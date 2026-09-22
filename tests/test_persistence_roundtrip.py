# -*- coding: utf-8 -*-
"""Task 42 —— 重启 / 损坏 / 并发读 / 整体无损往返 测试。

规范点名要求覆盖::

    CRUD / transaction / rollback / restart / migration / serialization
    duplicate / idempotency / concurrent read / corrupted DB handling

CRUD、transaction、rollback、migration、serialization、duplicate、
idempotency 已由另外四个文件覆盖。本文件专攻剩下的三件事, 以及本层真正的
验收标准 —— **重启之后一个字节都没丢**:

- ``restart``:        关掉再打开, schema / 行 / 顺序 / 状态 / 关系全在
- ``corrupted DB``:   payload 坏了要**报错**, 不能伪装成"空"
- ``concurrent read``:多线程同时读, 看到的必须是同一份已提交数据
- 整体无损往返:        领域对象 -> 数据库 -> 领域对象, ``to_dict()`` 深度相等

为什么"无损"用 ``to_dict()`` 深度相等来断言
--------------------------------------------------------------------
因为这是唯一不依赖我猜字段的判据。手写 ``assert loaded.title == ...``
只能证明我记得的那几个字段没丢; ``to_dict()`` 相等证明**所有**字段
(溯源 ``source_reference``、开放的 ``metadata``、以及将来新增的字段)
都原样回来了。领域层的 ``from_dict`` 还会顺便做一次校验。

顺序敏感的比较另外单独断言: 证据的插入顺序、评审历史的 id 顺序、学习路径
的节点顺序、学习事件的 sequence —— 这些顺序本身就是信息, 不能靠"集合相等"
糊过去。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading

import pytest

from src.evidence_store import EvidenceState, EvidenceStore
from src.exercises import Choice, Exercise, ExerciseType
from src.knowledge_organization import (
    CourseKnowledgeStructure,
    KnowledgeMembership,
    KnowledgeOrganizationService,
    KnowledgeRelation,
    KnowledgeRelationType,
    SessionKnowledgeMembership,
    Topic,
)
from src.knowledge_review import ReviewDecision, ReviewRecord
from src.knowledge_structure import KnowledgeStructure, Relationship, RelationType
from src.models import (
    Confidence,
    Evidence,
    EvidenceType,
    Language,
    SourceReference,
)
from src.persistence.database import Database
from src.persistence.errors import CorruptedDatabaseError, PayloadDecodeError
from src.persistence.migrations import MIGRATIONS, latest_version
from src.persistence.models.codec import decode_payload, payload_checksum
from src.persistence.repositories import Repositories
from src.persistence.snapshot import (
    load_evidence_store,
    load_knowledge_structure,
    load_organization_structure,
    load_student_log,
    save_evidence_store,
    save_knowledge_structure,
    save_organization_structure,
    save_student_log,
)
from src.student_learning import (
    LearningEventType,
    LearningState,
    Student,
    StudentLearningLog,
)
from src.answer_evaluation import ExactEvaluator, StudentAnswer
from src.study_plan import (
    RULES_VERSION,
    STUDY_PLAN_SCHEMA_VERSION,
    LearningPath,
    PathStatus,
    StudyItem,
    StudyPlan,
    StudyReason,
)
from tests.test_persistence_repositories import (
    _course,
    _evidence,
    _kp,
    _material_record,
    _processing_record,
    _session,
)


# ======================================================================
# 构造辅助
# ======================================================================


def _reopen(db_path: str) -> tuple[Database, Repositories]:
    """关掉再打开 —— 这就是"重启"。

    每次都用**新的** ``Database`` 对象, 因此不可能靠进程内的缓存蒙混
    过关: 数据必须真的在磁盘上。
    """
    database = Database(db_path)
    database.migrate()
    return database, Repositories(database)


def _multilingual_evidence(evidence_id: str, content: str, material_id: str = "material-1"):
    """带西语 / 加泰语 / 中文原文的证据 —— 原文必须原样回来。"""
    return Evidence(
        evidence_id=evidence_id,
        content=content,
        language=Language.SPANISH,
        source_reference=SourceReference(
            material_id=material_id,
            timestamp_start=12.5,
            timestamp_end=48.25,
            page=7,
            line=3,
            paragraph="párrafo 2",
        ),
        confidence=Confidence.HIGH,
        evidence_type=EvidenceType.TRANSCRIPT,
        metadata={"nota": "àudio de classe", "备注": "课堂音频"},
    )


def _real_study_plan(
    *,
    student_id: str = "stu-1",
    course_id: str = "course-1",
    knowledge_point_ids: tuple[str, ...] = ("kp-1",),
) -> StudyPlan:
    """用**领域层自己的规则**算出一个带真实 ``plan_id`` 的学习计划。

    ``StudyPlan.plan_id`` 是内容寻址的 (由 items + rules_version 派生),
    所以测试不能手写一个假 id —— 否则 ``to_dict()`` 比较会因为"输入本身
    就不自洽"而失败, 掩盖真正的存储问题。走 ``from_dict`` 就等于让领域层
    自己把 id 补上, 测试里不需要复制一遍哈希逻辑。
    """
    return StudyPlan.from_dict(
        {
            "schema_version": STUDY_PLAN_SCHEMA_VERSION,
            "plan_id": "",
            "student_id": student_id,
            "course_id": course_id,
            "items": [
                StudyItem(
                    knowledge_point_id=kp_id,
                    reason_codes=(StudyReason.LOW_PRACTICE_COUNT,),
                    prerequisite_ids=(),
                ).to_dict()
                for kp_id in knowledge_point_ids
            ],
            "rules_version": RULES_VERSION,
        }
    )


def _sorted_by(items: list[dict], key: str) -> list[dict]:
    return sorted(items, key=lambda d: d[key])


def _normalise_structure(snapshot: dict) -> dict:
    """把知识结构快照里的列表按 id 排序, 只消除**顺序**, 不消除字段。

    顺序本身另有专门的断言; 这里做深度比较时不希望被列表顺序干扰。
    """
    return {
        "knowledge_points": _sorted_by(snapshot["knowledge_points"], "knowledge_id"),
        "relationships": _sorted_by(snapshot["relationships"], "relationship_id"),
        "conflicts": _sorted_by(snapshot["conflicts"], "conflict_id"),
        "review_records": _sorted_by(snapshot["review_records"], "review_id"),
    }


# ======================================================================
# restart —— schema
# ======================================================================


def test_reopened_database_keeps_its_schema_version(db_path):
    first = Database(db_path)
    first.migrate()
    version = first.schema_version()
    first.close()

    second, _ = _reopen(db_path)
    try:
        assert second.schema_version() == version == latest_version()
    finally:
        second.close()


def test_reopened_database_keeps_the_migration_ledger(db_path):
    first = Database(db_path)
    first.migrate()
    ledger = [(m["version"], m["name"]) for m in first.applied_migrations()]
    first.close()

    second, _ = _reopen(db_path)
    try:
        assert [(m["version"], m["name"]) for m in second.applied_migrations()] == ledger
    finally:
        second.close()


def test_reopening_does_not_reapply_any_migration(db_path):
    """第二次打开时迁移链已经是最新的, 应该一条都不执行。"""
    first = Database(db_path)
    first.migrate()
    first.close()

    second, _ = _reopen(db_path)
    try:
        assert second.migrate() == 0  # 没有新的迁移被应用
        assert len(second.applied_migrations()) == len(MIGRATIONS)
    finally:
        second.close()


def test_reopening_an_up_to_date_database_writes_nothing(db_path):
    """``migrate()`` 在最新版本上必须完全只读 —— 否则每次打开都要抢写锁。

    这条不是微优化: 写锁会被任何正在进行的导入/批量落库挡住。本项目是
    浏览器轮询的本地服务, "打开库 = 可能等 5 秒然后失败" 是不能接受的。
    """
    first = Database(db_path)
    first.migrate()
    first.close()

    before = os.stat(db_path)
    second, _ = _reopen(db_path)
    try:
        assert second.migrate() == 0
        assert second.schema_version() == latest_version()
    finally:
        second.close()

    after = os.stat(db_path)
    # 内容没变 (WAL 下主库文件不该被改)
    assert after.st_size == before.st_size
    # 也没有残留的 -wal / -journal 需要恢复
    assert not os.path.exists(db_path + "-journal")


def test_a_second_connection_can_open_while_a_writer_holds_a_transaction(db_path):
    """写事务进行中, 新连接也必须能打开并迁移到最新版本。

    这是 ``_ensure_ledger`` 走只读快速路径的直接验收: 库已经是最新版时
    打开数据库不申请写锁, 因此不会被别人的长事务挡在门外。
    """
    first = Database(db_path)
    first.migrate()
    Repositories(first).courses.save(_course())

    try:
        with Repositories(first).transaction():
            Repositories(first).knowledge.save(_kp("kp-1"), course_id="course-1")
            second, reopened = _reopen(db_path)  # 写锁还开着, 这里不能卡住
            try:
                assert second.schema_version() == latest_version()
                assert reopened.courses.count() == 1
            finally:
                second.close()
    finally:
        first.close()


def test_reopened_database_is_healthy(db_path):
    first = Database(db_path)
    first.migrate()
    first.close()

    second, _ = _reopen(db_path)
    try:
        assert second.is_healthy() is True
        assert second.foreign_key_violations() == []
    finally:
        second.close()


def test_reopened_database_keeps_every_table(db_path):
    first = Database(db_path)
    first.migrate()
    tables = set(first.table_names())
    first.close()

    second, _ = _reopen(db_path)
    try:
        assert set(second.table_names()) == tables
    finally:
        second.close()


# ======================================================================
# restart —— 行 / 顺序 / 状态 / 关系
# ======================================================================


def test_reopened_database_keeps_rows(db_path):
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    repos.courses.save(_course())
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        assert reopened.courses.load("course-1").name == "Álgebra"
        assert reopened.knowledge.load("kp-1").title == "Función"
    finally:
        second.close()


def test_reopened_database_keeps_evidence_insertion_order(db_path):
    """Task 23 的查询依赖插入顺序 —— SQLite 不保证无 ORDER BY 的行序。"""
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    ids = ["e-3", "e-1", "e-2", "e-4"]  # 故意不是字典序
    for seq, eid in enumerate(ids):
        repos.evidence.save(_evidence(eid, content=f"Texto {eid}"), insertion_seq=seq)
    first.close()

    second, reopened = _reopen(db_path)
    try:
        assert reopened.evidence.insertion_order() == ids
        assert [e.evidence_id for e in reopened.evidence.load_all()] == ids
    finally:
        second.close()


def test_reopened_database_keeps_retired_evidence_state(db_path):
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    repos.evidence.save(_evidence("e-1", content="Uno"), insertion_seq=0)
    repos.evidence.save(_evidence("e-2", content="Dos"), insertion_seq=1)
    repos.evidence.set_state("e-2", EvidenceState.RETIRED)
    first.close()

    second, reopened = _reopen(db_path)
    try:
        assert reopened.evidence.state_of("e-2") == EvidenceState.RETIRED.value
        assert reopened.evidence.state_of("e-1") == EvidenceState.ACTIVE.value
        # 退役的证据仍然可按 id 取回 (历史必须留痕)
        assert reopened.evidence.load("e-2") is not None
        assert [e.evidence_id for e in reopened.evidence.load_active()] == ["e-1"]
    finally:
        second.close()


def test_reopened_database_keeps_the_canonical_key_index(db_path):
    """去重身份是 canonical_key —— 重启后必须还能按它查到。"""
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    evidence = _evidence("e-1", content="Contenido canónico")
    repos.evidence.save(evidence, insertion_seq=0)
    key = repos.evidence.canonical_key_of("e-1")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        assert reopened.evidence.canonical_key_of("e-1") == key
        assert reopened.evidence.find_by_canonical_key(key) == "e-1"
    finally:
        second.close()


def test_reopened_database_keeps_knowledge_point_provenance_chain(db_path):
    """溯源链的顺序是信息的一部分, 重启后必须逐位相同。"""
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    for seq, eid in enumerate(["e-1", "e-2", "e-3"]):
        repos.evidence.save(_evidence(eid, content=f"Texto {eid}"), insertion_seq=seq)
    kp = _kp("kp-1", evidence_refs=["e-3", "e-1", "e-2"])  # 刻意乱序
    repos.knowledge.save(kp, course_id="course-1")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        assert reopened.knowledge.evidence_ids_for("kp-1") == ["e-3", "e-1", "e-2"]
        assert reopened.knowledge.load("kp-1").evidence_refs == ["e-3", "e-1", "e-2"]
    finally:
        second.close()


def test_reopened_database_keeps_review_history_order(db_path):
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    for rid in ("r-03", "r-01", "r-02"):
        repos.reviews.save(
            ReviewRecord(
                review_id=rid,
                knowledge_point_id="kp-1",
                decision=ReviewDecision.CONFIRM,
                selected_evidence_ids=("e-1",),
            )
        )
    first.close()

    second, reopened = _reopen(db_path)
    try:
        history = reopened.reviews.history_for("kp-1")
        assert [r.review_id for r in history] == ["r-01", "r-02", "r-03"]
        assert reopened.reviews.latest_for("kp-1").review_id == "r-03"
        assert reopened.reviews.count_for("kp-1") == 3
    finally:
        second.close()


def test_reopened_database_keeps_conflicts(db_path):
    from src.integration import ConflictRecord

    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    repos.evidence.save(_evidence("e-1"), insertion_seq=0)
    conflict = ConflictRecord(
        conflict_id="conf-1",
        evidence_refs=["e-1"],
        description="Dos fuentes discrepan",
        status="PENDING",
    )
    repos.conflicts.save(conflict)
    first.close()

    second, reopened = _reopen(db_path)
    try:
        loaded = reopened.conflicts.load("conf-1")
        assert loaded.description == "Dos fuentes discrepan"
        assert reopened.conflicts.evidence_ids_for("conf-1") == ["e-1"]
        assert reopened.conflicts.ids_with_status("PENDING") == ["conf-1"]
    finally:
        second.close()


def test_reopened_database_keeps_student_state_counters(db_path):
    """计数落库; 而"作答不推动状态机"这条领域规则也必须原样保留。

    Task 30 的状态机里 ``ANSWERED`` **不是**迁移事件 —— 只有
    VIEWED/PRACTICED/REVIEWED 才推状态。所以"答了 3 次"的记录
    ``state`` 仍然是 ``not_started``。这不是缺陷, 是设计: 存储层
    只搬运领域层算出来的结果, 不自己重新解释事件。
    """
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    log = StudentLearningLog(Student.create("stu-1"))
    log.register_knowledge_point("course-1", "kp-1")
    log.record_answer("course-1", "kp-1", is_correct=True)
    log.record_answer("course-1", "kp-1", is_correct=True)
    log.record_answer("course-1", "kp-1", is_correct=False)
    save_student_log(repos, log, course_id="course-1")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        record = reopened.student_states.load("stu-1", "course-1", "kp-1")
        assert record.correct_count == 2
        assert record.incorrect_count == 1
        assert record.answer_count == 3
        assert record.state is LearningState.NOT_STARTED
        assert record.to_dict() == log.derive_state("course-1", "kp-1").to_dict()
        assert reopened.student_states.state_of("stu-1", "course-1", "kp-1") == (
            LearningState.NOT_STARTED.value
        )
    finally:
        second.close()


def test_reopened_database_keeps_a_practicing_state(db_path):
    """走完 VIEWED -> PRACTICED 之后状态是 ``practicing``, 重启后不变。"""
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    log = StudentLearningLog(Student.create("stu-1"))
    log.register_knowledge_point("course-1", "kp-1")
    log.record_event("course-1", "kp-1", LearningEventType.VIEWED)
    log.record_event("course-1", "kp-1", LearningEventType.PRACTICED)
    save_student_log(repos, log, course_id="course-1")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        record = reopened.student_states.load("stu-1", "course-1", "kp-1")
        assert record.state is LearningState.PRACTICING
        assert record.exposure_count == 1
        assert record.practice_count == 1
    finally:
        second.close()


def test_reopened_database_keeps_exercise_and_evaluation(db_path):
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.students.save(Student.create("stu-1"), course_id="course-1")
    exercise = Exercise.create(
        "course-1",
        ExerciseType.MULTIPLE_CHOICE,
        "¿Qué es una función?",
        ("kp-1",),
        choices=(Choice("a", "Una relación"), Choice("b", "Un número")),
        correct_choice_id="a",
        difficulty=2,
    )
    repos.exercises.save(exercise)
    answer = StudentAnswer.create("stu-1", exercise.exercise_id, "a", 0)
    repos.answers.save(answer, course_id="course-1", submitted_at="2026-02-02T10:00:00+00:00")
    result = ExactEvaluator({exercise.exercise_id: exercise}).evaluate(answer)
    repos.evaluations.save(result, exercise_id=exercise.exercise_id, student_id="stu-1")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        assert reopened.exercises.load(exercise.exercise_id).prompt == "¿Qué es una función?"
        assert reopened.exercises.knowledge_point_ids_for(exercise.exercise_id) == ["kp-1"]
        loaded_answer = reopened.answers.load(answer.answer_id)
        assert loaded_answer.submitted_value == "a"
        assert reopened.answers.submitted_at_for(answer.answer_id) == "2026-02-02T10:00:00+00:00"
        loaded_result = reopened.evaluations.for_answer(answer.answer_id)
        assert loaded_result.evaluation_id == result.evaluation_id
        assert loaded_result.status is result.status
    finally:
        second.close()


def test_reopened_database_keeps_study_plan_and_learning_path(db_path):
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    repos.students.save(Student.create("stu-1"), course_id="course-1")
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.knowledge.save(_kp("kp-2"), course_id="course-1")
    plan = _real_study_plan()
    repos.study_plans.save(plan)
    path = LearningPath(
        status=PathStatus.OK,
        target_knowledge_point_id="kp-2",
        node_ids=("kp-1", "kp-2"),
        cycle_node_ids=(),
    )
    repos.learning_paths.save(path, course_id="course-1", student_id="stu-1")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        loaded_plan = reopened.study_plans.load(plan.plan_id)
        assert [i.knowledge_point_id for i in loaded_plan.items] == ["kp-1"]
        assert reopened.study_plans.item_count(plan.plan_id) == 1
        assert reopened.learning_paths.node_ids("course-1", "stu-1", "kp-2") == [
            "kp-1",
            "kp-2",
        ]
        assert reopened.learning_paths.status_of("course-1", "stu-1", "kp-2") == (
            PathStatus.OK.value
        )
    finally:
        second.close()


def test_reopened_database_keeps_the_material_processing_pair(db_path):
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    repos.materials.register_with_status(
        _material_record("m-1"), _processing_record("m-1", "PROCESSING", attempts=2)
    )
    first.close()

    second, reopened = _reopen(db_path)
    try:
        assert reopened.materials.load_record("m-1")["filename"] == "clase1.mp3"
        assert reopened.material_processing.status_of("m-1") == "PROCESSING"
        assert reopened.material_processing.ids_with_status("PROCESSING") == ["m-1"]
    finally:
        second.close()


def test_uncommitted_work_is_discarded_on_close(db_path):
    """进程在事务中间挂掉, 磁盘上不能出现"写了一半"的数据。"""
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    repos.courses.save(_course())
    first.begin()
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    # 故意不 commit, 直接关掉 (模拟崩溃)
    first.close()

    second, reopened = _reopen(db_path)
    try:
        assert reopened.courses.count() == 1  # 已提交的在
        assert reopened.knowledge.count() == 0  # 未提交的没了
    finally:
        second.close()


def test_writes_after_a_restart_are_visible_after_another_restart(db_path):
    first = Database(db_path)
    first.migrate()
    Repositories(first).courses.save(_course())
    first.close()

    second, reopened = _reopen(db_path)
    reopened.courses.save(_course(course_id="course-2", name="Càlcul", code="CAL"))
    second.close()

    third, again = _reopen(db_path)
    try:
        assert {c.course_id for c in again.courses.load_all()} == {"course-1", "course-2"}
    finally:
        third.close()


# ======================================================================
# corrupted DB handling
# ======================================================================


def test_corrupted_database_file_is_rejected_on_open(tmp_path):
    path = tmp_path / "garbage.sqlite"
    path.write_bytes(b"this is definitely not a sqlite database, not even close")
    with pytest.raises(CorruptedDatabaseError):
        Database(str(path)).connect()


def test_truncated_database_file_is_rejected_on_open(tmp_path):
    path = tmp_path / "truncated.sqlite"
    path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 10 + b"junk" * 100)
    with pytest.raises(CorruptedDatabaseError):
        Database(str(path)).migrate()


def test_corrupted_file_error_carries_a_storage_code(tmp_path):
    path = tmp_path / "garbage2.sqlite"
    path.write_bytes(b"nope" * 100)
    with pytest.raises(CorruptedDatabaseError) as excinfo:
        Database(str(path)).connect()
    assert excinfo.value.code == "STORAGE_CORRUPTED_DATABASE"


def test_corrupted_payload_is_reported_not_silently_empty(db_path):
    """坏 payload 必须**报错** —— 静默返回 ``{}`` 会把损坏伪装成空数据。"""
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    repos.courses.save(_course())
    first.close()

    second, reopened = _reopen(db_path)
    try:
        reopened.database.execute(
            "UPDATE courses SET payload = ? WHERE course_id = ?",
            ("{not valid json", "course-1"),
        )
        with pytest.raises(PayloadDecodeError):
            reopened.courses.load("course-1")
    finally:
        second.close()


def test_schema_forbids_a_null_payload(db_path):
    """``payload`` 是 NOT NULL —— 数据库层面就不允许"没有内容"的行存在。

    这比运行期检查更强: 根本写不进去, 因此读取端不可能遇到 NULL。
    """
    first = Database(db_path)
    first.migrate()
    Repositories(first).courses.save(_course())
    first.close()

    second, reopened = _reopen(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            reopened.database.execute(
                "UPDATE courses SET payload = NULL WHERE course_id = ?", ("course-1",)
            )
        # 原值没被破坏
        assert reopened.courses.load("course-1").name == "Álgebra"
    finally:
        second.close()


def test_decode_payload_rejects_none_instead_of_returning_empty():
    """万一真拿到 NULL, 解码器也必须报错, 绝不静默返回 ``{}``。"""
    with pytest.raises(PayloadDecodeError):
        decode_payload(None)
    with pytest.raises(PayloadDecodeError):
        decode_payload(None, context="courses.payload")


def test_payload_that_is_not_an_object_is_reported(db_path):
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    repos.courses.save(_course())
    first.close()

    second, reopened = _reopen(db_path)
    try:
        reopened.database.execute(
            "UPDATE courses SET payload = ? WHERE course_id = ?", ("[1, 2, 3]", "course-1")
        )
        with pytest.raises(PayloadDecodeError):
            reopened.courses.load("course-1")
    finally:
        second.close()


def test_one_corrupted_row_does_not_break_the_others(db_path):
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    repos.courses.save(_course())
    repos.courses.save(_course(course_id="course-2", name="Càlcul", code="CAL"))
    first.close()

    second, reopened = _reopen(db_path)
    try:
        reopened.database.execute(
            "UPDATE courses SET payload = ? WHERE course_id = ?", ("{broken", "course-1")
        )
        assert reopened.courses.load("course-2").name == "Càlcul"
        with pytest.raises(PayloadDecodeError):
            reopened.courses.load("course-1")
    finally:
        second.close()


def test_payload_checksum_detects_tampering(db_path):
    """checksum 是"内容没被改过"的证据 —— 改动一个字节就该变。"""
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    repos.courses.save(_course())
    original = repos.courses.payload_text("course-1")
    before = payload_checksum(original)
    first.close()

    second, reopened = _reopen(db_path)
    try:
        assert payload_checksum(reopened.courses.payload_text("course-1")) == before
        reopened.database.execute(
            "UPDATE courses SET payload = ? WHERE course_id = ?",
            (original.replace("Álgebra", "Algebra"), "course-1"),
        )
        assert payload_checksum(reopened.courses.payload_text("course-1")) != before
    finally:
        second.close()


def test_row_level_corruption_does_not_make_the_file_unhealthy(db_path):
    """payload 坏了是**行级**问题; 文件级完整性检查仍然通过。

    这个边界很重要: 若把两者混为一谈, 一处 payload 损坏就会让整个库
    被判为不可用, 用户连好的数据都读不出来。
    """
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    repos.courses.save(_course())
    first.close()

    second, reopened = _reopen(db_path)
    try:
        reopened.database.execute(
            "UPDATE courses SET payload = ? WHERE course_id = ?", ("{broken", "course-1")
        )
        assert reopened.database.integrity_check() == "ok"
        assert reopened.database.is_healthy() is True
    finally:
        second.close()


def test_writing_still_works_after_a_corrupted_row_is_found(db_path):
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    repos.courses.save(_course())
    first.close()

    second, reopened = _reopen(db_path)
    try:
        reopened.database.execute(
            "UPDATE courses SET payload = ? WHERE course_id = ?", ("{broken", "course-1")
        )
        with pytest.raises(PayloadDecodeError):
            reopened.courses.load("course-1")
        # 损坏被隔离: 修好那一行, 其它写入不受影响
        reopened.database.execute(
            "UPDATE courses SET payload = ? WHERE course_id = ?",
            (json.dumps(_course().to_dict(), ensure_ascii=False, sort_keys=True), "course-1"),
        )
        assert reopened.courses.load("course-1").name == "Álgebra"
        reopened.courses.save(_course(course_id="course-9", name="Física", code="FIS"))
        assert reopened.courses.count() == 2
    finally:
        second.close()


def test_corrupted_payload_is_reported_through_the_whole_store_rebuild(db_path):
    """整体重建证据库时遇到坏 payload 也要报错, 不能悄悄少一条。"""
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    repos.evidence.save(_evidence("e-1", content="Uno"), insertion_seq=0)
    repos.evidence.save(_evidence("e-2", content="Dos"), insertion_seq=1)
    first.close()

    second, reopened = _reopen(db_path)
    try:
        reopened.database.execute(
            "UPDATE evidence SET payload = ? WHERE evidence_id = ?", ("{broken", "e-2")
        )
        with pytest.raises(PayloadDecodeError):
            load_evidence_store(reopened)
    finally:
        second.close()


# ======================================================================
# concurrent read
# ======================================================================


def test_concurrent_readers_see_the_same_committed_rows(db_path):
    database = Database(db_path)
    database.migrate()
    repos = Repositories(database)
    for i in range(5):
        repos.courses.save(_course(course_id=f"course-{i}", name=f"Curso {i}", code=f"C{i}"))

    results: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def read() -> None:
        try:
            count = Repositories(database).courses.count()
            with lock:
                results.append(count)
        except BaseException as exc:  # noqa: BLE001 - 线程里的异常要带回主线程
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=read) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    try:
        assert errors == []
        assert results == [5] * 8
    finally:
        database.close()


def test_concurrent_readers_load_identical_payloads(db_path):
    database = Database(db_path)
    database.migrate()
    repos = Repositories(database)
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")

    payloads: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def read() -> None:
        try:
            text = Repositories(database).knowledge.payload_text("kp-1")
            with lock:
                payloads.append(text)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=read) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    try:
        assert errors == []
        assert len(set(payloads)) == 1  # 8 个线程读到逐字节相同的 payload
    finally:
        database.close()


def test_a_reader_is_not_blocked_while_a_writer_holds_a_transaction(db_path):
    """WAL 的核心价值: 读不被写阻塞。

    这是本项目"单机、单人、但界面可以同时轮询"的前提 —— 如果读会被
    写阻塞, 一次长写入就会让页面卡住。
    """
    database = Database(db_path)
    database.migrate()
    Repositories(database).courses.save(_course())

    finished = threading.Event()
    observed: list[int] = []
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            other = Database(db_path)
            other.migrate()
            try:
                observed.append(Repositories(other).courses.count())
            finally:
                other.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            finished.set()

    try:
        with Repositories(database).transaction():
            Repositories(database).knowledge.save(_kp("kp-1"), course_id="course-1")
            thread = threading.Thread(target=reader)
            thread.start()
            # 写事务还开着的时候读线程就应该能读完
            assert finished.wait(timeout=10), "读线程被写事务阻塞了"
            thread.join(timeout=10)

        assert errors == []
        assert observed == [1]  # 看到的是写事务开始前的快照
    finally:
        database.close()


def test_readers_see_the_pre_write_snapshot_inside_a_transaction(db_path):
    database = Database(db_path)
    database.migrate()
    Repositories(database).courses.save(_course())

    observed: list[int] = []
    try:
        with Repositories(database).transaction():
            Repositories(database).courses.save(
                _course(course_id="course-2", name="Càlcul", code="CAL")
            )
            other = Database(db_path)
            other.migrate()
            try:
                observed.append(Repositories(other).courses.count())
            finally:
                other.close()
        assert observed == [1]
        assert Repositories(database).courses.count() == 2
    finally:
        database.close()


def test_concurrent_reads_during_commits_never_see_a_partial_row(db_path):
    """边写边读时, 读到的行数只能是 0..N 之间的**已提交**值, 不能是半个。"""
    database = Database(db_path)
    database.migrate()

    stop = threading.Event()
    started = threading.Barrier(4, timeout=10)  # 3 个读者 + 主线程
    seen: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def reader() -> None:
        other = Database(db_path)
        other.migrate()
        repos = Repositories(other)
        try:
            started.wait()
            while not stop.is_set():
                count = repos.courses.count()
                # 每一行都必须能完整解码 (没有"写了一半"的行)
                for course in repos.courses.load_all():
                    assert course.name
                with lock:
                    seen.append(count)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)
        finally:
            other.close()

    threads = [threading.Thread(target=reader) for _ in range(3)]
    for t in threads:
        t.start()
    try:
        started.wait()  # 等三个读者都进入循环, 再开始写
        repos = Repositories(database)
        for i in range(10):
            repos.courses.save(
                _course(course_id=f"course-{i}", name=f"Curso {i}", code=f"C{i}")
            )
            assert repos.courses.count() <= 10
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=10)
        database.close()

    assert errors == []
    assert seen  # 确实读到了东西
    assert max(seen) <= 10


# ======================================================================
# 整体无损往返 —— 证据库
# ======================================================================


def test_evidence_store_round_trip_is_lossless(db_path):
    store = EvidenceStore()
    store.add(_multilingual_evidence("e-1", "Una función es una relación."))
    store.add(_multilingual_evidence("e-2", "Una funció és una relació."))
    store.add(_multilingual_evidence("e-3", "函数是一种关系。"))
    before = store.to_dict()

    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    save_evidence_store(repos, store)
    first.close()

    second, reopened = _reopen(db_path)
    try:
        after = load_evidence_store(reopened).to_dict()
        assert after == before
    finally:
        second.close()


def test_evidence_store_round_trip_preserves_original_text(db_path):
    """西语 / 加泰语 / 中文必须原样落库 —— 不许变成 \\uXXXX 转义。"""
    store = EvidenceStore()
    store.add(_multilingual_evidence("e-1", "Assignatura: Àlgebra abstracta"))
    store.add(_multilingual_evidence("e-2", "函数是一种关系。"))

    first = Database(db_path)
    first.migrate()
    save_evidence_store(Repositories(first), store)
    first.close()

    second, reopened = _reopen(db_path)
    try:
        raw = reopened.courses.database.query("SELECT payload FROM evidence")
        joined = "".join(row["payload"] for row in raw)
        assert "Àlgebra abstracta" in joined
        assert "函数是一种关系。" in joined
        assert "\\u" not in joined  # 没有转义
        loaded = load_evidence_store(reopened)
        assert [e.content for e in loaded.all()] == [
            "Assignatura: Àlgebra abstracta",
            "函数是一种关系。",
        ]
    finally:
        second.close()


def test_evidence_store_round_trip_keeps_retirement_and_order(db_path):
    store = EvidenceStore()
    store.add(_multilingual_evidence("e-1", "Primero"))
    store.add(_multilingual_evidence("e-2", "Segundo"))
    store.add(_multilingual_evidence("e-3", "Tercero"))
    store.retire("e-2")

    first = Database(db_path)
    first.migrate()
    save_evidence_store(Repositories(first), store)
    first.close()

    second, reopened = _reopen(db_path)
    try:
        loaded = load_evidence_store(reopened)
        assert loaded.get_state("e-2") == EvidenceState.RETIRED
        assert [e.evidence_id for e in loaded.all()] == ["e-1", "e-3"]
        assert [e.evidence_id for e in loaded.all(active_only=False)] == [
            "e-1",
            "e-2",
            "e-3",
        ]
    finally:
        second.close()


def test_evidence_store_snapshot_is_byte_identical_across_restarts(db_path):
    store = EvidenceStore()
    store.add(_multilingual_evidence("e-1", "Contenido original"))
    store.add(_multilingual_evidence("e-2", "Més contingut"))

    first = Database(db_path)
    first.migrate()
    save_evidence_store(Repositories(first), store)
    before = Repositories(first).evidence.snapshot()
    first.close()

    second, reopened = _reopen(db_path)
    try:
        assert reopened.evidence.snapshot() == before
    finally:
        second.close()


# ======================================================================
# 整体无损往返 —— 知识结构
# ======================================================================


def _knowledge_structure_fixture() -> KnowledgeStructure:
    """一个包含知识点 / 溯源 / 关系 / 冲突 / 评审历史的完整结构。"""
    from src.integration import ConflictRecord

    structure = KnowledgeStructure()
    structure.add_knowledge_point(_kp("kp-1", evidence_refs=["e-1", "e-2"]))
    structure.add_knowledge_point(_kp("kp-2", evidence_refs=["e-2"]))
    structure.add_relationship(
        Relationship(
            source_id="kp-1",
            target_id="kp-2",
            relation_type=RelationType.PRE_REQUSITE_OF,
            evidence_refs=["e-2"],
            relationship_id="rel-1",
        )
    )
    structure.add_conflict(
        ConflictRecord(
            conflict_id="conf-1",
            evidence_refs=["e-1", "e-2"],
            description="Las fuentes discrepan",
            status="PENDING",
        )
    )
    structure.add_review_record(
        ReviewRecord(
            review_id="r-01",
            knowledge_point_id="kp-1",
            decision=ReviewDecision.CONFIRM,
            selected_evidence_ids=("e-1",),
            note="confirmado",
        )
    )
    structure.add_review_record(
        ReviewRecord(
            review_id="r-02",
            knowledge_point_id="kp-1",
            decision=ReviewDecision.REJECT,
            selected_evidence_ids=(),
            note="revisado",
        )
    )
    return structure


def _seed_knowledge_prerequisites(repos) -> None:
    for seq, eid in enumerate(["e-1", "e-2"]):
        repos.evidence.save(_evidence(eid, content=f"Texto {eid}"), insertion_seq=seq)


def test_knowledge_structure_round_trip_is_lossless(db_path):
    structure = _knowledge_structure_fixture()
    before = structure.to_dict()

    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    _seed_knowledge_prerequisites(repos)
    save_knowledge_structure(repos, structure, course_id="course-1")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        after = load_knowledge_structure(reopened, course_id="course-1").to_dict()
        assert _normalise_structure(after) == _normalise_structure(before)
    finally:
        second.close()


def test_knowledge_structure_round_trip_keeps_review_history(db_path):
    structure = _knowledge_structure_fixture()
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    _seed_knowledge_prerequisites(repos)
    save_knowledge_structure(repos, structure, course_id="course-1")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        loaded = load_knowledge_structure(reopened, course_id="course-1")
        history = loaded.review_records_for_knowledge_point("kp-1")
        assert [r.review_id for r in history] == ["r-01", "r-02"]
        assert [r.decision for r in history] == [
            ReviewDecision.CONFIRM,
            ReviewDecision.REJECT,
        ]
        assert loaded.review_records_for_knowledge_point("kp-2") == []
    finally:
        second.close()


def test_knowledge_structure_round_trip_keeps_provenance_order(db_path):
    structure = KnowledgeStructure()
    structure.add_knowledge_point(_kp("kp-1", evidence_refs=["e-2", "e-1"]))
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    _seed_knowledge_prerequisites(repos)
    save_knowledge_structure(repos, structure, course_id="course-1")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        loaded = load_knowledge_structure(reopened, course_id="course-1")
        assert loaded.get_knowledge_point("kp-1").evidence_refs == ["e-2", "e-1"]
    finally:
        second.close()


def test_knowledge_structure_round_trip_keeps_conflicts(db_path):
    structure = _knowledge_structure_fixture()
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    _seed_knowledge_prerequisites(repos)
    save_knowledge_structure(repos, structure, course_id="course-1")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        loaded = load_knowledge_structure(reopened, course_id="course-1")
        assert [c.conflict_id for c in loaded.conflicts] == ["conf-1"]
        assert loaded.conflict_ids_for_knowledge_point("kp-1") == ["conf-1"]
    finally:
        second.close()


def test_knowledge_structure_round_trip_keeps_relationships(db_path):
    structure = _knowledge_structure_fixture()
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    _seed_knowledge_prerequisites(repos)
    save_knowledge_structure(repos, structure, course_id="course-1")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        loaded = load_knowledge_structure(reopened, course_id="course-1")
        assert [
            (r.source_id, r.target_id, r.relation_type.value) for r in loaded.relationships
        ] == [("kp-1", "kp-2", RelationType.PRE_REQUSITE_OF.value)]
        assert loaded.get_relationships("kp-1")[0].relationship_id == "rel-1"
    finally:
        second.close()


def test_review_history_does_not_leak_across_courses(db_path):
    """评审历史必须只属于本课程的知识点。

    评审记录本来只按 ``review_id`` 存, 没有课程维度; Task 68 (migration 003)
    加了 ``course_review_records`` —— 主键 ``(course_id, review_id)`` ——
    每门课各存一份。不加这个维度, 两门课的评审会串在一起, 那违反项目
    铁律"不混合不同课程的知识点"。

    注意这里**不要求**知识点 id 全局唯一: 两门课共用同一份讲义时
    ``knowledge_id`` 是一模一样的 (内容寻址), 那是合法的。所以下面除了
    "不同 id" 的常规用例, 还专门测了"同一个 id、两门课各一份评审" ——
    真正必须成立的是**课程作用域的查询**, 不是 id 唯一。
    """
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    _seed_knowledge_prerequisites(repos)
    repos.knowledge.save(_kp("kp-c1"), course_id="course-1")
    repos.knowledge.save(_kp("kp-c2"), course_id="course-2")
    # 同一个内容寻址 id 同时属于两门课 —— 合法, 且必须各自保留自己的评审。
    repos.knowledge.save(_kp("kp-shared"), course_id="course-1")
    repos.knowledge.save(_kp("kp-shared"), course_id="course-2")
    for review_id, course_id, kp_id in (
        ("r-c1", "course-1", "kp-c1"),
        ("r-c2", "course-2", "kp-c2"),
        ("r-shared-1", "course-1", "kp-shared"),
        ("r-shared-2", "course-2", "kp-shared"),
    ):
        repos.reviews.save(
            ReviewRecord(
                review_id=review_id,
                knowledge_point_id=kp_id,
                decision=ReviewDecision.CONFIRM,
                selected_evidence_ids=(),
            ),
            course_id=course_id,
        )
    first.close()

    second, reopened = _reopen(db_path)
    try:
        course1 = load_knowledge_structure(reopened, course_id="course-1")
        assert sorted(r.review_id for r in course1.review_records) == [
            "r-c1",
            "r-shared-1",
        ]
        course2 = load_knowledge_structure(reopened, course_id="course-2")
        assert sorted(r.review_id for r in course2.review_records) == [
            "r-c2",
            "r-shared-2",
        ]
        # 共享 id 的知识点在两门课里都还在 —— 课程隔离不能变成"丢数据"。
        assert "kp-shared" in course1.knowledge_points
        assert "kp-shared" in course2.knowledge_points
    finally:
        second.close()


# ======================================================================
# 整体无损往返 —— 知识组织
# ======================================================================


def _organization_fixture(repos) -> CourseKnowledgeStructure:
    repos.courses.save(_course())
    repos.sessions.save(_session("session-1", "course-1", 1))
    repos.sessions.save(_session("session-2", "course-1", 2))
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.knowledge.save(_kp("kp-2"), course_id="course-1")

    structure = CourseKnowledgeStructure("course-1")
    topic = Topic.create("course-1", "Tema 1")
    structure.topics[topic.topic_id] = topic
    membership = KnowledgeMembership.create(topic.topic_id, "kp-1")
    structure.knowledge_memberships[membership.membership_id] = membership
    for session_id, kp_id in (("session-1", "kp-1"), ("session-2", "kp-2")):
        smem = SessionKnowledgeMembership.create(session_id, kp_id)
        structure.session_memberships[smem.membership_id] = smem
    relation = KnowledgeRelation.create(
        "course-1", "kp-1", "kp-2", KnowledgeRelationType.PREREQUISITE
    )
    structure.relations[relation.relation_id] = relation
    return structure


def test_organization_structure_round_trip_is_lossless(db_path):
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    structure = _organization_fixture(repos)
    before = structure.to_dict()
    save_organization_structure(repos, structure)
    first.close()

    second, reopened = _reopen(db_path)
    try:
        after = load_organization_structure(reopened, "course-1").to_dict()
        assert after == before
    finally:
        second.close()


def test_organization_session_memberships_do_not_leak_across_courses(db_path):
    """课堂归属只能通过本课程的 session 找到。

    ``SessionKnowledgeMembership`` 只有 session_id + knowledge_point_id,
    没有 course_id, 所以"属于哪门课"完全由 ``sessions`` 表决定。
    不过滤就会把 course-2 的课堂归属端到 course-1 里。
    """
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    course1_structure = _organization_fixture(repos)
    save_organization_structure(repos, course1_structure)
    # 第二门课: 自己的 session + 知识点 + 课堂归属
    repos.courses.save(_course(course_id="course-2", name="Càlcul", code="CAL"))
    repos.sessions.save(_session("session-9", "course-2", 1))
    repos.knowledge.save(_kp("kp-9"), course_id="course-2")
    second_structure = CourseKnowledgeStructure("course-2")
    smem = SessionKnowledgeMembership.create("session-9", "kp-9")
    second_structure.session_memberships[smem.membership_id] = smem
    save_organization_structure(repos, second_structure)
    first.close()

    second, reopened = _reopen(db_path)
    try:
        course1 = load_organization_structure(reopened, "course-1")
        assert {m.session_id for m in course1.session_memberships.values()} == {
            "session-1",
            "session-2",
        }
        assert smem.membership_id not in course1.session_memberships

        course2 = load_organization_structure(reopened, "course-2")
        assert set(course2.session_memberships) == {smem.membership_id}
    finally:
        second.close()


def test_organization_knowledge_memberships_do_not_leak_across_courses(db_path):
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    course1_structure = _organization_fixture(repos)
    save_organization_structure(repos, course1_structure)
    repos.courses.save(_course(course_id="course-2", name="Càlcul", code="CAL"))
    repos.knowledge.save(_kp("kp-9"), course_id="course-2")
    other = CourseKnowledgeStructure("course-2")
    topic = Topic.create("course-2", "Tema 9")
    other.topics[topic.topic_id] = topic
    membership = KnowledgeMembership.create(topic.topic_id, "kp-9")
    other.knowledge_memberships[membership.membership_id] = membership
    save_organization_structure(repos, other)
    first.close()

    second, reopened = _reopen(db_path)
    try:
        course1 = load_organization_structure(reopened, "course-1")
        assert membership.membership_id not in course1.knowledge_memberships
        assert {t.course_id for t in course1.topics.values()} == {"course-1"}
        assert {t.name for t in course1.topics.values()} == {"Tema 1"}
    finally:
        second.close()


def test_organization_round_trip_rebuilds_internal_indexes(db_path):
    """``from_dict`` 会重建索引 —— 重启后 topic -> kp 的映射必须可用。"""
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    structure = _organization_fixture(repos)
    save_organization_structure(repos, structure)
    topic_id = next(iter(structure.topics))
    first.close()

    second, reopened = _reopen(db_path)
    try:
        loaded = load_organization_structure(reopened, "course-1")
        # 用领域服务查询 —— 它完全依赖 from_dict 重建出来的索引
        service = KnowledgeOrganizationService(_course(), loaded)
        assert service.get_topic_knowledge_points(topic_id) == ("kp-1",)
        assert service.get_knowledge_point_topics("kp-1") == (topic_id,)
    finally:
        second.close()


# ======================================================================
# 整体无损往返 —— 学生学习日志
# ======================================================================


def _student_log_fixture() -> StudentLearningLog:
    log = StudentLearningLog(Student.create("stu-1"))
    for kp_id in ("kp-1", "kp-2"):
        log.register_knowledge_point("course-1", kp_id)
    log.record_event("course-1", "kp-1", LearningEventType.VIEWED)
    log.record_answer("course-1", "kp-1", is_correct=True)
    log.record_answer("course-1", "kp-1", is_correct=False)
    log.record_event("course-1", "kp-1", LearningEventType.REVIEWED)
    log.record_answer("course-1", "kp-2", is_correct=True)
    return log


def test_student_log_round_trip_is_lossless(db_path):
    log = _student_log_fixture()
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    save_student_log(repos, log, course_id="course-1")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        loaded = load_student_log(reopened, "stu-1", course_id="course-1")
        assert loaded.student.to_dict() == log.student.to_dict()
        assert loaded.registered_knowledge_points == log.registered_knowledge_points
    finally:
        second.close()


def test_student_log_round_trip_keeps_event_sequence(db_path):
    """事件流是只追加的; sequence 决定顺序, 重启后必须原样。"""
    log = _student_log_fixture()
    first = Database(db_path)
    first.migrate()
    save_student_log(Repositories(first), log, course_id="course-1")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        loaded = load_student_log(reopened, "stu-1", course_id="course-1")
        assert loaded.all_events() == log.all_events()
        assert [
            e.sequence for e in loaded.events_for("course-1", "kp-1")
        ] == [0, 1, 2, 3]
        assert [e.event_type for e in loaded.events_for("course-1", "kp-1")] == [
            LearningEventType.VIEWED,
            LearningEventType.ANSWERED,
            LearningEventType.ANSWERED,
            LearningEventType.REVIEWED,
        ]
    finally:
        second.close()


def test_student_log_round_trip_state_matches_domain_derivation(db_path):
    """重启后的状态必须与领域层自己归约出来的结果**逐字段相同**。"""
    log = _student_log_fixture()
    first = Database(db_path)
    first.migrate()
    save_student_log(Repositories(first), log, course_id="course-1")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        loaded = load_student_log(reopened, "stu-1", course_id="course-1")
        for kp_id in ("kp-1", "kp-2"):
            assert loaded.derive_state("course-1", kp_id).to_dict() == (
                log.derive_state("course-1", kp_id).to_dict()
            )
        assert loaded.derive_state("course-1", "kp-1").correct_count == 1
        assert loaded.derive_state("course-1", "kp-1").incorrect_count == 1
    finally:
        second.close()


def test_student_log_round_trip_keeps_students_isolated(db_path):
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    save_student_log(repos, _student_log_fixture(), course_id="course-1")
    other = StudentLearningLog(Student.create("stu-2"))
    other.register_knowledge_point("course-1", "kp-1")
    other.record_answer("course-1", "kp-1", is_correct=True)
    save_student_log(repos, other, course_id="course-1")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        stu1 = load_student_log(reopened, "stu-1", course_id="course-1")
        stu2 = load_student_log(reopened, "stu-2", course_id="course-1")
        assert len(stu1.all_events()) == 5
        assert len(stu2.all_events()) == 1
        assert stu2.derive_state("course-1", "kp-1").correct_count == 1
        assert stu1.derive_state("course-1", "kp-2").correct_count == 1
    finally:
        second.close()


def test_student_log_round_trip_keeps_courses_isolated(db_path):
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    log = StudentLearningLog(Student.create("stu-1"))
    log.register_knowledge_point("course-1", "kp-1")
    log.record_answer("course-1", "kp-1", is_correct=True)
    log.register_knowledge_point("course-2", "kp-1")  # 同 id, 不同课程
    log.record_answer("course-2", "kp-1", is_correct=False)
    save_student_log(repos, log, course_id="course-1")
    save_student_log(repos, log, course_id="course-2")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        course1 = load_student_log(reopened, "stu-1", course_id="course-1")
        assert course1.derive_state("course-1", "kp-1").correct_count == 1
        assert course1.derive_state("course-1", "kp-1").incorrect_count == 0
        assert len(course1.all_events()) == 1

        course2 = load_student_log(reopened, "stu-1", course_id="course-2")
        assert course2.derive_state("course-2", "kp-1").correct_count == 0
        assert course2.derive_state("course-2", "kp-1").incorrect_count == 1
    finally:
        second.close()


def test_loading_an_unknown_student_returns_none(db_path):
    database = Database(db_path)
    database.migrate()
    try:
        assert load_student_log(Repositories(database), "ghost", course_id="c") is None
    finally:
        database.close()


# ======================================================================
# 全链路整体往返 —— 一层都不能丢
# ======================================================================


def test_full_pipeline_round_trip_keeps_every_layer(db_path):
    """材料 -> 证据 -> 知识点 -> 评审 -> 学生 -> 练习 -> 作答 -> 评估 -> 计划 -> 路径。

    这是 Task 42 的**总验收**: 整条证据链一起落库, 重启后逐层比对。
    任何一层丢了, 这个测试都会红。
    """
    # ---- 组装 (全部在内存里, 用真实领域对象) ----
    store = EvidenceStore()
    store.add(_multilingual_evidence("e-1", "Una función es una relación."))
    store.add(_multilingual_evidence("e-2", "Una funció és una relació."))
    store.retire("e-2")

    knowledge = KnowledgeStructure()
    knowledge.add_knowledge_point(_kp("kp-1", evidence_refs=["e-1"]))
    knowledge.add_knowledge_point(_kp("kp-2", evidence_refs=["e-1"]))
    knowledge.add_relationship(
        Relationship(
            source_id="kp-1",
            target_id="kp-2",
            relation_type=RelationType.PRE_REQUSITE_OF,
            evidence_refs=["e-1"],
            relationship_id="rel-1",
        )
    )
    knowledge.add_review_record(
        ReviewRecord(
            review_id="r-01",
            knowledge_point_id="kp-1",
            decision=ReviewDecision.CONFIRM,
            selected_evidence_ids=("e-1",),
        )
    )

    student_log = _student_log_fixture()

    exercise = Exercise.create(
        "course-1",
        ExerciseType.MULTIPLE_CHOICE,
        "¿Qué es una función?",
        ("kp-1",),
        choices=(Choice("a", "Una relación"), Choice("b", "Un número")),
        correct_choice_id="a",
        evidence_ids=("e-1",),
        difficulty=1,
    )
    answer = StudentAnswer.create("stu-1", exercise.exercise_id, "a", 0)
    evaluation = ExactEvaluator({exercise.exercise_id: exercise}).evaluate(answer)

    plan = _real_study_plan()
    path = LearningPath(
        status=PathStatus.OK,
        target_knowledge_point_id="kp-2",
        node_ids=("kp-1", "kp-2"),
        cycle_node_ids=(),
    )

    # ---- 落库 ----
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    with repos.transaction():
        repos.courses.save(_course())
        repos.sessions.save(_session())
        repos.materials.register_with_status(
            _material_record("material-1"), _processing_record("material-1", "PROCESSED")
        )
        save_evidence_store(repos, store)
        save_knowledge_structure(repos, knowledge, course_id="course-1")
        save_student_log(repos, student_log, course_id="course-1")
        repos.exercises.save(exercise)
        repos.answers.save(
            answer, course_id="course-1", submitted_at="2026-03-03T09:00:00+00:00"
        )
        repos.evaluations.save(
            evaluation, exercise_id=exercise.exercise_id, student_id="stu-1"
        )
        repos.study_plans.save(plan)
        repos.learning_paths.save(path, course_id="course-1", student_id="stu-1")
    rows_before = repos.total_rows()
    first.close()

    # ---- 重启并逐层比对 ----
    second, reopened = _reopen(db_path)
    try:
        assert reopened.total_rows() == rows_before

        # 1. 材料 + 处理状态
        assert reopened.materials.load_record("material-1")["filename"] == "clase1.mp3"
        assert reopened.material_processing.status_of("material-1") == "PROCESSED"

        # 2. 证据 (含状态与顺序)
        assert load_evidence_store(reopened).to_dict() == store.to_dict()

        # 3. 知识点 + 溯源 + 关系 + 评审
        loaded_knowledge = load_knowledge_structure(reopened, course_id="course-1")
        assert _normalise_structure(loaded_knowledge.to_dict()) == _normalise_structure(
            knowledge.to_dict()
        )
        assert loaded_knowledge.get_knowledge_point("kp-1").evidence_refs == ["e-1"]

        # 4. 学生状态
        loaded_log = load_student_log(reopened, "stu-1", course_id="course-1")
        assert loaded_log.all_events() == student_log.all_events()
        assert loaded_log.derive_state("course-1", "kp-1").to_dict() == (
            student_log.derive_state("course-1", "kp-1").to_dict()
        )

        # 5. 练习 + 作答 + 评估
        assert reopened.exercises.load(exercise.exercise_id).to_dict() == exercise.to_dict()
        assert reopened.exercises.evidence_ids_for(exercise.exercise_id) == ["e-1"]
        assert reopened.answers.load(answer.answer_id).to_dict() == answer.to_dict()
        assert reopened.answers.submitted_at_for(answer.answer_id) == (
            "2026-03-03T09:00:00+00:00"
        )
        loaded_eval = reopened.evaluations.for_answer(answer.answer_id)
        assert loaded_eval.to_dict() == evaluation.to_dict()
        assert loaded_eval.status is evaluation.status

        # 6. 计划 + 路径
        assert reopened.study_plans.load(plan.plan_id).to_dict() == plan.to_dict()
        assert reopened.learning_paths.node_ids("course-1", "stu-1", "kp-2") == [
            "kp-1",
            "kp-2",
        ]

        # 7. 库本身仍然健康
        assert reopened.database.is_healthy() is True
    finally:
        second.close()


def test_full_pipeline_survives_two_restarts_unchanged(db_path):
    """第二次重启和第一次结果必须**逐字节相同** —— 存储是幂等的。"""
    store = EvidenceStore()
    store.add(_multilingual_evidence("e-1", "Contenido estable"))

    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    with repos.transaction():
        repos.courses.save(_course())
        save_evidence_store(repos, store)
        repos.knowledge.save(_kp("kp-1", evidence_refs=["e-1"]), course_id="course-1")
    snapshot_a = repos.evidence.snapshot()
    first.close()

    second, reopened = _reopen(db_path)
    snapshot_b = reopened.evidence.snapshot()
    second.close()

    third, again = _reopen(db_path)
    try:
        snapshot_c = again.evidence.snapshot()
        assert snapshot_b == snapshot_a
        assert snapshot_c == snapshot_a
    finally:
        third.close()


def test_resaving_loaded_data_does_not_duplicate_anything(db_path):
    """读回来再整体存一遍 —— 幂等: 行数不变。"""
    store = EvidenceStore()
    store.add(_multilingual_evidence("e-1", "Uno"))
    store.add(_multilingual_evidence("e-2", "Dos"))

    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    save_evidence_store(repos, store)
    before = repos.total_rows()
    first.close()

    second, reopened = _reopen(db_path)
    try:
        again = load_evidence_store(reopened)
        save_evidence_store(reopened, again)
        assert reopened.total_rows() == before
        assert load_evidence_store(reopened).to_dict() == store.to_dict()
    finally:
        second.close()


def test_total_row_count_matches_the_sum_of_repository_counts(db_path):
    first = Database(db_path)
    first.migrate()
    repos = Repositories(first)
    repos.courses.save(_course())
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")
    repos.students.save(Student.create("stu-1"), course_id="course-1")
    first.close()

    second, reopened = _reopen(db_path)
    try:
        counts = reopened.counts()
        assert reopened.total_rows() == sum(counts.values())
    finally:
        second.close()
