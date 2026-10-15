# -*- coding: utf-8 -*-
"""系统托盘入口 (TASK-110 — Windows 安装包)。

安装版是 ``--noconsole`` 的窗口程序, 没有控制台可看, 启动形态因此改成
「托盘常驻 + 浏览器自动打开」。本模块是托盘形态的**唯一**实现:
冻结入口 (``packaging/entry.py``) 与手动运行 ``python -m src.application.tray``
都调 :func:`main`。

进程模型
--------------------------------------------------------------------

- HTTP 服务由 :class:`Launcher` 在后台 daemon 线程里跑
  (``ApiServer.start()`` 本来就是如此), 主线程只跑托盘事件循环
  (pystray 要求主线程) —— 不需要任何并发改造;
- 退出顺序是 ``launcher.stop()`` -> ``icon.stop()``: 先停服务 (关数据库、
  清 ``classroom-data/.run/`` 下的 PID / 端口标记), 再结束事件循环。
  ``run`` 的 ``finally`` 里还有一次 ``launcher.stop()`` 兜底 —— 事件循环
  无论因为什么结束, 标记都不会残留 (两处都幂等)。

「已经在跑」不是错误
--------------------------------------------------------------------

双击第二次是用户最自然的动作。端口被占、且握手确认对面**是本应用**时,
直接打开浏览器指向既有实例并正常退出: 不出现第二个托盘图标, 也不静默
失败。端口被别的程序占用才如实报错。

依赖缺失的可见性
--------------------------------------------------------------------

源码环境没装 ``pystray`` 时, 错误文本给出可照做的命令; 冻结产物里依赖
都在包内。启动失败在冻结态会弹系统错误框 —— 无控制台时那是用户唯一能
看到的地方 (``_report_error``)。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Any, Callable, Optional

from src.application.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_OK,
    EXIT_STARTUP_ERROR,
    build_config,
    parse_cli,
)
from src.application.config import AppConfig, is_frozen
from src.application.errors import ApplicationError, PortInUseError
from src.application.launcher import Launcher, LauncherError, is_our_service
from src.application.logging_setup import get_logger, log_event
from src.application.workspace import APPLICATION_NAME, APPLICATION_VERSION

__all__ = [
    "MENU_OPEN_INTERFACE",
    "MENU_OPEN_DATA_DIR",
    "MENU_QUIT",
    "ICON_FILENAME",
    "default_icon_path",
    "open_in_browser",
    "open_directory",
    "TrayApp",
    "main",
]

#: 托盘菜单文案 (托盘只服务本机用户, 这里刻意只用中文, 不进 Web UI 的 i18n 表)。
MENU_OPEN_INTERFACE = "打开界面"
MENU_OPEN_DATA_DIR = "打开数据目录"
MENU_QUIT = "退出"

#: 应用图标文件名 (与 ``packaging/installer.iss`` / spec 里的同一份资源)。
ICON_FILENAME = "app.ico"

#: 托盘组件在 pystray 内部的名字 (Windows 后端把它编进窗口类名,
#: 自动化验收脚本按这个前缀找窗口, 见 ``docs/installer-windows.md``)。
ICON_NAME = "classroom-assistant"


def default_icon_path() -> str:
    """应用图标路径 (源码态与冻结态同一段代码)。

    与 ``src.api.server.default_static_dir()`` 同一原理: 图标作为 datas
    收进 ``_internal/src/application/assets/``, 冻结后 ``__file__`` 仍指向
    包内路径, 因此 ``os.path.dirname(__file__)`` 推导天然成立。
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", ICON_FILENAME)


# ----------------------------------------------------------------------
# 系统动作 (可注入, 测试不碰真实桌面)
# ----------------------------------------------------------------------


def open_in_browser(url: str) -> None:
    """用系统默认浏览器打开 URL; 失败只记日志, 绝不杀死托盘。"""
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001 - 打不开浏览器不该让服务停掉
        log_event(
            get_logger("classroom.tray"),
            logging.WARNING,
            "tray_open_browser_failed",
            str(exc),
            detail={"url": url},
        )


def open_directory(path: str) -> None:
    """在系统文件管理器里打开目录 (Windows 用 ``os.startfile``)。"""
    target = os.path.abspath(path)
    if os.name == "nt":
        os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen(
        [opener, target],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _show_message_box(title: str, text: str) -> None:
    """冻结态的无控制台错误出口 (系统对话框)。

    用标准库 ``ctypes`` 直接调 user32, 不引入 tkinter —— 后者会把整个
    Tcl/Tk 链拖进安装包, 只为一个错误框不值得。
    """
    import ctypes

    # MB_OK | MB_ICONERROR
    ctypes.windll.user32.MessageBoxW(None, str(text), str(title), 0x10)


def _report_error(message: str) -> None:
    """启动失败的可见性出口, 按可靠性从高到低三档。

    1. 控制台还在 (源码态 / 终端启动) -> 打印 stderr;
    2. 冻结态 (双击启动, 没有控制台) -> 弹系统错误框;
    3. 两者都没有 -> 至少照常返回, 由日志文件兜底。
    """
    if sys.stderr is not None:
        line = message if message.endswith("\n") else message + "\n"
        try:
            sys.stderr.write(line)
            sys.stderr.flush()
        except Exception:  # noqa: BLE001 - 输出失败不能反过来杀死进程
            pass
    if is_frozen() and os.name == "nt":
        _show_message_box(APPLICATION_NAME, message)


def _interface_url(config: AppConfig) -> str:
    """``config`` 指向的界面地址 (服务尚未由本进程持有时用)。

    规则与 ``ApiServer.url`` / ``launcher._probe_host`` 一致: 通配绑定
    地址换成本机回环, 其余原样。
    """
    host = config.host
    if host in ("", "0.0.0.0"):
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    return f"http://{host}:{config.port}"


# ----------------------------------------------------------------------
# 托盘应用
# ----------------------------------------------------------------------


class TrayApp:
    """托盘应用本体 (依赖全部可注入, 便于无 GUI 测试)。

    真实运行 (:meth:`run`) 的次序: 起服务 -> 建图标 -> ``icon.run()``
    (阻塞主线程) -> 退出时兜底停服务。
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        launcher: Optional[Launcher] = None,
        browser_opener: Optional[Callable[[str], None]] = None,
        directory_opener: Optional[Callable[[str], None]] = None,
        icon_path: Optional[str] = None,
    ) -> None:
        self.config = config
        self.browser_opener = browser_opener if browser_opener is not None else open_in_browser
        self.directory_opener = (
            directory_opener if directory_opener is not None else open_directory
        )
        self.icon_path = icon_path if icon_path is not None else default_icon_path()
        # 托盘启动也必须自动开浏览器 —— ``Launcher`` 的默认是"不开",
        # 所以这里显式把自己的 opener 传进去。
        self.launcher = (
            launcher
            if launcher is not None
            else Launcher(config, browser_opener=self.browser_opener)
        )
        self._logger = get_logger("classroom.tray")
        self._icon: Any = None

    # -- 菜单动作 -------------------------------------------------------

    def open_interface(self) -> None:
        """打开浏览器指向正在运行的界面 (菜单默认项, 双击图标触发)。"""
        try:
            url = self.launcher.url
        except LauncherError:
            log_event(
                self._logger,
                logging.WARNING,
                "tray_open_interface_skipped",
                "launcher is not running; nothing to open",
            )
            return
        self.browser_opener(url)

    def open_data_dir(self) -> None:
        """在资源管理器中打开数据目录 (用户数据所在, 卸载不删)。"""
        try:
            self.directory_opener(self.config.data_dir)
        except Exception as exc:  # noqa: BLE001 - 打不开目录不该杀死托盘
            log_event(
                self._logger,
                logging.WARNING,
                "tray_open_data_dir_failed",
                str(exc),
                detail={"data_dir": self.config.data_dir},
            )

    def quit(self) -> None:
        """退出: 先停服务 (清 ``.run`` 标记), 再结束事件循环。"""
        try:
            self.launcher.stop()
        finally:
            icon = self._icon
            self._icon = None
            if icon is not None:
                icon.stop()

    # -- 图标与事件循环 -------------------------------------------------

    def build_icon(self) -> Any:
        """构造 pystray 图标 (延迟 import: 服务起不来时不必先要 GUI 依赖)。"""
        import pystray
        from PIL import Image

        with Image.open(self.icon_path) as handle:
            image = handle.copy()
        menu = pystray.Menu(
            pystray.MenuItem(MENU_OPEN_INTERFACE, self.open_interface, default=True),
            pystray.MenuItem(MENU_OPEN_DATA_DIR, self.open_data_dir),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(MENU_QUIT, self.quit),
        )
        title = f"{APPLICATION_NAME} {APPLICATION_VERSION}"
        return pystray.Icon(ICON_NAME, image, title, menu)

    def run(self) -> int:
        """起服务 -> 托盘事件循环; 事件循环结束 (任意原因) 必停服务。"""
        try:
            self.launcher.start(block=False)
        except PortInUseError as exc:
            if is_our_service(self.config.host, self.config.port):
                # 已有实例: 双击第二次的预期是"把界面打开", 不是"报错"。
                url = _interface_url(self.config)
                log_event(
                    self._logger,
                    logging.INFO,
                    "tray_attach_existing",
                    f"an instance is already running; opening {url}",
                    detail={"url": url},
                )
                self.browser_opener(url)
                return EXIT_OK
            raise

        try:
            icon = self.build_icon()
            self._icon = icon
            icon.run()
            return EXIT_OK
        finally:
            self._icon = None
            self.launcher.stop()
            log_event(
                self._logger,
                logging.INFO,
                "tray_stopped",
                "tray event loop ended; local server stopped",
            )


# ----------------------------------------------------------------------
# 命令行入口
# ----------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    """``python -m src.application.tray [--data-dir ...]`` (无参数 = 默认配置)。"""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        options = parse_cli(args)
        config = build_config(options)
    except ApplicationError as exc:
        _report_error(f"[{exc.code}] {exc.message}")
        return EXIT_CONFIG_ERROR

    try:
        return TrayApp(config).run()
    except PortInUseError as exc:
        _report_error(str(exc.message))
        return EXIT_STARTUP_ERROR
    except ApplicationError as exc:
        _report_error(f"[{exc.code}] {exc.message}")
        return EXIT_STARTUP_ERROR
    except OSError as exc:
        _report_error(f"startup failed: {exc}")
        return EXIT_STARTUP_ERROR
    except ImportError as exc:
        # 冻结产物里依赖都在包内, 走到这里几乎只可能是源码环境没装托盘组件;
        # 给出能照做的命令, 而不是一段 ImportError 栈。
        _report_error(
            f"tray component is unavailable: {exc}. "
            "Install the runtime dependencies first: pip install -r requirements.txt"
        )
        return EXIT_CONFIG_ERROR


if __name__ == "__main__":  # pragma: no cover - 手动运行
    raise SystemExit(main())
