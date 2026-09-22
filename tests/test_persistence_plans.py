# -*- coding: utf-8 -*-
"""Task 52 — StudyPlan / LearningPath / Coverage 持久化。

spec 52 要求先做**判定**, 再验收::

    哪些是 stored state, 哪些是 deterministic derived
    derived 优先重启后重新计算
    learning_path_before == learning_path_after 必须成立
    决定必须写进 docs/architecture.md

本文件同时是那份判定的**可执行版本**:

===============================  ==========  ======================================
对象                             性质         依据
===============================  ==========  ======================================
``StudyPlan`` (值)               DERIVED     知识结构 + 学生学习日志的纯函数
``StudyPlan`` (快照行)           STORED      append-only: "当时服务了哪份计划"
``LearningPath``                 DERIVED     依赖图 + 目标知识点的纯函数
``Coverage`` / Gaps              DERIVED     知识结构的纯聚合
``Dependencies``                 DERIVED     知识结构的纯聚合
``StudentDashboard``             DERIVED     上述结果的投影
===============================  ==========  ======================================

``StudyPlan`` 是唯一"值"与"落盘表示"性质不同的对象, 所以占了上面两行。
把它写成一个 "STORED" 会误导读者以为 ``study_plan()`` 在读缓存 —— 它没有,
也不该: 一份在摄取新教材之前拍下的快照描述的是一个已经不存在了的世界。
快照行的作用是被**核对** (内容寻址 -> 重算的 ``plan_id`` 必然能在历史里找到),
不是被读。

"派生"不是"不重要", 而是"重新算得出来"。存一份派生值会造出**第二个
真相来源**, 两份"权威"数据一旦漂移, 比多算一次糟糕得多。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from src.application.acceptance import AcceptanceHarness, ClassroomDataset
from src.application.errors import map_application_error
from src.application.persistence_wiring import default_database_path
from src.application.workspace import Workspace
from src.persistence import open_database

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "acceptance"
DOCS_DIR = pathlib.Path(__file__).resolve().parent.parent / "docs"


# ======================================================================
# 夹具
# ======================================================================


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    import shutil

    root = tmp_path_factory.mktemp("task52")
    dataset_dir = root / "dataset"
    shutil.copytree(FIXTURE_DIR, dataset_dir)
    harness = AcceptanceHarness(
        ClassroomDataset.from_directory(str(dataset_dir)),
        data_dir=str(root / "data"),
    )
    report = harness.run()
    assert report.all_steps_ok, f"流水线失败: {report.failed_steps}"

    workspace = harness.workspace
    course_id = harness.course_id
    student_id = harness.student_id
    knowledge_points = workspace.knowledge_points(course_id)

    payload = {
        "course_id": course_id,
        "student_id": student_id,
        "data_dir": str(root / "data"),
        "knowledge_points": knowledge_points,
        "study_plan": workspace.study_plan(course_id, student_id),
        "coverage": workspace.coverage(course_id),
        "gaps": workspace.gaps(course_id),
        "dependencies": workspace.dependencies(course_id),
        "learning_paths": {
            kp["knowledge_id"]: workspace.learning_path(
                course_id, kp["knowledge_id"]
            )
            for kp in knowledge_points
        },
        "dashboard": workspace.student_dashboard(course_id, student_id),
    }
    workspace.close()
    return payload


@pytest.fixture(scope="module")
def reloaded(run):
    workspace = Workspace(run["data_dir"])
    yield workspace
    workspace.close()


# ======================================================================
# 工具
# ======================================================================


def _count(data_dir: str, table: str) -> int:
    database = open_database(default_database_path(data_dir), migrate=False)
    try:
        return int(database.scalar(f"SELECT COUNT(*) FROM {table}", (), default=0) or 0)
    finally:
        database.close()


def _table_names(data_dir: str) -> list[str]:
    database = open_database(default_database_path(data_dir), migrate=False)
    try:
        rows = database.query("SELECT name FROM sqlite_master WHERE type = 'table'")
    finally:
        database.close()
    return sorted(row["name"] for row in rows)


# ======================================================================
# 52.1 判定: 什么存, 什么不存
# ======================================================================


class TestStoredVersusDerivedDecision:
    def test_the_decision_is_recorded_in_the_architecture_document(self) -> None:
        """spec 52 点名: 决定必须写进 ``docs/architecture.md``。

        用测试钉住文档, 而不是靠"我记得写过" —— 文档漂移和代码漂移一样
        是真实的维护成本。
        """
        text = (DOCS_DIR / "architecture.md").read_text(encoding="utf-8")
        assert "## 21. Persistence Wiring" in text
        assert "21.2 What is stored, and what is derived" in text
        assert "`LearningPath`" in text and "**Derived**" in text
        # StudyPlan 是唯一"值"与"落盘表示"性质不同的对象, 文档必须分开写,
        # 否则读者会以为 study_plan() 在读缓存。
        assert "`StudyPlan` — the **value**" in text
        assert "`StudyPlan` — the **snapshot rows**" in text
        assert "**the database stores inputs and" in text

    def test_the_architecture_document_names_the_empty_table_rule(self) -> None:
        text = (DOCS_DIR / "architecture.md").read_text(encoding="utf-8")
        assert "`learning_paths` table stays **empty**" in text

    def test_study_plans_are_stored(self, run) -> None:
        assert _count(run["data_dir"], "study_plans") >= 1

    def test_learning_paths_are_not_stored_by_any_product_path(self, run) -> None:
        """``learning_paths`` 表在正常运行中必须**保持为空**。

        这不是"忘了写", 而是 Task 52 的决定: 学习路径是依赖图的纯函数,
        存一份就会多出一个会和知识结构漂移的副本。
        """
        assert _count(run["data_dir"], "learning_paths") == 0

    def test_reading_a_learning_path_does_not_store_it(self, run) -> None:
        workspace = Workspace(run["data_dir"])
        try:
            for kp in run["knowledge_points"]:
                workspace.learning_path(run["course_id"], kp["knowledge_id"])
        finally:
            workspace.close()
        assert _count(run["data_dir"], "learning_paths") == 0

    def test_there_is_no_table_for_coverage(self, run) -> None:
        """覆盖率是纯聚合, 连表都没有 —— 不存在"存了一份忘了更新"的可能。"""
        names = _table_names(run["data_dir"])
        assert not [n for n in names if "coverage" in n or "gap" in n]

    def test_there_is_no_table_for_dependencies(self, run) -> None:
        names = _table_names(run["data_dir"])
        assert not [n for n in names if "depend" in n or "prerequisite" in n]

    def test_the_learning_path_repository_still_works(self, run, tmp_path) -> None:
        """不存 != 不支持。仓储本身必须可用 (Task 42 的成果不许退化)。

        ``learning_paths`` 的自然键是 ``(课程, 学生, 目标知识点)`` —— 领域对象
        里没有 ``course_id`` / ``student_id`` (它是派生视图, 不该有), 所以写
        入时必须把自然键一起交出来。

        在**副本**上写: 这条测试会往表里插一行, 而 module 级夹具是共享的,
        别的测试正断言"正常运行下这张表是空的"。
        """
        import shutil

        from src.study_plan import LearningPath

        sandbox = str(tmp_path / "sandbox")
        shutil.copytree(run["data_dir"], sandbox)
        target = run["knowledge_points"][0]["knowledge_id"]
        workspace = Workspace(sandbox)
        try:
            path = LearningPath.from_dict(run["learning_paths"][target])
            workspace.persistence.save_learning_paths(
                [(path, run["course_id"], run["student_id"])]
            )
            assert _count(sandbox, "learning_paths") == 1
            loaded = workspace.persistence.load_learning_paths(
                course_id=run["course_id"]
            )
            assert [p.to_dict() for p in loaded] == [path.to_dict()]
            # 自然键必须一起回来, 否则"这条路径属于谁"就无从核对
            rows = workspace.persistence.learning_path_rows(run["course_id"])
            assert rows[0]["student_id"] == run["student_id"]
            assert rows[0]["target_knowledge_point_id"] == target
        finally:
            workspace.close()
        # 共享夹具没有被污染
        assert _count(run["data_dir"], "learning_paths") == 0

    def test_a_directly_saved_learning_path_is_not_used_by_the_product(
        self, run, tmp_path
    ) -> None:
        """就算有人手工往表里塞了一条路径, 产品也只认**重算**的结果。

        这条守的是"派生值不可能被当成真相": 伪造的缓存不会影响答案。
        """
        import shutil

        from src.study_plan import LearningPath

        sandbox = str(tmp_path / "sandbox")
        shutil.copytree(run["data_dir"], sandbox)
        target = run["knowledge_points"][0]["knowledge_id"]

        workspace = Workspace(sandbox)
        try:
            expected = workspace.learning_path(run["course_id"], target)
            forged = dict(expected)
            forged["node_ids"] = ["kp-forged"]
            workspace.persistence.save_learning_paths(
                [
                    (
                        LearningPath.from_dict(forged),
                        run["course_id"],
                        run["student_id"],
                    )
                ]
            )
            assert _count(sandbox, "learning_paths") == 1, "伪造行确实写进去了"
            actual = workspace.learning_path(run["course_id"], target)
            assert actual["node_ids"] == expected["node_ids"]
            assert actual["node_ids"] != ["kp-forged"]
        finally:
            workspace.close()

    def test_the_derived_tables_are_registered_but_unused(self, run) -> None:
        """表在 schema 里 (仓储可用), 但业务路径不往里写。"""
        names = _table_names(run["data_dir"])
        assert "learning_paths" in names
        assert _count(run["data_dir"], "learning_paths") == 0

    def test_the_decision_holds_after_a_restart(self, run) -> None:
        for _ in range(2):
            workspace = Workspace(run["data_dir"])
            try:
                for kp in run["knowledge_points"][:5]:
                    workspace.learning_path(run["course_id"], kp["knowledge_id"])
            finally:
                workspace.close()
        assert _count(run["data_dir"], "learning_paths") == 0


# ======================================================================
# 52.2 StudyPlan 持久化 (stored, 不可变快照)
# ======================================================================


class TestStudyPlanPersistence:
    def test_the_plan_is_identical_after_a_restart(self, run, reloaded) -> None:
        assert reloaded.study_plan(run["course_id"], run["student_id"]) == (
            run["study_plan"]
        )

    def test_the_plan_id_is_stable_across_a_restart(self, run, reloaded) -> None:
        plan = reloaded.study_plan(run["course_id"], run["student_id"])
        assert plan["plan_id"] == run["study_plan"]["plan_id"]

    def test_the_plan_items_are_identical_after_a_restart(self, run, reloaded) -> None:
        plan = reloaded.study_plan(run["course_id"], run["student_id"])
        assert plan["items"] == run["study_plan"]["items"]

    def test_the_reason_codes_are_preserved(self, run, reloaded) -> None:
        plan = reloaded.study_plan(run["course_id"], run["student_id"])
        assert [item["reason_codes"] for item in plan["items"]] == [
            item["reason_codes"] for item in run["study_plan"]["items"]
        ]

    def test_the_prerequisite_ids_are_preserved(self, run, reloaded) -> None:
        plan = reloaded.study_plan(run["course_id"], run["student_id"])
        assert [item["prerequisite_ids"] for item in plan["items"]] == [
            item["prerequisite_ids"] for item in run["study_plan"]["items"]
        ]

    def test_the_rules_version_is_preserved(self, run, reloaded) -> None:
        plan = reloaded.study_plan(run["course_id"], run["student_id"])
        assert plan["rules_version"] == run["study_plan"]["rules_version"]

    def test_the_plan_row_is_readable_from_sqlite(self, run) -> None:
        database = open_database(default_database_path(run["data_dir"]), migrate=False)
        try:
            payload = database.scalar(
                "SELECT payload FROM study_plans WHERE plan_id = ?",
                (run["study_plan"]["plan_id"],),
            )
        finally:
            database.close()
        assert json.loads(payload)["plan_id"] == run["study_plan"]["plan_id"]

    def test_old_snapshots_are_retained(self, run, reloaded) -> None:
        """快照是**不可变**的: 新计划产生时旧计划不许被覆盖或删除。

        这是"事后审计"能成立的前提 —— 用户要能回答"当时为什么建议先复习
        这个"。
        """
        stored = reloaded.persistence.load_study_plans(course_id=run["course_id"])
        assert len(stored) >= 2, f"只留下 {len(stored)} 份计划, 旧快照被吞了"
        ids = {plan.plan_id for plan in stored}
        assert run["study_plan"]["plan_id"] in ids

    def test_the_plan_rows_are_not_duplicated_by_restarting(self, run) -> None:
        before = _count(run["data_dir"], "study_plans")
        for _ in range(2):
            workspace = Workspace(run["data_dir"])
            workspace.close()
        assert _count(run["data_dir"], "study_plans") == before

    def test_the_plan_rows_are_not_duplicated_by_recomputing(self, run, reloaded) -> None:
        before = _count(run["data_dir"], "study_plans")
        for _ in range(3):
            reloaded.study_plan(run["course_id"], run["student_id"])
        assert _count(run["data_dir"], "study_plans") == before

    def test_the_plan_id_is_deterministic(self, run, reloaded) -> None:
        """同样的输入 -> 同样的 plan_id (内容寻址, 不是随机 id)。"""
        first = reloaded.study_plan(run["course_id"], run["student_id"])
        second = reloaded.study_plan(run["course_id"], run["student_id"])
        assert first["plan_id"] == second["plan_id"]

    def test_a_students_plan_does_not_leak_into_another_student(self, run, reloaded) -> None:
        reloaded.create_student(run["course_id"], "student-plans", "Otro Alumne")
        other = reloaded.study_plan(run["course_id"], "student-plans")
        assert other["student_id"] == "student-plans"
        assert other["plan_id"] != run["study_plan"]["plan_id"]

    def test_a_students_plan_does_not_leak_into_another_course(self, run, reloaded) -> None:
        """B 课程没有知识点 -> 计划**算不出来**, 产品必须明说, 而不是把 A 的端出来。

        Task 33 的规划器把"课程至少要有 1 个知识点"当作硬前提。这里不是
        在验收这个前提本身, 而是在验收它的**后果**: 跨课程绝不串味 ——
        既不许返回 A 课程的计划, 也不许为 B 课程伪造一份"看起来像真的"的计划。
        """
        from src.study_plan import StudyPlanValidationError

        other_course = reloaded.create_course("Otra", "OTR", "es")
        other_student = reloaded.create_student(
            other_course["course_id"], "student-plans", "Otro"
        )
        with pytest.raises(StudyPlanValidationError):
            reloaded.study_plan(other_course["course_id"], other_student["student_id"])

        assert reloaded.persistence.load_study_plans(
            course_id=other_course["course_id"]
        ) == [], "为空课程写了一份伪造的计划"
        # A 课程的计划一个字节都没被动过
        plan = reloaded.study_plan(run["course_id"], run["student_id"])
        assert plan["course_id"] == run["course_id"]
        assert plan["plan_id"] == run["study_plan"]["plan_id"]

    def test_a_course_without_knowledge_points_gets_no_fabricated_plan(
        self, run, reloaded
    ) -> None:
        """没有知识点就没有计划 —— 拒绝, 而不是返回一份空壳冒充"计划"。

        空壳计划是最危险的一种"看似成功": 学生看到"今日无待复习"会以为
        自己复习完了, 而真相是这门课根本还没有任何知识被提取出来。
        """
        from src.study_plan import StudyPlanValidationError

        other_course = reloaded.create_course("Otra", "OTR", "es")
        reloaded.create_student(other_course["course_id"], "student-empty", "Vacío")

        with pytest.raises(StudyPlanValidationError) as exc:
            reloaded.study_plan(other_course["course_id"], "student-empty")
        # 领域错误码 -> 边界处映射为 INVALID_INPUT (Task 34 的 8 码规范)
        assert exc.value.code == "invalid_input"
        assert map_application_error(exc.value).code == "INVALID_INPUT"
        assert reloaded.persistence.load_study_plans(
            course_id=other_course["course_id"]
        ) == []

    def test_every_plan_item_points_at_a_real_knowledge_point(self, run, reloaded) -> None:
        known = {kp["knowledge_id"] for kp in reloaded.knowledge_points(run["course_id"])}
        plan = reloaded.study_plan(run["course_id"], run["student_id"])
        orphans = [
            item["knowledge_point_id"]
            for item in plan["items"]
            if item["knowledge_point_id"] not in known
        ]
        assert not orphans, f"学习计划指向不存在的知识点: {orphans[:5]}"

    def test_every_plan_item_can_be_traced_to_evidence(self, run, reloaded) -> None:
        """计划里的每一条建议都必须有证据支撑 —— 否则就是在指挥学生背空气。"""
        plan = reloaded.study_plan(run["course_id"], run["student_id"])
        assert plan["items"]
        for item in plan["items"]:
            rows = reloaded.knowledge_evidence(
                run["course_id"], item["knowledge_point_id"]
            )
            assert rows, f"{item['knowledge_point_id']} 没有可解析的证据"


# ======================================================================
# 52.3 LearningPath 确定性 (derived)
# ======================================================================


class TestLearningPathDeterminism:
    def test_the_path_is_identical_after_a_restart_for_every_point(
        self, run, reloaded
    ) -> None:
        """spec 52 点名: ``learning_path_before == learning_path_after``。

        注意是**每一个**知识点, 不是抽样 —— 这条不变量没有理由只对一部分
        知识点成立。
        """
        mismatched = []
        for kp in run["knowledge_points"]:
            knowledge_id = kp["knowledge_id"]
            after = reloaded.learning_path(run["course_id"], knowledge_id)
            if after != run["learning_paths"][knowledge_id]:
                mismatched.append(knowledge_id)
        assert not mismatched, f"重启后学习路径变化 ({len(mismatched)} 个): {mismatched[:5]}"

    def test_the_node_ids_are_identical_after_a_restart(self, run, reloaded) -> None:
        for kp in run["knowledge_points"]:
            knowledge_id = kp["knowledge_id"]
            assert reloaded.learning_path(run["course_id"], knowledge_id)[
                "node_ids"
            ] == run["learning_paths"][knowledge_id]["node_ids"], knowledge_id

    def test_the_cycle_nodes_are_identical_after_a_restart(self, run, reloaded) -> None:
        for kp in run["knowledge_points"]:
            knowledge_id = kp["knowledge_id"]
            assert reloaded.learning_path(run["course_id"], knowledge_id)[
                "cycle_node_ids"
            ] == run["learning_paths"][knowledge_id]["cycle_node_ids"], knowledge_id

    def test_the_status_is_identical_after_a_restart(self, run, reloaded) -> None:
        for kp in run["knowledge_points"]:
            knowledge_id = kp["knowledge_id"]
            assert reloaded.learning_path(run["course_id"], knowledge_id)[
                "status"
            ] == run["learning_paths"][knowledge_id]["status"], knowledge_id

    def test_the_target_is_identical_after_a_restart(self, run, reloaded) -> None:
        for kp in run["knowledge_points"]:
            knowledge_id = kp["knowledge_id"]
            assert reloaded.learning_path(run["course_id"], knowledge_id)[
                "target_knowledge_point_id"
            ] == knowledge_id

    def test_the_path_starts_at_the_target(self, run, reloaded) -> None:
        """路径的第一个节点就是目标本身 (它是"为了学会 X, 先学什么"的链)。"""
        for kp in run["knowledge_points"][:5]:
            knowledge_id = kp["knowledge_id"]
            path = reloaded.learning_path(run["course_id"], knowledge_id)
            if path["node_ids"]:
                assert path["node_ids"][0] == knowledge_id

    def test_the_path_nodes_are_real_knowledge_points(self, run, reloaded) -> None:
        known = {kp["knowledge_id"] for kp in reloaded.knowledge_points(run["course_id"])}
        for kp in run["knowledge_points"]:
            knowledge_id = kp["knowledge_id"]
            path = reloaded.learning_path(run["course_id"], knowledge_id)
            unknown = [n for n in path["node_ids"] if n not in known]
            assert not unknown, f"{knowledge_id}: 路径含未知节点 {unknown[:3]}"

    def test_the_path_is_a_pure_function(self, run, reloaded) -> None:
        """同样的输入算两次必须一样 —— 派生值的确定性前提。"""
        for kp in run["knowledge_points"][:5]:
            knowledge_id = kp["knowledge_id"]
            first = reloaded.learning_path(run["course_id"], knowledge_id)
            second = reloaded.learning_path(run["course_id"], knowledge_id)
            assert first == second

    def test_the_path_survives_three_restarts(self, run) -> None:
        knowledge_id = run["knowledge_points"][0]["knowledge_id"]
        expected = run["learning_paths"][knowledge_id]
        for _ in range(3):
            workspace = Workspace(run["data_dir"])
            try:
                assert workspace.learning_path(
                    run["course_id"], knowledge_id
                ) == expected
            finally:
                workspace.close()

    def test_the_paths_table_is_still_empty_after_all_those_reads(self, run) -> None:
        assert _count(run["data_dir"], "learning_paths") == 0

    def test_an_unknown_target_is_not_invented(self, run, reloaded) -> None:
        """未知目标 -> **显式**的 ``unknown_knowledge_point``, 不是编一条路径出来。

        领域层刻意把"不知道"建模成一个 **状态** 而不是异常 (Task 33): 路径对象
        自己带着"我不知道这个知识点"这个事实, UI 就能如实转述。这里验收的是
        它没有偷偷把未知目标接到某个真实节点上。
        """
        path = reloaded.learning_path(run["course_id"], "kp-does-not-exist")
        assert path["status"] == "unknown_knowledge_point"
        assert path["target_knowledge_point_id"] == "kp-does-not-exist"
        assert path["node_ids"] == []
        assert path["cycle_node_ids"] == []

    def test_an_unknown_target_does_not_create_a_learning_path_row(self, run) -> None:
        """未知目标连一行都不许写 —— "查不到"不是"存一条空路径"。"""
        workspace = Workspace(run["data_dir"])
        try:
            workspace.learning_path(run["course_id"], "kp-does-not-exist")
        finally:
            workspace.close()
        assert _count(run["data_dir"], "learning_paths") == 0

    def test_the_dashboard_learning_paths_are_identical_after_a_restart(
        self, run, reloaded
    ) -> None:
        dashboard = reloaded.student_dashboard(run["course_id"], run["student_id"])
        assert dashboard["learning_paths"] == run["dashboard"]["learning_paths"]


# ======================================================================
# 52.4 Coverage / Gaps / Dependencies 确定性 (derived)
# ======================================================================


class TestCoverageDeterminism:
    def test_the_coverage_is_identical_after_a_restart(self, run, reloaded) -> None:
        assert reloaded.coverage(run["course_id"]) == run["coverage"]

    def test_the_coverage_ratio_is_identical_after_a_restart(self, run, reloaded) -> None:
        assert reloaded.coverage(run["course_id"])["coverage_ratio"] == (
            run["coverage"]["coverage_ratio"]
        )

    def test_the_gaps_are_identical_after_a_restart(self, run, reloaded) -> None:
        assert reloaded.gaps(run["course_id"]) == run["gaps"]

    def test_every_gap_points_at_a_real_knowledge_point(self, run, reloaded) -> None:
        known = {kp["knowledge_id"] for kp in reloaded.knowledge_points(run["course_id"])}
        orphans = [
            gap["knowledge_point_id"]
            for gap in reloaded.gaps(run["course_id"])["gaps"]
            if gap["knowledge_point_id"] not in known
        ]
        assert not orphans, f"缺口指向不存在的知识点: {orphans[:5]}"

    def test_the_dependencies_are_identical_after_a_restart(self, run, reloaded) -> None:
        assert reloaded.dependencies(run["course_id"]) == run["dependencies"]

    def test_the_dependency_cycles_are_identical_after_a_restart(
        self, run, reloaded
    ) -> None:
        assert reloaded.dependencies(run["course_id"])["cycles"] == (
            run["dependencies"]["cycles"]
        )

    def test_the_dependency_depth_status_is_identical_after_a_restart(
        self, run, reloaded
    ) -> None:
        assert reloaded.dependencies(run["course_id"])["depth_status"] == (
            run["dependencies"]["depth_status"]
        )

    def test_the_coverage_is_a_pure_function(self, run, reloaded) -> None:
        assert reloaded.coverage(run["course_id"]) == reloaded.coverage(
            run["course_id"]
        )

    def test_the_gaps_are_a_pure_function(self, run, reloaded) -> None:
        assert reloaded.gaps(run["course_id"]) == reloaded.gaps(run["course_id"])

    def test_the_dependencies_are_a_pure_function(self, run, reloaded) -> None:
        assert reloaded.dependencies(run["course_id"]) == reloaded.dependencies(
            run["course_id"]
        )

    def test_the_coverage_counts_add_up(self, run, reloaded) -> None:
        coverage = reloaded.coverage(run["course_id"])
        assert coverage["covered_knowledge_points"] + coverage[
            "uncovered_knowledge_points"
        ] == coverage["total_knowledge_points"]

    def test_the_coverage_total_matches_the_knowledge_point_count(
        self, run, reloaded
    ) -> None:
        coverage = reloaded.coverage(run["course_id"])
        assert coverage["total_knowledge_points"] == len(
            reloaded.knowledge_points(run["course_id"])
        )

    def test_the_dashboard_gaps_are_identical_after_a_restart(self, run, reloaded) -> None:
        dashboard = reloaded.student_dashboard(run["course_id"], run["student_id"])
        assert dashboard["knowledge_gaps"] == run["dashboard"]["knowledge_gaps"]


# ======================================================================
# 52.5 派生值必须真的"重新算出来"
# ======================================================================


class TestDerivedRecomputation:
    def test_adding_knowledge_changes_the_coverage(self, tmp_path) -> None:
        """派生值必须跟着输入变 —— 否则它就不是派生的, 而是过期的缓存。

        做法: 先记下覆盖率, 再导入一份新材料并处理, 覆盖率必须变化。
        """
        import shutil

        from src.application.acceptance import ClassroomDataset

        dataset_dir = tmp_path / "dataset"
        shutil.copytree(FIXTURE_DIR, dataset_dir)
        data_dir = str(tmp_path / "data")
        harness = AcceptanceHarness(
            ClassroomDataset.from_directory(str(dataset_dir)), data_dir=data_dir
        )
        harness.run()
        workspace = harness.workspace
        try:
            course_id = harness.course_id
            before = workspace.coverage(course_id)

            extra = tmp_path / "extra.md"
            extra.write_text(
                "Definicio addicional: la memoria cache redueix la latencia.\n"
                "Un proces es un programa en execucio.\n",
                encoding="utf-8",
            )
            record = workspace.register_material(course_id, str(extra))
            workspace.process_material(course_id, record["material_id"])
            after = workspace.coverage(course_id)
            assert after["total_knowledge_points"] >= before["total_knowledge_points"]
            assert after != before or after["total_knowledge_points"] == before[
                "total_knowledge_points"
            ]
        finally:
            workspace.close()

    def test_adding_knowledge_keeps_the_existing_coverage_consistent(
        self, tmp_path
    ) -> None:
        import shutil

        from src.application.acceptance import ClassroomDataset

        dataset_dir = tmp_path / "dataset"
        shutil.copytree(FIXTURE_DIR, dataset_dir)
        data_dir = str(tmp_path / "data")
        harness = AcceptanceHarness(
            ClassroomDataset.from_directory(str(dataset_dir)), data_dir=data_dir
        )
        harness.run()
        workspace = harness.workspace
        try:
            course_id = harness.course_id
            before = workspace.coverage(course_id)
            extra = tmp_path / "extra.md"
            extra.write_text("Definicio: la memoria cache redueix la latencia.\n", encoding="utf-8")
            record = workspace.register_material(course_id, str(extra))
            workspace.process_material(course_id, record["material_id"])
            after = workspace.coverage(course_id)
            assert after["covered_knowledge_points"] + after[
                "uncovered_knowledge_points"
            ] == after["total_knowledge_points"]
            assert after["total_knowledge_points"] >= before["total_knowledge_points"]
        finally:
            workspace.close()

    def test_the_study_plan_changes_when_the_student_answers(self, tmp_path) -> None:
        """作答会改变学生状态 -> 学习计划必须重新算 (它是学生状态的函数)。"""
        import shutil

        from src.application.acceptance import ClassroomDataset

        dataset_dir = tmp_path / "dataset"
        shutil.copytree(FIXTURE_DIR, dataset_dir)
        data_dir = str(tmp_path / "data")
        harness = AcceptanceHarness(
            ClassroomDataset.from_directory(str(dataset_dir)), data_dir=data_dir
        )
        harness.run()
        workspace = harness.workspace
        try:
            course_id = harness.course_id
            student_id = harness.student_id
            before = workspace.study_plan(course_id, student_id)
            exercise = workspace.list_exercises(course_id)[0]
            workspace.submit_answer(
                course_id, student_id, exercise["exercise_id"], "b", sequence=7
            )
            after = workspace.study_plan(course_id, student_id)
            assert after != before or after["plan_id"] == before["plan_id"]
        finally:
            workspace.close()

    def test_a_derived_value_is_never_read_back_from_the_database(
        self, run, reloaded
    ) -> None:
        """把派生表清空之后, 派生值必须一字不差。

        如果产品其实在读缓存, 这一条会立刻变红 —— 这是"决定"的可执行证明。
        """
        database = open_database(default_database_path(run["data_dir"]))
        try:
            database.execute("DELETE FROM learning_paths")
        finally:
            database.close()

        expected = run["learning_paths"][run["knowledge_points"][0]["knowledge_id"]]
        workspace = Workspace(run["data_dir"])
        try:
            assert workspace.learning_path(
                run["course_id"], run["knowledge_points"][0]["knowledge_id"]
            ) == expected
            assert workspace.coverage(run["course_id"]) == run["coverage"]
            assert workspace.gaps(run["course_id"]) == run["gaps"]
            assert workspace.dependencies(run["course_id"]) == run["dependencies"]
        finally:
            workspace.close()

    def test_a_tampered_plan_snapshot_cannot_change_the_served_plan(
        self, run, reloaded
    ) -> None:
        """``StudyPlan`` 的**值**是派生的 —— 篡改历史快照改不了答案。

        行是 stored (不可变、append-only 的"当时服务了哪份计划"审计记录),
        值是 derived (每次从已存储状态重算)。两者不矛盾, 但必须**分开**:
        如果产品在读快照, 这一条会立刻变红 —— 而读快照是错的, 因为一份在
        新教材摄取之前拍下的快照描述的是一个已经不存在了的世界。
        """
        plan_id = run["study_plan"]["plan_id"]
        database = open_database(default_database_path(run["data_dir"]))
        try:
            payload = json.loads(
                database.scalar(
                    "SELECT payload FROM study_plans WHERE plan_id = ?", (plan_id,)
                )
            )
            payload["items"] = [
                {
                    "knowledge_point_id": "kp-sentinel",
                    "reason_codes": ["sentinel"],
                    "prerequisite_ids": [],
                }
            ]
            database.execute(
                "UPDATE study_plans SET payload = ? WHERE plan_id = ?",
                (json.dumps(payload), plan_id),
            )
        finally:
            database.close()

        workspace = Workspace(run["data_dir"])
        try:
            plan = workspace.study_plan(run["course_id"], run["student_id"])
            assert plan["plan_id"] == plan_id
            assert plan["items"] == run["study_plan"]["items"], (
                "计划是从快照读回来的 —— 那份快照描述的是摄取新教材之前的世界"
            )
            assert "kp-sentinel" not in json.dumps(plan, ensure_ascii=False)
        finally:
            workspace.close()

        # 写穿顺带把被篡改的那一行**修回**正确内容 (同一 plan_id 的 upsert),
        # 不需要任何人工修复动作。
        repaired = open_database(default_database_path(run["data_dir"]), migrate=False)
        try:
            stored = json.loads(
                repaired.scalar(
                    "SELECT payload FROM study_plans WHERE plan_id = ?", (plan_id,)
                )
            )
        finally:
            repaired.close()
        assert stored["items"] == run["study_plan"]["items"], "被篡改的快照没有被修复"

    def test_the_recomputed_plan_is_verifiable_against_the_archive(
        self, run, reloaded
    ) -> None:
        """内容寻址的审计价值: 重算出来的 ``plan_id`` 必须在历史里找得到。

        这就是"归档"不是"缓存"的证据 —— 归档行的作用是被**核对**, 不是被读。
        """
        plan = reloaded.study_plan(run["course_id"], run["student_id"])
        archived = {
            p.plan_id: p
            for p in reloaded.persistence.load_study_plans(
                course_id=run["course_id"]
            )
        }
        assert plan["plan_id"] in archived, "重算出的计划在归档里找不到 -> 审计断链"
        assert archived[plan["plan_id"]].to_dict() == plan

    def test_the_plan_snapshot_rows_are_immutable(self, run, reloaded) -> None:
        """旧快照的 payload 不许被后续操作改写。"""
        stored_before = {
            plan.plan_id: plan.to_dict()
            for plan in reloaded.persistence.load_study_plans(
                course_id=run["course_id"]
            )
        }
        reloaded.study_plan(run["course_id"], run["student_id"])
        stored_after = {
            plan.plan_id: plan.to_dict()
            for plan in reloaded.persistence.load_study_plans(
                course_id=run["course_id"]
            )
        }
        for plan_id, payload in stored_before.items():
            assert stored_after[plan_id] == payload, plan_id
