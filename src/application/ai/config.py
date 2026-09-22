# -*- coding: utf-8 -*-
"""AI provider 配置 (TASK-76 §5/§29/§30)。

独立于 ``AppConfig`` 的轻量配置: ``AppConfig`` 的键集合由配置测试逐键
钉死, 在那里加 ``CLASSROOM_AI_*`` 会扰动现有门禁; AI 配置只在显式调用
AI 管线时读取, 默认关闭 (``CLASSROOM_AI_ENABLED``), 不影响旧链路。

环境变量 (全部可选, 文档只写 placeholder)::

    CLASSROOM_AI_ENABLED=true|false        (默认 false: 旧确定性链路)
    CLASSROOM_AI_BASE_URL=<YOUR_API_BASE>  (默认沿用 CLASSROOM_LLM_API_BASE)
    CLASSROOM_AI_API_KEY=<YOUR_API_KEY>    (默认沿用 CLASSROOM_LLM_API_KEY)
    CLASSROOM_AI_MODEL=<YOUR_MODEL>        (默认沿用 CLASSROOM_LLM_MODEL)
    CLASSROOM_AI_VISION_MODEL=<MODEL>      (默认回落到 TEXT model)
    CLASSROOM_AI_AUDIO_MODEL=<MODEL>       (默认回落到 TEXT model)
    CLASSROOM_AI_TIMEOUT_SECONDS=60
    CLASSROOM_AI_MAX_RETRIES=1
    CLASSROOM_AI_JSON_MODE=true|false  (默认 true: 带 response_format;
        某些网关对该字段犯病时设 false, 走纯文本 + 严格 schema 校验)
    CLASSROOM_AI_ALLOW_IMAGE_BYTES=true|false (默认 false: 只发 OCR 文字;
        true 时图片材料的原始字节出境走 vision)

密钥规则 (与 ``src/application/config.py`` 同源):

- ``*_API_KEY`` 只读进程环境, 绝不写文件 / 数据库 / 日志 / UI。
- ``repr`` / ``to_dict`` / exception message 里 key 只有"有/无"两种形状。
- ``.env`` 已在 ``.gitignore`` (``.env`` 条目, P0-3)。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

from src.application.config import SECRET_PLACEHOLDER
from src.application.errors import ConfigurationError

__all__ = [
    "DEFAULT_AI_TIMEOUT_SECONDS",
    "DEFAULT_AI_MAX_RETRIES",
    "AIEnvNames",
    "AIConfig",
    "load_ai_config",
]


#: 默认超时 / 重试 (与现有 summarizer 口径一致: 60s + 1 次重试)。
DEFAULT_AI_TIMEOUT_SECONDS = 60
DEFAULT_AI_MAX_RETRIES = 1


class AIEnvNames:
    """AI 配置的环境变量名 (唯一真相, 测试据此断言文档与实现一致)。"""

    ENABLED = "CLASSROOM_AI_ENABLED"
    BASE_URL = "CLASSROOM_AI_BASE_URL"
    API_KEY = "CLASSROOM_AI_API_KEY"
    MODEL = "CLASSROOM_AI_MODEL"
    TEXT_MODEL = "CLASSROOM_AI_TEXT_MODEL"
    VISION_MODEL = "CLASSROOM_AI_VISION_MODEL"
    AUDIO_MODEL = "CLASSROOM_AI_AUDIO_MODEL"
    TIMEOUT = "CLASSROOM_AI_TIMEOUT_SECONDS"
    MAX_RETRIES = "CLASSROOM_AI_MAX_RETRIES"
    #: 是否发送 ``response_format: json_object`` (默认 true; 某些网关对
    #: 该字段犯病时会 200 包错, 此时设 false 走纯文本 + 严格 schema 校验)。
    JSON_MODE = "CLASSROOM_AI_JSON_MODE"
    #: 是否允许把图片字节发给模型 (默认 false: 只发 OCR 文字; 开启后图片
    #: 材料走 vision，原始字节出境——用户已明确豁免课堂材料的隐私顾虑)。
    IMAGE_BYTES = "CLASSROOM_AI_ALLOW_IMAGE_BYTES"

    #: 回落到既有 LLM 配置的旧变量名 (AI 未配时沿用, 不强制双份配置)。
    FALLBACK_BASE_URL = "CLASSROOM_LLM_API_BASE"
    FALLBACK_API_KEY = "CLASSROOM_LLM_API_KEY"
    FALLBACK_MODEL = "CLASSROOM_LLM_MODEL"


_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "y", "t"})

_FALSE_VALUES = frozenset({"0", "false", "no", "off", "n", "f"})


def _as_bool_text(value: Any) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _as_default_true_bool(value: Any, *, key: str) -> bool:
    """默认 true 的布尔环境变量 (缺省/空 -> True; 非法值直接报错)。

    与 ``_as_bool_text`` (默认 false) 对偶: ``CLASSROOM_AI_JSON_MODE``
    默认开, 只有显式写 false 类才关 —— 关掉是个需要有意识的动作,
    写错别字时报错比静默关掉安全。
    """
    if value is None or not str(value).strip():
        return True
    text = str(value).strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    raise ConfigurationError(
        "%s must be a boolean (true/false)" % key,
        detail={"key": key},
    )


@dataclass(frozen=True)
class AIConfig:
    """AI 管线配置 (值对象, 不可变)。

    ``enabled=False`` 时管线拒绝发起任何 AI 调用 (调用方应走旧确定性
    链路)。``api_key`` 为 ``None`` 表示"未配置真实凭证",
    此时只能使用 ``FakeAIProvider``。
    """

    enabled: bool = False
    base_url: Optional[str] = None
    text_model: Optional[str] = None
    vision_model: Optional[str] = None
    audio_model: Optional[str] = None
    timeout_seconds: int = DEFAULT_AI_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_AI_MAX_RETRIES
    api_key_present: bool = False
    #: 是否在请求里带 ``response_format: json_object`` (默认开)。
    json_mode: bool = True
    #: 是否允许把图片字节发给模型 (默认关，见 IMAGE_BYTES)。
    allow_image_bytes: bool = False
    sources: Mapping[str, str] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", dict(self.sources or {}))

    @property
    def model(self) -> Optional[str]:
        """主文本模型 (vision/audio 未配时回落到它)。"""
        return self.text_model

    @property
    def has_credentials(self) -> bool:
        """是否具备发起真实调用的凭证与地址。"""
        return bool(self.api_key_present and (self.base_url or "").strip())

    def describe(self) -> dict[str, Any]:
        """可安全展示/写日志的视图 (key 只有有/无, 绝无明文)。"""
        return {
            "enabled": self.enabled,
            "base_url_configured": bool((self.base_url or "").strip()),
            "api_key_present": bool(self.api_key_present),
            "text_model": self.text_model,
            "vision_model": self.vision_model,
            "audio_model": self.audio_model,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "json_mode": self.json_mode,
            "allow_image_bytes": self.allow_image_bytes,
            "sources": dict(self.sources),
        }

    def __repr__(self) -> str:
        # 防御性: 即使将来有人给本对象加了明文字段, repr 也不打印它。
        return "AIConfig(%s)" % ", ".join(
            f"{k}={v!r}" for k, v in self.describe().items() if k != "sources"
        )


def _first_nonempty(*values: Optional[str]) -> Optional[str]:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def load_ai_config(
    env: Optional[Mapping[str, str]] = None,
) -> AIConfig:
    """从进程环境读取 AI 配置 (默认 ``os.environ``; 测试可注入映射)。

    未知 ``CLASSROOM_AI_*`` 变量直接忽略 (环境是共享命名空间, 与
    ``AppConfig.read_environment`` 同一理由), 但绝不记录它们的值。
    """
    source: Mapping[str, str] = os.environ if env is None else env
    get = lambda name: source.get(name) if isinstance(source, Mapping) else None

    enabled = _as_bool_text(get(AIEnvNames.ENABLED))

    base_url = _first_nonempty(get(AIEnvNames.BASE_URL), get(AIEnvNames.FALLBACK_BASE_URL))
    text_model = _first_nonempty(
        get(AIEnvNames.TEXT_MODEL), get(AIEnvNames.MODEL), get(AIEnvNames.FALLBACK_MODEL)
    )
    vision_model = _first_nonempty(get(AIEnvNames.VISION_MODEL), text_model)
    audio_model = _first_nonempty(get(AIEnvNames.AUDIO_MODEL), text_model)
    api_key = get(AIEnvNames.API_KEY) or get(AIEnvNames.FALLBACK_API_KEY)
    api_key_present = bool(isinstance(api_key, str) and api_key.strip())

    try:
        timeout = int(str(get(AIEnvNames.TIMEOUT) or "").strip() or DEFAULT_AI_TIMEOUT_SECONDS)
    except ValueError:
        raise ConfigurationError(
            f"{AIEnvNames.TIMEOUT} must be an integer",
            detail={"key": AIEnvNames.TIMEOUT},
        )
    try:
        retries = int(str(get(AIEnvNames.MAX_RETRIES) or "").strip() or DEFAULT_AI_MAX_RETRIES)
    except ValueError:
        raise ConfigurationError(
            f"{AIEnvNames.MAX_RETRIES} must be an integer",
            detail={"key": AIEnvNames.MAX_RETRIES},
        )
    if timeout <= 0:
        raise ConfigurationError(
            f"{AIEnvNames.TIMEOUT} must be positive",
            detail={"key": AIEnvNames.TIMEOUT},
        )
    if retries < 0:
        raise ConfigurationError(
            f"{AIEnvNames.MAX_RETRIES} must be >= 0",
            detail={"key": AIEnvNames.MAX_RETRIES},
        )

    if base_url:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ConfigurationError(
                f"{AIEnvNames.BASE_URL} must be an https URL",
                # 绝不回显完整 URL (host 可能含内部域名)。
                detail={"key": AIEnvNames.BASE_URL, "base_url": "https://<redacted-host>/v1"},
            )

    _ = SECRET_PLACEHOLDER  # 占位符的唯一真相仍在 config 模块, 这里只复用。
    return AIConfig(
        enabled=enabled,
        base_url=base_url,
        text_model=text_model,
        vision_model=vision_model,
        audio_model=audio_model,
        timeout_seconds=timeout,
        max_retries=retries,
        api_key_present=api_key_present,
        json_mode=_as_default_true_bool(get(AIEnvNames.JSON_MODE), key=AIEnvNames.JSON_MODE),
        allow_image_bytes=_as_bool_text(get(AIEnvNames.IMAGE_BYTES)),
        sources={"origin": "environment"},
    )


def get_api_key(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """取出真实 API Key 明文 (仅在发起网络调用前一刻调用)。

    调用方必须保证: 不记录、不透传、不进 exception message。
    """
    source: Mapping[str, str] = os.environ if env is None else env
    key = source.get(AIEnvNames.API_KEY) if isinstance(source, Mapping) else None
    if isinstance(key, str) and key.strip():
        return key.strip()
    fallback = source.get(AIEnvNames.FALLBACK_API_KEY) if isinstance(source, Mapping) else None
    if isinstance(fallback, str) and fallback.strip():
        return fallback.strip()
    return None
