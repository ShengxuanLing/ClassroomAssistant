# -*- coding: utf-8 -*-
"""Task 39 tests: Web Dashboard (HTML / CSS / Vanilla JS)。

验证方式的说明 (重要, 不掩饰限制)
----------------------------------
本机是 Windows, 而浏览器自动化 (agent-browser) **只支持 macOS / Linux**,
因此本任务**没有**浏览器端到端测试。绝不假装做过。

替代验证策略 (真实可执行的四层):
  1. 静态资源层: 用真实 HTTP 取回 index.html / styles.css / app.js, 校验
     HTTP 语义 (状态码 / Content-Type / charset / 安全头) 与路径安全。
  2. 资源自包含层: 校验 UI 无构建链、无外部 CDN / 网络依赖, 可离线运行。
  3. API 契约层: 校验 UI 依赖的 dashboard / trace endpoint 返回页面渲染
     所需的**全部字段**, 并且是确定性的。
  4. 溯源链层: 校验 "KnowledgePoint -> Evidence -> Source Material" 在 API
     层完整、可解析, 且断链会被显式暴露而不是被静默吞掉。

UI 交互 (点击按钮、hash 路由、表单提交) 由 app.js 中的函数实现, 这里通过
静态断言 + API 契约断言覆盖其依赖面, 但不声称执行了 DOM 事件。
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

import pytest

from src.api.server import create_server, default_static_dir
from src.application.runtime import fixed_clock
from src.application.workspace import APPLICATION_NAME, APPLICATION_VERSION, Workspace
from src.models import Confidence, Evidence, EvidenceType, Language, SourceReference

from tests.support import read_web_source

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXED_TIME = "2026-01-01T00:00:00+00:00"
WEB_DIR = Path(default_static_dir())


# ---------------------------------------------------------------------------
# HTTP 客户端 (最小实现; 不依赖第三方库)
# ---------------------------------------------------------------------------


class Client:
    """最小 HTTP 客户端。

    连接**复用**是刻意为之: 每个测试都会起一个 ``port=0`` 的临时服务器,
    若每条请求都新建 TCP 连接, 整个测试会话会累积上千个 TIME_WAIT 套接字,
    在长跑 (full regression) 里足以耗尽本机临时端口, 表现为
    ``ConnectionAbortedError [WinError 10053]`` —— 看起来像被测服务崩了,
    其实是测试自己把端口用光了。用 keep-alive 连接池把这个副作用消掉。
    """

    def __init__(self, base_url: str) -> None:
        self.base = base_url
        # 同一 host:port 复用一条连接; 服务器换端口时自动换连接。
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPHandler(), urllib.request.HTTPSHandler()
        )
        self._connection: Optional[http.client.HTTPConnection] = None
        self._connection_key: Optional[str] = None

    def _connection_for(self, url: str) -> http.client.HTTPConnection:
        parsed = urllib.parse.urlsplit(url)
        key = "%s://%s:%s" % (parsed.scheme, parsed.hostname, parsed.port)
        if self._connection_key != key or self._connection is None:
            self.close()
            self._connection = http.client.HTTPConnection(
                parsed.hostname, parsed.port, timeout=30
            )
            self._connection_key = key
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响测试结论
                pass
        self._connection = None
        self._connection_key = None

    def request(self, method, path, *, body=None, raw=None, headers=None):
        data = raw
        if data is None and body is not None:
            data = json.dumps(body).encode("utf-8")
        url = self.base + path
        send_headers = {}
        if body is not None:
            send_headers["Content-Type"] = "application/json"
        for key, value in (headers or {}).items():
            send_headers[key] = value
        if data is not None:
            send_headers["Content-Length"] = str(len(data))

        connection = self._connection_for(url)
        parsed = urllib.parse.urlsplit(url)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        try:
            connection.request(method, target, body=data, headers=send_headers)
            response = connection.getresponse()
            payload = response.read()
            return response.status, _decode(response, payload)
        except (http.client.HTTPException, ConnectionError, OSError):
            # 连接可能被上一条响应的 keep-alive 超时关掉; 重试一次新连接。
            self.close()
            connection = self._connection_for(url)
            connection.request(method, target, body=data, headers=send_headers)
            response = connection.getresponse()
            payload = response.read()
            return response.status, _decode(response, payload)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body=body, **kw)

    def upload(self, path, content, filename, headers=None):
        merged = {"X-Filename": filename}
        merged.update(headers or {})
        return self.request("POST", path, raw=content, headers=merged)

    def upload_multipart(self, path, content, filename, fields=None):
        """按浏览器 FormData 的方式上传 (UTF-8 文件名直接写进头部)。"""
        boundary = "----ClassroomAssistantBoundary39"
        chunks = []
        for key, value in (fields or {}).items():
            chunks.append(
                (
                    "--" + boundary + "\r\n"
                    'Content-Disposition: form-data; name="' + key + '"\r\n\r\n'
                    + str(value) + "\r\n"
                ).encode("utf-8")
            )
        chunks.append(
            (
                "--" + boundary + "\r\n"
                'Content-Disposition: form-data; name="file"; filename="'
                + filename + '"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(content)
        chunks.append(("\r\n--" + boundary + "--\r\n").encode("utf-8"))
        return self.request(
            "POST",
            path,
            raw=b"".join(chunks),
            headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
        )

    def raw_get(self, path):
        """取回原始响应 (状态码, 响应头, 字节); 4xx/5xx 不抛异常。"""
        url = self.base + path
        parsed = urllib.parse.urlsplit(url)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        for attempt in (0, 1):
            connection = self._connection_for(url)
            try:
                connection.request("GET", target)
                response = connection.getresponse()
                body = response.read()
                return response.status, response.headers, body
            except (http.client.HTTPException, ConnectionError, OSError):
                # 连接被 keep-alive 超时关掉: 换一条新连接重试一次。
                self.close()
                if attempt == 1:
                    raise


def _decode(response, payload: bytes):
    content_type = response.headers.get("Content-Type", "")
    if "json" not in content_type:
        return payload
    return json.loads(payload.decode("utf-8"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path):
    return Workspace(
        str(tmp_path / "data"),
        clock=fixed_clock(FIXED_TIME),
        asr_mode="mock",
        ocr_mode="mock",
    )


@pytest.fixture
def server(workspace):
    instance = create_server(workspace, port=0)
    instance.start()
    try:
        yield instance
    finally:
        instance.stop()


@pytest.fixture
def client(server):
    instance = Client(server.url)
    try:
        yield instance
    finally:
        # 显式释放 keep-alive 连接, 不依赖 GC —— 长跑回归里
        # 依赖 GC 释放会积累套接字, 最终耗尽临时端口。
        instance.close()


@pytest.fixture
def course(client):
    status, payload = client.post("/api/courses", {"name": "Algebra Lineal", "code": "AL"})
    assert status == 201
    return payload["data"]


@pytest.fixture
def processed(client, course):
    """真实跑一遍: 建课堂 -> 上传 multilingual.pdf -> 整堂处理。"""
    course_id = course["course_id"]
    _, session = client.post(
        "/api/sessions", {"course_id": course_id, "session_number": 1, "title": "Tema 1"}
    )
    session_id = session["data"]["session_id"]
    status, uploaded = client.upload(
        f"/api/materials?course_id={course_id}&session_id={session_id}",
        (FIXTURES / "documents" / "multilingual.pdf").read_bytes(),
        "multilingual.pdf",
    )
    assert status == 201, uploaded
    client.post(f"/api/sessions/{session_id}/process")
    _, listing = client.get(f"/api/knowledge?course_id={course_id}")
    points = listing["data"]["knowledge_points"]
    assert points, "fixture must produce at least one knowledge point"
    return {
        "course_id": course_id,
        "session_id": session_id,
        "material_id": uploaded["data"]["material_id"],
        "knowledge_ids": [point["knowledge_id"] for point in points],
        "points": points,
    }


def read_asset(name: str) -> str:
    """读取前端资源。

    P1-6: 前端已拆分成多个零构建脚本; ``read_asset("app.js")`` 返回按加载
    顺序拼接的**全部前端源码**, 语义与拆分前一致 (见 ``tests/support.py``)。
    """
    return read_web_source(name)


# ===========================================================================
# 0. 服务器生命周期: 关闭时必须主动断开连接
# ===========================================================================


class TestServerConnectionLifecycle:
    """每个用例都起一个 ``port=0`` 的临时服务器, 所以**连接回收**是
    测试基础设施正确性的一部分, 不是产品可选项。

    背景: 单进程跑完整套件时曾出现 3 failed + 37 errors, 错误类型清一色是
    ``ConnectionAbortedError [WinError 10053]`` / ``ConnectionResetError``
    —— 因为 ``shutdown()`` 只停 accept 循环, 不关闭已被工作线程持有的
    keep-alive 连接, 套接字于是进入 TIME_WAIT 并累积, 最终耗尽临时端口。
    """

    def test_stop_closes_the_listening_socket(self, workspace):
        from src.api.server import create_server

        instance = create_server(workspace, port=0)
        instance.start()
        port = instance.port
        instance.stop()
        # 关闭后同一端口应当可以**立即**被重新绑定 (Windows 上尤其严格)。
        import socket as _socket

        probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", port))
        finally:
            probe.close()

    def test_stop_disconnects_live_keep_alive_connections(self, workspace):
        """建立一条 keep-alive 连接后 stop(), 连接必须被服务端断开。"""
        from src.api.server import create_server

        instance = create_server(workspace, port=0)
        instance.start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1", instance.port, timeout=10
            )
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            response.read()
            assert response.status == 200
            # keep-alive: 连接此时仍然活着。
            assert connection.sock is not None
        finally:
            instance.stop()

        # 服务器已主动断开 —— 后续读写必须失败, 而不是静默挂着。
        try:
            connection.request("GET", "/api/health")
            leftover = connection.getresponse()
            leftover.read()
        except (http.client.HTTPException, ConnectionError, OSError):
            pass
        else:
            raise AssertionError(
                "server stopped but the keep-alive connection was still usable"
            )
        finally:
            connection.close()

    def test_server_tracks_and_forgets_connections(self, workspace):
        """连接登记表必须随连接建立/结束而增减 (不是只增不减)。"""
        from src.api.server import create_server

        instance = create_server(workspace, port=0)
        instance.start()
        try:
            assert instance._httpd is not None
            assert instance._httpd._live_connections == set()
            connection = http.client.HTTPConnection(
                "127.0.0.1", instance.port, timeout=10
            )
            connection.request("GET", "/api/health")
            connection.getresponse().read()
            assert len(instance._httpd._live_connections) >= 1
            # 主动关闭连接后, 登记表应当被清理。
            connection.close()
            deadline = time.time() + 5
            while instance._httpd._live_connections and time.time() < deadline:
                time.sleep(0.02)
            assert instance._httpd._live_connections == set()
        finally:
            instance.stop()


# ===========================================================================
# 1. 静态资源: HTTP 语义与路径安全
# ===========================================================================


class TestStaticAssets:
    def test_root_serves_dashboard_shell(self, client):
        status, headers, body = client.raw_get("/")
        assert status == 200
        assert headers.get("Content-Type") == "text/html; charset=utf-8"
        text = body.decode("utf-8")
        assert "<!DOCTYPE html>" in text
        assert 'id="view"' in text

    def test_explicit_index_path(self, client):
        status, headers, body = client.raw_get("/index.html")
        assert status == 200
        assert headers.get("Content-Type") == "text/html; charset=utf-8"
        assert b"Classroom Assistant" in body

    def test_stylesheet_is_served_with_css_content_type(self, client):
        status, headers, body = client.raw_get("/styles.css")
        assert status == 200
        assert headers.get("Content-Type") == "text/css; charset=utf-8"
        assert b":root" in body

    def test_script_is_served_with_javascript_content_type(self, client):
        status, headers, body = client.raw_get("/app.js")
        assert status == 200
        assert headers.get("Content-Type") == "application/javascript; charset=utf-8"
        assert b"'use strict'" in body

    def test_static_assets_carry_no_store_and_nosniff(self, client):
        for path in ("/", "/styles.css", "/app.js"):
            _, headers, _ = client.raw_get(path)
            assert headers.get("Cache-Control") == "no-store"
            assert headers.get("X-Content-Type-Options") == "nosniff"

    def test_unknown_path_falls_back_to_shell_for_client_routing(self, client):
        """hash 路由不需要服务端支持, 但深链直接访问也必须能打开。"""
        status, headers, body = client.raw_get("/courses/course-abc/knowledge/kp-1")
        assert status == 200
        assert headers.get("Content-Type") == "text/html; charset=utf-8"
        assert b'id="view"' in body

    def test_static_path_traversal_never_leaks_repository_files(self, client):
        """路径逃逸尝试绝不能让静态层读到 ``src/web`` 之外的文件。

        注意: 对 SPA 而言, 未命中的路径回落到 ``index.html`` 是**设计行为**
        (客户端路由需要它), 所以这里断言的不是状态码, 而是安全属性 ——
        仓库文件的字节绝不能出现在响应里。
        """
        payloads = (
            "/../AGENTS.md",
            "/../../AGENTS.md",
            "/..%2fAGENTS.md",
            "/%2e%2e/%2e%2e/AGENTS.md",
            "/../server.py",
            "/../api/server.py",
            "/....//AGENTS.md",
        )
        for path in payloads:
            status, _, body = client.raw_get(path)
            assert status in (200, 400, 404), (path, status)
            assert b"# AGENTS.md" not in body, path
            assert b"from __future__" not in body, path
            assert b"def build_router" not in body, path

    def test_traversal_that_would_escape_returns_the_shell_or_an_error(self, client):
        status, headers, body = client.raw_get("/..%2f..%2fAGENTS.md")
        if status == 200:
            assert headers.get("Content-Type") == "text/html; charset=utf-8"
            assert b'id="view"' in body
        else:
            assert status in (400, 404)


# ===========================================================================
# 2. 静态资源: 自包含 / 无构建链 / 无外部依赖
# ===========================================================================


class TestAssetSelfContainment:
    def test_shell_references_only_local_assets(self):
        html = read_asset("index.html")
        assert 'href="/styles.css"' in html
        assert 'src="/app.js"' in html
        assert "http://" not in html
        assert "https://" not in html

    def test_javascript_has_no_network_or_module_dependencies(self):
        js = read_asset("app.js")
        for forbidden in ("http://", "https://", "cdn.", "import ", "require(", "node_modules"):
            assert forbidden not in js, f"app.js must not depend on {forbidden!r}"

    def test_css_has_no_external_references(self):
        css = read_asset("styles.css")
        for forbidden in ("http://", "https://", "@import", "url("):
            assert forbidden not in css, f"styles.css must not reference {forbidden!r}"

    def test_shell_exposes_mount_points_used_by_the_router(self):
        html = read_asset("index.html")
        for mount in ('id="view"', 'id="course-list"', 'id="pill-health"',
                      'id="pill-modes"', 'id="banner"', 'id="footer-app"'):
            assert mount in html, mount

    def test_shell_navigation_covers_dashboard_course_knowledge_pages(self):
        html = read_asset("index.html")
        for href in ('href="#/"', 'href="#/knowledge"', 'href="#/materials"', 'href="#/reviews"'):
            assert href in html, href

    def test_javascript_routes_cover_all_required_pages(self):
        js = read_asset("app.js")
        for marker in ("pageDashboard", "pageCourse", "pageSession",
                       "pageKnowledgeDetail", "pageMaterials", "pageReviews"):
            assert "function " + marker in js, marker
        for route in ("'knowledge'", "'materials'", "'reviews'", "'courses'",
                      "'sessions'", "'students'"):
            assert route in js, route

    def test_javascript_escapes_api_text_before_inserting_into_dom(self):
        """API 返回的原文是任意用户内容, 必须转义后才能进 innerHTML。"""
        js = read_asset("app.js")
        assert "function esc(" in js
        assert ".replace(/&/g" in js
        assert ".replace(/</g" in js
        assert "esc(m.filename)" in js
        assert "esc(ev.content)" in js or "esc(text || '')" in js

    def test_css_defines_traceability_chain_styles(self):
        css = read_asset("styles.css")
        assert ".trace" in css
        assert ".trace-broken" in css
        assert ".trace-node" in css

    def test_web_assets_are_utf8_and_keep_non_ascii(self):
        for name in ("index.html", "styles.css", "app.js"):
            text = read_asset(name)
            assert isinstance(text, str)
        assert "课堂助手" in read_asset("index.html")
        assert "课堂助手" in read_asset("app.js")

    def test_javascript_uses_only_the_documented_api_surface(self):
        """UI 只能通过 /api/* 取数, 不得触碰其他路径。"""
        js = read_asset("app.js")
        assert "const API_BASE = '/api';" in js
        assert "fetch(url" in js


# ===========================================================================
# 3. /api/dashboard —— 首页快照
# ===========================================================================


class TestDashboardEndpoint:
    REQUIRED_SECTIONS = (
        "application", "version", "courses", "course_id", "sessions",
        "materials", "processing", "knowledge", "review_pending",
        "students", "exercises", "gaps",
    )

    def test_dashboard_has_every_required_section(self, client, course):
        status, payload = client.get("/api/dashboard")
        assert status == 200
        assert payload["success"] is True
        for section in self.REQUIRED_SECTIONS:
            assert section in payload["data"], section

    def test_dashboard_empty_state_is_legal(self, client):
        status, payload = client.get("/api/dashboard")
        assert status == 200
        data = payload["data"]
        assert data["courses"] == []
        assert data["course_id"] is None
        assert data["knowledge"] == {"count": 0, "points": [], "summary": None}
        assert data["materials"] == []

    def test_dashboard_selects_lowest_course_id_deterministically(self, client):
        ids = []
        for name in ("Zeta", "Alpha", "Mu"):
            _, payload = client.post("/api/courses", {"name": name})
            ids.append(payload["data"]["course_id"])
        _, payload = client.get("/api/dashboard")
        assert payload["data"]["course_id"] == sorted(ids)[0]

    def test_dashboard_honours_explicit_course_id(self, client, course):
        _, payload = client.get(f"/api/dashboard?course_id={course['course_id']}")
        assert payload["data"]["course_id"] == course["course_id"]

    def test_dashboard_unknown_course_is_404(self, client):
        status, payload = client.get("/api/dashboard?course_id=course-nope")
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_dashboard_reports_application_identity(self, client, course):
        _, payload = client.get("/api/dashboard")
        assert payload["data"]["application"] == APPLICATION_NAME
        assert payload["data"]["version"] == APPLICATION_VERSION

    def test_dashboard_reflects_processed_course(self, client, processed):
        cid = processed["course_id"]
        _, payload = client.get(f"/api/dashboard?course_id={cid}")
        data = payload["data"]
        assert len(data["sessions"]) == 1
        assert len(data["materials"]) == 1
        assert data["materials"][0]["filename"] == "multilingual.pdf"
        assert data["knowledge"]["count"] == len(processed["knowledge_ids"])
        assert data["knowledge"]["summary"]["course_id"] == cid

    def test_dashboard_processing_section_reports_job_status(self, client, processed):
        cid = processed["course_id"]
        _, payload = client.get(f"/api/dashboard?course_id={cid}")
        processing = payload["data"]["processing"]
        assert processing["total"] == 1
        assert processing["by_status"]["SUCCEEDED"] == 1
        assert processing["evidence_total"] >= 1
        assert processing["jobs"][0]["material_id"] == processed["material_id"]

    def test_dashboard_knowledge_points_are_compact_and_grounded(self, client, processed):
        cid = processed["course_id"]
        _, payload = client.get(f"/api/dashboard?course_id={cid}")
        for point in payload["data"]["knowledge"]["points"]:
            assert set(point) == {
                "knowledge_id", "title", "validation_status",
                "review_status", "knowledge_score",
            }

    def test_dashboard_review_pending_lists_real_candidates(self, client, processed):
        cid = processed["course_id"]
        _, payload = client.get(f"/api/dashboard?course_id={cid}")
        assert isinstance(payload["data"]["review_pending"], list)

    def test_dashboard_gaps_section_is_scoped_to_course(self, client, processed):
        cid = processed["course_id"]
        _, payload = client.get(f"/api/dashboard?course_id={cid}")
        assert payload["data"]["gaps"]["course_id"] == cid

    def test_dashboard_is_deterministic(self, client, processed):
        cid = processed["course_id"]
        _, first = client.get(f"/api/dashboard?course_id={cid}")
        _, second = client.get(f"/api/dashboard?course_id={cid}")
        assert first["data"] == second["data"]

    def test_dashboard_does_not_mutate_state(self, client, processed):
        cid = processed["course_id"]
        _, before = client.get(f"/api/dashboard?course_id={cid}")
        client.get(f"/api/dashboard?course_id={cid}")
        _, after = client.get(f"/api/dashboard?course_id={cid}")
        assert before["data"] == after["data"]

    def test_dashboard_reports_students_and_exercises(self, client, processed):
        cid = processed["course_id"]
        client.post("/api/students", {"course_id": cid, "student_id": "stu-1"})
        client.post("/api/exercises", {
            "course_id": cid,
            "exercise_type": "multiple_choice",
            "prompt": "¿Qué es una función?",
            "knowledge_point_ids": [processed["knowledge_ids"][0]],
            "choices": [{"choice_id": "a", "text": "Una relación"},
                        {"choice_id": "b", "text": "Un número"}],
            "correct_choice_id": "a",
        })
        _, payload = client.get(f"/api/dashboard?course_id={cid}")
        assert len(payload["data"]["students"]) == 1
        assert len(payload["data"]["exercises"]) == 1


# ===========================================================================
# 4. /api/knowledge/{id}/trace —— 核心 UX 溯源链
# ===========================================================================


class TestKnowledgeTraceEndpoint:
    def test_trace_requires_course_id(self, client, processed):
        kid = processed["knowledge_ids"][0]
        status, payload = client.get(f"/api/knowledge/{kid}/trace")
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_trace_unknown_knowledge_is_404(self, client, course):
        status, payload = client.get(
            f"/api/knowledge/kp-nope/trace?course_id={course['course_id']}"
        )
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_trace_has_every_field_the_page_renders(self, client, processed):
        kid = processed["knowledge_ids"][0]
        _, payload = client.get(
            f"/api/knowledge/{kid}/trace?course_id={processed['course_id']}"
        )
        data = payload["data"]
        for key in ("course_id", "knowledge_point", "evidence", "links", "materials",
                    "unresolved_material_ids", "sessions", "source_sessions",
                    "topics", "dependencies", "language", "complete"):
            assert key in data, key

    def test_knowledge_point_carries_the_fields_the_page_displays(self, client, processed):
        kid = processed["knowledge_ids"][0]
        _, payload = client.get(
            f"/api/knowledge/{kid}/trace?course_id={processed['course_id']}"
        )
        kp = payload["data"]["knowledge_point"]
        for key in ("knowledge_id", "title", "content", "original_terms",
                    "validation_status", "review_status", "knowledge_score", "confidence",
                    "importance", "needs_verification", "evidence_refs", "related_points"):
            assert key in kp, key
        assert kp["knowledge_id"] == kid

    def test_every_evidence_link_resolves_to_a_source_material(self, client, processed):
        kid = processed["knowledge_ids"][0]
        _, payload = client.get(
            f"/api/knowledge/{kid}/trace?course_id={processed['course_id']}"
        )
        data = payload["data"]
        assert data["links"], "trace must expose the evidence chain"
        for link in data["links"]:
            assert "evidence" in link and "material" in link
            assert link["evidence"]["evidence_id"]
            assert link["material"] is not None
            assert link["material"]["filename"]
            assert link["material"]["material_id"] == \
                link["evidence"]["source"]["material_id"]

    def test_trace_materials_match_the_evidence_sources(self, client, processed):
        kid = processed["knowledge_ids"][0]
        _, payload = client.get(
            f"/api/knowledge/{kid}/trace?course_id={processed['course_id']}"
        )
        data = payload["data"]
        referenced = {
            link["evidence"]["source"]["material_id"]
            for link in data["links"] if link["evidence"]["source"]["material_id"]
        }
        resolved = {material["material_id"] for material in data["materials"]}
        assert referenced == resolved
        assert data["complete"] is True
        assert data["unresolved_material_ids"] == []

    def test_trace_preserves_the_original_uploaded_filename(self, client, processed):
        kid = processed["knowledge_ids"][0]
        _, payload = client.get(
            f"/api/knowledge/{kid}/trace?course_id={processed['course_id']}"
        )
        assert payload["data"]["materials"][0]["filename"] == "multilingual.pdf"

    def test_trace_evidence_content_is_verbatim(self, client, processed):
        kid = processed["knowledge_ids"][0]
        _, payload = client.get(
            f"/api/knowledge/{kid}/trace?course_id={processed['course_id']}"
        )
        for link in payload["data"]["links"]:
            evidence = link["evidence"]
            assert evidence["content"]
            assert evidence["language"]
            assert evidence["confidence"]
            assert evidence["evidence_type"]

    def test_trace_derives_source_session_from_the_evidence_chain(self, client, processed):
        kid = processed["knowledge_ids"][0]
        _, payload = client.get(
            f"/api/knowledge/{kid}/trace?course_id={processed['course_id']}"
        )
        data = payload["data"]
        assert [session["session_id"] for session in data["source_sessions"]] == \
            [processed["session_id"]]
        # 组织层显式归属与证据推导归属是两个不同的事实, 必须分开报告。
        assert isinstance(data["sessions"], list)
        assert isinstance(data["topics"], list)

    def test_trace_reports_language_without_inventing_it(self, client, processed):
        kid = processed["knowledge_ids"][0]
        _, payload = client.get(
            f"/api/knowledge/{kid}/trace?course_id={processed['course_id']}"
        )
        language = payload["data"]["language"]
        assert language["declared"] is None
        assert isinstance(language["evidence_languages"], list)
        assert "no language field" in language["note"]

    def test_trace_exposes_dependencies(self, client, processed):
        kid = processed["knowledge_ids"][0]
        _, payload = client.get(
            f"/api/knowledge/{kid}/trace?course_id={processed['course_id']}"
        )
        deps = payload["data"]["dependencies"]
        assert set(deps) == {"outgoing", "incoming", "related_points"}
        assert isinstance(deps["outgoing"], list)
        assert isinstance(deps["incoming"], list)
        assert deps["related_points"] == []

    def test_trace_is_deterministic(self, client, processed):
        kid = processed["knowledge_ids"][0]
        _, first = client.get(
            f"/api/knowledge/{kid}/trace?course_id={processed['course_id']}"
        )
        _, second = client.get(
            f"/api/knowledge/{kid}/trace?course_id={processed['course_id']}"
        )
        assert first["data"] == second["data"]

    def test_trace_surfaces_broken_provenance_instead_of_hiding_it(
        self, client, workspace, course
    ):
        """证据引用了一个不存在的材料: 必须显式报告断链, 而不是假装完整。"""
        cid = course["course_id"]
        ctx = workspace.context(cid)
        workspace.store.add(
            Evidence(
                evidence_id="ev-broken-1",
                content="Evidencia cuyo material no existe",
                language=Language.SPANISH,
                source_reference=SourceReference(material_id="mat-does-not-exist", page=1),
                confidence=Confidence.HIGH,
                evidence_type=EvidenceType.DOCUMENT,
            )
        )
        ctx.workflow.register_knowledge_point({
            "knowledge_id": "kp-broken-1",
            "title": "Punto sin material",
            "content": "Contenido de prueba",
            "evidence_refs": ["ev-broken-1"],
        })
        status, payload = client.get(f"/api/knowledge/kp-broken-1/trace?course_id={cid}")
        assert status == 200
        data = payload["data"]
        assert data["complete"] is False
        assert data["unresolved_material_ids"] == ["mat-does-not-exist"]
        assert data["materials"] == []
        assert data["links"][0]["material"] is None
        assert data["links"][0]["evidence"]["evidence_id"] == "ev-broken-1"

    def test_trace_reports_no_evidence_without_fabricating_one(self, client, workspace, course):
        cid = course["course_id"]
        ctx = workspace.context(cid)
        ctx.workflow.register_knowledge_point({
            "knowledge_id": "kp-no-evidence",
            "title": "Punto sin evidencia",
            "content": "Sin respaldo",
            "evidence_refs": [],
        })
        status, payload = client.get(f"/api/knowledge/kp-no-evidence/trace?course_id={cid}")
        assert status == 200
        data = payload["data"]
        assert data["evidence"] == []
        assert data["links"] == []
        assert data["materials"] == []
        assert data["complete"] is True


# ===========================================================================
# 5. 溯源链完整性: 对课程内**每一个**知识点都要成立
# ===========================================================================


class TestTraceabilityIntegrity:
    def test_every_knowledge_point_of_a_real_course_is_traceable(self, client, processed):
        cid = processed["course_id"]
        _, listing = client.get(f"/api/knowledge?course_id={cid}")
        points = listing["data"]["knowledge_points"]
        assert points
        for point in points:
            status, payload = client.get(
                f"/api/knowledge/{point['knowledge_id']}/trace?course_id={cid}"
            )
            assert status == 200
            data = payload["data"]
            assert data["knowledge_point"]["knowledge_id"] == point["knowledge_id"]
            for link in data["links"]:
                source = link["evidence"]["source"]
                assert source["material_id"], "evidence must carry a material reference"
                assert link["material"]["material_id"] == source["material_id"]
            assert data["complete"] is True

    def test_material_evidence_endpoint_feeds_the_same_chain(self, client, processed):
        cid = processed["course_id"]
        status, payload = client.get(
            f"/api/materials/{processed['material_id']}/evidence?course_id={cid}"
        )
        assert status == 200
        evidence = payload["data"]["evidence"]
        assert evidence
        for item in evidence:
            assert item["source"]["material_id"] == processed["material_id"]

    def test_review_actions_used_by_the_ui_are_available(self, client, processed):
        cid = processed["course_id"]
        kid = processed["knowledge_ids"][0]
        status, payload = client.post(
            f"/api/reviews/{kid}/keep-unverified?course_id={cid}",
            {"note": "revisado a mano"},
        )
        assert status == 200
        assert payload["data"]["decision"]
        _, history = client.get(f"/api/reviews/{kid}?course_id={cid}")
        assert history["data"]["history"]
        assert history["data"]["latest"]["note"] == "revisado a mano"

    def test_processing_action_used_by_the_ui_is_idempotent(self, client, processed):
        cid = processed["course_id"]
        first = client.post(f"/api/materials/{processed['material_id']}/process?course_id={cid}")
        second = client.post(f"/api/materials/{processed['material_id']}/process?course_id={cid}")
        assert first[0] == 200 and second[0] == 200
        assert first[1]["data"]["evidence_ids"] == second[1]["data"]["evidence_ids"]

    def test_course_page_data_is_scoped_per_course(self, client, processed):
        _, other = client.post("/api/courses", {"name": "Otra asignatura"})
        other_id = other["data"]["course_id"]
        _, payload = client.get(f"/api/dashboard?course_id={other_id}")
        data = payload["data"]
        assert data["course_id"] == other_id
        assert data["materials"] == []
        assert data["knowledge"]["count"] == 0
        assert data["sessions"] == []


# ===========================================================================
# 6. 数据保真与安全: UI 展示的文本必须原样、不得被改写
# ===========================================================================


class TestDataFidelityAndSafety:
    def test_multipart_upload_keeps_unicode_filename_verbatim(self, client, course):
        """浏览器用 multipart 上传; 西语/加泰语/中文文件名必须逐字保留。

        这条测试同时锁住两个真实缺陷:
        1) multipart 头部若按 latin-1 解码, 非 ASCII 文件名会变成乱码;
        2) 暂存文件名若直接使用用户原始名, Windows 上遇非法字符会 500。
        """
        cid = course["course_id"]
        name = "Tema 1 – Introducció a la funció (català).txt"
        status, payload = client.upload_multipart(
            f"/api/materials?course_id={cid}", "Contingut original".encode("utf-8"), name
        )
        assert status == 201, payload
        assert payload["data"]["filename"] == name
        _, listing = client.get(f"/api/materials?course_id={cid}")
        assert listing["data"]["materials"][0]["filename"] == name

    def test_multipart_upload_keeps_chinese_filename_verbatim(self, client, course):
        cid = course["course_id"]
        name = "课堂笔记 第三讲.txt"
        status, payload = client.upload_multipart(
            f"/api/materials?course_id={cid}", "笔记内容".encode("utf-8"), name
        )
        assert status == 201, payload
        assert payload["data"]["filename"] == name

    def test_uploaded_filename_is_never_rewritten(self, client, course):
        """文件名是溯源信息, 上传 / 登记过程绝不能改写它 (含非 ASCII)。"""
        cid = course["course_id"]
        name = "Tema 1 - Introduccio a la funcio (catala).txt"
        status, payload = client.upload(
            f"/api/materials?course_id={cid}", b"Contingut original", name
        )
        assert status == 201
        assert payload["data"]["filename"] == name

    def test_filename_via_percent_encoded_query_keeps_unicode(self, client, course):
        """``X-Filename`` 头部只能承载 latin-1, 因此非 ASCII 必须走 multipart
        或百分号编码的 query 参数 —— 两条路径都要正确。"""
        cid = course["course_id"]
        name = "Funció de capital.txt"
        encoded = urllib.parse.quote(name)
        status, payload = client.request(
            "POST",
            f"/api/materials?course_id={cid}&filename={encoded}",
            raw=b"Contingut",
        )
        assert status == 201, payload
        assert payload["data"]["filename"] == name

    def test_windows_illegal_filename_characters_do_not_break_upload(self, client, course):
        """``<`` ``>`` 在 HTTP 里合法, 在 Windows 文件名里非法。

        暂存名必须被净化 (否则 500), 但溯源字段必须原样保留。
        """
        cid = course["course_id"]
        name = "Tema<1>: notas|parte?.txt"
        status, payload = client.upload_multipart(
            f"/api/materials?course_id={cid}", b"payload", name
        )
        assert status == 201, payload
        assert payload["data"]["filename"] == name
        # 受管副本使用内容寻址名, 与用户文件名无关 —— 落盘一定是安全的。
        assert "<" not in payload["data"]["stored_path"]
        assert ">" not in payload["data"]["stored_path"]
        assert "?" not in payload["data"]["stored_path"]

    def test_sanitize_filename_is_deterministic_and_keeps_extension(self):
        from src.application.data_dirs import sanitize_filename

        assert sanitize_filename("a<b>c.txt") == "a_b_c.txt"
        assert sanitize_filename("  notas .md ") == "notas.md"
        assert sanitize_filename("CON.txt") == "upload.txt"
        assert sanitize_filename("") == "upload"
        assert sanitize_filename("../../evil.pdf") == "evil.pdf"
        assert sanitize_filename("a<b>.txt") == sanitize_filename("a<b>.txt")
        assert sanitize_filename("x" * 500 + ".txt").endswith(".txt")
        assert len(sanitize_filename("x" * 500 + ".txt")) <= 124

    def test_html_in_filename_is_preserved_verbatim_for_client_side_escaping(self, client, course):
        """API 不负责转义 (那是渲染层的责任), 但必须原样保留字节。"""
        cid = course["course_id"]
        name = "<img src=x onerror=alert(1)>.txt"
        status, payload = client.upload_multipart(
            f"/api/materials?course_id={cid}", b"payload", name
        )
        assert status == 201, payload
        assert payload["data"]["filename"] == name
        _, listing = client.get(f"/api/materials?course_id={cid}")
        assert listing["data"]["materials"][0]["filename"] == name

    def test_spanish_and_catalan_text_survives_the_api_round_trip(self, client, processed):
        cid = processed["course_id"]
        kid = processed["knowledge_ids"][0]
        _, payload = client.get(f"/api/knowledge/{kid}/trace?course_id={cid}")
        kp = payload["data"]["knowledge_point"]
        assert "Introduccio" in kp["title"] or "Introducció" in kp["title"]
        for link in payload["data"]["links"]:
            assert link["evidence"]["content"].strip()

    def test_api_endpoints_never_return_html(self, client, processed):
        cid = processed["course_id"]
        for path in ("/api/health", "/api/dashboard", f"/api/knowledge?course_id={cid}",
                     f"/api/materials?course_id={cid}", f"/api/processing?course_id={cid}"):
            _, headers, _ = client.raw_get(path)
            assert headers.get("Content-Type") == "application/json; charset=utf-8"

    def test_ui_error_payloads_never_leak_tracebacks(self, client):
        status, payload = client.get("/api/knowledge/kp-missing/trace?course_id=course-missing")
        assert status in (404, 400)
        text = json.dumps(payload, ensure_ascii=False)
        assert "Traceback" not in text
        assert "File \"" not in text

    def test_web_directory_contains_no_python_or_build_artifacts(self):
        """src/web 只放静态资源; 混入源码或构建产物会让部署边界变模糊。

        P1-6: 前端做了零构建拆分 (无打包器、无新依赖), 单个 app.js 变成
        ``api.js`` / ``i18n.js`` / ``app.js`` / ``views/``。这一条仍然是
        "只放静态资源"的守卫 —— **视图目录里的每一个文件都必须是 .js**,
        绝不能混进 Python 源码或构建产物 (node_modules / dist / .map 等)。
        """
        names = sorted(path.name for path in WEB_DIR.iterdir())
        assert names == [
            "api.js", "app.js", "i18n.js", "index.html", "styles.css", "views",
        ], names
        # views/ 里只允许 .js —— 零构建拆分不得引入打包产物。
        for path in (WEB_DIR / "views").iterdir():
            assert path.is_file() and path.suffix == ".js", (
                f"views/ 里混入了非 JS 文件: {path.name}"
            )


# ===========================================================================
# 7. UI <-> API 契约: app.js 里用到的每一个 endpoint 都必须真实可用
# ===========================================================================


class TestUiApiContract:
    """双向锁: app.js 里出现的路径必须在本测试里被验证, 反之亦然。

    没有浏览器可跑, 于是用"路径清单 + 真实 HTTP 调用"来保证 UI 不会指向
    不存在的 endpoint。若 app.js 改了路径而测试没跟上, 下面的片段断言会
    失败, 强迫维护者同步更新。
    """

    #: app.js 中真实出现的路径片段 (从 `api('...')` 字面量提取)。
    UI_PATH_FRAGMENTS = (
        "/health", "/dashboard", "/courses", "/sessions", "/materials",
        "/processing", "/knowledge", "/course-knowledge", "/reviews", "/students",
        # Task 68: 多课程工作台 (总览 / 课程选择 / 单课摘要)。
        "/my-courses", "/course-selection",
    )

    #: Web UI 依赖的完整 API 面 (含 Task 40/41 将要接入的 endpoint,
    #: 它们现在就必须可用, 否则后续 UI 无法落地)。
    #: (方法, 路径模板, 需要 course_id query, 期望状态码)
    CONTRACT = (
        ("GET", "/api/health", False, {200, 503}),
        ("GET", "/api/dashboard", True, {200}),
        ("GET", "/api/courses", False, {200}),
        ("GET", "/api/courses/{course_id}", False, {200}),
        ("GET", "/api/sessions", True, {200}),
        ("GET", "/api/sessions/{session_id}", False, {200}),
        ("POST", "/api/sessions/{session_id}/process", False, {200}),
        ("GET", "/api/materials", True, {200}),
        ("GET", "/api/materials/{material_id}", True, {200}),
        ("GET", "/api/materials/{material_id}/evidence", True, {200}),
        ("POST", "/api/materials/{material_id}/process", True, {200}),
        ("POST", "/api/materials/{material_id}/retry", True, {200}),
        ("GET", "/api/processing", True, {200}),
        ("GET", "/api/knowledge", True, {200}),
        ("GET", "/api/knowledge/{knowledge_id}", True, {200}),
        ("GET", "/api/knowledge/{knowledge_id}/evidence", True, {200}),
        ("GET", "/api/knowledge/{knowledge_id}/trace", True, {200}),
        ("GET", "/api/course-knowledge", True, {200}),
        ("GET", "/api/reviews", True, {200}),
        ("GET", "/api/reviews/{knowledge_id}", True, {200}),
        ("POST", "/api/reviews/{knowledge_id}/keep-unverified", True, {200}),
        ("GET", "/api/students", True, {200}),
        ("POST", "/api/students", True, {201, 200}),
        ("GET", "/api/students/{student_id}/learning-status", True, {200}),
        ("GET", "/api/exercises", True, {200}),
        ("POST", "/api/exercises", True, {201, 200}),
        ("POST", "/api/answers", True, {201}),
        ("GET", "/api/evaluations/{answer_id}", True, {200}),
        ("GET", "/api/study-plans/{student_id}", True, {200}),
        # Task 68: 多课程。
        ("GET", "/api/my-courses", False, {200}),
        ("GET", "/api/course-selection", False, {200}),
        ("GET", "/api/courses/{course_id}/summary", False, {200}),
    )

    def test_javascript_only_calls_paths_covered_by_this_contract(self):
        js = read_asset("app.js")
        for fragment in self.UI_PATH_FRAGMENTS:
            assert "'" + fragment in js, "app.js no longer calls " + fragment
            assert any(fragment in path for _, path, _, _ in self.CONTRACT), fragment

    def test_every_ui_endpoint_is_reachable(self, client, processed):
        cid = processed["course_id"]
        sid = processed["session_id"]
        kid = processed["knowledge_ids"][0]
        mid = processed["material_id"]

        client.post("/api/students", {"course_id": cid, "student_id": "stu-ui"})
        _, exercise = client.post("/api/exercises", {
            "course_id": cid,
            "exercise_type": "true_false",
            "prompt": "¿La función es una relación?",
            "knowledge_point_ids": [kid],
            "is_true": True,
        })
        exercise_id = exercise["data"]["exercise_id"]
        _, answer = client.post("/api/answers", {
            "course_id": cid,
            "student_id": "stu-ui",
            "exercise_id": exercise_id,
            "submitted_value": "true",
        })
        answer_id = answer["data"]["answer_id"]

        values = {
            "course_id": cid,
            "session_id": sid,
            "material_id": mid,
            "knowledge_id": kid,
            "student_id": "stu-ui",
            "exercise_id": exercise_id,
            "answer_id": answer_id,
        }
        for method, template, needs_course, expected in self.CONTRACT:
            path = template.format(**values)
            if needs_course:
                path += ("&" if "?" in path else "?") + "course_id=" + cid
            if method == "GET":
                status, payload = client.get(path)
            else:
                status, payload = client.post(path, self._post_body(template, values))
            assert status in expected, (method, path, status, payload)
            assert payload["success"] is True, (method, path, payload)

    @staticmethod
    def _post_body(template, values):
        """POST endpoint 的最小合法请求体 (course_id 一律走 query)。"""
        if template == "/api/reviews/{knowledge_id}/keep-unverified":
            return {"note": "ui-contract"}
        if template == "/api/students":
            return {"student_id": "stu-ui-contract"}
        if template == "/api/exercises":
            return {
                "exercise_type": "true_false",
                "prompt": "¿La función es una relación?",
                "knowledge_point_ids": [values["knowledge_id"]],
                "is_true": True,
            }
        if template == "/api/answers":
            return {
                "student_id": "stu-ui",
                "exercise_id": values["exercise_id"],
                "submitted_value": "true",
            }
        return {}

    def test_ui_endpoints_reject_unknown_course_with_structured_error(self, client):
        status, payload = client.get("/api/dashboard?course_id=course-ghost")
        assert status == 404
        assert payload["success"] is False
        assert payload["error"]["code"] == "NOT_FOUND"
        assert payload["error"]["message"]

    def test_ui_endpoints_never_return_html(self, client, processed):
        cid = processed["course_id"]
        for _, template, needs_course, _ in self.CONTRACT:
            if not template.startswith("/api/"):
                continue
            if "{" in template:
                continue
            path = template
            if needs_course:
                path += "?course_id=" + cid
            _, headers, _ = client.raw_get(path)
            assert headers.get("Content-Type") == "application/json; charset=utf-8", path
