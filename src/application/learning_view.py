# -*- coding: utf-8 -*-
"""学习 UI 的只读投影 (Task 40)。

本模块只做**投影**: 把 Task 29–33 已经算好的结果组装成学生页面所需的形状。
它不新增任何 mastery 算法、不做任何推断、不写任何状态。

三条硬规则 (spec Task 40):
1. ``progress`` 只能来自已有的 Student State / Evaluation 数据 ——
   绝不偷偷增加新的 mastery inference。
2. ``Learning Path`` 的 AVAILABLE / IN_PROGRESS / COMPLETED / BLOCKED 是
   **导航状态**, 由"Task 30 的学习状态" + "Task 33 的前置链"投影得到;
   原始状态同时返回, 页面必须同时显示两者, 避免把导航状态误读成掌握度。
3. ``grounded explanation`` 只能来自 Task 29 的 GroundedKnowledgeBuilder。
   没有证据 -> ``No grounded explanation available.``; 请求语言没有证据支持
   -> ``LANGUAGE_NOT_AVAILABLE``, **绝不自动翻译、绝不自行生成**。

关于 ``submitted_at``
---------------------
领域层的 ``StudentAnswer`` **没有**时间戳 (确定性要求: 时间不参与身份)。
spec Task 41 要求答案保存 ``submitted_at``, 因此时间戳作为**运行时元数据**
记录在本层: 它由可注入的 Clock 提供, 不参与 ``answer_id``, 且重复提交同一
答案时保留**首次**提交时间 (保持幂等)。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional, Sequence

from src.application.errors import InvalidInputError, NotFoundError
from src.application.runtime import Clock, utc_now_iso

__all__ = [
    "UI_LANGUAGES",
    "DEFAULT_LANGUAGE",
    "normalize_language",
    "language_code",
    "ANSWER_FORMATS",
    "PATH_STATUSES",
    "PATH_AVAILABLE",
    "PATH_IN_PROGRESS",
    "PATH_COMPLETED",
    "PATH_BLOCKED",
    "STATE_TO_PATH_STATUS",
    "LearningViewService",
]

#: UI 可选语言 (spec Task 40 多语言)。只影响 UI 与解释请求的语言标签,
#: **绝不修改任何 Evidence**。
UI_LANGUAGES: tuple[str, ...] = ("es", "ca", "zh")

PATH_AVAILABLE = "AVAILABLE"
PATH_IN_PROGRESS = "IN_PROGRESS"
PATH_COMPLETED = "COMPLETED"
PATH_BLOCKED = "BLOCKED"
PATH_STATUSES: tuple[str, ...] = (
    PATH_AVAILABLE,
    PATH_IN_PROGRESS,
    PATH_COMPLETED,
    PATH_BLOCKED,
)

#: Task 30 的 LearningState -> UI 导航状态。
#: 这是**展示映射**, 不是掌握度判定: Task 30 的模型只有
#: not_started / exposed / practicing / reviewing 四态, 没有 "mastered"。
#: 页面必须同时显示原始 state, 让读者自行判断。
STATE_TO_PATH_STATUS: dict[str, str] = {
    "not_started": PATH_AVAILABLE,
    "exposed": PATH_IN_PROGRESS,
    "practicing": PATH_IN_PROGRESS,
    "reviewing": PATH_COMPLETED,
}

#: 视为"该模型定义的学习状态已走完"的状态集合 (仅用于判断前置是否满足)。
COMPLETION_STATES: frozenset[str] = frozenset({"reviewing"})

#: "证据语言未知" 的取值。``Unknown`` 不是一种语言, 不能当成"语言不匹配"。
UNKNOWN_LANGUAGE = "Unknown"

#: 默认请求语言 (UI 未指定时)。
DEFAULT_LANGUAGE = "es"

#: 合法的语言标签形状: BCP-47 的实用子集 —— 2~3 位字母主标签, 后接若干
#: 字母数字子标签 (``es`` / ``ca`` / ``zh`` / ``es-ES`` / ``zh-Hans``)。
#: 之所以要校验: ``language`` 会进入 ``LearningRepresentation.language``,
#: 而 representation_id 是内容寻址的 —— 放任任意字符串会把用户输入直接
#: 写进领域对象并生成垃圾表示 (实测 ``language=../../etc`` 曾被原样接受)。
_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


#: ``Language`` 枚举的显示值 -> ISO 639-1 码。证据 DTO 里的 ``language``
#: 是显示值 (``"Spanish"``), 而请求语言是码 (``"es"``); 两者不归一化就会
#: 被判定为"语言不匹配", 从而**错误地**拒绝一个本来可用的解释。
_LANGUAGE_CODES = {
    "spanish": "es",
    "catalan": "ca",
    "chinese": "zh",
    "english": "en",
}

#: 表示"语言未知"的取值 (大小写不敏感)。未知 != 另一种语言。
_UNKNOWN_LANGUAGE_CODES = frozenset(
    {UNKNOWN_LANGUAGE.lower(), "und", "unsupported", ""}
)


def language_code(value: Any) -> Optional[str]:
    """把证据的语言表示归一为 ISO 639-1 小写码; 未知/无法识别返回 ``None``。

    ``None`` 表示"不知道这门证据是什么语言", 调用方必须把它当作
    **没有语言约束**, 而不是"不匹配"。
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in _UNKNOWN_LANGUAGE_CODES:
        return None
    if text in _LANGUAGE_CODES:
        return _LANGUAGE_CODES[text]
    return text if _LANGUAGE_TAG.match(text) else None


def normalize_language(value: Any, *, default: str = DEFAULT_LANGUAGE) -> str:
    """校验并归一化请求语言标签。

    - ``None`` / 空白 -> ``default`` (UI 未选择语言时的缺省)。
    - 形状非法 (含路径分隔符、引号、超长等) -> ``INVALID_INPUT``。
    - 合法则**统一小写**: ``ES`` 与 ``es`` 必须落到同一个 representation_id,
      否则同一内容会因大小写差异产生两个不同的内容寻址 id (破坏幂等)。
    """
    if value is None:
        return default
    if not isinstance(value, str):
        raise InvalidInputError("language must be a string")
    text = value.strip()
    if not text:
        return default
    if not _LANGUAGE_TAG.match(text):
        raise InvalidInputError(f"language is not a valid language tag: {text!r}")
    return text.lower()


#: 学生视角必须隐藏的练习字段 (Task 41)。
#:
#: 提交前把答案发给浏览器会让练习失去意义。``explanation`` 与答案键一起给出
#: (它就是"为什么是这个答案"), 因此同样归入答案键, 提交后才作为反馈返回。
_ANSWER_KEY_FIELDS: tuple[str, ...] = (
    "correct_choice_id",
    "expected_answer",
    "fill_blank",
    "explanation",
)

#: 题型 -> 前端应渲染的作答控件。
ANSWER_FORMATS: dict[str, str] = {
    "multiple_choice": "choice",
    "true_false": "true_false",
    "short_answer": "text",
    "fill_blank": "text",
}


def _answer_format(exercise_type: Any) -> str:
    """题型 -> 作答控件类型 (未知题型退化为自由文本)。"""
    return ANSWER_FORMATS.get(str(exercise_type or ""), "text")


def _answer_key(exercise: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """参考答案 (只在提交后作为反馈返回)。没有可判定的答案键则返回 ``None``。"""
    key: dict[str, Any] = {}
    if exercise.get("correct_choice_id") is not None:
        key["correct_choice_id"] = exercise.get("correct_choice_id")
    if exercise.get("expected_answer") is not None:
        key["expected_answer"] = exercise.get("expected_answer")
    fill_blank = exercise.get("fill_blank")
    if isinstance(fill_blank, Mapping):
        key["blank_id"] = fill_blank.get("blank_id")
        key["accepted_answers"] = list(fill_blank.get("accepted_answers") or [])
    if exercise.get("explanation") is not None:
        key["explanation"] = exercise.get("explanation")
    return key or None


class LearningViewService:
    """学生页面 / 学习路径 / grounded explanation 的只读投影服务。"""

    def __init__(
        self,
        course_id: str,
        *,
        org_service: Any,
        store: Any,
        learning_service: Any,
        knowledge_service: Any,
        clock: Optional[Clock] = None,
    ) -> None:
        if not isinstance(course_id, str) or not course_id.strip():
            raise InvalidInputError("course_id must be a non-empty string")
        self._course_id = course_id
        self._org = org_service
        self._store = store
        self._learning = learning_service
        self._knowledge = knowledge_service
        self._clock: Clock = clock or utc_now_iso

    # ------------------------------------------------------------------
    # 1. Grounded explanation (Task 29)
    # ------------------------------------------------------------------

    def grounded_explanation(
        self, knowledge_point_id: str, language: str = "es"
    ) -> dict[str, Any]:
        """按 Task 29 的规则构建 grounded explanation。

        返回结构::

            {
              "knowledge_point_id", "requested_language",
              "status": "ok" | "not_available" | "language_not_available",
              "available": bool,
              "message": str,               # 页面直接显示的文案
              "representation": {...} | None,
              "evidence": [...],            # 支撑证据 (原文, 不改写)
              "evidence_languages": [...],  # 证据实际声明的语言
              "supported_languages": [...], # 已知的具体语言 (不含 Unknown)
              "related_concepts": [...],
              "prerequisites": [...],
            }
        """
        kp_id = _require_str(knowledge_point_id, "knowledge_point_id")
        lang = normalize_language(language)

        kp_dto = self._knowledge.get_knowledge_point(kp_id)
        evidence = self._knowledge.get_evidence_for_knowledge_point(kp_id)

        from src.knowledge_learning import GroundedKnowledgeBuilder
        from src.models import KnowledgePoint

        # language_index 只收录**已知的具体语言**, 且必须是 ISO 码:
        # 证据 DTO 里的 ``language`` 是显示值 (``"Spanish"``), 直接塞进去会
        # 与请求码 (``"es"``) 比较失败, 从而错误地否决一个本来可用的解释。
        # Language.UNKNOWN 表示"不知道", 不是"不匹配", 因此也不能用来否决。
        language_index = {}
        for item in evidence:
            code = language_code(item.get("language"))
            if code:
                language_index[str(item["evidence_id"])] = code
        builder = GroundedKnowledgeBuilder(self._store, language_index=language_index)
        status, representation = builder.build(
            KnowledgePoint.from_dict(kp_dto), lang
        )

        available = status.value == "ok"
        if available:
            message = ""
        elif status.value == "language_not_available":
            message = (
                f"No grounded explanation available in '{lang}'. "
                "The supporting evidence is in a different language; "
                "no automatic translation is performed."
            )
        else:
            message = "No grounded explanation available."

        return {
            "knowledge_point_id": kp_id,
            "requested_language": lang,
            "status": status.value,
            "available": available,
            "message": message,
            "representation": representation.to_dict() if representation else None,
            "evidence": evidence,
            "evidence_languages": sorted(
                {str(item["language"]) for item in evidence if item.get("language")}
            ),
            "supported_languages": sorted(language_index.values()),
            "related_concepts": list(kp_dto.get("related_points") or []),
            "prerequisites": self._prerequisite_ids(kp_id),
        }

    def _prerequisite_ids(self, knowledge_point_id: str) -> list[str]:
        """直接前置 (Task 26 关系层的入边 source), 确定性排序。

        只报告显式存在的关系, 不做传递闭包、不推断。
        """
        try:
            incoming = self._org.get_related_knowledge(knowledge_point_id, "incoming")
        except Exception:  # noqa: BLE001 - 关系层对未注册 KP 会抛错
            return []
        return sorted({str(rel.source_knowledge_point_id) for rel in incoming})

    # ------------------------------------------------------------------
    # 2. Learning Path view
    # ------------------------------------------------------------------

    def learning_path_view(
        self, student_id: str, target_knowledge_point_id: str
    ) -> dict[str, Any]:
        """前置链 + 每个节点的导航状态 + 练习 + 评估。"""
        sid = _require_str(student_id, "student_id")
        target = _require_str(target_knowledge_point_id, "target_knowledge_point_id")

        self._learning.get_student(sid)  # 不存在时 NotFoundError
        path = self._learning.get_learning_path(target)
        if path.get("status") == "unknown_knowledge_point":
            # 与 grounded_explanation 保持一致: 不存在的知识点是 404,
            # 而不是"一个空的路径" —— 两者语义完全不同, 不能混为一谈。
            raise NotFoundError(f"knowledge point {target!r} not found")
        node_ids = [str(n) for n in (path.get("node_ids") or [])]

        state_by_kp = self._student_state_records(sid)
        exercises_by_kp = self._exercises_by_knowledge_point()
        evaluations = self._latest_evaluation_by_exercise(sid)

        nodes: list[dict[str, Any]] = []
        for position, kp_id in enumerate(node_ids):
            earlier = node_ids[:position]
            unmet = [
                other
                for other in earlier
                if self._state_of(state_by_kp, other) not in COMPLETION_STATES
            ]
            record = state_by_kp.get(kp_id) or {}
            state = self._state_of(state_by_kp, kp_id)
            status = (
                PATH_BLOCKED
                if unmet
                else STATE_TO_PATH_STATUS.get(state, PATH_AVAILABLE)
            )
            nodes.append(
                {
                    "position": position,
                    "knowledge_point_id": kp_id,
                    "knowledge_point": self._knowledge_point_summary(kp_id),
                    "state": state,
                    "status": status,
                    "status_basis": (
                        "BLOCKED: unmet prerequisites "
                        + ", ".join(sorted(unmet))
                        if unmet
                        else f"state={state} (Task 30 LearningState)"
                    ),
                    "next_event": self._learning.next_learning_event(state),
                    "activity": {
                        "exposure_count": record.get("exposure_count", 0),
                        "practice_count": record.get("practice_count", 0),
                        "answer_count": record.get("answer_count", 0),
                        "correct_count": record.get("correct_count", 0),
                        "incorrect_count": record.get("incorrect_count", 0),
                    },
                    "prerequisite_ids": sorted(set(earlier)),
                    "unmet_prerequisite_ids": sorted(set(unmet)),
                    "exercises": exercises_by_kp.get(kp_id, []),
                    "evaluations": [
                        evaluations[exercise["exercise_id"]]
                        for exercise in exercises_by_kp.get(kp_id, [])
                        if exercise["exercise_id"] in evaluations
                    ],
                }
            )

        return {
            "course_id": self._course_id,
            "student_id": sid,
            "target_knowledge_point_id": target,
            "status": path.get("status"),
            "cycle_node_ids": list(path.get("cycle_node_ids") or []),
            "node_ids": node_ids,
            "nodes": nodes,
            "states_used": {
                kp_id: state_by_kp.get(kp_id, "not_started") for kp_id in node_ids
            },
        }

    # ------------------------------------------------------------------
    # 3. Student dashboard
    # ------------------------------------------------------------------

    def student_dashboard(self, student_id: str, *, path_limit: int = 5) -> dict[str, Any]:
        """学生首页快照。

        每一项都来自既有数据: Task 30 (状态) / Task 31 (练习) /
        Task 32 (评估) / Task 33 (计划与路径) / Task 26 (课程缺口)。
        """
        sid = _require_str(student_id, "student_id")
        student = self._learning.get_student(sid)
        status = self._learning.get_learning_status(sid)

        course_kps = sorted(self._course_knowledge_points())
        state_records = list(status.get("states") or [])
        touched = {str(record["knowledge_point_id"]) for record in state_records}

        state_counts: dict[str, int] = {}
        for record in state_records:
            key = str(record.get("state") or "unknown")
            state_counts[key] = state_counts.get(key, 0) + 1

        evaluations = self._latest_evaluation_by_exercise(sid)
        evaluation_counts: dict[str, int] = {}
        scores: list[float] = []
        for result in evaluations.values():
            key = str(result.get("status") or "unknown")
            evaluation_counts[key] = evaluation_counts.get(key, 0) + 1
            score = result.get("score")
            if isinstance(score, (int, float)):
                scores.append(float(score))
        average_score = round(sum(scores) / len(scores), 6) if scores else None

        pending = self._pending_exercises(sid)
        gaps = self._knowledge_gaps(sid, course_kps, touched, status)

        return {
            "course_id": self._course_id,
            "student_id": sid,
            "display_name": student.get("display_name"),
            "progress": {
                # 直接来自 Task 30 / 32, 不做二次推断
                "states": state_records,
                "state_counts": state_counts,
                "registered_knowledge_points": sorted(touched),
                "course_knowledge_points": course_kps,
                "not_started_knowledge_points": [
                    kp for kp in course_kps if kp not in touched
                ],
                "exercise_count": status.get("exercise_count", 0),
                "answered_count": status.get("answered_count", 0),
                "practice_counts": status.get("practice_counts", {}),
                "recent_incorrect_kps": status.get("recent_incorrect_kps", []),
                "evaluation_counts": evaluation_counts,
                "average_score": average_score,
            },
            "study_plan": self._learning.get_study_plan(sid),
            "learning_paths": self._learning_paths(sid, course_kps, path_limit),
            "pending_exercises": pending,
            "recent_evaluations": self._recent_evaluations(sid),
            "knowledge_gaps": gaps,
        }

    # ------------------------------------------------------------------
    # 4. Exercise / Answer / Evaluation view (Task 41)
    # ------------------------------------------------------------------

    def exercise_list_view(self, student_id: str) -> dict[str, Any]:
        """练习列表 (学生视角): 题型 / 关联知识点 / 前置 / 是否已作答。

        **不含答案**: 见 ``_ANSWER_KEY_FIELDS`` 的说明。
        """
        sid = _require_str(student_id, "student_id")
        self._learning.get_student(sid)  # 不存在时 NotFoundError
        evaluations = self._latest_evaluation_by_exercise(sid)
        answered = {
            str(answer["exercise_id"]): answer
            for answer in self._learning.answer_log_for(sid)
        }

        items: list[dict[str, Any]] = []
        for exercise in self._learning.list_exercises():
            exercise_id = str(exercise["exercise_id"])
            kp_ids = [str(k) for k in (exercise.get("knowledge_point_ids") or [])]
            result = evaluations.get(exercise_id)
            answer = answered.get(exercise_id)
            items.append(
                {
                    "exercise_id": exercise_id,
                    "exercise_type": exercise.get("exercise_type"),
                    "prompt": exercise.get("prompt"),
                    "difficulty": exercise.get("difficulty"),
                    "answer_format": _answer_format(exercise.get("exercise_type")),
                    "knowledge_point_ids": kp_ids,
                    "knowledge_points": self._knowledge_point_summaries(kp_ids),
                    "prerequisites": self._prerequisites_of(kp_ids),
                    "submitted": exercise_id in answered,
                    "answer_id": answer.get("answer_id") if answer else None,
                    "submitted_value": answer.get("submitted_value") if answer else None,
                    "submitted_at": self._learning.submitted_at_for(
                        str(answer["answer_id"])
                    )
                    if answer
                    else None,
                    "evaluation_status": result.get("status") if result else None,
                    "score": result.get("score") if result else None,
                }
            )
        items.sort(key=lambda item: item["exercise_id"])

        return {
            "course_id": self._course_id,
            "student_id": sid,
            "exercises": items,
            "total": len(items),
            "answered": sum(1 for item in items if item["submitted"]),
            "unanswered": sum(1 for item in items if not item["submitted"]),
        }

    def exercise_view(self, student_id: str, exercise_id: str) -> dict[str, Any]:
        """单题视图 (学生视角)。

        显示 spec Task 41 要求的全部内容: 题目 / 题型 / 关联 KnowledgePoint /
        prerequisite, 以及"这道题凭什么存在"的证据。

        **提交前绝不返回答案** (``_ANSWER_KEY_FIELDS``)。提交后答案作为**反馈**
        出现 —— 与评估结果一起, 而不是在题目里。
        """
        sid = _require_str(student_id, "student_id")
        eid = _require_str(exercise_id, "exercise_id")
        self._learning.get_student(sid)  # 不存在时 NotFoundError
        exercise = self._learning.get_exercise(eid)  # 不存在时 NotFoundError

        kp_ids = [str(k) for k in (exercise.get("knowledge_point_ids") or [])]
        evidence, unresolved = self._evidence_for(kp_ids, exercise.get("evidence_ids"))
        answer = self._latest_answer_for(sid, eid)
        evaluation = self._evaluation_view(sid, eid, exercise, kp_ids, evidence)
        submitted = answer is not None

        return {
            "course_id": self._course_id,
            "student_id": sid,
            "exercise_id": eid,
            "exercise_type": exercise.get("exercise_type"),
            "prompt": exercise.get("prompt"),
            "choices": [
                {"choice_id": str(c.get("choice_id")), "text": c.get("text")}
                for c in (exercise.get("choices") or [])
            ],
            "difficulty": exercise.get("difficulty"),
            "answer_format": _answer_format(exercise.get("exercise_type")),
            "knowledge_point_ids": kp_ids,
            "knowledge_points": self._knowledge_point_summaries(kp_ids),
            "prerequisites": self._prerequisites_of(kp_ids),
            "evidence": evidence,
            "unresolved_evidence_ids": unresolved,
            "evidence_complete": not unresolved,
            "submitted": submitted,
            "answer_id": answer.get("answer_id") if answer else None,
            "submitted_value": answer.get("submitted_value") if answer else None,
            "submitted_at": self._learning.submitted_at_for(str(answer["answer_id"]))
            if answer
            else None,
            "sequence": answer.get("sequence") if answer else None,
            "evaluation": evaluation,
            # 答案键: 只在提交后作为反馈出现, 且明确标注它**不是**知识核验。
            "answer_key_available": submitted,
            "answer_key": _answer_key(exercise) if submitted else None,
            "answer_key_withheld": not submitted,
        }

    def exercise_evaluation_view(
        self, student_id: str, exercise_id: str
    ) -> dict[str, Any]:
        """评估结果视图: score / status / feedback / knowledge_point / evidence。

        顶部显式声明 ``is_fact_verification: False`` / ``affects_knowledge_base:
        False`` —— 评估是"学生这一答对不对", 与"知识是否成立"是两件事。
        """
        sid = _require_str(student_id, "student_id")
        eid = _require_str(exercise_id, "exercise_id")
        self._learning.get_student(sid)
        exercise = self._learning.get_exercise(eid)

        answer = self._latest_answer_for(sid, eid)
        if answer is None:
            raise NotFoundError(
                f"no answer submitted by {sid!r} for exercise {eid!r}"
            )
        kp_ids = [str(k) for k in (exercise.get("knowledge_point_ids") or [])]
        evidence, unresolved = self._evidence_for(kp_ids, exercise.get("evidence_ids"))
        evaluation = self._evaluation_view(sid, eid, exercise, kp_ids, evidence)

        return {
            "course_id": self._course_id,
            "student_id": sid,
            "exercise_id": eid,
            "submitted": True,
            "answer_id": answer.get("answer_id"),
            "submitted_value": answer.get("submitted_value"),
            "submitted_at": self._learning.submitted_at_for(str(answer["answer_id"])),
            "sequence": answer.get("sequence"),
            "evaluation": evaluation,
            # 明确的语义边界: 评估 ≠ 事实核验。
            "is_fact_verification": False,
            "affects_knowledge_base": False,
            "note": (
                "This is an evaluation of the student's answer, not a fact "
                "verification of the knowledge point. A wrong answer never "
                "changes the knowledge base."
            ),
        }

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _course_knowledge_points(self) -> list[str]:
        """课程 KP 集合 (知识库为准, 确定性排序)。"""
        ids: set[str] = set()
        for getter in (
            lambda: self._org.registered_knowledge_point_ids,
            lambda: self._learning.course_knowledge_points,
        ):
            try:
                ids |= {str(item) for item in getter()}
            except Exception:  # noqa: BLE001 - 快照缺失时退化为空集
                continue
        return sorted(ids)

    def _student_state_records(self, student_id: str) -> dict[str, dict[str, Any]]:
        """该学生在本课程的 Task 30 状态记录, 按 knowledge_point_id 索引。"""
        state = self._learning.get_student_state(student_id)
        return {
            str(record["knowledge_point_id"]): dict(record)
            for record in (state.get("states") or [])
        }

    @staticmethod
    def _state_of(records: Mapping[str, dict[str, Any]], knowledge_point_id: str) -> str:
        record = records.get(knowledge_point_id)
        if not record:
            return "not_started"
        return str(record.get("state") or "not_started")

    def _knowledge_point_summary(self, knowledge_point_id: str) -> Optional[dict[str, Any]]:
        try:
            dto = self._knowledge.get_knowledge_point(knowledge_point_id)
        except NotFoundError:
            return None
        return {
            "knowledge_id": dto["knowledge_id"],
            "title": dto["title"],
            "validation_status": dto.get("validation_status"),
            "review_status": dto.get("review_status"),
            "knowledge_score": dto.get("knowledge_score"),
        }

    def _exercises_by_knowledge_point(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for exercise in self._learning.list_exercises():
            for kp_id in exercise.get("knowledge_point_ids") or []:
                grouped.setdefault(str(kp_id), []).append(exercise)
        for items in grouped.values():
            items.sort(key=lambda item: str(item["exercise_id"]))
        return grouped

    def _latest_evaluation_by_exercise(self, student_id: str) -> dict[str, dict[str, Any]]:
        """每个练习取该学生 sequence 最大的那份提交对应的评估 (确定性)。"""
        log = self._learning.answer_log_for(student_id)
        latest: dict[str, tuple[int, str]] = {}
        for answer in log:
            exercise_id = str(answer["exercise_id"])
            sequence = int(answer.get("sequence") or 0)
            current = latest.get(exercise_id)
            if current is None or sequence >= current[0]:
                latest[exercise_id] = (sequence, str(answer["answer_id"]))
        out: dict[str, dict[str, Any]] = {}
        for exercise_id, (_, answer_id) in latest.items():
            try:
                result = self._learning.get_evaluation(answer_id)
            except NotFoundError:
                continue
            out[exercise_id] = {
                "answer_id": answer_id,
                "evaluation_id": result.get("evaluation_id"),
                "status": result.get("status"),
                "score": result.get("score"),
                "feedback": result.get("feedback"),
            }
        return out

    def _pending_exercises(self, student_id: str) -> list[dict[str, Any]]:
        answered = {
            str(answer["exercise_id"])
            for answer in self._learning.answer_log_for(student_id)
        }
        registered = {
            str(record["knowledge_point_id"])
            for record in (self._learning.get_student_state(student_id).get("states") or [])
        }
        course_kps = set(self._course_knowledge_points())
        pending: list[dict[str, Any]] = []
        for exercise in self._learning.list_exercises():
            exercise_id = str(exercise["exercise_id"])
            if exercise_id in answered:
                continue
            kp_ids = [str(k) for k in (exercise.get("knowledge_point_ids") or [])]
            if not (set(kp_ids) & (registered | course_kps)):
                continue
            pending.append(
                {
                    "exercise_id": exercise_id,
                    "exercise_type": exercise.get("exercise_type"),
                    "prompt": exercise.get("prompt"),
                    "knowledge_point_ids": kp_ids,
                    "difficulty": exercise.get("difficulty"),
                }
            )
        pending.sort(key=lambda item: item["exercise_id"])
        return pending

    def _recent_evaluations(self, student_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """最近评估: 顺序 = 提交顺序的逆序 (领域层无时间戳, 不臆造时间)。

        ``submitted_at`` 来自运行时 Clock (见模块 docstring), 不参与身份。
        """
        log = self._learning.answer_log_for(student_id)
        items: list[dict[str, Any]] = []
        for answer in reversed(log):
            answer_id = str(answer["answer_id"])
            try:
                result = self._learning.get_evaluation(answer_id)
            except NotFoundError:
                continue
            items.append(
                {
                    "answer_id": answer_id,
                    "exercise_id": str(answer["exercise_id"]),
                    "submitted_value": answer.get("submitted_value"),
                    "sequence": answer.get("sequence"),
                    "submitted_at": self._learning.submitted_at_for(answer_id),
                    "status": result.get("status"),
                    "score": result.get("score"),
                    "feedback": result.get("feedback"),
                }
            )
            if len(items) >= limit:
                break
        return items

    # ---- Task 41 helpers ---------------------------------------------

    def _knowledge_point_summaries(
        self, knowledge_point_ids: Sequence[str]
    ) -> list[dict[str, Any]]:
        """关联知识点摘要, 保持调用方给的顺序 (确定性)。"""
        out: list[dict[str, Any]] = []
        for kp_id in knowledge_point_ids:
            summary = self._knowledge_point_summary(str(kp_id))
            if summary is not None:
                out.append(summary)
        return out

    def _prerequisites_of(self, knowledge_point_ids: Sequence[str]) -> list[str]:
        """关联知识点的**直接**前置并集 (Task 26 入边, 不做传递闭包)。

        只报告显式存在的关系, 不推断 —— 与 ``_prerequisite_ids`` 同一规则。
        """
        ids: set[str] = set()
        for kp_id in knowledge_point_ids:
            ids |= set(self._prerequisite_ids(str(kp_id)))
        return sorted(ids)

    def _evidence_for(
        self, knowledge_point_ids: Sequence[str], exercise_evidence_ids: Any
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """练习引用的证据 (原文不改写) + 解析不到的 id。

        先取关联知识点的证据作为索引, 再用练习自己的 ``evidence_ids`` 挑选;
        两者都不足时按 id 回查 EvidenceStore。解析不到的 id **显式返回**,
        不静默吞掉 (与 Task 39 的断链处理一致)。
        """
        by_id: dict[str, dict[str, Any]] = {}
        for kp_id in knowledge_point_ids:
            try:
                items = self._knowledge.get_evidence_for_knowledge_point(str(kp_id))
            except NotFoundError:
                continue
            for item in items:
                by_id.setdefault(str(item.get("evidence_id")), item)

        wanted = [str(e) for e in (exercise_evidence_ids or []) if str(e)]
        if not wanted:
            return self._sorted_evidence(by_id.values()), []

        resolved: list[dict[str, Any]] = []
        unresolved: list[str] = []
        for evidence_id in wanted:
            item = by_id.get(evidence_id)
            if item is None:
                item = self._evidence_by_id(evidence_id)
            if item is None:
                unresolved.append(evidence_id)
            else:
                resolved.append(item)
        return self._sorted_evidence(resolved), sorted(unresolved)

    def _evidence_by_id(self, evidence_id: str) -> Optional[dict[str, Any]]:
        store = self._store
        getter = getattr(store, "get", None)
        if getter is None:
            return None
        try:
            evidence = getter(evidence_id, include_retired=True)
        except TypeError:
            evidence = getter(evidence_id)
        except Exception:  # noqa: BLE001 - 存储异常不应让页面崩掉
            return None
        if evidence is None:
            return None
        to_dict = getattr(evidence, "to_dict", None)
        return to_dict() if callable(to_dict) else None

    @staticmethod
    def _sorted_evidence(items: Any) -> list[dict[str, Any]]:
        return sorted(
            (dict(item) for item in items),
            key=lambda item: str(item.get("evidence_id") or ""),
        )

    def _latest_answer_for(
        self, student_id: str, exercise_id: str
    ) -> Optional[dict[str, Any]]:
        """该学生在该题上的最新作答 (按 sequence, 再按 answer_id 确定性打破平局)。"""
        best: Optional[dict[str, Any]] = None
        best_key: Optional[tuple[int, str]] = None
        for answer in self._learning.answer_log_for(student_id):
            if str(answer.get("exercise_id")) != exercise_id:
                continue
            sequence = int(answer.get("sequence") or 0)
            key = (sequence, str(answer.get("answer_id")))
            if best_key is None or key > best_key:
                best, best_key = dict(answer), key
        return best

    def _evaluation_view(
        self,
        student_id: str,
        exercise_id: str,
        exercise: Mapping[str, Any],
        knowledge_point_ids: Sequence[str],
        evidence: Sequence[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        """评估 DTO (Task 32 结果的投影, 不重算)。

        spec Task 41 要求显示 ``score / status / feedback / knowledge_point /
        evidence``; ``expected`` 是**参考答案**, 只在评估已存在时给出。
        """
        answer = self._latest_answer_for(student_id, exercise_id)
        if answer is None:
            return None
        try:
            result = self._learning.get_evaluation(str(answer["answer_id"]))
        except NotFoundError:
            return None
        return {
            "evaluation_id": result.get("evaluation_id"),
            "answer_id": result.get("answer_id"),
            "status": result.get("status"),
            "score": result.get("score"),
            "feedback": result.get("feedback"),
            "evaluator_version": result.get("evaluator_version"),
            "knowledge_point_ids": list(knowledge_point_ids),
            "knowledge_points": self._knowledge_point_summaries(knowledge_point_ids),
            "evidence": [dict(item) for item in evidence],
            "expected": _answer_key(exercise),
            "is_fact_verification": False,
            "affects_knowledge_base": False,
        }

    def _knowledge_gaps(
        self,
        student_id: str,
        course_kps: Sequence[str],
        touched: set[str],
        status: Mapping[str, Any],
    ) -> dict[str, Any]:
        """学习缺口 = 已有的两类事实, 不新增判定规则。

        - STUDENT_RECENT_INCORRECT: Task 30/32 已有的 recent_incorrect 集合。
        - COURSE_COVERAGE_GAP: Task 26 KnowledgeCoverageAnalyzer 的缺口报告。
        - NOT_STARTED: 课程有该 KP, 但学生还没有任何学习记录 (纯计数事实)。
        """
        recent_incorrect = sorted(
            str(kp) for kp in (status.get("recent_incorrect_kps") or [])
        )
        student_gaps = [
            {"knowledge_point_id": kp, "reason": "STUDENT_RECENT_INCORRECT"}
            for kp in recent_incorrect
        ]
        not_started = [kp for kp in course_kps if kp not in touched]
        course_gaps: list[dict[str, Any]] = []
        try:
            report = self._knowledge.get_gaps(self._course_id)
            course_gaps = [
                dict(gap) for gap in (report.get("gaps") or [])
            ]
        except Exception:  # noqa: BLE001 - 缺口分析失败不应让首页崩掉
            course_gaps = []
        return {
            "student_gaps": student_gaps,
            "not_started_knowledge_points": not_started,
            "course_gaps": course_gaps,
        }

    def _learning_paths(
        self, student_id: str, course_kps: Sequence[str], limit: int
    ) -> list[dict[str, Any]]:
        """为尚未走完的课程 KP 生成路径视图 (确定性取前 limit 个)。

        选择规则是纯展示规则: 课程 KP 升序, 跳过已达 COMPLETION_STATES 的,
        取前 ``limit`` 个。不涉及任何掌握度推断。
        """
        state_by_kp = self._student_state_records(student_id)
        selected: list[str] = []
        for kp_id in course_kps:
            if self._state_of(state_by_kp, kp_id) in COMPLETION_STATES:
                continue
            selected.append(kp_id)
            if len(selected) >= max(0, int(limit)):
                break
        return [
            {
                "target_knowledge_point_id": kp_id,
                "path": self.learning_path_view(student_id, kp_id),
            }
            for kp_id in selected
        ]


def _require_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(f"{name} must be a non-empty string")
    return value.strip()
