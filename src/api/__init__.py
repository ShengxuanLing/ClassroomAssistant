# -*- coding: utf-8 -*-
"""本地 HTTP API 层 (Task 38)。

对外导出:
- Workspace: 多课程组合根 (src.application.workspace)
- ApiServer / create_server: 基于标准库 http.server 的本地服务器
- build_router: 路由表 (endpoint 列表见 src/api/endpoints.py)
- responses: 统一响应封装与错误码 -> HTTP 状态码映射

默认监听 127.0.0.1, 绝不默认监听 0.0.0.0。
"""

from src.api.responses import (
    HTTP_STATUS_BY_CODE,
    ApiResponse,
    failure,
    failure_from_exception,
    json_bytes,
    status_for,
    success,
)
from src.api.router import NotFoundRoute, Request, Route, Router
from src.api.endpoints import MAX_UPLOAD_BYTES, build_router
from src.api.server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    ApiServer,
    create_server,
    default_static_dir,
)

__all__ = [
    "ApiResponse",
    "ApiServer",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "HTTP_STATUS_BY_CODE",
    "MAX_UPLOAD_BYTES",
    "NotFoundRoute",
    "Request",
    "Route",
    "Router",
    "build_router",
    "create_server",
    "default_static_dir",
    "failure",
    "failure_from_exception",
    "json_bytes",
    "status_for",
    "success",
]
