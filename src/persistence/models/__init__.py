# -*- coding: utf-8 -*-
"""行 <-> 领域对象的转换层 (Task 42)。

- :mod:`src.persistence.models.codec` —— payload 的规范化 JSON 编解码,
  以及 SQLite 与 Python 之间的标量转换 (bool/None/数值)。
- :mod:`src.persistence.models.tables` —— 表规格注册表 (表名 / 主键 /
  默认确定性排序 / payload 版本)。

这一层的存在理由是: **序列化规则必须只有一处**。仓储只负责"把列写对",
不负责"怎么把领域对象变成字节" —— 后者一旦分散到 12 个仓储里, 迟早会
出现"这个仓储多转义了一次、那个少存了一个字段"。
"""

from __future__ import annotations

from src.persistence.models.codec import (
    as_list,
    as_mapping,
    canonical_json,
    decode_payload,
    encode_payload,
    from_db_bool,
    opt_float,
    opt_int,
    opt_str,
    payload_checksum,
    to_db_bool,
)
from src.persistence.models.tables import LINK_TABLES, TABLES, TableSpec, spec_for

__all__ = [
    "TABLES",
    "LINK_TABLES",
    "TableSpec",
    "spec_for",
    "encode_payload",
    "decode_payload",
    "canonical_json",
    "payload_checksum",
    "to_db_bool",
    "from_db_bool",
    "opt_str",
    "opt_int",
    "opt_float",
    "as_list",
    "as_mapping",
]
