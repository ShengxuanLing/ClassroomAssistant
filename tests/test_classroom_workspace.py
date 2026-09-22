# -*- coding: utf-8 -*-
"""Task 56 — Real Classroom Workspace 测试。

覆盖的 spec 条款::

    56.1  Course Workspace    课程名 / 代码 / 教师 / 学期 / 课堂 / 覆盖 / 待审核 / 最近材料
    56.2  Session Workspace   Overview / Materials / Processing / Transcript /
                              OCR / Evidence / Knowledge / Review / Learning
    56.3  Session Status      PLANNED → MATERIALS_ADDED → PROCESSING →
                              PROCESSED → REVIEW_REQUIRED → READY_TO_STUDY
    56.4  Today               今天的课程 / 课堂 / 未处理材料 / 待审核 / 待学习

几条刻意写死的判据 (不是实现细节, 是产品语义)
--------------------------------------------
1. **视图只读**: 连续调用三次, 证据 / 知识点 / 审核记录的数量一个都不许变。
   "打开页面" 绝不能产生业务对象 (Invariant 6 的 UI 侧体现)。
2. **READY_TO_STUDY 不是掌握度**: 断言它的 status_reason 里带着
   "not mastery" —— 这条字符串是给用户的提醒, 不是注释。
3. **不静默升格**: 有冲突 / 未验证的知识点时, 课堂状态必须是
   REVIEW_REQUIRED, 绝不可以是 READY_TO_STUDY。
4. **断链可见**: 证据的来源材料解析不到时, ``material`` 必须是 ``None``,
   由 UI 显示 "Traceability broken" —— 不许被省略掉。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from src.api.server import create_server
from src.application.classroom_view import (
    NO_LEARNING_STATE,
    SESSION_MATERIALS_ADDED,
    SESSION_PLANNED,
    SESSION_PROCESSED,
    SESSION_PROCESSING,
    SESSION_READY_TO_STUDY,
    SESSION_REVIEW_REQUIRED,
    SESSION_STATUSES,
    resolve_session_status,
)
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: 固定时钟。今天的日期就是它 —— "今天" 必须是可测的输入, 不是墙上时间。
FIXED_TIME = "2026-09-17T09:00:00+00:00"
TODAY = "2026-09-17"
OTHER_DAY = "2026-09-10"

DOCUMENTS = ("documents/simple.pdf", "documents/simple.docx")
NOTES = ("notes/spanish.md",)
AUDIO = "tone_3s.wav"
BOARD = "board.png"
EMPTY_DOCS = ("documents/empty.pdf", "documents/empty.docx")


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


@pytest.fixture
def empty_session(workspace, course) -> dict[str, Any]:
    return workspace.create_session(
        course["course_id"], session_number=1, date=TODAY, title="Programació"
    )


def _register(workspace, course_id: str, session_id: str, names) -> list[dict[str, Any]]:
    return [
        workspace.register_material(course_id, str(FIXTURES / name), session_id=session_id)
        for name in names
    ]


@pytest.fixture
def added_session(workspace, course, empty_session):
    """材料已注册, 但一个都没处理。"""
    cid = course["course_id"]
    sid = empty_session["session_id"]
    _register(workspace, cid, sid, DOCUMENTS + NOTES)
    return cid, sid


@pytest.fixture
def queued_session(workspace, added_session):
    """已显式入队 (点了"处理本节课") 但尚未真正跑。"""
    cid, sid = added_session
    workspace.start_session_processing(cid, sid)
    return cid, sid


@pytest.fixture
def processed_session(workspace, added_session):
    """完整跑过一次整堂处理: 3 材料 / 有证据 / 有知识点 / 有待审核。"""
    cid, sid = added_session
    workspace.process_session(cid, sid)
    return cid, sid


@pytest.fixture
def no_knowledge_session(workspace, course):
    """处理成功但一个知识点都没产出 —— 用于 PROCESSED 状态。"""
    cid = course["course_id"]
    session = workspace.create_session(cid, session_number=2, date=TODAY, title="Buit")
    sid = session["session_id"]
    _register(workspace, cid, sid, EMPTY_DOCS)
    workspace.process_session(cid, sid)
    return cid, sid


@pytest.fixture
def ready_session(workspace, processed_session):
    """全部待审核知识点都人工确认过 —— 用于 READY_TO_STUDY。"""
    cid, sid = processed_session
    for point in workspace.knowledge_points(cid, session_id=sid):
        workspace.review_confirm(cid, point["knowledge_id"], note="task56")
    return cid, sid


@pytest.fixture
def client(workspace):
    """真实 HTTP 客户端 (验证 API 契约, 不声称做了浏览器测试)。"""
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
                payload = response.read()
                return response.status, json.loads(payload.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# 56.3 Session status —— 纯函数判据
# ---------------------------------------------------------------------------


class TestSessionStatusRules:
    def test_status_set_matches_the_spec_order(self) -> None:
        assert SESSION_STATUSES == (
            "PLANNED",
            "MATERIALS_ADDED",
            "PROCESSING",
            "PROCESSED",
            "REVIEW_REQUIRED",
            "READY_TO_STUDY",
        )

    def test_no_material_is_planned(self) -> None:
        assert resolve_session_status(material_count=0) == SESSION_PLANNED

    def test_material_without_success_or_queue_is_materials_added(self) -> None:
        assert (
            resolve_session_status(material_count=3, succeeded=0)
            == SESSION_MATERIALS_ADDED
        )

    def test_enqueued_job_is_processing(self) -> None:
        assert (
            resolve_session_status(material_count=3, queued=2) == SESSION_PROCESSING
        )
        assert (
            resolve_session_status(material_count=3, running=1) == SESSION_PROCESSING
        )

    def test_processing_wins_over_everything_below(self) -> None:
        """只要还有作业在跑, 就不能因为"已经有知识点"就跳到后面去。"""
        assert (
            resolve_session_status(
                material_count=3, running=1, succeeded=2, knowledge_count=9,
                pending_review=0,
            )
            == SESSION_PROCESSING
        )

    def test_success_without_knowledge_is_processed(self) -> None:
        assert (
            resolve_session_status(material_count=3, succeeded=3, knowledge_count=0)
            == SESSION_PROCESSED
        )

    def test_knowledge_with_pending_review_is_review_required(self) -> None:
        assert (
            resolve_session_status(
                material_count=3, succeeded=3, knowledge_count=9, pending_review=2
            )
            == SESSION_REVIEW_REQUIRED
        )

    def test_knowledge_without_pending_review_is_ready_to_study(self) -> None:
        assert (
            resolve_session_status(
                material_count=3, succeeded=3, knowledge_count=9, pending_review=0
            )
            == SESSION_READY_TO_STUDY
        )

    def test_negative_counts_degrade_to_zero_instead_of_lying(self) -> None:
        """计数来自外部统计; 宁可退化成 PLANNED, 也不传播脏值。"""
        assert (
            resolve_session_status(material_count=-5, succeeded=-1)
            == SESSION_PLANNED
        )

    def test_conflicted_knowledge_never_reaches_ready_to_study_silently(self) -> None:
        """有待审核候选时, 无论多少材料成功, 都不能是 READY_TO_STUDY。"""
        for succeeded in (1, 5, 100):
            assert (
                resolve_session_status(
                    material_count=succeeded,
                    succeeded=succeeded,
                    knowledge_count=10,
                    pending_review=1,
                )
                == SESSION_REVIEW_REQUIRED
            )


# ---------------------------------------------------------------------------
# 56.1 Course Workspace
# ---------------------------------------------------------------------------


class TestCourseWorkspace:
    def test_exposes_course_identity_fields(self, workspace, course) -> None:
        view = workspace.course_workspace(course["course_id"])
        assert view["course"]["name"] == "Gestió de Ciutats Intel·ligents"
        assert view["course"]["code"] == "GCI201"
        # Teacher / Semester 不在 Course 的顶层字段里, 来自 metadata
        assert view["teacher"] == "Prof. Puig"
        assert view["semester"] == "2026-2"

    def test_missing_metadata_yields_none_not_a_guess(self, workspace) -> None:
        course = workspace.create_course("Sense Metadades", "SM1", "es")
        view = workspace.course_workspace(course["course_id"])
        assert view["teacher"] is None
        assert view["semester"] is None

    def test_counts_match_the_underlying_services(self, workspace, processed_session) -> None:
        cid, _ = processed_session
        view = workspace.course_workspace(cid)
        assert view["counts"]["sessions"] == len(workspace.list_sessions(cid))
        assert view["counts"]["materials"] == len(workspace.list_materials(cid))
        assert view["counts"]["knowledge_points"] == len(workspace.knowledge_points(cid))
        assert view["counts"]["pending_review"] == len(workspace.review_candidates(cid))

    def test_sessions_carry_their_own_status_and_counts(
        self, workspace, processed_session
    ) -> None:
        cid, sid = processed_session
        view = workspace.course_workspace(cid)
        entry = [s for s in view["sessions"] if s["session_id"] == sid][0]
        assert entry["status"] == SESSION_REVIEW_REQUIRED
        assert entry["counts"]["materials"] == 3
        assert entry["counts"]["knowledge_points"] > 0
        assert entry["counts"]["pending_review"] > 0

    def test_coverage_is_present_and_course_scoped(self, workspace, course) -> None:
        view = workspace.course_workspace(course["course_id"])
        assert view["coverage"]["course_id"] == course["course_id"]

    def test_recent_materials_are_newest_first_and_deterministic(
        self, workspace, processed_session
    ) -> None:
        cid, _ = processed_session
        view = workspace.course_workspace(cid)
        recent = view["recent_materials"]
        assert recent, "有材料就应该有 recent_materials"
        # 固定时钟下 created_at 全相同 -> 退化为 material_id 升序 (全序)
        ids = [str(item["material_id"]) for item in recent]
        assert ids == sorted(ids)

    def test_pending_review_ids_are_sorted_and_match_the_list(
        self, workspace, processed_session
    ) -> None:
        cid, _ = processed_session
        view = workspace.course_workspace(cid)
        listed = sorted(
            str(item["knowledge_point_id"]) for item in view["pending_review"]
        )
        assert view["pending_review_ids"] == listed

    def test_empty_course_is_a_legal_state(self, workspace, course) -> None:
        view = workspace.course_workspace(course["course_id"])
        assert view["counts"]["sessions"] == 0
        assert view["sessions"] == []
        assert view["recent_materials"] == []

    def test_unknown_course_raises(self, workspace) -> None:
        with pytest.raises(Exception):
            workspace.course_workspace("course-does-not-exist")

    def test_blank_course_id_is_rejected(self, workspace) -> None:
        with pytest.raises(Exception):
            workspace.course_workspace("   ")


# ---------------------------------------------------------------------------
# 56.2 / 56.3 Session Workspace
# ---------------------------------------------------------------------------


class TestSessionWorkspace:
    def test_all_nine_sections_are_present(self, workspace, processed_session) -> None:
        cid, sid = processed_session
        view = workspace.session_workspace(cid, sid)
        for key in (
            "overview",
            "materials",
            "processing",
            "transcript",
            "ocr",
            "evidence",
            "knowledge",
            "review",
            "learning",
        ):
            assert key in view, f"课堂工作台缺少分区 {key}"

    def test_planned_when_nothing_is_registered(self, workspace, empty_session) -> None:
        cid, sid = course_id_of(empty_session), empty_session["session_id"]
        view = workspace.session_workspace(cid, sid)
        assert view["status"] == SESSION_PLANNED
        assert "No materials" in view["status_reason"]

    def test_materials_added_after_upload_without_processing(
        self, workspace, added_session
    ) -> None:
        cid, sid = added_session
        view = workspace.session_workspace(cid, sid)
        assert view["status"] == SESSION_MATERIALS_ADDED
        assert view["overview"]["materials"] == 3
        assert view["overview"]["evidence"] == 0

    def test_processing_after_the_user_clicks_process(
        self, workspace, queued_session
    ) -> None:
        cid, sid = queued_session
        view = workspace.session_workspace(cid, sid)
        assert view["status"] == SESSION_PROCESSING

    def test_review_required_after_a_real_run(self, workspace, processed_session) -> None:
        cid, sid = processed_session
        view = workspace.session_workspace(cid, sid)
        assert view["status"] == SESSION_REVIEW_REQUIRED
        assert view["overview"]["pending_review"] == len(view["review"]["pending"])

    def test_processed_when_nothing_was_assembled(
        self, workspace, no_knowledge_session
    ) -> None:
        cid, sid = no_knowledge_session
        view = workspace.session_workspace(cid, sid)
        assert view["status"] == SESSION_PROCESSED
        assert view["overview"]["knowledge_points"] == 0

    def test_ready_to_study_once_everything_is_reviewed(
        self, workspace, ready_session
    ) -> None:
        cid, sid = ready_session
        view = workspace.session_workspace(cid, sid)
        assert view["status"] == SESSION_READY_TO_STUDY

    def test_ready_to_study_explicitly_denies_mastery(
        self, workspace, ready_session
    ) -> None:
        """READY_TO_STUDY 只表示"没有挂起的审核", UI 必须把这句话带出来。"""
        cid, sid = ready_session
        view = workspace.session_workspace(cid, sid)
        assert "not mastery" in view["status_reason"]

    def test_evidence_is_grouped_into_transcript_ocr_document_note(
        self, workspace, processed_session
    ) -> None:
        cid, sid = processed_session
        view = workspace.session_workspace(cid, sid)
        grouped = (
            len(view["transcript"])
            + len(view["ocr"])
            + len(view["documents"])
            + len(view["notes"])
        )
        assert grouped <= view["overview"]["evidence"]
        # 每个分组里的 evidence_type 必须与分组名一致 (不允许归错堆)
        assert all(
            str(e["evidence_type"]).lower() == "transcript" for e in view["transcript"]
        )
        assert all(str(e["evidence_type"]).lower() == "ocr" for e in view["ocr"])
        assert all(
            str(e["evidence_type"]).lower() == "document" for e in view["documents"]
        )

    def test_evidence_carries_its_source_material(self, workspace, processed_session) -> None:
        cid, sid = processed_session
        view = workspace.session_workspace(cid, sid)
        material_ids = {str(m["material_id"]) for m in view["materials"]}
        for evidence in view["evidence"]:
            assert evidence["material_id"] in material_ids
            assert evidence["material"] is not None, "断链被静默吞掉了"
            assert evidence["material"]["filename"]

    def test_transcript_keeps_audio_timestamps(self, workspace, course) -> None:
        cid = course["course_id"]
        session = workspace.create_session(cid, 3, TODAY, "Àudio")
        sid = session["session_id"]
        _register(workspace, cid, sid, (AUDIO,))
        workspace.process_session(cid, sid)
        view = workspace.session_workspace(cid, sid)
        assert view["transcript"], "音频材料应产出 transcript 证据"
        for evidence in view["transcript"]:
            source = evidence.get("source") or {}
            # 时间戳可以缺失 (不是所有来源都有), 但**不能是伪造的**
            assert source.get("timestamp_start") is None or isinstance(
                source.get("timestamp_start"), (int, float)
            )

    def test_board_evidence_lands_in_ocr(self, workspace, course) -> None:
        cid = course["course_id"]
        session = workspace.create_session(cid, 4, TODAY, "Pissarra")
        sid = session["session_id"]
        _register(workspace, cid, sid, (BOARD,))
        workspace.process_session(cid, sid)
        view = workspace.session_workspace(cid, sid)
        assert view["ocr"], "板书图片应产出 OCR 证据"

    def test_session_and_course_mismatch_is_rejected(self, workspace, course) -> None:
        other = workspace.create_course("Altra", "ALT1", "es")
        session = workspace.create_session(other["course_id"], 1, TODAY, "Aliena")
        with pytest.raises(Exception):
            workspace.session_workspace(course["course_id"], session["session_id"])

    def test_learning_without_a_student_says_so(self, workspace, processed_session) -> None:
        cid, sid = processed_session
        view = workspace.session_workspace(cid, sid)
        assert view["learning"]["available"] is False
        assert view["learning"]["student_id"] is None
        assert view["learning"]["note"] == NO_LEARNING_STATE

    def test_learning_reports_real_student_state(self, workspace, ready_session) -> None:
        cid, sid = ready_session
        student = workspace.create_student(cid, "stu-1", "Alumne")
        workspace.record_learning_event(cid, student["student_id"], _first_kp(workspace, cid, sid), "viewed")
        view = workspace.session_workspace(cid, sid)
        learning = view["learning"]
        assert learning["available"] is True
        assert learning["student_id"] == "stu-1"
        assert learning["states"], "记录过学习事件就该有状态"
        assert all(
            s["knowledge_point_id"] in view["knowledge"]["knowledge_ids"]
            for s in learning["states"]
        )

    def test_review_history_is_scoped_to_this_session(
        self, workspace, ready_session
    ) -> None:
        cid, sid = ready_session
        view = workspace.session_workspace(cid, sid)
        session_ids = set(view["knowledge"]["knowledge_ids"])
        for record in view["review"]["history"]:
            assert record["knowledge_point_id"] in session_ids


def course_id_of(session: dict[str, Any]) -> str:
    return str(session["course_id"])


def _first_kp(workspace, course_id: str, session_id: str) -> str:
    points = workspace.knowledge_points(course_id, session_id=session_id)
    assert points, "测试前提不成立: 该课堂没有知识点"
    return str(points[0]["knowledge_id"])


# ---------------------------------------------------------------------------
# 56.4 Today
# ---------------------------------------------------------------------------


class TestToday:
    def test_date_comes_from_the_workspace_clock(self, workspace, course) -> None:
        view = workspace.today()
        assert view["date"] == TODAY
        assert view["generated_at"] == FIXED_TIME

    def test_only_today_s_sessions_are_listed(self, workspace, course) -> None:
        cid = course["course_id"]
        today_session = workspace.create_session(cid, 1, TODAY, "Avui")
        workspace.create_session(cid, 2, OTHER_DAY, "Ahir")
        view = workspace.today()
        listed = {s["session_id"] for s in view["sessions_today"]}
        assert listed == {today_session["session_id"]}

    def test_courses_today_only_includes_courses_with_a_session(
        self, workspace, course
    ) -> None:
        workspace.create_session(course["course_id"], 1, TODAY, "Avui")
        other = workspace.create_course("Altra", "ALT1", "es")
        workspace.create_session(other["course_id"], 1, OTHER_DAY, "Ahir")
        view = workspace.today()
        ids = {c["course_id"] for c in view["courses_today"]}
        assert ids == {course["course_id"]}

    def test_pending_materials_only_counts_unprocessed(
        self, workspace, added_session
    ) -> None:
        cid, _ = added_session
        assert workspace.today()["counts"]["pending_materials"] == 3
        workspace.process_session(cid, added_session[1])
        assert workspace.today()["counts"]["pending_materials"] == 0

    def test_pending_review_aggregates_across_courses(self, workspace, course) -> None:
        cid = course["course_id"]
        session = workspace.create_session(cid, 1, TODAY, "Avui")
        _register(workspace, cid, session["session_id"], DOCUMENTS)
        workspace.process_session(cid, session["session_id"])
        view = workspace.today()
        assert view["counts"]["pending_review"] == len(workspace.review_candidates(cid))

    def test_study_without_a_student_is_explicit(self, workspace, course) -> None:
        view = workspace.today()
        assert view["study"]["available"] is False
        assert view["study"]["note"] == NO_LEARNING_STATE
        assert view["study"]["items"] == []

    def test_study_items_come_from_the_study_plan(self, workspace, ready_session) -> None:
        cid, _ = ready_session
        student = workspace.create_student(cid, "stu-1", "Alumne")
        plan = workspace.study_plan(cid, student["student_id"])
        view = workspace.today(student_id=student["student_id"])
        assert view["study"]["available"] is True
        assert view["study"]["student_id"] == "stu-1"
        assert view["study"]["items"] == [
            dict(item) for item in (plan.get("items") or [])
        ]

    def test_course_filter_narrows_everything(self, workspace, course) -> None:
        cid = course["course_id"]
        workspace.create_session(cid, 1, TODAY, "Avui")
        other = workspace.create_course("Altra", "ALT1", "es")
        workspace.create_session(other["course_id"], 1, TODAY, "També avui")
        view = workspace.today(course_id=cid)
        assert view["course_id"] == cid
        assert {c["course_id"] for c in view["courses_today"]} == {cid}

    def test_unknown_course_filter_raises(self, workspace) -> None:
        with pytest.raises(Exception):
            workspace.today(course_id="course-nope")

    def test_counts_match_the_rendered_lists(self, workspace, added_session) -> None:
        view = workspace.today()
        assert view["counts"]["sessions_today"] == len(view["sessions_today"])
        assert view["counts"]["pending_review"] == len(view["pending_review"])
        assert view["counts"]["pending_materials"] >= len(view["pending_materials"])

    def test_no_courses_is_a_legal_state(self, workspace) -> None:
        view = workspace.today()
        assert view["courses_today"] == []
        assert view["sessions_today"] == []
        assert view["counts"]["courses_today"] == 0


# ---------------------------------------------------------------------------
# 只读性 / 确定性 (视图绝不能产生业务对象)
# ---------------------------------------------------------------------------


class TestViewsAreReadOnlyAndDeterministic:
    def test_repeated_calls_do_not_create_business_objects(
        self, workspace, processed_session
    ) -> None:
        cid, sid = processed_session
        before = _business_snapshot(workspace, cid)
        for _ in range(3):
            workspace.course_workspace(cid)
            workspace.session_workspace(cid, sid)
            workspace.today()
        after = _business_snapshot(workspace, cid)
        assert before == after, f"打开页面产生了业务对象: {before} -> {after}"

    def test_same_input_gives_identical_output(self, workspace, processed_session) -> None:
        cid, sid = processed_session
        first = json.dumps(
            [workspace.course_workspace(cid), workspace.session_workspace(cid, sid)],
            ensure_ascii=False,
            sort_keys=True,
        )
        second = json.dumps(
            [workspace.course_workspace(cid), workspace.session_workspace(cid, sid)],
            ensure_ascii=False,
            sort_keys=True,
        )
        assert first == second

    def test_today_does_not_append_a_study_plan_snapshot(
        self, workspace, ready_session
    ) -> None:
        """``Workspace.study_plan()`` 会追加一条计划快照行。

        "今日" 是纯展示视图, 走的是 learning_service 而不是它 —— 否则每刷新
        一次页面就多一条快照, 那不是数据, 是垃圾。
        """
        cid, _ = ready_session
        student = workspace.create_student(cid, "stu-1", "Alumne")
        workspace.study_plan(cid, student["student_id"])
        before = workspace.persistence.counts().get("study_plans", 0)
        workspace.today(student_id=student["student_id"])
        workspace.today(student_id=student["student_id"])
        assert workspace.persistence.counts().get("study_plans", 0) == before

    def test_session_lists_are_deterministically_ordered(
        self, workspace, processed_session
    ) -> None:
        cid, sid = processed_session
        view = workspace.session_workspace(cid, sid)
        assert _ids(view["materials"], "material_id") == sorted(
            _ids(view["materials"], "material_id")
        )
        assert view["knowledge"]["knowledge_ids"] == sorted(
            view["knowledge"]["knowledge_ids"]
        )


def _ids(rows, key: str) -> list[str]:
    return [str(row[key]) for row in rows]


def _business_snapshot(workspace, course_id: str) -> dict[str, int]:
    return {
        "materials": len(workspace.list_materials(course_id)),
        "knowledge_points": len(workspace.knowledge_points(course_id)),
        "review_candidates": len(workspace.review_candidates(course_id)),
        "evidence": sum(
            len(m.get("evidence_ids") or []) for m in workspace.list_materials(course_id)
        ),
        "sessions": len(workspace.list_sessions(course_id)),
        "students": len(workspace.list_students(course_id)),
        "exercises": len(workspace.list_exercises(course_id)),
    }


# ---------------------------------------------------------------------------
# API 契约
# ---------------------------------------------------------------------------


class TestApiContract:
    def test_course_workspace_endpoint(self, client, workspace, course) -> None:
        cid = course["course_id"]
        status, payload = client.get(f"/api/courses/{cid}/workspace")
        assert status == 200
        assert payload["success"] is True
        assert payload["data"]["course_id"] == cid
        assert payload["data"]["teacher"] == "Prof. Puig"

    def test_session_workspace_endpoint(self, client, workspace, processed_session) -> None:
        cid, sid = processed_session
        status, payload = client.get(
            f"/api/courses/{cid}/sessions/{sid}/workspace"
        )
        assert status == 200
        assert payload["data"]["status"] == SESSION_REVIEW_REQUIRED
        for key in ("overview", "transcript", "ocr", "evidence", "review"):
            assert key in payload["data"]

    def test_today_endpoint(self, client, workspace, course) -> None:
        workspace.create_session(course["course_id"], 1, TODAY, "Avui")
        status, payload = client.get("/api/today")
        assert status == 200
        assert payload["data"]["date"] == TODAY
        for key in (
            "courses_today",
            "sessions_today",
            "pending_materials",
            "pending_review",
            "study",
            "counts",
        ):
            assert key in payload["data"]

    def test_today_accepts_a_course_filter(self, client, workspace, course) -> None:
        cid = course["course_id"]
        workspace.create_session(cid, 1, TODAY, "Avui")
        status, payload = client.get(f"/api/today?course_id={cid}")
        assert status == 200
        assert payload["data"]["course_id"] == cid

    def test_unknown_course_returns_an_error_not_a_blank_page(
        self, client, workspace
    ) -> None:
        status, payload = client.get("/api/courses/course-nope/workspace")
        assert status >= 400
        assert payload["success"] is False

    def test_unknown_session_returns_an_error(self, client, workspace, course) -> None:
        status, payload = client.get(
            f"/api/courses/{course['course_id']}/sessions/session-nope/workspace"
        )
        assert status >= 400
        assert payload["success"] is False


# ---------------------------------------------------------------------------
# 真实验收数据集集成 (不是 synthetic 桩)
# ---------------------------------------------------------------------------


class TestRealAcceptanceDataset:
    """用 ``tests/fixtures/acceptance`` 的真实课堂数据跑一遍。

    这条集成测试存在的理由: 上面的 fixture 只用 3 份材料, 而真实一节课有
    PDF / DOCX / 音频 / 板书 / 笔记且**故意包含一份坏 PDF**。状态与分区
    在"有失败材料"时必须仍然成立。
    """

    @pytest.fixture
    def harness(self, tmp_path):
        from src.application.acceptance import (
            AcceptanceHarness,
            ClassroomDataset,
        )

        dataset = ClassroomDataset.from_directory(str(FIXTURES / "acceptance"))
        instance = AcceptanceHarness(dataset, data_dir=str(tmp_path / "data"))
        for name in (
            "_step_create_course",
            "_step_create_session",
            "_step_upload_documents",
            "_step_upload_audio",
            "_step_upload_board_image",
            "_step_process",
            "_step_evidence",
            "_step_knowledge",
            "_step_validation",
        ):
            getattr(instance, name)()
        yield instance
        instance.workspace.close()

    def test_session_status_is_review_required_with_real_data(self, harness) -> None:
        view = harness.workspace.session_workspace(harness.course_id, harness.session_id)
        assert view["status"] == SESSION_REVIEW_REQUIRED

    def test_failed_material_does_not_hide_the_rest(self, harness) -> None:
        view = harness.workspace.session_workspace(harness.course_id, harness.session_id)
        statuses = view["overview"]["materials_by_status"]
        assert statuses.get("COMPLETED", 0) >= 4
        assert statuses.get("FAILED", 0) >= 1

    def test_every_evidence_resolves_to_a_registered_material(self, harness) -> None:
        view = harness.workspace.session_workspace(harness.course_id, harness.session_id)
        material_ids = {str(m["material_id"]) for m in view["materials"]}
        for evidence in view["evidence"]:
            assert evidence["material_id"] in material_ids
            assert evidence["material"] is not None

    def test_course_workspace_counts_agree_with_the_services(self, harness) -> None:
        ws = harness.workspace
        view = ws.course_workspace(harness.course_id)
        assert view["counts"]["knowledge_points"] == len(
            ws.knowledge_points(harness.course_id)
        )
        assert view["counts"]["pending_review"] == len(
            ws.review_candidates(harness.course_id)
        )
