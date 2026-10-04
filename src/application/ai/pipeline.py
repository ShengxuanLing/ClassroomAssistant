# -*- coding: utf-8 -*-
"""AI 理解管线 (TASK-76 §7/§11/§24/§25/§28)。

统一链路::

    Material -> detect_type -> extract (既有确定性 ingestion, 不碰)
        -> create_evidence (既有, 不碰) -> segment/chunk (本包)
        -> AI analysis -> structured candidates -> candidate validation
        -> candidate deduplication -> evidence grounding
        -> knowledgepoint creation/update -> review classification -> persist

管线保证:

- 可重复运行、幂等、可恢复、可 retry; 中途失败不破坏旧数据。
- AI 请求失败不会删除已有 Evidence, 也不伪造 KnowledgePoint。
- 同一 ``(material_hash, chunk_hash, model, prompt_version)`` 不重复调用
  (chunk caching, 成本控制); retry 只重跑失败 chunk。
- 每次运行记录 ``(provider, model, prompt_version, pipeline_version,
  created_at)``; 历史结果不静默覆盖 (调用方保留最近一次 + 计数)。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from src.application.ai import PIPELINE_VERSION
from src.application.ai.chunking import (
    TextChunk,
    chunks_for_evidence,
)
from src.application.ai.merge import (
    MergeProposal,
    deduplicate_candidates,
    propose_merge_with_existing,
)
from src.application.ai.prompts import (
    CHUNK_EXTRACTION_PROMPT_VERSION,
    IMAGE_PROMPT_VERSION,
    AUDIO_PROMPT_VERSION,
    MERGE_PROMPT_VERSION,
    SUMMARY_PROMPT_VERSION,
    SUMMARY_ZH_PROMPT_VERSION,
    GLOSSARY_PROMPT_VERSION,
    KP_ZH_PROMPT_VERSION,
    COURSE_OVERVIEW_PROMPT_VERSION,
    build_chunk_extraction_prompt,
    build_merge_prompt,
    build_summary_prompt,
    build_summary_zh_prompt,
    build_kp_zh_prompt,
    build_glossary_prompt,
    build_course_overview_prompt,
)
from src.application.ai.provider import (
    AIProvider,
    AIRequestError,
    FakeAIProvider,
)
from src.application.ai.schemas import (
    MALFORMED_ERROR,
    ChunkAIResult,
    KnowledgeCandidate,
    MaterialAIResult,
    SummaryZhResult,
    KpZhResult,
    GlossaryEntry,
    CourseOverviewResult,
    parse_material_response,
    parse_structured_response,
    parse_summary_zh_response,
    parse_kp_zh_response,
    parse_glossary_response,
    parse_course_overview_response,
)
from src.application.ai.validators import (
    GroundedCandidate,
    ground_candidates,
    ground_glossary,
    ground_kp_zh,
    map_candidate_to_kp_payload,
)
from src.application.errors import ProcessingError
from src.application.runtime import Clock, utc_now_iso

__all__ = [
    "AI_ANALYSIS_FAILED",
    "AIAnalysisFailure",
    "AIAnalysisReport",
    "AIUnderstandingPipeline",
    "processing_identity",
    "detect_material_kind",
    "KP_TRANSLATION_FAILED",
]

#: TASK-79: 知识点级按需翻译的失败码。与材料级分析一样走 422 (证据安全、
#: 可 retry), 且**从不**触发任何 KnowledgePoint 写入。
KP_TRANSLATION_FAILED = "KP_TRANSLATION_FAILED"

#: AI 分析失败的统一错误码 (processing failure, Evidence 安全)。
AI_ANALYSIS_FAILED = "AI_ANALYSIS_FAILED"


class AIAnalysisFailure(ProcessingError):
    """AI 分析失败 (Evidence 已保留, 用户可 retry)。

    code 沿用 ``PROCESSING_ERROR`` (422); 模块级 ``AI_ANALYSIS_FAILED``
    字符串作为 detail 里的 ``failure`` 标记, 供日志/排查区分"AI 失败"与
    其它处理失败。``detail`` 只记形状, 绝不含 key、正文与原始异常文本。
    """

    def __init__(self, message: str, detail: Optional[Mapping[str, Any]] = None) -> None:
        merged = {"failure": AI_ANALYSIS_FAILED}
        if detail:
            merged.update(dict(detail))
        super().__init__(message, detail=merged)


@dataclass
class AIAnalysisReport:
    """一次材料 AI 分析的完整报告 (可直接返回给 UI/API)。"""

    material_id: str
    course_id: str
    kind: str
    status: str  # "completed" | "failed" | "disabled"
    stages: list[dict[str, Any]] = field(default_factory=list)
    report_version: str = "ai-report-v2"
    summary: str = ""
    topics: list[str] = field(default_factory=list)
    summary_zh: str = ""
    topics_zh: list[str] = field(default_factory=list)
    summary_zh_status: str = "skipped"
    summary_zh_evidence_ids: list[str] = field(default_factory=list)
    summary_zh_grounded: bool = False
    glossary: list[dict[str, Any]] = field(default_factory=list)
    glossary_rejected: list[dict[str, Any]] = field(default_factory=list)
    glossary_total: int = 0
    glossary_status: str = "skipped"
    derived_prompt_versions: dict[str, str] = field(default_factory=dict)
    summary_zh_prompt_version: str = SUMMARY_ZH_PROMPT_VERSION
    glossary_prompt_version: str = GLOSSARY_PROMPT_VERSION
    definitions: list[str] = field(default_factory=list)
    formulas: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    difficulties: list[str] = field(default_factory=list)
    auto_accepted: list[dict[str, Any]] = field(default_factory=list)
    needs_review: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    chunk_total: int = 0
    chunk_succeeded: int = 0
    chunk_failed: int = 0
    chunk_ids: list[str] = field(default_factory=list)
    truncated: bool = False
    provider: str = ""
    model: str = ""
    prompt_version: str = ""
    pipeline_version: str = PIPELINE_VERSION
    processing_identity: str = ""
    created_at: Optional[str] = None
    error: Optional[str] = None
    error_detail: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "material_id": self.material_id,
            "course_id": self.course_id,
            "kind": self.kind,
            "status": self.status,
            "report_version": self.report_version,
            "stages": list(self.stages),
            "summary": self.summary,
            "topics": list(self.topics),
            "summary_zh": self.summary_zh,
            "topics_zh": list(self.topics_zh),
            "summary_zh_status": self.summary_zh_status,
            "summary_zh_evidence_ids": list(self.summary_zh_evidence_ids),
            "summary_zh_grounded": self.summary_zh_grounded,
            "glossary": list(self.glossary),
            "glossary_rejected": list(self.glossary_rejected),
            "glossary_total": len(self.glossary),
            "glossary_status": self.glossary_status,
            "derived_prompt_versions": dict(self.derived_prompt_versions),
            "summary_zh_prompt_version": self.summary_zh_prompt_version,
            "glossary_prompt_version": self.glossary_prompt_version,
            "definitions": list(self.definitions),
            "formulas": list(self.formulas),
            "examples": list(self.examples),
            "prerequisites": list(self.prerequisites),
            "difficulties": list(self.difficulties),
            "knowledge_points_total": (
                len(self.auto_accepted) + len(self.needs_review) + len(self.conflicts)
            ),
            "auto_accepted": list(self.auto_accepted),
            "needs_review": list(self.needs_review),
            "conflicts": list(self.conflicts),
            "rejected": list(self.rejected),
            "chunk_total": self.chunk_total,
            "chunk_succeeded": self.chunk_succeeded,
            "chunk_failed": self.chunk_failed,
            "chunk_ids": list(self.chunk_ids),
            "truncated": self.truncated,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "pipeline_version": self.pipeline_version,
            "processing_identity": self.processing_identity,
            "created_at": self.created_at,
            "error": self.error,
            "error_detail": self.error_detail,
        }


def processing_identity(
    *,
    material_hash: str,
    model: str,
    prompt_version: str,
    pipeline_version: str = PIPELINE_VERSION,
) -> str:
    """AI 处理幂等键: ``ai-<sha16(material|pipeline|prompt|model)>``。

    同一材料重复点击"分析" -> 同一 identity -> 已有 KP 按 ID 幂等复用,
    不产生 ``KnowledgePoint x 3``。
    """
    raw = "|".join(
        [str(material_hash), str(pipeline_version), str(prompt_version), str(model)]
    ).encode("utf-8")
    return "ai-" + hashlib.sha256(raw).hexdigest()[:16]


def detect_material_kind(
    *,
    filename: str = "",
    source_type: str = "",
    material_type: str = "",
) -> str:
    """材料类型识别 (确定性, 扩展名优先, 与 ingestion 适配器同源划分)。

    返回 ``text`` | ``image`` | ``audio`` | ``unknown``。
    """
    name = (filename or "").lower()
    source = (source_type or "").lower()
    mtype = (material_type or "").lower()
    audio_exts = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".wma", ".aac", ".opus")
    image_exts = (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp")
    if source == "audio" or mtype == "audio" or name.endswith(audio_exts):
        return "audio"
    if source in ("ocr", "image") or mtype == "image" or name.endswith(image_exts):
        return "image"
    if source in ("note", "pdf", "docx", "pptx", "document") or mtype in ("text", "note", "syllabus"):
        return "text"
    if name.endswith((".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown")):
        return "text"
    return "unknown"


def _stage(name: str, state: str, detail: str = "") -> dict[str, Any]:
    return {"stage": name, "state": state, "detail": detail}


class AIUnderstandingPipeline:
    """AI 理解管线 (确定性 ingestion 之后的部分)。

    本对象**不**做文件读取 / ingestion / 数据库写入: 调用方
    (``Workspace.analyze_material_with_ai``) 负责提供 Evidence 列表与
    ``register`` 回调, 这里只做 chunk -> AI -> 校验 -> grounding ->
    去重 -> 分类。student learning state 绝不经过这里。
    """

    def __init__(
        self,
        provider: Optional[AIProvider] = None,
        *,
        clock: Optional[Clock] = None,
        prompt_version: str = CHUNK_EXTRACTION_PROMPT_VERSION,
        policy: Optional[Mapping[str, float]] = None,
    ) -> None:
        self._provider = provider or FakeAIProvider()
        self._clock: Clock = clock or utc_now_iso
        self._prompt_version = prompt_version
        self._policy = dict(policy) if policy else {}
        # chunk 缓存: (chunk_hash, model, prompt_version) -> ChunkAIResult。
        self._chunk_cache: dict[tuple[str, str, str], ChunkAIResult] = {}
        # 失败 chunk 记录 (retry 只重跑这些)。
        self._failed_chunks: dict[str, str] = {}

    @property
    def provider(self) -> AIProvider:
        return self._provider

    @property
    def provider_name(self) -> str:
        return str(getattr(self._provider, "name", "?"))

    @property
    def model_id(self) -> str:
        return str(getattr(self._provider, "model", self.provider_name))

    def cache_size(self) -> int:
        return len(self._chunk_cache)

    def clear_cache(self) -> None:
        self._chunk_cache.clear()
        self._failed_chunks.clear()

    # ------------------------------------------------------------------
    # Level 1: chunk understanding
    # ------------------------------------------------------------------

    def analyze_chunk(
        self,
        chunk: TextChunk,
        *,
        material_label: str = "",
        kind: str = "text",
        content_language: Optional[str] = None,
        timeout_seconds: int = 60,
        image_bytes: Optional[bytes] = None,
    ) -> ChunkAIResult:
        """分析单个 chunk (带缓存; 失败抛 ``AIAnalysisFailure``)。

        ``image_bytes`` 只在 ``kind == "image"`` 且 provider 声明
        ``supports_image_bytes`` 时使用 (vision 可选, OCR 回落); 否则
        忽略，多余字节绝不进请求。
        """
        cache_key = (chunk.content_hash, self.model_id, self._prompt_version)
        cached = self._chunk_cache.get(cache_key)
        if cached is not None:
            return cached
        use_vision = (
            kind == "image"
            and bool(image_bytes)
            and bool(self._provider.capabilities.supports_image_bytes)
        )
        if kind == "image":
            from src.application.ai.prompts import build_image_analysis_prompt

            prompt = build_image_analysis_prompt(
                ocr_text=chunk.text,
                chunk_id=chunk.chunk_id,
                material_label=material_label,
                content_language=content_language,
            )
        elif kind == "audio":
            from src.application.ai.prompts import build_audio_analysis_prompt

            prompt = build_audio_analysis_prompt(
                transcript_text=chunk.text,
                chunk_id=chunk.chunk_id,
                material_label=material_label,
                content_language=content_language,
            )
        else:
            prompt = build_chunk_extraction_prompt(
                chunk_id=chunk.chunk_id,
                chunk_text=chunk.text,
                material_label=material_label,
                content_language=content_language,
            )
        try:
            if use_vision:
                # vision 失败直接记 chunk 失败 (可重试; 关掉开关即回 OCR)——
                # 绝不静默降级 (与 asr real/mock 的诚实边界同口径)。
                raw_text = self._provider.analyze_image_bytes(
                    bytes(image_bytes or b""),
                    ocr_text=chunk.text,
                    chunk_id=chunk.chunk_id,
                    material_label=material_label,
                    content_language=content_language,
                    timeout_seconds=timeout_seconds,
                )
            else:
                raw_text = self._provider.generate_structured(
                    prompt, timeout_seconds=timeout_seconds
                )
        except AIRequestError as exc:
            raise AIAnalysisFailure(
                "AI analysis failed; evidence is safe and the material can be retried",
                detail={
                    "provider": self.provider_name,
                    "failed_chunk": chunk.chunk_id,
                },
            ) from exc
        result, error = parse_structured_response(raw_text, chunk.chunk_id)
        if error is not None or result is None:
            raise AIAnalysisFailure(
                "AI returned a malformed response; evidence is safe",
                detail={
                    "provider": self.provider_name,
                    "error_code": MALFORMED_ERROR,
                    "failed_chunk": chunk.chunk_id,
                },
            )
        # 模型返回的 evidence_refs 一律重写为本 chunk (模型不许自创引用;
        # 跨 chunk 引用在 Level 2 合并时重建)。
        for candidate in result.candidates:
            candidate.chunk_id = chunk.chunk_id
            if not candidate.evidence_refs:
                candidate.evidence_refs = [chunk.chunk_id]
        self._chunk_cache[cache_key] = result
        return result

    # ------------------------------------------------------------------
    # Level 2: material understanding
    # ------------------------------------------------------------------

    def analyze_material(
        self,
        evidences: Sequence[Any],
        *,
        material_id: str,
        material_hash: str = "",
        material_label: str = "",
        kind: str = "text",
        content_language: Optional[str] = None,
        timeout_seconds: int = 60,
        policy: Optional[Mapping[str, float]] = None,
        image_bytes: Optional[bytes] = None,
    ) -> tuple[MaterialAIResult, dict[str, str], list[TextChunk], dict[str, Any]]:
        """两级理解: chunk (Level 1) -> merge (Level 2)。

        返回 ``(merged_result, chunk_to_evidence, chunks, stats)``。
        任一 chunk 失败 -> 抛 ``AIAnalysisFailure`` (stats 含成功/失败计数,
        已成功 chunk 留在缓存, retry 只重跑失败的)。

        ``image_bytes``: 单份图片材料的原始字节 (仅 ``kind == "image"`` 且
        provider 声明 ``supports_image_bytes`` 时发 vision, 否则忽略)。
        """
        chunks, chunk_to_evidence, truncated = chunks_for_evidence(
            evidences, material_id=material_id
        )
        if not chunks:
            raise AIAnalysisFailure(
                "no analysable text in material evidence; nothing was sent to AI",
                detail={"material_id": material_id, "kind": kind},
            )
        policy = dict(policy) if policy else dict(self._policy)
        chunk_results: list[ChunkAIResult] = []
        failed: dict[str, str] = {}
        for chunk in chunks:
            try:
                chunk_results.append(
                    self.analyze_chunk(
                        chunk,
                        material_label=material_label,
                        kind=kind,
                        content_language=content_language,
                        timeout_seconds=timeout_seconds,
                        image_bytes=image_bytes,
                    )
                )
            except AIAnalysisFailure as exc:
                failed[chunk.chunk_id] = str(exc)
        stats = {
            "chunk_total": len(chunks),
            "chunk_succeeded": len(chunk_results),
            "chunk_failed": len(failed),
            "truncated": truncated,
        }
        if failed:
            self._failed_chunks.update(failed)
            raise AIAnalysisFailure(
                "AI analysis failed for %d of %d chunks; evidence is safe" % (
                    len(failed), len(chunks)),
                detail={
                    "provider": self.provider_name,
                    "chunk_total": len(chunks),
                    "chunk_succeeded": len(chunk_results),
                    "chunk_failed": len(failed),
                },
            )
        merged = self._merge_chunk_results(
            chunk_results,
            chunk_ids=[c.chunk_id for c in chunks],
            material_label=material_label,
            content_language=content_language,
            timeout_seconds=timeout_seconds,
        )
        return merged, chunk_to_evidence, chunks, stats

    def _merge_chunk_results(
        self,
        chunk_results: Sequence[ChunkAIResult],
        *,
        chunk_ids: Sequence[str],
        material_label: str = "",
        content_language: Optional[str] = None,
        timeout_seconds: int = 60,
    ) -> MaterialAIResult:
        """Level 2 合并: 先确定性合并, 再用模型做全局概念整理 (失败则回落)。"""
        all_candidates: list[KnowledgeCandidate] = []
        topics: list[str] = []
        summaries: list[str] = []
        for result in chunk_results or ():
            summaries.append(result.summary)
            for topic in result.topics or ():
                if topic and topic not in topics:
                    topics.append(topic)
            all_candidates.extend(result.candidates)
        deduped, _ = deduplicate_candidates(all_candidates)
        # 模型做全局整理 (失败/畸形 -> 回落确定性合并, 不算整体失败)。
        merged: Optional[MaterialAIResult] = None
        try:
            digest = json.dumps(
                [r.to_dict() for r in chunk_results], ensure_ascii=False
            )[:12000]
            prompt = build_merge_prompt(
                chunk_summaries=digest,
                material_label=material_label,
                content_language=content_language,
            )
            raw_text = self._provider.generate_structured(
                prompt, timeout_seconds=timeout_seconds
            )
            parsed, error = parse_material_response(raw_text, list(chunk_ids))
            if error is None and parsed is not None:
                merged = parsed
        except (AIRequestError, ValueError, TypeError):
            merged = None
        # 总结 (失败/畸形 -> 回落抽取版, 不算整体失败)。
        summary_text = " ".join(s for s in summaries if s)[:2000]
        definitions: list[str] = []
        formulas: list[str] = []
        examples: list[str] = []
        for candidate in deduped:
            if candidate.kp_type == "definition" and candidate.description:
                definitions.append(candidate.description[:300])
            elif candidate.kp_type == "formula" and candidate.description:
                formulas.append(candidate.description[:300])
            elif candidate.kp_type == "example" and candidate.description:
                examples.append(candidate.description[:300])
        if merged is not None:
            merged.candidates = deduped or merged.candidates
            merged.chunk_ids = list(chunk_ids)
            if not merged.summary:
                merged.summary = summary_text
            if not merged.topics:
                merged.topics = topics
            merged.definitions = merged.definitions or definitions[:10]
            merged.formulas = merged.formulas or formulas[:10]
            merged.examples = merged.examples or examples[:10]
            merged.provider = self.provider_name
            merged.model = self.model_id
            merged.prompt_version = MERGE_PROMPT_VERSION
            merged.pipeline_version = PIPELINE_VERSION
            merged.content_language = content_language
            return merged
        return MaterialAIResult(
            summary=summary_text,
            topics=topics,
            definitions=definitions[:10],
            formulas=formulas[:10],
            examples=examples[:10],
            prerequisites=[],
            difficulties=[],
            candidates=deduped,
            chunk_ids=list(chunk_ids),
            provider=self.provider_name,
            model=self.model_id,
            prompt_version=self._prompt_version,
            pipeline_version=PIPELINE_VERSION,
            content_language=content_language,
        )

    def build_summary(
        self,
        evidences: Sequence[Any],
        *,
        material_label: str = "",
        content_language: Optional[str] = None,
        timeout_seconds: int = 60,
    ) -> MaterialAIResult:
        """材料级总结 (summary/topics/definitions/formulas/...)。

        总结字段映射到**返回报告**, 不塞进任何数据库 TEXT 字段
        (§13: 禁止把整个 AI response JSON 塞进 summary 字段)。
        """
        texts: list[str] = []
        for ev in evidences or ():
            content = getattr(ev, "content", "") or ""
            if content.strip():
                texts.append(content.strip())
        if not texts:
            raise AIAnalysisFailure(
                "no analysable text in material evidence",
                detail={"kind": "summary"},
            )
        prompt = build_summary_prompt(
            material_text="\n\n".join(texts)[:12000],
            material_label=material_label,
            content_language=content_language,
        )
        try:
            raw_text = self._provider.generate_structured(
                prompt, timeout_seconds=timeout_seconds
            )
        except AIRequestError as exc:
            raise AIAnalysisFailure(
                "AI summary failed; evidence is safe",
                detail={"provider": self.provider_name},
            ) from exc
        parsed, error = parse_material_response(raw_text)
        if error is not None or parsed is None:
            raise AIAnalysisFailure(
                "AI returned a malformed summary; evidence is safe",
                detail={"provider": self.provider_name, "error_code": MALFORMED_ERROR},
            )
        parsed.provider = self.provider_name
        parsed.model = self.model_id
        parsed.prompt_version = SUMMARY_PROMPT_VERSION
        parsed.pipeline_version = PIPELINE_VERSION
        parsed.content_language = content_language
        return parsed

    # ------------------------------------------------------------------
    # Derived report stages (best effort; never block KP persistence)
    # ------------------------------------------------------------------

    @staticmethod
    def _summary_digest_fields(merged: Any) -> tuple[str, list[str], list[str]]:
        if isinstance(merged, Mapping):
            summary = str(merged.get("summary") or "")
            topics = [str(value) for value in (merged.get("topics") or []) if str(value).strip()]
            definitions = [
                str(value)
                for value in (merged.get("definitions") or [])
                if str(value).strip()
            ]
            candidates = list(merged.get("candidates") or merged.get("knowledge_points") or [])
            for candidate in candidates:
                if isinstance(candidate, Mapping):
                    definitions.extend(
                        str(value)
                        for value in (
                            list(candidate.get("original_terms") or [])
                            + [candidate.get("title") or ""]
                        )
                        if str(value).strip()
                    )
            return summary, topics, list(dict.fromkeys(definitions))
        candidates = list(getattr(merged, "candidates", []) or [])
        definitions = [
            str(value)
            for value in (getattr(merged, "definitions", []) or [])
            if str(value).strip()
        ]
        for candidate in candidates:
            definitions.extend(
                str(value)
                for value in (
                    list(getattr(candidate, "original_terms", []) or [])
                    + [getattr(candidate, "title", "") or ""]
                )
                if str(value).strip()
            )
        return (
            str(getattr(merged, "summary", "") or ""),
            [str(value) for value in (getattr(merged, "topics", []) or []) if str(value).strip()],
            list(dict.fromkeys(definitions)),
        )

    def build_summary_zh(
        self,
        merged: Any,
        *,
        material_label: str = "",
        content_language: Optional[str] = None,
        timeout_seconds: int = 60,
    ) -> dict[str, Any]:
        """Translate the merged report into a grounded Chinese learning layer.

        The method intentionally returns a skipped result instead of raising:
        a translation outage must never prevent grounded KnowledgePoints from
        being registered.  The raw summary remains the source-language report.
        """
        summary, topics, definitions = self._summary_digest_fields(merged)
        if not summary and not topics:
            return {
                "summary_zh": "",
                "topics_zh": [],
                "status": "skipped",
                "prompt_version": SUMMARY_ZH_PROMPT_VERSION,
            }
        prompt = build_summary_zh_prompt(
            summary,
            topics,
            definitions,
            material_label,
            content_language=content_language,
        )
        try:
            raw_text = self._provider.generate_structured(
                prompt, timeout_seconds=timeout_seconds
            )
            parsed, error = parse_summary_zh_response(raw_text)
            if error is not None or parsed is None:
                return {
                    "summary_zh": "",
                    "topics_zh": [],
                    "status": "skipped",
                    "error_code": MALFORMED_ERROR,
                    "prompt_version": SUMMARY_ZH_PROMPT_VERSION,
                }
            return {
                "summary_zh": parsed.summary_zh,
                "topics_zh": list(parsed.topics_zh),
                "status": "completed",
                "prompt_version": SUMMARY_ZH_PROMPT_VERSION,
                "provider": self.provider_name,
                "model": self.model_id,
            }
        except Exception:  # noqa: BLE001 - derived report stage is best effort
            return {
                "summary_zh": "",
                "topics_zh": [],
                "status": "skipped",
                "prompt_version": SUMMARY_ZH_PROMPT_VERSION,
            }

    # ------------------------------------------------------------------
    # TASK-79: knowledge-point Chinese explanation (on demand, one LLM call)
    # ------------------------------------------------------------------
    #
    # Deliberately **not** a derived-report stage of ``analyze_material``:
    # a semester holds hundreds of knowledge points and the user asked for a
    # specific one.  Spending the budget up front would be both expensive and
    # unread; the call happens when a student opens a knowledge point.

    def build_kp_zh(
        self,
        *,
        knowledge_id: str,
        title: str = "",
        content: str = "",
        terms: Optional[Sequence[Any]] = None,
        evidence_ids: Optional[Sequence[str]] = None,
        content_language: Optional[str] = None,
        timeout_seconds: int = 60,
    ) -> dict[str, Any]:
        """One grounded Chinese explanation for a single knowledge point.

        Returns a ``status`` dict instead of raising for the two "there is
        nothing to explain" cases (no evidence / empty statement), so the
        caller can show an honest empty state.  Provider and shape failures
        raise ``AIAnalysisFailure``: the caller may retry, and nothing has
        been written either way.
        """
        refs = [str(value) for value in (evidence_ids or []) if str(value)]
        if not refs:
            return {
                "knowledge_id": str(knowledge_id or ""),
                "translation_zh": "",
                "terms_zh": [],
                "status": "skipped",
                "reason": "no_evidence",
                "prompt_version": KP_ZH_PROMPT_VERSION,
            }
        if not (str(title or "").strip() or str(content or "").strip()):
            return {
                "knowledge_id": str(knowledge_id or ""),
                "translation_zh": "",
                "terms_zh": [],
                "status": "skipped",
                "reason": "empty_knowledge_point",
                "prompt_version": KP_ZH_PROMPT_VERSION,
            }
        prompt = build_kp_zh_prompt(
            knowledge_id=str(knowledge_id or ""),
            title=str(title or ""),
            content=str(content or ""),
            terms=list(terms or []),
            evidence_ids=refs,
            content_language=content_language,
        )
        try:
            raw_text = self._provider.generate_structured(
                prompt, timeout_seconds=timeout_seconds
            )
        except Exception as exc:  # noqa: BLE001 - any provider fault is a 422
            # 窄化到 ``AIRequestError`` 会让一个真实 provider 的内部异常
            # (socket / ssl / 解析) 穿透成 HTTP 500 —— 而本项目的硬规则是
            # "AI 失败从不是 500, 永远是 422 且可 retry"。
            raise AIAnalysisFailure(
                "AI translation failed; the knowledge point is unchanged",
                detail={
                    "provider": self.provider_name,
                    "failure": KP_TRANSLATION_FAILED,
                    "knowledge_id": str(knowledge_id or ""),
                },
            ) from exc
        parsed, error = parse_kp_zh_response(raw_text, allowed_evidence_ids=refs)
        if error is not None or parsed is None:
            raise AIAnalysisFailure(
                "AI returned a malformed knowledge-point translation; nothing was changed",
                detail={
                    "provider": self.provider_name,
                    "error_code": MALFORMED_ERROR,
                    "failure": KP_TRANSLATION_FAILED,
                    "knowledge_id": str(knowledge_id or ""),
                },
            )
        source_texts = [str(title or ""), str(content or "")]
        source_texts.extend(str(value) for value in (terms or []) if str(value).strip())
        grounded, rejected = ground_kp_zh(
            parsed,
            knowledge_id=str(knowledge_id or ""),
            source_texts=source_texts,
            evidence_ids=refs,
        )
        if grounded is None:
            return {
                "knowledge_id": str(knowledge_id or ""),
                "translation_zh": "",
                "terms_zh": [],
                "status": "skipped",
                "reason": "not_grounded",
                "prompt_version": KP_ZH_PROMPT_VERSION,
            }
        grounded.update(
            {
                "status": "completed",
                "prompt_version": KP_ZH_PROMPT_VERSION,
                "provider": self.provider_name,
                "model": self.model_id,
            }
        )
        grounded["terms_rejected"] = rejected
        return grounded

    @staticmethod
    def _glossary_digest(
        chunks: Sequence[Any],
        evidences: Sequence[Any],
        chunk_to_evidence: Mapping[str, str],
    ) -> tuple[str, list[str], dict[str, str]]:
        lines: list[str] = []
        material_ids: list[str] = []
        for chunk in chunks or ():
            chunk_id = str(getattr(chunk, "chunk_id", "") or "")
            text = str(getattr(chunk, "text", "") or "")
            if not chunk_id or not text.strip():
                continue
            lines.append("CHUNK %s\n%s" % (chunk_id, text.strip()))
            evidence_id = str(chunk_to_evidence.get(chunk_id, "") or "")
            if evidence_id and evidence_id not in material_ids:
                material_ids.append(evidence_id)
        if not lines:
            # Direct Evidence input is useful for unit callers that do not need
            # chunking.  Treat the Evidence ID as the model-facing reference;
            # the validator still checks that it belongs to this material.
            for evidence in evidences or ():
                evidence_id = str(getattr(evidence, "evidence_id", "") or "")
                text = str(getattr(evidence, "content", "") or "")
                if evidence_id and text.strip():
                    lines.append("CHUNK %s\n%s" % (evidence_id, text.strip()))
                    if evidence_id not in material_ids:
                        material_ids.append(evidence_id)
        return "\n\n".join(lines)[:12000], material_ids, dict(chunk_to_evidence)

    def build_glossary(
        self,
        evidences: Sequence[Any] = (),
        merged: Any = None,
        *,
        chunks: Sequence[Any] = (),
        chunk_to_evidence: Optional[Mapping[str, str]] = None,
        material_evidence_ids: Optional[Sequence[str]] = None,
        evidence_texts: Optional[Mapping[str, Any]] = None,
        material_label: str = "",
        kp_titles: Sequence[Any] = (),
        course_id: str = "",
        timeout_seconds: int = 60,
    ) -> dict[str, Any]:
        """Extract and ground a bilingual glossary without touching KP state."""
        mapping = dict(chunk_to_evidence or {})
        if chunks and not mapping and evidences:
            derived_chunks, derived_mapping, _ = chunks_for_evidence(
                evidences,
                material_id=str(getattr(chunks[0], "material_id", "") or "material"),
            )
            if derived_chunks and derived_mapping:
                chunks = derived_chunks
                mapping = derived_mapping
        if chunks:
            if not mapping:
                mapping = {
                    str(getattr(chunk, "chunk_id", "")): str(
                        getattr(chunk, "evidence_id", "") or ""
                    )
                    for chunk in chunks
                }
            chunk_to_evidence = mapping
            digest, discovered_ids, mapping = self._glossary_digest(
                chunks, evidences, mapping
            )
        else:
            for evidence in evidences or ():
                evidence_id = str(getattr(evidence, "evidence_id", "") or "")
                if evidence_id:
                    mapping.setdefault(evidence_id, evidence_id)
            digest, discovered_ids, mapping = self._glossary_digest(
                (), evidences, mapping
            )
        ids = [str(value) for value in (material_evidence_ids or discovered_ids) if str(value)]
        if not digest.strip() or not ids:
            return {
                "glossary": [],
                "glossary_rejected": [],
                "status": "skipped",
                "prompt_version": GLOSSARY_PROMPT_VERSION,
            }
        if evidence_texts is None:
            evidence_texts = {
                str(getattr(evidence, "evidence_id", "")): str(
                    getattr(evidence, "content", "") or ""
                )
                for evidence in evidences or ()
                if str(getattr(evidence, "evidence_id", "") or "")
            }
        if not kp_titles and merged is not None:
            candidates = (
                merged.get("candidates")
                if isinstance(merged, Mapping)
                else getattr(merged, "candidates", [])
            )
            kp_titles = [
                candidate.get("title")
                if isinstance(candidate, Mapping)
                else getattr(candidate, "title", "")
                for candidate in (candidates or [])
            ]
        title_values = [
            str(value.get("title") or "").strip()
            if isinstance(value, Mapping)
            else str(value or "").strip()
            for value in (kp_titles or [])
        ]
        prompt = build_glossary_prompt(
            digest,
            kp_titles=[value for value in title_values if value],
            material_label=material_label,
        )
        try:
            raw_text = self._provider.generate_structured(
                prompt, timeout_seconds=timeout_seconds
            )
            entries, error = parse_glossary_response(raw_text)
            if error is not None or entries is None:
                return {
                    "glossary": [],
                    "glossary_rejected": [],
                    "status": "skipped",
                    "error_code": MALFORMED_ERROR,
                    "prompt_version": GLOSSARY_PROMPT_VERSION,
                }
            accepted, rejected = ground_glossary(
                entries,
                chunk_to_evidence=mapping,
                material_evidence_ids=ids,
                evidence_texts=evidence_texts,
                course_id=course_id,
                kp_titles=kp_titles,
            )
            return {
                "glossary": accepted,
                "glossary_rejected": rejected,
                "status": "completed",
                "prompt_version": GLOSSARY_PROMPT_VERSION,
                "provider": self.provider_name,
                "model": self.model_id,
            }
        except Exception:  # noqa: BLE001 - glossary is report-only
            return {
                "glossary": [],
                "glossary_rejected": [],
                "status": "skipped",
                "prompt_version": GLOSSARY_PROMPT_VERSION,
            }

    @staticmethod
    def aggregate_course_reports(
        reports: Sequence[Mapping[str, Any]],
        *,
        course_id: str = "",
        missing_material_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Build a deterministic, course-scoped digest for synthesis/UI."""
        rows: list[dict[str, Any]] = []
        topic_materials: dict[str, list[str]] = {}
        topic_summaries: dict[str, list[str]] = {}
        for report in reports or ():
            if not isinstance(report, Mapping):
                continue
            # Defence in depth: Workspace already selects by course, but the
            # pure aggregator must not become a cross-course leak when reused.
            report_course = str(report.get("course_id") or course_id)
            if course_id and report_course != str(course_id):
                continue
            material_id = str(report.get("material_id") or "")
            if not material_id:
                continue
            summary = str(report.get("summary") or "")
            topics = [str(value).strip() for value in (report.get("topics") or []) if str(value).strip()]
            row = {
                "material_id": material_id,
                "filename": str(report.get("filename") or material_id),
                "summary": summary[:1200],
                "topics": topics,
                "knowledge_points_total": int(report.get("knowledge_points_total") or 0),
                "auto_accepted": len(report.get("auto_accepted") or []),
                "needs_review": len(report.get("needs_review") or []),
                "conflicts": len(report.get("conflicts") or []),
            }
            rows.append(row)
            for topic in topics:
                key = " ".join(topic.casefold().split())
                if not key:
                    continue
                label = next(
                    (value for value in topic_materials if value.casefold() == key),
                    topic,
                )
                topic_materials.setdefault(label, [])
                if material_id not in topic_materials[label]:
                    topic_materials[label].append(material_id)
                if summary:
                    topic_summaries.setdefault(label, [])
                    if summary not in topic_summaries[label]:
                        topic_summaries[label].append(summary[:300])
        rows.sort(key=lambda item: item["material_id"])
        missing = [str(value) for value in (missing_material_ids or []) if str(value)]
        topic_map = [
            {
                "topic": topic,
                "material_ids": list(material_ids),
                "count": len(material_ids),
                "summary": " · ".join(topic_summaries.get(topic, []))[:800],
            }
            for topic, material_ids in sorted(
                topic_materials.items(), key=lambda pair: pair[0].casefold()
            )
        ]
        overview_parts = [
            "%s: %s" % (row["filename"], row["summary"])
            for row in rows
            if row["summary"]
        ]
        overview = "；".join(overview_parts)[:6000]
        total = len(rows) + len(missing)
        return {
            "course_id": str(course_id or ""),
            "overview": overview,
            "topic_map": topic_map,
            "gaps": list(missing),
            "materials": rows,
            "coverage": {
                "materials_total": total,
                "materials_with_reports": len(rows),
                "materials_missing_reports": len(missing),
                "gaps": list(missing),
            },
            "truncated": False,
        }

    def synthesize_course_overview(
        self,
        course_reports_digest: Any,
        *,
        course_id: str = "",
        reports: Optional[Sequence[Mapping[str, Any]]] = None,
        missing_material_ids: Sequence[str] = (),
        provider: Optional[AIProvider] = None,
        timeout_seconds: int = 60,
    ) -> dict[str, Any]:
        """Run at most one LLM call over a report-only course digest."""
        if reports is None:
            if isinstance(course_reports_digest, Mapping):
                reports = list(
                    course_reports_digest.get("reports")
                    or course_reports_digest.get("materials")
                    or []
                )
            elif isinstance(course_reports_digest, (list, tuple)):
                reports = list(course_reports_digest)
            elif isinstance(course_reports_digest, str):
                try:
                    decoded = json.loads(course_reports_digest)
                except (TypeError, ValueError):
                    decoded = None
                if isinstance(decoded, list):
                    reports = decoded
                elif isinstance(decoded, Mapping):
                    reports = list(decoded.get("reports") or decoded.get("materials") or [])
                else:
                    reports = []
            else:
                reports = []
        base = self.aggregate_course_reports(
            reports,
            course_id=course_id,
            missing_material_ids=missing_material_ids,
        )
        # Workspace passes the already-reduced report digest (summaries,
        # topics and KP titles only).  Keep that boundary instead of silently
        # re-expanding the full reports; direct callers may still supply a
        # JSON/list shape, which is reduced to a compact course-scoped payload.
        if isinstance(course_reports_digest, str) and course_reports_digest.strip():
            digest = course_reports_digest
        else:
            digest_rows = []
            for report in reports or ():
                if not isinstance(report, Mapping):
                    continue
                report_course = str(report.get("course_id") or course_id)
                if course_id and report_course != str(course_id):
                    continue
                digest_rows.append(
                    {
                        "material_id": str(report.get("material_id") or ""),
                        "filename": str(
                            report.get("filename") or report.get("material_id") or ""
                        ),
                        "summary": str(report.get("summary") or "")[:1200],
                        "topics": [
                            str(value) for value in (report.get("topics") or [])
                        ],
                    }
                )
            digest = json.dumps(
                {"course_id": course_id, "reports": digest_rows},
                ensure_ascii=False,
            )
        base["truncated"] = len(digest) > 16000
        if not reports:
            base.update({"status": "partial", "provider": "", "model": ""})
            return base
        actual_provider = provider or self._provider
        actual_pipeline = self
        if provider is not None and provider is not self._provider:
            actual_pipeline = AIUnderstandingPipeline(provider, clock=self._clock)
        try:
            prompt = build_course_overview_prompt(digest, course_id or "course")
            raw_text = actual_provider.generate_structured(
                prompt, timeout_seconds=timeout_seconds
            )
            parsed, error = parse_course_overview_response(raw_text)
            if error is not None or parsed is None:
                raise ValueError("malformed course overview")
            allowed_ids = {
                str(row.get("material_id"))
                for row in base.get("materials", [])
                if row.get("material_id")
            }
            clean_map: list[dict[str, Any]] = []
            for item in parsed.topic_map:
                ids = [
                    value
                    for value in item.get("material_ids", [])
                    if str(value) in allowed_ids
                ]
                if not ids:
                    continue
                clean_map.append(
                    {
                        "topic": str(item.get("topic") or ""),
                        "material_ids": ids,
                        "count": len(ids),
                        "summary": str(item.get("summary") or ""),
                    }
                )
            if parsed.overview.strip():
                base["overview"] = parsed.overview.strip()
            if clean_map:
                base["topic_map"] = clean_map
            # Deterministic missing-report gaps always win over model prose.
            base["llm_gaps"] = list(parsed.gaps)
            base.update(
                {
                    "status": "completed",
                    "provider": str(getattr(actual_pipeline, "provider_name", "?")),
                    "model": str(getattr(actual_pipeline, "model_id", "?")),
                    "prompt_version": COURSE_OVERVIEW_PROMPT_VERSION,
                }
            )
        except Exception:  # noqa: BLE001 - always retain deterministic fallback
            base.update(
                {
                    "status": "partial",
                    "provider": str(getattr(actual_pipeline, "provider_name", "?")),
                    "model": str(getattr(actual_pipeline, "model_id", "?")),
                    "prompt_version": COURSE_OVERVIEW_PROMPT_VERSION,
                }
            )
        return base

    # ------------------------------------------------------------------
    # Level 3: grounding + dedup + classification (落库前最后一步)
    # ------------------------------------------------------------------

    def ground_and_classify(
        self,
        merged: MaterialAIResult,
        *,
        chunk_to_evidence: Mapping[str, str],
        material_evidence_ids: Sequence[str],
        evidence_texts: Optional[Mapping[str, Any]] = None,
        existing_kps: Sequence[Mapping[str, Any]] = (),
        policy: Optional[Mapping[str, float]] = None,
    ) -> dict[str, Any]:
        """grounding -> 去重 -> 与已有 KP 比对 -> 分类。

        返回 ``{grounded, rejected, proposals, auto, review, conflict}``
        (均为可 JSON 序列化的字典, 供 Workspace 落库与报告)。

        ``evidence_texts`` 是生产路径的硬门: 有真实 Evidence 文本时必须传入，
        ``ground_candidates`` 才会执行 candidate/evidence 复制检测。保留
        ``None`` 参数只为兼容旧的纯结构单元测试；应用编排不得省略它。
        """
        policy = dict(policy) if policy else dict(self._policy)
        deduped, dropped = deduplicate_candidates(list(merged.candidates))
        grounded, rejected = ground_candidates(
            deduped,
            chunk_to_evidence=dict(chunk_to_evidence),
            material_evidence_ids=list(material_evidence_ids),
            evidence_texts=evidence_texts,
            policy=policy,
        )
        proposals = propose_merge_with_existing(
            [g.candidate for g in grounded],
            list(existing_kps),
            candidate_evidence=[list(g.evidence_ids) for g in grounded],
        )
        auto: list[GroundedCandidate] = []
        review: list[GroundedCandidate] = []
        conflict: list[dict[str, Any]] = []
        proposal_by_index = {p.candidate_index: p for p in proposals}
        for index, item in enumerate(grounded):
            proposal = proposal_by_index.get(index)
            action = proposal.action if proposal is not None else "create"
            if action == "attach":
                # 同一知识点: 挂新 Evidence —— 但仍走正常 KP 更新路径,
                # 分类为 auto (确定性命中, 非语义合并)。
                auto.append(item)
            elif action == "conflict":
                conflict.append(
                    {
                        "candidate": item.candidate.to_dict(),
                        "evidence_ids": list(item.evidence_ids),
                        "existing_id": proposal.existing_id if proposal else None,
                        "reason": proposal.reason if proposal else "",
                    }
                )
            elif item.decision == "auto" and action == "create":
                auto.append(item)
            else:
                review.append(item)
        return {
            "grounded": grounded,
            "rejected": rejected,
            "proposals": proposals,
            "auto": auto,
            "review": review,
            "conflict": conflict,
            "dedup_dropped": dropped,
        }

    # ------------------------------------------------------------------
    # 落库 (经调用方 register 回调 —— 本对象不直接碰服务)
    # ------------------------------------------------------------------

    def kp_payloads(
        self,
        items: Sequence[GroundedCandidate],
        *,
        course_id: str,
        material_id: str,
        content_language: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Grounded 候选 -> KP payload 列表 (再由调用方逐个 register)。"""
        return [
            map_candidate_to_kp_payload(
                item,
                course_id=course_id,
                material_id=material_id,
                content_language=content_language,
            )
            for item in items or ()
        ]
