# -*- coding: utf-8 -*-
"""Real Classroom Material Workflow (Task 35).

统一流水线::

    file -> Material Registration -> Validation -> Ingestion -> Evidence -> Knowledge

安全模型 (Task 35 spec):
- 用户原始文件**永不**被就地读取 / 复制 / 处理。任何被接受的文件都先被
  复制到应用管理的 ``data_dir`` 内, 之后才做任何提取。
- 路径穿越 (``../..``, 绝对路径逃逸) 在触碰文件之前就被拒绝。
- 不支持的扩展名、0 字节、超大文件在 validation 阶段被拒绝。
- 文件 identity = 内容 hash + course + source metadata, 因此同一门课内
  重复上传的同一文件会被识别为 duplicate, 不会重复占用存储。
- 复制一律原子完成 (temp -> fsync -> os.replace): 失败时目标位置绝不留下
  半成品, 临时文件一定被清理。

状态机 (spec)::

    REGISTERED -> VALIDATING -> PROCESSING -> COMPLETED
                                     \\-----> FAILED  (可 retry)

公开入口 (MaterialWorkflowService):
- register_material(path, session_id=None, language=None) -> dict
- register_material_batch(paths, session_id=None) -> dict
- validate_material(material_id) -> dict
- process_material(material_id) -> dict
- retry_material(material_id) -> dict
- get_material(material_id) / list_materials() / list_rejected()
- get_processing_status() -> dict
- evidence_for_material(material_id) -> list[dict]
- register_knowledge_point(payload) -> dict
- cleanup()
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable, Mapping, Optional, Sequence

from src.models import Course, Language, Material, MaterialType
from src.evidence_store import EvidenceStore
from src.evidence_ingestion import EvidenceIngestionService
from src.application.data_dirs import (
    DataLayout,
    atomic_copy,
    atomic_write_text,
    contains_traversal,
    ensure_data_layout,
    iter_files,
    remove_quietly,
    safe_join,
)
from src.application.errors import (
    InvalidInputError,
    NotFoundError,
)
from src.application.runtime import Clock, utc_now_iso
from src.application.dto import evidence_to_dict, knowledge_point_to_dict

__all__ = [
    "MaterialWorkflowService",
    "MATERIAL_STATUSES",
    "RETRYABLE_ERROR_CODES",
    "NON_RETRYABLE_ERROR_CODES",
    "DEFAULT_MAX_ATTEMPTS",
    "REGISTRY_SCHEMA_VERSION",
    "resolve_material_path",
]


def resolve_material_path(
    record: Mapping[str, Any], layout: DataLayout
) -> Optional[str]:
    """材料记录 -> 当前 data_dir 下**实际**的存储路径 (Task 49)。

    记录里同时存了 ``relative_path`` (相对 data_dir, 可移植) 与
    ``stored_path`` (写它那一刻的绝对路径)。用户把 data_dir 整体搬走、
    或者从备份恢复到另一个目录之后, 绝对路径就指向了一个不存在的地方
    —— 而 ``relative_path`` 依然成立。所以先信 ``relative_path``。

    文件确实不在时**仍然返回"应该在哪"**, 而不是 ``None``: 调用方需要
    一个可操作的路径才能报出有用的 ``MATERIAL_FILE_MISSING``。

    放在模块级 (而不是只做方法) 是因为诊断路径要能在**不建立课程上下文**
    的前提下工作 —— 健康检查不该为了数一数缺了几个文件就把每门课的服务
    图都建起来。
    """
    relative = record.get("relative_path")
    if relative:
        candidate = safe_join(layout.root, str(relative))
        if os.path.isfile(candidate):
            return candidate
    stored = record.get("stored_path")
    if stored and os.path.isfile(str(stored)):
        return str(stored)
    if relative:
        return safe_join(layout.root, str(relative))
    return str(stored) if stored else None

# ---------------------------------------------------------------------------
# 扩展名 / 分类表
# ---------------------------------------------------------------------------

_NOTE_EXTS = {".txt", ".md", ".markdown"}
_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".wma", ".aac", ".opus"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}
_PDF_EXTS = {".pdf"}
_DOCX_EXTS = {".docx"}
_SUPPORTED = _NOTE_EXTS | _AUDIO_EXTS | _IMAGE_EXTS | _PDF_EXTS | _DOCX_EXTS

_EXTENSION_CATEGORIES: dict[str, str] = {}
for _e in _NOTE_EXTS:
    _EXTENSION_CATEGORIES[_e] = "note"
for _e in _AUDIO_EXTS:
    _EXTENSION_CATEGORIES[_e] = "audio"
for _e in _IMAGE_EXTS:
    _EXTENSION_CATEGORIES[_e] = "image"
for _e in _PDF_EXTS:
    _EXTENSION_CATEGORIES[_e] = "document"
for _e in _DOCX_EXTS:
    _EXTENSION_CATEGORIES[_e] = "document"

_MATERIAL_TYPE_BY_CATEGORY = {
    "note": MaterialType.NOTE,
    "audio": MaterialType.AUDIO,
    "image": MaterialType.IMAGE,
    "document": MaterialType.TEXT,
}

# ---------------------------------------------------------------------------
# 状态机 / 错误码
# ---------------------------------------------------------------------------

REGISTERED = "REGISTERED"
VALIDATING = "VALIDATING"
PROCESSING = "PROCESSING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"

#: spec 要求的状态集合 (顺序即状态机的推进顺序)。
MATERIAL_STATUSES: tuple[str, ...] = (REGISTERED, VALIDATING, PROCESSING, COMPLETED, FAILED)

#: 注册 / 校验阶段的结构性拒绝 —— 重试没有意义。
NON_RETRYABLE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "PATH_TRAVERSAL",
        "UNSUPPORTED_EXTENSION",
        "FILE_NOT_FOUND",
        "ZERO_BYTE_FILE",
        "OVERSIZED_FILE",
        "EMPTY_PATH",
        "UNSUPPORTED_SOURCE",
        "MALFORMED_EVIDENCE",
        # 文档本身损坏是文件的固有属性, 重试不会改变结果。
        "DOCUMENT_PARSE_FAILED",
    }
)

#: 处理阶段的失败 —— 可能是环境/瞬时问题, 允许有限重试。
RETRYABLE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "COPY_FAILED",
        "INGESTION_FAILED",
        "ASR_ERROR",
        "EXTRACTOR_ERROR",
        "STORE_REJECTION",
        "INTERNAL_ERROR",
        "STORAGE_ERROR",
    }
)

#: 集中配置的最大尝试次数 (spec: max_attempts = 3, 不得无限重试)。
DEFAULT_MAX_ATTEMPTS = 3

REGISTRY_SCHEMA_VERSION = 1


def _extension(path: str) -> str:
    return os.path.splitext(path)[1].lower()


class MaterialWorkflowService:
    """真实文件 -> 证据 -> 知识 的工作流编排。

    构造时注入共享的应用状态:
    - course: 本工作流服务的课程。
    - source_root: 用户可见的上传目录 (我们只读)。
    - data_dir: 应用管理的目录; 文件会被**复制**进来。
    - store / ingestion_service / org_service: 共享的领域状态。
    - clock: 可注入时钟 (测试用固定时钟即可获得确定性输出)。

    注册表 (material registry) 以 ``data/materials/<course_id>.json`` 形式
    原子持久化, 因此进程重启后仍然认识已注册的材料。
    """

    def __init__(
        self,
        course: Course,
        source_root: str,
        data_dir: str,
        *,
        store: Optional[EvidenceStore] = None,
        ingestion_service: Optional[EvidenceIngestionService] = None,
        org_service: Optional[Any] = None,
        max_file_size: int = 200 * 1024 * 1024,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        clock: Optional[Clock] = None,
        layout: Optional[DataLayout] = None,
    ) -> None:
        if course is None or not course.course_id:
            raise InvalidInputError("course must be a Course with a non-empty course_id")
        if not isinstance(source_root, str) or not source_root.strip():
            raise InvalidInputError("source_root must be a non-empty string")
        if max_file_size <= 0:
            raise InvalidInputError("max_file_size must be positive")
        if max_attempts <= 0:
            raise InvalidInputError("max_attempts must be positive")

        self._course = course
        self._course_id = course.course_id
        self._source_root = os.path.abspath(source_root)
        self._layout = layout or ensure_data_layout(data_dir)
        self._data_dir = self._layout.root
        self._max_file_size = max_file_size
        self._max_attempts = max_attempts
        self._clock: Clock = clock or utc_now_iso
        self._store = store or EvidenceStore()
        self._ingestion = ingestion_service or EvidenceIngestionService(self._store)
        self._org = org_service

        # material_id -> record dict
        self._materials: dict[str, dict[str, Any]] = {}
        # content_hash -> material_id (课程内内容去重)
        self._hash_index: dict[str, str] = {}
        self._registry_path = safe_join(self._layout.materials, f"{self._course_id}.json")
        self._load_registry()

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def course_id(self) -> str:
        return self._course_id

    @property
    def layout(self) -> DataLayout:
        return self._layout

    @property
    def registry_path(self) -> str:
        return self._registry_path

    @property
    def store(self) -> EvidenceStore:
        return self._store

    @property
    def org_service(self) -> Any:
        """知识组织服务 (惰性创建并缓存, 保证全流程共享同一实例)。"""
        if self._org is None:
            from src.knowledge_organization import KnowledgeOrganizationService

            self._org = KnowledgeOrganizationService(self._course)
        return self._org

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_material(
        self,
        path: str,
        session_id: Optional[str] = None,
        language: Optional[str] = None,
        *,
        filename: Optional[str] = None,
    ) -> dict[str, Any]:
        """注册 + 校验 + 复制一个文件。

        返回携带 spec 统一 metadata 字段的 record dict。校验失败时
        record 带 ``processing_status=FAILED`` 与结构化 ``error`` 码;
        调用方决定后续动作 (retry / cleanup)。被拒绝的文件**不抛异常**
        —— 拒绝是一种数据, 不是崩溃。

        ``filename`` 用于覆盖"从磁盘路径推导文件名"的默认行为。HTTP 上传
        必须先落盘再登记, 而落盘名必须规避 Windows 非法字符; 此时真实
        文件名由调用方显式传入, 保证溯源字段逐字保留 (见
        :func:`src.application.data_dirs.sanitize_filename`)。
        """
        if not isinstance(path, str) or not path.strip():
            raise InvalidInputError("path must be a non-empty string")
        record = self._validate_and_register(path, session_id, language, filename)
        self._persist_registry()
        return record

    def register_material_batch(
        self,
        paths: Iterable[str],
        session_id: Optional[str] = None,
        language: Optional[str] = None,
    ) -> dict[str, Any]:
        """批量注册。单个文件被拒绝不影响其他文件 (失败隔离)。"""
        if isinstance(paths, (str, bytes)) or paths is None:
            raise InvalidInputError("paths must be an iterable of file paths")
        results: list[dict[str, Any]] = []
        for item in paths:
            if not isinstance(item, str) or not item.strip():
                results.append(
                    self._failed_record("", "", "EMPTY_PATH", session_id, language)
                )
                continue
            results.append(self._validate_and_register(item, session_id, language))
        self._persist_registry()
        accepted = [r for r in results if r["processing_status"] != FAILED]
        return {
            "course_id": self._course_id,
            "total": len(results),
            "accepted": len(accepted),
            "rejected": len(results) - len(accepted),
            "duplicates": sum(1 for r in results if r.get("duplicate")),
            "materials": results,
        }

    def _validate_and_register(
        self,
        path: str,
        session_id: Optional[str],
        language: Optional[str],
        filename: Optional[str] = None,
    ) -> dict[str, Any]:
        if filename is not None and not isinstance(filename, str):
            raise InvalidInputError("filename must be a string or None")
        if filename is not None:
            # 显式文件名同样是用户输入: 只取基名, 拒绝路径成分。
            claimed = os.path.basename(str(filename).replace("\\", "/")).strip()
            if not claimed or contains_traversal(claimed) or claimed in (".", ".."):
                raise InvalidInputError(f"invalid filename: {filename!r}")
            base_name = claimed
        else:
            base_name = os.path.basename(os.path.normpath(path))
        ext = _extension(base_name)

        if contains_traversal(path):
            return self._failed_record(path, base_name, "PATH_TRAVERSAL", session_id, language)
        if ext not in _SUPPORTED:
            return self._failed_record(path, base_name, "UNSUPPORTED_EXTENSION", session_id, language)
        if not os.path.isfile(path):
            return self._failed_record(path, base_name, "FILE_NOT_FOUND", session_id, language)

        size = os.path.getsize(path)
        if size == 0:
            return self._failed_record(path, base_name, "ZERO_BYTE_FILE", session_id, language)
        if size > self._max_file_size:
            return self._failed_record(path, base_name, "OVERSIZED_FILE", session_id, language)

        content_hash = self._sha256(path)
        material_id = self._deterministic_material_id(content_hash, base_name)

        existing = self._materials.get(material_id)
        if existing is not None:
            # 完全相同的 (课程, 文件名, 内容) 重复上传。
            #
            # Task 62 修复的真实缺陷
            # --------------------
            # 旧实现无条件"幂等返回原记录"。当重复上传发生在**另一节课**
            # 时, 返回的 record 仍然带着**原来那节课**的 session_id —— 于是:
            #   1) 新课堂永远拿不到这份材料 (list_session_materials 按
            #      session_id 过滤, 直接漏掉),
            #   2) ``process_session(新课堂)`` 报
            #      "no materials registered for session" —— 用户明明上传了;
            #   3) 而且这个错误**没有留下任何痕迹**: 上传接口照样返回 200。
            #
            # 既有测试只覆盖"同一节课内重传", 所以这条路径一直没被测到。
            #
            # 语义: material_id 由 (内容, 文件名) 决定, 一个课程里同内容同名
            # 只存一份副本 —— 这没错。但 session_id 是**归属**, 不是身份。
            # 重新登记到另一节课时, 应该把这份副本**改派**过去 (并重置处理
            # 状态, 因为它对新课堂来说尚未处理), 而不是假装什么都没发生。
            if session_id != existing.get("session_id"):
                record = dict(existing)
                record.update(
                    {
                        "session_id": session_id,
                        "language": (language or "").strip() if language else "",
                        "created_at": self._clock(),
                        "processing_status": REGISTERED,
                        "error": None,
                        "error_detail": None,
                        "warning": None,
                        "retryable": False,
                        "evidence_ids": [],
                        "attempts": 0,
                        "duplicate": True,
                        "duplicate_of": material_id,
                        "reassigned_from_session_id": existing.get("session_id"),
                    }
                )
                self._materials[material_id] = record
                # 处理作业**不在这里**改: 作业状态归 ClassroomProcessingService
                # 所有, 而本服务看不到它 (方向是 processing -> workflow)。
                # 作业侧的同步在 ``_ensure_job`` 里做 —— 它每次都会用记录里的
                # 当前 session_id 校正作业, 于是改派自动生效。
                return dict(record)
            record = dict(existing)
            record["duplicate"] = True
            return record

        category = _EXTENSION_CATEGORIES[ext]
        duplicate_of = self._hash_index.get(content_hash)
        if duplicate_of is not None:
            # 同一内容以不同文件名再次上传: 复用已有副本, 不重复占用存储。
            source_record = self._materials[duplicate_of]
            record = dict(source_record)
            record.update(
                {
                    "material_id": material_id,
                    "filename": base_name,
                    "extension": ext,
                    "session_id": session_id,
                    "language": (language or "").strip() if language else "",
                    "created_at": self._clock(),
                    "processing_status": REGISTERED,
                    "error": None,
                    "error_detail": None,
                    "warning": None,
                    "retryable": False,
                    "evidence_ids": [],
                    "attempts": 0,
                    "duplicate": True,
                    "duplicate_of": duplicate_of,
                }
            )
            self._materials[material_id] = record
            return dict(record)

        bucket = self._layout.bucket_for(category)
        dest_path = safe_join(bucket, self._course_id, material_id + ext)
        try:
            atomic_copy(path, dest_path)
        except OSError as exc:
            remove_quietly(dest_path)
            return self._failed_record(
                path, base_name, "COPY_FAILED", session_id, language, detail=str(exc)
            )

        record = {
            "material_id": material_id,
            "course_id": self._course_id,
            "session_id": session_id,
            "filename": base_name,
            "extension": ext,
            "size": size,
            "content_hash": content_hash,
            "source_type": category,
            "created_at": self._clock(),
            "processing_status": REGISTERED,
            "error": None,
            "error_detail": None,
            "warning": None,
            "retryable": False,
            "material_type": _MATERIAL_TYPE_BY_CATEGORY[category].value,
            "language": (language or "").strip() if language else "",
            "stored_path": dest_path,
            "relative_path": self._layout.relative(dest_path),
            "evidence_ids": [],
            "attempts": 0,
            "duplicate": False,
            "duplicate_of": None,
        }
        self._materials[material_id] = record
        self._hash_index[content_hash] = material_id
        return dict(record)

    def _failed_record(
        self,
        path: str,
        base_name: str,
        code: str,
        session_id: Optional[str],
        language: Optional[str],
        detail: str = "",
    ) -> dict[str, Any]:
        ext = _extension(base_name)
        category = _EXTENSION_CATEGORIES.get(ext, "unknown")
        return {
            "material_id": None,
            "course_id": self._course_id,
            "session_id": session_id,
            "filename": base_name,
            "extension": ext,
            "size": None,
            "content_hash": None,
            "source_type": category,
            "created_at": self._clock(),
            "processing_status": FAILED,
            "error": code,
            "error_detail": detail or None,
            "warning": None,
            "material_type": _MATERIAL_TYPE_BY_CATEGORY.get(category, MaterialType.TEXT).value,
            "language": (language or "").strip() if language else "",
            "stored_path": None,
            "relative_path": None,
            "evidence_ids": [],
            "attempts": 0,
            "retryable": False,
            "duplicate": False,
            "duplicate_of": None,
        }

    # ------------------------------------------------------------------
    # Validation (spec 状态机)
    # ------------------------------------------------------------------

    def validate_material(self, material_id: str) -> dict[str, Any]:
        """重新校验已注册材料的不变量 (不重复复制文件)。"""
        record = self._get_record(material_id)
        if record["processing_status"] == REGISTERED:
            record["processing_status"] = VALIDATING

        ext = record.get("extension") or ""
        size = record.get("size")
        stored_path = record.get("stored_path")

        if ext not in _SUPPORTED:
            return self._fail(record, "UNSUPPORTED_EXTENSION")
        if size == 0:
            return self._fail(record, "ZERO_BYTE_FILE")
        if size is not None and size > self._max_file_size:
            return self._fail(record, "OVERSIZED_FILE")
        if not stored_path or not os.path.isfile(stored_path):
            return self._fail(record, "STORAGE_ERROR")
        if os.path.getsize(stored_path) != size:
            return self._fail(record, "STORAGE_ERROR")
        self._persist_registry()
        return dict(record)

    # ------------------------------------------------------------------
    # Processing (Ingestion -> Evidence)
    # ------------------------------------------------------------------

    def process_material(self, material_id: str) -> dict[str, Any]:
        """运行摄取流水线。

        幂等: 已经 COMPLETED 的材料直接返回原记录, 不会重新复制或重复
        摄取 (EvidenceStore 内容寻址, 二次摄取只会命中 duplicate)。
        摄取失败标记 FAILED + 结构化 error, 调用方可 retry。
        """
        record = self._get_record(material_id)
        if record["processing_status"] == COMPLETED and self._evidence_present(record):
            return dict(record)

        if record["processing_status"] == FAILED:
            code = record.get("error") or "PREV_VALIDATION_FAILED"
            if code not in RETRYABLE_ERROR_CODES:
                return dict(record)
            if record.get("attempts", 0) >= self._max_attempts:
                record["retryable"] = False
                return dict(record)
        else:
            if record.get("processing_status") == REGISTERED:
                record["processing_status"] = VALIDATING

        record["attempts"] = int(record.get("attempts", 0)) + 1
        material = self._to_material(record)
        record["processing_status"] = PROCESSING
        record["error"] = None
        try:
            report = self._ingestion.ingest(material)
        except Exception as exc:  # noqa: BLE001 - adapter 边界, 绝不外泄 traceback
            report = None
            record["error_detail"] = type(exc).__name__

        if report is None or not report.succeeded:
            code = "INGESTION_FAILED"
            if report is not None:
                errors = list(report.errors)
                if errors:
                    code = errors[0].code or "INGESTION_FAILED"
            record["error"] = code
            record["retryable"] = code in RETRYABLE_ERROR_CODES
            record["evidence_ids"] = []
            record["processing_status"] = FAILED
            self._persist_registry()
            return dict(record)

        if report.total_extracted == 0:
            # 区分 "文档解析失败" 与 "文档本来就没有可提取文本"。
            outcome, warning = self._document_outcome(record)
            if outcome is not None:
                record["error"] = outcome
                record["retryable"] = outcome in RETRYABLE_ERROR_CODES
                record["evidence_ids"] = []
                record["processing_status"] = FAILED
                self._persist_registry()
                return dict(record)
            record["warning"] = warning

        record["processing_status"] = COMPLETED
        record["error"] = None
        record["retryable"] = False
        record["evidence_ids"] = list(report.evidence_ids)
        record["evidence_count"] = len(record["evidence_ids"])
        record["duplicate_evidence_count"] = int(getattr(report, "duplicate_count", 0) or 0)
        self._persist_registry()
        return dict(record)

    def _evidence_present(self, record: Mapping[str, Any]) -> bool:
        """注册表说 COMPLETED, 但证据真的还在本进程的证据库里吗?

        Task 45 修复的真实缺陷
        ----------------------
        材料注册表是**持久化**的 (``data/materials/<course_id>.json``), 而
        证据库目前是内存中的 (Task 44 的 ``bootstrap`` docstring 已明确声明
        这个边界)。进程重启后注册表仍然写着 ``COMPLETED`` + 一串
        ``evidence_ids``, 而证据库里一条都没有 —— 此时"幂等早退"会让整节课
        **静默地变成零证据、零知识**: 材料显示"已处理", 知识库却是空的。

        用真实数据跑验收 (Task 45 的 restart 场景) 第一次就把这条抓了出来。

        所以幂等的判据是"证据真的在场", 而不是注册表的一句承诺。重新摄取是
        安全的: 证据是内容寻址的, 同一份材料重新摄取得到**同一组
        evidence_id**, 不会产生第二份证据。
        """
        ids = [str(eid) for eid in (record.get("evidence_ids") or []) if eid]
        if not ids:
            # 合法情况: 空文档 / 无可提取文本, 证据本来就是 0 条。
            return True
        return all(
            self._store.get(eid, include_retired=True) is not None for eid in ids
        )

    def retry_material(self, material_id: str) -> dict[str, Any]:
        """显式重试一个 FAILED 且 retryable 的材料。

        - 结构性拒绝 (PATH_TRAVERSAL / UNSUPPORTED_EXTENSION / ...) 永不重试。
        - 达到 max_attempts 后拒绝重试 (不无限重试)。
        """
        record = self._get_record(material_id)
        if record["processing_status"] == COMPLETED:
            return dict(record)
        code = record.get("error")
        if code not in RETRYABLE_ERROR_CODES:
            record["retryable"] = False
            return dict(record)
        if int(record.get("attempts", 0)) >= self._max_attempts:
            record["retryable"] = False
            self._persist_registry()
            return dict(record)
        record["processing_status"] = REGISTERED if record.get("stored_path") else FAILED
        record["error"] = None
        return self.process_material(material_id)

    def _document_outcome(
        self, record: Mapping[str, Any]
    ) -> tuple[Optional[str], Optional[str]]:
        """空提取结果时判断文档的真实结局。

        返回 ``(error_code, warning)``:

        - ``(None, None)`` —— 非文档材料, 空提取是正常结果;
        - ``("DOCUMENT_PARSE_FAILED", None)`` —— 文档解析失败;
        - ``(None, "NO_TEXT_EXTRACTED")`` —— 文档可解析但没有可提取文本
          (例如纯扫描件), 属于**合法成功**, 但必须让用户看见。
        """
        source_type = record.get("source_type")
        if source_type != "document":
            return None, None
        stored = record.get("stored_path")
        if not stored:
            return "STORAGE_ERROR", None
        try:
            from src.document_input import parse_document, DocumentStatus
        except ImportError:  # pragma: no cover - 依赖缺失时保守处理
            return "DOCUMENT_PARSE_FAILED", None
        try:
            parsed = parse_document(stored, material_id=str(record.get("material_id") or ""))
        except ValueError:
            return "DOCUMENT_PARSE_FAILED", None
        if parsed.status == DocumentStatus.FAILED:
            return "DOCUMENT_PARSE_FAILED", None
        if parsed.status == DocumentStatus.PARSED_EMPTY:
            return None, "NO_TEXT_EXTRACTED"
        return None, None

    def _to_material(self, record: Mapping[str, Any]) -> Material:
        lang_raw = record.get("language")
        lang = Language.from_string(str(lang_raw)) if lang_raw else Language.UNKNOWN
        return Material(
            material_id=str(record["material_id"]),
            filename=str(record.get("filename") or ""),
            path=str(record.get("stored_path") or ""),
            material_type=MaterialType.from_string(str(record.get("material_type") or "text")),
            language=lang,
            created_at=record.get("created_at") or None,
            metadata={
                "course_id": self._course_id,
                "session_id": record.get("session_id"),
                "source_type": record.get("source_type"),
                "content_hash": record.get("content_hash"),
            },
        )

    # ------------------------------------------------------------------
    # Knowledge
    # ------------------------------------------------------------------

    def register_knowledge_point(self, knowledge_point: Mapping[str, Any]) -> dict[str, Any]:
        """把 KnowledgePoint 登记到共享的组织服务 (按 knowledge_id 幂等)。

        ``evidence_refs`` 必须指向 EvidenceStore 中**真实存在**的证据 ——
        不允许凭空制造"看起来像事实"的知识点。
        """
        if not isinstance(knowledge_point, Mapping):
            raise InvalidInputError(
                f"knowledge_point must be a mapping, got {type(knowledge_point).__name__}"
            )
        org = self.org_service
        kp_id = knowledge_point.get("knowledge_id")
        if not kp_id or not str(kp_id).strip():
            raise InvalidInputError("knowledge_id is required")
        for ref in knowledge_point.get("evidence_refs") or []:
            if not self._store.contains(ref):
                raise NotFoundError(f"evidence {ref!r} not found in store")
        from src.models import KnowledgePoint

        kp = KnowledgePoint.from_dict(dict(knowledge_point))
        org.register_knowledge_point(kp)
        return knowledge_point_to_dict(kp)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_material(self, material_id: str) -> dict[str, Any]:
        return dict(self._get_record(material_id))

    def list_materials(self) -> list[dict[str, Any]]:
        return [
            dict(record)
            for record in sorted(self._materials.values(), key=lambda r: str(r["material_id"]))
        ]

    def list_rejected(self) -> list[dict[str, Any]]:
        return [r for r in self.list_materials() if r["processing_status"] == FAILED]

    def list_session_materials(self, session_id: str) -> list[dict[str, Any]]:
        if not session_id or not str(session_id).strip():
            raise InvalidInputError("session_id is required")
        return [r for r in self.list_materials() if r.get("session_id") == session_id]

    def get_processing_status(self) -> dict[str, Any]:
        """材料处理状态汇总 (供 UI / API 的 dashboard 使用)。"""
        counts = {status: 0 for status in MATERIAL_STATUSES}
        for record in self._materials.values():
            status = record.get("processing_status")
            if status in counts:
                counts[status] += 1
        return {
            "course_id": self._course_id,
            "total": len(self._materials),
            "by_status": counts,
            "failed": counts[FAILED],
            "completed": counts[COMPLETED],
            "pending": counts[REGISTERED] + counts[VALIDATING] + counts[PROCESSING],
            "evidence_total": sum(
                len(r.get("evidence_ids") or []) for r in self._materials.values()
            ),
        }

    def evidence_for_material(self, material_id: str) -> list[dict[str, Any]]:
        record = self._get_record(material_id)
        evidences = self._store.get_by_source(str(record["material_id"]))
        return [evidence_to_dict(e) for e in evidences]

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self) -> dict[str, Any]:
        """删除本课程在 data_dir 内的所有副本与注册表。

        用户原始文件 (source_root 下) **永不**被触碰。临时目录也会被清空。
        """
        removed: list[str] = []
        for name in ("documents", "audio", "images"):
            course_dir = os.path.join(getattr(self._layout, name), self._course_id)
            if os.path.isdir(course_dir):
                remove_quietly(course_dir)
                removed.append(self._layout.relative(course_dir))
        if os.path.exists(self._registry_path):
            remove_quietly(self._registry_path)
            removed.append(self._layout.relative(self._registry_path))
        self._materials.clear()
        self._hash_index.clear()
        return {"course_id": self._course_id, "removed": removed}

    def cleanup_temp(self) -> int:
        """清理 ``data/temp`` 下的残留临时文件, 返回删除数量。"""
        removed = 0
        for path in list(iter_files(self._layout.temp)):
            if remove_quietly(path):
                removed += 1
        return removed

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_record(self, material_id: str) -> dict[str, Any]:
        if not material_id or not str(material_id).strip():
            raise InvalidInputError("material_id is required")
        record = self._materials.get(str(material_id))
        if record is None:
            raise NotFoundError(f"material {material_id!r} not registered")
        return record

    def _fail(self, record: dict[str, Any], code: str) -> dict[str, Any]:
        record["processing_status"] = FAILED
        record["error"] = code
        record["retryable"] = code in RETRYABLE_ERROR_CODES
        self._persist_registry()
        return dict(record)

    def _sha256(self, path: str) -> str:
        from src.application.data_dirs import file_sha256

        return file_sha256(path)

    def _deterministic_material_id(self, content_hash: str, filename: str) -> str:
        import hashlib

        raw = (self._course_id + "|" + filename + "|" + content_hash).encode("utf-8")
        return "mat-" + hashlib.sha256(raw).hexdigest()[:24]

    # -- registry ------------------------------------------------------

    def _load_registry(self) -> None:
        path = self._registry_path
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        if payload.get("schema_version") != REGISTRY_SCHEMA_VERSION:
            return
        if payload.get("course_id") != self._course_id:
            return
        for record in payload.get("materials") or []:
            if not isinstance(record, dict):
                continue
            material_id = record.get("material_id")
            if not material_id:
                continue
            adopted = self._adopt_record(record)
            self._materials[str(material_id)] = adopted
            content_hash = adopted.get("content_hash")
            if content_hash:
                self._hash_index.setdefault(str(content_hash), str(material_id))

    def _adopt_record(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """把一条外部记录收编成内存记录, **重新解析**它的存储位置。

        记录可能来自 JSON 注册表、SQLite, 或者更早的机器/目录。它里面的
        ``stored_path`` 是"写它那一刻"的绝对路径, 而 ``relative_path``
        相对 data_dir, 一直成立。用户在另一台机器上恢复备份、或者把
        data_dir 整体搬走之后, 只信 ``stored_path`` 会让校验与摄取在
        "文件明明在"的情况下报 STORAGE_ERROR。

        注意**不重算** ``content_hash`` / ``material_id``: 那是身份, 身份
        绝不在加载时被改写 (否则历史证据的溯源链会指向另一个材料)。
        """
        adopted = dict(record)
        resolved = resolve_material_path(adopted, self._layout)
        if resolved is not None:
            adopted["stored_path"] = resolved
        return adopted

    def _persist_registry(self) -> None:
        """原子写入注册表 (确定性排序, 便于 diff 与审计)。"""
        payload = {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "course_id": self._course_id,
            "materials": sorted(
                (dict(r) for r in self._materials.values()),
                key=lambda r: str(r.get("material_id")),
            ),
        }
        atomic_write_text(
            self._registry_path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        )

    def registry_snapshot(self) -> dict[str, Any]:
        """只读的注册表快照 (测试 / API 用)。"""
        return {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "course_id": self._course_id,
            "materials": self.list_materials(),
        }

    # ------------------------------------------------------------------
    # 持久化恢复 (Task 49)
    # ------------------------------------------------------------------

    def restore_records(self, records: Iterable[Mapping[str, Any]]) -> int:
        """从 SQLite 恢复材料注册表 (**幂等**, 返回新增条数)。

        为什么需要它: 注册表同时以 JSON (``data/materials/<course>.json``)
        和 SQLite 保存。两者由同一段代码写出, 但**只有 SQLite 是 source of
        truth** —— JSON 可能因为手工删除、部分恢复而落后。因此启动时以
        数据库记录为准做一次并集: 数据库里有的、JSON 里没有的, 补回来。

        只接受 ``course_id`` 匹配本服务的记录: 材料注册表是课程隔离的,
        把别的课程的材料塞进来会直接破坏"不混合不同课程"的铁律。

        恢复时会**重新解析存储位置** (见 :meth:`resolve_stored_path`):
        从备份恢复到另一个目录之后, 记录里的绝对路径必然失效, 但
        ``relative_path`` 仍然成立。文件确实找不到时**保留记录**并让
        完整性诊断报 ``MATERIAL_FILE_MISSING`` —— 静默丢一条材料记录
        等于静默丢一段溯源链。
        """
        added = 0
        for record in records or ():
            if not isinstance(record, Mapping):
                continue
            material_id = record.get("material_id")
            if not material_id:
                continue
            if str(record.get("course_id") or "") != self._course_id:
                continue
            key = str(material_id)
            if key in self._materials:
                # 记录已经在内存里 (通常来自 JSON 注册表)。仍然要**刷新
                # 存储位置**: JSON 可能是搬动 data_dir 之前写的, 而数据库
                # 记录里的 relative_path 才是在当前目录下成立的答案。
                self._materials[key]["stored_path"] = self.resolve_stored_path(
                    self._materials[key]
                )
                continue
            self._materials[key] = self._adopt_record(record)
            content_hash = record.get("content_hash")
            if content_hash:
                self._hash_index.setdefault(str(content_hash), key)
            added += 1
        return added

    def resolve_stored_path(self, record: Mapping[str, Any]) -> Optional[str]:
        """把记录里的存储位置解析成**当前** data_dir 下的实际路径。

        记录里同时存了 ``relative_path`` (相对 data_dir, 可移植) 与
        ``stored_path`` (写它那一刻的绝对路径)。用户把 data_dir 整体搬走、
        或者从备份恢复到另一个目录之后, 绝对路径就指向了一个不存在的地方
        —— 而 ``relative_path`` 依然成立。所以先信 ``relative_path``。

        三者都不存在时返回原来的 ``stored_path`` (可能是 ``None``):
        调用方据此报 ``MATERIAL_FILE_MISSING``, 而不是把记录删掉。
        """
        return resolve_material_path(record, self._layout)

    def storage_diagnostics(self) -> list[dict[str, Any]]:
        """逐条检查材料的存储位置是否成立 (Task 49 的文件一致性)。

        诊断码:

        - ``MATERIAL_FILE_MISSING`` —— 记录在库里, 但文件不在。这是
          "数据库说有、磁盘说没有"的情况, 必须显式报出来: 静默略过会让
          用户在真正要用那份材料时才发现问题 (而且那时已经无从追查)。
        - ``MATERIAL_PATH_UNKNOWN`` —— 记录里连存储位置都没有 (不该发生,
          但历史库/手工改过的库可能出现)。

        不做内容哈希校验 —— 那是 ``Workspace.material_integrity()`` 的
        可选深度检查, 这里只回答"文件在不在", 因此可以放心在健康检查里
        每条材料都调。
        """
        diagnostics: list[dict[str, Any]] = []
        for record in self.list_materials():
            material_id = str(record.get("material_id") or "")
            resolved = self.resolve_stored_path(record)
            if resolved is None:
                diagnostics.append(
                    {
                        "diagnostic": "MATERIAL_PATH_UNKNOWN",
                        "material_id": material_id,
                        "course_id": self._course_id,
                        "filename": record.get("filename"),
                        "stored_path": record.get("stored_path"),
                        "relative_path": record.get("relative_path"),
                    }
                )
                continue
            if not os.path.isfile(resolved):
                diagnostics.append(
                    {
                        "diagnostic": "MATERIAL_FILE_MISSING",
                        "material_id": material_id,
                        "course_id": self._course_id,
                        "filename": record.get("filename"),
                        "stored_path": record.get("stored_path"),
                        "relative_path": record.get("relative_path"),
                        "expected_path": resolved,
                    }
                )
        return diagnostics
