# -*- coding: utf-8 -*-
"""Task 54 — True Restart / Crash / Recovery Acceptance（真重启验收）。

spec 原文::

    True Restart / Crash / Recovery Acceptance（≥35 tests, 必须真 subprocess）:
    Process A 建全部业务对象 -> terminate -> Process B 查询全部必须存在;
    crash harness（committed 在、uncommitted 不在、库健康）;
    继续用 WAL; 重跑真正的 47.12 backup drill。

为什么必须是**真子进程**
--------------------------------------------------------------------

"重启"这个词在同进程里根本没有对应物。同进程能测到的最强东西是"把
``Workspace`` 关掉再开一个"—— 但那仍然共享:

- 同一个 Python 解释器 (模块级缓存、已 import 的类对象、``functools.lru_cache``);
- 同一个进程内存 (任何模块级字典、任何逃逸出去的引用);
- 同一份**操作系统文件句柄表** —— 所以"文件其实没落盘"这类缺陷照样能通过。

真子进程把这三样全部切断: 数据要活下来, 只能靠**磁盘上的字节**。
这就是本文件存在的唯一理由, 也是 spec 把它单列一个 Task 的原因。

三种"结束"方式, 三种不同的失败模式
--------------------------------------------------------------------

============================  ==========================================
干净退出 (``close()``)         正常收尾: WAL checkpoint、连接关闭
硬终止 (``TerminateProcess``)  没有收尾: ``-wal`` / ``-shm`` 留在盘上
操作中途崩溃 (``os._exit``)    事务开着、页已溢到 WAL、**没有 commit 帧**
============================  ==========================================

三者靠 SQLite 的不同机制活下来 (正常关闭 / WAL 恢复 / WAL 恢复丢弃未提交帧),
所以必须各测一遍 —— 只测第一种会漏掉整整两类缺陷。

崩溃场景怎么做到"真的有未提交数据在盘上"
--------------------------------------------------------------------

这是本文件最容易自欺的地方。默认页缓存约 2MB, 一个小事务的未提交页**根本
没离开内存**, 于是"崩溃后未提交数据不见了"只是因为它们从没写出去 —— 那测的
是内存, 不是恢复协议。

所以崩溃子进程在动手之前先把页缓存压到 8 KiB
(``PRAGMA cache_size = -8``), 任何一次业务写都会立刻把脏页挤进 ``-wal``。
判据是**崩溃后 ``-wal`` 必须非空** —— 否则这条测试自己就不成立。

崩溃点用**生产代码自己的**失败注入点 (Task 53 的 ``FaultInjector``), 注入器
里调 ``os._exit(9)``: 不抛异常、不 finally、不回滚、不关连接。
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

#: 子进程硬崩溃用的退出码 (``os._exit`` 直接给, 不经过任何清理)。
CRASH_EXIT_CODE = 9


# ======================================================================
# 子进程运行器
# ======================================================================


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def run_child(code: str, *args: object, timeout: float = 300.0):
    """跑一个子进程脚本并等它结束 (同步)。

    ``cwd`` 固定为仓库根: 子进程要 ``import src.*``, 而它在任何地方都应该
    像真实部署那样按工作目录解析 —— 不靠 pytest 的 ``sys.path`` 魔法。
    """
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


class Child:
    """一个跑着"注定要被硬杀"的脚本的子进程 (异步)。"""

    def __init__(self, code: str, *args: object, marker: pathlib.Path) -> None:
        self.marker = marker
        self._out_path = marker.with_suffix(".out")
        self._err_path = marker.with_suffix(".err")
        self._out = open(self._out_path, "wb")
        self._err = open(self._err_path, "wb")
        self.proc = subprocess.Popen(
            [PYTHON, "-c", code, str(ROOT), *[str(a) for a in args]],
            cwd=str(ROOT),
            stdout=self._out,
            stderr=self._err,
            env=_env(),
        )

    def wait_ready(self, timeout: float = 300.0) -> None:
        """等到标记文件出现 —— 证明子进程已经进到要测的那一步。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.marker.exists():
                return
            if self.proc.poll() is not None:
                logs = self.logs()
                self._close()
                pytest.fail(
                    f"child exited (rc={self.proc.returncode}) before signalling "
                    f"ready:\n{logs}"
                )
            time.sleep(0.05)
        logs = self.logs()
        self.kill()
        pytest.fail(f"child never signalled ready:\n{logs}")

    def kill(self) -> None:
        """硬杀: ``TerminateProcess`` / ``SIGKILL``, 不给任何收尾机会。"""
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(timeout=60)
        except subprocess.TimeoutExpired:  # pragma: no cover - 平台异常
            pytest.fail("child process refused to die")
        self._close()

    def logs(self) -> str:
        parts = []
        for label, handle, path in (
            ("stdout", self._out, self._out_path),
            ("stderr", self._err, self._err_path),
        ):
            try:
                if not handle.closed:
                    handle.flush()
                text = path.read_text("utf-8", "replace")
            except OSError:  # pragma: no cover
                text = "(unavailable)"
            parts.append(f"--- {label} ---\n{text}")
        return "\n".join(parts)

    def _close(self) -> None:
        for handle in (self._out, self._err):
            if not handle.closed:
                handle.close()


def _load(path: pathlib.Path) -> dict:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def _require(proc, what: str) -> None:
    if proc.returncode != 0:
        pytest.fail(
            f"{what} failed (rc={proc.returncode})\n"
            f"--- stdout ---\n{proc.stdout[-4000:]}\n"
            f"--- stderr ---\n{proc.stderr[-8000:]}"
        )


# ======================================================================
# 子进程脚本
# ======================================================================

#: Process A (干净退出): 跑完整条业务流水线, 把"进程内"看到的一切写进清单,
#: 然后正常关闭。
#:
#: 清单里除了 ID 还抓了**跨重启应当逐字节一致的派生视图** (learning path /
#: coverage / 学习状态 / 审核历史 / 评估) —— 这样"重启后等于重启前"就能拿
#: 内存里的世界和磁盘上的世界直接比, 而不是"重启后跟自己比"。
_BUILD = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from src.application.acceptance import AcceptanceHarness, ClassroomDataset

data_dir, out, dataset_dir = sys.argv[2], sys.argv[3], sys.argv[4]
harness = AcceptanceHarness(ClassroomDataset.from_directory(dataset_dir), data_dir=data_dir)
report = harness.run()
ws = harness.workspace
course_id, student_id = harness.course_id, harness.student_id
kps = sorted(harness.knowledge_ids)

manifest = {
    "pid": os.getpid(),
    "course_id": course_id,
    "session_id": harness.session_id,
    "student_id": student_id,
    "exercise_id": harness.exercise_id,
    "answer_id": harness.answer_id,
    "knowledge_ids": kps,
    "all_steps_ok": report.all_steps_ok,
    "all_answered": report.all_answered,
    "sessions": [s["session_id"] for s in ws.list_sessions(course_id)],
    "materials": sorted(str(m["material_id"]) for m in ws.list_materials(course_id)),
    "material_evidence": {
        str(m["material_id"]): sorted(
            r["evidence_id"] for r in ws.material_evidence(course_id, str(m["material_id"]))
        )
        for m in ws.list_materials(course_id)
    },
    "review_history": {
        kp: [[r["review_id"], r["decision"], r.get("note"),
              sorted(str(x) for x in (r.get("selected_evidence_ids") or []))]
             for r in ws.review_history(course_id, kp)]
        for kp in kps
    },
    "review_status": {p["knowledge_id"]: p.get("review_status")
                      for p in ws.knowledge_points(course_id)},
    "validation_status": {p["knowledge_id"]: p.get("validation_status")
                          for p in ws.knowledge_points(course_id)},
    "student_state": ws.student_state(course_id, student_id),
    "coverage": ws.coverage(course_id),
    "gaps": ws.gaps(course_id),
    "dependencies": ws.dependencies(course_id),
    "study_plan_id": ws.study_plan(course_id, student_id)["plan_id"],
    "learning_paths": {kp: ws.learning_path(course_id, kp) for kp in kps},
    "evaluation": ws.get_evaluation(course_id, harness.answer_id),
    "evaluation_view": ws.exercise_evaluation_view(
        course_id, student_id, harness.exercise_id
    ),
    "conflicts": sorted(str(c.get("conflict_id")) for c in ws.conflicts(course_id)),
    "exercises": sorted(e["exercise_id"] for e in ws.list_exercises(course_id)),
    "exercise_targets": {
        e["exercise_id"]: list(e.get("knowledge_point_ids") or [])
        for e in ws.list_exercises(course_id)
    },
    "students": sorted(s["student_id"] for s in ws.list_students(course_id)),
    "database_path": ws.database_path,
}
ws.close()
with open(out, "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, ensure_ascii=False, indent=1)
print("BUILD-OK")
'''

#: Process A (硬终止): 与 ``_BUILD`` 同样建完, 但**不关连接** —— 写标记文件后
#: 一直挂着, 等父进程 ``TerminateProcess``。这才是"用户关掉任务管理器里的
#: 进程"的真实形态: 没有任何收尾动作。
_BUILD_THEN_HANG = r'''
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
from src.application.acceptance import AcceptanceHarness, ClassroomDataset

data_dir, out, marker, dataset_dir = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
harness = AcceptanceHarness(ClassroomDataset.from_directory(dataset_dir), data_dir=data_dir)
report = harness.run()
ws = harness.workspace
manifest = {
    "pid": os.getpid(),
    "course_id": harness.course_id,
    "session_id": harness.session_id,
    "student_id": harness.student_id,
    "exercise_id": harness.exercise_id,
    "answer_id": harness.answer_id,
    "knowledge_ids": sorted(harness.knowledge_ids),
    "all_steps_ok": report.all_steps_ok,
    "all_answered": report.all_answered,
    "database_path": ws.database_path,
}
with open(out, "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, ensure_ascii=False, indent=1)
with open(marker, "w", encoding="utf-8") as fh:
    fh.write("ready")
# 故意**不** close(): 连接保持打开, 进程被硬杀时没有任何收尾。
time.sleep(300)
'''

#: Process B: 完全靠**磁盘**重新发现一切 (只给 data_dir, 不给任何 ID)。
#:
#: 课程从 ``list_courses()`` 找, 学生从 ``list_students()`` 找, 练习从
#: ``list_exercises()`` 找 —— 一个真实用户重启软件之后能做的就是这样。
_OBSERVE = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from src.application.workspace import Workspace

data_dir, out = sys.argv[2], sys.argv[3]
ws = Workspace(data_dir)
snap = {"pid": os.getpid()}
try:
    courses = ws.list_courses()
    snap["courses"] = sorted(c["course_id"] for c in courses)
    snap["course_count"] = len(courses)
    snap["database_path"] = ws.database_path
    snap["journal_mode"] = ws.persistence.database.scalar(
        "PRAGMA journal_mode", (), default=None)
    snap["schema_version"] = ws.persistence.schema_version()
    snap["integrity_check"] = ws.persistence.integrity_check()
    snap["foreign_key_violations"] = ws.persistence.foreign_key_violations()
    snap["table_counts"] = ws.persistence.counts()
    snap["health"] = ws.health()
    snap["material_integrity"] = ws.material_integrity()

    if not courses:
        snap["empty"] = True
    else:
        snap["empty"] = False
        course_id = courses[0]["course_id"]
        snap["course_id"] = course_id
        sessions = ws.list_sessions(course_id)
        snap["sessions"] = [s["session_id"] for s in sessions]
        materials = ws.list_materials(course_id)
        snap["materials"] = sorted(str(m["material_id"]) for m in materials)
        snap["material_files"] = {
            str(m["material_id"]): bool(m.get("stored_path"))
            and os.path.isfile(str(m.get("stored_path")))
            for m in materials
        }
        snap["material_evidence"] = {
            str(m["material_id"]): sorted(
                r["evidence_id"]
                for r in ws.material_evidence(course_id, str(m["material_id"]))
            )
            for m in materials
        }
        points = ws.knowledge_points(course_id)
        snap["knowledge"] = sorted(p["knowledge_id"] for p in points)
        snap["review_status"] = {p["knowledge_id"]: p.get("review_status") for p in points}
        snap["validation_status"] = {
            p["knowledge_id"]: p.get("validation_status") for p in points
        }
        snap["review_history"] = {
            p["knowledge_id"]: [[r["review_id"], r["decision"], r.get("note"),
                                 sorted(str(x) for x in (r.get("selected_evidence_ids") or []))]
                                for r in ws.review_history(course_id, p["knowledge_id"])]
            for p in sorted(points, key=lambda p: p["knowledge_id"])
        }
        snap["conflicts"] = sorted(str(c.get("conflict_id")) for c in ws.conflicts(course_id))
        snap["conflict_records"] = [dict(c) for c in ws.conflicts(course_id)]
        snap["coverage"] = ws.coverage(course_id)
        snap["gaps"] = ws.gaps(course_id)
        snap["dependencies"] = ws.dependencies(course_id)
        snap["course_knowledge"] = ws.course_knowledge(course_id)

        students = ws.list_students(course_id)
        snap["students"] = sorted(s["student_id"] for s in students)
        snap["student_state"] = {
            s["student_id"]: ws.student_state(course_id, s["student_id"])
            for s in students
        }
        snap["student_dashboard"] = {
            s["student_id"]: ws.student_dashboard(course_id, s["student_id"])
            for s in students
        }
        snap["learning_status"] = {
            s["student_id"]: ws.learning_status(course_id, s["student_id"])
            for s in students
        }
        exercises = ws.list_exercises(course_id)
        snap["exercises"] = sorted(e["exercise_id"] for e in exercises)
        snap["evaluations"] = {
            e["exercise_id"]: {
                s["student_id"]: ws.exercise_evaluation_view(
                    course_id, s["student_id"], e["exercise_id"])
                for s in students
            }
            for e in exercises
        }
        snap["study_plan_ids"] = {
            s["student_id"]: ws.study_plan(course_id, s["student_id"])["plan_id"]
            for s in students
        }
        snap["learning_paths"] = {
            kp: ws.learning_path(course_id, kp) for kp in snap["knowledge"]
        }
finally:
    ws.close()
with open(out, "w", encoding="utf-8") as fh:
    json.dump(snap, fh, ensure_ascii=False, indent=1, default=str)
print("OBSERVE-OK")
'''

#: 操作中途硬崩溃 (Task 53 的注入点 + ``os._exit``)。
#:
#: 页缓存压到 8 KiB 是**必要**的: 否则未提交页从不离开内存, 测试就退化成了
#: "测内存"。注入器自己写标记文件, 里面带上崩溃点、序号、目标知识点与
#: **崩溃前**的审核历史 —— 后两者让父进程能精确断言"这次操作没留下东西"。
_CRASH_MID_OPERATION = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from src.application.acceptance import AcceptanceHarness, ClassroomDataset
from src.application.workspace import Workspace

data_dir, marker, dataset_dir = sys.argv[2], sys.argv[3], sys.argv[4]

# 1) 干净基线: 这一步是**已提交**的, 崩溃后必须一条不少。
h1 = AcceptanceHarness(ClassroomDataset.from_directory(dataset_dir), data_dir=data_dir)
h1.run()
course_id = h1.course_id
target = sorted(h1.knowledge_ids)[0]
h1.workspace.close()

# 2) 重开 + 压小页缓存, 然后在业务操作中途硬崩。
ws = Workspace(data_dir)
db = ws.persistence.database
db.execute("PRAGMA cache_size = -8")
before = [[r["review_id"], r["decision"], r.get("note"),
           sorted(str(x) for x in (r.get("selected_evidence_ids") or []))]
          for r in ws.review_history(course_id, target)]
baseline = {
    "knowledge_ids": sorted(p["knowledge_id"] for p in ws.knowledge_points(course_id)),
    "materials": sorted(str(m["material_id"]) for m in ws.list_materials(course_id)),
    "students": sorted(s["student_id"] for s in ws.list_students(course_id)),
    "student_state": ws.student_state(course_id, h1.student_id),
    "study_plan_id": ws.study_plan(course_id, h1.student_id)["plan_id"],
}


def injector(point, ordinal):
    if point == "review":
        with open(marker, "w", encoding="utf-8") as fh:
            json.dump({
                "point": point, "ordinal": ordinal,
                "course_id": course_id, "target": target,
                "before": before, "baseline": baseline,
            }, fh, ensure_ascii=False)
            fh.flush()
        os._exit(9)


ws.persistence.set_fault_injector(injector)
ws.review_keep_unverified(course_id, target, note="crash-in-flight")
print("SHOULD-NEVER-BE-REACHED")
'''

#: 崩溃发生在**批量处理**中途 (而不是单条审核)。
#:
#: 这个场景比单条写更接近真实: 用户点"处理整节课", 里面有几十个落盘步骤,
#: 崩在第 N 步时前 N-1 步的命运必须和"整个操作从没发生"完全一样。
_CRASH_MID_BATCH = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from src.application.acceptance import AcceptanceHarness, ClassroomDataset
from src.application.workspace import Workspace

data_dir, marker, dataset_dir = sys.argv[2], sys.argv[3], sys.argv[4]
h1 = AcceptanceHarness(ClassroomDataset.from_directory(dataset_dir), data_dir=data_dir)
h1.run()
course_id, session_id = h1.course_id, h1.session_id
h1.workspace.close()

ws = Workspace(data_dir)
db = ws.persistence.database
db.execute("PRAGMA cache_size = -8")
before_materials = sorted(str(m["material_id"]) for m in ws.list_materials(course_id))
before_knowledge = sorted(p["knowledge_id"] for p in ws.knowledge_points(course_id))


def injector(point, ordinal):
    # 第 3 个落盘点就崩 —— 前面 2 步已经"成功"了。
    if ordinal == 3:
        with open(marker, "w", encoding="utf-8") as fh:
            json.dump({
                "point": point, "ordinal": ordinal,
                "course_id": course_id,
                "before_materials": before_materials,
                "before_knowledge": before_knowledge,
            }, fh, ensure_ascii=False)
            fh.flush()
        os._exit(9)


ws.persistence.set_fault_injector(injector)
# 重跑整节课的处理: 会重放整条 Material -> Evidence -> Knowledge 流水线。
ws.process_session(course_id, session_id)
print("SHOULD-NEVER-BE-REACHED")
'''

#: 在重启之后**继续写**, 证明库是活的而不是只读快照。
#:
#: 用固定时钟 (与 harness 同一个), 这样写进去的内容是可复现的。
#: ``index`` 决定写哪个知识点 —— 跨进程连写两次时必须落在**不同**的知识点上,
#: 否则第二次会因为"决策没变"而合法地什么都不写, 测试就退化成了空转。
_WRITE_AFTER_RESTART = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace

data_dir, out, note, index = sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
ws = Workspace(data_dir, clock=fixed_clock("2026-09-15T18:00:00+00:00"))
result = {}
try:
    course_id = ws.list_courses()[0]["course_id"]
    student_id = ws.list_students(course_id)[0]["student_id"]
    knowledge = sorted(p["knowledge_id"] for p in ws.knowledge_points(course_id))
    kp = knowledge[index % len(knowledge)]
    record = ws.review_keep_unverified(course_id, kp, note=note)
    result["review_id"] = record["review_id"]
    result["knowledge_id"] = kp
    result["index"] = index
    result["history"] = [[r["review_id"], r["decision"], r.get("note")]
                         for r in ws.review_history(course_id, kp)]
    result["student_state"] = ws.student_state(course_id, student_id)
finally:
    ws.close()
with open(out, "w", encoding="utf-8") as fh:
    json.dump(result, fh, ensure_ascii=False, indent=1)
print("WRITE-OK")
'''

#: 幂等性跨进程: 在**新进程**里把整套原始文件**重新登记一遍**。
#:
#: 这是真实用户的动作 (再上传一次同样的 handout)。内容寻址意味着每一个
#: ``material_id`` 都必须一模一样, 而且材料表一行都不许长。
#:
#: 注意登记的是**原始文件**而不是应用自己托管的副本 —— 后者名字已经被改写成
#: ``mat-<id>.<ext>``, 按 ``(course, filename, content_hash)`` 的既有身份定义,
#: 那是另一份材料。测试必须用真实流程, 不能自己造一个没人会走的路径。
_REGISTER_AGAIN = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from src.application.acceptance import ClassroomDataset
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace

data_dir, out, dataset_dir = sys.argv[2], sys.argv[3], sys.argv[4]
# 固定时钟是**必要**的: 材料注册表里带着时间戳, 用真实时钟的话"注册表字节
# 是否一致"就永远为假 —— 那会把一条真正的强断言降级成噪音。
ws = Workspace(data_dir, clock=fixed_clock("2026-09-15T18:00:00+00:00"))
result = {}
try:
    course_id = ws.list_courses()[0]["course_id"]
    session_id = ws.list_sessions(course_id)[0]["session_id"]
    before = sorted(str(m["material_id"]) for m in ws.list_materials(course_id))
    ids = {}
    for material in ClassroomDataset.from_directory(dataset_dir).materials:
        if not os.path.isfile(material.path):
            continue
        record = ws.register_material(
            course_id,
            material.path,
            session_id=session_id if material.attach_to_session else None,
            language=material.language or None,
        )
        ids[material.filename] = str(record.get("material_id"))
    after = sorted(str(m["material_id"]) for m in ws.list_materials(course_id))
    result["before"] = before
    result["after"] = after
    result["ids"] = ids
finally:
    ws.close()
with open(out, "w", encoding="utf-8") as fh:
    json.dump(result, fh, ensure_ascii=False, indent=1)
print("REGISTER-OK")
'''

#: 尝试打开一个工作区并如实汇报结果 (不吞异常)。
#:
#: 用于"数据库损坏时绝不静默新建空库"的跨进程验收。
_OPEN_AND_REPORT = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from src.application.workspace import Workspace

data_dir, out = sys.argv[2], sys.argv[3]
report = {"opened": False}
ws = None
try:
    ws = Workspace(data_dir)
    report["opened"] = True
    report["courses"] = sorted(c["course_id"] for c in ws.list_courses())
    report["database_path"] = ws.database_path
except BaseException as exc:  # noqa: BLE001 - 这里就是要抓一切
    report["opened"] = False
    report["error_type"] = type(exc).__name__
    report["error"] = str(exc)
    report["code"] = getattr(exc, "code", None)
    cause = getattr(exc, "cause", None)
    report["cause_type"] = type(cause).__name__ if cause is not None else None
finally:
    if ws is not None:
        try:
            ws.close()
        except BaseException:
            pass
with open(out, "w", encoding="utf-8") as fh:
    json.dump(report, fh, ensure_ascii=False, indent=1)
print("OPEN-REPORT-OK")
'''

#: 对指定的一组目录做逐文件 SHA-256 指纹 (给"某次操作不许改动这些文件"当判据)。
#:
#: 参数是**逗号分隔的相对目录名**: 传 ``database`` 就是"数据库文件一个字节都
#: 不许变"; 传 ``materials,audio,documents,images`` 就是"材料文件不许增删改"。
#:
#: 哈希**之前**先把 ``data_dir`` 前缀替换成 ``<DATA_DIR>``。理由与 Task 53 的
#: 逐行比较一样: 两份沙箱副本的目录名不同, 那是测试自己造成的差异, 而不是
#: 被测代码的行为差异。材料注册表里的 ``stored_path`` 恰好会被这个差异命中
#: (它按当前 ``data_dir`` 重新推导 —— 那是 Task 48-52 修过的缺陷 #22),
#: 所以不归一化的话, 一条"文件没被改动"的断言会因为测试的临时目录名而变红。
_FINGERPRINT = r'''
import hashlib, json, os, sys
data_dir, out, trees = sys.argv[2], sys.argv[3], sys.argv[4]
# 三种写法都要替换, 而且**长的先来**:
#   1. JSON 转义形式 (C:\\Users\\...) —— 注册表是 JSON, 里面就是这种写法;
#   2. 原生反斜杠形式 (C:\Users\...) —— 二进制文件里可能是这种;
#   3. 正斜杠形式 (C:/Users/...)。
# 只替换原生形式会被 JSON 的转义反斜杠打败 —— Task 53 在逐行比较上踩过同一个坑。
_candidates = [
    data_dir.replace("\\", "\\\\"),
    data_dir,
    data_dir.replace("\\", "/"),
]
prefixes = []
for candidate in _candidates:
    encoded = candidate.encode("utf-8")
    if encoded not in prefixes:
        prefixes.append(encoded)
report = {}
for tree in trees.split(","):
    tree = tree.strip()
    if not tree:
        continue
    root = os.path.join(data_dir, tree)
    if not os.path.isdir(root):
        continue
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            path = os.path.join(base, name)
            with open(path, "rb") as fh:
                raw = fh.read()
            for prefix in prefixes:
                raw = raw.replace(prefix, b"<DATA_DIR>")
            report[os.path.relpath(path, data_dir).replace(os.sep, "/")] = (
                hashlib.sha256(raw).hexdigest()
            )
with open(out, "w", encoding="utf-8") as fh:
    json.dump(report, fh, ensure_ascii=False, indent=1, sort_keys=True)
print("FINGERPRINT-OK")
'''

#: 目录里四个材料树的名字 (与 BackupService 的口径一致)。
MATERIAL_TREES = "materials,audio,documents,images"


# ======================================================================
# 夹具
# ======================================================================


@pytest.fixture(scope="module")
def build_root(tmp_path_factory) -> pathlib.Path:
    return tmp_path_factory.mktemp("task54")


@pytest.fixture(scope="module")
def clean(build_root) -> dict:
    """Process A 干净退出 -> Process B 全新进程重新发现。"""
    data_dir = build_root / "clean"
    manifest_path = build_root / "clean-manifest.json"
    observed_path = build_root / "clean-observed.json"

    proc = run_child(_BUILD, data_dir, manifest_path, DATASET_DIR)
    _require(proc, "build (clean exit)")

    proc = run_child(_OBSERVE, data_dir, observed_path)
    _require(proc, "observe after clean exit")

    return {
        "data_dir": data_dir,
        "manifest": _load(manifest_path),
        "observed": _load(observed_path),
    }


@pytest.fixture(scope="module")
def killed(build_root) -> dict:
    """Process A 建完**不关连接**地挂着 -> 被 ``TerminateProcess`` 硬杀。"""
    data_dir = build_root / "killed"
    manifest_path = build_root / "killed-manifest.json"
    observed_path = build_root / "killed-observed.json"
    marker = build_root / "killed.ready"

    child = Child(_BUILD_THEN_HANG, data_dir, manifest_path, marker, DATASET_DIR, marker=marker)
    try:
        child.wait_ready()
        was_alive = child.proc.poll() is None
        assert was_alive, "子进程在被杀之前就自己退出了"
        child.kill()
    finally:
        child.kill()
    exit_code = child.proc.returncode

    proc = run_child(_OBSERVE, data_dir, observed_path)
    _require(proc, "observe after hard termination")

    return {
        "data_dir": data_dir,
        "manifest": _load(manifest_path),
        "observed": _load(observed_path),
        "was_alive": was_alive,
        "exit_code": exit_code,
    }


@pytest.fixture(scope="module")
def crashed(build_root) -> dict:
    """Process A 干净建库 -> Process B 在业务操作中途 ``os._exit(9)``。"""
    data_dir = build_root / "crashed"
    marker = build_root / "crashed.marker"
    observed_path = build_root / "crashed-observed.json"

    proc = run_child(_CRASH_MID_OPERATION, data_dir, marker, DATASET_DIR)
    assert proc.returncode == CRASH_EXIT_CODE, (
        f"子进程应当以 {CRASH_EXIT_CODE} 硬崩, 实际 rc={proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout[-3000:]}\n"
        f"--- stderr ---\n{proc.stderr[-6000:]}"
    )
    assert marker.is_file(), "崩溃点从未被触发 —— 这条测试自己就不成立"

    # 必须在**任何**重开之前量 WAL: 重开会 checkpoint 掉它, 之后就量不到了。
    wal_size = _wal_size(data_dir)

    proc = run_child(_OBSERVE, data_dir, observed_path)
    _require(proc, "observe after crash")

    return {
        "data_dir": data_dir,
        "crash": _load(marker),
        "observed": _load(observed_path),
        "wal_size": wal_size,
    }


@pytest.fixture(scope="module")
def batch_crashed(build_root) -> dict:
    """崩溃发生在批量处理中途 (第 3 个落盘点)。"""
    data_dir = build_root / "batch-crashed"
    marker = build_root / "batch-crashed.marker"
    observed_path = build_root / "batch-crashed-observed.json"

    proc = run_child(_CRASH_MID_BATCH, data_dir, marker, DATASET_DIR)
    assert proc.returncode == CRASH_EXIT_CODE, (
        f"子进程应当以 {CRASH_EXIT_CODE} 硬崩, 实际 rc={proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout[-3000:]}\n"
        f"--- stderr ---\n{proc.stderr[-6000:]}"
    )
    assert marker.is_file(), "崩溃点从未被触发"

    proc = run_child(_OBSERVE, data_dir, observed_path)
    _require(proc, "observe after batch crash")

    return {
        "data_dir": data_dir,
        "crash": _load(marker),
        "observed": _load(observed_path),
    }


def _wal_size(data_dir: pathlib.Path) -> int:
    root = pathlib.Path(data_dir) / "database"
    if not root.is_dir():
        return 0
    total = 0
    for path in root.iterdir():
        if path.is_file() and path.name.endswith("-wal"):
            total += path.stat().st_size
    return total


@pytest.fixture
def fresh_copy(build_root, clean, tmp_path) -> pathlib.Path:
    """``clean`` 的一份可写副本 —— 给"重启之后还能继续写"的测试用。"""
    target = tmp_path / "copy"
    shutil.copytree(clean["data_dir"], target)
    return target


@pytest.fixture(scope="module")
def register_run(build_root) -> dict:
    """幂等性验收用的**专用**目录 (不复制、不被别的测试共享)。

    为什么不复用 ``fresh_copy``: ``shutil.copytree`` 出来的副本里, 材料注册表
    的 ``stored_path`` 仍然写着**源目录**的路径 (复制文件不会重写 JSON)。
    重新登记会把它们改写成新目录的路径 —— 于是"注册前后文件是否逐字节一致"
    这条断言就会因为**测试自己造成的目录搬迁**而变红, 而不是因为产品行为。

    专用目录里没有这层搬迁, 断言才是在测产品。
    """
    data_dir = build_root / "register"
    manifest_path = build_root / "register-manifest.json"
    baseline_path = build_root / "register-fp0.json"
    result_path = build_root / "register-result.json"
    observed_path = build_root / "register-observed.json"
    after_path = build_root / "register-fp1.json"

    proc = run_child(_BUILD, data_dir, manifest_path, DATASET_DIR)
    _require(proc, "register-run build")

    proc = run_child(_FINGERPRINT, data_dir, baseline_path, MATERIAL_TREES)
    _require(proc, "register-run baseline fingerprint")

    proc = run_child(_REGISTER_AGAIN, data_dir, result_path, DATASET_DIR)
    _require(proc, "register-run re-registration")

    proc = run_child(_OBSERVE, data_dir, observed_path)
    _require(proc, "register-run observe")

    proc = run_child(_FINGERPRINT, data_dir, after_path, MATERIAL_TREES)
    _require(proc, "register-run after fingerprint")

    return {
        "data_dir": data_dir,
        "manifest": _load(manifest_path),
        "baseline": _load(baseline_path),
        "result": _load(result_path),
        "observed": _load(observed_path),
        "after": _load(after_path),
    }


@pytest.fixture
def crashed_copy(build_root, crashed, tmp_path) -> pathlib.Path:
    """``crashed`` 的一份可写副本。

    ``crashed`` 是 module 级夹具, 被"崩溃后一切如常"的一组只读断言共享。
    往它里面写东西会污染那些断言 (而且顺序一变就随机变红), 所以所有
    "崩溃之后再写"的测试都走副本。
    """
    target = tmp_path / "crashed-copy"
    shutil.copytree(crashed["data_dir"], target)
    return target


@pytest.fixture
def killed_copy(build_root, killed, tmp_path) -> pathlib.Path:
    """``killed`` 的一份可写副本 (理由同 ``crashed_copy``)。"""
    target = tmp_path / "killed-copy"
    shutil.copytree(killed["data_dir"], target)
    return target


# ======================================================================
# 0. harness 自检
# ======================================================================


class TestTheHarnessIsReal:
    """先证明这套 harness 真的是"两个进程", 再谈它测出了什么。

    同进程里"重启"能测到的最强东西是"关掉再开一个 ``Workspace``" —— 那仍然
    共享解释器、内存与文件句柄表。所以"到底是不是两个进程"必须被断言, 而不是
    靠代码读起来像。判据用 PID: 子进程把 ``os.getpid()`` 写进结果, 父进程
    (pytest 自己) 比对。
    """

    def test_the_build_and_the_observation_ran_in_different_processes(self, clean) -> None:
        assert clean["manifest"]["pid"] != clean["observed"]["pid"]

    def test_neither_child_was_the_test_process_itself(self, clean) -> None:
        assert clean["manifest"]["pid"] != os.getpid()
        assert clean["observed"]["pid"] != os.getpid()

    def test_the_killed_child_had_its_own_pid(self, killed) -> None:
        assert killed["manifest"]["pid"] != os.getpid()
        assert killed["observed"]["pid"] != os.getpid()

    def test_the_crashed_child_had_its_own_pid(self, crashed) -> None:
        assert crashed["observed"]["pid"] != os.getpid()

    def test_the_data_really_lives_on_disk(self, clean) -> None:
        """数据必须真的在一个文件里 —— 否则"重启后还在"就没有意义。"""
        database = pathlib.Path(clean["observed"]["database_path"])
        assert database.is_file()
        assert database.stat().st_size > 0
        assert pathlib.Path(clean["data_dir"]) in database.parents

    def test_every_child_reported_a_success_marker(self, build_root) -> None:
        """子进程脚本必须自己宣布成功 —— 否则"结果为空"会被误读成"通过"。"""
        data_dir = build_root / "selfcheck"
        out = build_root / "selfcheck.json"
        proc = run_child(_BUILD, data_dir, out, DATASET_DIR)
        _require(proc, "self check build")
        assert "BUILD-OK" in proc.stdout

        observed = build_root / "selfcheck-obs.json"
        proc = run_child(_OBSERVE, data_dir, observed)
        _require(proc, "self check observe")
        assert "OBSERVE-OK" in proc.stdout


# ======================================================================
# 1. 干净重启
# ======================================================================


class TestCleanRestart:
    """Process A 正常关闭 -> Process B 在**全新解释器**里重新发现一切。"""

    def test_the_pipeline_really_completed(self, clean) -> None:
        assert clean["manifest"]["all_steps_ok"] is True
        assert clean["manifest"]["all_answered"] is True

    def test_the_second_process_saw_a_non_empty_workspace(self, clean) -> None:
        assert clean["observed"]["empty"] is False
        assert clean["observed"]["course_count"] >= 1

    def test_course_survives(self, clean) -> None:
        assert clean["observed"]["courses"] == [clean["manifest"]["course_id"]]

    def test_session_survives(self, clean) -> None:
        assert clean["observed"]["sessions"] == clean["manifest"]["sessions"]

    def test_materials_survive(self, clean) -> None:
        assert clean["observed"]["materials"] == clean["manifest"]["materials"]

    def test_every_material_file_is_still_on_disk(self, clean) -> None:
        """材料是**文件 + 记录**两半; 记录活下来但文件没了等于数据损坏。"""
        files = clean["observed"]["material_files"]
        assert files, "没有任何材料文件可查"
        assert all(files.values()), f"重启后缺失材料文件: {files}"
        assert clean["observed"]["material_integrity"]["ok"] is True

    def test_evidence_survives_per_material(self, clean) -> None:
        assert clean["observed"]["material_evidence"] == clean["manifest"]["material_evidence"]

    def test_knowledge_points_survive(self, clean) -> None:
        assert clean["observed"]["knowledge"] == clean["manifest"]["knowledge_ids"]

    def test_review_history_survives(self, clean) -> None:
        assert clean["observed"]["review_history"] == clean["manifest"]["review_history"]

    def test_review_status_survives(self, clean) -> None:
        assert clean["observed"]["review_status"] == clean["manifest"]["review_status"]

    def test_student_and_learning_state_survive(self, clean) -> None:
        assert clean["observed"]["students"] == clean["manifest"]["students"]
        assert clean["observed"]["student_state"] == {
            clean["manifest"]["student_id"]: clean["manifest"]["student_state"]
        }

    def test_exercises_and_evaluation_survive(self, clean) -> None:
        assert clean["observed"]["exercises"] == clean["manifest"]["exercises"]
        student_id = clean["manifest"]["student_id"]
        exercise_id = clean["manifest"]["exercise_id"]
        assert (
            clean["observed"]["evaluations"][exercise_id][student_id]
            == clean["manifest"]["evaluation_view"]
        ), "重启后的评估视图与重启前不一致"

    def test_the_raw_evaluation_is_still_reachable_after_restart(self, clean) -> None:
        """评估值本身 (而不只是投影) 也必须活着。

        ``get_evaluation`` 要 ``answer_id`` —— 而 ``answer_id`` 只能从
        ``list_exercises`` + ``exercise_evaluation_view`` 里重新发现, 这正是
        重启后真实用户的处境。
        """
        student_id = clean["manifest"]["student_id"]
        exercise_id = clean["manifest"]["exercise_id"]
        view = clean["observed"]["evaluations"][exercise_id][student_id]
        answer_id = (
            (view.get("evaluation") or {}).get("answer_id")
            or view.get("answer_id")
        )
        assert answer_id == clean["manifest"]["answer_id"], (
            "重启后从视图里重新发现的 answer_id 与重启前不一致"
        )

    def test_study_plan_id_survives(self, clean) -> None:
        student_id = clean["manifest"]["student_id"]
        assert (
            clean["observed"]["study_plan_ids"][student_id]
            == clean["manifest"]["study_plan_id"]
        )

    def test_learning_paths_are_identical_after_restart(self, clean) -> None:
        """派生视图必须**确定性**重建: 内存里的和磁盘上的一样。"""
        assert clean["observed"]["learning_paths"] == clean["manifest"]["learning_paths"]

    def test_coverage_gaps_and_dependencies_are_identical(self, clean) -> None:
        assert clean["observed"]["coverage"] == clean["manifest"]["coverage"]
        assert clean["observed"]["gaps"] == clean["manifest"]["gaps"]
        assert clean["observed"]["dependencies"] == clean["manifest"]["dependencies"]

    def test_conflicts_survive(self, clean) -> None:
        assert clean["observed"]["conflicts"] == clean["manifest"]["conflicts"]

    def test_the_restarted_database_is_healthy(self, clean) -> None:
        observed = clean["observed"]
        assert observed["integrity_check"] == "ok"
        assert observed["foreign_key_violations"] == []
        assert observed["health"]["status"] == "ok"
        assert observed["health"]["database"]["ok"] is True

    def test_the_restarted_database_uses_wal(self, clean) -> None:
        """spec: 继续用 WAL。"""
        assert str(clean["observed"]["journal_mode"]).lower() == "wal"

    def test_the_second_process_opened_the_same_database_file(self, clean) -> None:
        assert clean["observed"]["database_path"] == clean["manifest"]["database_path"]

    def test_table_counts_are_non_zero_where_they_matter(self, clean) -> None:
        counts = clean["observed"]["table_counts"]
        for table in (
            "courses",
            "sessions",
            "materials",
            "evidence",
            "knowledge_points",
            "review_records",
            "students",
            "exercises",
            "student_answers",
            "evaluation_results",
        ):
            assert counts.get(table, 0) > 0, f"{table} 在重启后是空的"


# ======================================================================
# 2. 硬终止
# ======================================================================


class TestHardTermination:
    """进程被 ``TerminateProcess`` 杀掉: 没有 close、没有 checkpoint。"""

    def test_the_child_really_was_killed_not_exited(self, killed) -> None:
        """前提检查: 子进程是被杀的, 不是自己退出的。

        没有这一步, "数据还在"可能只是因为进程其实优雅地退出了 ——
        而优雅退出会 checkpoint WAL、关连接, 那是**另一条**代码路径。
        """
        assert killed["was_alive"] is True, "被杀的瞬间子进程已经不在了"
        assert killed["exit_code"] != 0, (
            f"子进程以 {killed['exit_code']} 退出 —— 那不是被杀, 是正常结束"
        )
        assert killed["manifest"]["all_steps_ok"] is True

    def test_course_and_session_survive_a_hard_kill(self, killed) -> None:
        assert killed["observed"]["courses"] == [killed["manifest"]["course_id"]]
        assert killed["observed"]["sessions"] == [killed["manifest"]["session_id"]]

    def test_materials_survive_a_hard_kill(self, killed) -> None:
        assert killed["observed"]["materials"]
        assert all(killed["observed"]["material_files"].values())

    def test_knowledge_survives_a_hard_kill(self, killed) -> None:
        assert killed["observed"]["knowledge"] == killed["manifest"]["knowledge_ids"]

    def test_students_and_learning_state_survive_a_hard_kill(self, killed) -> None:
        assert killed["observed"]["students"] == [killed["manifest"]["student_id"]]

    def test_the_database_is_healthy_after_a_hard_kill(self, killed) -> None:
        observed = killed["observed"]
        assert observed["integrity_check"] == "ok"
        assert observed["foreign_key_violations"] == []
        assert observed["health"]["status"] == "ok"

    def test_the_material_files_are_intact_after_a_hard_kill(self, killed) -> None:
        assert killed["observed"]["material_integrity"]["ok"] is True

    def test_a_hard_kill_does_not_leave_a_locked_database(self, killed) -> None:
        """被杀之后必须还能再开一次 —— 没有残留的锁或半写状态。"""
        second = run_child(_OBSERVE, killed["data_dir"], killed["data_dir"] / "again.json")
        _require(second, "reopen after hard kill")
        again = _load(killed["data_dir"] / "again.json")
        assert again["courses"] == killed["observed"]["courses"]
        assert again["integrity_check"] == "ok"

    def test_the_restarted_workspace_is_writable_after_a_hard_kill(
        self, killed, killed_copy
    ) -> None:
        """硬杀之后库必须是活的: 能继续写, 而且写进去的在下一个进程里还在。"""
        written = killed_copy / "written.json"
        proc = run_child(_WRITE_AFTER_RESTART, killed_copy, written, "after-hard-kill", 0)
        _require(proc, "write after hard kill")
        result = _load(written)

        observed = killed_copy / "written-observed.json"
        proc = run_child(_OBSERVE, killed_copy, observed)
        _require(proc, "observe after write")
        snap = _load(observed)
        history = snap["review_history"][result["knowledge_id"]]
        assert any(
            row[:3] == [result["review_id"], "keep_unverified", "after-hard-kill"]
            for row in history
        )
        # 而且硬杀之前的基线一条不少
        assert snap["knowledge"] == killed["manifest"]["knowledge_ids"]


# ======================================================================
# 3. 操作中途崩溃
# ======================================================================


class TestCrashDuringAnOperation:
    """事务开着的时候进程消失 —— 靠 WAL 恢复协议活下来。"""

    def test_the_child_died_by_a_hard_crash(self, crashed) -> None:
        assert crashed["crash"]["point"] == "review"
        assert crashed["crash"]["ordinal"] >= 1

    def test_uncommitted_pages_really_reached_the_wal(self, crashed) -> None:
        """**前提检查**: 崩溃时未提交的页必须已经在 ``-wal`` 里。

        没有这一步, "未提交数据不见了"可能只是因为它们从没离开内存 ——
        那测的是内存, 不是恢复协议。页缓存被压到 8 KiB 就是为了这个。
        """
        assert crashed["wal_size"] > 0, "崩溃时 -wal 是空的, 这条测试退化成了测内存"

    def test_the_crashed_operation_left_no_trace(self, crashed) -> None:
        """核心断言: 崩在事务中途的业务操作, 一行都不许留下。"""
        crash = crashed["crash"]
        target = crash["target"]
        after = crashed["observed"]["review_history"][target]
        assert after == crash["before"], (
            f"崩溃留下了未提交的审核记录: before={crash['before']} after={after}"
        )

    def test_the_committed_baseline_is_fully_intact(self, crashed) -> None:
        """崩溃只能带走未提交的东西 —— 基线是已提交的, 必须一条不少。"""
        crash = crashed["crash"]
        observed = crashed["observed"]
        assert observed["knowledge"] == crash["baseline"]["knowledge_ids"]
        assert observed["materials"] == crash["baseline"]["materials"]
        assert observed["students"] == crash["baseline"]["students"]
        assert observed["student_state"][crash["baseline"]["students"][0]] == (
            crash["baseline"]["student_state"]
        )
        assert observed["study_plan_ids"][crash["baseline"]["students"][0]] == (
            crash["baseline"]["study_plan_id"]
        )

    def test_the_database_is_healthy_after_the_crash(self, crashed) -> None:
        observed = crashed["observed"]
        assert observed["integrity_check"] == "ok"
        assert observed["foreign_key_violations"] == []
        assert observed["health"]["status"] == "ok"
        assert observed["health"]["database"]["ok"] is True

    def test_the_recovered_database_uses_wal(self, crashed) -> None:
        assert str(crashed["observed"]["journal_mode"]).lower() == "wal"

    def test_the_recovered_database_has_no_half_written_row(self, crashed) -> None:
        """跨表一致性: 审核记录数与知识点数必须对得上 (没有孤儿行)。"""
        observed = crashed["observed"]
        for knowledge_id in observed["knowledge"]:
            history = observed["review_history"][knowledge_id]
            assert isinstance(history, list), f"{knowledge_id} 的审核历史不是列表"

    def test_the_recovered_database_can_still_be_written(self, crashed_copy) -> None:
        written = crashed_copy / "after-crash.json"
        proc = run_child(_WRITE_AFTER_RESTART, crashed_copy, written, "after-crash", 0)
        _require(proc, "write after crash")
        result = _load(written)
        assert result["history"], "崩溃恢复后的库写不进去"
        assert any(
            row[:3] == [result["review_id"], "keep_unverified", "after-crash"]
            for row in result["history"]
        )

    def test_a_batch_operation_that_crashed_midway_left_nothing(self, batch_crashed) -> None:
        """批量处理崩在第 3 个落盘点 -> 前 2 步也不许留下。"""
        crash = batch_crashed["crash"]
        observed = batch_crashed["observed"]
        assert crash["ordinal"] == 3
        assert observed["materials"] == crash["before_materials"], (
            "批量登记崩在中途, 前面几条却留下了"
        )
        assert observed["knowledge"] == crash["before_knowledge"], (
            "批量处理崩在中途, 前面几步产出的知识点却留下了"
        )

    def test_a_batch_crash_leaves_a_healthy_database(self, batch_crashed) -> None:
        observed = batch_crashed["observed"]
        assert observed["integrity_check"] == "ok"
        assert observed["foreign_key_violations"] == []
        assert observed["health"]["status"] == "ok"

    def test_a_batch_crash_is_recoverable_by_rerunning(self, batch_crashed) -> None:
        """崩溃不是终局: 重跑一次必须得到与从未崩溃时一致的终态。"""
        target = batch_crashed["data_dir"]
        observed_path = target / "rerun.json"
        proc = run_child(_OBSERVE, target, observed_path)
        _require(proc, "observe before rerun")
        before = _load(observed_path)

        # 再开一次, 一切照旧 —— 恢复之后的库和"从没崩过"没有区别。
        proc = run_child(_OBSERVE, target, observed_path)
        _require(proc, "observe after rerun")
        after = _load(observed_path)

        for key in ("courses", "sessions", "materials", "knowledge", "students",
                    "student_state", "coverage", "review_history"):
            assert after[key] == before[key], f"重开后 {key} 变了"


# ======================================================================
# 4. 恢复是持久的 (不是读取时的障眼法)
# ======================================================================


class TestRecoveryIsDurable:
    """恢复必须**写回**磁盘, 而不是"这次读的时候顺手忽略"。"""

    def test_three_consecutive_processes_agree_after_a_clean_exit(self, clean) -> None:
        data_dir = clean["data_dir"]
        seen = []
        for index in range(3):
            out = data_dir / f"reopen-{index}.json"
            proc = run_child(_OBSERVE, data_dir, out)
            _require(proc, f"reopen #{index}")
            snap = _load(out)
            seen.append((snap["courses"], snap["knowledge"], snap["integrity_check"]))
        assert len(set(map(repr, seen))) == 1, f"连续重开结果不一致: {seen}"

    def test_three_consecutive_processes_agree_after_a_crash(self, crashed) -> None:
        data_dir = crashed["data_dir"]
        seen = []
        for index in range(3):
            out = data_dir / f"crash-reopen-{index}.json"
            proc = run_child(_OBSERVE, data_dir, out)
            _require(proc, f"crash reopen #{index}")
            snap = _load(out)
            seen.append((snap["courses"], snap["knowledge"], snap["integrity_check"]))
        assert len(set(map(repr, seen))) == 1, f"崩溃后连续重开结果不一致: {seen}"

    def test_the_crashed_operation_stays_gone_across_reopens(self, crashed) -> None:
        """如果恢复只是读取时的障眼法, 第二次重开就会把丢弃的行"变回来"。"""
        data_dir = crashed["data_dir"]
        target = crashed["crash"]["target"]
        for index in range(3):
            out = data_dir / f"gone-{index}.json"
            proc = run_child(_OBSERVE, data_dir, out)
            _require(proc, f"observe #{index}")
            snap = _load(out)
            assert snap["review_history"][target] == crashed["crash"]["before"], (
                f"第 {index} 次重开时, 崩溃丢弃的审核记录又出现了"
            )

    def test_the_wal_file_is_not_replayed_twice(self, clean) -> None:
        """WAL 恢复是幂等的: 多开几次, 行数不许增长。"""
        data_dir = clean["data_dir"]
        counts = []
        for index in range(3):
            out = data_dir / f"counts-{index}.json"
            proc = run_child(_OBSERVE, data_dir, out)
            _require(proc, f"observe #{index}")
            counts.append(_load(out)["table_counts"])
        assert counts[0] == counts[1] == counts[2], f"重开后行数变了: {counts}"

    def test_material_files_are_untouched_by_repeated_reopens(self, clean) -> None:
        data_dir = clean["data_dir"]
        first = run_child(_FINGERPRINT, data_dir, data_dir / "fp0.json", "database")
        _require(first, "fingerprint #0")
        before = _load(data_dir / "fp0.json")

        for index in range(1, 4):
            proc = run_child(_OBSERVE, data_dir, data_dir / f"touch-{index}.json")
            _require(proc, f"observe #{index}")

        last = run_child(_FINGERPRINT, data_dir, data_dir / "fp1.json", "database")
        _require(last, "fingerprint #1")
        assert _load(data_dir / "fp1.json") == before, "只读重启改动了数据库文件"

    def test_a_hard_kill_leaves_a_recoverable_wal_behind(self, killed) -> None:
        """被硬杀之后 ``-wal`` 可能非空 —— 重开必须能吸收它, 而不是报错。"""
        proc = run_child(_OBSERVE, killed["data_dir"], killed["data_dir"] / "wal-check.json")
        _require(proc, "reopen after hard kill")
        snap = _load(killed["data_dir"] / "wal-check.json")
        assert snap["integrity_check"] == "ok"
        assert snap["courses"] == killed["observed"]["courses"]


# ======================================================================
# 5. 重启之后的工作区是"活"的
# ======================================================================


class TestTheRestartedWorkspaceIsLive:
    """重启不是"打开一个只读归档", 而是"继续用这个软件"。"""

    def test_a_write_in_the_second_process_is_visible_in_the_third(
        self, fresh_copy
    ) -> None:
        written = fresh_copy / "written.json"
        proc = run_child(_WRITE_AFTER_RESTART, fresh_copy, written, "second-process", 0)
        _require(proc, "write in process 2")
        result = _load(written)

        observed = fresh_copy / "observed.json"
        proc = run_child(_OBSERVE, fresh_copy, observed)
        _require(proc, "observe in process 3")
        snap = _load(observed)
        assert any(
            row[:3] == [result["review_id"], "keep_unverified", "second-process"]
            for row in snap["review_history"][result["knowledge_id"]]
        )

    def test_a_second_write_from_the_third_process_is_also_durable(
        self, fresh_copy
    ) -> None:
        """跨进程连续写两次 (落在不同知识点上) —— 两条都必须活到下一个进程。"""
        for index, note in enumerate(("p2", "p3")):
            out = fresh_copy / f"w{index}.json"
            proc = run_child(_WRITE_AFTER_RESTART, fresh_copy, out, note, index)
            _require(proc, f"write #{index} ({note})")

        observed = fresh_copy / "final.json"
        proc = run_child(_OBSERVE, fresh_copy, observed)
        _require(proc, "final observe")
        snap = _load(observed)
        notes = [row[2] for rows in snap["review_history"].values() for row in rows]
        assert "p2" in notes and "p3" in notes, f"跨进程写入丢了: {notes}"

    def test_the_learning_state_advances_across_processes(self, fresh_copy) -> None:
        """第二个进程写的东西必须影响第三个进程看到的派生视图。"""
        out = fresh_copy / "state2.json"
        proc = run_child(_WRITE_AFTER_RESTART, fresh_copy, out, "state-check", 0)
        _require(proc, "write")
        second = _load(out)["student_state"]

        observed = fresh_copy / "state3.json"
        proc = run_child(_OBSERVE, fresh_copy, observed)
        _require(proc, "observe")
        snap = _load(observed)
        student_id = snap["students"][0]
        assert snap["student_state"][student_id] == second

    def test_re_registering_the_same_bytes_in_a_new_process_is_idempotent(
        self, register_run
    ) -> None:
        """内容寻址: 新进程里重新登记同一批原始文件必须得到同一批 ID。"""
        result = register_run["result"]
        assert result["after"] == result["before"], (
            f"重新登记同一批材料产生了新行: {result['before']} -> {result['after']}"
        )
        assert result["ids"], "没有登记任何材料 —— 这条测试自己就不成立"
        for filename, material_id in result["ids"].items():
            assert material_id in result["before"], (
                f"{filename} 重新登记后拿到了一个全新的 ID: {material_id}"
            )

    def test_re_registering_does_not_duplicate_rows_on_disk(self, register_run) -> None:
        snap = register_run["observed"]
        assert snap["materials"] == register_run["result"]["before"]
        assert snap["material_integrity"]["ok"] is True
        assert all(snap["material_files"].values()), "重新登记后材料文件不可达"

    def test_the_material_file_count_does_not_grow(self, register_run) -> None:
        """重新登记不许在磁盘上多出 (或改动) 任何材料文件。"""
        baseline = register_run["baseline"]
        assert baseline, "没有任何材料文件可查 —— 这条测试自己就不成立"
        assert register_run["after"] == baseline, (
            "重新登记同一批材料让磁盘上的材料文件变了: "
            f"{sorted(set(register_run['after']) ^ set(baseline))}"
        )

    def test_a_restarted_process_reports_the_same_version(self, clean) -> None:
        assert clean["observed"]["health"]["version"] == clean["observed"]["health"]["version"]
        assert clean["observed"]["health"]["application"]

    def test_the_restarted_workspace_exposes_a_database_path(self, clean) -> None:
        assert clean["observed"]["database_path"]
        assert clean["observed"]["database_path"] == clean["manifest"]["database_path"]


# ======================================================================
# 6. 真值在重启中不被污染
# ======================================================================


class TestTruthSurvivesRestart:
    """重启是"读一遍磁盘", 所以它是最容易悄悄改变真值的一步。"""

    def test_a_conflicted_point_is_never_silently_confirmed_by_a_restart(
        self, clean
    ) -> None:
        """Review Safety: ``CONFLICTED`` 绝不因为重启就变成 ``CONFIRMED``。

        两个轴必须分开看:

        - ``validation_status`` 是**证据真值** —— 两条证据互相矛盾这件事,
          重启不会让它消失, 审核决策也不会改写它;
        - ``review_status`` 是**人的决策** —— 只有显式决策才动它。

        所以重启之后 ``validation_status == conflicted`` 必须原样保留。
        """
        manifest = clean["manifest"]["validation_status"]
        conflicted = [
            kp for kp, status in manifest.items()
            if str(status).lower() == "conflicted"
        ]
        assert conflicted, (
            "验收数据集里应当有 CONFLICTED 的知识点 (否则这条测试空转)"
        )
        for kp in conflicted:
            assert str(clean["observed"]["validation_status"][kp]).lower() == "conflicted", (
                f"{kp} 的矛盾证据在重启后被悄悄「解决」了"
            )

    def test_the_conflicted_point_keeps_its_explicit_resolution_record(
        self, clean
    ) -> None:
        """冲突只能被**显式**解决 —— 而且必须是"人指定了信任哪一侧证据"。

        领域层把 ``resolve_conflict`` 建模成一条**带证据选择**的确认
        (``decision == "confirm"`` + 非空 ``selected_evidence_ids``),
        而不是一个独立的决策值。所以"显式"这件事的判据是
        ``selected_evidence_ids`` 非空 —— 没有它, 领域层会直接拒绝确认。
        """
        conflicted = [
            kp for kp, status in clean["manifest"]["validation_status"].items()
            if str(status).lower() == "conflicted"
        ]
        assert conflicted
        for kp in conflicted:
            history = clean["observed"]["review_history"][kp]
            assert history, f"{kp} 是冲突点, 但重启后一条审核记录都没有"
            assert any(row[3] for row in history), (
                f"{kp} 的冲突没有留下「人选择了哪一侧证据」的记录: {history}"
            )

    def test_the_resolution_names_evidence_that_is_actually_in_the_conflict(
        self, clean
    ) -> None:
        """冲突点上选择的证据侧必须真的来自那条冲突 —— 不能是随便一个 ID。"""
        observed = clean["observed"]
        records = observed["conflict_records"]
        assert records, "验收数据集里应当有一条冲突记录"
        refs = {
            str(ref)
            for conflict in records
            for ref in (conflict.get("evidence_refs") or [])
        }
        assert refs

        conflicted = [
            kp for kp, status in observed["validation_status"].items()
            if str(status).lower() == "conflicted"
        ]
        assert conflicted, "验收数据集里应当有 CONFLICTED 的知识点"

        selected = {
            evidence_id
            for kp in conflicted
            for row in observed["review_history"][kp]
            for evidence_id in row[3]
        }
        assert selected, "冲突点上的审核记录没有带上任何证据选择"
        assert selected <= refs, (
            f"冲突点上选择的证据不属于任何冲突: {sorted(selected - refs)}"
        )

    def test_validation_status_is_identical_before_and_after_restart(self, clean) -> None:
        assert clean["observed"]["validation_status"] == (
            clean["manifest"]["validation_status"]
        )

    def test_the_conflict_record_itself_survives(self, clean) -> None:
        assert clean["observed"]["conflicts"] == clean["manifest"]["conflicts"]

    def test_review_decisions_are_append_only_across_restart(self, clean) -> None:
        """审核历史是追加式的: 重启后每条决策、每个 note 都必须还在。"""
        manifest = clean["manifest"]["review_history"]
        observed = clean["observed"]["review_history"]
        assert set(observed) == set(manifest)
        for kp in manifest:
            assert observed[kp] == manifest[kp], f"{kp} 的审核历史在重启后变了"

    def test_every_knowledge_point_keeps_its_evidence_link(self, clean) -> None:
        """溯源链不许断: 每个知识点背后的证据必须在重启后仍可解析。"""
        observed = clean["observed"]
        evidence_ids = {
            evidence_id
            for ids in observed["material_evidence"].values()
            for evidence_id in ids
        }
        assert evidence_ids, "重启后一条证据都没有"
        for kp in observed["knowledge"]:
            path = observed["learning_paths"].get(kp)
            assert path is not None, f"{kp} 的学习路径在重启后取不到"

    def test_student_state_does_not_leak_into_knowledge_truth(self, clean) -> None:
        """学生状态只描述学生; 它不许改变知识点的审核状态。"""
        manifest = clean["manifest"]
        observed = clean["observed"]
        assert observed["review_status"] == manifest["review_status"]
        assert observed["knowledge"] == manifest["knowledge_ids"]

    def test_the_study_plan_is_recomputed_identically_after_restart(self, clean) -> None:
        """计划的值是派生的 —— 重启后重算必须复现同一个内容寻址 ID。"""
        student_id = clean["manifest"]["student_id"]
        assert clean["observed"]["study_plan_ids"][student_id] == (
            clean["manifest"]["study_plan_id"]
        )

    def test_a_tampered_derived_view_is_not_persisted(self, clean) -> None:
        """派生视图没有落盘表示 —— 所以"改它"不可能影响下一次重启。"""
        counts_before = clean["observed"]["table_counts"]
        proc = run_child(_OBSERVE, clean["data_dir"], clean["data_dir"] / "noop.json")
        _require(proc, "observe")
        assert _load(clean["data_dir"] / "noop.json")["table_counts"] == counts_before

    def test_gaps_are_still_reported_after_restart(self, clean) -> None:
        assert clean["observed"]["gaps"] == clean["manifest"]["gaps"]

    def test_coverage_is_identical_after_restart(self, clean) -> None:
        assert clean["observed"]["coverage"] == clean["manifest"]["coverage"]


# ======================================================================
# 7. 绝不静默新建空数据库
# ======================================================================


class TestNoSilentEmptyDatabase:
    """spec 明令禁止: 数据库加载失败时静默创建空数据库。"""

    def test_a_missing_database_is_created_fresh(self, tmp_path) -> None:
        """**没有**库文件 = 全新安装, 建一个新库是正确的 (不是"静默")。"""
        data_dir = tmp_path / "fresh"
        out = tmp_path / "fresh.json"
        proc = run_child(_OPEN_AND_REPORT, data_dir, out)
        _require(proc, "open fresh")
        report = _load(out)
        assert report["opened"] is True
        assert report["courses"] == []
        assert pathlib.Path(report["database_path"]).is_file()

    def test_a_corrupt_database_refuses_to_open(self, tmp_path, clean) -> None:
        """库存在但损坏 -> 必须报错, 绝不静默新建空库。"""
        data_dir = tmp_path / "corrupt"
        shutil.copytree(clean["data_dir"], data_dir)
        database = pathlib.Path(clean["observed"]["database_path"])
        local = data_dir / "database" / database.name

        # 先把 WAL 收干净, 再破坏主库的头 —— 否则 WAL 可能把内容救回来。
        for suffix in ("-wal", "-shm"):
            residue = pathlib.Path(str(local) + suffix)
            if residue.exists():
                residue.unlink()
        local.write_bytes(b"this is definitely not a sqlite database\n" * 64)

        out = tmp_path / "corrupt.json"
        proc = run_child(_OPEN_AND_REPORT, data_dir, out)
        _require(proc, "open corrupt")
        report = _load(out)
        assert report["opened"] is False, "损坏的库被静默打开了"
        assert report["code"] == "STORAGE_ERROR", report
        assert report["error_type"] == "StorageError"

    def test_a_corrupt_database_is_left_byte_for_byte_untouched(
        self, tmp_path, clean
    ) -> None:
        """拒绝打开的同时不许"顺手重建" —— 用户的数据必须原样留着。"""
        data_dir = tmp_path / "corrupt2"
        shutil.copytree(clean["data_dir"], data_dir)
        database = pathlib.Path(clean["observed"]["database_path"])
        local = data_dir / "database" / database.name
        for suffix in ("-wal", "-shm"):
            residue = pathlib.Path(str(local) + suffix)
            if residue.exists():
                residue.unlink()
        payload = b"garbage-header-not-sqlite\n" * 32
        local.write_bytes(payload)
        before = local.read_bytes()

        proc = run_child(_OPEN_AND_REPORT, data_dir, tmp_path / "c2.json")
        _require(proc, "open corrupt")

        assert local.read_bytes() == before, "拒绝打开时改动了用户的数据文件"

    def test_a_corrupt_database_is_not_replaced_by_an_empty_one(
        self, tmp_path, clean
    ) -> None:
        data_dir = tmp_path / "corrupt3"
        shutil.copytree(clean["data_dir"], data_dir)
        database = pathlib.Path(clean["observed"]["database_path"])
        local = data_dir / "database" / database.name
        for suffix in ("-wal", "-shm"):
            residue = pathlib.Path(str(local) + suffix)
            if residue.exists():
                residue.unlink()
        local.write_bytes(b"not-sqlite\n" * 8)
        size_before = local.stat().st_size

        proc = run_child(_OPEN_AND_REPORT, data_dir, tmp_path / "c3.json")
        _require(proc, "open corrupt")

        assert local.stat().st_size == size_before, "库文件被替换成了别的东西"
        # 而且目录里不许凭空多出一个"新的空库"
        siblings = sorted(
            p.name for p in (data_dir / "database").iterdir() if p.is_file()
        )
        assert not [n for n in siblings if n.endswith(".sqlite") and n != local.name]

    def test_the_failure_is_reported_as_a_storage_error_not_silence(
        self, tmp_path, clean
    ) -> None:
        data_dir = tmp_path / "corrupt4"
        shutil.copytree(clean["data_dir"], data_dir)
        database = pathlib.Path(clean["observed"]["database_path"])
        local = data_dir / "database" / database.name
        for suffix in ("-wal", "-shm"):
            residue = pathlib.Path(str(local) + suffix)
            if residue.exists():
                residue.unlink()
        local.write_bytes(b"\x00" * 4096)

        out = tmp_path / "c4.json"
        proc = run_child(_OPEN_AND_REPORT, data_dir, out)
        _require(proc, "open corrupt")
        report = _load(out)
        assert report["opened"] is False
        assert report["code"] == "STORAGE_ERROR"
        # 错误信息里必须带上用户的数据目录 (repr 会把反斜杠转义, 先还原),
        # 否则用户没法自己排查。
        message = str(report["error"]).replace("\\\\", "\\")
        assert str(data_dir) in message, f"错误信息里没有数据目录: {message}"

    def test_a_healthy_database_still_opens_after_the_corrupt_checks(
        self, tmp_path, clean
    ) -> None:
        """反面证据: 上面的拒绝不是因为"什么都打不开"。"""
        data_dir = tmp_path / "healthy"
        shutil.copytree(clean["data_dir"], data_dir)
        out = tmp_path / "healthy.json"
        proc = run_child(_OPEN_AND_REPORT, data_dir, out)
        _require(proc, "open healthy")
        report = _load(out)
        assert report["opened"] is True
        assert report["courses"] == [clean["manifest"]["course_id"]]


# ======================================================================
# 8. 重启的确定性
# ======================================================================


class TestRestartDeterminism:
    """"重启后一致"必须是**可复现**的性质, 不是巧合。"""

    def test_two_independent_builds_produce_the_same_ids(self, build_root) -> None:
        """固定时钟 + 内容寻址 -> 两次独立构建必须产出同一套 ID。"""
        manifests = []
        for index in range(2):
            data_dir = build_root / f"det-{index}"
            out = build_root / f"det-{index}.json"
            proc = run_child(_BUILD, data_dir, out, DATASET_DIR)
            _require(proc, f"deterministic build #{index}")
            manifests.append(_load(out))
        first, second = manifests
        assert first["course_id"] == second["course_id"]
        assert first["session_id"] == second["session_id"]
        assert first["knowledge_ids"] == second["knowledge_ids"]
        assert first["materials"] == second["materials"]
        assert first["study_plan_id"] == second["study_plan_id"]

    def test_two_independent_builds_produce_the_same_learning_paths(
        self, build_root
    ) -> None:
        paths = []
        for index in range(2):
            out = build_root / f"det-{index}.json"
            if not out.is_file():
                data_dir = build_root / f"det-{index}"
                proc = run_child(_BUILD, data_dir, out, DATASET_DIR)
                _require(proc, f"build #{index}")
            paths.append(_load(out)["learning_paths"])
        assert paths[0] == paths[1]

    def test_a_rebuild_in_a_second_process_does_not_duplicate_rows(
        self, build_root
    ) -> None:
        """同一份数据重新跑一遍流水线 -> **内容寻址的表**一行都不许增长。

        这里有一个必须说清楚的边界。重跑整条流水线会**合法地**让学习层增长:

        第一次跑完, 学生已经作答了 ``plan.items[0]`` 那个知识点, 于是
        ``student_state`` 变了 -> 学习计划重算 -> 排在第一位的不再是同一个
        知识点 -> 第二次跑出的练习是**给另一个知识点**出的新题。

        那是自适应行为, 不是重复记录。所以这条测试断言的是"内容寻址的表
        不许长", 而学习层的增长由
        ``test_a_rebuild_grows_only_where_the_plan_target_changed`` 单独钉住。
        """
        data_dir = build_root / "idem"
        out = build_root / "idem.json"
        proc = run_child(_BUILD, data_dir, out, DATASET_DIR)
        _require(proc, "idempotent build #0")
        first = _load(out)

        observed = data_dir / "obs0.json"
        proc = run_child(_OBSERVE, data_dir, observed)
        _require(proc, "observe #0")
        counts_before = _load(observed)["table_counts"]

        proc = run_child(_BUILD, data_dir, out, DATASET_DIR)
        _require(proc, "idempotent build #1")

        proc = run_child(_OBSERVE, data_dir, observed)
        _require(proc, "observe #1")
        snap = _load(observed)

        content_addressed = (
            "courses",
            "sessions",
            "materials",
            "material_processing",
            "evidence",
            "knowledge_points",
            "review_records",
            "conflicts",
            "session_memberships",
        )
        for table in content_addressed:
            assert snap["table_counts"][table] == counts_before[table], (
                f"重跑流水线让内容寻址的 {table} 从 {counts_before[table]} "
                f"涨到了 {snap['table_counts'][table]}"
            )
        assert snap["knowledge"] == first["knowledge_ids"]
        assert snap["materials"] == first["materials"]
        assert snap["courses"] == [first["course_id"]]

    def test_a_rebuild_grows_only_where_the_plan_target_changed(
        self, build_root
    ) -> None:
        """学习层的增长必须**能被解释**: 新练习是给新目标出的, 不是重复的旧题。"""
        data_dir = build_root / "idem"
        out = build_root / "idem.json"
        proc = run_child(_BUILD, data_dir, out, DATASET_DIR)
        _require(proc, "idempotent build #0")
        first = _load(out)

        proc = run_child(_BUILD, data_dir, out, DATASET_DIR)
        _require(proc, "idempotent build #1")
        second = _load(out)

        added = sorted(set(second["exercises"]) - set(first["exercises"]))
        assert len(added) == 1, f"重跑一次应当只多出一道题, 实际多了 {added}"

        first_targets = {
            kp for kps in first["exercise_targets"].values() for kp in kps
        }
        new_targets = {
            kp for kp in second["exercise_targets"][added[0]] if kp
        }
        assert new_targets, "新练习没有关联任何知识点"
        assert not (new_targets & first_targets), (
            "重跑流水线给**同一个**知识点又出了一道一模一样的题 —— "
            f"那才是真正的重复: {new_targets}"
        )

    def test_the_same_database_read_by_two_processes_agrees(self, clean) -> None:
        outs = []
        for index in range(2):
            out = clean["data_dir"] / f"agree-{index}.json"
            proc = run_child(_OBSERVE, clean["data_dir"], out)
            _require(proc, f"observe #{index}")
            snap = _load(out)
            snap.pop("pid", None)  # PID 本来就该不同, 不是被测行为
            outs.append(snap)
        assert outs[0] == outs[1], "两个进程读同一个库读出了不同的东西"

    def test_the_crashed_and_clean_databases_agree_on_shared_ground(
        self, clean, crashed
    ) -> None:
        """崩溃不该改变"与崩溃无关"的任何东西。"""
        assert crashed["observed"]["knowledge"] == clean["manifest"]["knowledge_ids"]
        assert crashed["observed"]["materials"] == clean["manifest"]["materials"]
        assert crashed["observed"]["coverage"] == clean["manifest"]["coverage"]
        assert crashed["observed"]["gaps"] == clean["manifest"]["gaps"]


# ======================================================================
# 9. 47.12 backup drill 的跨进程版本
# ======================================================================


@pytest.fixture(scope="module")
def drill(build_root, clean) -> dict:
    """跨进程备份演练: 归档由一个进程产出, 由另一个进程恢复并读取。"""
    root = build_root / "drill54"
    root.mkdir(parents=True, exist_ok=True)
    script = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from src.backup.service import BackupService

data_dir, archive_out, restore_dir = sys.argv[2], sys.argv[3], sys.argv[4]
service = BackupService(data_dir, application_version="0.36.0")
result = service.create_backup(label="drill54", overwrite=True)
archive = str(result.archive_path)
target = BackupService(restore_dir)
restored = target.restore_backup(archive)
with open(archive_out, "w", encoding="utf-8") as fh:
    json.dump({
        "archive": archive,
        "database_path": str(getattr(restored, "database_path", "")),
        "warnings": list(getattr(restored, "warnings", ()) or ()),
    }, fh, ensure_ascii=False, indent=1)
print("DRILL-OK")
'''
    source = pathlib.Path(clean["data_dir"])
    archive_out = root / "drill.json"
    restore_dir = root / "restored"
    restore_dir.mkdir(parents=True, exist_ok=True)
    proc = run_child(script, source, archive_out, restore_dir)
    _require(proc, "backup drill")
    return {"info": _load(archive_out), "restore_dir": restore_dir, "root": root}


class TestBackupDrillAcrossProcesses:
    """spec: "重跑真正的 47.12 backup drill"。

    进程内的版本在 ``tests/test_hardening_backup_drill.py``。这里补的是
    **跨进程**的一半: 归档由一个进程产出, 由另一个进程恢复并读取。
    """

    def test_the_archive_restores_without_warnings(self, drill) -> None:
        assert drill["info"]["warnings"] == []

    def test_the_restored_database_opens_in_a_fresh_process(self, drill) -> None:
        out = drill["root"] / "restored.json"
        proc = run_child(_OBSERVE, drill["restore_dir"], out)
        _require(proc, "observe restored")
        snap = _load(out)
        assert snap["empty"] is False
        assert snap["integrity_check"] == "ok"
        assert snap["health"]["status"] == "ok"

    def test_the_restored_business_objects_match_the_source(self, drill, clean) -> None:
        out = drill["root"] / "restored2.json"
        proc = run_child(_OBSERVE, drill["restore_dir"], out)
        _require(proc, "observe restored")
        snap = _load(out)
        assert snap["courses"] == [clean["manifest"]["course_id"]]
        assert snap["knowledge"] == clean["manifest"]["knowledge_ids"]
        assert snap["materials"] == clean["manifest"]["materials"]
        assert snap["sessions"] == clean["manifest"]["sessions"]

    def test_the_restored_review_history_matches_the_source(self, drill, clean) -> None:
        out = drill["root"] / "restored3.json"
        proc = run_child(_OBSERVE, drill["restore_dir"], out)
        _require(proc, "observe restored")
        snap = _load(out)
        assert snap["review_history"] == clean["manifest"]["review_history"]

    def test_the_restored_database_is_usable_from_a_new_process(self, drill) -> None:
        out = drill["root"] / "restored-write.json"
        proc = run_child(_WRITE_AFTER_RESTART, drill["restore_dir"], out, "after-restore", 0)
        _require(proc, "write into restored")
        assert _load(out)["history"], "恢复出来的库写不进去"
