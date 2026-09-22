# -*- coding: utf-8 -*-
"""AnswerRepository (Task 42)。

``StudentAnswer`` 的身份是**内容寻址**的 (学生 + 练习 + 提交值 + sequence),
因此 ``answer_id`` 就是主键, 也就是幂等键: 同一份作答重复提交只会命中
同一行, 不可能产生重复记录。

``submitted_at`` 是应用层元数据 (领域对象不带时间戳, 见
``LearningService._submitted_at``), 因此它是**显式传入**的列, 不是从
payload 派生的。重复提交时调用方保留首次时间 -> 幂等。
"""

from __future__ import annotations

from typing import Iterable, Optional

from src.answer_evaluation import StudentAnswer
from src.persistence.repositories.base import DocumentRepository

__all__ = ["AnswerRepository"]


class AnswerRepository(DocumentRepository):
    """学生作答仓储 (内容寻址, 追加式)。"""

    table = "student_answers"

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def save(
        self,
        answer: StudentAnswer,
        *,
        course_id: Optional[str] = None,
        submitted_at: Optional[str] = None,
    ) -> None:
        if not isinstance(answer, StudentAnswer):
            raise TypeError(
                f"expected StudentAnswer, got {type(answer).__name__}"
            )
        self.put(
            {
                "answer_id": answer.answer_id,
                "student_id": answer.student_id,
                "course_id": course_id,
                "exercise_id": answer.exercise_id,
                "sequence": int(answer.sequence),
                "submitted_value": answer.submitted_value,
                "submitted_at": submitted_at,
            },
            answer,
        )

    def save_many(
        self,
        answers: Iterable[StudentAnswer],
        *,
        course_id: Optional[str] = None,
        submitted_at: Optional[str] = None,
    ) -> int:
        count = 0
        for answer in answers:
            self.save(answer, course_id=course_id, submitted_at=submitted_at)
            count += 1
        return count

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def load(self, answer_id: str) -> Optional[StudentAnswer]:
        payload = self.get(answer_id)
        if payload is None:
            return None
        return StudentAnswer.from_dict(payload)

    def load_all(
        self, *, student_id: Optional[str] = None, course_id: Optional[str] = None
    ) -> list[StudentAnswer]:
        clauses: list[str] = []
        params: list[object] = []
        if student_id is not None:
            clauses.append("student_id = ?")
            params.append(student_id)
        if course_id is not None:
            clauses.append("course_id = ?")
            params.append(course_id)
        where = " AND ".join(clauses) if clauses else None
        return [
            StudentAnswer.from_dict(p)
            for p in self.all(where=where, params=tuple(params))
        ]

    def submitted_at_for(self, answer_id: str) -> Optional[str]:
        row = self.get_row(answer_id)
        if row is None:
            return None
        value = row["submitted_at"]
        return None if value is None else str(value)

    def exercise_id_of(self, answer_id: str) -> Optional[str]:
        row = self.get_row(answer_id)
        if row is None:
            return None
        return str(row["exercise_id"])

    def student_id_of(self, answer_id: str) -> Optional[str]:
        row = self.get_row(answer_id)
        if row is None:
            return None
        return str(row["student_id"])

    def answers_for_exercise(
        self, exercise_id: str, *, student_id: Optional[str] = None
    ) -> list[StudentAnswer]:
        if student_id is None:
            return [
                StudentAnswer.from_dict(p)
                for p in self.all(
                    where="exercise_id = ?", params=(exercise_id,)
                )
            ]
        return [
            StudentAnswer.from_dict(p)
            for p in self.all(
                where="exercise_id = ? AND student_id = ?",
                params=(exercise_id, student_id),
            )
        ]

    def submitted_at_map(self) -> dict[str, str]:
        """``{answer_id: submitted_at}`` (只含非空值), 确定性顺序。"""
        return {
            str(r["answer_id"]): str(r["submitted_at"])
            for r in self.rows()
            if r["submitted_at"] is not None
        }
