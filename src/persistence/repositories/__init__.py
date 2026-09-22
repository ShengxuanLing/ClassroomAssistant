# -*- coding: utf-8 -*-
"""仓储集合 (Task 42)。

:class:`Repositories` 把 12+ 个仓储绑定到**同一个** :class:`Database`,
并提供统一的事务入口。它是应用层的"工作单元 (unit of work)":
所有跨聚合的原子操作都写成

    with repos.transaction():
        repos.materials.save_record(record)
        repos.materials.processing.save(processing)
        repos.evidence.save(evidence)

**一个** 事务, 要么全成, 要么全不成。

为什么仓储不自己开事务
--------------------------------------------------------------------

仓储方法只发 SQL, 不 ``BEGIN``。这样它们既能被单独调用 (自动提交),
也能被组合进更大的事务。如果每个仓储自己开事务, "材料 + 状态" 这类
跨表原子性就永远做不到 —— 内层提交会把外层的回滚变成谎言。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from src.persistence.database import Database
from src.persistence.repositories.answer import AnswerRepository
from src.persistence.repositories.course import CourseRepository
from src.persistence.repositories.evaluation import EvaluationRepository
from src.persistence.repositories.evidence import EvidenceRepository
from src.persistence.repositories.exercise import ExerciseRepository
from src.persistence.repositories.knowledge import (
    ConflictRepository,
    KnowledgeRepository,
    RelationshipRepository,
)
from src.persistence.repositories.learning_path import LearningPathRepository
from src.persistence.repositories.material import (
    MaterialProcessingRepository,
    MaterialRepository,
)
from src.persistence.repositories.organization import OrganizationRepository
from src.persistence.repositories.review import ReviewRepository
from src.persistence.repositories.session import SessionRepository
from src.persistence.repositories.student import (
    LearningEventRepository,
    StudentKnowledgeStateRepository,
    StudentRepository,
)
from src.persistence.repositories.study_plan import StudyPlanRepository

__all__ = [
    "Repositories",
    "CourseRepository",
    "SessionRepository",
    "MaterialRepository",
    "MaterialProcessingRepository",
    "EvidenceRepository",
    "KnowledgeRepository",
    "RelationshipRepository",
    "ConflictRepository",
    "ReviewRepository",
    "OrganizationRepository",
    "StudentRepository",
    "LearningEventRepository",
    "StudentKnowledgeStateRepository",
    "ExerciseRepository",
    "AnswerRepository",
    "EvaluationRepository",
    "StudyPlanRepository",
    "LearningPathRepository",
]


class Repositories:
    """绑定到同一个数据库的一组仓储。"""

    def __init__(self, database: Database) -> None:
        self.database = database
        # 课程 / 课堂
        self.courses = CourseRepository(database)
        self.sessions = SessionRepository(database)
        # 材料
        self.materials = MaterialRepository(database)
        self.material_processing = MaterialProcessingRepository(database)
        # 证据
        self.evidence = EvidenceRepository(database)
        # 知识
        self.knowledge = KnowledgeRepository(database)
        self.relationships = RelationshipRepository(database)
        self.conflicts = ConflictRepository(database)
        self.reviews = ReviewRepository(database)
        self.organization = OrganizationRepository(database)
        # 学生
        self.students = StudentRepository(database)
        self.learning_events = LearningEventRepository(database)
        self.student_states = StudentKnowledgeStateRepository(database)
        # 练习 / 作答 / 评估
        self.exercises = ExerciseRepository(database)
        self.answers = AnswerRepository(database)
        self.evaluations = EvaluationRepository(database)
        # 计划 / 路径
        self.study_plans = StudyPlanRepository(database)
        self.learning_paths = LearningPathRepository(database)

    # ------------------------------------------------------------------

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[Database]:
        """跨仓储的原子操作。嵌套安全 (SAVEPOINT)。"""
        with self.database.transaction(immediate=immediate) as conn:
            yield conn

    @contextmanager
    def deferred_foreign_keys(self) -> Iterator[Database]:
        """在事务内推迟外键检查到提交时。

        用于**整体恢复**一个工作区: 那时插入顺序无法天然满足所有外键
        (例如某条证据先于它引用的材料被写入)。推迟检查让"先全写进去,
        再统一校验"成为可能, 同时保证提交时一切合法 —— 悬空引用仍然
        会被拒绝, 只是检查点从"每条语句"移到"提交时"。
        """
        with self.database.transaction() as conn:
            conn.execute("PRAGMA defer_foreign_keys = ON")
            yield self.database

    # ------------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        """各表行数 (确定性顺序, 用于诊断与测试)。"""
        return {
            "courses": self.courses.count(),
            "sessions": self.sessions.count(),
            "materials": self.materials.count(),
            "material_processing": self.material_processing.count(),
            "evidence": self.evidence.count(),
            "knowledge_points": self.knowledge.count(),
            "relationships": self.relationships.count(),
            "conflicts": self.conflicts.count(),
            "review_records": self.reviews.count(),
            "topics": self.organization.topics.count(),
            "knowledge_memberships": self.organization.knowledge_memberships.count(),
            "session_memberships": self.organization.session_memberships.count(),
            "knowledge_relations": self.organization.relations.count(),
            "students": self.students.count(),
            "learning_events": self.learning_events.count(),
            "student_knowledge_state": self.student_states.count(),
            "exercises": self.exercises.count(),
            "student_answers": self.answers.count(),
            "evaluation_results": self.evaluations.count(),
            "study_plans": self.study_plans.count(),
            "learning_paths": self.learning_paths.count(),
        }

    def total_rows(self) -> int:
        return sum(self.counts().values())
