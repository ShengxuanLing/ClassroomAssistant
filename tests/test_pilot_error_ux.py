# -*- coding: utf-8 -*-
"""Task 71.5 — Error UX。

用户看到的错误必须是: 发生了什么 / 为什么 / 下一步可以做什么。

绝不向用户暴露: KeyError / TypeError / Traceback / SQLiteError 这类内部栈。

验证
--------------------------------------------------------------------
- 常见错误场景产出**结构化、用户可读**的 ApplicationError (带稳定错误码),
  而不是把底层异常原样抛出。
- 返回的失败记录携带清晰的 error 码 (FILE_NOT_FOUND / UNSUPPORTED_EXTENSION /
  ZERO_BYTE_FILE / DUPLICATE ...), 不假装成功。
- 通过 API 触发的错误是标准 4xx 信封 (success:false + message), 不含 traceback。
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

import pytest

from src.application.errors import (
    ApplicationError,
    InvalidInputError,
    NotFoundError,
)
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace


@pytest.fixture
def ws(tmp_path):
    workspace = Workspace(
        str(tmp_path / "data"),
        clock=fixed_clock("2026-01-01T00:00:00+00:00"),
        asr_mode="mock",
        ocr_mode="mock",
    )
    yield workspace
    workspace.close()


def _no_traceback(text: str) -> None:
    assert "Traceback" not in text
    assert "KeyError" not in text
    assert "TypeError" not in text
    assert "SQLiteError" not in text
    assert 'File "' not in text


def test_empty_course_name_is_friendly_invalid_input(ws):
    with pytest.raises(InvalidInputError) as exc:
        ws.create_course("")
    _no_traceback(str(exc.value))
    assert exc.value.code == "INVALID_INPUT"


def test_unknown_course_is_not_found(ws):
    with pytest.raises(NotFoundError) as exc:
        ws.get_course("does-not-exist")
    _no_traceback(str(exc.value))
    assert exc.value.code == "NOT_FOUND"


def test_register_missing_file_returns_friendly_failure(ws):
    course = ws.create_course("UX Course", language="es")
    cid = course["course_id"]
    record = ws.register_material(cid, os.path.join(ws.data_dir, "ghost.txt"))
    assert record["processing_status"] == "FAILED"
    assert record["error"] == "FILE_NOT_FOUND"
    _no_traceback(str(record.get("error", "")))


def test_register_unsupported_format_is_explicit(ws, tmp_path):
    course = ws.create_course("UX Course", language="es")
    cid = course["course_id"]
    bad = tmp_path / "lecture.xyz"
    bad.write_text("nope")
    record = ws.register_material(cid, str(bad))
    assert record["processing_status"] == "FAILED"
    assert record["error"] == "UNSUPPORTED_EXTENSION"


def test_register_empty_file_is_explicit(ws, tmp_path):
    course = ws.create_course("UX Course", language="es")
    cid = course["course_id"]
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    record = ws.register_material(cid, str(empty))
    assert record["processing_status"] == "FAILED"
    assert record["error"] == "ZERO_BYTE_FILE"


def test_duplicate_material_is_marked_not_crash(ws, tmp_path):
    course = ws.create_course("UX Course", language="es")
    cid = course["course_id"]
    note = tmp_path / "notes.txt"
    note.write_text("contenido de prueba")
    first = ws.register_material(cid, str(note))
    assert first["processing_status"] != "FAILED"
    second = ws.register_material(cid, str(note))
    assert second.get("duplicate") is True
    # 重复是"信息性"结果, 不是崩溃。
    assert second["processing_status"] != "FAILED"


def test_process_unknown_material_is_not_found(ws):
    course = ws.create_course("UX Course", language="es")
    cid = course["course_id"]
    with pytest.raises(NotFoundError):
        ws.process_material(cid, "missing-material")


def test_register_to_unknown_course_is_not_found(ws, tmp_path):
    note = tmp_path / "notes.txt"
    note.write_text("contenido")
    with pytest.raises(NotFoundError):
        ws.register_material("no-such-course", str(note))


def test_api_unknown_course_returns_standard_envelope(ws, tmp_path):
    """经由 API: 错误是 success:false 信封, 绝不是 500 / traceback。"""
    from src.api.server import ApiServer, create_server

    instance = create_server(ws, port=0).start()
    try:
        url = instance.url
        try:
            urllib.request.urlopen(
                urllib.request.Request(f"{url}/api/courses/no-such-course"), timeout=30
            )
            pytest.fail("expected HTTP error")
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8")
            assert exc.code == 404
            assert "Traceback" not in payload
            assert "success" in payload
            assert "false" in payload
    finally:
        instance.stop()
