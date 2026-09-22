# -*- coding: utf-8 -*-
"""zip 归档的读写与安全校验 (Task 43)。

规范要求恢复前必须验证::

    zip integrity
    manifest
    database checksum
    expected paths
    no path traversal

本模块负责其中与 zip 结构有关的部分; 校验和与清单语义在
:mod:`src.backup.manifest` 与 :mod:`src.backup.service`。

威胁模型 (为什么这些检查一个都不能省)
--------------------------------------------------------------------
备份文件是**可以被用户搬运、编辑、从别处拷来的**, 因此必须当成**不可信
输入**处理。具体防的是:

1. **路径穿越** —— 条目名写成 ``../../etc/passwd`` 或 ``C:\\Windows\\...``,
   解压时写到目标目录之外。这类攻击在 zip 里极其常见。
2. **符号链接条目** —— 归档里塞一个符号链接, 解压后后续条目顺着链接写到
   目录外。名字看起来完全正常, 所以必须单独检查 Unix mode 位。
3. **重复条目名** —— 两个同名条目, 校验时读到一个、解压时写入另一个
   (校验与使用看到不同的数据)。一律拒绝。
4. **zip 炸弹** —— 声明体积很小, 解压后几百 GB。用**解压后总字节数 +
   条目数 + 单文件压缩比**三重上限拦。
5. **CRC 损坏** —— 归档在传输中坏了。``testzip()`` 逐个校验 CRC。

所有拒绝都抛结构化错误 (``STORAGE_BACKUP_*``), 绝不静默跳过。
"""

from __future__ import annotations

import os
import stat
import zipfile
from contextlib import contextmanager
from typing import Iterable, Iterator, Optional, Sequence

from src.application.data_dirs import contains_traversal, safe_join
from src.backup.errors import (
    ArchiveTooLargeError,
    CorruptedBackupError,
    MissingArchiveEntryError,
    UnsafeArchivePathError,
)
from src.backup.manifest import MANIFEST_ENTRY_NAME

__all__ = [
    "MAX_ARCHIVE_ENTRIES",
    "MAX_UNCOMPRESSED_BYTES",
    "MAX_COMPRESSION_RATIO",
    "is_safe_archive_name",
    "check_entry_names",
    "check_archive_limits",
    "is_symlink_entry",
    "open_archive",
    "read_entry_bytes",
    "read_manifest_bytes",
    "verify_zip_integrity",
    "extract_to",
]

#: 归档条目数上限 (正常课堂数据远低于此)。
MAX_ARCHIVE_ENTRIES = 200_000

#: 解压后总字节数上限 (默认 8 GiB)。
MAX_UNCOMPRESSED_BYTES = 8 * 1024**3

#: 单个条目的最大压缩比 (zip 炸弹的典型特征是极高压缩比)。
MAX_COMPRESSION_RATIO = 2000


# ----------------------------------------------------------------------
# 条目名校验
# ----------------------------------------------------------------------


def is_safe_archive_name(name: str) -> bool:
    """归档条目名是否安全 (相对、无 ``..``、无盘符、无绝对路径)。

    同时拒绝反斜杠: zip 规范用 ``/`` 作分隔符, 出现 ``\\`` 说明写入方
    要么是坏工具、要么是在利用"Windows 把 ``\\`` 当分隔符"这一点。
    """
    if not isinstance(name, str) or not name:
        return False
    if "\\" in name:
        return False
    if name.startswith("/") or name.startswith("~"):
        return False
    if len(name) > 1 and name[1] == ":":  # C: / D:
        return False
    if any(ord(ch) < 32 for ch in name):
        return False
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    if contains_traversal(name):
        return False
    # 归一化后必须仍等于原值 (挡住 a/./b、a//b 这类混淆)
    normalised = os.path.normpath(name).replace(os.sep, "/")
    return normalised == name


def check_entry_names(names: Iterable[str]) -> tuple[str, ...]:
    """逐个校验条目名, 并拒绝重复。返回去重后的有序元组。"""
    checked: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name.endswith("/"):
            # 目录条目: 去掉尾斜杠后按同样规则校验
            directory = name[:-1]
            if not directory:
                raise UnsafeArchivePathError(
                    f"archive contains a root directory entry: {name!r}"
                )
            if not is_safe_archive_name(directory):
                raise UnsafeArchivePathError(
                    f"archive contains an unsafe directory entry: {name!r}",
                    detail={"entry": name},
                )
            continue
        if not is_safe_archive_name(name):
            raise UnsafeArchivePathError(
                f"archive contains an unsafe entry name: {name!r}",
                detail={"entry": name},
            )
        if name in seen:
            raise UnsafeArchivePathError(
                f"archive contains duplicate entry: {name!r} "
                "(validation and extraction could disagree)",
                detail={"entry": name},
            )
        seen.add(name)
        checked.append(name)
    return tuple(checked)


def is_symlink_entry(info: zipfile.ZipInfo) -> bool:
    """条目是否是符号链接 (看 Unix mode 位)。"""
    mode = info.external_attr >> 16
    if mode == 0:
        return False
    return stat.S_ISLNK(mode)


def check_archive_limits(
    archive: zipfile.ZipFile,
    *,
    max_entries: int = MAX_ARCHIVE_ENTRIES,
    max_uncompressed_bytes: int = MAX_UNCOMPRESSED_BYTES,
    max_compression_ratio: int = MAX_COMPRESSION_RATIO,
) -> int:
    """zip 炸弹防护。返回解压后的总字节数。"""
    infos = archive.infolist()
    if len(infos) > max_entries:
        raise ArchiveTooLargeError(
            f"archive has {len(infos)} entries, limit is {max_entries}",
            detail={"entries": len(infos), "limit": max_entries},
        )

    total = 0
    for info in infos:
        size = int(info.file_size)
        compressed = int(info.compress_size)
        total += size
        if size > max_uncompressed_bytes:
            raise ArchiveTooLargeError(
                f"entry {info.filename!r} expands to {size} bytes, "
                f"limit is {max_uncompressed_bytes}",
                detail={"entry": info.filename, "size": size},
            )
        if compressed > 0 and size / compressed > max_compression_ratio:
            raise ArchiveTooLargeError(
                f"entry {info.filename!r} has compression ratio "
                f"{size / compressed:.0f}:1, limit is {max_compression_ratio}:1",
                detail={"entry": info.filename},
            )
    if total > max_uncompressed_bytes:
        raise ArchiveTooLargeError(
            f"archive expands to {total} bytes, limit is {max_uncompressed_bytes}",
            detail={"total_bytes": total, "limit": max_uncompressed_bytes},
        )
    return total


# ----------------------------------------------------------------------
# 打开 / 读取
# ----------------------------------------------------------------------


@contextmanager
def open_archive(
    path: str,
    *,
    check_integrity: bool = True,
    check_limits: bool = True,
) -> Iterator[zipfile.ZipFile]:
    """打开并**校验结构**的 zip 归档。

    校验顺序 (便宜的在前, 让明显损坏的文件快速失败):
    1. 能作为 zip 打开;
    2. 条目名安全且不重复;
    3. 无符号链接条目;
    4. 解压体积 / 条目数 / 压缩比在限内;
    5. 逐个 CRC 校验 (``testzip``)。
    """
    if not isinstance(path, str) or not path.strip():
        raise CorruptedBackupError("archive path must be a non-empty string")
    if not os.path.isfile(path):
        raise CorruptedBackupError(f"archive not found: {path!r}")

    try:
        archive = zipfile.ZipFile(path, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise CorruptedBackupError(
            f"not a readable zip archive: {path!r} ({exc})", cause=exc
        ) from exc

    try:
        names = check_entry_names(archive.namelist())
        for info in archive.infolist():
            if is_symlink_entry(info):
                raise UnsafeArchivePathError(
                    f"archive contains a symbolic link entry: {info.filename!r}",
                    detail={"entry": info.filename},
                )
        if check_limits:
            check_archive_limits(archive)
        if check_integrity:
            verify_zip_integrity(archive, path)
        yield archive
    finally:
        archive.close()


def verify_zip_integrity(archive: zipfile.ZipFile, path: str = "") -> None:
    """逐条目 CRC 校验。``testzip()`` 返回第一个坏条目的名字。"""
    try:
        broken = archive.testzip()
    except (zipfile.BadZipFile, OSError) as exc:
        raise CorruptedBackupError(
            f"archive is damaged: {path!r} ({exc})", cause=exc
        ) from exc
    if broken is not None:
        raise CorruptedBackupError(
            f"archive entry failed CRC check: {broken!r}",
            detail={"entry": broken},
        )


def read_entry_bytes(archive: zipfile.ZipFile, name: str, *, required: bool = True) -> Optional[bytes]:
    """读取一个条目的原始字节。缺失时按 ``required`` 决定抛错还是返回 None。"""
    try:
        info = archive.getinfo(name)
    except KeyError:
        if required:
            raise MissingArchiveEntryError(
                f"archive is missing the required entry {name!r}",
                detail={"entry": name},
            ) from None
        return None
    try:
        return archive.read(info)
    except (zipfile.BadZipFile, OSError) as exc:
        raise CorruptedBackupError(
            f"cannot read entry {name!r}: {exc}", cause=exc
        ) from exc


def read_manifest_bytes(archive: zipfile.ZipFile) -> bytes:
    """读取 manifest.json 的字节 (缺失即错)。"""
    payload = read_entry_bytes(archive, MANIFEST_ENTRY_NAME, required=False)
    if payload is None:
        raise MissingArchiveEntryError(
            f"archive is missing {MANIFEST_ENTRY_NAME!r}",
            detail={"entry": MANIFEST_ENTRY_NAME},
        )
    return payload


# ----------------------------------------------------------------------
# 解压
# ----------------------------------------------------------------------


def extract_to(
    archive: zipfile.ZipFile,
    destination: str,
    *,
    entries: Optional[Sequence[str]] = None,
    prefix: str = "",
) -> list[str]:
    """把归档条目安全解压到 ``destination``。

    - 每个条目都过 :func:`safe_join` (二次防线: 即使条目名校验被绕过,
      拼接结果仍必须落在 ``destination`` 内);
    - ``prefix`` 会被剥掉 (用于把 ``materials/audio/x`` 还原成 ``audio/x``);
    - 返回实际写出的**绝对路径**列表 (确定性顺序)。
    """
    root = os.path.abspath(destination)
    os.makedirs(root, exist_ok=True)

    names = list(entries) if entries is not None else [
        name for name in check_entry_names(archive.namelist())
    ]
    written: list[str] = []
    for name in sorted(names):
        relative = name
        if prefix:
            if not name.startswith(prefix):
                continue
            relative = name[len(prefix) :]
        if not relative:
            continue
        try:
            target = safe_join(root, relative)
        except ValueError as exc:
            # 二次防线: 条目名校验已过, 但拼接结果仍逃出 root -> 拒绝。
            raise UnsafeArchivePathError(
                f"entry {name!r} resolves outside the destination: {exc}",
                detail={"entry": name},
                cause=exc,
            ) from exc
        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:
            with archive.open(name, "r") as source, open(target, "wb") as sink:
                while True:
                    chunk = source.read(65536)
                    if not chunk:
                        break
                    sink.write(chunk)
        except (zipfile.BadZipFile, OSError) as exc:
            raise CorruptedBackupError(
                f"cannot extract entry {name!r}: {exc}", cause=exc
            ) from exc
        written.append(target)
    return written
