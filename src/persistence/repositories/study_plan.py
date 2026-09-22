# -*- coding: utf-8 -*-
"""StudyPlanRepository (Task 42)。

``StudyPlan`` 是**不可变的确定性快照**: ``plan_id`` 是内容寻址的
(学生 + 课程 + 条目 + 规则版本), 因此:

- 同一份输入重复规划 -> 同一 ``plan_id`` -> 只更新同一行 (幂等);
- 输入变了 -> 新的 ``plan_id`` -> 新行, 旧快照仍然保留 (可审计)。

正因为 id 是内容寻址而不是时间戳, "最新的计划"**不是**一个有意义的
概念 —— 本仓储不提供 ``latest()``, 而是提供
:meth:`plans_for_student`, 让调用方显式选择。用 ``plan_id`` 排序只是
为了确定性, 不代表时间先后。
"""

from __future__ import annotations

from typing import Iterable, Optional

from src.persistence.repositories.base import DocumentRepository
from src.study_plan import StudyPlan

__all__ = ["StudyPlanRepository"]


class StudyPlanRepository(DocumentRepository):
    """学习计划仓储 (不可变快照, 内容寻址)。"""

    table = "study_plans"

    # ------------------------------------------------------------------

    def save(self, plan: StudyPlan) -> None:
        if not isinstance(plan, StudyPlan):
            raise TypeError(f"expected StudyPlan, got {type(plan).__name__}")
        self.put(
            {
                "plan_id": plan.plan_id,
                "student_id": plan.student_id,
                "course_id": plan.course_id,
            },
            plan,
        )

    def save_many(self, plans: Iterable[StudyPlan]) -> int:
        count = 0
        for plan in plans:
            self.save(plan)
            count += 1
        return count

    # ------------------------------------------------------------------

    def load(self, plan_id: str) -> Optional[StudyPlan]:
        payload = self.get(plan_id)
        if payload is None:
            return None
        return StudyPlan.from_dict(payload)

    def load_all(self) -> list[StudyPlan]:
        return [StudyPlan.from_dict(p) for p in self.all()]

    def plans_for_student(
        self, student_id: str, *, course_id: Optional[str] = None
    ) -> list[StudyPlan]:
        """该学生的全部计划快照。

        顺序按 ``plan_id`` (确定性), **不是**时间顺序 —— ``plan_id`` 是
        内容哈希, 不含时间。
        """
        if course_id is None:
            payloads = self.all(
                where="student_id = ?", params=(student_id,)
            )
        else:
            payloads = self.all(
                where="student_id = ? AND course_id = ?",
                params=(student_id, course_id),
            )
        return [StudyPlan.from_dict(p) for p in payloads]

    def plan_ids_for_student(self, student_id: str) -> list[str]:
        return list(self.keys(where="student_id = ?", params=(student_id,)))

    def item_count(self, plan_id: str) -> int:
        plan = self.load(plan_id)
        return 0 if plan is None else len(plan.items)
