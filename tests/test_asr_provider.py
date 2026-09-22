"""Task 17 - ASR Provider interface tests.

Covers the provider contract, mock behaviour, error model, capabilities,
determinism, provider replacement, dependency boundaries and the legacy
adapter.  No network access and no real ASR SDKs are used anywhere.
"""

import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.audio_input import AudioInput, AudioMaterialValidator
from src.asr_provider import (
    ASRProvider,
    ASRProviderConfig,
    ASRProviderErrorCode,
    ASRProviderError,
    ASRUnavailableError,
    ASRProcessingError,
    ASRConfigurationError,
    ASRInvalidInputError,
    ASRUnsupportedError,
    ASRTranscriptionEngineAdapter,
    MockASRProvider,
    TranscriptionResult,
    create_mock_asr_provider,
)
from src.models import Transcript, TranscriptLanguage, TranscriptSegment


def _validated_audio_input(tmp_path, name="lesson.mp3"):
    path = tmp_path / name
    path.write_bytes(b"fake-audio-bytes")
    validator = AudioMaterialValidator()
    result = validator.validate(str(path))
    assert result.valid, result.to_dict()
    return validator.to_audio_input(result)


def _configured_segments():
    return [
        {
            "start": 1.0,
            "end": 2.5,
            "text": "hola mundo",
            "speaker": "teacher",
            "language": "spanish",
            "confidence": 0.9,
        },
        {
            "start": 3.0,
            "end": 4.0,
            "text": "bon dia",
            "speaker": "",
            "language": "catalan",
            "confidence": 0.7,
        },
    ]


class TestProviderInterface:
    """Test 1 - provider contract and base class behaviour."""

    def test_mock_provider_satisfies_contract(self):
        provider = MockASRProvider()
        assert isinstance(provider, ASRProvider)
        assert provider.name == "mock-asr"
        assert provider.capabilities().supports_timestamps is True
        assert provider.capabilities().supports_speakers is False
        assert isinstance(provider.config(), ASRProviderConfig)

    def test_provider_replaceable_by_protocol(self):
        class ProviderA(ASRProvider):
            name = "provider-a"

            def transcribe(self, audio_input):
                t = Transcript(
                    material_id=audio_input.material_id or audio_input.path,
                    language=TranscriptLanguage.UNKNOWN,
                    segments=[TranscriptSegment(start=0.0, end=1.0, text="a")],
                )
                return TranscriptionResult(transcript=t)

        class ProviderB(ASRProvider):
            name = "provider-b"

            def transcribe(self, audio_input):
                t = Transcript(
                    material_id=audio_input.material_id or audio_input.path,
                    language=TranscriptLanguage.UNKNOWN,
                    segments=[TranscriptSegment(start=0.0, end=2.0, text="b")],
                )
                return TranscriptionResult(transcript=t)

        audio = AudioInput(path="same.wav", extension=".wav")
        result_a = ProviderA().transcribe(audio)
        result_b = ProviderB().transcribe(audio)
        assert result_a.segments[0].text == "a"
        assert result_b.segments[0].text == "b"

    def test_base_provider_transcribe_not_implemented(self):
        class BareProvider(ASRProvider):
            name = "bare"

        with pytest.raises(NotImplementedError):
            BareProvider().transcribe(AudioInput(path="x.wav"))

    def test_base_default_config(self):
        assert MockASRProvider.default_config().provider_name == "mock-asr"


class TestAudioInputAccepted:
    """Test 2 - validated AudioInput flows through the mock provider."""

    def test_validated_audio_input(self, tmp_path):
        audio = _validated_audio_input(tmp_path)
        provider = MockASRProvider()
        result = provider.transcribe(audio)
        assert isinstance(result, TranscriptionResult)
        assert result.ok is True
        assert result.transcript.material_id == (audio.material_id or audio.path)

    def test_provider_rejects_non_audio_input(self):
        provider = MockASRProvider()
        with pytest.raises(ASRInvalidInputError):
            provider.transcribe("not-an-audio-input")


class TestResultType:
    """Test 3 - provider returns TranscriptionResult, not dict."""

    def test_result_is_dataclass_not_dict(self):
        provider = MockASRProvider()
        result = provider.transcribe(AudioInput(path="a.wav", extension=".wav"))
        assert isinstance(result, TranscriptionResult)
        assert not isinstance(result, dict)
        data = result.to_dict()
        restored = TranscriptionResult.from_dict(data)
        assert isinstance(restored, TranscriptionResult)


class TestSegmentPreservation:
    """Test 4 - configured segments round-trip through the provider."""

    def test_segment_fields_preserved(self):
        provider = MockASRProvider(segments=_configured_segments())
        audio = AudioInput(path="seg.mp3", extension=".mp3")
        result = provider.transcribe(audio)
        assert result.ok is True
        first, second = result.segments
        assert first.text == "hola mundo"
        assert first.start == 1.0
        assert first.end == 2.5
        assert first.speaker == "teacher"
        assert first.language is TranscriptLanguage.SPANISH
        assert first.confidence == 0.9
        assert second.text == "bon dia"
        assert second.speaker == ""
        assert second.language is TranscriptLanguage.CATALAN
        assert second.confidence == 0.7

    def test_preconfigured_transcript_returned(self):
        transcript = Transcript(
            material_id="m-1",
            language=TranscriptLanguage.SPANISH,
            segments=[TranscriptSegment(start=0.0, end=1.0, text="x")],
        )
        provider = MockASRProvider(transcript=transcript)
        result = provider.transcribe(AudioInput(path="p.wav"))
        assert result.transcript is transcript


class TestEmptyTranscript:
    """Test 5 - a transcript without segments is a valid result."""

    def test_empty_segments_are_ok(self):
        provider = MockASRProvider(transcript=Transcript(material_id="m0"))
        result = provider.transcribe(AudioInput(path="e.wav"))
        assert result.ok is True
        assert result.segments == []

    def test_no_segments_no_exception(self):
        provider = MockASRProvider(
            transcript=Transcript(material_id="m-empty")
        )
        result = provider.transcribe(AudioInput(path="e2.wav"))
        assert result.ok is True
        assert len(result.segments) == 0


class TestProviderError:
    """Test 6 - provider errors are raised consistently."""

    @pytest.mark.parametrize(
        "code, exc_type",
        [
            (ASRProviderErrorCode.INVALID_INPUT, ASRInvalidInputError),
            (ASRProviderErrorCode.PROCESSING_ERROR, ASRProcessingError),
            (ASRProviderErrorCode.UNAVAILABLE, ASRUnavailableError),
        ],
    )
    def test_failure_simulation(self, code, exc_type):
        provider = MockASRProvider(fail_with=code, fail_message="boom")
        with pytest.raises(exc_type) as exc_info:
            provider.transcribe(AudioInput(path="f.wav"))
        assert exc_info.value.error_code is code
        assert exc_info.value.message == "boom"
        assert isinstance(exc_info.value, ASRProviderError)

    def test_set_and_clear_failure(self):
        provider = MockASRProvider()
        provider.set_failure(ASRProviderErrorCode.UNAVAILABLE, "down")
        with pytest.raises(ASRUnavailableError):
            provider.transcribe(AudioInput(path="g.wav"))
        provider.clear_failure()
        assert provider.transcribe(AudioInput(path="g.wav")).ok is True

    def test_error_structured_dict(self):
        provider = MockASRProvider(
            fail_with=ASRProviderErrorCode.PROCESSING_ERROR,
            fail_message="internal",
        )
        with pytest.raises(ASRProviderError) as exc_info:
            provider.transcribe(AudioInput(path="h.wav"))
        data = exc_info.value.to_dict()
        assert data["error_code"] is ASRProviderErrorCode.PROCESSING_ERROR
        assert data["retryable"] is True
        assert data["message"] == "internal"


class TestErrorClassification:
    """Test 7 - retryable flags follow the documented mapping."""

    @pytest.mark.parametrize(
        "exc, retryable",
        [
            (ASRInvalidInputError("m"), False),
            (ASRConfigurationError("m"), False),
            (ASRUnavailableError("m"), True),
            (ASRProcessingError("m"), True),
            (ASRUnsupportedError("m"), False),
        ],
    )
    def test_retryable_flags(self, exc, retryable):
        assert exc.retryable is retryable
        assert exc.error_code is exc.code


class TestDeterminism:
    """Test 8 - identical inputs produce identical outputs."""

    def test_same_input_same_result(self):
        audio = AudioInput(path="det.mp3", extension=".mp3")
        provider = MockASRProvider(language="spanish")
        first = provider.transcribe(audio).transcript
        second = MockASRProvider(language="spanish").transcribe(audio).transcript
        assert first.to_dict() == second.to_dict()

    def test_stable_mock_segments_within_bounds(self):
        result = MockASRProvider().transcribe(AudioInput(path="stable.ogg"))
        for segment in result.segments:
            assert segment.start >= 0.0
            assert segment.end >= segment.start
            assert 0.5 <= segment.confidence <= 1.0


class TestCapabilityConsistency:
    """Test 10 - capabilities match provider behaviour honestly."""

    def test_no_speakers_fabricated(self):
        result = MockASRProvider().transcribe(AudioInput(path="cap.wav"))
        assert result.transcript.metadata["provider"] == "mock-asr"
        for segment in result.segments:
            assert segment.speaker == ""

    def test_timestamps_supported(self):
        result = MockASRProvider().transcribe(AudioInput(path="cap2.wav"))
        caps = MockASRProvider().capabilities()
        assert caps.supports_timestamps is True
        assert all(s.end >= s.start for s in result.segments)


class TestNoExternalDependencies:
    """Test 11 + 12 - no network, no real ASR SDKs imported."""

    FORBIDDEN = (
        "whisper",
        "faster_whisper",
        "torch",
        "torchaudio",
        "openai",
        "google",
        "azure",
        "boto3",
        "requests",
        "socket",
    )

    def test_module_has_no_forbidden_imports(self):
        import src.asr_provider as provider_module

        source = inspect.getsource(provider_module)
        for forbidden in self.FORBIDDEN:
            assert (
                forbidden not in source
            ), "module source mentions forbidden dependency: " + forbidden


class TestErrorLeakage:
    """Error messages must not leak credential-like configuration."""

    def test_messages_are_neutral(self):
        for exc in (
            ASRInvalidInputError("m"),
            ASRConfigurationError("m"),
            ASRUnavailableError("m"),
            ASRProcessingError("m"),
            ASRUnsupportedError("m"),
        ):
            text = str(exc).lower()
            for secret in ("api_key", "apikey", "secret", "password", "token"):
                assert secret not in text


class TestLegacyAdapter:
    """Adapter mirrors MockTranscriber semantics for upper layers."""

    def test_adapter_transcribe_returns_transcript(self, tmp_path):
        audio = _validated_audio_input(tmp_path, "adapter.mp3")
        adapter = ASRTranscriptionEngineAdapter(MockASRProvider())
        transcript = adapter.transcribe(str(audio.path))
        assert isinstance(transcript, Transcript)
        assert len(transcript.segments) >= 1

    def test_adapter_matches_mock_transcriber_segments(self):
        from src.transcription import MockTranscriber

        mock_engine = MockTranscriber()
        reference = mock_engine.transcribe_segments(
            "x.wav",
            [
                {"start": 1.0, "end": 2.0, "text": "uno", "language": "spanish"},
                {"text": "dos"},
            ],
        )
        adapter = ASRTranscriptionEngineAdapter(MockASRProvider())
        actual = adapter.transcribe_segments(
            "x.wav",
            [
                {"start": 1.0, "end": 2.0, "text": "uno", "language": "spanish"},
                {"text": "dos"},
            ],
        )
        assert [s.to_dict() for s in actual] == [s.to_dict() for s in reference]

    def test_adapter_raises_structured_error(self):
        adapter = ASRTranscriptionEngineAdapter(
            MockASRProvider(fail_with=ASRProviderErrorCode.UNAVAILABLE)
        )
        with pytest.raises(ASRProviderError) as exc_info:
            adapter.transcribe("x.wav")
        assert exc_info.value.error_code is ASRProviderErrorCode.UNAVAILABLE
        assert exc_info.value.retryable is True


class TestProcessorIntegration:
    """Provider injection into ClassSessionProcessor is minimal and compatible."""

    def test_asr_provider_injection(self, tmp_path):
        from src.models import Material, MaterialType
        from src.processor import ClassSessionProcessor

        audio_path = tmp_path / "injected.mp3"
        audio_path.write_bytes(b"fake")
        processor = ClassSessionProcessor(asr_provider=MockASRProvider())
        material = Material(path=str(audio_path), material_type=MaterialType.AUDIO)
        evidence = processor.process_materials([material])
        assert len(evidence) == 1
        assert isinstance(processor._transcription_engine, ASRTranscriptionEngineAdapter)

    def test_default_and_legacy_engines_unchanged(self):
        from src.processor import ClassSessionProcessor
        from src.transcription import MockTranscriber

        default_engine = ClassSessionProcessor()
        assert isinstance(default_engine._transcription_engine, MockTranscriber)
        legacy = ClassSessionProcessor(transcription_engine=MockTranscriber())
        assert isinstance(legacy._transcription_engine, MockTranscriber)


class TestFactory:
    def test_create_mock_asr_provider(self):
        provider = create_mock_asr_provider({"provider_name": "test-prov", "timeout": 2.0})
        assert provider.name == "mock-asr"
        assert provider.config().provider_name == "test-prov"
        assert provider.config().timeout == 2.0
        plain = create_mock_asr_provider()
        assert plain.name == "mock-asr"
