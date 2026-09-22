# -*- coding: utf-8 -*-
"""跨聚合的整体往返 (Task 42)。

有些东西**不是单表能表达的**, 必须跨仓储一起读写才算"没丢":

- ``EvidenceStore``      —— 证据本体 + 生命周期状态 + 插入顺序
- ``KnowledgeStructure`` —— 知识点 + 溯源链 + 关系 + 冲突 + **评审历史**
  (评审历史在另一个仓储里)
- ``StudentLearningLog`` —— 身份 + 事件流 + 状态记录

本模块只做**编排**: 它按正确的顺序调用仓储, 自己不发一条 SQL。
这样"整体往返"的正确性来自各仓储, 而不是来自这里的一堆临时语句。

为什么都用领域层自己的 ``to_dict`` / ``from_dict``
--------------------------------------------------------------------

因为领域层的 ``from_dict`` 带着**校验**: ``EvidenceStore.from_dict``
拒绝损坏快照, ``KnowledgeStructure.from_dict`` 拒绝悬空引用 / 重复实体 /
自环, ``LearningEvent.from_dict`` 校验 ``event_id`` 与内容一致。
走这些入口, 意味着"从数据库读回来"和"从文件读回来"受到的检查完全一样 ——
存储层不可能比文件层更宽松。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from src.evidence_store import EvidenceStore
from src.knowledge_organization import CourseKnowledgeStructure
from src.knowledge_review import ReviewRecord
from src.knowledge_structure import KnowledgeStructure
from src.models import Evidence
from src.persistence.repositories import Repositories
from src.persistence.repositories.student import rebuild_student_log
from src.student_learning import StudentLearningLog

__all__ = [
    "save_evidence_store",
    "evidence_store_snapshot",
    "load_evidence_store",
    "save_knowledge_structure",
    "load_knowledge_structure",
    "save_organization_structure",
    "load_organization_structure",
    "save_student_log",
    "load_student_log",
    "load_student_logs",
]


# ----------------------------------------------------------------------
# 证据库
# ----------------------------------------------------------------------


def save_evidence_store(repos: Repositories, store: EvidenceStore) -> int:
    """把内存证据库整体写进数据库 (幂等, 保留插入顺序与状态)。

    顺序即 ``insertion_seq`` —— Task 23 的查询依赖插入顺序, 而 SQLite
    不保证无 ``ORDER BY`` 的行序, 所以顺序必须显式落列。
    """
    snapshot = store.to_dict()
    count = 0
    for seq, payload in enumerate(snapshot.get("evidences") or ()):
        evidence = Evidence.from_dict(payload)
        state = str(payload.get("state") or "ACTIVE")
        repos.evidence.save(evidence, state=state, insertion_seq=seq)
        count += 1
    return count


def evidence_store_snapshot(repos: Repositories) -> dict[str, Any]:
    """重建 ``EvidenceStore.to_dict()`` 兼容的快照。

    返回的是**纯数据**, 交给 ``EvidenceStore.from_dict()`` 才能得到对象 ——
    这样状态校验仍然由领域层负责。
    """
    evidences: list[dict[str, Any]] = []
    states = repos.evidence.state_map()
    for evidence in repos.evidence.load_all():
        payload = evidence.to_dict()
        payload["state"] = states.get(evidence.evidence_id, "ACTIVE")
        evidences.append(payload)
    return {"schema_version": 1, "evidences": evidences}


def load_evidence_store(repos: Repositories) -> EvidenceStore:
    """从数据库重建一个内存 ``EvidenceStore`` (走领域层校验)。"""
    return EvidenceStore.from_dict(evidence_store_snapshot(repos))


# ----------------------------------------------------------------------
# 知识结构 (含评审历史)
# ----------------------------------------------------------------------


def save_knowledge_structure(
    repos: Repositories,
    structure: KnowledgeStructure,
    *,
    course_id: Optional[str] = None,
) -> None:
    """整体保存知识点 / 溯源链 / 关系 / 冲突 **+ 评审历史**。

    评审历史单独走 ``ReviewRepository`` —— 它属于审核域, 不属于知识域。
    """
    repos.knowledge.save_structure(structure, course_id=course_id)
    if structure.review_records:
        repos.reviews.save_many(
            list(structure.review_records), course_id=course_id
        )


def load_knowledge_structure(
    repos: Repositories, *, course_id: Optional[str] = None
) -> KnowledgeStructure:
    """整体重建 ``KnowledgeStructure`` (**含评审历史**)。

    评审历史只取**本结构里那些知识点**的记录。给了 ``course_id`` 时还
    会限定课程 —— 同一个 ``review_id`` 可能属于两门课 (见
    ``CourseReviewRepository`` 的注释), 不过滤就会把别的课程的评审挂进来,
    让整体往返多出不属于它的数据。

    过滤后按 ``review_id`` 排序, 与
    ``KnowledgeStructure.review_records_for_knowledge_point`` 的排序规则
    一致, 因此重启前后"某知识点的评审历史"顺序相同。
    """
    structure = repos.knowledge.load_structure(course_id=course_id)
    records = repos.reviews.load_for_knowledge_points(
        structure.knowledge_points, course_id=course_id
    )
    for record in records:
        structure.review_records.append(record)
        if record.review_id:
            structure._review_ids.add(record.review_id)
    return structure


def save_organization_structure(
    repos: Repositories, structure: CourseKnowledgeStructure
) -> None:
    """保存课程知识组织结构 (主题 / 归属 / 关系图)。"""
    repos.organization.save_structure(structure)


def load_organization_structure(
    repos: Repositories, course_id: str
) -> CourseKnowledgeStructure:
    return repos.organization.load_structure(course_id)


# ----------------------------------------------------------------------
# 学生学习日志
# ----------------------------------------------------------------------


def save_student_log(
    repos: Repositories, log: StudentLearningLog, *, course_id: str
) -> None:
    """保存一个学生的学习状态 (身份 + 事件流 + 状态记录)。

    状态记录由 ``log.derive_state()`` 派生 —— 也就是**领域层自己的归约**,
    而不是存储层重新算一遍。这样"事件说 3 次对、状态说 3 次对"是结构性
    保证, 不是巧合。

    这里只写**新增/变更**的部分, 不把整段历史重放一遍 (Task 69):

    - 事件: 追加且不可变, ``event_id`` 由内容派生 —— 已落盘的跳过与再
      upsert 一遍逐字节等价。不跳过的话, 每条作答都会重写该学生全部
      历史: 实测答到第 400 条时提交下一条要 410 条 SQL。
    - 状态: 会变, 所以读回来逐个比对, 只写真的变了的那些。
    """
    student = log.student
    sid = student.student_id
    repos.students.save(student, course_id=course_id)

    known_events = repos.learning_events.existing_ids(sid)
    for event in log.all_events():
        if event.event_id in known_events:
            continue
        repos.learning_events.save(event)

    stored_states = repos.student_states.rows_for(sid, course_id)
    for cid, kp_id in sorted(log.registered_knowledge_points):
        if cid != course_id:
            continue
        record = log.derive_state(cid, kp_id)
        if _same_state(stored_states.get(kp_id), record):
            continue
        repos.student_states.save(record)


_STATE_COLUMNS = (
    "state",
    "first_seen_at",
    "last_activity_at",
    "exposure_count",
    "practice_count",
    "answer_count",
    "correct_count",
    "incorrect_count",
)


def _same_state(stored: Any, record: Any) -> bool:
    """库里那一行与将要写入的这行是否**完全一致**。

    比的是 ``StudentKnowledgeStateRepository.save`` 真正会写进去的那些列,
    不是 payload —— 只比 payload 会把"计数变了但 payload 没变"的情况漏掉,
    也会把"payload 编码差异"误判成变更（后者只会多写, 不会丢数据）。
    """
    if stored is None:
        return False
    for column in _STATE_COLUMNS:
        value = getattr(record, column, None)
        value = value.value if hasattr(value, "value") else value
        current = stored[column]
        try:
            if int(current) != int(value):  # type: ignore[arg-type]
                return False
        except (TypeError, ValueError):
            if str(current) != str(value):
                return False
    return True


def load_student_log(
    repos: Repositories, student_id: str, *, course_id: str
) -> Optional[StudentLearningLog]:
    """重建一个学生的学习状态; 学生不存在返回 ``None``。"""
    student = repos.students.load(student_id, course_id=course_id)
    if student is None:
        return None
    events = repos.learning_events.load_for_student(student_id, course_id=course_id)
    states = repos.student_states.load_all(student_id=student_id, course_id=course_id)
    return rebuild_student_log(student, course_id, events=events, states=states)


def load_student_logs(
    repos: Repositories, *, course_id: str
) -> list[StudentLearningLog]:
    """重建一门课**全部**学生的学习状态 (Task 69)。

    单学生版本 :func:`load_student_log` 每个学生要 3 条 SQL (身份 + 事件
    + 状态)。一门课 500 个学生就是 1500 条 —— 而这三条全都是
    ``WHERE course_id = ?`` 能一次拿完的。这里改成 3 条 SQL + 内存分组,
    重建结果必须与逐学生读取**逐个字段相同** (顺序也相同, 见两个仓储的
    ``load_for_course``)。
    """
    students = repos.students.load_all(course_id=course_id)
    events_by_student = repos.learning_events.load_for_course(course_id)
    states_by_student = repos.student_states.load_for_course(course_id)
    logs: list[StudentLearningLog] = []
    for student in students:
        sid = str(student.student_id)
        logs.append(
            rebuild_student_log(
                student,
                course_id,
                events=events_by_student.get(sid, ()),
                states=states_by_student.get(sid, ()),
            )
        )
    return logs
