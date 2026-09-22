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
    "parse_structured_response",
    "candidate_to_dict",
    "material_result_to_dict",
]

#: 结构化 schema 版本 (进入 processing identity)。
SCHEMA_VERSION = "ai-schema-v1"

#: JSON 解析/校验失败时的统一错误码 (调用方映射为 processing failure)。
MALFORMED_ERROR = "AI_MALFORMED_RESPONSE"


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


def candidate_to_dict(candidate: KnowledgeCandidate) -> dict[str, Any]:
    return candidate.to_dict()


def material_result_to_dict(result: MaterialAIResult) -> dict[str, Any]:
    return result.to_dict()
