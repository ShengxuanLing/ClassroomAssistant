from __future__ import annotations
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple

from src.knowledge_structure import KnowledgeStructure, KnowledgePoint
from src.knowledge_validation import KnowledgeValidator
from src.integration import ConflictRecord

# P1-4: ``ValidationStatus`` / ``ReviewStatus`` 的真源已收口到 ``src.models``
# (领域核心), 以消除 ``src.models`` 对上层模块的懒导入。此处保留兼容 re-export,
# 历史 ``from src.knowledge_review import ReviewStatus`` 的调用方无需改动。
from src.models import ReviewStatus, ValidationStatus


class ReviewDecision(str, Enum):
    """The explicit human actions a review service accepts."""
    CONFIRM = "confirm"
    REJECT = "reject"
    KEEP_UNVERIFIED = "keep_unverified"

    @classmethod
    def from_string(cls, value: Any) -> "ReviewDecision":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            for d in cls:
                if d.value.lower() == value.strip().lower():
                    return d
        raise ValueError(f"unknown review decision: {value!r}")


def _stable_review_id(
    knowledge_point_id: str,
    decision: str,
    selected_evidence_ids: Sequence[str],
) -> str:
    """Deterministic business ID: same point + decision + selected evidence
    -> same review_id, so resubmitting an identical action does not create
    duplicate records (idempotency).
    """
    payload = "|".join(
        [knowledge_point_id, decision.lower()]
        + sorted(set(e for e in selected_evidence_ids if e))
    ).encode("utf-8")
    return "rev-" + hashlib.sha256(payload).hexdigest()[:16]


def _review_context_fingerprint(structure: "KnowledgeStructure", kp: "KnowledgePoint") -> str:
    """Stable hash of the evidence/conflict context a review decision was
    made against.

    A human decision is 'sticky' across re-scans of an UNCHANGED structure
    (same evidence + same conflicts + same validation state -> same
    fingerprint -> the decision stands), yet it is automatically re-opened
    when GENUINELY NEW evidence or a new conflict arrives for that point
    after the decision was recorded (fingerprint changes). This is the
    precise fix for defect #13: previously any CONFLICTED point was re-opened
    on every scan, silently discarding the human's resolution and breaking
    determinism across re-runs / restarts.

    The fingerprint folds in the validation_status / needs_verification of the
    point (not just the raw evidence refs), because the pipeline expresses a
    freshly-arrived conflict via those fields even when the new evidence is
    not (yet) attached to the point's own evidence_refs list.
    """
    kp_refs = set(e for e in getattr(kp, "evidence_refs", ()) if e)
    evs = tuple(sorted(kp_refs))
    conflict_evs: list[str] = []
    for c in getattr(structure, "conflicts", ()):
        cref = getattr(c, "evidence_refs", ())
        if any(r in kp_refs for r in cref):
            conflict_evs.extend(r for r in cref if r)
    cf = tuple(sorted(set(conflict_evs)))
    status = ValidationStatus.from_string(getattr(kp, "validation_status", "")).value
    nv = "1" if bool(getattr(kp, "needs_verification", False)) else "0"
    payload = (
        "E:" + "|".join(evs)
        + "#C:" + "|".join(cf)
        + "#S:" + status
        + "#V:" + nv
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


@dataclass(frozen=True)
class ReviewRecord:
    """Immutable record of one human review decision.

    Fields:
    - review_id: stable business ID (see _stable_review_id)
    - knowledge_point_id: which point was reviewed (never modified)
    - decision: CONFIRM / REJECT / KEEP_UNVERIFIED
    - selected_evidence_ids: the specific evidence the user chose to trust
      (required for conflicts; may be empty for non-conflict points)
    - note: optional user remark; deliberately NOT evidence (it is audit
      context only, and is never written into the Evidence base)

    History is append-only: resubmitting the same operation does not
    overwrite an earlier record; the record set is deduplicated by
    review_id so repeated identical submissions stay single.
    """
    review_id: str
    knowledge_point_id: str
    decision: ReviewDecision
    selected_evidence_ids: Tuple[str, ...] = ()
    note: Optional[str] = None
    evidence_fingerprint: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.decision, str):
            object.__setattr__(self, "decision", ReviewDecision.from_string(self.decision))
        selected = tuple(sorted(set(e for e in self.selected_evidence_ids if e)))
        if selected != self.selected_evidence_ids:
            object.__setattr__(self, "selected_evidence_ids", selected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "knowledge_point_id": self.knowledge_point_id,
            "decision": self.decision.value,
            "selected_evidence_ids": list(self.selected_evidence_ids),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReviewRecord":
        return cls(
            review_id=data.get("review_id", ""),
            knowledge_point_id=data.get("knowledge_point_id", ""),
            decision=data.get("decision", ReviewDecision.KEEP_UNVERIFIED.value),
            selected_evidence_ids=tuple(data.get("selected_evidence_ids", ())),
            note=data.get("note"),
        )


@dataclass(frozen=True)
class ReviewCandidate:
    """A KnowledgePoint that currently needs human attention.

    Pure view/projection: computed from the structure's
    validation_status / needs_verification / knowledge_score / evidence
    refs / conflicts. It is NOT a source of facts; the underlying
    KnowledgePoint remains authoritative.
    """
    knowledge_point_id: str
    validation_status: ValidationStatus
    knowledge_score: float
    supporting_evidence_ids: Tuple[str, ...]
    conflict_ids: Tuple[str, ...]
    needs_verification: bool
    priority: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_point_id": self.knowledge_point_id,
            "validation_status": self.validation_status.value,
            "knowledge_score": self.knowledge_score,
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "conflict_ids": list(self.conflict_ids),
            "needs_verification": self.needs_verification,
            "priority": self.priority,
        }


class KnowledgeReviewService:
    """Human-in-the-loop review layer over KnowledgeStructure (Task 15).

    Responsibilities:
    - find review candidates (deterministic, stable ordering)
    - record explicit human decisions as ReviewRecords (stable IDs)
    - update each KnowledgePoint's review_status WITHOUT touching
      validation_status, evidence, or conflict records

    Invariants (hard rules):
    - No automatic confirmation: SUPPORTED / high knowledge_score / many
      supporting evidence items never produce ReviewStatus.CONFIRMED on
      their own.
    - No automatic rejection: CONFLICTED never produces ReviewStatus.
      REJECTED without an explicit human REJECT.
    - No automatic conflict resolution: a conflict can only be resolved
      by the user explicitly selecting which evidence side(s) to trust
      (resolve_conflict / confirm with selected evidence).
    - Review operations never mutate Evidence or delete KnowledgePoint /
      ConflictRecord objects.
    - Review notes are user remarks, never written into Evidence.
    """

    # ------------------------------------------------------------------
    # Candidate discovery
    # ------------------------------------------------------------------

    @staticmethod
    def _priority_for(status: ValidationStatus, needs_verification: bool) -> int:
        if status == ValidationStatus.CONFLICTED:
            return 0
        if status == ValidationStatus.UNVERIFIED:
            return 1
        return 2

    def get_review_candidates(self, structure: KnowledgeStructure) -> list[ReviewCandidate]:
        """Deterministic review-queue projection over a KnowledgeStructure.

        Inclusion rule:
        - A point whose review_status is already CONFIRMED / REJECTED /
          KEPT_UNVERIFIED is normally NOT a candidate: human decisions are
          sticky and are never silently reverted by a later scan of an
          unchanged structure (a resolved CONFLICTED point therefore stays
          resolved across re-runs / restarts -> determinism).
        - Exception (re-open): if new evidence or a new conflict has arrived
          for that point since the decision was recorded (detected via a
          context fingerprint), the point is put back into PENDING and
          re-surfaces as a candidate so the human can re-verify.
        - CONFLICTED with review_status == PENDING -> candidate (priority 0)
        - UNVERIFIED with review_status == PENDING -> candidate (priority 1)
        - SUPPORTED with review_status == PENDING -> candidate (priority 2),
          whether or not needs_verification is set (evidence-consistent, but
          no explicit human confirmation yet)

        Ordering: priority ascending, then knowledge_point_id ascending.
        No randomness, no timestamps, no object-identity tie-breaks.
        """
        candidates: list[ReviewCandidate] = []
        for kp in sorted(
            structure.knowledge_points.values(), key=lambda k: k.knowledge_id
        ):
            status = ValidationStatus.from_string(kp.validation_status)
            # Use the stored validation facts written by the pipeline
            # (kp.needs_verification / kp.knowledge_score). Recalculating
            # from an empty evidence list would lose SUPPORTED state and
            # wrongly re-flag already-stable points.
            stored_needs_verification = bool(getattr(kp, "needs_verification", False))
            seen: set[str] = set()
            supporting: list[str] = []
            for ref in kp.evidence_refs:
                if ref and ref not in seen:
                    seen.add(ref)
                    supporting.append(ref)
            conflict_ids = tuple(sorted(structure.conflict_ids_for_knowledge_point(kp.knowledge_id)))
            needs_verification = stored_needs_verification or status in (
                ValidationStatus.UNVERIFIED,
                ValidationStatus.CONFLICTED,
            )
            review_status = ReviewStatus.from_string(
                getattr(kp, "review_status", "pending")
            )

            # Human decisions are STICKY, but not frozen forever. A point that
            # already carries an explicit decision (CONFIRMED / REJECTED /
            # KEPT_UNVERIFIED) is normally NOT a candidate. It is re-opened
            # (review_status -> PENDING) ONLY when the evidence/conflict context
            # has genuinely changed since that decision was recorded: i.e. new
            # evidence or a new conflict has arrived for this point. We detect
            # that by comparing the current context fingerprint against the
            # fingerprint stored on the latest ReviewRecord.
            #
            # This is the precise fix for defect #13: previously any CONFLICTED
            # point was re-opened on every scan, silently discarding the human's
            # resolution and breaking determinism across re-runs / restarts. Now
            # an unchanged structure keeps the decision (no mutation, identical
            # candidates every time) while a genuinely new conflict still
            # surfaces for a fresh human look.
            if review_status != ReviewStatus.PENDING:
                records = structure.review_records_for_knowledge_point(kp.knowledge_id)
                if records:
                    latest_fp = records[-1].evidence_fingerprint
                    current_fp = _review_context_fingerprint(structure, kp)
                    if latest_fp and latest_fp != current_fp:
                        # Genuinely new evidence/conflict since the decision -> re-open.
                        kp.review_status = ReviewStatus.PENDING.value
                        review_status = ReviewStatus.PENDING
                    else:
                        continue  # sticky: context unchanged
                else:
                    continue  # decided but no record -> leave as is

            if status == ValidationStatus.CONFLICTED or status == ValidationStatus.UNVERIFIED:
                pass  # pending + unverified/conflicted -> candidate
            elif status == ValidationStatus.SUPPORTED and review_status == ReviewStatus.PENDING:
                pass  # supported but not yet explicitly reviewed
            elif status == ValidationStatus.SUPPORTED and needs_verification:
                pass  # explicitly flagged for verification
            else:
                continue  # supported + pending + stable

            cand = ReviewCandidate(
                knowledge_point_id=kp.knowledge_id,
                validation_status=status,
                knowledge_score=getattr(kp, "knowledge_score", 0.0) or 0.0,
                supporting_evidence_ids=tuple(supporting),
                conflict_ids=conflict_ids,
                needs_verification=needs_verification,
                priority=self._priority_for(status, needs_verification),
            )
            candidates.append(cand)
        candidates.sort(key=lambda c: (c.priority, c.knowledge_point_id))
        return candidates

    # ------------------------------------------------------------------
    # Human decisions
    # ------------------------------------------------------------------

    @staticmethod
    def _get_knowledge_point(structure: KnowledgeStructure, knowledge_point_id: str) -> KnowledgePoint:
        kp = structure.knowledge_points.get(knowledge_point_id)
        if kp is None:
            raise KeyError(f"knowledge_point_id not found in structure: {knowledge_point_id!r}")
        return kp

    @staticmethod
    def _allowed_evidence_refs(structure: KnowledgeStructure, kp: KnowledgePoint) -> set[str]:
        """The union of this KP's own evidence refs and the refs of any
        conflict that touches this KP. Anything else is unrelated and
        must not be silently attached."""
        allowed = set(kp.evidence_refs)
        kp_refs = set(kp.evidence_refs)
        for c in structure.conflicts:
            if any(r in kp_refs for r in c.evidence_refs):
                allowed.update(c.evidence_refs)
        return allowed

    @staticmethod
    def _validate_selected_evidence(
        structure: KnowledgeStructure,
        kp: KnowledgePoint,
        selected_evidence_ids: Sequence[str],
    ) -> Tuple[str, ...]:
        allowed = KnowledgeReviewService._allowed_evidence_refs(structure, kp)
        normalized = tuple(sorted(set(e for e in selected_evidence_ids if e)))
        for eid in normalized:
            if allowed and eid not in allowed:
                raise ValueError(
                    f"selected evidence {eid!r} does not belong to knowledge point "
                    f"{kp.knowledge_id!r} (not among its evidence refs nor among "
                    f"evidence involved in its conflicts)"
                )
        return normalized

    def record_decision(
        self,
        structure: KnowledgeStructure,
        knowledge_point_id: str,
        decision: ReviewDecision,
        selected_evidence_ids: Sequence[str] = (),
        note: Optional[str] = None,
    ) -> ReviewRecord:
        """Record one explicit human decision.

        - CONFIRM on a CONFLICTED point requires a non-empty, valid
          selected_evidence_ids (the user must pick which side to trust).
        - REJECT / KEEP_UNVERIFIED do not require selections.
        - review_status is updated to match the decision; validation_status,
          evidence, and conflict records are left untouched.
        - Idempotent: resubmitting the same (point, decision, selected
          evidence) returns the same stable review_id and does not create
          a duplicate record.
        """
        kp = self._get_knowledge_point(structure, knowledge_point_id)
        status = ValidationStatus.from_string(kp.validation_status)
        if decision == ReviewDecision.CONFIRM and status == ValidationStatus.CONFLICTED:
            selected = self._validate_selected_evidence(
                structure, kp, selected_evidence_ids
            )
            if not selected:
                raise ValueError(
                    "confirming a CONFLICTED knowledge point requires explicitly "
                    "selecting which evidence side(s) to trust; an empty selection "
                    "would not actually resolve the conflict"
                )
        else:
            # Confirming / rejecting / keeping a non-conflicted point without an
            # explicit selection is pinned to the point's own supporting evidence
            # so every record carries a factual basis. Unrelated evidence IDs are
            # still rejected by the validator below.
            if selected_evidence_ids:
                selected = self._validate_selected_evidence(
                    structure, kp, selected_evidence_ids
                )
            else:
                selected = tuple(sorted(set(e for e in kp.evidence_refs if e)))
        review_id = _stable_review_id(knowledge_point_id, decision.value, selected)
        record = ReviewRecord(
            review_id=review_id,
            knowledge_point_id=knowledge_point_id,
            decision=decision,
            selected_evidence_ids=selected,
            note=note,
            evidence_fingerprint=_review_context_fingerprint(structure, kp),
        )
        structure.add_review_record(record)
        new_status = {
            ReviewDecision.CONFIRM: ReviewStatus.CONFIRMED,
            ReviewDecision.REJECT: ReviewStatus.REJECTED,
            ReviewDecision.KEEP_UNVERIFIED: ReviewStatus.KEPT_UNVERIFIED,
        }[decision]
        kp.review_status = new_status.value
        return record

    def confirm(
        self,
        structure: KnowledgeStructure,
        knowledge_point_id: str,
        selected_evidence_ids: Sequence[str] = (),
        note: Optional[str] = None,
    ) -> ReviewRecord:
        return self.record_decision(
            structure,
            knowledge_point_id,
            ReviewDecision.CONFIRM,
            selected_evidence_ids=selected_evidence_ids,
            note=note,
        )

    def reject(
        self,
        structure: KnowledgeStructure,
        knowledge_point_id: str,
        note: Optional[str] = None,
    ) -> ReviewRecord:
        return self.record_decision(
            structure, knowledge_point_id, ReviewDecision.REJECT, note=note
        )

    def keep_unverified(
        self,
        structure: KnowledgeStructure,
        knowledge_point_id: str,
        note: Optional[str] = None,
    ) -> ReviewRecord:
        return self.record_decision(
            structure, knowledge_point_id, ReviewDecision.KEEP_UNVERIFIED, note=note
        )

    def resolve_conflict(
        self,
        structure: KnowledgeStructure,
        knowledge_point_id: str,
        selected_evidence_ids: Sequence[str],
        note: Optional[str] = None,
    ) -> ReviewRecord:
        """Explicitly pick which evidence side(s) of a conflict are trusted.

        Requires a non-empty selection whose IDs belong to the knowledge
        point (its own refs or one of its conflicts). Records a CONFIRM
        decision whose selected_evidence_ids pin the chosen side(s). The
        ConflictRecord and both evidence items are preserved for audit;
        only the knowledge point's review_status moves to CONFIRMED.
        """
        if not [e for e in selected_evidence_ids if e]:
            raise ValueError(
                "resolve_conflict requires at least one explicitly selected "
                "evidence ID; an empty selection would not actually resolve "
                "the conflict (use keep_unverified instead)"
            )
        return self.record_decision(
            structure,
            knowledge_point_id,
            ReviewDecision.CONFIRM,
            selected_evidence_ids=selected_evidence_ids,
            note=note,
        )

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    @staticmethod
    def review_status_of(structure: KnowledgeStructure, knowledge_point_id: str) -> ReviewStatus:
        kp = structure.knowledge_points.get(knowledge_point_id)
        if kp is None:
            raise KeyError(f"knowledge_point_id not found: {knowledge_point_id!r}")
        return ReviewStatus.from_string(getattr(kp, "review_status", "pending"))

    @staticmethod
    def review_history(
        structure: KnowledgeStructure, knowledge_point_id: str
    ) -> list[ReviewRecord]:
        return structure.review_records_for_knowledge_point(knowledge_point_id)

    @staticmethod
    def latest_review(
        structure: KnowledgeStructure, knowledge_point_id: str
    ) -> Optional[ReviewRecord]:
        history = structure.review_records_for_knowledge_point(knowledge_point_id)
        return history[-1] if history else None

    @staticmethod
    def has_conflict(structure: KnowledgeStructure, knowledge_point_id: str) -> bool:
        return structure.conflict_count(knowledge_point_id) > 0
