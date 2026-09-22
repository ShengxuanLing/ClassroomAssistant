"""Question / Exercise Layer (Task 31).

Deterministic, caller-supplied exercise model on top of the course
knowledge layer:

- ``Exercise`` is a frozen, content-addressed record: its id is
  ``"exercise-" + sha256(canonical contents)[:24]`` -- no
  creation time, no uuid.
- Every factual exercise is grounded: it links to at least one
  ``knowledge_point_id`` and (optionally) to ``evidence_ids`` that
  exist in the course.  Dangling references are rejected with a
  stable error.  Cross-KP evidence is *strictly rejected* by the
  default validator (documented in ``CourseExerciseValidator``);
  a caller can pass ``allow_cross_kp=True`` to opt into the
  permissive mode.
- All content (prompt, choices, answers, explanation) is
  caller-supplied: there is NO automatic generation.  An
  ``ExerciseGenerator`` interface is reserved but raises
  ``NotImplementedError``.

Exercise types (spec 31.3)
--------------------------
- MULTIPLE_CHOICE: ``Choice(choice_id, text)`` list, at least 2
  choices, unique choice ids, ``correct_choice_id`` must reference
  an existing choice.
- TRUE_FALSE: the answer is exactly ``True`` / ``False``.
- SHORT_ANSWER: stores an ``expected_answer``; this layer does NOT
  grade natural language (that is Task 32's evaluator, which
  returns UNSUPPORTED for free text).
- FILL_BLANK: stores ``blank_id`` + ``accepted_answers`` tuple;
  no fuzzy matching (Task 32 does exact comparison).

Constraints honored
------------------
- No LLM, no network, no auto-generation.
- Deterministic ids; sorted / deduped collections.
- frozen=True; to_dict / from_dict; schema_version = 1; unknown
  versions -> stable error.
- Languages (es / ca / zh) preserved verbatim; Unicode safe.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

__all__ = [
    "EXERCISE_SCHEMA_VERSION",
    "ExerciseErrorCode",
    "ExerciseError",
    "ExerciseValidationError",
    "ExerciseSchemaError",
    "ExerciseType",
    "Choice",
    "FillBlank",
    "Exercise",
    "ExplicitExerciseBuilder",
    "CourseExerciseValidator",
    "ExerciseGenerator",
]

EXERCISE_SCHEMA_VERSION = 1


class ExerciseErrorCode(str, Enum):
    """Stable error codes for the exercise layer."""

    INVALID_INPUT = "invalid_input"
    MISSING_KNOWLEDGE_POINT = "missing_knowledge_point"
    MISSING_EVIDENCE = "missing_evidence"
    CROSS_KP_EVIDENCE = "cross_kp_evidence"
    INVALID_CHOICES = "invalid_choices"
    UNKNOWN_EXERCISE_TYPE = "unknown_exercise_type"
    INVALID_CORRECT_CHOICE = "invalid_correct_choice"
    DUPLICATE_CHOICE_ID = "duplicate_choice_id"
    INVALID_SCHEMA_VERSION = "invalid_schema_version"
    INVALID_STATE = "invalid_state"


class ExerciseError(Exception):
    def __init__(self, code: ExerciseErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code.value}] {message}")


class ExerciseValidationError(ExerciseError):
    pass


class ExerciseSchemaError(ExerciseError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha24(payload: Any) -> str:
    if isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = _canonical_json(payload).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


class ExerciseType(str, Enum):
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"
    SHORT_ANSWER = "short_answer"
    FILL_BLANK = "fill_blank"


@dataclass(frozen=True)
class Choice:
    """One option of a multiple-choice question."""

    choice_id: str
    text: str

    def to_dict(self) -> Dict[str, Any]:
        return {"choice_id": self.choice_id, "text": self.text}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Choice":
        return cls(
            choice_id=str(data.get("choice_id") or ""),
            text=str(data.get("text") or ""),
        )


@dataclass(frozen=True)
class FillBlank:
    """Fill-blank payload: a blank id plus the exact accepted answers."""

    blank_id: str
    accepted_answers: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blank_id": self.blank_id,
            "accepted_answers": list(self.accepted_answers),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FillBlank":
        return cls(
            blank_id=str(data.get("blank_id") or ""),
            accepted_answers=tuple(str(a) for a in (data.get("accepted_answers") or ())),
        )


@dataclass(frozen=True)
class Exercise:
    """One deterministic, grounded exercise.

    Type-specific fields
    --------------------
    - MULTIPLE_CHOICE: ``choices`` (>= 2, unique ids) +
      ``correct_choice_id`` (must reference one of them).
    - TRUE_FALSE: ``correct_choice_id`` is ``"true"`` or
      ``"false"``; ``choices`` is a canonical two-option list.
    - SHORT_ANSWER: ``expected_answer`` holds the reference text
      (grading is Task 32's job; no NL auto-grading here).
    - FILL_BLANK: ``fill_blank`` holds ``FillBlank``.
    """

    exercise_id: str
    course_id: str
    knowledge_point_ids: Tuple[str, ...]
    exercise_type: ExerciseType
    prompt: str
    choices: Tuple[Choice, ...]
    correct_choice_id: Optional[str]
    expected_answer: Optional[str]
    fill_blank: Optional[FillBlank]
    explanation: Optional[str]
    evidence_ids: Tuple[str, ...]
    difficulty: Optional[int]

    @classmethod
    def create(
        cls,
        course_id: str,
        exercise_type: ExerciseType,
        prompt: str,
        knowledge_point_ids: Tuple[str, ...],
        *,
        choices: Optional[Tuple[Choice, ...]] = None,
        correct_choice_id: Optional[str] = None,
        expected_answer: Optional[str] = None,
        fill_blank: Optional[FillBlank] = None,
        explanation: Optional[str] = None,
        evidence_ids: Optional[Tuple[str, ...]] = None,
        difficulty: Optional[int] = None,
    ) -> "Exercise":
        cid = str(course_id or "").strip()
        if not cid:
            raise ExerciseValidationError(
                ExerciseErrorCode.INVALID_INPUT,
                "course_id must be non-empty",
            )
        if not isinstance(exercise_type, ExerciseType):
            raise ExerciseValidationError(
                ExerciseErrorCode.UNKNOWN_EXERCISE_TYPE,
                f"unknown exercise type: {exercise_type!r}",
            )
        prompt_text = str(prompt or "").strip()
        if not prompt_text:
            raise ExerciseValidationError(
                ExerciseErrorCode.INVALID_INPUT,
                "prompt must be non-empty",
            )
        kp_ids = tuple(sorted(set(str(k) for k in (knowledge_point_ids or ()) if str(k))))
        if not kp_ids:
            raise ExerciseValidationError(
                ExerciseErrorCode.MISSING_KNOWLEDGE_POINT,
                "at least one knowledge_point_id is required",
            )
        ev_ids = tuple(sorted(set(str(e) for e in (evidence_ids or ()) if str(e))))

        if difficulty is not None:
            if isinstance(difficulty, bool) or not isinstance(difficulty, int) or not 1 <= difficulty <= 5:
                raise ExerciseValidationError(
                    ExerciseErrorCode.INVALID_INPUT,
                    "difficulty must be an integer in 1..5 when provided",
                )

        choices_tuple: Tuple[Choice, ...] = tuple()
        if exercise_type is ExerciseType.MULTIPLE_CHOICE:
            choices_tuple = cls._validate_choices(choices, correct_choice_id)
        elif exercise_type is ExerciseType.TRUE_FALSE:
            if choices is not None:
                choices_tuple = cls._validate_choices(choices, correct_choice_id)
            else:
                if correct_choice_id not in ("true", "false"):
                    raise ExerciseValidationError(
                        ExerciseErrorCode.INVALID_CORRECT_CHOICE,
                        "true/false exercises require correct_choice_id of 'true' or 'false'",
                    )
                choices_tuple = (
                    Choice(choice_id="true", text="Vero / True"),
                    Choice(choice_id="false", text="Falso / False"),
                )
            if correct_choice_id is None:
                raise ExerciseValidationError(
                    ExerciseErrorCode.INVALID_CORRECT_CHOICE,
                    "true/false exercises require correct_choice_id",
                )
        elif exercise_type is ExerciseType.SHORT_ANSWER:
            if expected_answer is None or not str(expected_answer).strip():
                raise ExerciseValidationError(
                    ExerciseErrorCode.INVALID_INPUT,
                    "short_answer exercises require a non-empty expected_answer",
                )
        elif exercise_type is ExerciseType.FILL_BLANK:
            if fill_blank is None:
                raise ExerciseValidationError(
                    ExerciseErrorCode.INVALID_INPUT,
                    "fill_blank exercises require a FillBlank payload",
                )
            if not fill_blank.accepted_answers:
                raise ExerciseValidationError(
                    ExerciseErrorCode.INVALID_INPUT,
                    "FillBlank.accepted_answers must be non-empty",
                )

        content_payload = {
            "course_id": cid,
            "exercise_type": exercise_type.value,
            "prompt": prompt_text,
            "knowledge_point_ids": list(kp_ids),
            # 身份用**规范化**的选项顺序 (按 choice_id 排序), 而 ``choices_tuple``
            # 保持作者给的展示顺序。
            #
            # 为什么必须分开 (Task 51 发现的真实缺陷): 判断题的自动选项是
            # (true, false), 而显式传入的选项过去会被 ``_validate_choices``
            # 排序成 (false, true)。两条路径算出**两个不同的 exercise_id**,
            # 于是 "创建 -> 存库 -> 读回" 时 ``from_dict`` 认定
            # "exercise_id 与内容不符 (payload 损坏)" 而拒绝反序列化 ——
            # 判断题根本无法从数据库读回来。
            #
            # 分开之后: 身份仍然与选项顺序无关 (同一道题换个顺序不会变成两道),
            # 展示顺序也仍然是作者写下的顺序 (Vero 在 Falso 前面)。
            "choices": [
                c.to_dict() for c in sorted(choices_tuple, key=lambda c: c.choice_id)
            ],
            "correct_choice_id": correct_choice_id,
            "expected_answer": expected_answer,
            "fill_blank": fill_blank.to_dict() if fill_blank is not None else None,
            "explanation": explanation,
            "evidence_ids": list(ev_ids),
            "difficulty": difficulty,
        }
        exercise_id = "exercise-" + _sha24(content_payload)
        return cls(
            exercise_id=exercise_id,
            course_id=cid,
            knowledge_point_ids=kp_ids,
            exercise_type=exercise_type,
            prompt=prompt_text,
            choices=choices_tuple,
            correct_choice_id=correct_choice_id,
            expected_answer=expected_answer,
            fill_blank=fill_blank,
            explanation=explanation,
            evidence_ids=ev_ids,
            difficulty=difficulty,
        )

    @staticmethod
    def _validate_choices(
        choices: Optional[Tuple[Choice, ...]],
        correct_choice_id: Optional[str],
    ) -> Tuple[Choice, ...]:
        """校验选项并**保留作者给出的顺序**。

        为什么不再按 choice_id 排序: 选项顺序是题目的一部分 (判断题希望
        "Vero" 在前, 选择题希望 A/B/C/D 按作者排)。排序会让展示顺序取决于
        哈希字母表, 还会和自动生成的判断题选项 (true, false) 打架 —— 两条
        路径算出不同的 ``exercise_id``, 于是存进数据库的练习读不回来。

        "身份与选项顺序无关" 这条性质没有被放弃: 它现在由 ``content_payload``
        里的规范化选项列表保证 (见 :meth:`create`)。
        """
        if not choices or len(choices) < 2:
            raise ExerciseValidationError(
                ExerciseErrorCode.INVALID_CHOICES,
                "multiple-choice exercises require at least 2 choices",
            )
        seen: Dict[str, Choice] = {}
        ordered: list = []
        for choice in choices:
            if not isinstance(choice, Choice):
                raise ExerciseValidationError(
                    ExerciseErrorCode.INVALID_CHOICES,
                    "choices must be Choice instances",
                )
            if not choice.choice_id:
                raise ExerciseValidationError(
                    ExerciseErrorCode.INVALID_CHOICES,
                    "choice_id must be non-empty",
                )
            if choice.choice_id in seen:
                raise ExerciseValidationError(
                    ExerciseErrorCode.DUPLICATE_CHOICE_ID,
                    f"duplicate choice_id: {choice.choice_id!r}",
                )
            seen[choice.choice_id] = choice
            ordered.append(choice)
        if correct_choice_id is not None and correct_choice_id not in seen:
            raise ExerciseValidationError(
                ExerciseErrorCode.INVALID_CORRECT_CHOICE,
                f"correct_choice_id {correct_choice_id!r} is not among the choices",
            )
        return tuple(ordered)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": EXERCISE_SCHEMA_VERSION,
            "exercise_id": self.exercise_id,
            "course_id": self.course_id,
            "knowledge_point_ids": list(self.knowledge_point_ids),
            "exercise_type": self.exercise_type.value,
            "prompt": self.prompt,
            "choices": [c.to_dict() for c in self.choices],
            "correct_choice_id": self.correct_choice_id,
            "expected_answer": self.expected_answer,
            "fill_blank": self.fill_blank.to_dict() if self.fill_blank is not None else None,
            "explanation": self.explanation,
            "evidence_ids": list(self.evidence_ids),
            "difficulty": self.difficulty,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Exercise":
        sv = data.get("schema_version")
        if sv != EXERCISE_SCHEMA_VERSION:
            raise ExerciseSchemaError(
                ExerciseErrorCode.INVALID_SCHEMA_VERSION,
                f"unsupported schema_version: {sv!r}",
            )
        et_value = str(data.get("exercise_type") or "")
        try:
            et = ExerciseType(et_value)
        except ValueError:
            raise ExerciseValidationError(
                ExerciseErrorCode.UNKNOWN_EXERCISE_TYPE,
                f"unknown exercise_type: {et_value!r}",
            )
        choices = tuple(Choice.from_dict(c) for c in (data.get("choices") or ()))
        fb_data = data.get("fill_blank")
        fill_blank = FillBlank.from_dict(fb_data) if fb_data else None
        difficulty = data.get("difficulty")
        rebuilt = cls.create(
            str(data.get("course_id") or ""),
            et,
            str(data.get("prompt") or ""),
            tuple(str(k) for k in (data.get("knowledge_point_ids") or ())),
            choices=choices or None,
            correct_choice_id=data.get("correct_choice_id"),
            expected_answer=data.get("expected_answer"),
            fill_blank=fill_blank,
            explanation=data.get("explanation"),
            evidence_ids=tuple(str(e) for e in (data.get("evidence_ids") or ())),
            difficulty=int(difficulty) if difficulty is not None else None,
        )
        stored = str(data.get("exercise_id") or "")
        if stored and stored != rebuilt.exercise_id:
            raise ExerciseValidationError(
                ExerciseErrorCode.INVALID_STATE,
                "exercise_id does not match content (corrupted payload)",
            )
        return rebuilt


class CourseExerciseValidator:
    """Validates an exercise's KP / evidence references against a
    course-scoped registry (spec 31.13).

    Default mode (``allow_cross_kp=False``): every referenced
    evidence id MUST belong to at least one of the exercise's own
    knowledge points -- cross-KP evidence is strictly rejected.
    Permissive mode (``allow_cross_kp=True``): any evidence id that
    exists somewhere in the course is accepted.

    The registry is a mapping ``knowledge_point_id -> tuple of
    evidence ids`` (deterministic, caller-supplied; e.g. built from
    each ``KnowledgePoint.evidence_refs``).
    """

    def __init__(
        self,
        course_knowledge_evidence: Mapping[str, Tuple[str, ...]],
        *,
        allow_cross_kp: bool = False,
    ) -> None:
        self._kp_evidence = {
            str(k): tuple(sorted(set(e for e in v if str(e))))
            for k, v in course_knowledge_evidence.items()
        }
        self._all_evidence: frozenset = frozenset(
            e for evs in self._kp_evidence.values() for e in evs
        )
        self._allow_cross_kp = allow_cross_kp

    def validate(self, exercise: Exercise) -> Exercise:
        missing_kps = tuple(
            kp for kp in exercise.knowledge_point_ids if kp not in self._kp_evidence
        )
        if missing_kps:
            raise ExerciseValidationError(
                ExerciseErrorCode.MISSING_KNOWLEDGE_POINT,
                "knowledge point ids not in course: " + ", ".join(missing_kps),
            )
        if self._allow_cross_kp:
            missing_ev = tuple(
                e for e in exercise.evidence_ids if e not in self._all_evidence
            )
            if missing_ev:
                raise ExerciseValidationError(
                    ExerciseErrorCode.MISSING_EVIDENCE,
                    "evidence ids not in course: " + ", ".join(missing_ev),
                )
        else:
            owned = frozenset(
                e
                for kp in exercise.knowledge_point_ids
                for e in self._kp_evidence.get(kp, ())
            )
            missing_ev = tuple(e for e in exercise.evidence_ids if e not in owned)
            if missing_ev:
                raise ExerciseValidationError(
                    ExerciseErrorCode.CROSS_KP_EVIDENCE,
                    "evidence ids do not belong to the exercise knowledge "
                    "points: " + ", ".join(missing_ev),
                )
        return exercise

    def validate_or_raise(self, exercise: Exercise) -> Exercise:
        """Alias kept for readability."""
        return self.validate(exercise)


class ExplicitExerciseBuilder:
    """Builds exercises from fully caller-supplied content.

    - All content (prompt / choices / answers / explanation) is
      provided by the caller; NO automatic generation (spec 31.6).
    - After construction, references are validated against the
      course registry via :class:`CourseExerciseValidator` when one
      is supplied (spec 31.13: dangling refs are never silently
      accepted).
    """

    def __init__(
        self,
        course_id: str,
        validator: Optional[CourseExerciseValidator] = None,
    ) -> None:
        self._course_id = course_id
        self._validator = validator

    def build_multiple_choice(
        self,
        prompt: str,
        knowledge_point_ids: Iterable[str],
        choices: Iterable[Choice],
        correct_choice_id: str,
        *,
        evidence_ids: Optional[Iterable[str]] = None,
        explanation: Optional[str] = None,
        difficulty: Optional[int] = None,
    ) -> Exercise:
        return self._build(
            ExerciseType.MULTIPLE_CHOICE,
            prompt,
            knowledge_point_ids,
            choices=tuple(choices),
            correct_choice_id=correct_choice_id,
            evidence_ids=evidence_ids,
            explanation=explanation,
            difficulty=difficulty,
        )

    def build_true_false(
        self,
        prompt: str,
        knowledge_point_ids: Iterable[str],
        is_true: bool,
        *,
        evidence_ids: Optional[Iterable[str]] = None,
        explanation: Optional[str] = None,
        difficulty: Optional[int] = None,
    ) -> Exercise:
        return self._build(
            ExerciseType.TRUE_FALSE,
            prompt,
            knowledge_point_ids,
            correct_choice_id="true" if is_true else "false",
            evidence_ids=evidence_ids,
            explanation=explanation,
            difficulty=difficulty,
        )

    def build_short_answer(
        self,
        prompt: str,
        knowledge_point_ids: Iterable[str],
        expected_answer: str,
        *,
        evidence_ids: Optional[Iterable[str]] = None,
        explanation: Optional[str] = None,
        difficulty: Optional[int] = None,
    ) -> Exercise:
        return self._build(
            ExerciseType.SHORT_ANSWER,
            prompt,
            knowledge_point_ids,
            expected_answer=expected_answer,
            evidence_ids=evidence_ids,
            explanation=explanation,
            difficulty=difficulty,
        )

    def build_fill_blank(
        self,
        prompt: str,
        knowledge_point_ids: Iterable[str],
        blank_id: str,
        accepted_answers: Iterable[str],
        *,
        evidence_ids: Optional[Iterable[str]] = None,
        explanation: Optional[str] = None,
        difficulty: Optional[int] = None,
    ) -> Exercise:
        return self._build(
            ExerciseType.FILL_BLANK,
            prompt,
            knowledge_point_ids,
            fill_blank=FillBlank(
                blank_id=blank_id,
                accepted_answers=tuple(sorted(set(str(a) for a in accepted_answers))),
            ),
            evidence_ids=evidence_ids,
            explanation=explanation,
            difficulty=difficulty,
        )

    def _build(
        self,
        exercise_type: ExerciseType,
        prompt: str,
        knowledge_point_ids: Iterable[str],
        **kwargs: Any,
    ) -> Exercise:
        exercise = Exercise.create(
            self._course_id,
            exercise_type,
            prompt,
            tuple(knowledge_point_ids),
            **kwargs,
        )
        if self._validator is not None:
            self._validator.validate(exercise)
        return exercise


class ExerciseGenerator:
    """Reserved seam for a future (non-LLM) generator.

    Task 31 deliberately does NOT implement automatic question
    generation (spec 31.6 / 31.7).  Concrete generators MUST only
    produce exercises whose content is grounded in existing
    KnowledgePoint + Evidence; they must never invent course facts.
    """

    def generate(self) -> Exercise:
        raise NotImplementedError
