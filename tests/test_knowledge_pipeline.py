import unittest
import copy
import json
import os
import tempfile
from pathlib import Path

from src.integration import EvidenceIntegrator
from src.knowledge_pipeline import KnowledgeExtractor, KnowledgePipeline, PipelineResult
from src.knowledge_structure import KnowledgeStructure
from src.models import (
    ClassSession,
    Confidence,
    Course,
    Evidence,
    EvidenceType,
    Language,
    Material,
    MaterialType,
    SourceReference,
)


def _make_evidence(
    content: str,
    material_id: str,
    language: Language = Language.SPANISH,
    evidence_type: EvidenceType = EvidenceType.PERSONAL_NOTE,
    confidence: Confidence = Confidence.MEDIUM,
) -> Evidence:
    return Evidence(
        content=content,
        language=language,
        source_reference=SourceReference(material_id=material_id),
        confidence=confidence,
        evidence_type=evidence_type,
    )


class TestPipelineBasic(unittest.TestCase):
    def test_empty_evidence_returns_empty_structure(self):
        pipeline = KnowledgePipeline()
        result = pipeline.process_evidence([])
        self.assertIsInstance(result.structure, KnowledgeStructure)
        self.assertEqual(result.structure.knowledge_points, {})
        self.assertEqual(result.structure.relationships, [])
        self.assertEqual(result.structure.conflicts, [])
        self.assertEqual(result.evidence_ids, [])
        self.assertEqual(result.new_knowledge_point_ids, [])

    def test_single_evidence_creates_single_kp(self):
        pipeline = KnowledgePipeline()
        ev = _make_evidence("La fotosíntesis es un proceso.", "m1")
        result = pipeline.process_evidence([ev])
        self.assertEqual(len(result.structure.knowledge_points), 1)
        kp = next(iter(result.structure.knowledge_points.values()))
        self.assertEqual(kp.evidence_refs, [ev.evidence_id])
        self.assertEqual(kp.title, "La fotosíntesis es un proceso.")
        self.assertEqual(kp.confidence, Confidence.MEDIUM)

    def test_multiple_evidences_create_multiple_kps(self):
        pipeline = KnowledgePipeline()
        ev1 = _make_evidence("La fotosíntesis es un proceso.", "m1")
        ev2 = _make_evidence("El agua es necesaria para la vida.", "m1")
        result = pipeline.process_evidence([ev1, ev2])
        self.assertEqual(len(result.structure.knowledge_points), 2)

    def test_result_structure_is_returned(self):
        structure = KnowledgeStructure()
        pipeline = KnowledgePipeline()
        ev = _make_evidence("Prueba de agua.", "m1")
        result = pipeline.process_evidence([ev], structure=structure)
        self.assertIs(result.structure, structure)
        self.assertEqual(len(structure.knowledge_points), 1)


class TestPipelineIncremental(unittest.TestCase):
    def test_incremental_new_structure_start(self):
        pipeline = KnowledgePipeline()
        ev1 = _make_evidence("Primer concepto de la clase.", "m1")
        r1 = pipeline.process_evidence([ev1])
        self.assertEqual(len(r1.new_knowledge_point_ids), 1)
        self.assertEqual(len(r1.updated_knowledge_point_ids), 0)

    def test_incremental_add_new_evidence_to_existing_structure(self):
        pipeline = KnowledgePipeline()
        ev1 = _make_evidence("Primer concepto de la clase.", "m1")
        r1 = pipeline.process_evidence([ev1])
        structure = r1.structure

        ev2 = _make_evidence("Segundo concepto nuevo de la clase.", "m2")
        r2 = pipeline.process_evidence([ev2], structure=structure)
        self.assertEqual(len(r2.new_knowledge_point_ids), 1)
        self.assertEqual(len(structure.knowledge_points), 2)
        self.assertIn(r1.new_knowledge_point_ids[0], structure.knowledge_points)

    def test_incremental_preserves_existing_kps(self):
        pipeline = KnowledgePipeline()
        ev1 = _make_evidence("Concepto A importante.", "m1")
        r1 = pipeline.process_evidence([ev1])
        structure = r1.structure
        original_id = r1.new_knowledge_point_ids[0]
        original_kp = copy.deepcopy(structure.knowledge_points[original_id])

        ev2 = _make_evidence("Concepto B totalmente distinto.", "m2")
        r2 = pipeline.process_evidence([ev2], structure=structure)
        self.assertEqual(structure.knowledge_points[original_id].title, original_kp.title)
        self.assertEqual(structure.knowledge_points[original_id].content, original_kp.content)
        self.assertEqual(len(structure.knowledge_points), 2)

    def test_duplicate_evidence_does_not_create_duplicate_kp(self):
        pipeline = KnowledgePipeline()
        ev1 = _make_evidence("Concepto idéntico de clase.", "m1")
        r1 = pipeline.process_evidence([ev1])
        structure = r1.structure
        original_count = len(structure.knowledge_points)

        ev_dup = _make_evidence("Concepto idéntico de clase.", "m1")
        r2 = pipeline.process_evidence([ev_dup], structure=structure)
        self.assertEqual(len(structure.knowledge_points), original_count)
        self.assertEqual(len(r2.new_knowledge_point_ids), 0)
        self.assertGreater(len(r2.updated_knowledge_point_ids), 0)
        kp = structure.knowledge_points[r1.new_knowledge_point_ids[0]]
        self.assertIn(ev_dup.evidence_id, kp.evidence_refs)

    def test_idempotent_resubmit_no_relationship_duplicates(self):
        pipeline = KnowledgePipeline()
        base = "El agua es un compuesto químico necesario"
        ev1 = _make_evidence(base, "m1")
        ev2 = _make_evidence(base + " para todos los seres vivos.", "m2")
        r1 = pipeline.process_evidence([ev1, ev2])
        structure = r1.structure

        rel_count_before = len(structure.relationships)
        kp_count_before = len(structure.knowledge_points)
        conflict_count_before = len(structure.conflicts)

        ev3 = _make_evidence(base, "m1")
        ev4 = _make_evidence(base + " para todos los seres vivos.", "m2")
        r2 = pipeline.process_evidence([ev3, ev4], structure=structure)

        self.assertEqual(len(structure.relationships), rel_count_before)
        self.assertEqual(len(structure.knowledge_points), kp_count_before)
        self.assertEqual(len(structure.conflicts), conflict_count_before)

    def test_idempotent_resubmit_no_conflict_duplicates(self):
        pipeline = KnowledgePipeline()
        ev_a = _make_evidence("La fotosíntesis antes que la respiración celular.", "m2")
        ev_b = _make_evidence("La respiración celular antes que la fotosíntesis.", "m2")
        r1 = pipeline.process_evidence([ev_a, ev_b])
        structure = r1.structure
        conflict_count = len(structure.conflicts)
        self.assertGreater(conflict_count, 0)

        ev_c = _make_evidence("La fotosíntesis antes que la respiración celular.", "m2")
        ev_d = _make_evidence("La respiración celular antes que la fotosíntesis.", "m2")
        r2 = pipeline.process_evidence([ev_c, ev_d], structure=structure)
        self.assertEqual(len(structure.conflicts), conflict_count)


class TestPipelineConflict(unittest.TestCase):
    def test_conflict_records_registered_in_structure(self):
        pipeline = KnowledgePipeline()
        ev_a = _make_evidence("La fotosíntesis antes que la respiración celular.", "m1")
        ev_b = _make_evidence("La respiración celular antes que la fotosíntesis.", "m1")
        result = pipeline.process_evidence([ev_a, ev_b])
        self.assertGreater(len(result.structure.conflicts), 0)
        self.assertGreater(len(result.conflict_ids), 0)

    def test_conflict_sets_needs_verification(self):
        pipeline = KnowledgePipeline()
        ev_a = _make_evidence("La fotosíntesis antes que la respiración celular.", "m1")
        ev_b = _make_evidence("La respiración celular antes que la fotosíntesis.", "m1")
        result = pipeline.process_evidence([ev_a, ev_b])
        for kp in result.structure.knowledge_points.values():
            self.assertTrue(kp.needs_verification)


class TestPipelineRelationships(unittest.TestCase):
    def test_supporting_group_evidences_merged_into_one_kp(self):
        pipeline = KnowledgePipeline()
        base = "El agua es un compuesto químico necesario"
        ev_a = _make_evidence(base, "m1")
        ev_b = _make_evidence(base + " para todos los seres vivos.", "m1")
        result = pipeline.process_evidence([ev_a, ev_b])
        # Supporting group detected: both evidences land in one KP.
        self.assertEqual(len(result.structure.knowledge_points), 1)
        kp = next(iter(result.structure.knowledge_points.values()))
        self.assertIn(ev_a.evidence_id, kp.evidence_refs)
        self.assertIn(ev_b.evidence_id, kp.evidence_refs)

    def test_distinct_evidences_produce_distinct_kps(self):
        pipeline = KnowledgePipeline()
        ev_a = _make_evidence("Concepto completamente distinto A.", "m1")
        ev_b = _make_evidence("Concepto completamente distinto B.", "m1")
        result = pipeline.process_evidence([ev_a, ev_b])
        self.assertEqual(len(result.structure.knowledge_points), 2)

    def test_relationship_evidence_refs_valid(self):
        pipeline = KnowledgePipeline()
        base = "El agua es un compuesto químico necesario"
        ev_a = _make_evidence(base, "m1")
        ev_b = _make_evidence(base + " para todos los seres vivos.", "m1")
        result = pipeline.process_evidence([ev_a, ev_b])
        for rel in result.structure.relationships:
            valid_refs = set()
            for kp in result.structure.knowledge_points.values():
                valid_refs.update(kp.evidence_refs)
            for ref in rel.evidence_refs:
                self.assertIn(ref, valid_refs)


class TestPipelineTraceability(unittest.TestCase):
    def test_kp_evidence_refs_present(self):
        pipeline = KnowledgePipeline()
        ev = _make_evidence("Concepto con trazabilidad.", "material-x")
        result = pipeline.process_evidence([ev])
        kp = next(iter(result.structure.knowledge_points.values()))
        self.assertEqual(kp.evidence_refs, [ev.evidence_id])

    def test_evidence_refs_point_to_material(self):
        pipeline = KnowledgePipeline()
        ev = _make_evidence("Concepto con trazabilidad.", "material-x")
        result = pipeline.process_evidence([ev])
        kp = next(iter(result.structure.knowledge_points.values()))
        self.assertEqual(kp.evidence_refs, [ev.evidence_id])
        self.assertEqual(ev.source_reference.material_id, "material-x")

    def test_session_processor_evidence_flow(self):
        pipeline = KnowledgePipeline()
        course = Course(name="Biología", code="BIO2026")
        session = ClassSession(course_id=course.course_id, session_number=1, date="2026-01-01", title="Sesión 1")
        with tempfile.TemporaryDirectory() as tmpdir:
            note_path = os.path.join(tmpdir, "notes.txt")
            Path(note_path).write_text(
                "La fotosíntesis es un proceso vital.\n\nEl agua es necesaria para la vida.",
                encoding="utf-8",
            )
            material = Material(
                filename="notes.txt",
                path=note_path,
                material_type=MaterialType.NOTE,
                language=Language.SPANISH,
            )
            result = pipeline.process_session(session, [material])
            self.assertGreater(len(result.structure.knowledge_points), 0)
            for kp in result.structure.knowledge_points.values():
                self.assertGreater(len(kp.evidence_refs), 0)


class TestPipelineMultilingual(unittest.TestCase):
    def test_spanish_text_preserved(self):
        pipeline = KnowledgePipeline()
        ev = _make_evidence("La fotosíntesis ocurre en los cloroplastos.", "m1", Language.SPANISH)
        result = pipeline.process_evidence([ev])
        kp = next(iter(result.structure.knowledge_points.values()))
        self.assertEqual(kp.content, "La fotosíntesis ocurre en los cloroplastos.")

    def test_catalan_text_preserved(self):
        pipeline = KnowledgePipeline()
        ev = _make_evidence("La fotosíntesi és un procés vital.", "m1", Language.CATALAN)
        result = pipeline.process_evidence([ev])
        kp = next(iter(result.structure.knowledge_points.values()))
        self.assertEqual(kp.content, "La fotosíntesi és un procés vital.")
        self.assertIn("fotosíntesi", kp.content)

    def test_chinese_text_preserved(self):
        pipeline = KnowledgePipeline()
        ev = _make_evidence("光合作用是一个重要的生物过程。", "m1", Language.CHINESE)
        result = pipeline.process_evidence([ev])
        kp = next(iter(result.structure.knowledge_points.values()))
        self.assertEqual(kp.content, "光合作用是一个重要的生物过程。")

    def test_no_unrequested_translation_occurs(self):
        pipeline = KnowledgePipeline()
        ev = _make_evidence("El agua es un compuesto.", "m1", Language.SPANISH)
        result = pipeline.process_evidence([ev])
        kp = next(iter(result.structure.knowledge_points.values()))
        self.assertIn("El agua es un compuesto.", kp.content)
        self.assertIn("El agua es un compuesto.", kp.original_terms)


class TestPipelineEvidenceIntegrity(unittest.TestCase):
    def test_evidence_content_not_mutated(self):
        pipeline = KnowledgePipeline()
        ev = _make_evidence("Contenido original.", "m1")
        original_content = ev.content
        original_conf = ev.confidence
        original_type = ev.evidence_type
        pipeline.process_evidence([ev])
        self.assertEqual(ev.content, original_content)
        self.assertEqual(ev.confidence, original_conf)
        self.assertEqual(ev.evidence_type, original_type)

    def test_evidence_metadata_not_mutated(self):
        pipeline = KnowledgePipeline()
        ev = _make_evidence("Contenido.", "m1")
        ev.metadata["custom"] = "value"
        pipeline.process_evidence([ev])
        self.assertEqual(ev.metadata.get("custom"), "value")


class TestPipelineSerialization(unittest.TestCase):
    def test_structure_round_trip_preserves_kps(self):
        pipeline = KnowledgePipeline()
        ev1 = _make_evidence("Concepto A.", "m1")
        ev2 = _make_evidence("Concepto B distinto.", "m1")
        result = pipeline.process_evidence([ev1, ev2])
        structure = result.structure

        serialized = structure.to_dict()
        json_str = json.dumps(serialized, ensure_ascii=False)
        deserialized = KnowledgeStructure.from_dict(json.loads(json_str))

        self.assertEqual(
            set(deserialized.knowledge_points.keys()),
            set(structure.knowledge_points.keys()),
        )
        for kp_id, kp in structure.knowledge_points.items():
            self.assertEqual(deserialized.knowledge_points[kp_id].title, kp.title)
            self.assertEqual(deserialized.knowledge_points[kp_id].content, kp.content)
            self.assertEqual(
                deserialized.knowledge_points[kp_id].evidence_refs,
                kp.evidence_refs,
            )

    def test_structure_round_trip_preserves_conflicts(self):
        pipeline = KnowledgePipeline()
        ev_a = _make_evidence("La fotosíntesis antes que la respiración celular.", "m1")
        ev_b = _make_evidence("La respiración celular antes que la fotosíntesis.", "m1")
        result = pipeline.process_evidence([ev_a, ev_b])
        structure = result.structure
        self.assertGreater(len(structure.conflicts), 0)

        serialized = structure.to_dict()
        json_str = json.dumps(serialized, ensure_ascii=False)
        deserialized = KnowledgeStructure.from_dict(json.loads(json_str))
        self.assertEqual(len(deserialized.conflicts), len(structure.conflicts))

    def test_serialization_stable_order(self):
        pipeline = KnowledgePipeline()
        ev1 = _make_evidence("Concepto 1.", "m1")
        ev2 = _make_evidence("Concepto 2.", "m1")
        r1 = pipeline.process_evidence([ev1, ev2])
        r2 = KnowledgePipeline().process_evidence([_make_evidence("Concepto 1.", "m1"), _make_evidence("Concepto 2.", "m1")])
        self.assertEqual(
            [kp.knowledge_id for kp in r1.structure.knowledge_points.values()],
            [kp.knowledge_id for kp in r2.structure.knowledge_points.values()],
        )


class TestKnowledgeExtractorStandalone(unittest.TestCase):
    def test_extract_without_integration(self):
        ev1 = _make_evidence("Concepto 1.", "m1")
        ev2 = _make_evidence("Concepto 2.", "m1")
        points = KnowledgeExtractor.extract([ev1, ev2])
        self.assertEqual(len(points), 2)

    def test_extract_deterministic(self):
        ev1 = _make_evidence("Concepto fijo.", "m1")
        p1 = KnowledgeExtractor.extract([ev1])
        p2 = KnowledgeExtractor.extract([_make_evidence("Concepto fijo.", "m1")])
        self.assertEqual(p1[0].knowledge_id, p2[0].knowledge_id)

    def test_kp_id_is_stable(self):
        ev = _make_evidence("Estable concepto.", "m1")
        points = KnowledgeExtractor.extract([ev])
        self.assertTrue(points[0].knowledge_id.startswith("kp-"))
        self.assertEqual(len(points[0].knowledge_id), 19)


class TestPipelineArchitectureGuards(unittest.TestCase):
    def test_pipeline_imports_only_stdlib_and_project(self):
        import inspect
        import src.knowledge_pipeline as kp_module
        source = inspect.getsource(kp_module)
        import re as _re
        import_statements = _re.findall(r"^import\s+(\S+)|^from\s+(\S+)\s+import", source, _re.MULTILINE)
        top_level_modules = set()
        for std, proj in import_statements:
            name = std or proj
            top_level_modules.add(name.split(".")[0])
        allowed = {"__future__", "hashlib", "dataclasses", "typing", "src"}
        for mod in top_level_modules:
            self.assertIn(
                mod,
                allowed,
                "Unexpected top-level import in knowledge_pipeline: " + str(mod),
            )

    def test_pipeline_does_not_use_network_or_db(self):
        import inspect
        import src.knowledge_pipeline as kp_module
        source = inspect.getsource(kp_module)
        for forbidden in ("requests", "urllib", "http.client", "socket.", "sqlite3", "psycopg", "pymongo"):
            self.assertNotIn(forbidden, source, "Forbidden reference in knowledge_pipeline: " + forbidden)

    def test_pipeline_does_not_use_llm(self):
        import inspect
        import src.knowledge_pipeline as kp_module
        source = inspect.getsource(kp_module).lower()
        for forbidden in ("openai", "anthropic", "llm", "gpt", "embedding", "vector", "transformers"):
            self.assertNotIn(forbidden, source, "Forbidden LLM reference in knowledge_pipeline: " + forbidden)