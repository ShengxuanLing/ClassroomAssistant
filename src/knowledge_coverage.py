"""Course knowledge coverage and gap analysis for the Classroom Assistant.

Task 27 - Course Knowledge Coverage & Gap Analysis Layer.

Pure, deterministic analysis layer on top of Task 26
(``KnowledgeOrganizationService``).  It reads the existing course
knowledge organization - topics, memberships, sessions, KPs, and their
validation / review state - and produces immutable structural reports.

Core semantics (analysis != inference):
- COVERED: a KnowledgePoint owns at least one SessionKnowledgeMembership
  in this course (strictly a session concept).
- ASSIGNED: a KnowledgePoint owns at least one Topic membership.
- Coverage says NOTHING about ValidationStatus, ReviewStatus or student
  mastery.  Uncovered does not mean unnecessary; unverified does not
  mean false; conflicted never triggers automatic resolution.

Guarantees:
- No LLM, embedding, semantic matching, translation, recommendation,
  student model, or database of any kind.
- Reports are frozen, immutable snapshots; re-running the analysis is
  idempotent, and mutating the service after a report is produced does
  not change an already-returned report.
- Every public ordering is deterministic and insertion-order
  independent:
    * KP ids: ascending string order.
    * Topics: ``(order_index, topic_id)`` with ``None`` last.
    * Sessions: positive ``session_number`` ascending, unnumbered
      sessions after all numbered ones, tie-break by ``session_id``.
    * Gap types inside a ``KnowledgeGap``: declaration order of
      ``KnowledgeGapType`` - never alphabetical.
- Sets are used for counting only; every business output list/tuple
  is deterministically sorted.
- Serialization: ``schema_version = 1``; unknown versions raise
  ``CoverageSchemaError``.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Set, Tuple

from src.knowledge_organization import KnowledgeOrganizationService
from src.knowledge_validation import ValidationStatus
from src.knowledge_review import ReviewStatus

SCHEMA_VERSION = 1

_UNNUMBERED_SESSION_RANK = 10 ** 9


def _session_rank(session_id: str, session_order: Mapping[str, int]) -> Tuple[int, str]:
    """Deterministic session ordering key: numbered sessions (lower
    ``session_number`` first), unnumbered last, tie-break by id."""
    return (session_order.get(session_id, _UNNUMBERED_SESSION_RANK), session_id)


class CoverageErrorCode(str, Enum):
    """Stable machine-readable error codes for coverage analysis errors."""

    INVALID_INPUT = "INVALID_INPUT"
    INVALID_STRUCTURE = "INVALID_STRUCTURE"
    INVALID_SESSION_ORDER = "INVALID_SESSION_ORDER"
    MISSING_KNOWLEDGE_POINT = "MISSING_KNOWLEDGE_POINT"
    MISSING_TOPIC = "MISSING_TOPIC"
    MISSING_SESSION = "MISSING_SESSION"
    INVALID_SCHEMA_VERSION = "INVALID_SCHEMA_VERSION"


class CoverageAnalysisError(Exception):
    """Base class for coverage analysis errors.

    Carries a stable ``code`` (CoverageErrorCode) so callers can branch
    on the error kind without parsing messages.
    """

    def __init__(self, code: CoverageErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class CoverageValidationError(CoverageAnalysisError):
    """Raised when an input reference or structure fails validation."""


class CoverageSchemaError(CoverageAnalysisError):
    """Raised when a serialized report has an unknown schema version."""


class KnowledgeCoverageStatus(str, Enum):
    """Structural session coverage of a KnowledgePoint.

    A learning / mastery judgement is deliberately NOT part of this
    enum: no MASTERED / LEARNED / WEAK / STRONG states.
    """

    COVERED = "covered"
    UNCOVERED = "uncovered"

    @classmethod
    def from_membership_count(cls, count: int) -> "KnowledgeCoverageStatus":
        return cls.COVERED if count >= 1 else cls.UNCOVERED


class KnowledgeGapType(str, Enum):
    """Structural data-state gaps.  These are data states, never
    claims about student deficiency (no ``student_gap`` etc.)."""

    UNASSIGNED = "unassigned"
    UNCOVERED = "uncovered"
    CONFLICTED = "conflicted"
    UNVERIFIED = "unverified"
    REVIEW_PENDING = "review_pending"


def _gap_type_order(gap_types: Tuple[KnowledgeGapType, ...]) -> Tuple[KnowledgeGapType, ...]:
    """Stable, declaration-order sort (never alphabetical)."""
    order = {g: i for i, g in enumerate(KnowledgeGapType)}
    return tuple(sorted(gap_types, key=lambda g: order[g]))


def _require_schema_version(data: Mapping[str, Any]) -> None:
    if not isinstance(data, Mapping):
        raise CoverageValidationError(
            CoverageErrorCode.INVALID_STRUCTURE, "report payload must be a mapping"
        )
    if data.get("schema_version") != SCHEMA_VERSION:
        raise CoverageSchemaError(
            CoverageErrorCode.INVALID_SCHEMA_VERSION,
            f"unsupported schema_version: {data.get('schema_version')!r} (expected {SCHEMA_VERSION})",
        )


def _ratio(part: int, total: int) -> float:
    return (part / total) if total else 0.0


def _parse_gap_types(raw: Any) -> Tuple[KnowledgeGapType, ...]:
    out: Set[KnowledgeGapType] = set()
    for value in raw or []:
        if isinstance(value, KnowledgeGapType):
            out.add(value)
        else:
            try:
                out.add(KnowledgeGapType(str(value).lower().strip()))
            except ValueError:
                out.add(KnowledgeGapType(str(value)))
    return _gap_type_order(out)


@dataclass(frozen=True)
class KnowledgeGap:
    """All structural gaps of one KnowledgePoint (multiple allowed)."""

    knowledge_point_id: str
    gap_types: Tuple[KnowledgeGapType, ...] = ()
    topic_ids: Tuple[str, ...] = ()
    session_count: int = 0
    validation_status: str = ValidationStatus.UNVERIFIED.value
    review_status: str = ReviewStatus.PENDING.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "knowledge_point_id": self.knowledge_point_id,
            "gap_types": [g.value for g in self.gap_types],
            "topic_ids": list(self.topic_ids),
            "session_count": self.session_count,
            "validation_status": self.validation_status,
            "review_status": self.review_status,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "KnowledgeGap":
        _require_schema_version(data)
        return cls(
            knowledge_point_id=str(data.get("knowledge_point_id") or ""),
            gap_types=_parse_gap_types(data.get("gap_types")),
            topic_ids=tuple(sorted(str(t) for t in (data.get("topic_ids") or []))),
            session_count=int(data.get("session_count") or 0),
            validation_status=str(data.get("validation_status") or ValidationStatus.UNVERIFIED.value),
            review_status=str(data.get("review_status") or ReviewStatus.PENDING.value),
        )


@dataclass(frozen=True)
class KnowledgeCoverageReport:
    """Course-level structural coverage and assignment statistics.

    ``coverage_ratio`` = covered / total KP count (0.0 when total is 0).
    ``assignment_ratio`` = topic-assigned / total KP count.
    """

    course_id: str
    total_knowledge_points: int
    covered_knowledge_points: int
    uncovered_knowledge_points: int
    assigned_knowledge_points: int
    unassigned_knowledge_points: int
    conflicted_knowledge_points: int
    unverified_knowledge_points: int
    review_pending_knowledge_points: int
    coverage_ratio: float
    assignment_ratio: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "course_id": self.course_id,
            "total_knowledge_points": self.total_knowledge_points,
            "covered_knowledge_points": self.covered_knowledge_points,
            "uncovered_knowledge_points": self.uncovered_knowledge_points,
            "assigned_knowledge_points": self.assigned_knowledge_points,
            "unassigned_knowledge_points": self.unassigned_knowledge_points,
            "conflicted_knowledge_points": self.conflicted_knowledge_points,
            "unverified_knowledge_points": self.unverified_knowledge_points,
            "review_pending_knowledge_points": self.review_pending_knowledge_points,
            "coverage_ratio": self.coverage_ratio,
            "assignment_ratio": self.assignment_ratio,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "KnowledgeCoverageReport":
        _require_schema_version(data)
        return cls(
            course_id=str(data.get("course_id") or ""),
            total_knowledge_points=int(data.get("total_knowledge_points") or 0),
            covered_knowledge_points=int(data.get("covered_knowledge_points") or 0),
            uncovered_knowledge_points=int(data.get("uncovered_knowledge_points") or 0),
            assigned_knowledge_points=int(data.get("assigned_knowledge_points") or 0),
            unassigned_knowledge_points=int(data.get("unassigned_knowledge_points") or 0),
            conflicted_knowledge_points=int(data.get("conflicted_knowledge_points") or 0),
            unverified_knowledge_points=int(data.get("unverified_knowledge_points") or 0),
            review_pending_knowledge_points=int(data.get("review_pending_knowledge_points") or 0),
            coverage_ratio=float(data.get("coverage_ratio") or 0.0),
            assignment_ratio=float(data.get("assignment_ratio") or 0.0),
        )


@dataclass(frozen=True)
class TopicCoverageReport:
    """Per-topic structural coverage among that topic's KPs."""

    topic_id: str
    total_knowledge_points: int
    covered_knowledge_points: int
    uncovered_knowledge_points: int
    conflicted_knowledge_points: int
    unverified_knowledge_points: int
    review_pending_knowledge_points: int
    coverage_ratio: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "topic_id": self.topic_id,
            "total_knowledge_points": self.total_knowledge_points,
            "covered_knowledge_points": self.covered_knowledge_points,
            "uncovered_knowledge_points": self.uncovered_knowledge_points,
            "conflicted_knowledge_points": self.conflicted_knowledge_points,
            "unverified_knowledge_points": self.unverified_knowledge_points,
            "review_pending_knowledge_points": self.review_pending_knowledge_points,
            "coverage_ratio": self.coverage_ratio,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TopicCoverageReport":
        _require_schema_version(data)
        total = int(data.get("total_knowledge_points") or 0)
        covered = int(data.get("covered_knowledge_points") or 0)
        return cls(
            topic_id=str(data.get("topic_id") or ""),
            total_knowledge_points=total,
            covered_knowledge_points=covered,
            uncovered_knowledge_points=max(0, total - covered),
            conflicted_knowledge_points=int(data.get("conflicted_knowledge_points") or 0),
            unverified_knowledge_points=int(data.get("unverified_knowledge_points") or 0),
            review_pending_knowledge_points=int(data.get("review_pending_knowledge_points") or 0),
            coverage_ratio=float(data.get("coverage_ratio") or 0.0),
        )


@dataclass(frozen=True)
class SessionCoveragePoint:
    """Per-session statistics on its knowledge points.

    ``knowledge_point_count`` counts the DISTINCT KPs of the session;
    duplicate membership records never inflate the numbers.
    """

    session_id: str
    knowledge_point_count: int
    unique_knowledge_point_count: int
    new_knowledge_point_count: int
    repeated_knowledge_point_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "knowledge_point_count": self.knowledge_point_count,
            "unique_knowledge_point_count": self.unique_knowledge_point_count,
            "new_knowledge_point_count": self.new_knowledge_point_count,
            "repeated_knowledge_point_count": self.repeated_knowledge_point_count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SessionCoveragePoint":
        _require_schema_version(data)
        total = int(data.get("knowledge_point_count") or 0)
        repeated = int(data.get("repeated_knowledge_point_count") or 0)
        return cls(
            session_id=str(data.get("session_id") or ""),
            knowledge_point_count=total,
            unique_knowledge_point_count=int(data.get("unique_knowledge_point_count") or 0),
            new_knowledge_point_count=max(0, total - repeated),
            repeated_knowledge_point_count=repeated,
        )


@dataclass(frozen=True)
class SessionCoverageReport:
    """Coverage statistics for a single session.

    ``new`` = the KP's first appearance in any session up to and
    including this one; ``repeated`` = already appeared in an earlier
    session.  Pure membership arithmetic - no semantic judgement.
    """

    session_id: str
    knowledge_point_count: int
    unique_knowledge_point_count: int
    new_knowledge_point_count: int
    repeated_knowledge_point_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "knowledge_point_count": self.knowledge_point_count,
            "unique_knowledge_point_count": self.unique_knowledge_point_count,
            "new_knowledge_point_count": self.new_knowledge_point_count,
            "repeated_knowledge_point_count": self.repeated_knowledge_point_count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SessionCoverageReport":
        _require_schema_version(data)
        total = int(data.get("knowledge_point_count") or 0)
        repeated = int(data.get("repeated_knowledge_point_count") or 0)
        return cls(
            session_id=str(data.get("session_id") or ""),
            knowledge_point_count=total,
            unique_knowledge_point_count=int(data.get("unique_knowledge_point_count") or 0),
            new_knowledge_point_count=max(0, total - repeated),
            repeated_knowledge_point_count=repeated,
        )


@dataclass(frozen=True)
class CourseCoverageTimeline:
    """New / repeated KP counts per session, in deterministic session
    order.  One ``SessionCoveragePoint`` per registered session."""

    course_id: str
    sessions: Tuple[SessionCoveragePoint, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "course_id": self.course_id,
            "sessions": [s.to_dict() for s in self.sessions],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CourseCoverageTimeline":
        _require_schema_version(data)
        return cls(
            course_id=str(data.get("course_id") or ""),
            sessions=tuple(
                SessionCoveragePoint.from_dict(s) for s in (data.get("sessions") or [])
            ),
        )


@dataclass(frozen=True)
class KnowledgeFrequency:
    """How many distinct sessions and topics reference one KP."""

    knowledge_point_id: str
    session_count: int
    topic_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "knowledge_point_id": self.knowledge_point_id,
            "session_count": self.session_count,
            "topic_count": self.topic_count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "KnowledgeFrequency":
        _require_schema_version(data)
        return cls(
            knowledge_point_id=str(data.get("knowledge_point_id") or ""),
            session_count=int(data.get("session_count") or 0),
            topic_count=int(data.get("topic_count") or 0),
        )


@dataclass(frozen=True)
class CourseGapReport:
    """All structural gaps of a course, deterministically ordered."""

    course_id: str
    gaps: Tuple[KnowledgeGap, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "course_id": self.course_id,
            "gaps": [g.to_dict() for g in self.gaps],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CourseGapReport":
        _require_schema_version(data)
        return cls(
            course_id=str(data.get("course_id") or ""),
            gaps=tuple(KnowledgeGap.from_dict(g) for g in (data.get("gaps") or [])),
        )


@dataclass(frozen=True)
class ConflictCoverageItem:
    """One conflicted KnowledgePoint.

    ``conflict_count`` counts the UNRESOLVED ConflictRecords that touch
    the KP's evidence refs, when the KnowledgeStructure(s) that
    registered the KP expose them.  When no conflict records are
    accessible the count degrades to 1 for conflicted KPs and 0
    otherwise - a state marker, never a re-implementation of the
    conflict detection algorithm (spec section 28).
    """

    knowledge_point_id: str
    conflict_count: int
    review_status: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "knowledge_point_id": self.knowledge_point_id,
            "conflict_count": self.conflict_count,
            "review_status": self.review_status,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConflictCoverageItem":
        _require_schema_version(data)
        return cls(
            knowledge_point_id=str(data.get("knowledge_point_id") or ""),
            conflict_count=int(data.get("conflict_count") or 0),
            review_status=str(data.get("review_status") or ReviewStatus.PENDING.value),
        )


@dataclass(frozen=True)
class KnowledgeOrphanReport:
    """KPs missing from the structural graph (never assigned and/or
    never session-covered).  A data state, not a judgment."""

    unassigned_knowledge_point_ids: Tuple[str, ...] = ()
    uncovered_knowledge_point_ids: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "unassigned_knowledge_point_ids": list(self.unassigned_knowledge_point_ids),
            "uncovered_knowledge_point_ids": list(self.uncovered_knowledge_point_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "KnowledgeOrphanReport":
        _require_schema_version(data)
        return cls(
            unassigned_knowledge_point_ids=tuple(
                sorted(str(x) for x in (data.get("unassigned_knowledge_point_ids") or []))
            ),
            uncovered_knowledge_point_ids=tuple(
                sorted(str(x) for x in (data.get("uncovered_knowledge_point_ids") or []))
            ),
        )


@dataclass(frozen=True)
class KnowledgeEvidenceCoverage:
    """Unique evidence refs attached to one KP (counting only)."""

    knowledge_point_id: str
    evidence_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "knowledge_point_id": self.knowledge_point_id,
            "evidence_count": self.evidence_count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "KnowledgeEvidenceCoverage":
        _require_schema_version(data)
        return cls(
            knowledge_point_id=str(data.get("knowledge_point_id") or ""),
            evidence_count=int(data.get("evidence_count") or 0),
        )


@dataclass(frozen=True)
class ValidationReviewMatrix:
    """Cross-tabulation of ValidationStatus x ReviewStatus.

    Outer key = ValidationStatus value, inner key = ReviewStatus value;
    every cell is an int and the sum of ALL cells equals the total KP
    count of the course.
    """

    counts: Dict[str, Dict[str, int]]

    @classmethod
    def build(cls) -> "ValidationReviewMatrix":
        return cls(
            counts={v.value: {r.value: 0 for r in ReviewStatus} for v in ValidationStatus}
        )

    def __post_init__(self) -> None:
        full = {(v.value, r.value) for v in ValidationStatus for r in ReviewStatus}
        cells: Set[Tuple[str, str]] = set()
        for outer, row in self.counts.items():
            for key in row:
                cells.add((str(outer), str(key)))
                int(row[key])
        missing = full - cells
        extra = cells - full
        if missing or extra:
            raise CoverageValidationError(
                CoverageErrorCode.INVALID_STRUCTURE,
                f"matrix keys invalid: missing={sorted(missing)} extra={sorted(extra)}",
            )

    @property
    def total(self) -> int:
        return sum(int(v) for row in self.counts.values() for v in row.values())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "counts": {outer: dict(row) for outer, row in sorted(self.counts.items())},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ValidationReviewMatrix":
        _require_schema_version(data)
        counts = {k: dict(row) for k, row in cls.build().counts.items()}
        raw = data.get("counts") or {}
        if isinstance(raw, Mapping):
            for outer, row in raw.items():
                outer_key = str(outer)
                if outer_key not in counts:
                    raise CoverageValidationError(
                        CoverageErrorCode.INVALID_STRUCTURE,
                        f"unknown validation status key: {outer_key!r}",
                    )
                if not isinstance(row, Mapping):
                    raise CoverageValidationError(
                        CoverageErrorCode.INVALID_STRUCTURE,
                        f"matrix row {outer_key!r} must be a mapping",
                    )
                for inner, value in row.items():
                    key = str(inner)
                    if key not in counts[outer_key]:
                        raise CoverageValidationError(
                            CoverageErrorCode.INVALID_STRUCTURE,
                            f"unknown review status key: {key!r}",
                        )
                    counts[outer_key][key] = int(value)
        return cls(counts=counts)


@dataclass(frozen=True)
class CourseKnowledgeStatusSummary:
    """High-level structural state summary of a course (data states
    only - no inference about learning)."""

    course_id: str
    total_knowledge_points: int
    coverage_ratio: float
    assignment_ratio: float
    conflicted_ratio: float
    unverified_ratio: float
    review_pending_ratio: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "course_id": self.course_id,
            "total_knowledge_points": self.total_knowledge_points,
            "coverage_ratio": self.coverage_ratio,
            "assignment_ratio": self.assignment_ratio,
            "conflicted_ratio": self.conflicted_ratio,
            "unverified_ratio": self.unverified_ratio,
            "review_pending_ratio": self.review_pending_ratio,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CourseKnowledgeStatusSummary":
        _require_schema_version(data)
        return cls(
            course_id=str(data.get("course_id") or ""),
            total_knowledge_points=int(data.get("total_knowledge_points") or 0),
            coverage_ratio=float(data.get("coverage_ratio") or 0.0),
            assignment_ratio=float(data.get("assignment_ratio") or 0.0),
            conflicted_ratio=float(data.get("conflicted_ratio") or 0.0),
            unverified_ratio=float(data.get("unverified_ratio") or 0.0),
            review_pending_ratio=float(data.get("review_pending_ratio") or 0.0),
        )


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class _CourseSnapshot:
    """One-pass read-only snapshot of everything the reports need.

    Built exactly once per analyzer; every report below runs on this
    frozen picture and never re-scans the live service.  The input
    service is read only - nothing in it is ever mutated.
    """

    __slots__ = (
        "course_id",
        "kp_ids",
        "kp_topic_ids",
        "topic_kp_ids",
        "kp_session_ids",
        "session_kp_ids",
        "topic_order_index",
        "session_order",
        "validation_status",
        "review_status",
        "evidence_counts",
        "conflict_counts",
    )

    def __init__(self, service: KnowledgeOrganizationService,
                 conflict_resolver: Optional[Callable]) -> None:
        from src.knowledge_organization import _order_key

        course = service.course
        structure = service.structure
        self.course_id = course.course_id

        kp_by_id = service._kp_by_id
        sessions_by_id = service._sessions_by_id
        self.kp_ids = tuple(sorted(kp_by_id))

        kp_topic: Dict[str, set] = {}
        for m in structure.knowledge_memberships.values():
            kp_topic.setdefault(m.knowledge_point_id, set()).add(m.topic_id)
        self.kp_topic_ids: Dict[str, FrozenSet[str]] = {
            kp: frozenset(kp_topic.get(kp, ())) for kp in self.kp_ids
        }
        topic_kp: Dict[str, set] = {}
        for m in structure.knowledge_memberships.values():
            topic_kp.setdefault(m.topic_id, set()).add(m.knowledge_point_id)
        self.topic_kp_ids: Dict[str, FrozenSet[str]] = {
            k: frozenset(v) for k, v in topic_kp.items()
        }
        kp_session: Dict[str, set] = {}
        for m in structure.session_memberships.values():
            kp_session.setdefault(m.knowledge_point_id, set()).add(m.session_id)
        self.kp_session_ids: Dict[str, FrozenSet[str]] = {
            kp: frozenset(kp_session.get(kp, ())) for kp in self.kp_ids
        }
        session_kp: Dict[str, set] = {}
        for m in structure.session_memberships.values():
            session_kp.setdefault(m.session_id, set()).add(m.knowledge_point_id)
        self.session_kp_ids: Dict[str, FrozenSet[str]] = {
            sid: frozenset(session_kp.get(sid, ())) for sid in sessions_by_id
        }
        self.topic_order_index: Dict[str, Tuple] = {
            topic_id: _order_key(topic.order_index)
            for topic_id, topic in structure.topics.items()
        }
        self.session_order: Dict[str, int] = {
            sid: (s.session_number if s.session_number > 0 else _UNNUMBERED_SESSION_RANK)
            for sid, s in sessions_by_id.items()
        }
        self.validation_status: Dict[str, ValidationStatus] = {}
        self.review_status: Dict[str, ReviewStatus] = {}
        self.evidence_counts: Dict[str, int] = {}
        self.conflict_counts: Dict[str, int] = {}
        for kp in self.kp_ids:
            point = kp_by_id[kp]
            self.validation_status[kp] = ValidationStatus.from_string(
                point.validation_status
            )
            self.review_status[kp] = ReviewStatus.from_string(point.review_status)
            refs = list(point.evidence_refs)
            self.evidence_counts[kp] = len(set(r for r in refs if r))
            if conflict_resolver is not None:
                self.conflict_counts[kp] = int(conflict_resolver(kp, refs))
            else:
                self.conflict_counts[kp] = (
                    1 if self.validation_status[kp] == ValidationStatus.CONFLICTED else 0
                )

    # ------------------------------------------------------------------

    def ordered_topic_ids(self) -> Tuple[str, ...]:
        """Topic ids ordered by (order_index, topic_id); None last."""
        return tuple(sorted(self.topic_kp_ids.keys(), key=lambda t: (self.topic_order_index.get(t, (1, 0)), t)))

    def ordered_session_ids(self) -> Tuple[str, ...]:
        """Registered session ids in deterministic chronological order:
        session_number ascending, unnumbered last, tie-break by id."""
        return tuple(
            sorted(self.session_kp_ids.keys(), key=lambda s: _session_rank(s, self.session_order))
        )

    def gaps_for(self, kp: str) -> Tuple[KnowledgeGapType, ...]:
        """All structural gap types of one KP (multiple allowed)."""
        types: Set[KnowledgeGapType] = set()
        if not self.kp_topic_ids.get(kp):
            types.add(KnowledgeGapType.UNASSIGNED)
        if not self.kp_session_ids.get(kp):
            types.add(KnowledgeGapType.UNCOVERED)
        vstatus = self.validation_status.get(kp)
        if vstatus == ValidationStatus.CONFLICTED:
            types.add(KnowledgeGapType.CONFLICTED)
        if vstatus == ValidationStatus.UNVERIFIED:
            types.add(KnowledgeGapType.UNVERIFIED)
        if self.review_status.get(kp) == ReviewStatus.PENDING:
            types.add(KnowledgeGapType.REVIEW_PENDING)
        return _gap_type_order(tuple(types))

    def covered_session_count(self, kp: str) -> int:
        return len(self.kp_session_ids.get(kp, frozenset()))

    def topic_count(self, kp: str) -> int:
        return len(self.kp_topic_ids.get(kp, frozenset()))


class KnowledgeCoverageAnalyzer:
    """Deterministic, read-only coverage and gap analysis.

    Construction accepts the Task 26 organization service and a single
    optional conflict resolver.  Every report is computed from one
    snapshot of the service at analysis time (first report call), so
    later mutations of the service never leak into already-returned
    reports and repeated calls stay idempotent.

    No LLM, embedding, semantic matching, translation, recommendation,
    student model, or database is introduced by this layer.
    """

    def __init__(
        self,
        service: KnowledgeOrganizationService,
        conflict_resolver: Optional[Callable] = None,
    ) -> None:
        """Create an analyzer.

        :param service: Task 26 organization service (read-only).
        :param conflict_resolver: optional ``fn(kp_id, evidence_refs) ->
            int`` counting unresolved conflicts that touch the KP's
            evidence refs.  Defaults to the deterministic state marker
            (1 when CONFLICTED, else 0) when no resolver is supplied.
        """
        if not isinstance(service, KnowledgeOrganizationService):
            raise CoverageValidationError(
                CoverageErrorCode.INVALID_INPUT,
                "service must be a KnowledgeOrganizationService",
            )
        if conflict_resolver is not None and not callable(conflict_resolver):
            raise CoverageValidationError(
                CoverageErrorCode.INVALID_INPUT,
                "conflict_resolver must be callable",
            )
        self._service = service
        self._conflict_resolver = conflict_resolver
        self._lock = threading.RLock()
        self._snapshot: Optional[_CourseSnapshot] = None

    # ------------------------------------------------------------------
    # snapshot access (lazy, built once)
    # ------------------------------------------------------------------

    @property
    def service(self) -> KnowledgeOrganizationService:
        return self._service

    def _snap(self) -> _CourseSnapshot:
        with self._lock:
            if self._snapshot is None:
                self._snapshot = _CourseSnapshot(self._service, self._conflict_resolver)
            return self._snapshot

    # ------------------------------------------------------------------
    # Course-level reports
    # ------------------------------------------------------------------

    def analyze_course(self) -> KnowledgeCoverageReport:
        """Course-level structural coverage and assignment stats."""
        snap = self._snap()
        total = len(snap.kp_ids)
        covered = sum(1 for kp in snap.kp_ids if snap.kp_session_ids.get(kp))
        assigned = sum(1 for kp in snap.kp_ids if snap.kp_topic_ids.get(kp))
        conflicted = sum(
            1 for kp in snap.kp_ids if snap.validation_status.get(kp) == ValidationStatus.CONFLICTED
        )
        unverified = sum(
            1 for kp in snap.kp_ids if snap.validation_status.get(kp) == ValidationStatus.UNVERIFIED
        )
        pending = sum(
            1 for kp in snap.kp_ids if snap.review_status.get(kp) == ReviewStatus.PENDING
        )
        return KnowledgeCoverageReport(
            course_id=snap.course_id,
            total_knowledge_points=total,
            covered_knowledge_points=covered,
            uncovered_knowledge_points=total - covered,
            assigned_knowledge_points=assigned,
            unassigned_knowledge_points=total - assigned,
            conflicted_knowledge_points=conflicted,
            unverified_knowledge_points=unverified,
            review_pending_knowledge_points=pending,
            coverage_ratio=_ratio(covered, total),
            assignment_ratio=_ratio(assigned, total),
        )

    def analyze_topic(self, topic_id: str) -> TopicCoverageReport:
        """Per-topic structural coverage among that topic's KPs."""
        snap = self._snap()
        if topic_id not in snap.topic_kp_ids and topic_id not in snap.topic_order_index:
            raise CoverageValidationError(
                CoverageErrorCode.MISSING_TOPIC,
                f"topic {topic_id!r} is not registered in course {snap.course_id}",
            )
        kp_ids = sorted(snap.topic_kp_ids.get(topic_id, frozenset()))
        total = len(kp_ids)
        covered = sum(1 for kp in kp_ids if snap.kp_session_ids.get(kp))
        conflicted = sum(
            1 for kp in kp_ids if snap.validation_status.get(kp) == ValidationStatus.CONFLICTED
        )
        unverified = sum(
            1 for kp in kp_ids if snap.validation_status.get(kp) == ValidationStatus.UNVERIFIED
        )
        pending = sum(
            1 for kp in kp_ids if snap.review_status.get(kp) == ReviewStatus.PENDING
        )
        return TopicCoverageReport(
            topic_id=topic_id,
            total_knowledge_points=total,
            covered_knowledge_points=covered,
            uncovered_knowledge_points=total - covered,
            conflicted_knowledge_points=conflicted,
            unverified_knowledge_points=unverified,
            review_pending_knowledge_points=pending,
            coverage_ratio=_ratio(covered, total),
        )

    def analyze_session(self, session_id: str) -> SessionCoverageReport:
        """Per-session stats: total KPs, new vs repeated."""
        snap = self._snap()
        if session_id not in snap.session_kp_ids:
            raise CoverageValidationError(
                CoverageErrorCode.MISSING_SESSION,
                f"session {session_id!r} is not registered in course {snap.course_id}",
            )
        point = self._session_point(snap, session_id)
        return SessionCoverageReport(
            session_id=session_id,
            knowledge_point_count=point.knowledge_point_count,
            unique_knowledge_point_count=point.unique_knowledge_point_count,
            new_knowledge_point_count=point.new_knowledge_point_count,
            repeated_knowledge_point_count=point.repeated_knowledge_point_count,
        )

    def analyze_coverage_timeline(self) -> CourseCoverageTimeline:
        """All sessions in deterministic order with new/repeated stats."""
        snap = self._snap()
        seen: Set[str] = set()
        points: Tuple[SessionCoveragePoint, ...] = []
        for session_id in snap.ordered_session_ids():
            kps = snap.session_kp_ids.get(session_id, frozenset())
            unique = len(kps)
            repeated = len(kps & seen)
            points.append(
                SessionCoveragePoint(
                    session_id=session_id,
                    knowledge_point_count=unique,
                    unique_knowledge_point_count=unique,
                    new_knowledge_point_count=unique - repeated,
                    repeated_knowledge_point_count=repeated,
                )
            )
            seen.update(kps)
        return CourseCoverageTimeline(course_id=snap.course_id, sessions=tuple(points))

    def _session_point(self, snap: _CourseSnapshot, session_id: str) -> SessionCoveragePoint:
        order = snap.session_order.get(session_id, _UNNUMBERED_SESSION_RANK)
        kps = snap.session_kp_ids.get(session_id, frozenset())
        earlier: Set[str] = set()
        for other, other_kps in snap.session_kp_ids.items():
            if other == session_id:
                continue
            if snap.session_order.get(other, _UNNUMBERED_SESSION_RANK) < order:
                earlier.update(other_kps)
        repeated = len(kps & earlier)
        unique = len(kps)
        return SessionCoveragePoint(
            session_id=session_id,
            knowledge_point_count=unique,
            unique_knowledge_point_count=unique,
            new_knowledge_point_count=unique - repeated,
            repeated_knowledge_point_count=repeated,
        )

    # ------------------------------------------------------------------
    # Gap analysis
    # ------------------------------------------------------------------

    def analyze_gaps(self) -> CourseGapReport:
        """Gaps for every registered KP that has at least one gap type."""
        snap = self._snap()
        gaps: List[KnowledgeGap] = []
        for kp in snap.kp_ids:
            types = snap.gaps_for(kp)
            if not types:
                continue
            gaps.append(
                KnowledgeGap(
                    knowledge_point_id=kp,
                    gap_types=types,
                    topic_ids=tuple(sorted(snap.kp_topic_ids.get(kp, ()))),
                    session_count=snap.covered_session_count(kp),
                    validation_status=snap.validation_status.get(kp, ValidationStatus.UNVERIFIED).value,
                    review_status=snap.review_status.get(kp, ReviewStatus.PENDING).value,
                )
            )
        return CourseGapReport(course_id=snap.course_id, gaps=tuple(gaps))

    def get_unassigned_knowledge_points(self) -> Tuple[str, ...]:
        snap = self._snap()
        return tuple(kp for kp in snap.kp_ids if not snap.kp_topic_ids.get(kp))

    def get_uncovered_knowledge_points(self) -> Tuple[str, ...]:
        snap = self._snap()
        return tuple(kp for kp in snap.kp_ids if not snap.kp_session_ids.get(kp))

    def get_conflicted_knowledge_points(self) -> Tuple[ConflictCoverageItem, ...]:
        snap = self._snap()
        return tuple(
            ConflictCoverageItem(
                knowledge_point_id=kp,
                conflict_count=snap.conflict_counts.get(kp, 0),
                review_status=snap.review_status.get(kp, ReviewStatus.PENDING).value,
            )
            for kp in snap.kp_ids
            if snap.validation_status.get(kp) == ValidationStatus.CONFLICTED
        )

    def get_unverified_knowledge_points(self) -> Tuple[str, ...]:
        snap = self._snap()
        return tuple(
            kp
            for kp in snap.kp_ids
            if snap.validation_status.get(kp) == ValidationStatus.UNVERIFIED
        )

    def get_review_pending_knowledge_points(self) -> Tuple[str, ...]:
        snap = self._snap()
        return tuple(
            kp
            for kp in snap.kp_ids
            if snap.review_status.get(kp) == ReviewStatus.PENDING
        )

    # ------------------------------------------------------------------
    # Frequency
    # ------------------------------------------------------------------

    def analyze_frequency(self) -> Tuple[KnowledgeFrequency, ...]:
        """Per-KP session/topic frequency, sorted by session_count desc,
        then kp_id asc."""
        snap = self._snap()
        out = [
            KnowledgeFrequency(
                knowledge_point_id=kp,
                session_count=snap.covered_session_count(kp),
                topic_count=snap.topic_count(kp),
            )
            for kp in snap.kp_ids
        ]
        out.sort(key=lambda f: (-f.session_count, f.knowledge_point_id))
        return tuple(out)

    def get_repeated_knowledge_points(self) -> Tuple[KnowledgeFrequency, ...]:
        """KPs seen in >= 2 distinct sessions, sorted by session_count
        desc then kp_id asc."""
        return tuple(f for f in self.analyze_frequency() if f.session_count >= 2)

    def get_single_occurrence_knowledge_points(self) -> Tuple[KnowledgeFrequency, ...]:
        """KPs seen in exactly one session, sorted by kp_id asc."""
        return tuple(f for f in self.analyze_frequency() if f.session_count == 1)

    def get_knowledge_evidence_coverage(self) -> Tuple[KnowledgeEvidenceCoverage, ...]:
        """Unique evidence ref counts per KP (counting only)."""
        snap = self._snap()
        return tuple(
            KnowledgeEvidenceCoverage(
                knowledge_point_id=kp,
                evidence_count=snap.evidence_counts.get(kp, 0),
            )
            for kp in snap.kp_ids
        )

    def get_orphan_report(self) -> KnowledgeOrphanReport:
        """Never-assigned and/or never-covered KP ids (both dimensions)."""
        snap = self._snap()
        unassigned = tuple(kp for kp in snap.kp_ids if not snap.kp_topic_ids.get(kp))
        uncovered = tuple(kp for kp in snap.kp_ids if not snap.kp_session_ids.get(kp))
        return KnowledgeOrphanReport(
            unassigned_knowledge_point_ids=unassigned,
            uncovered_knowledge_point_ids=uncovered,
        )

    def get_status_summary(self) -> CourseKnowledgeStatusSummary:
        """Ratio-level status overview (all data states, no inference)."""
        snap = self._snap()
        total = len(snap.kp_ids)
        covered = sum(1 for kp in snap.kp_ids if snap.kp_session_ids.get(kp))
        assigned = sum(1 for kp in snap.kp_ids if snap.kp_topic_ids.get(kp))
        conflicted = sum(
            1 for kp in snap.kp_ids if snap.validation_status.get(kp) == ValidationStatus.CONFLICTED
        )
        unverified = sum(
            1 for kp in snap.kp_ids if snap.validation_status.get(kp) == ValidationStatus.UNVERIFIED
        )
        pending = sum(
            1 for kp in snap.kp_ids if snap.review_status.get(kp) == ReviewStatus.PENDING
        )
        return CourseKnowledgeStatusSummary(
            course_id=snap.course_id,
            total_knowledge_points=total,
            coverage_ratio=_ratio(covered, total),
            assignment_ratio=_ratio(assigned, total),
            conflicted_ratio=_ratio(conflicted, total),
            unverified_ratio=_ratio(unverified, total),
            review_pending_ratio=_ratio(pending, total),
        )

    # ------------------------------------------------------------------
    # Validation / Review cross-tab
    # ------------------------------------------------------------------

    def analyze_validation_review(self) -> ValidationReviewMatrix:
        """Full ValidationStatus x ReviewStatus cross-tab.

        Every cell is an int; the sum of all cells equals the total
        registered KP count.  Pure counting - no inference.
        """
        snap = self._snap()
        matrix = ValidationReviewMatrix.build()
        for kp in snap.kp_ids:
            v = snap.validation_status.get(kp, ValidationStatus.UNVERIFIED).value
            r = snap.review_status.get(kp, ReviewStatus.PENDING).value
            if v not in matrix.counts:
                matrix.counts[v] = {rr: 0 for rr in ReviewStatus}
            if r not in matrix.counts[v]:
                matrix.counts[v][r] = 0
            matrix.counts[v][r] = int(matrix.counts[v].get(r, 0)) + 1
        return matrix

