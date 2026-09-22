# -*- coding: utf-8 -*-
"""SQLite 连接 / 事务 / 迁移执行器 (Task 42)。

职责边界
--------------------------------------------------------------------

本模块**只**负责"如何可靠地打开一个 SQLite 库并把 schema 迁移到最新版":

- 线程局部连接 (每个线程一条, 读并发天然成立);
- 显式事务 (``begin`` / ``commit`` / ``rollback``, 支持嵌套 SAVEPOINT);
- ``schema_version`` 台账 + 编号迁移 (``001`` / ``002`` / ...);
- 损坏库的显式识别 (绝不把 ``sqlite3.DatabaseError`` 原样抛给上层)。

本模块**不**知道任何业务概念: 没有 Course / Evidence / KnowledgePoint。
行 <-> 领域对象的转换属于 ``src/persistence/repositories``。

非确定性来源
--------------------------------------------------------------------

迁移台账的 ``applied_at`` 是运行期元数据, 因此复用
``src.common.clock`` 的注入式 ``Clock``, 而不是就地调用
``datetime.now()`` —— 全项目的非确定性来源必须只有一处。
``runtime`` 模块不依赖任何项目代码, 因此这里是**无环**依赖。

事务语义 (重要)
--------------------------------------------------------------------

- 每个线程有自己的连接, 因此事务是**线程局部**的。
- 写事务默认 ``BEGIN IMMEDIATE``: 立即取写锁, 避免"先读后写"时
  在提交阶段才发现冲突 (SQLite 会把升级失败的写事务直接判死)。
- 嵌套: 最外层 ``BEGIN``, 内层用 ``SAVEPOINT``。内层回滚只回滚到自己的
  保存点, 不影响外层 —— 这正是仓储方法既能独立调用、又能被组合进一个
  更大事务的前提。
- 写事务全程持有进程级 ``RLock``: 单机单用户场景下让并发写变成**确定性
  串行**, 而不是随机撞 ``SQLITE_BUSY``。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Optional, Sequence

from src.common.clock import Clock, utc_now_iso
from src.persistence.errors import (
    CorruptedDatabaseError,
    MigrationError,
    PersistenceValidationError,
    TransactionError,
    UnsupportedSchemaVersionError,
)

__all__ = ["Database", "SCHEMA_VERSION_TABLE", "MEMORY_PATH"]

#: 迁移台账表名 (规范明确要求存在 ``schema_version``)。
SCHEMA_VERSION_TABLE = "schema_version"

#: 内存库路径 (单连接模式)。
MEMORY_PATH = ":memory:"

#: 本代码能处理的最高 schema 版本。库里的版本更高 -> 拒绝打开,
#: 绝不"猜着读"一份来自更新版本的 schema。
MAX_SUPPORTED_SCHEMA_VERSION = 999


def _is_memory_path(path: str) -> bool:
    if path == MEMORY_PATH:
        return True
    return path.startswith("file:") and "mode=memory" in path


class Database:
    """一个 SQLite 数据库文件的连接 / 事务 / 迁移门面。

    用法::

        db = Database("data/database/classroom.sqlite")
        db.migrate()                       # 建表 + 迁移到最新版
        with db.transaction():
            db.execute("INSERT INTO courses ...", (...))

    ``clock`` 可注入, 用于测试获得确定性的迁移时间戳。
    """

    def __init__(
        self,
        path: str,
        *,
        clock: Optional[Clock] = None,
        busy_timeout_ms: int = 5000,
        create: bool = True,
    ) -> None:
        if not isinstance(path, str) or not path.strip():
            raise PersistenceValidationError("database path must be a non-empty string")
        self._path = path.strip()
        self._is_memory = _is_memory_path(self._path)
        self._clock: Clock = clock or utc_now_iso
        self._busy_timeout_ms = int(busy_timeout_ms)
        if self._busy_timeout_ms < 0:
            raise PersistenceValidationError("busy_timeout_ms must be non-negative")

        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._all_connections: list[sqlite3.Connection] = []
        self._registry_lock = threading.Lock()
        self._closed = False
        self._depth = 0

        if not self._is_memory and not create and not os.path.exists(self._path):
            raise PersistenceValidationError(
                f"database file does not exist: {self._path!r} (create=False)"
            )
        if not self._is_memory:
            parent = os.path.dirname(os.path.abspath(self._path))
            if create:
                os.makedirs(parent, exist_ok=True)
            elif not os.path.isdir(parent):
                raise PersistenceValidationError(
                    f"database directory does not exist: {parent!r}"
                )

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def path(self) -> str:
        return self._path

    @property
    def is_memory(self) -> bool:
        return self._is_memory

    @property
    def in_transaction(self) -> bool:
        return self._depth > 0

    @property
    def closed(self) -> bool:
        return self._closed

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def _new_connection(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(
                self._path,
                timeout=self._busy_timeout_ms / 1000.0,
                isolation_level=None,  # 事务由本模块显式控制
                check_same_thread=False,
            )
        except sqlite3.OperationalError as exc:
            raise CorruptedDatabaseError(
                f"cannot open database {self._path!r}: {exc}",
                cause=exc,
            ) from exc
        conn.row_factory = sqlite3.Row
        self._configure(conn)
        return conn

    def _configure(self, conn: sqlite3.Connection) -> None:
        """设置 PRAGMA。任何失败都翻译成结构化错误。"""
        try:
            conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            conn.execute("PRAGMA foreign_keys = ON")
            if not self._is_memory:
                # WAL: 读写不互斥 -> 并发读成立; 内存库不支持 WAL。
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.DatabaseError as exc:
            raise CorruptedDatabaseError(
                f"database is not readable as SQLite: {self._path!r} ({exc})",
                cause=exc,
            ) from exc

    def connect(self) -> sqlite3.Connection:
        """返回**当前线程**的连接 (惰性创建)。

        首次打开一个已存在的非空文件时会做一次可读性校验; 若文件不是
        合法 SQLite 库, 抛 :class:`CorruptedDatabaseError` 而不是让
        ``sqlite3.DatabaseError`` 泄漏到上层。
        """
        if self._closed:
            raise TransactionError("database is closed")
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn

        if self._is_memory:
            # 内存库: 所有线程共享同一条连接 (否则每个线程一个空库)。
            with self._registry_lock:
                shared = getattr(self, "_memory_conn", None)
                if shared is None:
                    shared = self._new_connection()
                    self._memory_conn = shared
                    self._all_connections.append(shared)
                conn = shared
        else:
            self._verify_readable()
            conn = self._new_connection()
            with self._registry_lock:
                self._all_connections.append(conn)

        self._local.conn = conn
        return conn

    def _verify_readable(self) -> None:
        """确认磁盘上的文件确实是一个 SQLite 库。"""
        if not os.path.exists(self._path):
            return
        try:
            if os.path.getsize(self._path) == 0:
                return  # 空文件: SQLite 会当作新库初始化
        except OSError:
            return
        probe: Optional[sqlite3.Connection] = None
        try:
            probe = sqlite3.connect(self._path)
            probe.execute("PRAGMA schema_version").fetchone()
        except sqlite3.DatabaseError as exc:
            raise CorruptedDatabaseError(
                f"database file is corrupted or not a SQLite database: "
                f"{self._path!r} ({exc})",
                detail={"path": self._path},
                cause=exc,
            ) from exc
        finally:
            if probe is not None:
                try:
                    probe.close()
                except sqlite3.Error:
                    pass

    def close(self) -> None:
        """关闭所有线程的连接 (幂等)。"""
        with self._registry_lock:
            connections = list(self._all_connections)
            self._all_connections.clear()
        for conn in connections:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()
        self._depth = 0
        self._closed = True

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # 备份快照 (Task 43)
    # ------------------------------------------------------------------

    def backup_to(self, target_path: str) -> str:
        """把当前库**一致性快照**到 ``target_path`` (Task 43 备份用)。

        为什么不能直接复制 ``.sqlite`` 文件
        ------------------------------------------------------------------
        本库默认跑在 **WAL** 模式 (见 :meth:`_configure`)。WAL 模式下已提交
        的数据可能还躺在 ``-wal`` 文件里, 主库文件本身是**过期的** —— 直接
        ``shutil.copy`` 会静默备份出一份**丢数据**的库, 而且看起来一切正常。

        正确做法是 SQLite 的在线备份 API (:meth:`sqlite3.Connection.backup`):
        它自己处理 WAL, 并且在整个过程中对源库保持一致读视图。这不是"更
        优雅", 而是唯一不会丢已提交数据的做法。

        目标文件必须不存在 (由调用方负责); 备份完成后 ``-wal`` 内容已合并
        进目标文件, 因此目标是一个自包含的单文件库。

        为什么"事务开着"必须**拒绝**而不是硬着头皮做
        ------------------------------------------------------------------
        在线备份 API 要在**源连接**上取得一个读锁; 而 ``BEGIN IMMEDIATE``
        已经在这个连接上持有写锁。SQLite 于是等一个自己永远放不掉的锁:
        ``backup_step`` 一直返回 ``SQLITE_BUSY``, Python 的循环里没有超时,
        进程就**永久挂死** (实测: 连"只 BEGIN、一个字都没写"的空事务也会挂)。

        这不是"慢", 是本机服务里最坏的失败形态 —— 请求永不返回, 而且写锁
        一直被占着, 之后每个写操作都要先等 5 秒 ``busy_timeout`` 再失败。

        所以这里显式拒绝。**绝不**替调用方 commit: 那会把一次本该回滚的事务
        变成永久落库, 比挂死更危险。备份层 (:class:`BackupService`) 的做法
        本来就是另开一个 :class:`Database` 再快照, 那条路径不受影响。
        """
        if self._is_memory:
            raise PersistenceValidationError(
                "in-memory databases cannot be backed up to a file "
                "(there is no durable source to snapshot)"
            )
        if not isinstance(target_path, str) or not target_path.strip():
            raise PersistenceValidationError("target_path must be a non-empty string")
        if self._depth > 0:
            raise TransactionError(
                "cannot snapshot the database while a transaction is open on this "
                "connection: the online-backup API waits forever for a read lock "
                "that this connection's own write transaction already holds. "
                "Commit or roll back first, or snapshot from a separate "
                "Database instance (which is what the backup layer does)."
            )

        target = os.path.abspath(target_path)
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
        if os.path.exists(target):
            raise PersistenceValidationError(
                f"backup target already exists: {target!r}"
            )

        # 先在临时文件上完成快照, 再原子改名 —— 中途失败不会留下半个库。
        tmp = target + ".snapshot"
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        destination = None
        try:
            destination = sqlite3.connect(tmp)
            self.connect().backup(destination)
            destination.commit()
        except sqlite3.Error as exc:
            raise CorruptedDatabaseError(
                f"cannot snapshot database {self._path!r}: {exc}", cause=exc
            ) from exc
        finally:
            if destination is not None:
                try:
                    destination.close()
                except sqlite3.Error:
                    pass

        try:
            os.replace(tmp, target)
        except OSError as exc:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise PersistenceValidationError(
                f"cannot move snapshot into place: {target!r} ({exc})", cause=exc
            ) from exc
        return target

    # ------------------------------------------------------------------
    # 事务
    # ------------------------------------------------------------------

    def begin(self, *, immediate: bool = True) -> None:
        """开始事务。最外层 ``BEGIN``, 嵌套层 ``SAVEPOINT``。"""
        if self._closed:
            raise TransactionError("database is closed")
        conn = self.connect()
        mode = "IMMEDIATE" if immediate else "DEFERRED"
        try:
            if self._depth == 0:
                conn.execute(f"BEGIN {mode}")
                self._write_lock.acquire()
            else:
                conn.execute(f"SAVEPOINT sp_{self._depth}")
        except sqlite3.Error as exc:
            raise TransactionError(
                f"failed to begin transaction: {exc}", cause=exc
            ) from exc
        self._depth += 1

    def commit(self) -> None:
        if self._depth == 0:
            raise TransactionError("commit() called with no active transaction")
        conn = self.connect()
        try:
            if self._depth == 1:
                conn.execute("COMMIT")
            else:
                conn.execute(f"RELEASE sp_{self._depth - 1}")
        except sqlite3.Error as exc:
            # COMMIT 失败时 SQLite **会保持事务打开**。如果不显式回滚,
            # 连接就停在"有未提交事务"的状态里, 后续写入会静默地被卷进
            # 那个永远不会提交的事务 —— 比直接报错危险得多。
            self._depth = max(0, self._depth - 1)
            self._abandon_transaction(conn)
            self._release_write_lock_if_idle()
            raise TransactionError(
                f"failed to commit transaction: {exc}", cause=exc
            ) from exc
        self._depth -= 1
        self._release_write_lock_if_idle()

    def _abandon_transaction(self, conn: sqlite3.Connection) -> None:
        """尽力把连接从失败的事务里拉出来 (不覆盖原始错误)。"""
        try:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        self._depth = 0
        self._release_write_lock_if_idle()

    def rollback(self) -> None:
        if self._depth == 0:
            raise TransactionError("rollback() called with no active transaction")
        conn = self.connect()
        try:
            if self._depth == 1:
                conn.execute("ROLLBACK")
            else:
                conn.execute(f"ROLLBACK TO sp_{self._depth - 1}")
                conn.execute(f"RELEASE sp_{self._depth - 1}")
        except sqlite3.Error as exc:
            self._depth = max(0, self._depth - 1)
            self._release_write_lock_if_idle()
            raise TransactionError(
                f"failed to rollback transaction: {exc}", cause=exc
            ) from exc
        self._depth -= 1
        self._release_write_lock_if_idle()

    def _release_write_lock_if_idle(self) -> None:
        if self._depth == 0:
            try:
                self._write_lock.release()
            except RuntimeError:
                pass

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """事务上下文: 正常退出 commit, 异常退出 rollback 并向上抛。

        嵌套调用是安全的: 内层异常只回滚内层保存点, 外层仍可继续
        (或自行回滚)。
        """
        self.begin(immediate=immediate)
        conn = self.connect()
        try:
            yield conn
        except BaseException:
            if self._depth > 0:
                self.rollback()
            raise
        else:
            if self._depth > 0:
                self.commit()

    # ------------------------------------------------------------------
    # SQL 执行
    # ------------------------------------------------------------------

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self.connect().execute(sql, tuple(params))

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
        return self.connect().executemany(sql, [tuple(p) for p in seq])

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self.connect().execute(sql, tuple(params)).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        return self.connect().execute(sql, tuple(params)).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        if row is None:
            return default
        return row[0]

    # ------------------------------------------------------------------
    # Schema / 迁移
    # ------------------------------------------------------------------

    def table_names(self) -> list[str]:
        rows = self.query(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [r["name"] for r in rows]

    def has_table(self, name: str) -> bool:
        return (
            self.query_one(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (str(name),),
            )
            is not None
        )

    def schema_version(self) -> int:
        """当前 schema 版本 (迁移台账中的最大版本号); 无台账时为 0。"""
        if not self.has_table(SCHEMA_VERSION_TABLE):
            return 0
        value = self.scalar(
            f"SELECT MAX(version) FROM {SCHEMA_VERSION_TABLE}", (), default=0
        )
        return int(value or 0)

    def applied_migrations(self) -> list[dict[str, Any]]:
        """已应用的迁移 (按版本升序)。"""
        if not self.has_table(SCHEMA_VERSION_TABLE):
            return []
        rows = self.query(
            f"SELECT version, name, applied_at FROM {SCHEMA_VERSION_TABLE} "
            "ORDER BY version ASC"
        )
        return [dict(r) for r in rows]

    def migrate(self, migrations: Optional[Sequence[Any]] = None) -> int:
        """把库迁移到最新版本; 返回本次应用的迁移条数。

        - 幂等: 已应用的版本会被跳过。
        - 每条迁移在**自己的事务**里执行, 失败即整体回滚该条,
          不会留下"半迁移"的 schema。
        - 库版本高于代码支持版本 -> :class:`UnsupportedSchemaVersionError`,
          绝不降级或猜测。
        """
        from src.persistence.migrations import MIGRATIONS, migration_name

        chain = tuple(migrations) if migrations is not None else MIGRATIONS
        _validate_chain(chain)

        current = self.schema_version()
        if current > MAX_SUPPORTED_SCHEMA_VERSION:
            raise UnsupportedSchemaVersionError(
                f"database schema version {current} is newer than this build "
                f"supports ({MAX_SUPPORTED_SCHEMA_VERSION})"
            )

        self._ensure_ledger()
        applied = 0
        for migration in chain:
            version = int(migration.version)
            if version <= current:
                continue
            try:
                with self.transaction():
                    migration.apply(self)
                    self.execute(
                        f"INSERT INTO {SCHEMA_VERSION_TABLE} "
                        "(version, name, applied_at) VALUES (?, ?, ?)",
                        (version, migration_name(migration), self._clock()),
                    )
            except Exception as exc:
                if isinstance(exc, (MigrationError, TransactionError)):
                    raise
                raise MigrationError(
                    f"migration {version:03d} ({migration_name(migration)}) failed: {exc}",
                    detail={"version": version},
                    cause=exc,
                ) from exc
            applied += 1
            current = version
        return applied

    def _ensure_ledger(self) -> None:
        """建立迁移台账表 (若不存在)。台账本身不属于任何编号迁移。

        先**只读地**看一眼表在不在, 不在才去写。这不是省事: ``migrate()``
        在"库已经是最新版本"这条最常见的路径上必须完全不写盘 ——
        否则每次打开数据库都要抢一次写锁, 而写锁会被任何正在进行的
        长事务 (导入、批量落库) 挡住。本项目是浏览器轮询的本地服务,
        "打开库 = 可能等 5 秒然后失败" 是不能接受的。

        ``CREATE TABLE IF NOT EXISTS`` 虽然逻辑上是空操作, 在 SQLite 里
        仍然是一条**写语句**, 会申请写锁 —— 所以不能靠它来当快速路径。
        """
        if self.has_table(SCHEMA_VERSION_TABLE):
            return
        try:
            with self.transaction():
                self.execute(
                    f"CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} ("
                    "  version INTEGER PRIMARY KEY,"
                    "  name TEXT NOT NULL,"
                    "  applied_at TEXT"
                    ")"
                )
        except Exception as exc:
            raise MigrationError(
                f"cannot create migration ledger: {exc}", cause=exc
            ) from exc

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    def integrity_check(self) -> str:
        """``PRAGMA integrity_check`` 的结果; 正常为 ``"ok"``。"""
        try:
            value = self.scalar("PRAGMA integrity_check", (), default="")
        except sqlite3.DatabaseError as exc:
            raise CorruptedDatabaseError(
                f"integrity check failed: {exc}", cause=exc
            ) from exc
        return str(value or "")

    def foreign_key_violations(self) -> list[dict[str, Any]]:
        """``PRAGMA foreign_key_check`` 的结果 (空列表 = 无悬挂引用)。"""
        rows = self.query("PRAGMA foreign_key_check")
        return [dict(r) for r in rows]

    def is_healthy(self) -> bool:
        return self.integrity_check() == "ok" and not self.foreign_key_violations()


def _validate_chain(chain: Sequence[Any]) -> None:
    """迁移链必须版本唯一且严格递增。"""
    seen: set[int] = set()
    previous = 0
    for migration in chain:
        version = getattr(migration, "version", None)
        if not isinstance(version, int) or version <= 0:
            raise MigrationError(
                f"migration version must be a positive int, got {version!r}"
            )
        if version in seen:
            raise MigrationError(f"duplicate migration version: {version}")
        if version <= previous:
            raise MigrationError(
                f"migration versions must be strictly increasing: "
                f"{version} after {previous}"
            )
        seen.add(version)
        previous = version
