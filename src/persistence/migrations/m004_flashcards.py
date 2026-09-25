# -*- coding: utf-8 -*-
"""Migration 004 — evidence-grounded, learner-scoped flashcards.

Cards are keyed by a deterministic content identity which already includes the
course and student.  Query columns are duplicated from the domain payload so
due queues and course/student isolation remain indexed.  ``flashcard_evidence``
is a real relation (not only a JSON list), so the database rejects a source
reference that does not exist in the authoritative Evidence store.
"""

from __future__ import annotations

from src.persistence.migrations import Migration

MIGRATION_004 = Migration(
    version=4,
    name="flashcards",
    statements=(
        """
        CREATE TABLE flashcards (
            flashcard_id      TEXT PRIMARY KEY,
            course_id         TEXT NOT NULL,
            student_id        TEXT NOT NULL,
            knowledge_point_id TEXT NOT NULL,
            front             TEXT NOT NULL,
            back              TEXT NOT NULL,
            due               TEXT NOT NULL DEFAULT '',
            stability         REAL NOT NULL DEFAULT 0.0,
            difficulty        REAL NOT NULL DEFAULT 5.0,
            desired_retention REAL NOT NULL DEFAULT 0.9,
            state             TEXT NOT NULL DEFAULT 'new',
            reps              INTEGER NOT NULL DEFAULT 0,
            lapses            INTEGER NOT NULL DEFAULT 0,
            payload           TEXT NOT NULL,
            payload_version   INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (course_id) REFERENCES courses(course_id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX idx_flashcards_course_student "
        "ON flashcards(course_id, student_id, due, flashcard_id)",
        "CREATE INDEX idx_flashcards_due ON flashcards(due, flashcard_id)",
        "CREATE INDEX idx_flashcards_kp ON flashcards(course_id, knowledge_point_id)",
        """
        CREATE TABLE flashcard_evidence (
            flashcard_id TEXT NOT NULL,
            evidence_id  TEXT NOT NULL,
            position     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (flashcard_id, evidence_id),
            FOREIGN KEY (flashcard_id) REFERENCES flashcards(flashcard_id) ON DELETE CASCADE,
            FOREIGN KEY (evidence_id) REFERENCES evidence(evidence_id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX idx_flashcard_evidence ON flashcard_evidence(evidence_id)",
    ),
)
