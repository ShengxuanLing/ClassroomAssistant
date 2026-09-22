"""Document -> Evidence extraction layer (Task 22).

Converts Task 21 ParsedDocument blocks into traceable Evidence
objects.  This module is deliberately thin:

    ParsedDocument / DocumentBlock
        |
    DocumentEvidenceExtractor
        |
    Evidence

Design rules (Task 22 spec):

- DocumentBlock.text is preserved verbatim in Evidence.content
  (strip() is used only to decide whether a block is empty).
- No semantic interpretation, no translation, no summarization,
  no LLM / OCR / ASR / embedding, no KnowledgePoint generation.
- No re-reading of the source file: the extractor only consumes the
  in-memory ParsedDocument produced by Task 21 parsers.
- No mutation: extract() leaves the ParsedDocument (and its blocks)
  byte-for-byte unchanged.
- No new third-party dependencies: stdlib only (hashlib, dataclasses).
- No file writes / network calls anywhere in this module.

Document Evidence identity
=========================

    evidence_id = "doc-evidence-" + sha256(
        document_id | document_type | block_location_key | text
    )[:24]

The identity is built from the same components that make up a
deterministic DocumentBlock id (document_id | block_type |
location_key | text), extended with document_type so that identical
blocks in a PDF and a DOCX document can never collide.  No uuid4,
no current time, no randomness.

Deduplication
=============

Exact-deterministic only: identical (document_id + location + text)
blocks map to the same evidence_id, so a repeated extract() call or
a repeated block produces one Evidence.  Blocks with the same text
but different locations are kept distinct.  No fuzzy / semantic /
embedding deduplication is performed.

Source traceability
==================

Each Evidence carries:

- source_reference: built from DocumentBlock.to_source_reference()
  (page / paragraph / line / location) with material_id falling back
  to the ParsedDocument material_id, then to the document_id.
- metadata (minimal, no full block copy):
    - block_id      : the deterministic DocumentBlock identity
    - block_type    : "TEXT" / "PARAGRAPH" / "HEADING" / "TABLE"
    - document_type : "PDF" / "DOCX"
    - document_id   : the Task 21 document identity
    - location      : human-readable source location

Status semantics
================

- ParsedDocument.status == FAILED       -> []
- ParsedDocument.status == PARSED_EMPTY -> []
- ParsedDocument.status == PARSED       -> one Evidence per non-empty block, in block order

Input validation
================

extract() accepts ParsedDocument or a ParsedDocument.to_dict()
mapping (re-hydrated via ParsedDocument.from_dict()).  Any other
type raises a structured DocumentEvidenceError (never a raw
AttributeError).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Union

from src.document_input import (
    DocumentBlock,
    DocumentBlockType,
    DocumentStatus,
    ParsedDocument,
)
from src.models import (
    Evidence,
    EvidenceType,
    Language,
    SourceReference,
)

__all__ = [
    "DOCUMENT_EVIDENCE_PREFIX",
    "DOCUMENT_EVIDENCE_ID_LENGTH",
    "DocumentEvidenceError",
    "DocumentEvidenceStatus",
    "DocumentEvidenceResult",
    "DocumentEvidenceExtractor",
    "block_evidence_id",
    "extract_document_evidence",
]

# Deterministic document-evidence identity (Task 22 spec section 23/25):
# same 24-char hex suffix as DocumentBlock ids, no uuid4.
DOCUMENT_EVIDENCE_PREFIX = "doc-evidence-"
DOCUMENT_EVIDENCE_ID_LENGTH = 24

_INPUT_TYPES: tuple[type, ...] = (ParsedDocument, dict, type(None))


# ---------------------------------------------------------------------------
# Deterministic evidence id
# ---------------------------------------------------------------------------

def block_evidence_id(
    document_id: str,
    document_type: str,
    block_type: str,
    location_key: str,
    text: str,
) -> str:
    """Deterministic Evidence id from document identity + block location + text.

    Components:
        document_id   - Task 21 document identity ("document-...")
        document_type - "PDF" / "DOCX"
        block_type    - DocumentBlockType value
        location_key  - DocumentBlock._location_key() semantics
        text          - exact block text

    Output: "doc-evidence-" + sha256(pipeline)[:24].
    """
    raw = (
        f"{document_id}|{document_type}|{block_type}|"
        f"{location_key}|{text}"
    ).encode("utf-8")
    return DOCUMENT_EVIDENCE_PREFIX + hashlib.sha256(raw).hexdigest()[:DOCUMENT_EVIDENCE_ID_LENGTH]


def _location_key(block: DocumentBlock) -> str:
    """Reproduce DocumentBlock._location_key() semantics without mutating the block."""
    if block.page_number is not None:
        return f"page:{block.page_number}"
    if block.paragraph_index is not None:
        return f"para:{block.paragraph_index}"
    return "none"


def _location_label(block: DocumentBlock, document_type: str) -> str:
    """Human-readable source location label for Evidence.metadata['location']."""
    if document_type == "PDF" and block.page_number is not None:
        return f"pdf-page-{block.page_number}-block-{block.block_index or 0}"
    if block.paragraph_index is not None:
        return f"docx-paragraph-{block.paragraph_index}"
    if block.block_type is DocumentBlockType.TABLE:
        table_index = block.metadata.get("table_index")
        if table_index is not None:
            return f"docx-table-{table_index}"
        return "docx-table"
    return "unknown-location"


# ---------------------------------------------------------------------------
# Structured input errors
# ---------------------------------------------------------------------------

class DocumentEvidenceError(ValueError):
    """Raised when extract() receives a non-ParsedDocument / non-dict input."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

class DocumentEvidenceStatus(str, Enum):
    """Extraction outcome for one ParsedDocument."""

    EXTRACTED = "EXTRACTED"          # at least one Evidence produced
    NO_EVIDENCE = "NO_EVIDENCE"      # PARSED_EMPTY / FAILED / all blocks empty
    INVALID_INPUT = "INVALID_INPUT"  # input not a ParsedDocument / dict


@dataclass
class DocumentEvidenceResult:
    """Structured outcome of one DocumentEvidenceExtractor.extract() call.

    Fields:
        evidences - deterministic, block-ordered, deduplicated Evidence list
        status    - DocumentEvidenceStatus
        error     - human-readable reason when status is INVALID_INPUT
        statistics- deterministic counters, e.g. skipped_empty_blocks
    """

    evidences: list[Evidence] = field(default_factory=list)
    status: DocumentEvidenceStatus = DocumentEvidenceStatus.NO_EVIDENCE
    error: str = ""
    statistics: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidences": [ev.to_dict() for ev in self.evidences],
            "status": self.status.value,
            "error": self.error,
            "statistics": dict(self.statistics),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DocumentEvidenceResult":
        evidences = [
            ev if isinstance(ev, Evidence) else Evidence.from_dict(ev)
            for ev in data.get("evidences", [])
        ]
        status_str = str(data.get("status", DocumentEvidenceStatus.NO_EVIDENCE.value))
        try:
            status = DocumentEvidenceStatus(status_str)
        except ValueError:
            status = DocumentEvidenceStatus.INVALID_INPUT
        return cls(
            evidences=evidences,
            status=status,
            error=str(data.get("error", "")),
            statistics=dict(data.get("statistics", {})),
        )


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

class DocumentEvidenceExtractor:
    """Converts Task 21 ParsedDocument blocks into traceable Evidence.

    Usage::

        extractor = DocumentEvidenceExtractor()
        evidences = extractor.extract(parsed_document)
        result = extractor.extract_as_result(parsed_document)

    Guarantees:
        - deterministic ids and ordering (document order, not text/hash/length order)
        - exact-text preservation (DocumentBlock.text == Evidence.content)
        - no mutation of the input ParsedDocument / blocks
        - no re-read of the source file, no OCR / ASR / LLM / network
    """

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def extract(self, parsed_document: Union[ParsedDocument, dict, None]) -> list[Evidence]:
        """Return the block-ordered, deduplicated Evidence list for one document.

        ParsedDocument: normal status.
        dict: re-hydrated via ParsedDocument.from_dict().
        None / other types: returns [] (no Evidence; no raise).
        """
        return self.extract_as_result(parsed_document).evidences

    def extract_as_result(
        self, parsed_document: Union[ParsedDocument, dict, None]
    ) -> DocumentEvidenceResult:
        """Extract and return the full DocumentEvidenceResult (with status + statistics)."""
        if parsed_document is None:
            return DocumentEvidenceResult(
                status=DocumentEvidenceStatus.INVALID_INPUT,
                error="parsed_document is None",
            )
        if isinstance(parsed_document, ParsedDocument):
            document = parsed_document
        elif isinstance(parsed_document, dict):
            document = ParsedDocument.from_dict(parsed_document)
        else:
            return DocumentEvidenceResult(
                status=DocumentEvidenceStatus.INVALID_INPUT,
                error=(
                    "expected ParsedDocument, dict, or None; got "
                    f"{type(parsed_document).__name__}"
                ),
            )

        doc = document

        if doc.status is DocumentStatus.FAILED:
            return DocumentEvidenceResult(
                status=DocumentEvidenceStatus.NO_EVIDENCE,
                error="ParsedDocument.status == FAILED; no Evidence generated",
                statistics={
                    "blocks_seen": len(doc.blocks),
                    "skipped_empty_blocks": 0,
                    "evidences_generated": 0,
                },
            )

        evidences: list[Evidence] = []
        seen_ids: set[str] = set()
        skipped_empty = 0

        for block in doc.blocks:
            # Empty block: no Evidence (strip used only for the emptiness check).
            if not block.text or not block.text.strip():
                skipped_empty += 1
                continue

            evidence = self._block_to_evidence(block, doc)
            if evidence.evidence_id not in seen_ids:
                seen_ids.add(evidence.evidence_id)
                evidences.append(evidence)

        status = (
            DocumentEvidenceStatus.EXTRACTED
            if evidences
            else DocumentEvidenceStatus.NO_EVIDENCE
        )
        return DocumentEvidenceResult(
            evidences=evidences,
            status=status,
            statistics={
                "blocks_seen": len(doc.blocks),
                "skipped_empty_blocks": skipped_empty,
                "evidences_generated": len(evidences),
            },
        )

    # ------------------------------------------------------------------
    # block -> Evidence mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _block_to_evidence(block: DocumentBlock, doc: ParsedDocument) -> Evidence:
        document_type = doc.document_type or ""
        location_key = _location_key(block)
        evidence_id = block_evidence_id(
            doc.document_id,
            document_type,
            block.block_type.value,
            location_key,
            block.text,
        )

        material_id = doc.material_id or doc.document_id
        ref = block.to_source_reference(material_id)
        source_ref = SourceReference(
            material_id=ref.get("material_id", material_id),
            location=ref.get("location"),
            timestamp_start=ref.get("timestamp_start"),
            timestamp_end=ref.get("timestamp_end"),
            page=ref.get("page"),
            line=ref.get("line"),
            paragraph=ref.get("paragraph"),
        )

        metadata: dict[str, Any] = {
            "block_id": block.block_id,
            "block_type": block.block_type.value,
            "document_type": document_type,
            "document_id": doc.document_id,
            "location": _location_label(block, document_type),
        }

        return Evidence(
            evidence_id=evidence_id,
            content=block.text,
            language=Language.UNKNOWN,
            source_reference=source_ref,
            evidence_type=EvidenceType.DOCUMENT,
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def extract_document_evidence(
    parsed_document: Union[ParsedDocument, dict, None],
) -> list[Evidence]:
    """Module-level shortcut: build a DocumentEvidenceExtractor and extract.

    Accepts ParsedDocument, ParsedDocument.to_dict(), or None.  Returns []
    for None / invalid input; raises nothing for those cases.
    """
    extractor = DocumentEvidenceExtractor()
    return extractor.extract(parsed_document)
