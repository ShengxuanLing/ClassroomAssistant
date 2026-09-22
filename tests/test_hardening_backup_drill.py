# -*- coding: utf-8 -*-
"""Task 54.5 / 47.12 — Backup Restore Drill (真实演练)。

spec 原文::

    完整执行:
        create course / add materials / process / generate knowledge / review /
        create student / submit answer / create backup / destroy working database /
        restore backup / restart application
    最终必须恢复:
        courses / sessions / materials / evidence / knowledge / review history /
        students / answers / evaluations / study plans / learning paths

历史
--------------------------------------------------------------------

Task 47.12 时这个演练是**分裂**的: ``TestRestorableNow`` 只验证材料侧,
``TestDrillGap`` 把"业务对象拿不回来"钉成断言 —— 因为那时 Course /
KnowledgePoint / ReviewRecord / Student / Answer / Evaluation / StudyPlan /
LearningPath 全都只活在内存里 (Task 44 的 Known limitation)。

Task 48-55 把业务对象真正接进 SQLite 之后, ``TestDrillGap`` 按设计失败了。
按 spec 55.14 的要求, 这里把它**改写为真正的 reload PASS 验收** (不是删除,
也不是降低标准): 同一个演练现在必须端到端成立。

演练的真实性
--------------------------------------------------------------------

- 工作数据库是**真的被销毁**的 (关闭工作区 -> 删掉 ``.sqlite`` / ``-wal`` /
  ``-shm``), 而不是"假装销毁";
- 恢复后的数据是从**归档**里读出来的, 不是从内存里重放的;
- 验证走的是 ``Workspace`` 的公开 API (与用户看到的是同一入口)。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil

import pytest

from src.application.acceptance import AcceptanceHarness, ClassroomDataset
from src.application.workspace import Workspace
from src.backup.service import BackupService

DATASET_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "acceptance"


@pytest.fixture(scope="module")
def dataset() -> ClassroomDataset:
    return ClassroomDataset.from_directory(str(DATASET_DIR))


@pytest.fixture(scope="module")
def drill(tmp_path_factory, dataset):
    """跑完整条业务流水线 -> 备份 -> **毁掉工作数据库** -> 从归档恢复。

    返回演练的全部证据 (harness / 归档路径 / 恢复目录 / 材料字节)。
    """
    root = tmp_path_factory.mktemp("drill")
    data_dir = root / "classroom-data"
    restore_dir = root / "restored"

    harness = AcceptanceHarness(dataset, data_dir=str(data_dir))
    harness.run()
    assert harness.knowledge_ids, "演练前提: 流水线必须产出知识点"

    # 记录原始材料文件的字节, 供恢复后逐字节比对。
    original_files: dict[str, bytes] = {}
    for tree in ("audio", "documents", "images"):
        base = data_dir / tree
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                original_files[str(path.relative_to(data_dir))] = path.read_bytes()
    assert original_files, "演练前提: 必须有落盘的材料文件"

    # 关闭工作区**之前**把所有"恢复前"的快照抓下来 —— 关掉之后内存状态就
    # 不可用了, 而"恢复后 == 恢复前"正是本演练要证明的东西。
    before = _snapshot(harness)

    service = BackupService(str(data_dir), application_version="0.47.0")
    result = service.create_backup(label="drill", overwrite=True)
    archive_path = str(result.archive_path)
    assert os.path.isfile(archive_path)

    # spec: destroy working database。
    #
    # 必须先关闭工作区: 活库被打开时 Windows 不允许删除文件 (WinError 5),
    # 而且"边写边删"本身也不是一个真实的故障场景。关闭 = 应用退出。
    harness.workspace.close()
    destroyed: list[str] = []
    for candidate in sorted(data_dir.rglob("*.sqlite*")):
        if candidate.is_file():
            candidate.unlink()
            destroyed.append(str(candidate.relative_to(data_dir)))
    assert destroyed, "演练前提: 必须真的有一个工作数据库被销毁"

    restore_dir.mkdir(parents=True, exist_ok=True)
    target = BackupService(str(restore_dir))
    restored = target.restore_backup(archive_path)

    return {
        "harness": harness,
        "archive_path": archive_path,
        "restore_dir": restore_dir,
        "restored": restored,
        "original_files": original_files,
        "course_id": harness.course_id,
        "session_id": harness.session_id,
        "student_id": harness.student_id,
        "knowledge_ids": list(harness.knowledge_ids),
        "destroyed": destroyed,
        "before": before,
    }


def _snapshot(harness: AcceptanceHarness) -> dict[str, object]:
    """把"恢复前"的业务状态抓成一份可比较的快照 (关闭工作区之前调用)。"""
    workspace = harness.workspace
    course_id = harness.course_id
    return {
        "courses": [c["course_id"] for c in workspace.list_courses()],
        "sessions": [s["session_id"] for s in workspace.list_sessions(course_id)],
        "materials": sorted(
            str(record["material_id"])
            for record in workspace.list_materials(course_id)
            if record.get("material_id")
        ),
        "knowledge_ids": sorted(
            p["knowledge_id"] for p in workspace.knowledge_points(course_id)
        ),
        "review_history": {
            knowledge_id: [
                (r["review_id"], r["decision"])
                for r in workspace.review_history(course_id, knowledge_id)
            ]
            for knowledge_id in sorted(harness.knowledge_ids)
        },
        "student_state": workspace.student_state(course_id, harness.student_id),
        "evaluation": workspace.get_evaluation(course_id, harness.answer_id),
        "study_plan_id": workspace.study_plan(course_id, harness.student_id)["plan_id"],
        "learning_path": workspace.learning_path(
            course_id, sorted(harness.knowledge_ids)[0]
        ),
        "material_evidence": {
            str(record["material_id"]): sorted(
                row["evidence_id"]
                for row in workspace.material_evidence(
                    course_id, str(record["material_id"])
                )
            )
            for record in workspace.list_materials(course_id)
            if record.get("material_id")
        },
    }


@pytest.fixture(scope="module")
def reloaded(drill):
    """从**恢复出来的**目录打开一个新工作区 (module 级复用)。"""
    workspace = Workspace(str(drill["restore_dir"]))
    yield workspace
    workspace.close()


class TestRestorableNow:
    """归档本身与材料侧必须成立的部分。"""

    def test_archive_validates_before_restore(self, drill) -> None:
        service = BackupService(str(drill["restore_dir"]))
        validation = service.validate_backup(drill["archive_path"])
        assert getattr(validation, "ok", False), "归档未通过校验"

    def test_restore_reports_no_warnings(self, drill) -> None:
        restored = drill["restored"]
        assert not tuple(getattr(restored, "warnings", ()) or ())

    def test_restored_database_file_exists(self, drill) -> None:
        database = pathlib.Path(str(getattr(drill["restored"], "database_path", "")))
        assert database.is_file(), "恢复后数据库文件缺失"
        assert database.stat().st_size > 0

    def test_restored_database_is_readable_and_consistent(self, drill) -> None:
        """恢复出来的库必须真的能打开 (而不只是"文件存在")。"""
        from src.persistence import open_database

        database = str(getattr(drill["restored"], "database_path", ""))
        db = open_database(database)
        try:
            assert db.schema_version() >= 1
            assert db.integrity_check() in (True, "ok")
        finally:
            db.close()

    def test_every_material_file_survives_byte_for_byte(self, drill) -> None:
        """材料文件必须逐字节一致 —— 内容被改动过就不是恢复。"""
        restore_dir = pathlib.Path(drill["restore_dir"])
        missing: list[str] = []
        changed: list[str] = []
        for relative, payload in drill["original_files"].items():
            candidate = restore_dir / relative
            if not candidate.is_file():
                missing.append(relative)
                continue
            if candidate.read_bytes() != payload:
                changed.append(relative)
        assert not missing, f"恢复后缺失材料文件: {missing}"
        assert not changed, f"恢复后材料文件内容不一致: {changed}"

    def test_material_registry_matches_entry_by_entry(self, drill) -> None:
        """材料注册表必须逐条一致 (material_id 集合相同)。"""
        course_id = drill["course_id"]
        original_path = (
            pathlib.Path(drill["harness"].data_dir) / "materials" / f"{course_id}.json"
        )
        restored_path = pathlib.Path(drill["restore_dir"]) / "materials" / f"{course_id}.json"
        assert original_path.is_file(), "原始注册表应当还在 (只销毁了数据库)"
        assert restored_path.is_file(), "恢复后材料注册表缺失"

        def _ids(path: pathlib.Path) -> list[str]:
            payload = json.loads(path.read_text(encoding="utf-8"))
            records = payload if isinstance(payload, list) else payload.get("materials", [])
            return sorted(str(record.get("material_id")) for record in records)

        original_ids = _ids(original_path)
        assert original_ids, "原始注册表是空的, 断言会退化成空转"
        assert _ids(restored_path) == original_ids

    def test_archive_lives_inside_the_data_dir_by_default(self, drill) -> None:
        """记录一个真实风险: 归档默认落在 ``<data_dir>/backups`` 内。

        也就是说, **目录级**的丢失 (误删 data_dir、磁盘故障) 会把备份一起带走。
        本演练因此严格按 spec 措辞只销毁"working database"。要抵御目录级丢失,
        必须把备份写到 data_dir 之外 —— 这是部署时的运维要求, 不是代码缺陷。
        """
        archive = pathlib.Path(drill["archive_path"])
        data_dir = pathlib.Path(drill["harness"].data_dir)
        assert data_dir in archive.parents

    def test_material_file_count_matches_the_manifest(self, drill) -> None:
        """清单声明的材料文件数必须等于实际恢复出来的数量。

        口径: 归档保留应用自己的**四棵树** (``materials`` / ``audio`` /
        ``documents`` / ``images``) —— ``materials/`` 是材料注册索引, 每门课
        一份 JSON, 也算在 ``material_file_count`` 里。
        """
        restored = drill["restored"]
        declared = int(getattr(restored, "material_file_count", 0))
        actual = sum(
            1
            for tree in ("materials", "audio", "documents", "images")
            for _, _, files in os.walk(pathlib.Path(drill["restore_dir"]) / tree)
            for _ in files
        )
        assert declared == actual, f"清单声明 {declared} 个材料文件, 实际恢复 {actual} 个"


class TestDrillGap:
    """spec 47.12 要求、Task 48-55 之后**必须成立**的部分。

    这个类名被 spec 55.14 点名保留 (它是"曾经有缺口"的历史记录), 但里面的
    断言已经**反过来**了: 从前断言"恢复不回来", 现在断言"必须恢复回来"。
    这样既保留历史, 又不可能悄悄回退。
    """

    def test_the_working_database_really_was_destroyed(self, drill) -> None:
        """先证明演练的前提成立: 工作库真的被删掉了。

        没有这一步, "恢复成功"可能只是因为原始数据一直还在。
        """
        assert drill["destroyed"], "演练没有销毁任何数据库文件"
        data_dir = pathlib.Path(drill["harness"].data_dir)
        assert not list(data_dir.rglob("*.sqlite*")), (
            "销毁后仍然存在数据库文件: "
            f"{[str(p) for p in data_dir.rglob('*.sqlite*')]}"
        )

    def test_course_and_session_are_restored(self, drill, reloaded) -> None:
        before = drill["before"]
        assert [c["course_id"] for c in reloaded.list_courses()] == before["courses"]
        assert [
            s["session_id"] for s in reloaded.list_sessions(drill["course_id"])
        ] == before["sessions"]

    def test_materials_are_restored_and_reachable(self, drill, reloaded) -> None:
        records = reloaded.list_materials(drill["course_id"])
        assert records, "恢复后材料列表为空"
        assert sorted(str(r["material_id"]) for r in records) == drill["before"][
            "materials"
        ]

    def test_evidence_is_restored(self, drill, reloaded) -> None:
        """每份材料的证据链 (evidence_id 集合) 必须逐条一致。"""
        expected = {
            material_id: ids
            for material_id, ids in drill["before"]["material_evidence"].items()
            if ids
        }
        assert expected, "演练前提: 必须至少有一份材料产出了证据"
        restored = {
            material_id: sorted(
                row["evidence_id"]
                for row in reloaded.material_evidence(drill["course_id"], material_id)
            )
            for material_id in expected
        }
        assert restored == expected

    def test_knowledge_is_restored(self, drill, reloaded) -> None:
        points = reloaded.knowledge_points(drill["course_id"])
        assert sorted(p["knowledge_id"] for p in points) == drill["before"][
            "knowledge_ids"
        ]

    def test_review_history_is_restored(self, drill, reloaded) -> None:
        """审核历史是**追加式**的, 恢复后必须逐条一致。"""
        before = drill["before"]["review_history"]
        assert any(before.values()), "演练前提: 必须真的有审核历史"
        restored = {
            knowledge_id: [
                (r["review_id"], r["decision"])
                for r in reloaded.review_history(drill["course_id"], knowledge_id)
            ]
            for knowledge_id in before
        }
        assert restored == before

    def test_student_and_learning_state_are_restored(self, drill, reloaded) -> None:
        after = reloaded.student_state(drill["course_id"], drill["student_id"])
        assert after == drill["before"]["student_state"], (
            "恢复后的学习状态与原始不一致"
        )

    def test_answer_and_evaluation_are_restored(self, drill, reloaded) -> None:
        after = reloaded.get_evaluation(drill["course_id"], drill["harness"].answer_id)
        assert after == drill["before"]["evaluation"]

    def test_study_plan_is_restored(self, drill, reloaded) -> None:
        """学习计划是内容寻址的不可变快照 —— 重启后 plan_id 必须一致。"""
        after = reloaded.study_plan(drill["course_id"], drill["student_id"])
        assert after["plan_id"] == drill["before"]["study_plan_id"]

    def test_learning_path_is_deterministically_regenerated(self, drill, reloaded) -> None:
        """学习路径是**派生视图**, 不落盘 —— 重启后必须确定性重建。"""
        target = sorted(drill["knowledge_ids"])[0]
        after = reloaded.learning_path(drill["course_id"], target)
        assert after == drill["before"]["learning_path"]

    def test_the_restored_database_is_actually_used(self, drill, reloaded) -> None:
        """恢复后的库必须是**活**的, 而不是"只读快照"。

        追加一个新的审核决定, 然后重启再读 —— 数据必须还在。这证明恢复出来
        的库可写、可继续使用, 而不是一个只能查询的残骸。
        """
        knowledge_id = drill["knowledge_ids"][0]
        reloaded.review_keep_unverified(
            drill["course_id"], knowledge_id, note="drill-restore"
        )
        before = reloaded.review_history(drill["course_id"], knowledge_id)

        second = Workspace(str(drill["restore_dir"]))
        try:
            after = second.review_history(drill["course_id"], knowledge_id)
        finally:
            second.close()
        assert after == before, "恢复出来的库写进去的数据在第二次重启后消失了"
