# -*- coding: utf-8 -*-
"""Full Classroom Recording Pipeline (Task 37)。

把 Task 35 的材料工作流与 Task 25 的知识装配串成一条真正的课堂处理链::

    Audio / Image / PDF / DOCX
              ↓
           Material
              ↓
           Ingestion            (Task 35: MaterialWorkflowService)
              ↓
           Evidence             (EvidenceStore, 内容寻址)
              ↓
          Knowledge             (Task 25: KnowledgeAssembler)
              ↓
         Validation             (KnowledgeValidator, 只读)
              ↓
           Review               (候选列表, 人工决定)
              ↓
       Course Knowledge         (KnowledgeOrganizationService)

硬性原则 (Task 37 spec):
- **失败隔离**: 5 个文件里 2 个失败, 剩下 3 个照常成功, 整节课不会失败。
  报告始终给出 total / succeeded / failed / skipped。
- **重试有界**: 只有显式标记 retryable 的错误才会重试, 且上限由
  ``max_attempts`` 集中配置 (默认 3), 绝不无限重试。
- **默认顺序执行**: 第一版不加 multiprocessing / Celery / 分布式队列。
- 处理过程绝不制造事实: 知识只能来自 EvidenceStore 中真实存在的证据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from src.application.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
)
from src.application.material_workflow import (
    COMPLETED as MATERIAL_COMPLETED,
    DEFAULT_MAX_ATTEMPTS,
    FAILED as MATERIAL_FAILED,
    MaterialWorkflowService,
    RETRYABLE_ERROR_CODES,
)
from src.application.runtime import Clock, utc_now_iso

__all__ = [
    "JOB_QUEUED",
    "JOB_RUNNING",
    "JOB_SUCCEEDED",
    "JOB_FAILED",
    "JOB_CANCELLED",
    "JOB_STATUSES",
    "ProcessingJob",
    "ClassroomProcessingService",
]

JOB_QUEUED = "QUEUED"
JOB_RUNNING = "RUNNING"
JOB_SUCCEEDED = "SUCCEEDED"
JOB_FAILED = "FAILED"
JOB_CANCELLED = "CANCELLED"

#: spec 要求的统一作业状态集合。
JOB_STATUSES: tuple[str, ...] = (JOB_QUEUED, JOB_RUNNING, JOB_SUCCEEDED, JOB_FAILED, JOB_CANCELLED)

#: 作业阶段 (可观测的处理进度)。
STAGE_QUEUED = "QUEUED"
STAGE_INGESTING = "INGESTING"
STAGE_EVIDENCE = "EVIDENCE"
STAGE_KNOWLEDGE = "KNOWLEDGE"
STAGE_DONE = "DONE"

#: 处理阶段条 (Task 57.1) —— 用户看到的"这节课处理到哪一步"。
PROCESS_STAGES: tuple[str, ...] = (
    "PREPARING",
    "PROCESSING_MATERIALS",
    "EXTRACTING_EVIDENCE",
    "ASSEMBLING_KNOWLEDGE",
    "VALIDATING",
    "CHECKING_CONFLICTS",
    "FINISHED",
)

#: 全局阶段文案 (中文是基准语言, 英文 key 仅供 UI 枚举徽章使用)。
PROCESS_STAGE_LABELS: dict[str, str] = {
    "PREPARING": "准备材料",
    "PROCESSING_MATERIALS": "处理材料",
    "EXTRACTING_EVIDENCE": "提取证据",
    "ASSEMBLING_KNOWLEDGE": "组装知识",
    "VALIDATING": "验证",
    "CHECKING_CONFLICTS": "检查冲突",
    "FINISHED": "完成",
}

#: Task 57.3: 失败材料的"推荐操作"必须是**可判定的**, 不能把 traceback 甩给
#: 用户, 也不能用 LLM 现编。这里用错误码 -> 确定性中文文案的映射。
_RECOMMENDED_ACTIONS: dict[str, str] = {
    "UNSUPPORTED_EXTENSION": "请转换为支持的格式（pdf / docx / txt / md / 音频 / 图片）后重新上传",
    "PATH_TRAVERSAL": "文件名含非法路径，请重命名后再上传",
    "ZERO_BYTE_FILE": "文件为空，请重新上传有内容的文件",
    "EMPTY_PATH": "文件路径为空，请重新上传",
    "OVERSIZED_FILE": "文件过大，请压缩或拆分后上传",
    "FILE_NOT_FOUND": "原始文件已丢失，请重新上传",
    "DOCUMENT_PARSE_FAILED": "文档解析失败（可能已损坏），请重新上传完好的文件",
    "UNSUPPORTED_SOURCE": "来源类型不被支持，请更换材料类型",
    "MALFORMED_EVIDENCE": "证据格式异常，请重新上传原始材料",
    "COPY_FAILED": "文件复制失败，请检查磁盘空间后重试",
    "STORAGE_ERROR": "存储写入失败，请检查磁盘空间后重试",
    "INGESTION_FAILED": "摄取失败，可点击重试；若持续失败请手动录入",
    "ASR_ERROR": "语音识别失败，可点击重试；若持续失败请改用文字笔记",
    "EXTRACTOR_ERROR": "内容提取失败，可点击重试；若持续失败请手动录入",
    "STORE_REJECTION": "证据被拒收，请检查材料内容后重试",
    "INTERNAL_ERROR": "内部处理错误，可点击重试；若持续失败请联系支持",
}


def recommended_action(error_code: Optional[str], retryable: bool) -> str:
    """把失败错误码翻译成用户可执行的"推荐操作" (Task 57.3)。

    确定性、无 LLM、无 traceback。未知错误码退回通用的"可重试 / 需人工"。
    """
    if not error_code:
        return ""
    action = _RECOMMENDED_ACTIONS.get(error_code)
    if action is not None:
        return action
    return "可点击重试" if retryable else "需人工处理，请检查材料后重新上传"


@dataclass
class ProcessingJob:
    """一个 Material 的处理作业 (spec: job_id / material_id / status /
    started_at / finished_at / error)。"""

    job_id: str
    material_id: str
    course_id: str
    session_id: Optional[str] = None
    status: str = JOB_QUEUED
    stage: str = STAGE_QUEUED
    # Task 56: "这个作业被显式排进过处理队列吗?"
    #
    # ``get_status()`` 会为**每个已注册材料**补建一个 QUEUED 作业 (否则
    # 面板会显示"没有作业"而实际有材料在等)。于是 "QUEUED" 同时表示两件
    # 不同的事: "用户点了处理, 在排队" 和 "材料刚注册, 还没人动过"。
    # 课堂状态 (spec 56.3) 必须区分这两者 —— 否则 MATERIALS_ADDED 永远
    # 达不到, 面板会把"上传完还没处理"显示成"处理中"。
    enqueued: bool = False
    attempts: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    error_detail: Optional[str] = None
    retryable: bool = False
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    quality: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "material_id": self.material_id,
            "course_id": self.course_id,
            "session_id": self.session_id,
            "status": self.status,
            "stage": self.stage,
            "enqueued": self.enqueued,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "error_detail": self.error_detail,
            "retryable": self.retryable,
            "recommended_action": recommended_action(self.error, self.retryable),
            "evidence_ids": list(self.evidence_ids),
            "evidence_count": len(self.evidence_ids),
            "warnings": list(self.warnings),
            "quality": self.quality,
        }


class ClassroomProcessingService:
    """课堂处理编排器。

    构造:
    - workflow: Task 35 的 :class:`MaterialWorkflowService` (材料 + 摄取)。
    - assembler: 可选的知识装配器 (默认 :class:`KnowledgeAssembler`)。
    - org_service: 可选的知识组织服务; 缺省时复用 workflow 的。
    - knowledge_service: 可选的应用层知识服务 (用于 coverage / gaps /
      review 候选统计)。
    - quality_source: 可选对象, 提供 ``report_for_material(material_id)``
      (通常就是 :class:`QualityCheckedASRProvider`)。
    - clock: 可注入时钟, 保证测试确定性。
    """

    def __init__(
        self,
        workflow: MaterialWorkflowService,
        *,
        assembler: Optional[Any] = None,
        org_service: Optional[Any] = None,
        knowledge_service: Optional[Any] = None,
        quality_source: Optional[Any] = None,
        clock: Optional[Clock] = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        if workflow is None:
            raise InvalidInputError("workflow is required")
        if max_attempts <= 0:
            raise InvalidInputError("max_attempts must be positive")
        self._workflow = workflow
        self._course_id = workflow.course_id
        self._store = workflow.store
        self._org = org_service
        self._assembler = assembler
        self._knowledge_service = knowledge_service
        self._quality_source = quality_source
        self._clock: Clock = clock or utc_now_iso
        self._max_attempts = max_attempts
        self._jobs: dict[str, ProcessingJob] = {}
        self._structure: Optional[Any] = None

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def course_id(self) -> str:
        return self._course_id

    @property
    def store(self) -> Any:
        return self._store

    @property
    def structure(self) -> Optional[Any]:
        """最近一次知识装配产出的 KnowledgeStructure (可能为 None)。"""
        return self._structure

    def restore_structure(self, structure: Any) -> None:
        """从持久化状态恢复装配结果 (Task 50)。

        恢复的是**领域对象本身** (由 ``KnowledgeStructure.from_dict`` 重建),
        不是"看起来一样"的空壳。此后 :meth:`assemble_knowledge` 会在这份
        结构上增量装配, 因此"重启后继续处理新材料"不会把已有知识抹掉。
        """
        self._structure = structure

    @property
    def org_service(self) -> Any:
        """知识组织服务: 显式注入优先, 否则复用工作流的那一个实例。"""
        if self._org is None:
            self._org = self._workflow.org_service
        return self._org

    # ------------------------------------------------------------------
    # 作业编排
    # ------------------------------------------------------------------

    def start_session_processing(self, session_id: str) -> dict[str, Any]:
        """为一节课的所有材料建立 QUEUED 作业 (不执行处理)。"""
        materials = self._session_materials(session_id)
        jobs = [self._ensure_job(record) for record in materials]
        for job in jobs:
            if job.status in (JOB_SUCCEEDED, JOB_RUNNING):
                continue
            job.status = JOB_QUEUED
            job.stage = STAGE_QUEUED
            job.enqueued = True
        return {
            "course_id": self._course_id,
            "session_id": session_id,
            "queued": sum(1 for j in jobs if j.status == JOB_QUEUED),
            "total": len(jobs),
            "jobs": [j.to_dict() for j in jobs],
        }

    def process_material(self, material_id: str) -> dict[str, Any]:
        """处理一个材料 (幂等: 已成功的作业直接返回)。"""
        record = self._workflow.get_material(material_id)
        job = self._ensure_job(record)
        if job.status == JOB_SUCCEEDED:
            return job.to_dict()
        if job.status == JOB_CANCELLED:
            return job.to_dict()

        job.status = JOB_RUNNING
        job.stage = STAGE_INGESTING
        job.started_at = self._clock()
        job.error = None
        job.error_detail = None

        result = self._workflow.process_material(material_id)
        job.attempts = int(result.get("attempts", 0))
        status = result.get("processing_status")

        if status == MATERIAL_COMPLETED:
            job.status = JOB_SUCCEEDED
            job.stage = STAGE_KNOWLEDGE
            job.evidence_ids = tuple(result.get("evidence_ids") or ())
            job.retryable = False
            job.error = None
            warnings: list[str] = []
            if result.get("warning"):
                warnings.append(str(result["warning"]))
            quality = self._quality_for(material_id)
            if quality is not None:
                job.quality = quality
                if quality.get("status") == "INVALID":
                    warnings.append("TRANSCRIPT_QUALITY_INVALID")
                elif quality.get("status") == "WARNING":
                    warnings.append("TRANSCRIPT_QUALITY_WARNING")
            job.warnings = tuple(warnings)
            job.stage = STAGE_DONE
        else:
            job.status = JOB_FAILED
            job.stage = STAGE_EVIDENCE
            job.error = result.get("error") or "PROCESSING_FAILED"
            job.error_detail = result.get("error_detail")
            job.retryable = bool(result.get("retryable"))
            job.evidence_ids = ()
        job.finished_at = self._clock()
        return job.to_dict()

    def process_session(self, session_id: str) -> dict[str, Any]:
        """顺序处理一节课的全部材料, 单文件失败不影响整节课。"""
        materials = self._session_materials(session_id)
        self.start_session_processing(session_id)
        jobs: list[dict[str, Any]] = []
        for record in materials:
            jobs.append(self.process_material(str(record["material_id"])))
        # 不显式给作用域 —— ``assemble_knowledge`` 的默认作用域是**本门课**,
        # 即本课全部材料产出的证据。不能更小 (只装配本节课) 也不能更大
        # (装配整个共享证据库): 前者会让同一门课里跨节课的矛盾证据组不成
        # CONFLICTED, 后者会把别门课的证据装配进这门课 (Task 68 实测:
        # 三门课的知识点数变成 28/48/68, 后建的课凭空包含先建的课的全部
        # 知识点)。证据库是全局的 —— 内容相同的材料产出同一个 evidence_id
        # —— 所以"这门课有哪些证据"只能由本课程的材料注册表回答。
        knowledge = self.assemble_knowledge()
        return self._session_report(session_id, jobs, knowledge)

    def retry_failed_material(self, material_id: str) -> dict[str, Any]:
        """重试一个 FAILED 且 retryable 的作业。

        结构性拒绝 (路径穿越 / 不支持的扩展名 / 空文件 / 超大文件) 与
        达到 max_attempts 的作业都不会被重试。
        """
        record = self._workflow.get_material(material_id)
        job = self._ensure_job(record)
        if job.status in (JOB_SUCCEEDED, JOB_CANCELLED):
            return job.to_dict()
        if job.attempts >= self._max_attempts:
            job.retryable = False
            return job.to_dict()
        if job.error not in RETRYABLE_ERROR_CODES:
            job.retryable = False
            return job.to_dict()
        self._workflow.retry_material(material_id)
        job.status = JOB_QUEUED
        job.enqueued = True
        return self.process_material(material_id)

    def cancel_session(self, session_id: str) -> dict[str, Any]:
        """把该节课尚未开始的作业标记为 CANCELLED。"""
        cancelled = 0
        for job in self._jobs.values():
            if job.session_id != session_id:
                continue
            if job.status in (JOB_QUEUED, JOB_RUNNING):
                job.status = JOB_CANCELLED
                job.finished_at = self._clock()
                cancelled += 1
        return {"session_id": session_id, "cancelled": cancelled}

    # ------------------------------------------------------------------
    # 知识装配 (Evidence -> Knowledge -> Validation -> Review)
    # ------------------------------------------------------------------

    def assemble_knowledge(
        self,
        session_id: Optional[str] = None,
        evidence_ids: Optional[Iterable[str]] = None,
    ) -> dict[str, Any]:
        """从 EvidenceStore 装配知识并发布到课程组织服务。

        只使用 store 中**真实存在**的证据; 不新增、不推断、不翻译。

        **作用域 (Task 68)**: ``EvidenceStore`` 是全局的 —— 内容相同的材料
        产出同一个 ``evidence_id`` —— 所以"装配哪些证据"必须显式限定:

        - ``evidence_ids`` —— 装配这几条;
        - ``session_id`` —— 装配这一节课的材料产出的证据;
        - 都不给 —— 装配**本门课**全部材料产出的证据 (默认)。

        默认绝不是"整个 store": 那会把别门课的证据装配进这门课 (实测:
        三门结构相同的课的知识点数变成 28/48/68, 后建的课凭空包含先建的课
        的全部知识点)。反过来, 处理单个材料时也不能只装配那一个材料 ——
        同一门课里跨材料 / 跨节课的矛盾证据就组不成 CONFLICTED 了, 那是
        spec 要求的真值状态, 不是可选项。
        """
        assembler = self._assembler
        if assembler is None:
            from src.knowledge_assembly import KnowledgeAssembler

            assembler = KnowledgeAssembler()

        if evidence_ids is not None:
            wanted = {str(e) for e in evidence_ids}
            selected = [e for e in self._store.all() if e.evidence_id in wanted]
        else:
            if session_id is not None:
                material_ids = {
                    str(r["material_id"]) for r in self._session_materials(session_id)
                }
            else:
                material_ids = self._course_material_ids()
            selected = [
                e
                for e in self._store.all()
                if e.source_reference is not None
                and e.source_reference.material_id in material_ids
            ]

        # 与 process_store 同一条理由: 装配结果必须只取决于**证据集合**,
        # 不取决于遍历顺序 (插入顺序是摄取历史的函数)。见
        # KnowledgeAssembler.process_store 的 docstring。
        selected.sort(key=lambda e: e.evidence_id)
        result = assembler.process_evidence(selected, structure=self._structure)
        self._structure = result.structure
        self.org_service.register_knowledge_structure(result.structure)
        return {
            "course_id": self._course_id,
            "evidence_count": len(result.evidence_ids),
            "new_knowledge_point_ids": list(result.new_knowledge_point_ids),
            "updated_knowledge_point_ids": list(result.updated_knowledge_point_ids),
            "new_relationship_ids": list(result.new_relationship_ids),
            "conflict_ids": list(result.conflict_ids),
            "knowledge_point_total": len(result.structure.knowledge_points),
        }

    def get_knowledge_summary(self) -> dict[str, Any]:
        """装配结果的只读摘要 (验证状态 / 冲突 / 待复核)。"""
        structure = self._structure
        if structure is None:
            return {
                "course_id": self._course_id,
                "knowledge_point_total": 0,
                "conflict_count": 0,
                "review_pending": 0,
                "validation": {},
            }
        validation: dict[str, int] = {}
        review_pending = 0
        for kp in structure.knowledge_points.values():
            status = str(getattr(kp, "validation_status", "unknown"))
            validation[status] = validation.get(status, 0) + 1
            if str(getattr(kp, "review_status", "pending")) == "pending":
                review_pending += 1
        return {
            "course_id": self._course_id,
            "knowledge_point_total": len(structure.knowledge_points),
            "conflict_count": len(getattr(structure, "conflicts", {}) or {}),
            "review_pending": review_pending,
            "validation": validation,
        }

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def get_status(self, session_id: Optional[str] = None) -> dict[str, Any]:
        """作业状态汇总; 给定 session_id 时只统计该节课。

        已注册但尚未建立作业的材料也会被纳入 (状态按材料记录推导为
        QUEUED) —— 否则 dashboard 会显示"没有作业"而实际上有材料在等着
        处理。
        """
        materials = self._workflow.list_materials()
        if session_id is not None:
            materials = [m for m in materials if m.get("session_id") == session_id]
        for record in materials:
            self._ensure_job(record)
        jobs = list(self._jobs.values())
        if session_id is not None:
            jobs = [j for j in jobs if j.session_id == session_id]
        counts = {status: 0 for status in JOB_STATUSES}
        for job in jobs:
            if job.status in counts:
                counts[job.status] += 1
        return {
            "course_id": self._course_id,
            "session_id": session_id,
            "total": len(jobs),
            "by_status": counts,
            "evidence_total": sum(len(j.evidence_ids) for j in jobs),
            "jobs": [j.to_dict() for j in jobs],
        }

    def get_job(self, material_id: str) -> dict[str, Any]:
        job = self._jobs.get(str(material_id))
        if job is None:
            raise NotFoundError(f"no processing job for material {material_id!r}")
        return job.to_dict()

    def peek_job(self, material_id: str) -> Optional[dict[str, Any]]:
        """作业状态的非抛异常视图 (材料页统一分析状态用)。

        与 :meth:`get_job` 的区别: 没有作业返回 ``None`` 而不是 404 ——
        "还没处理过"是一条正常信息, 不是错误。
        """
        job = self._jobs.get(str(material_id))
        return job.to_dict() if job is not None else None

    def discard_job(self, material_id: str) -> dict[str, Any]:
        """删除材料时移除其处理作业 (材料页「删除」的后半段)。

        语义: 作业从 ``_jobs`` 里**移除** (之后 ``get_job`` 404, 轮询自然
        收尾), 同时把内存里的作业对象标记 CANCELLED —— 同步执行模型下,
        若删除恰好与另一次 ``process_material`` 并发, 那个线程手里已持有
        作业引用; CANCELLED 标记保证它收尾时不会把作业报成 SUCCEEDED。
        摄取产物不会复活: 注册表行与 ``_flush_materials`` 都来自
        ``list_materials()``, 而那条记录已被删除。
        """
        job = self._jobs.pop(str(material_id), None)
        if job is None:
            return {"existed": False, "was_running": False}
        was_running = job.status == JOB_RUNNING
        job.status = JOB_CANCELLED
        job.finished_at = self._clock()
        return {
            "existed": True,
            "was_running": was_running,
            "job_id": job.job_id,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _session_materials(self, session_id: str) -> list[dict[str, Any]]:
        if not isinstance(session_id, str) or not session_id.strip():
            raise InvalidInputError("session_id must be a non-empty string")
        materials = self._workflow.list_session_materials(session_id)
        if not materials:
            # 明确区分"这节课没有材料"和"这节课不存在"。
            raise NotFoundError(f"no materials registered for session {session_id!r}")
        return materials

    def _course_material_ids(self) -> set[str]:
        """本门课已注册的全部材料 id —— 装配知识的默认作用域。

        ``MaterialWorkflowService`` 本身是按课程构造的 (注册表里写着
        ``course_id``, 加载时会核对), 所以 ``list_materials()`` 返回的
        就是这门课的材料, 不含别门课的。
        """
        return {
            str(record["material_id"])
            for record in self._workflow.list_materials()
            if record.get("material_id")
        }

    def _ensure_job(self, record: Mapping[str, Any]) -> ProcessingJob:
        material_id = str(record["material_id"])
        job = self._jobs.get(material_id)
        if job is not None:
            # Task 62: 材料可以被**改派**到另一节课 (同一份内容重新登记到
            # 新课堂, 见 MaterialWorkflowService._validate_and_register)。
            # 作业是按 material_id 惰性建立的, 已经存在时不能想当然地认为
            # 它的 session_id 还有效 —— 否则新课堂的处理报告会把这份材料
            # 算到旧课堂头上 (课程内串台, 且是静默的)。
            current = record.get("session_id")
            if job.session_id != current:
                job.session_id = current
            return job
        job = ProcessingJob(
            job_id=_deterministic_job_id(self._course_id, material_id),
            material_id=material_id,
            course_id=self._course_id,
            session_id=record.get("session_id"),
            max_attempts=self._max_attempts,
        )
        self._jobs[material_id] = job
        return job

    def _quality_for(self, material_id: str) -> Optional[dict[str, Any]]:
        if self._quality_source is None:
            return None
        try:
            report = self._quality_source.report_for_material(material_id)
        except Exception:  # noqa: BLE001 - 诊断查询失败不影响处理
            return None
        if report is None:
            return None
        if hasattr(report, "to_dict"):
            return report.to_dict()
        if isinstance(report, Mapping):
            return dict(report)
        return None

    def _session_report(
        self,
        session_id: str,
        jobs: Sequence[Mapping[str, Any]],
        knowledge: Mapping[str, Any],
    ) -> dict[str, Any]:
        succeeded = sum(1 for j in jobs if j["status"] == JOB_SUCCEEDED)
        failed = sum(1 for j in jobs if j["status"] == JOB_FAILED)
        cancelled = sum(1 for j in jobs if j["status"] == JOB_CANCELLED)
        return {
            "course_id": self._course_id,
            "session_id": session_id,
            "total": len(jobs),
            "succeeded": succeeded,
            "failed": failed,
            "cancelled": cancelled,
            "skipped": len(jobs) - succeeded - failed - cancelled,
            "evidence_total": sum(len(j["evidence_ids"]) for j in jobs),
            "jobs": list(jobs),
            "knowledge": dict(knowledge),
            "status": self.get_status(session_id)["by_status"],
        }


def _deterministic_job_id(course_id: str, material_id: str) -> str:
    import hashlib

    raw = f"{course_id}|{material_id}".encode("utf-8")
    return "job-" + hashlib.sha256(raw).hexdigest()[:20]
