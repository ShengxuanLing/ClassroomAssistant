# -*- coding: utf-8 -*-
"""Task 67 — Exam Review Mode（考前复习模式, 只读投影层）。

本模块回答的问题是:

    考试快到了, 我该按什么顺序复习, 哪些地方必须先解决?

它**不回答**:

    哪些内容最可能考? / 这道题会不会出现? / 我能拿多少分?

最后这一类问题在本模块里**在结构上无法被回答** —— 因为 `ReviewSet`
压根没有概率、置信度、预测分值之类的字段。这不是"我们不去算", 而是
"没有地方放这个数"。见下方 ``NO_PREDICTION_FIELDS``。

四条硬约束
------------

1. **不是考试预测器 (spec 67)。** 排序依据只能是**既有的客观数据**:
   知识点在课程里的结构位置、覆盖状态、证据状态、冲突状态、审核状态,
   以及学生自己在 Task 30 里已有的学习状态。没有"考试概率"这种输入,
   因此也不可能有这种输出。

2. **冲突绝不被自动解决。** 一个知识点只要还有**未解决**的冲突, 它在
   ReviewSet 里就是 ``BLOCKED`` —— 两侧证据并列展示, 由人决定。
   本模块**调用不了**任何 resolve 操作 (它只有读方法), 这是结构性保证,
   不是纪律要求。

3. **两条真相轴分开呈现。** ``validation_status`` (证据说了什么) 与
   ``review_status`` (人说了什么) 各自独立成字段, 并各自单独计数。
   合并成"综合可信度"会让"证据冲突但人已确认"这类事实消失。

4. **确定性。** 所有列表有显式排序键; 同一份数据在任何时刻、
   任何进程里产出的 ReviewSet 完全一致 (逐字节)。

投影来源 (全部既有)
----------------------

    Knowledge      -> knowledge_service.get_knowledge_points()
    Coverage/Gaps  -> workspace.coverage() / workspace.gaps()
    Conflicts      -> workspace.conflicts()
    Review queue   -> workspace.review_candidates()
    Evidence       -> workspace.knowledge_evidence(cid, kp_id)
    StudentState   -> learning_service.get_student_state(sid)
    Evaluations    -> learning_service.answer_log_for(sid) + get_evaluation()
    Exercises      -> learning_service.list_exercises(cid)
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from src.application.errors import InvalidInputError, NotFoundError

__all__ = [
    "REVIEW_MODE_VERSION",
    "REVIEW_SET_EMPTY_NOTE",
    "NO_PREDICTION_FIELDS",
    "REVIEW_FORBIDDEN_TERMS",
    "BLOCK_REASONS",
    "BUCKETS",
    "ReviewSet",
]

#: 投影契约版本。客户端可据此判断字段语义是否变化。
REVIEW_MODE_VERSION = "exam-review-mode-v1"

#: 没有任何可复习内容时的固定文案 (英文原文是契约的一部分)。
REVIEW_SET_EMPTY_NOTE = "No review set available yet."

#: spec 67 的 "No Prediction Contract"：**这些字段名不得出现在本模块的
#: 任何输出里**。它们不是"我们注意不去填", 而是这个投影里根本没有
#: 对应的数据结构 —— 所以测试可以逐字段扫描响应体来证明。
NO_PREDICTION_FIELDS: tuple[str, ...] = (
    "probability",
    "likelihood",
    "predicted",
    "predicted_score",
    "score_prediction",
    "exam_chance",
    "exam_probability",
    "most_likely_exam",
    "expected_score",
    "confidence_score",
)

#: 禁止出现在本投影响应里的**自然语言**词 (中英西加四语)。
#: 中文那几条是 spec 67 点名的: 最可能考 / 考试概率 / 预测题 / 押题 /
#: 考点概率 / 预测分数。
REVIEW_FORBIDDEN_TERMS: tuple[str, ...] = (
    # 英文
    "most likely exam",
    "exam probability",
    "predicted question",
    "exam forecast",
    "will be on the exam",
    "guaranteed",
    "pass probability",
    # 中文 (spec 点名)
    "最可能考",
    "考试概率",
    "预测题",
    "押题",
    "考点概率",
    "预测分数",
    # 西语
    "probabilidad de examen",
    "pregunta probable",
    "predicción de nota",
    # 加泰语
    "probabilitat d'examen",
    "pregunta probable",
    "predicció de nota",
)

#: 一个知识点为什么不能直接进入"优先复习"的位置。
#: 每一种都是**已存在的数据状态**, 不是推测。
BLOCK_REASONS: tuple[str, ...] = (
    "unresolved_conflict",
    "rejected_by_review",
)

#: ReviewSet 的三个桶。桶的归属规则见 ``ReviewSet._bucket_for``。
#: - ``blocked``   : 有未解决冲突 / 被人工否决 —— 先解决, 不能当复习材料
#: - ``attention`` : 需要先处理再看 —— StudentState 说要看 (NEEDS_REVIEW /
#:                   NEEDS_PRACTICE) / 证据不到 supported / 人工尚未确认
#: - ``ready``     : 证据支持**且**人工已确认**且**无需重看 —— 可以正常复习
BUCKETS: tuple[str, ...] = ("blocked", "attention", "ready")

#: ``attention`` 桶的成因。逐项显式给出, 让 UI 能说清"为什么要先看这个",
#: 而不是笼统地标一个"低优先级"。
ATTENTION_REASONS: tuple[str, ...] = (
    "student_state",
    "evidence_not_supported",
    "review_pending",
)

#: 复用 Task 65 的口径: 只有这两个 StudentState 才是"需要再看一眼"。
_WEAK_STATES = ("NEEDS_REVIEW", "NEEDS_PRACTICE")

_STATE_SIGNAL = {
    "reviewing": "NEEDS_REVIEW",
    "practicing": "NEEDS_PRACTICE",
    "exposed": "EXPOSED",
    "not_started": "NOT_STARTED",
}

#: 桶的排序秩 —— 越小越靠前。blocked 必须先被看到。
_BUCKET_RANK = {"blocked": 0, "attention": 1, "ready": 2}

#: 证据状态秩: 证据越弱越靠前 (因为更需要先处理)。
_VALIDATION_RANK = {"conflicted": 0, "unverified": 1, "supported": 2}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _require_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(f"{field} must be a non-empty string")
    return value.strip()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


class ReviewSet:
    """考前复习集合的只读投影。

    ``workspace`` 是 :class:`src.application.workspace.Workspace`; 用
    ``Any`` 标注以避免与编排器形成导入环 (与其它 view 模块一致)。
    """

    def __init__(self, workspace: Any) -> None:
        self._ws = workspace

    # ------------------------------------------------------------------
    # 内部索引 (一次取全量, 内存里分组 —— 不做 per-knowledge 查询)
    # ------------------------------------------------------------------

    def _knowledge(self, course_id: str) -> list[dict[str, Any]]:
        rows = self._ws.knowledge_points(course_id) or []
        return [dict(row) for row in rows]

    def _conflict_ids_by_kp(
        self, course_id: str, points: Sequence[Mapping[str, Any]]
    ) -> dict[str, list[str]]:
        """未解决冲突所牵涉的知识点 (kp_id -> [conflict_id, ...])。

        判定口径与 ``KnowledgeService._conflict_ids_by_kp`` 一致: 冲突只要
        与某个知识点的证据集有交集, 就算在这个知识点名下; 已 RESOLVED 的
        冲突不再计入。这里**不自己做新的判定**, 只是把 workspace 已有的
        ``conflicts()`` 结果索引一遍。

        ``points`` 由调用方传入而不是自己再取一遍 —— 否则在冲突数 × 知识点数
        上会退化成二次取数 (N+1 的变体)。
        """
        # 先把每个知识点的证据集算好 (线性一次), 然后逐个冲突求交集。
        kp_refs: dict[str, set[str]] = {}
        for kp in points:
            kp_id = _text(kp.get("knowledge_id"))
            if not kp_id:
                continue
            refs = {_text(r) for r in _as_list(kp.get("evidence_refs")) if _text(r)}
            if refs:
                kp_refs[kp_id] = refs

        out: dict[str, list[str]] = {}
        try:
            conflicts = self._ws.conflicts(course_id) or []
        except Exception:  # noqa: BLE001 - 冲突读取失败不该让整页 500
            return out

        for conflict in conflicts:
            if isinstance(conflict, Mapping):
                row = conflict
            elif hasattr(conflict, "to_dict"):
                row = conflict.to_dict()
            else:
                continue
            status = _text(row.get("status"))
            if status.upper() == "RESOLVED":
                continue
            cid = _text(row.get("conflict_id"))
            refs = {_text(r) for r in _as_list(row.get("evidence_refs")) if _text(r)}
            if not refs:
                continue
            for kp_id in sorted(kp_refs):
                if kp_refs[kp_id] & refs:
                    out.setdefault(kp_id, []).append(cid)
        return {k: sorted(set(v)) for k, v in out.items()}

    def _student_states(self, course_id: str, student_id: str) -> dict[str, dict[str, Any]]:
        """StudentState 索引: kp_id -> 状态记录。

        ``get_student_state`` 返回的是 ``{"states": [ ... ]}`` —— 一个
        **list of dicts**, 不是以 kp_id 为键的字典 (Task 30 的既有形状)。
        这里只是把它转成索引, 不改写内容。
        """
        try:
            payload = self._ws.student_state(course_id, student_id)
        except (NotFoundError, InvalidInputError):
            return {}
        records = payload.get("states") if isinstance(payload, Mapping) else None
        out: dict[str, dict[str, Any]] = {}
        for record in _as_list(records):
            if not isinstance(record, Mapping):
                continue
            kp_id = _text(record.get("knowledge_point_id"))
            if kp_id:
                out[kp_id] = dict(record)
        return out

    # ------------------------------------------------------------------
    # 67 — ReviewSet
    # ------------------------------------------------------------------

    def review_set(
        self, course_id: str, student_id: str, *, lang: str = "zh"
    ) -> dict[str, Any]:
        """为学生产出一份考前复习集合。

        这个方法的返回**不含任何概率/预测字段** (见 ``NO_PREDICTION_FIELDS``)。
        """
        cid = _require_id(course_id, "course_id")
        sid = _require_id(student_id, "student_id")
        ws = self._ws
        # 不存在的课程 / 学生 -> 404 (与其它端点一致, 不是 500 也不是空集合)
        ws.get_course(cid)
        ws.get_student(cid, sid)

        points = self._knowledge(cid)
        conflict_ids = self._conflict_ids_by_kp(cid, points)
        states = self._student_states(cid, sid)

        items = [
            self._item(cid, kp, conflict_ids, states)
            for kp in points
        ]
        items.sort(key=lambda row: (
            _BUCKET_RANK.get(row["bucket"], 9),
            row["sort_key"],
        ))
        for position, row in enumerate(items, start=1):
            row["position"] = position

        by_bucket = {name: [r for r in items if r["bucket"] == name] for name in BUCKETS}
        conflicts = self._conflicts(cid)

        return {
            "review_mode_version": REVIEW_MODE_VERSION,
            "course_id": cid,
            "student_id": sid,
            "lang": lang,
            "empty": not items,
            "empty_note": None if items else REVIEW_SET_EMPTY_NOTE,
            # 两条真相轴各自独立计数 —— 绝不合成一个"可信度"。
            "by_validation_status": self._tally(items, "validation_status"),
            "by_review_status": self._tally(items, "review_status"),
            "by_bucket": {name: len(rows) for name, rows in by_bucket.items()},
            "counts": {
                "total": len(items),
                "blocked": len(by_bucket["blocked"]),
                "attention": len(by_bucket["attention"]),
                "ready": len(by_bucket["ready"]),
                "unresolved_conflicts": sum(
                    1 for c in conflicts if c.get("blocking")
                ),
            },
            "buckets": {
                name: [r["knowledge_id"] for r in rows] for name, rows in by_bucket.items()
            },
            "items": items,
            "coverage": self._coverage(cid),
            "conflicts": conflicts,
            "not_a_predictor": True,
            "ordering_basis": (
                "coverage -> evidence status -> unresolved conflict -> "
                "student learning state -> knowledge_id"
            ),
        }

    # ------------------------------------------------------------------
    # 单项
    # ------------------------------------------------------------------

    def _item(
        self,
        course_id: str,
        kp: Mapping[str, Any],
        conflict_ids: Mapping[str, Sequence[str]],
        states: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        kp_id = _text(kp.get("knowledge_id"))
        validation = _text(kp.get("validation_status")) or "unverified"
        review = _text(kp.get("review_status")) or "pending"
        conflicts = list(conflict_ids.get(kp_id, ()))

        state_row = states.get(kp_id) or {}
        raw_state = _text(state_row.get("state")) or "not_started"
        signal = _STATE_SIGNAL.get(raw_state, "NOT_STARTED")

        block_reason: Optional[str] = None
        if conflicts and review != "confirmed":
            # 冲突存在, 且**人还没有就此表过态** —— 才是真正的"未解决"。
            #
            # 为什么这里需要 `review != "confirmed"` 这一半
            # --------------------------------------------
            # 领域层的 ``resolve_conflict`` 只把知识的 ``review_status``
            # 推到 CONFIRMED (并且要求调用方**显式选出**信任的一侧);
            # 它**不会**把 ``ConflictRecord.status`` 改成 RESOLVED ——
            # 记录本身按设计要为审计原样保留 ("both evidence items are
            # preserved for audit")。
            #
            # 只看 conflict.status 的话, 一个已经被人工解决的冲突会**永远**
            # 把知识点按在 blocked 上, "绝不自动解决"就变成了"永远无法解决"。
            # 反过来只看 review_status 也不行: 一条新来的冲突在人工过问之前
            # 就该拦住这个知识点。两个条件同时成立才是"未解决"。
            block_reason = "unresolved_conflict"
        elif review == "rejected":
            block_reason = "rejected_by_review"

        bucket = self._bucket_for(block_reason, signal, validation, review)
        attention_reason = (
            self._attention_reason(signal, validation, review)
            if bucket == "attention"
            else None
        )

        return {
            "knowledge_id": kp_id,
            "title": kp.get("title"),
            "content": kp.get("content"),
            "session_id": kp.get("session_id"),
            # --- 两条真相轴: 各自独立成字段 ---
            "validation_status": validation,
            "review_status": review,
            "conflict": bool(conflicts),
            "conflict_ids": sorted(conflicts),
            # --- 学生侧既有状态 (Task 30) ---
            "learning_state": raw_state,
            "learning_signal": signal,
            # --- 本模块的派生判断 (为什么放这个桶) ---
            "bucket": bucket,
            "block_reason": block_reason,
            "block_note": self._block_note(block_reason),
            "attention_reason": attention_reason,
            "attention_note": self._attention_note(attention_reason),
            "sort_key": self._sort_key(kp_id, validation, raw_state, conflicts),
        }

    def _bucket_for(
        self,
        block_reason: Optional[str],
        signal: str,
        validation: str,
        review: str,
    ) -> str:
        """桶归属 —— 只有三种结果, 且规则是显式的。

        1. 有未解决冲突 / 被人否决 -> ``blocked`` (必须先处理)
        2. 下面任一条成立 -> ``attention``:
           - StudentState 说要看 (NEEDS_REVIEW / NEEDS_PRACTICE)
           - 证据还不到 ``supported`` (unverified / conflicted)
           - **人工尚未确认** (``review_status`` 还是 ``pending``)
        3. 其余 -> ``ready``

        第 2 条里"人工尚未确认"这一项是**必需的**: 一个知识点可以同时是
        ``validation_status = supported`` (证据支持) 与
        ``review_status = pending`` (没人看过)。只看 validation 就把它放进
        "ready to review", 等于告诉学生"这是已经定稿的复习材料" ——
        而事实上没有第二个人核过。两条轴必须各自参与判桶, 否则"分开呈现"
        就只是把两个字段并排打印, 决策却仍然只看了一条。
        """
        if block_reason is not None:
            return "blocked"
        if signal in _WEAK_STATES:
            return "attention"
        if validation != "supported":
            return "attention"
        if review != "confirmed":
            return "attention"
        return "ready"

    def _block_note(self, reason: Optional[str]) -> Optional[str]:
        """阻塞说明。注意措辞里**不出现**任何"哪一方是对的"的倾向。"""
        if reason == "unresolved_conflict":
            return "Unresolved conflict: both sides are shown as-is."
        if reason == "rejected_by_review":
            return "Rejected by human review."
        return None

    @staticmethod
    def _attention_reason(
        signal: str, validation: str, review: str
    ) -> Optional[str]:
        """``attention`` 的第一成因 (按优先级取一个, 不叠加)。"""
        if signal in _WEAK_STATES:
            return "student_state"
        if validation != "supported":
            return "evidence_not_supported"
        if review != "confirmed":
            return "review_pending"
        return None

    @staticmethod
    def _attention_note(reason: Optional[str]) -> Optional[str]:
        if reason == "student_state":
            return "Your learning state records this as needing another look."
        if reason == "evidence_not_supported":
            return "The evidence behind this is not yet supported."
        if reason == "review_pending":
            return "No human has confirmed this yet."
        return None

    def _sort_key(
        self,
        kp_id: str,
        validation: str,
        raw_state: str,
        conflicts: Sequence[str],
    ) -> tuple[Any, ...]:
        """确定性排序键。

        顺序: 证据状态 -> 冲突有无 -> 学习状态 -> 知识点 ID。
        **没有任何一项来自"考试概率"** —— 这个投影里不存在这种输入。
        """
        return (
            _VALIDATION_RANK.get(validation, 3),
            0 if conflicts else 1,
            0 if raw_state in _WEAK_STATES or raw_state == "not_started" else 1,
            kp_id,
        )

    @staticmethod
    def _tally(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in rows:
            label = _text(row.get(key)) or "unknown"
            out[label] = out.get(label, 0) + 1
        return {k: out[k] for k in sorted(out)}

    # ------------------------------------------------------------------
    # 覆盖与冲突 (复用既有, 不重算)
    # ------------------------------------------------------------------

    def _coverage(self, course_id: str) -> dict[str, Any]:
        try:
            report = self._ws.coverage(course_id)
        except Exception:  # noqa: BLE001
            return {"unavailable": True, "coverage_ratio": 0.0}
        return report

    def _conflicts(self, course_id: str) -> list[dict[str, Any]]:
        """冲突的并列展示 —— 两侧证据原样输出, 不排序出"谁对"。

        ``blocking`` 表明这条冲突当前是否还在拦着复习。判据与
        ``_item`` 里的 ``block_reason`` **同源**: 只要冲突记录未 RESOLVED,
        且牵涉的知识点里还有没被人工确认过的, 就算 blocking。

        已解决的冲突**仍然返回** (记录按设计为审计保留), 只是
        ``blocking=False``。
        """
        try:
            raw = self._ws.conflicts(course_id) or []
        except Exception:  # noqa: BLE001
            return []

        # 哪些知识点已被人工确认 -> 它们名下的冲突不再阻塞
        confirmed: set[str] = set()
        for kp in self._knowledge(course_id):
            if _text(kp.get("review_status")) == "confirmed":
                confirmed.add(_text(kp.get("knowledge_id")))
        blocking_kps = {
            _text(kp.get("knowledge_id")) for kp in self._knowledge(course_id)
        } - confirmed

        rows: list[dict[str, Any]] = []
        for conflict in raw:
            row = dict(conflict) if isinstance(conflict, Mapping) else (
                conflict.to_dict() if hasattr(conflict, "to_dict") else {}
            )
            refs = sorted(_text(r) for r in _as_list(row.get("evidence_refs")) if _text(r))
            row["evidence_refs"] = refs
            # 两侧并列: Evidence A / Evidence B ... 不做"哪边正确"的判断。
            row["sides"] = [
                {"label": f"Evidence {chr(ord('A') + i)}", "evidence_id": ev}
                for i, ev in enumerate(refs)
            ]
            row["auto_resolved"] = False
            status_resolved = _text(row.get("status")).upper() == "RESOLVED"
            row["blocking"] = not status_resolved and bool(blocking_kps)
            rows.append(row)
        rows.sort(key=lambda r: _text(r.get("conflict_id")))
        return rows
