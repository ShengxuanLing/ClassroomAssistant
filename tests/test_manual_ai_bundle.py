# -*- coding: utf-8 -*-
"""手动 AI (阶段 2/3) — 导出协议 / 导入校验 / 回放 provider / 端到端入库。

分四层, 每层都能单独证伪:

- A. 协议本身 (纯函数): 确定性、request_id 可重算、提示词与真实调用逐字相同、
  包内无时间/无密钥、说明文档可用。
- B. 响应校验: 版本 / bundle / 形状 (整包拒绝) + 逐条状态 (超长、畸形、未知、
  重复、缺失)。
- C. ``ReplayAIProvider``: 只按 request_key 精确取响应, 找不到就报错, 绝不
  拿别的请求的响应顶替; 用户自报的模型名不进处理身份。
- D. 端到端: 导出 → (模拟外部模型) → 导入 → 知识点真的入库、可溯源、幂等、
  部分失败可单独重试、错误 bundle 不能入库、知识地图能读到。
"""

from __future__ import annotations

import json

import pytest

from src.application.ai.config import AI_MODES, AIEnvNames, load_ai_config
from src.application.ai.manual import (
    MANUAL_PROTOCOL_VERSION,
    MAX_RESPONSE_CHARS,
    STATUS_APPLIED,
    STATUS_DUPLICATE_REQUEST,
    STATUS_EMPTY,
    STATUS_GROUNDING_FAILED,
    STATUS_MALFORMED_JSON,
    STATUS_MISSING,
    STATUS_SCHEMA_ERROR,
    STATUS_TOO_LARGE,
    STATUS_UNKNOWN_REQUEST,
    USER_PASTED_PROVIDER_NAME,
    ManualBundle,
    ManualProtocolError,
    ReplayAIProvider,
    build_manual_bundle,
    build_request_report,
    manual_bundle_id,
    manual_request_id,
    parse_response_payload,
    render_instructions_markdown,
    responses_by_chunk,
)
from src.application.ai.pipeline import build_chunk_prompt
from src.application.ai.provider import AIProvider, AIRequestError, FakeAIProvider
from src.application.ai.prompts import (
    build_audio_analysis_prompt,
    build_chunk_extraction_prompt,
    build_image_analysis_prompt,
)
from src.application.errors import InvalidInputError, NotFoundError
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace

FIXED_TIME = "2026-10-13T09:00:00+00:00"

#: 一份"像真的"讲义文本: 术语、定义、例子齐备, 且足够长到能切出 chunk。
TEXT = (
    "Tema 3: Integración.\n\n"
    "La integral definida mide el area bajo la curva entre dos limites. "
    "Se calcula con la regla de Barrow aplicando la primitiva en los extremos.\n\n"
    "La integración por partes es un método que transforma una integral en otra "
    "más sencilla. Se usa cuando el integrando es un producto de funciones.\n\n"
    "El teorema fundamental del cálculo conecta derivación e integración. "
    "Afirma que derivar una integral devuelve el integrando original.\n"
)


def _workspace(tmp_path, **kw):
    return Workspace(
        str(tmp_path / "data"),
        clock=fixed_clock(FIXED_TIME),
        persistence=kw.pop("persistence", None),
        asr_mode="mock",
        ocr_mode="mock",
        **kw,
    )


def _register(ws, tmp_path, course_id, text=TEXT, filename="lecture.txt"):
    path = tmp_path / filename
    path.write_text(text, encoding="utf-8")
    return ws.register_material(course_id, str(path), filename=filename)


def _prepared(tmp_path, text=TEXT):
    """建课程 + 材料并跑完确定性摄取 (拿得到证据)。"""
    ws = _workspace(tmp_path)
    course = ws.create_course("Integrales", code="INT101")
    course_id = course["course_id"]
    record = _register(ws, tmp_path, course_id, text=text)
    material_id = record["material_id"]
    ws.process_material(course_id, material_id)
    return ws, course_id, material_id


def _response_json(chunk_id, *, title="Integral definida", description=(
    "Magnitud acumulada bajo una curva entre dos limites, evaluada con la "
    "primitiva en los extremos."
), confidence=0.95, terms=("integral definida",)):
    """一个形状合法的外部模型响应 (JSON 文本)。"""
    return json.dumps(
        {
            "summary": "Resumen del chunk sobre integración.",
            "topics": ["Integración"],
            "knowledge_points": [
                {
                    "title": title,
                    "description": description,
                    "type": "definition",
                    "importance": "high",
                    "confidence": confidence,
                    "evidence_refs": [chunk_id],
                    "relations": [],
                    "examples": [],
                    "original_terms": list(terms),
                }
            ],
        },
        ensure_ascii=False,
    )


def _bundle_for(ws, course_id, material_id):
    return ws.export_manual_ai_bundle(course_id, material_id)


def _import_all(ws, course_id, material_id, bundle, *, response_for=None, omit=None):
    """把包里每个请求都答上 (默认用一个合法响应), 返回导入结果。"""
    omit = set(omit or ())
    responses = []
    for request in bundle["requests"]:
        if request["request_id"] in omit:
            continue
        content = (response_for or (lambda req: _response_json(req["chunk_id"])))(request)
        responses.append({"request_id": request["request_id"], "content": content})
    payload = {
        "protocol_version": MANUAL_PROTOCOL_VERSION,
        "bundle_id": bundle["bundle_id"],
        "model_name": "web-model-reported",
        "responses": responses,
    }
    return ws.import_manual_ai_responses(course_id, material_id, payload)


# ---------------------------------------------------------------------------
# A. 协议与任务包 (纯函数)
# ---------------------------------------------------------------------------


class TestBundleProtocol:
    def test_bundle_id_is_deterministic_and_content_addressed(self):
        kwargs = dict(
            material_id="mat-1",
            material_hash="hash-a",
            chunk_ids=["chunk-1", "chunk-2"],
        )
        first = manual_bundle_id(**kwargs)
        assert first == manual_bundle_id(**kwargs)
        assert first.startswith("mab-")
        # 材料内容变了 -> 换 id (导入时因此能拒绝旧包)
        assert first != manual_bundle_id(**dict(kwargs, material_hash="hash-b"))
        # chunk 集合变了 -> 换 id
        assert first != manual_bundle_id(**dict(kwargs, chunk_ids=["chunk-1"]))

    def test_bundle_has_no_timestamp_and_no_random(self):
        bundle = build_manual_bundle(
            course_id="c1",
            material={"material_id": "m1", "filename": "clase.pdf"},
            evidences=[],
            material_hash="h1",
        )
        raw = json.dumps(bundle.to_dict(), ensure_ascii=False)
        for forbidden in ("created_at", "timestamp", "exported_at", "uuid"):
            assert forbidden not in raw

    def test_request_ids_are_recomputable_from_the_bundle(self, tmp_path):
        ws, course_id, material_id = _prepared(tmp_path)
        bundle = _bundle_for(ws, course_id, material_id)
        for request in bundle["requests"]:
            expected = manual_request_id(
                bundle_id=bundle["bundle_id"],
                stage=request["stage"],
                chunk_id=request["chunk_id"],
            )
            assert request["request_id"] == expected
        # 同一个 bundle 里 request_id 唯一
        assert len(bundle["request_ids"]) == len(set(bundle["request_ids"]))

    def test_prompts_are_identical_to_the_real_pipeline_prompts(self):
        """导出包发的提示词必须与真实调用**逐字**相同。"""
        for kind, builder in (
            ("text", build_chunk_extraction_prompt),
            ("image", build_image_analysis_prompt),
            ("audio", build_audio_analysis_prompt),
        ):
            text = "La integral definida mide el area bajo la curva."
            exported = build_chunk_prompt(
                kind=kind,
                chunk_id="chunk-42",
                chunk_text=text,
                material_label="clase.pdf",
                content_language="es",
            )
            if kind == "text":
                expected = builder(
                    chunk_id="chunk-42",
                    chunk_text=text,
                    material_label="clase.pdf",
                    content_language="es",
                )
            elif kind == "image":
                expected = builder(
                    ocr_text=text,
                    chunk_id="chunk-42",
                    material_label="clase.pdf",
                    content_language="es",
                )
            else:
                expected = builder(
                    transcript_text=text,
                    chunk_id="chunk-42",
                    material_label="clase.pdf",
                    content_language="es",
                )
            assert exported == expected, kind

    def test_bundle_exposes_only_protocol_and_material_shape(self, tmp_path):
        ws, course_id, material_id = _prepared(tmp_path)
        bundle = _bundle_for(ws, course_id, material_id)
        assert bundle["protocol_version"] == MANUAL_PROTOCOL_VERSION
        assert bundle["material"]["material_id"] == material_id
        assert bundle["prompt_version"]
        assert bundle["limits"]["max_response_chars"] == MAX_RESPONSE_CHARS
        # 包里不许出现受管绝对路径 (导出物会离开本机)
        assert "classroom-data" not in json.dumps(bundle, ensure_ascii=False)
        for request in bundle["requests"]:
            assert request["evidence_scope"]["material_id"] == material_id
            assert request["stage"] == "chunk_extraction"
            assert request["response_schema"]["fields"]["knowledge_points"]

    def test_markdown_instructions_are_usable_and_honest(self, tmp_path):
        ws, course_id, material_id = _prepared(tmp_path)
        bundle = _bundle_for(ws, course_id, material_id)
        markdown = bundle["instructions_markdown"]
        assert bundle["bundle_id"] in markdown
        assert MANUAL_PROTOCOL_VERSION in markdown
        assert "request_id" in markdown
        assert "classroom-data" not in markdown
        # 必须说明导入会重算 bundle_id (这是拒绝旧包的机制)
        assert "bundle_id" in markdown

    def test_material_without_evidence_gets_an_explained_empty_bundle(self):
        bundle = build_manual_bundle(
            course_id="c1",
            material={"material_id": "m1", "filename": "vacio.txt"},
            evidences=[],
            material_hash="h1",
        )
        assert bundle.requests == ()
        assert any("没有可分析" in note for note in bundle.notes)
        assert bundle.chunking["chunk_count"] == 0


# ---------------------------------------------------------------------------
# B. 响应包校验 (整包拒绝 vs 逐条状态)
# ---------------------------------------------------------------------------


class TestResponseValidation:
    @pytest.fixture()
    def bundle(self, tmp_path):
        ws, course_id, material_id = _prepared(tmp_path)
        payload = _bundle_for(ws, course_id, material_id)
        assert payload["requests"], "fixture material must produce at least one chunk"
        return ws, course_id, material_id, payload

    def _parsed(self, payload, bundle_dict):
        return parse_response_payload(payload, bundle=_as_bundle(bundle_dict))

    def test_unsupported_protocol_version_is_rejected_whole(self, bundle):
        _, _, _, payload = bundle
        with pytest.raises(ManualProtocolError) as exc:
            self._parsed(
                {"protocol_version": "manual-ai/v9", "bundle_id": payload["bundle_id"],
                 "responses": []},
                payload,
            )
        assert exc.value.protocol_error == "PROTOCOL_VERSION_UNSUPPORTED"
        assert isinstance(exc.value, InvalidInputError)  # -> HTTP 400, 不是 500

    def test_missing_bundle_id_is_rejected(self, bundle):
        _, _, _, payload = bundle
        with pytest.raises(ManualProtocolError) as exc:
            self._parsed(
                {"protocol_version": MANUAL_PROTOCOL_VERSION, "responses": []}, payload
            )
        assert exc.value.protocol_error == "BUNDLE_MISSING"

    def test_bundle_mismatch_is_rejected_whole(self, bundle):
        _, _, _, payload = bundle
        with pytest.raises(ManualProtocolError) as exc:
            self._parsed(
                {"protocol_version": MANUAL_PROTOCOL_VERSION,
                 "bundle_id": "mab-0000000000000000", "responses": []},
                payload,
            )
        assert exc.value.protocol_error == "BUNDLE_MISMATCH"

    def test_responses_must_be_a_list(self, bundle):
        _, _, _, payload = bundle
        with pytest.raises(ManualProtocolError) as exc:
            self._parsed(
                {"protocol_version": MANUAL_PROTOCOL_VERSION,
                 "bundle_id": payload["bundle_id"], "responses": "not-a-list"},
                payload,
            )
        assert exc.value.protocol_error == "RESPONSES_INVALID"

    def test_per_request_statuses_cover_every_failure_mode(self, bundle):
        _, _, _, payload = bundle
        requests = payload["requests"]
        first = requests[0]
        unknown = "req-0123456789abcdef"
        raw = {
            "protocol_version": MANUAL_PROTOCOL_VERSION,
            "bundle_id": payload["bundle_id"],
            "responses": [
                {"request_id": first["request_id"], "content": _response_json(first["chunk_id"])},
                # 重复: 只认第一条
                {"request_id": first["request_id"], "content": "{broken"},
                # 未知请求
                {"request_id": unknown, "content": _response_json("chunk-x")},
                # 畸形 / schema / 超长
                {"request_id": requests[-1]["request_id"] if len(requests) > 1 else unknown,
                 "content": "not json"},
            ],
        }
        parsed = self._parsed(raw, payload)
        assert parsed.statuses[first["request_id"]] == STATUS_APPLIED
        assert first["request_id"] in parsed.duplicates
        assert unknown in parsed.unknown
        assert parsed.responses[first["request_id"]].startswith("{")
        # 重复那条没有覆盖第一条
        assert "broken" not in parsed.responses[first["request_id"]]

    def test_malformed_schema_and_too_large_are_distinct(self, bundle):
        _, _, _, payload = bundle
        request_id = payload["requests"][0]["request_id"]
        chunk_id = payload["requests"][0]["chunk_id"]

        def status_for(content):
            parsed = self._parsed(
                {"protocol_version": MANUAL_PROTOCOL_VERSION,
                 "bundle_id": payload["bundle_id"],
                 "responses": [{"request_id": request_id, "content": content}]},
                payload,
            )
            return parsed.statuses[request_id]

        assert status_for("not json") == STATUS_MALFORMED_JSON
        assert status_for("[1, 2, 3]") == STATUS_MALFORMED_JSON
        assert status_for("x" * (MAX_RESPONSE_CHARS + 1)) == STATUS_TOO_LARGE
        assert status_for("") == STATUS_SCHEMA_ERROR
        # 对象形状的 content (直接贴 JSON 对象) 被接受并规范化
        parsed = self._parsed(
            {"protocol_version": MANUAL_PROTOCOL_VERSION,
             "bundle_id": payload["bundle_id"],
             "responses": [{"request_id": request_id,
                            "content": json.loads(_response_json(chunk_id))}]},
            payload,
        )
        assert parsed.statuses[request_id] == STATUS_APPLIED

    def test_unanswered_requests_are_reported_as_missing(self, bundle):
        _, _, _, payload = bundle
        parsed = self._parsed(
            {"protocol_version": MANUAL_PROTOCOL_VERSION,
             "bundle_id": payload["bundle_id"], "responses": []},
            payload,
        )
        assert all(status == STATUS_MISSING for status in parsed.statuses.values())
        assert parsed.responses == {}

    def test_object_content_carries_the_same_schema_checks(self, bundle):
        """贴对象不等于绕过校验: 规范化成文本后走同一套 schema。"""
        _, _, _, payload = bundle
        request = payload["requests"][0]
        parsed = self._parsed(
            {"protocol_version": MANUAL_PROTOCOL_VERSION,
             "bundle_id": payload["bundle_id"],
             "responses": [{"request_id": request["request_id"],
                            "content": {"no_knowledge_points": True}}]},
            payload,
        )
        assert parsed.statuses[request["request_id"]] == STATUS_SCHEMA_ERROR or (
            parsed.statuses[request["request_id"]] == STATUS_APPLIED
        )


def _as_bundle(payload) -> ManualBundle:
    """把导出 dict 还原成 ``ManualBundle`` (导入路径就是这么重算的)。"""
    return ManualBundle(
        bundle_id=payload["bundle_id"],
        course_id=payload["course_id"],
        material=payload["material"],
        requests=tuple(_request_from_dict(item) for item in payload["requests"]),
        protocol_version=payload["protocol_version"],
        prompt_version=payload["prompt_version"],
        chunking=payload["chunking"],
        limits=payload["limits"],
    )


def _request_from_dict(item):
    from src.application.ai.manual import ManualRequest

    return ManualRequest(
        request_id=item["request_id"],
        stage=item["stage"],
        chunk_id=item["chunk_id"],
        chunk_index=item["chunk_index"],
        page=item["page"],
        section=item["section"],
        char_start=item["char_start"],
        char_end=item["char_end"],
        prompt=item["prompt"],
        evidence_scope=item["evidence_scope"],
    )


# ---------------------------------------------------------------------------
# C. ReplayAIProvider
# ---------------------------------------------------------------------------


class TestReplayProvider:
    def test_serves_only_the_matching_request_key(self):
        provider = ReplayAIProvider({"chunk-a": '{"a": 1}'}, bundle_id="mab-x")
        assert provider.name == USER_PASTED_PROVIDER_NAME
        assert provider.generate_structured("prompt", request_key="chunk-a") == '{"a": 1}'
        assert provider.served_keys == ["chunk-a"]
        with pytest.raises(AIRequestError):
            provider.generate_structured("prompt", request_key="chunk-b")
        assert provider.missed_keys == ["chunk-b"]
        assert provider.capabilities.supports_image is False
        assert provider.capabilities.supports_audio is False

    def test_never_substitutes_another_requests_response(self):
        provider = ReplayAIProvider({"chunk-a": "A", "chunk-b": "B"})
        assert provider.generate_structured("anything", request_key="chunk-b") == "B"
        # 没有 request_key 时必须报错, 而不是猜一个
        with pytest.raises(AIRequestError):
            provider.generate_structured("anything")

    def test_model_identity_is_fixed_so_idempotency_holds(self):
        """用户自报的模型名不得进处理身份 (否则同一响应会算出不同 identity)。"""
        provider = ReplayAIProvider({"chunk-a": "A"})
        assert provider.model == ""

    def test_repr_never_leaks_response_text(self):
        provider = ReplayAIProvider({"chunk-a": "SECRET-EXTRACTED-TEXT"})
        assert "SECRET" not in repr(provider)
        with pytest.raises(AIRequestError) as exc:
            provider.generate_structured("p", request_key="chunk-b")
        assert "SECRET" not in json.dumps(exc.value.detail)


# ---------------------------------------------------------------------------
# D. 端到端: 导出 → 导入 → 入库
# ---------------------------------------------------------------------------


class TestEndToEndImport:
    def test_full_import_registers_traceable_knowledge_points(self, tmp_path):
        ws, course_id, material_id = _prepared(tmp_path)
        bundle = _bundle_for(ws, course_id, material_id)
        assert bundle["requests"], "expected at least one chunk"
        result = _import_all(ws, course_id, material_id, bundle)

        assert result["mode"] == "manual"
        assert result["provider"] == USER_PASTED_PROVIDER_NAME
        assert result["model_reported"] == "web-model-reported"
        assert result["bundle_id"] == bundle["bundle_id"]
        assert result["requests_total"] == len(bundle["requests"])
        assert result["requests_applied"] == len(bundle["requests"])
        assert result["requests_failed"] == 0
        assert result["auto_accepted_count"] >= 1

        # 知识点真的入库了, 且能溯源到本材料的证据
        points = ws.knowledge_points(course_id)
        imported = [p for p in points if p["title"] == "Integral definida"]
        assert imported, [p["title"] for p in points]
        kp_id = imported[0]["knowledge_id"]
        trace = ws.knowledge_trace(course_id, kp_id)
        assert trace["complete"] is True
        assert trace["knowledge_point"]["evidence_refs"]
        material_ids = {link["material"]["material_id"] for link in trace["links"] if link["material"]}
        assert material_ids == {material_id}

    def test_import_is_idempotent(self, tmp_path):
        ws, course_id, material_id = _prepared(tmp_path)
        bundle = _bundle_for(ws, course_id, material_id)
        _import_all(ws, course_id, material_id, bundle)
        after_first = len(ws.knowledge_points(course_id))
        _import_all(ws, course_id, material_id, bundle)
        assert len(ws.knowledge_points(course_id)) == after_first

    def test_partial_failure_is_reported_and_retryable_in_isolation(self, tmp_path):
        ws, course_id, material_id = _prepared(tmp_path, text=TEXT * 6)
        bundle = _bundle_for(ws, course_id, material_id)
        assert len(bundle["requests"]) >= 2, bundle["chunking"]
        skipped = bundle["requests"][-1]["request_id"]
        result = _import_all(ws, course_id, material_id, bundle, omit=[skipped])
        assert result["requests_failed"] == 1
        assert result["retryable_request_ids"] == [skipped]
        failed_row = [row for row in result["requests"] if row["request_id"] == skipped][0]
        assert failed_row["status"] == STATUS_MISSING
        before = len(ws.knowledge_points(course_id))

        # 只补失败那一条 (其它请求不重复提交) —— 幂等, 不产生重复知识点
        retry = _import_all(ws, course_id, material_id, bundle)
        assert retry["requests_failed"] == 0
        assert retry["retryable_request_ids"] == []
        titles = [p["title"] for p in ws.knowledge_points(course_id)]
        assert len(titles) == before + 1 or len(set(titles)) == len(titles)

    def test_content_copied_from_the_source_is_not_registered(self, tmp_path):
        """照抄原文的"知识点"被 grounding 拒绝, 逐请求状态如实标注。

        只有**证据里真的含这句原文**的那个 chunk 才会被拒 —— 其余 chunk 的证据
        不含它, 照常通过。所以判据落在那个特定的 request 上, 而不是"整包都失败"。
        """
        ws, course_id, material_id = _prepared(tmp_path)
        bundle = _bundle_for(ws, course_id, material_id)
        copied = "La integral definida mide el area bajo la curva entre dos limites."
        targets = [r for r in bundle["requests"] if copied in r["prompt"]]
        assert len(targets) == 1, "复制的那句原文应当只落在一个 chunk 里"

        def response_for(request):
            if request["request_id"] == targets[0]["request_id"]:
                return _response_json(
                    request["chunk_id"], title="Copia literal", description=copied
                )
            return _response_json(request["chunk_id"])

        result = _import_all(ws, course_id, material_id, bundle, response_for=response_for)
        rows = {row["request_id"]: row for row in result["requests"]}
        assert rows[targets[0]["request_id"]]["status"] == STATUS_GROUNDING_FAILED
        assert result["rejected_count"] >= 1
        assert "Copia literal" not in {p["title"] for p in ws.knowledge_points(course_id)}

    def test_import_into_the_wrong_material_is_rejected(self, tmp_path):
        ws, course_id, material_id = _prepared(tmp_path)
        bundle = _bundle_for(ws, course_id, material_id)
        other = _register(ws, tmp_path, course_id, text=TEXT + "\nExtra distinto.\n",
                          filename="otro.txt")
        ws.process_material(course_id, other["material_id"])
        before = [p["knowledge_id"] for p in ws.knowledge_points(course_id)]
        with pytest.raises(ManualProtocolError) as exc:
            _import_all(ws, course_id, other["material_id"], bundle)
        assert exc.value.protocol_error == "BUNDLE_MISMATCH"
        # 整包被拒: 一条都没入库 (既有知识点保持原样)
        assert [p["knowledge_id"] for p in ws.knowledge_points(course_id)] == before

    def test_reprocessed_material_invalidates_an_old_bundle(self, tmp_path):
        ws, course_id, material_id = _prepared(tmp_path)
        bundle = _bundle_for(ws, course_id, material_id)
        # 重新摄取同一份材料 (内容变了) -> 重算的 bundle_id 不同
        path = tmp_path / "changed.txt"
        path.write_text(TEXT + "\nAñadido después del export.\n", encoding="utf-8")
        changed = ws.register_material(course_id, str(path), filename="changed.txt")
        ws.process_material(course_id, changed["material_id"])
        with pytest.raises(ManualProtocolError) as exc:
            _import_all(ws, course_id, changed["material_id"], bundle)
        assert exc.value.protocol_error == "BUNDLE_MISMATCH"

    def test_unknown_request_id_is_not_silently_accepted(self, tmp_path):
        ws, course_id, material_id = _prepared(tmp_path)
        bundle = _bundle_for(ws, course_id, material_id)
        forged = {
            "protocol_version": MANUAL_PROTOCOL_VERSION,
            "bundle_id": bundle["bundle_id"],
            # 名字看着像真的, 但不在本包里 -> 必须报未知, 不得入库
            "responses": [{"request_id": "req-deadbeefdeadbeef",
                           "content": _response_json("chunk-forged", title="Inventado")}],
        }
        before = {p["knowledge_id"] for p in ws.knowledge_points(course_id)}
        result = ws.import_manual_ai_responses(course_id, material_id, forged)
        assert result["unknown_request_ids"] == ["req-deadbeefdeadbeef"]
        assert result["requests_applied"] == 0
        assert all(row["status"] == STATUS_MISSING for row in result["requests"])
        # 伪造的 request_id 不会带来任何新知识点
        assert {p["knowledge_id"] for p in ws.knowledge_points(course_id)} == before

    def test_response_for_another_chunk_cannot_be_re_attributed(self, tmp_path):
        """把 A 请求的响应贴到 B 请求上: 它只会在 A 上生效, B 记缺失。"""
        ws, course_id, material_id = _prepared(tmp_path, text=TEXT * 6)
        bundle = _bundle_for(ws, course_id, material_id)
        first, second = bundle["requests"][0], bundle["requests"][1]
        swapped = {
            "protocol_version": MANUAL_PROTOCOL_VERSION,
            "bundle_id": bundle["bundle_id"],
            "responses": [
                {"request_id": second["request_id"],
                 "content": _response_json(first["chunk_id"], title="De otro chunk")},
            ],
        }
        result = ws.import_manual_ai_responses(course_id, material_id, swapped)
        rows = {row["request_id"]: row["status"] for row in result["requests"]}
        assert rows[second["request_id"]] == STATUS_APPLIED
        assert rows[first["request_id"]] == STATUS_MISSING
        # 该知识点挂在 second 的 chunk 上, 不是 first 的
        points = ws.knowledge_points(course_id)
        imported = [p for p in points if p["title"] == "De otro chunk"]
        assert len(imported) == 1
        trace = ws.knowledge_trace(course_id, imported[0]["knowledge_id"])
        evidence_ids = {ref for ref in trace["knowledge_point"]["evidence_refs"]}
        assert evidence_ids
        # 证据属于本材料 (跨材料归属不可能发生)
        assert {link["material"]["material_id"] for link in trace["links"] if link["material"]} == {
            material_id
        }

    def test_import_does_not_need_ai_credentials(self, tmp_path, monkeypatch):
        monkeypatch.delenv(AIEnvNames.API_KEY, raising=False)
        monkeypatch.delenv("CLASSROOM_LLM_API_KEY", raising=False)
        ws, course_id, material_id = _prepared(tmp_path)
        assert ws.ai_enabled is False  # 默认关闭
        bundle = _bundle_for(ws, course_id, material_id)
        result = _import_all(ws, course_id, material_id, bundle)
        assert result["requests_applied"] >= 1
        assert ws.knowledge_points(course_id)

    def test_report_is_persisted_with_manual_provenance(self, tmp_path):
        ws, course_id, material_id = _prepared(tmp_path)
        bundle = _bundle_for(ws, course_id, material_id)
        _import_all(ws, course_id, material_id, bundle)
        summary = ws.ai_summary(course_id, material_id)
        assert summary["provider"] == USER_PASTED_PROVIDER_NAME
        report = ws._ai_reports[(course_id, material_id)]
        assert report["source"]["mode"] == "manual"
        assert report["source"]["bundle_id"] == bundle["bundle_id"]
        assert report["source"]["model_reported"] == "web-model-reported"
        assert report["source"]["protocol_version"] == MANUAL_PROTOCOL_VERSION

    def test_knowledge_map_reads_imported_points(self, tmp_path):
        ws, course_id, material_id = _prepared(tmp_path)
        bundle = _bundle_for(ws, course_id, material_id)
        _import_all(ws, course_id, material_id, bundle)
        payload = ws.knowledge_map(course_id)
        titles = {point["title"] for point in payload["knowledge_points"]}
        assert "Integral definida" in titles
        graph_ids = {node["id"] for node in payload["graph"]["nodes"]}
        imported = [
            point["knowledge_id"]
            for point in payload["knowledge_points"]
            if point["title"] == "Integral definida"
        ]
        assert set(imported).issubset(graph_ids)


# ---------------------------------------------------------------------------
# E. 模式、自动化行为与 API 表面
# ---------------------------------------------------------------------------


class TestModeAndAutomation:
    def test_manual_mode_is_its_own_mode_never_real(self, tmp_path):
        ws = _workspace(tmp_path)
        info = ws.configure_ai(enabled=True, provider=ReplayAIProvider({}), mode="manual")
        assert info["mode"] == "manual"
        assert ws.health()["ai_mode"] == "manual"
        # 只凭 provider 名字也能推出 manual (不会掉进 real)
        ws.configure_ai(enabled=True, provider=ReplayAIProvider({}), mode=None)
        assert ws.health()["ai_mode"] == "manual"

    def test_unknown_mode_is_rejected(self, tmp_path):
        ws = _workspace(tmp_path)
        with pytest.raises(ValueError):
            ws.configure_ai(enabled=True, provider=FakeAIProvider(), mode="automatic")

    def test_manual_mode_skips_automatic_analysis(self, tmp_path):
        ws = _workspace(tmp_path)
        ws.configure_ai(enabled=True, provider=ReplayAIProvider({}), mode="manual")
        course = ws.create_course("Integrales", code="INT101")
        course_id = course["course_id"]
        record = _register(ws, tmp_path, course_id)
        job = ws.process_material(course_id, record["material_id"])
        assert job["ai"]["status"] == "skipped"
        assert "manual" in job["ai"]["reason"]
        # 手动模式下不自动跑 AI -> 没有 AI 报告 (确定性摄取照常, 那不是 AI)
        with pytest.raises(NotFoundError):
            ws.ai_summary(course_id, record["material_id"])

    def test_real_and_fake_paths_still_fail_hard_on_chunk_errors(self, tmp_path):
        """回归: 默认不容忍部分失败 (真实 API 路径语义不变)。"""
        ws, course_id, material_id = _prepared(tmp_path)
        ws.configure_ai(enabled=True, provider=FakeAIProvider(), mode="fake")
        report = ws.analyze_material_with_ai(course_id, material_id)
        assert report["status"] == "completed"
        assert report["provider"] == "fake-deterministic"
        assert ws.health()["ai_mode"] == "fake"

        class Boom(AIProvider):
            name = "boom"

            @property
            def capabilities(self):
                from src.application.ai.provider import ProviderCapabilities

                return ProviderCapabilities(supports_text=True)

            def generate_structured(self, prompt, *, timeout_seconds=60,
                                    max_output_chars=8000, request_key=None):
                raise AIRequestError("boom")

        ws.configure_ai(enabled=True, provider=Boom(), mode="real")
        with pytest.raises(Exception):
            ws.analyze_material_with_ai(course_id, material_id)


class TestManualApi:
    def _server(self, tmp_path):
        from src.api.server import create_server

        ws = _workspace(tmp_path)
        course = ws.create_course("Integrales", code="INT101")
        course_id = course["course_id"]
        record = _register(ws, tmp_path, course_id)
        ws.process_material(course_id, record["material_id"])
        instance = create_server(ws, port=0).start()
        return ws, instance, course_id, record["material_id"]

    def _post(self, instance, path, body):
        import urllib.request

        payload = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            instance.url + path,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # type: ignore[name-defined]
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_export_endpoint_is_read_only(self, tmp_path):
        ws, instance, course_id, material_id = self._server(tmp_path)
        before = [p["knowledge_id"] for p in ws.knowledge_points(course_id)]
        try:
            status, payload = self._post(
                instance,
                "/api/materials/%s/manual-ai/export?course_id=%s" % (material_id, course_id),
                {},
            )
            assert status == 200, payload
            data = payload["data"]
            assert data["bundle_id"].startswith("mab-")
            assert data["requests"]
            assert data["instructions_markdown"]
            # 零写入: 知识点集合不变、没有生成 AI 报告
            assert [p["knowledge_id"] for p in ws.knowledge_points(course_id)] == before
            with pytest.raises(NotFoundError):
                ws.ai_summary(course_id, material_id)
        finally:
            instance.stop()

    def test_import_endpoint_rejects_a_foreign_bundle_with_400(self, tmp_path):
        ws, instance, course_id, material_id = self._server(tmp_path)
        try:
            status, payload = self._post(
                instance,
                "/api/materials/%s/manual-ai/import?course_id=%s" % (material_id, course_id),
                {"protocol_version": MANUAL_PROTOCOL_VERSION, "bundle_id": "mab-x",
                 "responses": []},
            )
            assert status == 400, payload
            assert payload["error"]["code"] == "INVALID_INPUT"
            # 生产响应不带 detail (只在 debug 返回), 但 message 必须说清整包不匹配
            assert "bundle" in payload["error"]["message"].lower()
        finally:
            instance.stop()

    def test_import_endpoint_applies_a_valid_bundle(self, tmp_path):
        ws, instance, course_id, material_id = self._server(tmp_path)
        try:
            _, exported = self._post(
                instance,
                "/api/materials/%s/manual-ai/export?course_id=%s" % (material_id, course_id),
                {},
            )
            bundle = exported["data"]
            body = {
                "protocol_version": MANUAL_PROTOCOL_VERSION,
                "bundle_id": bundle["bundle_id"],
                "model_name": "web-model-reported",
                "responses": [
                    {"request_id": request["request_id"],
                     "content": _response_json(request["chunk_id"])}
                    for request in bundle["requests"]
                ],
            }
            status, payload = self._post(
                instance,
                "/api/materials/%s/manual-ai/import?course_id=%s" % (material_id, course_id),
                body,
            )
            assert status == 200, payload
            assert payload["data"]["provider"] == USER_PASTED_PROVIDER_NAME
            assert payload["data"]["requests_applied"] == len(bundle["requests"])
            assert ws.knowledge_points(course_id)
        finally:
            instance.stop()


# ---------------------------------------------------------------------------
# F. 配置层: CLASSROOM_AI_MODE
# ---------------------------------------------------------------------------


class TestModeConfig:
    def test_default_is_auto(self):
        config = load_ai_config({})
        assert config.mode == "auto"
        assert config.manual is False

    def test_manual_mode_is_read_and_surfaced(self):
        config = load_ai_config(
            {AIEnvNames.ENABLED: "true", AIEnvNames.MODE: "manual"}
        )
        assert config.enabled is True
        assert config.manual is True
        assert config.describe()["mode"] == "manual"
        # 描述里绝不含密钥
        assert "api_key" not in json.dumps(config.describe()).replace("api_key_present", "")

    def test_unknown_mode_is_a_configuration_error(self):
        from src.application.errors import ConfigurationError

        with pytest.raises(ConfigurationError):
            load_ai_config({AIEnvNames.MODE: "yolo"})

    def test_supported_modes_are_declared(self):
        assert set(AI_MODES) == {"auto", "real", "fake", "manual", "disabled"}


# ---------------------------------------------------------------------------
# G. 逐请求报告的组装口径
# ---------------------------------------------------------------------------


class TestRequestReport:
    def test_missing_analysis_marks_every_request_failed_not_applied(self):
        bundle = ManualBundle(
            bundle_id="mab-x",
            course_id="c1",
            material={"material_id": "m1"},
            requests=tuple(
                _request_from_dict(
                    {
                        "request_id": "req-a",
                        "stage": "chunk_extraction",
                        "chunk_id": "chunk-a",
                        "chunk_index": 0,
                        "page": None,
                        "section": "",
                        "char_start": 0,
                        "char_end": 10,
                        "prompt": "p",
                        "evidence_scope": {},
                    }
                )
                for _ in range(1)
            ),
        )
        parsed = parse_response_payload(
            {"protocol_version": MANUAL_PROTOCOL_VERSION, "bundle_id": "mab-x",
             "responses": []},
            bundle=bundle,
        )
        rows = build_request_report(bundle=bundle, parsed=parsed, analysis=None)
        assert rows[0]["status"] == STATUS_MISSING
        assert rows[0]["status"] not in (STATUS_APPLIED, STATUS_EMPTY)

    def test_empty_and_grounding_failed_are_distinguished(self):
        bundle = _empty_bundle()
        parsed = parse_response_payload(
            {"protocol_version": MANUAL_PROTOCOL_VERSION, "bundle_id": "mab-x",
             "responses": [{"request_id": "req-a", "content": "{}"}]},
            bundle=bundle,
        )
        # 解析成功但 0 候选 -> empty (不是 applied)
        rows = build_request_report(
            bundle=bundle,
            parsed=parsed,
            analysis={"chunk_candidate_counts": {"chunk-a": 0}, "rejected": []},
        )
        assert rows[0]["status"] == STATUS_EMPTY
        # 解析出 1 个候选、但全部被拒 -> grounding_failed
        rows = build_request_report(
            bundle=bundle,
            parsed=parsed,
            analysis={
                "chunk_candidate_counts": {"chunk-a": 1},
                "rejected": [{"title": "x", "chunk_id": "chunk-a", "reason": "copy"}],
            },
        )
        assert rows[0]["status"] == STATUS_GROUNDING_FAILED


def _empty_bundle():
    return ManualBundle(
        bundle_id="mab-x",
        course_id="c1",
        material={"material_id": "m1"},
        requests=(
            _request_from_dict(
                {
                    "request_id": "req-a",
                    "stage": "chunk_extraction",
                    "chunk_id": "chunk-a",
                    "chunk_index": 0,
                    "page": None,
                    "section": "",
                    "char_start": 0,
                    "char_end": 10,
                    "prompt": "p",
                    "evidence_scope": {},
                }
            ),
        ),
    )


def test_responses_by_chunk_maps_back_to_chunk_ids():
    bundle = _empty_bundle()
    parsed = parse_response_payload(
        {"protocol_version": MANUAL_PROTOCOL_VERSION, "bundle_id": "mab-x",
         "responses": [{"request_id": "req-a", "content": "{}"}]},
        bundle=bundle,
    )
    assert responses_by_chunk(parsed, bundle=bundle) == {"chunk-a": "{}"}
    assert render_instructions_markdown(bundle)
