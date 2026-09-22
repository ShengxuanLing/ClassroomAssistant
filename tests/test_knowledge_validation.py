import copy
import json
import unittest

from src.integration import EvidenceIntegrator, ConflictRecord
from src.knowledge_structure import KnowledgeStructure
from src.knowledge_pipeline import KnowledgePipeline, KnowledgeExtractor
from src.knowledge_validation import (
    KnowledgeValidator,
    ValidationReport,
    ValidationResult,
    ValidationStatus,
)
from src.models import (
    Confidence,
    Evidence,
    EvidenceType,
    KnowledgePoint,
    Language,
    SourceReference,
    VerificationStatus,
)


def _make_evidence(eid, content, mat='m1', etype=EvidenceType.PERSONAL_NOTE,
                   lang=Language.SPANISH, conf=Confidence.MEDIUM):
    return Evidence(
        evidence_id=eid,
        content=content,
        language=lang,
        source_reference=SourceReference(material_id=mat),
        confidence=conf,
        evidence_type=etype,
    )


def _structure_from_pipeline(evidence_list):
    pipeline = KnowledgePipeline()
    result = pipeline.process_evidence(list(evidence_list))
    return result.structure, result


# 37.1 No evidence
class TestNoEvidence(unittest.TestCase):
    def test_no_evidence_unverified(self):
        kp = KnowledgePoint(knowledge_id='kp1', title='t', content='c')
        r = KnowledgeValidator.validate_knowledge_point(kp, [], [])
        self.assertEqual(r.status, ValidationStatus.UNVERIFIED)
        self.assertEqual(r.knowledge_score, 0.0)
        self.assertEqual(r.supporting_evidence_ids, ())
        self.assertEqual(r.conflict_ids, ())
        self.assertTrue(r.needs_verification)

    def test_empty_structure_report(self):
        structure = KnowledgeStructure()
        results = KnowledgeValidator.validate_structure(structure, [])
        self.assertEqual(results, {})


# 37.2 One supporting evidence
class TestOneSupporting(unittest.TestCase):
    def test_one_evidence_supported(self):
        ev = _make_evidence('e1', 'La mitosis es una division celular.')
        kp = KnowledgePoint(knowledge_id='kp1', title='t', content='c', evidence_refs=['e1'])
        r = KnowledgeValidator.validate_knowledge_point(kp, [ev], [])
        self.assertEqual(r.status, ValidationStatus.SUPPORTED)
        self.assertEqual(r.knowledge_score, 0.5)
        self.assertEqual(r.supporting_evidence_ids, ('e1',))
        self.assertFalse(r.needs_verification)


# 37.3 Multiple supporting evidence
class TestMultipleSupporting(unittest.TestCase):
    def test_two_evidence_075(self):
        evs = [_make_evidence('e1', 'mitosis antes que meiosis.'), _make_evidence('e2', 'mitosis antes que meiosis.')]
        kp = KnowledgePoint(knowledge_id='kp1', title='t', content='c', evidence_refs=['e1', 'e2'])
        r = KnowledgeValidator.validate_knowledge_point(kp, evs, [])
        self.assertEqual(r.status, ValidationStatus.SUPPORTED)
        self.assertEqual(r.knowledge_score, 0.75)
        self.assertEqual(r.supporting_evidence_ids, ('e1', 'e2'))

    def test_three_plus_evidence_09(self):
        evs = [_make_evidence('e%d' % i, 'concepto %d' % i) for i in range(1, 4)]
        kp = KnowledgePoint(knowledge_id='kp1', title='t', content='c',
                             evidence_refs=['e1', 'e2', 'e3'])
        r = KnowledgeValidator.validate_knowledge_point(kp, evs, [])
        self.assertEqual(r.knowledge_score, 0.9)

    def test_duplicate_listed_refs_counted_once(self):
        evs = [_make_evidence('e1', 'concepto 1'), _make_evidence('e2', 'concepto 2')]
        kp = KnowledgePoint(knowledge_id='kp1', title='t', content='c',
                             evidence_refs=['e1', 'e1', 'e2', 'e1'])
        r = KnowledgeValidator.validate_knowledge_point(kp, evs, [])
        self.assertEqual(len(r.supporting_evidence_ids), 2)


# 37.4 Duplicate evidence resubmission
class TestDuplicateEvidence(unittest.TestCase):
    def test_duplicate_evidence_not_double_counted(self):
        ev1 = _make_evidence('e1', 'concepto 1')
        ev1_copy = Evidence(content=ev1.content, language=ev1.language,
                            source_reference=SourceReference(material_id='m1'),
                            confidence=ev1.confidence, evidence_type=ev1.evidence_type,
                            evidence_id='e1')
        kp = KnowledgePoint(knowledge_id='kp1', title='t', content='c', evidence_refs=['e1'])
        r = KnowledgeValidator.validate_knowledge_point(kp, [ev1, ev1_copy], [])
        self.assertEqual(len(r.supporting_evidence_ids), 1)
        self.assertEqual(r.status, ValidationStatus.SUPPORTED)

    def test_pipeline_duplicate_submission_idempotent(self):
        ev1 = _make_evidence('e1', 'mitosis antes que meiosis.')
        pipeline = KnowledgePipeline()
        r1 = pipeline.process_evidence([ev1, ev1])
        structure = r1.structure
        self.assertEqual(len(structure.knowledge_points), 1)
        kp = next(iter(structure.knowledge_points.values()))
        self.assertEqual(len(set(kp.evidence_refs)), 1)


# 37.5 Conflict -> CONFLICTED + needs_verification
class TestConflictDetection(unittest.TestCase):
    def test_conflict_marks_conflicted(self):
        ev_a = _make_evidence('ea', 'mitosis antes que meiosis.')
        ev_b = _make_evidence('eb', 'meiosis antes que mitosis.')
        structure, result = _structure_from_pipeline([ev_a, ev_b])
        kp = next(iter(structure.knowledge_points.values()))
        self.assertEqual(len(structure.conflicts), 1)
        self.assertEqual(kp.validation_status, 'conflicted')
        self.assertTrue(kp.needs_verification)
        self.assertEqual(kp.knowledge_score, 0.5)

    def test_conflict_status_enum(self):
        ev_a = _make_evidence('ea', 'mitosis antes que meiosis.')
        ev_b = _make_evidence('eb', 'meiosis antes que mitosis.')
        structure, _ = _structure_from_pipeline([ev_a, ev_b])
        kp = next(iter(structure.knowledge_points.values()))
        self.assertEqual(ValidationStatus.from_string(kp.validation_status),
                           ValidationStatus.CONFLICTED)


# 37.6 Duplicate conflict not double counted
class TestDuplicateConflict(unittest.TestCase):
    def test_conflict_count_dedup_by_id(self):
        ev_a = _make_evidence('ea', 'mitosis antes que meiosis.')
        ev_b = _make_evidence('eb', 'meiosis antes que mitosis.')
        structure, _ = _structure_from_pipeline([ev_a, ev_b, ev_a, ev_b])
        kp = next(iter(structure.knowledge_points.values()))
        self.assertEqual(structure.conflict_count(kp.knowledge_id), 1)

    def test_same_conflict_record_twice(self):
        c1 = ConflictRecord(conflict_id='c1', evidence_refs=['ea', 'eb'], description='d')
        c2 = ConflictRecord(conflict_id='c1', evidence_refs=['ea', 'eb'], description='d')
        kp = KnowledgePoint(knowledge_id='kp1', title='t', content='c', evidence_refs=['ea', 'eb'])
        r = KnowledgeValidator.validate_knowledge_point(kp, [], [c1, c2])
        self.assertEqual(r.conflict_ids, ('c1',))
        self.assertEqual(r.status, ValidationStatus.CONFLICTED)


# 37.7 Support + conflict coexist
class TestSupportAndConflict(unittest.TestCase):
    def test_supporting_evidence_preserved_with_conflict(self):
        ev_a = _make_evidence('ea', 'mitosis antes que meiosis.')
        ev_b = _make_evidence('eb', 'meiosis antes que mitosis.')
        ev_c = _make_evidence('ec', 'mitosis antes que meiosis.')
        structure, _ = _structure_from_pipeline([ev_a, ev_b, ev_c])
        for kp in structure.knowledge_points.values():
            r = KnowledgeValidator.validate_knowledge_point(kp, [ev_a, ev_b, ev_c], structure.conflicts)
            if r.status == ValidationStatus.CONFLICTED:
                self.assertGreaterEqual(len(r.supporting_evidence_ids), 1,
                                   'supporting evidence must be preserved')


# 37.8 Incremental conflict: SUPPORTED -> CONFLICTED
class TestIncrementalConflict(unittest.TestCase):
    def test_supported_then_conflicted(self):
        pipeline = KnowledgePipeline()
        ev_a = _make_evidence('ea', 'mitosis antes que meiosis.')
        s1 = pipeline.process_evidence([ev_a]).structure
        kp = next(iter(s1.knowledge_points.values()))
        self.assertEqual(kp.validation_status, 'supported')
        ev_b = _make_evidence('eb', 'meiosis antes que mitosis.')
        s2 = pipeline.process_evidence([ev_a, ev_b], structure=s1).structure
        self.assertIs(s2, s1)
        kp2 = next(iter(s2.knowledge_points.values()))
        self.assertEqual(kp2.validation_status, 'conflicted')
        self.assertTrue(kp2.needs_verification)


# 37.9 Existing conflict preserved when unrelated KP added
class TestConflictPreservation(unittest.TestCase):
    def test_unrelated_kp_does_not_remove_conflict(self):
        ev_a = _make_evidence('ea', 'mitosis antes que meiosis.')
        ev_b = _make_evidence('eb', 'meiosis antes que mitosis.')
        s1, _ = _structure_from_pipeline([ev_a, ev_b])
        n_conflicts_before = len(s1.conflicts)
        self.assertGreaterEqual(n_conflicts_before, 1)
        ev_c = _make_evidence('ec', 'El agua es necesaria para la vida.')
        s2 = KnowledgePipeline().process_evidence([ev_a, ev_b, ev_c], structure=s1).structure
        self.assertGreaterEqual(len(s2.conflicts), n_conflicts_before)


# 37.10 Multilingual: no false conflicts across languages
class TestMultilingualValidation(unittest.TestCase):
    def test_spanish_supported(self):
        ev = _make_evidence('es', 'Los recursos naturales son limitados.',
                            etype=EvidenceType.PERSONAL_NOTE, lang=Language.SPANISH)
        r = KnowledgeValidator.validate_knowledge_point(
            KnowledgePoint(knowledge_id='k', title='t', content='c', evidence_refs=['es']),
            [ev], [])
        self.assertEqual(r.status, ValidationStatus.SUPPORTED)

    def test_catalan_supported(self):
        ev = _make_evidence('ca', 'Els recursos naturals son limitats.',
                            etype=EvidenceType.PERSONAL_NOTE, lang=Language.CATALAN)
        r = KnowledgeValidator.validate_knowledge_point(
            KnowledgePoint(knowledge_id='k', title='t', content='c', evidence_refs=['ca']),
            [ev], [])
        self.assertEqual(r.status, ValidationStatus.SUPPORTED)

    def test_chinese_supported(self):
        ev = _make_evidence('zh', '自然资源是有限的。',
                            etype=EvidenceType.PERSONAL_NOTE, lang=Language.CHINESE)
        r = KnowledgeValidator.validate_knowledge_point(
            KnowledgePoint(knowledge_id='k', title='t', content='c', evidence_refs=['zh']),
            [ev], [])
        self.assertEqual(r.status, ValidationStatus.SUPPORTED)

    def test_different_languages_not_conflict(self):
        ev_es = _make_evidence('es', 'Los recursos naturales son limitados.', lang=Language.SPANISH)
        ev_ca = _make_evidence('ca', 'Els recursos naturals son limitats.', lang=Language.CATALAN)
        ev_zh = _make_evidence('zh', '自然资源是有限的。', lang=Language.CHINESE)
        kp = KnowledgePoint(knowledge_id='k', title='t', content='c',
                            evidence_refs=['es', 'ca', 'zh'])
        r = KnowledgeValidator.validate_knowledge_point(kp, [ev_es, ev_ca, ev_zh], [])
        self.assertEqual(r.status, ValidationStatus.SUPPORTED)
        self.assertEqual(r.conflict_ids, ())
        self.assertEqual(len(r.supporting_evidence_ids), 3)


# 37.11/12/13 NOTE, TRANSCRIPT, OCR evidence support KPs
class TestEvidenceTypeCoverage(unittest.TestCase):
    def _validate_with_type(self, etype):
        ev = _make_evidence('e', 'concepto importante.', etype=etype)
        kp = KnowledgePoint(knowledge_id='k', title='t', content='c', evidence_refs=['e'])
        r = KnowledgeValidator.validate_knowledge_point(kp, [ev], [])
        self.assertEqual(r.status, ValidationStatus.SUPPORTED, 'type=%s' % etype)
        return ev

    def test_note_evidence(self):
        self._validate_with_type(EvidenceType.PERSONAL_NOTE)

    def test_transcript_evidence(self):
        self._validate_with_type(EvidenceType.TRANSCRIPT)

    def test_ocr_evidence(self):
        self._validate_with_type(EvidenceType.OCR)

    def test_ocr_traceability_preserved(self):
        src = SourceReference(material_id='m-ocr', page=3, line=12)
        ev = Evidence(content='grafica de la mitosis.', language=Language.SPANISH,
                    source_reference=src, confidence=Confidence.HIGH,
                    evidence_type=EvidenceType.OCR, evidence_id='ocr1')
        kp = KnowledgePoint(knowledge_id='k', title='t', content='c', evidence_refs=['ocr1'])
        structure = KnowledgeStructure()
        structure.add_knowledge_point(kp)
        results = KnowledgeValidator.validate_structure(structure, [ev])
        r = results['k']
        self.assertIn('ocr1', r.supporting_evidence_ids)
        self.assertEqual(ev.source_reference.material_id, 'm-ocr')
        self.assertEqual(ev.source_reference.page, 3)

    def test_audio_timestamp_traceability(self):
        src = SourceReference(material_id='m-audio', timestamp_start=12.5, timestamp_end=30.0)
        ev = Evidence(content='explicacion del profesor.', language=Language.SPANISH,
                    source_reference=src, confidence=Confidence.HIGH,
                    evidence_type=EvidenceType.TRANSCRIPT, evidence_id='aud1')
        r = KnowledgeValidator.validate_knowledge_point(
            KnowledgePoint(knowledge_id='k', title='t', content='c', evidence_refs=['aud1']), [ev], [])
        self.assertIn('aud1', r.supporting_evidence_ids)
        self.assertEqual(ev.source_reference.timestamp_start, 12.5)


# 37.14 KnowledgePoint -> Evidence -> Material traceability
class TestTraceability(unittest.TestCase):
    def test_kp_to_evidence_to_material(self):
        ev = _make_evidence('e1', 'concepto 1', mat='material-42')
        kp = KnowledgePoint(knowledge_id='k', title='t', content='c', evidence_refs=['e1'])
        r = KnowledgeValidator.validate_knowledge_point(kp, [ev], [])
        self.assertEqual(r.supporting_evidence_ids, ('e1',))
        self.assertEqual(ev.source_reference.material_id, 'material-42')


# 37.15 Confidence bounds
class TestConfidenceBounds(unittest.TestCase):
    def test_score_always_in_range(self):
        from src.knowledge_validation import _knowledge_score
        for n in range(0, 10):
            for c in range(0, 4):
                s = _knowledge_score(n, c)
                self.assertGreaterEqual(s, 0.0, 'n=%d,c=%d' % (n, c))
                self.assertLessEqual(s, 1.0, 'n=%d,c=%d' % (n, c))

    def test_pipeline_kp_scores_in_range(self):
        evs = [_make_evidence('e%d' % i, 'concepto %d' % i) for i in range(3)]
        structure, _ = _structure_from_pipeline(evs)
        for kp in structure.knowledge_points.values():
            self.assertGreaterEqual(kp.knowledge_score, 0.0)
            self.assertLessEqual(kp.knowledge_score, 1.0)


# 37.16 Determinism
class TestDeterminism(unittest.TestCase):
    def test_same_input_same_output(self):
        evs = [_make_evidence('e%d' % i, 'concepto %d' % i) for i in range(3)]
        kp = KnowledgePoint(knowledge_id='k', title='t', content='c',
                            evidence_refs=['e1', 'e2', 'e3'])
        r1 = KnowledgeValidator.validate_knowledge_point(kp, evs, [])
        r2 = KnowledgeValidator.validate_knowledge_point(kp, list(reversed(evs)), [])
        self.assertEqual(r1.status, r2.status)
        self.assertEqual(r1.knowledge_score, r2.knowledge_score)
        self.assertEqual(sorted(r1.supporting_evidence_ids), sorted(r2.supporting_evidence_ids))


# 37.17 Idempotency
class TestIdempotency(unittest.TestCase):
    def test_update_update_identical(self):
        evs = [_make_evidence('e1', 'concepto 1'), _make_evidence('e2', 'concepto 2')]
        s1, _ = _structure_from_pipeline(evs)
        pipeline = KnowledgePipeline()
        s2 = pipeline.process_evidence(evs, structure=s1).structure
        self.assertIs(s2, s1)
        self.assertEqual(len(s1.knowledge_points), 2)
        for kp in s1.knowledge_points.values():
            self.assertEqual(len(set(kp.evidence_refs)), len(list(set(kp.evidence_refs))))

    def test_conflict_resubmission_no_duplicate(self):
        ev_a = _make_evidence('ea', 'mitosis antes que meiosis.')
        ev_b = _make_evidence('eb', 'meiosis antes que mitosis.')
        pipeline = KnowledgePipeline()
        s1 = pipeline.process_evidence([ev_a, ev_b]).structure
        n1 = len(s1.conflicts)
        s2 = pipeline.process_evidence([ev_a, ev_b, ev_a, ev_b], structure=s1).structure
        self.assertEqual(len(s2.conflicts), n1)
        self.assertIs(s2, s1)


# 37.18 Serialization round-trip
class TestSerialization(unittest.TestCase):
    def test_structure_roundtrip_preserves_validation(self):
        ev_a = _make_evidence('ea', 'mitosis antes que meiosis.')
        ev_b = _make_evidence('eb', 'meiosis antes que mitosis.')
        structure, _ = _structure_from_pipeline([ev_a, ev_b])
        data = structure.to_dict()
        json.dumps(data)
        restored = KnowledgeStructure.from_dict(data)
        kp = next(iter(restored.knowledge_points.values()))
        self.assertEqual(kp.validation_status, 'conflicted')
        self.assertEqual(kp.knowledge_score, 0.5)
        self.assertTrue(kp.needs_verification)
        self.assertEqual(len(restored.conflicts), len(structure.conflicts))

    def test_knowledge_point_serialization(self):
        kp = KnowledgePoint(knowledge_id='k', title='t', content='c',
                            validation_status='supported', knowledge_score=0.75,
                            evidence_refs=['e1', 'e2'])
        d = kp.to_dict()
        self.assertEqual(d['validation_status'], 'supported')
        self.assertEqual(d['knowledge_score'], 0.75)
        kp2 = KnowledgePoint.from_dict(d)
        self.assertEqual(kp2.validation_status, 'supported')
        self.assertEqual(kp2.knowledge_score, 0.75)


# 37.19 Backward compatibility
class TestBackwardCompatibility(unittest.TestCase):
    def test_old_kp_data_reads_with_defaults(self):
        old_data = {
            'knowledge_id': 'k', 'title': 't', 'content': 'c',
            'original_terms': [], 'importance': 'medium',
            'confidence': 'UNCERTAIN', 'evidence_refs': [],
            'related_points': [], 'needs_verification': False,
        }
        kp = KnowledgePoint.from_dict(old_data)
        self.assertEqual(kp.validation_status, 'unverified')
        self.assertEqual(kp.knowledge_score, 0.0)

    def test_old_structure_data_reads(self):
        old_structure_data = {
            'knowledge_points': [
                {'knowledge_id': 'k1', 'title': 't', 'content': 'c',
                 'original_terms': [], 'importance': 'medium',
                 'confidence': 'MEDIUM', 'evidence_refs': ['e1'],
                 'related_points': [], 'needs_verification': False}
            ],
            'relationships': [],
            'conflicts': [],
        }
        restored = KnowledgeStructure.from_dict(old_structure_data)
        self.assertEqual(restored.get_knowledge_point('k1').validation_status, 'unverified')

    def test_kp_coerces_invalid_validation_status(self):
        kp = KnowledgePoint(knowledge_id='k', title='t', content='c',
                            validation_status='nonsense')
        self.assertEqual(kp.validation_status, 'unverified')

    def test_kp_clamps_out_of_range_score(self):
        kp = KnowledgePoint(knowledge_id='k', title='t', content='c',
                            knowledge_score=1.7)
        self.assertEqual(kp.knowledge_score, 1.0)
        kp2 = KnowledgePoint(knowledge_id='k', title='t', content='c',
                            knowledge_score=-0.5)
        self.assertEqual(kp2.knowledge_score, 0.0)


# 37.20 Immutability / read-only validation
class TestReadOnlyValidation(unittest.TestCase):
    def test_validation_does_not_mutate_evidence(self):
        ev = _make_evidence('e1', 'concepto 1')
        snapshot = copy.deepcopy(ev.to_dict())
        kp = KnowledgePoint(knowledge_id='k', title='t', content='c', evidence_refs=['e1'])
        KnowledgeValidator.validate_knowledge_point(kp, [ev], [])
        KnowledgeValidator.validate_knowledge_point(kp, [ev], [])
        self.assertEqual(ev.to_dict(), snapshot)

    def test_supporting_evidence_count_unique(self):
        structure = KnowledgeStructure()
        kp = KnowledgePoint(knowledge_id='k', title='t', content='c',
                            evidence_refs=['e1', 'e1', 'e2'])
        structure.add_knowledge_point(kp)
        self.assertEqual(structure.supporting_evidence_count('k'), 2)
        self.assertEqual(structure.supporting_evidence_ids('k'), ['e1', 'e2'])

    def test_unknown_kp_returns_empty(self):
        structure = KnowledgeStructure()
        self.assertEqual(structure.supporting_evidence_ids('nope'), [])
        self.assertEqual(structure.conflict_count('nope'), 0)


if __name__ == '__main__':
    unittest.main()
