# -*- coding: utf-8 -*-
"""语义去重 / 合并 / 冲突检测 (TASK-76 §18/§19)。

- ``candidate`` vs ``existing KnowledgePoint``:
  归一化标题完全一致 -> 同一知识点 -> 挂新 Evidence (确定性, 可自动)。
- 标题相似但内容实质不同 -> **冲突候选** -> Review Center (沿用 Task 61
  的 conflict workflow, LLM 绝不自己选边)。
- 低置信度的"可能是同一知识点" -> ``needs_review`` 的合并建议, 绝不静默合并。

本模块只做**提议** (纯函数, 无 IO); 落库与冲突登记由 ``pipeline.py``
经现有 ``KnowledgePipeline`` / ``ReviewService`` 完成。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from src.application.ai.schemas import KnowledgeCandidate

__all__ = [
    "MergeProposal",
    "deduplicate_candidates",
    "propose_merge_with_existing",
    "normalise_title",
]

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[·•\-—_:：，。、； ramen;,.!?！？()（）\[\]\"'「」『』]+", re.UNICODE)


def normalise_title(title: str) -> str:
    """标题归一化 (大小写/空白/标点折叠, 保留原文语义字符)。"""
    text = _WS_RE.sub(" ", (title or "").strip().lower())
    text = _PUNCT_RE.sub("", text)
    return _WS_RE.sub(" ", text).strip()


@dataclass
class MergeProposal:
    """一条合并/冲突提议。"""

    action: str  # "attach" | "create" | "conflict" | "needs_review"
    candidate_index: int
    existing_id: Optional[str] = None
    reason: str = ""
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "candidate_index": self.candidate_index,
            "existing_id": self.existing_id,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
        }


def deduplicate_candidates(
    candidates: Sequence[KnowledgeCandidate],
) -> tuple[list[KnowledgeCandidate], int]:
    """候选集合内去重 (归一化标题 + 类型完全一致 -> 保留首个, 合并 evidence)。

    返回 ``(deduped, dropped)``。confidence 取合并前的**最小值**
    (合并不能凭空提高置信度)。
    """
    seen: dict[tuple[str, str], KnowledgeCandidate] = {}
    dropped = 0
    for candidate in candidates or ():
        key = (normalise_title(candidate.title), (candidate.kp_type or "concept").lower())
        existing = seen.get(key)
        if existing is None:
            seen[key] = candidate
            continue
        dropped += 1
        merged_refs = list(existing.evidence_refs)
        for ref in candidate.evidence_refs or ():
            if ref not in merged_refs:
                merged_refs.append(ref)
        existing.evidence_refs = merged_refs
        existing.confidence = min(float(existing.confidence), float(candidate.confidence))
        for term in candidate.original_terms or ():
            if term not in existing.original_terms:
                existing.original_terms.append(term)
    return list(seen.values()), dropped


def _token_overlap(a: str, b: str) -> float:
    set_a = {t for t in re.split(r"\W+", a.lower()) if t}
    set_b = {t for t in re.split(r"\W+", b.lower()) if t}
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / max(len(set_a), len(set_b))


def propose_merge_with_existing(
    candidates: Sequence[KnowledgeCandidate],
    existing: Sequence[Mapping[str, Any]],
    *,
    candidate_evidence: Optional[Sequence[Sequence[str]]] = None,
    similarity_threshold: float = 0.6,
) -> list[MergeProposal]:
    """候选 vs 已有 KP 的合并提议。

    ``existing`` 为已有知识点字典 (至少 ``knowledge_id`` / ``title`` /
    ``content`` / ``evidence_refs``)。规则:

    - 归一化标题相等 -> ``attach`` (同一知识点, 挂新 Evidence)。
    - 标题 token 重叠 >= 阈值但内容差异大 -> ``conflict`` (交 Review 裁决)。
    - 标题相似 (0.3..阈值) -> ``needs_review`` (合并建议, 不自动执行)。
    - 否则 -> ``create``。
    """
    proposals: list[MergeProposal] = []
    index_by_norm: dict[str, Mapping[str, Any]] = {}
    for kp in existing or ():
        norm = normalise_title(str(kp.get("title") or ""))
        if norm and norm not in index_by_norm:
            index_by_norm[norm] = kp
    for index, candidate in enumerate(candidates or ()):
        norm = normalise_title(candidate.title)
        evidences = list((candidate_evidence or [[]])[index] if candidate_evidence else [])
        direct = index_by_norm.get(norm)
        if direct is not None:
            proposals.append(
                MergeProposal(
                    action="attach",
                    candidate_index=index,
                    existing_id=str(direct.get("knowledge_id")),
                    reason="identical normalised title",
                    evidence_ids=evidences,
                )
            )
            continue
        best: Optional[Mapping[str, Any]] = None
        best_score = 0.0
        for kp in existing or ():
            score = _token_overlap(candidate.title, str(kp.get("title") or ""))
            if score > best_score:
                best_score = score
                best = kp
        if best is not None and best_score >= similarity_threshold:
            old_refs = {str(r) for r in (best.get("evidence_refs") or ())}
            new_refs = {str(r) for r in evidences}
            if new_refs and new_refs != old_refs:
                action = "conflict"
                reason = "similar title but different evidence/content"
            else:
                action = "needs_review"
                reason = "similar title, possible duplicate"
            proposals.append(
                MergeProposal(
                    action=action,
                    candidate_index=index,
                    existing_id=str(best.get("knowledge_id")),
                    reason="%s (similarity=%.2f)" % (reason, best_score),
                    evidence_ids=evidences,
                )
            )
        elif best is not None and best_score >= 0.3:
            proposals.append(
                MergeProposal(
                    action="needs_review",
                    candidate_index=index,
                    existing_id=str(best.get("knowledge_id")),
                    reason="possibly related (similarity=%.2f)" % (best_score,),
                    evidence_ids=evidences,
                )
            )
        else:
            proposals.append(
                MergeProposal(
                    action="create",
                    candidate_index=index,
                    existing_id=None,
                    reason="no similar existing knowledge point",
                    evidence_ids=evidences,
                )
            )
    return proposals
