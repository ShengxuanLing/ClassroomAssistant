import pytest
import sys
sys.path.insert(0, r"D:\Project\Clases")

from src.models import (Language,
    TranscriptLanguage, TranscriptSegment, Transcript,
    Evidence, EvidenceType, Language, Confidence, SourceReference,
)
from src.transcription import (
    TranscriptionEngine, MockTranscriber,
    transcript_to_evidence, create_transcript_from_segments,
)

class TestTranscriptLanguage:
    def test_spanish_value(self):
        assert TranscriptLanguage.SPANISH.value == "Spanish"
    def test_catalan_value(self):
        assert TranscriptLanguage.CATALAN.value == "Catalan"
    def test_chinese_value(self):
        assert TranscriptLanguage.CHINESE.value == "Chinese"
    def test_english_value(self):
        assert TranscriptLanguage.ENGLISH.value == "English"
    def test_unknown_value(self):
        assert TranscriptLanguage.UNKNOWN.value == "Unknown"
    def test_from_string_spanish(self):
        assert TranscriptLanguage.from_string("spanish") == TranscriptLanguage.SPANISH
    def test_from_string_catalan(self):
        assert TranscriptLanguage.from_string("catalan") == TranscriptLanguage.CATALAN
    def test_from_string_unknown(self):
        assert TranscriptLanguage.from_string("french") == TranscriptLanguage.UNKNOWN


class TestTranscriptSegment:
    def test_default_values(self):
        seg = TranscriptSegment()
        assert seg.start == 0.0
        assert seg.end == 0.0
        assert seg.text == ""
        assert seg.speaker == ""
        assert seg.language == TranscriptLanguage.UNKNOWN
        assert seg.confidence == 0.0
    def test_custom_values(self):
        seg = TranscriptSegment(start=1.0, end=3.5, text="Hello", speaker="Teacher", language=TranscriptLanguage.SPANISH, confidence=0.95)
        assert seg.start == 1.0
        assert seg.end == 3.5
        assert seg.text == "Hello"
        assert seg.speaker == "Teacher"
        assert seg.language == TranscriptLanguage.SPANISH
        assert seg.confidence == 0.95
    def test_negative_start_clamped(self):
        seg = TranscriptSegment(start=-1.0, end=2.0, text="Test")
        assert seg.start == 0.0
    def test_end_less_than_start(self):
        seg = TranscriptSegment(start=5.0, end=2.0, text="Test")
        assert seg.end == 5.0
    def test_empty_text(self):
        seg = TranscriptSegment(start=0.0, end=1.0, text="   ")
        assert seg.text == ""
    def test_to_dict(self):
        seg = TranscriptSegment(start=0.0, end=1.5, text="Hola", speaker="Teacher", language=TranscriptLanguage.SPANISH, confidence=0.9)
        d = seg.to_dict()
        assert d["start"] == 0.0
        assert d["end"] == 1.5
        assert d["text"] == "Hola"
        assert d["language"] == "Spanish"
        assert d["confidence"] == 0.9
    def test_from_dict(self):
        data = {"start": 1.0, "end": 2.0, "text": "Hello", "speaker": "Student", "language": "Spanish", "confidence": 0.8}
        seg = TranscriptSegment.from_dict(data)
        assert seg.start == 1.0
        assert seg.text == "Hello"
        assert seg.language == TranscriptLanguage.SPANISH
    def test_from_dict_with_unknown_language(self):
        data = {"start": 0.0, "end": 1.0, "text": "Test"}
        seg = TranscriptSegment.from_dict(data)
        assert seg.language == TranscriptLanguage.UNKNOWN


class TestTranscript:
    def test_stable_id_generation(self):
        t = Transcript(material_id="audio1", language="spanish", segments=[
            TranscriptSegment(start=0.0, end=5.0, text="Hola", language=TranscriptLanguage.SPANISH, confidence=0.9)
        ])
        assert t.transcript_id.startswith("transcript-")
        assert len(t.transcript_id) == 27
    def test_same_input_same_id(self):
        t1 = Transcript(material_id="audio1", language="spanish", segments=[
            TranscriptSegment(start=0.0, end=5.0, text="Hola", language=TranscriptLanguage.SPANISH, confidence=0.9)
        ])
        t2 = Transcript(material_id="audio1", language="spanish", segments=[
            TranscriptSegment(start=0.0, end=5.0, text="Hola", language=TranscriptLanguage.SPANISH, confidence=0.9)
        ])
        assert t1.transcript_id == t2.transcript_id
    def test_different_material_different_id(self):
        t1 = Transcript(material_id="audio1", language="spanish", segments=[])
        t2 = Transcript(material_id="audio2", language="spanish", segments=[])
        assert t1.transcript_id != t2.transcript_id
    def test_to_dict(self):
        t = Transcript(material_id="audio1", language="spanish", segments=[
            TranscriptSegment(start=0.0, end=5.0, text="Hola", language=TranscriptLanguage.SPANISH, confidence=0.9)
        ])
        d = t.to_dict()
        assert d["material_id"] == "audio1"
        assert d["language"] == "Spanish"
        assert len(d["segments"]) == 1
        assert d["segments"][0]["text"] == "Hola"
    def test_from_dict(self):
        data = {
            "transcript_id": "test-id",
            "material_id": "audio1",
            "language": "Spanish",
            "segments": [{"start": 0.0, "end": 5.0, "text": "Hola", "language": "Spanish", "confidence": 0.9}],
            "metadata": {"note": "test"}
        }
        t = Transcript.from_dict(data)
        assert t.transcript_id == "test-id"
        assert t.material_id == "audio1"
        assert t.language == TranscriptLanguage.SPANISH
        assert len(t.segments) == 1
    def test_from_dict_validates_segments(self):
        data = {
            "transcript_id": "test-id",
            "material_id": "audio1",
            "language": "Spanish",
            "segments": [{"start": -1.0, "end": 2.0, "text": "  ", "language": "Spanish", "confidence": 0.9}]
        }
        t = Transcript.from_dict(data)
        assert t.segments[0].start == 0.0
        assert t.segments[0].text == ""
    def test_to_transcript_evidence(self):
        t = Transcript(material_id="audio1", language="spanish", segments=[
            TranscriptSegment(start=0.0, end=5.0, text="Hola", language=TranscriptLanguage.SPANISH, confidence=0.9)
        ])
        ev = t.to_transcript_evidence()
        assert ev.evidence_type == EvidenceType.TRANSCRIPT
        assert ev.language == Language.SPANISH
        assert "Hola" in ev.content
        assert ev.source_reference.material_id == "audio1"
    def test_to_transcript_evidence_empty(self):
        t = Transcript(material_id="audio1", language="spanish", segments=[])
        ev = t.to_transcript_evidence()
        assert ev.evidence_type == EvidenceType.TRANSCRIPT
        assert ev.content == ""
    def test_empty_segments_confidence(self):
        t = Transcript(material_id="audio1", language="spanish", segments=[])
        ev = t.to_transcript_evidence()
        assert ev.confidence == Confidence.MEDIUM

class TestMockTranscriber:
    def test_deterministic_output(self):
        mt = MockTranscriber()
        t1 = mt.transcribe('test_audio.wav')
        t2 = mt.transcribe('test_audio.wav')
        assert t1.transcript_id == t2.transcript_id
        assert len(t1.segments) == len(t2.segments)
        for s1, s2 in zip(t1.segments, t2.segments):
            assert s1.text == s2.text
            assert s1.start == s2.start
            assert s1.end == s2.end

    def test_segment_bounds(self):
        mt = MockTranscriber()
        t = mt.transcribe('test_audio.wav')
        for seg in t.segments:
            assert seg.start >= 0.0
            assert seg.end >= seg.start

    def test_language_handling(self):
        mt = MockTranscriber()
        t = mt.transcribe('test.wav', language='catalan')
        assert t.language == TranscriptLanguage.CATALAN
        for seg in t.segments:
            assert seg.language == TranscriptLanguage.CATALAN

    def test_confidence_bounds(self):
        mt = MockTranscriber()
        t = mt.transcribe('test.wav')
        for seg in t.segments:
            assert 0.5 <= seg.confidence <= 1.0

    def test_speaker_naming(self):
        mt = MockTranscriber()
        t = mt.transcribe('test.wav')
        for seg in t.segments:
            assert seg.speaker.startswith('Speaker_')

    def test_text_content(self):
        mt = MockTranscriber()
        t = mt.transcribe('test_audio.wav')
        for seg in t.segments:
            assert 'Segment' in seg.text
            assert 'test_audio.wav' in seg.text

    def test_transcribe_segments(self):
        mt = MockTranscriber()
        segs = mt.transcribe_segments('path.wav', [
            {'start': 0.0, 'end': 5.0, 'text': 'Hello', 'speaker': 'T1', 'language': 'spanish', 'confidence': 0.9},
            {'start': 5.0, 'end': 10.0, 'text': 'Mundo', 'speaker': 'T2', 'language': 'english', 'confidence': 0.8},
        ])
        assert len(segs) == 2
        assert segs[0].text == 'Hello'
        assert segs[0].speaker == 'T1'
        assert segs[0].confidence == 0.9
        assert segs[1].text == 'Mundo'
        assert segs[1].language == TranscriptLanguage.ENGLISH

    def test_transcribe_segments_with_defaults(self):
        mt = MockTranscriber()
        segs = mt.transcribe_segments('path.wav', [{'start': 1.0, 'end': 2.0}])
        assert len(segs) == 1
        assert segs[0].start == 1.0
        assert segs[0].end == 2.0

    def test_num_segments_based_on_hash(self):
        mt = MockTranscriber()
        t1 = mt.transcribe('audio1.wav')
        t2 = mt.transcribe('audio2.wav')
        assert len(t1.segments) >= 1
        assert len(t1.segments) <= 5
        assert len(t2.segments) >= 1
        assert len(t2.segments) <= 5


class TestTranscriptToEvidence:
    def test_evidence_conversion(self):
        from src.transcription import transcript_to_evidence
        t = Transcript(material_id='audio1', language='spanish', segments=[
            TranscriptSegment(start=0.0, end=5.0, text='Hola', language=TranscriptLanguage.SPANISH, confidence=0.9)
        ])
        ev = transcript_to_evidence(t)
        assert ev.evidence_type == EvidenceType.TRANSCRIPT
        assert ev.language == Language.SPANISH
        assert 'Hola' in ev.content

    def test_content_preservation(self):
        from src.transcription import transcript_to_evidence
        t = Transcript(material_id='audio1', language='spanish', segments=[
            TranscriptSegment(start=0.0, end=3.0, text='First', language=TranscriptLanguage.SPANISH, confidence=0.9),
            TranscriptSegment(start=3.0, end=6.0, text='Second', language=TranscriptLanguage.SPANISH, confidence=0.9),
        ])
        ev = transcript_to_evidence(t)
        assert 'First' in ev.content
        assert 'Second' in ev.content
        assert '\n' in ev.content

    def test_language_preservation(self):
        from src.transcription import transcript_to_evidence
        t = Transcript(material_id='audio1', language='catalan', segments=[
            TranscriptSegment(start=0.0, end=5.0, text='Hola', language=TranscriptLanguage.CATALAN, confidence=0.9)
        ])
        ev = transcript_to_evidence(t)
        assert ev.language == TranscriptLanguage.CATALAN


class TestCreateTranscriptFromSegments:
    def test_factory_function(self):
        from src.transcription import create_transcript_from_segments
        segs_data = [
            {'start': 0.0, 'end': 5.0, 'text': 'Hello', 'speaker': 'T1', 'language': 'spanish', 'confidence': 0.9},
            {'start': 5.0, 'end': 10.0, 'text': 'World', 'speaker': 'T2', 'language': 'english', 'confidence': 0.8},
        ]
        t = create_transcript_from_segments('audio1', segs_data)
        assert t.material_id == 'audio1'
        assert len(t.segments) == 2
        assert t.segments[0].text == 'Hello'
        assert t.segments[1].text == 'World'

    def test_default_language(self):
        from src.transcription import create_transcript_from_segments
        segs_data = [{'start': 0.0, 'end': 5.0, 'text': 'Test', 'language': 'spanish', 'confidence': 0.9}]
        t = create_transcript_from_segments('audio1', segs_data)
        assert t.language == TranscriptLanguage.SPANISH

    def test_stable_id(self):
        from src.transcription import create_transcript_from_segments
        segs_data = [{'start': 0.0, 'end': 5.0, 'text': 'Test', 'language': 'spanish', 'confidence': 0.9}]
        t1 = create_transcript_from_segments('audio1', segs_data)
        t2 = create_transcript_from_segments('audio1', segs_data)
        assert t1.transcript_id == t2.transcript_id
        assert t1.transcript_id.startswith('transcript-')


class TestIntegration:
    def test_full_pipeline(self):
        from src.transcription import MockTranscriber, transcript_to_evidence
        mt = MockTranscriber()
        t = mt.transcribe('audio1.wav')
        assert len(t.segments) >= 1
        ev = transcript_to_evidence(t)
        assert ev.evidence_type == EvidenceType.TRANSCRIPT
        assert len(ev.content) > 0

    def test_roundtrip(self):
        from src.transcription import MockTranscriber, transcript_to_evidence
        from src.models import Evidence, SourceReference
        mt = MockTranscriber()
        t = mt.transcribe('audio1.wav')
        d = t.to_dict()
        t2 = Transcript.from_dict(d)
        assert t2.transcript_id == t.transcript_id
        assert len(t2.segments) == len(t.segments)
        ev = transcript_to_evidence(t2)
        assert ev.evidence_type == EvidenceType.TRANSCRIPT

    def test_create_and_convert(self):
        from src.transcription import create_transcript_from_segments, transcript_to_evidence
        segs_data = [
            {'start': 0.0, 'end': 5.0, 'text': 'Hola', 'speaker': 'T1', 'language': 'spanish', 'confidence': 0.9},
        ]
        t = create_transcript_from_segments('audio1', segs_data, language='spanish')
        ev = transcript_to_evidence(t)
        assert ev.content == 'Hola'
        assert ev.language == Language.SPANISH


class TestAntiHallucination:
    def test_no_real_api(self):
        from src.transcription import MockTranscriber, TranscriptionEngine
        mt = MockTranscriber()
        assert isinstance(mt, TranscriptionEngine)
        t = mt.transcribe('fake_audio.wav')
        assert t is not None
        assert isinstance(t, Transcript)

    def test_deterministic_mock(self):
        from src.transcription import MockTranscriber
        mt = MockTranscriber()
        t1 = mt.transcribe('deterministic_audio.wav')
        t2 = mt.transcribe('deterministic_audio.wav')
        assert t1.transcript_id == t2.transcript_id
        assert t1.segments == t2.segments

    def test_confidence_bounds(self):
        from src.transcription import MockTranscriber
        mt = MockTranscriber()
        t = mt.transcribe('test.wav')
        for seg in t.segments:
            assert seg.confidence >= 0.5
            assert seg.confidence <= 1.0

    def test_no_external_dependencies(self):
        import inspect
        from src.transcription import MockTranscriber
        source = inspect.getsource(MockTranscriber)
        assert 'import' not in source.split('class MockTranscriber')[0] or 'hashlib' in source
        assert 'open(' not in source
        assert 'requests' not in source
        assert 'socket' not in source
