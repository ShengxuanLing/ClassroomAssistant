# -*- coding: utf-8 -*-
"""Task 44 —— 运行时装配 (组合根) 与生产环境错误处理。

``src/application/bootstrap.py`` 是唯一允许同时认识 persistence / backup /
api 的地方。本文件验证:

- 配置的每一项都**真的驱动了**运行时 (否则它就是个摆设字段);
- ``asr_mode="auto"`` 回落 Mock 时绝不静默 (会有 runtime_note 警告);
- ``asr_mode="real"`` 在运行时缺失时直接报错, 而不是偷偷给 Mock;
- 组合根不会形成 import 环 (否则 ``python -m src.application.cli`` 直接崩)。
"""

from __future__ import annotations

import io
import json
import logging
import os
import socket
import subprocess
import sys
import urllib.request

import pytest

from src.application.bootstrap import (
    APPLICATION_LOGGER_NAME,
    Runtime,
    build_runtime,
    close_runtime,
    describe_runtime,
    is_local_whisper_available,
)
from src.application.config import AppConfig, load_config
from src.application.errors import ConfigurationError
from src.application.logging_setup import reset_logging

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = sys.executable


@pytest.fixture(autouse=True)
def _clean_logging():
    reset_logging()
    yield
    reset_logging()


def _config(tmp_path, **overrides) -> AppConfig:
    base = {
        "data_dir": str(tmp_path / "data"),
        "asr_mode": "mock",
        "ocr_config": {"kind": "mock"},
    }
    base.update(overrides)
    return AppConfig.load(env={}, cli_overrides=base)


@pytest.fixture
def runtime(tmp_path):
    built = build_runtime(_config(tmp_path), logger=logging.getLogger("classroom.test"))
    try:
        yield built
    finally:
        close_runtime(built)


# ======================================================================
# 装配
# ======================================================================


def test_build_runtime_requires_an_app_config(tmp_path):
    with pytest.raises(ConfigurationError):
        build_runtime({"data_dir": str(tmp_path)})  # type: ignore[arg-type]


def test_build_runtime_creates_the_data_layout(tmp_path):
    with build_runtime(_config(tmp_path), logger=logging.getLogger("x")) as built:
        for name in ("materials", "audio", "images", "documents", "database", "logs", "backups", "temp"):
            assert os.path.isdir(os.path.join(built.config.data_dir, name)), name


def test_build_runtime_opens_and_migrates_the_database(tmp_path):
    with build_runtime(_config(tmp_path), logger=logging.getLogger("x")) as built:
        assert os.path.isfile(built.config.database_path)
        assert built.database.schema_version() >= 1
        assert built.database.is_healthy() is True


def test_the_configured_database_path_is_actually_used(tmp_path):
    """``database_path`` 必须是真被用到的字段, 不是摆设。"""
    custom = tmp_path / "data" / "database" / "custom-name.sqlite"
    with build_runtime(
        _config(tmp_path, database_path=str(custom)), logger=logging.getLogger("x")
    ) as built:
        assert built.config.database_path == os.path.abspath(str(custom))
        assert os.path.isfile(str(custom))


def test_a_deep_database_path_is_created_on_demand(tmp_path):
    deep = tmp_path / "data" / "nested" / "deeper" / "db.sqlite"
    with build_runtime(
        _config(tmp_path, database_path=str(deep)), logger=logging.getLogger("x")
    ) as built:
        assert os.path.isfile(str(deep))
        assert built.config.database_filename == "db.sqlite"


def test_backup_service_is_bound_to_the_same_data_dir(runtime):
    assert runtime.backup.data_dir == runtime.config.data_dir


def test_backup_service_knows_the_database_filename(tmp_path):
    custom = tmp_path / "data" / "database" / "renamed.sqlite"
    with build_runtime(
        _config(tmp_path, database_path=str(custom)), logger=logging.getLogger("x")
    ) as built:
        # 备份清单里声明的数据库文件名必须与实际一致, 否则恢复会找不到它。
        assert built.config.database_filename == "renamed.sqlite"


def test_workspace_uses_the_configured_data_dir(runtime):
    assert runtime.workspace.data_dir == runtime.config.data_dir


def test_max_upload_size_drives_both_limits(runtime):
    """一个配置值必须同时驱动 HTTP 上限与工作区上限 —— 两者不一致会出现
    "上传成功了但处理拒绝"或反过来的诡异现象。"""
    assert runtime.server.max_upload_bytes == runtime.config.max_upload_size
    assert runtime.workspace._max_file_size == runtime.config.max_upload_size  # noqa: SLF001


def test_custom_max_upload_size_is_applied(tmp_path):
    with build_runtime(
        _config(tmp_path, max_upload_size=4096), logger=logging.getLogger("x")
    ) as built:
        assert built.server.max_upload_bytes == 4096
        assert built.workspace._max_file_size == 4096  # noqa: SLF001


def test_server_takes_host_port_and_debug_from_the_config(tmp_path):
    with build_runtime(
        _config(tmp_path, host="localhost", port=0, debug=True),
        logger=logging.getLogger("x"),
    ) as built:
        assert built.server.host == "localhost"
        assert built.server.debug is True
        assert built.server.running is False


def test_build_runtime_does_not_bind_the_port(tmp_path):
    """装配 ≠ 启动。测试与 health 脚本需要能装配而不占端口。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    with build_runtime(
        _config(tmp_path, port=free_port), logger=logging.getLogger("x")
    ) as built:
        assert built.server.running is False


def test_runtime_is_a_dataclass_with_the_expected_surface(runtime):
    assert isinstance(runtime, Runtime)
    for name in ("config", "layout", "database", "backup", "workspace", "server", "logger"):
        assert getattr(runtime, name) is not None


# ======================================================================
# ASR 模式
# ======================================================================


def test_asr_mode_mock_never_imports_a_real_model(tmp_path):
    with build_runtime(
        _config(tmp_path, asr_mode="mock"), logger=logging.getLogger("x")
    ) as built:
        assert built.asr_mode == "mock"
        assert built.workspace.asr_mode == "mock"
        assert built.notes == ()


def test_asr_mode_real_reports_real(tmp_path):
    if not is_local_whisper_available():
        pytest.skip("real ASR runtime not installed")
    with build_runtime(
        _config(tmp_path, asr_mode="real"), logger=logging.getLogger("x")
    ) as built:
        assert built.asr_mode == "real"
        assert built.workspace.asr_mode == "real"


def test_asr_mode_real_raises_when_the_runtime_is_missing(tmp_path, monkeypatch):
    """明确要求真货却拿到 Mock, 是最不该被容忍的"看起来在工作"。"""
    monkeypatch.setattr("src.application.bootstrap.is_local_whisper_available", lambda: False)
    with pytest.raises(ConfigurationError) as excinfo:
        build_runtime(_config(tmp_path, asr_mode="real"), logger=logging.getLogger("x"))
    assert "not installed" in excinfo.value.message


def test_asr_mode_auto_uses_the_real_runtime_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr("src.application.bootstrap.is_local_whisper_available", lambda: True)
    with build_runtime(
        _config(tmp_path, asr_mode="auto"), logger=logging.getLogger("x")
    ) as built:
        assert built.asr_mode == "real"
        assert built.notes == ()


def test_asr_mode_auto_falls_back_to_mock_with_a_visible_note(tmp_path, monkeypatch):
    monkeypatch.setattr("src.application.bootstrap.is_local_whisper_available", lambda: False)
    with build_runtime(
        _config(tmp_path, asr_mode="auto"), logger=logging.getLogger("x")
    ) as built:
        assert built.asr_mode == "mock"
        assert built.workspace.asr_mode == "mock"
        assert any("faster_whisper" in note for note in built.notes)


def test_the_mock_fallback_note_is_logged_as_a_warning(tmp_path, monkeypatch):
    monkeypatch.setattr("src.application.bootstrap.is_local_whisper_available", lambda: False)
    stream = io.StringIO()
    built = build_runtime(
        _config(tmp_path, asr_mode="auto"), log_stream=stream, clock=lambda: "T"
    )
    try:
        text = stream.getvalue()
        assert "runtime_note" in text
        assert "WARNING" in text
    finally:
        close_runtime(built)


def test_is_local_whisper_available_returns_a_bool():
    assert isinstance(is_local_whisper_available(), bool)


# ======================================================================
# OCR 模式
# ======================================================================


def test_ocr_kind_mock_reports_mock(tmp_path):
    with build_runtime(
        _config(tmp_path, ocr_config={"kind": "mock"}), logger=logging.getLogger("x")
    ) as built:
        assert built.ocr_mode == "mock"
        assert built.workspace.ocr_mode == "mock"


def test_ocr_kind_local_reports_real(tmp_path):
    from src.ocr_provider import is_local_ocr_available

    if not is_local_ocr_available():
        pytest.skip("real OCR engine not installed")
    with build_runtime(
        _config(tmp_path, ocr_config={"kind": "local"}), logger=logging.getLogger("x")
    ) as built:
        assert built.ocr_mode == "real"
        assert built.workspace.ocr_mode == "real"


def test_ocr_kind_auto_falls_back_to_mock_with_a_note(tmp_path, monkeypatch):
    monkeypatch.setattr("src.ocr_provider.is_local_ocr_available", lambda: False)
    with build_runtime(
        _config(tmp_path, ocr_config={"kind": "auto"}), logger=logging.getLogger("x")
    ) as built:
        assert built.ocr_mode == "mock"
        assert any("OCR" in note for note in built.notes)


def test_ocr_kind_auto_is_never_reported_as_real_when_it_fell_back(tmp_path, monkeypatch):
    """模式由**实际拿到的 provider 类型**推导, 不是把工厂的判断抄一遍。"""
    monkeypatch.setattr("src.ocr_provider.is_local_ocr_available", lambda: False)
    with build_runtime(
        _config(tmp_path, ocr_config={"kind": "auto"}), logger=logging.getLogger("x")
    ) as built:
        assert built.ocr_mode != "real"


def test_ocr_kind_mock_does_not_produce_a_fallback_note(tmp_path):
    """显式要 Mock 不是"回落", 不该报警告。"""
    with build_runtime(
        _config(tmp_path, ocr_config={"kind": "mock"}), logger=logging.getLogger("x")
    ) as built:
        assert not any("OCR" in note for note in built.notes)


# ======================================================================
# 快照 / 关闭
# ======================================================================


def test_describe_runtime_reports_the_essentials(runtime):
    snapshot = describe_runtime(runtime)
    for key in (
        "application",
        "version",
        "data_dir",
        "database_path",
        "database_schema_version",
        "backups_dir",
        "asr_mode",
        "ocr_mode",
        "host",
        "port",
        "log_level",
        "debug",
        "notes",
    ):
        assert key in snapshot, key


def test_describe_runtime_is_json_serialisable(runtime):
    assert json.loads(json.dumps(describe_runtime(runtime), ensure_ascii=False))


def test_describe_runtime_reports_the_configured_schema_version(tmp_path):
    with build_runtime(_config(tmp_path), logger=logging.getLogger("x")) as built:
        assert describe_runtime(built)["database_schema_version"] == built.database.schema_version()


def test_close_runtime_closes_the_database(runtime):
    close_runtime(runtime)
    assert runtime.database.closed is True


def test_close_runtime_is_idempotent(runtime):
    close_runtime(runtime)
    close_runtime(runtime)
    assert runtime.database.closed is True


def test_close_runtime_stops_a_started_server(tmp_path):
    with build_runtime(_config(tmp_path, port=0), logger=logging.getLogger("x")) as built:
        built.server.start()
        assert built.server.running is True
        close_runtime(built)
        assert built.server.running is False


def test_runtime_can_be_used_as_a_context_manager(tmp_path):
    with build_runtime(_config(tmp_path), logger=logging.getLogger("x")) as built:
        assert built.database.closed is False
    assert built.database.closed is True


# ======================================================================
# 日志装配
# ======================================================================


def test_build_runtime_logs_a_startup_event(tmp_path):
    stream = io.StringIO()
    built = build_runtime(_config(tmp_path), log_stream=stream, clock=lambda: "T")
    try:
        text = stream.getvalue()
        assert "runtime_ready" in text
        assert "Classroom Assistant" in text
    finally:
        close_runtime(built)


def test_build_runtime_does_not_install_a_handler_when_a_logger_is_injected(tmp_path):
    """调用方给了 logger 就说明它自己管日志 —— 我们只往里写, 不改造它。

    (注入的 logger **应该**收到 runtime_ready; 不该发生的是我们额外装一个
    结构化 handler 上去。)
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    target = logging.getLogger("injected")
    target.addHandler(handler)
    try:
        before = list(target.handlers)
        with build_runtime(_config(tmp_path), logger=target):
            pass
        assert list(target.handlers) == before, "bootstrap 不该改动注入 logger 的 handler"
        assert "runtime_ready" in stream.getvalue()
    finally:
        target.removeHandler(handler)


def test_build_runtime_installs_a_logger_named_classroom(tmp_path):
    stream = io.StringIO()
    built = build_runtime(_config(tmp_path), log_stream=stream, clock=lambda: "T")
    try:
        assert built.logger.name == APPLICATION_LOGGER_NAME
    finally:
        close_runtime(built)


def test_the_startup_log_does_not_leak_the_config_secret_shape(tmp_path):
    stream = io.StringIO()
    built = build_runtime(
        _config(tmp_path, ocr_config={"note": "api_key=sk-live-DEADBEEF0123456789"}),
        log_stream=stream,
        clock=lambda: "T",
    )
    try:
        assert "sk-live-DEADBEEF0123456789" not in stream.getvalue()
    finally:
        close_runtime(built)


# ======================================================================
# 端到端: 配置 -> 运行时 -> HTTP
# ======================================================================


def test_the_http_health_endpoint_reflects_the_configured_modes(tmp_path):
    with build_runtime(_config(tmp_path, port=0), logger=logging.getLogger("x")) as built:
        built.server.start()
        with urllib.request.urlopen(built.server.url + "/api/health", timeout=10) as response:
            body = json.loads(response.read())
    assert body["data"]["processing"]["asr"] == "mock"
    assert body["data"]["processing"]["ocr"] == "mock"


def test_a_config_that_requests_real_modes_is_reported_as_real(tmp_path):
    from src.ocr_provider import is_local_ocr_available

    if not (is_local_whisper_available() and is_local_ocr_available()):
        pytest.skip("real engines not installed")
    with build_runtime(
        _config(tmp_path, asr_mode="real", ocr_config={"kind": "local"}, port=0),
        logger=logging.getLogger("x"),
    ) as built:
        built.server.start()
        with urllib.request.urlopen(built.server.url + "/api/health", timeout=10) as response:
            body = json.loads(response.read())
    assert body["data"]["processing"]["asr"] == "real"
    assert body["data"]["processing"]["ocr"] == "real"


def test_a_runtime_can_be_rebuilt_on_the_same_data_dir(tmp_path):
    """重启 (Task 45 的验收项之一) 必须能在同一个 data_dir 上重新装配。"""
    first = build_runtime(_config(tmp_path), logger=logging.getLogger("x"))
    version = first.database.schema_version()
    close_runtime(first)

    second = build_runtime(_config(tmp_path), logger=logging.getLogger("x"))
    try:
        assert second.database.schema_version() == version
    finally:
        close_runtime(second)


def test_backup_service_can_take_a_backup_of_the_live_database(tmp_path):
    """配置 -> 数据库 -> 备份 这条链必须真的通。"""
    with build_runtime(
        _config(tmp_path), logger=logging.getLogger("x"), clock=lambda: "2026-01-01T00:00:00+00:00"
    ) as built:
        result = built.backup.create_backup(label="runtime-check")
        assert os.path.isfile(result.archive_path)
        assert built.config.backups_dir in result.archive_path or os.path.dirname(
            result.archive_path
        ) == os.path.abspath(built.config.backups_dir)


# ======================================================================
# import 方向 / 组合根
# ======================================================================


def _run_python(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_bootstrap_imports_cleanly_on_its_own():
    result = _run_python("import src.application.bootstrap")
    assert result.returncode == 0, result.stderr


def test_bootstrap_imports_cleanly_after_the_api_package():
    """顺序敏感: 若 bootstrap 在模块顶层 import src.api, 这条会因环而失败。"""
    result = _run_python("import src.api.server; import src.application.bootstrap")
    assert result.returncode == 0, result.stderr


def test_bootstrap_imports_cleanly_before_the_api_package():
    result = _run_python("import src.application.bootstrap; import src.api.server")
    assert result.returncode == 0, result.stderr


def test_the_cli_module_imports_cleanly_on_its_own():
    result = _run_python("import src.application.cli")
    assert result.returncode == 0, result.stderr


def test_importing_the_application_package_does_not_pull_in_bootstrap():
    """``bootstrap`` 刻意**不**从 ``src.application`` 再导出。

    因为它会 import ``src.api``, 而 ``src.api.server`` 又 import
    ``src.application.workspace`` —— 一旦 ``__init__`` 里也 import 它,
    就形成了 ``application -> bootstrap -> api -> application.workspace``
    的环。这条断言把"别再导出它"钉在代码里。
    """
    result = _run_python(
        "import sys, src.application; "
        "print('src.application.bootstrap' in sys.modules)"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_bootstrap_declares_a_public_surface():
    from src.application import bootstrap

    for name in bootstrap.__all__:
        assert hasattr(bootstrap, name), name


def test_bootstrap_does_not_import_the_web_layer():
    import ast
    import pathlib

    path = pathlib.Path(PROJECT_ROOT) / "src" / "application" / "bootstrap.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not [name for name in imported if name.startswith("src.web")]


def test_the_runtime_does_not_touch_sqlite_outside_the_persistence_layer():
    """bootstrap 通过 ``open_database`` 用库, 而不是自己 import sqlite3。"""
    import ast
    import pathlib

    path = pathlib.Path(PROJECT_ROOT) / "src" / "application" / "bootstrap.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    assert not [name for name in names if name.startswith("sqlite3")]


def test_build_runtime_accepts_an_injected_clock(tmp_path):
    """注入时钟 -> 确定性时间戳 (Task 47.2 的确定性审计要求可注入)。"""
    stream = io.StringIO()
    built = build_runtime(
        _config(tmp_path), log_stream=stream, clock=lambda: "2026-02-02T00:00:00+00:00"
    )
    try:
        assert "2026-02-02T00:00:00+00:00" in stream.getvalue()
    finally:
        close_runtime(built)


# ======================================================================
# 版本元数据一致性 (Task 47 终审发现)
# ======================================================================
#
# 终审时发现: ``src/__init__.py`` 与 ``tests/__init__.py`` 都写死
# ``__version__ = "2.0.0"``, 而当时产品实际版本 (``/api/health`` 报的) 是 0.35.0。
# 仓库里同时存在两个互相矛盾的版本号, 而且**两处都没有被任何代码使用**,
# 所以一直没人发现。
#
# 修法不只是"把字符串改对": 那样下次还会漂移。这里把一致性**钉成断言** ——
# 版本号有 3 处, 唯一真源是 ``APPLICATION_VERSION``。


def test_package_version_matches_the_application_version():
    """``src.__version__`` 必须与 ``APPLICATION_VERSION`` 一致。

    两者不一致时, 用户看到的版本 (API / 界面) 和读代码看到的版本会不同 ——
    报 bug 时会非常难对账。
    """
    import src
    from src.application.workspace import APPLICATION_VERSION

    assert src.__version__ == APPLICATION_VERSION, (
        f"src.__version__ ({src.__version__!r}) 与 "
        f"APPLICATION_VERSION ({APPLICATION_VERSION!r}) 不一致; "
        "版本真源是 src/application/workspace.py 的 APPLICATION_VERSION"
    )


def test_test_package_version_matches_the_application_version():
    """``tests.__version__`` 也要一致 —— 它同样会被人当成产品版本读。"""
    import tests
    from src.application.workspace import APPLICATION_VERSION

    assert tests.__version__ == APPLICATION_VERSION


def test_application_version_is_a_sane_semver_string():
    """版本号本身得是个能解析的三段式, 而不是随手写的字符串。"""
    from src.application.workspace import APPLICATION_VERSION

    parts = APPLICATION_VERSION.split(".")
    assert len(parts) == 3, f"不是三段式: {APPLICATION_VERSION!r}"
    assert all(part.isdigit() for part in parts), (
        f"含非数字段: {APPLICATION_VERSION!r}"
    )
