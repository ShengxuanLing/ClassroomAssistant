# -*- coding: utf-8 -*-
"""Task 36 tests: Production OCR Provider.

覆盖 spec 要求: fake provider tests / real provider tests / Unicode tests /
bounding box tests / empty result / invalid image / model load failure /
repeated invocation / deterministic mapping。

真实引擎测试标记 ``@pytest.mark.integration`` (默认 deselect)。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.models import BoundingBox, Language, Material, MaterialType, OCRLanguage
from src.ocr_provider import (
    SUPPORTED_IMAGE_EXTENSIONS,
    ImageInput,
    InvalidImageError,
    LocalOCRProvider,
    MockOCRProvider,
    OCRProvider,
    OCRProviderError,
    OCRProviderResult,
    OCRProviderUnavailableError,
    OCRRegion,
    ProviderOCREngine,
    create_ocr_provider,
    is_local_ocr_available,
)
from src.ocr_provider import _coerce_confidence, _polygon_to_bbox, _split_engine_output

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def png_file(tmp_path, name="board.png", payload=b"\x89PNG\r\n\x1a\n fake"):
    path = tmp_path / name
    path.write_bytes(payload)
    return str(path)


def row(x, y, w, h, text, score=None):
    item = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
    if score is None:
        return [item, text]
    return [item, text, score]


class FakeEngine:
    """最小可注入引擎: 记录调用次数, 可返回固定 payload 或抛异常。"""

    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = 0

    def __call__(self, path):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.payload


def local_provider(payload=None, error=None, **kwargs):
    engine = FakeEngine(payload=payload, error=error)
    return LocalOCRProvider(engine_loader=lambda: engine, **kwargs), engine


# ===========================================================================
# 1. 错误类型
# ===========================================================================


class TestErrors:
    def test_unavailable_is_provider_error(self):
        assert issubclass(OCRProviderUnavailableError, OCRProviderError)

    def test_invalid_image_is_provider_error(self):
        assert issubclass(InvalidImageError, OCRProviderError)

    def test_error_codes_are_stable(self):
        assert OCRProviderUnavailableError("x").code == "OCR_ENGINE_UNAVAILABLE"
        assert InvalidImageError("x").code == "INVALID_IMAGE"

    def test_error_to_dict(self):
        payload = InvalidImageError("bad", detail={"path": "a.png"}).to_dict()
        assert payload["code"] == "INVALID_IMAGE"
        assert payload["message"] == "bad"
        assert payload["detail"] == {"path": "a.png"}


# ===========================================================================
# 2. ImageInput
# ===========================================================================


class TestImageInput:
    def test_extension_lowercased(self):
        assert ImageInput(path="A.PNG").extension == ".png"

    def test_supported_extensions_cover_spec(self):
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            assert ext in SUPPORTED_IMAGE_EXTENSIONS

    def test_from_material_carries_id_and_language_hint(self):
        material = Material(
            material_id="mat-1",
            filename="b.png",
            path="/tmp/b.png",
            material_type=MaterialType.IMAGE,
            language=Language.SPANISH,
        )
        image = ImageInput.from_material(material, page=2)
        assert image.material_id == "mat-1"
        assert image.language_hint == "Spanish"
        assert image.page == 2

    def test_from_material_unknown_language_has_no_hint(self):
        material = Material(material_id="m", path="/tmp/b.png")
        assert ImageInput.from_material(material).language_hint is None

    def test_frozen(self):
        image = ImageInput(path="a.png")
        with pytest.raises(Exception):
            image.path = "b.png"  # type: ignore[misc]

    def test_default_material_id_is_empty(self):
        assert ImageInput(path="a.png").material_id == ""


# ===========================================================================
# 3. OCRRegion / OCRProviderResult
# ===========================================================================


class TestRegionAndResult:
    def test_region_to_dict_none_fields_preserved(self):
        payload = OCRRegion(text="hola").to_dict()
        assert payload["confidence"] is None
        assert payload["bounding_box"] is None
        assert payload["language"] is None

    def test_region_to_dict_with_bbox(self):
        region = OCRRegion(text="hola", confidence=0.5, bounding_box=BoundingBox(1, 2, 3, 4))
        assert region.to_dict()["bounding_box"] == {"x": 1, "y": 2, "width": 3, "height": 4}

    def test_result_union_bounding_box(self):
        result = OCRProviderResult(
            provider="p",
            material_id="m",
            text="t",
            confidence=1.0,
            language=None,
            regions=(
                OCRRegion(text="a", bounding_box=BoundingBox(10, 10, 20, 5)),
                OCRRegion(text="b", bounding_box=BoundingBox(5, 40, 50, 10)),
            ),
        )
        box = result.bounding_box
        assert (box.x, box.y, box.width, box.height) == (5, 10, 50, 40)

    def test_result_bounding_box_none_without_regions(self):
        result = OCRProviderResult("p", "m", "", None, None)
        assert result.bounding_box is None

    def test_result_bounding_box_ignores_region_without_box(self):
        result = OCRProviderResult(
            "p", "m", "t", None, None, regions=(OCRRegion(text="x"),)
        )
        assert result.bounding_box is None

    def test_result_is_empty(self):
        assert OCRProviderResult("p", "m", "", None, None).is_empty
        assert not OCRProviderResult("p", "m", "x", None, None).is_empty

    def test_result_to_dict_shape(self):
        result = OCRProviderResult("p", "m", "x", 0.5, None)
        payload = result.to_dict()
        for key in (
            "provider", "material_id", "text", "confidence", "language",
            "regions", "bounding_box", "source_reference",
        ):
            assert key in payload


# ===========================================================================
# 4. Mock provider
# ===========================================================================


class TestMockProvider:
    def test_is_available(self):
        assert MockOCRProvider().is_available() is True

    def test_name(self):
        assert MockOCRProvider().name == "mock"

    def test_deterministic(self, tmp_path):
        provider = MockOCRProvider()
        image = ImageInput(path=png_file(tmp_path), material_id="mat-1")
        assert provider.ocr(image).to_dict() == provider.ocr(image).to_dict()

    def test_regions_non_empty(self, tmp_path):
        result = MockOCRProvider().ocr(ImageInput(path=png_file(tmp_path), material_id="m"))
        assert result.regions
        assert result.confidence is not None

    def test_output_is_labelled_as_mock(self, tmp_path):
        result = MockOCRProvider().ocr(ImageInput(path=png_file(tmp_path), material_id="m"))
        assert result.warnings == ("MOCK_OCR_OUTPUT",)
        assert "mock" in result.text

    def test_regions_sorted_by_position(self, tmp_path):
        result = MockOCRProvider().ocr(ImageInput(path=png_file(tmp_path), material_id="m"))
        ys = [r.bounding_box.y for r in result.regions]
        assert ys == sorted(ys)

    def test_language_is_none(self, tmp_path):
        result = MockOCRProvider().ocr(ImageInput(path=png_file(tmp_path), material_id="m"))
        assert result.language is None
        assert all(r.language is None for r in result.regions)

    def test_rejects_missing_file(self, tmp_path):
        with pytest.raises(InvalidImageError):
            MockOCRProvider().ocr(ImageInput(path=str(tmp_path / "nope.png")))


# ===========================================================================
# 5. Local provider (fake engine)
# ===========================================================================


class TestLocalProviderFakeEngine:
    def test_lazy_loading(self, tmp_path):
        provider, engine = local_provider(payload=[])
        assert provider.loaded is False
        assert engine.calls == 0
        provider.ocr(ImageInput(path=png_file(tmp_path)))
        assert provider.loaded is True
        assert engine.calls == 1

    def test_engine_loaded_once_for_repeated_invocation(self, tmp_path):
        provider, engine = local_provider(payload=[])
        image = ImageInput(path=png_file(tmp_path))
        provider.ocr(image)
        provider.ocr(image)
        provider.ocr(image)
        assert engine.calls == 3  # 调用引擎 3 次
        assert provider._engine is not None  # noqa: SLF001 - 模型只加载一次

    def test_deterministic_mapping(self, tmp_path):
        payload = [row(10, 20, 100, 20, "hola", "0.9"), row(10, 60, 80, 20, "adios", "0.8")]
        provider, _ = local_provider(payload=payload)
        image = ImageInput(path=png_file(tmp_path), material_id="m")
        assert provider.ocr(image).to_dict() == provider.ocr(image).to_dict()

    def test_regions_sorted_by_y_then_x(self, tmp_path):
        payload = [
            row(50, 100, 10, 10, "third"),
            row(10, 10, 10, 10, "first"),
            row(80, 10, 10, 10, "second"),
        ]
        provider, _ = local_provider(payload=payload)
        result = provider.ocr(ImageInput(path=png_file(tmp_path)))
        assert [r.text for r in result.regions] == ["first", "second", "third"]

    def test_text_joined_in_reading_order(self, tmp_path):
        payload = [row(0, 90, 10, 10, "b"), row(0, 10, 10, 10, "a")]
        provider, _ = local_provider(payload=payload)
        assert provider.ocr(ImageInput(path=png_file(tmp_path))).text == "a\nb"

    def test_empty_result_none_payload(self, tmp_path):
        provider, _ = local_provider(payload=None)
        result = provider.ocr(ImageInput(path=png_file(tmp_path)))
        assert result.is_empty
        assert result.text == ""
        assert result.regions == ()
        assert result.confidence is None
        assert result.warnings == ("NO_TEXT_DETECTED",)

    def test_empty_result_empty_list(self, tmp_path):
        provider, _ = local_provider(payload=[])
        result = provider.ocr(ImageInput(path=png_file(tmp_path)))
        assert result.is_empty
        assert result.warnings == ("NO_TEXT_DETECTED",)

    def test_confidence_is_mean_of_regions(self, tmp_path):
        payload = [row(0, 0, 10, 10, "a", 0.5), row(0, 20, 10, 10, "b", 1.0)]
        provider, _ = local_provider(payload=payload)
        result = provider.ocr(ImageInput(path=png_file(tmp_path)))
        assert result.confidence == 0.75

    def test_string_scores_are_coerced(self, tmp_path):
        payload = [row(0, 0, 10, 10, "a", "0.9081976041197777")]
        provider, _ = local_provider(payload=payload)
        result = provider.ocr(ImageInput(path=png_file(tmp_path)))
        assert result.regions[0].confidence == pytest.approx(0.9081976041197777)

    def test_missing_score_yields_none_confidence(self, tmp_path):
        payload = [row(0, 0, 10, 10, "a")]
        provider, _ = local_provider(payload=payload)
        result = provider.ocr(ImageInput(path=png_file(tmp_path)))
        assert result.regions[0].confidence is None
        assert result.confidence is None

    def test_min_confidence_filters_regions(self, tmp_path):
        payload = [row(0, 0, 10, 10, "keep", 0.9), row(0, 20, 10, 10, "drop", 0.1)]
        provider, _ = local_provider(payload=payload, min_confidence=0.5)
        result = provider.ocr(ImageInput(path=png_file(tmp_path)))
        assert [r.text for r in result.regions] == ["keep"]

    def test_min_confidence_does_not_rewrite_text(self, tmp_path):
        payload = [row(0, 0, 10, 10, "  spaced  ", 0.9)]
        provider, _ = local_provider(payload=payload)
        assert provider.ocr(ImageInput(path=png_file(tmp_path))).text == "  spaced  "

    def test_bounding_box_from_polygon(self, tmp_path):
        payload = [row(30, 40, 100, 20, "a", 0.9)]
        provider, _ = local_provider(payload=payload)
        box = provider.ocr(ImageInput(path=png_file(tmp_path))).regions[0].bounding_box
        assert (box.x, box.y, box.width, box.height) == (30.0, 40.0, 100.0, 20.0)

    def test_malformed_polygon_yields_none_box(self, tmp_path):
        payload = [[[[1], [2]], "text", 0.9]]
        provider, _ = local_provider(payload=payload)
        result = provider.ocr(ImageInput(path=png_file(tmp_path)))
        assert [r.text for r in result.regions] == ["text"]
        assert result.regions[0].bounding_box is None

    def test_language_never_guessed(self, tmp_path):
        payload = [row(0, 0, 10, 10, "hola", 0.9)]
        provider, _ = local_provider(payload=payload)
        result = provider.ocr(ImageInput(path=png_file(tmp_path), language_hint="Spanish"))
        assert result.language is None
        assert result.regions[0].language is None

    def test_unicode_preserved_exactly(self, tmp_path):
        text = "función çatalà 课堂笔记 é ̈"
        payload = [row(0, 0, 10, 10, text, 0.9)]
        provider, _ = local_provider(payload=payload)
        assert provider.ocr(ImageInput(path=png_file(tmp_path))).text == text

    def test_non_string_rows_skipped(self, tmp_path):
        payload = [row(0, 0, 10, 10, "good", 0.9), [None, None], [None, 123, 0.5], "junk"]
        provider, _ = local_provider(payload=payload)
        result = provider.ocr(ImageInput(path=png_file(tmp_path)))
        assert [r.text for r in result.regions] == ["good"]

    def test_blank_text_skipped(self, tmp_path):
        payload = [row(0, 0, 10, 10, "   ", 0.9), row(0, 20, 10, 10, "real", 0.9)]
        provider, _ = local_provider(payload=payload)
        result = provider.ocr(ImageInput(path=png_file(tmp_path)))
        assert [r.text for r in result.regions] == ["real"]

    def test_source_reference_carries_material_and_page(self, tmp_path):
        provider, _ = local_provider(payload=[])
        result = provider.ocr(
            ImageInput(path=png_file(tmp_path), material_id="mat-9", page=3)
        )
        assert result.source_reference.material_id == "mat-9"
        assert result.source_reference.page == 3
        assert result.source_reference.location == "page 3"

    def test_source_reference_without_page(self, tmp_path):
        provider, _ = local_provider(payload=[])
        result = provider.ocr(ImageInput(path=png_file(tmp_path), material_id="m"))
        assert result.source_reference.page is None
        assert result.source_reference.location is None

    def test_engine_load_failure(self, tmp_path):
        def boom():
            raise RuntimeError("onnxruntime missing")

        provider = LocalOCRProvider(engine_loader=boom)
        with pytest.raises(OCRProviderUnavailableError) as exc:
            provider.ocr(ImageInput(path=png_file(tmp_path)))
        assert "onnxruntime missing" in str(exc.value)
        assert provider.loaded is False

    def test_engine_loader_returning_none(self, tmp_path):
        provider = LocalOCRProvider(engine_loader=lambda: None)
        with pytest.raises(OCRProviderUnavailableError):
            provider.ocr(ImageInput(path=png_file(tmp_path)))

    def test_engine_failure_during_call(self, tmp_path):
        provider, _ = local_provider(error=ValueError("decode failed"))
        with pytest.raises(InvalidImageError):
            provider.ocr(ImageInput(path=png_file(tmp_path)))

    def test_engine_error_does_not_leak_traceback(self, tmp_path):
        provider, _ = local_provider(error=RuntimeError("secret internal detail"))
        with pytest.raises(InvalidImageError) as exc:
            provider.ocr(ImageInput(path=png_file(tmp_path)))
        assert "secret internal detail" not in str(exc.value)

    def test_provider_metadata(self, tmp_path):
        provider, _ = local_provider(payload=[])
        result = provider.ocr(ImageInput(path=png_file(tmp_path)))
        assert result.provider == "local-rapidocr"
        assert result.provider_version is not None
        assert result.model is not None

    def test_accepts_bare_row_list(self, tmp_path):
        payload = [row(0, 0, 10, 10, "a", 0.9)]
        provider, _ = local_provider(payload=payload)
        assert provider.ocr(ImageInput(path=png_file(tmp_path))).text == "a"

    def test_rejects_bad_min_confidence(self):
        with pytest.raises(ValueError):
            LocalOCRProvider(min_confidence=1.5)


# ===========================================================================
# 6. 输入校验
# ===========================================================================


class TestInvalidImages:
    def test_missing_file(self, tmp_path):
        with pytest.raises(InvalidImageError):
            MockOCRProvider().ocr(ImageInput(path=str(tmp_path / "gone.png")))

    def test_unsupported_extension(self, tmp_path):
        path = tmp_path / "notes.txt"
        path.write_bytes(b"x")
        with pytest.raises(InvalidImageError):
            MockOCRProvider().ocr(ImageInput(path=str(path)))

    def test_zero_byte_image(self, tmp_path):
        with pytest.raises(InvalidImageError):
            MockOCRProvider().ocr(ImageInput(path=png_file(tmp_path, "empty.png", b"")))

    def test_empty_path(self):
        with pytest.raises(InvalidImageError):
            MockOCRProvider().ocr(ImageInput(path=""))

    def test_wrong_input_type(self):
        with pytest.raises(InvalidImageError):
            MockOCRProvider().ocr("not-an-image-input")  # type: ignore[arg-type]

    def test_local_provider_validates_before_loading(self, tmp_path):
        provider, engine = local_provider(payload=[])
        with pytest.raises(InvalidImageError):
            provider.ocr(ImageInput(path=str(tmp_path / "gone.png")))
        assert engine.calls == 0
        assert provider.loaded is False


# ===========================================================================
# 7. 内部归一化函数
# ===========================================================================


class TestNormalisation:
    def test_split_tuple_payload(self):
        boxes, texts, scores = _split_engine_output(([["box", "t", 0.5]], [0.1]))
        assert texts == ["t"]
        assert scores == [0.5]

    def test_split_none_payload(self):
        assert _split_engine_output((None, [0.1])) == ([], [], [])

    def test_split_bare_list(self):
        boxes, texts, _ = _split_engine_output([["box", "t", 0.5]])
        assert texts == ["t"]

    def test_split_short_rows_ignored(self):
        assert _split_engine_output([["only-one"]]) == ([], [], [])

    def test_split_garbage(self):
        assert _split_engine_output("nonsense") == ([], [], [])

    def test_coerce_confidence_string(self):
        assert _coerce_confidence("0.5") == 0.5

    def test_coerce_confidence_rejects_garbage(self):
        assert _coerce_confidence("abc") is None
        assert _coerce_confidence(None) is None
        assert _coerce_confidence(True) is None
        assert _coerce_confidence(float("nan")) is None

    def test_polygon_to_bbox_clamps_negative(self):
        box = _polygon_to_bbox([[-5, -5], [10, -5], [10, 10], [-5, 10]])
        assert box.x == 0.0 and box.y == 0.0
        assert box.width == 10.0 and box.height == 10.0

    def test_polygon_to_bbox_rejects_single_point(self):
        assert _polygon_to_bbox([[1, 2]]) is None

    def test_polygon_to_bbox_none(self):
        assert _polygon_to_bbox(None) is None


# ===========================================================================
# 8. 工厂
# ===========================================================================


class TestFactory:
    def test_mock_kind(self):
        assert isinstance(create_ocr_provider("mock"), MockOCRProvider)

    def test_local_kind(self):
        assert isinstance(create_ocr_provider("local"), LocalOCRProvider)

    def test_unknown_kind(self):
        with pytest.raises(ValueError):
            create_ocr_provider("tesseract")

    def test_auto_prefers_local_when_available(self, monkeypatch):
        monkeypatch.setattr("src.ocr_provider.is_local_ocr_available", lambda: True)
        assert isinstance(create_ocr_provider("auto"), LocalOCRProvider)

    def test_auto_falls_back_to_mock(self, monkeypatch):
        monkeypatch.setattr("src.ocr_provider.is_local_ocr_available", lambda: False)
        assert isinstance(create_ocr_provider("auto"), MockOCRProvider)

    def test_auto_require_real_raises(self, monkeypatch):
        monkeypatch.setattr("src.ocr_provider.is_local_ocr_available", lambda: False)
        with pytest.raises(OCRProviderUnavailableError):
            create_ocr_provider("auto", require_real=True)

    def test_local_unavailable_raises_structured_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.ocr_provider.is_local_ocr_available", lambda: False)
        provider = create_ocr_provider("local")
        with pytest.raises(OCRProviderUnavailableError) as exc:
            provider.ocr(ImageInput(path=png_file(tmp_path)))
        assert exc.value.detail["hint"].startswith("pip install")

    def test_is_local_ocr_available_returns_bool(self):
        assert isinstance(is_local_ocr_available(), bool)


# ===========================================================================
# 9. Domain 适配器
# ===========================================================================


class TestProviderOCREngine:
    def image_material(self, tmp_path, language=Language.UNKNOWN):
        return Material(
            material_id="mat-img",
            filename="b.png",
            path=png_file(tmp_path),
            material_type=MaterialType.IMAGE,
            language=language,
        )

    def test_maps_regions_to_segments(self, tmp_path):
        payload = [row(10, 20, 100, 20, "hola", 0.9)]
        provider, _ = local_provider(payload=payload)
        engine = ProviderOCREngine(provider)
        result = engine.ocr(self.image_material(tmp_path))
        assert len(result.segments) == 1
        assert result.segments[0].text == "hola"
        assert result.segments[0].confidence == pytest.approx(0.9)

    def test_keeps_material_id(self, tmp_path):
        provider, _ = local_provider(payload=[])
        result = ProviderOCREngine(provider).ocr(self.image_material(tmp_path))
        assert result.material_id == "mat-img"

    def test_unknown_language_maps_to_unknown_enum(self, tmp_path):
        provider, _ = local_provider(payload=[])
        result = ProviderOCREngine(provider).ocr(self.image_material(tmp_path))
        assert result.language == OCRLanguage.UNKNOWN

    def test_metadata_records_provider(self, tmp_path):
        provider, _ = local_provider(payload=[])
        result = ProviderOCREngine(provider).ocr(self.image_material(tmp_path))
        assert result.metadata["engine"] == "local-rapidocr"

    def test_rejects_non_image_material(self, tmp_path):
        provider, _ = local_provider(payload=[])
        material = Material(material_id="m", material_type=MaterialType.TEXT, path="a.txt")
        with pytest.raises(ValueError):
            ProviderOCREngine(provider).ocr(material)

    def test_rejects_non_material(self):
        provider, _ = local_provider(payload=[])
        with pytest.raises(TypeError):
            ProviderOCREngine(provider).ocr("nope")  # type: ignore[arg-type]

    def test_requires_provider(self):
        with pytest.raises(ValueError):
            ProviderOCREngine(None)  # type: ignore[arg-type]

    def test_ocr_segments_matches_ocr(self, tmp_path):
        provider, _ = local_provider(payload=[row(0, 0, 10, 10, "x", 0.9)])
        engine = ProviderOCREngine(provider)
        material = self.image_material(tmp_path)
        assert engine.ocr_segments(material) == engine.ocr(material).segments

    def test_provider_is_exposed(self):
        provider = MockOCRProvider()
        assert ProviderOCREngine(provider).provider is provider


# ===========================================================================
# 10. 真实本地 OCR (需要 rapidocr-onnxruntime)
# ===========================================================================


@pytest.mark.integration
class TestRealLocalOCR:
    def test_real_engine_reports_available(self):
        assert is_local_ocr_available() is True

    def test_real_ocr_extracts_latin_and_cjk(self):
        provider = LocalOCRProvider()
        result = provider.ocr(
            ImageInput(path=str(FIXTURES / "ocr_sample.png"), material_id="mat-real")
        )
        assert result.provider == "local-rapidocr"
        assert not result.is_empty
        assert "2026" in result.text
        assert "llengua" in result.text.lower()
        assert "课堂笔记" in result.text

    def test_real_ocr_is_deterministic(self):
        provider = LocalOCRProvider()
        image = ImageInput(path=str(FIXTURES / "ocr_sample.png"), material_id="mat-real")
        assert provider.ocr(image).to_dict() == provider.ocr(image).to_dict()

    def test_real_ocr_bounding_boxes_are_positive(self):
        provider = LocalOCRProvider()
        result = provider.ocr(ImageInput(path=str(FIXTURES / "ocr_sample.png")))
        assert result.regions
        for region in result.regions:
            assert region.bounding_box is not None
            assert region.bounding_box.width > 0
            assert region.bounding_box.height > 0
        assert result.bounding_box is not None

    def test_real_ocr_on_blank_image_is_empty_success(self, tmp_path):
        from PIL import Image

        blank = tmp_path / "blank.png"
        Image.new("RGB", (400, 200), "white").save(blank)
        result = LocalOCRProvider().ocr(ImageInput(path=str(blank)))
        assert result.is_empty
        assert result.warnings == ("NO_TEXT_DETECTED",)

    def test_real_ocr_feeds_domain_pipeline(self):
        provider = LocalOCRProvider()
        engine = ProviderOCREngine(provider)
        material = Material(
            material_id="mat-real",
            filename="ocr_sample.png",
            path=str(FIXTURES / "ocr_sample.png"),
            material_type=MaterialType.IMAGE,
        )
        result = engine.ocr(material)
        assert result.segments
        assert all(seg.text.strip() for seg in result.segments)
