# -*- coding: utf-8 -*-
"""复习包 / 材料摘要的只读装配服务 (P1/§2)。

硬性边界 (与 AGENTS.md 证据优先原则逐条对应):

- **只读**: 只调用 ``KnowledgeService.get_evidence_for_knowledge_point`` /
  ``get_conflicts`` / ``get_coverage`` 与材料注册表的既有查询; 禁止调用
  ``process`` / ``retry``; 禁止写库、写注册表、写任何受管文件。
- **证据链**: 响应里的每段总结都挂产生它的证据 ID 列表; 原文逐字携带,
  绝不改写、绝不翻译。
- **needs_verification=true 强制**: Summary 永远是"待人工核验"状态,
  永不自动 confirm —— 审核状态只能由人改变。
- **冲突并列不裁决**: 命中冲突的证据并列返回, 系统绝不挑边。
- **失败回落**: real 调用失败 / 超时 / 缺 key 时回落抽取版
  (MockSummarizer), 并在响应里明示 ``fallback=True``。
- 出境仅由显式的 digest / review-pack 请求触发, 无后台发送。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from src.llm_provider import (
    DEFAULT_SUMMARY_CHAR_LIMIT,
    DEFAULT_SUMMARY_TIMEOUT_SECONDS,
    PROMPT_VERSION,
)

__all__ = [
    "ReviewPackService",
    "SUMMARY_FALLBACK_NOTICE",
]

#: 回落提示码 (进入 summary 元数据; UI 用 t() 键渲染人话, 不进原文)。
SUMMARY_FALLBACK_NOTICE = "SUMMARY_FALLBACK_EXTRACTIVE"


class ReviewPackService:
    """按材料 / 课程装配"总结 + 证据链"的只读服务。

    依赖全部注入: ``KnowledgeService`` 供证据与冲突, 材料查询函数供
    材料记录, ``SummarizerProvider`` 供总结 (Mock 或 OpenAI 兼容)。
    本服务自己不开库、不建线程、不写任何受管文件 —— classroom-data/
    零写入。
    """

    def __init__(
        self,
        *,
        knowledge_points_fn: Any,
        evidence_for_kp_fn: Any,
        conflicts_fn: Any,
        coverage_fn: Any,
        evidence_for_material_fn: Any,
        material_record_fn: Any,
        summarizer: Any,
        llm_mode: str = "mock",
        timeout_seconds: int = DEFAULT_SUMMARY_TIMEOUT_SECONDS,
        char_limit: int = DEFAULT_SUMMARY_CHAR_LIMIT,
    ) -> None:
        # 全部依赖以**函数**注入 (只读查询), 本服务不知道 Workspace 内部结构,
        # 更拿不到任何可写入口。
        self._knowledge_points_fn = knowledge_points_fn
        self._evidence_for_kp_fn = evidence_for_kp_fn
        self._conflicts_fn = conflicts_fn
        self._coverage_fn = coverage_fn
        self._evidence_for_material_fn = evidence_for_material_fn
        self._material_record_fn = material_record_fn
        self._summarizer = summarizer
        self._llm_mode = llm_mode
        self._timeout = int(timeout_seconds)
        self._char_limit = int(char_limit)

    # ------------------------------------------------------------------
    # 内部工具 (顺序稳定: evidence_id 升序)
    # ------------------------------------------------------------------

    @staticmethod
    def _group_by_material(
        evidence_dicts: Sequence[Mapping[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for ev in evidence_dicts or ():
            source = (ev or {}).get("source") or {}
            material_id = str(source.get("material_id") or "")
            grouped.setdefault(material_id, []).append(dict(ev))
        for key in grouped:
            grouped[key].sort(key=lambda e: str(e.get("evidence_id") or ""))
        return grouped

    def _material_filename(self, material_id: str) -> str:
        try:
            record = self._material_record_fn(material_id) or {}
        except Exception:  # noqa: BLE001 - 查不到文件名不能让总结失败
            record = {}
        return str(record.get("filename") or material_id)

    def _summarize_with_fallback(self, text: str, ids: Sequence[str]) -> dict[str, Any]:
        """调用注入的 provider; 失败/超时回落抽取版 (对用户绝不 5xx)。"""
        from src.llm_provider import MockSummarizer

        try:
            result = self._summarizer.summarize(
                text,
                tuple(str(i) for i in ids),
                timeout_seconds=self._timeout,
                char_limit=self._char_limit,
            )
        except Exception:  # noqa: BLE001 - 回落是产品语义, 不是吞错误
            result = MockSummarizer().summarize(
                text,
                tuple(str(i) for i in ids),
                timeout_seconds=self._timeout,
                char_limit=self._char_limit,
            )
        fallback = bool(result.fallback)
        return {
            "text": result.text,
            "model": result.model,
            "prompt_version": getattr(result, "prompt_version", None) or PROMPT_VERSION,
            "truncated": bool(result.truncated),
            "fallback": fallback,
            "fallback_notice": SUMMARY_FALLBACK_NOTICE if fallback else None,
            "evidence_ids": list(result.evidence_ids),
            # 铁律: Summary 永远待人工核验, 永不自动 confirm。
            "needs_verification": True,
        }

    @staticmethod
    def _conflicts_touching(
        conflicts: Sequence[Mapping[str, Any]], evidence_ids: set[str]
    ) -> list[dict[str, Any]]:
        """命中所给证据的冲突记录 (并列返回, 绝不裁决)。"""
        out = []
        for conflict in conflicts or ():
            refs = {str(r) for r in (conflict.get("evidence_refs") or [])}
            if refs & evidence_ids:
                out.append(dict(conflict))
        return out

    # ------------------------------------------------------------------
    # 公开: 单材料摘要 (GET /api/materials/{id}/digest)
    # ------------------------------------------------------------------

    def material_digest(self, course_id: str, material_id: str) -> dict[str, Any]:
        """单材料的中文总结 + 证据链 (只读, 不落盘)。"""
        material_id = str(material_id)
        evs = [
            dict(ev)
            for ev in (self._evidence_for_material_fn(course_id, material_id) or ())
        ]
        evs.sort(key=lambda e: str(e.get("evidence_id") or ""))
        ids = [str(ev.get("evidence_id") or "") for ev in evs]
        text = "\n\n".join(
            f"[{ev.get('evidence_id')}] {ev.get('content', '')}" for ev in evs
        )
        summary = self._summarize_with_fallback(text, [i for i in ids if i])
        return {
            "material_id": material_id,
            "course_id": str(course_id),
            "filename": self._material_filename(material_id),
            "summary": summary,
            "evidence": evs,
            "conflicts": self._conflicts_touching(
                self._conflicts_fn(course_id) or [], set(ids)
            ),
            "input_evidence_ids": [i for i in ids if i],
            "llm_mode": self._llm_mode,
        }

    # ------------------------------------------------------------------
    # 公开: 课程合并包 (GET /api/courses/{id}/review-pack)
    # ------------------------------------------------------------------

    def course_review_pack(self, course_id: str) -> dict[str, Any]:
        """全课程的合并复习包: 每份材料一段总结 + 课程级冲突并列。

        课程证据 = 该课程每个知识点的支撑证据
        (``get_evidence_for_knowledge_point`` 的并集, 按 evidence_id 去重)。
        """
        course_id = str(course_id)
        knowledge_points = [
            dict(kp) for kp in (self._knowledge_points_fn(course_id) or [])
        ]
        conflicts = [dict(c) for c in (self._conflicts_fn(course_id) or [])]

        unique: dict[str, dict[str, Any]] = {}
        for kp in knowledge_points:
            for ev in self._evidence_for_kp_fn(kp.get("knowledge_id") or "") or []:
                record = dict(ev)
                eid = str(record.get("evidence_id") or "")
                if eid and eid not in unique:
                    unique[eid] = record
        evidence_dicts = [unique[key] for key in sorted(unique)]

        grouped = self._group_by_material(evidence_dicts)

        digests: list[dict[str, Any]] = []
        for material_id in sorted(grouped):
            evs = grouped[material_id]
            ids = [str(ev.get("evidence_id") or "") for ev in evs]
            text = "\n\n".join(
                f"[{ev.get('evidence_id')}] {ev.get('content', '')}" for ev in evs
            )
            summary = self._summarize_with_fallback(text, [i for i in ids if i])
            digests.append(
                {
                    "material_id": material_id,
                    "filename": self._material_filename(material_id),
                    "summary": summary,
                    "evidence": evs,
                    "conflicts": self._conflicts_touching(conflicts, set(ids)),
                    "input_evidence_ids": [i for i in ids if i],
                }
            )

        try:
            coverage = self._coverage_fn(course_id)
        except Exception:  # noqa: BLE001 - 覆盖分析失败不阻塞复习包
            coverage = None

        return {
            "course_id": course_id,
            "llm_mode": self._llm_mode,
            "prompt_version": PROMPT_VERSION,
            "knowledge_point_count": len(knowledge_points),
            "knowledge_points": knowledge_points,
            "digests": digests,
            # 课程级冲突只并列、不裁决 (铁律)。
            "conflicts": conflicts,
            "coverage": coverage,
            "needs_verification": True,
        }
