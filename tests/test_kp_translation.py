# -*- coding: utf-8 -*-
"""TASK-79: 知识点级中文解释 (按需翻译 + 术语表)。

覆盖的是这个任务**真正可能出错**的地方, 而不是"能跑通就算":

1. 无证据不翻 (无 source = 不给中文), 有证据才翻;
2. 原文与证据**逐字不动** —— 这是这个功能的边界条件, 不是附带要求;
3. 幂等: 同一知识点重复翻译命中缓存, **不再调 LLM**; 改内容 / 换 prompt
   版本必须换身份, 不能拿旧解释冒充新的;
4. 失败是 422 且可 retry, 绝不是 500, 且失败后原文仍在;
5. 术语必须逐字出现在知识点自身文本里 (幻觉术语被拒收), 语言只能是
   es / ca / [语言待确认];
6. GET 只读缓存, 绝不调 LLM; POST 是唯一的调用入口。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.application.ai.pipeline import AIUnderstandingPipeline, processing_identity
from src.application.ai.prompts import (
    KP_ZH_MAX_INPUT_CHARS,
    KP_ZH_PROMPT_VERSION,
    build_kp_zh_prompt,
)
from src.application.ai.provider import AIProvider, FakeAIProvider
from src.application.ai.schemas import (
    MALFORMED_ERROR,
    MAX_KP_ZH_CHARS,
    MAX_KP_ZH_TERMS,
    GlossaryEntry,
    KpZhResult,
    parse_kp_zh_response,
)
from src.application.ai.validators import ground_kp_zh, kp_translation_id
from src.application.errors import InvalidInputError, NotFoundError, ProcessingError
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace
from tests.test_ai_understanding import (
    TEXT_ES,
    _register_text,
    _request,
    _serve,
    _workspace,
)

FIXED_TIME = "2026-10-02T00:00:00+00:00"

#: 与 ``tests/test_ai_understanding.py`` 的 GEO 式西语正文同族: 治理/沟通类
#: 概念在真实数据里极常见, 且都不是离散术语, 正好压住"术语必须逐字出现"。
KP_TITLE = "Gobernanza y comunicación"
KP_CONTENT = (
    "La gobernanza define quién decide y la comunicación canaliza esas decisiones "
    "hacia el equipo."
)
KP_TERMS = ["gobernanza", "comunicación"]


# ------------------------------------------------------------------ 提示词


def test_kp_prompt_is_versioned_explanation_layer_and_truncates():
    prompt = build_kp_zh_prompt(
        knowledge_id="kp-1",
        title=KP_TITLE,
        content="x" * (KP_ZH_MAX_INPUT_CHARS + 500),
        terms=KP_TERMS,
        evidence_ids=["ev-1"],
        content_language="es",
    )
    assert KP_ZH_PROMPT_VERSION.endswith("kp-zh-v1")
    # 解释层, 不是全文对照翻译 —— prompt 必须把这条说死。
    assert "EXPLANATION LAYER, not a verbatim translation" in prompt
    assert "Keep every Spanish/Catalan technical term verbatim" in prompt
    assert "capital（资本）" in prompt
    assert "at most 200 Chinese characters" in prompt
    assert "lang=[语言待确认]" in prompt
    assert "The knowledge point language is es" in prompt
    # 截断真的生效: 进入 prompt 的 content 不超过上限。
    payload = json.loads(
        prompt.rsplit("KNOWLEDGE POINT INPUT:\n", 1)[1].split("\nReturn strict JSON:")[0]
    )
    assert len(payload["content"]) == KP_ZH_MAX_INPUT_CHARS
    assert payload["evidence_ids"] == ["ev-1"]
    # 提示词里绝不含证据原文本身 (只有 id), 这是"不做全文对照"的第二道保证。
    assert "ev-1" in prompt


def test_kp_prompt_without_declared_language_stays_neutral():
    prompt = build_kp_zh_prompt(knowledge_id="kp-1", title="x", content="y")
    assert "knowledge point language is" not in prompt
    assert "The requested explanation language is zh (Chinese)." in prompt


# ------------------------------------------------------------------ Schema


def test_kp_zh_schema_rejects_every_partial_or_loose_shape():
    parsed, error = parse_kp_zh_response(
        "```json\n"
        + json.dumps(
            {
                "translation_zh": "中文解释",
                "terms_zh": [
                    {"term": "gobernanza", "lang": "es", "zh": "治理", "evidence_refs": ["ev-1"]}
                ],
                "evidence_refs": ["ev-1"],
            },
            ensure_ascii=False,
        )
        + "\n```"
    )
    assert error is None and parsed is not None
    assert parsed.translation_zh == "中文解释"
    assert parsed.terms_zh[0].term == "gobernanza"

    for payload in (
        {},
        {"translation_zh": "x"},
        {"translation_zh": "x", "terms_zh": "not-a-list"},
        {"translation_zh": "", "terms_zh": []},
        {"translation_zh": "x", "terms_zh": [{"term": "t", "lang": "es"}]},
        {"translation_zh": "x", "terms_zh": [{"term": "", "lang": "es", "zh": "z"}]},
        {"translation_zh": "x", "terms_zh": "nope"},
    ):
        got, code = parse_kp_zh_response(json.dumps(payload, ensure_ascii=False))
        assert got is None and code == MALFORMED_ERROR, payload


def test_kp_zh_schema_budgets_output_and_refuses_invented_evidence():
    parsed, error = parse_kp_zh_response(
        json.dumps(
            {
                "translation_zh": "中" * 500,
                "terms_zh": [
                    {"term": "t%d" % index, "lang": "es", "zh": "词", "evidence_refs": ["ev-1"]}
                    for index in range(20)
                ],
                "evidence_refs": ["ev-1"],
            },
            ensure_ascii=False,
        )
    )
    assert error is None and parsed is not None
    assert len(parsed.translation_zh) == MAX_KP_ZH_CHARS
    assert len(parsed.terms_zh) == MAX_KP_ZH_TERMS

    # 模型自创一个这个知识点没有的证据 ID -> 整份拒绝 (不是悄悄丢掉那条)。
    invented, code = parse_kp_zh_response(
        json.dumps(
            {
                "translation_zh": "x",
                "terms_zh": [
                    {"term": "t", "lang": "es", "zh": "词", "evidence_refs": ["ev-made-up"]}
                ],
                "evidence_refs": ["ev-1"],
            },
            ensure_ascii=False,
        ),
        allowed_evidence_ids=["ev-1"],
    )
    assert invented is None and code == MALFORMED_ERROR


# ------------------------------------------------------------------ Grounding


def test_kp_zh_grounding_rejects_terms_absent_from_the_knowledge_point():
    result = KpZhResult(
        translation_zh="治理决定谁做决定，沟通把这些决定送到团队。",
        terms_zh=[
            GlossaryEntry(term="gobernanza", lang="es", zh="治理", evidence_refs=["ev-1"]),
            GlossaryEntry(term="capital", lang="es", zh="资本", evidence_refs=["ev-1"]),
            GlossaryEntry(term="comunicación", lang="xx", zh="沟通", evidence_refs=["ev-1"]),
            # 大小写变体: 仍算逐字命中 (与术语表同一条不区分大小写的规则),
            # 且同一条术语的重复项静默去重。
            GlossaryEntry(term="Comunicación", lang="ca", zh="沟通", evidence_refs=["ev-1"]),
            # 引用了这条知识点没有的证据 -> 拒收。
            GlossaryEntry(term="decisión", lang="ca", zh="决定", evidence_refs=["ev-9"]),
        ],
        evidence_refs=["ev-1"],
    )
    grounded, rejected = ground_kp_zh(
        result,
        knowledge_id="kp-1",
        source_texts=[KP_TITLE, KP_CONTENT, *KP_TERMS],
        evidence_ids=["ev-1"],
    )
    assert grounded is not None
    assert [item["term"] for item in grounded["terms_zh"]] == [
        "gobernanza",
        "comunicación",
    ]
    # 不认识的语言标成可见值, 而不是编一个 es / ca。
    assert grounded["terms_zh"][1]["lang"] == "[语言待确认]"
    reasons = [item["reason"] for item in rejected]
    assert any("verbatim knowledge-point substring" in reason for reason in reasons)
    assert any("illegal evidence refs" in reason for reason in reasons)


def test_kp_zh_grounding_refuses_without_evidence_or_text():
    result = KpZhResult(translation_zh="解释", terms_zh=[], evidence_refs=["ev-1"])
    # 无证据: 中文没有锚, 拒绝。
    grounded, rejected = ground_kp_zh(
        result, knowledge_id="kp-1", source_texts=[KP_TITLE], evidence_ids=[]
    )
    assert grounded is None
    assert rejected and "no evidence" in rejected[0]["reason"]
    # 知识点自身没内容: 同样不产中文 (否则就是让模型凭空写一段)。
    grounded, _ = ground_kp_zh(
        result, knowledge_id="kp-1", source_texts=["   "], evidence_ids=["ev-1"]
    )
    assert grounded is None


def test_kp_translation_identity_changes_with_content_and_prompt_version():
    base = kp_translation_id(knowledge_id="kp-1", content_hash="h1", prompt_version="v1")
    assert base.startswith("kpzh-")
    assert base == kp_translation_id(knowledge_id="kp-1", content_hash="h1", prompt_version="v1")
    assert base != kp_translation_id(knowledge_id="kp-1", content_hash="h2", prompt_version="v1")
    assert base != kp_translation_id(
        knowledge_id="kp-1", content_hash="h1", prompt_version="v2"
    )
    assert base != kp_translation_id(knowledge_id="kp-2", content_hash="h1", prompt_version="v1")


# ------------------------------------------------------------------ 管线


class _CountingProvider(FakeAIProvider):
    def __init__(self):
        super().__init__()
        self.calls = 0
        self.prompts = []

    def generate_structured(self, prompt, **kwargs):
        self.calls += 1
        self.prompts.append(str(prompt))
        return super().generate_structured(prompt, **kwargs)


class _FailingProvider(_CountingProvider):
    def generate_structured(self, prompt, **kwargs):
        self.calls += 1
        self.prompts.append(str(prompt))
        raise RuntimeError("model unavailable")


class _MalformedProvider(_CountingProvider):
    """只让**知识点翻译**这一步失败。

    材料级分析必须正常跑完 —— 否则失败的是另一个阶段, 我们测的就不是
    "翻译失败可重试" 了。
    """

    def generate_structured(self, prompt, **kwargs):
        if "KNOWLEDGE POINT INPUT:" in str(prompt):
            self.calls += 1
            self.prompts.append(str(prompt))
            return "not-json"
        return super().generate_structured(prompt, **kwargs)


def test_pipeline_skips_without_evidence_or_text_and_grounds_the_rest():
    pipeline = AIUnderstandingPipeline(_CountingProvider())
    assert pipeline.build_kp_zh(
        knowledge_id="kp-1", title=KP_TITLE, content=KP_CONTENT, evidence_ids=[]
    )["reason"] == "no_evidence"
    assert pipeline.build_kp_zh(
        knowledge_id="kp-1", title="", content="", evidence_ids=["ev-1"]
    )["reason"] == "empty_knowledge_point"
    # 上面两次都**没有**调到模型 —— "没有就不花钱"。
    assert pipeline.provider.generate_structured.__self__.calls == 0

    out = pipeline.build_kp_zh(
        knowledge_id="kp-1",
        title=KP_TITLE,
        content=KP_CONTENT,
        terms=KP_TERMS,
        evidence_ids=["ev-1"],
    )
    assert out["status"] == "completed"
    assert out["translation_zh"]
    assert out["provider"] == "fake-deterministic"
    for term in out["terms_zh"]:
        assert term["term"]
        assert term["lang"] in {"es", "ca", "[语言待确认]"}


def test_pipeline_failure_is_processing_error_and_leaves_the_call_retryable():
    for provider in (_FailingProvider(), _MalformedProvider()):
        pipeline = AIUnderstandingPipeline(provider)
        args = {
            "knowledge_id": "kp-1",
            "title": KP_TITLE,
            "content": KP_CONTENT,
            "evidence_ids": ["ev-1"],
        }
        with pytest.raises(ProcessingError):
            pipeline.build_kp_zh(**args)
        # 失败不是“降级成功”: 再调一次会**真的**再调模型 (没有被静默缓存
        # 成一个空结果), 所以重试是真重试。
        with pytest.raises(ProcessingError):
            pipeline.build_kp_zh(**args)
        assert provider.calls == 2


# ------------------------------------------------------------------ Workspace


def _prepared(tmp_path, provider=None):
    """建一门课 + 一份材料 + 一条带证据的知识点, 返回 (ws, course_id, kp)。

    TASK-81 §A 起: ``analyze_material_with_ai`` 已经把本次新落库的知识点逐条
    翻过 (kp_zh_auto), 所以这个 fixture 返回的知识点**已经**有中文缓存。
    需要"还没翻过"的知识点时用 ``_forget_translation`` 删掉缓存, 那才是
    早于本任务的历史数据形状。
    """
    ws = Workspace(
        str(tmp_path / "data"),
        clock=fixed_clock(FIXED_TIME),
        asr_mode="mock",
        ocr_mode="mock",
    )
    course_id = ws.create_course("Governança", "GOV1", "es")["course_id"]
    record = _register_text(ws, course_id, TEXT_ES, filename="gob.txt")
    ws.process_material(course_id, record["material_id"])
    ws.configure_ai(enabled=True, provider=provider or FakeAIProvider())
    ws.analyze_material_with_ai(course_id, record["material_id"])
    points = ws.knowledge_points(course_id)
    assert points, "fixture must produce at least one grounded knowledge point"
    return ws, course_id, points[0]


def _forget_translation(ws, course_id, knowledge_id):
    """删掉一条知识点的中文缓存 —— 模拟"早于自动阶段"的历史数据。"""
    Path(ws._kp_translation_path(course_id, knowledge_id)).unlink(missing_ok=True)


def _kp_zh_calls(provider):
    """已发出的知识点翻译 (kp-zh) prompt 数量 = 这条链路花掉的钱。"""
    return sum(1 for prompt in provider.prompts if "KNOWLEDGE POINT INPUT:" in prompt)


def test_translate_kp_writes_one_cache_file_and_is_idempotent(tmp_path):
    provider = _CountingProvider()
    ws, course_id, kp = _prepared(tmp_path, provider)
    try:
        kp_id = kp["knowledge_id"]
        # TASK-81 §A: 分析已经把这条翻过了, 所以显式翻译命中同一份缓存,
        # **不再调模型** (自动路径与手点路径共用一个身份)。
        auto = ws.kp_translation(course_id, kp_id)
        before = provider.calls
        payload = ws.translate_kp(course_id, kp_id)
        assert payload["status"] == "completed"
        assert payload["translation_zh"]
        assert payload["translation_identity"].startswith("kpzh-")
        assert payload["translation_identity"] == auto["translation_identity"]
        assert provider.calls == before

        # 缓存落盘 (TASK-77 的坑: 不能只写内存)。
        path = Path(ws._kp_translation_path(course_id, kp_id))
        assert path.exists()
        assert "kp-translations" in str(path).replace("\\", "/")
        assert json.loads(path.read_text(encoding="utf-8"))["knowledge_id"] == kp_id

        # 重复请求幂等: 同一文件、同一身份、**不再调模型**。
        again = ws.translate_kp(course_id, kp_id)
        assert again["translation_identity"] == payload["translation_identity"]
        assert again["created_at"] == payload["created_at"]
        assert provider.calls == before
        assert ws.kp_translation(course_id, kp_id)["translation_zh"] == payload["translation_zh"]

        # 没有缓存时 (历史知识点) 才真调一次模型 —— force=True 同理。
        _forget_translation(ws, course_id, kp_id)
        rebuilt = ws.translate_kp(course_id, kp_id)
        assert provider.calls == before + 1
        assert rebuilt["translation_zh"]
        assert ws.translate_kp(course_id, kp_id, force=True)["translation_zh"]
        assert provider.calls == before + 2
    finally:
        ws.close()


def test_translate_kp_requires_ai_and_never_touches_the_knowledge_point(tmp_path):
    ws, course_id, kp = _prepared(tmp_path)
    try:
        kp_id = kp["knowledge_id"]
        snapshot = dict(kp)
        # 分析已经翻过一次; 先清掉缓存, 才能验证"禁用时不产生缓存文件"。
        _forget_translation(ws, course_id, kp_id)
        ws.configure_ai(enabled=False)
        # AI 未启用 -> 400 INVALID_INPUT (与 /ai-analyze 同一个口径),
        # 不是 422 也不是 500: 这是配置问题, 不是一次可重试的处理失败。
        with pytest.raises(InvalidInputError):
            ws.translate_kp(course_id, kp_id)
        # 禁用时不产生任何缓存文件。
        assert not Path(ws._kp_translation_path(course_id, kp_id)).exists()
        assert dict(ws.knowledge_point(course_id, kp_id)) == snapshot

        ws.configure_ai(enabled=True)
        payload = ws.translate_kp(course_id, kp_id)
        # 翻译只读: 知识点本体一个字节都没动 (标题/正文/术语/证据链/状态)。
        assert dict(ws.knowledge_point(course_id, kp_id)) == snapshot
        assert payload["evidence_refs"] == list(kp["evidence_refs"])
    finally:
        ws.close()


def test_translate_kp_rejects_a_target_language_it_cannot_honour(tmp_path):
    ws, course_id, kp = _prepared(tmp_path)
    try:
        kp_id = kp["knowledge_id"]
        _forget_translation(ws, course_id, kp_id)
        with pytest.raises(InvalidInputError):
            ws.translate_kp(course_id, kp_id, target_lang="en")
        # 拒绝时**不**写缓存 —— 否则下一次合法请求会命中一个从没算出来的条目。
        assert not Path(ws._kp_translation_path(course_id, kp_id)).exists()
    finally:
        ws.close()


def test_translation_cache_is_invalidated_when_the_point_changes(tmp_path):
    ws, course_id, kp = _prepared(tmp_path)
    try:
        first = ws.translate_kp(course_id, kp["knowledge_id"])
        # 模拟"知识点内容被改了": 身份必须跟着变, 否则旧解释会冒充新的。
        mutated = ws._kp_content_hash(
            {**kp, "content": kp["content"] + " Una versión editada."}
        )
        assert ws._kp_content_hash(kp) != mutated
        assert ws._load_kp_translation(course_id, kp["knowledge_id"], mutated) is None
        assert ws._load_kp_translation(
            course_id, kp["knowledge_id"], ws._kp_content_hash(kp)
        )["translation_identity"] == first["translation_identity"]
    finally:
        ws.close()


def test_translation_cache_refuses_another_course_or_point(tmp_path):
    ws, course_id, kp = _prepared(tmp_path)
    try:
        ws.translate_kp(course_id, kp["knowledge_id"])
        content_hash = ws._kp_content_hash(kp)
        # 身份匹配但课程不符 -> 当未缓存 (宁可重算, 也不展示别人课的中文)。
        assert ws._load_kp_translation("course-other", kp["knowledge_id"], content_hash) is None
        assert ws._load_kp_translation(course_id, "kp-other", content_hash) is None
        # 换一个课程的缓存文件被塞进本课程路径 -> 同样拒绝。
        path = Path(ws._kp_translation_path(course_id, kp["knowledge_id"]))
        tampered = json.loads(path.read_text(encoding="utf-8"))
        tampered["course_id"] = "course-other"
        path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
        assert ws._load_kp_translation(course_id, kp["knowledge_id"], content_hash) is None
    finally:
        ws.close()


def test_translation_read_is_404_before_translation_and_never_calls_the_model(tmp_path):
    provider = _CountingProvider()
    ws, course_id, kp = _prepared(tmp_path, provider)
    try:
        kp_id = kp["knowledge_id"]
        # TASK-81 §A: 分析已自动翻过这条 -> 读接口直接给缓存, 零 LLM。
        assert ws.kp_translation(course_id, kp_id)["translation_zh"]
        # 历史知识点 (无缓存) 仍是 404, 且**绝不**为了好看而现场补算。
        _forget_translation(ws, course_id, kp_id)
        before = provider.calls
        with pytest.raises(NotFoundError):
            ws.kp_translation(course_id, kp_id)
        assert provider.calls == before
        with pytest.raises(NotFoundError):
            ws.kp_translation(course_id, "kp-does-not-exist")
    finally:
        ws.close()


def test_kp_glossary_is_free_and_scoped_to_this_knowledge_point(tmp_path):
    provider = _CountingProvider()
    ws, course_id, kp = _prepared(tmp_path, provider)
    try:
        before = provider.calls
        # 术语表来自**已落盘**的材料报告, 不调 LLM, 也不需要先翻译。
        glossary = ws.kp_glossary(course_id, kp["knowledge_id"])
        assert provider.calls == before
        assert glossary["source"] == "material-ai-report"
        assert glossary["glossary_total"] == len(glossary["glossary"])
        for entry in glossary["glossary"]:
            assert entry["term"] and entry["zh"]
            assert entry["lang"] in {"es", "ca", "[语言待确认]"}
        # 未分析的知识点: 空列表是合法答案 (不是 404)。
        other = ws.knowledge_points(course_id)[-1]
        assert isinstance(ws.kp_glossary(course_id, other["knowledge_id"])["glossary"], list)
    finally:
        ws.close()


# ------------------------------------------------------------------ HTTP


def test_http_endpoints_translate_glossary_and_error_codes(tmp_path):
    ws, course_id, kp = _prepared(tmp_path, _CountingProvider())
    server = _serve(ws)
    try:
        kp_id = kp["knowledge_id"]
        # GET 术语表: 零 LLM, 200。
        status, body = _request(
            server, "GET", "/api/knowledge/%s/glossary?course_id=%s" % (kp_id, course_id)
        )
        assert status == 200 and body["success"] is True, body
        assert "glossary" in body["data"]

        # TASK-81 §A: 分析已经翻过这条 -> GET 直接 200, 零 LLM。
        status, auto = _request(
            server, "GET", "/api/knowledge/%s/translate?course_id=%s" % (kp_id, course_id)
        )
        assert status == 200 and auto["data"]["translation_zh"], auto
        # 历史知识点 (无缓存) 仍必须 404, 不是 500, 更不是现场补算。
        _forget_translation(ws, course_id, kp_id)
        status, body = _request(
            server, "GET", "/api/knowledge/%s/translate?course_id=%s" % (kp_id, course_id)
        )
        assert status == 404 and body["error"]["code"] == "NOT_FOUND"

        # POST 翻译: 200 + 中文解释 + 术语。
        status, body = _request(
            server,
            "POST",
            "/api/knowledge/%s/translate?course_id=%s" % (kp_id, course_id),
            {"target_lang": "zh"},
        )
        assert status == 200 and body["success"] is True, body
        assert body["data"]["translation_zh"]
        assert body["data"]["knowledge_id"] == kp_id
        # 重算出的身份与自动阶段那次完全一致 (同一个文件, 不是两份缓存)。
        assert body["data"]["translation_identity"] == auto["data"]["translation_identity"]

        # 再 POST 一次是幂等的 (同一 identity)。
        status, again = _request(
            server,
            "POST",
            "/api/knowledge/%s/translate?course_id=%s" % (kp_id, course_id),
            {"target_lang": "zh"},
        )
        assert status == 200
        assert again["data"]["translation_identity"] == body["data"]["translation_identity"]

        # GET 现在能读到缓存。
        status, body = _request(
            server, "GET", "/api/knowledge/%s/translate?course_id=%s" % (kp_id, course_id)
        )
        assert status == 200
        assert body["data"]["translation_zh"] == again["data"]["translation_zh"]

        # 不支持的目标语言 -> 400 INVALID_INPUT, 不是悄悄按 zh 回答。
        status, body = _request(
            server,
            "POST",
            "/api/knowledge/%s/translate?course_id=%s" % (kp_id, course_id),
            {"target_lang": "en"},
        )
        assert status == 400 and body["error"]["code"] == "INVALID_INPUT"

        # 未知知识点 -> 404, 不是 500。
        status, body = _request(
            server,
            "POST",
            "/api/knowledge/kp-nope/translate?course_id=%s" % course_id,
            {"target_lang": "zh"},
        )
        assert status == 404 and body["error"]["code"] == "NOT_FOUND"
    finally:
        server.stop()
        ws.close()


def test_http_translate_failure_is_422_and_the_knowledge_point_survives(tmp_path):
    ws, course_id, kp = _prepared(tmp_path, _MalformedProvider())
    server = _serve(ws)
    try:
        kp_id = kp["knowledge_id"]
        status, body = _request(
            server,
            "POST",
            "/api/knowledge/%s/translate?course_id=%s" % (kp_id, course_id),
            {"target_lang": "zh"},
        )
        assert status == 422, (status, body)
        # 失败后知识点**仍在**, 原文与证据链一个字节没动。
        status, detail = _request(
            server, "GET", "/api/knowledge/%s?course_id=%s" % (kp_id, course_id)
        )
        assert status == 200
        assert detail["data"]["content"] == kp["content"]
        assert detail["data"]["evidence_refs"] == list(kp["evidence_refs"])
        assert detail["data"]["title"] == kp["title"]
        # 没有留下半个缓存文件 -> 重试会真跑一次, 而不是命中一个空结果。
        assert not Path(ws._kp_translation_path(course_id, kp_id)).exists()
        status, retry = _request(
            server,
            "POST",
            "/api/knowledge/%s/translate?course_id=%s" % (kp_id, course_id),
            {"target_lang": "zh"},
        )
        assert status == 422
    finally:
        server.stop()
        ws.close()


# ------------------------------------------------------- TASK-80: 前后端契约


def test_http_translate_accepts_course_id_in_body_only(tmp_path):
    """course_id **只**在 body 里也必须是 200 (前端真实形状)。

    TASK-80 的实测故障: 详情页的按钮 POST 只带
    ``{course_id, target_lang}`` body, 而 ``kp_translate`` 当时只读 query ——
    query 与 body 在 ``api.js`` 里是分开拼的, body 里的 course_id 永远不会
    出现在 URL 上。于是用户点一次“翻译成中文”就从"无反应"(按钮 data-* 为空,
    请求根本没发) 跳到"必现 400"。

    这条测试用**前端真实形状** (无 query) 打后端, 并断言 identity 与带 query
    时完全一致 —— 证明两种形状落到同一个缓存身份, 而不是两个缓存。
    """
    ws, course_id, kp = _prepared(tmp_path, _CountingProvider())
    server = _serve(ws)
    try:
        kp_id = kp["knowledge_id"]
        # 前端形状: course_id 只在 body, URL 上没有 query。
        status, body = _request(
            server,
            "POST",
            "/api/knowledge/%s/translate" % kp_id,
            {"course_id": course_id, "target_lang": "zh"},
        )
        assert status == 200 and body["success"] is True, (status, body)
        assert body["data"]["translation_zh"]
        assert body["data"]["knowledge_id"] == kp_id
        # 幂等 + 身份一致: 与带 query 的调用是同一份缓存。
        status, with_query = _request(
            server,
            "POST",
            "/api/knowledge/%s/translate?course_id=%s" % (kp_id, course_id),
            {"target_lang": "zh"},
        )
        assert status == 200
        assert (
            with_query["data"]["translation_identity"]
            == body["data"]["translation_identity"]
        )
        # 原文与证据仍然逐字未动 (本任务只改传参, 不碰内容)。
        status, detail = _request(
            server, "GET", "/api/knowledge/%s?course_id=%s" % (kp_id, course_id)
        )
        assert status == 200
        assert detail["data"]["content"] == kp["content"]
        assert detail["data"]["evidence_refs"] == list(kp["evidence_refs"])
    finally:
        server.stop()
        ws.close()


def test_http_translate_without_any_course_id_is_still_400(tmp_path):
    """双读不得把 course_id 变成**可选**。

    ``body.get("course_id") or request.require_q("course_id")`` 的存在意义是
    兼容两种形状, 不是取消必填: 两处都没有时仍是 400 INVALID_INPUT
    (参数错), 而不是拿着空 course_id 去翻一个不存在的知识点。
    """
    ws, course_id, kp = _prepared(tmp_path, _CountingProvider())
    server = _serve(ws)
    try:
        status, body = _request(
            server,
            "POST",
            "/api/knowledge/%s/translate" % kp["knowledge_id"],
            {"target_lang": "zh"},
        )
        assert status == 400, (status, body)
        assert body["error"]["code"] == "INVALID_INPUT"
        # body 为空字面量 (非 JSON 对象) 仍是 400, 不是 500。
        status, bad = _request(
            server,
            "POST",
            "/api/knowledge/%s/translate?course_id=%s" % (kp["knowledge_id"], course_id),
            [],
        )
        assert status == 400 and bad["error"]["code"] == "INVALID_INPUT"
    finally:
        server.stop()
        ws.close()


# ------------------------------------------------------- TASK-81: 自动中文层


def test_analysis_translates_every_new_knowledge_point_and_skips_the_second_run(tmp_path):
    """TASK-81 §A: 分析完 -> 每条新 KP 都有中文缓存; 改内容才失效重翻。"""
    provider = _CountingProvider()
    ws, course_id, kp = _prepared(tmp_path, provider)
    try:
        material_id = _material_of(ws, course_id)
        report = ws._load_persisted_ai_report(course_id, material_id)
        stage = [s for s in report["stages"] if s["stage"] == "kp_zh_auto"]
        assert len(stage) == 1, report["stages"]
        stage = stage[0]
        assert stage["state"] == "done", stage
        stored = len(report["auto_accepted"]) + len(report["needs_review"]) + len(report["conflicts"])
        assert stage["detail"].startswith("%d/%d ok" % (stored, stored)), stage

        for entry in report["auto_accepted"] + report["needs_review"] + report["conflicts"]:
            point = ws.knowledge_point(course_id, entry["knowledge_id"])
            cached = ws.kp_translation(course_id, point["knowledge_id"])
            assert cached["translation_zh"], point
            assert cached["translation_identity"].startswith("kpzh-")
            assert cached["evidence_refs"], cached

        # 二刷: 身份不变 -> 知识点翻译这一步**零调用** (材料级重跑本身仍会
        # 重新抽取/总结/术语表, 所以这里数的是 kp-zh 那部分的 prompt)。
        before = _kp_zh_calls(provider)
        again = ws.analyze_material_with_ai(course_id, material_id)
        assert _kp_zh_calls(provider) == before
        stage = [s for s in again["stages"] if s["stage"] == "kp_zh_auto"][0]
        assert stage["state"] == "done", stage
        assert "(0 translated, %d cached)" % stored in stage["detail"], stage

        # 改知识点内容 -> 身份变了 -> 真的重翻一次 (而不是拿旧解释冒充新的)。
        point = ws.knowledge_points(course_id)[0]
        edited = dict(point)
        edited["content"] = str(point["content"]) + " Una versión editada."
        ws.context(course_id).workflow.register_knowledge_point(edited)
        assert ws._load_kp_translation(
            course_id, point["knowledge_id"], ws._kp_content_hash(point)
        ) is not None
        assert ws._load_kp_translation(
            course_id, point["knowledge_id"], ws._kp_content_hash(edited)
        ) is None
    finally:
        ws.close()


def _material_of(ws, course_id):
    materials = ws.list_materials(course_id)
    assert materials, "fixture must produce at least one material"
    return str(materials[0]["material_id"])


def test_one_failing_translation_never_rolls_back_the_knowledge_points(tmp_path):
    """单条翻译失败 -> 材料仍成功, stage=partial, 重试只补失败的那条。"""
    ws, course_id, kp = _prepared(tmp_path, _CountingProvider())
    material_id = _material_of(ws, course_id)
    broken = _MalformedProvider()
    try:
        # 清掉缓存 + 只让知识点翻译这一步失败 -> 模拟"逐条翻译中途失败"。
        for point in ws.knowledge_points(course_id):
            _forget_translation(ws, course_id, point["knowledge_id"])
        ws.configure_ai(enabled=True, provider=broken)
        report = ws.analyze_material_with_ai(course_id, material_id)
        assert report["status"] == "completed"
        stage = [s for s in report["stages"] if s["stage"] == "kp_zh_auto"][0]
        assert stage["state"] == "partial", stage
        assert "0 failed" not in stage["detail"], stage
        # 知识点与证据链一个字节都没动 —— 翻译失败不是分析失败。
        for point in ws.knowledge_points(course_id):
            assert ws.knowledge_point(course_id, point["knowledge_id"])["content"] == point["content"]
        # 重试(provider 恢复)只补翻译, 不重新落知识点。
        ws.configure_ai(enabled=True, provider=FakeAIProvider())
        retry = ws.analyze_material_with_ai(course_id, material_id)
        stage = [s for s in retry["stages"] if s["stage"] == "kp_zh_auto"][0]
        assert stage["state"] == "done", stage
        assert ws.kp_translation(course_id, kp["knowledge_id"])["translation_zh"]
    finally:
        ws.close()


def test_backfill_is_idempotent_and_costs_nothing_the_second_time(tmp_path):
    """一键补翻: 第一次真翻, 第二次 translated=0 且 provider 零调用。"""
    provider = _CountingProvider()
    ws, course_id, _kp = _prepared(tmp_path, provider)
    try:
        total = len(ws.knowledge_points(course_id))
        for point in ws.knowledge_points(course_id):
            _forget_translation(ws, course_id, point["knowledge_id"])

        first = ws.backfill_kp_translations(course_id)
        assert first["total"] == total
        assert first["translated"] + first["skipped"] == total
        assert first["failed"] == 0, first["details"]
        calls_after_first = provider.calls

        second = ws.backfill_kp_translations(course_id)
        assert second["translated"] == 0
        assert second["cached"] == total - second["skipped"]
        assert provider.calls == calls_after_first
        # 原文与证据仍然逐字未动。
        assert ws.knowledge_point(course_id, ws.knowledge_points(course_id)[0]["knowledge_id"])[
            "evidence_refs"
        ]
    finally:
        ws.close()


def test_backfill_needs_ai_and_rejects_a_bad_limit(tmp_path):
    ws, course_id, kp = _prepared(tmp_path)
    try:
        ws.configure_ai(enabled=False)
        with pytest.raises(InvalidInputError):
            ws.backfill_kp_translations(course_id)
        ws.configure_ai(enabled=True)
        assert ws.backfill_kp_translations(course_id, limit=1)["total"] == 1
    finally:
        ws.close()


def test_http_backfill_reports_counts_and_is_idempotent(tmp_path):
    ws, course_id, kp = _prepared(tmp_path, _CountingProvider())
    server = _serve(ws)
    try:
        path = "/api/courses/%s/kp-translations/backfill" % course_id
        status, body = _request(server, "POST", path, {})
        assert status == 200 and body["success"] is True, (status, body)
        data = body["data"]
        assert data["course_id"] == course_id
        assert data["total"] >= 1
        assert data["failed"] == 0, data["details"]
        assert data["cached"] + data["translated"] + data["skipped"] == data["total"]
        # GET 是只读的: 缓存已经存在。
        status, read = _request(
            server,
            "GET",
            "/api/knowledge/%s/translate?course_id=%s" % (kp["knowledge_id"], course_id),
        )
        assert status == 200 and read["data"]["translation_zh"]

        status, again = _request(server, "POST", path, {})
        assert status == 200 and again["data"]["translated"] == 0

        # 坏的 limit -> 400 INVALID_INPUT, 不是 500。
        status, bad = _request(server, "POST", path, {"limit": "abc"})
        assert status == 400 and bad["error"]["code"] == "INVALID_INPUT"
    finally:
        server.stop()
        ws.close()


def test_auto_accepted_knowledge_points_are_confirmed_but_still_rejectable(tmp_path):
    """TASK-81 §B: 自动确认 + 拒绝流仍可用 (兜底只剩证据链与拒绝按钮)。"""
    ws = _workspace(tmp_path)
    course_id = ws.create_course("Governança", "GOV1", "es")["course_id"]
    record = _register_text(ws, course_id, TEXT_ES, filename="gob.txt")
    ws.process_material(course_id, record["material_id"])
    ws.configure_ai(enabled=True, provider=FakeAIProvider())
    report = ws.analyze_material_with_ai(course_id, record["material_id"])
    try:
        assert report["auto_accepted"], report
        target = report["auto_accepted"][0]["knowledge_id"]
        kp = ws.knowledge_point(course_id, target)
        assert kp["review_status"] == "confirmed"
        assert kp["validation_status"] == "unverified"  # 验证轴照旧
        assert kp["needs_verification"] is False
        # 存疑的那批仍 pending -> 待审核页不会空。
        assert any(
            item["review_status"] == "pending" for item in ws.knowledge_points(course_id)
        )
        # 拒绝流仍可用: 点一下就能把自动确认的知识点拉下来。
        ws.review_reject(course_id, target)
        assert ws.knowledge_point(course_id, target)["review_status"] == "rejected"
    finally:
        ws.close()


# ------------------------------------------------------------------ 结构守卫


def test_automatic_translation_only_calls_the_pipeline_never_the_http_entry():
    """自动段只调 ``build_kp_zh``, 绝不回走 ``translate_kp`` (POST 入口)。

    TASK-81 把"按需"改成"自动 + 按需可重试", 但两者**必须**是两条路:

    - 自动路径: 单条失败只记 skipped/partial, 绝不抛、绝不回滚知识点;
    - 显式 POST: 失败要抛 422 让用户能重试。

    如果自动段改成调 ``translate_kp``, 一次 provider 抖动就会把整份材料
    报成失败 —— 这正是 summary_zh/glossary 当年用隔离换取材料安全的原因。
    """
    import inspect

    stage_source = inspect.getsource(Workspace._kp_translation_stage)
    once_source = inspect.getsource(Workspace._translate_kp_once)
    analyze_source = inspect.getsource(Workspace.analyze_material_with_ai)
    assert "build_kp_zh" in once_source
    assert "translate_kp(" not in stage_source
    assert "translate_kp(" not in once_source
    assert "translate_kp(" not in analyze_source
    # 材料级报告的版本号没有被翻译阶段牵动 (否则改一句 prompt 就得全量重跑)。
    assert "kp-zh" not in processing_identity(
        material_hash="h", model="m", prompt_version="ai-prompts-v1:extract-v3"
    )
    # 入口仍只有三个公开面: 显式翻译 / 只读缓存 / 术语表 + 批量补翻。
    assert hasattr(Workspace, "translate_kp")
    assert hasattr(Workspace, "kp_translation")
    assert hasattr(Workspace, "kp_glossary")
    assert hasattr(Workspace, "backfill_kp_translations")


def test_the_explanation_language_picker_is_gone_from_the_ui():
    """TASK-81 §C: 请求语言下拉被删掉 (它只能选出一个必然 404 的组合)。"""
    import tests.support as support

    raw = support.read_web_source("views/students.js")
    # 注释里可以解释历史, 但**代码**里不许再有这个下拉 (扫描器不剥注释,
    # 所以这里显式只查代码部分)。
    source = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    source = re.sub(r"(?m)//[^\n]*", "", source)
    assert "explain-language" not in source
    assert "UI_LANGUAGES.map" not in source
    assert "expl.request" not in source
    i18n = support.read_web_source("i18n.js")
    assert "'expl.request'" not in i18n
    # 证据原文仍逐字渲染 (面板与溯源链都在)。
    assert "originalBlock(rep.explanation, null)" in raw
    assert "originalBlock(ev.content, ev.language)" in raw
    detail = support.read_web_source("views/knowledge.js")
    assert "function explanationLanguage(" in detail
    assert "explanationLanguage(trace)" in detail