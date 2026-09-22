# -*- coding: utf-8 -*-
"""一键启动器 (Task 46 — Windows Packaging / One-Command Startup)。

目标: 普通用户双击 ``start.bat`` 即可在本机起一个本地 HTTP 服务, 浏览器
自动打开 ``http://127.0.0.1:<port>``。本模块是启动逻辑的**唯一**库实现,
``scripts/start.bat`` / ``stop.bat`` / ``health.bat`` 只是调它的薄壳。

设计要点 (与 cli.py 的关系)
--------------------------------------------------------------------
- ``cli.py`` 解析 argv -> 配置 -> 装配运行时; 本模块在其之上封装
  "启动 / 停止 / 健康检查 / 环境自检 / 浏览器打开 / PID 文件管理"。
- 端口占用的报错统一走 :class:`PortInUseError`, 信息是
  ``Port XXXX is already in use.`` (不含错误码前缀, 普通用户友好)。
- ``stop`` **不信任 PID 文件**: 操作系统会复用 PID, 服务异常退出后的残留标记
  可能指向无关程序, 直接 ``taskkill /F`` 会误杀用户的其他程序。因此杀之前先
  用端口标记握手一次 ``/api/health`` (见 :func:`is_our_service`), 确认对面
  是本应用; 确认不了就只清理陈旧标记并报错。``stop --force`` 跳过该确认。

Windows 特化
--------------------------------------------------------------------
- 数据目录可含空格 / Unicode (``Álgebra`` 之类); 所有文件/子进程操作用
  传入的绝对路径, 不做任何依赖当前代码页的编码假设。
- 浏览器打开可注入 (测试里换成哑实现), 避免测试真的拉起系统浏览器。
- 进程停止用 ``os.kill(pid, SIGTERM)`` (Windows 上等价于 TerminateProcess),
  并提供回退到 ``taskkill``。
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from src.application.config import AppConfig
from src.application.errors import ApplicationError, PortInUseError
from src.application.logging_setup import get_logger
from src.application.workspace import APPLICATION_NAME, APPLICATION_VERSION

__all__ = [
    "Launcher",
    "EnvironmentReport",
    "CheckResult",
    "LauncherError",
    "find_running_pid",
    "read_running_port",
    "kill_pid",
    "is_port_free",
    "is_our_service",
    "validate_environment",
    "main",
]

#: 进程模型下, 运行态文件落在 data_dir 下的这个子目录。
RUN_DIR = ".run"
PID_FILENAME = "launcher.pid"
PORT_FILENAME = "launcher.port"

#: 本项目要求的最低 Python 版本 (3.10 起引入的语法/语义都在用)。
_MIN_PYTHON = (3, 10)

#: 启动时必须可导入的核心依赖 (缺失即无法运行)。
_REQUIRED_IMPORTS = (
    "src.api",
    "src.api.server",
    "src.persistence",
    "src.backup",
    "src.application.bootstrap",
)
#: 可选重型依赖 —— 缺失时回落 Mock (auto 模式), 不算启动失败。
_OPTIONAL_IMPORTS = ("faster_whisper", "onnxruntime")

BrowserOpener = Callable[[str], None]


class LauncherError(Exception):
    """启动器自身可预期的错误 (与业务 ApplicationError 区分)。"""


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    #: warning 类检查 (如模型回落 Mock) 不计入硬性失败, 只提示。
    warning: bool = False


@dataclass
class EnvironmentReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if not c.warning)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [
                {"name": c.name, "ok": c.ok, "warning": c.warning, "detail": c.detail}
                for c in self.checks
            ],
        }


# ----------------------------------------------------------------------
# 控制台输出 (Windows 编码安全)
# ----------------------------------------------------------------------


def _emit_to(stream: Any, text: str) -> None:
    line = text if text.endswith("\n") else text + "\n"
    try:
        stream.write(line)
    except UnicodeEncodeError:
        # 控制台是 GBK/cp1252 而内容含重音字符时, 降级替换而非崩。
        encoding = getattr(stream, "encoding", None) or "utf-8"
        stream.write(line.encode(encoding, "replace").decode(encoding, "replace"))
    flush = getattr(stream, "flush", None)
    if callable(flush):
        flush()


def _emit(text: str) -> None:
    _emit_to(sys.stdout, text)


def _emit_error(text: str) -> None:
    _emit_to(sys.stderr, text)


# ----------------------------------------------------------------------
# 进程辅助
# ----------------------------------------------------------------------


def _run_dir(data_dir: str) -> str:
    return os.path.join(os.path.abspath(data_dir), RUN_DIR)


def _pid_path(data_dir: str) -> str:
    return os.path.join(_run_dir(data_dir), PID_FILENAME)


def _port_path(data_dir: str) -> str:
    return os.path.join(_run_dir(data_dir), PORT_FILENAME)


def write_runtime_markers(data_dir: str, *, pid: int, port: int) -> None:
    """写 PID / 端口标记文件 (供 ``stop`` 找到运行中的进程)。"""
    run = _run_dir(data_dir)
    os.makedirs(run, exist_ok=True)
    with open(_pid_path(data_dir), "w", encoding="utf-8") as handle:
        handle.write(str(pid))
    with open(_port_path(data_dir), "w", encoding="utf-8") as handle:
        handle.write(str(port))


def remove_runtime_markers(data_dir: str) -> None:
    for path in (_pid_path(data_dir), _port_path(data_dir)):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:  # pragma: no cover - 清理尽力而为
            pass


def find_running_pid(data_dir: str) -> Optional[int]:
    """读 PID 标记文件; 文件不存在或内容非法时返回 ``None``。"""
    path = _pid_path(data_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read().strip()
        return int(text)
    except (OSError, ValueError):
        return None


def read_running_port(data_dir: str) -> Optional[int]:
    """读端口标记文件; 文件不存在或内容非法时返回 ``None``。

    与 :func:`find_running_pid` 配对使用: ``stop`` 需要凭这个端口向服务
    握手确认身份 (见 :func:`is_our_service`)。
    """
    path = _port_path(data_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            port = int(handle.read().strip())
    except (OSError, ValueError):
        return None
    return port if 0 < port <= 65535 else None


def _process_alive(pid: int) -> bool:
    """进程是否还活着 (跨平台, 不发送任何信号)。"""
    if os.name == "nt":
        # Windows 上用 tasklist 判定最可靠 (os.kill(pid, 0) 语义不稳)。
        # 不解码文本: 非 UTF-8 代码页会导致 reader 线程抛 UnicodeDecodeError。
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                check=False,
            ).stdout
        except OSError:  # pragma: no cover - 极端环境
            return False
        return out is not None and str(pid).encode("ascii") in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def kill_pid(pid: int, *, timeout: float = 5.0) -> bool:
    """终止进程; 成功返回 True。

    Windows 上 ``os.kill(pid, SIGTERM)`` 对非控制台子进程常常**无效**
    (它走 console-ctrl-event, 子进程未必接管), 所以直接用 ``taskkill /F``;
    POSIX 上用 ``SIGTERM``。
    """
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                check=False,
                capture_output=True,
            )
        except OSError:  # pragma: no cover - 极端环境
            pass
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True  # 进程已不在, 视为已停止
        except OSError:  # pragma: no cover - 回退
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            return True
        time.sleep(0.05)
    return False  # pragma: no cover - 超时, 进程仍存活


# ----------------------------------------------------------------------
# 端口探测
# ----------------------------------------------------------------------


def _nearest_existing_writable(path: str) -> Optional[str]:
    """向上找到 ``path`` 最近的、已存在且可写的祖先目录; 找不到返回 ``None``。"""
    cur = os.path.abspath(path)
    while True:
        if os.path.isdir(cur):
            return cur if os.access(cur, os.W_OK) else None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def is_port_free(host: str, port: int) -> bool:
    """探测端口是否空闲 (best-effort)。

    ``port == 0`` 表示交给 OS 分配, 永远算空闲。探测用独立 socket,
    关闭时不复用地址, 因此能较可靠地发现已占用端口 (真实启动仍由
    :class:`ApiServer` 最终裁决, 给出准确的友好报错)。
    """
    if port == 0:
        return True
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    probe = socket.socket(family, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        probe.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _probe_host(host: str) -> str:
    """把通配绑定地址换成可连接的地址 (``0.0.0.0`` 自身连不上)。"""
    if host in ("", "0.0.0.0"):
        return "127.0.0.1"
    if host == "::":
        return "::1"
    return host


def _contains_application_name(node: Any) -> bool:
    """递归查 JSON 里是否出现本应用名 (不依赖响应包装格式)。"""
    if isinstance(node, dict):
        if node.get("application") == APPLICATION_NAME:
            return True
        return any(_contains_application_name(value) for value in node.values())
    if isinstance(node, list):
        return any(_contains_application_name(value) for value in node)
    return False


def is_our_service(host: str, port: int, *, timeout: float = 2.0) -> bool:
    """``host:port`` 上跑的是不是**本应用** (而非碰巧占用该端口的别的程序)。

    ``stop`` 只能凭 PID 文件定位进程, 而操作系统会**复用 PID**: 服务异常退出
    (例如用户直接关掉 cmd 窗口) 后残留的标记, 其 PID 可能已属于无关程序,
    此时 ``taskkill /F`` 会误杀用户的其他程序。因此杀之前先握一次
    ``/api/health``, 确认对面是本应用。
    """
    import json
    import urllib.request

    if port <= 0:
        return False
    url = f"http://{_probe_host(host)}:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - 连不上 / 非 HTTP / 非 JSON 都算"不是我们"
        return False
    return _contains_application_name(payload)


def _marker_points_at_our_service(config: AppConfig) -> bool:
    """PID 标记是否真的指向本应用 (端口文件 + 健康握手双重确认)。"""
    port = read_running_port(config.data_dir)
    if port is None:
        return False
    return is_our_service(config.host, port)


# ----------------------------------------------------------------------
# 环境自检
# ----------------------------------------------------------------------


def validate_environment(config: AppConfig) -> EnvironmentReport:
    """启动前自检: Python / 依赖 / 数据目录 / 数据库 / 模型 / 端口。

    返回 :class:`EnvironmentReport`; 调用方据 ``report.ok`` 决定能否继续。
    """
    report = EnvironmentReport()

    # 1. Python 运行时
    ver = sys.version_info
    report.checks.append(
        CheckResult("python", ok=ver >= _MIN_PYTHON, detail=f"{ver.major}.{ver.minor}.{ver.micro}")
    )

    # 2. 依赖 (必需 vs 可选)
    missing_req: list[str] = []
    for mod in _REQUIRED_IMPORTS:
        try:
            __import__(mod)
        except Exception:  # noqa: BLE001 - 任何导入失败都记
            missing_req.append(mod)
    report.checks.append(
        CheckResult(
            "dependencies",
            ok=not missing_req,
            detail="ok" if not missing_req else f"missing: {', '.join(missing_req)}",
        )
    )

    missing_opt: list[str] = []
    for mod in _OPTIONAL_IMPORTS:
        try:
            __import__(mod)
        except Exception:  # noqa: BLE001
            missing_opt.append(mod)
    report.checks.append(
        CheckResult(
            "optional-dependencies",
            ok=True,
            warning=bool(missing_opt),
            detail="ok" if not missing_opt else f"fallback to Mock: {', '.join(missing_opt)}",
        )
    )

    # 3. 数据目录
    data_dir = os.path.abspath(config.data_dir)
    if os.path.isdir(data_dir):
        writable = os.access(data_dir, os.W_OK)
        report.checks.append(
            CheckResult(
                "data directory",
                ok=writable,
                detail=data_dir if writable else f"not writable: {data_dir}",
            )
        )
    else:
        parent = os.path.dirname(data_dir)
        if parent and os.path.isdir(parent) and os.access(parent, os.W_OK):
            report.checks.append(
                CheckResult("data directory", ok=True, detail=f"will be created: {data_dir}")
            )
        else:
            report.checks.append(
                CheckResult(
                    "data directory",
                    ok=False,
                    detail=f"cannot create data_dir (parent not writable): {parent}",
                )
            )

    # 4. 数据库 (database_path 必须在 data_dir 内且目录可写; 目录尚不存在时
    #    向上找到可写的祖先即可, 启动时会自动建出来)
    db_path = os.path.abspath(config.database_path)
    db_dir = os.path.dirname(db_path)
    inside = db_path == data_dir or db_path.startswith(data_dir + os.sep)
    if not inside:
        report.checks.append(
            CheckResult("database", ok=False, detail=f"database_path not inside data_dir: {db_path}")
        )
    else:
        ancestor = _nearest_existing_writable(db_dir)
        if ancestor is None:
            report.checks.append(
                CheckResult("database", ok=False, detail=f"database directory not writable: {db_dir}")
            )
        else:
            detail = db_path if os.path.isdir(db_dir) else f"directory will be created: {db_dir}"
            report.checks.append(CheckResult("database", ok=True, detail=detail))

    # 5. 模型可用性
    from src.application.bootstrap import is_local_whisper_available
    from src.ocr_provider import is_local_ocr_available

    asr = is_local_whisper_available()
    ocr = is_local_ocr_available()
    if config.asr_mode == "real" and not asr:
        report.checks.append(
            CheckResult("model availability", ok=False, detail="asr_mode='real' but faster-whisper not installed")
        )
    elif bool(config.ocr_config.get("require_real")) and not ocr:
        report.checks.append(
            CheckResult("model availability", ok=False, detail="ocr require_real but no local OCR engine")
        )
    else:
        report.checks.append(
            CheckResult(
                "model availability",
                ok=True,
                warning=(not asr) or (not ocr),
                detail=f"asr={'real' if asr else 'mock'} ocr={'real' if ocr else 'mock'}",
            )
        )

    # 6. 端口
    if is_port_free(config.host, config.port):
        report.checks.append(CheckResult("port", ok=True, detail=f"{config.host}:{config.port}"))
    else:
        report.checks.append(
            CheckResult("port", ok=False, detail=f"Port {config.port} is already in use.")
        )

    return report


# ----------------------------------------------------------------------
# 启动器
# ----------------------------------------------------------------------


class Launcher:
    """可嵌入、可测试的本地服务启动/停止封装。

    典型进程模型 (``start.bat`` 用)::

        launcher = Launcher(config)
        launcher.start(block=True)   # 起服务 + 写 PID + 开浏览器 + 阻塞
        # Ctrl+C -> stop()

    测试用非阻塞::

        launcher = Launcher(config, browser_opener=fake_open)
        launcher.start(block=False)  # 后台线程跑服务
        assert launcher.running
        launcher.stop()
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        browser_opener: Optional[BrowserOpener] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config
        self.browser_opener = browser_opener
        self.logger = logger
        self._runtime: Any = None

    # -- 健康检查 -----------------------------------------------------

    def health(self) -> dict[str, Any]:
        """装配一遍运行时并打印快照 (不绑定端口, 不写 PID 文件)。

        内部用 ``__enter__/__exit__`` 保证装配出的数据库一定被关掉。
        """
        from src.application.bootstrap import build_runtime, close_runtime, describe_runtime

        runtime = build_runtime(self.config, logger=self.logger)
        try:
            return dict(describe_runtime(runtime))
        finally:
            close_runtime(runtime)

    # -- 启动 ---------------------------------------------------------

    def start(self, *, block: bool = False) -> "Launcher":
        """装配 + 绑定端口 + (可选阻塞)。

        端口被占用时透传 :class:`PortInUseError`, 由上层打印
        ``Port XXXX is already in use.``。
        """
        from src.application.bootstrap import build_runtime, close_runtime

        self._runtime = build_runtime(self.config, logger=self.logger)
        self.logger = self._runtime.logger
        try:
            self._runtime.server.start()
        except PortInUseError:
            close_runtime(self._runtime)
            self._runtime = None
            raise

        write_runtime_markers(
            self.config.data_dir, pid=os.getpid(), port=self._runtime.server.port
        )
        url = self._runtime.server.url
        if self.browser_opener is not None:
            self.browser_opener(url)
        if self.logger is not None:
            self.logger.info("%s %s listening on %s", APPLICATION_NAME, APPLICATION_VERSION, url)

        if block:
            try:
                self._runtime.server.serve_forever()
            except KeyboardInterrupt:  # pragma: no cover - 交互停止
                pass
            finally:
                self.stop()
        return self

    @property
    def url(self) -> str:
        if self._runtime is None:
            raise LauncherError("launcher not started")
        return self._runtime.server.url

    @property
    def running(self) -> bool:
        return self._runtime is not None and bool(getattr(self._runtime.server, "running", False))

    # -- 停止 ---------------------------------------------------------

    def stop(self) -> None:
        if self._runtime is not None:
            from src.application.bootstrap import close_runtime

            try:
                close_runtime(self._runtime)
            finally:
                self._runtime = None
        remove_runtime_markers(self.config.data_dir)


# ----------------------------------------------------------------------
# 命令行入口
# ----------------------------------------------------------------------


def _open_browser(url: str) -> None:
    """用系统默认浏览器打开 URL。

    打不开浏览器 (无图形环境 / 没有默认浏览器) 不该让服务起不来 —— 此时服务
    已经绑定成功, 用户完全可以手动访问提示的地址。
    """
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - 尽力而为
        pass


def _emit_environment(report: EnvironmentReport) -> None:
    _emit("Environment check:")
    for check in report.checks:
        if check.ok:
            mark = "WARN" if check.warning else "OK  "
        else:
            mark = "FAIL"
        _emit(f"  [{mark}] {check.name}: {check.detail}")
    if not report.ok:
        _emit_error("Environment check failed. Fix the items above before starting.")


def main(argv: Optional[list[str]] = None) -> int:
    """``python -m src.application.launcher [start|stop|health] [options]``。"""
    from src.application.cli import (
        EXIT_CONFIG_ERROR,
        EXIT_OK,
        EXIT_STARTUP_ERROR,
        build_config,
        parse_cli,
    )

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        _emit_error(
            "usage: python -m src.application.launcher [start|stop|health] [options]\n"
            "       stop --force   kill the recorded PID without confirming it is ours"
        )
        return EXIT_CONFIG_ERROR

    command, rest = argv[0], argv[1:]
    # ``--force`` 是本启动器自己的开关; cli.parse_cli 不认识它, 先摘掉。
    force = "--force" in rest
    rest = [item for item in rest if item != "--force"]

    try:
        options = parse_cli(rest)
        config = build_config(options)
    except ApplicationError as exc:
        _emit_error(f"[{exc.code}] {exc.message}")
        return EXIT_CONFIG_ERROR

    if command == "health":
        report = validate_environment(config)
        _emit_environment(report)
        if not report.ok:
            return EXIT_STARTUP_ERROR
        snapshot = Launcher(config).health()
        for key in sorted(snapshot):
            _emit(f"  {key:<24} = {snapshot[key]!r}")
        return EXIT_OK

    if command == "start":
        try:
            Launcher(config, browser_opener=_open_browser).start(block=True)
        except PortInUseError as exc:
            _emit_error(str(exc.message))
            return EXIT_STARTUP_ERROR
        except ApplicationError as exc:
            _emit_error(f"[{exc.code}] {exc.message}")
            return EXIT_STARTUP_ERROR
        except OSError as exc:
            _emit_error(f"startup failed: {exc}")
            return EXIT_STARTUP_ERROR
        return EXIT_OK

    if command == "stop":
        pid = find_running_pid(config.data_dir)
        if pid is None:
            _emit_error("no running instance found (no PID file)")
            return EXIT_STARTUP_ERROR
        # PID 会被操作系统复用: 标记文件可能是服务异常退出后的残留, 其 PID
        # 已属于无关程序。先握手确认, 否则拒绝杀 (--force 可跳过确认)。
        if not force and not _marker_points_at_our_service(config):
            remove_runtime_markers(config.data_dir)
            _emit_error(
                f"stale PID file removed: no {APPLICATION_NAME} service is listening on "
                f"{_probe_host(config.host)}:{config.port}. Refusing to kill PID {pid} "
                f"(that PID may have been reused by an unrelated program). "
                f"Re-run 'stop --force' to kill it anyway."
            )
            return EXIT_STARTUP_ERROR
        if kill_pid(pid):
            remove_runtime_markers(config.data_dir)
            _emit("stopped.")
            return EXIT_OK
        _emit_error(f"failed to stop process {pid}")
        return EXIT_STARTUP_ERROR

    _emit_error(f"unknown command: {command}")
    return EXIT_CONFIG_ERROR


if __name__ == "__main__":  # pragma: no cover - 手动运行
    raise SystemExit(main())
