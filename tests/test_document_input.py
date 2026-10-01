"""Tests for Task 21: PDF / DOCX / PPTX document input & parsing layer."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

import pytest

from src.document_input import (
    DocumentBlock,
    DocumentBlockType,
    DocumentImage,
    DocumentInput,
    DocumentMaterialValidator,
    DocumentParserErrorCode,
    DocumentStatus,
    DocumentType,
    DocumentValidationError,
    DocumentValidationResult,
    ParsedDocument,
    ParserError,
    SUPPORTED_DOCUMENT_EXTENSIONS,
    create_document_parser,
    parse_document,
    validate_document,
)
from src.models import Material, MaterialType

FIXTURES = Path(__file__).parent / "fixtures" / "documents"


def _pdf(name: str) -> Path:
    return FIXTURES / name


def _pptx(name: str) -> Path:
    return FIXTURES / name


# ---------------------------------------------------------------------------
# Deterministic id computation
# ---------------------------------------------------------------------------


class TestDocumentStableId:
    def test_deterministic_same_input_same_id(self) -> None:
        from src.document_input import _compute_document_stable_id

        a = _compute_document_stable_id("", _pdf("simple.pdf"), 1234, 0)
        b = _compute_document_stable_id("", _pdf("simple.pdf"), 1234, 0)
        assert a == b
        assert a.startswith("document-")
        assert len(a) == len("document-") + 16

    def test_different_type_different_id(self) -> None:
        from src.document_input import _compute_document_stable_id

        a = _compute_document_stable_id("", _pdf("simple.pdf"), 1234, 0)
        b = _compute_document_stable_id("", _pdf("simple.docx"), 1234, 0)
        assert a != b

    def test_different_size_different_id(self) -> None:
        from src.document_input import _compute_document_stable_id

        a = _compute_document_stable_id("", _pdf("simple.pdf"), 100, 0)
        b = _compute_document_stable_id("", _pdf("simple.pdf"), 200, 0)
        assert a != b

    def test_mtime_ns_not_part_of_id(self) -> None:
        from src.document_input import _compute_document_stable_id

        a = _compute_document_stable_id("", _pdf("simple.pdf"), 1234, 100)
        b = _compute_document_stable_id("", _pdf("simple.pdf"), 1234, 200)
        assert a == b


# ---------------------------------------------------------------------------
# DocumentBlock
# ---------------------------------------------------------------------------


class TestDocumentBlock:
    def test_default_values(self) -> None:
        b = DocumentBlock()
        assert b.text == ""
        assert b.block_type is DocumentBlockType.TEXT
        assert b.page_number is None
        assert b.paragraph_index is None

    def test_to_dict_roundtrip(self) -> None:
        b = DocumentBlock(
            block_id="docblock-abc",
            block_type=DocumentBlockType.HEADING,
            text="Titulo",
            page_number=3,
            paragraph_index=7,
        )
        d = b.to_dict()
        b2 = DocumentBlock.from_dict(d)
        assert b2.block_id == "docblock-abc"
        assert b2.block_type is DocumentBlockType.HEADING
        assert b2.text == "Titulo"
        assert b2.page_number == 3
        assert b2.paragraph_index == 7

    def test_assign_block_id_deterministic(self) -> None:
        b1 = DocumentBlock(block_type=DocumentBlockType.TEXT, text="hello")
        b2 = DocumentBlock(block_type=DocumentBlockType.TEXT, text="hello")
        b1.assign_block_id("document-1")
        b2.assign_block_id("document-1")
        assert b1.block_id == b2.block_id
        assert b1.block_id.startswith("docblock-")
        assert len(b1.block_id) == len("docblock-") + 24

    def test_assign_block_id_differs_by_text(self) -> None:
        b1 = DocumentBlock(block_type=DocumentBlockType.TEXT, text="hello")
        b2 = DocumentBlock(block_type=DocumentBlockType.TEXT, text="world")
        b1.assign_block_id("document-1")
        b2.assign_block_id("document-1")
        assert b1.block_id != b2.block_id

    def test_assign_block_id_differs_by_page(self) -> None:
        b1 = DocumentBlock(block_type=DocumentBlockType.TEXT, text="x", page_number=1)
        b2 = DocumentBlock(block_type=DocumentBlockType.TEXT, text="x", page_number=2)
        b1.assign_block_id("document-1")
        b2.assign_block_id("document-1")
        assert b1.block_id != b2.block_id

    def test_to_source_reference_page(self) -> None:
        b = DocumentBlock(
            block_type=DocumentBlockType.TEXT,
            text="x",
            page_number=5,
            block_index=0,
        )
        ref = b.to_source_reference(material_id="mat-1")
        assert ref["material_id"] == "mat-1"
        assert ref["page"] == 5
        assert ref["line"] == 0
        assert "pdf-page-5" in ref["location"]

    def test_to_source_reference_paragraph(self) -> None:
        b = DocumentBlock(
            block_type=DocumentBlockType.TEXT,
            text="x",
            paragraph_index=12,
        )
        ref = b.to_source_reference(material_id="mat-2")
        assert ref["paragraph"] == "paragraph_12"
        assert "docx-paragraph-12" in ref["location"]

    def test_to_source_reference_table(self) -> None:
        b = DocumentBlock(
            block_type=DocumentBlockType.TABLE,
            text="a | b",
        )
        ref = b.to_source_reference(material_id="mat-3")
        assert ref["location"] == "table"


# ---------------------------------------------------------------------------
# ParsedDocument
# ---------------------------------------------------------------------------


class TestParsedDocument:
    def test_to_dict_from_dict_roundtrip(self) -> None:
        doc = ParsedDocument(
            document_id="document-abc",
            document_type="PDF",
            path="test.pdf",
            material_id="mat-1",
            status=DocumentStatus.PARSED,
            blocks=[
                DocumentBlock(
                    block_id="docblock-1",
                    block_type=DocumentBlockType.TEXT,
                    text="page one",
                    page_number=1,
                    block_index=0,
                )
            ],
        )
        d = doc.to_dict()
        doc2 = ParsedDocument.from_dict(d)
        assert doc2.document_id == "document-abc"
        assert doc2.document_type == "PDF"
        assert doc2.status is DocumentStatus.PARSED
        assert len(doc2.blocks) == 1
        assert doc2.blocks[0].text == "page one"
        assert doc2.blocks[0].page_number == 1

    def test_from_dict_invalid_status_defaults_failed(self) -> None:
        d = {"document_id": "x", "status": "BROKEN"}
        doc = ParsedDocument.from_dict(d)
        assert doc.status is DocumentStatus.FAILED

    def test_block_count_property(self) -> None:
        doc = ParsedDocument(
            blocks=[DocumentBlock(), DocumentBlock(), DocumentBlock()]
        )
        assert doc.block_count == 3

    def test_page_count_from_metadata(self) -> None:
        doc = ParsedDocument(metadata={"page_count": 42})
        assert doc.page_count == 42

    def test_page_count_defaults_zero(self) -> None:
        doc = ParsedDocument()
        assert doc.page_count == 0


# ---------------------------------------------------------------------------
# DocumentMaterialValidator
# ---------------------------------------------------------------------------


class TestDocumentMaterialValidator:
    def test_valid_pdf(self) -> None:
        v = DocumentMaterialValidator()
        r = v.validate(_pdf("simple.pdf"))
        assert r.valid is True
        assert r.status.value == "VALID"
        assert r.extension == ".pdf"
        assert r.document_type == "PDF"
        assert r.file_size > 0

    def test_valid_docx(self) -> None:
        v = DocumentMaterialValidator()
        r = v.validate(_pdf("simple.docx"))
        assert r.valid is True
        assert r.document_type == "DOCX"
        assert r.material_type is MaterialType.SYLLABUS

    def test_missing_file(self) -> None:
        v = DocumentMaterialValidator()
        r = v.validate(FIXTURES / "does_not_exist.pdf")
        assert r.valid is False
        assert DocumentValidationError.FILE_NOT_FOUND in r.errors

    def test_unsupported_extension(self) -> None:
        v = DocumentMaterialValidator()
        r = v.validate(FIXTURES / "unsupported.txt")
        assert r.valid is False
        assert DocumentValidationError.UNSUPPORTED_EXTENSION in r.errors

    def test_zero_byte_file(self) -> None:
        v = DocumentMaterialValidator()
        r = v.validate(_pdf("zero_bytes.pdf"))
        assert r.valid is False
        assert DocumentValidationError.EMPTY_FILE in r.errors

    def test_directory_rejected(self) -> None:
        v = DocumentMaterialValidator()
        r = v.validate(FIXTURES)
        assert r.valid is False
        assert DocumentValidationError.NOT_A_FILE in r.errors

    def test_case_insensitive_extension(self) -> None:
        v = DocumentMaterialValidator()
        r = v.validate(_pdf("simple.pdf"))
        assert r.extension == ".pdf"

    def test_to_document_input_valid(self) -> None:
        v = DocumentMaterialValidator()
        r = v.validate(_pdf("simple.pdf"))
        di = v.to_document_input(r)
        assert di.path == str(_pdf("simple.pdf"))
        assert di.document_type == "PDF"
        assert di.material_id is None

    def test_to_document_input_invalid_raises(self) -> None:
        v = DocumentMaterialValidator()
        r = v.validate(FIXTURES / "does_not_exist.pdf")
        with pytest.raises(ValueError):
            v.to_document_input(r)

    def test_compute_material_id_deterministic(self) -> None:
        v = DocumentMaterialValidator()
        r = v.validate(_pdf("simple.pdf"))
        a = v.compute_material_id(r)
        b = v.compute_material_id(r)
        assert a == b
        assert a.startswith("document-")

    def test_validate_document_helper(self) -> None:
        r = validate_document(_pdf("simple.pdf"))
        assert r.valid is True
        assert r.document_type == "PDF"

    def test_validate_document_zero_byte(self) -> None:
        r = validate_document(_pdf("zero_bytes.pdf"))
        assert r.valid is False
        assert DocumentValidationError.EMPTY_FILE in r.errors


# ---------------------------------------------------------------------------
# PDF parser
# ---------------------------------------------------------------------------


class TestPDFDocumentParser:
    def test_simple_pdf_parsed(self) -> None:
        doc = create_document_parser("PDF").parse(_pdf("simple.pdf"))
        assert doc.status is DocumentStatus.PARSED
        assert doc.page_count == 2
        assert doc.block_count == 2
        assert doc.blocks[0].page_number == 1
        assert doc.blocks[1].page_number == 2
        assert "Programa" in doc.blocks[0].text
        assert "Calculo I" in doc.blocks[0].text
        assert "Fecha del examen" in doc.blocks[1].text

    def test_pdf_block_ids_deterministic(self) -> None:
        p = create_document_parser("PDF")
        a = p.parse(_pdf("simple.pdf"))
        b = p.parse(_pdf("simple.pdf"))
        for ba, bb in zip(a.blocks, b.blocks):
            assert ba.block_id == bb.block_id
            assert ba.block_id.startswith("docblock-")

    def test_pdf_document_id_deterministic(self) -> None:
        p = create_document_parser("PDF")
        a = p.parse(_pdf("simple.pdf"))
        b = p.parse(_pdf("simple.pdf"))
        assert a.document_id == b.document_id
        assert a.document_id.startswith("document-")

    def test_password_pdf_failed(self) -> None:
        doc = create_document_parser("PDF").parse(_pdf("password.pdf"))
        assert doc.status is DocumentStatus.FAILED
        assert len(doc.errors) == 1
        assert doc.errors[0].error_code is DocumentParserErrorCode.PASSWORD_PROTECTED

    def test_corrupted_pdf_failed(self) -> None:
        doc = create_document_parser("PDF").parse(_pdf("corrupted.pdf"))
        assert doc.status is DocumentStatus.FAILED
        assert doc.errors[0].error_code is DocumentParserErrorCode.INVALID_DOCUMENT

    def test_scanned_pdf_parsed_empty(self) -> None:
        doc = create_document_parser("PDF").parse(_pdf("scanned_like.pdf"))
        assert doc.status is DocumentStatus.PARSED_EMPTY
        assert doc.metadata.get("scanned_or_no_text_layer") is True
        assert doc.page_count == 2

    def test_empty_pdf_parsed_empty(self) -> None:
        doc = create_document_parser("PDF").parse(_pdf("empty.pdf"))
        assert doc.status is DocumentStatus.PARSED_EMPTY
        assert doc.page_count == 1

    def test_large_pdf_all_pages(self) -> None:
        doc = create_document_parser("PDF").parse(_pdf("large_120_pages.pdf"))
        assert doc.status is DocumentStatus.PARSED
        assert doc.block_count == 120
        assert doc.blocks[0].text.startswith("Pagina 1:")
        assert doc.blocks[119].text.startswith("Pagina 120:")

    def test_pdf_block_text_stripped(self) -> None:
        doc = create_document_parser("PDF").parse(_pdf("simple.pdf"))
        for b in doc.blocks:
            assert b.text == b.text.strip()
            assert b.block_type is DocumentBlockType.TEXT

    def test_pdf_missing_file(self) -> None:
        doc = create_document_parser("PDF").parse(FIXTURES / "nope.pdf")
        assert doc.status is DocumentStatus.FAILED
        assert doc.errors[0].error_code is DocumentParserErrorCode.INVALID_DOCUMENT

    def test_pdf_multilingual_content(self) -> None:
        doc = create_document_parser("PDF").parse(_pdf("multilingual.pdf"))
        assert doc.status is DocumentStatus.PARSED
        assert "Introduccio" in doc.blocks[0].text
        assert "Preguntas de revision" in doc.blocks[1].text


# ---------------------------------------------------------------------------
# DOCX parser
# ---------------------------------------------------------------------------


class TestDOCXDocumentParser:
    def test_simple_docx_parsed(self) -> None:
        doc = create_document_parser("DOCX").parse(_pdf("simple.docx"))
        assert doc.status is DocumentStatus.PARSED
        # 3 non-empty paragraphs (one empty skipped, index advances)
        non_empty = [b for b in doc.blocks if b.block_type is not DocumentBlockType.TABLE]
        assert len(non_empty) == 3
        assert non_empty[0].paragraph_index == 0
        assert non_empty[1].paragraph_index == 1
        assert non_empty[2].paragraph_index == 3

    def test_docx_paragraph_index_advances_past_empty(self) -> None:
        doc = create_document_parser("DOCX").parse(_pdf("simple.docx"))
        paras = [b for b in doc.blocks if b.block_type is DocumentBlockType.TEXT]
        assert [p.paragraph_index for p in paras] == [0, 1, 3]

    def test_headings_docx(self) -> None:
        doc = create_document_parser("DOCX").parse(_pdf("headings.docx"))
        assert doc.status is DocumentStatus.PARSED
        headings = [b for b in doc.blocks if b.block_type is DocumentBlockType.HEADING]
        texts = [h.text for h in headings]
        assert "Capitulo 1: Analisis" in texts
        assert "1.1 Limites" in texts
        assert "Capitulo 2: Derivadas" in texts

    def test_docx_table_blocks(self) -> None:
        doc = create_document_parser("DOCX").parse(_pdf("tables.docx"))
        tables = [b for b in doc.blocks if b.block_type is DocumentBlockType.TABLE]
        assert len(tables) == 2
        assert tables[0].metadata.get("table_index") == 0
        assert tables[1].metadata.get("table_index") == 1
        assert "Asignacion" in tables[0].text
        assert "30%" in tables[0].text

    def test_docx_table_text_format(self) -> None:
        doc = create_document_parser("DOCX").parse(_pdf("tables.docx"))
        tables = [b for b in doc.blocks if b.block_type is DocumentBlockType.TABLE]
        # rows joined with " | ", cells within row joined with " || "
        parts = tables[0].text.split(" | ")
        assert len(parts) == 4
        assert " || " in parts[0]

    def test_docx_empty_parsed_empty(self) -> None:
        doc = create_document_parser("DOCX").parse(_pdf("empty.docx"))
        assert doc.status is DocumentStatus.PARSED_EMPTY
        assert doc.block_count == 0

    def test_docx_images_counted(self) -> None:
        doc = create_document_parser("DOCX").parse(_pdf("images.docx"))
        assert doc.status is DocumentStatus.PARSED
        assert doc.metadata.get("image_count") == 1

    def test_docx_corrupted_failed(self) -> None:
        doc = create_document_parser("DOCX").parse(_pdf("corrupted.docx"))
        assert doc.status is DocumentStatus.FAILED
        assert doc.errors[0].error_code is DocumentParserErrorCode.INVALID_DOCUMENT

    def test_docx_zero_byte_failed(self) -> None:
        doc = create_document_parser("DOCX").parse(_pdf("zero_bytes.docx"))
        assert doc.status is DocumentStatus.FAILED

    def test_docx_large_1000_paragraphs(self) -> None:
        doc = create_document_parser("DOCX").parse(_pdf("large_1000.docx"))
        assert doc.status is DocumentStatus.PARSED
        texts = [b for b in doc.blocks if b.block_type is DocumentBlockType.TEXT]
        assert len(texts) == 1000
        assert texts[0].text.startswith("Linea 0")
        assert texts[999].text.startswith("Linea 999")

    def test_docx_block_ids_deterministic(self) -> None:
        p = create_document_parser("DOCX")
        a = p.parse(_pdf("simple.docx"))
        b = p.parse(_pdf("simple.docx"))
        assert a.document_id == b.document_id
        for ba, bb in zip(a.blocks, b.blocks):
            assert ba.block_id == bb.block_id

    def test_docx_multilingual_content(self) -> None:
        doc = create_document_parser("DOCX").parse(_pdf("multilingual.docx"))
        assert doc.status is DocumentStatus.PARSED
        all_text = " ".join(b.text for b in doc.blocks)
        assert "Introduccio" in all_text
        assert "Definicion" in all_text

    def test_docx_block_index_not_set(self) -> None:
        doc = create_document_parser("DOCX").parse(_pdf("simple.docx"))
        for b in doc.blocks:
            assert b.block_index is None
            assert b.page_number is None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestFactory:
    def test_create_pdf_parser(self) -> None:
        p = create_document_parser("PDF")
        assert p.document_type == "PDF"

    def test_create_docx_parser(self) -> None:
        p = create_document_parser("DOCX")
        assert p.document_type == "DOCX"

    def test_create_case_insensitive(self) -> None:
        p = create_document_parser("pdf")
        assert p.document_type == "PDF"
        p2 = create_document_parser("docx")
        assert p2.document_type == "DOCX"

    def test_create_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            create_document_parser("XLSX")

    def test_parse_document_dispatches_by_extension(self) -> None:
        doc = parse_document(_pdf("simple.pdf"))
        assert doc.document_type == "PDF"
        doc2 = parse_document(_pdf("simple.docx"))
        assert doc2.document_type == "DOCX"

    def test_parse_document_unsupported_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_document(FIXTURES / "unsupported.txt")

    def test_parse_document_missing_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_document(FIXTURES / "does_not_exist.pdf")

    def test_parse_document_overrides_type(self) -> None:
        doc = parse_document(_pdf("simple.docx"), document_type="DOCX")
        assert doc.document_type == "DOCX"


# ---------------------------------------------------------------------------
# Serialization / deserialization
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_parsed_document_full_roundtrip(self) -> None:
        doc = create_document_parser("PDF").parse(_pdf("simple.pdf"))
        d = doc.to_dict()
        doc2 = ParsedDocument.from_dict(d)
        assert doc2.document_id == doc.document_id
        assert doc2.status is doc.status
        assert doc2.page_count == doc.page_count
        for b1, b2 in zip(doc.blocks, doc2.blocks):
            assert b1.block_id == b2.block_id
            assert b1.block_type is b2.block_type
            assert b1.text == b2.text
            assert b1.page_number == b2.page_number

    def test_parsed_document_with_errors_roundtrip(self) -> None:
        doc = create_document_parser("PDF").parse(_pdf("password.pdf"))
        d = doc.to_dict()
        doc2 = ParsedDocument.from_dict(d)
        assert doc2.status is DocumentStatus.FAILED
        assert len(doc2.errors) == 1
        assert doc2.errors[0].error_code is DocumentParserErrorCode.PASSWORD_PROTECTED

    def test_parser_error_roundtrip(self) -> None:
        e = ParserError(DocumentParserErrorCode.PARSER_ERROR, "test message")
        d = e.to_dict()
        e2 = ParserError.from_dict(d)
        assert e2.error_code is DocumentParserErrorCode.PARSER_ERROR
        assert e2.message == "test message"

    def test_parser_error_unknown_code_defaults(self) -> None:
        e = ParserError.from_dict({"error_code": "NOT_A_REAL_CODE", "message": "x"})
        assert e.error_code is DocumentParserErrorCode.PARSER_ERROR

    def test_document_input_roundtrip(self) -> None:
        v = DocumentMaterialValidator()
        r = v.validate(_pdf("simple.pdf"))
        di = v.to_document_input(r)
        d = di.to_dict()
        di2 = DocumentInput.from_dict(d)
        assert di2.path == di.path
        assert di2.extension == ".pdf"
        assert di2.document_type == "PDF"

    def test_document_validation_result_roundtrip(self) -> None:
        v = DocumentMaterialValidator()
        r = v.validate(_pdf("simple.pdf"))
        d = r.to_dict()
        r2 = DocumentValidationResult.from_dict(d)
        assert r2.valid is True
        assert r2.extension == ".pdf"
        assert r2.document_type == "PDF"
        assert r2.material_type is MaterialType.SYLLABUS

    def test_document_validation_result_invalid_roundtrip(self) -> None:
        v = DocumentMaterialValidator()
        r = v.validate(FIXTURES / "unsupported.txt")
        d = r.to_dict()
        r2 = DocumentValidationResult.from_dict(d)
        assert r2.valid is False
        assert DocumentValidationError.UNSUPPORTED_EXTENSION in r2.errors


# ---------------------------------------------------------------------------
# Determinism (no uuid4 anywhere)
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_no_uuid_in_ids(self) -> None:
        p = create_document_parser("PDF")
        doc = p.parse(_pdf("simple.pdf"))
        assert doc.document_id.startswith("document-")
        assert len(doc.document_id) == 9 + 16
        for b in doc.blocks:
            assert b.block_id.startswith("docblock-")
            assert len(b.block_id) == 9 + 24

    def test_same_file_same_ids_across_calls(self) -> None:
        p = create_document_parser("DOCX")
        a = p.parse(_pdf("headings.docx"))
        b = p.parse(_pdf("headings.docx"))
        assert a.document_id == b.document_id
        assert [x.block_id for x in a.blocks] == [x.block_id for x in b.blocks]

    def test_different_files_different_doc_ids(self) -> None:
        p = create_document_parser("PDF")
        a = p.parse(_pdf("simple.pdf"))
        b = p.parse(_pdf("multilingual.pdf"))
        assert a.document_id != b.document_id


# ---------------------------------------------------------------------------
# Source location
# ---------------------------------------------------------------------------


class TestSourceLocation:
    def test_pdf_block_source_ref_has_page(self) -> None:
        doc = create_document_parser("PDF").parse(_pdf("simple.pdf"))
        for b in doc.blocks:
            ref = b.to_source_reference("mat-x")
            assert ref["page"] is not None
            assert ref["page"] == b.page_number

    def test_docx_block_source_ref_has_paragraph(self) -> None:
        doc = create_document_parser("DOCX").parse(_pdf("headings.docx"))
        for b in doc.blocks:
            if b.paragraph_index is not None:
                ref = b.to_source_reference("mat-y")
                assert ref["paragraph"] == f"paragraph_{b.paragraph_index}"

    def test_docx_table_source_ref_location_is_table(self) -> None:
        doc = create_document_parser("DOCX").parse(_pdf("tables.docx"))
        for b in doc.blocks:
            if b.block_type is DocumentBlockType.TABLE:
                ref = b.to_source_reference("mat-z")
                assert ref["location"] == "table"


# ---------------------------------------------------------------------------
# Unicode / accent handling
# ---------------------------------------------------------------------------


class TestUnicodeContent:
    def test_pdf_accented_content_preserved(self) -> None:
        doc = create_document_parser("PDF").parse(_pdf("simple.pdf"))
        # Fixture uses ASCII only; verify no mojibake in block text.
        for b in doc.blocks:
            assert "\ufffd" not in b.text

    def test_docx_unicode_content_roundtrip(self) -> None:
        from docx import Document as DocxDocument
        import io

        d = DocxDocument()
        d.add_paragraph("Introducci\u00f3n: \u00e9s d\u00edgit")
        buf = io.BytesIO()
        d.save(buf)
        target = FIXTURES / "tmp_unicode.docx"
        target.write_bytes(buf.getvalue())
        try:
            doc = create_document_parser("DOCX").parse(target)
            assert doc.status is DocumentStatus.PARSED
            assert "Introducci\u00f3n" in doc.blocks[0].text
            assert "\u00e9s d\u00edgit" in doc.blocks[0].text
        finally:
            target.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Integration: validator -> parser -> source reference
# ---------------------------------------------------------------------------


class TestIntegration:
    @pytest.mark.integration
    def test_full_pipeline_pdf(self) -> None:
        v = DocumentMaterialValidator()
        r = v.validate(_pdf("simple.pdf"))
        assert r.valid is True
        doc = create_document_parser(r.document_type or "PDF").parse(
            Path(r.path), material_id=v.compute_material_id(r)
        )
        assert doc.status is DocumentStatus.PARSED
        for b in doc.blocks:
            ref = b.to_source_reference(doc.material_id)
            assert ref["material_id"] == doc.material_id
            assert ref["page"] is not None

    @pytest.mark.integration
    def test_full_pipeline_docx(self) -> None:
        v = DocumentMaterialValidator()
        r = v.validate(_pdf("headings.docx"))
        assert r.valid is True
        doc = create_document_parser("DOCX").parse(
            Path(r.path), material_id=v.compute_material_id(r)
        )
        assert doc.status is DocumentStatus.PARSED
        for b in doc.blocks:
            if b.block_type in (DocumentBlockType.TEXT, DocumentBlockType.HEADING):
                ref = b.to_source_reference(doc.material_id)
                assert ref["material_id"] == doc.material_id

    @pytest.mark.integration
    def test_parser_on_invalid_file_returns_failed_not_raises(self) -> None:
        for name in ("corrupted.pdf", "password.pdf", "corrupted.docx"):
            path = _pdf(name)
            parser = create_document_parser("PDF" if name.endswith("pdf") else "DOCX")
            doc = parser.parse(path)
            assert doc.status is DocumentStatus.FAILED, name

    @pytest.mark.integration
    def test_full_pipeline_pptx(self) -> None:
        v = DocumentMaterialValidator()
        r = v.validate(_pptx("lecture.pptx"))
        assert r.valid is True
        assert r.document_type == "PPTX"
        doc = create_document_parser(r.document_type or "").parse(
            Path(r.path), material_id=v.compute_material_id(r)
        )
        assert doc.status is DocumentStatus.PARSED
        for b in doc.blocks:
            ref = b.to_source_reference(doc.material_id)
            assert ref["material_id"] == doc.material_id
            assert ref["location"].startswith("pptx-slide-")


# ===========================================================================
# PPTX (PowerPoint) — 白名单 / 校验 / 解析 / 失败语义
# ===========================================================================


class TestPptxWhitelist:
    """``.pptx`` 必须和 pdf / docx 同等待遇; ``.ppt`` 必须仍然被拒。"""

    def test_pptx_is_a_supported_document_extension(self) -> None:
        assert ".pptx" in SUPPORTED_DOCUMENT_EXTENSIONS
        assert ".pdf" in SUPPORTED_DOCUMENT_EXTENSIONS
        assert ".docx" in SUPPORTED_DOCUMENT_EXTENSIONS

    def test_legacy_ppt_binary_format_stays_unsupported(self) -> None:
        assert ".ppt" not in SUPPORTED_DOCUMENT_EXTENSIONS
        assert ".pps" not in SUPPORTED_DOCUMENT_EXTENSIONS
        assert ".odp" not in SUPPORTED_DOCUMENT_EXTENSIONS

    def test_validate_accepts_pptx(self) -> None:
        result = validate_document(_pptx("lecture.pptx"))
        assert result.valid is True
        assert result.status.value == "VALID"
        assert result.document_type == "PPTX"
        assert result.extension == ".pptx"
        assert result.errors == ()

    def test_validate_accepts_uppercase_pptx_extension(self, tmp_path) -> None:
        target = tmp_path / "DECK.PPTX"
        target.write_bytes(_pptx("lecture.pptx").read_bytes())
        result = validate_document(target)
        assert result.valid is True
        assert result.document_type == "PPTX"

    def test_validate_rejects_ppt_with_unsupported_extension(self, tmp_path) -> None:
        target = tmp_path / "old.ppt"
        target.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 binary deck")
        result = validate_document(target)
        assert result.valid is False
        assert DocumentValidationError.UNSUPPORTED_EXTENSION in result.errors
        assert result.document_type is None

    def test_validate_rejects_odp(self, tmp_path) -> None:
        target = tmp_path / "deck.odp"
        target.write_bytes(b"PK\x03\x04 odp")
        result = validate_document(target)
        assert DocumentValidationError.UNSUPPORTED_EXTENSION in result.errors

    def test_document_type_enum_knows_pptx(self) -> None:
        assert DocumentType.from_extension(".pptx") is DocumentType.PPTX
        assert DocumentType.from_extension(".PPTX") is DocumentType.PPTX
        assert DocumentType.from_extension(".ppt") is None
        assert DocumentType.from_string("pptx") is DocumentType.PPTX


class TestPptxParserContent:
    """幻灯片文字 + 表格 + 演讲者备注, 定位符必须是 ``pptx-slide-*``。"""

    @pytest.fixture()
    def doc(self) -> ParsedDocument:
        return create_document_parser("PPTX").parse(
            _pptx("lecture.pptx"), material_id="mat-pptx"
        )

    def test_status_and_type(self, doc: ParsedDocument) -> None:
        assert doc.document_type == "PPTX"
        assert doc.status is DocumentStatus.PARSED
        assert doc.material_id == "mat-pptx"
        assert doc.errors == ()

    def test_slide_text_is_extracted_verbatim(self, doc: ParsedDocument) -> None:
        texts = [b.text for b in doc.blocks]
        assert "Funcions de densitat" in texts
        assert (
            "La densitat de probabilitat integra 1 sobre tot el suport." in texts
        )
        # Unicode from the fixture must survive untouched.
        assert any("à" in t for t in texts)

    def test_placeholder_text_counts_like_a_text_box(self, doc: ParsedDocument) -> None:
        # The body of slide 1 is a *placeholder*, not a plain text box.
        placeholder = [b for b in doc.blocks if b.location == "pptx-slide-1-shape-1"]
        assert len(placeholder) == 1
        assert placeholder[0].block_type is DocumentBlockType.TEXT

    def test_speaker_notes_are_extracted(self, doc: ParsedDocument) -> None:
        notes = [b for b in doc.blocks if b.metadata.get("kind") == "notes"]
        assert len(notes) == 2
        assert "Recordator: normalitzar abans de dibuixar." in [b.text for b in notes]
        assert all(b.location.endswith("-notes") for b in notes)

    def test_table_uses_the_docx_wire_format(self, doc: ParsedDocument) -> None:
        tables = [b for b in doc.blocks if b.block_type is DocumentBlockType.TABLE]
        assert len(tables) == 1
        assert tables[0].text == "x || f(x) | 0 || 0.25"

    def test_locators_are_pptx_specific(self, doc: ParsedDocument) -> None:
        for b in doc.blocks:
            assert b.location.startswith("pptx-slide-"), b.location
            assert "docx-paragraph" not in b.location
            assert "pdf-page" not in b.location

    def test_source_reference_keeps_slide_page_and_shape(self, doc: ParsedDocument) -> None:
        first = doc.blocks[0]
        ref = first.to_source_reference("mat-pptx")
        assert ref["material_id"] == "mat-pptx"
        assert ref["location"] == "pptx-slide-1-shape-0"
        assert ref["page"] == 1          # slide 1
        assert ref["line"] == 0          # shape 0
        assert "paragraph" not in ref

    def test_metadata_reports_slides_tables_notes_and_images(
        self, doc: ParsedDocument
    ) -> None:
        assert doc.metadata["slide_count"] == 3
        assert doc.metadata["page_count"] == 3
        assert doc.metadata["table_count"] == 1
        assert doc.metadata["notes_count"] == 2
        assert doc.metadata["image_count"] == 1
        assert "scanned_or_no_text_layer" not in doc.metadata

    def test_same_slide_same_shape_keeps_one_block(self, doc: ParsedDocument) -> None:
        locations = [b.location for b in doc.blocks]
        assert len(locations) == len(set(locations))

    def test_empty_paragraph_shapes_produce_no_block(self, doc: ParsedDocument) -> None:
        assert all(b.text.strip() for b in doc.blocks)


class TestPptxImagePayload:
    """图片: 计数 + 可 OCR payload, 字节不泄进 to_dict()。"""

    @pytest.fixture()
    def doc(self) -> ParsedDocument:
        return create_document_parser("PPTX").parse(
            _pptx("lecture.pptx"), material_id="mat-pptx"
        )

    def test_image_is_counted_and_exposed_with_bytes(self, doc: ParsedDocument) -> None:
        assert len(doc.images) == 1
        image = doc.images[0]
        assert isinstance(image, DocumentImage)
        assert image.content
        assert image.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert image.extension == ".png"
        assert image.slide_number == 3
        assert image.location == "pptx-slide-3-shape-1"
        assert image.byte_size == len(image.content)
        assert image.content_sha256 == hashlib.sha256(image.content).hexdigest()
        assert image.image_id.startswith("docimage-")

    def test_image_is_flagged_ocr_capable(self, doc: ParsedDocument) -> None:
        assert doc.images[0].is_ocr_capable is True

    def test_image_bytes_never_reach_to_dict(self, doc: ParsedDocument) -> None:
        payload = doc.to_dict()
        assert "images" not in payload
        # The serializable *summary* is there; the pixels are not.
        summary = payload["metadata"]["images"]
        assert summary[0]["content_sha256"] == doc.images[0].content_sha256
        assert "content" not in summary[0]

    def test_ocr_extension_set_matches_the_ocr_provider(
        self, doc: ParsedDocument
    ) -> None:
        from src.document_input import _OCR_IMAGE_EXTENSIONS
        from src.ocr_provider import SUPPORTED_IMAGE_EXTENSIONS

        assert set(_OCR_IMAGE_EXTENSIONS) == set(SUPPORTED_IMAGE_EXTENSIONS)

    def test_vector_or_unknown_pictures_are_counted_but_not_ocr_capable(
        self,
    ) -> None:
        image = DocumentImage(
            image_id="docimage-x",
            location="pptx-slide-1-shape-0",
            extension=".emf",
            content=b"\x01\x00\x00\x00",
        )
        assert image.is_ocr_capable is False
        assert image.to_metadata()["ocr_capable"] is False


class TestPptxEmptyAndFailure:
    def test_picture_only_deck_is_parsed_empty_with_the_image(self) -> None:
        doc = create_document_parser("PPTX").parse(_pptx("picture_only.pptx"))
        assert doc.status is DocumentStatus.PARSED_EMPTY
        assert doc.block_count == 0
        assert doc.errors == ()
        assert doc.metadata["image_count"] == 1
        assert doc.metadata["scanned_or_no_text_layer"] is True
        assert len(doc.images) == 1

    def test_deck_without_slides_is_parsed_empty(self) -> None:
        doc = create_document_parser("PPTX").parse(_pptx("empty.pptx"))
        assert doc.status is DocumentStatus.PARSED_EMPTY
        assert doc.metadata["slide_count"] == 0
        assert doc.metadata["scanned_or_no_text_layer"] is True

    def test_encrypted_deck_fails_with_invalid_document(self) -> None:
        doc = create_document_parser("PPTX").parse(_pptx("encrypted.pptx"))
        assert doc.status is DocumentStatus.FAILED
        assert doc.block_count == 0
        assert len(doc.errors) == 1
        assert doc.errors[0].error_code is DocumentParserErrorCode.INVALID_DOCUMENT
        # No fabricated content, no traceback, no absolute path leakage.
        assert "Traceback" not in doc.errors[0].message

    def test_corrupted_deck_fails_with_invalid_document(self) -> None:
        doc = create_document_parser("PPTX").parse(_pptx("corrupted.pptx"))
        assert doc.status is DocumentStatus.FAILED
        assert doc.errors[0].error_code is DocumentParserErrorCode.INVALID_DOCUMENT

    def test_missing_file_fails_with_invalid_document(self, tmp_path) -> None:
        doc = create_document_parser("PPTX").parse(tmp_path / "nope.pptx")
        assert doc.status is DocumentStatus.FAILED
        assert doc.errors[0].error_code is DocumentParserErrorCode.INVALID_DOCUMENT

    def test_missing_python_pptx_reports_parser_unavailable(
        self, monkeypatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "pptx", None)
        monkeypatch.setitem(sys.modules, "pptx.exc", None)
        doc = create_document_parser("PPTX").parse(_pptx("lecture.pptx"))
        assert doc.status is DocumentStatus.FAILED
        assert doc.errors[0].error_code is DocumentParserErrorCode.PARSER_UNAVAILABLE
        assert "python-pptx import failed" in doc.errors[0].message

    def test_corrupt_deck_never_succeeds_with_zero_evidence(self) -> None:
        for name in ("encrypted.pptx", "corrupted.pptx"):
            doc = create_document_parser("PPTX").parse(_pptx(name))
            assert doc.status is DocumentStatus.FAILED, name
            assert doc.block_count == 0, name
            assert doc.images == (), name


class TestPptxFactoryAndIdentity:
    def test_factory_returns_pptx_parser(self) -> None:
        from src.document_input import PPTXDocumentParser

        parser = create_document_parser("pptx")
        assert isinstance(parser, PPTXDocumentParser)
        assert parser.document_type == "PPTX"

    def test_parse_document_dispatches_by_extension(self) -> None:
        doc = parse_document(_pptx("lecture.pptx"), material_id="mat-pptx")
        assert doc.document_type == "PPTX"
        assert doc.status is DocumentStatus.PARSED

    def test_parse_document_rejects_ppt(self, tmp_path) -> None:
        target = tmp_path / "old.ppt"
        target.write_bytes(b"\xd0\xcf\x11\xe0")
        with pytest.raises(ValueError):
            parse_document(target)

    def test_pptx_id_differs_from_pdf_and_docx_for_the_same_material(self) -> None:
        from src.document_input import _compute_document_stable_id

        a = _compute_document_stable_id("mat-1", "deck.pptx", 100, 0)
        b = _compute_document_stable_id("mat-1", "deck.pdf", 100, 0)
        c = _compute_document_stable_id("mat-1", "deck.docx", 100, 0)
        assert len({a, b, c}) == 3
        assert a == _compute_document_stable_id("mat-1", "deck.pptx", 100, 0)

    def test_parsing_is_deterministic(self) -> None:
        first = create_document_parser("PPTX").parse(
            _pptx("lecture.pptx"), material_id="mat-pptx"
        )
        second = create_document_parser("PPTX").parse(
            _pptx("lecture.pptx"), material_id="mat-pptx"
        )
        assert first.document_id == second.document_id
        assert [b.block_id for b in first.blocks] == [b.block_id for b in second.blocks]
        assert [i.image_id for i in first.images] == [i.image_id for i in second.images]

    def test_to_dict_roundtrip_preserves_the_pptx_locator(self) -> None:
        doc = create_document_parser("PPTX").parse(_pptx("lecture.pptx"))
        rehydrated = ParsedDocument.from_dict(doc.to_dict())
        assert rehydrated.document_type == "PPTX"
        assert [b.location for b in rehydrated.blocks] == [
            b.location for b in doc.blocks
        ]
        assert [b.block_id for b in rehydrated.blocks] == [
            b.block_id for b in doc.blocks
        ]
