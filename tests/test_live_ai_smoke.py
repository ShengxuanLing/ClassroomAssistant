# -*- coding: utf-8 -*-
"""TASK-77 §17: 真实 AI provider 冒烟 (live smoke)。

默认 SKIP。只有同时满足以下全部条件时才真正出站::

    CLASSROOM_AI_LIVE_TEST=true
    CLASSROOM_AI_API_KEY (或 CLASSROOM_LLM_API_KEY) 已配置
    CLASSROOM_AI_BASE_URL (或 CLASSROOM_LLM_API_BASE) 为 https
    CLASSROOM_AI_MODEL (或 CLASSROOM_LLM_MODEL) 已配置

验证 (§17 要求):

1. provider connection (能连通并拿回响应)
2. structured output (合法 JSON, schema 可解析)
3. knowledge extraction (至少一个候选)
4. evidence grounding (候选可绑定真实 Evidence)
5. confidence (置信度为有限数值, 落在 [0, 1])

任何 401 / 403 / 429 / 500 / timeout / 畸形 JSON 都必须产生明确可诊断
结果 (pytest.fail 带 http_status, 绝不把 key/正文写进输出)。
Key 只读进程环境, 本文件无任何硬编码凭证。
"""

from __future__ import annotations

import math
import os

import pytest

pytestmark = pytest.mark.integration

from src.application.ai.config import get_api_key, load_ai_config
from src.application.ai.pipeline import AIUnderstandingPipeline
from src.application.ai.provider import AIRequestError, OpenAICompatibleAIProvider
from src.application.ai.validators import ground_candidates
from src.models import Evidence, EvidenceType, SourceReference

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _live_enabled() -> bool:
    return os.environ.get("CLASSROOM_AI_LIVE_TEST", "").strip().lower() in _TRUE_VALUES


def _require_live():
    if not _live_enabled():
        pytest.skip("CLASSROOM_AI_LIVE_TEST is not enabled")
    config = load_ai_config()
    api_key = get_api_key()
    missing = []
    if not api_key:
        missing.append("CLASSROOM_AI_API_KEY")
    if not (config.base_url or "").strip():
        missing.append("CLASSROOM_AI_BASE_URL")
    if not (config.text_model or "").strip():
        missing.append("CLASSROOM_AI_MODEL")
    if missing:
        pytest.skip("no live AI credentials configured (missing: %s)" % ", ".join(missing))
    return config, api_key


def _evidence(evidence_id, content, material_id="mat-live"):
    return Evidence(
        evidence_id=evidence_id,
        content=content,
        source_reference=SourceReference(material_id=material_id),
        evidence_type=EvidenceType.DOCUMENT,
    )


def test_live_provider_connection_and_structured_output():
    """1+2: 连通 + 结构化 JSON 输出 (UAB 西语微积分短文本)。"""
    config, api_key = _require_live()
    provider = OpenAICompatibleAIProvider(
        api_key=api_key,
        base_url=config.base_url or "",
        model=config.text_model or "",
        max_retries=1,
    )
    try:
        text = provider.analyze_text(
            "La integración por partes es un método fundamental del cálculo. "
            "La definición de integral definida es el área bajo la curva.",
            chunk_id="chunk-live-0",
            material_label="live-smoke",
            content_language="es",
            timeout_seconds=120,
        )
    except AIRequestError as exc:
        pytest.fail("live AI request failed: %s | detail=%s" % (exc, exc.detail))
    assert isinstance(text, str) and text.strip(), "empty live response"
    import json as _json

    try:
        payload = _json.loads(text)
    except ValueError as exc:
        pytest.fail("live AI returned malformed JSON: %s" % (exc,))
    assert isinstance(payload, dict), type(payload)


def test_live_knowledge_extraction_grounding_confidence():
    """3+4+5: 知识抽取 + Evidence grounding + 置信度形状。"""
    config, api_key = _require_live()
    provider = OpenAICompatibleAIProvider(
        api_key=api_key,
        base_url=config.base_url or "",
        model=config.text_model or "",
        max_retries=1,
    )
    pipeline = AIUnderstandingPipeline(provider)
    evidences = [
        _evidence(
            "ev-live-1",
            "La fotosíntesis ocurre en los cloroplastos de la célula vegetal. "
            "La definición de cloroplasto es el orgánulo donde ocurre la fotosíntesis.",
        )
    ]
    try:
        merged, mapping, _, stats = pipeline.analyze_material(
            evidences,
            material_id="mat-live",
            material_label="live-smoke",
            kind="text",
            content_language="es",
            timeout_seconds=120,
        )
    except AIRequestError as exc:
        pytest.fail("live AI analysis failed: %s | detail=%s" % (exc, exc.detail))
    assert stats["chunk_failed"] == 0, stats
    assert merged.candidates, "live model returned zero candidates"
    grounded, rejected = ground_candidates(
        merged.candidates,
        chunk_to_evidence=mapping,
        material_evidence_ids=["ev-live-1"],
    )
    assert grounded, "live candidates failed evidence grounding: %s" % (rejected,)
    for entry in grounded:
        confidence = float(entry.candidate.confidence)
        assert math.isfinite(confidence) and 0.0 <= confidence <= 1.0, confidence
        assert entry.evidence_ids, entry
        assert set(entry.evidence_ids) <= {"ev-live-1"}, entry.evidence_ids
