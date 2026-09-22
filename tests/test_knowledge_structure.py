import unittest
import uuid
from src.models import KnowledgePoint, Confidence, SourceReference
from src.knowledge_structure import KnowledgeStructure, Relationship, RelationType
class TestKnowledgeStructureEmpty(unittest.TestCase):
    def test_empty_structure(self):
        ks = KnowledgeStructure()
        self.assertEqual(len(ks.knowledge_points), 0)
        self.assertEqual(len(ks.relationships), 0)
    def test_get_relationships_empty(self):
        ks = KnowledgeStructure()
        self.assertEqual(len(ks.get_relationships()), 0)
class TestAddKnowledgePoints(unittest.TestCase):
    def test_add_single_kp(self):
        ks = KnowledgeStructure()
        kp = KnowledgePoint(title='test', content='test content')
        result = ks.add_knowledge_point(kp)
        self.assertTrue(result)
        self.assertEqual(len(ks.knowledge_points), 1)
        self.assertIn(kp.knowledge_id, ks.knowledge_points)
    def test_add_duplicate_kp(self):
        ks = KnowledgeStructure()
        kp = KnowledgePoint(title='test', content='test content')
        ks.add_knowledge_point(kp)
        result = ks.add_knowledge_point(kp)
        self.assertFalse(result)
        self.assertEqual(len(ks.knowledge_points), 1)
    def test_add_multiple_kps(self):
        ks = KnowledgeStructure()
        for i in range(5):
            kp = KnowledgePoint(title=f'kp{i}', content=f'content{i}')
            ks.add_knowledge_point(kp)
        self.assertEqual(len(ks.knowledge_points), 5)
class TestRelationshipValidation(unittest.TestCase):
    def test_add_relationship_nonexistent_source(self):
        ks = KnowledgeStructure()
        kp = KnowledgePoint(title='test', content='test')
        ks.add_knowledge_point(kp)
        rel = Relationship(source_id='nonexistent', target_id=kp.knowledge_id, relation_type=RelationType.RELATED_TO)
        result = ks.add_relationship(rel)
        self.assertFalse(result)
    def test_add_relationship_nonexistent_target(self):
        ks = KnowledgeStructure()
        kp = KnowledgePoint(title='test', content='test')
        ks.add_knowledge_point(kp)
        rel = Relationship(source_id=kp.knowledge_id, target_id='nonexistent', relation_type=RelationType.RELATED_TO)
        result = ks.add_relationship(rel)
        self.assertFalse(result)
    def test_add_relationship_self_reference(self):
        ks = KnowledgeStructure()
        kp = KnowledgePoint(title='test', content='test')
        ks.add_knowledge_point(kp)
        rel = Relationship(source_id=kp.knowledge_id, target_id=kp.knowledge_id, relation_type=RelationType.RELATED_TO)
        result = ks.add_relationship(rel)
        self.assertFalse(result)
    def test_add_relationship_invalid_type(self):
        ks = KnowledgeStructure()
        kp = KnowledgePoint(title='test', content='test')
        ks.add_knowledge_point(kp)
        rel = Relationship(source_id=kp.knowledge_id, target_id=kp.knowledge_id, relation_type='invalid_type')
        result = ks.add_relationship(rel)
        self.assertFalse(result)
class TestRelationshipDedup(unittest.TestCase):
    def test_related_to_dedup_symmetric(self):
        ks = KnowledgeStructure()
        kp1 = KnowledgePoint(title='kp1', content='c1')
        kp2 = KnowledgePoint(title='kp2', content='c2')
        ks.add_knowledge_point(kp1)
        ks.add_knowledge_point(kp2)
        rel1 = Relationship(source_id=kp1.knowledge_id, target_id=kp2.knowledge_id, relation_type=RelationType.RELATED_TO)
        rel2 = Relationship(source_id=kp2.knowledge_id, target_id=kp1.knowledge_id, relation_type=RelationType.RELATED_TO)
        self.assertTrue(ks.add_relationship(rel1))
        self.assertFalse(ks.add_relationship(rel2))
    def test_prerequisite_of_not_symmetric(self):
        ks = KnowledgeStructure()
        kp1 = KnowledgePoint(title='kp1', content='c1')
        kp2 = KnowledgePoint(title='kp2', content='c2')
        ks.add_knowledge_point(kp1)
        ks.add_knowledge_point(kp2)
        rel1 = Relationship(source_id=kp1.knowledge_id, target_id=kp2.knowledge_id, relation_type=RelationType.PRE_REQUSITE_OF)
        rel2 = Relationship(source_id=kp2.knowledge_id, target_id=kp1.knowledge_id, relation_type=RelationType.PRE_REQUSITE_OF)
        self.assertTrue(ks.add_relationship(rel1))
        self.assertTrue(ks.add_relationship(rel2))
    def test_duplicate_relationship_rejected(self):
        ks = KnowledgeStructure()
        kp1 = KnowledgePoint(title='kp1', content='c1')
        kp2 = KnowledgePoint(title='kp2', content='c2')
        ks.add_knowledge_point(kp1)
        ks.add_knowledge_point(kp2)
        rel1 = Relationship(source_id=kp1.knowledge_id, target_id=kp2.knowledge_id, relation_type=RelationType.RELATED_TO)
        rel2 = Relationship(source_id=kp1.knowledge_id, target_id=kp2.knowledge_id, relation_type=RelationType.RELATED_TO)
        self.assertTrue(ks.add_relationship(rel1))
        self.assertFalse(ks.add_relationship(rel2))
class TestEvidenceRefValidation(unittest.TestCase):
    def test_relationship_with_invalid_evidence_ref_rejected(self):
        ks = KnowledgeStructure()
        kp1 = KnowledgePoint(title='kp1', content='c1', evidence_refs=['ev1'])
        kp2 = KnowledgePoint(title='kp2', content='c2', evidence_refs=['ev2'])
        ks.add_knowledge_point(kp1)
        ks.add_knowledge_point(kp2)
        rel = Relationship(source_id=kp1.knowledge_id, target_id=kp2.knowledge_id, relation_type=RelationType.RELATED_TO, evidence_refs=['ev_nonexistent'])
        result = ks.add_relationship(rel)
        self.assertFalse(result)
    def test_relationship_with_valid_evidence_ref_accepted(self):
        ks = KnowledgeStructure()
        kp1 = KnowledgePoint(title='kp1', content='c1', evidence_refs=['ev1'])
        kp2 = KnowledgePoint(title='kp2', content='c2', evidence_refs=['ev2'])
        ks.add_knowledge_point(kp1)
        ks.add_knowledge_point(kp2)
        rel = Relationship(source_id=kp1.knowledge_id, target_id=kp2.knowledge_id, relation_type=RelationType.RELATED_TO, evidence_refs=['ev1'])
        result = ks.add_relationship(rel)
        self.assertTrue(result)
class TestSerialization(unittest.TestCase):
    def test_to_dict_and_from_dict_roundtrip(self):
        ks = KnowledgeStructure()
        kp1 = KnowledgePoint(title='kp1', content='c1')
        kp2 = KnowledgePoint(title='kp2', content='c2')
        ks.add_knowledge_point(kp1)
        ks.add_knowledge_point(kp2)
        rel = Relationship(source_id=kp1.knowledge_id, target_id=kp2.knowledge_id, relation_type=RelationType.RELATED_TO)
        ks.add_relationship(rel)
        d = ks.to_dict()
        ks2 = KnowledgeStructure.from_dict(d)
        self.assertEqual(len(ks2.knowledge_points), 2)
        self.assertEqual(len(ks2.relationships), 1)
    def test_from_dict_empty(self):
        ks = KnowledgeStructure.from_dict({'knowledge_points': [], 'relationships': []})
        self.assertEqual(len(ks.knowledge_points), 0)
        self.assertEqual(len(ks.relationships), 0)
class TestGetRelationships(unittest.TestCase):
    def test_get_relationships_filtered(self):
        ks = KnowledgeStructure()
        kp1 = KnowledgePoint(title='kp1', content='c1')
        kp2 = KnowledgePoint(title='kp2', content='c2')
        kp3 = KnowledgePoint(title='kp3', content='c3')
        ks.add_knowledge_point(kp1)
        ks.add_knowledge_point(kp2)
        ks.add_knowledge_point(kp3)
        rel1 = Relationship(source_id=kp1.knowledge_id, target_id=kp2.knowledge_id, relation_type=RelationType.RELATED_TO)
        rel2 = Relationship(source_id=kp2.knowledge_id, target_id=kp3.knowledge_id, relation_type=RelationType.RELATED_TO)
        ks.add_relationship(rel1)
        ks.add_relationship(rel2)
        rels_kp2 = ks.get_relationships(kp2.knowledge_id)
        self.assertEqual(len(rels_kp2), 2)
    def test_get_relationships_all(self):
        ks = KnowledgeStructure()
        kp1 = KnowledgePoint(title='kp1', content='c1')
        kp2 = KnowledgePoint(title='kp2', content='c2')
        ks.add_knowledge_point(kp1)
        ks.add_knowledge_point(kp2)
        rel = Relationship(source_id=kp1.knowledge_id, target_id=kp2.knowledge_id, relation_type=RelationType.RELATED_TO)
        ks.add_relationship(rel)
        self.assertEqual(len(ks.get_relationships()), 1)
class TestMultiLanguage(unittest.TestCase):
    def test_spanish_knowledge_point(self):
        ks = KnowledgeStructure()
        kp = KnowledgePoint(title='capital', content='La capital de Espaa', original_terms=['capital'], importance='high', confidence=Confidence.HIGH)
        ks.add_knowledge_point(kp)
        self.assertEqual(ks.get_knowledge_point(kp.knowledge_id).title, 'capital')
    def test_catalan_knowledge_point(self):
        ks = KnowledgeStructure()
        kp = KnowledgePoint(title='funci', content='Una funci', original_terms=['funci'], importance='medium', confidence=Confidence.MEDIUM)
        ks.add_knowledge_point(kp)
        self.assertIn('funci', ks.get_knowledge_point(kp.knowledge_id).title)
class TestDataIntegrity(unittest.TestCase):
    def test_relationship_to_dict_preserves_data(self):
        rel = Relationship(source_id='id1', target_id='id2', relation_type=RelationType.PRE_REQUSITE_OF, evidence_refs=['ev1'])
        d = rel.to_dict()
        self.assertEqual(d['source_id'], 'id1')
        self.assertEqual(d['target_id'], 'id2')
        self.assertEqual(d['relation_type'], 'prerequisite_of')
        self.assertEqual(d['evidence_refs'], ['ev1'])
    def test_relationship_from_dict_roundtrip(self):
        rel = Relationship(source_id='id1', target_id='id2', relation_type=RelationType.TOPIC, evidence_refs=['ev1'])
        d = rel.to_dict()
        rel2 = Relationship.from_dict(d)
        self.assertEqual(rel2.source_id, 'id1')
        self.assertEqual(rel2.target_id, 'id2')
        self.assertEqual(rel2.relation_type, RelationType.TOPIC)
    def test_knowledge_point_roundtrip(self):
        kp = KnowledgePoint(title='test', content='test content', evidence_refs=['ev1'])
        d = kp.to_dict()
        kp2 = KnowledgePoint.from_dict(d)
        self.assertEqual(kp2.title, 'test')
        self.assertEqual(kp2.content, 'test content')
    def test_relationship_id_auto_generated(self):
        rel = Relationship(source_id='id1', target_id='id2', relation_type=RelationType.RELATED_TO)
        self.assertIsNotNone(rel.relationship_id)
        self.assertTrue(uuid.UUID(rel.relationship_id) is not None)
class TestNoAutoInference(unittest.TestCase):
    def test_no_auto_inference_on_add(self):
        ks = KnowledgeStructure()
        kp1 = KnowledgePoint(title='kp1', content='c1')
        kp2 = KnowledgePoint(title='kp2', content='c2')
        ks.add_knowledge_point(kp1)
        ks.add_knowledge_point(kp2)
        self.assertEqual(len(ks.relationships), 0)
    def test_empty_evidence_refs_not_rejected(self):
        ks = KnowledgeStructure()
        kp1 = KnowledgePoint(title='kp1', content='c1', evidence_refs=[])
        kp2 = KnowledgePoint(title='kp2', content='c2', evidence_refs=[])
        ks.add_knowledge_point(kp1)
        ks.add_knowledge_point(kp2)
        rel = Relationship(source_id=kp1.knowledge_id, target_id=kp2.knowledge_id, relation_type=RelationType.RELATED_TO, evidence_refs=[])
        result = ks.add_relationship(rel)
        self.assertTrue(result)
if __name__ == '__main__':
    unittest.main()
