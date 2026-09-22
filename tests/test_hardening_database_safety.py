# -*- coding: utf-8 -*-
"""Task 47.6 —— 数据库安全审计。

规范原文 (47.6) 要求测试:

    unexpected shutdown / partial write / rollback / backup / restore /
    migration / corrupt DB / duplicate records

其中 rollback / partial write / backup / restore / migration / corrupt DB /
duplicate records 已由 Task 42/43 的 359 条测试覆盖
(``test_persistence_*.py``、``test_backup_*.py``)。本文件**只补真正
没被覆盖的部分**, 不重复计分:

1. **unexpected shutdown** —— 进程被硬杀 (``TerminateProcess`` / ``SIGKILL``),
   而不是"抛异常"。
2. **crash during migration** —— 迁移执行到一半进程消失。
3. **duplicate records 的批量原子性** —— 一批里第 N 条重复时前面几条的命运。
4. **backup 的边界** —— 事务开着时快照、目标已存在、已关闭的库。
5. **migrate() 的"零写盘"承诺** —— 库已最新时不得产生任何写入。

为什么"异常关闭"必须用真进程
--------------------------------------------------------------------
单元测试里最常见的假做法是 ``with pytest.raises(...)`` 或者手动
``db.rollback()``。那测的是**受控回滚**, 不是崩溃:

- 受控路径下 Python 会正常关连接、SQLite 会正常收尾;
- 崩溃路径下没有任何收尾动作, 靠的是 WAL 的恢复协议。

两者的失败模式完全不同, 所以这里用 ``subprocess`` 起真子进程, 在它持有
一个**未提交**事务时硬杀掉它 —— 不给它任何清理机会。

子进程用**标记文件**而不是 stdout 握手: Windows 管道 + 阻塞式
``readline`` 会带来不必要的挂起风险, 而"文件出现"是原子的、非阻塞的。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.persistence.database import Database
from src.persistence.errors import (
    DuplicateRecordError,
    PersistenceError,
    PersistenceValidationError,
    TransactionError,
)
from src.persistence.migrations import latest_version

from tests.test_persistence_repositories import _course, _material_record

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

_COURSE_INSERT = (
    "INSERT INTO courses (course_id, name, code, language, payload) "
    "VALUES (?, ?, ?, ?, ?)"
)


# ======================================================================
# 子进程夹具
# ======================================================================


class _Child:
    """一个跑着"注定要被硬杀"的脚本的子进程。"""

    def __init__(self, tmp_path: Path, code: str, *args: str) -> None:
        self.marker = tmp_path / "child.ready"
        self._out_path = tmp_path / "child.out"
        self._err_path = tmp_path / "child.err"
        self._out = open(self._out_path, "wb")
        self._err = open(self._err_path, "wb")
        self.proc = subprocess.Popen(
            [PYTHON, "-c", code, str(ROOT), *args, str(self.marker)],
            cwd=str(ROOT),
            stdout=self._out,
            stderr=self._err,
        )

    # -- 握手 ----------------------------------------------------------

    def wait_ready(self, timeout: float = 120.0) -> None:
        """等到子进程写完标记文件 (证明它已经进到要测的那一步)。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.marker.exists():
                return
            if self.proc.poll() is not None:
                logs = self.logs()
                self.close()
                pytest.fail(
                    f"child exited (rc={self.proc.returncode}) before signalling "
                    f"ready:\n{logs}"
                )
            time.sleep(0.05)
        logs = self.logs()
        self.kill()
        pytest.fail(f"child never signalled ready:\n{logs}")

    def wait_exit(self, timeout: float = 60.0) -> int:
        """等子进程自己退出 (迁移崩溃场景: 它自杀, 不用外部杀)。"""
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logs = self.logs()
            self.kill()
            pytest.fail(f"child did not exit on its own:\n{logs}")

    # -- 杀 / 收尾 -----------------------------------------------------

    def kill(self) -> None:
        """硬杀: ``TerminateProcess`` / ``SIGKILL``, 不给任何清理机会。"""
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - 平台异常
            pytest.fail("child process refused to die")
        self.close()

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
            except OSError:  # pragma: no cover - 日志读不到不该让测试失败
                text = "(unavailable)"
            parts.append(f"--- {label} ---\n{text}")
        return "\n".join(parts)

    def close(self) -> None:
        for handle in (self._out, self._err):
            if not handle.closed:
                handle.close()


@pytest.fixture
def spawn_child(tmp_path):
    """起子进程并在测试结束时保证它一定死掉 (不留孤儿)。"""
    children: list[_Child] = []

    def _spawn(code: str, *args: str) -> _Child:
        child = _Child(tmp_path, code, *args)
        children.append(child)
        return child

    yield _spawn
    for child in children:
        child.kill()


# ======================================================================
# 子进程脚本
# ======================================================================

#: 已提交一行 + 一个未提交的大事务, 然后等死。
#:
#: 未提交的 payload 故意写到 3MB: 默认页缓存约 2MB, 写不下就会**真的**把
#: 未提交的页刷进 ``-wal`` 文件。否则"未提交数据消失"只是因为数据从没离开
#: 内存 —— 那测不到 WAL 的恢复协议。
_CRASH_WRITER = r'''
import sys, time
sys.path.insert(0, sys.argv[1])
from src.persistence.database import Database

db_path, marker = sys.argv[2], sys.argv[3]
db = Database(db_path)
db.migrate()

with db.transaction():
    db.execute(
        "INSERT INTO courses (course_id, name, code, language, payload) "
        "VALUES (?, ?, ?, ?, ?)",
        ("crash-committed", "Committed", "C1", "es", '{"committed": true}'),
    )

db.begin()
db.execute(
    "INSERT INTO courses (course_id, name, code, language, payload) "
    "VALUES (?, ?, ?, ?, ?)",
    ("crash-uncommitted", "Uncommitted", "C2", "es", "x" * 3_000_000),
)

open(marker, "w", encoding="utf-8").write("ready")
time.sleep(300)
'''

#: 跨表原子对 (materials + material_processing) 写一半就崩。
#:
#: 第一行的 payload 同样是 3MB, 用来把"材料已写、状态没写"这个半成品
#: **真的**推到磁盘上, 而不是只停在内存里。
_CRASH_PAIR = r'''
import sys, time
sys.path.insert(0, sys.argv[1])
from src.persistence.database import Database

db_path, marker = sys.argv[2], sys.argv[3]
db = Database(db_path)
db.migrate()

db.begin()
db.execute(
    "INSERT INTO materials (material_id, filename, extension, size, "
    "material_type, language, source_type, payload) VALUES (?,?,?,?,?,?,?,?)",
    ("m-half", "clase1.mp3", ".mp3", 1024, "audio", "es", "audio",
     "y" * 3_000_000),
)
db.execute(
    "INSERT INTO material_processing (material_id, processing_status, "
    "attempts, payload) VALUES (?,?,?,?)",
    ("m-half", "PROCESSING", 1, '{"b":2}'),
)

open(marker, "w", encoding="utf-8").write("ready")
time.sleep(300)
'''

#: 迁移执行到一半硬退出 (``os._exit`` 跳过一切收尾)。
#:
#: hook 里先写标记文件再自杀 —— 标记文件的存在证明 ``CREATE TABLE`` 那几条
#: 语句**已经在这个事务里执行过了**, 这样"回滚掉"才是个有内容的断言。
_CRASH_MIGRATION = r'''
import os, sys
sys.path.insert(0, sys.argv[1])
from src.persistence.database import Database
from src.persistence.migrations import Migration
from src.persistence.migrations.m001_initial_schema import MIGRATION_001

db_path, marker = sys.argv[2], sys.argv[3]


def _die(database):
    open(marker, "w", encoding="utf-8").write("ready")
    os._exit(9)


chain = (
    MIGRATION_001,
    Migration(
        version=2,
        name="boom",
        statements=(
            "CREATE TABLE boom_table (x TEXT)",
            "INSERT INTO boom_table (x) VALUES ('half-migrated')",
        ),
        hook=_die,
    ),
)

db = Database(db_path)
db.migrate(chain)
print("should never be reached", flush=True)
'''


# ======================================================================
# 47.6 (1) unexpected shutdown —— 真崩溃
# ======================================================================


class TestUnexpectedShutdown:
    """硬杀进程, 而不是"抛个异常"。"""

    def test_committed_rows_survive_and_uncommitted_rows_vanish(
        self, tmp_path, spawn_child
    ) -> None:
        """核心断言: 崩溃只能带走**未提交**的东西。"""
        db_path = tmp_path / "classroom.sqlite"
        child = spawn_child(_CRASH_WRITER, str(db_path))
        child.wait_ready()
        child.kill()

        db = Database(str(db_path))
        try:
            ids = {row["course_id"] for row in db.query("SELECT course_id FROM courses")}
        finally:
            db.close()

        assert "crash-committed" in ids
        assert "crash-uncommitted" not in ids

    def test_database_is_healthy_after_a_hard_kill(self, tmp_path, spawn_child) -> None:
        """崩溃后库必须是"健康"的, 而不是"能打开但已经坏了"。"""
        db_path = tmp_path / "classroom.sqlite"
        child = spawn_child(_CRASH_WRITER, str(db_path))
        child.wait_ready()
        child.kill()

        db = Database(str(db_path))
        try:
            assert db.integrity_check() == "ok"
            assert db.foreign_key_violations() == []
            assert db.is_healthy() is True
            assert db.schema_version() == latest_version()
        finally:
            db.close()

    def test_database_is_writable_again_after_a_hard_kill(
        self, tmp_path, spawn_child
    ) -> None:
        """恢复之后必须还能继续写 —— 恢复不是只读的旁路。"""
        db_path = tmp_path / "classroom.sqlite"
        child = spawn_child(_CRASH_WRITER, str(db_path))
        child.wait_ready()
        child.kill()

        db = Database(str(db_path))
        try:
            with db.transaction():
                db.execute(
                    _COURSE_INSERT, ("after-crash", "After", "C3", "es", "{}")
                )
            assert db.query_one(
                "SELECT name FROM courses WHERE course_id = ?", ("after-crash",)
            )["name"] == "After"
        finally:
            db.close()

    def test_recovery_is_durable_across_repeated_reopens(
        self, tmp_path, spawn_child
    ) -> None:
        """恢复必须**写回**库, 而不是"这次读的时候顺手忽略"。

        连续重开三次, 每次都必须看到同一个结果: 已提交的在, 未提交的不在。
        如果恢复只是读取时的障眼法, 第二次重开就会把丢弃的行"变回来"。
        """
        db_path = tmp_path / "classroom.sqlite"
        child = spawn_child(_CRASH_WRITER, str(db_path))
        child.wait_ready()
        child.kill()

        for attempt in range(3):
            db = Database(str(db_path))
            try:
                ids = {
                    row["course_id"]
                    for row in db.query("SELECT course_id FROM courses")
                }
                assert "crash-committed" in ids, f"reopen #{attempt}"
                assert "crash-uncommitted" not in ids, f"reopen #{attempt}"
                assert db.integrity_check() == "ok", f"reopen #{attempt}"
            finally:
                db.close()

    def test_crash_inside_a_cross_table_pair_leaves_no_half_write(
        self, tmp_path, spawn_child
    ) -> None:
        """规范点名的 atomic 对: 材料注册 + 处理状态, 崩在中间也不能半写。

        注意这与 ``test_persistence_transaction`` 里的受控回滚是**两件事**:
        那里是"第二步抛异常", 这里是"第二步还没执行, 进程就没了"。
        """
        db_path = tmp_path / "classroom.sqlite"
        child = spawn_child(_CRASH_PAIR, str(db_path))
        child.wait_ready()
        child.kill()

        db = Database(str(db_path))
        try:
            assert db.scalar("SELECT COUNT(*) FROM materials", (), default=-1) == 0
            assert (
                db.scalar("SELECT COUNT(*) FROM material_processing", (), default=-1)
                == 0
            )
            assert db.integrity_check() == "ok"
        finally:
            db.close()

    def test_a_crash_during_migration_rolls_that_migration_back(
        self, tmp_path, spawn_child
    ) -> None:
        """迁移执行到一半进程消失 -> 该条迁移整体回滚, 且迁移器能续跑。"""
        db_path = tmp_path / "classroom.sqlite"
        child = spawn_child(_CRASH_MIGRATION, str(db_path))
        child.wait_ready()  # hook 已执行 -> CREATE TABLE 已经在事务里跑过了
        assert child.wait_exit() == 9

        db = Database(str(db_path))
        try:
            # 崩掉的那条迁移不许留下任何痕迹
            assert db.has_table("boom_table") is False
            assert db.schema_version() == 1
            ledger = db.applied_migrations()
            assert [row["version"] for row in ledger] == [1]
            assert db.integrity_check() == "ok"

            # 而且迁移器必须能**接着**跑完真实迁移链
            applied = db.migrate()
            assert applied == latest_version() - 1
            assert db.schema_version() == latest_version()
            assert db.has_table("boom_table") is False
            assert db.is_healthy() is True
        finally:
            db.close()


# ======================================================================
# 47.6 (2) partial write —— 批量写入的原子性
# ======================================================================


def _duplicate_pair():
    """同一个课程下、同名同内容的两个材料 -> 违反 UNIQUE(course_id, filename, content_hash)。

    这是真实世界里最常见的"重复记录": 同一个音频文件被注册了两次, 只是
    生成了两个不同的 ``material_id``。
    """
    first = _material_record("m0")
    second = _material_record("m1")
    second["content_hash"] = first["content_hash"]
    return first, second


class TestPartialWrite:
    def test_a_batch_that_hits_a_duplicate_writes_nothing(self, repos) -> None:
        """一批 3 条, 第 2 条重复 -> 在事务里则一条都不落库。"""
        first, duplicate = _duplicate_pair()
        third = _material_record("m2")

        with pytest.raises(DuplicateRecordError):
            with repos.transaction():
                repos.materials.save_records((first, duplicate, third))

        assert repos.materials.count() == 0

    def test_without_a_transaction_the_same_batch_does_leave_a_partial_write(
        self, repos
    ) -> None:
        """反面证据: 不包事务时第 1 条会留下。

        这条测试是**故意**断言"坏行为"的: 它证明上面那条测试里的
        ``repos.transaction()`` 不是装饰, 而是真的在挡住半写。
        """
        first, duplicate = _duplicate_pair()
        third = _material_record("m2")

        with pytest.raises(DuplicateRecordError):
            repos.materials.save_records((first, duplicate, third))

        assert repos.materials.count() == 1
        assert repos.materials.exists("m0") is True
        assert repos.materials.exists("m1") is False
        assert repos.materials.exists("m2") is False

    def test_a_duplicate_never_leaves_the_existing_row_modified(self, repos) -> None:
        """重复被拒绝时, 已存在的那一行必须**一个字节都没变**。"""
        first, duplicate = _duplicate_pair()
        repos.materials.save_record(first)
        before = dict(repos.materials.get_row("m0"))

        duplicate["size"] = 999_999  # 想让它在冲突前先被写进去
        with pytest.raises(DuplicateRecordError):
            repos.materials.save_record(duplicate)

        assert repos.materials.count() == 1
        assert dict(repos.materials.get_row("m0")) == before


# ======================================================================
# 47.6 (3) duplicate records —— 重复不会累积
# ======================================================================


class TestDuplicateRecords:
    def test_repeat_save_of_the_same_key_updates_and_never_duplicates(
        self, repos
    ) -> None:
        """同一主键重复保存 = 更新, 不是新增行。"""
        repos.courses.save(_course(name="Old"))
        repos.courses.save(_course(name="New"))
        assert repos.courses.count() == 1
        assert repos.courses.load("course-1").name == "New"

    def test_unique_index_duplicate_is_rejected_with_a_conflict_code(
        self, repos
    ) -> None:
        first, duplicate = _duplicate_pair()
        repos.materials.save_record(first)

        with pytest.raises(DuplicateRecordError) as caught:
            repos.materials.save_record(duplicate)

        assert caught.value.code == "DUPLICATE_RECORD"
        assert "materials" in str(caught.value)

    def test_repeated_failed_duplicates_never_grow_the_table(self, repos) -> None:
        """连撞 5 次: 表里永远只有那 1 行。"""
        first, duplicate = _duplicate_pair()
        repos.materials.save_record(first)

        for _ in range(5):
            with pytest.raises(DuplicateRecordError):
                repos.materials.save_record(duplicate)

        assert repos.materials.count() == 1


# ======================================================================
# 47.6 (4) backup —— 边界与前置条件
# ======================================================================


class TestBackupSafety:
    def test_backup_refuses_while_a_transaction_is_open(self, db_path) -> None:
        """**回归测试**: 事务开着时快照必须报错, 绝不能永久挂起。

        修复前的实测行为: ``db.begin()`` (一个字都没写) 之后调用
        ``backup_to()``, 进程**永不返回** —— 在线备份 API 在等一个被自己
        的写事务占住的读锁。这是本机服务里最坏的失败形态: 请求不返回,
        而且写锁一直被占着。
        """
        db = Database(str(db_path))
        try:
            db.migrate()
            with db.transaction():
                db.execute(_COURSE_INSERT, ("c1", "C", "C1", "es", "{}"))

            db.begin()  # 空事务也会挂
            with pytest.raises(TransactionError) as caught:
                db.backup_to(str(Path(db_path).parent / "snap.sqlite"))
            assert caught.value.code == "STORAGE_TRANSACTION_FAILED"

            # 事务关掉之后必须马上恢复正常
            db.rollback()
            assert db.in_transaction is False
            db.backup_to(str(Path(db_path).parent / "snap.sqlite"))
        finally:
            db.close()

        snapshot = Database(str(Path(db_path).parent / "snap.sqlite"), create=False)
        try:
            ids = [row["course_id"] for row in snapshot.query("SELECT course_id FROM courses")]
            assert ids == ["c1"]
            assert snapshot.integrity_check() == "ok"
        finally:
            snapshot.close()

    def test_backup_refuses_to_overwrite_an_existing_target(self, db_path) -> None:
        """绝不静默覆盖上一份备份。"""
        db = Database(str(db_path))
        try:
            db.migrate()
            target = str(Path(db_path).parent / "snap.sqlite")
            db.backup_to(target)
            with pytest.raises(PersistenceValidationError):
                db.backup_to(target)
        finally:
            db.close()

    def test_backup_of_a_closed_database_is_refused(self, db_path) -> None:
        db = Database(str(db_path))
        db.migrate()
        db.close()
        with pytest.raises(PersistenceError):
            db.backup_to(str(Path(db_path).parent / "snap.sqlite"))

    def test_backup_of_an_in_memory_database_is_refused(self) -> None:
        db = Database(":memory:")
        try:
            with pytest.raises(PersistenceValidationError):
                db.backup_to("whatever.sqlite")
        finally:
            db.close()

    def test_a_restored_copy_is_complete_and_healthy(self, db_path) -> None:
        """restore 语义: 快照必须自包含 (没有 -wal 也完整)。"""
        db = Database(str(db_path))
        try:
            db.migrate()
            for index in range(25):
                with db.transaction():
                    db.execute(
                        _COURSE_INSERT,
                        (f"c{index}", f"Course {index}", f"C{index}", "es", "{}"),
                    )
            target = str(Path(db_path).parent / "snap.sqlite")
            db.backup_to(target)
        finally:
            db.close()

        # 只留快照自己, 把任何 -wal / -shm 都删掉 —— 快照必须仍然完整
        for suffix in ("-wal", "-shm"):
            residue = Path(target + suffix)
            if residue.exists():
                residue.unlink()

        snapshot = Database(target, create=False)
        try:
            assert snapshot.integrity_check() == "ok"
            assert snapshot.foreign_key_violations() == []
            assert snapshot.scalar("SELECT COUNT(*) FROM courses", (), default=-1) == 25
            assert snapshot.schema_version() == latest_version()
        finally:
            snapshot.close()


# ======================================================================
# 47.6 (5) migration —— "库已最新时不写盘"的承诺
# ======================================================================


class TestMigrationLeavesUpToDateDatabasesAlone:
    def test_migrate_on_an_up_to_date_database_does_not_touch_the_file(
        self, db_path
    ) -> None:
        """``migrate()`` 的文档承诺: 库已最新时**完全不写盘**。

        这不是省事, 而是必需: 打开库就要抢一次写锁的话, 任何正在跑的长事务
        (导入、批量落库) 都会把"打开库"堵成"等 5 秒然后失败"。所以这里
        用文件 mtime 与 WAL 大小来验证"确实没写", 而不是只看返回值。
        """
        db = Database(str(db_path))
        try:
            assert db.migrate() == latest_version()
            db.close()

            before_mtime = os.path.getmtime(str(db_path))
            wal = Path(str(db_path) + "-wal")
            before_wal = wal.stat().st_size if wal.exists() else 0

            time.sleep(0.05)  # 让文件系统时间戳有分辨率

            reopened = Database(str(db_path))
            try:
                assert reopened.migrate() == 0
            finally:
                reopened.close()

            assert os.path.getmtime(str(db_path)) == before_mtime
            after_wal = wal.stat().st_size if wal.exists() else 0
            assert after_wal == before_wal
        finally:
            if not db.closed:
                db.close()

    def test_migrate_is_idempotent_and_keeps_the_ledger_stable(self, db) -> None:
        before = db.applied_migrations()
        assert db.migrate() == 0
        assert db.migrate() == 0
        assert db.applied_migrations() == before
        assert db.schema_version() == latest_version()
