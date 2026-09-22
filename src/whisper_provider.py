"""Local Whisper ASR provider (Task 18).

First real ASR provider on top of the Task 17 ASRProvider contract.

Selected runtime: faster-whisper (CTranslate2).
The model is loaded once per provider instance (lazy on first transcribe).
Whisper-specific configuration lives in WhisperConfig (extends ASRProviderConfig).
No VAD, diarization, chunking, LLM or cloud calls (Task 18 scope only).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

from src.asr_provider import (
    ASRProvider, ASRProviderConfig, ASRProviderErrorCode,
    ASRInvalidInputError, ASRConfigurationError, ASRProcessingError,
    ASRUnavailableError, ASRProviderError, TranscriptionResult,
)
from src.audio_input import AudioInput
from src.models import Transcript, TranscriptLanguage, TranscriptSegment

logger = logging.getLogger(__name__)

SUPPORTED_WHISPER_MODELS: tuple = (
    "tiny", "base", "small", "medium",
    "large-v1", "large-v2", "large-v3", "distil-large-v3",
)
SUPPORTED_DEVICES: tuple = ("cpu", "cuda")
SUPPORTED_COMPUTE_TYPES: tuple = ("int8", "float16", "float32", "int8_float16")
DEFAULT_WHISPER_MODEL = "base"

# ISO 639-1/639-2 codes accepted for the explicit language setting.
# Whisper accepts many codes; we map the ones the project models into
# its TranscriptLanguage enum and leave the rest as UNKNOWN so that
# transcription text is never silently re-labelled.
LANGUAGE_CODE_TO_TRANSCRIPT = {
    "es": "Spanish",
    "ca": "Catalan",
    "zh": "Chinese",
    "en": "English",
}


class WhisperConfig(ASRProviderConfig):
    """Whisper-specific configuration extending the generic ASRProviderConfig."""

    def __init__(self, model_name: str = DEFAULT_WHISPER_MODEL, device: str = "cpu",
                 compute_type: str = "int8", language: Optional[str] = None,
                 provider_name: str = "local-whisper", timeout: Optional[float] = None) -> None:
        super().__init__(provider_name=provider_name, timeout=timeout)
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.language = language

    def to_dict(self) -> dict:
        base = {"provider_name": self.provider_name, "timeout": self.timeout}
        base.update(model_name=self.model_name, device=self.device,
                    compute_type=self.compute_type, language=self.language)
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "WhisperConfig":
        return cls(model_name=data.get("model_name", DEFAULT_WHISPER_MODEL),
                   device=data.get("device", "cpu"),
                   compute_type=data.get("compute_type", "int8"),
                   language=data.get("language"),
                   provider_name=data.get("provider_name", "local-whisper"),
                   timeout=data.get("timeout"))

    def validate(self) -> None:
        """Fail fast on invalid model/device/compute_type/language."""
        if self.model_name not in SUPPORTED_WHISPER_MODELS:
            raise ASRConfigurationError(
                "Unsupported whisper model_name: %r. Supported: %s"
                % (self.model_name, ", ".join(SUPPORTED_WHISPER_MODELS)))
        device = (self.device or "").lower()
        if device not in SUPPORTED_DEVICES:
            raise ASRConfigurationError(
                "Unsupported device: %r. Supported: %s"
                % (self.device, ", ".join(SUPPORTED_DEVICES)))
        self.device = device
        if self.compute_type not in SUPPORTED_COMPUTE_TYPES:
            raise ASRConfigurationError(
                "Unsupported compute_type: %r. Supported: %s"
                % (self.compute_type, ", ".join(SUPPORTED_COMPUTE_TYPES)))
        if self.language is not None:
            lang = str(self.language).strip().lower()
            if not (2 <= len(lang) <= 3 and lang.isalpha()):
                raise ASRConfigurationError(
                    "Invalid language code: %r (expected ISO 639-1/639-2 code)"
                    % (self.language,))
            self.language = lang

    @property
    def validated_device(self) -> str:
        """Device lower-cased as validated (call validate() first)."""
        return (self.device or "").lower()


class LocalWhisperProvider(ASRProvider):
    """Local faster-whisper ASR provider (Task 18)."""

    name = "local-whisper"

    def __init__(self, config: Optional[WhisperConfig] = None,
                 *, model_factory: Optional[Any] = None) -> None:
        if config is None:
            config = WhisperConfig(provider_name=self.name)
        elif not isinstance(config, WhisperConfig):
            config = WhisperConfig(
                provider_name=getattr(config, "provider_name", self.name),
                timeout=getattr(config, "timeout", None),
            )
        super().__init__(config)
        self._whisper_config = config
        self._whisper_config.validate()
        self._model_factory = model_factory or _default_model_factory
        self._model: Optional[Any] = None
        self._model_lock = threading.Lock()
        self.model_load_count = 0

    # Generic ASRProvider surface
    def capabilities(self):
        from src.asr_provider import ASRProviderCapabilities
        return ASRProviderCapabilities(
            supports_timestamps=True,
            supports_speakers=False,
            supports_language=self._whisper_config.language is not None,
            supports_word_timestamps=False,
        )

    def config(self):
        return self._whisper_config

    @classmethod
    def default_config(cls) -> WhisperConfig:
        return WhisperConfig(provider_name=cls.name)

    # Model lifecycle
    def _load_model_once(self) -> Any:
        """Load the model exactly once for this provider instance."""
        with self._model_lock:
            if self._model is None:
                cfg = self._whisper_config
                try:
                    self._model = self._model_factory(
                        cfg.model_name, cfg.validated_device, cfg.compute_type)
                except (ASRConfigurationError, ASRUnavailableError):
                    raise
                except Exception as exc:
                    raise ASRUnavailableError(
                        "Failed to load whisper model %r on %s/%s: %s"
                        % (cfg.model_name, cfg.validated_device, cfg.compute_type, exc)
                    ) from exc
                self.model_load_count += 1
            return self._model

    def close(self) -> None:
        """Release the loaded model. Not part of the ASRProvider contract."""
        with self._model_lock:
            self._model = None

    # Transcription
    def transcribe(self, audio_input: AudioInput) -> TranscriptionResult:
        if not isinstance(audio_input, AudioInput):
            raise ASRInvalidInputError(
                "ASR provider input must be an AudioInput, got %s"
                % type(audio_input).__name__)
        path = getattr(audio_input, "path", "") or ""
        if not path:
            raise ASRInvalidInputError("AudioInput has no audio path")

        model = self._load_model_once()

        if not os.path.exists(path):
            raise ASRUnavailableError("Audio file no longer exists: %s" % path)

        cfg = self._whisper_config
        kwargs: dict = {}
        if cfg.language is not None:
            kwargs["language"] = cfg.language
        try:
            segments_iter, _info = model.transcribe(path, **kwargs)
        except ASRProviderError:
            raise
        except FileNotFoundError as exc:
            raise ASRUnavailableError("Audio file missing or unreadable: %s" % exc) from exc
        except Exception as exc:
            raise ASRProcessingError(
                "Whisper transcription failed for %s: %s" % (path, exc)) from exc

        return self._build_result(segments_iter, audio_input)

    def _build_result(self, segments, audio_input: AudioInput) -> TranscriptionResult:
        cfg = self._whisper_config
        lang = TranscriptLanguage.from_string(LANGUAGE_CODE_TO_TRANSCRIPT.get(cfg.language, "unknown")) if cfg.language else None
        material_id = getattr(audio_input, "material_id", None) or audio_input.path
        converted: list = []
        seen_any = False
        for seg in segments:
            text = str(getattr(seg, "text", "")).strip()
            start = float(getattr(seg, "start", 0.0) or 0.0)
            end = float(getattr(seg, "end", 0.0) or 0.0)
            if start < 0 or end < start:
                raise ASRProcessingError(
                    "Whisper returned invalid timestamps start=%r end=%r" % (start, end))
            if not text:
                continue
            seen_any = True
            converted.append(TranscriptSegment(
                start=start, end=end, text=text, speaker="",
                language=lang if lang else TranscriptLanguage.UNKNOWN,
                confidence=1.0,
            ))
        transcript = Transcript(
            material_id=material_id,
            language=lang if lang else TranscriptLanguage.UNKNOWN,
            segments=converted,
            metadata={
                "provider": self.name,
                "runtime": "faster-whisper",
                "model": cfg.model_name,
                "device": cfg.validated_device,
                "empty_result": not seen_any,
            },
        )
        return TranscriptionResult(transcript=transcript)


def _default_model_factory(model_name: str, device: str, compute_type: str) -> Any:
    """Create a real faster-whisper model (lazy import, no module-level dep)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ASRConfigurationError(
            "faster-whisper runtime is not installed: %s" % exc) from exc

    if device == "cuda":
        try:
            import torch
            if not torch.cuda.is_available():
                raise ASRUnavailableError(
                    "CUDA requested but torch.cuda.is_available() is False")
        except ASRUnavailableError:
            raise
        except Exception as exc:
            raise ASRUnavailableError(
                "CUDA device requested but availability check failed: %s" % exc)
    return WhisperModel(model_name, device=device, compute_type=compute_type)


def create_local_whisper_provider(
        config: Optional[WhisperConfig] = None,
        *, model_factory: Optional[Any] = None) -> LocalWhisperProvider:
    """Factory mirroring create_mock_asr_provider."""
    return LocalWhisperProvider(config=config, model_factory=model_factory)
