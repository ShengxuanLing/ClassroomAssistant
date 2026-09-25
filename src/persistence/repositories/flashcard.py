# -*- coding: utf-8 -*-
"""Evidence-grounded flashcard persistence.

The card payload is authoritative; ``flashcard_evidence`` is the indexed,
ordered relation used to navigate back to source evidence.  Validation happens
before either row is written so a rejected card cannot leave partial data.
"""

from __future__ import annotations

from typing import Iterable, Optional

from src.models import Flashcard
from src.persistence.errors import PersistenceValidationError
from src.persistence.models.codec import decode_payload
from src.persistence.repositories.base import DocumentRepository, LinkRepository

__all__ = ["FlashcardRepository"]


class FlashcardRepository(DocumentRepository):
    """Flashcards plus their strict, ordered evidence relation."""

    table = "flashcards"

    def __init__(self, database) -> None:
        super().__init__(database)
        self.evidence_links = LinkRepository(database, "flashcard_evidence")

    def _validate_references(self, card: Flashcard) -> None:
        missing = [
            evidence_id
            for evidence_id in card.source_refs
            if self._db.query_one(
                "SELECT 1 FROM evidence WHERE evidence_id = ?", (evidence_id,)
            )
            is None
        ]
        if missing:
            raise PersistenceValidationError(
                "flashcard source_refs must exist in the evidence store",
                detail={"flashcard_id": card.flashcard_id, "missing": missing},
            )
        row = self._db.query_one(
            "SELECT 1 FROM course_knowledge_points "
            "WHERE course_id = ? AND knowledge_id = ?",
            (card.course_id, card.kp_id),
        )
        if row is None:
            raise PersistenceValidationError(
                "flashcard kp_id must reference a knowledge point in the same course",
                detail={
                    "flashcard_id": card.flashcard_id,
                    "course_id": card.course_id,
                    "kp_id": card.kp_id,
                },
            )

    def save(self, card: Flashcard) -> None:
        if not isinstance(card, Flashcard):
            raise TypeError(f"expected Flashcard, got {type(card).__name__}")
        self._validate_references(card)
        with self._db.transaction():
            self.put(
                {
                    "flashcard_id": card.flashcard_id,
                    "course_id": card.course_id,
                    "student_id": card.student_id,
                    "knowledge_point_id": card.kp_id,
                    "front": card.front,
                    "back": card.back,
                    "due": card.due,
                    "stability": card.stability,
                    "difficulty": card.difficulty,
                    "desired_retention": card.desired_retention,
                    "state": card.state,
                    "reps": card.reps,
                    "lapses": card.lapses,
                },
                card,
            )
            self.evidence_links.replace(card.flashcard_id, card.source_refs)

    def save_many(self, cards: Iterable[Flashcard]) -> int:
        count = 0
        for card in cards:
            self.save(card)
            count += 1
        return count

    def load(self, flashcard_id: str) -> Optional[Flashcard]:
        payload = self.get(flashcard_id)
        return Flashcard.from_dict(payload) if payload is not None else None

    def load_all(
        self,
        *,
        course_id: Optional[str] = None,
        student_id: Optional[str] = None,
        due_before: Optional[str] = None,
    ) -> list[Flashcard]:
        clauses: list[str] = []
        params: list[str] = []
        if course_id is not None:
            clauses.append("course_id = ?")
            params.append(course_id)
        if student_id is not None:
            clauses.append("student_id = ?")
            params.append(student_id)
        if due_before is not None:
            clauses.append("due <= ?")
            params.append(due_before)
        where = " AND ".join(clauses) if clauses else None
        rows = self.rows(where=where, params=params)
        return [Flashcard.from_dict(self._payload_from_row(row)) for row in rows]

    def evidence_ids_for(self, flashcard_id: str) -> list[str]:
        return self.evidence_links.rights_for(flashcard_id)

    def flashcard_ids_for_evidence(self, evidence_id: str) -> list[str]:
        return self.evidence_links.lefts_for(evidence_id)

    @staticmethod
    def _payload_from_row(row) -> dict:
        return decode_payload(row["payload"], context="flashcards.payload")
