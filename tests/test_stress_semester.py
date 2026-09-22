# -*- coding: utf-8 -*-
"""Task 69 —— Real Semester Stress（真实学期规模压力）测试。

覆盖的 spec 条款
----------------

    69.1  真实学期规模: 5 门课 × 15 节课 × 10 份材料 = 750 份材料
    69.2  1000+ 知识点 / 100 个主题
    69.3  500 个合成学生 + 1 个"真人风格"学生 (确定性种子)
    69.4  5000 道题 / 20000 条作答
    69.5  10 次重启闭环
    69.6  3 次备份 / 3 次恢复
    69.7  100 个知识点抽样溯源审计
    69.8  跨课程泄漏审计
    69.9  数据库不得 N+1
    69.10 10 个页面的 UI 压力审计

这个文件刻意**不**做的事
------------------------

1. **不压测"响应有多快"的绝对值。** 本机同时跑着 IDE、数据库和其它
   进程，绝对耗时在机器上抖动很大，把它写成断言只会得到一条随机失败的
   测试。真正该断言的是**复杂度**：同一个操作的 SQL 条数必须与数据量
   **无关**（``TestNoNPlusOne``）以及**单条作答的 SQL 条数必须是常数**
   （``TestWriteAmplification``）。这两条是能抓住真实缺陷的判据。

2. **不为了规模而牺牲语义。** 规模是手段，不是目的：20000 条作答里
   约 1/3 是错的（``is_true=(i % 3 != 0)``），所以错题中心、学生状态、
   复习集在压力规模下仍然有真实内容，不是一堆全对的空壳。

3. **重启 / 备份测试不改动共享夹具。** 恢复一律恢复到**另一个** data_dir
   （灾难恢复语义），这样既能验证"换台机器也能还原"，又不会把模块级
   夹具写成别的测试读不到的状态。

关于耗时
--------

模块级夹具要真造 750 份材料并跑 75 次 ``process_session``，本机约 100 秒；
再加 20000 条作答约 40 秒。这是**一次性**的：夹具是 ``scope="module"``。
刻意没有为了跑得快而缩小规模 —— 缩小了就测不到这个任务要测的东西。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

import pytest

from src.api.server import create_server
from src.application.multi_course import COUNT_KEYS, MultiCourseWorkspace
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace
from src.backup import BackupService

ROOT = Path(__file__).resolve().parents[1]
NODE = os.environ.get(
    "CLASSROOM_NODE",
    r"C:/Users/Rafae/.workbuddy-ai/binaries/node/versions/22.22.2-2/node.exe",
)
UI_STRESS = ROOT / "scripts" / "ui_stress_check.js"

FIXED_TIME = "2026-09-18T09:00:00+00:00"
TODAY = "2026-09-18"

# ---------------------------------------------------------------- 规模常量

COURSES = 5
SESSIONS_PER_COURSE = 15
MATERIALS_PER_SESSION = 10
MATERIALS_TOTAL = COURSES * SESSIONS_PER_COURSE * MATERIALS_PER_SESSION  # 750

TOPIC_POOL = 100

SYNTHETIC_STUDENTS = 500  # 每门课 100
STUDENTS_PER_COURSE = SYNTHETIC_STUDENTS // COURSES
REAL_STUDENT = "stu-real"

EXERCISES_TOTAL = 5000
EXERCISES_PER_COURSE = EXERCISES_TOTAL // COURSES

ANSWERS_TOTAL = 20000
ANSWERS_PER_COURSE = ANSWERS_TOTAL // COURSES

RESTART_CYCLES = 10
BACKUP_ROUNDS = 3
TRACE_SAMPLE = 100
UI_PAGES = 10

#: Task 66 点名禁止的措辞。压力规模下"下一任务"的文案同样受此约束。
FORBIDDEN_NEXT_TASK = "You mastered this topic."

#: SQL 预算：一个"只读投影"每门课允许打的语句条数上限。
#:
#: 判据刻意不是绝对耗时（本机同时跑着 IDE 和其它进程，毫秒数抖动很大），
#: 而是"条数必须与数据量无关"。预算按课程数线性放大 —— 若哪天
#: ``my_courses`` 退化成每答案一查，20000 条作答会立刻把它顶穿。
SQL_BUDGET_PER_COURSE = 30

#: 大库小库之间允许的语句条数差。
#:
#: ``CourseLinkRepository.rights_for_many`` 用 ``IN (...)`` 分批取回溯源链，
#: 每批 500 个 id。一门课 600 个知识点走 2 批，小库 2 个知识点走 1 批 ——
#: 于是**可以多一两条**。这是 O(N/500) 的常量级差异，不是 N+1：真正 N+1
#: 的差值是 600 条（每知识点一查），一发就顶穿这个容差。
SQL_BATCH_ALLOWANCE = 2

TOPIC_RE = re.compile(r"Tema (\d+) \(")
TAG_RE = re.compile(r"\((c\d+s\d+m\d+)\)")


# =====================================================================
# 工具
# =====================================================================


def _student(semester: dict[str, Any], course_id: str, index: int) -> str:
    """这门课第 ``index`` 号合成学生的学号。"""
    return "%s-%03d" % (semester["student_prefix"][course_id], index)


def _note(directory: Path, tag: str, topic_a: int, topic_b: int) -> str:
    """写一份包含**两个**主题的材料并返回路径。

    ``tag`` 进标题（冒号前），因为 ``knowledge_pipeline._entity_anchor``
    会把标题在 ``. / ; / :`` 处截断 —— 标签必须落在截断**之前**，否则
    不同材料的知识点会撞成同一个 id（Task 68 踩过）。
    """
    path = directory / ("apuntes-%s.md" % tag)
    path.write_text(
        "## Tema %d (%s): Concepto\n\n"
        "El concepto %s describe una relacion entre dos elementos del tema %d.\n\n"
        "## Tema %d (%s-segon): Definicio\n\n"
        "La definicio %s establece una propiedad observable del tema %d.\n"
        % (
            topic_a,
            tag,
            tag,
            topic_a,
            topic_b,
            tag,
            tag,
            topic_b,
        ),
        encoding="utf-8",
    )
    return str(path)


def _open(data_dir: str) -> Workspace:
    return Workspace(
        data_dir,
        clock=fixed_clock(FIXED_TIME),
        asr_mode="mock",
        ocr_mode="mock",
    )


def _fingerprint(workspace: Workspace, course_ids: list[str]) -> str:
    """整个学期的业务内容指纹（跨重启必须逐字节不变）。"""
    digest = hashlib.sha256()
    for course_id in sorted(course_ids):
        digest.update(course_id.encode("utf-8"))
        digest.update(
            "|".join(
                sorted(
                    str(point["knowledge_id"])
                    for point in workspace.knowledge_points(course_id)
                )
            ).encode("utf-8")
        )
        digest.update(
            "|".join(
                sorted(
                    str(row["material_id"])
                    for row in workspace.list_materials(course_id)
                )
            ).encode("utf-8")
        )
        digest.update(
            "|".join(
                sorted(
                    str(row["exercise_id"])
                    for row in workspace.list_exercises(course_id)
                )
            ).encode("utf-8")
        )
        digest.update(
            "|".join(
                sorted(
                    str(row["student_id"]) for row in workspace.list_students(course_id)
                )
            ).encode("utf-8")
        )
        digest.update(str(workspace.course_summary(course_id)).encode("utf-8"))
    return digest.hexdigest()


def _backup_service(data_dir: str, stamp: str) -> BackupService:
    """带**固定**时间戳的备份服务。

    归档文件名来自时钟，所以同一秒内连做三次备份会撞
    ``DuplicateBackupError``。这里给每一轮一个不同的时钟值 —— 三轮备份
    必须是**三个**归档，而不是同一个文件被覆盖三次。
    """
    return BackupService(data_dir, clock=lambda: stamp)


@contextlib.contextmanager
def _sql_count(workspace: Workspace) -> Iterator[list[int]]:
    """统计一段代码里真正打到 sqlite 的**语句条数**。

    用 ``sqlite3.Connection.set_trace_callback`` 而不是给 ``Database``
    打猴子补丁：后者漏得掉 ``query_one`` / ``scalar``（它们直接走
    ``connect().execute``），而 trace 回调是驱动层，一条都漏不掉。
    """
    connection = workspace.persistence.database.connect()
    counter = [0]

    def _trace(statement: str) -> None:
        counter[0] += 1

    connection.set_trace_callback(_trace)
    try:
        yield counter
    finally:
        connection.set_trace_callback(None)


def _count_sql(workspace: Workspace, action: Callable[[], Any]) -> int:
    with _sql_count(workspace) as counter:
        action()
    return counter[0]


# =====================================================================
# 夹具：真实学期
# =====================================================================


def _build(data_dir: str) -> dict[str, Any]:
    """造一整个学期，返回规模统计（工作区已关闭，重开才能验证持久化）。"""
    root = Path(data_dir)
    notes = root / "notes"
    notes.mkdir(parents=True, exist_ok=True)

    workspace = _open(data_dir)
    course_ids: list[str] = []
    session_ids: list[str] = []
    material_ids: list[str] = []
    exercise_ids: list[str] = []
    answer_ids: list[str] = []
    try:
        # --- 1) 课程 / 课堂 / 材料 / 处理 --------------------------------
        material_index = 0
        for course_index in range(COURSES):
            cid = workspace.create_course(
                "Assignatura %d" % course_index, "A%d" % course_index, "es"
            )["course_id"]
            course_ids.append(cid)
            for session_number in range(1, SESSIONS_PER_COURSE + 1):
                sid = workspace.create_session(
                    cid,
                    session_number=session_number,
                    date=TODAY,
                    title="Tema %d" % session_number,
                )["session_id"]
                session_ids.append(sid)
                for material_number in range(MATERIALS_PER_SESSION):
                    tag = "c%ds%dm%d" % (course_index, session_number, material_number)
                    topic_a = (material_index * 2) % TOPIC_POOL
                    topic_b = (material_index * 2 + 37) % TOPIC_POOL
                    material_ids.append(
                        workspace.register_material(
                            cid, _note(notes, tag, topic_a, topic_b), session_id=sid
                        )["material_id"]
                    )
                    material_index += 1
                workspace.process_session(cid, sid)

        knowledge_counts = {
            cid: len(workspace.knowledge_points(cid)) for cid in course_ids
        }
        topic_labels: set[str] = set()
        for cid in course_ids:
            for point in workspace.knowledge_points(cid):
                topic_labels.update(TOPIC_RE.findall(str(point.get("title") or "")))

        # --- 2) 学生 -----------------------------------------------------
        #
        # 学号里带的是**课程序号**，不是 ``course_id[:6]`` —— 后者会撞车
        # （内容寻址的课程 id 前缀不保证唯一），一撞车"两门课共享学生"
        # 就成了假阳性。
        for course_index, cid in enumerate(course_ids):
            for index in range(STUDENTS_PER_COURSE):
                workspace.create_student(
                    cid, "syn-c%d-%03d" % (course_index, index), "S%03d" % index
                )
        # 真人风格学生只在第一门课 —— spec 是 "500 + 1"，不是 "每门课 1 个"。
        workspace.create_student(course_ids[0], REAL_STUDENT, "Estudiant Real")

        # --- 3) 练习 -----------------------------------------------------
        for cid in course_ids:
            own = [
                str(point["knowledge_id"]) for point in workspace.knowledge_points(cid)
            ]
            for index in range(EXERCISES_PER_COURSE):
                exercise_ids.append(
                    workspace.create_exercise(
                        cid,
                        "true_false",
                        "Pregunta %d de %s" % (index, cid[:6]),
                        [own[index % len(own)]],
                        is_true=(index % 3 != 0),
                    )["exercise_id"]
                )

        # --- 4) 合成作答 --------------------------------------------------
        #
        # answer_id 是内容寻址的（学生 + 练习 + 作答值 + sequence）。若只按
        # ``i % n`` 循环，同一个四元组会在不同 i 上重复，答案日志按 id 去重
        # 之后实际只剩 1000 条 —— "20000 条作答"就变成了谎言。因此用
        # ``sequence = i // 每门课题目数`` 把四元组做成双射。
        synthetic_answers = 0
        for course_index, cid in enumerate(course_ids):
            own = [
                row["exercise_id"] for row in workspace.list_exercises(cid)
            ]
            for index in range(ANSWERS_PER_COURSE):
                student = "syn-c%d-%03d" % (
                    course_index,
                    index % STUDENTS_PER_COURSE,
                )
                exercise = own[index % len(own)]
                answer_ids.append(
                    workspace.submit_answer(
                        cid,
                        student,
                        exercise,
                        "true" if index % 2 == 0 else "false",
                        sequence=index // len(own),
                    )["answer_id"]
                )
            synthetic_answers += workspace.course_summary(cid)["counts"]["answers"]

        # --- 5) 真人风格学生走一遍每日流程 --------------------------------
        real_steps = _run_real_student(workspace, course_ids[0])

        payload = {
            "data_dir": data_dir,
            "course_ids": course_ids,
            "student_prefix": {
                cid: "syn-c%d" % index for index, cid in enumerate(course_ids)
            },
            "session_ids": session_ids,
            "material_ids": material_ids,
            "exercise_ids": exercise_ids,
            "answer_ids": answer_ids,
            "knowledge_counts": knowledge_counts,
            "topics": sorted(topic_labels),
            "synthetic_answers": synthetic_answers,
            "real_steps": real_steps,
            "real_answers": len(real_steps),
        }
    finally:
        workspace.close()
    return payload


def _run_real_student(workspace: Workspace, course_id: str) -> list[dict[str, Any]]:
    """一个"真人风格"学生按 Task 66 的每日流程学 3 个知识点。

    确定性：任务选择本身是确定性的，这里不给任何随机种子。学生每一步
    都答**错**（选与正确答案相反的值），这样错题中心与"答错不等于薄弱"
    这两条语义在压力规模下也有真实内容可查。
    """
    steps: list[dict[str, Any]] = []
    start = workspace.learning_workflow_start(course_id, REAL_STUDENT)
    seen: set[str] = set()
    for _ in range(12):
        next_task = start.get("next_task") or {}
        knowledge_id = next_task.get("knowledge_point_id")
        if not knowledge_id or knowledge_id in seen:
            start = workspace.learning_workflow_start(course_id, REAL_STUDENT)
            next_task = start.get("next_task") or {}
            knowledge_id = next_task.get("knowledge_point_id")
            if not knowledge_id or knowledge_id in seen:
                break
        seen.add(knowledge_id)
        opened = workspace.learning_workflow_open_knowledge(
            course_id, REAL_STUDENT, knowledge_id
        )
        exercise = workspace.learning_workflow_exercise(
            course_id, REAL_STUDENT, knowledge_id
        )
        exercise_id = str(exercise["exercise"]["exercise_id"])
        # 故意答错：取与答案键相反的那个值。
        expected = "true" if exercise["exercise"].get("is_true") else "false"
        submitted = "false" if expected == "true" else "true"
        answered = workspace.learning_workflow_answer(
            course_id, REAL_STUDENT, exercise_id, submitted
        )
        steps.append(
            {
                "knowledge_id": knowledge_id,
                "exercise_id": exercise_id,
                "answer_id": str((answered.get("answer") or {}).get("answer_id")),
                "evaluation_status": answered.get("evaluation_status"),
                "next_task_text": json.dumps(
                    answered.get("next_task"), ensure_ascii=False, sort_keys=True
                ),
                "opened_state": json.dumps(
                    opened.get("student_state"), ensure_ascii=False, sort_keys=True
                ),
            }
        )
        start = workspace.learning_workflow_start(course_id, REAL_STUDENT)
        if len(seen) >= 3:
            break
    return steps


@pytest.fixture(scope="module")
def semester(tmp_path_factory) -> dict[str, Any]:
    """一整个学期。只造一次，供**只读**测试共用。"""
    data_dir = str(tmp_path_factory.mktemp("semester") / "data")
    return _build(data_dir)


@pytest.fixture
def ws(semester):
    workspace = _open(semester["data_dir"])
    yield workspace
    workspace.close()


@pytest.fixture(scope="module")
def tiny(tmp_path_factory) -> dict[str, Any]:
    """同形状但极小的学期（1 门课 / 1 节课 / 1 份材料 / 1 学生 / 1 题 / 1 答）。

    N+1 判据要用它：同一个操作的 SQL 条数在大库和小库上必须**一样**。
    """
    data_dir = str(tmp_path_factory.mktemp("tiny") / "data")
    root = Path(data_dir)
    notes = root / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    workspace = _open(data_dir)
    try:
        cid = workspace.create_course("Petita", "P", "es")["course_id"]
        sid = workspace.create_session(
            cid, session_number=1, date=TODAY, title="Tema 1"
        )["session_id"]
        workspace.register_material(
            cid, _note(notes, "c0s1m0", 0, 37), session_id=sid
        )
        workspace.process_session(cid, sid)
        workspace.create_student(cid, "syn-petit-000", "S000")
        kp = workspace.knowledge_points(cid)[0]["knowledge_id"]
        exercise_id = workspace.create_exercise(
            cid, "true_false", "Pregunta 0", [kp], is_true=True
        )["exercise_id"]
        workspace.submit_answer(cid, "syn-petit-000", exercise_id, "true")
    finally:
        workspace.close()
    return {
        "data_dir": data_dir,
        "course_id": cid,
        "student_id": "syn-petit-000",
        "exercise_id": exercise_id,
    }


@pytest.fixture
def tiny_ws(tiny):
    workspace = _open(tiny["data_dir"])
    yield workspace
    workspace.close()


# =====================================================================
# 69.1 / 69.2  真实学期规模
# =====================================================================


class TestSemesterScale:
    def test_five_courses(self, ws) -> None:
        assert len(ws.list_courses()) == COURSES

    def test_seventy_five_sessions(self, ws, semester) -> None:
        total = sum(len(ws.list_sessions(cid)) for cid in semester["course_ids"])
        assert total == COURSES * SESSIONS_PER_COURSE

    def test_seven_hundred_fifty_materials(self, ws, semester) -> None:
        total = sum(len(ws.list_materials(cid)) for cid in semester["course_ids"])
        assert total == MATERIALS_TOTAL

    def test_every_course_has_the_same_number_of_materials(self, ws, semester) -> None:
        counts = {len(ws.list_materials(cid)) for cid in semester["course_ids"]}
        assert counts == {MATERIALS_TOTAL // COURSES}

    def test_more_than_a_thousand_knowledge_points(self, ws, semester) -> None:
        total = sum(len(ws.knowledge_points(cid)) for cid in semester["course_ids"])
        assert total >= 1000, total

    def test_knowledge_points_are_evenly_built(self, ws, semester) -> None:
        """五门课结构相同 -> 知识点数必须相同（装配作用域没串课）。"""
        counts = {len(ws.knowledge_points(cid)) for cid in semester["course_ids"]}
        assert len(counts) == 1, counts

    def test_one_hundred_topics_are_covered(self, semester) -> None:
        assert len(semester["topics"]) >= TOPIC_POOL, len(semester["topics"])

    def test_five_hundred_synthetic_students_plus_one_real(self, ws, semester) -> None:
        synthetic = 0
        real = 0
        for cid in semester["course_ids"]:
            for row in ws.list_students(cid):
                if row["student_id"] == REAL_STUDENT:
                    real += 1
                else:
                    synthetic += 1
        assert synthetic == SYNTHETIC_STUDENTS, synthetic
        assert real == 1, real

    def test_five_thousand_exercises(self, ws, semester) -> None:
        total = sum(len(ws.list_exercises(cid)) for cid in semester["course_ids"])
        assert total == EXERCISES_TOTAL, total

    def test_twenty_thousand_answers(self, ws, semester) -> None:
        total = sum(
            ws.course_summary(cid)["counts"]["answers"] for cid in semester["course_ids"]
        )
        assert total == ANSWERS_TOTAL + semester["real_answers"], total

    def test_the_synthetic_phase_alone_is_twenty_thousand(self, semester) -> None:
        """合成阶段本身就是 20000 条 —— 真人学生那几步是**额外**的。"""
        assert semester["synthetic_answers"] == ANSWERS_TOTAL

    def test_answers_are_not_all_correct(self, ws, semester) -> None:
        """压力规模下"错题中心"必须有真东西可查。

        全部答对的数据集是测不出错题投影的：它只会渲染一个空壳，而空壳
        照样"不抛异常"。所以合成作答里刻意混了约 1/3 的错误。
        """
        wrong = 0
        for cid in semester["course_ids"][:2]:
            center = ws.mistakes_center(cid, _student(semester, cid, 0))
            wrong += len(center.get("groups") or center.get("items") or [])
        assert wrong > 0

    def test_the_database_file_is_real_and_not_tiny(self, semester) -> None:
        db_path = Path(semester["data_dir"]) / "database" / "classroom.sqlite"
        assert db_path.exists()
        assert db_path.stat().st_size > 1_000_000, db_path.stat().st_size

    def test_material_integrity_reports_no_missing_files(self, ws) -> None:
        report = ws.material_integrity()
        assert report.get("missing") in (None, 0, []), report


# =====================================================================
# 写放大（Task 69 实测出来的真实缺陷）
# =====================================================================


class TestWriteAmplification:
    """单条操作的 SQL 条数必须与**课内有多少别人的数据**无关。

    Task 69 实测出**两**处写放大，两处都修掉了，两处都在这里钉住：

    1. **课内别人的数据**：``submit_answer`` 会把整门课的练习、全部学生
       的日志、全部答案与评估重写一遍 —— 单条作答 68ms，20000 条是
       O(N²) 条 SQL，一门课答完要 23 分钟。
    2. **这个学生自己的历史**：``save_student_log`` 把该学生**全部历史
       事件**逐条 upsert 一遍。实测一个学生答到第 400 条时，提交下一条
       要 410 条 SQL —— 一个认真学习的学生就能把整学期拖成 O(N²)。

    第 2 条曾经被写进类文档说成"append-only 的固有语义，不是缺陷"。
    那是错的：事件确实 append-only，但"把已有的再 upsert 一遍"不是
    append-only 的要求 —— ``event_id`` 由内容派生，跳过已落盘的事件与
    重写一遍**逐字节等价**。所以它是可以修的，也就必须修。
    """

    @staticmethod
    def _seed(workspace: Workspace, course_id: str) -> None:
        workspace.create_session(
            course_id, session_number=1, date=TODAY, title="T1"
        )

    @staticmethod
    def _grow(workspace: Workspace, course_id: str, size: int) -> None:
        """往课里塞 ``size`` 个**别的**学生 + 练习 + 作答。"""
        kps = [str(p["knowledge_id"]) for p in workspace.knowledge_points(course_id)]
        exercise_ids = []
        for index in range(size):
            workspace.create_student(course_id, "amp-other-%04d" % index, "O")
            exercise_ids.append(
                workspace.create_exercise(
                    course_id,
                    "true_false",
                    "Amp %d" % index,
                    [kps[index % len(kps)]],
                    is_true=True,
                )["exercise_id"]
            )
        for index in range(size):
            workspace.submit_answer(
                course_id, "amp-other-%04d" % index, exercise_ids[index], "true"
            )

    @staticmethod
    def _course(workspace: Workspace, name: str, notes: Path) -> str:
        cid = workspace.create_course(name, name[:2], "es")["course_id"]
        sid = workspace.create_session(
            cid, session_number=1, date=TODAY, title="T1"
        )["session_id"]
        notes.mkdir(parents=True, exist_ok=True)
        workspace.register_material(cid, _note(notes, "c0s1m0", 0, 37), session_id=sid)
        workspace.process_session(cid, sid)
        return cid

    def test_a_single_answer_costs_a_constant_number_of_statements(self, tmp_path):
        workspace = _open(str(tmp_path / "data"))
        try:
            cid = self._course(workspace, "Amp", tmp_path / "notes")
            kp = workspace.knowledge_points(cid)[0]["knowledge_id"]
            exercise_id = workspace.create_exercise(
                cid, "true_false", "Pregunta", [kp], is_true=True
            )["exercise_id"]

            def measure(tag: str) -> int:
                workspace.create_student(cid, "amp-%s" % tag, tag)
                return _count_sql(
                    workspace,
                    lambda: workspace.submit_answer(cid, "amp-%s" % tag, exercise_id, "true"),
                )

            # 预热: 第一次写某张表时 sqlite 驱动会先探一次表结构
            # (PRAGMA table_info), 那是一次性的, 不该算进"每条作答的成本"。
            measure("warm")
            before = measure("a")
            self._grow(workspace, cid, 120)
            after = measure("b")
            assert after == before, (
                f"单条作答的 SQL 条数随课内数据量增长了: {before} -> {after}"
            )
            assert after <= 20, after
        finally:
            workspace.close()

    def test_a_single_answer_does_not_rewrite_the_students_own_history(self, tmp_path):
        """一个学生答到第 N 条时，提交**下**一条的代价不得随 N 增长。

        这条守的是第 2 处写放大（学生自己的历史），与上面几条守的"课内有
        多少别人"不是一回事。判据直接看增长曲线：若干个观测点上条数必须
        全都相等 —— 修复前是 12/20/60/110/210/310/410，一发就露。
        """
        workspace = _open(str(tmp_path / "data"))
        try:
            cid = self._course(workspace, "Amp1b", tmp_path / "notes")
            kps = [str(p["knowledge_id"]) for p in workspace.knowledge_points(cid)]
            exercise_id = workspace.create_exercise(
                cid, "true_false", "Pregunta", [kps[0]], is_true=True
            )["exercise_id"]
            workspace.create_student(cid, "amp-history", "H")

            seen: dict[int, int] = {}
            for index in range(60):
                with _sql_count(workspace) as counter:
                    workspace.submit_answer(
                        cid,
                        "amp-history",
                        exercise_id,
                        "true" if index % 2 == 0 else "false",
                        sequence=index,
                    )
                if index in (0, 1, 9, 19, 39, 59):
                    seen[index + 1] = counter[0]

            # 头一条含一次性的表结构探测（PRAGMA table_info），单独比。
            steady = {n: c for n, c in seen.items() if n > 1}
            assert len(set(steady.values())) == 1, (
                f"单条作答的 SQL 条数随这个学生的历史增长了: {seen}"
            )
            assert max(steady.values()) <= 20, seen
        finally:
            workspace.close()

    def test_creating_an_exercise_costs_a_constant_number_of_statements(self, tmp_path):
        workspace = _open(str(tmp_path / "data"))
        try:
            cid = self._course(workspace, "Amp2", tmp_path / "notes")
            kps = [str(p["knowledge_id"]) for p in workspace.knowledge_points(cid)]

            def measure(prompt: str) -> int:
                return _count_sql(
                    workspace,
                    lambda: workspace.create_exercise(
                        cid, "true_false", prompt, [kps[0]], is_true=True
                    ),
                )

            measure("Calentament")
            before = measure("Primera")
            self._grow(workspace, cid, 100)
            after = measure("Ultima")
            assert after == before, f"{before} -> {after}"
            assert after <= 20, after
        finally:
            workspace.close()

    def test_recording_a_learning_event_does_not_rewrite_the_course(self, tmp_path):
        workspace = _open(str(tmp_path / "data"))
        try:
            cid = self._course(workspace, "Amp3", tmp_path / "notes")
            kps = [str(p["knowledge_id"]) for p in workspace.knowledge_points(cid)]

            def measure(tag: str) -> int:
                workspace.create_student(cid, "amp-%s" % tag, tag)
                return _count_sql(
                    workspace,
                    lambda: workspace.record_learning_event(
                        cid, "amp-%s" % tag, kps[0], "viewed"
                    ),
                )

            measure("warm")
            before = measure("a")
            self._grow(workspace, cid, 60)
            after = measure("b")
            assert after == before, f"{before} -> {after}"
            # 一个事件 = 写这一个学生的日志。与课内有多少学生无关。
            assert after <= 20, after
        finally:
            workspace.close()

    def test_a_student_creation_does_not_rewrite_the_course(self, tmp_path):
        workspace = _open(str(tmp_path / "data"))
        try:
            cid = self._course(workspace, "Amp4", tmp_path / "notes")
            kps = [str(p["knowledge_id"]) for p in workspace.knowledge_points(cid)]
            for index in range(40):
                workspace.create_exercise(
                    cid, "true_false", "P %d" % index, [kps[0]], is_true=True
                )
            _count_sql(workspace, lambda: workspace.create_student(cid, "amp-warm", "W"))
            before = _count_sql(
                workspace, lambda: workspace.create_student(cid, "amp-a", "A")
            )
            self._grow(workspace, cid, 40)
            after = _count_sql(
                workspace, lambda: workspace.create_student(cid, "amp-b", "B")
            )
            assert after == before, f"{before} -> {after}"
            assert after <= 20, after
        finally:
            workspace.close()

    def test_the_student_log_write_touches_only_that_student(self, tmp_path):
        """落盘只碰**这一个**学生的事件行 —— 别人的日志一个字节都不动。

        这条直接查数据库，而不是数 SQL 条数：条数相等也可能是"两边都写了
        同样多的垃圾"。这里看的是**写了谁的**。
        """
        workspace = _open(str(tmp_path / "data"))
        try:
            cid = self._course(workspace, "Amp5", tmp_path / "notes")
            kp = workspace.knowledge_points(cid)[0]["knowledge_id"]
            exercise_id = workspace.create_exercise(
                cid, "true_false", "Pregunta", [kp], is_true=True
            )["exercise_id"]
            self._grow(workspace, cid, 30)
            workspace.create_student(cid, "amp-me", "Me")

            def others() -> list[tuple]:
                return [
                    tuple(row)
                    for row in workspace.persistence.database.query(
                        "SELECT course_id, event_id, event_type, knowledge_point_id, "
                        "sequence, student_id, payload FROM learning_events "
                        "WHERE student_id LIKE 'amp-other-%' "
                        "ORDER BY student_id, event_id"
                    )
                ]

            before = others()
            assert before, "前置数据没落盘，这条测试就测不到东西"
            workspace.submit_answer(cid, "amp-me", exercise_id, "true")
            assert others() == before, "提交一条作答改写了别的学生的日志"

            rows_me = workspace.persistence.database.query(
                "SELECT COUNT(*) AS n FROM learning_events WHERE student_id = 'amp-me'"
            )
            assert rows_me[0]["n"] > 0
        finally:
            workspace.close()


# =====================================================================
# 69.9  数据库不得 N+1
# =====================================================================


class TestNoNPlusOne:
    """同一个读操作的 SQL 条数必须与数据量**无关**。

    判据是"大库 vs 小库条数相等"，而不是"小于某个数"：后者会把常数很大
    但恒定的实现判成及格，也会把真正随数据增长的实现漏掉。
    """

    OPS: tuple[tuple[str, str], ...] = (
        ("knowledge_points", "knowledge_points"),
        ("list_materials", "list_materials"),
        ("list_students", "list_students"),
        ("list_exercises", "list_exercises"),
        ("course_summary", "course_summary"),
        ("knowledge_summary", "knowledge_summary"),
        ("coverage", "coverage"),
        ("review_candidates", "review_candidates"),
        ("dashboard", "dashboard"),
    )

    @staticmethod
    def _run(workspace: Workspace, course_id: str, name: str) -> int:
        method = getattr(workspace, name)
        with _sql_count(workspace) as counter:
            method(course_id)
        return counter[0]

    def test_single_course_reads_cost_the_same_on_big_and_small(
        self, ws, semester, tiny_ws, tiny
    ) -> None:
        big_id = semester["course_ids"][0]
        small_id = tiny["course_id"]
        for _label, name in self.OPS:
            big = self._run(ws, big_id, name)
            small = self._run(tiny_ws, small_id, name)
            assert abs(big - small) <= SQL_BATCH_ALLOWANCE, (
                f"{name}: 大库 {big} 条 vs 小库 {small} 条 —— 差值超过了 "
                f"IN(...) 分批能解释的范围, 这就是 N+1"
            )

    def test_my_courses_scales_with_the_number_of_courses_only(
        self, ws, semester, tiny_ws, tiny
    ) -> None:
        """``my_courses`` 每门课查各的 -> 条数应与课程数成正比，与数据量无关。"""
        big = _count_sql(ws, lambda: MultiCourseWorkspace(ws).my_courses())
        small = _count_sql(
            tiny_ws, lambda: MultiCourseWorkspace(tiny_ws).my_courses()
        )
        per_course_big = big / COURSES
        assert abs(per_course_big - small) <= 2, (
            f"每门课的 SQL 条数不一致: 大库 {per_course_big} vs 小库 {small}"
        )

    def test_my_courses_does_not_query_per_answer(self, ws, semester) -> None:
        """20000 条作答绝不能变成 20000 条查询。

        这里给的是**预算**而不是"小于答案数除以十"这种比例：后者在规模
        缩小时会先把自己判死（答案少 -> 阈值小 -> 恒定开销也能顶穿），
        抓不到真正的缺陷。预算按课程数线性放大，因为 ``my_courses`` 本来
        就是每门课各查各的。
        """
        count = _count_sql(ws, lambda: MultiCourseWorkspace(ws).my_courses())
        assert count <= SQL_BUDGET_PER_COURSE * COURSES, count

    def test_student_state_is_not_computed_per_knowledge_point(
        self, ws, semester, tiny_ws, tiny
    ) -> None:
        """``student_state`` 的 SQL 条数必须与注册了多少知识点**无关**。

        判据是大库小库**相等**，而不是"小于某个常数"：常数会被实现细节
        （比如惰性加载整门课那一发）顶穿，而"相等"抓的正是 N+1 本身。
        """
        cid = semester["course_ids"][0]
        big = _count_sql(ws, lambda: ws.student_state(cid, _student(semester, cid, 0)))
        small = _count_sql(
            tiny_ws,
            lambda: tiny_ws.student_state(tiny["course_id"], tiny["student_id"]),
        )
        assert abs(big - small) <= SQL_BATCH_ALLOWANCE, (
            f"大库 {big} 条 vs 小库 {small} 条 —— 这就是 N+1"
        )

    def test_the_mistakes_center_does_not_query_per_answer(
        self, ws, semester, tiny_ws, tiny
    ) -> None:
        """错题中心的 SQL 条数不得随作答数增长 —— 大库小库必须相等。"""
        cid = semester["course_ids"][0]
        big = _count_sql(
            ws, lambda: ws.mistakes_center(cid, _student(semester, cid, 0))
        )
        small = _count_sql(
            tiny_ws,
            lambda: tiny_ws.mistakes_center(
                tiny["course_id"], tiny["student_id"]
            ),
        )
        assert abs(big - small) <= SQL_BATCH_ALLOWANCE, (
            f"大库 {big} 条 vs 小库 {small} 条 —— 这就是 N+1"
        )


# =====================================================================
# 69.5  10 次重启闭环
# =====================================================================


class TestRestartCycles:
    def test_ten_restarts_keep_the_same_fingerprint(self, semester) -> None:
        course_ids = semester["course_ids"]
        seen: set[str] = set()
        for _ in range(RESTART_CYCLES):
            workspace = _open(semester["data_dir"])
            try:
                seen.add(_fingerprint(workspace, course_ids))
            finally:
                workspace.close()
        assert len(seen) == 1, "十次重启读到了不同的内容"

    def test_ten_restarts_keep_every_count(self, semester) -> None:
        expected: Optional[dict[str, int]] = None
        for _ in range(RESTART_CYCLES):
            workspace = _open(semester["data_dir"])
            try:
                counts = {
                    cid: {
                        "knowledge": len(workspace.knowledge_points(cid)),
                        "materials": len(workspace.list_materials(cid)),
                        "exercises": len(workspace.list_exercises(cid)),
                        "students": len(workspace.list_students(cid)),
                        "answers": workspace.course_summary(cid)["counts"]["answers"],
                    }
                    for cid in semester["course_ids"]
                }
            finally:
                workspace.close()
            if expected is None:
                expected = counts
            assert counts == expected

    def test_ten_restarts_keep_the_answer_history(self, semester) -> None:
        cid = semester["course_ids"][0]
        student = _student(semester, cid, 3)
        baseline: Optional[list[str]] = None
        for _ in range(RESTART_CYCLES):
            workspace = _open(semester["data_dir"])
            try:
                log = workspace.context(cid).learning_service.answer_log_for(student)
                ids = sorted(str(row["answer_id"]) for row in log)
            finally:
                workspace.close()
            if baseline is None:
                baseline = ids
            assert ids == baseline

    def test_a_write_survives_ten_restarts(self, tmp_path) -> None:
        """重启闭环要"带着写"跑：每次重启后写一条，10 次后一条都不能丢。"""
        data_dir = str(tmp_path / "data")
        root = Path(data_dir)
        notes = root / "notes"
        notes.mkdir(parents=True, exist_ok=True)
        workspace = _open(data_dir)
        cid = workspace.create_course("Cicle", "C", "es")["course_id"]
        sid = workspace.create_session(
            cid, session_number=1, date=TODAY, title="T1"
        )["session_id"]
        workspace.register_material(cid, _note(notes, "c0s1m0", 0, 37), session_id=sid)
        workspace.process_session(cid, sid)
        workspace.create_student(cid, "stu-cicle", "C")
        kp = workspace.knowledge_points(cid)[0]["knowledge_id"]
        exercise_id = workspace.create_exercise(
            cid, "true_false", "P", [kp], is_true=True
        )["exercise_id"]
        workspace.close()

        for index in range(RESTART_CYCLES):
            reopened = _open(data_dir)
            try:
                assert (
                    reopened.course_summary(cid)["counts"]["answers"] == index
                ), f"第 {index} 次重启后答案数不对"
                reopened.submit_answer(cid, "stu-cicle", exercise_id, "true", sequence=index)
            finally:
                reopened.close()

        final = _open(data_dir)
        try:
            assert final.course_summary(cid)["counts"]["answers"] == RESTART_CYCLES
        finally:
            final.close()

    def test_a_learning_event_survives_ten_restarts(self, tmp_path) -> None:
        data_dir = str(tmp_path / "data")
        root = Path(data_dir)
        notes = root / "notes"
        notes.mkdir(parents=True, exist_ok=True)
        workspace = _open(data_dir)
        cid = workspace.create_course("Cicle2", "C", "es")["course_id"]
        sid = workspace.create_session(
            cid, session_number=1, date=TODAY, title="T1"
        )["session_id"]
        workspace.register_material(cid, _note(notes, "c0s1m0", 0, 37), session_id=sid)
        workspace.process_session(cid, sid)
        workspace.create_student(cid, "stu-cicle2", "C")
        kp = workspace.knowledge_points(cid)[0]["knowledge_id"]
        workspace.close()

        for index in range(RESTART_CYCLES):
            reopened = _open(data_dir)
            try:
                reopened.record_learning_event(cid, "stu-cicle2", kp, "viewed")
                state = reopened.student_state(cid, "stu-cicle2")
                assert state is not None
            finally:
                reopened.close()

        final = _open(data_dir)
        try:
            log = final.context(cid).learning_service._student_logs["stu-cicle2"]
            assert log is not None
        finally:
            final.close()

    def test_reopening_the_semester_stays_bounded(self, semester) -> None:
        """重开一整个学期不能把 20000 条作答全部读进内存后才返回。

        这里不断言耗时（机器抖动大），而是断言重开后**立即可用**：课程数
        与知识点数在不触碰作答历史的情况下就能查到。
        """
        workspace = _open(semester["data_dir"])
        try:
            assert len(workspace.list_courses()) == COURSES
            assert (
                len(workspace.knowledge_points(semester["course_ids"][0]))
                == semester["knowledge_counts"][semester["course_ids"][0]]
            )
        finally:
            workspace.close()


# =====================================================================
# 69.6  3 次备份 / 3 次恢复
# =====================================================================


class TestBackupRestore:
    def test_three_backups_then_three_restores_into_fresh_dirs(
        self, semester, tmp_path
    ) -> None:
        """灾难恢复语义：备份源库 -> 恢复到**另一个** data_dir -> 内容一致。"""
        source_dir = semester["data_dir"]
        archives = []
        for _round in range(BACKUP_ROUNDS):
            service = _backup_service(
                source_dir, "2026-09-18T09:%02d:00+00:00" % (10 + _round)
            )
            archives.append(service.create_backup().archive_path)
        assert len(archives) == BACKUP_ROUNDS

        reference = _open(source_dir)
        try:
            expected = {
                cid: {
                    "knowledge": len(reference.knowledge_points(cid)),
                    "materials": len(reference.list_materials(cid)),
                    "exercises": len(reference.list_exercises(cid)),
                    "students": len(reference.list_students(cid)),
                    "answers": reference.course_summary(cid)["counts"]["answers"],
                }
                for cid in semester["course_ids"]
            }
            expected_courses = sorted(
                str(row["course_id"]) for row in reference.list_courses()
            )
        finally:
            reference.close()

        for index, archive in enumerate(archives):
            target = str(tmp_path / ("restored-%d" % index))
            target_service = BackupService(target)
            restored = target_service.restore_backup(archive)
            assert restored is not None
            workspace = _open(target)
            try:
                assert sorted(
                    str(row["course_id"]) for row in workspace.list_courses()
                ) == expected_courses
                actual = {
                    cid: {
                        "knowledge": len(workspace.knowledge_points(cid)),
                        "materials": len(workspace.list_materials(cid)),
                        "exercises": len(workspace.list_exercises(cid)),
                        "students": len(workspace.list_students(cid)),
                        "answers": workspace.course_summary(cid)["counts"]["answers"],
                    }
                    for cid in semester["course_ids"]
                }
                assert actual == expected, f"第 {index} 份归档恢复后内容不一致"
            finally:
                workspace.close()

    def test_restored_material_files_keep_their_bytes(self, semester, tmp_path) -> None:
        source_dir = semester["data_dir"]
        archive = _backup_service(
            source_dir, "2026-09-18T09:20:00+00:00"
        ).create_backup().archive_path

        source_notes = sorted(
            (p.name, p.read_bytes())
            for p in (Path(source_dir) / "documents").rglob("*")
            if p.is_file()
        )[:20]

        target = str(tmp_path / "restored-bytes")
        BackupService(target).restore_backup(archive)
        target_notes = sorted(
            (p.name, p.read_bytes())
            for p in (Path(target) / "documents").rglob("*")
            if p.is_file()
        )[:20]
        assert target_notes == source_notes

    def test_restored_answers_are_still_course_scoped(self, semester, tmp_path) -> None:
        source_dir = semester["data_dir"]
        archive = _backup_service(
            source_dir, "2026-09-18T09:30:00+00:00"
        ).create_backup().archive_path

        target = str(tmp_path / "restored-scope")
        BackupService(target).restore_backup(archive)
        workspace = _open(target)
        try:
            first, second = semester["course_ids"][0], semester["course_ids"][1]
            assert workspace.course_summary(first)["counts"]["answers"] > 0
            assert workspace.course_summary(second)["counts"]["answers"] > 0
            log_first = workspace.context(first).learning_service.answer_log_for(
                _student(semester, first, 0)
            )
            log_second = workspace.context(second).learning_service.answer_log_for(
                _student(semester, second, 0)
            )
            ids_first = {str(row["answer_id"]) for row in log_first}
            ids_second = {str(row["answer_id"]) for row in log_second}
            assert ids_first and ids_second
            assert not (ids_first & ids_second), "恢复之后两门课的答案串了"
        finally:
            workspace.close()

    def test_three_sequential_round_trips_keep_the_fingerprint(
        self, semester, tmp_path
    ) -> None:
        """备份 -> 恢复 -> 再备份 -> 再恢复：指纹必须一路不变。"""
        reference = _open(semester["data_dir"])
        try:
            expected = _fingerprint(reference, semester["course_ids"])
        finally:
            reference.close()

        current = semester["data_dir"]
        for index in range(BACKUP_ROUNDS):
            archive = _backup_service(
                current, "2026-09-18T10:%02d:00+00:00" % (10 + index)
            ).create_backup().archive_path
            target = str(tmp_path / ("round-%d" % index))
            BackupService(target).restore_backup(archive)
            workspace = _open(target)
            try:
                assert _fingerprint(workspace, semester["course_ids"]) == expected
            finally:
                workspace.close()
            current = target

    def test_a_semester_backup_is_a_real_archive(self, semester) -> None:
        """归档必须**真的装着**这个学期：非空、可列出、可再次定位。

        （"开着事务时不得快照"是备份层自己的契约，由
        ``tests/test_backup_recovery.py`` 守卫；这里只关心学期规模下备份
        本身还成立 —— 库大了以后最容易坏的是"归档看着成功其实是空的"。）
        """
        service = _backup_service(semester["data_dir"], "2026-09-18T11:00:00+00:00")
        result = service.create_backup()

        # 归档里必须**真的装着**数据库和这个学期的全部材料 —— 只断言"字节
        # 数大于某个常数"是假的判据：常数调大就在小库上失败，调小就什么都
        # 没守住。真正的判据是"归档里有什么"。
        assert result.archive_size == os.path.getsize(result.archive_path)
        assert result.archive_size > 0
        assert result.material_file_count >= MATERIALS_TOTAL, result.material_file_count
        with zipfile.ZipFile(result.archive_path) as archive:
            names = archive.namelist()
        # 归档里的数据库叫 ``database.sqlite``（归档内部名），不是运行时的
        # ``classroom.sqlite`` —— 按运行时的名字找会找不到。
        assert any(name.endswith("database.sqlite") for name in names), names[:10]

        # ``list_backups`` 返回的是 ``BackupInfo``（字段是 ``path``），不是
        # ``BackupResult``（字段是 ``archive_path``）—— 两者不是一个类型。
        listed = service.list_backups()
        assert any(item.path == result.archive_path for item in listed), [
            item.name for item in listed
        ]
        assert any(item.readable for item in listed)


# =====================================================================
# 69.7  100 个知识点抽样溯源审计
# =====================================================================


class TestTraceabilityAudit:
    @staticmethod
    def _sample(workspace: Workspace, course_ids: list[str], size: int) -> list[tuple[str, str]]:
        pool: list[tuple[str, str]] = []
        for cid in sorted(course_ids):
            for point in workspace.knowledge_points(cid):
                pool.append((cid, str(point["knowledge_id"])))
        pool.sort(key=lambda pair: (pair[0], pair[1]))
        step = max(1, len(pool) // size)
        return pool[::step][:size]

    def test_one_hundred_knowledge_points_are_sampled(self, ws, semester) -> None:
        sample = self._sample(ws, semester["course_ids"], TRACE_SAMPLE)
        assert len(sample) == TRACE_SAMPLE, len(sample)

    def test_every_sampled_point_resolves_to_evidence(self, ws, semester) -> None:
        sample = self._sample(ws, semester["course_ids"], TRACE_SAMPLE)
        empty = []
        for cid, knowledge_id in sample:
            trace = ws.knowledge_trace(cid, knowledge_id)
            if not trace.get("evidence"):
                empty.append(knowledge_id)
        assert not empty, f"{len(empty)} 个知识点没有任何证据: {empty[:5]}"

    def test_every_sampled_point_resolves_its_materials(self, ws, semester) -> None:
        sample = self._sample(ws, semester["course_ids"], TRACE_SAMPLE)
        broken = []
        for cid, knowledge_id in sample:
            trace = ws.knowledge_trace(cid, knowledge_id)
            if trace.get("unresolved_material_ids"):
                broken.append((knowledge_id, trace["unresolved_material_ids"]))
        assert not broken, f"{len(broken)} 个知识点溯源断链: {broken[:3]}"

    def test_every_sampled_point_stays_inside_its_own_course(self, ws, semester) -> None:
        """溯源链上的材料必须属于**同一门课** —— 跨课溯源就是串课。"""
        sample = self._sample(ws, semester["course_ids"], TRACE_SAMPLE)
        leaked = []
        for cid, knowledge_id in sample:
            known = {
                str(row["material_id"]) for row in ws.list_materials(cid)
            }
            trace = ws.knowledge_trace(cid, knowledge_id)
            for material in trace.get("materials") or []:
                if str(material.get("material_id")) not in known:
                    leaked.append((cid, knowledge_id, material.get("material_id")))
        assert not leaked, f"溯源链指向了别的课的材料: {leaked[:3]}"

    def test_sampled_titles_carry_their_source_tag(self, ws, semester) -> None:
        """知识点标题里的材料标签必须属于这门课（内容寻址 id 的连带检查）。"""
        sample = self._sample(ws, semester["course_ids"], TRACE_SAMPLE)
        wrong = []
        # 用**创建顺序**（``course_ids`` 本身就是创建顺序），不能排序：标签
        # 里的 ``c{i}`` 是创建时的课程序号，按 course_id 字典序 enumerate
        # 会把编号和课程对错位，于是"每门课的知识点都带着别门的标签"这种
        # 假阳性就出来了（内容寻址的 id 字典序与创建顺序无关）。
        for course_index, cid in enumerate(semester["course_ids"]):
            for knowledge_id in [k for (c, k) in sample if c == cid]:
                point = ws.knowledge_point(cid, knowledge_id)
                for tag in TAG_RE.findall(str(point.get("title") or "")):
                    if not tag.startswith("c%ds" % course_index):
                        wrong.append((cid, knowledge_id, tag))
        assert not wrong, f"{len(wrong)} 个知识点带着别门的标签: {wrong[:3]}"

    def test_traceability_survives_a_restart(self, semester) -> None:
        workspace = _open(semester["data_dir"])
        try:
            sample = self._sample(workspace, semester["course_ids"], 20)
            for cid, knowledge_id in sample:
                trace = workspace.knowledge_trace(cid, knowledge_id)
                assert trace.get("evidence"), knowledge_id
                assert not trace.get("unresolved_material_ids"), knowledge_id
        finally:
            workspace.close()


# =====================================================================
# 69.8  跨课程泄漏审计
# =====================================================================


class TestCrossCourseLeakage:
    def test_knowledge_point_ids_are_disjoint(self, ws, semester) -> None:
        seen: dict[str, set[str]] = {}
        for cid in semester["course_ids"]:
            seen[cid] = {
                str(p["knowledge_id"]) for p in ws.knowledge_points(cid)
            }
        ids = list(seen.values())
        for index, left in enumerate(ids):
            for right in ids[index + 1 :]:
                assert not (left & right), "两门课的知识点 id 有交集 —— 串课"

    def test_material_ids_are_disjoint(self, ws, semester) -> None:
        seen = [
            {str(row["material_id"]) for row in ws.list_materials(cid)}
            for cid in semester["course_ids"]
        ]
        for index, left in enumerate(seen):
            for right in seen[index + 1 :]:
                assert not (left & right)

    def test_exercise_ids_are_disjoint(self, ws, semester) -> None:
        seen = [
            {str(row["exercise_id"]) for row in ws.list_exercises(cid)}
            for cid in semester["course_ids"]
        ]
        for index, left in enumerate(seen):
            for right in seen[index + 1 :]:
                assert not (left & right)

    def test_answer_ids_are_disjoint(self, ws, semester) -> None:
        seen: list[set[str]] = []
        for cid in semester["course_ids"]:
            ids: set[str] = set()
            for row in ws.list_students(cid):
                student = str(row["student_id"])
                if student == REAL_STUDENT:
                    continue
                log = ws.context(cid).learning_service.answer_log_for(student)
                ids.update(str(item["answer_id"]) for item in log)
            seen.append(ids)
        for index, left in enumerate(seen):
            for right in seen[index + 1 :]:
                assert not (left & right), "两门课的答案 id 有交集"

    def test_students_do_not_appear_in_another_course(self, ws, semester) -> None:
        for index, cid in enumerate(semester["course_ids"]):
            own = {str(row["student_id"]) for row in ws.list_students(cid)}
            for other in semester["course_ids"][index + 1 :]:
                others = {str(row["student_id"]) for row in ws.list_students(other)}
                assert not (own & others), f"{cid} 与 {other} 共享学生"

    def test_course_summary_counts_match_direct_queries(self, ws, semester) -> None:
        """总览里的每个计数都必须来自**带 course_id 的查询**，不是合并后拆分。"""
        for cid in semester["course_ids"]:
            summary = ws.course_summary(cid)
            counts = summary["counts"]
            assert counts["knowledge_points"] == len(ws.knowledge_points(cid)), cid
            assert counts["exercises"] == len(ws.list_exercises(cid)), cid
            assert counts["students"] == len(ws.list_students(cid)), cid

    def test_my_courses_totals_are_the_sum_of_per_course_rows(self, ws, semester) -> None:
        payload = MultiCourseWorkspace(ws).my_courses()
        rows = payload["courses"]
        assert len(rows) == COURSES
        for key in COUNT_KEYS:
            if key == "courses":
                continue
            row_sum = sum(int(row["counts"][key]) for row in rows)
            totals = payload.get("totals") or {}
            if key in totals:
                assert totals[key] == row_sum, key

    def test_my_courses_rows_are_sorted_without_ranking(self, ws) -> None:
        payload = MultiCourseWorkspace(ws).my_courses()
        ids = [row["course_id"] for row in payload["courses"]]
        assert ids == sorted(ids), "课程顺序必须是稳定的字典序，不是推荐序"

    def test_leakage_audit_survives_a_restart(self, semester) -> None:
        workspace = _open(semester["data_dir"])
        try:
            seen: list[set[str]] = []
            for cid in semester["course_ids"]:
                seen.append(
                    {str(p["knowledge_id"]) for p in workspace.knowledge_points(cid)}
                )
            for index, left in enumerate(seen):
                for right in seen[index + 1 :]:
                    assert not (left & right)
        finally:
            workspace.close()


# =====================================================================
# 69.3  真人风格学生（确定性种子）
# =====================================================================


class TestRealStudent:
    def test_the_real_student_walked_the_daily_flow(self, semester) -> None:
        steps = semester["real_steps"]
        assert steps, "真人学生一步都没走"
        assert len({step["knowledge_id"] for step in steps}) >= 1

    def test_every_step_produced_an_answer(self, semester) -> None:
        for step in semester["real_steps"]:
            assert step["answer_id"], step
            assert step["evaluation_status"], step

    def test_the_real_student_actually_made_mistakes(self, semester) -> None:
        """真人学生是故意答错的 —— 否则"答错不等于薄弱"这条语义没被测到。"""
        wrong = [
            step
            for step in semester["real_steps"]
            if str(step["evaluation_status"]).lower() != "correct"
        ]
        assert wrong, semester["real_steps"]

    def test_next_task_never_claims_mastery(self, semester) -> None:
        for step in semester["real_steps"]:
            assert FORBIDDEN_NEXT_TASK not in step["next_task_text"], step

    def test_the_same_state_yields_the_same_next_task(self, ws, semester) -> None:
        """确定性：同一状态下重复询问"下一步"，答案必须一致。"""
        cid = semester["course_ids"][0]
        first = ws.learning_workflow_start(cid, REAL_STUDENT)
        second = ws.learning_workflow_start(cid, REAL_STUDENT)
        assert json.dumps(first, sort_keys=True, ensure_ascii=False) == json.dumps(
            second, sort_keys=True, ensure_ascii=False
        )

    def test_the_real_student_answer_is_grounded(self, ws, semester) -> None:
        for step in semester["real_steps"]:
            grounding = ws.exercise_grounding(semester["course_ids"][0], step["exercise_id"])
            assert grounding["knowledge_points"], step
            assert not grounding.get("unresolved"), step

    def test_the_real_student_state_is_not_a_weakness_verdict(self, ws, semester) -> None:
        """答错不得被写成"薄弱"判定 —— 状态只能由合法 LearningEvent 驱动。"""
        cid = semester["course_ids"][0]
        state = ws.student_state(cid, REAL_STUDENT)
        blob = json.dumps(state, ensure_ascii=False, sort_keys=True)
        for word in ("薄弱", "weak", "weakness", "dominat", "mastered"):
            assert word.lower() not in blob.lower(), (word, blob[:200])


# =====================================================================
# 69.10  UI 压力审计（10 个页面，真实服务器）
# =====================================================================


class TestUiStress:
    def _ids(self, workspace: Workspace, semester: dict[str, Any]) -> dict[str, str]:
        cid = semester["course_ids"][0]
        session = workspace.list_sessions(cid)[0]
        point = workspace.knowledge_points(cid)[0]
        exercise = workspace.list_exercises(cid)[0]
        student = _student(semester, cid, 0)
        return {
            "course_id": cid,
            "session_id": str(session["session_id"]),
            "knowledge_id": str(point["knowledge_id"]),
            "exercise_id": str(exercise["exercise_id"]),
            "student_id": student,
        }

    def test_ten_pages_render_against_a_live_server(self, semester, tmp_path) -> None:
        if not UI_STRESS.exists():
            pytest.skip("scripts/ui_stress_check.js 不存在")
        workspace = _open(semester["data_dir"])
        ids = self._ids(workspace, semester)
        server = create_server(workspace, port=0).start()
        try:
            config = tmp_path / "ui-stress.json"
            config.write_text(
                json.dumps({"base": server.url, "ids": ids}, ensure_ascii=False),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [NODE, str(UI_STRESS), str(config)],
                capture_output=True,
                text=True,
                timeout=300,
            )
        finally:
            server.stop()
            workspace.close()

        marker = [line for line in proc.stdout.splitlines() if line.startswith("__STRESS_JSON__")]
        assert marker, (proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])
        report = json.loads(marker[0][len("__STRESS_JSON__") :])
        assert not report["failures"], report["failures"]
        assert len(report["pages"]) >= UI_PAGES, len(report["pages"])

    def test_the_api_serves_the_semester_without_a_server_error(
        self, semester, tmp_path
    ) -> None:
        workspace = _open(semester["data_dir"])
        server = create_server(workspace, port=0).start()
        try:
            cid = semester["course_ids"][0]
            ids = self._ids(workspace, semester)
            paths = [
                "/api/my-courses",
                "/api/courses",
                "/api/dashboard",
                "/api/today",
                "/api/knowledge?course_id=%s" % cid,
                # 课程作用域的端点一律要带 course_id —— 不带是 400（用户输入
                # 错误），不是 500。这里同时在守 Task 70 的"用户输入错误不
                # 得产生 500"这条契约。
                "/api/students?course_id=%s" % cid,
                "/api/exercises?course_id=%s" % cid,
                "/api/students/%s/mistakes?course_id=%s" % (ids["student_id"], cid),
                "/api/courses/%s/review" % cid,
                "/api/courses/%s/summary" % cid,
            ]
            for path in paths:
                request = urllib.request.Request(server.url + path, method="GET")
                try:
                    with urllib.request.urlopen(request, timeout=120) as response:
                        assert response.status == 200, path
                        payload = json.loads(response.read().decode("utf-8"))
                except urllib.error.HTTPError as exc:  # pragma: no cover
                    pytest.fail(f"{path} -> {exc.code}")
                assert payload.get("success") is True, path
        finally:
            server.stop()
            workspace.close()
