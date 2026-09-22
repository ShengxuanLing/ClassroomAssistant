# -*- coding: utf-8 -*-
"""编号迁移链 (Task 42)。

规范要求: 建立 ``schema_version``, 支持 ``migration 001`` / ``002`` / ...,
**不得**依赖"删库重建"。

约定
--------------------------------------------------------------------

- 每个迁移是一个 :class:`Migration`, 携带**严格递增**的整数 ``version``。
- 迁移只做加法: 新增表 / 新增列 / 建索引 / 数据回填。
  绝不 ``DROP TABLE`` 已有业务表 —— 那等于删库重建。
- 每条迁移由 :meth:`Database.migrate` 放进**自己的事务**里执行,
  失败即整体回滚, 不会留下半迁移的 schema。
- 迁移名一旦发布就不许改 (它写进了台账, 改名会让审计对不上)。

为什么版本号在代码里而不在数据库里
--------------------------------------------------------------------

``version`` 是代码的事实, 台账只是"已经执行过哪些"的流水。这样
"代码里有 001/002/003, 库里只到 002" 就自然表示"还差 003", 迁移器
只要按序补齐即可, 不需要任何外部状态文件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

__all__ = ["Migration", "MIGRATIONS", "migration_name", "latest_version"]


@dataclass(frozen=True)
class Migration:
    """一条编号迁移。

    ``statements`` 按顺序执行; ``hook`` 用于语句表达不了的逻辑
    (例如数据回填)。两者都在同一个事务内。
    """

    version: int
    name: str
    statements: tuple[str, ...] = ()
    hook: Optional[Callable[[object], None]] = field(default=None, compare=False)

    def apply(self, database: object) -> None:
        """在 ``database`` 上执行本迁移 (调用方负责事务)。"""
        for statement in self.statements:
            database.execute(statement)
        if self.hook is not None:
            self.hook(database)


def migration_name(migration: Migration) -> str:
    return f"{int(migration.version):03d}_{migration.name}"


def _chain() -> tuple:
    from src.persistence.migrations.m001_initial_schema import MIGRATION_001
    from src.persistence.migrations.m002_knowledge_organization import MIGRATION_002
    from src.persistence.migrations.m003_course_scoped_identity import MIGRATION_003

    return (MIGRATION_001, MIGRATION_002, MIGRATION_003)


#: 完整迁移链 (按版本升序)。延迟导入以避免循环依赖。
MIGRATIONS: tuple = _chain()


def latest_version() -> int:
    """代码中定义的最高迁移版本。"""
    return max((int(m.version) for m in MIGRATIONS), default=0)
