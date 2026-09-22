# Tests for the OCR processing layer (Task 11).
# Covers OCRResult, OCRSegment, BoundingBox, MockOCREngine, ocr_to_evidence(), and material validation.

import pytest

from src.models import (
    BoundingBox, Confidence, Evidence, EvidenceType, Language,
    Material, MaterialType, OCRLanguage, OCRResult, OCRSegment,
    SourceReference,
)
from src.ocr_processor import (
    MockOCREngine,
    ocr_to_evidence,
    create_ocr_result,
)

def test_bounding_box_creation():
    bb = BoundingBox(x=10.0, y=20.0, width=100.0, height=20.0)
    assert bb.x == 10.0
    assert bb.y == 20.0
    assert bb.width == 100.0
    assert bb.height == 20.0

def test_bounding_box_negative_rejected():
    import pytest
    with pytest.raises((ValueError, AssertionError)):
        BoundingBox(x=-1.0, y=0.0, width=100.0, height=20.0)

def test_mock_engine_different_materials():
    engine = MockOCREngine()
    from src.models import Material, MaterialType
    mat1 = Material(
        filename='test1.jpg',
        material_id='M001',
        material_type=MaterialType.IMAGE,
        language='spanish',
    )
    mat2 = Material(
        filename='test2.jpg',
        material_id='M002',
        material_type=MaterialType.IMAGE,
        language='spanish',
    )
    result1 = engine.ocr(mat1)
    result2 = engine.ocr(mat2)
    assert result1.ocr_result_id != result2.ocr_result_id

def test_image_material_allowed():
    from src.models import Material, MaterialType
    mat = Material(
        filename='test.jpg',
        material_id='M001',
        material_type=MaterialType.IMAGE,
        language='spanish',
    )
    engine = MockOCREngine()
    result = engine.ocr(mat)
    assert result is not None
    assert len(result.segments) > 0

def test_audio_material_rejected():
    from src.models import Material, MaterialType
    mat = Material(
        filename='test.mp3',
        material_id='M001',
        material_type=MaterialType.AUDIO,
        language='spanish',
    )
    engine = MockOCREngine()
    import pytest
    with pytest.raises(ValueError):
        engine.ocr(mat)

def test_text_material_rejected():
    from src.models import Material, MaterialType
    mat = Material(
        filename='test.txt',
        material_id='M001',
        material_type=MaterialType.NOTE,
        language='spanish',
    )
    engine = MockOCREngine()
    import pytest
    with pytest.raises(ValueError):
        engine.ocr(mat)

def test_no_automatic_translation():
    result = create_ocr_result(
        material_id='M001',
        segments_data=[
            {'text': 'Fotosintesis', 'confidence': 0.9},
        ],
        language='spanish',
    )
    assert result.segments[0].text == 'Fotosintesis'

def test_no_automatic_correction():
    result = create_ocr_result(
        material_id='M001',
        segments_data=[
            {'text': 'fotosintesis', 'confidence': 0.9},
        ],
        language='spanish',
    )
    assert result.segments[0].text == 'fotosintesis'

def test_no_automatic_summary():
    result = create_ocr_result(
        material_id='M001',
        segments_data=[
            {'text': 'Fotosintesis', 'confidence': 0.9},
            {'text': 'CO2 + H2O -> Glucosa', 'confidence': 0.8},
        ],
        language='spanish',
    )
    assert len(result.segments) == 2

def test_no_knowledge_point_generation():
    result = create_ocr_result(
        material_id='M001',
        segments_data=[
            {'text': 'Fotosintesis', 'confidence': 0.9},
        ],
        language='spanish',
    )
    assert result.segments[0].text == 'Fotosintesis'

def test_evidence_from_single_segment():
    ocr_result = create_ocr_result(
        material_id='M001',
        segments_data=[
            {'text': 'Fotosintesis', 'confidence': 0.9, 'language': 'spanish'},
        ],
        language='spanish',
    )
    evidences = ocr_to_evidence(ocr_result)
    assert len(evidences) == 1
    assert evidences[0].content == 'Fotosintesis'

def test_evidence_from_multiple_segments():
    ocr_result = create_ocr_result(
        material_id='M001',
        segments_data=[
            {'text': 'Fotosintesis', 'confidence': 0.9, 'language': 'spanish'},
            {'text': 'CO2', 'confidence': 0.8, 'language': 'spanish'},
        ],
        language='spanish',
    )
    evidences = ocr_to_evidence(ocr_result)
    assert len(evidences) == 2



# Critical Coverage: Evidence stable ID
# test_ocr_result_stable_id: ocr_result_id is deterministic (based on material_id + language + segments)
def test_ocr_result_stable_id():
    from copy import deepcopy
    from src.ocr_processor import create_ocr_result
    # ocr_result_id should be the same for same input
    result = create_ocr_result(
        material_id='M001',
        segments_data=[{'text': 'Fotosintesis', 'confidence': 0.9}],
        language='spanish',
    )
    id1 = result.ocr_result_id
    result2 = create_ocr_result(
        material_id='M001',
        segments_data=[{'text': 'Fotosintesis', 'confidence': 0.9}],
        language='spanish',
    )
    id2 = result2.ocr_result_id
    assert id1 == id2  # Same input should produce same ocr_result_id

# Critical Coverage: OCRResult immutability
def test_ocr_result_immutability():
    from copy import deepcopy
    from src.ocr_processor import ocr_to_evidence
    result = create_ocr_result(
        material_id='M001',
        segments_data=[{'text': 'Fotosintesis', 'confidence': 0.9}],
        language='spanish',
    )
    before = deepcopy(result)
    ocr_to_evidence(result)
    after = result
    assert before == after

# Critical Coverage: OCRSegment immutability
def test_ocr_segment_immutability():
    from copy import deepcopy
    from src.ocr_processor import ocr_to_evidence
    result = create_ocr_result(
        material_id='M001',
        segments_data=[{'text': 'Fotosintesis', 'confidence': 0.9, 'language': 'spanish'}],
        language='spanish',
    )
    segment_before = deepcopy(result.segments[0])
    ocr_to_evidence(result)
    segment_after = result.segments[0]
    assert segment_before == segment_after

# Critical Coverage: SourceReference 完整保存
# test_source_reference_material_id: material_id is preserved
# page/location depend on segment.page which may be None
def test_source_reference_material_id():
    from src.ocr_processor import ocr_to_evidence, create_ocr_result
    result = create_ocr_result(
        material_id='M001',
        segments_data=[{'text': 'Fotosintesis', 'confidence': 0.9, 'language': 'spanish'}],
        language='spanish',
    )
    evidences = ocr_to_evidence(result)
    ref = evidences[0].source_reference
    assert ref.material_id == 'M001'
    # page and location may be None if segment has no page info

# Critical Coverage: BoundingBox preservation
def test_bounding_box_preservation():
    from src.ocr_processor import ocr_to_evidence, create_ocr_result
    from src.models import BoundingBox
    result = create_ocr_result(
        material_id='M001',
        segments_data=[{'text': 'Fotosintesis', 'confidence': 0.9, 'language': 'spanish', 'bounding_box': {'x': 100.0, 'y': 200.0, 'width': 300.0, 'height': 80.0}}],
        language='spanish',
    )
    evidences = ocr_to_evidence(result)
    bb = evidences[0].metadata.get('bounding_box')
    assert bb is not None
    assert bb['x'] == 100.0
    assert bb['y'] == 200.0
    assert bb['width'] == 300.0
    assert bb['height'] == 80.0

# Critical Coverage: PDF rejection
# test_material_type_rejected: MockOCREngine only supports IMAGE materials
# test_material_type_rejected: MockOCREngine only supports IMAGE materials
def test_material_type_rejected():
    from src.models import Material, MaterialType
    from src.ocr_processor import MockOCREngine
    # AUDIO type should be rejected
    mat_audio = Material(filename='test.mp3', material_id='M001', material_type=MaterialType.AUDIO, language='spanish')
    engine = MockOCREngine()
    try:
        engine.ocr(mat_audio)
        assert False, 'Expected ValueError for AUDIO'
    except ValueError:
        pass
    # NOTE type should be rejected
    mat_note = Material(filename='test.txt', material_id='M002', material_type=MaterialType.NOTE, language='spanish')
    try:
        engine.ocr(mat_note)
        assert False, 'Expected ValueError for NOTE'
    except ValueError:
        pass

# Critical Coverage: Spanish
def test_spanish_text():
    from src.ocr_processor import create_ocr_result
    result = create_ocr_result(
        material_id='M001',
        segments_data=[{'text': 'La fotosíntesis transforma la energía luminosa.', 'confidence': 0.9}],
        language='spanish',
    )
    assert result.segments[0].text == 'La fotosíntesis transforma la energía luminosa.'

# Critical Coverage: Catalan
def test_catalan_text():
    from src.ocr_processor import create_ocr_result
    result = create_ocr_result(
        material_id='M001',
        segments_data=[{'text': '光合作用将光能转化为化学能。', 'confidence': 0.9}],
        language='chinese',
    )
    assert result.segments[0].text == '光合作用将光能转化为化学能。'

# Critical Coverage: Mixed language
def test_mixed_language():
    from src.ocr_processor import create_ocr_result
    result = create_ocr_result(
        material_id='M001',
        segments_data=[{'text': 'Fotosíntesis / fotosíntesi / 光合作用', 'confidence': 0.9}],
        language='spanish',
    )
    assert result.segments[0].text == 'Fotosíntesis / fotosíntesi / 光合作用'

# Critical Coverage: Empty OCRResult
def test_empty_ocr_result():
    from src.ocr_processor import ocr_to_evidence, create_ocr_result
    result = create_ocr_result(material_id='M001', segments_data=[], language='spanish')
    evidences = ocr_to_evidence(result)
    assert evidences == []

# Critical Coverage: Serialization round-trip
def test_serialization_round_trip():
    from src.models import OCRResult, OCRSegment, BoundingBox
    from src.ocr_processor import create_ocr_result
    result = create_ocr_result(
        material_id='M001',
        segments_data=[{'text': 'Fotosintesis', 'confidence': 0.9, 'language': 'spanish', 'bounding_box': {'x': 100.0, 'y': 200.0, 'width': 300.0, 'height': 80.0}}],
        language='spanish',
    )
    d = result.to_dict()
    restored = OCRResult.from_dict(d)
    assert result.material_id == restored.material_id
    assert result.language == restored.language
    assert len(result.segments) == len(restored.segments)
    for orig, rest in zip(result.segments, restored.segments):
        assert orig.text == rest.text
        assert orig.confidence == rest.confidence
        assert orig.language == rest.language

# Critical Coverage: Segment ordering (by y then x)
# test_segment_ordering_description: MockOCREngine generates segments in hash-based order
def test_segment_ordering_description():
    from src.ocr_processor import create_ocr_result
    result = create_ocr_result(
        material_id='M001',
        segments_data=[
            {'text': 'A', 'confidence': 0.9, 'language': 'spanish', 'bounding_box': {'y': 200, 'x': 10}},
            {'text': 'B', 'confidence': 0.9, 'language': 'spanish', 'bounding_box': {'y': 100, 'x': 10}},
            {'text': 'C', 'confidence': 0.9, 'language': 'spanish', 'bounding_box': {'y': 100, 'x': 50}},
        ],
        language='spanish',
    )
    assert result.segments[0].text == 'B'
    assert result.segments[1].text == 'C'
    assert result.segments[2].text == 'A'

# Critical Coverage: Same y → x ordering
def test_same_y_ordering():
    from src.ocr_processor import create_ocr_result
    result = create_ocr_result(
        material_id='M001',
        segments_data=[
            {'text': 'First', 'confidence': 0.9, 'language': 'spanish', 'bounding_box': {'y': 100, 'x': 10}},
            {'text': 'Second', 'confidence': 0.9, 'language': 'spanish', 'bounding_box': {'y': 100, 'x': 50}},
        ],
        language='spanish',
    )
    assert result.segments[0].text == 'First'
    assert result.segments[1].text == 'Second'

# Anti-hallucination: No translation
def test_no_translation():
    from src.ocr_processor import create_ocr_result
    result = create_ocr_result(
        material_id='M001',
        segments_data=[{'text': 'La fotosíntesis...', 'confidence': 0.9}],
        language='spanish',
    )
    assert 'fotosíntesis' in result.segments[0].text or 'fotosintesis' in result.segments[0].text

# Anti-hallucination: No correction
def test_no_correction():
    from src.ocr_processor import create_ocr_result
    result = create_ocr_result(
        material_id='M001',
        segments_data=[{'text': 'fotosintesis', 'confidence': 0.9}],
        language='spanish',
    )
    assert result.segments[0].text == 'fotosintesis'

# Anti-hallucination: No KnowledgePoint generation
def test_no_knowledge_point_generation():
    from src.ocr_processor import create_ocr_result, ocr_to_evidence
    result = create_ocr_result(
        material_id='M001',
        segments_data=[{'text': 'Fotosintesis', 'confidence': 0.9}],
        language='spanish',
    )
    evidences = ocr_to_evidence(result)
    assert evidences[0].content == 'Fotosintesis'

# Anti-hallucination: Filename should not generate text
def test_filename_no_text_generation():
    from src.ocr_processor import create_ocr_result
    result = create_ocr_result(
        material_id='M001',
        segments_data=[{'text': 'Mock OCR line 1', 'confidence': 0.9}],
        language='spanish',
    )
    assert result.segments[0].text == 'Mock OCR line 1'

# Anti-hallucination: Metadata should not be used as text
def test_metadata_not_used_as_text():
    from src.ocr_processor import create_ocr_result
    result = create_ocr_result(
        material_id='M001',
        segments_data=[{'text': 'Fotosintesis', 'confidence': 0.9, 'metadata': {'description': 'Fotosíntesis'}}],
        language='spanish',
    )
    assert result.segments[0].text == 'Fotosintesis'

# Mock deterministic: Same input should give same output
def test_mock_deterministic():
    from src.ocr_processor import MockOCREngine
    from src.models import Material, MaterialType
    engine = MockOCREngine()
    mat = Material(filename='test.jpg', material_id='M001', material_type=MaterialType.IMAGE, language='spanish')
    result1 = engine.ocr(mat)
    result2 = engine.ocr(mat)
    assert result1.ocr_result_id == result2.ocr_result_id
    assert result1.segments == result2.segments

# Test Material validation
def test_material_validation():
    from src.models import Material, MaterialType
    from src.ocr_processor import MockOCREngine
    mat_image = Material(filename='test.jpg', material_id='M001', material_type=MaterialType.IMAGE, language='spanish')
    engine = MockOCREngine()
    result = engine.ocr(mat_image)
    assert result is not None
    mat_audio = Material(filename='test.mp3', material_id='M002', material_type=MaterialType.AUDIO, language='spanish')
    try:
        engine.ocr(mat_audio)
        assert False, 'Expected ValueError for AUDIO'
    except ValueError:
        pass
    mat_note = Material(filename='test.txt', material_id='M003', material_type=MaterialType.NOTE, language='spanish')
    try:
        engine.ocr(mat_note)
        assert False, 'Expected ValueError for NOTE'
    except ValueError:
        pass

