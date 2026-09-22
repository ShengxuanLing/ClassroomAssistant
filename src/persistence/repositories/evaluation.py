# -*- coding: utf-8 -*-
"""EvaluationRepository (Task 42)。

``EvaluationResult`` 自身只带 ``answer_id``, 不带 ``exercise_id`` /
``student_id``。存储层把这两个值**从被评估的答案派生**并写进规范列,
纯粹是为了能按练习/学生查询 —— 它们不是新的领域事实, 因此
:meth:`EvaluationRepository.save` 要求调用方显式传入, 而不是自己去猜。

外键 ``evaluation_results.answer_id -> student_answers.answer_id``
保证"评估必须挂在真实存在的作答上"。这条约束正是规范所说的
"不得丢 exercise/evaluation relation" 的另一半: 关系不但要存下来,
还要**不可能断**。
"""

from __future__ import annotations

from typing import Iterable, Optional

from src.answer_evaluation import EvaluationResult
from src.persistence.repositories.base import DocumentRepository

__all__ = ["EvaluationRepository"]


class EvaluationRepository(DocumentRepository):
    """评估结果仓储。"""

    table = "evaluation_results"

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def save(
        self,
        result: EvaluationResult,
        *,
        exercise_id: Optional[str] = None,
        student_id: Optional[str] = None,
    ) -> None:
        if not isinstance(result, EvaluationResult):
            raise TypeError(
                f"expected EvaluationResult, got {type(result).__name__}"
            )
        self.put(
            {
                "evaluation_id": result.evaluation_id,
                "answer_id": result.answer_id,
                "exercise_id": exercise_id,
                "student_id": student_id,
                "status": result.status.value,
                "score": float(result.score),
                "evaluator_version": result.evaluator_version,
            },
            result,
        )

    def save_many(
        self,
        results: Iterable[EvaluationResult],
        *,
        answer_lookup=None,
    ) -> int:
        """批量保存。

        ``answer_lookup`` 可选: ``answer_id -> (exercise_id, student_id)``。
        给了就顺便把两个派生列填上, 省掉调用方手工 join。
        """
        count = 0
        for result in results:
            exercise_id = student_id = None
            if answer_lookup is not None:
                pair = answer_lookup.get(result.answer_id)
                if pair is not None:
                    exercise_id, student_id = pair
            self.save(result, exercise_id=exercise_id, student_id=student_id)
            count += 1
        return count

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def load(self, evaluation_id: str) -> Optional[EvaluationResult]:
        payload = self.get(evaluation_id)
        if payload is None:
            return None
        return EvaluationResult.from_dict(payload)

    def load_all(
        self, *, student_id: Optional[str] = None, course_id: Optional[str] = None
    ) -> list[EvaluationResult]:
        clauses: list[str] = []
        params: list[object] = []
        if student_id is not None:
            clauses.append("student_id = ?")
            params.append(student_id)
        where = " AND ".join(clauses) if clauses else None
        return [
            EvaluationResult.from_dict(p)
            for p in self.all(where=where, params=tuple(params))
        ]

    def for_answer(self, answer_id: str) -> Optional[EvaluationResult]:
        """某作答的评估 (一个 answer_id 至多一条)。"""
        payloads = self.all(where="answer_id = ?", params=(answer_id,))
        if not payloads:
            return None
        return EvaluationResult.from_dict(payloads[0])

    def status_of(self, evaluation_id: str) -> Optional[str]:
        row = self.get_row(evaluation_id)
        if row is None:
            return None
        return str(row["status"])

    def ids_for_student(self, student_id: str) -> list[str]:
        return list(self.keys(where="student_id = ?", params=(student_id,)))

    def ids_for_exercise(self, exercise_id: str) -> list[str]:
        return list(self.keys(where="exercise_id = ?", params=(exercise_id,)))

    def status_map(self) -> dict[str, str]:
        """``{evaluation_id: status}`` (确定性顺序)。"""
        return {
            str(r["evaluation_id"]): str(r["status"]) for r in self.rows()
        }
