# -*- coding: utf-8 -*-
"""运行期时钟原语 (Task 35 / Task 44 / P1-4)。

本模块是 ``Clock`` / ``utc_now_iso`` / ``fixed_clock`` 的**唯一真源**, 位于
中立的 ``src.common`` 包内 —— ``src.persistence`` (迁移台账时间戳) 与
``src.application`` (日志 / 业务元数据) 都需要注入式时钟, 但分层规则禁止
这两个包互相依赖。把真源放在零依赖的 ``src.common`` 里, 两个方向都不会形成
逆向环。

``src.application.runtime`` 现在只做**兼容 re-export**: 历史
``from src.application.runtime import Clock`` 的调用方无需改动。任何新代码都应
直接 ``from src.common.clock import Clock``。

硬性约束 (Task 47.2 Determinism Audit):
- 这些值**绝不**进入任何业务对象的 identity。
- 需要确定性输出的调用方必须注入自己的 ``clock``, 而不是直接调用
  :func:`utc_now_iso`。
"""

from __future__ import annotations

import datetime as _dt
from typing import Callable, Optional

__all__ = ["utc_now_iso", "Clock", "fixed_clock"]


def utc_now_iso() -> str:
    """当前 UTC 时间 (ISO-8601, 秒精度)。

    仅用于运行期元数据 (created_at / started_at / finished_at 等展示字段),
    不参与任何 identity 计算。
    """
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


#: 可注入时钟: 返回 ISO-8601 字符串。
Clock = Callable[[], str]


def fixed_clock(value: str = "2026-01-01T00:00:00+00:00") -> Clock:
    """返回一个恒定时钟, 供测试获得完全确定性的时间戳。"""
    def _clock() -> str:
        return value

    return _clock
