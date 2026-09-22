# -*- coding: utf-8 -*-
"""AI 候选校验: 语义校验 + evidence grounding + domain 校验 (TASK-76 §9/§20)。

LLM 返回的 ``evidence_refs`` **不能直接相信**::

    AI evidence ref -> 是否存在? -> 是否属于当前 Material?
        -> 是否在合法 chunk/page/timestamp? -> 非法则 rejected/needs_review

绝对不能自动创建无 Evidence 的高置信度 KnowledgePoint。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from src.application.ai.prompts import ALLOWED_KNOWLEDGE_TYPES
from src.application.ai.schemas import KnowledgeCandidate
from src.knowledge_validation import knowledge_score_from_counts

__all__ = [
    "AI_PIPELINE_POLICY",
    "ConfidenceDecision",
    "GroundedCandidate",
    "classify_confidence",
    "ground_candidates",
    "candidate_knowledge_id",
    "map_candidate_to_kp_payload",
]

#: 统一置信度策略 (可测、可覆盖; 阈值调整只改这里)。
#: ``auto_accept``: 高置信度自动加入 (仍需合法 Evidence + 无冲突);
#: ``review``: 待确认; 低于 review 的: 拒绝/复核队列。
AI_PIPELINE_POLICY: dict[str, float] = {
    "auto_accept": 0.90,
    "review": 0.70,
}


@dataclass(frozen=True)
class ConfidenceDecision:
    """置信度裁决。"""

    decision: str  # "auto" | "review" | "reject"
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {"decision": self.decision, "confidence": self.confidence}


def classify_confidence(
    confidence: float,
    policy: Optional[Mapping[str, float]] = None,
) -> ConfidenceDecision:
    """按 ``AI_PIPELINE_POLICY`` 裁决候选去向 (纯函数, 可测)。"""
    table = dict(AI_PIPELINE_POLICY)
    if policy:
        table.update({k: float(v) for k, v in policy.items()})
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        value = 0.0
    if value != value:  # NaN
        value = 0.0
    if value >= table["auto_accept"]:
        return ConfidenceDecision(decision="auto", confidence=value)
    if value >= table["review"]:
        return ConfidenceDecision(decision="review", confidence=value)
    return ConfidenceDecision(decision="reject", confidence=value)


@dataclass
class GroundedCandidate:
    """通过 grounding 的候选: 模型引用的 chunk 已翻回真实 Evidence。"""

    candidate: KnowledgeCandidate
    evidence_ids: list[str] = field(default_factory=list)
    decision: str = "review"
    reject_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "evidence_ids": list(self.evidence_ids),
            "decision": self.decision,
            "reject_reason": self.reject_reason,
        }


def ground_candidates(
    candidates: Sequence[KnowledgeCandidate],
    *,
    chunk_to_evidence: Mapping[str, str],
    material_evidence_ids: Sequence[str],
    policy: Optional[Mapping[str, float]] = None,
) -> tuple[list[GroundedCandidate], list[GroundedCandidate]]:
    """Evidence grounding (防幻觉核心)。

    - 候选引用的每个 chunk ref 必须存在于 ``chunk_to_evidence`` 且翻出的
      Evidence 属于当前 Material, 否则该候选 ``rejected`` (附原因)。
    - 无任何合法 Evidence 的候选**永远**不能是 ``auto`` (即使 confidence
      再高也只能 ``review`` —— 且是"缺证据"的 review)。
    - 返回 ``(grounded, rejected)``。
    """
    valid_ids = {str(e) for e in (material_evidence_ids or ())}
    grounded: list[GroundedCandidate] = []
    rejected: list[GroundedCandidate] = []
    for candidate in candidates or ():
        resolved: list[str] = []
        illegal: list[str] = []
        for ref in candidate.evidence_refs or ():
            evidence_id = chunk_to_evidence.get(str(ref))
            if evidence_id is None and str(ref) in valid_ids:
                # 容忍: 模型直接回填了真实 Evidence ID (prompt 要求 chunk id,
                # 但旧 prompt/手工 fixture 可能直接给 evidence id)。
                evidence_id = str(ref)
            if evidence_id is None or evidence_id not in valid_ids:
                illegal.append(str(ref))
            elif evidence_id not in resolved:
                resolved.append(evidence_id)
        # chunk_id 兜底: 候选来自某 chunk 但 refs 为空 -> 归属其 chunk 的 Evidence。
        if not resolved and not illegal and candidate.chunk_id:
            fallback = chunk_to_evidence.get(candidate.chunk_id)
            if fallback is not None and fallback in valid_ids:
                resolved.append(fallback)
        decision = classify_confidence(candidate.confidence, policy)
        if illegal or not resolved:
            reason = (
                "illegal evidence refs: %s" % (", ".join(illegal[:5]),)
                if illegal
                else "no evidence grounding"
            )
            rejected.append(
                GroundedCandidate(
                    candidate=candidate,
                    evidence_ids=[],
                    decision="reject",
                    reject_reason=reason,
                )
            )
            continue
        # 有合法 Evidence 但原始裁决是 reject (低置信): 进入 review 队列而不是
        # 直接丢弃 —— 低置信度内容仍值得用户看一眼 (Review Center 兜底)。
        final = decision.decision
        if final == "reject":
            final = "review"
        grounded.append(
            GroundedCandidate(
                candidate=candidate,
                evidence_ids=resolved,
                decision=final,
                reject_reason=None,
            )
        )
    return grounded, rejected


_WS_RE = re.compile(r"\s+")


def _normalise_title(title: str) -> str:
    return _WS_RE.sub(" ", (title or "").strip().lower())


def candidate_knowledge_id(
    *,
    course_id: str,
    title: str,
    kp_type: str,
) -> str:
    """AI 候选的确定性 KnowledgePoint ID (幂等基础)。

    命名空间与确定性抽取器 (``kp-<sha16(anchor,type)>``) 不同 ——
    ``aikp-<sha16(course|title|type)>`` —— 因此 AI 与旧链路永远不会
    悄悄复用/覆盖对方的知识点行 (双链路并存不互踩)。
    """
    raw = "|".join(
        [str(course_id), _normalise_title(title), str(kp_type or "concept").lower()]
    ).encode("utf-8")
    return "aikp-" + hashlib.sha256(raw).hexdigest()[:16]


_KP_TYPE_IMPORTANCE = {
    "concept": "medium",
    "definition": "high",
    "formula": "high",
    "procedure": "medium",
    "example": "low",
    "relationship": "medium",
    "fact": "medium",
    "principle": "high",
}


def map_candidate_to_kp_payload(
    grounded: GroundedCandidate,
    *,
    course_id: str,
    material_id: str,
    content_language: Optional[str] = None,
) -> dict[str, Any]:
    """Grounded 候选 -> 现有 ``KnowledgePoint.from_dict`` 可接受的 payload。

    映射到**现有 domain model** (不新增字段/表, 无 migration):

    - ``content`` = description + 例子/关系 (原文术语保留在 ``original_terms``)。
    - ``confidence``: 0.9+ -> HIGH, 0.7+ -> MEDIUM, 否则 LOW。
    - ``knowledge_score``: 由 grounding 后的**证据条数**经
      ``knowledge_score_from_counts`` 派生 (与确定性管线共用同一公式),
      **不是** LLM 自报的 confidence。后者只以 ``confidence`` 档位
      (HIGH/MEDIUM/LOW) 与 ``metadata.ai_confidence`` 表达。
    - ``needs_verification``: review 候选恒 True (人工确认前不宣称已验证)。
    - ``validation_status`` / ``review_status`` 保持初始值 (验证器重算、
      人工确认), 管线绝不私自 CONFIRMED。
    - ``metadata`` 轻量版本记录 (provider/model/prompt/pipeline/chunk),
      **不是**把整个 AI response JSON 塞进 content —— 总结类字段
      (summary/topics/...) 由 ``pipeline.py`` 经知识关系/材料记录返回,
      不污染 KnowledgePoint 行。
    """
    candidate = grounded.candidate
    kp_type = candidate.kp_type if candidate.kp_type in ALLOWED_KNOWLEDGE_TYPES else "concept"
    confidence_value = float(candidate.confidence or 0.0)
    if confidence_value >= 0.9:
        confidence = "HIGH"
    elif confidence_value >= 0.7:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    parts = [candidate.description] if candidate.description else []
    if candidate.examples:
        parts.append("例子: " + " / ".join(candidate.examples[:5]))
    if candidate.relations:
        parts.append("关联: " + " / ".join(candidate.relations[:5]))
    importance = candidate.importance if candidate.importance in ("high", "medium", "low") else _KP_TYPE_IMPORTANCE.get(kp_type, "medium")
    original_terms = list(candidate.original_terms or [])
    if candidate.title and candidate.title not in original_terms:
        original_terms = [candidate.title] + original_terms
    return {
        "knowledge_id": candidate_knowledge_id(
            course_id=course_id, title=candidate.title, kp_type=kp_type
        ),
        "title": candidate.title[:80] if candidate.title else kp_type,
        "content": "\n".join(parts)[:4000],
        "original_terms": original_terms[:20],
        "importance": importance,
        "confidence": confidence,
        "evidence_refs": list(grounded.evidence_ids),
        "related_points": [],
        "needs_verification": grounded.decision != "auto",
        "validation_status": "unverified",
        "knowledge_score": knowledge_score_from_counts(len(set(grounded.evidence_ids))),
        "review_status": "pending",
        "metadata": {
            "origin": "ai-pipeline",
            "ai_type": kp_type,
            "ai_confidence": round(confidence_value, 4),
            "ai_decision": grounded.decision,
            "material_id": material_id,
            "content_language": content_language,
        },
    }
