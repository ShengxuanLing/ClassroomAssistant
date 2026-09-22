# -*- coding: utf-8 -*-
"""Task 71.7 — Pilot 操作日志。

轻量级应用日志, 记录真实 Pilot 使用中的关键操作:
    operation / timestamp / course / session / material / success / duration

硬性原则验证:
- 写入失败绝不抛出 (日志不能让业务操作失败)。
- 隐私: 密码 / API key / secret / 完整内容绝不落盘。
- 追加写 + 可查询 (recent / query / count)。
- 长跑不写满磁盘 (软上限旋转)。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.application.operation_log import DEFAULT_MAX_ENTRIES, OperationLog
from src.application.runtime import fixed_clock

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _make_log(tmp_path, **kw):
    return OperationLog(str(tmp_path / "logs"), clock=fixed_clock("2026-09-18T00:00:00+00:00"), **kw)


def test_record_and_recent(tmp_path):
    log = _make_log(tmp_path)
    rec = log.record("create_course", course="c1", success=True, duration_ms=1.5)
    assert rec is not None
    recent = log.recent(10)
    assert len(recent) == 1
    assert recent[0].operation == "create_course"
    assert recent[0].course == "c1"
    assert recent[0].success is True
    assert recent[0].duration_ms == 1.5
    # 文件确实落盘。
    assert os.path.exists(log.path)


def test_query_filters(tmp_path):
    log = _make_log(tmp_path)
    log.record("create_course", course="c1", success=True)
    log.record("submit_answer", course="c2", success=False, failure="boom")
    log.record("create_course", course="c1", success=True)
    assert log.count() == 3
    assert log.count(success=True) == 2
    assert log.count(success=False) == 1
    assert len(log.query(operation="create_course")) == 2
    assert len(log.query(course="c1")) == 2
    assert len(log.query(success=False)) == 1


def test_record_never_raises_on_bad_path(tmp_path):
    # 把一个已存在的文件当成日志目录 -> 写入必然失败, 但必须静默返回 None。
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    log = OperationLog(str(blocker), clock=fixed_clock("2026-09-18T00:00:00+00:00"))
    result = log.record("create_course", success=True)
    assert result is None  # 绝不抛出


def test_secret_and_bulk_content_are_not_logged(tmp_path):
    log = _make_log(tmp_path)
    log.record(
        "register_material",
        course="c1",
        success=True,
        api_key="super-secret-key-123",
        password="hunter2",
        transcript="a very long lecture transcript " * 50,
    )
    with open(log.path, "r", encoding="utf-8") as handle:
        raw = handle.read()
    assert "super-secret-key-123" not in raw
    assert "hunter2" not in raw
    # 长文本被截断 (出现脱敏后的摘要, 但绝不是原文逐字全量)。
    assert "a very long lecture transcript " * 50 not in raw


def test_rotation_keeps_recent(tmp_path):
    log = _make_log(tmp_path, max_entries=5)
    for i in range(20):
        log.record(f"op-{i}", course="c1", success=True)
    # 旋转后只保留最近 max_entries 条, 且是最近的那几条。
    assert log.count() <= 5
    recent = log.recent(10)
    assert [r.operation for r in recent] == [f"op-{i}" for i in range(15, 20)]


def test_respects_default_max_entries(tmp_path):
    log = _make_log(tmp_path)
    assert log._max_entries == DEFAULT_MAX_ENTRIES


def test_workspace_writes_operation_log_when_not_under_pytest(tmp_path, monkeypatch):
    """真实 Pilot 下, Workspace 的关键操作会落盘到 <data_dir>/logs/operations.log。"""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    from src.application.workspace import Workspace

    ws = Workspace(
        str(tmp_path / "data"),
        clock=fixed_clock("2026-09-18T00:00:00+00:00"),
        asr_mode="mock",
        ocr_mode="mock",
    )
    try:
        course = ws.create_course("Logged Course", language="es")
        cid = course["course_id"]
        log = ws.operation_log
        recs = log.query(operation="create_course")
        assert len(recs) == 1
        assert recs[0].course == cid
        assert recs[0].success is True
    finally:
        ws.close()


# ===========================================================================
# D3 — process_material 的 success 口径
# ===========================================================================


def pilot_workspace(tmp_path, monkeypatch):
    """真实 Pilot 下的 Workspace。

    ``_record_op`` 在 pytest 下默认**禁用** (避免往受测的数据目录写文件),
    所以这里必须先删掉 ``PYTEST_CURRENT_TEST`` —— 否则下面断言的是"日志里
    什么都没有", 永远绿, 什么也没证明。
    """
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    from src.application.workspace import Workspace

    return Workspace(
        str(tmp_path / "data"),
        clock=fixed_clock("2026-09-18T00:00:00+00:00"),
        asr_mode="mock",
        ocr_mode="mock",
    )


def register_fixture_material(ws, course_id, tmp_path, fixture, *, name=None):
    """把 ``tests/fixtures`` 下的样例登记进课程。

    ``fixture`` 是相对 ``tests/fixtures`` 的路径 (样例按类型分目录存放)。
    """
    upload = tmp_path / (name or Path(fixture).name)
    upload.write_bytes((FIXTURES / fixture).read_bytes())
    return ws.register_material(course_id, str(upload))


def test_process_material_logs_success_for_a_zero_evidence_material(tmp_path, monkeypatch):
    """零证据的成功也是成功 —— 日志口径跟作业状态走。

    回归本体: ``Workspace.process_material`` 曾按作业字典里的
    ``processing_status`` 判定成败, 而作业字典的键是 ``status``
    (ProcessingJob.to_dict) —— ``processing_status`` 只存在于材料注册表。
    键读错**不抛异常**, 只是让 ``ok`` 恒为 False: 每一次成功的处理都被记成
    ``success:false``, 且因为 ``failure_reason`` 同样取不到 (status 也不是
    "FAILED"), 日志里连失败原因都没有 —— 完全看不出发生了什么。

    这里故意选"成功但零证据"的材料: 它在契约里是合法成功
    (``COMPLETED`` + 0 条证据), 正是历史实现记错的那一类。
    """
    ws = pilot_workspace(tmp_path, monkeypatch)
    try:
        course = ws.create_course("Logged Course", language="es")
        cid = course["course_id"]
        record = register_fixture_material(ws, cid, tmp_path, "documents/empty.pdf")

        job = ws.process_material(cid, record["material_id"])
        # 前置事实: 引擎内部的作业字段长这样 —— 下面断言的键名必须与它一致。
        assert job["status"] == "SUCCEEDED"
        assert job["evidence_ids"] == []
        assert "processing_status" not in job

        records = ws.operation_log.query(operation="process_material")
        assert len(records) == 1
        assert records[0].material == record["material_id"]
        assert records[0].success is True
        assert records[0].failure is None
    finally:
        ws.close()


def test_process_material_logs_the_failure_reason(tmp_path, monkeypatch):
    """失败路径必须记 ``success:false`` **且**带上失败原因。

    只断 ``success is False`` 是不够的: 历史缺陷正是"记成失败但没有原因"
    (键读错时两个字段同时错)。所以这里把原因一起钉住。
    """
    ws = pilot_workspace(tmp_path, monkeypatch)
    try:
        course = ws.create_course("Logged Course", language="es")
        cid = course["course_id"]
        record = register_fixture_material(ws, cid, tmp_path, "documents/corrupted.pdf")

        job = ws.process_material(cid, record["material_id"])
        assert job["status"] == "FAILED"
        assert job["error"] == "DOCUMENT_PARSE_FAILED"

        records = ws.operation_log.query(operation="process_material")
        assert len(records) == 1
        assert records[0].success is False
        assert records[0].failure == "DOCUMENT_PARSE_FAILED"
    finally:
        ws.close()
