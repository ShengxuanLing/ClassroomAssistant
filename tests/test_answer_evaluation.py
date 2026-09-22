"""Tests for the deterministic answer evaluation layer (Task 32)."""

from __future__ import annotations

import pytest

from src.answer_evaluation import (
    EVALUATOR_VERSION,
    EVALUATION_SCHEMA_VERSION,
    AnswerEvaluationLog,
    EvaluationErrorCode,
    EvaluationResult,
    EvaluationStatus,
    EvaluationValidationError,
    ExactEvaluator,
    StudentAnswer,
)
from src.exercises import Choice, ExerciseType, ExplicitExerciseBuilder, Exercise


def _builder():
    return ExplicitExerciseBuilder("math-101")


def _mcq():
    ex = _builder().build_multiple_choice(
        "¿Cuál es 2+2?",
        ("kp-math",),
        (Choice("a", "3"), Choice("b", "4"), Choice("c", "5")),
        "b",
    )
    return ex


def _tf():
    return _builder().build_true_false("El agua hierve a 100°C.", ("kp-physics",), True)


def _fb():
    return _builder().build_fill_blank(
        "La fórmula es ___",
        ("kp-formula",),
        "blank-1",
        ["E=mc^2"],
    )


def _sa():
    return _builder().build_short_answer(
        "Capital de España:",
        ("kp-geo",),
        "Madrid",
    )


ALL_EXERCISES = {
    _mcq().exercise_id: _mcq(),
    _tf().exercise_id: _tf(),
    _fb().exercise_id: _fb(),
    _sa().exercise_id: _sa(),
}


def _evaluator(exercises=None):
    return ExactEvaluator(exercises if exercises is not None else ALL_EXERCISES)


def _answer(exercise_id, value, seq=0, student="stu-1"):
    return StudentAnswer.create(student, exercise_id, value, seq)


class TestMultipleChoice:
    def test_correct(self):
        ex = _mcq()
        res = _evaluator().evaluate(_answer(ex.exercise_id, "b"))
        assert res.status is EvaluationStatus.CORRECT
        assert res.score == 1.0

    def test_incorrect(self):
        ex = _mcq()
        res = _evaluator().evaluate(_answer(ex.exercise_id, "a"))
        assert res.status is EvaluationStatus.INCORRECT
        assert res.score == 0.0

    def test_invalid_choice_unsupported(self):
        ex = _mcq()
        res = _evaluator().evaluate(_answer(ex.exercise_id, "z"))
        assert res.status is EvaluationStatus.UNSUPPORTED
        assert res.score == 0.0


class TestTrueFalse:
    def test_true_correct(self):
        ex = _tf()
        res = _evaluator().evaluate(_answer(ex.exercise_id, "true"))
        assert res.status is EvaluationStatus.CORRECT

    def test_false_incorrect(self):
        ex = _tf()
        res = _evaluator().evaluate(_answer(ex.exercise_id, "false"))
        assert res.status is EvaluationStatus.INCORRECT

    def test_non_boolean_unsupported(self):
        ex = _tf()
        res = _evaluator().evaluate(_answer(ex.exercise_id, "quizas"))
        assert res.status is EvaluationStatus.UNSUPPORTED


class TestFillBlank:
    def test_exact_match_correct(self):
        ex = _fb()
        res = _evaluator().evaluate(_answer(ex.exercise_id, "E=mc^2"))
        assert res.status is EvaluationStatus.CORRECT

    def test_case_difference_incorrect(self):
        ex = _fb()
        res = _evaluator().evaluate(_answer(ex.exercise_id, "e=mc^2"))
        assert res.status is EvaluationStatus.INCORRECT

    def test_trimming_not_applied(self):
        ex = _fb()
        res = _evaluator().evaluate(_answer(ex.exercise_id, " E=mc^2"))
        assert res.status is EvaluationStatus.INCORRECT


class TestShortAnswer:
    def test_exact_match_correct(self):
        ex = _sa()
        res = _evaluator().evaluate(_answer(ex.exercise_id, "Madrid"))
        assert res.status is EvaluationStatus.CORRECT

    def test_semantic_equivalent_not_accepted(self):
        ex = _sa()
        res = _evaluator().evaluate(_answer(ex.exercise_id, "马德里"))
        assert res.status is EvaluationStatus.UNSUPPORTED

    def test_no_external_calls(self):
        import inspect
        import src.answer_evaluation as mod
        source = inspect.getsource(mod)
        assert "openai" not in source.lower()
        assert "http" not in source.lower()
        assert "requests" not in source.lower()
        assert "socket" not in source.lower()


class TestDeterministicIds:
    def test_answer_id_stable(self):
        a1 = _answer(_mcq().exercise_id, "b", 0, "stu-1")
        a2 = _answer(_mcq().exercise_id, "b", 0, "stu-1")
        assert a1.answer_id == a2.answer_id
        assert a1.answer_id.startswith("answer-")

    def test_answer_id_changes_with_value(self):
        ex = _mcq()
        a1 = _answer(ex.exercise_id, "b", 0)
        a2 = _answer(ex.exercise_id, "a", 0)
        assert a1.answer_id != a2.answer_id

    def test_evaluation_id_stable(self):
        ev = _evaluator()
        ex = _mcq()
        r1 = ev.evaluate(_answer(ex.exercise_id, "b"))
        r2 = ev.evaluate(_answer(ex.exercise_id, "b"))
        assert r1.evaluation_id == r2.evaluation_id
        assert r1.evaluation_id.startswith("evaluation-")


class TestDuplicates:
    def test_duplicate_answer_noop(self):
        log = AnswerEvaluationLog(_evaluator())
        ex = _mcq()
        ans = _answer(ex.exercise_id, "b")
        log.record_answer(ans)
        log.record_answer(ans)
        assert len(log.answers_for("stu-1")) == 1

    def test_duplicate_evaluation_noop(self):
        log = AnswerEvaluationLog(_evaluator())
        ex = _mcq()
        ans = _answer(ex.exercise_id, "b")
        r1 = log.evaluate(ans)
        r2 = log.evaluate(ans)
        assert r1.evaluation_id == r2.evaluation_id
        assert len(log.results_for("stu-1")) == 1


class TestStudentIsolation:
    def test_student_a_does_not_affect_b(self):
        log = AnswerEvaluationLog(_evaluator())
        ex = _mcq()
        log.evaluate(_answer(ex.exercise_id, "b", 0, "stu-a"))
        assert len(log.answers_for("stu-b")) == 0
        assert len(log.results_for("stu-b")) == 0


class TestExerciseIsolation:
    def test_unknown_exercise_rejected(self):
        with pytest.raises(EvaluationValidationError) as exc:
            _evaluator().evaluate(StudentAnswer.create("stu-1", "exercise-does-not-exist", "b", 0))
        assert exc.value.code is EvaluationErrorCode.UNKNOWN_EXERCISE


class TestEvaluatorVersion:
    def test_version_tagged(self):
        res = _evaluator().evaluate(_answer(_mcq().exercise_id, "b"))
        assert res.evaluator_version == EVALUATOR_VERSION

    def test_result_serialization_includes_version(self):
        res = _evaluator().evaluate(_answer(_mcq().exercise_id, "b"))
        d = res.to_dict()
        assert d["evaluator_version"] == EVALUATOR_VERSION
        assert d["schema_version"] == EVALUATION_SCHEMA_VERSION


class TestScoreRange:
    def test_scores_in_unit_interval(self):
        ev = _evaluator()
        ex = _mcq()
        for value in ("a", "b", "c"):
            res = ev.evaluate(_answer(ex.exercise_id, value))
            assert 0.0 <= res.score <= 1.0


class TestSerialization:
    def test_answer_round_trip(self):
        ans = _answer(_mcq().exercise_id, "b", 3)
        rebuilt = StudentAnswer.from_dict(ans.to_dict())
        assert rebuilt == ans

    def test_result_round_trip(self):
        res = _evaluator().evaluate(_answer(_mcq().exercise_id, "b"))
        rebuilt = EvaluationResult.from_dict(res.to_dict())
        assert rebuilt == res

    def test_log_round_trip(self):
        log = AnswerEvaluationLog(_evaluator())
        ex = _mcq()
        ans = _answer(ex.exercise_id, "b")
        log.evaluate(ans)
        ev2 = _evaluator()
        log2 = AnswerEvaluationLog.from_dict(log.to_dict(), ev2)
        assert log2.answers_for_all() == log.answers_for_all()
        assert log2.results_for_all() == log.results_for_all()


class TestStateEventIntegration:
    def test_answered_event_shape(self):
        log = AnswerEvaluationLog(_evaluator())
        ex = _mcq()
        ans = _answer(ex.exercise_id, "b", 0)
        res = log.evaluate(ans)
        ev = log.answered_event("math-101", "kp-math", res, 0)
        assert ev["event_type"] is not None
        assert ev["is_correct"] is True
        assert ev["student_id"] == "stu-1"

    def test_unsupported_is_correct_none(self):
        log = AnswerEvaluationLog(_evaluator())
        ex = _sa()
        ans = _answer(ex.exercise_id, "Paris", 0)
        res = log.evaluate(ans)
        ev = log.answered_event("math-101", "kp-geo", res, 0)
        assert ev["is_correct"] is None
