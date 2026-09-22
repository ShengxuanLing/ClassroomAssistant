# -*- coding: utf-8 -*-
"""多课程工作区 (Task 38)。

``AppService`` 是**单课程**门面; HTTP API 需要同时服务多门课程, 因此这里
提供一个 ``Workspace``: 一个应用进程内的唯一组合根 (composition root)。

共享与隔离:
- ``EvidenceStore`` 与 ``EvidenceIngestionService`` 全局共享 (证据是内容
  寻址的, 跨课程共用一份真相源)。
- ``KnowledgeOrganizationService`` **按课程隔离** (课程知识绝不混合)。
- 课程 / 课堂注册表由共享的 ``CourseService`` 管理。
- 材料注册表只有一处: 课程的 ``MaterialWorkflowService``。
  ``CourseService`` 的材料接口不在本工作区路径上使用, 避免双份真相源。

处理模式是**显式可见**的: ``asr_mode`` / ``ocr_mode`` 会出现在
``/api/health`` 里。绝不静默地把 Mock 当作真实 Whisper/OCR 使用。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Optional, Sequence

from src.application.classroom_view import ClassroomWorkspaceView
from src.application.course_review_view import CourseReviewView
from src.application.exercise_workflow import ExerciseWorkflow
from src.application.mistakes_view import MistakesView
from src.application.course_service import CourseService
from src.application.data_dirs import (
    DataLayout,
    atomic_write_bytes,
    ensure_data_layout,
    iter_files,
    remove_quietly,
    safe_join,
)
from src.application.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    StorageError,
    UnsupportedError,
)
from src.application.knowledge_service import KnowledgeService, ReviewService
from src.application.learning_service import LearningService
from src.application.learning_view import LearningViewService
from src.application.learning_workflow import LearningWorkflow
from src.application.review_mode import ReviewSet
from src.application.material_workflow import (
    MaterialWorkflowService,
    resolve_material_path,
)
from src.application.multi_course import MultiCourseWorkspace
from src.application.operation_log import OperationLog
from src.application.persistence_wiring import (
    WorkspacePersistence,
    build_answer_log,
    default_database_path,
)
from src.application.processing_service import (
    JOB_FAILED,
    JOB_SUCCEEDED,
    ClassroomProcessingService,
)
from src.application.runtime import Clock, utc_now_iso
from src.application.student_today_view import StudentTodayView
from src.evidence_ingestion import EvidenceIngestionService
from src.evidence_store import EvidenceStore
from src.knowledge_organization import (
    KnowledgeOrganizationService,
    KnowledgeRelationType,
)
from src.models import ClassSession, Course

__all__ = ["CourseContext", "Workspace", "APPLICATION_NAME", "APPLICATION_VERSION"]

APPLICATION_NAME = "Classroom Assistant"
APPLICATION_VERSION = "1.0.1"

#: TASK-77: 自动 AI 触发状态 (``process_material`` 返回作业字典里 ``ai``
#: 子对象的 ``status`` 取值)。这是对既有 ``JOB_SUCCEEDED / JOB_FAILED``
#: 作业模型的**扩展**, 不是第二套状态系统: 材料摄取状态机原样不动,
#: ``ai`` 只描述"摄取成功之后那次可选语义分析"的结局。
AI_AUTO_DISABLED = "disabled"
AI_AUTO_SKIPPED = "skipped"
AI_AUTO_COMPLETED = "completed"
AI_AUTO_FAILED = "failed"

#: TASK-77: AI 报告在数据目录里的持久化位置 (相对于 ``materials/``)。
#: 报告层可由 KP + Evidence 重新推导, 因此不新增数据库表 (与 TASK-76
#: "零 DB migration" 一致); 文件落盘只解决"重启后总结消失"的体验问题。
AI_REPORT_SUBDIR = "ai-reports"


def _sha256_file(path: str, *, chunk_size: int = 1024 * 1024) -> str:
    """流式计算文件哈希 (材料可能是几百 MB 的音频, 不能整个读进内存)。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


#: 查询串里表示真值的写法 (HTTPServer 只能给出字符串)。
_TRUE_LITERALS = frozenset({"1", "true", "yes", "on"})
_FALSE_LITERALS = frozenset({"0", "false", "no", "off"})


def _as_optional_bool(value: Any) -> Optional[bool]:
    """把查询串里的三态布尔串归一为 ``True`` / ``False`` / ``None``。

    ``None`` 表示"不筛选" —— 这与 ``False`` ("筛选出没有冲突的") 是两件事。
    无法识别的取值同样返回 ``None``, 因为"看不懂"时**不做筛选**比"猜一个
    布尔值然后把数据过滤掉"安全: 后者会让用户以为看到的是全部。
    """
    if value is None or isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE_LITERALS:
        return True
    if text in _FALSE_LITERALS:
        return False
    return None


@dataclass
class CourseContext:
    """一门课程的全部应用服务 (课程级隔离的知识状态)。"""

    course: Course
    org_service: KnowledgeOrganizationService
    workflow: MaterialWorkflowService
    processing: ClassroomProcessingService
    knowledge_service: KnowledgeService
    review_service: ReviewService
    learning_service: LearningService
    learning_view: LearningViewService
    review_view: CourseReviewView
    exercise_workflow: ExerciseWorkflow
    learning_workflow: LearningWorkflow
    review_mode: ReviewSet

    @property
    def course_id(self) -> str:
        return self.course.course_id


class Workspace:
    """应用级工作区: 课程注册表 + 共享证据/摄取 + 每课程上下文。

    **SQLite 是业务数据的 source of truth** (Task 48-55)。构造工作区时
    严格按下面的顺序恢复::

        Workspace(data_dir)
            ↓  open database
            ↓  migrate (先迁移, 再加载 —— 顺序不可颠倒)
            ↓  initialize repositories
            ↓  load persistent state (课程 / 课堂 / 材料 / 证据 / 知识 / 学习)
            ↓  initialize services

    ``close()`` 关闭数据库 (只在自己打开它时)。

    ``persistence=None`` 时自动在 ``<data_dir>/database/classroom.sqlite``
    上建一个; 传 ``persistence=False`` 可显式关闭持久化 (只用于纯内存的
    单元测试)。已有的内存实现没有被删除 —— 它现在只是**缓存**, 不再是
    唯一的数据来源。
    """

    def __init__(
        self,
        data_dir: str,
        *,
        source_root: Optional[str] = None,
        clock: Optional[Clock] = None,
        max_file_size: int = 200 * 1024 * 1024,
        max_attempts: int = 3,
        store: Optional[EvidenceStore] = None,
        ingestion_service: Optional[EvidenceIngestionService] = None,
        asr_provider: Optional[Any] = None,
        ocr_engine: Optional[Any] = None,
        asr_mode: Optional[str] = None,
        ocr_mode: Optional[str] = None,
        summarizer: Optional[Any] = None,
        llm_mode: Optional[str] = None,
        persistence: Optional[Any] = None,
        database_path: Optional[str] = None,
    ) -> None:
        if not isinstance(data_dir, str) or not data_dir.strip():
            raise InvalidInputError("data_dir must be a non-empty string")
        self._layout = ensure_data_layout(data_dir)
        self._clock: Clock = clock or utc_now_iso
        self._max_file_size = max_file_size
        self._max_attempts = max_attempts

        # 上传暂存区: HTTP 上传的字节先落在这里, 再由材料工作流复制进
        # 受管目录。暂存文件绝不长期保留。
        self._upload_root = safe_join(self._layout.temp, "uploads")
        self._source_root = source_root or self._upload_root

        # ---- 持久化 (Task 48) --------------------------------------
        # 顺序: open -> migrate -> repositories -> load -> services。
        # ``migrate()`` 在最新版库上是**只读**的 (见 Database.migrate 的
        # docstring), 因此"打开数据库"不会被长事务卡住。
        self._persistence: Optional[WorkspacePersistence] = None
        if persistence is False:
            self._persistence = None
        elif isinstance(persistence, WorkspacePersistence):
            self._persistence = persistence
        elif persistence is not None:
            raise InvalidInputError(
                "persistence must be a WorkspacePersistence, False or None, "
                f"got {type(persistence).__name__}"
            )
        else:
            path = database_path or default_database_path(self._layout.root)
            try:
                self._persistence = WorkspacePersistence.open(path, clock=self._clock)
            except Exception as exc:  # noqa: BLE001 - 存储层错误一律显式上报
                # 数据库存在但损坏 / 不是 SQLite 库 / 打不开: **必须报错**。
                # 静默新建一个空库会让用户以为"数据没了", 那是本阶段最不
                # 可接受的一种失败。
                raise StorageError(
                    f"cannot open the classroom database at {path!r}: {exc}",
                    detail={"database_path": path},
                    cause=exc,
                ) from exc

        self.course_service = CourseService()
        if store is not None:
            self.store = store
        elif self._persistence is not None:
            # 证据是内容寻址的, 跨课程共用一份真相源 —— 因此整库加载一次。
            self.store = self._persistence.load_evidence_store()
        else:
            self.store = EvidenceStore()
        self._asr_mode = asr_mode or ("real" if asr_provider is not None else "mock")
        self._ocr_mode = ocr_mode or ("real" if ocr_engine is not None else "mock")
        # §1: LLM 总结提供方 (默认 Mock, 绝不静默)。真实 provider 由
        # bootstrap 按显式 llm_mode 装配并注入; Workspace 自己绝不联网。
        self.summarizer = summarizer
        self._llm_mode = llm_mode or ("real" if summarizer is not None else "mock")
        # TASK-76: AI 语义理解管线 (默认关闭 + Fake provider, 零出站)。
        # 开启必须显式 configure_ai(enabled=True) —— 旧确定性链路不受影响。
        # 最近一次分析报告缓存在内存 (KP 本体经组织层正常落盘, 报告本身
        # 可由 KP + Evidence 重新推导, 不新增数据库表, 见 service.py)。
        self._ai_enabled = False
        self._ai_provider: Optional[Any] = None
        self._ai_reports: dict[tuple[str, str], dict[str, Any]] = {}
        if ingestion_service is not None:
            self.ingestion_service = ingestion_service
        else:
            if asr_provider is None:
                from src.asr_provider import MockASRProvider

                asr_provider = MockASRProvider()
            if ocr_engine is None:
                from src.ocr_processor import MockOCREngine

                ocr_engine = MockOCREngine()
            self.ingestion_service = EvidenceIngestionService(
                self.store, asr_provider=asr_provider, ocr_engine=ocr_engine
            )
        self._quality_source = asr_provider
        self._contexts: dict[str, CourseContext] = {}
        # Task 56: 真实课堂工作台的只读投影 (懒构造 —— 它只是转发, 无状态)。
        self._classroom: Optional[ClassroomWorkspaceView] = None
        # Task 63: 学生每日首页投影 (同上, 只读 + 无状态)。
        self._student_today: Optional[StudentTodayView] = None
        # Task 68: 多课程总览投影 (同上, 只读 + 无状态)。
        self._multi_course: Optional[MultiCourseWorkspace] = None
        # Task 71.7: 轻量级操作日志 (落在 <data_dir>/logs/, 不进备份归档)。
        # 记录真实 Pilot 使用中的关键操作, 便于事后排查真实世界问题。
        self._operation_log = OperationLog(self._layout.logs, clock=self._clock)
        self._closed = False
        self._restore_registries()

    # ------------------------------------------------------------------
    # 操作日志 (Task 71.7)
    # ------------------------------------------------------------------

    @property
    def operation_log(self) -> OperationLog:
        """真实 Pilot 操作日志 (追加写 JSON Lines, 隐私安全)。"""
        return self._operation_log

    def _record_op(
        self,
        operation: str,
        *,
        course: Optional[str] = None,
        session: Optional[str] = None,
        material: Optional[str] = None,
        success: bool = True,
        failure: Optional[str] = None,
        duration_ms: Optional[float] = None,
        **extra: Any,
    ) -> None:
        """记录一条操作; 任何失败都静默 (绝不干扰业务操作)。

        在 pytest 下禁用: 真实 Pilot 日志是"真实使用"才需要的产物, 测试套件
        里既不需要它, 也不能让它往受测的数据目录写文件 (否则会干扰那些严格
        比较数据目录树的回归断言)。``OperationLog`` 本身有独立单元测试, 另有一
        个集成测试会清除该环境变量显式验证 Workspace 确实落盘。
        """
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        self._operation_log.record(
            operation,
            course=course,
            session=session,
            material=material,
            success=success,
            failure=failure,
            duration_ms=duration_ms,
            **extra,
        )

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def layout(self) -> DataLayout:
        return self._layout

    @property
    def data_dir(self) -> str:
        return self._layout.root

    @property
    def upload_root(self) -> str:
        return self._upload_root

    @property
    def asr_mode(self) -> str:
        return self._asr_mode

    @property
    def ocr_mode(self) -> str:
        return self._ocr_mode

    @property
    def persistence(self) -> Optional[WorkspacePersistence]:
        """业务对象持久化门面 (None = 纯内存模式)。"""
        return self._persistence

    @property
    def database_path(self) -> Optional[str]:
        return None if self._persistence is None else self._persistence.path

    # ------------------------------------------------------------------
    # 生命周期 (Task 48)
    # ------------------------------------------------------------------

    def _restore_registries(self) -> None:
        """从 SQLite 恢复课程 / 课堂注册表 (幂等)。

        必须在任何课程上下文建立之前完成 —— 否则第一个 ``context()``
        会拿到一个空课程表, 于是"重启后课程消失"。
        """
        if self._persistence is None:
            return
        courses = self._persistence.load_courses()
        sessions = self._persistence.load_sessions()
        if not courses and not sessions:
            return
        self.course_service.load_state(courses=courses, sessions=sessions)

    def close(self) -> None:
        """刷出待写状态并关闭数据库 (可重复调用)。

        ``Workspace`` 只在**自己打开**数据库时关闭它 —— 组合根 (bootstrap)
        复用同一个连接时, 连接的生命周期由组合根负责。
        """
        if self._closed:
            return
        self._closed = True
        if self._persistence is not None:
            self._persistence.close()

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    @property
    def closed(self) -> bool:
        return self._closed

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        """一次业务操作的落盘边界 (Task 53)。

        每个**写**方法都把"领域调用 + 落盘"整体包在这里。语义:

        - 全部成功 -> 提交;
        - 任何一步失败 -> 整个操作涉及的每一张表都回到操作前的状态,
          异常原样向上传播 (绝不吞掉)。

        纯内存模式 (``persistence=False``) 下是空操作 —— 那里本来就没有
        可回滚的东西, 加一层假装的事务只会骗人。
        """
        if self._persistence is None:
            yield
            return
        with self._persistence.atomic():
            yield

    # ------------------------------------------------------------------
    # Course / Session
    # ------------------------------------------------------------------

    def create_course(
        self,
        name: str,
        code: str = "",
        language: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        course_id: Optional[str] = None
        try:
            with self._atomic():
                dto = self.course_service.create_course(name, code, language, metadata)
                self._context(dto["course_id"])  # 立即建立上下文 (幂等)
                self._flush_course(dto["course_id"])
            course_id = dto["course_id"]
            return dto
        finally:
            self._record_op(
                "create_course",
                course=course_id,
                success=course_id is not None,
                duration_ms=(time.monotonic() - started) * 1000,
                name=name,
            )

    def get_course(self, course_id: str) -> dict[str, Any]:
        return self.course_service.get_course(course_id)

    def list_courses(self) -> list[dict[str, Any]]:
        return self.course_service.list_courses()

    def update_course(self, course_id: str, **fields: Any) -> dict[str, Any]:
        with self._atomic():
            dto = self.course_service.update_course(course_id, **fields)
            self._flush_course(course_id)
        return dto

    def create_session(
        self,
        course_id: str,
        session_number: int = 0,
        date: str = "",
        title: str = "",
    ) -> dict[str, Any]:
        started = time.monotonic()
        session_id: Optional[str] = None
        ok = False
        try:
            with self._atomic():
                dto = self.course_service.create_session(
                    course_id, session_number=session_number, date=date, title=title
                )
                # Task 45: 课堂一旦创建, 就必须在知识组织层可见。否则
                # ``GET /api/knowledge?session_id=...`` 会对一个确实存在的课堂报
                # NOT_FOUND, coverage 也永远是 0 (见 _register_course_sessions)。
                ctx = self._contexts.get(dto["course_id"])
                if ctx is not None:
                    self._register_course_sessions(ctx)
                self._flush_course(dto["course_id"])
                self._flush_organization(dto["course_id"])
            session_id = dto["session_id"]
            ok = True
            return dto
        finally:
            self._record_op(
                "create_session",
                course=course_id,
                session=session_id,
                success=ok,
                duration_ms=(time.monotonic() - started) * 1000,
            )

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self.course_service.get_session(session_id)

    def list_sessions(self, course_id: Optional[str] = None) -> list[dict[str, Any]]:
        return self.course_service.list_sessions(course_id)

    # ------------------------------------------------------------------
    # Context
    # ------------------------------------------------------------------

    def context(self, course_id: str) -> CourseContext:
        """取得课程上下文 (不存在则 NotFoundError)。"""
        if not isinstance(course_id, str) or not course_id.strip():
            raise InvalidInputError("course_id must be a non-empty string")
        ctx = self._contexts.get(course_id)
        if ctx is None:
            ctx = self._build_context(course_id)
        return ctx

    def _context(self, course_id: str) -> CourseContext:
        return self.context(course_id)

    def reload_course(self, course_id: str) -> CourseContext:
        """丢弃内存里的课程上下文, 下次访问时从持久化状态重建。

        为什么需要它
        ------------
        ``CourseContext`` 是**有状态**的 (处理服务持有装配结果、组织服务持有
        已登记的知识点、审核服务持有审核记录)。正常业务写入都经由上下文自身
        的方法, 因此内存与库始终一致。

        但只要有人**绕过上下文**直接写持久化层 (例如以仓储为唯一真源注入一条
        冲突记录、或外部脚本/维护任务改了库), 内存里的那份快照就过期了 ——
        在此之前没有任何受支持的刷新手段, 只能重建整个 ``Workspace``。

        这里显式提供一条受支持的刷新路径: 只丢弃**这一门课**的上下文, 下一次
        ``context()`` 会走完整的 ``_build_context`` + ``_restore_course_state``
        重建 (幂等, 不写任何业务事实)。其他课程不受影响。

        注意: 调用方拿着的旧 ``CourseContext`` 引用即失效, 应重新 ``context()``
        获取。本方法不修改持久化内容。
        """
        if not isinstance(course_id, str) or not course_id.strip():
            raise InvalidInputError("course_id must be a non-empty string")
        self._contexts.pop(course_id, None)
        return self.context(course_id)

    def _build_context(self, course_id: str) -> CourseContext:
        dto = self.course_service.get_course(course_id)  # 不存在时抛 NotFoundError
        course = Course(
            course_id=dto["course_id"],
            name=dto["name"],
            code=dto["code"],
            language=dto["language"],
            metadata=dict(dto.get("metadata") or {}),
        )
        # 课程知识组织结构 (主题 / 归属 / 关系图) 先恢复, 再构造组织服务 ——
        # 否则 ``KnowledgeOrganizationService`` 会从空结构起步, 主题与
        # 课堂归属在重启后消失。
        org_structure = (
            None
            if self._persistence is None
            else self._persistence.load_organization_structure(course_id)
        )
        org = KnowledgeOrganizationService(course, structure=org_structure)
        workflow = MaterialWorkflowService(
            course,
            self._source_root,
            self._layout.root,
            store=self.store,
            ingestion_service=self.ingestion_service,
            org_service=org,
            max_file_size=self._max_file_size,
            max_attempts=self._max_attempts,
            clock=self._clock,
        )
        if self._persistence is not None:
            # 材料注册表: SQLite 是 source of truth, JSON 只是索引。
            # 以数据库为准做并集, 补上 JSON 里缺失的记录。
            workflow.restore_records(
                self._persistence.load_material_records(course_id)
            )
        processing = ClassroomProcessingService(
            workflow,
            org_service=org,
            clock=self._clock,
            max_attempts=self._max_attempts,
            quality_source=self._quality_source,
        )
        knowledge_service = KnowledgeService(org, store=self.store)

        def _course_prerequisites() -> dict[str, tuple[str, ...]]:
            """课程内显式的 PREREQUISITE 关系 (source 是 target 的前置)。

            只读, 不推断: Task 33 明确 RELATED / CONTRASTS / EXTENDS
            永不算前置, 因此这里只取 PREREQUISITE 边。
            """
            mapping: dict[str, list[str]] = {}
            try:
                relations = org.list_relations()
            except Exception:  # noqa: BLE001 - 关系层不可用时退化为空图
                return {}
            for relation in relations:
                if relation.relation_type is not KnowledgeRelationType.PREREQUISITE:
                    continue
                mapping.setdefault(relation.target_knowledge_point_id, []).append(
                    relation.source_knowledge_point_id
                )
            return {key: tuple(sorted(set(value))) for key, value in mapping.items()}

        learning = LearningService(
            course_id,
            clock=self._clock,
            prerequisite_provider=_course_prerequisites,
        )
        review_service = ReviewService(org)
        review_view = CourseReviewView(self)
        ctx = CourseContext(
            course=course,
            org_service=org,
            workflow=workflow,
            processing=processing,
            knowledge_service=knowledge_service,
            review_service=review_service,
            learning_service=learning,
            learning_view=LearningViewService(
                course_id,
                org_service=org,
                store=self.store,
                learning_service=learning,
                knowledge_service=knowledge_service,
                clock=self._clock,
            ),
            review_view=review_view,
            exercise_workflow=ExerciseWorkflow(self),
            learning_workflow=LearningWorkflow(self),
            review_mode=ReviewSet(self),
        )
        self._contexts[course_id] = ctx
        # 补齐历史课堂: 课程上下文可能是在课堂创建之后才第一次构建的
        # (例如进程重启后课程从持久化恢复)。幂等。
        self._register_course_sessions(ctx)
        self._restore_course_state(ctx)
        return ctx

    def _restore_course_state(self, ctx: CourseContext) -> dict[str, int]:
        """从 SQLite 恢复一门课程的全部业务状态 (幂等)。

        恢复顺序有依赖: 证据 -> 知识 -> 组织 -> 学习。理由:

        - 知识点的 ``evidence_refs`` 必须能在 EvidenceStore 里解析, 否则
          溯源链在重启后断掉 (规范第 7 条: Evidence-first 不得改变);
        - 学习层的练习/作答引用知识点, 所以必须排在知识之后。
        """
        p = self._persistence
        if p is None:
            return {}
        course_id = ctx.course_id

        # 1) 证据先恢复: 知识点的 evidence_refs 必须能在 EvidenceStore
        #    里解析, 否则溯源链在重启后断掉。
        #    (课程归属不再靠"证据属于哪门课"推导 —— 见下一步的说明。)

        # 2) 知识结构 (含冲突 + 评审历史)
        #
        # 课程隔离由**存储层主键**保证: 知识点 / 溯源链 / 冲突 / 评审历史
        # 都按 (course_id, id) 存。Task 68 之前只能靠"这条知识点的证据是
        # 不是本课程的"来猜归属, 而两门课共用同一份讲义时证据 id 也一样,
        # 那个推导无法区分 —— 实测每门课会凭空少掉一半知识点。
        structure = p.load_knowledge_structure(course_id)
        ctx.processing.restore_structure(structure)
        ctx.org_service.register_knowledge_structure(structure)
        ctx.review_service.restore_review_records(structure.review_records)

        # 3) 学习层
        exercises = p.load_exercises(course_id)
        students = p.load_students(course_id)
        # 批量读, 不要逐个学生读: 逐个读是 3 条 SQL/学生 (身份 + 事件 +
        # 状态), 一门课 500 个学生就是 1500 条 (Task 69 实测)。批量版的
        # 重建结果与逐个读一致, 见 snapshot.load_student_logs。
        logs = p.load_student_logs(course_id)
        answer_log = build_answer_log(exercises, self._answer_log_payload(course_id))
        evaluations = p.load_evaluations(course_id=course_id)
        ctx.learning_service.load_state(
            students=students,
            logs=logs,
            exercises=exercises,
            answer_log=answer_log,
            evaluations=evaluations,
            submitted_at=p.submitted_at_map(),
            course_knowledge_points=ctx.org_service.registered_knowledge_point_ids,
        )
        return {
            "knowledge_points": len(structure.knowledge_points),
            "conflicts": len(structure.conflicts),
            "review_records": len(structure.review_records),
            "students": len(students),
            "exercises": len(exercises),
        }

    def _answer_log_payload(self, course_id: str) -> Optional[dict[str, Any]]:
        """把该课程的作答 / 评估重建成 ``AnswerEvaluationLog.to_dict()`` 形状。

        作答与评估在数据库里是两张表 (评估通过外键挂在作答上), 而领域层的
        ``AnswerEvaluationLog.from_dict`` 需要一份合并快照。这里按课程过滤
        后重新组装 —— 不新增事实, 只是把两半拼回去。
        """
        p = self._persistence
        if p is None:
            return None
        answers = p.load_answers(course_id=course_id)
        if not answers:
            return None
        answer_ids = {answer.answer_id for answer in answers}
        evaluations = [
            result
            for result in p.load_evaluations(course_id=course_id)
            if result.answer_id in answer_ids
        ]
        from src.answer_evaluation import EVALUATION_SCHEMA_VERSION

        return {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "answers": [answer.to_dict() for answer in answers],
            "evaluations": [result.to_dict() for result in evaluations],
        }

    # ------------------------------------------------------------------
    # Materials
    # ------------------------------------------------------------------

    def register_material(
        self,
        course_id: str,
        path: str,
        session_id: Optional[str] = None,
        language: Optional[str] = None,
        *,
        filename: Optional[str] = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        material_id: Optional[str] = None
        ok = False
        failure_reason: Optional[str] = None
        try:
            with self._atomic():
                record = self.context(course_id).workflow.register_material(
                    path, session_id=session_id, language=language, filename=filename
                )
                self._flush_materials(course_id)
                self._flush_evidence()
            material_id = record.get("material_id")
            status = record.get("processing_status")
            ok = status != "FAILED"
            if not ok:
                failure_reason = str(record.get("error"))
            return record
        except Exception as exc:  # noqa: BLE001 - 记录失败原因, 然后照常向上抛
            failure_reason = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._record_op(
                "register_material",
                course=course_id,
                session=session_id,
                material=material_id,
                success=ok,
                failure=failure_reason,
                duration_ms=(time.monotonic() - started) * 1000,
                filename=filename or os.path.basename(str(path)),
            )

    def register_material_batch(
        self,
        course_id: str,
        paths: Sequence[str],
        session_id: Optional[str] = None,
        language: Optional[str] = None,
    ) -> dict[str, Any]:
        with self._atomic():
            report = self.context(course_id).workflow.register_material_batch(
                paths, session_id=session_id, language=language
            )
            self._flush_materials(course_id)
            self._flush_evidence()
        return report

    def list_materials(
        self, course_id: str, session_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        workflow = self.context(course_id).workflow
        if session_id is None:
            return workflow.list_materials()
        return workflow.list_session_materials(session_id)

    def get_material(self, course_id: str, material_id: str) -> dict[str, Any]:
        return self.context(course_id).workflow.get_material(material_id)

    def validate_material(self, course_id: str, material_id: str) -> dict[str, Any]:
        with self._atomic():
            record = self.context(course_id).workflow.validate_material(material_id)
            self._flush_materials(course_id)
        return record

    def material_evidence(
        self, course_id: str, material_id: str
    ) -> list[dict[str, Any]]:
        return self.context(course_id).workflow.evidence_for_material(material_id)

    def cleanup_temp(self) -> int:
        """清理上传暂存区与临时目录 (绝不触碰受管材料副本)。"""
        removed = 0
        for path in list(iter_files(self._layout.temp)):
            if remove_quietly(path):
                removed += 1
        return removed

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def process_material(self, course_id: str, material_id: str) -> dict[str, Any]:
        started = time.monotonic()
        ok = False
        failure_reason: Optional[str] = None
        try:
            with self._atomic():
                ctx = self.context(course_id)
                job = ctx.processing.process_material(material_id)
                self._sync_course_knowledge(ctx, job)
                self._flush_processing(course_id)
            # TASK-77: 确定性摄取提交之后, 再做可选的自动 AI 理解。AI 在
            # 自身的原子边界里执行 (``analyze_material_with_ai`` 自带
            # ``_atomic``), 与上面的摄取事务严格分离: AI 失败只污染 ``ai``
            # 子对象, 绝不回滚已经提交的 Evidence, 也绝不把材料翻成 FAILED。
            ai_info = self._maybe_auto_ai(course_id, material_id, job)
            if ai_info is not None:
                job["ai"] = ai_info
            # 作业字典的键是 ``status`` (ProcessingJob.to_dict -> status/stage/...),
            # 材料的 ``processing_status`` 只存在于**材料注册表**里。历史上这里读的是
            # ``processing_status``: 键不存在 -> ``ok`` 恒 False, 而 ``failure_reason``
            # 也恒 None (读错键时 status != "FAILED", 取不到 error) —— 于是
            # operations.log 里每一次"处理成功"都被记成 success:false, 而且**连
            # 失败原因都没有** (OperationRecord.to_dict 省略 None 字段)。
            # 口径与 ProcessingJob 的状态机常量对齐: 只有 FAILED 才带原因。
            status = str(job.get("status") or "")
            ok = status == JOB_SUCCEEDED
            if status == JOB_FAILED:
                failure_reason = str(job.get("error"))
            return job
        except Exception as exc:  # noqa: BLE001
            failure_reason = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._record_op(
                "process_material",
                course=course_id,
                material=material_id,
                success=ok,
                failure=failure_reason,
                duration_ms=(time.monotonic() - started) * 1000,
            )

    def process_session(self, course_id: str, session_id: str) -> dict[str, Any]:
        with self._atomic():
            ctx = self.context(course_id)
            self._register_course_sessions(ctx)
            report = ctx.processing.process_session(session_id)
            report["knowledge"]["session_link"] = self._link_session_knowledge(
                ctx, session_id
            )
            self._sync_learning_knowledge(ctx)
            self._flush_processing(course_id)
        # TASK-77: 整节课处理完后, 对其中摄取成功的材料逐个自动 AI 分析。
        # 受控串行 (与 processing_service 的"默认顺序执行"同口径, 不引入
        # Celery/Redis): 一份材料失败只污染它自己的 ``ai`` 条目, 不影响整节课。
        auto = self._maybe_auto_ai_session(course_id, report)
        if auto is not None:
            report["ai_auto"] = auto
        return report

    def start_session_processing(self, course_id: str, session_id: str) -> dict[str, Any]:
        return self.context(course_id).processing.start_session_processing(session_id)

    def retry_material(self, course_id: str, material_id: str) -> dict[str, Any]:
        with self._atomic():
            job = self.context(course_id).processing.retry_failed_material(material_id)
            self._flush_materials(course_id)
            self._flush_evidence()
            self._flush_knowledge(course_id)
            self._flush_organization(course_id)
        # TASK-77: 重试把材料救活后, 同样走一次自动 AI (幂等, 不产生重复 KP)。
        ai_info = self._maybe_auto_ai(course_id, material_id, job)
        if ai_info is not None:
            job["ai"] = ai_info
        return job

    def retry_ai_analysis(
        self, course_id: str, material_id: str, **kw: Any
    ) -> dict[str, Any]:
        """显式重试一次 AI 分析 (``POST .../ai-analyze`` 即本方法, 幂等)。

        TASK-77 的"AI 重试"入口: 材料摄取成功但 AI 失败 (429/超时/畸形
        响应) 后, 用户点"重试分析"走这里。底层复用
        :meth:`analyze_material_with_ai` 的幂等键 (``ai-<sha16>`` +
        确定性 ``aikp-*`` ID) 与 chunk 缓存 —— 已成功的 chunk 不重调,
        只补失败部分。
        """
        return self.analyze_material_with_ai(
            course_id, material_id, **kw  # type: ignore[arg-type]
        )

    # ------------------------------------------------------------------
    # TASK-77: 自动 AI 触发与报告持久化
    # ------------------------------------------------------------------
    #
    # ``process_material`` 摄取成功后调用 ``_maybe_auto_ai``; 关闭
    # (``CLASSROOM_AI_ENABLED=false`` / ``configure_ai(enabled=False)``)
    # 时返回 None —— 作业字典形状与 TASK-76 完全一致, 旧链路零改动。
    # 开启时 AI 失败只产生 ``{"status": "failed", ...}`` 子对象, 材料本身
    # 保持 SUCCEEDED, Evidence 原样保留, 用户可经 ``POST .../ai-analyze``
    # 重试 (幂等)。

    @staticmethod
    def _sanitize_ai_error(exc: BaseException) -> str:
        """把 AI 异常转成可展示的短原因 (脱敏)。

        Provider 与配置层已保证异常里不带 key/正文; 这里再截断到 300
        字符, 纯属纵深防御 —— 即使未来某个 provider 把 URL 参数拼进
        message, 也不会把整段 token 甩给 UI/日志。
        """
        text = "%s: %s" % (type(exc).__name__, exc)
        return text[:300]

    def _maybe_auto_ai(
        self, course_id: str, material_id: str, job: Mapping[str, Any]
    ) -> Optional[dict[str, Any]]:
        """摄取成功后可选地自动 AI 分析 (非阻塞, 不抛异常)。

        返回 None 表示"AI 未启用, 什么都没做" (调用方不加 ``ai`` 键);
        否则返回 ``ai`` 子对象 (``status`` 为 ``completed`` / ``failed`` /
        ``skipped`` 之一, 见模块级 ``AI_AUTO_*`` 常量)。
        """
        if not self._ai_enabled:
            return None
        if str(job.get("status") or "") != JOB_SUCCEEDED:
            return {
                "status": AI_AUTO_SKIPPED,
                "reason": (
                    "material ingestion did not succeed; "
                    "AI analysis skipped (evidence is safe)"
                ),
                "retryable": False,
            }
        try:
            report = self.analyze_material_with_ai(course_id, material_id)
        except Exception as exc:  # noqa: BLE001 - AI 失败绝不污染材料状态
            return {
                "status": AI_AUTO_FAILED,
                "error": self._sanitize_ai_error(exc),
                "retryable": True,
            }
        return {
            "status": AI_AUTO_COMPLETED,
            "processing_identity": report.get("processing_identity"),
            "provider": report.get("provider"),
            "model": report.get("model"),
            "summary": str(report.get("summary") or "")[:500],
            "topics": list(report.get("topics") or []),
            "auto_accepted": len(report.get("auto_accepted") or []),
            "needs_review": len(report.get("needs_review") or []),
            "conflicts": len(report.get("conflicts") or []),
            "chunk_total": report.get("chunk_total"),
            "chunk_succeeded": report.get("chunk_succeeded"),
            "chunk_failed": report.get("chunk_failed"),
            "retryable": False,
        }

    def _maybe_auto_ai_session(
        self, course_id: str, report: Mapping[str, Any]
    ) -> Optional[dict[str, Any]]:
        """整节课处理完后, 对其中摄取成功的材料逐个自动 AI (受控串行)。"""
        if not self._ai_enabled:
            return None
        out: dict[str, Any] = {}
        jobs = report.get("jobs")
        if not isinstance(jobs, list):
            return out
        for job in jobs:
            if not isinstance(job, Mapping):
                continue
            material_id = str(job.get("material_id") or "")
            if not material_id:
                continue
            info = self._maybe_auto_ai(course_id, material_id, job)
            if info is not None:
                out[material_id] = info
        return out

    def _ai_report_path(self, course_id: str, material_id: str) -> str:
        """AI 报告落盘路径 (``materials/ai-reports/<课>/<材料>.json``)。"""
        return safe_join(
            self._layout.materials,
            AI_REPORT_SUBDIR,
            course_id,
            material_id + ".json",
        )

    def _persist_ai_report(
        self, course_id: str, material_id: str, payload: Mapping[str, Any]
    ) -> None:
        """把一次成功的 AI 报告落盘 (衍生缓存, 最佳努力)。

        KP/Evidence/Review 本体经组织层正常落库 —— 报告丢了可由它们重新
        推导, 因此这里失败绝不抛异常 (更不能回滚已经提交的分析结果)。
        纯内存模式 (``persistence=False``) 下直接跳过。
        """
        if self._persistence is None:
            return
        try:
            target = self._ai_report_path(course_id, material_id)
            atomic_write_bytes(
                target,
                json.dumps(
                    dict(payload), ensure_ascii=False, sort_keys=True
                ).encode("utf-8"),
            )
        except (OSError, ValueError):
            # 衍生缓存写失败: 内存里的报告仍是权威, 本次分析不受影响。
            return

    def _load_persisted_ai_report(
        self, course_id: str, material_id: str
    ) -> Optional[dict[str, Any]]:
        """读落盘的 AI 报告 (形状校验通过才返回, 否则 None)。"""
        if self._persistence is None:
            return None
        try:
            target = self._ai_report_path(course_id, material_id)
        except ValueError:
            return None
        try:
            with open(target, "rb") as handle:
                payload = json.loads(handle.read().decode("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        if str(payload.get("material_id") or "") != material_id:
            return None
        return payload

    def _ai_brief(self, course_id: str, material_id: str) -> Optional[dict[str, Any]]:
        """最近一次 AI 分析的轻量摘要 (读路径用; 没有分析过则 None)。"""
        payload = self._ai_reports.get((course_id, material_id))
        if payload is None:
            payload = self._load_persisted_ai_report(course_id, material_id)
            if payload is not None:
                self._ai_reports[(course_id, material_id)] = payload
        if payload is None:
            return None
        return {
            "status": AI_AUTO_COMPLETED,
            "processing_identity": payload.get("processing_identity"),
            "provider": payload.get("provider"),
            "model": payload.get("model"),
            "topics": list(payload.get("topics") or []),
            "auto_accepted": len(payload.get("auto_accepted") or []),
            "needs_review": len(payload.get("needs_review") or []),
            "conflicts": len(payload.get("conflicts") or []),
            "retryable": False,
        }

    def processing_status(
        self, course_id: str, session_id: Optional[str] = None
    ) -> dict[str, Any]:
        return self.context(course_id).processing.get_status(session_id)

    def processing_job(self, course_id: str, material_id: str) -> dict[str, Any]:
        job = self.context(course_id).processing.get_job(material_id)
        # TASK-77: 读路径同样带上最近一次 AI 分析的摘要 (内存命中优先,
        # 否则读落盘报告; 没有分析过则不加键, 旧形状不变)。
        brief = self._ai_brief(course_id, material_id)
        if brief is not None:
            job["ai"] = brief
        return job

    def knowledge_summary(self, course_id: str) -> dict[str, Any]:
        return self.context(course_id).processing.get_knowledge_summary()

    # ------------------------------------------------------------------
    # Knowledge
    # ------------------------------------------------------------------

    def knowledge_points(
        self,
        course_id: str,
        session_id: Optional[str] = None,
        topic_id: Optional[str] = None,
        *,
        validation_status: Optional[str] = None,
        review_status: Optional[str] = None,
        conflict: Optional[bool] = None,
        language: Optional[str] = None,
        search: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """课程知识点列表, 可按状态 / 冲突 / 语言 / 关键词过滤。

        为什么这五个参数必须透传 (Task 62 修复的真实缺陷)
        --------------------------------------------------
        ``KnowledgeService.get_knowledge_points()`` 从 Task 22 起就支持这五个
        过滤器, ``/api/knowledge`` 也从一开始就把查询串解析好传下来 —— 但**装配
        层 ``Workspace.knowledge_points()`` 的签名里从来没有它们**。于是
        ``GET /api/knowledge?course_id=...`` 只要带上任何一个过滤器就必然
        抛 ``TypeError: unexpected keyword argument``, 被映射成 400
        ``INVALID_INPUT``。

        这不是"少写一个功能", 而是本项目的头号缺陷形状: **能力在两端都实现了,
        中间的接线从来没接上**。而且它污染范围极广 —— ``tests/test_learning_view``
        (64 条) / ``tests/test_exercise_ui`` (63 条) / ``tests/test_web_ui`` (28
        条) 的夹具都要先建知识点, 全部在这一步 ERRAR。所以修一条签名, 消掉
        9 failed + 155 error。

        ``conflict`` 的归一化也是**必需的**: HTTP 查询串里它永远是字符串
        (``"true"`` / ``"false"``), 而服务层的判据是
        ``conflict != has_conflict`` —— 传 ``"false"`` 进去做 ``"false" != False``
        恒为真, 于是"只看无冲突"会返回**空列表**(静默错), 比抛异常更难查。
        这里显式归一, 非法值直接忽略 (当作"不筛选")。
        """
        return self.context(course_id).knowledge_service.get_knowledge_points(
            course_id=course_id,
            session_id=session_id,
            topic_id=topic_id,
            validation_status=validation_status,
            review_status=review_status,
            conflict=_as_optional_bool(conflict),
            language=language,
            search=search,
        )

    def knowledge_point(self, course_id: str, knowledge_id: str) -> dict[str, Any]:
        return self.context(course_id).knowledge_service.get_knowledge_point(knowledge_id)

    def knowledge_evidence(
        self, course_id: str, knowledge_id: str
    ) -> list[dict[str, Any]]:
        return self.context(course_id).knowledge_service.get_evidence_for_knowledge_point(
            knowledge_id
        )

    def coverage(self, course_id: str) -> dict[str, Any]:
        return self.context(course_id).knowledge_service.get_coverage(course_id)

    def gaps(self, course_id: str) -> dict[str, Any]:
        return self.context(course_id).knowledge_service.get_gaps(course_id)

    def dependencies(self, course_id: str) -> dict[str, Any]:
        return self.context(course_id).knowledge_service.get_dependencies(course_id)

    def conflicts(self, course_id: str) -> list[dict[str, Any]]:
        return self.context(course_id).knowledge_service.get_conflicts(course_id)

    # ------------------------------------------------------------------
    # Review pack (P1/§2, 只读装配)
    # ------------------------------------------------------------------
    #
    # 材料摘要 / 课程合并包: 复用既有的只读查询 (证据 / 冲突 / 覆盖),
    # 经 ReviewPackService 装配后返回。**不落盘、不写库、不触碰
    # process/retry**; LLM 出站只由这两个显式 GET 触发。

    def _review_pack_service(self, course_id: str) -> Any:
        from src.application.review_pack_service import ReviewPackService
        from src.llm_provider import MockSummarizer

        context = self.context(course_id)
        summarizer = self.summarizer or MockSummarizer()
        return ReviewPackService(
            knowledge_points_fn=self.knowledge_points,
            evidence_for_kp_fn=context.knowledge_service.get_evidence_for_knowledge_point,
            conflicts_fn=self.conflicts,
            coverage_fn=self.coverage,
            evidence_for_material_fn=(
                lambda cid, mid: context.workflow.evidence_for_material(mid)
            ),
            material_record_fn=lambda mid: context.workflow.get_material(mid),
            summarizer=summarizer,
            llm_mode=self._llm_mode,
        )

    def material_digest(self, course_id: str, material_id: str) -> dict[str, Any]:
        """单材料摘要 (只读 GET)。材料不存在时显式 404, 不编造内容。"""
        self.get_material(course_id, material_id)
        return self._review_pack_service(course_id).material_digest(
            course_id, material_id
        )

    def course_review_pack(self, course_id: str) -> dict[str, Any]:
        """课程合并复习包 (只读 GET)。课程不存在时显式 404。"""
        self.get_course(course_id)
        return self._review_pack_service(course_id).course_review_pack(course_id)

    # ------------------------------------------------------------------
    # AI understanding (TASK-76, 显式触发)
    # ------------------------------------------------------------------
    #
    # 确定性 ingestion 之后的可选语义层: Evidence -> chunk -> AI Provider ->
    # 结构化候选 -> grounding/校验/去重 -> KnowledgePoint (+ Review)。
    # 默认关闭 (``CLASSROOM_AI_ENABLED`` / ``configure_ai``); 关闭时旧链路
    # 照常工作。显式分析失败绝不删除已有 Evidence/KP (422 + 可 retry)。
    # 本层绝不写 student learning state (只做课程 KP 注册表的只读同步)。

    def configure_ai(
        self,
        *,
        enabled: Optional[bool] = None,
        provider: Optional[Any] = None,
    ) -> dict[str, Any]:
        """配置 AI 管线 (显式调用, 内存生效, 不落盘、不读 key 明文)。

        ``provider=None`` 时保留当前 provider (默认 FakeAIProvider, 零出站)。
        返回可安全展示的 provider 描述 (无 key)。
        """
        if enabled is not None:
            self._ai_enabled = bool(enabled)
        if provider is not None:
            self._ai_provider = provider
        name = getattr(self._ai_provider, "name", None) or "fake-deterministic"
        return {
            "enabled": self._ai_enabled,
            "provider": name,
            "model": getattr(self._ai_provider, "model", name),
        }

    @property
    def ai_enabled(self) -> bool:
        return self._ai_enabled

    def analyze_material_with_ai(
        self,
        course_id: str,
        material_id: str,
        *,
        content_language: Optional[str] = None,
        provider: Optional[Any] = None,
    ) -> dict[str, Any]:
        """对一份材料执行 AI 语义分析并自动落库 KnowledgePoint。

        前置: 材料必须已处理出 Evidence (否则 422, 先 process 再 retry)。
        成功报告形如 ``自动确认 N / 待确认 M / 冲突 K``; 低置信度与冲突进
        Review 队列 (review_status=pending, 需人工 confirm/reject)。
        重复调用幂等 (同一 processing_identity + 确定性 aikp-* ID)。
        """
        from src.application.ai.service import AIAnalysisService, require_enabled

        started = time.monotonic()
        ok = False
        failure_reason: Optional[str] = None
        try:
            require_enabled(self._ai_enabled)
            with self._atomic():
                ctx = self.context(course_id)
                record = ctx.workflow.get_material(material_id)
                service = AIAnalysisService()
                report = service.analyze(
                    ctx,
                    record,
                    course_id=course_id,
                    material_id=material_id,
                    content_language=content_language,
                    provider=provider or self._ai_provider,
                )
                self._sync_learning_knowledge(ctx)
                self._flush_knowledge(course_id)
                self._flush_organization(course_id)
                payload = report.to_dict()
                self._ai_reports[(course_id, material_id)] = payload
            # TASK-77: 落盘在事务之外 —— 衍生缓存写失败绝不回滚已提交的 KP。
            self._persist_ai_report(course_id, material_id, payload)
            ok = True
            return payload
        except Exception as exc:  # noqa: BLE001 - 记录失败原因, 然后照常向上抛
            failure_reason = "%s: %s" % (type(exc).__name__, exc)
            raise
        finally:
            self._record_op(
                "analyze_material_with_ai",
                course=course_id,
                material=material_id,
                success=ok,
                failure=failure_reason,
                duration_ms=(time.monotonic() - started) * 1000,
            )

    def ai_summary(self, course_id: str, material_id: str) -> dict[str, Any]:
        """最近一次 AI 分析的总结视图 (只读, 不重新调用 AI)。

        从未分析过 -> NotFoundError (404), 而不是现场补算 (§36.5/36.6:
        禁止每次打开页面重新调用 LLM)。

        TASK-77: 内存未命中时读落盘报告 (重启后总结仍在); 落盘也没有
        才 404。KP/Evidence 本体与报告是否在盘无关 —— 它们经组织层落库。
        """
        self.get_material(course_id, material_id)
        payload = self._ai_reports.get((course_id, material_id))
        if payload is None:
            payload = self._load_persisted_ai_report(course_id, material_id)
            if payload is not None:
                self._ai_reports[(course_id, material_id)] = payload
        if payload is None:
            raise NotFoundError(
                "no AI analysis for material %r yet; run AI analysis first"
                % (material_id,)
            )
        return {
            "material_id": material_id,
            "course_id": course_id,
            "status": payload.get("status"),
            "summary": payload.get("summary"),
            "topics": payload.get("topics"),
            "definitions": payload.get("definitions"),
            "formulas": payload.get("formulas"),
            "examples": payload.get("examples"),
            "prerequisites": payload.get("prerequisites"),
            "difficulties": payload.get("difficulties"),
            "knowledge_points_total": payload.get("knowledge_points_total"),
            "auto_accepted": len(payload.get("auto_accepted") or []),
            "needs_review": len(payload.get("needs_review") or []),
            "conflicts": len(payload.get("conflicts") or []),
            "provider": payload.get("provider"),
            "model": payload.get("model"),
            "prompt_version": payload.get("prompt_version"),
            "pipeline_version": payload.get("pipeline_version"),
            "processing_identity": payload.get("processing_identity"),
            "created_at": payload.get("created_at"),
        }

    def course_knowledge(self, course_id: str) -> dict[str, Any]:
        return self.context(course_id).knowledge_service.get_course_knowledge(course_id)

    # ------------------------------------------------------------------
    # Course Review Center (Task 62, 只读投影)
    # ------------------------------------------------------------------
    #
    # 这两条是**只读**的: 不写库、不追加计划快照、不改任何状态。
    # 全部计数与分组都委托给 ``CourseReviewView``, 后者复用
    # ``KnowledgeCoverageAnalyzer`` (一次快照, 无 N+1)。

    def course_review(self, course_id: str) -> dict[str, Any]:
        """课程级复习中心 (overview / topics / sessions / queue / conflicts / gaps)。"""
        return self.context(course_id).review_view.course_review(course_id)

    def course_review_summary(self, course_id: str) -> dict[str, Any]:
        """复习中心轻量摘要 (给导航卡片用; 空课程返回 0 计数而非 404)。"""
        return self.context(course_id).review_view.course_review_summary(course_id)

    # ------------------------------------------------------------------
    # Exam Review Mode (Task 67, 只读投影)
    # ------------------------------------------------------------------
    #
    # 同样是**只读**的: 不写库、不改任何状态、不解决任何冲突。
    # ReviewSet 这个类上根本不存在写方法 —— "冲突绝不被自动解决"因此
    # 是结构性保证, 而不是"我们记得不去解决"。

    def student_review_set(
        self, course_id: str, student_id: str, *, lang: str = "zh"
    ) -> dict[str, Any]:
        """考前复习集合 (blocked / attention / ready 三桶, 不含任何预测字段)。"""
        return self.context(course_id).review_mode.review_set(
            course_id, student_id, lang=lang
        )

    # ------------------------------------------------------------------
    # Multi-Course Workspace (Task 68, 只读投影)
    # ------------------------------------------------------------------
    #
    # 与上面两个投影一样只读。区别在于它是**全局**的 (跨课程), 所以挂在
    # Workspace 上而不是 CourseContext 上 —— 一门课的上下文里不该有
    # "其他课" 这个概念。
    #
    # 隔离靠的是每一条查询都带 course_id (见 MultiCourseWorkspace._row),
    # 不是靠"先查全部再筛"。后者一旦漏筛就会串课。

    def my_courses(
        self, *, lang: str = "zh", preferred: Optional[str] = None
    ) -> dict[str, Any]:
        """全部课程的逐课程总览 (每门课各查各的, 再并列)。"""
        if self._multi_course is None:
            self._multi_course = MultiCourseWorkspace(self)
        return self._multi_course.my_courses(lang=lang, preferred=preferred)

    def course_summary(self, course_id: str, *, lang: str = "zh") -> dict[str, Any]:
        """单门课的一行总览 (与 my_courses()['courses'][i] 同形)。"""
        if self._multi_course is None:
            self._multi_course = MultiCourseWorkspace(self)
        return self._multi_course.course_summary(course_id, lang=lang)

    def resolve_course_selection(
        self, preferred: Optional[str] = None
    ) -> dict[str, Any]:
        """把"我想看这门课"解析成"实际该看这门课" (+ 为什么)。

        localStorage 里的 course_id 可能指向一门已被删除的课; 这条规则
        给出确定性的回落, 避免 UI 拿着脏 id 去请求然后显示 404。
        """
        if self._multi_course is None:
            self._multi_course = MultiCourseWorkspace(self)
        return self._multi_course.resolve_selection(preferred)

    # ------------------------------------------------------------------
    # Review
    # ------------------------------------------------------------------

    def review_candidates(self, course_id: str) -> list[dict[str, Any]]:
        return self.context(course_id).knowledge_service.get_review_candidates(course_id)

    def review_history(self, course_id: str, knowledge_id: str) -> list[dict[str, Any]]:
        return self.context(course_id).review_service.get_review_history(knowledge_id)

    def review_confirm(self, course_id: str, knowledge_id: str, **kw: Any) -> dict[str, Any]:
        with self._atomic():
            record = self.context(course_id).review_service.confirm(knowledge_id, **kw)
            self._flush_knowledge(course_id)
        return record

    def review_reject(self, course_id: str, knowledge_id: str, **kw: Any) -> dict[str, Any]:
        with self._atomic():
            record = self.context(course_id).review_service.reject(knowledge_id, **kw)
            self._flush_knowledge(course_id)
        return record

    def review_keep_unverified(
        self, course_id: str, knowledge_id: str, **kw: Any
    ) -> dict[str, Any]:
        with self._atomic():
            record = self.context(course_id).review_service.keep_unverified(
                knowledge_id, **kw
            )
            self._flush_knowledge(course_id)
        return record

    def review_resolve_conflict(
        self, course_id: str, knowledge_id: str, **kw: Any
    ) -> dict[str, Any]:
        with self._atomic():
            record = self.context(course_id).review_service.resolve_conflict(
                knowledge_id, **kw
            )
            self._flush_knowledge(course_id)
        return record

    # ------------------------------------------------------------------
    # Learning
    # ------------------------------------------------------------------

    def create_student(
        self, course_id: str, student_id: str, display_name: Optional[str] = None
    ) -> dict[str, Any]:
        with self._atomic():
            dto = self.context(course_id).learning_service.create_student(
                student_id, display_name
            )
            # 只写这一个学生 —— 建学生不动练习、不动答案 (Task 69)。
            self._flush_learning(
                course_id, student_ids=[student_id], exercise_ids=[], answer_ids=[]
            )
        return dto

    def get_student(self, course_id: str, student_id: str) -> dict[str, Any]:
        return self.context(course_id).learning_service.get_student(student_id)

    def list_students(self, course_id: str) -> list[dict[str, Any]]:
        return self.context(course_id).learning_service.list_students()

    def student_state(self, course_id: str, student_id: str) -> dict[str, Any]:
        return self.context(course_id).learning_service.get_student_state(student_id)

    def learning_status(self, course_id: str, student_id: str) -> dict[str, Any]:
        return self.context(course_id).learning_service.get_learning_status(student_id)

    def create_exercise(
        self,
        course_id: str,
        exercise_type: str,
        prompt: str,
        knowledge_point_ids: Sequence[str],
        **kw: Any,
    ) -> dict[str, Any]:
        with self._atomic():
            dto = self.context(course_id).learning_service.create_exercise(
                exercise_type, prompt, knowledge_point_ids, **kw
            )
            # 只写这一道新题 —— 建题不改学生日志、不改答案 (Task 69)。
            self._flush_learning(
                course_id,
                student_ids=[],
                exercise_ids=[dto["exercise_id"]],
                answer_ids=[],
            )
        return dto

    def list_exercises(self, course_id: str) -> list[dict[str, Any]]:
        return self.context(course_id).learning_service.list_exercises()

    def get_exercise(self, course_id: str, exercise_id: str) -> dict[str, Any]:
        return self.context(course_id).learning_service.get_exercise(exercise_id)

    def submit_answer(
        self,
        course_id: str,
        student_id: str,
        exercise_id: str,
        submitted_value: str,
        sequence: int = 0,
    ) -> dict[str, Any]:
        started = time.monotonic()
        answer_id: Optional[str] = None
        ok = False
        failure_reason: Optional[str] = None
        try:
            with self._atomic():
                dto = self.context(course_id).learning_service.submit_answer(
                    student_id, exercise_id, submitted_value, sequence
                )
                # 只写这条答案 + 这个学生的日志。练习**一个都不写** (提交答案
                # 不改练习对象, 它们建题时就已经落盘了) —— 见 _flush_learning。
                self._flush_learning(
                    course_id,
                    student_ids=[student_id],
                    exercise_ids=[],
                    answer_ids=[dto["answer_id"]],
                )
            answer_id = dto.get("answer_id")
            ok = True
            return dto
        except Exception as exc:  # noqa: BLE001
            failure_reason = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._record_op(
                "submit_answer",
                course=course_id,
                material=None,
                success=ok,
                failure=failure_reason,
                duration_ms=(time.monotonic() - started) * 1000,
                student=student_id,
                exercise=exercise_id,
                answer=answer_id,
            )

    def get_evaluation(self, course_id: str, answer_id: str) -> dict[str, Any]:
        return self.context(course_id).learning_service.get_evaluation(answer_id)

    # ------------------------------------------------------------------
    # 出题工作流 (Task 64)
    # ------------------------------------------------------------------

    def preview_exercise(
        self,
        course_id: str,
        knowledge_point_id: str,
        *,
        config: Any = None,
    ) -> dict[str, Any]:
        """确定性生成预览 (纯读, 不落库)。"""
        return self.context(course_id).exercise_workflow.preview(
            course_id, knowledge_point_id, config=config
        )

    def generate_exercise(
        self,
        course_id: str,
        knowledge_point_id: str,
        *,
        config: Any = None,
    ) -> dict[str, Any]:
        """生成并落库 (幂等: 同一草稿 -> 同一个 exercise_id)。"""
        with self._atomic():
            result = self.context(course_id).exercise_workflow.generate(
                course_id, knowledge_point_id, config=config
            )
            # 不额外落盘: ``generate`` 内部走的是 ``Workspace.create_exercise``,
            # 它已经把这道新题写穿了。这里再全量写一遍整门课是纯浪费 ——
            # Task 69 里一次批量出题会把全部练习 + 全部学生日志重写 N 遍。
        return result

    def generate_exercises(
        self,
        course_id: str,
        *,
        knowledge_point_ids: Optional[Sequence[str]] = None,
        config: Any = None,
        limit: Optional[int] = None,
    ) -> dict[str, Any]:
        """批量生成 (按 knowledge_id 确定性排序)。"""
        with self._atomic():
            result = self.context(course_id).exercise_workflow.generate_batch(
                course_id,
                knowledge_point_ids=knowledge_point_ids,
                config=config,
                limit=limit,
            )
            # 同上: 每一道题在被 ``create_exercise`` 建出来时就已经落盘。
        return result

    def exercise_grounding(self, course_id: str, exercise_id: str) -> dict[str, Any]:
        """Exercise -> KnowledgePoint -> Evidence -> Material 完整追溯链。"""
        return self.context(course_id).exercise_workflow.grounding(course_id, exercise_id)

    def exercise_start(self, course_id: str, exercise_id: str) -> dict[str, Any]:
        """开始练习 (答案保持隐藏, 返回 grounding)。"""
        return self.context(course_id).exercise_workflow.start(course_id, exercise_id)

    def exercise_submit(
        self,
        course_id: str,
        student_id: str,
        exercise_id: str,
        submitted_value: str,
        sequence: int = 0,
    ) -> dict[str, Any]:
        """提交作答并返回既有评估层的判定 + grounding。"""
        with self._atomic():
            result = self.context(course_id).exercise_workflow.submit(
                course_id, student_id, exercise_id, submitted_value, sequence
            )
            # 同上: ``submit`` 内部走 ``Workspace.submit_answer``, 答案与该
            # 学生的日志已经写穿了; 这里再重写整门课没有任何新状态可写。
        return result

    def knowledge_evidence_trace(
        self, course_id: str, knowledge_point_id: str
    ) -> dict[str, Any]:
        """KnowledgePoint -> Evidence -> Material (供出题页与错题页复用)。"""
        return self.context(course_id).exercise_workflow.evidence_trace(
            course_id, knowledge_point_id
        )

    # ------------------------------------------------------------------
    # 错题与薄弱知识点中心 (Task 65)
    # ------------------------------------------------------------------

    def mistakes_center(
        self,
        course_id: str,
        student_id: str,
        *,
        group_by: str = "knowledge",
        lang: str = "zh",
    ) -> dict[str, Any]:
        """错题中心 (只读投影)。

        所有"对错"都来自既有 Evaluation (Task 32); 所有"需要关注"都来自
        既有 StudentState (Task 30)。本方法不写任何东西 —— **不落库、
        不生成新练习**(spec 65.10)。
        """
        return MistakesView(self, course_id).center(
            student_id, group_by=group_by, lang=lang
        )

    def mistake_detail(
        self, course_id: str, student_id: str, knowledge_id: str
    ) -> dict[str, Any]:
        """单条错题的下钻: 为什么错 -> 重新学习依据 -> 可复用的练习。"""
        return MistakesView(self, course_id).enrich(student_id, knowledge_id)

    def study_plan(self, course_id: str, student_id: str) -> dict[str, Any]:
        with self._atomic():
            plan = self.context(course_id).learning_service.get_study_plan(student_id)
            # 计划快照是**不可变的内容寻址审计记录**: 输入变 -> 新 plan_id ->
            # 新行, 旧快照保留下来可审计 (Task 52.2)。值本身是派生的 ——
            # 每次重算, 从不从快照读回 (见 docs/architecture.md 21.2)。
            self._flush_plans(course_id, student_id)
        return plan

    def learning_path(self, course_id: str, knowledge_id: str) -> dict[str, Any]:
        return self.context(course_id).learning_service.get_learning_path(knowledge_id)

    # ------------------------------------------------------------------
    # 学习 UI 投影 (Task 40)
    # ------------------------------------------------------------------

    def student_dashboard(self, course_id: str, student_id: str) -> dict[str, Any]:
        """学生首页快照 (只读投影; 数据全部来自 Task 30-33)。"""
        ctx = self.context(course_id)
        return ctx.learning_view.student_dashboard(student_id)

    def student_learning_path(
        self, course_id: str, student_id: str, knowledge_id: str
    ) -> dict[str, Any]:
        """前置链 + 导航状态 + 练习 + 评估。"""
        ctx = self.context(course_id)
        return ctx.learning_view.learning_path_view(student_id, knowledge_id)

    def grounded_explanation(
        self, course_id: str, knowledge_id: str, language: str = "es"
    ) -> dict[str, Any]:
        """Task 29 grounded explanation 的只读投影 (无证据即明确报缺)。"""
        ctx = self.context(course_id)
        return ctx.learning_view.grounded_explanation(knowledge_id, language)

    def record_learning_event(
        self, course_id: str, student_id: str, knowledge_id: str, event_type: str
    ) -> dict[str, Any]:
        """记录学生学习事件 (viewed / practiced / reviewed)。

        使用 Task 30 已定义的事件与转移规则; 学习层不会因此新增掌握度判定。
        """
        with self._atomic():
            ctx = self.context(course_id)
            dto = ctx.learning_service.record_learning_event(
                student_id, knowledge_id, event_type
            )
            # 事件只改这个学生的日志: 不写练习, 也不写别人的日志。
            self._flush_learning(
                course_id, student_ids=[student_id], exercise_ids=[], answer_ids=[]
            )
        return dto

    def exercise_list_view(self, course_id: str, student_id: str) -> dict[str, Any]:
        """练习列表 (学生视角, 不含答案)。"""
        ctx = self.context(course_id)
        return ctx.learning_view.exercise_list_view(student_id)

    def exercise_view(
        self, course_id: str, student_id: str, exercise_id: str
    ) -> dict[str, Any]:
        """单题视图 (题目 / 题型 / 关联知识点 / 前置 / 证据; 提交前无答案)。"""
        ctx = self.context(course_id)
        return ctx.learning_view.exercise_view(student_id, exercise_id)

    def exercise_evaluation_view(
        self, course_id: str, student_id: str, exercise_id: str
    ) -> dict[str, Any]:
        """评估结果视图 (score / status / feedback / knowledge_point / evidence)。"""
        ctx = self.context(course_id)
        return ctx.learning_view.exercise_evaluation_view(student_id, exercise_id)

    # ------------------------------------------------------------------
    # 课堂归属与知识装配接线 (Task 45 验收修复)
    # ------------------------------------------------------------------
    #
    # 下面三个方法补上了一条此前**完全缺失**的接线:
    #
    #   ClassSession (CourseService)
    #        ↓  _register_course_sessions
    #   知识组织层 session 注册表
    #        ↓  _link_session_knowledge (证据优先)
    #   KnowledgePoint ↔ SessionKnowledgeMembership
    #
    # 为什么必须在应用层做: 课堂由 ``CourseService`` 创建, 而覆盖 / 课堂级
    # 知识查询由 ``KnowledgeOrganizationService`` 提供 —— 两者此前从不相通。
    # 后果 (Task 45 用真实课堂数据验收时发现):
    #   1) ``GET /api/knowledge?session_id=...`` 对真实存在的课堂返回
    #      NOT_FOUND ("老师今天讲了什么?" 直接失败);
    #   2) ``coverage`` 的 covered 恒为 0, 每个知识点永远是 "uncovered";
    #   3) ``knowledge_trace`` 的 ``sessions`` 恒为空。
    # 这三条都是 spec 明确要求的验收答案 (Q1 / Q4), 因此属于"会阻塞当前
    # Task 的真实问题", 必须修复而不是记录。
    #
    # 归属判定严格证据优先: 只有证据确实来自本节课登记材料的知识点才会被
    # 归属; 绝不按标题相似度或常识猜测。

    def _register_course_sessions(self, ctx: CourseContext) -> int:
        """把该课程已存在的课堂登记到知识组织层 (幂等, 返回新登记数)。"""
        registered = 0
        known = set(ctx.org_service.registered_session_ids)
        for dto in self.course_service.list_sessions(ctx.course_id):
            session_id = str(dto.get("session_id") or "")
            if not session_id or session_id in known:
                continue
            ctx.org_service.register_session(ClassSession.from_dict(dto))
            known.add(session_id)
            registered += 1
        return registered

    def _sync_course_knowledge(
        self, ctx: CourseContext, job: Mapping[str, Any]
    ) -> None:
        """材料处理成功后装配知识并登记课堂归属 (幂等)。

        单材料处理此前**只产出证据, 不装配知识** —— 界面上"处理"按钮点完
        以后知识点仍然是 0 个, 而"处理整节课"按钮却能出知识。同一个动作
        在两条路径上结果不同, 是 Task 45 验收发现的真实缺陷。

        失败的作业绝不装配: 知识只能来自真实存在的证据。
        """
        if str(job.get("status") or "") == "SUCCEEDED":
            # 不给 ``evidence_ids`` —— 那会把作用域缩到"这一个材料", 同一门课
            # 里跨材料 / 跨节课的矛盾证据就组不成 CONFLICTED 了。也不给
            # ``session_id`` —— 没挂课时的材料会被漏掉。默认作用域是**本门课**,
            # 理由见 processing_service.assemble_knowledge 的 docstring。
            ctx.processing.assemble_knowledge()
            session_id = job.get("session_id")
            if session_id:
                self._register_course_sessions(ctx)
                self._link_session_knowledge(ctx, str(session_id))
        self._sync_learning_knowledge(ctx)

    def _link_session_knowledge(
        self, ctx: CourseContext, session_id: str
    ) -> dict[str, Any]:
        """把本节课材料产出的知识点登记为课堂归属 (幂等)。

        归属依据是证据链: 知识点 → 证据 → 源材料 → 材料登记的课堂。
        只要该知识点有一条证据来自本节课的材料, 就归属本节课。
        """
        structure = ctx.processing.structure
        if structure is None or session_id not in ctx.org_service.registered_session_ids:
            return {
                "session_id": session_id,
                "knowledge_point_ids": [],
                "linked": 0,
                "known": False,
            }
        material_ids = sorted(
            {
                str(record["material_id"])
                for record in ctx.workflow.list_session_materials(session_id)
                if record.get("material_id")
            }
        )
        if not material_ids:
            return {
                "session_id": session_id,
                "knowledge_point_ids": [],
                "linked": 0,
                "known": True,
            }
        from src.knowledge_assembly import KnowledgeAssembler

        candidates: set[str] = set()
        for material_id in material_ids:
            candidates.update(
                KnowledgeAssembler.knowledge_for_material(
                    structure, material_id, self.store
                )
            )
        candidates &= set(ctx.org_service.registered_knowledge_point_ids)
        linked = sorted(candidates)
        if linked:
            ctx.org_service.add_knowledge_to_sessions(session_id, linked)
        return {
            "session_id": session_id,
            "knowledge_point_ids": linked,
            "linked": len(linked),
            "known": True,
        }

    def _sync_learning_knowledge(self, ctx: CourseContext) -> int:
        """把知识库里真实存在的 KP 同步给学习层 (幂等)。

        学习规划必须建立在 Evidence-backed 的知识点之上, 因此这条接线是
        单向的: 知识库 -> 学习层。学习层永远不会反向制造知识点。
        """
        return ctx.learning_service.register_course_knowledge_points(
            ctx.org_service.registered_knowledge_point_ids
        )

    # ------------------------------------------------------------------
    # UI 只读投影 (Task 39)
    # ------------------------------------------------------------------
    #
    # 下面两个方法是**纯只读投影**, 供 Web UI 使用:
    # - 它们不引入任何业务规则, 只是把已存在的应用服务结果组装成
    #   页面一次渲染所需的形状;
    # - 它们绝不写任何状态, 也绝不绕过 domain 层;
    # - 它们把"溯源断链"显式暴露出来 (unresolved / missing 标记), 而不是
    #   静默丢弃 —— 证据链断了必须看得见。

    def knowledge_trace(self, course_id: str, knowledge_id: str) -> dict[str, Any]:
        """知识点 -> 证据 -> 源材料 的完整溯源包 (核心 UX 要求)。

        每个 evidence 都带 ``source.material_id``; 这里把它解析成真实材料
        记录。任何解析不到的引用都会进入 ``unresolved_material_ids``,
        页面据此显示"溯源断链", 而不是假装一切正常。
        """
        ctx = self.context(course_id)
        kp = ctx.knowledge_service.get_knowledge_point(knowledge_id)
        evidence = ctx.knowledge_service.get_evidence_for_knowledge_point(knowledge_id)

        material_cache: dict[str, Optional[dict[str, Any]]] = {}
        links: list[dict[str, Any]] = []
        unresolved: list[str] = []
        for item in evidence:
            source = item.get("source") or {}
            material_id = source.get("material_id")
            material: Optional[dict[str, Any]] = None
            if material_id:
                if material_id not in material_cache:
                    try:
                        material_cache[material_id] = ctx.workflow.get_material(material_id)
                    except NotFoundError:
                        material_cache[material_id] = None
                material = material_cache[material_id]
                if material is None:
                    unresolved.append(material_id)
            links.append({"evidence": item, "material": material})

        sessions: list[dict[str, Any]] = []
        for session_id in sorted(ctx.org_service.get_knowledge_point_sessions(knowledge_id)):
            try:
                sessions.append(self.course_service.get_session(session_id))
            except NotFoundError:
                sessions.append({"session_id": session_id, "missing": True})

        # 证据优先的课堂归属: 知识点的来源课堂 = 其证据所引用材料登记的课堂。
        # 这是从溯源链**推导**出来的事实, 与组织层显式声明的归属 (上面
        # 的 sessions) 分开报告, 两者都可能是空的, 都绝不臆造。
        source_session_ids = sorted(
            {
                str(material["session_id"])
                for material in material_cache.values()
                if material is not None and material.get("session_id")
            }
        )
        source_sessions: list[dict[str, Any]] = []
        for session_id in source_session_ids:
            try:
                source_sessions.append(self.course_service.get_session(session_id))
            except NotFoundError:
                source_sessions.append({"session_id": session_id, "missing": True})

        topics: list[dict[str, Any]] = []
        for topic_id in sorted(ctx.org_service.get_knowledge_point_topics(knowledge_id)):
            topics.append(ctx.org_service.get_topic(topic_id).to_dict())

        outgoing = [
            rel.to_dict()
            for rel in ctx.org_service.get_related_knowledge(knowledge_id, "outgoing")
        ]
        incoming = [
            rel.to_dict()
            for rel in ctx.org_service.get_related_knowledge(knowledge_id, "incoming")
        ]

        # KnowledgePoint DTO 本身没有 language 字段 —— 绝不臆造。这里只报告
        # 证据实际声明的语言集合, 并显式说明该字段未声明。
        languages = sorted(
            {str(item["language"]) for item in evidence if item.get("language")}
        )

        return {
            "course_id": course_id,
            "knowledge_point": kp,
            "evidence": evidence,
            "links": links,
            "materials": [
                material
                for material in (material_cache.get(key) for key in sorted(material_cache))
                if material is not None
            ],
            "unresolved_material_ids": sorted(set(unresolved)),
            "sessions": sessions,
            "source_sessions": source_sessions,
            "topics": topics,
            "dependencies": {
                "outgoing": outgoing,
                "incoming": incoming,
                "related_points": list(kp.get("related_points") or []),
            },
            "language": {
                "declared": None,
                "evidence_languages": languages,
                "note": "KnowledgePoint carries no language field; "
                        "languages shown are those declared by its evidence.",
            },
            "complete": not unresolved,
        }

    def dashboard(self, course_id: Optional[str] = None) -> dict[str, Any]:
        """首页快照: 课程 / 课堂 / 材料 / 处理 / 知识点 / 待审 / 学生 / 练习 / 缺口。

        未指定 ``course_id`` 时使用 course_id 最小的课程 (确定性), 没有任何
        课程时返回只含 courses 的空快照 —— 空状态也是一种合法状态。
        """
        courses = self.list_courses()
        selected: Optional[str] = None
        if course_id is not None and str(course_id).strip():
            selected = str(course_id).strip()
            ctx = self.context(selected)  # 不存在时抛 NotFoundError
        elif courses:
            selected = courses[0]["course_id"]

        snapshot: dict[str, Any] = {
            "application": APPLICATION_NAME,
            "version": APPLICATION_VERSION,
            "courses": courses,
            "course_id": selected,
            "sessions": [],
            "materials": [],
            "processing": {"total": 0, "by_status": {}, "jobs": [], "evidence_total": 0},
            "knowledge": {"count": 0, "points": [], "summary": None},
            "review_pending": [],
            "students": [],
            "exercises": [],
            "gaps": {"course_id": selected, "gaps": []},
        }
        if selected is None:
            return snapshot

        ctx = self.context(selected)
        points = self.knowledge_points(selected)
        snapshot["sessions"] = self.list_sessions(selected)
        snapshot["materials"] = self.list_materials(selected)
        snapshot["processing"] = self.processing_status(selected)
        snapshot["knowledge"] = {
            "count": len(points),
            "points": [
                {
                    "knowledge_id": point["knowledge_id"],
                    "title": point["title"],
                    "validation_status": point.get("validation_status"),
                    "review_status": point.get("review_status"),
                    "knowledge_score": point.get("knowledge_score"),
                }
                for point in points
            ],
            "summary": ctx.org_service.get_course_summary().to_dict(),
        }
        snapshot["review_pending"] = self.review_candidates(selected)
        snapshot["students"] = self.list_students(selected)
        snapshot["exercises"] = self.list_exercises(selected)
        snapshot["gaps"] = self.gaps(selected)
        return snapshot

    # ------------------------------------------------------------------
    # Task 56: 真实课堂工作台 (只读视图)
    # ------------------------------------------------------------------
    #
    # 这三个方法只是转发到 ``classroom_view.ClassroomWorkspaceView``。
    # 为什么不在这里直接写: 它们需要组合材料 / 处理 / 知识 / 审核 / 学习
    # 五个服务, 写进编排器会让"写"与"读"两种职责互相缠绕。
    #
    # 视图本身不落库 —— 它只读, 且不调用任何 ``_flush_*``。

    def _classroom_view(self) -> ClassroomWorkspaceView:
        if self._classroom is None:
            self._classroom = ClassroomWorkspaceView(self)
        return self._classroom

    def course_workspace(self, course_id: str) -> dict[str, Any]:
        """Task 56.1: 一门课的工作台 (课程信息 / 课堂 / 覆盖 / 待审核 / 最近材料)。"""
        return self._classroom_view().course_workspace(course_id)

    def session_workspace(self, course_id: str, session_id: str) -> dict[str, Any]:
        """Task 56.2/56.3: 一节课的完整工作台 + 推导出的课堂状态。"""
        return self._classroom_view().session_workspace(course_id, session_id)

    def today(
        self,
        *,
        course_id: Optional[str] = None,
        student_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Task 56.4: 今日入口 (今天的课程 / 课堂 / 未处理材料 / 待审核 / 待学习)。"""
        return self._classroom_view().today(
            course_id=course_id, student_id=student_id
        )

    # ------------------------------------------------------------------
    # 学生每日首页 (Task 63, 只读投影)
    # ------------------------------------------------------------------

    def student_today(
        self,
        *,
        course_id: Optional[str] = None,
        student_id: Optional[str] = None,
        lang: str = "zh",
    ) -> dict[str, Any]:
        """Task 63: 学生视角的今日首页 (课程 / 学习计划 / 路径 / 复习 / 练习 / 评估)。

        与 ``today()`` 的区别: ``today()`` 是**课堂侧**的今日工作台
        (今天有什么课、哪些材料没处理完); ``student_today()`` 是**学生侧**
        的"今天学什么", 两者组合既有事实, 互不重复计算 ——
        ``student_today`` 内部直接复用 ``today()`` 的课堂部分。

        只读: 不写库、不追加计划快照、不改任何状态。
        """
        if self._student_today is None:
            self._student_today = StudentTodayView(self)
        return self._student_today.today(
            course_id=course_id, student_id=student_id, lang=lang
        )

    # ------------------------------------------------------------------
    # 今天的学习流程 (Task 66)
    # ------------------------------------------------------------------
    #
    # ``LearningWorkflow`` 只做两件事: **选择**当前学习任务 + **投影**已
    # 有事实。它不重写 StudyPlan / LearningPath / Exercise / Evaluation ——
    # 全部经既有服务读取 (spec 66.3)。写操作只有两个:
    # ``viewed`` 事件 (打开知识页) 与作答 (委托既有路径), 两者都写穿落库。

    def _learning_workflow(self) -> LearningWorkflow:
        return LearningWorkflow(self)

    def learning_workflow_start(
        self, course_id: str, student_id: str, *, lang: str = "zh"
    ) -> dict[str, Any]:
        """「开始今天的学习」: 当前学习任务 + 前置 + 下一步。只读。"""
        return self._learning_workflow().start(course_id, student_id, lang=lang)

    def learning_workflow_knowledge(
        self,
        course_id: str,
        student_id: str,
        knowledge_point_id: str,
        *,
        lang: str = "zh",
        language: Optional[str] = None,
    ) -> dict[str, Any]:
        """知识学习页投影 (KP / 证据 / grounded explanation / 状态 / 前置)。只读。"""
        return self._learning_workflow().knowledge(
            course_id,
            student_id,
            knowledge_point_id,
            lang=lang,
            language=language,
        )

    def learning_workflow_open_knowledge(
        self,
        course_id: str,
        student_id: str,
        knowledge_point_id: str,
        *,
        lang: str = "zh",
        language: Optional[str] = None,
    ) -> dict[str, Any]:
        """打开知识页 = 读投影 + 记 ``viewed`` 事件 (Task 30 的合法事件)。

        重复打开是幂等的: 领域层按事件归约, 越序事件不改变状态。
        """
        with self._atomic():
            result = self._learning_workflow().open_knowledge(
                course_id,
                student_id,
                knowledge_point_id,
                lang=lang,
                language=language,
            )
            # ``open_knowledge`` 只记这个学生的 ``viewed`` 事件, 所以只写
            # 这一个学生的日志 (Task 69: 全量写会让"翻一页书"变成 O(全课))。
            self._flush_learning(
                course_id, student_ids=[student_id], exercise_ids=[], answer_ids=[]
            )
        return result

    def learning_workflow_exercise(
        self,
        course_id: str,
        student_id: str,
        knowledge_point_id: str,
        *,
        config: Any = None,
    ) -> dict[str, Any]:
        """当前知识点的练习 (已有则复用, 没有才幂等生成)。"""
        return self._learning_workflow().exercise_for_knowledge(
            course_id, student_id, knowledge_point_id, config=config
        )

    def learning_workflow_answer(
        self,
        course_id: str,
        student_id: str,
        exercise_id: str,
        submitted_value: str,
        sequence: int = 0,
    ) -> dict[str, Any]:
        """作答 -> 评价 -> 状态 -> 下一任务 (评价/状态/导航三者分离)。"""
        with self._atomic():
            result = self._learning_workflow().answer(
                course_id, student_id, exercise_id, submitted_value, sequence
            )
            # 同上: ``answer`` 内部走 ``exercise_submit`` -> ``submit_answer``,
            # 答案 + 该学生日志已经写穿。后面的状态/导航都是只读投影。
        return result

    # ------------------------------------------------------------------
    # 持久化写穿 (Task 49-52)
    # ------------------------------------------------------------------
    #
    # 设计: **写穿 (write-through)**, 而不是"退出时统一 flush"。
    #
    # 理由: 进程可能被强杀 (Task 54 的 crash harness 就专门做这件事)。
    # 只在 close() 里落盘的话, 崩溃会丢掉整个会话; 而写穿保证"已经返回给
    # 用户的成功操作"一定已经落库。close() 因此只需关连接, 不需要补偿。
    #
    # 每个 flush 都是幂等的 (仓储用 upsert), 所以重复调用安全。

    def _flush_course(self, course_id: str) -> None:
        """课程 + 课堂 (幂等)。"""
        p = self._persistence
        if p is None:
            return
        try:
            course = Course.from_dict(self.course_service.get_course(course_id))
        except NotFoundError:
            return
        p.save_course(course)
        for dto in self.course_service.list_sessions(course_id):
            p.save_session(ClassSession.from_dict(dto))

    def _flush_materials(self, course_id: str) -> None:
        """材料注册表 + 处理状态 (同一事务)。"""
        p = self._persistence
        if p is None:
            return
        ctx = self._contexts.get(course_id)
        if ctx is None:
            return
        p.save_material_records(ctx.workflow.list_materials())

    def _flush_evidence(self) -> None:
        """共享证据库 (增量, 保留插入顺序与生命周期状态)。"""
        p = self._persistence
        if p is None:
            return
        p.save_evidence_store(self.store)

    def _flush_knowledge(self, course_id: str) -> None:
        """知识点 / 溯源链 / 关系 / 冲突 + 评审历史 (幂等)。"""
        p = self._persistence
        if p is None:
            return
        ctx = self._contexts.get(course_id)
        if ctx is None:
            return
        structure = ctx.processing.structure
        if structure is None:
            return
        p.save_knowledge_structure(structure, course_id=course_id)
        # 评审历史属于审核域, 不在知识结构的写入路径上 —— 单独落盘。
        p.save_review_records(
            ctx.review_service.review_records, course_id=course_id
        )

    def _flush_organization(self, course_id: str) -> None:
        """主题 / 归属 / 关系图 (课程隔离)。"""
        p = self._persistence
        if p is None:
            return
        ctx = self._contexts.get(course_id)
        if ctx is None:
            return
        p.save_organization_structure(ctx.org_service.structure)

    def _flush_learning(
        self,
        course_id: str,
        *,
        student_ids: Optional[Iterable[str]] = None,
        exercise_ids: Optional[Iterable[str]] = None,
        answer_ids: Optional[Iterable[str]] = None,
    ) -> None:
        """学生 / 学习日志 / 练习 / 作答 / 评估 (幂等)。

        三个可选参数给出时**只写这些**对象, 不写整门课。这不是"可选的优化"
        —— Task 69 实测: 提交**一条**答案会把整门课的全部练习、全部学生的
        日志、全部答案与评估重写一遍。于是单条答案 68ms, 20000 条答案是
        O(N²) 条 SQL, 一门课答完要 23 分钟; 真实学期规模根本跑不动。

        只写改动过的那些是安全的, 因为持久化是**写穿**的: 没改动的对象在
        上一次写入时就已经落盘, 跳过它们不会造成丢失, 也不会改变任何
        可观测状态 (保存本身是幂等的 upsert)。

        空列表 = "一个都不写" (和 ``None`` = "全部写" 严格区分): 提交答案
        不改练习对象, 所以那里给 ``exercise_ids=[]``。练习仓储是按 id 幂等
        upsert 的, 不会先按课程清空再插入, 所以**写子集不会删掉没写的**
        ——这一点是 Task 69 查过 ``ExerciseRepository.save`` 才敢窄化的。
        """
        p = self._persistence
        if p is None:
            return
        ctx = self._contexts.get(course_id)
        if ctx is None:
            return
        service = ctx.learning_service

        exercises = service._all_exercises()
        if exercise_ids is None:
            selected_exercises = list(exercises.values())
        else:
            selected_exercises = [
                exercises[eid] for eid in exercise_ids if eid in exercises
            ]
        p.save_exercises(selected_exercises)

        if student_ids is None:
            for student in service._all_students().values():
                log = service._student_logs.get(student.student_id)
                if log is not None:
                    p.save_student_log(log, course_id=course_id)
        else:
            for sid in student_ids:
                log = service._student_logs.get(sid)
                if log is not None:
                    p.save_student_log(log, course_id=course_id)

        answers = service._answer_log.answers_for_all()
        if answer_ids is not None:
            wanted = {str(aid) for aid in answer_ids}
            answers = [a for a in answers if a.answer_id in wanted]
        if answers:
            p.save_answers(
                answers, course_id=course_id, submitted_at=service._submitted_at
            )
            by_id = {answer.answer_id: answer for answer in answers}
            results = [
                r for r in service._answer_log.results_for_all()
                if r.answer_id in by_id
            ]
            p.save_evaluations(results, answers=by_id)

    def _flush_plans(self, course_id: str, student_id: str) -> None:
        """学习计划快照 (不可变, 内容寻址 —— 旧快照保留可审计)。"""
        p = self._persistence
        if p is None:
            return
        ctx = self._contexts.get(course_id)
        if ctx is None:
            return
        from src.study_plan import StudyPlan

        dto = ctx.learning_service.get_study_plan(student_id)
        p.save_study_plans([StudyPlan.from_dict(dto)])

    def _flush_processing(self, course_id: str) -> None:
        """处理流水线之后的整体落盘。

        顺序: 证据 -> 材料 -> 知识 -> 组织 -> 学习。

        理由: 知识点引用证据、组织层的课堂归属引用知识点、学习层的作答引用
        练习与知识点; 而 ``material_evidence`` 关系表对 ``evidence`` 有外键。
        按依赖顺序写, 外键在每一步都是可满足的 (提交时不会被数据库拒绝),
        因此这里**不需要**推迟外键检查。
        """
        self._flush_evidence()
        self._flush_materials(course_id)
        self._flush_knowledge(course_id)
        self._flush_organization(course_id)
        self._flush_learning(course_id)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def material_integrity(self, *, verify_hash: bool = False) -> dict[str, Any]:
        """文件 <-> 数据库一致性诊断 (Task 49 / Task 55)。

        数据库是业务数据的 source of truth, 但材料**内容**在磁盘上。两者
        可能不同步 (用户手工删了 ``data/materials/...``、从备份恢复时只
        恢复了库、磁盘坏道)。本方法把不一致**显式报出来**:

        - ``MATERIAL_FILE_MISSING`` —— 库里有记录, 磁盘上没有文件。
        - ``MATERIAL_PATH_UNKNOWN`` —— 记录里没有可解析的存储位置。
        - ``MATERIAL_HASH_MISMATCH`` —— 文件在, 但内容哈希对不上
          (只在 ``verify_hash=True`` 时检查; 默认不读文件内容, 因为
          材料可能是几百 MB 的音频)。

        数据源是**数据库**, 不是内存注册表: 刚启动、还没为某门课建立上下文
        时, 内存里是空的, 而库里是有记录的 —— 只查内存会把"文件丢了"报成
        "一切正常"。
        """
        records: list[dict[str, Any]] = []
        if self._persistence is not None:
            records = list(self._persistence.load_material_records())
        else:
            for ctx in self._contexts.values():
                records.extend(ctx.workflow.list_materials())

        findings: list[dict[str, Any]] = []
        checked = 0
        for record in records:
            material_id = str(record.get("material_id") or "")
            if not material_id:
                continue
            checked += 1
            resolved = resolve_material_path(record, self._layout)
            base = {
                "material_id": material_id,
                "course_id": record.get("course_id"),
                "session_id": record.get("session_id"),
                "filename": record.get("filename"),
                "stored_path": record.get("stored_path"),
                "relative_path": record.get("relative_path"),
            }
            if resolved is None:
                findings.append(dict(base, diagnostic="MATERIAL_PATH_UNKNOWN"))
                continue
            if not os.path.isfile(resolved):
                findings.append(
                    dict(base, diagnostic="MATERIAL_FILE_MISSING", expected_path=resolved)
                )
                continue
            if verify_hash:
                expected = record.get("content_hash")
                if expected:
                    actual = _sha256_file(resolved)
                    if actual != expected:
                        findings.append(
                            dict(
                                base,
                                diagnostic="MATERIAL_HASH_MISMATCH",
                                expected_hash=expected,
                                actual_hash=actual,
                                path=resolved,
                            )
                        )
        return {
            "ok": not findings,
            "checked": checked,
            "verify_hash": bool(verify_hash),
            "findings": findings,
            "counts": {
                "missing": sum(
                    1 for f in findings if f["diagnostic"] == "MATERIAL_FILE_MISSING"
                ),
                "path_unknown": sum(
                    1 for f in findings if f["diagnostic"] == "MATERIAL_PATH_UNKNOWN"
                ),
                "hash_mismatch": sum(
                    1 for f in findings if f["diagnostic"] == "MATERIAL_HASH_MISMATCH"
                ),
            },
        }

    def health(self) -> dict[str, Any]:
        """spec 要求: application / version / database / storage / processing。

        数据库一节报告的是**真实**情况 (Task 48 起 SQLite 是业务数据的
        source of truth)。绝不把"文件存在"说成"数据已持久化"。

        ``storage.materials`` 报告材料文件与数据库记录的一致性: 只要有一条
        材料在库里而文件不在, 状态就是 ``degraded``。用户必须能从这里看到
        "数据库说有、磁盘说没有", 而不是在使用那份材料时才发现。
        """
        storage_ok = os.path.isdir(self._layout.root)
        for name in ("materials", "documents", "audio", "images", "temp"):
            storage_ok = storage_ok and os.path.isdir(getattr(self._layout, name))

        try:
            integrity = self.material_integrity()
        except Exception:  # noqa: BLE001 - 健康检查绝不因诊断本身而崩掉
            integrity = {
                "ok": False,
                "checked": 0,
                "counts": {"missing": 0, "path_unknown": 0, "hash_mismatch": 0},
                "error": "material integrity check failed",
            }
        materials_ok = bool(integrity.get("ok"))

        if self._persistence is None:
            database: dict[str, Any] = {
                "backend": "memory-only",
                "path": None,
                "ok": False,
                "schema_version": None,
                "note": "persistence is disabled for this workspace",
            }
            database_ok = True  # 显式关闭持久化不是"故障"
        else:
            try:
                schema_version = self._persistence.schema_version()
                healthy = self._persistence.database.is_healthy()
            except Exception:  # noqa: BLE001 - 健康检查绝不因存储异常而崩掉
                schema_version = None
                healthy = False
            database = {
                "backend": "sqlite",
                "path": self._persistence.path,
                "ok": bool(healthy),
                "schema_version": schema_version,
                "business_objects": self._persistence.counts(),
                # Task 53: 被回滚的写操作。**不是**故障 (回滚正是设计行为),
                # 但一个不断增长的数字说明有东西在持续失败, 运维需要看得见。
                "rollbacks": self._persistence.rollback_diagnostics()["count"],
            }
            database_ok = bool(healthy)

        return {
            "application": APPLICATION_NAME,
            "version": APPLICATION_VERSION,
            "status": (
                "ok" if (storage_ok and database_ok and materials_ok) else "degraded"
            ),
            "database": database,
            "storage": {
                "data_dir": self._layout.root,
                "ok": storage_ok,
                "courses": len(self._contexts),
                "materials": {
                    "checked": integrity.get("checked", 0),
                    "missing": integrity["counts"]["missing"],
                    "path_unknown": integrity["counts"]["path_unknown"],
                    "ok": materials_ok,
                },
            },
            "processing": {
                "asr": self._asr_mode,
                "ocr": self._ocr_mode,
                "sequential": True,
                "evidence_count": len(self.store.all()),
            },
            # §1: LLM 模式显式可见 (与 asr/ocr 同语义)。绝不静默把 Mock
            # 当作真实模型; key 永不出现在 health 里。
            "llm": {
                "mode": self._llm_mode,
                "provider": getattr(self.summarizer, "name", None) or "mock",
                "model": getattr(self.summarizer, "model", None) or "mock-extractive",
            },
        }
