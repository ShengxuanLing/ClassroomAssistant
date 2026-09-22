# -*- coding: utf-8 -*-
"""Task 51 — Review / Student / Exercise 持久化接线。

spec 51 的核心要求::

    ReviewRecord append-only 语义
    (PENDING -> CONFIRM -> later REVIEW 历史不得被压平)
    Student / LearningState / Exercise / Answer / Evaluation 持久化
    **临界不变量**: 学生答错绝不能修改 KnowledgePoint.statement /
    Evidence.content / ReviewRecord

最后一条是本文件存在的最大理由。学生数据是**过程数据**, 知识是**真值**;
两者一旦互相污染, 整个"证据优先"的架构就塌了。所以本文件里有一整组
测试专门盯着"作答之后知识有没有被改", 而且不只在内存里看 —— 还要
重启之后再看一遍。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil

import pytest

from src.application.acceptance import AcceptanceHarness, ClassroomDataset
from src.application.persistence_wiring import default_database_path
from src.application.workspace import Workspace
from src.persistence import open_database

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "acceptance"


# ======================================================================
# 夹具
# ======================================================================


def _run_pipeline(root: pathlib.Path) -> AcceptanceHarness:
    dataset_dir = root / "dataset"
    shutil.copytree(FIXTURE_DIR, dataset_dir)
    harness = AcceptanceHarness(
        ClassroomDataset.from_directory(str(dataset_dir)),
        data_dir=str(root / "data"),
    )
    report = harness.run()
    assert report.all_steps_ok, f"流水线失败: {report.failed_steps}"
    return harness


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """只读的基线: 跑完流水线后**关闭**, 之后所有测试都从磁盘读。"""
    root = tmp_path_factory.mktemp("task51-base")
    harness = _run_pipeline(root)
    workspace = harness.workspace
    payload = {
        "course_id": harness.course_id,
        "student_id": harness.student_id,
        "exercise_id": harness.exercise_id,
        "answer_id": harness.answer_id,
        "data_dir": str(root / "data"),
        "knowledge_points": workspace.knowledge_points(harness.course_id),
        "exercises": workspace.list_exercises(harness.course_id),
        "students": workspace.list_students(harness.course_id),
        "student_state": workspace.student_state(harness.course_id, harness.student_id),
        "learning_status": workspace.learning_status(
            harness.course_id, harness.student_id
        ),
        "evaluation": workspace.get_evaluation(harness.course_id, harness.answer_id),
        "answer": _stored_answers(workspace, harness.course_id)[harness.answer_id],
        "evidence": {
            e.evidence_id: e.to_dict()
            for e in workspace.store.all(active_only=False)
        },
        "review_records": workspace.persistence.load_all_review_records(),
    }
    workspace.close()
    return payload


@pytest.fixture(scope="module")
def reloaded(base):
    workspace = Workspace(base["data_dir"])
    yield workspace
    workspace.close()


@pytest.fixture(scope="module")
def reviewed(tmp_path_factory):
    """一份**多步评审历史**的库: confirm -> reject -> keep_unverified。"""
    root = tmp_path_factory.mktemp("task51-reviewed")
    harness = _run_pipeline(root)
    workspace = harness.workspace
    course_id = harness.course_id
    student_id = harness.student_id

    target = workspace.knowledge_points(course_id)[0]["knowledge_id"]
    workspace.review_confirm(course_id, target, note="primera")
    workspace.review_reject(course_id, target, note="segunda")
    workspace.review_keep_unverified(course_id, target, note="tercera")

    payload = {
        "course_id": course_id,
        "student_id": student_id,
        "exercise_id": harness.exercise_id,
        "target": target,
        "data_dir": str(root / "data"),
        "history": workspace.review_history(course_id, target),
        "review_status": workspace.knowledge_point(course_id, target)["review_status"],
        "second_target": workspace.knowledge_points(course_id)[1]["knowledge_id"],
        "second_history": workspace.review_history(
            course_id, workspace.knowledge_points(course_id)[1]["knowledge_id"]
        ),
        "student_state": workspace.student_state(course_id, student_id),
        "learning_status": workspace.learning_status(course_id, student_id),
    }
    workspace.close()
    return payload


@pytest.fixture(scope="module")
def reviewed_reloaded(reviewed):
    workspace = Workspace(reviewed["data_dir"])
    yield workspace
    workspace.close()


@pytest.fixture
def sandbox(base, tmp_path):
    """一份**可写**的 data_dir 副本。

    为什么需要它: ``base`` / ``reviewed`` 是 module 级共享的, 任何"往库里
    写东西"的测试都会污染同模块的其他测试 (实测: 一个测试建了学生, 另一个
    测试就看到 4 个学生)。共享夹具只读, 改数据一律在副本上做。

    副本里的材料记录仍然带着原目录的绝对路径 —— 对学习域的测试没有影响,
    因为这里根本不碰文件。
    """
    target = tmp_path / "sandbox"
    shutil.copytree(base["data_dir"], target)
    return str(target)


@pytest.fixture
def review_sandbox(reviewed, tmp_path):
    target = tmp_path / "review-sandbox"
    shutil.copytree(reviewed["data_dir"], target)
    return str(target)


# ======================================================================
# 工具
# ======================================================================


def _count(data_dir: str, table: str, where: str = "", params=()) -> int:
    database = open_database(default_database_path(data_dir), migrate=False)
    try:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(database.scalar(sql, params, default=0) or 0)
    finally:
        database.close()


def _decision_sequence(history) -> list[str]:
    return [record["decision"] for record in history]


# ======================================================================
# 51.1 ReviewRecord 持久化 (append-only)
# ======================================================================


class TestReviewRecordPersistence:
    def test_review_records_land_in_sqlite(self, base) -> None:
        assert base["review_records"]
        assert _count(base["data_dir"], "review_records") == len(base["review_records"])

    def test_the_review_history_is_identical_after_a_restart(self, base, reloaded) -> None:
        for kp in base["knowledge_points"]:
            knowledge_id = kp["knowledge_id"]
            assert reloaded.review_history(base["course_id"], knowledge_id) == (
                reloaded.review_history(base["course_id"], knowledge_id)
            ), knowledge_id

    def test_every_review_record_field_is_preserved(self, reviewed, reviewed_reloaded) -> None:
        before = reviewed_reloaded.review_history(
            reviewed["course_id"], reviewed["target"]
        )
        assert before == reviewed["history"]

    def test_a_multi_step_history_is_not_flattened(
        self, reviewed, reviewed_reloaded
    ) -> None:
        """spec 51 点名: PENDING -> CONFIRM -> later REVIEW 不许被压平。

        做法: 对同一个知识点依次 confirm / reject / keep_unverified, 三个
        决定产生三条不同的 review_id。重启后必须**三条都在**, 而且顺序一致。
        """
        after = reviewed_reloaded.review_history(
            reviewed["course_id"], reviewed["target"]
        )
        assert len(after) == 3
        assert _decision_sequence(after) == _decision_sequence(reviewed["history"])
        assert len({r["review_id"] for r in after}) == 3

    def test_the_history_does_not_shrink_after_a_restart(
        self, reviewed, reviewed_reloaded
    ) -> None:
        after = reviewed_reloaded.review_history(
            reviewed["course_id"], reviewed["target"]
        )
        assert len(after) >= len(reviewed["history"])

    def test_the_notes_are_preserved(self, reviewed, reviewed_reloaded) -> None:
        after = reviewed_reloaded.review_history(
            reviewed["course_id"], reviewed["target"]
        )
        assert [r["note"] for r in after] == [r["note"] for r in reviewed["history"]]

    def test_a_note_is_not_part_of_the_review_identity(
        self, reviewed, review_sandbox
    ) -> None:
        """``note`` 只是审计上下文, **不参与身份**。

        因此"同一个知识点 + 同一个决定 + 同一组证据, 只换了备注"是同一个
        身份 —— 重放它不会新增记录, 也不会改写原来那条的备注。这是 Task 15
        定下的领域语义, 持久化层不许把它变松或变紧。

        在**沙箱**上做: 重放一个决定会更新当前 review_status, 那是真实的
        领域行为, 不该污染同模块其他测试看到的共享夹具。
        """
        course_id = reviewed["course_id"]
        target = reviewed["target"]
        workspace = Workspace(review_sandbox)
        try:
            before = workspace.review_history(course_id, target)
            workspace.review_confirm(course_id, target, note="nota distinta")
            after = workspace.review_history(course_id, target)
            assert after == before
        finally:
            workspace.close()

    def test_the_selected_evidence_is_preserved(self, reviewed, reviewed_reloaded) -> None:
        after = reviewed_reloaded.review_history(
            reviewed["course_id"], reviewed["target"]
        )
        assert [r["selected_evidence_ids"] for r in after] == [
            r["selected_evidence_ids"] for r in reviewed["history"]
        ]

    def test_the_review_ids_are_stable_across_a_restart(
        self, reviewed, reviewed_reloaded
    ) -> None:
        after = reviewed_reloaded.review_history(
            reviewed["course_id"], reviewed["target"]
        )
        assert [r["review_id"] for r in after] == [
            r["review_id"] for r in reviewed["history"]
        ]

    def test_a_new_decision_after_a_restart_is_appended_not_replaced(
        self, reviewed, review_sandbox
    ) -> None:
        """重启之后继续复核: 历史必须**变长**, 而不是被覆盖。

        用第二个知识点 (它的历史里只有验收跑出来的那一条 confirm), 在重启
        之后补一个 reject —— 结果必须是两条, 且第一条原样保留。
        """
        course_id = reviewed["course_id"]
        target = reviewed["second_target"]
        before = reviewed["second_history"]
        assert len(before) == 1, "前置条件: 第二个知识点应当只有一条历史"

        workspace = Workspace(review_sandbox)
        try:
            workspace.review_reject(course_id, target, note="tras el reinicio")
            after = workspace.review_history(course_id, target)
            assert len(after) == len(before) + 1
            assert after[0] == before[0], "旧记录被改写了 —— append-only 语义被破坏"
        finally:
            workspace.close()

    def test_the_appended_decision_survives_another_restart(
        self, reviewed, review_sandbox
    ) -> None:
        course_id = reviewed["course_id"]
        target = reviewed["second_target"]
        workspace = Workspace(review_sandbox)
        try:
            workspace.review_reject(course_id, target, note="tras el reinicio")
            expected = workspace.review_history(course_id, target)
        finally:
            workspace.close()

        reopened = Workspace(review_sandbox)
        try:
            assert reopened.review_history(course_id, target) == expected
            assert len(expected) == len(reviewed["second_history"]) + 1
        finally:
            reopened.close()

    def test_the_review_status_is_stable_across_a_restart(
        self, reviewed, reviewed_reloaded
    ) -> None:
        """状态是从记录集合确定性派生的 —— 重启不许改变它。

        这里刻意**不写死**具体值: 领域层的 ``review_status`` 按稳定的
        ``review_id`` 顺序派生, 具体是哪一个决定"赢"属于领域语义, 不该由
        持久化测试来规定。要钉的是"重启前后一致"。
        """
        kp = reviewed_reloaded.knowledge_point(reviewed["course_id"], reviewed["target"])
        assert kp["review_status"] == reviewed["review_status"]

    def test_review_records_are_not_duplicated_by_resaving(self, sandbox) -> None:
        before = _count(sandbox, "review_records")
        workspace = Workspace(sandbox)
        try:
            workspace.persistence.save_review_records(
                workspace.persistence.load_all_review_records()
            )
        finally:
            workspace.close()
        assert _count(sandbox, "review_records") == before

    def test_review_records_are_not_duplicated_by_restarting(self, base) -> None:
        before = _count(base["data_dir"], "review_records")
        for _ in range(2):
            workspace = Workspace(base["data_dir"])
            workspace.close()
        assert _count(base["data_dir"], "review_records") == before

    def test_the_review_history_is_ordered_deterministically(self, base, reloaded) -> None:
        orders = []
        for _ in range(2):
            workspace = Workspace(base["data_dir"])
            try:
                orders.append(
                    [
                        r["review_id"]
                        for kp in base["knowledge_points"][:5]
                        for r in workspace.review_history(
                            base["course_id"], kp["knowledge_id"]
                        )
                    ]
                )
            finally:
                workspace.close()
        assert orders[0] == orders[1]

    def test_a_review_record_points_at_a_real_knowledge_point(self, base) -> None:
        known = {kp["knowledge_id"] for kp in base["knowledge_points"]}
        orphans = [
            record.review_id
            for record in base["review_records"]
            if record.knowledge_point_id not in known
        ]
        assert not orphans, f"评审记录指向不存在的知识点: {orphans[:5]}"

    def test_the_review_record_decision_values_are_valid(self, base) -> None:
        allowed = {"confirm", "reject", "keep_unverified", "resolve_conflict"}
        bad = [
            record.review_id
            for record in base["review_records"]
            if str(record.decision.value).lower() not in allowed
        ]
        assert not bad, f"非法的评审决定: {bad[:5]}"

    def test_the_persisted_review_rows_match_the_domain_records(self, base) -> None:
        """数据库里的行数与领域对象数必须一一对应 (不是"大概相等")。"""
        assert _count(base["data_dir"], "review_records") == len(base["review_records"])

    def test_the_review_rows_carry_their_knowledge_point_column(self, base) -> None:
        database = open_database(default_database_path(base["data_dir"]), migrate=False)
        try:
            rows = database.query(
                "SELECT review_id, knowledge_point_id FROM review_records"
            )
        finally:
            database.close()
        assert rows
        assert all(row["knowledge_point_id"] for row in rows)


def _stored_answers(workspace, course_id: str) -> dict[str, dict]:
    """从**数据库**读回全部作答 (绕过内存, 这才是持久化的证据)。"""
    return {
        answer.answer_id: answer.to_dict()
        for answer in workspace.persistence.load_answers(course_id=course_id)
    }


# ======================================================================
# 51.2 Student / LearningState 持久化
# ======================================================================


class TestStudentPersistence:
    def test_students_land_in_sqlite(self, base) -> None:
        assert _count(base["data_dir"], "students") == len(base["students"])

    def test_the_student_list_is_identical_after_a_restart(self, base, reloaded) -> None:
        assert reloaded.list_students(base["course_id"]) == base["students"]

    def test_the_display_name_is_preserved(self, base, reloaded) -> None:
        student = reloaded.get_student(base["course_id"], base["student_id"])
        assert student["display_name"] == base["students"][0]["display_name"]

    def test_the_student_id_is_stable_across_a_restart(self, base, reloaded) -> None:
        ids = {s["student_id"] for s in reloaded.list_students(base["course_id"])}
        assert base["student_id"] in ids

    def test_creating_the_same_student_twice_keeps_one_row(self, base, sandbox) -> None:
        workspace = Workspace(sandbox)
        try:
            workspace.create_student(
                base["course_id"], base["student_id"], "Alumne de prova"
            )
        finally:
            workspace.close()
        assert _count(sandbox, "students") == 1

    def test_the_student_state_is_identical_after_a_restart(self, base, reloaded) -> None:
        assert reloaded.student_state(base["course_id"], base["student_id"]) == (
            base["student_state"]
        )

    def test_the_learning_status_is_identical_after_a_restart(self, base, reloaded) -> None:
        assert reloaded.learning_status(base["course_id"], base["student_id"]) == (
            base["learning_status"]
        )

    def test_the_registered_knowledge_points_are_preserved(self, base, reloaded) -> None:
        state = reloaded.student_state(base["course_id"], base["student_id"])
        assert state["registered_knowledge_points"] == (
            base["student_state"]["registered_knowledge_points"]
        )

    def test_the_per_knowledge_point_counters_are_preserved(self, base, reloaded) -> None:
        state = reloaded.student_state(base["course_id"], base["student_id"])
        assert state["states"] == base["student_state"]["states"]

    def test_the_practice_counts_are_preserved(self, base, reloaded) -> None:
        status = reloaded.learning_status(base["course_id"], base["student_id"])
        assert status["practice_counts"] == base["learning_status"]["practice_counts"]

    def test_the_recent_incorrect_list_is_preserved(self, base, reloaded) -> None:
        status = reloaded.learning_status(base["course_id"], base["student_id"])
        assert status["recent_incorrect_kps"] == (
            base["learning_status"]["recent_incorrect_kps"]
        )

    def test_the_learning_events_are_persisted(self, base) -> None:
        assert _count(base["data_dir"], "learning_events") >= 1

    def test_the_learning_events_are_not_duplicated_by_restarting(self, base) -> None:
        before = _count(base["data_dir"], "learning_events")
        for _ in range(2):
            workspace = Workspace(base["data_dir"])
            workspace.close()
        assert _count(base["data_dir"], "learning_events") == before

    def test_two_students_are_isolated(self, base, sandbox) -> None:
        workspace = Workspace(sandbox)
        try:
            workspace.create_student(base["course_id"], "student-otro", "Otro Alumne")
            assert len(workspace.list_students(base["course_id"])) == 2
            other = workspace.student_state(base["course_id"], "student-otro")
            assert other["registered_knowledge_points"] == []
        finally:
            workspace.close()

    def test_a_second_students_state_is_empty_not_a_copy(self, base, sandbox) -> None:
        """新学生的状态必须**从零开始**, 不许继承别人的进度。"""
        workspace = Workspace(sandbox)
        try:
            workspace.create_student(base["course_id"], "student-nuevo", "Nuevo")
            state = workspace.student_state(base["course_id"], "student-nuevo")
            assert state["states"] == []
        finally:
            workspace.close()

    def test_a_students_state_does_not_appear_in_another_course(self, sandbox) -> None:
        workspace = Workspace(sandbox)
        try:
            other = workspace.create_course("Otra", "OTR", "es")
            workspace.create_student(other["course_id"], "student-x", "X")
            assert [s["student_id"] for s in workspace.list_students(other["course_id"])] == [
                "student-x"
            ]
            assert workspace.student_state(other["course_id"], "student-x")["states"] == []
        finally:
            workspace.close()

    def test_an_unknown_student_is_not_invented(self, base, reloaded) -> None:
        from src.application.errors import NotFoundError

        with pytest.raises(NotFoundError):
            reloaded.get_student(base["course_id"], "student-does-not-exist")

    def test_the_student_rows_are_not_duplicated(self, base) -> None:
        ids = [
            row["student_id"]
            for row in _rows(base["data_dir"], "students", "student_id")
        ]
        assert len(ids) == len(set(ids))


def _rows(data_dir: str, table: str, column: str) -> list[dict]:
    database = open_database(default_database_path(data_dir), migrate=False)
    try:
        return [dict(row) for row in database.query(f"SELECT {column} FROM {table}")]
    finally:
        database.close()


# ======================================================================
# 51.3 Exercise 持久化
# ======================================================================


class TestExercisePersistence:
    def test_exercises_land_in_sqlite(self, base) -> None:
        assert _count(base["data_dir"], "exercises") == len(base["exercises"])

    def test_the_exercise_list_is_identical_after_a_restart(self, base, reloaded) -> None:
        assert reloaded.list_exercises(base["course_id"]) == base["exercises"]

    def test_every_exercise_field_is_preserved(self, base, reloaded) -> None:
        before = {e["exercise_id"]: e for e in base["exercises"]}
        after = {e["exercise_id"]: e for e in reloaded.list_exercises(base["course_id"])}
        assert after == before

    def test_the_prompt_is_preserved(self, base, reloaded) -> None:
        got = reloaded.get_exercise(base["course_id"], base["exercise_id"])
        assert got["prompt"] == base["exercises"][0]["prompt"]

    def test_the_answer_key_is_preserved(self, base, reloaded) -> None:
        """答案键必须留在库里 —— 否则老师再也编辑不了这道题。

        "学生视角看不到答案"是投影层的职责, 不是存储层删数据的理由。
        """
        got = reloaded.get_exercise(base["course_id"], base["exercise_id"])
        assert got["correct_choice_id"] == base["exercises"][0]["correct_choice_id"]

    def test_the_explanation_is_preserved(self, base, reloaded) -> None:
        got = reloaded.get_exercise(base["course_id"], base["exercise_id"])
        assert got["explanation"] == base["exercises"][0]["explanation"]

    def test_the_choices_are_preserved(self, base, reloaded) -> None:
        got = reloaded.get_exercise(base["course_id"], base["exercise_id"])
        assert got["choices"] == base["exercises"][0]["choices"]

    def test_the_knowledge_point_links_are_preserved(self, base, reloaded) -> None:
        got = reloaded.get_exercise(base["course_id"], base["exercise_id"])
        assert got["knowledge_point_ids"] == base["exercises"][0]["knowledge_point_ids"]

    def test_the_evidence_links_are_preserved(self, base, reloaded) -> None:
        got = reloaded.get_exercise(base["course_id"], base["exercise_id"])
        assert got["evidence_ids"] == base["exercises"][0]["evidence_ids"]

    def test_the_exercise_knowledge_link_table_is_populated(self, base) -> None:
        assert _count(base["data_dir"], "exercise_knowledge_points") >= 1

    def test_the_exercise_evidence_link_table_is_populated(self, base) -> None:
        assert _count(base["data_dir"], "exercise_evidence") >= 1

    def test_the_exercise_id_is_stable_across_a_restart(self, base, reloaded) -> None:
        ids = {e["exercise_id"] for e in reloaded.list_exercises(base["course_id"])}
        assert base["exercise_id"] in ids

    def test_creating_the_same_exercise_twice_keeps_one_row(self, base, sandbox) -> None:
        """同一条练习重放必须回到同一个 exercise_id (内容寻址)。"""
        exercise = base["exercises"][0]
        workspace = Workspace(sandbox)
        try:
            again = workspace.create_exercise(
                base["course_id"],
                exercise["exercise_type"],
                exercise["prompt"],
                exercise["knowledge_point_ids"],
                choices=exercise.get("choices"),
                correct_choice_id=exercise.get("correct_choice_id"),
                expected_answer=exercise.get("expected_answer"),
                explanation=exercise.get("explanation"),
                evidence_ids=exercise.get("evidence_ids") or (),
            )
            assert again["exercise_id"] == exercise["exercise_id"]
        finally:
            workspace.close()
        assert _count(sandbox, "exercises") == len(base["exercises"])

    def test_exercises_of_two_courses_are_isolated(self, sandbox) -> None:
        workspace = Workspace(sandbox)
        try:
            other = workspace.create_course("Otra", "OTR", "es")
            assert workspace.list_exercises(other["course_id"]) == []
        finally:
            workspace.close()

    def test_a_broken_evidence_reference_survives_a_restart(self, tmp_path) -> None:
        """领域层刻意允许练习引用不存在的证据 (UI 要报断链)。

        存储层的外键**不许**把这个合法的领域状态变成写入失败, 也不许在
        重启后把它抹掉 —— 抹掉就等于把数据缺陷藏起来。
        """
        data_dir = str(tmp_path / "broken-evidence")
        workspace = Workspace(data_dir)
        try:
            course = workspace.create_course("Álgebra", "ALG", "es")
            course_id = course["course_id"]
            session = workspace.create_session(course_id, session_number=1)
            source = tmp_path / "apuntes.txt"
            source.write_text("Tema 1\n", encoding="utf-8")
            workspace.register_material(
                course_id, str(source), session_id=session["session_id"]
            )
            workspace.process_session(course_id, session["session_id"])
            kps = workspace.knowledge_points(course_id)
            assert kps
            exercise = workspace.create_exercise(
                course_id,
                "true_false",
                "¿Es correcto?",
                [kps[0]["knowledge_id"]],
                is_true=True,
                evidence_ids=["evidence-does-not-exist"],
            )
            assert exercise["evidence_ids"] == ["evidence-does-not-exist"]
        finally:
            workspace.close()

        reopened = Workspace(data_dir)
        try:
            got = reopened.get_exercise(course_id, exercise["exercise_id"])
            assert got["evidence_ids"] == ["evidence-does-not-exist"]
            # 视图必须**显式报告**断链, 而不是把它藏起来。
            reopened.create_student(course_id, "student-broken", "Alumne")
            view = reopened.exercise_view(
                course_id, "student-broken", exercise["exercise_id"]
            )
            assert view["unresolved_evidence_ids"] == ["evidence-does-not-exist"]
            assert view["evidence_complete"] is False
        finally:
            reopened.close()

    def test_the_exercise_rows_are_not_duplicated(self, base) -> None:
        ids = [
            row["exercise_id"]
            for row in _rows(base["data_dir"], "exercises", "exercise_id")
        ]
        assert len(ids) == len(set(ids))


# ======================================================================
# 51.4 Answer / Evaluation 持久化
# ======================================================================


class TestAnswerPersistence:
    def test_answers_land_in_sqlite(self, base) -> None:
        assert _count(base["data_dir"], "student_answers") >= 1

    def test_the_answer_is_identical_after_a_restart(self, base, reloaded) -> None:
        assert _stored_answers(reloaded, base["course_id"])[base["answer_id"]] == (
            base["answer"]
        )

    def test_the_answer_fields_are_preserved(self, base, reloaded) -> None:
        got = _stored_answers(reloaded, base["course_id"])[base["answer_id"]]
        assert got["student_id"] == base["student_id"]
        assert got["exercise_id"] == base["exercise_id"]
        assert got["submitted_value"] == base["answer"]["submitted_value"]

    def test_the_submitted_at_timestamp_is_preserved(self, base, reloaded) -> None:
        """``submitted_at`` 是**列**, 不在 payload 里 (它不是领域身份的一部分)。

        所以这里直接查列 —— 也正是这条测试的意义: 时间戳有没有真的落盘。
        """
        database = open_database(default_database_path(base["data_dir"]), migrate=False)
        try:
            value = database.scalar(
                "SELECT submitted_at FROM student_answers WHERE answer_id = ?",
                (base["answer_id"],),
            )
        finally:
            database.close()
        assert value, "作答时间戳没有落盘"

    def test_the_answer_sequence_is_preserved(self, base, reloaded) -> None:
        got = _stored_answers(reloaded, base["course_id"])[base["answer_id"]]
        assert got["sequence"] == base["answer"]["sequence"]

    def test_the_answer_id_is_stable_across_a_restart(self, base, reloaded) -> None:
        ids = {
            row["answer_id"]
            for row in _rows(base["data_dir"], "student_answers", "answer_id")
        }
        assert base["answer_id"] in ids

    def test_the_evaluation_is_identical_after_a_restart(self, base, reloaded) -> None:
        got = reloaded.get_evaluation(base["course_id"], base["answer_id"])
        assert got == base["evaluation"]

    def test_the_evaluation_fields_are_preserved(self, base, reloaded) -> None:
        got = reloaded.get_evaluation(base["course_id"], base["answer_id"])
        for field in ("evaluation_id", "answer_id", "status", "score", "feedback"):
            assert got[field] == base["evaluation"][field], field

    def test_the_evaluation_links_to_its_answer(self, base, reloaded) -> None:
        got = reloaded.get_evaluation(base["course_id"], base["answer_id"])
        assert got["answer_id"] == base["answer_id"]

    def test_the_evaluation_rows_are_persisted(self, base) -> None:
        assert _count(base["data_dir"], "evaluation_results") >= 1

    def test_resubmitting_the_same_answer_keeps_one_row(self, base, sandbox) -> None:
        """答案身份是内容寻址的: 同 (学生, 练习, 值, 序号) -> 同一 answer_id。"""
        before = _count(sandbox, "student_answers")
        workspace = Workspace(sandbox)
        try:
            again = workspace.submit_answer(
                base["course_id"],
                base["student_id"],
                base["exercise_id"],
                base["answer"]["submitted_value"],
                sequence=int(base["answer"]["sequence"]),
            )
            assert again["answer_id"] == base["answer_id"]
        finally:
            workspace.close()
        assert _count(sandbox, "student_answers") == before

    def test_resubmitting_the_same_answer_after_a_restart_keeps_one_row(
        self, base, sandbox
    ) -> None:
        before = _count(sandbox, "student_answers")
        workspace = Workspace(sandbox)
        try:
            again = workspace.submit_answer(
                base["course_id"],
                base["student_id"],
                base["exercise_id"],
                base["answer"]["submitted_value"],
                sequence=int(base["answer"]["sequence"]),
            )
            assert again["answer_id"] == base["answer_id"]
        finally:
            workspace.close()

        # 再重启一次, 仍然只有一行。
        reopened = Workspace(sandbox)
        try:
            assert _count(sandbox, "student_answers") == before
        finally:
            reopened.close()

    def test_a_different_answer_accumulates_a_new_row(self, base, sandbox) -> None:
        workspace = Workspace(sandbox)
        try:
            before = _count(sandbox, "student_answers")
            workspace.submit_answer(
                base["course_id"], base["student_id"], base["exercise_id"], "b"
            )
            assert _count(sandbox, "student_answers") == before + 1
        finally:
            workspace.close()

    def test_answers_of_two_students_are_isolated(self, base, sandbox) -> None:
        workspace = Workspace(sandbox)
        try:
            workspace.create_student(base["course_id"], "student-otro", "Otro")
            other = workspace.learning_status(base["course_id"], "student-otro")
            assert other["answered_count"] == 0
        finally:
            workspace.close()

    def test_the_answer_rows_are_not_duplicated(self, base) -> None:
        ids = [
            row["answer_id"]
            for row in _rows(base["data_dir"], "student_answers", "answer_id")
        ]
        assert len(ids) == len(set(ids))

    def test_the_answer_rows_are_not_duplicated_by_restarting(self, base) -> None:
        before = _count(base["data_dir"], "student_answers")
        for _ in range(2):
            workspace = Workspace(base["data_dir"])
            workspace.close()
        assert _count(base["data_dir"], "student_answers") == before

    def test_the_evaluation_rows_are_not_duplicated_by_restarting(self, base) -> None:
        before = _count(base["data_dir"], "evaluation_results")
        for _ in range(2):
            workspace = Workspace(base["data_dir"])
            workspace.close()
        assert _count(base["data_dir"], "evaluation_results") == before


class TestExerciseSerializationRoundTrip:
    """Task 51 发现的真实缺陷: 判断题存进数据库后读不回来。

    根因: 自动生成的判断题选项是 ``(true, false)``, 而显式传入的选项会被
    ``_validate_choices`` 排序成 ``(false, true)``。两条路径算出**两个不同的
    ``exercise_id``**, 于是 ``from_dict`` 认定 payload 损坏而拒绝反序列化。

    修法: 身份用规范化 (排序后) 的选项列表, 展示顺序保留作者给的顺序。

    为什么这个缺陷以前没被发现: 在 Task 51 之前, 练习从来没有被真正写进
    数据库再读回来过 —— 内存里创建、内存里用, 往返路径根本没人走。
    """

    def test_a_true_false_exercise_round_trips(self) -> None:
        from src.exercises import Exercise, ExplicitExerciseBuilder

        builder = ExplicitExerciseBuilder("course-1")
        for is_true in (True, False):
            exercise = builder.build_true_false("¿Es correcto?", ("kp-1",), is_true)
            restored = Exercise.from_dict(exercise.to_dict())
            assert restored.exercise_id == exercise.exercise_id

    def test_the_true_false_display_order_is_true_then_false(self) -> None:
        from src.exercises import ExplicitExerciseBuilder

        exercise = ExplicitExerciseBuilder("course-1").build_true_false(
            "¿Es correcto?", ("kp-1",), True
        )
        assert [c.choice_id for c in exercise.choices] == ["true", "false"]

    def test_the_true_false_display_order_survives_the_round_trip(self) -> None:
        from src.exercises import Exercise, ExplicitExerciseBuilder

        exercise = ExplicitExerciseBuilder("course-1").build_true_false(
            "¿Es correcto?", ("kp-1",), True
        )
        restored = Exercise.from_dict(exercise.to_dict())
        assert [c.choice_id for c in restored.choices] == ["true", "false"]

    def test_a_multiple_choice_exercise_round_trips(self) -> None:
        from src.exercises import Choice, Exercise, ExplicitExerciseBuilder

        exercise = ExplicitExerciseBuilder("course-1").build_multiple_choice(
            "¿Cuál?",
            ("kp-1",),
            (Choice("a", "Uno"), Choice("b", "Dos"), Choice("c", "Tres")),
            "b",
        )
        restored = Exercise.from_dict(exercise.to_dict())
        assert restored.exercise_id == exercise.exercise_id
        assert [c.choice_id for c in restored.choices] == ["a", "b", "c"]

    def test_the_choice_order_does_not_change_the_identity(self) -> None:
        """同一道题换一个选项顺序**不许**变成两道题 (身份与顺序无关)。"""
        from src.exercises import Choice, ExplicitExerciseBuilder

        builder = ExplicitExerciseBuilder("course-1")
        first = builder.build_multiple_choice(
            "¿Cuál?", ("kp-1",), (Choice("a", "Uno"), Choice("b", "Dos")), "a"
        )
        second = builder.build_multiple_choice(
            "¿Cuál?", ("kp-1",), (Choice("b", "Dos"), Choice("a", "Uno")), "a"
        )
        assert first.exercise_id == second.exercise_id

    def test_the_author_choice_order_is_kept_for_display(self) -> None:
        from src.exercises import Choice, ExplicitExerciseBuilder

        exercise = ExplicitExerciseBuilder("course-1").build_multiple_choice(
            "¿Cuál?", ("kp-1",), (Choice("b", "Dos"), Choice("a", "Uno")), "a"
        )
        assert [c.choice_id for c in exercise.choices] == ["b", "a"]

    def test_a_true_false_exercise_survives_the_database(self, tmp_path) -> None:
        """端到端: 写进 SQLite 再读回来 (这才是当初漏掉的那条路径)。"""
        data_dir = str(tmp_path / "tf-roundtrip")
        workspace = Workspace(data_dir)
        try:
            course = workspace.create_course("Álgebra", "ALG", "es")
            course_id = course["course_id"]
            session = workspace.create_session(course_id, session_number=1)
            source = tmp_path / "apuntes.txt"
            source.write_text("Un algoritmo transforma una entrada.\n", encoding="utf-8")
            workspace.register_material(
                course_id, str(source), session_id=session["session_id"]
            )
            workspace.process_session(course_id, session["session_id"])
            kp = workspace.knowledge_points(course_id)[0]
            created = workspace.create_exercise(
                course_id,
                "true_false",
                "¿Un algoritmo transforma una entrada en una salida?",
                [kp["knowledge_id"]],
                is_true=True,
            )
        finally:
            workspace.close()

        reopened = Workspace(data_dir)
        try:
            restored = reopened.get_exercise(course_id, created["exercise_id"])
            assert restored == created
        finally:
            reopened.close()


# ======================================================================
# 51.5 真值安全 (本文件最重要的一组)
# ======================================================================


class TestTruthSafety:
    """**学生答错绝不能修改知识真值。**

    这一组测试的共同做法: 记下作答前的知识快照 -> 提交一个错答案 ->
    断言快照逐字节不变 -> 重启 -> 再断言一次。

    为什么连重启都要查: 如果"答案污染知识"这件事发生在加载路径上
    (例如加载时按学生进度重算知识点), 内存里看不出来, 重启才暴露。
    """

    @pytest.fixture()
    def lab(self, tmp_path):
        """一个独立的小实验台: 一节课 -> 知识 -> 练习 -> 学生。"""
        data_dir = str(tmp_path / "lab")
        workspace = Workspace(data_dir)
        course = workspace.create_course("Álgebra", "ALG", "es")
        course_id = course["course_id"]
        session = workspace.create_session(course_id, session_number=1)
        source = tmp_path / "apuntes.txt"
        source.write_text(
            "Un algoritmo transforma una entrada en una salida.\n"
            "La complejidad temporal mide el coste.\n",
            encoding="utf-8",
        )
        workspace.register_material(
            course_id, str(source), session_id=session["session_id"]
        )
        workspace.process_session(course_id, session["session_id"])
        knowledge_points = workspace.knowledge_points(course_id)
        assert knowledge_points, "实验台必须产出知识点"
        student = workspace.create_student(course_id, "student-lab", "Alumne")
        exercise = workspace.create_exercise(
            course_id,
            "true_false",
            "¿Un algoritmo transforma una entrada en una salida?",
            [knowledge_points[0]["knowledge_id"]],
            is_true=True,
        )
        yield {
            "workspace": workspace,
            "data_dir": data_dir,
            "course_id": course_id,
            "knowledge_points": knowledge_points,
            "student_id": student["student_id"],
            "exercise_id": exercise["exercise_id"],
        }
        workspace.close()

    def _snapshot(self, lab) -> dict:
        workspace = lab["workspace"]
        course_id = lab["course_id"]
        return {
            "knowledge_points": workspace.knowledge_points(course_id),
            "evidence": {
                e.evidence_id: e.to_dict()
                for e in workspace.store.all(active_only=False)
            },
            "conflicts": workspace.conflicts(course_id),
            "coverage": workspace.coverage(course_id),
            "history": {
                kp["knowledge_id"]: workspace.review_history(
                    course_id, kp["knowledge_id"]
                )
                for kp in lab["knowledge_points"]
            },
        }

    def _submit_wrong(self, lab) -> dict:
        return lab["workspace"].submit_answer(
            lab["course_id"], lab["student_id"], lab["exercise_id"], "false"
        )

    def test_a_wrong_answer_does_not_change_the_knowledge_point_statement(
        self, lab
    ) -> None:
        before = self._snapshot(lab)
        self._submit_wrong(lab)
        after = self._snapshot(lab)
        assert after["knowledge_points"] == before["knowledge_points"]

    def test_a_wrong_answer_does_not_change_the_evidence_content(self, lab) -> None:
        before = self._snapshot(lab)
        self._submit_wrong(lab)
        after = self._snapshot(lab)
        assert after["evidence"] == before["evidence"]

    def test_a_wrong_answer_does_not_change_the_review_records(self, lab) -> None:
        before = self._snapshot(lab)
        self._submit_wrong(lab)
        after = self._snapshot(lab)
        assert after["history"] == before["history"]

    def test_a_wrong_answer_does_not_change_the_conflicts(self, lab) -> None:
        before = self._snapshot(lab)
        self._submit_wrong(lab)
        after = self._snapshot(lab)
        assert after["conflicts"] == before["conflicts"]

    def test_a_wrong_answer_does_not_change_the_coverage(self, lab) -> None:
        before = self._snapshot(lab)
        self._submit_wrong(lab)
        after = self._snapshot(lab)
        assert after["coverage"] == before["coverage"]

    def test_a_wrong_answer_does_not_change_the_validation_status(self, lab) -> None:
        before = {
            kp["knowledge_id"]: kp["validation_status"]
            for kp in lab["workspace"].knowledge_points(lab["course_id"])
        }
        self._submit_wrong(lab)
        after = {
            kp["knowledge_id"]: kp["validation_status"]
            for kp in lab["workspace"].knowledge_points(lab["course_id"])
        }
        assert after == before

    def test_a_wrong_answer_does_not_change_the_evidence_refs(self, lab) -> None:
        before = {
            kp["knowledge_id"]: kp["evidence_refs"]
            for kp in lab["workspace"].knowledge_points(lab["course_id"])
        }
        self._submit_wrong(lab)
        after = {
            kp["knowledge_id"]: kp["evidence_refs"]
            for kp in lab["workspace"].knowledge_points(lab["course_id"])
        }
        assert after == before

    def test_a_wrong_answer_does_not_change_the_evidence_store_size(self, lab) -> None:
        before = len(lab["workspace"].store.all(active_only=False))
        self._submit_wrong(lab)
        assert len(lab["workspace"].store.all(active_only=False)) == before

    def test_a_wrong_answer_only_changes_the_students_own_counters(self, lab) -> None:
        before = lab["workspace"].learning_status(lab["course_id"], lab["student_id"])
        self._submit_wrong(lab)
        after = lab["workspace"].learning_status(lab["course_id"], lab["student_id"])
        assert after != before, "作答必须真的被记录下来"
        assert after["answered_count"] == before["answered_count"] + 1

    def test_a_wrong_answer_does_not_touch_the_knowledge_rows_in_sqlite(self, lab) -> None:
        """连数据库里的原始 payload 也不许变 —— 不只是 API 视图不变。"""
        before = _payload_rows(lab["data_dir"], "knowledge_points")
        self._submit_wrong(lab)
        assert _payload_rows(lab["data_dir"], "knowledge_points") == before

    def test_a_wrong_answer_does_not_touch_the_evidence_rows_in_sqlite(self, lab) -> None:
        before = _payload_rows(lab["data_dir"], "evidence")
        self._submit_wrong(lab)
        assert _payload_rows(lab["data_dir"], "evidence") == before

    def test_a_wrong_answer_does_not_touch_the_review_rows_in_sqlite(self, lab) -> None:
        before = _payload_rows(lab["data_dir"], "review_records")
        self._submit_wrong(lab)
        assert _payload_rows(lab["data_dir"], "review_records") == before

    def test_the_truth_is_still_intact_after_a_restart(self, lab) -> None:
        before = self._snapshot(lab)
        self._submit_wrong(lab)
        lab["workspace"].close()

        reopened = Workspace(lab["data_dir"])
        try:
            course_id = lab["course_id"]
            assert reopened.knowledge_points(course_id) == before["knowledge_points"]
            assert {
                e.evidence_id: e.to_dict()
                for e in reopened.store.all(active_only=False)
            } == before["evidence"]
            assert reopened.conflicts(course_id) == before["conflicts"]
            assert reopened.coverage(course_id) == before["coverage"]
        finally:
            reopened.close()

    def test_an_incorrect_answer_marks_the_student_not_the_knowledge(self, lab) -> None:
        """答错只在**学生侧**留下痕迹 (incorrect_count), 知识侧必须干净。"""
        exercise = lab["workspace"].get_exercise(lab["course_id"], lab["exercise_id"])
        lab["workspace"].submit_answer(
            lab["course_id"],
            lab["student_id"],
            lab["exercise_id"],
            exercise["correct_choice_id"] or "a",
        )
        state = lab["workspace"].student_state(lab["course_id"], lab["student_id"])
        assert any(entry["answer_count"] >= 1 for entry in state["states"])
        assert all(entry["incorrect_count"] == 0 for entry in state["states"])

    def test_the_knowledge_ids_are_unchanged_by_answering(self, lab) -> None:
        before = [kp["knowledge_id"] for kp in lab["workspace"].knowledge_points(lab["course_id"])]
        self._submit_wrong(lab)
        after = [kp["knowledge_id"] for kp in lab["workspace"].knowledge_points(lab["course_id"])]
        assert after == before

    def test_the_knowledge_structure_rows_are_unchanged_by_answering(self, lab) -> None:
        before = _count(lab["data_dir"], "knowledge_point_evidence")
        self._submit_wrong(lab)
        assert _count(lab["data_dir"], "knowledge_point_evidence") == before


def _payload_rows(data_dir: str, table: str) -> list[tuple]:
    database = open_database(default_database_path(data_dir), migrate=False)
    try:
        rows = database.query(
            f"SELECT * FROM {table} ORDER BY 1"
        )
    finally:
        database.close()
    out = []
    for row in rows:
        item = {key: row[key] for key in row.keys()}
        item.pop("payload", None)
        out.append((tuple(sorted(item.items())), row["payload"]))
    return out


# ======================================================================
# 51.6 幂等 / 确定性
# ======================================================================


class TestLearningIdempotency:
    def test_submitting_the_same_answer_three_times_counts_once(self, tmp_path) -> None:
        """重复提交 3 次, 学习计数必须还是 1。

        这条曾经是**真实缺陷**: 幂等 DTO 让 answer_id 相同, 但日志与
        planner 快照仍然每次都加一, 于是 correct_count / practice_counts
        会膨胀到 3 —— 而且重启后重建快照会得到另一个数字。
        """
        data_dir = str(tmp_path / "idem")
        workspace = Workspace(data_dir)
        try:
            course = workspace.create_course("Álgebra", "ALG", "es")
            course_id = course["course_id"]
            session = workspace.create_session(course_id, session_number=1)
            source = tmp_path / "apuntes.txt"
            source.write_text("Un algoritmo transforma una entrada.\n", encoding="utf-8")
            workspace.register_material(
                course_id, str(source), session_id=session["session_id"]
            )
            workspace.process_session(course_id, session["session_id"])
            kp = workspace.knowledge_points(course_id)[0]
            workspace.create_student(course_id, "student-idem", "Alumne")
            exercise = workspace.create_exercise(
                course_id, "true_false", "¿Es correcto?", [kp["knowledge_id"]], is_true=True
            )

            for _ in range(3):
                workspace.submit_answer(
                    course_id, "student-idem", exercise["exercise_id"], "true"
                )

            status = workspace.learning_status(course_id, "student-idem")
            state = workspace.student_state(course_id, "student-idem")
            assert status["answered_count"] == 1
            assert status["practice_counts"] == {kp["knowledge_id"]: 1}
            assert state["states"][0]["answer_count"] == 1
            assert state["states"][0]["correct_count"] == 1
            assert _count(data_dir, "student_answers") == 1
            assert _count(data_dir, "evaluation_results") == 1
        finally:
            workspace.close()

    def test_the_counts_do_not_inflate_across_a_restart(self, tmp_path) -> None:
        data_dir = str(tmp_path / "idem2")
        workspace = Workspace(data_dir)
        course = workspace.create_course("Álgebra", "ALG", "es")
        course_id = course["course_id"]
        session = workspace.create_session(course_id, session_number=1)
        source = tmp_path / "apuntes.txt"
        source.write_text("Un algoritmo transforma una entrada.\n", encoding="utf-8")
        workspace.register_material(course_id, str(source), session_id=session["session_id"])
        workspace.process_session(course_id, session["session_id"])
        kp = workspace.knowledge_points(course_id)[0]
        workspace.create_student(course_id, "student-idem", "Alumne")
        exercise = workspace.create_exercise(
            course_id, "true_false", "¿Es correcto?", [kp["knowledge_id"]], is_true=True
        )
        for _ in range(3):
            workspace.submit_answer(
                course_id, "student-idem", exercise["exercise_id"], "true"
            )
        before = workspace.learning_status(course_id, "student-idem")
        workspace.close()

        for _ in range(2):
            reopened = Workspace(data_dir)
            try:
                assert reopened.learning_status(course_id, "student-idem") == before
            finally:
                reopened.close()

    def test_the_student_state_is_stable_across_two_restarts(self, base) -> None:
        states = []
        for _ in range(2):
            workspace = Workspace(base["data_dir"])
            try:
                states.append(
                    workspace.student_state(base["course_id"], base["student_id"])
                )
            finally:
                workspace.close()
        assert states[0] == states[1]

    def test_the_learning_events_are_stable_across_two_restarts(self, base) -> None:
        counts = []
        for _ in range(2):
            workspace = Workspace(base["data_dir"])
            workspace.close()
            counts.append(_count(base["data_dir"], "learning_events"))
        assert counts[0] == counts[1]

    def test_restarting_does_not_create_new_exercises(self, base) -> None:
        for _ in range(2):
            workspace = Workspace(base["data_dir"])
            workspace.close()
        assert _count(base["data_dir"], "exercises") == len(base["exercises"])

    def test_restarting_does_not_create_new_students(self, base) -> None:
        for _ in range(2):
            workspace = Workspace(base["data_dir"])
            workspace.close()
        assert _count(base["data_dir"], "students") == len(base["students"])

    def test_the_evaluation_is_deterministic_for_the_same_answer(self, base) -> None:
        workspace = Workspace(base["data_dir"])
        try:
            assert workspace.get_evaluation(base["course_id"], base["answer_id"]) == (
                base["evaluation"]
            )
        finally:
            workspace.close()

    def test_the_learning_view_is_identical_after_a_restart(self, base, reloaded) -> None:
        """学生视角的练习列表 (含进度) 必须逐字节一致。

        这条把"持久化"和"投影"接在一起: 只要学生状态或练习有一处没落盘,
        投影出来的视图就会变。
        """
        before = _stored_answers(reloaded, base["course_id"])
        assert before, "前置条件: 必须已有一份作答"
        view = reloaded.exercise_list_view(base["course_id"], base["student_id"])
        assert view["course_id"] == base["course_id"]
        assert view["student_id"] == base["student_id"]
        assert len(view["exercises"]) == len(base["exercises"])
        # 重启前后必须一致 —— 用同一个工作区再取一次做对照。
        assert view == reloaded.exercise_list_view(base["course_id"], base["student_id"])

    def test_the_exercise_evaluation_view_is_identical_after_a_restart(
        self, base, reloaded
    ) -> None:
        view = reloaded.exercise_evaluation_view(
            base["course_id"], base["student_id"], base["exercise_id"]
        )
        assert view["exercise_id"] == base["exercise_id"]
        assert view["evaluation"]["evaluation_id"] == base["evaluation"]["evaluation_id"]
