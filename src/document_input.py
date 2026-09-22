"""Document (PDF / DOCX) input & parsing layer (Task 21).

Deterministic, evidence-first reading of PDF and DOCX course materials.
Answers: "What is in this file, page by page / paragraph by paragraph?"

Pipeline position:

    PDF / DOCX file
        |
    DocumentMaterialValidator      (file-level checks only, no content reads)
        |
    DocumentValidationResult       (stable error codes; valid / invalid)
        |
    DocumentInput                  (file identity: path, type, material link)
        |
    create_document_parser(type)   (dispatch: PDF | DOCX)
        |
    ParsedDocument                 (blocks + metadata + status + errors)
        |
    DocumentBlock                  (text + location + deterministic id)
        |
    (future) Evidence layer

Explicitly NOT done here (by design):
    LLM, OCR, ASR, translation, NLP, embedding, semantic extraction,
    KnowledgePoint / KnowledgeStructure / Review generation, automatic
    repair / reordering / cleanup of source text.

Dependencies: pypdf (PDF text extraction) + python-docx (DOCX body).
Both are local, offline, deterministic text-extraction libraries.
"""

from __future__ import annotations

import hashlib
import io
import os
import stat as _stat
import zipfile
import abc
from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

from src.models import Material, MaterialType, SourceReference

# ---------------------------------------------------------------------------
# Extension / type constants
# ---------------------------------------------------------------------------

SUPPORTED_DOCUMENT_EXTENSIONS: tuple[str, ...] = (".pdf", ".docx")

_DOCUMENT_TYPES: tuple[str, ...] = ("PDF", "DOCX")

_EXT_TO_DOC_TYPE: dict[str, str] = {
    ".pdf": "PDF",
    ".docx": "DOCX",
}


class DocumentValidationError(str, Enum):
    """Stable, system-independent validation error codes."""

    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    NOT_A_FILE = "NOT_A_FILE"
    UNSUPPORTED_EXTENSION = "UNSUPPORTED_EXTENSION"
    EMPTY_FILE = "EMPTY_FILE"
    UNREADABLE = "UNREADABLE"


class DocumentParserErrorCode(str, Enum):
    """Stable, system-independent parser error codes."""

    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    PASSWORD_PROTECTED = "PASSWORD_PROTECTED"
    PARSER_ERROR = "PARSER_ERROR"
    PARSER_UNAVAILABLE = "PARSER_UNAVAILABLE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"


class DocumentType(str, Enum):
    PDF = "PDF"
    DOCX = "DOCX"

    @classmethod
    def from_extension(cls, ext: str) -> Optional["DocumentType"]:
        ext = ext.lower()
        if ext == ".pdf":
            return cls.PDF
        if ext == ".docx":
            return cls.DOCX
        return None

    @classmethod
    def from_string(cls, value: str) -> Optional["DocumentType"]:
        for t in cls:
            if t.value.lower() == str(value).strip().lower():
                return t
        return None


class DocumentBlockType(str, Enum):
    """Block type carried on every DocumentBlock.

    PDF blocks are always TEXT (no heading inference in v1).
    DOCX blocks use HEADING only when the paragraph style is explicitly
    named Heading N (style-based, not content-based).
    """

    PARAGRAPH = "PARAGRAPH"
    HEADING = "HEADING"
    TEXT = "TEXT"
    TABLE = "TABLE"


class DocumentStatus(str, Enum):
    """High-level parse outcome.  PARSED_EMPTY is a legitimate success."""

    PARSED = "PARSED"
    PARSED_EMPTY = "PARSED_EMPTY"
    FAILED = "FAILED"

    @classmethod
    def from_string(cls, value: str) -> "DocumentStatus":
        for s in cls:
            if s.value.lower() == str(value).strip().lower():
                return s
        return cls.FAILED

# ---------------------------------------------------------------------------
# ParserError
# ---------------------------------------------------------------------------

@dataclass
class ParserError:
    """One structured parser failure (carries a stable code + message).

    No traceback, no absolute path, no internal system info in the message.
    """

    error_code: DocumentParserErrorCode
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"error_code": self.error_code.value, "message": self.message}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParserError":
        code_str = data.get("error_code", "PARSER_ERROR")
        try:
            code = DocumentParserErrorCode(code_str)
        except ValueError:
            code = DocumentParserErrorCode.PARSER_ERROR
        return cls(error_code=code, message=str(data.get("message", "")))


# ---------------------------------------------------------------------------
# DocumentBlock
# ---------------------------------------------------------------------------

@dataclass
class DocumentBlock:
    """One structured text block from a document.

    block_id is deterministic:
        "docblock-" + sha256(document_id|block_type|location_key|text)[:24]

    No uuid4 is used anywhere in the business ID.
    """

    block_id: str = ""
    block_type: DocumentBlockType = field(default_factory=lambda: DocumentBlockType.TEXT)
    text: str = ""
    page_number: Optional[int] = None       # 1-based (PDF only)
    block_index: Optional[int] = None       # 0-based within page (PDF only)
    paragraph_index: Optional[int] = None   # 0-based body position (DOCX only)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "block_type": self.block_type.value,
            "text": self.text,
            "page_number": self.page_number,
            "block_index": self.block_index,
            "paragraph_index": self.paragraph_index,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DocumentBlock":
        btype_str = data.get("block_type", "TEXT")
        try:
            btype = DocumentBlockType(btype_str)
        except ValueError:
            btype = DocumentBlockType.TEXT
        return cls(
            block_id=data.get("block_id", ""),
            block_type=btype,
            text=data.get("text", ""),
            page_number=data.get("page_number"),
            block_index=data.get("block_index"),
            paragraph_index=data.get("paragraph_index"),
            metadata=dict(data.get("metadata", {})),
        )

    def _location_key(self) -> str:
        if self.page_number is not None:
            return f"page:{self.page_number}"
        if self.paragraph_index is not None:
            return f"para:{self.paragraph_index}"
        return "none"

    def assign_block_id(self, document_id: str) -> None:
        """Deterministically assign block_id from document_id + location + text."""
        raw = f"{document_id}|{self.block_type.value}|{self._location_key()}|{self.text}".encode("utf-8")
        self.block_id = "docblock-" + hashlib.sha256(raw).hexdigest()[:24]

    def to_source_reference(self, material_id: str = "") -> dict[str, Any]:
        """SourceReference-compatible dict (page / paragraph / line / location)."""
        ref: dict[str, Any] = {"material_id": material_id}
        if self.page_number is not None:
            ref["page"] = self.page_number
        if self.paragraph_index is not None:
            ref["paragraph"] = f"paragraph_{self.paragraph_index}"
        if self.block_index is not None:
            ref["line"] = self.block_index
        if self.block_type is DocumentBlockType.TABLE:
            ref["location"] = "table"
        elif self.page_number is not None:
            ref["location"] = f"pdf-page-{self.page_number}-block-{self.block_index}"
        elif self.paragraph_index is not None:
            ref["location"] = f"docx-paragraph-{self.paragraph_index}"
        return ref

# ---------------------------------------------------------------------------
# ParsedDocument
# ---------------------------------------------------------------------------

@dataclass
class ParsedDocument:
    """Complete parse result for one document file.

    document_id is deterministic:
        "document-" + sha256(document_type|path|file_size)[:16]

    status:
        PARSED       - at least one non-empty block was extracted
        PARSED_EMPTY - parsing succeeded, zero blocks (scanned PDF, empty DOCX)
        FAILED       - parser raised an error (corrupt, password-locked, etc.)
    """

    document_id: str = ""
    document_type: str = "PDF"
    path: str = ""
    material_id: str = ""
    status: DocumentStatus = DocumentStatus.FAILED
    blocks: list[DocumentBlock] = field(default_factory=list)
    errors: tuple[ParserError, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return int(self.metadata.get("page_count", 0))

    @property
    def block_count(self) -> int:
        return len(self.blocks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "document_type": self.document_type,
            "path": self.path,
            "material_id": self.material_id,
            "status": self.status.value,
            "blocks": [b.to_dict() for b in self.blocks],
            "errors": [e.to_dict() for e in self.errors],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParsedDocument":
        status_str = data.get("status", "FAILED")
        try:
            status = DocumentStatus(status_str)
        except ValueError:
            status = DocumentStatus.FAILED
        return cls(
            document_id=data.get("document_id", ""),
            document_type=data.get("document_type", "PDF"),
            path=data.get("path", ""),
            material_id=data.get("material_id", ""),
            status=status,
            blocks=[DocumentBlock.from_dict(b) for b in data.get("blocks", [])],
            errors=tuple(ParserError.from_dict(e) for e in data.get("errors", [])),
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# DocumentInput
# ---------------------------------------------------------------------------

@dataclass
class DocumentInput:
    """File-level identity for a document material candidate.

    Mirrors AudioInput (Task 16).  Does NOT carry parsed content.
    """

    path: str = ""
    extension: str = ""
    document_type: str = "PDF"
    material: Optional[Material] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def material_id(self) -> Optional[str]:
        return self.material.material_id if self.material is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "extension": self.extension,
            "document_type": self.document_type,
            "material": self.material.to_dict() if self.material is not None else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DocumentInput":
        mat = data.get("material")
        return cls(
            path=data.get("path", ""),
            extension=data.get("extension", ""),
            document_type=data.get("document_type", "PDF"),
            material=Material.from_dict(mat) if isinstance(mat, dict) else None,
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# DocumentValidationResult
# ---------------------------------------------------------------------------

class DocumentValidationStatus(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"

    @classmethod
    def from_string(cls, value: str) -> "DocumentValidationStatus":
        for s in cls:
            if s.value.lower() == str(value).strip().lower():
                return s
        return cls.INVALID


@dataclass
class DocumentValidationResult:
    """Structured outcome of document file validation (no content reads)."""

    valid: bool = False
    status: DocumentValidationStatus = field(default_factory=lambda: DocumentValidationStatus.INVALID)
    errors: tuple[DocumentValidationError, ...] = field(default_factory=tuple)
    path: str = ""
    extension: str = ""
    document_type: Optional[str] = None
    file_size: int = 0
    modified_time: float = 0.0
    material: Optional[Material] = None
    material_type: MaterialType = MaterialType.SYLLABUS
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "status": self.status.value,
            "errors": [e.value for e in self.errors],
            "path": self.path,
            "extension": self.extension,
            "document_type": self.document_type,
            "file_size": self.file_size,
            "modified_time": self.modified_time,
            "material": self.material.to_dict() if self.material is not None else None,
            "material_type": self.material_type.value,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DocumentValidationResult":
        known = set(DocumentValidationError._value2member_map_)
        errors = tuple(
            DocumentValidationError(code)
            for code in data.get("errors", [])
            if code in known
        )
        mat = data.get("material")
        mat_type_str = data.get("material_type", "syllabus")
        return cls(
            valid=bool(data.get("valid", False)),
            status=DocumentValidationStatus.from_string(str(data.get("status", "INVALID"))),
            errors=errors,
            path=str(data.get("path", "")),
            extension=str(data.get("extension", "")),
            document_type=data.get("document_type"),
            file_size=int(data.get("file_size", 0)),
            modified_time=float(data.get("modified_time", 0.0)),
            material=Material.from_dict(mat) if isinstance(mat, dict) else None,
            material_type=MaterialType.from_string(mat_type_str),
            metadata=dict(data.get("metadata", {})),
        )

# ---------------------------------------------------------------------------
# Deterministic id computation
# ---------------------------------------------------------------------------

def _compute_document_stable_id(
    material_id: str,
    path: Union[str, Path],
    file_size: int,
    mtime_ns: int = 0,
) -> str:
    """'document-' + sha256(stable components)[:16]

    Identity is content-addressed, NOT path-addressed:

    - When ``material_id`` is supplied (the application layer already
      derives it deterministically from file content + course + filename),
      it is used directly.  Identical documents therefore yield identical
      document_id no matter which directory they are uploaded from, so the
      downstream ``doc-evidence-*`` ids (and everything assembled from them)
      stay stable across runs / machines / data directories.
    - Only when ``material_id`` is absent (legacy / direct parse calls that
      bypass the material workflow) does it fall back to
      ``(type|path|file_size)``, preserving the old behaviour for those
      callers.

    ``mtime_ns`` / ``file_size`` are intentionally NOT part of the
    content-addressed form (stable across content copies / index refreshes;
    same content -> same id); they are only used by the legacy fallback.
    """
    if material_id:
        doc_type = _EXT_TO_DOC_TYPE.get(Path(path).suffix.lower(), "UNKNOWN")
        raw = f"{doc_type}|{material_id}".encode("utf-8")
    else:
        p = Path(path)
        doc_type = _EXT_TO_DOC_TYPE.get(p.suffix.lower(), "UNKNOWN")
        raw = f"{doc_type}|{p.as_posix()}|{file_size}".encode("utf-8")
    return "document-" + hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# DocumentMaterialValidator
# ---------------------------------------------------------------------------


class DocumentMaterialValidator:
    """Deterministic, file-level validator for PDF / DOCX materials.

    Mirrors AudioMaterialValidator (Task 16) / NoteMaterialValidator:
    a single file is checked, and the result is a pure
    DocumentValidationResult carrying a stable error code.  No bytes
    are read, no parser runs, no OCR / LLM, no mutation of caller
    state.

    Usage::

        validator = DocumentMaterialValidator()
        result = validator.validate(path)
        result = validator.validate(material)

    All check failures are returned inside the DocumentValidationResult;
    only internal programmer errors raise exceptions.
    """

    supported_extensions: tuple[str, ...] = SUPPORTED_DOCUMENT_EXTENSIONS

    def validate(
        self,
        material_or_path: Union[Material, str, "os.PathLike[str]"],
    ) -> DocumentValidationResult:
        """Validate a single document material candidate.

        When a Material is passed, its indexed metadata (size, sha256,
        mtime, relative_path) is reused rather than recomputed, and its
        material_id is carried into the result.  When a raw path is
        passed, the check is performed directly on the filesystem.
        The input is never mutated.
        """
        material: Optional[Material] = None
        if isinstance(material_or_path, Material):
            material = material_or_path
            path_str: str = material.path or material.filename
            metadata = dict(material.metadata)
        else:
            path_str = os.fspath(material_or_path)
            metadata = {}

        if not path_str:
            return self._fail(DocumentValidationError.FILE_NOT_FOUND, "")

        return self._validate_path(path_str, metadata, material)

    def to_document_input(
        self, result: DocumentValidationResult
    ) -> DocumentInput:
        """Build a DocumentInput from a VALID result.

        Raises:
            ValueError: if the result is not valid.
        """
        if not result.valid:
            raise ValueError(
                "Cannot build DocumentInput from an invalid result: "
                + ", ".join(e.value for e in result.errors)
            )
        return DocumentInput(
            path=result.path,
            extension=result.extension,
            document_type=result.document_type or "PDF",
            material=result.material,
            metadata=dict(result.metadata),
        )

    def _fail(
        self,
        code: DocumentValidationError,
        path: str = "",
    ) -> DocumentValidationResult:
        return DocumentValidationResult(
            valid=False,
            status=DocumentValidationStatus.INVALID,
            errors=(code,),
            path=path,
        )

    # ------------------------------------------------------------------

    def _validate_path(
        self,
        path_str: str,
        metadata: dict[str, Any],
        material: Optional[Material],
    ) -> DocumentValidationResult:
        p = Path(path_str)

        # 1. Existence
        try:
            st = p.stat()
        except OSError:
            return self._fail(DocumentValidationError.FILE_NOT_FOUND, path_str)

        errors: list[DocumentValidationError] = []

        # 2. Regular file (directories are rejected even if named *.pdf)
        if not _stat.S_ISREG(st.st_mode):
            errors.append(DocumentValidationError.NOT_A_FILE)

        # 3. Extension - case-insensitive; original path is never mutated
        ext = p.suffix.lower()
        if ext not in self.supported_extensions:
            errors.append(DocumentValidationError.UNSUPPORTED_EXTENSION)

        # 4. Non-empty
        if st.st_size == 0:
            errors.append(DocumentValidationError.EMPTY_FILE)

        # 5. Readable (lightweight access check; contents are NOT read)
        if not os.access(p, os.R_OK):
            errors.append(DocumentValidationError.UNREADABLE)

        status = (
            DocumentValidationStatus.INVALID
            if errors
            else DocumentValidationStatus.VALID
        )

        doc_type: Optional[str] = None
        if not errors:
            doc_type = _EXT_TO_DOC_TYPE.get(ext)

        return DocumentValidationResult(
            valid=not errors,
            status=status,
            errors=tuple(errors),
            path=path_str,
            extension=ext,
            document_type=doc_type,
            file_size=st.st_size,
            modified_time=st.st_mtime,
            material=material,
            material_type=MaterialType.SYLLABUS,
            metadata=metadata,
        )

    def compute_material_id(
        self, result: DocumentValidationResult
    ) -> str:
        """Deterministic document id for a validated result.

        Mirrors MaterialIndex._compute_stable_id's role (stable across
        repeated scans of an unchanged file) without requiring the file
        to be present on disk at call time - it uses only the result's
        recorded size / mtime, never re-stats.
        """
        return _compute_document_stable_id(
            "", result.path, result.file_size, int(result.modified_time * 1e9)
        )

# ---------------------------------------------------------------------------
# Document parsers
# ---------------------------------------------------------------------------


class DocumentParser(ABC):
    """Base class.  Subclasses implement :meth:`parse`, which must be
    fully deterministic for a given input file (same content -> same ids,
    same block order, same texts, same status)."""

    document_type: str = ""

    @abc.abstractmethod
    def parse(
        self,
        source: Union[str, Path],
        material_id: str = "",
    ) -> ParsedDocument:
        """Parse the document at ``source``.

        Never raises for parser-level problems: those are encoded in the
        returned ParsedDocument via ``status`` + ``errors``.
        """
        ...

    # ---- shared helpers ----------------------------------------------------

    @staticmethod
    def _make_block(
        document_id: str,
        block_type: DocumentBlockType,
        text: str,
        page_number: Optional[int] = None,
        block_index: Optional[int] = None,
        paragraph_index: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> DocumentBlock:
        b = DocumentBlock(
            block_type=block_type,
            text=text,
            page_number=page_number,
            block_index=block_index,
            paragraph_index=paragraph_index,
            metadata=dict(metadata or {}),
        )
        b.assign_block_id(document_id)
        return b

    @staticmethod
    def _failed(
        source: Union[str, Path],
        document_type: str,
        code: DocumentParserErrorCode,
        message: str,
        *,
        material_id: str = "",
        path_str: Optional[str] = None,
        file_size: int = 0,
    ) -> ParsedDocument:
        p = Path(source)
        if path_str is None:
            path_str = str(p)
        try:
            if file_size == 0:
                file_size = p.stat().st_size
        except OSError:
            pass
        document_id = _compute_document_stable_id(material_id, path_str, file_size, 0)
        return ParsedDocument(
            document_id=document_id,
            document_type=document_type,
            path=path_str,
            material_id=material_id,
            status=DocumentStatus.FAILED,
            errors=(ParserError(code, message),),
        )


class PDFDocumentParser(DocumentParser):
    """Deterministic PDF text extraction via pypdf.

    - one DocumentBlock per page (block_index always 0 in v1)
    - block_type is always TEXT (no heading inference)
    - scanned / no-text-layer PDF -> PARSED_EMPTY, no error
    - password-protected PDF -> FAILED + PASSWORD_PROTECTED
    - corrupt PDF -> FAILED + INVALID_DOCUMENT
    - pypdf missing -> FAILED + PARSER_UNAVAILABLE
    """

    document_type = "PDF"

    def parse(
        self,
        source: Union[str, Path],
        material_id: str = "",
    ) -> ParsedDocument:
        p = Path(source)

        # 1. Missing / unreadable file.
        try:
            st = p.stat()
        except OSError:
            return self._failed(
                p, "PDF", DocumentParserErrorCode.INVALID_DOCUMENT,
                "file not found or unreadable",
                material_id=material_id,
            )

        try:
            with open(p, "rb") as fh:
                raw = fh.read()
        except OSError as e:
            return self._failed(
                p, "PDF", DocumentParserErrorCode.INVALID_DOCUMENT,
                f"cannot read file: {e.strerror or e}",
                material_id=material_id,
            )

        # 2. Lazy pypdf import (PARSER_UNAVAILABLE if missing).
        try:
            from pypdf import PdfReader
            from pypdf.errors import (
                EmptyFileError,
                FileNotDecryptedError,
                ParseError,
                PdfReadError,
                PyPdfError,
                WrongPasswordError,
            )
        except Exception as e:  # pragma: no cover - only when pypdf missing
            return self._failed(
                p, "PDF", DocumentParserErrorCode.PARSER_UNAVAILABLE,
                f"pypdf import failed: {e}",
                material_id=material_id,
            )

        # 3. Open the reader.
        try:
            reader = PdfReader(io.BytesIO(raw), strict=False)
        except EmptyFileError:
            return self._failed(
                p, "PDF", DocumentParserErrorCode.INVALID_DOCUMENT,
                "zero-byte PDF file",
                material_id=material_id,
            )
        except WrongPasswordError:
            return self._failed(
                p, "PDF", DocumentParserErrorCode.PASSWORD_PROTECTED,
                "encrypted PDF without password",
                material_id=material_id,
            )
        except (ParseError, PdfReadError, PyPdfError) as e:
            return self._failed(
                p, "PDF", DocumentParserErrorCode.INVALID_DOCUMENT,
                f"unparseable PDF: {e}",
                material_id=material_id,
            )

        # 4. Encrypted-but-not-decrypted check.
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:
                pass
            if getattr(reader, "is_encrypted", False):
                return self._failed(
                    p, "PDF", DocumentParserErrorCode.PASSWORD_PROTECTED,
                    "PDF remains encrypted after empty-password attempt",
                    material_id=material_id,
                )

        # 5. Extract page by page.
        page_count = len(reader.pages)
        blocks: list[DocumentBlock] = []
        empty_page_count = 0
        document_id = _compute_document_stable_id(
            material_id, str(p), st.st_size, int(st.st_mtime_ns)
        )

        try:
            for page_idx in range(page_count):
                page = reader.pages[page_idx]
                text = (page.extract_text() or "").strip()
                page_no = page_idx + 1
                if text == "":
                    empty_page_count += 1
                blocks.append(
                    self._make_block(
                        document_id,
                        DocumentBlockType.TEXT,
                        text,
                        page_number=page_no,
                        block_index=0,
                    )
                )
        except FileNotDecryptedError:
            return self._failed(
                p, "PDF", DocumentParserErrorCode.PASSWORD_PROTECTED,
                "extraction blocked by encryption",
                material_id=material_id,
            )
        except PyPdfError as e:
            return self._failed(
                p, "PDF", DocumentParserErrorCode.INVALID_DOCUMENT,
                f"page extraction failed: {e}",
                material_id=material_id,
            )
        except Exception as e:
            return self._failed(
                p, "PDF", DocumentParserErrorCode.PARSER_ERROR,
                f"unexpected extraction error: {e}",
                material_id=material_id,
            )

        metadata: dict[str, Any] = {"page_count": page_count}
        if page_count > 0 and empty_page_count == page_count:
            metadata["scanned_or_no_text_layer"] = True

        if blocks and any(b.text for b in blocks):
            status = DocumentStatus.PARSED
        else:
            status = DocumentStatus.PARSED_EMPTY

        return ParsedDocument(
            document_id=document_id,
            document_type="PDF",
            path=str(p),
            material_id=material_id,
            status=status,
            blocks=blocks,
            metadata=metadata,
        )


class DOCXDocumentParser(DocumentParser):
    """Deterministic DOCX body extraction via python-docx.

    - one block per non-empty paragraph (paragraph_index advances past
      empty paragraphs)
    - HEADING only when the paragraph style name starts with 'Heading'
      (case-insensitive prefix match) or equals 'Title'
    - one TABLE block per table; text = cells joined with ' || ' per row
      and ' | ' between rows, whitespace stripped at both ends only
    - image parts counted (no OCR, no content), recorded in metadata
    - corrupt / non-OOXML docx -> FAILED + INVALID_DOCUMENT
    - python-docx missing -> FAILED + PARSER_UNAVAILABLE
    """

    document_type = "DOCX"

    @staticmethod
    def _is_heading_style(style_name: str) -> bool:
        n = style_name.strip().lower()
        return n.startswith("heading") or n == "title"

    def parse(
        self,
        source: Union[str, Path],
        material_id: str = "",
    ) -> ParsedDocument:
        p = Path(source)

        # 1. Missing / unreadable file.
        try:
            st = p.stat()
        except OSError:
            return self._failed(
                p, "DOCX", DocumentParserErrorCode.INVALID_DOCUMENT,
                "file not found or unreadable",
                material_id=material_id,
            )

        try:
            with open(p, "rb") as fh:
                raw = fh.read()
        except OSError as e:
            return self._failed(
                p, "DOCX", DocumentParserErrorCode.INVALID_DOCUMENT,
                f"cannot read file: {e.strerror or e}",
                material_id=material_id,
            )

        # 2. Lazy python-docx import.
        try:
            from docx import Document as DocxDocument
            from docx.opc.exceptions import PackageNotFoundError
        except Exception as e:  # pragma: no cover
            return self._failed(
                p, "DOCX", DocumentParserErrorCode.PARSER_UNAVAILABLE,
                f"python-docx import failed: {e}",
                material_id=material_id,
            )

        # 3. Open the package.
        try:
            doc = DocxDocument(io.BytesIO(raw))
        except PackageNotFoundError:
            return self._failed(
                p, "DOCX", DocumentParserErrorCode.INVALID_DOCUMENT,
                "not a valid OOXML package",
                material_id=material_id,
            )
        except zipfile.BadZipFile:
            return self._failed(
                p, "DOCX", DocumentParserErrorCode.INVALID_DOCUMENT,
                "not a valid ZIP container",
                material_id=material_id,
            )
        except Exception as e:
            return self._failed(
                p, "DOCX", DocumentParserErrorCode.PARSER_ERROR,
                f"docx open failed: {e}",
                material_id=material_id,
            )

        document_id = _compute_document_stable_id(
            material_id, str(p), st.st_size, int(st.st_mtime_ns)
        )

        blocks: list[DocumentBlock] = []

        # ---- paragraphs (in document order, index advances past empty) ----
        paragraph_index = 0
        for para in doc.paragraphs:
            text = (para.text or "").strip()
            style_name = ""
            try:
                style = para.style
                if style is not None:
                    style_name = str(getattr(style, "name", "") or "")
            except Exception:
                style_name = ""
            if text == "":
                paragraph_index += 1
                continue
            btype = (
                DocumentBlockType.HEADING
                if self._is_heading_style(style_name)
                else DocumentBlockType.TEXT
            )
            blocks.append(
                self._make_block(
                    document_id,
                    btype,
                    text,
                    paragraph_index=paragraph_index,
                    metadata={"style": style_name},
                )
            )
            paragraph_index += 1

        # ---- tables (in document order) ----
        table_index = 0
        for table in doc.tables:
            row_texts: list[str] = []
            for row in table.rows:
                cells = [
                    (cell.text or "").strip() for cell in row.cells
                ]
                row_texts.append(" || ".join(cells))
            text = " | ".join(row_texts).strip()
            blocks.append(
                self._make_block(
                    document_id,
                    DocumentBlockType.TABLE,
                    text,
                    metadata={"table_index": table_index},
                )
            )
            table_index += 1

        # ---- image part count (no OCR, no content) ----
        image_count = 0
        try:
            img_exts = (
                ".png", ".jpg", ".jpeg", ".gif", ".bmp",
                ".tiff", ".tif", ".webp", ".emf", ".wmf",
            )
            for part in doc.part.package.iter_parts():
                partname = str(getattr(part, "partname", "") or "")
                if "/media/" in partname and partname.lower().endswith(img_exts):
                    image_count += 1
        except Exception:
            image_count = -1

        metadata: dict[str, Any] = {"image_count": image_count}
        if any(b.block_type is DocumentBlockType.TABLE for b in blocks):
            metadata["table_count"] = len(
                [b for b in blocks if b.block_type is DocumentBlockType.TABLE]
            )

        if any(b.text for b in blocks):
            status = DocumentStatus.PARSED
        else:
            status = DocumentStatus.PARSED_EMPTY

        return ParsedDocument(
            document_id=document_id,
            document_type="DOCX",
            path=str(p),
            material_id=material_id,
            status=status,
            blocks=blocks,
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# Factory / top-level helpers
# ---------------------------------------------------------------------------


def create_document_parser(document_type: str) -> DocumentParser:
    """Return the concrete parser for ``"PDF"`` or ``"DOCX"`` (case-
    insensitive).  Raises ValueError for unknown types."""
    t = str(document_type or "").strip().upper()
    if t == "PDF":
        return PDFDocumentParser()
    if t == "DOCX":
        return DOCXDocumentParser()
    raise ValueError(f"unknown document_type: {document_type!r}")


def parse_document(
    source: Union[str, Path],
    document_type: Optional[str] = None,
    material_id: str = "",
) -> ParsedDocument:
    """Convenience wrapper: validate extension, dispatch to the matching
    parser, return a ParsedDocument.  Never raises for parser-level
    problems; raises ValueError for missing / non-file /
    unsupported-extension inputs.
    """
    p = Path(source)
    if not p.exists():
        raise ValueError(f"document not found: {p}")
    if not p.is_file():
        raise ValueError(f"not a file: {p}")
    ext = p.suffix.lower()
    if ext not in SUPPORTED_DOCUMENT_EXTENSIONS:
        raise ValueError(f"unsupported document extension: {ext}")
    dt = document_type or _EXT_TO_DOC_TYPE.get(ext, "")
    if not dt:
        raise ValueError(f"cannot determine document type for {ext}")
    parser = create_document_parser(dt)
    return parser.parse(p, material_id=material_id)


def validate_document(
    source: Union[str, Path],
    material: Optional[Material] = None,
) -> DocumentValidationResult:
    """File-level validation without running any parser.

    When ``material`` is a pre-indexed Material for the same file, its
    metadata and material_id are carried into the result (mirroring
    AudioMaterialValidator.validate()).
    """
    validator = DocumentMaterialValidator()
    if material is not None and (material.path or material.filename):
        return validator.validate(material)
    return validator.validate(source)