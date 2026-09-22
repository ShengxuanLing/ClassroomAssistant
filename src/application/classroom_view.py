# -*- coding: utf-8 -*-
"""Task 56 — Real Classroom Workspace (只读投影层)。

为什么单独一个模块
------------------------
``workspace.Workspace`` 是编排器: 事务、持久化写穿、服务装配。把"课程
工作台 / 课堂工作台 / 今日"这三个**只读视图**塞进去, 会让它同时承担写与
读两种职责, 而读视图需要反复组合多个服务 —— 混在一起之后两边都难读。

这个模块只做投影, 三条硬约束:

1. **不产生事实**: 视图里的每一个数字都是从既有服务读出来再统计的,
   本模块不新增任何"知识是否正确 / 学生是否掌握"之类的判断。
2. **不写库**: 全部方法只读。不调用任何 ``_flush_*``, 也不调用会写快照
   的 ``Workspace.study_plan()`` (它每次都会追加一条计划快照行 —— 对一个
   纯展示视图来说那是副作用)。
3. **确定性**: 所有列表都有显式排序键, 不存在"取决于字典插入顺序"的
   输出。

课堂状态 (56.3) 的判据
------------------------
六个状态是**推导出来的**, 不是存下来的字段 —— 存字段就会出现"字段说
READY_TO_STUDY 但还有 7 条待审核"这种漂移。判据按下面的顺序短路:

===================  ==================================================
PLANNED              一节课材料都还没有
PROCESSING           有作业**已被显式入队**且尚未终态 (QUEUED / RUNNING)
MATERIALS_ADDED      有材料, 但没有一个作业成功过, 也没有在排队
PROCESSED            处理完了, 但这节课还没产出知识点
REVIEW_REQUIRED      有知识点, 且其中存在待审核候选
READY_TO_STUDY       有知识点, 且没有待审核候选
===================  ==================================================

"已被显式入队" 这一条不是修辞。``get_status()`` 会为**每个已注册材料**
补建一个 QUEUED 作业 (否则面板会显示"没有作业"而实际有材料在等), 于是
``QUEUED`` 同时表示"用户点了处理, 在排队"和"材料刚注册, 还没人动过"。
不区分这两者的话, MATERIALS_ADDED 永远达不到 —— 上传完还没处理的课会被
显示成"处理中"。区分的标记是 :attr:`ProcessingJob.enqueued`。

注意 ``READY_TO_STUDY`` **不是** "已掌握"。它只表示"这节课的知识点当前
没有挂起的人工审核"。掌握度只在 Task 30 的 Student State 里定义, 这里
一律不推断 (spec 59.4 / 禁止的产品行为)。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from src.application.errors import InvalidInputError, NotFoundError

__all__ = [
    "SESSION_PLANNED",
    "SESSION_MATERIALS_ADDED",
    "SESSION_PROCESSING",
    "SESSION_PROCESSED",
    "SESSION_REVIEW_REQUIRED",
    "SESSION_READY_TO_STUDY",
    "SESSION_STATUSES",
    "COURSE_TEACHER_KEY",
    "COURSE_SEMESTER_KEY",
    "NO_LEARNING_STATE",
    "resolve_session_status",
    "ClassroomWorkspaceView",
    "TimelineFilter",
    "TimelineItem",
    "resolve_timeline",
]

# ----------------------------------------------------------------------
# 课堂状态 (spec 56.3)
# ----------------------------------------------------------------------

SESSION_PLANNED = "PLANNED"
SESSION_MATERIALS_ADDED = "MATERIALS_ADDED"
SESSION_PROCESSING = "PROCESSING"
SESSION_PROCESSED = "PROCESSED"
SESSION_REVIEW_REQUIRED = "REVIEW_REQUIRED"
SESSION_READY_TO_STUDY = "READY_TO_STUDY"

#: 顺序即 spec 列出的生命周期顺序。测试用它断言"集合不漂移"。
SESSION_STATUSES: tuple[str, ...] = (
    SESSION_PLANNED,
    SESSION_MATERIALS_ADDED,
    SESSION_PROCESSING,
    SESSION_PROCESSED,
    SESSION_REVIEW_REQUIRED,
    SESSION_READY_TO_STUDY,
)

#: Course 没有独立的 teacher / semester 字段 —— 它们属于 ``Course.metadata``
#: (spec 56.1: "具体字段以现有模型为准")。这里只定义读取约定用的键名。
COURSE_TEACHER_KEY = "teacher"
COURSE_SEMESTER_KEY = "semester"

#: 学生没有任何学习状态时, UI 必须说的原话 (spec 63.2)。
NO_LEARNING_STATE = "No learning state yet"

#: 证据分组 (spec 56.2 的 Transcript / OCR 两栏, 外加文档与笔记)。
_EVIDENCE_GROUP_TRANSCRIPT = "transcript"
_EVIDENCE_GROUP_OCR = "ocr"
_EVIDENCE_GROUP_DOCUMENT = "document"
_EVIDENCE_GROUP_NOTE = "note"
_EVIDENCE_GROUP_OTHER = "other"

_EVIDENCE_GROUPS: tuple[str, ...] = (
    _EVIDENCE_GROUP_TRANSCRIPT,
    _EVIDENCE_GROUP_OCR,
    _EVIDENCE_GROUP_DOCUMENT,
    _EVIDENCE_GROUP_NOTE,
    _EVIDENCE_GROUP_OTHER,
)

_TYPE_TO_GROUP: dict[str, str] = {
    "transcript": _EVIDENCE_GROUP_TRANSCRIPT,
    "ocr": _EVIDENCE_GROUP_OCR,
    "document": _EVIDENCE_GROUP_DOCUMENT,
    "personal_note": _EVIDENCE_GROUP_NOTE,
    "classmate_note": _EVIDENCE_GROUP_NOTE,
}

#: "最近材料" 的展示条数 (spec 56.1 Recent materials)。
RECENT_MATERIAL_LIMIT = 10

#: "今日" 里待审核 / 待处理材料的展示条数。
TODAY_ITEM_LIMIT = 20


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


def _sort_key_material(record: Mapping[str, Any]) -> tuple[str, str]:
    """材料排序键: ``(created_at 降序, material_id 升序)`` 之外无法全序。

    用降序的办法是把 created_at 取负不行 (字符串), 所以拆成两次 sort:
    先按 material_id 升序, 再按 created_at 降序 (Python sort 稳定)。
    """
    return (_as_str(record.get("material_id")),)


def resolve_session_status(
    *,
    material_count: int,
    queued: int = 0,
    running: int = 0,
    succeeded: int = 0,
    knowledge_count: int = 0,
    pending_review: int = 0,
) -> str:
    """按 spec 56.3 的判据推导一节课的状态 (纯函数, 便于单测)。

    负数的入参一律视为 0 —— 计数来自外部统计, 宁可退化也不传播脏值。
    """
    material_count = max(0, int(material_count))
    queued = max(0, int(queued))
    running = max(0, int(running))
    succeeded = max(0, int(succeeded))
    knowledge_count = max(0, int(knowledge_count))
    pending_review = max(0, int(pending_review))

    if material_count == 0:
        return SESSION_PLANNED
    if queued or running:
        return SESSION_PROCESSING
    if succeeded == 0:
        return SESSION_MATERIALS_ADDED
    if knowledge_count == 0:
        return SESSION_PROCESSED
    if pending_review > 0:
        return SESSION_REVIEW_REQUIRED
    return SESSION_READY_TO_STUDY


def _job_counts(status_payload: Mapping[str, Any]) -> dict[str, int]:
    """从 ``processing_status()`` 的作业列表里数出状态判据需要的四个数。

    只把 ``enqueued`` 为真的 QUEUED 作业算作"在排队" —— 理由见
    :attr:`ProcessingJob.enqueued` 的注释: 未入队的 QUEUED 作业其实是
    "材料刚注册, 还没人动过", 把它算成排队会让 MATERIALS_ADDED 永远
    达不到。
    """
    jobs = status_payload.get("jobs") or []
    queued = 0
    running = 0
    succeeded = 0
    failed = 0
    for job in jobs:
        status = _as_str(job.get("status"))
        if status == "QUEUED" and bool(job.get("enqueued")):
            queued += 1
        elif status == "RUNNING":
            running += 1
        elif status == "SUCCEEDED":
            succeeded += 1
        elif status == "FAILED":
            failed += 1
    return {
        "queued": queued,
        "running": running,
        "succeeded": succeeded,
        "failed": failed,
    }


def _derive_processing_stages(
    status_payload: Mapping[str, Any], knowledge_count: int
) -> list[dict[str, Any]]:
    """Task 57.1: 把"一节课处理到哪一步"翻译成 7 个可勾选阶段。

    全部从**已发生的真实结果**推导, 不是凭空宣告"已完成"。任一阶段
    没有对应事实支撑时就是未完成 —— 例如没有知识点就绝不把
    ASSEMBLING_KNOWLEDGE 标成 done。

    判定来源 (全部确定性):
    - PREPARING: 有材料已注册 (materials > 0)。
    - PROCESSING_MATERIALS: 所有作业都已终态 (SUCCEEDED / FAILED / CANCELLED)。
    - EXTRACTING_EVIDENCE: 有作业成功产出证据 (evidence_total > 0)。
    - ASSEMBLING_KNOWLEDGE: 本节课组装出了知识点 (knowledge_count > 0)。
    - VALIDATING: 处理已跑完 (同 PROCESSING_MATERIALS)。
    - CHECKING_CONFLICTS: 处理已跑完 (同 PROCESSING_MATERIALS)。
    - FINISHED: 处理跑完且至少产出了证据或知识点。
    """
    from src.application.processing_service import (
        JOB_CANCELLED,
        JOB_FAILED,
        JOB_SUCCEEDED,
        PROCESS_STAGES,
        PROCESS_STAGE_LABELS,
    )

    jobs = status_payload.get("jobs") or []
    evidence_total = int(status_payload.get("evidence_total") or 0)
    finished_states = {JOB_SUCCEEDED, JOB_FAILED, JOB_CANCELLED}
    all_terminal = bool(jobs) and all(
        _as_str(job.get("status")) in finished_states for job in jobs
    )
    processing_done = all_terminal
    produced = evidence_total > 0 or knowledge_count > 0

    done_by_stage = {
        "PREPARING": len(jobs) > 0,
        "PROCESSING_MATERIALS": processing_done,
        "EXTRACTING_EVIDENCE": evidence_total > 0,
        "ASSEMBLING_KNOWLEDGE": knowledge_count > 0,
        "VALIDATING": processing_done,
        "CHECKING_CONFLICTS": processing_done,
        "FINISHED": processing_done and produced,
    }
    return [
        {
            "key": key,
            "label": PROCESS_STAGE_LABELS.get(key, key),
            "done": bool(done_by_stage.get(key, False)),
        }
        for key in PROCESS_STAGES
    ]


def _classify_evidence(evidence: Mapping[str, Any]) -> str:
    """按 ``evidence_type`` 把证据分到 Transcript / OCR / Document / Note。

    分不进去的落 ``other`` —— 绝不猜测, 也绝不因为认不出就丢掉。
    """
    raw = _as_str(evidence.get("evidence_type")).strip().lower()
    return _TYPE_TO_GROUP.get(raw, _EVIDENCE_GROUP_OTHER)


def _empty_evidence_buckets() -> dict[str, list[dict[str, Any]]]:
    return {group: [] for group in _EVIDENCE_GROUPS}


def _evidence_sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    """证据排序键 (确定性):

    ``(无时间戳标记, 起始时间戳, 证据 ID)`` —— 有时间戳的排在前面并按时间
    升序 (与 spec 58 的时间线口径一致), 没有时间戳的排在后面按 ID 升序。
    ``timestamp_start`` 可能是 None, 直接比较会 TypeError, 所以用
    ``(is_none, value_or_zero)`` 拆开。
    """
    start = item.get("timestamp_start")
    has_start = start is not None
    try:
        value = float(start) if has_start else 0.0
    except (TypeError, ValueError):
        has_start = False
        value = 0.0
    return (0 if has_start else 1, value, _as_str(item.get("evidence_id")))


def _enrich_evidence(
    evidence: Mapping[str, Any], material_index: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """给证据补上**来源材料**的展示信息 (filename / source_type)。

    补的是材料注册表里已经存在的字段, 不是新事实。材料缺失时
    ``material`` 为 None —— 断链必须显式可见 (spec Invariant 1 的 UI 侧
    要求), 不能静默省略。
    """
    out = dict(evidence)
    source = evidence.get("source") or {}
    material_id = _as_str(source.get("material_id")) if isinstance(source, Mapping) else ""
    record = material_index.get(material_id)
    out["material_id"] = material_id or None
    out["material"] = (
        {
            "material_id": record.get("material_id"),
            "filename": record.get("filename"),
            "material_type": record.get("material_type"),
            "source_type": record.get("source_type"),
        }
        if record is not None
        else None
    )
    return out


class ClassroomWorkspaceView:
    """课程工作台 / 课堂工作台 / 今日 的只读投影。

    ``workspace`` 是 :class:`src.application.workspace.Workspace`; 这里用
    ``Any`` 标注以避免与编排器形成导入环 (类型检查在测试里做)。
    """

    def __init__(self, workspace: Any) -> None:
        self._ws = workspace

    # ------------------------------------------------------------------
    # 56.1 Course Workspace
    # ------------------------------------------------------------------

    def course_workspace(self, course_id: str) -> dict[str, Any]:
        """一门课的工作台: 课程信息 + 课堂列表 + 覆盖 + 待审核 + 最近材料。"""
        cid = _require_id(course_id, "course_id")
        ws = self._ws
        course = ws.get_course(cid)
        metadata = dict(course.get("metadata") or {})

        materials = ws.list_materials(cid)
        points = ws.knowledge_points(cid)
        candidates = ws.review_candidates(cid)
        pending_ids = {
            _as_str(c.get("knowledge_point_id")) for c in candidates
        }

        sessions = [
            self.session_summary(cid, session)
            for session in ws.list_sessions(cid)
        ]

        validation_counts = _count_by(points, "validation_status")
        review_counts = _count_by(points, "review_status")

        try:
            coverage = ws.coverage(cid)
        except Exception:  # noqa: BLE001 - 覆盖率是辅助信息, 失败不该让整页 500
            coverage = {"course_id": cid, "unavailable": True}
        try:
            gaps = ws.gaps(cid)
        except Exception:  # noqa: BLE001 - 同上
            gaps = {"course_id": cid, "gaps": []}

        return {
            "course_id": cid,
            "course": course,
            "teacher": metadata.get(COURSE_TEACHER_KEY),
            "semester": metadata.get(COURSE_SEMESTER_KEY),
            "language": course.get("language"),
            "metadata": metadata,
            "counts": {
                "sessions": len(sessions),
                "materials": len(materials),
                "evidence": sum(
                    len(_as_list(record.get("evidence_ids"))) for record in materials
                ),
                "knowledge_points": len(points),
                "pending_review": len(candidates),
            },
            "sessions": sessions,
            "coverage": coverage,
            "gaps": gaps,
            "pending_review": [
                dict(item) for item in sorted(
                    candidates,
                    key=lambda c: _as_str(c.get("knowledge_point_id")),
                )
            ],
            "pending_review_ids": sorted(pending_ids),
            "knowledge": {
                "count": len(points),
                "by_validation_status": validation_counts,
                "by_review_status": review_counts,
            },
            "recent_materials": _recent_materials(materials, RECENT_MATERIAL_LIMIT),
        }

    # ------------------------------------------------------------------
    # 56.2 / 56.3 Session Workspace
    # ------------------------------------------------------------------

    def session_summary(
        self, course_id: str, session: Mapping[str, Any]
    ) -> dict[str, Any]:
        """一节课的**摘要** (课程页的课堂列表用, 不含重型字段)。"""
        cid = _require_id(course_id, "course_id")
        session_id = _as_str(session.get("session_id"))
        if not session_id:
            raise InvalidInputError("session is missing session_id")

        ws = self._ws
        materials = ws.list_materials(cid, session_id)
        points = ws.knowledge_points(cid, session_id=session_id)
        pending = self._pending_review_for(cid, points)
        status_payload = ws.processing_status(cid, session_id)
        by_status = dict(status_payload.get("by_status") or {})
        job_counts = _job_counts(status_payload)

        status = resolve_session_status(
            material_count=len(materials),
            queued=job_counts["queued"],
            running=job_counts["running"],
            succeeded=job_counts["succeeded"],
            knowledge_count=len(points),
            pending_review=len(pending),
        )
        return {
            "session_id": session_id,
            "course_id": cid,
            "session_number": session.get("session_number"),
            "date": session.get("date"),
            "title": session.get("title"),
            "status": status,
            "counts": {
                "materials": len(materials),
                "evidence": sum(
                    len(_as_list(record.get("evidence_ids"))) for record in materials
                ),
                "knowledge_points": len(points),
                "pending_review": len(pending),
            },
        }

    def session_workspace(self, course_id: str, session_id: str) -> dict[str, Any]:
        """一节课的完整工作台 (spec 56.2 的九个分区)。"""
        cid = _require_id(course_id, "course_id")
        sid = _require_id(session_id, "session_id")
        ws = self._ws

        course = ws.get_course(cid)
        session = ws.get_session(sid)
        if _as_str(session.get("course_id")) != cid:
            raise NotFoundError(
                f"session {sid!r} does not belong to course {cid!r}"
            )

        materials = ws.list_materials(cid, sid)
        material_index = {
            _as_str(record.get("material_id")): record for record in materials
        }
        points = ws.knowledge_points(cid, session_id=sid)
        point_ids = {_as_str(p.get("knowledge_id")) for p in points}
        candidates = self._pending_review_for(cid, points)

        status_payload = ws.processing_status(cid, sid)
        by_status = dict(status_payload.get("by_status") or {})
        job_counts = _job_counts(status_payload)
        status = resolve_session_status(
            material_count=len(materials),
            queued=job_counts["queued"],
            running=job_counts["running"],
            succeeded=job_counts["succeeded"],
            knowledge_count=len(points),
            pending_review=len(candidates),
        )

        buckets = _empty_evidence_buckets()
        all_evidence: list[dict[str, Any]] = []
        seen: set[str] = set()
        for record in materials:
            material_id = _as_str(record.get("material_id"))
            if not material_id:
                continue
            for raw in ws.material_evidence(cid, material_id):
                evidence_id = _as_str(raw.get("evidence_id"))
                if evidence_id and evidence_id in seen:
                    continue
                if evidence_id:
                    seen.add(evidence_id)
                enriched = _enrich_evidence(raw, material_index)
                all_evidence.append(enriched)
                buckets[_classify_evidence(raw)].append(enriched)
        for group in buckets:
            buckets[group].sort(key=_evidence_sort_key)
        all_evidence.sort(key=_evidence_sort_key)

        return {
            "course_id": cid,
            "session_id": sid,
            "course": course,
            "session": session,
            "status": status,
            "status_reason": _status_reason(
                status,
                materials=len(materials),
                succeeded=job_counts["succeeded"],
                failed=job_counts["failed"],
                knowledge=len(points),
                pending_review=len(candidates),
            ),
            # --- Overview ---
            "overview": {
                "materials": len(materials),
                "materials_by_status": _count_by(materials, "processing_status"),
                "evidence": len(all_evidence),
                "evidence_by_group": {
                    group: len(buckets[group]) for group in _EVIDENCE_GROUPS
                },
                "knowledge_points": len(points),
                "knowledge_by_validation_status": _count_by(points, "validation_status"),
                "knowledge_by_review_status": _count_by(points, "review_status"),
                "pending_review": len(candidates),
                "processing_by_status": by_status,
            },
            # --- Materials / Processing ---
            "materials": [_enrich_material(record) for record in materials],
            "processing": {
                **status_payload,
                "stages": _derive_processing_stages(status_payload, len(points)),
            },
            # --- Transcript / OCR / Documents / Notes ---
            "transcript": buckets[_EVIDENCE_GROUP_TRANSCRIPT],
            "ocr": buckets[_EVIDENCE_GROUP_OCR],
            "documents": buckets[_EVIDENCE_GROUP_DOCUMENT],
            "notes": buckets[_EVIDENCE_GROUP_NOTE],
            # --- Evidence ---
            "evidence": all_evidence,
            # --- Knowledge ---
            "knowledge": {
                "count": len(points),
                "points": points,
                "knowledge_ids": sorted(point_ids),
            },
            # --- Review ---
            "review": {
                "pending": candidates,
                "pending_count": len(candidates),
                "history": self._review_history_for(cid, sorted(point_ids)),
            },
            # --- Learning ---
            "learning": self._session_learning(cid, sorted(point_ids)),
        }

    # ------------------------------------------------------------------
    # 56.4 Today
    # ------------------------------------------------------------------

    def today(
        self,
        *,
        course_id: Optional[str] = None,
        student_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """今日入口: 今天的课程 / 课堂 / 未处理材料 / 待审核 / 待学习。

        "今天" 由 Workspace 的时钟决定 (``clock()[:10]``)。这不是装饰 ——
        固定时钟让"今天是哪天"成为可测的输入, 而不是墙上时间。
        """
        ws = self._ws
        now = _as_str(ws._clock())
        today = now[:10]

        courses = ws.list_courses()
        if course_id is not None and _as_str(course_id).strip():
            selected = _as_str(course_id).strip()
            if not any(_as_str(c.get("course_id")) == selected for c in courses):
                raise NotFoundError(f"course {selected!r} not found")
            courses = [c for c in courses if _as_str(c.get("course_id")) == selected]
        else:
            selected = None

        today_sessions: list[dict[str, Any]] = []
        today_courses: list[dict[str, Any]] = []
        pending_materials: list[dict[str, Any]] = []
        pending_review: list[dict[str, Any]] = []

        for course in courses:
            cid = _as_str(course.get("course_id"))
            sessions = ws.list_sessions(cid)
            matched = [s for s in sessions if _as_str(s.get("date")) == today]
            for session in matched:
                summary = self.session_summary(cid, session)
                summary["course_name"] = course.get("name")
                summary["course_code"] = course.get("code")
                today_sessions.append(summary)
            if matched:
                today_courses.append(
                    {
                        "course_id": cid,
                        "name": course.get("name"),
                        "code": course.get("code"),
                        "language": course.get("language"),
                        "sessions_today": len(matched),
                    }
                )

            for record in ws.list_materials(cid):
                if _as_str(record.get("processing_status")) == "COMPLETED":
                    continue
                item = _enrich_material(record)
                item["course_id"] = cid
                item["course_name"] = course.get("name")
                pending_materials.append(item)

            for candidate in ws.review_candidates(cid):
                item = dict(candidate)
                item["course_id"] = cid
                item["course_name"] = course.get("name")
                pending_review.append(item)

        pending_materials.sort(
            key=lambda r: (_as_str(r.get("course_id")), _as_str(r.get("material_id")))
        )
        pending_review.sort(
            key=lambda r: (
                _as_str(r.get("course_id")),
                _as_str(r.get("knowledge_point_id")),
            )
        )
        today_sessions.sort(
            key=lambda s: (_as_str(s.get("course_id")), _as_str(s.get("session_id")))
        )

        study = self._today_study(courses, student_id)

        return {
            "date": today,
            "generated_at": now,
            "course_id": selected,
            "courses_today": today_courses,
            "sessions_today": today_sessions,
            "pending_materials": pending_materials[:TODAY_ITEM_LIMIT],
            "pending_review": pending_review[:TODAY_ITEM_LIMIT],
            "study": study,
            "counts": {
                "courses_today": len(today_courses),
                "sessions_today": len(today_sessions),
                "pending_materials": len(pending_materials),
                "pending_review": len(pending_review),
                "study_items": int(study.get("items_total") or 0),
            },
            "limits": {
                "pending_materials": TODAY_ITEM_LIMIT,
                "pending_review": TODAY_ITEM_LIMIT,
            },
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _pending_review_for(
        self, course_id: str, points: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """把课程级待审核候选收敛到"这节课的知识点"。

        课程级候选是唯一真源 —— 这里只做集合交集, 不重新计算审核判据
        (Invariant 7: 派生视图不得变成第二个互相冲突的真值源)。
        """
        if not points:
            return []
        allowed = {_as_str(p.get("knowledge_id")) for p in points}
        candidates = self._ws.review_candidates(course_id)
        return [
            dict(item)
            for item in candidates
            if _as_str(item.get("knowledge_point_id")) in allowed
        ]

    def _review_history_for(
        self, course_id: str, knowledge_ids: Sequence[str], *, limit: int = 50
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for knowledge_id in knowledge_ids:
            try:
                history = self._ws.review_history(course_id, knowledge_id)
            except Exception:  # noqa: BLE001 - 单条历史缺失不该让整页失败
                continue
            for record in history:
                item = dict(record)
                item.setdefault("knowledge_point_id", knowledge_id)
                out.append(item)
        out.sort(
            key=lambda r: (
                _as_str(r.get("knowledge_point_id")),
                _as_str(r.get("review_id")),
            )
        )
        return out[:limit]

    def _session_learning(
        self, course_id: str, knowledge_ids: Sequence[str]
    ) -> dict[str, Any]:
        """本堂知识点的学生学习状态 (只读)。

        没有学生时明确报 "No learning state yet" —— 绝不把"没有数据"渲染成
        "已掌握 / 已学完" (spec 禁止的产品行为)。
        """
        ws = self._ws
        students = ws.list_students(course_id)
        if not students:
            return {
                "student_id": None,
                "available": False,
                "note": NO_LEARNING_STATE,
                "states": [],
                "pending_exercises": [],
            }
        student = students[0]
        student_id = _as_str(student.get("student_id"))
        allowed = set(knowledge_ids)
        try:
            state = ws.student_state(course_id, student_id)
        except Exception:  # noqa: BLE001
            state = {"states": []}
        states = [
            dict(record)
            for record in (state.get("states") or [])
            if _as_str(record.get("knowledge_point_id")) in allowed
        ]
        states.sort(key=lambda r: _as_str(r.get("knowledge_point_id")))

        exercises = ws.list_exercises(course_id)
        pending: list[dict[str, Any]] = []
        for exercise in exercises:
            linked = {
                _as_str(kid)
                for kid in _as_list(exercise.get("knowledge_point_ids"))
            }
            if linked & allowed:
                pending.append(dict(exercise))
        pending.sort(key=lambda e: _as_str(e.get("exercise_id")))

        return {
            "student_id": student_id,
            "display_name": student.get("display_name"),
            "available": True,
            "note": None,
            "states": states,
            "pending_exercises": pending,
        }

    def _today_study(
        self, courses: Sequence[Mapping[str, Any]], student_id: Optional[str]
    ) -> dict[str, Any]:
        """今日待学习内容: 直接取 StudyPlan, 前端不重新生成 (spec 63.3)。"""
        ws = self._ws
        explicit = _as_str(student_id).strip() or None

        for course in courses:
            cid = _as_str(course.get("course_id"))
            students = ws.list_students(cid)
            if not students:
                continue
            sid = explicit
            if sid is None:
                sid = _as_str(students[0].get("student_id"))
            elif not any(_as_str(s.get("student_id")) == sid for s in students):
                continue
            try:
                # 走 learning_service 而不是 Workspace.study_plan(): 后者会
                # 追加一条计划快照行, 而"今日"是纯展示视图, 不该有写副作用。
                plan = ws.context(cid).learning_service.get_study_plan(sid)
            except Exception:  # noqa: BLE001 - 学生存在但没有计划输入
                plan = None
            if plan is None:
                return {
                    "available": False,
                    "course_id": cid,
                    "student_id": sid,
                    "note": NO_LEARNING_STATE,
                    "items": [],
                    "items_total": 0,
                }
            items = [dict(item) for item in _as_list(plan.get("items"))]
            return {
                "available": True,
                "course_id": cid,
                "student_id": sid,
                "plan_id": plan.get("plan_id"),
                "rules_version": plan.get("rules_version"),
                "note": None,
                "items": items,
                "items_total": len(items),
            }

        return {
            "available": False,
            "course_id": None,
            "student_id": explicit,
            "note": NO_LEARNING_STATE,
            "items": [],
            "items_total": 0,
        }


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _require_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _count_by(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        label = _as_str(row.get(key)) or "unknown"
        out[label] = out.get(label, 0) + 1
    return {k: out[k] for k in sorted(out)}


def _enrich_material(record: Mapping[str, Any]) -> dict[str, Any]:
    """Task 57.3: 给材料记录补上面向用户的失败处理字段, 并防止 traceback 泄漏。

    - ``error_category``: 失败错误码 (确定性, 给用户看"哪类问题")。
    - ``recommended_action``: 由 ``recommended_action()`` 从错误码确定性推导,
      绝不现编、绝不含 traceback。
    - ``error_detail``: 只保留单行短消息; 若原始 detail 含换行 / Traceback /
      ``File "`` 这类痕迹, 截断到第一行, 避免把栈帧甩给用户。
    """
    from src.application.processing_service import recommended_action

    rec = dict(record)
    err = rec.get("error")
    rec["error_category"] = err or ""
    rec["recommended_action"] = recommended_action(
        err, bool(rec.get("retryable"))
    )
    detail = rec.get("error_detail")
    if isinstance(detail, str) and (
        "\n" in detail or "Traceback" in detail or 'File "' in detail
    ):
        first_line = detail.splitlines()[0] if detail.splitlines() else ""
        rec["error_detail"] = first_line[:200]
    return rec


def _recent_materials(
    materials: Sequence[Mapping[str, Any]], limit: int
) -> list[dict[str, Any]]:
    """最近材料: created_at 降序, 同刻按 material_id 升序 (稳定全序)。"""
    ordered = sorted(materials, key=lambda r: _as_str(r.get("material_id")))
    ordered.sort(key=lambda r: _as_str(r.get("created_at")), reverse=True)
    return [_enrich_material(record) for record in ordered[:limit]]


def _status_reason(
    status: str,
    *,
    materials: int,
    succeeded: int,
    failed: int,
    knowledge: int,
    pending_review: int,
) -> str:
    """人话解释当前状态 —— 给 UI 直接显示, 不另外发明状态语义。"""
    if status == SESSION_PLANNED:
        return "No materials registered for this session."
    if status == SESSION_PROCESSING:
        return "Materials are queued or being processed."
    if status == SESSION_MATERIALS_ADDED:
        return f"{materials} material(s) added; none processed successfully yet."
    if status == SESSION_PROCESSED:
        return (
            f"{succeeded} material(s) processed, {failed} failed; "
            "no knowledge points assembled for this session."
        )
    if status == SESSION_REVIEW_REQUIRED:
        return f"{pending_review} knowledge point(s) pending human review."
    return (
        f"{knowledge} knowledge point(s); no review pending. "
        "Ready to study is not mastery."
    )


# ---------------------------------------------------------------------------
# Task 58 — Unified Classroom Timeline
# ---------------------------------------------------------------------------

class TimelineItem:
    """Timeline entry representing a discrete item in a session's knowledge evolution.

    This is the read-model for Task 58's unified timeline, mapping session
    knowledge and evidence into a chronological view.
    """

    def __init__(
        self,
        item_id: str,
        session_id: str,
        item_type: str,
        timestamp: Optional[float],
        end_timestamp: Optional[float],
        title: str,
        source_id: Optional[str] = None,
        material_id: Optional[str] = None,
        knowledge_ids: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self.item_id = item_id
        self.session_id = session_id
        self.item_type = item_type
        self.timestamp = timestamp
        self.end_timestamp = end_timestamp
        self.title = title
        self.source_id = source_id
        self.material_id = material_id
        self.knowledge_ids = knowledge_ids or []
        self.metadata = metadata or {}


class TimelineFilter:
    """Filter controls for the timeline UI.

    Supports filtering by item type, time range, and knowledge/review status.
    """

    def __init__(
        self,
        item_types: Optional[list[str]] = None,
        start_timestamp: Optional[float] = None,
        end_timestamp: Optional[float] = None,
        knowledge_status: Optional[str] = None,
        review_status: Optional[str] = None,
    ) -> None:
        self.item_types = item_types or []
        self.start_timestamp = start_timestamp
        self.end_timestamp = end_timestamp
        self.knowledge_status = knowledge_status
        self.review_status = review_status


def resolve_timeline(
    workspace: Any,
    course_id: str,
    session_id: str,
    *,
    filter: Optional[TimelineFilter] = None,
) -> list[TimelineItem]:
    """Build the unified timeline for a session.

    The timeline maps evidence and knowledge points in chronological order,
    using real timestamps from evidence whenever available. Unknown-timestamp
    items are placed at the end, sorted by stable ID.

    Ordering priority (deterministic):
        1. known timestamp (ascending)
        2. type priority (transcript < OCR < document < note)
        3. stable ID (evidence_id / knowledge_id)

    Args:
        workspace: The application workspace.
        course_id: The course identifier.
        session_id: The session identifier.
        filter: Optional timeline filter.

    Returns:
        A list of TimelineItem sorted by the ordering priority above.
    """
    from src.application.classroom_view import (
        _evidence_sort_key,
        _classify_evidence,
        _EVIDENCE_GROUPS,
        _TYPE_TO_GROUP,
    )

    ws = workspace
    sid = session_id
    cid = course_id

    # ---- Gather materials for this session ----
    materials = ws.list_materials(cid, session_id=sid)
    material_index: dict[str, dict[str, Any]] = {
        str(m.get("material_id")): m for m in materials if m.get("material_id")
    }

    # ---- Gather all evidence for this session ----
    all_evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for material in materials:
        material_id = str(material.get("material_id") or "")
        if not material_id:
            continue
        for raw in ws.material_evidence(cid, material_id):
            eid = str(raw.get("evidence_id") or "")
            if eid and eid in seen:
                continue
            if eid:
                seen.add(eid)
            all_evidence.append(raw)

    # ---- Build timeline items from evidence ----
    items: list[TimelineItem] = []
    for ev in all_evidence:
        eid = str(ev.get("evidence_id") or "")
        source = ev.get("source") or {}
        material_id = str(source.get("material_id") or "")
        timestamp_start = source.get("timestamp_start")
        timestamp_end = source.get("timestamp_end")

        # Determine item type from evidence classification
        ev_type = _classify_evidence(ev)
        type_priority = _TYPE_TO_GROUP.get(ev_type, "other")

        # Title from evidence content or material filename
        title = str(ev.get("content") or material_index.get(material_id, {}).get("filename") or "Untitled")
        if len(title) > 80:
            title = title[:77] + "..."

        items.append(
            TimelineItem(
                item_id=f"ev-{eid}",
                session_id=sid,
                item_type=f"evidence:{type_priority}",
                timestamp=timestamp_start,
                end_timestamp=timestamp_end,
                title=title,
                source_id=eid,
                material_id=material_id,
                metadata={"evidence_type": ev_type, "evidence_raw": ev},
            )
        )

    # ---- Gather knowledge points for this session ----
    points = ws.knowledge_points(cid, session_id=sid)
    point_ids = {str(p.get("knowledge_id")) for p in points}

    # ---- Build timeline items from knowledge points ----
    # Each KP gets a timestamp from its supporting evidence's earliest timestamp
    for kp in points:
        kp_id = str(kp.get("knowledge_id"))
        # Get evidence for this KP and find earliest timestamp
        kp_evidence = ws.knowledge_evidence(cid, kp_id)
        earliest_ts: Optional[float] = None
        latest_ts: Optional[float] = None
        for ev_item in kp_evidence:
            source = ev_item.get("source") or {}
            ts = source.get("timestamp_start")
            if ts is not None:
                if earliest_ts is None or ts < earliest_ts:
                    earliest_ts = ts
            # Also check end timestamp if available
            te = source.get("timestamp_end")
            if te is not None:
                if latest_ts is None or te > latest_ts:
                    latest_ts = te

        # Title from KP content
        title = str(kp.get("title") or kp.get("content") or f"Knowledge {kp_id[:8]}")
        if len(title) > 80:
            title = title[:77] + "..."

        items.append(
            TimelineItem(
                item_id=f"kp-{kp_id}",
                session_id=sid,
                item_type="knowledge",
                timestamp=earliest_ts,
                end_timestamp=latest_ts,
                title=title,
                source_id=kp_id,
                material_id=None,
                knowledge_ids=[kp_id],
                metadata={
                    "validation_status": kp.get("validation_status"),
                    "review_status": kp.get("review_status"),
                    "knowledge_score": kp.get("knowledge_score"),
                },
            )
        )

    # ---- Sort items by deterministic ordering ----
    # Priority: known timestamp ascending, then type priority, then stable ID
    def _timeline_sort_key(item: TimelineItem) -> tuple:
        # Unknown timestamps sort last (1 > 0)
        ts_key = 0 if (item.timestamp is not None) else 1
        ts_val = item.timestamp if item.timestamp is not None else 0.0
        # Type priority: transcript/document/note/other
        type_order = {"transcript": 0, "ocr": 1, "document": 2, "note": 3}.get(
            item.item_type.split(":")[-1] if ":" in item.item_type else "other", 4
        )
        id_key = str(item.item_id)
        return (ts_key, ts_val, type_order, id_key)

    items.sort(key=_timeline_sort_key)

    # ---- Apply filter if provided ----
    if filter is not None:
        filtered: list[TimelineItem] = []
        for item in items:
            # Item type filter
            if filter.item_types and item.item_type not in filter.item_types:
                continue
            # Timestamp range filter
            if filter.start_timestamp is not None and (
                item.timestamp is None or item.timestamp < filter.start_timestamp
            ):
                continue
            if filter.end_timestamp is not None and (
                item.end_timestamp is None or item.end_timestamp > filter.end_timestamp
            ):
                continue
            # Knowledge status filter
            if filter.knowledge_status and item.item_type == "knowledge":
                kp = ws.knowledge_point(cid, item.source_id)
                kp_status = kp.get("review_status") or kp.get("validation_status", "")
                if kp_status != filter.knowledge_status:
                    continue
            # Review status filter
            if filter.review_status and item.item_type == "knowledge":
                # Check review history
                history = ws.review_history(cid, item.source_id)
                has_status = any(
                    r.get("status") == filter.review_status for r in history
                )
                if not has_status:
                    continue
            filtered.append(item)
        items = filtered

    return items
