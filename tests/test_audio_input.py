"""Tests for Task 16 — audio material input validation layer."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, r"D:\Project\Clases")

from src.models import Material, MaterialType
from src.audio_input import (
    AudioInput,
    AudioMaterialValidator,
    AudioValidationError,
    AudioValidationResult,
    AudioValidationStatus,
    SUPPORTED_AUDIO_EXTENSIONS,
)


@pytest.fixture()
def validator():
    return AudioMaterialValidator()


@pytest.fixture()
def workdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _write(path, data=b"\x00\x01\x02"):
    with open(path, "wb") as f:
        f.write(data)
    return path


class TestSupportedExtensions:
    def test_extension_set_matches_existing_definition(self, validator):
        assert set(validator.supported_extensions) == {".mp3", ".m4a", ".wav", ".ogg"}

    def test_module_constant_is_authoritative(self, validator):
        assert validator.supported_extensions == SUPPORTED_AUDIO_EXTENSIONS

    @pytest.mark.parametrize("ext", [".mp3", ".m4a", ".wav", ".ogg"])
    def test_each_supported_extension_passes(self, validator, workdir, ext):
        p = _write(os.path.join(workdir, "lecture" + ext))
        result = validator.validate(p)
        assert result.valid is True
        assert result.status is AudioValidationStatus.VALID
        assert result.errors == ()
        assert result.extension == ext

    @pytest.mark.parametrize("name", [
        "LECTURE.MP3", "lecture.Mp3", "Lecture.WAV", "LECTURE.OGG", "X.M4A",
    ])
    def test_extension_case_insensitive(self, validator, workdir, name):
        p = _write(os.path.join(workdir, name))
        result = validator.validate(p)
        assert result.valid is True, [e.value for e in result.errors]
        assert os.path.basename(result.path) == name
        assert result.extension in SUPPORTED_AUDIO_EXTENSIONS

    @pytest.mark.parametrize("ext", [".txt", ".pdf", ".jpg", ".docx", ".mp4", ""])
    def test_unsupported_extension_rejected(self, validator, workdir, ext):
        name = "lecture" + ext
        p = _write(os.path.join(workdir, name))
        result = validator.validate(p)
        assert result.valid is False
        assert result.status is AudioValidationStatus.INVALID
        assert AudioValidationError.UNSUPPORTED_EXTENSION in result.errors


class TestMissingAndInvalidPaths:
    def test_missing_file_rejected_with_stable_code(self, validator, workdir):
        missing = os.path.join(workdir, "does_not_exist.mp3")
        result = validator.validate(missing)
        assert result.valid is False
        assert result.status is AudioValidationStatus.INVALID
        assert result.errors == (AudioValidationError.FILE_NOT_FOUND,)

    def test_directory_rejected_even_with_audio_name(self, validator, workdir):
        d = os.path.join(workdir, "lecture.mp3")
        os.makedirs(d)
        result = validator.validate(d)
        assert result.valid is False
        assert AudioValidationError.NOT_A_FILE in result.errors

    def test_missing_subdirectory_rejected(self, validator, workdir):
        result = validator.validate(os.path.join(workdir, "sub", "a.mp3"))
        assert result.valid is False
        assert result.errors == (AudioValidationError.FILE_NOT_FOUND,)

    def test_empty_file_rejected(self, validator, workdir):
        p = os.path.join(workdir, "empty.mp3")
        _write(p, b"")
        result = validator.validate(p)
        assert result.valid is False
        assert AudioValidationError.EMPTY_FILE in result.errors


class TestUnicodeAndMetadata:
    @pytest.mark.parametrize("name", [
        "Vorlesung_Systemtheorie.mp3",
        "Vorlesung_第1课.mp3",
        "Introducció a la teoria de sistemes.wav",
        "Introducción a la teoría de sistemas.mp3",
        "Sessió_2_català.M4A",
    ])
    def test_unicode_filename_validated_and_preserved(self, validator, workdir, name):
        p = _write(os.path.join(workdir, name))
        result = validator.validate(p)
        assert result.valid is True
        assert os.path.basename(result.path) == name
        assert result.path == p

    def test_spanish_catalan_metadata_not_normalized(self, validator, workdir):
        name = "Introducción a la teoria de sistemes.ogg"
        p = _write(os.path.join(workdir, name))
        mat = Material(
            filename=name,
            path=p,
            material_type=MaterialType.AUDIO,
            metadata={"title_original": "Introducció a la teoria de sistemes"},
        )
        original_metadata = dict(mat.metadata)
        result = validator.validate(mat)
        assert result.valid is True
        assert result.metadata == original_metadata
        assert mat.metadata == original_metadata

    def test_result_is_structured_and_serializable(self, validator, workdir):
        p = _write(os.path.join(workdir, "lecture.wav"))
        result = validator.validate(p)
        d = result.to_dict()
        assert d["valid"] is True
        assert d["status"] == "VALID"
        assert d["errors"] == []
        back = AudioValidationResult.from_dict(d)
        assert back.valid is result.valid
        assert back.status is result.status
        assert back.errors == result.errors


class TestDeterminismAndSafety:
    def test_deterministic_result_for_same_file(self, validator, workdir):
        p = _write(os.path.join(workdir, "lecture.mp3"))
        r1 = validator.validate(p)
        r2 = validator.validate(p)
        assert r1 == r2
        assert r1.to_dict() == r2.to_dict()

    def test_validator_does_not_mutate_file(self, validator, workdir):
        p = _write(os.path.join(workdir, "lecture.mp3"))
        st_before = os.stat(p)
        validator.validate(p)
        st_after = os.stat(p)
        assert st_before.st_size == st_after.st_size
        assert st_before.st_mtime_ns == st_after.st_mtime_ns

    def test_no_undocumented_size_limit(self, validator, workdir):
        p = os.path.join(workdir, "big_lecture.m4a")
        _write(p, b"\x00" * (2 * 1024 * 1024))
        result = validator.validate(p)
        assert result.valid is True
        assert result.file_size == 2 * 1024 * 1024

    def test_module_has_no_audio_decoder_imports(self):
        import src.audio_input as mod
        names = set(dir(mod))
        banned = {
            "whisper", "faster_whisper", "torch", "torchaudio",
            "librosa", "pydub", "soundfile", "ffmpeg",
        }
        assert not (banned & names)


class TestAudioInput:
    def test_audio_input_from_valid_result(self, validator, workdir):
        p = _write(os.path.join(workdir, "lecture.mp3"))
        result = validator.validate(p)
        ai = validator.to_audio_input(result)
        assert isinstance(ai, AudioInput)
        assert ai.path == p
        assert ai.extension == ".mp3"
        assert ai.material is None
        assert ai.material_id is None
        d = ai.to_dict()
        assert d["path"] == p
        assert d["extension"] == ".mp3"

    def test_audio_input_rejects_invalid_result(self, validator, workdir):
        result = validator.validate(os.path.join(workdir, "ghost.mp3"))
        with pytest.raises(ValueError):
            validator.to_audio_input(result)

    def test_audio_input_reuses_material_metadata(self, validator, workdir):
        p = _write(os.path.join(workdir, "lecture.ogg"))
        mat = Material(
            filename="lecture.ogg",
            path=p,
            material_type=MaterialType.AUDIO,
            metadata={"size": 42, "sha256": "abc", "mtime": 1},
        )
        result = validator.validate(mat)
        assert result.valid is True
        assert result.material is mat
        assert result.metadata.get("sha256") == "abc"
        ai = validator.to_audio_input(result)
        assert ai.material_id == mat.material_id

    def test_audio_input_from_dict_roundtrip(self, workdir):
        p = _write(os.path.join(workdir, "lecture.wav"))
        v = AudioMaterialValidator()
        ai = v.to_audio_input(v.validate(p))
        ai2 = AudioInput.from_dict(ai.to_dict())
        assert ai2.path == ai.path
        assert ai2.extension == ai.extension
        assert ai2.metadata == ai.metadata

    def test_audio_input_must_not_carry_transcript_fields(self):
        ai = AudioInput()
        for forbidden in ("transcript", "segments", "speaker", "language", "confidence"):
            assert not hasattr(ai, forbidden)
