# -*- coding: utf-8 -*-
"""ReviewRepository (Task 42)。

规范要求 "不得丢 Review history"。评审历史是**只追加**的:
``review_id`` 是主键, 因此重复提交同一决定不会产生第二行 ——
与 ``KnowledgeStructure.add_review_record`` 的去重语义一致。

本仓储只负责"存/取", 不做任何评审决策 —— 决策属于
``KnowledgeReviewService`` (领域层)。
"""

from __future__ import annotations

from typing import Iterable, Optional

from src.knowledge_review import ReviewRecord
from src.persistence.repositories.base import DocumentRepository

__all__ = ["ReviewRepository", "CourseReviewRepository"]


class CourseReviewRepository(DocumentRepository):
    """**每门课一份**的评审历史表 (Task 68, migration 003)。

    ``review_id`` 由 (知识点, 决定, 选中证据) 派生, 不含课程 —— 两门课对
    同一个知识点做出同样的决定会得到**同一个** review_id, 于是"这门课确认
    过"会渗到另一门课。那是对真相轴的直接污染, 因此按课程分开存。
    """

    table = "course_review_records"


class ReviewRepository(DocumentRepository):
    """知识点评审历史仓储 (追加式)。

    ``course_id`` 给了就同时写 ``course_review_records``; 读的时候给
    ``course_id`` 就只从那张表读 (不串课)。
    """

    table = "review_records"

    def __init__(self, database) -> None:
        super().__init__(database)
        self.course_rows = CourseReviewRepository(database)

    # ------------------------------------------------------------------

    def save(self, record: ReviewRecord, *, course_id: Optional[str] = None) -> None:
        if not isinstance(record, ReviewRecord):
            raise TypeError(
                f"expected ReviewRecord, got {type(record).__name__}"
            )
        if not record.review_id:
            raise ValueError("review record must have a non-empty review_id")
        self.put(
            {
                "review_id": record.review_id,
                "knowledge_point_id": record.knowledge_point_id,
                "decision": record.decision.value,
            },
            record,
        )
        if course_id:
            self.course_rows.put(
                {
                    "course_id": course_id,
                    "review_id": record.review_id,
                    "knowledge_point_id": record.knowledge_point_id,
                    "decision": record.decision.value,
                },
                record,
            )

    def save_many(
        self, records: Iterable[ReviewRecord], *, course_id: Optional[str] = None
    ) -> int:
        count = 0
        for record in records:
            self.save(record, course_id=course_id)
            count += 1
        return count

    # ------------------------------------------------------------------

    def load(
        self, review_id: str, *, course_id: Optional[str] = None
    ) -> Optional[ReviewRecord]:
        payload = (
            self.course_rows.get(course_id, review_id)
            if course_id
            else self.get(review_id)
        )
        if payload is None:
            return None
        return ReviewRecord.from_dict(payload)

    def load_all(self, *, course_id: Optional[str] = None) -> list[ReviewRecord]:
        payloads = (
            self.course_rows.all(where="course_id = ?", params=(course_id,))
            if course_id
            else self.all()
        )
        return [ReviewRecord.from_dict(p) for p in payloads]

    def load_for_knowledge_points(
        self,
        knowledge_point_ids: Iterable[str],
        *,
        course_id: Optional[str] = None,
    ) -> list[ReviewRecord]:
        """只取这些知识点的评审记录, 按 ``(knowledge_point_id, review_id)`` 排序。

        为什么要按课程读: 不加过滤地 ``load_all()`` 会把别的课程的评审
        记录也挂到本课程的知识结构上 —— 那既违反"不混合不同课程"的项目
        铁律, 也会让"整体往返"多出不属于它的数据。
        """
        ids = [str(k) for k in knowledge_point_ids if k]
        if not ids:
            return []
        placeholders = ", ".join("?" for _ in ids)
        where = f"knowledge_point_id IN ({placeholders})"
        params: tuple = tuple(ids)
        if course_id:
            where = "course_id = ? AND " + where
            params = (course_id,) + params
            return [
                ReviewRecord.from_dict(p)
                for p in self.course_rows.all(
                    where=where,
                    params=params,
                    order_by="course_id, knowledge_point_id, review_id",
                )
            ]
        return [
            ReviewRecord.from_dict(p)
            for p in self.all(
                where=where,
                params=params,
                order_by="knowledge_point_id, review_id",
            )
        ]

    def history_for(
        self, knowledge_point_id: str, *, course_id: Optional[str] = None
    ) -> list[ReviewRecord]:
        """某知识点的评审历史, 按 ``review_id`` 确定性排序。

        与 ``KnowledgeStructure.review_records_for_knowledge_point`` 的顺序
        规则一致 —— 领域层按 ``review_id`` 排序, 这里也必须一致,
        否则"重启前后历史顺序不同"会成为一个静默的行为变化。
        """
        where = "knowledge_point_id = ?"
        params: tuple = (knowledge_point_id,)
        if course_id:
            where = "course_id = ? AND " + where
            params = (course_id, knowledge_point_id)
            return [
                ReviewRecord.from_dict(p)
                for p in self.course_rows.all(
                    where=where,
                    params=params,
                    order_by="course_id, knowledge_point_id, review_id",
                )
            ]
        return [
            ReviewRecord.from_dict(p)
            for p in self.all(
                where=where,
                params=params,
                order_by="knowledge_point_id, review_id",
            )
        ]

    def latest_for(
        self, knowledge_point_id: str, *, course_id: Optional[str] = None
    ) -> Optional[ReviewRecord]:
        """最后一次评审决定 (按 ``review_id`` 最大者)。

        注意: ``review_id`` 是内容寻址的稳定 id, 不含时间, 因此
        "最后一次"是**按 id 排序的最后一条**, 与领域层
        ``KnowledgeReviewService.latest_review`` 的规则保持一致。
        """
        records = self.history_for(knowledge_point_id, course_id=course_id)
        return records[-1] if records else None

    def decisions_for(
        self, knowledge_point_id: str, *, course_id: Optional[str] = None
    ) -> list[str]:
        return [
            r.decision.value
            for r in self.history_for(knowledge_point_id, course_id=course_id)
        ]

    def count_for(
        self, knowledge_point_id: str, *, course_id: Optional[str] = None
    ) -> int:
        if course_id:
            return self.course_rows.count(
                where="course_id = ? AND knowledge_point_id = ?",
                params=(course_id, knowledge_point_id),
            )
        return self.count(
            where="knowledge_point_id = ?", params=(knowledge_point_id,)
        )
