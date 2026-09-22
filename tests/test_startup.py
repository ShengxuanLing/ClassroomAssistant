# -*- coding: utf-8 -*-
"""Task 46 — Windows Packaging / One-Command Startup 测试。

覆盖规范点名的 Windows 特化项:
- paths with spaces
- Unicode paths
- non-ASCII filenames (数据目录路径)
- long filenames
- Windows temp directory
- process shutdown
- browser opening
- UTF-8 console

外加: 环境自检六项的各自断言、端口占用的友好报错、PID/端口文件往返、
bat 脚本内容断言、``python -m src.application.launcher`` 子命令。
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time

import pytest

from src.application.config import AppConfig
from src.application.errors import PortInUseError
from src.application.launcher import (
    RUN_DIR,
    Launcher,
    LauncherError,
    _emit_to,
    find_running_pid,
    is_our_service,
    is_port_free,
    kill_pid,
    read_running_port,
    remove_runtime_markers,
    validate_environment,
    write_runtime_markers,
)
from src.application.launcher import main as launcher_main


def _asr_installed() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


@pytest.fixture
def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def base_config(tmp_path, free_port) -> AppConfig:
    return AppConfig.load(
        cwd=str(tmp_path),
        cli_overrides={"port": free_port, "data_dir": str(tmp_path / "classroom-data")},
    )


# ----------------------------------------------------------------------
# 健康检查
# ----------------------------------------------------------------------


def test_health_returns_expected_keys(base_config) -> None:
    snap = Launcher(base_config).health()
    for key in ("application", "version", "data_dir", "port", "host", "asr_mode", "ocr_mode"):
        assert key in snap


def test_health_does_not_bind_port(base_config) -> None:
    Launcher(base_config).health()
    # 健康检查不占端口, 端口在 health 之后仍空闲。
    assert is_port_free(base_config.host, base_config.port)


def test_health_does_not_write_pid_file(base_config) -> None:
    Launcher(base_config).health()
    assert find_running_pid(base_config.data_dir) is None


# ----------------------------------------------------------------------
# 启动 / 停止
# ----------------------------------------------------------------------


def test_start_nonblocking_runs(base_config) -> None:
    launcher = Launcher(base_config)
    try:
        launcher.start(block=False)
        assert launcher.running
    finally:
        launcher.stop()


def test_start_url(base_config) -> None:
    launcher = Launcher(base_config)
    try:
        launcher.start(block=False)
        assert launcher.url == f"http://127.0.0.1:{base_config.port}"
    finally:
        launcher.stop()


def test_browser_opener_called_with_url(base_config) -> None:
    calls: list[str] = []
    launcher = Launcher(base_config, browser_opener=calls.append)
    try:
        launcher.start(block=False)
        assert calls == [launcher.url]
    finally:
        launcher.stop()


def test_browser_opener_called_once(base_config) -> None:
    import unittest.mock as mock

    opener = mock.Mock()
    launcher = Launcher(base_config, browser_opener=opener)
    try:
        launcher.start(block=False)
    finally:
        launcher.stop()
    opener.assert_called_once()


def test_no_browser_opener_no_error(base_config) -> None:
    launcher = Launcher(base_config)  # browser_opener=None
    try:
        launcher.start(block=False)
        assert launcher.running
    finally:
        launcher.stop()


def test_stop_shuts_down_and_removes_markers(base_config) -> None:
    launcher = Launcher(base_config)
    launcher.start(block=False)
    assert os.path.isfile(os.path.join(base_config.data_dir, RUN_DIR, "launcher.pid"))
    launcher.stop()
    assert not launcher.running
    assert find_running_pid(base_config.data_dir) is None


def test_stop_idempotent(base_config) -> None:
    launcher = Launcher(base_config)
    launcher.start(block=False)
    launcher.stop()
    launcher.stop()  # 二次 stop 不报错
    assert not launcher.running


# ----------------------------------------------------------------------
# 端口占用 (Graceful failure)
# ----------------------------------------------------------------------


def test_port_in_use_error_message() -> None:
    exc = PortInUseError(8765)
    assert exc.message == "Port 8765 is already in use."
    assert exc.port == 8765


def test_launcher_port_in_use_raises(base_config) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    sock.bind(("127.0.0.1", base_config.port))
    try:
        with pytest.raises(PortInUseError):
            Launcher(base_config).start(block=False)
    finally:
        sock.close()


def test_cli_emits_port_in_use_message(base_config, capsys) -> None:
    from src.application.cli import CliOptions, EXIT_STARTUP_ERROR, run

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", base_config.port))
    try:
        code = run(
            CliOptions(overrides={"port": base_config.port, "data_dir": base_config.data_dir}),
            cwd=os.path.dirname(base_config.data_dir),
        )
    finally:
        sock.close()
    assert code == EXIT_STARTUP_ERROR
    err = capsys.readouterr().err
    assert "Port" in err and "is already in use." in err


# ----------------------------------------------------------------------
# 端口探测
# ----------------------------------------------------------------------


def test_is_port_free_free(free_port) -> None:
    assert is_port_free("127.0.0.1", free_port)


def test_is_port_free_port_zero() -> None:
    assert is_port_free("127.0.0.1", 0)


def test_is_port_free_occupied(free_port) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", free_port))
    try:
        assert not is_port_free("127.0.0.1", free_port)
    finally:
        sock.close()


# ----------------------------------------------------------------------
# 环境自检 (六项的各自断言)
# ----------------------------------------------------------------------


def _check(report, name):
    return next(c for c in report.checks if c.name == name)


def test_environment_python_ok(base_config) -> None:
    assert _check(validate_environment(base_config), "python").ok


def test_environment_dependencies_ok(base_config) -> None:
    assert _check(validate_environment(base_config), "dependencies").ok


def test_environment_data_dir_writable(base_config) -> None:
    assert _check(validate_environment(base_config), "data directory").ok


def test_environment_database_ok(base_config) -> None:
    assert _check(validate_environment(base_config), "database").ok


def test_environment_port_free(base_config) -> None:
    assert _check(validate_environment(base_config), "port").ok


def test_environment_port_occupied(base_config) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", base_config.port))
    try:
        report = validate_environment(base_config)
    finally:
        sock.close()
    port_check = _check(report, "port")
    assert not port_check.ok
    assert port_check.detail == f"Port {base_config.port} is already in use."


def test_environment_model_fallback_warning(base_config) -> None:
    report = validate_environment(base_config)
    model = _check(report, "model availability")
    assert model.ok  # auto 回落 Mock 是可接受的, 不算失败
    if not _asr_installed():
        assert model.warning


def test_environment_asr_real_missing_fails(tmp_path, free_port) -> None:
    if _asr_installed():
        pytest.skip("faster-whisper is installed; cannot exercise the missing path")
    cfg = AppConfig.load(
        cwd=str(tmp_path),
        cli_overrides={"port": free_port, "data_dir": str(tmp_path / "data"), "asr_mode": "real"},
    )
    report = validate_environment(cfg)
    assert not _check(report, "model availability").ok


def test_environment_report_ok_true(base_config) -> None:
    assert validate_environment(base_config).ok is True


def test_environment_report_as_dict(base_config) -> None:
    data = validate_environment(base_config).as_dict()
    assert "ok" in data and "checks" in data
    assert all("name" in c and "ok" in c for c in data["checks"])


# ----------------------------------------------------------------------
# PID / 端口文件
# ----------------------------------------------------------------------


def test_pid_file_roundtrip(tmp_path) -> None:
    data_dir = str(tmp_path / "dd")
    write_runtime_markers(data_dir, pid=12345, port=8765)
    assert find_running_pid(data_dir) == 12345
    remove_runtime_markers(data_dir)
    assert find_running_pid(data_dir) is None


# ----------------------------------------------------------------------
# 进程停止 (process shutdown)
# ----------------------------------------------------------------------


def test_kill_pid_terminates_process() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert proc.poll() is None
        assert kill_pid(proc.pid)
        proc.wait(timeout=5)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:  # pragma: no cover - 兜底
            proc.kill()


def test_kill_pid_invalid_pid() -> None:
    # 不存在的 pid 应安全返回 True (不抛异常)。
    assert kill_pid(9_999_999) is True


# ----------------------------------------------------------------------
# Windows 特化: 路径
# ----------------------------------------------------------------------


def test_data_dir_with_spaces(tmp_path, free_port) -> None:
    data_dir = str(tmp_path / "my classroom data")
    cfg = AppConfig.load(cwd=str(tmp_path), cli_overrides={"port": free_port, "data_dir": data_dir})
    launcher = Launcher(cfg)
    try:
        launcher.start(block=False)
        assert launcher.running
    finally:
        launcher.stop()


def test_data_dir_with_unicode(tmp_path, free_port) -> None:
    data_dir = str(tmp_path / "Álgebra 代数")
    cfg = AppConfig.load(cwd=str(tmp_path), cli_overrides={"port": free_port, "data_dir": data_dir})
    launcher = Launcher(cfg)
    try:
        launcher.start(block=False)
        assert launcher.running
    finally:
        launcher.stop()


def test_data_dir_long_path(tmp_path, free_port) -> None:
    long_name = "longpathsegment" * 8  # ~120 字符, 仍远低于系统上限
    data_dir = str(tmp_path / long_name)
    cfg = AppConfig.load(cwd=str(tmp_path), cli_overrides={"port": free_port, "data_dir": data_dir})
    launcher = Launcher(cfg)
    try:
        launcher.start(block=False)
        assert launcher.running
    finally:
        launcher.stop()


def test_windows_temp_dir(free_port) -> None:
    with tempfile.TemporaryDirectory() as td:
        cfg = AppConfig.load(
            cwd=td, cli_overrides={"port": free_port, "data_dir": os.path.join(td, "data")}
        )
        launcher = Launcher(cfg)
        try:
            launcher.start(block=False)
            assert launcher.running
        finally:
            launcher.stop()


# ----------------------------------------------------------------------
# UTF-8 控制台
# ----------------------------------------------------------------------


def test_utf8_console_emit() -> None:
    class FailingStream:
        # 模拟编码为 ascii 的真实控制台: 收到非 ASCII 才抛 UnicodeEncodeError,
        # 收到纯 ASCII (降级替换后的内容) 则正常写入。
        encoding = "ascii"

        def write(self, text):  # noqa: A003
            if any(ord(ch) > 127 for ch in text):
                raise UnicodeEncodeError("ascii", text, 0, 1, "boom")
            self.written = text

        def flush(self):
            pass

    # 编码不了的字符必须降级替换, 绝不抛 UnicodeEncodeError。
    _emit_to(FailingStream(), "Álgebra 代数 naïve café")


# ----------------------------------------------------------------------
# 子命令 (bat 等价路径)
# ----------------------------------------------------------------------


def test_main_health_command(tmp_path, free_port, capsys) -> None:
    data_dir = str(tmp_path / "data")
    code = launcher_main(["health", "--data-dir", data_dir, "--port", str(free_port)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Environment check" in out
    assert "application" in out


def test_main_stop_no_instance(tmp_path, capsys) -> None:
    data_dir = str(tmp_path / "data")
    code = launcher_main(["stop", "--data-dir", data_dir])
    assert code != 0
    assert "no running instance" in capsys.readouterr().err


# ----------------------------------------------------------------------
# bat 脚本内容断言
# ----------------------------------------------------------------------


def test_bat_scripts_present_and_well_formed() -> None:
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
    for name in ("start.bat", "stop.bat", "health.bat"):
        path = os.path.join(base, name)
        assert os.path.isfile(path), f"missing {name}"
        content = open(path, encoding="utf-8").read()
        assert "src.application.launcher" in content
        assert name.split(".")[0] in content


# ----------------------------------------------------------------------
# PID 复用防护 (Task 47 硬化: stop 不得误杀无关进程)
# ----------------------------------------------------------------------


def _dead_port() -> int:
    """借一个当前空闲的端口号 (上面没有任何服务在监听)。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_read_running_port_roundtrip(tmp_path) -> None:
    data_dir = str(tmp_path / "dd")
    write_runtime_markers(data_dir, pid=12345, port=8765)
    assert read_running_port(data_dir) == 8765
    remove_runtime_markers(data_dir)
    assert read_running_port(data_dir) is None


def test_read_running_port_rejects_garbage(tmp_path) -> None:
    data_dir = str(tmp_path / "dd")
    run_dir = os.path.join(data_dir, RUN_DIR)
    os.makedirs(run_dir, exist_ok=True)
    for bad in ("abc", "-1", "0", "70000", ""):
        with open(os.path.join(run_dir, "launcher.port"), "w", encoding="utf-8") as handle:
            handle.write(bad)
        assert read_running_port(data_dir) is None, f"应拒绝非法端口 {bad!r}"


def test_is_our_service_false_when_nothing_listening() -> None:
    assert is_our_service("127.0.0.1", _dead_port()) is False


def test_is_our_service_detects_running_app(base_config) -> None:
    launcher = Launcher(base_config)
    try:
        launcher.start(block=False)
        assert is_our_service(base_config.host, base_config.port) is True
    finally:
        launcher.stop()


def test_stop_refuses_to_kill_reused_pid(tmp_path, capsys) -> None:
    """PID 被复用给无关程序时, stop 必须拒绝杀 (只清理陈旧标记)。

    复现路径: 用户关掉 cmd 窗口 -> 服务进程死了但 launcher.pid 残留 ->
    Windows 把该 PID 分配给别的程序 -> 双击 stop.bat。
    """
    victim = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    data_dir = str(tmp_path / "data")
    try:
        write_runtime_markers(data_dir, pid=victim.pid, port=_dead_port())
        code = launcher_main(["stop", "--data-dir", data_dir])
        assert code != 0
        err = capsys.readouterr().err
        assert "Refusing to kill" in err
        assert victim.poll() is None, "stop 误杀了 PID 被复用的无关进程"
        assert find_running_pid(data_dir) is None, "陈旧标记应被清理"
    finally:
        victim.kill()
        victim.wait(timeout=5)


def test_stop_force_kills_reused_pid(tmp_path, capsys) -> None:
    """``--force`` 是逃生门: 用户明确要求时照杀。"""
    victim = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    data_dir = str(tmp_path / "data")
    try:
        write_runtime_markers(data_dir, pid=victim.pid, port=_dead_port())
        code = launcher_main(["stop", "--force", "--data-dir", data_dir])
        assert code == 0
        assert "stopped." in capsys.readouterr().out
        victim.wait(timeout=5)
        assert victim.poll() is not None
    finally:
        if victim.poll() is None:
            victim.kill()


def test_stop_kills_running_service(tmp_path, free_port, capsys) -> None:
    """真实服务在跑时 stop 必须仍能停掉 —— 防身份确认把正常路径也挡掉。

    必须用**子进程**跑服务: ``Launcher.start`` 记录的是 ``os.getpid()``,
    在测试进程里那等于 pytest 自己, 直接 stop 会把测试进程杀掉。
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    data_dir = str(tmp_path / "data")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "src.application.launcher",
            "start",
            "--data-dir",
            data_dir,
            "--port",
            str(free_port),
        ],
        cwd=str(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and find_running_pid(data_dir) is None:
            time.sleep(0.2)
        assert find_running_pid(data_dir) is not None, "服务未能在 30s 内启动"

        code = launcher_main(["stop", "--data-dir", data_dir])
        assert code == 0
        assert "stopped." in capsys.readouterr().out
        proc.wait(timeout=10)
        assert find_running_pid(data_dir) is None
    finally:
        if proc.poll() is None:  # pragma: no cover - 兜底
            proc.kill()


# ----------------------------------------------------------------------
# 浏览器自动打开必须真的接线 (start.bat 承诺过, 但 main() 曾一直走默认 None)
# ----------------------------------------------------------------------


def test_main_start_passes_browser_opener(tmp_path, free_port, monkeypatch) -> None:
    """``start`` 子命令必须把真实 opener 传进去, 而不是只留一个没人用的参数。"""
    from src.application import launcher as launcher_mod

    captured: dict = {}

    class FakeLauncher:
        def __init__(self, config, *, browser_opener=None, logger=None):
            captured["browser_opener"] = browser_opener
            captured["logger"] = logger

        def start(self, *, block=False):
            captured["block"] = block
            return self

    monkeypatch.setattr(launcher_mod, "Launcher", FakeLauncher)
    code = launcher_main(["start", "--data-dir", str(tmp_path / "d"), "--port", str(free_port)])
    assert code == 0
    assert captured["browser_opener"] is launcher_mod._open_browser
    assert captured["block"] is True


def test_open_browser_swallows_failure(monkeypatch) -> None:
    """没有默认浏览器 / 无图形环境时不得抛异常 (服务已经起来了)。"""
    import webbrowser

    from src.application import launcher as launcher_mod

    def boom(url):  # noqa: ANN001
        raise RuntimeError("no display")

    monkeypatch.setattr(webbrowser, "open", boom)
    launcher_mod._open_browser("http://127.0.0.1:1/")
