# -*- coding: utf-8 -*-
"""答案历史回归测试 —— "追加式日志绝不可被重建" 缺陷类。

背景 (Task 41 交付后发现, Task 42 检查阶段定位)
--------------------------------------------------------------------

``LearningService.submit_answer`` 曾经每次提交都执行::

    self._answer_log = AnswerEvaluationLog(evaluator)   # 重建 -> 清空历史
    self._answer_log.record_answer(answer)

``AnswerEvaluationLog`` 在设计上 (Task 32) 是**追加式、幂等、按学生隔离**的
历史; 整体重建会让"第二次提交"抹掉第一次提交的答案与评估。实测: 学生连续
正确作答 3 道题后, ``answer_log_for`` 只返回 1 条, ``answered_count`` 报 1。

这是本项目最典型的一类缺陷 —— **能力存在, 但从未被接线**:
Task 32 的历史能力被 Task 40 的调用方抹掉了, 而单看 DTO 字段是否存在
(``200 + 字段存在``) 完全发现不了。

因此本文件不仅断言"数值正确", 还断言**结构上不允许重建**:
``test_learning_service_never_rebuilds_the_answer_log`` 直接检查源码,
确保 ``AnswerEvaluationLog(`` 只在 ``__init__`` 中出现一次。

另一半: ``ExactEvaluator`` 会**快照**练习目录 (构造时复制 dict), 所以
长期存活的日志必须能刷新 evaluator (``set_evaluator``), 否则"先提交、
后新建练习"的顺序会让新练习无法评估。
"""

import inspect

import pytest

from src.answer_evaluation import (
    AnswerEvaluationLog,
    ExactEvaluator,
    EvaluationStatus,
    StudentAnswer,
)
from src.application.errors import NotFoundError
from src.application.learning_service import LearningService


COURSE_ID = "course-history"


def _svc() -> LearningService:
    return LearningService(COURSE_ID)


def _mc(svc: LearningService, prompt: str, correct: str = "a", kp: str = "kp-1") -> str:
    """建一道四选一题, 返回 exercise_id。"""
    return svc.create_exercise(
        "multiple_choice",
        prompt,
        [kp],
        choices=[
            {"choice_id": "a", "text": "Madrid"},
            {"choice_id": "b", "text": "Lisbon"},
        ],
        correct_choice_id=correct,
    )["exercise_id"]


def _three_exercises(svc: LearningService) -> list:
    return [_mc(svc, f"Q{i}") for i in range(1, 4)]


# ----------------------------------------------------------------------
# 1) 历史必须累积 (缺陷的直接回归)
# ----------------------------------------------------------------------


def test_three_answers_are_all_retained():
    svc = _svc()
    svc.create_student("s1")
    for exercise_id in _three_exercises(svc):
        svc.submit_answer("s1", exercise_id, "a")

    assert len(svc.answer_log_for("s1")) == 3


def test_answered_count_counts_every_answer():
    svc = _svc()
    svc.create_student("s1")
    for exercise_id in _three_exercises(svc):
        svc.submit_answer("s1", exercise_id, "a")

    assert svc.get_learning_status("s1")["answered_count"] == 3


def test_answered_count_grows_monotonically():
    svc = _svc()
    svc.create_student("s1")
    seen = []
    for exercise_id in _three_exercises(svc):
        svc.submit_answer("s1", exercise_id, "a")
        seen.append(svc.get_learning_status("s1")["answered_count"])
    assert seen == [1, 2, 3]


def test_answer_ids_are_distinct_across_exercises():
    svc = _svc()
    svc.create_student("s1")
    for exercise_id in _three_exercises(svc):
        svc.submit_answer("s1", exercise_id, "a")

    ids = [a["answer_id"] for a in svc.answer_log_for("s1")]
    assert len(ids) == len(set(ids)) == 3


def test_answer_log_is_in_stable_order():
    svc = _svc()
    svc.create_student("s1")
    for exercise_id in _three_exercises(svc):
        svc.submit_answer("s1", exercise_id, "a")

    ids = [a["answer_id"] for a in svc.answer_log_for("s1")]
    assert ids == sorted(ids)


# ----------------------------------------------------------------------
# 2) 幂等: 同一次作答重提不产生新历史
# ----------------------------------------------------------------------


def test_resubmitting_the_same_value_is_idempotent():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = _mc(svc, "Q1")

    first = svc.submit_answer("s1", exercise_id, "a")
    second = svc.submit_answer("s1", exercise_id, "a")

    assert first["answer_id"] == second["answer_id"]
    assert len(svc.answer_log_for("s1")) == 1
    assert svc.get_learning_status("s1")["answered_count"] == 1


def test_resubmitting_the_same_value_keeps_the_first_submitted_at():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = _mc(svc, "Q1")

    first = svc.submit_answer("s1", exercise_id, "a")
    second = svc.submit_answer("s1", exercise_id, "a")

    assert first["submitted_at"] == second["submitted_at"]
    assert svc.submitted_at_for(first["answer_id"]) == first["submitted_at"]


def test_resubmitting_does_not_duplicate_evaluations():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = _mc(svc, "Q1")

    svc.submit_answer("s1", exercise_id, "a")
    svc.submit_answer("s1", exercise_id, "a")

    assert len(svc.answer_log_for("s1")) == 1
    log = svc._answer_log
    assert len(log.results_for("s1")) == 1


def test_a_different_value_for_the_same_exercise_appends_a_new_answer():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = _mc(svc, "Q1")

    svc.submit_answer("s1", exercise_id, "a")
    svc.submit_answer("s1", exercise_id, "b")

    assert len(svc.answer_log_for("s1")) == 2


# ----------------------------------------------------------------------
# 3) 评估历史: 早先的评估在后续提交之后依然可取
# ----------------------------------------------------------------------


def test_earlier_evaluation_is_still_available_after_later_submissions():
    svc = _svc()
    svc.create_student("s1")
    exercise_ids = _three_exercises(svc)

    first = svc.submit_answer("s1", exercise_ids[0], "a")
    for exercise_id in exercise_ids[1:]:
        svc.submit_answer("s1", exercise_id, "a")

    result = svc.get_evaluation(first["answer_id"])
    assert result["status"] == "correct"
    assert result["answer_id"] == first["answer_id"]


def test_evaluation_status_is_retained_per_answer():
    svc = _svc()
    svc.create_student("s1")
    exercise_ids = _three_exercises(svc)

    svc.submit_answer("s1", exercise_ids[0], "a")   # correct
    svc.submit_answer("s1", exercise_ids[1], "b")   # incorrect
    svc.submit_answer("s1", exercise_ids[2], "a")   # correct

    statuses = [
        svc.get_evaluation(a["answer_id"])["status"]
        for a in svc.answer_log_for("s1")
    ]
    assert statuses == ["correct", "incorrect", "correct"]


def test_results_for_returns_every_evaluation():
    svc = _svc()
    svc.create_student("s1")
    for exercise_id in _three_exercises(svc):
        svc.submit_answer("s1", exercise_id, "a")

    assert len(svc._answer_log.results_for("s1")) == 3


# ----------------------------------------------------------------------
# 4) evaluator 刷新: "先提交, 后建练习" 必须仍然可评估
# ----------------------------------------------------------------------


def test_exercise_created_after_history_still_evaluates():
    """ExactEvaluator 会快照目录 -> 日志必须能刷新 evaluator。"""
    svc = _svc()
    svc.create_student("s1")

    first_exercise = _mc(svc, "Q1")
    svc.submit_answer("s1", first_exercise, "a")

    # 之后才新建的练习
    later_exercise = _mc(svc, "Q2")
    result = svc.submit_answer("s1", later_exercise, "a")

    assert result["evaluation_status"] == "correct"
    assert len(svc.answer_log_for("s1")) == 2


def test_set_evaluator_preserves_history():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = _mc(svc, "Q1")
    svc.submit_answer("s1", exercise_id, "a")

    log = svc._answer_log
    before = [a.answer_id for a in log.answers_for_all()]

    log.set_evaluator(ExactEvaluator({exercise_id: svc._exercises[exercise_id]}))

    assert [a.answer_id for a in log.answers_for_all()] == before


def test_set_evaluator_makes_a_new_exercise_gradeable():
    svc = _svc()
    svc.create_student("s1")
    first_exercise = _mc(svc, "Q1")
    svc.submit_answer("s1", first_exercise, "a")

    later_exercise = _mc(svc, "Q2")
    log = svc._answer_log
    log.set_evaluator(ExactEvaluator(svc._exercises))

    answer = StudentAnswer.create("s1", later_exercise, "a", 0)
    assert log.evaluate(answer).status is EvaluationStatus.CORRECT


def test_answer_log_class_keeps_history_across_evaluator_swaps():
    """领域层单测: 刷新 evaluator 不丢答案。"""
    log = AnswerEvaluationLog(ExactEvaluator({}))
    log.set_evaluator(ExactEvaluator({"ex-1": object()}))
    log.set_evaluator(ExactEvaluator({}))
    # 未记录过答案 -> 仍然为空; 关键是没有因刷新而被清空或抛错
    assert log.answers_for_all() == ()


# ----------------------------------------------------------------------
# 5) 学生隔离
# ----------------------------------------------------------------------


def test_two_students_do_not_share_history():
    svc = _svc()
    svc.create_student("s1")
    svc.create_student("s2")
    exercise_id = _mc(svc, "Q1")

    svc.submit_answer("s1", exercise_id, "a")
    svc.submit_answer("s2", exercise_id, "b")

    assert len(svc.answer_log_for("s1")) == 1
    assert len(svc.answer_log_for("s2")) == 1
    assert svc.answer_log_for("s1")[0]["answer_id"] != svc.answer_log_for("s2")[0]["answer_id"]


def test_answer_log_for_unknown_student_raises():
    svc = _svc()
    with pytest.raises(NotFoundError):
        svc.answer_log_for("nobody")


# ----------------------------------------------------------------------
# 6) 学习闭环不受影响 (答案历史绝不写知识库)
# ----------------------------------------------------------------------


def test_answer_history_never_touches_the_knowledge_base():
    svc = _svc()
    svc.create_student("s1")
    for exercise_id in _three_exercises(svc):
        svc.submit_answer("s1", exercise_id, "a")

    # LearningService 没有 KnowledgePoint 写入口: 状态只有学生维度
    state = svc.get_student_state("s1")
    assert state["student_id"] == "s1"
    assert "knowledge_points" not in state


def test_practice_counts_match_the_number_of_submissions():
    svc = _svc()
    svc.create_student("s1")
    for exercise_id in _three_exercises(svc):
        svc.submit_answer("s1", exercise_id, "a")

    status = svc.get_learning_status("s1")
    assert status["practice_counts"]["kp-1"] == 3


def test_wrong_answers_accumulate_in_recent_incorrect():
    svc = _svc()
    svc.create_student("s1")
    exercise_id = _mc(svc, "Q1")
    svc.submit_answer("s1", exercise_id, "b")   # incorrect

    status = svc.get_learning_status("s1")
    assert "kp-1" in status["recent_incorrect_kps"]


# ----------------------------------------------------------------------
# 7) 结构守卫: 禁止再次整体重建答案日志
# ----------------------------------------------------------------------


def test_learning_service_never_rebuilds_the_answer_log():
    """``AnswerEvaluationLog(`` 只能出现一次 —— 在 __init__ 里。

    这是对缺陷本身的守卫: 只要有人在 submit_answer 里重建日志,
    本测试立即失败。
    """
    from src.application import learning_service as mod

    source = inspect.getsource(mod)
    assert source.count("AnswerEvaluationLog(") == 1, (
        "LearningService 只能构造一次 AnswerEvaluationLog (在 __init__); "
        "在提交路径上重建会清空全部作答历史。"
    )


def test_submit_path_only_refreshes_the_evaluator():
    from src.application import learning_service as mod

    source = inspect.getsource(mod)
    assert "set_evaluator" in source, (
        "提交路径必须通过 set_evaluator 刷新 evaluator, 而不是重建日志"
    )


def test_answer_log_class_exposes_set_evaluator():
    assert hasattr(AnswerEvaluationLog, "set_evaluator")
    assert callable(AnswerEvaluationLog.set_evaluator)


def test_set_evaluator_is_documented_as_history_preserving():
    doc = inspect.getdoc(AnswerEvaluationLog.set_evaluator) or ""
    assert "history" in doc.lower()
