"""Knowledge Learning Representation & Grounded Explanation Layer (Task 29).

Deterministic, evidence-grounded learning representation on top of the
existing course knowledge layer (``KnowledgePoint`` + ``Evidence``).

Semantics
---------
- A ``LearningRepresentation`` is a *readable* rendering of a single
  KnowledgePoint for a single explicitly-specified language.  It is
  built ONLY from explicit inputs (caller-supplied claims / content)
  plus the existing KnowledgePoint and Evidence objects: no
  auto-generation, no LLM, no translation, no common-sense filling.
- Every ``LearningClaim`` (the unit of "one statement a student can
  read") must reference at least one evidence id that exists in the
  caller-supplied evidence registry.  Without any evidence, a
  representation is explicitly unavailable (``NOT_AVAILABLE``) --
  the system never invents facts.
- Identity is deterministic: content-addressed sha256 digests.  No
  uuid4, datetime.now, random, or builtin hash().
- All public dataclasses are frozen; serialization carries
  ``schema_version`` and rejects unknown versions with a stable error.
- Languages (es / ca / zh / en) are preserved verbatim; a
  representation for language L is only valid when every language-known
  referenced evidence is available in language L, otherwise the build
  result is ``LANGUAGE_NOT_AVAILABLE`` (no implicit translation).

Constraints honored (spec section 2)
------------------------------------
- Evidence-first / no hallucination: grounded generation only.
- Deterministic ids, sorted collections.
- Immutability: inputs are never mutated.
- No LLM, no network, no database, no automatic translation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, FrozenSet, Iterable, Mapping, Optional, Tuple

from src.models import Evidence, KnowledgePoint

__all__ = [
    "LEARNING_SCHEMA_VERSION",
    "LearningErrorCode",
    "LearningRepresentationError",
    "LearningValidationError",
    "LearningSchemaError",
    "BuildStatus",
    "LearningClaim",
    "LearningRepresentation",
    "GroundedKnowledgeBuilder",
    "LearningRepresentationProvider",
]

LEARNING_SCHEMA_VERSION = 1

#: 语言代码中表示"未知"的取值 (小写)。未知不是"另一种语言"。
_UNKNOWN_LANGUAGE_CODES = frozenset({"unknown", "und", ""})


class LearningErrorCode(str, Enum):
    """Stable error codes for the learning-representation layer."""

    INVALID_INPUT = "invalid_input"
    KNOWLEDGE_POINT_NOT_FOUND = "knowledge_point_not_found"
    EVIDENCE_NOT_FOUND = "evidence_not_found"
    EMPTY_CLAIM_TEXT = "empty_claim_text"
    EMPTY_EVIDENCE_REF = "empty_evidence_ref"
    INVALID_SCHEMA_VERSION = "invalid_schema_version"
    INVALID_STATE = "invalid_state"
    LANGUAGE_NOT_AVAILABLE = "language_not_available"
    NOT_AVAILABLE = "not_available"


class LearningRepresentationError(Exception):
    """Base error for the learning representation layer."""

    def __init__(self, code: LearningErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code.value}] {message}")


class LearningValidationError(LearningRepresentationError):
    """Stable input validation error."""


class LearningSchemaError(LearningRepresentationError):
    """Raised for unknown schema versions or corrupted payloads."""


class BuildStatus(str, Enum):
    """Outcome of a grounded build request.

    - OK: representation built from supplied content + evidence.
    - NOT_AVAILABLE: no evidence backs the KP; nothing is invented.
    - LANGUAGE_NOT_AVAILABLE: evidence exists but not in the
      requested language; no automatic translation is performed.
    """

    OK = "ok"
    NOT_AVAILABLE = "not_available"
    LANGUAGE_NOT_AVAILABLE = "language_not_available"


def _canonical_json(value: Any) -> str:
    """Deterministic canonical JSON (sorted keys, compact, UTF-8 kept)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha24(payload: Any) -> str:
    """Deterministic 24-hex sha256 digest of the canonical form."""
    if isinstance(payload, bytes):
        raw = payload
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = _canonical_json(payload).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


@dataclass(frozen=True)
class LearningClaim:
    """One evidence-backed statement a learner can read.

    ``claim_id`` is deterministic:
    ``"claim-" + sha256(stripped text + ":" + ":".join(sorted, deduped
    evidence ids)))[:24]`` -- text-anchored, evidence-set-anchored,
    order-insensitive.
    """

    claim_id: str
    text: str
    evidence_ids: Tuple[str, ...]

    @classmethod
    def create(cls, text: str, evidence_ids: Tuple[str, ...]) -> "LearningClaim":
        """Build a validated, deterministically-identified claim.

        - ``text`` must be a non-empty (non-whitespace) string.
        - ``evidence_ids`` must be non-empty, each a non-empty string.
          (Evidence *existence* is checked later against the caller's
          registry at build time.)
        - Duplicates inside ``evidence_ids`` are collapsed; order is
          normalized to sorted order so the id is insertion-order
          independent.
        """
        if not isinstance(text, str) or not text.strip():
            raise LearningValidationError(
                LearningErrorCode.EMPTY_CLAIM_TEXT,
                "claim text must be a non-empty string",
            )
        ids: Tuple[str, ...] = tuple(str(e) for e in (evidence_ids or ()))
        for eid in ids:
            if not eid:
                raise LearningValidationError(
                    LearningErrorCode.EMPTY_EVIDENCE_REF,
                    "evidence reference must be a non-empty string",
                )
        if not ids:
            raise LearningValidationError(
                LearningErrorCode.EVIDENCE_NOT_FOUND,
                "a claim must reference at least one evidence id",
            )
        unique_ids = tuple(sorted(set(ids)))
        claim_text = text.strip()
        digest = hashlib.sha256(
            (claim_text + ":" + ":".join(unique_ids)).encode("utf-8")
        ).hexdigest()[:24]
        return cls(
            claim_id="claim-" + digest,
            text=claim_text,
            evidence_ids=unique_ids,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "evidence_ids": list(self.evidence_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LearningClaim":
        """Rebuild a claim; the stored id must match the recomputed
        one (corruption / tampering guard)."""
        if not isinstance(data, Mapping):
            raise LearningValidationError(
                LearningErrorCode.INVALID_INPUT,
                "claim payload must be a mapping",
            )
        text = str(data.get("text") or "")
        ids = tuple(str(e) for e in (data.get("evidence_ids") or ()))
        rebuilt = cls.create(text, ids)
        stored = str(data.get("claim_id") or "")
        if stored and stored != rebuilt.claim_id:
            raise LearningValidationError(
                LearningErrorCode.INVALID_STATE,
                "claim_id does not match claim content (corrupted payload)",
            )
        return rebuilt

@dataclass(frozen=True)
class LearningRepresentation:
    """Readable, language-pinned, evidence-grounded rendering of one
    KnowledgePoint.

    - ``representation_id`` is deterministic:
      ``"representation-" + sha256(knowledge_point_id + language +
      sorted claim ids + sorted source evidence ids +
      representation_version)[:24]``  -- no timestamp.
    - ``claims`` carry per-statement evidence traceability; the
      flattened ``source_evidence_ids`` is the deterministic union
      used for the id and for citation.
    """

    representation_id: str
    knowledge_point_id: str
    language: str
    title: str
    explanation: str
    key_points: Tuple[str, ...]
    examples: Tuple[str, ...]
    source_evidence_ids: Tuple[str, ...]
    representation_version: int
    claims: Tuple[LearningClaim, ...] = ()

    @classmethod
    def create(
        cls,
        knowledge_point_id: str,
        language: str,
        *,
        title: str = "",
        explanation: str = "",
        key_points: Tuple[str, ...] = (),
        examples: Tuple[str, ...] = (),
        claims: Tuple[LearningClaim, ...] = (),
        representation_version: int = 1,
    ) -> "LearningRepresentation":
        """Build a validated representation with a deterministic id.

        - ``knowledge_point_id`` / ``language`` must be non-empty.
        - ``representation_version`` must be a positive integer.
        - The claim list is order-independent (deduped by
          ``claim_id``); ``source_evidence_ids`` is derived as the
          deterministic sorted union of every claim's evidence ids.
        """
        if not knowledge_point_id or not str(knowledge_point_id).strip():
            raise LearningValidationError(
                LearningErrorCode.INVALID_INPUT,
                "knowledge_point_id must be non-empty",
            )
        lang = str(language or "").strip()
        if not lang:
            raise LearningValidationError(
                LearningErrorCode.INVALID_INPUT,
                "language must be a non-empty code (caller-supplied)",
            )
        if not isinstance(representation_version, int) or isinstance(
            representation_version, bool
        ) or representation_version < 1:
            raise LearningValidationError(
                LearningErrorCode.INVALID_INPUT,
                "representation_version must be a positive integer",
            )
        kp_id = str(knowledge_point_id).strip()
        # Order-independent, duplicate-free claim set.
        seen: Dict[str, LearningClaim] = {}
        for claim in claims or ():
            if not isinstance(claim, LearningClaim):
                raise LearningValidationError(
                    LearningErrorCode.INVALID_INPUT,
                    "claims must be LearningClaim instances",
                )
            seen[claim.claim_id] = claim
        ordered_claims = tuple(seen[cid] for cid in sorted(seen))
        union_evidence = tuple(
            sorted({e for c in ordered_claims for e in c.evidence_ids})
        )
        identity_payload = [
            kp_id,
            lang,
            [c.claim_id for c in ordered_claims],
            list(union_evidence),
            representation_version,
        ]
        rid = "representation-" + _sha24(identity_payload)
        return cls(
            representation_id=rid,
            knowledge_point_id=kp_id,
            language=lang,
            title=title,
            explanation=explanation,
            key_points=tuple(str(k) for k in (key_points or ())),
            examples=tuple(str(e) for e in (examples or ())),
            source_evidence_ids=union_evidence,
            representation_version=representation_version,
            claims=ordered_claims,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": LEARNING_SCHEMA_VERSION,
            "representation_id": self.representation_id,
            "knowledge_point_id": self.knowledge_point_id,
            "language": self.language,
            "title": self.title,
            "explanation": self.explanation,
            "key_points": list(self.key_points),
            "examples": list(self.examples),
            "source_evidence_ids": list(self.source_evidence_ids),
            "representation_version": self.representation_version,
            "claims": [c.to_dict() for c in self.claims],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LearningRepresentation":
        if not isinstance(data, Mapping):
            raise LearningValidationError(
                LearningErrorCode.INVALID_INPUT,
                "representation payload must be a mapping",
            )
        schema_version = data.get("schema_version")
        if schema_version != LEARNING_SCHEMA_VERSION:
            raise LearningSchemaError(
                LearningErrorCode.INVALID_SCHEMA_VERSION,
                f"unsupported schema_version: {schema_version!r} "
                f"(expected {LEARNING_SCHEMA_VERSION})",
            )
        claims = tuple(
            LearningClaim.from_dict(c) for c in (data.get("claims") or ())
        )
        rebuilt = cls.create(
            str(data.get("knowledge_point_id") or ""),
            str(data.get("language") or ""),
            title=str(data.get("title") or ""),
            explanation=str(data.get("explanation") or ""),
            key_points=tuple(str(k) for k in (data.get("key_points") or ())),
            examples=tuple(str(e) for e in (data.get("examples") or ())),
            claims=claims,
            representation_version=int(data.get("representation_version") or 1),
        )
        stored = str(data.get("representation_id") or "")
        if stored and stored != rebuilt.representation_id:
            raise LearningValidationError(
                LearningErrorCode.INVALID_STATE,
                "representation_id does not match content (corrupted payload)",
            )
        return rebuilt


class GroundedKnowledgeBuilder:
    """Deterministic builder of grounded ``LearningRepresentation``s.

    The builder receives an explicit ``evidence_registry`` -- any
    mapping-like object exposing ``contains(evidence_id)`` (e.g.
    ``src.evidence_store.EvidenceStore``) or a plain mapping /
    iterable of ``Evidence`` -- and optionally per-evidence language
    information to enforce the language-availability rule.

    Grounding rules (spec 29):
    - Every claim's evidence ids must exist in the registry, else
      ``EVIDENCE_NOT_FOUND`` (stable error; construction refused).
    - If the KP has no supporting evidence at all, the result status
      is ``NOT_AVAILABLE`` and no representation is produced -- no
      common-sense fill-in.
    - If the requested language is not backed by the supplied
      evidence, the result status is ``LANGUAGE_NOT_AVAILABLE`` and
      nothing is auto-translated.
    - All content (title/explanation/key_points/examples/claims) is
      caller-supplied; the builder only validates, derives the
      deterministic id, and assembles the frozen object.
    """

    def __init__(
        self,
        evidence_registry: Optional[Mapping[str, Evidence]] = None,
        *,
        language_index: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._registry = evidence_registry
        self._language_index = language_index or {}
        self._ids_cache: Optional[FrozenSet[str]] = None

    # ------------------------------------------------------------------
    # Registry accessors (duck-typed, deterministic, read-only)
    # ------------------------------------------------------------------
    def _contains_evidence(self, evidence_id: str) -> bool:
        if self._registry is None:
            return False
        if hasattr(self._registry, "contains"):
            return bool(self._registry.contains(evidence_id))
        if isinstance(self._registry, Mapping):
            return evidence_id in self._registry
        return evidence_id in self._registry_ids()

    def _registry_ids(self) -> FrozenSet[str]:
        """Lazily materialized id set for iterable-style registries."""
        if self._ids_cache is not None:
            return self._ids_cache
        ids = set()
        for ev in self._registry:
            eid = getattr(ev, "evidence_id", None) or getattr(ev, "id", None)
            if eid:
                ids.add(str(eid))
        self._ids_cache = frozenset(ids)
        return self._ids_cache

    def _get_evidence(self, evidence_id: str) -> Optional[Evidence]:
        registry = self._registry
        if registry is None:
            return None
        if hasattr(registry, "get"):
            return registry.get(evidence_id)
        if isinstance(registry, Mapping):
            return registry.get(evidence_id)
        for ev in registry:
            eid = getattr(ev, "evidence_id", None) or getattr(ev, "id", None)
            if eid == evidence_id:
                return ev
        return None

    def _language_of(self, evidence_id: str) -> Optional[str]:
        """Language code for an evidence id: explicit index wins, else
        the Evidence object's ``language`` (mapped to ISO-ish code),
        else None (language unknown -> no availability constraint).

        ``Language.UNKNOWN`` means "we do not know the language", which is
        **not** the same as "a different language". Treating it as a known
        code would reject every language request for documents whose
        language was never detected, contradicting this method's own
        contract. It is therefore normalised to ``None``.
        """
        lang = self._language_index.get(evidence_id)
        if lang:
            return lang
        ev = self._get_evidence(evidence_id)
        if ev is None:
            return None
        language = getattr(ev, "language", None)
        if language is None:
            return None
        value = getattr(language, "value", language)
        if isinstance(value, str) and value:
            mapping = {
                "spanish": "es",
                "catalan": "ca",
                "chinese": "zh",
                "english": "en",
            }
            code = mapping.get(value.lower(), value.lower())
            if code in _UNKNOWN_LANGUAGE_CODES:
                return None
            return code
        return None

    # ------------------------------------------------------------------
    # Public build API
    # ------------------------------------------------------------------
    def build(
        self,
        knowledge_point: KnowledgePoint,
        language: str,
        *,
        evidence_ids: Optional[Tuple[str, ...]] = None,
        title: str = "",
        explanation: str = "",
        key_points: Optional[Tuple[str, ...]] = None,
        examples: Optional[Tuple[str, ...]] = None,
        claims: Optional[Tuple[LearningClaim, ...]] = None,
        representation_version: int = 1,
    ) -> Tuple[BuildStatus, Optional[LearningRepresentation]]:
        """Build a grounded representation for *knowledge_point*.

        Returns ``(status, representation_or_None)``:
        - ``(OK, rep)`` when content is grounded and the language is
          backed by evidence.
        - ``(NOT_AVAILABLE, None)`` when there is no evidence to
          ground the representation on (no common-sense fill-in).
        - ``(LANGUAGE_NOT_AVAILABLE, None)`` when evidence exists but
          not in the requested language (no auto-translation).

        ``evidence_ids``: the evidence the caller claims supports this
        representation; defaults to ``knowledge_point.evidence_refs``.
        Validation: every cited id must exist in the registry; every
        claim must reference only registry-known evidence.
        """
        if not isinstance(knowledge_point, KnowledgePoint):
            raise LearningValidationError(
                LearningErrorCode.INVALID_INPUT,
                "knowledge_point must be a KnowledgePoint",
            )
        kp_id = knowledge_point.knowledge_id
        if not kp_id:
            raise LearningValidationError(
                LearningErrorCode.INVALID_INPUT,
                "knowledge_point has an empty id",
            )
        lang = str(language or "").strip()
        if not lang:
            raise LearningValidationError(
                LearningErrorCode.INVALID_INPUT,
                "language must be a non-empty caller-supplied code",
            )

        # Canonical evidence set for this build: explicit wins, else
        # the KP's own refs; order-independent, deduped.
        if evidence_ids is None:
            base_refs = tuple(str(e) for e in (knowledge_point.evidence_refs or ()))
        else:
            base_refs = tuple(str(e) for e in evidence_ids)
        supported = tuple(sorted(set(e for e in base_refs if e)))

        if not supported:
            # Nothing to ground on -> explicit NOT_AVAILABLE.
            return (BuildStatus.NOT_AVAILABLE, None)

        # Evidence existence check against the registry.
        missing = tuple(e for e in supported if not self._contains_evidence(e))
        if missing:
            raise LearningValidationError(
                LearningErrorCode.EVIDENCE_NOT_FOUND,
                "evidence ids not found in registry: " + ", ".join(missing),
            )

        # Language availability: when we know the language of any
        # referenced evidence, all known ones must match the request.
        known_langs = [self._language_of(e) for e in supported]
        if any(l is not None for l in known_langs):
            if not all(l in (None, lang) for l in known_langs):
                return (BuildStatus.LANGUAGE_NOT_AVAILABLE, None)

        claims_tuple: Tuple[LearningClaim, ...] = tuple(claims or ())
        for claim in claims_tuple:
            for eid in claim.evidence_ids:
                if not self._contains_evidence(eid):
                    raise LearningValidationError(
                        LearningErrorCode.EVIDENCE_NOT_FOUND,
                        "claim references unknown evidence %r" % (eid,),
                    )

        # Default title/explanation from the KP (existing content only,
        # never invented).
        effective_title = title if title else knowledge_point.title
        effective_explanation = (
            explanation if explanation else knowledge_point.content
        )
        effective_key_points = (
            tuple(key_points) if key_points is not None else tuple()
        )
        effective_examples = (
            tuple(examples) if examples is not None else tuple()
        )

        # Canonical citation union: explicit evidence + all claims.
        union = tuple(
            sorted(
                set(supported)
                | {e for c in claims_tuple for e in c.evidence_ids}
            )
        )

        # Deterministic, order-independent claim ordering (deduped by
        # claim_id), matching LearningRepresentation.create().
        seen: Dict[str, LearningClaim] = {}
        for claim in claims_tuple:
            seen[claim.claim_id] = claim
        ordered_claims = tuple(seen[cid] for cid in sorted(seen))

        identity_payload = [
            kp_id,
            lang,
            [c.claim_id for c in ordered_claims],
            list(union),
            representation_version,
        ]
        rep = LearningRepresentation(
            representation_id="representation-" + _sha24(identity_payload),
            knowledge_point_id=kp_id,
            language=lang,
            title=effective_title,
            explanation=effective_explanation,
            key_points=effective_key_points,
            examples=effective_examples,
            source_evidence_ids=union,
            representation_version=representation_version,
            claims=ordered_claims,
        )
        return (BuildStatus.OK, rep)

    def build_with_citations(
        self,
        knowledge_point: KnowledgePoint,
        language: str,
        **kwargs: Any,
    ) -> LearningRepresentation:
        """Convenience wrapper: returns only the representation; raises
        ``LearningValidationError`` (NOT_AVAILABLE /
        LANGUAGE_NOT_AVAILABLE / EVIDENCE_NOT_FOUND / ...) instead of
        returning a status tuple."""
        status, rep = self.build(knowledge_point, language, **kwargs)
        if status is BuildStatus.OK:
            assert rep is not None
            return rep
        code = (
            LearningErrorCode.LANGUAGE_NOT_AVAILABLE
            if status is BuildStatus.LANGUAGE_NOT_AVAILABLE
            else LearningErrorCode.NOT_AVAILABLE
        )
        raise LearningValidationError(
            code,
            "representation for language %r is %s" % (language, status.value),
        )


class LearningRepresentationProvider:
    """Provider interface for future grounded generation.

    Task 29 does NOT implement a remote/LLM provider.  This class is a
    stable structural seam: concrete providers MUST produce a
    ``LearningRepresentation`` whose every claim is evidence-grounded
    and whose content derives from existing ``KnowledgePoint`` +
    ``Evidence``.  A provider must never invent course facts.
    """

    def build(
        self,
        knowledge_point: KnowledgePoint,
        language: str,
    ) -> Tuple[BuildStatus, Optional[LearningRepresentation]]:
        raise NotImplementedError
