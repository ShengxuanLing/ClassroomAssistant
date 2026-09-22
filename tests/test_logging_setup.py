# -*- coding: utf-8 -*-
"""Task 44 —— 统一日志与隐私过滤。

规范要求:
- 级别: DEBUG / INFO / WARNING / ERROR / CRITICAL。
- 日志至少包括: timestamp / level / component / event / message。
- 日志禁止写入: 完整用户答案 / API key / secret / 不必要的课堂原文 /
  敏感文件内容。
- 生产环境: 用户看到友好错误, 日志保留详细错误; 开发环境可以暴露 debug。

本文件还包含一条**回归测试**: ``ApiServer`` 注入 logger 后必须能正常响应。
在 Task 44 之前, ``log_message`` 传的是 ``extra={"message": ...}``, 而
``message`` 是 logging 的保留属性名 —— 于是每个 HTTP 响应都会在
``send_response()`` 里抛 ``KeyError``, 客户端只看到 ``RemoteDisconnected``。
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from src.application.config import AppConfig, load_config
from src.application.errors import ConfigurationError
from src.application.logging_setup import (
    BULK_CONTENT_EXTRA_KEYS,
    DEFAULT_EXCERPT_LIMIT,
    LOG_LEVELS,
    REQUIRED_LOG_FIELDS,
    RESERVED_EXTRA_KEYS,
    PrivacyFilter,
    StructuredFormatter,
    configure_logging,
    get_logger,
    log_event,
    reset_logging,
)

FIXED_TS = "2026-01-01T00:00:00+00:00"
FAKE_SECRET = "sk-live-DEADBEEF0123456789"
LOGGER_NAME = "classroom.tests.logging"


@pytest.fixture
def stream() -> io.StringIO:
    return io.StringIO()


@pytest.fixture
def logger(stream):
    """一个装了结构化 handler 的独立 logger (不碰 root, 不污染别的测试)。"""
    reset_logging(LOGGER_NAME)
    configure_logging(
        level="DEBUG", stream=stream, clock=lambda: FIXED_TS, name=LOGGER_NAME
    )
    target = logging.getLogger(LOGGER_NAME)
    target.propagate = False
    try:
        yield target
    finally:
        reset_logging(LOGGER_NAME)


def _record(
    *,
    level: int = logging.INFO,
    msg: str = "event_name",
    args: tuple = (),
    name: str = "component.name",
    exc_info=None,
    **extra,
) -> logging.LogRecord:
    """构造一条 LogRecord。

    注意: ``extra`` 不是 ``LogRecord.__init__`` 的参数 —— 那是
    ``Logger.makeRecord`` 的活。所以这里手工把字段挂到 record 上, 模拟
    真实调用路径 (这正是 ``extra={"message": ...}`` 会炸的原因所在)。
    """
    record = logging.LogRecord(name, level, "path.py", 1, msg, args, exc_info)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# ======================================================================
# 规范点名的字段与级别
# ======================================================================


def test_the_five_required_fields_are_exactly_the_spec_ones():
    assert REQUIRED_LOG_FIELDS == ("timestamp", "level", "component", "event", "message")


def test_the_five_log_levels_are_exactly_the_spec_ones():
    assert LOG_LEVELS == ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def test_json_output_contains_every_required_field_by_name():
    """JSON 形态保证 5 个字段名逐字存在 (人类可读形态按固定位置排列)。"""
    payload = json.loads(StructuredFormatter(json_output=True, clock=lambda: FIXED_TS).format(_record()))
    for field in REQUIRED_LOG_FIELDS:
        assert field in payload, field
    assert set(REQUIRED_LOG_FIELDS) <= set(payload)


@pytest.mark.parametrize(
    "level,label",
    [
        (logging.DEBUG, "DEBUG"),
        (logging.INFO, "INFO"),
        (logging.WARNING, "WARNING"),
        (logging.ERROR, "ERROR"),
        (logging.CRITICAL, "CRITICAL"),
    ],
)
def test_every_level_is_rendered(level, label):
    text = StructuredFormatter(clock=lambda: FIXED_TS).format(_record(level=level))
    assert f" {label}" in text


# ======================================================================
# 人类可读格式
# ======================================================================


def test_human_format_orders_the_fields_as_the_spec_lists_them():
    text = StructuredFormatter(clock=lambda: FIXED_TS).format(
        _record(name="classroom.api", msg="http_request", message_text="GET /api/health 200")
    )
    assert text.startswith(FIXED_TS)
    assert " INFO " in text
    assert "classroom.api" in text
    assert "event=http_request" in text
    assert text.index(FIXED_TS) < text.index("INFO") < text.index("classroom.api")
    assert text.index("classroom.api") < text.index("event=http_request")
    assert text.index("event=http_request") < text.index("GET /api/health 200")


def test_human_format_is_single_line():
    """一条日志一行 —— 否则日志系统按行收集时会错位。"""
    text = StructuredFormatter(clock=lambda: FIXED_TS).format(
        _record(msg="ev", message_text="line1\nline2\r\nline3")
    )
    assert "\n" not in text
    assert "line1\\nline2\\nline3" in text


def test_timestamp_comes_from_the_injected_clock():
    text = StructuredFormatter(clock=lambda: "2001-02-03T04:05:06+00:00").format(_record())
    assert text.startswith("2001-02-03T04:05:06+00:00")


def test_default_clock_is_an_iso_timestamp():
    import datetime as _dt

    text = StructuredFormatter().format(_record())
    stamp = text.split(" ")[0]
    _dt.datetime.fromisoformat(stamp)  # 不抛异常即合法


def test_component_defaults_to_the_logger_name():
    text = StructuredFormatter(clock=lambda: FIXED_TS).format(_record(name="classroom.backup"))
    assert "classroom.backup" in text


def test_component_can_be_overridden():
    text = StructuredFormatter(clock=lambda: FIXED_TS).format(
        _record(component="explicit.component")
    )
    assert "explicit.component" in text


def test_event_defaults_to_the_message_argument():
    text = StructuredFormatter(clock=lambda: FIXED_TS).format(_record(msg="ingest_started"))
    assert "event=ingest_started" in text


def test_message_defaults_to_the_formatted_message():
    text = StructuredFormatter(clock=lambda: FIXED_TS).format(
        _record(msg="uploaded %s", args=("a.pdf",))
    )
    assert "uploaded a.pdf" in text


def test_message_can_be_supplied_separately_from_the_event():
    """这正是 server.py 需要的形态: 事件名 + 具体消息分开。"""
    text = StructuredFormatter(clock=lambda: FIXED_TS).format(
        _record(msg="http_request", message_text='GET /api/health 200')
    )
    assert "event=http_request" in text
    assert "GET /api/health 200" in text


def test_broken_format_arguments_do_not_swallow_the_log():
    text = StructuredFormatter(clock=lambda: FIXED_TS).format(
        _record(msg="value %s %s", args=("only-one",))
    )
    assert "value %s %s" in text


def test_detail_is_rendered_as_json():
    text = StructuredFormatter(clock=lambda: FIXED_TS).format(
        _record(detail={"material_id": "m1", "count": 2})
    )
    assert 'detail={"count": 2, "material_id": "m1"}' in text


def test_extra_fields_are_rendered_not_silently_dropped():
    """传了字段却看不到, 会让人以为日志系统坏了。"""
    text = StructuredFormatter(clock=lambda: FIXED_TS).format(_record(material_id="m1"))
    assert "material_id" in text
    assert "m1" in text


def test_non_serialisable_extra_does_not_break_the_formatter():
    text = StructuredFormatter(clock=lambda: FIXED_TS).format(_record(thing=object()))
    assert "thing" in text


def test_exception_traceback_is_included_in_the_log():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        text = StructuredFormatter(clock=lambda: FIXED_TS).format(
            _record(exc_info=sys.exc_info())
        )
    assert "Traceback" in text
    assert "ValueError: boom" in text


def test_json_output_carries_the_exception_field():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        payload = json.loads(
            StructuredFormatter(json_output=True, clock=lambda: FIXED_TS).format(
                _record(exc_info=sys.exc_info())
            )
        )
    assert "ValueError: boom" in payload["exception"]


# ======================================================================
# 隐私过滤
# ======================================================================


@pytest.mark.parametrize("key", ["api_key", "apikey", "token", "password", "client_secret"])
def test_secret_extra_keys_are_fully_replaced(key):
    record = _record(**{key: FAKE_SECRET})
    PrivacyFilter().filter(record)
    assert getattr(record, key) == "<redacted>"


@pytest.mark.parametrize("key", sorted(BULK_CONTENT_EXTRA_KEYS))
def test_bulk_content_extra_keys_are_excerpted(key):
    record = _record(**{key: "x" * 5000})
    PrivacyFilter().filter(record)
    value = getattr(record, key)
    assert len(value) < 500
    assert "truncated" in value


def test_the_full_answer_never_reaches_the_log(logger, stream):
    answer = "学生答案:" + "内容" * 2000
    log_event(logger, logging.INFO, "answer_recorded", "recorded", answer=answer)
    text = stream.getvalue()
    assert answer not in text
    assert "truncated" in text


def test_a_secret_in_extra_never_reaches_the_log(logger, stream):
    log_event(logger, logging.INFO, "call", "calling", api_key=FAKE_SECRET)
    text = stream.getvalue()
    assert FAKE_SECRET not in text
    assert "<redacted>" in text


def test_a_secret_inside_detail_never_reaches_the_log(logger, stream):
    log_event(
        logger, logging.INFO, "call", "calling", detail={"api_key": FAKE_SECRET}
    )
    assert FAKE_SECRET not in stream.getvalue()


def test_a_secret_shaped_message_is_redacted(logger, stream):
    log_event(logger, logging.INFO, "call", f"using api_key={FAKE_SECRET}")
    assert FAKE_SECRET not in stream.getvalue()


def test_an_over_long_message_is_truncated(logger, stream):
    log_event(logger, logging.INFO, "big", "y" * 5000)
    text = stream.getvalue()
    assert "y" * 5000 not in text
    assert "truncated" in text


def test_privacy_filter_always_returns_true():
    """过滤是**改写**而不是丢弃 —— 丢掉整条记录会让问题无从查起。"""
    assert PrivacyFilter().filter(_record()) is True


def test_privacy_filter_leaves_ordinary_extras_alone():
    record = _record(material_id="m1", count=3)
    PrivacyFilter().filter(record)
    assert record.material_id == "m1"
    assert record.count == 3


def test_default_excerpt_limit_is_reasonable():
    assert 50 <= DEFAULT_EXCERPT_LIMIT <= 1000


def test_reserved_extra_keys_cover_the_logging_builtins():
    assert {"message", "asctime", "msg", "args"} <= RESERVED_EXTRA_KEYS


# ======================================================================
# configure_logging
# ======================================================================


def test_configure_logging_returns_a_handler(stream):
    handler = configure_logging(level="INFO", stream=stream, name="classroom.t1")
    try:
        assert isinstance(handler, logging.Handler)
        assert handler.formatter is not None
    finally:
        reset_logging("classroom.t1")


def test_configure_logging_uses_the_config_log_level(stream):
    config = load_config(env={}, cli_overrides={"log_level": "warning"})
    handler = configure_logging(config, stream=stream, name="classroom.t2")
    try:
        assert handler.level == logging.WARNING
    finally:
        reset_logging("classroom.t2")


def test_explicit_level_beats_the_config_level(stream):
    config = load_config(env={}, cli_overrides={"log_level": "warning"})
    handler = configure_logging(config, level="debug", stream=stream, name="classroom.t3")
    try:
        assert handler.level == logging.DEBUG
    finally:
        reset_logging("classroom.t3")


def test_configure_logging_is_idempotent(stream):
    """重复配置不能叠加 handler —— 否则每条日志会打印多份。"""
    name = "classroom.t4"
    first = configure_logging(level="INFO", stream=stream, name=name)
    second = configure_logging(level="INFO", stream=stream, name=name)
    try:
        assert first is second
        assert len(logging.getLogger(name).handlers) == 1
        logging.getLogger(name).warning("only once")
        # 按**行数**判断是否重复 (字符串出现两次是正常的: event 与 message
        # 都会回落到同一条消息)。
        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
        assert len(lines) == 1
    finally:
        reset_logging(name)


def test_configure_logging_force_replaces_the_handler(stream):
    name = "classroom.t5"
    first = configure_logging(level="INFO", stream=stream, name=name)
    second = configure_logging(level="INFO", stream=stream, name=name, force=True)
    try:
        assert first is not second
        assert len(logging.getLogger(name).handlers) == 1
    finally:
        reset_logging(name)


def test_reset_logging_removes_the_installed_handler(stream):
    name = "classroom.t6"
    configure_logging(level="INFO", stream=stream, name=name)
    assert reset_logging(name) == 1
    assert reset_logging(name) == 0


def test_reset_logging_leaves_foreign_handlers_alone(stream):
    name = "classroom.t7"
    foreign = logging.NullHandler()
    target = logging.getLogger(name)
    target.addHandler(foreign)
    configure_logging(level="INFO", stream=stream, name=name)
    try:
        reset_logging(name)
        assert foreign in target.handlers
    finally:
        target.removeHandler(foreign)


def test_level_filtering_actually_suppresses(stream):
    name = "classroom.t8"
    configure_logging(level="WARNING", stream=stream, name=name)
    try:
        target = logging.getLogger(name)
        target.propagate = False
        target.info("invisible")
        target.warning("visible")
        output = stream.getvalue()
        assert "invisible" not in output
        assert "visible" in output
    finally:
        reset_logging(name)


@pytest.mark.parametrize("bad", ["LOUD", "", 3.5, True])
def test_invalid_level_is_rejected(bad, stream):
    with pytest.raises(ConfigurationError):
        configure_logging(level=bad, stream=stream, name="classroom.t9")


def test_numeric_levels_are_accepted(stream):
    handler = configure_logging(level=logging.ERROR, stream=stream, name="classroom.t10")
    try:
        assert handler.level == logging.ERROR
    finally:
        reset_logging("classroom.t10")


def test_json_output_is_parseable_per_line(stream):
    name = "classroom.t11"
    configure_logging(
        level="INFO", stream=stream, clock=lambda: FIXED_TS, json_output=True, name=name
    )
    try:
        target = logging.getLogger(name)
        target.propagate = False
        target.info("ev", extra={"event": "ev", "message_text": "hello"})
        payload = json.loads(stream.getvalue().strip())
        assert payload["timestamp"] == FIXED_TS
        assert payload["level"] == "INFO"
        assert payload["event"] == "ev"
        assert payload["message"] == "hello"
        assert payload["component"] == name
    finally:
        reset_logging(name)


def test_get_logger_returns_a_named_logger():
    assert get_logger("classroom.x") is logging.getLogger("classroom.x")


# ======================================================================
# log_event
# ======================================================================


def test_log_event_renders_event_and_message(logger, stream):
    log_event(logger, logging.INFO, "course_created", "Course created")
    text = stream.getvalue()
    assert "event=course_created" in text
    assert "Course created" in text


def test_log_event_accepts_a_level_name(logger, stream):
    log_event(logger, "WARNING", "degraded", "asr fell back to mock")
    assert "WARNING" in stream.getvalue()


def test_log_event_renders_detail(logger, stream):
    log_event(logger, logging.INFO, "ev", "m", detail={"material_id": "m1"})
    assert "material_id" in stream.getvalue()


@pytest.mark.parametrize("key", sorted(RESERVED_EXTRA_KEYS - {"message"}))
def test_log_event_rejects_reserved_field_names(logger, key):
    """传保留名会得到一条可读的 ConfigurationError, 而不是 logging 内部的
    KeyError —— 后者曾经让每一个 HTTP 响应都崩掉。

    ``message`` 被排除在外: 它是 ``log_event`` 的**正式参数**, 也就是
    推荐用法本身, 根本走不到 ``**extra`` 这条路上。
    """
    with pytest.raises(ConfigurationError):
        log_event(logger, logging.INFO, "ev", "m", **{key: "x"})


def test_log_event_message_keyword_is_the_blessed_path(logger, stream):
    """``message=`` 是 log_event 的正式参数 —— 用户不需要记住"哪个键名会炸"。"""
    log_event(logger, logging.INFO, "ev", message="hello")
    assert "hello" in stream.getvalue()


def test_log_event_without_message_still_logs_the_event(logger, stream):
    log_event(logger, logging.INFO, "started")
    text = stream.getvalue()
    assert "event=started" in text


def test_log_event_supports_multilingual_text(logger, stream):
    log_event(logger, logging.INFO, "ingesta", "材料已摄取: apuntes-álgebra.pdf")
    text = stream.getvalue()
    assert "材料已摄取" in text
    assert "apuntes-álgebra.pdf" in text


# ======================================================================
# 回归: 注入 logger 的 ApiServer 必须能正常响应
# ======================================================================


def test_api_server_with_an_injected_logger_still_serves_requests(tmp_path, stream):
    """Task 44 修复的真实缺陷的回归测试。

    修复前: ``log_message`` 传 ``extra={"message": ...}`` -> logging 抛
    ``KeyError`` -> 而 ``log_message`` 位于 ``send_response()`` 的路径上,
    于是**每个响应**都在发头之前崩掉 (客户端看到 RemoteDisconnected)。
    """
    import urllib.request

    from src.application.workspace import Workspace
    from src.api.server import create_server

    name = "classroom.t12"
    configure_logging(level="INFO", stream=stream, clock=lambda: FIXED_TS, name=name)
    logger = logging.getLogger(name)
    logger.propagate = False
    try:
        workspace = Workspace(str(tmp_path / "data"))
        server = create_server(workspace, port=0, logger=logger)
        server.start()
        try:
            with urllib.request.urlopen(server.url + "/api/health", timeout=10) as response:
                body = json.loads(response.read())
            assert response.status == 200
            assert body["success"] is True
            assert body["data"]["status"] == "ok"
        finally:
            server.stop()

        text = stream.getvalue()
        assert "event=http_request" in text
        assert "GET /api/health" in text
        assert "KeyError" not in text
    finally:
        reset_logging(name)


def test_api_server_without_a_logger_is_still_silent(tmp_path):
    import urllib.request

    from src.application.workspace import Workspace
    from src.api.server import create_server

    workspace = Workspace(str(tmp_path / "data"))
    server = create_server(workspace, port=0)
    server.start()
    try:
        with urllib.request.urlopen(server.url + "/api/health", timeout=10) as response:
            assert response.status == 200
    finally:
        server.stop()


def test_api_server_http_log_does_not_leak_a_secret_in_the_query(tmp_path, stream):
    """查询串会进访问日志 —— 密钥形状必须在那里也被抹掉。"""
    import urllib.request

    from src.application.workspace import Workspace
    from src.api.server import create_server

    name = "classroom.t13"
    configure_logging(level="INFO", stream=stream, clock=lambda: FIXED_TS, name=name)
    logger = logging.getLogger(name)
    logger.propagate = False
    try:
        workspace = Workspace(str(tmp_path / "data"))
        server = create_server(workspace, port=0, logger=logger)
        server.start()
        try:
            try:
                urllib.request.urlopen(
                    server.url + f"/api/courses?api_key={FAKE_SECRET}", timeout=10
                ).read()
            except Exception:  # noqa: BLE001 - 404 无所谓, 我们只看日志
                pass
        finally:
            server.stop()
        assert FAKE_SECRET not in stream.getvalue()
    finally:
        reset_logging(name)


def test_build_runtime_configures_logging_to_a_stream(tmp_path):
    """bootstrap 必须把 config.log_level 真正应用到日志上。"""
    from src.application.bootstrap import build_runtime, close_runtime

    stream = io.StringIO()
    config = AppConfig.load(
        env={},
        cli_overrides={
            "data_dir": str(tmp_path / "data"),
            "asr_mode": "mock",
            "ocr_config": {"kind": "mock"},
            "log_level": "debug",
        },
    )
    reset_logging()
    try:
        runtime = build_runtime(config, log_stream=stream, clock=lambda: FIXED_TS)
        try:
            text = stream.getvalue()
            assert "runtime_ready" in text
            assert FIXED_TS in text
        finally:
            close_runtime(runtime)
    finally:
        reset_logging()
