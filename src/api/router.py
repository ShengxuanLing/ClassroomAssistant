# -*- coding: utf-8 -*-
"""极简 HTTP 路由 (Task 38)。

为什么不用 FastAPI / Flask: 本项目是**单机、本地、单用户、Windows 优先**
的应用 (spec 明确要求)。引入 Web 框架会带来 ASGI server、pydantic、
依赖解析等一整套额外运行时, 而这里需要的只是:

- 路径 -> 处理函数的映射
- JSON 请求/响应
- 一个文件上传入口

标准库 ``http.server`` + 约 200 行路由代码即可满足, 并且零新增依赖。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional

__all__ = [
    "Request",
    "Route",
    "Router",
    "NotFoundRoute",
    "parse_query",
    "parse_multipart_file",
]


class NotFoundRoute(Exception):
    """没有匹配的路由。"""


@dataclass(frozen=True)
class Request:
    """一次已解析的 HTTP 请求。"""

    method: str
    path: str
    query: Mapping[str, list[str]] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""
    params: Mapping[str, str] = field(default_factory=dict)
    remote: str = ""

    # -- query helpers -------------------------------------------------

    def q(self, name: str, default: Optional[str] = None) -> Optional[str]:
        values = self.query.get(name)
        if not values:
            return default
        return values[0]

    def require_q(self, name: str) -> str:
        value = self.q(name)
        if value is None or not str(value).strip():
            from src.application.errors import InvalidInputError

            raise InvalidInputError(f"query parameter {name!r} is required")
        return str(value).strip()

    def q_int(self, name: str, default: Optional[int] = None) -> Optional[int]:
        raw = self.q(name)
        if raw is None or str(raw).strip() == "":
            return default
        try:
            return int(str(raw))
        except ValueError:
            from src.application.errors import InvalidInputError

            raise InvalidInputError(f"query parameter {name!r} must be an integer")

    def q_bool(self, name: str, default: bool = False) -> bool:
        raw = self.q(name)
        if raw is None:
            return default
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    # -- body helpers --------------------------------------------------

    def json_body(self) -> dict[str, Any]:
        if not self.body:
            return {}
        try:
            payload = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            from src.application.errors import InvalidInputError

            raise InvalidInputError("request body must be valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            from src.application.errors import InvalidInputError

            raise InvalidInputError("request body must be a JSON object")
        return payload

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)


@dataclass(frozen=True)
class Route:
    method: str
    pattern: str
    handler: Callable[[Request], Any]
    _regex: Any = None
    _names: tuple[str, ...] = ()

    def match(self, method: str, path: str) -> Optional[dict[str, str]]:
        if self.method != method:
            return None
        found = self._regex.match(path)
        if found is None:
            return None
        return {name: found.group(name) for name in self._names}


class Router:
    """路径 -> 处理函数的路由表。

    支持 ``/api/courses/{course_id}`` 形式的占位段; 占位段不匹配 ``/``。
    """

    def __init__(self) -> None:
        self._routes: list[Route] = []

    @property
    def routes(self) -> tuple[Route, ...]:
        return tuple(self._routes)

    def add(self, method: str, pattern: str, handler: Callable[[Request], Any]) -> Route:
        regex, names = _compile_pattern(pattern)
        route = Route(
            method=method.upper(),
            pattern=pattern,
            handler=handler,
            _regex=regex,
            _names=names,
        )
        self._routes.append(route)
        return route

    def get(self, pattern: str, handler: Callable[[Request], Any]) -> Route:
        return self.add("GET", pattern, handler)

    def post(self, pattern: str, handler: Callable[[Request], Any]) -> Route:
        return self.add("POST", pattern, handler)

    def patch(self, pattern: str, handler: Callable[[Request], Any]) -> Route:
        return self.add("PATCH", pattern, handler)

    def delete(self, pattern: str, handler: Callable[[Request], Any]) -> Route:
        return self.add("DELETE", pattern, handler)

    def dispatch(self, request: Request) -> Any:
        """找到匹配的路由并调用; 无匹配时抛 :class:`NotFoundRoute`。"""
        path_matched = False
        for route in self._routes:
            params = route.match(request.method, request.path)
            if params is None:
                continue
            path_matched = True
            bound = Request(
                method=request.method,
                path=request.path,
                query=request.query,
                headers=request.headers,
                body=request.body,
                params=params,
                remote=request.remote,
            )
            return route.handler(bound)
        raise NotFoundRoute(request.path)

    def allowed_methods(self, path: str) -> list[str]:
        methods: list[str] = []
        for route in self._routes:
            if route._regex.match(path) is not None:  # noqa: SLF001 - 同类内部使用
                methods.append(route.method)
        return sorted(set(methods))


# ----------------------------------------------------------------------
# Parsing helpers
# ----------------------------------------------------------------------


def _compile_pattern(pattern: str) -> tuple[Any, tuple[str, ...]]:
    if not pattern.startswith("/"):
        raise ValueError(f"route pattern must start with '/': {pattern!r}")
    names: list[str] = []
    chunks: list[str] = []
    for segment in pattern.split("/"):
        if not segment:
            continue
        if segment.startswith("{") and segment.endswith("}"):
            name = segment[1:-1].strip()
            if not name:
                raise ValueError(f"empty route parameter in {pattern!r}")
            names.append(name)
            chunks.append(f"(?P<{name}>[^/]+)")
        else:
            chunks.append(re.escape(segment))
    regex = re.compile("^/" + "/".join(chunks) + "/?$")
    return regex, tuple(names)


def parse_query(query_string: str) -> dict[str, list[str]]:
    """解析 URL query (不做 URL 解码之外的任何语义处理)。"""
    from urllib.parse import parse_qs

    return parse_qs(query_string or "", keep_blank_values=True)


def parse_multipart_file(
    body: bytes, content_type: str
) -> tuple[Optional[str], bytes, dict[str, str]]:
    """从 multipart/form-data 里取出第一个文件字段。

    返回 ``(filename, content, fields)``; 不是 multipart 时返回
    ``(None, b"", {})``。只支持单层、非嵌套的 multipart —— 这对浏览器
    ``FormData`` 的单文件上传已经足够, 且不引入第三方依赖。
    """
    marker = "boundary="
    if marker not in content_type:
        return None, b"", {}
    boundary = content_type.split(marker, 1)[1].strip().strip('"')
    if not boundary:
        return None, b"", {}
    delimiter = b"--" + boundary.encode("latin-1")
    parts = body.split(delimiter)
    filename: Optional[str] = None
    content = b""
    fields: dict[str, str] = {}
    for part in parts:
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        raw_headers, _, raw_body = part.partition(b"\r\n\r\n")
        headers = _parse_part_headers(raw_headers)
        disposition = headers.get("content-disposition", "")
        name = _disposition_value(disposition, "name")
        file_name = _disposition_value(disposition, "filename")
        if file_name is not None:
            if filename is None:
                filename = file_name
                content = raw_body
        elif name:
            try:
                fields[name] = raw_body.decode("utf-8")
            except UnicodeDecodeError:
                fields[name] = ""
    return filename, content, fields


def _parse_part_headers(raw: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in raw.split(b"\r\n"):
        if b":" not in line:
            continue
        key, _, value = line.partition(b":")
        headers[_decode_header_text(key).strip().lower()] = _decode_header_text(value).strip()
    return headers


def _decode_header_text(raw: bytes) -> str:
    """multipart 部件头部按 **UTF-8 优先** 解码。

    HTTP 头部默认字符集是 latin-1, 但 WHATWG 规定浏览器把 ``FormData`` 的
    文件名以 UTF-8 字节直接写进 ``Content-Disposition``。若按 latin-1 解码,
    西语 / 加泰语 / 中文文件名会变成乱码 —— 而文件名是溯源信息的一部分,
    绝不能损坏。因此 UTF-8 优先, 解码失败再退回 latin-1 (纯 ASCII 头两
    者等价, 不会改变既有行为)。

    已知未支持: RFC 5987 的 ``filename*=UTF-8''...`` 形式。浏览器不会为
    ``FormData`` 生成它, 因此不作为产品路径。
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _disposition_value(disposition: str, key: str) -> Optional[str]:
    if not disposition:
        return None
    for chunk in disposition.split(";"):
        chunk = chunk.strip()
        if chunk.lower().startswith(key + "="):
            value = chunk[len(key) + 1:].strip()
            if value.startswith('"') and value.endswith('"') and len(value) >= 2:
                value = value[1:-1]
            return value or None
    return None
