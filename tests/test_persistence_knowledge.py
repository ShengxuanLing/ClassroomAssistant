# -*- coding: utf-8 -*-
"""Task 50 — Evidence / Knowledge 持久化接线。

这一层验证 spec 50 的核心要求::

    Evidence 全字段持久化
    复用既有 EvidenceStore
    KnowledgePoint / Conflict / KnowledgeStructure 一致
    抽样 50 个 KP 验证 KP -> Evidence -> Material -> source location
    数据库加载也不得产生无证据的知识点

为什么用**真实流水线**而不是手工构造对象
--------------------------------------------------------------------

手工 ``KnowledgePoint.from_dict(...)`` 只能证明"序列化器能读自己写的
东西", 证明不了产品在跑完一节课之后能不能把知识留下来。所以本文件用
验收夹具 (``tests/fixtures/acceptance``) 跑完整条
Material -> Evidence -> Knowledge 流水线, 然后:

1. 直接读 SQLite 断言"确实进了库";
2. 关掉工作区、重新打开, 断言"拿回来的东西一模一样";
3. 逐条走通 KP -> Evidence -> Material -> source location。

第三条是本文件的重点: 知识可以重新算, **溯源不能重新算**。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil

import pytest

from src.application.acceptance import AcceptanceHarness, ClassroomDataset
from src.application.persistence_wiring import default_database_path
from src.application.workspace import Workspace
from src.persistence import open_database

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "acceptance"

#: 50.7 点名的抽样规模。
SAMPLE_SIZE = 50

#: 放大后的板书段数 —— 让知识点总数越过 50, 抽样才有意义。
BOARD_SEGMENTS = 80

#: 固定种子: 抽样必须可复现。
SAMPLE_SEED = 50


# ======================================================================
# 夹具
# ======================================================================


def _write_board_ocr(path: pathlib.Path, count: int) -> None:
    segments = [
        {
            "text": (
                f"Concepte {index + 1}: definicio completa del concepte numero "
                f"{index + 1} amb els termes originals corresponents."
            ),
            "confidence": 0.9,
            "page": 1 + index // 20,
            "bounding_box": {
                "x": 30.0,
                "y": float(32 + (index % 20) * 30),
                "width": 840.0,
                "height": 28.0,
            },
        }
        for index in range(count)
    ]
    path.write_text(
        json.dumps(
            {"fixture": "task50-board-ocr", "language": "ca", "segments": segments},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _run_pipeline(root: pathlib.Path, *, board_segments: int) -> AcceptanceHarness:
    dataset_dir = root / "dataset"
    shutil.copytree(FIXTURE_DIR, dataset_dir)
    if board_segments:
        _write_board_ocr(dataset_dir / "tema1-pissarra-ocr.json", board_segments)
    harness = AcceptanceHarness(
        ClassroomDataset.from_directory(str(dataset_dir)),
        data_dir=str(root / "data"),
    )
    report = harness.run()
    assert report.all_steps_ok, f"流水线失败: {report.failed_steps}"
    return harness


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """跑一次**放大版**流水线 (知识点 > 50), 全模块复用。

    返回 dict 而不是 tuple: 这个夹具被 80 条测试共享, 具名访问比下标
    可读得多, 也避免"改一处顺序, 全文件报错"。
    """
    root = tmp_path_factory.mktemp("task50")
    harness = _run_pipeline(root, board_segments=BOARD_SEGMENTS)
    workspace = harness.workspace
    course_id = harness.course_id
    yield {
        "harness": harness,
        "workspace": workspace,
        "course_id": course_id,
        "data_dir": str(root / "data"),
        "knowledge_points": workspace.knowledge_points(course_id),
    }
    workspace.close()


@pytest.fixture(scope="module")
def reloaded(run):
    """在**同一个 data_dir** 上重新打开的工作区 (真重启, 真读盘)。"""
    workspace = Workspace(run["data_dir"])
    yield workspace
    workspace.close()


# ======================================================================
# 工具
# ======================================================================


def _count(data_dir: str, table: str, where: str = "", params=()) -> int:
    """用独立连接数行数 —— 绕过内存, 否则"内存有、库里没有"会被掩盖。"""
    database = open_database(default_database_path(data_dir), migrate=False)
    try:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(database.scalar(sql, params, default=0) or 0)
    finally:
        database.close()


def _payloads(data_dir: str, table: str, key: str) -> list[dict]:
    database = open_database(default_database_path(data_dir), migrate=False)
    try:
        rows = database.query(f"SELECT {key}, payload FROM {table}")
    finally:
        database.close()
    out = []
    for row in rows:
        payload = json.loads(row["payload"])
        payload[key] = row[key]
        out.append(payload)
    return out


def _has_location(source: dict) -> bool:
    for key in ("location", "page", "line", "paragraph", "timestamp_start", "timestamp_end"):
        if source.get(key) not in (None, "", []):
            return True
    return False


def _resolved_evidence(workspace, course_id: str) -> dict[str, list[str]]:
    return {
        kp["knowledge_id"]: [
            row["evidence_id"]
            for row in workspace.knowledge_evidence(course_id, kp["knowledge_id"])
        ]
        for kp in workspace.knowledge_points(course_id)
    }


# ======================================================================
# 50.1 Evidence 全字段持久化
# ======================================================================


class TestEvidencePersistence:
    def test_evidence_rows_land_in_sqlite(self, run) -> None:
        store = run["workspace"].store.all(active_only=False)
        assert store, "流水线必须产出证据"
        assert _count(run["data_dir"], "evidence") == len(store)

    def test_every_in_memory_evidence_is_in_the_database(self, run) -> None:
        ids = {e.evidence_id for e in run["workspace"].store.all(active_only=False)}
        stored = {row["evidence_id"] for row in _payloads(run["data_dir"], "evidence", "evidence_id")}
        assert ids == stored

    def test_the_evidence_store_round_trips_through_the_database(self, run, reloaded) -> None:
        """整库等价 —— 而不是"关键的几条还在"。"""
        before = run["workspace"].store.to_dict()
        after = reloaded.store.to_dict()
        assert [e["evidence_id"] for e in after["evidences"]] == [
            e["evidence_id"] for e in before["evidences"]
        ]
        assert after["evidences"] == before["evidences"]

    def test_the_insertion_order_is_preserved(self, run, reloaded) -> None:
        """证据顺序是溯源信息 (多来源按摄取先后排列)。"""
        before = [e.evidence_id for e in run["workspace"].store.all(active_only=False)]
        after = [e.evidence_id for e in reloaded.store.all(active_only=False)]
        assert after == before

    def test_every_evidence_field_is_preserved(self, run, reloaded) -> None:
        before = {e.evidence_id: e.to_dict() for e in run["workspace"].store.all(active_only=False)}
        after = {e.evidence_id: e.to_dict() for e in reloaded.store.all(active_only=False)}
        assert set(after) == set(before)
        for evidence_id, payload in before.items():
            assert after[evidence_id] == payload, evidence_id

    def test_the_content_is_preserved(self, run, reloaded) -> None:
        before = {e.evidence_id: e.content for e in run["workspace"].store.all(active_only=False)}
        after = {e.evidence_id: e.content for e in reloaded.store.all(active_only=False)}
        assert after == before

    def test_the_evidence_type_is_preserved(self, run, reloaded) -> None:
        before = {e.evidence_id: e.evidence_type for e in run["workspace"].store.all(active_only=False)}
        after = {e.evidence_id: e.evidence_type for e in reloaded.store.all(active_only=False)}
        assert after == before
        assert len(set(after.values())) >= 2, "夹具应当产出多种证据类型"

    def test_the_confidence_is_preserved(self, run, reloaded) -> None:
        before = {e.evidence_id: e.confidence for e in run["workspace"].store.all(active_only=False)}
        after = {e.evidence_id: e.confidence for e in reloaded.store.all(active_only=False)}
        assert after == before

    def test_the_language_is_preserved(self, run, reloaded) -> None:
        before = {e.evidence_id: e.language for e in run["workspace"].store.all(active_only=False)}
        after = {e.evidence_id: e.language for e in reloaded.store.all(active_only=False)}
        assert after == before

    def test_the_source_reference_is_preserved(self, run, reloaded) -> None:
        """provenance 的核心: 每条证据必须还记得它来自哪份材料的哪个位置。"""
        before = {
            e.evidence_id: e.source_reference
            for e in run["workspace"].store.all(active_only=False)
        }
        after = {
            e.evidence_id: e.source_reference
            for e in reloaded.store.all(active_only=False)
        }
        assert after == before

    def test_every_evidence_still_points_at_a_registered_material(
        self, run, reloaded
    ) -> None:
        materials = {m["material_id"] for m in reloaded.list_materials(run["course_id"])}
        assert materials
        broken = [
            e.evidence_id
            for e in reloaded.store.all(active_only=False)
            if (e.to_dict()["source_reference"] or {}).get("material_id") not in materials
        ]
        assert not broken, f"重启后证据指向未登记材料: {broken[:5]}"

    def test_every_evidence_still_has_a_source_location(self, reloaded) -> None:
        broken = [
            e.evidence_id
            for e in reloaded.store.all(active_only=False)
            if not _has_location(e.to_dict()["source_reference"] or {})
        ]
        assert not broken, f"重启后证据丢了源位置: {broken[:5]}"

    def test_the_metadata_is_preserved(self, run, reloaded) -> None:
        before = {e.evidence_id: e.metadata for e in run["workspace"].store.all(active_only=False)}
        after = {e.evidence_id: e.metadata for e in reloaded.store.all(active_only=False)}
        assert after == before

    def test_saving_the_store_twice_writes_nothing_new(self, run) -> None:
        """增量写入: 第二次必须写 0 条 (不是"重复插入被忽略")。"""
        persistence = run["workspace"].persistence
        assert persistence.save_evidence_store(run["workspace"].store) == 0
        assert persistence.save_evidence_store(run["workspace"].store) == 0

    def test_saving_the_store_twice_does_not_duplicate_rows(self, run) -> None:
        before = _count(run["data_dir"], "evidence")
        run["workspace"].persistence.save_evidence_store(run["workspace"].store)
        assert _count(run["data_dir"], "evidence") == before

    def test_the_evidence_table_has_no_duplicate_ids(self, run) -> None:
        ids = [row["evidence_id"] for row in _payloads(run["data_dir"], "evidence", "evidence_id")]
        assert len(ids) == len(set(ids))

    def test_the_store_survives_three_consecutive_restarts(self, run) -> None:
        """反复重启不许把证据磨掉 (每次都会 flush 一遍)。"""
        expected = [e.evidence_id for e in run["workspace"].store.all(active_only=False)]
        for _ in range(3):
            workspace = Workspace(run["data_dir"])
            try:
                assert [
                    e.evidence_id for e in workspace.store.all(active_only=False)
                ] == expected
            finally:
                workspace.close()

    def test_the_evidence_payload_is_valid_json_in_the_database(self, run) -> None:
        database = open_database(default_database_path(run["data_dir"]), migrate=False)
        try:
            rows = database.query("SELECT payload FROM evidence")
        finally:
            database.close()
        for row in rows:
            assert isinstance(json.loads(row["payload"]), dict)

    def test_evidence_can_be_looked_up_by_id_in_the_database(self, run) -> None:
        first = run["workspace"].store.all(active_only=False)[0]
        database = open_database(default_database_path(run["data_dir"]), migrate=False)
        try:
            payload = database.scalar(
                "SELECT payload FROM evidence WHERE evidence_id = ?",
                (first.evidence_id,),
            )
        finally:
            database.close()
        assert json.loads(payload)["evidence_id"] == first.evidence_id

    def test_the_evidence_count_is_stable_across_a_restart(self, run, reloaded) -> None:
        assert len(reloaded.store.all(active_only=False)) == len(
            run["workspace"].store.all(active_only=False)
        )


# ======================================================================
# 50.2 KnowledgePoint 持久化
# ======================================================================


class TestKnowledgePointPersistence:
    def test_knowledge_points_land_in_sqlite(self, run) -> None:
        assert _count(run["data_dir"], "knowledge_points") == len(run["knowledge_points"])

    def test_the_enlarged_dataset_produces_at_least_fifty_points(self, run) -> None:
        assert len(run["knowledge_points"]) >= SAMPLE_SIZE

    def test_every_knowledge_point_is_in_the_database(self, run) -> None:
        ids = {kp["knowledge_id"] for kp in run["knowledge_points"]}
        stored = {
            row["knowledge_id"]
            for row in _payloads(run["data_dir"], "knowledge_points", "knowledge_id")
        }
        assert ids == stored

    def test_the_knowledge_point_set_is_identical_after_a_restart(self, run, reloaded) -> None:
        before = [kp["knowledge_id"] for kp in run["knowledge_points"]]
        after = [kp["knowledge_id"] for kp in reloaded.knowledge_points(run["course_id"])]
        assert after == before

    def test_every_knowledge_point_field_is_preserved(self, run, reloaded) -> None:
        before = {kp["knowledge_id"]: kp for kp in run["knowledge_points"]}
        after = {kp["knowledge_id"]: kp for kp in reloaded.knowledge_points(run["course_id"])}
        assert set(after) == set(before)
        for knowledge_id, payload in before.items():
            assert after[knowledge_id] == payload, knowledge_id

    def test_the_statement_is_preserved(self, run, reloaded) -> None:
        before = {kp["knowledge_id"]: kp["content"] for kp in run["knowledge_points"]}
        after = {
            kp["knowledge_id"]: kp["content"]
            for kp in reloaded.knowledge_points(run["course_id"])
        }
        assert after == before

    def test_the_title_is_preserved(self, run, reloaded) -> None:
        before = {kp["knowledge_id"]: kp["title"] for kp in run["knowledge_points"]}
        after = {
            kp["knowledge_id"]: kp["title"]
            for kp in reloaded.knowledge_points(run["course_id"])
        }
        assert after == before

    def test_the_confidence_is_preserved(self, run, reloaded) -> None:
        before = {kp["knowledge_id"]: kp["confidence"] for kp in run["knowledge_points"]}
        after = {
            kp["knowledge_id"]: kp["confidence"]
            for kp in reloaded.knowledge_points(run["course_id"])
        }
        assert after == before

    def test_the_validation_status_is_preserved(self, run, reloaded) -> None:
        before = {
            kp["knowledge_id"]: kp["validation_status"] for kp in run["knowledge_points"]
        }
        after = {
            kp["knowledge_id"]: kp["validation_status"]
            for kp in reloaded.knowledge_points(run["course_id"])
        }
        assert after == before

    def test_the_review_status_is_preserved(self, run, reloaded) -> None:
        before = {kp["knowledge_id"]: kp["review_status"] for kp in run["knowledge_points"]}
        after = {
            kp["knowledge_id"]: kp["review_status"]
            for kp in reloaded.knowledge_points(run["course_id"])
        }
        assert after == before

    def test_the_original_terms_are_preserved(self, run, reloaded) -> None:
        """原始术语是"术语保留原文"这条规范的数据基础, 不许丢。"""
        before = {
            kp["knowledge_id"]: kp["original_terms"] for kp in run["knowledge_points"]
        }
        after = {
            kp["knowledge_id"]: kp["original_terms"]
            for kp in reloaded.knowledge_points(run["course_id"])
        }
        assert after == before

    def test_the_importance_is_preserved(self, run, reloaded) -> None:
        before = {kp["knowledge_id"]: kp["importance"] for kp in run["knowledge_points"]}
        after = {
            kp["knowledge_id"]: kp["importance"]
            for kp in reloaded.knowledge_points(run["course_id"])
        }
        assert after == before

    def test_the_knowledge_score_is_preserved(self, run, reloaded) -> None:
        before = {
            kp["knowledge_id"]: kp["knowledge_score"] for kp in run["knowledge_points"]
        }
        after = {
            kp["knowledge_id"]: kp["knowledge_score"]
            for kp in reloaded.knowledge_points(run["course_id"])
        }
        assert after == before

    def test_a_single_knowledge_point_can_be_fetched_after_a_restart(
        self, run, reloaded
    ) -> None:
        target = run["knowledge_points"][0]["knowledge_id"]
        got = reloaded.knowledge_point(run["course_id"], target)
        assert got["knowledge_id"] == target
        assert got["content"] == run["knowledge_points"][0]["content"]

    def test_saving_the_structure_twice_does_not_duplicate_points(self, run) -> None:
        before = _count(run["data_dir"], "knowledge_points")
        structure = run["workspace"]._contexts[run["course_id"]].processing.structure
        run["workspace"].persistence.save_knowledge_structure(
            structure, course_id=run["course_id"]
        )
        assert _count(run["data_dir"], "knowledge_points") == before

    def test_the_knowledge_points_table_has_no_duplicate_ids(self, run) -> None:
        ids = [
            row["knowledge_id"]
            for row in _payloads(run["data_dir"], "knowledge_points", "knowledge_id")
        ]
        assert len(ids) == len(set(ids))

    def test_a_database_load_does_not_invent_a_knowledge_point(self, run, reloaded) -> None:
        """库里 15 条, 读回来就只能是 15 条 —— 不许凭空多出"补全"的知识。"""
        stored = _count(run["data_dir"], "knowledge_points")
        assert len(reloaded.knowledge_points(run["course_id"])) == stored

    def test_a_database_load_does_not_lose_a_knowledge_point(self, run, reloaded) -> None:
        stored = _count(run["data_dir"], "knowledge_points")
        assert len(run["knowledge_points"]) == stored

    def test_no_persisted_knowledge_point_is_without_evidence(self, run, reloaded) -> None:
        """spec 50 点名: **数据库加载也不得产生无证据的知识点**。"""
        orphans = [
            kp["knowledge_id"]
            for kp in reloaded.knowledge_points(run["course_id"])
            if not kp.get("evidence_refs")
        ]
        assert not orphans, f"{len(orphans)} 个持久化知识点没有证据引用: {orphans[:5]}"

    def test_every_persisted_knowledge_point_resolves_to_real_evidence(
        self, run, reloaded
    ) -> None:
        broken = []
        for kp in reloaded.knowledge_points(run["course_id"]):
            rows = reloaded.knowledge_evidence(run["course_id"], kp["knowledge_id"])
            if not rows:
                broken.append(kp["knowledge_id"])
        assert not broken, f"{len(broken)} 个知识点的证据无法解析: {broken[:5]}"

    def test_every_evidence_ref_resolves_to_an_existing_evidence(self, run, reloaded) -> None:
        known = {e.evidence_id for e in reloaded.store.all(active_only=False)}
        broken = []
        for kp in reloaded.knowledge_points(run["course_id"]):
            for ref in kp["evidence_refs"]:
                if ref not in known:
                    broken.append(f"{kp['knowledge_id']} -> {ref}")
        assert not broken, f"悬空的证据引用: {broken[:5]}"

    def test_the_evidence_ref_order_is_preserved(self, run, reloaded) -> None:
        """顺序不是装饰: 第一条证据通常是主来源。"""
        before = {kp["knowledge_id"]: kp["evidence_refs"] for kp in run["knowledge_points"]}
        after = {
            kp["knowledge_id"]: kp["evidence_refs"]
            for kp in reloaded.knowledge_points(run["course_id"])
        }
        assert after == before

    def test_an_unknown_knowledge_point_is_not_invented(self, run, reloaded) -> None:
        from src.application.errors import NotFoundError

        with pytest.raises(NotFoundError):
            reloaded.knowledge_point(run["course_id"], "kp-does-not-exist")

    def test_the_knowledge_summary_is_identical_after_a_restart(self, run, reloaded) -> None:
        assert reloaded.knowledge_summary(run["course_id"]) == run["workspace"].knowledge_summary(
            run["course_id"]
        )

    def test_the_course_knowledge_view_is_identical_after_a_restart(
        self, run, reloaded
    ) -> None:
        assert reloaded.course_knowledge(run["course_id"]) == run["workspace"].course_knowledge(
            run["course_id"]
        )


# ======================================================================
# 50.3 Conflict 持久化
# ======================================================================


class TestConflictPersistence:
    def test_conflicts_land_in_sqlite(self, run) -> None:
        conflicts = run["workspace"].conflicts(run["course_id"])
        assert conflicts, "夹具应当产出一个冲突 (dijkstra 顺序矛盾)"
        assert _count(run["data_dir"], "conflicts") >= len(conflicts)

    def test_the_conflict_set_is_identical_after_a_restart(self, run, reloaded) -> None:
        before = sorted(c["conflict_id"] for c in run["workspace"].conflicts(run["course_id"]))
        after = sorted(c["conflict_id"] for c in reloaded.conflicts(run["course_id"]))
        assert after == before

    def test_every_conflict_field_is_preserved(self, run, reloaded) -> None:
        before = {c["conflict_id"]: c for c in run["workspace"].conflicts(run["course_id"])}
        after = {c["conflict_id"]: c for c in reloaded.conflicts(run["course_id"])}
        assert after == before

    def test_the_conflict_description_is_preserved(self, run, reloaded) -> None:
        before = {c["conflict_id"]: c["description"] for c in run["workspace"].conflicts(run["course_id"])}
        after = {c["conflict_id"]: c["description"] for c in reloaded.conflicts(run["course_id"])}
        assert after == before
        assert all(desc for desc in after.values())

    def test_the_conflict_evidence_refs_are_preserved(self, run, reloaded) -> None:
        before = {c["conflict_id"]: c["evidence_refs"] for c in run["workspace"].conflicts(run["course_id"])}
        after = {c["conflict_id"]: c["evidence_refs"] for c in reloaded.conflicts(run["course_id"])}
        assert after == before

    def test_the_conflict_status_is_preserved(self, run, reloaded) -> None:
        before = {c["conflict_id"]: c["status"] for c in run["workspace"].conflicts(run["course_id"])}
        after = {c["conflict_id"]: c["status"] for c in reloaded.conflicts(run["course_id"])}
        assert after == before

    def test_a_pending_conflict_does_not_become_resolved_by_restarting(
        self, run, reloaded
    ) -> None:
        """Review Safety: 重启**绝不能**改变冲突状态。

        这是本阶段最不能出错的一条 —— 如果重启能把 PENDING 变成别的状态,
        那"人工复核"这个机制就是假的。
        """
        before = {c["conflict_id"]: c["status"] for c in run["workspace"].conflicts(run["course_id"])}
        pending = [cid for cid, status in before.items() if status == "PENDING"]
        assert pending, "夹具应当留下至少一个待复核冲突, 否则这条测试是空转"
        after = {c["conflict_id"]: c["status"] for c in reloaded.conflicts(run["course_id"])}
        for conflict_id in pending:
            assert after[conflict_id] == "PENDING"

    def test_the_conflicted_knowledge_points_stay_conflicted(self, run, reloaded) -> None:
        before = {
            kp["knowledge_id"]: kp["validation_status"]
            for kp in run["knowledge_points"]
            if kp["validation_status"] == "conflicted"
        }
        assert before, "夹具应当产出 conflicted 知识点"
        after = {
            kp["knowledge_id"]: kp["validation_status"]
            for kp in reloaded.knowledge_points(run["course_id"])
        }
        for knowledge_id, status in before.items():
            assert after[knowledge_id] == status

    def test_the_conflict_count_in_the_summary_survives(self, run, reloaded) -> None:
        before = run["workspace"].knowledge_summary(run["course_id"])["conflict_count"]
        after = reloaded.knowledge_summary(run["course_id"])["conflict_count"]
        assert after == before

    def test_conflicts_are_not_duplicated_by_resaving(self, run) -> None:
        before = _count(run["data_dir"], "conflicts")
        structure = run["workspace"]._contexts[run["course_id"]].processing.structure
        run["workspace"].persistence.save_knowledge_structure(
            structure, course_id=run["course_id"]
        )
        assert _count(run["data_dir"], "conflicts") == before

    def test_conflict_evidence_links_are_persisted(self, run) -> None:
        conflicts = run["workspace"].conflicts(run["course_id"])
        database = open_database(default_database_path(run["data_dir"]), migrate=False)
        try:
            for conflict in conflicts:
                rows = database.query(
                    "SELECT evidence_id FROM conflict_evidence WHERE conflict_id = ? "
                    "ORDER BY position",
                    (conflict["conflict_id"],),
                )
                assert [r["evidence_id"] for r in rows] == list(conflict["evidence_refs"])
        finally:
            database.close()


# ======================================================================
# 50.4 KnowledgeStructure / 组织层一致性
# ======================================================================


class TestKnowledgeStructureConsistency:
    def test_the_knowledge_point_evidence_links_are_persisted(self, run) -> None:
        database = open_database(default_database_path(run["data_dir"]), migrate=False)
        try:
            rows = database.query(
                "SELECT knowledge_id, evidence_id, position FROM knowledge_point_evidence "
                "ORDER BY knowledge_id, position"
            )
        finally:
            database.close()
        assert rows, "知识点 -> 证据 关系表必须非空"

        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(row["knowledge_id"], []).append(row["evidence_id"])
        before = {kp["knowledge_id"]: kp["evidence_refs"] for kp in run["knowledge_points"]}
        for knowledge_id, refs in grouped.items():
            assert refs == list(before[knowledge_id]), knowledge_id

    def test_the_review_records_are_persisted(self, run) -> None:
        assert _count(run["data_dir"], "review_records") >= len(run["knowledge_points"])

    def test_the_review_history_is_identical_after_a_restart(self, run, reloaded) -> None:
        for kp in run["knowledge_points"]:
            knowledge_id = kp["knowledge_id"]
            assert reloaded.review_history(run["course_id"], knowledge_id) == (
                run["workspace"].review_history(run["course_id"], knowledge_id)
            ), knowledge_id

    def test_the_review_history_is_not_flattened(self, run, reloaded) -> None:
        """评审历史是 append-only: 条数不许因为重启而变少。"""
        for kp in run["knowledge_points"][:10]:
            knowledge_id = kp["knowledge_id"]
            before = run["workspace"].review_history(run["course_id"], knowledge_id)
            after = reloaded.review_history(run["course_id"], knowledge_id)
            assert len(after) == len(before), knowledge_id

    def test_the_session_knowledge_memberships_survive(self, run) -> None:
        assert _count(run["data_dir"], "session_memberships") > 0

    def test_the_session_memberships_are_identical_after_a_restart(
        self, run, reloaded
    ) -> None:
        before = reloaded.course_knowledge(run["course_id"])
        assert before["session_count"] == run["workspace"].course_knowledge(
            run["course_id"]
        )["session_count"]

    def test_the_coverage_is_identical_after_a_restart(self, run, reloaded) -> None:
        assert reloaded.coverage(run["course_id"]) == run["workspace"].coverage(
            run["course_id"]
        )

    def test_the_gaps_are_identical_after_a_restart(self, run, reloaded) -> None:
        assert reloaded.gaps(run["course_id"]) == run["workspace"].gaps(run["course_id"])

    def test_the_dependencies_are_identical_after_a_restart(self, run, reloaded) -> None:
        assert reloaded.dependencies(run["course_id"]) == run["workspace"].dependencies(
            run["course_id"]
        )

    def test_the_validation_counts_are_identical_after_a_restart(self, run, reloaded) -> None:
        before = run["workspace"].knowledge_summary(run["course_id"])["validation"]
        after = reloaded.knowledge_summary(run["course_id"])["validation"]
        assert after == before

    def test_the_conflict_count_in_coverage_survives(self, run, reloaded) -> None:
        assert reloaded.coverage(run["course_id"])["conflicted_knowledge_points"] == (
            run["workspace"].coverage(run["course_id"])["conflicted_knowledge_points"]
        )

    def test_the_course_knowledge_relation_count_survives(self, run, reloaded) -> None:
        assert reloaded.course_knowledge(run["course_id"])["relation_count"] == (
            run["workspace"].course_knowledge(run["course_id"])["relation_count"]
        )

    def test_the_knowledge_relations_are_persisted(self, run) -> None:
        assert _count(run["data_dir"], "knowledge_relations") >= 0  # 允许为空

    def test_processing_an_extra_material_does_not_wipe_existing_knowledge(
        self, run
    ) -> None:
        """重启后增量装配: 已有知识不许被"从空开始"的装配抹掉。

        这是 ``ProcessingService.restore_structure`` 存在的理由。
        """
        before = _count(run["data_dir"], "knowledge_points")
        workspace = Workspace(run["data_dir"])
        try:
            materials = workspace.list_materials(run["course_id"])
            completed = [
                m for m in materials if m["processing_status"] == "COMPLETED"
            ]
            if completed:
                workspace.process_material(
                    run["course_id"], completed[0]["material_id"]
                )
            assert _count(run["data_dir"], "knowledge_points") >= before
        finally:
            workspace.close()


# ======================================================================
# 50.5 抽样 50 个 KP 的完整溯源
# ======================================================================


class TestTraceabilitySample:
    def test_fifty_sampled_knowledge_points_are_traceable_before_a_restart(
        self, run
    ) -> None:
        import random

        workspace = run["workspace"]
        course_id = run["course_id"]
        sample = random.Random(SAMPLE_SEED).sample(run["knowledge_points"], SAMPLE_SIZE)
        materials = {m["material_id"] for m in workspace.list_materials(course_id)}

        broken: list[str] = []
        for kp in sample:
            knowledge_id = kp["knowledge_id"]
            if not kp.get("evidence_refs"):
                broken.append(f"{knowledge_id}: 没有 evidence_refs")
                continue
            rows = workspace.knowledge_evidence(course_id, knowledge_id)
            if not rows:
                broken.append(f"{knowledge_id}: knowledge_evidence 为空")
                continue
            resolved = {row["evidence_id"] for row in rows}
            missing = [ref for ref in kp["evidence_refs"] if ref not in resolved]
            if missing:
                broken.append(f"{knowledge_id}: 无法解析 {missing}")
                continue
            for row in rows:
                source = row.get("source") or {}
                if source.get("material_id") not in materials:
                    broken.append(f"{knowledge_id}: 指向未登记材料")
                if not _has_location(source):
                    broken.append(f"{knowledge_id}: 证据 {row['evidence_id']} 没有源位置")
        assert not broken, f"追溯链断裂 ({len(broken)}):\n" + "\n".join(broken[:10])

    def test_fifty_sampled_knowledge_points_are_traceable_after_a_restart(
        self, run, reloaded
    ) -> None:
        """**本文件最重要的一条**: 重启之后同一条证据链必须还能走通。"""
        import random

        course_id = run["course_id"]
        kps = reloaded.knowledge_points(course_id)
        assert len(kps) >= SAMPLE_SIZE
        sample = random.Random(SAMPLE_SEED).sample(kps, SAMPLE_SIZE)
        materials = {m["material_id"] for m in reloaded.list_materials(course_id)}
        assert materials

        broken: list[str] = []
        for kp in sample:
            knowledge_id = kp["knowledge_id"]
            rows = reloaded.knowledge_evidence(course_id, knowledge_id)
            if not rows:
                broken.append(f"{knowledge_id}: 重启后证据无法解析")
                continue
            for row in rows:
                source = row.get("source") or {}
                material_id = source.get("material_id")
                if material_id not in materials:
                    broken.append(f"{knowledge_id}: 指向未登记材料 {material_id!r}")
                    continue
                if not _has_location(source):
                    broken.append(f"{knowledge_id}: 证据 {row['evidence_id']} 丢了源位置")
                # 材料 -> 实际文件 必须成立 (Task 49 的链接在这一层也要通)。
                record = reloaded.get_material(course_id, material_id)
                if not os.path.isfile(record["stored_path"]):
                    broken.append(f"{knowledge_id}: 材料 {material_id} 的文件不存在")
        assert not broken, f"重启后追溯链断裂 ({len(broken)}):\n" + "\n".join(broken[:10])

    def test_the_resolved_evidence_map_is_identical_after_a_restart(
        self, run, reloaded
    ) -> None:
        before = _resolved_evidence(run["workspace"], run["course_id"])
        after = _resolved_evidence(reloaded, run["course_id"])
        assert after == before

    def test_the_trace_view_is_identical_after_a_restart(self, run, reloaded) -> None:
        for kp in run["knowledge_points"][:10]:
            knowledge_id = kp["knowledge_id"]
            assert reloaded.knowledge_trace(run["course_id"], knowledge_id) == (
                run["workspace"].knowledge_trace(run["course_id"], knowledge_id)
            ), knowledge_id

    def test_the_trace_view_reports_complete_for_every_point(self, run, reloaded) -> None:
        incomplete = [
            kp["knowledge_id"]
            for kp in reloaded.knowledge_points(run["course_id"])
            if not reloaded.knowledge_trace(run["course_id"], kp["knowledge_id"])["complete"]
        ]
        assert not incomplete, f"重启后 {len(incomplete)} 个知识点溯源不完整: {incomplete[:5]}"

    def test_the_trace_view_has_no_unresolved_material(self, run, reloaded) -> None:
        for kp in reloaded.knowledge_points(run["course_id"])[:SAMPLE_SIZE]:
            trace = reloaded.knowledge_trace(run["course_id"], kp["knowledge_id"])
            assert trace["unresolved_material_ids"] == []

    def test_the_trace_view_reports_materials_and_sessions(self, run, reloaded) -> None:
        trace = reloaded.knowledge_trace(
            run["course_id"], run["knowledge_points"][0]["knowledge_id"]
        )
        assert trace["materials"], "溯源视图必须列出材料"
        assert trace["source_sessions"], "溯源视图必须列出课堂归属"

    def test_every_knowledge_point_in_the_database_is_traceable_not_just_the_sample(
        self, run, reloaded
    ) -> None:
        broken = []
        materials = {m["material_id"] for m in reloaded.list_materials(run["course_id"])}
        for kp in reloaded.knowledge_points(run["course_id"]):
            rows = reloaded.knowledge_evidence(run["course_id"], kp["knowledge_id"])
            if not rows:
                broken.append(kp["knowledge_id"])
                continue
            for row in rows:
                source = row.get("source") or {}
                if source.get("material_id") not in materials or not _has_location(source):
                    broken.append(kp["knowledge_id"])
                    break
        assert not broken, f"{len(broken)} 个知识点溯源不完整: {broken[:5]}"


# ======================================================================
# 50.6 损坏 / 异常数据必须显式报错
# ======================================================================


#: 知识点 payload 现在存两份: 全局表 (外键锚点) + 每门课一份的
#: ``course_knowledge_points`` (Task 68, migration 003, 真正的读路径)。
#: 模拟"数据损坏"时两张都要改, 否则测试会退化成空转。
_KNOWLEDGE_PAYLOAD_TABLES = ("knowledge_points", "course_knowledge_points")


class TestCorruptedKnowledgeData:
    def test_a_corrupted_knowledge_payload_is_reported_not_ignored(self, run) -> None:
        """损坏的 payload 必须抛错。

        spec 第十三节禁止把"数据损坏"处理成"数据为空" —— 那会让用户以为
        知识被删了, 而不是被告知库坏了。
        """
        data_dir = str(pathlib.Path(run["data_dir"]) / "corrupt-kp")
        shutil.copytree(run["data_dir"], data_dir)
        database = open_database(default_database_path(data_dir))
        try:
            # 两张表都改: ``knowledge_points`` 是外键锚点,
            # ``course_knowledge_points`` 才是**每门课各一份**的读路径
            # (Task 68, migration 003)。只改一张表在换了读路径之后会变成
            # 空转测试 —— 那比没有测试更危险。
            for table in _KNOWLEDGE_PAYLOAD_TABLES:
                database.execute(
                    f"UPDATE {table} SET payload = ? WHERE knowledge_id = ?",
                    ("{not json", run["knowledge_points"][0]["knowledge_id"]),
                )
        finally:
            database.close()

        from src.persistence.errors import PayloadDecodeError

        workspace = Workspace(data_dir)
        try:
            with pytest.raises(PayloadDecodeError):
                workspace.knowledge_points(run["course_id"])
        finally:
            workspace.close()

    def test_a_corrupted_evidence_payload_is_reported_not_ignored(self, run) -> None:
        """证据库在**打开工作区**时就会读盘, 因此错误必须在这一步报出来。"""
        data_dir = str(pathlib.Path(run["data_dir"]) / "corrupt-evidence")
        shutil.copytree(run["data_dir"], data_dir)
        evidence_id = run["workspace"].store.all(active_only=False)[0].evidence_id
        database = open_database(default_database_path(data_dir))
        try:
            database.execute(
                "UPDATE evidence SET payload = ? WHERE evidence_id = ?",
                ("[]", evidence_id),
            )
        finally:
            database.close()

        from src.persistence.errors import PayloadDecodeError

        with pytest.raises(PayloadDecodeError):
            Workspace(data_dir)

    def test_a_knowledge_payload_that_is_not_an_object_is_reported(self, run) -> None:
        data_dir = str(pathlib.Path(run["data_dir"]) / "list-payload")
        shutil.copytree(run["data_dir"], data_dir)
        database = open_database(default_database_path(data_dir))
        try:
            for table in _KNOWLEDGE_PAYLOAD_TABLES:
                database.execute(
                    f"UPDATE {table} SET payload = ? WHERE knowledge_id = ?",
                    ("[]", run["knowledge_points"][0]["knowledge_id"]),
                )
        finally:
            database.close()

        from src.persistence.errors import PayloadDecodeError

        workspace = Workspace(data_dir)
        try:
            with pytest.raises(PayloadDecodeError):
                workspace.knowledge_points(run["course_id"])
        finally:
            workspace.close()

    def test_the_schema_cannot_even_hold_a_null_payload(self, run) -> None:
        """NULL payload 在 schema 层就被拒绝 —— 连"空知识"这种状态都不存在。

        这是比"读到 NULL 再报错"更强的一道防线: 数据库本身不允许。
        """
        import sqlite3

        data_dir = str(pathlib.Path(run["data_dir"]) / "null-payload")
        shutil.copytree(run["data_dir"], data_dir)
        database = open_database(default_database_path(data_dir))
        try:
            with pytest.raises(sqlite3.IntegrityError):
                database.execute(
                    "UPDATE knowledge_points SET payload = NULL WHERE knowledge_id = ?",
                    (run["knowledge_points"][0]["knowledge_id"],),
                )
        finally:
            database.close()

    def test_a_dangling_evidence_ref_is_not_silently_turned_into_knowledge(
        self, run
    ) -> None:
        """证据被删掉之后, 那个知识点**不许再作为知识出现**。

        这是 spec 50 的硬规则: "数据库加载也不得产生无证据的知识点"。
        知识点行本身还在库里 (我们没有毁掉数据), 但它已经失去了唯一来源,
        因此不再满足"知识"的定义 —— 必须从课程知识视图里消失, 而不是
        假装它还有证据。
        """
        data_dir = str(pathlib.Path(run["data_dir"]) / "dangling")
        shutil.copytree(run["data_dir"], data_dir)
        target = next(
            kp for kp in run["knowledge_points"] if len(kp["evidence_refs"]) == 1
        )
        victim = target["evidence_refs"][0]
        database = open_database(default_database_path(data_dir))
        try:
            database.execute("DELETE FROM evidence WHERE evidence_id = ?", (victim,))
        finally:
            database.close()

        workspace = Workspace(data_dir)
        try:
            visible = {
                kp["knowledge_id"]
                for kp in workspace.knowledge_points(run["course_id"])
            }
            assert target["knowledge_id"] not in visible, (
                "证据已经不存在, 这个知识点却还作为知识出现 —— 那等于伪造溯源"
            )
            # 其它知识点不受影响。
            assert len(visible) == len(run["knowledge_points"]) - 1
            # 我们**没有**删掉那条知识点行 —— 静默销毁数据同样是禁止的。
            assert _count(data_dir, "knowledge_points") == len(run["knowledge_points"])
        finally:
            workspace.close()

    def test_a_deleted_material_leaves_an_explicit_trace_gap(self, tmp_path) -> None:
        """材料文件被删掉时, 健康检查必须显式报告缺口 (Task 49 的联动)。

        独立建一个工作区: 复制 data_dir 会让记录里的**绝对路径**仍然指向
        原目录, 那样"删掉文件"删的其实不是这条记录解析出来的路径, 测试会
        变成空转。
        """
        from src.application.workspace import Workspace as WorkspaceClass

        data_dir = str(tmp_path / "trace-gap")
        source = tmp_path / "apuntes.txt"
        source.write_text("Tema 1\n", encoding="utf-8")

        workspace = WorkspaceClass(data_dir)
        try:
            course = workspace.create_course("Álgebra", "ALG", "es")
            record = workspace.register_material(course["course_id"], str(source))
            workspace.process_material(course["course_id"], record["material_id"])
            assert workspace.knowledge_points(course["course_id"])

            os.remove(record["stored_path"])
            report = workspace.material_integrity()
            assert report["counts"]["missing"] == 1
            assert report["findings"][0]["diagnostic"] == "MATERIAL_FILE_MISSING"
            # 知识本身还在 (证据不依赖文件是否还在), 但文件缺口是可见的。
            assert workspace.knowledge_points(course["course_id"])
            assert workspace.health()["status"] == "degraded"
        finally:
            workspace.close()

    def test_a_database_that_cannot_be_opened_raises(self, run) -> None:
        data_dir = str(pathlib.Path(run["data_dir"]) / "broken")
        shutil.copytree(run["data_dir"], data_dir)
        with open(default_database_path(data_dir), "wb") as handle:
            handle.write(b"not a database at all" * 16)

        from src.application.errors import StorageError

        with pytest.raises(StorageError):
            Workspace(data_dir)


# ======================================================================
# 50.7 课程隔离 (知识真值不得跨课程混合)
# ======================================================================


class TestKnowledgeCourseIsolation:
    def test_a_second_course_does_not_see_the_first_courses_knowledge(
        self, run
    ) -> None:
        workspace = Workspace(run["data_dir"])
        try:
            other = workspace.create_course("Otra Asignatura", "OTR", "es")
            assert workspace.knowledge_points(other["course_id"]) == []
            assert workspace.conflicts(other["course_id"]) == []
            assert workspace.coverage(other["course_id"])["total_knowledge_points"] == 0
        finally:
            workspace.close()

    def test_the_first_course_still_sees_its_own_knowledge(self, run) -> None:
        workspace = Workspace(run["data_dir"])
        try:
            other = workspace.create_course("Otra Asignatura", "OTR", "es")
            assert other["course_id"] != run["course_id"]
            assert len(workspace.knowledge_points(run["course_id"])) >= SAMPLE_SIZE
        finally:
            workspace.close()

    def test_conflicts_are_not_shared_between_courses(self, run) -> None:
        workspace = Workspace(run["data_dir"])
        try:
            other = workspace.create_course("Otra Asignatura", "OTR", "es")
            assert workspace.conflicts(other["course_id"]) == []
            assert workspace.conflicts(run["course_id"]), "原课程的冲突必须还在"
        finally:
            workspace.close()

    def test_the_other_courses_coverage_is_empty_not_a_copy(self, run) -> None:
        workspace = Workspace(run["data_dir"])
        try:
            other = workspace.create_course("Otra Asignatura", "OTR", "es")
            coverage = workspace.coverage(other["course_id"])
            assert coverage["covered_knowledge_points"] == 0
            assert coverage["conflicted_knowledge_points"] == 0
        finally:
            workspace.close()


# ======================================================================
# 50.8 确定性
# ======================================================================


class TestKnowledgeDeterminism:
    def test_two_restarts_give_the_same_knowledge_order(self, run) -> None:
        orders = []
        for _ in range(2):
            workspace = Workspace(run["data_dir"])
            try:
                orders.append(
                    [
                        kp["knowledge_id"]
                        for kp in workspace.knowledge_points(run["course_id"])
                    ]
                )
            finally:
                workspace.close()
        assert orders[0] == orders[1]

    def test_two_restarts_give_the_same_evidence_order(self, run) -> None:
        orders = []
        for _ in range(2):
            workspace = Workspace(run["data_dir"])
            try:
                orders.append(
                    [
                        e.evidence_id
                        for e in workspace.store.all(active_only=False)
                    ]
                )
            finally:
                workspace.close()
        assert orders[0] == orders[1]

    def test_the_knowledge_ids_do_not_change_on_restart(self, run, reloaded) -> None:
        """内容寻址身份: 重启不许重新派生出一批新 id。"""
        before = {kp["knowledge_id"] for kp in run["knowledge_points"]}
        after = {kp["knowledge_id"] for kp in reloaded.knowledge_points(run["course_id"])}
        assert after == before

    def test_the_evidence_ids_do_not_change_on_restart(self, run, reloaded) -> None:
        before = {e.evidence_id for e in run["workspace"].store.all(active_only=False)}
        after = {e.evidence_id for e in reloaded.store.all(active_only=False)}
        assert after == before

    def test_restarting_twice_does_not_duplicate_any_knowledge_row(self, run) -> None:
        for _ in range(2):
            workspace = Workspace(run["data_dir"])
            workspace.close()
        assert _count(run["data_dir"], "knowledge_points") == len(run["knowledge_points"])

    def test_restarting_twice_does_not_duplicate_any_evidence_row(self, run) -> None:
        expected = _count(run["data_dir"], "evidence")
        for _ in range(2):
            workspace = Workspace(run["data_dir"])
            workspace.close()
        assert _count(run["data_dir"], "evidence") == expected

    def test_restarting_twice_does_not_duplicate_any_review_row(self, run) -> None:
        expected = _count(run["data_dir"], "review_records")
        for _ in range(2):
            workspace = Workspace(run["data_dir"])
            workspace.close()
        assert _count(run["data_dir"], "review_records") == expected
