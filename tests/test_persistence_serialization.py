# -*- coding: utf-8 -*-
"""Task 42 —— payload 编解码与序列化测试。

规范要求: "数据库读取出来的对象必须和当前 Domain model 正确转换"。
这里证明三件事:

1. **无损**: 原文 (西语 / 加泰语 / 中文) 逐字节保留, 嵌套结构不丢。
2. **确定性**: 同一对象永远编码成同一串字节 (键排序 + 固定分隔符)。
3. **不静默**: 损坏的 payload 抛结构化错误, 绝不返回空 dict。
"""

import hashlib
import json

import pytest

from src.persistence.errors import PayloadDecodeError, PersistenceValidationError
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

# ----------------------------------------------------------------------
# canonical_json
# ----------------------------------------------------------------------


def test_canonical_json_sorts_keys():
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_canonical_json_is_key_order_independent():
    assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})


def test_canonical_json_uses_compact_separators():
    assert " " not in canonical_json({"a": [1, 2], "b": {"c": 3}})


def test_canonical_json_does_not_escape_non_ascii():
    assert canonical_json({"t": "Álgebra"}) == '{"t":"Álgebra"}'


def test_canonical_json_preserves_catalan():
    text = "La funció és contínua"
    assert text in canonical_json({"t": text})


def test_canonical_json_preserves_chinese():
    text = "资本与函数"
    assert text in canonical_json({"t": text})


def test_canonical_json_is_deterministic():
    payload = {"z": 1, "a": {"y": 2, "b": 3}, "m": [1, "x", None, True]}
    assert canonical_json(payload) == canonical_json(payload)


def test_canonical_json_rejects_non_serializable():
    with pytest.raises(PersistenceValidationError):
        canonical_json({"bad": object()})


def test_canonical_json_handles_nested_containers():
    payload = {"a": [{"b": [1, 2, {"c": "d"}]}]}
    assert json.loads(canonical_json(payload)) == payload


# ----------------------------------------------------------------------
# encode_payload
# ----------------------------------------------------------------------


def test_encode_payload_from_mapping():
    assert encode_payload({"a": 1}) == '{"a":1}'


def test_encode_payload_accepts_objects_with_to_dict():
    class Thing:
        def to_dict(self):
            return {"a": 1}

    assert encode_payload(Thing()) == '{"a":1}'


def test_encode_payload_rejects_none():
    with pytest.raises(PersistenceValidationError):
        encode_payload(None)


def test_encode_payload_rejects_plain_string():
    with pytest.raises(PersistenceValidationError):
        encode_payload("just a string")


def test_encode_payload_rejects_list():
    with pytest.raises(PersistenceValidationError):
        encode_payload([1, 2, 3])


def test_encode_payload_rejects_non_serializable_values():
    with pytest.raises(PersistenceValidationError):
        encode_payload({"bad": object()})


def test_encode_payload_error_names_the_type():
    with pytest.raises(PersistenceValidationError) as excinfo:
        encode_payload(42)
    assert "int" in str(excinfo.value)


# ----------------------------------------------------------------------
# decode_payload
# ----------------------------------------------------------------------


def test_decode_payload_roundtrips():
    payload = {"a": 1, "b": ["x", None, True, 2.5]}
    assert decode_payload(encode_payload(payload)) == payload


def test_decode_payload_preserves_unicode():
    payload = {"es": "función", "ca": "funció", "zh": "函数"}
    assert decode_payload(encode_payload(payload)) == payload


def test_decode_payload_rejects_invalid_json():
    with pytest.raises(PayloadDecodeError):
        decode_payload("{not json")


def test_decode_payload_rejects_non_object_json():
    with pytest.raises(PayloadDecodeError):
        decode_payload("[1, 2, 3]")


def test_decode_payload_rejects_null():
    with pytest.raises(PayloadDecodeError):
        decode_payload(None)


def test_decode_payload_rejects_non_text():
    with pytest.raises(PayloadDecodeError):
        decode_payload(123)


def test_decode_payload_accepts_utf8_bytes():
    assert decode_payload('{"a":"ñ"}'.encode("utf-8")) == {"a": "ñ"}


def test_decode_payload_rejects_invalid_utf8_bytes():
    with pytest.raises(PayloadDecodeError):
        decode_payload(b"\xff\xfe\x00")


def test_decode_payload_error_is_a_storage_error():
    with pytest.raises(PayloadDecodeError) as excinfo:
        decode_payload("nope")
    assert excinfo.value.code == "STORAGE_ERROR"


def test_decode_payload_error_mentions_context():
    with pytest.raises(PayloadDecodeError) as excinfo:
        decode_payload("nope", context="courses.payload")
    assert "courses.payload" in str(excinfo.value)


def test_decode_payload_never_returns_empty_dict_silently():
    """损坏数据必须是错误, 不能伪装成"空数据"。"""
    with pytest.raises(PayloadDecodeError):
        decode_payload("")


# ----------------------------------------------------------------------
# payload_checksum
# ----------------------------------------------------------------------


def test_payload_checksum_matches_sha256():
    text = '{"a":1}'
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert payload_checksum(text) == expected


def test_payload_checksum_is_stable_for_equal_payloads():
    assert payload_checksum(encode_payload({"a": 1, "b": 2})) == payload_checksum(
        encode_payload({"b": 2, "a": 1})
    )


def test_payload_checksum_differs_for_different_payloads():
    assert payload_checksum(encode_payload({"a": 1})) != payload_checksum(
        encode_payload({"a": 2})
    )


# ----------------------------------------------------------------------
# 标量转换
# ----------------------------------------------------------------------


def test_to_db_bool_true_and_false():
    assert to_db_bool(True) == 1
    assert to_db_bool(False) == 0


def test_to_db_bool_keeps_none_as_none():
    """三态: None (未知) 不能塌成 False。"""
    assert to_db_bool(None) is None


def test_from_db_bool_true_and_false():
    assert from_db_bool(1) is True
    assert from_db_bool(0) is False


def test_from_db_bool_keeps_none_as_none():
    assert from_db_bool(None) is None


def test_bool_roundtrip_preserves_three_states():
    for value in (True, False, None):
        assert from_db_bool(to_db_bool(value)) is value


def test_opt_str_converts_and_keeps_none():
    assert opt_str(42) == "42"
    assert opt_str(None) is None


def test_opt_str_keeps_empty_string():
    assert opt_str("") == ""


def test_opt_int_parses_and_falls_back():
    assert opt_int("7") == 7
    assert opt_int("nope") is None
    assert opt_int(None) is None


def test_opt_float_parses_and_falls_back():
    assert opt_float("1.5") == 1.5
    assert opt_float("nope") is None
    assert opt_float(None) is None


def test_as_list_normalises():
    assert as_list(None) == []
    assert as_list([1, 2]) == [1, 2]
    assert as_list((1, 2)) == [1, 2]
    assert as_list("abc") == []


def test_as_mapping_normalises():
    assert as_mapping(None) == {}
    assert as_mapping({"a": 1}) == {"a": 1}
    assert as_mapping([1]) == {}


# ----------------------------------------------------------------------
# 存储层不改写原文
# ----------------------------------------------------------------------


def test_stored_payload_is_byte_identical_to_the_encoded_object(db_factory):
    """写进去的 payload 文本与领域对象编码结果**逐字节**一致。"""
    repos = db_factory()
    from src.models import Course

    course = Course(
        course_id="course-1",
        name="Fonaments de Programació",
        code="FP-101",
        metadata={"términos": ["funció", "变量"]},
    )
    repos.courses.save(course)
    stored = repos.courses.payload_text("course-1")
    assert stored == encode_payload(course)
    assert payload_checksum(stored) == payload_checksum(encode_payload(course))


def test_payload_version_is_recorded_per_row(db_factory):
    repos = db_factory()
    from src.models import Course

    repos.courses.save(Course(course_id="course-1", name="A"))
    row = repos.courses.get_row("course-1")
    assert int(row["payload_version"]) == 1


def test_row_payload_is_valid_json(db_factory):
    repos = db_factory()
    from src.models import Course

    repos.courses.save(Course(course_id="course-1", name="A"))
    text = repos.courses.payload_text("course-1")
    assert isinstance(json.loads(text), dict)
