"""Task 65: Mistake & Weak Knowledge Center（错题与薄弱知识点中心）。

这是一个**只读投影层**（read model）。它回答的问题是:

    我哪里需要重新学习？

设计约束（spec 65.5 / 65.6 / 65.7 / 65.10）:

1. **correctness 只读 Evaluation。** 本模块不重新判分、不重新比较
   答案。每一条"错题"都必须是某份既有 ``EvaluationResult`` 里
   ``status == INCORRECT`` 的那一条。项目里已经有两套评估器
   (Task 32 的 ``exact`` / 结构化)，再写第三套必然与它们漂移。

2. **薄弱 ≠ wrong_count > 0。** 除非 Task 30 的 ``StudentState``
   明确定义了该信号，否则不能把"错过一次"标成"薄弱"。本模块只透传
   ``NEEDS_REVIEW`` / ``NEEDS_PRACTICE`` 这两个既有信号，
   并把"错过一次"如实命名为 ``incorrect_attempts``（次数），
   而不是 ``weak``（判断）。

3. **"再练一次"复用既有练习。** 不新建、不重新生成（spec 65.10）。
   ``practice_targets`` 只从已有 exercise 里选，优先挑该学生
   **没做过** 的、其次挑答错的，且按 ``exercise_id`` 排序保证确定性。

4. **建议动作必须有真实依据。** ``suggested_actions`` 只在对应关系
   真的存在时才出现（有证据才给 "View Evidence"，
   有前置才给 "View Prerequisite"，有未做练习才给 "Practice Again"）。

5. **空状态是正常状态。** 没有错题时 ``has_mistakes == False`` +
   ``MISTAKES_EMPTY_NOTE``，不是 500，也不是空白页。

投影来源（全部既有）::

    Exercise          -> learning_service.list_exercises()
    Answer            -> learning_service.answer_log_for(sid)
    Evaluation        -> learning_service.get_evaluation(answer_id)
    Knowledge         -> knowledge_service.get_knowledge_points()
    Evidence          -> workspace.knowledge_evidence(cid, kp_id)
    Material          -> workspace.list_materials(cid)
    Topic             -> org_service.get_knowledge_point_topics(kp_id)
    StudentState      -> learning_service.get_student_state(sid)
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

from src.application.errors import InvalidInputError, NotFoundError

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

CENTER_VERSION = "mistake-center-v1"

SCHEMA_VERSION = 1

#: 没有错题时的固定文案（spec 65.11）。前端按 key 渲染，
#: 三语各自翻译，但英文原文是契约的一部分（测试锁它）。
MISTAKES_EMPTY_NOTE = "No mistakes yet."

#: Task 30 里**真正表示"需要再看一眼"**的两个状态。
#: 只有这两个会被投影成 attention；其余状态（含 not_started）不是薄弱信号。
#: 注意 ``reviewing`` 在 Task 30 里由 reviewed 事件产生，
#: ``practicing`` 由 practiced 事件产生 —— 它们都是**显式学习事件**的结果，
#: 不是"答错几次"推出来的。
WEAK_STATES = ("NEEDS_REVIEW", "NEEDS_PRACTICE")

#: StudentState -> 对外信号 的映射。与 student_today_view 保持一致。
_STATE_SIGNAL = {
    "reviewing": "NEEDS_REVIEW",
    "practicing": "NEEDS_PRACTICE",
}

#: 本模块的输出里**禁止出现**的措辞。UI 与测试都用它做守卫。
#:
#: 这些词的问题在于它们把"一次错答"升级成了对人的判断:
#:   - "you don't know this" / "weak"  -> 未经验证的推断
#:   - "mastered" / "proficiency"      -> 本项目从不计算掌握度
#:   - "will be on the exam"           -> 编造考试承诺
CENTER_FORBIDDEN_TERMS = (
    "mastered",
    "mastery",
    "proficiency",
    "proficient",
    "you are ready",
    "you don't know",
    "you do not know",
    "weak knowledge",
    "already learned",
    "this will be on the exam",
)

#: 建议动作的类型。UI 只渲染实际出现的那些。
SUGGESTED_ACTIONS = (
    "REVIEW_KNOWLEDGE",
    "VIEW_EVIDENCE",
    "PRACTICE_AGAIN",
    "VIEW_PREREQUISITE",
)

#: 单次查询的上限，避免一个学生几千条错题时把页面拖死。
DEFAULT_LIMIT = 200


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _text(value: Any) -> Optional[str]:
    """把知识点正文压成一行摘要（用于列表展示，不改写原文）。"""
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    return text


class MistakesView:
    """一个 workspace + 课程范围内的错题与薄弱知识点投影。

    构造后调用 :meth:`center` 得到完整视图。所有方法都是只读的 ——
    本类不持有任何可写句柄，也从不调用 ``create_*`` / ``submit_*``。
    """

    def __init__(
        self,
        workspace: Any,
        course_id: str,
        *,
        limit: int = DEFAULT_LIMIT,
    ) -> None:
        cid = _clean(course_id)
        if not cid:
            raise InvalidInputError("course_id must be non-empty")
        if int(limit) <= 0:
            raise InvalidInputError("limit must be positive")
        self._ws = workspace
        self._course_id = cid
        self._limit = int(limit)

    # ------------------------------------------------------------------
    # 既有服务（全部从 CourseContext 取，不新建任何容器）
    # ------------------------------------------------------------------

    def _ctx(self) -> Any:
        return self._ws.context(self._course_id)

    def _learning(self) -> Any:
        """Task 30/31/32/33 的学习服务（学生 / 练习 / 作答 / 评估）。"""
        return self._ctx().learning_service

    def _knowledge(self) -> Any:
        """Task 26 的知识服务（知识点 DTO）。"""
        return self._ctx().knowledge_service

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    def center(
        self,
        student_id: str,
        *,
        group_by: str = "knowledge",
        lang: str = "zh",
    ) -> dict[str, Any]:
        """错题中心主视图。

        ``group_by`` 只影响 ``groups`` 的键（``knowledge`` / ``topic``）,
        ``mistakes`` 与 ``weak_knowledge`` 始终是同一份事实的两种切法。
        """
        sid = _clean(student_id)
        if not sid:
            raise InvalidInputError("student_id must be non-empty")
        mode = _clean(group_by).lower() or "knowledge"
        if mode not in ("knowledge", "topic"):
            raise InvalidInputError(
                f"group_by must be 'knowledge' or 'topic', got {group_by!r}"
            )

        # --- 1) 读既有事实 -------------------------------------------------
        try:
            self._learning().get_student(sid)
        except NotFoundError:
            raise NotFoundError(
                f"student {sid!r} not found in course {self._course_id!r}"
            )

        kp_index = self._knowledge_index()
        exercise_index = self._exercise_index()
        mistakes = self._mistakes(sid, exercise_index, kp_index)
        states = self._state_index(sid)

        knowledge_rows = self._by_knowledge(mistakes, kp_index, states)
        groups = (
            self._group_by_knowledge(knowledge_rows)
            if mode == "knowledge"
            else self._group_by_topic(knowledge_rows, kp_index)
        )

        weak = self._weak_knowledge(knowledge_rows, states)

        return {
            "schema_version": SCHEMA_VERSION,
            "center_version": CENTER_VERSION,
            "course_id": self._course_id,
            "student_id": sid,
            "lang": _clean(lang) or "zh",
            "group_by": mode,
            "has_mistakes": bool(mistakes),
            "note": None if mistakes else MISTAKES_EMPTY_NOTE,
            "mistakes": mistakes,
            "knowledge": knowledge_rows,
            "groups": groups,
            "weak_knowledge": weak,
            "counts": {
                "mistakes": len(mistakes),
                "knowledge_with_mistakes": len(knowledge_rows),
                "groups": len(groups),
                "weak_knowledge": len(weak),
                "incorrect_attempts": sum(
                    int(row.get("incorrect_attempts") or 0) for row in knowledge_rows
                ),
            },
            "links": {
                "review_center": f"/api/courses/{self._course_id}/review",
                "exercises": f"/api/students/{sid}/exercises",
                "exercise_workflow": "/api/exercise-generation",
            },
            "limits": {"mistakes": self._limit},
        }

    # ------------------------------------------------------------------
    # 事实读取（全部走既有层）
    # ------------------------------------------------------------------

    def _knowledge_index(self) -> dict[str, dict[str, Any]]:
        """kp_id -> 知识点 DTO（含 topic 归属与依据链所需的 href）。"""
        rows = self._knowledge().get_knowledge_points(course_id=self._course_id) or []
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            kp_id = _clean(row.get("knowledge_id"))
            if not kp_id:
                continue
            out[kp_id] = {
                "knowledge_id": kp_id,
                "title": row.get("title"),
                "content": _text(row.get("content")),
                "validation_status": row.get("validation_status"),
                "review_status": row.get("review_status"),
                "language": row.get("language"),
            }
        return out

    def _exercise_index(self) -> dict[str, dict[str, Any]]:
        rows = self._learning().list_exercises() or []
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            eid = _clean(row.get("exercise_id"))
            if not eid:
                continue
            out[eid] = {
                "exercise_id": eid,
                "exercise_type": row.get("exercise_type"),
                "prompt": row.get("prompt"),
                "difficulty": row.get("difficulty"),
                "knowledge_point_ids": sorted(
                    _clean(k) for k in _as_list(row.get("knowledge_point_ids")) if _clean(k)
                ),
                "prerequisites": sorted(
                    _clean(k) for k in _as_list(row.get("prerequisites")) if _clean(k)
                ),
            }
        return out

    def _state_index(self, student_id: str) -> dict[str, dict[str, Any]]:
        """kp_id -> StudentState 记录。

        ``states`` 是 **list of dicts**（不是以 kp_id 为键的字典）——
        这是 Task 30 的既有形状，这里只是转成索引。
        """
        try:
            payload = self._learning().get_student_state(student_id)
        except (NotFoundError, InvalidInputError):
            return {}
        records = payload.get("states") if isinstance(payload, Mapping) else None
        out: dict[str, dict[str, Any]] = {}
        for record in _as_list(records):
            if not isinstance(record, Mapping):
                continue
            kp_id = _clean(record.get("knowledge_point_id"))
            if kp_id:
                out[kp_id] = dict(record)
        return out

    def _evaluation_index(
        self, answers: Sequence[Any]
    ) -> dict[str, dict[str, Any]]:
        """一次性建 ``answer_id -> evaluation`` 索引。

        显式存在的理由: 逐条 ``get_evaluation`` 是 N+1 形状。这里把
        取数收敛成一个线性过程, 并把"缺评估"表达成"索引里没有这一项",
        而不是靠异常控制每条答案的流程。索引缺失 = 没有对错概念, 跳过。
        """
        index: dict[str, dict[str, Any]] = {}
        for answer in answers:
            if not isinstance(answer, Mapping):
                continue
            answer_id = _clean(answer.get("answer_id"))
            if not answer_id or answer_id in index:
                continue
            try:
                index[answer_id] = self._learning().get_evaluation(answer_id)
            except NotFoundError:
                continue
        return index

    def _mistakes(
        self,
        student_id: str,
        exercise_index: Mapping[str, Mapping[str, Any]],
        kp_index: Mapping[str, Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """错题列表 —— 每一条都来自一份既有 Evaluation。

        判定只有一个来源: ``evaluation["status"] == "incorrect"``。
        这里**不比较** ``submitted_value`` 与任何参考答案。

        查询模式: ``answer_log_for`` 一次取回该学生的**全部**答案,
        再一次性建 ``answer_id -> evaluation`` 索引。全程没有逐条
        往返数据库的循环 (spec: 禁止 N+1 / N×M)。
        """
        log = self._learning().answer_log_for(student_id) or []
        index = self._evaluation_index(log)
        rows: list[dict[str, Any]] = []
        for answer in log:
            if not isinstance(answer, Mapping):
                continue
            answer_id = _clean(answer.get("answer_id"))
            exercise_id = _clean(answer.get("exercise_id"))
            if not answer_id or not exercise_id:
                continue
            evaluation = index.get(answer_id)
            if evaluation is None:
                # 没有评估就没有"对错"这个概念 —— 跳过，不猜。
                continue
            if _clean(evaluation.get("status")).lower() != "incorrect":
                continue
            exercise = exercise_index.get(exercise_id) or {}
            kp_ids = list(exercise.get("knowledge_point_ids") or [])
            rows.append(
                {
                    "answer_id": answer_id,
                    "evaluation_id": evaluation.get("evaluation_id"),
                    "exercise_id": exercise_id,
                    "exercise_type": exercise.get("exercise_type"),
                    "prompt": exercise.get("prompt"),
                    "submitted_value": answer.get("submitted_value"),
                    "sequence": answer.get("sequence"),
                    "submitted_at": answer.get("submitted_at"),
                    "status": evaluation.get("status"),
                    "score": evaluation.get("score"),
                    "feedback": evaluation.get("feedback"),
                    "knowledge_point_ids": kp_ids,
                    "knowledge": [
                        {
                            "knowledge_id": kp_id,
                            "title": (kp_index.get(kp_id) or {}).get("title"),
                        }
                        for kp_id in kp_ids
                    ],
                    "evaluator_version": evaluation.get("evaluator_version"),
                }
            )
        # 确定性排序: 提交顺序（answer_log 本身是追加序）的逆序太脆，
        # 直接按 answer_id 排序 —— 同一份日志永远给出同一个顺序。
        rows.sort(key=lambda item: (_clean(item["exercise_id"]), _clean(item["answer_id"])))
        return rows[: self._limit]

    # ------------------------------------------------------------------
    # 按知识点聚合（spec 65.3）
    # ------------------------------------------------------------------

    def _by_knowledge(
        self,
        mistakes: Sequence[Mapping[str, Any]],
        kp_index: Mapping[str, Mapping[str, Any]],
        states: Mapping[str, Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """知识点 -> 错答次数 + 依据 + 建议动作。

        ``incorrect_attempts`` 是**次数**（事实），
        ``attention`` 是 StudentState 的**信号**（既有语义）。
        两者都给出，但绝不合并成一个 "weak" 布尔。
        """
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for row in mistakes:
            for kp_id in row.get("knowledge_point_ids") or []:
                grouped.setdefault(_clean(kp_id), []).append(row)

        # 课程知识点里没有错、但有 StudentState 信号的也要出现 ——
        # 否则"需要复习"的知识点会因为"还没错过"而被漏掉。
        for kp_id, record in states.items():
            signal = _STATE_SIGNAL.get(_clean(record.get("state")))
            if signal and kp_id not in grouped:
                grouped.setdefault(kp_id, [])

        out: list[dict[str, Any]] = []
        for kp_id in sorted(grouped):
            attempts = grouped[kp_id]
            meta = kp_index.get(kp_id) or {}
            record = states.get(kp_id) or {}
            state = _clean(record.get("state")) or None
            signal = _STATE_SIGNAL.get(state)
            wrong = len(attempts)

            out.append(
                {
                    "knowledge_id": kp_id,
                    "title": meta.get("title") or kp_id,
                    "excerpt": meta.get("content"),
                    "validation_status": meta.get("validation_status"),
                    "review_status": meta.get("review_status"),
                    "language": meta.get("language"),
                    # --- 事实 ---
                    "incorrect_attempts": wrong,
                    "exercise_ids": sorted(
                        {_clean(a["exercise_id"]) for a in attempts if a.get("exercise_id")}
                    ),
                    "answer_ids": sorted(
                        {_clean(a["answer_id"]) for a in attempts if a.get("answer_id")}
                    ),
                    # --- 既有语义（Task 30）---
                    "student_state": state,
                    "attention": signal,
                    "attention_basis": (
                        f"StudentState state={state} (Task 30)" if signal else None
                    ),
                    # --- 明细 ----
                    "mistakes": [dict(a) for a in attempts],
                    "suggested_actions": [],
                    "evidence_count": None,
                    "practice_targets": [],
                }
            )
        return out

    # ------------------------------------------------------------------
    # 分组视图
    # ------------------------------------------------------------------

    def _group_by_knowledge(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """spec 65.3 的两级视图: 知识点 -> 错答次数。

        每个知识点是它自己的组，因为行本身就是知识点粒度。
        真正让这个视图有用的是 ``children`` 里的练习明细。
        """
        groups: list[dict[str, Any]] = []
        for row in rows:
            groups.append(
                {
                    "group_kind": "knowledge",
                    "group_id": row["knowledge_id"],
                    "title": row.get("title") or row["knowledge_id"],
                    "incorrect_attempts": row.get("incorrect_attempts", 0),
                    "attention": row.get("attention"),
                    "attention_basis": row.get("attention_basis"),
                    "children_kind": "exercise",
                    "children": [
                        {
                            "exercise_id": m.get("exercise_id"),
                            "prompt": m.get("prompt"),
                            "submitted_value": m.get("submitted_value"),
                            "status": m.get("status"),
                            "answer_id": m.get("answer_id"),
                        }
                        for m in row.get("mistakes") or []
                    ],
                }
            )
        return groups

    def _group_by_topic(
        self,
        rows: Sequence[Mapping[str, Any]],
        kp_index: Mapping[str, Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """spec 65.4 的三级视图: Topic -> Knowledge -> Exercises。

        topic 归属来自知识组织层的 membership（与 Task 62 的
        ``CourseReviewView._topics`` 同一个来源）。知识点若没有 topic，
        会被放进一个明确的 ``__unassigned__`` 组，而不是被静默丢掉。
        """
        org = self._org()
        topic_rows: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            kp_id = _clean(row.get("knowledge_id"))
            topic_ids: list[str] = []
            if org is not None and kp_id:
                try:
                    topic_ids = [
                        _clean(t)
                        for t in _as_list(org.get_knowledge_point_topics(kp_id))
                        if _clean(t)
                    ]
                except Exception:  # noqa: BLE001 - 未注册 KP 不该让整页失败
                    topic_ids = []
            if not topic_ids:
                topic_ids = ["__unassigned__"]
            for topic_id in topic_ids:
                topic_rows.setdefault(topic_id, []).append(row)

        meta: dict[str, Mapping[str, Any]] = {}
        if org is not None:
            try:
                for topic in org.list_topics(course_id=self._course_id):
                    try:
                        dto = topic.to_dict()
                    except Exception:  # noqa: BLE001
                        dto = {}
                    meta[_clean(dto.get("topic_id"))] = dto
            except Exception:  # noqa: BLE001
                meta = {}

        groups: list[dict[str, Any]] = []
        for topic_id in sorted(topic_rows):
            members = topic_rows[topic_id]
            info = meta.get(topic_id) or {}
            groups.append(
                {
                    "group_kind": "topic",
                    "group_id": topic_id,
                    "title": info.get("name") or (
                        None if topic_id == "__unassigned__" else topic_id
                    ),
                    "unassigned": topic_id == "__unassigned__",
                    "incorrect_attempts": sum(
                        int(r.get("incorrect_attempts") or 0) for r in members
                    ),
                    "children_kind": "knowledge",
                    "children": [
                        {
                            "group_kind": "knowledge",
                            "group_id": r.get("knowledge_id"),
                            "title": r.get("title"),
                            "incorrect_attempts": r.get("incorrect_attempts", 0),
                            "attention": r.get("attention"),
                            "children_kind": "exercise",
                            "children": [
                                {
                                    "exercise_id": m.get("exercise_id"),
                                    "prompt": m.get("prompt"),
                                    "status": m.get("status"),
                                    "answer_id": m.get("answer_id"),
                                }
                                for m in (r.get("mistakes") or [])
                            ],
                        }
                        for r in members
                    ],
                }
            )
        return groups

    # ------------------------------------------------------------------
    # 薄弱知识点（spec 65.6 / 65.7）
    # ------------------------------------------------------------------

    def _weak_knowledge(
        self,
        rows: Sequence[Mapping[str, Any]],
        states: Mapping[str, Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """薄弱知识点。

        **只有 StudentState 明确给出 NEEDS_REVIEW / NEEDS_PRACTICE 的
        才算"需要关注"。** 其余只有"错答次数"，如实标成
        ``attention: None`` + ``incorrect_attempts``。

        这不是保守，而是 spec 65.6 的硬要求：``wrong_count > 0`` 不是
        ``weak`` 的定义。答错一次可能是笔误；把它写成"薄弱"就是把
        一次事件升级成对人的判断，而且项目里根本没有这个模型。
        """
        out: list[dict[str, Any]] = []
        for row in rows:
            signal = row.get("attention")
            if signal in WEAK_STATES:
                out.append(
                    {
                        "knowledge_id": row["knowledge_id"],
                        "title": row.get("title"),
                        "signal": signal,
                        "basis": row.get("attention_basis"),
                        "student_state": row.get("student_state"),
                        "incorrect_attempts": row.get("incorrect_attempts", 0),
                        "definition": "StudentState (Task 30)",
                    }
                )
        out.sort(key=lambda r: _clean(r["knowledge_id"]))
        return out

    # ------------------------------------------------------------------
    # 建议动作与依据（spec 65.8 / 65.9 / 65.10）
    # ------------------------------------------------------------------

    def enrich(
        self,
        student_id: str,
        kp_id: str,
    ) -> dict[str, Any]:
        """单个知识点的建议动作 + 依据链 + 可复用的练习。

        UI 在用户展开一条错题时调用它 —— 这样列表页不必为每个知识点
        都去扫证据（避免 N×M）。这**不是**写操作。
        """
        cid = self._course_id
        sid = _clean(student_id)
        kp = _clean(kp_id)
        if not sid:
            raise InvalidInputError("student_id must be non-empty")
        if not kp:
            raise InvalidInputError("knowledge_point_id must be non-empty")

        kp_index = self._knowledge_index()
        if kp not in kp_index:
            raise NotFoundError(
                f"knowledge point {kp!r} not found in course {cid!r}"
            )
        exercise_index = self._exercise_index()
        states = self._state_index(sid)
        mistakes = [
            row
            for row in self._mistakes(sid, exercise_index, kp_index)
            if kp in (row.get("knowledge_point_ids") or [])
        ]
        rows = self._by_knowledge(mistakes, kp_index, states)
        row = next((r for r in rows if r["knowledge_id"] == kp), None)
        if row is None:
            # 没错过、也没有状态信号 —— 仍然给出依据，只是没有建议。
            meta = kp_index[kp]
            row = {
                "knowledge_id": kp,
                "title": meta.get("title") or kp,
                "excerpt": meta.get("content"),
                "validation_status": meta.get("validation_status"),
                "review_status": meta.get("review_status"),
                "language": meta.get("language"),
                "incorrect_attempts": 0,
                "exercise_ids": [],
                "answer_ids": [],
                "student_state": _clean((states.get(kp) or {}).get("state")) or None,
                "attention": _STATE_SIGNAL.get(
                    _clean((states.get(kp) or {}).get("state"))
                ),
                "attention_basis": None,
                "mistakes": [],
            }

        evidence = self._evidence_chain(kp)
        prerequisites = self._prerequisites(kp, exercise_index)
        practice = self._practice_targets(kp, sid, exercise_index, mistakes)

        return {
            "schema_version": SCHEMA_VERSION,
            "center_version": CENTER_VERSION,
            "course_id": cid,
            "student_id": sid,
            "knowledge": {
                "knowledge_id": kp,
                "title": row.get("title"),
                "excerpt": row.get("excerpt"),
                "validation_status": row.get("validation_status"),
                "review_status": row.get("review_status"),
                "language": row.get("language"),
            },
            "why_incorrect": [
                {
                    "answer_id": m.get("answer_id"),
                    "exercise_id": m.get("exercise_id"),
                    "prompt": m.get("prompt"),
                    "submitted_value": m.get("submitted_value"),
                    "status": m.get("status"),
                    "score": m.get("score"),
                    "feedback": m.get("feedback"),
                    "evaluator_version": m.get("evaluator_version"),
                }
                for m in row.get("mistakes") or []
            ],
            "incorrect_attempts": row.get("incorrect_attempts", 0),
            "student_state": row.get("student_state"),
            "attention": row.get("attention"),
            "attention_basis": row.get("attention_basis"),
            "evidence": evidence,
            "prerequisites": prerequisites,
            "practice_targets": practice,
            "suggested_actions": self._suggested_actions(
                row, evidence, prerequisites, practice
            ),
        }

    def _suggested_actions(
        self,
        row: Mapping[str, Any],
        evidence: Sequence[Mapping[str, Any]],
        prerequisites: Sequence[str],
        practice: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """只给出**依据成立**的动作（spec 65.8）。

        每个动作都带 ``basis`` —— 说明"为什么这个动作现在可用"。
        没有依据就不出现，而不是给一个点了没反应的按钮。
        """
        actions: list[dict[str, Any]] = []
        kp_id = _clean(row.get("knowledge_id"))

        # 1) 回去看知识点本身 —— 永远可用（它就是这一行）。
        actions.append(
            {
                "action": "REVIEW_KNOWLEDGE",
                "target": kp_id,
                "href": f"/api/knowledge/{kp_id}",
                "basis": "the knowledge point this mistake is attached to",
            }
        )

        # 2) 看证据 —— 只有真的链到证据才给。
        if evidence:
            actions.append(
                {
                    "action": "VIEW_EVIDENCE",
                    "target": kp_id,
                    "href": f"/api/knowledge/{kp_id}/evidence-trace",
                    "basis": f"{len(evidence)} evidence row(s) resolved",
                }
            )

        # 3) 看前置 —— 只有真的声明了前置才给。
        if prerequisites:
            actions.append(
                {
                    "action": "VIEW_PREREQUISITE",
                    "target": prerequisites[0],
                    "targets": list(prerequisites),
                    "href": f"/api/knowledge/{prerequisites[0]}",
                    "basis": "declared prerequisite relation",
                }
            )

        # 4) 再练一次 —— 只有存在可复用的既有练习才给（**不新建**）。
        if practice:
            actions.append(
                {
                    "action": "PRACTICE_AGAIN",
                    "target": _clean(practice[0].get("exercise_id")),
                    "targets": [_clean(p.get("exercise_id")) for p in practice],
                    "href": f"/api/exercises/{_clean(practice[0].get('exercise_id'))}",
                    "basis": f"{len(practice)} existing exercise(s) reused (no new generation)",
                }
            )
        return actions

    def _practice_targets(
        self,
        kp_id: str,
        student_id: str,
        exercise_index: Mapping[str, Mapping[str, Any]],
        mistakes: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """可复用的既有练习（spec 65.10）。

        排序规则（确定性，可复现）:
          1. 该学生**从未提交过**的练习优先（还有新东西可做）
          2. 其次是他答错过的练习（重做）
          3. 同级按 ``exercise_id`` 排序

        **绝不重新生成题目。** 这里只读 ``list_exercises``。
        """
        log = self._learning().answer_log_for(student_id) or []
        attempted = {
            _clean(a.get("exercise_id")) for a in log if isinstance(a, Mapping)
        }
        wrong = {
            _clean(m.get("exercise_id")) for m in mistakes if m.get("exercise_id")
        }
        candidates = [
            ex
            for ex in exercise_index.values()
            if kp_id in (ex.get("knowledge_point_ids") or [])
        ]

        def rank(ex: Mapping[str, Any]) -> tuple[int, str]:
            eid = _clean(ex.get("exercise_id"))
            if eid not in attempted:
                return (0, eid)
            if eid in wrong:
                return (1, eid)
            return (2, eid)

        candidates.sort(key=rank)
        return [
            {
                "exercise_id": _clean(ex.get("exercise_id")),
                "exercise_type": ex.get("exercise_type"),
                "prompt": ex.get("prompt"),
                "difficulty": ex.get("difficulty"),
                "reused": True,
                "previously_attempted": _clean(ex.get("exercise_id")) in attempted,
                "previously_incorrect": _clean(ex.get("exercise_id")) in wrong,
            }
            for ex in candidates
        ]

    def _prerequisites(
        self, kp_id: str, exercise_index: Mapping[str, Mapping[str, Any]]
    ) -> list[str]:
        """该知识点的前置（来自练习上声明的 relation，以及知识图谱）。"""
        found: set[str] = set()
        for ex in exercise_index.values():
            if kp_id in (ex.get("knowledge_point_ids") or []):
                found.update(_clean(p) for p in ex.get("prerequisites") or () if _clean(p))
        # 知识组织层的 relation（若可用）
        org = self._org()
        if org is not None:
            for getter in ("get_knowledge_point_prerequisites",):
                fn = getattr(org, getter, None)
                if fn is None:
                    continue
                try:
                    found.update(_clean(p) for p in _as_list(fn(kp_id)) if _clean(p))
                except Exception:  # noqa: BLE001
                    continue
        found.discard(kp_id)
        return sorted(found)

    def _evidence_chain(self, kp_id: str) -> list[dict[str, Any]]:
        """知识点 -> 证据 -> 材料（spec 65.9 的"重新学习依据"）。

        复用 ``workspace.knowledge_evidence`` 与 ``get_material`` —— 与
        Task 64 出题依据链读的是同一份事实，只是入口不同。
        """
        try:
            rows = self._knowledge().get_evidence_for_knowledge_point(kp_id) or []
        except (NotFoundError, InvalidInputError):
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            source = row.get("source") or {}
            material_id = _clean(source.get("material_id"))
            material = None
            if material_id:
                try:
                    raw = self._ws.get_material(self._course_id, material_id)
                except (NotFoundError, InvalidInputError):
                    raw = None
                if isinstance(raw, Mapping):
                    material = {
                        "material_id": raw.get("material_id"),
                        "filename": raw.get("filename"),
                        "material_type": raw.get("material_type"),
                        "language": raw.get("language"),
                    }
            out.append(
                {
                    "evidence_id": row.get("evidence_id"),
                    "evidence_type": row.get("evidence_type"),
                    "language": row.get("language"),
                    "confidence": row.get("confidence"),
                    "content": row.get("content"),
                    "source": source,
                    "material": material,
                }
            )
        out.sort(key=lambda r: _clean(r.get("evidence_id")))
        return out

    # ------------------------------------------------------------------

    def _org(self) -> Any:
        """知识组织服务（拿不到就返回 None —— 分组会退化为单组）。"""
        try:
            return self._ws.context(self._course_id).org_service
        except Exception:  # noqa: BLE001
            return None
