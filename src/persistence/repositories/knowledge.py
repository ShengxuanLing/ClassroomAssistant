# -*- coding: utf-8 -*-
"""KnowledgeRepository (+ 关系 / 冲突) —— Task 42。

规范点名要保住的东西
--------------------------------------------------------------------

"不得丢 Evidence provenance / Review history / conflict / student state /
exercise-evaluation relation"。本模块负责其中三项:

- **Evidence provenance**: ``knowledge_point_evidence`` 关系表 (有序),
  并且有外键 —— 悬挂引用会被数据库拒绝, 不是靠人记得检查。
- **conflict**: ``conflicts`` + ``conflict_evidence``。
- **关系图**: ``relationships`` (RELATED_TO / PREREQUISITE_OF / TOPIC)。

Review history 在 :mod:`src.persistence.repositories.review` 里。

为什么用关系表而不是把 id 列表塞进 payload
--------------------------------------------------------------------

``KnowledgePoint.to_dict()`` 已经把 ``evidence_refs`` 序列化了, 那为什么
还要单独的 ``knowledge_point_evidence`` 表?

因为**列表只是数据, 关系才是约束**。有了关系表:

- 外键保证"知识点引用的证据必须真实存在" —— 这是溯源可信的前提;
- 可以反向查询"这条证据支撑了哪些知识点" (溯源审计必需);
- 顺序由 ``position`` 显式保存, 不依赖 JSON 数组的隐式顺序。

payload 里的 ``evidence_refs`` 仍然保留, 两者由仓储**从同一对象**同时派生,
因此不可能不一致; 一致性另有测试盯着。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from src.integration import ConflictRecord
from src.knowledge_structure import KnowledgeStructure, Relationship
from src.models import KnowledgePoint
from src.persistence.repositories.base import (
    CourseLinkRepository,
    DocumentRepository,
    LinkRepository,
)

__all__ = [
    "KnowledgeRepository",
    "RelationshipRepository",
    "ConflictRepository",
    "CourseKnowledgeRepository",
    "CourseConflictRepository",
]

#: 需要遍历"全部课程"时的哨兵。写成常量而不是散落的 None —— "没有课程
#: 维度" 与 "课程未知" 是两件不同的事。
ALL_COURSES = None


def _kp_columns(
    knowledge_point: KnowledgePoint, *, course_id: Optional[str]
) -> dict[str, Any]:
    return {
        "knowledge_id": knowledge_point.knowledge_id,
        "course_id": course_id,
        "title": knowledge_point.title,
        "importance": str(knowledge_point.importance),
        "confidence": knowledge_point.confidence.value,
        "validation_status": str(knowledge_point.validation_status),
        "review_status": str(knowledge_point.review_status),
        "knowledge_score": float(knowledge_point.knowledge_score),
        "needs_verification": 1 if knowledge_point.needs_verification else 0,
    }


class CourseKnowledgeRepository(DocumentRepository):
    """**每门课一份**的知识点表 (Task 68, migration 003)。

    与 ``KnowledgeRepository`` 的关系
    --------------------------------

    两张表写同样的内容, 分工不同:

    - ``knowledge_points``  (:class:`KnowledgeRepository`) —— 主键只有
      ``knowledge_id``。它是**全局身份登记 + 外键锚点**: 子表
      (``session_memberships`` / ``exercise_knowledge_points`` ...) 的外键
      指向它, 而 SQLite 要求父键唯一, 所以这张表不能带 ``course_id``
      进主键。它的 payload 是"最后写它的那门课"的版本, 因此**不可用于
      回答"这门课的知识点是什么"**。
    - ``course_knowledge_points`` (本类) —— 主键 ``(course_id, knowledge_id)``。
      这才是每门课各自的真相, 所有**课程维度的读取一律走这里**。

    为什么必须两张: 见 ``migrations/m003_course_scoped_identity.py`` 的
    模块注释。别把它们合并回去 —— 那正是这次要修的 bug。
    """

    table = "course_knowledge_points"


class KnowledgeRepository(DocumentRepository):
    """知识点仓储 (+ 溯源链)。

    ``course_id`` 参与两张表: 全局表 (外键锚点) 与课程表 (真相)。
    传 ``course_id`` 时两处都写; 不传时只写全局表 (历史行为, 保持兼容)。
    """

    table = "knowledge_points"

    def __init__(self, database) -> None:
        super().__init__(database)
        self.evidence_links = LinkRepository(database, "knowledge_point_evidence")
        self.course_rows = CourseKnowledgeRepository(database)
        self.course_evidence = CourseLinkRepository(database, "course_knowledge_evidence")
        self.relationships = RelationshipRepository(database)
        self.conflicts = ConflictRepository(database)
        #: "库里真实存在的 evidence_id" 缓存 —— 见 :meth:`_surviving_evidence`。
        self._surviving: Optional[set[str]] = None

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def save(
        self, knowledge_point: KnowledgePoint, *, course_id: Optional[str] = None
    ) -> None:
        if not isinstance(knowledge_point, KnowledgePoint):
            raise TypeError(
                f"expected KnowledgePoint, got {type(knowledge_point).__name__}"
            )
        columns = _kp_columns(knowledge_point, course_id=course_id)
        self.put(columns, knowledge_point)
        if course_id:
            self.course_rows.put(columns, knowledge_point)
        # 溯源链与 payload 从同一对象派生 -> 不可能不一致。
        refs = list(knowledge_point.evidence_refs)
        self.evidence_links.replace(knowledge_point.knowledge_id, refs)
        if course_id:
            self.course_evidence.replace(
                course_id, knowledge_point.knowledge_id, refs
            )

    def save_many(
        self,
        knowledge_points: Iterable[KnowledgePoint],
        *,
        course_id: Optional[str] = None,
    ) -> int:
        count = 0
        for kp in knowledge_points:
            self.save(kp, course_id=course_id)
            count += 1
        return count

    def replace_evidence(
        self,
        knowledge_id: str,
        evidence_ids: Iterable[str],
        *,
        course_id: Optional[str] = None,
    ) -> None:
        refs = list(evidence_ids)
        self.evidence_links.replace(knowledge_id, refs)
        if course_id:
            self.course_evidence.replace(course_id, knowledge_id, refs)

    def save_structure(
        self, structure: KnowledgeStructure, *, course_id: Optional[str] = None
    ) -> None:
        """整体保存一个 ``KnowledgeStructure`` (不含 review_records)。

        review_records 属于 :class:`ReviewRepository`; 需要"整个结构无损
        往返"时用 :func:`src.persistence.snapshot.save_knowledge_structure`。
        """
        if not isinstance(structure, KnowledgeStructure):
            raise TypeError(
                f"expected KnowledgeStructure, got {type(structure).__name__}"
            )
        for kp in structure.knowledge_points.values():
            self.save(kp, course_id=course_id)
        for relationship in structure.relationships:
            self.relationships.save(relationship)
        for conflict in structure.conflicts:
            self.conflicts.save(conflict, course_id=course_id)

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def load(
        self, knowledge_id: str, *, course_id: Optional[str] = None
    ) -> Optional[KnowledgePoint]:
        if course_id:
            payload = self.course_rows.get(course_id, knowledge_id)
        else:
            payload = self.get(knowledge_id)
        if payload is None:
            return None
        return KnowledgePoint.from_dict(payload)

    def load_all(self, *, course_id: Optional[str] = None) -> list[KnowledgePoint]:
        """按课程取知识点。

        ``course_id`` 给了就读**课程表** (每门课各一份, 不串课);
        没给就读全局表 (历史行为 —— 同一 knowledge_id 只会有最后写入的那份)。
        """
        if course_id is None:
            payloads = self.all()
        else:
            payloads = self.course_rows.all(
                where="course_id = ?", params=(course_id,)
            )
        return [KnowledgePoint.from_dict(p) for p in payloads]

    def evidence_ids_for(
        self, knowledge_id: str, *, course_id: Optional[str] = None
    ) -> list[str]:
        if course_id:
            return self.course_evidence.rights_for(course_id, knowledge_id)
        return self.evidence_links.rights_for(knowledge_id)

    def knowledge_ids_for_evidence(
        self, evidence_id: str, *, course_id: Optional[str] = None
    ) -> list[str]:
        if course_id:
            return self.course_evidence.lefts_for(course_id, evidence_id)
        return self.evidence_links.lefts_for(evidence_id)

    def _surviving_evidence(self) -> set[str]:
        """库里**真实存在**的 evidence_id 集合 (懒加载, 每次 load_structure 一份)。

        只在真遇到"关系表里查不到溯源链"的知识点时才去查 —— 正常路径
        一次都不查, Task 69 那种上万条证据的规模也不会被拖慢。
        """
        if self._surviving is None:
            self._surviving = {
                row[0] for row in self.database.query("SELECT evidence_id FROM evidence")
            }
        return self._surviving

    def load_structure(self, *, course_id: Optional[str] = None) -> KnowledgeStructure:
        """重建 ``KnowledgeStructure`` (review_records 为空)。

        溯源链用关系表的顺序**覆盖** payload 里的 ``evidence_refs`` ——
        关系表是顺序的权威来源, 这样"读回来顺序变了"不可能发生。

        证据被删掉的知识点**不出现** (spec 50 硬规则)
        ----------------------------------------------

        "数据库加载也不得产生无证据的知识点"。知识点行本身还在库里 ——
        静默销毁数据同样是禁止的 —— 但它已经失去唯一来源, 不再满足"知识"
        的定义, 所以不能出现在课程知识视图里。

        判定方式必须是**课程无关**的证据存活检查, 不能退回"用证据归属推导
        课程"那套老办法 (两门课共用讲义时证据 id 相同, 那套推导无法区分
        课程, 实测会让每门课凭空少掉一半知识点)。
        """
        structure = KnowledgeStructure()
        points = self.load_all(course_id=course_id)
        # 溯源链**一次**取回全部知识点, 而不是每个知识点查一次 —— Task 69
        # 实测: 逐个查就是"每门课 600 条 SQL", 冷启动的语句条数被知识点数
        # 绑架。见 ``CourseLinkRepository.rights_for_many``。
        linked_map = (
            self.course_evidence.rights_for_many(
                course_id, [kp.knowledge_id for kp in points]
            )
            if course_id
            else {}
        )
        for kp in points:
            linked = (
                linked_map.get(kp.knowledge_id)
                if course_id
                else self.evidence_ids_for(kp.knowledge_id)
            )
            if linked:
                kp.evidence_refs = linked
            if kp.evidence_refs:
                kept = [
                    evidence_id
                    for evidence_id in kp.evidence_refs
                    if evidence_id in self._surviving_evidence()
                ]
                if not kept:
                    continue
                kp.evidence_refs = kept
            structure.knowledge_points[kp.knowledge_id] = kp
        for relationship in self.relationships.load_all():
            if (
                relationship.source_id in structure.knowledge_points
                and relationship.target_id in structure.knowledge_points
            ):
                structure.relationships.append(relationship)
                structure._relationship_keys.add(relationship._canonical_key())
        for conflict in self.conflicts.load_all(course_id=course_id):
            structure.conflicts.append(conflict)
            structure._conflict_ids.add(conflict.conflict_id)
        return structure

    def relationship_edges(self) -> list[tuple[str, str, str]]:
        """(source, target, relation_type) 三元组, 确定性排序。"""
        return [
            (source, target, relation_type)
            for source, target, relation_type, _rid in self.relationships.edges()
        ]


class RelationshipRepository(DocumentRepository):
    """知识点关系仓储 (RELATED_TO / PREREQUISITE_OF / TOPIC)。"""

    table = "relationships"

    def save(self, relationship: Relationship) -> None:
        if not isinstance(relationship, Relationship):
            raise TypeError(
                f"expected Relationship, got {type(relationship).__name__}"
            )
        self.put(
            {
                "relationship_id": relationship.relationship_id,
                "source_id": relationship.source_id,
                "target_id": relationship.target_id,
                "relation_type": relationship.relation_type.value,
            },
            relationship,
        )

    def save_many(self, relationships: Iterable[Relationship]) -> int:
        count = 0
        for relationship in relationships:
            self.save(relationship)
            count += 1
        return count

    def load(self, relationship_id: str) -> Optional[Relationship]:
        payload = self.get(relationship_id)
        if payload is None:
            return None
        return Relationship.from_dict(payload)

    def load_all(self) -> list[Relationship]:
        return [Relationship.from_dict(p) for p in self.all()]

    def edges(self) -> list[tuple[str, str, str, str]]:
        """(source, target, relation_type, relationship_id), 确定性排序。"""
        rows = self.rows(order_by="source_id, target_id, relation_type, relationship_id")
        return [
            (
                str(r["source_id"]),
                str(r["target_id"]),
                str(r["relation_type"]),
                str(r["relationship_id"]),
            )
            for r in rows
        ]

    def outgoing(self, knowledge_id: str) -> list[Relationship]:
        return [
            Relationship.from_dict(p)
            for p in self.all(
                where="source_id = ?", params=(knowledge_id,)
            )
        ]

    def incoming(self, knowledge_id: str) -> list[Relationship]:
        return [
            Relationship.from_dict(p)
            for p in self.all(
                where="target_id = ?", params=(knowledge_id,)
            )
        ]

    def of_type(self, relation_type: str) -> list[Relationship]:
        return [
            Relationship.from_dict(p)
            for p in self.all(
                where="relation_type = ?", params=(str(relation_type),)
            )
        ]


class CourseConflictRepository(DocumentRepository):
    """**每门课一份**的冲突表 (Task 68, migration 003)。

    ``ConflictRecord`` 领域对象没有 ``course_id`` 字段, 所以课程归属由
    **证据**推导 (见 migration 003 的回填语句), 这里只负责存。
    """

    table = "course_conflicts"


class ConflictRepository(DocumentRepository):
    """冲突仓储 (+ 冲突涉及证据链)。

    冲突表同样是全局主键 (``conflict_id`` 由两条证据派生, 不含课程),
    两门课出现同样的冲突会互相覆盖 —— 因此加了 ``course_conflicts``。
    """

    table = "conflicts"

    def __init__(self, database) -> None:
        super().__init__(database)
        self.evidence_links = LinkRepository(database, "conflict_evidence")
        self.course_rows = CourseConflictRepository(database)

    def save(
        self, conflict: ConflictRecord, *, course_id: Optional[str] = None
    ) -> None:
        if not isinstance(conflict, ConflictRecord):
            raise TypeError(
                f"expected ConflictRecord, got {type(conflict).__name__}"
            )
        if not conflict.conflict_id:
            raise ValueError("conflict must have a non-empty conflict_id")
        self.put(
            {
                "conflict_id": conflict.conflict_id,
                "description": conflict.description,
                "status": conflict.status.value,
            },
            conflict,
        )
        if course_id:
            self.course_rows.put(
                {
                    "course_id": course_id,
                    "conflict_id": conflict.conflict_id,
                    "description": conflict.description,
                    "status": conflict.status.value,
                },
                conflict,
            )
        self.evidence_links.replace(conflict.conflict_id, list(conflict.evidence_refs))

    def save_many(
        self, conflicts: Iterable[ConflictRecord], *, course_id: Optional[str] = None
    ) -> int:
        count = 0
        for conflict in conflicts:
            self.save(conflict, course_id=course_id)
            count += 1
        return count

    def load(
        self, conflict_id: str, *, course_id: Optional[str] = None
    ) -> Optional[ConflictRecord]:
        payload = (
            self.course_rows.get(course_id, conflict_id)
            if course_id
            else self.get(conflict_id)
        )
        if payload is None:
            return None
        return ConflictRecord.from_dict(payload)

    def load_all(self, *, course_id: Optional[str] = None) -> list[ConflictRecord]:
        payloads = (
            self.course_rows.all(where="course_id = ?", params=(course_id,))
            if course_id
            else self.all()
        )
        conflicts: list[ConflictRecord] = []
        for payload in payloads:
            conflict = ConflictRecord.from_dict(payload)
            linked = self.evidence_links.rights_for(conflict.conflict_id)
            if linked:
                conflict.evidence_refs = linked
            conflicts.append(conflict)
        return conflicts

    def evidence_ids_for(self, conflict_id: str) -> list[str]:
        return self.evidence_links.rights_for(conflict_id)

    def ids_with_status(self, status: str) -> list[str]:
        return list(self.keys(where="status = ?", params=(str(status),)))
