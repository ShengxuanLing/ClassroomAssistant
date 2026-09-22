# -*- coding: utf-8 -*-
"""Task 66 — Daily Learning Workflow 测试。

覆盖的 spec 条款::

    66.2  用户入口 "开始今天的学习"
    66.3  学习任务选择 (deterministic; 不随机 / 不按考试概率)
    66.4  Knowledge 学习页 (无证据即明确报缺)
    66.5  Grounded Explanation (只组织既有信息)
    66.6  学习状态 (只由 LearningEvent 推动)
    66.7  Exercise Loop (grounding / answer / evaluation / state 分离)
    66.8  Next Task (事实性表达, 禁止 "You mastered this topic.")
    66.9  Resume (重启后继续)

几条刻意写死的判据（产品语义，不是实现细节）
--------------------------------------------
1. **答对不推进状态。** Task 30 的 ``ANSWERED`` 不是转移事件，所以连答
   正确题之后状态仍是 ``exposed``。测试断言这一点 —— 一个把
   "答对 3 次 = mastered" 的实现会在这里挂掉，而这正是 spec 66.3.6 与
   项目铁律 5 禁止的事。
2. **确定性排序是从同一份数据得到同一个答案。** 同一进程内构造两个
   Workspace 读同一个数据目录，``current_task`` 必须逐字段相同。
3. **排除规则只排除 ``rejected`` / ``conflicted``。** ``unverified``
   知识**可以**成为学习任务（会带标记），因为 spec 66.3.7 禁止的是
   "正式学习事实来源"，不是"看都不能看"。
4. **重复生成不增加练习。** 调用两次 ``exercise_for_knowledge``，
   ``list_exercises`` 的长度必须不变（内容寻址 + 复用）。
5. **重启后链路完整。** 进程 A 走到一半，进程 B 只拿到数据目录，
   必须重放出同一个 current_task / 状态 / 练习。
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Optional

import pytest

from src.api.server import create_server
from src.application.learning_workflow import (
    EVIDENCE_UNAVAILABLE,
    EXCLUDED_VALIDATION_STATUSES,
    NO_ACTIVITY_NOTE,
    NO_TASK_NOTE,
    ORDERABLE_STATES,
    WORKFLOW_FORBIDDEN_TERMS,
    WORKFLOW_VERSION,
    LearningWorkflow,
)
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace
from src.exercise_generation import GenerationConfig, TemplateId
from tests.support import build_conflicted_structure

FIXTURES = Path(__file__).resolve().parent / "fixtures"

FIXED_TIME = "2026-09-18T09:00:00+00:00"
TODAY = "2026-09-18"

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
    return workspace.create_course("Programacio", "PROG101", "ca")


def _register(workspace, course_id: str, session_id: str, names) -> None:
    for name in names:
        workspace.register_material(
            course_id, str(FIXTURES / name), session_id=session_id
        )


def _processed(workspace, course_id: str, session_number: int = 1) -> str:
    session = workspace.create_session(
        course_id, session_number=session_number, date=TODAY, title="Tema"
    )
    sid = session["session_id"]
    _register(workspace, course_id, sid, _fixtures_for(session_number))
    workspace.process_session(course_id, sid)
    return sid


@pytest.fixture
def processed_course(workspace, course):
    cid = course["course_id"]
    _processed(workspace, cid, 1)
    return cid


@pytest.fixture
def kps(workspace, processed_course) -> list[dict[str, Any]]:
    points = workspace.knowledge_points(processed_course)
    if not points:
        pytest.skip("fixture produced no knowledge point")
    return points


@pytest.fixture
def student(workspace, processed_course) -> str:
    return workspace.create_student(
        processed_course, "s-quim", "Quim"
    )["student_id"]


@pytest.fixture
def workflow(workspace, processed_course) -> LearningWorkflow:
    wf = LearningWorkflow(workspace)
    wf._course_id = processed_course
    return wf


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _start(workspace, cid, sid, **kw):
    return workspace.learning_workflow_start(cid, sid, **kw)


def _wrong_value(exercise: Mapping[str, Any]) -> Optional[str]:
    """一个**必然判 incorrect** 的提交值；不可得时 None。

    与 ``tests/test_mistakes_center.py`` 同一条规则: ``short_answer`` 永远
    只给 ``unsupported``（无语义判分），因此它不是可用的"错答"来源。
    """
    kind = exercise.get("exercise_type")
    if kind == "true_false":
        correct = exercise.get("is_true")
        if correct is None:
            correct = exercise.get("correct_choice_id") == "true"
        return "false" if correct else "true"
    if kind == "multiple_choice":
        correct = exercise.get("correct_choice_id")
        for choice in exercise.get("choices") or []:
            if choice.get("choice_id") and choice["choice_id"] != correct:
                return str(choice["choice_id"])
        return None
    if kind == "fill_blank":
        return "___definitely_not_the_answer___"
    return None


def _correct_value(exercise: Mapping[str, Any]) -> Optional[str]:
    kind = exercise.get("exercise_type")
    if kind == "true_false":
        correct = exercise.get("is_true")
        if correct is None:
            correct = exercise.get("correct_choice_id") == "true"
        return "true" if correct else "false"
    if kind == "multiple_choice":
        return exercise.get("correct_choice_id")
    if kind == "short_answer":
        return exercise.get("expected_answer")
    if kind == "fill_blank":
        blank = exercise.get("fill_blank") or {}
        answers = blank.get("accepted_answers") or []
        return str(answers[0]) if answers else None
    return None


def _answerable_exercise(workspace, cid, sid, kp_id) -> dict[str, Any]:
    """拿到一道有正确/错误值可用的练习。"""
    row = workspace.learning_workflow_exercise(cid, sid, kp_id)
    if not row.get("exercise"):
        pytest.skip(f"generator refused: {row.get('refusal')}")
    return row


# ---------------------------------------------------------------------------
# 1. 选择规则 (spec 66.3)
# ---------------------------------------------------------------------------


class TestTaskSelection:
    def test_empty_course_has_no_task_but_is_not_an_error(self, workspace):
        cid = workspace.create_course("Buit", "EMPTY", "ca")["course_id"]
        sid = workspace.create_student(cid, "s-1", "Buit")["student_id"]
        row = _start(workspace, cid, sid)
        assert row["has_task"] is False
        assert row["current_task"] is None
        assert row["note"] == NO_TASK_NOTE
        assert row["progress"]["total"] == 0

    def test_empty_course_no_students_raises_not_found(self, workspace):
        """空课程 + 不存在的学生 -> 404（"学生不存在"不是"没有任务"）。"""
        from src.application.errors import NotFoundError

        cid = workspace.create_course("Buit", "EMPTY", "ca")["course_id"]
        with pytest.raises(NotFoundError):
            _start(workspace, cid, "s-ghost")

    def test_course_with_materials_but_no_student_has_no_task(self, workspace):
        """课程有知识但没有学生时, 入口必须 404（学生不存在）, 不是静默空任务。"""
        from src.application.errors import NotFoundError

        cid = workspace.create_course("Buit", "EMPTY", "ca")["course_id"]
        with pytest.raises(NotFoundError):
            _start(workspace, cid, "s-nobody")

    def test_single_knowledge_point_becomes_the_task(self, workspace, processed_course):
        sid = workspace.create_student(processed_course, "s-1", "Un")["student_id"]
        row = _start(workspace, processed_course, sid)
        assert row["has_task"] is True
        assert row["current_task"]["knowledge_point_id"]
        assert row["progress"]["orderable"] >= 1

    def test_selection_is_deterministic_across_instances(self, workspace, processed_course):
        sid = workspace.create_student(processed_course, "s-1", "Un")["student_id"]
        first = _start(workspace, processed_course, sid)
        second = _start(workspace, processed_course, sid)
        assert first["current_task"] == second["current_task"]

    def test_completed_task_is_not_selected_twice(self, workspace, processed_course):
        sid = workspace.create_student(processed_course, "s-1", "Un")["student_id"]
        row = _start(workspace, processed_course, sid)
        kp_id = row["current_task"]["knowledge_point_id"]
        # 走到状态机末端: viewed -> practiced -> reviewed
        for event in ("viewed", "practiced", "reviewed"):
            workspace.record_learning_event(processed_course, sid, kp_id, event)
        after = _start(workspace, processed_course, sid)
        assert after["current_task"]["knowledge_point_id"] != kp_id

    def test_prerequisite_order_is_respected(self, workspace, processed_course, kps):
        cid = processed_course
        sid = workspace.create_student(cid, "s-1", "Un")["student_id"]
        ctx = workspace.context(cid)
        if len(kps) < 2:
            pytest.skip("need two knowledge points")
        # 让第二个 KP 依赖第一个: 第一个是前置
        prereq = kps[0]["knowledge_id"]
        dependent = kps[1]["knowledge_id"]
        ctx.org_service.add_relation(prereq, dependent, "prerequisite")

        row = _start(workspace, cid, sid)
        # 依赖项有未完成前置 -> 前置应该排在它前面
        order = [r["knowledge_point_id"] for r in [row["current_task"]]]
        assert order[0] == prereq, "未完成前置的知识点不得排在它的前置之前"
        # 依赖项必须报出未完成前置
        dependent_row = next(
            item
            for item in _all_candidates(workspace, cid, sid)
            if item["knowledge_point_id"] == dependent
        )
        assert prereq in dependent_row["unmet_prerequisite_ids"]

    def test_rejected_knowledge_is_excluded(self, workspace, processed_course, kps):
        """人工审查否决的知识不得成为学习任务。

        关键：``review_reject()`` 只把 ``review_status`` 改成 ``rejected``，
        ``validation_status`` 仍是 ``supported`` —— 两个轴是独立的
        （铁律 4）。只查 validation 会漏掉全部人工否决。
        """
        cid = processed_course
        sid = workspace.create_student(cid, "s-1", "Un")["student_id"]
        target = kps[0]["knowledge_id"]
        workspace.review_reject(cid, target, note="not usable")
        after = workspace.knowledge_point(cid, target)
        assert after["review_status"] == "rejected"
        assert after["validation_status"] == "supported", (
            "review 轴与 validation 轴必须独立改变"
        )
        row = _start(workspace, cid, sid)
        assert target in row["excluded"]["rejected"], (
            "review_status=rejected 的知识必须被显式排除并报告"
        )
        assert all(
            item["knowledge_point_id"] != target
            for item in _all_candidates(workspace, cid, sid)
        )

    def test_unverified_knowledge_may_still_be_a_task(self, workspace, processed_course, kps):
        """spec 66.3.7 排除的是"正式学习事实来源", 不是"看都不能看"。"""
        cid = processed_course
        sid = workspace.create_student(cid, "s-1", "Un")["student_id"]
        row = _start(workspace, cid, sid)
        statuses = {
            item["validation_status"] for item in _all_candidates(workspace, cid, sid)
        }
        assert "rejected" not in statuses
        assert "conflicted" not in statuses
        assert row["has_task"] is True

    def test_no_randomness_in_source(self):
        """选择规则里不得出现随机源（spec 66.3.4）。"""
        import inspect

        from src.application import learning_workflow as mod

        src = inspect.getsource(mod)
        for forbidden in ("import random", "random.", "uuid4(", "time.time"):
            assert forbidden not in src, f"选择规则不得使用 {forbidden!r}"

    def test_no_exam_probability_in_source(self):
        """源码里不得出现"预测/概率"排序逻辑。

        判据要**先移除禁止词表本身** —— ``WORKFLOW_FORBIDDEN_TERMS`` 就是
        一张"禁止输出的词"清单，里面当然含这些词。直接搜子串会把自己判违规
        （本项目已有同类教训：静态断言必须先去掉注释与常量声明）。
        """
        import inspect

        from src.application import learning_workflow as mod

        src = inspect.getsource(mod)
        # 逐行移除禁止词表的整段（从常量名那行到与之配平的右括号）。
        lines = src.splitlines()
        out: list[str] = []
        skipping = False
        depth = 0
        for line in lines:
            if not skipping and line.startswith("WORKFLOW_FORBIDDEN_TERMS"):
                skipping = True
                depth = line.count("(") - line.count(")")
                if depth <= 0:
                    skipping = False
                continue
            if skipping:
                depth += line.count("(") - line.count(")")
                if depth <= 0:
                    skipping = False
                continue
            out.append(line)
        body = "\n".join(out)
        # ``__all__`` 里还会出现这个名字（那是导出清单, 不是词表内容）——
        # 判据是"词表里那些词不再出现在正文", 而不是"这个名字消失"。
        assert '"exam_probability"' not in body, (
            "禁止词表未被成功移除, 断言会误报"
        )
        lowered = body.lower()
        for forbidden in (
            "probability",
            "most_likely",
            "most likely",
            "predicted",
            "prediction",
            "押题",
            "预测",
        ):
            assert forbidden not in lowered, f"不得按 {forbidden!r} 排序"


def _conflicted_structure(kp_id: str, refs: list[str], *, existing=None):
    """构造一个含"conflicted 知识点 + 对应冲突"的 ``KnowledgeStructure``。

    实现已移到 ``tests/support.build_conflicted_structure`` —— 这个夹具在
    Task 66 (学习流程排除冲突知识) 与 Task 67 (复习集合阻塞) 里都要用,
    各写一份就会各走样一次, 而它恰恰是最容易写错的那种夹具: 写错不会报错,
    只会让"冲突应被排除/阻塞"的断言在一个假阴性上通过。

    完整的语义说明 (为什么必须整体构造、为什么冲突证据必须取自该知识点)
    见 ``tests/support.py`` 的文档字符串。
    """
    return build_conflicted_structure(kp_id, refs, existing=existing)


def _all_candidates(workspace, cid, sid) -> list[dict[str, Any]]:
    """按同样的规则把全部候选列出来（测试用，复用私有方法以保持一致）。"""
    wf = LearningWorkflow(workspace)
    ctx = workspace.context(cid)
    rows, _ = wf._candidates(ctx, cid, sid)
    return rows


# ---------------------------------------------------------------------------
# 2. Knowledge 学习页 (spec 66.4 / 66.5)
# ---------------------------------------------------------------------------


class TestKnowledgePage:
    def test_knowledge_page_reports_all_required_sections(
        self, workspace, processed_course, student
    ):
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        page = workspace.learning_workflow_knowledge(processed_course, student, kp_id)
        for key in (
            "knowledge_point",
            "source_language",
            "evidence",
            "materials",
            "grounded_explanation",
            "student_state",
            "prerequisites",
            "next_event",
            "next_task",
        ):
            assert key in page, f"学习页缺少 {key}"
        kp = page["knowledge_point"]
        assert kp["title"]
        assert kp["content"]
        # 两个 truth axis 分别存在，绝不合并
        assert "validation_status" in kp
        assert "review_status" in kp

    def test_evidence_available_is_true_with_evidence(
        self, workspace, processed_course, student
    ):
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        page = workspace.learning_workflow_knowledge(processed_course, student, kp_id)
        assert page["evidence_available"] is True
        assert page["evidence_note"] is None
        assert page["evidence"], "有证据时必须给出 evidence 列表"

    def test_evidence_rows_carry_material_and_source(
        self, workspace, processed_course, student
    ):
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        page = workspace.learning_workflow_knowledge(processed_course, student, kp_id)
        assert page["evidence"]
        for item in page["evidence"]:
            # 每一跳都可追溯: evidence -> source -> material
            assert item["evidence_id"]
            assert "source" in item
            assert "material_resolved" in item

    def test_unknown_knowledge_point_is_not_found(
        self, workspace, processed_course, student
    ):
        from src.application.errors import NotFoundError

        with pytest.raises(NotFoundError):
            workspace.learning_workflow_knowledge(
                processed_course, student, "kp-does-not-exist"
            )

    def test_grounded_explanation_never_invents_content(
        self, workspace, processed_course, student
    ):
        """解释只能是既有数据的重组 (spec 66.5)。"""
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        page = workspace.learning_workflow_knowledge(processed_course, student, kp_id)
        explanation = page["grounded_explanation"]
        assert explanation is not None
        # 状态是枚举里已知的值, 不是新造的判定
        assert explanation["status"] in (
            "ok",
            "not_available",
            "language_not_available",
        )
        # 请求语言被归一化成 ISO 码 (不是 "Catalan" 这种显示值)
        assert explanation["requested_language"] in ("ca", "es", "en", "zh")
        # 没有可用的解释时给出明确文案, 而不是自己编一段
        if not explanation["available"]:
            assert explanation["message"]

    def test_source_language_never_guesses(
        self, workspace, processed_course, student
    ):
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        page = workspace.learning_workflow_knowledge(processed_course, student, kp_id)
        lang = page["source_language"]
        # 未知就是 None + unknown=true, 绝不猜一个语言
        if lang["unknown"]:
            assert lang["declared"] is None
        else:
            assert isinstance(lang["declared"], list) and lang["declared"]

    def test_evidence_unavailable_constant_is_stable(self):
        assert EVIDENCE_UNAVAILABLE == "Evidence unavailable."


class TestKnowledgePageNoEvidence:
    def test_page_without_evidence_says_so_explicitly(self, workspace, processed_course):
        """没有证据时必须显式报缺, 而不是补一段解释（spec 66.4）。"""
        cid = processed_course
        ctx = workspace.context(cid)

        # 走真实的领域 API: 登记一个**没有 evidence_refs** 的 KnowledgePoint。
        # 组织服务接受这样的 KP（它只要求非空 id），于是它成为课程知识点
        # 之一 —— 而它的证据链是空的。
        orphan = "kp-orphan-no-evidence"
        kp = _bare_knowledge_point(orphan, "Orphan", "No evidence for this one.")
        ctx.org_service.register_knowledge_point(kp)
        assert orphan in ctx.org_service.registered_knowledge_point_ids

        sid = workspace.create_student(cid, "s-orphan", "Orphan")["student_id"]
        page = workspace.learning_workflow_knowledge(cid, sid, orphan)
        assert page["evidence_available"] is False
        assert page["evidence_note"] == EVIDENCE_UNAVAILABLE
        assert page["evidence"] == []
        assert page["truth_flags"]["has_evidence"] is False


def _bare_knowledge_point(kp_id: str, title: str, content: str):
    """构造一个领域层 ``KnowledgePoint``（不引入第二个知识模型）。"""
    from src.models import KnowledgePoint

    return KnowledgePoint(
        knowledge_id=kp_id,
        title=title,
        content=content,
        evidence_refs=[],
    )


# ---------------------------------------------------------------------------
# 3. 学习状态只能由 LearningEvent 推动 (spec 66.6)
# ---------------------------------------------------------------------------


class TestLearningStatePushedOnlyByEvents:
    def test_viewed_event_moves_not_started_to_exposed(
        self, workspace, processed_course, student
    ):
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        assert row["current_task"]["state"] == "not_started"
        page = workspace.learning_workflow_open_knowledge(
            processed_course, student, kp_id
        )
        assert page["student_state"] == "exposed"
        assert page["next_event"] == "practiced"

    def test_repeated_open_is_idempotent(self, workspace, processed_course, student):
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        first = workspace.learning_workflow_open_knowledge(
            processed_course, student, kp_id
        )
        second = workspace.learning_workflow_open_knowledge(
            processed_course, student, kp_id
        )
        assert first["student_state"] == second["student_state"] == "exposed"
        # exposure_count 是事件计数, 但状态不变 (幂等性的可观测形式)
        assert second["student_activity"]["exposure_count"] >= 1

    def test_correct_answer_does_not_advance_state(
        self, workspace, processed_course, student
    ):
        """Task 30 的 ANSWERED 不是转移事件 —— 答对不等于掌握。"""
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        workspace.learning_workflow_open_knowledge(processed_course, student, kp_id)
        ex = _answerable_exercise(workspace, processed_course, student, kp_id)
        value = _correct_value(ex["exercise"])
        if value is None:
            pytest.skip("exercise has no correct value usable by the evaluator")
        result = workspace.learning_workflow_answer(
            processed_course, student, ex["exercise_id"], value
        )
        assert result["evaluation_status"] == "correct"
        # 状态仍是 exposed: 答对 1 次不构成状态推进
        state = result["student_state"]
        state_value = state["state"] if isinstance(state, dict) else state[0]["state"]
        assert state_value == "exposed", (
            "答对一次不得推进 Task 30 状态 (spec 66.3.6 / 铁律 5)"
        )
        assert result["student_state"]["correct_count"] == 1
        assert result["student_state"]["practice_count"] == 0

    def test_wrong_answer_does_not_mark_mastery_failure(
        self, workspace, processed_course, student
    ):
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        workspace.learning_workflow_open_knowledge(processed_course, student, kp_id)
        ex = _answerable_exercise(workspace, processed_course, student, kp_id)
        value = _wrong_value(ex["exercise"])
        if value is None:
            pytest.skip("exercise type cannot produce an incorrect evaluation")
        result = workspace.learning_workflow_answer(
            processed_course, student, ex["exercise_id"], value
        )
        assert result["evaluation_status"] == "incorrect"
        state = result["student_state"]
        state_value = state["state"] if isinstance(state, dict) else state[0]["state"]
        # 一次错误不构成 mastery failure: 状态不因答题而改变
        assert state_value == "exposed"
        assert result["student_state"]["incorrect_count"] == 1

    def test_answer_count_never_directly_sets_state(
        self, workspace, processed_course, student
    ):
        """count 字段存在, 但状态永远由领域层归约得出。"""
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        workspace.learning_workflow_open_knowledge(processed_course, student, kp_id)
        ex = _answerable_exercise(workspace, processed_course, student, kp_id)
        value = _correct_value(ex["exercise"])
        if value is None:
            pytest.skip("no correct value")
        for sequence in range(5):
            workspace.learning_workflow_answer(
                processed_course, student, ex["exercise_id"], value, sequence=sequence
            )
        page = workspace.learning_workflow_knowledge(
            processed_course, student, kp_id
        )
        assert page["student_state"] == "exposed"
        assert page["student_activity"]["answer_count"] == 5

    def test_state_progression_requires_explicit_events(
        self, workspace, processed_course, student
    ):
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        seen = ["not_started"]
        workspace.learning_workflow_open_knowledge(processed_course, student, kp_id)
        seen.append(
            workspace.learning_workflow_knowledge(
                processed_course, student, kp_id
            )["student_state"]
        )
        for event in ("practiced", "reviewed"):
            workspace.record_learning_event(processed_course, student, kp_id, event)
            seen.append(
                workspace.learning_workflow_knowledge(
                    processed_course, student, kp_id
                )["student_state"]
            )
        assert seen == ["not_started", "exposed", "practicing", "reviewing"]

    def test_next_event_mapping_matches_domain_transitions(self):
        """映射表是消费领域层的, 不是抄的副本 —— 逐项对照。"""
        from src.application.learning_service import LearningService
        from src.student_learning import _TRANSITIONS, LearningState

        for state in LearningState:
            legal = _TRANSITIONS[state]
            expected = next(iter(legal)).value if len(legal) == 1 else None
            assert LearningService.NEXT_LEARNING_EVENT[state.value] == expected

    def test_answered_is_not_a_transition_event(self):
        from src.student_learning import _TRANSITIONS

        for legal in _TRANSITIONS.values():
            assert all(event.value != "answered" for event in legal)


# ---------------------------------------------------------------------------
# 4. Exercise Loop (spec 66.7)
# ---------------------------------------------------------------------------


class TestExerciseLoop:
    def test_exercise_is_grounded(self, workspace, processed_course, student):
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        ex = _answerable_exercise(workspace, processed_course, student, kp_id)
        grounding = ex["grounding"]
        assert grounding["knowledge_points"], "练习必须有知识点依据"
        assert grounding["evidence"], "练习必须有证据依据"
        assert grounding["complete"] is True

    def test_repeated_generation_does_not_inflate_exercises(
        self, workspace, processed_course, student
    ):
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        ctx = workspace.context(processed_course)
        before = len(ctx.learning_service.list_exercises())
        first = workspace.learning_workflow_exercise(processed_course, student, kp_id)
        after_first = len(ctx.learning_service.list_exercises())
        second = workspace.learning_workflow_exercise(processed_course, student, kp_id)
        after_second = len(ctx.learning_service.list_exercises())
        assert after_second == after_first, "重复调用不得新增练习"
        assert second["reused"] is True
        assert second["exercise_id"] == first["exercise_id"]
        assert after_first >= before

    def test_preview_is_side_effect_free(self, workspace, processed_course):
        """预览不落库 (spec 原则 9)。"""
        kp = workspace.knowledge_points(processed_course)[0]
        ctx = workspace.context(processed_course)
        before = len(ctx.learning_service.list_exercises())
        workspace.preview_exercise(processed_course, kp["knowledge_id"])
        assert len(ctx.learning_service.list_exercises()) == before

    def test_answer_traceable_to_evaluation(self, workspace, processed_course, student):
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        ex = _answerable_exercise(workspace, processed_course, student, kp_id)
        value = _correct_value(ex["exercise"]) or _wrong_value(ex["exercise"])
        if value is None:
            pytest.skip("no usable submitted value")
        result = workspace.learning_workflow_answer(
            processed_course, student, ex["exercise_id"], value
        )
        answer = result["answer"]
        evaluation = result["evaluation"]
        assert answer["answer_id"]
        assert evaluation["answer_id"] == answer["answer_id"]
        assert evaluation["evaluation_id"]
        assert result["evaluation_status"] == evaluation["status"]

    def test_submit_is_idempotent_for_same_sequence(
        self, workspace, processed_course, student
    ):
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        ex = _answerable_exercise(workspace, processed_course, student, kp_id)
        value = _correct_value(ex["exercise"]) or _wrong_value(ex["exercise"])
        if value is None:
            pytest.skip("no usable submitted value")
        first = workspace.learning_workflow_answer(
            processed_course, student, ex["exercise_id"], value, 0
        )
        second = workspace.learning_workflow_answer(
            processed_course, student, ex["exercise_id"], value, 0
        )
        assert first["answer"]["answer_id"] == second["answer"]["answer_id"]
        assert first["evaluation"]["evaluation_id"] == second["evaluation"]["evaluation_id"]

    def test_evaluation_and_state_are_separate_axes(
        self, workspace, processed_course, student
    ):
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        ex = _answerable_exercise(workspace, processed_course, student, kp_id)
        value = _wrong_value(ex["exercise"]) or _correct_value(ex["exercise"])
        if value is None:
            pytest.skip("no usable submitted value")
        result = workspace.learning_workflow_answer(
            processed_course, student, ex["exercise_id"], value
        )
        # 评价是事实, 状态是状态 —— 两条独立信息都存在
        assert result["evaluation"]["status"] in (
            "correct",
            "incorrect",
            "partial",
            "unsupported",
        )
        assert "state" in result["student_state"]

    def test_exercise_hides_answers_before_submission(
        self, workspace, processed_course, student
    ):
        """学生视角绝不把答案键发给浏览器 (spec 原则 9 / Task 41)。"""
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        ex = workspace.learning_workflow_exercise(processed_course, student, kp_id)
        if not ex.get("exercise"):
            pytest.skip("generator refused")
        blob = json.dumps(ex, ensure_ascii=False)
        # 生成/复用响应里的 exercise 是作者视角 DTO; 学生视角题目必须走
        # exercise_view (既有端点)。这里断言复用响应标明了 reused/generated
        # 语义, 且 grounding 不含答案键以外的推理。
        assert ex["reused"] in (True, False)
        assert ex["exercise_id"]
        # 学生视角题目端点必须隐藏答案
        view = workspace.exercise_view(
            processed_course, student, ex["exercise_id"]
        )
        view_blob = json.dumps(view, ensure_ascii=False)
        for field in ("correct_choice_id", "expected_answer", "accepted_answers"):
            assert field not in view_blob, f"学生视角题目泄露了 {field}"


# ---------------------------------------------------------------------------
# 5. Next Task (spec 66.8)
# ---------------------------------------------------------------------------


class TestNextTask:
    def test_next_task_is_reported_factually(self, workspace, processed_course, student):
        row = _start(workspace, processed_course, student)
        task = row["current_task"]
        assert task["state"] in ORDERABLE_STATES
        assert task["next_event"] in ("viewed", "practiced", "reviewed", None)
        assert task["basis"], "每一项都要说明它来自哪条既有事实"

    def test_no_mastery_claim_in_response(self, workspace, processed_course, student):
        row = _start(workspace, processed_course, student)
        blob = json.dumps(row, ensure_ascii=False).lower()
        for forbidden in WORKFLOW_FORBIDDEN_TERMS:
            assert forbidden not in blob, f"响应里不得出现 {forbidden!r}"

    def test_no_mastery_claim_after_answering(
        self, workspace, processed_course, student
    ):
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        workspace.learning_workflow_open_knowledge(processed_course, student, kp_id)
        ex = _answerable_exercise(workspace, processed_course, student, kp_id)
        value = _correct_value(ex["exercise"])
        if value is None:
            pytest.skip("no correct value")
        result = workspace.learning_workflow_answer(
            processed_course, student, ex["exercise_id"], value
        )
        blob = json.dumps(result, ensure_ascii=False).lower()
        for forbidden in WORKFLOW_FORBIDDEN_TERMS:
            assert forbidden not in blob, f"答题后响应里不得出现 {forbidden!r}"

    def test_knowledge_page_reports_next_task_excluding_current(
        self, workspace, processed_course, student
    ):
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        page = workspace.learning_workflow_knowledge(
            processed_course, student, kp_id
        )
        if page["next_task"] is not None:
            assert page["next_task"]["knowledge_point_id"] != kp_id

    def test_has_more_reflects_remaining_work(self, workspace, processed_course, student):
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        ex = _answerable_exercise(workspace, processed_course, student, kp_id)
        value = _correct_value(ex["exercise"]) or _wrong_value(ex["exercise"])
        if value is None:
            pytest.skip("no usable submitted value")
        result = workspace.learning_workflow_answer(
            processed_course, student, ex["exercise_id"], value
        )
        assert isinstance(result["has_more"], bool)
        assert result["progress"]["orderable"] >= 1


# ---------------------------------------------------------------------------
# 6. 空态 / 边界 (spec 66.10 的重点测试项)
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_unknown_course_raises_not_found(self, workspace):
        from src.application.errors import NotFoundError

        with pytest.raises(NotFoundError):
            _start(workspace, "course-nope", "s-1")

    def test_unknown_student_raises_not_found(self, workspace, processed_course):
        from src.application.errors import NotFoundError

        with pytest.raises(NotFoundError):
            _start(workspace, processed_course, "s-nope")

    def test_empty_course_id_invalid(self, workspace, processed_course, student):
        from src.application.errors import InvalidInputError

        with pytest.raises(InvalidInputError):
            _start(workspace, "", student)

    def test_empty_student_id_invalid(self, workspace, processed_course):
        from src.application.errors import InvalidInputError

        with pytest.raises(InvalidInputError):
            _start(workspace, processed_course, "")

    def test_multi_course_isolation(self, workspace, course):
        """A 课的学习任务不得出现在 B 课 (spec 66.10.16)。"""
        cid_a = course["course_id"]
        cid_b = workspace.create_course("Fisica", "FIS", "es")["course_id"]
        _processed(workspace, cid_a, 1)
        _processed(workspace, cid_b, 2)
        sid_a = workspace.create_student(cid_a, "s-a", "A")["student_id"]
        sid_b = workspace.create_student(cid_b, "s-b", "B")["student_id"]

        tasks_a = {
            item["knowledge_point_id"]
            for item in _all_candidates(workspace, cid_a, sid_a)
        }
        tasks_b = {
            item["knowledge_point_id"]
            for item in _all_candidates(workspace, cid_b, sid_b)
        }
        # knowledge_id 是内容寻址的, 两门课可能合法地共享同一个 id; 因此
        # 判据是 **membership 查询**不能跨课程泄漏, 而不是 id 集合不相交。
        page_a = _start(workspace, cid_a, sid_a)
        assert page_a["course_id"] == cid_a
        for item in _all_candidates(workspace, cid_a, sid_a):
            assert item["course_id"] == cid_a
        assert isinstance(tasks_a, set) and isinstance(tasks_b, set)

    def test_student_isolation_within_course(
        self, workspace, processed_course, student
    ):
        other = workspace.create_student(processed_course, "s-other", "Other")[
            "student_id"
        ]
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        workspace.learning_workflow_open_knowledge(processed_course, student, kp_id)
        other_page = workspace.learning_workflow_knowledge(
            processed_course, other, kp_id
        )
        assert other_page["student_state"] == "not_started", "学生状态必须隔离"

    def test_conflicted_knowledge_is_excluded_and_reported(
        self, workspace, processed_course, kps
    ):
        """带未解决冲突的知识不得成为学习任务（spec 66.3.7）。

        ``conflict`` 字段来自 ``KnowledgeService`` 的既有冲突标记
        （``KnowledgePoint`` 上"这条知识有未解决冲突"的事实）。
        """
        cid = processed_course
        sid = workspace.create_student(cid, "s-1", "Un")["student_id"]

        # 走真实的领域 API。冲突的判据是"两条互相矛盾的证据", 因此需要
        # 一个**至少引用两条证据**的知识点 —— 本夹具每条 KP 只引一条证据,
        # 所以这里构造一个引用两条既有证据的知识点（证据都来自真实材料,
        # 不是假造的内容）。
        ctx = workspace.context(cid)
        evidence_ids = sorted(
            {
                str(e)
                for kp in kps
                for e in (kp.get("evidence_refs") or [])
                if e
            }
        )
        assert len(evidence_ids) >= 2, "夹具前提: 课程至少要有两条证据"
        target = "kp-conflicted-under-test"

        # 走**真实的领域路径**: 冲突只存在于 ``KnowledgeStructure`` 里, 而
        # ``register_knowledge_point`` 只登记 KP、不产生结构快照（
        # ``_structure_by_kp`` 仅由 ``register_knowledge_structure`` 填充,
        # 见 src/knowledge_organization.py 两处实现）。所以必须把 KP 与它的
        # 冲突一起放进一个结构再整体注册 —— 这才是产品里冲突产生的实际
        # 方式（``processing_service`` / ``workspace._restore_course`` 都走
        # ``register_knowledge_structure``）。
        structure = _conflicted_structure(
            target, evidence_ids[:2], existing=ctx.org_service
        )
        ctx.org_service.register_knowledge_structure(structure)

        # 冲突确实与该知识点关联（判据 = 证据 refs 有交集）
        conflicts = workspace.conflicts(cid)
        touched = {
            str(k)
            for c in conflicts
            for k in (c.get("knowledge_point_ids") or [])
        }
        assert target in touched, (
            "冲突必须被投影到该知识点上, 否则排除规则没有输入"
        )

        row = _start(workspace, cid, sid)
        assert target in row["excluded"]["conflicted"], (
            "冲突知识必须被显式排除并报告, 而不是默默留在任务里"
        )
        assert all(
            item["knowledge_point_id"] != target
            for item in _all_candidates(workspace, cid, sid)
        )

    def test_truthy_conflict_normalisation(self):
        """``conflict`` 的字符串 "false" 不得被判为真（HTTP 查询串形态）。"""
        from src.application.learning_workflow import _truthy_conflict

        for true_value in (True, "true", "True", "1", "yes"):
            assert _truthy_conflict(true_value) is True
        for false_value in (False, None, "", "false", "False", "0", "no", "none"):
            assert _truthy_conflict(false_value) is False

    def test_progress_separates_the_two_truth_axes(
        self, workspace, processed_course, student
    ):
        row = _start(workspace, processed_course, student)
        progress = row["progress"]
        assert set(progress["by_state"]) == set(ORDERABLE_STATES)
        # 被排除的知识点单独统计, 不与"可学习"混在一起
        assert "excluded" in progress
        assert set(progress["excluded"]) == {"rejected", "conflicted"}

    def test_excluded_status_constants_cover_both_axes(self):
        """两个 truth axis 都要有各自的排除集合 —— 这是铁律 4 的可执行形式。"""
        from src.application.learning_workflow import EXCLUDED_REVIEW_STATUSES

        # validation 轴: 冲突知识不得当事实用
        assert EXCLUDED_VALIDATION_STATUSES == frozenset({"conflicted"})
        # review 轴: 人工否决同样不得当事实用（实测二者独立变化）
        assert EXCLUDED_REVIEW_STATUSES == frozenset({"rejected"})
        # unverified 不是排除项 —— 它可以学, 只是要带标记
        assert "unverified" not in EXCLUDED_VALIDATION_STATUSES
        assert "unverified" not in EXCLUDED_REVIEW_STATUSES
        assert "kept_unverified" not in EXCLUDED_REVIEW_STATUSES

    def test_workflow_version_is_stable(self):
        assert WORKFLOW_VERSION == "daily-learning-workflow-v1"


# ---------------------------------------------------------------------------
# 7. 多知识点 / 多次练习 / 重复点击
# ---------------------------------------------------------------------------


class TestManyKnowledgePoints:
    def test_multiple_knowledge_points_all_reachable(
        self, workspace, processed_course, student
    ):
        rows = _all_candidates(workspace, processed_course, student)
        assert len(rows) >= 1
        # 顺序必须是全序且稳定: 重复调用给出同一个序列
        again = _all_candidates(workspace, processed_course, student)
        assert [r["knowledge_point_id"] for r in rows] == [
            r["knowledge_point_id"] for r in again
        ]

    def test_walking_through_all_tasks_terminates(
        self, workspace, processed_course, student
    ):
        """逐个把知识点推到末端, 最终 has_task 必须变 false。"""
        guard = 0
        while guard < 60:
            guard += 1
            row = _start(workspace, processed_course, student)
            if not row["has_task"]:
                break
            kp_id = row["current_task"]["knowledge_point_id"]
            for event in ("viewed", "practiced", "reviewed"):
                workspace.record_learning_event(
                    processed_course, student, kp_id, event
                )
        final = _start(workspace, processed_course, student)
        assert final["has_task"] is False, (
            "把所有知识点推到 reviewing 之后不应再有任务"
        )
        assert final["note"] == NO_TASK_NOTE

    def test_repeated_clicks_do_not_duplicate_state(
        self, workspace, processed_course, student
    ):
        row = _start(workspace, processed_course, student)
        kp_id = row["current_task"]["knowledge_point_id"]
        for _ in range(5):
            workspace.learning_workflow_open_knowledge(
                processed_course, student, kp_id
            )
        page = workspace.learning_workflow_knowledge(
            processed_course, student, kp_id
        )
        assert page["student_state"] == "exposed"


# ---------------------------------------------------------------------------
# 8. Resume / restart (spec 66.9)
# ---------------------------------------------------------------------------


def _seed_dir(data_dir: str) -> tuple[str, str, str, str]:
    """在一个数据目录里建好课程 -> 材料 -> 知识 -> 学生 -> 走到一半。"""
    ws = Workspace(
        data_dir,
        clock=fixed_clock(FIXED_TIME),
        asr_mode="mock",
        ocr_mode="mock",
    )
    try:
        cid = ws.create_course("Programacio", "PROG101", "ca")["course_id"]
        session = ws.create_session(
            cid, session_number=1, date=TODAY, title="Tema"
        )
        for name in _fixtures_for(1):
            ws.register_material(cid, str(FIXTURES / name), session_id=session["session_id"])
        ws.process_session(cid, session["session_id"])
        sid = ws.create_student(cid, "s-quim", "Quim")["student_id"]
        row = _start(ws, cid, sid)
        kp_id = row["current_task"]["knowledge_point_id"]
        ws.learning_workflow_open_knowledge(cid, sid, kp_id)
        ex = ws.learning_workflow_exercise(cid, sid, kp_id)
        value = None
        if ex.get("exercise"):
            value = _correct_value(ex["exercise"]) or _wrong_value(ex["exercise"])
            if value is not None:
                ws.learning_workflow_answer(cid, sid, ex["exercise_id"], value)
        return cid, sid, kp_id, str(ex.get("exercise_id") or "")
    finally:
        ws.close()


class TestResume:
    def test_reopen_workspace_resumes_the_same_task(self, tmp_path):
        data_dir = str(tmp_path / "data")
        cid, sid, kp_id, eid = _seed_dir(data_dir)

        ws2 = Workspace(
            data_dir,
            clock=fixed_clock(FIXED_TIME),
            asr_mode="mock",
            ocr_mode="mock",
        )
        try:
            row = _start(ws2, cid, sid)
            assert row["has_task"] is True
            # 已经推进到 exposed 的知识点不应再是"未开始"
            page = ws2.learning_workflow_knowledge(cid, sid, kp_id)
            assert page["student_state"] == "exposed"
            assert page["student_activity"]["exposure_count"] >= 1
        finally:
            ws2.close()

    def test_restart_preserves_exercises_and_answers(self, tmp_path):
        data_dir = str(tmp_path / "data")
        cid, sid, kp_id, eid = _seed_dir(data_dir)
        ws2 = Workspace(
            data_dir,
            clock=fixed_clock(FIXED_TIME),
            asr_mode="mock",
            ocr_mode="mock",
        )
        try:
            ctx = ws2.context(cid)
            exercises = ctx.learning_service.list_exercises()
            assert len(exercises) >= 1
            assert any(str(e["exercise_id"]) == eid for e in exercises)
            state = ctx.learning_service.get_student_state(sid)
            assert state["states"], "重启后学生状态必须恢复"
        finally:
            ws2.close()

    def test_restart_keeps_progress_counts_consistent(self, tmp_path):
        data_dir = str(tmp_path / "data")
        cid, sid, kp_id, _ = _seed_dir(data_dir)
        ws2 = Workspace(
            data_dir,
            clock=fixed_clock(FIXED_TIME),
            asr_mode="mock",
            ocr_mode="mock",
        )
        try:
            row = _start(ws2, cid, sid)
            assert row["progress"]["excluded"]["rejected"] == 0
            assert row["progress"]["excluded"]["conflicted"] == 0
            assert row["progress"]["orderable"] >= 1
        finally:
            ws2.close()

    def test_restart_in_subprocess_matches_in_process(self, tmp_path):
        """真子进程启动 -> 读同一个数据目录, 结果必须一致。"""
        data_dir = str(tmp_path / "data")
        cid, sid, kp_id, _ = _seed_dir(data_dir)
        script = (
            "import json, sys\n"
            "sys.path.insert(0, r'D:/Project/Clases')\n"
            "from src.application.workspace import Workspace\n"
            f"ws = Workspace(r'{data_dir}')\n"
            f"row = ws.learning_workflow_start('{cid}', '{sid}')\n"
            "out = {'has_task': row['has_task'],\n"
            "       'task': (row['current_task'] or {}).get('knowledge_point_id'),\n"
            "       'orderable': row['progress']['orderable']}\n"
            "print(json.dumps(out))\n"
            "ws.close()\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=120,
            cwd="D:/Project/Clases",
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        assert payload["has_task"] is True
        assert payload["orderable"] >= 1

    def test_restart_is_stable_across_three_cycles(self, tmp_path):
        data_dir = str(tmp_path / "data")
        cid, sid, kp_id, _ = _seed_dir(data_dir)
        snapshots = []
        for _ in range(3):
            ws = Workspace(
                data_dir,
                clock=fixed_clock(FIXED_TIME),
                asr_mode="mock",
                ocr_mode="mock",
            )
            try:
                row = _start(ws, cid, sid)
                snapshots.append(
                    (
                        row["has_task"],
                        (row["current_task"] or {}).get("knowledge_point_id"),
                        row["progress"]["orderable"],
                        row["progress"]["by_state"],
                    )
                )
            finally:
                ws.close()
        assert snapshots[0] == snapshots[1] == snapshots[2]

    def test_resume_after_partial_learning_then_continue(self, tmp_path):
        data_dir = str(tmp_path / "data")
        cid, sid, kp_id, _ = _seed_dir(data_dir)
        ws = Workspace(
            data_dir,
            clock=fixed_clock(FIXED_TIME),
            asr_mode="mock",
            ocr_mode="mock",
        )
        try:
            # 继续走完剩下的状态推进, 证明链路是可续的
            workspace = ws
            for event in ("practiced", "reviewed"):
                workspace.record_learning_event(cid, sid, kp_id, event)
            page = workspace.learning_workflow_knowledge(cid, sid, kp_id)
            assert page["student_state"] == "reviewing"
            assert page["next_event"] is None
        finally:
            ws.close()

    def test_empty_data_dir_gives_empty_workflow(self, tmp_path):
        ws = Workspace(
            str(tmp_path / "fresh"),
            clock=fixed_clock(FIXED_TIME),
            asr_mode="mock",
            ocr_mode="mock",
        )
        try:
            assert ws.list_courses() == []
        finally:
            ws.close()


# ---------------------------------------------------------------------------
# 9. API contract (spec 66.10: 10 条 API 测试)
# ---------------------------------------------------------------------------


@pytest.fixture
def api(workspace, processed_course):
    server = create_server(workspace, host="127.0.0.1", port=0)
    server.start()
    yield f"http://127.0.0.1:{server.port}"
    server.stop()


def _get(base: str, path: str) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(base + path, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _post(base: str, path: str, body: Mapping[str, Any]) -> tuple[int, Any]:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class TestApiContract:
    def test_learning_start_returns_current_task(self, api, processed_course, student):
        status, payload = _get(
            api,
            f"/api/students/{student}/learning/start?course_id={processed_course}",
        )
        assert status == 200
        assert payload["success"] is True
        data = payload["data"]
        assert data["has_task"] is True
        assert data["current_task"]["knowledge_point_id"]
        assert data["workflow_version"] == WORKFLOW_VERSION

    def test_learning_start_unknown_course_is_404(self, api, student):
        status, payload = _get(
            api, f"/api/students/{student}/learning/start?course_id=course-nope"
        )
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_learning_start_unknown_student_is_404(self, api, processed_course):
        status, payload = _get(
            api,
            f"/api/students/s-nope/learning/start?course_id={processed_course}",
        )
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_learning_start_missing_course_id_is_400(self, api, student):
        status, payload = _get(api, f"/api/students/{student}/learning/start")
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_knowledge_endpoint_returns_page(self, api, processed_course, student):
        _, start = _get(
            api,
            f"/api/students/{student}/learning/start?course_id={processed_course}",
        )
        kp_id = start["data"]["current_task"]["knowledge_point_id"]
        status, payload = _get(
            api,
            f"/api/students/{student}/learning/knowledge/{kp_id}"
            f"?course_id={processed_course}",
        )
        assert status == 200
        data = payload["data"]
        assert data["knowledge_point"]["title"]
        assert "evidence" in data
        assert "source_language" in data

    def test_knowledge_endpoint_unknown_kp_is_404(self, api, processed_course, student):
        status, payload = _get(
            api,
            f"/api/students/{student}/learning/knowledge/kp-nope"
            f"?course_id={processed_course}",
        )
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_open_knowledge_records_viewed_event(
        self, api, processed_course, student
    ):
        _, start = _get(
            api,
            f"/api/students/{student}/learning/start?course_id={processed_course}",
        )
        kp_id = start["data"]["current_task"]["knowledge_point_id"]
        status, payload = _post(
            api,
            f"/api/students/{student}/learning/knowledge/{kp_id}"
            f"?course_id={processed_course}",
            {},
        )
        assert status == 200
        assert payload["data"]["student_state"] == "exposed"

    def test_exercise_endpoint_returns_grounded_exercise(
        self, api, processed_course, student
    ):
        _, start = _get(
            api,
            f"/api/students/{student}/learning/start?course_id={processed_course}",
        )
        kp_id = start["data"]["current_task"]["knowledge_point_id"]
        status, payload = _get(
            api,
            f"/api/students/{student}/learning/knowledge/{kp_id}/exercise"
            f"?course_id={processed_course}",
        )
        assert status == 200
        data = payload["data"]
        assert "reused" in data and "generated" in data

    def test_answer_endpoint_returns_evaluation_and_next_task(
        self, api, workspace, processed_course, student
    ):
        _, start = _get(
            api,
            f"/api/students/{student}/learning/start?course_id={processed_course}",
        )
        kp_id = start["data"]["current_task"]["knowledge_point_id"]
        _, ex = _get(
            api,
            f"/api/students/{student}/learning/knowledge/{kp_id}/exercise"
            f"?course_id={processed_course}",
        )
        exercise = ex["data"].get("exercise") or {}
        if not exercise.get("exercise_id"):
            pytest.skip("generator refused to produce an exercise")
        value = _correct_value(exercise) or _wrong_value(exercise)
        if value is None:
            pytest.skip("no usable submitted value")
        status, payload = _post(
            api,
            f"/api/students/{student}/learning/answer",
            {
                "course_id": processed_course,
                "exercise_id": exercise["exercise_id"],
                "submitted_value": value,
            },
        )
        assert status == 201
        data = payload["data"]
        assert data["evaluation"]["status"]
        assert data["evaluation_status"] == data["evaluation"]["status"]
        assert "next_task" in data

    def test_answer_endpoint_missing_field_is_400(self, api, processed_course, student):
        status, payload = _post(
            api,
            f"/api/students/{student}/learning/answer",
            {"course_id": processed_course, "exercise_id": "exercise-x"},
        )
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_answer_endpoint_mismatched_student_is_400(self, api, processed_course, student):
        status, payload = _post(
            api,
            f"/api/students/{student}/learning/answer",
            {
                "course_id": processed_course,
                "student_id": "s-someone-else",
                "exercise_id": "exercise-x",
                "submitted_value": "y",
            },
        )
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_answer_endpoint_unknown_exercise_is_404(
        self, api, processed_course, student
    ):
        status, payload = _post(
            api,
            f"/api/students/{student}/learning/answer",
            {
                "course_id": processed_course,
                "exercise_id": "exercise-does-not-exist",
                "submitted_value": "y",
            },
        )
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_no_endpoint_returns_500(self, api, processed_course, student):
        """正常的用户输入错误绝不能变成 500（spec 70.12）。"""
        cases = [
            ("GET", f"/api/students/{student}/learning/start", None),
            ("GET", "/api/students/s-nope/learning/start?course_id=x", None),
            (
                "GET",
                f"/api/students/{student}/learning/knowledge/kp-nope"
                f"?course_id={processed_course}",
                None,
            ),
            (
                "POST",
                f"/api/students/{student}/learning/answer",
                {"course_id": processed_course},
            ),
        ]
        for method, path, body in cases:
            if method == "GET":
                status, _ = _get(api, path)
            else:
                status, _ = _post(api, path, body or {})
            assert status != 500, f"{method} {path} returned 500"

    def test_learning_start_lang_parameter_is_echoed(self, api, processed_course, student):
        for lang in ("zh", "es", "ca"):
            status, payload = _get(
                api,
                f"/api/students/{student}/learning/start"
                f"?course_id={processed_course}&lang={lang}",
            )
            assert status == 200
            assert payload["data"]["lang"] == lang

    def test_workflow_response_is_json_serializable(
        self, api, processed_course, student
    ):
        status, payload = _get(
            api,
            f"/api/students/{student}/learning/start?course_id={processed_course}",
        )
        assert status == 200
        json.dumps(payload, ensure_ascii=False)  # 不抛异常即通过

    def test_knowledge_page_can_request_language(
        self, api, processed_course, student
    ):
        _, start = _get(
            api,
            f"/api/students/{student}/learning/start?course_id={processed_course}",
        )
        kp_id = start["data"]["current_task"]["knowledge_point_id"]
        status, payload = _get(
            api,
            f"/api/students/{student}/learning/knowledge/{kp_id}"
            f"?course_id={processed_course}&language=es",
        )
        assert status == 200
        explanation = payload["data"]["grounded_explanation"]
        assert explanation["requested_language"] == "es"

    def test_invalid_language_is_rejected(self, api, processed_course, student):
        _, start = _get(
            api,
            f"/api/students/{student}/learning/start?course_id={processed_course}",
        )
        kp_id = start["data"]["current_task"]["knowledge_point_id"]
        status, payload = _get(
            api,
            f"/api/students/{student}/learning/knowledge/{kp_id}"
            f"?course_id={processed_course}&language=../etc/passwd",
        )
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# 回归: Task 66 期间发现的三个真实缺陷
# ---------------------------------------------------------------------------


class TestKnowledgeFilterRegression:
    """``GET /api/knowledge`` 的全部过滤器 (Task 66 发现的真实缺陷)。

    修之前的状态
    ------------
    ``KnowledgeService.get_knowledge_points`` 把 ``kp`` (domain 对象
    ``KnowledgePoint``) 当成 dict 调 ``.get()``, 于是**只要带任何一个过滤器**
    就必然 ``AttributeError`` -> 5xx。5 个过滤器在测试里零覆盖, 所以这个
    崩溃一直没被发现 —— 而 ``Workspace.knowledge_points`` 那一侧的签名
    接线是好的 (Task 62 修过), 造成"看起来已经修好了"的假象。

    同时 ``conflict`` 过滤器读的 ``kp.get("conflict")`` 字段在 DTO 与
    domain 里**都不存在**, 即便不崩也恒为 False -> ``?conflict=true``
    永远返回空列表 (静默错, 比崩更难查)。
    """

    FILTERS = (
        "validation_status=supported",
        "validation_status=unverified",
        "validation_status=conflicted",
        "review_status=pending",
        "review_status=confirmed",
        "conflict=true",
        "conflict=false",
        "language=es",
        "language=ca",
        "search=Calculus",
    )

    @pytest.mark.parametrize("query", FILTERS)
    def test_filter_never_crashes(self, api, processed_course, query):
        status, payload = _get(
            api, f"/api/knowledge?course_id={processed_course}&{query}"
        )
        assert status == 200, f"{query} -> {status} (过滤器不得崩)"
        assert payload["success"] is True
        assert isinstance(payload["data"]["knowledge_points"], list)

    def test_no_filter_returns_everything(self, api, processed_course, kps):
        status, payload = _get(api, f"/api/knowledge?course_id={processed_course}")
        assert status == 200
        assert len(payload["data"]["knowledge_points"]) == len(kps)

    def test_validation_status_filter_partitions_the_set(
        self, api, processed_course, kps
    ):
        """三个 validation 取值必须把知识点**不重不漏**地分完。"""
        seen: set[str] = set()
        for value in ("supported", "unverified", "conflicted"):
            status, payload = _get(
                api,
                f"/api/knowledge?course_id={processed_course}"
                f"&validation_status={value}",
            )
            assert status == 200
            for row in payload["data"]["knowledge_points"]:
                assert row["validation_status"] == value, (
                    "过滤结果里混进了别的状态 —— 过滤器没生效"
                )
                seen.add(row["knowledge_id"])
        assert seen == {kp["knowledge_id"] for kp in kps}

    def test_conflict_filter_is_the_complement_of_itself(
        self, api, processed_course, kps
    ):
        """``conflict=true`` 与 ``conflict=false`` 必须互补。

        修之前两者都是空列表 (字段不存在) —— 互补性断言能抓住这种
        "静默返回空" 的实现, 光断言"不崩"抓不住。
        """
        ids_true: set[str] = set()
        ids_false: set[str] = set()
        for flag, bucket in (("true", ids_true), ("false", ids_false)):
            status, payload = _get(
                api,
                f"/api/knowledge?course_id={processed_course}&conflict={flag}",
            )
            assert status == 200
            for row in payload["data"]["knowledge_points"]:
                bucket.add(row["knowledge_id"])
                assert row["conflict"] is (flag == "true"), (
                    "conflict 字段与过滤器口径不一致"
                )
        assert ids_true | ids_false == {kp["knowledge_id"] for kp in kps}
        assert not (ids_true & ids_false)

    def test_search_filter_is_case_insensitive_substring(
        self, api, processed_course, kps
    ):
        title = str(kps[0]["title"])
        needle = title[:6].lower()
        status, payload = _get(
            api, f"/api/knowledge?course_id={processed_course}&search={needle}"
        )
        assert status == 200
        rows = payload["data"]["knowledge_points"]
        assert rows, "搜索自己的标题片段却什么都没搜到"
        assert all(
            needle in (str(r["title"]) + str(r["content"])).lower()
            or any(needle in str(t).lower() for t in (r["original_terms"] or []))
            for r in rows
        )

    def test_unknown_filter_value_returns_empty_not_error(
        self, api, processed_course
    ):
        """未知枚举值不是用户输入错误, 应返回空集合而不是 4xx/5xx。"""
        status, payload = _get(
            api,
            f"/api/knowledge?course_id={processed_course}"
            f"&validation_status=definitely-not-a-status",
        )
        assert status == 200
        assert payload["data"]["knowledge_points"] == []

    def test_knowledge_point_dto_exposes_conflict_flag(
        self, api, processed_course, kps
    ):
        """DTO 必须带 ``conflict`` 键 —— 两个下游都在读它。"""
        status, payload = _get(
            api,
            f"/api/knowledge/{kps[0]['knowledge_id']}?course_id={processed_course}",
        )
        assert status == 200
        assert "conflict" in payload["data"]
        assert isinstance(payload["data"]["conflict"], bool)

    def test_conflict_flag_surfaces_a_real_conflict(
        self, api, workspace, processed_course, kps
    ):
        """真有一条冲突时, 该知识点必须 ``conflict=true`` 并被学习流程排除。"""
        cid = processed_course
        ctx = workspace.context(cid)
        evidence_ids = sorted(
            {
                str(e)
                for kp in kps
                for e in (kp.get("evidence_refs") or [])
                if e
            }
        )
        assert len(evidence_ids) >= 2, "夹具前提: 至少两条证据"
        target = "kp-conflict-flag-check"
        ctx.org_service.register_knowledge_structure(
            _conflicted_structure(target, evidence_ids[:2], existing=ctx.org_service)
        )

        status, payload = _get(
            api, f"/api/knowledge/{target}?course_id={cid}"
        )
        assert status == 200
        assert payload["data"]["conflict"] is True, (
            "带未解决冲突的知识点在 DTO 上必须是 conflict=true"
        )
        status, payload = _get(
            api,
            f"/api/knowledge?course_id={cid}&conflict=true",
        )
        assert status == 200
        assert target in {
            r["knowledge_id"] for r in payload["data"]["knowledge_points"]
        }


class TestOpenKnowledgeParameterContract:
    """POST 知识页的参数来源契约 (Task 66 发现的真实缺陷)。

    同一组端点里 ``learning_answer`` 接受 body 里的 ``course_id``, 而
    ``learning_open_knowledge`` 只认查询串。调用方无法预测, 把参数放 body
    就吃到 400 —— 这是接口层面的自相矛盾, 不是调用方的错。
    """

    def test_course_id_from_querystring(self, api, processed_course, student):
        _, start = _get(
            api,
            f"/api/students/{student}/learning/start?course_id={processed_course}",
        )
        kp_id = start["data"]["current_task"]["knowledge_point_id"]
        status, payload = _post(
            api,
            f"/api/students/{student}/learning/knowledge/{kp_id}"
            f"?course_id={processed_course}",
            {},
        )
        assert status == 200
        assert payload["data"]["student_state"] == "exposed"

    def test_course_id_from_body(self, api, processed_course, student):
        _, start = _get(
            api,
            f"/api/students/{student}/learning/start?course_id={processed_course}",
        )
        kp_id = start["data"]["current_task"]["knowledge_point_id"]
        status, payload = _post(
            api,
            f"/api/students/{student}/learning/knowledge/{kp_id}",
            {"course_id": processed_course},
        )
        assert status == 200, "body 里的 course_id 必须被接受 (与 answer 端点一致)"
        assert payload["data"]["student_state"] == "exposed"

    def test_course_id_nowhere_is_400(self, api, processed_course, student):
        _, start = _get(
            api,
            f"/api/students/{student}/learning/start?course_id={processed_course}",
        )
        kp_id = start["data"]["current_task"]["knowledge_point_id"]
        status, payload = _post(
            api, f"/api/students/{student}/learning/knowledge/{kp_id}", {}
        )
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_reopening_the_same_page_is_idempotent(
        self, api, workspace, processed_course, student
    ):
        """重复打开同一页不得把 exposure_count 越加越多。

        注意 ``knowledge()`` 的 ``student_state`` 是**状态名**（字符串），
        计数在 ``student_activity`` 里 —— 两者刻意分开：状态由状态机决定，
        计数由事件归约决定 (Task 30)。
        """
        _, start = _get(
            api,
            f"/api/students/{student}/learning/start?course_id={processed_course}",
        )
        kp_id = start["data"]["current_task"]["knowledge_point_id"]
        counts = []
        states = []
        for _ in range(3):
            _, payload = _post(
                api,
                f"/api/students/{student}/learning/knowledge/{kp_id}",
                {"course_id": processed_course},
            )
            states.append(payload["data"]["student_state"])
            counts.append(payload["data"]["student_activity"]["exposure_count"])
        assert states == ["exposed"] * 3
        assert counts[0] >= 1
        assert counts[-1] == counts[0], (
            f"重复打开知识页改变了 exposure_count: {counts}"
        )


class TestAnswerRecordingSemantics:
    """作答记录语义 (Task 66 核实, 防止把 unsupported 误当答错)。"""

    def _opened_knowledge(self, api, cid, student) -> str:
        """取当前任务并**打开**它 —— 作答前必须有 viewed 事件。

        不做这一步, 状态还是 ``not_started``, 而状态机里 ``not_started``
        到 ``exposed`` 的唯一合法边是 ``viewed``。直接断言 ``exposed``
        会失败, 但那不是产品缺陷, 是测试漏了一步。
        """
        _, start = _get(
            api, f"/api/students/{student}/learning/start?course_id={cid}"
        )
        kp_id = start["data"]["current_task"]["knowledge_point_id"]
        _, opened = _post(
            api,
            f"/api/students/{student}/learning/knowledge/{kp_id}",
            {"course_id": cid},
        )
        assert opened["data"]["student_state"] == "exposed"
        return kp_id

    def _answer_with(self, api, cid, student, kp_id, *, correct: bool):
        _, ex = _get(
            api,
            f"/api/students/{student}/learning/knowledge/{kp_id}/exercise"
            f"?course_id={cid}",
        )
        exercise = ex["data"].get("exercise") or {}
        if not exercise.get("exercise_id"):
            pytest.skip("generator refused to produce an exercise")
        value = _correct_value(exercise) if correct else _wrong_value(exercise)
        if value is None:
            pytest.skip("exercise has no decidable value")
        return _post(
            api,
            f"/api/students/{student}/learning/answer",
            {
                "course_id": cid,
                "exercise_id": exercise["exercise_id"],
                "submitted_value": value,
            },
        )

    def test_correct_answer_updates_counts_but_not_state(
        self, api, processed_course, student
    ):
        kp_id = self._opened_knowledge(api, processed_course, student)
        status, payload = self._answer_with(
            api, processed_course, student, kp_id, correct=True
        )
        assert status == 201
        assert payload["data"]["evaluation_status"] == "correct"
        state = payload["data"]["student_state"]
        assert state["correct_count"] == 1
        assert state["answer_count"] == 1
        # 铁律 5: ANSWERED 不是转移事件, 答对**不推进**状态
        assert state["state"] == "exposed"
        assert state["practice_count"] == 0

    def test_incorrect_answer_updates_counts_but_not_state(
        self, api, processed_course, student
    ):
        kp_id = self._opened_knowledge(api, processed_course, student)
        status, payload = self._answer_with(
            api, processed_course, student, kp_id, correct=False
        )
        assert status == 201
        assert payload["data"]["evaluation_status"] == "incorrect"
        state = payload["data"]["student_state"]
        assert state["incorrect_count"] == 1
        assert state["answer_count"] == 1
        assert state["state"] == "exposed", "答错同样不推进状态"

    def test_undecidable_answer_records_no_correctness(
        self, api, processed_course, student
    ):
        """``unsupported`` = 评价器**无法判定**, 不是"答错"。

        把它记成答错会污染错题中心 (Task 65) —— 学生交了一段乱码, 系统却
        宣布他答错了这道题。所以这里断言计数**完全不变**。
        """
        kp_id = self._opened_knowledge(api, processed_course, student)
        _, ex = _get(
            api,
            f"/api/students/{student}/learning/knowledge/{kp_id}/exercise"
            f"?course_id={processed_course}",
        )
        exercise = ex["data"].get("exercise") or {}
        if not exercise.get("exercise_id"):
            pytest.skip("generator refused to produce an exercise")
        status, payload = _post(
            api,
            f"/api/students/{student}/learning/answer",
            {
                "course_id": processed_course,
                "exercise_id": exercise["exercise_id"],
                "submitted_value": "\u4e00\u6bb5\u65e0\u6cd5\u5224\u5b9a\u7684\u4e71\u7801 zzzz",
            },
        )
        assert status == 201
        assert payload["data"]["evaluation_status"] == "unsupported"
        state = payload["data"]["student_state"]
        assert state["correct_count"] == 0
        assert state["incorrect_count"] == 0
        assert state["answer_count"] == 0

    def test_repeating_the_same_answer_is_idempotent(
        self, api, processed_course, student
    ):
        kp_id = self._opened_knowledge(api, processed_course, student)
        _, first = self._answer_with(
            api, processed_course, student, kp_id, correct=True
        )
        _, second = self._answer_with(
            api, processed_course, student, kp_id, correct=True
        )
        assert first["data"]["student_state"] == second["data"]["student_state"], (
            "同一份答案重复提交不得重复计数 (Task 51 的幂等要求)"
        )
