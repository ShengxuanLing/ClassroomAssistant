# -*- coding: utf-8 -*-
"""LLM 总结提供方 (P0/§1 出站唯一通道)。

设计约束 (与 ASR / OCR provider 同构, 但边界更紧):

- 默认 ``MockSummarizer``: 确定性抽取式总结, 无网络、无密钥、无依赖。
  默认回归全绿永远走它, 并在输出里明说自己"不是真实模型"。
- ``OpenAICompatibleProvider``: 仅用标准库 ``urllib.request`` 访问
  OpenAI 兼容的 ``/chat/completions``。**禁止** httpx / requests / openai
  等 SDK (``tests/test_release_gate.py`` 的 AI 供应链门按 import 形状
  扫描, ``urllib`` 不在禁名单内; 本文件是新依赖的**唯一**落点)。
- 出境规则: 网络调用只允许由显式用户动作触发的上层服务调用。本模块
  绝不后台发送、绝不记录密钥与正文 (调用方日志只允许记长度/截断/脱敏)。
- 只吃文本: 音频只走本地 Whisper 转写文本, 图片默认走本地 OCR 文字路;
  音频/图片字节绝不进入请求体。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlparse

from src.application.errors import ConfigurationError, ProcessingError

__all__ = [
    "DEFAULT_LLM_API_BASE",
    "DEFAULT_LLM_MODEL",
    "DEFAULT_SUMMARY_CHAR_LIMIT",
    "DEFAULT_SUMMARY_TIMEOUT_SECONDS",
    "DEFAULT_SUMMARY_MAX_RETRIES",
    "TRUNCATED_MARKER",
    "PROMPT_VERSION",
    "CHINESE_PREAMBLE",
    "SummarizerProvider",
    "SummarizerResult",
    "MockSummarizer",
    "OpenAICompatibleProvider",
    "build_prompt",
    "truncate_evidence_text",
]

# §0 默认值: 未给 API base URL / model 时用占位 (health 只断形状不断连通)。
DEFAULT_LLM_API_BASE = "https://LLM_API_BASE/v1"
DEFAULT_LLM_MODEL = "llm-model-default"

# §0 默认值: 单材料 6000 字符、超时 60s、重试 1 次、超限标 [truncated]。
DEFAULT_SUMMARY_CHAR_LIMIT = 6000
DEFAULT_SUMMARY_TIMEOUT_SECONDS = 60
DEFAULT_SUMMARY_MAX_RETRIES = 1

#: 超限截断标记 (追加在被截断的输入末尾, 调用方可据此提示用户)。
TRUNCATED_MARKER = "[truncated]"

#: 提示词版本 (随 Summary 返回, 审计"哪一版提示词产出了哪份总结")。
PROMPT_VERSION = "review-pack-v1"

#: 固定中文导读前缀: 请求体 = 选中证据原文 + 该前缀。证据原文一字不改,
#: 前缀只说明"用中文总结 / 每段挂证据编号 / 冲突并列不裁决"。
CHINESE_PREAMBLE = (
    "你是课堂助手的内容整理员。请用中文总结以下课堂证据的要点。"
    "要求: 每个要点标注对应证据编号; 不改写、不翻译证据原文; "
    "证据之间相互矛盾时并列列出, 不做裁决。\n\n证据原文:\n"
)


def truncate_evidence_text(
    text: str, limit: int = DEFAULT_SUMMARY_CHAR_LIMIT
) -> tuple[str, bool]:
    """按字符上限截断证据原文, 返回 ``(文本, 是否截断)``。"""
    raw = str(text or "")
    if limit <= 0 or len(raw) <= limit:
        return raw, False
    return raw[:limit] + TRUNCATED_MARKER, True


def build_prompt(
    evidence_text: str, *, limit: int = DEFAULT_SUMMARY_CHAR_LIMIT
) -> tuple[str, bool]:
    """组装发往 LLM 的提示词: 固定中文前缀 + 截断后的证据原文。"""
    clipped, truncated = truncate_evidence_text(evidence_text, limit)
    return CHINESE_PREAMBLE + clipped, truncated


@dataclass(frozen=True)
class SummarizerResult:
    """一次总结调用的结构化结果 (只记录形状, 绝不记录密钥)。"""

    text: str
    model: str
    #: 输入是否因超出 char_limit 而被截断 (标 [truncated])。
    truncated: bool = False
    #: True = 抽取版回落 (Mock 或 real 失败后的兜底), 供 UI 明示。
    fallback: bool = False
    evidence_ids: tuple[str, ...] = ()
    prompt_version: str = PROMPT_VERSION
    detail: Mapping[str, Any] = field(default_factory=dict)


class SummarizerProvider(ABC):
    """总结提供方抽象。只吃文本, 不吃音频/图片字节。"""

    name: str = "summarizer"

    @abstractmethod
    def summarize(
        self,
        evidence_text: str,
        evidence_ids: Sequence[str] = (),
        *,
        timeout_seconds: int = DEFAULT_SUMMARY_TIMEOUT_SECONDS,
        char_limit: int = DEFAULT_SUMMARY_CHAR_LIMIT,
    ) -> SummarizerResult:
        """对给定证据原文做中文总结; 失败由调用方回落抽取版。"""


class MockSummarizer(SummarizerProvider):
    """默认的确定性抽取式总结 (无网络、无密钥)。

    它**不是**真实模型: 输出第一句就明说, 供 UI 与用户核对
    (与 MockASRProvider / MockOCREngine 的"诚实边界"同一原则)。
    """

    name = "mock"

    def summarize(
        self,
        evidence_text: str,
        evidence_ids: Sequence[str] = (),
        *,
        timeout_seconds: int = DEFAULT_SUMMARY_TIMEOUT_SECONDS,
        char_limit: int = DEFAULT_SUMMARY_CHAR_LIMIT,
    ) -> SummarizerResult:
        clipped, truncated = truncate_evidence_text(evidence_text, char_limit)
        excerpt = " ".join(clipped.split())[:200]
        ids = tuple(str(e) for e in (evidence_ids or ()))
        head = "、".join(ids[:5]) + ("…" if len(ids) > 5 else "") if ids else "无"
        text = (
            "【抽取版总结 —— 本地生成, 未调用外部模型】"
            f"共 {len(ids)} 条证据 ({head})。要点摘录: {excerpt}"
        )
        return SummarizerResult(
            text=text,
            model="mock-extractive",
            truncated=truncated,
            fallback=True,
            evidence_ids=ids,
            detail={"provider": "mock"},
        )


class OpenAICompatibleProvider(SummarizerProvider):
    """OpenAI 兼容 ``/chat/completions`` 提供方 (标准库实现)。

    - ``api_key`` 只经构造参数注入 (来自进程环境变量
      ``CLASSROOM_LLM_API_KEY``, 由 bootstrap 装配), 绝不写
      配置文件 / 数据库 / 日志。
    - 只向 ``api_base`` 的 host 发 HTTPS 请求 (allowlist 即配置值本身)。
    - 请求体只有 ``{model, messages}`` 文本 JSON; 音频/图片字节绝不进入。
    - 超时 / 失败按 §0: 超时 60s、重试 1 次; 重试仍失败则抛
      ``ProcessingError``, 由调用方回落抽取版。
    """

    name = "openai-compatible"

    def __init__(
        self,
        *,
        api_key: str,
        api_base: str = DEFAULT_LLM_API_BASE,
        model: str = DEFAULT_LLM_MODEL,
        max_retries: int = DEFAULT_SUMMARY_MAX_RETRIES,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            # real 缺 key 即 ConfigurationError, 禁静默降级 (§1 验收)。
            raise ConfigurationError(
                "llm_mode='real' requires CLASSROOM_LLM_API_KEY to be set; "
                "refusing to silently fall back to mock"
            )
        parsed = urlparse(str(api_base or "").strip())
        if parsed.scheme != "https" or not parsed.hostname:
            raise ConfigurationError("llm_api_base must be an https URL")
        if not isinstance(model, str) or not model.strip():
            raise ConfigurationError("llm_model must be a non-empty string")
        self._api_key = api_key
        self._api_base = api_base.strip().rstrip("/")
        self._model = model.strip()
        self._max_retries = max(0, int(max_retries))

    @property
    def model(self) -> str:
        return self._model

    @property
    def api_host(self) -> str:
        """唯一允许出境的 host (allowlist 即配置值本身)。"""
        return str(urlparse(self._api_base).hostname or "")

    def _post_chat_completions(self, prompt: str, timeout_seconds: int) -> str:
        url = self._api_base + "/chat/completions"
        payload = {"model": self._model, "messages": [{"role": "user", "content": prompt}]}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        attempts = 1 + self._max_retries
        last_error: Optional[BaseException] = None
        for _ in range(attempts):
            request = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + self._api_key,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                    body = json.loads(response.read().decode("utf-8", "replace"))
                return str(body["choices"][0]["message"]["content"])
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError,
                    IndexError, TypeError) as exc:
                last_error = exc
        raise ProcessingError(
            "llm summarization failed; fall back to the extractive version",
            detail={"host": self.api_host, "model": self._model},
        ) from last_error

    def summarize(
        self,
        evidence_text: str,
        evidence_ids: Sequence[str] = (),
        *,
        timeout_seconds: int = DEFAULT_SUMMARY_TIMEOUT_SECONDS,
        char_limit: int = DEFAULT_SUMMARY_CHAR_LIMIT,
    ) -> SummarizerResult:
        prompt, truncated = build_prompt(evidence_text, limit=char_limit)
        text = self._post_chat_completions(prompt, timeout_seconds)
        ids = tuple(str(e) for e in (evidence_ids or ()))
        return SummarizerResult(
            text=text,
            model=self._model,
            truncated=truncated,
            fallback=False,
            evidence_ids=ids,
            detail={"provider": "openai-compatible", "host": self.api_host},
        )

    def __repr__(self) -> str:
        # 脱敏: 绝不在 repr 里出现 api_key / 完整 base URL。
        return (
            "OpenAICompatibleProvider(host=%r, model=%r, api_key=<redacted>)"
            % (self.api_host, self._model)
        )
