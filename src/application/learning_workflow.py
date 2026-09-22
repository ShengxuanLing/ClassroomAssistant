# -*- coding: utf-8 -*-
"""Daily Learning Workflow (Task 66).

把已经存在的 Domain 能力串成一条**可连续使用、可恢复**的今天学习流程：

    Today
     -> Current Learning Task
     -> Knowledge
     -> Evidence
     -> Grounded Explanation
     -> Exercise
     -> Answer
     -> Evaluation
     -> Student State
     -> Next Learning Task

本模块**不重新实现任何 Domain 能力**（spec 66.3 / 66.6 / 66.7）
------------------------------------------------------------
- 学习计划 / 学习路径: ``StudyPlan`` / ``LearningPath``（Task 33），
  经 ``LearningService`` 只读取出，绝不另建第二套。
- 知识点事实 / 证据: ``KnowledgeService``（Task 22/23）。
- 练习生成: ``ExerciseWorkflow``（Task 64），本层只调用。
- 作答 / 评估: ``LearningService.submit_answer``（Task 31/32）。
- 状态推进: Task 30 的事件与转移规则（``StudentLearningLog``）。

本层只做两件事
--------------
1. **选择**「当前学习任务」—— 一个纯函数的、确定性的选择规则。
2. **投影**「学完这一步之后看什么」—— 把已有事实重新组织成一条链路。

## 选择规则（spec 66.3，必须 deterministic）

候选集合 = 该课程的知识点中，**允许成为正式学习事实来源**的那些：

- ``validation_status == "rejected"`` 排除（知识已被人为否决）；
- ``validation_status == "conflicted"`` 或带未解决冲突的排除
  （spec 66.3.7：未验证 / 冲突知识不得成为正式学习事实来源）；
- 其余（``supported`` / ``unverified`` / ``pending``）保留 —— ``unverified``
  知识可以**看**（会带 ``unverified`` 标记与 banner），只是不能当已核实事实。

排序键（稳定、全序、不含随机性，可复现）：

1. **是否可推进**：有合法 next event 的排在前面（"能做的先做"）；
2. **前置完成度**：未完成前置数量升序 —— 但**只把有合法 next event 的
   候选项算作"已完成"**，因为"答对了"不是 Task 30 的状态推进
   （spec 66.3.6：一个错误答案 ≠ mastery failure，反过来，"答对过"
   也不等于 mastered）；
3. **状态序**：``not_started < exposed < practicing < reviewing``
   —— 状态机自身的顺序，越靠前越"新"；
4. ``session_number`` 升序（课堂顺序；没有 session 视为 −1，排最前）；
5. ``knowledge_id`` 升序 —— 最后的全序兜底，保证同一份数据永远给出同一个答案。

**绝不**使用：随机选择、考试概率、重要度加权、最近错误次数。
``importance`` / ``knowledge_score`` 都只是**展示**字段，不参与排序。

## 关于"next"

``learning_path_view`` 的节点已经给出 ``next_event``
（= ``LearningService.next_learning_event(state)``，其映射表由
``tests/test_learning_view.py`` 与领域层 ``_TRANSITIONS`` 逐项比对）。
本层**只消费**它，不自己再写一份映射。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from src.application.errors import InvalidInputError, NotFoundError

__all__ = [
    "LearningWorkflow",
    "WORKFLOW_VERSION",
    "ORDERABLE_STATES",
    "EXCLUDED_VALIDATION_STATUSES",
    "EXCLUDED_REVIEW_STATUSES",
    "WORKFLOW_FORBIDDEN_TERMS",
]

WORKFLOW_VERSION = "daily-learning-workflow-v1"

#: 状态机的顺序（Task 30 的状态定义顺序）。越小越"新"。
ORDERABLE_STATES: tuple[str, ...] = (
    "not_started",
    "exposed",
    "practicing",
    "reviewing",
)

#: ``validation_status`` 中**不得**成为正式学习任务的取值。
#:
#: ``conflicted`` = 两条证据互相矛盾（Task 15/23）—— 在人工解决之前，
#: 任何一方都不许当事实用。
EXCLUDED_VALIDATION_STATUSES: frozenset[str] = frozenset({"conflicted"})

#: ``review_status`` 中**不得**成为正式学习任务的取值。
#:
#: ``rejected`` = 人工审查已经否决了这条知识（Task 15）。
#:
#: **两个轴必须分别检查**（项目铁律 4：``validation_status`` 与
#: ``review_status`` 绝不合并）。实测确认它们是真正独立的：
#: ``review_reject()`` 只把 ``review_status`` 改成 ``rejected``，
#: ``validation_status`` 仍然是 ``supported`` —— 只查前者会漏掉全部
#: 人工否决的知识。
EXCLUDED_REVIEW_STATUSES: frozenset[str] = frozenset({"rejected"})

#: 响应里绝不允许出现的词（spec 66.8 禁止 "You mastered this topic."）。
WORKFLOW_FORBIDDEN_TERMS: frozenset[str] = frozenset(
    {
        "mastery",
        "mastered",
        "proficiency",
        "proficient",
        "predicted",
        "prediction",
        "estimated_ability",
        "exam_probability",
        "most_likely",
        "guaranteed",
    }
)

#: 没有证据时的固定文案（三语共用，因为它描述的是"知识库里没有"这个事实）。
EVIDENCE_UNAVAILABLE = "Evidence unavailable."

#: 没有任何可学习任务时的固定文案。
NO_TASK_NOTE = "No learning task available."

#: 学生尚未开始时的固定文案。
NO_ACTIVITY_NOTE = "No learning activity yet."

#: 单个响应里列表的上限 —— 这是"今天做什么"，不是全量报告。
WORKFLOW_LIMIT = 20


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _truthy_conflict(value: Any) -> bool:
    """``conflict`` 字段的真值判据（大小写不敏感）。

    HTTP 查询串里布尔值永远是字符串（``"true"`` / ``"false"``），
    直接 ``bool("false")`` 为真 —— 那是本项目已经踩过的坑
    （见 ``Workspace.knowledge_points`` 的 ``_as_optional_bool``）。
    排除了 ``rejected`` 只是因为 ``False`` 与 ``"false"`` 都要判假。
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return _clean(value).lower() not in ("", "false", "0", "no", "none")


class LearningWorkflow:
    """一天的学习流程：选任务 -> 看知识 -> 做练习 -> 看评价 -> 下一步。

    所有方法都是**只读**的，除了 :meth:`open_knowledge` ——
    它会按 Task 30 的规则记一个 ``viewed`` 事件（这正是"打开知识页"的语义）。
    :meth:`answer` 委托既有作答路径。除此之外本层不写任何东西。
    """

    def __init__(self, workspace: Any) -> None:
        self._ws = workspace

    # ------------------------------------------------------------------
    # 入口：今天的学习流程
    # ------------------------------------------------------------------

    def start(
        self,
        course_id: str,
        student_id: str,
        *,
        lang: str = "zh",
    ) -> dict[str, Any]:
        """「开始今天的学习」的入口。

        返回当前学习任务（含前置状态与下一步），**不写任何东西**。
        没有任何可学任务时 ``has_task: false`` + ``note``，HTTP 仍是 200 ——
        空课程 / 空学生是正常状态，不是错误。
        """
        cid, sid = self._require(course_id, student_id)
        ctx = self._ws.context(cid)
        # 学生必须存在（否则就是真的 404，而不是"没有任务"）。
        ctx.learning_service.get_student(sid)

        candidates, excluded = self._candidates(ctx, cid, sid)
        task = candidates[0] if candidates else None

        return {
            "course_id": cid,
            "student_id": sid,
            "lang": lang,
            "workflow_version": WORKFLOW_VERSION,
            "has_task": task is not None,
            "note": None if task else NO_TASK_NOTE,
            "current_task": task,
            "next_task": self._peek_next(candidates),
            "progress": self._progress(ctx, cid, sid, candidates, excluded),
            "excluded": {
                "rejected": excluded["rejected"],
                "conflicted": excluded["conflicted"],
            },
            "links": {
                "knowledge": (
                    None if not task else f"/api/knowledge/{task['knowledge_point_id']}"
                ),
                "review_center": "/api/reviews",
                "mistakes": f"/api/students/{sid}/mistakes",
            },
        }

    # ------------------------------------------------------------------
    # 知识点学习页
    # ------------------------------------------------------------------

    def knowledge(
        self,
        course_id: str,
        student_id: str,
        knowledge_point_id: str,
        *,
        lang: str = "zh",
        language: Optional[str] = None,
    ) -> dict[str, Any]:
        """当前 KnowledgePoint 的完整学习页投影（spec 66.4 / 66.5）。

        所有事实都来自既有数据：
        - KP 本体与两个 truth axis: ``KnowledgeService``；
        - Evidence -> Material -> Source: ``ExerciseWorkflow.evidence_trace``；
        - Grounded explanation: ``LearningViewService.grounded_explanation``；
        - 当前 StudentState / 前置 / 下一步: ``LearningService`` 与
          ``learning_path_view``。

        没有证据时**明确报缺**（``evidence_available: false`` +
        ``evidence unavailable``），绝不自行补充解释。
        """
        cid, sid = self._require(course_id, student_id)
        kp_id = _clean(knowledge_point_id)
        if not kp_id:
            raise InvalidInputError("knowledge_point_id must be non-empty")
        ctx = self._ws.context(cid)
        ctx.learning_service.get_student(sid)

        # 1) 知识点本体（不存在 -> NotFoundError，语义是 404 而不是空页）
        kp = self._ws.knowledge_point(cid, kp_id)

        # 2) 证据链（Evidence -> Material -> Source），无证据时形状完整但为空
        try:
            trace = self._ws.knowledge_evidence_trace(cid, kp_id)
        except NotFoundError:
            trace = {"evidence": [], "knowledge_point": {}, "course_id": cid}
        evidence = list(trace.get("evidence") or [])
        materials = sorted(
            {
                str((row.get("material") or {}).get("material_id"))
                for row in evidence
                if (row.get("material") or {}).get("material_id")
            }
        )

        # 3) Grounded explanation —— 请求语言缺省用课程语言，再回落到 es。
        req_lang = _clean(language) or self._course_language(cid) or "es"
        try:
            explanation = ctx.learning_view.grounded_explanation(kp_id, req_lang)
        except NotFoundError:
            explanation = None

        # 4) 学习路径（前置 / 当前 / 下一步）
        path_node = self._path_node(ctx, sid, kp_id)

        return {
            "course_id": cid,
            "student_id": sid,
            "lang": lang,
            "workflow_version": WORKFLOW_VERSION,
            "knowledge_point": {
                "knowledge_id": kp.get("knowledge_id"),
                "title": kp.get("title"),
                "content": kp.get("content"),
                "original_terms": list(kp.get("original_terms") or []),
                # 两个 truth axis 分别呈现，绝不合并（spec 原则 4）
                "validation_status": kp.get("validation_status"),
                "review_status": kp.get("review_status"),
                "needs_verification": kp.get("needs_verification"),
                "knowledge_score": kp.get("knowledge_score"),
                "importance": kp.get("importance"),
            },
            "source_language": self._source_language(kp, evidence),
            "evidence": self._evidence_rows(evidence),
            "materials": materials,
            "evidence_available": bool(evidence),
            "evidence_note": None if evidence else EVIDENCE_UNAVAILABLE,
            "grounded_explanation": explanation,
            "student_state": (
                path_node.get("state") if path_node else self._state_of(ctx, sid, kp_id)
            ),
            "student_activity": (
                path_node.get("activity") if path_node else None
            ),
            "prerequisites": self._prerequisites(path_node, ctx, kp_id),
            "unmet_prerequisites": list(
                (path_node or {}).get("unmet_prerequisite_ids") or []
            ),
            "next_event": (
                path_node.get("next_event")
                if path_node
                else ctx.learning_service.next_learning_event(
                    self._state_of(ctx, sid, kp_id)
                )
            ),
            "next_task": self._next_task_after(ctx, cid, sid, kp_id),
            "truth_flags": self._truth_flags(kp, evidence),
        }

    def open_knowledge(
        self,
        course_id: str,
        student_id: str,
        knowledge_point_id: str,
        *,
        lang: str = "zh",
        language: Optional[str] = None,
    ) -> dict[str, Any]:
        """打开知识页 = 读投影 + 记一个 ``viewed`` 事件。

        ``viewed`` 是 Task 30 定义的事件，且是 ``not_started -> exposed``
        的**唯一**合法事件。重复打开不会重复推进状态（领域层按事件序列
        归约，越序事件被记录但不改变状态），因此本方法是幂等的。
        """
        cid, sid = self._require(course_id, student_id)
        kp_id = _clean(knowledge_point_id)
        if not kp_id:
            raise InvalidInputError("knowledge_point_id must be non-empty")
        # 先校验知识点确实存在（避免为一个不存在的 KP 记录学习事件）
        self._ws.knowledge_point(cid, kp_id)
        self._ws.record_learning_event(cid, sid, kp_id, "viewed")
        return self.knowledge(
            cid, sid, kp_id, lang=lang, language=language
        )

    # ------------------------------------------------------------------
    # 练习与作答（纯委托）
    # ------------------------------------------------------------------

    def exercise_for_knowledge(
        self,
        course_id: str,
        student_id: str,
        knowledge_point_id: str,
        *,
        config: Any = None,
    ) -> dict[str, Any]:
        """为当前知识点拿到一道有证据依据的练习（幂等生成）。

        - 已存在的练习优先（确定性排序取第一个），**不重复生成**；
        - 没有才走 ``ExerciseWorkflow.generate``（内容寻址 -> 同一草稿
          永远得到同一个 ``exercise_id``）。
        """
        cid, sid = self._require(course_id, student_id)
        kp_id = _clean(knowledge_point_id)
        if not kp_id:
            raise InvalidInputError("knowledge_point_id must be non-empty")
        ctx = self._ws.context(cid)
        ctx.learning_service.get_student(sid)

        existing = self._exercises_for(ctx, kp_id)
        if existing:
            exercise = existing[0]
            return {
                "course_id": cid,
                "student_id": sid,
                "knowledge_point_id": kp_id,
                "generated": False,
                "reused": True,
                "exercise": exercise,
                "exercise_id": exercise.get("exercise_id"),
                "refusal": None,
                "grounding": self._ws.exercise_grounding(
                    cid, str(exercise.get("exercise_id"))
                ),
            }

        result = self._ws.generate_exercise(cid, kp_id, config=config)
        if not result.get("generated"):
            return {
                "course_id": cid,
                "student_id": sid,
                "knowledge_point_id": kp_id,
                "generated": False,
                "reused": False,
                "exercise": None,
                "exercise_id": None,
                "refusal": result.get("refusal"),
                "grounding": None,
            }
        exercise = result.get("exercise") or {}
        return {
            "course_id": cid,
            "student_id": sid,
            "knowledge_point_id": kp_id,
            "generated": True,
            "reused": not result.get("created", False),
            "exercise": exercise,
            "exercise_id": exercise.get("exercise_id"),
            "refusal": None,
            "grounding": result.get("grounding"),
            "banner": result.get("banner"),
        }

    def answer(
        self,
        course_id: str,
        student_id: str,
        exercise_id: str,
        submitted_value: str,
        sequence: int = 0,
    ) -> dict[str, Any]:
        """提交作答，返回评价 + 状态 + 下一任务（spec 66.7 / 66.8）。

        评价来自既有 ``LearningService.submit_answer``（ExactEvaluator），
        状态来自 Task 30，下一任务来自本层选择规则。三者语义分离：
        ``evaluation`` 是事实，``student_state`` 是状态，``next_task`` 是导航。
        """
        cid, sid = self._require(course_id, student_id)
        eid = _clean(exercise_id)
        if not eid:
            raise InvalidInputError("exercise_id must be non-empty")

        submitted = self._ws.exercise_submit(
            cid, sid, eid, submitted_value, sequence
        )
        ctx = self._ws.context(cid)
        kp_ids = sorted(
            {
                str(k)
                for k in (
                    self._ws.get_exercise(cid, eid).get("knowledge_point_ids") or []
                )
            }
        )

        states = [
            self._state_record(ctx, sid, kp_id) for kp_id in kp_ids
        ]
        candidates, excluded = self._candidates(ctx, cid, sid)
        return {
            "course_id": cid,
            "student_id": sid,
            "exercise_id": eid,
            "answer": submitted.get("answer"),
            "evaluation": submitted.get("evaluation"),
            "evaluation_status": (submitted.get("answer") or {}).get(
                "evaluation_status"
            ),
            "knowledge_point_ids": kp_ids,
            "student_state": states[0] if len(states) == 1 else states,
            "grounding": submitted.get("grounding"),
            "next_task": self._peek_next(candidates),
            "has_more": bool(candidates),
            "progress": self._progress(ctx, cid, sid, candidates, excluded),
        }

    # ------------------------------------------------------------------
    # 候选与排序（纯函数式，确定性）
    # ------------------------------------------------------------------

    def _candidates(
        self, ctx: Any, course_id: str, student_id: str
    ) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
        """按确定性规则给出**仍有工作可做**的候选学习任务 + 被排除的知识点。

        「还有工作可做」的判据是 **Task 30 状态机是否还有合法下一步**
        （``next_event is not None``）。一个已经到 ``reviewing`` 的知识点
        在状态机里没有出边 —— 它不是一个"待学习任务"，把它继续摆在
        "今天要学什么"里会让流程**永远结束不了**（这是本模块修复过的真实
        缺陷：10 个知识点全部推到 reviewing 之后 ``has_task`` 仍是 true）。

        这不是"掌握度判定"，而是**状态机自身的终态**：``reviewing`` 是
        Task 30 定义的终点，不是我们新加的结论（铁律 5 不受影响）。
        """
        rows = self._ws.knowledge_points(course_id)
        states = self._student_state_records(ctx, student_id)
        # 一次算好"已能推进的"集合 —— 不能在 KP 循环里反复重算 (O(n^2))。
        finished = self._completed(ctx, student_id)

        excluded: dict[str, list[str]] = {"rejected": [], "conflicted": []}
        scored: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        for kp in rows:
            kp_id = str(kp.get("knowledge_id"))
            validation = str(kp.get("validation_status") or "")
            review = str(kp.get("review_status") or "")
            # 两个 truth axis **分别**检查, 绝不合并 (铁律 4)。
            if validation == "rejected" or review in EXCLUDED_REVIEW_STATUSES:
                excluded["rejected"].append(kp_id)
                continue
            if validation in EXCLUDED_VALIDATION_STATUSES:
                excluded["conflicted"].append(kp_id)
                continue
            # ``conflict`` 是知识点 DTO 上"这条知识有未解决冲突"的既有字段
            # (见 ``KnowledgeService.get_knowledge_points`` 的 conflict 过滤)。
            if _truthy_conflict(kp.get("conflict")):
                excluded["conflicted"].append(kp_id)
                continue

            record = states.get(kp_id) or {}
            state = str(record.get("state") or "not_started")
            next_event = ctx.learning_service.next_learning_event(state)
            # 状态机已到终点 -> 不再是"待学习任务"（但也不是错误）。
            if next_event is None:
                continue

            prereqs = self._prerequisite_ids(ctx, kp_id)
            unmet = [p for p in prereqs if p not in finished]

            scored.append(
                (
                    (
                        0,
                        len(unmet),
                        self._state_rank(state),
                        self._session_number(ctx, course_id, kp_id),
                        kp_id,
                    ),
                    {
                        "course_id": course_id,
                        "student_id": student_id,
                        "knowledge_point_id": kp_id,
                        "title": kp.get("title"),
                        "validation_status": validation or None,
                        "review_status": kp.get("review_status"),
                        "needs_verification": kp.get("needs_verification"),
                        "state": state,
                        "next_event": next_event,
                        "prerequisite_ids": prereqs,
                        "unmet_prerequisite_ids": sorted(unmet),
                        "excluded_reason": None,
                        "basis": (
                            f"state={state} (Task 30); "
                            f"unmet_prerequisites={len(unmet)}; "
                            f"next_event={next_event}"
                        ),
                    },
                )
            )

        scored.sort(key=lambda pair: pair[0])
        for key in excluded:
            excluded[key] = sorted(excluded[key])
        return [row for _, row in scored], excluded

    def _completed(self, ctx: Any, student_id: str) -> set[str]:
        """"已经能推进"的知识点集合。

        判据是 **Task 30 的状态**，不是"答对过"。理由（spec 66.3.6）：
        一次正确作答不构成 mastery，一次错误作答也不构成 failure ——
        把作答次数当状态正是本项目反复禁止的那件事。
        """
        out: set[str] = set()
        for kp_id, record in self._student_state_records(ctx, student_id).items():
            state = str(record.get("state") or "not_started")
            if ctx.learning_service.next_learning_event(state) is None:
                # state == reviewing，状态机已到末端
                out.add(kp_id)
        return out

    def _progress(
        self,
        ctx: Any,
        course_id: str,
        student_id: str,
        candidates: Sequence[Mapping[str, Any]],
        excluded: Mapping[str, Sequence[str]],
    ) -> dict[str, Any]:
        """按**两个 truth axis 分开**统计进度（绝不合并）。

        ``by_state`` 覆盖课程里**全部**可学习知识点（含已到 ``reviewing``
        终点的那些）—— 否则"全部学完"会显示成"一个都没有"，把进度抹掉。
        ``orderable`` 才是"现在还能继续做的数量"。
        """
        states = self._student_state_records(ctx, student_id)
        by_state: dict[str, int] = {state: 0 for state in ORDERABLE_STATES}
        finished = 0
        for kp in self._ws.knowledge_points(course_id):
            kp_id = str(kp.get("knowledge_id"))
            validation = str(kp.get("validation_status") or "")
            review = str(kp.get("review_status") or "")
            if (
                validation == "rejected"
                or review in EXCLUDED_REVIEW_STATUSES
                or validation in EXCLUDED_VALIDATION_STATUSES
                or kp.get("has_conflict")
                or _truthy_conflict(kp.get("conflict"))
            ):
                continue
            state = str((states.get(kp_id) or {}).get("state") or "not_started")
            by_state[state] = by_state.get(state, 0) + 1
            if ctx.learning_service.next_learning_event(state) is None:
                finished += 1

        total = sum(by_state.values())
        return {
            "total": total
            + len(excluded.get("rejected") or [])
            + len(excluded.get("conflicted") or []),
            "orderable": len(candidates),
            "finished": finished,
            "by_state": by_state,
            "excluded": {
                "rejected": len(excluded.get("rejected") or []),
                "conflicted": len(excluded.get("conflicted") or []),
            },
        }

    def _peek_next(
        self, candidates: Sequence[Mapping[str, Any]]
    ) -> Optional[dict[str, Any]]:
        """候选里的第一个就是"下一个"（同一个排序规则，不另写一份）。"""
        return dict(candidates[0]) if candidates else None

    # ------------------------------------------------------------------
    # 内部：读既有投影
    # ------------------------------------------------------------------

    def _path_node(
        self, ctx: Any, student_id: str, kp_id: str
    ) -> Optional[dict[str, Any]]:
        """该知识点在学习路径里的节点（前置 / 状态 / 下一步）。"""
        try:
            path = ctx.learning_view.learning_path_view(student_id, kp_id)
        except (NotFoundError, InvalidInputError):
            return None
        except Exception:  # noqa: BLE001 - 单条路径失败不该让学习页崩
            return None
        nodes = list(path.get("nodes") or [])
        if not nodes:
            return None
        # 目标是路径的最后一个节点
        return nodes[-1] if nodes[-1].get("knowledge_point_id") == kp_id else nodes[0]

    def _next_task_after(
        self, ctx: Any, course_id: str, student_id: str, current_kp_id: str
    ) -> Optional[dict[str, Any]]:
        """做完当前知识点之后的下一任务（排除它自己）。"""
        candidates, _ = self._candidates(ctx, course_id, student_id)
        for row in candidates:
            if row["knowledge_point_id"] != current_kp_id:
                return dict(row)
        return None

    def _student_state_records(
        self, ctx: Any, student_id: str
    ) -> dict[str, dict[str, Any]]:
        state = ctx.learning_service.get_student_state(student_id)
        return {
            str(record["knowledge_point_id"]): dict(record)
            for record in (state.get("states") or [])
        }

    def _state_of(self, ctx: Any, student_id: str, kp_id: str) -> str:
        record = self._student_state_records(ctx, student_id).get(kp_id)
        if not record:
            return "not_started"
        return str(record.get("state") or "not_started")

    def _state_record(
        self, ctx: Any, student_id: str, kp_id: str
    ) -> dict[str, Any]:
        record = self._student_state_records(ctx, student_id).get(kp_id) or {}
        return {
            "knowledge_point_id": kp_id,
            "state": str(record.get("state") or "not_started"),
            "exposure_count": int(record.get("exposure_count") or 0),
            "practice_count": int(record.get("practice_count") or 0),
            "answer_count": int(record.get("answer_count") or 0),
            "correct_count": int(record.get("correct_count") or 0),
            "incorrect_count": int(record.get("incorrect_count") or 0),
            "next_event": ctx.learning_service.next_learning_event(
                str(record.get("state") or "not_started")
            ),
        }

    def _prerequisite_ids(self, ctx: Any, kp_id: str) -> list[str]:
        """直接前置（Task 26 关系层的入边），确定性排序。

        与 ``LearningViewService._prerequisite_ids`` 同源：只报告显式关系，
        不做传递闭包、不推断。
        """
        try:
            incoming = ctx.org_service.get_related_knowledge(kp_id, "incoming")
        except Exception:  # noqa: BLE001 - 关系层对未注册 KP 会抛错
            return []
        return sorted(
            {
                str(rel.source_knowledge_point_id)
                for rel in incoming
                if str(getattr(rel, "relation_type", "")).endswith("prerequisite_of")
                or getattr(rel, "relation_type", None) is not None
            }
        )

    def _exercises_for(self, ctx: Any, kp_id: str) -> list[dict[str, Any]]:
        rows = [
            exercise
            for exercise in ctx.learning_service.list_exercises()
            if kp_id in [str(k) for k in (exercise.get("knowledge_point_ids") or [])]
        ]
        rows.sort(key=lambda item: str(item.get("exercise_id")))
        return rows

    def _prerequisites(
        self, path_node: Optional[Mapping[str, Any]], ctx: Any, kp_id: str
    ) -> list[str]:
        if path_node and path_node.get("prerequisite_ids") is not None:
            return sorted({str(p) for p in (path_node.get("prerequisite_ids") or [])})
        return self._prerequisite_ids(ctx, kp_id)

    def _truth_flags(
        self, kp: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """两个 truth axis + 证据链完整性，全部显式呈现。"""
        validation = str(kp.get("validation_status") or "")
        return {
            "validation_status": kp.get("validation_status"),
            "review_status": kp.get("review_status"),
            "is_conflicted": validation == "conflicted",
            "is_unverified": validation in ("unverified", "pending", "")
            or bool(kp.get("needs_verification")),
            "has_evidence": bool(evidence),
            "evidence_count": len(evidence),
        }

    def _source_language(
        self, kp: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """来源语言 —— 只报**已知的**, 不知道就 null（绝不猜）。

        ``Language`` 枚举的值是显示值（``"Spanish"``），这里原样透传，
        由渲染层决定怎么显示。
        """
        declared = [
            str(row.get("language"))
            for row in evidence
            if row.get("language") and str(row.get("language")) != "Unknown"
        ]
        return {
            "declared": sorted(set(declared)) or None,
            "unknown": not declared,
            "terms": list(kp.get("original_terms") or []),
        }

    def _evidence_rows(
        self, evidence: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in sorted(evidence, key=lambda r: str(r.get("evidence_id") or "")):
            source = row.get("source") or {}
            material = row.get("material") or {}
            rows.append(
                {
                    "evidence_id": row.get("evidence_id"),
                    "content": row.get("content"),
                    "language": row.get("language"),
                    "confidence": row.get("confidence"),
                    "evidence_type": row.get("evidence_type"),
                    "source": source,
                    "material": material or None,
                    # 断链必须被报告，而不是被隐藏
                    "material_resolved": bool(material.get("material_id")),
                }
            )
        return rows

    def _session_number(self, ctx: Any, course_id: str, kp_id: str) -> int:
        """该知识点所属课堂的序号（没有则 −1，排最前）。

        ``ClassSession.session_number`` 是既有元数据；本层不推断课表。
        """
        best = -1
        try:
            sessions = self._ws.list_sessions(course_id)
        except Exception:  # noqa: BLE001
            return best
        for session in sessions:
            refs = [str(r) for r in (session.get("knowledge_point_refs") or [])]
            if kp_id not in refs:
                continue
            number = session.get("session_number")
            if isinstance(number, int) and number > best:
                best = number
        return best

    @staticmethod
    def _state_rank(state: str) -> int:
        try:
            return ORDERABLE_STATES.index(state)
        except ValueError:
            return len(ORDERABLE_STATES)

    def _course_language(self, course_id: str) -> Optional[str]:
        """课程语言的 ISO 码, 无法确定时 ``None``。

        ``Course.language`` 的域内值是 ``Language`` 枚举的**显示值**
        (``"Catalan"`` / ``"Spanish"``), 不是 ISO 码。直接把它当请求语言
        传给 ``grounded_explanation`` 会被 ``normalize_language`` 拒绝
        (``"Catalan"`` 不是合法语言标签) —— 这正是本项目反复出现的
        "显示值当码值用"缺陷。必须经 ``language_code()`` 归一:
        已知语言 -> ISO 码; 未知/无法识别 -> ``None`` (= 没有约束)。
        """
        from src.application.learning_view import language_code

        try:
            course = self._ws.get_course(course_id)
        except (NotFoundError, InvalidInputError):
            return None
        return language_code(course.get("language"))

    @staticmethod
    def _require(course_id: Any, student_id: Any) -> tuple[str, str]:
        cid = _clean(course_id)
        sid = _clean(student_id)
        if not cid:
            raise InvalidInputError("course_id is required")
        if not sid:
            raise InvalidInputError("student_id is required")
        return cid, sid
