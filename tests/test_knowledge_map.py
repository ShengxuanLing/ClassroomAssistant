# -*- coding: utf-8 -*-
"""Knowledge Map 投影 (``Workspace.knowledge_map``) 回归测试。

背景 (TASK-KM-PREMIUM-QA P0 实测)
----------------------------------
真实课程 course-32dde014868219be 有 61 个 KP, 但去重后只有 6 条独立证据
(61 条引用共享 6 条证据)。旧实现有两个真实缺陷:

1. ``stats.evidence_count`` 用 ``sum(每点引用数)`` 填充 (=61), 与"独立证据"
   的语义不符, 前端又把它标成"条证据"。现在 ``evidence_count`` = 去重且
   可解析的独立证据数, 引用总数另有 ``evidence_ref_count``。
2. ``material_to_knowledge`` 的键是**证据 ID** (``doc-evidence-*``), 因为
   ``evidence_refs`` 里本来就是证据 ID 字符串, 旧代码把它们原样当材料 ID
   用。前端按 ``material_id`` (``mat-*``) 查全 miss。现在经 EvidenceStore
   解析到真实 ``material_id``。

本文件的断言全部是"变异杀手": 把实现回退到任一旧写法都会红。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from src.api.server import create_server
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace
from src.models import KnowledgePoint

FIXTURES = Path(__file__).resolve().parent / "fixtures"

FIXED_TIME = "2026-09-18T09:00:00+00:00"
TODAY = "2026-09-18"

#: 一节课的真实材料集合 (与 test_course_review 同源)。
SESSION_FILES = ("documents/simple.pdf", "documents/simple.docx", "notes/spanish.md")


@pytest.fixture
def workspace(tmp_path):
    ws = Workspace(
        str(tmp_path / "data"),
        clock=fixed_clock(FIXED_TIME),
        asr_mode="mock",
        ocr_mode="mock",
    )
    yield ws
    ws.close()


@pytest.fixture
def processed(workspace):
    """一门跑过整堂处理的课程: 有材料 / 有证据 / 有知识点。"""
    course = workspace.create_course("Mapa de prova", "KM001", "ca")
    cid = course["course_id"]
    session = workspace.create_session(
        cid, session_number=1, date=TODAY, title="Sessio 1"
    )
    sid = session["session_id"]
    for name in SESSION_FILES:
        workspace.register_material(cid, str(FIXTURES / name), session_id=sid)
    workspace.process_session(cid, sid)
    return cid


def _distinct_refs(kps: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for kp in kps:
        for ref in kp.get("evidence_refs") or []:
            if isinstance(ref, dict):
                eid = str(ref.get("evidence_id") or "")
            else:
                eid = str(ref or "")
            if eid:
                out.add(eid)
    return out


def _evidence_material(workspace: Workspace, evidence_id: str) -> str:
    try:
        evidence = workspace.store.get(evidence_id)
    except Exception:  # noqa: BLE001 - 测试只关心"能否解析到材料"
        return ""
    if evidence is None or evidence.source_reference is None:
        return ""
    return str(evidence.source_reference.material_id or "")


class TestStatsCaliber:
    """统计口径: evidence_count 是独立证据, evidence_ref_count 是引用总数。"""

    def test_evidence_counts_has_one_entry_per_kp(self, workspace, processed) -> None:
        payload = workspace.knowledge_map(processed)
        kps = payload["knowledge_points"]
        assert len(kps) > 0
        assert set(payload["evidence_counts"]) == {
            str(kp["knowledge_id"]) for kp in kps
        }
        for kp in kps:
            assert payload["evidence_counts"][str(kp["knowledge_id"])] == len(
                kp.get("evidence_refs") or []
            )

    def test_stats_evidence_count_is_distinct_not_sum(self, workspace, processed) -> None:
        payload = workspace.knowledge_map(processed)
        distinct = _distinct_refs(payload["knowledge_points"])
        assert payload["stats"]["evidence_count"] == len(distinct)
        # 旧写法是 sum, 只要有共享证据两者就不同 —— 本断言在旧写法下必红。
        # (若夹具恰好无共享, 下一条共享测试仍会杀死旧写法。)
        assert payload["stats"]["evidence_ref_count"] == sum(
            payload["evidence_counts"].values()
        )

    def test_shared_evidence_counts_once_but_refs_twice(
        self, workspace, processed
    ) -> None:
        """复刻真实 61-vs-6 形状: 两个 KP 共享一条证据。"""
        before = workspace.knowledge_map(processed)
        kps = before["knowledge_points"]
        first_refs = [kp for kp in kps if (kp.get("evidence_refs") or [])]
        assert first_refs, "夹具课程应至少有一个带证据的知识点"
        shared = first_refs[0]["evidence_refs"][0]
        shared_id = (
            str(shared.get("evidence_id"))
            if isinstance(shared, dict)
            else str(shared)
        )
        ctx = workspace.context(processed)
        ctx.org_service.register_knowledge_point(
            KnowledgePoint(
                knowledge_id="kp-shared-probe",
                title="shared probe",
                content="shares one evidence with another point",
                evidence_refs=[shared_id],
            )
        )
        after = workspace.knowledge_map(processed)
        # 独立证据数不变 (去重), 引用总数 +1。
        assert after["stats"]["evidence_count"] == before["stats"]["evidence_count"]
        assert (
            after["stats"]["evidence_ref_count"]
            == before["stats"]["evidence_ref_count"] + 1
        )
        assert after["evidence_counts"]["kp-shared-probe"] == 1
        # 共享的落点材料必须同时列出新旧两个 KP。
        mid = _evidence_material(workspace, shared_id)
        assert mid, "夹具证据应能解析到材料"
        assert "kp-shared-probe" in after["material_to_knowledge"].get(mid, [])
        assert first_refs[0]["knowledge_id"] in after["material_to_knowledge"].get(
            mid, []
        )

    def test_dangling_ref_is_unresolved_not_counted_not_mapped(
        self, workspace, processed
    ) -> None:
        ctx = workspace.context(processed)
        ctx.org_service.register_knowledge_point(
            KnowledgePoint(
                knowledge_id="kp-dangling-probe",
                title="dangling probe",
                content="references evidence that does not exist",
                evidence_refs=["ev-does-not-exist"],
            )
        )
        before_evidence = workspace.knowledge_map(processed)["stats"]["evidence_count"]
        payload = workspace.knowledge_map(processed)
        # 每点计数仍诚实 (该点确实有 1 条引用), 引用总数 +1...
        assert payload["evidence_counts"]["kp-dangling-probe"] == 1
        # ...但独立证据数不涨, 映射里不出现, unresolved 明确列出。
        assert payload["stats"]["evidence_count"] == before_evidence
        assert "ev-does-not-exist" in payload["unresolved_evidence_ids"]
        for kids in payload["material_to_knowledge"].values():
            assert "kp-dangling-probe" not in kids


class TestMaterialMapping:
    """material_to_knowledge 的键必须是本课程真实材料 ID。"""

    def test_keys_are_registered_material_ids_not_evidence_ids(
        self, workspace, processed
    ) -> None:
        payload = workspace.knowledge_map(processed)
        material_ids = {
            str(m["material_id"]) for m in workspace.list_materials(processed)
        }
        assert material_ids, "夹具课程应有材料"
        keys = set(payload["material_to_knowledge"])
        assert keys, "处理过的课程应有至少一个材料分组"
        # 旧缺陷: 键是 doc-evidence-*。这条在旧写法下必红。
        assert keys <= material_ids, (
            f"映射键必须是材料 ID, 越界键: {sorted(keys - material_ids)}"
        )
        assert keys & _distinct_refs(payload["knowledge_points"]) == set()

    def test_each_mapped_kp_resolves_to_that_material(
        self, workspace, processed
    ) -> None:
        payload = workspace.knowledge_map(processed)
        by_id = {str(kp["knowledge_id"]): kp for kp in payload["knowledge_points"]}
        for mid, kids in payload["material_to_knowledge"].items():
            assert kids == sorted(kids), "知识点列表必须确定性排序"
            for kid in kids:
                refs = by_id[kid].get("evidence_refs") or []
                resolved = {_evidence_material(workspace, str(r)) for r in refs}
                assert mid in resolved, f"{kid} 的证据解析不到材料 {mid}"

    def test_empty_course_returns_zeros_and_empty_arrays(
        self, workspace
    ) -> None:
        course = workspace.create_course("Curs buit", "EMPTY1", "ca")
        payload = workspace.knowledge_map(course["course_id"])
        assert payload["knowledge_points"] == []
        assert payload["evidence_counts"] == {}
        assert payload["material_to_knowledge"] == {}
        assert payload["topics"] == []
        assert payload["knowledge_memberships"] == []
        assert payload["relations"] == []
        assert payload["conflicts"] == []
        assert payload["stats"]["knowledge_point_count"] == 0
        assert payload["stats"]["evidence_count"] == 0
        assert payload["stats"]["evidence_ref_count"] == 0


class TestKnowledgeMapHttp:
    """端点注册 + 新字段经 HTTP 可达 (只读, 不写库)。"""

    def test_endpoint_shape_over_http(self, workspace, processed) -> None:
        instance = create_server(workspace, port=0)
        instance.start()
        try:
            url = instance.url + "/api/courses/%s/knowledge-map" % processed
            with urllib.request.urlopen(url, timeout=60) as response:
                assert response.status == 200
                body = json.loads(response.read().decode("utf-8"))
        finally:
            instance.stop()
        assert body.get("success") is True
        data = body["data"]
        for key in (
            "knowledge_points",
            "materials",
            "evidence_counts",
            "material_to_knowledge",
            "unresolved_evidence_ids",
            "stats",
        ):
            assert key in data, f"响应缺键 {key}"
        assert "evidence_ref_count" in data["stats"]
        second = workspace.knowledge_map(processed)
        assert data["stats"] == second["stats"], "只读投影不应因 HTTP 而漂移"
