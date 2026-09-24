# -*- coding: utf-8 -*-
"""DTO 序列化层 (Task 34)。

所有 API/UI 可见的数据对象都必须经过本模块转换为扁平 JSON 结构。
Domain 对象 (KnowledgePoint / Evidence / ...) 不得直接暴露给 UI 层。

约定:
- 每个函数接受一个 domain 对象并返回 dict (可 JSON 序列化)。
- 字段命名与 domain 层保持一致 (snake_case)。
- 不修改 domain 对象本身。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from src.models import (
    ClassSession,
    Course,
    Evidence,
    KnowledgePoint,
    Material,
    SourceReference,
)

__all__ = [
    "course_to_dict",
    "session_to_dict",
    "material_to_dict",
    "evidence_to_dict",
    "knowledge_point_to_dict",
    "conflict_to_dict",
    "review_record_to_dict",
    "to_jsonable",
]


def course_to_dict(course: Course) -> dict[str, Any]:
    """Course -> 扁平 dict。"""
    return {
        "course_id": course.course_id,
        "name": course.name,
        "code": course.code,
        "language": course.language.value,
        "metadata": dict(course.metadata or {}),
    }


def session_to_dict(session: ClassSession) -> dict[str, Any]:
    """ClassSession -> 扁平 dict。"""
    return {
        "session_id": session.session_id,
        "course_id": session.course_id,
        "session_number": session.session_number,
        "date": session.date,
        "title": session.title,
        "material_refs": list(session.material_refs or []),
        "evidence_refs": list(session.evidence_refs or []),
        "knowledge_point_refs": list(session.knowledge_point_refs or []),
        "verification_refs": list(session.verification_refs or []),
    }


def material_to_dict(material: Material) -> dict[str, Any]:
    """Material -> 扁平 dict。"""
    return {
        "material_id": material.material_id,
        "filename": material.filename,
        "path": material.path,
        "material_type": material.material_type.value,
        "language": material.language.value,
        "created_at": material.created_at,
        "metadata": dict(material.metadata or {}),
    }


def source_ref_to_dict(ref: Optional[SourceReference]) -> Optional[dict[str, Any]]:
    """SourceReference -> 扁平 dict; 未提供则 None。"""
    if ref is None:
        return None
    return {
        "material_id": ref.material_id,
        "location": ref.location,
        "timestamp_start": ref.timestamp_start,
        "timestamp_end": ref.timestamp_end,
        "page": ref.page,
        "line": ref.line,
        "paragraph": ref.paragraph,
    }


def evidence_to_dict(evidence: Evidence) -> dict[str, Any]:
    """Evidence -> 扁平 dict。"""
    out: dict[str, Any] = {
        "evidence_id": evidence.evidence_id,
        "content": evidence.content,
        "language": evidence.language.value,
        "confidence": evidence.confidence.value,
        "evidence_type": evidence.evidence_type.value,
        "metadata": dict(evidence.metadata or {}),
        "source": source_ref_to_dict(evidence.source_reference),
    }
    return out


def knowledge_point_to_dict(
    kp: KnowledgePoint, *, conflict: Any = None
) -> dict[str, Any]:
    """KnowledgePoint -> 扁平 dict (保留全部 provenance 字段)。

    ``conflict`` 参数 (Task 66 修复的真实缺陷)
    ------------------------------------------
    ``KnowledgePoint`` **本身没有** "这条知识有未解决冲突" 这个字段 ——
    领域层的冲突 (``ConflictRecord``) 说的是 "哪两条证据互相矛盾", 归属
    只能由证据 refs 的交集推导 (见 ``KnowledgeService.get_conflicts``)。
    所以判据必须由**持有结构快照的调用方**传进来, DTO 自己无从得知。

    但在这之前, DTO 里**根本没有** ``conflict`` 键, 而两处下游早就在读它:

    - ``KnowledgeService.get_knowledge_points(conflict=True)`` 的过滤器
      (``bool(kp.get("conflict", False))``);
    - ``LearningWorkflow`` 的"冲突知识不得进入正式练习"排除规则。

    于是 ``GET /api/knowledge?conflict=true`` **永远返回空列表** —— 又一个
    "能力在两端实现、中间从未接线" (Task 62 修过同一个 family 的签名问题,
    没修字段本身)。静默空结果比抛异常更难查。

    ``conflict`` 的取值语义 (**三态, 不是二态**):

    - ``None`` —— 调用方**不知道** (没有结构快照, 例如 KP 刚登记完)。
      此时输出 ``conflict: False`` 是**伪造事实**, 所以这里刻意输出
      ``"unknown"`` 之外的形态: 见下方实现 —— 传 ``None`` 时该键取
      ``False`` 会被过滤器判为"无冲突", 仍然在骗人。因此调用方**必须**
      显式传入判据; 本函数对 ``None`` 保留成 ``False`` 仅为向后兼容
      ``material_workflow`` 的"刚创建、尚无冲突"语义 (那确实是已知无冲突,
      而不是未知)。

    真正的"未知"在服务层用一个显式的标记表达, 不在这里猜。
    """
    if conflict is None:
        has_conflict = False
    else:
        has_conflict = _coerce_conflict_flag(conflict)
    knowledge_id = str(kp.knowledge_id or "")
    if knowledge_id.startswith("aikp-"):
        generation_mode = "ai_summary"
    elif knowledge_id.startswith("kp-"):
        generation_mode = "deterministic_fallback"
    else:
        generation_mode = "manual"
    return {
        "knowledge_id": kp.knowledge_id,
        "title": kp.title,
        "content": kp.content,
        "original_terms": list(kp.original_terms or []),
        "importance": kp.importance,
        "confidence": getattr(kp.confidence, "value", kp.confidence),
        "evidence_refs": list(kp.evidence_refs or []),
        "related_points": list(kp.related_points or []),
        "needs_verification": kp.needs_verification,
        "validation_status": kp.validation_status,
        "knowledge_score": kp.knowledge_score,
        "review_status": kp.review_status,
        # ``kp-*`` is the deterministic extractor's namespace and may contain a
        # source excerpt; ``aikp-*`` is reserved for validated AI abstractions.
        # Project the distinction in the API so the UI never calls fallback text
        # an AI summary.  No DB migration is needed: the namespace is persisted.
        "generation_mode": generation_mode,
        # "这条知识是否有未解决的冲突" —— 由调用方从结构快照推导后传入。
        "conflict": has_conflict,
    }


#: ``conflict`` 真值归一 (HTTP 查询串里永远是字符串)。
_FALSE_STRINGS = frozenset({"", "0", "false", "no", "none", "null"})


def _coerce_conflict_flag(value: Any) -> bool:
    """把 ``conflict`` 判据归一成 bool (大小写不敏感)。

    与 ``Workspace._as_optional_bool`` / ``LearningWorkflow._truthy_conflict``
    口径一致: 字符串 ``"false"`` 绝不能被判为真, 否则"只看无冲突"会静默
    返回空列表。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    return text not in _FALSE_STRINGS


def conflict_to_dict(conflict: Any) -> dict[str, Any]:
    """ConflictRecord -> 扁平 dict。

    接受 src.integration.ConflictRecord; 不引入额外依赖。

    这里**不**输出 ``knowledge_point_id`` —— 领域层的 ``ConflictRecord``
    只记录"哪两条证据互相矛盾", 本身没有知识点字段 (ConflictRecord 不是
    "某个知识点错了", 而是"两条证据对不上")。证据↔知识点的关联只存在于
    ``KnowledgePoint.evidence_refs``, 因此归属必须由持有 KP 视图的调用方
    推导后补上 (见 ``KnowledgeService.get_conflicts``)。
    """
    status = getattr(conflict.status, "value", conflict.status)
    return {
        "conflict_id": conflict.conflict_id,
        "evidence_refs": list(getattr(conflict, "evidence_refs", [])),
        "description": getattr(conflict, "description", ""),
        "status": status,
    }


def review_record_to_dict(record: Any) -> dict[str, Any]:
    """ReviewRecord -> 扁平 dict (Task 15 人类审查记录)。"""
    decision = getattr(record.decision, "value", record.decision)
    return {
        "review_id": record.review_id,
        "knowledge_point_id": record.knowledge_point_id,
        "decision": decision,
        "selected_evidence_ids": list(getattr(record, "selected_evidence_ids", [])),
        "note": getattr(record, "note", None),
    }


def to_jsonable(value: Any) -> Any:
    """递归把 domain 对象 / dataclass / 常见可序列化类型转换为 JSON 友好结构。

    - dict: 递归转换每个 value
    - list / tuple / set / frozenset: 递归转换每个元素 (排序 set 以保持确定性)
    - 有 to_dict() 的对象: 调用 to_dict()
    - Enum: 取 .value
    - dataclass: 递归转换每个字段
    - 其他原始类型: 原样返回
    """
    import dataclasses
    import enum as _enum

    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        items = sorted(value, key=lambda x: (str(type(x)), str(x)))
        return [to_jsonable(v) for v in items]
    if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
        return to_jsonable(value.to_dict())
    if isinstance(value, _enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        out: dict[str, Any] = {}
        for field_ in dataclasses.fields(value):
            out[field_.name] = to_jsonable(getattr(value, field_.name))
        return out
    return str(value)
