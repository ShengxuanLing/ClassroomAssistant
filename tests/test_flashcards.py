# -*- coding: utf-8 -*-
"""Flashcard model, evidence grounding, persistence, and due queues."""

import json
from datetime import datetime, timezone

import pytest

from src.api.endpoints import build_router
from src.api.router import Request
from src.application.workspace import Workspace
from src.models import (
    Confidence,
    Course,
    Evidence,
    EvidenceType,
    Flashcard,
    KnowledgePoint,
    Language,
    SourceReference,
)
from src.persistence.errors import PersistenceValidationError
from src.scheduling.fsrs import FSRSScheduler


def _seed(repos, *, course_id="course-1", kp_id="kp-1", evidence_id="evidence-1"):
    repos.courses.save(Course(course_id=course_id, name="Álgebra", code="ALG"))
    repos.evidence.save(
        Evidence(
            evidence_id=evidence_id,
            content=f"Una función es una relación: {course_id}.",
            language=Language.SPANISH,
            source_reference=SourceReference(
                material_id="material-1", timestamp_start=1.0, timestamp_end=2.0
            ),
            confidence=Confidence.HIGH,
            evidence_type=EvidenceType.TRANSCRIPT,
        )
    )
    kp = KnowledgePoint(
        knowledge_id=kp_id,
        title="Función",
        content="Una función es una relación.",
        evidence_refs=[evidence_id],
    )
    repos.knowledge.save(kp, course_id=course_id)
    return kp


def _card(**overrides):
    values = {
        "course_id": "course-1",
        "student_id": "student-1",
        "kp_id": "kp-1",
        "front": "¿Qué es una función?",
        "back": "Una relación entre conjuntos.",
        "source_refs": ["evidence-1"],
        "due": "2026-09-26T10:00:00+00:00",
    }
    values.update(overrides)
    return Flashcard(**values)


def test_flashcard_requires_evidence():
    with pytest.raises(ValueError, match="source_ref"):
        _card(source_refs=[])


def test_flashcard_roundtrips_and_orders_source_refs(repos):
    _seed(repos)
    repos.evidence.save(
        Evidence(
            evidence_id="evidence-2",
            content="Una relación es un vínculo.",
            language=Language.SPANISH,
            source_reference=SourceReference(material_id="material-1"),
            confidence=Confidence.HIGH,
            evidence_type=EvidenceType.TRANSCRIPT,
        )
    )
    card = _card(source_refs=["evidence-2", "evidence-1", "evidence-2"])
    repos.flashcards.save(card)

    loaded = repos.flashcards.load(card.flashcard_id)
    assert loaded == card
    assert repos.flashcards.evidence_ids_for(card.flashcard_id) == [
        "evidence-2",
        "evidence-1",
    ]
    assert repos.flashcards.flashcard_ids_for_evidence("evidence-1") == [
        card.flashcard_id
    ]


def test_flashcard_due_queue_filters_without_cross_course_leakage(repos):
    _seed(repos, course_id="course-1", kp_id="kp-1", evidence_id="evidence-1")
    _seed(repos, course_id="course-2", kp_id="kp-2", evidence_id="evidence-2")
    due = _card(flashcard_id="due", due="2026-09-26T09:00:00+00:00")
    later = _card(
        flashcard_id="later",
        front="Otra",
        due="2026-09-27T09:00:00+00:00",
    )
    other_student = _card(
        flashcard_id="other-student", student_id="student-2", front="Estudiante"
    )
    repos.flashcards.save_many([due, later, other_student])

    queue = repos.flashcards.load_all(
        course_id="course-1",
        student_id="student-1",
        due_before="2026-09-26T12:00:00+00:00",
    )
    assert [card.flashcard_id for card in queue] == ["due"]


def test_flashcard_rejects_missing_source_without_partial_row(repos):
    _seed(repos)
    card = _card(source_refs=["ghost"])
    with pytest.raises(PersistenceValidationError, match="evidence"):
        repos.flashcards.save(card)
    assert repos.flashcards.count() == 0
    assert repos.flashcards.evidence_links.count() == 0


def test_flashcard_rejects_knowledge_point_from_another_course(repos):
    _seed(repos, course_id="course-1", kp_id="kp-1", evidence_id="evidence-1")
    _seed(repos, course_id="course-2", kp_id="kp-2", evidence_id="evidence-2")
    card = _card(flashcard_id="cross-course", kp_id="kp-2", source_refs=["evidence-2"])
    with pytest.raises(PersistenceValidationError, match="same course"):
        repos.flashcards.save(card)
    assert repos.flashcards.count() == 0


def test_flashcard_resave_replaces_evidence_chain_idempotently(repos):
    _seed(repos)
    card = _card()
    repos.flashcards.save(card)
    card.source_refs = list(reversed(card.source_refs))
    repos.flashcards.save(card)
    assert repos.flashcards.count() == 1
    assert repos.flashcards.evidence_links.count() == 1
    assert repos.flashcards.load(card.flashcard_id).source_refs == ["evidence-1"]


def test_workspace_creates_and_restores_flashcard(tmp_path):
    workspace = Workspace(
        str(tmp_path / "data"),
        flashcard_scheduler=FSRSScheduler(
            clock=lambda: datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
        ),
    )
    try:
        course = workspace.create_course("Matemáticas", "MAT")
        repos = workspace.persistence.repositories
        evidence = Evidence(
            evidence_id="evidence-workspace",
            content="Una función es una relación.",
            language=Language.SPANISH,
            source_reference=SourceReference(material_id="material-1"),
            confidence=Confidence.HIGH,
            evidence_type=EvidenceType.TRANSCRIPT,
        )
        repos.evidence.save(evidence)
        repos.knowledge.save(
            KnowledgePoint(
                knowledge_id="kp-workspace",
                title="Función",
                content="Una función es una relación.",
                evidence_refs=[evidence.evidence_id],
            ),
            course_id=course["course_id"],
        )
        workspace.reload_course(course["course_id"])
        created = workspace.create_flashcard(
            course["course_id"], "student-1", "kp-workspace"
        )
        assert created["source_refs"] == ["evidence-workspace"]
        listed = workspace.list_flashcards(
            course["course_id"], student_id="student-1"
        )
        assert len(listed) == 1
        assert listed[0]["flashcard_id"] == created["flashcard_id"]
        assert listed[0]["source_refs"] == created["source_refs"]
        reviewed = workspace.review_flashcard(
            course["course_id"], created["flashcard_id"], "good", student_id="student-1"
        )
        assert reviewed["reps"] == 1
        assert reviewed["due"] > reviewed["last_review"]
        assert workspace.get_flashcard(
            course["course_id"], created["flashcard_id"], student_id="student-1"
        )["due"] == reviewed["due"]
    finally:
        workspace.close()


def test_flashcard_api_routes_list_and_create():
    class FakeWorkspace:
        def __init__(self):
            self.cards = []

        def list_flashcards(self, course_id, *, student_id=None, due_before=None):
            assert course_id == "course-1"
            return self.cards

        def get_flashcard(self, course_id, flashcard_id, *, student_id=None):
            return next(card for card in self.cards if card["flashcard_id"] == flashcard_id)

        def review_flashcard(self, course_id, flashcard_id, rating, *, student_id=None):
            card = self.get_flashcard(course_id, flashcard_id, student_id=student_id)
            card["rating"] = rating
            return card

        def create_flashcard(self, course_id, student_id, kp_id, **kwargs):
            card = {
                "flashcard_id": "flashcard-1",
                "course_id": course_id,
                "student_id": student_id,
                "kp_id": kp_id,
                "front": kwargs.get("front") or "Función",
                "back": kwargs.get("back") or "Relación",
                "source_refs": ["evidence-1"],
            }
            self.cards.append(card)
            return card

    workspace = FakeWorkspace()
    router = build_router(workspace)
    response = router.dispatch(
        Request(
            "POST",
            "/api/flashcards",
            body=json.dumps(
                {
                    "course_id": "course-1",
                    "student_id": "student-1",
                    "kp_id": "kp-1",
                }
            ).encode("utf-8"),
        )
    )
    assert response.status == 201
    assert response.payload["data"]["flashcard_id"] == "flashcard-1"
    response = router.dispatch(
        Request(
            "GET",
            "/api/flashcards",
            query={"course_id": ["course-1"], "student_id": ["student-1"]},
        )
    )
    assert response.payload["data"]["flashcards"] == workspace.cards
    response = router.dispatch(
        Request(
            "POST",
            "/api/flashcards/flashcard-1/review",
            query={"course_id": ["course-1"]},
            body=json.dumps({"student_id": "student-1", "rating": "good"}).encode("utf-8"),
        )
    )
    assert response.payload["data"]["rating"] == 3
