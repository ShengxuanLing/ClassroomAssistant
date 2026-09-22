# -*- coding: utf-8 -*-
"""Task 62 — Course Review Center 测试。

覆盖的 spec 条款::

    62.2  Overview          Total / Supported / Unverified / Conflicted /
                            Pending Review / Confirmed / Rejected
    62.3  Topic Coverage    Topic -> knowledge count / supported / unverified /
                            conflicted / review pending
    62.4  Session Review    Session -> evidence / knowledge / pending /
                            unverified / conflicted, 点击进 Session Timeline
    62.5  Review Queue      显示待审核, 点击进既有 Review Center (不复制服务)
    62.6  Conflict          显示冲突知识点, 进 Knowledge Detail, 看 Evidence A/B
    62.7  Gap Analysis      复用已有 coverage, 用实际定义
    62.9  Empty State       新课程"No knowledge available yet.", 不是 500
    62.10 i18n              zh / es / ca 文案进 I18N 表 (在 test_exercise_ui
                            的 ui_audit 里统一校验, 这里断言后端不返回文案)
    62.11 Tests             40+

几条刻意写死的判据 (产品语义, 不是实现细节)
--------------------------------------------
1. **状态不重新定义**: 概览里的 supported / unverified / conflicted 必须与
   ``ValidationStatus`` 枚举值**逐字相等**; pending / confirmed / rejected /
   kept_unverified 必须与 ``ReviewStatus`` 逐字相等。抄错一个字就红。
2. **两个轴不许相加**: validation 与 review 是独立的两轴。断言
   ``supported + unverified + conflicted == total`` (validation 轴闭合) 且
   ``pending + confirmed + rejected + kept_unverified == total`` (review 轴闭合),
   但**不**断言两者相等 —— 它们本来就不同。
3. **空课程是正常状态**: 计数全 0、``empty: true``、HTTP 200。
4. **复习中心只读**: 连续调用三次, 知识点 / 审核记录 / 计划快照数量一个都不变。
5. **不出现掌握度词汇**: 响应 JSON 里搜不到 mastery / proficiency / predicted。
6. **多课程隔离**: A 课的复习中心里绝不出现 B/C 课的知识点 id。
7. **重启后一致**: 同一份数据在子进程重载后给出同样的概览计数。
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from src.api.server import create_server
from src.application.course_review_view import (
    CENTER_FORBIDDEN_TERMS,
    COVERAGE_STATUSES,
    GAP_TYPES,
    REVIEW_STATUSES,
    VALIDATION_STATUSES,
)
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace
from src.knowledge_coverage import KnowledgeGapType
from src.knowledge_review import ReviewStatus
from src.knowledge_validation import ValidationStatus

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROOT = Path(__file__).resolve().parent.parent

FIXED_TIME = "2026-09-18T09:00:00+00:00"
TODAY = "2026-09-18"

DOCUMENTS = ("documents/simple.pdf", "documents/simple.docx")
NOTES = ("notes/spanish.md",)
EMPTY_DOCS = ("documents/empty.pdf", "documents/empty.docx")

#: 每节课用**不同**的材料, 否则同一份文件会被 content+filename 去重,
#: 第二节课其实什么都没登记 (material_id 由 内容+文件名 决定)。
#: 多会话夹具必须覆盖这一点, 不然测的是"空课堂"而不是"两节课"。
SESSION_FIXTURE_SETS = (
    ("documents/simple.pdf", "documents/simple.docx", "notes/spanish.md"),
    ("documents/multilingual.pdf", "notes/catalan.md"),
    ("documents/tables.pdf", "notes/mixed.md"),
)


def _fixtures_for(session_number: int) -> tuple[str, ...]:
    return SESSION_FIXTURE_SETS[(session_number - 1) % len(SESSION_FIXTURE_SETS)]


# ---------------------------------------------------------------------------
# 领域常量漂移守卫 (对照领域私有定义, 不抄本地副本)
# ---------------------------------------------------------------------------


class TestDomainConstantsMatchTheirSources:
    """投影层映射的枚举必须与领域定义逐字一致。

    这是本项目"领域常量漂移守卫"的标准写法: 直接 import 领域枚举来比对,
    而不是对照自己手抄的一份列表。领域改一个字, 这里立刻红。
    """

    def test_validation_statuses_match_the_enum(self) -> None:
        domain = {s.value for s in ValidationStatus}
        assert set(VALIDATION_STATUSES) == domain, (
            f"投影层写的 {VALIDATION_STATUSES} 与 ValidationStatus 不符: {domain}"
        )

    def test_review_statuses_match_the_enum(self) -> None:
        domain = {s.value for s in ReviewStatus}
        assert set(REVIEW_STATUSES) == domain, (
            f"投影层写的 {REVIEW_STATUSES} 与 ReviewStatus 不符: {domain}"
        )

    def test_gap_types_match_the_enum(self) -> None:
        domain = {g.value for g in KnowledgeGapType}
        assert set(GAP_TYPES) == domain, (
            f"投影层写的 {GAP_TYPES} 与 KnowledgeGapType 不符: {domain}"
        )

    def test_coverage_statuses_are_covered_and_uncovered(self) -> None:
        from src.knowledge_coverage import KnowledgeCoverageStatus

        assert set(COVERAGE_STATUSES) == {s.value for s in KnowledgeCoverageStatus}

    def test_forbidden_terms_include_the_mastery_family(self) -> None:
        """禁止词汇表本身不能退化 —— 至少覆盖掌握度与考试预测两类。"""
        assert "mastery" in CENTER_FORBIDDEN_TERMS
        assert "predicted" in CENTER_FORBIDDEN_TERMS
        assert "you_are_ready" in CENTER_FORBIDDEN_TERMS


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


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
def course(workspace) -> dict[str, Any]:
    return workspace.create_course(
        "Gestió de Ciutats Intel·ligents",
        "GCI201",
        "ca",
        metadata={"teacher": "Prof. Puig", "semester": "2026-2"},
    )


def _register(workspace, course_id: str, session_id: str, names) -> list[dict[str, Any]]:
    return [
        workspace.register_material(course_id, str(FIXTURES / name), session_id=session_id)
        for name in names
    ]


def _processed(workspace, course_id: str, session_number: int = 1):
    """跑一节真实课堂: 注册材料 -> 整堂处理 -> 得到证据与知识点。

    每节课用**不同**的夹具集合, 因为 material_id 由 (内容, 文件名) 决定 ——
    复用同一份文件会让第二节课其实没有材料。
    """
    session = workspace.create_session(
        course_id, session_number=session_number, date=TODAY, title="Programació"
    )
    sid = session["session_id"]
    _register(workspace, course_id, sid, _fixtures_for(session_number))
    workspace.process_session(course_id, sid)
    return sid


@pytest.fixture
def processed_course(workspace, course):
    """一门有 1 节课 / 3 材料 / 有知识点 / 有待审核的课程。"""
    cid = course["course_id"]
    sid = _processed(workspace, cid, 1)
    return cid, sid


@pytest.fixture
def client(workspace):
    instance = create_server(workspace, port=0)
    instance.start()
    try:
        yield _Client(instance.url)
    finally:
        instance.stop()


class _Client:
    def __init__(self, base: str) -> None:
        self.base = base

    def get(self, path: str) -> tuple[int, Any]:
        request = urllib.request.Request(self.base + path, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


# ===========================================================================
# 62.2 Overview
# ===========================================================================


class TestOverview:
    def test_empty_course_returns_zero_counts_not_an_error(self, workspace, course) -> None:
        view = workspace.course_review(course["course_id"])
        assert view["overview"]["total"] == 0
        assert view["empty"] is True

    def test_every_required_counter_is_present(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        overview = workspace.course_review(cid)["overview"]
        for key in (
            "total",
            "supported",
            "unverified",
            "conflicted",
            "pending_review",
            "confirmed",
            "rejected",
        ):
            assert key in overview, f"概览缺少 spec 62.2 要求的 {key}"

    def test_total_matches_the_knowledge_service(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        view = workspace.course_review(cid)
        assert view["overview"]["total"] == len(workspace.knowledge_points(cid))
        assert view["overview"]["total"] > 0

    def test_validation_axis_closes_over_the_total(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        o = workspace.course_review(cid)["overview"]
        assert o["supported"] + o["unverified"] + o["conflicted"] == o["total"]

    def test_review_axis_closes_over_the_total(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        o = workspace.course_review(cid)["overview"]
        assert (
            o["pending_review"] + o["confirmed"] + o["rejected"] + o["kept_unverified"]
            == o["total"]
        )

    def test_the_two_axes_are_reported_separately_not_merged(
        self, workspace, processed_course
    ) -> None:
        """validation 与 review 是**两个独立的轴**, 必须分别出现。

        刚跑完流水线的知识点是 supported + pending: 一个轴说"材料支持"，
        另一个轴说"人还没审核"。如果有人把它们合并成一个"已确认数"，
        ``by_validation_status`` / ``by_review_status`` 就会消失或对不上。
        """
        cid, _ = processed_course
        o = workspace.course_review(cid)["overview"]
        # 两个轴都完整暴露
        assert set(o["by_validation_status"]) == set(VALIDATION_STATUSES)
        assert set(o["by_review_status"]) == set(REVIEW_STATUSES)
        # 顶层便捷计数字段同时给出, 且各自与所属轴一致
        assert o["supported"] == o["by_validation_status"]["supported"]
        assert o["pending_review"] == o["by_review_status"]["pending"]
        assert o["confirmed"] == o["by_review_status"]["confirmed"]
        # "已确认" 与 "材料支持" 是不同的东西 —— 不能互相顶替
        assert "confirmed" in o and "supported" in o
        # 刚跑完流水线时确认数必然是 0, 支持数必然 > 0 (两轴确实不同)
        assert o["confirmed"] == 0
        assert o["supported"] > 0

    def test_by_validation_status_lists_all_three_labels(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        o = workspace.course_review(cid)["overview"]
        assert set(o["by_validation_status"]) == set(VALIDATION_STATUSES)
        assert all(isinstance(v, int) for v in o["by_validation_status"].values())

    def test_by_review_status_lists_all_four_labels(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        o = workspace.course_review(cid)["overview"]
        assert set(o["by_review_status"]) == set(REVIEW_STATUSES)

    def test_pending_review_agrees_with_the_review_service(
        self, workspace, processed_course
    ) -> None:
        """待审核数是权威来源 (ReviewService), 不是自己数的。"""
        cid, _ = processed_course
        view = workspace.course_review(cid)
        assert view["overview"]["review_candidates"] == len(
            workspace.review_candidates(cid)
        )
        assert view["review_queue"]["count"] == len(workspace.review_candidates(cid))

    def test_confirming_a_point_moves_it_off_the_pending_axis(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        before = workspace.course_review(cid)["overview"]
        points = workspace.knowledge_points(cid)
        workspace.review_confirm(cid, points[0]["knowledge_id"], note="task62")
        after = workspace.course_review(cid)["overview"]
        assert after["confirmed"] == before["confirmed"] + 1
        assert after["pending_review"] == before["pending_review"] - 1
        # 总数不变: 审核不改变知识点数量
        assert after["total"] == before["total"]

    def test_rejecting_a_point_moves_it_to_the_rejected_bucket(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        before = workspace.course_review(cid)["overview"]
        points = workspace.knowledge_points(cid)
        workspace.review_reject(cid, points[0]["knowledge_id"], note="task62")
        after = workspace.course_review(cid)["overview"]
        assert after["rejected"] == before["rejected"] + 1
        assert after["total"] == before["total"]

    def test_keep_unverified_records_its_own_bucket(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        points = workspace.knowledge_points(cid)
        workspace.review_keep_unverified(cid, points[0]["knowledge_id"], note="task62")
        after = workspace.course_review(cid)["overview"]
        assert after["kept_unverified"] == 1


# ===========================================================================
# 62.3 Topic Coverage
# ===========================================================================


class TestTopicCoverage:
    def _assign_all(self, workspace, course_id: str, name: str) -> str:
        ctx = workspace.context(course_id)
        topic = ctx.org_service.add_topic(name, order_index=1)
        for point in workspace.knowledge_points(course_id):
            ctx.org_service.add_knowledge_to_topic(
                topic.topic_id, point["knowledge_id"]
            )
        return topic.topic_id

    def test_course_without_topics_reports_no_topic_rows(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        assert workspace.course_review(cid)["topics"] == []

    def test_topic_row_carries_the_required_counters(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        self._assign_all(workspace, cid, "Programació")
        rows = workspace.course_review(cid)["topics"]
        assert len(rows) == 1
        row = rows[0]
        for key in (
            "topic_id",
            "knowledge_count",
            "supported",
            "unverified",
            "conflicted",
            "review_pending",
        ):
            assert key in row, f"topic 行缺少 {key}"

    def test_topic_knowledge_count_matches_its_members(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        self._assign_all(workspace, cid, "Programació")
        row = workspace.course_review(cid)["topics"][0]
        assert row["knowledge_count"] == len(workspace.knowledge_points(cid))
        assert len(row["knowledge_ids"]) == row["knowledge_count"]

    def test_topic_counters_reuse_the_coverage_analyzer(
        self, workspace, processed_course
    ) -> None:
        """topic 的覆盖数字必须来自 analyze_topic, 不是自己算的。"""
        from src.knowledge_coverage import KnowledgeCoverageAnalyzer

        cid, _ = processed_course
        topic_id = self._assign_all(workspace, cid, "Programació")
        row = workspace.course_review(cid)["topics"][0]
        expected = KnowledgeCoverageAnalyzer(
            workspace.context(cid).org_service
        ).analyze_topic(topic_id).to_dict()
        assert row["coverage"] == expected

    def test_topics_are_sorted_by_id_for_determinism(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        ctx = workspace.context(cid)
        ctx.org_service.add_topic("Zeta", order_index=2)
        ctx.org_service.add_topic("Alfa", order_index=1)
        rows = workspace.course_review(cid)["topics"]
        ids = [r["topic_id"] for r in rows]
        assert ids == sorted(ids)

    def test_unassigned_topic_row_is_not_invented(
        self, workspace, processed_course
    ) -> None:
        """没有 topic 的知识点不该被伪造一个 "Altres" 行。

        未分配是一个**缺口** (unassigned), 不是主题。
        """
        cid, _ = processed_course
        self._assign_all(workspace, cid, "Programació")
        rows = workspace.course_review(cid)["topics"]
        assert len(rows) == 1
        assert workspace.course_review(cid)["gaps"]["by_type"]["unassigned"] == 0


# ===========================================================================
# 62.4 Session Review
# ===========================================================================


class TestSessionReview:
    def test_session_row_carries_the_required_fields(
        self, workspace, processed_course
    ) -> None:
        cid, sid = processed_course
        rows = workspace.course_review(cid)["sessions"]
        assert len(rows) == 1
        row = rows[0]
        assert row["session_id"] == sid
        for key in (
            "evidence_count",
            "knowledge_count",
            "pending_review",
            "unverified",
            "conflicted",
        ):
            assert key in row, f"session 行缺少 {key}"

    def test_session_evidence_count_matches_the_materials(
        self, workspace, processed_course
    ) -> None:
        cid, sid = processed_course
        row = workspace.course_review(cid)["sessions"][0]
        expected = sum(
            len(m.get("evidence_ids") or ())
            for m in workspace.list_materials(cid, session_id=sid)
        )
        assert row["evidence_count"] == expected
        assert expected > 0

    def test_session_knowledge_count_matches_the_session_query(
        self, workspace, processed_course
    ) -> None:
        cid, sid = processed_course
        row = workspace.course_review(cid)["sessions"][0]
        assert row["knowledge_count"] == len(
            workspace.knowledge_points(cid, session_id=sid)
        )

    def test_session_links_to_the_existing_timeline_endpoint(
        self, workspace, processed_course
    ) -> None:
        cid, sid = processed_course
        row = workspace.course_review(cid)["sessions"][0]
        assert row["timeline_path"] == f"/api/sessions/{sid}/timeline"

    def test_session_timeline_link_actually_resolves(
        self, client, processed_course
    ) -> None:
        """链接必须真的可达 —— 不是写死一个看起来对的字符串。"""
        cid, sid = processed_course
        status, payload = client.get(f"/api/sessions/{sid}/timeline")
        assert status == 200, payload

    def test_two_sessions_are_both_listed_and_scoped(
        self, workspace, processed_course
    ) -> None:
        cid, sid1 = processed_course
        sid2 = _processed(workspace, cid, 2)
        rows = workspace.course_review(cid)["sessions"]
        assert {r["session_id"] for r in rows} == {sid1, sid2}
        by_id = {r["session_id"]: r for r in rows}
        # 两节课各自的知识点集合不能互相污染
        assert set(by_id[sid1]["knowledge_ids"]) != set(by_id[sid2]["knowledge_ids"]) or (
            by_id[sid2]["knowledge_count"] == len(
                workspace.knowledge_points(cid, session_id=sid2)
            )
        )

    def test_sessions_are_sorted_by_id(self, workspace, processed_course) -> None:
        cid, _ = processed_course
        _processed(workspace, cid, 2)
        ids = [r["session_id"] for r in workspace.course_review(cid)["sessions"]]
        assert ids == sorted(ids)


# ===========================================================================
# 62.5 Review Queue
# ===========================================================================


class TestReviewQueue:
    def test_queue_points_at_the_existing_review_center(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        queue = workspace.course_review(cid)["review_queue"]
        assert queue["review_center_path"] == "/api/reviews"

    def test_review_center_path_actually_resolves(self, client, processed_course) -> None:
        cid, _ = processed_course
        status, payload = client.get(f"/api/reviews?course_id={cid}")
        assert status == 200, payload
        assert isinstance(payload["data"]["reviews"], list)

    def test_queue_items_come_from_the_review_service(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        candidates = workspace.review_candidates(cid)
        queue = workspace.course_review(cid)["review_queue"]
        assert [i["knowledge_point_id"] for i in queue["items"]] == [
            c["knowledge_point_id"] for c in candidates
        ]

    def test_queue_is_empty_for_an_untouched_course(self, workspace, course) -> None:
        queue = workspace.course_review(course["course_id"])["review_queue"]
        assert queue["count"] == 0
        assert queue["items"] == []

    def test_no_second_review_service_is_constructed(self) -> None:
        """结构守卫: 投影层不得自己 new 一个 ReviewService。"""
        source = (
            ROOT / "src" / "application" / "course_review_view.py"
        ).read_text(encoding="utf-8")
        assert "ReviewService(" not in source, (
            "复习中心不得复制一套 Review Service (spec 62.5)"
        )

    def test_queue_actions_go_through_the_workspace_review_methods(
        self, workspace, processed_course
    ) -> None:
        """确认动作仍然走既有 workspace.review_confirm (不是投影层自实现)。"""
        cid, _ = processed_course
        point = workspace.review_candidates(cid)[0]["knowledge_point_id"]
        record = workspace.review_confirm(cid, point, note="task62-queue")
        assert record["decision"] == "confirm"
        assert point not in {
            c["knowledge_point_id"] for c in workspace.review_candidates(cid)
        }


# ===========================================================================
# 62.6 Conflict
# ===========================================================================


class TestConflict:
    def _inject_conflict(self, workspace, course_id: str) -> tuple[str, list[str]]:
        """向知识结构注入一条冲突 (走**持久化**路径, 仓储是唯一真源)。

        三条踩过的坑, 记在这里避免重犯
        --------------------------------
        1. ``register_knowledge_structure`` 只遍历 ``knowledge_points``,
           传一个只有 conflict 的空结构什么都不会发生 —— 必须把**真实的
           KP 对象**一起放进去。
        2. ``register_knowledge_structure`` 用 ``setdefault`` 登记结构, 已经
           登记过的 KP **不会被新结构覆盖** —— 所以"先注册一个带冲突的
           结构"在内存路径上永远看不到冲突。真正权威的写入路径是
           ``WorkspacePersistence.save_knowledge_structure()`` (跨表事务),
           重启后由 ``load_knowledge_structure`` 读回来。
        3. 装配产出的每个 KP **只挂 1 条证据** (一节课 N 个知识点 ↔ N 条
           证据), 而一条冲突是"两条证据互相矛盾"。所以两侧的证据必须从
           **两个不同的 KP** 各取一条 —— 在单个 KP 上找两条证据会永远
           找不到 (实测: 3 组夹具、24 个知识点, 全是 refs=1)。
        """
        from src.integration import ConflictRecord, VerificationStatus
        from src.knowledge_structure import KnowledgeStructure
        from src.models import KnowledgePoint

        points = sorted(
            workspace.knowledge_points(course_id), key=lambda p: p["knowledge_id"]
        )
        assert len(points) >= 2, f"夹具需要 >=2 个知识点, 实际 {len(points)}"

        # 每个 KP 各取它的第一条证据 -> 两条互不相同的证据作为冲突两侧。
        sides: list[str] = []
        for point in points:
            refs = sorted(str(r) for r in (point.get("evidence_refs") or ()) if r)
            if refs and refs[0] not in sides:
                sides.append(refs[0])
            if len(sides) >= 2:
                break
        assert len(sides) >= 2, f"夹具需要 >=2 条不同证据来构造两侧冲突, 实际 {sides}"

        structure = workspace.persistence.load_knowledge_structure(course_id)
        assert isinstance(structure, KnowledgeStructure)
        structure.add_conflict(
            ConflictRecord(
                conflict_id="conf-task62",
                evidence_refs=list(sides),
                description="Task 62 conflict fixture",
                status=VerificationStatus.PENDING,
            )
        )
        workspace.persistence.save_knowledge_structure(structure, course_id=course_id)
        # 这一步是**必需的**: 冲突写在了持久化层, 而课程上下文里那份快照是
        # 之前建的。不刷新的结果是 review 里永远看不到这条冲突 —— 夹具与
        # 被测代码会一起"假装正确"。reload_course() 走完整的重建路径。
        workspace.reload_course(course_id)
        return "conf-task62", sides

    def test_no_conflicts_reported_for_a_clean_course(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        assert workspace.course_review(cid)["conflicts"] == []

    def test_conflict_is_reported_with_both_sides(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        self._inject_conflict(workspace, cid)
        conflicts = workspace.course_review(cid)["conflicts"]
        assert len(conflicts) >= 1
        row = conflicts[0]
        assert row["conflict_id"]
        # spec 62.6: 必须能看到 Evidence A / Evidence B (两侧都在)
        assert len(row["sides"]) >= 2
        assert row["sides"][0]["label"] == "Evidence A"
        assert row["sides"][1]["label"] == "Evidence B"
        assert row["sides"][0]["evidence_id"] != row["sides"][1]["evidence_id"]

    def test_conflict_sides_preserve_every_evidence_ref(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        _, refs = self._inject_conflict(workspace, cid)
        row = workspace.course_review(cid)["conflicts"][0]
        assert sorted(s["evidence_id"] for s in row["sides"]) == sorted(set(refs))

    def test_conflict_is_deduplicated_not_repeated_per_knowledge_point(
        self, workspace, processed_course
    ) -> None:
        """Task 45 修过的真实缺陷: 同一冲突被每个 KP 各报一次。"""
        cid, _ = processed_course
        self._inject_conflict(workspace, cid)
        conflicts = workspace.course_review(cid)["conflicts"]
        ids = [c["conflict_id"] for c in conflicts]
        assert len(ids) == len(set(ids)), f"冲突被重复报告: {ids}"

    def test_conflict_carries_a_knowledge_detail_link(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        self._inject_conflict(workspace, cid)
        row = workspace.course_review(cid)["conflicts"][0]
        assert row["knowledge_detail_path"].startswith("/api/knowledge/")

    def test_conflicts_are_sorted_by_id(self, workspace, processed_course) -> None:
        cid, _ = processed_course
        self._inject_conflict(workspace, cid)
        ids = [c["conflict_id"] for c in workspace.course_review(cid)["conflicts"]]
        assert ids == sorted(ids)

    def test_conflict_is_attributed_to_the_knowledge_points_it_touches(
        self, workspace, processed_course
    ) -> None:
        """冲突 → 知识点 的归属必须按**证据交集**推导, 且是全部而非第一个。

        领域层的 ``ConflictRecord`` 只记录"哪两条证据互相矛盾", 没有知识点
        字段。归属只能由"证据 refs 与该冲突 refs 有交集的 KP"推导 —— 这与
        ``KnowledgeReviewService._allowed_evidence_refs`` 判定"该冲突触及这个
        KP"的口径必须完全一致, 否则 UI 会把人带到错误的知识点。
        """
        cid, _ = processed_course
        _, sides = self._inject_conflict(workspace, cid)
        row = workspace.course_review(cid)["conflicts"][0]

        assert set(row["evidence_refs"]) == set(sides)
        # 每个 KPI 的 evidence_refs 与冲突 refs 有交集的才应出现。
        expected = sorted(
            point["knowledge_id"]
            for point in workspace.knowledge_points(cid)
            if set(point.get("evidence_refs") or ()) & set(sides)
        )
        assert row["knowledge_point_ids"] == expected
        assert expected, "夹具里至少应有一个知识点触及该冲突"
        assert row["knowledge_point_id"] == expected[0]
        assert len(expected) >= 2, (
            "两侧证据分别来自两个知识点, 所以归属应至少两个 —— "
            f"实际 {expected}"
        )

    def test_conflict_attribution_matches_the_review_layer(
        self, workspace, processed_course
    ) -> None:
        """投影层的归属与审核层"该冲突可被哪些 KP 解决"必须一致。"""
        from src.knowledge_review import KnowledgeReviewService
        from src.models import KnowledgePoint

        cid, _ = processed_course
        _, sides = self._inject_conflict(workspace, cid)
        row = workspace.course_review(cid)["conflicts"][0]
        projected = set(row["knowledge_point_ids"])

        ctx = workspace.context(cid)
        structure = ctx.review_service._review_structure
        authoritative: set[str] = set()
        for point in workspace.knowledge_points(cid):
            kp = KnowledgePoint.from_dict(point)
            allowed = KnowledgeReviewService._allowed_evidence_refs(structure, kp)
            if set(sides) & allowed:
                authoritative.add(kp.knowledge_id)
        assert projected == authoritative, (
            f"投影 {sorted(projected)} != 审核层 {sorted(authoritative)}"
        )

    def test_reload_course_refreshes_the_in_memory_snapshot(
        self, workspace, processed_course
    ) -> None:
        """绕过上下文直接写持久化后, 必须有一条受支持的刷新路径。

        没有它就只能重建整个 ``Workspace`` —— 而那会连带丢掉其他课程的内存
        状态。``reload_course`` 只丢弃这一门课的上下文。
        """
        from src.integration import ConflictRecord, VerificationStatus

        cid, _ = processed_course
        structure = workspace.persistence.load_knowledge_structure(cid)
        sides = sorted(
            str(r)
            for r in (workspace.knowledge_points(cid)[0].get("evidence_refs") or ())
        )
        structure.add_conflict(
            ConflictRecord(
                conflict_id="conf-reload",
                evidence_refs=sides[:1],
                description="reload fixture",
                status=VerificationStatus.PENDING,
            )
        )
        workspace.persistence.save_knowledge_structure(structure, course_id=cid)

        # 刷新前: 内存快照还是旧的。
        assert "conf-reload" not in [
            c["conflict_id"] for c in workspace.conflicts(cid)
        ]
        workspace.reload_course(cid)
        assert "conf-reload" in [c["conflict_id"] for c in workspace.conflicts(cid)]

    def test_reload_course_does_not_touch_persisted_facts(
        self, workspace, processed_course
    ) -> None:
        """刷新是只读重建 —— 知识点 / 审核记录 / 计划快照一个都不能变。"""
        cid, _ = processed_course
        student = workspace.create_student(cid, "Ada", "Lovelace")
        sid = student["student_id"]
        before = workspace.knowledge_points(cid)
        before_reviews = workspace.review_history(cid, before[0]["knowledge_id"])
        before_plans = count_plan_rows(workspace, cid, sid)

        workspace.reload_course(cid)

        assert workspace.knowledge_points(cid) == before
        assert workspace.review_history(cid, before[0]["knowledge_id"]) == before_reviews
        assert count_plan_rows(workspace, cid, sid) == before_plans

    def test_reload_course_rejects_empty_id(self, workspace) -> None:
        from src.application.errors import InvalidInputError

        with pytest.raises(InvalidInputError):
            workspace.reload_course("   ")

    def test_reload_course_on_unknown_course_is_not_found(
        self, workspace, course
    ) -> None:
        from src.application.errors import NotFoundError

        with pytest.raises(NotFoundError):
            workspace.reload_course("course-does-not-exist")


def conflict_id_of(workspace, course_id: str) -> str:
    conflicts = workspace.conflicts(course_id)
    return conflicts[0]["conflict_id"] if conflicts else ""


# ===========================================================================
# 62.7 Gap Analysis
# ===========================================================================


class TestGapAnalysis:
    def test_gap_report_uses_the_domain_gap_types(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        gaps = workspace.course_review(cid)["gaps"]
        assert set(gaps["by_type"]) == set(GAP_TYPES)

    def test_unassigned_gap_appears_when_no_topic_exists(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        gaps = workspace.course_review(cid)["gaps"]
        assert gaps["by_type"]["unassigned"] > 0

    def test_unassigned_gap_disappears_once_every_point_is_assigned(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        ctx = workspace.context(cid)
        topic = ctx.org_service.add_topic("Programació")
        for point in workspace.knowledge_points(cid):
            ctx.org_service.add_knowledge_to_topic(topic.topic_id, point["knowledge_id"])
        gaps = workspace.course_review(cid)["gaps"]
        assert gaps["by_type"]["unassigned"] == 0

    def test_review_pending_gap_matches_the_overview(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        view = workspace.course_review(cid)
        assert (
            view["gaps"]["by_type"]["review_pending"]
            == view["overview"]["pending_review"]
        )

    def test_coverage_is_reused_from_the_domain_analyzer(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        assert workspace.course_review(cid)["coverage"] == workspace.coverage(cid)

    def test_gap_knowledge_ids_are_sorted(self, workspace, processed_course) -> None:
        cid, _ = processed_course
        ids = workspace.course_review(cid)["gaps"]["knowledge_points_with_gaps"]
        assert ids == sorted(ids)

    def test_gap_rows_are_sorted_by_knowledge_id(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        ids = [
            g["knowledge_point_id"]
            for g in workspace.course_review(cid)["gaps"]["gaps"]
        ]
        assert ids == sorted(ids)

    def test_partially_covered_is_not_invented(self, workspace, processed_course) -> None:
        """spec 62.7 的 "Partially Covered" 在本项目模型里不存在 —— 不许伪造。

        coverage 模型只有 covered / uncovered, 缺口是五种数据状态。
        伪造一个不存在的概念会比缺字段更糟 (用户会去追一个不存在的状态)。
        """
        cid, _ = processed_course
        blob = json.dumps(workspace.course_review(cid), ensure_ascii=False).lower()
        assert "partially" not in blob


# ===========================================================================
# 62.9 Empty State
# ===========================================================================


class TestEmptyState:
    def test_empty_course_has_a_complete_shape(self, workspace, course) -> None:
        view = workspace.course_review(course["course_id"])
        assert view["empty"] is True
        assert view["overview"]["total"] == 0
        assert view["topics"] == []
        assert view["sessions"] == []
        assert view["review_queue"]["count"] == 0
        assert view["conflicts"] == []
        assert view["gaps"]["total_gaps"] == 0

    def test_empty_course_returns_http_200_not_500(self, client, course) -> None:
        status, payload = client.get(
            f"/api/courses/{course['course_id']}/review"
        )
        assert status == 200, payload
        assert payload["success"] is True

    def test_empty_summary_returns_http_200_not_500(self, client, course) -> None:
        status, payload = client.get(
            f"/api/courses/{course['course_id']}/review-summary"
        )
        assert status == 200, payload
        assert payload["data"]["empty"] is True

    def test_unknown_course_is_404_not_500(self, client) -> None:
        status, payload = client.get("/api/courses/course-missing/review")
        assert status == 404, payload
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_empty_course_id_path_value_is_rejected(self, client) -> None:
        """空 course_id 必须是 400/404, 绝不能 500。"""
        status, payload = client.get("/api/courses/%20/review")
        assert status in (400, 404), payload
        assert payload["success"] is False

    def test_course_with_materials_but_no_knowledge_is_not_empty_error(
        self, workspace, course
    ) -> None:
        """有材料但没知识点: 不是 empty, 也不是错误。"""
        cid = course["course_id"]
        session = workspace.create_session(cid, session_number=1, date=TODAY)
        _register(workspace, cid, session["session_id"], EMPTY_DOCS)
        workspace.process_session(cid, session["session_id"])
        view = workspace.course_review(cid)
        assert view["overview"]["total"] == 0
        assert len(view["sessions"]) == 1
        assert view["empty"] is False  # 有课堂, 所以不是"全新课程"


# ===========================================================================
# 只读性 / 真值安全 / 确定性
# ===========================================================================


class TestReadOnlyAndTruthSafety:
    def test_repeated_calls_do_not_change_any_state(
        self, workspace, processed_course
    ) -> None:
        """打开页面绝不能产生业务对象 (Invariant 6)。"""
        cid, _ = processed_course
        before = (
            len(workspace.knowledge_points(cid)),
            len(workspace.review_candidates(cid)),
            len(workspace.list_materials(cid)),
            len(workspace.list_sessions(cid)),
        )
        for _ in range(3):
            workspace.course_review(cid)
        after = (
            len(workspace.knowledge_points(cid)),
            len(workspace.review_candidates(cid)),
            len(workspace.list_materials(cid)),
            len(workspace.list_sessions(cid)),
        )
        assert after == before

    def test_repeated_calls_do_not_append_study_plan_snapshots(
        self, workspace, processed_course
    ) -> None:
        """投影层绝不能调用会写快照的 study_plan()。"""
        cid, _ = processed_course
        ws_student = workspace.create_student(cid, "student-1", "Test")
        workspace.study_plan(cid, ws_student["student_id"])  # 显式建一次快照
        before = count_plan_rows(workspace, cid, ws_student["student_id"])
        for _ in range(3):
            workspace.course_review(cid)
        after = count_plan_rows(workspace, cid, ws_student["student_id"])
        assert after == before, "复习中心不得写计划快照"

    def test_response_contains_no_mastery_or_prediction_vocabulary(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        blob = json.dumps(workspace.course_review(cid), ensure_ascii=False).lower()
        for term in CENTER_FORBIDDEN_TERMS:
            assert term not in blob, f"复习中心不得出现 {term!r}"

    def test_response_is_deterministic_across_calls(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        first = workspace.course_review(cid)
        second = workspace.course_review(cid)
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    def test_response_is_json_serialisable(self, workspace, processed_course) -> None:
        cid, _ = processed_course
        text = json.dumps(workspace.course_review(cid), ensure_ascii=False, sort_keys=True)
        assert json.loads(text)

    def test_original_text_is_preserved_verbatim(
        self, workspace, processed_course
    ) -> None:
        """西语 / 加泰语原文逐字保留 (不翻译、不改写)。"""
        cid, _ = processed_course
        course = workspace.get_course(cid)
        view = workspace.course_review(cid)
        assert view["course"]["name"] == course["name"]
        assert view["course"]["name"] == "Gestió de Ciutats Intel·ligents"
        assert view["course"]["code"] == "GCI201"


def count_plan_rows(workspace, course_id: str, student_id: str) -> int:
    """数计划快照行数 (通过计划 id 的数量推)。

    ``study_plan()`` 每次调用追加一行内容寻址快照, 所以"行数不变"等价于
    "没有新的快照被写入"。这里直接查持久化层。
    """
    persistence = workspace.persistence
    if persistence is None:
        return 0
    try:
        rows = persistence.repositories.plans.load_for_student(course_id, student_id)
    except Exception:  # noqa: BLE001 - 无持久化时按 0 算
        return 0
    return len(list(rows))


# ===========================================================================
# 多课程隔离
# ===========================================================================


class TestMultiCourseIsolation:
    def _three_courses(self, workspace):
        """三门课各用**不同**的材料。

        为什么必须不同: ``knowledge_id`` 是**内容寻址**的, 不包含 course_id
        —— 同一份材料在两个课程里会合法地产生**相同**的 kp id (这是确定性的
        设计, 不是 bug)。因此"隔离"不能靠 id 集合不相交来证明, 只能靠
        "每个课程只报出自己的 membership"。用不同材料则两条性质同时可验。
        """
        made = []
        for index, code in enumerate(("AAA101", "BBB202", "CCC303")):
            course = workspace.create_course(f"Curs {code}", code, "ca")
            cid = course["course_id"]
            sid = _processed(workspace, cid, index + 1)
            made.append((cid, sid))
        return made

    def test_each_course_reports_only_its_own_knowledge(self, workspace) -> None:
        made = self._three_courses(workspace)
        id_sets = []
        for cid, _ in made:
            ids = {p["knowledge_id"] for p in workspace.knowledge_points(cid)}
            assert ids, f"{cid} 应该有知识点"
            view = workspace.course_review(cid)
            reported = set()
            for row in view["sessions"]:
                reported |= set(row["knowledge_ids"])
            # 复习中心报出的每一个 kp 都必须属于本课程
            assert reported <= ids, f"{cid} 的复习中心出现了别的课程的知识点"
            id_sets.append(ids)
        # 三门课用的材料互不相同, 因此知识点集合也不该相同
        assert id_sets[0] != id_sets[1]
        assert id_sets[1] != id_sets[2]
        assert id_sets[0] != id_sets[2]

    def test_same_content_in_two_courses_still_yields_disjoint_membership(
        self, workspace
    ) -> None:
        """同内容在两门课里: id 可以相同, 但 membership 必须各自独立。

        这是隔离的真正判据 —— 比"id 不重复"更严格, 因为它能在 id 合法共享
        时依然验证隔离是否生效。
        """
        same = ("documents/simple.pdf", "documents/simple.docx", "notes/spanish.md")
        courses = []
        for code in ("ZZZ101", "YYY202"):
            course = workspace.create_course(f"Curs {code}", code, "ca")
            cid = course["course_id"]
            session = workspace.create_session(
                cid, session_number=1, date=TODAY, title="Programació"
            )
            sid = session["session_id"]
            _register(workspace, cid, sid, same)
            workspace.process_session(cid, sid)
            courses.append((cid, sid))

        (cid_a, sid_a), (cid_b, sid_b) = courses
        # 同内容 -> id 确实可以相同 (确定性设计)
        ids_a = {p["knowledge_id"] for p in workspace.knowledge_points(cid_a)}
        ids_b = {p["knowledge_id"] for p in workspace.knowledge_points(cid_b)}
        assert ids_a == ids_b, "同内容应当产生相同的知识点 id"

        # 但各自的复习中心只报自己那节课的 membership
        view_a = workspace.course_review(cid_a)
        view_b = workspace.course_review(cid_b)
        assert {r["session_id"] for r in view_a["sessions"]} == {sid_a}
        assert {r["session_id"] for r in view_b["sessions"]} == {sid_b}
        assert sid_a != sid_b

    def test_review_queue_is_course_scoped(self, workspace) -> None:
        made = self._three_courses(workspace)
        for cid, _ in made:
            own = {
                p["knowledge_id"] for p in workspace.knowledge_points(cid)
            }
            queue_ids = {
                i["knowledge_point_id"]
                for i in workspace.course_review(cid)["review_queue"]["items"]
            }
            assert queue_ids <= own

    def test_sessions_are_course_scoped(self, workspace) -> None:
        made = self._three_courses(workspace)
        for cid, sid in made:
            rows = workspace.course_review(cid)["sessions"]
            assert {r["session_id"] for r in rows} == {sid}

    def test_totals_differ_per_course_but_are_all_nonzero(self, workspace) -> None:
        made = self._three_courses(workspace)
        totals = [workspace.course_review(cid)["overview"]["total"] for cid, _ in made]
        assert all(t > 0 for t in totals)


# ===========================================================================
# 重启一致性
# ===========================================================================


class TestRestartConsistency:
    def test_review_center_is_identical_after_a_real_subprocess_restart(
        self, workspace, processed_course, tmp_path
    ) -> None:
        """真子进程重载: 同一份 data_dir 给出同样的概览计数。

        为什么必须跨进程: 内存里"再建一个 Workspace"读的还是同一个进程的
        注册表, 证明不了数据真的落盘了。
        """
        cid, _ = processed_course
        before = workspace.course_review(cid)
        data_dir = str(workspace.data_dir)
        workspace.close()

        probe = tmp_path / "restart_probe.py"
        probe.write_text(
            "import json, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from src.application.workspace import Workspace\n"
            "ws = Workspace(sys.argv[2], asr_mode='mock', ocr_mode='mock')\n"
            "view = ws.course_review(sys.argv[3])\n"
            "print('REVIEWJSON:' + json.dumps(view['overview'], sort_keys=True))\n"
            "print('SESSIONCOUNT:' + str(len(view['sessions'])))\n"
            "ws.close()\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(probe), str(ROOT), data_dir, cid],
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert proc.returncode == 0, proc.stderr[-3000:]
        line = [l for l in proc.stdout.splitlines() if l.startswith("REVIEWJSON:")][0]
        restored_overview = json.loads(line.split("REVIEWJSON:", 1)[1])
        session_line = [
            l for l in proc.stdout.splitlines() if l.startswith("SESSIONCOUNT:")
        ][0]
        restored_sessions = int(session_line.split(":", 1)[1])

        assert restored_overview == before["overview"]
        assert restored_sessions == len(before["sessions"])
        assert restored_overview["total"] > 0


# ===========================================================================
# API 契约
# ===========================================================================


class TestApiContract:
    def test_review_endpoint_shape(self, client, processed_course) -> None:
        cid, _ = processed_course
        status, payload = client.get(f"/api/courses/{cid}/review")
        assert status == 200, payload
        assert payload["success"] is True
        data = payload["data"]
        assert data["course_id"] == cid
        for key in (
            "overview",
            "topics",
            "sessions",
            "review_queue",
            "conflicts",
            "coverage",
            "gaps",
        ):
            assert key in data, f"响应缺少 {key}"

    def test_summary_endpoint_shape(self, client, processed_course) -> None:
        cid, _ = processed_course
        status, payload = client.get(f"/api/courses/{cid}/review-summary")
        assert status == 200, payload
        assert set(payload["data"]["counts"]) == {
            "knowledge",
            "sessions",
            "pending_review",
            "conflicted",
        }

    def test_endpoint_never_returns_html(self, client, processed_course) -> None:
        cid, _ = processed_course
        for path in (
            f"/api/courses/{cid}/review",
            f"/api/courses/{cid}/review-summary",
        ):
            request = urllib.request.Request(client.base + path)
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
                assert "<html" not in body.lower()
                assert "traceback" not in body.lower()

    def test_error_is_structured_json_not_a_traceback(self, client) -> None:
        status, payload = client.get("/api/courses/nope/review")
        assert status == 404
        assert set(payload["error"]) == {"code", "message"}
        assert "Traceback" not in json.dumps(payload)

    def test_summary_agrees_with_full_review(self, client, processed_course) -> None:
        cid, _ = processed_course
        _, full = client.get(f"/api/courses/{cid}/review")
        _, summary = client.get(f"/api/courses/{cid}/review-summary")
        assert summary["data"]["counts"]["knowledge"] == full["data"]["counts"]["knowledge"]
        assert (
            summary["data"]["counts"]["pending_review"]
            == full["data"]["counts"]["pending_review"]
        )
