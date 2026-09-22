# -*- coding: utf-8 -*-
"""Task 53 — Transactional Application Operations（事务化业务操作）。

spec 53 要求::

    建立可注入 failure point (after material insert / evidence insert /
    knowledge insert / review insert / answer insert)
    验证 failure -> rollback

本文件把这条要求做成**穷尽**验收, 而不是抽样: 对每个业务操作, 先在一次性
副本上数出它会做多少次落盘, 然后**每一次**都在另一份副本上注入一次失败,
每次都断言数据库与操作前逐行相同。

为什么"逐行相同"是唯一值得信的判据
--------------------------------------------------------------------

断言"证据表还是 0 行"只能证明某一个具体缺陷不存在。断言**整个数据库的
逻辑内容与操作前完全一致**, 才能证明"半成品不可能存在"这条性质本身 ——
包括将来新增的表, 因为快照是遍历 `table_names()` 得到的, 不是手写的表清单。

为什么用两份副本
--------------------------------------------------------------------

观察落盘顺序这件事本身**会真的把数据写进去** (注入器只是旁观者)。在同一份
数据上先观察再断言, 断言的就是被观察污染过的状态, 而不是"失败之后的原始
状态"。所以观察用一次性副本, 断言用另一份干净副本。

三层保证
--------------------------------------------------------------------

1. **注入点覆盖** (`TestFaultInjectionPoints`): 每个命名落盘点都真的可达,
   并且在该点失败会让操作整体回滚。
2. **穷尽回滚** (`TestExhaustiveRollback`): 对每个写操作, `k = 1..N` 全部
   试一遍, 一次都不许留下痕迹。
3. **失败之后** (`TestAfterAFailure`): 失败不留半成品、不留下打开的事务、
   不污染下一次操作、重启后仍然是操作前的状态。
"""

from __future__ import annotations

import json
import pathlib
import shutil
from typing import Any, Callable, Optional

import pytest

from src.application.acceptance import (
    DEFAULT_ACCEPTANCE_CLOCK,
    AcceptanceHarness,
    ClassroomDataset,
)
from src.application.errors import (
    ApplicationError,
    InvalidInputError,
    StorageError,
)
from src.application.persistence_wiring import (
    FAULT_POINTS,
    WorkspacePersistence,
    default_database_path,
)
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace
from src.persistence import open_database
from src.persistence.errors import PersistenceError

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "acceptance"

#: 与验收夹具一致的固定时钟 (见 ``make_sandbox`` 的 docstring)。
ACCEPTANCE_CLOCK = DEFAULT_ACCEPTANCE_CLOCK

#: ``create_exercise`` 的选项形状: ``Choice(choice_id, text)`` 的字典形式。
_CHOICES = (
    {"choice_id": "a", "text": "Uno"},
    {"choice_id": "b", "text": "Dos"},
    {"choice_id": "c", "text": "Tres"},
)


class Boom(RuntimeError):
    """模拟"进程在写第 N 行时炸了"。

    故意**不是** ``PersistenceError``: 真实故障往往也不是存储层自己报的错
    (磁盘满 / 进程被 OOM killer 干掉 / 断电)。如果只有存储层异常能回滚,
    那回滚就只是"存储层自己的错误处理", 不是业务操作的性质。
    """


# ======================================================================
# 夹具
# ======================================================================


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """跑一遍真实验收流水线, 得到一个"有内容"的工作区 (只读基线)。"""
    root = tmp_path_factory.mktemp("task53")
    dataset_dir = root / "dataset"
    shutil.copytree(FIXTURE_DIR, dataset_dir)
    data_dir = str(root / "data")
    harness = AcceptanceHarness(
        ClassroomDataset.from_directory(str(dataset_dir)), data_dir=data_dir
    )
    report = harness.run()
    assert report.all_steps_ok, f"流水线失败: {report.failed_steps}"
    payload = {
        "data_dir": data_dir,
        "course_id": harness.course_id,
        "session_id": harness.session_id,
        "student_id": harness.student_id,
    }
    harness.workspace.close()
    return payload


@pytest.fixture
def make_sandbox(built, tmp_path):
    """每次调用都返回一份**新的**可写副本 + 已打开的工作区。

    用工厂而不是单一副本, 是因为本文件里"观察"与"断言"必须发生在两份不同
    的数据上 (见模块 docstring)。

    固定时钟: 本文件里有几处要比较"两条路径产生的数据库是否逐行相同", 而
    时间戳是唯一会让它们不同的东西 (``created_at`` / ``last_attempt_at``)。
    项目本来就规定"唯一非确定性来源是可注入 ``Clock``", 这里把它钉死,
    于是差异只可能来自真实的业务行为差异。
    """
    made: list[Workspace] = []
    counter = {"n": 0}

    def _make(name: Optional[str] = None) -> dict[str, Any]:
        counter["n"] += 1
        root = tmp_path / (name or f"box{counter['n']}")
        shutil.copytree(built["data_dir"], root)
        workspace = Workspace(str(root), clock=fixed_clock(ACCEPTANCE_CLOCK))
        made.append(workspace)
        return {**built, "data_dir": str(root), "workspace": workspace}

    try:
        yield _make
    finally:
        for workspace in made:
            workspace.close()


@pytest.fixture
def sandbox(make_sandbox):
    """只要一份副本的测试用这个。"""
    return make_sandbox("main")


# ======================================================================
# 工具
# ======================================================================


def _raw_rows(data_dir: str) -> dict[str, list[dict[str, Any]]]:
    """整个数据库的原始行: 表名 -> 行字典列表 (按表名排序)。"""
    database = open_database(default_database_path(data_dir), migrate=False)
    try:
        snapshot: dict[str, list[dict[str, Any]]] = {}
        for name in sorted(database.table_names()):
            if name.startswith("sqlite_"):
                continue
            snapshot[name] = [dict(row) for row in database.query(f"SELECT * FROM {name}")]
        return snapshot
    finally:
        database.close()


def _dump(data_dir: str) -> dict[str, list[str]]:
    """整个数据库的逻辑快照: 表名 -> 排序后的行 (规范化 JSON)。

    用**独立连接**读, 因此看到的是已提交状态 —— 未提交的事务不会污染
    比较结果, 这正是"回滚是否真的发生了"要问的问题。
    """
    return {
        table: sorted(
            json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
            for row in rows
        )
        for table, rows in _raw_rows(data_dir).items()
    }


def _scrub(value: Any, variants: set[str]) -> Any:
    """递归把字符串里的 ``data_dir`` 前缀换成占位符。

    在**解析之后、序列化之前**做, 免得跟 JSON 的转义反斜杠打架
    (``C:\\\\Users\\\\...`` 在序列化后的文本里是四层反斜杠)。
    """
    if isinstance(value, str):
        for variant in variants:
            value = value.replace(variant, "<DATA_DIR>")
        return value
    if isinstance(value, dict):
        return {key: _scrub(item, variants) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, variants) for item in value]
    return value


def _dump_normalised(data_dir: str) -> dict[str, list[str]]:
    """同 :func:`_dump`, 但归一化两类**与位置 / 历史有关**的字段:

    - ``material_processing`` 的 ``attempts`` —— 尝试次数, 重试路径会多一次;
    - 任何落在 ``data_dir`` 下的绝对路径 (``stored_path``) —— 两份副本的
      目录名不同, 那是**测试自己**造成的差异, 不是产品行为差异。

    归一化是**显式列出**的, 不是模糊匹配: 除这两项外任何差异都会让断言变红。
    """
    variants = {str(pathlib.Path(data_dir).resolve()), data_dir}
    out: dict[str, list[str]] = {}
    for table, rows in _raw_rows(data_dir).items():
        normalised: list[str] = []
        for row in rows:
            row = dict(row)
            for key, value in list(row.items()):
                if isinstance(value, str) and value[:1] in "{[":
                    try:
                        parsed = json.loads(value)
                    except ValueError:
                        parsed = None
                    if isinstance(parsed, (dict, list)):
                        parsed = _scrub(parsed, variants)
                        if (
                            table == "material_processing"
                            and isinstance(parsed, dict)
                            and "attempts" in parsed
                        ):
                            parsed["attempts"] = "NORMALISED"
                        row[key] = json.dumps(parsed, sort_keys=True)
                        continue
                row[key] = _scrub(value, variants)
            normalised.append(
                json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
            )
        out[table] = sorted(normalised)
    return out


def _attempts(data_dir: str) -> list[int]:
    """``material_processing`` 表里每条记录的 ``attempts`` (确定性排序)。"""
    values: list[int] = []
    for row in _raw_rows(data_dir).get("material_processing", []):
        try:
            payload = json.loads(row.get("payload") or "{}")
        except ValueError:
            continue
        if isinstance(payload, dict) and "attempts" in payload:
            values.append(int(payload["attempts"]))
    return sorted(values)


def _first_exercise_id(workspace: Workspace, box: dict[str, Any]) -> str:
    return str(workspace.list_exercises(box["course_id"])[0]["exercise_id"])


def _write_materials(
    box: dict[str, Any], count: int, prefix: str, *, session_id: Optional[str] = None
) -> list[str]:
    """在副本里一次性登记 ``count`` 份材料 (批量, 返回路径)。"""
    root = pathlib.Path(box["data_dir"])
    paths: list[str] = []
    for index in range(count):
        path = root / f"{prefix}-{index}.md"
        path.write_text(
            f"Definicio {prefix}{index}: el proces {index} consumeix memoria cache.\n",
            encoding="utf-8",
        )
        paths.append(str(path))
    box["workspace"].register_material_batch(
        box["course_id"], paths, session_id=session_id
    )
    return paths


def _count(data_dir: str, table: str) -> int:
    database = open_database(default_database_path(data_dir), migrate=False)
    try:
        return int(database.scalar(f"SELECT COUNT(*) FROM {table}", (), default=0) or 0)
    finally:
        database.close()


class _FailAt:
    """在第 ``ordinal`` 次落盘之后抛 ``Boom``; 同时记录走过的点。"""

    def __init__(self, ordinal: int = 1) -> None:
        self.ordinal = ordinal
        self.seen: list[tuple[str, int]] = []

    def __call__(self, point: str, ordinal: int) -> None:
        self.seen.append((point, ordinal))
        if ordinal == self.ordinal:
            raise Boom(f"injected failure after {point} #{ordinal}")


class _Count:
    """只记录、不抛异常 —— 用来数出某个操作会做多少次落盘。"""

    def __init__(self) -> None:
        self.seen: list[tuple[str, int]] = []

    def __call__(self, point: str, ordinal: int) -> None:
        self.seen.append((point, ordinal))

    @property
    def total(self) -> int:
        return len(self.seen)

    @property
    def points(self) -> list[str]:
        return [point for point, _ in self.seen]


def _observe(box: dict[str, Any], action: Callable[[Workspace], Any]) -> _Count:
    """在给定副本上跑一次 ``action``, 记录它做了哪些落盘。"""
    counter = _Count()
    box["workspace"].persistence.set_fault_injector(counter)
    try:
        action(box["workspace"])
    finally:
        box["workspace"].persistence.set_fault_injector(None)
    return counter


def _write_material(box: dict[str, Any], name: str) -> str:
    """在副本里登记一份新材料 (会被真正落盘)。"""
    path = pathlib.Path(box["data_dir"]) / name
    path.write_text(
        "Definicio cache: la memoria cache redueix la latencia del proces.\n"
        "Un proces es un programa en execucio.\n",
        encoding="utf-8",
    )
    record = box["workspace"].register_material(box["course_id"], str(path))
    return str(record["material_id"])


def _first_ordinal(box: dict[str, Any], point: str, setup, action) -> int:
    """先在一次性副本上观察, 返回 ``point`` 首次出现的落盘序号 (从 1 计)。"""
    observer = box["make_sandbox"]()
    if setup is not None:
        setup(observer)
    counter = _observe(observer, action)
    assert point in counter.points, (
        f"落盘序列里没有 {point!r} (实际: {counter.points})"
    )
    return counter.points.index(point) + 1


def _expect_rollback(
    make_sandbox,
    built: dict[str, Any],
    point: str,
    action: Callable[[Workspace], Any],
    *,
    setup: Optional[Callable[[dict[str, Any]], None]] = None,
) -> int:
    """在 ``point`` **首次出现**处注入失败, 断言整个操作被回滚。

    先在一次性副本上观察落盘顺序 (这一步会真的写数据, 所以不能用它做断言),
    再在另一份干净副本上注入失败并逐行比较数据库。

    返回落盘点总数。
    """
    observer = make_sandbox("observe")
    if setup is not None:
        setup(observer)
    counter = _observe(observer, action)
    assert point in counter.points, (
        f"落盘序列里没有 {point!r} (实际: {counter.points})"
    )
    target = counter.points.index(point) + 1

    subject = make_sandbox("subject")
    if setup is not None:
        setup(subject)
    before = _dump(subject["data_dir"])

    fail = _FailAt(target)
    subject["workspace"].persistence.set_fault_injector(fail)
    try:
        with pytest.raises(Boom):
            action(subject["workspace"])
    finally:
        subject["workspace"].persistence.set_fault_injector(None)

    assert fail.seen[target - 1][0] == point, (
        f"期望第 {target} 次落盘是 {point!r}, 实际是 {fail.seen[target - 1]!r}"
    )
    assert _dump(subject["data_dir"]) == before, (
        f"在 {point!r} 处失败后数据库留下了痕迹"
    )
    assert subject["workspace"].persistence.database.in_transaction is False
    assert subject["workspace"].persistence.rollback_diagnostics()["count"] == 1
    return counter.total


# ======================================================================
# 53.1 事务边界本身
# ======================================================================


class TestTransactionBoundary:
    def test_a_workspace_starts_with_no_open_transaction(self, sandbox) -> None:
        assert sandbox["workspace"].persistence.database.in_transaction is False

    def test_a_workspace_starts_with_no_rollbacks(self, sandbox) -> None:
        assert sandbox["workspace"].persistence.rollback_diagnostics()["count"] == 0

    def test_the_write_ordinal_is_zero_outside_an_operation(self, sandbox) -> None:
        assert sandbox["workspace"].persistence.write_ordinal == 0

    def test_a_successful_write_does_not_leave_a_transaction_open(self, sandbox) -> None:
        workspace = sandbox["workspace"]
        workspace.create_student(sandbox["course_id"], "student-tx-1", "Tx Uno")
        assert workspace.persistence.database.in_transaction is False

    def test_a_failed_write_does_not_leave_a_transaction_open(self, sandbox) -> None:
        workspace = sandbox["workspace"]
        workspace.persistence.set_fault_injector(_FailAt(1))
        with pytest.raises(Boom):
            workspace.create_student(sandbox["course_id"], "student-tx-2", "Tx Dos")
        workspace.persistence.set_fault_injector(None)
        assert workspace.persistence.database.in_transaction is False

    def test_the_write_ordinal_returns_to_zero_after_success(self, sandbox) -> None:
        workspace = sandbox["workspace"]
        workspace.create_student(sandbox["course_id"], "student-tx-3", "Tx Tres")
        assert workspace.persistence.write_ordinal == 0

    def test_the_write_ordinal_returns_to_zero_after_failure(self, sandbox) -> None:
        workspace = sandbox["workspace"]
        workspace.persistence.set_fault_injector(_FailAt(1))
        with pytest.raises(Boom):
            workspace.create_student(sandbox["course_id"], "student-tx-4", "Tx Cuatro")
        workspace.persistence.set_fault_injector(None)
        assert workspace.persistence.write_ordinal == 0

    def test_a_rollback_does_not_block_the_next_operation(self, sandbox) -> None:
        """回滚必须把写锁还回去 —— 否则"失败一次以后整个工作区写不动了"。"""
        workspace = sandbox["workspace"]
        workspace.persistence.set_fault_injector(_FailAt(1))
        with pytest.raises(Boom):
            workspace.create_student(sandbox["course_id"], "student-tx-5", "Tx Cinco")
        workspace.persistence.set_fault_injector(None)
        workspace.create_student(sandbox["course_id"], "student-tx-6", "Tx Seis")
        assert workspace.get_student(sandbox["course_id"], "student-tx-6")

    def test_a_failed_operation_does_not_poison_the_retry(self, sandbox) -> None:
        workspace = sandbox["workspace"]
        workspace.persistence.set_fault_injector(_FailAt(1))
        with pytest.raises(Boom):
            workspace.create_student(sandbox["course_id"], "student-tx-7", "Tx Siete")
        workspace.persistence.set_fault_injector(None)
        dto = workspace.create_student(sandbox["course_id"], "student-tx-7", "Tx Siete")
        assert dto["student_id"] == "student-tx-7"
        assert workspace.get_student(sandbox["course_id"], "student-tx-7")

    def test_nested_atomic_blocks_are_safe(self, sandbox) -> None:
        """嵌套是 SAVEPOINT: 内层回滚不该把外层已经写好的东西带走。"""
        workspace = sandbox["workspace"]
        persistence = workspace.persistence
        course = workspace.context(sandbox["course_id"]).course
        with persistence.atomic():
            persistence.save_course(course)
            with pytest.raises(Boom):
                with persistence.atomic():
                    persistence.save_course(course)
                    raise Boom("inner")
        assert persistence.database.in_transaction is False
        assert persistence.rollback_diagnostics()["count"] == 1

    def test_a_memory_only_workspace_has_no_transaction_machinery(self, tmp_path) -> None:
        """``persistence=False`` 时 ``_atomic`` 是空操作 —— 不假装有事务。"""
        workspace = Workspace(str(tmp_path / "memory"), persistence=False)
        try:
            assert workspace.persistence is None
            dto = workspace.create_course("Memoria", "MEM", "es")
            assert dto["course_id"]
        finally:
            workspace.close()

    def test_the_fault_injector_must_be_callable(self, sandbox) -> None:
        with pytest.raises(TypeError):
            sandbox["workspace"].persistence.set_fault_injector(object())

    def test_the_fault_injector_can_be_removed(self, sandbox) -> None:
        persistence = sandbox["workspace"].persistence
        injector = _Count()
        persistence.set_fault_injector(injector)
        assert persistence.fault_injector is injector
        persistence.set_fault_injector(None)
        assert persistence.fault_injector is None

    def test_the_default_workspace_has_no_fault_injector(self, sandbox) -> None:
        """生产代码永远不装注入器。"""
        assert sandbox["workspace"].persistence.fault_injector is None

    def test_the_fault_points_are_a_tuple_of_unique_names(self) -> None:
        assert isinstance(FAULT_POINTS, tuple)
        assert len(FAULT_POINTS) == len(set(FAULT_POINTS))
        assert all(name.strip() for name in FAULT_POINTS)

    def test_every_named_fault_point_is_reachable(self, make_sandbox) -> None:
        """``FAULT_POINTS`` 不是愿望清单 —— 每个名字都必须真的会出现。

        做法: 走一遍完整生命周期 (建课程 / 课堂 / 材料 / 处理 / 审核 / 练习 /
        作答 / 计划 / 路径), 把实际出现过的点名与常量对表。
        """
        from src.study_plan import LearningPath

        box = make_sandbox("reach")
        workspace = box["workspace"]
        course_id = box["course_id"]
        counter = _Count()
        workspace.persistence.set_fault_injector(counter)
        try:
            new_course = workspace.create_course("Reach", "RCH", "es")
            workspace.create_session(new_course["course_id"], session_number=1)
            material_id = _write_material(box, "reach.md")
            workspace.process_material(course_id, material_id)
            candidate = workspace.knowledge_points(course_id)[0]["knowledge_id"]
            workspace.review_confirm(course_id, candidate)
            workspace.create_exercise(
                course_id,
                "multiple_choice",
                "¿Que es un proceso?",
                [candidate],
                choices=_CHOICES,
                correct_choice_id="a",
            )
            exercise = workspace.list_exercises(course_id)[0]
            workspace.submit_answer(
                course_id, box["student_id"], exercise["exercise_id"], "b"
            )
            workspace.study_plan(course_id, box["student_id"])
            path = LearningPath.from_dict(
                workspace.learning_path(course_id, candidate)
            )
            with workspace.persistence.atomic():
                workspace.persistence.save_learning_paths(
                    [(path, course_id, box["student_id"])]
                )
        finally:
            workspace.persistence.set_fault_injector(None)

        seen = set(counter.points)
        missing = sorted(set(FAULT_POINTS) - seen)
        assert not missing, f"这些注入点在真实路径上从未出现: {missing}"


# ======================================================================
# 53.2 每个注入点都能触发整体回滚
# ======================================================================


class TestFaultInjectionPoints:
    """逐个点名验收 spec 列出的五类插入 + 其余全部落盘步骤。"""

    # ---- spec 点名的五类插入 ------------------------------------------

    def test_failing_after_a_material_insert_rolls_back(self, make_sandbox, built) -> None:
        holder: dict[str, str] = {}

        def setup(box):
            path = pathlib.Path(box["data_dir"]) / "tx-material.md"
            path.write_text("Definicio bucle: una repeticio.\n", encoding="utf-8")
            holder["path"] = str(path)

        _expect_rollback(
            make_sandbox,
            built,
            "material",
            lambda w: w.register_material(built["course_id"], holder["path"]),
            setup=setup,
        )

    def test_failing_after_an_evidence_insert_rolls_back(self, make_sandbox, built) -> None:
        material = {"id": ""}

        def setup(box):
            material["id"] = _write_material(box, "tx-evidence.md")

        _expect_rollback(
            make_sandbox,
            built,
            "evidence",
            lambda w: w.process_material(built["course_id"], material["id"]),
            setup=setup,
        )

    def test_failing_after_a_knowledge_insert_rolls_back(self, make_sandbox, built) -> None:
        material = {"id": ""}

        def setup(box):
            material["id"] = _write_material(box, "tx-knowledge.md")

        _expect_rollback(
            make_sandbox,
            built,
            "knowledge_structure",
            lambda w: w.process_material(built["course_id"], material["id"]),
            setup=setup,
        )

    def test_failing_after_a_review_insert_rolls_back(self, make_sandbox, built) -> None:
        target = {"id": ""}

        def setup(box):
            target["id"] = box["workspace"].knowledge_points(box["course_id"])[0][
                "knowledge_id"
            ]

        _expect_rollback(
            make_sandbox,
            built,
            "review",
            lambda w: w.review_confirm(built["course_id"], target["id"]),
            setup=setup,
        )

    def test_failing_after_an_answer_insert_rolls_back(self, make_sandbox, built) -> None:
        exercise = {"id": ""}

        def setup(box):
            exercise["id"] = box["workspace"].list_exercises(box["course_id"])[0][
                "exercise_id"
            ]

        _expect_rollback(
            make_sandbox,
            built,
            "answer",
            lambda w: w.submit_answer(
                built["course_id"], built["student_id"], exercise["id"], "a", sequence=11
            ),
            setup=setup,
        )

    # ---- 其余全部落盘步骤 ---------------------------------------------

    def test_failing_after_a_course_insert_rolls_back(self, make_sandbox, built) -> None:
        _expect_rollback(
            make_sandbox,
            built,
            "course",
            lambda w: w.create_course("Nueva", "NUE", "es"),
        )

    def test_failing_after_a_session_insert_rolls_back(self, make_sandbox, built) -> None:
        _expect_rollback(
            make_sandbox,
            built,
            "session",
            lambda w: w.create_session(built["course_id"], session_number=99, title="Nueva"),
        )

    def test_failing_after_an_organization_write_rolls_back(self, make_sandbox, built) -> None:
        """``organization`` 只在课程上下文已建立时才会被写。

        刚打开的工作区里上下文是**惰性**的: ``create_session`` 找不到上下文
        就跳过组织层登记, 等下一次 ``context()`` 构建时再由
        ``_build_context`` 幂等补齐。所以这里先建立上下文, 才能验到那个点。
        """

        def setup(box):
            box["workspace"].context(box["course_id"])

        _expect_rollback(
            make_sandbox,
            built,
            "organization",
            lambda w: w.create_session(built["course_id"], session_number=98, title="Org"),
            setup=setup,
        )

    def test_failing_after_a_student_log_write_rolls_back(self, make_sandbox, built) -> None:
        _expect_rollback(
            make_sandbox,
            built,
            "student_log",
            lambda w: w.create_student(built["course_id"], "student-log", "Log"),
        )

    def test_failing_after_an_exercise_insert_rolls_back(self, make_sandbox, built) -> None:
        target = {"id": ""}

        def setup(box):
            target["id"] = box["workspace"].knowledge_points(box["course_id"])[0][
                "knowledge_id"
            ]

        _expect_rollback(
            make_sandbox,
            built,
            "exercise",
            lambda w: w.create_exercise(
                built["course_id"],
                "multiple_choice",
                "¿Que es un proceso?",
                [target["id"]],
                choices=_CHOICES,
                correct_choice_id="a",
            ),
            setup=setup,
        )

    def test_failing_after_an_evaluation_insert_rolls_back(self, make_sandbox, built) -> None:
        exercise = {"id": ""}

        def setup(box):
            exercise["id"] = box["workspace"].list_exercises(box["course_id"])[0][
                "exercise_id"
            ]

        _expect_rollback(
            make_sandbox,
            built,
            "evaluation",
            lambda w: w.submit_answer(
                built["course_id"], built["student_id"], exercise["id"], "b", sequence=12
            ),
            setup=setup,
        )

    def test_failing_after_a_study_plan_insert_rolls_back(self, make_sandbox, built) -> None:
        _expect_rollback(
            make_sandbox,
            built,
            "study_plan",
            lambda w: w.study_plan(built["course_id"], built["student_id"]),
        )

    def test_failing_after_a_learning_path_insert_rolls_back(self, make_sandbox, built) -> None:
        """学习路径**没有产品写入路径** (Task 52 判定它是派生值)。

        所以这里直接调仓储 —— 但必须**显式包在 ``persistence.atomic()`` 里**:
        ``save_*`` 是可组合的落盘步骤, 不是操作边界。不包的话每次写入各自
        自动提交, 注入的失败就落在了一个已经提交的语句之后, 回滚无从谈起。
        """
        from src.study_plan import LearningPath

        path = {"obj": None}

        def setup(box):
            workspace = box["workspace"]
            target = workspace.knowledge_points(box["course_id"])[0]["knowledge_id"]
            path["obj"] = LearningPath.from_dict(
                workspace.learning_path(box["course_id"], target)
            )

        def action(workspace):
            with workspace.persistence.atomic():
                workspace.persistence.save_learning_paths(
                    [(path["obj"], built["course_id"], built["student_id"])]
                )

        _expect_rollback(
            make_sandbox,
            built,
            "learning_path",
            action,
            setup=setup,
        )

    def test_an_injected_failure_inside_a_large_processing_run_rolls_back(
        self, make_sandbox, built
    ) -> None:
        """真正危险的那种: 十几条证据已经写进去, 知识点那一步炸了。

        一份文档只产出一条证据, 所以"多证据"要靠**多份材料**: 给同一节课
        批量登记 12 份材料, 再整体处理这节课。
        """
        files = {"paths": []}

        def setup(box):
            files["paths"] = _write_materials(
                box, 12, "big", session_id=box["session_id"]
            )

        observer = make_sandbox("observe-big")
        setup(observer)
        counter = _observe(
            observer,
            lambda w: w.process_session(built["course_id"], built["session_id"]),
        )
        evidence_writes = counter.points.count("evidence")
        assert evidence_writes >= 10, (
            f"数据集太小, 验不出中间状态: 只有 {evidence_writes} 条证据"
        )
        assert "knowledge_structure" in counter.points
        target = counter.points.index("knowledge_structure") + 1

        subject = make_sandbox("subject-big")
        setup(subject)
        before = _dump(subject["data_dir"])
        subject["workspace"].persistence.set_fault_injector(_FailAt(target))
        try:
            with pytest.raises(Boom):
                subject["workspace"].process_session(
                    built["course_id"], built["session_id"]
                )
        finally:
            subject["workspace"].persistence.set_fault_injector(None)

        assert _dump(subject["data_dir"]) == before, (
            "证据写进去了、知识点没有 —— 半成品留在库里"
        )

    def test_failing_in_the_middle_of_a_batch_registration_rolls_back(
        self, make_sandbox, built
    ) -> None:
        """批量登记 30 份材料, 在第 15 次落盘处失败 —— 前 14 条一条都不许留下。

        这是最容易被忽略的一种半成品: 单条登记失败只是"这次没成功", 而批量
        登记写到一半失败会留下一个**看起来正常的半批材料**, 用户完全看不出
        少了 16 份。
        """
        files = {"paths": []}

        def setup(box):
            root = pathlib.Path(box["data_dir"])
            paths = []
            for index in range(30):
                path = root / f"batch-{index}.md"
                path.write_text(
                    f"Definicio batch{index}: contingut.\n", encoding="utf-8"
                )
                paths.append(str(path))
            files["paths"] = paths

        observer = make_sandbox("observe-batch")
        setup(observer)
        counter = _observe(
            observer, lambda w: w.register_material_batch(built["course_id"], files["paths"])
        )
        total = counter.total
        assert total >= 30, f"批量登记只产生了 {total} 次落盘, 验不出中间状态"
        target = total // 2

        subject = make_sandbox("subject-batch")
        setup(subject)
        before = _dump(subject["data_dir"])
        subject["workspace"].persistence.set_fault_injector(_FailAt(target))
        try:
            with pytest.raises(Boom):
                subject["workspace"].register_material_batch(
                    built["course_id"], files["paths"]
                )
        finally:
            subject["workspace"].persistence.set_fault_injector(None)

        assert _dump(subject["data_dir"]) == before, (
            f"在第 {target}/{total} 条材料处失败后, 前 {target - 1} 条留在了库里"
        )


# ======================================================================
# 53.3 穷尽回滚: 每个写操作的每一次落盘
# ======================================================================


class TestExhaustiveRollback:
    """对每个写操作, 依次在第 1、2、…、N 次落盘处注入失败, 每次都必须干净。"""

    def _exhaustive(
        self,
        make_sandbox,
        action: Callable[[Workspace], Any],
        *,
        setup: Optional[Callable[[dict[str, Any]], None]] = None,
        label: str,
        expect: tuple[str, ...] = (),
    ) -> int:
        """返回落盘点总数; 每个点都注入过一次失败并断言过回滚。

        ``expect`` 断言**写点的种类**必须出现。这比单纯数"写了几次"强:
        次数会随实现变化 (Task 69 把"提交一条答案重写整门课"改成了只写
        这条答案, 次数从 4 降到 3), 而"这次操作必须写答案和评估"是**业务
        契约**, 不该跟着实现漂移。
        """
        observer = make_sandbox(f"{label}-observe")
        if setup is not None:
            setup(observer)
        counter = _observe(observer, action)
        total = counter.total
        assert total >= 1, f"{label}: 没有观察到任何落盘"
        missing = sorted(set(expect) - set(counter.points))
        assert not missing, f"{label}: 缺少落盘点 {missing}, 实际 {counter.points}"

        for k in range(1, total + 1):
            subject = make_sandbox(f"{label}-fail{k}")
            if setup is not None:
                setup(subject)
            before = _dump(subject["data_dir"])
            subject["workspace"].persistence.set_fault_injector(_FailAt(k))
            try:
                with pytest.raises(Boom):
                    action(subject["workspace"])
            finally:
                subject["workspace"].persistence.set_fault_injector(None)
            after = _dump(subject["data_dir"])
            assert after == before, (
                f"{label}: 在第 {k}/{total} 次落盘失败后留下痕迹"
            )
            assert subject["workspace"].persistence.database.in_transaction is False
        return total

    def test_study_plan_rolls_back_at_every_write(self, make_sandbox, built) -> None:
        total = self._exhaustive(
            make_sandbox,
            lambda w: w.study_plan(built["course_id"], built["student_id"]),
            label="study_plan",
        )
        assert total >= 1

    def test_create_session_rolls_back_at_every_write(self, make_sandbox, built) -> None:
        total = self._exhaustive(
            make_sandbox,
            lambda w: w.create_session(built["course_id"], session_number=301, title="X"),
            label="create_session",
        )
        assert total >= 3  # 课程 + 课堂 (幂等重写) + 组织

    def test_update_course_rolls_back_at_every_write(self, make_sandbox, built) -> None:
        total = self._exhaustive(
            make_sandbox,
            lambda w: w.update_course(built["course_id"], name="Nom Actualitzat"),
            label="update_course",
        )
        assert total >= 1

    def test_create_student_rolls_back_at_every_write(self, make_sandbox, built) -> None:
        total = self._exhaustive(
            make_sandbox,
            lambda w: w.create_student(built["course_id"], "student-ex", "Ex"),
            label="create_student",
            # Task 69: 建学生只写这个学生的日志, 不再顺带重写整门课的练习
            # 与全部历史答案 —— 那些对象一个字节都没变。
            expect=("student_log",),
        )
        assert total >= 1

    def test_submit_answer_rolls_back_at_every_write(self, make_sandbox, built) -> None:
        exercise = {"id": ""}

        def setup(box):
            exercise["id"] = box["workspace"].list_exercises(box["course_id"])[0][
                "exercise_id"
            ]

        total = self._exhaustive(
            make_sandbox,
            lambda w: w.submit_answer(
                built["course_id"], built["student_id"], exercise["id"], "a", sequence=302
            ),
            setup=setup,
            label="submit_answer",
            # Task 69: 练习对象在作答时**没有被改动** (见
            # ``LearningService.submit_answer``, 它只动答案日志 / 评估 /
            # 该学生日志), 所以不再重写练习。写点从 4 降到 3, 但"答案 +
            # 评估 + 该生日志"这三类一个都不能少。
            expect=("student_log", "answer", "evaluation"),
        )
        assert total >= 3  # 学生日志 + 答案 + 评估

    def test_record_learning_event_rolls_back_at_every_write(self, make_sandbox, built) -> None:
        target = {"id": ""}

        def setup(box):
            target["id"] = box["workspace"].knowledge_points(box["course_id"])[0][
                "knowledge_id"
            ]

        total = self._exhaustive(
            make_sandbox,
            lambda w: w.record_learning_event(
                built["course_id"], built["student_id"], target["id"], "viewed"
            ),
            setup=setup,
            label="record_learning_event",
            # 同上: 一个学习事件只改这个学生的日志。
            expect=("student_log",),
        )
        assert total >= 1

    def test_create_exercise_rolls_back_at_every_write(self, make_sandbox, built) -> None:
        target = {"id": ""}

        def setup(box):
            target["id"] = box["workspace"].knowledge_points(box["course_id"])[0][
                "knowledge_id"
            ]

        total = self._exhaustive(
            make_sandbox,
            lambda w: w.create_exercise(
                built["course_id"],
                "multiple_choice",
                "¿Que es un proceso?",
                [target["id"]],
                choices=_CHOICES,
                correct_choice_id="a",
            ),
            setup=setup,
            label="create_exercise",
        )
        assert total >= 1

    def test_review_confirm_rolls_back_at_every_write(self, make_sandbox, built) -> None:
        target = {"id": ""}

        def setup(box):
            target["id"] = box["workspace"].knowledge_points(box["course_id"])[0][
                "knowledge_id"
            ]

        total = self._exhaustive(
            make_sandbox,
            lambda w: w.review_confirm(built["course_id"], target["id"]),
            setup=setup,
            label="review_confirm",
        )
        assert total >= 2  # 知识结构 + 评审历史

    def test_register_material_rolls_back_at_every_write(self, make_sandbox, built) -> None:
        holder = {"path": ""}

        def setup(box):
            path = pathlib.Path(box["data_dir"]) / "reg.md"
            path.write_text("Definicio reg: contingut.\n", encoding="utf-8")
            holder["path"] = str(path)

        total = self._exhaustive(
            make_sandbox,
            lambda w: w.register_material(built["course_id"], holder["path"]),
            setup=setup,
            label="register_material",
        )
        assert total >= 2

    def test_process_material_rolls_back_at_every_write(self, make_sandbox, built) -> None:
        material = {"id": ""}

        def setup(box):
            material["id"] = _write_material(box, "pm.md")

        total = self._exhaustive(
            make_sandbox,
            lambda w: w.process_material(built["course_id"], material["id"]),
            setup=setup,
            label="process_material",
        )
        assert total >= 5  # 证据 + 材料 + 知识结构 + 组织 + 学习

    def test_process_session_rolls_back_at_every_write(self, make_sandbox, built) -> None:
        def setup(box):
            path = pathlib.Path(box["data_dir"]) / "ps.md"
            path.write_text(
                "Definicio ps: el proces consumeix memoria cache.\n", encoding="utf-8"
            )
            box["workspace"].register_material(
                box["course_id"], str(path), session_id=box["session_id"]
            )

        total = self._exhaustive(
            make_sandbox,
            lambda w: w.process_session(built["course_id"], built["session_id"]),
            setup=setup,
            label="process_session",
        )
        assert total >= 5

    def test_the_write_count_is_deterministic(self, make_sandbox, built) -> None:
        """落盘点数量必须可复现, 否则上面的"穷尽"就是碰运气。"""
        counts = []
        for index in range(3):
            box = make_sandbox(f"determinism-{index}")
            counts.append(
                _observe(
                    box, lambda w: w.study_plan(built["course_id"], built["student_id"])
                ).total
            )
        assert len(set(counts)) == 1, counts

    def test_the_suite_exercises_a_substantial_number_of_injection_points(
        self, make_sandbox, built
    ) -> None:
        """本文件覆盖的注入点总量 —— "穷尽"这个词的度量。

        一个操作有 N 次落盘, 就有 N 个不同的**半成品状态**需要被证明不可能
        存在。这条测试把这个数字钉出来: 它不是"测试条数"的替代品, 而是
        "我们到底试了多少个中间点"的诚实交代。
        """
        course_id = built["course_id"]
        student_id = built["student_id"]

        def study_plan(box):
            box["workspace"].study_plan(course_id, student_id)

        def create_session(box):
            box["workspace"].create_session(course_id, session_number=401)

        def create_student(box):
            box["workspace"].create_student(course_id, "student-tally", "T")

        def submit_answer(box):
            box["workspace"].submit_answer(
                course_id,
                student_id,
                _first_exercise_id(box["workspace"], box),
                "a",
                sequence=402,
            )

        def review_confirm(box):
            target = box["workspace"].knowledge_points(course_id)[0]["knowledge_id"]
            box["workspace"].review_confirm(course_id, target)

        def process_material(box):
            material_id = _write_material(box, "tally.md")
            box["workspace"].process_material(course_id, material_id)

        operations = [
            ("study_plan", study_plan),
            ("create_session", create_session),
            ("create_student", create_student),
            ("submit_answer", submit_answer),
            ("review_confirm", review_confirm),
            ("process_material", process_material),
        ]
        total = 0
        for label, action in operations:
            box = make_sandbox(f"tally-{label}")
            counter = _observe(box, lambda w, a=action: a({"workspace": w, **box}))
            assert counter.total >= 1, label
            total += counter.total
        assert total >= 40, f"注入点总量太少, 穷尽验收没有实际分量: {total}"


# ======================================================================
# 53.4 失败绝不被吞掉
# ======================================================================


class TestFailureIsNeverSwallowed:
    def test_an_injected_runtime_error_propagates_unchanged(self, sandbox) -> None:
        workspace = sandbox["workspace"]
        workspace.persistence.set_fault_injector(_FailAt(1))
        with pytest.raises(Boom) as exc:
            workspace.create_student(sandbox["course_id"], "student-s1", "S")
        workspace.persistence.set_fault_injector(None)
        assert "injected failure" in str(exc.value)

    def test_a_persistence_error_becomes_a_structured_storage_error(
        self, sandbox
    ) -> None:
        workspace = sandbox["workspace"]
        persistence = workspace.persistence
        before = _dump(sandbox["data_dir"])

        with pytest.raises(StorageError) as exc:
            with persistence.atomic():
                persistence.save_course(workspace.context(sandbox["course_id"]).course)
                raise PersistenceError("disk went away")
        assert exc.value.code == "STORAGE_ERROR"
        assert exc.value.detail == {"operation_rolled_back": True}
        assert isinstance(exc.value.cause, PersistenceError)
        assert _dump(sandbox["data_dir"]) == before

    def test_a_domain_validation_error_keeps_its_own_code(self, sandbox) -> None:
        """领域错误已经带着正确的错误码, 不许被改写成 STORAGE_ERROR。"""
        persistence = sandbox["workspace"].persistence
        with pytest.raises(ApplicationError) as exc:
            with persistence.atomic():
                raise InvalidInputError("bad input")
        assert exc.value.code == "INVALID_INPUT"

    def test_a_failed_operation_does_not_swallow_the_error_into_a_return_value(
        self, sandbox
    ) -> None:
        workspace = sandbox["workspace"]
        workspace.persistence.set_fault_injector(_FailAt(1))
        raised = False
        try:
            workspace.create_student(sandbox["course_id"], "student-s2", "S")
        except Boom:
            raised = True
        finally:
            workspace.persistence.set_fault_injector(None)
        assert raised, "写失败被吞掉了 —— 调用方以为成功了"

    def test_a_keyboard_interrupt_still_rolls_back(self, sandbox) -> None:
        """``BaseException`` 也要回滚 —— Ctrl-C 不是"可以留下半成品"的理由。"""
        workspace = sandbox["workspace"]
        persistence = workspace.persistence
        before = _dump(sandbox["data_dir"])

        def _interrupt(point: str, ordinal: int) -> None:
            raise KeyboardInterrupt

        persistence.set_fault_injector(_interrupt)
        try:
            with pytest.raises(KeyboardInterrupt):
                workspace.create_student(sandbox["course_id"], "student-s3", "S")
        finally:
            persistence.set_fault_injector(None)
        assert _dump(sandbox["data_dir"]) == before
        assert persistence.database.in_transaction is False

    def test_a_system_exit_still_rolls_back(self, sandbox) -> None:
        workspace = sandbox["workspace"]
        persistence = workspace.persistence
        before = _dump(sandbox["data_dir"])

        def _exit(point: str, ordinal: int) -> None:
            raise SystemExit(3)

        persistence.set_fault_injector(_exit)
        try:
            with pytest.raises(SystemExit):
                workspace.create_student(sandbox["course_id"], "student-s4", "S")
        finally:
            persistence.set_fault_injector(None)
        assert _dump(sandbox["data_dir"]) == before

    def test_the_error_message_does_not_leak_a_traceback(self, sandbox) -> None:
        persistence = sandbox["workspace"].persistence
        with pytest.raises(StorageError) as exc:
            with persistence.atomic():
                raise PersistenceError("boom")
        assert "Traceback" not in exc.value.message
        assert 'File "' not in exc.value.message

    def test_rollback_diagnostics_records_the_error_type(self, sandbox) -> None:
        workspace = sandbox["workspace"]
        workspace.persistence.set_fault_injector(_FailAt(1))
        with pytest.raises(Boom):
            workspace.create_student(sandbox["course_id"], "student-s5", "S")
        workspace.persistence.set_fault_injector(None)
        recent = workspace.persistence.rollback_diagnostics()["recent"]
        assert recent[-1]["error_type"] == "Boom"
        assert recent[-1]["writes_before_failure"] >= 1

    def test_rollback_diagnostics_records_the_domain_code(self, sandbox) -> None:
        persistence = sandbox["workspace"].persistence
        with pytest.raises(InvalidInputError):
            with persistence.atomic():
                raise InvalidInputError("nope")
        recent = persistence.rollback_diagnostics()["recent"]
        assert recent[-1]["error_code"] == "INVALID_INPUT"

    def test_rollback_diagnostics_keeps_counting_past_the_recent_cap(
        self, sandbox
    ) -> None:
        workspace = sandbox["workspace"]
        persistence = workspace.persistence
        for index in range(25):
            persistence.set_fault_injector(_FailAt(1))
            try:
                with pytest.raises(Boom):
                    workspace.create_student(
                        sandbox["course_id"], f"student-cap-{index}", "Cap"
                    )
            finally:
                persistence.set_fault_injector(None)
        diagnostics = persistence.rollback_diagnostics()
        assert diagnostics["count"] == 25
        assert len(diagnostics["recent"]) == 20

    def test_health_reports_the_rollback_count(self, sandbox) -> None:
        workspace = sandbox["workspace"]
        assert workspace.health()["database"]["rollbacks"] == 0
        workspace.persistence.set_fault_injector(_FailAt(1))
        with pytest.raises(Boom):
            workspace.create_student(sandbox["course_id"], "student-s6", "S")
        workspace.persistence.set_fault_injector(None)
        assert workspace.health()["database"]["rollbacks"] == 1

    def test_a_rollback_does_not_make_the_database_unhealthy(self, sandbox) -> None:
        """回滚是**设计行为**, 不是损坏 —— health 的 database.ok 必须仍然是真。"""
        workspace = sandbox["workspace"]
        workspace.persistence.set_fault_injector(_FailAt(1))
        with pytest.raises(Boom):
            workspace.create_student(sandbox["course_id"], "student-s7", "S")
        workspace.persistence.set_fault_injector(None)
        health = workspace.health()
        assert health["database"]["ok"] is True
        assert health["status"] == "ok"


# ======================================================================
# 53.5 失败之后: 数据库仍然自洽, 重启仍然正确
# ======================================================================


class TestAfterAFailure:
    def test_the_database_stays_integrity_clean(self, sandbox) -> None:
        workspace = sandbox["workspace"]
        workspace.persistence.set_fault_injector(_FailAt(1))
        with pytest.raises(Boom):
            workspace.create_student(sandbox["course_id"], "student-a1", "A")
        workspace.persistence.set_fault_injector(None)
        assert workspace.persistence.integrity_check() == "ok"
        assert workspace.persistence.foreign_key_violations() == []

    def test_no_orphan_rows_are_left_behind(self, sandbox) -> None:
        """在"材料 + 处理状态"之间失败, 两张表都必须干净。"""
        workspace = sandbox["workspace"]
        before_records = _count(sandbox["data_dir"], "materials")
        before_processing = _count(sandbox["data_dir"], "material_processing")
        extra = pathlib.Path(sandbox["data_dir"]) / "orphan.md"
        extra.write_text("Definicio orphan: res.\n", encoding="utf-8")

        workspace.persistence.set_fault_injector(_FailAt(1))
        with pytest.raises(Boom):
            workspace.register_material(sandbox["course_id"], str(extra))
        workspace.persistence.set_fault_injector(None)

        assert _count(sandbox["data_dir"], "materials") == before_records
        assert _count(sandbox["data_dir"], "material_processing") == before_processing

    def test_a_restart_after_a_failure_sees_the_pre_failure_state(self, sandbox) -> None:
        workspace = sandbox["workspace"]
        course_id = sandbox["course_id"]
        before = _dump(sandbox["data_dir"])

        workspace.persistence.set_fault_injector(_FailAt(1))
        with pytest.raises(Boom):
            workspace.create_course("Fantasma", "FAN", "es")
        workspace.persistence.set_fault_injector(None)
        workspace.close()

        reopened = Workspace(sandbox["data_dir"])
        try:
            assert _dump(sandbox["data_dir"]) == before
            assert not [
                c for c in reopened.list_courses() if c["code"] == "FAN"
            ], "失败的创建在重启后出现了"
            assert reopened.get_course(course_id)["course_id"] == course_id
        finally:
            reopened.close()

    def test_a_retry_after_a_failure_matches_a_clean_run(
        self, make_sandbox
    ) -> None:
        """失败一次再重试, 结果必须与"从未失败"完全一致。"""

        def _run(box: dict[str, Any], *, fail_first: bool) -> dict[str, list[str]]:
            workspace = box["workspace"]
            if fail_first:
                workspace.persistence.set_fault_injector(_FailAt(1))
                with pytest.raises(Boom):
                    workspace.create_course("Retry", "RET", "es")
                workspace.persistence.set_fault_injector(None)
            workspace.create_course("Retry", "RET", "es")
            return _dump(box["data_dir"])

        retried = make_sandbox("retry-fail")
        clean = make_sandbox("retry-clean")
        assert _run(retried, fail_first=True) == _run(clean, fail_first=False)

    def test_a_retry_after_a_failure_in_processing_matches_a_clean_run(
        self, make_sandbox
    ) -> None:
        """处理流水线失败一次再重试, **重启后**的状态必须与从未失败完全一致。

        为什么比较的是重启后的状态而不是当场的内存状态: 回滚只覆盖数据库,
        内存里的作业计数器 (``attempts``) 不会被撤销 —— 那需要重写领域层的
        状态机, 而规范第一条就是"不重写核心 Domain"。重启是这两者交汇的地方:
        内存从数据库重建, 于是"数据库说了什么"变成唯一的事实。

        这也正是最终验收要问的问题: 关掉程序再打开, 数据对不对。
        """
        material = {"id": ""}

        def setup(box):
            material["id"] = _write_material(box, "retry.md")

        def _run(box: dict[str, Any], *, fail_first: bool) -> dict[str, list[str]]:
            setup(box)
            workspace = box["workspace"]
            if fail_first:
                workspace.persistence.set_fault_injector(_FailAt(1))
                with pytest.raises(Boom):
                    workspace.process_material(box["course_id"], material["id"])
                workspace.persistence.set_fault_injector(None)
            workspace.process_material(box["course_id"], material["id"])
            workspace.close()
            reopened = Workspace(box["data_dir"], clock=fixed_clock(ACCEPTANCE_CLOCK))
            try:
                return _dump(box["data_dir"])
            finally:
                reopened.close()

        retried = make_sandbox("proc-retry-fail")
        clean = make_sandbox("proc-retry-clean")
        _run(retried, fail_first=True)
        _run(clean, fail_first=False)

        retried_dump = _dump(retried["data_dir"])
        clean_dump = _dump(clean["data_dir"])
        differing = sorted(
            table
            for table in set(retried_dump) | set(clean_dump)
            if retried_dump.get(table) != clean_dump.get(table)
        )
        # 原始比较里只允许这两张表不同 —— 而且它们的不同只来自**副本自己的
        # 目录名** (``stored_path``), 由下面的归一化比较证明。
        assert set(differing) <= {"materials", "material_processing"}, (
            f"回滚没有完全生效, 或者多出了别的差异: {differing}"
        )
        assert _dump_normalised(retried["data_dir"]) == _dump_normalised(
            clean["data_dir"]
        ), "除了 attempts 与副本路径之外还有别的差异 —— 回滚漏了东西"

        # 连**尝试次数**都一样: 写 ``material_processing`` 的那一步也在被回滚
        # 的范围内, 所以"失败过一次"这件事没有以任何形式留在数据库里。
        assert _attempts(retried["data_dir"]) == _attempts(clean["data_dir"]), (
            "数据库里的 attempts 应该与从未失败的那条路径完全一致"
        )

    def test_the_material_file_survives_even_if_the_record_write_fails(
        self, sandbox
    ) -> None:
        """诚实记录一个**不可回滚**的副作用。

        文件系统不是事务性的: ``register_material`` 先把字节复制进受管目录,
        再写数据库记录。回滚能保证"库里没有半条记录", 但已经复制过去的文件
        会留下来 —— 它是一个**孤儿文件**, 不是孤儿记录。

        这个方向是安全的: 危险的是"库里有记录但文件不在"(溯源断链, 由
        ``material_integrity`` 报 MATERIAL_FILE_MISSING), 而"文件在但库里没
        记录"只是一个多余的字节, 不会被任何东西引用。**绝不**反过来做。
        """
        workspace = sandbox["workspace"]
        before = _dump(sandbox["data_dir"])
        extra = pathlib.Path(sandbox["data_dir"]) / "side-effect.md"
        extra.write_text("Definicio side effect: res.\n", encoding="utf-8")

        workspace.persistence.set_fault_injector(_FailAt(1))
        with pytest.raises(Boom):
            workspace.register_material(sandbox["course_id"], str(extra))
        workspace.persistence.set_fault_injector(None)

        # 数据库干净
        assert _dump(sandbox["data_dir"]) == before
        # 而且"记录缺失"不会让完整性检查报错 —— 它只检查库 -> 文件这个方向
        assert workspace.material_integrity()["ok"] is True
        # 重启后材料列表里没有它
        workspace.close()
        reopened = Workspace(sandbox["data_dir"])
        try:
            assert _dump(sandbox["data_dir"]) == before
        finally:
            reopened.close()

    def test_two_failures_in_a_row_leave_the_same_clean_state(self, sandbox) -> None:
        workspace = sandbox["workspace"]
        before = _dump(sandbox["data_dir"])
        for index in range(2):
            workspace.persistence.set_fault_injector(_FailAt(1))
            try:
                with pytest.raises(Boom):
                    workspace.create_student(
                        sandbox["course_id"], f"student-twice-{index}", "Twice"
                    )
            finally:
                workspace.persistence.set_fault_injector(None)
            assert _dump(sandbox["data_dir"]) == before

    def test_a_failure_does_not_lose_already_committed_data(self, sandbox) -> None:
        """回滚只能撤销**本次操作**, 不能碰到之前已经提交的东西。"""
        workspace = sandbox["workspace"]
        course_id = sandbox["course_id"]
        created = workspace.create_student(course_id, "student-keep", "Keep")
        assert created["student_id"] == "student-keep"

        before = _dump(sandbox["data_dir"])
        workspace.persistence.set_fault_injector(_FailAt(1))
        with pytest.raises(Boom):
            workspace.create_student(course_id, "student-lost", "Lost")
        workspace.persistence.set_fault_injector(None)

        assert _dump(sandbox["data_dir"]) == before
        assert workspace.get_student(course_id, "student-keep")

    def test_a_failed_processing_run_does_not_break_health(self, make_sandbox) -> None:
        box = make_sandbox("health")
        material_id = _write_material(box, "health.md")
        workspace = box["workspace"]
        workspace.persistence.set_fault_injector(_FailAt(1))
        with pytest.raises(Boom):
            workspace.process_material(box["course_id"], material_id)
        workspace.persistence.set_fault_injector(None)
        health = workspace.health()
        assert health["database"]["ok"] is True
        assert health["storage"]["ok"] is True
        assert health["database"]["rollbacks"] == 1


# ======================================================================
# 53.6 显式事务入口 (供组合根 / 需要跨多个应用操作的调用方使用)
# ======================================================================


class TestExplicitTransactionEntry:
    def test_the_wiring_exposes_a_transaction_context(self, sandbox) -> None:
        persistence = sandbox["workspace"].persistence
        with persistence.transaction() as connection:
            assert connection is not None
            assert persistence.database.in_transaction is True
        assert persistence.database.in_transaction is False

    def test_an_exception_inside_the_explicit_transaction_rolls_back(self, sandbox) -> None:
        workspace = sandbox["workspace"]
        persistence = workspace.persistence
        before = _dump(sandbox["data_dir"])
        with pytest.raises(Boom):
            with persistence.transaction():
                persistence.save_course(workspace.context(sandbox["course_id"]).course)
                raise Boom("explicit")
        assert _dump(sandbox["data_dir"]) == before
        assert persistence.database.in_transaction is False

    def test_the_explicit_transaction_commits_on_success(self, sandbox) -> None:
        workspace = sandbox["workspace"]
        persistence = workspace.persistence
        course = workspace.context(sandbox["course_id"]).course
        with persistence.transaction():
            persistence.save_course(course)
        assert _dump(sandbox["data_dir"])["courses"], "显式事务没有提交"

    def test_a_write_outside_an_operation_is_autocommitted(self, sandbox) -> None:
        """明确记录边界在哪里: ``save_*`` 是**可组合的落盘步骤**, 不是操作边界。

        单独调用一个 ``save_*`` 时, 语句各自自动提交; 此时注入的失败落在了一个
        已经提交的语句之后, 当然回滚不了。这不是缺陷, 而是分工: 原子性由
        ``atomic()`` (以及 ``Workspace`` 的每个写方法) 负责。

        如果将来有人把 ``atomic()`` 从某个 ``Workspace`` 写方法里删掉, 上面的
        穷尽回滚测试会立刻变红 —— 这一条只是把"边界在哪"写清楚。
        """
        workspace = sandbox["workspace"]
        persistence = workspace.persistence
        from src.models import Course

        fresh = Course(
            course_id="course-autocommit",
            name="Auto",
            code="AUT",
            language="es",
        )
        assert "course-autocommit" not in _dump(sandbox["data_dir"])["courses"]

        persistence.set_fault_injector(_FailAt(1))
        try:
            with pytest.raises(Boom):
                persistence.save_course(fresh)
        finally:
            persistence.set_fault_injector(None)

        # 没有原子边界 -> 那一条写入已经提交了 (所以在库里能查到)
        rows = _dump(sandbox["data_dir"])["courses"]
        assert any("course-autocommit" in row for row in rows), (
            "单独调用 save_course 本应是自动提交 —— 如果这里变了, 说明 "
            "save_* 自己开了事务, 那是另一套语义, 需要重新审视"
        )
        assert persistence.rollback_diagnostics()["count"] == 0, (
            "没有进入 atomic() 就不该记回滚 —— 回滚诊断只统计真正的操作边界"
        )

    def test_for_database_reuses_the_connection(self, sandbox) -> None:
        """组合根场景: 复用同一个连接, 不产生第二个数据库句柄。"""
        database = sandbox["workspace"].persistence.database
        store = WorkspacePersistence.for_database(database)
        assert store.database is database
        assert store.fault_injector is None
        store.close()  # 不拥有连接 -> 不应真的关掉
        assert database.closed is False

    def test_a_workspace_can_be_given_an_explicit_persistence(self, tmp_path) -> None:
        """显式传入的 ``WorkspacePersistence`` 被工作区接管。

        ``Workspace.close()`` 委托给 ``WorkspacePersistence.close()``, 而后者
        只在 ``owns_database`` 为真时真的关连接 —— 由 ``open()`` 创建的 store
        拥有连接, 由 ``for_database()`` 创建的不拥有。这条测试钉住这个区分:
        组合根复用同一个连接时必须用 ``for_database()``。
        """
        root = tmp_path / "explicit"
        root.mkdir(parents=True)
        path = default_database_path(str(root))
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)

        owned = WorkspacePersistence.open(path)
        try:
            workspace = Workspace(str(root), persistence=owned)
            try:
                dto = workspace.create_course("Explicita", "EXP", "es")
                assert dto["course_id"]
                assert _count(str(root), "courses") == 1
            finally:
                workspace.close()
            assert owned.database.closed is True, "open() 创建的 store 拥有连接"
        finally:
            owned.close()

        # 复用连接时必须用 for_database(): 工作区关闭后连接仍在
        database = open_database(path)
        shared = WorkspacePersistence.for_database(database)
        try:
            workspace = Workspace(str(root), persistence=shared)
            try:
                assert workspace.list_courses()
            finally:
                workspace.close()
            assert shared.database.closed is False, "for_database() 不拥有连接"
            assert workspace.closed is True
        finally:
            shared.close()
            database.close()
