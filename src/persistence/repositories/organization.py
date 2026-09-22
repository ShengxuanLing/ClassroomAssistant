# -*- coding: utf-8 -*-
"""OrganizationRepository (Task 42)。

知识组织层是**课程隔离**的 (一门课一份 ``CourseKnowledgeStructure``)。
因此本仓储的每个方法都要求 ``course_id`` 或从对象里读出来, 从而不可能
把两门课的知识混进一张表。

``CourseKnowledgeStructure.to_dict()`` / ``from_dict()`` 本身已经能完整
往返 (topics / knowledge_memberships / session_memberships / relations),
并且 ``from_dict`` 会校验悬空引用、重复实体与自环。本仓储因此把
"整体往返"作为主要入口: :meth:`save_structure` / :meth:`load_structure`
直接复用领域层的校验, 而不是在存储层重写一遍规则。
"""

from __future__ import annotations

from typing import Optional

from src.knowledge_organization import (
    SCHEMA_VERSION,
    CourseKnowledgeStructure,
    KnowledgeMembership,
    KnowledgeRelation,
    SessionKnowledgeMembership,
    Topic,
)
from src.persistence.repositories.base import DocumentRepository

__all__ = ["OrganizationRepository"]


class _TopicRepository(DocumentRepository):
    table = "topics"

    def save(self, topic: Topic) -> None:
        self.put(
            {
                "topic_id": topic.topic_id,
                "course_id": topic.course_id,
                "parent_topic_id": topic.parent_topic_id,
                "name": topic.name,
            },
            topic,
        )

    def load(self, topic_id: str) -> Optional[Topic]:
        payload = self.get(topic_id)
        return None if payload is None else Topic.from_dict(payload)

    def load_all(self, *, course_id: Optional[str] = None) -> list[Topic]:
        if course_id is None:
            return [Topic.from_dict(p) for p in self.all()]
        return [
            Topic.from_dict(p)
            for p in self.all(where="course_id = ?", params=(course_id,))
        ]


class _KnowledgeMembershipRepository(DocumentRepository):
    table = "knowledge_memberships"

    def save(self, membership: KnowledgeMembership) -> None:
        self.put(
            {
                "membership_id": membership.membership_id,
                "topic_id": membership.topic_id,
                "knowledge_point_id": membership.knowledge_point_id,
            },
            membership,
        )

    def load(self, membership_id: str) -> Optional[KnowledgeMembership]:
        payload = self.get(membership_id)
        return None if payload is None else KnowledgeMembership.from_dict(payload)

    def load_all(self) -> list[KnowledgeMembership]:
        return [KnowledgeMembership.from_dict(p) for p in self.all()]


class _SessionMembershipRepository(DocumentRepository):
    table = "session_memberships"

    def save(self, membership: SessionKnowledgeMembership) -> None:
        self.put(
            {
                "membership_id": membership.membership_id,
                "session_id": membership.session_id,
                "knowledge_point_id": membership.knowledge_point_id,
            },
            membership,
        )

    def load(self, membership_id: str) -> Optional[SessionKnowledgeMembership]:
        payload = self.get(membership_id)
        if payload is None:
            return None
        return SessionKnowledgeMembership.from_dict(payload)

    def load_all(self) -> list[SessionKnowledgeMembership]:
        return [SessionKnowledgeMembership.from_dict(p) for p in self.all()]


class _KnowledgeRelationRepository(DocumentRepository):
    table = "knowledge_relations"

    def save(self, relation: KnowledgeRelation) -> None:
        self.put(
            {
                "relation_id": relation.relation_id,
                "course_id": relation.course_id,
                "source_knowledge_point_id": relation.source_knowledge_point_id,
                "target_knowledge_point_id": relation.target_knowledge_point_id,
                "relation_type": relation.relation_type.value,
            },
            relation,
        )

    def load(self, relation_id: str) -> Optional[KnowledgeRelation]:
        payload = self.get(relation_id)
        return None if payload is None else KnowledgeRelation.from_dict(payload)

    def load_all(self) -> list[KnowledgeRelation]:
        return [KnowledgeRelation.from_dict(p) for p in self.all()]

    def load_for_course(self, course_id: str) -> list[KnowledgeRelation]:
        return [
            KnowledgeRelation.from_dict(p)
            for p in self.all(where="course_id = ?", params=(course_id,))
        ]


class OrganizationRepository:
    """知识组织层的组合仓储 (topics / memberships / relations)。"""

    def __init__(self, database) -> None:
        self.topics = _TopicRepository(database)
        self.knowledge_memberships = _KnowledgeMembershipRepository(database)
        self.session_memberships = _SessionMembershipRepository(database)
        self.relations = _KnowledgeRelationRepository(database)

    # ------------------------------------------------------------------

    def save_structure(self, structure: CourseKnowledgeStructure) -> None:
        """整体保存一份课程知识结构 (幂等)。

        顺序: 先 topics (父引用), 再 memberships, 再 relations。
        外键在事务内是立即检查的, 因此顺序不能反。
        """
        if not isinstance(structure, CourseKnowledgeStructure):
            raise TypeError(
                "expected CourseKnowledgeStructure, "
                f"got {type(structure).__name__}"
            )
        for topic in structure.topics.values():
            self.topics.save(topic)
        for membership in structure.knowledge_memberships.values():
            self.knowledge_memberships.save(membership)
        for membership in structure.session_memberships.values():
            self.session_memberships.save(membership)
        for relation in structure.relations.values():
            self.relations.save(relation)

    def load_structure(self, course_id: str) -> CourseKnowledgeStructure:
        """按课程重建 ``CourseKnowledgeStructure``。

        走领域层自己的 ``from_dict`` —— 它会校验悬空引用 / 重复实体 /
        自环, 并自行重建内部索引。存储层因此**不需要**(也不应该)去调用
        私有方法或重写一遍校验规则: 从数据库读回来受到的检查, 与从
        JSON 文件读回来完全一样。

        **课程隔离**: ``session_memberships`` 自己没有 ``course_id``
        (它只有 session_id + knowledge_point_id), 所以"哪些属于这门课"
        只能由 ``sessions`` 表回答。领域层的 ``from_dict`` 也查不出来
        —— 它手里只有一份 payload。因此这里必须先按课程的 session 过滤,
        否则加载 course-1 会把 course-2 的课堂归属一起端出来。
        """
        session_ids = self._session_ids_for_course(course_id)
        topics = self.topics.load_all(course_id=course_id)
        topic_ids = {t.topic_id for t in topics}
        payload = {
            "schema_version": SCHEMA_VERSION,
            "course_id": course_id,
            "topics": [t.to_dict() for t in topics],
            "knowledge_memberships": [
                m.to_dict()
                for m in self.knowledge_memberships.load_all()
                if m.topic_id in topic_ids
            ],
            "session_memberships": [
                m.to_dict()
                for m in self.session_memberships.load_all()
                if m.session_id in session_ids
            ],
            "relations": [
                r.to_dict() for r in self.relations.load_for_course(course_id)
            ],
        }
        return CourseKnowledgeStructure.from_dict(payload)

    def _session_ids_for_course(self, course_id: str) -> set[str]:
        """这门课的 session id 集合 (课程隔离的唯一依据)。"""
        rows = self.topics.database.query(
            "SELECT session_id FROM sessions WHERE course_id = ?", (course_id,)
        )
        return {row["session_id"] for row in rows}

    def count(self) -> dict[str, int]:
        return {
            "topics": self.topics.count(),
            "knowledge_memberships": self.knowledge_memberships.count(),
            "session_memberships": self.session_memberships.count(),
            "relations": self.relations.count(),
        }
