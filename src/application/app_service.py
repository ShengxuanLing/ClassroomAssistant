# -*- coding: utf-8 -*-
"""应用服务门面 (Task 34)。

AppService 组合所有子服务 (Course / Material / Knowledge / Review /
Learning), 提供统一入口。各子服务通过构造函数注入, 共享同一套
domain 对象 (EvidenceStore / KnowledgeOrganizationService / ...),
保证跨服务的状态一致性与确定性。

本层只做组合与路由, 不引入新的业务规则; 所有事实仍来自 domain 层
(Material -> Evidence -> KnowledgePoint -> Review), UI/API 不得在此
制造新 "事实"。
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from src.models import Course, Evidence
from src.evidence_store import EvidenceStore
from src.evidence_ingestion import EvidenceIngestionService
from src.knowledge_organization import KnowledgeOrganizationService
from src.knowledge_structure import KnowledgePoint

from src.application.course_service import CourseService
from src.application.knowledge_service import KnowledgeService, ReviewService
from src.application.learning_service import LearningService
from src.application.material_service import MaterialService
from src.application.material_workflow import (
    DEFAULT_MAX_ATTEMPTS,
    MaterialWorkflowService,
)
from src.application.processing_service import ClassroomProcessingService
from src.application.runtime import Clock
from src.application.errors import (
    ApplicationError,
    InvalidInputError,
    NotFoundError,
    map_application_error,
)

__all__ = ["AppService"]


class AppService:
    """课堂助手应用服务门面。

    构造:
    - EvidenceStore: 证据统一去重存储
    - EvidenceIngestionService: 材料 -> 证据 摄取
    - KnowledgeOrganizationService: 课程知识组织
    - LearningService: 学生/练习/学习 (按 course_id 绑定)

    所有子服务共享同一 EvidenceStore; 重复调用 create / ingest 幂等。
    异常统一映射为 ApplicationError (结构化错误码)。
    """

    def __init__(
        self,
        course: Course,
        store: Optional[EvidenceStore] = None,
        ingestion_service: Optional[EvidenceIngestionService] = None,
        org_service: Optional[KnowledgeOrganizationService] = None,
        *,
        source_root: Optional[str] = None,
        data_dir: Optional[str] = None,
        max_file_size: int = 200 * 1024 * 1024,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        clock: Optional[Clock] = None,
        quality_source: Optional[Any] = None,
    ) -> None:
        if course is None or not course.course_id:
            raise InvalidInputError("course must be a Course with a non-empty course_id")
        self._course = course
        self._course_id = course.course_id

        # 注入或新建 EvidenceStore / IngestionService
        self._store = store or EvidenceStore()
        self._ingestion_service = ingestion_service or EvidenceIngestionService(self._store)

        # 课程组织服务
        self._org_service = org_service or KnowledgeOrganizationService(self._course)

        # 子服务
        self.course_service = CourseService()
        self.material_service = MaterialService(self.course_service, self._ingestion_service)
        self.knowledge_service = KnowledgeService(self._org_service, store=self._store)
        self.review_service = ReviewService(self._org_service)
        self.learning_service = LearningService(self._course_id)

        # Task 35: 真实材料工作流 (仅在提供 data_dir 时启用)。
        # 文件必须被复制到应用管理目录, 因此没有 data_dir 就无法安全地
        # 接收真实文件 —— 此时显式不提供该能力, 而不是偷偷就地读取用户文件。
        self.material_workflow: Optional[MaterialWorkflowService] = None
        self.processing_service: Optional[ClassroomProcessingService] = None
        if data_dir is not None:
            self.material_workflow = MaterialWorkflowService(
                self._course,
                source_root or data_dir,
                data_dir,
                store=self._store,
                ingestion_service=self._ingestion_service,
                org_service=self._org_service,
                max_file_size=max_file_size,
                max_attempts=max_attempts,
                clock=clock,
            )
            # Task 37: 课堂处理编排 (材料 -> 证据 -> 知识 -> 校验 -> 复核)。
            self.processing_service = ClassroomProcessingService(
                self.material_workflow,
                org_service=self._org_service,
                knowledge_service=self.knowledge_service,
                quality_source=quality_source,
                clock=clock,
                max_attempts=max_attempts,
            )

    # ------------------------------------------------------------------
    # 属性 / 路由
    # ------------------------------------------------------------------

    @property
    def course_id(self) -> str:
        return self._course_id

    @property
    def store(self) -> EvidenceStore:
        return self._store

    @property
    def ingestion_service(self) -> EvidenceIngestionService:
        return self._ingestion_service

    @property
    def org_service(self) -> KnowledgeOrganizationService:
        return self._org_service

    # ------------------------------------------------------------------
    # 知识注册 (Evidence -> KnowledgePoint, 幂等)
    # ------------------------------------------------------------------

    def register_knowledge_point(
        self,
        knowledge_point: Mapping[str, Any],
    ) -> dict[str, Any]:
        """登记一个 KnowledgePoint 到组织服务 (幂等: 相同 knowledge_id 不重复)。

        接受 dict 形式 (来自 API/UI), 内部转换为 domain KnowledgePoint。
        必须包含 knowledge_id / title / content。
        登记同时确保 KP 出现在 KnowledgeService 可查询范围内。
        """
        if not isinstance(knowledge_point, Mapping):
            raise InvalidInputError(
                f"knowledge_point must be a mapping, got {type(knowledge_point).__name__}"
            )
        kp_id = _require_nonempty_str(knowledge_point.get("knowledge_id"), "knowledge_id")
        title = _require_nonempty_str(knowledge_point.get("title"), "title")
        content = _require_nonempty_str(knowledge_point.get("content"), "content")

        kp = KnowledgePoint(
            knowledge_id=kp_id,
            title=title,
            content=content,
            original_terms=list(knowledge_point.get("original_terms") or []),
            importance=str(knowledge_point.get("importance", "medium")),
            confidence=_confidence_value(knowledge_point.get("confidence")),
            evidence_refs=list(knowledge_point.get("evidence_refs") or []),
            related_points=list(knowledge_point.get("related_points") or []),
            needs_verification=bool(knowledge_point.get("needs_verification", False)),
            validation_status=str(knowledge_point.get("validation_status", "unverified")),
            knowledge_score=float(knowledge_point.get("knowledge_score", 0.0)),
            review_status=str(knowledge_point.get("review_status", "pending")),
        )
        self._org_service.register_knowledge_point(kp)
        return self.knowledge_service.get_knowledge_point(kp_id)

    def list_knowledge_points(
        self,
        session_id: Optional[str] = None,
        topic_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return self.knowledge_service.get_knowledge_points(
            course_id=self._course_id,
            session_id=session_id,
            topic_id=topic_id,
        )

    # ------------------------------------------------------------------
    # 统一错误映射
    # ------------------------------------------------------------------

    @staticmethod
    def map_error(exc: BaseException) -> ApplicationError:
        """把子服务抛出的异常映射为结构化 ApplicationError。

        - 已是 ApplicationError: 原样返回。
        - 携带 .code 的 domain 错误: 按语义映射。
        - 其他: INTERNAL_ERROR (message 保守化, 不暴露 traceback)。
        """
        return map_application_error(exc)


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _require_nonempty_str(value: Any, field_name: str) -> str:
    if value is None:
        raise InvalidInputError(f"{field_name} is required")
    if not isinstance(value, str):
        raise InvalidInputError(
            f"{field_name} must be a string, got {type(value).__name__}"
        )
    stripped = value.strip()
    if not stripped:
        raise InvalidInputError(f"{field_name} must be a non-empty string")
    return stripped


def _confidence_value(raw: Any) -> Optional[Any]:
    """把 confidence 字符串 / None 解析为 domain Confidence 枚举。"""
    if raw is None:
        return None
    from src.models import Confidence

    if isinstance(raw, Confidence):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        for member in Confidence:
            if member.value.lower() == normalized:
                return member
    return None
