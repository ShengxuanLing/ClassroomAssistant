# -*- coding: utf-8 -*-
"""payload 编解码 (Task 42)。

规范要求: "数据库读取出来的对象必须和当前 Domain model 正确转换"。

做法: 数据库里的 ``payload`` 列就是领域对象**自己** ``to_dict()`` 的结果,
经**规范化 JSON** 编码。这样:

- 完整性由领域层保证 —— 仓储不需要 (也不允许) 手写字段映射, 因此不可能
  漏掉某个字段。溯源 (``source_reference``)、``metadata`` 这类开放字典、
  以及将来新增的字段都自动跟随。
- 确定性由本模块保证 —— 键排序、分隔符固定, 同一对象永远编码成同一串
  字节, 因此 payload 的 sha256 可以作为"内容未被改动"的证据。

``ensure_ascii=False`` 是刻意的: 西语 / 加泰语 / 中文必须原样落库,
而不是变成 ``\\uXXXX`` 转义。规范要求"保留原文", 这一条同样适用于存储层。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Optional

from src.persistence.errors import PayloadDecodeError, PersistenceValidationError

__all__ = [
    "encode_payload",
    "decode_payload",
    "payload_checksum",
    "canonical_json",
    "to_db_bool",
    "from_db_bool",
    "opt_str",
    "opt_int",
    "opt_float",
    "as_list",
    "as_mapping",
]

#: 规范化 JSON 的编码参数 (确定性: 键排序 + 紧凑分隔符 + 不转义非 ASCII)。
_ENCODE_KWARGS = {
    "ensure_ascii": False,
    "sort_keys": True,
    "separators": (",", ":"),
}


def canonical_json(value: Any) -> str:
    """把任意可 JSON 化的值编码成规范化字符串 (确定性)。"""
    try:
        return json.dumps(value, **_ENCODE_KWARGS)
    except (TypeError, ValueError) as exc:
        raise PersistenceValidationError(
            f"payload is not JSON-serializable: {exc}", cause=exc
        ) from exc


def encode_payload(payload: Any) -> str:
    """领域对象 -> 数据库 payload 文本。

    接受 ``Mapping`` 或任何有 ``to_dict()`` 的对象; 其他类型一律拒绝 ——
    静默地把 ``str(obj)`` 存进去会让读取端拿到无法还原的垃圾。
    """
    if payload is None:
        raise PersistenceValidationError("payload must not be None")
    if hasattr(payload, "to_dict") and not isinstance(payload, Mapping):
        payload = payload.to_dict()
    if not isinstance(payload, Mapping):
        raise PersistenceValidationError(
            f"payload must be a mapping or expose to_dict(), got {type(payload).__name__}"
        )
    return canonical_json(dict(payload))


def decode_payload(text: Any, *, context: Optional[str] = None) -> dict[str, Any]:
    """数据库 payload 文本 -> dict。

    非 JSON / 非对象 -> :class:`PayloadDecodeError` (映射到 ``STORAGE_ERROR``),
    而不是让 ``json.JSONDecodeError`` 泄漏, 也不静默返回 ``{}``
    —— 后者会把"数据损坏"伪装成"数据为空"。
    """
    if text is None:
        raise PayloadDecodeError(
            f"payload is NULL{f' ({context})' if context else ''}"
        )
    if isinstance(text, (bytes, bytearray)):
        try:
            text = bytes(text).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PayloadDecodeError(
                f"payload is not valid UTF-8{f' ({context})' if context else ''}",
                cause=exc,
            ) from exc
    if not isinstance(text, str):
        raise PayloadDecodeError(
            f"payload must be TEXT, got {type(text).__name__}"
            f"{f' ({context})' if context else ''}"
        )
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PayloadDecodeError(
            f"payload is not valid JSON{f' ({context})' if context else ''}: {exc}",
            cause=exc,
        ) from exc
    if not isinstance(value, dict):
        raise PayloadDecodeError(
            f"payload must decode to an object, got {type(value).__name__}"
            f"{f' ({context})' if context else ''}"
        )
    return value


def payload_checksum(text: str) -> str:
    """payload 文本的 sha256 (十六进制)。

    用于测试"写进去和读出来逐字节一致", 也可用于备份校验 (Task 43)。
    """
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------
# SQLite <-> Python 的标量转换
# ----------------------------------------------------------------------
#
# SQLite 没有原生 bool: 一律用 0/1 存储。转换必须显式, 否则 True 会
# 静默变成 1 再变成 True —— 看似正常, 但 None 会被当成 False。


def to_db_bool(value: Any) -> Optional[int]:
    """bool -> 0/1; None 保持 None (三态: 未知 != False)。"""
    if value is None:
        return None
    return 1 if value else 0


def from_db_bool(value: Any) -> Optional[bool]:
    """0/1 -> bool; None 保持 None。"""
    if value is None:
        return None
    return bool(value)


def opt_str(value: Any) -> Optional[str]:
    """None 保持 None; 其他转成 str。空串保留为空串 (它不是 None)。"""
    if value is None:
        return None
    return str(value)


def opt_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def opt_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_list(value: Any) -> list:
    """None -> []; 已是 list -> 原样; 其他可迭代 -> list。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set, frozenset)):
        return list(value)
    return []


def as_mapping(value: Any) -> dict:
    if isinstance(value, Mapping):
        return dict(value)
    return {}
