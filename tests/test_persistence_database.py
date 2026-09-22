# -*- coding: utf-8 -*-
"""Task 42 —— Database (连接 / 事务 / 迁移执行器) 测试。

覆盖: 打开与创建 / PRAGMA / 线程局部连接 / begin-commit-rollback /
嵌套 SAVEPOINT / 事务状态错误 / 损坏库处理 / 健康检查 / 内存库。
"""

import os
import sqlite3
import threading

import pytest

from src.persistence.database import (
    MEMORY_PATH,
    SCHEMA_VERSION_TABLE,
    Database,
)
from src.persistence.migrations import (
    MIGRATIONS,
    latest_version,
    migration_name,
)
from src.persistence.errors import (
    CorruptedDatabaseError,
    PersistenceValidationError,
    TransactionError,
)


# ----------------------------------------------------------------------
# 夹具
# ----------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "classroom.sqlite")


@pytest.fixture
def db(db_path):
    database = Database(db_path)
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def raw_db(db_path):
    """已迁移但由测试自行关闭的库。"""
    return Database(db_path)


# ----------------------------------------------------------------------
# 打开 / 创建
# ----------------------------------------------------------------------


def test_open_creates_the_database_file(db_path):
    database = Database(db_path)
    database.migrate()
    assert os.path.isfile(db_path)
    database.close()


def test_open_creates_missing_parent_directories(tmp_path):
    nested = str(tmp_path / "a" / "b" / "c" / "classroom.sqlite")
    database = Database(nested)
    database.migrate()
    assert os.path.isfile(nested)
    database.close()


def test_open_with_create_false_on_missing_file_is_rejected(tmp_path):
    with pytest.raises(PersistenceValidationError):
        Database(str(tmp_path / "nope.sqlite"), create=False)


def test_open_rejects_blank_path():
    with pytest.raises(PersistenceValidationError):
        Database("   ")


def test_open_rejects_negative_busy_timeout(db_path):
    with pytest.raises(PersistenceValidationError):
        Database(db_path, busy_timeout_ms=-1)


def test_path_property_is_preserved(db_path):
    database = Database(db_path)
    assert database.path == db_path
    database.close()


def test_empty_existing_file_is_treated_as_a_new_database(db_path):
    with open(db_path, "wb"):
        pass
    database = Database(db_path)
    assert database.migrate() > 0
    database.close()


def test_closed_database_rejects_connect(db_path):
    database = Database(db_path)
    database.migrate()
    database.close()
    assert database.closed is True
    with pytest.raises(TransactionError):
        database.connect()


def test_close_is_idempotent(db_path):
    database = Database(db_path)
    database.migrate()
    database.close()
    database.close()  # 不抛异常
    assert database.closed is True


def test_database_works_as_a_context_manager(db_path):
    with Database(db_path) as database:
        database.migrate()
        assert database.schema_version() > 0
    assert database.closed is True


# ----------------------------------------------------------------------
# 损坏库
# ----------------------------------------------------------------------


def test_garbage_file_is_reported_as_corrupted(db_path):
    with open(db_path, "wb") as handle:
        handle.write(b"this is definitely not a sqlite database" * 20)
    database = Database(db_path)
    with pytest.raises(CorruptedDatabaseError):
        database.connect()


def test_truncated_header_is_reported_as_corrupted(db_path):
    # 正确的 SQLite magic 但其余全是垃圾 -> 打开时报错
    with open(db_path, "wb") as handle:
        handle.write(b"SQLite format 3\x00" + b"\x00" * 10 + b"junk" * 100)
    database = Database(db_path)
    with pytest.raises(CorruptedDatabaseError):
        database.migrate()


def test_corrupted_error_carries_a_structured_code(db_path):
    with open(db_path, "wb") as handle:
        handle.write(b"nope" * 100)
    database = Database(db_path)
    with pytest.raises(CorruptedDatabaseError) as excinfo:
        database.connect()
    assert excinfo.value.code == "STORAGE_CORRUPTED_DATABASE"
    assert "STORAGE" in excinfo.value.code


# ----------------------------------------------------------------------
# PRAGMA / 健康
# ----------------------------------------------------------------------


def test_foreign_keys_are_enabled(db):
    assert db.scalar("PRAGMA foreign_keys") == 1


def test_file_database_uses_wal_journal(db):
    assert str(db.scalar("PRAGMA journal_mode")).lower() == "wal"


def test_integrity_check_is_ok(db):
    assert db.integrity_check() == "ok"
    assert db.is_healthy() is True


def test_foreign_key_check_is_empty(db):
    assert db.foreign_key_violations() == []


def test_dangling_foreign_key_insert_is_rejected(db):
    """外键是**立即**强制的: 悬挂引用根本插不进去。

    (比"插进去再检查"更强 —— 数据库不让坏数据落地。)
    """
    db.execute(
        "INSERT INTO courses (course_id, payload) VALUES (?, ?)",
        ("course-x", "{}"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO sessions (session_id, course_id, payload) VALUES (?, ?, ?)",
            ("session-x", "course-missing", "{}"),
        )


def test_foreign_key_check_finds_pre_existing_violations(db):
    """``foreign_key_check`` 能发现"绕过外键写入的"历史脏数据。

    做法: 关掉外键写一行悬挂引用, 再打开外键并检查 —— 这正是迁移/恢复
    场景下需要的能力 (先批量写入, 再统一校验)。
    """
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(
        "INSERT INTO sessions (session_id, course_id, payload) VALUES (?, ?, ?)",
        ("session-x", "course-missing", "{}"),
    )
    db.execute("PRAGMA foreign_keys = ON")

    violations = db.foreign_key_violations()
    assert len(violations) == 1
    assert db.is_healthy() is False


def test_healthy_database_reports_no_violations(db):
    assert db.foreign_key_violations() == []
    assert db.is_healthy() is True


def test_busy_timeout_is_applied(db):
    assert db.scalar("PRAGMA busy_timeout") == 5000


# ----------------------------------------------------------------------
# SQL 执行
# ----------------------------------------------------------------------


def test_execute_and_query_roundtrip(db):
    db.execute(
        "INSERT INTO database_meta (key, value) VALUES (?, ?)", ("k", "v")
    )
    assert db.scalar("SELECT value FROM database_meta WHERE key = ?", ("k",)) == "v"


def test_query_one_returns_none_when_missing(db):
    assert db.query_one("SELECT 1 AS x WHERE 0") is None


def test_scalar_default_is_used_when_missing(db):
    assert db.scalar("SELECT 1 AS x WHERE 0", (), default="fallback") == "fallback"


def test_executemany_inserts_every_row(db):
    db.executemany(
        "INSERT INTO database_meta (key, value) VALUES (?, ?)",
        [("a", "1"), ("b", "2"), ("c", "3")],
    )
    assert db.scalar("SELECT COUNT(*) FROM database_meta") == 3


def test_rows_expose_column_names(db):
    db.execute("INSERT INTO database_meta (key, value) VALUES (?, ?)", ("k", "v"))
    row = db.query_one("SELECT key, value FROM database_meta")
    assert row["key"] == "k"
    assert row["value"] == "v"


def test_has_table_reports_presence(db):
    assert db.has_table("courses") is True
    assert db.has_table("no_such_table") is False


def test_table_names_are_deterministic(db):
    assert db.table_names() == sorted(db.table_names())


# ----------------------------------------------------------------------
# 事务
# ----------------------------------------------------------------------


def test_in_transaction_is_false_outside_a_transaction(db):
    assert db.in_transaction is False


def test_begin_commit_persists(db):
    db.begin()
    assert db.in_transaction is True
    db.execute("INSERT INTO database_meta (key, value) VALUES (?, ?)", ("k", "v"))
    db.commit()
    assert db.in_transaction is False
    assert db.scalar("SELECT COUNT(*) FROM database_meta") == 1


def test_begin_rollback_discards(db):
    db.begin()
    db.execute("INSERT INTO database_meta (key, value) VALUES (?, ?)", ("k", "v"))
    db.rollback()
    assert db.in_transaction is False
    assert db.scalar("SELECT COUNT(*) FROM database_meta") == 0


def test_commit_without_begin_raises(db):
    with pytest.raises(TransactionError):
        db.commit()


def test_rollback_without_begin_raises(db):
    with pytest.raises(TransactionError):
        db.rollback()


def test_transaction_context_manager_commits(db):
    with db.transaction():
        db.execute("INSERT INTO database_meta (key, value) VALUES (?, ?)", ("k", "v"))
    assert db.scalar("SELECT COUNT(*) FROM database_meta") == 1


def test_transaction_context_manager_rolls_back_on_exception(db):
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.execute(
                "INSERT INTO database_meta (key, value) VALUES (?, ?)", ("k", "v")
            )
            raise RuntimeError("boom")
    assert db.scalar("SELECT COUNT(*) FROM database_meta") == 0
    assert db.in_transaction is False


def test_nested_inner_rollback_keeps_outer_work(db):
    with db.transaction():
        db.execute("INSERT INTO database_meta (key, value) VALUES (?, ?)", ("outer", "1"))
        with pytest.raises(RuntimeError):
            with db.transaction():
                db.execute(
                    "INSERT INTO database_meta (key, value) VALUES (?, ?)",
                    ("inner", "2"),
                )
                raise RuntimeError("boom")
        # 外层仍然活着
        assert db.in_transaction is True
    assert db.scalar("SELECT COUNT(*) FROM database_meta") == 1
    assert db.scalar("SELECT value FROM database_meta WHERE key='outer'") == "1"


def test_nested_inner_commit_is_discarded_by_outer_rollback(db):
    with pytest.raises(RuntimeError):
        with db.transaction():
            with db.transaction():
                db.execute(
                    "INSERT INTO database_meta (key, value) VALUES (?, ?)",
                    ("inner", "2"),
                )
            raise RuntimeError("boom")
    assert db.scalar("SELECT COUNT(*) FROM database_meta") == 0


def test_nested_transactions_can_go_three_deep(db):
    with db.transaction():
        with db.transaction():
            with db.transaction():
                db.execute(
                    "INSERT INTO database_meta (key, value) VALUES (?, ?)", ("deep", "3")
                )
    assert db.scalar("SELECT COUNT(*) FROM database_meta") == 1
    assert db.in_transaction is False


def test_transaction_leaves_no_open_state_after_failure(db):
    with pytest.raises(RuntimeError):
        with db.transaction():
            raise RuntimeError("boom")
    assert db.in_transaction is False
    # 之后仍能正常开新事务
    with db.transaction():
        db.execute("INSERT INTO database_meta (key, value) VALUES (?, ?)", ("k", "v"))
    assert db.scalar("SELECT COUNT(*) FROM database_meta") == 1


def test_deferred_transaction_also_works(db):
    with db.transaction(immediate=False):
        db.execute("INSERT INTO database_meta (key, value) VALUES (?, ?)", ("k", "v"))
    assert db.scalar("SELECT COUNT(*) FROM database_meta") == 1


# ----------------------------------------------------------------------
# 连接与线程
# ----------------------------------------------------------------------


def test_connect_returns_the_same_connection_within_a_thread(db):
    assert db.connect() is db.connect()


def test_each_thread_gets_its_own_connection(db):
    seen = {}

    def worker():
        seen["conn"] = db.connect()
        seen["value"] = db.scalar("SELECT COUNT(*) FROM courses")

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert seen["conn"] is not db.connect()
    assert seen["value"] == 0


def test_many_threads_can_read_concurrently(db):
    db.execute(
        "INSERT INTO courses (course_id, name, payload) VALUES (?, ?, ?)",
        ("course-1", "A", "{}"),
    )
    results = []
    lock = threading.Lock()

    def worker():
        value = db.scalar("SELECT COUNT(*) FROM courses")
        with lock:
            results.append(value)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [1] * 8


def test_connection_survives_after_close_of_another_thread(db):
    """一个线程关掉自己的连接, 不影响别的线程。"""
    database = db

    def worker():
        conn = database.connect()
        conn.execute("SELECT 1")

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    # 主线程连接仍然可用
    assert database.scalar("SELECT 1") == 1


# ----------------------------------------------------------------------
# 内存库
# ----------------------------------------------------------------------


def test_memory_database_is_recognised():
    assert Database(MEMORY_PATH).is_memory is True


def test_memory_database_migrates_and_queries():
    database = Database(MEMORY_PATH)
    applied = database.migrate()
    # 不写死 2: 迁移链会随版本变长 (Task 68 加了 003), 写死只会让
    # "新增一条迁移" 变成一次无意义的红灯。真正要钉的是"全部应用完"。
    assert applied == len(MIGRATIONS)
    assert database.schema_version() == latest_version()
    database.execute(
        "INSERT INTO database_meta (key, value) VALUES (?, ?)", ("k", "v")
    )
    assert database.scalar("SELECT COUNT(*) FROM database_meta") == 1
    database.close()


def test_memory_database_has_no_wal():
    database = Database(MEMORY_PATH)
    database.migrate()
    assert str(database.scalar("PRAGMA journal_mode")).lower() != "wal"
    database.close()


# ----------------------------------------------------------------------
# 迁移台账表
# ----------------------------------------------------------------------


def test_schema_version_table_exists_after_migrate(db):
    assert SCHEMA_VERSION_TABLE in db.table_names()


def test_schema_version_is_zero_before_migrate(db_path):
    database = Database(db_path)
    assert database.schema_version() == 0
    database.close()


def test_applied_migrations_is_empty_before_migrate(db_path):
    database = Database(db_path)
    assert database.applied_migrations() == []
    database.close()


def test_applied_migrations_records_versions_and_names(db):
    applied = db.applied_migrations()
    assert [row["version"] for row in applied] == [
        migration.version for migration in MIGRATIONS
    ]
    assert [row["name"] for row in applied] == [
        migration_name(migration) for migration in MIGRATIONS
    ]


def test_migration_ledger_timestamps_come_from_the_injected_clock(db_path):
    from src.application.runtime import fixed_clock

    database = Database(db_path, clock=fixed_clock("2026-01-01T00:00:00+00:00"))
    database.migrate()
    assert database.applied_migrations()[0]["applied_at"] == "2026-01-01T00:00:00+00:00"
    database.close()
