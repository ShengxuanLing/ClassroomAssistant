# -*- coding: utf-8 -*-
"""Task 42 —— 事务与原子性测试。

规范原文: "关键操作必须 atomic。例如 Material registration + processing
status 不能写一半。"

这里用**真实的失败注入**证明这一点, 而不是只测"成功路径也成功":

- 材料注册 + 处理状态: 第二步非法 -> 第一步也不落库;
- 跨仓储: 中途抛异常 -> 三个仓储的写入全部消失;
- 嵌套: 内层回滚不影响外层, 内层提交被外层回滚吞掉;
- 推迟外键: 允许乱序批量写入, 但提交时**仍然**拒绝悬挂引用。
"""

import sqlite3

import pytest

from src.models import Course
from src.persistence.errors import (
    DuplicateRecordError,
    PersistenceValidationError,
    TransactionError,
)
from src.persistence.repositories import Repositories
from src.persistence.repositories.material import (
    MaterialProcessingRepository,
    MaterialRepository,
)

from tests.test_persistence_repositories import (
    _course,
    _evidence,
    _kp,
    _material_record,
    _processing_record,
    _session,
)


# ----------------------------------------------------------------------
# 规范点名的 atomic 对: 材料注册 + 处理状态
# ----------------------------------------------------------------------


def test_material_registration_and_status_are_written_together(repos):
    repos.materials.register_with_status(
        _material_record("m1"), _processing_record("m1", "REGISTERED")
    )
    assert repos.materials.count() == 1
    assert repos.material_processing.count() == 1
    assert repos.material_processing.status_of("m1") == "REGISTERED"


def test_material_registration_is_rolled_back_when_status_is_invalid(repos):
    """**核心原子性测试**: 第二步失败 -> 第一步不留痕。"""
    with pytest.raises(ValueError):
        repos.materials.register_with_status(
            _material_record("m1"),
            {"material_id": "m1"},  # 缺 processing_status -> 第二步炸
        )
    assert repos.materials.count() == 0
    assert repos.materials.exists("m1") is False
    assert repos.material_processing.count() == 0


def test_material_registration_is_rolled_back_when_status_violates_fk(repos):
    """第二步违反外键 (指向不存在的材料) -> 第一步也不留痕。"""
    with pytest.raises(PersistenceValidationError):
        repos.materials.register_with_status(
            _material_record("m1"),
            _processing_record("some-other-material", "REGISTERED"),
        )
    assert repos.materials.count() == 0
    assert repos.material_processing.count() == 0


def test_no_half_written_material_status_pair(repos):
    """任何失败路径下都不存在"有材料没状态"或"有状态没材料"。"""
    for attempt in range(3):
        with pytest.raises((ValueError, PersistenceValidationError)):
            repos.materials.register_with_status(
                _material_record(f"m{attempt}"),
                {"material_id": f"m{attempt}"},
            )
    assert repos.materials.count() == 0
    assert repos.material_processing.count() == 0


def test_successful_pair_survives_a_restart(db_path):
    from src.persistence.database import Database

    database = Database(db_path)
    database.migrate()
    repos = Repositories(database)
    repos.materials.register_with_status(
        _material_record("m1"), _processing_record("m1", "PROCESSED")
    )
    database.close()

    reopened = Database(db_path)
    reopened.migrate()
    assert Repositories(reopened).material_processing.status_of("m1") == "PROCESSED"
    reopened.close()


# ----------------------------------------------------------------------
# 跨仓储原子性
# ----------------------------------------------------------------------


def test_cross_repository_rollback_discards_every_write(repos):
    repos.evidence.save(_evidence("e1"))
    repos.knowledge.save(_kp("kp-1"), course_id="course-1")

    with pytest.raises(RuntimeError):
        with repos.transaction():
            repos.courses.save(_course())
            repos.sessions.save(_session())
            repos.knowledge.save(_kp("kp-2", evidence_refs=["e1"]), course_id="course-1")
            raise RuntimeError("boom")

    assert repos.courses.count() == 0
    assert repos.sessions.count() == 0
    assert repos.knowledge.count() == 1
    assert repos.evidence.count() == 1


def test_cross_repository_commit_persists_every_write(repos):
    repos.evidence.save(_evidence("e1"))
    with repos.transaction():
        repos.courses.save(_course())
        repos.sessions.save(_session())
        repos.knowledge.save(_kp("kp-1", evidence_refs=["e1"]), course_id="course-1")
    assert repos.courses.count() == 1
    assert repos.sessions.count() == 1
    assert repos.knowledge.count() == 1


def test_repository_methods_work_without_an_explicit_transaction(repos):
    """仓储方法自己不开事务 -> 单条调用自动提交。"""
    repos.courses.save(_course())
    assert repos.courses.count() == 1


def test_explicit_begin_and_commit(repos):
    repos.database.begin()
    repos.courses.save(_course())
    repos.database.commit()
    assert repos.courses.count() == 1


def test_explicit_begin_and_rollback(repos):
    repos.database.begin()
    repos.courses.save(_course())
    repos.database.rollback()
    assert repos.courses.count() == 0


def test_nested_inner_rollback_keeps_outer_writes(repos):
    with repos.transaction():
        repos.courses.save(_course())
        with pytest.raises(RuntimeError):
            with repos.transaction():
                repos.sessions.save(_session())
                raise RuntimeError("inner")
    assert repos.courses.count() == 1
    assert repos.sessions.count() == 0


def test_nested_inner_commit_is_discarded_by_outer_rollback(repos):
    with pytest.raises(RuntimeError):
        with repos.transaction():
            with repos.transaction():
                repos.courses.save(_course())
            raise RuntimeError("outer")
    assert repos.courses.count() == 0


def test_deeply_nested_rollback_only_affects_its_own_level(repos):
    with repos.transaction():
        repos.courses.save(_course())
        with repos.transaction():
            repos.sessions.save(_session())
            with pytest.raises(RuntimeError):
                with repos.transaction():
                    repos.evidence.save(_evidence("e1"))
                    raise RuntimeError("deep")
    assert repos.courses.count() == 1
    assert repos.sessions.count() == 1
    assert repos.evidence.count() == 0


def test_failed_transaction_leaves_the_database_usable(repos):
    with pytest.raises(RuntimeError):
        with repos.transaction():
            repos.courses.save(_course())
            raise RuntimeError("boom")
    # 之后仍能正常写入
    with repos.transaction():
        repos.courses.save(_course())
    assert repos.courses.count() == 1


# ----------------------------------------------------------------------
# 推迟外键
# ----------------------------------------------------------------------


def test_deferred_foreign_keys_allow_out_of_order_inserts(repos):
    """整体恢复时插入顺序无法天然满足所有外键 -> 推迟到提交时校验。"""
    with repos.deferred_foreign_keys():
        repos.database.execute(
            "INSERT INTO sessions (session_id, course_id, payload) VALUES (?, ?, ?)",
            ("session-1", "course-1", "{}"),
        )
        repos.database.execute(
            "INSERT INTO courses (course_id, payload) VALUES (?, ?)",
            ("course-1", "{}"),
        )
    assert repos.courses.count() == 1
    assert repos.sessions.count() == 1


def test_deferred_foreign_keys_still_reject_dangling_references(repos):
    """推迟检查**不是**关闭检查: 悬挂引用在提交时依然被拒绝。"""
    with pytest.raises(TransactionError):
        with repos.deferred_foreign_keys():
            repos.database.execute(
                "INSERT INTO sessions (session_id, course_id, payload) "
                "VALUES (?, ?, ?)",
                ("session-1", "course-missing", "{}"),
            )
    assert repos.sessions.count() == 0


def test_failed_commit_does_not_leave_a_hidden_transaction(repos):
    """COMMIT 失败后连接必须干净 —— 否则后续写入会被卷进一个永不提交的事务。"""
    with pytest.raises(TransactionError):
        with repos.deferred_foreign_keys():
            repos.database.execute(
                "INSERT INTO sessions (session_id, course_id, payload) "
                "VALUES (?, ?, ?)",
                ("session-1", "course-missing", "{}"),
            )
    assert repos.database.in_transaction is False
    # 之后写入必须真的落库
    repos.courses.save(_course())
    assert repos.courses.count() == 1


# ----------------------------------------------------------------------
# 事务状态错误
# ----------------------------------------------------------------------


def test_commit_without_begin_is_a_transaction_error(repos):
    with pytest.raises(TransactionError):
        repos.database.commit()


def test_rollback_without_begin_is_a_transaction_error(repos):
    with pytest.raises(TransactionError):
        repos.database.rollback()


def test_transaction_error_carries_a_storage_code(repos):
    with pytest.raises(TransactionError) as excinfo:
        repos.database.commit()
    assert excinfo.value.code == "STORAGE_TRANSACTION_FAILED"
    assert excinfo.value.code.startswith("STORAGE")


# ----------------------------------------------------------------------
# 删除也遵守事务
# ----------------------------------------------------------------------


def test_delete_is_rolled_back(repos):
    repos.courses.save(_course())
    with pytest.raises(RuntimeError):
        with repos.transaction():
            repos.courses.delete("course-1")
            raise RuntimeError("boom")
    assert repos.courses.exists("course-1") is True


def test_cascade_delete_is_rolled_back(repos):
    repos.courses.save(_course())
    repos.sessions.save(_session())
    with pytest.raises(RuntimeError):
        with repos.transaction():
            repos.courses.delete("course-1")
            raise RuntimeError("boom")
    assert repos.sessions.count() == 1


def test_standalone_repositories_share_the_same_database(db):
    """两个仓储实例指向同一个库, 事务对它们同时生效。"""
    first = MaterialRepository(db)
    second = MaterialProcessingRepository(db)
    with db.transaction():
        first.save_record(_material_record("m1"))
        second.save(_processing_record("m1", "REGISTERED"))
    assert first.count() == 1
    assert second.count() == 1


def test_material_repository_exposes_processing_repository(repos):
    assert isinstance(repos.materials.processing, MaterialProcessingRepository)
    assert isinstance(repos.materials, MaterialRepository)


def test_foreign_keys_are_still_enabled_after_a_transaction(repos):
    with repos.transaction():
        repos.courses.save(_course())
    assert repos.database.scalar("PRAGMA foreign_keys") == 1


def test_integrity_is_intact_after_many_rollbacks(repos):
    for _ in range(5):
        with pytest.raises(RuntimeError):
            with repos.transaction():
                repos.courses.save(_course())
                repos.sessions.save(_session())
                raise RuntimeError("boom")
    assert repos.database.is_healthy() is True
    assert repos.courses.count() == 0


def test_unique_constraint_violation_inside_a_transaction_rolls_back(repos):
    """UNIQUE 冲突映射为 DuplicateRecordError (→ CONFLICT), 并整段回滚。

    注意: ``PersistenceValidationError`` 是它的**兄弟类**, 不是父类 ——
    UNIQUE/PK 冲突走 DUPLICATE_RECORD, 只有 NOT NULL / FK / CHECK 才是
    INVALID_INPUT。断言必须精确到具体异常, 否则错误码映射会悄悄漂移。
    """
    repos.evidence.save(_evidence("e1"))
    with pytest.raises(DuplicateRecordError):
        with repos.transaction():
            repos.evidence.save(_evidence("e2"))  # 同 canonical key
    assert repos.evidence.count() == 1


def test_raw_sql_constraint_violation_rolls_back_without_translation(repos):
    """绕过仓储直接执行 SQL 时, 约束错误保持原生 ``sqlite3`` 形态。

    翻译成结构化错误是**仓储层**的职责, Database 层不越权替上层决定
    语义。这里同时确认: 即便异常是原生的, 事务依然整段回滚。
    """
    with pytest.raises(sqlite3.IntegrityError):
        with repos.transaction():
            repos.courses.save(_course())
            repos.database.execute(
                "INSERT INTO students (student_id, course_id, display_name, payload, "
                "payload_version) VALUES (?, ?, ?, ?, ?)",
                ("stu-x", "course-1", "Sin payload", None, 1),
            )
    assert repos.courses.count() == 0
    assert repos.students.count() == 0
    assert repos.database.in_transaction is False


def test_foreign_key_violation_through_a_repository_is_an_input_error(repos):
    """经仓储写入的外键违约映射为 PersistenceValidationError (→ INVALID_INPUT)。"""
    repos.materials.save_record(_material_record("m1"))
    with pytest.raises(PersistenceValidationError):
        with repos.transaction():
            repos.materials.evidence_links.replace("m1", ["evidence-missing"])
    assert repos.materials.evidence_links.count() == 0
    assert repos.materials.count() == 1


def test_integrity_error_inside_a_transaction_leaves_no_open_transaction(repos):
    repos.evidence.save(_evidence("e1"))
    with pytest.raises(DuplicateRecordError):
        with repos.transaction():
            repos.evidence.save(_evidence("e2"))
    assert repos.database.in_transaction is False
    # 之后仍能正常写入 —— 失败的事务没有留下隐藏的开放事务。
    repos.evidence.save(_evidence("e3", content="Otro texto distinto"))
    assert repos.evidence.count() == 2


def test_sqlite_error_inside_transaction_propagates(repos):
    with pytest.raises(sqlite3.OperationalError):
        with repos.transaction():
            repos.database.execute("SELECT * FROM no_such_table")
    assert repos.database.in_transaction is False
