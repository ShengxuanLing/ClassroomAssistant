# -*- coding: utf-8 -*-
"""学生 / 练习 / 作答 / 评估 / 学习 服务 (Task 34)。

把 StudentLearningLog + ExplicitExerciseBuilder + ExactEvaluator +
AnswerEvaluationLog + StudyPlanner 组合为稳定业务入口:

- 学生: create_student / get_student / list_students / get_student_state
- 练习: create_exercise / list_exercises / get_exercise / submit_answer / get_evaluation
- 学习: get_study_plan / get_learning_path / get_learning_status

硬性约束:
- 幂等: 重复创建相同学生 / 练习 返回同一 DTO。
- 确定性: 业务 ID 由 domain 层 SHA-256 生成, 应用层不引入随机性。
- 作答闭环: submit_answer 之后, 评估结果同步登记到该学生每个相关
  KP 的 StudentLearningLog (answer event + correct/incorrect 计数),
  并维护 planner 所需的 practice_counts / recent_incorrect 快照。
  学生作答永远不修改 KnowledgePoint 事实 (Task 34 硬性原则)。
"""

from __future__ import annotations

from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from src.application.runtime import Clock, utc_now_iso
from src.student_learning import (
    LearningEventType,
    LearningState,
    Student,
    StudentLearningLog,
)
from src.exercises import (
    Choice,
    ExplicitExerciseBuilder,
    Exercise,
    ExerciseError,
    ExerciseType,
    FillBlank,
)
from src.answer_evaluation import (
    AnswerEvaluationLog,
    EvaluationResult,
    EvaluationStatus,
    ExactEvaluator,
    StudentAnswer,
)
from src.study_plan import (
    LearningPath,
    StudyPlanner,
)
from src.application.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
)

__all__ = ["LearningService"]

_EXERCISE_TYPE_ALIASES: Dict[str, ExerciseType] = {
    "multiple_choice": ExerciseType.MULTIPLE_CHOICE,
    "true_false": ExerciseType.TRUE_FALSE,
    "short_answer": ExerciseType.SHORT_ANSWER,
    "fill_blank": ExerciseType.FILL_BLANK,
}


def _require_nonempty_str(value: Any, field_name: str) -> str:
    if value is None:
        raise InvalidInputError(f"{field_name} is required")
    if not isinstance(value, str):
        raise InvalidInputError(f"{field_name} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise InvalidInputError(f"{field_name} must be a non-empty string")
    return stripped


def _student_to_dict(student: Student) -> dict[str, Any]:
    return {
        "student_id": student.student_id,
        "display_name": student.display_name,
    }


def _exercise_to_dict(exercise: Exercise) -> dict[str, Any]:
    return {
        "exercise_id": exercise.exercise_id,
        "course_id": exercise.course_id,
        "knowledge_point_ids": list(exercise.knowledge_point_ids),
        "exercise_type": exercise.exercise_type.value,
        "prompt": exercise.prompt,
        "choices": [
            {"choice_id": c.choice_id, "text": c.text}
            for c in exercise.choices
        ],
        "correct_choice_id": exercise.correct_choice_id,
        "expected_answer": exercise.expected_answer,
        "fill_blank": (
            {
                "blank_id": exercise.fill_blank.blank_id,
                "accepted_answers": list(exercise.fill_blank.accepted_answers),
            }
            if exercise.fill_blank
            else None
        ),
        "explanation": exercise.explanation,
        "evidence_ids": list(exercise.evidence_ids),
        "difficulty": exercise.difficulty,
    }


def _answer_to_dict(answer: StudentAnswer) -> dict[str, Any]:
    return {
        "answer_id": answer.answer_id,
        "student_id": answer.student_id,
        "exercise_id": answer.exercise_id,
        "submitted_value": answer.submitted_value,
        "sequence": answer.sequence,
    }


def _evaluation_to_dict(result: EvaluationResult) -> dict[str, Any]:
    return {
        "evaluation_id": result.evaluation_id,
        "answer_id": result.answer_id,
        "status": result.status.value,
        "score": result.score,
        "feedback": result.feedback,
        "evaluator_version": result.evaluator_version,
    }


def _state_record_to_dict(record: Any) -> dict[str, Any]:
    state = getattr(record, "state", None)
    return {
        "knowledge_point_id": record.knowledge_point_id,
        "state": state.value if isinstance(state, LearningState) else str(state),
        "first_seen_at": record.first_seen_at,
        "last_activity_at": record.last_activity_at,
        "exposure_count": record.exposure_count,
        "practice_count": record.practice_count,
        "answer_count": record.answer_count,
        "correct_count": record.correct_count,
        "incorrect_count": record.incorrect_count,
    }


class LearningService:
    """学生 / 练习 / 作答 / 评估 / 学习 的统一入口。

    作答闭环 (submit_answer):
    1. 校验学生 / 练习存在;
    2. 创建确定性 StudentAnswer (同输入幂等);
    3. ExactEvaluator 评估该答案;
    4. 把答案事件登记到该学生每个相关 KP 的 StudentLearningLog
       (ANSWERED 事件 + correct/incorrect 计数);
    5. 更新 planner 快照: 该练习关联 KP 的练习次数 +1,
       答错的 KP 记入 recent_incorrect。

    planner 快照 (practice_counts / recent_incorrect / 已注册 KP) 与
    每个学生的 StudentLearningLog 保持一致: get_study_plan 依据这些
    快照生成学习计划, 保证 "计划反映真实作答"。
    """

    def __init__(
        self,
        course_id: str,
        *,
        clock: Optional[Clock] = None,
        prerequisite_provider: Optional[Callable[[], Mapping[str, Any]]] = None,
    ) -> None:
        self._course_id = _require_nonempty_str(course_id, "course_id")
        self._students: Dict[str, Student] = {}
        self._student_logs: Dict[str, StudentLearningLog] = {}
        self._exercises: Dict[str, Exercise] = {}
        self._exercise_builder = ExplicitExerciseBuilder(self._course_id)
        self._answer_log = AnswerEvaluationLog(ExactEvaluator({}))
        self._practice_counts: Dict[str, int] = {}
        self._recent_incorrect: Set[str] = set()
        self._evaluation_by_answer: Dict[str, EvaluationResult] = {}
        # 课程知识点集合: 由知识库 (Evidence-backed) 显式登记进来,
        # 学习层绝不自行推断"这门课应该有哪些知识点"。
        self._course_knowledge_points: Set[str] = set()
        # 答案提交时间戳 (Task 40/41): **运行时元数据**, 由可注入 Clock 提供。
        # 它不参与 answer_id (确定性身份), 且同一答案重复提交时保留首次时间,
        # 因此提交是幂等的。
        self._clock: Clock = clock or utc_now_iso
        self._submitted_at: Dict[str, str] = {}
        # 前置关系提供者 (Task 40 修复): 学习路径必须看到**真实的**
        # PREREQUISITE 关系, 否则 Task 33 的前置链永远为空、Learning Path
        # 退化成 [target] 单节点。提供者由装配层注入 (读知识组织服务),
        # 学习层自己不推断依赖 —— 只消费显式关系。
        self._prerequisite_provider = prerequisite_provider

    # ------------------------------------------------------------------
    # 课程知识点登记 (Task 38 接线)
    # ------------------------------------------------------------------

    @property
    def course_knowledge_points(self) -> Set[str]:
        return set(self._course_knowledge_points)

    def register_course_knowledge_points(
        self, knowledge_point_ids: Iterable[str]
    ) -> int:
        """把课程知识点登记到学习层 (幂等), 返回新增数量。

        调用方是知识库 (KnowledgeOrganizationService 的已注册 KP), 因此
        学习规划只能基于**真实存在**的知识点展开。
        """
        added = 0
        for kp_id in knowledge_point_ids or ():
            if not isinstance(kp_id, str) or not kp_id.strip():
                continue
            if kp_id not in self._course_knowledge_points:
                self._course_knowledge_points.add(kp_id)
                added += 1
        return added

    # ------------------------------------------------------------------
    # 持久化恢复 (Task 51)
    # ------------------------------------------------------------------

    def load_state(
        self,
        *,
        students: Iterable[Student] = (),
        logs: Iterable[StudentLearningLog] = (),
        exercises: Iterable[Exercise] = (),
        answer_log: Optional[AnswerEvaluationLog] = None,
        evaluations: Iterable[EvaluationResult] = (),
        submitted_at: Optional[Mapping[str, str]] = None,
        course_knowledge_points: Iterable[str] = (),
    ) -> dict[str, int]:
        """从 SQLite 恢复学习层状态 (**幂等**, 返回恢复条数)。

        恢复的东西与内存里的一一对应, 没有"替身对象"::

            students            -> self._students
            logs                -> self._student_logs
            exercises           -> self._exercises (+ evaluator 目录)
            answer_log          -> self._answer_log (追加式历史)
            evaluations         -> self._evaluation_by_answer
            submitted_at        -> self._submitted_at (运行时元数据)
            course_knowledge_points -> self._course_knowledge_points

        ``practice_counts`` / ``recent_incorrect`` **不单独持久化**, 而是由
        已落盘的作答与评估**重新推导**: 它们是同一份事实的两种视图, 存第二份
        就一定会漂移。推导规则与 :meth:`submit_answer` 的写入规则逐条对应
        (见 ``_rebuild_planner_snapshot``), 因此"重启前后学习计划一致"是
        结构性保证, 不是巧合。
        """
        counts = {"students": 0, "logs": 0, "exercises": 0, "evaluations": 0}

        for exercise in exercises:
            if exercise is None or not exercise.exercise_id:
                continue
            self._exercises[exercise.exercise_id] = exercise
            counts["exercises"] += 1

        for student in students:
            if student is None or not student.student_id:
                continue
            self._students[student.student_id] = student
            self._student_logs.setdefault(student.student_id, StudentLearningLog(student))
            counts["students"] += 1

        for log in logs:
            if log is None:
                continue
            student = log.student
            self._students[student.student_id] = student
            self._student_logs[student.student_id] = log
            counts["logs"] += 1

        if answer_log is not None:
            self._answer_log = answer_log

        for result in evaluations:
            if result is None or not result.answer_id:
                continue
            self._evaluation_by_answer[result.answer_id] = result
            counts["evaluations"] += 1

        for answer_id, value in dict(submitted_at or {}).items():
            if value is not None:
                self._submitted_at[str(answer_id)] = str(value)

        self.register_course_knowledge_points(course_knowledge_points)
        self._rebuild_planner_snapshot()
        return counts

    def _rebuild_planner_snapshot(self) -> None:
        """从已恢复的作答 / 评估重建 planner 快照 (确定性)。

        - ``practice_counts[kp]`` = 引用了该 kp 的**去重作答**数;
        - ``recent_incorrect``   = 至少有一次 INCORRECT 评估的 kp 集合。

        与 :meth:`submit_answer` 的写入路径逐条对应 —— 那边同样只在
        **新的**答案上计数 (幂等), 因此两侧不可能给出不同数字。
        """
        self._practice_counts = {}
        self._recent_incorrect = set()
        for answer in self._answer_log.answers_for_all():
            exercise = self._exercises.get(answer.exercise_id)
            if exercise is None:
                continue
            for kp_id in exercise.knowledge_point_ids:
                self._practice_counts[kp_id] = self._practice_counts.get(kp_id, 0) + 1
            result = self._evaluation_by_answer.get(answer.answer_id)
            if result is not None and result.status is EvaluationStatus.INCORRECT:
                self._recent_incorrect.update(exercise.knowledge_point_ids)

    # ------------------------------------------------------------------
    # 学生
    # ------------------------------------------------------------------

    def create_student(
        self,
        student_id: str,
        display_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """创建 / 获取一个 Student (幂等: 相同 student_id 返回同一个)。"""
        sid = _require_nonempty_str(student_id, "student_id")
        existing = self._students.get(sid)
        if existing is not None:
            if display_name is not None and existing.display_name != display_name:
                raise ConflictError(
                    f"student {sid!r} already exists with display_name "
                    f"{existing.display_name!r}"
                )
            return _student_to_dict(existing)
        student = Student.create(sid, display_name)
        self._students[sid] = student
        self._student_logs[sid] = StudentLearningLog(student)
        return _student_to_dict(student)

    def get_student(self, student_id: str) -> dict[str, Any]:
        sid = _require_nonempty_str(student_id, "student_id")
        student = self._students.get(sid)
        if student is None:
            raise NotFoundError(f"student {sid!r} not found")
        return _student_to_dict(student)

    def list_students(self) -> List[dict[str, Any]]:
        return [
            _student_to_dict(s)
            for _, s in sorted(self._students.items())
        ]

    def get_student_state(
        self,
        student_id: str,
        course_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """返回指定学生的当前学习状态 (per-KP 状态 + 事件计数)。"""
        sid = _require_nonempty_str(student_id, "student_id")
        log = self._student_logs.get(sid)
        if log is None:
            raise NotFoundError(f"student {sid!r} not found")
        cid = course_id or self._course_id
        # 该学生在本课程已注册的 KP (课程级过滤, 确定性排序)
        registered = sorted(
            key[1] for key in log.registered_knowledge_points if key[0] == cid
        )
        states: List[dict[str, Any]] = [
            _state_record_to_dict(log.derive_state(cid, kp_id))
            for kp_id in registered
        ]
        return {
            "student_id": sid,
            "course_id": cid,
            "registered_knowledge_points": registered,
            "states": states,
        }

    # ------------------------------------------------------------------
    # 练习
    # ------------------------------------------------------------------

    def create_exercise(
        self,
        exercise_type: str,
        prompt: str,
        knowledge_point_ids: Sequence[str],
        *,
        choices: Optional[Sequence[Mapping[str, Any]]] = None,
        correct_choice_id: Optional[str] = None,
        expected_answer: Optional[str] = None,
        is_true: Optional[bool] = None,
        blank_id: Optional[str] = None,
        accepted_answers: Optional[Sequence[str]] = None,
        evidence_ids: Optional[Sequence[str]] = None,
        explanation: Optional[str] = None,
        difficulty: Optional[int] = None,
    ) -> dict[str, Any]:
        """创建一道练习 (幂等: 相同输入返回同一个练习)。

        exercise_type 取值: "multiple_choice" / "true_false" /
        "short_answer" / "fill_blank"。
        """
        et_raw = _require_nonempty_str(exercise_type, "exercise_type").lower()
        et = _EXERCISE_TYPE_ALIASES.get(et_raw)
        if et is None:
            raise InvalidInputError(f"unknown exercise_type: {et_raw!r}")

        _prompt = _require_nonempty_str(prompt, "prompt")
        if not knowledge_point_ids:
            raise InvalidInputError("knowledge_point_ids must not be empty")
        kp_ids = tuple(
            _require_nonempty_str(k, "knowledge_point_ids")
            for k in knowledge_point_ids
        )
        # 相关 KP 必须已注册到学生学习日志之外: 练习允许引用任意 KP,
        # 但作答时只登记该学生已注册的 KP (见 submit_answer)。

        choice_objs: Optional[Tuple[Choice, ...]] = None
        if choices:
            choice_objs = tuple(
                Choice(
                    choice_id=_require_nonempty_str(
                        dict(c).get("choice_id"), "choices.choice_id"
                    ),
                    text=str(dict(c).get("text", "")),
                )
                for c in choices
            )

        fill_obj: Optional[Tuple[str, Tuple[str, ...]]] = None
        if et is ExerciseType.FILL_BLANK:
            blank_id = _require_nonempty_str(blank_id, "blank_id")
            if not accepted_answers:
                raise InvalidInputError(
                    "accepted_answers must not be empty for fill_blank exercises"
                )
            fill_obj = (blank_id, tuple(
                _require_nonempty_str(a, "accepted_answers")
                for a in accepted_answers
            ))

        kwargs: Dict[str, Any] = dict(
            evidence_ids=tuple(evidence_ids or ()),
            explanation=explanation,
            difficulty=difficulty,
        )

        try:
            if et is ExerciseType.MULTIPLE_CHOICE:
                if choice_objs is None:
                    raise InvalidInputError(
                        "choices are required for multiple_choice exercises"
                    )
                exercise = self._exercise_builder.build_multiple_choice(
                    _prompt, kp_ids, choice_objs,
                    _require_nonempty_str(correct_choice_id, "correct_choice_id"),
                    **kwargs,
                )
            elif et is ExerciseType.TRUE_FALSE:
                if is_true is None:
                    raise InvalidInputError(
                        "is_true is required for true_false exercises"
                    )
                exercise = self._exercise_builder.build_true_false(
                    _prompt, kp_ids, is_true, **kwargs
                )
            elif et is ExerciseType.SHORT_ANSWER:
                exercise = self._exercise_builder.build_short_answer(
                    _prompt, kp_ids,
                    _require_nonempty_str(expected_answer, "expected_answer"),
                    **kwargs,
                )
            else:
                exercise = self._exercise_builder.build_fill_blank(
                    _prompt, kp_ids, fill_obj[0], fill_obj[1], **kwargs
                )
        except ExerciseError as exc:
            code = getattr(exc, "code", None)
            if code is not None and hasattr(code, "value"):
                code = code.value
            raise InvalidInputError(
                f"cannot create exercise: {exc}",
                detail={"domain_code": str(code)} if code else None,
            )

        existing = self._exercises.get(exercise.exercise_id)
        if existing is not None:
            return _exercise_to_dict(existing)
        self._exercises[exercise.exercise_id] = exercise
        return _exercise_to_dict(exercise)

    def list_exercises(self) -> List[dict[str, Any]]:
        return [
            _exercise_to_dict(e)
            for _, e in sorted(self._exercises.items())
        ]

    def get_exercise(self, exercise_id: str) -> dict[str, Any]:
        eid = _require_nonempty_str(exercise_id, "exercise_id")
        exercise = self._exercises.get(eid)
        if exercise is None:
            raise NotFoundError(f"exercise {eid!r} not found")
        return _exercise_to_dict(exercise)

    # ------------------------------------------------------------------
    # 作答 / 评估
    # ------------------------------------------------------------------

    def submit_answer(
        self,
        student_id: str,
        exercise_id: str,
        submitted_value: str,
        sequence: int = 0,
    ) -> dict[str, Any]:
        """提交一份学生答案 -> 幂等 DTO (同输入返回同一答案)。

        提交后自动:
        - 评估答案 (ExactEvaluator);
        - 在学生学习日志登记 answer 事件 + 计数;
        - 更新 planner 快照 (practice_counts / recent_incorrect)。

        绝不修改 KnowledgePoint 事实或 knowledge structure。
        """
        sid = _require_nonempty_str(student_id, "student_id")
        eid = _require_nonempty_str(exercise_id, "exercise_id")
        if sid not in self._students:
            raise NotFoundError(f"student {sid!r} not found")
        if eid not in self._exercises:
            raise NotFoundError(f"exercise {eid!r} not found")
        if not isinstance(submitted_value, str):
            raise InvalidInputError(
                f"submitted_value must be a string, got {type(submitted_value).__name__}"
            )
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise InvalidInputError("sequence must be a non-negative integer")

        exercise = self._exercises[eid]
        answer = StudentAnswer.create(sid, eid, submitted_value, sequence)

        # 0) 这是一次**新的**作答吗?
        #
        # Task 51 修复的真实缺陷: 重复提交同一份答案时, 答案日志与评估是
        # 幂等的 (按 answer_id 去重), 但"学生学习日志 + planner 快照"此前
        # **无条件**再记一次 —— 于是同一份答案提交 3 次会得到 3 条 ANSWERED
        # 事件、correct_count = 3、practice_counts = 3。那既违反规范 55.8 的
        # "same operation 重复 1x/2x/3x 必须保持正确", 也让"重启后从已落盘
        # 的作答重建快照"必然与内存里的数字对不上 (重建只能看到 1 份作答)。
        is_new_answer = answer.answer_id not in self._evaluation_by_answer

        # 1) 评估 (幂等: 相同答案 ID 返回同一评估结果, 按 answer_id 索引)
        #
        # 关键: ``_answer_log`` 是**追加式**历史, 只能刷新它的 evaluator,
        # 绝不可整体重建 —— 重建会让每次提交都丢掉之前所有作答与评估。
        # ``ExactEvaluator`` 会快照练习目录, 因此这里每次提交都用当前
        # 目录构造新 evaluator 再注入, 既保证新练习可被评估, 又保住历史。
        self._answer_log.set_evaluator(ExactEvaluator(self._exercises))
        self._answer_log.record_answer(answer)
        result = self._answer_log.evaluate(answer)
        self._evaluation_by_answer[answer.answer_id] = result

        # 2) 学生学习日志登记 (每个相关且已注册的 KP)
        log = self._student_logs[sid]
        is_correct = result.status is EvaluationStatus.CORRECT
        is_incorrect = result.status is EvaluationStatus.INCORRECT
        if is_new_answer and (is_correct or is_incorrect):
            for kp_id in exercise.knowledge_point_ids:
                key = (self._course_id, kp_id)
                if key not in log.registered_knowledge_points:
                    log.register_knowledge_point(self._course_id, kp_id)
                log.record_answer(
                    self._course_id, kp_id, is_correct=is_correct
                )

        # 3) planner 快照 (只在新的作答上计数 -> 与 _rebuild_planner_snapshot 一致)
        if is_new_answer:
            for kp_id in exercise.knowledge_point_ids:
                self._practice_counts[kp_id] = self._practice_counts.get(kp_id, 0) + 1
                if is_incorrect:
                    self._recent_incorrect.add(kp_id)

        # 4) 提交时间 (运行时元数据; 重复提交保留首次时间 -> 幂等)
        self._submitted_at.setdefault(answer.answer_id, self._clock())

        out = _answer_to_dict(answer)
        out["evaluation_id"] = result.evaluation_id
        out["evaluation_status"] = result.status.value
        out["submitted_at"] = self._submitted_at[answer.answer_id]
        return out

    def get_evaluation(
        self,
        answer_id: str,
    ) -> dict[str, Any]:
        """获取指定答案的评估结果 DTO (参数为 answer_id)。"""
        aid = _require_nonempty_str(answer_id, "answer_id")
        result = self._evaluation_by_answer.get(aid)
        if result is None:
            raise NotFoundError(
                f"evaluation for answer {aid!r} not found "
                "(submit the answer first)"
            )
        return _evaluation_to_dict(result)

    # ------------------------------------------------------------------
    # 学习
    # ------------------------------------------------------------------

    def _prerequisites(self) -> Dict[str, Tuple[str, ...]]:
        """``dependent -> (prerequisite kps,)``, 来自装配层注入的显式关系。

        只使用 PREREQUISITE 关系 (Task 33 规则: RELATED / CONTRASTS /
        EXTENDS 永不算前置)。提供者缺失或抛错时退化为空图 —— 学习计划
        与路径仍可用, 只是不含前置链; 绝不因此让请求失败。
        """
        provider = self._prerequisite_provider
        if provider is None:
            return {}
        try:
            raw = provider() or {}
        except Exception:  # noqa: BLE001 - 关系层不可用不应让学习层崩掉
            return {}
        out: Dict[str, Tuple[str, ...]] = {}
        for dependent, prereqs in dict(raw).items():
            if isinstance(prereqs, str):
                candidates = (prereqs,)
            else:
                candidates = tuple(prereqs or ())
            deps = tuple(sorted({str(p) for p in candidates if str(p).strip()}))
            if deps:
                out[str(dependent)] = deps
        return out

    def _planner(self) -> StudyPlanner:
        """基于当前快照构建确定性 StudyPlanner。

        课程 KP 集合 = 该课程已注册 KP ∪ 练习关联 KP (确定性并集)。
        """
        registered_kps: Set[str] = set()
        for log in self._student_logs.values():
            for key in log.registered_knowledge_points:
                course_id, kp_id = key
                if course_id == self._course_id:
                    registered_kps.add(kp_id)
        course_kps = {
            kp: None
            for kp in sorted(
                self._course_knowledge_points
                | registered_kps
                | {
                    kp_id
                    for e in self._exercises.values()
                    for kp_id in e.knowledge_point_ids
                }
            )
        }
        return StudyPlanner(
            self._course_id,
            course_kps,
            self._prerequisites(),
            practice_counts=dict(self._practice_counts),
            recent_incorrect_kps=sorted(self._recent_incorrect),
        )

    def get_study_plan(self, student_id: str) -> dict[str, Any]:
        """返回指定学生的当前学习计划 DTO (基于作答快照 + 课程 KP)。"""
        sid = _require_nonempty_str(student_id, "student_id")
        if sid not in self._students:
            raise NotFoundError(f"student {sid!r} not found")
        planner = self._planner()
        plan = planner.build_plan(sid)
        return plan.to_dict()

    def get_learning_path(self, target_knowledge_point_id: str) -> dict[str, Any]:
        """返回指定目标知识点的前置学习路径 DTO。"""
        target = _require_nonempty_str(
            target_knowledge_point_id, "target_knowledge_point_id"
        )
        planner = self._planner()
        path: LearningPath = planner.build_learning_path(target)
        return path.to_dict()

    def get_learning_status(self, student_id: str) -> dict[str, Any]:
        """返回指定学生的整体学习状态概览。"""
        sid = _require_nonempty_str(student_id, "student_id")
        state = self.get_student_state(sid)
        exercises = self.list_exercises()
        registered = set(state["registered_knowledge_points"])
        # 该学生实际提交的答案: 按 EvaluationResult 集合 (同一答案重提幂等)
        answered_ids: Set[str] = {r.answer_id for r in self._answer_log.results_for(sid)}
        return {
            "student_id": sid,
            "course_id": self._course_id,
            "registered_knowledge_points": state["registered_knowledge_points"],
            "states": state["states"],
            "exercise_count": len(exercises),
            "answered_count": len(answered_ids),
            "practice_counts": {
                kp: self._practice_counts[kp]
                for kp in sorted(registered & set(self._practice_counts))
            },
            "recent_incorrect_kps": sorted(
                self._recent_incorrect & registered
            ),
        }

    def _all_students(self) -> Dict[str, Student]:
        return self._students

    # ------------------------------------------------------------------
    # 只读查询 (Task 40/41 UI 需要)
    # ------------------------------------------------------------------

    def answer_log_for(self, student_id: str) -> List[dict[str, Any]]:
        """该学生的全部答案 DTO, 顺序 = 提交顺序 (确定性, 无时间戳参与)。

        领域层 ``StudentAnswer`` 不带时间戳; 顺序由 ``AnswerEvaluationLog``
        的插入顺序给出, 因此同一进程内可复现。跨进程顺序由 Task 42 的持久化
        层决定。
        """
        sid = _require_nonempty_str(student_id, "student_id")
        if sid not in self._students:
            raise NotFoundError(f"student {sid!r} not found")
        return [
            {
                **_answer_to_dict(answer),
                "submitted_at": self._submitted_at.get(answer.answer_id),
            }
            for answer in self._answer_log.answers_for(sid)
        ]

    def submitted_at_for(self, answer_id: str) -> Optional[str]:
        """答案的运行时提交时间; 未提交过则 None (不臆造时间)。"""
        if not isinstance(answer_id, str):
            return None
        return self._submitted_at.get(answer_id)

    # ------------------------------------------------------------------
    # 学习事件 (Task 40 UI 动作; 使用 Task 30 已定义的事件与转移规则)
    # ------------------------------------------------------------------

    #: 与 Task 30 ``student_learning._TRANSITIONS`` 一致的状态机顺序。
    #: 只用于回答"UI 下一步该给哪个按钮", 不参与任何状态判定 ——
    #: 状态永远由 Task 30 的 ``derive_state`` 计算。
    #: tests/test_learning_view.py 里有一条测试把这张表与领域层的
    #: _TRANSITIONS 逐项比对, 防止漂移。
    NEXT_LEARNING_EVENT: Mapping[str, Optional[str]] = {
        "not_started": "viewed",
        "exposed": "practiced",
        "practicing": "reviewed",
        "reviewing": None,
    }

    def record_learning_event(
        self,
        student_id: str,
        knowledge_point_id: str,
        event_type: str,
    ) -> dict[str, Any]:
        """记录一个学习事件 (VIEWED / PRACTICED / REVIEWED)。

        这是**使用** Task 30 的规则, 不是重新实现: 事件类型与合法转移
        完全由 ``StudentLearningLog`` / ``reduce_events`` 决定。越序事件
        会被记录但不推进状态 (与领域层行为一致), 调用方看到的 state 就是
        领域层算出来的真实状态。
        """
        sid = _require_nonempty_str(student_id, "student_id")
        kp_id = _require_nonempty_str(knowledge_point_id, "knowledge_point_id")
        if sid not in self._students:
            raise NotFoundError(f"student {sid!r} not found")
        try:
            event = LearningEventType(str(event_type).strip().lower())
        except ValueError:
            raise InvalidInputError(
                "event_type must be one of "
                + ", ".join(sorted(item.value for item in LearningEventType))
            )
        log = self._student_logs[sid]
        key = (self._course_id, kp_id)
        if key not in log.registered_knowledge_points:
            # 学生首次接触该知识点: 登记是幂等的。
            log.register_knowledge_point(self._course_id, kp_id)
        log.record_event(self._course_id, kp_id, event)
        record = _state_record_to_dict(log.derive_state(self._course_id, kp_id))
        return {
            **record,
            "event_type": event.value,
            "next_event": self.next_learning_event(record["state"]),
        }

    @classmethod
    def next_learning_event(cls, state: str) -> Optional[str]:
        """给定 Task 30 状态, 返回下一个合法事件 (无则 None)。"""
        return cls.NEXT_LEARNING_EVENT.get(str(state), None)

    def _all_exercises(self) -> Dict[str, Exercise]:
        return self._exercises

    def _answer_log(self) -> AnswerEvaluationLog:
        return self._answer_log

    def answer_count(self) -> int:
        """本课程的作答总条数 (只读, O(1), 不跨课程)。

        Task 68 的多课程总览用它。刻意**不**提供"按学生展开再求和"的
        写法: 那种写法在 Task 69 的规模 (500 学生 / 20000 作答) 下是
        O(学生 × 作答)。
        """
        return self._answer_log.answer_count()

    def _practice_counts_snapshot(self) -> Dict[str, int]:
        return dict(self._practice_counts)

    def _recent_incorrect_snapshot(self) -> Set[str]:
        return set(self._recent_incorrect)
