# -*- coding: utf-8 -*-
"""FSRS scheduling isolated from StudyPlanner and from the domain layer.

The optional MIT ``fsrs`` package is the production backend.  A deterministic,
dependency-free fallback keeps tests and offline diagnosis honest when the
optional wheel is not installed; health/application wiring can expose which
backend is active instead of pretending a real FSRS run happened.

StudyPlanner still decides *what* to study.  This module only decides *when* a
card should come back.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from typing import Callable, Optional, Protocol

from src.common.clock import utc_now
from src.models import Flashcard

__all__ = [
    "DEFAULT_DESIRED_RETENTION",
    "FSRSScheduler",
    "PyFSRSBackend",
    "Rating",
    "ReviewUpdate",
]

DEFAULT_DESIRED_RETENTION = 0.9
DEFAULT_MAX_INTERVAL_DAYS = 365
PERSONALIZATION_REVIEW_THRESHOLD = 1000


class Rating(IntEnum):
    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4


@dataclass(frozen=True)
class ReviewUpdate:
    due: str
    stability: float
    difficulty: float
    state: str
    reps: int
    lapses: int
    last_review: str


class SchedulingBackend(Protocol):
    name: str

    def review(
        self,
        card: Flashcard,
        rating: Rating,
        *,
        reviewed_at: datetime,
        desired_retention: float,
        maximum_interval_days: int,
    ) -> tuple[datetime, float, float, str]:
        ...


def _utc_now() -> datetime:
    """默认时钟 —— **只作为 :attr:`FSRSScheduler.clock` 的默认值存在**。

    刻意写成可注入的 ``clock`` 参数 (见构造函数), 调度计算本身不直接读系统
    时钟, 测试全部走注入的假时钟。这是项目的确定性惯例 (P1-4), **不要**把这里
    重构成"直接算"的写法。

    读挂钟的**真源**在 :mod:`src.common.clock` (Determinism Audit 只允许那里
    直接调 ``datetime.now``), 本函数只是把它适配成调度器要的 ``datetime``。
    """
    return utc_now()


def _parse_due(value: str, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _state_value(name: str) -> int:
    return {"new": 0, "learning": 1, "review": 2, "relearning": 3}.get(
        str(name or "new").lower(), 0
    )


class PyFSRSBackend:
    """Adapter for the MIT ``fsrs`` package (open-spaced-repetition)."""

    name = "open-spaced-repetition/fsrs"

    @staticmethod
    def available() -> bool:
        try:
            import fsrs  # noqa: F401
        except ImportError:
            return False
        return True

    def review(
        self,
        card: Flashcard,
        rating: Rating,
        *,
        reviewed_at: datetime,
        desired_retention: float,
        maximum_interval_days: int,
    ) -> tuple[datetime, float, float, str]:
        from fsrs import Card as FSRSCard
        from fsrs import Rating as PackageRating
        from fsrs import Scheduler
        from fsrs import State as PackageState

        due = _parse_due(card.due, reviewed_at)
        state_name = str(card.state or "new").lower()
        package_state = {
            "new": PackageState.Learning,
            "learning": PackageState.Learning,
            "review": PackageState.Review,
            "relearning": PackageState.Relearning,
        }.get(state_name, PackageState.Learning)
        # py-fsrs uses an integer card id.  Derive it from our stable string id
        # without Python's process-randomized hash().
        package_card_id = int(
            hashlib.sha256(card.flashcard_id.encode("utf-8")).hexdigest()[:15],
            16,
        )
        package_card = FSRSCard(
            card_id=package_card_id,
            state=package_state,
            step=0 if package_state in (PackageState.Learning, PackageState.Relearning) else None,
            stability=card.stability or None,
            difficulty=card.difficulty,
            due=due,
            last_review=_parse_due(card.last_review or "", reviewed_at)
            if card.last_review
            else None,
        )
        scheduler = Scheduler(
            desired_retention=desired_retention,
            maximum_interval=maximum_interval_days,
            enable_fuzzing=False,
        )
        rating_value = {
            Rating.AGAIN: PackageRating.Again,
            Rating.HARD: PackageRating.Hard,
            Rating.GOOD: PackageRating.Good,
            Rating.EASY: PackageRating.Easy,
        }[rating]
        reviewed, _log = scheduler.review_card(
            package_card, rating_value, review_datetime=reviewed_at
        )
        next_due = reviewed.due
        if next_due.tzinfo is None:
            next_due = next_due.replace(tzinfo=timezone.utc)
        next_due = next_due.astimezone(timezone.utc)
        state = str(getattr(reviewed.state, "name", reviewed.state)).lower()
        return next_due, float(reviewed.stability), float(reviewed.difficulty), state


class DeterministicFSRSBackend:
    """Small FSRS-shaped fallback used when the optional package is absent.

    It preserves the invariants needed by the product and test suite (retention
    interval monotonicity, lapse reset, difficulty bounds, 365-day cap) without
    pretending to be the full optimizer.
    """

    name = "deterministic-fsrs-fallback"

    def review(
        self,
        card: Flashcard,
        rating: Rating,
        *,
        reviewed_at: datetime,
        desired_retention: float,
        maximum_interval_days: int,
    ) -> tuple[datetime, float, float, str]:
        difficulty = card.difficulty
        lapses = card.lapses
        is_new = card.reps == 0 or str(card.state).lower() == "new"

        if is_new:
            delays = {
                Rating.AGAIN: timedelta(minutes=1),
                Rating.HARD: timedelta(minutes=10),
                Rating.GOOD: timedelta(days=1),
                Rating.EASY: timedelta(days=4),
            }
            stability = {Rating.AGAIN: 0.2, Rating.HARD: 0.8, Rating.GOOD: 2.0, Rating.EASY: 5.0}[rating]
            next_due = reviewed_at + delays[rating]
            difficulty += {Rating.AGAIN: 1.0, Rating.HARD: 0.4, Rating.GOOD: 0.0, Rating.EASY: -0.4}[rating]
            state = "relearning" if rating is Rating.AGAIN else "learning"
        else:
            stability = float(card.stability or 0.2)
            if rating is Rating.AGAIN:
                stability = max(0.1, stability * 0.35)
                difficulty += 1.0
                lapses += 1
                state = "relearning"
                next_due = reviewed_at + timedelta(minutes=10)
            else:
                growth = {Rating.HARD: 1.2, Rating.GOOD: 2.5, Rating.EASY: 3.5}[rating]
                stability *= growth
                difficulty += {Rating.HARD: 0.3, Rating.GOOD: 0.0, Rating.EASY: -0.4}[rating]
                # S = t / (1/R - 1) * (desired_retention^(1/S) - 1), inverted to
                # the interval form S * ln(R) / ln(0.9).  Lower desired retention
                # therefore produces a strictly longer interval.
                interval_days = stability * math.log(desired_retention) / math.log(0.9)
                next_due = reviewed_at + timedelta(days=max(1.0, interval_days))
                state = "review"

        hard_cap = reviewed_at + timedelta(days=maximum_interval_days)
        if next_due > hard_cap:
            next_due = hard_cap
        return next_due.astimezone(timezone.utc), stability, min(10.0, max(1.0, difficulty)), state


class FSRSScheduler:
    """Validate scheduling policy and apply a rating to a Flashcard."""

    def __init__(
        self,
        *,
        desired_retention: float = DEFAULT_DESIRED_RETENTION,
        max_interval_days: int = DEFAULT_MAX_INTERVAL_DAYS,
        backend: Optional[SchedulingBackend] = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not 0.7 <= float(desired_retention) < 1.0:
            raise ValueError("desired_retention must be in [0.7, 1.0)")
        if int(max_interval_days) != max_interval_days or max_interval_days <= 0:
            raise ValueError("max_interval_days must be a positive integer")
        self.desired_retention = float(desired_retention)
        self.max_interval_days = int(max_interval_days)
        self.backend = backend or (
            PyFSRSBackend() if PyFSRSBackend.available() else DeterministicFSRSBackend()
        )
        self._clock = clock

    @staticmethod
    def personalization_eligible(review_count: int) -> bool:
        """Research guard: do not optimize a parameter set below 1000 reviews."""
        return int(review_count) >= PERSONALIZATION_REVIEW_THRESHOLD

    def review(
        self,
        card: Flashcard,
        rating: Rating | int,
        *,
        reviewed_at: Optional[datetime] = None,
    ) -> ReviewUpdate:
        if not isinstance(card, Flashcard):
            raise TypeError(f"expected Flashcard, got {type(card).__name__}")
        if isinstance(rating, str):
            rating = {
                "again": 1,
                "hard": 2,
                "good": 3,
                "easy": 4,
                "missed": 1,
                "got_it": 3,
            }.get(rating.strip().lower(), rating)
        rating = Rating(int(rating))
        now = reviewed_at or self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        due, stability, difficulty, state = self.backend.review(
            card,
            rating,
            reviewed_at=now,
            desired_retention=self.desired_retention,
            maximum_interval_days=self.max_interval_days,
        )
        reps = card.reps + 1
        lapses = card.lapses + (1 if rating is Rating.AGAIN and card.reps > 0 else 0)
        return ReviewUpdate(
            due=due.isoformat(),
            stability=max(0.0, float(stability)),
            difficulty=min(10.0, max(1.0, float(difficulty))),
            state=state,
            reps=reps,
            lapses=lapses,
            last_review=now.isoformat(),
        )
