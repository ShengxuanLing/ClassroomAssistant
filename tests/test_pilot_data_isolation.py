# -*- coding: utf-8 -*-
"""Task 71.2 — Pilot 数据与测试数据彻底分离。

核心命题: pytest / stress test / UI audit / backup drill 都不能污染真实 Pilot 数据。

验证手段
--------------------------------------------------------------------
1. 数据目录守卫 ``guard_data_dir`` 拒绝把仓库根 / ``src`` / ``tests`` 当成数据目录
   (否则会在源码树里就地建 ``database/`` ``materials/`` ...)。
2. 数据目录画像解析 ``resolve_data_dir`` 把真实使用与测试运行落到两个不相交目录
   (``classroom-data`` vs ``data-test``)。
3. ``Workspace`` 在仓库根上构造会被守卫拦下 (用户可读的 ConfigurationError)。
4. 测试套件使用 ``tmp_path`` 时一切正常 (隔离不破坏正常用例)。
"""

from __future__ import annotations

import os

import pytest

from src.application.config import resolve_data_dir
from src.application.data_dirs import (
    guard_data_dir,
    is_repository_root,
)
from src.application.errors import ConfigurationError
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace


REPO_ROOT = os.path.abspath(os.getcwd())


@pytest.mark.parametrize(
    "candidate",
    [
        REPO_ROOT,
        os.path.join(REPO_ROOT, "src"),
        os.path.join(REPO_ROOT, "tests"),
        ".",
    ],
)
def test_guard_rejects_repository_tree(candidate):
    """仓库根 / src / tests 一律被守卫拒绝。"""
    with pytest.raises(ConfigurationError):
        guard_data_dir(candidate)


def test_guard_allows_dedicated_data_dirs():
    """classroom-data / data-test / data-pilot 这类专有子目录放行。"""
    for name in ("classroom-data", "data-test", "data-pilot"):
        path = os.path.join(REPO_ROOT, name)
        assert guard_data_dir(path) == os.path.abspath(path)


def test_guard_rejects_empty_string():
    with pytest.raises(ValueError):
        guard_data_dir("   ")


def test_is_repository_root_recognizes_project_root():
    assert is_repository_root(REPO_ROOT) is True
    assert is_repository_root(os.path.join(REPO_ROOT, "classroom-data")) is False


def test_resolve_data_dir_default_is_pilot():
    assert resolve_data_dir(REPO_ROOT, explicit=None, profile=None).endswith(
        "classroom-data"
    )


def test_resolve_data_dir_test_profile_is_separate():
    resolved = resolve_data_dir(REPO_ROOT, explicit=None, profile="test")
    assert resolved.endswith("data-test")
    assert resolved != resolve_data_dir(REPO_ROOT, explicit=None, profile="pilot")


def test_resolve_data_dir_explicit_wins():
    assert resolve_data_dir(REPO_ROOT, explicit="/tmp/foo", profile="test") == os.path.abspath(
        "/tmp/foo"
    )


def test_workspace_cannot_use_repository_root(tmp_path):
    """在仓库根上构造 Workspace 会被守卫拦下, 避免就地污染源码树。"""
    with pytest.raises(ConfigurationError):
        Workspace(REPO_ROOT)


def test_workspace_uses_tmp_path_isolated(tmp_path):
    """测试套件用 tmp_path 时一切正常 (隔离策略不破坏正常用例)。"""
    ws = Workspace(
        str(tmp_path / "data"),
        clock=fixed_clock("2026-01-01T00:00:00+00:00"),
        asr_mode="mock",
        ocr_mode="mock",
    )
    try:
        course = ws.create_course("Isolation Course", language="es")
        assert course["course_id"]
        # 数据落在 tmp_path 下, 不触碰仓库根。
        assert ws.data_dir.startswith(str(tmp_path))
    finally:
        ws.close()


def test_default_data_dir_does_not_resolve_inside_source_tree(monkeypatch):
    """默认画像解析出的数据目录绝不在 src / tests 里。"""
    import src.application.config as config

    monkeypatch.delenv("CLASSROOM_DATA_DIR", raising=False)
    monkeypatch.delenv("CLASSROOM_DATA_PROFILE", raising=False)
    # 即使 cwd 恰好是仓库根, 默认仍是 classroom-data 子目录, 而非根本身。
    monkeypatch.setattr(config.os, "getcwd", lambda: REPO_ROOT)
    resolved = config.default_data_dir_name()
    assert resolved == "classroom-data"
