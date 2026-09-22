"""Tests for the exercise layer (Task 31)."""

from __future__ import annotations

import pytest

from src.exercises import (
    EXERCISE_SCHEMA_VERSION,
    Choice,
    CourseExerciseValidator,
    ExplicitExerciseBuilder,
    Exercise,
    ExerciseErrorCode,
    ExerciseGenerator,
    ExerciseType,
    ExerciseValidationError,
    FillBlank,
)

REGISTRY = {
    "kp-a": ("ev-1", "ev-2"),
    "kp-b": ("ev-3",),
}


def build_mcq(validator=None, **kw):
    defaults = dict(
        prompt="¿Cuál es la raíz de 16?",
        knowledge_point_ids=("kp-a",),
        choices=(
            Choice("a", "2"),
            Choice("b", "4"),
            Choice("c", "8"),
        ),
        correct_choice_id="b",
    )
    defaults.update(kw)
    return ExplicitExerciseBuilder("course-1", validator).build_multiple_choice(
        **defaults
    )


class TestMultipleChoice:
    def test_valid_mcq(self):
        ex = build_mcq()
        assert ex.exercise_type is ExerciseType.MULTIPLE_CHOICE
        assert ex.course_id == "course-1"
        assert ex.knowledge_point_ids == ("kp-a",)
        assert [c.choice_id for c in ex.choices] == ["a", "b", "c"]

    def test_minimal_two_choices(self):
        ex = build_mcq(
            choices=(Choice("a", "uno"), Choice("b", "dos")),
            correct_choice_id="a",
        )
        assert len(ex.choices) == 2

    def test_fewer_than_two_choices_rejected(self):
        with pytest.raises(ExerciseValidationError) as exc:
            build_mcq(
                choices=(Choice("a", "uno"),),
                correct_choice_id="a",
            )
        assert exc.value.code is ExerciseErrorCode.INVALID_CHOICES

    def test_no_choices_rejected(self):
        with pytest.raises(ExerciseValidationError) as exc:
            build_mcq(choices=(), correct_choice_id="a")
        assert exc.value.code is ExerciseErrorCode.INVALID_CHOICES

    def test_duplicate_choice_id_rejected(self):
        with pytest.raises(ExerciseValidationError) as exc:
            build_mcq(
                choices=(
                    Choice("a", "x"),
                    Choice("a", "y"),
                ),
                correct_choice_id="a",
            )
        assert exc.value.code is ExerciseErrorCode.DUPLICATE_CHOICE_ID

    def test_missing_correct_choice_rejected(self):
        with pytest.raises(ExerciseValidationError) as exc:
            build_mcq(correct_choice_id="z")
        assert exc.value.code is ExerciseErrorCode.INVALID_CORRECT_CHOICE

    def test_empty_prompt_rejected(self):
        with pytest.raises(ExerciseValidationError) as exc:
            build_mcq(prompt="   ")
        assert exc.value.code is ExerciseErrorCode.INVALID_INPUT

    def test_missing_knowledge_point_rejected(self):
        with pytest.raises(ExerciseValidationError) as exc:
            build_mcq(knowledge_point_ids=())
        assert exc.value.code is ExerciseErrorCode.MISSING_KNOWLEDGE_POINT

class TestTrueFalse:
    def test_true_answer(self):
        ex = ExplicitExerciseBuilder("course-1").build_true_false(
            prompt="La derivada de x^2 es 2x.",
            knowledge_point_ids=("kp-b",),
            is_true=True,
        )
        assert ex.exercise_type is ExerciseType.TRUE_FALSE
        assert ex.correct_choice_id == "true"
        assert [c.choice_id for c in ex.choices] == ["true", "false"]
        assert all(c.text for c in ex.choices)

    def test_false_answer(self):
        ex = ExplicitExerciseBuilder("course-1").build_true_false(
            prompt="2+2=5.",
            knowledge_point_ids=("kp-b",),
            is_true=False,
        )
        assert ex.correct_choice_id == "false"

    def test_missing_correct_choice_rejected(self):
        with pytest.raises(ExerciseValidationError) as exc:
            Exercise.create(
                "course-1",
                ExerciseType.TRUE_FALSE,
                "p",
                ("kp-a",),
            )
        assert exc.value.code is ExerciseErrorCode.INVALID_CORRECT_CHOICE


class TestShortAnswer:
    def test_valid_short_answer(self):
        ex = ExplicitExerciseBuilder("course-1").build_short_answer(
            prompt="Nombre de la capital de España.",
            knowledge_point_ids=("kp-a",),
            expected_answer="Madrid",
            explanation="Capital oficial.",
        )
        assert ex.expected_answer == "Madrid"
        assert ex.explanation == "Capital oficial."
        assert ex.choices == ()

    def test_empty_expected_answer_rejected(self):
        with pytest.raises(ExerciseValidationError) as exc:
            ExplicitExerciseBuilder("course-1").build_short_answer(
                prompt="p",
                knowledge_point_ids=("kp-a",),
                expected_answer="   ",
            )
        assert exc.value.code is ExerciseErrorCode.INVALID_INPUT


class TestFillBlank:
    def test_valid_fill_blank(self):
        ex = ExplicitExerciseBuilder("course-1").build_fill_blank(
            prompt="La fórmula de la base es ___",
            knowledge_point_ids=("kp-a", "kp-b"),
            blank_id="b1",
            accepted_answers=["a^2 + b^2", "b^2 + a^2"],
        )
        assert ex.fill_blank is not None
        assert ex.fill_blank.blank_id == "b1"
        assert ex.fill_blank.accepted_answers == ("a^2 + b^2", "b^2 + a^2")

    def test_fill_blank_without_payload_rejected(self):
        with pytest.raises(ExerciseValidationError) as exc:
            Exercise.create(
                "course-1",
                ExerciseType.FILL_BLANK,
                "p",
                ("kp-a",),
            )
        assert exc.value.code is ExerciseErrorCode.INVALID_INPUT

    def test_fill_blank_without_answers_rejected(self):
        with pytest.raises(ExerciseValidationError) as exc:
            Exercise.create(
                "course-1",
                ExerciseType.FILL_BLANK,
                "p",
                ("kp-a",),
                fill_blank=FillBlank("b1", ()),
            )
        assert exc.value.code is ExerciseErrorCode.INVALID_INPUT


class TestValidator:
    def test_valid_references_pass(self):
        validator = CourseExerciseValidator(REGISTRY)
        ex = build_mcq(evidence_ids=("ev-1", "ev-2"))
        assert validator.validate(ex) is ex

    def test_missing_knowledge_point_rejected(self):
        validator = CourseExerciseValidator(REGISTRY)
        ex = build_mcq(knowledge_point_ids=("kp-x",), evidence_ids=("ev-1",))
        with pytest.raises(ExerciseValidationError) as exc:
            validator.validate(ex)
        assert exc.value.code is ExerciseErrorCode.MISSING_KNOWLEDGE_POINT

    def test_cross_kp_evidence_rejected_strict(self):
        validator = CourseExerciseValidator(REGISTRY)
        ex = build_mcq(evidence_ids=("ev-3",))
        with pytest.raises(ExerciseValidationError) as exc:
            validator.validate(ex)
        assert exc.value.code is ExerciseErrorCode.CROSS_KP_EVIDENCE

    def test_permissive_mode_allows_cross_kp(self):
        validator = CourseExerciseValidator(REGISTRY, allow_cross_kp=True)
        ex = build_mcq(evidence_ids=("ev-3",))
        assert validator.validate(ex) is ex

    def test_permissive_mode_still_rejects_unknown_evidence(self):
        validator = CourseExerciseValidator(REGISTRY, allow_cross_kp=True)
        ex = build_mcq(evidence_ids=("ev-999",))
        with pytest.raises(ExerciseValidationError) as exc:
            validator.validate(ex)
        assert exc.value.code is ExerciseErrorCode.MISSING_EVIDENCE

    def test_builder_applies_validator(self):
        validator = CourseExerciseValidator(REGISTRY)
        builder = ExplicitExerciseBuilder("course-1", validator)
        ex = builder.build_multiple_choice(
            "p",
            ("kp-a", "kp-b"),
            (Choice("a", "x"), Choice("b", "y")),
            "a",
            evidence_ids=("ev-1", "ev-3"),
        )
        assert ex.evidence_ids == ("ev-1", "ev-3")

    def test_builder_raises_for_bad_references(self):
        validator = CourseExerciseValidator(REGISTRY)
        builder = ExplicitExerciseBuilder("course-1", validator)
        with pytest.raises(ExerciseValidationError):
            builder.build_short_answer(
                "p", ("kp-x",), "ans", evidence_ids=("ev-1",)
            )


class TestDeterminismAndIds:
    def test_same_content_same_id(self):
        ex1 = build_mcq()
        ex2 = build_mcq()
        assert ex1.exercise_id == ex2.exercise_id
        assert ex1.exercise_id.startswith("exercise-")
        assert len(ex1.exercise_id.split("-", 1)[1]) == 24

    def test_different_content_different_id(self):
        ex1 = build_mcq()
        ex2 = build_mcq(prompt="Otra pregunta.")
        assert ex1.exercise_id != ex2.exercise_id

    def test_kp_and_evidence_order_independent(self):
        ex1 = build_mcq(knowledge_point_ids=("kp-a", "kp-b"), evidence_ids=("ev-1", "ev-3"))
        ex2 = build_mcq(knowledge_point_ids=("kp-b", "kp-a"), evidence_ids=("ev-3", "ev-1"))
        assert ex1.exercise_id == ex2.exercise_id

    def test_difficulty_range_validation(self):
        for value in (0, 6, -1, 1.5, True):
            with pytest.raises(ExerciseValidationError):
                build_mcq(difficulty=value)
        ex = build_mcq(difficulty=3)
        assert ex.difficulty == 3
        assert build_mcq().difficulty is None

    def test_unknown_exercise_type_rejected(self):
        with pytest.raises(ExerciseValidationError) as exc:
            Exercise.create("course-1", "essay", "p", ("kp-a",))
        assert exc.value.code is ExerciseErrorCode.UNKNOWN_EXERCISE_TYPE

    def test_empty_course_id_rejected(self):
        with pytest.raises(ExerciseValidationError) as exc:
            Exercise.create("", ExerciseType.SHORT_ANSWER, "p", ("kp-a",), expected_answer="x")
        assert exc.value.code is ExerciseErrorCode.INVALID_INPUT


class TestSerialization:
    def test_round_trip(self):
        ex = build_mcq(evidence_ids=("ev-1",), explanation="exp", difficulty=2)
        data = ex.to_dict()
        assert data["schema_version"] == EXERCISE_SCHEMA_VERSION
        rebuilt = Exercise.from_dict(data)
        assert rebuilt == ex

    def test_round_trip_fill_blank(self):
        ex = ExplicitExerciseBuilder("course-1").build_fill_blank(
            "p", ("kp-a",), "b1", ["x", "y"]
        )
        assert Exercise.from_dict(ex.to_dict()) == ex

    def test_tampered_payload_rejected(self):
        ex = build_mcq()
        data = ex.to_dict()
        data["prompt"] = "modificada"
        with pytest.raises(ExerciseValidationError) as exc:
            Exercise.from_dict(data)
        assert exc.value.code is ExerciseErrorCode.INVALID_STATE

    def test_unknown_schema_version_rejected(self):
        data = build_mcq().to_dict()
        data["schema_version"] = 99
        with pytest.raises(Exception) as exc:
            Exercise.from_dict(data)
        assert exc.value.code is ExerciseErrorCode.INVALID_SCHEMA_VERSION

    def test_unknown_exercise_type_in_payload(self):
        data = build_mcq().to_dict()
        data["exercise_type"] = "essay"
        with pytest.raises(ExerciseValidationError) as exc:
            Exercise.from_dict(data)
        assert exc.value.code is ExerciseErrorCode.UNKNOWN_EXERCISE_TYPE


class TestMultilingual:
    def test_spanish_catalan_chinese_unicode_preserved(self):
        ex = ExplicitExerciseBuilder("course-x").build_short_answer(
            prompt="Quina és la capital de Catalunya?",
            knowledge_point_ids=("kp-a",),
            expected_answer="加泰罗尼亚的首府是巴塞罗那",
            explanation="Cap de la Generalitat.",
        )
        data = ex.to_dict()
        assert data["prompt"].startswith("Quina")
        assert "巴塞罗那" in data["expected_answer"]
        assert Exercise.from_dict(data) == ex

    def test_choices_unicode(self):
        ex = build_mcq(
            prompt="¿Qué es un 'gràfic'?",
            choices=(
                Choice("a", "Un gràfic de funcions"),
                Choice("b", "表（中文）"),
            ),
            correct_choice_id="a",
        )
        texts = [c.text for c in ex.choices]
        assert "gràfic" in texts[0]
        assert "表（中文）" in texts[1]


class TestImmutability:
    def test_exercise_is_frozen(self):
        ex = build_mcq()
        with pytest.raises(AttributeError):
            ex.prompt = "changed"

    def test_choice_is_frozen(self):
        choice = Choice("a", "x")
        with pytest.raises(AttributeError):
            choice.text = "y"

    def test_fill_blank_is_frozen(self):
        fb = FillBlank("b1", ("x",))
        with pytest.raises(AttributeError):
            fb.accepted_answers = ("y",)


class TestNoAutoGeneration:
    def test_generator_not_implemented(self):
        with pytest.raises(NotImplementedError):
            ExerciseGenerator().generate()
