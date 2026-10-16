# -*- coding: utf-8 -*-
"""界面窗口的复用与几何记忆 (TASK-112)。

背景
--------------------------------------------------------------------

界面不是原生窗口, 而是 Chromium 系浏览器 ``--app=<url>`` 开出的无地址栏
窗口 (见 :func:`src.application.launcher.open_ui_window`)。2026-10-16 实测
确认了浏览器行为带来的两个缺陷:

1. **重复启动不复用窗口** —— 同一参数再跑一次 ``--app=`` 会多出一个顶层
   窗口, 于是"点一次托盘 / 开始菜单就多一个界面";
2. **几何不被记住** —— ``--window-position`` / ``--window-size`` 只在浏览器
   需要新起一个进程时才生效 (用户已经有 Edge 在跑时, 新窗口请求被转发给既有
   实例, 这两个参数在转发中丢失), 而 ``--start-maximized`` 对 ``--app`` 窗口
   压根无效 (实测 showCmd 仍为 1)。用户调好的最大化 / 半屏状态下次全丢。

本模块用 Win32 (纯 ``ctypes``, 不新增依赖) 补上这两块:

- :func:`find_existing_window` 按**精确标题 + 窗口类**认出我们自己的窗口,
  :func:`focus_window` 把它还原并置前 —— 复用而不是新开;
- :func:`watch_window` 先**同步**等窗口出现并套用记忆 (调用方往往是"打开
  界面就退出"的短命托盘进程, 异步等会丢状态), 之后在窗口存活期间跟踪几何
  (位置 / 尺寸 / 是否最大化), 变化即落盘; 窗口关闭后最后一次记录就是
  "上次关闭时的样式", :func:`load_geometry` / :func:`apply_geometry` 负责
  下次恢复。

平台边界
--------------------------------------------------------------------

非 Windows 上 ``--app`` 外壳本就不可用 (``find_app_shell_browser`` 返回
``None``, 走系统默认浏览器), 因此本模块所有函数在非 Windows 上都是**安全
空操作** —— 返回 ``None`` / 不做事, 绝不抛异常。

标题从哪来
--------------------------------------------------------------------

窗口标题就是页面 ``<title>`` (``src/web/index.html``)。这里硬编码成
:data:`WINDOW_TITLE` 是为了"认出自己", 必须与页面标题逐字一致 ——
``tests/test_ui_window.py`` 会读页面比对, 改标题而不同步这里会直接红。
"""

from __future__ import annotations

import ctypes
import json
import os
import threading
import time
from typing import Any, Optional

from src.application.data_dirs import atomic_write_bytes

__all__ = [
    "IS_WINDOWS",
    "STATE_FILENAME",
    "WINDOW_CLASS",
    "WINDOW_TITLE",
    "apply_geometry",
    "find_existing_window",
    "focus_window",
    "load_geometry",
    "read_geometry",
    "save_geometry",
    "watch_window",
]

IS_WINDOWS = os.name == "nt"

#: 界面窗口标题 —— 逐字等于 ``src/web/index.html`` 的 ``<title>``
#: (``--app`` 窗口不带浏览器的 " - 个人 - Microsoft Edge" 后缀)。
WINDOW_TITLE = "Loomind · Weave Knowledge Together."

#: Chromium 顶层窗口的窗口类名。``--app`` 窗口与普通标签页窗口同类,
#: 所以必须和标题一起判断。
WINDOW_CLASS = "Chrome_WidgetWin_1"

#: 几何记忆文件名 (由调用方放在 ``<data_dir>/.run/``, 与 PID / 端口标记同性质)。
STATE_FILENAME = "ui-window.json"

#: 同步等窗口出现的上限 (Edge 冷启动实测 3–8 秒)。等待会挡住调用方, 所以
#: 不设太长 —— 等不到只是这次没恢复几何, 下次还有机会。
_WAIT_SECONDS = 12.0
#: 等窗口出现时的轮询间隔。
_POLL_SECONDS = 0.25
#: 跟踪几何的间隔。几何变化全是人工操作, 1 秒的粒度足够; 写盘只在**变化**时发生。
_TRACK_SECONDS = 1.0
#: 窗口出现后先让它稳定一下再套用几何, 免得和 Chromium 自己的初始化抢。
_SETTLE_SECONDS = 0.3
#: 连续这么多次找不到窗口才判定"已关闭" (页面重载时窗口会短暂消失)。
_MISSES_BEFORE_CLOSED = 2

_SW_SHOWMAXIMIZED = 3
_SW_RESTORE = 9
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010

_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79

#: 判定"还看得见"的最小可见面积 —— 换显示器 / 改分辨率后, 旧几何可能整个
#: 落在屏幕外, 那样恢复出来等于界面消失。
_MIN_VISIBLE_WIDTH = 200
_MIN_VISIBLE_HEIGHT = 100


# ----------------------------------------------------------------------
# Win32 绑定 (非 Windows 上全部为 None, 公开函数随之退化为空操作)
# ----------------------------------------------------------------------


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _WindowPlacement(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("showCmd", ctypes.c_uint),
        ("ptMinPosition", _Point),
        ("ptMaxPosition", _Point),
        ("rcNormalPosition", _Rect),
    ]


#: ``EnumWindows`` 的回调签名。返回 ``False`` 即停止枚举。
_ENUM_PROC = (
    ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    if IS_WINDOWS
    else None
)


def _bind_user32() -> Any:
    """绑定 user32 并**显式声明参数类型**。

    ``HWND`` 在 64 位下是指针: 不声明 argtypes 时 ctypes 按 ``c_int`` 传参,
    句柄被截断, 所有调用都会失败 (返回 0 / 不生效)。
    """
    if not IS_WINDOWS:
        return None
    import ctypes.wintypes as wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumWindows.argtypes = [_ENUM_PROC, ctypes.c_void_p]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = ctypes.c_bool
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = ctypes.c_bool
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowPlacement.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(_WindowPlacement),
    ]
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    return user32


_USER32 = _bind_user32()


# ----------------------------------------------------------------------
# 找窗口 / 复用
# ----------------------------------------------------------------------


def find_existing_window(title: str = WINDOW_TITLE) -> Optional[int]:
    """按**精确标题 + 窗口类**找出已经打开的界面窗口; 没有则 ``None``。

    标题必须逐字相等: 用户用普通 Edge 打开同一个页面时, 标题会带上
    " - 个人 - Microsoft Edge" 这类后缀, 因此不会被误认成界面窗口。
    """
    user32 = _USER32
    if user32 is None:
        return None

    hits: list[int] = []

    def visit(hwnd: Any, _lparam: Any) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if buffer.value != title:
            return True
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, name, 256)
        if name.value != WINDOW_CLASS:
            return True
        hits.append(int(hwnd))
        return False  # 找到一个就够, 不必再枚举

    user32.EnumWindows(_ENUM_PROC(visit), 0)
    return hits[0] if hits else None


def focus_window(hwnd: int) -> None:
    """把已有窗口还原 (若最小化) 并置于前台 —— 复用而不是新开。"""
    user32 = _USER32
    if user32 is None or not hwnd:
        return
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, _SW_RESTORE)
    user32.SetForegroundWindow(hwnd)


# ----------------------------------------------------------------------
# 几何读写
# ----------------------------------------------------------------------


def read_geometry(hwnd: int) -> Optional[dict[str, Any]]:
    """读窗口当前几何。

    返回的是**还原后**的位置尺寸 (窗口最大化时也一样) —— 这样最大化窗口
    被还原时能回到用户当初调好的尺寸, 而不是最大化尺寸。
    """
    user32 = _USER32
    if user32 is None or not hwnd:
        return None
    placement = _WindowPlacement()
    placement.length = ctypes.sizeof(_WindowPlacement)
    if not user32.GetWindowPlacement(hwnd, ctypes.byref(placement)):
        return None
    normal = placement.rcNormalPosition
    geometry = {
        "x": int(normal.left),
        "y": int(normal.top),
        "width": int(normal.right - normal.left),
        "height": int(normal.bottom - normal.top),
        "maximized": int(placement.showCmd) == _SW_SHOWMAXIMIZED,
    }
    if geometry["width"] <= 0 or geometry["height"] <= 0:
        return None
    return geometry


def apply_geometry(hwnd: int, geometry: Optional[dict[str, Any]]) -> None:
    """把几何套用到窗口。

    位置尺寸**必须**用 ``SetWindowPos`` 摆: ``--window-position`` /
    ``--window-size`` 只在浏览器需要新起一个进程时才生效 —— 用户已经有
    Edge 在跑时, 新窗口请求被转发给既有实例, 这两个启动参数在转发中被
    丢弃 (2026-10-16 实测)。

    最大化走 ``ShowWindow`` (``--start-maximized`` 对 ``--app`` 窗口无效),
    且刻意**先摆好还原尺寸再最大化** —— 这样用户点"还原"回到的是当初的
    大小, 而不是浏览器的默认尺寸。
    """
    user32 = _USER32
    if user32 is None or not hwnd or not geometry:
        return
    user32.SetWindowPos(
        hwnd,
        None,
        int(geometry["x"]),
        int(geometry["y"]),
        int(geometry["width"]),
        int(geometry["height"]),
        _SWP_NOZORDER | _SWP_NOACTIVATE,
    )
    if geometry.get("maximized"):
        user32.ShowWindow(hwnd, _SW_SHOWMAXIMIZED)


def load_geometry(path: Optional[str]) -> Optional[dict[str, Any]]:
    """读几何记忆; 文件缺失 / 损坏 / 形状不对 / 已跑到屏幕外一律 ``None``。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            payload = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    geometry = _normalise_geometry(payload)
    if geometry is None or not _fits_screen(geometry):
        return None
    return geometry


def save_geometry(path: str, geometry: dict[str, Any]) -> None:
    """原子写几何记忆; 失败只当"这次没记住", 绝不打断界面打开。"""
    try:
        text = json.dumps(geometry, ensure_ascii=False, sort_keys=True, indent=2)
        atomic_write_bytes(path, (text + "\n").encode("utf-8"))
    except OSError:  # pragma: no cover - 磁盘满 / 目录被删
        pass


def watch_window(
    path: str,
    geometry: Optional[dict[str, Any]] = None,
    *,
    wait_seconds: float = _WAIT_SECONDS,
    interval: float = _TRACK_SECONDS,
) -> bool:
    """等新窗口出现、套用 ``geometry``, 再转入后台跟踪; 返回是否等到了窗口。

    **"等窗口"这一步是同步的, 不是可选项。** 调用方常常是"服务已经在跑,
    打开界面就退出"的短命托盘进程 (实测): 把等待整个丢给后台线程的话, 进程
    一退出 daemon 线程就没了, 记忆里的最大化状态会被静默丢掉 —— 2026-10-16
    在安装版上就是这么复现的。同步等完 (窗口出现通常 3–8 秒) 再起跟踪线程,
    后面那半段才允许是异步的。

    非 Windows 上直接返回 ``False`` (没有可跟踪的窗口)。
    """
    if _USER32 is None:
        return False

    hwnd = _await_window(wait_seconds)
    if hwnd is None:
        return False
    time.sleep(_SETTLE_SECONDS)
    if geometry:
        apply_geometry(hwnd, geometry)
    last = read_geometry(hwnd)
    if last is not None:
        save_geometry(path, last)

    def track() -> None:
        nonlocal last
        misses = 0
        while True:
            time.sleep(interval)
            current = find_existing_window()
            if current is None:
                misses += 1
                if misses >= _MISSES_BEFORE_CLOSED:
                    return
                continue
            misses = 0
            snapshot = read_geometry(current)
            if snapshot is not None and snapshot != last:
                last = snapshot
                save_geometry(path, snapshot)

    threading.Thread(target=track, name="loomind-ui-window", daemon=True).start()
    return True


def _await_window(wait_seconds: float) -> Optional[int]:
    """轮询等界面窗口出现; 超时返回 ``None``。"""
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        hwnd = find_existing_window()
        if hwnd is not None:
            return hwnd
        time.sleep(_POLL_SECONDS)
    return None


def _normalise_geometry(payload: Any) -> Optional[dict[str, Any]]:
    """把落盘内容收敛成几何字典; 形状不对返回 ``None`` (调用方回退默认尺寸)。"""
    if not isinstance(payload, dict):
        return None
    values: dict[str, Any] = {}
    for key in ("x", "y", "width", "height"):
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        values[key] = value
    if values["width"] <= 0 or values["height"] <= 0:
        return None
    values["maximized"] = bool(payload.get("maximized"))
    return values


def _fits_screen(geometry: dict[str, Any]) -> bool:
    """窗口是否还落在当前虚拟屏幕内 (换显示器后旧几何可能整个跑到屏幕外)。

    非 Windows 上不做判断 —— 本模块在那些平台上本就不生效。
    """
    user32 = _USER32
    if user32 is None:
        return True
    left = user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
    top = user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
    right = left + user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
    bottom = top + user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
    overlap_x = min(geometry["x"] + geometry["width"], right) - max(geometry["x"], left)
    overlap_y = min(geometry["y"] + geometry["height"], bottom) - max(geometry["y"], top)
    return overlap_x >= _MIN_VISIBLE_WIDTH and overlap_y >= _MIN_VISIBLE_HEIGHT
