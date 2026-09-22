"""Evidence-Backed Knowledge Assembly (Task 25).

This module adds the deterministic, evidence-backed knowledge assembly
layer on top of the existing Task 13 pipeline, Task 14 validation, and
Task 15 review semantics.  It reuses -- never re-implements -- the
deterministic building blocks that already exist:

- EvidenceIntegrator (duplicates / conflicts / supporting groups,
  exact-match normalization only, no semantic matching)
- KnowledgePipeline / KnowledgeExtractor (Evidence -> KnowledgePoint
  semantics, stable knowledge IDs)
- KnowledgeValidator (read-only SUPPORTED / CONFLICTED / UNVERIFIED
  recomputation, deterministic knowledge_score)
- KnowledgeReviewService (human-in-the-loop candidate discovery; this
  module never makes review decisions on its own)

Core principles (evidence-first, Task 25 spec):

- No LLM, no semantic matching, no translation, no automatic conflict
  resolution, no automatic review decisions.
- No uuid4() and no datetime.now() participate in any identity.  All
  derived provenance (material ids, document ids, source types,
  session ids, evidence counts) is computed on demand from the current
  Evidence base; it is NEVER stored as a second state on
  KnowledgePoint.
- Invariant 1: every KnowledgePoint carries at least one evidence
  reference.  The extractor guarantees this (a cluster always has
  members; a supporting group with no members is skipped); this
  module asserts it in integrity checks.
- Incremental assembly is idempotent: reprocessing the same evidence
  list -- whether split across several process_evidence calls or
  replayed whole -- lands on the same structure (KP / conflict /
  relationship sets are stable; only the result's "new" vs "updated"
  bookkeeping differs).
- RETIRED evidence is excluded from processing by default
  (include_retired=False).  Historical structure already built from
  now-retired evidence keeps its references: nothing in this module
  ever deletes a reference from an existing KnowledgePoint.
- Re-submitted evidence is de-duplicated by evidence_id (first-seen
  wins) before the integrator runs.  Rationale: integrating N records
  is O(N^2) in the integrator (pairwise conflict + subsequence scan);
  deduping a replayed store drops that to O(fresh^2).  Equivalence
  with KnowledgePipeline().process_evidence() is exact for the
  no-duplicates case (a batch the pipeline would dedupe internally
  is identical to the already-deduped batch this layer feeds it).
  Brand-new cross-source duplicates and conflicts are still found by
  the integrator within the fresh batch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from src.evidence_store import EvidenceStore
from src.integration import EvidenceIntegrator
from src.knowledge_pipeline import KnowledgePipeline
from src.knowledge_review import KnowledgeReviewService, ReviewStatus
from src.knowledge_structure import KnowledgePoint, KnowledgeStructure
from src.knowledge_validation import KnowledgeValidator, ValidationStatus
from src.models import ClassSession, Evidence


class KnowledgeAssemblyError(Exception):
    """Raised when a serialized KnowledgeStructure cannot be restored.

    Corrupt payload (unknown validation status, missing knowledge_id,
    duplicate knowledge_id) and unreadable JSON both raise this.
    """


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AssemblyResult:
    """Deterministic outcome of one assembly pass.

    Mirrors PipelineResult (Task 13) field-for-field so that
    assembler output is *identical* to calling
    KnowledgePipeline().process_evidence() directly:

    - evidence_ids: the evidence processed in this call (the pipeline
      integrates the list as given; re-submission is deduped by id
      inside the integrator).
    - new_knowledge_point_ids / updated_knowledge_point_ids: KP ids
      created / touched by this call (order = pipeline order).
    - new_relationship_ids: relationships added by this call.
    - conflict_ids: conflicts reported by this call's integration.
    - structure: the (possibly newly created) KnowledgeStructure.
    """

    evidence_ids: tuple
    new_knowledge_point_ids: tuple
    updated_knowledge_point_ids: tuple
    new_relationship_ids: tuple
    conflict_ids: tuple
    structure: KnowledgeStructure


@dataclass(frozen=True)
class IntegrityReport:
    """Read-only integrity audit of a KnowledgeStructure against a store.

    The audit NEVER repairs: violations are reported, the structure is
    left exactly as given (no auto-repair, no ref deletion).
    """

    valid: bool
    dangling_evidence_refs: tuple  # (kp_id, (missing refs...)) pairs
    duplicate_refs: tuple  # kp_ids whose evidence_refs repeat
    conflict_ref_errors: tuple  # conflict_ids referencing missing evidence
    orphaned_knowledge_points: tuple  # kp ids with NO evidence refs at all

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "dangling_evidence_refs": [
                {"knowledge_id": kp_id, "missing_refs": list(refs)}
                for kp_id, refs in self.dangling_evidence_refs
            ],
            "duplicate_refs": list(self.duplicate_refs),
            "conflict_ref_errors": list(self.conflict_ref_errors),
            "orphaned_knowledge_points": list(self.orphaned_knowledge_points),
        }


# ---------------------------------------------------------------------------
# Internal helpers (module-private, deterministic, no side effects)
# ---------------------------------------------------------------------------

def _unique_ordered(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _dedupe_evidence(items: List[Evidence]) -> List[Evidence]:
    """Drop repeated evidence_id values (first-seen wins), keep order.

    Mirrors EvidenceIntegrator.integrate(): a batch containing the same
    record twice is equivalent to a batch containing it once.  This
    keeps incremental replays cheap without changing any behaviour.
    """
    seen: set[str] = set()
    out: List[Evidence] = []
    for item in items:
        if item.evidence_id in seen:
            continue
        seen.add(item.evidence_id)
        out.append(item)
    return out


def _require_structure(structure: Optional[KnowledgeStructure]) -> KnowledgeStructure:
    """Accept None (fresh structure) or a KnowledgeStructure, reject the rest."""
    if structure is None:
        return KnowledgeStructure()
    if not isinstance(structure, KnowledgeStructure):
        raise TypeError(
            f"structure must be KnowledgeStructure or None, "
            f"got {type(structure).__name__}"
        )
    return structure


def _require_evidence_list(evidence: Any) -> List[Evidence]:
    """Coerce the evidence argument into a plain list[Evidence].

    Raises TypeError for non-sequence inputs (None, dict, Evidence)
    and ValueError for lists that mix in non-Evidence items.
    """
    if isinstance(evidence, Evidence):
        raise TypeError(
            "process_evidence() expects a sequence of Evidence, "
            "not a single Evidence; wrap it in a list"
        )
    if isinstance(evidence, (str, bytes)):
        raise TypeError("evidence must be a sequence of Evidence, not a string")
    if evidence is None:
        return []
    if not hasattr(evidence, "__iter__"):
        raise TypeError(f"evidence must be iterable, got {type(evidence).__name__}")
    items = list(evidence)
    for item in items:
        if not isinstance(item, Evidence):
            raise ValueError(
                f"evidence items must be Evidence instances, got {type(item).__name__}"
            )
    return items


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------

class KnowledgeAssembler:
    """Task 25 - deterministic Evidence-backed Knowledge assembly.

    Reuses EvidenceIntegrator (duplicates/conflicts/supporting),
    KnowledgeExtractor semantics, KnowledgeValidator, KnowledgeReviewService.
    No LLM, no semantic matching, no translation, no automatic conflict
    resolution, no automatic review decisions.

    Usage::

        assembler = KnowledgeAssembler()
        result = assembler.process_store(store)
        structure = result.structure
        service = KnowledgeReviewService()
        candidates = service.get_review_candidates(structure)

    The assembler NEVER creates ReviewRecords or changes review_status
    automatically; review is an explicit human action through
    KnowledgeReviewService on the produced structure.
    """

    def __init__(self) -> None:
        # One shared deterministic pipeline instance; process_evidence()
        # is stateless with respect to it, so sharing is safe and keeps
        # behaviour byte-identical to Task 13.
        self._pipeline = KnowledgePipeline()

    # ------------------------------------------------------------------
    # Core API (spec 17)
    # ------------------------------------------------------------------

    def process_evidence(
        self,
        evidence: Sequence[Evidence],
        structure: Optional[KnowledgeStructure] = None,
    ) -> AssemblyResult:
        """Assemble knowledge from an evidence list into *structure*.

        Behaviour is identical to KnowledgePipeline().process_evidence():
        duplicate / conflict / supporting detection runs on the
        integration layer; KPs are extracted with stable ids; conflicts
        and relationships are registered idempotently; validation
        recomputes status / score / needs_verification read-only.
        """
        items = _dedupe_evidence(_require_evidence_list(evidence))
        target = _require_structure(structure)
        pipeline_result = self._pipeline.process_evidence(items, structure=target)
        return AssemblyResult(
            evidence_ids=tuple(pipeline_result.evidence_ids),
            new_knowledge_point_ids=tuple(pipeline_result.new_knowledge_point_ids),
            updated_knowledge_point_ids=tuple(pipeline_result.updated_knowledge_point_ids),
            new_relationship_ids=tuple(pipeline_result.new_relationship_ids),
            conflict_ids=tuple(pipeline_result.conflict_ids),
            structure=target,
        )

    def process_evidences(
        self,
        evidences: Sequence[Evidence],
        structure: Optional[KnowledgeStructure] = None,
    ) -> AssemblyResult:
        """Alias of process_evidence() (plural, spec 17)."""
        return self.process_evidence(evidences, structure)

    def process_store(
        self,
        store: EvidenceStore,
        structure: Optional[KnowledgeStructure] = None,
        include_retired: bool = False,
    ) -> AssemblyResult:
        """Assemble from an EvidenceStore's records.

        The store is read with ``all(active_only=not include_retired)`` and
        the result is then put into a **canonical order** (by
        ``evidence_id``) before it reaches ``process_evidence()``.

        Why canonical and not insertion order (Task 45 修复的真实缺陷):
        支持关系/冲突检测对**遍历顺序**敏感 —— 同一批证据按不同顺序送入,
        会聚成不同的 KnowledgePoint 分组, 于是 ``knowledge_id`` 也不同。
        而插入顺序是**摄取历史**的函数, 不是数据的函数: 逐份上传材料 vs
        一次性处理整节课, 得到的证据集合完全相同 (17 条, 逐字相同), 但
        顺序不同, 结果就分叉了。这在产品上是可见的 —— 用户分几次上传同一
        批材料, 拿到的知识点和一次上传不一样。按 ``evidence_id`` (内容寻址
        的业务标识) 排序后, 装配结果只取决于**证据集合**, 与摄取顺序无关。

        Idempotent: repeated calls land on the same structure state; only
        the "new"/"updated" bookkeeping of the result changes.

        RETIRED records are skipped by default; a structure built
        earlier from now-retired evidence keeps its references
        (retirement never triggers a deletion pass over KPs).
        """
        if not isinstance(store, EvidenceStore):
            raise TypeError(f"store must be EvidenceStore, got {type(store).__name__}")
        evidence = store.all(active_only=not include_retired)
        evidence.sort(key=lambda ev: ev.evidence_id)
        return self.process_evidence(evidence, structure=structure)

    # ------------------------------------------------------------------
    # Provenance aggregation (spec 29) - derived, never stored
    # ------------------------------------------------------------------

    @staticmethod
    def evidence_map_for(store: EvidenceStore) -> dict[str, Evidence]:
        """O(1) evidence_id -> Evidence lookup over ACTIVE records.

        Built once per analysis session so per-KP queries do not scan
        the store repeatedly (spec performance rule).
        """
        if not isinstance(store, EvidenceStore):
            raise TypeError(f"store must be EvidenceStore, got {type(store).__name__}")
        return {ev.evidence_id: ev for ev in store.all()}

    def material_ids_for_knowledge_point(
        self,
        structure: KnowledgeStructure,
        kp_id: str,
        store: EvidenceStore,
    ) -> list[str]:
        """Material ids feeding one KP, first-seen order, de-duplicated."""
        ev_map = self.evidence_map_for(store)
        out: list[str] = []
        seen: set[str] = set()
        for ref in self.supporting_evidence_ids(structure, kp_id):
            ev = ev_map.get(ref)
            if ev is None:
                continue
            mid = ev.source_reference.material_id if ev.source_reference else ""
            if mid and mid not in seen:
                seen.add(mid)
                out.append(mid)
        return out

    @staticmethod
    def document_ids_for_knowledge_point(
        structure: KnowledgeStructure,
        kp_id: str,
        store: EvidenceStore,
    ) -> list[str]:
        """Document ids (evidence.metadata["document_id"]) for one KP."""
        ev_map = KnowledgeAssembler.evidence_map_for(store)
        out: list[str] = []
        seen: set[str] = set()
        for ref in structure.supporting_evidence_ids(kp_id):
            ev = ev_map.get(ref)
            if ev is None:
                continue
            doc = ev.metadata.get("document_id")
            if doc and doc not in seen:
                seen.add(doc)
                out.append(doc)
        return out

    @staticmethod
    def session_ids_for_knowledge_point(
        structure: KnowledgeStructure,
        kp_id: str,
        sessions: Sequence[ClassSession],
    ) -> list[str]:
        """Sessions whose evidence_refs intersect the KP's evidence refs.

        Sessions are scanned in the given order; a session "contains" the
        KP when any of its evidence_refs appears in the KP's refs.
        """
        kp = structure.get_knowledge_point(kp_id)
        if kp is None:
            return []
        kp_refs = set(kp.evidence_refs)
        out: list[str] = []
        seen: set[str] = set()
        for session in sessions or []:
            sid = session.session_id
            if sid in seen:
                continue
            if any(ref in kp_refs for ref in (session.evidence_refs or [])):
                seen.add(sid)
                out.append(sid)
        return out

    @staticmethod
    def source_types_for_knowledge_point(
        structure: KnowledgeStructure,
        kp_id: str,
        evidence_store: EvidenceStore,
    ) -> list[str]:
        """Distinct evidence_type values backing one KP, first-seen order."""
        ev_map = KnowledgeAssembler.evidence_map_for(evidence_store)
        out: list[str] = []
        seen: set[str] = set()
        for ref in structure.supporting_evidence_ids(kp_id):
            ev = ev_map.get(ref)
            if ev is None:
                continue
            etype = ev.evidence_type.value
            if etype not in seen:
                seen.add(etype)
                out.append(etype)
        return out

    @staticmethod
    def supporting_evidence_ids(structure: KnowledgeStructure, kp_id: str) -> list[str]:
        """First-seen, de-duplicated evidence refs of a KP (stable)."""
        return structure.supporting_evidence_ids(kp_id)

    @staticmethod
    def evidence_counts(structure: KnowledgeStructure, kp_id: str) -> int:
        """Number of distinct evidence refs supporting one KP."""
        return structure.supporting_evidence_count(kp_id)

    # ------------------------------------------------------------------
    # Queries (spec 66)
    # ------------------------------------------------------------------

    @staticmethod
    def knowledge_for_evidence(structure: KnowledgeStructure, evidence_id: str) -> list[str]:
        """Sorted KP ids whose evidence_refs contain *evidence_id*."""
        out: list[str] = []
        for kp in structure.knowledge_points.values():
            if evidence_id in kp.evidence_refs:
                out.append(kp.knowledge_id)
        return sorted(out)

    @staticmethod
    def knowledge_for_material(
        structure: KnowledgeStructure,
        material_id: str,
        store: EvidenceStore,
    ) -> list[str]:
        """Sorted union of KPs for all evidence sourced from *material_id*."""
        ev_map = KnowledgeAssembler.evidence_map_for(store)
        ids: set[str] = set()
        for ev in ev_map.values():
            if ev.source_reference and ev.source_reference.material_id == material_id:
                ids.update(
                    kp.knowledge_id
                    for kp in structure.knowledge_points.values()
                    if ev.evidence_id in kp.evidence_refs
                )
        return sorted(ids)

    @staticmethod
    def knowledge_for_document(
        structure: KnowledgeStructure,
        document_id: str,
        store: EvidenceStore,
    ) -> list[str]:
        """Sorted union of KPs for all evidence tagged with *document_id*."""
        ev_map = KnowledgeAssembler.evidence_map_for(store)
        ids: set[str] = set()
        for ev in ev_map.values():
            if ev.metadata.get("document_id") == document_id:
                ids.update(
                    kp.knowledge_id
                    for kp in structure.knowledge_points.values()
                    if ev.evidence_id in kp.evidence_refs
                )
        return sorted(ids)

    @staticmethod
    def knowledge_for_session(
        structure: KnowledgeStructure,
        session_id: str,
        sessions: Sequence[ClassSession],
        store: EvidenceStore,
    ) -> list[str]:
        """Sorted union of KPs over the evidence refs of one session."""
        refs: list[str] = []
        for session in sessions or []:
            if session.session_id == session_id:
                refs = list(session.evidence_refs or [])
                break
        if not refs:
            return []
        ev_map = KnowledgeAssembler.evidence_map_for(store)
        ids: set[str] = set()
        for ref in refs:
            if ref not in ev_map:
                continue
            for kp in structure.knowledge_points.values():
                if ref in kp.evidence_refs:
                    ids.add(kp.knowledge_id)
        return sorted(ids)

    # ------------------------------------------------------------------
    # Integrity (spec 68-69)
    # ------------------------------------------------------------------

    def validate_integrity(
        self,
        structure: KnowledgeStructure,
        store: EvidenceStore,
    ) -> IntegrityReport:
        """Audit a structure against the current store.  Read-only.

        Checks:
        - every KP has at least one evidence ref (invariant 1);
        - every KP ref exists in the store (dangling refs);
        - no KP repeats the same ref (duplicate refs);
        - every conflict ref exists in the store;
        - every review record points at an existing KP.

        Violations are reported, never repaired.
        """
        target = _require_structure(structure)
        ev_map = self.evidence_map_for(store)
        known_ids = set(ev_map)

        dangling: list[tuple[str, tuple[str, ...]]] = []
        duplicates: list[str] = []
        orphans: list[str] = []

        for kp in sorted(target.knowledge_points.values(), key=lambda k: k.knowledge_id):
            refs = [r for r in kp.evidence_refs if r]
            if not refs:
                orphans.append(kp.knowledge_id)
                continue
            seen: set[str] = set()
            dup_found = False
            for ref in refs:
                if ref in seen:
                    dup_found = True
                seen.add(ref)
            if dup_found:
                duplicates.append(kp.knowledge_id)
            missing = tuple(sorted({r for r in refs if r not in known_ids}))
            if missing:
                dangling.append((kp.knowledge_id, missing))

        conflict_errors: list[str] = []
        for conflict in target.conflicts:
            refs = [r for r in conflict.evidence_refs if r]
            if any(r not in known_ids for r in refs):
                conflict_errors.append(conflict.conflict_id)

        bad_review_refs: list[str] = []
        for record in target.review_records:
            if record.knowledge_point_id not in target.knowledge_points:
                bad_review_refs.append(record.review_id)

        all_errors = orphans + duplicates + dangling + conflict_errors
        valid = not all_errors and not bad_review_refs
        return IntegrityReport(
            valid=valid,
            dangling_evidence_refs=tuple(dangling),
            duplicate_refs=tuple(sorted(duplicates)),
            conflict_ref_errors=tuple(conflict_errors),
            orphaned_knowledge_points=tuple(orphans),
        )

    # ------------------------------------------------------------------
    # Statistics (spec 70)
    # ------------------------------------------------------------------

    @staticmethod
    def statistics(structure: KnowledgeStructure) -> dict[str, Any]:
        """Deterministic counts over a structure (no store needed).

        Keys:
        - total_knowledge_points
        - by_validation_status   (dict status -> count, all three keys)
        - by_review_status       (dict status -> count, all four keys)
        - total_supporting_links (sum of distinct evidence refs over KPs)
        - conflicted_count / supported_count / unverified_count
        - total_conflicts, total_relationships, total_review_records
        """
        target = _require_structure(structure)
        by_validation = {st.value: 0 for st in ValidationStatus}
        by_review = {
            "pending": 0,
            "confirmed": 0,
            "rejected": 0,
            "kept_unverified": 0,
        }
        supporting = 0
        for kp in target.knowledge_points.values():
            status = ValidationStatus.from_string(kp.validation_status).value
            by_validation[status] = by_validation.get(status, 0) + 1
            review = ReviewStatus.from_string(kp.review_status).value
            by_review[review] = by_review.get(review, 0) + 1
            supporting += target.supporting_evidence_count(kp.knowledge_id)
        conflicted = by_validation.get("conflicted", 0)
        supported = by_validation.get("supported", 0)
        unverified = by_validation.get("unverified", 0)
        return {
            "total_knowledge_points": len(target.knowledge_points),
            "by_validation_status": by_validation,
            "by_review_status": by_review,
            "total_supporting_links": supporting,
            "conflicted_count": conflicted,
            "supported_count": supported,
            "unverified_count": unverified,
            "total_conflicts": len({c.conflict_id for c in target.conflicts if c.conflict_id}),
            "total_relationships": len(target.relationships),
            "total_review_records": len(target.review_records),
        }


# ---------------------------------------------------------------------------
# Module-level persistence helpers
# ---------------------------------------------------------------------------

def save_structure(structure: KnowledgeStructure, path: Union[str, Path]) -> None:
    """Persist a KnowledgeStructure as canonical UTF-8 JSON.

    JSON is written with sort_keys + ensure_ascii=False + stable
    separators so byte-identical structures produce byte-identical
    files.  The parent directory is created when missing.
    """
    target = _require_structure(structure)
    payload = target.to_dict()
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as handle:
        handle.write(text)


def load_structure(path: Union[str, Path]) -> KnowledgeStructure:
    """Load a structure saved by save_structure(); validate on the way in.

    Raises KnowledgeAssemblyError on: unreadable file, invalid JSON,
    non-mapping payload, unknown validation_status value, missing
    knowledge_id, or duplicate knowledge_id.  Backward compatible:
    missing review_records / knowledge_score keys load fine (the
    structure's own from_dict already defaults them).
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise KnowledgeAssemblyError(f"cannot read {p}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise KnowledgeAssemblyError(f"invalid JSON in {p}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise KnowledgeAssemblyError(
            f"structure payload must be a mapping, got {type(data).__name__}"
        )
    known_statuses = {st.value for st in ValidationStatus}
    for index, kp_data in enumerate(data.get("knowledge_points", [])):
        if not isinstance(kp_data, Mapping):
            raise KnowledgeAssemblyError(
                f"knowledge_points[{index}] must be a mapping"
            )
        status = kp_data.get("validation_status", "unverified")
        if status not in known_statuses:
            raise KnowledgeAssemblyError(
                f"knowledge_points[{index}] has unknown validation_status {status!r}"
            )
        if not kp_data.get("knowledge_id"):
            raise KnowledgeAssemblyError(
                f"knowledge_points[{index}] is missing its knowledge_id"
            )
    seen: set[str] = set()
    for kp_data in data.get("knowledge_points", []):
        kid = kp_data["knowledge_id"]
        if kid in seen:
            raise KnowledgeAssemblyError(f"duplicate knowledge_id {kid!r}")
        seen.add(kid)
    return KnowledgeStructure.from_dict(data)


__all__ = [
    "AssemblyResult",
    "IntegrityReport",
    "KnowledgeAssemblyError",
    "KnowledgeAssembler",
    "save_structure",
    "load_structure",
]
