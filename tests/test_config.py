# -*- coding: utf-8 -*-
"""Task 44 —— AppConfig / 配置优先级 / 校验。

规范要求 (Task 44):
- ``AppConfig`` 至少包含 data_dir / database_path / host / port /
  max_upload_size / whisper_model / whisper_device / ocr_config / log_level。
- 优先级 ``defaults -> config file -> environment variables -> CLI overrides``,
  规则明确且**被测试**。
- 至少 40 个测试 (本任务合计远超)。

这个文件刻意大量使用"来源断言" (``config.source_of(key)``), 而不只是断言
最终值。原因: 只看最终值的话, "CLI 覆盖了环境变量"与"环境变量根本没生效"
在结果上可能长得一模一样 —— 优先级就变成了不可验证的口号。
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib

import pytest

from src.application.config import (
    ASR_MODES,
    CONFIGURABLE_KEYS,
    DEFAULT_CONFIG_FILENAME,
    DEFAULT_DATA_DIR_NAME,
    DEFAULT_DATABASE_FILENAME,
    DEFAULT_HOST,
    DEFAULT_LOG_LEVEL,
    DEFAULT_MAX_UPLOAD_SIZE,
    DEFAULT_PORT,
    ENV_PREFIX,
    ENV_VARS,
    LOG_LEVELS,
    OCR_KINDS,
    AppConfig,
    ConfigSource,
    default_values,
    discover_config_file,
    load_config,
    normalise_overrides,
    read_config_file,
    read_environment,
)
from src.application.errors import ConfigurationError

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def cwd(tmp_path: pathlib.Path) -> str:
    """一个空的工作目录 —— 默认 data_dir 会落在它下面。"""
    work = tmp_path / "work"
    work.mkdir()
    return str(work)


def _write_config(directory: str, payload: object, name: str = DEFAULT_CONFIG_FILENAME) -> str:
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        if isinstance(payload, str):
            handle.write(payload)
        else:
            json.dump(payload, handle, ensure_ascii=False)
    return path


# ======================================================================
# 默认值
# ======================================================================


def test_defaults_are_local_and_loopback(cwd):
    config = load_config(cwd=cwd, env={})
    assert config.host == "127.0.0.1"
    assert config.port == DEFAULT_PORT
    assert config.log_level == "INFO"
    assert config.debug is False
    assert config.allow_remote is False


def test_default_data_dir_is_under_the_working_directory(cwd):
    config = load_config(cwd=cwd, env={})
    assert config.data_dir == os.path.join(os.path.abspath(cwd), DEFAULT_DATA_DIR_NAME)


def test_default_values_do_not_hardcode_database_path(cwd):
    """database_path 必须是**派生**的, 而不是一个写死的默认绝对路径。"""
    assert "database_path" not in default_values(cwd)


def test_database_path_is_derived_from_data_dir(cwd):
    config = load_config(cwd=cwd, env={})
    assert config.database_path == os.path.join(
        config.data_dir, "database", DEFAULT_DATABASE_FILENAME
    )
    assert config.source_of("database_path") is ConfigSource.DERIVED


def test_every_default_value_is_recorded_as_coming_from_defaults(cwd):
    config = load_config(cwd=cwd, env={})
    for key in CONFIGURABLE_KEYS - {"database_path"}:
        assert config.source_of(key) is ConfigSource.DEFAULT, key


def test_database_filename_is_a_bare_name(cwd):
    config = load_config(cwd=cwd, env={})
    assert config.database_filename == DEFAULT_DATABASE_FILENAME
    assert os.path.basename(config.database_filename) == config.database_filename


def test_derived_directories_live_inside_data_dir(cwd):
    config = load_config(cwd=cwd, env={})
    assert config.backups_dir == os.path.join(config.data_dir, "backups")
    assert config.logs_dir == os.path.join(config.data_dir, "logs")
    assert config.database_dir == os.path.dirname(config.database_path)


def test_data_dir_is_absolutised(cwd):
    """相对路径按**进程工作目录**解析 (标准 CLI 行为), 不受 ``cwd`` 参数影响。"""
    config = load_config(cwd=cwd, env={}, cli_overrides={"data_dir": "relative-data"})
    assert os.path.isabs(config.data_dir)
    assert config.data_dir == os.path.join(os.getcwd(), "relative-data")


def test_load_is_deterministic(tmp_path, cwd):
    first = load_config(cwd=cwd, env={}, cli_overrides={"port": 1234})
    second = load_config(cwd=cwd, env={}, cli_overrides={"port": 1234})
    assert first == second
    assert first.to_dict() == second.to_dict()


# ======================================================================
# 单一真相: 复述的常量必须与源头一致
# ======================================================================


def test_config_defaults_match_their_sources():
    """配置层刻意不 import api / backup (会带来 import 环与分层越界),
    因此几个常量在这里复述了一遍。**由本测试保证它们不会漂移**。
    """
    from src.api.endpoints import MAX_UPLOAD_BYTES
    from src.api.server import DEFAULT_HOST as SERVER_HOST
    from src.api.server import DEFAULT_PORT as SERVER_PORT
    from src.backup.service import DEFAULT_DATABASE_FILENAME as BACKUP_DB_NAME
    from src.whisper_provider import DEFAULT_WHISPER_MODEL

    assert DEFAULT_HOST == SERVER_HOST
    assert DEFAULT_PORT == SERVER_PORT
    assert DEFAULT_MAX_UPLOAD_SIZE == MAX_UPLOAD_BYTES
    assert DEFAULT_DATABASE_FILENAME == BACKUP_DB_NAME
    assert default_values("/x")["whisper_model"] == DEFAULT_WHISPER_MODEL


def test_configurable_keys_match_the_dataclass_fields():
    """新增配置项时, 必须同时进 CONFIGURABLE_KEYS —— 否则它无法被任何一层设置。"""
    fields = {f.name for f in dataclasses.fields(AppConfig)} - {
        "config_path",
        "sources",
        "unknown_env_vars",
    }
    assert fields == set(CONFIGURABLE_KEYS)


def test_env_vars_cover_every_configurable_key():
    assert set(ENV_VARS.values()) == set(CONFIGURABLE_KEYS)


def test_every_env_var_name_is_prefixed():
    for name in ENV_VARS:
        assert name.startswith(ENV_PREFIX), name


def test_required_spec_fields_are_present():
    """规范点名的 9 个字段一个都不能少。"""
    required = {
        "data_dir",
        "database_path",
        "host",
        "port",
        "max_upload_size",
        "whisper_model",
        "whisper_device",
        "ocr_config",
        "log_level",
    }
    assert required <= set(CONFIGURABLE_KEYS)


# ======================================================================
# 优先级: defaults -> file -> environment -> CLI
# ======================================================================


def test_config_file_overrides_defaults(cwd):
    _write_config(cwd, {"port": 1111, "log_level": "debug"})
    config = load_config(cwd=cwd, env={})
    assert config.port == 1111
    assert config.source_of("port") is ConfigSource.FILE
    assert config.log_level == "DEBUG"  # 大小写被规范化
    assert config.source_of("log_level") is ConfigSource.FILE


def test_environment_overrides_config_file(cwd):
    _write_config(cwd, {"port": 1111})
    config = load_config(cwd=cwd, env={"CLASSROOM_PORT": "2222"})
    assert config.port == 2222
    assert config.source_of("port") is ConfigSource.ENV


def test_cli_overrides_environment(cwd):
    _write_config(cwd, {"port": 1111})
    config = load_config(
        cwd=cwd, env={"CLASSROOM_PORT": "2222"}, cli_overrides={"port": 3333}
    )
    assert config.port == 3333
    assert config.source_of("port") is ConfigSource.CLI


def test_all_four_layers_apply_at_once(cwd):
    """同一份配置里, 每个键各自来自它应该来自的那一层。"""
    _write_config(cwd, {"port": 1111, "host": "localhost", "log_level": "debug"})
    config = load_config(
        cwd=cwd,
        env={"CLASSROOM_HOST": "::1", "CLASSROOM_LOG_LEVEL": "warning"},
        cli_overrides={"log_level": "critical"},
    )
    assert (config.port, config.source_of("port")) == (1111, ConfigSource.FILE)
    assert (config.host, config.source_of("host")) == ("::1", ConfigSource.ENV)
    assert (config.log_level, config.source_of("log_level")) == (
        "CRITICAL",
        ConfigSource.CLI,
    )
    # 没被任何上层碰过的键仍然来自默认值。
    assert config.source_of("max_upload_size") is ConfigSource.DEFAULT


def test_absent_key_in_a_high_layer_does_not_clear_a_low_layer(cwd):
    _write_config(cwd, {"port": 1111})
    config = load_config(cwd=cwd, env={"CLASSROOM_LOG_LEVEL": "debug"})
    assert config.port == 1111


def test_explicit_null_in_a_high_layer_clears_a_low_layer(cwd):
    """``None`` = 显式置空, 与"这个键没出现"是两件事。"""
    _write_config(cwd, {"whisper_language": "ca"})
    assert load_config(cwd=cwd, env={}).whisper_language == "ca"
    cleared = load_config(cwd=cwd, env={}, cli_overrides={"whisper_language": ""})
    assert cleared.whisper_language is None
    assert cleared.source_of("whisper_language") is ConfigSource.CLI


def test_file_layer_null_is_recorded_as_file_source(cwd):
    _write_config(cwd, {"whisper_language": None})
    config = load_config(cwd=cwd, env={})
    assert config.whisper_language is None
    assert config.source_of("whisper_language") is ConfigSource.FILE


@pytest.mark.parametrize(
    "key,value",
    [
        ("data_dir", "other-data"),
        ("host", "localhost"),
        ("port", 1234),
        ("max_upload_size", 4096),
        ("whisper_model", "small"),
        ("whisper_device", "cpu"),
        ("whisper_compute_type", "float32"),
        ("whisper_language", "ca"),
        ("asr_mode", "mock"),
        ("ocr_config", {"kind": "mock"}),
        ("log_level", "debug"),
        ("debug", True),
        ("allow_remote", True),
    ],
)
def test_every_key_can_be_overridden_from_the_cli(cwd, key, value):
    config = load_config(cwd=cwd, env={}, cli_overrides={key: value})
    assert config.source_of(key) is ConfigSource.CLI


def test_database_path_can_be_overridden_from_the_cli(cwd):
    target = os.path.join(cwd, DEFAULT_DATA_DIR_NAME, "custom", "db.sqlite")
    config = load_config(cwd=cwd, env={}, cli_overrides={"database_path": target})
    assert config.database_path == os.path.abspath(target)
    assert config.source_of("database_path") is ConfigSource.CLI


@pytest.mark.parametrize("env_name,key,value", sorted(
    [
        ("CLASSROOM_DATA_DIR", "data_dir", "env-data"),
        ("CLASSROOM_HOST", "host", "localhost"),
        ("CLASSROOM_PORT", "port", "4321"),
        ("CLASSROOM_MAX_UPLOAD_SIZE", "max_upload_size", "8192"),
        ("CLASSROOM_WHISPER_MODEL", "whisper_model", "medium"),
        ("CLASSROOM_WHISPER_DEVICE", "whisper_device", "cuda"),
        ("CLASSROOM_WHISPER_COMPUTE_TYPE", "whisper_compute_type", "float16"),
        ("CLASSROOM_WHISPER_LANGUAGE", "whisper_language", "es"),
        ("CLASSROOM_ASR_MODE", "asr_mode", "mock"),
        ("CLASSROOM_OCR_CONFIG", "ocr_config", '{"kind": "mock"}'),
        ("CLASSROOM_LOG_LEVEL", "log_level", "error"),
        ("CLASSROOM_DEBUG", "debug", "1"),
        ("CLASSROOM_ALLOW_REMOTE", "allow_remote", "true"),
    ]
))
def test_every_env_var_is_honoured(cwd, env_name, key, value):
    config = load_config(cwd=cwd, env={env_name: value})
    assert config.source_of(key) is ConfigSource.ENV


def test_environment_database_path_is_honoured(cwd):
    target = os.path.join(cwd, DEFAULT_DATA_DIR_NAME, "env-db.sqlite")
    config = load_config(cwd=cwd, env={"CLASSROOM_DATABASE_PATH": target})
    assert config.database_path == os.path.abspath(target)
    assert config.source_of("database_path") is ConfigSource.ENV


# ======================================================================
# 配置文件
# ======================================================================


def test_read_config_file_returns_coerced_values(tmp_path):
    path = _write_config(str(tmp_path), {"port": "1234", "debug": "yes"})
    values = read_config_file(path)
    assert values == {"port": 1234, "debug": True}


def test_read_config_file_rejects_a_missing_file(tmp_path):
    with pytest.raises(ConfigurationError) as excinfo:
        read_config_file(str(tmp_path / "nope.json"))
    assert excinfo.value.code == "CONFIGURATION_ERROR"
    assert "not found" in excinfo.value.message


def test_read_config_file_rejects_blank_path():
    with pytest.raises(ConfigurationError):
        read_config_file("   ")


def test_read_config_file_rejects_invalid_json(tmp_path):
    path = _write_config(str(tmp_path), "{not json")
    with pytest.raises(ConfigurationError) as excinfo:
        read_config_file(path)
    assert "not valid JSON" in excinfo.value.message


def test_read_config_file_rejects_a_json_array(tmp_path):
    path = _write_config(str(tmp_path), "[1, 2, 3]")
    with pytest.raises(ConfigurationError) as excinfo:
        read_config_file(path)
    assert "JSON object" in excinfo.value.message


def test_read_config_file_rejects_an_unknown_key(tmp_path):
    """拼错键名却静默忽略, 会变成"我明明配了却没生效"。"""
    path = _write_config(str(tmp_path), {"prot": 1234})
    with pytest.raises(ConfigurationError) as excinfo:
        read_config_file(path)
    assert "unknown configuration key" in excinfo.value.message
    assert excinfo.value.detail["unknown"] == ["prot"]


def test_read_config_file_reports_the_offending_layer_for_a_bad_value(tmp_path):
    path = _write_config(str(tmp_path), {"port": "not-a-number"})
    with pytest.raises(ConfigurationError) as excinfo:
        read_config_file(path)
    assert excinfo.value.detail["key"] == "port"
    assert "config file" in excinfo.value.detail["origin"]


def test_load_config_rejects_an_unknown_cli_key(cwd):
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(cwd=cwd, env={}, cli_overrides={"prot": 1})
    assert "unknown configuration key" in excinfo.value.message


def test_config_file_can_be_passed_explicitly(tmp_path, cwd):
    path = _write_config(str(tmp_path), {"port": 7777}, name="explicit.json")
    config = load_config(config_path=path, cwd=cwd, env={})
    assert config.port == 7777
    assert config.config_path == os.path.abspath(path)


def test_explicitly_requested_missing_config_file_is_an_error(cwd, tmp_path):
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(config_path=str(tmp_path / "ghost.json"), cwd=cwd, env={})
    assert "does not exist" in excinfo.value.message


def test_config_path_env_var_is_honoured(tmp_path, cwd):
    path = _write_config(str(tmp_path), {"port": 8888}, name="via-env.json")
    config = load_config(cwd=cwd, env={"CLASSROOM_CONFIG": path})
    assert config.port == 8888
    assert config.config_path == os.path.abspath(path)


def test_discover_returns_none_when_there_is_no_config_file(cwd):
    assert discover_config_file(cwd=cwd, env={}) is None
    assert load_config(cwd=cwd, env={}).config_path is None


def test_discover_finds_the_file_in_the_working_directory(cwd):
    """配置文件与启动目录放一起 —— 它的职责之一就是说明 data_dir 在哪,
    所以不可能反过来去 data_dir 里找它。
    """
    _write_config(cwd, {"port": 5555})
    config = load_config(cwd=cwd, env={})
    assert config.port == 5555
    assert config.config_path == os.path.join(os.path.abspath(cwd), DEFAULT_CONFIG_FILENAME)


def test_a_cli_data_dir_does_not_move_the_config_file_lookup(tmp_path):
    """``--data-dir`` 不改变配置文件位置 (规则只有两条, 没有隐含的第三条)。"""
    work = tmp_path / "work"
    work.mkdir()
    data_dir = tmp_path / "cli-data"
    data_dir.mkdir()
    _write_config(str(data_dir), {"port": 6666})
    config = load_config(
        cwd=str(work), env={}, cli_overrides={"data_dir": str(data_dir)}
    )
    assert config.port == DEFAULT_PORT
    assert config.config_path is None


def test_explicit_config_path_beats_the_data_dir_candidate(tmp_path, cwd):
    _write_config(cwd, {"port": 1111})
    explicit = _write_config(str(tmp_path), {"port": 2222}, name="explicit.json")
    config = load_config(config_path=explicit, cwd=cwd, env={})
    assert config.port == 2222


def test_config_file_may_be_utf8_with_multibyte_values(tmp_path, cwd):
    _write_config(str(tmp_path), {"data_dir": str(tmp_path / "资料-álgebra")})
    config = load_config(
        config_path=str(tmp_path / DEFAULT_CONFIG_FILENAME), cwd=cwd, env={}
    )
    assert "资料" in config.data_dir


# ======================================================================
# 环境变量: 未知变量只记名字, 绝不记值
# ======================================================================


def test_unknown_env_vars_are_recorded_by_name():
    values, unknown = read_environment(
        {"CLASSROOM_PORT": "1", "CLASSROOM_PROT": "9999", "CLASSROOM_API_KEY": "x"}
    )
    assert values == {"port": 1}
    assert unknown == ("CLASSROOM_API_KEY", "CLASSROOM_PROT")


def test_unknown_env_var_values_are_never_stored(cwd):
    """诊断信息里泄露一个 API key, 等于把密钥写进了日志。"""
    config = load_config(
        cwd=cwd, env={"CLASSROOM_API_KEY": "super-secret-value"}
    )
    blob = json.dumps(config.to_dict(), ensure_ascii=False) + repr(config) + config.describe()
    assert "super-secret-value" not in blob
    assert config.unknown_env_vars == ("CLASSROOM_API_KEY",)


def test_non_classroom_env_vars_are_ignored():
    values, unknown = read_environment({"PATH": "/usr/bin", "HOME": "/root"})
    assert values == {}
    assert unknown == ()


def test_config_file_env_var_is_not_a_config_key():
    values, unknown = read_environment({"CLASSROOM_CONFIG": "/tmp/x.json"})
    assert values == {}
    assert unknown == ()


def test_read_environment_is_deterministic():
    first = read_environment({"CLASSROOM_PORT": "1", "CLASSROOM_B": "x", "CLASSROOM_A": "y"})
    second = read_environment({"CLASSROOM_A": "y", "CLASSROOM_B": "x", "CLASSROOM_PORT": "1"})
    assert first == second


def test_empty_environment_yields_nothing():
    assert read_environment({}) == ({}, ())


# ======================================================================
# 类型转换
# ======================================================================


@pytest.mark.parametrize("text,expected", [("1234", 1234), ("+42", 42), ("-7", -7), (" 9 ", 9)])
def test_integers_can_come_from_strings(cwd, text, expected):
    overrides = normalise_overrides({"port": text})
    assert overrides["port"] == expected


@pytest.mark.parametrize("bad", ["abc", "12.5", "", "1e3", True, 1.5, None])
def test_bad_integers_are_rejected(cwd, bad):
    if bad is None:
        assert normalise_overrides({"port": bad}) == {}
        return
    with pytest.raises(ConfigurationError):
        normalise_overrides({"port": bad})


@pytest.mark.parametrize("text", ["1", "true", "TRUE", "yes", "on", "y", "t"])
def test_truthy_strings(cwd, text):
    assert normalise_overrides({"debug": text})["debug"] is True


@pytest.mark.parametrize("text", ["0", "false", "no", "off", "n", "f", "FALSE"])
def test_falsy_strings(cwd, text):
    assert normalise_overrides({"debug": text})["debug"] is False


def test_booleans_accept_json_ints(cwd):
    assert normalise_overrides({"debug": 1})["debug"] is True
    assert normalise_overrides({"debug": 0})["debug"] is False


@pytest.mark.parametrize("bad", ["maybe", "2", 3, 1.0, []])
def test_bad_booleans_are_rejected(cwd, bad):
    with pytest.raises(ConfigurationError):
        normalise_overrides({"debug": bad})


def test_ocr_config_accepts_a_json_string(cwd):
    overrides = normalise_overrides({"ocr_config": '{"kind": "mock", "min_confidence": 0.5}'})
    assert overrides["ocr_config"] == {"kind": "mock", "min_confidence": 0.5}


def test_ocr_config_accepts_an_empty_json_string(cwd):
    assert normalise_overrides({"ocr_config": "  "})["ocr_config"] == {}


def test_ocr_config_rejects_invalid_json(cwd):
    with pytest.raises(ConfigurationError) as excinfo:
        normalise_overrides({"ocr_config": "{oops"})
    assert "JSON object" in excinfo.value.message


def test_ocr_config_rejects_a_json_scalar(cwd):
    with pytest.raises(ConfigurationError):
        normalise_overrides({"ocr_config": "42"})


def test_ocr_config_rejects_non_string_keys(cwd):
    with pytest.raises(ConfigurationError):
        normalise_overrides({"ocr_config": {1: "x"}})


def test_ocr_config_rejects_a_list(cwd):
    with pytest.raises(ConfigurationError):
        normalise_overrides({"ocr_config": [1, 2]})


def test_text_fields_reject_non_strings(cwd):
    with pytest.raises(ConfigurationError):
        normalise_overrides({"host": 1234})
    with pytest.raises(ConfigurationError):
        normalise_overrides({"host": True})


def test_text_values_are_stripped(cwd):
    config = load_config(cwd=cwd, env={}, cli_overrides={"host": "  localhost  "})
    assert config.host == "localhost"


def test_enum_values_are_case_normalised(cwd):
    config = load_config(
        cwd=cwd,
        env={},
        cli_overrides={"log_level": "wArNiNg", "whisper_device": "CPU", "asr_mode": "MOCK"},
    )
    assert config.log_level == "WARNING"
    assert config.whisper_device == "cpu"
    assert config.asr_mode == "mock"


def test_none_overrides_mean_not_specified(cwd):
    assert normalise_overrides({"port": None, "debug": None}) == {}
    assert normalise_overrides(None) == {}
    assert normalise_overrides({}) == {}


# ======================================================================
# 校验
# ======================================================================


@pytest.mark.parametrize("bad", ["", "   ", 1234])
def test_data_dir_must_be_a_non_empty_string(cwd, bad):
    with pytest.raises(ConfigurationError):
        load_config(cwd=cwd, env={}, cli_overrides={"data_dir": bad})


def test_a_none_data_dir_override_means_not_specified(cwd):
    """``None`` = 这个开关没给, 于是沿用下层 —— 不是"把 data_dir 设成 None"。"""
    config = load_config(cwd=cwd, env={}, cli_overrides={"data_dir": None})
    assert config.source_of("data_dir") is ConfigSource.DEFAULT


def test_data_dir_must_not_be_a_file(tmp_path, cwd):
    target = tmp_path / "a-file"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(cwd=cwd, env={}, cli_overrides={"data_dir": str(target)})
    assert "is a file" in excinfo.value.message


@pytest.mark.parametrize("name", ["src", "tests"])
def test_data_dir_must_not_live_inside_the_source_tree(name):
    """AGENTS.md 硬性规则: 用户数据绝不写入 src/ 或 tests/。"""
    forbidden = PROJECT_ROOT / name / "sneaky-data"
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(env={}, cli_overrides={"data_dir": str(forbidden)})
    assert "must not live inside" in excinfo.value.message


def test_data_dir_equal_to_the_source_tree_itself_is_rejected():
    with pytest.raises(ConfigurationError):
        load_config(env={}, cli_overrides={"data_dir": str(PROJECT_ROOT / "src")})


def test_database_path_outside_data_dir_is_rejected(tmp_path, cwd):
    """否则备份/恢复会**静默漏掉**这个库 —— 最坏的一类数据丢失。"""
    outside = tmp_path / "elsewhere" / "db.sqlite"
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(
            cwd=cwd,
            env={},
            cli_overrides={
                "data_dir": os.path.join(cwd, "data"),
                "database_path": str(outside),
            },
        )
    assert "must live inside data_dir" in excinfo.value.message


def test_database_path_equal_to_data_dir_is_rejected(cwd):
    data = os.path.join(cwd, "data")
    with pytest.raises(ConfigurationError):
        load_config(
            cwd=cwd, env={}, cli_overrides={"data_dir": data, "database_path": data}
        )


def test_database_path_inside_data_dir_is_accepted(cwd):
    data = os.path.join(cwd, "data")
    config = load_config(
        cwd=cwd,
        env={},
        cli_overrides={"data_dir": data, "database_path": os.path.join(data, "db.sqlite")},
    )
    assert config.database_path == os.path.join(data, "db.sqlite")


def test_database_path_with_traversal_that_escapes_is_rejected(cwd):
    data = os.path.join(cwd, "data")
    with pytest.raises(ConfigurationError):
        load_config(
            cwd=cwd,
            env={},
            cli_overrides={
                "data_dir": data,
                "database_path": os.path.join(data, "..", "escaped.sqlite"),
            },
        )


@pytest.mark.parametrize("bad", [-1, 65536, 999999])
def test_port_out_of_range_is_rejected(cwd, bad):
    with pytest.raises(ConfigurationError):
        load_config(cwd=cwd, env={}, cli_overrides={"port": bad})


def test_port_zero_is_allowed(cwd):
    """0 = 让系统分配一个空闲端口 (测试与并发启动时很有用)。"""
    assert load_config(cwd=cwd, env={}, cli_overrides={"port": 0}).port == 0


def test_port_65535_is_allowed(cwd):
    assert load_config(cwd=cwd, env={}, cli_overrides={"port": 65535}).port == 65535


@pytest.mark.parametrize("bad", [0, -1])
def test_max_upload_size_must_be_positive(cwd, bad):
    with pytest.raises(ConfigurationError):
        load_config(cwd=cwd, env={}, cli_overrides={"max_upload_size": bad})


def test_implausibly_small_max_upload_size_is_rejected_with_a_hint(cwd):
    """200 通常意味着"想写 200 MB" —— 直接说清楚单位是字节。"""
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(cwd=cwd, env={}, cli_overrides={"max_upload_size": 200})
    assert "bytes" in excinfo.value.message


def test_max_upload_size_of_exactly_1_kib_is_accepted(cwd):
    assert load_config(cwd=cwd, env={}, cli_overrides={"max_upload_size": 1024}).max_upload_size == 1024


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "example.com"])
def test_non_loopback_host_is_rejected_by_default(cwd, host):
    """单机单用户应用默认绝不上网暴露。"""
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(cwd=cwd, env={}, cli_overrides={"host": host})
    assert "loopback" in excinfo.value.message


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_are_accepted(cwd, host):
    assert load_config(cwd=cwd, env={}, cli_overrides={"host": host}).host == host


def test_allow_remote_permits_a_non_loopback_host(cwd):
    config = load_config(
        cwd=cwd, env={}, cli_overrides={"host": "0.0.0.0", "allow_remote": True}
    )
    assert config.host == "0.0.0.0"
    assert config.allow_remote is True


@pytest.mark.parametrize("bad", ["TRACE", "verbose", "info2", ""])
def test_invalid_log_level_is_rejected(cwd, bad):
    with pytest.raises(ConfigurationError):
        load_config(cwd=cwd, env={}, cli_overrides={"log_level": bad})


@pytest.mark.parametrize("level", LOG_LEVELS)
def test_every_spec_log_level_is_accepted(cwd, level):
    assert load_config(cwd=cwd, env={}, cli_overrides={"log_level": level.lower()}).log_level == level


@pytest.mark.parametrize("bad", ["sometimes", "REAL2", ""])
def test_invalid_asr_mode_is_rejected(cwd, bad):
    with pytest.raises(ConfigurationError):
        load_config(cwd=cwd, env={}, cli_overrides={"asr_mode": bad})


@pytest.mark.parametrize("mode", ASR_MODES)
def test_every_asr_mode_is_accepted(cwd, mode):
    assert load_config(cwd=cwd, env={}, cli_overrides={"asr_mode": mode}).asr_mode == mode


@pytest.mark.parametrize("kind", OCR_KINDS)
def test_every_ocr_kind_is_accepted(cwd, kind):
    config = load_config(cwd=cwd, env={}, cli_overrides={"ocr_config": {"kind": kind}})
    assert config.ocr_config["kind"] == kind


def test_invalid_ocr_kind_is_rejected(cwd):
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(cwd=cwd, env={}, cli_overrides={"ocr_config": {"kind": "magic"}})
    assert "kind" in excinfo.value.message


def test_ocr_require_real_must_be_a_boolean(cwd):
    with pytest.raises(ConfigurationError):
        load_config(cwd=cwd, env={}, cli_overrides={"ocr_config": {"require_real": "yes"}})


def test_ocr_config_must_be_json_serialisable(cwd):
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(cwd=cwd, env={}, cli_overrides={"ocr_config": {"engine_loader": object()}})
    assert "JSON-serialisable" in excinfo.value.message


@pytest.mark.parametrize("model", ["nope", "large-v4", ""])
def test_invalid_whisper_model_is_rejected(cwd, model):
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(cwd=cwd, env={}, cli_overrides={"whisper_model": model})
    assert excinfo.value.code == "CONFIGURATION_ERROR"


@pytest.mark.parametrize("device", ["gpu", "tpu", ""])
def test_invalid_whisper_device_is_rejected(cwd, device):
    with pytest.raises(ConfigurationError):
        load_config(cwd=cwd, env={}, cli_overrides={"whisper_device": device})


def test_invalid_whisper_compute_type_is_rejected(cwd):
    with pytest.raises(ConfigurationError):
        load_config(cwd=cwd, env={}, cli_overrides={"whisper_compute_type": "int4"})


def test_invalid_whisper_language_is_rejected(cwd):
    with pytest.raises(ConfigurationError):
        load_config(cwd=cwd, env={}, cli_overrides={"whisper_language": "espanol!"})


def test_whisper_validation_reuses_the_domain_error_as_cause(cwd):
    """配置层不重复实现 whisper 校验, 而是复用 domain 的, 因此原因被保留。"""
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(cwd=cwd, env={}, cli_overrides={"whisper_model": "nope"})
    assert excinfo.value.cause is not None
    assert excinfo.value.detail["domain_code"]


@pytest.mark.parametrize(
    "model", ["tiny", "base", "small", "medium", "large-v1", "large-v2", "large-v3", "distil-large-v3"]
)
def test_every_supported_whisper_model_is_accepted(cwd, model):
    assert load_config(cwd=cwd, env={}, cli_overrides={"whisper_model": model}).whisper_model == model


@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_every_supported_whisper_device_is_accepted(cwd, device):
    assert load_config(cwd=cwd, env={}, cli_overrides={"whisper_device": device}).whisper_device == device


@pytest.mark.parametrize("compute", ["int8", "float16", "float32", "int8_float16"])
def test_every_supported_compute_type_is_accepted(cwd, compute):
    config = load_config(cwd=cwd, env={}, cli_overrides={"whisper_compute_type": compute})
    assert config.whisper_compute_type == compute


def test_whisper_config_returns_a_validated_domain_object(cwd):
    from src.whisper_provider import WhisperConfig

    config = load_config(
        cwd=cwd, env={}, cli_overrides={"whisper_model": "small", "whisper_language": "ca"}
    )
    whisper = config.whisper_config()
    assert isinstance(whisper, WhisperConfig)
    assert whisper.model_name == "small"
    assert whisper.language == "ca"


def test_source_of_unknown_key_raises_key_error(cwd):
    config = load_config(cwd=cwd, env={})
    with pytest.raises(KeyError):
        config.source_of("nope")


# ======================================================================
# 值对象语义
# ======================================================================


def test_config_is_frozen(cwd):
    config = load_config(cwd=cwd, env={})
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.port = 1  # type: ignore[misc]


def test_ocr_config_is_immutable(cwd):
    config = load_config(cwd=cwd, env={}, cli_overrides={"ocr_config": {"kind": "mock"}})
    with pytest.raises(TypeError):
        config.ocr_config["kind"] = "local"  # type: ignore[index]


def test_ocr_config_is_a_copy_of_the_input(tmp_path, cwd):
    source = {"kind": "mock"}
    path = _write_config(str(tmp_path), {"ocr_config": source})
    config = load_config(config_path=path, cwd=cwd, env={})
    source["kind"] = "local"
    assert config.ocr_config["kind"] == "mock"


def test_sources_do_not_affect_equality(cwd):
    first = load_config(cwd=cwd, env={"CLASSROOM_PORT": "1"})
    second = load_config(cwd=cwd, env={}, cli_overrides={"port": 1})
    assert first == second
    assert first.source_of("port") is not second.source_of("port")


def test_with_overrides_returns_a_new_validated_config(cwd):
    config = load_config(cwd=cwd, env={})
    changed = config.with_overrides(port=9999)
    assert changed.port == 9999
    assert config.port == DEFAULT_PORT
    assert changed.source_of("port") is ConfigSource.OVERRIDE


def test_with_overrides_validates(cwd):
    config = load_config(cwd=cwd, env={})
    with pytest.raises(ConfigurationError):
        config.with_overrides(port=-5)


def test_with_overrides_rejects_unknown_keys(cwd):
    config = load_config(cwd=cwd, env={})
    with pytest.raises(ConfigurationError):
        config.with_overrides(prot=1)


def test_with_overrides_with_no_arguments_is_identity(cwd):
    config = load_config(cwd=cwd, env={})
    assert config.with_overrides() is config


def test_with_overrides_preserves_metadata(cwd):
    config = load_config(cwd=cwd, env={"CLASSROOM_PROT": "x"})
    changed = config.with_overrides(port=1)
    assert changed.unknown_env_vars == config.unknown_env_vars
    assert changed.config_path == config.config_path


def test_validate_returns_self(cwd):
    config = load_config(cwd=cwd, env={})
    assert config.validate() is config


# ======================================================================
# 输出
# ======================================================================


def test_to_dict_is_json_serialisable(cwd):
    config = load_config(cwd=cwd, env={}, cli_overrides={"ocr_config": {"kind": "mock"}})
    blob = json.dumps(config.to_dict(), ensure_ascii=False)
    assert "data_dir" in blob


def test_to_dict_includes_metadata_by_default(cwd):
    config = load_config(cwd=cwd, env={})
    payload = config.to_dict()
    assert "sources" in payload
    assert "config_path" in payload
    assert "unknown_env_vars" in payload


def test_to_dict_can_exclude_metadata(cwd):
    config = load_config(cwd=cwd, env={})
    payload = config.to_dict(include_metadata=False)
    assert "sources" not in payload
    assert set(payload) == set(CONFIGURABLE_KEYS)


def test_public_dict_equals_to_dict(cwd):
    config = load_config(cwd=cwd, env={})
    assert config.public_dict() == config.to_dict()


def test_describe_lists_every_key_and_its_source(cwd):
    config = load_config(cwd=cwd, env={}, cli_overrides={"port": 1234})
    text = config.describe()
    for key in CONFIGURABLE_KEYS:
        assert key in text, key
    assert "[cli]" in text
    assert "[default]" in text


def test_describe_mentions_ignored_env_vars(cwd):
    config = load_config(cwd=cwd, env={"CLASSROOM_NOPE": "1"})
    assert "CLASSROOM_NOPE" in config.describe()


def test_describe_mentions_the_config_path(tmp_path, cwd):
    path = _write_config(str(tmp_path), {"port": 1})
    config = load_config(config_path=path, cwd=cwd, env={})
    # describe() 用 repr() 输出路径 (Windows 上反斜杠会被转义), 因此按 repr 比较。
    assert repr(os.path.abspath(path)) in config.describe()


def test_default_log_level_constant_is_valid():
    assert DEFAULT_LOG_LEVEL in LOG_LEVELS


def test_the_application_package_reexports_the_config_surface():
    """配置是新的一层公开能力, 应该能从 src.application 直接拿到。"""
    import src.application as application

    assert application.AppConfig is AppConfig
    assert application.load_config is load_config
    assert application.ConfigSource is ConfigSource
    for name in ("AppConfig", "ConfigSource", "load_config", "configure_logging", "log_event"):
        assert name in application.__all__, name


def test_the_application_package_does_not_reexport_bootstrap():
    """bootstrap 会 import src.api, 再导出它就会形成 import 环。"""
    import src.application as application

    assert "bootstrap" not in application.__all__
    assert not hasattr(application, "build_runtime")
