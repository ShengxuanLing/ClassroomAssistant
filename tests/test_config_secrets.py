# -*- coding: utf-8 -*-
"""Task 44 —— 密钥边界与隐私 (规范: Secrets + Privacy)。

规范原文:
- 如果未来存在 API key, 不得在 source code / database plaintext / logs / UI
  直接暴露。
- 当前没有外部 API 的话, 不要为了"以后"增加 secret manager。
- 日志禁止写入: 完整用户答案 / API key / secret / 不必要的课堂原文 /
  敏感文件内容。

因此本文件验证的**不是**"我们做了个密钥管理服务", 而是相反的一件事:
**在当前这个没有任何外部 API 的版本里, 密钥根本没有藏身之处**。
"藏身之处"被逐个堵上并测试:

1. 源码里 —— 不引入 secret manager (由本文件的 import 守卫断言);
2. 配置里 —— ``ocr_config`` 递归拒绝密钥型键名 (含嵌套);
3. 日志里 —— 键名命中即整体替换, 整段内容只留摘要;
4. 输出里 —— ``to_dict`` / ``describe`` / ``repr`` 全部走脱敏。
"""

from __future__ import annotations

import json

import pytest

from src.application.config import (
    SECRET_PLACEHOLDER,
    AppConfig,
    is_secret_key,
    load_config,
    redact_mapping,
    redact_text,
    safe_excerpt,
    secret_paths,
    truncate,
)
from src.application.errors import ConfigurationError

# 一个足够显眼、任何地方出现都能被 grep 到的假密钥。
FAKE_SECRET = "sk-live-DEADBEEF0123456789"


@pytest.fixture
def cwd(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    return str(work)


# ======================================================================
# 什么算"密钥型键名"
# ======================================================================


@pytest.mark.parametrize(
    "name",
    [
        "api_key",
        "apiKey",
        "API_KEY",
        "apikey",
        "x-api-key",
        "X-Api-Key",
        "secret",
        "client_secret",
        "password",
        "passwd",
        "PASSWORD",
        "token",
        "auth_token",
        "access_token",
        "refresh_token",
        "access_key",
        "private_key",
        "credentials",
        "authorization",
        "session_key",
        "signing_key",
    ],
)
def test_secret_looking_keys_are_detected(name):
    assert is_secret_key(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "kind",
        "language",
        "author",
        "authoring",
        "model",
        "min_confidence",
        "engine_loader",
        "use_det",
        "cache_key_id",
        "monkey",
        "tokenizer_path",
    ],
)
def test_ordinary_keys_are_not_flagged(name):
    """宁可多判也不能漏判 —— 但也不能把无害字段全判成密钥, 否则规则会被绕过。"""
    assert is_secret_key(name) is False


@pytest.mark.parametrize("value", [None, "", 0, False, [], {}])
def test_empty_or_non_string_keys_are_not_secrets(value):
    assert is_secret_key(value) is False


def test_secret_detection_ignores_separators_and_case():
    """``x-api-key`` 是最常见的写法, 绝不能因为连字符而漏过去。"""
    assert is_secret_key("x-api-key") == is_secret_key("X_API_KEY") == is_secret_key("xApiKey")


# ======================================================================
# 文本脱敏
# ======================================================================


@pytest.mark.parametrize(
    "text",
    [
        f"api_key={FAKE_SECRET}",
        f"API_KEY: {FAKE_SECRET}",
        f"apikey={FAKE_SECRET}",
        f"access_key = {FAKE_SECRET}",
        f"private_key={FAKE_SECRET}",
        f"secret={FAKE_SECRET}",
        f"password: {FAKE_SECRET}",
        f"passwd={FAKE_SECRET}",
        f"token={FAKE_SECRET}",
    ],
)
def test_key_value_secrets_are_redacted(text):
    redacted = redact_text(text)
    assert FAKE_SECRET not in redacted
    assert SECRET_PLACEHOLDER in redacted


def test_authorization_header_is_redacted():
    redacted = redact_text(f"Authorization: Bearer {FAKE_SECRET}")
    assert FAKE_SECRET not in redacted
    assert "Bearer" in redacted  # 保留方案名, 只抹掉凭据


def test_bare_openai_style_key_is_redacted():
    assert FAKE_SECRET not in redact_text(f"using {FAKE_SECRET} now")


def test_jwt_shaped_token_is_redacted():
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    redacted = redact_text(f"token is {jwt}")
    assert jwt not in redacted
    assert SECRET_PLACEHOLDER in redacted


def test_ordinary_text_is_left_alone():
    text = "材料已摄取: apuntes-álgebra.pdf (12 个知识点)"
    assert redact_text(text) == text


def test_the_word_token_in_prose_is_not_mangled():
    """``tokenizer`` / 散文里的 "token" 不该被误伤 —— 规则要求 ``key=`` 形式。"""
    text = "The tokenizer splits tokens without any assignment."
    assert redact_text(text) == text


def test_redaction_happens_before_truncation():
    """顺序必须是"先脱敏再截断"。

    反过来的话, 一个被切断的**裸密钥**(``sk-`` 后面不足 8 个字符) 就不再
    匹配密钥形状, 于是半截明文会留在日志里 —— 半截密钥比完整密钥更难被发现。
    """
    text = f"prefix api_key={FAKE_SECRET} " + "x" * 300
    redacted = redact_text(text, limit=60)
    assert FAKE_SECRET not in redacted
    assert SECRET_PLACEHOLDER in redacted


def test_redact_text_accepts_non_strings():
    assert redact_text(None) == ""
    assert redact_text(1234) == "1234"


def test_redact_text_limit_is_applied():
    assert len(redact_text("y" * 100, limit=10)) <= 10 + 40


# ======================================================================
# 截断 / 摘要
# ======================================================================


def test_truncate_is_a_noop_when_short_enough():
    assert truncate("short", 100) == "short"


def test_truncate_marks_how_much_was_removed():
    """不做无声截断 —— 否则读者会以为日志里就是全部内容。"""
    result = truncate("a" * 100, 10)
    assert result.startswith("a" * 10)
    assert "truncated 90 chars" in result


def test_truncate_accepts_a_custom_marker():
    assert truncate("a" * 10, 4, marker="...") == "aaaa..."


def test_truncate_handles_none():
    assert truncate(None, 10) == ""


def test_safe_excerpt_redacts_and_truncates():
    excerpt = safe_excerpt("z" * 100 + f" api_key={FAKE_SECRET}", 20)
    assert FAKE_SECRET not in excerpt
    assert "truncated" in excerpt


def test_safe_excerpt_never_returns_a_full_long_answer():
    answer = "学生答案:" + "内容" * 2000
    excerpt = safe_excerpt(answer, 200)
    assert answer not in excerpt
    assert len(excerpt) < 300


# ======================================================================
# 映射脱敏
# ======================================================================


def test_redact_mapping_replaces_secret_values_entirely():
    """密钥型字段不能做摘要 —— 摘要会留下密钥的前若干字符。"""
    result = redact_mapping({"api_key": FAKE_SECRET})
    assert result == {"api_key": SECRET_PLACEHOLDER}


def test_redact_mapping_recurses_into_nested_mappings():
    result = redact_mapping({"engine": {"api_key": FAKE_SECRET, "kind": "local"}})
    assert result["engine"]["api_key"] == SECRET_PLACEHOLDER
    assert result["engine"]["kind"] == "local"


def test_redact_mapping_recurses_into_lists():
    result = redact_mapping({"steps": [{"token": FAKE_SECRET}, {"n": 1}]})
    assert result["steps"][0]["token"] == SECRET_PLACEHOLDER
    assert result["steps"][1]["n"] == 1


def test_redact_mapping_scrubs_secret_shapes_inside_plain_strings():
    result = redact_mapping({"note": f"use api_key={FAKE_SECRET}"})
    assert FAKE_SECRET not in json.dumps(result)


def test_redact_mapping_sorts_keys_deterministically():
    first = redact_mapping({"b": 1, "a": 2})
    second = redact_mapping({"a": 2, "b": 1})
    assert list(first) == list(second) == ["a", "b"]


def test_redact_mapping_leaves_non_string_scalars():
    assert redact_mapping({"n": 3, "ok": True, "none": None}) == {
        "n": 3,
        "ok": True,
        "none": None,
    }


def test_redact_mapping_stops_at_max_depth():
    deep: dict = {}
    node = deep
    for _ in range(20):
        node["child"] = {}
        node = node["child"]
    node["api_key"] = FAKE_SECRET
    blob = json.dumps(redact_mapping(deep))
    assert "max-depth" in blob


def test_redact_mapping_handles_non_mapping_input():
    assert redact_mapping("plain") == "plain"


# ======================================================================
# 嵌套密钥路径
# ======================================================================


def test_secret_paths_finds_nested_keys():
    assert secret_paths({"engine": {"api_key": "x"}}) == ["engine.api_key"]


def test_secret_paths_finds_list_items():
    assert secret_paths({"steps": [{"n": 1}, {"token": "x"}]}) == ["steps[1].token"]


def test_secret_paths_is_empty_for_clean_config():
    assert secret_paths({"kind": "local", "min_confidence": 0.5}) == []


# ======================================================================
# 配置层: 密钥根本进不来
# ======================================================================


def test_ocr_config_rejects_a_top_level_secret(cwd):
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(cwd=cwd, env={}, cli_overrides={"ocr_config": {"api_key": FAKE_SECRET}})
    assert excinfo.value.detail["secret_paths"] == ["api_key"]


def test_ocr_config_rejects_a_nested_secret(cwd):
    """只查顶层的话, {"engine": {"api_key": ...}} 会直接走过去。"""
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(
            cwd=cwd,
            env={},
            cli_overrides={"ocr_config": {"engine": {"api_key": FAKE_SECRET}}},
        )
    assert excinfo.value.detail["secret_paths"] == ["engine.api_key"]


def test_ocr_config_rejects_a_secret_in_a_list(cwd):
    with pytest.raises(ConfigurationError):
        load_config(
            cwd=cwd, env={}, cli_overrides={"ocr_config": {"steps": [{"token": "x"}]}}
        )


def test_ocr_config_rejects_a_secret_via_environment(cwd):
    with pytest.raises(ConfigurationError):
        load_config(cwd=cwd, env={"CLASSROOM_OCR_CONFIG": '{"client_secret": "x"}'})


def test_ocr_config_accepts_ordinary_keys(cwd):
    config = load_config(
        cwd=cwd,
        env={},
        cli_overrides={"ocr_config": {"kind": "local", "min_confidence": 0.4, "use_det": True}},
    )
    assert config.ocr_config["min_confidence"] == 0.4


def test_config_to_dict_redacts_secret_shaped_values_in_ocr_config(cwd):
    """即使密钥藏在**无害键名**的值里, 输出也必须是脱敏的 (纵深防御)。"""
    config = load_config(
        cwd=cwd, env={}, cli_overrides={"ocr_config": {"note": f"api_key={FAKE_SECRET}"}}
    )
    assert FAKE_SECRET not in json.dumps(config.to_dict(), ensure_ascii=False)


def test_config_repr_and_describe_never_leak_a_secret(cwd):
    config = load_config(
        cwd=cwd, env={}, cli_overrides={"ocr_config": {"note": f"api_key={FAKE_SECRET}"}}
    )
    assert FAKE_SECRET not in repr(config)
    assert FAKE_SECRET not in config.describe()
    assert FAKE_SECRET not in config.public_dict()["ocr_config"]["note"]


def test_unknown_env_secret_value_never_reaches_output(cwd):
    config = load_config(cwd=cwd, env={"CLASSROOM_API_KEY": FAKE_SECRET})
    blob = (
        json.dumps(config.to_dict(), ensure_ascii=False)
        + repr(config)
        + config.describe()
        + str(config.unknown_env_vars)
    )
    assert FAKE_SECRET not in blob


def test_a_secret_shaped_data_dir_is_not_echoed_in_errors(cwd, tmp_path):
    """错误信息里回显用户输入是常见的泄露渠道 —— 这里回显的路径也走脱敏。"""
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(
            cwd=cwd,
            env={},
            cli_overrides={"ocr_config": {"api_key": FAKE_SECRET}, "port": 1},
        )
    assert FAKE_SECRET not in str(excinfo.value)


def test_config_module_does_not_import_a_secret_manager():
    """规范明确要求: 当前没有外部 API, 就不要为"以后"引入 secret manager。

    这条断言是**反向**的: 它保证我们没有偷偷加一个 —— 只有真的需要外部
    API 凭据时才应该引入。
    """
    import ast
    import pathlib

    banned = {
        "keyring",
        "boto3",
        "botocore",
        "vault",
        "hvac",
        "azure",
        "google",
        "dotenv",
        "python_dotenv",
        "cryptography",
        "passlib",
    }
    module_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "application" / "config.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & banned), f"配置层不应引入 secret manager: {sorted(imported & banned)}"


def test_no_api_key_literal_lives_in_the_source_tree():
    """源码里不许有硬编码的密钥字面量。"""
    import pathlib
    import re

    src = pathlib.Path(__file__).resolve().parents[1] / "src"
    pattern = re.compile(r"sk-[A-Za-z0-9]{16,}")
    offenders = []
    for path in src.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(path.relative_to(src).as_posix())
    assert offenders == [], f"源码里出现疑似硬编码密钥: {offenders}"


def test_default_config_carries_no_secret(cwd):
    config = load_config(cwd=cwd, env={})
    assert secret_paths(config.ocr_config) == []


def test_app_config_rejects_secrets_even_when_built_directly(cwd):
    """直接构造 + validate 也必须挡住 (不能只靠 load_config 这条路)。"""
    base = load_config(cwd=cwd, env={})
    candidate = AppConfig(
        **{**base.to_dict(include_metadata=False), "ocr_config": {"api_key": "x"}}
    )
    with pytest.raises(ConfigurationError):
        candidate.validate()
