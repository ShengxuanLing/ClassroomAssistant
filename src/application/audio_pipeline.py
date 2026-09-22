# -*- coding: utf-8 -*-
"""音频处理链适配器 (Task 37)。

把 Task 18 已经完成的两块能力接进摄取流水线, 而不改动 domain 层:

    Audio -> LongAudioProcessor -> ASR (Whisper) -> Transcript
          -> TranscriptQualityValidator -> Evidence

两个适配器都实现 ``ASRProvider`` 的 ``transcribe(audio_input)`` 协议,
因此 :class:`~src.evidence_ingestion.AudioExtractorAdapter` 可以原样使用
它们, 无需任何领域层修改。

硬性原则:
- 适配器**只做编排与诊断**, 绝不改写转录文本。
- 长音频切块由 LongAudioProcessor 负责; 它失败时如实向上抛结构化错误,
  不返回"部分完成的转录"。
- 质量校验是只读诊断: 报告被记录并随作业一起暴露给用户, 但不会删除
  证据、不会自动修正内容、不会自动确认任何结论。
"""

from __future__ import annotations

from typing import Any, Optional

from src.asr_provider import (
    ASRProcessingError,
    ASRProvider,
    ASRProviderError,
    TranscriptionResult,
)
from src.audio_segmentation import LongAudioConfig, LongAudioProcessor
from src.transcript_quality import TranscriptQualityValidator

__all__ = [
    "LongAudioASRProvider",
    "QualityCheckedASRProvider",
    "build_audio_chain",
]


class LongAudioASRProvider(ASRProvider):
    """把任意 ASRProvider 包成"长音频安全"的 provider。

    - 通过 LongAudioProcessor 做确定性切块 + 顺序转录, 最后重映射回原始
      全局时间轴。
    - ``duration_provider`` 不可用时 (例如 PyAV 缺失 / 音频无法解析)
      会降级为直接调用内层 provider, 并记录一条 warning —— 降级是显式的,
      不会假装切块成功。
    """

    name = "long-audio"

    def __init__(
        self,
        inner: Any,
        *,
        duration_provider: Optional[Any] = None,
        config: Optional[LongAudioConfig] = None,
    ) -> None:
        if inner is None:
            raise ValueError("inner ASR provider is required")
        super().__init__(config=getattr(inner, "_config", None))
        self._inner = inner
        self._config = config or LongAudioConfig()
        self._duration_provider = duration_provider
        self._processor: Optional[LongAudioProcessor] = None
        self._degraded = False
        self.warnings: list[str] = []

    @property
    def inner(self) -> Any:
        return self._inner

    @property
    def degraded(self) -> bool:
        """是否已降级为"不切块"模式。"""
        return self._degraded

    @property
    def processor(self) -> Optional[LongAudioProcessor]:
        return self._processor

    def _ensure_processor(self) -> Optional[LongAudioProcessor]:
        if self._processor is not None:
            return self._processor
        if self._duration_provider is None:
            try:
                from src.audio_segmentation import PyAvDurationProvider

                self._duration_provider = PyAvDurationProvider()
            except Exception:  # noqa: BLE001 - 可选运行时依赖
                self._degraded = True
                self.warnings.append("DURATION_PROVIDER_UNAVAILABLE")
                return None
        try:
            self._processor = LongAudioProcessor(
                self._inner, self._duration_provider, self._config
            )
        except Exception:  # noqa: BLE001 - 配置/运行时问题
            self._degraded = True
            self.warnings.append("SEGMENTATION_UNAVAILABLE")
            return None
        return self._processor

    def transcribe(self, audio_input: Any) -> TranscriptionResult:
        processor = self._ensure_processor()
        if processor is None:
            return self._inner.transcribe(audio_input)
        try:
            return processor.process(audio_input)
        except ASRProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - 适配器边界
            raise ASRProcessingError(
                f"long-audio transcription failed: {type(exc).__name__}"
            ) from exc

    def capabilities(self) -> Any:
        return self._inner.capabilities()


def build_audio_chain(
    inner: Any,
    *,
    long_audio: bool = True,
    duration_provider: Optional[Any] = None,
    config: Optional[LongAudioConfig] = None,
    validator: Optional[TranscriptQualityValidator] = None,
    source_duration: Optional[float] = None,
) -> "QualityCheckedASRProvider":
    """组装标准的音频处理链::

        ASR -> (LongAudioProcessor) -> TranscriptQualityValidator

    返回最外层的 :class:`QualityCheckedASRProvider`; 它同时是注入给
    ``EvidenceIngestionService(asr_provider=...)`` 的对象, 也是
    :class:`~src.application.processing_service.ClassroomProcessingService`
    的 ``quality_source``。这样"用哪条链"只有一处定义。
    """
    provider = inner
    if long_audio:
        provider = LongAudioASRProvider(
            inner, duration_provider=duration_provider, config=config
        )
    return QualityCheckedASRProvider(
        provider, validator=validator, source_duration=source_duration
    )


class QualityCheckedASRProvider(ASRProvider):
    """在转录之后追加只读的转录质量诊断。

    语义:
    - 转录失败: 原样向上传播 (质量校验不会掩盖真实失败)。
    - 转录成功: 运行 TranscriptQualityValidator, 把报告记录到
      ``reports``, 并在 ``report_for_material(material_id)`` 中可查询。
    - **绝不**因为质量分数低就丢弃或改写转录内容。
    """

    name = "quality-checked"

    def __init__(
        self,
        inner: Any,
        *,
        validator: Optional[TranscriptQualityValidator] = None,
        source_duration: Optional[float] = None,
    ) -> None:
        if inner is None:
            raise ValueError("inner ASR provider is required")
        super().__init__(config=getattr(inner, "_config", None))
        self._inner = inner
        self._validator = validator or TranscriptQualityValidator(
            source_duration=source_duration
        )
        self.reports: list[Any] = []
        self.reports_by_material: dict[str, list[Any]] = {}

    @property
    def inner(self) -> Any:
        return self._inner

    def transcribe(self, audio_input: Any) -> TranscriptionResult:
        result = self._inner.transcribe(audio_input)
        if not isinstance(result, TranscriptionResult):
            return result
        if result.transcript is None:
            return result
        try:
            report = self._validator.validate(result.transcript)
        except Exception:  # noqa: BLE001 - 诊断失败绝不能破坏转录
            return result
        self.reports.append(report)
        material_id = getattr(audio_input, "material_id", None)
        if material_id:
            self.reports_by_material.setdefault(str(material_id), []).append(report)
        return result

    def report_for(self, index: int = -1) -> Optional[Any]:
        if not self.reports:
            return None
        try:
            return self.reports[index]
        except IndexError:
            return None

    def report_for_material(self, material_id: str) -> Optional[Any]:
        """某个材料的最近一次质量报告 (无则 None)。"""
        reports = self.reports_by_material.get(str(material_id))
        return reports[-1] if reports else None

    def latest_report(self) -> Optional[Any]:
        return self.reports[-1] if self.reports else None

    def capabilities(self) -> Any:
        return self._inner.capabilities()
