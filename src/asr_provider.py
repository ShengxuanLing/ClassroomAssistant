from __future__ import annotations
import hashlib
from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from src.audio_input import AudioInput
from src.models import Transcript, TranscriptLanguage, TranscriptSegment

class ASRProviderErrorCode(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    UNAVAILABLE = "UNAVAILABLE"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    PROCESSING_ERROR = "PROCESSING_ERROR"
    UNSUPPORTED = "UNSUPPORTED"

# Mapping of error code -> retryable flag
# UNAVAILABLE and PROCESSING_ERROR are retryable;
# INVALID_INPUT, CONFIGURATION_ERROR, UNSUPPORTED are not.
_RETRYABLE_BY_CODE = {
    ASRProviderErrorCode.INVALID_INPUT: False,
    ASRProviderErrorCode.UNAVAILABLE: True,
    ASRProviderErrorCode.CONFIGURATION_ERROR: False,
    ASRProviderErrorCode.PROCESSING_ERROR: True,
    ASRProviderErrorCode.UNSUPPORTED: False,
}


class ASRProviderError(Exception):
    code = "ERROR"

    def __init__(self, message, error_code=None, retryable=None):
        self.message = message
        self.error_code = error_code if error_code is not None else self.code
        if retryable is None:
            retryable = _RETRYABLE_BY_CODE.get(self.error_code, False)
        self.retryable = retryable
        super().__init__(message)

    def to_dict(self):
        return {
            "error_code": self.error_code,
            "message": self.message,
            "retryable": self.retryable,
        }

class ASRInvalidInputError(ASRProviderError):
    code = ASRProviderErrorCode.INVALID_INPUT
    default_message = "Invalid ASR input"

class ASRUnavailableError(ASRProviderError):
    code = ASRProviderErrorCode.UNAVAILABLE
    default_message = "ASR provider unavailable"

class ASRConfigurationError(ASRProviderError):
    code = ASRProviderErrorCode.CONFIGURATION_ERROR
    default_message = "ASR provider configuration error"

class ASRProcessingError(ASRProviderError):
    code = ASRProviderErrorCode.PROCESSING_ERROR
    default_message = "ASR provider processing error"

class ASRUnsupportedError(ASRProviderError):
    code = ASRProviderErrorCode.UNSUPPORTED
    default_message = "Operation not supported"

def error_to_dict(error):
    return error.to_dict()


@dataclass
class ASRProviderConfig:
    provider_name: str = ""
    timeout: Optional[float] = None

    def to_dict(self):
        return {
            "provider_name": self.provider_name,
            "timeout": self.timeout,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            provider_name=data.get("provider_name", ""),
            timeout=data.get("timeout"),
        )


@dataclass(frozen=True)
class ASRProviderCapabilities:
    supports_timestamps: bool = False
    supports_speakers: bool = False
    supports_language: bool = False
    supports_word_timestamps: bool = False

    def to_dict(self):
        return {
            "supports_timestamps": self.supports_timestamps,
            "supports_speakers": self.supports_speakers,
            "supports_language": self.supports_language,
            "supports_word_timestamps": self.supports_word_timestamps,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            supports_timestamps=data.get("supports_timestamps", False),
            supports_speakers=data.get("supports_speakers", False),
            supports_language=data.get("supports_language", False),
            supports_word_timestamps=data.get("supports_word_timestamps", False),
        )

@dataclass
class TranscriptionResult:
    transcript: Optional[Transcript] = None
    errors: tuple = ()

    @property
    def ok(self):
        return self.transcript is not None and not self.errors

    @property
    def segments(self):
        if self.transcript is None:
            return []
        return self.transcript.segments

    def to_dict(self):
        return {
            "transcript": self.transcript.to_dict() if self.transcript else None,
            "errors": list(self.errors),
        }

    @classmethod
    def from_dict(cls, data):
        t = data.get("transcript")
        transcript = Transcript.from_dict(t) if t else None
        errors = tuple(data.get("errors", []))
        return cls(transcript=transcript, errors=errors)

    @classmethod
    def failure(cls, error):
        return cls(transcript=None, errors=(error_to_dict(error),))

class ASRProvider(ABC):
    name: str = ""

    def __init__(self, config=None):
        if config is None:
            config = ASRProviderConfig(provider_name=self.name)
        self._config = config

    def transcribe(self, audio_input):
        raise NotImplementedError

    def capabilities(self):
        return ASRProviderCapabilities()

    def config(self):
        return self._config

    @classmethod
    def default_config(cls):
        return ASRProviderConfig(provider_name=cls.name)


def _stable_mock_transcript(audio_input, language):
    """Deterministic mock transcript mirroring MockTranscriber."""
    lang = TranscriptLanguage.from_string(language)
    path = audio_input.path or ""
    path_hash = hashlib.sha256(path.encode("utf-8")).hexdigest()
    num_segments = (int(path_hash[:2], 16) % 5) + 1
    segments = []
    for i in range(num_segments):
        seg_start = round(i * 10.0 + int(path_hash[2+i*2:4+i*2], 16) % 10 * 0.1, 2)
        seg_end = round(seg_start + 5.0 + int(path_hash[4+i*2:6+i*2], 16) % 5 * 0.1, 2)
        text = "Segment " + str(i+1) + " from " + path
        conf = 0.5 + int(path_hash[6+i*2:8+i*2], 16) % 50 / 100.0
        conf = min(conf, 1.0)
        segments.append(TranscriptSegment(
            start=seg_start, end=seg_end, text=text,
            speaker="", language=lang, confidence=round(conf, 2)
        ))
    material_id = audio_input.material_id or path
    return Transcript(
        material_id=material_id, language=lang, segments=segments,
        metadata={"provider": "mock-asr", "deterministic": True},
    )

_ERROR_CLASS_BY_CODE = {
    ASRProviderErrorCode.INVALID_INPUT: ASRInvalidInputError,
    ASRProviderErrorCode.UNAVAILABLE: ASRUnavailableError,
    ASRProviderErrorCode.CONFIGURATION_ERROR: ASRConfigurationError,
    ASRProviderErrorCode.PROCESSING_ERROR: ASRProcessingError,
    ASRProviderErrorCode.UNSUPPORTED: ASRUnsupportedError,
}


class MockASRProvider(ASRProvider):
    name = "mock-asr"

    def __init__(self, config=None, *, transcript=None, segments=(), language="unknown", fail_with=None, fail_message=""):
        if config is None:
            config = ASRProviderConfig(provider_name=self.name)
        super().__init__(config)
        self._transcript = transcript
        self._segments = tuple(segments)
        self._language = language
        self._fail_code = fail_with
        self._fail_message = fail_message

    def set_failure(self, code, message=None):
        self._fail_code = code
        self._fail_message = message or code.name

    def clear_failure(self):
        self._fail_code = None
        self._fail_message = ""

    def capabilities(self):
        """Mock produces timestamps but does NOT fake speakers or language detection."""
        return ASRProviderCapabilities(supports_timestamps=True)

    def transcribe(self, audio_input):
        if not isinstance(audio_input, AudioInput):
            raise ASRInvalidInputError(
                "ASR provider input must be an AudioInput, got " + type(audio_input).__name__
            )
        if self._fail_code is not None:
            cls = _ERROR_CLASS_BY_CODE[self._fail_code]
            raise cls(self._fail_message or self._fail_code.name)
        if self._transcript is not None:
            return TranscriptionResult(transcript=self._transcript)
        if self._segments:
            lang = TranscriptLanguage.from_string(self._language)
            material_id = audio_input.material_id or audio_input.path
            segs = []
            for d in self._segments:
                seg_lang = d.get("language", self._language)
                if not isinstance(seg_lang, TranscriptLanguage):
                    seg_lang = TranscriptLanguage.from_string(str(seg_lang))
                segs.append(TranscriptSegment(
                    start=float(d.get("start", 0.0)),
                    end=float(d.get("end", 0.0)),
                    text=str(d.get("text", "")),
                    speaker=str(d.get("speaker", "")),
                    language=seg_lang,
                    confidence=float(d.get("confidence", 0.5)),
                ))
            return TranscriptionResult(Transcript(
                material_id=material_id, language=lang, segments=segs,
                metadata={"provider": "mock-asr", "configured": True},
            ))
        return TranscriptionResult(transcript=_stable_mock_transcript(audio_input, self._language))


class ASRTranscriptionEngineAdapter:
    """Adapts an ASRProvider to the legacy TranscriptionEngine API."""

    def __init__(self, provider, language="unknown"):
        self._provider = provider
        self._language = language

    def provider(self):
        return self._provider

    def transcribe(self, audio_path, language=None, audio_input=None):
        if audio_input is None:
            from src.audio_input import AudioInput
            audio_input = AudioInput(path=audio_path or "", extension="")
        lang = self._language if language is None else language
        result = self._provider.transcribe(audio_input)
        if result.transcript is None:
            if result.errors:
                d = result.errors[0]
                code = ASRProviderErrorCode(d.get("error_code", "PROCESSING_ERROR"))
                err = ASRProviderError(d.get("message", ""), error_code=code, retryable=d.get("retryable"))
                raise err
            raise ASRProviderError("ASR provider returned no transcript", error_code=ASRProviderErrorCode.PROCESSING_ERROR)
        return result.transcript

    def transcribe_segments(self, audio_path, segments):
        from src.models import TranscriptSegment, TranscriptLanguage
        result = []
        for seg_data in segments:
            lang = seg_data.get("language", "unknown")
            if not isinstance(lang, TranscriptLanguage):
                lang = TranscriptLanguage.from_string(str(lang))
            result.append(TranscriptSegment(
                start=float(seg_data.get("start", 0.0)),
                end=float(seg_data.get("end", 0.0)),
                text=str(seg_data.get("text", "")),
                speaker=str(seg_data.get("speaker", "")),
                language=lang,
                confidence=float(seg_data.get("confidence", 0.5)),
            ))
        return result

def create_mock_asr_provider(config=None):
    provider = MockASRProvider()
    if config is not None:
        provider._config = ASRProviderConfig.from_dict(config)
    return provider
