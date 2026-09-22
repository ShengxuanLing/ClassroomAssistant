"""Knowledge Validation module for the Classroom Assistant project.

Task 14 - Knowledge Validation & Evidence Conflict Resolution.

This module adds a deterministic validation layer on top of the existing
Task 13 pipeline. It answers, for each KnowledgePoint, how much of the
current Evidence base supports it, whether any conflicts remain, and
what verification state the point is in.

Core principles (evidence-first):
- Validation only judges consistency among the current classroom
  material. It NEVER judges whether a fact is true in the real world.
- A point in SUPPORTED state means "the current Evidence forms a
  consistent support" - not "the fact is verified to be correct".
- Conflicts are detected and recorded; they are never auto-resolved.
  Both sides of a conflict are preserved.
- All computations are deterministic: same input -> same output, with
  stable ordering of evidence/conflict IDs.
- Validation never mutates its inputs (Evidence objects are read-only).

Responsibilities:
- KnowledgePoint + Evidence + ConflictRecords
    -> ValidationStatus + deterministic knowledge_score + needs_verification

The module does NOT:
- Re-implement EvidenceIntegrator or KnowledgeExtractor
- Use LLMs, network, databases, randomness, or timestamps
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

from src.integration import EvidenceIntegrator, ConflictRecord
from src.knowledge_structure import KnowledgeStructure, KnowledgePoint

# P1-4: ``ValidationStatus`` 的真源已收口到 ``src.models`` (领域核心), 以消除
# ``src.models`` 对上层模块的懒导入。此处保留兼容 re-export, 历史
# ``from src.knowledge_validation import ValidationStatus`` 的调用方无需改动。
from src.models import ValidationStatus


@dataclass(frozen=True)
class ValidationResult:
    """Deterministic validation outcome for a single KnowledgePoint."""
    knowledge_id: str
    status: ValidationStatus
    knowledge_score: float
    supporting_evidence_ids: tuple
    conflict_ids: tuple
    needs_verification: bool


def _unique_ordered(values):
    seen = set()
    out = []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _conflict_ref_sets(conflicts):
    """Per-conflict reference sets, when *conflicts* is an
    EvidenceIntegrationResult carrying pre-computed
    conflict_refs_by_id. Otherwise None (fall back to scanning)."""
    ref_sets = getattr(conflicts, "conflict_refs_by_id", None)
    if ref_sets is not None:
        return ref_sets
    return None


def _build_conflict_index(conflicts):
    """Inverted index: evidence_id -> list of ConflictRecord objects.

    Built once per structure-validation pass so each KnowledgePoint
    only inspects the conflicts that reference one of its own evidence
    refs (Task 25 performance work). Returns None when conflicts does
    not carry pre-computed reference sets.
    """
    ref_sets = _conflict_ref_sets(conflicts)
    if ref_sets is None:
        return None
    records = getattr(conflicts, "conflicts", None) or []
    index: dict[str, list] = {}
    for record in records:
        refs = ref_sets.get(record.conflict_id)
        if not refs:
            continue
        for ref in refs:
            index.setdefault(ref, []).append(record)
    return index


def _build_evidence_map(evidence_all):
    """De-duplicate evidence by evidence_id, preserving input order."""
    m = {}
    for ev in evidence_all:
        if ev.evidence_id not in m:
            m[ev.evidence_id] = ev
    return m


def _knowledge_score(n_support: int, n_conflict: int) -> float:
    """Deterministic score in [0.0, 1.0] from support/conflict counts.

    - 0 supporting evidence -> 0.0
    - 1 supporting evidence -> 0.5
    - 2 supporting evidence -> 0.75
    - 3+ supporting evidence -> 0.9
    - if any conflict exists, the score is capped at 0.5
    """
    if n_support <= 0:
        score = 0.0
    elif n_support == 1:
        score = 0.5
    elif n_support == 2:
        score = 0.75
    else:
        score = 0.9
    if n_conflict > 0:
        score = min(score, 0.5)
    if math.isnan(score) or math.isinf(score):
        score = 0.0
    if score < 0.0:
        score = 0.0
    if score > 1.0:
        score = 1.0
    return score


def knowledge_score_from_counts(n_support: int, n_conflict: int = 0) -> float:
    """Deterministic knowledge_score from support/conflict counts (single source).

    Both write paths MUST go through this function:

    - the deterministic pipeline (``KnowledgeValidator`` ->
      ``KnowledgePipeline._apply_validation``), and
    - the AI extraction path (``src/application/ai/validators.py``).

    Before 2026-09-21 the AI path wrote the LLM's self-reported confidence
    straight into ``knowledge_score``, producing values (1.0, 0.95) that this
    formula can never emit -- its range is exactly 0.0 / 0.5 / 0.75 / 0.9.
    The same database column therefore carried two incompatible meanings.
    The AI path now derives the score from its grounded evidence count, so
    the field means "how much of the current Evidence supports this point"
    on every path.

    ``n_conflict`` stays 0 on the AI path: evidence-level conflict detection
    belongs to the deterministic assembly (``EvidenceIntegrator``), not to
    candidate extraction.
    """
    return _knowledge_score(n_support, n_conflict)


def _resolve_status(n_support: int, n_conflict: int, knowledge_score: float):
    if n_conflict > 0:
        return ValidationStatus.CONFLICTED
    if n_support >= 1 and knowledge_score >= 0.5:
        return ValidationStatus.SUPPORTED
    return ValidationStatus.UNVERIFIED


def _needs_verification(status):
    return status != ValidationStatus.SUPPORTED


class KnowledgeValidator:
    """Deterministic validation of KnowledgePoints against Evidence.

    This class is read-only over its inputs. It does not mutate
    KnowledgePoint, Evidence, or ConflictRecord objects.
    """

    @staticmethod
    def validate_knowledge_point(kp, evidence_all, conflicts, evidence_map=None, conflict_index=None):
        """Validate a single KnowledgePoint against the full Evidence base.
        Reuses a pre-built evidence map when provided.

        When *conflict_index* (evidence_id -> [ConflictRecord]) is
        provided, only conflicts touching the KP's own evidence refs are
        inspected, keeping per-point work proportional to its refs
        instead of the whole conflict list (Task 25 performance work).
        """
        if evidence_map is None:
            evidence_map = _build_evidence_map(evidence_all)
        refs = _unique_ordered(kp.evidence_refs)
        supporting_ids = [r for r in refs if r in evidence_map]
        n_support = len(supporting_ids)

        relevant = []
        if conflict_index is not None:
            kp_ev_refs = set(refs)
            for ref in refs:
                for c in conflict_index.get(ref, ()):
                    relevant.append(c)
        else:
            ref_sets = _conflict_ref_sets(conflicts)
            if ref_sets is not None:
                kp_ev_refs = set(refs)
                conflict_records = getattr(conflicts, "conflicts", None) or []
                for c in conflict_records:
                    refs_c = ref_sets.get(c.conflict_id, frozenset())
                    if refs_c and kp_ev_refs & refs_c:
                        relevant.append(c)
            else:
                relevant = [
                    c for c in conflicts
                    if c.conflict_id
                    and any(ref in set(refs) for ref in c.evidence_refs)
                ]
        seen_c = set()
        deduped_conflicts = []
        for c in relevant:
            key = c.conflict_id
            if key not in seen_c:
                seen_c.add(key)
                deduped_conflicts.append(c)
        n_conflict = len(deduped_conflicts)
        conflict_ids = tuple(sorted(c.conflict_id for c in deduped_conflicts))

        score = _knowledge_score(n_support, n_conflict)
        status = _resolve_status(n_support, n_conflict, score)
        needs_ver = _needs_verification(status)

        return ValidationResult(
            knowledge_id=kp.knowledge_id,
            status=status,
            knowledge_score=score,
            supporting_evidence_ids=tuple(supporting_ids),
            conflict_ids=conflict_ids,
            needs_verification=needs_ver,
        )



    @staticmethod
    def validate_structure(structure, evidence_all, conflicts=None):
        """Validate every KnowledgePoint in a KnowledgeStructure.
        Builds the evidence map and the conflict index once and reuses
        them across all points."""
        if conflicts is None:
            conflicts = structure.conflicts
        evidence_map = _build_evidence_map(evidence_all)
        conflict_index = _build_conflict_index(conflicts)
        results = {}
        for kp in sorted(structure.knowledge_points.values(),
                         key=lambda k: k.knowledge_id):
            results[kp.knowledge_id] = KnowledgeValidator.validate_knowledge_point(
                kp, evidence_all, conflicts,
                evidence_map=evidence_map,
                conflict_index=conflict_index,
            )
        return results


class ValidationReport:
    """Aggregate report over a KnowledgeStructure."""

    def __init__(self, results):
        self._results = results

    @property
    def results(self):
        return self._results

    def get(self, knowledge_id):
        return self._results.get(knowledge_id)

    def summary(self):
        out = {"unverified": 0, "supported": 0, "conflicted": 0, "total": 0}
        for r in self._results.values():
            out[r.status.value] += 1
            out["total"] += 1
        return out

    def to_dict(self):
        return {
            knowledge_id: {
                "status": r.status.value,
                "knowledge_score": r.knowledge_score,
                "supporting_evidence_ids": list(r.supporting_evidence_ids),
                "conflict_ids": list(r.conflict_ids),
                "needs_verification": r.needs_verification,
            }
            for knowledge_id, r in self._results.items()
        }

