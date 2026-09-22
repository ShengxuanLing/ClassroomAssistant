# -*- coding: utf-8 -*-
"""SessionRepository (Task 42)。"""

from __future__ import annotations

from typing import Iterable, Optional

from src.models import ClassSession
from src.persistence.repositories.base import DocumentRepository

__all__ = ["SessionRepository"]


class SessionRepository(DocumentRepository):
    """课堂仓储。

    ``course_id`` 既写进规范列 (便于按课程查询, 并有外键保证不会悬挂),
    也留在 payload 里 (``ClassSession.to_dict()`` 自带)。
    两处来自同一个对象, 因此不可能不一致。
    """

    table = "sessions"

    # ------------------------------------------------------------------

    def save(self, session: ClassSession) -> None:
        if not isinstance(session, ClassSession):
            raise TypeError(
                f"expected ClassSession, got {type(session).__name__}"
            )
        self.put(
            {
                "session_id": session.session_id,
                "course_id": session.course_id,
                "session_number": int(session.session_number),
                "date": session.date,
                "title": session.title,
            },
            session,
        )

    def save_many(self, sessions: Iterable[ClassSession]) -> int:
        count = 0
        for session in sessions:
            self.save(session)
            count += 1
        return count

    # ------------------------------------------------------------------

    def load(self, session_id: str) -> Optional[ClassSession]:
        payload = self.get(session_id)
        if payload is None:
            return None
        return ClassSession.from_dict(payload)

    def load_all(self) -> list[ClassSession]:
        return [ClassSession.from_dict(p) for p in self.all()]

    def load_for_course(self, course_id: str) -> list[ClassSession]:
        """某课程的课堂, 按 (session_number, session_id) 确定性排序。"""
        payloads = self.all(where="course_id = ?", params=(course_id,))
        return [ClassSession.from_dict(p) for p in payloads]

    def session_ids(self) -> list[str]:
        return list(self.keys())
