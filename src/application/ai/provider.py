# -*- coding: utf-8 -*-
"""AI provider 抽象与实现 (TASK-76 §4)。

统一抽象::

    AIProvider
      ├─ generate_structured(prompt) -> str   (结构化 JSON 文本)
      ├─ analyze_text(...)                    (文本理解: chunk 抽取)
      ├─ analyze_image(...)                   (图片理解: OCR 文本 + 可选视觉)
      └─ transcribe_audio(...)                (音频转写: 默认不支持 -> 回落既有链路)

实现:

- ``FakeAIProvider``: 确定性 fixture (默认, 零出站)。它**不是**真实模型,
  输出即本地规则抽取, 供全部管线测试与无凭证回归使用。
- ``OpenAICompatibleAIProvider``: 标准库 ``urllib`` 访问
  ``{base_url}/chat/completions`` (``response_format: json_object``),
  与 ``src/llm_provider.py`` 同一供应链形状 (无新增依赖)。

禁止事项 (违者即破坏 Evidence-first):

- provider 绝不碰数据库 / 绝不决定 Evidence/Course/Session ID。
- API Key 只经构造参数注入, ``repr`` / error / 日志一律脱敏。
- 音频/图片**字节**默认不进入请求体 (只送 OCR/转写文本); vision 需要
  显式 ``allow_image_bytes=True`` 且 capability 声明支持。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlparse

from src.application.errors import ConfigurationError, ProcessingError

__all__ = [
    "ProviderCapabilities",
    "AIProvider",
    "AIRequestError",
    "FakeAIProvider",
    "OpenAICompatibleAIProvider",
    "FAKE_PROVIDER_NAME",
    "OPENAI_COMPATIBLE_PROVIDER_NAME",
    "MAX_IMAGE_BYTES",
    "sniff_image_mime",
]

FAKE_PROVIDER_NAME = "fake-deterministic"
OPENAI_COMPATIBLE_PROVIDER_NAME = "openai-compatible"

#: 发往 vision 的单张图片上限 (8MB; 超过则用 OCR 文字路径, 不硬发)。
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def sniff_image_mime(data: bytes) -> Optional[str]:
    """按魔数识别图片类型 (标准库实现, 无新依赖); 未知返回 None。"""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 12:
        return None
    if bytes(data[:8]) == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if bytes(data[:3]) == b"\xff\xd8\xff":
        return "image/jpeg"
    if bytes(data[:4]) == b"RIFF" and bytes(data[8:12]) == b"WEBP":
        return "image/webp"
    if bytes(data[:6]) in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if bytes(data[:2]) == b"BM":
        return "image/bmp"
    return None


@dataclass(frozen=True)
class ProviderCapabilities:
    """Provider 能力显式声明 (调用方据此决定 text/vision/audio 路由)。"""

    supports_text: bool = True
    supports_image: bool = False
    supports_audio: bool = False
    supports_structured_output: bool = False
    supports_image_bytes: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "supports_text": self.supports_text,
            "supports_image": self.supports_image,
            "supports_audio": self.supports_audio,
            "supports_structured_output": self.supports_structured_output,
            "supports_image_bytes": self.supports_image_bytes,
        }


class AIRequestError(ProcessingError):
    """AI 请求失败 (超时 / 4xx / 5xx / 网络错误 / 畸形响应)。

    code 沿用 ``PROCESSING_ERROR`` (422, 与其它处理失败同口径);
    区分身份由 message/detail 里的 provider/chunk 形状信息承担。
    ``detail`` 只记 host/model/http_status 等形状, 绝不含 key 与正文。
    """

    def __init__(self, message: str, detail: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, detail=dict(detail) if detail else None)


def _raise_for_wrapped_error(text: str, *, host: str, model: str) -> None:
    """识别包在 HTTP 200 里的网关报错并转成可诊断的 ``AIRequestError``。

    个别 OpenAI-compatible 网关不按规矩出 4xx/5xx, 而是 200 + 正文
    ``{"type":"error","error":{"message":"..."}}``。不拦截的话, 这段正文
    会流进 schema 校验器, 最终只剩一句含糊的" N of N chunks failed"。
    内部 message 截断 200 字符 (网关文本, 不含 key)。
    """
    try:
        outer = json.loads(text)
    except ValueError:
        return
    if not isinstance(outer, dict):
        return
    message: Optional[str] = None
    if outer.get("type") == "error" and isinstance(outer.get("error"), dict):
        raw = outer["error"].get("message")
        message = str(raw).strip() if raw else None
    elif isinstance(outer.get("error"), dict):
        raw = outer["error"].get("message")
        message = str(raw).strip() if raw else None
    elif isinstance(outer.get("error"), str) and outer["error"].strip():
        message = outer["error"].strip()
    if not message:
        return
    raise AIRequestError(
        "AI provider reported an error: %s; evidence is preserved "
        "and the material can be retried" % message[:200],
        detail={"host": host, "model": model},
    )


class AIProvider(ABC):
    """AI provider 统一抽象。只做语义理解, 不碰任何持久化。"""

    name: str = "ai-provider"

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """能力声明。"""

    @abstractmethod
    def generate_structured(
        self,
        prompt: str,
        *,
        timeout_seconds: int = 60,
        max_output_chars: int = 8000,
    ) -> str:
        """给定提示词返回**结构化 JSON 文本** (调用方再做 schema 校验)。"""

    def analyze_text(
        self,
        text: str,
        *,
        chunk_id: str = "",
        material_label: str = "",
        content_language: Optional[str] = None,
        timeout_seconds: int = 60,
    ) -> str:
        """文本理解 (默认走 generate_structured + chunk 抽取 prompt)。"""
        from src.application.ai.prompts import build_chunk_extraction_prompt

        prompt = build_chunk_extraction_prompt(
            chunk_id=chunk_id or "chunk",
            chunk_text=text,
            material_label=material_label,
            content_language=content_language,
        )
        return self.generate_structured(prompt, timeout_seconds=timeout_seconds)

    def analyze_image(
        self,
        ocr_text: str,
        *,
        chunk_id: str = "",
        material_label: str = "",
        content_language: Optional[str] = None,
        timeout_seconds: int = 60,
    ) -> str:
        """图片理解 (默认: OCR 文本 + 视觉结构 prompt; 不发送图片字节)。"""
        from src.application.ai.prompts import build_image_analysis_prompt

        prompt = build_image_analysis_prompt(
            ocr_text=ocr_text,
            material_label=material_label,
            content_language=content_language,
        )
        void = chunk_id  # chunk 绑定由调用方经 evidence_refs 完成, 不进 prompt。
        _ = void
        return self.generate_structured(prompt, timeout_seconds=timeout_seconds)

    def transcribe_audio(
        self,
        audio_path: str,
        *,
        timeout_seconds: int = 120,
    ) -> Sequence[Mapping[str, Any]]:
        """音频转写 (默认不支持 -> 调用方回落既有 Whisper/ASR 链路)。

        子类若声明 ``supports_audio`` 才可覆写本方法。
        """
        raise AIRequestError(
            "this provider does not support audio transcription; "
            "use the existing ASR pipeline instead",
            detail={"provider": self.name},
        )

    def analyze_image_bytes(
        self,
        image_bytes: bytes,
        *,
        ocr_text: str = "",
        chunk_id: str = "",
        material_label: str = "",
        content_language: Optional[str] = None,
        timeout_seconds: int = 60,
    ) -> str:
        """图片字节理解 (默认不支持 -> 调用方走 OCR 文字路径)。

        子类需同时声明 ``supports_image_bytes`` 才可覆写。注意：调用本方法
        意味着原始图片字节出境（OCR 文字路径则不出境），是否调用由
        ``CLASSROOM_AI_ALLOW_IMAGE_BYTES`` 与 capability 双重门控。
        """
        raise AIRequestError(
            "this provider does not support image bytes; "
            "use the OCR-text path instead",
            detail={"provider": self.name},
        )


_SENTENCE_SPLIT = re.compile(r"(?<=[。！？.!?;；])\s*")


def _fake_candidates_for_text(text: str, chunk_id: str) -> list[dict[str, Any]]:
    """确定性 fixture: 按句子切分, 每句一个候选 (诚实 mock, 非语义理解)。"""
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text or "") if s.strip()]
    out: list[dict[str, Any]] = []
    for index, sentence in enumerate(sentences[:8]):
        lowered = sentence.lower()
        if any(k in sentence for k in ("定义", "定义：", "定义:", "es decir", "definición", "definició")):
            kp_type = "definition"
        elif re.search(r"[=＋＋^∫∑√α-ω∀∃∈]", sentence) or "=" in sentence:
            kp_type = "formula"
        elif any(k in sentence for k in ("例如", "比如", "ejemplo", "exemple", "por ejemplo")):
            kp_type = "example"
        elif any(k in lowered for k in ("步骤", "首先", "然后", "paso", "step")):
            kp_type = "procedure"
        else:
            kp_type = "concept"
        out.append(
            {
                "title": sentence[:60],
                "description": sentence,
                "type": kp_type,
                "importance": "high" if index == 0 else "medium",
                # 确定性置信度: 首句 0.95 (自动接受), 次句 0.8 (待确认),
                # 其余 0.6 (拒绝/复核队列) —— 覆盖三档阈值。
                "confidence": 0.95 if index == 0 else (0.8 if index == 1 else 0.6),
                "evidence_refs": [chunk_id] if chunk_id else [],
                "relations": [],
                "examples": [],
                "original_terms": [sentence[:30]],
            }
        )
    return out


class FakeAIProvider(AIProvider):
    """确定性 fake provider (默认; 零出站、零密钥)。

    它**不是**真实模型: 输出第一句即声明本地生成 (与 ``MockSummarizer`` /
    ``MockASRProvider`` 的"诚实边界"同一原则)。所有管线测试走它。
    """

    name = FAKE_PROVIDER_NAME

    def __init__(self, *, tag: str = "fake") -> None:
        self._tag = tag

    @property
    def capabilities(self) -> ProviderCapabilities:
        # fake 覆盖 text/image/audio 三条语义链路 (输入都是文本形态:
        # 音频先经转写文本, 图片先经 OCR 文本) —— 但转写本身不支持。
        return ProviderCapabilities(
            supports_text=True,
            supports_image=True,
            supports_audio=False,
            supports_structured_output=True,
            supports_image_bytes=False,
        )

    def generate_structured(
        self,
        prompt: str,
        *,
        timeout_seconds: int = 60,
        max_output_chars: int = 8000,
    ) -> str:
        # 从 prompt 里还原证据文本: 取最后一个 "EVIDENCE TEXT:/OCR TEXT:/
        # TRANSCRIPT:/CHUNK RESULTS:" 标记之后的内容做确定性抽取。
        marker_text = prompt or ""
        for marker in ("EVIDENCE TEXT:\n", "OCR TEXT:\n", "TRANSCRIPT:\n", "CHUNK RESULTS:\n"):
            if marker in marker_text:
                marker_text = marker_text.rsplit(marker, 1)[1]
                break
        # chunk id 引用: 取 AVAILABLE CHUNKS 行声明的 id。
        chunk_ids = re.findall(r"AVAILABLE CHUNKS: \[(.*?)\]", prompt or "")
        chunk_id = chunk_ids[0].strip() if chunk_ids else "chunk-0"
        payload = {
            "summary": "【本地 fixture 总结 —— 未调用外部模型】",
            "topics": ["fixture-topic"],
            "knowledge_points": _fake_candidates_for_text(marker_text, chunk_id),
        }
        text = json.dumps(payload, ensure_ascii=False)
        return text[:max_output_chars]

    def __repr__(self) -> str:
        return "FakeAIProvider(tag=%r)" % (self._tag,)


class OpenAICompatibleAIProvider(AIProvider):
    """OpenAI-compatible ``/chat/completions`` provider (标准库实现)。

    - ``api_key`` 只经构造参数注入 (来自 ``CLASSROOM_AI_API_KEY`` /
      ``CLASSROOM_LLM_API_KEY``), 绝不写配置文件 / 数据库 / 日志。
    - 只向配置的 host 发 HTTPS 请求; 请求体只有文本 JSON
      (``model/messages/response_format``); 音频/图片字节默认绝不进入。
    - 失败抛 ``AIRequestError`` (形状信息 only), 由管线记 processing
      failure 并保留 Evidence。
    """

    name = OPENAI_COMPATIBLE_PROVIDER_NAME

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        vision_model: Optional[str] = None,
        audio_model: Optional[str] = None,
        max_retries: int = 1,
        allow_image_bytes: bool = False,
        json_mode: bool = True,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ConfigurationError(
                "AI provider requires an API key from the process environment "
                "(CLASSROOM_AI_API_KEY); refusing to run without credentials",
            )
        parsed = urlparse(str(base_url or "").strip())
        if parsed.scheme != "https" or not parsed.hostname:
            raise ConfigurationError("AI base_url must be an https URL")
        if not isinstance(model, str) or not model.strip():
            raise ConfigurationError("AI model must be a non-empty string")
        self._api_key = api_key
        self._base_url = str(base_url).strip().rstrip("/")
        self._model = model.strip()
        self._vision_model = (vision_model or model).strip()
        self._audio_model = (audio_model or model).strip()
        self._max_retries = max(0, int(max_retries))
        self._allow_image_bytes = bool(allow_image_bytes)
        # 某些 OpenAI-compatible 网关对 response_format 犯病 (200 包错);
        # 关掉后走纯文本 + 调用方的严格 schema 校验 (schemas.py 容忍围栏)。
        self._json_mode = bool(json_mode)

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_text=True,
            supports_image=True,
            supports_audio=False,
            supports_structured_output=True,
            supports_image_bytes=self._allow_image_bytes,
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def api_host(self) -> str:
        """唯一允许出境的 host (allowlist 即配置值本身)。"""
        return str(urlparse(self._base_url).hostname or "")

    def _post_json(self, payload: Mapping[str, Any], timeout_seconds: int) -> str:
        """POST JSON 到 ``{base_url}/chat/completions`` (有界重试 + 退避)。

        TASK-77 §31/§32: 429/5xx 做有界退避重试 (``1 + max_retries`` 次,
        退避 ``0.5s -> 1s -> 2s`` 上限), 最终失败进 ``AI_FAILED`` 由用户
        手动 Retry —— 绝不疯狂重试。401/403 等其它 4xx 直接失败 (重试无用)。
        每次 ``urlopen`` 都带 ``timeout`` (connect+read 共用, 调用方经
        ``CLASSROOM_AI_TIMEOUT_SECONDS`` 配置, 默认 60s), 绝不无限等待。
        ``detail`` 只记 host/model/http_status, 绝无 key 与正文。
        """
        url = self._base_url + "/chat/completions"
        data = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        attempts = 1 + self._max_retries
        last_error: Optional[BaseException] = None
        for attempt in range(attempts):
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
            except urllib.error.HTTPError as exc:
                last_error = exc
                try:
                    status = int(exc.code)
                except (TypeError, ValueError):
                    status = 0
                detail = {
                    "host": self.api_host,
                    "model": self._model,
                    "http_status": status,
                }
                if status in (401, 403):
                    raise AIRequestError(
                        "AI authentication failed; evidence is preserved "
                        "and the material can be retried",
                        detail=detail,
                    ) from exc
                if status == 429:
                    if attempt < attempts - 1:
                        time.sleep(min(2.0, 0.5 * (2**attempt)))
                        continue
                    raise AIRequestError(
                        "AI rate limit exceeded (HTTP 429); evidence is preserved "
                        "and the material can be retried later",
                        detail=detail,
                    ) from exc
                if 500 <= status <= 599:
                    if attempt < attempts - 1:
                        time.sleep(min(2.0, 0.5 * (2**attempt)))
                        continue
                    raise AIRequestError(
                        "AI request failed; evidence is preserved "
                        "and the material can be retried",
                        detail=detail,
                    ) from exc
                raise AIRequestError(
                    "AI request failed; evidence is preserved "
                    "and the material can be retried",
                    detail=detail,
                ) from exc
            except (TimeoutError, OSError) as exc:
                last_error = exc
                if attempt < attempts - 1:
                    time.sleep(min(2.0, 0.5 * (2**attempt)))
                    continue
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                # 畸形响应 (非 JSON / 缺 choices[0].message.content): 不重试,
                # 直接报可诊断错误 (形状 only, 不贴正文)。
                raise AIRequestError(
                    "AI returned a malformed response; evidence is preserved "
                    "and the material can be retried",
                    detail={"host": self.api_host, "model": self._model},
                ) from exc
        raise AIRequestError(
            "AI request failed; evidence is preserved and the material can be retried",
            detail={"host": self.api_host, "model": self._model},
        ) from last_error

    def generate_structured(
        self,
        prompt: str,
        *,
        timeout_seconds: int = 60,
        max_output_chars: int = 8000,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}
        text = self._post_json(payload, timeout_seconds)
        return self._finish_text(text, max_output_chars=max_output_chars)

    def analyze_image_bytes(
        self,
        image_bytes: bytes,
        *,
        ocr_text: str = "",
        chunk_id: str = "",
        material_label: str = "",
        content_language: Optional[str] = None,
        timeout_seconds: int = 60,
    ) -> str:
        """图片字节理解：OCR 文字 + 原图一起发 vision。

        前置：构造时 ``allow_image_bytes=True``（否则拒绝，避免悄悄出境）。
        OCR 文字照样发送（grounding 与回落依据）；超大/未知类型直接拒收，
        调用方走纯 OCR 路径。``detail`` 只记形状（mime/字节数），绝无字节。
        """
        if not self._allow_image_bytes:
            raise AIRequestError(
                "image bytes are not enabled for this provider "
                "(set CLASSROOM_AI_ALLOW_IMAGE_BYTES=true); "
                "use the OCR-text path instead",
                detail={"host": self.api_host, "model": self._model},
            )
        blob = bytes(image_bytes or b"")
        if not blob:
            raise AIRequestError(
                "empty image bytes; use the OCR-text path instead",
                detail={"host": self.api_host, "model": self._model},
            )
        if len(blob) > MAX_IMAGE_BYTES:
            raise AIRequestError(
                "image exceeds the %d-byte vision budget; "
                "use the OCR-text path instead" % MAX_IMAGE_BYTES,
                detail={
                    "host": self.api_host,
                    "model": self._model,
                    "image_bytes": len(blob),
                },
            )
        mime = sniff_image_mime(blob)
        if mime is None:
            raise AIRequestError(
                "unrecognized image format; use the OCR-text path instead",
                detail={"host": self.api_host, "model": self._model},
            )
        from src.application.ai.prompts import build_image_analysis_prompt

        prompt = build_image_analysis_prompt(
            ocr_text=ocr_text,
            chunk_id=chunk_id,
            material_label=material_label,
            content_language=content_language,
        )
        data_url = "data:%s;base64,%s" % (
            mime,
            base64.b64encode(blob).decode("ascii"),
        )
        payload: dict[str, Any] = {
            "model": self._vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        }
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}
        text = self._post_json(payload, timeout_seconds)
        return self._finish_text(text)

    def _finish_text(self, text: str, *, max_output_chars: int = 8000) -> str:
        """模型输出文本的统一出口检查 (预算 + 200 包错识别)。"""
        if len(text) > max_output_chars:
            # 截断的是**模型输出文本**, 截断后仍须是合法 JSON —— 超长直接
            # 判畸形 (绝不把半截 JSON 塞进校验器)。
            raise AIRequestError(
                "AI response exceeded the output budget",
                detail={"host": self.api_host, "model": self._model},
            )
        # 部分网关把后端报错包在 HTTP 200 里 ({"type":"error",...}):
        # 这不是结构化结果, 必须报可诊断错误, 不能流进 schema 校验器
        # 变成一句含糊的" N of N chunks failed"。
        _raise_for_wrapped_error(text, host=self.api_host, model=self._model)
        return text

    def __repr__(self) -> str:
        # 脱敏: 绝不在 repr 里出现 api_key / 完整 base URL。
        return "%s(host=%r, model=%r, api_key=<redacted>)" % (
            type(self).__name__,
            self.api_host,
            self._model,
        )


def provider_fingerprint(provider: AIProvider, model: str = "") -> str:
    """provider 指纹 (进入 processing identity, 不含任何密钥)。"""
    raw = "%s|%s" % (getattr(provider, "name", "?"), model or getattr(provider, "model", ""))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
