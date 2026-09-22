"""Task 18 - Local Whisper ASR Provider tests.

Covers the provider contract, fake-runtime behaviour, model lifecycle,
configuration forwarding, segment conversion, Unicode preservation,
empty results, error mapping, timestamp integrity, speaker policy and
the CPU path.  One marked integration test runs a real tiny model when
faster-whisper and its model are available, and skips cleanly otherwise.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.asr_provider import (
    ASRProvider,
    ASRProviderErrorCode,
    ASRProviderError,
    ASRInvalidInputError,
    ASRConfigurationError,
    ASRProcessingError,
    ASRUnavailableError,
    TranscriptionResult,
)
from src.audio_input import AudioInput, AudioMaterialValidator
from src.models import Transcript, TranscriptLanguage, TranscriptSegment
from src.whisper_provider import (
    DEFAULT_WHISPER_MODEL,
    SUPPORTED_DEVICES,
    SUPPORTED_WHISPER_MODELS,
    LocalWhisperProvider,
    WhisperConfig,
    create_local_whisper_provider,
)

# ----------------------------------------------------------------------
# Fake runtime
# ----------------------------------------------------------------------


class FakeWhisperSegment:
    def __init__(self, text, start, end):
        self.text = text
        self.start = start
        self.end = end


class FakeWhisperModel:
    """Mimics the parts of faster_whisper.WhisperModel the provider uses."""

    def __init__(self, segments=(), fail_transcribe=None):
        self.segments = list(segments)
        self.fail_transcribe = fail_transcribe
        self.calls = []
        self.transcribe_calls = 0

    def transcribe(self, path, **kwargs):
        self.transcribe_calls += 1
        self.calls.append((path, dict(kwargs)))
        if self.fail_transcribe is not None:
            raise self.fail_transcribe
        return iter(self.segments), None


def _audio_input(tmp_path, name="lesson.mp3", material=None):
    path = tmp_path / name
    path.write_bytes(b"fake-audio-bytes")
    validator = AudioMaterialValidator()
    result = validator.validate(str(path))
    assert result.valid, result.to_dict()
    ai = validator.to_audio_input(result)
    if material is not None:
        ai.material = material
    return ai


def _provider(fake, language=None):
    return LocalWhisperProvider(
        WhisperConfig(language=language, device="cpu"),
        model_factory=lambda name, device, compute_type: fake,
    )


def _cpu_only_factory(name, device, compute_type):
    """Emulates a host without CUDA: cuda load must raise."""
    if device == "cuda":
        raise ASRUnavailableError("CUDA requested but unavailable on this host")
    return FakeWhisperModel([])


# ----------------------------------------------------------------------
# 1. Provider contract
# ----------------------------------------------------------------------


class TestProviderContract:
    def test_is_asr_provider(self):
        p = LocalWhisperProvider()
        assert isinstance(p, ASRProvider)
        assert p.name == "local-whisper"

    def test_factory_returns_provider(self):
        p = create_local_whisper_provider()
        assert isinstance(p, LocalWhisperProvider)
        assert isinstance(p, ASRProvider)

    def test_capabilities(self):
        p = LocalWhisperProvider()
        caps = p.capabilities()
        assert caps.supports_timestamps is True
        assert caps.supports_speakers is False
        assert caps.supports_language is False
        assert caps.supports_word_timestamps is False

    def test_capabilities_report_language_when_configured(self):
        p = LocalWhisperProvider(WhisperConfig(language="es"))
        assert p.capabilities().supports_language is True

    def test_transcribe_returns_transcription_result(self, tmp_path):
        p = _provider(FakeWhisperModel([FakeWhisperSegment("hola", 0.0, 1.0)]))
        result = p.transcribe(_audio_input(tmp_path))
        assert isinstance(result, TranscriptionResult)
        assert result.ok
        assert result.transcript is not None

    def test_does_not_expose_runtime_objects(self, tmp_path):
        fake = FakeWhisperModel([FakeWhisperSegment("hola", 0.0, 1.0)])
        p = _provider(fake)
        result = p.transcribe(_audio_input(tmp_path))
        assert all(isinstance(s, TranscriptSegment) for s in result.segments)
        assert not any(isinstance(s, FakeWhisperSegment) for s in result.segments)


# ----------------------------------------------------------------------
# 2. Model loading lifecycle
# ----------------------------------------------------------------------


class TestModelLoadingLifecycle:
    def test_model_loaded_once_across_calls(self, tmp_path):
        fake = FakeWhisperModel([FakeWhisperSegment("a", 0.0, 1.0)])
        loads = []

        def factory(name, device, compute_type):
            loads.append((name, device, compute_type))
            return fake

        p = LocalWhisperProvider(WhisperConfig(), model_factory=factory)
        p.transcribe(_audio_input(tmp_path, "a.mp3"))
        p.transcribe(_audio_input(tmp_path, "b.mp3"))
        assert len(loads) == 1
        assert p.model_load_count == 1

    def test_close_forces_reload_on_next_use(self, tmp_path):
        fake = FakeWhisperModel([FakeWhisperSegment("a", 0.0, 1.0)])
        p = _provider(fake)
        p.transcribe(_audio_input(tmp_path, "a.mp3"))
        assert p.model_load_count == 1
        p.close()
        p.transcribe(_audio_input(tmp_path, "b.mp3"))
        assert p.model_load_count == 2

    def test_lazy_loading_defers_until_first_transcribe(self, tmp_path):
        fake = FakeWhisperModel([FakeWhisperSegment("a", 0.0, 1.0)])
        p = _provider(fake)
        assert p.model_load_count == 0
        assert fake.transcribe_calls == 0
        p.transcribe(_audio_input(tmp_path))
        assert p.model_load_count == 1

    def test_close_is_per_provider(self, tmp_path):
        fake_a = FakeWhisperModel([FakeWhisperSegment("a", 0.0, 1.0)])
        fake_b = FakeWhisperModel([FakeWhisperSegment("b", 0.0, 1.0)])
        p_a = _provider(fake_a)
        p_b = _provider(fake_b)
        p_a.transcribe(_audio_input(tmp_path, "a.mp3"))
        p_b.transcribe(_audio_input(tmp_path, "b.mp3"))
        p_a.close()
        assert p_b.model_load_count == 1
        p_b.transcribe(_audio_input(tmp_path, "c.mp3"))
        assert p_b.model_load_count == 1


# ----------------------------------------------------------------------
# 3. Configuration forwarding
# ----------------------------------------------------------------------


class TestConfigurationForwarding:
    def test_factory_receives_model_device_compute_type(self, tmp_path):
        received = []

        def factory(name, device, compute_type):
            received.append((name, device, compute_type))
            return FakeWhisperModel([FakeWhisperSegment("x", 0.0, 1.0)])

        p = LocalWhisperProvider(
            WhisperConfig(model_name="tiny", device="CPU", compute_type="float32"),
            model_factory=factory,
        )
        p.transcribe(_audio_input(tmp_path))
        assert received == [("tiny", "cpu", "float32")]

    def test_language_forwarded_to_runtime(self, tmp_path):
        fake = FakeWhisperModel([FakeWhisperSegment("x", 0.0, 1.0)])
        p = _provider(fake, language="es")
        p.transcribe(_audio_input(tmp_path))
        assert fake.calls[0][1] == {"language": "es"}

    def test_no_language_means_no_language_kwarg(self, tmp_path):
        fake = FakeWhisperModel([FakeWhisperSegment("x", 0.0, 1.0)])
        p = _provider(fake)
        p.transcribe(_audio_input(tmp_path))
        assert fake.calls[0][1] == {}

    def test_default_config(self):
        cfg = LocalWhisperProvider.default_config()
        assert isinstance(cfg, WhisperConfig)
        assert cfg.model_name == DEFAULT_WHISPER_MODEL
        assert cfg.device == "cpu"
        assert cfg.language is None

    def test_config_separated_from_generic_provider_config(self):
        from src.asr_provider import ASRProviderConfig
        generic = ASRProviderConfig(provider_name="mock-asr")
        assert not hasattr(generic, "model_name")
        assert not hasattr(generic, "compute_type")
        specific = WhisperConfig(model_name="base")
        assert isinstance(specific, ASRProviderConfig)
        assert specific.model_name == "base"

    def test_whisper_config_roundtrip_dict(self):
        cfg = WhisperConfig(model_name="small", device="cuda",
                            compute_type="float16", language="ca")
        clone = WhisperConfig.from_dict(cfg.to_dict())
        assert clone.model_name == "small"
        assert clone.device == "cuda"
        assert clone.compute_type == "float16"
        assert clone.language == "ca"

    def test_invalid_model_name_fails_fast(self):
        with pytest.raises(ASRConfigurationError):
            LocalWhisperProvider(WhisperConfig(model_name="huge"))

    def test_invalid_device_fails_fast(self):
        with pytest.raises(ASRConfigurationError):
            LocalWhisperProvider(WhisperConfig(device="metal"))

    def test_invalid_compute_type_fails_fast(self):
        with pytest.raises(ASRConfigurationError):
            LocalWhisperProvider(WhisperConfig(compute_type="int4"))

    def test_invalid_language_code_fails_fast(self):
        with pytest.raises(ASRConfigurationError):
            WhisperConfig(language="espanol").validate()

    def test_language_normalised_to_lowercase(self):
        cfg = WhisperConfig(language="ES")
        cfg.validate()
        assert cfg.language == "es"

    def test_generic_config_upgraded_to_whisper_config(self, tmp_path):
        from src.asr_provider import ASRProviderConfig
        p = LocalWhisperProvider(
            ASRProviderConfig(provider_name="local-whisper"),
            model_factory=lambda *a: FakeWhisperModel([]),
        )
        assert isinstance(p.config(), WhisperConfig)
        assert p.config().model_name == DEFAULT_WHISPER_MODEL

    def test_supported_sets_expose_options(self):
        assert "tiny" in SUPPORTED_WHISPER_MODELS
        assert "base" in SUPPORTED_WHISPER_MODELS
        assert "cpu" in SUPPORTED_DEVICES
        assert "cuda" in SUPPORTED_DEVICES


# ----------------------------------------------------------------------
# 4. Segment conversion
# ----------------------------------------------------------------------


class TestSegmentConversion:
    def test_segment_mapping(self, tmp_path):
        fake = FakeWhisperModel([
            FakeWhisperSegment("Hola", 0.0, 1.2),
            FakeWhisperSegment("mundo", 1.3, 2.1),
        ])
        p = _provider(fake)
        ai = _audio_input(tmp_path)
        result = p.transcribe(ai)
        assert [s.text for s in result.segments] == ["Hola", "mundo"]
        assert [round(s.start, 2) for s in result.segments] == [0.0, 1.3]
        assert [round(s.end, 2) for s in result.segments] == [1.2, 2.1]
        assert result.transcript.material_id == ai.material_id or ai.path

    def test_text_is_stripped_uniformly(self, tmp_path):
        fake = FakeWhisperModel([FakeWhisperSegment("   hola mundo   ", 0.0, 1.0)])
        p = _provider(fake)
        result = p.transcribe(_audio_input(tmp_path))
        assert result.segments[0].text == "hola mundo"

    def test_segment_chronological_order_preserved(self, tmp_path):
        fake = FakeWhisperModel([
            FakeWhisperSegment("a", 0.0, 1.0),
            FakeWhisperSegment("b", 1.0, 2.0),
            FakeWhisperSegment("c", 2.0, 3.0),
        ])
        p = _provider(fake)
        result = p.transcribe(_audio_input(tmp_path))
        starts = [s.start for s in result.segments]
        assert starts == sorted(starts)
        assert [s.text for s in result.segments] == ["a", "b", "c"]

    def test_speaker_is_never_fabricated(self, tmp_path):
        fake = FakeWhisperModel([
            FakeWhisperSegment("first", 0.0, 1.0),
            FakeWhisperSegment("second", 1.0, 2.0),
        ])
        p = _provider(fake)
        result = p.transcribe(_audio_input(tmp_path))
        for seg in result.segments:
            assert seg.speaker in ("", None)

    def test_language_configured_marks_transcript_language(self, tmp_path):
        fake = FakeWhisperModel([FakeWhisperSegment("hola", 0.0, 1.0)])
        p = _provider(fake, language="es")
        result = p.transcribe(_audio_input(tmp_path))
        assert result.transcript.language == TranscriptLanguage.SPANISH
        assert all(s.language == TranscriptLanguage.SPANISH for s in result.segments)

    def test_no_language_defaults_to_unknown_not_guessing(self, tmp_path):
        fake = FakeWhisperModel([FakeWhisperSegment("hola", 0.0, 1.0)])
        p = _provider(fake)
        result = p.transcribe(_audio_input(tmp_path))
        assert result.transcript.language == TranscriptLanguage.UNKNOWN

    def test_result_metadata_records_provenance(self, tmp_path):
        fake = FakeWhisperModel([FakeWhisperSegment("hola", 0.0, 1.0)])
        p = _provider(fake, language="ca")
        result = p.transcribe(_audio_input(tmp_path))
        meta = result.transcript.metadata
        assert meta["provider"] == "local-whisper"
        assert meta["runtime"] == "faster-whisper"
        assert meta["model"] == DEFAULT_WHISPER_MODEL
        assert meta["device"] == "cpu"
        assert meta["empty_result"] is False


# ----------------------------------------------------------------------
# 5. Unicode preservation
# ----------------------------------------------------------------------


class TestUnicodePreservation:
    def test_spanish_accents_survive(self, tmp_path):
        text = "Estabilidad y función más allá del cálculo."
        fake = FakeWhisperModel([FakeWhisperSegment(text, 0.0, 2.0)])
        p = _provider(fake, language="es")
        result = p.transcribe(_audio_input(tmp_path))
        assert result.segments[0].text == text

    def test_catalan_accents_survive(self, tmp_path):
        text = "Introducció a la teoria dels sistemes."
        fake = FakeWhisperModel([FakeWhisperSegment(text, 0.0, 2.0)])
        p = _provider(fake, language="ca")
        result = p.transcribe(_audio_input(tmp_path))
        assert result.segments[0].text == text

    def test_chinese_text_survives(self, tmp_path):
        text = "这是一个测试。"
        fake = FakeWhisperModel([FakeWhisperSegment(text, 0.0, 2.0)])
        p = _provider(fake, language="zh")
        result = p.transcribe(_audio_input(tmp_path))
        assert result.segments[0].text == text

    def test_mixed_language_text_survives(self, tmp_path):
        text = "El concepto de funció (函数) es clave."
        fake = FakeWhisperModel([FakeWhisperSegment(text, 0.0, 2.0)])
        p = _provider(fake)
        result = p.transcribe(_audio_input(tmp_path))
        assert result.segments[0].text == text

    def test_transcript_serialisation_roundtrip_keeps_unicode(self, tmp_path):
        text = "Àèç ñ ü — 测试"
        fake = FakeWhisperModel([FakeWhisperSegment(text, 0.0, 1.0)])
        p = _provider(fake)
        result = p.transcribe(_audio_input(tmp_path))
        clone = Transcript.from_dict(result.transcript.to_dict())
        assert clone.segments[0].text == text


# ----------------------------------------------------------------------
# 6. Empty results
# ----------------------------------------------------------------------


class TestEmptyResults:
    def test_no_segments_is_legal_empty_result(self, tmp_path):
        fake = FakeWhisperModel([])
        p = _provider(fake)
        result = p.transcribe(_audio_input(tmp_path))
        assert result.ok
        assert result.transcript is not None
        assert result.segments == []
        assert result.transcript.metadata["empty_result"] is True

    def test_whitespace_only_segments_are_filtered(self, tmp_path):
        fake = FakeWhisperModel([
            FakeWhisperSegment("", 0.0, 0.5),
            FakeWhisperSegment("   ", 0.5, 1.0),
            FakeWhisperSegment("real", 1.0, 2.0),
        ])
        p = _provider(fake)
        result = p.transcribe(_audio_input(tmp_path))
        assert [s.text for s in result.segments] == ["real"]
        assert result.transcript.metadata["empty_result"] is False


# ----------------------------------------------------------------------
# 7. Error handling
# ----------------------------------------------------------------------


class TestErrorHandling:
    def test_invalid_input_type_never_reaches_runtime(self, tmp_path):
        fake = FakeWhisperModel([FakeWhisperSegment("x", 0.0, 1.0)])
        p = _provider(fake)
        with pytest.raises(ASRInvalidInputError):
            p.transcribe("not-an-audio-input")
        assert fake.transcribe_calls == 0

    def test_missing_file_raises_unavailable(self, tmp_path):
        p = _provider(FakeWhisperModel([]))
        ai = AudioInput(path=str(tmp_path / "gone.mp3"), extension=".mp3")
        with pytest.raises(ASRUnavailableError) as exc:
            p.transcribe(ai)
        assert exc.value.error_code == ASRProviderErrorCode.UNAVAILABLE

    def test_runtime_failure_raises_processing_error(self, tmp_path):
        fake = FakeWhisperModel(
            [FakeWhisperSegment("x", 0.0, 1.0)],
            fail_transcribe=RuntimeError("boom"),
        )
        p = _provider(fake)
        with pytest.raises(ASRProcessingError) as exc:
            p.transcribe(_audio_input(tmp_path))
        assert exc.value.error_code == ASRProviderErrorCode.PROCESSING_ERROR

    def test_runtime_failure_is_not_an_empty_result(self, tmp_path):
        fake = FakeWhisperModel(
            [FakeWhisperSegment("x", 0.0, 1.0)],
            fail_transcribe=RuntimeError("boom"),
        )
        p = _provider(fake)
        with pytest.raises(ASRProcessingError):
            p.transcribe(_audio_input(tmp_path))

    def test_model_load_failure_raises_unavailable(self, tmp_path):
        def bad_factory(name, device, compute_type):
            raise OSError("model weights missing")

        p = LocalWhisperProvider(WhisperConfig(), model_factory=bad_factory)
        with pytest.raises(ASRUnavailableError) as exc:
            p.transcribe(_audio_input(tmp_path))
        assert exc.value.error_code == ASRProviderErrorCode.UNAVAILABLE

    def test_file_error_from_runtime_maps_to_unavailable(self, tmp_path):
        fake = FakeWhisperModel(
            [], fail_transcribe=FileNotFoundError("no such audio"))
        p = _provider(fake)
        with pytest.raises(ASRUnavailableError):
            p.transcribe(_audio_input(tmp_path))

    def test_asr_provider_errors_pass_through_unwrapped(self, tmp_path):
        class CustomProviderError(ASRProcessingError):
            pass

        fake = FakeWhisperModel(
            [], fail_transcribe=CustomProviderError("custom"))
        p = _provider(fake)
        with pytest.raises(CustomProviderError):
            p.transcribe(_audio_input(tmp_path))

    def test_invalid_timestamps_rejected_not_silently_fixed(self, tmp_path):
        fake = FakeWhisperModel([FakeWhisperSegment("x", -1.0, 2.0)])
        p = _provider(fake)
        with pytest.raises(ASRProcessingError):
            p.transcribe(_audio_input(tmp_path))

    def test_end_before_start_rejected(self, tmp_path):
        fake = FakeWhisperModel([FakeWhisperSegment("x", 5.0, 1.0)])
        p = _provider(fake)
        with pytest.raises(ASRProcessingError):
            p.transcribe(_audio_input(tmp_path))

    def test_deleted_file_raises_stable_error(self, tmp_path):
        p = _provider(FakeWhisperModel([]))
        ai = _audio_input(tmp_path, "delete-me.mp3")
        os.remove(ai.path)
        with pytest.raises(ASRUnavailableError):
            p.transcribe(ai)

    def test_error_to_dict_shape(self):
        err = ASRProcessingError("x")
        d = err.to_dict()
        assert d["error_code"] == "PROCESSING_ERROR"
        assert d["retryable"] is True


# ----------------------------------------------------------------------
# 8. CPU path
# ----------------------------------------------------------------------


class TestCpuPath:
    def test_default_config_is_cpu(self):
        cfg = WhisperConfig()
        cfg.validate()
        assert cfg.device == "cpu"

    def test_cpu_device_accepted(self):
        cfg = WhisperConfig(device="cpu", model_name="tiny")
        cfg.validate()
        assert cfg.validated_device == "cpu"

    def test_gpu_is_optional_not_required(self):
        # cpu must remain a legal path regardless of CUDA presence.
        cfg = WhisperConfig(device="cpu")
        cfg.validate()
        assert cfg.validated_device in SUPPORTED_DEVICES

    def test_cuda_config_without_cuda_raises_unavailable(self, tmp_path):
        # Emulated host without CUDA: the load path must fail clearly.
        p = LocalWhisperProvider(
            WhisperConfig(device="cuda"),
            model_factory=_cpu_only_factory,
        )
        with pytest.raises(ASRUnavailableError):
            p.transcribe(_audio_input(tmp_path, "x.mp3"))


# ----------------------------------------------------------------------
# 9. Real Whisper integration (skips cleanly when runtime/model absent)
# ----------------------------------------------------------------------


class _RealModelWrapper:
    """Adapts a real faster-whisper model to the runtime interface used here."""

    def __init__(self, model):
        self._model = model

    def transcribe(self, path, **kwargs):
        return self._model.transcribe(path, **kwargs)


class TestRealWhisperIntegration:
    """Runs a real tiny-model transcription when the runtime is available."""

    _model = None

    @classmethod
    def setup_class(cls):
        try:
            import faster_whisper
        except ImportError:
            pytest.skip("faster-whisper not installed")
        try:
            cls._model = faster_whisper.WhisperModel(
                "tiny", device="cpu", compute_type="int8")
        except Exception as exc:
            pytest.skip("tiny model unavailable: %s" % exc)

    def test_real_tiny_model_runs_end_to_end(self, tmp_path):
        # 1 second of silence at 16 kHz mono: the model still runs the
        # full decode loop and returns a valid TranscriptionResult.
        import struct
        import wave
        wav_path = tmp_path / "silence.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            for _i in range(16000):
                wf.writeframes(struct.pack("<h", 0))

        p = LocalWhisperProvider(
            WhisperConfig(model_name="tiny", device="cpu", compute_type="int8"),
            model_factory=lambda *a, **k: _RealModelWrapper(self._model),
        )
        validator = AudioMaterialValidator()
        result = validator.validate(str(wav_path))
        assert result.valid
        out = p.transcribe(validator.to_audio_input(result))
        assert out.ok
        assert out.transcript is not None
        for seg in out.segments:
            assert seg.end >= seg.start >= 0
            assert isinstance(seg.text, str)
