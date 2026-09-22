# -*- coding: utf-8 -*-
"""CourseRepository (Task 42)。"""

from __future__ import annotations

from typing import Iterable, Optional

from src.models import Course
from src.persistence.repositories.base import DocumentRepository

__all__ = ["CourseRepository"]


class CourseRepository(DocumentRepository):
    """课程仓储。

    ``course_id`` 是内容寻址的 (见 ``Course._generate_stable_id``), 因此
    主键本身就是幂等键: 同一 (name, code) 重复保存只会更新同一行。
    """

    table = "courses"

    # ------------------------------------------------------------------

    def save(self, course: Course) -> None:
        if not isinstance(course, Course):
            raise TypeError(f"expected Course, got {type(course).__name__}")
        self.put(
            {
                "course_id": course.course_id,
                "name": course.name,
                "code": course.code,
                "language": course.language.value,
            },
            course,
        )

    def save_many(self, courses: Iterable[Course]) -> int:
        count = 0
        for course in courses:
            self.save(course)
            count += 1
        return count

    # ------------------------------------------------------------------

    def load(self, course_id: str) -> Optional[Course]:
        payload = self.get(course_id)
        if payload is None:
            return None
        return Course.from_dict(payload)

    def load_all(self) -> list[Course]:
        return [Course.from_dict(p) for p in self.all()]

    def course_ids(self) -> list[str]:
        return list(self.keys())
