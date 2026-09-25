# -*- coding: utf-8 -*-
"""AI 结构化输出 schema 与校验 (TASK-76 §8/§27)。

禁止依赖"总结一下这段内容"式的自然语言解析, 更禁止::

    json.loads(response); db.insert(response)

所有 LLM 输出必须经过::

    JSON parsing -> schema validation -> semantic validation
    -> evidence validation -> domain validation

本模块只做前两步 (后三步在 ``validators.py`` / ``pipeline.py``)。
解析失败返回 ``(None, error_code)``, 由调用方记 processing failure
并保留原始 Evidence —— 绝不伪造确定性 KnowledgePoint。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from src.application.ai.prompts import ALLOWED_KNOWLEDGE_TYPES

__all__ = [
    "SCHEMA_VERSION",
    "MALFORMED_ERROR",
    "KnowledgeCandidate",
    "ChunkAIResult",
    "MaterialAIResult",
    "SummaryZhResult",
    "GlossaryEntry",
    "CourseOverviewResult",
    "parse_structured_response",
    "parse_material_response",
    "parse_summary_zh_response",
    "parse_glossary_response",
    "parse_course_overview_response",
    "candidate_to_dict",
    "material_result_to_dict",
]

#: 结构化 schema 版本 (进入 processing identity)。
SCHEMA_VERSION = "ai-schema-v1"

#: JSON 解析/校验失败时的统一错误码 (调用方映射为 processing failure)。
MALFORMED_ERROR = "AI_MALFORMED_RESPONSE"

#: Hard output budgets for derived report stages.  They protect the report
#: contract and keep a model response from becoming an accidental data dump.
MAX_SUMMARY_ZH_CHARS = 6000
MAX_GLOSSARY_TERM_CHARS = 120
MAX_GLOSSARY_ZH_CHARS = 20
MAX_COURSE_OVERVIEW_CHARS = 6000


def _as_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return default
    return str(value).strip()


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        text = _as_str(item)
        if text and text not in out:
            out.append(text)
    return out


def _as_confidence(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return number


@dataclass
class KnowledgeCandidate:
    """单个 AI 知识候选 (尚未落库, 尚未绑定真实 Evidence)。"""

    title: str = ""
    description: str = ""
    kp_type: str = "concept"
    importance: str = "medium"
    confidence: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    original_terms: list[str] = field(default_factory=list)
    chunk_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "type": self.kp_type,
            "importance": self.importance,
            "confidence": self.confidence,
            "evidence_refs": list(self.evidence_refs),
            "relations": list(self.relations),
            "examples": list(self.examples),
            "original_terms": list(self.original_terms),
            "chunk_id": self.chunk_id,
        }


@dataclass
class ChunkAIResult:
    """单个 chunk 的 AI 理解结果 (Level 1)。"""

    chunk_id: str = ""
    summary: str = ""
    topics: list[str] = field(default_factory=list)
    candidates: list[KnowledgeCandidate] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "summary": self.summary,
            "topics": list(self.topics),
            "knowledge_points": [c.to_dict() for c in self.candidates],
        }


@dataclass
class SummaryZhResult:
    """Grounded Chinese-learning summary returned by a derived stage."""

    summary_zh: str = ""
    topics_zh: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_zh": self.summary_zh,
            "topics_zh": list(self.topics_zh),
        }


@dataclass
class GlossaryEntry:
    """One bilingual term card before/after evidence grounding.

    ``evidence_refs`` are the model-facing chunk references.  Grounding may
    also expose the resolved Evidence IDs through ``evidence_ids``; both keys
    are retained in the serialized report so older and newer readers can use
    the shape they know.
    """

    term: str = ""
    lang: str = ""
    zh: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    kp_id: Optional[str] = None
    lang_raw: str = ""

    def __post_init__(self) -> None:
        if not self.evidence_refs and self.evidence_ids:
            self.evidence_refs = list(self.evidence_ids)
        if not self.evidence_ids and self.evidence_refs:
            self.evidence_ids = list(self.evidence_refs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "lang": self.lang,
            "zh": self.zh,
            "evidence_refs": list(self.evidence_refs),
            "evidence_ids": list(self.evidence_ids),
            "kp_id": self.kp_id,
        }


@dataclass
class CourseOverviewResult:
    """Structured output of the one-shot course synthesis stage."""

    overview: str = ""
    topic_map: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overview": self.overview,
            "topic_map": [dict(item) for item in self.topic_map],
            "gaps": list(self.gaps),
        }


@dataclass
class MaterialAIResult:
    """单个材料的 AI 理解结果 (Level 2 合并后, 尚未落库)。"""

    summary: str = ""
    topics: list[str] = field(default_factory=list)
    definitions: list[str] = field(default_factory=list)
    formulas: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    difficulties: list[str] = field(default_factory=list)
    candidates: list[KnowledgeCandidate] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    prompt_version: str = ""
    pipeline_version: str = ""
    content_language: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "topics": list(self.topics),
            "definitions": list(self.definitions),
            "formulas": list(self.formulas),
            "examples": list(self.examples),
            "prerequisites": list(self.prerequisites),
            "difficulties": list(self.difficulties),
            "knowledge_points": [c.to_dict() for c in self.candidates],
            "chunk_ids": list(self.chunk_ids),
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "pipeline_version": self.pipeline_version,
            "content_language": self.content_language,
            "schema_version": SCHEMA_VERSION,
        }


def _parse_candidate(raw: Any, chunk_id: str) -> Optional[KnowledgeCandidate]:
    if not isinstance(raw, Mapping):
        return None
    title = _as_str(raw.get("title"))
    description = _as_str(raw.get("description"))
    if not title and not description:
        return None
    kp_type = _as_str(raw.get("type"), "concept").lower()
    if kp_type not in ALLOWED_KNOWLEDGE_TYPES:
        kp_type = "concept"
    importance = _as_str(raw.get("importance"), "medium").lower()
    if importance not in ("high", "medium", "low"):
        importance = "medium"
    confidence = _as_confidence(raw.get("confidence"))
    if confidence is None:
        confidence = 0.0
    refs = [_as_str(r) for r in (raw.get("evidence_refs") or []) if _as_str(r)]
    return KnowledgeCandidate(
        title=title[:200],
        description=description,
        kp_type=kp_type,
        importance=importance,
        confidence=confidence,
        evidence_refs=refs,
        relations=_as_str_list(raw.get("relations")),
        examples=_as_str_list(raw.get("examples")),
        original_terms=_as_str_list(raw.get("original_terms")),
        chunk_id=chunk_id,
    )


def _parse_chunk_result(
    raw: Any, chunk_id: str
) -> Optional[ChunkAIResult]:
    if not isinstance(raw, Mapping):
        return None
    candidates: list[KnowledgeCandidate] = []
    for item in raw.get("knowledge_points") or []:
        candidate = _parse_candidate(item, chunk_id)
        if candidate is not None:
            candidates.append(candidate)
    return ChunkAIResult(
        chunk_id=chunk_id,
        summary=_as_str(raw.get("summary")),
        topics=_as_str_list(raw.get("topics")),
        candidates=candidates,
    )


def parse_structured_response(
    text: str, chunk_id: str = ""
) -> tuple[Optional[ChunkAIResult], Optional[str]]:
    """解析单 chunk 的 LLM 结构化输出。

    返回 ``(result, error_code)``: 成功时 error 为 ``None``;
    任何形状问题 (非 JSON / 非对象 / 无可用字段) 都返回
    ``(None, MALFORMED_ERROR)`` —— 调用方不得用部分结果拼凑知识点。
    """
    if not isinstance(text, str) or not text.strip():
        return None, MALFORMED_ERROR
    cleaned = text.strip()
    # 容忍模型在 JSON 外包一层 ```json 代码围栏 (内容仍必须严格 JSON)。
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    try:
        raw = json.loads(cleaned)
    except (ValueError, TypeError):
        return None, MALFORMED_ERROR
    result = _parse_chunk_result(raw, chunk_id)
    if result is None:
        return None, MALFORMED_ERROR
    return result, None


def parse_material_response(
    text: str, chunk_ids: Sequence[str] = ()
) -> tuple[Optional[MaterialAIResult], Optional[str]]:
    """解析材料级 (Level 2 / summary) 的 LLM 结构化输出。"""
    if not isinstance(text, str) or not text.strip():
        return None, MALFORMED_ERROR
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    try:
        raw = json.loads(cleaned)
    except (ValueError, TypeError):
        return None, MALFORMED_ERROR
    if not isinstance(raw, Mapping):
        return None, MALFORMED_ERROR
    candidates: list[KnowledgeCandidate] = []
    for item in raw.get("knowledge_points") or []:
        candidate = _parse_candidate(item, "")
        if candidate is not None:
            candidates.append(candidate)
    return MaterialAIResult(
        summary=_as_str(raw.get("summary")),
        topics=_as_str_list(raw.get("topics")),
        definitions=_as_str_list(raw.get("definitions")),
        formulas=_as_str_list(raw.get("formulas")),
        examples=_as_str_list(raw.get("examples")),
        prerequisites=_as_str_list(raw.get("prerequisites")),
        difficulties=_as_str_list(raw.get("difficulties")),
        candidates=candidates,
        chunk_ids=[str(c) for c in (chunk_ids or ())],
    ), None


def _decode_json_object(text: str) -> Optional[Mapping[str, Any]]:
    """Decode one JSON object, tolerating only a markdown JSON fence."""
    if not isinstance(text, str) or not text.strip():
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    try:
        raw = json.loads(cleaned)
    except (ValueError, TypeError):
        return None
    return raw if isinstance(raw, Mapping) else None


def parse_summary_zh_response(
    text: str,
) -> tuple[Optional[SummaryZhResult], Optional[str]]:
    """Parse ``{summary_zh, topics_zh}`` without accepting partial output."""
    raw = _decode_json_object(text)
    if raw is None or "summary_zh" not in raw or "topics_zh" not in raw:
        return None, MALFORMED_ERROR
    summary = raw.get("summary_zh")
    topics = raw.get("topics_zh")
    if not isinstance(summary, str) or not isinstance(topics, (list, tuple)):
        return None, MALFORMED_ERROR
    topic_values: list[str] = []
    for item in topics:
        if not isinstance(item, str):
            return None, MALFORMED_ERROR
        value = item.strip()
        if value and value not in topic_values:
            topic_values.append(value)
    summary = summary.strip()
    if not summary:
        return None, MALFORMED_ERROR
    return (
        SummaryZhResult(
            summary_zh=summary[:MAX_SUMMARY_ZH_CHARS],
            topics_zh=topic_values,
        ),
        None,
    )


def parse_glossary_response(
    text: str,
) -> tuple[Optional[list[GlossaryEntry]], Optional[str]]:
    """Parse and shape-check the strict bilingual glossary response."""
    raw = _decode_json_object(text)
    if raw is None or not isinstance(raw.get("glossary"), (list, tuple)):
        return None, MALFORMED_ERROR
    entries: list[GlossaryEntry] = []
    for item in raw.get("glossary") or []:
        if not isinstance(item, Mapping):
            return None, MALFORMED_ERROR
        term = item.get("term")
        lang = item.get("lang")
        zh = item.get("zh")
        refs = item.get("evidence_refs", item.get("evidence_ids"))
        if (
            not isinstance(term, str)
            or not isinstance(lang, str)
            or not isinstance(zh, str)
            or not isinstance(refs, (list, tuple))
        ):
            return None, MALFORMED_ERROR
        term = term.strip()
        lang = lang.strip()
        zh = zh.strip()
        if not term or not lang or not zh:
            return None, MALFORMED_ERROR
        clean_refs: list[str] = []
        for ref in refs:
            if not isinstance(ref, str) or not ref.strip():
                return None, MALFORMED_ERROR
            value = ref.strip()
            if value not in clean_refs:
                clean_refs.append(value)
        entries.append(
            GlossaryEntry(
                term=term[:MAX_GLOSSARY_TERM_CHARS],
                lang=lang[:8],
                zh=zh[:MAX_GLOSSARY_ZH_CHARS],
                evidence_refs=clean_refs,
                evidence_ids=list(clean_refs),
                kp_id=(str(item.get("kp_id")).strip() if item.get("kp_id") else None),
            )
        )
    return entries, None


def parse_course_overview_response(
    text: str,
) -> tuple[Optional[CourseOverviewResult], Optional[str]]:
    """Parse the course synthesis response with a closed top-level shape."""
    raw = _decode_json_object(text)
    if raw is None or "overview" not in raw or "topic_map" not in raw:
        return None, MALFORMED_ERROR
    overview = raw.get("overview")
    topic_map = raw.get("topic_map")
    gaps = raw.get("gaps", [])
    if (
        not isinstance(overview, str)
        or not isinstance(topic_map, (list, tuple))
        or not isinstance(gaps, (list, tuple))
    ):
        return None, MALFORMED_ERROR
    clean_topics: list[dict[str, Any]] = []
    for item in topic_map:
        if not isinstance(item, Mapping) or not isinstance(item.get("topic"), str):
            return None, MALFORMED_ERROR
        topic = str(item.get("topic") or "").strip()
        if not topic:
            return None, MALFORMED_ERROR
        ids = item.get("material_ids") or []
        summary = item.get("summary") or ""
        if not isinstance(ids, (list, tuple)) or not isinstance(summary, str):
            return None, MALFORMED_ERROR
        clean_topics.append(
            {
                "topic": topic[:200],
                "material_ids": [str(value) for value in ids if str(value).strip()],
                "summary": summary.strip()[:1000],
            }
        )
    clean_gaps: list[str] = []
    for gap in gaps:
        if not isinstance(gap, str):
            return None, MALFORMED_ERROR
        value = gap.strip()
        if value and value not in clean_gaps:
            clean_gaps.append(value[:300])
    return (
        CourseOverviewResult(
            overview=overview.strip()[:MAX_COURSE_OVERVIEW_CHARS],
            topic_map=clean_topics,
            gaps=clean_gaps,
        ),
        None,
    )


def candidate_to_dict(candidate: KnowledgeCandidate) -> dict[str, Any]:
    return candidate.to_dict()


def material_result_to_dict(result: MaterialAIResult) -> dict[str, Any]:
    return result.to_dict()
