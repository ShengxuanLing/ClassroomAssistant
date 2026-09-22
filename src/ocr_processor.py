from __future__ import annotations
import hashlib
from typing import Any, List, Optional

from src.models import (
    BoundingBox, Confidence, Evidence, EvidenceType, Language,
    Material, MaterialType, OCRLanguage, OCRResult, OCRSegment,
    SourceReference,
)

class OCREngine:
    def ocr(self, material: Material) -> OCRResult:
        raise NotImplementedError

    def ocr_segments(self, material: Material) -> List[OCRSegment]:
        raise NotImplementedError

class MockOCREngine(OCREngine):
    def __init__(self) -> None:
        self._default_language = OCRLanguage.UNKNOWN

    def _validate_material(self, material: Material) -> None:
        if not isinstance(material, Material):
            raise TypeError(
                'material must be a Material instance'
            )
        if material.material_type != MaterialType.IMAGE:
            raise ValueError(
                f'OCR only supports IMAGE materials, got {material.material_type.value}'
            )

    def ocr(self, material: Material) -> OCRResult:
        self._validate_material(material)
        segments = self.ocr_segments(material)
        lang = OCRLanguage.from_string(material.language.value)
        return OCRResult(
            material_id=material.material_id,
            language=lang,
            segments=segments,
            metadata={
                'engine': MockOCREngine,
                'filename': material.filename,
                'note': 'Mock OCR output - not real text extraction',
            },
        )

    def ocr_segments(self, material: Material) -> List[OCRSegment]:
        self._validate_material(material)
        path_hash = hashlib.sha256(material.material_id.encode('utf-8')).hexdigest()
        num_segments = (int(path_hash[:2], 16) % 4) + 1
        lang = OCRLanguage.from_string(material.language.value)
        segments: List[OCRSegment] = []
        for i in range(num_segments):
            x = 10.0 + (int(path_hash[2 + i * 2:4 + i * 2], 16) % 100)
            y = 10.0 + i * 30.0
            w = 200.0 + (int(path_hash[4 + i * 2:6 + i * 2], 16) % 200)
            h = 20.0 + (int(path_hash[6 + i * 2:8 + i * 2], 16) % 20)
            confidence = 0.5 + (int(path_hash[8 + i * 2:10 + i * 2], 16) % 50) / 100.0
            confidence = min(confidence, 1.0)
            text = f'OCR line {i + 1} from {material.filename}'
            segments.append(OCRSegment(
                text=text,
                bounding_box=BoundingBox(x=round(x, 2), y=round(y, 2), width=round(w, 2), height=round(h, 2)),
                confidence=round(confidence, 2),
                language=lang,
                page=1,
            ))
        return segments

def ocr_to_evidence(ocr_result: OCRResult) -> List[Evidence]:
    evidence_list: List[Evidence] = []
    for index, seg in enumerate(ocr_result.segments):
        source_ref = SourceReference(
            material_id=ocr_result.material_id,
            location=f'page {seg.page}' if seg.page is not None else None,
            page=seg.page,
        )
        if seg.confidence > 0.8:
            conf = Confidence.HIGH
        elif seg.confidence >= 0.5:
            conf = Confidence.MEDIUM
        else:
            conf = Confidence.LOW
        evidence_list.append(Evidence(
            content=seg.text,
            language=Language.from_string(seg.language.value),
            source_reference=source_ref,
            confidence=conf,
            evidence_type=EvidenceType.OCR,
            metadata={
                'ocr_result_id': ocr_result.ocr_result_id,
                'segment_index': index,
                'bounding_box': seg.bounding_box.to_dict(),
                'ocr_confidence': seg.confidence,
            },
        ))
    return evidence_list
def create_ocr_result(
    material_id: str,
    segments_data: list,
    language: str = "Spanish",
) -> OCRResult:
    lang = OCRLanguage.from_string(language)
    segments = []
    for i, seg_data in enumerate(segments_data):
        text = seg_data.get("text", "")
        confidence = seg_data.get("confidence", 0.5)
        bbox_data = seg_data.get("bounding_box", {})
        bbox = BoundingBox(x=bbox_data.get("x", 0.0), y=bbox_data.get("y", 0.0), width=bbox_data.get("width", 200.0), height=bbox_data.get("height", 20.0))
        segments.append(OCRSegment(text=text, bounding_box=bbox, confidence=confidence, language=lang, page=bbox_data.get("page", 1)))
    segments.sort(key=lambda s: (s.bounding_box.y, s.bounding_box.x))
    from src.models import OCRResult
    result = OCRResult(material_id=material_id, language=lang, segments=segments)
    return result
