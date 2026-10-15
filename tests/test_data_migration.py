# -*- coding: utf-8 -*-
"""品牌改名 (Classroom Assistant -> Loomind) 的数据迁移。

这个模块是**唯一会移动用户既有数据**的地方, 所以每条"不动手"的分支都要有
测试钉住: 目标已有数据不覆盖、旧目录不存在就跳过、失败不抛异常打断启动。
"""

from __future__ import annotations

import os

import pytest

from src.application.data_migration import (
    STATUS_FAILED,
    STATUS_MIGRATED,
    STATUS_SKIPPED,
    migrate_legacy_data_dir,
)


def _make_legacy(root: str, *, database: bool = True) -> str:
    """造一个最小可辨认的旧布局。"""
    legacy = os.path.join(root, "classroom-data")
    os.makedirs(os.path.join(legacy, "database"), exist_ok=True)
    os.makedirs(os.path.join(legacy, "materials"), exist_ok=True)
    if database:
        with open(os.path.join(legacy, "database", "classroom.sqlite"), "w") as handle:
            handle.write("sqlite")
        with open(os.path.join(legacy, "database", "classroom.sqlite-wal"), "w") as handle:
            handle.write("wal")
        with open(os.path.join(legacy, "database", "classroom.sqlite-shm"), "w") as handle:
            handle.write("shm")
    return legacy


def test_moves_the_legacy_directory_and_renames_the_database(tmp_path):
    legacy = _make_legacy(str(tmp_path))
    target = os.path.join(str(tmp_path), "loomind-data")

    result = migrate_legacy_data_dir(target, legacy=legacy)

    assert result.status == STATUS_MIGRATED
    assert result.changed is True
    assert result.renamed_database is True
    assert not os.path.exists(legacy)
    database_dir = os.path.join(target, "database")
    assert sorted(os.listdir(database_dir)) == [
        "loomind.sqlite",
        "loomind.sqlite-shm",
        "loomind.sqlite-wal",
    ]


def test_is_idempotent(tmp_path):
    legacy = _make_legacy(str(tmp_path))
    target = os.path.join(str(tmp_path), "loomind-data")

    assert migrate_legacy_data_dir(target, legacy=legacy).status == STATUS_MIGRATED
    second = migrate_legacy_data_dir(target, legacy=legacy)

    assert second.status == STATUS_SKIPPED
    assert second.changed is False


def test_skips_when_the_legacy_directory_does_not_exist(tmp_path):
    result = migrate_legacy_data_dir(
        os.path.join(str(tmp_path), "loomind-data"),
        legacy=os.path.join(str(tmp_path), "classroom-data"),
    )
    assert result.status == STATUS_SKIPPED
    assert result.changed is False


def test_never_overwrites_a_target_that_already_has_data(tmp_path):
    """目标已有真实数据时两边都不动 —— 覆盖等于毁数据。"""
    legacy = _make_legacy(str(tmp_path))
    target = os.path.join(str(tmp_path), "loomind-data")
    os.makedirs(os.path.join(target, "database"))
    with open(os.path.join(target, "database", "loomind.sqlite"), "w") as handle:
        handle.write("current")

    result = migrate_legacy_data_dir(target, legacy=legacy)

    assert result.status == STATUS_SKIPPED
    assert os.path.isdir(legacy)
    with open(os.path.join(target, "database", "loomind.sqlite")) as handle:
        assert handle.read() == "current"


def test_replaces_an_empty_layout_skeleton(tmp_path):
    """``ensure_data_layout`` 可能已经建出 8 个空目录 —— 那不算"已有数据"。"""
    from src.application.data_dirs import DATA_LAYOUT_DIRS

    legacy = _make_legacy(str(tmp_path))
    target = os.path.join(str(tmp_path), "loomind-data")
    for name in DATA_LAYOUT_DIRS:
        os.makedirs(os.path.join(target, name), exist_ok=True)

    result = migrate_legacy_data_dir(target, legacy=legacy)

    assert result.status == STATUS_MIGRATED
    assert os.path.isfile(os.path.join(target, "database", "loomind.sqlite"))


def test_skips_when_legacy_and_target_are_the_same_path(tmp_path):
    legacy = _make_legacy(str(tmp_path))
    result = migrate_legacy_data_dir(legacy, legacy=legacy)
    assert result.status == STATUS_SKIPPED
    assert os.path.isdir(legacy)


def test_migrates_even_without_a_database_file(tmp_path):
    legacy = _make_legacy(str(tmp_path), database=False)
    target = os.path.join(str(tmp_path), "loomind-data")

    result = migrate_legacy_data_dir(target, legacy=legacy)

    assert result.status == STATUS_MIGRATED
    assert result.renamed_database is False


def test_empty_arguments_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        migrate_legacy_data_dir("", legacy=str(tmp_path))
    with pytest.raises(ValueError):
        migrate_legacy_data_dir(str(tmp_path), legacy="   ")


def test_failure_returns_a_result_instead_of_raising(tmp_path, monkeypatch):
    """搬迁失败只回报 STATUS_FAILED —— 起不来比少一次便利严重得多。"""
    import src.application.data_migration as module

    legacy = _make_legacy(str(tmp_path))
    target = os.path.join(str(tmp_path), "loomind-data")

    def boom(*args, **kwargs):
        raise OSError("simulated failure")

    monkeypatch.setattr(module.os, "rename", boom)
    monkeypatch.setattr(module.shutil, "copytree", boom)

    result = migrate_legacy_data_dir(target, legacy=legacy)

    assert result.status == STATUS_FAILED
    assert result.changed is False
    assert os.path.isdir(legacy)
