# -*- coding: utf-8 -*-
"""统一应用层结构化错误定义 (Task 34)。

本模块定义应用服务层对用户/上层 (API/UI) 暴露的标准化错误。

硬性原则:
- 所有用户可见错误必须携带稳定的错误码 (ERROR_CODES 枚举成员之一)。
- 禁止把 Python traceback 直接暴露给最终用户; 开发日志可以记录。
- 所有领域层异常必须映射为结构化 ApplicationError。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

__all__ = [
    "ERROR_CODES",
    "ApplicationError",
    "InvalidInputError",
    "NotFoundError",
    "ConflictError",
    "ProcessingError",
    "UnsupportedError",
    "StorageError",
    "ConfigurationError",
    "InternalError",
    "PortInUseError",
    "map_application_error",
]


class ERROR_CODES(str, Enum):
    """8 种用户可见错误码 (Task 34 硬性规范)。"""

    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    PROCESSING_ERROR = "PROCESSING_ERROR"
    UNSUPPORTED = "UNSUPPORTED"
    STORAGE_ERROR = "STORAGE_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class ApplicationError(Exception):
    """应用层结构化错误基类。

    携带:
    - code: 稳定的错误码
    - message: 用户可读的错误描述 (不包含 traceback)
    - detail: 可选的附加诊断信息 (dict, 可 JSON 序列化, 不含敏感数据)
    - cause: 原始异常 (仅开发日志用; 上层展示时不暴露)
    """

    code: str
    message: str
    detail: Optional[Mapping[str, Any]] = None
    cause: Optional[BaseException] = None

    def __init__(
        self,
        code: Any,
        message: str,
        *,
        detail: Optional[Mapping[str, Any]] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        code_value = code.value if isinstance(code, ERROR_CODES) else str(code)
        object.__setattr__(self, "code", code_value)
        object.__setattr__(self, "message", str(message))
        object.__setattr__(self, "detail", None if detail is None else dict(detail))
        object.__setattr__(self, "cause", cause)
        super().__init__(f"[{code_value}] {message}")

    @property
    def error_code(self) -> str:
        return self.code

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.detail:
            out["detail"] = dict(self.detail)
        return out


class InvalidInputError(ApplicationError):
    """输入缺失 / 格式错误 / 非法值。"""

    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__(ERROR_CODES.INVALID_INPUT, message, **kw)


class NotFoundError(ApplicationError):
    """请求的业务对象 (course/material/student/...) 不存在。"""

    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__(ERROR_CODES.NOT_FOUND, message, **kw)


class ConflictError(ApplicationError):
    """重复创建 / 状态冲突 / 已被拒绝。"""

    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__(ERROR_CODES.CONFLICT, message, **kw)


class ProcessingError(ApplicationError):
    """业务处理过程中出现可归因的失败 (摄取/验证/计算失败)。"""

    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__(ERROR_CODES.PROCESSING_ERROR, message, **kw)


class UnsupportedError(ApplicationError):
    """请求的能力在当前实现中不支持。"""

    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__(ERROR_CODES.UNSUPPORTED, message, **kw)


class StorageError(ApplicationError):
    """存储层 (文件/数据库) 操作失败。"""

    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__(ERROR_CODES.STORAGE_ERROR, message, **kw)


class ConfigurationError(ApplicationError):
    """配置缺失或非法。"""

    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__(ERROR_CODES.CONFIGURATION_ERROR, message, **kw)


class InternalError(ApplicationError):
    """不可预期的内部错误; 面向用户的描述必须保持保守。"""

    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__(ERROR_CODES.INTERNAL_ERROR, message, **kw)


class PortInUseError(ApplicationError, OSError):
    """端口被占用 —— 用户只应看到 ``Port XXXX is already in use.``。

    这是"启动失败"而非"配置错", 所以跨 CLI / launcher 统一只用这句英文友好提示,
    不夹带 ``[PORT_IN_USE]`` 错误码前缀 (那会让普通用户看到他们用不上的术语)。
    同时是 ``OSError`` 的子类 —— 端口被占用本来就是操作系统级错误, 这样既有
    测试里 ``pytest.raises(OSError)`` 仍然成立, 新代码又能精确捕获它。
    """

    def __init__(self, port: int, *, cause: Optional[BaseException] = None) -> None:
        super().__init__("PORT_IN_USE", f"Port {port} is already in use.", cause=cause)
        object.__setattr__(self, "port", int(port))


def map_application_error(
    exc: BaseException,
    default_code: Any = ERROR_CODES.INTERNAL_ERROR,
) -> ApplicationError:
    """把领域层 / 底层异常映射为结构化 ApplicationError。

    规则:
    - ApplicationError 原样返回。
    - 携带稳定 .code 属性的领域错误按 code 语义映射:
        * INVALID / VALIDATION / SCHEMA -> INVALID_INPUT
        * *_NOT_FOUND / *_UNKNOWN -> NOT_FOUND
        * DUPLICATE / CONFLICT -> CONFLICT
        * STORAGE / PERSISTENCE -> STORAGE_ERROR
        * 其他 -> default_code
    - ValueError / TypeError -> INVALID_INPUT
    - KeyError -> NOT_FOUND
    - 其他 Exception -> default_code (INTERNAL_ERROR 兜底)
    """
    if isinstance(exc, ApplicationError):
        return exc

    default_code_value = (
        default_code.value if isinstance(default_code, ERROR_CODES) else str(default_code)
    )

    domain_code = getattr(exc, "code", None)
    if isinstance(domain_code, str) and domain_code:
        upper = domain_code.upper()
        if upper.startswith("INVALID") or "VALIDATION" in upper or upper.startswith("SCHEMA"):
            return InvalidInputError(str(exc), detail={"domain_code": domain_code})
        if upper.endswith("NOT_FOUND") or upper.endswith("UNKNOWN"):
            return NotFoundError(str(exc), detail={"domain_code": domain_code})
        if upper.startswith("DUPLICATE") or upper.startswith("CONFLICT"):
            return ConflictError(str(exc), detail={"domain_code": domain_code})
        if upper.startswith("STORAGE") or upper.startswith("PERSISTENCE"):
            return StorageError(str(exc), detail={"domain_code": domain_code})
        return ApplicationError(
            default_code_value, str(exc), detail={"domain_code": domain_code}, cause=exc
        )

    if isinstance(exc, ValueError):
        return InvalidInputError(str(exc), cause=exc)
    if isinstance(exc, TypeError):
        return InvalidInputError(str(exc), cause=exc)
    if isinstance(exc, KeyError):
        key_repr = repr(exc.args[0]) if exc.args else repr(None)
        return NotFoundError(f"key not found: {key_repr}", cause=exc)
    return ApplicationError(default_code_value, "unexpected error", cause=exc)
