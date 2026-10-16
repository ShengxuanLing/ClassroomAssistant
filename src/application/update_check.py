# -*- coding: utf-8 -*-
"""更新检查与自动安装 (GitHub Releases)。

打 ``https://api.github.com/repos/ShengxuanLing/Loomind/releases/latest``,
与 ``APPLICATION_VERSION`` 比较, 给出"有新版本 + 安装包直链"; 用户在右下角
弹窗点"立即安装"后, :func:`install_update` 下载安装包、静默启动安装程序、
退出旧进程 —— 用户从开始菜单重开即新版本。

设计约束:

- ``fetch`` / ``launcher`` / ``shutdown`` 全部可注入 (测试用桩), 默认
  ``urllib`` + 8s 检查超时 / 300s 下载超时 / ``subprocess.Popen`` 静默启动 /
  daemon 线程延迟自退;
- **网络失败不抛异常**: 返回 ``ok=False`` + 原因 —— 设置页照常渲染,
  用户看到的是"检查失败 (超时/离线)", 而不是一个错误页;
- 版本比较是纯数字逐段比 (``v1.10.0 > v1.9.0``), 预发布后缀 (``-beta``)
  只取前缀数字段 —— 比较"是否更新"够用, 不做完整 semver;
- :func:`install_update` 只在安装版 Windows 应用里真正执行
  (:func:`install_supported` 门禁) —— 开发模式 (``python -m ...``) 下
  拒绝, 前端回落到下载链接。自动安装杀死的是旧进程自己, 在开发机上
  触发等于自杀, 必须挡住;
- 安装包文件名白名单 (``Loomind-*-Setup.exe``): release 里的其它附件
  (源码 zip 之类) 永远不会被下载执行;
- 下载只接受 ``https`` 链接 —— 客户端传 URL 的形状不存在 (服务端重新
  查最新版、自己选附件), 没有 SSRF 面。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
from typing import Any, Callable, Optional

from src.application.config import is_frozen  # noqa: PLC0415 - 延迟导入, 避免循环依赖

__all__ = [
    "GITHUB_RELEASES_LATEST",
    "INSTALLER_FILENAME_PATTERN",
    "MAX_INSTALLER_BYTES",
    "parse_version",
    "check_for_update",
    "install_supported",
    "install_update",
]


#: 品牌重塑后的正式仓库 (ClassroomAssistant -> Loomind, 旧名 301 重定向到这里)。
GITHUB_RELEASES_LATEST = (
    "https://api.github.com/repos/ShengxuanLing/Loomind/releases/latest"
)

#: 默认超时。设置页的"立即检查"是同步交互, 慢于这个值的网络没有显示价值。
DEFAULT_TIMEOUT_SECONDS = 8.0

#: GitHub API 要求 User-Agent, 否则 403。
_USER_AGENT = "Loomind-Update-Check"

#: 安装包文件名白名单: ``Loomind-<版本>-Setup.exe`` (大小写不敏感)。
#: 与 ``packaging/build.py`` 的 ``Loomind-{version}-Setup.exe`` 同源 ——
#: 改了一边必须改另一边, 否则自动安装永远找不到附件。
INSTALLER_FILENAME_PATTERN = re.compile(
    r"^Loomind-.+-Setup\.exe$", re.IGNORECASE
)

#: 安装包体积上限 (1 GiB)。GitHub release 附件 + slack 后的硬顶 ——
#: 超过这个值的"安装包"一定不对, 直接拒收 (防破损/错挂大文件)。
MAX_INSTALLER_BYTES = 1024 * 1024 * 1024

#: 下载超时。安装包约 100MB, 慢速网络也要能走完; 端点是线程服务,
#: 下载期间其它请求不受影响, 前端显示"正在下载"。
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 300.0

#: 安装程序启动后、退出旧进程前的等待。给 HTTP 响应留出落盘时间,
#: 让前端先看到"安装程序已启动"再断连 —— 否则用户看到的是"连接断开"。
SHUTDOWN_DELAY_SECONDS = 3.0

#: Inno Setup 静默参数。``/VERYSILENT`` = 全自动 (无向导、无进度条);
#: ``/CLOSEAPPLICATIONS`` = 关掉占着安装目录文件的进程 (旧进程正常情况
#: 下已经自己退了, 这是双保险); ``/NORESTART`` = 需要重启时不弹窗
#: (那种极端情形下用户重开仍是旧版本、弹窗会再次出现, 见 status 记录)。
INNO_SILENT_ARGS = ("/VERYSILENT", "/CLOSEAPPLICATIONS", "/NORESTART")


def parse_version(raw: str) -> tuple:
    """``"v1.2.0"`` / ``"1.2"`` / ``"1.2.0-beta"`` -> ``(1, 2, 0)``。

    非数字字符被切掉; 完全解析不出数字 -> 空元组 (调用方按"无法比较"处理)。
    """
    text = str(raw or "").strip().lstrip("vV")
    parts: list[int] = []
    for chunk in text.split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _default_fetch(url: str, timeout: float) -> bytes:
    from urllib.request import Request, urlopen  # noqa: PLC0415 - 延迟导入, 失败由调用方兜住

    request = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def check_for_update(
    current_version: str,
    *,
    fetch: Optional[Callable[[str, float], bytes]] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """检查最新 release。永不抛异常 —— 网络问题以 ``ok=False`` 回报。

    返回形状 (成功)::

        {"ok": True, "current": "1.2.0", "latest": "1.2.0",
         "update_available": False, "release_url": "...",
         "published_at": "...", "notes": "release 正文"}

    失败: ``{"ok": False, "error": "<原因>"}`` (原因给设置页展示)。
    """
    get = fetch or _default_fetch
    try:
        raw = get(GITHUB_RELEASES_LATEST, timeout)
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 网络/解析/任何失败都不该冒出去
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        return {"ok": False, "error": "release has no tag_name"}
    latest = parse_version(tag)
    current = parse_version(current_version)
    if not latest:
        return {"ok": False, "error": f"cannot parse latest tag {tag!r}"}
    if not current:
        return {"ok": False, "error": f"cannot parse current version {current_version!r}"}

    notes = payload.get("body") or ""
    return {
        "ok": True,
        "current": current_version,
        "latest": tag.lstrip("vV"),
        "update_available": latest > current,
        "release_url": str(payload.get("html_url") or ""),
        "published_at": str(payload.get("published_at") or ""),
        "notes": notes if isinstance(notes, str) else "",
    }


def install_supported() -> bool:
    """自动安装只在冻结产物 (PyInstaller 打包后的 .exe) + Windows 上可用。

    开发模式 (``python -m src.application.cli ...``) 下返回 False,
    前端回落到给出 release 直链供用户手动下载。
    """
    return is_frozen() and os.name == "nt"


def _default_launcher(path: str) -> None:
    """静默启动安装程序 (Inno Setup)。失败由 :func:`install_update` 兜住。"""
    subprocess.Popen(
        [path] + list(INNO_SILENT_ARGS),
        cwd=tempfile.gettempdir(),
        close_fds=True,
    )


def _default_shutdown(delay: float) -> None:
    """延迟退出当前进程, 让安装程序先接管安装目录。

    用 daemon 线程而不是直接 ``os._exit``: 调用方 (HTTP handler) 还要把
    "安装程序已启动"写回响应, 立刻退出会让前端看到连接断开。
    """

    def _die() -> None:
        import time  # noqa: PLC0415 - 仅在此处使用

        time.sleep(delay)
        os._exit(0)

    threading.Thread(target=_die, daemon=True).start()


def install_update(
    latest_version: str,
    release_url: str,
    *,
    fetch: Optional[Callable[[str, float], bytes]] = None,
    download_timeout: float = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    shutdown_delay: float = SHUTDOWN_DELAY_SECONDS,
    launcher: Optional[Callable[[str], None]] = None,
    shutdown: Optional[Callable[[float], None]] = None,
) -> dict[str, Any]:
    """下载最新 release 的安装包、静默启动 Inno Setup、延迟退出旧进程。

    永不抛异常 —— 所有失败以 ``{"ok": False, "error": ...}`` 回报。
    安装包文件名必须匹配白名单 ``Loomind-*-Setup.exe``, 下载只接受 https,
    体积有上限, 防止把错误的附件当安装包执行。

    返回::

        {"ok": True, "installer": "<文件名>", "path": "<下载路径>"}
        或 {"ok": False, "error": "<原因>"}
    """
    if not install_supported():
        return {"ok": False, "error": "auto-install only supported on frozen Windows builds"}

    get = fetch or _default_fetch
    try:
        raw = get(GITHUB_RELEASES_LATEST, download_timeout)
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 网络/解析失败不冒出去
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    assets = payload.get("assets") or []
    installer = None
    for asset in assets:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if INSTALLER_FILENAME_PATTERN.match(name) and url.startswith("https://"):
            installer = (name, url)
            break
    if not installer:
        return {"ok": False, "error": "no matching installer asset in release"}

    name, url = installer
    try:
        data = get(url, download_timeout)
        if len(data) > MAX_INSTALLER_BYTES:
            return {"ok": False, "error": f"installer too large: {len(data)} bytes"}
        fd, path = tempfile.mkstemp(suffix=".exe", prefix="loomind-update-")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        (launcher or _default_launcher)(path)
    except Exception as exc:  # noqa: BLE001
        try:
            os.unlink(path)
        except OSError:
            pass
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # 安装程序启动后再杀旧进程, 给 HTTP 响应留出落盘时间, 让前端
    # 先看到"安装程序已启动"再断连。
    (shutdown or _default_shutdown)(shutdown_delay)
    return {"ok": True, "installer": name, "path": path}
