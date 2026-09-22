"""Knowledge Dependency & Prerequisite Analysis Layer (Task 28).

Deterministic, read-only dependency analysis on top of the Task 26
course knowledge organization (``KnowledgeOrganizationService``).

Semantics
---------
- Only *explicit* PREREQUISITE relations are analyzed.  A PREREQUISITE
  relation ``p -> d`` reads as "``p`` is a prerequisite of ``d``".
- Closures (prerequisite / dependent) are *analysis results* only; they
  never create new relations in the underlying structure.
- Cycle detection covers multi-node prerequisite cycles and explicit
  self-loops.  A cycle is reported, never silently resolved and never
  mutated away.
- Prerequisite coverage answers *structural course questions* (is the
  prerequisite session-covered, topic-organized, what is its
  validation/review state?).  It never states anything about a
  student's knowledge.

Constraints honored (spec section 2)
------------------------------------
- Evidence-first: no fact is invented; everything traces back to
  explicit relations and the registered course structure.
- Determinism: all ids are content-addressed (sha256 digests); no
  uuid4, datetime.now, random or builtin hash().
- Immutability: every report dataclass is frozen; the input service is
  only read, never mutated.
- No student inference: this layer has no concept of a learner.
- No database, no LLM, no network, no translation.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, FrozenSet, Mapping, Optional, Tuple

from src.knowledge_organization import (
    KnowledgeOrganizationService,
    KnowledgeRelationType,
    OrganizationSchemaError,
)

__all__ = [
    "DEPENDENCY_SCHEMA_VERSION",
    "DependencyErrorCode",
    "DependencyAnalysisError",
    "DependencyValidationError",
    "DependencySchemaError",
    "DependencyStatus",
    "DependencyCycle",
    "DependencyAnalysis",
    "DependencyCoverageItem",
    "DependencyCoverageReport",
    "KnowledgeDependencyAnalyzer",
]

DEPENDENCY_SCHEMA_VERSION = 1


class DependencyErrorCode(str, Enum):
    """Stable error codes for dependency analysis failures."""

    INVALID_INPUT = "invalid_input"
    KNOWLEDGE_POINT_NOT_FOUND = "knowledge_point_not_found"
    INVALID_STATE = "invalid_state"
    INVALID_SCHEMA_VERSION = "invalid_schema_version"
    CROSS_COURSE_REFERENCE = "cross_course_reference"


class DependencyAnalysisError(Exception):
    """Base error for the dependency analysis layer."""

    def __init__(self, code: DependencyErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code.value}] {message}")


class DependencyValidationError(DependencyAnalysisError):
    """Stable input validation error."""


class DependencySchemaError(DependencyAnalysisError):
    """Raised for unknown schema versions or corrupted payloads."""


class DependencyStatus(str, Enum):
    """Per-KP prerequisite depth outcome.

    - OK: depth is defined (the prerequisite subgraph is acyclic).
    - CYCLE_DETECTED: the KP sits inside a cycle or downstream of one;
      depth is undefined (-1), no unbounded recursion occurs.
    """

    OK = "ok"
    CYCLE_DETECTED = "cycle_detected"


@dataclass(frozen=True)
class DependencyCycle:
    """One detected prerequisite cycle (deterministic).

    ``node_ids`` lists the cycle members starting from the
    lexicographically smallest member, following the prerequisite
    direction; ``relation_ids`` carries the relation id used for each
    hop, in the same order.  ``len(relation_ids) == len(node_ids)``.

    ``cycle_id`` is deterministic:
    ``"cycle-" + sha256(course_id + ":" + ":".join(node_ids))[:24]``.
    """

    cycle_id: str
    course_id: str
    node_ids: Tuple[str, ...]
    relation_ids: Tuple[str, ...]

    @classmethod
    def create(
        cls,
        course_id: str,
        node_ids: Tuple[str, ...],
        relation_ids: Tuple[str, ...],
    ) -> "DependencyCycle":
        """Build a normalized cycle.

        Normalization: the node list is rotated so that the
        lexicographically smallest node comes first; relation ids are
        re-ordered to match the rotated hops.
        """
        if not course_id:
            raise DependencyValidationError(
                DependencyErrorCode.INVALID_INPUT, "course_id must be non-empty"
            )
        # Normalize: keep only distinct nodes (the closing repetition
        # of the start node is dropped when it equals the first).
        if node_ids and node_ids[0] == node_ids[-1] and len(set(node_ids)) > 1:
            node_ids = tuple(node_ids[:-1])
            relation_ids = tuple(relation_ids)
        if len(set(node_ids)) < 2 or len(relation_ids) != len(node_ids):
            raise DependencyValidationError(
                DependencyErrorCode.INVALID_INPUT,
                "a cycle needs at least 2 distinct nodes and one relation per hop",
            )
        pivot = min(range(len(node_ids)), key=lambda x: node_ids[x])
        rotated_nodes = node_ids[pivot:] + node_ids[:pivot]
        rotated_rels = relation_ids[pivot:] + relation_ids[:pivot]
        digest = hashlib.sha256(
            (course_id + ":" + ":".join(rotated_nodes)).encode("utf-8")
        ).hexdigest()[:24]
        return cls(
            cycle_id="cycle-" + digest,
            course_id=course_id,
            node_ids=tuple(rotated_nodes),
            relation_ids=tuple(rotated_rels),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "course_id": self.course_id,
            "node_ids": list(self.node_ids),
            "relation_ids": list(self.relation_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DependencyCycle":
        if not isinstance(data, Mapping):
            raise DependencyValidationError(
                DependencyErrorCode.INVALID_INPUT,
                "cycle payload must be a mapping",
            )
        node_ids = tuple(str(n) for n in data.get("node_ids") or ())
        relation_ids = tuple(str(r) for r in data.get("relation_ids") or ())
        course_id = str(data.get("course_id") or "")
        rebuilt = cls.create(course_id, node_ids, relation_ids)
        stored_id = str(data.get("cycle_id") or "")
        if stored_id and stored_id != rebuilt.cycle_id:
            raise DependencyValidationError(
                DependencyErrorCode.INVALID_STATE,
                "cycle_id does not match cycle content (corrupted payload)",
            )
        return rebuilt
@dataclass(frozen=True)
class DependencyAnalysis:
    """Full course dependency picture (one deterministic snapshot).

    - ``prerequisite_edges``: explicit prerequisite relations only
      (source = prerequisite, target = dependent), sorted by
      relation_id.
    - ``cycles``: all detected cycles, sorted by cycle_id.
    - ``depth_status``: per-KP ``(status, depth)``; KPs inside or
      downstream of a cycle are CYCLE_DETECTED with depth -1.
    - ``uncovered_prerequisite_pairs``: (dependent, prerequisite) where
      the prerequisite has no session coverage.

    Pure read result: never feeds back into the structure.
    """

    course_id: str
    prerequisite_edges: Tuple[Tuple[str, str, str], ...]
    cycles: Tuple[DependencyCycle, ...]
    cycle_count: int
    cyclical_knowledge_point_ids: Tuple[str, ...]
    depth_status: Dict[str, Tuple[DependencyStatus, int]]
    uncovered_prerequisite_pairs: Tuple[Tuple[str, str], ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": DEPENDENCY_SCHEMA_VERSION,
            "course_id": self.course_id,
            "prerequisite_edges": [
                {"source": s, "target": t, "relation_id": rid}
                for s, t, rid in self.prerequisite_edges
            ],
            "cycles": [c.to_dict() for c in self.cycles],
            "cycle_count": self.cycle_count,
            "cyclical_knowledge_point_ids": list(
                self.cyclical_knowledge_point_ids
            ),
            "depth_status": {
                kp: [st.value, depth]
                for kp, (st, depth) in self.depth_status.items()
            },
            "uncovered_prerequisite_pairs": [
                {"dependent": d, "prerequisite": p}
                for d, p in self.uncovered_prerequisite_pairs
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DependencyAnalysis":
        if not isinstance(data, Mapping):
            raise DependencyValidationError(
                DependencyErrorCode.INVALID_INPUT,
                "analysis payload must be a mapping",
            )
        schema_version = data.get("schema_version")
        if schema_version != DEPENDENCY_SCHEMA_VERSION:
            raise DependencySchemaError(
                DependencyErrorCode.INVALID_SCHEMA_VERSION,
                f"unsupported schema_version: {schema_version!r} "
                f"(expected {DEPENDENCY_SCHEMA_VERSION})",
            )
        edges = tuple(
            (
                str(e.get("source") or ""),
                str(e.get("target") or ""),
                str(e.get("relation_id") or ""),
            )
            for e in data.get("prerequisite_edges") or []
        )
        cycles = tuple(
            DependencyCycle.from_dict(c) for c in data.get("cycles") or []
        )
        depth_status = {
            str(kp): (DependencyStatus(st), int(depth))
            for kp, (st, depth) in (data.get("depth_status") or {}).items()
        }
        return cls(
            course_id=str(data.get("course_id") or ""),
            prerequisite_edges=edges,
            cycles=cycles,
            cycle_count=int(data.get("cycle_count") or len(cycles)),
            cyclical_knowledge_point_ids=tuple(
                str(n) for n in data.get("cyclical_knowledge_point_ids") or ()
            ),
            depth_status=depth_status,
            uncovered_prerequisite_pairs=tuple(
                (str(p.get("dependent") or ""), str(p.get("prerequisite") or ""))
                for p in data.get("uncovered_prerequisite_pairs") or []
            ),
        )


@dataclass(frozen=True)
class DependencyCoverageItem:
    """Per-KP prerequisite coverage snapshot (structural facts only).

    - ``prerequisite_ids``: all direct prerequisites (sorted).
    - ``covered_prerequisite_ids``: prerequisites with >= 1 session
      membership in this course (sorted).
    - ``uncovered_prerequisite_ids``: the complementary set (sorted).
    - ``topic_organized_prerequisite_ids``: prerequisites assigned to
      >= 1 topic (sorted).
    - ``validation_status`` / ``review_status``: state of the
      *dependent* KP itself (string form for stable serialization).

    No student claims, ever.
    """

    knowledge_point_id: str
    prerequisite_ids: Tuple[str, ...]
    covered_prerequisite_ids: Tuple[str, ...]
    uncovered_prerequisite_ids: Tuple[str, ...]
    topic_organized_prerequisite_ids: Tuple[str, ...]
    validation_status: str
    review_status: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "knowledge_point_id": self.knowledge_point_id,
            "prerequisite_ids": list(self.prerequisite_ids),
            "covered_prerequisite_ids": list(self.covered_prerequisite_ids),
            "uncovered_prerequisite_ids": list(self.uncovered_prerequisite_ids),
            "topic_organized_prerequisite_ids": list(
                self.topic_organized_prerequisite_ids
            ),
            "validation_status": self.validation_status,
            "review_status": self.review_status,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DependencyCoverageItem":
        if not isinstance(data, Mapping):
            raise DependencyValidationError(
                DependencyErrorCode.INVALID_INPUT,
                "coverage item payload must be a mapping",
            )
        return cls(
            knowledge_point_id=str(data.get("knowledge_point_id") or ""),
            prerequisite_ids=tuple(str(x) for x in data.get("prerequisite_ids") or ()),
            covered_prerequisite_ids=tuple(
                str(x) for x in data.get("covered_prerequisite_ids") or ()
            ),
            uncovered_prerequisite_ids=tuple(
                str(x) for x in data.get("uncovered_prerequisite_ids") or ()
            ),
            topic_organized_prerequisite_ids=tuple(
                str(x) for x in data.get("topic_organized_prerequisite_ids") or ()
            ),
            validation_status=str(data.get("validation_status") or "unverified"),
            review_status=str(data.get("review_status") or "pending"),
        )


@dataclass(frozen=True)
class DependencyCoverageReport:
    """Course-level prerequisite coverage report (frozen snapshot).

    ``missing_prerequisite_pairs``: (dependent, prerequisite) where the
    prerequisite has no session membership - reported as a data state
    ("A depends on uncovered prerequisite B"), *never* triggering
    session creation, topic assignment or study recommendations.
    """

    course_id: str
    items: Tuple[DependencyCoverageItem, ...]
    missing_prerequisite_pairs: Tuple[Tuple[str, str], ...]

    def get_item(self, knowledge_point_id: str) -> Optional[DependencyCoverageItem]:
        for item in self.items:
            if item.knowledge_point_id == knowledge_point_id:
                return item
        return None

    def has_uncovered_prerequisites(self, knowledge_point_id: str) -> bool:
        item = self.get_item(knowledge_point_id)
        return bool(item and item.uncovered_prerequisite_ids)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": DEPENDENCY_SCHEMA_VERSION,
            "course_id": self.course_id,
            "items": [i.to_dict() for i in self.items],
            "missing_prerequisite_pairs": [
                {"dependent": d, "prerequisite": p}
                for d, p in self.missing_prerequisite_pairs
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DependencyCoverageReport":
        if not isinstance(data, Mapping):
            raise DependencyValidationError(
                DependencyErrorCode.INVALID_INPUT,
                "coverage report payload must be a mapping",
            )
        schema_version = data.get("schema_version")
        if schema_version != DEPENDENCY_SCHEMA_VERSION:
            raise DependencySchemaError(
                DependencyErrorCode.INVALID_SCHEMA_VERSION,
                f"unsupported schema_version: {schema_version!r} "
                f"(expected {DEPENDENCY_SCHEMA_VERSION})",
            )
        items = tuple(
            DependencyCoverageItem.from_dict(i) for i in data.get("items") or []
        )
        missing = tuple(
            (str(p.get("dependent") or ""), str(p.get("prerequisite") or ""))
            for p in data.get("missing_prerequisite_pairs") or []
        )
        return cls(
            course_id=str(data.get("course_id") or ""),
            items=items,
            missing_prerequisite_pairs=missing,
        )
class KnowledgeDependencyAnalyzer:
    """Read-only, deterministic dependency analyzer for one course.

    Construction accepts the Task 26 ``KnowledgeOrganizationService``.
    A frozen snapshot of the prerequisite graph is taken on first use;
    later service mutations never leak into already-computed results,
    and re-analysis on the same input is idempotent.

    The service is only read - no relation, topic or membership is ever
    created or removed by this analyzer.
    """

    def __init__(self, service: KnowledgeOrganizationService) -> None:
        if not isinstance(service, KnowledgeOrganizationService):
            raise DependencyValidationError(
                DependencyErrorCode.INVALID_INPUT,
                "service must be a KnowledgeOrganizationService",
            )
        self._service = service
        self._lock = threading.RLock()
        self._snapshot: Optional["_DependencySnapshot"] = None

    @property
    def service(self) -> KnowledgeOrganizationService:
        return self._service

    def _snap(self) -> "_DependencySnapshot":
        with self._lock:
            if self._snapshot is None:
                self._snapshot = _DependencySnapshot(self._service)
            return self._snapshot

    def _require_kp(self, knowledge_point_id: str) -> str:
        snap = self._snap()
        if knowledge_point_id not in snap.kp_ids:
            raise DependencyValidationError(
                DependencyErrorCode.KNOWLEDGE_POINT_NOT_FOUND,
                f"knowledge point {knowledge_point_id!r} is not registered",
            )
        return knowledge_point_id

    # ------------------------------------------------------------------
    # Direct queries
    # ------------------------------------------------------------------

    def get_prerequisites(self, knowledge_point_id: str) -> Tuple[str, ...]:
        """Direct prerequisite ids of one KP (sorted, deduplicated).

        PREREQUISITE relation ``p -> kp`` means ``p`` is a prerequisite
        of ``kp``.  No transitive expansion is performed here.
        """
        self._require_kp(knowledge_point_id)
        snap = self._snap()
        return tuple(sorted(snap.prereq_of.get(knowledge_point_id, frozenset())))

    def get_dependents(self, knowledge_point_id: str) -> Tuple[str, ...]:
        """Direct dependents of one KP (sorted, deduplicated)."""
        self._require_kp(knowledge_point_id)
        snap = self._snap()
        return tuple(sorted(snap.dependents_of.get(knowledge_point_id, frozenset())))

    # ------------------------------------------------------------------
    # Closures
    # ------------------------------------------------------------------

    def get_prerequisite_chain(self, knowledge_point_id: str) -> Tuple[str, ...]:
        """Every prerequisite transitively reachable (multi-hop).

        This closure is an *analysis result*: it never creates
        A -> C relations in the structure.  Cycle walks terminate via
        a visited set; in-cycle nodes are included exactly once.
        """
        self._require_kp(knowledge_point_id)
        snap = self._snap()
        return tuple(sorted(snap.prereq_closure(knowledge_point_id)))

    def get_dependent_chain(self, knowledge_point_id: str) -> Tuple[str, ...]:
        """Every dependent transitively reachable (multi-hop, reverse)."""
        self._require_kp(knowledge_point_id)
        snap = self._snap()
        return tuple(sorted(snap.dependent_closure(knowledge_point_id)))

    def get_prerequisite_closure(self, knowledge_point_id: str) -> Tuple[str, ...]:
        """Spec alias of :meth:`get_prerequisite_chain`."""
        return self.get_prerequisite_chain(knowledge_point_id)

    def get_dependent_closure(self, knowledge_point_id: str) -> Tuple[str, ...]:
        """Spec alias of :meth:`get_dependent_chain`."""
        return self.get_dependent_chain(knowledge_point_id)

    # ------------------------------------------------------------------
    # Cycles
    # ------------------------------------------------------------------

    def detect_cycles(self) -> Tuple[DependencyCycle, ...]:
        """All prerequisite cycles in deterministic order (by cycle_id).

        Multi-node cycles come from strongly connected components;
        explicit self-loops (which Task 26 rejects at creation time but
        a reloaded/corrupted snapshot could still carry) are reported as
        length-1 cycles instead of recursing forever.
        """
        snap = self._snap()
        return tuple(snap.cycles)

    # ------------------------------------------------------------------
    # Depth
    # ------------------------------------------------------------------

    def get_dependency_depth(
        self, knowledge_point_id: str
    ) -> Tuple[DependencyStatus, int]:
        """Longest prerequisite chain depth for one KP.

        ``depth(A) = 0`` when A has no prerequisites; each hop adds 1.
        When A is inside or downstream of a cycle, the status is
        CYCLE_DETECTED and depth is -1 (undefined) so no infinite
        computation can occur.
        """
        self._require_kp(knowledge_point_id)
        snap = self._snap()
        return snap.depth_status.get(knowledge_point_id, (DependencyStatus.OK, 0))

    # ------------------------------------------------------------------
    # Coverage
    # ------------------------------------------------------------------

    def get_prerequisite_coverage_report(self) -> DependencyCoverageReport:
        """Course prerequisite-coverage report (frozen snapshot).

        For every registered KP: which prerequisites are session-covered
        / topic-organized, and the KP's own validation & review state.
        Missing prerequisites are reported as data only: no session is
        created, no topic assigned, no study plan recommended.
        """
        snap = self._snap()
        return snap.coverage_report

    # ------------------------------------------------------------------
    # Full course analysis
    # ------------------------------------------------------------------

    def analyze_course(self) -> DependencyAnalysis:
        """One deterministic dependency picture of the course.

        Pure computation over the frozen snapshot; repeated calls on an
        unchanged service return identical content.
        """
        snap = self._snap()
        return snap.analysis

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def save_to_dict(self) -> Dict[str, Any]:
        """Deterministic payload of this analyzer's frozen snapshot."""
        snap = self._snap()
        return {
            "schema_version": DEPENDENCY_SCHEMA_VERSION,
            "course_id": snap.course_id,
            "coverage": snap.coverage_report.to_dict(),
            "analysis": snap.analysis.to_dict(),
        }

    @classmethod
    def from_snapshot(
        cls,
        data: Mapping[str, Any],
        service: KnowledgeOrganizationService,
    ) -> "KnowledgeDependencyAnalyzer":
        """Restore an analyzer whose snapshot must match the service.

        Raises DependencySchemaError for unknown schema versions,
        CROSS_COURSE_REFERENCE for foreign payloads, and INVALID_STATE
        when the stored snapshot does not equal the recomputed one
        (guards against silently accepting stale / corrupted files).
        """
        if not isinstance(service, KnowledgeOrganizationService):
            raise DependencyValidationError(
                DependencyErrorCode.INVALID_INPUT,
                "service must be a KnowledgeOrganizationService",
            )
        if not isinstance(data, Mapping):
            raise DependencyValidationError(
                DependencyErrorCode.INVALID_INPUT, "payload must be a mapping"
            )
        schema_version = data.get("schema_version")
        if schema_version != DEPENDENCY_SCHEMA_VERSION:
            raise DependencySchemaError(
                DependencyErrorCode.INVALID_SCHEMA_VERSION,
                f"unsupported schema_version: {schema_version!r} "
                f"(expected {DEPENDENCY_SCHEMA_VERSION})",
            )
        course_id = str(data.get("course_id") or "")
        if course_id != service.course_id:
            raise DependencyValidationError(
                DependencyErrorCode.CROSS_COURSE_REFERENCE,
                f"payload course {course_id!r} does not match service course "
                f"{service.course_id!r}",
            )
        analyzer = cls(service)
        with analyzer._lock:
            snap = _DependencySnapshot(service)
            snap._verify_restored(data)
            analyzer._snapshot = snap
        return analyzer


class _DependencySnapshot:
    """One-pass frozen picture of the prerequisite graph + coverage.

    Built exactly once per analyzer.  All analyzer queries read only
    this structure; the live service is never mutated by this layer.
    Every walk is iterative (explicit stacks), so cycles can never
    cause unbounded recursion.
    """

    def __init__(self, service: KnowledgeOrganizationService) -> None:
        course = service.course
        structure = service.structure
        self.course_id: str = course.course_id

        kp_by_id = service._kp_by_id
        self.kp_ids: Tuple[str, ...] = tuple(sorted(kp_by_id))
        self._kp_ids_set: FrozenSet[str] = frozenset(self.kp_ids)

        # Explicit prerequisite edges (source = prerequisite,
        # target = dependent).  Foreign-course relations excluded.
        prereq_relations = [
            r
            for r in structure.relations.values()
            if r.course_id == self.course_id
            and r.relation_type == KnowledgeRelationType.PREREQUISITE
        ]
        prereq_of: Dict[str, set] = {}
        dependents_of: Dict[str, set] = {}
        for r in prereq_relations:
            prereq_of.setdefault(r.target_knowledge_point_id, set()).add(
                r.source_knowledge_point_id
            )
            dependents_of.setdefault(r.source_knowledge_point_id, set()).add(
                r.target_knowledge_point_id
            )
        self.prereq_of: Dict[str, FrozenSet[str]] = {
            kp: frozenset(prereq_of.get(kp, ())) for kp in self.kp_ids
        }
        self.dependents_of: Dict[str, FrozenSet[str]] = {
            kp: frozenset(dependents_of.get(kp, ())) for kp in self.kp_ids
        }
        self.prerequisite_edges: Tuple[Tuple[str, str, str], ...] = tuple(
            (r.source_knowledge_point_id, r.target_knowledge_point_id, r.relation_id)
            for r in sorted(prereq_relations, key=lambda r: r.relation_id)
        )
        # Explicit self-loops (defensive: Task 26 rejects them at
        # creation; a reloaded structure might still carry them).
        self.self_loop_pairs: Tuple[Tuple[str, str], ...] = tuple(
            (r.source_knowledge_point_id, r.relation_id)
            for r in prereq_relations
            if r.source_knowledge_point_id == r.target_knowledge_point_id
        )

        # Structural coverage facts (session / topic memberships).
        kp_session: Dict[str, set] = {}
        for m in structure.session_memberships.values():
            if m.knowledge_point_id in self._kp_ids_set:
                kp_session.setdefault(m.knowledge_point_id, set()).add(m.session_id)
        self.kp_covered: FrozenSet[str] = frozenset(kp_session.keys())
        kp_topic: Dict[str, set] = {}
        for m in structure.knowledge_memberships.values():
            if m.knowledge_point_id in self._kp_ids_set:
                kp_topic.setdefault(m.knowledge_point_id, set()).add(m.topic_id)
        self.kp_topic_assigned: FrozenSet[str] = frozenset(kp_topic.keys())
        self.kp_validation_status: Dict[str, str] = {
            kp: kp_by_id[kp].validation_status for kp in self.kp_ids
        }
        self.kp_review_status: Dict[str, str] = {
            kp: kp_by_id[kp].review_status for kp in self.kp_ids
        }

        cycles, cyclic_kps, depth_status = self._compute_cycles_and_depth()
        self.cycles: Tuple[DependencyCycle, ...] = tuple(
            sorted(cycles, key=lambda c: c.cycle_id)
        )
        self.cyclic_kp_ids: FrozenSet[str] = frozenset(cyclic_kps)
        self.depth_status: Dict[str, Tuple[DependencyStatus, int]] = depth_status

        self.coverage_report: DependencyCoverageReport = self._build_coverage_report()
        self.analysis: DependencyAnalysis = self._build_analysis()
    # ------------------------------------------------------------------

    def _compute_cycles_and_depth(self):
        """Iterative Tarjan SCC + longest-prerequisite-path depth.

        Depth is only defined for KPs whose prerequisite subgraph is
        acyclic; anything on or downstream of a cycle gets
        CYCLE_DETECTED / -1 (bounded, iterative computation only).
        """
        adj: Dict[str, tuple] = {
            kp: tuple(sorted(self.prereq_of.get(kp, frozenset()))) for kp in self.kp_ids
        }
        self_loop_kps = {kp for kp, _ in self.self_loop_pairs}

        # Iterative Tarjan SCC.
        index: Dict[str, int] = {}
        lowlink: Dict[str, int] = {}
        on_stack: Dict[str, bool] = {}
        stack: list = []
        counter = 0
        sccs: list = []

        for root in self.kp_ids:
            if root in index:
                continue
            index[root] = lowlink[root] = counter
            counter += 1
            stack.append(root)
            on_stack[root] = True
            work_stack: list = [(root, 0)]  # (node, adjacency cursor)
            while work_stack:
                node, cursor = work_stack[-1]
                successors = adj.get(node, ())
                if cursor < len(successors):
                    nxt = successors[cursor]
                    work_stack[-1] = (node, cursor + 1)
                    if nxt not in index:
                        index[nxt] = lowlink[nxt] = counter
                        counter += 1
                        stack.append(nxt)
                        on_stack[nxt] = True
                        work_stack.append((nxt, 0))
                    elif on_stack.get(nxt, False):
                        lowlink[node] = min(lowlink[node], index[nxt])
                else:
                    work_stack.pop()
                    if work_stack:
                        parent = work_stack[-1][0]
                        lowlink[parent] = min(lowlink[parent], lowlink[node])
                    if lowlink[node] == index[node]:
                        members = []
                        while True:
                            member = stack.pop()
                            on_stack[member] = False
                            members.append(member)
                            if member == node:
                                break
                        sccs.append(sorted(members))

        cycles: list = []
        cyclic_kps: set = set()
        for scc in sccs:
            if len(scc) > 1:
                cycles.append(self._scc_to_cycle(scc))
                cyclic_kps.update(scc)
        for scc in sccs:
            if len(scc) == 1 and scc[0] in self_loop_kps:
                node = scc[0]
                rid = next(r for kp, r in self.self_loop_pairs if kp == node)
                cycles.append(
                    DependencyCycle.create(self.course_id, (node, node), (rid, rid))
                )
                cyclic_kps.add(node)

        # Longest prerequisite chain, memoized.  Recursion only ever
        # descends into strict prerequisite chains of the acyclic part;
        # cycle members are short-circuited before descending, so the
        # walk is bounded by the number of KPs.
        memo: Dict[str, int] = {}
        cyclic_set: set = set(cyclic_kps)

        def depth_of(kp: str) -> Tuple[DependencyStatus, int]:
            if kp in memo:
                return DependencyStatus.OK, memo[kp]
            if kp in cyclic_set:
                return DependencyStatus.CYCLE_DETECTED, -1
            best = 0
            for p in adj.get(kp, ()):
                st, d = depth_of(p)
                if st == DependencyStatus.CYCLE_DETECTED:
                    return DependencyStatus.CYCLE_DETECTED, -1
                best = max(best, d + 1)
            memo[kp] = best
            return DependencyStatus.OK, best

        depth_status: Dict[str, Tuple[DependencyStatus, int]] = {}
        for kp in self.kp_ids:
            depth_status[kp] = depth_of(kp)
        return cycles, sorted(cyclic_kps), depth_status

    def _scc_to_cycle(self, scc_members: list) -> DependencyCycle:
        """Deterministic representative cycle inside one SCC.

        Restricted DFS over the SCC (members only, directed by
        prerequisite edges: node -> its prerequisites) starting
        from the lexicographically smallest member; neighbors are
        explored in sorted order, and the first back-edge (node
        to a prerequisite already on the DFS path) closes a
        simple cycle.  Because the SCC is strongly connected, at
        least one SCC-internal back edge exists, so a cycle is
        always found.  The result is fully deterministic because
        exploration order is sorted.
        """
        member_set = set(scc_members)
        start = min(scc_members)
        stack: list = []
        on_path: set = set()

        def dfs(node: str) -> Optional[list]:
            on_path.add(node)
            stack.append(node)
            for nxt in sorted(self.prereq_of.get(node, frozenset())):
                if nxt not in member_set:
                    continue
                if nxt in on_path:
                    idx = stack.index(nxt)
                    return stack[idx:] + [nxt]
                found = dfs(nxt)
                if found is not None:
                    return found
            stack.pop()
            on_path.discard(node)
            return None

        found: Optional[list] = dfs(start)
        if found is None:
            # A strongly connected SCC of > 1 node always has an
            # SCC-internal cycle; walk the smallest member's
            # SCC-internal prerequisite chain until a node
            # repeats (always happens within len(scc) steps).
            order = [start]
            current = start
            for _ in range(len(scc_members)):
                internal = [
                    p for p in self.prereq_of.get(current, frozenset())
                    if p in member_set
                ]
                if not internal:
                    break
                nxt = min(internal)
                if nxt in order:
                    order.append(nxt)
                    break
                order.append(nxt)
                current = nxt
            if len(order) < 2:
                # Only possible with a corrupted input; fall back
                # to the lexicographically smallest SCC edge pair.
                a, b = sorted(member_set)[:2]
                order = [a, b, a]
            found = order
        rels = []
        for i in range(len(found) - 1):
            # Hops: found[i+1] is a prerequisite of found[i].
            rels.append(self._edge_relation_id(found[i + 1], found[i]))
        return DependencyCycle.create(self.course_id, tuple(found), tuple(rels))
    def _edge_relation_id(self, prereq: str, dependent: str) -> str:
        for source, target, rid in self.prerequisite_edges:
            if source == prereq and target == dependent:
                return rid
        return "krel-missing"

    # ------------------------------------------------------------------
    # Closures (iterative)
    # ------------------------------------------------------------------

    def prereq_closure(self, start: str) -> set:
        visited: set = set()
        stack = [start]
        while stack:
            node = stack.pop()
            for p in self.prereq_of.get(node, frozenset()):
                if p not in visited:
                    visited.add(p)
                    stack.append(p)
        visited.discard(start)
        return visited

    def dependent_closure(self, start: str) -> set:
        visited: set = set()
        stack = [start]
        while stack:
            node = stack.pop()
            for d in self.dependents_of.get(node, frozenset()):
                if d not in visited:
                    visited.add(d)
                    stack.append(d)
        visited.discard(start)
        return visited

    # ------------------------------------------------------------------
    # Coverage / analysis
    # ------------------------------------------------------------------

    def _build_coverage_report(self) -> DependencyCoverageReport:
        items: list = []
        missing: list = []
        for kp in self.kp_ids:
            prereqs = tuple(sorted(self.prereq_of.get(kp, frozenset())))
            covered = tuple(sorted(p for p in prereqs if p in self.kp_covered))
            uncovered = tuple(sorted(p for p in prereqs if p not in self.kp_covered))
            topic_org = tuple(
                sorted(p for p in prereqs if p in self.kp_topic_assigned)
            )
            items.append(
                DependencyCoverageItem(
                    knowledge_point_id=kp,
                    prerequisite_ids=prereqs,
                    covered_prerequisite_ids=covered,
                    uncovered_prerequisite_ids=uncovered,
                    topic_organized_prerequisite_ids=topic_org,
                    validation_status=self.kp_validation_status.get(
                        kp, "unverified"
                    ),
                    review_status=self.kp_review_status.get(kp, "pending"),
                )
            )
            for p in uncovered:
                missing.append((kp, p))
        missing.sort()
        return DependencyCoverageReport(
            course_id=self.course_id,
            items=tuple(items),
            missing_prerequisite_pairs=tuple(missing),
        )

    def _build_analysis(self) -> DependencyAnalysis:
        return DependencyAnalysis(
            course_id=self.course_id,
            prerequisite_edges=self.prerequisite_edges,
            cycles=self.cycles,
            cycle_count=len(self.cycles),
            cyclical_knowledge_point_ids=tuple(sorted(self.cyclic_kp_ids)),
            depth_status=dict(self.depth_status),
            uncovered_prerequisite_pairs=self.coverage_report.missing_prerequisite_pairs,
        )

    def _verify_restored(self, data: Mapping[str, Any]) -> None:
        """A restored snapshot must equal the recomputed one."""
        if (data.get("coverage") or {}) != self.coverage_report.to_dict():
            raise DependencyValidationError(
                DependencyErrorCode.INVALID_STATE,
                "restored coverage snapshot does not match service state",
            )
        if (data.get("analysis") or {}) != self.analysis.to_dict():
            raise DependencyValidationError(
                DependencyErrorCode.INVALID_STATE,
                "restored analysis snapshot does not match service state",
            )