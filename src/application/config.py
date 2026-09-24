# -*- coding: utf-8 -*-
"""应用配置 / 密钥边界 / 隐私原语 (Task 44)。

本模块把"开发时随手能跑"变成"稳定运行环境", 只做三件事:

1. **唯一配置对象** ``AppConfig`` —— 所有可调项在这里有且只有一处定义。
2. **明确的优先级** ``defaults -> config file -> environment -> CLI``,
   并且**记录每个值来自哪一层** (``AppConfig.sources``)。
3. **密钥与隐私的唯一定义** —— 什么算 secret、日志里该怎么脱敏。

优先级规则 (逐条可测)
--------------------------------------------------------------------

- 层内**出现的键 = 该层显式指定**; 未出现的键 = 该层不管 (继续沿用下层)。
  因此 ``{"whisper_language": null}`` 是"显式置空", 与"没写这个键"不同。
- 高优先级层覆盖低优先级层, 逐键覆盖 (不是整层替换)。
- ``config file`` 与 ``CLI`` 里出现未知键 -> **硬错误**。理由: 这两个来源是
  我们自己解析的, 拼错键名却静默忽略, 会变成"我明明配了却没生效"。
- ``environment`` 里出现未知的 ``CLASSROOM_*`` -> **不报错, 但记入
  ``unknown_env_vars`` (只记名字, 绝不记值)**。理由: 环境变量是继承来的
  共享命名空间, 无法区分"拼错"与"别的工具/旧版本留下的"; 但把它暴露出来,
  拼错 ``CLASSROOM_PROT`` 就不会再悄无声息。
- ``database_path`` 若没有任何一层指定, 则由 ``data_dir`` **派生**
  (来源标记为 ``DERIVED``), 而不是硬编码一个默认绝对路径。
- 配置文件位置: ``--config`` / ``CLASSROOM_CONFIG`` 显式指定 (必须存在),
  否则取**工作目录**下的 ``classroom-assistant.json``。
  相对路径 (含 ``--data-dir``) 一律按**进程工作目录**解析 —— 标准 CLI 行为,
  不因为传入 ``cwd`` 参数而改变 (``cwd`` 只用于定位默认 data_dir 与配置文件,
  它是测试与嵌入调用的接缝)。

分层与 import 方向 (重要)
--------------------------------------------------------------------

本模块**不** import ``src.api`` / ``src.backup``。理由不是洁癖, 而是:

- ``src.api.server`` 依赖 ``src.application.workspace``, 应用层反向 import
  它就会形成环 (``src.application.__init__`` -> bootstrap -> src.api ->
  src.application.workspace)。所以 ``DEFAULT_HOST`` / ``DEFAULT_PORT`` 在这里
  **复述**一遍。
- ``src.backup`` 依赖 ``src.persistence``; 配置层一旦 import 它, 就会经由它
  把 ``src.persistence`` 拉进应用层, 触发分层守卫
  (``tests/test_persistence_layering.py``)。所以数据库文件名也在这里复述。

复述的常量由**测试**断言与源头相等
(``tests/test_config.py::test_config_defaults_match_their_sources``),
即"单一真相由测试保证, 而不是靠 import 方向"。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, MutableMapping, Optional, Sequence

from src.application.errors import ConfigurationError
from src.whisper_provider import (
    DEFAULT_WHISPER_MODEL,
    WhisperConfig,
)
from src.asr_provider import ASRConfigurationError

__all__ = [
    # 常量
    "DEFAULT_DATA_DIR_NAME",
    "TEST_DATA_DIR_NAME",
    "DATA_DIR_PROFILES",
    "DATA_PROFILE_ENV_VAR",
    "default_data_dir_name",
    "resolve_data_dir",
    "DEFAULT_CONFIG_FILENAME",
    "CONFIG_FILE_ENV_VAR",
    "ENV_PREFIX",
    "ENV_VARS",
    "ENV_FILE_KEYS",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_MAX_UPLOAD_SIZE",
    "DEFAULT_WHISPER_DEVICE",
    "DEFAULT_WHISPER_COMPUTE_TYPE",
    "DEFAULT_ASR_MODE",
    "DEFAULT_OCR_KIND",
    "DEFAULT_LLM_API_BASE",
    "DEFAULT_LLM_MODEL",
    "DEFAULT_DATABASE_FILENAME",
    "DEFAULT_LOG_LEVEL",
    "LOG_LEVELS",
    "ASR_MODES",
    "OCR_KINDS",
    "LOOPBACK_HOSTS",
    "CONFIGURABLE_KEYS",
    # 密钥 / 隐私
    "SECRET_PLACEHOLDER",
    "is_secret_key",
    "secret_paths",
    "redact_text",
    "redact_mapping",
    "truncate",
    "safe_excerpt",
    # 配置
    "ConfigSource",
    "AppConfig",
    "default_values",
    "read_config_file",
    "read_environment",
    "normalise_overrides",
    "discover_config_file",
    "load_config",
    "load_local_environment",
]

# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------

#: 默认数据根目录名 (相对启动时的工作目录, 不用 home 目录以免"文件去哪了")。
#: 这个目录就是**真实 Pilot / 生产**数据所在地。
DEFAULT_DATA_DIR_NAME = "classroom-data"

#: 测试专用数据目录名。pytest / stress test / UI audit / backup drill 一律解析到
#: 这里 (或显式传入的临时目录), 与真实 Pilot 数据 (``classroom-data``) 彻底分离
#: (Task 71.2)。
TEST_DATA_DIR_NAME = "data-test"

#: 数据目录"画像" -> 目录名。``pilot`` 是真实使用, ``test`` 是测试隔离。
DATA_DIR_PROFILES: dict[str, str] = {
    "pilot": DEFAULT_DATA_DIR_NAME,
    "test": TEST_DATA_DIR_NAME,
}

#: 选择数据目录画像的环境变量 (``CLASSROOM_DATA_DIR`` 优先级更高, 因为它直接
#: 指定了绝对/相对路径)。
DATA_PROFILE_ENV_VAR = "CLASSROOM_DATA_PROFILE"

#: 默认配置文件名 (在 data_dir 下自动发现)。
DEFAULT_CONFIG_FILENAME = "classroom-assistant.json"

#: 指向配置文件的环境变量 (它本身不是配置项)。
CONFIG_FILE_ENV_VAR = "CLASSROOM_CONFIG"

#: 环境变量前缀。
ENV_PREFIX = "CLASSROOM_"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_MAX_UPLOAD_SIZE = 200 * 1024 * 1024
DEFAULT_WHISPER_DEVICE = "cpu"
DEFAULT_WHISPER_COMPUTE_TYPE = "int8"
DEFAULT_ASR_MODE = "auto"
DEFAULT_OCR_KIND = "auto"

#: §0 默认值: API base URL 未给时用占位, health 只断形状不断连通。
DEFAULT_LLM_API_BASE = "https://LLM_API_BASE/v1"

#: §0 默认值: model 未给时用占位, 经 CLASSROOM_LLM_MODEL 可覆写。
DEFAULT_LLM_MODEL = "llm-model-default"
DEFAULT_DATABASE_FILENAME = "classroom.sqlite"
DEFAULT_LOG_LEVEL = "INFO"

#: 规范点名的 5 个日志级别 (Task 44 Logging)。
LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

#: ASR 运行模式。``auto`` = 有真实运行时就用真实, 否则回落 Mock **并显式可见**。
ASR_MODES: tuple[str, ...] = ("auto", "real", "mock")

#: OCR provider 类型 (与 ``create_ocr_provider`` 的 kind 参数一致)。
OCR_KINDS: tuple[str, ...] = ("auto", "local", "mock")

#: 允许默认监听的地址。其它地址必须显式 ``allow_remote=True``。
LOOPBACK_HOSTS: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")

#: 环境变量 -> 配置键 (唯一映射表)。
ENV_VARS: dict[str, str] = {
    "CLASSROOM_DATA_DIR": "data_dir",
    "CLASSROOM_DATABASE_PATH": "database_path",
    "CLASSROOM_HOST": "host",
    "CLASSROOM_PORT": "port",
    "CLASSROOM_MAX_UPLOAD_SIZE": "max_upload_size",
    "CLASSROOM_WHISPER_MODEL": "whisper_model",
    "CLASSROOM_WHISPER_DEVICE": "whisper_device",
    "CLASSROOM_WHISPER_COMPUTE_TYPE": "whisper_compute_type",
    "CLASSROOM_WHISPER_LANGUAGE": "whisper_language",
    "CLASSROOM_ASR_MODE": "asr_mode",
    "CLASSROOM_OCR_CONFIG": "ocr_config",
    "CLASSROOM_LOG_LEVEL": "log_level",
    "CLASSROOM_DEBUG": "debug",
    "CLASSROOM_ALLOW_REMOTE": "allow_remote",
    "CLASSROOM_LLM_API_KEY": "llm_api_key",
    "CLASSROOM_LLM_API_BASE": "llm_api_base",
    "CLASSROOM_LLM_MODEL": "llm_model",
}

#: §0 环境变量名 (LLM 出站三件套, 固定这三个)。
LLM_ENV_VARS: tuple[str, ...] = (
    "CLASSROOM_LLM_API_KEY",
    "CLASSROOM_LLM_API_BASE",
    "CLASSROOM_LLM_MODEL",
)

#: ``llm_api_key`` 只允许来自进程环境变量, 禁止出现在配置文件 / CLI /
#: with_overrides 里 (密钥只读进程环境, 禁写配置文件/数据库)。
ENV_FILE_KEYS: frozenset[str] = frozenset({"llm_api_key"})

#: 允许出现在配置文件 / CLI / 环境变量里的配置键 (config_path 是元数据, 不算)。
CONFIGURABLE_KEYS: frozenset[str] = frozenset(ENV_VARS.values())

#: 上传大小的下界 —— 低于 1 KiB 几乎必然是"把 MB 当成了字节"。
_MIN_PLAUSIBLE_UPLOAD_SIZE = 1024

#: 项目内**绝不允许**作为 data_dir 的目录名 (AGENTS.md 硬性规则的可执行版本)。
_FORBIDDEN_DATA_DIR_NAMES: tuple[str, ...] = ("src", "tests")

# ----------------------------------------------------------------------
# 本地启动环境加载
# ----------------------------------------------------------------------


def _parse_local_environment_file(path: str) -> dict[str, str]:
    """Parse a small, deliberately narrow env-file subset.

    This parser never raises and never includes a value in an error/log.  It
    accepts ordinary ``KEY=VALUE`` lines as well as the ``set "KEY=VALUE"``
    form used by ``scripts/ai-env.bat``.  Quoted values are unwrapped; no
    interpolation, command execution, or variable expansion is performed.
    """
    values: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            lines = handle.readlines()
    except (OSError, UnicodeError):
        return values

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("REM "):
            continue
        if line.lower().startswith("set "):
            line = line[4:].strip()
        elif line.lower().startswith("set"):
            # Ignore setlocal/endlocal and other batch directives.
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if len(line) >= 2 and line[0] == line[-1] and line[0] in ("'", '"'):
            line = line[1:-1]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not key.startswith(ENV_PREFIX):
            continue
        # Only inject runtime AI/LLM settings.  Other CLASSROOM_* values
        # remain the responsibility of the explicit config/CLI layers, and a
        # local helper file must not unexpectedly relocate the data directory.
        if not (key.startswith("CLASSROOM_AI_") or key.startswith("CLASSROOM_LLM_")):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def load_local_environment(
    project_root: Optional[str] = None,
    *,
    env: Optional[MutableMapping[str, str]] = None,
) -> tuple[str, ...]:
    """Load ignored local AI env files into the process environment.

    ``.env`` is preferred over ``scripts/ai-env.bat`` when both define a
    variable, and an already-present process variable always wins.  The
    returned tuple contains file paths only -- never parsed values -- so it is
    safe to use in diagnostics.  This function is intentionally a startup
    convenience, not a persistence mechanism: it does not write files, logs,
    responses, or database rows.

    Callers running under pytest should skip this helper (the test suite must
    never inherit a developer's real credentials).  Production CLI/launcher
    entry points call it before configuration/runtime assembly.
    """
    target: MutableMapping[str, str] = os.environ if env is None else env
    root = os.path.abspath(
        project_root
        or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    candidates = (
        os.path.join(root, ".env"),
        os.path.join(root, "scripts", "ai-env.bat"),
    )
    loaded: list[str] = []
    for path in candidates:
        if not os.path.isfile(path):
            continue
        parsed = _parse_local_environment_file(path)
        changed = False
        for key, value in parsed.items():
            # Process environment has precedence over both local files.  The
            # first file also wins over the legacy batch fallback.
            if key not in target:
                target[key] = value
                changed = True
        if changed:
            loaded.append(path)
    return tuple(loaded)


# ----------------------------------------------------------------------
# 密钥与隐私原语 (规范: Secrets + Privacy)
# ----------------------------------------------------------------------
#
# 为什么放在配置模块里: 规范的 "Secrets" 与 "Privacy" 是同一个要求的两面
# ——"什么东西算 secret"。把这份定义放在一处, 才不会出现"配置层认得 api_key
# 但日志层不认得"这种半吊子脱敏。

#: 单独一个词就足够判定为密钥的词。
_SECRET_WORDS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "secrets",
        "token",
        "tokens",
        "credential",
        "credentials",
        "authorization",
        "apikey",
    }
)

#: 需要**相邻两个词**才判定为密钥的组合 (避免 ``cache_key`` 之类被误判)。
_SECRET_PAIRS: tuple[tuple[str, str], ...] = (
    ("api", "key"),
    ("access", "key"),
    ("private", "key"),
    ("session", "key"),
    ("signing", "key"),
    ("secret", "key"),
)

#: 脱敏后的占位符 (保留键名, 只替换值, 便于定位问题)。
SECRET_PLACEHOLDER = "<redacted>"

#: 驼峰 / 连续大写 -> 分词边界。
_WORD_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_WORD = re.compile(r"[^A-Za-z0-9]+")

#: 文本中的密钥形状。每条规则把值放在最后一个分组 (或整体替换)。
_REDACTION_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)\b(api[_-]?key|apikey|access[_-]?key|private[_-]?key"
            r"|secret|password|passwd|token)\b(\s*[:=]\s*)(\S+)"
        ),
        r"\1\2" + SECRET_PLACEHOLDER,
    ),
    (
        re.compile(r"(?i)\b(authorization)\b(\s*[:=]\s*)(bearer|basic|token)\s+\S+"),
        r"\1\2\3 " + SECRET_PLACEHOLDER,
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"), SECRET_PLACEHOLDER),
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}\b"),
        SECRET_PLACEHOLDER,
    ),
)


def _key_words(name: Any) -> list[str]:
    """把键名切成小写词: ``x-api-key`` / ``apiKey`` / ``API_KEY`` -> [api, key]。"""
    spaced = _WORD_BOUNDARY.sub(" ", str(name))
    return [word for word in _NON_WORD.split(spaced.lower()) if word]


def is_secret_key(name: Any) -> bool:
    """键名是否属于密钥型。

    判定基于**词**, 而不是子串 —— 这是修正过的一版: 子串匹配会把
    ``tokenizer_path`` (Whisper 里非常正常的一个配置项) 判成密钥, 于是
    一份合法配置会被直接拒绝。词级判定同时做到:

    - 认得 ``x-api-key`` / ``apiKey`` / ``API_KEY`` / ``apikey`` 这些写法;
    - 不误伤 ``tokenizer_path`` / ``author`` / ``cache_key_id`` / ``monkey``。
    """
    words = _key_words(name)
    if not words:
        return False
    if any(word in _SECRET_WORDS for word in words):
        return True
    return any(pair in zip(words, words[1:]) for pair in _SECRET_PAIRS)


def truncate(value: Any, limit: int, *, marker: Optional[str] = None) -> str:
    """截断文本, 并**显式标注**截掉了多少 —— 不做无声截断。"""
    text = "" if value is None else str(value)
    if limit is None or len(text) <= limit:
        return text
    suffix = marker if marker is not None else f"...<truncated {len(text) - limit} chars>"
    return text[:limit] + suffix


def redact_text(value: Any, *, limit: Optional[int] = None) -> str:
    """抹掉文本里的密钥形状, 再按需截断。

    顺序很重要: **先脱敏再截断**。反过来的话, 一个刚好被切断的密钥会留下
    半截明文, 比完整密钥更难被发现。
    """
    text = "" if value is None else str(value)
    for pattern, replacement in _REDACTION_RULES:
        text = pattern.sub(replacement, text)
    if limit is not None:
        text = truncate(text, limit)
    return text


def safe_excerpt(value: Any, limit: int = 200) -> str:
    """课堂原文的安全摘要 (脱敏 + 截断)。

    日志里想引用一句课堂原文时用这个, 而**不是**直接把整段材料塞进去:
    规范禁止把"不必要的课堂原文"写进日志。
    """
    return truncate(redact_text(value), limit)


def secret_paths(
    value: Any, *, prefix: str = "", depth: int = 0, max_depth: int = 6
) -> list[str]:
    """列出映射 (含嵌套) 中所有"密钥型键名"的路径。

    为什么要递归: 只查顶层的话, ``{"engine": {"api_key": "..."}}`` 会从
    "配置里不许有密钥"这条规则下面直接走过去。路径形如
    ``engine.api_key`` / ``steps[0].token``, 便于把话说清楚。
    """
    found: list[str] = []
    if depth > max_depth:
        return found
    if isinstance(value, Mapping):
        for key in sorted(value, key=lambda item: str(item)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if is_secret_key(key):
                found.append(path)
            else:
                found.extend(
                    secret_paths(value[key], prefix=path, depth=depth + 1, max_depth=max_depth)
                )
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(
                secret_paths(
                    item, prefix=f"{prefix}[{index}]", depth=depth + 1, max_depth=max_depth
                )
            )
    return found


def redact_mapping(value: Any, *, depth: int = 0, max_depth: int = 6) -> dict[str, Any]:
    """递归脱敏映射 (键名命中密钥模式 -> 值整体替换; 字符串值 -> 抹密钥形状)。

    返回普通 dict 且键已排序, 保证输出确定性。递归有深度上限, 避免
    自引用结构把日志写死。
    """
    if depth > max_depth:
        return {"__truncated__": "max-depth"}
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key in sorted(value, key=lambda item: str(item)):
            if is_secret_key(key):
                out[str(key)] = SECRET_PLACEHOLDER
            else:
                out[str(key)] = redact_mapping(
                    value[key], depth=depth + 1, max_depth=max_depth
                )
        return out
    if isinstance(value, (list, tuple)):
        return [
            redact_mapping(item, depth=depth + 1, max_depth=max_depth) for item in value
        ]
    if isinstance(value, str):
        return redact_text(value)
    return value


# ----------------------------------------------------------------------
# 来源标记
# ----------------------------------------------------------------------


class ConfigSource(str, Enum):
    """一个配置值的来源 (优先级从低到高)。

    保留来源不是为了好看: 没有它, "优先级正确"就只能靠肉眼比对最终值,
    而"CLI 覆盖了环境变量"与"环境变量覆盖了配置文件"在最终值上可能长得
    一模一样。有了它, 测试可以直接断言"这个值来自哪一层"。
    """

    DEFAULT = "default"
    FILE = "file"
    ENV = "environment"
    CLI = "cli"
    #: 由其它字段派生 (当前只有 database_path)。
    DERIVED = "derived"
    #: 加载完成之后由调用方显式覆盖 (测试 / 编程式装配)。
    OVERRIDE = "override"


# ----------------------------------------------------------------------
# 默认值与逐层读取
# ----------------------------------------------------------------------


def default_data_dir_name() -> str:
    """根据运行画像选择默认数据目录名 (Task 71.2)。

    - 设了 ``CLASSROOM_DATA_DIR`` 时, 调用方应直接用那个显式路径, 本函数不参与;
    - 设了 ``CLASSROOM_DATA_PROFILE=test`` 时, 解析到 ``data-test`` (测试隔离);
    - 否则解析到 ``classroom-data`` (真实 Pilot / 生产数据)。
    """
    profile = os.environ.get(DATA_PROFILE_ENV_VAR, "").strip().lower()
    name = DATA_DIR_PROFILES.get(profile)
    if name is None:
        return DEFAULT_DATA_DIR_NAME
    return name


def resolve_data_dir(
    base: str,
    *,
    explicit: Optional[str] = None,
    profile: Optional[str] = None,
) -> str:
    """把一个"基准目录 + 画像"解析成具体数据目录 (Task 71.2)。

    优先级: 显式路径 > 命名画像 (``pilot`` / ``test``) > 默认画像。

    - ``explicit`` 非空 -> 原样返回 (调用方已决定路径);
    - ``profile == "test"`` -> ``<base>/data-test``;
    - ``profile == "pilot"`` 或无画像 -> ``<base>/classroom-data``。

    这样同一个代码库里, 真实使用与测试运行天然落到两个不相交的目录, 测试永远
    碰不到真实 Pilot 数据。
    """
    if explicit and explicit.strip():
        return os.path.abspath(explicit)
    chosen = DATA_DIR_PROFILES.get((profile or "").strip().lower())
    if chosen is None:
        chosen = default_data_dir_name()
    return os.path.join(os.path.abspath(base), chosen)


def default_values(cwd: Optional[str] = None) -> dict[str, Any]:
    """第 0 层: 默认值。

    ``data_dir`` 相对**启动时的工作目录**, 而不是用户 home 目录 ——
    本地单用户工具里, "数据就在我启动它的地方" 比 "藏在 home 里" 好排查。

    ``database_path`` 故意**不**在这里出现: 它由 ``data_dir`` 派生,
    来源会被标成 ``DERIVED``, 而不是伪装成一个默认值。
    """
    base = os.path.abspath(cwd or os.getcwd())
    return {
        "data_dir": os.path.join(base, default_data_dir_name()),
        "host": DEFAULT_HOST,
        "port": DEFAULT_PORT,
        "max_upload_size": DEFAULT_MAX_UPLOAD_SIZE,
        "whisper_model": DEFAULT_WHISPER_MODEL,
        "whisper_device": DEFAULT_WHISPER_DEVICE,
        "whisper_compute_type": DEFAULT_WHISPER_COMPUTE_TYPE,
        "whisper_language": None,
        "asr_mode": DEFAULT_ASR_MODE,
        "ocr_config": {"kind": DEFAULT_OCR_KIND},
        "log_level": DEFAULT_LOG_LEVEL,
        "debug": False,
        "allow_remote": False,
        # §1: llm_api_key 缺席即 None (与 whisper_language 同 pattern);
        # base/model 为占位默认值 (见 §0)。
        "llm_api_key": None,
        "llm_api_base": DEFAULT_LLM_API_BASE,
        "llm_model": DEFAULT_LLM_MODEL,
    }


_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "y", "t"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", "n", "f"})

_TEXT_KEYS = frozenset(
    {
        "data_dir",
        "database_path",
        "host",
        "whisper_model",
        "whisper_device",
        "whisper_compute_type",
        "asr_mode",
        "log_level",
        "llm_api_key",
        "llm_api_base",
        "llm_model",
    }
)
_INT_KEYS = frozenset({"port", "max_upload_size"})
_BOOL_KEYS = frozenset({"debug", "allow_remote"})


def _error(origin: str, key: str, message: str, **detail: Any) -> ConfigurationError:
    payload = {"key": key, "origin": origin}
    payload.update(detail)
    return ConfigurationError(f"{origin}: {message}", detail=payload)


def _as_text(key: str, value: Any, origin: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise _error(origin, key, f"{key} must be a string (got {value!r})")
    return value.strip()


def _as_int(key: str, value: Any, origin: str) -> int:
    if isinstance(value, bool):
        raise _error(origin, key, f"{key} must be an integer (got {value!r})")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        body = text[1:] if text[:1] in "+-" else text
        if text and body.isdigit():
            return int(text)
    raise _error(origin, key, f"{key} must be an integer (got {value!r})")


def _as_bool(key: str, value: Any, origin: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)
        raise _error(origin, key, f"{key} must be a boolean (got {value!r})")
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_VALUES:
            return True
        if text in _FALSE_VALUES:
            return False
    raise _error(origin, key, f"{key} must be a boolean like true/false (got {value!r})")


def _as_mapping(key: str, value: Any, origin: str) -> dict[str, Any]:
    """对象或 JSON 对象字符串 -> 普通 dict (环境变量/CLI 只能给字符串)。"""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str) or not raw_key.strip():
                raise _error(
                    origin, key, f"{key} keys must be non-empty strings (got {raw_key!r})"
                )
            out[str(raw_key)] = raw_value
        return out
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            raise _error(
                origin, key, f"{key} must be a JSON object (got {value!r}): {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise _error(
                origin,
                key,
                f"{key} must be a JSON object, not {type(parsed).__name__}",
            )
        return parsed
    raise _error(origin, key, f"{key} must be an object or a JSON object string")


def _coerce(key: str, value: Any, origin: str) -> Any:
    """把某一层的原始值转成该配置项的类型 (只做类型转换, 不做大小写规范化)。

    大小写规范化统一在 :meth:`AppConfig.__post_init__` 里做, 这样直接构造
    ``AppConfig`` 与经由 :func:`load_config` 的行为完全一致。
    """
    if key in _TEXT_KEYS:
        return _as_text(key, value, origin)
    if key == "whisper_language":
        # 唯一一个 Optional 字段: 显式 null 与空串都表示"不指定语言"。
        # 其它字段写 null 仍然是错误 —— 它们没有"空"这个合法状态。
        if value is None:
            return None
        text = _as_text(key, value, origin)
        return text.lower() or None
    if key in _INT_KEYS:
        return _as_int(key, value, origin)
    if key in _BOOL_KEYS:
        return _as_bool(key, value, origin)
    if key == "ocr_config":
        return _as_mapping(key, value, origin)
    raise _error(origin, key, f"unknown configuration key {key!r}")


# LLM 出站三件套在三层的归属: key 只读进程环境, base/model 允许三层。
_LLM_ENV_ONLY_KEYS: frozenset[str] = frozenset({"llm_api_key"})
_LLM_FILE_FORBIDDEN_KEYS: frozenset[str] = frozenset({"llm_api_key"})


def _check_known_keys(keys: Sequence[str], origin: str) -> None:
    unknown = sorted(key for key in keys if key not in CONFIGURABLE_KEYS)
    if unknown:
        raise ConfigurationError(
            f"{origin}: unknown configuration key(s) {unknown}; "
            f"known keys: {sorted(CONFIGURABLE_KEYS)}",
            detail={"unknown": unknown, "known": sorted(CONFIGURABLE_KEYS)},
        )


def read_config_file(path: str) -> dict[str, Any]:
    """第 1 层: 配置文件 (JSON)。

    只支持 JSON —— 一个格式, 标准库解析, 不存在"我改了但解析器读的是另一个
    分支"的可能。未知键是硬错误。
    """
    if not isinstance(path, str) or not path.strip():
        raise ConfigurationError("config file path must be a non-empty string")
    absolute = os.path.abspath(path)
    if not os.path.isfile(absolute):
        raise ConfigurationError(
            f"config file not found: {absolute}", detail={"path": absolute}
        )
    try:
        with open(absolute, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except ValueError as exc:
        raise ConfigurationError(
            f"config file is not valid JSON: {absolute}: {exc}",
            detail={"path": absolute},
            cause=exc,
        ) from exc
    except OSError as exc:
        raise ConfigurationError(
            f"config file could not be read: {absolute}: {exc}",
            detail={"path": absolute},
            cause=exc,
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(
            f"config file must contain a JSON object: {absolute}",
            detail={"path": absolute, "found": type(raw).__name__},
        )
    _check_known_keys(list(raw), f"config file {absolute}")
    forbidden = sorted(k for k in raw if k in _LLM_FILE_FORBIDDEN_KEYS)
    if forbidden:
        raise ConfigurationError(
            f"config file {absolute}: key(s) {forbidden} must only come from "
            "process environment variables (CLASSROOM_LLM_API_KEY); "
            "refusing to read a secret from a file",
            detail={"forbidden": forbidden},
        )
    origin = f"config file {absolute}"
    return {key: _coerce(key, value, origin) for key, value in raw.items()}


def read_environment(
    env: Optional[Mapping[str, str]] = None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """第 2 层: 环境变量。

    返回 ``(值, 未知变量名)``。未知变量**只记名字, 绝不记值** ——
    否则一个 ``CLASSROOM_API_KEY=...`` 就会被我们自己的诊断信息写进日志。
    """
    source: Mapping[str, str] = os.environ if env is None else env
    values: dict[str, Any] = {}
    unknown: list[str] = []
    for name in sorted(source):
        if not isinstance(name, str) or not name.startswith(ENV_PREFIX):
            continue
        if name == CONFIG_FILE_ENV_VAR:
            continue
        key = ENV_VARS.get(name)
        if key is None:
            unknown.append(name)
            continue
        raw = source[name]
        if raw is None:
            continue
        values[key] = _coerce(key, raw, f"environment variable {name}")
    return values, tuple(unknown)


def normalise_overrides(
    overrides: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """第 3 层: CLI 覆盖。

    ``None`` 表示"这个开关没给", 直接跳过 —— 所以 ``--debug`` / ``--no-debug``
    必须用 ``default=None`` 的 argparse 动作来区分"没给"与"给了 False"。
    """
    if not overrides:
        return {}
    _check_known_keys(list(overrides), "command line")
    forbidden = sorted(k for k in overrides if k in _LLM_ENV_ONLY_KEYS and overrides[k] is not None)
    if forbidden:
        raise ConfigurationError(
            f"command line: key(s) {forbidden} must only come from "
            "process environment variables (CLASSROOM_LLM_API_KEY)",
            detail={"forbidden": forbidden},
        )
    out: dict[str, Any] = {}
    for key in sorted(overrides):
        value = overrides[key]
        if value is None:
            continue
        out[key] = _coerce(key, value, f"command line --{key.replace('_', '-')}")
    return out


def discover_config_file(
    *,
    cwd: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    cli_overrides: Optional[Mapping[str, Any]] = None,
    explicit: Optional[str] = None,
) -> Optional[str]:
    """定位配置文件。

    规则 (确定性, 只有这两条):

    1. 显式指定 (``--config`` / ``CLASSROOM_CONFIG``) -> **必须存在**, 否则报错。
       显式点名一个不存在的文件却被静默忽略, 是最容易让人白查半天的一类 bug。
    2. 否则看工作目录下的 ``classroom-assistant.json``; 再没有就是没有。

    **为什么不在 data_dir 下找**: 配置文件的首要职责之一就是告诉程序
    ``data_dir`` 在哪 —— 再去 data_dir 里找配置文件, 在用户看来是个循环。
    "配置文件跟启动目录放一起"也最符合 Windows 上双击/批处理启动的直觉
    (Task 46 的 ``start.bat`` 就落在这个目录)。``cli_overrides`` 保留在签名
    里只是为了调用方不必分两处传参, 它**不影响**查找位置。
    """
    source: Mapping[str, str] = os.environ if env is None else env

    requested = explicit or source.get(CONFIG_FILE_ENV_VAR)
    if isinstance(requested, str) and requested.strip():
        absolute = os.path.abspath(requested.strip())
        if not os.path.isfile(absolute):
            raise ConfigurationError(
                f"requested config file does not exist: {absolute}",
                detail={"path": absolute},
            )
        return absolute

    candidate = os.path.join(
        os.path.abspath(cwd or os.getcwd()), DEFAULT_CONFIG_FILENAME
    )
    return candidate if os.path.isfile(candidate) else None


# ----------------------------------------------------------------------
# AppConfig
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class AppConfig:
    """应用配置 (值对象)。

    直接构造**不**做校验, 请用 :meth:`AppConfig.load` 或
    :meth:`with_overrides` (二者都会校验)。这样测试可以故意造出非法配置来
    验证校验逻辑本身。
    """

    data_dir: str
    database_path: str
    host: str
    port: int
    max_upload_size: int
    whisper_model: str
    whisper_device: str
    whisper_compute_type: str
    whisper_language: Optional[str]
    asr_mode: str
    ocr_config: Mapping[str, Any]
    log_level: str
    debug: bool
    allow_remote: bool
    llm_api_key: Optional[str] = None
    llm_api_base: str = DEFAULT_LLM_API_BASE
    llm_model: str = DEFAULT_LLM_MODEL
    #: 实际加载的配置文件 (None = 没有配置文件)。
    config_path: Optional[str] = None
    #: 每个配置值的来源。不参与相等比较 (它是元数据, 不是配置本身)。
    sources: Mapping[str, str] = field(default_factory=dict, compare=False, repr=False)
    #: 无法识别的 ``CLASSROOM_*`` 环境变量名 (只有名字)。
    unknown_env_vars: tuple[str, ...] = ()

    # ------------------------------------------------------------------

    def __post_init__(self) -> None:
        object.__setattr__(self, "ocr_config", MappingProxyType(dict(self.ocr_config or {})))
        object.__setattr__(self, "sources", dict(self.sources or {}))
        object.__setattr__(self, "unknown_env_vars", tuple(self.unknown_env_vars or ()))
        # 路径归一化 (确定性: 相对路径永远先变成绝对路径再比较/展示)。
        for name in ("data_dir", "database_path"):
            value = getattr(self, name)
            if isinstance(value, str) and value.strip():
                object.__setattr__(self, name, os.path.abspath(value.strip()))

        # 枚举型配置统一大小写, 让直接构造与 load_config 行为一致。
        for name in ("whisper_model", "whisper_device", "whisper_compute_type", "asr_mode"):
            value = getattr(self, name)
            if isinstance(value, str):
                object.__setattr__(self, name, value.strip().lower())
        for name in ("log_level", "host"):
            value = getattr(self, name)
            if isinstance(value, str):
                object.__setattr__(
                    self, name, value.strip().upper() if name == "log_level" else value.strip()
                )
        for name in ("llm_api_base", "llm_model"):
            value = getattr(self, name)
            if isinstance(value, str):
                object.__setattr__(self, name, value.strip())
        key = getattr(self, "llm_api_key", None)
        if isinstance(key, str):
            stripped = key.strip()
            object.__setattr__(self, "llm_api_key", stripped or None)

    # ------------------------------------------------------------------
    # 构造入口
    # ------------------------------------------------------------------

    @classmethod
    def load(
        cls,
        *,
        config_path: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        cli_overrides: Optional[Mapping[str, Any]] = None,
        cwd: Optional[str] = None,
    ) -> "AppConfig":
        """按规范优先级加载并校验配置。"""
        return load_config(
            config_path=config_path,
            env=env,
            cli_overrides=cli_overrides,
            cwd=cwd,
        )

    def with_overrides(self, **overrides: Any) -> "AppConfig":
        """返回覆盖若干项之后的**新**配置 (校验通过才返回)。

        覆盖后的来源标记为 ``OVERRIDE`` —— 不会假装自己来自配置文件。
        """
        if not overrides:
            return self
        _check_known_keys(list(overrides), "override")
        if any(k in _LLM_ENV_ONLY_KEYS and overrides.get(k) is not None for k in overrides):
            raise ConfigurationError(
                "override: llm_api_key must only come from "
                "process environment variables (CLASSROOM_LLM_API_KEY)",
                detail={"forbidden": ["llm_api_key"]},
            )
        current = self._writable_dict()
        current.update(
            {
                key: _coerce(key, value, f"override {key}")
                for key, value in overrides.items()
                if value is not None
            }
        )
        sources = dict(self.sources)
        for key in overrides:
            if overrides[key] is not None:
                sources[key] = ConfigSource.OVERRIDE.value
        return AppConfig(
            **current,
            config_path=self.config_path,
            sources=sources,
            unknown_env_vars=self.unknown_env_vars,
        ).validate()

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------

    def validate(self) -> "AppConfig":
        """校验全部字段; 不合法时抛 :class:`ConfigurationError`。"""
        self._validate_data_dir()
        self._validate_database_path()
        self._validate_host()
        self._validate_port()
        self._validate_max_upload_size()
        self._validate_whisper()
        self._validate_asr_mode()
        self._validate_ocr_config()
        self._validate_log_level()
        self._validate_llm()
        return self

    def _validate_data_dir(self) -> None:
        if not isinstance(self.data_dir, str) or not self.data_dir.strip():
            raise ConfigurationError("data_dir must be a non-empty string")
        root = os.path.abspath(self.data_dir)
        if os.path.isfile(root):
            raise ConfigurationError(
                f"data_dir is a file, not a directory: {root}", detail={"data_dir": root}
            )
        # AGENTS.md: "用户数据绝不写入 src/ 或 tests/" —— 把它变成可执行的检查,
        # 否则一次手滑 --data-dir 就可能把测试产物写进源码树。
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for name in _FORBIDDEN_DATA_DIR_NAMES:
            forbidden = os.path.join(project_root, name)
            if root == forbidden or root.startswith(forbidden + os.sep):
                raise ConfigurationError(
                    f"data_dir must not live inside the project's {name}/ directory: {root}",
                    detail={"data_dir": root, "forbidden": forbidden},
                )

    def _validate_database_path(self) -> None:
        if not isinstance(self.database_path, str) or not self.database_path.strip():
            raise ConfigurationError("database_path must be a non-empty string")
        root = os.path.abspath(self.data_dir)
        target = os.path.abspath(self.database_path)
        if target == root or not target.startswith(root + os.sep):
            raise ConfigurationError(
                "database_path must live inside data_dir "
                f"({target!r} is outside {root!r}); otherwise backup/restore "
                "would silently skip it",
                detail={"database_path": target, "data_dir": root},
            )
        if os.path.basename(target) in ("", ".", ".."):
            raise ConfigurationError(
                f"database_path must name a file: {target}", detail={"database_path": target}
            )

    def _validate_host(self) -> None:
        if not isinstance(self.host, str) or not self.host.strip():
            raise ConfigurationError("host must be a non-empty string")
        if self.host in LOOPBACK_HOSTS:
            return
        if not self.allow_remote:
            raise ConfigurationError(
                f"host {self.host!r} is not a loopback address; this is a local "
                "single-user application. Pass allow_remote=True "
                "(CLI: --allow-remote, env: CLASSROOM_ALLOW_REMOTE=1) to bind it anyway.",
                detail={"host": self.host, "allowed": list(LOOPBACK_HOSTS)},
            )

    def _validate_port(self) -> None:
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise ConfigurationError(f"port must be an integer (got {self.port!r})")
        if not 0 <= self.port <= 65535:
            raise ConfigurationError(
                f"port must be within 0..65535 (got {self.port}); 0 means "
                "'let the OS pick a free port'",
                detail={"port": self.port},
            )

    def _validate_max_upload_size(self) -> None:
        if isinstance(self.max_upload_size, bool) or not isinstance(self.max_upload_size, int):
            raise ConfigurationError(
                f"max_upload_size must be an integer (got {self.max_upload_size!r})"
            )
        if self.max_upload_size <= 0:
            raise ConfigurationError(
                f"max_upload_size must be positive (got {self.max_upload_size})",
                detail={"max_upload_size": self.max_upload_size},
            )
        if self.max_upload_size < _MIN_PLAUSIBLE_UPLOAD_SIZE:
            raise ConfigurationError(
                f"max_upload_size is in bytes, but {self.max_upload_size} is smaller "
                "than 1 KiB -- did you mean to write "
                f"{self.max_upload_size} * 1024 * 1024?",
                detail={"max_upload_size": self.max_upload_size},
            )

    def _validate_whisper(self) -> None:
        # 复用 domain 层的校验, 而不是把支持列表抄一遍 —— 否则配置层与
        # provider 层会各自漂移, 出现"配置通过了但 provider 拒绝"的错位。
        try:
            self.whisper_config()
        except ASRConfigurationError as exc:
            raise ConfigurationError(
                f"invalid whisper configuration: {exc}",
                detail={"domain_code": getattr(exc, "code", None)},
                cause=exc,
            ) from exc

    def _validate_asr_mode(self) -> None:
        if self.asr_mode not in ASR_MODES:
            raise ConfigurationError(
                f"asr_mode must be one of {list(ASR_MODES)} (got {self.asr_mode!r})",
                detail={"asr_mode": self.asr_mode},
            )

    def _validate_ocr_config(self) -> None:
        if not isinstance(self.ocr_config, Mapping):
            raise ConfigurationError(
                f"ocr_config must be an object (got {type(self.ocr_config).__name__})"
            )
        for key in self.ocr_config:
            if not isinstance(key, str) or not key.strip():
                raise ConfigurationError(
                    f"ocr_config keys must be non-empty strings (got {key!r})"
                )
        # 递归检查: 嵌套的 {"engine": {"api_key": ...}} 也必须被挡住。
        leaks = secret_paths(self.ocr_config)
        if leaks:
            raise ConfigurationError(
                f"ocr_config must not carry secrets (found {leaks}); this build has no "
                "external API, so a secret here is either a mistake or a leak",
                detail={"secret_paths": leaks},
            )
        kind = self.ocr_config.get("kind")
        if kind is not None and kind not in OCR_KINDS:
            raise ConfigurationError(
                f"ocr_config.kind must be one of {list(OCR_KINDS)} (got {kind!r})",
                detail={"kind": kind},
            )
        require_real = self.ocr_config.get("require_real")
        if require_real is not None and not isinstance(require_real, bool):
            raise ConfigurationError(
                f"ocr_config.require_real must be a boolean (got {require_real!r})"
            )
        try:
            json.dumps(dict(self.ocr_config), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(
                f"ocr_config must be JSON-serialisable: {exc}", cause=exc
            ) from exc

    def _validate_log_level(self) -> None:
        if self.log_level not in LOG_LEVELS:
            raise ConfigurationError(
                f"log_level must be one of {list(LOG_LEVELS)} (got {self.log_level!r})",
                detail={"log_level": self.log_level},
            )

    def _validate_llm(self) -> None:
        # §1: key 只读进程环境变量, 禁写配置文件/数据库。
        # ``llm_api_key`` 不进 CONFIGURABLE_KEYS —— 它不是"可调项",
        # 因此走到 _validate_llm 时它只能来自 ENV 层 (其余层的入口已在
        # read_config_file / normalise_overrides / with_overrides 挡掉)。
        # 这里只做形状校验与 HTTPS 约束, 绝不回显明文。
        if self.llm_api_key is not None:
            if not isinstance(self.llm_api_key, str) or not self.llm_api_key.strip():
                raise ConfigurationError("llm_api_key must be a non-empty string")
            origin = self.sources.get("llm_api_key", "?")
            if origin not in (ConfigSource.ENV.value, ConfigSource.DEFAULT.value):
                raise ConfigurationError(
                    "llm_api_key must only come from process environment "
                    "(CLASSROOM_LLM_API_KEY)",
                    detail={"origin": origin},
                )
        if not isinstance(self.llm_api_base, str) or not self.llm_api_base.strip():
            raise ConfigurationError("llm_api_base must be a non-empty string")
        from urllib.parse import urlparse as _urlparse

        parsed = _urlparse(self.llm_api_base.strip())
        if parsed.scheme != "https" or not parsed.hostname:
            raise ConfigurationError(
                "llm_api_base must be an https URL (health 只断形状不断连通)",
                detail={"llm_api_base": "https://<redacted-host>/v1"},
            )
        if not isinstance(self.llm_model, str) or not self.llm_model.strip():
            raise ConfigurationError("llm_model must be a non-empty string")

    # ------------------------------------------------------------------
    # 派生视图
    # ------------------------------------------------------------------

    def _writable_dict(self) -> dict[str, Any]:
        """可回灌 ``AppConfig(**...)`` 的字段字典 (真实值, 非展示口径)。

        与 :meth:`to_dict` 的区别只有一处: ``llm_api_key`` 保留真实值
        (它本来就是内存字段, 只是不允许出现在任何展示/落盘输出里)。
        ``with_overrides`` 与 ``AppConfig(**to_dict)`` 这类"重建配置"的
        调用方必须用它, 否则重建会把 key 丢掉或形状对不上。
        """
        out = dict(self.to_dict(include_metadata=False))
        out.pop("llm_api_key_present", None)
        out["llm_api_key"] = self.llm_api_key
        return out

    @property
    def database_filename(self) -> str:
        """数据库文件名 (备份层按文件名在 data_dir 内定位它)。"""
        return os.path.basename(self.database_path)

    @property
    def database_dir(self) -> str:
        return os.path.dirname(self.database_path)

    @property
    def backups_dir(self) -> str:
        return os.path.join(self.data_dir, "backups")

    @property
    def logs_dir(self) -> str:
        return os.path.join(self.data_dir, "logs")

    def source_of(self, key: str) -> ConfigSource:
        """某个配置值来自哪一层 (未知则抛 KeyError, 避免静默返回 None)。"""
        raw = self.sources[key]
        return ConfigSource(raw)

    def whisper_config(self) -> WhisperConfig:
        """构造并校验 ``WhisperConfig`` (与 provider 层共用同一份校验)。"""
        config = WhisperConfig(
            model_name=self.whisper_model,
            device=self.whisper_device,
            compute_type=self.whisper_compute_type,
            language=self.whisper_language,
        )
        config.validate()
        return config

    def ocr_provider(self, **overrides: Any) -> Any:
        """按 ``ocr_config`` 构造 OCR provider (延迟 import, 保持配置模块轻量)。"""
        from src.ocr_provider import create_ocr_provider

        settings = dict(self.ocr_config)
        settings.update(overrides)
        kind = settings.pop("kind", DEFAULT_OCR_KIND)
        require_real = bool(settings.pop("require_real", False))
        return create_ocr_provider(kind, require_real=require_real, **settings)

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        """repr 必须脱敏。

        它会出现在 traceback、pytest 失败输出、调试器面板里 —— 而这些内容
        经常被直接复制进日志或 issue。dataclass 默认生成的 repr 会原样打印
        字段值, 于是 ``ocr_config`` 里一个形如 ``api_key=...`` 的字符串就会
        从这条完全不被注意的渠道漏出去。
        """
        fields = ", ".join(
            f"{key}={value!r}" for key, value in self.to_dict(include_metadata=False).items()
        )
        return f"AppConfig({fields})"

    def to_dict(self, *, include_metadata: bool = True) -> dict[str, Any]:
        """JSON 可序列化的完整配置 (密钥已按定义不可能存在, 仍做一次防御性脱敏)。"""
        # §1: to_dict 是展示口径 —— llm_api_key 只记"有没有", 绝不记值。
        # 注意它与 AppConfig 字段名不同 (llm_api_key_present), 因此
        # with_overrides / AppConfig(**to_dict) 必须显式剔除它 (见下)。
        out: dict[str, Any] = {
            "data_dir": self.data_dir,
            "database_path": self.database_path,
            "host": self.host,
            "port": self.port,
            "max_upload_size": self.max_upload_size,
            "whisper_model": self.whisper_model,
            "whisper_device": self.whisper_device,
            "whisper_compute_type": self.whisper_compute_type,
            "whisper_language": self.whisper_language,
            "asr_mode": self.asr_mode,
            "ocr_config": redact_mapping(self.ocr_config),
            "log_level": self.log_level,
            "debug": self.debug,
            "allow_remote": self.allow_remote,
            # §1: 展示口径里 key 只有"占位/缺席"两种形状, 绝不记值。
            # with_overrides / 重建配置走 _writable_dict() (保留真实值)。
            "llm_api_key": SECRET_PLACEHOLDER if self.llm_api_key else None,
            "llm_api_base": self.llm_api_base,
            "llm_model": self.llm_model,
        }
        if include_metadata:
            out["config_path"] = self.config_path
            out["sources"] = {key: self.sources.get(key) for key in sorted(self.sources)}
            out["unknown_env_vars"] = list(self.unknown_env_vars)
        return out

    def public_dict(self) -> dict[str, Any]:
        """可安全展示/写日志的配置视图 (供 ``/api/health``、``--print-config`` 用)。"""
        return self.to_dict()

    def describe(self) -> str:
        """人类可读的多行配置说明 (含每个值的来源)。"""
        lines = ["Classroom Assistant configuration"]
        for key in sorted(self.to_dict(include_metadata=False)):
            value = self.to_dict(include_metadata=False)[key]
            origin = self.sources.get(key, "?")
            lines.append(f"  {key:<20} = {value!r}  [{origin}]")
        if self.config_path:
            lines.append(f"  {'config_path':<20} = {self.config_path!r}")
        if self.unknown_env_vars:
            lines.append(
                f"  {'ignored env vars':<20} = {list(self.unknown_env_vars)!r} "
                "(unrecognised CLASSROOM_* variables)"
            )
        return "\n".join(lines)


# ----------------------------------------------------------------------
# 加载
# ----------------------------------------------------------------------


def load_config(
    *,
    config_path: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    cli_overrides: Optional[Mapping[str, Any]] = None,
    cwd: Optional[str] = None,
) -> AppConfig:
    """按 ``defaults -> config file -> environment -> CLI`` 组装配置。

    每一层在读取时就地做类型转换, 因此报错信息能准确指出"是哪个来源的哪个键"
    有问题 —— 而不是等到合并之后才发现某个值是坏的, 却不知道从哪来。
    """
    source_env: Mapping[str, str] = os.environ if env is None else env
    raw_cli = dict(cli_overrides or {})

    resolved_path = discover_config_file(
        cwd=cwd, env=source_env, cli_overrides=raw_cli, explicit=config_path
    )

    layers: list[tuple[ConfigSource, dict[str, Any]]] = [
        (ConfigSource.DEFAULT, default_values(cwd)),
    ]
    if resolved_path is not None:
        layers.append((ConfigSource.FILE, read_config_file(resolved_path)))

    env_values, unknown_env_vars = read_environment(source_env)
    layers.append((ConfigSource.ENV, env_values))
    layers.append((ConfigSource.CLI, normalise_overrides(raw_cli)))

    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for source, layer in layers:
        for key, value in layer.items():
            values[key] = value
            sources[key] = source.value

    if "database_path" not in values:
        values["database_path"] = os.path.join(
            os.path.abspath(values["data_dir"]), "database", DEFAULT_DATABASE_FILENAME
        )
        sources["database_path"] = ConfigSource.DERIVED.value

    config = AppConfig(
        **values,
        config_path=resolved_path,
        sources=sources,
        unknown_env_vars=unknown_env_vars,
    )
    return config.validate()
