"""Deterministic Study Planning Layer (Task 33).

Pipeline position:

    Course Knowledge + Dependency + Coverage + Student LearningState
    + Exercise Results
        ->
    StudyPlan (rule-triggered, fully explainable, deterministic)

Design constraints honored
--------------------------
- Rule system, NOT an AI ranking system (spec 33.5): every
  StudyItem carries explicit ``reason_codes``; no score-based
  ranking, no LLM, no embedding similarity.
- No mastery model (spec 33.8): reasons are rule triggers only;
  the string "weak" never appears in item semantics.
- CONFLICTED is a course-content review signal, NOT student
  weakness (spec 33.6): it surfaces as REVIEW_PENDING together with
  a conflict annotation, never as a "needs practice" claim.
- "Recent incorrect" uses a caller-defined window over the most
  recent N ANSWERED events (spec 33.9); no datetime.now()
  anywhere.
- "Low practice" uses caller-supplied minimum_practice_count
  (spec 33.10).
- Deterministic ordering (spec 33.21): reason priority, then
  dependency depth, then knowledge_point_id.
- Plan is an immutable snapshot (spec 33.13); plan_id is a
  deterministic content address over (student, course, canonical
  input, rules_version) -- same input state -> same plan id.
- Student / course isolation is structural: the planner only
  reads the supplied per-student log and the course snapshot.
- Standard library only; no uuid / datetime.now / random / hash().
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, FrozenSet, Iterable, Mapping, Optional, Tuple

from src.knowledge_dependency import (
    DependencyCoverageItem,
    DependencyStatus,
)

__all__ = [
    "STUDY_PLAN_SCHEMA_VERSION",
    "RULES_VERSION",
    "StudyReason",
    "StudyItem",
    "StudyPlan",
    "PathStatus",
    "LearningPath",
    "StudyPlanErrorCode",
    "StudyPlanError",
    "StudyPlanValidationError",
    "StudyPlanSchemaError",
    "StudyPlanner",
]

STUDY_PLAN_SCHEMA_VERSION = 1
RULES_VERSION = "rules-v1"


class StudyReason(str, Enum):
    """Rule-trigger reasons only (spec 33.4) -- deliberately no
    WEAK_KNOWLEDGE: this system has no mastery model."""

    UNCOVERED_PREREQUISITE = "uncovered_prerequisite"
    UNVERIFIED_KNOWLEDGE = "unverified_knowledge"
    CONFLICTED_KNOWLEDGE = "conflicted_knowledge"
    REVIEW_PENDING = "review_pending"
    LOW_PRACTICE_COUNT = "low_practice_count"
    RECENT_INCORRECT = "recent_incorrect"


# Hardcoded priority order (spec 33.5): prerequisite gaps first,
# then recent incorrect, low practice, review pending, and finally
# unverified / conflicted knowledge.  Reason codes are stored
# in a StudyItem in this priority order.
_REASON_PRIORITY = (
    StudyReason.UNCOVERED_PREREQUISITE,
    StudyReason.RECENT_INCORRECT,
    StudyReason.LOW_PRACTICE_COUNT,
    StudyReason.REVIEW_PENDING,
    StudyReason.UNVERIFIED_KNOWLEDGE,
    StudyReason.CONFLICTED_KNOWLEDGE,
)
_REASON_RANK = {r: i for i, r in enumerate(_REASON_PRIORITY)}


class StudyPlanErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    UNKNOWN_KNOWLEDGE_POINT = "unknown_knowledge_point"
    UNKNOWN_STUDENT = "unknown_student"
    UNKNOWN_EXERCISE = "unknown_exercise"
    INVALID_SCHEMA_VERSION = "invalid_schema_version"
    INVALID_STATE = "invalid_state"


class StudyPlanError(Exception):
    def __init__(self, code: StudyPlanErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code.value}] {message}")


class StudyPlanValidationError(StudyPlanError):
    pass


class StudyPlanSchemaError(StudyPlanError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha24(payload: Any) -> str:
    if isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = _canonical_json(payload).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


@dataclass(frozen=True)
class StudyItem:
    """One explainable planning entry (spec 33.3 / 33.20)."""

    knowledge_point_id: str
    reason_codes: Tuple[StudyReason, ...]
    prerequisite_ids: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "knowledge_point_id": self.knowledge_point_id,
            "reason_codes": [r.value for r in self.reason_codes],
            "prerequisite_ids": list(self.prerequisite_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StudyItem":
        reasons = tuple(
            StudyReason(str(r))
            for r in sorted(
                {str(r) for r in (data.get("reason_codes") or ())},
                key=lambda v: _REASON_RANK.get(StudyReason(v), len(_REASON_RANK)),
            )
        )
        return cls(
            knowledge_point_id=str(data.get("knowledge_point_id") or ""),
            reason_codes=reasons,
            prerequisite_ids=tuple(sorted({str(p) for p in (data.get("prerequisite_ids") or ())})),
        )


@dataclass(frozen=True)
class StudyPlan:
    """Immutable, deterministic planning snapshot (spec 33.11 / 33.13)."""

    plan_id: str
    student_id: str
    course_id: str
    items: Tuple[StudyItem, ...]
    rules_version: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": STUDY_PLAN_SCHEMA_VERSION,
            "plan_id": self.plan_id,
            "student_id": self.student_id,
            "course_id": self.course_id,
            "items": [item.to_dict() for item in self.items],
            "rules_version": self.rules_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StudyPlan":
        sv = data.get("schema_version")
        if sv != STUDY_PLAN_SCHEMA_VERSION:
            raise StudyPlanSchemaError(
                StudyPlanErrorCode.INVALID_SCHEMA_VERSION,
                f"unsupported schema_version: {sv!r}",
            )
        items = tuple(StudyItem.from_dict(d) for d in (data.get("items") or ()))
        plan = cls(
            plan_id="",
            student_id=str(data.get("student_id") or ""),
            course_id=str(data.get("course_id") or ""),
            items=items,
            rules_version=str(data.get("rules_version") or RULES_VERSION),
        )
        payload = {
            "student_id": plan.student_id,
            "course_id": plan.course_id,
            "items": [i.to_dict() for i in plan.items],
            "rules_version": plan.rules_version,
        }
        expected = "plan-" + _sha24(payload)
        stored = str(data.get("plan_id") or "")
        if stored and stored != expected:
            raise StudyPlanValidationError(
                StudyPlanErrorCode.INVALID_STATE,
                "plan_id does not match content (corrupted payload)",
            )
        return cls(
            plan_id=expected,
            student_id=plan.student_id,
            course_id=plan.course_id,
            items=items,
            rules_version=plan.rules_version,
        )


class PathStatus(str, Enum):
    OK = "ok"
    DEPENDENCY_CYCLE = "dependency_cycle"
    UNKNOWN_KNOWLEDGE_POINT = "unknown_knowledge_point"


@dataclass(frozen=True)
class LearningPath:
    """Prerequisite chain for a target KP (spec 33.14 / 33.15).

    ``node_ids`` is ordered root-first: A -> B -> C when A is the
    ultimate prerequisite of C.  A cycle returns status
    DEPENDENCY_CYCLE with the involved ids; no infinite loop.
    """

    status: PathStatus
    target_knowledge_point_id: str
    node_ids: Tuple[str, ...]
    cycle_node_ids: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": STUDY_PLAN_SCHEMA_VERSION,
            "status": self.status.value,
            "target_knowledge_point_id": self.target_knowledge_point_id,
            "node_ids": list(self.node_ids),
            "cycle_node_ids": list(self.cycle_node_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LearningPath":
        sv = data.get("schema_version")
        if sv != STUDY_PLAN_SCHEMA_VERSION:
            raise StudyPlanSchemaError(
                StudyPlanErrorCode.INVALID_SCHEMA_VERSION,
                f"unsupported schema_version: {sv!r}",
            )
        try:
            status = PathStatus(str(data.get("status") or ""))
        except ValueError:
            raise StudyPlanValidationError(
                StudyPlanErrorCode.INVALID_STATE,
                f"unknown path status: {data.get('status')!r}",
            )
        return cls(
            status=status,
            target_knowledge_point_id=str(data.get("target_knowledge_point_id") or ""),
            node_ids=tuple(str(n) for n in (data.get("node_ids") or ())),
            cycle_node_ids=tuple(str(n) for n in (data.get("cycle_node_ids") or ())),
        )


class StudyPlanner:
    """Deterministic rule-based planner (spec 33.5 / 33.7 / 33.9 / 33.10).

    Inputs are all explicit snapshots:

    - ``course_kps``: ``kp_id -> KnowledgePoint`` for one course.
    - ``prerequisites``: ``dependent_kp -> (prerequisite kps, ...)``
      built from PREREQUISITE relations only (spec 33.15: RELATED /
      CONTRASTS / EXTENDS never count).
    - ``uncovered_kps``: KP ids with no session membership.
    - ``validation_status``: ``kp_id -> str`` (e.g. UNVERIFIED /
      SUPPORTED / CONFLICTED).
    - ``review_status``: ``kp_id -> str`` (PENDING / CONFIRMED / ...).
    - ``practice_counts``: ``kp_id -> int`` answers recorded.
    - ``recent_incorrect_kps``: KP ids with an INCORRECT ANSWERED
      event inside the caller-chosen window of the last N events
      (spec 33.9 -- no datetime.now()).

    Every rule is hardcoded; the output is fully explainable.
    """

    def __init__(
        self,
        course_id: str,
        course_kps: Mapping[str, Any],
        prerequisites: Mapping[str, Tuple[str, ...]],
        *,
        uncovered_kps: Iterable[str] = (),
        validation_status: Optional[Mapping[str, str]] = None,
        review_status: Optional[Mapping[str, str]] = None,
        practice_counts: Optional[Mapping[str, int]] = None,
        recent_incorrect_kps: Iterable[str] = (),
        minimum_practice_count: int = 1,
    ) -> None:
        cid = str(course_id or "").strip()
        if not cid:
            raise StudyPlanValidationError(
                StudyPlanErrorCode.INVALID_INPUT, "course_id must be non-empty"
            )
        if isinstance(minimum_practice_count, bool) or not isinstance(
            minimum_practice_count, int
        ) or minimum_practice_count < 0:
            raise StudyPlanValidationError(
                StudyPlanErrorCode.INVALID_INPUT,
                "minimum_practice_count must be a non-negative integer",
            )
        self._course_id = cid
        self._kp_ids: FrozenSet[str] = frozenset(str(k) for k in course_kps)
        if not self._kp_ids:
            raise StudyPlanValidationError(
                StudyPlanErrorCode.INVALID_INPUT, "course must have at least one KP"
            )
        known = self._kp_ids
        self._prerequisites: Dict[str, Tuple[str, ...]] = {
            str(k): tuple(sorted({str(p) for p in v if str(p) in known}))
            for k, v in (prerequisites or {}).items()
            if str(k) in known
        }
        self._uncovered: FrozenSet[str] = frozenset(
            str(k) for k in uncovered_kps if str(k) in known
        )
        self._validation: Dict[str, str] = {
            str(k): str(v) for k, v in (validation_status or {}).items() if str(k) in known
        }
        self._review: Dict[str, str] = {
            str(k): str(v) for k, v in (review_status or {}).items() if str(k) in known
        }
        self._practice: Dict[str, int] = {
            str(k): int(v) for k, v in (practice_counts or {}).items() if str(k) in known
        }
        self._recent_incorrect: FrozenSet[str] = frozenset(
            str(k) for k in recent_incorrect_kps if str(k) in known
        )
        self._min_practice = minimum_practice_count
        self._depth_cache: Dict[str, Any] = {}
        self._cycle: Optional[FrozenSet[str]] = None
        self._cycle_members: Tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # prerequisite graph helpers (PREREQUISITE edges only, spec 33.15)
    # ------------------------------------------------------------------

    def _cycle_and_depths(self) -> None:
        if self._cycle is not None or self._depth_cache:
            return
        # Iterative DFS with deterministic node order; detect any
        # back edge -> cycle set.
        state: Dict[str, int] = {}  # 0 unseen, 1 in-progress, 2 done

        for start_root in sorted(self._kp_ids):
            if state.get(start_root, 0) == 2:
                continue
            path: list = []
            pos: Dict[str, int] = {}
            root = start_root
            while True:
                if state.get(root, 0) == 2:
                    break
                if state.get(root, 0) == 1:
                    if root in pos:
                        cycle_nodes = frozenset(path[pos[root]:])
                        self._cycle = cycle_nodes
                        self._cycle_members = tuple(sorted(cycle_nodes))
                        break
                    root = next(
                        (n for n in self._prerequisites.get(root, ()) if state.get(n, 0) == 1),
                        None,
                    )
                    if root is None:
                        break
                    continue
                state[root] = 1
                pos[root] = len(path)
                path.append(root)
                nexts = [n for n in self._prerequisites.get(root, ()) if state.get(n, 0) != 2]
                if not nexts:
                    state[root] = 2
                    path.pop()
                    if not path:
                        break
                    root = path[-1]
                    continue
                root = nexts[0]
            if self._cycle is not None:
                break
        if self._cycle is None:
            # depths: longest prerequisite chain (acyclic guaranteed)
            memo: Dict[str, int] = {}

            def depth(kp: str) -> int:
                if kp in memo:
                    return memo[kp]
                memo[kp] = 0  # guard (acyclic, safe)
                deps = self._prerequisites.get(kp, ())
                memo[kp] = 1 + max((depth(d) for d in deps), default=-1)
                return memo[kp]

            for kp in sorted(self._kp_ids):
                depth(kp)
            self._depth_cache = memo
        else:
            self._depth_cache = {kp: -1 for kp in self._kp_ids}

    def prerequisite_depth(self, kp_id: str) -> int:
        self._cycle_and_depths()
        return self._depth_cache.get(str(kp_id), -1)

    def detect_prerequisite_cycle(self) -> Tuple[PathStatus, Tuple[str, ...]]:
        self._cycle_and_depths()
        if self._cycle is not None:
            return PathStatus.DEPENDENCY_CYCLE, self._cycle_members
        return PathStatus.OK, ()

    # ------------------------------------------------------------------
    # rule evaluation
    # ------------------------------------------------------------------

    def _reasons_for(self, kp: str) -> Tuple[StudyReason, ...]:
        reasons: set = set()
        direct_prereqs = self._prerequisites.get(kp, ())
        # Spec 33.17: if A is prerequisite of B and B is uncovered,
        # the path must include B -- but the REASON on the *item for
        # the dependent KP* is that its prerequisite was not covered
        # in class.  Trigger when any direct prerequisite of ``kp``
        # is uncovered.
        if any(p in self._uncovered for p in direct_prereqs):
            reasons.add(StudyReason.UNCOVERED_PREREQUISITE)
        # Also: kp itself may be uncovered and that fact belongs to
        # the UNCOVERED reason of any *dependent* item, which is
        # handled above by the dependents\' own evaluation.
        if kp in self._recent_incorrect:
            reasons.add(StudyReason.RECENT_INCORRECT)
        if self._practice.get(kp, 0) < self._min_practice:
            reasons.add(StudyReason.LOW_PRACTICE_COUNT)
        validation = self._validation.get(kp, "UNVERIFIED").upper()
        if validation in ("CONFLICTED",):
            reasons.add(StudyReason.CONFLICTED_KNOWLEDGE)
            # spec 33.6: conflicted content surfaces as a review
            # signal, not student weakness.
            reasons.add(StudyReason.REVIEW_PENDING)
        if self._review.get(kp, "PENDING") == "PENDING":
            reasons.add(StudyReason.REVIEW_PENDING)
        if validation in ("UNVERIFIED", ""):
            reasons.add(StudyReason.UNVERIFIED_KNOWLEDGE)
        ordered = tuple(
            r for r in _REASON_PRIORITY if r in reasons
        )
        return ordered

    # ------------------------------------------------------------------
    # planning
    # ------------------------------------------------------------------

    def build_plan(self, student_id: str) -> StudyPlan:
        """Create the immutable, explainable plan snapshot (spec 33.12).

        Items are ordered deterministically:
          1. highest-priority reason present in the item
          2. prerequisite depth of the KP (deeper / more dependent
             first)
          3. knowledge_point_id (lexicographic tie-break)
        """
        sid = str(student_id or "").strip()
        if not sid:
            raise StudyPlanValidationError(
                StudyPlanErrorCode.INVALID_INPUT, "student_id must be non-empty"
            )
        self._cycle_and_depths()
        items: list = []
        for kp in sorted(self._kp_ids):
            reasons = self._reasons_for(kp)
            if not reasons:
                continue
            items.append(
                StudyItem(
                    knowledge_point_id=kp,
                    reason_codes=reasons,
                    prerequisite_ids=self._prerequisites.get(kp, ()),
                )
            )
        items.sort(
            key=lambda it: (
                _REASON_RANK.get(it.reason_codes[0], len(_REASON_RANK))
                if it.reason_codes
                else len(_REASON_RANK),
                -self.prerequisite_depth(it.knowledge_point_id),
                it.knowledge_point_id,
            )
        )
        payload = {
            "student_id": sid,
            "course_id": self._course_id,
            "items": [i.to_dict() for i in items],
            "rules_version": RULES_VERSION,
        }
        return StudyPlan(
            plan_id="plan-" + _sha24(payload),
            student_id=sid,
            course_id=self._course_id,
            items=tuple(items),
            rules_version=RULES_VERSION,
        )

    # ------------------------------------------------------------------
    # learning path (spec 33.14-33.18)
    # ------------------------------------------------------------------

    def build_learning_path(self, target_kp: str) -> LearningPath:
        """Return the PREREQUISITE-only chain ending at ``target_kp``.

        - cycle anywhere in the target's prerequisite closure ->
          status DEPENDENCY_CYCLE with the cycle ids, node_ids empty.
        - the target itself must exist in the course, otherwise
          status UNKNOWN_KNOWLEDGE_POINT.
        - the path is root-first (prerequisites before dependents);
          it includes uncovered prerequisites (spec 33.17) and
          covered prerequisites alike (spec 33.18 -- inclusion is a
          structural fact, never a "mastered" claim).
        """
        target = str(target_kp or "")
        if target not in self._kp_ids:
            return LearningPath(
                status=PathStatus.UNKNOWN_KNOWLEDGE_POINT,
                target_knowledge_point_id=target,
                node_ids=(),
                cycle_node_ids=(),
            )
        self._cycle_and_depths()
        if self._cycle is not None:
            return LearningPath(
                status=PathStatus.DEPENDENCY_CYCLE,
                target_knowledge_point_id=target,
                node_ids=(),
                cycle_node_ids=self._cycle_members,
            )
        chain: list = []
        visited: set = set()
        frontier = [target]
        while frontier:
            current = frontier.pop()
            prereqs = self._prerequisites.get(current, ())
            for p in prereqs:
                if p in visited:
                    continue
                visited.add(p)
                chain.append(p)
                frontier.append(p)
        chain.append(target)
        # reverse postorder gives dependency depth order (roots first)
        order: Dict[str, int] = {}
        for kp in sorted(self._kp_ids):
            order[kp] = self.prerequisite_depth(kp)
        chain.sort(key=lambda k: (order.get(k, -1), k))
        return LearningPath(
            status=PathStatus.OK,
            target_knowledge_point_id=target,
            node_ids=tuple(chain),
            cycle_node_ids=(),
        )
