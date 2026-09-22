"""Task 24 - Unified Evidence Ingestion Service tests.

Covers: source routing, single-material cases A-H, batch ordering /
failure isolation / repeated batch, idempotency, provenance preservation,
Unicode round-trip, determinism, history / status / report helpers,
statistics, report serialization, thread safety, large batch performance,
store-injection independence, and real end-to-end chains (Note, Audio,
OCR, PDF, DOCX) feeding the shared EvidenceStore.
"""

import json
import os
import tempfile
import threading
import time
from pathlib import Path

from src.models import (
    ClassSession,
    Evidence,
    EvidenceType,
    Language,
    Material,
    MaterialType,
    SourceReference,
)
from src.evidence_store import EvidenceStore
from src.evidence_ingestion import (
    BatchIngestionReport,
    EvidenceIngestionService,
    ExtractorAdapter,
    IngestionError,
    IngestionErrorCode,
    IngestionHistory,
    IngestionHistoryEntry,
    IngestionReport,
    IngestionStatus,
    resolve_source_type,
)

FIX_DOCS = Path(__file__).parent / "fixtures" / "documents"
FIX_NOTES = Path(__file__).parent / "fixtures" / "notes"
FIX_WAV = str(Path(__file__).parent / "fixtures" / "long_silence_5s.wav")


def _note_material(tmpdir, name, body, material_id, language=Language.UNKNOWN, path_note=True):
    path = str(Path(tmpdir) / name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return Material(
        material_id=material_id,
        filename=name,
        path=path,
        material_type=MaterialType.NOTE,
        language=language,
    )


class FakeNoteExtractor(ExtractorAdapter):
    """Deterministic fake note extractor (no uuid4, no files)."""

    source_type = "note"

    def __init__(self, texts_by_id=None, fail_ids=None, malformed_ids=None, empty_ids=None, skip_ids=None):
        self.texts_by_id = texts_by_id or {}
        self.fail_ids = set(fail_ids or ())
        self.malformed_ids = set(malformed_ids or ())
        self.empty_ids = set(empty_ids or ())
        self.skip_ids = set(skip_ids or ())

    def extract(self, material):
        mid = material.material_id
        if mid in self.skip_ids:
            return None
        if mid in self.fail_ids:
            raise RuntimeError("fake extractor failure for " + mid)
        if mid in self.empty_ids:
            return []
        texts = self.texts_by_id.get(mid, [mid + " default text"])
        out = []
        for i, text in enumerate(texts):
            ev = Evidence(
                content=text,
                language=Language.SPANISH,
                source_reference=SourceReference(
                    material_id=mid,
                    location=material.filename,
                    paragraph=f"para-{i}",
                ),
                evidence_type=EvidenceType.PERSONAL_NOTE,
            )
            ev.evidence_id = "fake-evid-" + mid + "-" + str(i)
            out.append(ev)
        if mid in self.malformed_ids:
            out.append({"not": "an Evidence"})
        return out


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

class TestRouting:
    def test_note_extension(self):
        m = Material(material_id="r1", path="notes/a.txt", material_type=MaterialType.NOTE)
        assert resolve_source_type(m) == "note"

    def test_markdown_extension_uppercase(self):
        m = Material(material_id="r2", path="notes/A.MD")
        assert resolve_source_type(m) == "note"

    def test_audio_extension(self):
        m = Material(material_id="r3", path="audio/a.mp3", material_type=MaterialType.AUDIO)
        assert resolve_source_type(m) == "audio"

    def test_image_extension(self):
        m = Material(material_id="r4", path="img/b.PNG", material_type=MaterialType.IMAGE)
        assert resolve_source_type(m) == "ocr"

    def test_pdf_extension(self):
        m = Material(material_id="r5", path="docs/c.pdf")
        assert resolve_source_type(m) == "pdf"

    def test_docx_extension(self):
        m = Material(material_id="r6", path="docs/d.docx")
        assert resolve_source_type(m) == "docx"

    def test_unsupported_extension(self):
        m = Material(material_id="r7", path="data.bin")
        assert resolve_source_type(m) == "unsupported"

    def test_audio_type_fallback(self):
        m = Material(material_id="r8", path="recordings/take1", material_type=MaterialType.AUDIO)
        assert resolve_source_type(m) == "audio"

    def test_image_type_fallback(self):
        m = Material(material_id="r9", path="photos/pic1", material_type=MaterialType.IMAGE)
        assert resolve_source_type(m) == "ocr"

    def test_note_type_fallback(self):
        m = Material(material_id="r10", path="misc/notes1", material_type=MaterialType.NOTE)
        assert resolve_source_type(m) == "note"

    def test_syllabus_type_maps_to_note(self):
        m = Material(material_id="r11", path="course/syllabus", material_type=MaterialType.SYLLABUS)
        assert resolve_source_type(m) == "note"

    def test_text_type_is_unsupported(self):
        m = Material(material_id="r12", path="misc/plain", material_type=MaterialType.TEXT)
        assert resolve_source_type(m) == "unsupported"

    def test_filename_fallback_when_path_empty(self):
        m = Material(material_id="r13", filename="x.txt", path="")
        assert resolve_source_type(m) == "note"

    def test_routing_deterministic(self):
        a = Material(material_id="x", path="f.docx")
        b = Material(material_id="x", path="f.docx")
        assert resolve_source_type(a) == resolve_source_type(b)


# ---------------------------------------------------------------------------
# Single-material error recovery cases (spec 43 A-H)
# ---------------------------------------------------------------------------

class TestSingleMaterialCases:
    def test_case_a_success_all_new(self, tmp_path):
        store = EvidenceStore()
        m = _note_material(str(tmp_path), "a.txt", "alpha\n", "mA")
        svc = EvidenceIngestionService(store)
        report = svc.ingest(m)
        assert report.status == IngestionStatus.SUCCESS
        assert report.added_count == 1
        assert report.duplicate_count == 0
        assert len(report.evidence_ids) == 1
        assert store.count() == 1

    def test_case_b_all_duplicates(self, tmp_path):
        store = EvidenceStore()
        m = _note_material(str(tmp_path), "b.txt", "beta\n", "mB")
        svc = EvidenceIngestionService(store)
        first = svc.ingest(m)
        assert first.added_count == 1
        second = svc.ingest(m)
        assert second.status == IngestionStatus.SUCCESS
        assert second.added_count == 0
        assert second.duplicate_count == 1
        assert store.count() == 1

    def test_case_c_empty_result_success_zero(self):
        store = EvidenceStore()
        ext = FakeNoteExtractor(empty_ids={"mC"})
        svc = EvidenceIngestionService(store, extractors={"note": ext})
        m = Material(material_id="mC", filename="c.txt", path="c.txt", material_type=MaterialType.NOTE)
        report = svc.ingest(m)
        assert report.status == IngestionStatus.SUCCESS
        assert report.total_extracted == 0
        assert report.added_count == 0
        assert store.count() == 0

    def test_case_d_known_error_fails(self):
        store = EvidenceStore()
        ext = FakeNoteExtractor(fail_ids={"mD"})
        svc = EvidenceIngestionService(store, extractors={"note": ext})
        m = Material(material_id="mD", filename="d.txt", path="d.txt", material_type=MaterialType.NOTE)
        report = svc.ingest(m)
        assert report.status == IngestionStatus.FAILED
        assert report.errors[0].code == IngestionErrorCode.EXTRACTOR_ERROR.value
        assert report.errors[0].fatal is True
        assert store.count() == 0

    def test_case_e_unexpected_exception_fails(self):
        class Boom(ExtractorAdapter):
            source_type = "note"

            def extract(self, material):
                raise ValueError("unexpected boom")

        store = EvidenceStore()
        svc = EvidenceIngestionService(store, extractors={"note": Boom()})
        m = Material(material_id="mE", filename="e.txt", path="e.txt", material_type=MaterialType.NOTE)
        report = svc.ingest(m)
        assert report.status == IngestionStatus.FAILED
        assert report.errors[0].code == IngestionErrorCode.EXTRACTOR_ERROR.value
        assert store.count() == 0

    def test_case_f_store_rejection_partial(self):
        class Rejecting(ExtractorAdapter):
            source_type = "note"

            def extract(self, material):
                good = Evidence(
                    evidence_id="rej-good",
                    content="ok text",
                    source_reference=SourceReference(material_id=material.material_id),
                    evidence_type=EvidenceType.PERSONAL_NOTE,
                )
                bad = Evidence(
                    evidence_id="rej-bad",
                    content="   ",
                    source_reference=SourceReference(material_id=material.material_id),
                    evidence_type=EvidenceType.PERSONAL_NOTE,
                )
                return [good, bad]

        store = EvidenceStore()
        svc = EvidenceIngestionService(store, extractors={"note": Rejecting()})
        m = Material(material_id="mF", filename="f.txt", path="f.txt", material_type=MaterialType.NOTE)
        report = svc.ingest(m)
        assert report.status == IngestionStatus.PARTIAL
        assert report.added_count == 1
        assert report.rejected_count == 1
        assert report.errors[0].code == IngestionErrorCode.STORE_REJECTION.value
        assert store.count() == 1

    def test_case_g_batch_multiple_failures(self):
        store = EvidenceStore()
        ext = FakeNoteExtractor(fail_ids={"bad1", "bad2"})
        svc = EvidenceIngestionService(store, extractors={"note": ext})
        materials = [
            Material(material_id="ok1", filename="1.txt", path="1.txt", material_type=MaterialType.NOTE),
            Material(material_id="bad1", filename="2.txt", path="2.txt", material_type=MaterialType.NOTE),
            Material(material_id="ok2", filename="3.txt", path="3.txt", material_type=MaterialType.NOTE),
            Material(material_id="bad2", filename="4.txt", path="4.txt", material_type=MaterialType.NOTE),
        ]
        batch = svc.ingest_many(materials)
        statuses = [r.status for r in batch.reports]
        assert statuses == [
            IngestionStatus.SUCCESS,
            IngestionStatus.FAILED,
            IngestionStatus.SUCCESS,
            IngestionStatus.FAILED,
        ]
        assert batch.succeeded == 2
        assert batch.failed == 2
        assert store.count() == 2

    def test_case_h_retry_after_partial(self):
        class HalfReject(ExtractorAdapter):
            source_type = "note"

            def __init__(self):
                self.calls = 0

            def extract(self, material):
                self.calls += 1
                if self.calls == 1:
                    return [
                        Evidence(
                            evidence_id="retry-good",
                            content="retry body",
                            source_reference=SourceReference(material_id=material.material_id),
                            evidence_type=EvidenceType.PERSONAL_NOTE,
                        ),
                        Evidence(
                            evidence_id="retry-rejected",
                            content="   ",
                            source_reference=SourceReference(material_id=material.material_id),
                            evidence_type=EvidenceType.PERSONAL_NOTE,
                        ),
                    ]
                return [
                    Evidence(
                        evidence_id="retry-good",
                        content="retry body",
                        source_reference=SourceReference(material_id=material.material_id),
                        evidence_type=EvidenceType.PERSONAL_NOTE,
                    ),
                ]

        store = EvidenceStore()
        ext = HalfReject()
        svc = EvidenceIngestionService(store, extractors={"note": ext})
        m = Material(material_id="mH", filename="h.txt", path="h.txt", material_type=MaterialType.NOTE)
        r1 = svc.ingest(m)
        assert r1.status == IngestionStatus.PARTIAL
        assert r1.added_count == 1
        r2 = svc.ingest(m)
        assert r2.status == IngestionStatus.SUCCESS
        assert r2.added_count == 0
        assert r2.duplicate_count == 1
        assert store.count() == 1


# ---------------------------------------------------------------------------
# Batch ingestion: ordering, aggregation, isolation, repeated batches
# ---------------------------------------------------------------------------

def _mk(mid, name):
    return Material(material_id=mid, filename=name, path=name, material_type=MaterialType.NOTE)


class TestBatchIngestion:
    def test_batch_reports_preserve_input_order(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor()})
        materials = [_mk("z1", "z1.txt"), _mk("a1", "a1.txt"), _mk("m1", "m1.txt")]
        batch = svc.ingest_many(materials)
        assert [r.material_id for r in batch.reports] == ["z1", "a1", "m1"]
        assert batch.total_materials == 3
        assert batch.succeeded == 3
        assert batch.total_added == 3
        assert store.count() == 3

    def test_batch_aggregates_counts(self):
        store = EvidenceStore()
        ext = FakeNoteExtractor(
            texts_by_id={
                "b1": ["line one", "line two"],
                "b2": ["only"],
            },
            fail_ids={"b3"},
            skip_ids={"b4"},
        )
        svc = EvidenceIngestionService(store, extractors={"note": ext})
        materials = [_mk(mid, mid + ".txt") for mid in ["b1", "b2", "b3", "b4"]]
        batch = svc.ingest_many(materials)
        assert batch.succeeded == 2
        assert batch.failed == 1
        assert batch.skipped == 1
        assert batch.total_added == 3
        assert batch.total_extracted == 3
        r_b1 = batch.report_for("b1")
        assert r_b1.added_count == 2
        r_b3 = batch.report_for("b3")
        assert r_b3.status == IngestionStatus.FAILED
        assert store.count() == 3

    def test_batch_report_for_unknown_id(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store)
        batch = svc.ingest_many([])
        assert batch.total_materials == 0
        assert batch.report_for("nobody") is None

    def test_batch_failure_isolation_keeps_good_materials(self):
        store = EvidenceStore()
        ext = FakeNoteExtractor(fail_ids={"boom"})
        svc = EvidenceIngestionService(store, extractors={"note": ext})
        good = _mk("good", "good.txt")
        bad = _mk("boom", "boom.txt")
        batch = svc.ingest_many([good, bad, good])
        assert [r.status for r in batch.reports] == [
            IngestionStatus.SUCCESS,
            IngestionStatus.FAILED,
            IngestionStatus.SUCCESS,
        ]
        # the third report is the re-ingestion of the same material
        assert batch.report_for("good").duplicate_count == 1
        assert store.count() == 1

    def test_repeated_batch_ingestion_is_idempotent(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor()})
        materials = [_mk("m" + str(i), "m" + str(i) + ".txt") for i in range(5)]
        first = svc.ingest_many(materials)
        second = svc.ingest_many(materials)
        assert first.total_added == 5
        assert second.total_added == 0
        assert second.total_duplicates == 5
        assert store.count() == 5

    def test_batch_with_non_material_entries_fails_structured(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor()})
        batch = svc.ingest_many([_mk("ok", "ok.txt"), "not-a-material", _mk("ok2", "ok2.txt")])
        assert len(batch.reports) == 3
        middle = batch.report_for(str("not-a-material"))
        assert middle.status == IngestionStatus.FAILED
        assert middle.errors[0].code == IngestionErrorCode.INTERNAL_ERROR.value
        assert batch.succeeded == 2

    def test_batch_mixed_supported_and_unsupported(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor()})
        note = _mk("n1", "n1.txt")
        unsupported = Material(material_id="u1", filename="u1.bin", path="u1.bin", material_type=MaterialType.TEXT)
        batch = svc.ingest_many([unsupported, note])
        assert batch.report_for("u1").status == IngestionStatus.SKIPPED
        assert batch.report_for("n1").status == IngestionStatus.SUCCESS
        assert batch.skipped == 1
        assert store.count() == 1

    def test_batch_ordering_within_dry_run(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store)
        materials = [_mk("m" + str(i), "m" + str(i) + ".txt") for i in range(3)]
        for m in materials:
            r = svc.ingest(m, dry_run=True)
            assert r.dry_run is True
            assert r.added_count == 0
        assert store.count() == 0

    def test_ingest_many_empty_list(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store)
        batch = svc.ingest_many([])
        assert batch.total_materials == 0
        assert batch.to_dict()["reports"] == []


# ---------------------------------------------------------------------------
# Idempotency, provenance preservation
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_single_repeated_ingestion_same_service(self, tmp_path):
        store = EvidenceStore()
        m = _note_material(str(tmp_path), "idem.txt", "stable paragraph\n", "midem")
        svc = EvidenceIngestionService(store)
        r1 = svc.ingest(m)
        r2 = svc.ingest(m)
        r3 = svc.ingest(m)
        assert (r1.added_count, r1.duplicate_count) == (1, 0)
        assert (r2.added_count, r2.duplicate_count) == (0, 1)
        assert (r3.added_count, r3.duplicate_count) == (0, 1)
        assert store.count() == 1
        assert r1.evidence_ids == r2.evidence_ids or len(r2.evidence_ids) == 0

    def test_repeated_ingestion_across_service_instances(self, tmp_path):
        store = EvidenceStore()
        m = _note_material(str(tmp_path), "cross.txt", "same body\n", "mcross")
        r1 = EvidenceIngestionService(store).ingest(m)
        r2 = EvidenceIngestionService(store).ingest(m)
        assert r1.added_count == 1
        assert r2.added_count == 0
        assert r2.duplicate_count == 1
        assert store.count() == 1

    def test_partial_retry_does_not_duplicate_added_items(self):
        class FlakyReject(ExtractorAdapter):
            source_type = "note"

            def extract(self, material):
                out = [
                    Evidence(
                        evidence_id="flaky-a",
                        content="keeps being added once",
                        source_reference=SourceReference(material_id=material.material_id),
                        evidence_type=EvidenceType.PERSONAL_NOTE,
                    )
                ]
                if not getattr(self, "_done", False):
                    self._done = True
                    out.append(
                        Evidence(
                            evidence_id="flaky-b",
                            content="   ",
                            source_reference=SourceReference(material_id=material.material_id),
                            evidence_type=EvidenceType.PERSONAL_NOTE,
                        )
                    )
                return out

        store = EvidenceStore()
        svc = EvidenceIngestionService(store, extractors={"note": FlakyReject()})
        m = Material(material_id="mflaky", filename="f.txt", path="f.txt", material_type=MaterialType.NOTE)
        first = svc.ingest(m)
        second = svc.ingest(m)
        assert first.status == IngestionStatus.PARTIAL
        assert first.added_count == 1
        assert second.status == IngestionStatus.SUCCESS
        assert second.added_count == 0
        assert second.duplicate_count == 1
        assert store.count() == 1

    def test_dry_run_never_affects_store(self, tmp_path):
        store = EvidenceStore()
        m = _note_material(str(tmp_path), "dry.txt", "dry body\n", "mdry")
        svc = EvidenceIngestionService(store)
        report = svc.ingest(m, dry_run=True)
        assert report.dry_run is True
        assert report.status == IngestionStatus.SUCCESS
        assert report.total_extracted >= 1
        assert report.added_count == 0
        assert store.count() == 0
        real = svc.ingest(m)
        assert real.dry_run is False
        assert real.added_count == report.total_extracted
        assert store.count() == report.total_extracted

    def test_uuid4_fallback_ids_are_rescaffolded_deterministically(self):
        import uuid

        class Uuid4Ids(ExtractorAdapter):
            source_type = "note"

            def extract(self, material):
                return [
                    Evidence(
                        content="body one",
                        source_reference=SourceReference(material_id=material.material_id),
                        evidence_type=EvidenceType.PERSONAL_NOTE,
                        metadata={},
                    )
                    for _ in range(1)
                ]

        store = EvidenceStore()
        svc = EvidenceIngestionService(store, extractors={"note": Uuid4Ids()})
        m = Material(material_id="muuid", filename="u.txt", path="u.txt", material_type=MaterialType.NOTE)
        r1 = svc.ingest(m)
        r2 = svc.ingest(m)
        assert len(r1.evidence_ids) == 1
        assert r1.evidence_ids[0].startswith("evid-")
        assert r2.duplicate_count == 1
        assert store.count() == 1
        ev = store.all()[0]
        assert ev.evidence_id.startswith("evid-")


# ---------------------------------------------------------------------------
# Provenance preservation through ingestion
# ---------------------------------------------------------------------------

class TestProvenance:
    def test_pdf_page_provenance_preserved(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store)
        m = Material(
            material_id="p1",
            filename="simple.pdf",
            path=str(FIX_DOCS / "simple.pdf"),
            material_type=MaterialType.SYLLABUS,
        )
        report = svc.ingest(m)
        assert report.status == IngestionStatus.SUCCESS
        assert store.count() == report.added_count
        assert store.count() >= 1
        for ev in store.all():
            assert ev.source_reference.material_id == "p1"
            assert ev.source_reference.page is not None
            assert ev.evidence_type.value in ("document", "extracted_fact")

    def test_docx_paragraph_and_table_provenance(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store)
        m = Material(
            material_id="d1",
            filename="tables.docx",
            path=str(FIX_DOCS / "tables.docx"),
            material_type=MaterialType.SYLLABUS,
        )
        report = svc.ingest(m)
        assert report.status == IngestionStatus.SUCCESS
        assert store.count() == report.added_count >= 1
        for ev in store.all():
            assert ev.source_reference.material_id == "d1"
            block_type = str(ev.metadata.get("block_type", "")).lower()
            if block_type == "table":
                assert ev.source_reference.location is not None
            else:
                assert ev.source_reference.location is not None
                assert ev.source_reference.paragraph is not None

    def test_audio_timestamps_preserved(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store)
        m = Material(
            material_id="a1",
            filename="long_silence_5s.wav",
            path=FIX_WAV,
            material_type=MaterialType.AUDIO,
        )
        report = svc.ingest(m)
        assert report.status == IngestionStatus.SUCCESS
        assert report.added_count == 1
        ev = store.all()[0]
        assert ev.evidence_type == EvidenceType.TRANSCRIPT
        assert ev.source_reference.timestamp_start == 0.4
        assert ev.source_reference.timestamp_end == 35.4
        assert ev.source_reference.material_id == "a1"
        assert ev.metadata.get("segment_count", 0) >= 1

    def test_audio_custom_segments_timestamps(self):
        from src.asr_provider import MockASRProvider
        from src.models import TranscriptLanguage

        provider = MockASRProvider(
            segments=[
                {"start": 1.5, "end": 4.0, "text": "primer", "confidence": 0.9},
                {"start": 4.0, "end": 6.25, "text": "segon", "confidence": 0.8},
            ],
            language="Spanish",
        )
        store = EvidenceStore()
        svc = EvidenceIngestionService(store, asr_provider=provider)
        m = Material(
            material_id="a2",
            filename="long_silence_5s.wav",
            path=FIX_WAV,
            material_type=MaterialType.AUDIO,
        )
        report = svc.ingest(m)
        assert report.status == IngestionStatus.SUCCESS
        ev = store.all()[0]
        assert ev.content == "primer\nsegon"
        assert ev.source_reference.timestamp_start == 1.5
        assert ev.source_reference.timestamp_end == 6.25
        assert ev.language == Language.SPANISH

    def test_ocr_bounding_box_metadata_preserved(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store)
        m = Material(
            material_id="img1",
            filename="board.png",
            path=str(Path(__file__).parent / "fixtures" / "board.png"),
            material_type=MaterialType.IMAGE,
        )
        Path(m.path).write_bytes(b"fake")
        report = svc.ingest(m)
        assert report.status == IngestionStatus.SUCCESS
        assert store.count() == report.added_count >= 1
        for ev in store.all():
            assert ev.evidence_type == EvidenceType.OCR
            bb = ev.metadata.get("bounding_box")
            assert isinstance(bb, dict)
            assert {"x", "y", "width", "height"} <= set(bb.keys())
            assert ev.metadata.get("ocr_confidence") is not None
            assert ev.source_reference.material_id == "img1"

    def test_note_line_and_paragraph_provenance(self, tmp_path):
        store = EvidenceStore()
        m = _note_material(
            str(tmp_path),
            "noted.txt",
            "primera linea\nsegunda linea\n\ntercera\n",
            "mnote",
        )
        svc = EvidenceIngestionService(store)
        report = svc.ingest(m)
        assert report.status == IngestionStatus.SUCCESS
        assert store.count() == 2
        (ev1, ev2) = store.all()
        assert ev1.content == "primera linea\nsegunda linea"
        assert ev1.source_reference.paragraph == "paragraph_0"
        assert ev1.source_reference.line == 1
        assert ev2.content == "tercera"
        assert ev2.source_reference.paragraph == "paragraph_1"
        assert ev2.source_reference.line == 4
        assert ev1.source_reference.material_id == "mnote"

    def test_cross_source_same_content_never_merged(self, tmp_path):
        store = EvidenceStore()
        m1 = _note_material(str(tmp_path), "s1.txt", "misma linea\n", "src1")
        m2 = _note_material(str(tmp_path), "s2.txt", "misma linea\n", "src2")
        svc = EvidenceIngestionService(store)
        assert svc.ingest(m1).added_count == 1
        assert svc.ingest(m2).added_count == 1
        assert store.count() == 2

    def test_cross_location_same_content_never_merged(self):
        store = EvidenceStore()

        class TwoPara(ExtractorAdapter):
            source_type = "note"

            def extract(self, material):
                return [
                    Evidence(
                        content="igual",
                        source_reference=SourceReference(
                            material_id=material.material_id, paragraph="P0"
                        ),
                        evidence_type=EvidenceType.PERSONAL_NOTE,
                        metadata={},
                    ),
                    Evidence(
                        content="igual",
                        source_reference=SourceReference(
                            material_id=material.material_id, paragraph="P1"
                        ),
                        evidence_type=EvidenceType.PERSONAL_NOTE,
                        metadata={},
                    ),
                ]

        svc = EvidenceIngestionService(store, extractors={"note": TwoPara()})
        m = Material(material_id="loc", filename="loc.txt", path="loc.txt", material_type=MaterialType.NOTE)
        report = svc.ingest(m)
        assert report.added_count == 2
        assert store.count() == 2


# ---------------------------------------------------------------------------
# Unicode round-trip, determinism, history, statistics, serialization
# ---------------------------------------------------------------------------

class TestUnicodeRoundTrip:
    def test_chinese_content_preserved(self, tmp_path):
        store = EvidenceStore()
        m = _note_material(
            str(tmp_path), "zh.txt", "函数是映射关系的定义。\n第二行：复习计划安排。\n\n第三段：考试日期与权重。\n", "mzh"
        )
        svc = EvidenceIngestionService(store)
        report = svc.ingest(m)
        assert report.added_count == 2
        contents = [ev.content for ev in store.all()]
        assert "函数是映射关系的定义。\n第二行：复习计划安排。" in contents
        assert "第三段：考试日期与权重。" in contents

    def test_spanish_catalan_accents_preserved(self, tmp_path):
        store = EvidenceStore()
        body = ("Funci\u00f3 i gr\u00e0fic: \u00e0rees, \u00e8rem, \u00eddem\n"
                "plano cartesiano y coordenadas\n\n"
                "\u00e0mbit: \u00e0lgebra")
        m = _note_material(str(tmp_path), "cat.txt", body, "mcat")
        svc = EvidenceIngestionService(store)
        report = svc.ingest(m)
        assert report.added_count == 2
        stored = [ev.content for ev in store.all()]
        assert "Funci\u00f3 i gr\u00e0fic: \u00e0rees, \u00e8rem, \u00eddem\nplano cartesiano y coordenadas" in stored
        assert "\u00e0mbit: \u00e0lgebra" in stored

    def test_mixed_language_newlines_preserved(self, tmp_path):
        store = EvidenceStore()
        body = ("\u4e2d\u6587 \u52a0\u6cf0\u8bed Catal\u00e0 es pa\u00f1ol\n"
                "segunda\n\n"
                "tercera: l'\u00e8xamen")
        m = _note_material(str(tmp_path), "mixed.txt", body, "mmix")
        svc = EvidenceIngestionService(store)
        report = svc.ingest(m)
        assert report.added_count == 2
        (e1, e2) = store.all()
        assert e1.content == "\u4e2d\u6587 \u52a0\u6cf0\u8bed Catal\u00e0 es pa\u00f1ol\nsegunda"
        assert e2.content == "tercera: l'\u00e8xamen"
        for ev in store.all():
            assert not ev.content.endswith("\n")

    def test_report_serialization_roundtrip(self):
        store = EvidenceStore()
        m = Material(material_id="mser", filename="s.txt", path="s.txt", material_type=MaterialType.NOTE)
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor(malformed_ids={"mser"})})
        report = svc.ingest(m)
        data = report.to_dict()
        restored = IngestionReport.from_dict(json.loads(json.dumps(data)))
        assert restored == report
        assert restored.to_dict() == data

    def test_batch_report_dict_roundtrip(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor()})
        batch = svc.ingest_many([_mk("b1", "b1.txt"), _mk("b2", "b2.txt")])
        data = json.loads(json.dumps(batch.to_dict(), ensure_ascii=False))
        assert data["reports"][0]["material_id"] == "b1"
        assert data["total_materials"] == 2
        assert data["total_added"] == 2

    def test_report_unicode_json_no_escaping(self, tmp_path):
        store = EvidenceStore()
        body = "funci\u00f3 \u00e0rea \u4e2d\u6587\n"
        svc = EvidenceIngestionService(
            store,
            extractors={"note": FakeNoteExtractor(texts_by_id={"muni": [body.rstrip()]})}
        )
        m = _note_material(str(tmp_path), "uni.txt", body, "muni")
        report = svc.ingest(m)
        assert report.added_count == 1
        stored = store.all()[0]
        assert stored.content == "funci\u00f3 \u00e0rea \u4e2d\u6587"
        blob = json.dumps(svc.to_dict(), ensure_ascii=False)
        assert blob == json.dumps(json.loads(blob), ensure_ascii=False)


class TestDeterminism:
    def test_two_services_same_input_same_reports(self, tmp_path):
        store_a = EvidenceStore()
        store_b = EvidenceStore()
        svc_a = EvidenceIngestionService(store_a, extractors={"note": FakeNoteExtractor()})
        svc_b = EvidenceIngestionService(store_b, extractors={"note": FakeNoteExtractor()})
        m_a = _mk("mdet", "det_a.txt")
        m_b = _mk("mdet", "det_b.txt")
        r_a = svc_a.ingest(m_a)
        r_b = svc_b.ingest(m_b)
        assert r_a.status == r_b.status
        assert r_a.evidence_ids == r_b.evidence_ids
        assert store_a.count() == store_b.count() == 1


    def test_report_summary_is_derived_text(self):
        store = EvidenceStore()
        m = Material(material_id="msum", filename="sum.txt", path="sum.txt", material_type=MaterialType.NOTE)
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor()} )
        report = svc.ingest(m)
        summary = report.summary()
        assert "extracted" in summary
        assert "added" in summary

    def test_unknown_material_id_in_history_is_empty(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store)
        assert svc.history_for("never-seen") == ()
        assert svc.status_for("never-seen") is None
        assert svc.report_for("never-seen") is None

    def test_error_code_retryable_flags(self):
        assert IngestionErrorCode.ASR_ERROR.retryable is True
        assert IngestionErrorCode.STORE_REJECTION.retryable is True
        assert IngestionErrorCode.EXTRACTOR_ERROR.retryable is False
        assert IngestionErrorCode.UNSUPPORTED_SOURCE.retryable is False
        assert IngestionErrorCode.MALFORMED_EVIDENCE.retryable is False
        assert IngestionErrorCode.INTERNAL_ERROR.retryable is False


class TestHistoryAndStatistics:
    def test_history_records_attempts(self):
        store = EvidenceStore()
        m = Material(material_id="mhist", filename="h.txt", path="h.txt", material_type=MaterialType.NOTE)
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor()})
        svc.ingest(m)
        svc.ingest(m)
        entries = svc.history_for("mhist")
        assert len(entries) == 2
        assert entries[0].attempt_count == 1
        assert entries[1].attempt_count == 2
        assert entries[0].status == IngestionStatus.SUCCESS
        blob = json.loads(json.dumps(svc.history().to_dict(), ensure_ascii=False))
        assert blob["schema_version"] == 1
        assert any(e["material_id"] == "mhist" for e in blob["entries"])

    def test_status_for_tracks_latest_attempt(self):
        store = EvidenceStore()
        m = Material(material_id="ms", filename="s.txt", path="s.txt", material_type=MaterialType.NOTE)
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor(skip_ids={"ms"})})
        first = svc.ingest(m)
        assert first.status == IngestionStatus.SKIPPED
        assert svc.status_for("ms") == IngestionStatus.SKIPPED
        svc._extractors["note"] = FakeNoteExtractor()
        second = svc.ingest(m)
        assert second.status == IngestionStatus.SUCCESS
        assert svc.status_for("ms") == IngestionStatus.SUCCESS

    def test_statistics_derived_from_all_reports(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor(fail_ids={"bad"}, skip_ids={"skip"})})
        svc.ingest_many([_mk("bad", "b.txt"), _mk("skip", "s.txt"), _mk("good", "g.txt")])
        stats = svc.statistics()
        assert stats.total_materials == 3
        assert stats.successful_materials == 1
        assert stats.failed_materials == 1
        assert stats.skipped_materials == 1
        assert stats.total_added == 1
        assert stats.to_dict()["total_errors"] == 2
        blob = json.loads(json.dumps(svc.to_dict(), ensure_ascii=False))
        assert blob["statistics"]["total_materials"] == 3

    def test_malformed_items_reported_not_silent(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor(malformed_ids={"mmal"})})
        m = Material(material_id="mmal", filename="mal.txt", path="mal.txt", material_type=MaterialType.NOTE)
        report = svc.ingest(m)
        assert report.status == IngestionStatus.PARTIAL
        assert report.added_count == 1
        assert report.total_extracted == 2
        codes = [e.code for e in report.errors]
        assert IngestionErrorCode.MALFORMED_EVIDENCE.value in codes
        assert store.count() == 1

    def test_service_requires_store_injection(self):
        try:
            EvidenceIngestionService(None)
            raise AssertionError("expected ValueError for None store")
        except ValueError:
            pass

    def test_history_entry_serialization_roundtrip(self):
        store = EvidenceStore()
        m = Material(material_id="mhe", filename="he.txt", path="he.txt", material_type=MaterialType.NOTE)
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor()})
        report = svc.ingest(m)
        entry = svc.history().record(report)
        data = json.loads(json.dumps(entry.to_dict(), ensure_ascii=False))
        assert data["material_id"] == "mhe"
        assert data["status"] == "SUCCESS"
        assert data["attempt_count"] == 2
        assert svc.history_for("mhe")[0].attempt_count == 1


# ---------------------------------------------------------------------------
# Session ingestion
# ---------------------------------------------------------------------------

class TestSessionIngestion:
    def test_session_tagging_stamps_metadata_only_when_absent(self, tmp_path):
        store = EvidenceStore()

        class TaggedNote(ExtractorAdapter):
            source_type = "note"

            def extract(self, material):
                ev = Evidence(
                    content=material.material_id + " body",
                    source_reference=SourceReference(material_id=material.material_id),
                    evidence_type=EvidenceType.PERSONAL_NOTE,
                    metadata={},
                )
                ev.evidence_id = "tagged-" + material.material_id
                return [ev]

        svc = EvidenceIngestionService(store, extractors={"note": TaggedNote()})
        m1 = _note_material(str(tmp_path), "s1.txt", "x", "ms1")
        m2 = _note_material(str(tmp_path), "s2.txt", "y", "ms2")
        session = ClassSession(course_id="FUNC", session_number=3, material_refs=["ms1", "ms2"])
        batch = svc.ingest_session(session, [m2, m1])
        assert batch.succeeded == 2
        for ev in store.all():
            assert ev.metadata.get("class_session_id") == session.session_id
        # shared state visible on the primary service
        assert svc.report_for("ms1") is not None
        assert svc.report_for("ms2") is not None

    def test_session_idempotent_reingestion(self, tmp_path):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store)
        m1 = _note_material(str(tmp_path), "s1.txt", "line one\n", "sma")
        m2 = _note_material(str(tmp_path), "s2.txt", "line two\n", "smb")
        session = ClassSession(course_id="CALC", session_number=1, material_refs=["sma", "smb"])
        first = svc.ingest_session(session, [m1, m2])
        second = svc.ingest_session(session, [m1, m2])
        assert first.total_added == 2
        assert second.total_added == 0
        assert second.total_duplicates == 2
        assert store.count() == 2

    def test_session_deterministic_ordering(self, tmp_path):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor()})
        materials = [
            _note_material(str(tmp_path), "zz.txt", "z", "zid"),
            _note_material(str(tmp_path), "aa.txt", "a", "aid"),
            _note_material(str(tmp_path), "mm.txt", "m", "mid"),
        ]
        session = ClassSession(course_id="ORD", session_number=2, material_refs=[m.material_id for m in materials])
        batch = svc.ingest_session(session, materials)
        ids = [r.material_id for r in batch.reports]
        assert ids == sorted(ids)

    def test_session_shares_history_with_primary_service(self, tmp_path):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store)
        m = _note_material(str(tmp_path), "h.txt", "h body\n", "msess")
        session = ClassSession(course_id="HIST", session_number=9, material_refs=["msess"])
        svc.ingest_session(session, [m])
        svc.ingest_session(session, [m])
        assert len(svc.history_for("msess")) == 2
        assert svc.history_for("msess")[1].attempt_count == 2


# ---------------------------------------------------------------------------
# Store-injection independence
# ---------------------------------------------------------------------------

class TestStoreIndependence:
    def test_separate_stores_do_not_share_evidence(self, tmp_path):
        store_a = EvidenceStore()
        store_b = EvidenceStore()
        svc_a = EvidenceIngestionService(store_a)
        svc_b = EvidenceIngestionService(store_b)
        m_a = _note_material(str(tmp_path), "a.txt", "solo a\n", "soloa")
        svc_a.ingest(m_a)
        m_b = _note_material(str(tmp_path) + "_b", "b.txt", "solo b\n", "solob")
        svc_b.ingest(m_b)
        assert store_a.count() == 1
        assert store_b.count() == 1
        contents_a = [ev.content for ev in store_a.all()]
        assert "solo b" not in contents_a

    def test_service_and_store_share_single_evidence(self, tmp_path):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store)
        assert svc.store is store
        m = _note_material(str(tmp_path), "shared.txt", "shared body\n", "mshared")
        svc.ingest(m)
        assert store.count() == 1
        assert store.all()[0].content == "shared body"

    def test_store_snapshot_roundtrip_after_ingestion(self, tmp_path):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store)
        m = _note_material(str(tmp_path), "snap.txt", "snapshot body\n", "msnap")
        svc.ingest(m)
        data = store.to_dict()
        import tempfile, os
        path = os.path.join(str(tmp_path), "store.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        restored = EvidenceStore.from_dict(json.load(open(path, encoding="utf-8")))
        assert restored.count() == store.count()
        assert [ev.content for ev in restored.all()] == [ev.content for ev in store.all()]


# ---------------------------------------------------------------------------
# Thread safety and performance
# ---------------------------------------------------------------------------

class TestConcurrencyAndPerformance:
    def test_concurrent_ingestion_same_material_no_dupes(self, tmp_path):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor()})
        m = _note_material(str(tmp_path), "conc.txt", "conc body\n", "mconc")
        errors = []

        def worker():
            try:
                svc.ingest(m)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert store.count() == 1
        statuses = [e.status for e in svc.history_for("mconc")]
        assert statuses.count(IngestionStatus.SUCCESS) == 10

    def test_ingest_many_concurrent_threads_distinct_materials(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor()})
        materials = [_mk("c%d" % i, "c%d.txt" % i) for i in range(50)]
        errors = []

        def worker(chunk):
            try:
                svc.ingest_many(chunk)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(materials[i * 10:(i + 1) * 10],))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert store.count() == 50

    def test_1000_material_batch_ingestion(self):
        import time

        store = EvidenceStore()
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor()})
        materials = [_mk("perf-%d" % i, "perf-%d.txt" % i) for i in range(1000)]
        started = time.perf_counter()
        batch = svc.ingest_many(materials)
        elapsed = time.perf_counter() - started
        assert batch.total_added == 1000
        assert store.count() == 1000
        assert elapsed < 60.0
        # repeat: no growth, still fast
        started = time.perf_counter()
        repeat = svc.ingest_many(materials)
        elapsed_repeat = time.perf_counter() - started
        assert repeat.total_added == 0
        assert repeat.total_duplicates == 1000
        assert store.count() == 1000
        assert elapsed_repeat < 60.0

    def test_repeated_ingestion_no_growth(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor()})
        materials = [_mk("rg%d" % i, "rg%d.txt" % i) for i in range(200)]
        for _ in range(5):
            batch = svc.ingest_many(materials)
        assert store.count() == 200
        assert batch.total_added == 0
        assert batch.total_duplicates == 200


# ---------------------------------------------------------------------------
# Real end-to-end chains (fixtures)
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_note_real_file_end_to_end(self):
        store = EvidenceStore()
        m = Material(
            material_id="e2e-note",
            filename="example.txt",
            path=str(FIX_NOTES / "example.txt"),
            material_type=MaterialType.NOTE,
        )
        svc = EvidenceIngestionService(store)
        report = svc.ingest(m)
        assert report.status == IngestionStatus.SUCCESS
        assert report.added_count >= 1
        assert store.count() == report.added_count
        repeat = svc.ingest(m)
        assert repeat.added_count == 0
        assert repeat.duplicate_count == report.added_count

    def test_audio_real_wav_end_to_end(self):
        store = EvidenceStore()
        m = Material(
            material_id="e2e-audio",
            filename="long_silence_5s.wav",
            path=FIX_WAV,
            material_type=MaterialType.AUDIO,
        )
        svc = EvidenceIngestionService(store)
        report = svc.ingest(m)
        assert report.status == IngestionStatus.SUCCESS
        assert report.added_count == 1
        ev = store.all()[0]
        assert ev.evidence_type == EvidenceType.TRANSCRIPT
        assert ev.source_reference.material_id == "e2e-audio"
        repeat = svc.ingest(m)
        assert repeat.duplicate_count == 1
        assert store.count() == 1

    def test_audio_missing_file_is_structured_skip(self):
        store = EvidenceStore()
        m = Material(
            material_id="e2e-audio-missing",
            filename="does_not_exist.wav",
            path=str(FIX_DOCS / "does_not_exist.wav"),
            material_type=MaterialType.AUDIO,
        )
        svc = EvidenceIngestionService(store)
        report = svc.ingest(m)
        assert report.status == IngestionStatus.SKIPPED
        assert report.errors[0].code == IngestionErrorCode.EXTRACTOR_ERROR.value
        assert store.count() == 0

    def test_ocr_image_end_to_end(self, tmp_path):
        store = EvidenceStore()
        img_path = str(tmp_path / "board-e2e.png")
        with open(img_path, "wb") as fh:
            fh.write(b"fake-image-bytes")
        m = Material(
            material_id="e2e-ocr",
            filename="board-e2e.png",
            path=img_path,
            material_type=MaterialType.IMAGE,
        )
        svc = EvidenceIngestionService(store)
        report = svc.ingest(m)
        assert report.status == IngestionStatus.SUCCESS
        assert report.added_count >= 1
        assert all(ev.evidence_type == EvidenceType.OCR for ev in store.all())
        repeat = svc.ingest(m)
        assert repeat.added_count == 0
        assert repeat.duplicate_count == report.added_count

    def test_pdf_real_file_end_to_end(self):
        store = EvidenceStore()
        m = Material(
            material_id="e2e-pdf",
            filename="simple.pdf",
            path=str(FIX_DOCS / "simple.pdf"),
            material_type=MaterialType.SYLLABUS,
        )
        svc = EvidenceIngestionService(store)
        first = svc.ingest(m)
        assert first.status == IngestionStatus.SUCCESS
        assert first.added_count >= 1
        second = svc.ingest(m)
        assert second.added_count == 0
        assert second.duplicate_count == first.added_count
        assert store.count() == first.added_count

    def test_docx_real_file_end_to_end(self):
        store = EvidenceStore()
        m = Material(
            material_id="e2e-docx",
            filename="tables.docx",
            path=str(FIX_DOCS / "tables.docx"),
            material_type=MaterialType.SYLLABUS,
        )
        svc = EvidenceIngestionService(store)
        first = svc.ingest(m)
        assert first.status == IngestionStatus.SUCCESS
        assert first.added_count >= 1
        second = svc.ingest(m)
        assert second.duplicate_count == first.added_count
        assert store.count() == first.added_count

    def test_pdf_docx_note_into_one_store(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store)
        materials = [
            Material(material_id="combo-note", filename="example.txt", path=str(FIX_NOTES / "example.txt"), material_type=MaterialType.NOTE),
            Material(material_id="combo-pdf", filename="simple.pdf", path=str(FIX_DOCS / "simple.pdf"), material_type=MaterialType.SYLLABUS),
            Material(material_id="combo-docx", filename="simple.docx", path=str(FIX_DOCS / "simple.docx"), material_type=MaterialType.SYLLABUS),
            Material(material_id="combo-audio", filename="long_silence_5s.wav", path=FIX_WAV, material_type=MaterialType.AUDIO),
        ]
        batch = svc.ingest_many(materials)
        assert batch.failed == 0
        assert store.count() == batch.total_added
        by_source = {}
        for ev in store.all():
            key = ev.source_reference.material_id
            by_source[key] = by_source.get(key, 0) + 1
        assert len(by_source) == 4

    def test_unsupported_source_stays_skipped_in_batch(self):
        store = EvidenceStore()
        svc = EvidenceIngestionService(store, extractors={"note": FakeNoteExtractor()})
        good = Material(material_id="sup-good", filename="ok.txt", path="ok.txt", material_type=MaterialType.NOTE)
        bad = Material(material_id="sup-bad", filename="bin.dat", path="bin.dat", material_type=MaterialType.TEXT)
        batch = svc.ingest_many([bad, good, bad])
        assert batch.report_for("sup-bad").status == IngestionStatus.SKIPPED
        assert batch.report_for("sup-good").status == IngestionStatus.SUCCESS
        assert batch.failed == 0
        assert store.count() == 1

    def test_markdown_note_real_file_end_to_end(self):
        store = EvidenceStore()
        m = Material(
            material_id="e2e-md",
            filename="catalan.md",
            path=str(FIX_NOTES / "catalan.md"),
            material_type=MaterialType.NOTE,
        )
        svc = EvidenceIngestionService(store)
        report = svc.ingest(m)
        assert report.status == IngestionStatus.SUCCESS
        assert report.added_count >= 1
        repeat = svc.ingest(m)
        assert repeat.duplicate_count == report.added_count
