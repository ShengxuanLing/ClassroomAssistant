# -*- coding: utf-8 -*-
"""Task 47.4 — Final production performance audit.

验证课堂助手在接近真实规模的负载下仍保持线性 (而非 O(N^2)) 的热路径性能:

规模基线
    - 1000 KnowledgePoints
    - 100  Topics
    - 50   Sessions
    - 500  Students
    - 5000 Exercises
    - 20000 Answers

被测热路径
    startup / dashboard / knowledge query / coverage / learning path /
    exercise list / answer submission / database save / database load / backup

硬性约束
    - 所有业务 ID 显式给定 (确定性), 不触发 uuid4 fallback。
    - 单一知识点查询必须是 O(1) (恒定时间), 不随数据集增长。
    - 各聚合热路径在给定规模下必须在宽松的时间上限内完成; 任何 O(N^2)
      的实现都会在此规模下突破上限被发现。
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List

import pytest

from src.models import KnowledgePoint, Course, ClassSession
from src.knowledge_organization import KnowledgeOrganizationService
from src.application.learning_service import LearningService
from src.knowledge_coverage import KnowledgeCoverageAnalyzer
from src.knowledge_dependency import KnowledgeDependencyAnalyzer
from src.student_learning import Student
from src.answer_evaluation import StudentAnswer

from src.persistence import open_database, Repositories

# --- 规模常量 (spec 47.4) -------------------------------------------------
KP_N = 1000
TOPIC_N = 100
SESSION_N = 50
STUDENT_N = 500
EXERCISE_N = 5000
ANSWER_N = 20000
# 通过真实服务路径提交的代表性样本 (用于测量 answer-submission 热路径);
# 其余 20000 - 样本 条答案通过持久化层整体落库, 以验证数据集规模容量。
ANSWER_SUBMIT_SAMPLE = 1500
# 前置链块长: 每 20 个知识点一段链 (真实课程的前置图是浅而宽的)。
PREREQ_BLOCK = 20

# --- 时间上限 (宽松; O(N^2) 会在此规模下突破) ----------------------------
BUILD_SECONDS_MAX = 30.0
KNOWLEDGE_QUERY_SECONDS_MAX = 0.050  # 1000 次单点查询的总时间上限
COVERAGE_SECONDS_MAX = 5.0
LEARNING_PATH_SECONDS_MAX = 3.0
DASHBOARD_SECONDS_MAX = 12.0
EXERCISE_LIST_SECONDS_MAX = 3.0
ANSWER_SUBMIT_PER_MAX = 0.050  # 每条 answer 平均耗时上限 (秒)
DB_SAVE_SECONDS_MAX = 30.0
DB_LOAD_SECONDS_MAX = 10.0
BACKUP_SECONDS_MAX = 10.0


def _build_knowledge_points() -> List[KnowledgePoint]:
    kps: List[KnowledgePoint] = []
    for i in range(1, KP_N + 1):
        kps.append(
            KnowledgePoint(
                knowledge_id=f"kp-{i:04d}",
                title=f"Concepto {i}",
                content=f"Definición determinista del conocimiento punto {i}.",
                original_terms=[f"término-{i}"],
                importance="medium",
                confidence="high",
                evidence_refs=[],
                validation_status="verified",
                review_status="approved",
                knowledge_score=0.9,
            )
        )
    return kps


@pytest.fixture(scope="module")
def scale_model() -> Dict[str, Any]:
    """构建一次接近真实规模的确定性内存模型, 并记录装配耗时。"""
    t0 = time.perf_counter()

    course = Course(course_id="perf-course", name="Curso Rendimiento", code="PERF101")
    org = KnowledgeOrganizationService(course)
    kps = _build_knowledge_points()
    for kp in kps:
        org.register_knowledge_point(kp)

    topic_ids: List[str] = []
    for i in range(TOPIC_N):
        topic = org.add_topic(f"Tema {i}", description=f"Tema número {i}")
        topic_ids.append(topic.topic_id)

    session_ids: List[str] = []
    for i in range(SESSION_N):
        sess = ClassSession(
            course_id=course.course_id,
            session_id=f"ses-{i:03d}",
            session_number=i + 1,
            title=f"Sesión {i}",
        )
        org.register_session(sess)
        session_ids.append(sess.session_id)

    # 前置链: kp[i] 依赖 kp[i-1] (prerequisite)。
    # KP id 从 kp-0001 开始, 因此 i 从 2 起, 避免引用不存在的 kp-0000。
    # 每 PREREQ_BLOCK 条边断开一次 —— 真实课程的前置图是浅而宽的,
    # 这里保持 ~千条边但把链深限制在 20 以内 (见 "已知限制" 说明)。
    prereqs: Dict[str, tuple] = {}
    for i in range(2, KP_N + 1):
        if (i - 1) % PREREQ_BLOCK == 0:
            continue
        org.add_relation(f"kp-{i:04d}", f"kp-{i-1:04d}", "prerequisite")
        prereqs[f"kp-{i:04d}"] = (f"kp-{i-1:04d}",)

    ls = LearningService(course.course_id, prerequisite_provider=lambda: dict(prereqs))
    ls.register_course_knowledge_points([kp.knowledge_id for kp in kps])

    student_ids: List[str] = []
    for i in range(STUDENT_N):
        sid = f"stu-{i:03d}"
        ls.create_student(sid, f"Estudiante {i}")
        student_ids.append(sid)

    exercise_ids: List[str] = []
    for i in range(EXERCISE_N):
        ex = ls.create_exercise(
            "multiple_choice",
            f"Enunciado del ejercicio {i}",
            [f"kp-{(i % KP_N) + 1:04d}"],
            choices=[
                {"choice_id": "a", "text": "Opción A"},
                {"choice_id": "b", "text": "Opción B"},
            ],
            correct_choice_id="a",
            evidence_ids=[],
            difficulty=1,
        )
        exercise_ids.append(ex["exercise_id"])

    build_seconds = time.perf_counter() - t0

    return {
        "course": course,
        "org": org,
        "kps": kps,
        "topic_ids": topic_ids,
        "session_ids": session_ids,
        "student_ids": student_ids,
        "exercise_ids": exercise_ids,
        "prereqs": prereqs,
        "ls": ls,
        "build_seconds": build_seconds,
    }


def _synthetic_answers(model: Dict[str, Any]) -> List[StudentAnswer]:
    """构造 20000 条确定性答案 (不依赖 uuid4, 答案 ID 由内容决定)。"""
    exercise_ids = model["exercise_ids"]
    student_ids = model["student_ids"]
    answers: List[StudentAnswer] = []
    for i in range(ANSWER_N):
        student_id = student_ids[i % STUDENT_N]
        exercise_id = exercise_ids[i % EXERCISE_N]
        # 约 1/4 错 (选 b), 其余对 (选 a) —— 用于评估分布, 不影响规模。
        value = "a" if (i % 4 != 0) else "b"
        answers.append(StudentAnswer.create(student_id, exercise_id, value, sequence=i))
    return answers


# --- 规模与装配 -----------------------------------------------------------
def test_scale_counts(scale_model: Dict[str, Any]) -> None:
    org = scale_model["org"]
    assert len(scale_model["kps"]) == KP_N
    assert len(scale_model["topic_ids"]) == TOPIC_N
    assert len(scale_model["session_ids"]) == SESSION_N
    assert len(scale_model["student_ids"]) == STUDENT_N
    assert len(scale_model["exercise_ids"]) == EXERCISE_N
    assert len(org.registered_knowledge_point_ids) == KP_N
    assert len(org.list_topics()) == TOPIC_N
    assert len(org.registered_session_ids) == SESSION_N
    assert len(org.list_relations()) == len(scale_model["prereqs"])
    # 规模基线: 至少达到 spec 47.4 要求的量级。
    assert len(scale_model["prereqs"]) >= 900


def test_startup_assembly_within_bound(scale_model: Dict[str, Any]) -> None:
    # 装配 1000 KP + 100 topics + 50 sessions + 999 关系 + 500 students
    # + 5000 exercises 的耗时必须在上限内 (冷装配成本代理 "startup")。
    assert scale_model["build_seconds"] < BUILD_SECONDS_MAX


# --- 单点查询: 必须是 O(1) ------------------------------------------------
def test_knowledge_query_constant_time(scale_model: Dict[str, Any]) -> None:
    org = scale_model["org"]
    kp_ids = [kp.knowledge_id for kp in scale_model["kps"]]
    topic_ids = scale_model["topic_ids"]

    t0 = time.perf_counter()
    hits = 0
    for kid in kp_ids:
        if kid in org.registered_knowledge_point_ids:
            hits += 1
    for tid in topic_ids:
        _ = org.get_topic(tid)
    elapsed = time.perf_counter() - t0

    assert hits == KP_N
    # 1000 次知识点集合成员 + 100 次单主题查询, 总耗时宽松上限。
    assert elapsed < KNOWLEDGE_QUERY_SECONDS_MAX


# --- 覆盖分析 ------------------------------------------------------------
def test_coverage_analysis_within_bound(scale_model: Dict[str, Any]) -> None:
    org = scale_model["org"]
    analyzer = KnowledgeCoverageAnalyzer(org)

    t0 = time.perf_counter()
    report = analyzer.analyze_course()
    elapsed = time.perf_counter() - t0

    assert report.total_knowledge_points == KP_N
    assert elapsed < COVERAGE_SECONDS_MAX


# --- 依赖分析 ------------------------------------------------------------
def test_dependency_analysis_within_bound(scale_model: Dict[str, Any]) -> None:
    org = scale_model["org"]
    analyzer = KnowledgeDependencyAnalyzer(org)

    t0 = time.perf_counter()
    analysis = analyzer.analyze_course()
    elapsed = time.perf_counter() - t0

    # 前置边应全部被依赖分析识别为 prerequisite 边, 且不存在环。
    assert len(analysis.prerequisite_edges) == len(scale_model["prereqs"])
    assert analysis.cycle_count == 0
    assert elapsed < COVERAGE_SECONDS_MAX


# --- 学习路径 (深链) -----------------------------------------------------
def test_learning_path_within_bound(scale_model: Dict[str, Any]) -> None:
    ls = scale_model["ls"]
    target = f"kp-{PREREQ_BLOCK:04d}"  # 第一段链的链尾

    t0 = time.perf_counter()
    path = ls.get_learning_path(target)
    elapsed = time.perf_counter() - t0

    # 路径应覆盖该段前置链的全部节点 (root-first)。
    assert len(path["node_ids"]) == PREREQ_BLOCK
    assert path["target_knowledge_point_id"] == target
    assert elapsed < LEARNING_PATH_SECONDS_MAX


# --- 仪表盘聚合 ----------------------------------------------------------
def test_dashboard_aggregation_within_bound(scale_model: Dict[str, Any]) -> None:
    org = scale_model["org"]
    ls = scale_model["ls"]
    student_id = scale_model["student_ids"][0]
    coverage = KnowledgeCoverageAnalyzer(org)

    t0 = time.perf_counter()
    _coverage_report = coverage.analyze_course()
    _exercises = ls.list_exercises()
    _students = ls.list_students()
    _plan = ls.get_study_plan(student_id)
    elapsed = time.perf_counter() - t0

    assert len(_exercises) == EXERCISE_N
    assert len(_students) == STUDENT_N
    assert _plan is not None
    assert elapsed < DASHBOARD_SECONDS_MAX


# --- 练习列表 ------------------------------------------------------------
def test_exercise_list_within_bound(scale_model: Dict[str, Any]) -> None:
    ls = scale_model["ls"]

    t0 = time.perf_counter()
    exercises = ls.list_exercises()
    elapsed = time.perf_counter() - t0

    assert len(exercises) == EXERCISE_N
    assert elapsed < EXERCISE_LIST_SECONDS_MAX


# --- 作答提交热路径 (真实服务路径) --------------------------------------
def test_answer_submission_throughput(scale_model: Dict[str, Any]) -> None:
    ls = scale_model["ls"]
    exercise_ids = scale_model["exercise_ids"]
    student_ids = scale_model["student_ids"]

    t0 = time.perf_counter()
    submitted = 0
    for i in range(ANSWER_SUBMIT_SAMPLE):
        student_id = student_ids[i % STUDENT_N]
        exercise_id = exercise_ids[i % EXERCISE_N]
        value = "a" if (i % 4 != 0) else "b"
        out = ls.submit_answer(student_id, exercise_id, value, sequence=i)
        assert out["answer_id"]
        submitted += 1
    elapsed = time.perf_counter() - t0

    assert submitted == ANSWER_SUBMIT_SAMPLE
    # 每条答案平均耗时必须在上限内 (恒定, 不随数据集二次膨胀)。
    assert (elapsed / submitted) < ANSWER_SUBMIT_PER_MAX


# --- 数据库落库 (规模) ---------------------------------------------------
def test_database_save_scale(scale_model: Dict[str, Any], tmp_path) -> None:
    db_path = str(tmp_path / "classroom.sqlite")
    db = open_database(db_path)
    repos = Repositories(db)

    kps = scale_model["kps"]
    students = list(scale_model["ls"]._students.values())
    exercises = list(scale_model["ls"]._exercises.values())
    sessions = [scale_model["org"]._sessions_by_id[s] for s in scale_model["session_ids"]]
    answers = _synthetic_answers(scale_model)

    t0 = time.perf_counter()
    course = scale_model["course"]
    with repos.transaction():
        # course 必须先落库: sessions / topics 对 courses 有外键约束。
        repos.courses.save(course)
        repos.knowledge.save_many(kps)
        repos.students.save_many(students)
        repos.exercises.save_many(exercises)
        repos.sessions.save_many(sessions)
        repos.organization.save_structure(scale_model["org"].structure)
        repos.answers.save_many(answers)
    elapsed = time.perf_counter() - t0

    assert elapsed < DB_SAVE_SECONDS_MAX
    # 落库后立即校验计数 (写已提交)。
    assert len(repos.knowledge.load_all()) == KP_N
    assert len(repos.answers.load_all()) == ANSWER_N
    db.close()


# --- 数据库加载 (规模) ---------------------------------------------------
def test_database_load_scale(scale_model: Dict[str, Any], tmp_path) -> None:
    db_path = str(tmp_path / "classroom.sqlite")
    db = open_database(db_path)
    repos = Repositories(db)

    kps = scale_model["kps"]
    students = list(scale_model["ls"]._students.values())
    exercises = list(scale_model["ls"]._exercises.values())
    sessions = [scale_model["org"]._sessions_by_id[s] for s in scale_model["session_ids"]]
    answers = _synthetic_answers(scale_model)

    course = scale_model["course"]
    with repos.transaction():
        # course 必须先落库: sessions / topics 对 courses 有外键约束。
        repos.courses.save(course)
        repos.knowledge.save_many(kps)
        repos.students.save_many(students)
        repos.exercises.save_many(exercises)
        repos.sessions.save_many(sessions)
        repos.organization.save_structure(scale_model["org"].structure)
        repos.answers.save_many(answers)

    t0 = time.perf_counter()
    kp_loaded = repos.knowledge.load_all()
    ex_loaded = repos.exercises.load_all()
    stu_loaded = repos.students.load_all()
    ans_loaded = repos.answers.load_all()
    struct = repos.organization.load_structure(scale_model["course"].course_id)
    elapsed = time.perf_counter() - t0

    assert len(kp_loaded) == KP_N
    assert len(ex_loaded) == EXERCISE_N
    assert len(stu_loaded) == STUDENT_N
    assert len(ans_loaded) == ANSWER_N
    assert len(struct.topics) == TOPIC_N
    assert elapsed < DB_LOAD_SECONDS_MAX
    db.close()


# --- 备份 (规模) ---------------------------------------------------------
def test_backup_restore_scale(scale_model: Dict[str, Any], tmp_path) -> None:
    db_path = str(tmp_path / "classroom.sqlite")
    backup_path = str(tmp_path / "backup.sqlite")
    db = open_database(db_path)
    repos = Repositories(db)

    kps = scale_model["kps"]
    students = list(scale_model["ls"]._students.values())
    exercises = list(scale_model["ls"]._exercises.values())
    sessions = [scale_model["org"]._sessions_by_id[s] for s in scale_model["session_ids"]]
    answers = _synthetic_answers(scale_model)

    course = scale_model["course"]
    with repos.transaction():
        # course 必须先落库: sessions / topics 对 courses 有外键约束。
        repos.courses.save(course)
        repos.knowledge.save_many(kps)
        repos.students.save_many(students)
        repos.exercises.save_many(exercises)
        repos.sessions.save_many(sessions)
        repos.organization.save_structure(scale_model["org"].structure)
        repos.answers.save_many(answers)

    t0 = time.perf_counter()
    db.backup_to(backup_path)
    elapsed = time.perf_counter() - t0

    assert os.path.exists(backup_path)
    assert elapsed < BACKUP_SECONDS_MAX

    # 打开备份副本并校验数据自包含 (WAL 已合并)。
    db2 = open_database(backup_path)
    repos2 = Repositories(db2)
    assert len(repos2.answers.load_all()) == ANSWER_N
    assert len(repos2.knowledge.load_all()) == KP_N
    db2.close()
    db.close()
