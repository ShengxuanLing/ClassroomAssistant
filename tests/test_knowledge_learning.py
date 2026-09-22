"""Tests for src/knowledge_learning.py (Task 29: Grounded Learning
Representation Layer).

Covers spec 29.15:
- grounded claim
- missing evidence
- invalid evidence ID
- deterministic ID
- multilingual
- Unicode
- multiple evidence
- empty evidence
- representation serialization
- round-trip
- no translation
- no hallucination
- immutable output
- insertion-order independence
"""

from __future__ import annotations

import json

import pytest

from src.knowledge_learning import (
    BuildStatus,
    GroundedKnowledgeBuilder,
    LEARNING_SCHEMA_VERSION,
    LearningClaim,
    LearningErrorCode,
    LearningRepresentation,
    LearningSchemaError,
    LearningValidationError,
)
from src.models import Evidence, KnowledgePoint, Language


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def make_kp(
    kp_id: str = "KP-001",
    title: str = "Ecuaciones lineales",
    content: str = "Una ecuación lineal tiene una incógnita de grado uno.",
    evidence_refs=("E1", "E2"),
) -> KnowledgePoint:
    return KnowledgePoint(
        knowledge_id=kp_id,
        title=title,
        content=content,
        evidence_refs=list(evidence_refs),
    )


def make_evidence(
    eid: str = "E1",
    content: str = "Una ecuación lineal tiene una solución única.",
    language: Language = Language.SPANISH,
) -> Evidence:
    return Evidence(
        evidence_id=eid,
        content=content,
        language=language,
        evidence_type="transcript",
    )


def evidence_registry(*items: Evidence) -> dict:
    return {ev.evidence_id: ev for ev in items}


# ---------------------------------------------------------------------------
# LearningClaim
# ---------------------------------------------------------------------------


def test_claim_id_is_deterministic_and_text_anchored() -> None:
    first = LearningClaim.create("texto X", ("E1",))
    second = LearningClaim.create("texto X", ("E1",))
    assert first.claim_id == second.claim_id
    assert first.claim_id.startswith("claim-")
    assert len(first.claim_id) == len("claim-") + 24


def test_claim_rejects_empty_text() -> None:
    with pytest.raises(LearningValidationError) as excinfo:
        LearningClaim.create("   ", ("E1",))
    assert excinfo.value.code is LearningErrorCode.EMPTY_CLAIM_TEXT


def test_claim_rejects_empty_evidence_ref() -> None:
    with pytest.raises(LearningValidationError) as excinfo:
        LearningClaim.create("texto", ("", "E1"))
    assert excinfo.value.code is LearningErrorCode.EMPTY_EVIDENCE_REF


def test_claim_requires_at_least_one_evidence() -> None:
    with pytest.raises(LearningValidationError) as excinfo:
        LearningClaim.create("texto", ())
    assert excinfo.value.code is LearningErrorCode.EVIDENCE_NOT_FOUND


def test_claim_is_insertion_order_independent() -> None:
    a = LearningClaim.create("texto", ("E2", "E1"))
    b = LearningClaim.create("texto", ("E1", "E2"))
    c = LearningClaim.create("texto", ("E1", "E2", "E1"))
    assert a.claim_id == b.claim_id == c.claim_id
    assert a.evidence_ids == ("E1", "E2")


def test_claim_is_frozen() -> None:
    claim = LearningClaim.create("texto", ("E1",))
    with pytest.raises(AttributeError):
        claim.text = "otro"


def test_claim_serialization_round_trip() -> None:
    claim = LearningClaim.create("texto", ("E2", "E1"))
    restored = LearningClaim.from_dict(claim.to_dict())
    assert restored == claim
    assert restored.claim_id == claim.claim_id
    assert restored.evidence_ids == ("E1", "E2")


def test_claim_from_dict_rejects_tampered_id() -> None:
    claim = LearningClaim.create("texto", ("E1",))
    payload = claim.to_dict()
    payload["claim_id"] = "claim-" + "0" * 24
    with pytest.raises(LearningValidationError) as excinfo:
        LearningClaim.from_dict(payload)
    assert excinfo.value.code is LearningErrorCode.INVALID_STATE


def test_claim_preserves_unicode_multilingual() -> None:
    cases = [
        ("Ecuación lineal: 2x + 3 = 7", "es"),
        ("Equació lineal: 2x + 3 = 7", "ca"),
        ("线性方程：2x + 3 = 7", "zh"),
    ]
    for text, lang in cases:
        claim = LearningClaim.create(text, ("E1",))
        assert claim.text == text
        restored = LearningClaim.from_dict(json.loads(json.dumps(claim.to_dict())))
        assert restored.text == text
        assert restored.claim_id == claim.claim_id


# ---------------------------------------------------------------------------
# LearningRepresentation
# ---------------------------------------------------------------------------


def test_representation_id_is_deterministic() -> None:
    claim = LearningClaim.create("solución única", ("E1",))
    a = LearningRepresentation.create(
        "KP-001", "es", title="Ecuaciones", explanation="def", claims=(claim,)
    )
    b = LearningRepresentation.create(
        "KP-001", "es", title="Ecuaciones", explanation="def", claims=(claim,)
    )
    assert a.representation_id == b.representation_id
    assert a.representation_id.startswith("representation-")


def test_representation_changes_when_content_changes() -> None:
    claim_a = LearningClaim.create("contenido A", ("E1",))
    claim_b = LearningClaim.create("contenido B", ("E1",))
    rep_a = LearningRepresentation.create("KP", "es", claims=(claim_a,))
    rep_b = LearningRepresentation.create("KP", "es", claims=(claim_b,))
    assert rep_a.representation_id != rep_b.representation_id


def test_representation_rejects_empty_kp_or_language() -> None:
    with pytest.raises(LearningValidationError):
        LearningRepresentation.create("", "es")
    with pytest.raises(LearningValidationError):
        LearningRepresentation.create("KP", "   ")


def test_representation_version_must_be_positive_int() -> None:
    with pytest.raises(LearningValidationError):
        LearningRepresentation.create("KP", "es", representation_version=0)
    with pytest.raises(LearningValidationError):
        LearningRepresentation.create("KP", "es", representation_version=True)


def test_representation_dedupes_and_orders_claims() -> None:
    claim1 = LearningClaim.create("primero", ("E1",))
    claim2 = LearningClaim.create("segundo", ("E2",))
    rep = LearningRepresentation.create(
        "KP", "es", claims=(claim2, claim1, claim1, claim2)
    )
    assert rep.claims[0].claim_id < rep.claims[1].claim_id
    assert len(rep.claims) == 2
    assert rep.source_evidence_ids == ("E1", "E2")


def test_representation_is_frozen() -> None:
    rep = LearningRepresentation.create("KP", "es")
    with pytest.raises(AttributeError):
        rep.title = "nuevo titulo"


def test_representation_from_dict_rejects_unknown_schema_version() -> None:
    rep = LearningRepresentation.create("KP", "es")
    payload = rep.to_dict()
    payload["schema_version"] = LEARNING_SCHEMA_VERSION + 1
    with pytest.raises(LearningSchemaError) as excinfo:
        LearningRepresentation.from_dict(payload)
    assert excinfo.value.code is LearningErrorCode.INVALID_SCHEMA_VERSION


def test_representation_serialization_round_trip() -> None:
    claim = LearningClaim.create("solución única de grado uno", ("E2", "E1"))
    rep = LearningRepresentation.create(
        "KP-001",
        "es",
        title="Ecuaciones lineales",
        explanation="2x + 3 = 7",
        key_points=("grado uno", "solución única"),
        examples=("2x + 3 = 7",),
        claims=(claim,),
    )
    wire = json.loads(json.dumps(rep.to_dict()))
    restored = LearningRepresentation.from_dict(wire)
    assert restored == rep
    assert restored.representation_id == rep.representation_id


def test_representation_round_trip_rejects_tampered_id() -> None:
    rep = LearningRepresentation.create("KP-001", "es")
    payload = rep.to_dict()
    payload["representation_id"] = "representation-" + "f" * 24
    with pytest.raises(LearningValidationError) as excinfo:
        LearningRepresentation.from_dict(payload)
    assert excinfo.value.code is LearningErrorCode.INVALID_STATE


def test_multilingual_unicode_preservation_round_trip() -> None:
    payloads = [
        ("KP-ES", "es", "Ecuación lineal: 2x + 3 = 7 (Español)"),
        ("KP-CA", "ca", "Equació lineal: 2x + 3 = 7 (Català)"),
        ("KP-ZH", "zh", "线性方程：2x + 3 = 7（中文）"),
    ]
    for kp_id, lang, text in payloads:
        claim = LearningClaim.create(text, ("E1",))
        rep = LearningRepresentation.create(
            kp_id, lang, title=text, claims=(claim,)
        )
        restored = LearningRepresentation.from_dict(json.loads(json.dumps(rep.to_dict())))
        assert restored.title == text
        assert restored.language == lang
        # IDs are independent of language.
        assert LearningRepresentation.create(
            kp_id, lang, title=text, claims=(claim,)
        ).representation_id == rep.representation_id


# ---------------------------------------------------------------------------
# GroundedKnowledgeBuilder
# ---------------------------------------------------------------------------


def test_builder_builds_grounded_representation() -> None:
    kp = make_kp(evidence_refs=["E1", "E2"])
    claim = LearningClaim.create("solución única", ("E1", "E2"))
    builder = GroundedKnowledgeBuilder(
        evidence_registry(make_evidence("E1"), make_evidence("E2", "otra", Language.CATALAN))
    )
    # Mixed languages known -> requesting "es" must NOT auto-translate.
    status, rep = builder.build(kp, "es")
    assert status is BuildStatus.LANGUAGE_NOT_AVAILABLE


def test_builder_ok_when_evidence_language_matches() -> None:
    kp = make_kp(evidence_refs=["E1"])
    es = make_evidence("E1", language=Language.SPANISH)
    builder = GroundedKnowledgeBuilder(evidence_registry(es))
    claim = LearningClaim.create("solución única", ("E1",))
    status, rep = builder.build(kp, "es", claims=(claim,))
    assert status is BuildStatus.OK
    assert rep is not None
    assert rep.knowledge_point_id == "KP-001"
    assert rep.language == "es"
    assert rep.source_evidence_ids == ("E1",)
    assert rep.claims == (claim,)
    # Defaults come from the KP content, never invented.
    assert rep.title == kp.title
    assert rep.explanation == kp.content


def test_builder_missing_evidence_raises_stable_error() -> None:
    kp = make_kp(evidence_refs=["E1"])
    builder = GroundedKnowledgeBuilder(evidence_registry())
    with pytest.raises(LearningValidationError) as excinfo:
        builder.build(kp, "es")
    assert excinfo.value.code is LearningErrorCode.EVIDENCE_NOT_FOUND


def test_builder_invalid_claim_evidence_id_raises() -> None:
    kp = make_kp(evidence_refs=["E1"])
    es = make_evidence("E1", language=Language.SPANISH)
    builder = GroundedKnowledgeBuilder(evidence_registry(es))
    bad_claim = LearningClaim.create("hecho nuevo", ("E999",))
    with pytest.raises(LearningValidationError) as excinfo:
        builder.build(kp, "es", claims=(bad_claim,))
    assert excinfo.value.code is LearningErrorCode.EVIDENCE_NOT_FOUND


def test_builder_empty_evidence_returns_not_available() -> None:
    kp = make_kp(evidence_refs=())
    builder = GroundedKnowledgeBuilder()
    status, rep = builder.build(kp, "es")
    assert status is BuildStatus.NOT_AVAILABLE
    assert rep is None


def test_builder_language_index_overrides_evidence_language() -> None:
    kp = make_kp(evidence_refs=["E1"])
    # Evidence claims Catalan, but the language index says "es".
    ca = make_evidence("E1", "una equació lineal", Language.CATALAN)
    builder = GroundedKnowledgeBuilder(
        evidence_registry(ca), language_index={"E1": "es"}
    )
    status, rep = builder.build(kp, "es")
    assert status is BuildStatus.OK
    assert rep is not None


def test_builder_uses_kp_refs_by_default_and_explicit_evidence_ids() -> None:
    kp = make_kp(evidence_refs=["E1", "E2"])
    e1 = make_evidence("E1", language=Language.SPANISH)
    e2 = make_evidence("E2", language=Language.SPANISH)
    builder = GroundedKnowledgeBuilder(evidence_registry(e1, e2))

    # Explicit subset.
    status, rep = builder.build(kp, "es", evidence_ids=("E1",))
    assert status is BuildStatus.OK
    assert rep is not None
    assert rep.source_evidence_ids == ("E1",)

    # Default (KP refs) + claim evidence union.
    claim = LearningClaim.create("otro hecho apoyado por E2", ("E2",))
    status, rep = builder.build(kp, "es", claims=(claim,))
    assert rep is not None
    assert rep.source_evidence_ids == ("E1", "E2")


def test_builder_multiple_evidence_deterministic_id() -> None:
    kp = make_kp(evidence_refs=["E1", "E2", "E3"])
    registry = evidence_registry(
        make_evidence("E1"), make_evidence("E2"), make_evidence("E3")
    )
    builder = GroundedKnowledgeBuilder(registry)

    a, rep_a = builder.build(kp, "es", evidence_ids=("E3", "E1", "E2"))
    b, rep_b = builder.build(kp, "es", evidence_ids=("E1", "E3", "E2"))
    assert a is BuildStatus.OK and b is BuildStatus.OK
    assert rep_a is not None and rep_b is not None
    assert rep_a.representation_id == rep_b.representation_id
    assert rep_a.source_evidence_ids == ("E1", "E2", "E3")


def test_builder_no_hallucination_only_explicit_content() -> None:
    """Grounded build must surface ONLY caller-supplied / KP content.

    With evidence "Una ecuación lineal tiene una solución única."
    the representation must not contain a fact like "x = 2" (a value
    that only a reasoner could invent).
    """
    kp = make_kp(evidence_refs=["E1"])
    e1 = make_evidence("E1", "Una ecuación lineal tiene una solución única.")
    builder = GroundedKnowledgeBuilder(evidence_registry(e1))
    status, rep = builder.build(kp, "es")
    assert status is BuildStatus.OK
    assert rep is not None
    text_blob = "\n".join(
        [rep.title, rep.explanation]
        + list(rep.key_points)
        + list(rep.examples)
        + [c.text for c in rep.claims]
    )
    assert "x = 2" not in text_blob
    # Nothing was invented: content equals KP content verbatim.
    assert rep.explanation == kp.content
    assert rep.title == kp.title
    assert rep.key_points == ()
    assert rep.examples == ()


def test_builder_does_not_mutate_inputs() -> None:
    kp = make_kp(evidence_refs=["E1"])
    e1 = make_evidence("E1")
    registry = evidence_registry(e1)
    builder = GroundedKnowledgeBuilder(registry)
    kp_before = kp.evidence_refs
    builder.build(kp, "es")
    assert kp.evidence_refs == kp_before
    assert len(registry) == 1


def test_builder_build_with_citations_wrapper() -> None:
    kp = make_kp(evidence_refs=())
    builder = GroundedKnowledgeBuilder()
    with pytest.raises(LearningValidationError) as excinfo:
        builder.build_with_citations(kp, "es")
    assert excinfo.value.code is LearningErrorCode.NOT_AVAILABLE

    kp2 = make_kp(evidence_refs=["E1"])
    e1 = make_evidence("E1", "texto", Language.CATALAN)
    builder2 = GroundedKnowledgeBuilder(evidence_registry(e1))
    with pytest.raises(LearningValidationError) as excinfo:
        builder2.build_with_citations(kp2, "es")
    assert excinfo.value.code is LearningErrorCode.LANGUAGE_NOT_AVAILABLE


def test_builder_accepts_evidence_store_like_registry() -> None:
    """The registry may be an EvidenceStore instance (contains() API)."""
    from src.evidence_store import EvidenceStore

    store = EvidenceStore()
    store.add(make_evidence("E1", language=Language.SPANISH))
    kp = make_kp(evidence_refs=["E1"])
    builder = GroundedKnowledgeBuilder(store)
    status, rep = builder.build(kp, "es")
    assert status is BuildStatus.OK
    assert rep is not None
    assert rep.source_evidence_ids == ("E1",)


def test_builder_language_unknown_does_not_block() -> None:
    """Evidence whose language is UNKNOWN carries no availability constraint:
    the explicit language_index is the caller's statement of fact."""
    kp = make_kp(evidence_refs=["E1"])
    e1 = make_evidence("E1", "texto", Language.UNKNOWN)
    builder = GroundedKnowledgeBuilder(
        evidence_registry(e1), language_index={"E1": "es"}
    )
    status, rep = builder.build(kp, "es")
    assert status is BuildStatus.OK
    assert rep is not None
