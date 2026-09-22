# -*- coding: utf-8 -*-
"""AI 分析编排服务 (TASK-76 §7/§21/§23/§24)。

``Workspace.analyze_material_with_ai`` 的全部业务逻辑落在这里,
``Workspace`` 只保留薄方法 (参数校验 + 原子边界 + 落盘 + 操作日志),
与 ``process_material`` 的分工形状一致。

流程::

    material record -> evidence (既有 store, 只读) -> kind 识别
        -> pipeline.analyze_material (chunk/AI/合并)
        -> ground_and_classify (grounding/去重/比对)
        -> register (既有 workflow.register_knowledge_point, 幂等)
        -> attach (已有 KP 挂新 Evidence) / conflict (进 Review 队列)
        -> flush + 学习层只读同步 -> AIAnalysisReport

硬性保证:

- Evidence 不存在/为空 -> ``AIAnalysisFailure`` (422), 材料本身不受影响。
- AI 失败 -> 已有 Evidence/KP 原样保留, 报告 ``status=failed`` 且
  ``Evidence is safe`` 可 retry。
- 重复分析同一材料 -> 同一 ``processing_identity`` + 同一批 ``aikp-*``
  ID -> register 幂等覆盖, 不产生重复 KP/Evidence。
- student learning state 只经 ``register_course_knowledge_points``
  只读同步 (课程 KP 注册表), 绝不写入学生事件/状态。
"""

from __future__ import annotations

import os
import time
from typing import Any, Mapping, Optional, Sequence

from src.application.ai import PIPELINE_VERSION
from src.application.ai.pipeline import (
    AIAnalysisFailure,
    AIAnalysisReport,
    AIUnderstandingPipeline,
    detect_material_kind,
    processing_identity,
)
from src.application.ai.prompts import CHUNK_EXTRACTION_PROMPT_VERSION
from src.application.ai.provider import AIProvider, FakeAIProvider
from src.application.errors import InvalidInputError, NotFoundError
from src.knowledge_validation import knowledge_score_from_counts

__all__ = [
    "AIAnalysisService",
    "require_enabled",
]

#: 未启用时的统一错误 (400, 非 500 —— release 契约门要求)。
AI_DISABLED_MESSAGE = (
    "AI understanding pipeline is disabled "
    "(set CLASSROOM_AI_ENABLED=true or call configure_ai(enabled=True)); "
    "the deterministic pipeline remains available"
)


def require_enabled(enabled: bool) -> None:
    if not enabled:
        raise InvalidInputError(AI_DISABLED_MESSAGE)


def _material_hash(record: Mapping[str, Any]) -> str:
    for key in ("content_hash", "content_sha256", "hash", "material_hash"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    # 回落: material_id 本身是内容寻址的 (register_material 分配), 可作幂等键。
    return str(record.get("material_id") or "")


class AIAnalysisService:
    """AI 分析编排 (无状态; provider/pipeline 按调用注入)。"""

    def __init__(
        self,
        *,
        pipeline: Optional[AIUnderstandingPipeline] = None,
        provider: Optional[AIProvider] = None,
        clock: Optional[Any] = None,
    ) -> None:
        from src.application.runtime import utc_now_iso

        self._pipeline = pipeline or AIUnderstandingPipeline(
            provider or FakeAIProvider(), clock=clock or utc_now_iso
        )

    @property
    def pipeline(self) -> AIUnderstandingPipeline:
        return self._pipeline

    # ------------------------------------------------------------------
    # 主入口 (由 Workspace.analyze_material_with_ai 调用, 已在 _atomic 内)
    # ------------------------------------------------------------------

    def analyze(
        self,
        ctx: Any,
        record: Mapping[str, Any],
        *,
        course_id: str,
        material_id: str,
        content_language: Optional[str] = None,
        provider: Optional[AIProvider] = None,
        timeout_seconds: int = 60,
    ) -> AIAnalysisReport:
        """执行一次材料 AI 分析并落库, 返回完整报告。"""
        started = time.monotonic()
        _ = started
        workflow = ctx.workflow
        store = workflow.store
        stages: list[dict[str, Any]] = [
            {"stage": "material_uploaded", "state": "done", "detail": ""},
        ]

        evidences = [e for e in store.get_by_source(material_id)]
        if not evidences:
            # 回落: 有些 evidence 经 ingestion 但 get_by_source 为空时,
            # 用 workflow 的 DTO 重建只读视图 (仍只读 store, 不编造)。
            stages.append(
                {"stage": "text_extracted", "state": "failed", "detail": "no evidence"}
            )
            raise AIAnalysisFailure(
                "material has no evidence yet; process the material first, "
                "then retry AI analysis (evidence is safe)",
                detail={"material_id": material_id},
            )
        stages.append(
            {
                "stage": "text_extracted",
                "state": "done",
                "detail": "%d evidence" % len(evidences),
            }
        )
        stages.append({"stage": "evidence_created", "state": "done", "detail": ""})

        kind = detect_material_kind(
            filename=str(record.get("filename") or ""),
            source_type=str(record.get("source_type") or ""),
            material_type=str(record.get("material_type") or ""),
        )
        if kind == "unknown":
            # 未知类型按 text 处理 ( ingestion 已产出文本证据 ), 但如实记录。
            kind = "text"
            stages.append(
                {
                    "stage": "kind_detected",
                    "state": "done",
                    "detail": "unknown -> text fallback",
                }
            )
        else:
            stages.append(
                {"stage": "kind_detected", "state": "done", "detail": kind}
            )

        pipeline = self._pipeline
        if provider is not None:
            from src.application.ai.pipeline import AIUnderstandingPipeline as _P

            pipeline = _P(provider, clock=self._pipeline._clock)
        material_label = str(record.get("filename") or material_id)
        image_blob = self._image_bytes_for(record, kind, pipeline.provider)
        try:
            merged, chunk_to_evidence, chunks, stats = pipeline.analyze_material(
                evidences,
                material_id=material_id,
                material_hash=_material_hash(record),
                material_label=material_label,
                kind=kind,
                content_language=content_language,
                timeout_seconds=timeout_seconds,
                image_bytes=image_blob,
            )
        except AIAnalysisFailure:
            raise
        stages.append(
            {
                "stage": "sections_analyzed",
                "state": "done",
                "detail": "%d/%d chunks" % (stats["chunk_succeeded"], stats["chunk_total"]),
            }
        )
        stages.append({"stage": "knowledge_extraction", "state": "done", "detail": ""})

        material_evidence_ids = [str(getattr(e, "evidence_id", "")) for e in evidences]
        try:
            existing_kps = ctx.knowledge_service.get_knowledge_points(course_id)
        except Exception:  # noqa: BLE001 - 知识查询失败时按空集处理, 不中断分析
            existing_kps = []
        classification = pipeline.ground_and_classify(
            merged,
            chunk_to_evidence=chunk_to_evidence,
            material_evidence_ids=material_evidence_ids,
            existing_kps=existing_kps,
        )
        stages.append({"stage": "knowledge_validation", "state": "done", "detail": ""})

        auto_payloads = pipeline.kp_payloads(
            classification["auto"],
            course_id=course_id,
            material_id=material_id,
            content_language=content_language,
        )
        review_payloads = pipeline.kp_payloads(
            classification["review"],
            course_id=course_id,
            material_id=material_id,
            content_language=content_language,
        )
        # 冲突候选同样落库为 pending/待验证 KP (Review 队列兜底), 但报告里
        # 单列 conflicts, 且绝不自动解决 —— 沿用 Task 61 的"冲突并列、人裁决"。
        conflict_payloads: list[dict[str, Any]] = []
        for entry in classification["conflict"]:
            candidate = entry["candidate"]
            from src.application.ai.schemas import KnowledgeCandidate
            from src.application.ai.validators import (
                GroundedCandidate,
                map_candidate_to_kp_payload,
            )

            grounded = GroundedCandidate(
                candidate=KnowledgeCandidate(
                    title=str(candidate.get("title") or ""),
                    description=str(candidate.get("description") or ""),
                    kp_type=str(candidate.get("type") or "concept"),
                    importance=str(candidate.get("importance") or "medium"),
                    confidence=float(candidate.get("confidence") or 0.0),
                    evidence_refs=list(entry.get("evidence_ids") or []),
                    relations=list(candidate.get("relations") or []),
                    examples=list(candidate.get("examples") or []),
                    original_terms=list(candidate.get("original_terms") or []),
                ),
                evidence_ids=list(entry.get("evidence_ids") or []),
                decision="review",
            )
            payload = map_candidate_to_kp_payload(
                grounded,
                course_id=course_id,
                material_id=material_id,
                content_language=content_language,
            )
            payload["needs_verification"] = True
            payload.setdefault("metadata", {})["conflict_with"] = entry.get("existing_id")
            payload["metadata"]["conflict_reason"] = entry.get("reason")
            conflict_payloads.append(payload)

        auto_accepted: list[dict[str, Any]] = []
        needs_review: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        for payload in auto_payloads:
            stored = workflow.register_knowledge_point(payload)
            auto_accepted.append(_kp_brief(stored))
        for payload in review_payloads:
            stored = workflow.register_knowledge_point(payload)
            needs_review.append(_kp_brief(stored))
        for payload in conflict_payloads:
            stored = workflow.register_knowledge_point(payload)
            conflicts.append(
                {
                    **_kp_brief(stored),
                    "conflict_with": (payload.get("metadata") or {}).get("conflict_with"),
                    "reason": (payload.get("metadata") or {}).get("conflict_reason"),
                }
            )
        # attach: 同一知识点挂新 Evidence (更新已有行, 不新增行)。
        for proposal in classification["proposals"]:
            if proposal.action != "attach" or not proposal.existing_id:
                continue
            self._attach_evidence(ctx, proposal.existing_id, proposal.evidence_ids)
        stages.append({"stage": "finalization", "state": "done", "detail": ""})
        # TASK-77 §15: AI 产出的 KP 同样镜像进 processing.structure。
        # ``Workspace._flush_knowledge`` 只持久化这份结构, 重启经它恢复;
        # 只写 org 会让 AI KP 在重启后消失 (实测)。``add_knowledge_point``
        # 按 ID 幂等; 后续确定性装配是增量式的, 不会清掉这些行。
        self._mirror_to_structure(
            ctx, auto_payloads + review_payloads + conflict_payloads
        )

        identity = processing_identity(
            material_hash=_material_hash(record) or material_id,
            model=pipeline.model_id,
            prompt_version=CHUNK_EXTRACTION_PROMPT_VERSION,
            pipeline_version=PIPELINE_VERSION,
        )
        report = AIAnalysisReport(
            material_id=material_id,
            course_id=course_id,
            kind=kind,
            status="completed",
            stages=stages,
            summary=merged.summary,
            topics=list(merged.topics),
            definitions=list(merged.definitions),
            formulas=list(merged.formulas),
            examples=list(merged.examples),
            prerequisites=list(merged.prerequisites),
            difficulties=list(merged.difficulties),
            auto_accepted=auto_accepted,
            needs_review=needs_review,
            conflicts=conflicts,
            rejected=[
                {
                    "title": str(g.candidate.title or "")[:80],
                    "reason": g.reject_reason,
                    "confidence": g.candidate.confidence,
                }
                for g in classification["rejected"]
            ],
            chunk_total=int(stats["chunk_total"]),
            chunk_succeeded=int(stats["chunk_succeeded"]),
            chunk_failed=int(stats["chunk_failed"]),
            chunk_ids=[c.chunk_id for c in chunks],
            truncated=bool(stats["truncated"]),
            provider=pipeline.provider_name,
            model=pipeline.model_id,
            prompt_version=CHUNK_EXTRACTION_PROMPT_VERSION,
            pipeline_version=PIPELINE_VERSION,
            processing_identity=identity,
            created_at=pipeline._clock(),
        )
        return report

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _image_bytes_for(
        record: Mapping[str, Any], kind: str, provider: Any
    ) -> Optional[bytes]:
        """图片材料的原始字节 (vision 可选; 拿不到就回 OCR, 不报错)。

        三重门: 材料是图片 + provider 声明 ``supports_image_bytes`` (即
        ``CLASSROOM_AI_ALLOW_IMAGE_BYTES=true`` 装配) + 受管文件可读且
        不超过 ``MAX_IMAGE_BYTES``。任一不满足即 None —— 调用方走纯 OCR
        路径, 材料不受影响。
        """
        if kind != "image":
            return None
        try:
            supported = bool(provider.capabilities.supports_image_bytes)
        except Exception:  # noqa: BLE001 - 能力未知时保守走 OCR
            return None
        if not supported:
            return None
        from src.application.ai.provider import MAX_IMAGE_BYTES

        try:
            path = str(record.get("stored_path") or "")
            if not path or not os.path.isfile(path):
                return None
            if os.path.getsize(path) > MAX_IMAGE_BYTES:
                return None
            with open(path, "rb") as handle:
                return handle.read()
        except OSError:
            return None

    def _mirror_to_structure(
        self, ctx: Any, payloads: Sequence[Mapping[str, Any]]
    ) -> None:
        """把 AI 落库的 KP 镜像进 ``processing.structure`` (重启可恢复)。

        payload 与 ``register_knowledge_point`` 接受的是同一形状
        (``KnowledgePoint.from_dict`` 兼容, 见 validators 文档), 因此这里
        的转换不可能引入新失败面; 单条异常也只跳过该条, 不中断主流程。
        """
        try:
            structure = ctx.processing.structure
        except Exception:  # noqa: BLE001 - 结构不可用时跳过镜像 (org 仍是真相)
            return
        if structure is None:
            from src.knowledge_structure import KnowledgeStructure

            structure = KnowledgeStructure()
            try:
                ctx.processing.restore_structure(structure)
            except Exception:  # noqa: BLE001 - 同上
                return
        from src.models import KnowledgePoint as _KP

        for payload in payloads:
            try:
                structure.add_knowledge_point(_KP.from_dict(dict(payload)))
            except Exception:  # noqa: BLE001 - 单条跳过, 不中断
                continue

    def _attach_evidence(        self, ctx: Any, knowledge_id: str, evidence_ids: Sequence[str]
    ) -> None:
        """已有 KP 挂新 Evidence (确定性命中才执行; 失败不中断主流程)。"""
        try:
            current = ctx.knowledge_service.get_knowledge_point(knowledge_id)
        except (NotFoundError, Exception):  # noqa: BLE001 - 容错: 找不到就跳过
            return
        merged_refs = list(current.get("evidence_refs") or [])
        changed = False
        for ref in evidence_ids or ():
            if ref and ref not in merged_refs:
                merged_refs.append(ref)
                changed = True
        if not changed:
            return
        payload = dict(current)
        payload["evidence_refs"] = merged_refs
        # 证据条数变了, 分数必须跟着重算 (与 validators.py 共用同一确定性
        # 公式), 否则 attach 会让 knowledge_score 与 evidence_refs 脱节。
        payload["knowledge_score"] = knowledge_score_from_counts(len(merged_refs))
        try:
            ctx.workflow.register_knowledge_point(payload)
        except Exception:  # noqa: BLE001 - 挂载失败不中断主流程 (报告里已有 KP)
            return


def _kp_brief(stored: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "knowledge_id": stored.get("knowledge_id"),
        "title": stored.get("title"),
        "confidence": stored.get("confidence"),
        "evidence_refs": list(stored.get("evidence_refs") or []),
        "needs_verification": stored.get("needs_verification"),
        "review_status": stored.get("review_status"),
    }
