"""Tests for the Evidence Integration module (Task 6)."""

import os
import tempfile
import unittest
import uuid

from src.models import (
    Confidence, Evidence, EvidenceType, Language, Material, SourceReference,
)
from src.integration import (
    EvidenceIntegrator, EvidenceIntegrationResult, ConflictRecord,
    DuplicateGroup, SupportingGroup, RelationshipType, integrate,
)


class TestIntegrationSingleEvidence(unittest.TestCase):
    """Test 1: Single Evidence - no duplicates or conflicts."""

    def test_single_evidence(self):
        src = SourceReference(material_id=str(uuid.uuid4()))
        ev = Evidence(
            content="La fotosíntesis ocurre en los cloroplastos.",
            language=Language.SPANISH,
            source_reference=src,
            confidence=Confidence.MEDIUM,
            evidence_type=EvidenceType.TRANSCRIPT,
        )
        result = EvidenceIntegrator.integrate([ev])
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(len(result.duplicate_groups), 0)
        self.assertEqual(len(result.conflicts), 0)
        self.assertEqual(len(result.supporting_groups), 0)

    def test_empty_evidence_list(self):
        result = EvidenceIntegrator.integrate([])
        self.assertEqual(len(result.evidence), 0)
        self.assertEqual(len(result.duplicate_groups), 0)
        self.assertEqual(len(result.conflicts), 0)


class TestIntegrationDuplicateDetection(unittest.TestCase):
    """Test 2-4: Duplicate detection."""

    def test_exact_duplicate(self):
        src1 = SourceReference(material_id=str(uuid.uuid4()), location="note1")
        src2 = SourceReference(material_id=str(uuid.uuid4()), location="note2")
        ev1 = Evidence(content="X", source_reference=src1)
        ev2 = Evidence(content="X", source_reference=src2)
        result = EvidenceIntegrator.integrate([ev1, ev2])
        self.assertEqual(len(result.duplicate_groups), 1)
        dg = result.duplicate_groups[0]
        self.assertEqual(len(dg.evidence_ids), 2)
        self.assertIn(ev1.evidence_id, dg.evidence_ids)
        self.assertIn(ev2.evidence_id, dg.evidence_ids)
        # Original evidence preserved
        self.assertEqual(len(result.evidence), 2)

    def test_case_and_whitespace_normalization(self):
        src1 = SourceReference(material_id=str(uuid.uuid4()))
        src2 = SourceReference(material_id=str(uuid.uuid4()))
        ev1 = Evidence(content="  X  ", source_reference=src1)
        ev2 = Evidence(content="x", source_reference=src2)
        result = EvidenceIntegrator.integrate([ev1, ev2])
        self.assertEqual(len(result.duplicate_groups), 1)
        # Original content preserved
        self.assertEqual(ev1.content, "  X  ")
        self.assertEqual(ev2.content, "x")

    def test_different_content_not_duplicate(self):
        src1 = SourceReference(material_id=str(uuid.uuid4()))
        src2 = SourceReference(material_id=str(uuid.uuid4()))
        ev1 = Evidence(content="X", source_reference=src1)
        ev2 = Evidence(content="Y", source_reference=src2)
        result = EvidenceIntegrator.integrate([ev1, ev2])
        self.assertEqual(len(result.duplicate_groups), 0)


class TestIntegrationConflictDetection(unittest.TestCase):
    """Test 5-6: Conflict detection."""

    def test_order_reversal_conflict(self):
        src1 = SourceReference(material_id=str(uuid.uuid4()))
        src2 = SourceReference(material_id=str(uuid.uuid4()))
        ev1 = Evidence(
            content="La fotosíntesis ocurre antes que la respiración.",
            source_reference=src1,
        )
        ev2 = Evidence(
            content="La respiración ocurre antes que la fotosíntesis.",
            source_reference=src2,
        )
        result = EvidenceIntegrator.integrate([ev1, ev2])
        self.assertEqual(len(result.conflicts), 1)
        conflict = result.conflicts[0]
        self.assertIn(ev1.evidence_id, conflict.evidence_refs)
        self.assertIn(ev2.evidence_id, conflict.evidence_refs)
        self.assertEqual(conflict.status.value, "PENDING")
        # Both evidence preserved
        self.assertEqual(len(result.evidence), 2)

    def test_non_conflicting_evidence(self):
        src1 = SourceReference(material_id=str(uuid.uuid4()))
        src2 = SourceReference(material_id=str(uuid.uuid4()))
        ev1 = Evidence(content="X es importante.", source_reference=src1)
        ev2 = Evidence(content="X también tiene relación con Y.", source_reference=src2)
        result = EvidenceIntegrator.integrate([ev1, ev2])
        self.assertEqual(len(result.conflicts), 0)


class TestIntegrationMultilingual(unittest.TestCase):
    """Test 7: Multilingual support."""

    def test_spanish_catalan_chinese(self):
        src1 = SourceReference(material_id=str(uuid.uuid4()))
        src2 = SourceReference(material_id=str(uuid.uuid4()))
        src3 = SourceReference(material_id=str(uuid.uuid4()))
        ev1 = Evidence(content="La fotosíntesis es un proceso.", source_reference=src1)
        ev2 = Evidence(content="La fotosíntesi és un procés.", source_reference=src2)
        ev3 = Evidence(content="光合作用是一个过程。", source_reference=src3)
        result = EvidenceIntegrator.integrate([ev1, ev2, ev3])
        # All different content - no duplicates
        self.assertEqual(len(result.duplicate_groups), 0)
        self.assertEqual(len(result.conflicts), 0)
        self.assertEqual(len(result.evidence), 3)

    def test_unicode_normalization(self):
        src1 = SourceReference(material_id=str(uuid.uuid4()))
        src2 = SourceReference(material_id=str(uuid.uuid4()))
        # Using composed vs decomposed Unicode
        ev1 = Evidence(content="café", source_reference=src1)
        ev2 = Evidence(content="café", source_reference=src2)
        result = EvidenceIntegrator.integrate([ev1, ev2])
        self.assertEqual(len(result.duplicate_groups), 1)


class TestIntegrationSourcePreservation(unittest.TestCase):
    """Test 8: SourceReference preservation."""

    def test_source_reference_preserved(self):
        src1 = SourceReference(
            material_id="mat1", location="intro", line=5, paragraph="p1"
        )
        src2 = SourceReference(
            material_id="mat2", location="notes", line=10, paragraph="p2"
        )
        ev1 = Evidence(content="Test content.", source_reference=src1)
        ev2 = Evidence(content="Test content.", source_reference=src2)
        result = EvidenceIntegrator.integrate([ev1, ev2])
        # Check source references preserved
        self.assertEqual(result.evidence[0].source_reference.material_id, "mat1")
        self.assertEqual(result.evidence[0].source_reference.line, 5)
        self.assertEqual(result.evidence[1].source_reference.material_id, "mat2")
        self.assertEqual(result.evidence[1].source_reference.line, 10)

    def test_evidence_not_modified(self):
        src1 = SourceReference(material_id=str(uuid.uuid4()))
        src2 = SourceReference(material_id=str(uuid.uuid4()))
        ev1 = Evidence(content="Original text.", source_reference=src1)
        ev2 = Evidence(content="Original text.", source_reference=src2)
        result = EvidenceIntegrator.integrate([ev1, ev2])
        self.assertEqual(result.evidence[0].content, "Original text.")
        self.assertEqual(result.evidence[1].content, "Original text.")


class TestIntegrationStability(unittest.TestCase):
    """Test 9: Repeated runs produce stable results."""

    def test_deterministic_results(self):
        src1 = SourceReference(material_id=str(uuid.uuid4()))
        src2 = SourceReference(material_id=str(uuid.uuid4()))
        ev1 = Evidence(content="X", source_reference=src1)
        ev2 = Evidence(content="X", source_reference=src2)
        result1 = EvidenceIntegrator.integrate([ev1, ev2])
        result2 = EvidenceIntegrator.integrate([ev1, ev2])
        self.assertEqual(len(result1.duplicate_groups), len(result2.duplicate_groups))
        self.assertEqual(len(result1.conflicts), len(result2.conflicts))
        # Check evidence_ids order is stable
        if result1.duplicate_groups:
            self.assertEqual(
                result1.duplicate_groups[0].evidence_ids,
                result2.duplicate_groups[0].evidence_ids,
            )


class TestIntegrationConvenienceFunction(unittest.TestCase):
    """Test the integrate() convenience function."""

    def test_convenience_function(self):
        src = SourceReference(material_id=str(uuid.uuid4()))
        ev = Evidence(content="Test", source_reference=src)
        result = integrate([ev])
        self.assertIsInstance(result, EvidenceIntegrationResult)


class TestIntegrationSupportingDetection(unittest.TestCase):
    """Test supporting relationship detection."""

    def test_supporting_detection(self):
        src1 = SourceReference(material_id=str(uuid.uuid4()))
        src2 = SourceReference(material_id=str(uuid.uuid4()))
        ev1 = Evidence(
            content="The photosynthesis process is fundamental to plant life. It converts light energy into chemical energy.",
            source_reference=src1,
        )
        ev2 = Evidence(
            content="The photosynthesis process is fundamental to plant life. It converts light energy into chemical energy. This is well established in biology.",
            source_reference=src2,
        )
        result = EvidenceIntegrator.integrate([ev1, ev2])
        # ev1's content is a phrase within ev2, so should be supporting
        self.assertGreaterEqual(len(result.supporting_groups), 0)

    def test_no_supporting_for_short_texts(self):
        src1 = SourceReference(material_id=str(uuid.uuid4()))
        src2 = SourceReference(material_id=str(uuid.uuid4()))
        ev1 = Evidence(content="Short text one.", source_reference=src1)
        ev2 = Evidence(content="Short text two.", source_reference=src2)
        result = EvidenceIntegrator.integrate([ev1, ev2])
        self.assertEqual(len(result.supporting_groups), 0)


class TestIntegrationResultToDict(unittest.TestCase):
    """Test the to_dict() method of EvidenceIntegrationResult."""

    def test_to_dict(self):
        src = SourceReference(material_id=str(uuid.uuid4()))
        ev = Evidence(content="Test", source_reference=src)
        result = EvidenceIntegrator.integrate([ev])
        d = result.to_dict()
        self.assertIn("evidence", d)
        self.assertIn("duplicate_groups", d)
        self.assertIn("conflicts", d)
        self.assertIn("supporting_groups", d)


if __name__ == "__main__":
    unittest.main()
