"""Tests for the Classroom Assistant models."""

import unittest
import uuid
from src.models import (
    Language, MaterialType, Confidence, EvidenceType, VerificationStatus,
    SourceReference, Material, Evidence, KnowledgePoint, ClassSession, VerificationItem
)

class TestEnums(unittest.TestCase):
    def test_language_from_string(self):
        self.assertEqual(Language.from_string("spanish"), Language.SPANISH)
        self.assertEqual(Language.from_string("catalan"), Language.CATALAN)
        self.assertEqual(Language.from_string("chinese"), Language.CHINESE)
        self.assertEqual(Language.from_string("english"), Language.ENGLISH)
        self.assertEqual(Language.from_string("unknown"), Language.UNKNOWN)
        self.assertEqual(Language.from_string("french"), Language.UNKNOWN)

    def test_material_type_from_string(self):
        self.assertEqual(MaterialType.from_string("audio"), MaterialType.AUDIO)
        self.assertEqual(MaterialType.from_string("image"), MaterialType.IMAGE)
        self.assertEqual(MaterialType.from_string("text"), MaterialType.TEXT)
        self.assertEqual(MaterialType.from_string("note"), MaterialType.NOTE)
        self.assertEqual(MaterialType.from_string("syllabus"), MaterialType.SYLLABUS)

    def test_confidence_from_string(self):
        self.assertEqual(Confidence.from_string("high"), Confidence.HIGH)
        self.assertEqual(Confidence.from_string("medium"), Confidence.MEDIUM)
        self.assertEqual(Confidence.from_string("low"), Confidence.LOW)
        self.assertEqual(Confidence.from_string("uncertain"), Confidence.UNCERTAIN)

    def test_evidence_type_from_string(self):
        self.assertEqual(EvidenceType.from_string("transcript"), EvidenceType.TRANSCRIPT)
        self.assertEqual(EvidenceType.from_string("ocr"), EvidenceType.OCR)
        self.assertEqual(EvidenceType.from_string("personal_note"), EvidenceType.PERSONAL_NOTE)
        self.assertEqual(EvidenceType.from_string("classmate_note"), EvidenceType.CLASSMATE_NOTE)
        self.assertEqual(EvidenceType.from_string("teacher_statement"), EvidenceType.TEACHER_STATEMENT)
        self.assertEqual(EvidenceType.from_string("extracted_fact"), EvidenceType.EXTRACTED_FACT)
        self.assertEqual(EvidenceType.from_string("other"), EvidenceType.OTHER)

    def test_verification_status_from_string(self):
        self.assertEqual(VerificationStatus.from_string("pending"), VerificationStatus.PENDING)
        self.assertEqual(VerificationStatus.from_string("confirmed"), VerificationStatus.CONFIRMED)
        self.assertEqual(VerificationStatus.from_string("rejected"), VerificationStatus.REJECTED)

class TestMaterial(unittest.TestCase):
    def test_material_auto_generates_id(self):
        m = Material(filename="test.mp3", path="/tmp/test.mp3", material_type=MaterialType.AUDIO, language=Language.SPANISH)
        self.assertIsNotNone(m.material_id)
        self.assertTrue(isinstance(uuid.UUID(m.material_id), uuid.UUID))

    def test_material_explicit_id(self):
        custom_id = str(uuid.uuid4())
        m = Material(material_id=custom_id, filename="test.mp3", path="/tmp/test.mp3", material_type=MaterialType.AUDIO, language=Language.SPANISH)
        self.assertEqual(m.material_id, custom_id)

    def test_material_roundtrip(self):
        m = Material(filename="test.mp3", path="/tmp/test.mp3", material_type=MaterialType.AUDIO, language=Language.SPANISH)
        d = m.to_dict()
        m2 = Material.from_dict(d)
        self.assertEqual(m.material_id, m2.material_id)
        self.assertEqual(m.filename, m2.filename)
        self.assertEqual(m.path, m2.path)
        self.assertEqual(m.material_type, m2.material_type)
        self.assertEqual(m.language, m2.language)

    def test_material_type_coercion(self):
        m = Material(filename="test.txt", path="/tmp/test.txt", material_type="text", language="spanish")
        self.assertEqual(m.material_type, MaterialType.TEXT)
        self.assertEqual(m.language, Language.SPANISH)

    def test_material_spanish_catalan_chinese(self):
        for lang in [Language.SPANISH, Language.CATALAN, Language.CHINESE, Language.ENGLISH]:
            m = Material(filename="test.mp3", path="/tmp/test.mp3", material_type=MaterialType.AUDIO, language=lang)
            self.assertEqual(m.language, lang)
            d = m.to_dict()
            self.assertEqual(d["language"], lang.value)

class TestEvidence(unittest.TestCase):
    def test_evidence_auto_generates_id(self):
        src = SourceReference(material_id=str(uuid.uuid4()))
        e = Evidence(content="test content", language=Language.SPANISH, source_reference=src, confidence=Confidence.HIGH, evidence_type=EvidenceType.TRANSCRIPT)
        self.assertIsNotNone(e.evidence_id)

    def test_evidence_roundtrip(self):
        src = SourceReference(material_id=str(uuid.uuid4()), location="intro", timestamp_start=1.0, timestamp_end=2.0)
        e = Evidence(content="test content", language=Language.SPANISH, source_reference=src, confidence=Confidence.HIGH, evidence_type=EvidenceType.TRANSCRIPT)
        d = e.to_dict()
        e2 = Evidence.from_dict(d)
        self.assertEqual(e.evidence_id, e2.evidence_id)
        self.assertEqual(e.content, e2.content)
        self.assertEqual(e.language, e2.language)
        self.assertEqual(e.confidence, e2.confidence)
        self.assertEqual(e.evidence_type, e2.evidence_type)

    def test_evidence_source_reference_coercion(self):
        src_dict = {"material_id": str(uuid.uuid4()), "location": "intro"}
        e = Evidence(content="test", language=Language.SPANISH, source_reference=src_dict, confidence=Confidence.MEDIUM, evidence_type=EvidenceType.OCR)
        self.assertIsInstance(e.source_reference, SourceReference)

    def test_evidence_with_catalan(self):
        src = SourceReference(material_id=str(uuid.uuid4()))
        e = Evidence(content="contingut", language=Language.CATALAN, source_reference=src, confidence=Confidence.LOW, evidence_type=EvidenceType.CLASSMATE_NOTE)
        self.assertEqual(e.language, Language.CATALAN)

class TestKnowledgePoint(unittest.TestCase):
    def test_knowledge_point_auto_generates_id(self):
        kp = KnowledgePoint(title="Test Concept", content="Definition here")
        self.assertIsNotNone(kp.knowledge_id)

    def test_knowledge_point_roundtrip(self):
        kp = KnowledgePoint(title="Test Concept", content="Definition here", original_terms=["concepto", "termino"], importance="high")
        d = kp.to_dict()
        kp2 = KnowledgePoint.from_dict(d)
        self.assertEqual(kp.knowledge_id, kp2.knowledge_id)
        self.assertEqual(kp.title, kp2.title)
        self.assertEqual(kp.original_terms, kp2.original_terms)

    def test_knowledge_point_evidence_refs(self):
        evid_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        kp = KnowledgePoint(title="Test", content="Content", evidence_refs=evid_ids)
        self.assertEqual(kp.evidence_refs, evid_ids)
        d = kp.to_dict()
        kp2 = KnowledgePoint.from_dict(d)
        self.assertEqual(kp2.evidence_refs, evid_ids)

    def test_knowledge_point_confidence_coercion(self):
        kp = KnowledgePoint(title="Test", content="Content", confidence="high")
        self.assertEqual(kp.confidence, Confidence.HIGH)

    def test_knowledge_point_importance_lowercase(self):
        kp = KnowledgePoint(title="Test", content="Content", importance="HIGH")
        self.assertEqual(kp.importance, "high")

class TestVerificationItem(unittest.TestCase):
    def test_verification_auto_generates_id(self):
        v = VerificationItem(description="Test", reason="Need to verify")
        self.assertIsNotNone(v.verification_id)

    def test_verification_roundtrip(self):
        v = VerificationItem(description="Test", reason="Need to verify", status=VerificationStatus.PENDING)
        d = v.to_dict()
        v2 = VerificationItem.from_dict(d)
        self.assertEqual(v.verification_id, v2.verification_id)
        self.assertEqual(v.status, v2.status)

    def test_verification_status_transitions(self):
        v = VerificationItem(description="Test", reason="Check")
        self.assertEqual(v.status, VerificationStatus.PENDING)
        v2 = VerificationItem.from_dict({"verification_id": v.verification_id, "description": "Test", "reason": "Check", "status": "confirmed"})
        self.assertEqual(v2.status, VerificationStatus.CONFIRMED)
        v3 = VerificationItem.from_dict({"verification_id": str(uuid.uuid4()), "description": "Test", "reason": "Check", "status": "rejected"})
        self.assertEqual(v3.status, VerificationStatus.REJECTED)

class TestClassSession(unittest.TestCase):
    def test_class_session_auto_generates_id(self):
        cs = ClassSession(course_id="CS101", date="2026-09-08", title="Lecture 1")
        self.assertIsNotNone(cs.session_id)

    def test_class_session_roundtrip(self):
        cs = ClassSession(course_id="CS101", date="2026-09-08", title="Lecture 1", material_refs=["m1", "m2"], knowledge_point_refs=["k1"])
        d = cs.to_dict()
        cs2 = ClassSession.from_dict(d)
        self.assertEqual(cs.session_id, cs2.session_id)
        self.assertEqual(cs.course_id, cs2.course_id)
        self.assertEqual(cs.material_refs, cs2.material_refs)
        self.assertEqual(cs.knowledge_point_refs, cs2.knowledge_point_refs)

    def test_class_session_with_all_refs(self):
        cs = ClassSession(course_id="CS101", date="2026-09-08", title="Lecture 1", material_refs=["m1"], evidence_refs=["e1"], knowledge_point_refs=["k1"], verification_refs=["v1"])
        self.assertEqual(cs.material_refs, ["m1"])
        self.assertEqual(cs.evidence_refs, ["e1"])
        self.assertEqual(cs.knowledge_point_refs, ["k1"])
        self.assertEqual(cs.verification_refs, ["v1"])

class TestSourceReference(unittest.TestCase):
    def test_source_reference_roundtrip(self):
        src = SourceReference(material_id=str(uuid.uuid4()), location="intro", timestamp_start=1.0, timestamp_end=2.0, page=5, line=10, paragraph="first")
        d = src.to_dict()
        src2 = SourceReference.from_dict(d)
        self.assertEqual(src.material_id, src2.material_id)
        self.assertEqual(src.location, src2.location)
        self.assertEqual(src.timestamp_start, src2.timestamp_start)
        self.assertEqual(src.page, src2.page)
        self.assertEqual(src.line, src2.line)
        self.assertEqual(src.paragraph, src2.paragraph)

class TestMixedLanguageScenarios(unittest.TestCase):
    def test_spanish_catalan_mixed(self):
        m1 = Material(filename="spanish.mp3", path="/tmp/s.mp3", material_type=MaterialType.AUDIO, language=Language.SPANISH)
        m2 = Material(filename="catalan.mp3", path="/tmp/c.mp3", material_type=MaterialType.AUDIO, language=Language.CATALAN)
        m3 = Material(filename="mixed.mp3", path="/tmp/m.mp3", material_type=MaterialType.AUDIO, language=Language.ENGLISH)
        self.assertEqual(m1.language, Language.SPANISH)
        self.assertEqual(m2.language, Language.CATALAN)
        self.assertEqual(m3.language, Language.ENGLISH)

    def test_evidence_with_spanish_catalan_chinese(self):
        for lang in [Language.SPANISH, Language.CATALAN, Language.CHINESE, Language.UNKNOWN]:
            src = SourceReference(material_id=str(uuid.uuid4()))
            e = Evidence(content="test", language=lang, source_reference=src, confidence=Confidence.MEDIUM, evidence_type=EvidenceType.TRANSCRIPT)
            self.assertEqual(e.language, lang)

if __name__ == "__main__":
    unittest.main()
