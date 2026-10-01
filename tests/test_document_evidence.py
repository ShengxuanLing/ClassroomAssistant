"""Task 22 - Document -> Evidence extraction & traceability tests.

Covers:
- PDF / DOCX blocks -> Evidence
- non-empty -> Evidence, empty -> skip
- PARSED_EMPTY / FAILED -> no Evidence
- document identity / location / type traceability
- deterministic / idempotent Evidence identity
- exact-text preservation (no translation / summary / mutation)
- ParsedDocument serialization round-trip
- Evidence serialization round-trip
- immutability (no ParsedDocument mutation)
- deduplication (exact same block, cross-document, cross-page)
- ordering (document order, not text/hash/length order)
- compatibility with existing Evidence collection
"""

import os
import copy
import hashlib
import tempfile
from pathlib import Path

import pytest

from src.models import (
    Evidence,
    EvidenceType,
    Language,
    Material,
    MaterialType,
    SourceReference,
)
from src.document_input import (
    DocumentBlock,
    DocumentBlockType,
    DocumentParserErrorCode,
    DocumentStatus,
    ParsedDocument,
    ParserError,
    PDFDocumentParser,
    DOCXDocumentParser,
    DocumentMaterialValidator,
    parse_document,
)
from src.document_evidence import (
    DocumentEvidenceExtractor,
    DocumentEvidenceStatus,
    DocumentEvidenceResult,
    block_evidence_id,
    extract_document_evidence,
)
from src.evidence_extractor import EvidenceExtractor

FIX = Path(__file__).parent / "fixtures" / "documents"


def _parsed(
    doc_id,
    doc_type="PDF",
    status=DocumentStatus.PARSED,
    blocks=(),
    material_id="",
    errors=(),
    path="",
):
    return ParsedDocument(
        document_id=doc_id,
        document_type=doc_type,
        path=path or "test.pdf",
        material_id=material_id,
        status=status,
        blocks=list(blocks),
        errors=tuple(errors),
    )


def _block(
    text,
    btype=DocumentBlockType.TEXT,
    page=None,
    block_index=None,
    para=None,
    table_index=None,
    location="",
    bid="",
):
    md = {}
    if table_index is not None:
        md["table_index"] = table_index
    b = DocumentBlock(
        block_id=bid,
        block_type=btype,
        text=text,
        page_number=page,
        block_index=block_index,
        paragraph_index=para,
        location=location,
        metadata=md,
    )
    return b


EXT = DocumentEvidenceExtractor()


class TestBasicMapping:
    def test_pdf_page_block_to_evidence(self):
        doc = _parsed(
            "document-pdf0000001",
            "PDF",
            material_id="mat-1",
            blocks=[_block("hola mundo", page=3, block_index=0)],
        )
        evs = EXT.extract(doc)
        assert len(evs) == 1
        e = evs[0]
        assert e.content == "hola mundo"
        assert e.evidence_type is EvidenceType.DOCUMENT
        assert e.source_reference.page == 3
        assert e.source_reference.material_id == "mat-1"
        assert e.metadata["document_type"] == "PDF"
        assert e.metadata["block_type"] == "TEXT"

    def test_docx_paragraph_to_evidence(self):
        doc = _parsed(
            "document-docx00001",
            "DOCX",
            material_id="mat-2",
            blocks=[_block("Un sistema estable.", para=18)],
        )
        evs = EXT.extract(doc)
        assert len(evs) == 1
        e = evs[0]
        assert e.content == "Un sistema estable."
        assert e.metadata["document_type"] == "DOCX"
        assert e.source_reference.paragraph == "paragraph_18"

    def test_heading_block_generates_evidence(self):
        doc = _parsed(
            "document-head00001",
            "DOCX",
            blocks=[_block("Capitulo 1", btype=DocumentBlockType.HEADING, para=0)],
        )
        evs = EXT.extract(doc)
        assert len(evs) == 1
        assert evs[0].content == "Capitulo 1"
        assert evs[0].metadata["block_type"] == "HEADING"

    def test_table_block_generates_evidence(self):
        doc = _parsed(
            "document-tbl000001",
            "DOCX",
            blocks=[
                _block("Name | Value", para=0),
                _block("A || 10 | B || 20", btype=DocumentBlockType.TABLE, table_index=0),
            ],
        )
        evs = EXT.extract(doc)
        assert len(evs) == 2
        table_ev = [e for e in evs if e.metadata["block_type"] == "TABLE"]
        assert len(table_ev) == 1
        assert table_ev[0].content == "A || 10 | B || 20"
        assert table_ev[0].source_reference.location == "table"
        assert table_ev[0].metadata["location"] == "docx-table-0"

    def test_empty_document_returns_empty_list(self):
        doc = _parsed(
            "document-empty00001",
            "PDF",
            status=DocumentStatus.PARSED_EMPTY,
            blocks=[],
        )
        assert EXT.extract(doc) == []

    def test_failed_document_returns_empty_list(self):
        doc = _parsed(
            "document-fail00001",
            "PDF",
            status=DocumentStatus.FAILED,
            errors=(
                ParserError(DocumentParserErrorCode.PARSER_ERROR, "corrupt"),
            ),
        )
        assert EXT.extract(doc) == []

    def test_whitespace_only_block_skipped(self):
        doc = _parsed(
            "document-ws00000001",
            "PDF",
            blocks=[
                _block("   ", page=1, block_index=0),
                _block("real content", page=2, block_index=0),
            ],
        )
        evs = EXT.extract(doc)
        assert len(evs) == 1
        assert evs[0].content == "real content"

    def test_statistics_record_skipped_empty_blocks(self):
        doc = _parsed(
            "document-stats0001",
            "PDF",
            blocks=[
                _block("", page=1, block_index=0),
                _block("text", page=2, block_index=0),
            ],
        )
        res = EXT.extract_as_result(doc)
        assert res.statistics["skipped_empty_blocks"] == 1
        assert res.statistics["evidences_generated"] == 1
        assert res.status is DocumentEvidenceStatus.EXTRACTED


class TestTraceability:
    def test_pdf_page_traceability(self):
        doc = _parsed(
            "document-trace001",
            "PDF",
            material_id="mat-pdf",
            blocks=[_block("page text", page=7, block_index=0)],
        )
        e = EXT.extract(doc)[0]
        assert e.source_reference.page == 7
        assert e.source_reference.location == "pdf-page-7-block-0"
        assert e.metadata["document_id"] == "document-trace001"
        assert e.metadata["location"] == "pdf-page-7-block-0"

    def test_docx_paragraph_traceability(self):
        doc = _parsed(
            "document-trace002",
            "DOCX",
            material_id="mat-docx",
            blocks=[_block("para text", para=42)],
        )
        e = EXT.extract(doc)[0]
        assert e.source_reference.paragraph == "paragraph_42"
        assert e.source_reference.location == "docx-paragraph-42"

    def test_source_reference_roundtrip_preserves_location(self):
        doc = _parsed(
            "document-trace003",
            "DOCX",
            material_id="m",
            blocks=[
                _block("a", btype=DocumentBlockType.HEADING, para=0),
                _block("b", btype=DocumentBlockType.TABLE, table_index=3),
            ],
        )
        evs = EXT.extract(doc)
        for e in evs:
            sr = SourceReference.from_dict(e.source_reference.to_dict())
            assert sr == e.source_reference

    def test_material_id_falls_back_to_document_id(self):
        doc = _parsed("document-trace004", "PDF", material_id="", blocks=[_block("t", page=1, block_index=0)])
        e = EXT.extract(doc)[0]
        assert e.source_reference.material_id == "document-trace004"


class TestDeterministicIdentity:
    def test_id_is_deterministic(self):
        a = block_evidence_id("document-x", "PDF", "TEXT", "page:1", "text")
        b = block_evidence_id("document-x", "PDF", "TEXT", "page:1", "text")
        assert a == b

    def test_id_differs_by_document_type(self):
        a = block_evidence_id("document-x", "PDF", "TEXT", "page:1", "text")
        b = block_evidence_id("document-x", "DOCX", "TEXT", "page:1", "text")
        assert a != b

    def test_id_differs_by_text(self):
        a = block_evidence_id("document-x", "PDF", "TEXT", "page:1", "text")
        b = block_evidence_id("document-x", "PDF", "TEXT", "page:1", "other")
        assert a != b

    def test_id_differs_by_location(self):
        a = block_evidence_id("document-x", "PDF", "TEXT", "page:1", "text")
        b = block_evidence_id("document-x", "PDF", "TEXT", "page:2", "text")
        assert a != b

    def test_no_uuid4_in_evidence_id(self):
        doc = _parsed("document-uuid0001", "PDF", blocks=[_block("t", page=1, block_index=0)])
        e = EXT.extract(doc)[0]
        assert e.evidence_id.startswith("doc-evidence-")
        assert len(e.evidence_id) == len("doc-evidence-") + 24

    def test_same_fixture_two_parses_same_ids(self):
        doc1 = parse_document(str(FIX / "simple.pdf"))
        doc2 = parse_document(str(FIX / "simple.pdf"))
        ids1 = [e.evidence_id for e in EXT.extract(doc1)]
        ids2 = [e.evidence_id for e in EXT.extract(doc2)]
        assert ids1 == ids2


class TestIdempotency:
    def test_extract_twice_gives_same_result(self):
        doc = _parsed("document-idem0001", "PDF", blocks=[_block("a", page=1, block_index=0), _block("b", page=2, block_index=0)])
        first = EXT.extract(doc)
        second = EXT.extract(doc)
        assert [e.evidence_id for e in first] == [e.evidence_id for e in second]
        assert first == second

    def test_repeated_block_deduplicated(self):
        doc = _parsed(
            "document-idem0002",
            "PDF",
            blocks=[
                _block("dup", page=1, block_index=0),
                _block("dup", page=1, block_index=0),
            ],
        )
        evs = EXT.extract(doc)
        assert len(evs) == 1


class TestDeduplication:
    def test_cross_page_same_text_kept_distinct(self):
        doc = _parsed(
            "document-dup0000001",
            "PDF",
            blocks=[
                _block("Definition", page=1, block_index=0),
                _block("Definition", page=2, block_index=0),
            ],
        )
        evs = EXT.extract(doc)
        assert len(evs) == 2
        assert evs[0].evidence_id != evs[1].evidence_id

    def test_cross_document_same_text_kept_distinct(self):
        d1 = _parsed("document-cross001", "PDF", material_id="m1", blocks=[_block("Hello", page=1, block_index=0)])
        d2 = _parsed("document-cross002", "PDF", material_id="m2", blocks=[_block("Hello", page=1, block_index=0)])
        e1 = EXT.extract(d1)[0]
        e2 = EXT.extract(d2)[0]
        assert e1.evidence_id != e2.evidence_id
        assert e1.content == e2.content == "Hello"

    def test_batch_same_block_not_duplicated(self):
        doc = _parsed(
            "document-batch0001",
            "PDF",
            blocks=[
                _block("A", page=1, block_index=0, bid="x"),
                _block("B", page=2, block_index=0, bid="y"),
                _block("A", page=1, block_index=0, bid="x"),
            ],
        )
        # Two blocks share the exact same (doc_id, location, text) -> identical
        # evidence identity; only two distinct evidences are emitted.
        evs = EXT.extract(doc)
        assert len(evs) == 2

    def test_similar_texts_not_deduplicated(self):
        doc = _parsed(
            "document-nosim0001",
            "PDF",
            blocks=[
                _block("The system is stable.", page=1, block_index=0),
                _block("The system remains stable.", page=2, block_index=0),
            ],
        )
        assert len(EXT.extract(doc)) == 2


class TestExactTextPreservation:
    def test_no_translation_spanish(self):
        doc = _parsed("document-span00001", "PDF", blocks=[_block("El sistema es estable.", page=1, block_index=0)])
        assert EXT.extract(doc)[0].content == "El sistema es estable."

    def test_no_summary_long_paragraph(self):
        long = "Parrafo largo de introduccion a la teoria de sistemas. " * 20
        doc = _parsed("document-sum00001", "DOCX", blocks=[_block(long, para=0)])
        assert EXT.extract(doc)[0].content == long

    def test_unicode_preserved(self):
        txt = "áéíóúñçàè中文系统稳定性"
        doc = _parsed("document-uni000001", "PDF", blocks=[_block(txt, page=1, block_index=0)])
        assert EXT.extract(doc)[0].content == txt

    def test_multilingual_catalan_spanish_chinese(self):
        doc = _parsed(
            "document-multi0001",
            "DOCX",
            blocks=[
                _block("Introducció", para=0),
                _block("Introducción", para=1),
                _block("系统稳定性", para=2),
            ],
        )
        evs = EXT.extract(doc)
        assert [e.content for e in evs] == ["Introducció", "Introducción", "系统稳定性"]


class TestOrdering:
    def test_pdf_order_preserved(self):
        doc = _parsed(
            "document-order0001",
            "PDF",
            blocks=[
                _block("zeta", page=1, block_index=0),
                _block("alpha", page=2, block_index=0),
                _block("mid", page=3, block_index=0),
            ],
        )
        evs = EXT.extract(doc)
        assert [e.content for e in evs] == ["zeta", "alpha", "mid"]

    def test_docx_order_preserved(self):
        doc = _parsed(
            "document-order0002",
            "DOCX",
            blocks=[
                _block("p1", para=0),
                _block("p2", para=1),
                _block("p3", para=2),
            ],
        )
        assert [e.content for e in EXT.extract(doc)] == ["p1", "p2", "p3"]


class TestSerialization:
    def test_parsed_document_serialization_roundtrip(self):
        doc = _parsed("document-ser00001", "PDF", material_id="m",
                       blocks=[_block("text", page=5, block_index=0)])
        evs_orig = EXT.extract(doc)
        doc_rt = ParsedDocument.from_dict(doc.to_dict())
        evs_rt = EXT.extract(doc_rt)
        assert [e.evidence_id for e in evs_orig] == [e.evidence_id for e in evs_rt]
        assert [e.content for e in evs_orig] == [e.content for e in evs_rt]

    def test_extract_accepts_dict_input(self):
        doc = _parsed("document-ser00002", "DOCX", blocks=[_block("x", para=0)])
        assert EXT.extract(doc.to_dict()) == EXT.extract(doc)

    def test_evidence_serialization_roundtrip(self):
        doc = _parsed("document-ser00003", "PDF", material_id="m",
                       blocks=[_block("y", page=2, block_index=0)])
        e = EXT.extract(doc)[0]
        e_rt = Evidence.from_dict(e.to_dict())
        assert e_rt.content == e.content
        assert e_rt.evidence_id == e.evidence_id
        assert e_rt.source_reference.page == e.source_reference.page
        assert e_rt.metadata == e.metadata
        assert e_rt.evidence_type is EvidenceType.DOCUMENT

    def test_result_serialization_roundtrip(self):
        doc = _parsed("document-ser00004", "PDF", blocks=[_block("z", page=1, block_index=0)])
        res = EXT.extract_as_result(doc)
        res_rt = DocumentEvidenceResult.from_dict(res.to_dict())
        assert [e.evidence_id for e in res_rt.evidences] == [e.evidence_id for e in res.evidences]
        assert res_rt.status == res.status
        assert res_rt.statistics == res.statistics


class TestImmutability:
    def test_extract_does_not_mutate_parsed_document(self):
        doc = _parsed("document-imm00001", "PDF", material_id="m",
                       blocks=[_block("t", page=1, block_index=0)])
        before = copy.deepcopy(doc)
        EXT.extract(doc)
        after = copy.deepcopy(doc)
        assert before.blocks == after.blocks
        assert before.document_id == after.document_id
        assert before.metadata == after.metadata

    def test_extract_does_not_mutate_blocks(self):
        doc = _parsed("document-imm00002", "DOCX", blocks=[_block("t", para=0)])
        block_before = copy.deepcopy(doc.blocks[0])
        EXT.extract(doc)
        assert doc.blocks[0] == block_before


class TestInputValidation:
    def test_none_input_returns_empty(self):
        assert EXT.extract(None) == []

    def test_wrong_type_returns_structured_failure(self):
        res = EXT.extract_as_result("not a document")
        assert res.status is DocumentEvidenceStatus.INVALID_INPUT
        assert res.evidences == []

    def test_module_level_none_input(self):
        assert extract_document_evidence(None) == []

    def test_failed_via_dict_input(self):
        doc = _parsed("document-fail001", "PDF", status=DocumentStatus.FAILED)
        res = EXT.extract_as_result(doc.to_dict())
        assert res.status is DocumentEvidenceStatus.NO_EVIDENCE
        assert res.evidences == []


class TestPDFIntegration:
    def test_simple_pdf_blocks_to_evidence(self):
        doc = parse_document(str(FIX / "simple.pdf"))
        assert doc.status is DocumentStatus.PARSED
        evs = EXT.extract(doc)
        assert len(evs) == 2
        assert all(e.evidence_type is EvidenceType.DOCUMENT for e in evs)
        assert all(e.metadata["document_type"] == "PDF" for e in evs)
        assert [e.source_reference.page for e in evs] == [1, 2]

    def test_pdf_unicode_preserved(self):
        doc = parse_document(str(FIX / "multilingual.pdf"))
        evs = EXT.extract(doc)
        assert len(evs) == 2
        assert all(e.content for e in evs)

    def test_scanned_pdf_no_evidence(self):
        doc = parse_document(str(FIX / "scanned_like.pdf"))
        assert doc.status is DocumentStatus.PARSED_EMPTY
        assert EXT.extract(doc) == []

    def test_empty_pdf_no_evidence(self):
        doc = parse_document(str(FIX / "empty.pdf"))
        assert doc.status is DocumentStatus.PARSED_EMPTY
        assert EXT.extract(doc) == []


class TestDOCXIntegration:
    def test_simple_docx_paragraphs_to_evidence(self):
        doc = parse_document(str(FIX / "simple.docx"))
        evs = EXT.extract(doc)
        assert all(e.metadata["document_type"] == "DOCX" for e in evs)
        assert all(e.content for e in evs)

    def test_headings_docx_to_evidence(self):
        doc = parse_document(str(FIX / "headings.docx"))
        evs = EXT.extract(doc)
        headings = [e for e in evs if e.metadata["block_type"] == "HEADING"]
        assert len(headings) >= 1
        assert all(e.content for e in headings)

    def test_tables_docx_to_evidence(self):
        doc = parse_document(str(FIX / "tables.docx"))
        evs = EXT.extract(doc)
        tables = [e for e in evs if e.metadata["block_type"] == "TABLE"]
        assert len(tables) >= 1
        assert all(t.source_reference.location == "table" for t in tables)

    def test_empty_docx_no_evidence(self):
        doc = parse_document(str(FIX / "empty.docx"))
        assert doc.status is DocumentStatus.PARSED_EMPTY
        assert EXT.extract(doc) == []

    def test_multilingual_docx_preserved(self):
        doc = parse_document(str(FIX / "multilingual.docx"))
        evs = EXT.extract(doc)
        assert all(e.content for e in evs)


class TestExistingEvidenceCompatibility:
    def test_document_evidence_coexists_with_note_evidence(self):
        doc_ev = EXT.extract(_parsed("document-coex001", "PDF", blocks=[_block("pdf text", page=1, block_index=0)]))[0]
        note_ev = Evidence(
            content="note text",
            evidence_type=EvidenceType.PERSONAL_NOTE,
            source_reference=SourceReference(material_id="mat-note", location="n.md"),
        )
        collection = [doc_ev, note_ev]
        assert collection[0].evidence_type is EvidenceType.DOCUMENT
        assert collection[1].evidence_type is EvidenceType.PERSONAL_NOTE
        assert collection[0].evidence_id != collection[1].evidence_id

    def test_document_evidence_accepted_by_evidence_extractor(self):
        mat = Material(
            filename="simple.pdf",
            path=str(FIX / "simple.pdf"),
            material_type=MaterialType.SYLLABUS,
            language=Language.UNKNOWN,
        )
        evs = EvidenceExtractor(mat).extract()
        assert len(evs) == 2
        assert all(e.evidence_type is EvidenceType.DOCUMENT for e in evs)

    def test_source_reference_is_std_model(self):
        doc = _parsed("document-coex002", "PDF", material_id="m", blocks=[_block("t", page=1, block_index=0)])
        e = EXT.extract(doc)[0]
        assert isinstance(e, Evidence)
        assert isinstance(e.source_reference, SourceReference)


class TestNoForbiddenSideEffects:
    def test_no_uuid_in_generated_ids(self):
        doc = _parsed("document-nouuid001", "PDF", blocks=[_block("t", page=1, block_index=0)])
        e = EXT.extract(doc)[0]
        # uuid4 ids are 36 chars with dashes; ours are 24 hex + prefix
        assert "-" not in e.evidence_id[len("doc-evidence-"):]
        assert len(e.evidence_id[len("doc-evidence-"):]) == 24

    def test_no_current_time_in_id(self):
        a = block_evidence_id("d", "PDF", "TEXT", "page:1", "x")
        b = block_evidence_id("d", "PDF", "TEXT", "page:1", "x")
        assert a == b


# ===========================================================================
# PPTX (PowerPoint) blocks -> Evidence
# ===========================================================================


class TestPptxEvidence:
    @pytest.fixture()
    def lecture(self) -> ParsedDocument:
        return parse_document(FIX / "lecture.pptx", material_id="mat-pptx")

    def test_slide_blocks_become_document_evidence(self, lecture):
        evs = DocumentEvidenceExtractor().extract(lecture)
        assert len(evs) == len(lecture.blocks)
        assert all(e.evidence_type is EvidenceType.DOCUMENT for e in evs)
        assert all(e.metadata["document_type"] == "PPTX" for e in evs)

    def test_location_is_the_pptx_locator_not_a_docx_paragraph(self, lecture):
        evs = DocumentEvidenceExtractor().extract(lecture)
        for e in evs:
            assert e.metadata["location"].startswith("pptx-slide-")
            assert e.source_reference.location == e.metadata["location"]
            assert not e.metadata["location"].startswith("docx-")

    def test_slide_number_is_carried_as_page(self, lecture):
        evs = DocumentEvidenceExtractor().extract(lecture)
        pages = {e.metadata["location"]: e.source_reference.page for e in evs}
        assert pages["pptx-slide-1-shape-0"] == 1
        assert pages["pptx-slide-2-shape-1"] == 2
        assert pages["pptx-slide-3-notes"] == 3

    def test_material_id_is_preserved(self, lecture):
        evs = DocumentEvidenceExtractor().extract(lecture)
        assert all(e.source_reference.material_id == "mat-pptx" for e in evs)

    def test_table_and_notes_keep_their_text_verbatim(self, lecture):
        evs = DocumentEvidenceExtractor().extract(lecture)
        contents = [e.content for e in evs]
        assert "x || f(x) | 0 || 0.25" in contents
        assert "Recordator: normalitzar abans de dibuixar." in contents

    def test_same_shape_same_text_is_deduplicated(self):
        doc = _parsed(
            "document-pptxdup01",
            "PPTX",
            material_id="mat-pptx",
            blocks=[
                _block("Repetit", page=1, block_index=0, location="pptx-slide-1-shape-0"),
                _block("Repetit", page=1, block_index=0, location="pptx-slide-1-shape-0"),
            ],
        )
        evs = DocumentEvidenceExtractor().extract(doc)
        assert len(evs) == 1

    def test_same_text_on_two_shapes_stays_distinct(self):
        doc = _parsed(
            "document-pptxdup02",
            "PPTX",
            material_id="mat-pptx",
            blocks=[
                _block("Repetit", page=1, block_index=0, location="pptx-slide-1-shape-0"),
                _block("Repetit", page=1, block_index=1, location="pptx-slide-1-shape-1"),
            ],
        )
        evs = DocumentEvidenceExtractor().extract(doc)
        assert len(evs) == 2
        assert len({e.evidence_id for e in evs}) == 2

    def test_pptx_pdf_and_docx_blocks_never_collide(self):
        """同一段文字在三种文档里必须得到不同的 evidence_id。"""
        same_text = "Valor de la densitat"
        ids = set()
        for doc_type, location in (
            ("PDF", ""),
            ("DOCX", ""),
            ("PPTX", "pptx-slide-1-shape-0"),
        ):
            block = (
                _block(same_text, page=1, block_index=0, location=location)
                if doc_type != "DOCX"
                else _block(same_text, para=0)
            )
            doc = _parsed(
                "document-shared0001", doc_type, material_id="mat", blocks=[block]
            )
            evs = DocumentEvidenceExtractor().extract(doc)
            assert len(evs) == 1
            ids.add(evs[0].evidence_id)
        assert len(ids) == 3

    def test_picture_only_deck_yields_no_document_evidence(self):
        parsed = parse_document(FIX / "picture_only.pptx", material_id="mat-pptx")
        assert parsed.status is DocumentStatus.PARSED_EMPTY
        assert DocumentEvidenceExtractor().extract(parsed) == []

    def test_failed_deck_yields_no_evidence(self):
        parsed = parse_document(FIX / "encrypted.pptx", material_id="mat-pptx")
        assert parsed.status is DocumentStatus.FAILED
        assert DocumentEvidenceExtractor().extract(parsed) == []

    def test_extraction_does_not_mutate_the_parsed_document(self, lecture):
        before = copy.deepcopy(lecture.to_dict())
        DocumentEvidenceExtractor().extract(lecture)
        assert lecture.to_dict() == before

    def test_evidence_ids_are_deterministic_across_runs(self):
        first = DocumentEvidenceExtractor().extract(
            parse_document(FIX / "lecture.pptx", material_id="mat-pptx")
        )
        second = DocumentEvidenceExtractor().extract(
            parse_document(FIX / "lecture.pptx", material_id="mat-pptx")
        )
        assert [e.evidence_id for e in first] == [e.evidence_id for e in second]

    def test_legacy_evidence_extractor_also_handles_pptx(self):
        """遗留链路 (src.evidence_extractor) 必须与新链路同等待遇。"""
        mat = Material(
            material_id="mat-pptx",
            filename="lecture.pptx",
            path=str(FIX / "lecture.pptx"),
            material_type=MaterialType.SYLLABUS,
            language=Language.UNKNOWN,
        )
        evs = EvidenceExtractor(mat).extract()
        assert len(evs) == 7
        assert all(e.evidence_type is EvidenceType.DOCUMENT for e in evs)
        assert all(e.metadata["location"].startswith("pptx-slide-") for e in evs)
