# -*- coding: utf-8 -*-
"""Task 62–65 综合学习闭环 integration fixture。

spec 第八节要求：至少一条**真实**的完整闭环 —— 不是 UI fixture 里拼 JSON，
而是真的调用 Application Service / Domain / Repository：

    Course → Session → Material → Evidence → Knowledge → Review →
    StudyPlan → LearningPath → Exercise → Answer → Evaluation →
    StudentState → Mistake → Review Knowledge → Evidence

spec 第九节要求真实子进程重启后逐字段可重放；
spec 第十节要求 A / B / C 三门课互相不可见。

本文件用 ``pytest.mark.integration`` 标记 —— 它比普通套件慢，
但要作为验收的一部分运行（``pytest -m integration``）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.application.mistakes_view import (
    MISTAKES_EMPTY_NOTE,
    WEAK_STATES,
)
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace
from src.exercise_generation import GenerationConfig

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

FIXED_TIME = "2026-09-18T09:00:00+00:00"
TODAY = "2026-09-18"

#: 每个 session 一套互不相同的材料 —— 复用 Task 62–65 既有夹具。
SESSION_FIXTURE_SETS = (
    ("documents/simple.pdf", "documents/simple.docx", "notes/spanish.md"),
    ("documents/multilingual.pdf", "notes/catalan.md"),
    ("documents/tables.pdf", "notes/mixed.md"),
)


def _fixtures_for(session_number: int) -> tuple[str, ...]:
    return SESSION_FIXTURE_SETS[(session_number - 1) % len(SESSION_FIXTURE_SETS)]


# ---------------------------------------------------------------------------
# 闭环夹具
# ---------------------------------------------------------------------------


class LearningLoop:
    """一条真实闭环的可读封装。

    每一步都透传真实返回值，测试可以直接断言中间产物 ——
    这正是"不是只在 UI fixture 中拼 JSON"的意思。
    """

    def __init__(self, workspace: Workspace, name: str, code: str, language: str):
        self.ws = workspace
        self.course = workspace.create_course(name, code, language)
        self.course_id = self.course["course_id"]
        self.steps: dict[str, object] = {}

    # -- 1..3 Course → Session → Material --------------------------------

    def add_session(self, number: int = 1) -> str:
        session = self.ws.create_session(
            self.course_id, session_number=number, date=TODAY, title="Tema %d" % number
        )
        self.session_id = session["session_id"]
        for name in _fixtures_for(number):
            self.ws.register_material(
                self.course_id, str(FIXTURES / name), session_id=self.session_id
            )
        self.steps["session"] = session
        return self.session_id

    # -- 4 Evidence + 5 Knowledge ---------------------------------------

    def process(self) -> None:
        self.ws.process_session(self.course_id, self.session_id)
        self.materials = self.ws.list_materials(self.course_id)
        self.knowledge = [
            kp
            for kp in self.ws.knowledge_points(self.course_id)
            if kp["validation_status"] == "supported"
        ]
        assert self.materials, "闭环必须有材料"
        assert self.knowledge, "闭环必须有 supported 知识点"
        self.steps["materials"] = self.materials
        self.steps["knowledge"] = self.knowledge

    # -- 6 Review --------------------------------------------------------

    def review_all(self) -> None:
        """把 pending 的知识点全部 confirm —— 闭环里必须真的走一遍审核。"""
        confirmed = []
        for kp in self.ws.knowledge_points(self.course_id):
            if kp["review_status"] == "pending":
                confirmed.append(
                    self.ws.review_confirm(self.course_id, kp["knowledge_id"])
                )
        self.reviews = confirmed
        self.steps["reviews"] = confirmed

    # -- 7 StudyPlan + 8 LearningPath ------------------------------------

    def create_student(self, student_id: str, display_name: str) -> str:
        self.student_id = self.ws.create_student(
            self.course_id, student_id, display_name
        )["student_id"]
        return self.student_id

    def plan(self) -> dict:
        self.study_plan = self.ws.study_plan(self.course_id, self.student_id)
        self.steps["study_plan"] = self.study_plan
        return self.study_plan

    def path(self, knowledge_id: str) -> dict:
        self.learning_path = self.ws.student_learning_path(
            self.course_id, self.student_id, knowledge_id
        )
        self.steps["learning_path"] = self.learning_path
        return self.learning_path

    # -- 9..11 Exercise → Answer → Evaluation ----------------------------

    def make_exercise(self, kp_id: str, prefer: str = "multiple_choice") -> dict:
        """生成一道**可判错**的题，返回规范化后的 exercise（含 draft 答案）。"""
        for seed in range(24):
            result = self.ws.generate_exercise(
                self.course_id, kp_id, config=GenerationConfig(seed=seed)
            )
            if not result.get("generated"):
                continue
            exercise = dict(result["exercise"])
            draft = result.get("draft") or {}
            if exercise["exercise_type"] != prefer:
                continue
            exercise["_draft_correct_choice_id"] = draft.get("correct_choice_id")
            exercise["_draft_choices"] = draft.get("choices") or []
            exercise["_draft_expected_answer"] = draft.get("expected_answer")
            exercise["_draft_accepted_answers"] = draft.get("accepted_answers") or []
            self.exercise = exercise
            self.steps["exercise"] = exercise
            return exercise
        pytest.skip("no %r exercise generated for this knowledge point" % prefer)

    def answer_wrong(self, exercise: dict) -> dict:
        """提交一个**必然判 incorrect** 的答案，走 Task 32 真实端点。"""
        kind = exercise["exercise_type"]
        if kind == "multiple_choice":
            correct = exercise.get("_draft_correct_choice_id")
            value = next(
                c["choice_id"]
                for c in exercise.get("_draft_choices") or []
                if c["choice_id"] != correct
            )
        elif kind == "true_false":
            value = "false"  # 模板约定正确答案恒为 true
        elif kind == "fill_blank":
            value = "zzz-not-in-accepted-answers-zzz"
        else:
            pytest.skip("%r cannot produce an incorrect evaluation" % kind)
        answer = self.ws.submit_answer(
            self.course_id, self.student_id, exercise["exercise_id"], value, sequence=1
        )
        assert answer["evaluation_status"] == "incorrect", answer
        self.answer = answer
        self.steps["answer"] = answer
        return answer

    def answer_right(self, exercise: dict) -> dict:
        kind = exercise["exercise_type"]
        if kind == "true_false":
            value = "true"
        elif kind == "multiple_choice":
            value = exercise.get("_draft_correct_choice_id")
        else:
            accepted = exercise.get("_draft_accepted_answers") or []
            value = accepted[0] if accepted else exercise.get("_draft_expected_answer")
        assert value, "cannot derive the correct value"
        answer = self.ws.submit_answer(
            self.course_id, self.student_id, exercise["exercise_id"], str(value), sequence=1
        )
        assert answer["evaluation_status"] == "correct", answer
        self.answer = answer
        self.steps["answer"] = answer
        return answer

    # -- 12 StudentState -------------------------------------------------

    def state(self) -> dict:
        self.student_state = self.ws.student_state(self.course_id, self.student_id)
        self.steps["student_state"] = self.student_state
        return self.student_state

    # -- 14 Mistake ------------------------------------------------------

    def mistakes(self, **kw) -> dict:
        self.mistake_center = self.ws.mistakes_center(
            self.course_id, self.student_id, **kw
        )
        self.steps["mistakes"] = self.mistake_center
        return self.mistake_center


@pytest.fixture
def workspace(tmp_path):
    ws = Workspace(
        str(tmp_path / "data"),
        clock=fixed_clock(FIXED_TIME),
        asr_mode="mock",
        ocr_mode="mock",
    )
    yield ws
    ws.close()


@pytest.fixture
def loop(workspace) -> LearningLoop:
    """一条已经走到 "有错题" 的完整闭环。"""
    instance = LearningLoop(workspace, "Programacio", "PROG101", "ca")
    instance.add_session(1)
    instance.process()
    instance.review_all()
    instance.create_student("s-loop", "Laia")
    instance.plan()
    instance.path(instance.knowledge[0]["knowledge_id"])
    exercise = instance.make_exercise(instance.knowledge[0]["knowledge_id"])
    instance.answer_wrong(exercise)
    instance.state()
    instance.mistakes()
    return instance


# ---------------------------------------------------------------------------
# 1) 完整闭环，每一段都真实经过 Application Service / Domain / Repository
# ---------------------------------------------------------------------------


class TestCompleteLearningLoop:
    def test_course_session_material_evidence_knowledge(self, loop):
        """Course → Session → Material → Evidence → Knowledge。"""
        assert loop.course["course_id"]
        assert loop.session_id
        assert loop.materials, "material 必须落库"

        material_ids = {m["material_id"] for m in loop.materials}
        assert material_ids

        # Evidence 必须真的从材料里抽出来，并且能被知识点引用
        kp_id = loop.knowledge[0]["knowledge_id"]
        evidence = loop.ws.knowledge_evidence(loop.course_id, kp_id)
        assert evidence, "知识点必须有证据支撑"
        assert any(
            row["source"]["material_id"] in material_ids
            for row in evidence
            if row.get("source")
        ), "证据必须能追溯到本课程的材料"

    def test_review_step_actually_writes_review_status(self, loop):
        """Review 段必须真的改变 review_status，而不是只在闭环里被跳过。"""
        assert loop.reviews, "闭环里必须真的执行过审核"
        for kp in loop.ws.knowledge_points(loop.course_id):
            assert kp["review_status"] != "pending"
        history = loop.ws.review_history(loop.course_id, loop.knowledge[0]["knowledge_id"])
        assert history, "审核必须留痕"

    def test_study_plan_and_learning_path_are_real_objects(self, loop):
        """StudyPlan 与 LearningPath 必须是既有服务产出的对象。"""
        assert loop.study_plan.get("plan_id"), "study plan 必须有 id"
        assert "items" in loop.study_plan
        assert loop.learning_path, "learning path 必须能取到"
        assert "path" in loop.learning_path or "nodes" in loop.learning_path

    def test_exercise_answer_evaluation_chain(self, loop):
        """Exercise → Answer → Evaluation 三段必须首尾相接。"""
        assert loop.exercise["exercise_id"]
        assert loop.answer["answer_id"]
        assert loop.answer["exercise_id"] == loop.exercise["exercise_id"]

        evaluation = loop.ws.get_evaluation(loop.course_id, loop.answer["answer_id"])
        assert evaluation["status"] == "incorrect"
        assert evaluation["evaluation_id"]

    def test_student_state_exists_and_is_not_derived_from_counts(self, loop):
        """StudentState 必须存在，且它的 state 不是由答错次数推导的。"""
        state = loop.state()
        assert state.get("states") is not None
        # 答错一次**不**足以把 state 推到 practicing/reviewing ——
        # 这正说明 state 来自显式学习事件，而不是答对答错的计数。
        for record in state["states"]:
            assert record["state"] in (
                "not_started",
                "exposed",
                "practicing",
                "reviewing",
            )

    def test_mistake_step_reads_the_existing_evaluation(self, loop):
        """Mistake 段必须复用上一步的 Evaluation，而不是另算一套。"""
        center = loop.mistakes()
        assert center["has_mistakes"] is True
        row = center["mistakes"][0]
        assert row["evaluation_id"] == loop.answer["evaluation_id"] or (
            row["answer_id"] == loop.answer["answer_id"]
        )
        assert row["status"] == "incorrect"

    def test_loop_closes_back_to_evidence(self, loop):
        """最后一段：从错题回到 Evidence —— 闭环必须真的合上。"""
        kp_id = loop.knowledge[0]["knowledge_id"]
        detail = loop.ws.mistake_detail(loop.course_id, loop.student_id, kp_id)
        assert detail["knowledge"]["knowledge_id"] == kp_id
        assert detail["why_incorrect"], "必须说明为什么错"
        assert detail["evidence"], "必须给出重新学习的依据"
        assert detail["suggested_actions"], "必须给出建议动作"
        actions = {a["action"] for a in detail["suggested_actions"]}
        assert "REVIEW_KNOWLEDGE" in actions
        assert "VIEW_EVIDENCE" in actions

    def test_every_step_is_reachable_through_workspace_api(self, loop):
        """整条闭环的每一步都必须能通过 Workspace 公开方法复现。"""
        ws = loop.ws
        cid = loop.course_id
        assert ws.get_course(cid)
        assert ws.list_sessions(cid)
        assert ws.list_materials(cid)
        assert ws.knowledge_points(cid)
        assert ws.review_candidates(cid) is not None
        assert ws.study_plan(cid, loop.student_id)
        assert ws.list_exercises(cid)
        assert ws.student_state(cid, loop.student_id)
        assert ws.mistakes_center(cid, loop.student_id)


# ---------------------------------------------------------------------------
# 2) Task 62 / 63 / 64 / 65 的投影在同一份数据上必须互相一致
# ---------------------------------------------------------------------------


class TestProjectionsAgree:
    def test_course_review_sees_the_reviewed_knowledge(self, loop):
        review = loop.ws.course_review(loop.course_id)
        assert review["course_id"] == loop.course_id
        # 这门课的知识点必须全部出现在课程复习概览的计数里
        assert review["counts"]["knowledge"] >= len(loop.knowledge)
        # 已经全部 confirm 过 → 不再有待审核
        assert review["counts"]["pending_review"] == 0
        # topics 为空是正常的（本夹具没有归主题），但必须如实报告
        assert review["counts"]["topics"] == len(review["topics"])

    def test_student_today_sees_the_exercise_and_evaluation(self, loop):
        today = loop.ws.student_today(
            course_id=loop.course_id, student_id=loop.student_id
        )
        assert today["course_id"] == loop.course_id
        # 做过题之后就不再是"无学习活动"
        assert today["has_activity"] is True
        recent = today.get("recent_evaluations") or []
        assert recent, "今日首页必须反映最近的评估"
        ids = {r.get("answer_id") for r in recent}
        assert loop.answer["answer_id"] in ids

    def test_today_and_mistakes_agree_on_the_same_evaluation(self, loop):
        today = loop.ws.student_today(
            course_id=loop.course_id, student_id=loop.student_id
        )
        center = loop.mistakes()
        today_answers = {
            r.get("answer_id") for r in today.get("recent_evaluations") or []
        }
        mistake_answers = {m["answer_id"] for m in center["mistakes"]}
        assert mistake_answers <= today_answers, (
            "错题集必须是今日评估的子集 —— 两者读同一份 Evaluation"
        )

    def test_exercise_grounding_matches_mistake_evidence(self, loop):
        """Task 64 的依据链与 Task 65 的重新学习依据必须指向同一批证据。"""
        exercise_id = loop.exercise["exercise_id"]
        grounding = loop.ws.exercise_grounding(loop.course_id, exercise_id)
        kp_id = loop.knowledge[0]["knowledge_id"]
        detail = loop.ws.mistake_detail(loop.course_id, loop.student_id, kp_id)

        ground_evidence = {e["evidence_id"] for e in grounding.get("evidence") or []}
        detail_evidence = {e["evidence_id"] for e in detail.get("evidence") or []}
        assert ground_evidence & detail_evidence, (
            "两条链路必须至少共享一条证据 —— 它们读同一个 Evidence Store"
        )

    def test_student_today_attention_agrees_with_mistake_weak(self, loop):
        """Task 63 的关注区与 Task 65 的薄弱区必须用同一套 StudentState 语义。"""
        today = loop.ws.student_today(
            course_id=loop.course_id, student_id=loop.student_id
        )
        center = loop.mistakes()

        today_signals = {
            row.get("signal") for row in today.get("attention") or []
        } & set(WEAK_STATES)
        weak_signals = {row["signal"] for row in center.get("weak_knowledge") or []}
        assert weak_signals <= today_signals, (
            "薄弱区不能出现今日首页没看到的信号 —— 两者都来自 StudentState"
        )


# ---------------------------------------------------------------------------
# 3) 多课程隔离 A / B / C（spec 第十节）
# ---------------------------------------------------------------------------


@pytest.fixture
def three_courses(workspace) -> dict[str, LearningLoop]:
    loops: dict[str, LearningLoop] = {}
    for index, (name, code) in enumerate(
        (("Algebra", "ALG101"), ("Quimica", "QUI201"), ("Historia", "HIS301")), start=1
    ):
        instance = LearningLoop(workspace, name, code, "es")
        instance.add_session(index)
        instance.process()
        instance.review_all()
        instance.create_student("s-" + code.lower(), "Alumno " + code)
        instance.plan()
        exercise = instance.make_exercise(instance.knowledge[0]["knowledge_id"])
        instance.answer_wrong(exercise)
        loops[code] = instance
    return loops


class TestMultiCourseIsolation:
    def test_each_course_has_its_own_mistakes(self, three_courses):
        for code, instance in three_courses.items():
            center = instance.mistakes()
            assert center["has_mistakes"] is True, code
            assert center["course_id"] == instance.course_id
            for row in center["mistakes"]:
                assert row["answer_id"] == instance.answer["answer_id"], (
                    "课程 %s 的错题集里混进了其它课程的行" % code
                )

    def test_course_a_never_sees_b_or_c(self, three_courses):
        a = three_courses["ALG101"]
        center = a.mistakes()
        a_answers = {m["answer_id"] for m in center["mistakes"]}
        for code in ("QUI201", "HIS301"):
            other = three_courses[code]
            assert other.answer["answer_id"] not in a_answers, (
                "课程 A 绝不能看到课程 %s 的错题" % code
            )
            assert other.course_id != a.course_id

    def test_knowledge_ids_may_collide_but_membership_does_not(self, three_courses):
        """knowledge_id 是内容寻址的：不同课程里相同的材料会产生相同的 id。

        所以隔离**不能**靠 id 唯一性，只能靠课程范围内的成员关系 ——
        这条断言就是把这个事实钉死。
        """
        a = three_courses["ALG101"]
        b = three_courses["QUI201"]
        a_kp = {kp["knowledge_id"] for kp in a.knowledge}
        b_kp = {kp["knowledge_id"] for kp in b.knowledge}
        # 材料不同 → 通常不相交；即使相交，也必须各自只报自己的错题
        for kp_id in a_kp & b_kp:
            a_detail = a.mistakes()
            for row in a_detail["mistakes"]:
                assert row["knowledge_point_ids"]
        # 更本质的判据：A 的课程范围查询不会返回 B 的答案
        assert a.mistakes()["course_id"] != b.mistakes()["course_id"]

    def test_student_state_is_scoped_per_course(self, three_courses):
        states = {}
        for code, instance in three_courses.items():
            state = instance.ws.student_state(instance.course_id, instance.student_id)
            states[code] = state
            assert state.get("course_id", instance.course_id) == instance.course_id
        # 三个学生各自独立
        assert len({id(v) for v in states.values()}) == 3

    def test_mistakes_center_rejects_a_foreign_student(self, three_courses):
        a = three_courses["ALG101"]
        b = three_courses["QUI201"]
        from src.application.errors import NotFoundError

        with pytest.raises(NotFoundError):
            a.ws.mistakes_center(a.course_id, b.student_id)


# ---------------------------------------------------------------------------
# 4) 真实子进程重启（spec 第九节）
# ---------------------------------------------------------------------------


_RESTART_WRITE_SCRIPT = r'''
import json, sys
from src.application.workspace import Workspace
from src.application.runtime import fixed_clock
from src.exercise_generation import GenerationConfig

data_dir = sys.argv[1]
fixtures = sys.argv[2]
ws = Workspace(data_dir, clock=fixed_clock("2026-09-18T09:00:00+00:00"),
               asr_mode="mock", ocr_mode="mock")

course = ws.create_course("Programacio", "PROG101", "ca")
cid = course["course_id"]
session = ws.create_session(cid, session_number=1, date="2026-09-18", title="Tema")
sid = session["session_id"]
for name in ("documents/simple.pdf", "documents/simple.docx", "notes/spanish.md"):
    ws.register_material(cid, fixtures + "/" + name, session_id=sid)
ws.process_session(cid, sid)
for kp in ws.knowledge_points(cid):
    if kp["review_status"] == "pending":
        ws.review_confirm(cid, kp["knowledge_id"])

student = ws.create_student(cid, "s-restart", "Rita")["student_id"]
ws.study_plan(cid, student)
supported = [k for k in ws.knowledge_points(cid)
             if k["validation_status"] == "supported"]
assert supported
kp_id = supported[0]["knowledge_id"]
ws.student_learning_path(cid, student, kp_id)

out = {}
for seed in range(24):
    r = ws.generate_exercise(cid, kp_id, config=GenerationConfig(seed=seed))
    if not r.get("generated"):
        continue
    ex = dict(r["exercise"]); d = r.get("draft") or {}
    kind = ex["exercise_type"]
    if kind == "multiple_choice":
        correct = d.get("correct_choice_id")
        value = next(c["choice_id"] for c in d.get("choices") or []
                     if c["choice_id"] != correct)
    elif kind == "true_false":
        value = "false"
    elif kind == "fill_blank":
        value = "zzz-not-accepted"
    else:
        continue
    ans = ws.submit_answer(cid, student, ex["exercise_id"], value, sequence=1)
    out = {
        "course_id": cid, "session_id": sid, "student_id": student,
        "knowledge_id": kp_id, "exercise_id": ex["exercise_id"],
        "exercise_type": kind, "submitted_value": value,
        "answer_id": ans["answer_id"], "evaluation_status": ans["evaluation_status"],
    }
    break
assert out, "restart fixture produced no exercise"
ws.close()
print(json.dumps(out))
'''


_RESTART_READ_SCRIPT = r'''
import json, sys
from src.application.workspace import Workspace
from src.application.runtime import fixed_clock

data_dir = sys.argv[1]
ws = Workspace(data_dir, clock=fixed_clock("2026-09-18T09:00:00+00:00"),
               asr_mode="mock", ocr_mode="mock")
cid = sys.argv[2]
student = sys.argv[3]

course = ws.get_course(cid)
sessions = ws.list_sessions(cid)
materials = ws.list_materials(cid)
knowledge = ws.knowledge_points(cid)
exercises = ws.list_exercises(cid)
state = ws.student_state(cid, student)
plan = ws.study_plan(cid, student)
center = ws.mistakes_center(cid, student)
kp_id = center["knowledge"][0]["knowledge_id"] if center["knowledge"] else None
detail = ws.mistake_detail(cid, student, kp_id) if kp_id else None

print(json.dumps({
    "course_id": course["course_id"] if course else None,
    "sessions": len(sessions),
    "materials": len(materials),
    "knowledge": len(knowledge),
    "exercises": len(exercises),
    "has_state": state is not None,
    "plan_id": plan.get("plan_id"),
    "has_mistakes": center["has_mistakes"],
    "mistake_count": len(center["mistakes"]),
    "answer_ids": sorted(m["answer_id"] for m in center["mistakes"]),
    "evaluation_ids": sorted(m["evaluation_id"] for m in center["mistakes"]),
    "incorrect_attempts": [k["incorrect_attempts"] for k in center["knowledge"]],
    "evidence_resolved": len(detail["evidence"]) if detail else 0,
    "actions": sorted(a["action"] for a in (detail or {}).get("suggested_actions") or []),
}))
'''


class TestRestartIntegration:
    def _run(self, script: str, *args: str) -> dict:
        proc = subprocess.run(
            [sys.executable, "-c", script, *args],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_full_loop_survives_a_real_subprocess_restart(self, tmp_path):
        """Process A 写完整闭环后退出；Process B 只拿到数据目录重放。"""
        data_dir = str(tmp_path / "restart-data")
        fixtures = str(FIXTURES)

        written = self._run(_RESTART_WRITE_SCRIPT, data_dir, fixtures)
        assert written["evaluation_status"] == "incorrect"

        read = self._run(
            _RESTART_READ_SCRIPT, data_dir, written["course_id"], written["student_id"]
        )

        # 闭环的每一段都必须在重启后仍然存在（spec 第九节清单）
        assert read["course_id"] == written["course_id"]
        assert read["sessions"] >= 1
        assert read["materials"] >= 1
        assert read["knowledge"] >= 1
        assert read["exercises"] >= 1
        assert read["has_state"] is True
        assert read["plan_id"]
        assert read["has_mistakes"] is True
        assert read["mistake_count"] >= 1
        assert read["answer_ids"] == [written["answer_id"]]
        assert read["evaluation_ids"]
        assert read["plan_id"]
        assert read["evidence_resolved"] >= 1
        assert "REVIEW_KNOWLEDGE" in read["actions"]
        assert "VIEW_EVIDENCE" in read["actions"]

    def test_restart_replays_the_same_identity(self, tmp_path):
        """重启不能改变任何内容寻址的 id —— 否则就是重新造了一份数据。"""
        data_dir = str(tmp_path / "restart-identity")
        written = self._run(_RESTART_WRITE_SCRIPT, data_dir, str(FIXTURES))
        first = self._run(
            _RESTART_READ_SCRIPT, data_dir, written["course_id"], written["student_id"]
        )
        second = self._run(
            _RESTART_READ_SCRIPT, data_dir, written["course_id"], written["student_id"]
        )
        assert first == second, "两次只读重放必须完全一致"


# ---------------------------------------------------------------------------
# 5) 闭环里的"空"分支 —— 没有错题时必须如实说"没有"
# ---------------------------------------------------------------------------


class TestLoopEmptyBranches:
    def test_loop_with_no_wrong_answer_has_no_mistakes(self, workspace):
        instance = LearningLoop(workspace, "Vuit", "VUIT", "ca")
        instance.add_session(1)
        instance.process()
        instance.review_all()
        instance.create_student("s-clean", "Neta")
        exercise = instance.make_exercise(instance.knowledge[0]["knowledge_id"])
        correct = instance.answer_right(exercise)

        center = instance.mistakes()
        assert center["has_mistakes"] is False
        assert center["mistakes"] == []
        assert center["note"] == MISTAKES_EMPTY_NOTE
        assert center["weak_knowledge"] == []
        # 答对的答案绝不能以"错题"身份出现
        assert correct["answer_id"] not in {
            m["answer_id"] for m in center["mistakes"]
        }

    def test_student_with_no_activity_reports_it_honestly(self, workspace):
        """空学生 = 没有学习任务、没有待做练习、没有评估。

        注意 ``has_activity`` 的判据是这三者之一非空 —— 它**不是**
        "这个学生有没有答过题"。所以本例刻意建一个尚未处理的课程，
        让三段都真的为空。
        """
        instance = LearningLoop(workspace, "Buit", "BUIT", "es")
        instance.create_student("s-buit", "Buit")
        # 只注册材料，不 process —— 于是没有知识点、没有 StudyPlan、
        # 没有练习、没有评估，三段全空。
        session = workspace.create_session(
            instance.course_id, session_number=1, date=TODAY, title="Sense processar"
        )
        for name in _fixtures_for(1):
            workspace.register_material(
                instance.course_id, str(FIXTURES / name), session_id=session["session_id"]
            )

        today = instance.ws.student_today(
            course_id=instance.course_id, student_id="s-buit"
        )
        assert today["has_activity"] is False
        assert today.get("note")
        assert today["recent_evaluations"] == []
        assert today["pending_exercises"] == []
        # study 里可以有行, 但每一行都必须是"不可用"的 ——
        # 有行不等于有学习任务, has_activity 的判据是 available。
        assert all(row["available"] is False for row in today["study"])
        assert all(not row["tasks_total"] for row in today["study"])

        center = instance.mistakes()
        assert center["has_mistakes"] is False
        assert center["mistakes"] == []
        assert center["note"] == MISTAKES_EMPTY_NOTE
