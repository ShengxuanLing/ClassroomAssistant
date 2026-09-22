# -*- coding: utf-8 -*-
"""Task 42 —— 迁移机制测试。

覆盖: ``schema_version`` 台账 / 编号迁移 001-003 / 幂等 / 增量升级
(旧库补到新版本且**不丢数据**) / 迁移失败整体回滚 / 版本链校验 /
"不依赖删库重建" 的结构守卫。

Task 68 新增了 ``003``。下面两条把"链上有几条迁移"写死成常量的断言
随之从 2 改到 3 —— 这不是为了迁就实现而放宽判据, 而是**事实变了**:
链上确实多了一条迁移。真正的守卫是"不得 DROP / 不得重建既有表",
那两条一个字都没改。
"""

import os

import pytest

from src.persistence.database import MAX_SUPPORTED_SCHEMA_VERSION, Database
from src.persistence.errors import (
    MigrationError,
    UnsupportedSchemaVersionError,
)
from src.persistence.migrations import MIGRATIONS, Migration, latest_version, migration_name
from src.persistence.migrations.m001_initial_schema import MIGRATION_001
from src.persistence.models.tables import LINK_TABLES, TABLES

#: 迁移器自己维护、不属于任何编号迁移的表。
_META_TABLES = frozenset({"schema_version", "database_meta"})


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "migrate.sqlite")


@pytest.fixture
def db(db_path):
    database = Database(db_path)
    database.migrate()
    yield database
    database.close()


# ----------------------------------------------------------------------
# 迁移链定义
# ----------------------------------------------------------------------


def test_latest_version_is_three():
    assert latest_version() == 3


def test_chain_versions_are_strictly_increasing():
    versions = [m.version for m in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(set(versions)) == len(versions)


def test_migration_names_are_prefixed_with_their_version():
    assert migration_name(MIGRATION_001) == "001_initial_schema"
    assert all(migration_name(m).startswith(f"{m.version:03d}_") for m in MIGRATIONS)


def test_no_migration_drops_a_table():
    """规范明确: 不要直接依赖"删库重建"。

    结构守卫: 任何迁移语句里出现 ``DROP TABLE`` 都视为违规。
    """
    for migration in MIGRATIONS:
        for statement in migration.statements:
            assert "DROP TABLE" not in statement.upper(), (
                f"migration {migration.version} drops a table; "
                "migrations must be additive"
            )


def test_no_migration_recreates_an_existing_business_table():
    """``CREATE TABLE`` 不得重复创建 001 里已经建好的业务表。"""
    seen: set[str] = set()
    for migration in MIGRATIONS:
        for statement in migration.statements:
            upper = statement.upper()
            if "CREATE TABLE" not in upper:
                continue
            if "IF NOT EXISTS" in upper:
                continue
            name = statement.split("CREATE TABLE", 1)[1].strip().split()[0]
            name = name.strip("(")
            assert name not in seen, (
                f"migration {migration.version} re-creates table {name}"
            )
            seen.add(name)


# ----------------------------------------------------------------------
# 台账 / 版本
# ----------------------------------------------------------------------


def test_migrate_reports_every_migration_applied(db_path):
    database = Database(db_path)
    assert database.migrate() == len(MIGRATIONS)
    database.close()


def test_schema_version_matches_latest(db):
    assert db.schema_version() == latest_version()


def test_migrate_is_idempotent(db):
    assert db.migrate() == 0
    assert db.migrate() == 0
    assert db.schema_version() == latest_version()


def test_ledger_has_one_row_per_migration(db):
    applied = db.applied_migrations()
    assert len(applied) == len(MIGRATIONS)
    assert [row["version"] for row in applied] == [m.version for m in MIGRATIONS]


def test_ledger_is_ordered_by_version(db):
    versions = [row["version"] for row in db.applied_migrations()]
    assert versions == sorted(versions)


def test_ledger_names_are_unique(db):
    names = [row["name"] for row in db.applied_migrations()]
    assert len(set(names)) == len(names)


# ----------------------------------------------------------------------
# 增量升级
# ----------------------------------------------------------------------


def test_migrating_only_001_stops_at_version_one(db_path):
    database = Database(db_path)
    assert database.migrate([MIGRATION_001]) == 1
    assert database.schema_version() == 1
    database.close()


def test_database_meta_is_absent_at_version_one(db_path):
    database = Database(db_path)
    database.migrate([MIGRATION_001])
    assert database.has_table("database_meta") is False
    database.close()


def test_upgrade_from_001_to_002_keeps_existing_data(db_path):
    """旧库补到新版本时**不丢数据** —— 这是迁移存在的唯一理由。"""
    database = Database(db_path)
    database.migrate([MIGRATION_001])
    database.execute(
        "INSERT INTO courses (course_id, name, code, payload) VALUES (?, ?, ?, ?)",
        ("course-1", "Álgebra", "ALG", '{"course_id":"course-1"}'),
    )
    database.close()

    reopened = Database(db_path)
    assert reopened.migrate() == len(MIGRATIONS) - 1  # 补 002 之后的所有迁移
    assert reopened.schema_version() == latest_version()
    assert reopened.has_table("database_meta") is True
    # 数据完好
    assert reopened.scalar("SELECT name FROM courses WHERE course_id='course-1'") == "Álgebra"
    reopened.close()


def test_migration_002_adds_the_processing_timestamp_column(db):
    columns = [r["name"] for r in db.query("PRAGMA table_info(material_processing)")]
    assert "last_attempt_at" in columns


def test_migration_002_adds_the_course_title_index(db):
    indexes = [r["name"] for r in db.query("PRAGMA index_list(knowledge_points)")]
    assert "idx_kp_course_title" in indexes


def test_migration_002_creates_database_meta(db):
    assert db.has_table("database_meta") is True
    db.execute("INSERT INTO database_meta (key, value) VALUES (?, ?)", ("k", "v"))
    assert db.scalar("SELECT COUNT(*) FROM database_meta") == 1


def test_upgrading_does_not_change_the_number_of_applied_rows(db):
    before = len(db.applied_migrations())
    db.migrate()
    assert len(db.applied_migrations()) == before


# ----------------------------------------------------------------------
# 失败回滚
# ----------------------------------------------------------------------


def test_failing_migration_rolls_back_its_own_statements(db):
    """迁移中途失败 -> 该条迁移的语句全部回滚, 台账不前进。"""
    database = Database(":memory:")
    database.migrate()
    assert database.schema_version() == latest_version()

    broken = Migration(
        version=latest_version() + 1,  # 必须是链上还没用到的版本号
        name="broken",
        statements=(
            "CREATE TABLE half_applied (a TEXT)",
            "THIS IS NOT VALID SQL",
        ),
    )
    with pytest.raises(MigrationError):
        database.migrate(list(MIGRATIONS) + [broken])

    # 半成品表不存在, 版本没有前进
    assert database.has_table("half_applied") is False
    assert database.schema_version() == latest_version()
    assert [r["version"] for r in database.applied_migrations()] == [
        m.version for m in MIGRATIONS
    ]
    database.close()


def test_failing_migration_error_names_the_version(db):
    database = Database(":memory:")
    database.migrate()
    broken = Migration(version=9, name="broken", statements=("NOPE",))
    with pytest.raises(MigrationError) as excinfo:
        database.migrate(list(MIGRATIONS) + [broken])
    assert "009" in str(excinfo.value)
    assert excinfo.value.code == "STORAGE_MIGRATION_FAILED"
    database.close()


def test_failed_migration_leaves_database_usable(db):
    database = Database(":memory:")
    database.migrate()
    broken = Migration(
        version=latest_version() + 1, name="broken", statements=("NOPE",)
    )
    with pytest.raises(MigrationError):
        database.migrate(list(MIGRATIONS) + [broken])
    # 之后仍可正常使用
    database.execute("INSERT INTO database_meta (key, value) VALUES (?, ?)", ("k", "v"))
    assert database.scalar("SELECT COUNT(*) FROM database_meta") == 1
    database.close()


# ----------------------------------------------------------------------
# 版本链校验
# ----------------------------------------------------------------------


def test_duplicate_migration_version_is_rejected():
    database = Database(":memory:")
    duplicated = (
        Migration(version=1, name="a", statements=("CREATE TABLE t1 (a)",)),
        Migration(version=1, name="b", statements=("CREATE TABLE t2 (a)",)),
    )
    with pytest.raises(MigrationError):
        database.migrate(duplicated)
    database.close()


def test_non_increasing_migration_version_is_rejected():
    database = Database(":memory:")
    out_of_order = (
        Migration(version=2, name="a", statements=("CREATE TABLE t1 (a)",)),
        Migration(version=1, name="b", statements=("CREATE TABLE t2 (a)",)),
    )
    with pytest.raises(MigrationError):
        database.migrate(out_of_order)
    database.close()


def test_zero_migration_version_is_rejected():
    database = Database(":memory:")
    with pytest.raises(MigrationError):
        database.migrate((Migration(version=0, name="a", statements=()),))
    database.close()


def test_non_integer_migration_version_is_rejected():
    database = Database(":memory:")
    with pytest.raises(MigrationError):
        database.migrate((Migration(version="one", name="a", statements=()),))
    database.close()


def test_version_newer_than_this_build_is_rejected(db_path):
    """库版本高于代码支持 -> 拒绝打开, 绝不降级或猜着读。"""
    database = Database(db_path)
    database.migrate()
    database.execute(
        "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
        (MAX_SUPPORTED_SCHEMA_VERSION + 1, "from_the_future", None),
    )
    with pytest.raises(UnsupportedSchemaVersionError):
        database.migrate()
    database.close()


# ----------------------------------------------------------------------
# schema 与表规格的一致性 (漂移守卫)
# ----------------------------------------------------------------------


def test_every_registered_table_exists_in_the_database(db):
    missing = sorted(name for name in TABLES if not db.has_table(name))
    assert missing == []


def test_every_link_table_exists_in_the_database(db):
    missing = sorted(name for name in LINK_TABLES if not db.has_table(name))
    assert missing == []


def test_every_created_business_table_is_registered(db):
    """新增了表却忘了登记到 TABLES -> 立即失败。

    没登记的后果是"这个表永远不会有仓储", 属于静默失效, 所以要有守卫。
    """
    known = set(TABLES) | set(LINK_TABLES) | _META_TABLES
    unregistered = sorted(set(db.table_names()) - known)
    assert unregistered == [], (
        f"tables created by migrations but missing from models/tables.py: {unregistered}"
    )


def test_no_registered_table_is_missing_from_the_schema(db):
    known = set(db.table_names())
    phantom = sorted(set(TABLES) - known)
    assert phantom == [], f"registered but never created: {phantom}"


def test_every_table_has_a_primary_key(db):
    for name in sorted(TABLES):
        info = db.query(f"PRAGMA table_info({name})")
        pk_columns = [r["name"] for r in info if r["pk"]]
        assert pk_columns, f"table {name} has no primary key"


def test_every_table_has_a_payload_column(db):
    for name in sorted(TABLES):
        columns = {r["name"] for r in db.query(f"PRAGMA table_info({name})")}
        assert "payload" in columns, f"table {name} has no payload column"
        assert "payload_version" in columns


def test_registered_key_columns_match_the_schema(db):
    """规格里声明的主键列必须真的存在且真的是主键。"""
    for name, spec in sorted(TABLES.items()):
        info = db.query(f"PRAGMA table_info({name})")
        declared = {r["name"] for r in info if r["pk"]}
        assert declared == set(spec.key_columns), (
            f"{name}: schema pk {sorted(declared)} != spec {sorted(spec.key_columns)}"
        )


def test_spec_order_by_columns_exist(db):
    """默认排序键必须真实存在, 否则 ``rows()`` 会在运行期炸。"""
    for name, spec in sorted(TABLES.items()):
        columns = {r["name"] for r in db.query(f"PRAGMA table_info({name})")}
        for token in spec.order_by.replace(",", " ").split():
            assert token in columns, f"{name}: order_by references unknown column {token}"


def test_migration_files_are_present_on_disk():
    root = os.path.join("src", "persistence", "migrations")
    assert os.path.isfile(os.path.join(root, "m001_initial_schema.py"))
    assert os.path.isfile(os.path.join(root, "m002_knowledge_organization.py"))
