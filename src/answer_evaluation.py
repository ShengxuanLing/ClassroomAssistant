"""Deterministic Answer Evaluation Layer (Task 32).

Pipeline position (spec 32.1):

    Exercise -> StudentAnswer -> EvaluationResult
               -> LearningEvent (ANSWERED) via student_learning

Design constraints honored
--------------------------
- Deterministic, LLM-free evaluation only (spec 32.6 / 32.15):
  MULTIPLE_CHOICE exact choice match, TRUE_FALSE direct comparison,
  FILL_BLANK exact match against explicit ``accepted_answers``
  (no trim / casefold -- exact comparison by design), SHORT_ANSWER
  is UNSUPPORTED (no semantic grading, spec 32.8: "Madrid" vs
  "马德里" is NEVER treated as equal without explicit accepted
  answers).
- Student answers are course artifacts: they are NEVER added to
  the EvidenceStore (spec 32.9) and never mutate KnowledgePoint
  content (global rule 7).
- Answers reference real exercises; unknown exercise ids are
  rejected with a stable error (spec 32.12).
- No uuid / datetime.now / random / hash(): all ids are
  ``answer-<sha256[:24]>`` / ``evaluation-<sha256[:24]>``.
- Student isolation: evaluation results are keyed per student;
  one student's answers never affect another's (spec 32.11).
- frozen=True; to_dict / from_dict; schema_version = 1; unknown
  versions -> stable error.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple

__all__ = [
    "EVALUATION_SCHEMA_VERSION",
    "EVALUATOR_VERSION",
    "EvaluationErrorCode",
    "EvaluationError",
    "EvaluationValidationError",
    "EvaluationSchemaError",
    "EvaluationStatus",
    "StudentAnswer",
    "EvaluationResult",
    "ExactEvaluator",
]

EVALUATION_SCHEMA_VERSION = 1
EVALUATOR_VERSION = "exact-v1"


class EvaluationErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    UNKNOWN_EXERCISE = "unknown_exercise"
    UNSUPPORTED_SUBMISSION = "unsupported_submission"
    DUPLICATE_ANSWER = "duplicate_answer"
    DUPLICATE_EVALUATION = "duplicate_evaluation"
    INVALID_SCHEMA_VERSION = "invalid_schema_version"
    INVALID_STATE = "invalid_state"


class EvaluationError(Exception):
    def __init__(self, code: EvaluationErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code.value}] {message}")


class EvaluationValidationError(EvaluationError):
    pass


class EvaluationSchemaError(EvaluationError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha24(payload: Any) -> str:
    if isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = _canonical_json(payload).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


class EvaluationStatus(str, Enum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class StudentAnswer:
    """One deterministic submission by one student for one exercise.

    ``submitted_value`` is type-shaped by the exercise: choice id for
    multiple choice / true-false, raw text for short answer /
    fill-blank (kept verbatim, no normalisation).
    """

    answer_id: str
    student_id: str
    exercise_id: str
    submitted_value: str
    sequence: int

    @classmethod
    def create(
        cls,
        student_id: str,
        exercise_id: str,
        submitted_value: str,
        sequence: int,
    ) -> "StudentAnswer":
        sid = str(student_id or "").strip()
        eid = str(exercise_id or "").strip()
        if not sid:
            raise EvaluationValidationError(
                EvaluationErrorCode.INVALID_INPUT,
                "student_id must be non-empty",
            )
        if not eid:
            raise EvaluationValidationError(
                EvaluationErrorCode.INVALID_INPUT,
                "exercise_id must be non-empty",
            )
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise EvaluationValidationError(
                EvaluationErrorCode.INVALID_INPUT,
                "sequence must be a non-negative integer",
            )
        value = str(submitted_value or "")
        payload = {
            "student_id": sid,
            "exercise_id": eid,
            "sequence": sequence,
            "submitted_value": value,
        }
        return cls(
            answer_id="answer-" + _sha24(payload),
            student_id=sid,
            exercise_id=eid,
            submitted_value=value,
            sequence=sequence,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "answer_id": self.answer_id,
            "student_id": self.student_id,
            "exercise_id": self.exercise_id,
            "submitted_value": self.submitted_value,
            "sequence": self.sequence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StudentAnswer":
        sv = data.get("schema_version")
        if sv != EVALUATION_SCHEMA_VERSION:
            raise EvaluationSchemaError(
                EvaluationErrorCode.INVALID_SCHEMA_VERSION,
                f"unsupported schema_version: {sv!r}",
            )
        rebuilt = cls.create(
            str(data.get("student_id") or ""),
            str(data.get("exercise_id") or ""),
            str(data.get("submitted_value") or ""),
            int(data.get("sequence") or 0),
        )
        stored = str(data.get("answer_id") or "")
        if stored and stored != rebuilt.answer_id:
            raise EvaluationValidationError(
                EvaluationErrorCode.INVALID_STATE,
                "answer_id does not match content (corrupted payload)",
            )
        return rebuilt


@dataclass(frozen=True)
class EvaluationResult:
    """Deterministic verdict for one student answer.

    - CORRECT = score 1.0
    - INCORRECT = score 0.0
    - UNSUPPORTED = score 0.0 (evaluator cannot grade reliably; no LLM)
    - PARTIAL is NEVER produced by the exact evaluator (no explicit
      partial rubric exists in this layer, spec 32.7).
    """

    evaluation_id: str
    answer_id: str
    status: EvaluationStatus
    score: float
    feedback: Optional[str]
    evaluator_version: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "evaluation_id": self.evaluation_id,
            "answer_id": self.answer_id,
            "status": self.status.value,
            "score": self.score,
            "feedback": self.feedback,
            "evaluator_version": self.evaluator_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvaluationResult":
        sv = data.get("schema_version")
        if sv != EVALUATION_SCHEMA_VERSION:
            raise EvaluationSchemaError(
                EvaluationErrorCode.INVALID_SCHEMA_VERSION,
                f"unsupported schema_version: {sv!r}",
            )
        status_value = str(data.get("status") or "")
        try:
            status = EvaluationStatus(status_value)
        except ValueError:
            raise EvaluationValidationError(
                EvaluationErrorCode.INVALID_STATE,
                f"unknown status: {status_value!r}",
            )
        score = float(data.get("score") or 0.0)
        result = cls(
            evaluation_id="",
            answer_id=str(data.get("answer_id") or ""),
            status=status,
            score=score,
            feedback=data.get("feedback"),
            evaluator_version=str(data.get("evaluator_version") or EVALUATOR_VERSION),
        )
        stored = str(data.get("evaluation_id") or "")
        payload = {
            "answer_id": result.answer_id,
            "evaluator_version": result.evaluator_version,
            "status": status.value,
            "score": score,
        }
        expected = "evaluation-" + _sha24(payload)
        if stored and stored != expected:
            raise EvaluationValidationError(
                EvaluationErrorCode.INVALID_STATE,
                "evaluation_id does not match content (corrupted payload)",
            )
        return cls(
            evaluation_id=expected,
            answer_id=result.answer_id,
            status=status,
            score=score,
            feedback=result.feedback,
            evaluator_version=result.evaluator_version,
        )


class ExactEvaluator:
    """Deterministic exact-match evaluator (spec 32.6).

    No semantic grading: "Madrid" is NEVER equal to "马德里" unless the
    exercise explicitly lists it in accepted answers (fill-blank).
    No LLM, no network, no datetime.now.
    """

    version: str = EVALUATOR_VERSION

    def __init__(self, exercises: Mapping[str, Any]) -> None:
        """exercises: mapping exercise_id -> Exercise instance."""
        self._exercises: Dict[str, Any] = {
            str(k): v for k, v in (exercises or {}).items()
        }

    def _exercise(self, exercise_id: str) -> Any:
        exercise = self._exercises.get(str(exercise_id or ""))
        if exercise is None:
            raise EvaluationValidationError(
                EvaluationErrorCode.UNKNOWN_EXERCISE,
                f"unknown exercise_id: {exercise_id!r}",
            )
        return exercise

    def _make_result(
        self,
        answer: StudentAnswer,
        status: EvaluationStatus,
        score: float,
        feedback: Optional[str],
    ) -> EvaluationResult:
        payload = {
            "answer_id": answer.answer_id,
            "evaluator_version": self.version,
            "status": status.value,
            "score": score,
        }
        return EvaluationResult(
            evaluation_id="evaluation-" + _sha24(payload),
            answer_id=answer.answer_id,
            status=status,
            score=score,
            feedback=feedback,
            evaluator_version=self.version,
        )

    def evaluate(self, answer: StudentAnswer) -> EvaluationResult:
        exercise = self._exercise(answer.exercise_id)
        from src.exercises import ExerciseType

        exercise_type = exercise.exercise_type
        if exercise_type is ExerciseType.MULTIPLE_CHOICE:
            valid_ids = frozenset(c.choice_id for c in exercise.choices)
            if answer.submitted_value not in valid_ids:
                return self._make_result(
                    answer,
                    EvaluationStatus.UNSUPPORTED,
                    0.0,
                    "submitted value is not a valid choice id",
                )
            correct = exercise.correct_choice_id
            status = (
                EvaluationStatus.CORRECT
                if answer.submitted_value == correct
                else EvaluationStatus.INCORRECT
            )
            score = 1.0 if status is EvaluationStatus.CORRECT else 0.0
            return self._make_result(answer, status, score, None)

        if exercise_type is ExerciseType.TRUE_FALSE:
            submitted = answer.submitted_value
            correct = exercise.correct_choice_id
            if submitted not in ("true", "false"):
                return self._make_result(
                    answer,
                    EvaluationStatus.UNSUPPORTED,
                    0.0,
                    "true/false answers must be 'true' or 'false'",
                )
            status = EvaluationStatus.CORRECT if submitted == correct else EvaluationStatus.INCORRECT
            score = 1.0 if status is EvaluationStatus.CORRECT else 0.0
            return self._make_result(answer, status, score, None)

        if exercise_type is ExerciseType.FILL_BLANK:
            accepted = exercise.fill_blank.accepted_answers
            status = (
                EvaluationStatus.CORRECT
                if answer.submitted_value in accepted
                else EvaluationStatus.INCORRECT
            )
            score = 1.0 if status is EvaluationStatus.CORRECT else 0.0
            return self._make_result(answer, status, score, None)

        # SHORT_ANSWER: exact text equality only.
        expected = str(exercise.expected_answer or "")
        if not expected:
            return self._make_result(
                answer,
                EvaluationStatus.UNSUPPORTED,
                0.0,
                "exercise has no expected answer to compare against",
            )
        if answer.submitted_value == expected:
            return self._make_result(answer, EvaluationStatus.CORRECT, 1.0, None)
        # Default per spec 32.6: free-text short answers are UNSUPPORTED
        # unless they exactly match; semantic grading is forbidden.
        return self._make_result(
            answer,
            EvaluationStatus.UNSUPPORTED,
            0.0,
            "short answers are not semantically graded; exact match only",
        )


class AnswerEvaluationLog:
    """Append-only, idempotent, per-student evaluation log.

    - duplicate (answer_id) is a no-op (spec 32.16 duplicate answer
      / duplicate evaluation).
    - results are keyed per student: student isolation is structural
      (spec 32.11).
    - the log NEVER writes to EvidenceStore or KnowledgePoint
      (spec 32.9 / global rule 7).
    - ``answered_event(...)`` builds a Task 30 ANSWERED LearningEvent
      so state updates flow through the event reducer, not via
      direct record mutation (spec 32.10).
    """

    def __init__(self, evaluator: ExactEvaluator) -> None:
        self._evaluator = evaluator
        self._answers: Dict[str, StudentAnswer] = {}
        self._evaluations: Dict[str, EvaluationResult] = {}

    def set_evaluator(self, evaluator: ExactEvaluator) -> None:
        """Point the log at a fresh evaluator **without losing history**.

        ``ExactEvaluator`` snapshots the exercise catalogue it is given,
        so a caller that keeps one long-lived log must refresh the
        evaluator whenever new exercises are authored.  Replacing the
        whole log instead would silently drop every previously recorded
        answer and evaluation -- history is append-only and must never
        be rebuilt from scratch.
        """
        self._evaluator = evaluator

    def record_answer(self, answer: StudentAnswer) -> StudentAnswer:
        if answer.answer_id in self._answers:
            return self._answers[answer.answer_id]
        self._evaluator._exercise(answer.exercise_id)
        self._answers[answer.answer_id] = answer
        return answer

    def evaluate(self, answer: StudentAnswer) -> EvaluationResult:
        self.record_answer(answer)
        if answer.answer_id in self._evaluations:
            return self._evaluations[answer.answer_id]
        result = self._evaluator.evaluate(answer)
        self._evaluations[answer.answer_id] = result
        return result

    def answers_for(self, student_id: str) -> Tuple[StudentAnswer, ...]:
        return tuple(
            a
            for a in sorted(self._answers.values(), key=lambda x: x.answer_id)
            if a.student_id == str(student_id or "")
        )

    def results_for(self, student_id: str) -> Tuple[EvaluationResult, ...]:
        return tuple(
            r
            for r in sorted(self._evaluations.values(), key=lambda x: x.evaluation_id)
            if self._answers[r.answer_id].student_id == str(student_id or "")
        )

    def answered_event(
        self,
        course_id: str,
        knowledge_point_id: str,
        result: EvaluationResult,
        sequence: int,
    ) -> Dict[str, Any]:
        """Describe the ANSWERED event to record for a graded answer.

        Returns a plain dict so the log stays decoupled from Task 30; a
        caller turns it into ``StudentLearningLog.record_event`` (the
        event id is derived deterministically there).  ``is_correct``
        is None for UNSUPPORTED / PARTIAL so no correct/incorrect
        counter moves.
        """
        from src.student_learning import LearningEventType

        event = LearningEventType.ANSWERED
        if result.status is EvaluationStatus.CORRECT:
            is_correct: Optional[bool] = True
        elif result.status is EvaluationStatus.INCORRECT:
            is_correct = False
        else:
            is_correct = None
        answer = self._answers[result.answer_id]
        return {
            "event_type": event,
            "student_id": answer.student_id,
            "course_id": course_id,
            "knowledge_point_id": knowledge_point_id,
            "sequence": sequence,
            "is_correct": is_correct,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "answers": [a.to_dict() for a in self.answers_for_all()],
            "evaluations": [r.to_dict() for r in self.results_for_all()],
        }

    def answers_for_all(self) -> Tuple[StudentAnswer, ...]:
        return tuple(sorted(self._answers.values(), key=lambda x: x.answer_id))

    def answer_count(self) -> int:
        """作答总条数 (O(1))。

        Task 68 的多课程总览要显示每门课有多少条作答。逐学生遍历是
        O(学生数 × 作答数), 在 Task 69 的规模下会退化成明显的卡顿;
        这里直接给长度, 不构造任何中间列表。
        """
        return len(self._answers)

    def results_for_all(self) -> Tuple[EvaluationResult, ...]:
        return tuple(sorted(self._evaluations.values(), key=lambda x: x.evaluation_id))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], evaluator: ExactEvaluator) -> "AnswerEvaluationLog":
        sv = data.get("schema_version")
        if sv != EVALUATION_SCHEMA_VERSION:
            raise EvaluationSchemaError(
                EvaluationErrorCode.INVALID_SCHEMA_VERSION,
                f"unsupported schema_version: {sv!r}",
            )
        log = cls(evaluator)
        for raw in data.get("answers") or ():
            log.record_answer(StudentAnswer.from_dict(raw))
        for raw in data.get("evaluations") or ():
            result = EvaluationResult.from_dict(raw)
            if result.answer_id not in log._answers:
                raise EvaluationValidationError(
                    EvaluationErrorCode.INVALID_STATE,
                    "evaluation references an answer not present in the log",
                )
            log._evaluations[result.answer_id] = result
        return log
