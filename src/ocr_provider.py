# -*- coding: utf-8 -*-
"""Production OCR provider layer (Task 36)。

把 OCR 从 "Mock 抽象" 推进到 "真实本地 OCR", 同时保证上层**永远不直接
依赖某个具体 OCR 库**::

    OCRProvider
    ├── MockOCRProvider     确定性占位实现 (测试 / 无引擎环境)
    └── LocalOCRProvider    本地 onnxruntime 引擎 (RapidOCR)

硬性原则 (Task 36 spec):
- lazy loading: 模型只在第一次真正 OCR 时才加载, 构造 provider 不会
  触发模型加载或磁盘扫描。
- deterministic result mapping: 区域按 (y, x, text) 稳定排序, 文本按该
  顺序用换行拼接; 同一图片重复调用返回完全相同的结果。
- clear errors: 模型不可用 -> OCRProviderUnavailableError;
  图片无法解码 -> InvalidImageError。绝不吞掉失败返回空结果。
- 底层引擎无法提供的字段一律 ``None``, **不伪造**。
- 禁止: 自动翻译、LLM 后处理、自动事实修正。
- Unicode 原样保留 (西语 / 加泰罗尼亚语 / 中文重音与变音符号不丢)。

依赖: ``rapidocr-onnxruntime`` (onnxruntime, CPU 默认; 模型随 wheel 分发,
无需云 API, 无需 GPU stack)。详见 requirements.txt。
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from src.models import (
    BoundingBox,
    Language,
    Material,
    MaterialType,
    OCRLanguage,
    OCRResult,
    OCRSegment,
    SourceReference,
)

__all__ = [
    "SUPPORTED_IMAGE_EXTENSIONS",
    "OCRProviderError",
    "OCRProviderUnavailableError",
    "InvalidImageError",
    "ImageInput",
    "OCRRegion",
    "OCRProviderResult",
    "OCRProvider",
    "MockOCRProvider",
    "LocalOCRProvider",
    "ProviderOCREngine",
    "is_local_ocr_available",
    "create_ocr_provider",
]

#: spec 要求至少支持 png / jpg / jpeg / webp; 另外兼容 bmp / tiff。
SUPPORTED_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
)

_PROVIDER_MOCK = "mock"
_PROVIDER_LOCAL = "local-rapidocr"


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------


class OCRProviderError(Exception):
    """OCR provider 层错误基类 (携带稳定错误码)。"""

    code = "OCR_ERROR"

    def __init__(self, message: str, *, detail: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = str(message)
        self.detail = dict(detail or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class OCRProviderUnavailableError(OCRProviderError):
    """引擎 / 模型无法加载 (依赖缺失、模型文件损坏、运行时不支持)。"""

    code = "OCR_ENGINE_UNAVAILABLE"


class InvalidImageError(OCRProviderError):
    """图片不存在 / 扩展名不支持 / 无法解码。"""

    code = "INVALID_IMAGE"


# ----------------------------------------------------------------------
# Value objects
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class ImageInput:
    """一次 OCR 请求的输入。

    ``language_hint`` 只是**给引擎的提示**, 永远不会被当成检测结果写回
    输出 (见 :class:`OCRProviderResult.language` 的说明)。
    """

    path: str
    material_id: str = ""
    page: Optional[int] = None
    language_hint: Optional[str] = None

    @property
    def extension(self) -> str:
        return os.path.splitext(self.path)[1].lower()

    @classmethod
    def from_material(cls, material: Material, page: Optional[int] = None) -> "ImageInput":
        hint = None
        if material.language not in (None, Language.UNKNOWN):
            hint = material.language.value
        return cls(
            path=str(material.path or ""),
            material_id=str(material.material_id or ""),
            page=page,
            language_hint=hint,
        )


@dataclass(frozen=True)
class OCRRegion:
    """一个文本区域。

    无法从引擎获得的字段为 ``None`` (绝不填 0 或猜测值)。
    """

    text: str
    confidence: Optional[float] = None
    bounding_box: Optional[BoundingBox] = None
    language: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "confidence": self.confidence,
            "bounding_box": None if self.bounding_box is None else self.bounding_box.to_dict(),
            "language": self.language,
        }


@dataclass(frozen=True)
class OCRProviderResult:
    """结构化 OCR 结果 (spec 要求字段: text / confidence / language /
    regions / bounding_box / source_reference)。"""

    provider: str
    material_id: str
    text: str
    confidence: Optional[float]
    language: Optional[str]
    regions: tuple[OCRRegion, ...] = ()
    source_reference: Optional[SourceReference] = None
    provider_version: Optional[str] = None
    model: Optional[str] = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def bounding_box(self) -> Optional[BoundingBox]:
        """整张图片文本区域的并集包围盒; 无区域时为 None。"""
        boxes = [r.bounding_box for r in self.regions if r.bounding_box is not None]
        if not boxes:
            return None
        x0 = min(b.x for b in boxes)
        y0 = min(b.y for b in boxes)
        x1 = max(b.x + b.width for b in boxes)
        y1 = max(b.y + b.height for b in boxes)
        return BoundingBox(x=x0, y=y0, width=x1 - x0, height=y1 - y0)

    @property
    def is_empty(self) -> bool:
        return not self.regions and not self.text.strip()

    def to_dict(self) -> dict[str, Any]:
        box = self.bounding_box
        return {
            "provider": self.provider,
            "material_id": self.material_id,
            "text": self.text,
            "confidence": self.confidence,
            "language": self.language,
            "regions": [r.to_dict() for r in self.regions],
            "bounding_box": None if box is None else box.to_dict(),
            "source_reference": None
            if self.source_reference is None
            else self.source_reference.to_dict(),
            "provider_version": self.provider_version,
            "model": self.model,
            "warnings": list(self.warnings),
        }


# ----------------------------------------------------------------------
# Provider interface
# ----------------------------------------------------------------------


class OCRProvider(ABC):
    """OCR provider 抽象。上层只依赖本接口。"""

    name: str = "abstract"

    @abstractmethod
    def is_available(self) -> bool:
        """引擎当前是否可用 (绝不抛异常, 不加载模型)。"""

    @abstractmethod
    def ocr(self, image: ImageInput) -> OCRProviderResult:
        """对一张图片执行 OCR; 失败时抛出 OCRProviderError 子类。"""

    # -- 共享的输入校验 ------------------------------------------------

    def _validate(self, image: ImageInput) -> None:
        if not isinstance(image, ImageInput):
            raise InvalidImageError(
                f"image must be an ImageInput, got {type(image).__name__}"
            )
        path = image.path
        if not path or not str(path).strip():
            raise InvalidImageError("image path must be a non-empty string")
        if image.extension not in SUPPORTED_IMAGE_EXTENSIONS:
            raise InvalidImageError(
                f"unsupported image extension {image.extension!r}; "
                f"supported: {sorted(SUPPORTED_IMAGE_EXTENSIONS)}"
            )
        if not os.path.isfile(path):
            raise InvalidImageError(f"image not found: {path}")
        if os.path.getsize(path) == 0:
            raise InvalidImageError(f"image is empty (0 bytes): {path}")


# ----------------------------------------------------------------------
# Mock provider
# ----------------------------------------------------------------------


class MockOCRProvider(OCRProvider):
    """确定性占位 provider。

    **不读取图片内容**, 也不假装读到了什么: 生成的行文本明确标注为
    mock, 供无 OCR 引擎的环境 / 纯单元测试使用。生产路径不得使用。
    """

    name = _PROVIDER_MOCK
    provider_version = "mock-1"

    def is_available(self) -> bool:
        return True

    def ocr(self, image: ImageInput) -> OCRProviderResult:
        self._validate(image)
        digest = hashlib.sha256(
            (image.material_id or image.path).encode("utf-8")
        ).hexdigest()
        count = (int(digest[:2], 16) % 4) + 1
        regions: list[OCRRegion] = []
        for index in range(count):
            x = 10.0 + (int(digest[2 + index * 2: 4 + index * 2], 16) % 100)
            y = 10.0 + index * 30.0
            width = 200.0 + (int(digest[4 + index * 2: 6 + index * 2], 16) % 200)
            height = 20.0 + (int(digest[6 + index * 2: 8 + index * 2], 16) % 20)
            confidence = min(
                1.0, 0.5 + (int(digest[8 + index * 2: 10 + index * 2], 16) % 50) / 100.0
            )
            regions.append(
                OCRRegion(
                    text=f"mock ocr line {index + 1}",
                    confidence=round(confidence, 2),
                    bounding_box=BoundingBox(
                        x=round(x, 2),
                        y=round(y, 2),
                        width=round(width, 2),
                        height=round(height, 2),
                    ),
                    language=None,
                )
            )
        return _build_result(
            provider=self.name,
            image=image,
            regions=regions,
            provider_version=self.provider_version,
            model="mock",
            warnings=("MOCK_OCR_OUTPUT",),
        )


# ----------------------------------------------------------------------
# Local provider (RapidOCR / onnxruntime)
# ----------------------------------------------------------------------


def is_local_ocr_available() -> bool:
    """本地 OCR 运行时是否可导入 (不加载模型)。"""
    try:
        return importlib.util.find_spec("rapidocr_onnxruntime") is not None
    except (ImportError, ValueError):  # pragma: no cover - 环境异常
        return False


class LocalOCRProvider(OCRProvider):
    """基于本地 onnxruntime 的真实 OCR provider。

    参数:
    - engine_loader: 可注入的引擎工厂 (测试用; 默认构造 RapidOCR)。
      必须是 ``() -> engine`` 的可调用对象。
    - use_det / use_cls / use_rec: 传递给底层引擎, 默认全部启用。
    - min_confidence: 低于该置信度的区域会被丢弃 (0.0 = 全保留)。
      **只做阈值过滤, 不做任何文本改写**。
    """

    name = _PROVIDER_LOCAL
    provider_version = "rapidocr-onnxruntime-1.2.3"
    model = "PP-OCRv4 (onnx, bundled)"

    def __init__(
        self,
        *,
        engine_loader: Optional[Callable[[], Any]] = None,
        min_confidence: float = 0.0,
        **engine_kwargs: Any,
    ) -> None:
        if min_confidence < 0.0 or min_confidence > 1.0:
            raise ValueError("min_confidence must be within [0, 1]")
        self._engine_loader = engine_loader
        self._engine_kwargs = dict(engine_kwargs)
        self._min_confidence = float(min_confidence)
        self._engine: Optional[Any] = None
        self._load_error: Optional[str] = None

    # -- availability --------------------------------------------------

    def is_available(self) -> bool:
        if self._engine is not None:
            return True
        if self._engine_loader is not None:
            return True
        return is_local_ocr_available()

    @property
    def loaded(self) -> bool:
        """模型是否已经被加载 (lazy loading 的可观测证据)。"""
        return self._engine is not None

    def _ensure_engine(self) -> Any:
        """惰性加载引擎; 失败时抛出结构化的不可用错误。"""
        if self._engine is not None:
            return self._engine
        if self._engine_loader is None and not is_local_ocr_available():
            self._load_error = "rapidocr_onnxruntime is not installed"
            raise OCRProviderUnavailableError(
                "local OCR engine is not available: rapidocr-onnxruntime is not installed",
                detail={"hint": "pip install rapidocr-onnxruntime==1.2.3"},
            )
        try:
            if self._engine_loader is not None:
                engine = self._engine_loader()
            else:
                from rapidocr_onnxruntime import RapidOCR

                engine = RapidOCR(**self._engine_kwargs)
        except Exception as exc:  # noqa: BLE001 - 引擎加载边界
            self._load_error = f"{type(exc).__name__}: {exc}"
            raise OCRProviderUnavailableError(
                f"failed to load local OCR engine: {self._load_error}",
                detail={"hint": "check onnxruntime / model files"},
            ) from exc
        if engine is None:
            self._load_error = "engine loader returned None"
            raise OCRProviderUnavailableError(
                "failed to load local OCR engine: engine loader returned None"
            )
        self._engine = engine
        return engine

    # -- ocr -----------------------------------------------------------

    def ocr(self, image: ImageInput) -> OCRProviderResult:
        self._validate(image)
        engine = self._ensure_engine()
        try:
            raw = engine(str(image.path))
        except OCRProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - 引擎调用边界
            raise InvalidImageError(
                f"OCR engine could not process {os.path.basename(image.path)!r}: "
                f"{type(exc).__name__}",
                detail={"error_type": type(exc).__name__},
            ) from exc

        boxes, texts, scores = _split_engine_output(raw)
        regions: list[OCRRegion] = []
        for index, text in enumerate(texts):
            if not isinstance(text, str) or not text.strip():
                continue
            score = _coerce_confidence(scores[index] if index < len(scores) else None)
            if score is not None and score < self._min_confidence:
                continue
            box = _polygon_to_bbox(boxes[index]) if index < len(boxes) else None
            regions.append(
                OCRRegion(
                    text=text,
                    confidence=score,
                    bounding_box=box,
                    # 本地引擎不做语种识别: 该字段保持 None, 不猜测。
                    language=None,
                )
            )

        return _build_result(
            provider=self.name,
            image=image,
            regions=regions,
            provider_version=self.provider_version,
            model=self.model,
            warnings=() if regions else ("NO_TEXT_DETECTED",),
        )


# ----------------------------------------------------------------------
# Domain adapter (让现有 ingestion 流水线可以使用真实 provider)
# ----------------------------------------------------------------------


class ProviderOCREngine:
    """把 :class:`OCRProvider` 适配成 domain 层的 ``OCREngine``。

    保持 domain 层不变: ``OCRProviderResult`` -> ``OCRResult`` 的映射是
    纯投影, 不做任何文本加工。
    """

    def __init__(self, provider: OCRProvider) -> None:
        if provider is None:
            raise ValueError("provider is required")
        self._provider = provider

    @property
    def provider(self) -> OCRProvider:
        return self._provider

    def ocr(self, material: Material) -> OCRResult:
        if not isinstance(material, Material):
            raise TypeError("material must be a Material instance")
        if material.material_type != MaterialType.IMAGE:
            raise ValueError(
                f"OCR only supports IMAGE materials, got {material.material_type.value}"
            )
        image = ImageInput.from_material(material)
        result = self._provider.ocr(image)
        return OCRResult(
            material_id=material.material_id,
            language=OCRLanguage.from_string(result.language or "Unknown"),
            segments=[
                OCRSegment(
                    text=region.text,
                    bounding_box=region.bounding_box or BoundingBox(),
                    confidence=region.confidence if region.confidence is not None else 0.0,
                    language=OCRLanguage.from_string(region.language or "Unknown"),
                    page=image.page,
                )
                for region in result.regions
            ],
            metadata={
                "engine": result.provider,
                "provider_version": result.provider_version,
                "model": result.model,
                "language_detected": result.language,
                "filename": material.filename,
            },
        )

    def ocr_segments(self, material: Material) -> list[OCRSegment]:
        return self.ocr(material).segments


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------


def create_ocr_provider(
    kind: str = "auto",
    *,
    require_real: bool = False,
    **kwargs: Any,
) -> OCRProvider:
    """构造 OCR provider。

    - ``kind="local"``: 强制真实本地 provider (不可用时抛
      :class:`OCRProviderUnavailableError` —— 绝不静默降级为 Mock,
      否则会向上层输出看起来像事实的假文本)。
    - ``kind="mock"``: 显式要求 Mock (仅测试 / 无引擎环境)。
    - ``kind="auto"``: 有本地引擎就用本地; 否则在
      ``require_real=False`` 时返回 Mock 并在 provider 名上保持可辨识。
    """
    normalized = (kind or "auto").strip().lower()
    if normalized == "local":
        return LocalOCRProvider(**kwargs)
    if normalized == "mock":
        return MockOCRProvider()
    if normalized != "auto":
        raise ValueError(f"unknown ocr provider kind: {kind!r}")
    if is_local_ocr_available():
        return LocalOCRProvider(**kwargs)
    if require_real:
        raise OCRProviderUnavailableError(
            "no real OCR engine available and require_real=True",
            detail={"hint": "pip install rapidocr-onnxruntime==1.2.3"},
        )
    return MockOCRProvider()


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


def _build_result(
    *,
    provider: str,
    image: ImageInput,
    regions: Sequence[OCRRegion],
    provider_version: Optional[str],
    model: Optional[str],
    warnings: Sequence[str],
) -> OCRProviderResult:
    """把区域列表归一为稳定排序 + 聚合字段的结果。"""
    ordered = sorted(
        regions,
        key=lambda r: (
            r.bounding_box.y if r.bounding_box is not None else 0.0,
            r.bounding_box.x if r.bounding_box is not None else 0.0,
            r.text,
        ),
    )
    scores = [r.confidence for r in ordered if r.confidence is not None]
    confidence = None if not scores else round(sum(scores) / len(scores), 6)
    text = "\n".join(r.text for r in ordered)
    return OCRProviderResult(
        provider=provider,
        material_id=image.material_id,
        text=text,
        confidence=confidence,
        language=None,  # 本地引擎不做语种识别 -> None (不伪造)
        regions=tuple(ordered),
        source_reference=SourceReference(
            material_id=image.material_id,
            location=f"page {image.page}" if image.page is not None else None,
            page=image.page,
        ),
        provider_version=provider_version,
        model=model,
        warnings=tuple(warnings),
    )


def _split_engine_output(raw: Any) -> tuple[list[Any], list[str], list[Any]]:
    """把底层引擎返回值归一为 (boxes, texts, scores)。

    RapidOCR 的公开契约是 ``(result, elapse)``, 其中 ``result`` 是
    ``[[box, text, score], ...]`` 或 ``None`` (未检测到任何文本)。
    空结果是合法成功, 不是错误。

    同时兼容"引擎直接返回行列表"的实现。
    """
    payload = raw
    if isinstance(raw, tuple) and len(raw) == 2:
        payload = raw[0]
    if payload is None:
        return [], [], []
    if not isinstance(payload, (list, tuple)):
        return [], [], []
    boxes: list[Any] = []
    texts: list[str] = []
    scores: list[Any] = []
    for row in payload:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        boxes.append(row[0])
        texts.append(row[1])
        scores.append(row[2] if len(row) > 2 else None)
    return boxes, texts, scores


def _coerce_confidence(value: Any) -> Optional[float]:
    """把引擎给出的置信度归一为 float; 无法解释时返回 None (不猜)。

    RapidOCR 1.2.x 把 score 作为**字符串**返回 (例如 ``"0.9081..."``),
    因此这里必须做类型归一 —— 这是忠实的类型转换, 不是数值改写。
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return number


def _polygon_to_bbox(polygon: Any) -> Optional[BoundingBox]:
    """四点多边形 -> 轴对齐 BoundingBox; 无法解析时返回 None (不猜)。"""
    if polygon is None:
        return None
    points: list[tuple[float, float]] = []
    try:
        for point in polygon:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                points.append((float(point[0]), float(point[1])))
    except (TypeError, ValueError):
        return None
    if len(points) < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, y0 = max(0.0, min(xs)), max(0.0, min(ys))
    x1, y1 = max(0.0, max(xs)), max(0.0, max(ys))
    return BoundingBox(x=x0, y=y0, width=x1 - x0, height=y1 - y0)
