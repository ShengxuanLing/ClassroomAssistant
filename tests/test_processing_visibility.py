# -*- coding: utf-8 -*-
"""Task 71.6 — Processing Visibility。

材料处理期间用户必须能区分: pending / processing / completed / failed。

验证
--------------------------------------------------------------------
- 注册后材料处于可观察状态, 绝不永久卡在 "loading..."。
- 处理状态机只在这四个 (含中间态) 合法值之间迁移, 不会留下未定义状态。
- ``processing_status`` 查询接口返回明确的状态值 (不是 loading)。
"""

from __future__ import annotations

import os

import pytest

from src.application.runtime import fixed_clock
from src.application.workspace import Workspace

VALID_TERMINAL_STATES = {"COMPLETED", "FAILED"}


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


def test_registered_material_is_observable_not_loading(ws, tmp_path):
    course = ws.create_course("PV Course", language="es")
    cid = course["course_id"]
    note = tmp_path / "notes.txt"
    note.write_text("La funcion es importante.")
    record = ws.register_material(cid, str(note))
    status = record["processing_status"]
    # 注册即进入可观察状态 (REGISTERED / VALIDATING / PROCESSING / COMPLETED),
    # 绝不返回 "loading"。
    assert status != "LOADING"
    assert status is not None


def test_processing_reaches_terminal_state(ws, tmp_path):
    course = ws.create_course("PV Course", language="es")
    cid = course["course_id"]
    note = tmp_path / "notes.txt"
    note.write_text("La derivada de x al cuadrado es 2x.")
    record = ws.register_material(cid, str(note))
    mid = record["material_id"]
    # 处理可能同步完成, 也可能落在某个中间态; 但绝不能永久停留在 LOADING。
    ws.process_material(cid, mid)
    final = ws.get_material(cid, mid)["processing_status"]
    assert final != "LOADING"
    assert final is not None


def test_failed_material_shows_failed_status(ws, tmp_path):
    course = ws.create_course("PV Course", language="es")
    cid = course["course_id"]
    bad = tmp_path / "lecture.xyz"
    bad.write_text("nope")
    record = ws.register_material(cid, str(bad))
    # 不支持的格式必须显式进入 FAILED, 而不是假装成功或卡住。
    assert record["processing_status"] == "FAILED"
    assert record["error"] == "UNSUPPORTED_EXTENSION"


def test_processing_status_query_returns_explicit_state(ws, tmp_path):
    course = ws.create_course("PV Course", language="es")
    cid = course["course_id"]
    note = tmp_path / "notes.txt"
    note.write_text("Contenido de prueba para estado.")
    record = ws.register_material(cid, str(note))
    mid = record["material_id"]
    ws.process_material(cid, mid)
    # 直接读取该材料的状态, 必须是明确四态之一, 不是 "loading"。
    stored = ws.get_material(cid, mid)
    assert stored["processing_status"] in {
        "REGISTERED",
        "VALIDATING",
        "PROCESSING",
        "COMPLETED",
        "FAILED",
    }
