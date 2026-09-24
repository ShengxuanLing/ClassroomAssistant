"""Unified Evidence Ingestion service for the Classroom Assistant project (Task 24).

Orchestrates extraction from all supported material sources (Note, Audio,
OCR/Image, PDF, DOCX) through the existing extractors and writes the
produced Evidence into the single authoritative EvidenceStore (Task 23).

Design rules (Task 24 spec):

- Extractors extract; the ingestion service orchestrates; the store persists
  and deduplicates; the knowledge pipeline interprets later.
- Source routing is deterministic (extension-first, MaterialType fallback).
- Extractor adapters are a thin boundary: parameter conversion, validator
  calls, extractor invocation, output normalization.  No semantic
  transformation, rewriting, translation, deduplication, Knowledge
  generation, or persistence inside the adapters.
- EvidenceStore is the sole authoritative dedup layer: ingestion always
  hands Evidence[] to store.add_many() and never re-implements content
  identity checks.
- Evidence content and provenance are passed through untouched.
- Failure isolation: batch/session ingestion isolates failures per material;
  one failing material never aborts the others.
- Reports are deterministic: no wall-clock timestamps, no random UUIDs, no
  filesystem enumeration order participate in business identity.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.models import (
    ClassSession,
    Evidence,
    Language,
    Material,
    MaterialType,
    SourceReference,
    Transcript,
)
from src.evidence_store import EvidenceStore

__all__ = [
    "IngestionStatus",
    "IngestionErrorCode",
    "IngestionError",
    "IngestionReport",
    "BatchIngestionReport",
    "IngestionHistoryEntry",
    "IngestionHistory",
    "IngestionStatistics",
    "ExtractorAdapter",
    "NoteExtractorAdapter",
    "AudioExtractorAdapter",
    "OCRExtractorAdapter",
    "DocumentExtractorAdapter",
    "EvidenceIngestionService",
    "resolve_source_type",
]

# ---------------------------------------------------------------------------
# Source routing
# ---------------------------------------------------------------------------

SOURCE_NOTE = "note"
SOURCE_AUDIO = "audio"
SOURCE_OCR = "ocr"
SOURCE_PDF = "pdf"
SOURCE_DOCX = "docx"
SOURCE_UNSUPPORTED = "unsupported"

_NOTE_EXTENSIONS = {".txt", ".md", ".markdown"}
_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".wma", ".aac", ".opus"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}


def resolve_source_type(material: Material) -> str:
    """Deterministic source routing discriminator for a Material.

    Extension-first, then MaterialType fallback.  Returns one of the
    SOURCE_* constants.  Never invents a second taxonomy; unknown
    inputs map to SOURCE_UNSUPPORTED.
    """
    ext = Path(material.path or material.filename or "").suffix.lower()
    if ext in _NOTE_EXTENSIONS:
        return SOURCE_NOTE
    if ext in _AUDIO_EXTENSIONS:
        return SOURCE_AUDIO
    if ext in _IMAGE_EXTENSIONS:
        return SOURCE_OCR
    if ext == ".pdf":
        return SOURCE_PDF
    if ext == ".docx":
        return SOURCE_DOCX
    mtype = material.material_type
    if isinstance(mtype, str):
        mtype = MaterialType.from_string(mtype)
    if mtype == MaterialType.AUDIO:
        return SOURCE_AUDIO
    if mtype == MaterialType.IMAGE:
        return SOURCE_OCR
    if mtype in (MaterialType.NOTE, MaterialType.SYLLABUS):
        return SOURCE_NOTE
    return SOURCE_UNSUPPORTED


# ---------------------------------------------------------------------------
# Status and error model
# ---------------------------------------------------------------------------

class IngestionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class IngestionErrorCode(str, Enum):
    UNSUPPORTED_SOURCE = "UNSUPPORTED_SOURCE"
    EXTRACTOR_ERROR = "EXTRACTOR_ERROR"
    ASR_ERROR = "ASR_ERROR"
    MALFORMED_EVIDENCE = "MALFORMED_EVIDENCE"
    STORE_REJECTION = "STORE_REJECTION"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    @property
    def retryable(self) -> bool:
        # Extraction-level transient failures (ASR outage) are retryable
        # by upstream contract; unsupported/validation failures are not.
        return self in (IngestionErrorCode.ASR_ERROR, IngestionErrorCode.STORE_REJECTION)


@dataclass(frozen=True)
class IngestionError:
    """Structured error record embedded in an IngestionReport."""

    code: str
    message: str
    fatal: bool = False
    evidence_index: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "fatal": self.fatal,
            "evidence_index": self.evidence_index,
        }


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IngestionReport:
    """Deterministic outcome of ingesting one material.

    No wall-clock timestamps participate in identity; equality is over
    business fields only.
    """

    status: IngestionStatus
    material_id: str
    source_type: str
    total_extracted: int
    added_count: int
    duplicate_count: int
    rejected_count: int
    skipped_count: int
    evidence_ids: Tuple[str, ...]
    errors: Tuple[IngestionError, ...]
    dry_run: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status in (IngestionStatus.SUCCESS, IngestionStatus.PARTIAL)

    def summary(self) -> str:
        """Human-readable diagnostic summary (derived data only)."""
        parts = [f"{self.total_extracted} extracted"]
        if not self.dry_run:
            parts.append(f"{self.added_count} added")
        parts.append(f"{self.duplicate_count} duplicate")
        parts.append(f"{self.rejected_count} rejected")
        suffix = "" if self.status == IngestionStatus.SUCCESS else f" ({self.status.value})"
        return ", ".join(parts) + suffix

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "material_id": self.material_id,
            "source_type": self.source_type,
            "total_extracted": self.total_extracted,
            "added_count": self.added_count,
            "duplicate_count": self.duplicate_count,
            "rejected_count": self.rejected_count,
            "skipped_count": self.skipped_count,
            "evidence_ids": list(self.evidence_ids),
            "errors": [e.to_dict() for e in self.errors],
            "dry_run": self.dry_run,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IngestionReport":
        errors = tuple(
            IngestionError(
                code=str(e.get("code", "")),
                message=str(e.get("message", "")),
                fatal=bool(e.get("fatal", False)),
                evidence_index=e.get("evidence_index"),
            )
            for e in data.get("errors", [])
        )
        return cls(
            status=IngestionStatus(str(data.get("status", IngestionStatus.FAILED.value))),
            material_id=str(data.get("material_id", "")),
            source_type=str(data.get("source_type", SOURCE_UNSUPPORTED)),
            total_extracted=int(data.get("total_extracted", 0)),
            added_count=int(data.get("added_count", 0)),
            duplicate_count=int(data.get("duplicate_count", 0)),
            rejected_count=int(data.get("rejected_count", 0)),
            skipped_count=int(data.get("skipped_count", 0)),
            evidence_ids=tuple(str(x) for x in data.get("evidence_ids", [])),
            errors=errors,
            dry_run=bool(data.get("dry_run", False)),
        )


@dataclass(frozen=True)
class BatchIngestionReport:
    """Aggregate of per-material reports; every aggregate is derived."""

    reports: Tuple[IngestionReport, ...]

    @property
    def total_materials(self) -> int:
        return len(self.reports)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.reports if r.status == IngestionStatus.SUCCESS)

    @property
    def partially_succeeded(self) -> int:
        return sum(1 for r in self.reports if r.status == IngestionStatus.PARTIAL)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.reports if r.status == IngestionStatus.FAILED)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.reports if r.status == IngestionStatus.SKIPPED)

    @property
    def total_extracted(self) -> int:
        return sum(r.total_extracted for r in self.reports)

    @property
    def total_added(self) -> int:
        return sum(r.added_count for r in self.reports)

    @property
    def total_duplicates(self) -> int:
        return sum(r.duplicate_count for r in self.reports)

    @property
    def total_rejected(self) -> int:
        return sum(r.rejected_count for r in self.reports)

    def report_for(self, material_id: str) -> Optional[IngestionReport]:
        """Last report for the given material (input order preserved)."""
        found: Optional[IngestionReport] = None
        for r in self.reports:
            if r.material_id == material_id:
                found = r
        return found

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_materials": self.total_materials,
            "succeeded": self.succeeded,
            "partially_succeeded": self.partially_succeeded,
            "failed": self.failed,
            "skipped": self.skipped,
            "total_extracted": self.total_extracted,
            "total_added": self.total_added,
            "total_duplicates": self.total_duplicates,
            "total_rejected": self.total_rejected,
            "reports": [r.to_dict() for r in self.reports],
        }


# ---------------------------------------------------------------------------
# Ingestion history (lightweight, deterministic, NOT Evidence)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IngestionHistoryEntry:
    """One ingestion attempt for one material (immutable snapshot)."""

    material_id: str
    status: IngestionStatus
    source_type: str
    attempt_count: int
    evidence_ids: Tuple[str, ...]
    error_code: str
    error_message: str
    dry_run: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "material_id": self.material_id,
            "status": self.status.value,
            "source_type": self.source_type,
            "attempt_count": self.attempt_count,
            "evidence_ids": list(self.evidence_ids),
            "error_code": self.error_code,
            "error_message": self.error_message,
            "dry_run": self.dry_run,
        }


class IngestionHistory:
    """Per-material attempt log.  NOT Evidence and NOT part of canonical
    Evidence identity.  Guarded by an RLock; entries are immutable."""

    SCHEMA_VERSION = 1

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: Dict[str, List[IngestionHistoryEntry]] = {}

    def record(self, report: IngestionReport) -> IngestionHistoryEntry:
        with self._lock:
            prior = self._entries.get(report.material_id)
            attempt = 1 if not prior else prior[-1].attempt_count + 1
            error_code = ""
            error_message = ""
            if report.errors:
                fatal = [e for e in report.errors if e.fatal]
                target = fatal[0] if fatal else report.errors[-1]
                error_code = target.code
                error_message = target.message
            entry = IngestionHistoryEntry(
                material_id=report.material_id,
                status=report.status,
                source_type=report.source_type,
                attempt_count=attempt,
                evidence_ids=report.evidence_ids,
                error_code=error_code,
                error_message=error_message,
                dry_run=report.dry_run,
            )
            self._entries.setdefault(report.material_id, []).append(entry)
            return entry

    def history_for(self, material_id: str) -> Tuple[IngestionHistoryEntry, ...]:
        with self._lock:
            return tuple(self._entries.get(material_id, ()))

    def status_for(self, material_id: str) -> Optional[IngestionStatus]:
        with self._lock:
            entries = self._entries.get(material_id)
            if not entries:
                return None
            return entries[-1].status

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "schema_version": self.SCHEMA_VERSION,
                "entries": [
                    {
                        "material_id": mid,
                        "history": [e.to_dict() for e in entries],
                    }
                    for mid, entries in self._entries.items()
                ],
            }


# ---------------------------------------------------------------------------
# Statistics (derived, deterministic)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IngestionStatistics:
    total_materials: int
    successful_materials: int
    partial_materials: int
    failed_materials: int
    skipped_materials: int
    total_extracted: int
    total_added: int
    total_duplicates: int
    total_rejected: int
    total_errors: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_materials": self.total_materials,
            "successful_materials": self.successful_materials,
            "partial_materials": self.partial_materials,
            "failed_materials": self.failed_materials,
            "skipped_materials": self.skipped_materials,
            "total_extracted": self.total_extracted,
            "total_added": self.total_added,
            "total_duplicates": self.total_duplicates,
            "total_rejected": self.total_rejected,
            "total_errors": self.total_errors,
        }


# ---------------------------------------------------------------------------
# Extractor adapters (thin boundary around existing extractors)
# ---------------------------------------------------------------------------

class ExtractorAdapter:
    """Base boundary: extract(material) -> list[Evidence].

    Returning None signals a *structured skip* for this material under
    this adapter (e.g., the audio file is invalid).  Raising signals an
    extractor failure.  Adapters may convert parameters, call existing
    validators, and invoke existing extractors.  They must not do
    semantic transformation, dedup, translation, or persistence.
    """

    source_type: str = ""

    def extract(self, material: Material) -> Optional[List[Evidence]]:
        raise NotImplementedError


class NoteExtractorAdapter(ExtractorAdapter):
    """Note routing: TXT/Markdown via the existing NoteParser."""

    source_type = SOURCE_NOTE

    def extract(self, material: Material) -> Optional[List[Evidence]]:
        from src.note_parser import NoteParser

        parser = NoteParser(material)
        evidences = parser.parse()
        # Mirror EvidenceExtractor behavior: overwrite language only when
        # the material explicitly declares one.  Content is untouched.
        if material.language != Language.UNKNOWN:
            for ev in evidences:
                ev.language = material.language
        return evidences


class AudioExtractorAdapter(ExtractorAdapter):
    """Audio routing: validator -> AudioInput -> ASRProvider -> Transcript
    -> single Transcript Evidence.  The ASR provider is injectable; the
    default is the MockASRProvider already used project-wide."""

    source_type = SOURCE_AUDIO

    def __init__(self, asr_provider: Optional[Any] = None) -> None:
        if asr_provider is None:
            from src.asr_provider import MockASRProvider

            asr_provider = MockASRProvider()
        self._asr = asr_provider

    def extract(self, material: Material) -> Optional[List[Evidence]]:
        from src.audio_input import AudioMaterialValidator
        from src.asr_provider import ASRProviderError
        from src.transcription import transcript_to_evidence

        validator = AudioMaterialValidator()
        result = validator.validate(material)
        if not result.valid:
            # Structured skip: missing / unsupported / empty audio file.
            return None
        audio_input = validator.to_audio_input(result)
        try:
            transcription = self._asr.transcribe(audio_input)
        except ASRProviderError as exc:
            raise _ASRFailureError(str(getattr(exc, "error_code", None)), str(exc))
        if not transcription.ok or transcription.transcript is None:
            first = transcription.errors[0] if transcription.errors else {}
            code = first.get("code") if isinstance(first, dict) else None
            message = first.get("message", "") if isinstance(first, dict) else str(first)
            raise _ASRFailureError(code, message)
        transcript: Transcript = transcription.transcript
        # Re-key the transcript's material reference to THIS material so
        # provenance is carried into the store (content untouched).
        if transcript.material_id != material.material_id:
            transcript.material_id = material.material_id
        return [transcript_to_evidence(transcript)]


class OCRExtractorAdapter(ExtractorAdapter):
    """Image routing: OCREngine -> OCRResult -> Evidence[] via the
    existing ocr_to_evidence.  The engine is injectable; the default is
    MockOCREngine."""

    source_type = SOURCE_OCR

    def __init__(self, ocr_engine: Optional[Any] = None) -> None:
        if ocr_engine is None:
            from src.ocr_processor import MockOCREngine

            ocr_engine = MockOCREngine()
        self._ocr = ocr_engine

    def extract(self, material: Material) -> Optional[List[Evidence]]:
        from src.ocr_processor import ocr_to_evidence

        ocr_result = self._ocr.ocr(material)
        return ocr_to_evidence(ocr_result)


class DocumentExtractorAdapter(ExtractorAdapter):
    """PDF/DOCX routing: parse_document + DocumentEvidenceExtractor
    (Task 21/22).  Empty / failed documents yield [] (SUCCESS with 0)."""

    source_type = SOURCE_PDF

    def extract(self, material: Material) -> Optional[List[Evidence]]:
        from src.document_input import parse_document
        from src.document_evidence import DocumentEvidenceExtractor

        parsed = parse_document(material.path, material_id=material.material_id)
        return DocumentEvidenceExtractor().extract(parsed)


class _ASRFailureError(Exception):
    """Structured ASR provider failure surfaced by the audio adapter."""

    def __init__(self, code: Optional[str], message: str) -> None:
        super().__init__(f"ASR provider failed: {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UUID4_FALLBACK_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _is_uuid4_fallback_id(evidence_id: str) -> bool:
    """True when the id looks like the model's uuid4() fallback.

    Evidence.__post_init__ fills a random uuid4 when the extractor left
    evidence_id empty.  Such ids are NOT business-identical across
    runs (spec section 39 forbids random values in report identity),
    so the ingestion layer re-scaffolds them deterministically.
    A regex avoids the pitfall that a *deterministic* hash-16 id built
    from 12 hex chars happens to parse as a uuid4 UUID object.
    """
    if not isinstance(evidence_id, str):
        return False
    return _UUID4_FALLBACK_RE.match(evidence_id) is not None


def _ensure_evidence_ref(evidence: Evidence, material: Material) -> Evidence:
    """Fill ONLY missing provenance scaffolding (material_id / id).

    Content, existing source fields, and metadata are never mutated.
    When the extractor left evidence_id empty OR filled it with the
    model's random uuid4 fallback, the id is derived deterministically
    from evidence type + source + location + content so repeated
    ingestion yields identical report evidence_ids (no wall-clock, no
    uuid4, no random).
    """
    ref = evidence.source_reference
    if ref is None:
        ref = SourceReference(material_id=material.material_id)
        evidence.source_reference = ref
    elif not ref.material_id:
        ref.material_id = material.material_id
    if not evidence.evidence_id or _is_uuid4_fallback_id(evidence.evidence_id):
        import hashlib

        etype = getattr(evidence.evidence_type, "value", evidence.evidence_type)
        raw = "|".join(
            [
                str(etype),
                ref.material_id,
                str(ref.location),
                str(ref.timestamp_start),
                str(ref.timestamp_end),
                str(ref.page),
                str(ref.line),
                str(ref.paragraph),
                evidence.content,
            ]
        )
        evidence.evidence_id = "evid-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return evidence


class _SessionTaggingAdapter:
    """Wraps an ExtractorAdapter, stamping class_session_id into Evidence
    metadata only when the extractor left it absent.  Metadata never
    participates in canonical Evidence identity, so idempotency holds."""

    def __init__(self, inner: ExtractorAdapter, session_id: str) -> None:
        self._inner = inner
        self._session_id = session_id

    @property
    def source_type(self) -> str:
        return self._inner.source_type

    def extract(self, material: Material) -> Optional[List[Evidence]]:
        evidences = self._inner.extract(material)
        if evidences is None:
            return None
        for ev in evidences:
            if isinstance(ev, Evidence) and "class_session_id" not in ev.metadata:
                ev.metadata["class_session_id"] = self._session_id
        return evidences


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class EvidenceIngestionService:
    """Unified Evidence ingestion entry point (Task 24).

    Usage::

        service = EvidenceIngestionService(store)
        report = service.ingest(material)
        batch = service.ingest_many(materials)
        report = service.ingest_session(session, materials)

    - store: the authoritative EvidenceStore (injected, never global).
    - extractors: optional {source_type: ExtractorAdapter} overrides for
      tests and future providers.
    """

    def __init__(
        self,
        store: EvidenceStore,
        *,
        extractors: Optional[Mapping[str, ExtractorAdapter]] = None,
        asr_provider: Optional[Any] = None,
        ocr_engine: Optional[Any] = None,
    ) -> None:
        if store is None:
            raise ValueError("EvidenceIngestionService requires an injected EvidenceStore")
        self._store = store
        defaults: Dict[str, ExtractorAdapter] = {
            SOURCE_NOTE: NoteExtractorAdapter(),
            SOURCE_AUDIO: AudioExtractorAdapter(asr_provider),
            SOURCE_OCR: OCRExtractorAdapter(ocr_engine),
            SOURCE_PDF: DocumentExtractorAdapter(),
            SOURCE_DOCX: DocumentExtractorAdapter(),
        }
        if extractors:
            for key, adapter in extractors.items():
                defaults[key] = adapter
        self._extractors = defaults
        self._history = IngestionHistory()
        self._reports: Dict[str, List[IngestionReport]] = {}
        self._reports_lock = threading.RLock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def store(self) -> EvidenceStore:
        return self._store

    def ingest(self, material: Material, dry_run: bool = False) -> IngestionReport:
        """Ingest one material; always returns a structured report."""
        if not isinstance(material, Material):
            report = IngestionReport(
                status=IngestionStatus.FAILED,
                material_id=str(material),
                source_type=SOURCE_UNSUPPORTED,
                total_extracted=0,
                added_count=0,
                duplicate_count=0,
                rejected_count=0,
                skipped_count=0,
                evidence_ids=(),
                errors=(
                    IngestionError(
                        code=IngestionErrorCode.INTERNAL_ERROR.value,
                        message=(
                            f"ingest() requires a Material, "
                            f"got {type(material).__name__}"
                        ),
                        fatal=True,
                    ),
                ),
                dry_run=dry_run,
            )
            self._record(report)
            return report

        source_type = resolve_source_type(material)
        report = self._ingest_one(material, source_type, dry_run)
        self._record(report)
        return report

    def ingest_many(self, materials: Iterable[Material]) -> BatchIngestionReport:
        """Sequential per-material ingestion with failure isolation."""
        reports: List[IngestionReport] = []
        for material in materials:
            if not isinstance(material, Material):
                bad = IngestionReport(
                    status=IngestionStatus.FAILED,
                    material_id=str(material),
                    source_type=SOURCE_UNSUPPORTED,
                    total_extracted=0,
                    added_count=0,
                    duplicate_count=0,
                    rejected_count=0,
                    skipped_count=0,
                    evidence_ids=(),
                    errors=(
                        IngestionError(
                            code=IngestionErrorCode.INTERNAL_ERROR.value,
                            message=f"non-Material entry in batch: {type(material).__name__}",
                            fatal=True,
                        ),
                    ),
                )
                self._record(bad)
                reports.append(bad)
                continue
            source_type = resolve_source_type(material)
            report = self._ingest_one(material, source_type, False)
            self._record(report)
            reports.append(report)
        return BatchIngestionReport(reports=tuple(reports))

    def ingest_session(
        self,
        session: ClassSession,
        materials: Sequence[Material],
    ) -> BatchIngestionReport:
        """Session-level ingestion with deterministic ordering by
        (material_id, filename).  Supported materials are ingested;
        unsupported ones yield SKIPPED reports (isolation preserved)."""
        ordered = sorted(
            list(materials),
            key=lambda m: (getattr(m, "material_id", ""), getattr(m, "filename", "")),
        )
        overrides: Dict[str, ExtractorAdapter] = {}
        for key, adapter in self._extractors.items():
            overrides[key] = _SessionTaggingAdapter(adapter, session.session_id)
        service = EvidenceIngestionService(self._store, extractors=overrides)
        # Share history / report state with the primary instance.
        service._history = self._history
        service._reports = self._reports
        service._reports_lock = self._reports_lock
        return service.ingest_many(ordered)

    def statistics(self) -> IngestionStatistics:
        """Derived, deterministic statistics over all recorded reports."""
        with self._reports_lock:
            all_reports = [r for rs in self._reports.values() for r in rs]
        return IngestionStatistics(
            total_materials=len(all_reports),
            successful_materials=sum(
                1 for r in all_reports if r.status == IngestionStatus.SUCCESS
            ),
            partial_materials=sum(
                1 for r in all_reports if r.status == IngestionStatus.PARTIAL
            ),
            failed_materials=sum(
                1 for r in all_reports if r.status == IngestionStatus.FAILED
            ),
            skipped_materials=sum(
                1 for r in all_reports if r.status == IngestionStatus.SKIPPED
            ),
            total_extracted=sum(r.total_extracted for r in all_reports),
            total_added=sum(r.added_count for r in all_reports),
            total_duplicates=sum(r.duplicate_count for r in all_reports),
            total_rejected=sum(r.rejected_count for r in all_reports),
            total_errors=sum(len(r.errors) for r in all_reports),
        )

    def report_for(self, material_id: str) -> Optional[IngestionReport]:
        """Read-only inspection of the latest report for a material."""
        with self._reports_lock:
            entries = self._reports.get(material_id)
            return entries[-1] if entries else None

    def status_for(self, material_id: str) -> Optional[IngestionStatus]:
        with self._reports_lock:
            entries = self._reports.get(material_id)
            return entries[-1].status if entries else None

    def history_for(self, material_id: str) -> Tuple[IngestionHistoryEntry, ...]:
        return self._history.history_for(material_id)

    def history(self) -> IngestionHistory:
        return self._history

    def to_dict(self) -> Dict[str, Any]:
        return {
            "statistics": self.statistics().to_dict(),
            "history": self._history.to_dict(),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _record(self, report: IngestionReport) -> None:
        self._history.record(report)
        with self._reports_lock:
            self._reports.setdefault(report.material_id, []).append(report)

    def _ingest_one(
        self,
        material: Material,
        source_type: str,
        dry_run: bool,
    ) -> IngestionReport:
        if source_type == SOURCE_UNSUPPORTED:
            return IngestionReport(
                status=IngestionStatus.SKIPPED,
                material_id=material.material_id,
                source_type=source_type,
                total_extracted=0,
                added_count=0,
                duplicate_count=0,
                rejected_count=0,
                skipped_count=1,
                evidence_ids=(),
                errors=(
                    IngestionError(
                        code=IngestionErrorCode.UNSUPPORTED_SOURCE.value,
                        message=(
                            f"unsupported material {material.material_id!r} "
                            f"(material_type={getattr(material.material_type, 'value', material.material_type)})"
                        ),
                        fatal=False,
                    ),
                ),
                dry_run=dry_run,
            )

        adapter = self._extractors.get(source_type)
        if adapter is None:
            return IngestionReport(
                status=IngestionStatus.SKIPPED,
                material_id=material.material_id,
                source_type=source_type,
                total_extracted=0,
                added_count=0,
                duplicate_count=0,
                rejected_count=0,
                skipped_count=1,
                evidence_ids=(),
                errors=(
                    IngestionError(
                        code=IngestionErrorCode.UNSUPPORTED_SOURCE.value,
                        message=f"no extractor registered for source {source_type!r}",
                        fatal=False,
                    ),
                ),
                dry_run=dry_run,
            )

        # --- Extraction (adapter boundary; failures become structured) ---
        extraction_errors: List[IngestionError] = []
        evidences: List[Evidence] = []
        malformed: List[Any] = []
        extractor_failed = False
        try:
            raw = adapter.extract(material)
            if raw is None:
                return IngestionReport(
                    status=IngestionStatus.SKIPPED,
                    material_id=material.material_id,
                    source_type=source_type,
                    total_extracted=0,
                    added_count=0,
                    duplicate_count=0,
                    rejected_count=0,
                    skipped_count=1,
                    evidence_ids=(),
                    errors=(
                        IngestionError(
                            code=IngestionErrorCode.EXTRACTOR_ERROR.value,
                            message=(
                                f"{source_type} adapter returned no result "
                                "(skipped per adapter contract)"
                            ),
                            fatal=False,
                        ),
                    ),
                    dry_run=dry_run,
                )
            if isinstance(raw, Evidence):
                raw = [raw]
            for index, item in enumerate(raw):
                if not isinstance(item, Evidence):
                    malformed.append(item)
                    extraction_errors.append(
                        IngestionError(
                            code=IngestionErrorCode.MALFORMED_EVIDENCE.value,
                            message=(
                                f"extractor returned non-Evidence item at "
                                f"index {index}: {type(item).__name__}"
                            ),
                            fatal=False,
                            evidence_index=index,
                        )
                    )
                else:
                    evidences.append(_ensure_evidence_ref(item, material))
        except _ASRFailureError as exc:
            extraction_errors.append(
                IngestionError(
                    code=IngestionErrorCode.ASR_ERROR.value,
                    message=exc.message,
                    fatal=True,
                )
            )
            extractor_failed = True
        except Exception as exc:  # noqa: BLE001 - adapter boundary
            extraction_errors.append(
                IngestionError(
                    code=IngestionErrorCode.EXTRACTOR_ERROR.value,
                    message=f"{source_type} extraction failed: {exc}",
                    fatal=True,
                )
            )
            extractor_failed = True

        if extractor_failed:
            return IngestionReport(
                status=IngestionStatus.FAILED,
                material_id=material.material_id,
                source_type=source_type,
                total_extracted=len(evidences) + len(malformed),
                added_count=0,
                duplicate_count=0,
                rejected_count=0,
                skipped_count=0,
                evidence_ids=(),
                errors=tuple(extraction_errors),
                dry_run=dry_run,
            )

        # Malformed items are excluded from the store write; valid items
        # still flow to the authoritative store (no silent data loss:
        # each malformed item has a structured error record).
        total_extracted = len(evidences) + len(malformed)

        if dry_run:
            status = (
                IngestionStatus.SUCCESS
                if not extraction_errors
                else IngestionStatus.PARTIAL
            )
            return IngestionReport(
                status=status,
                material_id=material.material_id,
                source_type=source_type,
                total_extracted=total_extracted,
                added_count=0,
                duplicate_count=0,
                rejected_count=0,
                skipped_count=0,
                evidence_ids=(),
                errors=tuple(extraction_errors),
                dry_run=True,
            )

        # --- Store write (add_many is the authoritative path) ---
        batch_result = self._store.add_many(evidences)
        added_ids = tuple(
            eid for eid in batch_result.evidence_ids if eid is not None
        )
        # A duplicate is not necessarily an already-active row.  The material
        # workflow retires evidence when a material is deleted; re-registering
        # the same content must make that existing, content-addressed row
        # visible again.  Do this at the ingestion boundary (rather than in
        # the UI or in a later knowledge pass) so every consumer -- evidence
        # listing, deterministic assembly and AI -- observes the same state.
        # Added rows are already ACTIVE, so reviving the complete returned ID
        # tuple is harmless and also covers a mixed batch.
        for evidence_id in added_ids:
            self._store.revive(evidence_id)
        rejection_errors: List[IngestionError] = []
        if batch_result.rejected > 0:
            for idx, eid in enumerate(batch_result.evidence_ids):
                if eid is None:
                    rejection_errors.append(
                        IngestionError(
                            code=IngestionErrorCode.STORE_REJECTION.value,
                            message=f"store rejected evidence at batch index {idx}",
                            fatal=False,
                            evidence_index=idx,
                        )
                    )
        combined_errors = tuple(extraction_errors + rejection_errors)
        if not evidences:
            # Extractor produced nothing (empty source contract).
            status = IngestionStatus.SUCCESS
        elif batch_result.added > 0 or batch_result.duplicates > 0:
            status = (
                IngestionStatus.SUCCESS
                if not combined_errors
                else IngestionStatus.PARTIAL
            )
        elif rejection_errors or extraction_errors:
            status = IngestionStatus.PARTIAL
        else:
            status = IngestionStatus.SUCCESS
        return IngestionReport(
            status=status,
            material_id=material.material_id,
            source_type=source_type,
            total_extracted=total_extracted,
            added_count=batch_result.added,
            duplicate_count=batch_result.duplicates,
            rejected_count=batch_result.rejected,
            skipped_count=0,
            evidence_ids=added_ids,
            errors=combined_errors,
            dry_run=False,
        )
