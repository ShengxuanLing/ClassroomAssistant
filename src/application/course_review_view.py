# -*- coding: utf-8 -*-
"""Task 62 — Course Review Center (课程级复习中心, 只读投影层)。

为什么单独一个模块
------------------------
``classroom_view.py`` 承担的是**课堂/工作台**视角 (一门课的运行状态: 材料、
处理、课堂六态)。课程级"复习中心"问的是另一个问题:

    这门课现在的**知识状态**是什么? 哪里需要我审核 / 补课 / 复习?

两者的数据来源高度重叠, 但信息架构完全不同 —— 塞进 ``classroom_view`` 会让
那个模块既讲"课堂跑到哪一步"又讲"知识可信到什么程度"。这里单开一层, 只做
课程级知识状态的投影。

四条硬约束 (与 ``classroom_view`` 同源)
-----------------------------------------
1. **不产生事实**: 每个数字都是从既有服务读出来再统计。本模块**不新增任何**
   "知识是否正确 / 学生是否掌握"之类的判断。
2. **不重新定义状态**: 概览直接消费 ``ValidationStatus``
   (unverified / supported / conflicted) 与 ``ReviewStatus``
   (pending / confirmed / rejected / kept_unverified)。这两个枚举在
   ``src/knowledge_validation.py`` / ``src/knowledge_review.py`` 定义,
   这里只做**分组计数**, 不改写、不合并、不发明新状态。
3. **不重新实现覆盖算法**: Topic Coverage / Gap Analysis 直接调用
   ``knowledge_coverage.py`` 的 ``KnowledgeCoverageAnalyzer``。那个分析器
   内部已经建了**一次**课程快照 (``_CourseSnapshot``), 所有报告都在同一份
   冻结数据上算 —— 因此天然没有 N+1。本模块同样是"一次取全量, 内存里分组",
   绝不 ``for knowledge: query``。
4. **确定性**: 所有列表有显式排序键, 不存在"取决于字典插入顺序"的输出。

空课程
--------
新课程 (没有知识点 / 没有课堂 / 没有材料) 必须返回一份**结构完整、计数全为 0**
的文档, 并带 ``empty: true`` —— 让 UI 能走正常渲染路径显示
"No knowledge available yet.", 而不是 500 或 404。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from src.application.errors import InvalidInputError, NotFoundError

__all__ = [
    "VALIDATION_STATUSES",
    "REVIEW_STATUSES",
    "COVERAGE_STATUSES",
    "GAP_TYPES",
    "CENTER_FORBIDDEN_TERMS",
    "CourseReviewView",
]

#: 概览里必须逐一列出的 validation 状态 (spec 62.2)。
#: 顺序固定 = UI 顺序固定 = 测试可断言。
VALIDATION_STATUSES: tuple[str, ...] = ("supported", "unverified", "conflicted")

#: 概览里必须逐一列出的 review 状态 (spec 62.2)。
REVIEW_STATUSES: tuple[str, ...] = (
    "pending",
    "confirmed",
    "rejected",
    "kept_unverified",
)

#: 覆盖状态 (spec 62.7 的 Covered / Uncovered; "Partially Covered" 是
#: **议题覆盖**概念, coverage 模型里没有这个枚举 —— spec 明确说
#: "如果现有模型定义不同, 使用实际定义"。)
COVERAGE_STATUSES: tuple[str, ...] = ("covered", "uncovered")

#: gap 类型 (来自 ``KnowledgeGapType``)。**不在这里重定义**, 只列出来给
#: 测试做对照; 实际取值运行时从领域枚举读。
GAP_TYPES: tuple[str, ...] = (
    "unassigned",
    "uncovered",
    "conflicted",
    "unverified",
    "review_pending",
)

#: 禁止出现在本投影响应里的词 (spec 23 的"禁止错误 UX")。
#: 复习中心只报告**结构性数据状态**, 绝不输出掌握度或考试预测。
CENTER_FORBIDDEN_TERMS: tuple[str, ...] = (
    "mastery",
    "mastered",
    "proficiency",
    "estimated_ability",
    "predicted",
    "exam_forecast",
    "will_be_on_the_exam",
    "you_are_ready",
    "guaranteed",
)


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _require_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _count_by(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        label = _as_str(row.get(key)) or "unknown"
        out[label] = out.get(label, 0) + 1
    return {k: out[k] for k in sorted(out)}


def _tally(rows: Sequence[Mapping[str, Any]], key: str, labels: Sequence[str]) -> dict[str, int]:
    """只统计给定标签, 缺失的一律补 0 —— UI 不必再处理 undefined。"""
    counts = _count_by(rows, key)
    return {label: int(counts.get(label, 0)) for label in labels}


class CourseReviewView:
    """课程级复习中心的只读投影。

    ``workspace`` 是 :class:`src.application.workspace.Workspace`; 这里用
    ``Any`` 标注以避免与编排器形成导入环。
    """

    def __init__(self, workspace: Any) -> None:
        self._ws = workspace

    # ------------------------------------------------------------------
    # 62.2 + 62.3 + 62.4 + 62.5 + 62.6 + 62.7 — 汇总文档
    # ------------------------------------------------------------------

    def course_review(self, course_id: str) -> dict[str, Any]:
        """一门课的复习中心快照 (Overview + Topics + Sessions + Queue + Conflicts + Gaps)。

        一次取全量, 内存里分组 —— 没有 per-knowledge 查询。
        """
        cid = _require_id(course_id, "course_id")
        ws = self._ws
        # ``get_course`` 对不存在的课程抛 NotFoundError (-> 404)。
        course = ws.get_course(cid)

        points = ws.knowledge_points(cid)
        sessions = ws.list_sessions(cid)
        materials = ws.list_materials(cid)
        candidates = ws.review_candidates(cid)

        overview = self._overview(points, candidates)
        topics = self._topics(cid, points)
        session_rows = self._sessions(cid, sessions, materials)
        conflicts = self._conflicts(cid)
        gaps = self._gaps(cid, points)
        coverage = self._coverage(cid)

        return {
            "course_id": cid,
            "course": course,
            "empty": overview["total"] == 0 and not sessions,
            "overview": overview,
            "topics": topics,
            "sessions": session_rows,
            "review_queue": self._review_queue(cid, candidates),
            "conflicts": conflicts,
            "coverage": coverage,
            "gaps": gaps,
            "counts": {
                "knowledge": overview["total"],
                "topics": len(topics),
                "sessions": len(session_rows),
                "pending_review": overview["pending_review"],
                "conflicted": overview["conflicted"],
                "conflicts_reported": len(conflicts),
            },
        }

    # ------------------------------------------------------------------
    # 62.2 Overview
    # ------------------------------------------------------------------

    def _overview(
        self,
        points: Sequence[Mapping[str, Any]],
        candidates: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """课程知识状态概览。

        ``validation`` 与 ``review`` 是**两个独立的轴** (Task 22 的领域定义):
        一个知识点可以同时是 ``supported`` + ``pending``。把它们相加当作
        "已确认总数" 是错的, 所以这里分开返回, 另给一个
        ``pending_review`` 便捷计数。
        """
        validation = _tally(points, "validation_status", VALIDATION_STATUSES)
        review = _tally(points, "review_status", REVIEW_STATUSES)
        # "有冲突" 是 validation 轴上的 conflicted; 冲突记录可能尚未解决。
        conflicted = int(validation.get("conflicted", 0))
        return {
            "total": len(points),
            "supported": int(validation.get("supported", 0)),
            "unverified": int(validation.get("unverified", 0)),
            "conflicted": conflicted,
            "pending_review": int(review.get("pending", 0)),
            "confirmed": int(review.get("confirmed", 0)),
            "rejected": int(review.get("rejected", 0)),
            "kept_unverified": int(review.get("kept_unverified", 0)),
            # 原始分轴, 便于 UI 同时展示两个独立维度
            "by_validation_status": validation,
            "by_review_status": review,
            # 待审核候选的权威计数来自 ReviewService (与 review_candidates 一致)
            "review_candidates": len(candidates),
        }

    # ------------------------------------------------------------------
    # 62.3 Topic Coverage
    # ------------------------------------------------------------------

    def _topics(
        self, course_id: str, points: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """按 topic 分组的知识状态 (复用 coverage 的 topic 报告)。

        知识点的 topic 归属来自知识组织层的 membership; 这里按 ``topic_id``
        **一次**分组, 再对每个 topic 调 ``analyze_topic`` (它读的是同一个
        已建好的快照, 不重新扫库)。
        """
        ws = self._ws
        ctx = ws.context(course_id)
        org = ctx.org_service

        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for point in points:
            kp_id = _as_str(point.get("knowledge_id"))
            if not kp_id:
                continue
            try:
                topic_ids = org.get_knowledge_point_topics(kp_id)
            except Exception:  # noqa: BLE001 - 未注册 KP 不该让整页失败
                continue
            for topic_id in topic_ids:
                grouped.setdefault(_as_str(topic_id), []).append(point)

        from src.knowledge_coverage import KnowledgeCoverageAnalyzer

        analyzer = KnowledgeCoverageAnalyzer(org)
        rows: list[dict[str, Any]] = []

        topic_meta: dict[str, Mapping[str, Any]] = {}
        for topic in org.list_topics(course_id=course_id):
            try:
                topic_meta[_as_str(topic.topic_id)] = topic.to_dict()
            except Exception:  # noqa: BLE001
                topic_meta[_as_str(topic.topic_id)] = {}

        for topic_id in sorted(set(grouped) | set(topic_meta)):
            members = grouped.get(topic_id, [])
            validation = _tally(members, "validation_status", VALIDATION_STATUSES)
            review = _tally(members, "review_status", REVIEW_STATUSES)
            try:
                report = analyzer.analyze_topic(topic_id).to_dict()
            except Exception:  # noqa: BLE001 - 覆盖率是辅助信息
                report = {
                    "topic_id": topic_id,
                    "total_knowledge_points": len(members),
                    "covered_knowledge_points": 0,
                    "coverage_ratio": 0.0,
                }
            meta = topic_meta.get(topic_id) or {}
            rows.append(
                {
                    "topic_id": topic_id,
                    "name": meta.get("name"),
                    "description": meta.get("description"),
                    "parent_topic_id": meta.get("parent_topic_id"),
                    "knowledge_count": len(members),
                    "supported": validation.get("supported", 0),
                    "unverified": validation.get("unverified", 0),
                    "conflicted": validation.get("conflicted", 0),
                    "review_pending": review.get("pending", 0),
                    "confirmed": review.get("confirmed", 0),
                    "knowledge_ids": sorted(
                        _as_str(p.get("knowledge_id")) for p in members
                    ),
                    "coverage": report,
                }
            )
        rows.sort(key=lambda row: row["topic_id"])
        return rows

    # ------------------------------------------------------------------
    # 62.4 Session Review
    # ------------------------------------------------------------------

    def _sessions(
        self,
        course_id: str,
        sessions: Sequence[Mapping[str, Any]],
        materials: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """每节课堂的 evidence / knowledge / pending / unverified / conflicted。

        knowledge 数通过 ``workspace.knowledge_points(course_id, session_id=...)``
        取 —— 它是会话级 membership 查询, 不是 per-knowledge 循环。
        """
        ws = self._ws
        materials_by_session: dict[str, list[Mapping[str, Any]]] = {}
        for record in materials:
            sid = _as_str(record.get("session_id"))
            if sid:
                materials_by_session.setdefault(sid, []).append(record)

        rows: list[dict[str, Any]] = []
        for session in sessions:
            sid = _as_str(session.get("session_id"))
            if not sid:
                continue
            try:
                points = ws.knowledge_points(course_id, session_id=sid)
            except Exception:  # noqa: BLE001 - 单节课查询失败不该让整页失败
                points = []
            validation = _tally(points, "validation_status", VALIDATION_STATUSES)
            review = _tally(points, "review_status", REVIEW_STATUSES)
            own_materials = materials_by_session.get(sid, [])
            rows.append(
                {
                    "session_id": sid,
                    "session_number": session.get("session_number"),
                    "title": session.get("title"),
                    "date": session.get("date"),
                    "material_count": len(own_materials),
                    "evidence_count": sum(
                        len(rec.get("evidence_ids") or ()) for rec in own_materials
                    ),
                    "knowledge_count": len(points),
                    "supported": validation.get("supported", 0),
                    "unverified": validation.get("unverified", 0),
                    "conflicted": validation.get("conflicted", 0),
                    "pending_review": review.get("pending", 0),
                    "knowledge_ids": sorted(
                        _as_str(p.get("knowledge_id")) for p in points
                    ),
                    # 点击进入已有 Session Timeline
                    "timeline_path": f"/api/sessions/{sid}/timeline",
                }
            )
        rows.sort(key=lambda row: row["session_id"])
        return rows

    # ------------------------------------------------------------------
    # 62.5 Review Queue
    # ------------------------------------------------------------------

    def _review_queue(
        self, course_id: str, candidates: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """待审核队列 (权威来源 = ReviewService 的 candidates)。

        **不复制一套 Review Service**: 这里只返回候选 + 一个指向既有
        Review Center 的入口。真正的 confirm/reject 仍走
        ``workspace.review_*``。
        """
        items = [dict(c) for c in sorted(
            candidates, key=lambda c: _as_str(c.get("knowledge_point_id"))
        )]
        return {
            "course_id": course_id,
            "count": len(items),
            "items": items,
            # 点击后进入既有 Review Center
            "review_center_path": "/api/reviews",
        }

    # ------------------------------------------------------------------
    # 62.6 Conflict
    # ------------------------------------------------------------------

    def _conflicts(self, course_id: str) -> list[dict[str, Any]]:
        """冲突知识点 (含两侧证据), 点击进 Knowledge Detail。

        ``get_conflicts`` 已按 ``conflict_id`` 去重 (Task 45 修的真实缺陷:
        同一冲突会被每个 KP 各报一次)。这里**原样透传**, 再加一个
        ``knowledge_detail_path`` 供 UI 直接跳转。
        """
        ws = self._ws
        try:
            raw = ws.conflicts(course_id)
        except Exception:  # noqa: BLE001
            raw = []
        rows: list[dict[str, Any]] = []
        for item in raw:
            row = dict(item)
            evidence_refs = sorted(_as_str(e) for e in (row.get("evidence_refs") or ()))
            row["evidence_refs"] = evidence_refs
            # spec 62.6: 能看到 Evidence A / Evidence B —— 两侧都要在响应里,
            # 顺序确定性排序 (绝不折叠成"一方是对的")。
            row["sides"] = [
                {"label": f"Evidence {chr(ord('A') + i)}", "evidence_id": ev}
                for i, ev in enumerate(evidence_refs)
            ]
            kp_id = _as_str(row.get("knowledge_point_id"))
            if kp_id:
                row["knowledge_detail_path"] = f"/api/knowledge/{kp_id}"
            rows.append(row)
        rows.sort(key=lambda row: _as_str(row.get("conflict_id")))
        return rows

    # ------------------------------------------------------------------
    # 62.7 Gap Analysis
    # ------------------------------------------------------------------

    def _coverage(self, course_id: str) -> dict[str, Any]:
        """结构性覆盖 (复用 coverage 算法, 不重新实现)。"""
        try:
            report = self._ws.coverage(course_id)
        except Exception:  # noqa: BLE001 - 覆盖率失败不该让整页 500
            return {
                "course_id": course_id,
                "unavailable": True,
                "total_knowledge_points": 0,
                "covered_knowledge_points": 0,
                "uncovered_knowledge_points": 0,
                "coverage_ratio": 0.0,
            }
        return report

    def _gaps(
        self, course_id: str, points: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """缺口分析: 使用领域层**实际定义**的 gap 类型。

        spec 62.7 提到 Covered / Partially Covered / Uncovered, 但本项目
        coverage 模型只有 ``covered`` / ``uncovered`` (见
        ``KnowledgeCoverageStatus``), 而缺口是五种数据状态
        (``unassigned`` / ``uncovered`` / ``conflicted`` / ``unverified`` /
        ``review_pending``)。按 spec 的兜底条款"如果现有模型定义不同,
        使用实际定义", 这里返回真实枚举 + 按类型聚合的计数。
        """
        ws = self._ws
        try:
            raw = ws.gaps(course_id)
            rows = list(raw.get("gaps") or [])
        except Exception:  # noqa: BLE001
            rows = []

        by_type: dict[str, int] = {gap: 0 for gap in GAP_TYPES}
        for row in rows:
            for gap_type in row.get("gap_types") or ():
                label = _as_str(gap_type)
                by_type[label] = by_type.get(label, 0) + 1

        return {
            "course_id": course_id,
            "total_gaps": len(rows),
            "by_type": {k: by_type[k] for k in sorted(by_type)},
            "knowledge_points_with_gaps": sorted(
                _as_str(row.get("knowledge_point_id")) for row in rows
            ),
            "gaps": [
                dict(row) for row in sorted(
                    rows, key=lambda r: _as_str(r.get("knowledge_point_id"))
                )
            ],
        }

    # ------------------------------------------------------------------
    # 62.9 Empty State
    # ------------------------------------------------------------------

    def course_review_summary(self, course_id: str) -> dict[str, Any]:
        """轻量摘要 (Overview + counts), 给导航/卡片用。

        与 ``course_review`` 共用同一套计数逻辑, 空课程同样返回结构完整的
        0 计数文档 —— **不是** 404、**不是** 500。
        """
        cid = _require_id(course_id, "course_id")
        ws = self._ws
        course = ws.get_course(cid)
        points = ws.knowledge_points(cid)
        sessions = ws.list_sessions(cid)
        candidates = ws.review_candidates(cid)
        overview = self._overview(points, candidates)
        return {
            "course_id": cid,
            "course": course,
            "empty": overview["total"] == 0 and not sessions,
            "overview": overview,
            "counts": {
                "knowledge": overview["total"],
                "sessions": len(sessions),
                "pending_review": overview["pending_review"],
                "conflicted": overview["conflicted"],
            },
        }
