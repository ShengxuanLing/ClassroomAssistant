# -*- coding: utf-8 -*-
"""备份 / 恢复层的结构化错误 (Task 43)。

与 :mod:`src.persistence.errors` 同样的约定: 错误码**刻意**按前缀命名,
使 ``src/application/errors.py::map_application_error`` 无需任何胶水代码
就能把备份异常映射成规范要求的 8 个用户可见错误码之一。

======================  ==================
备份 code                映射结果
======================  ==================
``INVALID_*``            ``INVALID_INPUT``
``*_NOT_FOUND``          ``NOT_FOUND``
``DUPLICATE_*``          ``CONFLICT``
``STORAGE_*``            ``STORAGE_ERROR``
======================  ==================

一个容易踩的坑: 映射函数**先**判断 ``startswith("INVALID")``, 再判断
``endswith("NOT_FOUND")``。因此不要造出 ``INVALID_..._NOT_FOUND`` 这种
两头都沾的码 —— 它会静默变成 ``INVALID_INPUT``。测试里有一条
``test_every_backup_error_code_maps_to_the_expected_http_code`` 盯着这件事。

语义上还有一条重要区分:
- **调用方传错了参数** -> ``INVALID_INPUT``
- **备份文件本身不可信** (损坏 / 校验和不符 / 路径穿越 / 超大) ->
  ``STORAGE_*``。这不是"用户输入非法", 而是"这份归档不能信"。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Optional

__all__ = [
    "BackupErrorCode",
    "BackupError",
    "BackupValidationError",
    "BackupNotFoundError",
    "DuplicateBackupError",
    "CorruptedBackupError",
    "ManifestError",
    "ChecksumMismatchError",
    "UnsafeArchivePathError",
    "ArchiveTooLargeError",
    "MissingArchiveEntryError",
    "DatabaseInUseError",
    "RestoreError",
    "UnsupportedBackupVersionError",
]


class BackupErrorCode(str, Enum):
    """备份层错误码 (前缀决定 ``map_application_error`` 的结果)。"""

    # -> INVALID_INPUT
    INVALID_INPUT = "INVALID_INPUT"
    INVALID_BACKUP_VERSION = "INVALID_BACKUP_VERSION"

    # -> NOT_FOUND
    BACKUP_NOT_FOUND = "BACKUP_NOT_FOUND"

    # -> CONFLICT
    DUPLICATE_BACKUP = "DUPLICATE_BACKUP"

    # -> STORAGE_ERROR
    STORAGE_ERROR = "STORAGE_ERROR"
    STORAGE_BACKUP_CORRUPTED = "STORAGE_BACKUP_CORRUPTED"
    STORAGE_BACKUP_MANIFEST_INVALID = "STORAGE_BACKUP_MANIFEST_INVALID"
    STORAGE_BACKUP_CHECKSUM_MISMATCH = "STORAGE_BACKUP_CHECKSUM_MISMATCH"
    STORAGE_BACKUP_UNSAFE_PATH = "STORAGE_BACKUP_UNSAFE_PATH"
    STORAGE_BACKUP_TOO_LARGE = "STORAGE_BACKUP_TOO_LARGE"
    STORAGE_BACKUP_MISSING_ENTRY = "STORAGE_BACKUP_MISSING_ENTRY"
    STORAGE_DATABASE_IN_USE = "STORAGE_DATABASE_IN_USE"
    STORAGE_RESTORE_FAILED = "STORAGE_RESTORE_FAILED"


class BackupError(Exception):
    """所有备份层错误的基类。

    携带稳定的 ``code``; ``detail`` 必须可 JSON 序列化且**不含敏感数据**
    (归档里可能有课堂原文, 绝不进错误详情)。
    """

    code: str = BackupErrorCode.STORAGE_ERROR.value

    def __init__(
        self,
        message: str,
        *,
        code: Any = None,
        detail: Optional[Mapping[str, Any]] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        if code is not None:
            self.code = code.value if isinstance(code, Enum) else str(code)
        self.message = str(message)
        self.detail = None if detail is None else dict(detail)
        self.cause = cause
        super().__init__(f"[{self.code}] {self.message}")

    @property
    def error_code(self) -> str:
        return self.code

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail:
            out["detail"] = dict(self.detail)
        return out

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code}] {self.message}"


class BackupValidationError(BackupError):
    """调用方参数非法 (空路径 / 非法 label / 目标已存在)。-> INVALID_INPUT"""

    code = BackupErrorCode.INVALID_INPUT.value


class UnsupportedBackupVersionError(BackupError):
    """归档的 ``backup_version`` 高于本代码支持的版本。-> INVALID_INPUT"""

    code = BackupErrorCode.INVALID_BACKUP_VERSION.value


class BackupNotFoundError(BackupError):
    """归档文件不存在。-> NOT_FOUND"""

    code = BackupErrorCode.BACKUP_NOT_FOUND.value


class DuplicateBackupError(BackupError):
    """同一标识的归档已存在 (不静默覆盖)。-> CONFLICT"""

    code = BackupErrorCode.DUPLICATE_BACKUP.value


class CorruptedBackupError(BackupError):
    """zip 结构损坏 / 无法作为 zip 打开。-> STORAGE_ERROR"""

    code = BackupErrorCode.STORAGE_BACKUP_CORRUPTED.value


class ManifestError(BackupError):
    """manifest.json 缺失、非法 JSON、字段缺失或类型错误。-> STORAGE_ERROR"""

    code = BackupErrorCode.STORAGE_BACKUP_MANIFEST_INVALID.value


class ChecksumMismatchError(BackupError):
    """数据库或材料文件的 sha256 与 manifest 记录不符。-> STORAGE_ERROR"""

    code = BackupErrorCode.STORAGE_BACKUP_CHECKSUM_MISMATCH.value


class UnsafeArchivePathError(BackupError):
    """归档条目名逃逸出目标目录 (``..`` / 绝对路径 / 盘符 / 符号链接)。

    -> STORAGE_ERROR
    """

    code = BackupErrorCode.STORAGE_BACKUP_UNSAFE_PATH.value


class ArchiveTooLargeError(BackupError):
    """归档解压后的体积或条目数超出上限 (zip 炸弹防护)。-> STORAGE_ERROR"""

    code = BackupErrorCode.STORAGE_BACKUP_TOO_LARGE.value


class MissingArchiveEntryError(BackupError):
    """manifest 声明了某个文件, 归档里却没有。-> STORAGE_ERROR"""

    code = BackupErrorCode.STORAGE_BACKUP_MISSING_ENTRY.value


class DatabaseInUseError(BackupError):
    """恢复时活库 (或材料目录里的文件) 仍被打开, 无法原子替换。

    这是 **Windows 特有的真实约束**: 只要还有任何句柄持有文件, ``os.replace``
    就报 ``WinError 5 (拒绝访问)``。恢复是**离线操作**, 因此这里给出一条
    可执行的提示, 而不是把裸 ``PermissionError`` 抛给上层。

    重要: 这个错误抛出时**原数据一定没被改动** —— 替换数据库是恢复流程里
    第一个破坏性步骤, 它失败就意味着后面一步都没做。

    -> STORAGE_ERROR
    """

    code = BackupErrorCode.STORAGE_DATABASE_IN_USE.value


class RestoreError(BackupError):
    """恢复失败 (此时原数据必须保持完好)。-> STORAGE_ERROR"""

    code = BackupErrorCode.STORAGE_RESTORE_FAILED.value
