# -*- coding: utf-8 -*-
"""Task 68 — Multi-Course Workspace（多课程工作台, 只读投影层）。

本模块回答的问题是:

    我有哪几门课? 每门课现在是什么状态? 我上一秒在看哪一门?

它**不回答**:

    哪门课更重要? / 哪门课该先学? / 我这学期能拿几分?

第二条里的东西需要"重要性"这种**本产品没有的事实**, 因此这里一个字都
不给。课程顺序是 ``course_id`` 的字典序 —— 一个**稳定但不带含义**的键,
不是"推荐顺序"。

四条硬约束
------------

1. **严格课程隔离 (spec 68.2)。** 每一行数字都只由 ``course_id`` 限定
   的查询得出: ``knowledge_points(cid)`` / ``list_sessions(cid)`` /
   ``list_students(cid)`` / ``list_exercises(cid)``。没有任何一处把两门
   课的结果并进同一个集合里再拆分 —— 那种写法一旦过滤条件写漏就会串课。
   总览是**逐课程各算一份再并列**, 不是"先合并再分组"。

2. **内容寻址 ID 跨课程碰撞是合法的 (spec 68 明示)。** 两门课用了同一份
   讲义就会产出**同一个** ``knowledge_id``。这不是 bug, 也不代表它们是
   同一个知识点 —— 它们只是同样内容在两门课里各有一份。因此本模块**从不**
   用 ``knowledge_id`` 做全局键, 也**从不**尝试"去重"。真正的要求是
   course-scoped query, 不是全局唯一。

3. **两条真相轴仍然分开 (全局铁律 4)。** ``validation`` (证据说了什么) 与
   ``review`` (人说了什么) 在每一行里各成一个字典, 各计各的数。绝不合成
   "可信度" / "完成度" 之类的单值 —— 那会让"证据冲突但人已确认"这类事实
   消失, 也会让"计数即掌握度"的旧病复发。

4. **确定性 + 只读。** 排序键显式 (``course_id``), 同一份数据在任何进程
   里产出逐字节相同的总览。类上只有读方法 —— 它没有改任何东西的能力,
   所以"切换课程会不会写脏数据"这个问题在结构上不存在。

课程切换为什么在这里
--------------------

"我上一次在看哪门课"是一个**选择**, 不是业务事实, 所以它存在浏览器里
(``localStorage``)。但"这个选择还成不成立"必须能判定: 课程可能被删,
localStorage 里的 id 可能是脏的。``resolve_selection()`` 就是这条规则 ——
给定偏好, 返回**唯一确定**的结果和**为什么是这个结果**。它是纯函数式的
只读判定, 因此可测、可重启复现。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from src.application.errors import InvalidInputError

__all__ = [
    "MULTI_COURSE_VERSION",
    "MY_COURSES_EMPTY_NOTE",
    "COUNT_KEYS",
    "VALIDATION_STATUSES",
    "REVIEW_STATUSES",
    "SELECTION_REASONS",
    "NO_RANKING_TERMS",
    "MultiCourseWorkspace",
]

#: 投影契约版本。客户端可据此判断字段语义是否变化。
MULTI_COURSE_VERSION = "multi-course-workspace-v1"

#: 空状态文案 (英文原文是契约的一部分, 与 Task 66/67 保持一致风格)。
MY_COURSES_EMPTY_NOTE = "No courses yet."

#: 每门课都会给出的计数键。缺数据就是 0 —— 不猜、不补、不省略键。
COUNT_KEYS: tuple[str, ...] = (
    "sessions",
    "materials",
    "knowledge_points",
    "pending_review",
    "conflicts",
    "students",
    "exercises",
    "answers",
)

#: 真相轴一: 证据验证状态 (来自知识点的派生状态)。
VALIDATION_STATUSES: tuple[str, ...] = ("supported", "unverified", "conflicted")

#: 真相轴二: 人工审核状态。**与上面那条轴永远不合并**。
REVIEW_STATUSES: tuple[str, ...] = (
    "confirmed",
    "pending",
    "rejected",
    "kept_unverified",
)

#: 课程选择的判定结果。UI 据此决定要不要纠正 localStorage 里的值。
SELECTION_REASONS: tuple[str, ...] = (
    "preferred",  # 偏好存在且仍然有效
    "preferred_missing",  # 偏好指向的课程已不存在 -> 退回第一门
    "first_course",  # 没有偏好 -> 取第一门 (确定性)
    "no_courses",  # 一门课都没有
)

#: 禁止出现在"我的课程"总览里的词。这不是订单/推荐系统: 课程顺序是
#: 字典序, 不是"该先学哪门"。出现这些词就意味着有人偷偷塞进了排序意图。
NO_RANKING_TERMS: tuple[str, ...] = (
    "priority",
    "recommended",
    "suggested order",
    "most important",
    "优先级",
    "推荐顺序",
    "更重要",
    "先学这门",
    "orden recomendado",
    "más importante",
    "ordre recomanat",
    "més important",
)


class MultiCourseWorkspace:
    """多课程总览的只读投影。

    参数
    ----
    workspace:
        ``application.workspace.Workspace``。这里只**读**它的方法,
        从不调用任何带 ``with self._atomic()`` 的写入口。

    公开方法**只有** ``my_courses`` / ``course_summary`` /
    ``resolve_selection`` 三个。测试会把 ``dir()`` 的结果与这个白名单
    逐一比对 —— 一旦有人加了写方法, 那条测试会立刻失败。
    """

    def __init__(self, workspace: Any) -> None:
        self._ws = workspace

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def my_courses(
        self,
        *,
        lang: str = "zh",
        preferred: Optional[str] = None,
    ) -> dict[str, Any]:
        """全部课程的逐课程总览 (每门课各算一份, 再并列)。

        ``preferred`` 是调用方"上一秒在看哪门课"的偏好; 它**只**影响
        ``selection`` 块的判定, 不影响任何计数, 也不影响课程顺序。
        """
        language = str(lang or "zh")
        courses = sorted(
            (dict(c) for c in self._ws.list_courses()),
            key=lambda row: str(row.get("course_id") or ""),
        )
        rows = [self._row(course) for course in courses]
        totals = self._totals(rows)
        return {
            "view": MULTI_COURSE_VERSION,
            "language": language,
            "course_count": len(rows),
            "selection": self.resolve_selection(preferred),
            "courses": rows,
            "totals": totals,
            "note": (
                "每门课的计数都由该课程的 course_id 单独查询得出；"
                "不同课程可以有相同的 knowledge_id（内容相同即 ID 相同），"
                "这里是并列展示，不是同一个知识点。"
            ),
            "empty": not rows,
            "empty_note": MY_COURSES_EMPTY_NOTE if not rows else None,
        }

    def course_summary(self, course_id: str, *, lang: str = "zh") -> dict[str, Any]:
        """单门课的一行总览 —— 与 ``my_courses()["courses"][i]`` 同一形状。"""
        cid = self._require_course_id(course_id)
        for course in self._ws.list_courses():
            if str(course.get("course_id")) == cid:
                row = self._row(dict(course))
                row["language_ui"] = str(lang or "zh")
                return row
        # list_courses() 与 context() 的真源相同, 走到这里说明课程确实没了。
        from src.application.errors import NotFoundError

        raise NotFoundError(f"course {cid!r} not found")

    def resolve_selection(self, preferred: Optional[str] = None) -> dict[str, Any]:
        """把"我想看这门课"解析成"实际该看这门课", 并说明为什么。

        为什么需要它: localStorage 里存的 course_id 可能指向一门**已经被删掉**
        的课。没有这条规则的话 UI 会拿着一个不存在的 id 去请求, 用户看到
        一个 404 而不是自己的课程表。
        """
        wanted: Optional[str] = None
        if preferred is not None:
            wanted = str(preferred).strip() or None
        ids = sorted(
            str(c.get("course_id") or "")
            for c in self._ws.list_courses()
            if c.get("course_id")
        )
        if not ids:
            return {
                "preferred": wanted,
                "course_id": None,
                "reason": "no_courses",
                "available": [],
            }
        if wanted is not None and wanted in ids:
            return {
                "preferred": wanted,
                "course_id": wanted,
                "reason": "preferred",
                "available": ids,
            }
        if wanted is not None:
            return {
                "preferred": wanted,
                "course_id": ids[0],
                "reason": "preferred_missing",
                "available": ids,
            }
        return {
            "preferred": None,
            "course_id": ids[0],
            "reason": "first_course",
            "available": ids,
        }

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _require_course_id(course_id: Any) -> str:
        if not isinstance(course_id, str) or not course_id.strip():
            raise InvalidInputError("course_id must be a non-empty string")
        return course_id.strip()

    def _row(self, course: Mapping[str, Any]) -> dict[str, Any]:
        """一门课的一行。**所有**查询都带 ``cid`` —— 没有一处是全局查再筛。"""
        cid = str(course.get("course_id") or "")
        points = list(self._ws.knowledge_points(cid))
        sessions = list(self._ws.list_sessions(cid))
        materials = list(self._ws.list_materials(cid))

        validation: dict[str, int] = {name: 0 for name in VALIDATION_STATUSES}
        review: dict[str, int] = {name: 0 for name in REVIEW_STATUSES}
        for point in points:
            self._bump(validation, point.get("validation_status"))
            self._bump(review, point.get("review_status"))

        processing = self._ws.processing_status(cid) or {}
        counts = {
            "sessions": len(sessions),
            "materials": len(materials),
            "knowledge_points": len(points),
            "pending_review": len(self._ws.review_candidates(cid)),
            "conflicts": len(self._ws.conflicts(cid)),
            "students": len(self._ws.list_students(cid)),
            "exercises": len(self._ws.list_exercises(cid)),
            "answers": self._answer_count(cid),
        }
        # 键顺序固定 (COUNT_KEYS), 避免"同样的数、不同的 JSON"。
        counts = {key: int(counts.get(key, 0)) for key in COUNT_KEYS}

        return {
            "course_id": cid,
            "name": course.get("name") or "",
            "code": course.get("code") or "",
            "language": course.get("language") or "",
            "counts": counts,
            "validation": validation,
            "review": review,
            "evidence_total": int(processing.get("evidence_total") or 0),
            "gaps": len((self._ws.gaps(cid) or {}).get("gaps") or []),
            "last_session": self._last_session(sessions),
        }

    def _answer_count(self, course_id: str) -> int:
        """该课程的作答条数。

        优先走学习服务的内存计数 (``O(1)``); 服务没有暴露该能力时退回
        0 —— **不猜、不用练习数冒充作答数**。
        """
        service = getattr(self._ws.context(course_id), "learning_service", None)
        counter = getattr(service, "answer_count", None)
        if callable(counter):
            try:
                return int(counter())
            except (TypeError, ValueError):
                return 0
        return 0

    @staticmethod
    def _bump(bucket: dict[str, int], status: Any) -> None:
        if status is None:
            return
        key = str(status)
        bucket[key] = int(bucket.get(key, 0)) + 1

    @staticmethod
    def _last_session(sessions: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        """最近一节课堂: 按 ``(session_number, session_id)`` 取最大。

        不用时间 —— ``date`` 是用户手填的字符串, 可能为空也可能不是 ISO,
        拿它排序会得到不稳定的结果。
        """
        if not sessions:
            return None
        ordered = sorted(
            sessions,
            key=lambda row: (
                int(row.get("session_number") or 0),
                str(row.get("session_id") or ""),
            ),
        )
        last = ordered[-1]
        return {
            "session_id": last.get("session_id"),
            "session_number": last.get("session_number"),
            "title": last.get("title") or "",
            "date": last.get("date") or "",
        }

    @staticmethod
    def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
        """所有课程的合计。**两条轴仍然分开合计**, 不合并。"""
        counts = {key: 0 for key in COUNT_KEYS}
        validation = {name: 0 for name in VALIDATION_STATUSES}
        review = {name: 0 for name in REVIEW_STATUSES}
        for row in rows:
            for key in COUNT_KEYS:
                counts[key] += int((row.get("counts") or {}).get(key, 0))
            for name, value in (row.get("validation") or {}).items():
                validation[name] = int(validation.get(name, 0)) + int(value)
            for name, value in (row.get("review") or {}).items():
                review[name] = int(review.get(name, 0)) + int(value)
        return {
            "counts": counts,
            "validation": validation,
            "review": review,
            "evidence_total": sum(int(row.get("evidence_total") or 0) for row in rows),
            "gaps": sum(int(row.get("gaps") or 0) for row in rows),
        }
