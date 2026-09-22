# -*- coding: utf-8 -*-
"""数据质量统计 (Task 72.7)。

把"真实课堂材料"跑过之后, 我们需要知道这份数据到底健不健康。本模块从
``Workspace`` 上读出可观察的指标, 供 ``docs/task-72-data-quality-report.md``
以及 Pilot 自检使用。

统计项 (规范点名)
--------------------------------------------------------------------
- materials processed   完成处理 (``processing_status == COMPLETED``) 的材料数
- materials failed      处理失败 (``processing_status == FAILED``) 的材料数
- evidence extracted    已提取的证据条数 (内容寻址, 跨课程共享, 全局计数)
- knowledge created     已创建的知识点总数
- unverified            validation_status == unverified 的知识点数
- conflicted            validation_status == conflicted 的知识点数
- broken traces         知识点引用了"已不存在的源材料"的数量 (溯源断链)
- duplicates            content-addressing 判定的重复材料数
- unsupported formats   不被支持的文件扩展名集合 (显式标记, 不强行 parser)

所有读取都在防御性包裹里: 单门课程读取出错不影响整体报告, 也不会让报告本身崩掉。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from src.application.runtime import Clock, utc_now_iso

def _extract_material_id(evidence: Any) -> Optional[str]:
    """从一条 evidence DTO 里取出源材料 id (兼容多种形状)。"""
    if not isinstance(evidence, Mapping):
        return None
    mid = evidence.get("material_id")
    if mid:
        return str(mid)
    source = evidence.get("source")
    if isinstance(source, Mapping):
        mid = source.get("material_id")
        if mid:
            return str(mid)
    return None

#: 被判定为"格式不支持"的处理错误码。
UNSUPPORTED_ERROR_CODES: frozenset[str] = frozenset(
    {"UNSUPPORTED_EXTENSION", "UNSUPPORTED_FORMAT", "UNSUPPORTED"}
)


def count_broken_traces(
    knowledge_points: Iterable[Mapping[str, Any]],
    material_ids: Iterable[str],
) -> int:
    """统计溯源断链: 知识点的证据引用了一个**不在册**的源材料 id。

    纯函数 (不碰 ``Workspace``), 便于独立单元测试。``knowledge_points`` 的每个
    元素形如 ``{"referenced_material_ids": [...]}`` —— 即该知识点引用到的**源材料**
    id 列表 (已把 evidence_refs 解析成 material_id); ``material_ids`` 是当前课程
    在册材料的 id 集合。只要有一个引用的 material_id 不在册, 即断链。

    注意: 真实数据里 ``KnowledgePoint.evidence_refs`` 存的是 *evidence_id*, 不是
    material_id, 因此调用方 (``collect_data_quality``) 必须先通过
    ``knowledge_evidence`` 把 evidence_id 解析成 material_id, 再喂给本函数。
    """
    known = set(material_ids)
    broken = 0
    for kp in knowledge_points:
        referenced = kp.get("referenced_material_ids") or []
        if referenced and not all(str(m) in known for m in referenced):
            broken += 1
    return broken


def collect_data_quality(
    workspace: Any,
    *,
    clock: Optional[Clock] = None,
) -> dict[str, Any]:
    """从 ``Workspace`` 收集数据质量统计。

    参数
    ----
    workspace: 一个打开了持久化的 ``Workspace`` 实例。

    返回
    ----
    含规范点名统计项的 dict; 另含 ``courses`` 总数与 ``generated_at`` 时间戳。
    任何单门课程的读取异常都会被吞掉并记入 ``errors`` 列表, 不让报告整体崩掉。
    """
    now: Clock = clock or utc_now_iso
    stats: dict[str, Any] = {
        "courses": 0,
        "materials_processed": 0,
        "materials_failed": 0,
        "materials_pending": 0,
        "duplicates": 0,
        "evidence_extracted": 0,
        "knowledge_created": 0,
        "unverified": 0,
        "conflicted": 0,
        "broken_traces": 0,
        "unsupported_formats": set(),
        "generated_at": str(now()),
        "errors": [],
    }

    try:
        courses = list(workspace.list_courses())
    except Exception as exc:  # noqa: BLE001
        stats["errors"].append(f"list_courses: {type(exc).__name__}: {exc}")
        return stats

    stats["courses"] = len(courses)

    # 证据是内容寻址、跨课程共享的真相源 -> 全局计数一次。
    try:
        stats["evidence_extracted"] = len(workspace.store.all())
    except Exception as exc:  # noqa: BLE001
        stats["errors"].append(f"evidence_count: {type(exc).__name__}: {exc}")

    for course in courses:
        course_id = course.get("course_id")
        if not course_id:
            continue
        try:
            materials = list(workspace.list_materials(course_id))
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(
                f"list_materials({course_id}): {type(exc).__name__}: {exc}"
            )
            materials = []

        material_ids = {
            m.get("material_id")
            for m in materials
            if m.get("material_id")
        }
        for m in materials:
            status = str(m.get("processing_status") or "")
            if status == "COMPLETED":
                stats["materials_processed"] += 1
            elif status == "FAILED":
                stats["materials_failed"] += 1
            else:
                stats["materials_pending"] += 1
            if m.get("duplicate"):
                stats["duplicates"] += 1
            error_code = str(m.get("error") or "")
            if error_code.upper() in UNSUPPORTED_ERROR_CODES or any(
                code in error_code.upper() for code in UNSUPPORTED_ERROR_CODES
            ):
                stats["materials_failed"] += 0  # 已计入 failed
                ext = str(m.get("filename") or m.get("stored_path") or "").rsplit(
                    ".", 1
                )
                if len(ext) == 2 and ext[1]:
                    stats["unsupported_formats"].add(ext[1].lower())

        # 知识点 + 溯源断链。
        try:
            kps = list(workspace.knowledge_points(course_id))
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(
                f"knowledge_points({course_id}): {type(exc).__name__}: {exc}"
            )
            kps = []
        stats["knowledge_created"] += len(kps)
        kps_with_material_ids: list[dict[str, Any]] = []
        for kp in kps:
            vstatus = str(kp.get("validation_status") or "")
            if vstatus == "unverified":
                stats["unverified"] += 1
            elif vstatus == "conflicted":
                stats["conflicted"] += 1
            # 把 evidence_refs (evidence_id) 解析成 material_id, 用于断链判定。
            referenced: list[str] = []
            kp_id = kp.get("knowledge_id")
            if kp_id is not None:
                try:
                    for ev in workspace.knowledge_evidence(course_id, kp_id):
                        mid = _extract_material_id(ev)
                        if mid:
                            referenced.append(str(mid))
                except Exception:  # noqa: BLE001 - 单条解析失败不影响整体
                    pass
            kps_with_material_ids.append({"referenced_material_ids": referenced})
        stats["broken_traces"] += count_broken_traces(
            kps_with_material_ids, material_ids
        )

    # 把 set 转成排序列表, 保证 JSON 友好 + 可复现。
    stats["unsupported_formats"] = sorted(stats["unsupported_formats"])
    return stats
