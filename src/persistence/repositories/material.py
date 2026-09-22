# -*- coding: utf-8 -*-
"""MaterialRepository + MaterialProcessingRepository (Task 42)。

存的是什么
--------------------------------------------------------------------

本项目的材料**权威表示**是材料工作流的"注册表记录"
(``MaterialWorkflowService`` 里那个 dict): 它比 ``Material`` 领域对象
多出 ``size`` / ``content_hash`` / ``source_type`` / ``processing_status`` /
``evidence_ids`` / ``attempts`` 等字段, 而且工作流的 ``_load_registry()``
本来就能直接消费它。

因此 ``materials.payload`` 存**注册表记录**, 从而零丢失。``Material``
领域对象通过 :func:`record_to_material` 派生 —— 它与
``MaterialWorkflowService._to_material`` 是同一套映射, 并由
``tests/test_persistence_material.py::test_domain_mapping_matches_workflow``
盯着两者不许漂移。

规范点名的 atomic 对
--------------------------------------------------------------------

"Material registration + processing status 不能写一半"。
:meth:`MaterialRepository.register_with_status` 把两次写入放进**同一个
事务**: 材料行 + 处理状态行。任一步失败, 两者都不落库 ——
``tests/test_persistence_transaction.py`` 会注入一个失败来证明这一点。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from src.models import Language, Material, MaterialType
from src.persistence.repositories.base import DocumentRepository, LinkRepository

__all__ = ["MaterialRepository", "MaterialProcessingRepository", "record_to_material"]


def record_to_material(record: Mapping[str, Any]) -> Material:
    """注册表记录 -> ``Material`` 领域对象。

    与 ``MaterialWorkflowService._to_material`` 保持**同一套映射**:
    ``path`` 取 ``stored_path`` (受管副本路径), ``language`` 空串视为
    ``Unknown``, 其余溯源字段进 ``metadata``。
    """
    raw_language = record.get("language")
    language = Language.from_string(str(raw_language)) if raw_language else Language.UNKNOWN
    return Material(
        material_id=str(record["material_id"]),
        filename=str(record.get("filename") or ""),
        path=str(record.get("stored_path") or ""),
        material_type=MaterialType.from_string(
            str(record.get("material_type") or "text")
        ),
        language=language,
        created_at=record.get("created_at") or None,
        metadata={
            "course_id": record.get("course_id"),
            "session_id": record.get("session_id"),
            "source_type": record.get("source_type"),
            "content_hash": record.get("content_hash"),
        },
    )


def _material_columns(record: Mapping[str, Any]) -> dict[str, Any]:
    """从注册表记录派生规范列 (绝不手写, 一律从记录读)。"""
    material_id = record.get("material_id")
    if not material_id:
        raise ValueError("material record must have a non-empty material_id")
    return {
        "material_id": str(material_id),
        "course_id": record.get("course_id"),
        "session_id": record.get("session_id"),
        "filename": str(record.get("filename") or ""),
        "extension": str(record.get("extension") or ""),
        "size": int(record.get("size") or 0),
        "content_hash": record.get("content_hash"),
        "material_type": str(record.get("material_type") or "text"),
        "language": str(record.get("language") or ""),
        "source_type": str(record.get("source_type") or ""),
        "created_at": record.get("created_at"),
        "stored_path": record.get("stored_path"),
        "relative_path": record.get("relative_path"),
    }


class MaterialRepository(DocumentRepository):
    """材料注册表仓储 (+ 材料 -> 证据 的溯源链)。"""

    table = "materials"

    def __init__(self, database) -> None:
        super().__init__(database)
        self.evidence_links = LinkRepository(database, "material_evidence")
        self.processing = MaterialProcessingRepository(database)

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def save_record(self, record: Mapping[str, Any]) -> None:
        """保存一条注册表记录 (幂等)。"""
        self.put(_material_columns(record), record)

    def save_records(self, records: Iterable[Mapping[str, Any]]) -> int:
        count = 0
        for record in records:
            self.save_record(record)
            count += 1
        return count

    def replace_evidence(self, material_id: str, evidence_ids: Iterable[str]) -> None:
        """按顺序替换某材料的证据链 (顺序 = 摄取顺序, 有意义)。"""
        self.evidence_links.replace(material_id, list(evidence_ids))

    def register_with_status(
        self,
        record: Mapping[str, Any],
        processing: Mapping[str, Any],
    ) -> None:
        """**原子**写入"材料注册 + 处理状态"。

        两次写入在同一事务内: 处理状态非法 (例如缺 ``processing_status``)
        时, 材料行也不会留下 —— 不会出现"注册了但没有状态"或反之。
        """
        with self.database.transaction():
            self.save_record(record)
            self.processing.save(processing)

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def load_record(self, material_id: str) -> Optional[dict[str, Any]]:
        return self.get(material_id)

    def load_records(self, *, course_id: Optional[str] = None) -> list[dict[str, Any]]:
        if course_id is None:
            return self.all()
        return self.all(where="course_id = ?", params=(course_id,))

    def load_material(self, material_id: str) -> Optional[Material]:
        """注册表记录 -> ``Material`` 领域对象。"""
        record = self.get(material_id)
        if record is None:
            return None
        return record_to_material(record)

    def load_materials(self, *, course_id: Optional[str] = None) -> list[Material]:
        return [record_to_material(r) for r in self.load_records(course_id=course_id)]

    def evidence_ids_for(self, material_id: str) -> list[str]:
        return self.evidence_links.rights_for(material_id)

    def material_ids_for_evidence(self, evidence_id: str) -> list[str]:
        return self.evidence_links.lefts_for(evidence_id)

    def snapshot(self, course_id: Optional[str] = None) -> dict[str, Any]:
        """``MaterialWorkflowService.registry_snapshot()`` 兼容的快照。

        可以直接喂给 ``_load_registry()`` 的等价逻辑, 从而支持重启恢复。
        """
        records = self.load_records(course_id=course_id)
        return {
            "schema_version": 1,
            "course_id": course_id,
            "materials": records,
        }


class MaterialProcessingRepository(DocumentRepository):
    """材料处理状态仓储 (与 materials 分表, 以便演示跨表原子性)。"""

    table = "material_processing"

    def save(
        self,
        record: Mapping[str, Any],
        *,
        last_attempt_at: Optional[str] = None,
    ) -> None:
        material_id = record.get("material_id")
        if not material_id:
            raise ValueError("processing record must have a non-empty material_id")
        status = record.get("processing_status")
        if status is None:
            # 故意让非法输入在这里炸掉: 用于验证跨表事务的原子性。
            raise ValueError("processing record must have a processing_status")
        self.put(
            {
                "material_id": str(material_id),
                "processing_status": str(status),
                "attempts": int(record.get("attempts") or 0),
                "error": record.get("error"),
                "error_detail": record.get("error_detail"),
                "warning": record.get("warning"),
                "retryable": 1 if record.get("retryable") else 0,
                "last_attempt_at": last_attempt_at,
            },
            record,
        )

    def load(self, material_id: str) -> Optional[dict[str, Any]]:  # type: ignore[override]
        return self.get(material_id)

    def load_all(self) -> list[dict[str, Any]]:
        return self.all()

    def status_of(self, material_id: str) -> Optional[str]:
        row = self.get_row(material_id)
        if row is None:
            return None
        return str(row["processing_status"])

    def ids_with_status(self, status: str) -> list[str]:
        return list(
            self.keys(where="processing_status = ?", params=(str(status),))
        )
