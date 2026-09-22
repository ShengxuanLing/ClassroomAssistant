# -*- coding: utf-8 -*-
"""备份清单 (manifest.json) 的定义与校验 (Task 43)。

规范要求 manifest 至少包含::

    backup_version
    schema_version
    created_at
    application_version
    file_count
    database_checksum

设计取舍
--------------------------------------------------------------------
- **``file_count`` 的精确定义**: 归档内**除 ``manifest.json`` 以外**的文件数
  (即 ``database.sqlite`` + 全部材料文件)。清单不能把自己算进去 —— 否则
  数字会依赖"清单本身存不存在"这种自指关系。有测试固定这个定义。

- **``created_at`` 来自注入的 ``Clock``**, 不就地调用 ``datetime.now()``。
  全项目的非确定性来源只能有一处 (``src.application.runtime``), 备份清单
  也不例外 —— 否则备份文件就无法在测试里做确定性断言。

- **``database_checksum`` 是 ``database.sqlite`` 的 sha256**, 逐字节校验。
  这是"归档没被改过"的唯一硬证据; 只检查"文件存在"是没用的。

- **材料文件逐个记录 ``sha256`` 与 ``size``**。规范只要求数据库校验和,
  但材料文件同样是用户数据: 只记数量会让"某个音频被替换"无法被发现。

- **``config`` 里不放绝对路径**。绝对路径是机器相关的 (而且会泄露用户名),
  恢复时目标目录由调用方给出。这里只记录**重建所需的结构信息**:
  目录布局、数据库文件名、被备份/被排除的目录。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from src.backup.errors import (
    ManifestError,
    UnsupportedBackupVersionError,
)
from src.persistence.models.codec import canonical_json

__all__ = [
    "BACKUP_VERSION",
    "MAX_SUPPORTED_BACKUP_VERSION",
    "MANIFEST_ENTRY_NAME",
    "DATABASE_ENTRY_NAME",
    "ManifestFile",
    "BackupManifest",
    "build_manifest",
    "manifest_to_bytes",
    "manifest_from_bytes",
    "REQUIRED_MANIFEST_FIELDS",
]

#: 本代码写出的备份格式版本。
BACKUP_VERSION = 1

#: 本代码能**读取**的最高备份格式版本。更高 -> 拒绝, 绝不猜着读。
MAX_SUPPORTED_BACKUP_VERSION = 1

#: 归档内清单文件名 (固定, 便于工具链识别)。
MANIFEST_ENTRY_NAME = "manifest.json"

#: 归档内数据库文件名 (规范点名要求就叫这个名字)。
DATABASE_ENTRY_NAME = "database.sqlite"

#: 规范点名要求的 manifest 字段 —— 缺任何一个都必须拒绝。
REQUIRED_MANIFEST_FIELDS: tuple[str, ...] = (
    "backup_version",
    "schema_version",
    "created_at",
    "application_version",
    "file_count",
    "database_checksum",
)


@dataclass(frozen=True)
class ManifestFile:
    """归档内一个非数据库文件 (材料) 的记录。"""

    path: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "size": int(self.size), "sha256": self.sha256}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, index: int = 0) -> "ManifestFile":
        if not isinstance(data, Mapping):
            raise ManifestError(
                f"material_files[{index}] must be an object, got {type(data).__name__}"
            )
        path = data.get("path")
        digest = data.get("sha256")
        size = data.get("size")
        if not isinstance(path, str) or not path:
            raise ManifestError(f"material_files[{index}].path must be a non-empty string")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ManifestError(
                f"material_files[{index}].sha256 must be a 64-char hex digest"
            )
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ManifestError(f"material_files[{index}].size must be a non-negative int")
        return cls(path=path, size=size, sha256=digest)


@dataclass(frozen=True)
class BackupManifest:
    """一份备份的自我描述。"""

    backup_version: int
    schema_version: int
    created_at: str
    application_version: str
    file_count: int
    database_checksum: str

    # 以下为增强字段 (规范未强制, 但对"可验证 + 可重建"是必需的)
    database_size: int = 0
    total_bytes: int = 0
    database_filename: str = ""
    label: Optional[str] = None
    material_files: tuple[ManifestFile, ...] = ()
    config: Mapping[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------

    @property
    def material_file_count(self) -> int:
        return len(self.material_files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_version": int(self.backup_version),
            "schema_version": int(self.schema_version),
            "created_at": str(self.created_at),
            "application_version": str(self.application_version),
            "file_count": int(self.file_count),
            "database_checksum": str(self.database_checksum),
            "database_size": int(self.database_size),
            "total_bytes": int(self.total_bytes),
            "database_filename": str(self.database_filename),
            "label": self.label,
            "material_files": [f.to_dict() for f in self.material_files],
            "config": dict(self.config),
        }

    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BackupManifest":
        """解析并**严格校验**清单。任何问题都抛结构化错误, 绝不猜测。"""
        if not isinstance(data, Mapping):
            raise ManifestError(
                f"{MANIFEST_ENTRY_NAME} must decode to an object, "
                f"got {type(data).__name__}"
            )

        missing = [name for name in REQUIRED_MANIFEST_FIELDS if name not in data]
        if missing:
            raise ManifestError(
                f"{MANIFEST_ENTRY_NAME} is missing required field(s): {missing}"
            )

        version = _require_int(data, "backup_version", minimum=1)
        if version > MAX_SUPPORTED_BACKUP_VERSION:
            raise UnsupportedBackupVersionError(
                f"backup_version {version} is newer than this build supports "
                f"({MAX_SUPPORTED_BACKUP_VERSION})"
            )

        schema_version = _require_int(data, "schema_version", minimum=0)
        created_at = _require_str(data, "created_at")
        application_version = _require_str(data, "application_version")
        file_count = _require_int(data, "file_count", minimum=0)
        checksum = _require_str(data, "database_checksum")
        if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
            raise ManifestError(
                "database_checksum must be a 64-char lowercase hex sha256 digest"
            )

        raw_files = data.get("material_files") or []
        if not isinstance(raw_files, (list, tuple)):
            raise ManifestError("material_files must be a list")
        material_files = tuple(
            ManifestFile.from_dict(entry, index=i) for i, entry in enumerate(raw_files)
        )

        label = data.get("label")
        if label is not None and not isinstance(label, str):
            raise ManifestError("label must be a string or null")

        config = data.get("config") or {}
        if not isinstance(config, Mapping):
            raise ManifestError("config must be an object")

        return cls(
            backup_version=version,
            schema_version=schema_version,
            created_at=created_at,
            application_version=application_version,
            file_count=file_count,
            database_checksum=checksum,
            database_size=_optional_int(data, "database_size"),
            total_bytes=_optional_int(data, "total_bytes"),
            database_filename=str(data.get("database_filename") or ""),
            label=label,
            material_files=material_files,
            config=dict(config),
        )

    def validate_self_consistency(self) -> None:
        """清单内部必须自洽 (文件数与实际记录一致)。"""
        expected = 1 + self.material_file_count  # 数据库 + 材料
        if self.file_count != expected:
            raise ManifestError(
                f"file_count {self.file_count} does not match the archive contents "
                f"(expected {expected} = 1 database + {self.material_file_count} material files)"
            )
        if self.material_file_count and self.total_bytes <= 0:
            raise ManifestError("total_bytes must be positive when material files exist")


# ----------------------------------------------------------------------
# 构造
# ----------------------------------------------------------------------


def build_manifest(
    *,
    schema_version: int,
    created_at: str,
    application_version: str,
    database_checksum: str,
    database_size: int,
    database_filename: str,
    material_files: Sequence[ManifestFile] = (),
    config: Optional[Mapping[str, Any]] = None,
    label: Optional[str] = None,
) -> BackupManifest:
    """构造一份清单。

    ``file_count`` / ``total_bytes`` 由这里**算出来**, 不接受调用方传入 ——
    数字与实际内容不可能不一致。
    """
    files = tuple(material_files)
    total = int(database_size) + sum(f.size for f in files)
    manifest = BackupManifest(
        backup_version=BACKUP_VERSION,
        schema_version=int(schema_version),
        created_at=str(created_at),
        application_version=str(application_version),
        file_count=1 + len(files),
        database_checksum=str(database_checksum),
        database_size=int(database_size),
        total_bytes=total,
        database_filename=str(database_filename),
        label=label,
        material_files=files,
        config=dict(config or {}),
    )
    manifest.validate_self_consistency()
    return manifest


# ----------------------------------------------------------------------
# 序列化
# ----------------------------------------------------------------------


def manifest_to_bytes(manifest: BackupManifest) -> bytes:
    """清单 -> 规范化 JSON 字节 (确定性: 键排序 + 不转义非 ASCII)。"""
    return canonical_json(manifest.to_dict()).encode("utf-8")


def manifest_from_bytes(payload: bytes) -> BackupManifest:
    """清单字节 -> :class:`BackupManifest`。非 JSON / 非对象都拒绝。"""
    if isinstance(payload, (bytearray, memoryview)):
        payload = bytes(payload)
    if not isinstance(payload, bytes):
        raise ManifestError(f"manifest payload must be bytes, got {type(payload).__name__}")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError(f"{MANIFEST_ENTRY_NAME} is not valid UTF-8", cause=exc) from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(
            f"{MANIFEST_ENTRY_NAME} is not valid JSON: {exc}", cause=exc
        ) from exc
    return BackupManifest.from_dict(value)


# ----------------------------------------------------------------------
# 字段读取辅助 (错误信息统一)
# ----------------------------------------------------------------------


def _require_int(data: Mapping[str, Any], name: str, *, minimum: int) -> int:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError(f"{name} must be an integer, got {type(value).__name__}")
    if value < minimum:
        raise ManifestError(f"{name} must be >= {minimum}, got {value}")
    return value


def _optional_int(data: Mapping[str, Any], name: str) -> int:
    value = data.get(name, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _require_str(data: Mapping[str, Any], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{name} must be a non-empty string")
    return value
