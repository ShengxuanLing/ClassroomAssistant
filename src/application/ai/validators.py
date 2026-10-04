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
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Mapping, Optional, Sequence

from src.application.ai.prompts import ALLOWED_KNOWLEDGE_TYPES
from src.application.ai.schemas import GlossaryEntry, KnowledgeCandidate, KpZhResult
from src.knowledge_validation import knowledge_score_from_counts

__all__ = [
    "AI_PIPELINE_POLICY",
    "ConfidenceDecision",
    "GroundedCandidate",
    "classify_confidence",
    "ground_candidates",
    "ground_glossary",
    "ground_kp_zh",
    "GroundedGlossary",
    "glossary_id",
    "kp_translation_id",
    "EVIDENCE_COPY_SIMILARITY_THRESHOLD",
    "evidence_copy_similarity",
    "candidate_copy_reason",
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


#: Exact source equality is always rejected.  Fuzzy rejection is deliberately
#: stricter than a generic similarity cutoff: substantial ordered coverage plus a
#: long contiguous run is required, so preserved/reordered terminology alone is
#: not mistaken for a copied explanation.
EVIDENCE_COPY_SIMILARITY_THRESHOLD = 0.90
_COPY_MIN_UNITS = 6
_COPY_MIN_CHARS = 40
_COPY_MIN_RUN_RATIO = 0.60
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _normalise_copy_text(value: Any) -> str:
    """Case/accent/punctuation-insensitive text used only for copy detection."""
    decomposed = unicodedata.normalize("NFKD", str(value or "")).casefold()
    plain = "".join(
        char if char.isalnum() else " "
        for char in decomposed
        if not unicodedata.combining(char)
    )
    return " ".join(plain.split())


def _copy_units(value: str) -> tuple[list[str], int]:
    """Return ordered word tokens, or character bigrams for unsegmented CJK."""
    compact = value.replace(" ", "")
    char_count = len(compact)
    if _CJK_RE.search(compact):
        if len(compact) < 2:
            return ([compact] if compact else []), char_count
        return [compact[index:index + 2] for index in range(len(compact) - 1)], char_count
    return _WORD_RE.findall(value), char_count


def _copy_metrics(candidate: str, evidence: str) -> tuple[float, int]:
    candidate_units, _ = _copy_units(candidate)
    evidence_units, _ = _copy_units(evidence)
    if not candidate_units or not evidence_units:
        return 0.0, 0
    matcher = SequenceMatcher(
        None, candidate_units, evidence_units, autojunk=False
    )
    blocks = matcher.get_matching_blocks()
    matched = sum(block.size for block in blocks)
    longest_run = max((block.size for block in blocks), default=0)
    return matched / len(candidate_units), longest_run


def evidence_copy_similarity(candidate_text: str, evidence_text: str) -> float:
    """Return ordered source-token coverage in the 0..1 range."""
    candidate = _normalise_copy_text(candidate_text)
    evidence = _normalise_copy_text(evidence_text)
    if not candidate or not evidence:
        return 0.0
    if candidate == evidence:
        return 1.0
    return _copy_metrics(candidate, evidence)[0]


def _candidate_knowledge_content(candidate: KnowledgeCandidate) -> str:
    """The exact prose mapped to ``KnowledgePoint.content`` (without evidence)."""
    parts = [candidate.description] if candidate.description else []
    if candidate.examples:
        parts.append("例子: " + " / ".join(candidate.examples[:5]))
    if candidate.relations:
        parts.append("关联: " + " / ".join(candidate.relations[:5]))
    return "\n".join(parts)


def candidate_copy_reason(
    candidate: KnowledgeCandidate,
    *,
    evidence_ids: Sequence[str],
    evidence_texts: Optional[Mapping[str, Any]],
) -> Optional[str]:
    """Explain a source quotation without echoing source text in the reason."""
    # ``None`` preserves the public helper's legacy unit-level call contract.
    # The production pipeline supplies this map explicitly.
    if evidence_texts is None:
        return None
    for evidence_id in evidence_ids or ():
        raw_text = evidence_texts.get(str(evidence_id), "")
        evidence_text = str(getattr(raw_text, "content", raw_text) or "").strip()
        if not evidence_text:
            return (
                "evidence text unavailable for candidate/evidence separation "
                "validation: %s" % evidence_id
            )

        evidence_normal = _normalise_copy_text(evidence_text)
        title_normal = _normalise_copy_text(candidate.title)
        if title_normal == evidence_normal:
            return "candidate title copies evidence exactly: %s" % evidence_id
        title_units, title_chars = _copy_units(title_normal)
        title_score, title_run = _copy_metrics(title_normal, evidence_normal)
        if (
            len(title_units) >= _COPY_MIN_UNITS
            and title_chars >= _COPY_MIN_CHARS
            and title_score >= EVIDENCE_COPY_SIMILARITY_THRESHOLD
            and title_run >= _COPY_MIN_UNITS
            and title_run / len(title_units) >= _COPY_MIN_RUN_RATIO
        ):
            return (
                "candidate title closely copies evidence %s "
                "(ordered-coverage=%.2f)" % (evidence_id, title_score)
            )

        description = _normalise_copy_text(candidate.description)
        content = _normalise_copy_text(_candidate_knowledge_content(candidate))
        if description and description == evidence_normal:
            return "candidate content copies evidence exactly: %s" % evidence_id
        if not description:
            continue
        units, char_count = _copy_units(content)
        score, longest_run = _copy_metrics(content, evidence_normal)
        substantial = len(units) >= _COPY_MIN_UNITS and char_count >= _COPY_MIN_CHARS
        if (
            substantial
            and score >= EVIDENCE_COPY_SIMILARITY_THRESHOLD
            and longest_run >= _COPY_MIN_UNITS
            and longest_run / len(units) >= _COPY_MIN_RUN_RATIO
        ):
            return (
                "candidate content closely copies evidence %s "
                "(ordered-coverage=%.2f)" % (evidence_id, score)
            )
    return None



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
    evidence_texts: Optional[Mapping[str, Any]] = None,
    policy: Optional[Mapping[str, float]] = None,
) -> tuple[list[GroundedCandidate], list[GroundedCandidate]]:
    """Evidence grounding (防幻觉核心)。

    - 候选引用的每个 chunk ref 必须存在于 ``chunk_to_evidence`` 且翻出的
      Evidence 属于当前 Material, 否则该候选 ``rejected`` (附原因)。
    - 候选 ``title``/``description`` 若与其已解析 Evidence 原文相同或达到
      异常高相似度，则 ``rejected``；拒绝记录仍保留合法 ``evidence_ids``。
    - 无 ``evidence_texts`` 时保持旧调用兼容，仅执行引用/置信度校验。
    - 无任何合法 Evidence 的候选**永远**不能是 ``auto`` (即使 confidence
      再高也只能 ``review`` —— 且是"缺证据" 的 review)。
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
        copy_reason = candidate_copy_reason(
            candidate,
            evidence_ids=resolved,
            evidence_texts=evidence_texts,
        )
        if copy_reason is not None:
            rejected.append(
                GroundedCandidate(
                    candidate=candidate,
                    evidence_ids=resolved,
                    decision="reject",
                    reject_reason=copy_reason,
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


def glossary_id(*, course_id: str, term: str) -> str:
    """Deterministic ID for a bilingual term card (idempotent report output)."""
    raw = "|".join(
        [str(course_id or ""), str(term or "").strip().lower()]
    ).encode("utf-8")
    return "gls-" + hashlib.sha256(raw).hexdigest()[:16]


def kp_translation_id(
    *,
    knowledge_id: str,
    content_hash: str,
    prompt_version: str,
) -> str:
    """Cache identity of one knowledge-point translation.

    Content **and** prompt version are part of the key on purpose: editing a
    knowledge point, or changing the translation prompt, must produce a
    different cache entry instead of silently serving a stale explanation.
    """
    raw = "|".join(
        [
            str(knowledge_id or ""),
            str(content_hash or ""),
            str(prompt_version or ""),
        ]
    ).encode("utf-8")
    return "kpzh-" + hashlib.sha256(raw).hexdigest()[:16]


def _glossary_term_is_present(term: str, texts: Sequence[Any]) -> bool:
    """Check a term against Evidence with the same accent/case normalisation."""
    needle = _normalise_copy_text(term)
    if not needle:
        return False
    for value in texts or ():
        raw = getattr(value, "content", value)
        haystack = _normalise_copy_text(raw)
        start = 0
        while True:
            index = haystack.find(needle, start)
            if index < 0:
                break
            end = index + len(needle)
            before_ok = index == 0 or not haystack[index - 1].isalnum()
            after_ok = end == len(haystack) or not haystack[end].isalnum()
            if before_ok and after_ok:
                return True
            start = index + 1
    return False


def _glossary_kp_id(
    term: str,
    kp_titles: Sequence[Any],
) -> Optional[str]:
    needle = _normalise_copy_text(term)
    for item in kp_titles or ():
        if isinstance(item, Mapping):
            title = str(item.get("title") or "")
            candidate_id = item.get("knowledge_id") or item.get("kp_id")
        else:
            title = str(item or "")
            candidate_id = None
        title_normal = _normalise_copy_text(title)
        if title_normal == needle or (needle and needle in title_normal):
            if candidate_id:
                return str(candidate_id)
    return None


class GroundedGlossary(dict):
    """JSON-friendly glossary card with attribute access for unit callers."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:  # pragma: no cover - normal attribute protocol
            raise AttributeError(name) from exc


def ground_glossary(
    entries: Sequence[GlossaryEntry],
    *,
    chunk_to_evidence: Mapping[str, str],
    material_evidence_ids: Sequence[str],
    evidence_texts: Optional[Mapping[str, Any]] = None,
    course_id: str = "",
    kp_titles: Sequence[Any] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Ground bilingual terms against the current material's Evidence.

    The model may only propose a term that can be found in the Evidence it
    cites.  Invalid references and rewritten terms are retained as rejected
    diagnostics, never sent to the KP/Review queue.  Language uncertainty is a
    visible value (``[语言待确认]``), not a fabricated ``es``/``ca`` guess.
    """
    valid_ids = {str(value) for value in (material_evidence_ids or ())}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_terms: set[str] = set()
    for entry in entries or ():
        if isinstance(entry, Mapping):
            term = str(entry.get("term") or "").strip()
            raw_lang = str(entry.get("lang") or "").strip()
            zh = str(entry.get("zh") or "").strip()
            refs = list(entry.get("evidence_refs") or entry.get("evidence_ids") or [])
        else:
            term = str(getattr(entry, "term", "") or "").strip()
            raw_lang = str(getattr(entry, "lang", "") or "").strip()
            zh = str(getattr(entry, "zh", "") or "").strip()
            refs = list(getattr(entry, "evidence_refs", None) or getattr(entry, "evidence_ids", None) or [])
        resolved: list[str] = []
        illegal: list[str] = []
        for ref in refs:
            evidence_id = chunk_to_evidence.get(str(ref))
            if evidence_id is None and str(ref) in valid_ids:
                evidence_id = str(ref)
            if evidence_id is None or str(evidence_id) not in valid_ids:
                illegal.append(str(ref))
            elif str(evidence_id) not in resolved:
                resolved.append(str(evidence_id))
        base = GroundedGlossary({
            "term": term[:120],
            "lang": raw_lang[:8],
            "zh": zh[:20],
            "evidence_refs": [str(ref) for ref in refs],
            "evidence_ids": list(resolved),
        })
        if not term or not zh:
            base["reject_reason"] = "missing term or Chinese explanation"
            rejected.append(base)
            continue
        if illegal or not resolved:
            base["reject_reason"] = (
                "illegal evidence refs: %s" % ", ".join(illegal[:5])
                if illegal
                else "no evidence grounding"
            )
            rejected.append(base)
            continue
        referenced_texts: list[Any] = []
        if evidence_texts is not None:
            for evidence_id in resolved:
                if evidence_id in evidence_texts:
                    referenced_texts.append(evidence_texts[evidence_id])
        # A missing text map fails closed.  This is the same evidence-first
        # rule used for KP candidates: no text, no grounded glossary card.
        if evidence_texts is None or not referenced_texts or not all(
            str(getattr(value, "content", value) or "").strip()
            for value in referenced_texts
        ):
            base["reject_reason"] = "evidence text unavailable for glossary grounding"
            rejected.append(base)
            continue
        if not _glossary_term_is_present(term, referenced_texts):
            base["reject_reason"] = "term is not a verbatim evidence substring"
            rejected.append(base)
            continue
        normalized_term = _normalise_copy_text(term)
        if normalized_term in seen_terms:
            base["reject_reason"] = "duplicate glossary term"
            rejected.append(base)
            continue
        seen_terms.add(normalized_term)
        language = raw_lang.casefold()
        if language not in {"es", "ca"}:
            language = "[语言待确认]"
        item = GroundedGlossary({
            "glossary_id": glossary_id(course_id=course_id, term=term),
            "term": term[:120],
            "lang": language,
            "zh": zh[:20],
            "evidence_refs": list(resolved),
            "evidence_ids": list(resolved),
            "kp_id": _glossary_kp_id(term, kp_titles),
        })
        accepted.append(item)
    return accepted, rejected


def ground_kp_zh(
    result: Optional[KpZhResult],
    *,
    knowledge_id: str,
    source_texts: Sequence[Any],
    evidence_ids: Optional[Sequence[str]] = None,
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
    """Ground one knowledge point's Chinese explanation against its own source.

    ``translation_zh`` is an explanation layer, so it is deliberately **not**
    compared to the Spanish text (a translation will always look different).
    What must hold is narrower and checkable:

    - the knowledge point must actually say something (empty title *and*
      content -> refuse, rather than asking a model to invent one);
    - every term card must be a verbatim substring of **this** knowledge
      point, using the same accent/case-insensitive rule as the glossary;
    - every evidence reference must belong to **this** knowledge point.

    A knowledge point without evidence gets no explanation layer: an
    unsupported translation is exactly the "Chinese text with no source"
    failure this project refuses everywhere else.
    """
    rejected: list[dict[str, Any]] = []
    texts = [str(value or "") for value in (source_texts or ())]
    joined = "\n".join(texts)
    refs = [str(value) for value in (evidence_ids or ()) if str(value)]
    if result is None or not result.translation_zh.strip():
        return None, rejected
    if not joined.strip():
        return None, rejected
    if not refs:
        return None, [
            {
                "reason": "knowledge point has no evidence to ground a translation",
                "terms": [],
            }
        ]
    terms: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in result.terms_zh or []:
        term = str(getattr(entry, "term", "") or "").strip()
        zh = str(getattr(entry, "zh", "") or "").strip()
        card_refs = [
            str(value)
            for value in (
                getattr(entry, "evidence_refs", None)
                or getattr(entry, "evidence_ids", None)
                or []
            )
            if str(value)
        ]
        if not term or not zh:
            rejected.append({"term": term, "reason": "missing term or Chinese explanation"})
            continue
        unknown_refs = [value for value in card_refs if value not in refs]
        if unknown_refs:
            rejected.append(
                {
                    "term": term,
                    "reason": "illegal evidence refs: %s" % ", ".join(unknown_refs[:5]),
                }
            )
            continue
        if not _glossary_term_is_present(term, texts):
            rejected.append(
                {"term": term, "reason": "term is not a verbatim knowledge-point substring"}
            )
            continue
        normalized = _normalise_copy_text(term)
        if normalized in seen:
            continue
        seen.add(normalized)
        language = str(getattr(entry, "lang", "") or "").casefold()
        if language not in {"es", "ca"}:
            language = "[语言待确认]"
        terms.append(
            {
                "term": term[:120],
                "lang": language,
                "zh": zh[:20],
                "evidence_refs": card_refs or list(refs),
            }
        )
    grounded_refs = [
        value for value in result.evidence_refs if value in refs
    ] or list(refs)
    return (
        {
            "knowledge_id": str(knowledge_id or ""),
            "translation_zh": result.translation_zh,
            "terms_zh": terms,
            "evidence_refs": grounded_refs,
            "terms_rejected": rejected,
        },
        rejected,
    )


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

    # ``content`` is knowledge prose, never the Evidence payload itself.  Keep
    # source quotations confined to explicitly labelled examples/terms.
    - ``content`` = abstracted description + examples/relations (原文术语保留在
      ``original_terms``；逐字 Evidence 只留在 evidence store，不作为知识点正文)。
    - ``confidence``: 0.9+ -> HIGH, 0.7+ -> MEDIUM, 否则 LOW。
    - ``knowledge_score``: 由 grounding 后的**证据条数**经
      ``knowledge_score_from_counts`` 派生 (与确定性管线共用同一公式),
      **不是** LLM 自报的 confidence。后者只以 ``confidence`` 档位
      (HIGH/MEDIUM/LOW) 与 ``metadata.ai_confidence`` 表达。
    - ``needs_verification``: review 候选恒 True (人工确认前不宣称已验证)。
    - ``review_status``: ``auto`` 候选 (confidence >= 0.90 且证据逐字命中)
      直接落 ``confirmed`` —— TASK-81 §B: 模型自己有把握的那批不再排人工队,
      兜底只剩证据链 + 拒绝按钮。``review`` 候选 (含冲突候选, 它们都以
      decision="review" 落库) 仍是 ``pending``: 那正是"模型自己说不准"的
      一批, 全放过等于把"不静默合并"的护栏删掉。
    - ``validation_status`` 保持 ``unverified`` (验证器照常重算); **verified
      语义与审核语义是两条轴**, 自动确认不越过验证。
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
    importance = (
        candidate.importance
        if candidate.importance in ("high", "medium", "low")
        else _KP_TYPE_IMPORTANCE.get(kp_type, "medium")
    )
    original_terms = list(candidate.original_terms or [])
    if candidate.title and candidate.title not in original_terms:
        original_terms = [candidate.title] + original_terms
    content = _candidate_knowledge_content(candidate)
    evidence_refs = list(dict.fromkeys(str(ref) for ref in grounded.evidence_ids if str(ref)))
    support_count = len(evidence_refs)
    knowledge_score = knowledge_score_from_counts(support_count)
    return {
        "knowledge_id": candidate_knowledge_id(
            course_id=course_id, title=candidate.title, kp_type=kp_type
        ),
        "title": candidate.title[:80] if candidate.title else kp_type,
        "content": content[:4000],
        "original_terms": original_terms[:20],
        "importance": importance,
        "confidence": confidence,
        "evidence_refs": evidence_refs,
        "related_points": [],
        "needs_verification": grounded.decision != "auto",
        "validation_status": "unverified",
        "knowledge_score": knowledge_score,
        # TASK-81 §B: 只有 auto 候选自动确认。review / 冲突候选保持 pending。
        "review_status": "confirmed" if grounded.decision == "auto" else "pending",
        "metadata": {
            "origin": "ai-pipeline",
            "ai_type": kp_type,
            "ai_confidence": round(confidence_value, 4),
            "ai_decision": grounded.decision,
            "review_status_source": (
                "ai_auto_accept" if grounded.decision == "auto" else "review_queue"
            ),
            "material_id": material_id,
            "content_language": content_language,
            "knowledge_score_source": {
                "kind": "grounding_evidence_count",
                "formula": "knowledge_score_from_counts",
                "formula_version": "evidence-support-v1",
                "support_count": support_count,
                "conflict_count": 0,
                "value": knowledge_score,
            },
        },
    }
