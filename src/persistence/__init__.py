# -*- coding: utf-8 -*-
"""本地持久化层 (Task 42)。

选择 SQLite 的理由 (与规范一致): 本地、单用户、零独立数据库服务、
Windows 友好、可备份、稳定、够用。

分层
--------------------------------------------------------------------

::

    src/persistence/
        database.py      连接 / 事务 / 迁移执行器 (不知道任何业务概念)
        migrations/      编号迁移链 (001, 002, ...)
        models/          行 <-> 领域对象的编解码 + 表规格
        repositories/    12+ 个仓储 (Domain 语义, 不自己开事务)
        snapshot.py      跨聚合的整体往返 (证据库 / 知识结构 / 学习状态)

**Domain 不依赖 SQL**: ``src/persistence`` 单向依赖领域模型
(``src.models`` / ``src.knowledge_structure`` / ...), 领域模型完全不知道
数据库的存在。没有任何领域模块 import sqlite3。

本包**不**反向依赖 ``src.application`` / ``src.backup`` / ``src.api`` /
``src.web``。可注入 ``Clock`` 的真源在零依赖的 ``src.common.clock``
(``utc_now_iso`` / ``Clock`` / ``fixed_clock``); ``src.application.runtime``
只是它的兼容 re-export, 以保证"全项目只有一处非确定性来源"。

``sqlite3`` 只出现在本包内
--------------------------------------------------------------------

现有回归测试 ``tests/test_evidence_store.py::TestScopeAudit`` 与
``tests/test_knowledge_pipeline.py::test_pipeline_does_not_use_network_or_db``
明确禁止 ``src.evidence_store`` 和 ``src.knowledge_pipeline`` 出现
``sqlite3``。把持久化放进独立包, 那两条约束继续成立 —— 提取/分析层保持
纯函数, 存储是它旁边的一层, 不是它里面的一部分。

用法
--------------------------------------------------------------------

::

    db = open_database("data/database/classroom.sqlite")
    repos = Repositories(db)
    with repos.transaction():
        repos.courses.save(course)
"""

from __future__ import annotations

from src.persistence.database import (
    MEMORY_PATH,
    SCHEMA_VERSION_TABLE,
    Database,
)
from src.persistence.errors import (
    CorruptedDatabaseError,
    DuplicateRecordError,
    MigrationError,
    PayloadDecodeError,
    PersistenceError,
    PersistenceErrorCode,
    PersistenceValidationError,
    RecordNotFoundError,
    TransactionError,
    UnsupportedSchemaVersionError,
)
from src.persistence.migrations import MIGRATIONS, latest_version, migration_name
from src.persistence.repositories import Repositories

__all__ = [
    # 数据库
    "Database",
    "open_database",
    "SCHEMA_VERSION_TABLE",
    "MEMORY_PATH",
    # 仓储
    "Repositories",
    # 迁移
    "MIGRATIONS",
    "latest_version",
    "migration_name",
    # 错误
    "PersistenceError",
    "PersistenceErrorCode",
    "PersistenceValidationError",
    "RecordNotFoundError",
    "DuplicateRecordError",
    "PayloadDecodeError",
    "CorruptedDatabaseError",
    "MigrationError",
    "TransactionError",
    "UnsupportedSchemaVersionError",
]


def open_database(
    path: str,
    *,
    clock=None,
    migrate: bool = True,
    busy_timeout_ms: int = 5000,
) -> Database:
    """打开 (必要时创建) 数据库并迁移到最新 schema。

    ``migrate=True`` (默认) 保证调用方拿到的库一定是当前版本的 schema,
    避免"忘了迁移"变成运行期的神秘 ``no such table`` 错误。
    """
    database = Database(path, clock=clock, busy_timeout_ms=busy_timeout_ms)
    if migrate:
        database.migrate()
    return database
