# -*- coding: utf-8 -*-
"""轻量级应用操作日志 (Task 71.7 Pilot Logging)。

真实 Pilot 阶段, 我们需要知道"真实世界里发生了什么"以便排查真实问题。本模块
提供一个**追加写**的 JSON Lines 操作日志, 每条记录包含规范点名的字段::

    operation  操作名 (create_course / register_material / process_material /
               submit_answer / backup / restore ...)
    timestamp  操作发生的 UTC 时间 (ISO-8601; 来自可注入时钟, 不参与 identity)
    course     课程 id (如适用)
    session    课堂 id (如适用)
    material   材料 id (如适用)
    success    操作是否成功
    failure    失败原因 (仅 success=False 时出现, 已脱敏、有界截断)
    duration   操作耗时 (毫秒, 如测得)

硬性原则 (与 Task 44 隐私约束一致)
--------------------------------------------------------------------
- 绝不记录密码 / API key / secret / 完整隐私内容。复用 ``config`` 的脱敏原语
  (``redact_mapping`` + 有界截断), 任何 ``extra`` 字段里的密钥整体替换, 整段
  内容只保留有界摘要。
- **写失败绝不抛出**: 日志本身绝不能让一次业务操作失败。所有写入都在防御性
  包裹里, 出错时静默丢弃该条记录。
- 默认不记录大量原始课堂文本: 只有调用方显式传入的 ``extra`` 才会落盘, 且一定
  经过脱敏。
- 追加写 + 进程内锁, 保证同一进程内多次操作串行落盘, 不会交错。
- 软上限: 超过 ``max_entries`` 时整体重写为最近 N 条, 避免长跑 (Task 74.7)
  把磁盘写满。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from src.application.config import (
    SECRET_PLACEHOLDER,
    is_secret_key,
    redact_text,
    safe_excerpt,
    truncate,
)
from src.application.runtime import Clock, utc_now_iso

__all__ = [
    "OPERATION_LOG_FILENAME",
    "DEFAULT_MAX_ENTRIES",
    "BULK_CONTENT_KEYS",
    "OperationRecord",
    "OperationLog",
]

#: 操作日志文件名 (落在 ``<data_dir>/logs/`` 下; 该目录**不**被备份归档包含)。
OPERATION_LOG_FILENAME = "operations.log"

#: 操作日志的软上限 (超过则整体重写为最近 N 条)。防止长期运行写满磁盘。
DEFAULT_MAX_ENTRIES = 200_000

#: 失败原因字符串的有界长度 (再长也只保留前若干字符)。
_FAILURE_LIMIT = 300

#: 单条 ``extra`` 里**整段内容**型键: 一律只保留有界摘要 (绝不写全文)。
#: 与 ``logging_setup.BULK_CONTENT_EXTRA_KEYS`` 同源, 这里独立一份以避免与日志
#: 子系统耦合 (操作日志是轻量独立模块)。
BULK_CONTENT_KEYS: frozenset[str] = frozenset(
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

#: 非 bulk 的普通字符串值也设一个上限, 防止任意大值把日志写爆。
_PLAIN_STRING_LIMIT = 500


@dataclass(frozen=True)
class OperationRecord:
    """一条操作日志的结构化表示 (JSON Lines 里的一行)。"""

    operation: str
    timestamp: str
    success: bool
    course: Optional[str] = None
    session: Optional[str] = None
    material: Optional[str] = None
    duration_ms: Optional[float] = None
    failure: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "operation": self.operation,
            "timestamp": self.timestamp,
            "success": bool(self.success),
        }
        if self.course is not None:
            out["course"] = self.course
        if self.session is not None:
            out["session"] = self.session
        if self.material is not None:
            out["material"] = self.material
        if self.duration_ms is not None:
            out["duration_ms"] = self.duration_ms
        if self.failure is not None:
            out["failure"] = self.failure
        if self.extra:
            out["extra"] = self.extra
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OperationRecord":
        return cls(
            operation=str(data.get("operation", "")),
            timestamp=str(data.get("timestamp", "")),
            success=bool(data.get("success", False)),
            course=data.get("course"),
            session=data.get("session"),
            material=data.get("material"),
            duration_ms=data.get("duration_ms"),
            failure=data.get("failure"),
            extra=dict(data.get("extra") or {}),
        )


class OperationLog:
    """追加写 JSON Lines 操作日志。

    线程安全 (进程内锁)。写入失败一律静默 (绝不向上抛)。读取 (`recent` /
    `query`) 同样防御: 单行解析失败只跳过那一行, 不会让整个读取崩掉。
    """

    def __init__(
        self,
        logs_dir: str,
        *,
        clock: Optional[Clock] = None,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._logs_dir = os.path.abspath(logs_dir)
        self._path = os.path.join(self._logs_dir, OPERATION_LOG_FILENAME)
        self._clock: Clock = clock or utc_now_iso
        self._max_entries = int(max_entries)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------

    @property
    def path(self) -> str:
        return self._path

    # ------------------------------------------------------------------

    def record(
        self,
        operation: str,
        *,
        course: Optional[str] = None,
        session: Optional[str] = None,
        material: Optional[str] = None,
        success: bool = True,
        failure: Optional[str] = None,
        duration_ms: Optional[float] = None,
        **extra: Any,
    ) -> Optional[OperationRecord]:
        """记录一条操作; 任何失败都返回 ``None`` 且绝不抛出。"""
        try:
            safe_extra = self._sanitize_extra(extra)
            safe_failure = (
                None
                if failure is None
                else safe_excerpt(str(failure), _FAILURE_LIMIT)
            )
            record = OperationRecord(
                operation=str(operation),
                timestamp=str(self._clock()),
                success=bool(success),
                course=course,
                session=session,
                material=material,
                duration_ms=duration_ms,
                failure=safe_failure,
                extra=safe_extra,
            )
            self._append(record)
            return record
        except Exception:  # noqa: BLE001 - 日志绝不能让业务操作失败
            return None

    # ------------------------------------------------------------------

    def recent(self, n: int = 100) -> list[OperationRecord]:
        """返回最近 ``n`` 条记录 (按时间从旧到新)。"""
        return self._read_tail(n)

    def query(
        self,
        *,
        operation: Optional[str] = None,
        course: Optional[str] = None,
        success: Optional[bool] = None,
        limit: int = 200,
    ) -> list[OperationRecord]:
        """按条件过滤 (全部为可选 AND)。``limit`` 限制返回条数 (取最近的)。"""
        matches: list[OperationRecord] = []
        wanted_op = operation
        wanted_course = course
        wanted_success = success
        # 从尾部读, 直到攒够 limit 或读完。
        lines = self._read_lines_from_tail()
        for raw in reversed(lines):
            rec = self._parse_line(raw)
            if rec is None:
                continue
            if wanted_op is not None and rec.operation != wanted_op:
                continue
            if wanted_course is not None and rec.course != wanted_course:
                continue
            if wanted_success is not None and rec.success != wanted_success:
                continue
            matches.append(rec)
            if len(matches) >= limit:
                break
        matches.reverse()
        return matches

    def count(self, *, success: Optional[bool] = None) -> int:
        """统计记录条数 (可选按成功/失败过滤)。"""
        total = 0
        for raw in self._iter_lines():
            rec = self._parse_line(raw)
            if rec is None:
                continue
            if success is None or rec.success == success:
                total += 1
        return total

    def clear(self) -> int:
        """清空日志文件 (测试 / 维护用); 返回被清掉的字节数。返回 0 表示无文件。"""
        try:
            if not os.path.exists(self._path):
                return 0
            size = os.path.getsize(self._path)
            with self._lock:
                os.remove(self._path)
            return size
        except OSError:
            return 0

    # ------------------------------------------------------------------
    # 内部原语

    @staticmethod
    def _sanitize_extra(extra: Mapping[str, Any]) -> dict[str, Any]:
        """脱敏并截断 ``extra``; 任何密钥整体替换, 任何长文本有界摘要。

        递归 (深度上限), 保证: 密钥型键 -> ``SECRET_PLACEHOLDER``; 整段内容型键
        -> 200 字摘要; 其它字符串 -> 500 字上限; 嵌套结构同样处理。绝不写密码 /
        API key / 完整隐私原文。
        """
        if not extra:
            return {}
        return {str(k): OperationLog._redact_value(k, v) for k, v in sorted(extra.items(), key=lambda kv: str(kv[0]))}

    @staticmethod
    def _redact_value(key: Any, value: Any, depth: int = 0) -> Any:
        if depth > 6:
            return {"__truncated__": "max-depth"}
        if is_secret_key(key):
            return SECRET_PLACEHOLDER
        if isinstance(value, Mapping):
            return {
                str(k): OperationLog._redact_value(k, v, depth + 1)
                for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
            }
        if isinstance(value, (list, tuple)):
            return [OperationLog._redact_value(key, item, depth + 1) for item in value]
        if isinstance(value, str):
            redacted = redact_text(value)  # 先抹密钥形状
            if str(key).lower() in BULK_CONTENT_KEYS:
                return safe_excerpt(redacted, 200)  # 整段内容 -> 200 字摘要
            return truncate(redacted, _PLAIN_STRING_LIMIT)
        return value

    def _append(self, record: OperationRecord) -> None:
        with self._lock:
            os.makedirs(self._logs_dir, exist_ok=True)
            line = json.dumps(
                record.to_dict(), ensure_ascii=False, sort_keys=True, default=str
            )
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.write("\n")
            self._maybe_rotate_locked()

    def _maybe_rotate_locked(self) -> None:
        if self._max_entries <= 0:
            return
        try:
            size = os.path.getsize(self._path)
        except OSError:
            return
        if size == 0:
            return
        # 轻量探测行数: 只在触顶附近重写, 避免每次都全量读。
        try:
            with open(self._path, "rb") as handle:
                # 跳到接近末尾, 统计最后的换行数估计是否接近上限。
                handle.seek(max(0, size - 1_000_000))
                tail = handle.read()
            newlines = tail.count(b"\n")
        except OSError:
            return
        if newlines < self._max_entries:
            return
        # 触顶: 读全部, 取最近 max_entries 条, 整体重写。
        kept = self._read_tail_locked(self._max_entries)
        tmp = self._path + ".rotating"
        with open(tmp, "w", encoding="utf-8") as handle:
            for rec in kept:
                handle.write(
                    json.dumps(rec.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
                )
                handle.write("\n")
        os.replace(tmp, self._path)

    def _iter_lines(self) -> Iterable[str]:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                for line in handle:
                    yield line
        except OSError:
            return

    def _read_lines_from_tail(self) -> list[str]:
        lines = list(self._iter_lines())
        return lines

    def _read_tail(self, n: int) -> list[OperationRecord]:
        with self._lock:
            return self._read_tail_locked(n)

    def _read_tail_locked(self, n: int) -> list[OperationRecord]:
        records: list[OperationRecord] = []
        for raw in self._iter_lines():
            rec = self._parse_line(raw)
            if rec is None:
                continue
            records.append(rec)
            if len(records) > n:
                records.pop(0)
        return records

    @staticmethod
    def _parse_line(raw: str) -> Optional[OperationRecord]:
        text = raw.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                return None
            return OperationRecord.from_dict(data)
        except (ValueError, TypeError):
            return None
