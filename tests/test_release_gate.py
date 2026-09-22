# -*- coding: utf-8 -*-
"""Task 70 —— Release 1.0 发布门禁。

这个文件守的是"能不能对外发 1.0"，不是"功能对不对"。功能由各任务的
测试守；这里守的是**发布级的契约**：

1. **API 契约门**：用户输入错误**永远**是 4xx + 标准信封，绝不能是 500。
   500 意味着"用户的输入把服务器打崩了"，那既是可用性问题也是安全问题
   （堆栈/内部信息可能随之泄漏）。这条是**穷举**式的：把所有路由都拿
   坏输入打一遍，而不是挑几个端点写几条断言 —— 挑着写会漏掉新加的路由。

2. **依赖门**：不得引入 LLM / 云 API / embedding / 向量库。这是本项目的
   硬约束（全部能力必须本地可跑、可审计、可复现）。已有的
   ``test_production_gate.py::FORBIDDEN_TECHNOLOGY`` 守的是数据库/消息
   队列/前端构建链；这里补的是"AI 供应链"那一侧，并且是**全 src 扫描**。

3. **安全门**：密钥不得进日志；路径不得穿越出 data_dir；前端不得把
   内部错误原样吐给用户。

4. **全新安装门**：空目录起服务 -> health 正常 -> 能建课。

刻意**不**做的事：

- 不断言"性能有多快"（见 docs/task-69-stress-report.md 里为什么）。
- 不复制 ``test_production_gate.py`` 已经守住的持久化/确定性/幂等门禁，
  那里是 A–J 十个门，这里只补它没覆盖的那一层。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

import pytest

from src.api.server import create_server
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FIXED_TIME = "2026-09-18T09:00:00+00:00"
TODAY = "2026-09-18"

#: 用户输入错误允许的响应码。500 绝不允许出现在这个集合里。
CLIENT_ERROR_CODES = {400, 401, 403, 404, 405, 409, 413, 415, 422}

#: 拿去打每一条路由的"坏输入"。既有空值、也有长得像注入/穿越的串。
BAD_VALUES = (
    "",
    "%20",
    "nope",
    "..%2F..%2F..%2Fetc%2Fpasswd",
    "%2Fetc%2Fpasswd",
    "0",
    "-1",
    "true",
    "null",
    "[]",
    "%00",
    "a" * 300,
    "course%27%20OR%201%3D1",
)


# =====================================================================
# 夹具
# =====================================================================


def _open(data_dir: str) -> Workspace:
    return Workspace(
        data_dir,
        clock=fixed_clock(FIXED_TIME),
        asr_mode="mock",
        ocr_mode="mock",
    )


@pytest.fixture(scope="module")
def workspace(tmp_path_factory) -> Iterator[Workspace]:
    """一个有真实内容的库 —— 空库上"找不到"和"崩了"会混在一起。"""
    data_dir = str(tmp_path_factory.mktemp("release") / "data")
    root = Path(data_dir)
    notes = root / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    ws = _open(data_dir)
    try:
        cid = ws.create_course("Release", "R", "es")["course_id"]
        sid = ws.create_session(
            cid, session_number=1, date=TODAY, title="Tema 1"
        )["session_id"]
        note = notes / "apuntes-r1.md"
        note.write_text(
            "## Tema 1 (r1): Concepto\n\n"
            "El concepto r1 describe una relacion entre dos elementos.\n",
            encoding="utf-8",
        )
        ws.register_material(cid, str(note), session_id=sid)
        ws.process_session(cid, sid)
        ws.create_student(cid, "stu-release", "R")
        point = ws.knowledge_points(cid)[0]
        ws.create_exercise(
            cid,
            "true_false",
            "Pregunta de release",
            [str(point["knowledge_id"])],
            is_true=True,
        )
    finally:
        ws.close()
    opened = _open(data_dir)
    yield opened
    opened.close()


@pytest.fixture(scope="module")
def server(workspace) -> Iterator[Any]:
    srv = create_server(workspace, port=0).start()
    try:
        yield srv
    finally:
        srv.stop()


def _maybe_json(raw: str) -> Any:
    """响应体可能是 HTML（SPA 回退页），不是 JSON —— 不能因为解析不了就崩。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return {"_raw": raw}


def _get(server: Any, path: str) -> tuple[int, Any]:
    request = urllib.request.Request(server.url + path, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, _maybe_json(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, _maybe_json(exc.read().decode("utf-8"))


def _post(server: Any, path: str, body: Any) -> tuple[int, Any]:
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        server.url + path,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, _maybe_json(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, _maybe_json(exc.read().decode("utf-8"))


def _routes(server: Any) -> list[tuple[str, str]]:
    return [(route.method, route.pattern) for route in server.router.routes]


def _placeholders(pattern: str) -> list[str]:
    return re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", pattern)


# =====================================================================
# 1. API 契约门
# =====================================================================


class TestApiContractGate:
    """用户输入错误 -> 4xx + 标准信封。500 一次都不许出现。"""

    def test_every_get_route_rejects_a_bad_path_parameter_without_a_500(
        self, server
    ) -> None:
        offenders: list[str] = []
        checked = 0
        for method, pattern in _routes(server):
            if method != "GET":
                continue
            names = _placeholders(pattern)
            if not names:
                continue
            for value in BAD_VALUES:
                path = pattern
                for name in names:
                    path = path.replace("{%s}" % name, value)
                status, payload = _get(server, path)
                checked += 1
                if status >= 500:
                    offenders.append(f"{pattern} <- {value!r} -> {status}")
                elif status >= 400 and isinstance(payload, dict):
                    if payload.get("success") is not False:
                        offenders.append(
                            f"{pattern} <- {value!r} -> {status} 不是标准信封"
                        )
        assert checked > 50, f"只检查了 {checked} 次，路由枚举可能失效了"
        assert not offenders, "用户输入打出了 500 或非标准信封:\n" + "\n".join(
            offenders[:20]
        )

    def test_every_get_route_without_required_query_is_not_a_500(self, server) -> None:
        """缺必填 query 参数：必须是 400，不能是"参数丢了于是崩了"。"""
        offenders: list[str] = []
        checked = 0
        for method, pattern in _routes(server):
            if method != "GET":
                continue
            path = pattern
            for name in _placeholders(pattern):
                path = path.replace("{%s}" % name, "nope")
            status, payload = _get(server, path)
            checked += 1
            if status >= 500:
                offenders.append(f"{pattern} -> {status}")
            elif status >= 400 and isinstance(payload, dict):
                if payload.get("success") is not False:
                    offenders.append(f"{pattern} -> {status} 不是标准信封")
        assert checked > 20, f"只检查了 {checked} 条路由"
        assert not offenders, "\n".join(offenders[:20])

    #: 带请求体的写方法。``PATCH`` 也在这里 —— 它同样是用户输入面，
    #: 只扫 POST 会漏掉它（实测：路由表里正好有 1 条 PATCH）。
    WRITE_METHODS = ("POST", "PATCH", "PUT", "DELETE")

    def test_every_write_route_rejects_a_malformed_body_without_a_500(
        self, server
    ) -> None:
        bad_bodies: list[Any] = [
            {},
            {"course_id": "", "student_id": ""},
            {"course_id": None},
            {"unexpected": "field"},
        ]
        offenders: list[str] = []
        checked = 0
        methods_seen: set[str] = set()
        for method, pattern in _routes(server):
            if method not in self.WRITE_METHODS:
                continue
            methods_seen.add(method)
            path = pattern
            for name in _placeholders(pattern):
                path = path.replace("{%s}" % name, "nope")
            for body in bad_bodies:
                status, payload = _post(server, path, body)
                checked += 1
                if status >= 500:
                    offenders.append(f"{pattern} <- {body} -> {status}")
                elif status >= 400 and isinstance(payload, dict):
                    if payload.get("success") is not False:
                        offenders.append(f"{pattern} <- {body} -> 非标准信封")
        assert checked > 10, f"只检查了 {checked} 次"
        # 路由表里必须真的有写方法，否则这条测试是空转的。
        assert "POST" in methods_seen, methods_seen
        assert "PATCH" in methods_seen, f"PATCH 路由没被扫到: {methods_seen}"
        assert not offenders, "\n".join(offenders[:20])

    def test_a_client_error_never_leaks_a_traceback(self, server) -> None:
        """4xx 的 message 里不许带 Python 堆栈 —— 那是内部实现细节。"""
        leaks: list[str] = []
        for method, pattern in _routes(server):
            if method != "GET":
                continue
            path = pattern
            for name in _placeholders(pattern):
                path = path.replace("{%s}" % name, "nope")
            status, payload = _get(server, path)
            if status not in CLIENT_ERROR_CODES:
                continue
            text = json.dumps(payload, ensure_ascii=False)
            for marker in ("Traceback (most recent call last)", "File \"", ".py\", line "):
                if marker in text:
                    leaks.append(f"{pattern} -> {marker}")
        assert not leaks, "错误响应泄漏了堆栈:\n" + "\n".join(leaks[:10])

    def test_unknown_api_routes_are_404_not_500(self, server) -> None:
        """只判 ``/api/`` 前缀：``/`` 开头的未知路径会回退到 SPA 首页，
        那是**故意**的（前端路由），不是错误 —— 所以不能一并判成 404。
        """
        for path in (
            "/api/does-not-exist",
            "/api/courses/a/b/c/d",
            "/api/knowledge/extra/segments",
        ):
            status, payload = _get(server, path)
            assert status in {404, 400, 405}, f"{path} -> {status}"
            assert payload.get("success") is False, path

    def test_the_non_api_fallback_is_the_spa_shell_not_a_500(self, server) -> None:
        status, payload = _get(server, "/#/courses")
        assert status == 200, status
        text = json.dumps(payload, ensure_ascii=False)
        assert "<!DOCTYPE html>" in text, "SPA 回退没有返回外壳页面"

    def test_the_known_good_paths_still_return_200(self, server, workspace) -> None:
        """契约门不能顺手把正常路径也判成坏的输入 —— 好的必须还是好的。"""
        cid = workspace.list_courses()[0]["course_id"]
        for path in (
            "/api/health",
            "/api/courses",
            "/api/my-courses",
            "/api/courses/%s/summary" % cid,
            "/api/courses/%s/review" % cid,
        ):
            status, payload = _get(server, path)
            assert status == 200, f"{path} -> {status}"
            assert payload.get("success") is True, path


# =====================================================================
# 2. 依赖门（AI 供应链那一侧）
# =====================================================================


#: 禁止的"AI 供应链"。键是人读的类别，值是真正会被 import 的模块名。
FORBIDDEN_AI_TECHNOLOGY = {
    "LLM API": (
        "openai",
        "anthropic",
        "cohere",
        "mistralai",
        "groq",
        "replicate",
        "together",
    ),
    "LLM framework": (
        "langchain",
        "llama_index",
        "llamaindex",
        "transformers",
        "sentence_transformers",
        "instructor",
        "guidance",
    ),
    "embedding / vector store": (
        "faiss",
        "chromadb",
        "pinecone",
        "qdrant",
        "weaviate",
        "annoy",
        "hnswlib",
        "milvus",
        "lancedb",
    ),
    "cloud SDK": (
        "boto3",
        "botocore",
        "google.cloud",
        "azure.identity",
        "azure.ai",
    ),
}

#: 唯一的例外：``torch`` 只在 CUDA 分支里被**惰性** import，用来探测本机
#: 有没有可用 GPU —— 它不参与任何推理（推理由 faster-whisper / CTranslate2
#: 完成），也不是 LLM / embedding 依赖。这个例外必须被单独钉住，否则
#: "例外"会悄悄变成"口子"。
TORCH_FILE = SRC / "whisper_provider.py"


class TestDependencyGate:
    def test_no_forbidden_ai_module_is_imported_anywhere_in_src(self) -> None:
        offenders: list[str] = []
        for path in sorted(SRC.rglob("*.py")):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):  # pragma: no cover
                continue
            for label, names in FORBIDDEN_AI_TECHNOLOGY.items():
                for name in names:
                    if re.search(
                        r"^\s*(import\s+%s\b|from\s+%s\b)" % (re.escape(name), re.escape(name)),
                        text,
                        re.MULTILINE,
                    ):
                        offenders.append(
                            f"{label}: {path.relative_to(ROOT)} imports {name}"
                        )
        assert not offenders, "\n".join(offenders)

    def test_the_torch_import_stays_lazy_and_inside_the_cuda_branch(self) -> None:
        """``torch`` 只能是"用户主动要 CUDA"时才碰一下的本地探测。"""
        assert TORCH_FILE.is_file(), f"找不到 {TORCH_FILE}"
        lines = TORCH_FILE.read_text(encoding="utf-8").splitlines()
        hits = [i for i, line in enumerate(lines) if re.search(r"\btorch\b", line)]
        assert hits, "torch 已经不再被 import —— 这条例外应当一并删掉"
        for index in hits:
            window = "\n".join(lines[max(0, index - 6) : index + 1])
            assert "cuda" in window.lower(), (
                f"第 {index + 1} 行的 torch 引用不在 CUDA 分支里:\n{window}"
            )

    def test_requirements_still_declares_no_ai_dependency(self) -> None:
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for label, names in FORBIDDEN_AI_TECHNOLOGY.items():
            for name in names:
                assert not re.search(
                    r"^%s\b" % re.escape(name), text, re.MULTILINE
                ), f"{label}: requirements.txt 里出现了 {name}"

    def test_the_storage_backend_is_still_sqlite_only(self) -> None:
        """Task 68/69 没有把存储换成别的东西。"""
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for name in ("psycopg", "pymysql", "pymongo", "redis", "sqlalchemy"):
            assert name not in text, f"requirements.txt 里出现了 {name}"


# =====================================================================
# 3. 安全门
# =====================================================================


class TestSecurityGate:
    def test_no_hardcoded_secret_looking_literal_in_src(self) -> None:
        """源码里不许出现"长得像密钥"的字面量。

        判据刻意保守：只扫形如 ``sk-...`` / ``AKIA...`` / ``ghp_...`` 的
        前缀，避免把正常的变量名也判成密钥（那样这条门禁会天天误报，然后
        被人关掉）。
        """
        pattern = re.compile(r"\b(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{12,}|ghp_[A-Za-z0-9]{20,})\b")
        offenders: list[str] = []
        for path in sorted(SRC.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if pattern.search(text):
                offenders.append(str(path.relative_to(ROOT)))
        assert not offenders, "源码里出现了疑似密钥的字面量:\n" + "\n".join(offenders)

    def test_a_material_from_outside_is_copied_in_not_referenced(self, tmp_path) -> None:
        """登记一份 data_dir **之外**的材料时，产品把它收进 data_dir。

        这条守的是"材料路径是个指针还是个副本"：如果只存路径，那么后续
        的读取/备份/恢复都会去碰用户文件系统上的任意位置；收进来之后，
        data_dir 就是唯一需要关心的地方（备份也才能真正带走内容）。
        """
        outside = tmp_path / "outside.md"
        outside.write_text("## Nota externa\n\nContingut.\n", encoding="utf-8")
        data_dir = str(tmp_path / "app-data")
        workspace = _open(data_dir)
        try:
            cid = workspace.create_course("Sec", "S", "es")["course_id"]
            workspace.register_material(cid, str(outside))
        finally:
            workspace.close()
        root = Path(data_dir).resolve()
        for path in (root / "documents").rglob("*"):
            if path.is_file():
                assert root in path.resolve().parents, (
                    f"材料文件跑到了 data_dir 之外: {path}"
                )

    def test_the_static_handler_does_not_serve_a_file_outside_the_static_dir(
        self, server
    ) -> None:
        """``..`` 穿越不得拿到 static 目录之外的文件。

        判据看的是**内容**：``requirements.txt`` 里有 ``faster-whisper``，
        只要响应体里没有它，就没被读到（回退到 SPA 首页是允许的）。
        """
        for path in (
            "/../requirements.txt",
            "/..%2F..%2Frequirements.txt",
            "/static/../../requirements.txt",
        ):
            status, payload = _get(server, path)
            text = json.dumps(payload, ensure_ascii=False)
            assert "faster-whisper" not in text, f"{path} -> 读到了仓库里的文件"

    def test_the_server_binds_to_the_loopback_interface(self, server) -> None:
        """本地应用默认只监听回环 —— 不得对外暴露。"""
        assert server.host in ("127.0.0.1", "localhost", "::1"), server.host
        assert "127.0.0.1" in server.url or "localhost" in server.url, server.url


# =====================================================================
# 4. 全新安装门
# =====================================================================


class TestFreshInstall:
    def test_an_empty_data_dir_boots_into_a_healthy_server(self, tmp_path) -> None:
        """全新安装：空目录 -> 起服务 -> health 正常 -> 能建课。"""
        data_dir = str(tmp_path / "fresh")
        workspace = _open(data_dir)
        srv = create_server(workspace, port=0).start()
        try:
            status, payload = _get(srv, "/api/health")
            assert status == 200, status
            assert payload["success"] is True
            assert payload["data"]["status"] == "ok", payload["data"]

            status, payload = _get(srv, "/api/courses")
            assert status == 200
            assert payload["data"]["courses"] == [], "全新安装不该有任何课程"

            status, payload = _post(
                srv, "/api/courses", {"name": "Primera", "code": "P1"}
            )
            assert status in (200, 201), (status, payload)
        finally:
            srv.stop()
            workspace.close()

    def test_the_fresh_install_created_a_real_database(self, tmp_path) -> None:
        data_dir = str(tmp_path / "fresh-db")
        workspace = _open(data_dir)
        try:
            workspace.create_course("A", "A", "es")
        finally:
            workspace.close()
        db = Path(data_dir) / "database" / "classroom.sqlite"
        assert db.is_file(), "全新安装没有建出数据库文件"
        assert db.stat().st_size > 0

    def test_the_health_endpoint_reports_the_released_version(self, server) -> None:
        from src.application.workspace import APPLICATION_VERSION

        _status, payload = _get(server, "/api/health")
        assert payload["data"]["version"] == APPLICATION_VERSION
        assert APPLICATION_VERSION == "1.0.1", APPLICATION_VERSION


# =====================================================================
# 5. 版本门
# =====================================================================


class TestVersionRelease:
    """1.0.0 必须在**每一处**都是同一个值。

    ``test_production_gate.py::TestVersionAndDocumentation`` 钉的是
    "与 EXPECTED_VERSION 一致"；这里钉的是"这个值就是 1.0.0"，并且列出
    全部真源，防止改了一处忘了另一处。
    """

    EXPECTED = "1.0.1"

    def test_every_version_source_agrees(self) -> None:
        import src
        import tests as tests_package

        from src.application.workspace import APPLICATION_VERSION

        assert APPLICATION_VERSION == self.EXPECTED, APPLICATION_VERSION
        assert src.__version__ == self.EXPECTED, src.__version__
        assert tests_package.__version__ == self.EXPECTED, tests_package.__version__

    def test_the_production_gate_constant_is_the_same(self) -> None:
        text = (ROOT / "tests" / "test_production_gate.py").read_text(encoding="utf-8")
        match = re.search(r'^EXPECTED_VERSION = "([^"]+)"', text, re.MULTILINE)
        assert match is not None, "找不到 EXPECTED_VERSION"
        assert match.group(1) == self.EXPECTED, match.group(1)

    def test_the_release_report_exists_and_states_the_verdict(self) -> None:
        report = ROOT / "docs" / "task-66-70-final-report.md"
        assert report.is_file(), "缺少 docs/task-66-70-final-report.md"
        text = report.read_text(encoding="utf-8")
        assert "FINAL VERDICT: PASS" in text
        assert "RELEASE: 1.0.0" in text

    def test_the_stress_report_exists(self) -> None:
        report = ROOT / "docs" / "task-69-stress-report.md"
        assert report.is_file(), "缺少 docs/task-69-stress-report.md"
        assert len(report.read_text(encoding="utf-8")) > 3000
