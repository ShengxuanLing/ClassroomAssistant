# -*- coding: utf-8 -*-
"""品牌改名 (Classroom Assistant -> Loomind) 的一次性数据迁移。

背景
--------------------------------------------------------------------

v1.1.1 及以前的数据布局是::

    classroom-data/                 <- 数据根
        database/classroom.sqlite   <- 库文件
    %LOCALAPPDATA%\\ClassroomAssistant\\   <- 安装态数据根

改名后是 ``loomind-data`` / ``loomind.sqlite`` / ``%LOCALAPPDATA%\\Loomind``。
不做迁移的后果很具体: 老用户双击新版会打开一个**空库** —— 在他看来就是
"我的课堂数据没了"。所以迁移不是可选项。

迁移语义 (刻意保守)
--------------------------------------------------------------------

- **只在目标位置还没有数据时迁移。** 目标已存在且有内容, 一律不动 —— 用户
  已经在新布局上工作过, 覆盖他等于毁数据。
- 优先 ``os.rename`` (同卷时原子、瞬时)。跨卷 / 权限失败时回落
  ``shutil.copytree``, 复制成功后**保留原目录**并明确告警: 宁可留一份冗余,
  也不要在复制没被验证之前删掉用户唯一的数据。
- 数据库文件名同步 ``classroom.sqlite`` -> ``loomind.sqlite``, 且 ``-wal`` /
  ``-shm`` 一并改名 —— 本库跑 WAL, 只搬主库文件会丢最近提交的事务。
- **失败不抛异常打断启动。** 返回结果对象 + 写日志, 由调用方决定怎么提示。
  迁移没做成是"少了一次便利", 起不来是"应用坏了", 两者严重程度差一个量级。

与 `ensure_data_layout` 的关系
--------------------------------------------------------------------

本函数必须在 ``ensure_data_layout`` **之前**调用: 后者会预先建出 8 个空目录,
于是 ``os.rename`` 的目标已存在。这里仍容忍"只剩空骨架的目标目录" (删掉骨架
再搬), 但正确的接线顺序是不给它这个机会。
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from typing import Optional

from src.application.config import LEGACY_DATABASE_FILENAME, DEFAULT_DATABASE_FILENAME
from src.application.data_dirs import DATA_LAYOUT_DIRS

__all__ = [
    "MigrationResult",
    "STATUS_MIGRATED",
    "STATUS_COPIED",
    "STATUS_SKIPPED",
    "STATUS_FAILED",
    "migrate_legacy_data_dir",
]

#: 原目录被 ``os.rename`` 整体搬到新位置 (干净、无冗余)。
STATUS_MIGRATED = "migrated"
#: 跨卷等导致 rename 失败, 退化成复制; **原目录保留**。
STATUS_COPIED = "copied"
#: 不需要迁移 (旧目录不存在 / 新目录已有数据 / 路径相同)。
STATUS_SKIPPED = "skipped"
#: 迁移尝试失败。调用方应提示用户, 但不阻止启动。
STATUS_FAILED = "failed"

#: 数据库文件的三件套后缀 (WAL 模式下主库之外的同伴文件)。
_DB_SIDECAR_SUFFIXES: tuple[str, ...] = ("", "-wal", "-shm")


@dataclass(frozen=True)
class MigrationResult:
    """一次迁移尝试的结果 (可安全地记日志 / 放进 notes)。"""

    status: str
    legacy: str
    target: str
    message: str
    #: 是否真的改动了磁盘 (migrated / copied 为 True)。
    changed: bool = False
    #: 是否顺带把库文件名从旧名改成了新名。
    renamed_database: bool = False


def _log(logger: Optional[logging.Logger], level: int, event: str, message: str) -> None:
    if logger is None:
        return
    try:
        logger.log(level, "%s: %s", event, message)
    except Exception:  # pragma: no cover - 日志绝不该成为失败源
        pass


def _same_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def _is_empty_skeleton(path: str) -> bool:
    """``path`` 是否只是 ``ensure_data_layout`` 建出的空骨架 (8 个空目录)。

    只有这种"目录在、但一条数据都没有"的目标才允许被搬进来的数据取代。
    任何别的非空内容都视为用户的真实数据, 一律不碰。
    """
    if not os.path.isdir(path):
        return False
    try:
        entries = os.listdir(path)
    except OSError:
        return False
    for name in entries:
        full = os.path.join(path, name)
        if not os.path.isdir(full):
            return False
        if name not in DATA_LAYOUT_DIRS:
            return False
        try:
            if os.listdir(full):
                return False
        except OSError:
            return False
    return True


def _rename_database(target_root: str) -> bool:
    """把 ``database/classroom.sqlite`` 改名为 ``loomind.sqlite`` (含 -wal/-shm)。

    返回是否真的改名了。主库不存在时返回 False (空库 / 还没建过库)。
    """
    if LEGACY_DATABASE_FILENAME == DEFAULT_DATABASE_FILENAME:
        return False
    database_dir = os.path.join(target_root, "database")
    legacy_stem = os.path.splitext(LEGACY_DATABASE_FILENAME)[0]
    new_stem = os.path.splitext(DEFAULT_DATABASE_FILENAME)[0]
    extension = os.path.splitext(DEFAULT_DATABASE_FILENAME)[1]

    changed = False
    for suffix in _DB_SIDECAR_SUFFIXES:
        source = os.path.join(database_dir, legacy_stem + extension + suffix)
        destination = os.path.join(database_dir, new_stem + extension + suffix)
        if not os.path.exists(source):
            continue
        if os.path.exists(destination):
            # 目标已存在: 不覆盖。这种情况理论上不会出现 (目标整体是新搬来的),
            # 真出现说明布局不干净, 保持原样并交给上层告警。
            continue
        os.replace(source, destination)
        changed = changed or suffix == ""
    return changed


def migrate_legacy_data_dir(
    target: str,
    *,
    legacy: str,
    logger: Optional[logging.Logger] = None,
) -> MigrationResult:
    """把 ``legacy`` 的数据搬到 ``target`` (若需要且安全)。

    幂等: 重复调用时第一次之后就只剩 ``skipped``。
    """
    if not isinstance(target, str) or not target.strip():
        raise ValueError("target must be a non-empty string")
    if not isinstance(legacy, str) or not legacy.strip():
        raise ValueError("legacy must be a non-empty string")

    target = os.path.abspath(target)
    legacy = os.path.abspath(legacy)

    if _same_path(target, legacy):
        return MigrationResult(
            STATUS_SKIPPED, legacy, target, "legacy and target are the same path"
        )

    if not os.path.isdir(legacy):
        return MigrationResult(
            STATUS_SKIPPED, legacy, target, "no legacy data directory found"
        )

    if os.path.isdir(target) and not _is_empty_skeleton(target):
        return MigrationResult(
            STATUS_SKIPPED,
            legacy,
            target,
            "target already contains data; leaving both untouched",
        )

    # 目标只是空骨架 -> 先移除, 让 rename 能落地。
    if os.path.isdir(target):
        try:
            shutil.rmtree(target)
        except OSError as exc:
            return MigrationResult(
                STATUS_FAILED,
                legacy,
                target,
                f"could not clear the empty target skeleton: {exc}",
            )

    status = STATUS_MIGRATED
    try:
        os.rename(legacy, target)
        message = f"moved legacy data directory {legacy} -> {target}"
    except OSError as rename_error:
        # 跨卷 / 被占用 / 权限 —— 退化成复制, 并且**保留原目录**。
        try:
            shutil.copytree(legacy, target)
        except OSError as copy_error:
            return MigrationResult(
                STATUS_FAILED,
                legacy,
                target,
                "could not move or copy legacy data "
                f"(rename: {rename_error}; copy: {copy_error})",
            )
        status = STATUS_COPIED
        message = (
            f"copied legacy data directory {legacy} -> {target} "
            "(the original directory was kept; delete it once the new "
            "location is verified)"
        )

    renamed = False
    try:
        renamed = _rename_database(target)
    except OSError as exc:
        # 库改名失败不回滚整个迁移: 目录已经就位, 库文件名后面再修也行,
        # 但要**说清楚**, 不能静默。
        message = f"{message}; database file rename failed: {exc}"

    _log(logger, logging.WARNING, "data_migration", message)
    return MigrationResult(
        status=status,
        legacy=legacy,
        target=target,
        message=message,
        changed=True,
        renamed_database=renamed,
    )
