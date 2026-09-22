"""Course-level knowledge organization for the Classroom Assistant project.

Task 26 - Knowledge Organization & Course-Level Knowledge Structure.

This module organizes EXISTING KnowledgePoints into a stable,
queryable, incrementally updatable course knowledge structure:

    Course
      +-- Topic (hierarchy, explicit, no cycles)
      +-- ClassSession -- (SessionKnowledgeMembership) --+
      |                                                   |
      +-- KnowledgeMembership (Topic -> KP) -------------+
                                                          v
                                                  KnowledgePoint
                                                          |
                                                   Evidence (read-only)

Core principles (deterministic, evidence-first, no hallucination):
- Only explicit inputs create Topics / memberships / relations.  No
  automatic topic classification, relation extraction, translation,
  LLM, embedding, or vector / graph database of any kind.
- All business IDs are deterministic SHA-256 canonical-JSON digests
  (no uuid4, no datetime.now, no random, no builtin hash()).
- Duplicate-safe and idempotent: re-adding identical entities never
  produces duplicates.
- Course isolation: a Topic and its memberships can never cross
  course boundaries; relations are course-scoped.
- Organization never mutates KnowledgePoint content: the layer stores
  reference IDs only.  ValidationStatus and ReviewStatus remain fully
  independent of organization, session coverage, and each other.
- Internal indexes keep add / remove / load consistent; queries are
  O(k) in the result size (no per-query full scans).
- Thread safety: every mutating operation takes an RLock, matching
  the EvidenceStore convention (Task 23); read-only queries return
  frozen copies.

Serialization: CourseKnowledgeStructure.to_dict() / from_dict() carry
schema_version = 1; an unknown version is rejected loudly.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from src.models import Course, ClassSession, KnowledgePoint
from src.knowledge_structure import KnowledgeStructure
from src.knowledge_validation import ValidationStatus
from src.knowledge_review import ReviewStatus, ReviewDecision

SCHEMA_VERSION = 1


def _canonical_json(payload: Any) -> str:
    """Deterministic canonical JSON: sorted keys, ASCII-preserving, compact."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha24(payload: Any) -> str:
    """Deterministic SHA-256 digest of the canonical JSON of *payload*."""
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()[:24]


def _order_key(value: Optional[int]) -> Tuple:
    """Deterministic sort key: None sorts last, then numeric."""
    if value is None:
        return (1, 0)
    return (0, value)


class OrganizationErrorCode(str, Enum):
    """Stable machine-readable error codes for organization errors."""

    INVALID_INPUT = "INVALID_INPUT"
    INVALID_SCHEMA_VERSION = "INVALID_SCHEMA_VERSION"
    COURSE_NOT_FOUND = "COURSE_NOT_FOUND"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    TOPIC_NOT_FOUND = "TOPIC_NOT_FOUND"
    KNOWLEDGE_POINT_NOT_FOUND = "KNOWLEDGE_POINT_NOT_FOUND"
    CROSS_COURSE_MEMBERSHIP = "CROSS_COURSE_MEMBERSHIP"
    INVALID_TOPIC_PARENT = "INVALID_TOPIC_PARENT"
    TOPIC_CYCLE = "TOPIC_CYCLE"
    SELF_RELATION = "SELF_RELATION"
    DANGLING_REFERENCE = "DANGLING_REFERENCE"
    DUPLICATE_ENTITY = "DUPLICATE_ENTITY"


class KnowledgeOrganizationError(Exception):
    """Base class for all course knowledge organization errors.

    Carries a stable ``code`` (OrganizationErrorCode) so callers can
    branch on the error kind without parsing messages.
    """

    def __init__(self, code: OrganizationErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class OrganizationValidationError(KnowledgeOrganizationError):
    """Raised when an input entity or reference fails validation."""


class OrganizationSchemaError(KnowledgeOrganizationError):
    """Raised when a serialized payload has an unknown schema version."""


class KnowledgeRelationType(str, Enum):
    """Explicit, caller-supplied relation kinds between KnowledgePoints.

    A relation ALWAYS represents the direction source -> target:
      - PREREQUISITE: source is a prerequisite of target.
      - RELATED: symmetric in meaning but stored as one directed edge;
        the reverse edge must be added explicitly if needed.
      - EXTENDS: source extends target.
      - CONTRASTS: source contrasts with target.
      - PART_OF: source is a part of target.

    No inference: relations are only created from explicit input.
    """

    PREREQUISITE = "prerequisite"
    RELATED = "related"
    EXTENDS = "extends"
    CONTRASTS = "contrasts"
    PART_OF = "part_of"

    @classmethod
    def from_string(cls, value: Union["KnowledgeRelationType", str]) -> "KnowledgeRelationType":
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT,
                f"relation type must be a string or KnowledgeRelationType, got {type(value).__name__}",
            )
        try:
            return cls(value.lower().strip())
        except ValueError:
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT,
                f"unknown knowledge relation type: {value!r}",
            ) from None


@dataclass(frozen=True)
class Topic:
    """A deterministic, course-scoped knowledge organization node.

    - topic_id is content-deterministic: same (course, parent, name)
      triple always yields the same id, so duplicate adds are no-ops.
    - A topic may have a parent topic; the parent must exist and belong
      to the same course; cycles are rejected.
    - order_index is caller-supplied; None sorts after every index.
    - name is stored verbatim (no translation / normalization).
    """

    topic_id: str
    course_id: str
    name: str
    description: Optional[str]
    parent_topic_id: Optional[str]
    order_index: Optional[int]

    @classmethod
    def create(
        cls,
        course_id: str,
        name: str,
        description: Optional[str] = None,
        parent_topic_id: Optional[str] = None,
        order_index: Optional[int] = None,
    ) -> "Topic":
        """Construct a Topic with a deterministic ID.

        Raises OrganizationValidationError (INVALID_INPUT) when
        course_id or name is empty.
        """
        if not isinstance(course_id, str) or not course_id:
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT, "course_id must be a non-empty string"
            )
        if not isinstance(name, str) or not name:
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT, "topic name must be a non-empty string"
            )
        if description is not None and not isinstance(description, str):
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT,
                "topic description must be a string or None",
            )
        if parent_topic_id is not None and not isinstance(parent_topic_id, str):
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT,
                "parent_topic_id must be a string or None",
            )
        if order_index is not None and not isinstance(order_index, int):
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT,
                "order_index must be an int or None",
            )
        payload = {
            "course_id": course_id,
            "parent_topic_id": parent_topic_id,
            "name": name,
        }
        topic_id = "topic-" + _sha24(payload)
        if parent_topic_id is not None and topic_id == parent_topic_id:
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_TOPIC_PARENT,
                "a topic cannot be its own parent",
            )
        return cls(
            topic_id=topic_id,
            course_id=course_id,
            name=name,
            description=description,
            parent_topic_id=parent_topic_id,
            order_index=order_index,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "course_id": self.course_id,
            "name": self.name,
            "description": self.description,
            "parent_topic_id": self.parent_topic_id,
            "order_index": self.order_index,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Topic":
        if not isinstance(data, Mapping):
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT, "topic payload must be a mapping"
            )
        topic_id = data.get("topic_id")
        course_id = data.get("course_id")
        if not topic_id:
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT, "topic_id is required"
            )
        if not course_id:
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT, "course_id is required"
            )
        name = str(data.get("name") or "")
        if not name:
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT, "topic name must be non-empty"
            )
        return cls(
            topic_id=str(topic_id),
            course_id=str(course_id),
            name=name,
            description=data.get("description"),
            parent_topic_id=data.get("parent_topic_id"),
            order_index=data.get("order_index"),
        )


@dataclass(frozen=True)
class KnowledgeMembership:
    """A deterministic link Topic -> KnowledgePoint.

    The KnowledgePoint itself is NOT copied here; only its id is
    stored, so one KP may belong to many topics and each topic may
    hold many KPs.  order_index participates only in ordering, never
    in the identity (re-ordering never recreates the membership).
    """

    membership_id: str
    topic_id: str
    knowledge_point_id: str
    order_index: Optional[int] = None

    @classmethod
    def create(
        cls,
        topic_id: str,
        knowledge_point_id: str,
        order_index: Optional[int] = None,
    ) -> "KnowledgeMembership":
        if not topic_id or not knowledge_point_id:
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT,
                "topic_id and knowledge_point_id must be non-empty",
            )
        if order_index is not None and not isinstance(order_index, int):
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT,
                "order_index must be an int or None",
            )
        payload = {
            "topic_id": topic_id,
            "knowledge_point_id": knowledge_point_id,
        }
        membership_id = "kmem-" + _sha24(payload)
        return cls(
            membership_id=membership_id,
            topic_id=topic_id,
            knowledge_point_id=knowledge_point_id,
            order_index=order_index,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "membership_id": self.membership_id,
            "topic_id": self.topic_id,
            "knowledge_point_id": self.knowledge_point_id,
            "order_index": self.order_index,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "KnowledgeMembership":
        if not isinstance(data, Mapping):
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT, "membership payload must be a mapping"
            )
        return cls(
            membership_id=str(data.get("membership_id") or ""),
            topic_id=str(data.get("topic_id") or ""),
            knowledge_point_id=str(data.get("knowledge_point_id") or ""),
            order_index=data.get("order_index"),
        )


@dataclass(frozen=True)
class SessionKnowledgeMembership:
    """A deterministic link ClassSession -> KnowledgePoint.

    Means "this knowledge point was actually involved / extracted in
    this class session".  order_index never participates in identity.
    """

    membership_id: str
    session_id: str
    knowledge_point_id: str
    order_index: Optional[int] = None

    @classmethod
    def create(
        cls,
        session_id: str,
        knowledge_point_id: str,
        order_index: Optional[int] = None,
    ) -> "SessionKnowledgeMembership":
        if not session_id or not knowledge_point_id:
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT,
                "session_id and knowledge_point_id must be non-empty",
            )
        if order_index is not None and not isinstance(order_index, int):
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT,
                "order_index must be an int or None",
            )
        payload = {
            "session_id": session_id,
            "knowledge_point_id": knowledge_point_id,
        }
        membership_id = "smem-" + _sha24(payload)
        return cls(
            membership_id=membership_id,
            session_id=session_id,
            knowledge_point_id=knowledge_point_id,
            order_index=order_index,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "membership_id": self.membership_id,
            "session_id": self.session_id,
            "knowledge_point_id": self.knowledge_point_id,
            "order_index": self.order_index,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SessionKnowledgeMembership":
        if not isinstance(data, Mapping):
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT, "membership payload must be a mapping"
            )
        return cls(
            membership_id=str(data.get("membership_id") or ""),
            session_id=str(data.get("session_id") or ""),
            knowledge_point_id=str(data.get("knowledge_point_id") or ""),
            order_index=data.get("order_index"),
        )


@dataclass(frozen=True)
class KnowledgeRelation:
    """An explicit, directed relation between two KnowledgePoints.

    The id is deterministic over (course, source, target, type):
    - re-adding the same relation is a no-op (duplicate-safe);
    - (A -> B prerequisite) and (B -> A prerequisite) are two distinct
      relations.
    No transitive closure or inferred relations exist in this layer.
    """

    relation_id: str
    course_id: str
    source_knowledge_point_id: str
    target_knowledge_point_id: str
    relation_type: KnowledgeRelationType

    @classmethod
    def create(
        cls,
        course_id: str,
        source: str,
        target: str,
        relation_type: Union[KnowledgeRelationType, str],
    ) -> "KnowledgeRelation":
        if not course_id or not source or not target:
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT,
                "course_id, source and target must be non-empty",
            )
        if source == target:
            raise OrganizationValidationError(
                OrganizationErrorCode.SELF_RELATION,
                "a knowledge point cannot relate to itself",
            )
        rtype = KnowledgeRelationType.from_string(relation_type)
        payload = {
            "course_id": course_id,
            "source_knowledge_point_id": source,
            "target_knowledge_point_id": target,
            "relation_type": rtype.value,
        }
        relation_id = "krel-" + _sha24(payload)
        return cls(
            relation_id=relation_id,
            course_id=course_id,
            source_knowledge_point_id=source,
            target_knowledge_point_id=target,
            relation_type=rtype,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "course_id": self.course_id,
            "source_knowledge_point_id": self.source_knowledge_point_id,
            "target_knowledge_point_id": self.target_knowledge_point_id,
            "relation_type": self.relation_type.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "KnowledgeRelation":
        if not isinstance(data, Mapping):
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT, "relation payload must be a mapping"
            )
        return cls(
            relation_id=str(data.get("relation_id") or ""),
            course_id=str(data.get("course_id") or ""),
            source_knowledge_point_id=str(data.get("source_knowledge_point_id") or ""),
            target_knowledge_point_id=str(data.get("target_knowledge_point_id") or ""),
            relation_type=KnowledgeRelationType.from_string(
                data.get("relation_type") or KnowledgeRelationType.RELATED
            ),
        )



@dataclass(frozen=True)
class CoverageReport:
    """Deterministic course-level coverage statistics.

    "Covered" is strictly a SESSION concept: a KnowledgePoint is
    covered iff it owns at least one SessionKnowledgeMembership in
    THIS course.  A covered point is not a supported / confirmed
    point: coverage says nothing about validation or human review.
    """

    course_id: str
    topic_count: int
    knowledge_point_count: int
    covered_knowledge_point_count: int
    uncovered_knowledge_point_count: int
    session_count: int
    coverage_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "course_id": self.course_id,
            "topic_count": self.topic_count,
            "knowledge_point_count": self.knowledge_point_count,
            "covered_knowledge_point_count": self.covered_knowledge_point_count,
            "uncovered_knowledge_point_count": self.uncovered_knowledge_point_count,
            "session_count": self.session_count,
            "coverage_ratio": self.coverage_ratio,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CoverageReport":
        return cls(
            course_id=str(data.get("course_id") or ""),
            topic_count=int(data.get("topic_count") or 0),
            knowledge_point_count=int(data.get("knowledge_point_count") or 0),
            covered_knowledge_point_count=int(data.get("covered_knowledge_point_count") or 0),
            uncovered_knowledge_point_count=int(data.get("uncovered_knowledge_point_count") or 0),
            session_count=int(data.get("session_count") or 0),
            coverage_ratio=float(data.get("coverage_ratio") or 0.0),
        )


@dataclass(frozen=True)
class TopicCoverageReport:
    """Per-topic coverage: how many of the topic's KPs are covered."""

    topic_id: str
    knowledge_point_count: int
    covered_count: int
    uncovered_count: int
    coverage_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "knowledge_point_count": self.knowledge_point_count,
            "covered_count": self.covered_count,
            "uncovered_count": self.uncovered_count,
            "coverage_ratio": self.coverage_ratio,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TopicCoverageReport":
        return cls(
            topic_id=str(data.get("topic_id") or ""),
            knowledge_point_count=int(data.get("knowledge_point_count") or 0),
            covered_count=int(data.get("covered_count") or 0),
            uncovered_count=int(data.get("uncovered_count") or 0),
            coverage_ratio=float(data.get("coverage_ratio") or 0.0),
        )


@dataclass(frozen=True)
class SessionCoverageReport:
    """Per-session statistics against the course knowledge base."""

    session_id: str
    knowledge_point_count: int
    already_known_count: int
    newly_introduced_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "knowledge_point_count": self.knowledge_point_count,
            "already_known_count": self.already_known_count,
            "newly_introduced_count": self.newly_introduced_count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SessionCoverageReport":
        return cls(
            session_id=str(data.get("session_id") or ""),
            knowledge_point_count=int(data.get("knowledge_point_count") or 0),
            already_known_count=int(data.get("already_known_count") or 0),
            newly_introduced_count=int(data.get("newly_introduced_count") or 0),
        )


@dataclass(frozen=True)
class ValidationSummary:
    """Aggregate of KnowledgePoint.validation_status within a course.

    Pure computation, read-only: producing this summary never mutates
    any KnowledgePoint.  The average score is computed over existing
    KPs only (0.0 when there are none) and kept at full float
    precision - no rounding policy is imposed.
    """

    course_id: str
    unverified_count: int
    supported_count: int
    conflicted_count: int
    average_knowledge_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "course_id": self.course_id,
            "unverified_count": self.unverified_count,
            "supported_count": self.supported_count,
            "conflicted_count": self.conflicted_count,
            "average_knowledge_score": self.average_knowledge_score,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ValidationSummary":
        return cls(
            course_id=str(data.get("course_id") or ""),
            unverified_count=int(data.get("unverified_count") or 0),
            supported_count=int(data.get("supported_count") or 0),
            conflicted_count=int(data.get("conflicted_count") or 0),
            average_knowledge_score=float(data.get("average_knowledge_score") or 0.0),
        )


@dataclass(frozen=True)
class ReviewSummary:
    """Aggregate of the LATEST effective ReviewStatus per KP in a course.

    "Latest effective" follows Task 15 review history: the decision of
    the last record in stable review_id order wins; with no records
    the point stays PENDING.  Aggregation is read-only.
    """

    course_id: str
    pending_count: int
    confirmed_count: int
    rejected_count: int
    kept_unverified_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "course_id": self.course_id,
            "pending_count": self.pending_count,
            "confirmed_count": self.confirmed_count,
            "rejected_count": self.rejected_count,
            "kept_unverified_count": self.kept_unverified_count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReviewSummary":
        return cls(
            course_id=str(data.get("course_id") or ""),
            pending_count=int(data.get("pending_count") or 0),
            confirmed_count=int(data.get("confirmed_count") or 0),
            rejected_count=int(data.get("rejected_count") or 0),
            kept_unverified_count=int(data.get("kept_unverified_count") or 0),
        )


@dataclass(frozen=True)
class CourseKnowledgeSummary:
    """Complete deterministic course summary (pure computation result).

    Computed on demand from the current structure; never cached, so
    it can never drift out of date.
    """

    course_id: str
    topic_count: int
    knowledge_point_count: int
    session_count: int
    relation_count: int
    validation_summary: ValidationSummary
    review_summary: ReviewSummary
    coverage: CoverageReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "course_id": self.course_id,
            "topic_count": self.topic_count,
            "knowledge_point_count": self.knowledge_point_count,
            "session_count": self.session_count,
            "relation_count": self.relation_count,
            "validation_summary": self.validation_summary.to_dict(),
            "review_summary": self.review_summary.to_dict(),
            "coverage": self.coverage.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CourseKnowledgeSummary":
        return cls(
            course_id=str(data.get("course_id") or ""),
            topic_count=int(data.get("topic_count") or 0),
            knowledge_point_count=int(data.get("knowledge_point_count") or 0),
            session_count=int(data.get("session_count") or 0),
            relation_count=int(data.get("relation_count") or 0),
            validation_summary=ValidationSummary.from_dict(data.get("validation_summary") or {}),
            review_summary=ReviewSummary.from_dict(data.get("review_summary") or {}),
            coverage=CoverageReport.from_dict(data.get("coverage") or {}),
        )


@dataclass(frozen=True)
class TopicNode:
    """One node of the deterministic topic tree."""

    topic: Topic
    children: Tuple["TopicNode", ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic.to_dict(),
            "children": [child.to_dict() for child in self.children],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TopicNode":
        if not isinstance(data, Mapping):
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT, "topic node payload must be a mapping"
            )
        return cls(
            topic=Topic.from_dict(data.get("topic") or {}),
            children=tuple(TopicNode.from_dict(child) for child in data.get("children") or []),
        )


@dataclass(frozen=True)
class RelationGraph:
    """Deterministic node / edge representation of course relations."""

    course_id: str
    nodes: Tuple[str, ...] = ()
    edges: Tuple[Tuple[str, str, str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "course_id": self.course_id,
            "nodes": list(self.nodes),
            "edges": [
                {"relation_id": e[0], "source": e[1], "target": e[2], "relation_type": e[3]}
                for e in self.edges
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RelationGraph":
        if not isinstance(data, Mapping):
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT, "relation graph payload must be a mapping"
            )
        return cls(
            course_id=str(data.get("course_id") or ""),
            nodes=tuple(str(n) for n in data.get("nodes") or []),
            edges=tuple(
                (
                    str(e.get("relation_id") or ""),
                    str(e.get("source") or ""),
                    str(e.get("target") or ""),
                    str(e.get("relation_type") or ""),
                )
                for e in data.get("edges") or []
            ),
        )


@dataclass
class CourseKnowledgeStructure:
    """Per-course canonical store of topics, memberships and relations.

    One instance per course.  All mutation happens through
    KnowledgeOrganizationService so the internal indexes can never go
    stale.  KnowledgePoints themselves live in a KnowledgeStructure;
    this container holds only their ids plus the organization layer.

    Serialized representation is a plain dict with schema_version = 1:
    {
      "schema_version": 1,
      "course_id": "...",
      "topics": [ ... ],
      "knowledge_memberships": [ ... ],
      "session_memberships": [ ... ],
      "relations": [ ... ]
    }
    """

    course_id: str
    topics: Dict[str, Topic] = field(default_factory=dict)
    knowledge_memberships: Dict[str, KnowledgeMembership] = field(default_factory=dict)
    session_memberships: Dict[str, SessionKnowledgeMembership] = field(default_factory=dict)
    relations: Dict[str, KnowledgeRelation] = field(default_factory=dict)
    _topic_kp_index: Dict[str, set] = field(default_factory=dict, repr=False)
    _kp_topic_index: Dict[str, set] = field(default_factory=dict, repr=False)
    _session_kp_index: Dict[str, set] = field(default_factory=dict, repr=False)
    _kp_session_index: Dict[str, set] = field(default_factory=dict, repr=False)
    _kp_outgoing: Dict[str, set] = field(default_factory=dict, repr=False)
    _kp_incoming: Dict[str, set] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.course_id, str) or not self.course_id:
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT, "course_id must be a non-empty string"
            )
        self._rebuild_indexes()

    def _rebuild_indexes(self) -> None:
        self._topic_kp_index = {}
        self._kp_topic_index = {}
        for m in self.knowledge_memberships.values():
            self._topic_kp_index.setdefault(m.topic_id, set()).add(m.knowledge_point_id)
            self._kp_topic_index.setdefault(m.knowledge_point_id, set()).add(m.topic_id)
        self._session_kp_index = {}
        self._kp_session_index = {}
        for m in self.session_memberships.values():
            self._session_kp_index.setdefault(m.session_id, set()).add(m.knowledge_point_id)
            self._kp_session_index.setdefault(m.knowledge_point_id, set()).add(m.session_id)
        self._kp_outgoing = {}
        self._kp_incoming = {}
        for r in self.relations.values():
            self._kp_outgoing.setdefault(r.source_knowledge_point_id, set()).add(r.relation_id)
            self._kp_incoming.setdefault(r.target_knowledge_point_id, set()).add(r.relation_id)

    def to_dict(self) -> Dict[str, Any]:
        """Deterministic snapshot: lists sorted by their ids."""
        return {
            "schema_version": SCHEMA_VERSION,
            "course_id": self.course_id,
            "topics": [t.to_dict() for t in sorted(self.topics.values(), key=lambda t: t.topic_id)],
            "knowledge_memberships": [
                m.to_dict()
                for m in sorted(self.knowledge_memberships.values(), key=lambda m: m.membership_id)
            ],
            "session_memberships": [
                m.to_dict()
                for m in sorted(self.session_memberships.values(), key=lambda m: m.membership_id)
            ],
            "relations": [
                r.to_dict()
                for r in sorted(self.relations.values(), key=lambda r: r.relation_id)
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CourseKnowledgeStructure":
        """Rebuild a structure from a to_dict() payload.

        Raises OrganizationSchemaError on unknown schema versions and
        OrganizationValidationError on any dangling reference,
        duplicate entity, or self relation.  The canonical id of every
        membership / relation is recomputed and cross-checked so
        corrupted files are rejected rather than silently patched.
        """
        if not isinstance(data, Mapping):
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT, "structure payload must be a mapping"
            )
        schema_version = data.get("schema_version")
        if schema_version != SCHEMA_VERSION:
            raise OrganizationSchemaError(
                OrganizationErrorCode.INVALID_SCHEMA_VERSION,
                f"unsupported schema_version: {schema_version!r} (expected {SCHEMA_VERSION})",
            )
        course_id = str(data.get("course_id") or "")
        if not course_id:
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT, "course_id is missing"
            )
        structure = cls(course_id=course_id)
        seen_topics: set = set()
        for topic_data in data.get("topics") or []:
            topic = Topic.from_dict(topic_data)
            if topic.course_id != course_id:
                raise OrganizationValidationError(
                    OrganizationErrorCode.CROSS_COURSE_MEMBERSHIP,
                    f"topic {topic.topic_id} belongs to course {topic.course_id}, not {course_id}",
                )
            if topic.topic_id in seen_topics:
                raise OrganizationValidationError(
                    OrganizationErrorCode.DUPLICATE_ENTITY,
                    f"duplicate topic {topic.topic_id}",
                )
            seen_topics.add(topic.topic_id)
            structure.topics[topic.topic_id] = topic
        # parent references + cycle detection over the loaded hierarchy
        for topic in structure.topics.values():
            if topic.parent_topic_id is not None:
                if topic.parent_topic_id not in structure.topics:
                    raise OrganizationValidationError(
                        OrganizationErrorCode.TOPIC_NOT_FOUND,
                        f"topic {topic.topic_id} references unknown parent "
                        f"{topic.parent_topic_id}",
                    )
        for topic_id, topic in structure.topics.items():
            seen: set = set()
            current = topic_id
            while current is not None:
                if current in seen:
                    raise OrganizationValidationError(
                        OrganizationErrorCode.TOPIC_CYCLE,
                        f"topic cycle detected at {current}",
                    )
                seen.add(current)
                parent = structure.topics.get(current)
                current = parent.parent_topic_id if parent is not None else None
        seen_kmem: set = set()
        for m_data in data.get("knowledge_memberships") or []:
            membership = KnowledgeMembership.create(
                topic_id=m_data["topic_id"],
                knowledge_point_id=m_data["knowledge_point_id"],
                order_index=m_data.get("order_index"),
            )
            expected_id = m_data.get("membership_id")
            if expected_id and expected_id != membership.membership_id:
                raise OrganizationValidationError(
                    OrganizationErrorCode.DANGLING_REFERENCE,
                    f"membership id mismatch for {expected_id}",
                )
            if membership.topic_id not in structure.topics:
                raise OrganizationValidationError(
                    OrganizationErrorCode.DANGLING_REFERENCE,
                    f"membership references unknown topic {membership.topic_id}",
                )
            if membership.membership_id in seen_kmem:
                raise OrganizationValidationError(
                    OrganizationErrorCode.DUPLICATE_ENTITY,
                    f"duplicate knowledge membership {membership.membership_id}",
                )
            seen_kmem.add(membership.membership_id)
            structure.knowledge_memberships[membership.membership_id] = membership
        seen_smem: set = set()
        for s_data in data.get("session_memberships") or []:
            membership = SessionKnowledgeMembership.create(
                session_id=s_data["session_id"],
                knowledge_point_id=s_data["knowledge_point_id"],
                order_index=s_data.get("order_index"),
            )
            expected_id = s_data.get("membership_id")
            if expected_id and expected_id != membership.membership_id:
                raise OrganizationValidationError(
                    OrganizationErrorCode.DANGLING_REFERENCE,
                    f"session membership id mismatch for {expected_id}",
                )
            if membership.membership_id in seen_smem:
                raise OrganizationValidationError(
                    OrganizationErrorCode.DUPLICATE_ENTITY,
                    f"duplicate session membership {membership.membership_id}",
                )
            seen_smem.add(membership.membership_id)
            structure.session_memberships[membership.membership_id] = membership
        seen_rel: set = set()
        for r_data in data.get("relations") or []:
            source = str(r_data.get("source_knowledge_point_id") or "")
            target = str(r_data.get("target_knowledge_point_id") or "")
            rtype = KnowledgeRelationType.from_string(
                r_data.get("relation_type") or KnowledgeRelationType.RELATED
            )
            if source == target:
                raise OrganizationValidationError(
                    OrganizationErrorCode.SELF_RELATION,
                    f"self relation {source} is not allowed",
                )
            relation = KnowledgeRelation.create(course_id, source, target, rtype)
            expected_id = r_data.get("relation_id")
            if expected_id and expected_id != relation.relation_id:
                raise OrganizationValidationError(
                    OrganizationErrorCode.DANGLING_REFERENCE,
                    f"relation id mismatch {expected_id}",
                )
            if relation.course_id != course_id:
                raise OrganizationValidationError(
                    OrganizationErrorCode.CROSS_COURSE_MEMBERSHIP,
                    f"relation {relation.relation_id} belongs to another course",
                )
            if relation.relation_id in seen_rel:
                raise OrganizationValidationError(
                    OrganizationErrorCode.DUPLICATE_ENTITY,
                    f"duplicate relation {relation.relation_id}",
                )
            seen_rel.add(relation.relation_id)
            structure.relations[relation.relation_id] = relation
        structure._rebuild_indexes()
        return structure

class KnowledgeOrganizationService:
    """Deterministic, incremental, idempotent course knowledge organization.

    The service is constructed against exactly one course.  It accepts
    Course / ClassSession / KnowledgePoint / KnowledgeStructure objects
    read-only, and never copies or mutates any of them.
    """

    def __init__(
        self,
        course: Course,
        structure: Optional[CourseKnowledgeStructure] = None,
    ) -> None:
        if not isinstance(course, Course):
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT,
                "course must be a Course instance",
            )
        if not course.course_id:
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT,
                "course.course_id must be non-empty",
            )
        self._course = course
        self._structure = structure if structure is not None else CourseKnowledgeStructure(course.course_id)
        self._lock = threading.RLock()
        self._kp_by_id: Dict[str, KnowledgePoint] = {}
        self._sessions_by_id: Dict[str, ClassSession] = {}
        self._session_order: Dict[str, int] = {}
        self._structure_by_kp: Dict[str, KnowledgeStructure] = {}
        # Task 47.4: "已注册 ID" 是 UI / 分析热路径里被反复读取的属性
        # (例如逐条判断某知识点是否已注册)。原先每次访问都重建 frozenset,
        # 单次 O(N) —— 在循环里就退化成 O(N^2)。这里做失效式缓存:
        # 只在唯一的写入点 (register_*) 置空, 保证读到的永远是最新快照。
        self._kp_ids_cache: Optional[frozenset] = None
        self._session_ids_cache: Optional[frozenset] = None

    @property
    def course(self) -> Course:
        return self._course

    @property
    def structure(self) -> CourseKnowledgeStructure:
        return self._structure

    @property
    def course_id(self) -> str:
        return self._course.course_id

    @property
    def registered_knowledge_point_ids(self) -> frozenset[str]:
        # 缓存只读快照; register_knowledge_point / register_knowledge_structure
        # 写入时置空, 因此不会出现过期结果。
        if self._kp_ids_cache is None:
            self._kp_ids_cache = frozenset(self._kp_by_id)
        return self._kp_ids_cache

    @property
    def registered_session_ids(self) -> frozenset[str]:
        if self._session_ids_cache is None:
            self._session_ids_cache = frozenset(self._sessions_by_id)
        return self._session_ids_cache

    # ------------------------------------------------------------------
    # Registration (idempotent, read-only)
    # ------------------------------------------------------------------

    def register_knowledge_structure(self, knowledge_structure: KnowledgeStructure) -> None:
        """Register a KnowledgeStructure's KPs as the reference points.

        Idempotent: re-registration never duplicates anything.
        Read-only: the structure itself is never mutated.
        """
        if not isinstance(knowledge_structure, KnowledgeStructure):
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT,
                "knowledge_structure must be a KnowledgeStructure",
            )
        with self._lock:
            self._kp_ids_cache = None
            for kp in knowledge_structure.knowledge_points.values():
                if not kp.knowledge_id:
                    raise OrganizationValidationError(
                        OrganizationErrorCode.INVALID_INPUT,
                        "knowledge structure contains an empty knowledge_id",
                    )
                self._kp_by_id[kp.knowledge_id] = kp
                self._structure_by_kp.setdefault(kp.knowledge_id, knowledge_structure)

    def register_knowledge_point(self, knowledge_point: KnowledgePoint) -> None:
        """Register a single KP as a reference point (idempotent)."""
        if not isinstance(knowledge_point, KnowledgePoint):
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT,
                "knowledge_point must be a KnowledgePoint",
            )
        if not knowledge_point.knowledge_id:
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT,
                "knowledge_point has an empty id",
            )
        with self._lock:
            self._kp_ids_cache = None
            self._kp_by_id[knowledge_point.knowledge_id] = knowledge_point

    def register_session(self, session: ClassSession) -> None:
        """Register a ClassSession for course-isolated session coverage.

        The session must belong to this service's course (otherwise
        CROSS_COURSE_MEMBERSHIP).  Registration is idempotent.
        """
        if not isinstance(session, ClassSession):
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT,
                "session must be a ClassSession",
            )
        if not session.session_id:
            raise OrganizationValidationError(
                OrganizationErrorCode.INVALID_INPUT,
                "session.session_id must be non-empty",
            )
        if getattr(session, "course_id", None) and session.course_id != self._course.course_id:
            raise OrganizationValidationError(
                OrganizationErrorCode.CROSS_COURSE_MEMBERSHIP,
                f"session {session.session_id} belongs to course {session.course_id}, not {self._course.course_id}",
            )
        with self._lock:
            self._session_ids_cache = None
            self._sessions_by_id[session.session_id] = session
            number = getattr(session, "session_number", 0) or 0
            self._session_order[session.session_id] = number if number > 0 else 10 ** 9

    # ------------------------------------------------------------------
    # Topics
    # ------------------------------------------------------------------

    def add_topic(
        self,
        name: str,
        *,
        description: Optional[str] = None,
        parent_topic_id: Optional[str] = None,
        order_index: Optional[int] = None,
    ) -> Topic:
        """Create (idempotently) a topic in this course and return it.

        - Duplicate (same course, parent, name) returns the existing
          topic; when description / order_index differ they are
          refreshed (content-defined, deterministic).
        - Parent must exist in the same course; a topic cannot be its
          own parent; cycles are rejected with TOPIC_CYCLE.
        """
        if parent_topic_id is not None and not parent_topic_id:
            parent_topic_id = None
        with self._lock:
            candidate = Topic.create(
                self._course.course_id,
                name,
                description=description,
                parent_topic_id=parent_topic_id,
                order_index=order_index,
            )
            if parent_topic_id is not None:
                parent = self._structure.topics.get(parent_topic_id)
                if parent is None:
                    raise OrganizationValidationError(
                        OrganizationErrorCode.TOPIC_NOT_FOUND,
                        f"parent topic {parent_topic_id} does not exist in course {self._course.course_id}",
                    )
                if parent.course_id != self._course.course_id:
                    raise OrganizationValidationError(
                        OrganizationErrorCode.CROSS_COURSE_MEMBERSHIP,
                        f"parent topic {parent_topic_id} belongs to course {parent.course_id}, not {self._course.course_id}",
                    )
                if parent_topic_id == candidate.topic_id:
                    raise OrganizationValidationError(
                        OrganizationErrorCode.INVALID_TOPIC_PARENT,
                        "a topic cannot be its own parent",
                    )
                if self._would_create_cycle(candidate.topic_id, parent_topic_id):
                    raise OrganizationValidationError(
                        OrganizationErrorCode.TOPIC_CYCLE,
                        f"setting {parent_topic_id} as parent of {candidate.name!r} would create a topic cycle",
                    )
            existing = self._structure.topics.get(candidate.topic_id)
            if existing is not None:
                if existing.course_id != candidate.course_id:
                    raise OrganizationValidationError(
                        OrganizationErrorCode.CROSS_COURSE_MEMBERSHIP,
                        f"topic {candidate.topic_id} belongs to course {existing.course_id}, not {candidate.course_id}",
                    )
                if existing.description == candidate.description and existing.order_index == candidate.order_index:
                    return existing
                refreshed = Topic(
                    topic_id=existing.topic_id,
                    course_id=existing.course_id,
                    name=existing.name,
                    description=candidate.description,
                    parent_topic_id=existing.parent_topic_id,
                    order_index=candidate.order_index,
                )
                self._structure.topics[existing.topic_id] = refreshed
                self._structure._rebuild_indexes()
                return refreshed
            self._structure.topics[candidate.topic_id] = candidate
            self._structure._rebuild_indexes()
            return candidate

    def _would_create_cycle(self, topic_id: str, parent_topic_id: str) -> bool:
        """Walk the parent chain from parent; a cycle means we hit topic_id."""
        current = parent_topic_id
        while current is not None:
            if current == topic_id:
                return True
            topic = self._structure.topics.get(current)
            current = topic.parent_topic_id if topic is not None else None
        return False

    def remove_topic(self, topic_id: str) -> None:
        """Remove a topic and its knowledge memberships.

        - Session memberships and relations are PRESERVED (their KPs
          remain registered and traceable).
        - Removal is refused while the topic still has children
          (INVALID_INPUT); remove children first.
        - Unknown topic -> TOPIC_NOT_FOUND.
        """
        with self._lock:
            topic = self._structure.topics.get(topic_id)
            if topic is None:
                raise OrganizationValidationError(
                    OrganizationErrorCode.TOPIC_NOT_FOUND,
                    f"topic {topic_id} does not exist in course {self._course.course_id}",
                )
            children = sorted(
                t.topic_id
                for t in self._structure.topics.values()
                if t.parent_topic_id == topic_id
            )
            if children:
                raise OrganizationValidationError(
                    OrganizationErrorCode.INVALID_INPUT,
                    f"cannot remove topic {topic_id}: it still has children {children}",
                )
            del self._structure.topics[topic_id]
            self._structure.knowledge_memberships = {
                m.membership_id: m
                for m in self._structure.knowledge_memberships.values()
                if m.topic_id != topic_id
            }
            self._structure._rebuild_indexes()

    def get_topic(self, topic_id: str) -> Topic:
        """Return the topic; TOPIC_NOT_FOUND when missing."""
        with self._lock:
            return self._isolated_topic(topic_id)

    def list_topics(
        self,
        course_id: Optional[str] = None,
        parent_topic_id: Optional[str] = None,
    ) -> Tuple[Topic, ...]:
        """Deterministic topic listing, sorted by (order_index, topic_id).

        - course_id, when given, must equal this service's course.
        - parent_topic_id=None lists ALL topics of the course;
          a non-None value lists only that parent's direct children.
        """
        if course_id is not None and course_id != self._course.course_id:
            raise OrganizationValidationError(
                OrganizationErrorCode.CROSS_COURSE_MEMBERSHIP,
                f"course {course_id} is not this service's course {self._course.course_id}",
            )
        with self._lock:
            topics = [
                t
                for t in self._structure.topics.values()
                if t.course_id == self._course.course_id
                and (parent_topic_id is None or t.parent_topic_id == parent_topic_id)
            ]
            topics.sort(key=lambda t: (_order_key(t.order_index), t.topic_id))
            return tuple(topics)

    def _isolated_topic(self, topic_id: str) -> Topic:
        """Fetch a topic, enforcing that it belongs to THIS course.

        Topics are content-addressed, so a foreign-course topic can be
        force-injected into the shared structure dict; every public
        query path must filter by course before trusting a reference.
        """
        topic = self._structure.topics.get(topic_id)
        if topic is None:
            raise OrganizationValidationError(
                OrganizationErrorCode.TOPIC_NOT_FOUND,
                f"topic {topic_id} does not exist in course {self._course.course_id}",
            )
        if topic.course_id != self._course.course_id:
            raise OrganizationValidationError(
                OrganizationErrorCode.CROSS_COURSE_MEMBERSHIP,
                f"topic {topic_id} belongs to course {topic.course_id}, not {self._course.course_id}",
            )
        return topic

    def get_topic_tree(self) -> Tuple[TopicNode, ...]:
        """Deterministic topic tree: roots = topics with no parent.

        Children of every node are sorted by (order_index, topic_id).
        Cycles cannot exist (add_topic rejects them).
        """
        with self._lock:
            children: Dict[str, list] = {}
            roots: list = []
            for t in self._structure.topics.values():
                if t.course_id != self._course.course_id:
                    continue
                if t.parent_topic_id is None:
                    roots.append(t)
                else:
                    children.setdefault(t.parent_topic_id, []).append(t)
            for nodes in children.values():
                nodes.sort(key=lambda t: (_order_key(t.order_index), t.topic_id))
            roots.sort(key=lambda t: (_order_key(t.order_index), t.topic_id))

            def build(topic: Topic, path: frozenset) -> TopicNode:
                if topic.topic_id in path:
                    raise OrganizationValidationError(
                        OrganizationErrorCode.TOPIC_CYCLE,
                        f"topic cycle detected at {topic.topic_id}",
                    )
                next_path = path | {topic.topic_id}
                kids = tuple(build(c, next_path) for c in children.get(topic.topic_id, ()))
                return TopicNode(topic=topic, children=kids)

            return tuple(build(root, frozenset()) for root in roots)

    # ------------------------------------------------------------------
    # Topic <-> KnowledgePoint membership
    # ------------------------------------------------------------------

    def add_knowledge_to_topic(
        self,
        topic_id: str,
        knowledge_point_id: str,
        order_index: Optional[int] = None,
    ) -> KnowledgeMembership:
        """Link a registered KP to a topic (idempotent, deterministic).

        Validates: topic exists in this course, KP is registered.
        A re-add with a different order_index refreshes the order
        without creating a second membership.
        """
        with self._lock:
            self._isolated_topic(topic_id)
            if knowledge_point_id not in self._kp_by_id:
                raise OrganizationValidationError(
                    OrganizationErrorCode.KNOWLEDGE_POINT_NOT_FOUND,
                    f"knowledge point {knowledge_point_id} is not registered",
                )
            membership = KnowledgeMembership.create(topic_id, knowledge_point_id, order_index)
            existing = self._structure.knowledge_memberships.get(membership.membership_id)
            if existing is None:
                self._structure.knowledge_memberships[membership.membership_id] = membership
                self._structure._rebuild_indexes()
                return membership
            if existing.order_index != membership.order_index:
                refreshed = KnowledgeMembership(
                    membership_id=existing.membership_id,
                    topic_id=existing.topic_id,
                    knowledge_point_id=existing.knowledge_point_id,
                    order_index=membership.order_index,
                )
                self._structure.knowledge_memberships[existing.membership_id] = refreshed
                self._structure._rebuild_indexes()
                return refreshed
            return existing

    def remove_knowledge_from_topic(self, topic_id: str, knowledge_point_id: str) -> None:
        """Remove one topic <-> KP membership (no dangling refs left)."""
        with self._lock:
            self._isolated_topic(topic_id)
            membership = KnowledgeMembership.create(topic_id, knowledge_point_id)
            if membership.membership_id not in self._structure.knowledge_memberships:
                raise OrganizationValidationError(
                    OrganizationErrorCode.DANGLING_REFERENCE,
                    f"no membership between topic {topic_id} and KP {knowledge_point_id}",
                )
            del self._structure.knowledge_memberships[membership.membership_id]
            self._structure._rebuild_indexes()

    def get_topic_knowledge_points(self, topic_id: str) -> Tuple[str, ...]:
        """KP ids under a topic, sorted by (order_index, kp_id)."""
        with self._lock:
            self._isolated_topic(topic_id)
            members = [
                m
                for m in self._structure.knowledge_memberships.values()
                if m.topic_id == topic_id
            ]
            members.sort(key=lambda m: (_order_key(m.order_index), m.knowledge_point_id))
            return tuple(m.knowledge_point_id for m in members)

    def get_knowledge_point_topics(self, knowledge_point_id: str) -> Tuple[str, ...]:
        """Topics containing the KP, sorted by topic_id."""
        with self._lock:
            return tuple(sorted(self._structure._kp_topic_index.get(knowledge_point_id, ())))

    def get_unassigned_knowledge_points(self) -> Tuple[str, ...]:
        """Registered KPs with no topic membership (deterministic).

        Independent of session coverage and of validation / review
        state.
        """
        with self._lock:
            return tuple(
                sorted(kp for kp in self._kp_by_id if not self._structure._kp_topic_index.get(kp))
            )

    # ------------------------------------------------------------------
    # Session <-> KnowledgePoint membership
    # ------------------------------------------------------------------

    def add_knowledge_to_session(
        self,
        session_id: str,
        knowledge_point_id: str,
        order_index: Optional[int] = None,
    ) -> SessionKnowledgeMembership:
        """Link a registered session to a registered KP (idempotent).

        - SESSION_NOT_FOUND when the session is not registered here.
        - KNOWLEDGE_POINT_NOT_FOUND when the KP is not registered.
        - The session's course must match this service's course when
          the session carries one (CROSS_COURSE_MEMBERSHIP otherwise).
        """
        with self._lock:
            session = self._sessions_by_id.get(session_id)
            if session is None:
                raise OrganizationValidationError(
                    OrganizationErrorCode.SESSION_NOT_FOUND,
                    f"session {session_id} is not registered in course {self._course.course_id}",
                )
            session_course = getattr(session, "course_id", None)
            if session_course and session_course != self._course.course_id:
                raise OrganizationValidationError(
                    OrganizationErrorCode.CROSS_COURSE_MEMBERSHIP,
                    f"session {session_id} belongs to course {session_course}",
                )
            if knowledge_point_id not in self._kp_by_id:
                raise OrganizationValidationError(
                    OrganizationErrorCode.KNOWLEDGE_POINT_NOT_FOUND,
                    f"knowledge point {knowledge_point_id} is not registered",
                )
            membership = SessionKnowledgeMembership.create(session_id, knowledge_point_id, order_index)
            existing = self._structure.session_memberships.get(membership.membership_id)
            if existing is None:
                self._structure.session_memberships[membership.membership_id] = membership
                self._structure._rebuild_indexes()
                return membership
            if existing.order_index != membership.order_index:
                refreshed = SessionKnowledgeMembership(
                    membership_id=existing.membership_id,
                    session_id=existing.session_id,
                    knowledge_point_id=existing.knowledge_point_id,
                    order_index=membership.order_index,
                )
                self._structure.session_memberships[existing.membership_id] = refreshed
                self._structure._rebuild_indexes()
                return refreshed
            return existing

    def add_knowledge_to_sessions(
        self,
        session_id: str,
        knowledge_point_ids: Sequence[str],
    ) -> List[SessionKnowledgeMembership]:
        """Register several KPs on one session (idempotent batch)."""
        return [self.add_knowledge_to_session(session_id, kp) for kp in knowledge_point_ids]

    def remove_knowledge_from_session(self, session_id: str, knowledge_point_id: str) -> None:
        """Remove one session <-> KP membership."""
        with self._lock:
            if session_id not in self._sessions_by_id:
                raise OrganizationValidationError(
                    OrganizationErrorCode.SESSION_NOT_FOUND,
                    f"session {session_id} is not registered in course {self._course.course_id}",
                )
            membership = SessionKnowledgeMembership.create(session_id, knowledge_point_id)
            if membership.membership_id not in self._structure.session_memberships:
                raise OrganizationValidationError(
                    OrganizationErrorCode.DANGLING_REFERENCE,
                    f"no membership between session {session_id} and KP {knowledge_point_id}",
                )
            del self._structure.session_memberships[membership.membership_id]
            self._structure._rebuild_indexes()

    def get_session_knowledge_points(self, session_id: str) -> Tuple[str, ...]:
        """KP ids of one session, sorted by (order_index, kp_id)."""
        with self._lock:
            if session_id not in self._sessions_by_id:
                raise OrganizationValidationError(
                    OrganizationErrorCode.SESSION_NOT_FOUND,
                    f"session {session_id} is not registered in course {self._course.course_id}",
                )
            members = [
                m
                for m in self._structure.session_memberships.values()
                if m.session_id == session_id
            ]
            members.sort(key=lambda m: (_order_key(m.order_index), m.knowledge_point_id))
            return tuple(m.knowledge_point_id for m in members)

    def get_knowledge_point_sessions(self, knowledge_point_id: str) -> Tuple[str, ...]:
        """Sessions involving the KP, sorted by session_id."""
        with self._lock:
            return tuple(sorted(self._structure._kp_session_index.get(knowledge_point_id, ())))

    def get_uncovered_knowledge_points(self) -> Tuple[str, ...]:
        """Registered KPs with no session membership (deterministic).

        Independent of topic assignment and of validation / review
        state.
        """
        with self._lock:
            return tuple(
                sorted(kp for kp in self._kp_by_id if not self._structure._kp_session_index.get(kp))
            )

    # ------------------------------------------------------------------
    # Relations
    # ------------------------------------------------------------------

    def add_relation(
        self,
        source_knowledge_point_id: str,
        target_knowledge_point_id: str,
        relation_type: Union[KnowledgeRelationType, str],
    ) -> KnowledgeRelation:
        """Explicit directed relation between two registered KPs.

        - Both KPs must be registered (KNOWLEDGE_POINT_NOT_FOUND).
        - source != target (SELF_RELATION, stable error).
        - Duplicate (same quadruple) is a no-op; the reverse
          direction is a DISTINCT relation.
        """
        with self._lock:
            for kp_id in (source_knowledge_point_id, target_knowledge_point_id):
                if kp_id not in self._kp_by_id:
                    raise OrganizationValidationError(
                        OrganizationErrorCode.KNOWLEDGE_POINT_NOT_FOUND,
                        f"knowledge point {kp_id} is not registered",
                    )
            if source_knowledge_point_id == target_knowledge_point_id:
                raise OrganizationValidationError(
                    OrganizationErrorCode.SELF_RELATION,
                    "a knowledge point cannot relate to itself",
                )
            rtype = KnowledgeRelationType.from_string(relation_type)
            relation = KnowledgeRelation.create(
                self._course.course_id,
                source_knowledge_point_id,
                target_knowledge_point_id,
                rtype,
            )
            existing = self._structure.relations.get(relation.relation_id)
            if existing is None:
                self._structure.relations[relation.relation_id] = relation
                self._structure._rebuild_indexes()
                return relation
            return existing

    def remove_relation(
        self,
        source_knowledge_point_id: str,
        target_knowledge_point_id: str,
        relation_type: Union[KnowledgeRelationType, str],
    ) -> None:
        """Remove one explicit relation; DANGLING_REFERENCE when absent."""
        with self._lock:
            rtype = KnowledgeRelationType.from_string(relation_type)
            relation = KnowledgeRelation.create(
                self._course.course_id,
                source_knowledge_point_id,
                target_knowledge_point_id,
                rtype,
            )
            if relation.relation_id not in self._structure.relations:
                raise OrganizationValidationError(
                    OrganizationErrorCode.DANGLING_REFERENCE,
                    f"no relation from {source_knowledge_point_id} to {target_knowledge_point_id} of type {rtype.value}",
                )
            del self._structure.relations[relation.relation_id]
            self._structure._rebuild_indexes()

    def list_relations(self) -> Tuple[KnowledgeRelation, ...]:
        """All relations of the course, sorted by relation_id."""
        with self._lock:
            return tuple(
                sorted(
                    (r for r in self._structure.relations.values() if r.course_id == self._course.course_id),
                    key=lambda r: r.relation_id,
                )
            )

    def get_related_knowledge(
        self,
        knowledge_point_id: str,
        direction: str = "outgoing",
    ) -> Tuple[KnowledgeRelation, ...]:
        """Relations touching one KP, sorted by relation_id.

        - direction="outgoing": source == kp_id.
        - direction="incoming": target == kp_id.
        - direction="both": outgoing, then incoming.
        No transitive closure is ever performed.
        """
        with self._lock:
            if knowledge_point_id not in self._kp_by_id:
                raise OrganizationValidationError(
                    OrganizationErrorCode.KNOWLEDGE_POINT_NOT_FOUND,
                    f"knowledge point {knowledge_point_id} is not registered",
                )
            norm = direction.lower()
            if norm not in ("outgoing", "incoming", "both"):
                raise OrganizationValidationError(
                    OrganizationErrorCode.INVALID_INPUT,
                    f"direction must be 'outgoing', 'incoming' or 'both', got {direction!r}",
                )
            relations = [
                r
                for r in self._structure.relations.values()
                if r.course_id == self._course.course_id
            ]
            if norm == "outgoing":
                matched = [r for r in relations if r.source_knowledge_point_id == knowledge_point_id]
            elif norm == "incoming":
                matched = [r for r in relations if r.target_knowledge_point_id == knowledge_point_id]
            else:
                matched = [
                    r
                    for r in relations
                    if r.source_knowledge_point_id == knowledge_point_id
                    or r.target_knowledge_point_id == knowledge_point_id
                ]
            matched.sort(key=lambda r: r.relation_id)
            return tuple(matched)

    def get_relation_graph(self) -> RelationGraph:
        """Deterministic node / edge graph of the course relations."""
        with self._lock:
            relations = [r for r in self._structure.relations.values() if r.course_id == self._course.course_id]
            nodes = sorted(
                {r.source_knowledge_point_id for r in relations}
                | {r.target_knowledge_point_id for r in relations}
            )
            edges = tuple(
                (r.relation_id, r.source_knowledge_point_id, r.target_knowledge_point_id, r.relation_type.value)
                for r in sorted(relations, key=lambda r: r.relation_id)
            )
            return RelationGraph(
                course_id=self._course.course_id,
                nodes=tuple(nodes),
                edges=edges,
            )

    # ------------------------------------------------------------------
    # Aggregation (pure computation, no cache)
    # ------------------------------------------------------------------

    def _course_topic_count(self) -> int:
        """Number of topics of THIS course (foreign topics excluded)."""
        return sum(1 for t in self._structure.topics.values() if t.course_id == self._course.course_id)

    def get_coverage_report(self) -> CoverageReport:
        """Course-level coverage.  Covered == has >= 1 session membership."""
        with self._lock:
            total = len(self._kp_by_id)
            covered = len(self._structure._kp_session_index)
            covered = min(covered, total)
            session_count = len({m.session_id for m in self._structure.session_memberships.values()})
            ratio = (covered / total) if total else 0.0
            return CoverageReport(
                course_id=self._course.course_id,
                topic_count=self._course_topic_count(),
                knowledge_point_count=total,
                covered_knowledge_point_count=covered,
                uncovered_knowledge_point_count=total - covered,
                session_count=session_count,
                coverage_ratio=ratio,
            )

    def get_topic_coverage_report(self, topic_id: str) -> TopicCoverageReport:
        """Per-topic coverage among the KPs assigned to that topic."""
        with self._lock:
            self._isolated_topic(topic_id)
            kp_ids = set(self._structure._topic_kp_index.get(topic_id, ()))
            total = len(kp_ids)
            covered = len(kp_ids & set(self._structure._kp_session_index))
            ratio = (covered / total) if total else 0.0
            return TopicCoverageReport(
                topic_id=topic_id,
                knowledge_point_count=total,
                covered_count=covered,
                uncovered_count=total - covered,
                coverage_ratio=ratio,
            )

    def get_session_coverage_report(self, session_id: str) -> SessionCoverageReport:
        """Per-session stats: total KPs, already-known, newly-introduced.

        'Already known' = the KP has an EARLIER session membership,
        where earlier is decided by the session's deterministic
        session_number ordering (lower number == earlier; unnumbered
        sessions sort last).  'Newly introduced' = first time the KP
        is seen in any session up to and including this one.  Pure
        membership arithmetic - no semantic judgement.
        """
        with self._lock:
            if session_id not in self._sessions_by_id:
                raise OrganizationValidationError(
                    OrganizationErrorCode.SESSION_NOT_FOUND,
                    f"session {session_id} is not registered in course {self._course.course_id}",
                )
            order = self._session_order.get(session_id, 10 ** 9)
            kp_ids = set(self._structure._session_kp_index.get(session_id, ()))
            earlier = set()
            for other, kps in self._structure._session_kp_index.items():
                if other == session_id:
                    continue
                if self._session_order.get(other, 10 ** 9) < order:
                    earlier.update(kps)
            already_known = len(kp_ids & earlier)
            newly_introduced = len(kp_ids - earlier)
            return SessionCoverageReport(
                session_id=session_id,
                knowledge_point_count=len(kp_ids),
                already_known_count=already_known,
                newly_introduced_count=newly_introduced,
            )

    def get_validation_summary(self) -> ValidationSummary:
        """Aggregate validation over registered KPs (read-only).

        Clamps the average to [0.0, 1.0]; 0.0 when no KPs exist.
        """
        with self._lock:
            kps = list(self._kp_by_id.values())
            unverified = supported = conflicted = 0
            for kp in kps:
                status = ValidationStatus.from_string(kp.validation_status).value
                if status == ValidationStatus.UNVERIFIED.value:
                    unverified += 1
                elif status == ValidationStatus.SUPPORTED.value:
                    supported += 1
                elif status == ValidationStatus.CONFLICTED.value:
                    conflicted += 1
            if kps:
                average = sum(kp.knowledge_score for kp in kps) / len(kps)
                average = max(0.0, min(1.0, average))
            else:
                average = 0.0
            return ValidationSummary(
                course_id=self._course.course_id,
                unverified_count=unverified,
                supported_count=supported,
                conflicted_count=conflicted,
                average_knowledge_score=average,
            )

    def get_review_summary(self) -> ReviewSummary:
        """Aggregate LATEST effective ReviewStatus per KP (read-only).

        Follows Task 15 semantics: when the structure that registered
        a KP exposes review_records_for_knowledge_point, the last
        record's decision (stable review_id order) is the effective
        status; without records the KP's own review_status field
        applies.  ValidationStatus and ReviewStatus never leak into
        each other: SUPPORTED never becomes CONFIRMED.
        """
        pending = confirmed = rejected = kept = 0
        decision_map = {
            ReviewDecision.CONFIRM: ReviewStatus.CONFIRMED.value,
            ReviewDecision.REJECT: ReviewStatus.REJECTED.value,
            ReviewDecision.KEEP_UNVERIFIED: ReviewStatus.KEPT_UNVERIFIED.value,
        }
        with self._lock:
            for kp_id, kp in self._kp_by_id.items():
                structure = self._structure_by_kp.get(kp_id)
                effective = None
                if structure is not None and hasattr(structure, "review_records_for_knowledge_point"):
                    records = structure.review_records_for_knowledge_point(kp_id)
                    if records:
                        effective = decision_map[records[-1].decision]
                if effective is None:
                    effective = ReviewStatus.from_string(kp.review_status).value
                if effective == ReviewStatus.PENDING.value:
                    pending += 1
                elif effective == ReviewStatus.CONFIRMED.value:
                    confirmed += 1
                elif effective == ReviewStatus.REJECTED.value:
                    rejected += 1
                else:
                    kept += 1
            return ReviewSummary(
                course_id=self._course.course_id,
                pending_count=pending,
                confirmed_count=confirmed,
                rejected_count=rejected,
                kept_unverified_count=kept,
            )

    def get_course_summary(self) -> CourseKnowledgeSummary:
        """Full deterministic course summary (pure computation, no cache)."""
        with self._lock:
            session_count = len({m.session_id for m in self._structure.session_memberships.values()})
            return CourseKnowledgeSummary(
                course_id=self._course.course_id,
                topic_count=self._course_topic_count(),
                knowledge_point_count=len(self._kp_by_id),
                session_count=session_count,
                relation_count=len(self._structure.relations),
                validation_summary=self.get_validation_summary(),
                review_summary=self.get_review_summary(),
                coverage=self.get_coverage_report(),
            )

    # ------------------------------------------------------------------
    # Ingest helpers
    # ------------------------------------------------------------------

    def ingest_knowledge_structure(
        self,
        knowledge_structure: KnowledgeStructure,
        session_id: str,
    ) -> List[SessionKnowledgeMembership]:
        """Register the structure's KPs and link them all to one session.

        - Idempotent: re-ingesting the same structure + session adds
          nothing new.
        - Does NOT re-run evidence extraction and does NOT create new
          KnowledgePoints.
        - Does NOT assign topics: assignment remains an explicit,
          separate act (add_knowledge_to_topic).
        - Validation / review status are read as-is; this call never
          mutates them.
        """
        self.register_knowledge_structure(knowledge_structure)
        return self.add_knowledge_to_sessions(session_id, list(knowledge_structure.knowledge_points.keys()))

    def ingest_session_knowledge_points(
        self,
        session: ClassSession,
        knowledge_point_ids: Sequence[str],
    ) -> List[SessionKnowledgeMembership]:
        """Register a session and link it to the given registered KPs."""
        self.register_session(session)
        return self.add_knowledge_to_sessions(session.session_id, list(knowledge_point_ids))

    # ------------------------------------------------------------------
    # Snapshot / restore
    # ------------------------------------------------------------------

    def save_to_dict(self) -> Dict[str, Any]:
        """Deterministic full snapshot (structure + registered ids)."""
        with self._lock:
            return {
                "structure": self._structure.to_dict(),
                "knowledge_point_ids": sorted(self._kp_by_id),
                "session_ids": sorted(self._sessions_by_id),
            }

    @classmethod
    def from_snapshot(
        cls,
        snapshot: Mapping[str, Any],
        course: Course,
        knowledge_structure: Optional[KnowledgeStructure] = None,
    ) -> "KnowledgeOrganizationService":
        """Restore a service from save_to_dict().

        - Validates schema_version and dangling references.
        - Re-registers KPs from knowledge_structure when supplied
          (required for KP-dependent queries to work after restore).
        - Without a structure, only id bookkeeping is restored.
        """
        structure = CourseKnowledgeStructure.from_dict(snapshot["structure"])
        if structure.course_id != course.course_id:
            raise OrganizationValidationError(
                OrganizationErrorCode.CROSS_COURSE_MEMBERSHIP,
                f"snapshot course {structure.course_id} != course {course.course_id}",
            )
        service = cls(course, structure)
        registered: set = set()
        if knowledge_structure is not None:
            service.register_knowledge_structure(knowledge_structure)
            registered = set(knowledge_structure.knowledge_points)
            declared = tuple(snapshot.get("knowledge_point_ids") or ())
            missing = sorted(set(declared) - registered)
            if missing:
                raise OrganizationValidationError(
                    OrganizationErrorCode.DANGLING_REFERENCE,
                    f"knowledge_point_ids {missing} missing from the supplied knowledge_structure",
                )
        for kp_id in tuple(snapshot.get("knowledge_point_ids") or ()):
            registered.add(kp_id)
        for m in structure.knowledge_memberships.values():
            if m.knowledge_point_id not in registered:
                raise OrganizationValidationError(
                    OrganizationErrorCode.DANGLING_REFERENCE,
                    f"membership references unknown KP {m.knowledge_point_id}",
                )
        for m in structure.session_memberships.values():
            if m.knowledge_point_id not in registered:
                raise OrganizationValidationError(
                    OrganizationErrorCode.DANGLING_REFERENCE,
                    f"membership references unknown KP {m.knowledge_point_id}",
                )
        for r in structure.relations.values():
            if r.source_knowledge_point_id not in registered or r.target_knowledge_point_id not in registered:
                raise OrganizationValidationError(
                    OrganizationErrorCode.DANGLING_REFERENCE,
                    f"relation {r.relation_id} references an unknown KP",
                )
        for session_id in tuple(snapshot.get("session_ids") or ()):
            placeholder = ClassSession(
                session_id=session_id,
                course_id=course.course_id,
                session_number=0,
            )
            service.register_session(placeholder)
        return service


__all__ = [
    "SCHEMA_VERSION",
    "OrganizationErrorCode",
    "KnowledgeOrganizationError",
    "OrganizationValidationError",
    "OrganizationSchemaError",
    "KnowledgeRelationType",
    "Topic",
    "KnowledgeMembership",
    "SessionKnowledgeMembership",
    "KnowledgeRelation",
    "CoverageReport",
    "TopicCoverageReport",
    "SessionCoverageReport",
    "ValidationSummary",
    "ReviewSummary",
    "CourseKnowledgeSummary",
    "TopicNode",
    "RelationGraph",
    "CourseKnowledgeStructure",
    "KnowledgeOrganizationService",
]
