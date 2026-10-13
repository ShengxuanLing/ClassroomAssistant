# -*- coding: utf-8 -*-
"""Migration 005 — remove flashcards tables (TASK-106).

This migration drops the two tables introduced by migration 004.  The link
table is dropped first to keep foreign-key teardown order sensible.

The codebase still imports ``m004_flashcards`` (historical release
contract is append-only), but no runtime path remains that reads or writes
flashcards after this migration is applied.
"""

from __future__ import annotations

from src.persistence.migrations import Migration

MIGRATION_005 = Migration(
    version=5,
    name="drop_flashcards",
    statements=(
        "DROP TABLE IF EXISTS flashcard_evidence;",
        "DROP TABLE IF EXISTS flashcards;",
    ),
)
