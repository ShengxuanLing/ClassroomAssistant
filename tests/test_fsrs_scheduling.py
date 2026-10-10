# -*- coding: utf-8 -*-
"""FSRS scheduling policy and flashcard review persistence."""

from datetime import datetime, timedelta, timezone

import pytest

from src.models import Flashcard
from src.scheduling.fsrs import (
    DeterministicFSRSBackend,
    FSRSScheduler,
    Rating,
    ReviewUpdate,
)


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _card(**overrides):
    values = {
        "flashcard_id": "flashcard-test",
        "course_id": "course-1",
        "student_id": "student-1",
        "kp_id": "kp-1",
        "front": "front",
        "back": "back",
        "source_refs": ["evidence-1"],
        "due": NOW.isoformat(),
    }
    values.update(overrides)
    return Flashcard(**values)


def test_new_card_defaults_are_close_to_sm2_not_an_anki_interval():
    scheduler = FSRSScheduler(
        clock=lambda: NOW, backend=DeterministicFSRSBackend())
    good = scheduler.review(_card(), Rating.GOOD, reviewed_at=NOW)
    hard = scheduler.review(_card(), Rating.HARD, reviewed_at=NOW)
    easy = scheduler.review(_card(), Rating.EASY, reviewed_at=NOW)
    assert good.due.startswith("2026-09-26")
    assert hard.due.startswith("2026-09-25")
    assert easy.due.startswith("2026-09-29")
    assert 0 < hard.stability < good.stability < easy.stability


def test_lower_desired_retention_produces_a_longer_interval():
    card = _card(state="review", reps=3, stability=4.0, difficulty=5.0)
    high = FSRSScheduler(desired_retention=0.95).review(
        card, Rating.GOOD, reviewed_at=NOW
    )
    baseline = FSRSScheduler(desired_retention=0.9).review(
        card, Rating.GOOD, reviewed_at=NOW
    )
    low = FSRSScheduler(desired_retention=0.8).review(
        card, Rating.GOOD, reviewed_at=NOW
    )
    assert low.due > baseline.due > high.due


def test_again_resets_stability_and_records_a_lapse():
    card = _card(state="review", reps=4, stability=8.0, difficulty=5.0)
    update = FSRSScheduler(clock=lambda: NOW).review(card, Rating.AGAIN, reviewed_at=NOW)
    assert update.stability < card.stability
    assert update.lapses == card.lapses + 1
    assert update.state == "relearning"
    assert update.due.startswith("2026-09-25")


def test_interval_is_capped_at_365_days():
    card = _card(state="review", reps=20, stability=1000.0, difficulty=5.0)
    update = FSRSScheduler(clock=lambda: NOW).review(card, Rating.EASY, reviewed_at=NOW)
    assert update.due <= (NOW + timedelta(days=365)).isoformat()


def test_scheduler_rejects_invalid_policy():
    with pytest.raises(ValueError):
        FSRSScheduler(desired_retention=1.0)
    with pytest.raises(ValueError):
        FSRSScheduler(max_interval_days=0)


def test_personalization_is_not_claimed_below_1000_reviews():
    assert FSRSScheduler.personalization_eligible(999) is False
    assert FSRSScheduler.personalization_eligible(1000) is True


def test_review_update_is_immutable_and_typed():
    update = ReviewUpdate(
        due=NOW.isoformat(),
        stability=1.0,
        difficulty=5.0,
        state="learning",
        reps=1,
        lapses=0,
        last_review=NOW.isoformat(),
    )
    assert update.reps == 1
    with pytest.raises(AttributeError):
        update.reps = 2
