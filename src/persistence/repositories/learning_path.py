# -*- coding: utf-8 -*-
"""LearningPathRepository (Task 42)。

``LearningPath`` 领域对象**没有**自己的 id —— 它是一条由
``(课程, 学生, 目标知识点)`` 唯一确定的**派生视图**。因此表的主键就是
这个自然键, 而不是另造一个存储层 id: 造一个领域里不存在的身份, 正是
本项目硬性原则所禁止的。

路径的 ``node_ids`` 顺序是**有意义**的 (根在前, 目标在后)。顺序保存在
payload 里 (JSON 数组本身有序), 因此往返后顺序不变; 另有测试直接断言
这一点 —— 因为"顺序悄悄变了"是这类派生视图最危险的回归。
"""

from __future__ import annotations

from typing import Iterable, Optional

from src.persistence.repositories.base import DocumentRepository
from src.study_plan import LearningPath

__all__ = ["LearningPathRepository"]


class LearningPathRepository(DocumentRepository):
    """学习路径仓储 (自然键: 课程 + 学生 + 目标知识点)。"""

    table = "learning_paths"

    # ------------------------------------------------------------------

    def save(
        self,
        path: LearningPath,
        *,
        course_id: str,
        student_id: str,
    ) -> None:
        if not isinstance(path, LearningPath):
            raise TypeError(f"expected LearningPath, got {type(path).__name__}")
        if not course_id or not student_id:
            raise ValueError("course_id and student_id must be non-empty")
        self.put(
            {
                "course_id": str(course_id),
                "student_id": str(student_id),
                "target_knowledge_point_id": path.target_knowledge_point_id,
                "status": path.status.value,
            },
            path,
        )

    def save_many(
        self,
        paths: Iterable[tuple[LearningPath, str, str]],
    ) -> int:
        """``paths``: (path, course_id, student_id) 三元组序列。"""
        count = 0
        for path, course_id, student_id in paths:
            self.save(path, course_id=course_id, student_id=student_id)
            count += 1
        return count

    # ------------------------------------------------------------------

    def load(
        self, course_id: str, student_id: str, target_knowledge_point_id: str
    ) -> Optional[LearningPath]:
        payload = self.get(course_id, student_id, target_knowledge_point_id)
        if payload is None:
            return None
        return LearningPath.from_dict(payload)

    def load_all(self) -> list[LearningPath]:
        return [LearningPath.from_dict(p) for p in self.all()]

    def paths_for_student(
        self, course_id: str, student_id: str
    ) -> list[LearningPath]:
        return [
            LearningPath.from_dict(p)
            for p in self.all(
                where="course_id = ? AND student_id = ?",
                params=(course_id, student_id),
            )
        ]

    def node_ids(
        self, course_id: str, student_id: str, target_knowledge_point_id: str
    ) -> list[str]:
        """路径节点 (顺序 = 根 -> 目标)。"""
        path = self.load(course_id, student_id, target_knowledge_point_id)
        return [] if path is None else list(path.node_ids)

    def status_of(
        self, course_id: str, student_id: str, target_knowledge_point_id: str
    ) -> Optional[str]:
        row = self.get_row(course_id, student_id, target_knowledge_point_id)
        if row is None:
            return None
        return str(row["status"])

    def targets_for_student(self, course_id: str, student_id: str) -> list[str]:
        return [
            str(r["target_knowledge_point_id"])
            for r in self.rows(
                where="course_id = ? AND student_id = ?",
                params=(course_id, student_id),
            )
        ]
