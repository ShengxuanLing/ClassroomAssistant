"""Student & Learning State Layer (Task 30).

Introduces the first ``Student``-facing layer on top of the course
knowledge layer, with strict data-boundary guarantees:

- A ``Student`` is an identity-only record (``student_id`` is
  caller-supplied; no auth / account / email / permissions).
- ``LearningState`` tracks *behavioral* state (NOT_STARTED, EXPOSED,
  PRACTICING, REVIEWING).  There is intentionally NO "mastered" /
  "weak" state -- mastery inference is out of scope.
- ``LearningEvent`` is an append-only, idempotent, deterministic
  log entry (``sequence`` is an integer activity counter, not a
  wall-clock timestamp).
- ``StudentKnowledgeRecord`` is a *derived* snapshot of the event
  log for one (student, course, knowledge_point) triple; the event
  log remains the source of truth.

Data-boundary rules (spec section 6 / 7)
----------------------------------------
- Student state NEVER mutates ``KnowledgePoint`` fields.
- Course isolation: the same ``knowledge_point_id`` in two
  different courses is tracked independently.
- Student isolation: one student's events never affect another's.

Determinism
-----------
- No ``uuid4()``, no ``datetime.now()``, no ``random``, no
  builtin ``hash()``.
- All business IDs are ``"event-" + sha256(student|course|kp|type|seq)[:24]``.
- All new entities are ``frozen=True`` dataclasses with
  ``to_dict`` / ``from_dict`` and a stable ``schema_version``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, FrozenSet, Iterable, Mapping, Optional, Tuple

__all__ = [
    "STUDENT_LEARNING_SCHEMA_VERSION",
    "StudentLearningErrorCode",
    "StudentLearningError",
    "StudentLearningValidationError",
    "StudentLearningSchemaError",
    "Student",
    "LearningState",
    "LearningEventType",
    "LearningEvent",
    "StudentKnowledgeRecord",
    "StudentLearningLog",
    "reduce_events",
]

STUDENT_LEARNING_SCHEMA_VERSION = 1


class StudentLearningErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    UNKNOWN_EVENT_TYPE = "unknown_event_type"
    INVALID_SEQUENCE = "invalid_sequence"
    KNOWLEDGE_POINT_NOT_REGISTERED = "knowledge_point_not_registered"
    INVALID_SCHEMA_VERSION = "invalid_schema_version"
    INVALID_STATE = "invalid_state"


class StudentLearningError(Exception):
    def __init__(self, code: StudentLearningErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code.value}] {message}")


class StudentLearningValidationError(StudentLearningError):
    pass


class StudentLearningSchemaError(StudentLearningError):
    pass


def _sha24(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class Student:
    student_id: str
    display_name: Optional[str] = None

    @classmethod
    def create(cls, student_id: str, display_name: Optional[str] = None) -> "Student":
        sid = str(student_id or "").strip()
        if not sid:
            raise StudentLearningValidationError(
                StudentLearningErrorCode.INVALID_INPUT,
                "student_id must be a non-empty string",
            )
        return cls(student_id=sid, display_name=display_name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": STUDENT_LEARNING_SCHEMA_VERSION,
            "student_id": self.student_id,
            "display_name": self.display_name,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Student":
        sv = data.get("schema_version")
        if sv != STUDENT_LEARNING_SCHEMA_VERSION:
            raise StudentLearningSchemaError(
                StudentLearningErrorCode.INVALID_SCHEMA_VERSION,
                f"unsupported schema_version: {sv!r}",
            )
        return cls(
            student_id=str(data.get("student_id") or ""),
            display_name=data.get("display_name"),
        )


class LearningState(str, Enum):
    NOT_STARTED = "not_started"
    EXPOSED = "exposed"
    PRACTICING = "practicing"
    REVIEWING = "reviewing"


class LearningEventType(str, Enum):
    VIEWED = "viewed"
    PRACTICED = "practiced"
    ANSWERED = "answered"
    REVIEWED = "reviewed"


_TRANSITIONS: Dict[LearningState, FrozenSet[LearningEventType]] = {
    LearningState.NOT_STARTED: frozenset({LearningEventType.VIEWED}),
    LearningState.EXPOSED: frozenset({LearningEventType.PRACTICED}),
    LearningState.PRACTICING: frozenset({LearningEventType.REVIEWED}),
    LearningState.REVIEWING: frozenset(),
}

_STATE_FOR_EVENT: Dict[LearningEventType, LearningState] = {
    LearningEventType.VIEWED: LearningState.EXPOSED,
    LearningEventType.PRACTICED: LearningState.PRACTICING,
    LearningEventType.REVIEWED: LearningState.REVIEWING,
}


def _event_target_state(event_type: LearningEventType) -> Optional[LearningState]:
    return _STATE_FOR_EVENT.get(event_type)


def _legal_from(current: LearningState, event_type: LearningEventType) -> bool:
    return event_type in _TRANSITIONS.get(current, frozenset())


@dataclass(frozen=True)
class LearningEvent:
    event_id: str
    student_id: str
    course_id: str
    knowledge_point_id: str
    event_type: LearningEventType
    sequence: int

    @classmethod
    def create(
        cls,
        student_id: str,
        course_id: str,
        knowledge_point_id: str,
        event_type: LearningEventType,
        sequence: int,
    ) -> "LearningEvent":
        sid = str(student_id or "").strip()
        cid = str(course_id or "").strip()
        kpid = str(knowledge_point_id or "").strip()
        if not sid or not cid or not kpid:
            raise StudentLearningValidationError(
                StudentLearningErrorCode.INVALID_INPUT,
                "student_id, course_id and knowledge_point_id must be non-empty",
            )
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise StudentLearningValidationError(
                StudentLearningErrorCode.INVALID_SEQUENCE,
                "sequence must be a non-negative integer",
            )
        if not isinstance(event_type, LearningEventType):
            raise StudentLearningValidationError(
                StudentLearningErrorCode.UNKNOWN_EVENT_TYPE,
                f"unknown event type: {event_type!r}",
            )
        payload = f"{sid}|{cid}|{kpid}|{event_type.value}|{sequence}"
        return cls(
            event_id="event-" + _sha24(payload),
            student_id=sid,
            course_id=cid,
            knowledge_point_id=kpid,
            event_type=event_type,
            sequence=sequence,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "student_id": self.student_id,
            "course_id": self.course_id,
            "knowledge_point_id": self.knowledge_point_id,
            "event_type": self.event_type.value,
            "sequence": self.sequence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LearningEvent":
        et_value = str(data.get("event_type") or "")
        try:
            et = LearningEventType(et_value)
        except ValueError:
            raise StudentLearningValidationError(
                StudentLearningErrorCode.UNKNOWN_EVENT_TYPE,
                f"unknown event_type value: {et_value!r}",
            )
        rebuilt = cls.create(
            student_id=str(data.get("student_id") or ""),
            course_id=str(data.get("course_id") or ""),
            knowledge_point_id=str(data.get("knowledge_point_id") or ""),
            event_type=et,
            sequence=int(data.get("sequence") or 0),
        )
        stored = str(data.get("event_id") or "")
        if stored and stored != rebuilt.event_id:
            raise StudentLearningValidationError(
                StudentLearningErrorCode.INVALID_STATE,
                "event_id does not match content (corrupted payload)",
            )
        return rebuilt


@dataclass(frozen=True)
class StudentKnowledgeRecord:
    student_id: str
    course_id: str
    knowledge_point_id: str
    state: LearningState
    first_seen_at: int
    last_activity_at: int
    exposure_count: int
    practice_count: int
    answer_count: int
    correct_count: int
    incorrect_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": STUDENT_LEARNING_SCHEMA_VERSION,
            "student_id": self.student_id,
            "course_id": self.course_id,
            "knowledge_point_id": self.knowledge_point_id,
            "state": self.state.value,
            "first_seen_at": self.first_seen_at,
            "last_activity_at": self.last_activity_at,
            "exposure_count": self.exposure_count,
            "practice_count": self.practice_count,
            "answer_count": self.answer_count,
            "correct_count": self.correct_count,
            "incorrect_count": self.incorrect_count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StudentKnowledgeRecord":
        sv = data.get("schema_version")
        if sv != STUDENT_LEARNING_SCHEMA_VERSION:
            raise StudentLearningSchemaError(
                StudentLearningErrorCode.INVALID_SCHEMA_VERSION,
                f"unsupported schema_version: {sv!r}",
            )
        state_value = str(data.get("state") or LearningState.NOT_STARTED.value)
        try:
            state = LearningState(state_value)
        except ValueError:
            raise StudentLearningValidationError(
                StudentLearningErrorCode.INVALID_STATE,
                f"unknown LearningState value: {state_value!r}",
            )
        return cls(
            student_id=str(data.get("student_id") or ""),
            course_id=str(data.get("course_id") or ""),
            knowledge_point_id=str(data.get("knowledge_point_id") or ""),
            state=state,
            first_seen_at=int(data.get("first_seen_at", 0)),
            last_activity_at=int(data.get("last_activity_at", 0)),
            exposure_count=int(data.get("exposure_count", 0)),
            practice_count=int(data.get("practice_count", 0)),
            answer_count=int(data.get("answer_count", 0)),
            correct_count=int(data.get("correct_count", 0)),
            incorrect_count=int(data.get("incorrect_count", 0)),
        )


def reduce_events(events: Iterable[LearningEvent]) -> StudentKnowledgeRecord:
    ordered = sorted(events, key=lambda e: (e.sequence, e.event_id))
    if not ordered:
        return StudentKnowledgeRecord(
            student_id="",
            course_id="",
            knowledge_point_id="",
            state=LearningState.NOT_STARTED,
            first_seen_at=0,
            last_activity_at=0,
            exposure_count=0,
            practice_count=0,
            answer_count=0,
            correct_count=0,
            incorrect_count=0,
        )

    state = LearningState.NOT_STARTED
    exposure_count = 0
    practice_count = 0
    answer_count = 0

    for event in ordered:
        target = _event_target_state(event.event_type)
        if target is None:
            answer_count += 1
            continue
        if _legal_from(state, event.event_type):
            state = target
            if event.event_type is LearningEventType.VIEWED:
                exposure_count += 1
            elif event.event_type is LearningEventType.PRACTICED:
                practice_count += 1

    return StudentKnowledgeRecord(
        student_id=ordered[0].student_id,
        course_id=ordered[0].course_id,
        knowledge_point_id=ordered[0].knowledge_point_id,
        state=state,
        first_seen_at=ordered[0].sequence,
        last_activity_at=ordered[-1].sequence,
        exposure_count=exposure_count,
        practice_count=practice_count,
        answer_count=answer_count,
        correct_count=0,
        incorrect_count=0,
    )


class StudentLearningLog:
    """Append-only, idempotent event log scoped to one student.

    Course isolation: events are keyed by (course_id, kp_id) so the
    same KP id in two courses never cross-contaminate.  Idempotency:
    re-adding an already-stored event (same deterministic identity)
    is a no-op.  The log never mutates any KnowledgePoint instance.
    """

    def __init__(self, student: Student) -> None:
        self._student = student
        self._events: Dict[Tuple[str, str], list] = {}
        self._seen_event_ids: Dict[Tuple[str, str], set] = {}
        self._next_seq: Dict[Tuple[str, str], int] = {}
        self._registered_kps: FrozenSet[Tuple[str, str]] = frozenset()
        self._correct: Dict[Tuple[str, str], int] = {}
        self._incorrect: Dict[Tuple[str, str], int] = {}

    @property
    def student(self) -> Student:
        return self._student

    @property
    def registered_knowledge_points(self) -> FrozenSet[Tuple[str, str]]:
        return self._registered_kps

    def register_knowledge_point(self, course_id: str, knowledge_point_id: str) -> None:
        key = (course_id, knowledge_point_id)
        self._registered_kps = self._registered_kps | {key}

    def record_event(
        self,
        course_id: str,
        knowledge_point_id: str,
        event_type: LearningEventType,
        *,
        is_correct: Optional[bool] = None,
        event: Optional[LearningEvent] = None,
    ) -> LearningEvent:
        key = (course_id, knowledge_point_id)
        if key not in self._registered_kps:
            raise StudentLearningValidationError(
                StudentLearningErrorCode.KNOWLEDGE_POINT_NOT_REGISTERED,
                f"knowledge point {knowledge_point_id!r} not registered "
                f"for course {course_id!r}",
            )
        if event is not None:
            if (
                event.student_id != self._student.student_id
                or event.course_id != course_id
                or event.knowledge_point_id != knowledge_point_id
                or event.event_type is not event_type
            ):
                raise StudentLearningValidationError(
                    StudentLearningErrorCode.INVALID_STATE,
                    "explicit event does not match (student, course, kp, type)",
                )
            duplicates = self._seen_event_ids.setdefault(key, set())
            if event.event_id in duplicates:
                for stored in self._events.get(key, []):
                    if stored.event_id == event.event_id:
                        return stored
                return event
            self._events.setdefault(key, []).append(event)
            duplicates.add(event.event_id)
            self._next_seq[key] = max(self._next_seq.get(key, 0), event.sequence + 1)
        else:
            seq = self._next_seq.get(key, 0)
            event = LearningEvent.create(
                self._student.student_id,
                course_id,
                knowledge_point_id,
                event_type,
                seq,
            )
            self._events.setdefault(key, []).append(event)
            self._seen_event_ids.setdefault(key, set()).add(event.event_id)
            self._next_seq[key] = seq + 1
        if event_type is LearningEventType.ANSWERED and is_correct is not None:
            if is_correct:
                self._correct[key] = self._correct.get(key, 0) + 1
            else:
                self._incorrect[key] = self._incorrect.get(key, 0) + 1
        return event

    def record_answer(
        self,
        course_id: str,
        knowledge_point_id: str,
        *,
        is_correct: Optional[bool],
    ) -> LearningEvent:
        return self.record_event(
            course_id,
            knowledge_point_id,
            LearningEventType.ANSWERED,
            is_correct=is_correct,
        )

    def events_for(
        self, course_id: str, knowledge_point_id: str
    ) -> Tuple[LearningEvent, ...]:
        return tuple(self._events.get((course_id, knowledge_point_id), ()))

    def all_events(self) -> Tuple[LearningEvent, ...]:
        out: list = []
        for key in sorted(self._events.keys()):
            out.extend(self._events[key])
        return tuple(out)

    def derive_state(
        self, course_id: str, knowledge_point_id: str
    ) -> StudentKnowledgeRecord:
        events = self.events_for(course_id, knowledge_point_id)
        key = (course_id, knowledge_point_id)
        if not events:
            return StudentKnowledgeRecord(
                student_id=self._student.student_id,
                course_id=course_id,
                knowledge_point_id=knowledge_point_id,
                state=LearningState.NOT_STARTED,
                first_seen_at=0,
                last_activity_at=0,
                exposure_count=0,
                practice_count=0,
                answer_count=0,
                correct_count=0,
                incorrect_count=0,
            )
        base = reduce_events(events)
        return StudentKnowledgeRecord(
            student_id=base.student_id,
            course_id=base.course_id,
            knowledge_point_id=base.knowledge_point_id,
            state=base.state,
            first_seen_at=base.first_seen_at,
            last_activity_at=base.last_activity_at,
            exposure_count=base.exposure_count,
            practice_count=base.practice_count,
            answer_count=base.answer_count,
            correct_count=self._correct.get(key, 0),
            incorrect_count=self._incorrect.get(key, 0),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": STUDENT_LEARNING_SCHEMA_VERSION,
            "student": self._student.to_dict(),
            "events": [e.to_dict() for e in self.all_events()],
            "registered_kps": [f"{c}|{k}" for c, k in sorted(self._registered_kps)],
            "correct_counts": {
                f"{c}|{k}": v
                for c, k, v in sorted((c, k, v) for (c, k), v in self._correct.items())
            },
            "incorrect_counts": {
                f"{c}|{k}": v
                for c, k, v in sorted((c, k, v) for (c, k), v in self._incorrect.items())
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StudentLearningLog":
        sv = data.get("schema_version")
        if sv != STUDENT_LEARNING_SCHEMA_VERSION:
            raise StudentLearningSchemaError(
                StudentLearningErrorCode.INVALID_SCHEMA_VERSION,
                f"unsupported schema_version: {sv!r}",
            )
        student = Student.from_dict(data.get("student") or {})
        log = cls(student)
        for pair in data.get("registered_kps") or ():
            c, k = pair.split("|", 1)
            log._registered_kps = log._registered_kps | {(c, k)}
        for e in data.get("events") or ():
            event = LearningEvent.from_dict(e)
            key = (event.course_id, event.knowledge_point_id)
            log._events.setdefault(key, []).append(event)
            log._seen_event_ids.setdefault(key, set()).add(event.event_id)
            log._next_seq[key] = max(log._next_seq.get(key, 0), event.sequence + 1)
        for pair, count in (data.get("correct_counts") or {}).items():
            c, k = pair.split("|", 1)
            log._correct[(c, k)] = int(count)
        for pair, count in (data.get("incorrect_counts") or {}).items():
            c, k = pair.split("|", 1)
            log._incorrect[(c, k)] = int(count)
        return log

    def __len__(self) -> int:
        return sum(len(v) for v in self._events.values())

    def __repr__(self) -> str:
        return (
            f"StudentLearningLog(student={self._student.student_id!r}, "
            f"events={len(self)})"
        )
