# -*- coding: utf-8 -*-
"""共享测试夹具。

注意: 这里**只**新增夹具, 不修改任何既有测试的行为。夹具是按需实例化的,
因此本文件对 Task 1–41 的既有测试完全无影响。

持久化相关夹具 (Task 42) 刻意用临时目录里的真实文件而不是 ``:memory:``
—— 因为"重启后数据还在"是持久化层的核心要求, 只有文件才能测。
"""

import pytest

from src.persistence.database import Database
from src.persistence.repositories import Repositories


@pytest.fixture
def db_path(tmp_path):
    """一个临时 SQLite 文件路径 (尚未创建)。"""
    return str(tmp_path / "classroom.sqlite")


@pytest.fixture
def db(db_path):
    """已迁移到最新 schema 的 ``Database``, 测试结束后自动关闭。"""
    database = Database(db_path)
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def repos(db):
    """绑定到 ``db`` 的全部仓储。"""
    return Repositories(db)


@pytest.fixture
def db_factory(tmp_path):
    """工厂: 每次调用返回一个**独立**的 ``Repositories``。

    用于"多个学生 / 多门课"这类需要互相隔离的测试。
    """
    created: list[Database] = []
    counter = {"n": 0}

    def make(*, migrate: bool = True) -> Repositories:
        counter["n"] += 1
        path = str(tmp_path / f"factory-{counter['n']}.sqlite")
        database = Database(path)
        if migrate:
            database.migrate()
        created.append(database)
        return Repositories(database)

    yield make

    for database in created:
        database.close()
