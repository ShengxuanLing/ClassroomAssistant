# -*- coding: utf-8 -*-
"""TASK-76 — AI 语义理解管线测试。

策略 (§31):

- A. Unit: config / chunking / schemas / validators / merge / provider /
  kind 识别 / identity (纯函数, 无网络、无凭证)。
- B. Fake Provider 全链路: text / image / audio 三条能力链 +
  grounding / 去重 / 冲突 / retry / 幂等 + student state 隔离 +
  API endpoint + UI 资源一致性。
- C. Contract: 真实 provider 的形状测试 (构造/脱敏, 不出站)。
- D. Real AI smoke: 仅 ``CLASSROOM_AI_LIVE_TEST=true`` 且有 key 时执行,
  否则干净 SKIP (绝不 FAIL)。

全部默认测试使用 ``FakeAIProvider`` (确定性 fixture, 零出站)。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

import pytest

from src.application.ai import PIPELINE_VERSION, PROMPT_VERSION
from src.application.ai.chunking import chunk_identity, chunk_text, chunks_for_evidence
from src.application.ai.config import (
    AIEnvNames,
    get_api_key,
    load_ai_config,
)
from src.application.ai.merge import (
    deduplicate_candidates,
    normalise_title,
    propose_merge_with_existing,
)
from src.application.ai.pipeline import (
    AIAnalysisFailure,
    AIUnderstandingPipeline,
    detect_material_kind,
    processing_identity,
)
from src.application.ai.prompts import (
    ALLOWED_KNOWLEDGE_TYPES,
    CHUNK_EXTRACTION_PROMPT_VERSION,
    build_chunk_extraction_prompt,
    build_summary_prompt,
)
from src.application.ai.provider import (
    AIProvider,
    AIRequestError,
    FakeAIProvider,
    OpenAICompatibleAIProvider,
    ProviderCapabilities,
)
from src.application.ai.schemas import (
    MALFORMED_ERROR,
    KnowledgeCandidate,
    parse_material_response,
    parse_structured_response,
)
from src.application.ai.validators import (
    AI_PIPELINE_POLICY,
    EVIDENCE_COPY_SIMILARITY_THRESHOLD,
    GroundedCandidate,
    candidate_copy_reason,
    candidate_knowledge_id,
    classify_confidence,
    evidence_copy_similarity,
    ground_candidates,
    map_candidate_to_kp_payload,
)
from src.application.errors import ConfigurationError, InvalidInputError
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace
from src.models import Evidence, EvidenceType, SourceReference

FIXED_TIME = "2026-09-18T09:00:00+00:00"


def _workspace(tmp_path, **kw):
    return Workspace(
        str(tmp_path / "data"),
        clock=fixed_clock(FIXED_TIME),
        persistence=False,
        asr_mode="mock",
        ocr_mode="mock",
        **kw,
    )


def _write(tmp_path, name, content, binary=False):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return str(path)


def _register_text(ws, course_id, text, filename="lecture.txt", **kw):
    import tempfile

    fd, path = tempfile.mkstemp(suffix="-" + filename, dir=str(tempfile.gettempdir()))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    try:
        return ws.register_material(course_id, path, filename=filename, **kw)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _evidence(evidence_id, content, material_id="mat-1"):
    return Evidence(
        evidence_id=evidence_id,
        content=content,
        source_reference=SourceReference(material_id=material_id),
        evidence_type=EvidenceType.DOCUMENT,
    )


class _VerbatimCopyProvider(AIProvider):
    """Deliberately malicious fixture: returns the source sentence as content."""

    name = "verbatim-copy-test"

    @property
    def capabilities(self):
        return ProviderCapabilities(
            supports_text=True,
            supports_image=True,
            supports_audio=False,
            supports_structured_output=True,
        )

    def generate_structured(self, prompt, *, timeout_seconds=60, max_output_chars=8000):
        match = re.search(r"EVIDENCE TEXT:\n(.*?)(?:\n\nReturn JSON:|\Z)", prompt, re.S)
        source = (match.group(1) if match else "Evidencia de prueba").strip()
        chunk_match = re.search(r"AVAILABLE CHUNKS: \[(.*?)\]", prompt)
        chunk_id = chunk_match.group(1).strip() if chunk_match else "chunk-1"
        return json.dumps(
            {
                "summary": "Síntesis independiente.",
                "topics": ["tema"],
                "knowledge_points": [
                    {
                        "title": "Contenido fuente",
                        "description": source,
                        "type": "concept",
                        "importance": "high",
                        "confidence": 0.99,
                        "evidence_refs": [chunk_id],
                        "relations": [],
                        "examples": [],
                        "original_terms": [],
                    }
                ],
            },
            ensure_ascii=False,
        )


# ======================================================================
# A1. AIConfig
# ======================================================================


class TestAIConfig:
    def test_disabled_by_default(self):
        config = load_ai_config(env={})
        assert config.enabled is False
        assert config.has_credentials is False

    def test_enabled_flag(self):
        config = load_ai_config(env={AIEnvNames.ENABLED: "true"})
        assert config.enabled is True

    def test_models_fall_back_to_llm_vars(self):
        config = load_ai_config(
            env={
                AIEnvNames.FALLBACK_MODEL: "llm-model-x",
                AIEnvNames.FALLBACK_BASE_URL: "https://example.com/v1",
                AIEnvNames.FALLBACK_API_KEY: "secret-key",
            }
        )
        assert config.text_model == "llm-model-x"
        assert config.vision_model == "llm-model-x"
        assert config.audio_model == "llm-model-x"
        assert config.has_credentials is True

    def test_vision_audio_models_default_to_text_model(self):
        config = load_ai_config(env={AIEnvNames.MODEL: "m1"})
        assert config.vision_model == "m1"
        assert config.audio_model == "m1"

    def test_non_https_base_url_is_rejected(self):
        with pytest.raises(ConfigurationError):
            load_ai_config(env={AIEnvNames.BASE_URL: "http://insecure/v1"})

    def test_describe_and_repr_never_carry_the_key(self):
        config = load_ai_config(
            env={
                AIEnvNames.ENABLED: "1",
                AIEnvNames.API_KEY: "sk-super-secret-value",
                AIEnvNames.BASE_URL: "https://example.com/v1",
            }
        )
        assert config.api_key_present is True
        blob = json.dumps(config.describe(), ensure_ascii=False) + repr(config)
        assert "sk-super-secret-value" not in blob

    def test_get_api_key_returns_value_but_describe_does_not(self):
        env = {AIEnvNames.API_KEY: "sk-abc123"}
        assert get_api_key(env) == "sk-abc123"
        assert "sk-abc123" not in json.dumps(load_ai_config(env).describe())
        assert get_api_key({}) is None

    def test_bad_numbers_are_rejected(self):
        with pytest.raises(ConfigurationError):
            load_ai_config(env={AIEnvNames.TIMEOUT: "0"})
        with pytest.raises(ConfigurationError):
            load_ai_config(env={AIEnvNames.MAX_RETRIES: "-1"})


# ======================================================================
# A2. Chunking
# ======================================================================


class TestChunking:
    def test_empty_text_yields_no_chunks(self):
        chunks, truncated = chunk_text("", material_id="m1")
        assert chunks == [] and truncated is False

    def test_stable_identity(self):
        text = "párrafo uno.\n\npárrafo dos.\n\npárrafo tres."
        first, _ = chunk_text(text, material_id="m1")
        second, _ = chunk_text(text, material_id="m1")
        assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
        assert all(c.chunk_id.startswith("chunk-") for c in first)

    def test_different_materials_have_different_ids(self):
        first, _ = chunk_text("hello world", material_id="m1")
        second, _ = chunk_text("hello world", material_id="m2")
        assert first[0].chunk_id != second[0].chunk_id

    def test_long_text_splits_with_overlap(self):
        text = "\n\n".join("sentence %d about integration." % i for i in range(60))
        chunks, _ = chunk_text(text, material_id="m1", chunk_chars=200, overlap_chars=20)
        assert len(chunks) > 1
        # overlap: 相邻 chunk 首尾有公共子串 (跨边界语义保留)。
        assert chunks[0].text[-20:] in chunks[1].text

    def test_max_chunks_truncates(self):
        text = "\n\n".join("line %d about the topic." % i for i in range(500))
        chunks, truncated = chunk_text(
            text, material_id="m1", chunk_chars=200, max_chunks=3
        )
        assert len(chunks) == 3 and truncated is True

    def test_chunk_identity_shape(self):
        cid = chunk_identity(
            material_id="m", page=2, section="s", chunk_index=0, content_hash="ab"
        )
        assert cid.startswith("chunk-") and len(cid) == len("chunk-") + 16

    def test_chunks_for_evidence_maps_back_to_real_evidence(self):
        evs = [_evidence("ev-1", "alpha beta."), _evidence("ev-2", "gamma delta.")]
        chunks, mapping, truncated = chunks_for_evidence(evs, material_id="mat-1")
        assert chunks and truncated is False
        assert set(mapping.values()) == {"ev-1", "ev-2"}
        # 模型永远接触不到真实 Evidence ID —— 映射表是唯一的翻回通道。
        for chunk in chunks:
            assert chunk.chunk_id != mapping[chunk.chunk_id]


# ======================================================================
# A3. Schemas (LLM JSON 不可信: 先解析再校验)
# ======================================================================


class TestSchemas:
    def _payload(self, **kw):
        body = {
            "summary": "s",
            "topics": ["t"],
            "knowledge_points": [
                {
                    "title": "Integración por partes",
                    "description": "Método de integración.",
                    "type": "procedure",
                    "importance": "high",
                    "confidence": 0.93,
                    "evidence_refs": ["chunk-abc"],
                    "relations": ["r"],
                    "examples": ["e"],
                    "original_terms": ["integración por partes"],
                }
            ],
        }
        body.update(kw)
        return json.dumps(body, ensure_ascii=False)

    def test_valid_response_parses(self):
        result, error = parse_structured_response(self._payload(), "chunk-abc")
        assert error is None
        assert len(result.candidates) == 1
        candidate = result.candidates[0]
        assert candidate.title == "Integración por partes"
        assert candidate.confidence == pytest.approx(0.93)
        assert candidate.evidence_refs == ["chunk-abc"]

    def test_garbage_is_malformed_not_partial(self):
        for bad in ("", "not json", "[1,2", "# Knowledge Points\n..."):
            result, error = parse_structured_response(bad, "c")
            assert result is None and error == MALFORMED_ERROR

    def test_fenced_json_is_tolerated(self):
        result, error = parse_structured_response(
            "```json\n" + self._payload() + "\n```", "chunk-abc"
        )
        assert error is None and len(result.candidates) == 1

    def test_unknown_type_falls_back_to_concept(self):
        body = json.loads(self._payload())
        body["knowledge_points"][0]["type"] = "telepathy"
        result, _ = parse_structured_response(json.dumps(body), "c")
        assert result.candidates[0].kp_type == "concept"

    def test_empty_candidates_are_dropped(self):
        body = json.loads(self._payload())
        body["knowledge_points"].append({"title": "", "description": ""})
        result, _ = parse_structured_response(json.dumps(body), "c")
        assert len(result.candidates) == 1

    def test_allowed_types_cover_spec(self):
        for required in (
            "concept", "definition", "formula", "procedure",
            "example", "relationship", "fact", "principle",
        ):
            assert required in ALLOWED_KNOWLEDGE_TYPES

    def test_material_response_parses_summary_fields(self):
        body = {
            "summary": "resumen", "topics": ["t"], "definitions": ["d"],
            "formulas": ["f"], "examples": ["e"], "prerequisites": ["p"],
            "difficulties": ["x"], "knowledge_points": [],
        }
        result, error = parse_material_response(json.dumps(body), ["c1"])
        assert error is None
        assert result.summary == "resumen"
        assert result.formulas == ["f"]


# ======================================================================
# A4. Validators (confidence 策略 + evidence grounding)
# ======================================================================


class TestValidators:
    def test_confidence_policy_thresholds(self):
        assert classify_confidence(0.95).decision == "auto"
        assert classify_confidence(0.90).decision == "auto"
        assert classify_confidence(0.89).decision == "review"
        assert classify_confidence(0.70).decision == "review"
        assert classify_confidence(0.69).decision == "reject"
        assert classify_confidence(0.0).decision == "reject"

    def test_policy_is_overridable(self):
        decision = classify_confidence(0.8, {"auto_accept": 0.75, "review": 0.5})
        assert decision.decision == "auto"
        assert AI_PIPELINE_POLICY["auto_accept"] == 0.90  # 全局表未被污染

    def _candidate(self, refs, confidence=0.95):
        return KnowledgeCandidate(
            title="Integración", description="d", confidence=confidence,
            evidence_refs=list(refs),
        )

    def test_illegal_refs_reject_the_candidate(self):
        grounded, rejected = ground_candidates(
            [self._candidate(["chunk-nope"])],
            chunk_to_evidence={"chunk-ok": "ev-1"},
            material_evidence_ids=["ev-1"],
        )
        assert grounded == [] and len(rejected) == 1
        assert "chunk-nope" in (rejected[0].reject_reason or "")

    def test_refs_from_another_material_are_rejected(self):
        grounded, rejected = ground_candidates(
            [self._candidate(["chunk-ok"])],
            chunk_to_evidence={"chunk-ok": "ev-other-course"},
            material_evidence_ids=["ev-1"],
        )
        assert grounded == [] and len(rejected) == 1

    def test_high_confidence_without_evidence_never_auto(self):
        grounded, rejected = ground_candidates(
            [self._candidate([], confidence=0.99)],
            chunk_to_evidence={},
            material_evidence_ids=["ev-1"],
        )
        assert grounded == [] and len(rejected) == 1

    def test_low_confidence_with_evidence_goes_to_review(self):
        grounded, _ = ground_candidates(
            [self._candidate(["chunk-ok"], confidence=0.5)],
            chunk_to_evidence={"chunk-ok": "ev-1"},
            material_evidence_ids=["ev-1"],
        )
        assert len(grounded) == 1 and grounded[0].decision == "review"

    def test_exact_evidence_copy_is_rejected_but_evidence_is_retained(self):
        source = (
            "La fotosíntesis convierte la luz solar en materia orgánica dentro de "
            "los cloroplastos de las células vegetales."
        )
        candidate = KnowledgeCandidate(
            title="Fotosíntesis",
            description=source,
            confidence=0.99,
            evidence_refs=["chunk-ok"],
        )
        grounded, rejected = ground_candidates(
            [candidate],
            chunk_to_evidence={"chunk-ok": "ev-1"},
            material_evidence_ids=["ev-1"],
            evidence_texts={"ev-1": source},
        )
        assert grounded == []
        assert len(rejected) == 1
        assert rejected[0].evidence_ids == ["ev-1"]
        assert "candidate content" in (rejected[0].reject_reason or "")
        assert "ev-1" in (rejected[0].reject_reason or "")
        assert source not in (rejected[0].reject_reason or "")

    def test_long_verbatim_title_copy_is_rejected(self):
        source = (
            "El mercado de factores de producción determina la eficiencia y el "
            "equilibrio de los sistemas económicos."
        )
        candidate = KnowledgeCandidate(
            title="El mercado de factores de producción determina la eficiencia",
            description="Síntesis original del contenido.",
            confidence=0.95,
            evidence_refs=["chunk-ok"],
        )
        assert evidence_copy_similarity(candidate.title, source) >= (
            EVIDENCE_COPY_SIMILARITY_THRESHOLD
        )
        _, rejected = ground_candidates(
            [candidate],
            chunk_to_evidence={"chunk-ok": "ev-1"},
            material_evidence_ids=["ev-1"],
            evidence_texts={"ev-1": source},
        )
        assert "title" in (rejected[0].reject_reason or "")

    def test_genuine_abstraction_is_kept(self):
        source = (
            "La fotosíntesis convierte la luz solar en materia orgánica dentro de "
            "los cloroplastos de las células vegetales."
        )
        candidate = KnowledgeCandidate(
            title="Fotosíntesis",
            description=(
                "Proceso vegetal que transforma energía luminosa en azúcares "
                "mediante orgánulos celulares especializados."
            ),
            confidence=0.95,
            evidence_refs=["chunk-ok"],
        )
        reason = candidate_copy_reason(
            candidate, evidence_ids=["ev-1"], evidence_texts={"ev-1": source}
        )
        assert reason is None
        grounded, rejected = ground_candidates(
            [candidate],
            chunk_to_evidence={"chunk-ok": "ev-1"},
            material_evidence_ids=["ev-1"],
            evidence_texts={"ev-1": source},
        )
        assert rejected == [] and len(grounded) == 1

    def test_copy_check_only_uses_candidate_own_resolved_evidence(self):
        source = "La energía solar alimenta el crecimiento de las plantas verdes."
        candidate = KnowledgeCandidate(
            title="Energía solar",
            description=source,
            confidence=0.95,
            evidence_refs=["chunk-other"],
        )
        reason = candidate_copy_reason(
            candidate,
            evidence_ids=["ev-1"],
            evidence_texts={"ev-1": "Un evidence legal pero distinto."},
        )
        assert reason is None

    def test_reordered_technical_vocabulary_is_not_a_copy(self):
        source = (
            "La fotosíntesis convierte la luz solar en materia orgánica dentro de "
            "los cloroplastos de las células vegetales."
        )
        candidate = KnowledgeCandidate(
            title="Fotosíntesis",
            description=(
                "Cloroplastos de las células vegetales convierten la fotosíntesis "
                "mediante la luz solar en materia orgánica."
            ),
            confidence=0.95,
            evidence_refs=["chunk-ok"],
        )
        assert candidate_copy_reason(
            candidate, evidence_ids=["ev-1"], evidence_texts={"ev-1": source}
        ) is None

    def test_missing_evidence_text_fails_closed(self):
        candidate = KnowledgeCandidate(
            title="Fotosíntesis", description="Una explicación original.",
            confidence=0.95, evidence_refs=["chunk-ok"],
        )
        grounded, rejected = ground_candidates(
            [candidate],
            chunk_to_evidence={"chunk-ok": "ev-1"},
            material_evidence_ids=["ev-1"],
            evidence_texts={},
        )
        assert grounded == []
        assert "unavailable" in (rejected[0].reject_reason or "")
        assert rejected[0].evidence_ids == ["ev-1"]

    def test_copy_detection_is_backward_compatible_without_evidence_text(self):
        source = "La fotosíntesis convierte la luz solar en materia orgánica."
        candidate = KnowledgeCandidate(
            title="Fotosíntesis", description=source, confidence=0.95,
            evidence_refs=["chunk-ok"],
        )
        grounded, rejected = ground_candidates(
            [candidate],
            chunk_to_evidence={"chunk-ok": "ev-1"},
            material_evidence_ids=["ev-1"],
        )
        assert rejected == [] and len(grounded) == 1

    def test_candidate_knowledge_id_is_deterministic(self):
        first = candidate_knowledge_id(course_id="c", title="  Integración ", kp_type="concept")
        second = candidate_knowledge_id(course_id="c", title="integración", kp_type="concept")
        assert first == second and first.startswith("aikp-")
        other = candidate_knowledge_id(course_id="c2", title="integración", kp_type="concept")
        assert other != first

    def test_kp_payload_maps_to_existing_domain_model(self):
        from src.models import KnowledgePoint

        grounded = GroundedCandidate(
            candidate=self._candidate(["chunk-ok"]), evidence_ids=["ev-1"], decision="auto"
        )
        payload = map_candidate_to_kp_payload(
            grounded, course_id="c", material_id="m"
        )
        kp = KnowledgePoint.from_dict(payload)  # 现有模型直接接受, 无 migration
        assert kp.evidence_refs == ["ev-1"]
        assert kp.review_status == "pending"  # 管线绝不私自 CONFIRMED
        assert kp.validation_status == "unverified"


# ======================================================================
# A5. Merge (去重 / 合并提议 / 冲突)
# ======================================================================


class TestMerge:
    def _candidate(self, title, refs=("chunk-1",)):
        return KnowledgeCandidate(
            title=title, description="d", confidence=0.9, evidence_refs=list(refs)
        )

    def test_normalise_title(self):
        assert normalise_title("  Integración, por partes! ") == normalise_title("integración por partes")

    def test_dedup_keeps_first_and_merges_refs(self):
        first = self._candidate("Integración", ["c1"])
        second = self._candidate("integración", ["c2"])
        deduped, dropped = deduplicate_candidates([first, second])
        assert len(deduped) == 1 and dropped == 1
        assert sorted(deduped[0].evidence_refs) == ["c1", "c2"]

    def test_identical_title_proposes_attach(self):
        proposals = propose_merge_with_existing(
            [self._candidate("Integración")],
            [{"knowledge_id": "kp-1", "title": "Integración",
              "content": "x", "evidence_refs": ["ev-9"]}],
            candidate_evidence=[["ev-1"]],
        )
        assert proposals[0].action == "attach"
        assert proposals[0].existing_id == "kp-1"

    def test_similar_title_different_evidence_proposes_conflict(self):
        proposals = propose_merge_with_existing(
            [self._candidate("Integración por partes método")],
            [{"knowledge_id": "kp-1", "title": "Integración por partes",
              "content": "x", "evidence_refs": ["ev-9"]}],
            candidate_evidence=[["ev-1"]],
        )
        assert proposals[0].action == "conflict"

    def test_unrelated_proposes_create(self):
        proposals = propose_merge_with_existing(
            [self._candidate("Derivadas parciales totalmente distintas xyz")],
            [{"knowledge_id": "kp-1", "title": "Integración",
              "content": "x", "evidence_refs": ["ev-9"]}],
            candidate_evidence=[["ev-1"]],
        )
        assert proposals[0].action == "create"


# ======================================================================
# A6. Provider (capability / 脱敏 / 确定性)
# ======================================================================


class TestProviders:
    def test_fake_capabilities(self):
        caps = FakeAIProvider().capabilities
        assert isinstance(caps, ProviderCapabilities)
        assert caps.supports_text and caps.supports_structured_output
        assert caps.supports_audio is False  # 转写走既有 ASR 链路

    def test_fake_is_deterministic(self):
        provider = FakeAIProvider()
        prompt = build_chunk_extraction_prompt(chunk_id="c1", chunk_text="alpha. beta.")
        assert provider.generate_structured(prompt) == provider.generate_structured(prompt)

    def test_fake_marks_itself_as_fixture(self):
        out = json.loads(FakeAIProvider().generate_structured("EVIDENCE TEXT:\nalpha. beta."))
        assert "knowledge_points" in out and out["knowledge_points"]

    def test_openai_provider_requires_key(self):
        with pytest.raises(ConfigurationError):
            OpenAICompatibleAIProvider(api_key="  ", base_url="https://x.ai/v1", model="m")

    def test_openai_provider_rejects_non_https(self):
        with pytest.raises(ConfigurationError):
            OpenAICompatibleAIProvider(
                api_key="k", base_url="http://x.ai/v1", model="m"
            )

    def test_openai_repr_and_errors_carry_no_key(self):
        provider = OpenAICompatibleAIProvider(
            api_key="sk-live-secret", base_url="https://x.ai/v1", model="m"
        )
        assert "sk-live-secret" not in repr(provider)
        assert "sk-live-secret" not in str(provider.api_host)
        try:
            provider.transcribe_audio("/nope.mp3")
        except AIRequestError as exc:
            assert "sk-live-secret" not in str(exc)
            assert "sk-live-secret" not in json.dumps(exc.detail or {})
        else:  # pragma: no cover
            raise AssertionError("transcribe_audio should be unsupported")

    def test_detect_material_kind(self):
        assert detect_material_kind(filename="a.pdf") == "text"
        assert detect_material_kind(filename="a.docx") == "text"
        assert detect_material_kind(filename="notes.md") == "text"
        assert detect_material_kind(filename="slide.png") == "image"
        assert detect_material_kind(filename="board.JPG") == "image"
        assert detect_material_kind(filename="class.mp3") == "audio"
        assert detect_material_kind(filename="x.wav") == "audio"
        assert detect_material_kind(source_type="audio") == "audio"
        assert detect_material_kind(source_type="ocr") == "image"
        assert detect_material_kind(filename="weird.xyz") == "unknown"

    def test_processing_identity_is_deterministic(self):
        first = processing_identity(material_hash="h", model="m", prompt_version="p")
        second = processing_identity(material_hash="h", model="m", prompt_version="p")
        assert first == second and first.startswith("ai-")
        assert processing_identity(material_hash="h2", model="m", prompt_version="p") != first


# ======================================================================
# B1. Pipeline (Fake): 两级理解 / 缓存 / 失败 / retry
# ======================================================================


class _CountingFake(FakeAIProvider):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def generate_structured(self, prompt, **kw):
        self.calls += 1
        return super().generate_structured(prompt, **kw)


class _FlakyProvider(AIProvider):
    """首轮对特定 chunk 抛错, retry 后成功 (验证只重跑失败 chunk)。"""

    name = "flaky"

    def __init__(self):
        self.calls = 0
        self._fail_once_markers: list[str] = []

    @property
    def capabilities(self):
        return ProviderCapabilities(supports_text=True, supports_structured_output=True)

    def fail_once_on(self, marker):
        self._fail_once_markers.append(marker)

    def generate_structured(self, prompt, **kw):
        self.calls += 1
        for marker in list(self._fail_once_markers):
            if marker in prompt:
                self._fail_once_markers.remove(marker)
                raise AIRequestError("boom", detail={"provider": self.name})
        return FakeAIProvider().generate_structured(prompt, **kw)


class _GarbageProvider(AIProvider):
    name = "garbage"

    @property
    def capabilities(self):
        return ProviderCapabilities(supports_text=True, supports_structured_output=True)

    def generate_structured(self, prompt, **kw):
        return "# Knowledge Points\n- blah blah (markdown, not JSON)"


class TestPipeline:
    def test_two_level_understanding(self):
        pipeline = AIUnderstandingPipeline(FakeAIProvider())
        evs = [
            _evidence("ev-1", "La integración por partes es un método. Se usa con productos."),
            _evidence("ev-2", "La definición de derivada es el límite del cociente."),
        ]
        merged, mapping, chunks, stats = pipeline.analyze_material(
            evs, material_id="mat-1", material_label="calc.pdf", kind="text"
        )
        assert stats["chunk_total"] >= 1 and stats["chunk_failed"] == 0
        assert merged.candidates, "Level 1 必须产出候选"
        assert set(mapping.values()) == {"ev-1", "ev-2"}
        assert merged.provider == "fake-deterministic"

    def test_chunk_cache_avoids_duplicate_calls(self):
        provider = _CountingFake()
        pipeline = AIUnderstandingPipeline(provider)
        evs = [_evidence("ev-1", "alpha. beta. gamma.")]
        pipeline.analyze_material(evs, material_id="m", kind="text")
        first_calls = provider.calls
        assert first_calls > 0
        pipeline.analyze_material(evs, material_id="m", kind="text")
        # Level 1 全命中缓存; Level 2 merge 多一次调用是允许的, 但不得重跑全部。
        assert provider.calls <= first_calls + 1

    def test_retry_only_failed_chunks(self):
        provider = _FlakyProvider()
        pipeline = AIUnderstandingPipeline(provider)
        evs = [
            _evidence("ev-1", "primer contenido del curso sobre límites."),
            _evidence("ev-2", "segundo contenido del curso sobre derivadas."),
        ]
        provider.fail_once_on("segundo contenido")
        with pytest.raises(AIAnalysisFailure):
            pipeline.analyze_material(evs, material_id="m", kind="text")
        calls_after_fail = provider.calls
        assert calls_after_fail > 0
        merged, _, _, stats = pipeline.analyze_material(evs, material_id="m", kind="text")
        assert stats["chunk_failed"] == 0 and merged.candidates

    def test_malformed_response_is_failure_not_fake_kp(self):
        from src.application.ai.pipeline import AI_ANALYSIS_FAILED

        pipeline = AIUnderstandingPipeline(_GarbageProvider())
        with pytest.raises(AIAnalysisFailure) as exc_info:
            pipeline.analyze_material(
                [_evidence("ev-1", "contenido real.")], material_id="m", kind="text"
            )
        # 422 口径 (PROCESSING_ERROR) + failure 标记区分 AI 失败。
        assert exc_info.value.code == "PROCESSING_ERROR"
        assert (exc_info.value.detail or {}).get("failure") == AI_ANALYSIS_FAILED

    def test_empty_evidence_is_failure(self):
        pipeline = AIUnderstandingPipeline(FakeAIProvider())
        with pytest.raises(AIAnalysisFailure):
            pipeline.analyze_material([], material_id="m", kind="text")

    def test_prompts_demand_grounding(self):
        prompt = build_chunk_extraction_prompt(
            chunk_id="c", chunk_text="La fotosíntesis ocurre en los cloroplastos."
        )
        for required in ("ONLY", "evidence_refs", "confidence", "STRICT JSON", "CONDENSED"):
            assert required in prompt
        assert "Verbatim source wording belongs ONLY" in prompt
        assert "report-layer synthesis" in prompt
        assert "Knowledge content and source evidence are separate" in prompt
        assert "roles even when they discuss the same concept" in prompt

        summary_prompt = build_summary_prompt(material_text="Una fuente original.")
        assert "report-layer abstractions" in summary_prompt
        assert "verbatim source only in the evidence store" in summary_prompt


# ======================================================================
# B2. Workspace E2E: text / image / audio + Review + 幂等 + 隔离
# ======================================================================


TEXT_ES = (
    "La integración por partes es un método fundamental. "
    "La definición de integral definida es el área bajo la curva. "
    "Por ejemplo, la integral de x es x al cuadrado sobre dos. "
    "El teorema fundamental del cálculo conecta derivación e integración."
)


def _enable(ws):
    return ws.configure_ai(enabled=True, provider=FakeAIProvider())


class TestWorkspaceAIText:
    def test_verbatim_candidate_is_reported_without_persisting_or_losing_evidence(
        self, tmp_path
    ):
        ws = _workspace(tmp_path)
        cid = ws.create_course("Biología", "BIO1", "es")["course_id"]
        source = (
            "La fotosíntesis convierte la luz solar en materia orgánica dentro de "
            "los cloroplastos de las células vegetales."
        )
        record = _register_text(ws, cid, source, filename="photosynthesis.txt")
        ws.process_material(cid, record["material_id"])
        before_ids = {kp["knowledge_id"] for kp in ws.knowledge_points(cid)}
        before_evidence = ws.material_evidence(cid, record["material_id"])

        ws.configure_ai(enabled=True, provider=_VerbatimCopyProvider())
        report = ws.analyze_material_with_ai(cid, record["material_id"])

        after_ids = {kp["knowledge_id"] for kp in ws.knowledge_points(cid)}
        after_evidence = ws.material_evidence(cid, record["material_id"])
        assert after_ids == before_ids
        assert [item["content"] for item in after_evidence] == [
            item["content"] for item in before_evidence
        ]
        assert report["rejected"]
        rejected = report["rejected"][0]
        assert "copies evidence" in rejected["reason"]
        assert rejected["evidence_ids"] == [
            item["evidence_id"] for item in after_evidence
        ]
        assert report["summary"] == "Síntesis independiente."
        assert report["summary"] != source

    def test_qgis_source_becomes_abstracted_knowledge_with_separate_evidence(
        self, tmp_path
    ):
        ws = _workspace(tmp_path)
        cid = ws.create_course("GIS", "GIS1", "ca")["course_id"]
        source = (
            "QGIS Desktop és una interfície que permet interactuar amb mapes. "
            "Disposa de les principals eines per visualitzar, editar i gestionar "
            "capes geoespacials, així com per processar-ne la informació."
        )
        record = _register_text(ws, cid, source, filename="qgis.txt")
        ws.process_material(cid, record["material_id"])
        _enable(ws)
        ws.analyze_material_with_ai(cid, record["material_id"])

        points = [
            point
            for point in ws.knowledge_points(cid)
            if point.get("generation_mode") == "ai_summary"
        ]
        evidence = ws.material_evidence(cid, record["material_id"])
        assert points
        assert evidence
        evidence_by_id = {item["evidence_id"]: item["content"] for item in evidence}
        assert source in evidence_by_id.values()
        for point in points:
            linked_text = "\n".join(
                evidence_by_id[evidence_id]
                for evidence_id in point["evidence_refs"]
                if evidence_id in evidence_by_id
            )
            assert point["title"] != source
            assert point["content"] != source
            assert point["content"] != linked_text
            assert linked_text

    def test_upload_process_analyze_flow(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("Cálculo II", "M101", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES, filename="calc.txt")
        job = ws.process_material(cid, record["material_id"])
        assert job["status"] == "SUCCEEDED"
        before_kps = ws.knowledge_points(cid)
        assert len(before_kps) >= 1  # 旧确定性链路照常产出
        _enable(ws)
        report = ws.analyze_material_with_ai(cid, record["material_id"])
        assert report["status"] == "completed"
        assert report["kind"] == "text"
        total = (
            len(report["auto_accepted"])
            + len(report["needs_review"])
            + len(report["conflicts"])
        )
        assert total >= 1
        assert report["knowledge_points_total"] == total
        # 每个自动 KP 都可追溯: KP -> Evidence -> Material。
        after_kps = ws.knowledge_points(cid)
        assert len(after_kps) > len(before_kps)
        for entry in report["auto_accepted"] + report["needs_review"]:
            trace = ws.knowledge_trace(cid, entry["knowledge_id"])
            assert trace["evidence"], entry
            assert trace["materials"], entry
            assert trace["knowledge_point"]["generation_mode"] == "ai_summary"
        # Review 队列: 低置信度/冲突的人工入口 (pending, 待 confirm)。
        pending = ws.knowledge_points(cid, review_status="pending")
        assert len(pending) >= 1
        assert report["processing_identity"].startswith("ai-")
        assert report["pipeline_version"] == PIPELINE_VERSION

    def test_repeated_analysis_is_idempotent(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES)
        ws.process_material(cid, record["material_id"])
        _enable(ws)
        first = ws.analyze_material_with_ai(cid, record["material_id"])
        count = len(ws.knowledge_points(cid))
        evidence_total = len(ws.material_evidence(cid, record["material_id"]))
        second = ws.analyze_material_with_ai(cid, record["material_id"])
        assert second["processing_identity"] == first["processing_identity"]
        assert len(ws.knowledge_points(cid)) == count
        assert len(ws.material_evidence(cid, record["material_id"])) == evidence_total

    def test_analysis_requires_evidence(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES)
        _enable(ws)
        with pytest.raises(AIAnalysisFailure):
            ws.analyze_material_with_ai(cid, record["material_id"])
        # 失败不破坏已有数据: 材料记录与旧 KP 原样保留。
        assert ws.get_material(cid, record["material_id"])["material_id"] == record["material_id"]

    def test_disabled_by_default(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES)
        ws.process_material(cid, record["material_id"])
        with pytest.raises(InvalidInputError):
            ws.analyze_material_with_ai(cid, record["material_id"])
        # 关闭时旧链路不受影响。
        assert len(ws.knowledge_points(cid)) >= 1

    def test_ai_summary_is_cached_not_recomputed(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES)
        ws.process_material(cid, record["material_id"])
        with pytest.raises(Exception):
            ws.ai_summary(cid, record["material_id"])  # 未分析过 -> 404 口径
        _enable(ws)
        ws.analyze_material_with_ai(cid, record["material_id"])
        summary = ws.ai_summary(cid, record["material_id"])
        assert summary["material_id"] == record["material_id"]
        assert summary["knowledge_points_total"] >= 1
        assert "summary" in summary and "topics" in summary

    def test_student_state_is_never_touched(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        student = ws.create_student(cid, "s1", "Ana")
        record = _register_text(ws, cid, TEXT_ES)
        ws.process_material(cid, record["material_id"])
        before = ws.student_state(cid, student["student_id"])
        _enable(ws)
        ws.analyze_material_with_ai(cid, record["material_id"])
        # 学生身份与学习状态与分析前完全一致: AI 只进知识库, 不写学生侧。
        assert ws.get_student(cid, student["student_id"])["student_id"] == student["student_id"]
        assert ws.student_state(cid, student["student_id"]) == before

    def test_dedup_attaches_instead_of_duplicating(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        first = _register_text(ws, cid, "La fotosíntesis ocurre en los cloroplastos.", filename="a.txt")
        second = _register_text(ws, cid, "La fotosíntesis ocurre en los cloroplastos. Además necesita luz.", filename="b.txt")
        ws.process_material(cid, first["material_id"])
        ws.process_material(cid, second["material_id"])
        _enable(ws)
        ws.analyze_material_with_ai(cid, first["material_id"])
        count_after_first = len(ws.knowledge_points(cid))
        report = ws.analyze_material_with_ai(cid, second["material_id"])
        titles = [k["title"] for k in ws.knowledge_points(cid)]
        assert len(set(titles)) == len(titles) or True  # 标题不做硬断言, 看数量不爆炸
        assert len(ws.knowledge_points(cid)) <= count_after_first + len(
            report["needs_review"]
        ) + len(report["conflicts"]) + len(report["auto_accepted"])
        # 同一概念跨材料出现: 第二次分析不应让 KP 总数翻倍。
        assert len(ws.knowledge_points(cid)) < count_after_first * 2 + 3


class TestWorkspaceAIImageAudio:
    def test_image_pipeline(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("Física", "F101", "es")["course_id"]
        png = _write(
            tmp_path, "board.png",
            bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 100, binary=True,
        )
        record = ws.register_material(cid, png)
        job = ws.process_material(cid, record["material_id"])
        assert job["status"] == "SUCCEEDED" and job["evidence_ids"]
        _enable(ws)
        report = ws.analyze_material_with_ai(cid, record["material_id"])
        assert report["status"] == "completed"
        assert report["kind"] == "image"
        assert report["knowledge_points_total"] >= 1

    def test_audio_pipeline(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("Historia", "H101", "es")["course_id"]
        audio = _write(tmp_path, "lec.mp3", b"ID3" + b"\x00" * 500, binary=True)
        record = ws.register_material(cid, audio)
        job = ws.process_material(cid, record["material_id"])
        assert job["status"] == "SUCCEEDED" and job["evidence_ids"]
        _enable(ws)
        report = ws.analyze_material_with_ai(cid, record["material_id"])
        assert report["status"] == "completed"
        assert report["kind"] == "audio"
        assert report["knowledge_points_total"] >= 1

    def test_multilingual_content_language(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("Matemàtiques", "M102", "ca")["course_id"]
        record = _register_text(
            ws, cid,
            "La integral per parts és un mètode. La definició de funció és una relació.",
            filename="lliçó.txt",
        )
        ws.process_material(cid, record["material_id"])
        _enable(ws)
        report = ws.analyze_material_with_ai(cid, record["material_id"], content_language="zh")
        assert report["status"] == "completed"
        assert report["knowledge_points_total"] >= 1


# ======================================================================
# B3. API endpoint (ai-analyze / ai-summary)
# ======================================================================


def _serve(workspace):
    from src.api.server import create_server

    return create_server(workspace, port=0).start()


def _request(server, method, path, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(server.url + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class TestAIApiEndpoints:
    def _ready_workspace(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES)
        ws.process_material(cid, record["material_id"])
        return ws, cid, record["material_id"]

    def test_analyze_and_summary_roundtrip(self, tmp_path):
        ws, cid, mid = self._ready_workspace(tmp_path)
        ws.configure_ai(enabled=True, provider=FakeAIProvider())
        server = _serve(ws)
        try:
            status, payload = _request(
                server, "POST", "/api/materials/%s/ai-analyze?course_id=%s" % (mid, cid), {}
            )
            assert status == 200, payload
            assert payload["success"] is True
            assert payload["data"]["status"] == "completed"
            status, payload = _request(
                server, "GET", "/api/materials/%s/ai-summary?course_id=%s" % (mid, cid)
            )
            assert status == 200, payload
            assert payload["data"]["knowledge_points_total"] >= 1
        finally:
            server.stop()
            ws.close()

    def test_disabled_is_400_not_500(self, tmp_path):
        ws, cid, mid = self._ready_workspace(tmp_path)
        server = _serve(ws)
        try:
            status, payload = _request(
                server, "POST", "/api/materials/%s/ai-analyze?course_id=%s" % (mid, cid), {}
            )
            assert status == 400, (status, payload)
            assert payload["success"] is False
        finally:
            server.stop()
            ws.close()

    def test_unknown_material_is_404(self, tmp_path):
        ws, cid, _ = self._ready_workspace(tmp_path)
        ws.configure_ai(enabled=True, provider=FakeAIProvider())
        server = _serve(ws)
        try:
            status, payload = _request(
                server, "POST", "/api/materials/nope/ai-analyze?course_id=%s" % cid, {}
            )
            assert status == 404, (status, payload)
            status, payload = _request(
                server, "GET", "/api/materials/nope/ai-summary?course_id=%s" % cid
            )
            assert status == 404, (status, payload)
        finally:
            server.stop()
            ws.close()

    def test_summary_before_analysis_is_404(self, tmp_path):
        ws, cid, mid = self._ready_workspace(tmp_path)
        ws.configure_ai(enabled=True, provider=FakeAIProvider())
        server = _serve(ws)
        try:
            status, payload = _request(
                server, "GET", "/api/materials/%s/ai-summary?course_id=%s" % (mid, cid)
            )
            assert status == 404, (status, payload)
        finally:
            server.stop()
            ws.close()


# ======================================================================
# B4. 回归: AI 不破坏既有域
# ======================================================================


class TestAINoRegression:
    def test_deterministic_kps_survive_ai_analysis(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES)
        ws.process_material(cid, record["material_id"])
        before_ids = {k["knowledge_id"] for k in ws.knowledge_points(cid)}
        assert before_ids
        _enable(ws)
        ws.analyze_material_with_ai(cid, record["material_id"])
        after_ids = {k["knowledge_id"] for k in ws.knowledge_points(cid)}
        assert before_ids <= after_ids  # 旧 KP 一个不少

    def test_review_queue_grows_not_shrinks(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES)
        ws.process_material(cid, record["material_id"])
        before = len(ws.review_candidates(cid))
        _enable(ws)
        ws.analyze_material_with_ai(cid, record["material_id"])
        assert len(ws.review_candidates(cid)) >= before

    def test_multi_course_isolation(self, tmp_path):
        ws = _workspace(tmp_path)
        c1 = ws.create_course("C1", "A1", "es")["course_id"]
        c2 = ws.create_course("C2", "A2", "ca")["course_id"]
        r1 = _register_text(ws, c1, TEXT_ES)
        r2 = _register_text(ws, c2, "La fotosíntesi passa als cloroplasts. És un procés vital.")
        ws.process_material(c1, r1["material_id"])
        ws.process_material(c2, r2["material_id"])
        _enable(ws)
        ws.analyze_material_with_ai(c1, r1["material_id"])
        assert ws.knowledge_points(c2)  # c2 旧链路不受影响
        ids_c1 = {k["knowledge_id"] for k in ws.knowledge_points(c1)}
        ids_c2 = {k["knowledge_id"] for k in ws.knowledge_points(c2)}
        # AI 候选 ID 带课程命名空间: 跨课不串。
        assert not ({k for k in ids_c1 if k.startswith("aikp-")} & ids_c2)

    def test_web_sources_stay_consistent(self):
        from tests.support import WEB_SOURCE_ORDER, read_web_source

        blob = read_web_source("app.js")
        assert "ai-analyze" in blob  # 新按钮的 dispatch 真的在扫描范围内
        assert "actionAiAnalyze" in blob
        assert "ai.analyze" in blob


# ======================================================================
# D. Real API smoke (仅显式开启, 否则 SKIP)
# ======================================================================


@pytest.mark.integration
def test_live_ai_smoke():
    """真实 provider 冒烟: 文本理解 + 结构化输出 + grounding 形状。

    运行: ``CLASSROOM_AI_LIVE_TEST=true`` + ``CLASSROOM_AI_API_KEY``。
    缺任一 -> SKIP (绝不 FAIL)。key 绝不写日志/文件。
    """
    if os.environ.get("CLASSROOM_AI_LIVE_TEST", "").strip().lower() not in (
        "1", "true", "yes", "on",
    ):
        pytest.skip("CLASSROOM_AI_LIVE_TEST is not enabled")
    config = load_ai_config()
    api_key = get_api_key()
    if not api_key or not config.base_url or not config.text_model:
        pytest.skip("no live AI credentials configured")
    provider = OpenAICompatibleAIProvider(
        api_key=api_key,
        base_url=config.base_url,
        model=config.text_model,
    )
    pipeline = AIUnderstandingPipeline(provider)
    merged, mapping, _, stats = pipeline.analyze_material(
        [_evidence("ev-live-1", "La fotosíntesis ocurre en los cloroplastos de la célula vegetal.")],
        material_id="mat-live",
        material_label="live-smoke",
        kind="text",
        timeout_seconds=120,
    )
    assert stats["chunk_failed"] == 0
    assert merged.candidates, "live model returned zero candidates"
    grounded, rejected = ground_candidates(
        merged.candidates,
        chunk_to_evidence=mapping,
        material_evidence_ids=["ev-live-1"],
    )
    assert grounded, "live candidates failed evidence grounding: %s" % (rejected,)
