# -*- coding: utf-8 -*-
"""持久化层的结构化错误 (Task 42)。

设计约束
--------------------------------------------------------------------

``src/application/errors.py::map_application_error`` 依据领域异常的 ``.code``
**前缀/后缀**自动映射到 8 个用户可见错误码:

======================  ==================
持久化 code              映射结果
======================  ==================
``INVALID_*``            ``INVALID_INPUT``
``*_NOT_FOUND``          ``NOT_FOUND``
``DUPLICATE_*``          ``CONFLICT``
``STORAGE_*``            ``STORAGE_ERROR``
======================  ==================

因此本模块的错误码**刻意**按这些前缀命名, 使持久化异常无需任何胶水代码
就能变成规范要求的 8 种结构化错误之一。这不是巧合, 是有意为之 ——
新增错误码时必须维持这个前缀约定, 否则会静默退化成 ``INTERNAL_ERROR``。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Optional

__all__ = [
    "PersistenceErrorCode",
    "PersistenceError",
    "PersistenceValidationError",
    "RecordNotFoundError",
    "DuplicateRecordError",
    "PayloadDecodeError",
    "CorruptedDatabaseError",
    "MigrationError",
    "TransactionError",
    "UnsupportedSchemaVersionError",
]


class PersistenceErrorCode(str, Enum):
    """持久化层错误码。

    命名前缀决定 ``map_application_error`` 的映射结果 (见模块 docstring)。
    """

    # -> INVALID_INPUT
    INVALID_INPUT = "INVALID_INPUT"
    INVALID_SCHEMA_VERSION = "INVALID_SCHEMA_VERSION"

    # -> NOT_FOUND
    RECORD_NOT_FOUND = "RECORD_NOT_FOUND"

    # -> CONFLICT
    DUPLICATE_RECORD = "DUPLICATE_RECORD"

    # -> STORAGE_ERROR
    STORAGE_ERROR = "STORAGE_ERROR"
    STORAGE_CORRUPTED_DATABASE = "STORAGE_CORRUPTED_DATABASE"
    STORAGE_MIGRATION_FAILED = "STORAGE_MIGRATION_FAILED"
    STORAGE_TRANSACTION_FAILED = "STORAGE_TRANSACTION_FAILED"


class PersistenceError(Exception):
    """所有持久化层错误的基类。

    携带稳定的 ``code`` (字符串), 便于上层按语义映射;
    ``detail`` 必须是可 JSON 序列化且**不含敏感数据**的 dict。
    """

    code: str = PersistenceErrorCode.STORAGE_ERROR.value

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


class PersistenceValidationError(PersistenceError):
    """输入非法: 非法的表名/字段/参数/主键。-> INVALID_INPUT"""

    code = PersistenceErrorCode.INVALID_INPUT.value


class RecordNotFoundError(PersistenceError):
    """按主键取记录但不存在。-> NOT_FOUND"""

    code = PersistenceErrorCode.RECORD_NOT_FOUND.value


class DuplicateRecordError(PersistenceError):
    """同一业务身份被以不同内容重复写入。-> CONFLICT"""

    code = PersistenceErrorCode.DUPLICATE_RECORD.value


class CorruptedDatabaseError(PersistenceError):
    """数据库文件损坏 / 不是合法的 SQLite 文件。-> STORAGE_ERROR"""

    code = PersistenceErrorCode.STORAGE_CORRUPTED_DATABASE.value


class PayloadDecodeError(PersistenceError):
    """行的 payload 无法还原成领域对象 (非 JSON / 非对象 / 非法编码)。

    这是**存储完整性**问题, 不是调用方的输入问题, 因此映射到
    ``STORAGE_ERROR`` 而不是 ``INVALID_INPUT``。
    -> STORAGE_ERROR
    """

    code = PersistenceErrorCode.STORAGE_ERROR.value


class MigrationError(PersistenceError):
    """迁移失败 (迁移脚本报错 / 版本链断裂)。-> STORAGE_ERROR"""

    code = PersistenceErrorCode.STORAGE_MIGRATION_FAILED.value


class TransactionError(PersistenceError):
    """事务状态非法 (提交无 begin / 回滚失败 / 嵌套使用错误)。-> STORAGE_ERROR"""

    code = PersistenceErrorCode.STORAGE_TRANSACTION_FAILED.value


class UnsupportedSchemaVersionError(PersistenceError):
    """数据库 schema 版本高于本代码可处理的版本。-> INVALID_INPUT"""

    code = PersistenceErrorCode.INVALID_SCHEMA_VERSION.value
