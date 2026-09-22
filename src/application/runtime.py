# -*- coding: utf-8 -*-
"""运行期原语 (Task 35 / Task 44)。

本模块集中存放**只允许存在于 runtime 层**的非确定性来源:

- 挂钟时间 (:func:`utc_now_iso`)
- 临时文件名
- 会话 token

硬性约束 (Task 47.2 Determinism Audit):
- 这些值**绝不能**进入任何业务对象的 identity (material_id / evidence_id /
  knowledge_id / student_id / exercise_id / evaluation_id / study_plan_id)。
- 需要确定性输出的调用方必须注入自己的 ``clock``, 而不是直接调用
  :func:`utc_now_iso`。
"""

from __future__ import annotations

import secrets
from typing import Callable, Optional

# P1-4: ``Clock`` / ``utc_now_iso`` / ``fixed_clock`` 的真源已收口到中立的
# ``src.common.clock`` (持久化层与应用层都需要注入式时钟, 但二者不得互相依赖,
# 故放在零依赖的 src.common 里)。此处只做**兼容 re-export**,
# 历史 ``from src.application.runtime import Clock`` 的调用方无需改动。
from src.common.clock import Clock, fixed_clock, utc_now_iso

__all__ = ["utc_now_iso", "new_runtime_token", "Clock", "fixed_clock"]


def new_runtime_token(length: int = 32) -> str:
    """生成一个随机运行期 token (绝不用于业务 identity)。"""
    return secrets.token_urlsafe(length)
