# -*- coding: utf-8 -*-
"""Task 42 —— 仓储 CRUD / 排序 / 幂等 / 去重 / 关系链 测试。

每个仓储都覆盖: 存 -> 取 -> 列表顺序 -> 幂等重存 -> 删除 -> 计数。
关系型仓储额外覆盖: 有序关系链、反向查询、去重、悬挂引用被外键拒绝。
"""

import pytest

from src.answer_evaluation import EvaluationResult, EvaluationStatus, StudentAnswer
from src.evidence_store import compute_canonical_key
from src.exercises import Choice, Exercise, ExerciseType
from src.integration import ConflictRecord
from src.knowledge_organization import (
    KnowledgeMembership,
    KnowledgeRelation,
    KnowledgeRelationType,
    SessionKnowledgeMembership,
    Topic,
)
from src.knowledge_review import ReviewDecision, ReviewRecord
from src.knowledge_structure import Relationship, RelationType
from src.models import (
    ClassSession,
    Confidence,
    Course,
    Evidence,
    EvidenceType,
    KnowledgePoint,
    Language,
    SourceReference,
)
from src.persistence.errors import (
    DuplicateRecordError,
    PersistenceValidationError,
    RecordNotFoundError,
)
from src.persistence.repositories.base import LinkRepository
from src.student_learning import (
    LearningEvent,
    LearningEventType,
    LearningState,
    Student,
    StudentKnowledgeRecord,
)
from src.study_plan import LearningPath, PathStatus, StudyItem, StudyPlan, StudyReason

# ----------------------------------------------------------------------
# 构造辅助
# ----------------------------------------------------------------------


def _course(course_id="course-1", name="Álgebra", code="ALG"):
    return Course(course_id=course_id, name=name, code=code)


def _session(session_id="session-1", course_id="course-1", number=1):
    return ClassSession(
        session_id=session_id,
        course_id=course_id,
        session_number=number,
        date="2026-01-01",
        title="Tema 1",
    )


def _evidence(evidence_id="evidence-1", content="Texto original", material_id="material-1"):
    return Evidence(
        evidence_id=evidence_id,
        content=content,
        language=Language.SPANISH,
        source_reference=SourceReference(
            material_id=material_id,
            timestamp_start=1.0,
            timestamp_end=2.0,
            page=3,
            line=4,
            paragraph="p1",
        ),
        confidence=Confidence.HIGH,
        evidence_type=EvidenceType.TRANSCRIPT,
    )


def _kp(knowledge_id="kp-1", evidence_refs=()):
    return KnowledgePoint(
        knowledge_id=knowledge_id,
        title="Función",
        content="Una función es una relación.",
        evidence_refs=list(evidence_refs),
    )


def _material_record(material_id="material-1", course_id="course-1", evidence_ids=()):
    return {
        "material_id": material_id,
        "course_id": course_id,
        "session_id": None,
        "filename": "clase1.mp3",
        "extension": ".mp3",
        "size": 1024,
        "content_hash": "hash-" + material_id,
        "source_type": "audio",
        "created_at": "2026-01-01T00:00:00+00:00",
        "processing_status": "REGISTERED",
        "error": None,
        "error_detail": None,
        "warning": None,
        "retryable": False,
        "material_type": "audio",
        "language": "Spanish",
        "stored_path": f"/data/audio/{material_id}.mp3",
        "relative_path": f"audio/{material_id}.mp3",
        "evidence_ids": list(evidence_ids),
        "attempts": 0,
        "duplicate": False,
        "duplicate_of": None,
    }


def _processing_record(material_id="material-1", status="REGISTERED", attempts=0):
    return {
        "material_id": material_id,
        "processing_status": status,
        "attempts": attempts,
        "error": None,
        "error_detail": None,
        "warning": None,
        "retryable": False,
    }


def _exercise(exercise_id=None, course_id="course-1", kp_ids=("kp-1",), evidence_ids=()):
    exercise = Exercise.create(
        course_id,
        ExerciseType.MULTIPLE_CHOICE,
        "¿Qué es una función?",
        tuple(kp_ids),
        choices=(Choice("a", "Una relación"), Choice("b", "Un número")),
        correct_choice_id="a",
        evidence_ids=tuple(evidence_ids),
        difficulty=2,
    )
    return exercise


def _review_record(review_id="review-1", kp_id="kp-1", decision=ReviewDecision.CONFIRM):
    return ReviewRecord(
        review_id=review_id,
        knowledge_point_id=kp_id,
        decision=decision,
        selected_evidence_ids=("evidence-1",),
        note="ok",
    )


def _study_plan(plan_id=None, student_id="student-1", course_id="course-1"):
    item = StudyItem(
        knowledge_point_id="kp-1",
        reason_codes=(StudyReason.LOW_PRACTICE_COUNT,),
        prerequisite_ids=(),
    )
    plan = StudyPlan(
        plan_id="",
        student_id=student_id,
        course_id=course_id,
        items=(item,),
        rules_version="v1",
    )
    return plan


def _learning_path(target="kp-2", nodes=("kp-1", "kp-2")):
    return LearningPath(
        status=PathStatus.OK,
        target_knowledge_point_id=target,
        node_ids=tuple(nodes),
        cycle_node_ids=(),
    )


# ======================================================================
# CourseRepository
# ======================================================================


def test_course_save_and_load(repos):
    repos.courses.save(_course())
    loaded = repos.courses.load("course-1")
    assert loaded is not None
    assert loaded.name == "Álgebra"
    assert loaded.code == "ALG"


def test_course_load_missing_returns_none(repos):
    assert repos.courses.load("nope") is None


def test_course_require_missing_raises(repos):
    with pytest.raises(RecordNotFoundError):
        repos.courses.require("nope")


def test_course_save_is_idempotent(repos):
    repos.courses.save(_course())
    repos.courses.save(_course())
    assert repos.courses.count() == 1


def test_course_save_updates_the_same_row(repos):
    repos.courses.save(_course(name="Old"))
    repos.courses.save(_course(name="New"))
    assert repos.courses.count() == 1
    assert repos.courses.load("course-1").name == "New"


def test_course_keys_are_sorted(repos):
    for cid in ("c", "a", "b"):
        repos.courses.save(_course(course_id=cid))
    assert repos.courses.keys() == ["a", "b", "c"]


def test_course_load_all_returns_domain_objects(repos):
    repos.courses.save(_course())
    loaded = repos.courses.load_all()
    assert [c.course_id for c in loaded] == ["course-1"]
    assert isinstance(loaded[0], Course)


def test_course_delete(repos):
    repos.courses.save(_course())
    assert repos.courses.delete("course-1") is True
    assert repos.courses.exists("course-1") is False


def test_course_delete_missing_returns_false(repos):
    assert repos.courses.delete("nope") is False


def test_course_exists(repos):
    repos.courses.save(_course())
    assert repos.courses.exists("course-1") is True
    assert repos.courses.exists("other") is False


def test_course_rejects_wrong_type(repos):
    with pytest.raises(TypeError):
        repos.courses.save("not a course")


def test_course_save_many(repos):
    assert repos.courses.save_many([_course(course_id="a"), _course(course_id="b")]) == 2
    assert repos.courses.count() == 2


def test_course_language_survives_roundtrip(repos):
    course = _course()
    course.language = Language.CATALAN
    repos.courses.save(course)
    assert repos.courses.load("course-1").language is Language.CATALAN


def test_course_metadata_survives_roundtrip(repos):
    course = _course()
    course.metadata = {"términos": ["funció", "变量"], "n": 3}
    repos.courses.save(course)
    assert repos.courses.load("course-1").metadata == {
        "términos": ["funció", "变量"],
        "n": 3,
    }


# ======================================================================
# SessionRepository
# ======================================================================


def test_session_save_and_load(repos):
    repos.courses.save(_course())
    repos.sessions.save(_session())
    loaded = repos.sessions.load("session-1")
    assert loaded.title == "Tema 1"
    assert loaded.session_number == 1


def test_session_requires_its_course_to_exist(repos):
    with pytest.raises(PersistenceValidationError):
        repos.sessions.save(_session())


def test_session_load_for_course_is_ordered_by_number(repos):
    repos.courses.save(_course())
    for number in (3, 1, 2):
        repos.sessions.save(
            _session(session_id=f"session-{number}", number=number)
        )
    numbers = [s.session_number for s in repos.sessions.load_for_course("course-1")]
    assert numbers == [1, 2, 3]


def test_session_load_for_course_filters(repos):
    repos.courses.save(_course(course_id="course-1"))
    repos.courses.save(_course(course_id="course-2"))
    repos.sessions.save(_session(session_id="s1", course_id="course-1"))
    repos.sessions.save(_session(session_id="s2", course_id="course-2"))
    assert len(repos.sessions.load_for_course("course-1")) == 1


def test_session_refs_survive_roundtrip(repos):
    repos.courses.save(_course())
    session = _session()
    session.material_refs = ["material-1"]
    session.evidence_refs = ["evidence-1"]
    session.knowledge_point_refs = ["kp-1"]
    repos.sessions.save(session)
    loaded = repos.sessions.load("session-1")
    assert loaded.material_refs == ["material-1"]
    assert loaded.evidence_refs == ["evidence-1"]
    assert loaded.knowledge_point_refs == ["kp-1"]


def test_deleting_a_course_cascades_to_its_sessions(repos):
    repos.courses.save(_course())
    repos.sessions.save(_session())
    repos.courses.delete("course-1")
    assert repos.sessions.count() == 0


# ======================================================================
# MaterialRepository
# ======================================================================


def test_material_save_and_load_record(repos):
    repos.materials.save_record(_material_record())
    record = repos.materials.load_record("material-1")
    assert record["filename"] == "clase1.mp3"
    assert record["content_hash"] == "hash-material-1"


def test_material_record_is_byte_identical_after_roundtrip(repos):
    record = _material_record()
    repos.materials.save_record(record)
    assert repos.materials.load_record("material-1") == record


def test_material_load_records_filters_by_course(repos):
    repos.materials.save_record(_material_record("m1", "course-1"))
    repos.materials.save_record(_material_record("m2", "course-2"))
    assert len(repos.materials.load_records(course_id="course-1")) == 1


def test_material_domain_mapping(repos):
    repos.materials.save_record(_material_record())
    material = repos.materials.load_material("material-1")
    assert material.filename == "clase1.mp3"
    assert material.language is Language.SPANISH
    assert material.path.endswith("material-1.mp3")


def test_material_save_rejects_missing_id(repos):
    record = _material_record()
    record["material_id"] = None
    with pytest.raises(ValueError):
        repos.materials.save_record(record)


def test_material_duplicate_filename_and_hash_is_rejected(repos):
    """(课程, 文件名, 内容哈希) 唯一 —— 数据库级去重。"""
    repos.materials.save_record(_material_record("m1"))
    second = _material_record("m2")
    second["content_hash"] = "hash-m1"
    second["filename"] = "clase1.mp3"
    with pytest.raises(DuplicateRecordError):
        repos.materials.save_record(second)


def test_material_evidence_links_are_ordered(repos):
    repos.evidence.save(_evidence("e1"))
    repos.evidence.save(_evidence("e2", content="Otro", material_id="material-1"))
    repos.materials.save_record(_material_record("material-1"))
    repos.materials.replace_evidence("material-1", ["e2", "e1"])
    assert repos.materials.evidence_ids_for("material-1") == ["e2", "e1"]


def test_material_evidence_reverse_lookup(repos):
    repos.evidence.save(_evidence("e1"))
    repos.materials.save_record(_material_record("material-1"))
    repos.materials.replace_evidence("material-1", ["e1"])
    assert repos.materials.material_ids_for_evidence("e1") == ["material-1"]


def test_material_evidence_link_requires_existing_evidence(repos):
    repos.materials.save_record(_material_record("material-1"))
    with pytest.raises(PersistenceValidationError):
        repos.materials.replace_evidence("material-1", ["ghost"])


def test_material_snapshot_shape(repos):
    repos.materials.save_record(_material_record("material-1"))
    snapshot = repos.materials.snapshot(course_id="course-1")
    assert snapshot["schema_version"] == 1
    assert len(snapshot["materials"]) == 1


# ======================================================================
# MaterialProcessingRepository
# ======================================================================


def test_processing_save_and_load(repos):
    repos.materials.save_record(_material_record("material-1"))
    repos.material_processing.save(_processing_record("material-1"))
    loaded = repos.material_processing.load("material-1")
    assert loaded["processing_status"] == "REGISTERED"


def test_processing_status_of(repos):
    repos.materials.save_record(_material_record("material-1"))
    repos.material_processing.save(_processing_record("material-1", "PROCESSED"))
    assert repos.material_processing.status_of("material-1") == "PROCESSED"


def test_processing_ids_with_status(repos):
    repos.materials.save_record(_material_record("m1"))
    repos.materials.save_record(_material_record("m2"))
    repos.material_processing.save(_processing_record("m1", "PROCESSED"))
    repos.material_processing.save(_processing_record("m2", "FAILED"))
    assert repos.material_processing.ids_with_status("PROCESSED") == ["m1"]


def test_processing_requires_a_material(repos):
    with pytest.raises(PersistenceValidationError):
        repos.material_processing.save(_processing_record("ghost"))


def test_processing_requires_a_status(repos):
    repos.materials.save_record(_material_record("material-1"))
    with pytest.raises(ValueError):
        repos.material_processing.save({"material_id": "material-1"})


def test_processing_status_is_updated_in_place(repos):
    repos.materials.save_record(_material_record("material-1"))
    repos.material_processing.save(_processing_record("material-1", "REGISTERED"))
    repos.material_processing.save(
        _processing_record("material-1", "PROCESSED", attempts=2)
    )
    assert repos.material_processing.count() == 1
    assert repos.material_processing.status_of("material-1") == "PROCESSED"


def test_processing_last_attempt_at_is_stored(repos):
    repos.materials.save_record(_material_record("material-1"))
    repos.material_processing.save(
        _processing_record("material-1"), last_attempt_at="2026-01-02T00:00:00+00:00"
    )
    row = repos.material_processing.get_row("material-1")
    assert row["last_attempt_at"] == "2026-01-02T00:00:00+00:00"


# ======================================================================
# EvidenceRepository
# ======================================================================


def test_evidence_save_and_load(repos):
    repos.evidence.save(_evidence())
    loaded = repos.evidence.load("evidence-1")
    assert loaded.content == "Texto original"
    assert loaded.language is Language.SPANISH


def test_evidence_provenance_survives_roundtrip(repos):
    repos.evidence.save(_evidence())
    reference = repos.evidence.load("evidence-1").source_reference
    assert reference.material_id == "material-1"
    assert reference.timestamp_start == 1.0
    assert reference.timestamp_end == 2.0
    assert reference.page == 3
    assert reference.line == 4
    assert reference.paragraph == "p1"


def test_evidence_content_is_stored_verbatim(repos):
    text = "La funció f(x) = x² és contínua en 中文测试"
    repos.evidence.save(_evidence(content=text))
    assert repos.evidence.content_of("evidence-1") == text


def test_evidence_canonical_key_is_stored(repos):
    evidence = _evidence()
    repos.evidence.save(evidence)
    assert repos.evidence.canonical_key_of("evidence-1") == compute_canonical_key(evidence)


def test_evidence_duplicate_canonical_key_is_rejected(repos):
    """Task 23 的去重身份由数据库 UNIQUE 约束兜底。"""
    repos.evidence.save(_evidence("e1"))
    with pytest.raises(DuplicateRecordError):
        repos.evidence.save(_evidence("e2"))


def test_evidence_different_content_gives_different_key(repos):
    repos.evidence.save(_evidence("e1", content="A"))
    repos.evidence.save(_evidence("e2", content="B"))
    assert repos.evidence.count() == 2


def test_evidence_find_by_canonical_key(repos):
    evidence = _evidence()
    repos.evidence.save(evidence)
    found = repos.evidence.find_by_canonical_key(compute_canonical_key(evidence))
    assert found == "evidence-1"
    assert repos.evidence.find_by_canonical_key("nope") is None


def test_evidence_insertion_order_is_preserved(repos):
    for index in (3, 1, 2):
        repos.evidence.save(_evidence(f"e{index}", content=f"c{index}"))
    # 显式给了插入序号 0/1/2 -> 顺序由序号决定
    assert repos.evidence.insertion_order() == ["e3", "e1", "e2"]


def test_evidence_save_many_assigns_sequence(repos):
    repos.evidence.save_many(
        [_evidence("e1", content="a"), _evidence("e2", content="b")]
    )
    assert repos.evidence.insertion_order() == ["e1", "e2"]
    assert repos.evidence.next_insertion_seq() == 2


def test_evidence_state_defaults_to_active(repos):
    repos.evidence.save(_evidence())
    assert repos.evidence.state_of("evidence-1") == "ACTIVE"


def test_evidence_state_can_be_retired(repos):
    repos.evidence.save(_evidence())
    assert repos.evidence.set_state("evidence-1", "RETIRED") is True
    assert repos.evidence.state_of("evidence-1") == "RETIRED"


def test_evidence_load_active_excludes_retired(repos):
    repos.evidence.save(_evidence("e1", content="a"))
    repos.evidence.save(_evidence("e2", content="b"))
    repos.evidence.set_state("e2", "RETIRED")
    assert [e.evidence_id for e in repos.evidence.load_active()] == ["e1"]
    assert len(repos.evidence.load_all()) == 2


def test_evidence_set_state_on_missing_returns_false(repos):
    assert repos.evidence.set_state("ghost", "RETIRED") is False


def test_evidence_load_for_material(repos):
    repos.evidence.save(_evidence("e1", material_id="m1"))
    repos.evidence.save(_evidence("e2", content="x", material_id="m2"))
    assert [e.evidence_id for e in repos.evidence.load_for_material("m1")] == ["e1"]


def test_evidence_material_id_of(repos):
    repos.evidence.save(_evidence())
    assert repos.evidence.material_id_of("evidence-1") == "material-1"


def test_evidence_accepts_unregistered_material_id(repos):
    """证据是内容寻址的, 溯源可以指向尚未注册的 material (故意不加外键)。"""
    repos.evidence.save(_evidence("e1", material_id="never-registered"))
    assert repos.evidence.material_id_of("e1") == "never-registered"


# ======================================================================
# KnowledgeRepository
# ======================================================================


def test_knowledge_save_and_load(repos):
    repos.knowledge.save(_kp(), course_id="course-1")
    loaded = repos.knowledge.load("kp-1")
    assert loaded.title == "Función"
    assert loaded.content == "Una función es una relación."


def test_knowledge_evidence_link_is_stored(repos):
    repos.evidence.save(_evidence("e1"))
    repos.evidence.save(_evidence("e2", content="b"))
    repos.knowledge.save(_kp(evidence_refs=["e2", "e1"]), course_id="course-1")
    assert repos.knowledge.evidence_ids_for("kp-1") == ["e2", "e1"]


def test_knowledge_reverse_evidence_lookup(repos):
    repos.evidence.save(_evidence("e1"))
    repos.knowledge.save(_kp(evidence_refs=["e1"]), course_id="course-1")
    assert repos.knowledge.knowledge_ids_for_evidence("e1") == ["kp-1"]


def test_knowledge_evidence_link_requires_existing_evidence(repos):
    with pytest.raises(PersistenceValidationError):
        repos.knowledge.save(_kp(evidence_refs=["ghost"]), course_id="course-1")


def test_knowledge_load_all_filters_by_course(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.knowledge.save(_kp("kp-2"), course_id="course-2")
    assert len(repos.knowledge.load_all(course_id="course-1")) == 1


def test_knowledge_rejects_wrong_type(repos):
    with pytest.raises(TypeError):
        repos.knowledge.save("nope")


# ======================================================================
# RelationshipRepository
# ======================================================================


def test_relationship_save_and_load(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.knowledge.save(_kp("kp-2"), course_id="course-1")
    rel = Relationship(
        source_id="kp-1", target_id="kp-2", relation_type=RelationType.PRE_REQUSITE_OF
    )
    repos.relationships.save(rel)
    loaded = repos.relationships.load(rel.relationship_id)
    assert loaded.source_id == "kp-1"
    assert loaded.relation_type is RelationType.PRE_REQUSITE_OF


def test_relationship_requires_existing_knowledge_points(repos):
    rel = Relationship(source_id="ghost", target_id="kp-2")
    with pytest.raises(PersistenceValidationError):
        repos.relationships.save(rel)


def test_relationship_outgoing_and_incoming(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.knowledge.save(_kp("kp-2"), course_id="course-1")
    rel = Relationship(source_id="kp-1", target_id="kp-2")
    repos.relationships.save(rel)
    assert len(repos.relationships.outgoing("kp-1")) == 1
    assert len(repos.relationships.incoming("kp-2")) == 1


def test_relationship_edges_are_deterministic(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.knowledge.save(_kp("kp-2"), course_id="course-1")
    repos.relationships.save(Relationship(source_id="kp-1", target_id="kp-2"))
    repos.relationships.save(Relationship(source_id="kp-2", target_id="kp-1"))
    assert repos.relationships.edges() == sorted(repos.relationships.edges())


def test_relationship_of_type(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.knowledge.save(_kp("kp-2"), course_id="course-1")
    repos.relationships.save(
        Relationship(
            source_id="kp-1", target_id="kp-2", relation_type=RelationType.PRE_REQUSITE_OF
        )
    )
    assert len(repos.relationships.of_type("prerequisite_of")) == 1
    assert repos.relationships.of_type("related_to") == []


# ======================================================================
# ConflictRepository
# ======================================================================


def test_conflict_save_and_load(repos):
    repos.evidence.save(_evidence("e1"))
    repos.evidence.save(_evidence("e2", content="b"))
    conflict = ConflictRecord(
        conflict_id="conflict-1",
        evidence_refs=["e2", "e1"],
        description="Orden contradictorio",
    )
    repos.conflicts.save(conflict)
    loaded = repos.conflicts.load("conflict-1")
    assert loaded.description == "Orden contradictorio"
    assert loaded.evidence_refs == ["e2", "e1"]


def test_conflict_evidence_order_is_preserved(repos):
    repos.evidence.save(_evidence("e1"))
    repos.evidence.save(_evidence("e2", content="b"))
    repos.conflicts.save(
        ConflictRecord(conflict_id="c1", evidence_refs=["e2", "e1"])
    )
    assert repos.conflicts.evidence_ids_for("c1") == ["e2", "e1"]


def test_conflict_requires_id(repos):
    """``ConflictRecord.__post_init__`` 会补 uuid4, 所以必须手工清空才能
    触发这条守卫 —— 它是防止"用空 id 写库"的安全网。"""
    conflict = ConflictRecord(conflict_id="placeholder")
    conflict.conflict_id = ""
    with pytest.raises(ValueError):
        repos.conflicts.save(conflict)


def test_conflict_with_generated_id_is_saved(repos):
    conflict = ConflictRecord(conflict_id="", description="auto id")
    repos.conflicts.save(conflict)
    assert repos.conflicts.count() == 1


def test_conflict_ids_with_status(repos):
    repos.conflicts.save(ConflictRecord(conflict_id="c1", status="PENDING"))
    assert repos.conflicts.ids_with_status("PENDING") == ["c1"]


# ======================================================================
# ReviewRepository
# ======================================================================


def test_review_save_and_load(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.reviews.save(_review_record())
    loaded = repos.reviews.load("review-1")
    assert loaded.decision is ReviewDecision.CONFIRM
    assert loaded.note == "ok"


def test_review_history_is_ordered_by_id(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    for review_id in ("review-c", "review-a", "review-b"):
        repos.reviews.save(_review_record(review_id=review_id))
    ids = [r.review_id for r in repos.reviews.history_for("kp-1")]
    assert ids == ["review-a", "review-b", "review-c"]


def test_review_save_is_idempotent(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.reviews.save(_review_record())
    repos.reviews.save(_review_record())
    assert repos.reviews.count() == 1


def test_review_latest_for(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.reviews.save(_review_record("review-a"))
    repos.reviews.save(_review_record("review-b", decision=ReviewDecision.REJECT))
    assert repos.reviews.latest_for("kp-1").review_id == "review-b"


def test_review_latest_for_missing_returns_none(repos):
    assert repos.reviews.latest_for("kp-1") is None


def test_review_requires_id(repos):
    with pytest.raises(ValueError):
        repos.reviews.save(_review_record(review_id=""))


def test_review_history_for_other_kp_is_empty(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.reviews.save(_review_record())
    assert repos.reviews.history_for("kp-2") == []


# ======================================================================
# StudentRepository / 事件 / 状态
# ======================================================================


def test_student_save_and_load(repos):
    repos.students.save(Student.create("student-1", "Ada"), course_id="course-1")
    loaded = repos.students.load("student-1")
    assert loaded.display_name == "Ada"


def test_student_load_all_filters_by_course(repos):
    repos.students.save(Student.create("s1"), course_id="course-1")
    repos.students.save(Student.create("s2"), course_id="course-2")
    assert len(repos.students.load_all(course_id="course-1")) == 1


def test_student_course_of(repos):
    repos.students.save(Student.create("s1"), course_id="course-1")
    assert repos.students.course_of("s1") == "course-1"


def test_learning_event_save_and_load(repos):
    repos.students.save(Student.create("s1"), course_id="course-1")
    event = LearningEvent.create(
        "s1", "course-1", "kp-1", LearningEventType.VIEWED, 0
    )
    repos.learning_events.save(event)
    loaded = repos.learning_events.load(event.event_id)
    assert loaded.event_type is LearningEventType.VIEWED


def test_learning_event_is_idempotent(repos):
    repos.students.save(Student.create("s1"), course_id="course-1")
    event = LearningEvent.create("s1", "course-1", "kp-1", LearningEventType.VIEWED, 0)
    repos.learning_events.save(event)
    repos.learning_events.save(event)
    assert repos.learning_events.count() == 1


def test_learning_event_requires_its_student(repos):
    event = LearningEvent.create("ghost", "course-1", "kp-1", LearningEventType.VIEWED, 0)
    with pytest.raises(PersistenceValidationError):
        repos.learning_events.save(event)


def test_learning_events_for_student_are_ordered_by_sequence(repos):
    repos.students.save(Student.create("s1"), course_id="course-1")
    for sequence in (2, 0, 1):
        repos.learning_events.save(
            LearningEvent.create(
                "s1", "course-1", "kp-1", LearningEventType.VIEWED, sequence
            )
        )
    sequences = [
        e.sequence for e in repos.learning_events.load_for_student("s1")
    ]
    assert sequences == [0, 1, 2]


def test_duplicate_event_sequence_is_rejected(repos):
    repos.students.save(Student.create("s1"), course_id="course-1")
    first = LearningEvent.create("s1", "course-1", "kp-1", LearningEventType.VIEWED, 0)
    repos.learning_events.save(first)
    # 同 sequence 但不同类型 -> event_id 不同, 但唯一索引拒绝
    second = LearningEvent.create(
        "s1", "course-1", "kp-1", LearningEventType.PRACTICED, 0
    )
    with pytest.raises(DuplicateRecordError):
        repos.learning_events.save(second)


def test_student_state_save_and_load(repos):
    repos.students.save(Student.create("s1"), course_id="course-1")
    record = StudentKnowledgeRecord(
        student_id="s1",
        course_id="course-1",
        knowledge_point_id="kp-1",
        state=LearningState.EXPOSED,
        first_seen_at=1,
        last_activity_at=2,
        exposure_count=1,
        practice_count=0,
        answer_count=0,
        correct_count=0,
        incorrect_count=0,
    )
    repos.student_states.save(record)
    loaded = repos.student_states.load("s1", "course-1", "kp-1")
    assert loaded.state is LearningState.EXPOSED
    assert loaded.exposure_count == 1


def test_student_state_of(repos):
    repos.students.save(Student.create("s1"), course_id="course-1")
    repos.student_states.save(
        StudentKnowledgeRecord(
            "s1", "course-1", "kp-1", LearningState.REVIEWING, 0, 0, 1, 1, 1, 1, 0
        )
    )
    assert repos.student_states.state_of("s1", "course-1", "kp-1") == "reviewing"


def test_student_state_map_for_student(repos):
    repos.students.save(Student.create("s1"), course_id="course-1")
    for kp_id in ("kp-1", "kp-2"):
        repos.student_states.save(
            StudentKnowledgeRecord(
                "s1", "course-1", kp_id, LearningState.EXPOSED, 0, 0, 1, 0, 0, 0, 0
            )
        )
    assert repos.student_states.states_for_student("s1", course_id="course-1") == {
        "kp-1": "exposed",
        "kp-2": "exposed",
    }


# ======================================================================
# ExerciseRepository
# ======================================================================


def test_exercise_save_and_load(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    exercise = _exercise()
    repos.exercises.save(exercise)
    loaded = repos.exercises.load(exercise.exercise_id)
    assert loaded.prompt == "¿Qué es una función?"
    assert loaded.correct_choice_id == "a"


def test_exercise_knowledge_links_use_the_domain_order(repos):
    """``Exercise.create`` 会把 kp id 去重并**排序**, 因此存储层看到的是
    领域归一化后的顺序。关系表忠实保存这个顺序 (而不是调用方传入的顺序)
    —— 这正说明"关系表从领域对象派生"这条设计是对的。"""
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.knowledge.save(_kp("kp-2"), course_id="course-1")
    exercise = _exercise(kp_ids=("kp-2", "kp-1"))
    repos.exercises.save(exercise)
    assert repos.exercises.knowledge_point_ids_for(exercise.exercise_id) == [
        "kp-1",
        "kp-2",
    ]
    # payload 与关系表一致
    assert list(exercise.knowledge_point_ids) == ["kp-1", "kp-2"]


def test_exercise_evidence_links_use_the_domain_order(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.evidence.save(_evidence("e1"))
    repos.evidence.save(_evidence("e2", content="b"))
    exercise = _exercise(evidence_ids=("e2", "e1"))
    repos.exercises.save(exercise)
    assert repos.exercises.evidence_ids_for(exercise.exercise_id) == ["e1", "e2"]


def test_exercise_reverse_knowledge_lookup(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    exercise = _exercise()
    repos.exercises.save(exercise)
    assert repos.exercises.exercise_ids_for_knowledge_point("kp-1") == [
        exercise.exercise_id
    ]


def test_exercise_evidence_links(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.evidence.save(_evidence("e1"))
    exercise = _exercise(evidence_ids=("e1",))
    repos.exercises.save(exercise)
    assert repos.exercises.evidence_ids_for(exercise.exercise_id) == ["e1"]


def test_exercise_of_type(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    exercise = _exercise()
    repos.exercises.save(exercise)
    assert len(repos.exercises.of_type("multiple_choice")) == 1
    assert repos.exercises.of_type("true_false") == []


def test_exercise_type_of(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    exercise = _exercise()
    repos.exercises.save(exercise)
    assert repos.exercises.type_of(exercise.exercise_id) == "multiple_choice"


def test_exercise_answer_key_is_stored_for_authoring(repos):
    """存储层保存完整练习 (含答案键) —— 隐藏答案是投影层的职责。"""
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    exercise = _exercise()
    repos.exercises.save(exercise)
    payload = repos.exercises.get(exercise.exercise_id)
    assert payload["correct_choice_id"] == "a"


# ======================================================================
# AnswerRepository / EvaluationRepository
# ======================================================================


def _saved_exercise(repos, course_id="course-1", kp_id="kp-1"):
    repos.knowledge.save(_kp(kp_id), course_id=course_id)
    exercise = _exercise(course_id=course_id, kp_ids=(kp_id,))
    repos.exercises.save(exercise)
    repos.students.save(Student.create("student-1"), course_id=course_id)
    return exercise


def test_answer_save_and_load(repos):
    exercise = _saved_exercise(repos)
    answer = StudentAnswer.create("student-1", exercise.exercise_id, "a", 0)
    repos.answers.save(answer, course_id="course-1", submitted_at="2026-01-01T00:00:00+00:00")
    loaded = repos.answers.load(answer.answer_id)
    assert loaded.submitted_value == "a"
    assert repos.answers.submitted_at_for(answer.answer_id) == "2026-01-01T00:00:00+00:00"


def test_answer_is_idempotent(repos):
    exercise = _saved_exercise(repos)
    answer = StudentAnswer.create("student-1", exercise.exercise_id, "a", 0)
    repos.answers.save(answer)
    repos.answers.save(answer)
    assert repos.answers.count() == 1


def test_answer_requires_its_exercise(repos):
    repos.students.save(Student.create("student-1"), course_id="course-1")
    answer = StudentAnswer.create("student-1", "ghost", "a", 0)
    with pytest.raises(PersistenceValidationError):
        repos.answers.save(answer)


def test_answer_requires_its_student(repos):
    exercise = _saved_exercise(repos)
    answer = StudentAnswer.create("ghost", exercise.exercise_id, "a", 0)
    with pytest.raises(PersistenceValidationError):
        repos.answers.save(answer)


def test_answers_for_student_are_ordered(repos):
    exercise = _saved_exercise(repos)
    for sequence in (2, 0, 1):
        repos.answers.save(
            StudentAnswer.create("student-1", exercise.exercise_id, f"v{sequence}", sequence)
        )
    sequences = [a.sequence for a in repos.answers.load_all(student_id="student-1")]
    assert sequences == [0, 1, 2]


def _real_evaluation(exercise, answer):
    """用真正的领域评估器产出一个 EvaluationResult。

    ``EvaluationResult.evaluation_id`` 是**内容寻址**的
    (``from_dict`` 会重算并校验), 所以测试不能手写一个假 id ——
    必须让领域层自己生成。这同时也证明了存储层能与真实领域对象往返。
    """
    from src.answer_evaluation import ExactEvaluator

    return ExactEvaluator({exercise.exercise_id: exercise}).evaluate(answer)


def test_evaluation_save_and_load(repos):
    exercise = _saved_exercise(repos)
    answer = StudentAnswer.create("student-1", exercise.exercise_id, "a", 0)
    repos.answers.save(answer, course_id="course-1")
    result = _real_evaluation(exercise, answer)
    repos.evaluations.save(
        result, exercise_id=exercise.exercise_id, student_id="student-1"
    )
    loaded = repos.evaluations.load(result.evaluation_id)
    assert loaded.status is EvaluationStatus.CORRECT
    assert loaded.score == 1.0
    assert loaded.evaluation_id == result.evaluation_id


def test_evaluation_requires_an_existing_answer(repos):
    exercise = _saved_exercise(repos)
    answer = StudentAnswer.create("student-1", exercise.exercise_id, "a", 0)
    result = _real_evaluation(exercise, answer)  # 答案**没有**落库
    with pytest.raises(PersistenceValidationError):
        repos.evaluations.save(result)


def test_evaluation_for_answer(repos):
    exercise = _saved_exercise(repos)
    answer = StudentAnswer.create("student-1", exercise.exercise_id, "b", 0)
    repos.answers.save(answer)
    result = _real_evaluation(exercise, answer)
    repos.evaluations.save(
        result, exercise_id=exercise.exercise_id, student_id="student-1"
    )
    assert (
        repos.evaluations.for_answer(answer.answer_id).status
        is EvaluationStatus.INCORRECT
    )


def test_evaluation_status_of(repos):
    exercise = _saved_exercise(repos)
    answer = StudentAnswer.create("student-1", exercise.exercise_id, "a", 0)
    repos.answers.save(answer)
    result = _real_evaluation(exercise, answer)
    repos.evaluations.save(
        result, exercise_id=exercise.exercise_id, student_id="student-1"
    )
    assert repos.evaluations.status_of(result.evaluation_id) == "correct"


def test_evaluation_ids_for_student_and_exercise(repos):
    exercise = _saved_exercise(repos)
    answer = StudentAnswer.create("student-1", exercise.exercise_id, "a", 0)
    repos.answers.save(answer)
    result = _real_evaluation(exercise, answer)
    repos.evaluations.save(
        result, exercise_id=exercise.exercise_id, student_id="student-1"
    )
    assert repos.evaluations.ids_for_student("student-1") == [result.evaluation_id]
    assert repos.evaluations.ids_for_exercise(exercise.exercise_id) == [
        result.evaluation_id
    ]


# ======================================================================
# StudyPlanRepository / LearningPathRepository
# ======================================================================


def test_study_plan_save_and_load(repos):
    plan = _study_plan()
    repos.study_plans.save(plan)
    loaded = repos.study_plans.load(plan.plan_id)
    assert loaded.student_id == "student-1"
    assert len(loaded.items) == 1


def test_study_plan_is_content_addressed(repos):
    """同一输入两次保存 -> 同一 plan_id -> 一行。"""
    repos.study_plans.save(_study_plan())
    repos.study_plans.save(_study_plan())
    assert repos.study_plans.count() == 1


def test_study_plan_item_count(repos):
    plan = _study_plan()
    repos.study_plans.save(plan)
    assert repos.study_plans.item_count(plan.plan_id) == 1


def test_study_plan_plans_for_student(repos):
    plan = _study_plan()
    repos.study_plans.save(plan)
    assert len(repos.study_plans.plans_for_student("student-1")) == 1
    assert repos.study_plans.plans_for_student("other") == []


def test_learning_path_save_and_load(repos):
    path = _learning_path()
    repos.learning_paths.save(path, course_id="course-1", student_id="student-1")
    loaded = repos.learning_paths.load("course-1", "student-1", "kp-2")
    assert loaded.node_ids == ("kp-1", "kp-2")


def test_learning_path_node_order_is_preserved(repos):
    """顺序是路径的语义 (根 -> 目标), 往返后必须不变。"""
    path = _learning_path(nodes=("kp-a", "kp-b", "kp-c", "kp-2"))
    repos.learning_paths.save(path, course_id="course-1", student_id="s1")
    assert repos.learning_paths.node_ids("course-1", "s1", "kp-2") == [
        "kp-a",
        "kp-b",
        "kp-c",
        "kp-2",
    ]


def test_learning_path_save_is_idempotent(repos):
    path = _learning_path()
    repos.learning_paths.save(path, course_id="course-1", student_id="s1")
    repos.learning_paths.save(path, course_id="course-1", student_id="s1")
    assert repos.learning_paths.count() == 1


def test_learning_path_requires_course_and_student(repos):
    with pytest.raises(ValueError):
        repos.learning_paths.save(_learning_path(), course_id="", student_id="s1")


def test_learning_path_status_of(repos):
    repos.learning_paths.save(
        _learning_path(), course_id="course-1", student_id="s1"
    )
    assert repos.learning_paths.status_of("course-1", "s1", "kp-2") == "ok"


def test_learning_path_targets_for_student(repos):
    repos.learning_paths.save(_learning_path("kp-2"), course_id="c", student_id="s")
    repos.learning_paths.save(
        _learning_path("kp-3", nodes=("kp-3",)), course_id="c", student_id="s"
    )
    assert repos.learning_paths.targets_for_student("c", "s") == ["kp-2", "kp-3"]


def test_learning_path_cycle_status_survives(repos):
    path = LearningPath(
        status=PathStatus.DEPENDENCY_CYCLE,
        target_knowledge_point_id="kp-1",
        node_ids=(),
        cycle_node_ids=("kp-1", "kp-2"),
    )
    repos.learning_paths.save(path, course_id="c", student_id="s")
    loaded = repos.learning_paths.load("c", "s", "kp-1")
    assert loaded.status is PathStatus.DEPENDENCY_CYCLE
    assert loaded.cycle_node_ids == ("kp-1", "kp-2")


# ======================================================================
# OrganizationRepository
# ======================================================================


def test_organization_save_and_load_structure(repos):
    structure = repos.organization.load_structure("course-1")
    topic = Topic.create("course-1", "Tema 1")
    structure.topics[topic.topic_id] = topic
    repos.organization.save_structure(structure)

    reloaded = repos.organization.load_structure("course-1")
    assert topic.topic_id in reloaded.topics
    assert reloaded.topics[topic.topic_id].name == "Tema 1"


def test_organization_memberships_roundtrip(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    structure = repos.organization.load_structure("course-1")
    topic = Topic.create("course-1", "Tema 1")
    structure.topics[topic.topic_id] = topic
    membership = KnowledgeMembership.create(topic.topic_id, "kp-1")
    structure.knowledge_memberships[membership.membership_id] = membership
    repos.organization.save_structure(structure)

    reloaded = repos.organization.load_structure("course-1")
    assert list(reloaded.knowledge_memberships) == [membership.membership_id]


def test_organization_relations_roundtrip(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.knowledge.save(_kp("kp-2"), course_id="course-1")
    structure = repos.organization.load_structure("course-1")
    relation = KnowledgeRelation.create(
        "course-1", "kp-1", "kp-2", KnowledgeRelationType.PREREQUISITE
    )
    structure.relations[relation.relation_id] = relation
    repos.organization.save_structure(structure)

    reloaded = repos.organization.load_structure("course-1")
    assert list(reloaded.relations) == [relation.relation_id]


def test_organization_session_memberships_roundtrip(repos):
    repos.courses.save(_course())
    repos.sessions.save(_session())
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    structure = repos.organization.load_structure("course-1")
    membership = SessionKnowledgeMembership.create("session-1", "kp-1")
    structure.session_memberships[membership.membership_id] = membership
    repos.organization.save_structure(structure)

    reloaded = repos.organization.load_structure("course-1")
    assert list(reloaded.session_memberships) == [membership.membership_id]


def test_organization_membership_requires_existing_knowledge_point(repos):
    structure = repos.organization.load_structure("course-1")
    topic = Topic.create("course-1", "Tema 1")
    structure.topics[topic.topic_id] = topic
    membership = KnowledgeMembership.create(topic.topic_id, "kp-ghost")
    structure.knowledge_memberships[membership.membership_id] = membership
    with pytest.raises(PersistenceValidationError):
        repos.organization.save_structure(structure)


def test_organization_relations_are_course_scoped(repos):
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.knowledge.save(_kp("kp-2"), course_id="course-1")
    structure = repos.organization.load_structure("course-1")
    structure.relations["r1"] = KnowledgeRelation.create(
        "course-1", "kp-1", "kp-2", KnowledgeRelationType.RELATED
    )
    repos.organization.save_structure(structure)
    assert len(repos.organization.relations.load_for_course("course-1")) == 1
    assert repos.organization.relations.load_for_course("course-2") == []


def test_organization_counts(repos):
    counts = repos.organization.count()
    assert set(counts) == {
        "topics",
        "knowledge_memberships",
        "session_memberships",
        "relations",
    }


# ======================================================================
# 基类行为
# ======================================================================


def test_unknown_column_is_rejected(repos):
    with pytest.raises(PersistenceValidationError):
        repos.courses.put({"course_id": "c1", "nope": 1}, {"a": 1})


def test_missing_primary_key_is_rejected(repos):
    with pytest.raises(PersistenceValidationError):
        repos.courses.put({"name": "A"}, {"a": 1})


def test_payload_cannot_be_passed_as_a_column(repos):
    with pytest.raises(PersistenceValidationError):
        repos.courses.put({"course_id": "c1", "payload": "{}"}, {"a": 1})


def test_wrong_key_arity_is_rejected(repos):
    with pytest.raises(PersistenceValidationError):
        repos.courses.get("a", "b")


def test_composite_key_arity_is_enforced(repos):
    with pytest.raises(PersistenceValidationError):
        repos.student_states.get("only-one")


def test_repository_rejects_non_database():
    with pytest.raises(PersistenceValidationError):
        LinkRepository("not a database", "material_evidence")


def test_unknown_link_table_is_rejected(db):
    with pytest.raises(PersistenceValidationError):
        LinkRepository(db, "no_such_link_table")


def test_link_replace_deduplicates_keeping_first_position(repos):
    repos.evidence.save(_evidence("e1"))
    repos.evidence.save(_evidence("e2", content="b"))
    repos.materials.save_record(_material_record("m1"))
    repos.materials.replace_evidence("m1", ["e2", "e1", "e2"])
    assert repos.materials.evidence_ids_for("m1") == ["e2", "e1"]


def test_link_add_appends_and_is_idempotent(repos):
    repos.evidence.save(_evidence("e1"))
    repos.evidence.save(_evidence("e2", content="b"))
    repos.materials.save_record(_material_record("m1"))
    links = repos.materials.evidence_links
    assert links.add("m1", "e1") is True
    assert links.add("m1", "e2") is True
    assert links.add("m1", "e1") is False
    assert links.rights_for("m1") == ["e1", "e2"]


def test_link_remove(repos):
    repos.evidence.save(_evidence("e1"))
    repos.materials.save_record(_material_record("m1"))
    links = repos.materials.evidence_links
    links.add("m1", "e1")
    assert links.remove("m1", "e1") is True
    assert links.rights_for("m1") == []


def test_link_all_pairs_is_deterministic(repos):
    repos.evidence.save(_evidence("e1"))
    repos.evidence.save(_evidence("e2", content="b"))
    repos.materials.save_record(_material_record("m1"))
    links = repos.materials.evidence_links
    links.replace("m1", ["e1", "e2"])
    assert links.all_pairs() == [("m1", "e1", 0), ("m1", "e2", 1)]


def test_repository_counts_snapshot(repos):
    repos.courses.save(_course())
    counts = repos.counts()
    assert counts["courses"] == 1
    assert counts["evidence"] == 0
    assert repos.total_rows() == 1
