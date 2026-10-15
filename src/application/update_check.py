# -*- coding: utf-8 -*-
"""更新检查 (GitHub Releases)。

打 ``https://api.github.com/repos/ShengxuanLing/Loomind/releases/latest``,
与 ``APPLICATION_VERSION`` 比较。**只检查、不下载、不安装** —— 自动安装是不可逆
操作 (替换 exe / 数据库), 需要独立的确认流程; 这里给出"有新版本 + 下载链接",
由用户在设置页决定下一步。

设计约束:

- ``fetch`` 可注入 (测试用桩), 默认 ``urllib`` + 8s 超时;
- **网络失败不抛异常**: 返回 ``ok=False`` + 原因 —— 设置页照常渲染,
  用户看到的是"检查失败 (超时/离线)", 而不是一个错误页;
- 版本比较是纯数字逐段比 (``v1.10.0 > v1.9.0``), 预发布后缀 (``-beta``)
  只取前缀数字段 —— 比较"是否更新"够用, 不做完整 semver。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

__all__ = [
    "GITHUB_RELEASES_LATEST",
    "parse_version",
    "check_for_update",
]


#: 品牌重塑后的正式仓库 (ClassroomAssistant -> Loomind, 旧名 301 重定向到这里)。
GITHUB_RELEASES_LATEST = (
    "https://api.github.com/repos/ShengxuanLing/Loomind/releases/latest"
)

#: 默认超时。设置页的"立即检查"是同步交互, 慢于这个值的网络没有显示价值。
DEFAULT_TIMEOUT_SECONDS = 8.0

#: GitHub API 要求 User-Agent, 否则 403。
_USER_AGENT = "Loomind-Update-Check"


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
