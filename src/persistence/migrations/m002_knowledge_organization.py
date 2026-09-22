# -*- coding: utf-8 -*-
"""迁移 002 —— 数据库级元数据 + 处理状态的时间列 + 课程内标题索引。

这是**第二条**迁移, 它的存在本身就是对迁移机制的验证:
``001`` 之后新建的库必须能升到 ``002``, 而一个停在 ``001`` 的旧库必须能
**在不丢数据的前提下**补齐到 ``002`` (见 tests/test_persistence_migration.py)。

只做加法:
- ``CREATE TABLE database_meta``  (新增表)
- ``ALTER TABLE material_processing ADD COLUMN last_attempt_at``  (新增列)
- ``CREATE INDEX idx_kp_course_title``  (新增索引)

绝不 ``DROP`` / 绝不重建已有业务表。
"""

from __future__ import annotations

from src.persistence.migrations import Migration

MIGRATION_002 = Migration(
    version=2,
    name="database_meta_and_processing_timestamp",
    statements=(
        # 数据库级键值元数据: 记录写入本库的应用版本等诊断信息。
        # 它不是业务数据, 因此不参与任何领域对象的身份计算。
        """
        CREATE TABLE database_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
        """,
        # 处理状态最近一次尝试时间 (运行期元数据, 不参与身份)。
        "ALTER TABLE material_processing ADD COLUMN last_attempt_at TEXT",
        # 课程内按标题查知识点 (UI 列表 / 诊断用)。
        "CREATE INDEX idx_kp_course_title ON knowledge_points(course_id, title)",
    ),
)
