"""Tests for src/student_learning.py (Task 30: Student & Learning State Layer).

Covers spec 30.15:
- student creation
- deterministic identity
- event append
- event idempotency
- state transitions
- invalid transition
- course isolation
- KP isolation
- multiple students
- multiple courses
- serialization
- replay
- deterministic state derivation
- no mutation of KnowledgePoint
"""

from __future__ import annotations

import json

import pytest

from src.student_learning import (
    STUDENT_LEARNING_SCHEMA_VERSION,
    LearningEvent,
    LearningEventType,
    LearningState,
    Student,
    StudentKnowledgeRecord,
    StudentLearningErrorCode,
    StudentLearningLog,
    StudentLearningSchemaError,
    StudentLearningValidationError,
    reduce_events,
)


# ---------------------------------------------------------------------------
# Student
# ---------------------------------------------------------------------------


def test_student_creation_with_display_name() -> None:
    s = Student.create("S001", display_name="Ada")
    assert s.student_id == "S001"
    assert s.display_name == "Ada"


def test_student_creation_without_display_name() -> None:
    s = Student.create("S002")
    assert s.display_name is None


def test_student_rejects_empty_id() -> None:
    with pytest.raises(StudentLearningValidationError) as excinfo:
        Student.create("   ")
    assert excinfo.value.code is StudentLearningErrorCode.INVALID_INPUT


def test_student_id_not_derived_from_name() -> None:
    """Same name, different ids -> different students; ids are caller-supplied."""
    a = Student.create("STU-A", display_name="Maria")
    b = Student.create("STU-B", display_name="Maria")
    assert a.student_id != b.student_id


def test_student_is_frozen() -> None:
    s = Student.create("S1")
    with pytest.raises(AttributeError):
        s.display_name = "changed"


def test_student_serialization_round_trip() -> None:
    s = Student.create("S001", display_name="林 太郎")
    restored = Student.from_dict(json.loads(json.dumps(s.to_dict())))
    assert restored == s
    assert restored.display_name == "林 太郎"


def test_student_from_dict_rejects_unknown_schema_version() -> None:
    payload = Student.create("S1").to_dict()
    payload["schema_version"] = 999
    with pytest.raises(StudentLearningSchemaError) as excinfo:
        Student.from_dict(payload)
    assert excinfo.value.code is StudentLearningErrorCode.INVALID_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# LearningEvent
# ---------------------------------------------------------------------------


def test_event_id_is_deterministic() -> None:
    a = LearningEvent.create("S1", "C1", "KP1", LearningEventType.VIEWED, 0)
    b = LearningEvent.create("S1", "C1", "KP1", LearningEventType.VIEWED, 0)
    assert a.event_id == b.event_id
    assert a.event_id.startswith("event-")
    assert len(a.event_id) == len("event-") + 24


def test_event_id_differs_per_component() -> None:
    base = LearningEvent.create("S1", "C1", "KP1", LearningEventType.VIEWED, 0)
    assert base.event_id != LearningEvent.create("S2", "C1", "KP1", LearningEventType.VIEWED, 0).event_id
    assert base.event_id != LearningEvent.create("S1", "C2", "KP1", LearningEventType.VIEWED, 0).event_id
    assert base.event_id != LearningEvent.create("S1", "C1", "KP2", LearningEventType.VIEWED, 0).event_id
    assert base.event_id != LearningEvent.create("S1", "C1", "KP1", LearningEventType.PRACTICED, 0).event_id
    assert base.event_id != LearningEvent.create("S1", "C1", "KP1", LearningEventType.VIEWED, 1).event_id


def test_event_rejects_invalid_inputs() -> None:
    with pytest.raises(StudentLearningValidationError):
        LearningEvent.create("", "C1", "KP1", LearningEventType.VIEWED, 0)
    with pytest.raises(StudentLearningValidationError):
        LearningEvent.create("S1", "C1", "KP1", LearningEventType.VIEWED, -1)
    with pytest.raises(StudentLearningValidationError):
        LearningEvent.create("S1", "C1", "KP1", LearningEventType.VIEWED, True)
    with pytest.raises(StudentLearningValidationError):
        LearningEvent.create("S1", "C1", "KP1", "not_a_type", 0)


def test_event_serialization_round_trip() -> None:
    e = LearningEvent.create("S1", "C1", "KP1", LearningEventType.REVIEWED, 7)
    restored = LearningEvent.from_dict(json.loads(json.dumps(e.to_dict())))
    assert restored == e
    assert restored.event_id == e.event_id


def test_event_from_dict_rejects_tampered_id() -> None:
    e = LearningEvent.create("S1", "C1", "KP1", LearningEventType.VIEWED, 0)
    payload = e.to_dict()
    payload["event_id"] = "event-" + "f" * 24
    with pytest.raises(StudentLearningValidationError) as excinfo:
        LearningEvent.from_dict(payload)
    assert excinfo.value.code is StudentLearningErrorCode.INVALID_STATE


# ---------------------------------------------------------------------------
# State machine / reducer
# ---------------------------------------------------------------------------


def ev(student: str, course: str, kp: str, t: LearningEventType, seq: int) -> LearningEvent:
    return LearningEvent.create(student, course, kp, t, seq)


def test_full_legal_progression() -> None:
    events = [
        ev("S1", "C1", "KP1", LearningEventType.VIEWED, 0),
        ev("S1", "C1", "KP1", LearningEventType.PRACTICED, 1),
        ev("S1", "C1", "KP1", LearningEventType.REVIEWED, 2),
    ]
    record = reduce_events(events)
    assert record.state is LearningState.REVIEWING
    assert record.exposure_count == 1
    assert record.practice_count == 1
    assert record.first_seen_at == 0
    assert record.last_activity_at == 2


def test_invalid_transition_rejected_state_unchanged() -> None:
    # Skipping PRACTICED: PRACTICED is illegal from NOT_STARTED,
    # REVIEWED is illegal from EXPOSED.
    events = [
        ev("S1", "C1", "KP1", LearningEventType.VIEWED, 0),
        ev("S1", "C1", "KP1", LearningEventType.REVIEWED, 1),
        ev("S1", "C1", "KP1", LearningEventType.PRACTICED, 2),
    ]
    record = reduce_events(events)
    # VIEWED -> EXPOSED; REVIEWED illegal from EXPOSED; PRACTICED legal from EXPOSED -> PRACTICING.
    assert record.state is LearningState.PRACTICING
    assert record.exposure_count == 1
    assert record.practice_count == 1


def test_regression_transition_rejected() -> None:
    # After reaching REVIEWING (terminal), any event is illegal.
    events = [
        ev("S1", "C1", "KP1", LearningEventType.VIEWED, 0),
        ev("S1", "C1", "KP1", LearningEventType.PRACTICED, 1),
        ev("S1", "C1", "KP1", LearningEventType.REVIEWED, 2),
        ev("S1", "C1", "KP1", LearningEventType.VIEWED, 3),
    ]
    record = reduce_events(events)
    assert record.state is LearningState.REVIEWING
    assert record.exposure_count == 1  # only the first VIEWED counted


def test_answered_does_not_advance_state() -> None:
    events = [
        ev("S1", "C1", "KP1", LearningEventType.VIEWED, 0),
        ev("S1", "C1", "KP1", LearningEventType.ANSWERED, 1),
        ev("S1", "C1", "KP1", LearningEventType.ANSWERED, 2),
    ]
    record = reduce_events(events)
    assert record.state is LearningState.EXPOSED
    assert record.answer_count == 2


def test_reducer_is_deterministic_under_shuffled_input() -> None:
    events = [
        ev("S1", "C1", "KP1", LearningEventType.REVIEWED, 2),
        ev("S1", "C1", "KP1", LearningEventType.VIEWED, 0),
        ev("S1", "C1", "KP1", LearningEventType.PRACTICED, 1),
    ]
    r1 = reduce_events(events)
    r2 = reduce_events(list(reversed(events)))
    assert r1.state == r2.state
    assert r1.first_seen_at == r2.first_seen_at
    assert r1.last_activity_at == r2.last_activity_at


def test_reduce_empty_events() -> None:
    record = reduce_events([])
    assert record.state is LearningState.NOT_STARTED
    assert record.exposure_count == 0


def test_state_enumeration_has_no_mastery_states() -> None:
    values = {s.value for s in LearningState}
    assert values == {"not_started", "exposed", "practicing", "reviewing"}


# ---------------------------------------------------------------------------
# StudentLearningLog
# ---------------------------------------------------------------------------


def make_log() -> StudentLearningLog:
    log = StudentLearningLog(Student.create("S1"))
    log.register_knowledge_point("C1", "KP1")
    log.register_knowledge_point("C1", "KP2")
    log.register_knowledge_point("C2", "KP1")
    return log


def test_log_rejects_unregistered_knowledge_point() -> None:
    log = StudentLearningLog(Student.create("S1"))
    with pytest.raises(StudentLearningValidationError) as excinfo:
        log.record_event("C1", "KPX", LearningEventType.VIEWED)
    assert excinfo.value.code is StudentLearningErrorCode.KNOWLEDGE_POINT_NOT_REGISTERED


def test_event_append_and_sequence_progression() -> None:
    log = make_log()
    e0 = log.record_event("C1", "KP1", LearningEventType.VIEWED)
    e1 = log.record_event("C1", "KP1", LearningEventType.PRACTICED)
    e2 = log.record_event("C1", "KP1", LearningEventType.REVIEWED)
    assert (e0.sequence, e1.sequence, e2.sequence) == (0, 1, 2)
    assert log.derive_state("C1", "KP1").state is LearningState.REVIEWING


def test_event_idempotency_explicit_duplicate() -> None:
    log = make_log()
    e0 = log.record_event("C1", "KP1", LearningEventType.VIEWED)
    # Re-adding the exact same event: no-op, sequence does not advance.
    again = log.record_event("C1", "KP1", LearningEventType.VIEWED, event=e0)
    assert again == e0
    assert len(log.events_for("C1", "KP1")) == 1
    # A NEW auto-assigned event still advances the sequence.
    e1 = log.record_event("C1", "KP1", LearningEventType.PRACTICED)
    assert e1.sequence == 1
    assert len(log.events_for("C1", "KP1")) == 2


def test_explicit_event_mismatch_rejected() -> None:
    log = make_log()
    foreign = LearningEvent.create("S9", "C1", "KP1", LearningEventType.VIEWED, 0)
    with pytest.raises(StudentLearningValidationError) as excinfo:
        log.record_event("C1", "KP1", LearningEventType.VIEWED, event=foreign)
    assert excinfo.value.code is StudentLearningErrorCode.INVALID_STATE


def test_course_isolation_same_kp_different_courses() -> None:
    log = make_log()
    log.record_event("C1", "KP1", LearningEventType.VIEWED)
    log.record_event("C1", "KP1", LearningEventType.PRACTICED)
    # Same KP id in C2 is independent.
    rec_c1 = log.derive_state("C1", "KP1")
    rec_c2 = log.derive_state("C2", "KP1")
    assert rec_c1.state is LearningState.PRACTICING
    assert rec_c2.state is LearningState.NOT_STARTED
    assert len(log.events_for("C2", "KP1")) == 0


def test_kp_isolation_within_course() -> None:
    log = make_log()
    log.record_event("C1", "KP1", LearningEventType.VIEWED)
    rec2 = log.derive_state("C1", "KP2")
    assert rec2.state is LearningState.NOT_STARTED


def test_multiple_students_independent() -> None:
    log_a = make_log()
    log_b = StudentLearningLog(Student.create("S2"))
    log_b.register_knowledge_point("C1", "KP1")
    log_a.record_event("C1", "KP1", LearningEventType.VIEWED)
    assert log_a.derive_state("C1", "KP1").state is LearningState.EXPOSED
    assert log_b.derive_state("C1", "KP1").state is LearningState.NOT_STARTED


def test_answer_counters_are_tracked_but_no_mastery_inferred() -> None:
    log = make_log()
    log.record_answer("C1", "KP1", is_correct=False)
    log.record_answer("C1", "KP1", is_correct=True)
    log.record_answer("C1", "KP1", is_correct=False)
    rec = log.derive_state("C1", "KP1")
    assert rec.answer_count == 3
    assert rec.correct_count == 1
    assert rec.incorrect_count == 2
    # No "mastered"/"weak" inference from the ratios.
    assert rec.state is LearningState.NOT_STARTED
    assert "mastered" not in {s.value for s in LearningState}


def test_unknown_correctness_does_not_advance_counters() -> None:
    log = make_log()
    log.record_answer("C1", "KP1", is_correct=None)
    rec = log.derive_state("C1", "KP1")
    assert rec.answer_count == 1
    assert rec.correct_count == 0
    assert rec.incorrect_count == 0


def test_replay_from_serialization_produces_same_state() -> None:
    log = make_log()
    log.record_event("C1", "KP1", LearningEventType.VIEWED)
    log.record_event("C1", "KP1", LearningEventType.PRACTICED)
    log.record_answer("C1", "KP1", is_correct=False)
    log.record_event("C2", "KP1", LearningEventType.VIEWED)

    wire = json.loads(json.dumps(log.to_dict()))
    replayed = StudentLearningLog.from_dict(wire)

    assert replayed.all_events() == log.all_events()
    for kp in ("KP1", "KP2"):
        assert replayed.derive_state("C1", kp) == log.derive_state("C1", kp)
    assert replayed.derive_state("C2", "KP1") == log.derive_state("C2", "KP1")
    # Counters survive replay.
    assert replayed.derive_state("C1", "KP1").incorrect_count == 1
    # Idempotency survives replay: next event continues the sequence.
    nxt = replayed.record_event("C1", "KP1", LearningEventType.REVIEWED)
    assert nxt.sequence == 3


def test_log_from_dict_rejects_unknown_schema_version() -> None:
    log = make_log()
    payload = log.to_dict()
    payload["schema_version"] = 42
    with pytest.raises(StudentLearningSchemaError) as excinfo:
        StudentLearningLog.from_dict(payload)
    assert excinfo.value.code is StudentLearningErrorCode.INVALID_SCHEMA_VERSION


def test_record_serialization_round_trip() -> None:
    log = make_log()
    log.record_event("C1", "KP1", LearningEventType.VIEWED)
    rec = log.derive_state("C1", "KP1")
    restored = StudentKnowledgeRecord.from_dict(json.loads(json.dumps(rec.to_dict())))
    assert restored == rec


def test_multilingual_unicode_preserved_in_serialization() -> None:
    log = StudentLearningLog(Student.create("S-学", display_name="林 太郎"))
    log.register_knowledge_point("Curso-Matemàtiques", "概念-函数")
    e = log.record_event("Curso-Matemàtiques", "概念-函数", LearningEventType.VIEWED)
    wire = json.loads(json.dumps(e.to_dict()))
    assert wire["student_id"] == "S-学"
    assert wire["course_id"] == "Curso-Matemàtiques"
    assert wire["knowledge_point_id"] == "概念-函数"
    restored = StudentLearningLog.from_dict(log.to_dict())
    assert restored.student == log.student
    assert len(restored) == 1


def test_student_layer_does_not_mutate_knowledge_point() -> None:
    from src.models import KnowledgePoint

    kp = KnowledgePoint(
        knowledge_id="KP1",
        title="Ecuación lineal",
        content="Una ecuación lineal tiene una incógnita de grado uno.",
        evidence_refs=["E1"],
        validation_status="supported",
        review_status="confirmed",
    )
    before = (
        kp.validation_status,
        kp.review_status,
        kp.content,
        kp.evidence_refs,
    )
    log = make_log()
    log.register_knowledge_point("C1", "KP1")
    log.record_event("C1", "KP1", LearningEventType.VIEWED)
    log.record_event("C1", "KP1", LearningEventType.PRACTICED)
    log.record_answer("C1", "KP1", is_correct=False)
    assert (
        kp.validation_status,
        kp.review_status,
        kp.content,
        kp.evidence_refs,
    ) == before


def test_log_is_len_and_replay_deterministic_ids() -> None:
    log = make_log()
    log.record_event("C1", "KP1", LearningEventType.VIEWED)
    log.record_event("C1", "KP2", LearningEventType.VIEWED)
    assert len(log) == 2
    ids_a = [e.event_id for e in log.all_events()]
    log2 = StudentLearningLog.from_dict(log.to_dict())
    ids_b = [e.event_id for e in log2.all_events()]
    assert ids_a == ids_b
    # Deterministic: rebuilding from scratch gives identical ids.
    log3 = make_log()
    log3.record_event("C1", "KP1", LearningEventType.VIEWED)
    log3.record_event("C1", "KP2", LearningEventType.VIEWED)
    assert [e.event_id for e in log3.all_events()] == ids_a
