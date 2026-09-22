# -*- coding: utf-8 -*-
"""ExerciseRepository (Task 42)。

规范点名: "不得丢 exercise/evaluation relation"。练习与知识点、练习与
出题证据都是多对多关系, 因此各有一张**有序**关系表。

注意 ``Exercise.to_dict()`` 里**包含答案键** (``correct_choice_id`` /
``expected_answer`` / ``fill_blank`` / ``explanation``)。这是刻意的:
存储层必须保存完整练习 (否则教师无法再编辑), 而"学生视角不得看到答案"
是**投影层**的职责 (``learning_view._ANSWER_KEY_FIELDS``), 不是存储层的。
把答案从数据库里删掉反而会让练习不可用。
"""

from __future__ import annotations

from typing import Iterable, Optional

from src.exercises import Exercise
from src.persistence.repositories.base import DocumentRepository, LinkRepository

__all__ = ["ExerciseRepository"]


class ExerciseRepository(DocumentRepository):
    """练习仓储 (+ 知识点链 + 证据链)。"""

    table = "exercises"

    def __init__(self, database) -> None:
        super().__init__(database)
        self.knowledge_links = LinkRepository(database, "exercise_knowledge_points")
        self.evidence_links = LinkRepository(database, "exercise_evidence")

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def save(self, exercise: Exercise) -> None:
        if not isinstance(exercise, Exercise):
            raise TypeError(f"expected Exercise, got {type(exercise).__name__}")
        self.put(
            {
                "exercise_id": exercise.exercise_id,
                "course_id": exercise.course_id,
                "exercise_type": exercise.exercise_type.value,
                "difficulty": exercise.difficulty,
            },
            exercise,
        )
        # 两条链都从同一对象派生 -> 与 payload 不可能不一致。
        self.knowledge_links.replace(
            exercise.exercise_id, list(exercise.knowledge_point_ids)
        )
        # 证据链**只索引可解析的证据**: 领域层刻意允许练习引用一条不存在的
        # 证据 (那是必须暴露给用户的数据缺陷, 见 learning_view 的
        # ``unresolved_evidence_ids``)。若这里让外键把它拒掉, 持久化层就
        # 偷偷收紧了领域语义。权威引用列表完整地留在 ``payload`` 里, 因此
        # 悬空引用照样报得出来, 重启之后也照样在。
        self.evidence_links.replace(
            exercise.exercise_id,
            list(exercise.evidence_ids),
            only_existing=("evidence", "evidence_id"),
        )

    def save_many(self, exercises: Iterable[Exercise]) -> int:
        count = 0
        for exercise in exercises:
            self.save(exercise)
            count += 1
        return count

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def load(self, exercise_id: str) -> Optional[Exercise]:
        payload = self.get(exercise_id)
        if payload is None:
            return None
        return Exercise.from_dict(payload)

    def load_all(self, *, course_id: Optional[str] = None) -> list[Exercise]:
        if course_id is None:
            payloads = self.all()
        else:
            payloads = self.all(where="course_id = ?", params=(course_id,))
        return [Exercise.from_dict(p) for p in payloads]

    def knowledge_point_ids_for(self, exercise_id: str) -> list[str]:
        return self.knowledge_links.rights_for(exercise_id)

    def exercise_ids_for_knowledge_point(self, knowledge_id: str) -> list[str]:
        return self.knowledge_links.lefts_for(knowledge_id)

    def evidence_ids_for(self, exercise_id: str) -> list[str]:
        return self.evidence_links.rights_for(exercise_id)

    def exercise_ids_for_evidence(self, evidence_id: str) -> list[str]:
        return self.evidence_links.lefts_for(evidence_id)

    def of_type(self, exercise_type: str) -> list[Exercise]:
        return [
            Exercise.from_dict(p)
            for p in self.all(
                where="exercise_type = ?", params=(str(exercise_type),)
            )
        ]

    def type_of(self, exercise_id: str) -> Optional[str]:
        row = self.get_row(exercise_id)
        if row is None:
            return None
        return str(row["exercise_type"])

    def ids_for_course(self, course_id: str) -> list[str]:
        return list(self.keys(where="course_id = ?", params=(course_id,)))
