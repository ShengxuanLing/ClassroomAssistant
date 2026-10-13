# -*- coding: utf-8 -*-
"""表规格注册表 (Task 42)。

仓储基类需要知道"表名 / 主键列 / 默认确定性排序 / payload 版本", 但不该
把这些字符串散落在 12 个仓储文件里。集中在这里, 好处是:

- 表名只有一个来源, 打错表名会在**一处**暴露, 而不是在某个仓储里静默查错;
- 默认排序集中定义, 因此"同一个库读两次得到同一顺序"是全局性质,
  不需要每个仓储各自记得 ``ORDER BY``;
- 迁移新增表时, 若忘了登记, ``test_every_table_is_registered`` 会失败。

排序为什么重要
--------------------------------------------------------------------

规范要求确定性。SQLite 不保证无 ``ORDER BY`` 时的行序, 因此"读出来
顺序会变"是一个真实风险 —— 它会污染下游 (例如学习路径节点的顺序)。
所以每个规格都必须给出**全序**排序键 (末尾一定带主键, 消除并列)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

__all__ = [
    "TableSpec",
    "TABLES",
    "spec_for",
    "table_names",
    "LINK_TABLES",
    "COURSE_LINK_TABLES",
]


@dataclass(frozen=True)
class TableSpec:
    """一张表的元信息。

    ``order_by`` 必须是**全序**: 末尾包含主键, 否则并列行的顺序由
    SQLite 决定, 不满足确定性要求。
    """

    name: str
    pk: str
    order_by: str
    payload_version: int = 1
    #: 复合主键表的列名 (非空时 ``pk`` 仅作展示用)。
    pk_columns: tuple[str, ...] = ()

    @property
    def key_columns(self) -> tuple[str, ...]:
        return self.pk_columns or (self.pk,)


def _spec(name: str, pk: str, order_by: Optional[str] = None, **kw) -> TableSpec:
    return TableSpec(
        name=name,
        pk=pk,
        order_by=order_by or pk,
        **kw,
    )


#: 全部表规格 (含 link 表)。
TABLES: dict[str, TableSpec] = {
    # --- 课程 / 课堂 ---
    "courses": _spec("courses", "course_id"),
    "sessions": _spec("sessions", "session_id", "course_id, session_number, session_id"),
    # --- 材料 ---
    "materials": _spec("materials", "material_id"),
    "material_processing": _spec("material_processing", "material_id"),
    # --- 证据 ---
    "evidence": _spec("evidence", "evidence_id", "insertion_seq, evidence_id"),
    # --- 知识 ---
    "knowledge_points": _spec("knowledge_points", "knowledge_id"),
    "relationships": _spec("relationships", "relationship_id"),
    "conflicts": _spec("conflicts", "conflict_id"),
    "review_records": _spec(
        "review_records", "review_id", "knowledge_point_id, review_id"
    ),
    # --- 知识组织层 ---
    "topics": _spec("topics", "topic_id", "course_id, topic_id"),
    "knowledge_memberships": _spec("knowledge_memberships", "membership_id"),
    "session_memberships": _spec("session_memberships", "membership_id"),
    "knowledge_relations": _spec("knowledge_relations", "relation_id"),
    # --- 学生 ---
    "students": _spec("students", "student_id"),
    "learning_events": _spec(
        "learning_events",
        "event_id",
        "student_id, course_id, knowledge_point_id, sequence, event_id",
    ),
    "student_knowledge_state": TableSpec(
        name="student_knowledge_state",
        pk="student_id",
        pk_columns=("student_id", "course_id", "knowledge_point_id"),
        order_by="student_id, course_id, knowledge_point_id",
    ),
    # --- 练习 / 作答 / 评估 ---
    "exercises": _spec("exercises", "exercise_id"),
    "student_answers": _spec(
        "student_answers", "answer_id", "student_id, course_id, sequence, answer_id"
    ),
    "evaluation_results": _spec("evaluation_results", "evaluation_id"),
    # --- 计划 / 路径 ---
    "study_plans": _spec("study_plans", "plan_id"),
    "learning_paths": TableSpec(
        name="learning_paths",
        pk="course_id",
        pk_columns=("course_id", "student_id", "target_knowledge_point_id"),
        order_by="course_id, student_id, target_knowledge_point_id",
    ),
    # --- Task 68: 课程范围内的身份 (见 migration 003) ---
    #
    # 业务 ID 是内容寻址的, 两门课用同一份讲义会得到同一个 knowledge_id。
    # 上面那几张"全局主键"的表保存不了这件事 (后写的覆盖先写的), 于是
    # 每门课在下面这几张表里各存一份。主键一律以 course_id 开头 ——
    # 课程隔离是主键的一部分, 不是查询时的筛选项。
    "course_knowledge_points": TableSpec(
        name="course_knowledge_points",
        pk="knowledge_id",
        pk_columns=("course_id", "knowledge_id"),
        order_by="course_id, knowledge_id",
    ),
    "course_students": TableSpec(
        name="course_students",
        pk="student_id",
        pk_columns=("course_id", "student_id"),
        order_by="course_id, student_id",
    ),
    "course_review_records": TableSpec(
        name="course_review_records",
        pk="review_id",
        pk_columns=("course_id", "review_id"),
        order_by="course_id, knowledge_point_id, review_id",
    ),
    "course_conflicts": TableSpec(
        name="course_conflicts",
        pk="conflict_id",
        pk_columns=("course_id", "conflict_id"),
        order_by="course_id, conflict_id",
    ),
}

#: 多对多关系表 (只有 id 对 + position, 没有 payload)。
LINK_TABLES: dict[str, tuple[str, str, str]] = {
    # 表名: (左列, 右列, 排序键)
    "material_evidence": ("material_id", "evidence_id", "material_id, position, evidence_id"),
    "knowledge_point_evidence": (
        "knowledge_id",
        "evidence_id",
        "knowledge_id, position, evidence_id",
    ),
    "conflict_evidence": ("conflict_id", "evidence_id", "conflict_id, position, evidence_id"),
    "exercise_knowledge_points": (
        "exercise_id",
        "knowledge_id",
        "exercise_id, position, knowledge_id",
    ),
    "exercise_evidence": ("exercise_id", "evidence_id", "exercise_id, position, evidence_id"),
    # Task 68: 溯源链同样按课程分开 —— 见 CourseLinkRepository。
    # 左列是 (course_id, knowledge_id) 两列, 由专门的仓储处理。
    "course_knowledge_evidence": (
        "knowledge_id",
        "evidence_id",
        "course_id, knowledge_id, position, evidence_id",
    ),
}

#: 带 ``course_id`` 前缀列的关系表 (左键是 (course_id, left) 复合键)。
COURSE_LINK_TABLES: dict[str, str] = {
    "course_knowledge_evidence": "knowledge_id",
}


def spec_for(table: str) -> TableSpec:
    try:
        return TABLES[table]
    except KeyError as exc:  # pragma: no cover - guarded by tests
        raise KeyError(f"unknown table spec: {table!r}") from exc


def table_names() -> list[str]:
    """所有有 payload 的表名 (确定性顺序)。"""
    return sorted(TABLES)
