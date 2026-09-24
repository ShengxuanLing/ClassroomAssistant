# -*- coding: utf-8 -*-
"""业务对象 <-> SQLite 的接线层 (Task 48-55)。

背景: 为什么需要这个文件
--------------------------------------------------------------------

Task 42 已经把 ``src/persistence`` 建好了 (14 个仓储, 385 条测试), Task 44
也已经按配置打开并迁移数据库。但**业务对象从来没有被写进那些表**::

    创建课程 -> 内存 -> 关闭程序 -> 数据消失

本模块就是那条缺失的接线: 它把 Application Service 持有的业务对象
(``Course`` / ``ClassSession`` / 材料注册表 / ``EvidenceStore`` /
``KnowledgeStructure`` / ``ReviewRecord`` / ``StudentLearningLog`` /
``Exercise`` / ``StudentAnswer`` / ``EvaluationResult`` /
``StudyPlan`` / ``LearningPath``) 与 SQLite 之间的映射**集中在一处**。

分层位置
--------------------------------------------------------------------

::

    Web UI / HTTP API
            |
    Application Services  (course_service / learning_service / ...)
            |
    persistence_wiring    <- 本文件 (唯一知道 SQLite 存在的应用层模块)
            |
    src/persistence       <- 仓储, 只认领域对象, 不认识应用服务
            |
    SQLite

为什么单独一个文件而不是散进各业务服务
--------------------------------------------------------------------

``tests/test_persistence_layering.py`` 逐**文件**钉住"应用层里谁可以 import
``src.persistence``"。把接线收在一个文件里, 让"谁依赖存储层"这件事一眼可见:
新增业务服务一个都不许碰存储, 而这个文件本身就是那个被登记的例外。

设计原则
--------------------------------------------------------------------

1. **SQLite 是业务数据的 source of truth。** 内存对象只是当前操作对象 /
   缓存 / 事务工作集。每个写操作都会落盘。
2. **复用领域层的 ``to_dict`` / ``from_dict``。** 读回对象一律走领域层的
   校验入口, 因此"从数据库读回来"与"从文件读回来"受的检查完全一样。
3. **不重写 repository。** 本模块只做编排: 按正确顺序调用仓储, 自己不发
   一条 SQL。
4. **课程隔离在加载时显式做。** 没有 ``course_id`` 列的实体
   (``ConflictRecord`` / ``ReviewRecord``) 必须按课程过滤 —— 领域层的
   ``from_dict`` 查不出来 (它手里只有一份 payload)。
5. **损坏的数据库必须报错, 绝不静默新建空库。** 见
   :meth:`WorkspacePersistence.open`。
6. **一次业务操作 = 一个事务 (Task 53)。** 每个写方法都跑在
   :meth:`WorkspacePersistence.atomic` 里: 要么这个操作涉及的所有表
   全部更新, 要么一行都不动。见该方法的 docstring。
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Sequence

from src.answer_evaluation import (
    AnswerEvaluationLog,
    EvaluationResult,
    ExactEvaluator,
    StudentAnswer,
)
from src.evidence_store import EvidenceStore
from src.exercises import Exercise
from src.knowledge_organization import CourseKnowledgeStructure
from src.knowledge_review import ReviewRecord
from src.knowledge_structure import KnowledgeStructure
from src.models import ClassSession, Course, Evidence
from src.student_learning import Student, StudentLearningLog
from src.study_plan import LearningPath, StudyPlan

from src.application.errors import ApplicationError, StorageError
from src.persistence import Database, Repositories, open_database
from src.persistence.errors import PersistenceError
from src.persistence.repositories.student import rebuild_student_log
from src.persistence.snapshot import evidence_store_snapshot
from src.persistence.snapshot import save_knowledge_structure as _save_knowledge
from src.persistence.snapshot import save_organization_structure as _save_organization
from src.persistence.snapshot import load_student_logs as _load_student_logs
from src.persistence.snapshot import save_student_log as _save_student_log

__all__ = [
    "DEFAULT_DATABASE_FILENAME",
    "FAULT_POINTS",
    "FaultInjector",
    "WorkspacePersistence",
]

#: 与 ``src.application.config.DEFAULT_DATABASE_FILENAME`` 保持一致。
#: 这里**复述**而不是 import 配置层: 配置层是"说明 data_dir 在哪"的地方,
#: 让存储接线反过来依赖它会把两层的职责搅在一起。
DEFAULT_DATABASE_FILENAME = "classroom.sqlite"

#: 失败注入点 (Task 53)。
#:
#: 名字是**落盘步骤**, 不是表名 —— 一个步骤可能写多张表 (例如
#: ``"material"`` 同时写 ``materials`` + ``material_processing`` +
#: ``material_evidence``)。这样"在第 3 条证据之后炸掉"这种话才能说得出来。
#:
#: 这些名字是**契约**: ``tests/test_persistence_transactions.py`` 逐条遍历
#: 它们, 在每个点上注入失败并断言数据库一个字节都没变。
FAULT_POINTS: tuple[str, ...] = (
    "course",
    "session",
    "material",
    "evidence",
    "knowledge_structure",
    "review",
    "organization",
    "student_log",
    "exercise",
    "answer",
    "evaluation",
    "study_plan",
    "learning_path",
)

#: 失败注入器: ``(point, ordinal) -> None``。
#:
#: - ``point`` 是 :data:`FAULT_POINTS` 里的一个名字;
#: - ``ordinal`` 是**本次业务操作内**第几次落盘 (从 1 开始计)。
#:
#: 注入器想模拟失败就抛异常。抛出的异常会被 :meth:`atomic` 当作真实失败
#: 处理: 整个操作回滚, 异常向调用方传播。**测试专用** —— 生产代码永远不装
#: 它 (默认为 ``None``)。
FaultInjector = Callable[[str, int], None]


def _material_ids_for_course(records: Iterable[Mapping[str, Any]], course_id: str) -> set[str]:
    return {
        str(record.get("material_id"))
        for record in records
        if record.get("material_id") and str(record.get("course_id")) == course_id
    }


class WorkspacePersistence:
    """一个 data_dir 的业务对象持久化门面。

    典型用法::

        store = WorkspacePersistence.open("data/database/classroom.sqlite")
        store.save_course(course)
        ...
        store.close()
    """

    def __init__(
        self,
        database: Database,
        *,
        clock: Optional[Any] = None,
        owns_database: bool = False,
        fault_injector: Optional[FaultInjector] = None,
    ) -> None:
        if not isinstance(database, Database):
            raise TypeError(
                f"database must be a Database, got {type(database).__name__}"
            )
        self._db = database
        self._repos = Repositories(database)
        self._clock = clock
        self._owns_database = bool(owns_database)
        self._fault_injector: Optional[FaultInjector] = fault_injector
        # 本次业务操作内已经成功落盘的次数 (``_write`` 递增, ``atomic`` 归零)。
        self._write_ordinal = 0
        # 回滚诊断: 只留最近若干条, 但计数是完整的 (见 rollback_diagnostics)。
        self._rollback_count = 0
        self._recent_rollbacks: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # 构造 / 生命周期
    # ------------------------------------------------------------------

    @classmethod
    def open(
        cls,
        path: str,
        *,
        clock: Optional[Any] = None,
        busy_timeout_ms: int = 5000,
        fault_injector: Optional[FaultInjector] = None,
    ) -> "WorkspacePersistence":
        """打开 (必要时创建) 数据库并迁移到最新 schema。

        打开失败 (文件损坏 / 不是 SQLite 库 / 只读) 一律**原样抛出**
        ``PersistenceError`` —— 绝不吞掉异常去新建一个空库。用户看到
        "数据没了"远比看到一条明确的错误更糟。
        """
        database = open_database(path, clock=clock, busy_timeout_ms=busy_timeout_ms)
        return cls(
            database,
            clock=clock,
            owns_database=True,
            fault_injector=fault_injector,
        )

    @classmethod
    def for_database(
        cls,
        database: Database,
        *,
        clock: Optional[Any] = None,
        fault_injector: Optional[FaultInjector] = None,
    ) -> "WorkspacePersistence":
        """复用一个已打开的 ``Database`` (组合根场景: 全进程一个连接)。"""
        return cls(
            database,
            clock=clock,
            owns_database=False,
            fault_injector=fault_injector,
        )

    @property
    def database(self) -> Database:
        return self._db

    @property
    def repositories(self) -> Repositories:
        return self._repos

    @property
    def path(self) -> str:
        return self._db.path

    def close(self) -> None:
        """关闭数据库 (只在自己拥有连接时)。可重复调用。"""
        if self._owns_database and not self._db.closed:
            self._db.close()

    # ------------------------------------------------------------------
    # 事务 (Task 53)
    # ------------------------------------------------------------------

    def transaction(self, *, immediate: bool = True):
        """跨仓储的原子操作上下文 (要么全成, 要么全不成)。"""
        return self._repos.transaction(immediate=immediate)

    @contextmanager
    def atomic(self) -> Iterator[None]:
        """一次业务操作的落盘边界: **要么全成, 要么一行都不动**。

        为什么需要它
        ------------------------------------------------------------------

        一个业务操作常常跨好几张表。``process_material`` 依次写证据、材料、
        知识点、组织、学习 —— 如果第 4 步失败, 前 3 步已经提交的话, 数据库
        里就留下了一个**半成品**: 有证据但没有知识点, 或者有知识点但组织层
        不知道它们属于哪节课。用户重启后会看到一个"看起来正常但内容缺失"的
        工作区, 而没有任何错误可追。

        把整次操作包进一个事务, 半成品就**不可能**存在于数据库中 —— 这是
        结构性的, 不依赖谁记得写补偿逻辑。

        失败时怎么办
        ------------------------------------------------------------------

        1. 事务回滚 (由 :meth:`Database.transaction` 保证)。
        2. 异常**原样向上传播**, 绝不吞掉:
           - ``ApplicationError`` 原样重抛 (领域错误已经带着正确的错误码);
           - ``PersistenceError`` 映射为 ``StorageError`` (8 码规范之一),
             并把底层异常挂在 ``cause`` 上供日志使用;
           - 其他异常原样重抛 (可能是注入的模拟故障, 也可能是真 bug)。
        3. 记一条回滚诊断 (计数 + 最近若干条), 供 ``health()`` 报告。

        注意内存状态**不会**被回滚: 领域对象是在事务之外被修改的。因此失败
        的操作对调用方来说是**失败**的 (异常), 而数据库保持在操作前的状态。
        两者的交汇点就是"重启" —— 重启后内存从数据库重建, 于是自动回到那个
        一致的过去。这条性质由 ``tests/test_persistence_transactions.py``
        用真实重启验收。
        """
        previous = self._write_ordinal
        self._write_ordinal = 0
        try:
            with self._repos.transaction():
                yield
        except BaseException as exc:  # noqa: BLE001 - 一律回滚并如实上报
            self._record_rollback(exc)
            mapped = self._as_application_error(exc)
            if mapped is exc:
                raise
            raise mapped from exc
        finally:
            self._write_ordinal = previous

    def _write(self, point: str, action: Callable[[], Any]) -> Any:
        """执行一次落盘, 并在**成功之后**给出注入点。

        注入点放在成功之后而不是之前, 是为了让"第 N 次落盘已经完成、第 N+1
        次还没开始"这个中间状态可以被精确地构造出来 —— 只有在这种状态下
        才能验证"已写入的那部分会被回滚"。
        """
        result = action()
        self._write_ordinal += 1
        injector = self._fault_injector
        if injector is not None:
            injector(point, self._write_ordinal)
        return result

    def _as_application_error(self, exc: BaseException) -> BaseException:
        """把存储层异常映射为结构化错误; 其他异常原样返回。"""
        if isinstance(exc, ApplicationError):
            return exc
        if isinstance(exc, PersistenceError):
            return StorageError(
                f"write failed and was rolled back: {exc}",
                detail={"operation_rolled_back": True},
                cause=exc,
            )
        return exc

    def _record_rollback(self, exc: BaseException) -> None:
        self._rollback_count += 1
        entry = {
            "error_type": type(exc).__name__,
            "error_code": str(getattr(exc, "code", "") or ""),
            "writes_before_failure": self._write_ordinal,
        }
        self._recent_rollbacks.append(entry)
        if len(self._recent_rollbacks) > 20:
            del self._recent_rollbacks[0]

    def rollback_diagnostics(self) -> dict[str, Any]:
        """回滚诊断 (计数 + 最近若干条), 供 ``health()`` 报告。"""
        return {
            "count": self._rollback_count,
            "recent": [dict(e) for e in self._recent_rollbacks],
        }

    # ------------------------------------------------------------------
    # 失败注入 (Task 53, 测试专用)
    # ------------------------------------------------------------------

    @property
    def fault_injector(self) -> Optional[FaultInjector]:
        return self._fault_injector

    def set_fault_injector(self, injector: Optional[FaultInjector]) -> None:
        """安装 / 卸下失败注入器。``None`` 表示正常生产行为。"""
        if injector is not None and not callable(injector):
            raise TypeError("fault_injector must be callable or None")
        self._fault_injector = injector

    @property
    def write_ordinal(self) -> int:
        """本次业务操作内已成功落盘的次数 (不在操作中时为 0)。"""
        return self._write_ordinal

    # ------------------------------------------------------------------
    # 课程 / 课堂
    # ------------------------------------------------------------------

    def save_course(self, course: Course) -> None:
        self._write("course", lambda: self._repos.courses.save(course))

    def save_courses(self, courses: Iterable[Course]) -> int:
        count = 0
        for course in courses:
            self._write("course", lambda c=course: self._repos.courses.save(c))
            count += 1
        return count

    def load_courses(self) -> list[Course]:
        return self._repos.courses.load_all()

    def save_session(self, session: ClassSession) -> None:
        self._write("session", lambda: self._repos.sessions.save(session))

    def load_sessions(self) -> list[ClassSession]:
        return self._repos.sessions.load_all()

    # ------------------------------------------------------------------
    # 材料注册表
    # ------------------------------------------------------------------

    def save_material_records(
        self, records: Iterable[Mapping[str, Any]]
    ) -> int:
        """保存材料注册表记录 (**含处理状态**, 同一事务内)。

        材料记录与处理状态分两张表 (Task 42 刻意这么设计, 用于验证跨表
        原子性)。两条写入必须在同一个事务里, 否则会出现"注册了但没有
        状态"或反之 —— 那正是规范禁止的孤儿数据。
        """
        pending = [dict(r) for r in records if r.get("material_id")]
        if not pending:
            return 0
        # 内层事务保留: 本方法也可以被**单独**调用 (没有外层 ``atomic``),
        # 那时"记录 + 处理状态"仍然必须原子。嵌套在 SQLite 里是 SAVEPOINT,
        # 内层 RELEASE 不构成提交 —— 外层回滚时它照样被撤销。
        with self._repos.transaction():
            for record in pending:
                material_id = str(record["material_id"])

                def _save_one(record=record, material_id=material_id) -> None:
                    self._repos.materials.save_record(record)
                    self._repos.materials.processing.save(record)
                    self._repos.materials.replace_evidence(
                        material_id, record.get("evidence_ids") or ()
                    )

                self._write("material", _save_one)
        return len(pending)

    def load_material_records(self, course_id: Optional[str] = None) -> list[dict[str, Any]]:
        return self._repos.materials.load_records(course_id=course_id)

    def material_ids(self) -> list[str]:
        return self._repos.materials.keys()

    def delete_material_record(self, material_id: str) -> bool:
        """删除一条材料数据库行 (**真实删除**, 材料页「删除」用)。

        ``material_processing`` / ``material_evidence`` 对 ``materials``
        有 ``ON DELETE CASCADE`` 外键 (migration 001), 因此删掉主行后
        处理状态与溯源链自动消失 —— 这里只删主行, 不手写 DELETE。
        证据行本身**不动**: 证据是内容寻址的共享资产, 退休与否由工作区
        层决定 (与 :meth:`save_evidence_store` 的写穿语义一致)。

        注册表里已经没有这条记录时返回 False (幂等重删安全)。
        """
        return bool(self._repos.materials.delete(material_id))

    def set_evidence_states(self, states: Mapping[str, str]) -> int:
        """显式更新一批证据的生命周期状态 (RETIRED 回写)。

        ``save_evidence_store`` 是增量写入 (只补新行), 已存在证据的
        状态**变化**它看不见 —— 否则每次摄取都要重写全库。退休走这里:
        逐行 UPDATE, 与内存里的状态机 (ACTIVE/RETIRED) 一一对应。
        返回真正更新的行数。
        """
        written = 0
        for evidence_id, state in states.items():
            if state not in ("ACTIVE", "RETIRED"):
                raise ValueError(f"unknown evidence state: {state!r}")

            def _set_one(evidence_id=evidence_id, state=state) -> None:
                self._db.execute(
                    "UPDATE evidence SET state = ? WHERE evidence_id = ?",
                    (state, evidence_id),
                )

            self._write("evidence_state", _set_one)
            written += 1
        return written

    # ------------------------------------------------------------------
    # 证据
    # ------------------------------------------------------------------

    def save_evidence_store(self, store: EvidenceStore) -> int:
        """把内存证据库增量写入数据库 (保留插入顺序与生命周期状态)。

        为什么增量: 证据库是全课程共享的, 每次摄取后整体重写会让"处理一节课"
        的代价随历史证据数线性增长。这里只写**数据库里还没有的**记录,
        因此单次摄取的成本只取决于新增证据数。
        """
        snapshot = store.to_dict()
        evidences = list(snapshot.get("evidences") or ())
        if not evidences:
            return 0
        existing = set(self._repos.evidence.insertion_order())
        next_seq = self._repos.evidence.next_insertion_seq()
        written = 0
        for payload in evidences:
            evidence = Evidence.from_dict(payload)
            if evidence.evidence_id in existing:
                continue
            state = str(payload.get("state") or "ACTIVE")
            seq = next_seq

            def _save_one(evidence=evidence, state=state, seq=seq) -> None:
                self._repos.evidence.save(evidence, state=state, insertion_seq=seq)

            self._write("evidence", _save_one)
            existing.add(evidence.evidence_id)
            next_seq += 1
            written += 1
        return written

    def load_evidence_store(self) -> EvidenceStore:
        return EvidenceStore.from_dict(evidence_store_snapshot(self._repos))

    def evidence_rows(self) -> list[Evidence]:
        return self._repos.evidence.load_all()

    # ------------------------------------------------------------------
    # 知识 (知识点 / 冲突 / 关系 / 评审历史)
    # ------------------------------------------------------------------

    def save_knowledge_structure(
        self, structure: KnowledgeStructure, *, course_id: str
    ) -> None:
        # 知识点 / 溯源链 / 关系 / 冲突是一次跨表写入, 只给一个注入点 ——
        # 这是 wiring 层能给出的**最深**粒度而不必把仓储的内部顺序抄一遍
        # (抄一遍就等于把"顺序"变成两份, 迟早漂移)。
        self._write(
            "knowledge_structure",
            lambda: _save_knowledge(self._repos, structure, course_id=course_id),
        )

    def load_knowledge_structure(self, course_id: str) -> KnowledgeStructure:
        """按课程重建 ``KnowledgeStructure`` (**含冲突与评审历史**)。

        课程隔离由**存储层的主键**保证, 不是靠读回来再筛:

        - 知识点 / 溯源链走 ``course_knowledge_points`` 与
          ``course_knowledge_evidence`` (主键含 ``course_id``);
        - 冲突走 ``course_conflicts``;
        - 评审历史走 ``course_review_records``。

        为什么不再用"证据归属"来推导课程 (Task 68 之前的做法)
        ------------------------------------------------------

        旧的 ``knowledge_points`` 主键只有 ``knowledge_id``, 两门课共享
        同一个知识点时后写的会**整行覆盖**先写的, 于是只能靠"这条知识点
        的证据是不是本课程的"来猜它属于谁。那个推导在两门课用了同一份
        讲义时**根本无法区分** (证据 id 也一样), 实测结果是每门课凭空
        少掉一半知识点。现在课程是键的一部分, 不需要猜。
        """
        structure = self._repos.knowledge.load_structure(course_id=course_id)
        records = self._repos.reviews.load_for_knowledge_points(
            structure.knowledge_points, course_id=course_id
        )
        for record in records:
            structure.review_records.append(record)
            if record.review_id:
                structure._review_ids.add(record.review_id)
        return structure

    def load_all_review_records(self) -> list[ReviewRecord]:
        return self._repos.reviews.load_all()

    def save_review_records(
        self, records: Iterable[ReviewRecord], *, course_id: Optional[str] = None
    ) -> int:
        count = 0
        for record in records:
            self._write(
                "review",
                lambda r=record: self._repos.reviews.save_many(
                    [r], course_id=course_id
                ),
            )
            count += 1
        return count

    def save_organization_structure(self, structure: CourseKnowledgeStructure) -> None:
        self._write(
            "organization",
            lambda: _save_organization(self._repos, structure),
        )

    def load_organization_structure(self, course_id: str) -> CourseKnowledgeStructure:
        return self._repos.organization.load_structure(course_id)

    # ------------------------------------------------------------------
    # 学生 / 学习状态
    # ------------------------------------------------------------------

    def save_student_log(self, log: StudentLearningLog, *, course_id: str) -> None:
        self._write(
            "student_log",
            lambda: _save_student_log(self._repos, log, course_id=course_id),
        )

    def load_student_log(
        self, student_id: str, *, course_id: str
    ) -> Optional[StudentLearningLog]:
        return load_student_log_for(self._repos, student_id, course_id=course_id)

    def load_student_logs(self, course_id: str) -> list[StudentLearningLog]:
        """一门课全部学生的日志，批量读 (Task 69)。

        逐个学生读是 3 条 SQL/学生 —— 500 个学生 1500 条。批量版是 3 条。
        """
        return _load_student_logs(self._repos, course_id=course_id)

    def load_students(self, course_id: str) -> list[Student]:
        return self._repos.students.load_all(course_id=course_id)

    # ------------------------------------------------------------------
    # 练习 / 作答 / 评估
    # ------------------------------------------------------------------

    def save_exercises(self, exercises: Iterable[Exercise]) -> int:
        count = 0
        for exercise in exercises:
            self._write("exercise", lambda e=exercise: self._repos.exercises.save(e))
            count += 1
        return count

    def load_exercises(self, course_id: str) -> list[Exercise]:
        return self._repos.exercises.load_all(course_id=course_id)

    def save_answers(
        self,
        answers: Iterable[StudentAnswer],
        *,
        course_id: str,
        submitted_at: Optional[Mapping[str, str]] = None,
    ) -> int:
        count = 0
        for answer in answers:
            submitted = (submitted_at or {}).get(answer.answer_id)

            def _save_one(answer=answer, submitted=submitted) -> None:
                self._repos.answers.save(
                    answer, course_id=course_id, submitted_at=submitted
                )

            self._write("answer", _save_one)
            count += 1
        return count

    def load_answers(self, *, course_id: str) -> list[StudentAnswer]:
        return self._repos.answers.load_all(course_id=course_id)

    def submitted_at_map(self) -> dict[str, str]:
        return self._repos.answers.submitted_at_map()

    def save_evaluations(
        self,
        results: Iterable[EvaluationResult],
        *,
        answers: Mapping[str, StudentAnswer],
    ) -> int:
        """保存评估结果; ``exercise_id`` / ``student_id`` 从被评估的答案派生。

        这两个值不是新的领域事实 —— 它们只是"评估挂在哪个练习/学生上"的
        查询列, 因此从答案派生而不是让调用方去猜。
        """
        count = 0
        for result in results:
            answer = answers.get(result.answer_id)

            def _save_one(result=result, answer=answer) -> None:
                self._repos.evaluations.save(
                    result,
                    exercise_id=(answer.exercise_id if answer is not None else None),
                    student_id=(answer.student_id if answer is not None else None),
                )

            self._write("evaluation", _save_one)
            count += 1
        return count

    def load_evaluations(self, *, course_id: Optional[str] = None) -> list[EvaluationResult]:
        return self._repos.evaluations.load_all(course_id=course_id)

    def evaluation_ids(self) -> list[str]:
        return self._repos.evaluations.keys()

    def evaluation_by_answer(self, answer_id: str) -> Optional[EvaluationResult]:
        return self._repos.evaluations.for_answer(answer_id)

    # ------------------------------------------------------------------
    # 学习计划 / 学习路径
    # ------------------------------------------------------------------

    def save_study_plans(self, plans: Iterable[StudyPlan]) -> int:
        count = 0
        for plan in plans:
            self._write("study_plan", lambda p=plan: self._repos.study_plans.save(p))
            count += 1
        return count

    def load_study_plans(self, *, course_id: str) -> list[StudyPlan]:
        plans = self._repos.study_plans.load_all()
        return [p for p in plans if p.course_id == course_id]

    def save_learning_paths(
        self, paths: Iterable[tuple[LearningPath, str, str]]
    ) -> int:
        count = 0
        for path, course_id, student_id in paths:
            self._write(
                "learning_path",
                lambda p=path, c=course_id, s=student_id: (
                    self._repos.learning_paths.save(p, course_id=c, student_id=s)
                ),
            )
            count += 1
        return count

    def load_learning_paths(self, *, course_id: str) -> list[LearningPath]:
        """某课程的全部学习路径。

        ``LearningPath`` 的 payload 里没有 ``course_id`` (领域层没有这个
        字段, 也不该有 —— 它是 ``(课程, 学生, 目标知识点)`` 的派生视图),
        所以只能按**自然键的列**查询, 而不是加载全部再猜。
        """
        out: list[LearningPath] = []
        for row in self._repos.learning_paths.rows(
            where="course_id = ?", params=(course_id,)
        ):
            path = self._repos.learning_paths.load(
                str(row["course_id"]),
                str(row["student_id"]),
                str(row["target_knowledge_point_id"]),
            )
            if path is not None:
                out.append(path)
        return out

    def paths_for_student(self, course_id: str, student_id: str) -> list[LearningPath]:
        return self._repos.learning_paths.paths_for_student(course_id, student_id)

    def learning_path_rows(self, course_id: str) -> list[dict[str, Any]]:
        """原始行 (含自然键), 用于"这条路径属于谁"的确定性核对。"""
        return [
            dict(row)
            for row in self._repos.learning_paths.rows(
                where="course_id = ?", params=(course_id,)
            )
        ]

    # ------------------------------------------------------------------
    # 诊断
    # ------------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        return self._repos.counts()

    def integrity_check(self) -> str:
        return self._db.integrity_check()

    def foreign_key_violations(self) -> list[dict[str, Any]]:
        return self._db.foreign_key_violations()

    def schema_version(self) -> int:
        return self._db.schema_version()

    def table_names(self) -> list[str]:
        return self._db.table_names()


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def load_student_log_for(
    repos: Repositories, student_id: str, *, course_id: str
) -> Optional[StudentLearningLog]:
    """重建一个学生的学习状态 (复用仓储层的唯一重建入口)。"""
    student = repos.students.load(student_id, course_id=course_id)
    if student is None:
        return None
    events = repos.learning_events.load_for_student(student_id, course_id=course_id)
    states = repos.student_states.load_all(student_id=student_id, course_id=course_id)
    return rebuild_student_log(student, course_id, events=events, states=states)


def build_answer_log(
    exercises: Iterable[Exercise],
    payload: Optional[Mapping[str, Any]],
) -> AnswerEvaluationLog:
    """按已保存的答案日志 payload 重建 ``AnswerEvaluationLog``。

    ``ExactEvaluator`` 会**快照**练习目录, 所以重建时必须把当前课程的全部
    练习喂进去 —— 否则重启后提交新答案会得到 ``UNKNOWN_EXERCISE``。
    """
    catalogue = {e.exercise_id: e for e in exercises}
    log = AnswerEvaluationLog(ExactEvaluator(catalogue))
    if not payload:
        return log
    return AnswerEvaluationLog.from_dict(dict(payload), ExactEvaluator(catalogue))


def default_database_path(data_dir: str) -> str:
    """``<data_dir>/database/classroom.sqlite`` (与配置层的派生规则一致)。"""
    return os.path.join(os.path.abspath(data_dir), "database", DEFAULT_DATABASE_FILENAME)


__all__ += ["build_answer_log", "default_database_path", "load_student_log_for"]
