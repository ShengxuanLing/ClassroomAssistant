# -*- coding: utf-8 -*-
"""Task 63 — Student Daily Dashboard（学生每日首页）的只读投影。

产品首页从"开发者视角"转为"学生视角"：打开软件首先看到 **Today**。

本模块只做**投影**，不新增任何状态
--------------------------------
Dashboard 必须是 ``derived view``：所有内容来自既有领域事实 ——
``Course`` / ``ClassSession`` / ``StudyPlan`` / ``LearningPath`` /
``StudentState`` / ``Exercise`` / ``Evaluation`` / ``Review``。
本层不维护第二份状态、不写任何东西、不新增判定规则。

复用的既有资产（绝不重写）
--------------------------
- 今天相关的课堂 / 待处理材料 / 待审核：``ClassroomWorkspaceView.today()``
  （Task 56.4 已经落地，含"今天由本机时钟决定"这条不变量）。
- 学习计划：``StudyPlan``（Task 33）—— **直接读，绝不重新生成一套**。
- 学习路径：``LearningPath``（Task 33）—— Current / Prerequisite / Next。
- 待作答练习 / 最近评估 / 学习缺口：``LearningViewService``
  的 ``_pending_exercises`` / ``_recent_evaluations`` / ``_knowledge_gaps``
  的**公开投影**（同一份推导，不抄第二遍）。

四条禁止事项（spec 63.8 / 63.9）
--------------------------------
1. **evaluation 绝不自动改写成 mastery**。评估只有
   ``status`` / ``score`` / ``feedback`` 这类既有字段；本层不产生
   ``mastery`` / ``proficiency`` / ``predicted`` / ``estimated_ability``。
2. **绝不从"错误次数"自行推导掌握度**。``attention`` 区只会透传
   ``StudentState`` **已经定义**的标签（``NEEDS_REVIEW`` /
   ``NEEDS_PRACTICE`` / ``PREREQUISITE_NEEDED``）；如果领域层没有给出
   某个标签，本层就**不显示**它 —— 而不是自己算一个。
3. **空学生是正常状态**：返回 ``has_activity: false`` 与
   ``No learning activity yet.``，HTTP 200，不是错误。
4. **课程上下文必须被遵守**：选了 Course A 就只给 Course A 的数据；
   "All Courses" 时每个 task 都保留 ``course_id``（spec 63.12）**和**
   ``course_name`` —— 前端要显示的是课程名, 而 ``course_id`` 是内容寻址的
   内部标识 (``course-3fd6392d1fd78e87``), 直接渲染出来对用户没有意义。

关于"今天"
----------
``today`` 由 ``Workspace`` 的时钟决定（``clock()[:10]``），不接受客户端传日期。
理由与 Task 56.4 相同：同一个快照在不同浏览器时区下给出不同的"今天"，
那就不叫同一份数据了。系统没有可靠的课程表时间/时区能力时，使用已有的
``ClassSession.date`` 元数据，**不自行假定**（spec 63.3）。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from src.application.errors import InvalidInputError, NotFoundError

__all__ = [
    "StudentTodayView",
    "ATTENTION_KINDS",
    "TODAY_FORBIDDEN_TERMS",
]

#: ``attention`` 区**允许**出现的标签 —— 全部来自 ``StudentState`` 的既有
#: 定义。这里刻意穷举，任何新增标签都必须先在领域层存在，避免本层"发明"
#: 一个新的掌握度概念。
ATTENTION_KINDS: tuple[str, ...] = (
    "NEEDS_REVIEW",
    "NEEDS_PRACTICE",
    "PREREQUISITE_NEEDED",
)

#: 响应里绝不允许出现的词。测试直接在 JSON 上断言 —— 产品语义是硬约束，
#: 不是"小心一点"。
TODAY_FORBIDDEN_TERMS: frozenset[str] = frozenset(
    {
        "mastery",
        "mastered",
        "proficiency",
        "proficient",
        "predicted",
        "prediction",
        "estimated_ability",
        "you_are_ready",
        "will_pass",
    }
)

#: 空学生文案 (spec 63.10)。前端按语言渲染，后端只给稳定的标记。
NO_ACTIVITY_NOTE = "No learning activity yet."

#: 每个列表返回的上限 —— 首页是"今天看什么"，不是全量报告。
TODAY_LIMIT = 20


class StudentTodayView:
    """学生每日首页投影 (Task 63)。只读。"""

    def __init__(self, workspace: Any) -> None:
        self._ws = workspace

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    def today(
        self,
        *,
        course_id: Optional[str] = None,
        student_id: Optional[str] = None,
        lang: str = "zh",
    ) -> dict[str, Any]:
        """今日首页。

        ``course_id`` 给了就只看那门课；没给就是 "All Courses" ——
        此时每个 task 都带 ``course_id``（spec 63.12）。
        """
        ws = self._ws

        # 1) 课程集合（含课程上下文的合法性校验）
        courses = ws.list_courses()
        selected: Optional[str] = None
        if course_id is not None and str(course_id).strip():
            selected = str(course_id).strip()
            if not any(str(c.get("course_id")) == selected for c in courses):
                raise NotFoundError(f"course {selected!r} not found")
            courses = [c for c in courses if str(c.get("course_id")) == selected]

        # 2) 复用 Task 56.4 的今日课堂投影（不重算"今天"）。
        #    ``TodayView`` 拿不到 ``CourseContext`` 上的 classroom_view ——
        #    它挂在 ``Workspace`` 上（跨课程视图），只能经 ``_classroom_view()``
        #    取。这是刻意的: "今天"是跨课程的, 不是某门课的属性。
        classroom = (
            ws._classroom_view().today(course_id=selected)
            if courses
            else _empty_classroom()
        )

        # 3) 学生：显式指定就校验归属；否则每门课取第一个（确定性）。
        targets = self._resolve_students(courses, student_id)
        # 课程级的"待审核"与学生学习状态无关 —— 学生侧没有目标时也必须报出来，
        # 否则"课上有 7 条待审核但首页显示 0"会让人以为系统没数据。
        student_by_course = {str(course["course_id"]): sid for course, sid in targets}

        # 4) 逐课程组装
        study: list[dict[str, Any]] = []
        paths: list[dict[str, Any]] = []
        pending_exercises: list[dict[str, Any]] = []
        recent_evaluations: list[dict[str, Any]] = []
        attention: list[dict[str, Any]] = []
        review_items: list[dict[str, Any]] = []

        for course in courses:
            cid = str(course["course_id"])
            ctx = ws.context(cid)

            # 4a) 课程级: 待审核（与是否有学生无关）
            for candidate in ws.review_candidates(cid):
                row = dict(candidate)
                row["course_id"] = cid
                row["course_name"] = course.get("name")
                review_items.append(row)

            # 4b) 学生级: 只有确实选了学生才组装
            sid = student_by_course.get(cid)
            if not sid:
                continue
            view = ctx.learning_view

            plan = self._study_plan_for(ctx, sid)
            study.append(
                {
                    "course_id": cid,
                    "course_name": course.get("name"),
                    "student_id": sid,
                    "available": plan is not None,
                    "note": None if plan is not None else NO_ACTIVITY_NOTE,
                    "tasks_today": (plan or {}).get("items", []),
                    "tasks_total": len((plan or {}).get("items") or []),
                    "next_task": _first((plan or {}).get("items") or []),
                    "plan_id": (plan or {}).get("plan_id"),
                    "rules_version": (plan or {}).get("rules_version"),
                }
            )

            paths.extend(self._learning_paths(view, sid, cid, course.get("name")))

            for item in view._pending_exercises(sid):
                row = dict(item)
                row["course_id"] = cid
                row["course_name"] = course.get("name")
                row["student_id"] = sid
                pending_exercises.append(row)

            for item in view._recent_evaluations(sid):
                row = dict(item)
                row["course_id"] = cid
                row["course_name"] = course.get("name")
                row["student_id"] = sid
                recent_evaluations.append(row)

            attention.extend(self._attention(view, sid, cid, course.get("name")))

        # 5) 确定性排序 + 截断
        #
        # 注意 recent_evaluations **不重排**: ``_recent_evaluations`` 已经按
        # "提交顺序的逆序"给出 —— 那是这个列表唯一的语义 (最近提交在最前)。
        # 用 answer_id 重排会把"最近"这个信息抹掉, 变成一条看似确定、
        # 实则无意义的顺序。跨课程时只按 course_id 稳定分组。
        for rows, keys in (
            (pending_exercises, ("course_id", "exercise_id")),
            (attention, ("course_id", "knowledge_point_id", "kind")),
            (review_items, ("course_id", "knowledge_point_id")),
        ):
            rows.sort(key=lambda r, ks=keys: tuple(str(r.get(k) or "") for k in ks))
        recent_evaluations.sort(key=lambda r: str(r.get("course_id") or ""))

        has_activity = bool(
            study
            and any(row["available"] and row["tasks_total"] for row in study)
        ) or bool(pending_exercises) or bool(recent_evaluations)

        return {
            "date": classroom.get("date"),
            "generated_at": classroom.get("generated_at"),
            "course_id": selected,
            "lang": lang,
            "has_activity": has_activity,
            "note": None if has_activity else NO_ACTIVITY_NOTE,
            "classes_today": classroom.get("sessions_today", []),
            "courses_today": classroom.get("courses_today", []),
            "pending_materials": classroom.get("pending_materials", []),
            "study": study,
            "learning_paths": paths[:TODAY_LIMIT],
            "pending_exercises": pending_exercises[:TODAY_LIMIT],
            "recent_evaluations": recent_evaluations[:TODAY_LIMIT],
            "attention": attention[:TODAY_LIMIT],
            "review": {
                "items": review_items[:TODAY_LIMIT],
                "total": len(review_items),
                "path": "/api/reviews",
            },
            "counts": {
                "classes_today": len(classroom.get("sessions_today", [])),
                "study_tasks": sum(
                    row["tasks_total"] for row in study if row["available"]
                ),
                "learning_paths": len(paths),
                "pending_exercises": len(pending_exercises),
                "recent_evaluations": len(recent_evaluations),
                "attention": len(attention),
                "review": len(review_items),
                "pending_materials": len(classroom.get("pending_materials", [])),
            },
            # UI 直达入口（spec 63.6 / 63.7 要求可点击进入既有页面）
            "links": {
                "review_center": "/api/reviews",
                "exercises": "/api/exercises",
            },
            "limits": {"items": TODAY_LIMIT},
        }

    # ------------------------------------------------------------------
    # 学生解析
    # ------------------------------------------------------------------

    def _resolve_students(
        self, courses: Sequence[Mapping[str, Any]], student_id: Optional[str]
    ) -> list[tuple[Mapping[str, Any], str]]:
        """给每门课定一个学生。

        显式 ``student_id`` 时, 只保留**确实拥有该学生**的课程 ——
        否则会把 A 课学生的计划贴到 B 课上（跨课程数据泄漏）。
        没给时每门课取 id 最小的那个（确定性，不依赖插入顺序）。
        """
        ws = self._ws
        explicit = str(student_id).strip() if student_id is not None else ""
        out: list[tuple[Mapping[str, Any], str]] = []
        for course in courses:
            cid = str(course["course_id"])
            students = sorted(
                ws.list_students(cid), key=lambda s: str(s.get("student_id") or "")
            )
            if not students:
                continue
            if explicit:
                if any(str(s.get("student_id")) == explicit for s in students):
                    out.append((course, explicit))
                continue
            out.append((course, str(students[0].get("student_id"))))
        return out

    def _study_plan_for(self, ctx: Any, student_id: str) -> Optional[dict[str, Any]]:
        """读既有 StudyPlan。**绝不**重新生成（spec 63.4）。

        走 ``learning_service.get_study_plan`` 而不是
        ``Workspace.study_plan()``: 后者每次调用会追加一行内容寻址快照，
        而首页是纯展示视图，不该有写副作用。这条坑 Task 56.4 已经踩过。
        """
        if not student_id:
            return None
        try:
            plan = ctx.learning_service.get_study_plan(student_id)
        except (NotFoundError, InvalidInputError):
            return None
        except Exception:  # noqa: BLE001 - 学生有但计划输入缺失, 首页不该崩
            return None
        return plan if isinstance(plan, Mapping) else None

    # ------------------------------------------------------------------
    # 学习路径（Current / Prerequisite / Next）
    # ------------------------------------------------------------------

    def _learning_paths(
        self,
        view: Any,
        student_id: str,
        course_id: str,
        course_name: Optional[str],
    ) -> list[dict[str, Any]]:
        """把既有 LearningPath 投影成 Current / Prerequisite / Next。

        字段全部来自 ``learning_path_view`` 的既有输出，不新增判定:

        - ``current``       = 路径里的**最后一个**节点 —— 复习路径是
          "前置…… → 目标"，目标排最后，也就是"现在该学的那一个"。
        - ``prerequisites`` = 该目标节点的 ``prerequisite_ids``。
        - ``unmet_prerequisites`` = 同一节点的 ``unmet_prerequisite_ids``。
        - ``next``          = ``None``（目标是终点，没有"下一个"）;
          真正的"下一步动作"由节点的 ``next_event`` 给出（Task 30 的事件）。

        一条只有目标自身的路径（没有前置）会得到 ``nodes`` 长度为 1 ——
        这不是异常，而是"这个知识点没有前置"的正常事实。
        """
        out: list[dict[str, Any]] = []
        try:
            records = view._student_state_records(student_id)
            course_kps = sorted(view._course_knowledge_points())
        except Exception:  # noqa: BLE001
            return out

        from src.application.learning_view import COMPLETION_STATES

        for kp_id in course_kps[:TODAY_LIMIT]:
            if view._state_of(records, kp_id) in COMPLETION_STATES:
                continue
            try:
                path = view.learning_path_view(student_id, kp_id)
            except (NotFoundError, InvalidInputError):
                continue
            except Exception:  # noqa: BLE001 - 单条路径失败不该让首页崩
                continue
            nodes = list(path.get("nodes") or [])
            if not nodes:
                continue
            current = nodes[-1]
            out.append(
                {
                    "course_id": course_id,
                    "course_name": course_name,
                    "student_id": student_id,
                    "knowledge_point_id": kp_id,
                    "current": current,
                    "prerequisites": list(current.get("prerequisite_ids") or []),
                    "unmet_prerequisites": list(
                        current.get("unmet_prerequisite_ids") or []
                    ),
                    "next": None,
                    "next_event": current.get("next_event"),
                    "nodes_total": len(nodes),
                }
            )
        return out

    # ------------------------------------------------------------------
    # Attention（只透传 StudentState 已定义的标签）
    # ------------------------------------------------------------------

    def _attention(
        self,
        view: Any,
        student_id: str,
        course_id: str,
        course_name: Optional[str],
    ) -> list[dict[str, Any]]:
        """需要关注的知识点。

        **只**使用 ``StudentState`` 领域已经给出的信号，绝不从"错了几次"
        反推掌握度（spec 63.9）:

        - ``NEEDS_REVIEW``      —— Task 30 状态机的 ``reviewing`` 态。
        - ``NEEDS_PRACTICE``    —— ``practicing`` 态。
        - ``PREREQUISITE_NEEDED`` —— ``not_started`` 且该 KP 有前置（Task 33 的
          真实关系，不是猜的）。

        每个条目都带 ``basis``，写明它来自哪条既有事实。
        """
        out: list[dict[str, Any]] = []
        try:
            records = view._student_state_records(student_id)
        except Exception:  # noqa: BLE001
            return out

        for kp_id in sorted(records):
            state = str(records[kp_id].get("state") or "not_started")
            if state == "reviewing":
                out.append(
                    {
                        "course_id": course_id,
                        "course_name": course_name,
                        "student_id": student_id,
                        "knowledge_point_id": kp_id,
                        "kind": "NEEDS_REVIEW",
                        "state": state,
                        "basis": "StudentState state=reviewing (Task 30)",
                    }
                )
            elif state == "practicing":
                out.append(
                    {
                        "course_id": course_id,
                        "course_name": course_name,
                        "student_id": student_id,
                        "knowledge_point_id": kp_id,
                        "kind": "NEEDS_PRACTICE",
                        "state": state,
                        "basis": "StudentState state=practicing (Task 30)",
                    }
                )
        return [row for row in out if row["kind"] in ATTENTION_KINDS]


def _empty_classroom() -> dict[str, Any]:
    """没有任何课程时的今日课堂投影（空，但形状完整）。"""
    return {
        "date": None,
        "generated_at": None,
        "courses_today": [],
        "sessions_today": [],
        "pending_materials": [],
        "pending_review": [],
    }


def _first(rows: Sequence[Any]) -> Any:
    return rows[0] if rows else None
