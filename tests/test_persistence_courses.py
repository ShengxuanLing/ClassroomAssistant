# -*- coding: utf-8 -*-
"""Task 49 — Course / Session / Material 持久化接线。

这一层验证的是 spec 的这句话::

    创建课程 -> Application Service -> Repository -> SQLite
             -> 关闭 -> 重启 -> 数据恢复

以及 49.7 点名的重点:

- Course 创建后必须落盘, 重启后必须还在;
- 身份必须是**确定性**的 (重启不许换 id);
- Session -> Course 引用必须有效 (悬空 = 错误, 不是数据);
- Material 必须保存全部溯源字段 (material_id / course_id / session_id /
  filename / extension / content hash / storage path / source type /
  processing status);
- 重启后 Material -> storage path -> **实际文件**必须成立;
- 文件丢失必须报 ``MATERIAL_FILE_MISSING``, 而不是静默消失;
- 重复注册必须仍然只有 1 条 Material。

与 Task 48 的分工: ``test_persistence_workspace.py`` 管生命周期 (开库 /
迁移 / 关闭), 本文件管**业务对象**是否真的进了库、能不能拿回来。
"""

from __future__ import annotations

import hashlib
import os
import shutil

import pytest

from src.application.errors import StorageError
from src.application.persistence_wiring import default_database_path
from src.application.workspace import Workspace
from src.persistence import open_database


# ======================================================================
# 工具
# ======================================================================


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _count(data_dir: str, table: str, where: str = "", params=()) -> int:
    """直接用一条**独立连接**数表里的行数。

    刻意不走 Workspace 的 API: 断言"确实进了库"必须绕过内存, 否则
    "内存里有、库里没有"这种最严重的失败会被测试自己掩盖掉。
    """
    database = open_database(default_database_path(data_dir), migrate=False)
    try:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(database.scalar(sql, params, default=0) or 0)
    finally:
        database.close()


@pytest.fixture
def data_dir(tmp_path) -> str:
    return str(tmp_path / "data")


@pytest.fixture
def source_dir(tmp_path) -> str:
    path = str(tmp_path / "uploads")
    os.makedirs(path, exist_ok=True)
    return path


@pytest.fixture
def workspace(data_dir):
    instance = Workspace(data_dir)
    yield instance
    instance.close()


# ======================================================================
# 49.1 Course 持久化
# ======================================================================


class TestCoursePersistence:
    def test_a_course_is_written_to_sqlite_immediately(self, workspace, data_dir) -> None:
        """写穿: ``create_course`` 返回时数据库里就必须已经有这一行。

        不是"关闭时才统一 flush" —— 进程可能被强杀, 那时什么都没了。
        """
        course = workspace.create_course("Álgebra", "ALG", "es")
        assert _count(data_dir, "courses") == 1
        assert _count(data_dir, "courses", "course_id = ?", (course["course_id"],)) == 1

    def test_a_course_survives_a_restart(self, data_dir) -> None:
        first = Workspace(data_dir)
        course = first.create_course("Álgebra", "ALG", "es")
        first.close()

        second = Workspace(data_dir)
        try:
            assert [c["course_id"] for c in second.list_courses()] == [
                course["course_id"]
            ]
        finally:
            second.close()

    def test_the_course_id_is_stable_across_restarts(self, data_dir) -> None:
        """身份是内容寻址的: 重启不许换 id (换了 id 等于换了一门课)。"""
        first = Workspace(data_dir)
        before = first.create_course("Álgebra", "ALG", "es")["course_id"]
        first.close()

        second = Workspace(data_dir)
        try:
            assert second.list_courses()[0]["course_id"] == before
            assert second.get_course(before)["course_id"] == before
        finally:
            second.close()

    def test_all_course_fields_round_trip(self, data_dir) -> None:
        first = Workspace(data_dir)
        before = first.create_course(
            "Cálculo", "CAL", "ca", metadata={"room": "A-12", "credits": 6}
        )
        first.close()

        second = Workspace(data_dir)
        try:
            after = second.list_courses()[0]
            # 逐字段比对"创建时返回的 DTO"与"重启后读回的 DTO":
            # 这才是真正的不变量 —— 重启不该让任何一个字段变样。
            assert after == before
        finally:
            second.close()

    def test_the_iso_language_alias_is_resolved_once_and_stays_resolved(
        self, data_dir
    ) -> None:
        """``ca`` 是 ISO 别名, 领域层的规范形式是 ``Catalan``。

        别名只允许在**输入边界**被解析一次。存进去、读回来都必须已经是
        规范形式, 否则同一个库会被不同入口解释成不同语言。
        """
        first = Workspace(data_dir)
        created = first.create_course("Cálculo", "CAL", "ca")
        assert created["language"] == "Catalan"
        first.close()

        second = Workspace(data_dir)
        try:
            assert second.list_courses()[0]["language"] == "Catalan"
        finally:
            second.close()

    def test_two_courses_are_both_persisted_and_isolated(self, data_dir) -> None:
        first = Workspace(data_dir)
        a = first.create_course("Álgebra", "ALG", "es")
        b = first.create_course("Historia", "HIS", "ca")
        assert a["course_id"] != b["course_id"]
        first.close()

        second = Workspace(data_dir)
        try:
            ids = sorted(c["course_id"] for c in second.list_courses())
            assert ids == sorted([a["course_id"], b["course_id"]])
        finally:
            second.close()

    def test_creating_the_same_course_twice_keeps_one_row(self, workspace, data_dir) -> None:
        first = workspace.create_course("Álgebra", "ALG", "es")
        again = workspace.create_course("Álgebra", "ALG", "es")
        assert again["course_id"] == first["course_id"]
        assert _count(data_dir, "courses") == 1
        assert len(workspace.list_courses()) == 1

    def test_an_update_is_persisted(self, data_dir) -> None:
        first = Workspace(data_dir)
        course = first.create_course("Álgebra", "ALG", "es")
        first.update_course(course["course_id"], name="Álgebra Lineal")
        first.close()

        second = Workspace(data_dir)
        try:
            assert second.get_course(course["course_id"])["name"] == "Álgebra Lineal"
        finally:
            second.close()

    def test_an_update_is_written_through_before_close(self, workspace, data_dir) -> None:
        course = workspace.create_course("Álgebra", "ALG", "es")
        workspace.update_course(course["course_id"], name="Álgebra Lineal")
        database = open_database(default_database_path(data_dir), migrate=False)
        try:
            payload = database.scalar(
                "SELECT payload FROM courses WHERE course_id = ?",
                (course["course_id"],),
            )
        finally:
            database.close()
        assert "Álgebra Lineal" in payload

    def test_the_registry_and_the_database_agree(self, data_dir) -> None:
        first = Workspace(data_dir)
        first.create_course("Álgebra", "ALG", "es")
        first.close()

        second = Workspace(data_dir)
        try:
            assert len(second.list_courses()) == _count(data_dir, "courses")
        finally:
            second.close()

    def test_an_unknown_course_is_not_invented_on_restart(self, data_dir) -> None:
        from src.application.errors import NotFoundError

        workspace = Workspace(data_dir)
        try:
            with pytest.raises(NotFoundError):
                workspace.get_course("course-does-not-exist")
        finally:
            workspace.close()


# ======================================================================
# 49.2 Session 持久化
# ======================================================================


class TestSessionPersistence:
    def test_a_session_is_written_to_sqlite_immediately(self, workspace, data_dir) -> None:
        course = workspace.create_course("Álgebra", "ALG", "es")
        workspace.create_session(course["course_id"], session_number=1, title="Tema 1")
        assert _count(data_dir, "sessions") == 1

    def test_sessions_survive_a_restart(self, data_dir) -> None:
        first = Workspace(data_dir)
        course = first.create_course("Álgebra", "ALG", "es")
        for number in (1, 2, 3):
            first.create_session(course["course_id"], session_number=number)
        first.close()

        second = Workspace(data_dir)
        try:
            assert len(second.list_sessions(course["course_id"])) == 3
        finally:
            second.close()

    def test_a_session_still_points_at_a_real_course_after_restart(self, data_dir) -> None:
        """Session -> Course 的引用必须有效 —— 这是 49 明确要求的不变量。"""
        first = Workspace(data_dir)
        course = first.create_course("Álgebra", "ALG", "es")
        session = first.create_session(course["course_id"], session_number=1)
        first.close()

        second = Workspace(data_dir)
        try:
            restored = second.list_sessions(course["course_id"])[0]
            assert restored["session_id"] == session["session_id"]
            assert restored["course_id"] == course["course_id"]
            # 课程确实还在 (不是悬空引用)。
            assert second.get_course(restored["course_id"])["course_id"] == course["course_id"]
        finally:
            second.close()

    def test_a_session_referencing_an_unknown_course_is_rejected(self) -> None:
        """悬空的 Session -> Course 是**错误**, 必须在加载时报出来。

        在 ``CourseService`` 这一层直接验证 (组合根就是这么调它的): 只给
        课堂、不给课程 —— 加载必须报 ``StorageError``, 不许静默丢弃课堂,
        也不许凭空造出一门课。
        """
        from src.application.course_service import CourseService
        from src.models import ClassSession

        service = CourseService()
        orphan = ClassSession(course_id="course-nope", session_number=1)
        with pytest.raises(StorageError) as exc:
            service.load_state(courses=[], sessions=[orphan])
        assert exc.value.code == "STORAGE_ERROR"
        assert "unknown course" in str(exc.value)

    def test_deleting_a_course_row_does_not_leave_a_phantom_session(
        self, data_dir
    ) -> None:
        """数据库侧的自洽: 课程行被删掉时, 课堂由外键级联一起走。

        这一条测的是"库自己不会处在半截状态": 重新打开后既没有课程, 也没有
        悬空课堂 —— 因此不存在"重启后冒出一堂没有课的课"。
        """
        first = Workspace(data_dir)
        course = first.create_course("Álgebra", "ALG", "es")
        first.create_session(course["course_id"], session_number=1)
        first.close()

        database = open_database(default_database_path(data_dir))
        try:
            database.execute(
                "DELETE FROM courses WHERE course_id = ?", (course["course_id"],)
            )
            assert int(database.scalar("SELECT COUNT(*) FROM sessions", (), default=0)) == 0
        finally:
            database.close()

        second = Workspace(data_dir)
        try:
            assert second.list_courses() == []
            assert second.list_sessions() == []
        finally:
            second.close()

    def test_the_session_id_is_stable_across_restarts(self, data_dir) -> None:
        first = Workspace(data_dir)
        course = first.create_course("Álgebra", "ALG", "es")
        session = first.create_session(course["course_id"], session_number=7)
        first.close()

        second = Workspace(data_dir)
        try:
            assert second.list_sessions(course["course_id"])[0]["session_id"] == session["session_id"]
        finally:
            second.close()

    def test_sessions_come_back_in_a_deterministic_order(self, data_dir) -> None:
        first = Workspace(data_dir)
        course = first.create_course("Álgebra", "ALG", "es")
        for number in (3, 1, 2):
            first.create_session(course["course_id"], session_number=number)
        first.close()

        orders = []
        for _ in range(2):
            workspace = Workspace(data_dir)
            try:
                orders.append(
                    [s["session_number"] for s in workspace.list_sessions(course["course_id"])]
                )
            finally:
                workspace.close()
        assert orders[0] == [1, 2, 3]
        assert orders[0] == orders[1]

    def test_a_session_carries_its_date_and_title(self, data_dir) -> None:
        first = Workspace(data_dir)
        course = first.create_course("Álgebra", "ALG", "es")
        first.create_session(
            course["course_id"], session_number=1, date="2026-02-10", title="Derivadas"
        )
        first.close()

        second = Workspace(data_dir)
        try:
            session = second.list_sessions(course["course_id"])[0]
            assert session["date"] == "2026-02-10"
            assert session["title"] == "Derivadas"
        finally:
            second.close()

    def test_sessions_of_two_courses_do_not_mix(self, data_dir) -> None:
        first = Workspace(data_dir)
        a = first.create_course("Álgebra", "ALG", "es")
        b = first.create_course("Historia", "HIS", "ca")
        first.create_session(a["course_id"], session_number=1)
        first.create_session(b["course_id"], session_number=1)
        first.close()

        second = Workspace(data_dir)
        try:
            assert len(second.list_sessions(a["course_id"])) == 1
            assert len(second.list_sessions(b["course_id"])) == 1
            assert len(second.list_sessions()) == 2
            assert (
                second.list_sessions(a["course_id"])[0]["course_id"] == a["course_id"]
            )
        finally:
            second.close()

    def test_creating_the_same_session_twice_keeps_one_row(self, workspace, data_dir) -> None:
        course = workspace.create_course("Álgebra", "ALG", "es")
        first = workspace.create_session(course["course_id"], session_number=1)
        again = workspace.create_session(course["course_id"], session_number=1)
        assert again["session_id"] == first["session_id"]
        assert _count(data_dir, "sessions") == 1


# ======================================================================
# 49.3 Material 注册与字段完整性
# ======================================================================


class TestMaterialRegistration:
    def test_a_material_is_written_to_sqlite_immediately(
        self, workspace, source_dir, data_dir
    ) -> None:
        course = workspace.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        workspace.register_material(course["course_id"], path)
        assert _count(data_dir, "materials") == 1

    def test_every_traceability_field_is_persisted(
        self, workspace, source_dir
    ) -> None:
        """spec 逐字点名的一组字段, 一个都不许少。"""
        course = workspace.create_course("Álgebra", "ALG", "es")
        session = workspace.create_session(course["course_id"], session_number=1)
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = workspace.register_material(
            course["course_id"], path, session_id=session["session_id"]
        )

        assert record["material_id"]
        assert record["course_id"] == course["course_id"]
        assert record["session_id"] == session["session_id"]
        assert record["filename"] == "apuntes.txt"
        assert record["extension"] == ".txt"
        assert record["content_hash"] == _sha256(path)
        assert record["stored_path"] and os.path.isfile(record["stored_path"])
        assert record["source_type"] == "note"
        assert record["processing_status"] == "REGISTERED"

    def test_every_traceability_field_survives_a_restart(self, data_dir, source_dir) -> None:
        first = Workspace(data_dir)
        course = first.create_course("Álgebra", "ALG", "es")
        session = first.create_session(course["course_id"], session_number=1)
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        before = first.register_material(
            course["course_id"], path, session_id=session["session_id"]
        )
        first.close()

        second = Workspace(data_dir)
        try:
            after = second.list_materials(course["course_id"])[0]
            for field in (
                "material_id",
                "course_id",
                "session_id",
                "filename",
                "extension",
                "content_hash",
                "source_type",
                "processing_status",
                "relative_path",
            ):
                assert after[field] == before[field], field
        finally:
            second.close()

    def test_the_stored_file_is_reachable_after_a_restart(self, data_dir, source_dir) -> None:
        """49 的核心要求: Material -> storage path -> **实际文件**。"""
        first = Workspace(data_dir)
        course = first.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        first.register_material(course["course_id"], path)
        first.close()

        second = Workspace(data_dir)
        try:
            record = second.list_materials(course["course_id"])[0]
            assert os.path.isfile(record["stored_path"])
            with open(record["stored_path"], encoding="utf-8") as handle:
                assert handle.read() == "Tema 1\n"
        finally:
            second.close()

    def test_the_stored_file_hash_still_matches_after_a_restart(
        self, data_dir, source_dir
    ) -> None:
        first = Workspace(data_dir)
        course = first.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        first.register_material(course["course_id"], path)
        first.close()

        second = Workspace(data_dir)
        try:
            record = second.list_materials(course["course_id"])[0]
            assert _sha256(record["stored_path"]) == record["content_hash"]
            assert second.material_integrity(verify_hash=True)["ok"] is True
        finally:
            second.close()

    def test_the_original_upload_is_never_used_as_the_storage_location(
        self, workspace, source_dir
    ) -> None:
        """材料必须被复制进受管 data_dir, 绝不就地引用用户原文件。"""
        course = workspace.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = workspace.register_material(course["course_id"], path)
        assert os.path.abspath(record["stored_path"]) != os.path.abspath(path)
        assert os.path.abspath(record["stored_path"]).startswith(
            os.path.abspath(workspace.data_dir)
        )

    def test_deleting_the_original_upload_does_not_break_the_material(
        self, workspace, source_dir
    ) -> None:
        course = workspace.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = workspace.register_material(course["course_id"], path)
        os.remove(path)
        assert os.path.isfile(record["stored_path"])
        assert workspace.material_integrity()["ok"] is True

    def test_a_batch_registration_persists_every_accepted_file(
        self, workspace, source_dir, data_dir
    ) -> None:
        course = workspace.create_course("Álgebra", "ALG", "es")
        paths = [
            _write(os.path.join(source_dir, f"nota-{i}.txt"), f"Tema {i}\n")
            for i in range(3)
        ]
        report = workspace.register_material_batch(course["course_id"], paths)
        assert report["accepted"] == 3
        assert _count(data_dir, "materials") == 3

    def test_a_rejected_file_creates_no_material_row(
        self, workspace, source_dir, data_dir
    ) -> None:
        """被拒绝的文件**没有** material_id, 因此不该留下任何材料行。

        拒绝是一种**响应**, 不是一条材料: 它没有存储副本、没有内容哈希,
        强行写一行"空材料"只会污染注册表。真正的要求是"不许留下半截数据"
        —— 所以这里断言库里干干净净。
        """
        course = workspace.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "raro.xyz"), "???\n")
        record = workspace.register_material(course["course_id"], path)
        assert record["processing_status"] == "FAILED"
        assert record["error"] == "UNSUPPORTED_EXTENSION"
        assert record["material_id"] is None
        assert _count(data_dir, "materials") == 0
        assert _count(data_dir, "material_processing") == 0
        assert workspace.list_materials(course["course_id"]) == []

    def test_a_rejection_does_not_stop_the_accepted_files_in_a_batch(
        self, workspace, source_dir, data_dir
    ) -> None:
        """批量注册里被拒的那一个不许拖累其他文件, 也不许在库里留垃圾。"""
        course = workspace.create_course("Álgebra", "ALG", "es")
        good = _write(os.path.join(source_dir, "bueno.txt"), "Tema 1\n")
        bad = _write(os.path.join(source_dir, "malo.xyz"), "???\n")
        report = workspace.register_material_batch(course["course_id"], [good, bad])
        assert report["accepted"] == 1
        assert report["rejected"] == 1
        assert _count(data_dir, "materials") == 1
        assert len(workspace.list_materials(course["course_id"])) == 1

    def test_the_processing_status_is_persisted(self, workspace, source_dir, data_dir) -> None:
        course = workspace.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = workspace.register_material(course["course_id"], path)
        workspace.validate_material(course["course_id"], record["material_id"])

        database = open_database(default_database_path(data_dir), migrate=False)
        try:
            rows = database.query(
                "SELECT material_id, processing_status FROM material_processing"
            )
        finally:
            database.close()
        assert len(rows) == 1
        assert rows[0]["material_id"] == record["material_id"]
        assert rows[0]["processing_status"] in ("REGISTERED", "VALIDATING")

    def test_a_material_can_be_registered_into_a_specific_session(
        self, data_dir, source_dir
    ) -> None:
        first = Workspace(data_dir)
        course = first.create_course("Álgebra", "ALG", "es")
        session = first.create_session(course["course_id"], session_number=2)
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 2\n")
        first.register_material(
            course["course_id"], path, session_id=session["session_id"]
        )
        first.close()

        second = Workspace(data_dir)
        try:
            per_session = second.list_materials(
                course["course_id"], session_id=session["session_id"]
            )
            assert len(per_session) == 1
            assert per_session[0]["session_id"] == session["session_id"]
        finally:
            second.close()

    def test_materials_of_two_courses_do_not_mix(self, data_dir, source_dir) -> None:
        first = Workspace(data_dir)
        a = first.create_course("Álgebra", "ALG", "es")
        b = first.create_course("Historia", "HIS", "ca")
        first.register_material(
            a["course_id"], _write(os.path.join(source_dir, "a.txt"), "A\n")
        )
        first.register_material(
            b["course_id"], _write(os.path.join(source_dir, "b.txt"), "B\n")
        )
        first.close()

        second = Workspace(data_dir)
        try:
            assert len(second.list_materials(a["course_id"])) == 1
            assert len(second.list_materials(b["course_id"])) == 1
            assert (
                second.list_materials(a["course_id"])[0]["course_id"] == a["course_id"]
            )
        finally:
            second.close()

    def test_the_content_hash_matches_the_file_on_disk(self, workspace, source_dir) -> None:
        course = workspace.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "contenido\n")
        record = workspace.register_material(course["course_id"], path)
        assert record["content_hash"] == _sha256(record["stored_path"])

    def test_the_relative_path_points_inside_the_data_dir(self, workspace, source_dir) -> None:
        course = workspace.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = workspace.register_material(course["course_id"], path)
        assert not os.path.isabs(record["relative_path"])
        resolved = os.path.join(workspace.data_dir, *record["relative_path"].split("/"))
        assert os.path.isfile(resolved)

    def test_a_material_is_reachable_from_its_course_and_session(
        self, workspace, source_dir
    ) -> None:
        course = workspace.create_course("Álgebra", "ALG", "es")
        session = workspace.create_session(course["course_id"], session_number=1)
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = workspace.register_material(
            course["course_id"], path, session_id=session["session_id"]
        )
        got = workspace.get_material(course["course_id"], record["material_id"])
        assert got["material_id"] == record["material_id"]
        assert got["session_id"] == session["session_id"]


def _stored_files(data_dir: str) -> list[str]:
    found: list[str] = []
    for root, _dirs, files in os.walk(data_dir):
        for name in files:
            if name.endswith(".sqlite") or name.endswith(".json"):
                continue
            if name.endswith((".txt", ".md", ".pdf", ".docx", ".png", ".mp3")):
                found.append(os.path.join(root, name))
    return found


# ======================================================================
# 49.4 重复注册的幂等性
# ======================================================================


class TestDuplicateRegistration:
    def test_registering_the_same_file_twice_keeps_one_material(
        self, workspace, source_dir, data_dir
    ) -> None:
        course = workspace.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        first = workspace.register_material(course["course_id"], path)
        again = workspace.register_material(course["course_id"], path)
        assert again["material_id"] == first["material_id"]
        assert again["duplicate"] is True
        assert _count(data_dir, "materials") == 1

    def test_the_duplicate_flag_survives_a_restart(self, data_dir, source_dir) -> None:
        first = Workspace(data_dir)
        course = first.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = first.register_material(course["course_id"], path)
        first.close()

        second = Workspace(data_dir)
        try:
            again = second.register_material(course["course_id"], path)
            assert again["material_id"] == record["material_id"]
            assert again["duplicate"] is True
            assert len(second.list_materials(course["course_id"])) == 1
        finally:
            second.close()

    def test_a_third_registration_still_keeps_one_material(
        self, workspace, source_dir, data_dir
    ) -> None:
        course = workspace.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        for _ in range(3):
            workspace.register_material(course["course_id"], path)
        assert _count(data_dir, "materials") == 1
        assert len(workspace.list_materials(course["course_id"])) == 1

    def test_the_same_content_under_a_different_name_reuses_the_copy(
        self, workspace, source_dir
    ) -> None:
        """同一内容、不同文件名: 记录是两条, 但**存储副本复用**。"""
        course = workspace.create_course("Álgebra", "ALG", "es")
        first = workspace.register_material(
            course["course_id"], _write(os.path.join(source_dir, "a.txt"), "mismo\n")
        )
        second = workspace.register_material(
            course["course_id"], _write(os.path.join(source_dir, "b.txt"), "mismo\n")
        )
        assert first["material_id"] != second["material_id"]
        assert second["duplicate_of"] == first["material_id"]
        assert second["stored_path"] == first["stored_path"]
        assert second["content_hash"] == first["content_hash"]

    def test_a_duplicate_does_not_create_a_second_stored_file(
        self, workspace, source_dir, data_dir
    ) -> None:
        course = workspace.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        workspace.register_material(course["course_id"], path)
        workspace.register_material(course["course_id"], path)
        assert len(_stored_files(data_dir)) == 1

    def test_the_same_content_in_another_course_is_a_separate_material(
        self, data_dir, source_dir
    ) -> None:
        """内容寻址是**课程内**的: 两门课各有一份, 互不覆盖。"""
        workspace = Workspace(data_dir)
        a = workspace.create_course("Álgebra", "ALG", "es")
        b = workspace.create_course("Historia", "HIS", "ca")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "compartido\n")
        first = workspace.register_material(a["course_id"], path)
        second = workspace.register_material(b["course_id"], path)
        assert first["material_id"] != second["material_id"]
        assert first["stored_path"] != second["stored_path"]
        workspace.close()


# ======================================================================
# 49.5 文件丢失的诊断 (MATERIAL_FILE_MISSING)
# ======================================================================


class TestMaterialFileIntegrity:
    def test_a_healthy_workspace_reports_no_findings(
        self, workspace, source_dir
    ) -> None:
        course = workspace.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        workspace.register_material(course["course_id"], path)
        report = workspace.material_integrity()
        assert report["ok"] is True
        assert report["checked"] == 1
        assert report["findings"] == []

    def test_a_missing_file_is_reported_as_material_file_missing(
        self, workspace, source_dir
    ) -> None:
        """spec: 文件丢失必须报 ``MATERIAL_FILE_MISSING``。"""
        course = workspace.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = workspace.register_material(course["course_id"], path)
        os.remove(record["stored_path"])

        report = workspace.material_integrity()
        assert report["ok"] is False
        assert report["counts"]["missing"] == 1
        finding = report["findings"][0]
        assert finding["diagnostic"] == "MATERIAL_FILE_MISSING"
        assert finding["material_id"] == record["material_id"]
        assert finding["course_id"] == course["course_id"]
        assert finding["filename"] == "apuntes.txt"

    def test_a_missing_file_does_not_delete_the_record(
        self, workspace, source_dir, data_dir
    ) -> None:
        """静默消失是本阶段最不可接受的失败 —— 记录必须留着。"""
        course = workspace.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = workspace.register_material(course["course_id"], path)
        os.remove(record["stored_path"])

        assert len(workspace.list_materials(course["course_id"])) == 1
        assert _count(data_dir, "materials") == 1
        assert workspace.get_material(course["course_id"], record["material_id"])[
            "material_id"
        ] == record["material_id"]

    def test_a_missing_file_is_still_reported_after_a_restart(
        self, data_dir, source_dir
    ) -> None:
        first = Workspace(data_dir)
        course = first.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = first.register_material(course["course_id"], path)
        os.remove(record["stored_path"])
        first.close()

        second = Workspace(data_dir)
        try:
            assert len(second.list_materials(course["course_id"])) == 1
            report = second.material_integrity()
            assert report["counts"]["missing"] == 1
            assert report["findings"][0]["diagnostic"] == "MATERIAL_FILE_MISSING"
        finally:
            second.close()

    def test_a_missing_file_makes_health_degraded(self, workspace, source_dir) -> None:
        course = workspace.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = workspace.register_material(course["course_id"], path)
        assert workspace.health()["status"] == "ok"

        os.remove(record["stored_path"])
        health = workspace.health()
        assert health["status"] == "degraded"
        assert health["storage"]["materials"]["missing"] == 1
        assert health["storage"]["materials"]["ok"] is False

    def test_a_changed_file_is_reported_as_a_hash_mismatch(
        self, workspace, source_dir
    ) -> None:
        course = workspace.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = workspace.register_material(course["course_id"], path)
        _write(record["stored_path"], "contenido adulterado\n")

        assert workspace.material_integrity()["ok"] is True  # 默认不读内容
        report = workspace.material_integrity(verify_hash=True)
        assert report["ok"] is False
        assert report["counts"]["hash_mismatch"] == 1
        assert report["findings"][0]["diagnostic"] == "MATERIAL_HASH_MISMATCH"
        assert report["findings"][0]["expected_hash"] == record["content_hash"]

    def test_the_integrity_check_reads_the_database_not_memory(
        self, data_dir, source_dir
    ) -> None:
        """刚打开、还没为任何课程建立上下文时也必须能查出来。

        只查内存注册表会把"文件丢了"报成"一切正常" —— 那正是静默失败。
        """
        first = Workspace(data_dir)
        course = first.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = first.register_material(course["course_id"], path)
        os.remove(record["stored_path"])
        first.close()

        second = Workspace(data_dir)
        try:
            assert second._contexts == {}  # 还没有任何课程上下文
            report = second.material_integrity()
            assert report["checked"] == 1
            assert report["counts"]["missing"] == 1
        finally:
            second.close()

    def test_a_record_without_any_storage_location_is_reported(
        self, data_dir, source_dir
    ) -> None:
        first = Workspace(data_dir)
        course = first.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = first.register_material(course["course_id"], path)
        first.close()

        import json

        database = open_database(default_database_path(data_dir))
        try:
            payload = json.loads(
                database.scalar(
                    "SELECT payload FROM materials WHERE material_id = ?",
                    (record["material_id"],),
                )
            )
            payload["stored_path"] = None
            payload["relative_path"] = None
            database.execute(
                "UPDATE materials SET payload = ?, stored_path = NULL, relative_path = NULL "
                "WHERE material_id = ?",
                (json.dumps(payload), record["material_id"]),
            )
        finally:
            database.close()

        second = Workspace(data_dir)
        try:
            report = second.material_integrity()
            assert report["counts"]["path_unknown"] == 1
            assert report["findings"][0]["diagnostic"] == "MATERIAL_PATH_UNKNOWN"
        finally:
            second.close()

    def test_the_integrity_check_is_ok_on_an_empty_workspace(self, data_dir) -> None:
        workspace = Workspace(data_dir)
        try:
            report = workspace.material_integrity()
            assert report["ok"] is True
            assert report["checked"] == 0
            assert report["findings"] == []
        finally:
            workspace.close()

    def test_the_integrity_check_covers_every_course(self, data_dir, source_dir) -> None:
        workspace = Workspace(data_dir)
        a = workspace.create_course("Álgebra", "ALG", "es")
        b = workspace.create_course("Historia", "HIS", "ca")
        first = workspace.register_material(
            a["course_id"], _write(os.path.join(source_dir, "a.txt"), "A\n")
        )
        workspace.register_material(
            b["course_id"], _write(os.path.join(source_dir, "b.txt"), "B\n")
        )
        os.remove(first["stored_path"])

        report = workspace.material_integrity()
        assert report["checked"] == 2
        assert report["counts"]["missing"] == 1
        assert report["findings"][0]["course_id"] == a["course_id"]
        workspace.close()


# ======================================================================
# 49.6 存储位置的可移植性 (data_dir 搬迁 / 备份恢复到别的目录)
# ======================================================================


class TestPortableStoragePaths:
    def test_moving_the_data_directory_keeps_materials_reachable(
        self, tmp_path, source_dir
    ) -> None:
        """从备份恢复到**另一个目录**之后, 绝对路径必然失效 —— 必须自动重解析。

        这是 Task 54 backup drill 的真实场景: 恢复出来的库不可能记得原来
        的绝对路径, 但它记得 ``relative_path``。
        """
        original = str(tmp_path / "original")
        first = Workspace(original)
        course = first.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = first.register_material(course["course_id"], path)
        first.close()

        moved = str(tmp_path / "restored")
        shutil.move(original, moved)

        second = Workspace(moved)
        try:
            restored = second.list_materials(course["course_id"])[0]
            assert restored["material_id"] == record["material_id"]
            assert os.path.isfile(restored["stored_path"])
            assert second.material_integrity()["ok"] is True
        finally:
            second.close()

    def test_the_resolved_path_points_into_the_new_directory(
        self, tmp_path, source_dir
    ) -> None:
        original = str(tmp_path / "original")
        first = Workspace(original)
        course = first.create_course("Álgebra", "ALG", "es")
        first.register_material(
            course["course_id"],
            _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n"),
        )
        first.close()

        moved = str(tmp_path / "restored")
        shutil.move(original, moved)

        second = Workspace(moved)
        try:
            stored = second.list_materials(course["course_id"])[0]["stored_path"]
            assert os.path.abspath(stored).startswith(os.path.abspath(moved))
        finally:
            second.close()

    def test_the_identity_is_not_recomputed_when_paths_are_re_resolved(
        self, tmp_path, source_dir
    ) -> None:
        """重解析只改存储位置, **绝不改身份** (改了身份 = 溯源链断裂)。"""
        original = str(tmp_path / "original")
        first = Workspace(original)
        course = first.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        before = first.register_material(course["course_id"], path)
        first.close()

        moved = str(tmp_path / "restored")
        shutil.move(original, moved)

        second = Workspace(moved)
        try:
            after = second.list_materials(course["course_id"])[0]
            assert after["material_id"] == before["material_id"]
            assert after["content_hash"] == before["content_hash"]
            assert after["relative_path"] == before["relative_path"]
        finally:
            second.close()

    def test_validation_still_passes_after_the_directory_moved(
        self, tmp_path, source_dir
    ) -> None:
        """路径没重解析的话, 这里会因为 stored_path 失效而报 STORAGE_ERROR。"""
        original = str(tmp_path / "original")
        first = Workspace(original)
        course = first.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = first.register_material(course["course_id"], path)
        first.close()

        moved = str(tmp_path / "restored")
        shutil.move(original, moved)

        second = Workspace(moved)
        try:
            validated = second.validate_material(
                course["course_id"], record["material_id"]
            )
            assert validated["error"] is None
            assert validated["processing_status"] in ("REGISTERED", "VALIDATING")
        finally:
            second.close()

    def test_registering_a_duplicate_still_works_after_the_directory_moved(
        self, tmp_path, source_dir
    ) -> None:
        original = str(tmp_path / "original")
        first = Workspace(original)
        course = first.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = first.register_material(course["course_id"], path)
        first.close()

        moved = str(tmp_path / "restored")
        shutil.move(original, moved)

        second = Workspace(moved)
        try:
            again = second.register_material(course["course_id"], path)
            assert again["material_id"] == record["material_id"]
            assert again["duplicate"] is True
            assert len(second.list_materials(course["course_id"])) == 1
        finally:
            second.close()

    def test_a_genuinely_lost_file_is_still_reported_after_a_move(
        self, tmp_path, source_dir
    ) -> None:
        """搬迁不是万能的: 文件真的没了就必须报 missing, 不许假装没事。"""
        original = str(tmp_path / "original")
        first = Workspace(original)
        course = first.create_course("Álgebra", "ALG", "es")
        path = _write(os.path.join(source_dir, "apuntes.txt"), "Tema 1\n")
        record = first.register_material(course["course_id"], path)
        os.remove(record["stored_path"])
        first.close()

        moved = str(tmp_path / "restored")
        shutil.move(original, moved)

        second = Workspace(moved)
        try:
            report = second.material_integrity()
            assert report["counts"]["missing"] == 1
        finally:
            second.close()
