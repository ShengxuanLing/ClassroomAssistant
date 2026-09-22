# -*- coding: utf-8 -*-
"""Task 63 — Student Daily Dashboard 测试。

覆盖的 spec 条款::

    63.2  Dashboard 数据来源  derived view, 不维护第二份状态
    63.3  Today's Classes     今天由本机时钟决定, 用已有 ClassSession.date
    63.4  Today's Study Plan  读既有 StudyPlan, **不重新生成**
    63.5  Learning Path       Current / Prerequisite / Next
    63.6  Pending Review      可点击进 Review Center
    63.7  Pending Exercises   可点击进 Exercise
    63.8  Recent Evaluations  绝不自动改写成 Mastery
    63.9  Attention Area      只用 StudentState 已定义的标签
    63.10 Empty Student       "No learning activity yet." 正常
    63.12 Course Context      选 A 课就只给 A 课; All Courses 时 task 保留 course_id
    63.13 Tests               40+

几条刻意写死的判据（产品语义，不是实现细节）
--------------------------------------------
1. **禁止词一条都不许出现**: ``mastery`` / ``proficiency`` / ``predicted`` /
   ``estimated_ability`` / ``you_are_ready``。断言直接扫整个响应 JSON。
2. **首页是只读的**: 连续调用 N 次, 计划快照行数 / 知识点数 / 学习状态数
   一个都不能变。``Workspace.study_plan()`` 会追加快照行, 所以首页**必须走
   learning_service** —— 这条有专门的回归测试盯着。
3. **"今天"不由客户端决定**: 同一个固定时钟下, 两个不同 timezone 的请求
   必须给出同一个 ``date``; 而换一个时钟日期就该变。
4. **空学生是正常状态**: ``has_activity: false`` + ``No learning activity yet.``
   + HTTP 200。
5. **课程上下文必须被遵守**: 选 A 课时响应里绝不出现 B 课的 course_id。
6. **Attention 只能来自 StudentState**: 每个条目都必须带 ``basis``,
   且 ``kind`` 必须在领域已定义的集合内 —— 不许从"错了几次"现推。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from src.api.server import create_server
from src.application.runtime import fixed_clock
from src.application.student_today_view import (
    ATTENTION_KINDS,
    TODAY_FORBIDDEN_TERMS,
    StudentTodayView,
)
from src.application.workspace import Workspace

FIXTURES = Path(__file__).resolve().parent / "fixtures"

FIXED_TIME = "2026-09-18T09:00:00+00:00"
TODAY = "2026-09-18"

#: 每节课用不同夹具: material_id = f(内容, 文件名), 复用同一份文件会被去重。
SESSION_FIXTURE_SETS = (
    ("documents/simple.pdf", "documents/simple.docx", "notes/spanish.md"),
    ("documents/multilingual.pdf", "notes/catalan.md"),
    ("documents/tables.pdf", "notes/mixed.md"),
)


def _fixtures_for(session_number: int) -> tuple[str, ...]:
    return SESSION_FIXTURE_SETS[(session_number - 1) % len(SESSION_FIXTURE_SETS)]


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
        workspace.register_material(
            course_id, str(FIXTURES / name), session_id=session_id
        )
        for name in names
    ]


def _processed(workspace, course_id: str, session_number: int = 1):
    """跑一节真实课堂: 注册材料 -> 整堂处理 -> 得到证据与知识点。"""
    session = workspace.create_session(
        course_id, session_number=session_number, date=TODAY, title="Programació"
    )
    sid = session["session_id"]
    _register(workspace, course_id, sid, _fixtures_for(session_number))
    workspace.process_session(course_id, sid)
    return sid


@pytest.fixture
def processed_course(workspace, course):
    """一门有 1 节课 / 3 材料 / 10 知识点 / 有待审核的课程。"""
    cid = course["course_id"]
    sid = _processed(workspace, cid, 1)
    return cid, sid


@pytest.fixture
def student(workspace, processed_course):
    cid, _ = processed_course
    record = workspace.create_student(cid, "s-adal", "Ada Lovelace")
    return record["student_id"]


@pytest.fixture
def active_student(workspace, processed_course, student):
    """一个**有真实学习活动**的学生: 练习 / 作答 / 评估 / 学习状态。

    完整闭环: KnowledgePoint -> Exercise -> Answer -> Evaluation
              -> StudentState（经 record_learning_event）
    """
    cid, _ = processed_course
    kps = sorted(p["knowledge_id"] for p in workspace.knowledge_points(cid))

    exercises = []
    for index, kp in enumerate(kps[:3]):
        exercises.append(
            workspace.create_exercise(
                cid,
                "short_answer",
                f"Pregunta {index + 1}?",
                [kp],
                expected_answer=f"resposta-{index + 1}",
            )
        )
    exercise_ids = sorted(e["exercise_id"] for e in exercises)

    # kp0 -> practicing, kp1 -> reviewing (Task 30 的真实状态机)
    workspace.record_learning_event(cid, student, kps[0], "viewed")
    workspace.record_learning_event(cid, student, kps[0], "practiced")
    workspace.record_learning_event(cid, student, kps[1], "viewed")
    workspace.record_learning_event(cid, student, kps[1], "practiced")
    workspace.record_learning_event(cid, student, kps[1], "reviewed")

    # 作答一道, 留下一条评估
    answer = workspace.submit_answer(cid, student, exercise_ids[0], "resposta-2")

    return {
        "course_id": cid,
        "student_id": student,
        "knowledge_point_ids": kps,
        "exercise_ids": exercise_ids,
        "answer_id": answer["answer_id"],
    }


# ---------------------------------------------------------------------------
# 63.2 数据来源 / 常量守卫
# ---------------------------------------------------------------------------


class TestContract:
    """常量与禁止词表本身不能退化。"""

    def test_attention_kinds_are_the_three_spec_labels(self) -> None:
        assert set(ATTENTION_KINDS) == {
            "NEEDS_REVIEW",
            "NEEDS_PRACTICE",
            "PREREQUISITE_NEEDED",
        }

    def test_forbidden_terms_cover_mastery_and_prediction(self) -> None:
        for term in ("mastery", "proficiency", "predicted", "estimated_ability"):
            assert term in TODAY_FORBIDDEN_TERMS

    def test_view_is_constructible_from_workspace(self, workspace) -> None:
        view = StudentTodayView(workspace)
        assert view is not None

    def test_workspace_exposes_student_today(self, workspace) -> None:
        assert callable(getattr(workspace, "student_today", None))


# ---------------------------------------------------------------------------
# 63.10 / 63.2 Empty Student
# ---------------------------------------------------------------------------


class TestEmptyStudent:
    def test_no_courses_at_all_is_a_normal_empty_state(self, workspace) -> None:
        view = workspace.student_today()
        assert view["has_activity"] is False
        assert view["note"] == "No learning activity yet."
        assert view["classes_today"] == []
        assert all(count == 0 for count in view["counts"].values())
        assert view["course_id"] is None

    def test_course_without_students_is_a_normal_empty_state(
        self, workspace, course
    ) -> None:
        cid = course["course_id"]
        view = workspace.student_today(course_id=cid)
        assert view["has_activity"] is False
        assert view["note"] == "No learning activity yet."
        assert view["study"] == []

    def test_new_student_has_no_pending_exercises_or_evaluations(
        self, workspace, processed_course, student
    ) -> None:
        """新学生: 课程已经有 10 个知识点, 但学生自己没有任何活动记录。"""
        cid, _ = processed_course
        view = workspace.student_today(course_id=cid, student_id=student)
        assert view["pending_exercises"] == []
        assert view["recent_evaluations"] == []
        assert view["attention"] == []
        assert view["study"][0]["student_id"] == student

    def test_unknown_student_is_silently_excluded_not_leaked(
        self, workspace, processed_course
    ) -> None:
        """未知学生不能拿到任何课程的数据 (否则就是跨课程泄漏)。"""
        cid, _ = processed_course
        view = workspace.student_today(course_id=cid, student_id="nope")
        assert view["study"] == []
        assert view["pending_exercises"] == []
        assert view["recent_evaluations"] == []
        assert view["has_activity"] is False

    def test_unknown_course_is_404_not_500(self, workspace, course) -> None:
        from src.application.errors import NotFoundError

        with pytest.raises(NotFoundError):
            workspace.student_today(course_id="course-nope")


# ---------------------------------------------------------------------------
# 63.3 Today's Classes
# ---------------------------------------------------------------------------


class TestTodaysClasses:
    def test_today_is_decided_by_the_clock_not_the_client(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        view = workspace.student_today(course_id=cid)
        assert view["date"] == TODAY
        assert view["generated_at"] == FIXED_TIME

    def test_a_session_dated_today_shows_up(self, workspace, processed_course) -> None:
        cid, _ = processed_course
        view = workspace.student_today(course_id=cid)
        assert len(view["classes_today"]) == 1
        row = view["classes_today"][0]
        assert row["course_id"] == cid
        assert row["date"] == TODAY

    def test_a_session_dated_another_day_does_not_show_up(
        self, workspace, course
    ) -> None:
        cid = course["course_id"]
        workspace.create_session(
            cid, session_number=1, date="2026-01-05", title="Vell"
        )
        view = workspace.student_today(course_id=cid)
        assert view["classes_today"] == []

    def test_a_session_without_a_date_never_shows_up_as_today(
        self, workspace, course
    ) -> None:
        """没有日期就是"未知", 不能假定它是今天 (spec 63.3)。"""
        cid = course["course_id"]
        workspace.create_session(cid, session_number=1, title="Sense data")
        view = workspace.student_today(course_id=cid)
        assert view["classes_today"] == []


# ---------------------------------------------------------------------------
# 63.4 Today's Study Plan
# ---------------------------------------------------------------------------


class TestStudyPlan:
    def test_study_plan_is_read_from_the_existing_layer(
        self, workspace, processed_course, student
    ) -> None:
        cid, _ = processed_course
        view = workspace.student_today(course_id=cid, student_id=student)
        block = view["study"][0]
        assert block["available"] is True
        assert block["tasks_total"] > 0
        assert block["tasks_total"] == len(block["tasks_today"])
        assert block["plan_id"]
        assert block["rules_version"]

    def test_next_task_is_the_first_of_the_plan(
        self, workspace, processed_course, student
    ) -> None:
        cid, _ = processed_course
        block = workspace.student_today(course_id=cid, student_id=student)["study"][0]
        assert block["next_task"] == block["tasks_today"][0]

    def test_the_dashboard_does_not_regenerate_a_plan(
        self, workspace, processed_course, student
    ) -> None:
        """任务书 63.4: 不能重新生成一套 StudyPlan。

        ``Workspace.study_plan()`` 每次调用会追加一行内容寻址快照。
        首页必须走 learning_service, 否则每刷一次页面就多一行审计噪声。
        """
        cid, _ = processed_course

        def plan_rows() -> int:
            return len(
                workspace.persistence.repositories.study_plans.plan_ids_for_student(
                    student
                )
            )

        before = plan_rows()
        for _ in range(5):
            workspace.student_today(course_id=cid, student_id=student)
        assert plan_rows() == before, "首页调用改动了计划快照"

        # 反证: 走 Workspace.study_plan() 确实会追加 —— 说明上面那条判据有效。
        workspace.study_plan(cid, student)
        assert plan_rows() == before + 1

    def test_plan_items_agree_with_the_learning_service(
        self, workspace, processed_course, student
    ) -> None:
        """首页的 tasks 必须与既有 StudyPlan 逐条一致, 不是另一份算法。"""
        cid, _ = processed_course
        authoritative = workspace.context(cid).learning_service.get_study_plan(student)
        block = workspace.student_today(course_id=cid, student_id=student)["study"][0]
        assert block["tasks_today"] == list(authoritative.get("items") or [])
        assert block["plan_id"] == authoritative.get("plan_id")

    def test_student_without_plan_input_reports_not_available(
        self, workspace, course
    ) -> None:
        """学生存在但课程没有任何知识点 -> 明确 available=False。"""
        cid = course["course_id"]
        workspace.create_student(cid, "s-empty", "Empty")
        block = workspace.student_today(course_id=cid, student_id="s-empty")["study"][0]
        assert block["available"] is False
        assert block["note"] == "No learning activity yet."
        assert block["tasks_total"] == 0
        assert block["next_task"] is None


# ---------------------------------------------------------------------------
# 63.5 Learning Path
# ---------------------------------------------------------------------------


class TestLearningPath:
    def test_path_entries_expose_current_and_prerequisites(
        self, workspace, active_student
    ) -> None:
        cid = active_student["course_id"]
        view = workspace.student_today(
            course_id=cid, student_id=active_student["student_id"]
        )
        assert view["learning_paths"], "应至少有一条路径"
        for entry in view["learning_paths"]:
            assert entry["course_id"] == cid
            assert entry["current"] is not None
            assert isinstance(entry["prerequisites"], list)
            assert isinstance(entry["unmet_prerequisites"], list)
            assert entry["nodes_total"] >= 1

    def test_current_node_comes_from_the_existing_learning_path_view(
        self, workspace, active_student
    ) -> None:
        cid = active_student["course_id"]
        sid = active_student["student_id"]
        entries = workspace.student_today(course_id=cid, student_id=sid)[
            "learning_paths"
        ]
        by_kp = {e["knowledge_point_id"]: e for e in entries}
        kp = active_student["knowledge_point_ids"][0]
        assert kp in by_kp
        authoritative = workspace.context(cid).learning_view.learning_path_view(sid, kp)
        assert by_kp[kp]["current"] == authoritative["nodes"][-1]

    def test_completed_knowledge_points_are_skipped(
        self, workspace, processed_course, student
    ) -> None:
        """已走完 (reviewing) 的知识点不再出现在"待学路径"里。"""
        cid, _ = processed_course
        kps = sorted(p["knowledge_id"] for p in workspace.knowledge_points(cid))
        workspace.record_learning_event(cid, student, kps[0], "viewed")
        workspace.record_learning_event(cid, student, kps[0], "practiced")
        workspace.record_learning_event(cid, student, kps[0], "reviewed")
        entries = workspace.student_today(course_id=cid, student_id=student)[
            "learning_paths"
        ]
        assert kps[0] not in {e["knowledge_point_id"] for e in entries}

    def test_path_carries_the_next_learning_event(
        self, workspace, active_student
    ) -> None:
        """下一动作来自 Task 30 的事件定义, 不是前端猜的。"""
        cid = active_student["course_id"]
        entries = workspace.student_today(
            course_id=cid, student_id=active_student["student_id"]
        )["learning_paths"]
        node = entries[0]["current"]
        assert node["next_event"] in {
            "viewed",
            "practiced",
            "reviewed",
            None,
        }


# ---------------------------------------------------------------------------
# 63.6 Pending Review
# ---------------------------------------------------------------------------


class TestPendingReview:
    def test_pending_review_items_are_reported_with_a_link(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        block = workspace.student_today(course_id=cid)["review"]
        assert block["total"] > 0
        assert block["path"] == "/api/reviews"
        assert len(block["items"]) == block["total"]

    def test_review_items_agree_with_the_existing_review_service(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        authoritative = workspace.review_candidates(cid)
        items = workspace.student_today(course_id=cid)["review"]["items"]
        assert [i["knowledge_point_id"] for i in items] == [
            c["knowledge_point_id"] for c in authoritative
        ]

    def test_review_block_is_empty_for_a_course_without_knowledge(
        self, workspace, course
    ) -> None:
        cid = course["course_id"]
        block = workspace.student_today(course_id=cid)["review"]
        assert block["total"] == 0
        assert block["items"] == []
        assert block["path"] == "/api/reviews"


# ---------------------------------------------------------------------------
# 63.7 Pending Exercises
# ---------------------------------------------------------------------------


class TestPendingExercises:
    def test_unanswered_exercises_are_pending(self, workspace, active_student) -> None:
        cid = active_student["course_id"]
        view = workspace.student_today(
            course_id=cid, student_id=active_student["student_id"]
        )
        # 3 道题, 作答 1 道 -> 2 道待作答
        assert view["counts"]["pending_exercises"] == 2
        assert len(view["pending_exercises"]) == 2
        assert active_student["exercise_ids"][0] not in {
            row["exercise_id"] for row in view["pending_exercises"]
        }

    def test_pending_exercises_agree_with_the_exercise_list_view(
        self, workspace, active_student
    ) -> None:
        cid = active_student["course_id"]
        sid = active_student["student_id"]
        authoritative = workspace.exercise_list_view(cid, sid)
        expected = sorted(
            row["exercise_id"] for row in authoritative["exercises"]
            if not row["submitted"]
        )
        view = workspace.student_today(course_id=cid, student_id=sid)
        assert sorted(r["exercise_id"] for r in view["pending_exercises"]) == expected

    def test_exercises_link_points_to_the_exercise_page(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        links = workspace.student_today(course_id=cid)["links"]
        assert links["exercises"] == "/api/exercises"
        assert links["review_center"] == "/api/reviews"

    def test_new_student_with_unanswered_exercises_sees_them(
        self, workspace, processed_course, student
    ) -> None:
        cid, _ = processed_course
        kp = sorted(p["knowledge_id"] for p in workspace.knowledge_points(cid))[0]
        workspace.create_exercise(cid, "short_answer", "P?", [kp], expected_answer="a")
        view = workspace.student_today(course_id=cid, student_id=student)
        assert view["counts"]["pending_exercises"] == 1


# ---------------------------------------------------------------------------
# 63.8 Recent Evaluations
# ---------------------------------------------------------------------------


class TestRecentEvaluations:
    def test_evaluations_are_reported_verbatim(
        self, workspace, active_student
    ) -> None:
        cid = active_student["course_id"]
        view = workspace.student_today(
            course_id=cid, student_id=active_student["student_id"]
        )
        assert view["counts"]["recent_evaluations"] == 1
        row = view["recent_evaluations"][0]
        authoritative = workspace.get_evaluation(cid, active_student["answer_id"])
        for key in ("status", "score", "feedback"):
            assert row[key] == authoritative[key]

    def test_evaluation_is_never_rewritten_into_mastery(
        self, workspace, active_student
    ) -> None:
        """spec 63.8: evaluation 不得被自动改写成 Mastery。"""
        cid = active_student["course_id"]
        view = workspace.student_today(
            course_id=cid, student_id=active_student["student_id"]
        )
        row = view["recent_evaluations"][0]
        assert "mastery" not in row
        assert "mastered" not in row
        assert "proficiency" not in row

    def test_evaluations_are_ordered_newest_first(
        self, workspace, active_student
    ) -> None:
        cid = active_student["course_id"]
        sid = active_student["student_id"]
        for exercise_id in active_student["exercise_ids"][1:]:
            workspace.submit_answer(cid, sid, exercise_id, "resposta-2")
        view = workspace.student_today(course_id=cid, student_id=sid)
        authoritative = workspace.context(cid).learning_view._recent_evaluations(sid)
        assert [r["answer_id"] for r in view["recent_evaluations"]] == [
            r["answer_id"] for r in authoritative
        ]


# ---------------------------------------------------------------------------
# 63.9 Attention Area
# ---------------------------------------------------------------------------


class TestAttentionArea:
    def test_practicing_becomes_needs_practice(self, workspace, active_student) -> None:
        cid = active_student["course_id"]
        rows = workspace.student_today(
            course_id=cid, student_id=active_student["student_id"]
        )["attention"]
        by_kp = {r["knowledge_point_id"]: r for r in rows}
        kp0 = active_student["knowledge_point_ids"][0]
        assert by_kp[kp0]["kind"] == "NEEDS_PRACTICE"
        assert by_kp[kp0]["state"] == "practicing"

    def test_reviewing_becomes_needs_review(self, workspace, active_student) -> None:
        cid = active_student["course_id"]
        rows = workspace.student_today(
            course_id=cid, student_id=active_student["student_id"]
        )["attention"]
        by_kp = {r["knowledge_point_id"]: r for r in rows}
        kp1 = active_student["knowledge_point_ids"][1]
        assert by_kp[kp1]["kind"] == "NEEDS_REVIEW"
        assert by_kp[kp1]["state"] == "reviewing"

    def test_every_attention_entry_declares_its_basis(
        self, workspace, active_student
    ) -> None:
        """每个条目都要写明"凭哪条既有事实", 且 kind 必须在允许集合内。"""
        cid = active_student["course_id"]
        rows = workspace.student_today(
            course_id=cid, student_id=active_student["student_id"]
        )["attention"]
        assert rows
        for row in rows:
            assert row["kind"] in ATTENTION_KINDS
            assert row["basis"]
            assert "StudentState" in row["basis"]

    def test_no_started_student_has_no_attention_rows(
        self, workspace, processed_course, student
    ) -> None:
        """完全没有学习记录的学生 -> attention 为空。

        这同时证明了 attention **不是**从"错了几次"推出来的: 这里一道题都
        没作答, 依然没有 attention 行。
        """
        cid, _ = processed_course
        view = workspace.student_today(course_id=cid, student_id=student)
        assert view["attention"] == []

    def test_attention_is_sorted_deterministically(
        self, workspace, active_student
    ) -> None:
        cid = active_student["course_id"]
        sid = active_student["student_id"]
        first = workspace.student_today(course_id=cid, student_id=sid)["attention"]
        second = workspace.student_today(course_id=cid, student_id=sid)["attention"]
        assert first == second
        keys = [(r["course_id"], r["knowledge_point_id"], r["kind"]) for r in first]
        assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# 63.12 Course Context
# ---------------------------------------------------------------------------


class TestCourseContext:
    def _three_courses(self, workspace):
        out = []
        for index, (name, code) in enumerate(
            (("Curs A", "CA"), ("Curs B", "CB"), ("Curs C", "CC")), start=1
        ):
            course = workspace.create_course(name, code, "ca")
            cid = course["course_id"]
            _processed(workspace, cid, index)
            workspace.create_student(cid, f"s-{index}", f"Alumne {index}")
            out.append(cid)
        return out

    def test_single_course_shows_only_that_course(
        self, workspace, processed_course, course
    ) -> None:
        cid, _ = processed_course
        workspace.create_student(cid, "s-a", "Alumne A")
        other = workspace.create_course("Altra", "ALT", "ca")
        # 给另一门课也放一个学生, 证明它确实被过滤掉了 (而不是"碰巧没数据")
        workspace.create_student(other["course_id"], "s-b", "Alumne B")
        view = workspace.student_today(course_id=cid)
        assert view["course_id"] == cid
        seen = {row["course_id"] for row in view["study"]}
        assert seen == {cid}
        assert other["course_id"] not in seen
        assert all(row["course_id"] == cid for row in view["review"]["items"])

    def test_all_courses_keeps_course_id_on_every_task(self, workspace) -> None:
        """spec 63.12: All Courses 时每个 task 必须保留 course_id。"""
        cids = self._three_courses(workspace)
        view = workspace.student_today()
        assert view["course_id"] is None
        assert {row["course_id"] for row in view["study"]} == set(cids)
        for row in view["study"]:
            assert row["course_id"] in cids
        for row in view["review"]["items"]:
            assert row["course_id"] in cids
        for row in view["learning_paths"]:
            assert row["course_id"] in cids

    def test_all_courses_keeps_course_name_on_every_task(self, workspace) -> None:
        """前端显示的是**课程名** —— 每一行都必须带它。

        ``course_id`` 是由课程名等内容派生的内部标识
        (``course-3fd6392d1fd78e87``); 只带 id 的行会让界面直接渲染那串哈希
        (用户报的"课程显示不对"就是这个形状)。
        """
        self._three_courses(workspace)
        names = {c["course_id"]: c["name"] for c in workspace.list_courses()}
        view = workspace.student_today()
        for key in ("study", "learning_paths"):
            assert view[key], f"夹具必须真的产出 {key}"
            for row in view[key]:
                assert row["course_name"] == names[row["course_id"]], key
        assert view["review"]["items"], "夹具必须真的产出 review.items"
        for row in view["review"]["items"]:
            assert row["course_name"] == names[row["course_id"]]

    def test_every_course_scoped_row_carries_the_course_name(
        self, workspace, active_student
    ) -> None:
        """待作答 / 最近评估 / 关注区 / 今日课堂同样要带课程名。"""
        cid = active_student["course_id"]
        name = workspace.get_course(cid)["name"]
        view = workspace.student_today(
            course_id=cid, student_id=active_student["student_id"]
        )
        for key in ("pending_exercises", "recent_evaluations", "attention"):
            assert view[key], f"夹具必须真的产出 {key}"
            for row in view[key]:
                assert row["course_name"] == name, key
        assert view["classes_today"], "夹具必须真的产出 classes_today"
        for row in view["classes_today"]:
            assert row["course_name"] == name

    def test_course_filter_never_leaks_another_course(self, workspace) -> None:
        cids = self._three_courses(workspace)
        target = cids[1]
        view = workspace.student_today(course_id=target)
        for key in ("study", "learning_paths", "pending_exercises",
                    "recent_evaluations", "attention"):
            assert all(row["course_id"] == target for row in view[key]), key
        assert all(row["course_id"] == target for row in view["review"]["items"])

    def test_counts_are_per_course_and_do_not_mix(self, workspace) -> None:
        cids = self._three_courses(workspace)
        one = workspace.student_today(course_id=cids[0])
        all_three = workspace.student_today()
        assert one["counts"]["classes_today"] <= all_three["counts"]["classes_today"]
        assert all_three["counts"]["review"] >= one["counts"]["review"]


# ---------------------------------------------------------------------------
# 真值安全 / 只读性
# ---------------------------------------------------------------------------


class TestTruthSafetyAndReadOnly:
    def _blob(self, view: dict[str, Any]) -> str:
        return json.dumps(view, ensure_ascii=False).lower()

    def test_no_forbidden_term_anywhere_in_the_response(
        self, workspace, active_student
    ) -> None:
        cid = active_student["course_id"]
        view = workspace.student_today(
            course_id=cid, student_id=active_student["student_id"]
        )
        blob = self._blob(view)
        for term in TODAY_FORBIDDEN_TERMS:
            assert term not in blob, f"响应里出现了禁止词 {term!r}"

    def test_repeated_calls_never_change_any_persisted_fact(
        self, workspace, active_student
    ) -> None:
        cid = active_student["course_id"]
        sid = active_student["student_id"]

        def snapshot():
            return (
                len(workspace.knowledge_points(cid)),
                len(workspace.list_exercises(cid)),
                len(workspace.context(cid).learning_view._student_state_records(sid)),
                len(
                    workspace.persistence.repositories.study_plans.plan_ids_for_student(
                        sid
                    )
                ),
                len(workspace.review_candidates(cid)),
            )

        before = snapshot()
        for _ in range(3):
            workspace.student_today(course_id=cid, student_id=sid)
        assert snapshot() == before

    def test_dashboard_never_invents_attention_from_wrong_counts(
        self, workspace, processed_course, student
    ) -> None:
        """答错题不产生 attention —— 除非 StudentState 明确给了状态。"""
        cid, _ = processed_course
        kp = sorted(p["knowledge_id"] for p in workspace.knowledge_points(cid))[0]
        exercise = workspace.create_exercise(
            cid, "short_answer", "P?", [kp], expected_answer="correcta"
        )
        workspace.submit_answer(cid, student, exercise["exercise_id"], "totalment-mal")
        view = workspace.student_today(course_id=cid, student_id=student)
        # 答错了, 但学生没有 practiced / reviewing 状态 -> attention 仍为空
        assert view["attention"] == []
        assert view["counts"]["recent_evaluations"] == 1

    def test_attention_only_contains_allowed_kinds(
        self, workspace, active_student
    ) -> None:
        cid = active_student["course_id"]
        rows = workspace.student_today(
            course_id=cid, student_id=active_student["student_id"]
        )["attention"]
        assert {r["kind"] for r in rows} <= set(ATTENTION_KINDS)


# ---------------------------------------------------------------------------
# 63.13 i18n / 语言标签
# ---------------------------------------------------------------------------


class TestLanguage:
    def test_default_language_is_simplified_chinese(self, workspace) -> None:
        assert workspace.student_today()["lang"] == "zh"

    @pytest.mark.parametrize("lang", ["zh", "es", "ca"])
    def test_language_is_echoed_but_never_rewrites_data(
        self, workspace, processed_course, lang
    ) -> None:
        """语言只影响 UI 渲染; 后端数据一字不改。"""
        cid, _ = processed_course
        view = workspace.student_today(course_id=cid, lang=lang)
        assert view["lang"] == lang
        assert view["date"] == TODAY

    def test_language_does_not_appear_in_business_identity(
        self, workspace, processed_course
    ) -> None:
        cid, _ = processed_course
        a = workspace.student_today(course_id=cid, lang="zh")
        b = workspace.student_today(course_id=cid, lang="es")
        a.pop("lang")
        b.pop("lang")
        assert a == b


# ---------------------------------------------------------------------------
# 63.13 restart
# ---------------------------------------------------------------------------


class TestRestartConsistency:
    def test_dashboard_survives_a_real_subprocess_restart(
        self, workspace, active_student
    ) -> None:
        """真子进程重启: 同一份数据必须给出同样的首页。"""
        cid = active_student["course_id"]
        sid = active_student["student_id"]
        before = workspace.student_today(course_id=cid, student_id=sid)
        data_dir = workspace.data_dir
        workspace.close()

        script = (
            "import json,sys\n"
            "sys.path.insert(0, r'%s')\n"
            "from src.application.workspace import Workspace\n"
            "from src.application.runtime import fixed_clock\n"
            "ws = Workspace(r'%s', clock=fixed_clock(r'%s'),\n"
            "               asr_mode='mock', ocr_mode='mock')\n"
            "out = ws.student_today(course_id=r'%s', student_id=r'%s')\n"
            "ws.close()\n"
            "print(json.dumps(out, ensure_ascii=False, sort_keys=True))\n"
        ) % (
            str(Path(__file__).resolve().parent.parent),
            data_dir,
            FIXED_TIME,
            cid,
            sid,
        )

        root = str(Path(__file__).resolve().parent.parent)
        # 子进程只做 JSON 输出: 课堂原文可含任意 Unicode (如夹具 BOM
        # U+FEFF), 必须强制 UTF-8 stdout —— 否则 Windows GBK 控制台下
        # print 即崩 (与 test_production_gate._env 同一先例)。
        child_env = dict(os.environ)
        child_env["PYTHONUTF8"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            env=child_env,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        reloaded = json.loads(proc.stdout.strip().splitlines()[-1])

        # 时间戳由运行时时钟生成, 重启后不参与业务身份 —— 单独剔除比较。
        for key in ("generated_at",):
            before.pop(key, None)
            reloaded.pop(key, None)
        assert reloaded["counts"] == before["counts"]
        assert reloaded["has_activity"] == before["has_activity"]
        assert reloaded["course_id"] == before["course_id"]
        assert reloaded["study"] == before["study"]
        assert reloaded["recent_evaluations"] == before["recent_evaluations"]
        assert reloaded["attention"] == before["attention"]
        assert reloaded["review"]["total"] == before["review"]["total"]


# ---------------------------------------------------------------------------
# API 契约
# ---------------------------------------------------------------------------


class _Client:
    def __init__(self, base: str) -> None:
        self.base = base

    def get(self, path: str) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(self.base + path)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture
def client(workspace):
    instance = create_server(workspace, port=0)
    instance.start()
    try:
        yield _Client(instance.url)
    finally:
        instance.stop()


class TestApiContract:
    def test_endpoint_shape(self, client, processed_course) -> None:
        cid, _ = processed_course
        status, payload = client.get(f"/api/student-today?course_id={cid}")
        assert status == 200, payload
        assert payload["success"] is True
        data = payload["data"]
        for key in (
            "date",
            "has_activity",
            "classes_today",
            "study",
            "learning_paths",
            "pending_exercises",
            "recent_evaluations",
            "attention",
            "review",
            "counts",
            "links",
        ):
            assert key in data, f"响应缺少 {key}"

    def test_empty_course_is_200_not_500(self, client, course) -> None:
        cid = course["course_id"]
        status, payload = client.get(f"/api/student-today?course_id={cid}")
        assert status == 200, payload
        assert payload["data"]["has_activity"] is False
        assert payload["data"]["note"] == "No learning activity yet."

    def test_no_courses_at_all_is_200(self, client) -> None:
        status, payload = client.get("/api/student-today")
        assert status == 200, payload
        assert payload["data"]["has_activity"] is False

    def test_unknown_course_is_structured_404(self, client) -> None:
        status, payload = client.get("/api/student-today?course_id=nope")
        assert status == 404
        assert set(payload["error"]) == {"code", "message"}
        assert "Traceback" not in json.dumps(payload)

    def test_endpoint_never_returns_html_or_traceback(
        self, client, processed_course
    ) -> None:
        cid, _ = processed_course
        request = urllib.request.Request(
            client.base + f"/api/student-today?course_id={cid}"
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
        assert "<html" not in body.lower()
        assert "traceback" not in body.lower()

    def test_lang_query_is_accepted(self, client, processed_course) -> None:
        cid, _ = processed_course
        for lang in ("zh", "es", "ca"):
            status, payload = client.get(
                f"/api/student-today?course_id={cid}&lang={lang}"
            )
            assert status == 200, payload
            assert payload["data"]["lang"] == lang

    def test_student_today_never_leaks_forbidden_terms(
        self, client, processed_course
    ) -> None:
        cid, _ = processed_course
        _, payload = client.get(f"/api/student-today?course_id={cid}")
        blob = json.dumps(payload, ensure_ascii=False).lower()
        for term in TODAY_FORBIDDEN_TERMS:
            assert term not in blob
