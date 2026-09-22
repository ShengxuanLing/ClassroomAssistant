# -*- coding: utf-8 -*-
"""统一日志与隐私过滤 (Task 44)。

规范要求
--------------------------------------------------------------------

- 级别: ``DEBUG / INFO / WARNING / ERROR / CRITICAL`` (5 级, 不多不少)。
- 每条日志至少包含: ``timestamp / level / component / event / message``。
- 日志**禁止**写入: 完整用户答案、API key、secret、不必要的课堂原文、
  敏感文件内容。
- 生产环境: 用户看到友好错误, 日志保留详细错误; 开发环境可以暴露 debug。

设计取舍
--------------------------------------------------------------------

**为什么不用 ``extra={"message": ...}``。** 这是本项目里真实踩过的坑:
``logging`` 保留 ``message`` / ``asctime`` 两个属性名, 传进去会直接抛
``KeyError: "Attempt to overwrite 'message' in LogRecord"``。而
``BaseHTTPRequestHandler.send_response`` 会调 ``log_message`` —— 于是"注入
一个 logger"这件事会让**每一个 HTTP 响应**在发头之前崩掉 (客户端看到的是
``RemoteDisconnected``, 服务端只有一条 traceback)。因此本模块把约定固定为:

- ``event``        <- ``extra["event"]``, 缺省取 logger 调用的第一个参数
- ``message``      <- ``extra["message_text"]``, 缺省取 ``record.getMessage()``
- ``component``    <- ``extra["component"]``, 缺省取 logger 名
- ``detail``       <- ``extra["detail"]`` (映射, 递归脱敏)

**为什么脱敏放在 formatter 与 filter 两处。** filter 管 ``extra`` 里的结构化
字段 (按**键名**判断语义), formatter 管最终字符串 (按**值的形状**兜底)。
只做一处一定会漏: 键名不敏感但值里嵌了 ``api_key=xxx`` 的日志, 只有
formatter 能看见; 而 ``extra={"answer": <3000 字>}`` 这种, 只有 filter 能
在它变成字符串之前截住。

时间戳来自 :mod:`src.application.runtime` 的可注入 ``Clock`` —— 它是运行期
元数据, 属于规范允许的非确定性范围, 绝不参与任何业务 identity。
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Mapping, Optional

from src.application.config import (
    DEFAULT_LOG_LEVEL,
    LOG_LEVELS,
    SECRET_PLACEHOLDER,
    is_secret_key,
    redact_mapping,
    redact_text,
    safe_excerpt,
)
from src.application.errors import ConfigurationError
from src.application.runtime import Clock, utc_now_iso

__all__ = [
    "LOG_LEVELS",
    "REQUIRED_LOG_FIELDS",
    "BULK_CONTENT_EXTRA_KEYS",
    "RESERVED_EXTRA_KEYS",
    "DEFAULT_EXCERPT_LIMIT",
    "StructuredFormatter",
    "PrivacyFilter",
    "configure_logging",
    "get_logger",
    "log_event",
    "reset_logging",
]

#: 规范点名的 5 个必需字段 (顺序即展示顺序)。
REQUIRED_LOG_FIELDS: tuple[str, ...] = (
    "timestamp",
    "level",
    "component",
    "event",
    "message",
)

#: 这些 ``extra`` 键承载"整段内容", 一律只保留有界摘要。
#:
#: 命中它们的不是"疑似密钥", 而是"可能整段搬运用户内容" —— 学生答案、
#: 课堂原文、上传文件正文都属于此类。
BULK_CONTENT_EXTRA_KEYS: frozenset[str] = frozenset(
    {
        "answer",
        "answer_text",
        "user_answer",
        "student_answer",
        "expected_answer",
        "full_text",
        "raw_text",
        "raw_content",
        "content",
        "file_content",
        "file_contents",
        "document_text",
        "material_text",
        "transcript",
        "transcript_text",
        "prompt",
        "response",
        "messages",
        "body",
        "payload",
    }
)

#: ``logging`` 自己占用的属性名 —— 作为 ``extra`` 传入会直接抛 KeyError。
RESERVED_EXTRA_KEYS: frozenset[str] = frozenset(
    {"message", "asctime", "msg", "args", "exc_info", "levelname", "name"}
)

#: 日志里单段文本的默认上限 (超出即截断并显式标注)。
DEFAULT_EXCERPT_LIMIT = 200

#: 打在本模块安装的 handler 上的标记, 用于幂等安装与精确卸载。
_HANDLER_MARKER = "_classroom_assistant_handler"


def _one_line(text: str) -> str:
    """把文本压成单行 —— 一条日志一行, 才能被 grep / 被日志系统按行收集。"""
    return text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def _resolve_level(level: Any) -> int:
    """字符串/整数 -> logging 级别数字; 不合法直接抛 ConfigurationError。"""
    if isinstance(level, bool):
        raise ConfigurationError(f"invalid log level: {level!r}")
    if isinstance(level, int):
        return level
    text = str(level or "").strip().upper()
    if text in LOG_LEVELS:
        return int(getattr(logging, text))
    raise ConfigurationError(
        f"invalid log level {level!r}; expected one of {list(LOG_LEVELS)}",
        detail={"level": level, "allowed": list(LOG_LEVELS)},
    )


class StructuredFormatter(logging.Formatter):
    """输出 ``timestamp / level / component / event / message`` 的 formatter。

    两种输出: 人类可读单行 (默认) 与 JSON (``json_output=True``)。两者都保证
    5 个字段存在 —— JSON 形态便于机器解析, 人类可读形态便于直接看日志。
    """

    def __init__(
        self,
        *,
        clock: Optional[Clock] = None,
        json_output: bool = False,
        excerpt_limit: int = DEFAULT_EXCERPT_LIMIT,
    ) -> None:
        super().__init__()
        self._clock: Clock = clock or utc_now_iso
        self._json = bool(json_output)
        self._excerpt_limit = int(excerpt_limit)

    # ------------------------------------------------------------------

    def fields_for(self, record: logging.LogRecord) -> dict[str, Any]:
        """抽出 5 个必需字段 (+ 可选 detail / exception)。"""
        component = getattr(record, "component", None) or record.name or "-"

        event = getattr(record, "event", None)
        if event is None:
            event = record.msg if isinstance(record.msg, str) else type(record.msg).__name__

        message = getattr(record, "message_text", None)
        if message is None:
            try:
                message = record.getMessage()
            except Exception:  # noqa: BLE001 - 格式化参数不匹配时绝不吞掉日志
                message = str(record.msg)

        fields: dict[str, Any] = {
            "timestamp": str(self._clock()),
            "level": record.levelname,
            "component": str(component),
            "event": redact_text(str(event), limit=self._excerpt_limit),
            "message": redact_text(str(message), limit=self._excerpt_limit),
        }

        detail = getattr(record, "detail", None)
        if detail is not None:
            fields["detail"] = redact_mapping(detail)

        # 其余 ``extra`` 字段 (``log_event(**extra)`` 传进来的) 归入 ``extra``。
        # 不渲染它们的话, 调用方传了字段却什么也看不到 —— 这种"静默丢弃"
        # 会让人以为日志系统坏了。它们已经被 PrivacyFilter 处理过。
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RECORD_BUILTINS
            and key not in ("event", "message_text", "component", "detail")
        }
        if extras:
            fields["extra"] = redact_mapping(extras)

        if record.exc_info:
            fields["exception"] = _one_line(self.formatException(record.exc_info))
        return fields

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        fields = self.fields_for(record)
        if self._json:
            # default=str: 日志绝不该因为一个不可序列化的字段而崩掉。
            return json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str)

        head = (
            f"{fields['timestamp']} {fields['level']:<8} "
            f"{fields['component']} event={fields['event']} {fields['message']}"
        )
        for name in ("detail", "extra"):
            if name in fields:
                head += f" {name}=" + json.dumps(
                    fields[name], ensure_ascii=False, sort_keys=True, default=str
                )
        if "exception" in fields:
            return head + f"\n{fields['exception']}"
        return _one_line(head)


class PrivacyFilter(logging.Filter):
    """在日志记录变成字符串**之前**处理 ``extra`` 里的敏感字段。

    两档处理:

    - 键名命中密钥模式 (``api_key`` / ``token`` / ``password`` ...) -> 整体替换。
      不能做摘要: 摘要会留下密钥的前若干字符, 而密钥往往整体都不该出现。
    - 键名属于"整段内容" (``answer`` / ``transcript`` / ``content`` ...) -> 有界摘要。
      摘要比直接丢弃更好: 日志仍然能告诉你"当时确实有这么一段、有多长",
      但完整答案不会落盘。
    """

    def __init__(self, *, excerpt_limit: int = DEFAULT_EXCERPT_LIMIT) -> None:
        super().__init__()
        self._excerpt_limit = int(excerpt_limit)

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        for key in list(record.__dict__):
            if key in _RECORD_BUILTINS:
                continue
            value = record.__dict__[key]
            if is_secret_key(key):
                record.__dict__[key] = SECRET_PLACEHOLDER
            elif key in BULK_CONTENT_EXTRA_KEYS:
                record.__dict__[key] = safe_excerpt(value, self._excerpt_limit)
        return True


#: ``LogRecord.__init__`` 自己设置的属性 (这些不是调用方传的 extra)。
_RECORD_BUILTINS: frozenset[str] = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message", "asctime",
    }
)


def _installed_handler(logger: logging.Logger) -> Optional[logging.Handler]:
    for handler in logger.handlers:
        if getattr(handler, _HANDLER_MARKER, False):
            return handler
    return None


def configure_logging(
    config: Optional[Any] = None,
    *,
    level: Optional[Any] = None,
    stream: Optional[Any] = None,
    clock: Optional[Clock] = None,
    json_output: bool = False,
    excerpt_limit: int = DEFAULT_EXCERPT_LIMIT,
    name: Optional[str] = None,
    force: bool = False,
) -> logging.Handler:
    """安装 (或复用) 一个结构化日志 handler, 返回它。

    **幂等**: 重复调用不会叠加 handler —— 否则同一个进程里每个日志会打印
    多份, 而"日志重复"这种事往往到线上才发现。``force=True`` 用于显式重建
    (例如改变输出流或格式)。

    优先级: 显式 ``level`` > ``config.log_level`` > ``DEFAULT_LOG_LEVEL``。
    """
    target = logging.getLogger(name) if name else logging.getLogger()

    if level is not None:
        resolved = level
    elif config is not None:
        resolved = getattr(config, "log_level", DEFAULT_LOG_LEVEL)
    else:
        resolved = DEFAULT_LOG_LEVEL
    numeric = _resolve_level(resolved)

    existing = _installed_handler(target)
    if existing is not None and not force:
        existing.setLevel(numeric)
        target.setLevel(numeric)
        return existing
    if existing is not None:
        target.removeHandler(existing)
        existing.close()

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setLevel(numeric)
    handler.setFormatter(
        StructuredFormatter(clock=clock, json_output=json_output, excerpt_limit=excerpt_limit)
    )
    handler.addFilter(PrivacyFilter(excerpt_limit=excerpt_limit))
    setattr(handler, _HANDLER_MARKER, True)
    target.addHandler(handler)
    target.setLevel(numeric)
    return handler


def reset_logging(name: Optional[str] = None) -> int:
    """卸载本模块安装的 handler; 返回卸载数量 (测试与进程重启用)。"""
    target = logging.getLogger(name) if name else logging.getLogger()
    removed = 0
    for handler in list(target.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            target.removeHandler(handler)
            handler.close()
            removed += 1
    return removed


def get_logger(component: str) -> logging.Logger:
    """按组件名取 logger (``component`` 字段的来源)。"""
    return logging.getLogger(component)


def log_event(
    logger: logging.Logger,
    level: Any,
    event: str,
    message: Optional[Any] = None,
    *,
    detail: Optional[Mapping[str, Any]] = None,
    **extra: Any,
) -> None:
    """本项目的**唯一**结构化打点方式。

    把 ``event`` / ``message`` / ``detail`` 放进 ``extra`` 的**安全**键名里
    (``message_text``, 不是被 logging 保留的 ``message``), 因此:

    - 调用方永远不需要记住"哪个键名会炸";
    - 传了保留键名会立刻得到可读的 :class:`ConfigurationError`, 而不是
      一个来自 logging 内部的 ``KeyError``。
    """
    payload: dict[str, Any] = {"event": event}
    if message is not None:
        payload["message_text"] = str(message)
    if detail is not None:
        payload["detail"] = detail
    payload.update(extra)

    reserved = sorted(key for key in payload if key in RESERVED_EXTRA_KEYS)
    if reserved:
        raise ConfigurationError(
            f"log field name(s) {reserved} are reserved by the logging module; "
            "use log_event(message=...) / detail=... instead",
            detail={"reserved": reserved},
        )
    logger.log(_resolve_level(level), event, extra=payload)
