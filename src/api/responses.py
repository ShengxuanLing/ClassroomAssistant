# -*- coding: utf-8 -*-
"""HTTP 响应封装与错误映射 (Task 38)。

统一响应契约 (spec)::

    成功:  {"success": true,  "data": {...}}
    失败:  {"success": false, "error": {"code": "...", "message": "..."}}

硬性原则:
- 绝不把 Python traceback 返回给最终用户。
- 结构化错误码 (8 种) 必须映射到合理的 HTTP 状态码。
- 开发模式可以额外返回 detail, 生产模式只返回 code + message。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from src.application.errors import ERROR_CODES, ApplicationError

__all__ = [
    "HTTP_STATUS_BY_CODE",
    "ApiResponse",
    "success",
    "failure",
    "status_for",
    "json_bytes",
]

#: 8 种结构化错误码 -> HTTP 状态码。
HTTP_STATUS_BY_CODE: dict[str, int] = {
    ERROR_CODES.INVALID_INPUT.value: 400,
    ERROR_CODES.NOT_FOUND.value: 404,
    ERROR_CODES.CONFLICT.value: 409,
    ERROR_CODES.PROCESSING_ERROR.value: 422,
    ERROR_CODES.UNSUPPORTED.value: 501,
    ERROR_CODES.STORAGE_ERROR.value: 500,
    ERROR_CODES.CONFIGURATION_ERROR: 500,
    ERROR_CODES.INTERNAL_ERROR.value: 500,
}


@dataclass(frozen=True)
class ApiResponse:
    status: int
    payload: dict[str, Any]

    @property
    def body(self) -> bytes:
        return json_bytes(self.payload)


def json_bytes(payload: Any) -> bytes:
    """确定性 JSON 序列化 (排序键, UTF-8, 不转义非 ASCII)。"""
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def status_for(code: str) -> int:
    return HTTP_STATUS_BY_CODE.get(str(code), 500)


def success(data: Any = None, status: int = 200) -> ApiResponse:
    return ApiResponse(status=status, payload={"success": True, "data": data})


def failure(
    code: str,
    message: str,
    *,
    status: Optional[int] = None,
    detail: Optional[Mapping[str, Any]] = None,
) -> ApiResponse:
    error: dict[str, Any] = {"code": str(code), "message": str(message)}
    if detail:
        error["detail"] = dict(detail)
    return ApiResponse(
        status=status if status is not None else status_for(code),
        payload={"success": False, "error": error},
    )


def failure_from_exception(
    exc: BaseException, *, debug: bool = False
) -> ApiResponse:
    """把异常映射为结构化失败响应; 绝不泄漏 traceback。"""
    if isinstance(exc, ApplicationError):
        detail = dict(exc.detail) if (debug and exc.detail) else None
        return failure(exc.code, exc.message, detail=detail)
    if isinstance(exc, ValueError):
        return failure(ERROR_CODES.INVALID_INPUT.value, str(exc))
    if isinstance(exc, KeyError):
        return failure(ERROR_CODES.NOT_FOUND.value, "resource not found")
    # 兜底: 面向用户保守化, 详细信息只进开发日志。
    message = str(exc) if debug else "unexpected internal error"
    return failure(ERROR_CODES.INTERNAL_ERROR.value, message)
