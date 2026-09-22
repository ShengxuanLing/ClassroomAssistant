"""Unified Evidence Store for the Classroom Assistant project (Task 23).

This module provides the single authoritative in-memory Evidence registry
for the whole project.  All Evidence produced by the extractors
(audio/transcript, OCR, notes, PDF, DOCX) is stored through this one
class:  no parallel per-source stores.

Design rules (Task 23 spec):

- Stable, deterministic identity:
    canonical_key = "evidence-key-" + sha256(
        evidence_type | source identity | source location | exact content
    )
  The hash is computed from explicit canonical fields (never str()/repr()),
  with JSON canonicalization (sort_keys=True, ensure_ascii=False,
  separators=(",", ":")).  No wall-clock time, no random UUIDs.
- Source-aware: same text coming from different sources stays distinct;
  same source + location + content maps to exactly one Evidence.
- Idempotent writes: add() of the same Evidence again returns
  DUPLICATE, never a second record.
- Never mutates or rewrites Evidence content.
- No semantic similarity, no LLM/NLP/embedding, no Knowledge logic.
- Read-only queries return deterministic, insertion-ordered lists.
- Snapshot / restore via to_dict() / from_dict() (schema_version = 1);
  derived indexes are rebuilt on restore, never serialized.
- Optional JSON file persistence with atomic replace (save/load).
- Simple ACTIVE / RETIRED lifecycle, fully independent of Knowledge
  Review status (REJECTED etc. never apply to Evidence).
- Thread-safe: all mutating operations and snapshot builds take an
  RLock; read-only queries are lock-free snapshot reads.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Union

from src.models import Evidence, EvidenceType, Language, SourceReference

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
"""Snapshot schema version.  Task 23 ships version 1."""

CANONICAL_KEY_PREFIX = "evidence-key-"
"""Prefix for deterministic canonical evidence identity keys."""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class EvidenceStoreError(Exception):
    """Base class for all EvidenceStore errors."""


class EvidenceValidationError(EvidenceStoreError):
    """Raised when an Evidence object fails the store's entry validation."""


class EvidenceIdentityError(EvidenceStoreError):
    """Raised when a canonical-identity conflict cannot be resolved safely."""


class EvidencePersistenceError(EvidenceStoreError):
    """Raised when a snapshot / JSON file cannot be parsed or restored."""


# ---------------------------------------------------------------------------
# Canonical identity
# ---------------------------------------------------------------------------

def _canonical_float(value: Any) -> Optional[str]:
    """Stable string representation for float identity inputs.

    The project already rounds timestamps / bounding boxes (see
    MockOCREngine / TranscriptSegment); we canonicalize the *decimal
    string form* so that 1.0 and 1.00 give the same key, while we
    never rewrite the stored Evidence field itself.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise EvidenceValidationError(f"float canonical field got bool: {value!r}")
    if isinstance(value, (int, float)):
        v = float(value)
        # Use repr-level precision via .16g, then strip trailing zeros so
        # that 1.0 -> "1.0" deterministically in every Python version.
        s = f"{v:.16g}"
        # Normalize integer-valued floats to include the decimal point.
        if s.replace(".", "", 1).replace("-", "", 1).replace("e", "", 1).replace("+", "", 1).isdigit() or "e" in s:
            if "." not in s and "e" not in s:
                s = s + ".0"
        return s
    raise EvidenceValidationError(f"float canonical field got non-numeric: {value!r}")


def _canonical_int(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise EvidenceValidationError(f"int canonical field got bool: {value!r}")
    if not isinstance(value, int):
        raise EvidenceValidationError(f"int canonical field got non-int: {value!r}")
    return str(value)


def _canonical_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EvidenceValidationError(f"string canonical field got non-str: {value!r}")
    return value


def _canonicalize_source_reference(ref: SourceReference) -> dict[str, Any]:
    """Build the explicit canonical field mapping for a SourceReference.

    Only these fields participate in the identity:

    - material_id    (source identity)
    - location       (free-form source location label)
    - timestamp_start / timestamp_end (audio segment position)
    - page / line / paragraph (document / OCR / note position)

    Field order does not matter (JSON sort_keys), dict-key-order
    differences never change the key.
    """
    return {
        "material_id": _canonical_str(ref.material_id),
        "location": _canonical_str(ref.location),
        "timestamp_start": _canonical_float(ref.timestamp_start),
        "timestamp_end": _canonical_float(ref.timestamp_end),
        "page": _canonical_int(ref.page),
        "line": _canonical_int(ref.line),
        "paragraph": _canonical_str(ref.paragraph),
    }


def compute_canonical_key(evidence: Evidence) -> str:
    """Compute the deterministic canonical identity key for an Evidence.

    Inputs (all canonical, never semantic):
        evidence_type        -> stable enum value string
        source identity      -> material_id / location
        source location      -> page / line / paragraph / timestamps
        exact content        -> content string, verbatim (no normalization)

    The result is a stable "evidence-key-<sha256-hex>" string.  The same
    Evidence always yields the same key across processes and platforms.
    """
    payload: dict[str, Any] = {
        "evidence_type": evidence.evidence_type.value
        if isinstance(evidence.evidence_type, EvidenceType)
        else str(evidence.evidence_type),
        "content": evidence.content,
        "source": _canonicalize_source_reference(evidence.source_reference),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return CANONICAL_KEY_PREFIX + digest


# ---------------------------------------------------------------------------
# Result / state types
# ---------------------------------------------------------------------------

class AddStatus(str, Enum):
    """Outcome of a single Evidence insertion attempt."""

    ADDED = "added"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


@dataclass(frozen=True)
class EvidenceAddResult:
    """Result of a single store.add() call.

    - ADDED: a new Evidence was inserted; evidence_id points to the new
      record, key is the new canonical key.
    - DUPLICATE: an equivalent Evidence already existed; evidence_id
      points to the *existing* record.
    - REJECTED: the Evidence failed entry validation; evidence_id and
      key are None, reason explains why.
    """

    status: AddStatus
    evidence_id: Optional[str] = None
    key: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class BatchAddResult:
    """Aggregated result of store.add_many()."""

    added: int
    duplicates: int
    rejected: int
    evidence_ids: tuple[str, ...]
    """IDs in input order; duplicates carry the existing record id,
    rejected slots carry None.  Deterministic for identical input."""


class EvidenceState(str, Enum):
    """Simple lifecycle state for stored Evidence.

    ACTIVE  : returned by default queries.
    RETIRED : kept for traceability, excluded from default queries.

    This is NOT a knowledge-review status.  Evidence never becomes
    CONFIRMED / REJECTED / APPROVED.
    """

    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


# ---------------------------------------------------------------------------
# Internal storage entry
# ---------------------------------------------------------------------------

@dataclass
class _EvidenceRecord:
    """Mutable wrapper that pairs a stored Evidence with its lifecycle state."""

    evidence: Evidence
    state: EvidenceState = EvidenceState.ACTIVE


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

class EvidenceStore:
    """Unified in-memory Evidence registry (single instance per process).

    Invariants:
    - Evidence.content is stored verbatim; the store never rewrites it.
    - Canonical key == "evidence-key-" + sha256(canonical fields) is the
      dedup identity: same source + location + content -> one record.
    - Different sources (even with identical text) -> distinct records.
    - Queries are read-only and deterministic (insertion order).
    - No Knowledge / Review / LLM / semantic behaviour.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, _EvidenceRecord] = {}
        self._insertion_order: list[str] = []
        self._by_canonical: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate(evidence: Evidence) -> Optional[str]:
        """Return a rejection reason, or None when the Evidence is valid.

        Rules:
        - must be an Evidence instance
        - content must be a str (empty or whitespace-only rejected:
          the extractors already filter those, this is the safety net)
        - evidence_id must be a non-empty str
        - source_reference must be a SourceReference
        """
        if not isinstance(evidence, Evidence):
            return f"expected Evidence, got {type(evidence).__name__}"
        if not isinstance(evidence.content, str):
            return f"content must be str, got {type(evidence.content).__name__}"
        if evidence.content.strip() == "":
            return "content is empty or whitespace-only"
        if not evidence.evidence_id or not isinstance(evidence.evidence_id, str):
            return "evidence_id must be a non-empty string"
        if not isinstance(evidence.source_reference, SourceReference):
            return "source_reference must be a SourceReference instance"
        return None

    # ------------------------------------------------------------------
    # Mutation API
    # ------------------------------------------------------------------

    def add(self, evidence: Evidence) -> EvidenceAddResult:
        """Insert one Evidence; idempotent.

        - ADDED: new record.
        - DUPLICATE: an equivalent Evidence (same canonical key) is
          already stored; nothing changes, evidence_id points to the
          existing record.
        - REJECTED: failed entry validation.

        Duplicate with a *different* evidence_id but the same canonical
        key is still a DUPLICATE (business identity is the canonical
        key; the existing record keeps its original ID).
        """
        with self._lock:
            return self._add_locked(evidence)

    def _add_locked(self, evidence: Evidence) -> EvidenceAddResult:
        reason = self._validate(evidence)
        if reason is not None:
            return EvidenceAddResult(status=AddStatus.REJECTED, reason=reason)

        key = compute_canonical_key(evidence)
        existing_id = self._by_canonical.get(key)
        if existing_id is not None:
            return EvidenceAddResult(
                status=AddStatus.DUPLICATE,
                evidence_id=existing_id,
                key=key,
            )

        record = _EvidenceRecord(
            evidence=copy.deepcopy(evidence),
            state=EvidenceState.ACTIVE,
        )
        existing_record = self._records.get(evidence.evidence_id)
        if existing_record is not None:
            # Same evidence_id claimed under a different canonical
            # identity: never overwrite the first-seen record.  The
            # newcomer is rejected and the original record (and its
            # key) are kept.
            return EvidenceAddResult(
                status=AddStatus.REJECTED,
                evidence_id=evidence.evidence_id,
                reason=(
                    f"evidence_id {evidence.evidence_id!r} already "
                    "belongs to a record with a different canonical "
                    "identity"
                ),
            )
        self._records[record.evidence.evidence_id] = record
        self._insertion_order.append(record.evidence.evidence_id)
        self._by_canonical[key] = record.evidence.evidence_id
        return EvidenceAddResult(
            status=AddStatus.ADDED,
            evidence_id=record.evidence.evidence_id,
            key=key,
        )

    def add_many(self, evidences: Iterable[Evidence]) -> BatchAddResult:
        """Insert a batch, preserving input order.

        Policy (deterministic, documented): each Evidence is validated
        first against the *pre-batch* store state, then inserted in
        input order.  A rejected entry skips only itself (REJECTED),
        never aborts the whole batch:

            A, B, INVALID, C  ->  A/B/C written, INVALID rejected

        This keeps the batch "deterministic + idempotent" while keeping
        the API simple (no transaction framework; the store is in-memory
        and a single call is a single logical operation anyway).

        Duplicate entries within the same batch are collapsed to the
        first occurrence; duplicates against the existing store return
        the existing ID.
        """
        with self._lock:
            items = list(evidences)
            seen_keys_in_batch: dict[str, Optional[str]] = {}
            added = 0
            duplicates = 0
            rejected = 0
            ids: list[Optional[str]] = []
            for ev in items:
                result = self._add_locked(ev)
                # Track in-batch duplicates via the canonical key of the
                # *input* item so we can report them deterministically.
                key_holder = result.key
                if result.status == AddStatus.ADDED:
                    added += 1
                elif result.status == AddStatus.DUPLICATE:
                    duplicates += 1
                    if key_holder is not None:
                        seen_keys_in_batch.setdefault(key_holder, result.evidence_id)
                else:
                    rejected += 1
                ids.append(result.evidence_id)
            return BatchAddResult(
                added=added,
                duplicates=duplicates,
                rejected=rejected,
                evidence_ids=tuple(ids),
            )

    def clear(self) -> None:
        """Remove all records (logical only: source files untouched)."""
        with self._lock:
            self._records.clear()
            self._insertion_order.clear()
            self._by_canonical.clear()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def retire(self, evidence_id: str) -> bool:
        """Mark an Evidence RETIRED (idempotent; existing state kept).

        Returns True when the record existed, False when the id is
        unknown.  The record is never physically deleted.
        """
        with self._lock:
            record = self._records.get(evidence_id)
            if record is None:
                return False
            record.state = EvidenceState.RETIRED
            return True

    def restore(self, evidence_id: str) -> bool:
        """Move a RETIRED Evidence back to ACTIVE (idempotent)."""
        with self._lock:
            record = self._records.get(evidence_id)
            if record is None:
                return False
            record.state = EvidenceState.ACTIVE
            return True

    def get_state(self, evidence_id: str) -> Optional[EvidenceState]:
        """Return the lifecycle state for an id, or None when unknown."""
        with self._lock:
            record = self._records.get(evidence_id)
            return record.state if record else None

    # ------------------------------------------------------------------
    # Query API (read-only, deterministic insertion order)
    # ------------------------------------------------------------------

    def get(self, evidence_id: str, include_retired: bool = False) -> Optional[Evidence]:
        """Return the Evidence for an id, or None when missing.

        By default RETIRED records are still retrievable by ID (they
        must remain traceable) -- include_retired only affects
        all()/filtered views.  get() always returns a record regardless
        of state so history is preserved; use all(active_only=True) to
        exclude RETIRED from listings.
        """
        with self._lock:
            record = self._records.get(evidence_id)
            return copy.deepcopy(record.evidence) if record else None

    def contains(self, evidence_id: str) -> bool:
        with self._lock:
            return evidence_id in self._records

    def all(self, active_only: bool = True) -> list[Evidence]:
        """All stored Evidence in insertion order.

        active_only=True (default) hides RETIRED records.
        """
        with self._lock:
            out: list[Evidence] = []
            for eid in self._insertion_order:
                record = self._records[eid]
                if active_only and record.state == EvidenceState.RETIRED:
                    continue
                out.append(copy.deepcopy(record.evidence))
            return out

    def count(self, active_only: bool = True) -> int:
        """Number of stored records (ACTIVE by default)."""
        with self._lock:
            if not active_only:
                return len(self._records)
            return sum(
                1
                for r in self._records.values()
                if r.state == EvidenceState.ACTIVE
            )

    def _where(self, predicate, active_only: bool) -> list[Evidence]:
        out: list[Evidence] = []
        for eid in self._insertion_order:
            record = self._records[eid]
            if active_only and record.state == EvidenceState.RETIRED:
                continue
            if predicate(record.evidence):
                out.append(copy.deepcopy(record.evidence))
        return out
    def get_by_type(
        self,
        evidence_type: EvidenceType,
        active_only: bool = True,
    ) -> list[Evidence]:
        """All ACTIVE Evidence of one type, in insertion order."""
        with self._lock:
            et = (
                evidence_type
                if isinstance(evidence_type, EvidenceType)
                else EvidenceType.from_string(str(evidence_type))
            )
            return self._where(lambda e: e.evidence_type == et, active_only)

    def get_by_source(
        self,
        material_id: str,
        active_only: bool = True,
    ) -> list[Evidence]:
        """All ACTIVE Evidence whose source_reference.material_id matches."""
        with self._lock:
            return self._where(
                lambda e: e.source_reference.material_id == material_id,
                active_only,
            )

    def get_by_document(
        self,
        document_id: str,
        active_only: bool = True,
    ) -> list[Evidence]:
        """All ACTIVE Evidence with metadata["document_id"] == document_id.

        Only document evidence (Task 21/22) carries this field; notes
        and transcripts return [] unless their metadata happens to
        reference a document.
        """
        with self._lock:
            def _match(e: Evidence) -> bool:
                return e.metadata.get("document_id") == document_id

            return self._where(_match, active_only)

    def get_by_session(
        self,
        session_id: str,
        active_only: bool = True,
    ) -> list[Evidence]:
        """All ACTIVE Evidence whose ClassSession id is recorded.

        A session id appears either in
        metadata["class_session_id"] or in
        source_reference.metadata["class_session_id"].  The store does
        not invent session identity itself.
        """
        with self._lock:
            def _match(e: Evidence) -> bool:
                if e.metadata.get("class_session_id") == session_id:
                    return True
                # Not in SourceReference (no metadata field there) --
                # only the Evidence-level metadata is authoritative.
                return False

            return self._where(_match, active_only)

    def get_by_location(
        self,
        page: Optional[int] = None,
        active_only: bool = True,
    ) -> list[Evidence]:
        """All ACTIVE Evidence for a given page (Optional[int]).

        Deliberately minimal; no query language.  page=None matches
        records with no page reference.
        """
        with self._lock:
            return self._where(
                lambda e: e.source_reference.page == page,
                active_only,
            )

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def statistics(self, active_only: bool = True) -> dict[str, Any]:
        """Deterministic statistics snapshot (counts only).

        Keys:
          total_count          : number of records (ACTIVE by default)
          count_by_type        : {EvidenceType.value -> count}
          count_by_material    : {material_id -> count}
          count_by_document   : {metadata.document_id -> count}
          unique_materials     : number of distinct material_ids
          unique_documents     : number of distinct document_ids
        """
        with self._lock:
            records = [
                r
                for r in (self._records[eid] for eid in self._insertion_order)
                if not active_only or r.state != EvidenceState.RETIRED
            ]

            count_by_type: dict[str, int] = {}
            count_by_material: dict[str, int] = {}
            documents: set[str] = set()
            for record in records:
                e = record.evidence
                count_by_type[e.evidence_type.value] = (
                    count_by_type.get(e.evidence_type.value, 0) + 1
                )
                mid = e.source_reference.material_id
                if mid:
                    count_by_material[mid] = (
                        count_by_material.get(mid, 0) + 1
                    )
                doc_id = e.metadata.get("document_id")
                if doc_id:
                    documents.add(doc_id)

            return {
                "total_count": len(records),
                "count_by_type": dict(
                    sorted(count_by_type.items())
                ),
                "count_by_material": dict(
                    sorted(count_by_material.items())
                ),
                "count_by_document": {
                    d: sum(
                        1
                        for r in records
                        if r.evidence.metadata.get("document_id") == d
                    )
                    for d in sorted(documents)
                },
                "unique_materials": len(count_by_material),
                "unique_documents": len(documents),
            }

    # ------------------------------------------------------------------
    # Snapshot / restore
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Deterministic full snapshot of the store (data, no indexes).

        Structure:
          {
            "schema_version": 1,
            "evidences": [ {evidence fields..., "state": "ACTIVE"} ],
          }
        Evidences are in insertion order; indexes are NOT serialized
        (they are rebuilt on from_dict()).
        """
        with self._lock:
            evidences: list[dict[str, Any]] = []
            for eid in self._insertion_order:
                record = self._records[eid]
                d = record.evidence.to_dict()
                d["state"] = record.state.value
                evidences.append(d)
            return {
                "schema_version": SCHEMA_VERSION,
                "evidences": evidences,
            }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceStore":
        """Restore a store from a to_dict() snapshot (atomic).

        The entire snapshot is parsed and validated into a *temporary*
        store first; on success the temporary is returned, on failure
        the caller's state is untouched (a fresh empty store is never
        returned half-built -- we raise instead).

        Raises EvidencePersistenceError on malformed / corrupt input.
        """
        if not isinstance(data, Mapping):
            raise EvidencePersistenceError(
                f"snapshot must be a mapping, got {type(data).__name__}"
            )

        schema_version = data.get("schema_version")
        if schema_version != SCHEMA_VERSION:
            raise EvidencePersistenceError(
                f"unsupported schema_version: {schema_version!r} "
                f"(expected {SCHEMA_VERSION})"
            )

        raw_evidences = data.get("evidences")
        if raw_evidences is None:
            raw_evidences = []
        if not isinstance(raw_evidences, list):
            raise EvidencePersistenceError(
                f"'evidences' must be a list, got {type(raw_evidences).__name__}"
            )

        store = cls()
        seen_ids: set[str] = set()
        seen_keys: set[str] = set()
        for index, item in enumerate(raw_evidences):
            if not isinstance(item, Mapping):
                raise EvidencePersistenceError(
                    f"evidences[{index}] must be a mapping, "
                    f"got {type(item).__name__}"
                )
            try:
                evidence = Evidence.from_dict(item)
            except (KeyError, TypeError, AttributeError) as exc:
                raise EvidencePersistenceError(
                    f"evidences[{index}] is malformed: {exc}"
                ) from exc

            raw_state = item.get("state", EvidenceState.ACTIVE.value)
            try:
                state = EvidenceState(raw_state)
            except ValueError as exc:
                raise EvidencePersistenceError(
                    f"evidences[{index}] has unknown state {raw_state!r}"
                ) from exc

            result = store._add_locked(evidence)
            if result.status == AddStatus.REJECTED:
                raise EvidencePersistenceError(
                    f"evidences[{index}] rejected: {result.reason}"
                )
            if result.status == AddStatus.DUPLICATE:
                # Duplicate canonical key inside the snapshot: that means
                # two different Evidence objects claim the same business
                # identity.  Refuse a half-destroyed store.
                raise EvidencePersistenceError(
                    f"duplicate canonical key within snapshot at index {index}"
                )
            eid = result.evidence_id
            if eid in seen_ids:
                # Same id twice with different keys: conflict, not duplicate.
                raise EvidencePersistenceError(
                    f"duplicate evidence_id {eid!r} at index {index}"
                )
            seen_ids.add(eid)
            key = compute_canonical_key(evidence)
            if key in seen_keys:
                raise EvidencePersistenceError(
                    f"duplicate canonical key at index {index}"
                )
            seen_keys.add(key)
            # Record restored state (defaults to ACTIVE via _add_locked).
            record = store._records[eid]
            record.state = state
        return store

    # ------------------------------------------------------------------
    # File persistence
    # ------------------------------------------------------------------

    def save(self, path: Union[str, Path]) -> None:
        """Persist the store as UTF-8 JSON to *path* (atomic replace).

        Uses a temp file in the same directory, flushes, then
        os.replace() so readers never see a half-written file.
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        snapshot = self.to_dict()
        text = json.dumps(snapshot, ensure_ascii=False, indent=2)
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
        # os.replace is atomic on Windows (NTFS move-with-replace).
        import os
        os.replace(tmp, p)

    @classmethod
    def load(cls, path: Union[str, Path]) -> "EvidenceStore":
        """Load a store saved via save().  Raises EvidencePersistenceError
        on corrupt / missing files (never returns a half-built store)."""
        p = Path(path)
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            raise EvidencePersistenceError(f"cannot read {p}: {exc}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise EvidencePersistenceError(f"invalid JSON in {p}: {exc}") from exc
        return cls.from_dict(data)
