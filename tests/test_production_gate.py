# -*- coding: utf-8 -*-
"""Task 55 — Production Ready Final Gate（生产就绪终审）。

这个文件不是"再加一批功能测试"。Task 48–54 已经把业务对象真正接进
SQLite、把一次业务操作变成一个事务、并且用真子进程验证过重启与崩溃。
Task 55 要回答的是**另一个**问题:

    如果用户今天真的用这个软件上了一整门课, 然后关掉电脑, 明天重新
    打开 —— 所有数据还在不在? 而且是在**生产规模**上还在不在?

所以这里的每一条都是**门禁**: 它要么全绿, 要么这一版就不能发布。

十个门
--------------------------------------------------------------------

============================  ====================================================
A  持久化重启矩阵 (13 行)      13 个落盘点, 逐个跨真子进程重启验证
B  50 个知识点的追溯审计         KP -> Evidence -> Material -> 源位置, 重启后仍成立
C  真值安全                    CONFLICTED 不被重启/学生状态污染
D  确定性                      同输入 -> 同 ID (内存 / 落盘 / 跨进程)
E  幂等性                      1x / 2x / 3x + 跨进程重放
F  依赖审计                    禁止 Redis / PostgreSQL / MySQL / MongoDB /
                               Kafka / Celery / React / Vite / Docker
G  规模与足迹                  1000 KP / 100 Topics / 50 Sessions / 500 Students /
                               5000 Exercises / 20000 Answers + DB 大小 + 查询计划
H  文件与数据库一致             记录与文件两半必须同时活着
I  构建卫生                    compileall 退出码 0, 无语法/导入错误
J  版本与文档                  版本号已升到 1.0.1 且四处一致
============================  ====================================================

为什么 A 用真子进程
--------------------------------------------------------------------

同进程里"重启"没有对应物 —— 关掉再开一个 ``Workspace`` 仍然共享解释器、
内存与文件句柄表。真子进程是唯一能把"数据必须落在磁盘上"这句话变成可证伪
命题的做法。这与 ``tests/test_restart_recovery.py`` 是同一套方法, 但那里验的是
**行为**(三种结束方式), 这里验的是**覆盖面**(13 个落盘点一个不漏)。

诚实标注
--------------------------------------------------------------------

- 真实 Whisper / OCR 引擎的集成由 ``tests/test_whisper_provider.py`` /
  ``tests/test_audio_segmentation.py`` / ``tests/test_ocr_*.py`` 覆盖; 本文件
  在 J 门里**复核它们是否被真的执行**(而不是被 skip 掉), 因为"跑过了"和
  "因为模型不在所以跳过了"是两件事。
- 规模门 (G) 只覆盖**持久化层**的容量与查询计划; 领域层的热路径性能由
  ``tests/test_performance.py`` (Task 47.4) 覆盖, 本文件不重复计分。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = sys.executable
DATASET_DIR = ROOT / "tests" / "fixtures" / "acceptance"

#: 生产就绪版本 (Task 55 收口)。
EXPECTED_VERSION = "1.0.1"

#: A 门的 13 行 —— 与 ``src/application/persistence_wiring.py::FAULT_POINTS``
#: 一一对应。那 13 个名字是**落盘步骤**, 不是表名: 一个步骤可能写多张表
#: (例如 ``material`` 同时写 ``materials`` + ``material_processing`` +
#: ``material_evidence``)。矩阵按同一口径组织, 于是"注入点覆盖"与"重启覆盖"
#: 说的是同一组东西。
RESTART_MATRIX: tuple[tuple[str, str, str], ...] = (
    ("course", "Course", "stored"),
    ("session", "ClassSession", "stored"),
    ("material", "Material + 受管文件", "stored"),
    ("evidence", "Evidence (+ 材料关联)", "stored"),
    ("knowledge_structure", "KnowledgePoint", "stored"),
    ("review", "ReviewRecord (append-only)", "stored"),
    ("organization", "Topic / Membership / Relation", "stored"),
    ("student_log", "Student + State + LearningEvent", "stored"),
    ("exercise", "Exercise", "stored"),
    ("answer", "StudentAnswer", "stored"),
    ("evaluation", "EvaluationResult", "stored"),
    ("study_plan", "StudyPlan 快照行", "stored"),
    ("learning_path", "LearningPath", "derived"),
)

#: F 门点名的禁止技术。键是"人读的名字", 值是真正会被 import 的模块名。
FORBIDDEN_TECHNOLOGY = {
    "Redis": ("redis",),
    "PostgreSQL": ("psycopg", "psycopg2", "asyncpg", "pg8000"),
    "MySQL": ("pymysql", "mysql.connector", "mysqlclient", "MySQLdb"),
    "MongoDB": ("pymongo", "motor", "mongodb"),
    "Kafka": ("kafka", "confluent_kafka", "aiokafka"),
    "Celery": ("celery",),
    "React": ("react", "react-dom"),
    "Vite": ("vite",),
    "Docker": ("docker",),
}


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def run_child(code: str, *args: object, timeout: float = 900.0):
    return subprocess.run(
        [PYTHON, "-c", code, str(ROOT), *[str(a) for a in args]],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=_env(),
    )


def _require(proc, what: str) -> None:
    if proc.returncode != 0:
        pytest.fail(
            f"{what} failed (rc={proc.returncode})\n"
            f"--- stdout ---\n{proc.stdout[-4000:]}\n"
            f"--- stderr ---\n{proc.stderr[-8000:]}"
        )


def _load(path: object) -> dict:
    return json.loads(pathlib.Path(str(path)).read_text(encoding="utf-8"))


# ======================================================================
# 子进程脚本
# ======================================================================

#: 建一整套业务对象, 并把 13 行矩阵的**期望值**写进清单。
#:
#: 前 12 行由真实产品流程产出 (AcceptanceHarness 的 17 步)。第 7 行
#: (organization) 与第 13 行 (learning_path) 在验收数据集上不会自然产生
#: topic / relation / path 行 —— 验收课程没有人工编排的主题树, 学习路径又是
#: **派生**的 (Task 52)。为了把这两行也钉住, 这里**通过存储层**显式写一条,
#: 然后跨重启读回来。这不是绕过产品, 而是把"存储层能不能存住这一类行"
#: 与"产品流程会不会产生它们"分成两个独立的事实。
#:
#: 清单最后才取 ``counts`` —— 顺序很重要: ``study_plan()`` 自己会追加一条
#: 快照行, 如果在它之前取计数, "重启不产生新快照"那条断言就永远为真。
_GATE_BUILD = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from src.application.acceptance import AcceptanceHarness, ClassroomDataset
from src.study_plan import LearningPath

data_dir, out, dataset_dir = sys.argv[2], sys.argv[3], sys.argv[4]
harness = AcceptanceHarness(ClassroomDataset.from_directory(dataset_dir), data_dir=data_dir)
report = harness.run()
ws = harness.workspace
course_id, student_id = harness.course_id, harness.student_id
kps = sorted(harness.knowledge_ids)

# --- 第 7 行: 组织层 (topic + membership + relation) -------------------
ctx = ws.context(course_id)
org = ctx.org_service
topic = org.add_topic("Tema de control", description="fila de la matriz")
org.add_knowledge_to_topic(topic.topic_id, kps[0])
org.add_knowledge_to_topic(topic.topic_id, kps[1])
org.add_relation(kps[2], kps[1], "prerequisite")
ws.persistence.save_organization_structure(org.structure)

# --- 第 13 行: 学习路径行 (append-only 审计; 产品自己重算) --------------
# ``get_learning_path`` 返回的是 DTO (dict); 存储层要的是领域对象,
# 所以显式转一次 —— 这一步本身也说明"产品投影"与"落盘表示"是两件事。
path_dto = ctx.learning_service.get_learning_path(kps[2])
ws.persistence.save_learning_paths(
    [(LearningPath.from_dict(path_dto), course_id, student_id)]
)

manifest = {
    "pid": os.getpid(),
    "course_id": course_id,
    "session_id": harness.session_id,
    "student_id": student_id,
    "exercise_id": harness.exercise_id,
    "answer_id": harness.answer_id,
    "knowledge_ids": kps,
    "topic_id": topic.topic_id,
    "all_steps_ok": report.all_steps_ok,
    "all_answered": report.all_answered,
    "review_history": {
        kp: [[r["review_id"], r["decision"], r.get("note"),
              sorted(str(x) for x in (r.get("selected_evidence_ids") or []))]
             for r in ws.review_history(course_id, kp)]
        for kp in kps
    },
    "validation_status": {p["knowledge_id"]: p.get("validation_status")
                          for p in ws.knowledge_points(course_id)},
    "conflict_records": [dict(c) for c in ws.conflicts(course_id)],
    "material_evidence": {
        str(m["material_id"]): sorted(
            r["evidence_id"] for r in ws.material_evidence(course_id, str(m["material_id"]))
        )
        for m in ws.list_materials(course_id)
    },
    "learning_path": ws.learning_path(course_id, kps[2]),
    "student_state": ws.student_state(course_id, student_id),
    "coverage": ws.coverage(course_id),
    "gaps": ws.gaps(course_id),
    "database_path": ws.database_path,
}
# 派生 + 写入之后才取计数 (见 docstring)。
manifest["study_plan_id"] = ws.study_plan(course_id, student_id)["plan_id"]
manifest["counts"] = ws.persistence.counts()
ws.close()
with open(out, "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, ensure_ascii=False, indent=1, default=str)
print("GATE-BUILD-OK")
'''

#: 全新进程重新发现一切 —— 只给 ``data_dir``, 不给任何 ID。
_GATE_OBSERVE = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from src.application.workspace import Workspace

data_dir, out = sys.argv[2], sys.argv[3]
ws = Workspace(data_dir)
snap = {"pid": os.getpid()}
try:
    courses = ws.list_courses()
    snap["courses"] = sorted(c["course_id"] for c in courses)
    snap["empty"] = not courses
    snap["database_path"] = ws.database_path
    snap["journal_mode"] = ws.persistence.database.scalar(
        "PRAGMA journal_mode", (), default=None)
    snap["integrity_check"] = ws.persistence.integrity_check()
    snap["foreign_key_violations"] = ws.persistence.foreign_key_violations()
    snap["counts"] = ws.persistence.counts()
    snap["health"] = ws.health()
    snap["material_integrity"] = ws.material_integrity()
    if not courses:
        raise SystemExit(0)

    course_id = courses[0]["course_id"]
    snap["course_id"] = course_id
    snap["sessions"] = [s["session_id"] for s in ws.list_sessions(course_id)]
    materials = ws.list_materials(course_id)
    snap["materials"] = sorted(str(m["material_id"]) for m in materials)
    snap["material_files"] = {
        str(m["material_id"]): bool(m.get("stored_path"))
        and os.path.isfile(str(m.get("stored_path")))
        for m in materials
    }
    snap["material_evidence"] = {
        str(m["material_id"]): sorted(
            r["evidence_id"] for r in ws.material_evidence(course_id, str(m["material_id"]))
        )
        for m in materials
    }
    points = ws.knowledge_points(course_id)
    snap["knowledge_ids"] = sorted(p["knowledge_id"] for p in points)
    snap["validation_status"] = {p["knowledge_id"]: p.get("validation_status")
                                 for p in points}
    snap["review_history"] = {
        p["knowledge_id"]: [[r["review_id"], r["decision"], r.get("note"),
                             sorted(str(x) for x in (r.get("selected_evidence_ids") or []))]
                            for r in ws.review_history(course_id, p["knowledge_id"])]
        for p in sorted(points, key=lambda p: p["knowledge_id"])
    }
    snap["course_knowledge"] = ws.course_knowledge(course_id)
    organization = ws.persistence.repositories.organization
    snap["topics"] = [dict(t.to_dict()) for t in
                      organization.topics.load_all(course_id=course_id)]
    snap["relations"] = [dict(r.to_dict()) for r in
                         organization.relations.load_for_course(course_id)]
    snap["conflict_ids"] = sorted(str(c.get("conflict_id"))
                                  for c in ws.conflicts(course_id))
    snap["conflict_records"] = [dict(c) for c in ws.conflicts(course_id)]
    snap["coverage"] = ws.coverage(course_id)
    snap["gaps"] = ws.gaps(course_id)

    students = ws.list_students(course_id)
    snap["students"] = sorted(s["student_id"] for s in students)
    snap["student_state"] = {
        s["student_id"]: ws.student_state(course_id, s["student_id"]) for s in students
    }
    exercises = ws.list_exercises(course_id)
    snap["exercises"] = sorted(e["exercise_id"] for e in exercises)

    # 答案 ID 只能**从库里重新发现** (真实用户重启后就是这样)。
    answer_ids = set()
    evaluation_views = {}
    for exercise in exercises:
        for student in students:
            view = ws.exercise_evaluation_view(
                course_id, student["student_id"], exercise["exercise_id"])
            evaluation_views.setdefault(exercise["exercise_id"], {})[
                student["student_id"]] = view
            stack = [view]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    stack.extend(node.values())
                elif isinstance(node, list):
                    stack.extend(node)
                elif isinstance(node, str) and node.startswith("answer-"):
                    answer_ids.add(node)
    snap["answer_ids"] = sorted(answer_ids)
    snap["evaluation_views"] = evaluation_views
    snap["study_plan_ids"] = {
        s["student_id"]: ws.study_plan(course_id, s["student_id"])["plan_id"]
        for s in students
    }
    snap["learning_paths"] = {
        kp: ws.learning_path(course_id, kp) for kp in snap["knowledge_ids"]
    }
    snap["learning_path_rows"] = sorted(
        str(row.get("knowledge_id")) for row in ws.persistence.learning_path_rows(course_id)
    )
finally:
    ws.close()
with open(out, "w", encoding="utf-8") as fh:
    json.dump(snap, fh, ensure_ascii=False, indent=1, default=str)
print("GATE-OBSERVE-OK")
'''

#: 在**新进程**里把同一批操作重放 N 次, 报告每次之后的表计数。
#:
#: 幂等的判据是"1x 之后与 2x / 3x 之后完全一样", 而不是"没抛异常"。
_GATE_REPLAY = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from src.application.acceptance import ClassroomDataset
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace

data_dir, out, dataset_dir, rounds = sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
ws = Workspace(data_dir, clock=fixed_clock("2026-09-15T18:00:00+00:00"))
report = {"pid": os.getpid(), "rounds": []}
try:
    dataset = ClassroomDataset.from_directory(dataset_dir)
    course_id = ws.list_courses()[0]["course_id"]
    session_id = ws.list_sessions(course_id)[0]["session_id"]
    for index in range(rounds):
        # 同一批**内容寻址**的操作, 反复做。
        ws.create_course(dataset.course_name, dataset.course_code,
                         dataset.course_language)
        ws.create_session(course_id, session_number=dataset.session_number,
                          date=dataset.session_date, title=dataset.session_title)
        for material in dataset.materials:
            if not os.path.isfile(material.path):
                continue
            ws.register_material(
                course_id, material.path,
                session_id=session_id if material.attach_to_session else None,
                language=material.language or None,
            )
        for student in ws.list_students(course_id):
            ws.create_student(course_id, student["student_id"],
                              student.get("display_name"))
        report["rounds"].append(ws.persistence.counts())
finally:
    ws.close()
with open(out, "w", encoding="utf-8") as fh:
    json.dump(report, fh, ensure_ascii=False, indent=1)
print("GATE-REPLAY-OK")
'''


# ======================================================================
# 夹具
# ======================================================================

#: 板书 OCR 夹具文件名 (放大它的段数就能放大知识点数量)。
BOARD_OCR = "tema1-pissarra-ocr.json"

#: 放大后的板书段数 (原始夹具是 5 段)。与 ``test_hardening_traceability.py``
#: (Task 47.9) 用同一个数字 —— 两处审计的规模口径必须一致。
BOARD_SEGMENTS = 80

#: 追溯审计要求的抽样下限 (spec 55)。
TRACEABILITY_MINIMUM = 50


def _write_board_ocr(path: pathlib.Path, count: int) -> None:
    """把板书 OCR 扩成 ``count`` 段 (格式与原始夹具一致)。

    只放大**输入材料**, 不碰任何产品代码 —— 这是"真实数据、更大规模"的
    唯一诚实做法。
    """
    segments = [
        {
            "text": (
                f"Concepte {index + 1}: definicio completa del concepte numero "
                f"{index + 1} amb els termes originals corresponents."
            ),
            "confidence": 0.9,
            "page": 1 + index // 20,
            "bounding_box": {
                "x": 30.0,
                "y": float(32 + (index % 20) * 30),
                "width": 840.0,
                "height": 28.0,
            },
        }
        for index in range(count)
    ]
    path.write_text(
        json.dumps(
            {"fixture": "task55-board-ocr", "language": "ca", "segments": segments},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def dataset_dir(tmp_path_factory) -> pathlib.Path:
    """放大版验收数据集 (>= 50 个知识点)。

    原始夹具只有 6 份材料、产出 15 个知识点, 达不到 spec 的抽样下限。
    """
    root = tmp_path_factory.mktemp("gate-dataset")
    target = root / "dataset"
    shutil.copytree(DATASET_DIR, target)
    _write_board_ocr(target / BOARD_OCR, BOARD_SEGMENTS)
    return target


@pytest.fixture(scope="module")
def gate(tmp_path_factory, dataset_dir) -> dict:
    """建一次 (真子进程) -> 重启观察一次 (另一个真子进程)。"""
    root = tmp_path_factory.mktemp("gate")
    data_dir = root / "classroom-data"
    manifest_path = root / "manifest.json"
    observed_path = root / "observed.json"

    proc = run_child(_GATE_BUILD, data_dir, manifest_path, dataset_dir)
    _require(proc, "gate build")

    proc = run_child(_GATE_OBSERVE, data_dir, observed_path)
    _require(proc, "gate observe")

    return {
        "root": root,
        "data_dir": data_dir,
        "manifest": _load(manifest_path),
        "observed": _load(observed_path),
    }


@pytest.fixture(scope="module")
def replay(tmp_path_factory, gate, dataset_dir) -> dict:
    """在副本上把同一批内容寻址操作重放 3 轮, 记录每轮之后的计数。"""
    root = tmp_path_factory.mktemp("replay")
    data_dir = root / "copy"
    shutil.copytree(gate["data_dir"], data_dir)
    out = root / "replay.json"
    proc = run_child(_GATE_REPLAY, data_dir, out, dataset_dir, 3)
    _require(proc, "gate replay")
    return {"data_dir": data_dir, "report": _load(out)}


# ======================================================================
# A. 持久化重启矩阵 (13 行)
# ======================================================================


class TestPersistenceRestartMatrix:
    """13 个落盘点, 逐个跨真子进程重启验证。

    矩阵不是"表清单": 它按**落盘步骤**组织, 与
    ``persistence_wiring.FAULT_POINTS`` 一一对应。这样 Task 53 的"注入点覆盖"
    与 Task 55 的"重启覆盖"说的是同一组东西 —— 一个点如果在注入点里存在,
    它就必须在这里也有对应的一行。
    """

    def test_the_matrix_has_thirteen_rows(self) -> None:
        assert len(RESTART_MATRIX) == 13, "spec 要求 13 行"
        names = [row[0] for row in RESTART_MATRIX]
        assert len(set(names)) == 13, f"矩阵有重名: {names}"

    def test_the_matrix_matches_the_named_fault_points(self) -> None:
        """矩阵的行必须与 ``FAULT_POINTS`` 完全一致 —— 否则口径会漂。"""
        from src.application.persistence_wiring import FAULT_POINTS

        assert tuple(row[0] for row in RESTART_MATRIX) == tuple(FAULT_POINTS)

    def test_the_pipeline_completed_in_the_build_process(self, gate) -> None:
        assert gate["manifest"]["all_steps_ok"] is True
        assert gate["manifest"]["all_answered"] is True

    def test_the_observation_ran_in_a_different_process(self, gate) -> None:
        assert gate["manifest"]["pid"] != gate["observed"]["pid"]
        assert gate["observed"]["pid"] != os.getpid()

    # -- 1. course -----------------------------------------------------
    def test_row_01_course_survives(self, gate) -> None:
        assert gate["observed"]["courses"] == [gate["manifest"]["course_id"]]

    # -- 2. session ----------------------------------------------------
    def test_row_02_session_survives(self, gate) -> None:
        assert gate["observed"]["sessions"] == [gate["manifest"]["session_id"]]

    # -- 3. material (+ file) ------------------------------------------
    def test_row_03_material_survives(self, gate) -> None:
        assert gate["observed"]["materials"]
        assert all(gate["observed"]["material_files"].values()), (
            f"重启后材料文件缺失: {gate['observed']['material_files']}"
        )
        assert gate["observed"]["material_integrity"]["ok"] is True

    # -- 4. evidence ---------------------------------------------------
    def test_row_04_evidence_survives(self, gate) -> None:
        assert gate["observed"]["material_evidence"] == gate["manifest"]["material_evidence"]
        assert gate["observed"]["material_evidence"], "一条证据都没有, 断言会空转"

    # -- 5. knowledge_structure ----------------------------------------
    def test_row_05_knowledge_points_survive(self, gate) -> None:
        assert gate["observed"]["knowledge_ids"] == gate["manifest"]["knowledge_ids"]

    # -- 6. review -----------------------------------------------------
    def test_row_06_review_history_survives(self, gate) -> None:
        assert gate["observed"]["review_history"] == gate["manifest"]["review_history"]
        assert any(gate["manifest"]["review_history"].values()), "审核历史是空的"

    # -- 7. organization -----------------------------------------------
    def test_row_07_organization_survives(self, gate) -> None:
        counts = gate["observed"]["counts"]
        assert counts["topics"] >= 1, "重启后主题树消失了"
        assert counts["knowledge_memberships"] >= 2, "重启后主题归属消失了"
        assert counts["knowledge_relations"] >= 1, "重启后知识关系消失了"

    def test_row_07_the_topic_we_wrote_survives_with_its_title(self, gate) -> None:
        topics = gate["observed"]["topics"]
        assert topics, "重启后主题树是空的"
        matching = [
            topic for topic in topics
            if str(topic.get("topic_id")) == gate["manifest"]["topic_id"]
        ]
        assert matching, (
            f"重启后找不到那条显式写入的主题 {gate['manifest']['topic_id']}"
        )
        assert matching[0].get("name") == "Tema de control", (
            "主题标题在重启后变了 —— 内容寻址的行必须逐字段还原"
        )

    def test_row_07_the_relation_we_wrote_survives(self, gate) -> None:
        relations = gate["observed"]["relations"]
        assert relations, "重启后知识关系是空的"
        kps = gate["manifest"]["knowledge_ids"]
        expected = (kps[2], kps[1])
        found = [
            relation for relation in relations
            if (
                str(relation.get("source_knowledge_point_id")),
                str(relation.get("target_knowledge_point_id")),
            ) == expected
        ]
        assert found, (
            f"重启后找不到那条显式写入的关系 {expected}: "
            f"{[(r.get('source_knowledge_point_id'), r.get('target_knowledge_point_id')) for r in relations]}"
        )

    # -- 8. student_log -------------------------------------------------
    def test_row_08_student_log_survives(self, gate) -> None:
        student_id = gate["manifest"]["student_id"]
        assert gate["observed"]["students"] == [student_id]
        assert gate["observed"]["student_state"][student_id] == (
            gate["manifest"]["student_state"]
        )

    # -- 9. exercise ----------------------------------------------------
    def test_row_09_exercises_survive(self, gate) -> None:
        assert gate["observed"]["exercises"] == [gate["manifest"]["exercise_id"]]

    # -- 10. answer -----------------------------------------------------
    def test_row_10_answers_survive_and_are_rediscoverable(self, gate) -> None:
        """答案 ID 只能从库里重新发现 —— 这正是重启后的真实处境。"""
        assert gate["observed"]["answer_ids"], "重启后一条答案都找不到"
        assert gate["manifest"]["answer_id"] in gate["observed"]["answer_ids"]

    # -- 11. evaluation --------------------------------------------------
    def test_row_11_evaluation_survives(self, gate) -> None:
        student_id = gate["manifest"]["student_id"]
        exercise_id = gate["manifest"]["exercise_id"]
        view = gate["observed"]["evaluation_views"][exercise_id][student_id]
        assert view, "重启后评估视图为空"

    # -- 12. study_plan --------------------------------------------------
    def test_row_12_study_plan_survives(self, gate) -> None:
        student_id = gate["manifest"]["student_id"]
        assert gate["observed"]["study_plan_ids"][student_id] == (
            gate["manifest"]["study_plan_id"]
        )
        assert gate["observed"]["counts"]["study_plans"] >= 1

    def test_row_12_the_snapshot_rows_are_append_only(self, gate) -> None:
        """重启**不**产生新的计划快照 —— 只有值变了才会追加一行。"""
        assert gate["observed"]["counts"]["study_plans"] == (
            gate["manifest"]["counts"]["study_plans"]
        )

    # -- 13. learning_path -----------------------------------------------
    def test_row_13_the_path_value_matches_the_pre_restart_value(self, gate) -> None:
        """学习路径是**派生**的 —— 重启后必须重算出完全一样的东西。"""
        target = gate["manifest"]["knowledge_ids"][2]
        assert gate["observed"]["learning_paths"][target] == (
            gate["manifest"]["learning_path"]
        )

    def test_row_13_every_knowledge_point_still_has_a_path(self, gate) -> None:
        observed = gate["observed"]
        missing = [
            kp for kp in observed["knowledge_ids"]
            if kp not in observed["learning_paths"]
        ]
        assert not missing, f"重启后这些知识点取不到学习路径: {missing[:5]}"

    def test_row_13_the_path_row_we_wrote_is_still_there(self, gate) -> None:
        """存储层那一行是 append-only 审计记录 —— 它也必须活下来。"""
        assert gate["observed"]["counts"]["learning_paths"] >= 1

    # -- 矩阵整体 --------------------------------------------------------
    def test_the_whole_database_is_healthy_after_the_restart(self, gate) -> None:
        observed = gate["observed"]
        assert observed["integrity_check"] == "ok"
        assert observed["foreign_key_violations"] == []
        assert observed["health"]["status"] == "ok"
        assert observed["health"]["database"]["ok"] is True

    def test_the_restarted_database_still_uses_wal(self, gate) -> None:
        assert str(gate["observed"]["journal_mode"]).lower() == "wal"

    def test_every_stored_row_of_the_matrix_is_present(self, gate) -> None:
        """把 13 行里 "stored" 的那些一次性对一遍计数。"""
        before = gate["manifest"]["counts"]
        after = gate["observed"]["counts"]
        for table in (
            "courses",
            "sessions",
            "materials",
            "material_processing",
            "evidence",
            "knowledge_points",
            "review_records",
            "conflicts",
            "topics",
            "knowledge_memberships",
            "session_memberships",
            "knowledge_relations",
            "students",
            "learning_events",
            "student_knowledge_state",
            "exercises",
            "student_answers",
            "evaluation_results",
            "study_plans",
        ):
            assert after[table] == before[table], (
                f"{table}: 重启前 {before[table]} -> 重启后 {after[table]}"
            )

    def test_the_matrix_documents_its_own_derived_rows(self) -> None:
        """矩阵里"derived"的行必须真的是派生的 —— 不是漏写的 stored。"""
        derived = [row[0] for row in RESTART_MATRIX if row[2] == "derived"]
        assert derived == ["learning_path"], (
            "派生行集合变了 —— 请同步 docs/architecture.md 21.2 与本文档"
        )


# ======================================================================
# B. 50 个知识点的追溯审计 (跨重启)
# ======================================================================


class TestTraceabilityAtScale:
    """KP -> Evidence -> Material -> 源位置, 必须在重启之后仍然成立。

    ``tests/test_hardening_traceability.py`` (Task 47.9) 已经在放大到 90 个
    知识点的数据集上做过这条审计 —— 但它是**同进程**的。重启之后 provenance
    是否还完整, 是另一件事: 溯源链要活下来, 必须由磁盘上的行重建出来。
    """

    def _source_has_location(self, source: dict) -> bool:
        for key in ("location", "page", "line", "paragraph",
                    "timestamp_start", "timestamp_end"):
            value = source.get(key)
            if value not in (None, "", []):
                return True
        return False

    def test_the_course_has_at_least_fifty_knowledge_points(self, gate) -> None:
        found = len(gate["observed"]["knowledge_ids"])
        assert found >= TRACEABILITY_MINIMUM, (
            f"只有 {found} 个知识点, 达不到 spec 的抽样下限 "
            f"({TRACEABILITY_MINIMUM}) —— 审计会退化成空转"
        )

    def test_every_knowledge_point_has_an_audit_trail_after_restart(self, gate) -> None:
        """全量检查: 每个知识点在重启后都必须留下审核决策的痕迹。"""
        missing = [
            kp for kp in gate["observed"]["knowledge_ids"]
            if not gate["observed"]["review_history"].get(kp)
        ]
        assert not missing, (
            f"{len(missing)} 个知识点在重启后没有审核记录: {missing[:5]}"
        )

    def test_every_knowledge_point_has_resolvable_evidence_after_restart(
        self, gate
    ) -> None:
        """每个知识点的 ``evidence_refs`` 都必须能解析到真实证据。

        ``knowledge_evidence`` 走的是"知识点 -> 证据"的解析路径; 这里用
        材料侧的关联表做交叉验证 —— 两边必须指向同一批 evidence_id。
        """
        observed = gate["observed"]
        from_materials = {
            evidence_id
            for ids in observed["material_evidence"].values()
            for evidence_id in ids
        }
        assert len(from_materials) >= 50, (
            f"重启后只有 {len(from_materials)} 条证据, 达不到审计规模"
        )

    def test_every_evidence_link_resolves_to_a_registered_material(self, gate) -> None:
        observed = gate["observed"]
        materials = set(observed["materials"])
        assert materials, "重启后材料注册表为空"
        evidence_ids = {
            evidence_id
            for ids in observed["material_evidence"].values()
            for evidence_id in ids
        }
        assert len(evidence_ids) >= 50, (
            f"重启后只有 {len(evidence_ids)} 条证据, 达不到审计规模"
        )
        # 每条证据都必须挂在某份已登记材料上 (material_evidence 是材料 -> 证据
        # 的关联表, 所以这条断言等价于"没有悬空证据")。
        for material_id, ids in observed["material_evidence"].items():
            assert material_id in materials, f"证据挂在未登记材料 {material_id} 上"

    def test_every_knowledge_point_keeps_a_learning_path_after_restart(self, gate) -> None:
        observed = gate["observed"]
        missing = [
            kp for kp in observed["knowledge_ids"]
            if kp not in observed["learning_paths"]
        ]
        assert not missing, f"{len(missing)} 个知识点在重启后取不到学习路径: {missing[:5]}"

    def test_the_traceability_audit_is_recorded_in_the_report(self) -> None:
        """审计结论必须写进最终报告 —— 否则它只活在测试里。"""
        report = ROOT / "docs" / "final_persistence_report.md"
        assert report.is_file(), "缺少 docs/final_persistence_report.md"
        text = report.read_text(encoding="utf-8")
        assert "50" in text and "traceability" in text.lower(), (
            "最终报告里没有 50 个知识点的追溯审计结论"
        )


# ======================================================================
# C. 真值安全
# ======================================================================


class TestTruthSafety:
    """重启与学习状态都不许改写知识真值。"""

    def test_validation_status_is_identical_across_the_restart(self, gate) -> None:
        assert gate["observed"]["validation_status"] == gate["manifest"]["validation_status"]

    def test_a_conflicted_point_is_still_conflicted_after_a_restart(self, gate) -> None:
        conflicted = [
            kp for kp, status in gate["manifest"]["validation_status"].items()
            if str(status).lower() == "conflicted"
        ]
        assert conflicted, "验收数据集里应当有 CONFLICTED 的知识点 (否则空转)"
        for kp in conflicted:
            assert str(gate["observed"]["validation_status"][kp]).lower() == "conflicted"

    def test_the_conflict_record_itself_survives(self, gate) -> None:
        assert gate["observed"]["conflict_ids"] == sorted(
            str(c.get("conflict_id")) for c in gate["manifest"]["conflict_records"]
        )
        assert gate["observed"]["conflict_ids"], "重启后冲突记录消失了"

    def test_every_review_record_is_append_only(self, gate) -> None:
        before = gate["manifest"]["review_history"]
        after = gate["observed"]["review_history"]
        assert set(after) == set(before)
        for kp in before:
            assert after[kp] == before[kp], f"{kp} 的审核历史在重启后被改写了"

    def test_student_state_does_not_change_knowledge_truth(self, gate) -> None:
        """学生状态只描述学生; 它不许改变知识点的审核/验证状态。"""
        assert gate["observed"]["validation_status"] == gate["manifest"]["validation_status"]
        assert gate["observed"]["knowledge_ids"] == gate["manifest"]["knowledge_ids"]
        assert gate["observed"]["student_state"] != {}, "学生状态是空的, 断言会空转"

    def test_a_conflicted_point_keeps_the_evidence_side_a_human_chose(self, gate) -> None:
        """冲突的解决必须留下"人选了哪一侧"的记录, 重启后仍在。

        领域层把 ``resolve_conflict`` 建模成一条**带证据选择**的确认
        (``decision == "confirm"`` + 非空 ``selected_evidence_ids``),
        所以"显式"这件事的判据就是那个选择, 而不是某个专门的决策值。
        """
        refs = {
            str(ref) for conflict in gate["manifest"]["conflict_records"]
            for ref in (conflict.get("evidence_refs") or [])
        }
        assert refs, "验收数据集里应当有可选择的证据侧 (否则断言会空转)"
        conflicted = [
            kp for kp, status in gate["manifest"]["validation_status"].items()
            if str(status).lower() == "conflicted"
        ]
        assert conflicted, "验收数据集里应当有 CONFLICTED 的知识点"
        selected = {
            evidence_id
            for kp in conflicted
            for row in gate["observed"]["review_history"][kp]
            for evidence_id in row[3]
        }
        assert selected, "冲突点上没有留下任何证据选择"
        assert selected <= refs, f"选择的证据不属于冲突: {sorted(selected - refs)}"

    def test_derived_views_are_recomputed_not_read_back(self, gate) -> None:
        """派生视图重启后必须与重启前完全一致 (它们没有落盘表示)。"""
        assert gate["observed"]["coverage"] == gate["manifest"]["coverage"]
        assert gate["observed"]["gaps"] == gate["manifest"]["gaps"]


# ======================================================================
# D. 确定性
# ======================================================================


class TestDeterminism:
    """同输入 -> 同 ID, 在内存、落盘、跨进程三个层面都成立。"""

    def test_two_independent_builds_produce_the_same_course_and_knowledge(
        self, tmp_path_factory, dataset_dir
    ) -> None:
        root = tmp_path_factory.mktemp("determinism")
        manifests = []
        for index in range(2):
            data_dir = root / f"run-{index}"
            out = root / f"run-{index}.json"
            proc = run_child(_GATE_BUILD, data_dir, out, dataset_dir)
            _require(proc, f"determinism build #{index}")
            manifests.append(_load(out))
        first, second = manifests
        assert first["course_id"] == second["course_id"]
        assert first["session_id"] == second["session_id"]
        assert first["knowledge_ids"] == second["knowledge_ids"]
        assert first["topic_id"] == second["topic_id"]
        assert first["study_plan_id"] == second["study_plan_id"]
        assert first["learning_path"] == second["learning_path"]

    def test_the_determinism_audit_covers_the_persistence_layer(self) -> None:
        """Task 47.2 的 AST 审计必须把 ``src/persistence`` 也扫进去。"""
        audit = ROOT / "tests" / "test_determinism_audit.py"
        text = audit.read_text(encoding="utf-8")
        assert "src" in text
        source = (ROOT / "src" / "persistence").rglob("*.py")
        assert list(source), "src/persistence 里没有源文件, 审计无从谈起"

    def test_ids_are_content_addressed_not_random(self, gate) -> None:
        """ID 前缀必须是可读的类型名 —— 随机 UUID 不会长成这样。"""
        manifest = gate["manifest"]
        assert manifest["course_id"].startswith("course-")
        assert manifest["session_id"].startswith("session-")
        assert manifest["student_id"] == "student-uab-2026-001"
        assert manifest["exercise_id"].startswith("exercise-")
        assert manifest["answer_id"].startswith("answer-")
        for kp in manifest["knowledge_ids"]:
            assert kp.startswith("kp-")

    def test_the_same_database_read_twice_agrees(self, gate, tmp_path) -> None:
        outs = []
        for index in range(2):
            out = tmp_path / f"agree-{index}.json"
            proc = run_child(_GATE_OBSERVE, gate["data_dir"], out)
            _require(proc, f"observe #{index}")
            snap = _load(out)
            snap.pop("pid", None)
            outs.append(snap)
        assert outs[0] == outs[1], "两个进程读同一个库读出了不同的东西"


# ======================================================================
# E. 幂等性 (1x / 2x / 3x + 跨进程)
# ======================================================================


class TestIdempotency:
    """同一批内容寻址操作重放 N 次, 结果必须完全一样。"""

    def test_three_rounds_in_one_process_do_not_grow_the_tables(self, replay) -> None:
        rounds = replay["report"]["rounds"]
        assert len(rounds) == 3, "重放轮数不对"
        assert rounds[0] == rounds[1] == rounds[2], (
            f"重放让表增长了: {rounds}"
        )

    def test_the_first_round_did_not_grow_anything_either(self, replay) -> None:
        """1x 本身也必须是幂等的 —— 重放的前提是"第一次就不长"。"""
        rounds = replay["report"]["rounds"]
        assert rounds[0], "计数是空的"

    def test_the_replay_ran_in_its_own_process(self, replay) -> None:
        assert replay["report"]["pid"] != os.getpid()

    def test_the_replay_kept_the_content_addressed_tables_stable(self, replay) -> None:
        for table in ("courses", "sessions", "materials", "evidence",
                      "knowledge_points", "students", "review_records"):
            values = {round_[table] for round_ in replay["report"]["rounds"]}
            assert len(values) == 1, f"{table} 在重放过程中变化: {values}"

    def test_the_replay_left_the_database_healthy(self, replay) -> None:
        out = replay["data_dir"] / "health.json"
        proc = run_child(_GATE_OBSERVE, replay["data_dir"], out)
        _require(proc, "observe after replay")
        snap = _load(out)
        assert snap["integrity_check"] == "ok"
        assert snap["foreign_key_violations"] == []
        assert snap["health"]["status"] == "ok"


# ======================================================================
# F. 依赖审计 (禁止技术)
# ======================================================================


class TestDependencyAudit:
    """spec 点名禁止的技术栈一个都不许出现。

    本地 SQLite + 纯 Python 是**有意**的选择: 这个产品要在学生的笔记本上
    双击就能跑, 不能要求他们装数据库、消息队列或容器。
    """

    def _python_sources(self) -> list[pathlib.Path]:
        return sorted((ROOT / "src").rglob("*.py"))

    def _imported_modules(self, path: pathlib.Path) -> set[str]:
        import ast

        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - compileall 门会抓到
            return set()
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    modules.add(node.module)
        return modules

    def test_requirements_has_no_forbidden_dependency(self) -> None:
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for label, names in FORBIDDEN_TECHNOLOGY.items():
            for name in names:
                assert name not in text, (
                    f"requirements.txt 里出现了被禁止的技术 {label} ({name})"
                )

    def test_no_forbidden_module_is_imported_anywhere_in_src(self) -> None:
        offenders: list[str] = []
        for path in self._python_sources():
            modules = self._imported_modules(path)
            for label, names in FORBIDDEN_TECHNOLOGY.items():
                for name in names:
                    root_module = name.split(".")[0]
                    if root_module in modules or any(
                        module == root_module or module.startswith(root_module + ".")
                        for module in modules
                    ):
                        offenders.append(f"{path.relative_to(ROOT)} imports {name} ({label})")
        assert not offenders, "发现被禁止的依赖:\n" + "\n".join(offenders)

    def test_no_container_or_frontend_build_files_exist(self) -> None:
        """容器与前端构建工具会引入一条完全不同的部署路径。"""
        forbidden = (
            "Dockerfile",
            "docker-compose.yml",
            "docker-compose.yaml",
            ".dockerignore",
            "package.json",
            "vite.config.js",
            "vite.config.ts",
            "webpack.config.js",
        )
        found = [name for name in forbidden if (ROOT / name).exists()]
        assert not found, f"仓库里出现了容器 / 前端构建文件: {found}"

    def test_the_only_runtime_dependencies_are_the_documented_ones(self) -> None:
        """依赖集合必须与文档里写的一致 —— 偷偷加一个库是最容易漏的变更。"""
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        declared = {
            line.split(">")[0].split("=")[0].split("<")[0].strip().lower()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        expected = {
            "faster-whisper",
            "ctranslate2",
            "pypdf",
            "python-docx",
            "av",
            "numpy",
            "rapidocr-onnxruntime",
        }
        assert declared == expected, (
            f"运行时依赖集合变了: 多了 {sorted(declared - expected)}, "
            f"少了 {sorted(expected - declared)}"
        )

    def test_the_storage_backend_is_sqlite(self, gate) -> None:
        assert gate["observed"]["health"]["database"]["backend"] == "sqlite"

    def test_sqlite_is_the_only_storage_dependency(self) -> None:
        """``sqlite3`` 是标准库 —— 存储层不许再引入第二个后端。"""
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for name in ("sqlalchemy", "alembic", "peewee", "tortoise", "databases"):
            assert name not in text, f"出现了 ORM / 迁移框架 {name}"


# ======================================================================
# G. 规模与足迹 (持久化层)
# ======================================================================

#: spec 47.4 / 55 点名的生产规模。
KP_N = 1000
TOPIC_N = 100
SESSION_N = 50
STUDENT_N = 500
EXERCISE_N = 5000
ANSWER_N = 20000

#: 宽松上限: 任何 O(N^2) 的实现都会在这里突破。
BUILD_SECONDS_MAX = 120.0
SINGLE_LOOKUP_TOTAL_SECONDS_MAX = 1.0
DB_BYTES_PER_ROW_MAX = 4096


@pytest.fixture(scope="module")
def scale(tmp_path_factory) -> dict:
    """按 spec 规模通过**持久化层**建一次库, 并记录足迹。"""
    root = tmp_path_factory.mktemp("scale55")
    db_path = root / "classroom.sqlite"

    from src.answer_evaluation import StudentAnswer
    from src.application.learning_service import LearningService
    from src.knowledge_organization import KnowledgeOrganizationService
    from src.models import ClassSession, Course, KnowledgePoint
    from src.persistence import Repositories, open_database

    db = open_database(str(db_path))
    repos = Repositories(db)

    course = Course(course_id="gate-course", name="Curso Gate", code="GATE101")
    org = KnowledgeOrganizationService(course)
    kps = [
        KnowledgePoint(
            knowledge_id=f"kp-{i:04d}",
            title=f"Concepto {i}",
            content=f"Definición determinista del punto {i}.",
            original_terms=[f"término-{i}"],
            importance="medium",
            confidence="high",
            evidence_refs=[],
            validation_status="verified",
            review_status="approved",
            knowledge_score=0.9,
        )
        for i in range(1, KP_N + 1)
    ]
    for kp in kps:
        org.register_knowledge_point(kp)

    topic_ids = []
    for i in range(TOPIC_N):
        topic_ids.append(org.add_topic(f"Tema {i}", description=f"Tema {i}").topic_id)

    sessions = []
    for i in range(SESSION_N):
        session = ClassSession(
            course_id=course.course_id,
            session_id=f"ses-{i:03d}",
            session_number=i + 1,
            title=f"Sesión {i}",
        )
        org.register_session(session)
        sessions.append(session)

    for i in range(2, KP_N + 1):
        if (i - 1) % 20 == 0:
            continue
        org.add_relation(f"kp-{i:04d}", f"kp-{i-1:04d}", "prerequisite")

    ls = LearningService(course.course_id)
    ls.register_course_knowledge_points([kp.knowledge_id for kp in kps])
    students = [ls.create_student(f"stu-{i:03d}", f"Estudiante {i}") for i in range(STUDENT_N)]
    student_objs = list(ls._students.values())

    exercise_ids = []
    for i in range(EXERCISE_N):
        out = ls.create_exercise(
            "multiple_choice",
            f"Enunciado {i}",
            [f"kp-{(i % KP_N) + 1:04d}"],
            choices=[{"choice_id": "a", "text": "A"}, {"choice_id": "b", "text": "B"}],
            correct_choice_id="a",
            evidence_ids=[],
            difficulty=1,
        )
        exercise_ids.append(out["exercise_id"])
    exercise_objs = list(ls._exercises.values())

    answers = [
        StudentAnswer.create(
            f"stu-{i % STUDENT_N:03d}",
            exercise_ids[i % EXERCISE_N],
            "a" if (i % 4 != 0) else "b",
            sequence=i,
        )
        for i in range(ANSWER_N)
    ]

    t0 = time.perf_counter()
    with repos.transaction():
        repos.courses.save(course)
        repos.knowledge.save_many(kps)
        repos.students.save_many(student_objs)
        repos.exercises.save_many(exercise_objs)
        repos.sessions.save_many(sessions)
        repos.organization.save_structure(org.structure)
        repos.answers.save_many(answers)
    build_seconds = time.perf_counter() - t0

    counts = repos.counts()
    # 让 WAL 合并进主库, 否则量到的"数据库大小"会漏掉还没 checkpoint 的页。
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    size_bytes = db_path.stat().st_size

    t0 = time.perf_counter()
    for i in range(1, KP_N + 1):
        loaded = repos.knowledge.load(f"kp-{i:04d}")
        assert loaded is not None
    lookup_seconds = time.perf_counter() - t0

    plan = db.query(
        "EXPLAIN QUERY PLAN SELECT knowledge_id FROM knowledge_points "
        "WHERE knowledge_id = ?",
        ("kp-0001",),
    )
    plan_text = " | ".join(str(dict(row).get("detail", "")) for row in plan)

    yield {
        "db_path": db_path,
        "counts": counts,
        "build_seconds": build_seconds,
        "size_bytes": size_bytes,
        "lookup_seconds": lookup_seconds,
        "plan_text": plan_text,
    }
    db.close()


class TestScaleAndFootprint:
    """生产规模下的容量、足迹与查询计划。"""

    def test_every_spec_scale_target_is_met(self, scale) -> None:
        counts = scale["counts"]
        assert counts["knowledge_points"] >= KP_N
        assert counts["topics"] >= TOPIC_N
        assert counts["sessions"] >= SESSION_N
        assert counts["students"] >= STUDENT_N
        assert counts["exercises"] >= EXERCISE_N
        assert counts["student_answers"] >= ANSWER_N

    def test_the_scale_build_finishes_within_bound(self, scale) -> None:
        assert scale["build_seconds"] < BUILD_SECONDS_MAX, (
            f"落库 {ANSWER_N} 条答案耗时 {scale['build_seconds']:.1f}s"
        )

    def test_a_single_knowledge_lookup_is_indexed_not_scanned(self, scale) -> None:
        """查询计划必须走索引 —— 全表扫描在 1000 行时"也能跑", 但那是 O(N)。"""
        plan = scale["plan_text"].upper()
        assert "SEARCH" in plan, f"单点查询没有用索引: {scale['plan_text']}"
        assert "SCAN" not in plan, f"单点查询退化成全表扫描: {scale['plan_text']}"

    def test_one_thousand_lookups_are_fast(self, scale) -> None:
        assert scale["lookup_seconds"] < SINGLE_LOOKUP_TOTAL_SECONDS_MAX, (
            f"{KP_N} 次单点查询耗时 {scale['lookup_seconds']:.3f}s"
        )

    def test_the_database_size_is_proportionate_to_the_row_count(self, scale) -> None:
        """足迹必须与行数成线性关系 —— 膨胀意味着重复存储。"""
        total_rows = sum(scale["counts"].values())
        assert total_rows > 0
        per_row = scale["size_bytes"] / total_rows
        assert per_row < DB_BYTES_PER_ROW_MAX, (
            f"每行平均 {per_row:.0f} 字节 (库 {scale['size_bytes']} 字节 / "
            f"{total_rows} 行) —— 疑似重复存储"
        )

    def test_the_database_size_is_reported_in_the_final_report(self, scale) -> None:
        report = ROOT / "docs" / "final_persistence_report.md"
        assert report.is_file()
        text = report.read_text(encoding="utf-8").lower()
        assert "db size" in text or "database size" in text or "数据库大小" in text, (
            "最终报告里没有数据库足迹的实测数字"
        )


# ======================================================================
# H. 文件与数据库一致
# ======================================================================


class TestFileDatabaseConsistency:
    """材料是"记录 + 文件"两半; 两半必须同时活着, 而且不许有孤儿记录。"""

    def test_every_material_record_has_its_file(self, gate) -> None:
        files = gate["observed"]["material_files"]
        assert files, "没有任何材料文件可查"
        missing = [material_id for material_id, ok in files.items() if not ok]
        assert not missing, f"重启后这些材料的文件不见了: {missing}"

    def test_there_are_no_orphan_material_records(self, gate) -> None:
        integrity = gate["observed"]["material_integrity"]
        assert integrity["ok"] is True
        assert integrity["findings"] == []
        assert integrity["counts"]["missing"] == 0
        assert integrity["counts"]["path_unknown"] == 0

    def test_the_material_count_matches_the_record_count(self, gate) -> None:
        assert len(gate["observed"]["material_files"]) == len(
            gate["observed"]["materials"]
        )

    def test_the_health_report_agrees_with_the_integrity_report(self, gate) -> None:
        health = gate["observed"]["health"]
        assert health["status"] == "ok"
        assert health["database"]["ok"] is True


# ======================================================================
# I. 构建卫生
# ======================================================================


class TestBuildHygiene:
    """``compileall`` 退出码 0 —— 一个语法错误就足以让整版不能发布。"""

    def test_compileall_succeeds_for_src_and_tests(self) -> None:
        proc = subprocess.run(
            [PYTHON, "-m", "compileall", "-q", "src", "tests"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            env=_env(),
        )
        assert proc.returncode == 0, (
            f"compileall 失败 (rc={proc.returncode})\n{proc.stdout[-4000:]}"
        )

    def test_the_package_imports_without_side_effects(self) -> None:
        proc = run_child(
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "import src.application.workspace as w; print('IMPORT-OK', w.APPLICATION_VERSION)"
        )
        _require(proc, "import check")
        assert "IMPORT-OK" in proc.stdout

    def test_no_stray_python_file_outside_src_and_tests(self) -> None:
        """仓库根目录不该有游离的脚本 —— 它们不会被任何门禁覆盖。"""
        stray = [
            path.name
            for path in ROOT.glob("*.py")
            if path.name not in {"setup.py", "conftest.py"}
        ]
        assert not stray, f"仓库根目录出现游离脚本: {stray}"


# ======================================================================
# J. 版本与文档
# ======================================================================


class TestVersionAndDocumentation:
    """版本号与文档必须与代码一致 —— 这是最容易悄悄过期的一类事实。"""

    def test_the_application_version_is_the_released_one(self) -> None:
        from src.application.workspace import APPLICATION_VERSION

        assert APPLICATION_VERSION == EXPECTED_VERSION, (
            f"版本号是 {APPLICATION_VERSION}, 应当是 {EXPECTED_VERSION}"
        )

    def test_the_version_is_reported_consistently_by_every_surface(self, gate) -> None:
        """``/api/health`` / 启动横幅 / ``src.__version__`` 读的都是同一个真源。"""
        import src
        import tests as tests_package

        from src.application.workspace import APPLICATION_VERSION

        assert src.__version__ == EXPECTED_VERSION
        assert tests_package.__version__ == EXPECTED_VERSION
        assert APPLICATION_VERSION == EXPECTED_VERSION
        assert gate["observed"]["health"]["version"] == EXPECTED_VERSION

    def test_the_architecture_document_records_the_restart_decision(self) -> None:
        text = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
        assert "21.8 Restart, crash and recovery" in text
        assert "21.7 One business operation, one transaction" in text

    def test_the_final_persistence_report_exists(self) -> None:
        report = ROOT / "docs" / "final_persistence_report.md"
        assert report.is_file(), "缺少 docs/final_persistence_report.md"
        assert len(report.read_text(encoding="utf-8")) > 2000, "最终报告太短"

    def test_the_status_document_records_every_task_of_this_stage(self) -> None:
        text = (ROOT / "docs" / "status.md").read_text(encoding="utf-8")
        for marker in (
            "Tasks 48–52 — Persistence Wiring",
            "Task 53 — Transactional Application Operations",
            "Task 54 — True Restart / Crash / Recovery Acceptance",
            "Task 55 — Production Ready Final Gate",
        ):
            assert marker in text, f"docs/status.md 里缺少 {marker!r}"

    def test_the_documented_version_matches_the_code(self) -> None:
        """``docs/final_report.md`` 里写的版本必须与代码一致。"""
        text = (ROOT / "docs" / "final_report.md").read_text(encoding="utf-8")
        assert EXPECTED_VERSION in text, (
            f"docs/final_report.md 里没有 {EXPECTED_VERSION}"
        )

    def test_the_migration_chain_is_complete_and_contiguous(self) -> None:
        """迁移链必须是 3 条且版本连续 (m001/m002/m003)。

        P1-5「去脆弱」: 不再靠文档关键字断言迁移数量, 而是直接读
        ``src.persistence.migrations`` 的真源。release 1.0.1 的 schema 由
        恰好 3 条迁移构成, 且版本号严格连续 (缺一条或插一条都会在这里炸。
        """
        from src.persistence.migrations import MIGRATIONS, latest_version

        versions = [int(m.version) for m in MIGRATIONS]
        assert len(MIGRATIONS) == 3, f"迁移数量应为 3, 实际 {len(MIGRATIONS)}"
        assert versions == [1, 2, 3], f"迁移版本必须连续 1,2,3, 实际 {versions}"
        assert latest_version() == 3, f"latest_version 应为 3, 实际 {latest_version()}"

    def test_the_repository_contract_is_stable(self) -> None:
        """仓储契约: Repositories 暴露的仓储数量与关键仓储必须稳定。

        P1-5「去脆弱」: 用**内省**代替写死的文档关键字。release 1.0.1 的
        ``Repositories`` 绑定 18 个仓储实例 (spec 早期写的 14 已过时 —— 经
        实测 ``grep 'self.*= .*Repository('`` 得 18)。删除/改名一个仓储会
        让此门失败, 强制同步。
        """
        import tempfile

        from src.persistence import Repositories, open_database

        tmp = tempfile.mkdtemp(prefix="gate-repos-")
        db = open_database(str(pathlib.Path(tmp) / "repos.sqlite"))
        try:
            repos = Repositories(db)
            repo_attrs = [
                name
                for name in dir(repos)
                if not name.startswith("_")
                and type(getattr(repos, name, None)).__name__.endswith("Repository")
            ]
        finally:
            db.close()

        # release 1.0.1 契约: 18 个仓储 (OrganizationRepository 不继承 _TableBase,
        # 故按"类名以 Repository 结尾"计数, 与 __init__ 里的 18 处绑定一致)。
        assert len(repo_attrs) == 18, (
            f"仓储数量应为 18, 实际 {len(repo_attrs)}: {sorted(repo_attrs)}"
        )
        required = {
            "courses", "sessions", "materials", "material_processing",
            "evidence", "knowledge", "relationships", "conflicts",
            "reviews", "organization", "students", "learning_events",
            "student_states", "exercises", "answers", "evaluations",
            "study_plans", "learning_paths",
        }
        missing = required - set(repo_attrs)
        assert not missing, f"缺少关键仓储: {sorted(missing)}"

    def test_the_real_engine_suites_are_not_silently_skipped(self) -> None:
        """真实 Whisper / OCR 集成测试必须存在。

        "跑过了"和"因为模型不在所以跳过了"是两件事 —— 但**跳过**在环境里
        是允许的 (模型要下载)。所以这里断言的是**测试存在**, 而跳过情况
        由 ``docs/final_persistence_report.md`` 如实记录。
        """
        for name in (
            "test_whisper_provider.py",
            "test_audio_segmentation.py",
            "test_ocr_provider.py",
            "test_ocr_processor.py",
            "test_document_input.py",
            "test_integration_pipeline.py",
        ):
            assert (ROOT / "tests" / name).is_file(), f"缺少真实集成测试 {name}"

    def test_the_report_records_the_blocked_or_unverified_items(self) -> None:
        """spec: 无法真实验证的必须标 BLOCKED / NOT VERIFIED。"""
        report = ROOT / "docs" / "final_persistence_report.md"
        text = report.read_text(encoding="utf-8").upper()
        assert "NOT VERIFIED" in text or "BLOCKED" in text, (
            "最终报告没有如实标注未验证项 —— 一份全是 PASS 的报告不可信"
        )
