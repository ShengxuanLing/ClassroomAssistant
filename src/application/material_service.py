# -*- coding: utf-8 -*-
"""材料注册 / 摄取 服务 (Task 34)。

把 EvidenceIngestionService 包装为稳定业务入口:
- register_material: 登记材料 (幂等, 由 CourseService 管理)
- ingest_material: 摄取单个材料 -> 结构化 IngestionReport DTO
- ingest_material_batch: 批量摄取 -> 结构化 BatchIngestionReport DTO
- get_ingestion_status: 查询指定材料的摄取状态

不绕过 domain 层, 所有证据仍通过 EvidenceStore 统一去重。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.models import Material
from src.evidence_store import EvidenceStore
from src.evidence_ingestion import (
    BatchIngestionReport,
    EvidenceIngestionService,
    IngestionReport,
)
from src.application.course_service import CourseService
from src.application.dto import material_to_dict
from src.application.errors import (
    InvalidInputError,
    NotFoundError,
)

__all__ = ["MaterialService"]


def _require_nonempty_str(value: Any, field_name: str) -> str:
    if value is None:
        raise InvalidInputError(f"{field_name} is required")
    if not isinstance(value, str):
        raise InvalidInputError(f"{field_name} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise InvalidInputError(f"{field_name} must be a non-empty string")
    return stripped


class MaterialService:
    """材料注册 / 摄取 的统一入口。

    - 材料元数据通过 CourseService.register_material() 登记
    - 摄取通过 EvidenceIngestionService 执行
    - 摄取状态可查询 (IngestionStatus 枚举值)
    """

    def __init__(
        self,
        course_service: CourseService,
        ingestion_service: EvidenceIngestionService,
    ) -> None:
        self._course_service = course_service
        self._ingestion_service = ingestion_service

    @property
    def store(self) -> EvidenceStore:
        """底层 EvidenceStore (供其他服务共享)。"""
        return self._ingestion_service.store

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------

    def register_material(
        self,
        course_id: str,
        session_id: Optional[str],
        filename: str,
        material_type: str = "text",
        language: str = "",
        path: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """登记一个 Material 到课程 (幂等)。

        材料 ID 由调用方 (UI/API 层) 提供 (稳定, 基于内容 hash + 课程 + 来源,
        参见 Task 35 规范)。
        返回 Material DTO。
        """
        cid = _require_nonempty_str(course_id, "course_id")
        sid = _require_nonempty_str(session_id, "session_id") if session_id else None
        fname = _require_nonempty_str(filename, "filename")

        merged_meta = dict(metadata or {})
        merged_meta["course_id"] = cid
        if sid:
            merged_meta["session_id"] = sid

        return self._course_service.register_material({
            "material_id": merged_meta.pop("material_id", None) or f"mat-{cid}-{fname}",
            "filename": fname,
            "path": path,
            "material_type": material_type,
            "language": language,
            "created_at": merged_meta.pop("created_at", None),
            "metadata": merged_meta,
        })

    def get_material(self, material_id: str) -> dict[str, Any]:
        return self._course_service.get_material(material_id)

    def list_materials(
        self,
        course_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[dict[str, Any]]:
        return self._course_service.list_materials(
            course_id=course_id,
            session_id=session_id,
        )

    # ------------------------------------------------------------------
    # 摄取
    # ------------------------------------------------------------------

    def ingest_material(
        self,
        material_id: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """摄取单个材料 -> IngestionReport DTO (幂等: 重复摄取不产生新证据)。"""
        mid = _require_nonempty_str(material_id, "material_id")
        material = self._get_material(mid)
        report: IngestionReport = self._ingestion_service.ingest(
            material, dry_run=dry_run
        )
        return report.to_dict()

    def ingest_material_batch(
        self,
        material_ids: Sequence[str],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """批量摄取材料 -> BatchIngestionReport DTO (失败隔离: 单个失败不影响整体)。"""
        if not isinstance(material_ids, Sequence) or isinstance(material_ids, (str, bytes)):
            raise InvalidInputError("material_ids must be a sequence of strings")
        materials: List[Material] = [self._get_material(mid) for mid in material_ids]
        report: BatchIngestionReport = self._ingestion_service.ingest_many(materials)
        return report.to_dict()

    def get_ingestion_status(self, material_id: str) -> Optional[dict[str, Any]]:
        """查询指定材料的最新摄取状态; 无记录则 None。"""
        mid = _require_nonempty_str(material_id, "material_id")
        status = self._ingestion_service.status_for(mid)
        if status is None:
            return None
        return {
            "material_id": mid,
            "status": status.value,
        }

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _get_material(self, material_id: str) -> Material:
        mid = _require_nonempty_str(material_id, "material_id")
        existing = self._course_service.get_material(mid)
        # get_material 返回 DTO dict; 还原为 domain Material 供 ingestion 使用
        return Material(
            material_id=existing["material_id"],
            filename=existing["filename"],
            path=existing["path"],
            material_type=existing["material_type"],
            language=existing["language"],
            created_at=existing.get("created_at"),
            metadata=existing.get("metadata") or {},
        )
