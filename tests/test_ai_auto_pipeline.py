# -*- coding: utf-8 -*-
"""TASK-77: 自动 AI 材料管线 (auto trigger + 非阻塞 + 幂等 + 隔离 + 总结持久化)。

与 ``tests/test_ai_understanding.py`` (TASK-76, 显式触发) 的分工:

- TASK-76: ``analyze_material_with_ai`` 本体的正确性 (chunk/grounding/
  dedup/policy)。本文件**不重复**那些断言, 只测"自动"这一层。
- TASK-77: ``process_material`` 自动触发 AI; 关闭时旧行为; 失败不污染
  材料; 重试; 幂等; 去重; 冲突进 Review; 课程/学生隔离; 总结落盘与重启;
  多模态自动; HTTP E2E (upload -> process -> auto AI -> KP, 零手工 KP)。

默认 provider 是确定性 ``FakeAIProvider`` (零出站)。真实 API 只在
``tests/test_live_ai_smoke.py`` (integration, 默认 SKIP)。
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import urllib.error
import urllib.request

import pytest

from src.application.ai import PIPELINE_VERSION
from src.application.ai.config import AIEnvNames, get_api_key, load_ai_config
from src.application.ai.merge import propose_merge_with_existing
from src.application.ai.pipeline import AIUnderstandingPipeline
from src.application.ai.prompts import (
    AUDIO_PROMPT_VERSION,
    CHUNK_EXTRACTION_PROMPT_VERSION,
    IMAGE_PROMPT_VERSION,
    MERGE_PROMPT_VERSION,
    SUMMARY_PROMPT_VERSION,
    build_audio_analysis_prompt,
    build_chunk_extraction_prompt,
    build_image_analysis_prompt,
    build_merge_prompt,
)
from src.application.ai.provider import (
    AIProvider,
    AIRequestError,
    FakeAIProvider,
    MAX_IMAGE_BYTES,
    OpenAICompatibleAIProvider,
    ProviderCapabilities,
    sniff_image_mime,
)
from src.application.ai.schemas import KnowledgeCandidate
from src.application.ai.validators import AI_PIPELINE_POLICY
from src.application.errors import ConfigurationError, InvalidInputError
from src.application.runtime import fixed_clock
from src.application.workspace import (
    AI_AUTO_COMPLETED,
    AI_AUTO_FAILED,
    AI_AUTO_SKIPPED,
    Workspace,
)

FIXED_TIME = "2026-09-21T09:00:00+00:00"

TEXT_ES = (
    "La integración por partes es un método fundamental. "
    "La definición de integral definida es el área bajo la curva. "
    "Por ejemplo, la integral de x es x al cuadrado sobre dos. "
    "El teorema fundamental del cálculo conecta derivación e integración."
)


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


def _enable(ws, provider=None):
    return ws.configure_ai(enabled=True, provider=provider or FakeAIProvider())


class _FailingAIProvider(AIProvider):
    """恒失败 provider (§27: AI 不可用时的非阻塞行为)。"""

    name = "failing-test"

    def __init__(self, message="AI request failed; evidence is safe", status=429):
        self._message = message
        self._status = status

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_text=True,
            supports_image=True,
            supports_audio=False,
            supports_structured_output=True,
            supports_image_bytes=False,
        )

    def generate_structured(self, prompt, *, timeout_seconds=60, max_output_chars=8000):
        raise AIRequestError(
            self._message,
            detail={"provider": self.name, "http_status": self._status},
        )


# ======================================================================
# 1. 自动触发与开关
# ======================================================================


class TestAutoTrigger:
    def test_process_auto_triggers_ai_when_enabled(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("Cálculo II", "M101", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES, filename="calc.txt")
        _enable(ws)
        # 用户只做上传 + 处理, 全程没有手动创建 KnowledgePoint。
        job = ws.process_material(cid, record["material_id"])
        assert job["status"] == "SUCCEEDED"
        ai = job.get("ai")
        assert ai is not None, job
        assert ai["status"] == AI_AUTO_COMPLETED, ai
        assert ai["auto_accepted"] + ai["needs_review"] + ai["conflicts"] >= 1
        assert ai["retryable"] is False
        # 知识库里已经有 AI 自动生成的知识点 (aikp-* 命名空间)。
        auto_kps = [
            k for k in ws.knowledge_points(cid)
            if str(k.get("knowledge_id") or "").startswith("aikp-")
        ]
        assert auto_kps, "AI enabled but no automatic KnowledgePoint was created"

    def test_ai_disabled_keeps_old_pipeline(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES)
        assert ws.ai_enabled is False
        job = ws.process_material(cid, record["material_id"])
        assert job["status"] == "SUCCEEDED"
        assert "ai" not in job  # 关闭时作业形状与 TASK-76 完全一致
        assert len(ws.knowledge_points(cid)) >= 1  # 旧确定性链路照常产出
        with pytest.raises(InvalidInputError):
            ws.analyze_material_with_ai(cid, record["material_id"])

    def test_auto_skipped_when_ingestion_failed(self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES, filename="broken.txt")
        _enable(ws)
        # 摄取层抛错 -> 材料 FAILED; AI 必须跳过而不是跟着炸。
        ingestion = ws.context(cid).workflow._ingestion

        def _boom(material, *args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(ingestion, "ingest", _boom)
        job = ws.process_material(cid, record["material_id"])
        assert job["status"] == "FAILED"
        assert job.get("ai", {}).get("status") == AI_AUTO_SKIPPED

    def test_process_session_auto_ai(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        sid = ws.create_session(cid, session_number=1, title="S1")["session_id"]
        r1 = _register_text(ws, cid, TEXT_ES, filename="a.txt", session_id=sid)
        r2 = _register_text(
            ws, cid, "La derivada de una función mide su ritmo de cambio. Es un concepto clave.",
            filename="b.txt", session_id=sid,
        )
        _enable(ws)
        report = ws.process_session(cid, sid)
        auto = report.get("ai_auto")
        assert auto is not None
        assert auto[r1["material_id"]]["status"] == AI_AUTO_COMPLETED
        assert auto[r2["material_id"]]["status"] == AI_AUTO_COMPLETED

    def test_concurrent_uploads_stay_isolated(self, tmp_path):
        # §30: 5 个文件串行自动处理, 各自的 AI 作业互不覆盖 (同步架构,
        # 受控串行, 无 Celery/Redis)。
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        _enable(ws)
        mids = []
        for index in range(5):
            record = _register_text(
                ws, cid,
                "Tema %d: la integral definida es el área bajo la curva. Es esencial." % index,
                filename="t%d.txt" % index,
            )
            mids.append(record["material_id"])
        jobs = [ws.process_material(cid, mid) for mid in mids]
        assert [j["status"] for j in jobs] == ["SUCCEEDED"] * 5
        assert [j["ai"]["status"] for j in jobs] == [AI_AUTO_COMPLETED] * 5
        identities = {j["ai"]["processing_identity"] for j in jobs}
        assert len(identities) == 5  # 每份材料各自的幂等键, 互不覆盖


# ======================================================================
# 2. 成功路径: 总结 / grounding / 置信度策略复用
# ======================================================================


class TestAutoSuccess:
    def test_summary_topics_and_grounding(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("Cálculo II", "M101", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES, filename="calc.txt")
        _enable(ws)
        ws.process_material(cid, record["material_id"])
        summary = ws.ai_summary(cid, record["material_id"])
        assert summary["material_id"] == record["material_id"]
        assert summary["summary"]
        assert summary["topics"]
        assert summary["knowledge_points_total"] >= 1
        assert summary["pipeline_version"] == PIPELINE_VERSION
        assert str(summary["processing_identity"]).startswith("ai-")
        # 全部自动 KP 都有合法 Evidence 绑定 (§9: 禁止无证据 KP)。
        evidence_ids = {
            e["evidence_id"] for e in ws.material_evidence(cid, record["material_id"])
        }
        for kp in ws.knowledge_points(cid):
            if not str(kp.get("knowledge_id") or "").startswith("aikp-"):
                continue
            refs = list(kp.get("evidence_refs") or [])
            assert refs, kp
            assert set(refs) <= evidence_ids, kp
            trace = ws.knowledge_trace(cid, kp["knowledge_id"])
            assert trace["evidence"], kp
            assert trace["materials"], kp

    def test_confidence_policy_is_reused_not_reinvented(self):
        # §11: 沿用 TASK-76 的阈值 (0.90 自动 / 0.70 待确认)。
        assert AI_PIPELINE_POLICY["auto_accept"] == 0.90
        assert AI_PIPELINE_POLICY["review"] == 0.70

    def test_low_confidence_enters_review(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES)
        _enable(ws)
        job = ws.process_material(cid, record["material_id"])
        assert job["ai"]["needs_review"] >= 1  # Fake 次句 0.8 -> 待确认
        pending = ws.knowledge_points(cid, review_status="pending")
        assert pending
        # Review 中心可处理: confirm / reject / keep 三条不断。
        target = pending[0]["knowledge_id"]
        ws.review_confirm(cid, target)
        ws.review_reject(cid, pending[-1]["knowledge_id"]) if len(pending) > 1 else None


# ======================================================================
# 3. 失败路径: 非阻塞 + 重试 (§5/§27/§29)
# ======================================================================


class TestAutoFailureAndRetry:
    def test_ai_failure_does_not_break_material(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES)
        ws.configure_ai(enabled=True, provider=_FailingAIProvider())
        job = ws.process_material(cid, record["material_id"])
        # Material 摄取成功, Evidence 安全, 只有 AI 失败。
        assert job["status"] == "SUCCEEDED", job
        assert job["evidence_ids"], job
        ai = job.get("ai")
        assert ai is not None and ai["status"] == AI_AUTO_FAILED, ai
        assert ai["retryable"] is True
        assert ai["error"]

    def test_retry_after_recovery_generates_kps(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES)
        ws.configure_ai(enabled=True, provider=_FailingAIProvider())
        failed = ws.process_material(cid, record["material_id"])
        assert failed["ai"]["status"] == AI_AUTO_FAILED
        before = len(ws.knowledge_points(cid))
        # AI 恢复 -> 重试 (幂等入口) -> 知识点生成。
        ws.configure_ai(enabled=True, provider=FakeAIProvider())
        report = ws.retry_ai_analysis(cid, record["material_id"])
        assert report["status"] == "completed"
        assert len(ws.knowledge_points(cid)) > before
        # 重试后读路径也带上 AI 摘要。
        brief = ws.processing_job(cid, record["material_id"]).get("ai")
        assert brief is not None and brief["status"] == AI_AUTO_COMPLETED

    def test_retry_material_also_retries_ai(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES)
        ws.configure_ai(enabled=True, provider=_FailingAIProvider())
        ws.process_material(cid, record["material_id"])
        ws.configure_ai(enabled=True, provider=FakeAIProvider())
        job = ws.retry_material(cid, record["material_id"])
        assert job["status"] == "SUCCEEDED"
        assert job.get("ai", {}).get("status") == AI_AUTO_COMPLETED


# ======================================================================
# 4. 幂等 / 去重 / 冲突 (§13/§14)
# ======================================================================


class TestIdempotencyDedupConflict:
    def test_repeated_auto_analysis_is_idempotent(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES)
        _enable(ws)
        first = ws.process_material(cid, record["material_id"])
        count = len(ws.knowledge_points(cid))
        evidence_total = len(ws.material_evidence(cid, record["material_id"]))
        for _ in range(2):
            job = ws.process_material(cid, record["material_id"])
            assert job["ai"]["status"] == AI_AUTO_COMPLETED
        assert len(ws.knowledge_points(cid)) == count
        assert len(ws.material_evidence(cid, record["material_id"])) == evidence_total
        assert ws.process_material(cid, record["material_id"])["ai"][
            "processing_identity"
        ] == first["ai"]["processing_identity"]

    def test_duplicate_concept_attaches_instead_of_duplicating(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        sentence = "La fotosíntesis ocurre en los cloroplastos."
        first = _register_text(ws, cid, sentence, filename="a.txt")
        second = _register_text(ws, cid, sentence, filename="b.txt")
        _enable(ws)
        ws.process_material(cid, first["material_id"])
        ws.process_material(cid, second["material_id"])
        matches = [
            k for k in ws.knowledge_points(cid)
            if k.get("title") == sentence
            and str(k.get("knowledge_id") or "").startswith("aikp-")
        ]
        assert len(matches) == 1  # 不能出现 Derivative / Derivative
        assert len(matches[0].get("evidence_refs") or []) >= 1

    def test_conflict_rule_and_review_entry(self, tmp_path):
        # 规则层 (确定性): 标题高度相似但证据不同 -> conflict。
        existing = [{
            "knowledge_id": "aikp-old",
            "title": "La fotosíntesis ocurre en los cloroplastos",
            "content": "old",
            "evidence_refs": ["ev-old"],
        }]
        candidate = KnowledgeCandidate(
            title="La fotosíntesis ocurre en los cloroplastos verdes",
            description="new",
            kp_type="concept",
            importance="high",
            confidence=0.95,
            evidence_refs=["chunk-x"],
            relations=[],
            examples=[],
            original_terms=[],
        )
        proposals = propose_merge_with_existing(
            [candidate], existing, candidate_evidence=[["ev-new"]],
        )
        assert proposals and proposals[0].action == "conflict"
        # 管线层: 近似重复句跨材料出现 -> 不翻倍, 且进 Review/冲突队列。
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        _enable(ws)
        r1 = _register_text(
            ws, cid, "La fotosíntesis ocurre en los cloroplastos.", filename="a.txt")
        r2 = _register_text(
            ws, cid,
            "La fotosíntesis ocurre en los cloroplastos verdes. Además necesita luz.",
            filename="b.txt",
        )
        ws.process_material(cid, r1["material_id"])
        count_after_first = len(ws.knowledge_points(cid))
        job2 = ws.process_material(cid, r2["material_id"])
        assert job2["ai"]["status"] == AI_AUTO_COMPLETED
        total_new = (
            job2["ai"]["needs_review"] + job2["ai"]["conflicts"]
            + job2["ai"]["auto_accepted"]
        )
        assert total_new >= 1
        assert len(ws.knowledge_points(cid)) < count_after_first * 2 + 3
        # 禁止 AI 自动替用户解决事实冲突: 冲突/待确认项必须仍在 Review 队列。
        pending = ws.knowledge_points(cid, review_status="pending")
        assert pending


# ======================================================================
# 5. 隔离 (§25/§26)
# ======================================================================


class TestIsolation:
    def test_course_isolation(self, tmp_path):
        ws = _workspace(tmp_path)
        c1 = ws.create_course("C1", "A1", "es")["course_id"]
        c2 = ws.create_course("C2", "A2", "ca")["course_id"]
        r1 = _register_text(ws, c1, TEXT_ES)
        r2 = _register_text(ws, c2, "La fotosíntesi passa als cloroplasts. És un procés vital.")
        _enable(ws)
        ws.process_material(c1, r1["material_id"])
        ws.process_material(c2, r2["material_id"])
        ids_c1 = {k["knowledge_id"] for k in ws.knowledge_points(c1)}
        ids_c2 = {k["knowledge_id"] for k in ws.knowledge_points(c2)}
        assert not ({k for k in ids_c1 if k.startswith("aikp-")} & ids_c2)
        for kp in ws.knowledge_points(c1):
            if str(kp.get("knowledge_id") or "").startswith("aikp-"):
                trace = ws.knowledge_trace(c1, kp["knowledge_id"])
                assert trace["materials"], kp

    def test_student_state_untouched(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        student = ws.create_student(cid, "s1", "Ana")
        sid = student["student_id"]
        record = _register_text(ws, cid, TEXT_ES)
        _enable(ws)
        before_state = ws.student_state(cid, sid)
        before_student = ws.get_student(cid, sid)
        ws.process_material(cid, record["material_id"])
        assert ws.get_student(cid, sid) == before_student
        assert ws.student_state(cid, sid) == before_state


# ======================================================================
# 6. 多模态自动 (§22/§23/§24)
# ======================================================================


class TestMultimodalAuto:
    def test_image_pipeline_auto(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("Física", "F101", "es")["course_id"]
        png = _write(
            tmp_path, "board.png",
            bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 100, binary=True,
        )
        record = ws.register_material(cid, png)
        _enable(ws)
        job = ws.process_material(cid, record["material_id"])
        assert job["status"] == "SUCCEEDED" and job["evidence_ids"]
        assert job["ai"]["status"] == AI_AUTO_COMPLETED
        summary = ws.ai_summary(cid, record["material_id"])
        assert summary["knowledge_points_total"] >= 1

    def test_audio_pipeline_auto_keeps_timestamp(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("Historia", "H101", "es")["course_id"]
        audio = _write(tmp_path, "lec.mp3", b"ID3" + b"\x00" * 500, binary=True)
        record = ws.register_material(cid, audio)
        _enable(ws)
        job = ws.process_material(cid, record["material_id"])
        assert job["status"] == "SUCCEEDED" and job["evidence_ids"]
        assert job["ai"]["status"] == AI_AUTO_COMPLETED
        for kp in ws.knowledge_points(cid):
            if not str(kp.get("knowledge_id") or "").startswith("aikp-"):
                continue
            trace = ws.knowledge_trace(cid, kp["knowledge_id"])
            assert trace["materials"]
            assert trace["materials"][0].get("filename", "").endswith(".mp3")

    def test_multilingual_original_is_preserved(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("Matemàtiques", "M102", "ca")["course_id"]
        original = "La integral per parts és un mètode. La definició de funció és una relació."
        record = _register_text(ws, cid, original, filename="lliçó.txt")
        _enable(ws)
        ws.process_material(cid, record["material_id"], )
        for ev in ws.material_evidence(cid, record["material_id"]):
            assert "integral per parts" in ev["content"] or "funció" in ev["content"]


# ======================================================================
# 7. 总结持久化: 重启后仍在 (§15)
# ======================================================================


class TestSummaryPersistence:
    def _persistent_workspace(self, data_dir):
        return Workspace(
            str(data_dir),
            clock=fixed_clock(FIXED_TIME),
            asr_mode="mock",
            ocr_mode="mock",
        )

    def test_restart_keeps_kps_and_summary(self, tmp_path):
        data_dir = tmp_path / "data"
        ws = self._persistent_workspace(data_dir)
        cid = ws.create_course("Cálculo II", "M101", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES, filename="calc.txt")
        _enable(ws)
        job = ws.process_material(cid, record["material_id"])
        assert job["ai"]["status"] == AI_AUTO_COMPLETED
        summary_before = ws.ai_summary(cid, record["material_id"])
        kp_count = len(ws.knowledge_points(cid))
        assert kp_count >= 1
        ws.close()
        # 服务器 restart: 新进程打开同一数据目录。
        ws2 = self._persistent_workspace(data_dir)
        try:
            assert len(ws2.knowledge_points(cid)) == kp_count
            summary_after = ws2.ai_summary(cid, record["material_id"])
            assert summary_after["summary"] == summary_before["summary"]
            assert (
                summary_after["knowledge_points_total"]
                == summary_before["knowledge_points_total"]
            )
        finally:
            ws2.close()


# ======================================================================
# 8. Provider 错误形状: 401/429/超时/畸形 (§17/§31/§32) + Key 安全 (§18)
# ======================================================================


class TestProviderErrorsAndKeySafety:
    def _provider(self, **kw):
        return OpenAICompatibleAIProvider(
            api_key=kw.pop("api_key", "sk-test-placeholder"),
            base_url=kw.pop("base_url", "https://example.com/v1"),
            model=kw.pop("model", "test-model"),
            **kw,
        )

    def test_401_is_auth_failure_without_key(self, monkeypatch):
        provider = self._provider(max_retries=0)
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(request.full_url)
            raise urllib.error.HTTPError(
                request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}")
            )

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(AIRequestError) as exc_info:
            provider.generate_structured("hola")
        assert "authentication failed" in str(exc_info.value).lower()
        assert exc_info.value.detail.get("http_status") == 401
        assert "sk-test-placeholder" not in str(exc_info.value)
        assert "sk-test-placeholder" not in json.dumps(exc_info.value.detail)

    def test_429_retries_bounded_then_fails(self, monkeypatch):
        provider = self._provider(max_retries=1)
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(1)
            raise urllib.error.HTTPError(
                request.full_url, 429, "Too Many Requests", {}, io.BytesIO(b"{}")
            )

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(AIRequestError) as exc_info:
            provider.generate_structured("hola")
        assert len(calls) == 2  # 1 + max_retries, 绝不疯狂重试
        assert "rate limit" in str(exc_info.value).lower()

    def test_timeout_has_a_budget(self, monkeypatch):
        provider = self._provider(max_retries=0)
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["timeout"] = timeout
            raise TimeoutError("timed out")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(AIRequestError):
            provider.generate_structured("hola", timeout_seconds=60)
        assert seen["timeout"] == 60  # 绝不无限等待

    def test_malformed_response_is_diagnosable(self, monkeypatch):
        provider = self._provider(max_retries=0)

        class _Resp:
            def read(self):
                return b"not json at all"
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Resp())
        with pytest.raises(AIRequestError) as exc_info:
            provider.generate_structured("hola")
        assert "malformed" in str(exc_info.value).lower()

    def test_key_never_leaks(self, tmp_path):
        secret = "sk-test-never-leak-0123456789"
        provider = self._provider(api_key=secret)
        assert secret not in repr(provider)
        config = load_ai_config(env={
            AIEnvNames.API_KEY: secret,
            AIEnvNames.BASE_URL: "https://example.com/v1",
        })
        assert secret not in json.dumps(config.describe(), ensure_ascii=False)
        assert secret not in repr(config)
        assert get_api_key({AIEnvNames.API_KEY: secret}) == secret
        # 失败路径的作业/报告里同样无 key。
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        record = _register_text(ws, cid, TEXT_ES)
        ws.configure_ai(enabled=True, provider=_FailingAIProvider())
        job = ws.process_material(cid, record["material_id"])
        assert secret not in json.dumps(job, ensure_ascii=False, default=str)

    def test_wrapped_200_error_is_diagnosable(self, monkeypatch):
        # 某些网关把后端报错包在 HTTP 200 里: 必须报出内部原因,
        # 不能流进 schema 校验器变成一句含糊的"N of N chunks failed"。
        provider = self._provider(max_retries=0)

        class _Resp:
            def read(self):
                inner = json.dumps({
                    "type": "error",
                    "error": {"type": "invalid_request_error",
                              "message": "An internal error occurred when running the model."},
                })
                return json.dumps({
                    "choices": [{"message": {"content": inner}}]}).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Resp())
        with pytest.raises(AIRequestError) as exc_info:
            provider.generate_structured("hola")
        assert "internal error occurred" in str(exc_info.value)
        assert "sk-test-placeholder" not in str(exc_info.value)

    def test_json_mode_flag_controls_response_format(self, monkeypatch):
        seen = {}

        class _Resp:
            def read(self):
                return json.dumps({
                    "choices": [{"message": {"content": json.dumps({
                        "summary": "s", "topics": [],
                        "knowledge_points": []})}}]}).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout=None):
            seen["body"] = json.loads(request.data.decode("utf-8"))
            return _Resp()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        self._provider().generate_structured("hola")
        assert seen["body"]["response_format"] == {"type": "json_object"}
        self._provider(json_mode=False).generate_structured("hola")
        assert "response_format" not in seen["body"]

    def test_json_mode_config_parsing(self):
        assert load_ai_config(env={}).json_mode is True
        assert load_ai_config(env={AIEnvNames.JSON_MODE: "false"}).json_mode is False
        assert load_ai_config(env={AIEnvNames.JSON_MODE: "0"}).json_mode is False
        assert load_ai_config(env={AIEnvNames.JSON_MODE: "true"}).json_mode is True
        assert load_ai_config(env={AIEnvNames.JSON_MODE: "false"}).describe()["json_mode"] is False
        with pytest.raises(ConfigurationError):
            load_ai_config(env={AIEnvNames.JSON_MODE: "quizas"})


# ======================================================================
# 9. 提示词 v2: 浓缩改写（考前复习导向，拒绝原文复读）
# ======================================================================


class TestPromptDistillationV2:
    def test_versions_bumped_to_v2(self):
        assert CHUNK_EXTRACTION_PROMPT_VERSION.endswith(":extract-v2")
        assert MERGE_PROMPT_VERSION.endswith(":merge-v2")
        assert SUMMARY_PROMPT_VERSION.endswith(":summary-v2")
        assert IMAGE_PROMPT_VERSION.endswith(":image-v2")
        assert AUDIO_PROMPT_VERSION.endswith(":audio-v2")

    def test_chunk_prompt_demands_condensation(self):
        prompt = build_chunk_extraction_prompt(chunk_id="c", chunk_text="t")
        for required in ("CONDENSED", "verbatim", "INTERPRET", "evidence_refs",
                         "confidence", "STRICT JSON", "ONLY"):
            assert required in prompt, required

    def test_merge_image_audio_prompts_share_distillation(self):
        merge = build_merge_prompt(chunk_summaries="s")
        image = build_image_analysis_prompt(ocr_text="t", chunk_id="c")
        audio = build_audio_analysis_prompt(transcript_text="t", chunk_id="c")
        for prompt in (merge, image, audio):
            assert "CONDENSED" in prompt
            assert "verbatim" in prompt


# ======================================================================
# 10. Vision 可选（默认关闭；开后图片字节出境）
# ======================================================================


class _VisionStubProvider(FakeAIProvider):
    name = "vision-stub"

    def __init__(self):
        super().__init__(tag="vision-stub")
        self.image_calls: list[dict] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_text=True,
            supports_image=True,
            supports_audio=False,
            supports_structured_output=True,
            supports_image_bytes=True,
        )

    def analyze_image_bytes(self, image_bytes, *, ocr_text="", chunk_id="",
                            material_label="", content_language=None,
                            timeout_seconds=60):
        self.image_calls.append({
            "bytes": bytes(image_bytes), "ocr": ocr_text, "chunk": chunk_id,
        })
        return self.analyze_image(
            ocr_text, chunk_id=chunk_id, material_label=material_label,
            content_language=content_language, timeout_seconds=timeout_seconds,
        )


PNG_BYTES = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 100


class TestVisionOptIn:
    def test_disabled_by_default(self):
        assert load_ai_config(env={}).allow_image_bytes is False
        assert FakeAIProvider().capabilities.supports_image_bytes is False
        assert self._openai().capabilities.supports_image_bytes is False
        with pytest.raises(AIRequestError):
            FakeAIProvider().analyze_image_bytes(PNG_BYTES, ocr_text="t")

    def test_config_parsing(self):
        assert load_ai_config(env={AIEnvNames.IMAGE_BYTES: "true"}).allow_image_bytes is True
        assert load_ai_config(
            env={AIEnvNames.IMAGE_BYTES: "true"}).describe()["allow_image_bytes"] is True

    def test_sniff_image_mime(self):
        assert sniff_image_mime(PNG_BYTES) == "image/png"
        assert sniff_image_mime(b"\xff\xd8\xff" + b"\x00" * 20) == "image/jpeg"
        assert sniff_image_mime(b"not an image at all........") is None
        assert sniff_image_mime(b"") is None

    def test_vision_payload_shape(self, monkeypatch):
        seen = {}

        class _Resp:
            def read(self):
                return json.dumps({
                    "choices": [{"message": {"content": json.dumps({
                        "summary": "s", "topics": [],
                        "knowledge_points": []})}}]}).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout=None):
            seen["body"] = json.loads(request.data.decode("utf-8"))
            return _Resp()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        provider = self._openai(allow_image_bytes=True, json_mode=False)
        provider.analyze_image_bytes(PNG_BYTES, ocr_text="pizarra", chunk_id="c1")
        content = seen["body"]["messages"][0]["content"]
        assert content[0]["type"] == "text"
        assert "pizarra" in content[0]["text"]
        image_part = content[1]
        assert image_part["type"] == "image_url"
        assert image_part["image_url"]["url"].startswith("data:image/png;base64,")
        assert "response_format" not in seen["body"]
        # json_mode 开时才带 response_format。
        self._openai(allow_image_bytes=True).analyze_image_bytes(PNG_BYTES, ocr_text="t")
        assert seen["body"]["response_format"] == {"type": "json_object"}

    def test_vision_rejects_oversize_and_unknown(self):
        provider = self._openai(allow_image_bytes=True)
        with pytest.raises(AIRequestError):
            provider.analyze_image_bytes(b"", ocr_text="t")
        with pytest.raises(AIRequestError):
            provider.analyze_image_bytes(b"not-an-image-bytes.........", ocr_text="t")
        with pytest.raises(AIRequestError):
            provider.analyze_image_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_IMAGE_BYTES + 1),
                                         ocr_text="t")

    def test_service_sends_bytes_only_for_images(self, tmp_path):
        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        stub = _VisionStubProvider()
        ws.configure_ai(enabled=True, provider=stub)
        png = _write(tmp_path, "board.png", PNG_BYTES, binary=True)
        image_record = ws.register_material(cid, png)
        text_record = _register_text(ws, cid, TEXT_ES, filename="n.txt")
        ws.process_material(cid, image_record["material_id"])
        assert stub.image_calls, "vision-capable provider got no image bytes"
        assert stub.image_calls[0]["bytes"][:8] == PNG_BYTES[:8]
        before = len(stub.image_calls)
        ws.process_material(cid, text_record["material_id"])
        assert len(stub.image_calls) == before  # 文本材料绝不发送字节

    def test_vision_failure_is_loud_not_silent(self, tmp_path):
        # vision 调用失败 = chunk 失败（可重试），绝不静默回落 OCR。
        class _BrokenVision(_VisionStubProvider):
            def analyze_image_bytes(self, *args, **kwargs):
                raise AIRequestError("vision boom", detail={"provider": "x"})

        ws = _workspace(tmp_path)
        cid = ws.create_course("C", "C1", "es")["course_id"]
        ws.configure_ai(enabled=True, provider=_BrokenVision())
        png = _write(tmp_path, "board.png", PNG_BYTES, binary=True)
        record = ws.register_material(cid, png)
        job = ws.process_material(cid, record["material_id"])
        assert job["status"] == "SUCCEEDED"  # 材料不受影响
        assert job["ai"]["status"] == "failed"

    @staticmethod
    def _openai(**kw):
        args = {"api_key": "sk-test-placeholder",
                "base_url": "https://example.com/v1", "model": "test-model"}
        args.update(kw)
        return OpenAICompatibleAIProvider(**args)


# ======================================================================
# 9. HTTP E2E: upload -> process -> auto AI -> KP (零手工 KP, §39)
# ======================================================================


def _serve(workspace):
    from src.api.server import create_server

    return create_server(workspace, port=0).start()


def _request(server, method, path, body=None, raw=None, headers=None):
    data = raw if raw is not None else (
        None if body is None else json.dumps(body).encode("utf-8")
    )
    request = urllib.request.Request(server.url + path, data=data, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
            ctype = response.headers.get("Content-Type", "")
            return response.status, json.loads(payload.decode("utf-8")) if "json" in ctype else payload
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class TestHttpAutoPipeline:
    def test_upload_process_auto_ai_e2e(self, tmp_path):
        ws = _workspace(tmp_path)
        _enable(ws)
        server = _serve(ws)
        try:
            status, payload = _request(server, "POST", "/api/courses", {"name": "Cálculo II", "code": "M101"})
            assert status == 201, payload
            cid = payload["data"]["course_id"]
            # 上传真实课程文本 (UAB 风格西语微积分)。
            status, payload = _request(
                server, "POST", "/api/materials?course_id=" + cid,
                raw=TEXT_ES.encode("utf-8"), headers={"X-Filename": "calculo.txt"},
            )
            assert status in (200, 201), payload
            mid = payload["data"]["material_id"]
            # 处理: 自动触发 AI, 无需手动 ai-analyze, 更无手工 KP。
            status, payload = _request(
                server, "POST", "/api/materials/%s/process?course_id=%s" % (mid, cid), {}
            )
            assert status == 200, payload
            job = payload["data"]
            assert job["status"] == "SUCCEEDED", job
            assert job.get("ai", {}).get("status") == AI_AUTO_COMPLETED, job
            # 总结直接可读。
            status, payload = _request(
                server, "GET", "/api/materials/%s/ai-summary?course_id=%s" % (mid, cid)
            )
            assert status == 200, payload
            assert payload["data"]["knowledge_points_total"] >= 1
            # 知识库里有自动 KP, 且全部可追溯。
            status, payload = _request(server, "GET", "/api/knowledge?course_id=" + cid)
            assert status == 200, payload
            auto_kps = [
                k for k in payload["data"]["knowledge_points"]
                if str(k.get("knowledge_id") or "").startswith("aikp-")
            ]
            assert auto_kps
            kid = auto_kps[0]["knowledge_id"]
            status, payload = _request(
                server, "GET", "/api/knowledge/%s/evidence?course_id=%s" % (kid, cid)
            )
            assert status == 200, payload
            assert payload["data"]["evidence"], payload
            # 待确认队列非空 (Fake 次句 0.8)。
            status, payload = _request(server, "GET", "/api/reviews?course_id=" + cid)
            assert status == 200, payload
        finally:
            server.stop()
            ws.close()
