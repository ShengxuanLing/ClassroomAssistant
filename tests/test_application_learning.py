# -*- coding: utf-8 -*-
"""Task 34 — LearningService 单元测试。

覆盖 create_student / get_student / list_students / get_student_state /
create_exercise (4 种题型) / list_exercises / get_exercise /
submit_answer / get_evaluation / get_study_plan / get_learning_path /
get_learning_status
重点: 幂等 / 错误映射 / 确定性 / 评估正确性
"""

import pytest

from src.application.errors import ConflictError, InvalidInputError, NotFoundError
from src.application.learning_service import LearningService


COURSE_ID = "course-t34"


def _svc() -> LearningService:
    return LearningService(COURSE_ID)


# ----------------------------------------------------------------------
# Student
# ----------------------------------------------------------------------


def test_create_student_returns_dto():
    svc = _svc()
    result = svc.create_student("student-1", "Alice")
    assert result["student_id"] == "student-1"
    assert result["display_name"] == "Alice"


def test_create_student_idempotent_same_id_and_name():
    svc = _svc()
    r1 = svc.create_student("s1", "Bob")
    r2 = svc.create_student("s1", "Bob")
    assert r1 == r2


def test_create_student_conflict_on_different_display_name():
    svc = _svc()
    svc.create_student("s1", "Bob")
    with pytest.raises(ConflictError):
        svc.create_student("s1", "Other")


def test_create_student_requires_nonempty_id():
    svc = _svc()
    with pytest.raises(InvalidInputError):
        svc.create_student("")


def test_get_student_not_found():
    svc = _svc()
    with pytest.raises(NotFoundError):
        svc.get_student("missing")


def test_list_students_sorted_by_id():
    svc = _svc()
    svc.create_student("b")
    svc.create_student("a")
    ids = [s["student_id"] for s in svc.list_students()]
    assert ids == ["a", "b"]


# ----------------------------------------------------------------------
# get_student_state
# ----------------------------------------------------------------------


def test_get_student_state_empty():
    svc = _svc()
    svc.create_student("s1")
    state = svc.get_student_state("s1")
    assert state["student_id"] == "s1"
    assert state["course_id"] == COURSE_ID
    assert state["registered_knowledge_points"] == []


def test_get_student_state_requires_nonempty_id():
    svc = _svc()
    with pytest.raises(InvalidInputError):
        svc.get_student_state("")


def test_get_student_state_not_found():
    svc = _svc()
    with pytest.raises(NotFoundError):
        svc.get_student_state("missing")


# ----------------------------------------------------------------------
# Exercise creation — 4 种题型
# ----------------------------------------------------------------------


def _kps():
    return ["kp-1"]


def test_create_multiple_choice_exercise():
    svc = _svc()
    result = svc.create_exercise(
        "multiple_choice",
        "Which city is the capital of Spain?",
        _kps(),
        choices=[{"choice_id": "a", "text": "Madrid"}, {"choice_id": "b", "text": "Lisbon"}],
        correct_choice_id="a",
    )
    assert result["exercise_id"].startswith("exercise-")
    assert result["exercise_type"] == "multiple_choice"
    assert result["knowledge_point_ids"] == _kps()


def test_create_true_false_exercise():
    svc = _svc()
    result = svc.create_exercise(
        "true_false",
        "The capital of Spain is Lisbon.",
        _kps(),
        is_true=False,
    )
    assert result["exercise_type"] == "true_false"
    assert result["knowledge_point_ids"] == _kps()


def test_create_short_answer_exercise():
    svc = _svc()
    result = svc.create_exercise(
        "short_answer",
        "Name the capital of France.",
        _kps(),
        expected_answer="Paris",
    )
    assert result["exercise_type"] == "short_answer"
    assert result["knowledge_point_ids"] == _kps()


def test_create_fill_blank_exercise():
    svc = _svc()
    result = svc.create_exercise(
        "fill_blank",
        "The capital of Spain is ____.",
        _kps(),
        blank_id="blank-1",
        accepted_answers=["Madrid"],
    )
    assert result["exercise_type"] == "fill_blank"
    assert result["knowledge_point_ids"] == _kps()


def test_create_exercise_rejects_unknown_type():
    svc = _svc()
    with pytest.raises(InvalidInputError):
        svc.create_exercise("unknown", "p", _kps())


def test_create_exercise_requires_knowledge_point_ids():
    svc = _svc()
    with pytest.raises(InvalidInputError):
        svc.create_exercise("true_false", "p", [], is_true=True)


def test_create_exercise_multiple_choice_requires_choices():
    svc = _svc()
    with pytest.raises(InvalidInputError):
        svc.create_exercise(
            "multiple_choice",
            "p",
            _kps(),
            correct_choice_id="a",  # no choices
        )


def test_create_exercise_fill_blank_requires_accepted_answers():
    svc = _svc()
    with pytest.raises(InvalidInputError):
        svc.create_exercise(
            "fill_blank", "p", _kps(), blank_id="b1", accepted_answers=[]
        )


def test_create_exercise_is_idempotent_for_identical_input():
    svc = _svc()
    kw = dict(
        choices=[{"choice_id": "a", "text": "Madrid"}, {"choice_id": "b", "text": "Lisbon"}],
        correct_choice_id="a",
    )
    r1 = svc.create_exercise("multiple_choice", "Capital?", _kps(), **kw)
    r2 = svc.create_exercise("multiple_choice", "Capital?", _kps(), **kw)
    assert r1["exercise_id"] == r2["exercise_id"]
    assert len(svc.list_exercises()) == 1


def test_create_exercise_distinct_prompts_create_distinct_exercises():
    svc = _svc()
    kw = dict(
        choices=[{"choice_id": "a", "text": "Madrid"}, {"choice_id": "b", "text": "Lisbon"}],
        correct_choice_id="a",
    )
    r1 = svc.create_exercise("multiple_choice", "Capital?", _kps(), **kw)
    r2 = svc.create_exercise("multiple_choice", "Different prompt?", _kps(), **kw)
    assert r1["exercise_id"] != r2["exercise_id"]
    assert len(svc.list_exercises()) == 2


# ----------------------------------------------------------------------
# list / get exercise
# ----------------------------------------------------------------------


def test_list_exercises_sorted_by_id():
    svc = _svc()
    for prompt in ["p1", "p2"]:
        svc.create_exercise(
            "true_false", prompt, _kps(), is_true=True
        )
    result = svc.list_exercises()
    ids = [e["exercise_id"] for e in result]
    assert ids == sorted(ids)
    assert len(result) == 2


def test_get_exercise_not_found():
    svc = _svc()
    with pytest.raises(NotFoundError):
        svc.get_exercise("exercise-missing")


def test_get_exercise_requires_nonempty_id():
    svc = _svc()
    with pytest.raises(InvalidInputError):
        svc.get_exercise("")


# ----------------------------------------------------------------------
# submit_answer / evaluation
# ----------------------------------------------------------------------


def _make_mc_exercise(svc: LearningService) -> str:
    result = svc.create_exercise(
        "multiple_choice",
        "Capital of Spain?",
        _kps(),
        choices=[{"choice_id": "a", "text": "Madrid"}, {"choice_id": "b", "text": "Lisbon"}],
        correct_choice_id="a",
    )
    return result["exercise_id"]


def test_submit_answer_requires_known_student():
    svc = _svc()
    svc.create_exercise("true_false", "p", _kps(), is_true=True)
    exercise_id = svc.list_exercises()[0]["exercise_id"]
    with pytest.raises(NotFoundError):
        svc.submit_answer("missing", exercise_id, "true")


def test_submit_answer_requires_known_exercise():
    svc = _svc()
    svc.create_student("s1")
    with pytest.raises(NotFoundError):
        svc.submit_answer("s1", "exercise-missing", "true")


def test_submit_answer_requires_string_value():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = _make_mc_exercise(svc)
    with pytest.raises(InvalidInputError):
        svc.submit_answer("s1", exercise_id, submitted_value=123)  # type: ignore[arg-type]


def test_submit_answer_requires_nonnegative_integer_sequence():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = _make_mc_exercise(svc)
    with pytest.raises(InvalidInputError):
        svc.submit_answer("s1", exercise_id, "a", sequence=-1)


def test_submit_answer_correct_multiple_choice():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = _make_mc_exercise(svc)
    result = svc.submit_answer("s1", exercise_id, "a")
    assert result["submitted_value"] == "a"
    assert result["evaluation_status"] == "correct"


def test_submit_answer_incorrect_multiple_choice():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = _make_mc_exercise(svc)
    result = svc.submit_answer("s1", exercise_id, "b")
    assert result["evaluation_status"] == "incorrect"


def test_submit_answer_is_idempotent_for_same_input():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = _make_mc_exercise(svc)
    r1 = svc.submit_answer("s1", exercise_id, "a")
    r2 = svc.submit_answer("s1", exercise_id, "a")
    assert r1["answer_id"] == r2["answer_id"]
    assert r1["evaluation_id"] == r2["evaluation_id"]


def test_submit_answer_different_sequences_create_different_answers():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = _make_mc_exercise(svc)
    r1 = svc.submit_answer("s1", exercise_id, "a", sequence=0)
    r2 = svc.submit_answer("s1", exercise_id, "a", sequence=1)
    assert r1["answer_id"] != r2["answer_id"]


def test_get_evaluation_returns_stored_result():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = _make_mc_exercise(svc)
    result = svc.submit_answer("s1", exercise_id, "a")
    evaluation = svc.get_evaluation(result["answer_id"])
    assert evaluation["status"] == "correct"
    assert evaluation["answer_id"] == result["answer_id"]


def test_get_evaluation_not_found():
    svc = _svc()
    with pytest.raises(NotFoundError):
        svc.get_evaluation("answer-missing")


def test_true_false_evaluation_correct():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = svc.create_exercise(
        "true_false", "Lisbon is the capital of Spain.", _kps(), is_true=False
    )["exercise_id"]
    result = svc.submit_answer("s1", exercise_id, "false")
    assert result["evaluation_status"] == "correct"


def test_short_answer_evaluation_correct():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = svc.create_exercise(
        "short_answer", "Capital of France?", _kps(), expected_answer="Paris"
    )["exercise_id"]
    result = svc.submit_answer("s1", exercise_id, "Paris")
    assert result["evaluation_status"] == "correct"


def test_short_answer_non_exact_wrong_answer_is_unsupported():
    # 按 spec 32.8, 非完全匹配的短答案一律 UNSUPPORTED, 不做语义判分
    svc = _svc()
    svc.create_student("s1")
    exercise_id = svc.create_exercise(
        "short_answer", "Capital of France?", _kps(), expected_answer="Paris"
    )["exercise_id"]
    result = svc.submit_answer("s1", exercise_id, "Rome")
    assert result["evaluation_status"] == "unsupported"


def test_fill_blank_evaluation_accepts_multiple_answers():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = svc.create_exercise(
        "fill_blank", "Capital of Spain is ____.", _kps(),
        blank_id="b1", accepted_answers=["Madrid", "madrid"],
    )["exercise_id"]
    assert svc.submit_answer("s1", exercise_id, "Madrid")["evaluation_status"] == "correct"
    assert svc.submit_answer("s1", exercise_id, "Lisbon")["evaluation_status"] == "incorrect"


def test_submit_answer_deterministic_across_services():
    def run():
        svc = _svc()
        svc.create_student("s1")
        exercise_id = _make_mc_exercise(svc)
        return svc.submit_answer("s1", exercise_id, "a")

    assert run()["answer_id"] == run()["answer_id"]


# ----------------------------------------------------------------------
# get_learning_status / get_study_plan / get_learning_path
# ----------------------------------------------------------------------


def test_get_learning_status_reports_exercise_and_answer_counts():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = _make_mc_exercise(svc)
    svc.submit_answer("s1", exercise_id, "a")
    status = svc.get_learning_status("s1")
    assert status["course_id"] == COURSE_ID
    assert status["registered_knowledge_points"] == _kps()
    assert "answered_count" in status


def test_get_learning_status_requires_known_student():
    svc = _svc()
    with pytest.raises(NotFoundError):
        svc.get_learning_status("missing")


def test_get_study_plan_returns_dto():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = _make_mc_exercise(svc)
    svc.submit_answer("s1", exercise_id, "a")
    plan = svc.get_study_plan("s1")
    assert isinstance(plan, dict)
    assert plan.get("student_id") == "s1" or "plan_id" in plan or "course_id" in plan


def test_get_study_plan_requires_known_student():
    svc = _svc()
    with pytest.raises(NotFoundError):
        svc.get_study_plan("missing")


def test_get_learning_path_for_unknown_kp_is_deterministic():
    # 先注册 KP (使 planner 有可用 KP), 再查询未注册 KP 的学习路径
    svc = _svc()
    svc.create_exercise(
        "true_false", "p", _kps(), is_true=True
    )
    result = svc.get_learning_path("unknown-kp")
    assert result["status"] == "unknown_knowledge_point"


def test_get_learning_path_requires_known_kp_with_exercise():
    # 有 KP 注册后, 查询已注册 KP 的学习路径 -> ok
    svc = _svc()
    svc.create_exercise("true_false", "p", _kps(), is_true=True)
    result = svc.get_learning_path("kp-1")
    assert result["status"] == "ok"

