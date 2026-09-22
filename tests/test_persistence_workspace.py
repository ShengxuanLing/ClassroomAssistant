# -*- coding: utf-8 -*-
"""Task 48 — Persistent Workspace Bootstrap。

这一层验证的是**生命周期**, 不是某个业务对象:

::

    Workspace(data_dir)
        ↓  open database
        ↓  migrate
        ↓  initialize repositories
        ↓  load persistent state
        ↓  initialize services

    Workspace.close()
        ↓  close database

重点覆盖 spec 48.7 点名的边界: 首次启动 / 第二次启动 / 空库 / 已有库 /
schema 迁移 / close / reopen / 损坏的库 / 幂等初始化。

为什么"损坏的库"是这里最重要的一条
--------------------------------------------------------------------

规范第十三节把下面这种行为列为明确禁止::

    corrupt DB -> create empty DB -> 用户以为数据没了

所以本文件里那条测试断言的是"必须抛错", 而不是"必须能打开"。
"""

from __future__ import annotations

import os
import pathlib

import pytest

from src.application.errors import StorageError
from src.application.persistence_wiring import (
    WorkspacePersistence,
    default_database_path,
)
from src.application.workspace import (
    APPLICATION_NAME,
    APPLICATION_VERSION,
    Workspace,
)
from src.models import Course
from src.persistence import open_database
from src.persistence.database import Database
from src.persistence.migrations import latest_version
from src.persistence.migrations.m001_initial_schema import MIGRATION_001
from src.persistence.repositories import Repositories


def _db_path(data_dir: str) -> str:
    return default_database_path(data_dir)


# ======================================================================
# 48.4 首次启动
# ======================================================================


class TestFirstStartup:
    def test_empty_directory_boots_without_error(self, tmp_path) -> None:
        workspace = Workspace(str(tmp_path / "data"))
        try:
            assert workspace.list_courses() == []
            assert workspace.list_sessions() == []
        finally:
            workspace.close()

    def test_boot_creates_the_database_file(self, tmp_path) -> None:
        data_dir = tmp_path / "data"
        workspace = Workspace(str(data_dir))
        workspace.close()
        assert os.path.isfile(_db_path(str(data_dir)))

    def test_boot_creates_the_full_directory_layout(self, tmp_path) -> None:
        data_dir = tmp_path / "data"
        workspace = Workspace(str(data_dir))
        workspace.close()
        for name in (
            "materials",
            "audio",
            "images",
            "documents",
            "database",
            "logs",
            "backups",
            "temp",
        ):
            assert (data_dir / name).is_dir(), f"缺少目录 {name}"

    def test_boot_migrates_to_the_latest_schema(self, tmp_path) -> None:
        workspace = Workspace(str(tmp_path / "data"))
        try:
            assert workspace.persistence is not None
            assert workspace.persistence.schema_version() >= 1
        finally:
            workspace.close()

    def test_boot_creates_every_business_table(self, tmp_path) -> None:
        workspace = Workspace(str(tmp_path / "data"))
        try:
            tables = set(workspace.persistence.table_names())
        finally:
            workspace.close()
        for expected in (
            "courses",
            "sessions",
            "materials",
            "material_processing",
            "evidence",
            "knowledge_points",
            "knowledge_point_evidence",
            "relationships",
            "conflicts",
            "review_records",
            "topics",
            "students",
            "learning_events",
            "student_knowledge_state",
            "exercises",
            "student_answers",
            "evaluation_results",
            "study_plans",
            "learning_paths",
        ):
            assert expected in tables, f"缺少表 {expected}"

    def test_fresh_database_is_healthy(self, tmp_path) -> None:
        workspace = Workspace(str(tmp_path / "data"))
        try:
            assert workspace.persistence.integrity_check() in (True, "ok")
            assert workspace.persistence.foreign_key_violations() == []
        finally:
            workspace.close()


# ======================================================================
# 48.5 已有数据库 / 48.3 restart
# ======================================================================


class TestSecondStartup:
    def test_second_workspace_sees_the_first_ones_data(self, tmp_path) -> None:
        data_dir = str(tmp_path / "data")
        first = Workspace(data_dir)
        created = first.create_course("Àlgebra Lineal", "ALG-1", "es")
        first.close()

        second = Workspace(data_dir)
        try:
            courses = second.list_courses()
            assert [c["course_id"] for c in courses] == [created["course_id"]]
            assert courses[0]["name"] == "Àlgebra Lineal"
            assert courses[0]["language"] == "Spanish"
        finally:
            second.close()

    def test_restart_preserves_sessions_and_their_course_link(self, tmp_path) -> None:
        data_dir = str(tmp_path / "data")
        first = Workspace(data_dir)
        course = first.create_course("Estructura de Dades", "EDA", "ca")
        session = first.create_session(
            course["course_id"], session_number=3, date="2026-09-17", title="Tema 3"
        )
        first.close()

        second = Workspace(data_dir)
        try:
            sessions = second.list_sessions(course["course_id"])
            assert [s["session_id"] for s in sessions] == [session["session_id"]]
            assert sessions[0]["course_id"] == course["course_id"]
            assert sessions[0]["session_number"] == 3
            assert sessions[0]["title"] == "Tema 3"
        finally:
            second.close()

    def test_reopening_an_existing_database_does_not_duplicate_rows(self, tmp_path) -> None:
        data_dir = str(tmp_path / "data")
        first = Workspace(data_dir)
        first.create_course("Repetida", "REP", "es")
        first.close()

        for _ in range(3):
            workspace = Workspace(data_dir)
            workspace.close()
        final = Workspace(data_dir)
        try:
            assert len(final.list_courses()) == 1
            assert final.persistence.counts()["courses"] == 1
        finally:
            final.close()

    def test_metadata_survives_a_restart(self, tmp_path) -> None:
        data_dir = str(tmp_path / "data")
        first = Workspace(data_dir)
        first.create_course("Meta", "META", "es", {"room": "A-12", "credits": 6})
        first.close()

        second = Workspace(data_dir)
        try:
            assert second.list_courses()[0]["metadata"] == {"room": "A-12", "credits": 6}
        finally:
            second.close()


# ======================================================================
# 48.6 迁移与 bootstrap 顺序
# ======================================================================


class TestMigrationOrder:
    def test_load_happens_after_migrate(self, tmp_path) -> None:
        """顺序必须是 open -> migrate -> load。

        做法: 造一个**只应用到 001** 的旧库 (真实用户从 0.35.0 升上来的
        样子), 用真实仓储写进一门课, 然后交给 ``Workspace`` 打开。

        - 若加载发生在迁移**之前**: 读 ``database_meta`` /
          ``material_processing.last_attempt_at`` 会撞 "no such table /
          no such column" —— 打开就炸。
        - 若迁移发生在加载**之后**: 旧库里那门课读不回来 —— 数据消失。

        两条都不许发生, 所以这个测试同时钉住了顺序和数据完整性。
        """
        data_dir = str(tmp_path / "data")
        db_path = _db_path(data_dir)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        # --- 造一个停在 schema 001 的旧库, 并写进真实业务数据 ---
        legacy = Database(db_path)
        assert legacy.migrate([MIGRATION_001]) == 1
        assert legacy.schema_version() == 1
        assert legacy.has_table("database_meta") is False
        repos = Repositories(legacy)
        course = Course(name="Migración", code="MIG", language="es")
        repos.courses.save(course)
        legacy.close()

        # --- 用 Workspace 打开: 必须先迁移, 再加载 ---
        workspace = Workspace(data_dir)
        try:
            # 不写死 2: 迁移链会随版本变长 (Task 68 加了 003)。
            # 要钉的是"打开后已经迁移到最新", 以及"旧数据一条都没丢"。
            assert workspace.persistence.schema_version() == latest_version()
            assert workspace.persistence.database.has_table("database_meta") is True
            assert [c["course_id"] for c in workspace.list_courses()] == [
                course.course_id
            ]
            assert workspace.list_courses()[0]["name"] == "Migración"
        finally:
            workspace.close()

    def test_migration_is_read_only_on_an_up_to_date_database(self, tmp_path) -> None:
        """``migrate()`` 在最新版库上不许申请写锁。

        做法: 打开一个长事务 (只读事务也持有读锁), 然后在新连接上迁移 ——
        如果迁移发的是 ``CREATE TABLE IF NOT EXISTS`` (一条写语句), 这里会
        撞 ``busy_timeout`` 然后失败。本项目是浏览器轮询的本地服务, 不能
        容忍"打开数据库"被任何长事务卡住。
        """
        data_dir = str(tmp_path / "data")
        Workspace(data_dir).close()

        holder = open_database(_db_path(data_dir))
        try:
            holder.begin()
            try:
                reopened = WorkspacePersistence.open(_db_path(data_dir))
                try:
                    assert reopened.schema_version() >= 1
                finally:
                    reopened.close()
            finally:
                holder.rollback()
        finally:
            holder.close()

    def test_database_lives_inside_the_data_dir(self, tmp_path) -> None:
        data_dir = tmp_path / "data"
        workspace = Workspace(str(data_dir))
        try:
            assert str(workspace.database_path).startswith(str(data_dir.resolve()))
        finally:
            workspace.close()


# ======================================================================
# 48.2 关闭
# ======================================================================


class TestClose:
    def test_close_is_idempotent(self, tmp_path) -> None:
        workspace = Workspace(str(tmp_path / "data"))
        workspace.close()
        workspace.close()
        assert workspace.closed is True

    def test_close_releases_the_database_file(self, tmp_path) -> None:
        """关闭后数据库文件必须可以被删除 (证明连接真的释放了)。"""
        data_dir = tmp_path / "data"
        workspace = Workspace(str(data_dir))
        workspace.create_course("Cerrable", "CLOSE", "es")
        workspace.close()
        os.unlink(_db_path(str(data_dir)))
        assert not os.path.exists(_db_path(str(data_dir)))

    def test_workspace_is_usable_as_a_context_manager(self, tmp_path) -> None:
        data_dir = str(tmp_path / "data")
        with Workspace(data_dir) as workspace:
            workspace.create_course("Contexto", "CTX", "es")
        reopened = Workspace(data_dir)
        try:
            assert len(reopened.list_courses()) == 1
        finally:
            reopened.close()

    def test_closing_does_not_touch_an_injected_database(self, tmp_path) -> None:
        """组合根复用连接时, Workspace 不许把别人的连接关掉。"""
        data_dir = tmp_path / "data"
        database = open_database(_db_path(str(data_dir)))
        store = WorkspacePersistence.for_database(database)
        workspace = Workspace(str(data_dir), persistence=store)
        try:
            workspace.create_course("Compartida", "SHARED", "es")
            workspace.close()
            assert database.closed is False
        finally:
            database.close()


# ======================================================================
# 48.7 幂等初始化 / 无效输入 / 损坏的库
# ======================================================================


class TestRobustness:
    def test_creating_the_same_course_twice_across_restarts_is_idempotent(
        self, tmp_path
    ) -> None:
        data_dir = str(tmp_path / "data")
        first = Workspace(data_dir)
        course = first.create_course("Idempotente", "IDEM", "es")
        first.close()

        second = Workspace(data_dir)
        try:
            again = second.create_course("Idempotente", "IDEM", "es")
            assert again["course_id"] == course["course_id"]
            assert len(second.list_courses()) == 1
        finally:
            second.close()

    def test_corrupt_database_raises_instead_of_creating_an_empty_one(
        self, tmp_path
    ) -> None:
        """规范明令禁止: 损坏的库 -> 新建空库 -> 用户以为数据没了。"""
        data_dir = tmp_path / "data"
        database = pathlib.Path(_db_path(str(data_dir)))
        database.parent.mkdir(parents=True, exist_ok=True)
        database.write_bytes(b"this is definitely not a sqlite database" * 8)

        with pytest.raises(StorageError) as exc:
            Workspace(str(data_dir))
        assert exc.value.code == "STORAGE_ERROR"
        # 关键: 原文件必须原样留在磁盘上, 没有被"顺手"替换成空库。
        assert database.read_bytes().startswith(b"this is definitely not a sqlite")

    def test_corrupt_database_error_names_the_path(self, tmp_path) -> None:
        data_dir = tmp_path / "data"
        database = pathlib.Path(_db_path(str(data_dir)))
        database.parent.mkdir(parents=True, exist_ok=True)
        database.write_bytes(b"\x00" * 4096)

        with pytest.raises(StorageError) as exc:
            Workspace(str(data_dir))
        assert str(database) in str(exc.value.detail["database_path"])

    def test_invalid_data_dir_is_rejected(self) -> None:
        from src.application.errors import InvalidInputError

        with pytest.raises(InvalidInputError):
            Workspace("   ")

    def test_invalid_persistence_argument_is_rejected(self, tmp_path) -> None:
        from src.application.errors import InvalidInputError

        with pytest.raises(InvalidInputError):
            Workspace(str(tmp_path / "data"), persistence="yes-please")

    def test_persistence_can_be_explicitly_disabled(self, tmp_path) -> None:
        """显式关闭持久化是合法的 (纯内存单元测试), 但不许静默发生。"""
        data_dir = tmp_path / "data"
        workspace = Workspace(str(data_dir), persistence=False)
        try:
            workspace.create_course("Solo Memoria", "MEM", "es")
            assert workspace.persistence is None
            assert workspace.database_path is None
            assert not os.path.isfile(_db_path(str(data_dir)))
        finally:
            workspace.close()

        reopened = Workspace(str(data_dir))
        try:
            assert reopened.list_courses() == []
        finally:
            reopened.close()

    def test_two_workspaces_on_the_same_directory_do_not_corrupt_each_other(
        self, tmp_path
    ) -> None:
        """两个工作区同时打开同一个库: 写入必须可见, 且不产生重复行。"""
        data_dir = str(tmp_path / "data")
        first = Workspace(data_dir)
        second = Workspace(data_dir)
        try:
            course = first.create_course("Compartida", "TWO", "es")
            again = second.create_course("Compartida", "TWO", "es")
            assert again["course_id"] == course["course_id"]
            assert len(second.list_courses()) == 1
        finally:
            first.close()
            second.close()

        final = Workspace(data_dir)
        try:
            assert len(final.list_courses()) == 1
        finally:
            final.close()


# ======================================================================
# 健康报告
# ======================================================================


class TestHealthReporting:
    def test_health_reports_sqlite_as_the_backend(self, tmp_path) -> None:
        workspace = Workspace(str(tmp_path / "data"))
        try:
            health = workspace.health()
        finally:
            workspace.close()
        assert health["database"]["backend"] == "sqlite"
        assert health["database"]["ok"] is True
        assert health["status"] == "ok"

    def test_health_reports_the_schema_version(self, tmp_path) -> None:
        workspace = Workspace(str(tmp_path / "data"))
        try:
            health = workspace.health()
        finally:
            workspace.close()
        assert health["database"]["schema_version"] >= 1

    def test_health_reports_business_object_counts(self, tmp_path) -> None:
        workspace = Workspace(str(tmp_path / "data"))
        try:
            workspace.create_course("Salud", "HEALTH", "es")
            health = workspace.health()
        finally:
            workspace.close()
        assert health["database"]["business_objects"]["courses"] == 1

    def test_health_says_memory_only_when_persistence_is_disabled(self, tmp_path) -> None:
        workspace = Workspace(str(tmp_path / "data"), persistence=False)
        try:
            health = workspace.health()
        finally:
            workspace.close()
        assert health["database"]["backend"] == "memory-only"
        assert health["database"]["path"] is None

    def test_health_reports_application_identity(self, tmp_path) -> None:
        workspace = Workspace(str(tmp_path / "data"))
        try:
            health = workspace.health()
        finally:
            workspace.close()
        assert health["application"] == APPLICATION_NAME
        assert health["version"] == APPLICATION_VERSION
