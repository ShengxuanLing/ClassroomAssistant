# -*- coding: utf-8 -*-
"""Task 65 — Mistake & Weak Knowledge Center 测试。

覆盖的 spec 条款::

    65.2  Mistake List         Exercise / Question / Answer / Evaluation /
                               Knowledge / Course / Topic
    65.3  Group by Knowledge    知识点 -> 错答次数
    65.4  Group by Topic        Topic -> Knowledge -> Exercises
    65.5  Evaluation Source     对错只读既有 Evaluation, 不另算一套
    65.6  Weak Knowledge        不允许 wrong_count > 0 == weak
    65.7  Student State         只用 NEEDS_REVIEW / NEEDS_PRACTICE 既有语义
    65.8  Suggested Actions     Review / Evidence / Practice / Prerequisite,
                               且必须有真实关系支撑
    65.9  Evidence              错题 -> 为什么错 -> 重新学习依据
    65.10 Practice Again        优先复用既有 Exercise, 不无限重生成
    65.11 Empty State           "No mistakes yet." 正常显示
    65.12 Tests                 40+

几条刻意写死的判据（产品语义，不是实现细节）
--------------------------------------------
1. **"答对" 不是错题。** 只有 ``status == "incorrect"`` 的评估才进错题表。
   答对的答案必须**完全不出现** —— 不是"标成对"，而是不出现。
2. **薄弱必须来自 StudentState。** 一个知识点哪怕错了 5 次，只要
   Task 30 没给它 NEEDS_REVIEW / NEEDS_PRACTICE，它就必须出现在
   ``knowledge`` 里（带 ``incorrect_attempts=5``）而**不在**
   ``weak_knowledge`` 里。这是 spec 65.6 的可执行形式。
3. **"再练一次" 不生成新题。** 调用前后 ``list_exercises`` 的长度必须
   完全一致。这是 spec 65.10 的可执行形式 —— 一个"每次点都多一道题"
   的实现会在这里挂掉。
4. **没有依据的动作不出现。** 知识点若既无证据也无前置，就只应有
   ``REVIEW_KNOWLEDGE``; 不该出现一个点了没反应的按钮。
5. **隔离落在成员关系上。** knowledge_id 是内容寻址的，两门课里相同材料
   会产生相同 id，所以断言必须查"这门课的错题集合"，不是 id 字符串。
6. **重启后错题集合逐字段一致。** 进程 A 作答，进程 B 只拿到数据目录，
   必须重放出同一个 mistake 集合（answer_id / evaluation_id / attempts）。
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
from src.application.mistakes_view import (
    CENTER_FORBIDDEN_TERMS,
    CENTER_VERSION,
    DEFAULT_LIMIT,
    MISTAKES_EMPTY_NOTE,
    SCHEMA_VERSION,
    SUGGESTED_ACTIONS,
    WEAK_STATES,
    MistakesView,
)
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace
from src.exercise_generation import GenerationConfig, TemplateId

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
def supported_kps(workspace, processed_course) -> list[dict[str, Any]]:
    points = [
        kp for kp in workspace.knowledge_points(processed_course)
        if kp["validation_status"] == "supported"
    ]
    if not points:
        pytest.skip("fixture produced no supported knowledge point")
    return points


@pytest.fixture
def supported_kp(supported_kps) -> dict[str, Any]:
    return supported_kps[0]


@pytest.fixture
def student(workspace, processed_course) -> str:
    return workspace.create_student(
        processed_course, "s-quim", "Quim"
    )["student_id"]


@pytest.fixture
def second_kp(supported_kps) -> dict[str, Any]:
    if len(supported_kps) < 2:
        pytest.skip("need at least two supported knowledge points")
    return supported_kps[1]


# ---------------------------------------------------------------------------
# 工具：造错题
# ---------------------------------------------------------------------------


#: 能产生 ``incorrect`` 的题型（这些题型的"答错"才是一个可构造的事实）。
#:
#: Task 32 的评估器对不同题型有不同规则:
#:
#:   multiple_choice  合法但非正确答案的选项 id            -> incorrect
#:   true_false       "true"/"false" 里的错的那一个        -> incorrect
#:   fill_blank       任何不在 accepted_answers 里的串     -> incorrect
#:   short_answer     **永远是 unsupported**（字面不等即 unsupported,
#:                    是 Task 32.6 "禁止语义判分" 的直接后果）
#:
#: 注意 multiple_choice 的错值**不能随便给一个字符串**: 给了非法
#: choice_id 时评估器返回的是 ``unsupported``（"submitted value is
#: not a valid choice id"），而不是 ``incorrect``。所以它的错值必须
#: 从这道题自己的 choices 里挑（见 ``_wrong_value``）。
#:
#: short_answer 题**造不出** incorrect —— 这不是本模块的缺陷, 而是
#: "没有语义判分就没有'答错'这个概念"。测试要造错题时不能依赖它。
_WRONGABLE_TYPES = ("multiple_choice", "true_false", "fill_blank")


def _wrong_value(exercise: Mapping[str, Any]) -> Optional[str]:
    """给定题目, 返回一个**必然判 incorrect** 的提交值。

    不可得时返回 None（调用方据此跳过, 而不是伪造一个值）。
    """
    kind = exercise.get("exercise_type")
    if kind == "multiple_choice":
        draft = exercise.get("draft") or {}
        correct = (
            draft.get("correct_choice_id")
            or exercise.get("_draft_correct_choice_id")
        )
        # 从这道题自己的选项里挑一个**合法但非正确**的 id —— 给非法 id
        # 只会得到 unsupported, 那样测的就不是错题而是非法输入了。
        for choice in draft.get("choices") or exercise.get("choices") or []:
            choice_id = choice.get("choice_id")
            if choice_id and choice_id != correct:
                return choice_id
        return None
    if kind == "true_false":
        # 模板约定正确答案恒为 "true"，所以错值恒为 "false"。
        return "false"
    if kind == "fill_blank":
        accepted = (
            exercise.get("_draft_accepted_answers")
            or (exercise.get("draft") or {}).get("accepted_answers")
            or []
        )
        if "zzz-not-the-answer-zzz" not in accepted:
            return "zzz-not-the-answer-zzz"
        return None
    return None


def _generate(workspace, course_id: str, kp_id: str, seed: int) -> Optional[dict]:
    """生成一次，返回规范化后的 exercise（含 draft 上的答案字段）。"""
    result = workspace.generate_exercise(
        course_id, kp_id, config=GenerationConfig(seed=seed)
    )
    if not result.get("generated"):
        return None
    exercise = _exercise_of(result)
    exercise["draft"] = result.get("draft") or {}
    return exercise


def _make_exercise(workspace, course_id: str, kp_id: str, *, seed: int = 0):
    """生成一道题（走 Task 64 的真实路径）。

    只返回**能产生 incorrect** 的题型（multiple_choice / true_false /
    fill_blank）。short_answer 会被跳过 —— 见 ``_wrong_value``。
    """
    for offset in range(8):
        exercise = _generate(workspace, course_id, kp_id, seed + offset)
        if exercise and exercise["exercise_type"] in _WRONGABLE_TYPES:
            return exercise
    pytest.skip("no seed produced an exercise whose wrongness is expressible")


def _make_exercise_of_type(workspace, course_id: str, kp_id: str, want: str):
    """生成一道**指定题型**的题（题型必须可判 incorrect）。"""
    assert want in _WRONGABLE_TYPES, want
    for seed in range(24):
        exercise = _generate(workspace, course_id, kp_id, seed)
        if exercise and exercise["exercise_type"] == want:
            return exercise
    pytest.skip(f"no seed produced a {want!r} exercise")


def _make_two_distinct_exercises(workspace, course_id: str, kp_id: str):
    """同一知识点上的两道**不同**练习题。

    优先挑两个不同题型（模板不同 -> exercise_id 必然不同）；若该知识点
    只支持一种题型，则退回"同题型不同 seed"，此时可能只有一道题，
    调用方需要能接受 —— 所以这里显式断言真的拿到了两道。
    """
    made: list[dict] = []
    seen: set[str] = set()
    for want in ("multiple_choice", "true_false", "fill_blank"):
        try:
            exercise = _make_exercise_of_type(workspace, course_id, kp_id, want)
        except Exception:  # noqa: BLE001 - 该题型不可生成, 试下一个
            continue
        eid = exercise["exercise_id"]
        if eid not in seen:
            seen.add(eid)
            made.append(exercise)
        if len(made) == 2:
            return made
    if len(made) < 2:
        pytest.skip("knowledge point supports fewer than two distinct exercises")
    return made


def _make_three_distinct_exercises(workspace, course_id: str, kp_id: str):
    """同一知识点上的三道不同练习题（用于"错 3 次"这类断言）。"""
    made: list[dict] = []
    seen: set[str] = set()
    for want in ("multiple_choice", "true_false", "fill_blank"):
        for seed in range(24):
            exercise = _generate(workspace, course_id, kp_id, seed)
            if not exercise or exercise["exercise_type"] != want:
                continue
            eid = exercise["exercise_id"]
            if eid in seen:
                continue
            seen.add(eid)
            made.append(exercise)
            break
        if len(made) == 3:
            return made
    if len(made) < 3:
        pytest.skip("knowledge point supports fewer than three distinct exercises")
    return made


def _pick_type(workspace, course_id: str, kp_id: str, *, want: str) -> dict:
    """选一个**指定题型**可生成的模板，返回 generate 结果。"""
    for seed in range(8):
        result = workspace.generate_exercise(
            course_id, kp_id, config=GenerationConfig(seed=seed)
        )
        if result.get("generated") and result["exercise"]["exercise_type"] == want:
            return result
    pytest.skip(f"no seed produced a {want!r} exercise for this knowledge point")


def _exercise_of(result: Mapping[str, Any]) -> dict[str, Any]:
    """从 generate 结果里取出 exercise 记录。

    判断题/选择题的**正确答案**不在 ``exercise`` 上（``Workspace`` 的
    exercise DTO 有意不暴露答案）, 而在 draft 上。所以这里把两份合并 ——
    测试需要答案才能造出"答对"这个事实。
    """
    exercise = dict(result.get("exercise") or {})
    draft = result.get("draft") or {}
    for field in ("correct_choice_id", "expected_answer", "accepted_answers", "blank_id"):
        if field in draft and (field not in exercise or exercise[field] is None):
            exercise["_draft_" + field] = draft[field]
    return exercise


def _answer_wrong(workspace, course_id: str, student_id: str, exercise: dict) -> dict:
    """提交一个**必然判错**（status == "incorrect"）的答案。

    对 ``short_answer`` 题跳过 —— 见 ``_wrong_value`` 的说明:
    Task 32 对自由文本只做字面比较, 不等即 ``unsupported``, 因此
    "答错"在 short_answer 上不是一个可构造的事实。
    """
    eid = exercise["exercise_id"]
    kind = exercise.get("exercise_type")
    value = _wrong_value(exercise)
    if value is None:
        pytest.skip(
            f"exercise_type {kind!r} cannot produce an 'incorrect' evaluation"
        )
    answer = workspace.submit_answer(course_id, student_id, eid, value, sequence=1)
    assert answer["evaluation_status"] == "incorrect", answer
    return answer


def _correct_value(exercise: Mapping[str, Any]) -> Optional[str]:
    """能判 correct 的提交值（不可得时返回 None）。

    - true_false: 模板约定正确答案永远是 ``"true"``
    - multiple_choice: draft 上的 ``correct_choice_id``
    - fill_blank / short_answer: draft 上的 ``expected_answer``
      或 ``accepted_answers[0]``
    """
    kind = exercise.get("exercise_type")
    if kind == "true_false":
        return "true"
    draft = exercise.get("draft") or {}
    if kind == "multiple_choice":
        return draft.get("correct_choice_id") or exercise.get("_draft_correct_choice_id")
    accepted = exercise.get("_draft_accepted_answers") or draft.get("accepted_answers")
    if accepted:
        return accepted[0]
    return exercise.get("_draft_expected_answer") or draft.get("expected_answer")


def _answer_right(workspace, course_id: str, student_id: str, exercise: dict) -> dict:
    """提交一个**必然判对**的答案。"""
    value = _correct_value(exercise)
    if not value:
        pytest.skip("cannot derive the correct value for this exercise")
    answer = workspace.submit_answer(
        course_id, student_id, exercise["exercise_id"], str(value), sequence=1
    )
    assert answer["evaluation_status"] == "correct", answer
    return answer


def _mistakes_for(workspace, course_id: str, student_id: str) -> dict[str, Any]:
    return workspace.mistakes_center(course_id, student_id)


# ---------------------------------------------------------------------------
# 1) 错题列表（spec 65.2）
# ---------------------------------------------------------------------------


class TestMistakeList:
    def test_empty_student_has_no_mistakes(
        self, workspace, processed_course, student
    ):
        view = _mistakes_for(workspace, processed_course, student)
        assert view["has_mistakes"] is False
        assert view["mistakes"] == []
        assert view["note"] == MISTAKES_EMPTY_NOTE

    def test_incorrect_answer_produces_exactly_one_mistake(
        self, workspace, processed_course, student, supported_kp
    ):
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        _answer_wrong(workspace, processed_course, student, ex)
        view = _mistakes_for(workspace, processed_course, student)
        assert view["has_mistakes"] is True
        assert len(view["mistakes"]) == 1
        assert view["note"] is None

    def test_mistake_carries_every_field_the_spec_lists(
        self, workspace, processed_course, student, supported_kp
    ):
        """65.2: Exercise / Question / Answer / Evaluation / Knowledge /
        Course / Topic 都必须在一条错题里找到。"""
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        _answer_wrong(workspace, processed_course, student, ex)
        row = _mistakes_for(workspace, processed_course, student)["mistakes"][0]

        assert row["exercise_id"] == ex["exercise_id"]        # Exercise
        assert row["prompt"]                                   # Question
        assert row["submitted_value"] is not None              # Answer
        assert row["evaluation_id"]                            # Evaluation
        assert row["status"] == "incorrect"                    # Evaluation
        assert row["knowledge_point_ids"]                      # Knowledge
        assert row["knowledge"][0]["title"]                    # Knowledge
        # course 由顶层给定（这个投影本身就是课程范围内的）
        view = _mistakes_for(workspace, processed_course, student)
        assert view["course_id"] == processed_course           # Course
        # topic 在 group_by=topic 视图里
        by_topic = workspace.mistakes_center(
            processed_course, student, group_by="topic"
        )
        assert by_topic["groups"], "topic grouping produced no groups"

    def test_correct_answer_never_becomes_a_mistake(
        self, workspace, processed_course, student, supported_kp
    ):
        """答对的答案必须**完全不出现**在错题表里。"""
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        answer = _answer_right(workspace, processed_course, student, ex)
        assert answer["evaluation_status"] == "correct"
        view = _mistakes_for(workspace, processed_course, student)
        assert view["has_mistakes"] is False
        assert answer["answer_id"] not in {
            m["answer_id"] for m in view["mistakes"]
        }

    def test_mixed_answers_only_surface_the_incorrect_ones(
        self, workspace, processed_course, student, supported_kps
    ):
        wrong_kp = supported_kps[0]["knowledge_id"]
        right_kp = supported_kps[1]["knowledge_id"]
        wrong_ex = _make_exercise(workspace, processed_course, wrong_kp, seed=0)
        right_ex = _make_exercise(workspace, processed_course, right_kp, seed=0)
        _answer_wrong(workspace, processed_course, student, wrong_ex)
        _answer_right(workspace, processed_course, student, right_ex)

        view = _mistakes_for(workspace, processed_course, student)
        ids = {m["exercise_id"] for m in view["mistakes"]}
        assert ids == {wrong_ex["exercise_id"]}
        assert right_ex["exercise_id"] not in ids

    def test_mistake_is_attributed_to_the_right_knowledge_point(
        self, workspace, processed_course, student, supported_kp
    ):
        """错题必须挂到它那道题真正关联的 KP 上。"""
        kp_id = supported_kp["knowledge_id"]
        ex = _make_exercise(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, ex)
        row = _mistakes_for(workspace, processed_course, student)["mistakes"][0]
        assert kp_id in row["knowledge_point_ids"]

    def test_two_exercises_for_one_kp_produce_two_mistakes(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        a, b = _make_two_distinct_exercises(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, a)
        _answer_wrong(workspace, processed_course, student, b)
        view = _mistakes_for(workspace, processed_course, student)
        assert len(view["mistakes"]) == 2
        assert view["counts"]["incorrect_attempts"] == 2

    def test_mistake_order_is_deterministic(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        a, b = _make_two_distinct_exercises(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, b)
        _answer_wrong(workspace, processed_course, student, a)
        first = _mistakes_for(workspace, processed_course, student)["mistakes"]
        second = _mistakes_for(workspace, processed_course, student)["mistakes"]
        assert [m["answer_id"] for m in first] == [m["answer_id"] for m in second]

    def test_limit_is_respected(self, workspace, processed_course, student, supported_kp):
        kp_id = supported_kp["knowledge_id"]
        for ex in _make_three_distinct_exercises(workspace, processed_course, kp_id):
            _answer_wrong(workspace, processed_course, student, ex)
        view = MistakesView(workspace, processed_course, limit=2).center(student)
        assert len(view["mistakes"]) == 2
        assert view["limits"]["mistakes"] == 2

    def test_unknown_student_raises_not_found(self, workspace, processed_course):
        from src.application.errors import NotFoundError

        with pytest.raises(NotFoundError):
            workspace.mistakes_center(processed_course, "nobody")

    def test_empty_student_id_is_rejected(self, workspace, processed_course):
        from src.application.errors import InvalidInputError

        with pytest.raises(InvalidInputError):
            workspace.mistakes_center(processed_course, "  ")

    def test_bad_group_by_is_rejected(self, workspace, processed_course, student):
        from src.application.errors import InvalidInputError

        with pytest.raises(InvalidInputError):
            workspace.mistakes_center(
                processed_course, student, group_by="astrology"
            )

    def test_view_declares_its_schema_and_version(
        self, workspace, processed_course, student
    ):
        view = _mistakes_for(workspace, processed_course, student)
        assert view["schema_version"] == SCHEMA_VERSION
        assert view["center_version"] == CENTER_VERSION

    def test_no_forbidden_wording_anywhere_in_the_payload(
        self, workspace, processed_course, student, supported_kp
    ):
        """整份 payload 不得含"已掌握 / 薄弱"这类未经验证的判断词。"""
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        _answer_wrong(workspace, processed_course, student, ex)
        blob = json.dumps(
            _mistakes_for(workspace, processed_course, student), ensure_ascii=False
        ).lower()
        for term in CENTER_FORBIDDEN_TERMS:
            assert term not in blob, term


# ---------------------------------------------------------------------------
# 2) 按知识点聚合（spec 65.3）
# ---------------------------------------------------------------------------


class TestGroupByKnowledge:
    def test_groups_are_keyed_by_knowledge(
        self, workspace, processed_course, student, supported_kp
    ):
        for ex in _make_two_distinct_exercises(
            workspace, processed_course, supported_kp["knowledge_id"]
        ):
            _answer_wrong(workspace, processed_course, student, ex)
        view = _mistakes_for(workspace, processed_course, student)
        assert view["group_by"] == "knowledge"
        assert all(g["group_kind"] == "knowledge" for g in view["groups"])
        assert len(view["groups"]) == 1
        assert view["groups"][0]["incorrect_attempts"] == 2

    def test_group_children_are_the_exercises(
        self, workspace, processed_course, student, supported_kp
    ):
        for ex in _make_two_distinct_exercises(
            workspace, processed_course, supported_kp["knowledge_id"]
        ):
            _answer_wrong(workspace, processed_course, student, ex)
        view = _mistakes_for(workspace, processed_course, student)
        children = view["groups"][0]["children"]
        assert len(children) == 2
        assert all(c["exercise_id"] for c in children)

    def test_two_knowledge_points_give_two_groups(
        self, workspace, processed_course, student, supported_kps
    ):
        for kp in supported_kps[:2]:
            ex = _make_exercise(workspace, processed_course, kp["knowledge_id"])
            _answer_wrong(workspace, processed_course, student, ex)
        view = _mistakes_for(workspace, processed_course, student)
        assert len(view["groups"]) == 2
        assert len(view["knowledge"]) == 2

    def test_knowledge_row_reports_attempt_count_not_a_verdict(
        self, workspace, processed_course, student, supported_kp
    ):
        """65.6: 只给次数, 不给 "weak" 布尔。"""
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        _answer_wrong(workspace, processed_course, student, ex)
        row = _mistakes_for(workspace, processed_course, student)["knowledge"][0]
        assert row["incorrect_attempts"] == 1
        assert "weak" not in row
        assert "is_weak" not in row
        assert "mastery" not in row

    def test_knowledge_row_keeps_validation_and_review_status(
        self, workspace, processed_course, student, supported_kp
    ):
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        _answer_wrong(workspace, processed_course, student, ex)
        row = _mistakes_for(workspace, processed_course, student)["knowledge"][0]
        assert row["validation_status"] in ("supported", "unverified", "conflicted")
        assert row["review_status"] is not None

    def test_counts_block_is_consistent_with_the_rows(
        self, workspace, processed_course, student, supported_kps
    ):
        for kp in supported_kps[:2]:
            ex = _make_exercise(workspace, processed_course, kp["knowledge_id"])
            _answer_wrong(workspace, processed_course, student, ex)
        view = _mistakes_for(workspace, processed_course, student)
        assert view["counts"]["mistakes"] == len(view["mistakes"])
        assert view["counts"]["knowledge_with_mistakes"] == len(view["knowledge"])
        assert view["counts"]["groups"] == len(view["groups"])
        assert view["counts"]["incorrect_attempts"] == sum(
            r["incorrect_attempts"] for r in view["knowledge"]
        )


# ---------------------------------------------------------------------------
# 3) 按 topic 聚合（spec 65.4）
# ---------------------------------------------------------------------------


class TestGroupByTopic:
    def test_topic_grouping_produces_topic_groups(
        self, workspace, processed_course, student, supported_kp
    ):
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        _answer_wrong(workspace, processed_course, student, ex)
        view = workspace.mistakes_center(
            processed_course, student, group_by="topic"
        )
        assert view["group_by"] == "topic"
        assert view["groups"]
        assert all(g["group_kind"] == "topic" for g in view["groups"])

    def test_topic_group_has_three_levels(
        self, workspace, processed_course, student, supported_kp
    ):
        """65.4: Topic -> Knowledge -> Exercises。"""
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        _answer_wrong(workspace, processed_course, student, ex)
        view = workspace.mistakes_center(
            processed_course, student, group_by="topic"
        )
        group = view["groups"][0]
        assert group["children_kind"] == "knowledge"
        for child in group["children"]:
            assert child["group_kind"] == "knowledge"
            assert child["children_kind"] == "exercise"
            for leaf in child["children"]:
                assert leaf["exercise_id"]

    def test_unassigned_knowledge_is_explicit_not_dropped(
        self, workspace, processed_course, student, supported_kp
    ):
        """没有 topic 归属的知识点必须出现在一个明确的组里。"""
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        _answer_wrong(workspace, processed_course, student, ex)
        view = workspace.mistakes_center(
            processed_course, student, group_by="topic"
        )
        ids = {g["group_id"] for g in view["groups"]}
        assert ids, "no groups at all"
        # 至少有一个组包住了这个 KP
        covered = {
            child["group_id"]
            for g in view["groups"]
            for child in g["children"]
        }
        assert supported_kp["knowledge_id"] in covered

    def test_topic_and_knowledge_views_agree_on_attempt_counts(
        self, workspace, processed_course, student, supported_kp
    ):
        for seed in (0, 1):
            ex = _make_exercise(
                workspace, processed_course, supported_kp["knowledge_id"], seed=seed
            )
            _answer_wrong(workspace, processed_course, student, ex)
        by_kp = _mistakes_for(workspace, processed_course, student)
        by_topic = workspace.mistakes_center(
            processed_course, student, group_by="topic"
        )
        assert by_topic["counts"]["incorrect_attempts"] == \
            by_kp["counts"]["incorrect_attempts"]


# ---------------------------------------------------------------------------
# 4) Evaluation 来源（spec 65.5）
# ---------------------------------------------------------------------------


class TestEvaluationSource:
    def test_mistake_status_comes_from_the_stored_evaluation(
        self, workspace, processed_course, student, supported_kp
    ):
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        answer = _answer_wrong(workspace, processed_course, student, ex)
        stored = workspace.get_evaluation(processed_course, answer["answer_id"])
        row = _mistakes_for(workspace, processed_course, student)["mistakes"][0]
        assert row["evaluation_id"] == stored["evaluation_id"]
        assert row["status"] == stored["status"]
        assert row["score"] == stored["score"]
        assert row["feedback"] == stored["feedback"]

    def test_the_view_never_recomputes_correctness(
        self, workspace, processed_course, student, supported_kp
    ):
        """把一个 'correct' 的评估手动改写成 'incorrect'，视图必须跟着变。

        这条断言证明视图**读的是存储里的那一份**，而不是自己算的:
        如果视图内部有任何重算逻辑，它就不会被这个改写影响。
        """
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        answer = _answer_right(workspace, processed_course, student, ex)
        assert _mistakes_for(workspace, processed_course, student)["has_mistakes"] is False

        # 直接改写既有评估记录（模拟"存储里的真相变了"）
        service = workspace.context(processed_course).learning_service
        result = service._evaluation_by_answer[answer["answer_id"]]
        object.__setattr__(result, "status", type(result.status)("incorrect"))

        view = _mistakes_for(workspace, processed_course, student)
        assert view["has_mistakes"] is True
        assert view["mistakes"][0]["evaluation_id"] == answer["evaluation_id"]

    def test_answer_without_evaluation_is_skipped_not_guessed(
        self, workspace, processed_course, student, supported_kp
    ):
        """没有评估 = 没有"对错"这个概念。必须跳过，不能猜成错。"""
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        answer = _answer_wrong(workspace, processed_course, student, ex)
        service = workspace.context(processed_course).learning_service
        service._evaluation_by_answer.pop(answer["answer_id"], None)

        view = _mistakes_for(workspace, processed_course, student)
        assert view["has_mistakes"] is False

    def test_evaluator_version_is_propagated(
        self, workspace, processed_course, student, supported_kp
    ):
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        _answer_wrong(workspace, processed_course, student, ex)
        row = _mistakes_for(workspace, processed_course, student)["mistakes"][0]
        assert row["evaluator_version"]


# ---------------------------------------------------------------------------
# 5) 薄弱知识点（spec 65.6 / 65.7）
# ---------------------------------------------------------------------------


class TestWeakKnowledge:
    def test_incorrect_attempts_alone_do_not_make_it_weak(
        self, workspace, processed_course, student, supported_kp
    ):
        """65.6 的可执行形式: 错了 3 次, 但没有 StudentState 信号 ->
        **不算薄弱**, 只有 incorrect_attempts=3。"""
        kp_id = supported_kp["knowledge_id"]
        for ex in _make_three_distinct_exercises(workspace, processed_course, kp_id):
            _answer_wrong(workspace, processed_course, student, ex)

        view = _mistakes_for(workspace, processed_course, student)
        row = next(r for r in view["knowledge"] if r["knowledge_id"] == kp_id)
        assert row["incorrect_attempts"] == 3
        assert row["attention"] is None
        assert row["attention_basis"] is None
        assert view["weak_knowledge"] == []

    def test_needs_practice_state_surfaces_as_weak(
        self, workspace, processed_course, student, supported_kp
    ):
        """显式投 viewed + practiced -> state=practicing -> NEEDS_PRACTICE。"""
        kp_id = supported_kp["knowledge_id"]
        workspace.record_learning_event(processed_course, student, kp_id, "viewed")
        workspace.record_learning_event(processed_course, student, kp_id, "practiced")

        view = _mistakes_for(workspace, processed_course, student)
        weak = view["weak_knowledge"]
        assert len(weak) == 1
        assert weak[0]["knowledge_id"] == kp_id
        assert weak[0]["signal"] == "NEEDS_PRACTICE"
        assert "Task 30" in weak[0]["basis"]

    def test_needs_review_state_surfaces_as_weak(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        for event in ("viewed", "practiced", "reviewed"):
            workspace.record_learning_event(processed_course, student, kp_id, event)

        view = _mistakes_for(workspace, processed_course, student)
        weak = view["weak_knowledge"]
        assert len(weak) == 1
        assert weak[0]["signal"] == "NEEDS_REVIEW"

    def test_not_started_is_never_weak(
        self, workspace, processed_course, student, supported_kp
    ):
        view = _mistakes_for(workspace, processed_course, student)
        assert view["weak_knowledge"] == []

    def test_weak_states_are_exactly_the_two_spec_signals(self):
        assert set(WEAK_STATES) == {"NEEDS_REVIEW", "NEEDS_PRACTICE"}

    def test_practicing_and_reviewing_are_the_only_sources(
        self, workspace, processed_course, student, supported_kps
    ):
        kp_a = supported_kps[0]["knowledge_id"]
        kp_b = supported_kps[1]["knowledge_id"]
        workspace.record_learning_event(processed_course, student, kp_a, "viewed")
        workspace.record_learning_event(processed_course, student, kp_b, "viewed")
        # kp_b 只到 exposed, kp_a 到 practicing
        workspace.record_learning_event(processed_course, student, kp_a, "practiced")

        view = _mistakes_for(workspace, processed_course, student)
        weak_ids = {w["knowledge_id"] for w in view["weak_knowledge"]}
        assert weak_ids == {kp_a}
        assert kp_b not in weak_ids

    def test_weak_rows_carry_their_definition(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        workspace.record_learning_event(processed_course, student, kp_id, "viewed")
        workspace.record_learning_event(processed_course, student, kp_id, "practiced")
        weak = _mistakes_for(workspace, processed_course, student)["weak_knowledge"][0]
        assert weak["definition"] == "StudentState (Task 30)"
        assert weak["student_state"] == "practicing"

    def test_a_knowledge_point_can_be_weak_without_any_mistake(
        self, workspace, processed_course, student, supported_kp
    ):
        """有 StudentState 信号但没错过 -> 仍应出现在薄弱列表里。

        这正是"不能只看 wrong_count"的理由之一: 错过才有记录的说法
        会漏掉这类知识点。
        """
        kp_id = supported_kp["knowledge_id"]
        workspace.record_learning_event(processed_course, student, kp_id, "viewed")
        workspace.record_learning_event(processed_course, student, kp_id, "practiced")

        view = _mistakes_for(workspace, processed_course, student)
        assert view["has_mistakes"] is False
        assert len(view["weak_knowledge"]) == 1
        assert view["weak_knowledge"][0]["incorrect_attempts"] == 0


# ---------------------------------------------------------------------------
# 6) 建议动作（spec 65.8）
# ---------------------------------------------------------------------------


class TestSuggestedActions:
    def test_review_knowledge_is_always_available(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        ex = _make_exercise(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, ex)
        detail = workspace.mistake_detail(processed_course, student, kp_id)
        actions = {a["action"] for a in detail["suggested_actions"]}
        assert "REVIEW_KNOWLEDGE" in actions

    def test_evidence_action_only_when_evidence_exists(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        ex = _make_exercise(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, ex)
        detail = workspace.mistake_detail(processed_course, student, kp_id)
        actions = {a["action"]: a for a in detail["suggested_actions"]}
        if detail["evidence"]:
            assert "VIEW_EVIDENCE" in actions
            assert actions["VIEW_EVIDENCE"]["basis"]
        else:
            assert "VIEW_EVIDENCE" not in actions

    def test_practice_action_only_when_exercises_exist(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        ex = _make_exercise(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, ex)
        detail = workspace.mistake_detail(processed_course, student, kp_id)
        actions = {a["action"]: a for a in detail["suggested_actions"]}
        assert "PRACTICE_AGAIN" in actions
        assert actions["PRACTICE_AGAIN"]["targets"]

    def test_prerequisite_action_only_when_a_relation_exists(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        ex = _make_exercise(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, ex)
        detail = workspace.mistake_detail(processed_course, student, kp_id)
        actions = {a["action"]: a for a in detail["suggested_actions"]}
        if detail["prerequisites"]:
            assert "VIEW_PREREQUISITE" in actions
            assert actions["VIEW_PREREQUISITE"]["target"] == detail["prerequisites"][0]
        else:
            assert "VIEW_PREREQUISITE" not in actions

    def test_every_action_carries_a_basis(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        ex = _make_exercise(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, ex)
        detail = workspace.mistake_detail(processed_course, student, kp_id)
        assert detail["suggested_actions"], "expected at least REVIEW_KNOWLEDGE"
        for action in detail["suggested_actions"]:
            assert action["action"] in SUGGESTED_ACTIONS
            assert action["basis"], action
            assert action["href"], action

    def test_no_action_points_at_a_nonexistent_target(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        ex = _make_exercise(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, ex)
        detail = workspace.mistake_detail(processed_course, student, kp_id)
        known_exercises = {
            e["exercise_id"] for e in workspace.list_exercises(processed_course)
        }
        for action in detail["suggested_actions"]:
            if action["action"] == "PRACTICE_AGAIN":
                assert set(action["targets"]) <= known_exercises


# ---------------------------------------------------------------------------
# 7) 依据链（spec 65.9）
# ---------------------------------------------------------------------------


class TestEvidenceTraceback:
    def test_detail_explains_why_it_was_wrong(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        ex = _make_exercise(workspace, processed_course, kp_id)
        answer = _answer_wrong(workspace, processed_course, student, ex)
        detail = workspace.mistake_detail(processed_course, student, kp_id)
        assert len(detail["why_incorrect"]) == 1
        row = detail["why_incorrect"][0]
        assert row["answer_id"] == answer["answer_id"]
        assert row["status"] == "incorrect"
        assert row["submitted_value"] is not None

    def test_detail_resolves_evidence_to_material(
        self, workspace, processed_course, student, supported_kp
    ):
        """错题 -> 依据: 证据必须能落到具体材料上。"""
        kp_id = supported_kp["knowledge_id"]
        ex = _make_exercise(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, ex)
        detail = workspace.mistake_detail(processed_course, student, kp_id)
        assert detail["evidence"], "expected evidence for a supported KP"
        for row in detail["evidence"]:
            assert row["evidence_id"]
            assert row["content"]
            assert row["material"], "evidence must resolve to a material"
            assert row["material"]["filename"]

    def test_evidence_is_sorted_deterministically(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        ex = _make_exercise(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, ex)
        a = workspace.mistake_detail(processed_course, student, kp_id)["evidence"]
        b = workspace.mistake_detail(processed_course, student, kp_id)["evidence"]
        assert [r["evidence_id"] for r in a] == [r["evidence_id"] for r in b]

    def test_detail_works_even_without_mistakes(
        self, workspace, processed_course, student, supported_kp
    ):
        """没错过也能看依据 —— 这是"提前复习"的入口, 不该报错。"""
        kp_id = supported_kp["knowledge_id"]
        detail = workspace.mistake_detail(processed_course, student, kp_id)
        assert detail["incorrect_attempts"] == 0
        assert detail["why_incorrect"] == []
        assert detail["evidence"]

    def test_detail_rejects_unknown_knowledge_point(
        self, workspace, processed_course, student
    ):
        from src.application.errors import NotFoundError

        with pytest.raises(NotFoundError):
            workspace.mistake_detail(processed_course, student, "kp-nope")

    def test_detail_carries_the_knowledge_metadata(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        detail = workspace.mistake_detail(processed_course, student, kp_id)
        assert detail["knowledge"]["knowledge_id"] == kp_id
        assert detail["knowledge"]["validation_status"] in (
            "supported", "unverified", "conflicted"
        )

    def test_prerequisites_come_from_a_real_relation(
        self, workspace, processed_course, student, supported_kp
    ):
        """前置必须真的被某道题声明过, 不是猜的。"""
        kp_id = supported_kp["knowledge_id"]
        detail = workspace.mistake_detail(processed_course, student, kp_id)
        declared: set[str] = set()
        for ex in workspace.list_exercises(processed_course):
            if kp_id in (ex.get("knowledge_point_ids") or []):
                declared.update(ex.get("prerequisites") or [])
        assert set(detail["prerequisites"]) >= (declared - {kp_id})
        assert kp_id not in detail["prerequisites"]


# ---------------------------------------------------------------------------
# 8) 再练一次（spec 65.10）
# ---------------------------------------------------------------------------


class TestPracticeAgain:
    def test_practice_again_creates_no_new_exercise(
        self, workspace, processed_course, student, supported_kp
    ):
        """65.10 的可执行形式: 读 N 次, 练习表长度不变。"""
        kp_id = supported_kp["knowledge_id"]
        ex = _make_exercise(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, ex)

        before = len(workspace.list_exercises(processed_course))
        for _ in range(5):
            workspace.mistake_detail(processed_course, student, kp_id)
            _mistakes_for(workspace, processed_course, student)
        after = len(workspace.list_exercises(processed_course))
        assert after == before

    def test_the_center_creates_no_new_exercise(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        ex = _make_exercise(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, ex)
        before = len(workspace.list_exercises(processed_course))
        for group_by in ("knowledge", "topic"):
            workspace.mistakes_center(
                processed_course, student, group_by=group_by
            )
        assert len(workspace.list_exercises(processed_course)) == before

    def test_practice_targets_are_existing_exercises(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        ex = _make_exercise(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, ex)
        detail = workspace.mistake_detail(processed_course, student, kp_id)
        known = {e["exercise_id"] for e in workspace.list_exercises(processed_course)}
        assert detail["practice_targets"]
        for target in detail["practice_targets"]:
            assert target["exercise_id"] in known
            assert target["reused"] is True

    def test_unattempted_exercises_rank_first(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        attempted, fresh = _make_two_distinct_exercises(
            workspace, processed_course, kp_id
        )
        _answer_wrong(workspace, processed_course, student, attempted)

        detail = workspace.mistake_detail(processed_course, student, kp_id)
        order = [t["exercise_id"] for t in detail["practice_targets"]]
        assert order[0] == fresh["exercise_id"]
        assert order[-1] == attempted["exercise_id"]

    def test_practice_targets_are_deterministically_ordered(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        for ex in _make_three_distinct_exercises(workspace, processed_course, kp_id):
            _answer_wrong(workspace, processed_course, student, ex)
        a = workspace.mistake_detail(processed_course, student, kp_id)["practice_targets"]
        b = workspace.mistake_detail(processed_course, student, kp_id)["practice_targets"]
        assert [t["exercise_id"] for t in a] == [t["exercise_id"] for t in b]

    def test_practice_targets_flag_what_was_already_wrong(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        ex = _make_exercise(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, ex)
        detail = workspace.mistake_detail(processed_course, student, kp_id)
        target = next(
            t for t in detail["practice_targets"]
            if t["exercise_id"] == ex["exercise_id"]
        )
        assert target["previously_attempted"] is True
        assert target["previously_incorrect"] is True

    def test_no_practice_targets_when_no_exercise_exists_for_the_kp(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        detail = workspace.mistake_detail(processed_course, student, kp_id)
        assert detail["practice_targets"] == []
        actions = {a["action"] for a in detail["suggested_actions"]}
        assert "PRACTICE_AGAIN" not in actions


# ---------------------------------------------------------------------------
# 9) 空状态（spec 65.11）
# ---------------------------------------------------------------------------


class TestEmptyState:
    def test_empty_note_is_the_spec_string(self):
        assert MISTAKES_EMPTY_NOTE == "No mistakes yet."

    def test_empty_student_view_is_normal_not_an_error(
        self, workspace, processed_course, student
    ):
        view = _mistakes_for(workspace, processed_course, student)
        assert view["has_mistakes"] is False
        assert view["note"] == MISTAKES_EMPTY_NOTE
        assert view["mistakes"] == []
        assert view["knowledge"] == []
        assert view["groups"] == []
        assert view["weak_knowledge"] == []
        assert view["counts"]["mistakes"] == 0

    def test_empty_view_still_returns_links_and_limits(
        self, workspace, processed_course, student
    ):
        view = _mistakes_for(workspace, processed_course, student)
        assert view["links"]["exercises"]
        assert view["links"]["review_center"]
        assert view["limits"]["mistakes"] == DEFAULT_LIMIT

    def test_empty_view_works_in_both_groupings(
        self, workspace, processed_course, student
    ):
        for group_by in ("knowledge", "topic"):
            view = workspace.mistakes_center(
                processed_course, student, group_by=group_by
            )
            assert view["has_mistakes"] is False
            assert view["groups"] == []


# ---------------------------------------------------------------------------
# 9b) 查询模式: 禁止 N+1（spec: 不得出现 N+1 / N×M 查询）
# ---------------------------------------------------------------------------


class TestQueryPattern:
    """错题列表必须一次取回日志 + 一次建索引, 不能在循环里逐条取评估。"""

    def test_evaluation_index_is_built_in_one_pass(self, workspace, processed_course, student):
        """``get_evaluation`` 的调用次数必须与"答案条数"同阶, 不是 2 倍。"""
        import src.application.mistakes_view as module

        view_obj = module.MistakesView(workspace, processed_course)
        learning = view_obj._learning()
        log = learning.answer_log_for(student)

        calls = {"n": 0}
        real = learning.get_evaluation

        def counting(answer_id):
            calls["n"] += 1
            return real(answer_id)

        learning.get_evaluation = counting  # type: ignore[assignment]
        try:
            index = view_obj._evaluation_index(log)
        finally:
            learning.get_evaluation = real  # type: ignore[assignment]

        # 每个答案最多查一次 —— 不会因为"先判空再取值"而被调用两次。
        assert calls["n"] <= len(log), (
            "get_evaluation called %d times for %d answers (N+1 shape)"
            % (calls["n"], len(log))
        )
        # 索引是完备的: 每个有评估的答案都在里面。
        for answer in log:
            answer_id = answer.get("answer_id")
            if answer_id:
                try:
                    real(answer_id)
                except NotFoundError:
                    assert answer_id not in index
                else:
                    assert answer_id in index

    def test_center_builds_the_index_once_not_per_row(
        self, workspace, processed_course, student, supported_kp
    ):
        """整个 ``center()`` 里 ``get_evaluation`` 的调用次数有上界。"""
        import src.application.mistakes_view as module

        kp_id = supported_kp["knowledge_id"]
        exercise = _make_exercise(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, exercise)

        view_obj = module.MistakesView(workspace, processed_course)
        learning = view_obj._learning()
        log = learning.answer_log_for(student)

        calls = {"n": 0}
        real = learning.get_evaluation

        def counting(answer_id):
            calls["n"] += 1
            return real(answer_id)

        learning.get_evaluation = counting  # type: ignore[assignment]
        try:
            view = view_obj.center(student)
        finally:
            learning.get_evaluation = real  # type: ignore[assignment]

        assert view["has_mistakes"] is True
        # 4 个投影区块 (mistakes / knowledge / groups / weak) 共用同一份索引,
        # 所以调用次数有上界 —— 不是 4 × 答案数。
        assert calls["n"] <= len(log), (
            "center() called get_evaluation %d times for %d answers"
            % (calls["n"], len(log))
        )

    def test_source_has_no_per_row_evaluation_lookup_inside_a_loop(self):
        """静态守卫: ``get_evaluation`` 只允许出现在索引构造函数里。

        N+1 的定义不是"循环里出现了一次查询" —— 建索引本身就是一次
        O(n) 遍历。真正的 N+1 是**同一批数据在多个阶段被反复查**:
        比如列表阶段查一遍、分组阶段再查一遍、薄弱判断阶段又查一遍。

        因此本守卫的判据是: ``get_evaluation`` 在整个模块里只允许出现
        **一次**, 且必须落在 ``_evaluation_index`` 里。
        """
        import re
        from pathlib import Path

        source = Path("src/application/mistakes_view.py").read_text(encoding="utf-8")
        # 去掉注释与 docstring, 避免文档里的字样误触发。
        body = re.sub(r"#[^\n]*", "", source)
        body = re.sub(r'"""[\s\S]*?"""', "", body)
        body = re.sub(r"'''[\s\S]*?'''", "", body)

        call_sites = [
            number
            for number, line in enumerate(body.split("\n"), 1)
            if "get_evaluation(" in line
        ]
        assert len(call_sites) == 1, (
            "get_evaluation must be called from exactly one place "
            "(the evaluation index); found %r" % call_sites
        )
        # 那一个调用点必须位于 _evaluation_index 内。
        index_start = body.index("def _evaluation_index(")
        index_end = body.index("def _mistakes(", index_start)
        offset = body[:index_start].count("\n") + 1
        end_line = body[:index_end].count("\n") + 1
        assert offset <= call_sites[0] <= end_line, (
            "get_evaluation is called outside _evaluation_index (line %d, "
            "index spans %d-%d)" % (call_sites[0], offset, end_line)
        )

    def test_no_projection_recomputes_correctness(self):
        """守卫: 投影里不出现"自己判对错"的形状。"""
        import re
        from pathlib import Path

        source = Path("src/application/mistakes_view.py").read_text(encoding="utf-8")
        body = re.sub(r"#[^\n]*", "", source)
        body = re.sub(r'"""[\s\S]*?"""', "", body)
        # 不允许比较提交值与任何"正确答案"。
        forbidden = [
            r"correct_choice_id",
            r"expected_answer",
            r"accepted_answers",
            r"is_true",
        ]
        found = [p for p in forbidden if re.search(p, body)]
        assert not found, (
            "mistakes view must not inspect answer keys (%r); "
            "correctness comes from Evaluation only" % found
        )


# ---------------------------------------------------------------------------
# 10) 课程隔离（spec 64.14 / 学习闭环）
# ---------------------------------------------------------------------------


class TestCourseIsolation:
    def test_mistakes_are_scoped_to_one_course(
        self, workspace, course, processed_course, student, supported_kp
    ):
        """A 课错题不得出现在 B 课视图里。"""
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        _answer_wrong(workspace, processed_course, student, ex)

        other = workspace.create_course("Algorismica", "ALG101", "ca")["course_id"]
        _processed(workspace, other, 2)
        other_student = workspace.create_student(other, "s-other", "Other")["student_id"]

        view = workspace.mistakes_center(other, other_student)
        assert view["has_mistakes"] is False
        assert view["course_id"] == other

    def test_a_student_from_another_course_is_not_found(
        self, workspace, course, processed_course, student
    ):
        from src.application.errors import NotFoundError

        other = workspace.create_course("Algorismica", "ALG101", "ca")["course_id"]
        _processed(workspace, other, 2)
        with pytest.raises(NotFoundError):
            workspace.mistakes_center(other, student)

    def test_identical_material_in_two_courses_keeps_membership_disjoint(
        self, workspace, course, processed_course, student, supported_kp
    ):
        """同一份材料在两门课里会产生相同 knowledge_id（内容寻址），
        所以隔离必须落在**成员集合**上，而不是 id 字符串。"""
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        _answer_wrong(workspace, processed_course, student, ex)
        a_error_kp = supported_kp["knowledge_id"]

        other = workspace.create_course("Algorismica", "ALG101", "ca")["course_id"]
        _processed(workspace, other, 1)  # 同一批夹具 -> 相同 KP id
        other_student = workspace.create_student(other, "s-other", "Other")["student_id"]

        b_view = workspace.mistakes_center(other, other_student)
        assert b_view["has_mistakes"] is False
        assert a_error_kp not in {m["knowledge_point_ids"][0] for m in b_view["mistakes"]}

        a_view = workspace.mistakes_center(processed_course, student)
        assert a_view["has_mistakes"] is True

    def test_three_courses_stay_independent(
        self, workspace, course, processed_course, supported_kp
    ):
        """A / B / C 三门课, 只有 A 有错题。"""
        a = processed_course
        a_student = workspace.create_student(a, "s-a", "A")["student_id"]
        ex = _make_exercise(workspace, a, supported_kp["knowledge_id"])
        _answer_wrong(workspace, a, a_student, ex)

        ids = {}
        for index, (name, code, n) in enumerate(
            (("B", "B101", 2), ("C", "C101", 3)), start=1
        ):
            cid = workspace.create_course(name, code, "ca")["course_id"]
            _processed(workspace, cid, n)
            sid = workspace.create_student(cid, f"s-{name}", name)["student_id"]
            ids[cid] = sid

        assert workspace.mistakes_center(a, a_student)["has_mistakes"] is True
        for cid, sid in ids.items():
            view = workspace.mistakes_center(cid, sid)
            assert view["has_mistakes"] is False
            assert view["course_id"] == cid


# ---------------------------------------------------------------------------
# 11) API 契约
# ---------------------------------------------------------------------------


class _Client:
    def __init__(self, base: str) -> None:
        self.base = base

    def request(self, path: str, *, method: str = "GET", body: Any = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def get(self, path: str):
        return self.request(path)


@pytest.fixture
def client(workspace):
    instance = create_server(workspace, port=0)
    instance.start()
    try:
        yield _Client(instance.url)
    finally:
        instance.stop()


class TestApiContract:
    def test_center_endpoint_returns_the_view(
        self, client, processed_course, student
    ):
        status, payload = client.get(
            f"/api/students/{student}/mistakes?course_id={processed_course}"
        )
        assert status == 200, payload
        assert payload["data"]["student_id"] == student

    def test_center_endpoint_reflects_mistakes(
        self, client, workspace, processed_course, student, supported_kp
    ):
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        _answer_wrong(workspace, processed_course, student, ex)
        status, payload = client.get(
            f"/api/students/{student}/mistakes?course_id={processed_course}"
        )
        assert status == 200, payload
        assert payload["data"]["has_mistakes"] is True
        assert len(payload["data"]["mistakes"]) == 1

    def test_center_endpoint_supports_topic_grouping(
        self, client, workspace, processed_course, student, supported_kp
    ):
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        _answer_wrong(workspace, processed_course, student, ex)
        status, payload = client.get(
            f"/api/students/{student}/mistakes"
            f"?course_id={processed_course}&group_by=topic"
        )
        assert status == 200, payload
        assert payload["data"]["group_by"] == "topic"

    def test_center_endpoint_rejects_unknown_group_by(
        self, client, processed_course, student
    ):
        status, payload = client.get(
            f"/api/students/{student}/mistakes"
            f"?course_id={processed_course}&group_by=nope"
        )
        assert status == 400, payload
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_center_endpoint_requires_course_id(self, client, student):
        status, payload = client.get(f"/api/students/{student}/mistakes")
        assert status == 400, payload

    def test_center_endpoint_returns_404_for_unknown_student(
        self, client, processed_course
    ):
        status, payload = client.get(
            f"/api/students/nobody/mistakes?course_id={processed_course}"
        )
        assert status == 404, payload
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_detail_endpoint_returns_the_traceback(
        self, client, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        ex = _make_exercise(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, ex)
        status, payload = client.get(
            f"/api/students/{student}/mistakes/{kp_id}?course_id={processed_course}"
        )
        assert status == 200, payload
        data = payload["data"]
        assert data["why_incorrect"]
        assert data["evidence"]
        assert data["suggested_actions"]

    def test_detail_endpoint_returns_404_for_unknown_kp(
        self, client, processed_course, student
    ):
        status, payload = client.get(
            f"/api/students/{student}/mistakes/kp-nope?course_id={processed_course}"
        )
        assert status == 404, payload

    def test_endpoints_never_leak_a_traceback(
        self, client, processed_course, student
    ):
        for path in (
            f"/api/students/{student}/mistakes?course_id={processed_course}&group_by=x",
            f"/api/students/{student}/mistakes/kp-nope?course_id={processed_course}",
        ):
            _, payload = client.get(path)
            blob = json.dumps(payload)
            assert "Traceback" not in blob
            assert 'File "' not in blob

    def test_center_endpoint_is_read_only(
        self, client, workspace, processed_course, student, supported_kp
    ):
        ex = _make_exercise(workspace, processed_course, supported_kp["knowledge_id"])
        _answer_wrong(workspace, processed_course, student, ex)
        before = len(workspace.list_exercises(processed_course))
        for _ in range(3):
            client.get(f"/api/students/{student}/mistakes?course_id={processed_course}")
        assert len(workspace.list_exercises(processed_course)) == before


# ---------------------------------------------------------------------------
# 12) 重启一致性
# ---------------------------------------------------------------------------


class TestRestartConsistency:
    def test_mistakes_replay_after_a_real_subprocess_restart(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        ex = _make_exercise(workspace, processed_course, kp_id)
        answer = _answer_wrong(workspace, processed_course, student, ex)
        evaluation = workspace.get_evaluation(processed_course, answer["answer_id"])

        before = _mistakes_for(workspace, processed_course, student)
        assert before["has_mistakes"] is True

        data_dir = str(workspace.data_dir)
        workspace.close()

        script = _RESTART_SCRIPT.format(
            data_dir=json.dumps(data_dir),
            course_id=json.dumps(processed_course),
            student_id=json.dumps(student),
            fixed_time=json.dumps(FIXED_TIME),
            project=json.dumps(str(Path(__file__).resolve().parents[1])),
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr
        replayed = json.loads(completed.stdout.strip().splitlines()[-1])

        assert replayed["has_mistakes"] is True
        assert replayed["mistake_count"] == 1
        assert replayed["answer_id"] == answer["answer_id"]
        assert replayed["evaluation_id"] == evaluation["evaluation_id"]
        assert replayed["status"] == "incorrect"
        assert replayed["incorrect_attempts"] == 1
        assert replayed["exercise_count"] == 1
        assert replayed["evidence_resolved"] is True
        assert replayed["actions"]

    def test_restart_does_not_duplicate_exercises(
        self, workspace, processed_course, student, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        ex = _make_exercise(workspace, processed_course, kp_id)
        _answer_wrong(workspace, processed_course, student, ex)
        data_dir = str(workspace.data_dir)
        workspace.close()

        script = _RESTART_SCRIPT_REREAD.format(
            data_dir=json.dumps(data_dir),
            course_id=json.dumps(processed_course),
            student_id=json.dumps(student),
            fixed_time=json.dumps(FIXED_TIME),
            project=json.dumps(str(Path(__file__).resolve().parents[1])),
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr
        replayed = json.loads(completed.stdout.strip().splitlines()[-1])
        assert replayed["exercise_count"] == 1
        assert replayed["mistake_count"] == 1


_RESTART_SCRIPT = """
import json, sys
sys.path.insert(0, {project})
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace

ws = Workspace({data_dir}, clock=fixed_clock({fixed_time}), asr_mode="mock", ocr_mode="mock")
try:
    course_id = {course_id}
    student_id = {student_id}
    view = ws.mistakes_center(course_id, student_id)
    mistake = view["mistakes"][0]
    kp_id = mistake["knowledge_point_ids"][0]
    detail = ws.mistake_detail(course_id, student_id, kp_id)
    print(json.dumps({{
        "has_mistakes": view["has_mistakes"],
        "mistake_count": len(view["mistakes"]),
        "answer_id": mistake["answer_id"],
        "evaluation_id": mistake["evaluation_id"],
        "status": mistake["status"],
        "incorrect_attempts": detail["incorrect_attempts"],
        "exercise_count": len(ws.list_exercises(course_id)),
        "evidence_resolved": bool(
            detail["evidence"] and detail["evidence"][0]["material"]
        ),
        "actions": [a["action"] for a in detail["suggested_actions"]],
    }}))
finally:
    ws.close()
"""

_RESTART_SCRIPT_REREAD = """
import json, sys
sys.path.insert(0, {project})
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace

ws = Workspace({data_dir}, clock=fixed_clock({fixed_time}), asr_mode="mock", ocr_mode="mock")
try:
    course_id = {course_id}
    student_id = {student_id}
    # 连读三次: 视图必须是纯读的, 不能每次多出一道题
    for _ in range(3):
        view = ws.mistakes_center(course_id, student_id)
    print(json.dumps({{
        "exercise_count": len(ws.list_exercises(course_id)),
        "mistake_count": len(view["mistakes"]),
    }}))
finally:
    ws.close()
"""
