# -*- coding: utf-8 -*-
"""Nightly 真实引擎冒烟测试 (P1-5)。

本文件只在 ``pytest -m integration -k nightly`` 下运行；默认 ``pytest``（不带
``-m "not integration"`` 也 deselected，因为本文件整体打 ``integration`` 标记）
不会跑它，避免在无 GPU / 无模型的环境下拖慢回归或误失败。

覆盖范围
--------------------------------------------------------------------
- 真实 Whisper 解码（西语 / 加泰语短音频各一段）: 断言产出非空、语言标记有效、
  证据定位（segment 时间戳）非空。
- 真实 RapidOCR 识别: 断言产出非空、文本含已知子串、bounding box（证据定位）非空。

无模型 / 无依赖时**干净 skip**（沿用 ``test_whisper_provider`` / ``test_ocr_provider``
的 skip 语义）: 不报错、不计为失败。缺真实双语语音时复用现有夹具
（``tests/fixtures/acceptance/tema1-classe.wav`` 为真实课堂录音；若无则回落到
``tone_3s.wav``）。若未来补充西语 / 加泰语独立短音频，可直接替换 ``ASR_FIXTURES``。
"""

from __future__ import annotations

import pytest

from src.application.bootstrap import is_local_whisper_available
from src.asr_provider import ASRProviderErrorCode
from src.audio_input import AudioInput, AudioMaterialValidator
from src.ocr_provider import ImageInput, is_local_ocr_available
from src.whisper_provider import (
    DEFAULT_WHISPER_MODEL,
    LocalWhisperProvider,
    WhisperConfig,
)

FIXTURES = __import__("pathlib").Path(__file__).resolve().parent / "fixtures"

#: 真实短音频夹具（西语 / 加泰语课堂录音；若缺则回落到 tone）。
ASR_FIXTURES = [
    FIXTURES / "acceptance" / "tema1-classe.wav",
    FIXTURES / "tone_3s.wav",
]

OCR_FIXTURE = FIXTURES / "ocr_sample.png"

SUPPORTED_ASR_LANGS = {
    "Spanish", "Catalan", "Chinese", "English", "Unknown",
}


def _first_existing(paths):
    for p in paths:
        if p.exists():
            return p
    return None


def _skip_on_model_load_error(exc):
    """模型加载/缓存损坏等「可用性」问题干净 skip；真实解码 bug 仍抛。

    本机若 cached Whisper 模型文件损坏（``File model.bin is incomplete`` 之类），
    本质等同于「模型不可用」，应沿用现有 integration 测试的 skip 语义，而不是
    把环境缺陷误报成测试失败。
    """
    msg = str(exc).lower()
    model_load_signatures = (
        "model.bin",
        "incomplete",
        "ctranslate",
        "download",
        "model is not",
        "load model",
        "onnxruntime",
        "faster_whisper",
    )
    is_model_load = (
        isinstance(exc, (OSError, FileNotFoundError, ImportError))
        or any(sig in msg for sig in model_load_signatures)
    )
    if is_model_load:
        pytest.skip("Whisper/OCR 模型不可用（可能缓存损坏或缺失）: %s" % exc)
    raise


def _make_real_whisper_provider():
    """构造一个走真实 faster-whisper 的 provider（模型懒加载）。"""
    return LocalWhisperProvider(
        WhisperConfig(
            model_name=DEFAULT_WHISPER_MODEL,
            device="cpu",
            compute_type="int8",
        )
    )


@pytest.mark.integration
class TestNightlyRealASR:
    """真实 Whisper 解码冒烟: 缺运行时 / 模型即 skip。"""

    def test_real_asr_fixtures_resolve(self):
        assert _first_existing(ASR_FIXTURES) is not None, (
            "没有任何 ASR 音频夹具 (acceptance/tema1-classe.wav / tone_3s.wav)"
        )

    @pytest.mark.nightly
    def test_real_whisper_decodes_non_empty_with_language_and_location(self):
        if not is_local_whisper_available():
            pytest.skip("faster-whisper 运行时不可用")
        audio_path = _first_existing(ASR_FIXTURES)
        try:
            provider = _make_real_whisper_provider()
            validator = AudioMaterialValidator()
            validation = validator.validate(str(audio_path))
            assert validation.valid, "音频夹具未通过输入校验"
            audio_input = validator.to_audio_input(validation)
            assert isinstance(audio_input, AudioInput)

            result = provider.transcribe(audio_input)
        except Exception as exc:  # noqa: BLE001 - 模型加载失败需干净 skip
            _skip_on_model_load_error(exc)
        # 产出非空: 至少要有 transcript 或 segment。
        assert result.ok, f"解码失败: {result.error_code}"
        assert result.transcript is not None
        text = (result.transcript.text or "").strip()
        assert text or list(result.segments), "解码结果为空"
        # 语言标记有效（不要求一定是 es/ca: 短音频/纯音可能落在其它语言）。
        lang = result.transcript.language
        assert getattr(lang, "value", lang) in SUPPORTED_ASR_LANGS, (
            f"语言标记非法: {lang!r}"
        )
        # 证据定位非空: segment 必须有非空时间戳。
        assert list(result.segments), "没有任何 segment（证据定位为空）"
        for seg in result.segments:
            assert seg.end >= seg.start, "segment 时间戳非法"


@pytest.mark.integration
class TestNightlyRealOCR:
    """真实 RapidOCR 识别冒烟: 缺运行时即 skip。"""

    @pytest.mark.nightly
    def test_real_ocr_extracts_non_empty_with_location(self):
        if not is_local_ocr_available():
            pytest.skip("rapidocr-onnxruntime 运行时不可用")
        if not OCR_FIXTURE.exists():
            pytest.skip("ocr_sample.png 夹具缺失")
        from src.ocr_provider import LocalOCRProvider

        try:
            provider = LocalOCRProvider()
            result = provider.ocr(
                ImageInput(path=str(OCR_FIXTURE), material_id="mat-nightly")
            )
        except Exception as exc:  # noqa: BLE001 - 模型加载失败需干净 skip
            _skip_on_model_load_error(exc)
        assert not result.is_empty, "OCR 结果为空"
        assert "2026" in result.text, "OCR 未识别到已知子串 2026"
        # 证据定位非空: 至少有 bounding box。
        assert result.regions, "OCR 没有产出任何 bounding box（证据定位为空）"
        for region in result.regions:
            box = region.bounding_box
            assert box is not None and box.width > 0 and box.height > 0
