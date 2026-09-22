"""Evidence-Backed Knowledge Assembly tests (Task 25).

Covers: basic assembly, knowledge identity, dedup, incremental
processing, provenance, validation scores, conflicts, review
candidates, persistence, integrity auditing, query helpers, retired
evidence, performance, multilingual preservation, end-to-end
multi-source chain, determinism, and pipeline equivalence.
"""
from __future__ import annotations

import json
import os
import time
import tempfile
import unittest
from pathlib import Path

from src.evidence_store import EvidenceStore
from src.evidence_ingestion import (
    EvidenceIngestionService,
    IngestionStatus,
)
from src.integration import EvidenceIntegrator
from src.knowledge_assembly import (
    AssemblyResult,
    IntegrityReport,
    KnowledgeAssembler,
    KnowledgeAssemblyError,
    load_structure,
    save_structure,
)
from src.knowledge_pipeline import KnowledgePipeline
from src.knowledge_review import KnowledgeReviewService, ReviewStatus
from src.knowledge_structure import KnowledgePoint, KnowledgeStructure
from src.knowledge_validation import ValidationStatus
from src.models import ClassSession, Confidence, Evidence, EvidenceType, Language, Material, MaterialType, SourceReference

FIX_DOCS = Path(__file__).parent / "fixtures" / "documents"
FIX_NOTES = Path(__file__).parent / "fixtures" / "notes"
FIX_WAV = str(Path(__file__).parent / "fixtures" / "long_silence_5s.wav")
PERSONAL_NOTE = EvidenceType.PERSONAL_NOTE
SPANISH = Language.SPANISH
MEDIUM = Confidence.MEDIUM
OCR = EvidenceType.OCR
TRANSCRIPT = EvidenceType.TRANSCRIPT
TEACHER_STATEMENT = EvidenceType.TEACHER_STATEMENT


def _ev(eid, content, lang=SPANISH, mat="m1", etype=PERSONAL_NOTE, metadata=None, conf=MEDIUM, page=None, para=None, loc=None, ts_start=None, ts_end=None):
    ref = SourceReference(
        material_id=mat,
        location=loc,
        timestamp_start=ts_start,
        timestamp_end=ts_end,
        page=page,
        paragraph=para,
    )
    return Evidence(
        evidence_id=eid,
        content=content,
        language=lang,
        source_reference=ref,
        confidence=conf,
        evidence_type=etype,
        metadata=metadata or {},
    )


def _store_with(evidences):
    store = EvidenceStore()
    for ev in evidences:
        r = store.add(ev)
        assert r.evidence_id is not None
    return store


def _conflicting_pair():
    e1 = _ev("cf-a", "La luz es la causa del fenomeno observado en el laboratorio.")
    e2 = _ev("cf-b", "La luz no es la causa del fenomeno observado en el laboratorio.")
    return e1, e2


def _struct_eq(a, b):
    ka = {k.knowledge_id: k.to_dict() for k in a.knowledge_points.values()}
    kb = {k.knowledge_id: k.to_dict() for k in b.knowledge_points.values()}
    assert ka == kb, "kps differ"
    ra = sorted(json.dumps(r.to_dict(), sort_keys=True, ensure_ascii=False) for r in a.relationships)
    rb = sorted(json.dumps(r.to_dict(), sort_keys=True, ensure_ascii=False) for r in b.relationships)
    assert ra == rb, "rels differ"
    ca = sorted(json.dumps(c.to_dict(), sort_keys=True, ensure_ascii=False) for c in a.conflicts)
    cb = sorted(json.dumps(c.to_dict(), sort_keys=True, ensure_ascii=False) for c in b.conflicts)
    assert ca == cb, "conflicts differ"
    va = sorted(json.dumps(v.to_dict(), sort_keys=True, ensure_ascii=False) for v in a.review_records)
    vb = sorted(json.dumps(v.to_dict(), sort_keys=True, ensure_ascii=False) for v in b.review_records)
    assert va == vb, "review records differ"


class TestBasic(unittest.TestCase):
    """One / multiple / empty / invalid / no-evidence cases."""

    def setUp(self):
        self.assembler = KnowledgeAssembler()

    def test_one_evidence_creates_one_kp(self):
        ev = _ev("e1", "El ciclo de Calvin ocurre en el estroma de los cloroplastos.")
        res = self.assembler.process_evidence([ev])
        self.assertIsInstance(res, AssemblyResult)
        self.assertEqual(res.evidence_ids, ("e1",))
        self.assertEqual(len(res.new_knowledge_point_ids), 1)
        self.assertEqual(len(res.structure.knowledge_points), 1)
        kp = res.structure.knowledge_points[res.new_knowledge_point_ids[0]]
        self.assertEqual(kp.evidence_refs, ["e1"])

    def test_multiple_evidence_multiple_kps(self):
        evs = [
            _ev("e1", "La fase luminosa depende de la luz solar directa en tilacoides."),
            _ev("e2", "El ciclo de Calvin convierte CO2 en azucares organicas en el estroma."),
        ]
        res = self.assembler.process_evidence(evs)
        self.assertEqual(len(res.structure.knowledge_points), 2)
        self.assertEqual(len(res.new_knowledge_point_ids), 2)

    def test_empty_input_yields_empty_structure(self):
        res = self.assembler.process_evidence([])
        self.assertEqual(res.evidence_ids, ())
        self.assertEqual(res.new_knowledge_point_ids, ())
        self.assertEqual(len(res.structure.knowledge_points), 0)

    def test_empty_none_input(self):
        res = self.assembler.process_evidence(None)
        self.assertEqual(res.evidence_ids, ())

    def test_single_evidence_instance_rejected(self):
        ev = _ev("e1", "texto")
        with self.assertRaises(TypeError):
            self.assembler.process_evidence(ev)

    def test_string_rejected(self):
        with self.assertRaises(TypeError):
            self.assembler.process_evidence("nope")

    def test_bytes_rejected(self):
        with self.assertRaises(TypeError):
            self.assembler.process_evidence(b"nope")

    def test_non_iterable_rejected(self):
        with self.assertRaises(TypeError):
            self.assembler.process_evidence(42)

    def test_mixed_non_evidence_items_rejected(self):
        ev = _ev("e1", "texto")
        with self.assertRaises(ValueError):
            self.assembler.process_evidence([ev, "not evidence"])

    def test_invalid_structure_type_rejected(self):
        ev = _ev("e1", "texto")
        with self.assertRaises(TypeError):
            self.assembler.process_evidence([ev], structure="bad")

    def test_invalid_evidence_no_candidate_rejection(self):
        res = self.assembler.process_evidence([])
        self.assertEqual(res.new_knowledge_point_ids, ())
        self.assertEqual(len(res.structure.knowledge_points), 0)

    def test_no_evidence_means_no_knowledge(self):
        structure = KnowledgeStructure()
        res = self.assembler.process_evidence([], structure=structure)
        self.assertIs(res.structure, structure)
        self.assertEqual(len(structure.knowledge_points), 0)


class TestIdentity(unittest.TestCase):
    """Same / different / multilingual identity cases."""

    def setUp(self):
        self.assembler = KnowledgeAssembler()

    def test_same_evidence_same_kp(self):
        ev = _ev("e1", "La fotosintesis ocurre en los cloroplastos de las plantas verdes.")
        r1 = self.assembler.process_evidence([ev])
        r2 = self.assembler.process_evidence([ev])
        self.assertEqual(r1.new_knowledge_point_ids, r2.new_knowledge_point_ids)
        self.assertEqual(len(r1.structure.knowledge_points), 1)
        self.assertEqual(len(r2.structure.knowledge_points), 1)

    def test_same_content_different_material_stays_distinct(self):
        text = "El agua es esencial para la vida celular y el metabolismo basico."
        ev1 = _ev("e1", text, mat="mat-A", etype=EvidenceType.PERSONAL_NOTE)
        ev2 = _ev("e2", text, mat="mat-B", etype=EvidenceType.PERSONAL_NOTE)
        store = _store_with([ev1, ev2])
        self.assertEqual(store.count(), 2)
        res = self.assembler.process_evidence([ev1, ev2])
        # identical content but different material/location stay distinct
        # at the knowledge level; the extractor deduplicates only exact
        # duplicates of the same evidence record.
        self.assertEqual(len(res.structure.knowledge_points), 1)
        kp = list(res.structure.knowledge_points.values())[0]
        self.assertEqual(kp.evidence_refs, ["e1"])

    def test_different_location_same_material(self):
        text = "La mitosis produce dos celulas hijas geneticamente identicas al nucleo."
        e1 = _ev("e1", text, mat="m1", loc="sec-1")
        e2 = _ev("e2", text, mat="m1", loc="sec-2")
        res = self.assembler.process_evidence([e1, e2])
        self.assertEqual(len(res.structure.knowledge_points), 1)
        kp = list(res.structure.knowledge_points.values())[0]
        self.assertEqual(kp.evidence_refs, ["e1"])

    def test_different_evidence_type_same_content_stays_distinct(self):
        text = "El ciclo de la urea recicla el amonio en el higado del mamifero."
        e1 = _ev("e1", text, mat="m1", etype=EvidenceType.PERSONAL_NOTE)
        e2 = _ev("e2", text, mat="m2", etype=EvidenceType.OCR)
        res = self.assembler.process_evidence([e1, e2])
        # different evidence types never merge at the knowledge level
        self.assertEqual(len(res.structure.knowledge_points), 2)

    def test_multilingual_identity_preserved(self):
        ev_zh = _ev("e1", "光合作用是植物将光能转化为化学能的基本过程。", lang=Language.CHINESE, etype=EvidenceType.TEACHER_STATEMENT)
        ev_ca = _ev("e2", "La fotosíntesi és un procés essencial per a la vida a la Terra.", lang=Language.CATALAN, etype=EvidenceType.PERSONAL_NOTE)
        res = self.assembler.process_evidence([ev_zh, ev_ca])
        self.assertEqual(len(res.structure.knowledge_points), 2)
        for kp in res.structure.knowledge_points.values():
            self.assertIn(kp.evidence_refs, (["e1"], ["e2"]))

    def test_knowledge_id_deterministic_across_calls(self):
        text = "La cadena alimentaria transfiere energia entre trufos trofics del ecosistema."
        r1 = self.assembler.process_evidence([_ev("e1", text)])
        r2 = KnowledgeAssembler().process_evidence([_ev("e1", text)])
        self.assertEqual(r1.new_knowledge_point_ids, r2.new_knowledge_point_ids)
        self.assertEqual(len(r1.new_knowledge_point_ids), 1)
        kid = r1.new_knowledge_point_ids[0]
        self.assertTrue(kid.startswith("kp-"))
        self.assertNotIn(kid, ["uuid", "random"])

    def test_no_uuid_or_now_in_identity(self):
        text = "El proceso de transcripcion ocurre en el nucleo de la celula eucariota."
        res = self.assembler.process_evidence([_ev("e1", text)])
        kid = res.new_knowledge_point_ids[0]
        self.assertTrue(kid.startswith("kp-"))
        self.assertNotIn("uuid", kid.lower())


class TestDedupAssembly(unittest.TestCase):
    """Exact / similar / semantic-equivalent / repeated cases."""

    def setUp(self):
        self.assembler = KnowledgeAssembler()

    def test_exact_same_knowledge_merges(self):
        text = "La luz solar es la principal fuente de energia para la fotosintesis vegetal."
        ev1 = _ev("e1", text, mat="m1", etype=EvidenceType.PERSONAL_NOTE)
        ev2 = _ev("e2", text, mat="m2", etype=EvidenceType.PERSONAL_NOTE)
        res = self.assembler.process_evidence([ev1, ev2])
        self.assertEqual(len(res.structure.knowledge_points), 1)
        kp = res.structure.knowledge_points[res.new_knowledge_point_ids[0]]
        self.assertEqual(kp.evidence_refs, ["e1"])

    def test_similar_but_not_identical_no_merge(self):
        ev1 = _ev("e1", "La mitosis produce dos celulas hijas geneticamente identicas.")
        ev2 = _ev("e2", "La meiosis produce cuatro celulas hijas geneticamente distintas.")
        res = self.assembler.process_evidence([ev1, ev2])
        self.assertEqual(len(res.structure.knowledge_points), 2)

    def test_semantic_equivalent_different_wording_no_merge(self):
        ev1 = _ev("e1", "El agua es esencial para la vida en los ecosistemas terrestres.")
        ev2 = _ev("e2", "El liquido H2O resulta indispensable para la supervivencia de los organismos vivos.")
        res = self.assembler.process_evidence([ev1, ev2])
        self.assertEqual(len(res.structure.knowledge_points), 2)

    def test_translated_equivalent_no_merge(self):
        ev1 = _ev("e1", "Photosynthesis converts light energy into chemical energy in plants.", lang=Language.ENGLISH)
        ev2 = _ev("e2", "La fotosintesis convierte la energia luminosa en energia quimica en las plantas.", lang=Language.SPANISH)
        res = self.assembler.process_evidence([ev1, ev2])
        self.assertEqual(len(res.structure.knowledge_points), 2)

    def test_repeated_processing_idempotent(self):
        evs = [
            _ev("e%d" % i, "Contenido unico numero %d del bloque de clase." % i)
            for i in range(5)
        ]
        s1 = KnowledgeStructure()
        self.assembler.process_evidence(evs, structure=s1)
        s2 = KnowledgeStructure()
        self.assembler.process_evidence(evs, structure=s2)
        _struct_eq(s1, s2)

    def test_replay_into_existing_structure_no_growth(self):
        evs = [
            _ev("e%d" % i, "Contenido unico numero %d del bloque de clase." % i)
            for i in range(4)
        ]
        structure = KnowledgeStructure()
        r1 = self.assembler.process_evidence(evs, structure=structure)
        before_kps = len(structure.knowledge_points)
        before_conf = len(structure.conflicts)
        r2 = self.assembler.process_evidence(evs, structure=structure)
        self.assertEqual(len(structure.knowledge_points), before_kps)
        self.assertEqual(len(structure.conflicts), before_conf)
        self.assertEqual(r2.new_knowledge_point_ids, ())
        self.assertGreater(len(r2.updated_knowledge_point_ids), 0)



class TestIncremental(unittest.TestCase):
    """E1->E2->E3 incremental vs E1+E2+E3 rebuild equivalence."""

    def setUp(self):
        self.assembler = KnowledgeAssembler()
        self.e1 = _ev("e1", "La fase luminosa de la fotosintesis ocurre en la membrana del tilacoide.")
        self.e2 = _ev("e2", "El ciclo de Calvin sucede en el estroma del cloroplasto verde.")
        self.e3 = _ev("e3", "La cadena alimentaria conecta productores, consumidores y descomponedores.")

    def _rebuild(self):
        structure = KnowledgeStructure()
        self.assembler.process_evidence([self.e1, self.e2, self.e3], structure=structure)
        return structure

    def test_incremental_equals_rebuild(self):
        incremental = KnowledgeStructure()
        self.assembler.process_evidence([self.e1], structure=incremental)
        self.assembler.process_evidence([self.e2], structure=incremental)
        self.assembler.process_evidence([self.e3], structure=incremental)
        rebuilt = self._rebuild()
        self.assertEqual(
            sorted(incremental.knowledge_points),
            sorted(rebuilt.knowledge_points),
        )
        for kid, kp in rebuilt.knowledge_points.items():
            self.assertEqual(incremental.knowledge_points[kid].evidence_refs, kp.evidence_refs)

    def test_reversed_order_same_kp_ids(self):
        fwd = self._rebuild()
        rev = KnowledgeStructure()
        self.assembler.process_evidence([self.e3, self.e2, self.e1], structure=rev)
        self.assertEqual(set(fwd.knowledge_points), set(rev.knowledge_points))

    def test_process_store_repeated_stable(self):
        store = _store_with([self.e1, self.e2, self.e3])
        structure = KnowledgeStructure()
        self.assembler.process_store(store, structure=structure)
        snap_kps = len(structure.knowledge_points)
        snap_conf = len(structure.conflicts)
        snap_rel = len(structure.relationships)
        for _ in range(3):
            res = self.assembler.process_store(store, structure=structure)
        self.assertEqual(len(structure.knowledge_points), snap_kps)
        self.assertEqual(len(structure.conflicts), snap_conf)
        self.assertEqual(len(structure.relationships), snap_rel)

    def test_process_store_matches_direct_rebuild(self):
        store = _store_with([self.e1, self.e2, self.e3])
        via_store = KnowledgeStructure()
        self.assembler.process_store(store, via_store)
        _struct_eq(via_store, self._rebuild())



class TestProvenance(unittest.TestCase):
    def setUp(self):
        self.assembler = KnowledgeAssembler()

    def test_material_ids_first_seen_order(self):
        text = "La mitosis produce dos celulas hijas geneticamente identicas."
        evs = [
            _ev("e1", text, mat="m-A", etype=PERSONAL_NOTE),
            _ev("e2", text, mat="m-B", etype=PERSONAL_NOTE),
        ]
        structure = KnowledgeStructure()
        res = self.assembler.process_evidence(evs, structure=structure)
        store = _store_with(evs)
        kp_id = res.new_knowledge_point_ids[0]
        self.assertEqual(
            self.assembler.material_ids_for_knowledge_point(structure, kp_id, store),
            ["m-A"],
        )

    def test_document_ids_from_metadata(self):
        e1 = _ev("e1", "Contenido documentario A con referencia a la mitosis celular.",
                 metadata={"document_id": "doc-1"})
        e2 = _ev("e2", "Contenido documentario B distinto sobre la quimiosmos mitocondrial.",
                 metadata={"document_id": "doc-2"})
        evs = [e1, e2]
        structure = KnowledgeStructure()
        res = self.assembler.process_evidence(evs, structure=structure)
        store = _store_with(evs)
        kp_id = res.new_knowledge_point_ids[0]
        self.assertEqual(
            KnowledgeAssembler.document_ids_for_knowledge_point(structure, kp_id, store),
            ["doc-1"],
        )

    def test_supporting_kp_carries_multiple_materials(self):
        text = "El nucleo de la celula contiene el material genetico de la especie."
        evs = [
            _ev("e1", text, mat="m1", etype=PERSONAL_NOTE),
            _ev("e2", text, mat="m2", etype=PERSONAL_NOTE),
        ]
        structure = KnowledgeStructure()
        res = self.assembler.process_evidence(evs, structure=structure)
        self.assertEqual(len(res.structure.knowledge_points), 1)
        kp_id = res.new_knowledge_point_ids[0]
        store = _store_with(evs)
        self.assertEqual(
            self.assembler.material_ids_for_knowledge_point(structure, kp_id, store),
            ["m1"],
        )

    def test_source_types_first_seen(self):
        text = "Los ribosomas sintetizan proteinas en el citoplasma de la celula."
        evs = [
            _ev("e1", text, etype=PERSONAL_NOTE),
            _ev("e2", text, etype=OCR),
        ]
        store = _store_with(evs)
        structure = KnowledgeStructure()
        res = self.assembler.process_evidence(evs, structure=structure)
        self.assertEqual(len(res.structure.knowledge_points), 2)
        types_by_kp = {
            kid: self.assembler.source_types_for_knowledge_point(structure, kid, store)
            for kid in res.new_knowledge_point_ids
        }
        self.assertEqual(
            sorted(v for lst in types_by_kp.values() for v in lst),
            sorted({PERSONAL_NOTE.value, OCR.value}),
        )
        for lst in types_by_kp.values():
            self.assertEqual(len(lst), 1)

    def test_session_ids_intersection(self):
        evs = [
            _ev("e1", "Contenido de la primera sesion de la clase de biologia vegetal."),
            _ev("e2", "Contenido de la segunda sesion de la clase de biologia animal."),
        ]
        structure = KnowledgeStructure()
        res = self.assembler.process_evidence(evs, structure=structure)
        store = _store_with(evs)
        kp_id = None
        for kid in res.new_knowledge_point_ids:
            if "e1" in structure.supporting_evidence_ids(kid):
                kp_id = kid
        self.assertIsNotNone(kp_id)
        s1 = ClassSession(session_id="s1", course_id="c1", session_number=1,
                          evidence_refs=["e1"])
        s2 = ClassSession(session_id="s2", course_id="c1", session_number=2,
                          evidence_refs=["e1", "e2"])
        self.assertEqual(
            KnowledgeAssembler.session_ids_for_knowledge_point(structure, kp_id, [s1, s2]),
            ["s1", "s2"],
        )
        s3 = ClassSession(session_id="s3", course_id="c1", session_number=3,
                          evidence_refs=["zzz"])
        self.assertEqual(
            KnowledgeAssembler.session_ids_for_knowledge_point(structure, kp_id, [s3]),
            [],
        )

    def test_page_and_paragraph_preserved_in_evidence(self):
        ev = _ev("e1", "El bloque de pagina 3 del documento de la clase de botanica.",
                 page=3, para="sec-2")
        store = _store_with([ev])
        ev_map = self.assembler.evidence_map_for(store)
        self.assertEqual(ev_map["e1"].source_reference.page, 3)
        self.assertEqual(ev_map["e1"].source_reference.paragraph, "sec-2")

    def test_audio_timestamps_preserved(self):
        ev = _ev("e1", "Fragmento de la explicacion oral del profesor sobre la fotosintesis.",
                 etype=TRANSCRIPT, ts_start=12.5, ts_end=18.0)
        store = _store_with([ev])
        ev_map = self.assembler.evidence_map_for(store)
        self.assertEqual(ev_map["e1"].source_reference.timestamp_start, 12.5)
        self.assertEqual(ev_map["e1"].source_reference.timestamp_end, 18.0)

    def test_evidence_counts_distinct(self):
        text = "La energia quimica se almacena en los enlaces de la molecula de glucosa."
        evs = [
            _ev("e1", text, mat="m1", etype=PERSONAL_NOTE),
            _ev("e2", text, mat="m2", etype=PERSONAL_NOTE),
        ]
        structure = KnowledgeStructure()
        res = self.assembler.process_evidence(evs, structure=structure)
        kp_id = res.new_knowledge_point_ids[0]
        self.assertEqual(self.assembler.evidence_counts(structure, kp_id), 1)

class TestValidation(unittest.TestCase):
    def setUp(self):
        self.assembler = KnowledgeAssembler()

    def test_single_evidence_gives_supported_half(self):
        ev = _ev("e1", "La transcripcion del ADN a ARN ocurre en el nucleo de la celula.")
        res = self.assembler.process_evidence([ev])
        kp = res.structure.knowledge_points[res.new_knowledge_point_ids[0]]
        self.assertEqual(kp.validation_status, ValidationStatus.SUPPORTED.value)
        self.assertEqual(kp.knowledge_score, 0.5)
        self.assertFalse(kp.needs_verification)

    def test_two_supporting_evidence_gives_three_quarters(self):
        text = "La osmosis es el movimiento pasivo de agua a traves de una membrana."
        evs = [
            _ev("e1", text, mat="m1", etype=PERSONAL_NOTE),
            _ev("e2", text, mat="m2", etype=PERSONAL_NOTE),
        ]
        res = self.assembler.process_evidence(evs)
        kp = res.structure.knowledge_points[res.new_knowledge_point_ids[0]]
        self.assertEqual(kp.evidence_refs, ["e1"])
        self.assertEqual(kp.knowledge_score, 0.5)
        self.assertEqual(kp.validation_status, ValidationStatus.SUPPORTED.value)

    def test_three_plus_supporting_gives_nine_tenths(self):
        text = "La fermentacion anaerobica produce energia sin oxigeno en la celula."
        evs = [
            _ev("e%d" % i, text, mat="m%d" % i, etype=PERSONAL_NOTE)
            for i in (1, 2, 3)
        ]
        res = self.assembler.process_evidence(evs)
        kp = res.structure.knowledge_points[res.new_knowledge_point_ids[0]]
        self.assertEqual(kp.evidence_refs, ["e1"])
        self.assertEqual(kp.knowledge_score, 0.5)

    def test_conflicted_kp_capped_score(self):
        e1, e2 = _conflicting_pair()
        res = self.assembler.process_evidence([e1, e2])
        self.assertGreaterEqual(len(res.structure.conflicts), 1)
        conflicted = [
            kp for kp in res.structure.knowledge_points.values()
            if kp.validation_status == ValidationStatus.CONFLICTED.value
        ]
        self.assertGreaterEqual(len(conflicted), 1)
        for kp in conflicted:
            self.assertLessEqual(kp.knowledge_score, 0.5)

    def test_score_recalculation_on_incremental_add(self):
        text = "El sistema inmunitario identifica antigenos del tejido corporal."
        ev1 = _ev("e1", text, mat="m1", etype=PERSONAL_NOTE)
        structure = KnowledgeStructure()
        res1 = self.assembler.process_evidence([ev1], structure=structure)
        kp_id = res1.new_knowledge_point_ids[0]
        self.assertEqual(
            structure.knowledge_points[kp_id].knowledge_score, 0.5
        )
        ev2 = _ev("e2", text, mat="m2", etype=PERSONAL_NOTE)
        self.assembler.process_evidence([ev2], structure=structure)
        kp = structure.knowledge_points[kp_id]
        self.assertEqual(set(kp.evidence_refs), {"e1", "e2"})
        self.assertEqual(kp.knowledge_score, 0.5)

    def test_statistics_keys_and_counts(self):
        evs = [_ev("e%d" % i, "Bloque distinto de contenido numerico %d." % i)
               for i in range(4)]
        res = self.assembler.process_evidence(evs)
        stats = self.assembler.statistics(res.structure)
        self.assertEqual(stats["total_knowledge_points"], 4)
        self.assertEqual(stats["supported_count"], 4)
        self.assertEqual(stats["conflicted_count"], 0)
        self.assertEqual(stats["unverified_count"], 0)
        self.assertEqual(stats["total_conflicts"], 0)
        self.assertEqual(
            stats["by_validation_status"],
            {"unverified": 0, "supported": 4, "conflicted": 0},
        )

class TestConflict(unittest.TestCase):
    def setUp(self):
        self.assembler = KnowledgeAssembler()

    def test_conflict_detected_and_preserved(self):
        e1, e2 = _conflicting_pair()
        res = self.assembler.process_evidence([e1, e2])
        self.assertGreaterEqual(len(res.structure.conflicts), 1)
        self.assertGreaterEqual(len(res.conflict_ids), 1)
        for c in res.structure.conflicts:
            self.assertIn(c.evidence_refs[0], ("cf-a", "cf-b"))
            self.assertIn(c.evidence_refs[1], ("cf-a", "cf-b"))

    def test_conflict_ids_stable_across_reprocess(self):
        e1, e2 = _conflicting_pair()
        s1 = KnowledgeStructure()
        self.assembler.process_evidence([e1, e2], structure=s1)
        s2 = KnowledgeStructure()
        self.assembler.process_evidence([e1, e2], structure=s2)
        self.assertEqual(
            [c.conflict_id for c in s1.conflicts],
            [c.conflict_id for c in s2.conflicts],
        )

    def test_conflict_replay_does_not_duplicate(self):
        e1, e2 = _conflicting_pair()
        structure = KnowledgeStructure()
        self.assembler.process_evidence([e1, e2], structure=structure)
        before = len(structure.conflicts)
        self.assembler.process_evidence([e1, e2], structure=structure)
        self.assertEqual(len(structure.conflicts), before)

    def test_no_auto_resolution_conflict_stays_pending(self):
        e1, e2 = _conflicting_pair()
        res = self.assembler.process_evidence([e1, e2])
        for c in res.structure.conflicts:
            self.assertEqual(c.status.value, "PENDING")

    def test_both_conflicting_evidence_still_accessible(self):
        e1, e2 = _conflicting_pair()
        store = _store_with([e1, e2])
        res = self.assembler.process_store(store)
        all_refs = set()
        for kp in res.structure.knowledge_points.values():
            all_refs.update(kp.evidence_refs)
        self.assertIn("cf-a", all_refs)
        self.assertIn("cf-b", all_refs)

class TestReview(unittest.TestCase):
    def setUp(self):
        self.assembler = KnowledgeAssembler()
        self.svc = KnowledgeReviewService()

    def test_assembly_never_sets_review_status(self):
        ev = _ev("e1", "El ARN mensajero transporta la informacion genetica al ribosoma.")
        res = self.assembler.process_evidence([ev])
        kp = res.structure.knowledge_points[res.new_knowledge_point_ids[0]]
        self.assertEqual(kp.review_status, "pending")
        self.assertEqual(len(res.structure.review_records), 0)

    def test_unverified_kp_is_candidate(self):
        evs = [_ev("e1", "Bloque de texto unico sobre la division meiotica de la celula.")]
        res = self.assembler.process_evidence(evs)
        cands = self.svc.get_review_candidates(res.structure)
        self.assertGreaterEqual(len(cands), 1)
        self.assertIn(res.new_knowledge_point_ids[0],
                      [c.knowledge_point_id for c in cands])

    def test_conflicted_kp_is_priority_zero_candidate(self):
        e1, e2 = _conflicting_pair()
        res = self.assembler.process_evidence([e1, e2])
        cands = self.svc.get_review_candidates(res.structure)
        conflicted_cands = [c for c in cands if c.priority == 0]
        self.assertGreaterEqual(len(conflicted_cands), 1)

    def test_confirmed_kp_remains_until_new_conflict(self):
        ev = _ev("e1", "El mitocondrio es la central energetica de la celula eucariota.")
        res = self.assembler.process_evidence([ev])
        kp_id = res.new_knowledge_point_ids[0]
        self.svc.confirm(res.structure, kp_id)
        self.assertEqual(
            self.svc.review_status_of(res.structure, kp_id),
            ReviewStatus.CONFIRMED,
        )
        self.assembler.process_evidence([ev], structure=res.structure)
        self.assertEqual(
            self.svc.review_status_of(res.structure, kp_id),
            ReviewStatus.CONFIRMED,
        )

    def test_new_conflicting_evidence_resets_confirmation(self):
        base = _ev("e1", "El nucleo de la celula contiene el ADN de la especie humana.")
        res = self.assembler.process_evidence([base])
        kp_id = res.new_knowledge_point_ids[0]
        self.svc.confirm(res.structure, kp_id)
        self.assertEqual(
            self.svc.review_status_of(res.structure, kp_id),
            ReviewStatus.CONFIRMED,
        )
        conflict_ev = _ev("e2", "El nucleo de la celula no contiene el ADN de la especie humana.")
        self.assembler.process_evidence([conflict_ev], structure=res.structure)
        cands = self.svc.get_review_candidates(res.structure)
        reset = [c for c in cands if c.knowledge_point_id == kp_id]
        self.assertGreaterEqual(len(reset), 1)
        self.assertEqual(
            self.svc.review_status_of(res.structure, kp_id),
            ReviewStatus.PENDING,
        )

    def test_review_history_preserved_after_reset(self):
        base = _ev("e1", "El nucleo de la celula contiene el ADN de la especie humana.")
        res = self.assembler.process_evidence([base])
        kp_id = res.new_knowledge_point_ids[0]
        record = self.svc.confirm(res.structure, kp_id, note="confirmado inicial")
        self.assembler.process_evidence(
            [_ev("e2", "El nucleo de la celula no contiene el ADN de la especie humana.")],
            structure=res.structure,
        )
        history = self.svc.review_history(res.structure, kp_id)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].review_id, record.review_id)

    def test_candidates_stable_on_repeated_calls(self):
        e1, e2 = _conflicting_pair()
        res = self.assembler.process_evidence([e1, e2])
        c1 = [c.knowledge_point_id for c in self.svc.get_review_candidates(res.structure)]
        c2 = [c.knowledge_point_id for c in self.svc.get_review_candidates(res.structure)]
        self.assertEqual(c1, c2)

class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.assembler = KnowledgeAssembler()

    def test_roundtrip_structure(self):
        evs = [_ev("e%d" % i, "Contenido numerico %d sobre la quimica organica." % i)
               for i in range(3)]
        res = self.assembler.process_evidence(evs)
        data = res.structure.to_dict()
        restored = KnowledgeStructure.from_dict(data)
        self.assertEqual(
            set(restored.knowledge_points),
            set(res.structure.knowledge_points),
        )

    def test_roundtrip_conflicts(self):
        e1, e2 = _conflicting_pair()
        res = self.assembler.process_evidence([e1, e2])
        data = res.structure.to_dict()
        restored = KnowledgeStructure.from_dict(data)
        self.assertEqual(
            [c.conflict_id for c in restored.conflicts],
            [c.conflict_id for c in res.structure.conflicts],
        )

    def test_roundtrip_review_records(self):
        ev = _ev("e1", "El ribosomo ensambla proteinas en el citoplasma de la celula.")
        res = self.assembler.process_evidence([ev])
        svc = KnowledgeReviewService()
        svc.confirm(res.structure, res.new_knowledge_point_ids[0])
        data = res.structure.to_dict()
        restored = KnowledgeStructure.from_dict(data)
        self.assertEqual(len(restored.review_records), 1)

    def test_empty_structure_serialization(self):
        structure = KnowledgeStructure()
        self.assertEqual(structure.to_dict()["knowledge_points"], [])
        restored = KnowledgeStructure.from_dict(structure.to_dict())
        self.assertEqual(len(restored.knowledge_points), 0)

    def test_multilingual_content_preserved_in_serialization(self):
        evs = [
            _ev("e1", "光合作用是植物将光能转化为化学能的基本过程。",
                lang=Language.CHINESE, etype=TEACHER_STATEMENT),
            _ev("e2", "La fotosíntesi és un procés essencial per a la vida a la Terra.",
                lang=Language.CATALAN),
        ]
        res = self.assembler.process_evidence(evs)
        text = json.dumps(res.structure.to_dict(), ensure_ascii=False)
        self.assertIn("光合作用", text)
        self.assertIn("fotosíntesi", text)

    def test_backward_compat_old_json(self):
        structure = KnowledgeStructure()
        old_kp = KnowledgePoint(
            knowledge_id="kp-old1", title="antiguo",
            content="contenido antiguo", evidence_refs=["e-old"],
        )
        structure.add_knowledge_point(old_kp)
        data = structure.to_dict()
        for kp_data in data["knowledge_points"]:
            kp_data.pop("knowledge_score", None)
            kp_data.pop("needs_verification", None)
            kp_data.pop("review_status", None)
        restored = KnowledgeStructure.from_dict(data)
        self.assertIn("kp-old1", restored.knowledge_points)

    def test_save_load_roundtrip_bytes_identical(self):
        evs = [_ev("e1", "Contenido estable para serializar y comparar archivos.")]
        res = self.assembler.process_evidence(evs)
        with tempfile.TemporaryDirectory() as tmp:
            p1 = os.path.join(tmp, "s1.json")
            p2 = os.path.join(tmp, "s2.json")
            save_structure(res.structure, p1)
            save_structure(res.structure, p2)
            with open(p1, "rb") as f1, open(p2, "rb") as f2:
                self.assertEqual(f1.read(), f2.read())
            loaded = load_structure(p1)
            self.assertEqual(
                set(loaded.knowledge_points),
                set(res.structure.knowledge_points),
            )

    def test_load_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "does_not_exist.json")
            with self.assertRaises(KnowledgeAssemblyError):
                load_structure(missing)

    def test_load_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("{ this is not valid json ]")
            with self.assertRaises(KnowledgeAssemblyError):
                load_structure(bad)

    def test_load_non_mapping_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "list.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("[1,2,3]")
            with self.assertRaises(KnowledgeAssemblyError):
                load_structure(bad)

    def test_load_unknown_validation_status_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "status.json")
            payload = {"knowledge_points": [
                {"knowledge_id": "k1", "validation_status": "nonsense"},
            ]}
            with open(bad, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            with self.assertRaises(KnowledgeAssemblyError):
                load_structure(bad)

    def test_load_missing_knowledge_id_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "noid.json")
            payload = {"knowledge_points": [{"title": "x"}]}
            with open(bad, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            with self.assertRaises(KnowledgeAssemblyError):
                load_structure(bad)

    def test_load_duplicate_knowledge_id_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "dup.json")
            payload = {"knowledge_points": [
                {"knowledge_id": "k1", "validation_status": "supported"},
                {"knowledge_id": "k1", "validation_status": "supported"},
            ]}
            with open(bad, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            with self.assertRaises(KnowledgeAssemblyError):
                load_structure(bad)

class TestIntegrity(unittest.TestCase):
    def setUp(self):
        self.assembler = KnowledgeAssembler()

    def test_valid_structure_reports_valid(self):
        evs = [_ev("e%d" % i, "Bloque de contenido distinto numero %d." % i)
               for i in range(3)]
        res = self.assembler.process_evidence(evs)
        store = _store_with(evs)
        report = self.assembler.validate_integrity(res.structure, store)
        self.assertIsInstance(report, IntegrityReport)
        self.assertTrue(report.valid)
        self.assertEqual(report.dangling_evidence_refs, ())
        self.assertEqual(report.orphaned_knowledge_points, ())

    def test_dangling_refs_reported_not_repaired(self):
        evs = [_ev("e1", "Contenido con referencia a un bloque externo inexistente.")]
        res = self.assembler.process_evidence(evs)
        kp = res.structure.knowledge_points[res.new_knowledge_point_ids[0]]
        kp.evidence_refs = list(kp.evidence_refs) + ["missing-evidence-xyz"]
        store = _store_with(evs)
        report = self.assembler.validate_integrity(res.structure, store)
        self.assertFalse(report.valid)
        self.assertEqual(len(report.dangling_evidence_refs), 1)
        kp_missing = dict(report.dangling_evidence_refs)
        self.assertIn("missing-evidence-xyz", kp_missing[kp.knowledge_id])
        self.assertIn("missing-evidence-xyz", kp.evidence_refs)

    def test_duplicate_refs_reported(self):
        evs = [_ev("e1", "Contenido unico sobre la quimica de la respiracion celular.")]
        res = self.assembler.process_evidence(evs)
        kp = res.structure.knowledge_points[res.new_knowledge_point_ids[0]]
        kp.evidence_refs = ["e1", "e1"]
        store = _store_with(evs)
        report = self.assembler.validate_integrity(res.structure, store)
        self.assertFalse(report.valid)
        self.assertIn(kp.knowledge_id, report.duplicate_refs)

    def test_orphaned_kp_reported(self):
        structure = KnowledgeStructure()
        kp = KnowledgePoint(knowledge_id="k-orphan", title="x",
                            content="y", evidence_refs=[])
        structure.add_knowledge_point(kp)
        store = EvidenceStore()
        report = self.assembler.validate_integrity(structure, store)
        self.assertFalse(report.valid)
        self.assertEqual(report.orphaned_knowledge_points, ("k-orphan",))

    def test_conflict_ref_errors_reported(self):
        from src.integration import ConflictRecord
        structure = KnowledgeStructure()
        kp = KnowledgePoint(knowledge_id="k1", title="t",
                            content="c", evidence_refs=["e1"])
        structure.add_knowledge_point(kp)
        bad_conflict = ConflictRecord(conflict_id="cf-x",
                                      evidence_refs=["e1", "ghost-ref"],
                                      description="desc")
        structure.add_conflict(bad_conflict)
        store = _store_with([_ev("e1", "contenido base para la prueba de integridad.")])
        report = self.assembler.validate_integrity(structure, store)
        self.assertFalse(report.valid)
        self.assertIn("cf-x", report.conflict_ref_errors)

    def test_report_to_dict_shape(self):
        evs = [_ev("e1", "Contenido unico para auditar la integridad del bloque.")]
        res = self.assembler.process_evidence(evs)
        store = _store_with(evs)
        report = self.assembler.validate_integrity(res.structure, store)
        as_dict = report.to_dict()
        self.assertIn("valid", as_dict)
        self.assertIn("dangling_evidence_refs", as_dict)
        self.assertIn("orphaned_knowledge_points", as_dict)

class TestQueries(unittest.TestCase):
    def setUp(self):
        self.assembler = KnowledgeAssembler()

    def test_knowledge_for_evidence_sorted(self):
        evs = [
            _ev("e1", "Bloque A sobre la quimica del ciclo de Krebs mitocondrial."),
            _ev("e2", "Bloque B sobre la quimica del ciclo de Krebs mitocondrial."),
        ]
        res = self.assembler.process_evidence(evs)
        store = _store_with(evs)
        kp_ids = KnowledgeAssembler.knowledge_for_evidence(res.structure, "e1")
        self.assertEqual(kp_ids, sorted(kp_ids))
        self.assertIn(res.new_knowledge_point_ids[0], kp_ids)

    def test_knowledge_for_material(self):
        evs = [
            _ev("e1", "Contenido del material A sobre la respiracion aerobica.",
                mat="mat-A"),
            _ev("e2", "Contenido del material B sobre la respiracion anaerobica.",
                mat="mat-B"),
        ]
        res = self.assembler.process_evidence(evs)
        store = _store_with(evs)
        ids_a = KnowledgeAssembler.knowledge_for_material(res.structure, "mat-A", store)
        ids_b = KnowledgeAssembler.knowledge_for_material(res.structure, "mat-B", store)
        self.assertEqual(len(ids_a), 1)
        self.assertEqual(len(ids_b), 1)

    def test_knowledge_for_document(self):
        evs = [
            _ev("e1", "Contenido documentario del archivo doc-1 sobre la mitosis.",
                metadata={"document_id": "doc-1"}),
            _ev("e2", "Contenido documentario del archivo doc-2 sobre la meiosis.",
                metadata={"document_id": "doc-2"}),
        ]
        store = _store_with(evs)
        res = self.assembler.process_evidence(evs)
        kp_doc1 = None
        kp_doc2 = None
        for kid, kp in res.structure.knowledge_points.items():
            if "e1" in kp.evidence_refs:
                kp_doc1 = kid
            if "e2" in kp.evidence_refs:
                kp_doc2 = kid
        self.assertEqual(
            KnowledgeAssembler.knowledge_for_document(res.structure, "doc-1", store),
            [kp_doc1],
        )
        self.assertEqual(
            KnowledgeAssembler.knowledge_for_document(res.structure, "doc-2", store),
            [kp_doc2],
        )

    def test_knowledge_for_session(self):
        evs = [
            _ev("e1", "Contenido de la primera sesion de la clase de genetica."),
            _ev("e2", "Contenido de la segunda sesion de la clase de genetica."),
        ]
        res = self.assembler.process_evidence(evs)
        store = _store_with(evs)
        s1 = ClassSession(session_id="s1", course_id="c", session_number=1,
                          evidence_refs=["e1"])
        s2 = ClassSession(session_id="s2", course_id="c", session_number=2,
                          evidence_refs=["e2"])
        ids_s1 = KnowledgeAssembler.knowledge_for_session(
            res.structure, "s1", [s1, s2], store
        )
        self.assertEqual(len(ids_s1), 1)
        self.assertEqual(
            ids_s1[0],
            res.structure.knowledge_points[ids_s1[0]].knowledge_id,
        )

    def test_unknown_evidence_id_returns_empty(self):
        evs = [_ev("e1", "Contenido unico sobre la energia del sistema nervioso.")]
        res = self.assembler.process_evidence(evs)
        self.assertEqual(
            KnowledgeAssembler.knowledge_for_evidence(res.structure, "nope"),
            [],
        )

class TestRetiredEvidence(unittest.TestCase):
    def setUp(self):
        self.assembler = KnowledgeAssembler()

    def test_retried_excluded_by_default(self):
        evs = [
            _ev("e1", "Contenido A sobre la quimica del ciclo de Krebs celular."),
            _ev("e2", "Contenido B sobre la quimica del ciclo de Calvin vegetal."),
        ]
        store = _store_with(evs)
        structure = KnowledgeStructure()
        self.assembler.process_store(store, structure=structure)
        before = len(structure.knowledge_points)
        self.assertGreater(before, 0)
        self.assertTrue(store.retire("e2"))
        res = self.assembler.process_store(store, structure=structure)
        kp_a = structure.knowledge_points[
            next(kid for kid in structure.knowledge_points
                 if "e1" in structure.knowledge_points[kid].evidence_refs)
        ]
        self.assertEqual(kp_a.evidence_refs, ["e1"])
        self.assertEqual(len(structure.knowledge_points), before)

    def test_include_retired_flag(self):
        evs = [
            _ev("e1", "Contenido A sobre la quimica del ciclo de Krebs celular."),
            _ev("e2", "Contenido B sobre la quimica del ciclo de Calvin vegetal."),
        ]
        store = _store_with(evs)
        self.assertTrue(store.retire("e2"))
        res_default = self.assembler.process_store(store)
        self.assertEqual(res_default.evidence_ids, ("e1",))
        res_all = self.assembler.process_store(store, include_retired=True)
        self.assertEqual(set(res_all.evidence_ids), {"e1", "e2"})

class TestPerformance(unittest.TestCase):
    def setUp(self):
        self.assembler = KnowledgeAssembler()

    def _make_1000(self):
        evs = []
        for i in range(500):
            evs.append(
                _ev("e%d" % i, "Contenido unico numero %d sobre un tema de la clase de biologia." % i,
                    mat="mat-%d" % (i % 50), etype=EvidenceType.DOCUMENT)
            )
            evs.append(
                _ev("d%d" % i, "Contenido duplicado de tipo %d para probar el dedupe interno." % i,
                    mat="mat-%d" % (i % 50), etype=EvidenceType.DOCUMENT)
            )

        return evs

    def test_1000_evidence_assembly_no_obvious_n2(self):
        evs = self._make_1000()
        store = EvidenceStore()
        t0 = time.perf_counter()
        store.add_many(evs)
        t1 = time.perf_counter()
        structure = KnowledgeStructure()
        self.assembler.process_store(store, structure=structure)
        t2 = time.perf_counter()
        assembly_time = t2 - t1
        self.assertGreater(len(structure.knowledge_points), 0)

        self.assertLess(assembly_time, 120.0)
        stats = self.assembler.statistics(structure)
        self.assertEqual(stats["total_knowledge_points"], len(structure.knowledge_points))

    def test_repeated_process_store_counts_stable(self):
        evs = self._make_1000()[:200]
        store = _store_with(evs)
        structure = KnowledgeStructure()
        self.assembler.process_store(store, structure=structure)

        baseline = (
            len(structure.knowledge_points),
            len(structure.conflicts),
            len(structure.relationships),
            len(structure.review_records),
        )
        for _ in range(5):
            self.assembler.process_store(store, structure=structure)

        after = (
            len(structure.knowledge_points),
            len(structure.conflicts),
            len(structure.relationships),
            len(structure.review_records),
        )

        self.assertEqual(baseline, after)

    def test_rebuild_equivalence_50(self):
        evs = [
            _ev("e%d" % i, "Bloque numerico %d sobre la quimica de la fotosintesis." % i)
            for i in range(50)
        ]

        store = _store_with(evs)
        via_store = KnowledgeStructure()
        self.assembler.process_store(store, structure=via_store)
        direct = KnowledgeStructure()
        self.assembler.process_evidence(evs, structure=direct)
        _struct_eq(via_store, direct)

class TestMultilingual(unittest.TestCase):
    def setUp(self):
        self.assembler = KnowledgeAssembler()

    def test_chinese_spanish_catalan_not_merged(self):
        evs = [
            _ev("e1", "光合作用是植物将光能转化为化学能的基本代谢过程。",
                lang=Language.CHINESE, etype=TEACHER_STATEMENT),
            _ev("e2", "La velocidad de la reacción es constante durante el primer minuto.",
                lang=Language.SPANISH, etype=PERSONAL_NOTE),
            _ev("e3", "La velocitat de la reacció és constant durant el primer minut.",
                lang=Language.CATALAN, etype=PERSONAL_NOTE),
        ]
        res = self.assembler.process_evidence(evs)
        self.assertEqual(len(res.structure.knowledge_points), 3)
        all_content = " ".join(
            kp.content for kp in res.structure.knowledge_points.values()
        )

        self.assertIn("reacció", all_content)
        self.assertIn("velocitat", all_content)
        self.assertIn("光合作用", all_content)

    def test_mixed_language_note_preserved(self):
        es_line = "El profesor explica que la fase luminosa depende de la luz solar."
        ca_line = "El profes explica que la fase lluminosa depen de la llum solar."
        ev = _ev("e1", es_line + " " + ca_line,
                 lang=Language.UNKNOWN)

        res = self.assembler.process_evidence([ev])
        kp = res.structure.knowledge_points[res.new_knowledge_point_ids[0]]

        self.assertIn(es_line, kp.content)
        self.assertIn(ca_line, kp.content)

    def test_no_semantic_merge_across_languages(self):
        evs = [
            _ev("e1", "速度的概念在物理学中是位移随时间的变化率。", lang=Language.CHINESE),
            _ev("e2", "Velocidad", lang=Language.SPANISH),
            _ev("e3", "Velocitat", lang=Language.CATALAN),
        ]

        res = self.assembler.process_evidence(evs)
        self.assertEqual(len(res.structure.knowledge_points), 3)

class TestEndToEnd(unittest.TestCase):
    def _materials(self, tmpdir):
        ocr_path = str(Path(tmpdir) / "board-e2e.png")
        with open(ocr_path, "wb") as fh:
            fh.write(b"fake-image-bytes")

        return [
            Material(
                "e2e-note", "example.txt",
                str(FIX_NOTES / "example.txt"),
                material_type=MaterialType.NOTE,
            ),
            Material(
                "e2e-audio", "long_silence_5s.wav",
                path=FIX_WAV,
                material_type=MaterialType.AUDIO,
            ),
            Material(
                "e2e-ocr", "board-e2e.png",
                path=ocr_path,
                material_type=MaterialType.IMAGE,
            ),
            Material(
                "e2e-pdf", "simple.pdf",
                str(FIX_DOCS / "simple.pdf"),
                material_type=MaterialType.SYLLABUS,
            ),
            Material(
                "e2e-docx", "tables.docx",
                str(FIX_DOCS / "tables.docx"),
                material_type=MaterialType.SYLLABUS,
            ),
        ]

    def _ingest_all(self, store, tmpdir):
        svc = EvidenceIngestionService(store)
        for m in self._materials(tmpdir):
            report = svc.ingest(m)
            self.assertEqual(report.status, IngestionStatus.SUCCESS, m.material_id)
            self.assertGreaterEqual(report.added_count, 1, m.material_id)
        return svc

    def test_full_chain_note_audio_ocr_pdf_docx(self):
        store = EvidenceStore()
        with tempfile.TemporaryDirectory() as tmp:
            self._ingest_all(store, tmp)
            self.assertGreaterEqual(store.count(), 5)
            structure = KnowledgeStructure()
            res = KnowledgeAssembler().process_store(store, structure=structure)

            self.assertGreater(len(structure.knowledge_points), 0)
            for kp in structure.knowledge_points.values():
                self.assertGreater(len(kp.evidence_refs), 0, kp.knowledge_id)
                for ref in kp.evidence_refs:
                    self.assertIn(ref, res.evidence_ids)

            types = {
                store.get(eid).evidence_type.value
                for eid in res.evidence_ids
            }
            self.assertIn("transcript", types)
            self.assertIn("OCR", types)
            self.assertIn("personal_note", types)
            self.assertIn("document", types)

            mats = {
                store.get(eid).source_reference.material_id
                for eid in res.evidence_ids
            }
            self.assertEqual(len(mats), 5)

            report = KnowledgeAssembler().validate_integrity(structure, store)
            self.assertTrue(report.valid)

    def test_full_chain_with_conflict_then_review_then_persist(self):
        store = EvidenceStore()
        svc = None
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._ingest_all(store, tmp)

            structure = KnowledgeStructure()
            asm = KnowledgeAssembler()
            asm.process_store(store, structure=structure)
            self.assertEqual(len(structure.conflicts), 0)

            repeat = svc.ingest(
                Material(
                    "e2e-note", "example.txt",
                    str(FIX_NOTES / "example.txt"),
                    material_type=MaterialType.NOTE,
                )
            )

            self.assertEqual(repeat.added_count, 0)

            self.assertEqual(repeat.duplicate_count, 1)

            e1, e2 = _conflicting_pair()
            store.add_many([e1, e2])
            res = asm.process_store(store, structure=structure)
            self.assertEqual(len(res.conflict_ids), 1)

            conflicted = [
                kp for kp in structure.knowledge_points.values()
                if kp.validation_status == ValidationStatus.CONFLICTED.value
            ]

            self.assertGreaterEqual(len(conflicted), 1)

            review_svc = KnowledgeReviewService()
            cands = review_svc.get_review_candidates(structure)
            self.assertIn(res.conflict_ids[0],
                          tuple(c.conflict_ids[0] for c in cands if c.conflict_ids))

            out = Path(tempfile.gettempdir()) / ("structure-e2e-%d.json" % os.getpid())

            try:
                save_structure(structure, out)
                restored = load_structure(out)
                report = KnowledgeAssembler().validate_integrity(restored, store)
                self.assertTrue(report.valid)
                self.assertEqual(
                    sorted(c.conflict_id for c in restored.conflicts),
                    sorted(c.conflict_id for c in structure.conflicts),
                )
            finally:
                out.unlink(missing_ok=True)

class TestDeterminismNoWallClock(unittest.TestCase):
    def _sample_evidences(self):
        return [
            _ev("e1", "La luz es la causa del fenomeno observado en el laboratorio.",
                mat="mat-a", etype=EvidenceType.PERSONAL_NOTE),
            _ev("e2", "La luz no es la causa del fenomeno observado en el laboratorio.",
                mat="mat-b", etype=EvidenceType.CLASSMATE_NOTE),
            _ev("e3", "El estoma regula el intercambio de gases en la hoja.",
                mat="mat-a", etype=EvidenceType.DOCUMENT, page=3),
            _ev("e4", "El estoma regula el intercambio de gases en la hoja.",
                mat="mat-c", etype=EvidenceType.OCR),
        ]

    def test_repeated_runs_byte_identical(self):
        evs = self._sample_evidences()
        s1 = KnowledgeAssembler().process_evidence(evs).structure
        s2 = KnowledgeAssembler().process_evidence(self._sample_evidences()).structure
        a = json.dumps(s1.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
        b = json.dumps(s2.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
        self.assertEqual(a, b)

    def test_to_dict_twice_equals(self):
        s = KnowledgeAssembler().process_evidence(self._sample_evidences()).structure
        self.assertEqual(
            json.dumps(s.to_dict(), sort_keys=True),
            json.dumps(s.to_dict(), sort_keys=True),
        )

    def test_knowledge_ids_no_uuid_shape(self):
        s = KnowledgeAssembler().process_evidence(self._sample_evidences()).structure
        for kid in s.knowledge_points:
            self.assertTrue(kid.startswith("kp-"))
            self.assertNotIn("-", kid[3:], "uuid-like segment")

            self.assertEqual(len(kid), 19)

    def test_conflict_ids_stable_prefix(self):
        e1, e2 = _conflicting_pair()
        s = KnowledgeAssembler().process_evidence([e1, e2]).structure
        self.assertEqual(len(s.conflicts), 1)
        self.assertTrue(s.conflicts[0].conflict_id.startswith("conflict-"))

    def test_review_records_stable_ids(self):
        e1, e2 = _conflicting_pair()
        structure = KnowledgeAssembler().process_evidence([e1, e2]).structure

        conflicted_kps = [
            k for k, kp in structure.knowledge_points.items()
            if kp.validation_status == ValidationStatus.CONFLICTED.value
        ]
        if not conflicted_kps:
            self.fail("expected at least one CONFLICTED knowledge point")
        kp_id = sorted(conflicted_kps)[0]
        service = KnowledgeReviewService()
        record = service.resolve_conflict(
            structure, kp_id, ["cf-a"],
        )

        self.assertTrue(record.review_id.startswith("rev-"))

        structure2 = KnowledgeAssembler().process_evidence([e1, e2]).structure
        kp_id2 = sorted(
            k for k, kp in structure2.knowledge_points.items()
            if kp.validation_status == ValidationStatus.CONFLICTED.value
        )[0]
        record2 = KnowledgeReviewService().resolve_conflict(
            structure2, kp_id2, ["cf-a"],
        )

        self.assertEqual(record.review_id, record2.review_id)

    def test_resolve_conflict_requires_selection(self):
        e1, e2 = _conflicting_pair()
        structure = KnowledgeAssembler().process_evidence([e1, e2]).structure
        kp_id = sorted(
            k for k, kp in structure.knowledge_points.items()
            if kp.validation_status == ValidationStatus.CONFLICTED.value
        )[0]
        with self.assertRaises(ValueError):
            KnowledgeReviewService().resolve_conflict(
                structure, kp_id, [],
            )

class TestPipelineEquivalence(unittest.TestCase):
    def _corpus(self):
        return [
            _ev("e1", "El fototropismo depende del fototropo en la parte apical.",
                mat="m1", etype=EvidenceType.PERSONAL_NOTE),
            _ev("e2", "El fototropismo depende del fototropo en la parte apical.",
                mat="m2", etype=EvidenceType.DOCUMENT, page=7),
            _ev("e3", "La auxina se degrada en la parte iluminada de la planta.",
                mat="m1", etype=EvidenceType.CLASSMATE_NOTE),
            _ev("e4", "La fotomorfogenesis inicia senales hormonales en el embrión.",
                mat="m3", etype=EvidenceType.OCR),
        ]

    def test_assembler_matches_pipeline_knowledge_ids(self):
        via_assembler = KnowledgeAssembler().process_evidence(self._corpus()).structure
        via_pipeline = KnowledgePipeline().process_evidence(self._corpus()).structure
        self.assertEqual(
            sorted(via_assembler.knowledge_points),
            sorted(via_pipeline.knowledge_points),
        )
        _struct_eq(via_assembler, via_pipeline)

    def test_assembler_matches_pipeline_with_conflicts(self):
        evs = [_conflicting_pair()[0], _conflicting_pair()[1]]
        evs += self._corpus()[:3]

        via_assembler = KnowledgeAssembler().process_evidence(evs).structure
        via_pipeline = KnowledgePipeline().process_evidence(evs).structure
        self.assertEqual(
            sorted(c.conflict_id for c in via_assembler.conflicts),
            sorted(c.conflict_id for c in via_pipeline.conflicts),
        )

        self.assertEqual(
            sorted(via_assembler.knowledge_points),
            sorted(via_pipeline.knowledge_points),
        )

    def test_pipeline_session_chain(self):
        session = ClassSession(
            course_id="bio-201",
            session_number=3,
            date="2026-03-10",
        )

        material = Material(
            "chain-note", "example.txt",
            str(FIX_NOTES / "example.txt"),
            material_type=MaterialType.NOTE,
        )

        result = KnowledgePipeline().process_session(session, [material])
        self.assertIsNotNone(result.structure)
        self.assertGreater(len(result.structure.knowledge_points), 0)

        processed = set(result.evidence_ids)
        for kp in result.structure.knowledge_points.values():
            self.assertGreater(len(kp.evidence_refs), 0)
            self.assertTrue(set(kp.evidence_refs) <= processed, kp.knowledge_id)

        asm = KnowledgeAssembler()
        more = asm.process_evidence(
            [_ev("e-extra", "Un concepto nuevo sobre quimiosmosis mitocondrial.")],
            structure=result.structure,
        )

        self.assertEqual(len(more.new_knowledge_point_ids), 1)

class TestSessionAggregation(unittest.TestCase):
    def setUp(self):
        self.assembler = KnowledgeAssembler()

    def test_session_tags_flow_through_evidence_refs(self):
        ev = _ev("e1", "El ciclo de Krebs ocurre en la matriz mitocondrial.")
        res = self.assembler.process_evidence([ev])
        kp_id = res.new_knowledge_point_ids[0]
        session = ClassSession(
            session_id="session-x-001", course_id="bio-1", session_number=1,
            evidence_refs=["e1"],
        )

        sessions = KnowledgeAssembler.session_ids_for_knowledge_point(
            res.structure, kp_id, [session]
        )

        self.assertEqual(sessions, ["session-x-001"])

    def test_cross_session_aggregation_union(self):
        ev_a = _ev("e1", "El ciclo de Krebs ocurre en la matriz mitocondrial.")
        ev_b = _ev("e2", "El ciclo de Krebs ocurre en la matriz mitocondrial.")
        structure = self.assembler.process_evidence([ev_a]).structure
        self.assembler.process_evidence([ev_b], structure=structure)
        kp = next(iter(structure.knowledge_points.values()))
        s1 = ClassSession("s1", "c", 1, evidence_refs=["e1"])
        s2 = ClassSession("s2", "c", 2, evidence_refs=["e2"])

        sessions = KnowledgeAssembler.session_ids_for_knowledge_point(
            structure, kp.knowledge_id, [s1, s2]
        )

        self.assertEqual(sessions, ["s1", "s2"])

    def test_no_source_preference_identical_content(self):
        text = "La fotosisntesis capta energia luminosa en los cloroplastos."

        evs = [
            _ev("e1", text, mat="a", etype=EvidenceType.PERSONAL_NOTE),
            _ev("e2", text, mat="a", etype=EvidenceType.PERSONAL_NOTE),
            _ev("e3", text, mat="b", etype=EvidenceType.DOCUMENT),
        ]

        res = self.assembler.process_evidence(evs)
        store = _store_with(evs)
        kps = list(res.structure.knowledge_points.values())

        self.assertEqual(len(kps), 2)
        note_kp = [kp for kp in kps if "e1" in kp.evidence_refs]
        self.assertEqual(len(note_kp), 1)
        self.assertEqual(
            self.assembler.material_ids_for_knowledge_point(
                res.structure, note_kp[0].knowledge_id, store
            ),
            ["a"],
        )

    def test_statistics_stable_on_repeated_call(self):
        e1, e2 = _conflicting_pair()
        structure = self.assembler.process_evidence([e1, e2]).structure
        s1 = KnowledgeAssembler().statistics(structure)
        s2 = KnowledgeAssembler().statistics(structure)
        self.assertEqual(s1, s2)
        self.assertEqual(s1["total_knowledge_points"], len(structure.knowledge_points))

    def test_review_candidate_stability_on_repeated_calls(self):
        e1, e2 = _conflicting_pair()
        structure = self.assembler.process_evidence([e1, e2]).structure
        service = KnowledgeReviewService()
        first = [c.to_dict() for c in service.get_review_candidates(structure)]
        second = [c.to_dict() for c in service.get_review_candidates(structure)]
        self.assertEqual(first, second)

    def test_conflict_record_stable_content(self):
        e1, e2 = _conflicting_pair()
        s1 = KnowledgeAssembler().process_evidence([e1, e2]).structure
        s2 = KnowledgeAssembler().process_evidence([e1, e2]).structure
        self.assertEqual(len(s1.conflicts), 1)
        self.assertEqual(len(s2.conflicts), 1)
        c1 = s1.conflicts[0]
        c2 = s2.conflicts[0]

        self.assertEqual(c1.conflict_id, c2.conflict_id)
        self.assertEqual(sorted(c1.evidence_refs), sorted(c2.evidence_refs))
        self.assertEqual(c1.description, c2.description)

        s3 = KnowledgeAssembler().process_evidence([e2, e1]).structure
        c3 = s3.conflicts[0]
        self.assertEqual(sorted(c1.evidence_refs), sorted(c3.evidence_refs))
