"""守卫：knowledge_score 在任何写入路径上都只能表示「证据支持度」。

背景（2026-09-21 实测）
----------------------
AI 提取路径（``src/application/ai/validators.py``）曾把 LLM 自报的
``confidence`` 直接写进 ``knowledge_score``，产出 1.0 / 0.95 —— 确定性公式
**不可能输出**的值（其值域恰为 0.0 / 0.5 / 0.75 / 0.9）。同一个数据库列因此
承载了两套互不兼容的语义：用户看到「分数 1.0」却同时看到 ``unverified``。

修复把公式提升为公开的单一真源 ``knowledge_score_from_counts``，两条路径
都经它取值。本文件守住这件事，而不只是守住当前这一版实现的拼法。

详见 ``docs/status.md`` 的「修复 — 「分数」列显示的不是它声称的东西」。
"""

from __future__ import annotations

import inspect
import os
import re
import tempfile

from src.application.ai import service as ai_service
from src.application.ai import validators as ai_validators
from src.application.ai.provider import FakeAIProvider
from src.application.ai.schemas import KnowledgeCandidate
from src.application.ai.validators import (
    GroundedCandidate,
    map_candidate_to_kp_payload,
)
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace
from src.knowledge_validation import (
    KnowledgeValidator,
    _knowledge_score,
    knowledge_score_from_counts,
)
from src.models import (
    Confidence,
    Evidence,
    EvidenceType,
    KnowledgePoint,
    Language,
)

#: 确定性公式的全部可能输出。任何落在这个集合之外的 knowledge_score
#: 都意味着某条写入路径绕过了公式。
DETERMINISTIC_RANGE = {0.0, 0.5, 0.75, 0.9}

#: 修复前真实存在过的那一行（用于自证判据不是空跑）。
REMOVED_SHAPE = '"knowledge_score": round(max(0.0, min(1.0, confidence_value)), 4),'

#: 把 "confidence 相关变量 clamp 进 knowledge_score" 这种形状抓出来的正则。
_LEAK_PATTERN = re.compile(
    r"knowledge_score['\"]\s*:\s*round\([^)]*confidence"
)


def _make_evidence(index: int) -> Evidence:
    return Evidence(
        evidence_id="ev-%d" % index,
        content="contenido %d" % index,
        language=Language.from_string("es"),
        source_reference={},
        confidence=Confidence.HIGH,
        evidence_type=EvidenceType.TRANSCRIPT,
    )


def _payload(confidence: float, evidence_ids, decision: str = "review") -> dict:
    """走真实的 AI 映射函数产出一个 KP payload。"""
    candidate = KnowledgeCandidate(
        title="Reglas de evaluacion: Nota minima",
        description="Nota final ponderada minima de 3/10.",
        kp_type="concept",
        importance="high",
        confidence=confidence,
        evidence_refs=list(evidence_ids),
    )
    grounded = GroundedCandidate(
        candidate=candidate,
        evidence_ids=list(evidence_ids),
        decision=decision,
    )
    return map_candidate_to_kp_payload(
        grounded, course_id="course-x", material_id="material-x"
    )


class TestAiPathUsesTheDeterministicFormula:
    """AI 路径的取值必须落在确定性值域内，且与验证器逐点一致。"""

    def test_score_never_leaves_the_deterministic_range(self):
        """LLM confidence 的整个 [0,1] 区间都不许漏进 knowledge_score。"""
        offenders = []
        for confidence in (0.0, 0.35, 0.5, 0.7, 0.9, 0.95, 0.99, 1.0):
            for n_evidence in range(0, 6):
                refs = ["ev-%d" % i for i in range(n_evidence)]
                score = _payload(confidence, refs)["knowledge_score"]
                if score not in DETERMINISTIC_RANGE:
                    offenders.append((confidence, n_evidence, score))
        assert offenders == [], (
            "AI 路径产出了确定性公式不可能输出的分数: %r" % (offenders,)
        )

    def test_score_matches_the_validator_point_by_point(self):
        """同一份证据，两条路径必须给出同一个答案。"""
        evidences = [_make_evidence(i) for i in range(3)]
        for n_evidence in range(0, 4):
            refs = ["ev-%d" % i for i in range(n_evidence)]
            kp = KnowledgePoint(
                knowledge_id="kp-x",
                title="t",
                content="c",
                evidence_refs=list(refs),
            )
            expected = KnowledgeValidator.validate_knowledge_point(
                kp, evidences, []
            ).knowledge_score
            assert _payload(1.0, refs)["knowledge_score"] == expected, (
                "n_evidence=%d 时两条路径不一致" % n_evidence
            )

    def test_llm_confidence_does_not_move_the_score(self):
        """证据条数相同时，LLM 说 0.70 还是 1.00，分数必须一样。"""
        refs = ["ev-0"]
        assert (
            _payload(0.70, refs)["knowledge_score"]
            == _payload(1.00, refs)["knowledge_score"]
        )

    def test_llm_confidence_survives_as_a_separate_signal(self):
        """解耦不等于丢弃 —— 置信度仍在 confidence 档位与 metadata 里。"""
        payload = _payload(0.95, ["ev-0"])
        assert payload["confidence"] == "HIGH"
        assert payload["metadata"]["ai_confidence"] == 0.95

    def test_one_evidence_means_half_not_one(self):
        """用户报的那个具体数字：1 条证据 + 高置信度 LLM 不再显示 1.0。"""
        assert _payload(1.00, ["ev-0"])["knowledge_score"] == 0.5
        assert _payload(0.95, ["ev-0"])["knowledge_score"] == 0.5


class TestTheSingleSourceOfTruth:
    """公开包装函数必须就是那个私有公式本身。"""

    def test_public_wrapper_is_the_private_formula(self):
        for n_support in range(0, 8):
            for n_conflict in range(0, 4):
                assert knowledge_score_from_counts(
                    n_support, n_conflict
                ) == _knowledge_score(n_support, n_conflict)

    def test_public_wrapper_defaults_to_no_conflict(self):
        for n_support in range(0, 5):
            assert knowledge_score_from_counts(
                n_support
            ) == knowledge_score_from_counts(n_support, 0)


class TestStructuralGuards:
    """钉住"绕过公式"这个形状，而不是钉住某一版拼法。"""

    def test_validators_goes_through_the_shared_formula(self):
        source = inspect.getsource(ai_validators)
        assert "knowledge_score_from_counts" in source

    def test_validators_never_clamps_llm_confidence_into_the_score(self):
        source = inspect.getsource(ai_validators)
        assert _LEAK_PATTERN.findall(source) == [], (
            "AI 路径又把 LLM 自报置信度写进 knowledge_score 了"
        )

    def test_the_guard_catches_the_removed_shape(self):
        """自证不是空跑：把修复前真实存在的那一行喂给同一条判据。"""
        assert _LEAK_PATTERN.findall(REMOVED_SHAPE), (
            "判据抓不住修复前的那一行，说明它是个空断言"
        )

    def test_attach_path_rescores_too(self):
        """挂新证据后必须重算，否则分数会与 evidence_refs 条数脱节。"""
        source = inspect.getsource(ai_service)
        assert "knowledge_score_from_counts(len(merged_refs))" in source

    def test_mapping_docstring_says_the_score_is_not_llm_confidence(self):
        """文档口径也要被钉住 —— 否则下一个人又会照着旧注释写回去。"""
        doc = ai_validators.map_candidate_to_kp_payload.__doc__ or ""
        assert "knowledge_score" in doc
        assert "不是" in doc


#: 端到端用的西语素材（含多个可抽取的候选，FakeAIProvider 零出站）。
TEXT_ES = (
    "La integración por partes es un método fundamental. "
    "La definición de integral definida es el área bajo la curva. "
    "Por ejemplo, la integral de x es x al cuadrado sobre dos. "
    "El teorema fundamental del cálculo conecta derivación e integración."
)


def _register_text(ws, course_id, text, filename="lecture.txt"):
    handle, path = tempfile.mkstemp(suffix="-" + filename, dir=tempfile.gettempdir())
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(text)
    try:
        return ws.register_material(course_id, path, filename=filename)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


class TestEndToEndIngestionKeepsTheScoreHonest:
    """真落库 + 真读回：分数不能在持久化链路上被写坏。

    上面的类只钉住映射函数（payload 层）。这一条走完整链路 ——
    ``register_material`` -> ``process_material``（自动 AI）-> 读回 ——
    因为"算对了但存坏了"是本项目反复出现过的失败形状。
    """

    def _auto_points(self, tmp_path):
        ws = Workspace(
            str(tmp_path / "data"),
            clock=fixed_clock("2026-09-21T09:00:00+00:00"),
            persistence=False,
            asr_mode="mock",
            ocr_mode="mock",
        )
        course_id = ws.create_course("Cálculo II", "M101", "es")["course_id"]
        record = _register_text(ws, course_id, TEXT_ES, filename="calc.txt")
        ws.configure_ai(enabled=True, provider=FakeAIProvider())
        job = ws.process_material(course_id, record["material_id"])
        assert job["status"] == "SUCCEEDED", job
        points = [
            k
            for k in ws.knowledge_points(course_id)
            if str(k.get("knowledge_id") or "").startswith("aikp-")
        ]
        assert points, "AI 开着却没产出 aikp-* 知识点, 下面的断言会空转"
        return points

    def test_persisted_scores_stay_in_the_deterministic_range(self, tmp_path):
        offenders = [
            (k["knowledge_id"], k["knowledge_score"])
            for k in self._auto_points(tmp_path)
            if k["knowledge_score"] not in DETERMINISTIC_RANGE
        ]
        assert offenders == [], "落库后出现了公式产不出的分数: %r" % (offenders,)

    def test_persisted_score_is_a_function_of_its_own_evidence_count(self, tmp_path):
        """最强的不变式：分数必须等于「它自己那几条证据」算出来的值。"""
        for kp in self._auto_points(tmp_path):
            refs = list(kp.get("evidence_refs") or [])
            expected = knowledge_score_from_counts(len(set(refs)))
            assert kp["knowledge_score"] == expected, (
                "%s: 有 %d 条证据, 分数应为 %s, 实际 %s"
                % (kp["knowledge_id"], len(refs), expected, kp["knowledge_score"])
            )
