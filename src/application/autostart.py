# -*- coding: utf-8 -*-
"""开机自启 (Windows 注册表)。

真相源是 ``HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
下的 ``Loomind`` 值 (REG_SZ, 启动命令)。三个操作都接受**可注入的钩子**:

- 测试在内存字典上跑, 绝不碰真实注册表;
- 生产路径用 ``winreg`` (仅 Windows); 非 Windows 平台 ``supported`` 恒为 False,
  设置页据此显示"仅 Windows", 而不是给出一个假开关。

命令形状由调用方给出 (见 :func:`default_launch_command`):

- 开发模式: ``"<python>" -m src.application.launcher start --data-dir "<dir>"``;
- 安装版 (PyInstaller 冻结 exe): ``"<exe>"`` —— 冻结入口就是托盘
  (packaging/entry.py 的默认行为)。

**刻意不做的事**: 不写 HKLM (需要管理员权限, 且"所有用户"语义对单用户应用
是错的); 不验证命令是否存在 (注册表写入成功就是用户选择, 可执行文件丢了是
另一个问题, 由 launcher 的环境检查报告)。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable, Optional

__all__ = [
    "AUTOSTART_VALUE_NAME",
    "RUN_KEY_PATH",
    "is_supported",
    "default_launch_command",
    "read_run_command",
    "write_run_command",
    "delete_run_command",
    "set_autostart",
]


#: Run 键下的值名。产品名 (品牌重塑后统一 Loomind)。
AUTOSTART_VALUE_NAME = "Loomind"

#: HKCU 下 Run 子键的路径 (winreg 用反斜杠)。
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


def is_supported() -> bool:
    """当前进程是否支持操作注册表 (仅 Windows)。"""
    return sys.platform == "win32" and os.name == "nt"


def default_launch_command(data_dir: str) -> str:
    """开机自启要写进注册表的命令。

    - 冻结 exe (PyInstaller): ``sys.executable`` 就是 exe 自身, 不带参数启动
      即托盘 + 打开浏览器 (packaging/entry.py 默认行为);
    - 开发模式: 用当前解释器拉起 ``python -m src.application.launcher start``,
      ``--data-dir`` 显式钉住数据目录 —— 开机启动的工作目录不确定, 不钉就会
      落到"默认数据目录", 与用户实际用的库分家。

    命令里的路径用双引号包住: Windows 注册表命令按空格切分, 未加引号的
    ``C:\\Program Files\\...`` 会在第一个空格处断掉。
    """
    exe = sys.executable or ""
    data = data_dir.replace('"', "")
    if exe.lower().endswith(("python.exe", "pythonw.exe")):
        return f'"{exe}" -m src.application.launcher start --data-dir "{data}"'
    return f'"{exe}" --data-dir "{data}"'


def _winreg() -> Any:
    """延迟导入 winreg。非 Windows 上 ImportError 由调用方的 supported 检查挡住。"""
    import winreg  # noqa: PLC0415 - 仅 Windows 可用, 必须延迟导入

    return winreg


def read_run_command(reader: Optional[Callable[[], Optional[str]]] = None) -> Optional[str]:
    """读 Run 键里的 Loomind 值。不支持平台 / 键不存在 / 读失败 -> None。"""
    if reader is not None:
        return reader()
    if not is_supported():
        return None
    try:
        winreg = _winreg()
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ)
        try:
            value, _kind = winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
        finally:
            winreg.CloseKey(key)
        return str(value) if value else None
    except OSError:
        # 键不存在 (FileNotFoundError / FileNotFoundError 2) 与权限问题都是
        # "读不到" —— 对调用方同一形状: 未开启。
        return None


def write_run_command(
    command: str,
    writer: Optional[Callable[[str], None]] = None,
) -> None:
    """写 Run 键。command 为空抛 ValueError (空命令等于写坏一个启动项)。"""
    if not command or not command.strip():
        raise ValueError("autostart command must be a non-empty string")
    if writer is not None:
        writer(command)
        return
    if not is_supported():
        raise OSError("autostart is only supported on Windows")
    winreg = _winreg()
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE)
    try:
        winreg.SetValueEx(key, AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, command)
    finally:
        winreg.CloseKey(key)


def delete_run_command(deleter: Optional[Callable[[], None]] = None) -> None:
    """删 Run 键里的 Loomind 值。值本来就不存在 -> 幂等成功。"""
    if deleter is not None:
        deleter()
        return
    if not is_supported():
        raise OSError("autostart is only supported on Windows")
    winreg = _winreg()
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE)
    try:
        winreg.DeleteValue(key, AUTOSTART_VALUE_NAME)
    except FileNotFoundError:
        pass  # 值不存在 —— 调用方要的就是这个结果
    finally:
        winreg.CloseKey(key)


def set_autostart(
    enabled: bool,
    command: str,
    *,
    reader: Optional[Callable[[], Optional[str]]] = None,
    writer: Optional[Callable[[str], None]] = None,
    deleter: Optional[Callable[[], None]] = None,
) -> bool:
    """开/关开机自启, 返回**读回来的最终状态**。

    写后读回而不是返回"我以为写成功了": 注册表写入在 Windows 上可以部分成功
    (键存在但值被组策略锁定), 那种情况下设置页显示"已开启"是撒谎。
    """
    if enabled:
        write_run_command(command, writer=writer)
    else:
        delete_run_command(deleter=deleter)
    return read_run_command(reader=reader) is not None
