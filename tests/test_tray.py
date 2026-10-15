# -*- coding: utf-8 -*-
"""TASK-110 — 托盘入口 (``src.application.tray``) 测试。

覆盖三块:

1. **图标资源**: 源码态存在、是真 ICO、推导规则与冻结态一致 (基于
   ``__file__``, 与 ``default_static_dir`` 同一原理);
2. **菜单与动作**: 默认项是「打开界面」、四个条目齐全、每个条目接到正确
   的回调 (用假 pystray 模块注入, 测试不碰真实桌面);
3. **生命周期**: 起服务 -> 事件循环 -> 退出时 ``launcher.stop()`` +
   ``icon.stop()``, ``loomind-data/.run/`` 的 PID / 端口标记被清掉;
   端口被本应用占时「附着到已有实例」, 被别的程序占时如实报错。

真实 pystray 的 ``Icon.run`` 需要交互式桌面, 因此所有用例都用**假模块**
替换 ``sys.modules["pystray"]``; 另有一条 ``importorskip`` 用例只在环境
装了真 pystray 时验证 ``build_icon`` 能构造出真 ``Icon``。
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import types
from types import SimpleNamespace

import pytest

from src.application import tray
from src.application.config import AppConfig
from src.application.errors import PortInUseError
from src.application.launcher import (
    Launcher,
    find_running_pid,
    is_port_free,
    read_running_port,
    validate_environment,
)
from src.application.tray import (
    ICON_NAME,
    MENU_OPEN_DATA_DIR,
    MENU_OPEN_INTERFACE,
    MENU_QUIT,
    TrayApp,
    default_icon_path,
)


# ----------------------------------------------------------------------
# 夹具
# ----------------------------------------------------------------------


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
        cli_overrides={"port": free_port, "data_dir": str(tmp_path / "loomind-data")},
    )


class _FakeMenuItem:
    def __init__(self, text, action, default=False):
        self.text = text
        self.action = action
        self.default = default


class _FakeIcon:
    def __init__(self, name, image, title, menu):
        self.name = name
        self.image = image
        self.title = title
        self.menu = menu
        self.stopped = False
        self.on_run: object = None

    def run(self) -> None:
        callback = self.on_run
        if callback is not None:
            callback(self)

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def fake_pystray(monkeypatch):
    """把 ``sys.modules["pystray"]`` 换成记录型假模块。

    假 ``Icon.run`` 不阻塞; 用例通过 ``findings["icon"].on_run`` 注入
    "事件循环里发生什么" (例如用户点了「退出」)。
    """
    module = types.ModuleType("pystray")
    findings: dict = {}

    class Menu:
        SEPARATOR = _FakeMenuItem("- - - -", None)

        def __init__(self, *items):
            self.items = items

    class Icon(_FakeIcon):
        def __init__(self, name, image, title, menu):
            super().__init__(name, image, title, menu)
            findings["icon"] = self

    module.Menu = Menu
    module.MenuItem = _FakeMenuItem
    module.Icon = Icon
    monkeypatch.setitem(sys.modules, "pystray", module)
    return SimpleNamespace(module=module, findings=findings)


@pytest.fixture
def browser_calls(monkeypatch):
    """默认浏览器替换为记录器 (真实桌面路径绝不在测试里被走到)。"""
    calls: list[str] = []
    monkeypatch.setattr(tray, "open_in_browser", calls.append)
    return calls


# ----------------------------------------------------------------------
# 图标资源
# ----------------------------------------------------------------------


class TestIconAsset:
    def test_default_icon_path_lives_in_application_assets(self) -> None:
        path = default_icon_path()
        assert path.endswith(os.path.join("application", "assets", "app.ico"))
        assert os.path.isfile(path)

    def test_icon_is_a_real_ico(self) -> None:
        from PIL import Image

        with Image.open(default_icon_path()) as image:
            assert image.format == "ICO"
            assert image.size[0] >= 16


# ----------------------------------------------------------------------
# 菜单
# ----------------------------------------------------------------------


class TestMenu:
    def test_menu_shape_and_default_item(self, fake_pystray, base_config) -> None:
        app = TrayApp(base_config, browser_opener=lambda url: None)
        icon = app.build_icon()

        texts = [item.text for item in icon.menu.items]
        assert texts[0] == MENU_OPEN_INTERFACE
        assert texts[1] == MENU_OPEN_DATA_DIR
        assert icon.menu.items[2] is fake_pystray.module.Menu.SEPARATOR
        assert texts[3] == MENU_QUIT
        assert icon.menu.items[0].default is True
        assert icon.menu.items[1].default is False
        assert icon.menu.items[3].default is False

    def test_icon_identity(self, fake_pystray, base_config) -> None:
        app = TrayApp(base_config, browser_opener=lambda url: None)
        icon = app.build_icon()
        assert icon.name == ICON_NAME
        assert "1.1.1" in icon.title

    def test_menu_actions_are_wired_to_callbacks(
        self, fake_pystray, base_config, browser_calls
    ) -> None:
        opened: list[str] = []
        app = TrayApp(base_config, browser_opener=browser_calls.append, directory_opener=opened.append)
        icon = app.build_icon()

        assert icon.menu.items[0].action == app.open_interface
        assert icon.menu.items[1].action == app.open_data_dir
        assert icon.menu.items[3].action == app.quit

        # 真实 pystray 支持 0 参回调 (内部自动包一层); 这里直接调用,
        # 验证的是"点下去会走到哪个动作", 而不是 pystray 的适配行为。
        try:
            app.launcher.start(block=False)
            browser_calls.clear()  # 忽略启动时自动开的那一次
            icon.menu.items[0].action()
            icon.menu.items[1].action()
            assert browser_calls == [app.launcher.url]
            assert opened == [base_config.data_dir]
        finally:
            app.launcher.stop()


# ----------------------------------------------------------------------
# 菜单动作
# ----------------------------------------------------------------------


class TestActions:
    def test_open_interface_uses_running_url(self, base_config, browser_calls) -> None:
        app = TrayApp(base_config, browser_opener=browser_calls.append)
        try:
            app.launcher.start(block=False)
            browser_calls.clear()  # 忽略启动时自动开的那一次
            app.open_interface()
            assert browser_calls == [app.launcher.url]
        finally:
            app.launcher.stop()

    def test_open_interface_without_launcher_is_a_noop(self, base_config, browser_calls) -> None:
        app = TrayApp(base_config, browser_opener=browser_calls.append)
        app.open_interface()
        assert browser_calls == []

    def test_open_data_dir_passes_configured_directory(self, base_config) -> None:
        opened: list[str] = []
        app = TrayApp(base_config, directory_opener=opened.append)
        app.open_data_dir()
        assert opened == [base_config.data_dir]

    def test_open_data_dir_swallows_opener_failure(self, base_config) -> None:
        def boom(path: str) -> None:
            raise OSError("no explorer here")

        app = TrayApp(base_config, directory_opener=boom)
        app.open_data_dir()  # 不抛 = 托盘不被一个打不开的目录杀死


# ----------------------------------------------------------------------
# 生命周期
# ----------------------------------------------------------------------


class TestRunLifecycle:
    def test_run_cleans_up_when_quit_from_menu(self, fake_pystray, base_config, browser_calls) -> None:
        app = TrayApp(base_config, browser_opener=browser_calls.append)

        def user_clicks_quit(icon) -> None:
            app.quit()

        fake_pystray.findings["icon"] = None
        original_build = app.build_icon

        def build_with_hook():
            icon = original_build()
            icon.on_run = user_clicks_quit
            return icon

        app.build_icon = build_with_hook  # type: ignore[method-assign]
        result = app.run()

        assert result == 0
        assert fake_pystray.findings["icon"].stopped is True
        assert browser_calls == [f"http://127.0.0.1:{base_config.port}"]
        # 退出后: 服务停了、标记清了。
        assert find_running_pid(base_config.data_dir) is None
        assert read_running_port(base_config.data_dir) is None
        assert is_port_free(base_config.host, base_config.port)

    def test_quit_stops_launcher_and_clears_markers(self, base_config) -> None:
        app = TrayApp(base_config, browser_opener=lambda url: None)
        app.launcher.start(block=False)
        assert find_running_pid(base_config.data_dir) == os.getpid()
        assert read_running_port(base_config.data_dir) == base_config.port

        stub = _FakeIcon(ICON_NAME, None, None, None)
        app._icon = stub
        app.quit()

        assert stub.stopped is True
        assert app.launcher.running is False
        assert find_running_pid(base_config.data_dir) is None
        assert read_running_port(base_config.data_dir) is None

    def test_run_cleans_up_even_if_event_loop_ends_without_quit(
        self, fake_pystray, base_config, browser_calls
    ) -> None:
        """事件循环以任何方式结束 (例如系统注销), 兜底也必停服务、清标记。"""
        app = TrayApp(base_config, browser_opener=browser_calls.append)
        # 假 Icon.run 默认不调任何回调, 直接返回 —— 模拟"没有走 quit 的正常退出"。
        result = app.run()

        assert result == 0
        assert app.launcher.running is False
        assert find_running_pid(base_config.data_dir) is None
        assert read_running_port(base_config.data_dir) is None

    def test_run_attaches_to_existing_instance(self, base_config, browser_calls) -> None:
        first = Launcher(base_config, browser_opener=lambda url: None)
        first.start(block=False)
        try:
            app = TrayApp(base_config, browser_opener=browser_calls.append)
            result = app.run()

            assert result == 0
            assert browser_calls == [f"http://127.0.0.1:{base_config.port}"]
            # 第一个实例不受影响, 标记仍指向它。
            assert first.running is True
            assert read_running_port(base_config.data_dir) == base_config.port
        finally:
            first.stop()

    def test_run_raises_when_port_held_by_another_program(self, base_config) -> None:
        holder = _start_foreign_http(base_config.host, base_config.port)
        try:
            app = TrayApp(base_config, browser_opener=lambda url: None)
            with pytest.raises(PortInUseError):
                app.run()
        finally:
            holder.close()


# ----------------------------------------------------------------------
# 其它: 系统动作与错误出口
# ----------------------------------------------------------------------


class TestSystemGlue:
    @pytest.mark.skipif(os.name != "nt", reason="os.startfile 是 Windows 专属")
    def test_open_directory_uses_startfile(self, monkeypatch, tmp_path) -> None:
        calls: list[str] = []
        monkeypatch.setattr(tray.os, "startfile", calls.append, raising=False)
        tray.open_directory(str(tmp_path))
        assert calls == [os.path.abspath(str(tmp_path))]

    def test_report_error_prints_without_console_and_dialog_when_frozen(
        self, monkeypatch, capsys
    ) -> None:
        shown: list[tuple] = []
        monkeypatch.setattr(tray, "is_frozen", lambda: True)
        monkeypatch.setattr(tray, "_show_message_box", lambda title, text: shown.append((title, text)))

        tray._report_error("boom")
        assert shown and shown[0][1] == "boom"
        assert "boom" in capsys.readouterr().err

    def test_report_error_has_no_dialog_outside_frozen(self, monkeypatch, capsys) -> None:
        shown: list[tuple] = []
        monkeypatch.setattr(tray, "is_frozen", lambda: False)
        monkeypatch.setattr(tray, "_show_message_box", lambda title, text: shown.append((title, text)))

        tray._report_error("quiet failure")
        assert shown == []
        assert "quiet failure" in capsys.readouterr().err


# ----------------------------------------------------------------------
# main()
# ----------------------------------------------------------------------


class TestMain:
    def test_main_config_error_reports_and_returns_2(self, tmp_path, capsys) -> None:
        missing = tmp_path / "nope.json"
        code = tray.main(["--config", str(missing)])
        assert code == 2
        assert "does not exist" in capsys.readouterr().err

    def test_main_missing_pystray_reports_install_hint(
        self, monkeypatch, tmp_path, free_port, capsys
    ) -> None:
        monkeypatch.setitem(sys.modules, "pystray", None)  # import 立即抛 ImportError
        browser_calls: list[str] = []
        monkeypatch.setattr(tray, "open_in_browser", browser_calls.append)

        data_dir = tmp_path / "loomind-data"
        code = tray.main(
            ["--data-dir", str(data_dir), "--port", str(free_port)]
        )

        assert code == 2
        err = capsys.readouterr().err
        assert "pip install -r requirements.txt" in err
        # 服务即便起来了也要被兜底停掉: 标记不残留。
        assert find_running_pid(str(data_dir)) is None
        assert read_running_port(str(data_dir)) is None

    def test_main_reports_startup_error_when_port_taken_by_other(self, base_config, capsys) -> None:
        holder = _start_foreign_http(base_config.host, base_config.port)
        try:
            code = tray.main(
                [
                    "--data-dir",
                    base_config.data_dir,
                    "--port",
                    str(base_config.port),
                ]
            )
            assert code == 3
            assert "already in use" in capsys.readouterr().err.lower()
        finally:
            holder.close()

    def test_environment_report_still_valid_for_tray_config(self, base_config) -> None:
        """托盘不改变启动自检 —— 安装版的 data_dir 仍是可创建的。"""
        report = validate_environment(base_config)
        assert report.ok


class TestRealPystray:
    def test_build_icon_with_real_pystray(self, base_config) -> None:
        pytest.importorskip("pystray")
        app = TrayApp(base_config, browser_opener=lambda url: None)
        icon = app.build_icon()
        assert icon.name == ICON_NAME


# ----------------------------------------------------------------------
# 辅助: “别的程序”占着端口 (监听但不认领本应用)
# ----------------------------------------------------------------------


class _ForeignHTTPServer:
    def __init__(self, sock: socket.socket) -> None:
        self._socket = sock

    def close(self) -> None:
        try:
            self._socket.close()
        except OSError:
            pass


def _start_foreign_http(host: str, port: int) -> _ForeignHTTPServer:
    """监听 ``port`` 并回一个**不是本应用**的 JSON (让健康握手失败)。"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(2)

    def serve() -> None:
        while True:
            try:
                connection, _ = server.accept()
            except OSError:
                return
            try:
                connection.recv(65536)
                body = b'{"application": "SomeOtherTool"}'
                connection.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
                    b"\r\n" + body
                )
            except OSError:
                pass
            finally:
                connection.close()

    threading.Thread(target=serve, daemon=True).start()
    return _ForeignHTTPServer(server)
