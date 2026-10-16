# -*- coding: utf-8 -*-
"""TASK-112 —— 界面窗口的复用与几何记忆。

测试刻意**不碰真实桌面**: Win32 调用被换成可注入的桩, 或直接验证 no-op 分支。
"能不能真的找到窗口 / 置前 / 恢复最大化" 由本机实机验收回答 (实测记录见
``src/application/ui_window.py`` 的模块 docstring)。
"""

from __future__ import annotations

import json
import pathlib
import re
import threading
import time

import pytest

from src.application import ui_window

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

GEOMETRY = {"x": 300, "y": 200, "width": 950, "height": 650, "maximized": False}


class _ScreenStub:
    """只回答虚拟屏幕尺寸的假 user32 (让 ``_fits_screen`` 可测)。"""

    def __init__(self, width: int = 1536, height: int = 864) -> None:
        self._values = {76: 0, 77: 0, 78: width, 79: height}

    def GetSystemMetrics(self, index: int) -> int:
        return self._values[index]


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition never became true")


def _tracker_alive() -> bool:
    """跟踪线程是否还活着 (窗口关闭后它必须自己退出, 不能泄漏)。"""
    return any(
        item.name == "loomind-ui-window" and item.is_alive()
        for item in threading.enumerate()
    )


# ----------------------------------------------------------------------
# 标题必须与页面标题一致
# ----------------------------------------------------------------------


def test_window_title_matches_the_page_title() -> None:
    """``WINDOW_TITLE`` 是"认出自己"的依据, 必须与 ``index.html`` 逐字一致。"""
    text = (PROJECT_ROOT / "src" / "web" / "index.html").read_text(encoding="utf-8")
    match = re.search(r"<title>(.*?)</title>", text, re.S)
    assert match is not None, "index.html 里没有 <title>"
    assert match.group(1).strip() == ui_window.WINDOW_TITLE


# ----------------------------------------------------------------------
# 几何读写
# ----------------------------------------------------------------------


def test_geometry_round_trips(tmp_path) -> None:
    path = str(tmp_path / "ui-window.json")
    ui_window.save_geometry(path, GEOMETRY)
    assert ui_window.load_geometry(path) == GEOMETRY


def test_missing_or_absent_path_reads_as_no_geometry(tmp_path) -> None:
    assert ui_window.load_geometry(str(tmp_path / "nope.json")) is None
    assert ui_window.load_geometry(None) is None
    assert ui_window.load_geometry("") is None


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        json.dumps([1, 2, 3]),
        json.dumps({"x": 1, "y": 2, "width": 0, "height": 100}),
        json.dumps({"x": 1, "y": 2, "width": 100}),
        json.dumps({"x": "1", "y": 2, "width": 100, "height": 100}),
        json.dumps({"x": True, "y": 2, "width": 100, "height": 100}),
    ],
)
def test_broken_geometry_reads_as_none(tmp_path, payload: str) -> None:
    """坏记忆 = 没记忆 (回退默认尺寸), 而不是把界面开成 0x0。"""
    path = tmp_path / "ui-window.json"
    path.write_text(payload, encoding="utf-8")
    assert ui_window.load_geometry(str(path)) is None


def test_geometry_off_the_screen_is_dropped(tmp_path, monkeypatch) -> None:
    """换了显示器 / 改了分辨率后旧几何可能整个跑到屏幕外 —— 丢掉它。"""
    monkeypatch.setattr(ui_window, "_USER32", _ScreenStub())
    path = str(tmp_path / "ui-window.json")
    off = {"x": 999999, "y": 999999, "width": 900, "height": 600, "maximized": False}
    ui_window.save_geometry(path, off)
    assert ui_window.load_geometry(path) is None


def test_geometry_on_the_screen_survives(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ui_window, "_USER32", _ScreenStub())
    path = str(tmp_path / "ui-window.json")
    ui_window.save_geometry(path, GEOMETRY)
    assert ui_window.load_geometry(path) == GEOMETRY


# ----------------------------------------------------------------------
# 非 Windows: 一切退化为空操作
# ----------------------------------------------------------------------


def test_everything_is_a_no_op_without_windows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ui_window, "_USER32", None)
    assert ui_window.find_existing_window() is None
    assert ui_window.read_geometry(12345) is None
    assert ui_window.watch_window(str(tmp_path / "ui-window.json")) is False
    ui_window.focus_window(12345)  # 不抛
    ui_window.apply_geometry(12345, GEOMETRY)  # 不抛
    ui_window.apply_geometry(12345, None)  # 不抛


# ----------------------------------------------------------------------
# 跟踪线程
# ----------------------------------------------------------------------


def test_watch_window_applies_memory_synchronously_then_follows(tmp_path, monkeypatch) -> None:
    """套用记忆是**同步**的 (短命托盘进程等不起), 之后的跟踪才交给线程。"""
    monkeypatch.setattr(ui_window, "_USER32", _ScreenStub())
    path = str(tmp_path / "ui-window.json")
    window = [11]
    applied: list = []
    current = {"x": 40, "y": 30, "width": 900, "height": 700, "maximized": False}

    monkeypatch.setattr(ui_window, "find_existing_window", lambda title=None: window[0])
    monkeypatch.setattr(ui_window, "apply_geometry", lambda hwnd, geo: applied.append(geo))
    monkeypatch.setattr(ui_window, "read_geometry", lambda hwnd: dict(current))

    remembered = {"x": 5, "y": 5, "width": 700, "height": 500, "maximized": True}
    assert ui_window.watch_window(path, remembered, wait_seconds=0.5, interval=0.01) is True
    # 返回时记忆已经套用并落盘 —— 不依赖任何后台线程。
    assert applied == [remembered]
    assert ui_window.load_geometry(path) == current

    current = {**current, "maximized": True}  # 用户把窗口最大化了
    _wait_until(lambda: ui_window.load_geometry(path) == current)

    window[0] = None  # 关掉窗口
    _wait_until(lambda: not _tracker_alive())


def test_watch_window_gives_up_when_no_window_ever_appears(tmp_path, monkeypatch) -> None:
    """等不到窗口 (浏览器没起来) 就如实返回 False, 且不留残留线程。"""
    monkeypatch.setattr(ui_window, "_USER32", _ScreenStub())
    monkeypatch.setattr(ui_window, "find_existing_window", lambda title=None: None)

    path = str(tmp_path / "ui-window.json")
    assert ui_window.watch_window(path, None, wait_seconds=0.05, interval=0.01) is False
    assert not _tracker_alive()
