# -*- coding: utf-8 -*-
"""StudentRepository + 学习事件 / 学习状态 (Task 42)。

规范要求 "不得丢 student state"。学生的状态由三块组成, 缺一不可:

1. ``students``                    —— 学生身份 (display_name)
2. ``learning_events``             —— 追加式事件流 (Task 30 的输入)
3. ``student_knowledge_state``     —— 事件归约后的状态与计数

:func:`rebuild_student_log` 把三者拼回一个 ``StudentLearningLog``, 因此
"重启后学习状态完全一致"是可测的, 而不是靠信念。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

from src.persistence.repositories.base import DocumentRepository
from src.student_learning import (
    STUDENT_LEARNING_SCHEMA_VERSION,
    LearningEvent,
    Student,
    StudentKnowledgeRecord,
    StudentLearningLog,
)

__all__ = [
    "StudentRepository",
    "CourseStudentRepository",
    "LearningEventRepository",
    "StudentKnowledgeStateRepository",
    "rebuild_student_log",
]


class CourseStudentRepository(DocumentRepository):
    """**每门课一份**的学生表 (Task 68, migration 003)。

    ``students`` 的主键只有 ``student_id``: 两门课各注册一个 ``s-1`` 时,
    后写的会把先写的整行替换掉 (实测 3 门课只剩 1 行)。``course_students``
    把 ``course_id`` 放进主键, 是每门课各自的名册。
    """

    table = "course_students"


class StudentRepository(DocumentRepository):
    """学生身份仓储。

    ``course_id`` 是**拥有者课程** (LearningService 是按课程隔离的),
    它不参与 ``Student`` 领域对象的身份 —— 因此只写进规范列, 不写进
    payload, 读回来时由调用方按课程取。

    写: ``students`` (外键锚点) + ``course_students`` (按课程的真相)。
    读: 给了 ``course_id`` 一律走 ``course_students``。
    """

    table = "students"

    def __init__(self, database) -> None:
        super().__init__(database)
        self.course_rows = CourseStudentRepository(database)

    def save(self, student: Student, *, course_id: Optional[str] = None) -> None:
        if not isinstance(student, Student):
            raise TypeError(f"expected Student, got {type(student).__name__}")
        self.put(
            {
                "student_id": student.student_id,
                "course_id": course_id,
                "display_name": student.display_name,
            },
            student,
        )
        if course_id:
            self.course_rows.put(
                {
                    "course_id": course_id,
                    "student_id": student.student_id,
                    "display_name": student.display_name,
                },
                student,
            )

    def save_many(
        self, students: Iterable[Student], *, course_id: Optional[str] = None
    ) -> int:
        count = 0
        for student in students:
            self.save(student, course_id=course_id)
            count += 1
        return count

    def load(
        self, student_id: str, *, course_id: Optional[str] = None
    ) -> Optional[Student]:
        payload = (
            self.course_rows.get(course_id, student_id)
            if course_id
            else self.get(student_id)
        )
        if payload is None:
            return None
        return Student.from_dict(payload)

    def load_all(self, *, course_id: Optional[str] = None) -> list[Student]:
        if course_id is None:
            payloads = self.all()
        else:
            payloads = self.course_rows.all(
                where="course_id = ?", params=(course_id,)
            )
        return [Student.from_dict(p) for p in payloads]

    def course_of(self, student_id: str) -> Optional[str]:
        row = self.get_row(student_id)
        if row is None:
            return None
        value = row["course_id"]
        return None if value is None else str(value)

    def ids_for_course(self, course_id: str) -> list[str]:
        return list(self.keys(where="course_id = ?", params=(course_id,)))


class LearningEventRepository(DocumentRepository):
    """学习事件仓储 (追加式)。

    ``(student_id, course_id, knowledge_point_id, sequence)`` 上有 UNIQUE
    约束: 同一事件重复登记只会命中同一行 —— 幂等由数据库保证, 不只是
    由领域层的确定性 ``event_id`` 保证。
    """

    table = "learning_events"

    def save(self, event: LearningEvent) -> None:
        if not isinstance(event, LearningEvent):
            raise TypeError(
                f"expected LearningEvent, got {type(event).__name__}"
            )
        self.put(
            {
                "event_id": event.event_id,
                "student_id": event.student_id,
                "course_id": event.course_id,
                "knowledge_point_id": event.knowledge_point_id,
                "event_type": event.event_type.value,
                "sequence": int(event.sequence),
            },
            event,
        )

    def save_many(self, events: Iterable[LearningEvent]) -> int:
        count = 0
        for event in events:
            self.save(event)
            count += 1
        return count

    def load(self, event_id: str) -> Optional[LearningEvent]:
        payload = self.get(event_id)
        if payload is None:
            return None
        return LearningEvent.from_dict(payload)

    def load_all(self) -> list[LearningEvent]:
        return [LearningEvent.from_dict(p) for p in self.all()]

    def load_for_student(
        self, student_id: str, *, course_id: Optional[str] = None
    ) -> list[LearningEvent]:
        if course_id is None:
            payloads = self.all(
                where="student_id = ?",
                params=(student_id,),
                order_by="student_id, course_id, knowledge_point_id, sequence",
            )
        else:
            payloads = self.all(
                where="student_id = ? AND course_id = ?",
                params=(student_id, course_id),
                order_by="student_id, course_id, knowledge_point_id, sequence",
            )
        return [LearningEvent.from_dict(p) for p in payloads]

    def load_for_course(self, course_id: str) -> dict[str, list[LearningEvent]]:
        """一门课**全部**学生的事件流，一次查询拿回 (Task 69)。

        之前是"每个学生查一次" —— 一门课 500 个学生就是 500 条 SQL，而
        它们是同一个 ``WHERE course_id = ?`` 就能一次拿完的。顺序与
        :meth:`load_for_student` 完全一致 (按学生再按知识点与序)，所以
        换成批量之后重建出来的日志与逐学生读出来的**逐字节相同**。
        """
        payloads = self.all(
            where="course_id = ?",
            params=(course_id,),
            order_by="student_id, course_id, knowledge_point_id, sequence",
        )
        grouped: dict[str, list[LearningEvent]] = {}
        for payload in payloads:
            event = LearningEvent.from_dict(payload)
            grouped.setdefault(str(event.student_id), []).append(event)
        return grouped

    def sequence_of(self, event_id: str) -> Optional[int]:
        row = self.get_row(event_id)
        if row is None:
            return None
        return int(row["sequence"])

    def existing_ids(self, student_id: str) -> set[str]:
        """这个学生**已经落盘**的事件 id —— 一条 SQL 拿回 (Task 69)。

        事件是追加的、且 ``event_id`` 完全由内容派生（见
        :meth:`LearningEvent.create`）：同一个 ``event_id`` 的 payload
        永远相同。所以"跳过已落盘的事件"与"再 upsert 一遍"是**逐字节
        等价**的，不是近似。

        不做这一步的代价：``save_student_log`` 会把该学生**全部历史**
        逐条重写一遍 —— Task 69 实测，一个学生答到第 400 条时，提交下
        一条要 410 条 SQL；答到 20000 条就是 O(N²)，一个学生就能把整个
        学期拖死。
        """
        rows = self._db.query(
            f"SELECT event_id FROM {self.table} WHERE student_id = ?",
            (str(student_id),),
        )
        return {str(row["event_id"]) for row in rows}


class StudentKnowledgeStateRepository(DocumentRepository):
    """学生学习状态仓储 (Task 30 状态机结果)。"""

    table = "student_knowledge_state"

    def save(self, record: StudentKnowledgeRecord) -> None:
        if not isinstance(record, StudentKnowledgeRecord):
            raise TypeError(
                f"expected StudentKnowledgeRecord, got {type(record).__name__}"
            )
        self.put(
            {
                "student_id": record.student_id,
                "course_id": record.course_id,
                "knowledge_point_id": record.knowledge_point_id,
                "state": record.state.value,
                "first_seen_at": int(record.first_seen_at),
                "last_activity_at": int(record.last_activity_at),
                "exposure_count": int(record.exposure_count),
                "practice_count": int(record.practice_count),
                "answer_count": int(record.answer_count),
                "correct_count": int(record.correct_count),
                "incorrect_count": int(record.incorrect_count),
            },
            record,
        )

    def save_many(self, records: Iterable[StudentKnowledgeRecord]) -> int:
        count = 0
        for record in records:
            self.save(record)
            count += 1
        return count

    def load(
        self, student_id: str, course_id: str, knowledge_point_id: str
    ) -> Optional[StudentKnowledgeRecord]:
        payload = self.get(student_id, course_id, knowledge_point_id)
        if payload is None:
            return None
        return StudentKnowledgeRecord.from_dict(payload)

    def load_all(
        self,
        *,
        student_id: Optional[str] = None,
        course_id: Optional[str] = None,
    ) -> list[StudentKnowledgeRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if student_id is not None:
            clauses.append("student_id = ?")
            params.append(student_id)
        if course_id is not None:
            clauses.append("course_id = ?")
            params.append(course_id)
        where = " AND ".join(clauses) if clauses else None
        payloads = self.all(where=where, params=tuple(params))
        return [StudentKnowledgeRecord.from_dict(p) for p in payloads]

    def rows_for(self, student_id: str, course_id: str) -> dict[str, Any]:
        """这个学生在这门课**已经落盘**的状态行，按知识点索引 (Task 69)。

        状态会变（计数递增），不能像事件那样按 id 跳过。但一条 SQL 把现
        有行读回来比对之后，就只写**真的变了**的那几条 —— 一个学生注册了
        600 个知识点时，每条作答就不必再重写 600 行。
        """
        rows = self.rows(
            where="student_id = ? AND course_id = ?",
            params=(str(student_id), str(course_id)),
        )
        return {str(row["knowledge_point_id"]): row for row in rows}

    def state_of(
        self, student_id: str, course_id: str, knowledge_point_id: str
    ) -> Optional[str]:
        row = self.get_row(student_id, course_id, knowledge_point_id)
        if row is None:
            return None
        return str(row["state"])

    def states_for_student(
        self, student_id: str, *, course_id: Optional[str] = None
    ) -> dict[str, str]:
        """``{knowledge_point_id: state}`` (确定性顺序)。"""
        return {
            record.knowledge_point_id: record.state.value
            for record in self.load_all(student_id=student_id, course_id=course_id)
        }

    def load_for_course(self, course_id: str) -> dict[str, list[StudentKnowledgeRecord]]:
        """一门课**全部**学生的状态记录，一次查询拿回 (Task 69)。

        与 :meth:`LearningEventRepository.load_for_course` 同理：逐个学生查
        是 500 条 SQL，一次 ``WHERE course_id = ?`` 是 1 条。
        """
        grouped: dict[str, list[StudentKnowledgeRecord]] = {}
        for record in self.load_all(course_id=course_id):
            grouped.setdefault(str(record.student_id), []).append(record)
        return grouped


def rebuild_student_log(
    student: Student,
    course_id: str,
    *,
    events: Sequence[LearningEvent],
    states: Sequence[StudentKnowledgeRecord],
) -> StudentLearningLog:
    """把 (身份, 事件流, 状态记录) 拼回一个 ``StudentLearningLog``。

    这是"student state 不丢"的**唯一**重建入口: 走领域层自己的
    ``StudentLearningLog.from_dict``, 因此重建后的对象与直接构造的
    对象在行为上完全一致 (包括 ``registered_knowledge_points`` 与
    correct / incorrect 计数)。

    ``correct_counts`` / ``incorrect_counts`` 由状态记录提供 —— 它们是
    同一份事实的两种视图, 因此不可能出现"事件说 3 次对、状态说 2 次对"。
    """
    correct_counts: dict[str, int] = {}
    incorrect_counts: dict[str, int] = {}
    registered: list[str] = []
    for record in states:
        if record.course_id != course_id:
            continue
        key = f"{record.course_id}|{record.knowledge_point_id}"
        if record.answer_count or record.correct_count or record.incorrect_count:
            registered.append(key)
        correct_counts[key] = int(record.correct_count)
        incorrect_counts[key] = int(record.incorrect_count)
    for event in events:
        key = f"{event.course_id}|{event.knowledge_point_id}"
        if key not in registered:
            registered.append(key)

    return StudentLearningLog.from_dict(
        {
            "schema_version": STUDENT_LEARNING_SCHEMA_VERSION,
            "student": student.to_dict(),
            "events": [
                event.to_dict()
                for event in sorted(
                    events,
                    key=lambda e: (
                        e.course_id,
                        e.knowledge_point_id,
                        e.sequence,
                        e.event_id,
                    ),
                )
            ],
            "registered_kps": sorted(set(registered)),
            "correct_counts": correct_counts,
            "incorrect_counts": incorrect_counts,
        }
    )
