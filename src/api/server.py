# -*- coding: utf-8 -*-
"""本地 HTTP 服务器 (Task 38)。

设计取舍 (spec 原则 9):
- 使用标准库 ``http.server.ThreadingHTTPServer``, 不引入 FastAPI/Flask/
  uvicorn。理由: 单机、本地、单用户, 只需要同步的请求-响应模型, 而框架
  会额外带来 ASGI server、数据校验库等一整套运行时依赖。
- **默认只监听 127.0.0.1**, 绝不默认监听 0.0.0.0。
- 用户可见错误一律是结构化 JSON; traceback 只写日志, 绝不返回。

静态资源 (Task 39 的 Web UI) 由同一进程提供, 因此部署只有一个入口。
"""

from __future__ import annotations

import logging
import os
import posixpath
import socket
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from src.application.errors import PortInUseError
from src.application.logging_setup import log_event
from src.application.workspace import Workspace
from src.api.endpoints import MAX_UPLOAD_BYTES, build_router
from src.api.responses import ApiResponse, failure, json_bytes, success
from src.api.router import NotFoundRoute, Request, Router, parse_query

__all__ = [
    "DEFAULT_HOST",
    "ApiServer",
    "create_server",
    "default_static_dir",
]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

_STATIC_CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
}


def default_static_dir() -> str:
    """内置 Web UI 资源目录 (``src/web``)。"""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


class _ClassroomHTTPServer(ThreadingHTTPServer):
    """端口冲突必须能被检测到。

    ``HTTPServer`` 默认 ``allow_reuse_address = 1``, 在 Windows 上这会允许
    两个进程绑定同一个端口, 于是"端口已被占用"永远不会被发现。Windows 上
    因此显式关闭该选项 (POSIX 上保留, 以便重启时不受 TIME_WAIT 影响)。

    另外记录**活跃连接**: ``shutdown()`` 只停止 accept 循环, 不会关闭已经
    建立的 keep-alive 连接 —— 那些连接由 ``daemon_threads`` 里的工作线程
    持有。测试里每个用例都起一个 ``port=0`` 的临时服务器, 若不主动断开,
    反复运行会累积大量 TIME_WAIT 套接字, 最终耗尽临时端口
    (症状是全量回归里出现 `ConnectionAbortedError [WinError 10053]`)。
    所以关闭时统一 ``shutdown(SHUT_RDWR)`` 掉所有还在的连接。
    """

    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._live_connections: set[Any] = set()
        self._connections_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def track_connection(self, connection: Any) -> None:
        with self._connections_lock:
            self._live_connections.add(connection)

    def forget_connection(self, connection: Any) -> None:
        with self._connections_lock:
            self._live_connections.discard(connection)

    def close_live_connections(self) -> None:
        """主动断开所有仍在的连接 (不依赖 GC 或对端超时)。"""
        with self._connections_lock:
            live = list(self._live_connections)
            self._live_connections.clear()
        for connection in live:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass


class ApiServer:
    """可嵌入、可测试的本地 API 服务器。"""

    def __init__(
        self,
        workspace: Workspace,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        static_dir: Optional[str] = None,
        max_upload_bytes: int = MAX_UPLOAD_BYTES,
        debug: bool = False,
        logger: Optional[Any] = None,
    ) -> None:
        if workspace is None:
            raise ValueError("workspace is required")
        if host not in ("127.0.0.1", "localhost", "::1"):
            # 显式允许但必须由调用方主动选择; 默认值永远是回环地址。
            pass
        self.workspace = workspace
        self.host = host
        self._static_dir = os.path.abspath(static_dir or default_static_dir())
        self.max_upload_bytes = max_upload_bytes
        self.debug = debug
        self.router: Router = build_router(workspace)
        self._logger = logger
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._requested_port = port

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    @property
    def port(self) -> int:
        if self._httpd is None:
            return self._requested_port
        return int(self._httpd.server_address[1])

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::") else self.host
        return f"http://{host}:{self.port}"

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> "ApiServer":
        """绑定端口并启动后台线程; 端口被占用时抛出可读错误。"""
        if self._httpd is not None:
            return self
        try:
            httpd = _ClassroomHTTPServer(
                (self.host, self._requested_port), self._make_handler()
            )
        except OSError as exc:
            raise PortInUseError(self._requested_port) from exc
        httpd.daemon_threads = True
        self._httpd = httpd
        self._thread = threading.Thread(
            target=httpd.serve_forever, name="classroom-assistant-http", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        httpd, thread = self._httpd, self._thread
        self._httpd = None
        self._thread = None
        if httpd is not None:
            httpd.shutdown()
            # shutdown() 只停 accept 循环; keep-alive 连接仍由工作线程持有,
            # 必须主动断开, 否则套接字进入 TIME_WAIT 并累积。
            httpd.close_live_connections()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def serve_forever(self) -> None:
        """阻塞式运行 (CLI 入口使用)。"""
        self.start()
        assert self._thread is not None
        self._thread.join()

    # ------------------------------------------------------------------
    # handler
    # ------------------------------------------------------------------

    def _httpd_track(self, connection: Any) -> None:
        """登记一条新连接 (由 handler 的 setup() 调用)。"""
        if self._httpd is not None:
            self._httpd.track_connection(connection)

    def _httpd_forget(self, connection: Any) -> None:
        """注销一条已结束的连接 (由 handler 的 finish() 调用)。"""
        if self._httpd is not None:
            self._httpd.forget_connection(connection)

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "ClassroomAssistant"
            sys_version = ""

            # -- connection lifecycle ------------------------------------

            def setup(self) -> None:
                super().setup()
                # 登记连接, 让服务器关闭时能主动断开 (见 _ClassroomHTTPServer)。
                server._httpd_track(self.connection)  # noqa: SLF001

            def finish(self) -> None:
                try:
                    super().finish()
                finally:
                    server._httpd_forget(self.connection)  # noqa: SLF001

            # -- logging -------------------------------------------------

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
                if server._logger is None:  # noqa: SLF001
                    return
                # Task 44 修复的真实缺陷: 这里原来传的是 extra={"message": ...},
                # 而 ``message`` 是 logging 的保留属性名 -> 每次都会抛
                # KeyError("Attempt to overwrite 'message' in LogRecord")。
                # 这个方法位于 send_response() 的调用路径上, 所以"注入一个
                # logger"会让**每一个 HTTP 响应**在发响应头之前崩掉, 客户端
                # 只看到 RemoteDisconnected。改用项目约定的 log_event。
                log_event(
                    server._logger,  # noqa: SLF001
                    logging.INFO,
                    "http_request",
                    fmt % args,
                )

            # -- verbs ---------------------------------------------------

            def do_GET(self) -> None:  # noqa: N802
                self._handle("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._handle("POST")

            def do_PATCH(self) -> None:  # noqa: N802
                self._handle("PATCH")

            def do_DELETE(self) -> None:  # noqa: N802
                self._handle("DELETE")

            def do_HEAD(self) -> None:  # noqa: N802
                self._handle("GET", send_body=False)

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Allow", "GET, POST, PATCH, DELETE, OPTIONS")
                self.send_header("Content-Length", "0")
                self.end_headers()

            # -- core ----------------------------------------------------

            def _handle(self, method: str, send_body: bool = True) -> None:
                parsed = urlparse(self.path)
                path = unquote(parsed.path)
                try:
                    if path.startswith("/api/"):
                        response = self._dispatch(method, path, parsed.query)
                    else:
                        response = self._static(path)
                except Exception as exc:  # noqa: BLE001 - 服务器兜底
                    response = server._error_response(exc)  # noqa: SLF001
                self._write(response, send_body=send_body)

            def _dispatch(self, method: str, path: str, query: str) -> ApiResponse:
                try:
                    content_length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    return failure("INVALID_INPUT", "invalid Content-Length header")
                if content_length > server.max_upload_bytes:  # noqa: SLF001
                    return failure(
                        "INVALID_INPUT",
                        f"payload too large: {content_length} bytes "
                        f"(limit {server.max_upload_bytes})",  # noqa: SLF001
                        status=413,
                    )
                body = self.rfile.read(content_length) if content_length else b""
                headers = {
                    key.lower(): value for key, value in self.headers.items()
                }
                request = Request(
                    method=method,
                    path=path,
                    query=parse_query(query),
                    headers=headers,
                    body=body,
                    remote=self.client_address[0] if self.client_address else "",
                )
                try:
                    return server.router.dispatch(request)  # noqa: SLF001
                except NotFoundRoute:
                    allowed = server.router.allowed_methods(path)  # noqa: SLF001
                    if allowed:
                        return failure(
                            "INVALID_INPUT",
                            f"method {method} not allowed for {path}",
                            status=405,
                            detail={"allowed": allowed},
                        )
                    return failure("NOT_FOUND", f"no route for {path}", status=404)

            def _static(self, path: str) -> ApiResponse:
                relative = path.lstrip("/") or "index.html"
                # 归一化后必须仍在 static 目录内 (拒绝 .. 逃逸)。
                normalized = posixpath.normpath("/" + relative).lstrip("/")
                if normalized.startswith(".."):
                    return failure("INVALID_INPUT", "invalid path", status=400)
                target = os.path.abspath(
                    os.path.join(server._static_dir, *normalized.split("/"))  # noqa: SLF001
                )
                root = server._static_dir  # noqa: SLF001
                if not (target == root or target.startswith(root + os.sep)):
                    return failure("INVALID_INPUT", "invalid path", status=400)
                if os.path.isdir(target):
                    target = os.path.join(target, "index.html")
                if not os.path.isfile(target):
                    if os.path.isfile(os.path.join(root, "index.html")):
                        # 单页应用的客户端路由: 未知路径回落到 index.html。
                        target = os.path.join(root, "index.html")
                    else:
                        return failure("NOT_FOUND", f"not found: {path}", status=404)
                extension = os.path.splitext(target)[1].lower()
                content_type = _STATIC_CONTENT_TYPES.get(
                    extension, "application/octet-stream"
                )
                with open(target, "rb") as handle:
                    payload = handle.read()
                return ApiResponse(
                    status=200,
                    payload={"__raw__": payload, "__content_type__": content_type},
                )

            # -- writing -------------------------------------------------

            def _write(self, response: ApiResponse, send_body: bool = True) -> None:
                raw = response.payload.get("__raw__")
                if raw is not None:
                    body = raw
                    content_type = response.payload["__content_type__"]
                else:
                    body = json_bytes(response.payload)
                    content_type = "application/json; charset=utf-8"
                self.send_response(response.status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                if send_body and body:
                    self.wfile.write(body)

        return Handler

    def _error_response(self, exc: BaseException) -> ApiResponse:
        """异常 -> 结构化响应 (traceback 只进日志)。

        所有非 ApplicationError 的异常都必须经过领域错误映射, 否则
        ``KeyError`` 之类的领域信号会退化成 500 INTERNAL_ERROR, 用户看到
        "服务器错误"而不是"资源不存在"。
        """
        from src.application.errors import ApplicationError, map_application_error

        if self._logger is not None:
            self._logger.exception("unhandled_request_error")
        mapped = exc if isinstance(exc, ApplicationError) else map_application_error(exc)
        message = mapped.message
        if self.debug and mapped.code == "INTERNAL_ERROR" and str(exc):
            # 开发模式: 未映射的内部错误保留原始描述, 方便定位。
            message = str(exc)
        detail = dict(mapped.detail) if (self.debug and mapped.detail) else None
        return failure(mapped.code, message, detail=detail)


def create_server(
    workspace: Workspace,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    static_dir: Optional[str] = None,
    max_upload_bytes: int = MAX_UPLOAD_BYTES,
    debug: bool = False,
    logger: Optional[Any] = None,
) -> ApiServer:
    """构造 (但不启动) 一个本地 API 服务器。"""
    return ApiServer(
        workspace,
        host=host,
        port=port,
        static_dir=static_dir,
        max_upload_bytes=max_upload_bytes,
        debug=debug,
        logger=logger,
    )
