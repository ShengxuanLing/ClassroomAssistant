# -*- coding: utf-8 -*-
"""Task 34: 应用层核心服务测试 (Course / Session / Material / 错误映射)。

覆盖:
- CourseService CRUD / 幂等 / 冲突 / 语言解析
- ClassSession 幂等 / 校验
- Material 注册幂等 / 过滤
- 错误映射 (map_application_error)
"""

from __future__ import annotations

import pytest

from src.application import (
    AppService,
    CourseService,
    InvalidInputError,
    NotFoundError,
    ConflictError,
    map_application_error,
)
from src.application.errors import ApplicationError, ERROR_CODES
from src.models import Course, Material


# ----------------------------------------------------------------------
# CourseService
# ----------------------------------------------------------------------


def test_create_course_returns_deterministic_id():
    cs = CourseService()
    c1 = cs.create_course("Algebra", code="ALG")
    c2 = cs.create_course("Algebra", code="ALG")
    assert c1["course_id"] == c2["course_id"]
    assert c1["course_id"].startswith("course-")
    assert c1["name"] == "Algebra"


def test_create_course_idempotent_same_input():
    cs = CourseService()
    c1 = cs.create_course("Algebra", code="ALG", language="es", metadata={"x": 1})
    c2 = cs.create_course("Algebra", code="ALG", language="es", metadata={"x": 1})
    assert c1 == c2
    assert len(cs.list_courses()) == 1


def test_create_course_conflict_on_language_mismatch():
    cs = CourseService()
    cs.create_course("Algebra", code="ALG", language="es")
    with pytest.raises(ConflictError) as exc:
        cs.create_course("Algebra", code="ALG", language="ca")
    assert exc.value.code == ERROR_CODES.CONFLICT


def test_create_course_conflict_on_metadata_mismatch():
    cs = CourseService()
    cs.create_course("Algebra", code="ALG", metadata={"a": 1})
    with pytest.raises(ConflictError):
        cs.create_course("Algebra", code="ALG", metadata={"a": 2})


def test_create_course_requires_name():
    cs = CourseService()
    with pytest.raises(InvalidInputError) as exc:
        cs.create_course("")
    assert exc.value.code == ERROR_CODES.INVALID_INPUT


def test_create_course_rejects_non_string_name():
    cs = CourseService()
    with pytest.raises(InvalidInputError):
        cs.create_course(123)


def test_get_course_not_found():
    cs = CourseService()
    with pytest.raises(NotFoundError) as exc:
        cs.get_course("course-doesnotexist")
    assert exc.value.code == ERROR_CODES.NOT_FOUND


def test_list_courses_sorted_deterministically():
    cs = CourseService()
    cs.create_course("BBB", code="b")
    cs.create_course("AAA", code="a")
    cs.create_course("CCC", code="c")
    ids = [c["course_id"] for c in cs.list_courses()]
    assert ids == sorted(ids)


def test_update_course_merges_metadata_by_default():
    cs = CourseService()
    c = cs.create_course("T", code="T", metadata={"a": 1})
    updated = cs.update_course(c["course_id"], metadata={"b": 2})
    assert updated["metadata"] == {"a": 1, "b": 2}


def test_update_course_replace_metadata():
    cs = CourseService()
    c = cs.create_course("T", code="T", metadata={"a": 1})
    updated = cs.update_course(c["course_id"], metadata={"z": 9}, replace_metadata=True)
    assert updated["metadata"] == {"z": 9}


def test_update_course_not_found():
    cs = CourseService()
    with pytest.raises(NotFoundError):
        cs.update_course("course-x", metadata={"a": 1})


def test_update_course_language_alias_ca():
    cs = CourseService()
    c = cs.create_course("T", code="T")
    updated = cs.update_course(c["course_id"], language="ca")
    assert updated["language"] == "Catalan"


# ----------------------------------------------------------------------
# ClassSession
# ----------------------------------------------------------------------


def test_create_session_idempotent():
    cs = CourseService()
    c = cs.create_course("S", code="S")
    s1 = cs.create_session(c["course_id"], session_number=1, date="2026-09-01")
    s2 = cs.create_session(c["course_id"], session_number=1, date="2026-09-01")
    assert s1["session_id"] == s2["session_id"]
    assert len(cs.list_sessions(c["course_id"])) == 1


def test_create_session_requires_known_course():
    cs = CourseService()
    with pytest.raises(NotFoundError):
        cs.create_session("course-unknown", session_number=1)


def test_get_session_not_found():
    cs = CourseService()
    c = cs.create_course("S", code="S")
    cs.create_session(c["course_id"], session_number=1)
    with pytest.raises(NotFoundError):
        cs.get_session("session-doesnotexist")


def test_list_sessions_filters_by_course():
    cs = CourseService()
    c1 = cs.create_course("C1", code="1")
    c2 = cs.create_course("C2", code="2")
    cs.create_session(c1["course_id"], session_number=1)
    cs.create_session(c2["course_id"], session_number=1)
    assert len(cs.list_sessions(c1["course_id"])) == 1
    assert len(cs.list_sessions()) == 2


# ----------------------------------------------------------------------
# Material
# ----------------------------------------------------------------------


def test_register_material_idempotent_same_id():
    cs = CourseService()
    c = cs.create_course("M", code="M")
    payload = {
        "material_id": "mat-1",
        "filename": "a.txt",
        "material_type": "text",
        "language": "es",
        "path": "input/a.txt",
        "metadata": {"course_id": c["course_id"]},
    }
    m1 = cs.register_material(payload)
    m2 = cs.register_material(payload)
    assert m1["material_id"] == m2["material_id"] == "mat-1"
    assert len(cs.list_materials()) == 1


def test_register_material_requires_material_id():
    cs = CourseService()
    with pytest.raises(InvalidInputError):
        cs.register_material({"filename": "a.txt"})


def test_register_material_requires_filename():
    cs = CourseService()
    with pytest.raises(InvalidInputError):
        cs.register_material({"material_id": "mat-1"})


def test_register_material_rejects_non_mapping():
    cs = CourseService()
    with pytest.raises(InvalidInputError):
        cs.register_material("not-a-mapping")


def test_get_material_not_found():
    cs = CourseService()
    with pytest.raises(NotFoundError):
        cs.get_material("mat-nope")


def test_list_materials_filters_by_course():
    cs = CourseService()
    c1 = cs.create_course("C1", code="1")
    c2 = cs.create_course("C2", code="2")
    cs.register_material(
        {"material_id": "m1", "filename": "a.txt", "metadata": {"course_id": c1["course_id"]}}
    )
    cs.register_material(
        {"material_id": "m2", "filename": "b.txt", "metadata": {"course_id": c2["course_id"]}}
    )
    assert len(cs.list_materials(course_id=c1["course_id"])) == 1
    assert len(cs.list_materials(course_id=c2["course_id"])) == 1


# ----------------------------------------------------------------------
# 错误映射
# ----------------------------------------------------------------------


def test_map_application_error_passthrough():
    err = NotFoundError("x")
    mapped = map_application_error(err)
    assert mapped is err
    assert mapped.code == "NOT_FOUND"


def test_map_application_error_keyerror_to_not_found():
    with pytest.raises(KeyError):
        _ = {"a": 1}["missing"]
    err = None
    try:
        _ = {"a": 1}["missing"]
    except KeyError as exc:
        err = exc
    mapped = map_application_error(err)
    assert isinstance(mapped, ApplicationError)
    assert mapped.code in {"NOT_FOUND", "INVALID_INPUT"}


def test_map_application_error_valueerror_to_invalid():
    err = ValueError("bad")
    mapped = map_application_error(err)
    assert isinstance(mapped, ApplicationError)
    assert mapped.code == "INVALID_INPUT"


def test_map_application_error_keyerror_message_does_not_leak_traceback():
    err = KeyError("secret")
    mapped = map_application_error(err)
    assert "Traceback" not in mapped.message
    assert "secret" in mapped.message


def test_all_error_codes_defined():
    assert {c.value for c in ERROR_CODES} == {
        "INVALID_INPUT",
        "NOT_FOUND",
        "CONFLICT",
        "PROCESSING_ERROR",
        "UNSUPPORTED",
        "STORAGE_ERROR",
        "CONFIGURATION_ERROR",
        "INTERNAL_ERROR",
    }


def test_application_error_to_dict_includes_code_and_message():
    err = ConflictError("dup")
    d = err.to_dict()
    assert d["code"] == "CONFLICT"
    assert d["message"] == "dup"
