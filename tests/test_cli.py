# -*- coding: utf-8 -*-
"""Task 44 —— 命令行入口。

覆盖三件事:
1. argparse 的开关到配置覆盖项的映射 (含 ``--no-*`` 与"没给"的区分);
2. ``--print-config`` / ``--check`` 两个排查用动作;
3. 生产/开发两种错误呈现 (友好错误 vs debug 细节) 与退出码。
"""

from __future__ import annotations

import io
import json
import os
import pathlib

import pytest

from src.application.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_OK,
    EXIT_STARTUP_ERROR,
    CliOptions,
    _emit,
    build_config,
    build_parser,
    main,
    parse_cli,
    run,
)
from src.application.config import DEFAULT_CONFIG_FILENAME, DEFAULT_PORT
from src.application.logging_setup import reset_logging


@pytest.fixture(autouse=True)
def _clean_logging():
    reset_logging()
    yield
    reset_logging()


@pytest.fixture
def cwd(tmp_path: pathlib.Path) -> str:
    work = tmp_path / "work"
    work.mkdir()
    return str(work)


def _args(tmp_path: pathlib.Path, *extra: str) -> list[str]:
    """始终把 data_dir 指向 tmp, 免得碰到真实目录。"""
    return ["--data-dir", str(tmp_path / "data"), *extra]


# ======================================================================
# 解析
# ======================================================================


def test_no_arguments_means_no_overrides():
    options = parse_cli([])
    assert options.overrides == {}
    assert options.config_path is None
    assert options.print_config is False
    assert options.check_only is False
    assert options.json_output is False


@pytest.mark.parametrize(
    "flag,key,value",
    [
        ("--data-dir", "data_dir", "D:/data"),
        ("--database-path", "database_path", "D:/data/db.sqlite"),
        ("--host", "host", "localhost"),
        ("--port", "port", 9000),
        ("--max-upload-size", "max_upload_size", 4096),
        ("--whisper-model", "whisper_model", "small"),
        ("--whisper-device", "whisper_device", "cuda"),
        ("--whisper-compute-type", "whisper_compute_type", "float16"),
        ("--whisper-language", "whisper_language", "ca"),
        ("--asr-mode", "asr_mode", "mock"),
        ("--log-level", "log_level", "debug"),
    ],
)
def test_value_flags_map_to_overrides(flag, key, value):
    options = parse_cli([flag, str(value)])
    assert options.overrides[key] == value


def test_equals_form_is_accepted():
    assert parse_cli(["--port=9000"]).overrides["port"] == 9000


def test_ocr_config_takes_a_json_object(tmp_path, cwd):
    """CLI 层原样保留字符串, JSON 解析属于配置层的职责 —— 校验只有一处。"""
    options = parse_cli(["--ocr-config", '{"kind": "mock"}'])
    assert options.overrides["ocr_config"] == '{"kind": "mock"}'
    config = build_config(options, env={}, cwd=cwd)
    assert config.ocr_config["kind"] == "mock"


def test_debug_flag_sets_true():
    assert parse_cli(["--debug"]).overrides["debug"] is True


def test_no_debug_flag_sets_false():
    assert parse_cli(["--no-debug"]).overrides["debug"] is False


def test_absent_debug_flag_is_not_an_override():
    """关键: 没给开关 ≠ 给了 False, 否则会把配置文件里的 debug=true 压掉。"""
    assert "debug" not in parse_cli([]).overrides
    assert "allow_remote" not in parse_cli([]).overrides


def test_allow_remote_flags():
    assert parse_cli(["--allow-remote"]).overrides["allow_remote"] is True
    assert parse_cli(["--no-allow-remote"]).overrides["allow_remote"] is False


def test_last_boolean_flag_wins():
    assert parse_cli(["--debug", "--no-debug"]).overrides["debug"] is False
    assert parse_cli(["--no-debug", "--debug"]).overrides["debug"] is True


def test_config_flag_is_not_an_override():
    options = parse_cli(["--config", "D:/cfg.json"])
    assert options.config_path == "D:/cfg.json"
    assert "config_path" not in options.overrides


def test_action_flags():
    options = parse_cli(["--print-config", "--check", "--json"])
    assert options.print_config is True
    assert options.check_only is True
    assert options.json_output is True


def test_empty_whisper_language_is_kept_as_an_explicit_clear():
    options = parse_cli(["--whisper-language", ""])
    assert options.overrides["whisper_language"] == ""


def test_wants_debug_reflects_the_override():
    assert parse_cli(["--debug"]).wants_debug is True
    assert parse_cli([]).wants_debug is False


def test_version_flag_exits():
    with pytest.raises(SystemExit) as excinfo:
        parse_cli(["--version"])
    assert excinfo.value.code == 0


def test_help_flag_exits():
    with pytest.raises(SystemExit) as excinfo:
        parse_cli(["--help"])
    assert excinfo.value.code == 0


def test_unknown_flag_is_an_error():
    with pytest.raises(SystemExit) as excinfo:
        parse_cli(["--frobnicate"])
    assert excinfo.value.code != 0


def test_parser_mentions_the_spec_defaults():
    text = build_parser().format_help()
    for fragment in ("data_dir", "port", "whisper", "ocr", "log-level"):
        assert fragment in text


def test_parser_does_not_advertise_a_remote_default():
    """默认必须仍是回环地址。"""
    assert "127.0.0.1" in build_parser().format_help()


# ======================================================================
# 解析 -> 配置
# ======================================================================


def test_build_config_applies_cli_over_the_environment(cwd):
    options = parse_cli(["--port", "1234"])
    config = build_config(options, env={"CLASSROOM_PORT": "9999"}, cwd=cwd)
    assert config.port == 1234


def test_build_config_reads_the_environment(cwd):
    config = build_config(CliOptions(), env={"CLASSROOM_LOG_LEVEL": "error"}, cwd=cwd)
    assert config.log_level == "ERROR"


def test_build_config_loads_local_ai_environment_for_process_start(cwd, monkeypatch):
    calls = []
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(
        "src.application.cli.load_local_environment",
        lambda: calls.append("loaded") or (),
    )

    build_config(CliOptions(), cwd=cwd)

    assert calls == ["loaded"]


def test_build_config_reads_a_config_file(tmp_path, cwd):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"port": 4321}), encoding="utf-8")
    config = build_config(CliOptions(config_path=str(path)), env={}, cwd=cwd)
    assert config.port == 4321


def test_build_config_validates(tmp_path, cwd):
    from src.application.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        build_config(CliOptions(overrides={"port": -1}), env={}, cwd=cwd)


# ======================================================================
# --print-config
# ======================================================================


def test_print_config_returns_ok_without_touching_the_disk(tmp_path, cwd, capsys):
    data_dir = tmp_path / "never-created"
    code = run(
        CliOptions(print_config=True, overrides={"data_dir": str(data_dir)}),
        env={},
        cwd=cwd,
    )
    assert code == EXIT_OK
    assert not data_dir.exists(), "--print-config 不该建目录或开库"
    assert "data_dir" in capsys.readouterr().out


def test_print_config_shows_the_winning_source(tmp_path, cwd, capsys):
    run(
        CliOptions(print_config=True, overrides={"data_dir": str(tmp_path / "d"), "port": 1234}),
        env={},
        cwd=cwd,
    )
    out = capsys.readouterr().out
    assert "[cli]" in out
    assert "[default]" in out


def test_print_config_json_is_parseable(tmp_path, cwd, capsys):
    code = run(
        CliOptions(
            print_config=True,
            json_output=True,
            overrides={"data_dir": str(tmp_path / "d")},
        ),
        env={},
        cwd=cwd,
    )
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["data_dir"] == os.path.abspath(str(tmp_path / "d"))
    assert "sources" in payload


def test_print_config_never_prints_a_secret(tmp_path, cwd, capsys):
    run(
        CliOptions(
            print_config=True,
            overrides={
                "data_dir": str(tmp_path / "d"),
                "ocr_config": {"note": "api_key=sk-live-DEADBEEF0123456789"},
            },
        ),
        env={},
        cwd=cwd,
    )
    assert "sk-live-DEADBEEF0123456789" not in capsys.readouterr().out


# ======================================================================
# --check
# ======================================================================


def test_check_builds_the_runtime_and_creates_the_database(tmp_path, cwd, capsys):
    data_dir = tmp_path / "data"
    code = run(
        CliOptions(
            check_only=True,
            overrides={
                "data_dir": str(data_dir),
                "asr_mode": "mock",
                "ocr_config": {"kind": "mock"},
            },
        ),
        env={},
        cwd=cwd,
    )
    assert code == EXIT_OK
    assert (data_dir / "database" / "classroom.sqlite").is_file()
    out = capsys.readouterr().out
    assert "database_schema_version" in out
    assert "asr_mode" in out


def test_check_json_is_parseable(tmp_path, cwd, capsys, monkeypatch):
    for name in (
        "CLASSROOM_AI_ENABLED",
        "CLASSROOM_AI_API_KEY",
        "CLASSROOM_AI_BASE_URL",
        "CLASSROOM_LLM_API_KEY",
        "CLASSROOM_LLM_API_BASE",
    ):
        monkeypatch.delenv(name, raising=False)
    code = run(
        CliOptions(
            check_only=True,
            json_output=True,
            overrides={
                "data_dir": str(tmp_path / "data"),
                "asr_mode": "mock",
                "ocr_config": {"kind": "mock"},
            },
        ),
        env={},
        cwd=cwd,
    )
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["application"] == "Classroom Assistant"
    assert payload["asr_mode"] == "mock"
    assert payload["ocr_mode"] == "mock"
    assert payload["ai_mode"] == "disabled"
    assert payload["ai_enabled"] is False
    assert isinstance(payload["database_schema_version"], int)


def test_check_never_prints_the_ai_key(tmp_path, cwd, capsys, monkeypatch):
    secret = "sk-test-AI-KEY-MUST-NOT-APPEAR-0123456789"
    monkeypatch.setenv("CLASSROOM_AI_ENABLED", "true")
    monkeypatch.setenv("CLASSROOM_AI_API_KEY", secret)
    monkeypatch.setenv("CLASSROOM_AI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("CLASSROOM_AI_MODEL", "test-model")
    monkeypatch.delenv("CLASSROOM_LLM_API_KEY", raising=False)

    code = run(
        CliOptions(
            check_only=True,
            json_output=True,
            overrides={
                "data_dir": str(tmp_path / "data"),
                "asr_mode": "mock",
                "ocr_config": {"kind": "mock"},
            },
        ),
        env={},
        cwd=cwd,
    )

    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert '"ai_mode": "real"' in out
    assert secret not in out


def test_check_reports_the_requested_port(tmp_path, cwd, capsys):
    run(
        CliOptions(
            check_only=True,
            json_output=True,
            overrides={
                "data_dir": str(tmp_path / "data"),
                "port": 0,
                "asr_mode": "mock",
                "ocr_config": {"kind": "mock"},
            },
        ),
        env={},
        cwd=cwd,
    )
    assert json.loads(capsys.readouterr().out)["port"] == 0


def test_check_does_not_bind_a_port(tmp_path, cwd):
    """``--check`` 只装配, 不占端口 —— 否则 health 脚本会和主进程打架。"""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    code = run(
        CliOptions(
            check_only=True,
            overrides={
                "data_dir": str(tmp_path / "data"),
                "port": free_port,
                "asr_mode": "mock",
                "ocr_config": {"kind": "mock"},
            },
        ),
        env={},
        cwd=cwd,
    )
    assert code == EXIT_OK


# ======================================================================
# 退出码与错误呈现
# ======================================================================


def test_exit_codes_are_distinct():
    assert len({EXIT_OK, EXIT_CONFIG_ERROR, EXIT_STARTUP_ERROR}) == 3


def test_invalid_configuration_returns_the_config_exit_code(tmp_path, cwd, capsys):
    code = run(
        CliOptions(overrides={"data_dir": str(tmp_path / "d"), "port": -1}),
        env={},
        cwd=cwd,
    )
    assert code == EXIT_CONFIG_ERROR
    captured = capsys.readouterr()
    assert "CONFIGURATION_ERROR" in captured.err
    assert "Traceback" not in captured.err  # 生产模式: 不暴露细节


def test_debug_mode_exposes_the_traceback(tmp_path, cwd, capsys):
    """开发环境可以提供 debug 信息 (规范原文)。"""
    code = run(
        CliOptions(overrides={"data_dir": str(tmp_path / "d"), "port": -1, "debug": True}),
        env={},
        cwd=cwd,
    )
    assert code == EXIT_CONFIG_ERROR
    assert "Traceback" in capsys.readouterr().err


def test_startup_failure_returns_the_startup_exit_code(tmp_path, cwd, capsys, monkeypatch):
    from src.application.bootstrap import build_runtime
    from src.application.errors import ConfigurationError

    def _boom(*args, **kwargs):
        raise ConfigurationError("database is unreadable")

    monkeypatch.setattr("src.application.bootstrap.build_runtime", _boom)
    code = run(
        CliOptions(
            check_only=True,
            overrides={
                "data_dir": str(tmp_path / "data"),
                "asr_mode": "mock",
                "ocr_config": {"kind": "mock"},
            },
        ),
        env={},
        cwd=cwd,
    )
    assert code == EXIT_STARTUP_ERROR
    assert "database is unreadable" in capsys.readouterr().err
    assert build_runtime is not None


def test_a_friendly_error_does_not_leak_a_secret(tmp_path, cwd, capsys):
    run(
        CliOptions(
            overrides={
                "data_dir": str(tmp_path / "d"),
                "ocr_config": {"api_key": "sk-live-DEADBEEF0123456789"},
            }
        ),
        env={},
        cwd=cwd,
    )
    assert "sk-live-DEADBEEF0123456789" not in capsys.readouterr().err


# ======================================================================
# Windows 控制台编码
# ======================================================================


class _AsciiOnlyStream:
    """模拟一个只能写 ASCII 的 Windows 控制台。"""

    encoding = "ascii"

    def __init__(self) -> None:
        self.chunks: list[str] = []

    def write(self, text: str) -> None:
        text.encode("ascii")  # 非 ASCII 会抛 UnicodeEncodeError
        self.chunks.append(text)

    def flush(self) -> None:
        pass

    @property
    def text(self) -> str:
        return "".join(self.chunks)


def test_emit_survives_a_non_utf8_console():
    """data_dir 里可能有 Á / à / 中文 —— 不能因为一条路径就让启动失败。"""
    stream = _AsciiOnlyStream()
    _emit("data_dir = D:/资料/álgebra", stream=stream)
    assert stream.text  # 降级替换后仍然写出来了
    assert "data_dir" in stream.text


def test_emit_writes_a_newline():
    stream = io.StringIO()
    _emit("hello", stream=stream)
    assert stream.getvalue() == "hello\n"


def test_emit_does_not_double_the_newline():
    stream = io.StringIO()
    _emit("hello\n", stream=stream)
    assert stream.getvalue() == "hello\n"


def test_print_config_survives_a_non_utf8_console(tmp_path, cwd, monkeypatch):
    stream = _AsciiOnlyStream()
    monkeypatch.setattr("sys.stdout", stream)
    code = run(
        CliOptions(
            print_config=True,
            overrides={"data_dir": str(tmp_path / "资料")},
        ),
        env={},
        cwd=cwd,
    )
    assert code == EXIT_OK
    assert "data_dir" in stream.text


# ======================================================================
# main
# ======================================================================


def test_main_with_print_config(tmp_path, capsys):
    code = main(["--print-config", "--data-dir", str(tmp_path / "data")])
    assert code == EXIT_OK
    assert "data_dir" in capsys.readouterr().out


def test_main_with_check(tmp_path, capsys):
    code = main(
        [
            "--check",
            "--json",
            "--data-dir",
            str(tmp_path / "data"),
            "--asr-mode",
            "mock",
            "--ocr-config",
            '{"kind": "mock"}',
        ]
    )
    assert code == EXIT_OK
    assert json.loads(capsys.readouterr().out)["asr_mode"] == "mock"


def test_main_defaults_to_the_standard_port_in_print_config(tmp_path, capsys):
    main(["--print-config", "--json", "--data-dir", str(tmp_path / "data")])
    assert json.loads(capsys.readouterr().out)["port"] == DEFAULT_PORT


def test_main_reads_a_config_file(tmp_path, capsys):
    config_file = tmp_path / DEFAULT_CONFIG_FILENAME
    config_file.write_text(json.dumps({"port": 7777}), encoding="utf-8")
    code = main(["--print-config", "--json", "--config", str(config_file)])
    assert code == EXIT_OK
    assert json.loads(capsys.readouterr().out)["port"] == 7777
