# -*- coding: utf-8 -*-
"""用户设置 —— 设置页开关的持久化真相源。

与 ``AppConfig`` 的分工 (刻意分层):

- ``AppConfig`` 是**启动配置** (env / 配置文件 / CLI), 键集合由配置测试逐键
  钉死, 在那里加键会扰动现有门禁;
- 本模块是**运行时用户选择** —— 设置页上的开关, 落在 ``<data_dir>/settings.json``,
  用 ``atomic_write_bytes`` 写 (与数据库、材料索引同口径的原子写)。

字段语义 (``None`` = 从未设置 = 跟随环境默认; 显式 ``False`` = 用户关掉):

- ``local_ai_enabled``: AI 自动管线开关。``None`` 时沿用 ``LOOMIND_AI_ENABLED``;
  ``False`` 时无论环境如何都关闭; ``True`` 时开启 (无凭证的诚实回落是确定性
  Fake provider, 与 bootstrap 的口径一致, 见 bootstrap 的 runtime note)。
- ``autostart``: 开机自启 (Windows 注册表 Run 键)。默认 ``False``。
- ``auto_update_check``: 启动时自动检查更新。默认 ``True`` —— 检查是只读的
  网络请求, 失败不打扰用户 (只记一条日志)。

界面语言**不在**这里: 它是浏览器侧状态 (localStorage ``ca.lang`` + 顶栏选择器),
服务端持久化会让"哪个浏览器看的语言"变得不确定, 且现有机制零改动即可工作。

损坏处理: ``settings.json`` 不存在 / JSON 非法 / 字段类型不对 -> 回落默认值,
并把原因记进 ``load()`` 返回的 ``notes`` (端点层不展示, 但绝不静默吞掉)。
未知字段**忽略** (前向兼容: 新版本写的键, 旧版本读到时不该炸)。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from src.application.data_dirs import atomic_write_bytes

__all__ = [
    "SETTINGS_FILENAME",
    "UserSettings",
    "SettingsStore",
]


#: 数据目录根下的设置文件名 (不是 DATA_LAYOUT_DIRS 之一 —— 那些是**目录**,
#: 布局守卫逐目录钉死; 根级文件与 ``.run/`` 同性质, 是运行期元数据)。
SETTINGS_FILENAME = "settings.json"

#: 可持久化的三个开关。白名单之外一切字段忽略 (含未来的新字段)。
_SETTING_KEYS = ("local_ai_enabled", "autostart", "auto_update_check")


def _coerce_optional_bool(value: Any, field: str) -> Optional[bool]:
    """把存储值收敛成 Optional[bool]。

    接受真布尔与 ``"true"/"false"/"1"/"0"`` 文本 (JSON 里可能是任意形状);
    其余一律 ``ValueError`` —— 端点层把它翻成 INVALID_INPUT, 用户看到的是
    结构化错误而不是一份被悄悄改成默认值的设置。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off"):
            return False
    raise ValueError(f"setting {field!r} must be a boolean (got {value!r})")


@dataclass(frozen=True)
class UserSettings:
    """三个开关的当前值。``None`` = 跟随环境默认。"""

    local_ai_enabled: Optional[bool] = None
    autostart: bool = False
    auto_update_check: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_ai_enabled": self.local_ai_enabled,
            "autostart": self.autostart,
            "auto_update_check": self.auto_update_check,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "UserSettings":
        """从存储字典构造。未知键忽略; 已知键类型非法抛 ValueError。"""
        if not isinstance(data, Mapping):
            raise ValueError("settings must be a JSON object")
        values: dict[str, Any] = {}
        for key in _SETTING_KEYS:
            if key not in data:
                continue
            raw = data[key]
            if key == "local_ai_enabled":
                values[key] = _coerce_optional_bool(raw, key)
            else:
                # autostart / auto_update_check 是**显式**布尔, 没有 None 形状
                # (None 只对 local_ai_enabled 有意义: 它要表达"未设置")。
                coerced = _coerce_optional_bool(raw, key)
                if coerced is None:
                    raise ValueError(f"setting {key!r} must be a boolean (got null)")
                values[key] = coerced
        return cls(**values)


class SettingsStore:
    """``<data_dir>/settings.json`` 的读写。

    线程安全由调用方 (HTTP 服务器) 的处理顺序保证: PATCH 是唯一的写路径,
    且同一时刻只有一个请求在跑设置更新 —— 与材料上传暂存同口径。
    """

    def __init__(self, data_dir: str) -> None:
        self._path = os.path.join(os.path.abspath(data_dir), SETTINGS_FILENAME)

    @property
    def path(self) -> str:
        return self._path

    def load(self) -> tuple[UserSettings, list[str]]:
        """读设置。文件不存在 -> 全默认值, 零 notes。

        损坏 (非法 JSON / 非法字段值) -> 默认值 + 一条 note。**绝不抛异常**:
        一份坏设置不该让应用起不来, 但也不能静默 —— note 进运行日志。
        """
        if not os.path.isfile(self._path):
            return UserSettings(), []
        try:
            with open(self._path, "rb") as handle:
                payload = json.loads(handle.read().decode("utf-8"))
            return UserSettings.from_dict(payload), []
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            return UserSettings(), [
                f"settings file unreadable, using defaults: {exc}"
            ]

    def save(self, settings: UserSettings) -> None:
        """原子写。排序后的规范化 JSON —— 同内容重写不产生字节差异。"""
        text = json.dumps(settings.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
        atomic_write_bytes(self._path, (text + "\n").encode("utf-8"))

    def update(self, **patch: Any) -> tuple[UserSettings, list[str]]:
        """load -> 合并 -> save。返回新值 + 本次的 notes (通常为空)。

        字段值非法时抛 ValueError (端点层 -> INVALID_INPUT), **不写盘**:
        半合法的设置写下去比拒绝更糟 (用户以为改成功了)。
        """
        current, notes = self.load()
        merged = current.to_dict()
        for key, value in patch.items():
            if key not in _SETTING_KEYS:
                raise ValueError(f"unknown setting {key!r}")
            if key == "local_ai_enabled":
                merged[key] = _coerce_optional_bool(value, key)
            else:
                coerced = _coerce_optional_bool(value, key)
                if coerced is None:
                    raise ValueError(f"setting {key!r} must be a boolean (got null)")
                merged[key] = coerced
        updated = UserSettings.from_dict(merged)
        self.save(updated)
        return updated, notes
