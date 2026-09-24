"""Tests for src/evidence_store.py (Task 23 - unified Evidence Store).

Covers identity, dedup, query, statistics, serialization, persistence,
lifecycle, immutability, thread safety, large store, and regression
against the existing extractors / integrator / pipeline / review.
"""

import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Optional

from src.models import (
    Evidence,
    EvidenceType,
    Language,
    Material,
    MaterialType,
    SourceReference,
    Transcript,
    TranscriptLanguage,
    BoundingBox,
    OCRResult,
    OCRSegment,
    OCRLanguage,
)
from src.evidence_store import (
    CANONICAL_KEY_PREFIX,
    EvidenceState,
    AddStatus,
    BatchAddResult,
    EvidenceAddResult,
    EvidencePersistenceError,
    EvidenceStore,
    compute_canonical_key,
    SCHEMA_VERSION,
)


def _deterministic_evidence_id(
    content: str, material_id: str, page: Optional[int]
) -> str:
    """Deterministic stand-in for a business id.

    曾经这里用的是内置 ``hash()``::

        "ev-" + str(abs(hash(content + material_id + str(page))) % 10**10)

    这是**错的**, 有两个后果:

    1. ``hash()`` 对 ``str`` 是按进程随机化的 (``PYTHONHASHSEED``), 所以同一个
       测试在不同进程里会算出不同的 id —— 测试结果因此依赖进程环境。
    2. 更糟的是 ``% 10**10`` 把 64 位哈希压到 10^10 个桶: 5000 个不同内容约有
       ``5000^2 / (2 * 10^10) ≈ 0.125%`` 的概率撞 id。而 ``EvidenceStore`` 对
       "同一个 evidence_id 但规范化身份不同" 的条目是 **REJECTED** 的, 于是
       ``test_10k_inserts_dedups_and_queries`` 会偶发地拿到 ``added < 5000``
       (实测在一次全量回归里真的红过一次)。

    改用 sha256 (截 32 个十六进制字符 = 128 位) 之后, 5000 个条目的碰撞概率是
    ``5000^2 / 2^129``, 可以当作 0; 而且结果**跨进程稳定**。

    本项目对**产品代码**禁止 ``hash()`` 是由 ``tests/test_determinism_audit.py``
    强制执行的 —— 它此前只扫 ``src/``, 所以这条测试里的违规没被发现。现在该审计
    也扫 ``tests/`` 了。
    """
    raw = "%s|%s|%s" % (content, material_id, page)
    return "ev-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def make_evidence(
    content: str = "La estabilidad del sistema es importante.",
    material_id: str = "material-A",
    evidence_type: EvidenceType = EvidenceType.PERSONAL_NOTE,
    evidence_id: Optional[str] = None,
    page: Optional[int] = None,
    line: Optional[int] = None,
    paragraph: Optional[str] = None,
    location: Optional[str] = "notes/a.md",
    ts_start: Optional[float] = None,
    ts_end: Optional[float] = None,
    metadata: Optional[dict] = None,
) -> Evidence:
    ref = SourceReference(
        material_id=material_id,
        location=location,
        timestamp_start=ts_start,
        timestamp_end=ts_end,
        page=page,
        line=line,
        paragraph=paragraph,
    )
    return Evidence(
        evidence_id=evidence_id
        or _deterministic_evidence_id(content, material_id, page),
        content=content,
        source_reference=ref,
        evidence_type=evidence_type,
        metadata=metadata or {},
    )


class TestEvidenceIdentity(unittest.TestCase):
    """Canonical identity behaviour."""

    def test_same_evidence_same_key(self):
        a = make_evidence(content="X", material_id="M1", page=3)
        b = make_evidence(content="X", material_id="M1", page=3)
        self.assertEqual(compute_canonical_key(a), compute_canonical_key(b))

    def test_different_page_different_key(self):
        a = make_evidence(content="X", material_id="M1", page=3)
        b = make_evidence(content="X", material_id="M1", page=7)
        self.assertNotEqual(compute_canonical_key(a), compute_canonical_key(b))

    def test_different_material_different_key(self):
        a = make_evidence(content="X", material_id="M1")
        b = make_evidence(content="X", material_id="M2")
        self.assertNotEqual(compute_canonical_key(a), compute_canonical_key(b))

    def test_different_type_different_key(self):
        a = make_evidence(content="X", material_id="M1", evidence_type=EvidenceType.OCR)
        b = make_evidence(content="X", material_id="M1", evidence_type=EvidenceType.PERSONAL_NOTE)
        self.assertNotEqual(compute_canonical_key(a), compute_canonical_key(b))

    def test_different_content_different_key(self):
        a = make_evidence(content="System is stable", material_id="M")
        b = make_evidence(content="System remains stable", material_id="M")
        self.assertNotEqual(compute_canonical_key(a), compute_canonical_key(b))

    def test_unicode_key_deterministic(self):
        for text in ["中文内容", "café ç ñ à", "La fotosíntesis", "光合作用"]:
            a = make_evidence(content=text, material_id="M")
            b = make_evidence(content=text, material_id="M")
            self.assertEqual(compute_canonical_key(a), compute_canonical_key(b))
            self.assertTrue(compute_canonical_key(a).startswith(CANONICAL_KEY_PREFIX))

    def test_no_datetime_dependency(self):
        a = make_evidence(content="T", material_id="M")
        key1 = compute_canonical_key(a)
        time.sleep(0.01)
        key2 = compute_canonical_key(make_evidence(content="T", material_id="M"))
        self.assertEqual(key1, key2)

    def test_float_identity_equivalent(self):
        a = make_evidence(content="T", material_id="M", ts_start=1.0, ts_end=2.0)
        b = make_evidence(content="T", material_id="M", ts_start=1.00, ts_end=2.00)
        self.assertEqual(compute_canonical_key(a), compute_canonical_key(b))

    def test_float_identity_difference(self):
        a = make_evidence(content="T", material_id="M", ts_start=1.0)
        b = make_evidence(content="T", material_id="M", ts_start=1.5)
        self.assertNotEqual(compute_canonical_key(a), compute_canonical_key(b))

    def test_whitespace_content_different(self):
        a = make_evidence(content="Hello", material_id="M")
        b = make_evidence(content="Hello ", material_id="M")
        c = make_evidence(content=" Hello", material_id="M")
        self.assertNotEqual(compute_canonical_key(a), compute_canonical_key(b))
        self.assertNotEqual(compute_canonical_key(a), compute_canonical_key(c))

    def test_newline_variants_differ(self):
        a = make_evidence(content="a\nb", material_id="M")
        b = make_evidence(content="a\r\nb", material_id="M")
        self.assertNotEqual(compute_canonical_key(a), compute_canonical_key(b))

    def test_case_matters(self):
        a = make_evidence(content="System", material_id="M")
        b = make_evidence(content="system", material_id="M")
        self.assertNotEqual(compute_canonical_key(a), compute_canonical_key(b))


class TestDeduplication(unittest.TestCase):
    """Exact-deterministic dedup policy."""

    def test_single_add(self):
        store = EvidenceStore()
        result = store.add(make_evidence(content="A", material_id="M"))
        self.assertEqual(result.status, AddStatus.ADDED)
        self.assertEqual(store.count(), 1)

    def test_duplicate_add(self):
        store = EvidenceStore()
        ev = make_evidence(content="A", material_id="M")
        first = store.add(ev)
        second = store.add(ev)
        self.assertEqual(first.status, AddStatus.ADDED)
        self.assertEqual(second.status, AddStatus.DUPLICATE)
        self.assertEqual(second.evidence_id, first.evidence_id)
        self.assertEqual(store.count(), 1)

    def test_duplicate_does_not_raise(self):
        store = EvidenceStore()
        ev = make_evidence(content="A", material_id="M")
        store.add(ev)
        store.add(ev)
        self.assertEqual(store.count(), 1)

    def test_batch_duplicate(self):
        store = EvidenceStore()
        ev = make_evidence(content="A", material_id="M")
        result = store.add_many([ev, ev, ev])
        self.assertEqual(result.added, 1)
        self.assertEqual(result.duplicates, 2)
        self.assertEqual(store.count(), 1)

    def test_cross_batch_duplicate(self):
        store = EvidenceStore()
        store.add(make_evidence(content="A", material_id="M"))
        ev2 = make_evidence(content="A", material_id="M")
        result = store.add(ev2)
        self.assertEqual(result.status, AddStatus.DUPLICATE)
        self.assertEqual(store.count(), 1)

    def test_cross_source_same_text_kept(self):
        store = EvidenceStore()
        pdf_ev = make_evidence(
            content="System stability",
            material_id="pdf-1",
            evidence_type=EvidenceType.DOCUMENT,
            page=1,
            metadata={"document_id": "doc1", "block_id": "b1"},
        )
        docx_ev = make_evidence(
            content="System stability",
            material_id="docx-1",
            evidence_type=EvidenceType.DOCUMENT,
            paragraph="3",
            metadata={"document_id": "doc2", "block_id": "b2"},
        )
        store.add(pdf_ev)
        store.add(docx_ev)
        self.assertEqual(store.count(), 2)

    def test_cross_page_same_text_kept(self):
        store = EvidenceStore()
        p3 = make_evidence(content="X", material_id="M", page=3, evidence_type=EvidenceType.DOCUMENT)
        p7 = make_evidence(content="X", material_id="M", page=7, evidence_type=EvidenceType.DOCUMENT)
        store.add(p3)
        store.add(p7)
        self.assertEqual(store.count(), 2)

    def test_same_source_same_page_deduped(self):
        store = EvidenceStore()
        a = make_evidence(content="X", material_id="M", page=3, evidence_type=EvidenceType.DOCUMENT)
        b = make_evidence(content="X", material_id="M", page=3, evidence_type=EvidenceType.DOCUMENT)
        self.assertEqual(compute_canonical_key(a), compute_canonical_key(b))
        store.add(a)
        store.add(b)
        self.assertEqual(store.count(), 1)

    def test_batch_preserves_input_order(self):
        store = EvidenceStore()
        evs = [make_evidence(content="C%d" % i, material_id="M%d" % i) for i in range(5)]
        store.add_many(evs)
        self.assertEqual([e.content for e in store.all()], ["C0", "C1", "C2", "C3", "C4"])

    def test_batch_duplicates_first_seen_order(self):
        store = EvidenceStore()
        a = make_evidence(content="A", material_id="M")
        b = make_evidence(content="B", material_id="M")
        c = make_evidence(content="C", material_id="M")
        store.add_many([a, b, a, c, b])
        self.assertEqual([e.content for e in store.all()], ["A", "B", "C"])
        self.assertEqual(store.count(), 3)

    def test_batch_existing_store(self):
        store = EvidenceStore()
        a = make_evidence(content="A", material_id="M")
        b = make_evidence(content="B", material_id="M")
        store.add_many([a, b])
        c = make_evidence(content="C", material_id="M")
        d = make_evidence(content="D", material_id="M")
        store.add_many([b, c, a, d])
        self.assertEqual([e.content for e in store.all()], ["A", "B", "C", "D"])

    def test_rejected_item_does_not_abort_batch(self):
        store = EvidenceStore()
        a = make_evidence(content="A", material_id="M")
        c = make_evidence(content="C", material_id="M")
        invalid = Evidence(content="   ", source_reference=SourceReference(material_id="M"))
        result = store.add_many([a, invalid, c])
        self.assertEqual(result.added, 2)
        self.assertEqual(result.rejected, 1)
        self.assertEqual(store.count(), 2)
        self.assertEqual([e.content for e in store.all()], ["A", "C"])

    def test_invalid_evidence_rejected(self):
        store = EvidenceStore()
        r = store.add(None)
        self.assertEqual(r.status, AddStatus.REJECTED)
        self.assertTrue(r.reason)
        self.assertIsNone(r.evidence_id)

    def test_non_evidence_type_rejected(self):
        store = EvidenceStore()
        r = store.add("not evidence")
        self.assertEqual(r.status, AddStatus.REJECTED)

    def test_whitespace_only_rejected(self):
        store = EvidenceStore()
        ev = Evidence(content="  \n  ", source_reference=SourceReference(material_id="M"))
        r = store.add(ev)
        self.assertEqual(r.status, AddStatus.REJECTED)

    def test_missing_source_reference_rejected(self):
        store = EvidenceStore()
        # Build an Evidence whose source_reference is not a SourceReference
        # instance (None) -- the store must reject it instead of crashing.
        # Bypass __post_init__ to create the malformed object.
        ev = object.__new__(Evidence)
        ev.evidence_id = "ev-missing-source"
        ev.content = "text"
        ev.language = Language.SPANISH
        ev.source_reference = None
        ev.confidence = 0.9
        ev.evidence_type = EvidenceType.OTHER
        ev.metadata = {}
        r = store.add(ev)
        self.assertEqual(r.status, AddStatus.REJECTED)

    def test_id_conflict_different_canonical(self):
        store = EvidenceStore()
        a = make_evidence(content="A", material_id="M", evidence_id="shared-id")
        store.add(a)
        b = make_evidence(content="B", material_id="M", evidence_id="shared-id")
        result = store.add(b)
        # Same ID, different canonical key: the first-seen record per ID
        # must win; the store never silently overwrites.
        self.assertEqual(store.count(active_only=False), 1)
        self.assertEqual(store.get("shared-id").content, "A")


class TestQueries(unittest.TestCase):
    """Query API determinism and correctness."""

    def test_get_existing(self):
        store = EvidenceStore()
        ev = make_evidence(content="A", material_id="M")
        store.add(ev)
        fetched = store.get(ev.evidence_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.content, "A")

    def test_get_missing_returns_none(self):
        store = EvidenceStore()
        self.assertIsNone(store.get("does-not-exist"))

    def test_contains(self):
        store = EvidenceStore()
        ev = make_evidence(content="A", material_id="M")
        store.add(ev)
        self.assertTrue(store.contains(ev.evidence_id))
        self.assertFalse(store.contains("nope"))

    def test_all_insertion_order(self):
        store = EvidenceStore()
        evs = [make_evidence(content="C%d" % i, material_id="M%d" % i) for i in range(10)]
        store.add_many(evs)
        self.assertEqual([e.content for e in store.all()], ["C%d" % i for i in range(10)])

    def test_empty_store(self):
        store = EvidenceStore()
        self.assertEqual(store.count(), 0)
        self.assertEqual(store.all(), [])

    def test_count_total(self):
        store = EvidenceStore()
        store.add_many([make_evidence(content="C%d" % i, material_id="M") for i in range(5)])
        self.assertEqual(store.count(), 5)

    def test_get_by_type_order(self):
        store = EvidenceStore()
        doc_a = make_evidence(content="A", material_id="M", evidence_type=EvidenceType.DOCUMENT)
        ocr_b = make_evidence(content="B", material_id="M", evidence_type=EvidenceType.OCR)
        doc_c = make_evidence(content="C", material_id="M", evidence_type=EvidenceType.DOCUMENT)
        store.add_many([doc_a, ocr_b, doc_c])
        docs = store.get_by_type(EvidenceType.DOCUMENT)
        self.assertEqual([e.content for e in docs], ["A", "C"])

    def test_get_by_source(self):
        store = EvidenceStore()
        m1a = make_evidence(content="A", material_id="M1")
        m1b = make_evidence(content="B", material_id="M1")
        m2 = make_evidence(content="C", material_id="M2")
        store.add_many([m1a, m1b, m2])
        self.assertEqual([e.content for e in store.get_by_source("M1")], ["A", "B"])
        self.assertEqual([e.content for e in store.get_by_source("M2")], ["C"])
        self.assertEqual(store.get_by_source("missing"), [])

    def test_get_by_document(self):
        store = EvidenceStore()
        ev1 = make_evidence(
            content="A", material_id="M", evidence_type=EvidenceType.DOCUMENT,
            metadata={"document_id": "doc1", "block_id": "b1"},
        )
        ev2 = make_evidence(
            content="B", material_id="M", evidence_type=EvidenceType.DOCUMENT,
            metadata={"document_id": "doc2"},
        )
        store.add_many([ev1, ev2])
        self.assertEqual(len(store.get_by_document("doc1")), 1)
        self.assertEqual(store.get_by_document("doc1")[0].content, "A")
        self.assertEqual(store.get_by_document("missing"), [])

    def test_get_by_session(self):
        store = EvidenceStore()
        ev1 = make_evidence(content="A", material_id="M", metadata={"class_session_id": "session-1"})
        ev2 = make_evidence(content="B", material_id="M")
        store.add_many([ev1, ev2])
        self.assertEqual(len(store.get_by_session("session-1")), 1)
        self.assertEqual(store.get_by_session("missing"), [])

    def test_get_by_location(self):
        store = EvidenceStore()
        p1 = make_evidence(content="A", material_id="M", page=1)
        p2 = make_evidence(content="B", material_id="M", page=2)
        noref = make_evidence(content="C", material_id="M")
        store.add_many([p1, p2, noref])
        self.assertEqual([e.content for e in store.get_by_location(page=1)], ["A"])
        self.assertEqual([e.content for e in store.get_by_location(page=None)], ["C"])

    def test_query_is_read_only(self):
        store = EvidenceStore()
        store.add_many([make_evidence(content="C%d" % i, material_id="M") for i in range(5)])
        _ = store.get_by_type(EvidenceType.PERSONAL_NOTE)
        _ = store.get_by_source("M")
        _ = store.get_by_document("missing")
        _ = store.get_by_session("missing")
        _ = store.get_by_location(page=1)
        self.assertEqual(store.count(), 5)

    def test_returned_object_is_copy(self):
        store = EvidenceStore()
        ev = make_evidence(content="A", material_id="M")
        store.add(ev)
        fetched = store.get(ev.evidence_id)
        self.assertIsNot(fetched, ev)
        fetched.content = "MUTATED"
        self.assertEqual(store.get(ev.evidence_id).content, "A")

    def test_clear(self):
        store = EvidenceStore()
        store.add_many([make_evidence(content="C%d" % i, material_id="M") for i in range(3)])
        store.clear()
        self.assertEqual(store.count(), 0)
        self.assertEqual(store.all(), [])
        # After clear, adding works normally
        store.add(make_evidence(content="A", material_id="M"))
        self.assertEqual(store.count(), 1)
        snap = store.to_dict()
        self.assertEqual(len(snap["evidences"]), 1)


class TestStatistics(unittest.TestCase):
    """Deterministic statistics."""

    def test_empty_statistics(self):
        store = EvidenceStore()
        stats = store.statistics()
        self.assertEqual(stats["total_count"], 0)
        self.assertEqual(stats["count_by_type"], {})
        self.assertEqual(stats["unique_materials"], 0)

    def test_total_count(self):
        store = EvidenceStore()
        store.add_many([make_evidence(content="C%d" % i, material_id="M") for i in range(7)])
        self.assertEqual(store.statistics()["total_count"], 7)

    def test_count_by_type(self):
        store = EvidenceStore()
        store.add(make_evidence(content="A", material_id="M1", evidence_type=EvidenceType.DOCUMENT, metadata={"document_id": "d1"}))
        store.add(make_evidence(content="B", material_id="M1", evidence_type=EvidenceType.OCR))
        store.add(make_evidence(content="C", material_id="M2", evidence_type=EvidenceType.DOCUMENT, metadata={"document_id": "d2"}))
        stats = store.statistics()
        self.assertEqual(stats["count_by_type"], {"OCR": 1, "document": 2})
        self.assertEqual(stats["count_by_material"], {"M1": 2, "M2": 1})
        self.assertEqual(stats["count_by_document"], {"d1": 1, "d2": 1})
        self.assertEqual(stats["unique_materials"], 2)
        self.assertEqual(stats["unique_documents"], 2)

    def test_statistics_deterministic(self):
        evs = [make_evidence(content="C%d" % i, material_id="M%d" % (i % 3)) for i in range(20)]
        s1 = EvidenceStore()
        s1.add_many(evs)
        s2 = EvidenceStore()
        s2.add_many(evs)
        self.assertEqual(s1.statistics(), s2.statistics())

    def test_statistics_excludes_retired_by_default(self):
        store = EvidenceStore()
        ev = make_evidence(content="A", material_id="M")
        store.add(ev)
        store.retire(ev.evidence_id)
        self.assertEqual(store.statistics()["total_count"], 0)
        self.assertEqual(store.statistics(active_only=False)["total_count"], 1)


class TestLifecycle(unittest.TestCase):
    """ACTIVE/RETIRED semantics, independent of Knowledge Review."""

    def test_new_evidence_is_active(self):
        store = EvidenceStore()
        ev = make_evidence(content="A", material_id="M")
        store.add(ev)
        self.assertEqual(store.get_state(ev.evidence_id), EvidenceState.ACTIVE)

    def test_retire_is_idempotent(self):
        store = EvidenceStore()
        ev = make_evidence(content="A", material_id="M")
        store.add(ev)
        self.assertTrue(store.retire(ev.evidence_id))
        self.assertTrue(store.retire(ev.evidence_id))
        self.assertEqual(store.get_state(ev.evidence_id), EvidenceState.RETIRED)

    def test_revive_is_idempotent(self):
        store = EvidenceStore()
        ev = make_evidence(content="A", material_id="M")
        store.add(ev)
        store.retire(ev.evidence_id)
        self.assertTrue(store.revive(ev.evidence_id))
        self.assertTrue(store.revive(ev.evidence_id))
        self.assertEqual(store.get_state(ev.evidence_id), EvidenceState.ACTIVE)

    def test_restore_remains_a_compatible_alias(self):
        store = EvidenceStore()
        ev = make_evidence(content="A", material_id="M")
        store.add(ev)
        store.retire(ev.evidence_id)
        self.assertTrue(store.restore(ev.evidence_id))
        self.assertEqual(store.get_state(ev.evidence_id), EvidenceState.ACTIVE)

    def test_retire_unknown_id_returns_false(self):
        store = EvidenceStore()
        self.assertFalse(store.retire("nope"))
        self.assertFalse(store.restore("nope"))
        self.assertIsNone(store.get_state("nope"))

    def test_get_still_returns_retired_by_id(self):
        store = EvidenceStore()
        ev = make_evidence(content="A", material_id="M")
        store.add(ev)
        store.retire(ev.evidence_id)
        fetched = store.get(ev.evidence_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.content, "A")

    def test_all_excludes_retired_by_default(self):
        store = EvidenceStore()
        evs = [make_evidence(content="C%d" % i, material_id="M") for i in range(3)]
        store.add_many(evs)
        store.retire(evs[1].evidence_id)
        self.assertEqual(len(store.all()), 2)
        self.assertEqual(len(store.all(active_only=False)), 3)

    def test_count_active_vs_total(self):
        store = EvidenceStore()
        evs = [make_evidence(content="C%d" % i, material_id="M") for i in range(3)]
        store.add_many(evs)
        store.retire(evs[0].evidence_id)
        self.assertEqual(store.count(), 2)
        self.assertEqual(store.count(active_only=False), 3)

    def test_no_review_status_applies(self):
        self.assertEqual(set(EvidenceState), {EvidenceState.ACTIVE, EvidenceState.RETIRED})


class TestSerialization(unittest.TestCase):
    """Snapshot round-trip and corruption handling."""

    def _one_evidence_snapshot(self) -> dict:
        store = EvidenceStore()
        store.add(make_evidence(content="Hola", material_id="M"))
        return store.to_dict()

    def test_empty_store_roundtrip(self):
        store = EvidenceStore()
        snapshot = store.to_dict()
        restored = EvidenceStore.from_dict(snapshot)
        self.assertEqual(restored.count(), 0)
        self.assertEqual(restored.to_dict(), snapshot)

    def test_one_evidence_roundtrip(self):
        store = EvidenceStore()
        ev = make_evidence(content="Hola", material_id="M")
        store.add(ev)
        snapshot = store.to_dict()
        restored = EvidenceStore.from_dict(snapshot)
        self.assertEqual(restored.count(), 1)
        got = restored.get(ev.evidence_id)
        self.assertIsNotNone(got)
        self.assertEqual(got.content, "Hola")

    def test_many_evidence_roundtrip(self):
        store = EvidenceStore()
        evs = [
            make_evidence(content="C%d" % i, material_id="M%d" % (i % 3),
                         evidence_type=EvidenceType.DOCUMENT,
                         metadata={"document_id": "doc%d" % i})
            for i in range(50)
        ]
        store.add_many(evs)
        snapshot = store.to_dict()
        restored = EvidenceStore.from_dict(snapshot)
        self.assertEqual(restored.count(), 50)
        self.assertEqual(restored.to_dict(), snapshot)

    def test_multilingual_roundtrip_preserves_content(self):
        store = EvidenceStore()
        texts = [
            "La fotosíntesis es un proceso vital.",
            "La fotosíntesi és un procés vital.",
            "光合作用是一个生物学过程。",
            "café ç ñ å ð",
        ]
        store.add_many([make_evidence(content=t, material_id="M") for t in texts])
        snapshot = store.to_dict()
        restored = EvidenceStore.from_dict(snapshot)
        self.assertEqual([e.content for e in restored.all()], texts)

    def test_snapshot_is_deterministic(self):
        evs = [make_evidence(content="C%d" % i, material_id="M") for i in range(20)]
        s1 = EvidenceStore()
        s1.add_many(evs)
        s2 = EvidenceStore()
        s2.add_many(evs)
        self.assertEqual(s1.to_dict(), s2.to_dict())

    def test_snapshot_contains_no_indexes(self):
        snapshot = EvidenceStore().to_dict()
        self.assertEqual(sorted(snapshot.keys()), ["evidences", "schema_version"])
        self.assertEqual(snapshot["schema_version"], SCHEMA_VERSION)

    def test_snapshot_roundtrip_after_retire(self):
        store = EvidenceStore()
        evs = [make_evidence(content="C%d" % i, material_id="M") for i in range(3)]
        store.add_many(evs)
        store.retire(evs[1].evidence_id)
        snapshot = store.to_dict()
        restored = EvidenceStore.from_dict(snapshot)
        self.assertEqual(restored.get_state(evs[1].evidence_id), EvidenceState.RETIRED)
        self.assertEqual(len(restored.all()), 2)
        self.assertEqual(len(restored.all(active_only=False)), 3)

    def test_snapshot_after_clear(self):
        store = EvidenceStore()
        store.add(make_evidence(content="A", material_id="M"))
        store.clear()
        snap = store.to_dict()
        self.assertEqual(snap["evidences"], [])
        restored = EvidenceStore.from_dict(snap)
        self.assertEqual(restored.count(), 0)

    def test_missing_schema_version_rejected(self):
        with self.assertRaises(EvidencePersistenceError):
            EvidenceStore.from_dict({"evidences": []})

    def test_wrong_schema_version_rejected(self):
        with self.assertRaises(EvidencePersistenceError):
            EvidenceStore.from_dict({"schema_version": 99, "evidences": []})

    def test_evidences_not_a_list_rejected(self):
        with self.assertRaises(EvidencePersistenceError):
            EvidenceStore.from_dict({"schema_version": SCHEMA_VERSION, "evidences": "bad"})

    def test_item_not_a_mapping_rejected(self):
        with self.assertRaises(EvidencePersistenceError):
            EvidenceStore.from_dict(
                {"schema_version": SCHEMA_VERSION, "evidences": ["not a mapping"]}
            )

    def test_bad_field_type_rejected(self):
        bad = {
            "evidence_id": "x",
            "content": 42,
            "language": "Unknown",
            "source_reference": {"material_id": "M"},
            "confidence": "UNCERTAIN",
            "evidence_type": "other",
            "metadata": {},
        }
        with self.assertRaises(EvidencePersistenceError):
            EvidenceStore.from_dict({"schema_version": SCHEMA_VERSION, "evidences": [bad]})

    def test_duplicate_canonical_key_in_snapshot_rejected(self):
        e1 = {
            "evidence_id": "id-1",
            "content": "same",
            "language": "Unknown",
            "source_reference": {"material_id": "M", "location": "notes/x.md",
                                 "timestamp_start": None, "timestamp_end": None,
                                 "page": None, "line": None, "paragraph": None},
            "confidence": "UNCERTAIN",
            "evidence_type": "personal_note",
            "metadata": {},
        }
        e2 = dict(e1)
        e2["evidence_id"] = "id-2"
        with self.assertRaises(EvidencePersistenceError):
            EvidenceStore.from_dict(
                {"schema_version": SCHEMA_VERSION, "evidences": [e1, e2]}
            )

    def test_unknown_state_rejected(self):
        e = make_evidence(content="A", material_id="M").to_dict()
        e["state"] = "REJECTED"
        with self.assertRaises(EvidencePersistenceError):
            EvidenceStore.from_dict(
                {"schema_version": SCHEMA_VERSION, "evidences": [e]}
            )

    def test_from_dict_on_non_mapping_rejected(self):
        with self.assertRaises(EvidencePersistenceError):
            EvidenceStore.from_dict("not a mapping")  # type: ignore[arg-type]

    def test_content_roundtrip_exact(self):
        text = "Exact text with \n newlines and éñç"
        store = EvidenceStore()
        store.add(make_evidence(content=text, material_id="M"))
        restored = EvidenceStore.from_dict(store.to_dict())
        self.assertEqual(restored.get(store.all()[0].evidence_id).content, text)


class TestPersistence(unittest.TestCase):
    """save/load behaviour on disk (JSON, UTF-8, atomic)."""

    def _store_with_evidences(self, n=5) -> EvidenceStore:
        store = EvidenceStore()
        store.add_many([make_evidence(content="C%d" % i, material_id="M%d" % (i % 3)) for i in range(n)])
        return store

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store_with_evidences()
            path = os.path.join(tmp, "store.json")
            store.save(path)
            loaded = EvidenceStore.load(path)
            self.assertEqual(loaded.count(), 5)
            self.assertEqual(loaded.to_dict(), store.to_dict())

    def test_save_load_multilingual(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore()
            texts = ["La fotosíntesis", "光合作用", "fotosíntesi"]
            store.add_many([make_evidence(content=t, material_id="M") for t in texts])
            path = os.path.join(tmp, "store.json")
            store.save(path)
            raw = open(path, "r", encoding="utf-8").read()
            self.assertIn("光合作用", raw)
            self.assertIn("La fotosíntesis", raw)
            loaded = EvidenceStore.load(path)
            self.assertEqual([e.content for e in loaded.all()], texts)

    def test_save_creates_parent_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store_with_evidences()
            nested = os.path.join(tmp, "a", "b", "store.json")
            store.save(nested)
            self.assertTrue(os.path.exists(nested))
            self.assertEqual(EvidenceStore.load(nested).count(), 5)

    def test_no_temp_file_remains_after_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store_with_evidences()
            path = os.path.join(tmp, "store.json")
            store.save(path)
            files = os.listdir(tmp)
            self.assertEqual(files, ["store.json"],
                             "atomic save should not leave temp files: %s" % files)

    def test_load_corrupted_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "corrupt.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"schema_version": 1, "evidences": [')
            with self.assertRaises(EvidencePersistenceError):
                EvidenceStore.load(path)

    def test_load_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "does-not-exist.json")
            with self.assertRaises(EvidencePersistenceError):
                EvidenceStore.load(missing)


class TestImmutability(unittest.TestCase):
    """External mutation must not corrupt the store."""

    def test_external_mutation_does_not_change_store(self):
        store = EvidenceStore()
        ev = make_evidence(content="A", material_id="M")
        store.add(ev)
        ev.content = "MUTATED"
        ev.source_reference.material_id = "MUTATED-M"
        ev.evidence_type = EvidenceType.OCR
        fetched = store.get(ev.evidence_id)
        self.assertEqual(fetched.content, "A")
        self.assertEqual(fetched.source_reference.material_id, "M")

    def test_external_mutation_does_not_affect_dedup(self):
        store = EvidenceStore()
        a = make_evidence(content="A", material_id="M")
        store.add(a)
        a.content = "CHANGED"
        a2 = make_evidence(content="A", material_id="M")
        result = store.add(a2)
        self.assertEqual(result.status, AddStatus.DUPLICATE)

    def test_mutation_before_readd_creates_new_record(self):
        store = EvidenceStore()
        a = make_evidence(content="A", material_id="M")
        store.add(a)
        # Same evidence_id but different content -> different canonical
        # identity.  The store keeps the first-seen record and rejects the
        # newcomer; it never overwrites silently.
        a.content = "totally different"
        result = store.add(a)
        self.assertEqual(result.status, AddStatus.REJECTED)
        self.assertEqual(store.count(), 1)
        self.assertEqual(store.get(a.evidence_id).content, "A")

    def test_evidence_id_immutable_in_store(self):
        store = EvidenceStore()
        ev = make_evidence(content="A", material_id="M")
        original_id = ev.evidence_id
        store.add(ev)
        # Mutating the external object must not affect the stored record.
        ev.evidence_id = "new-id"
        self.assertIsNone(store.get("new-id"))
        self.assertTrue(store.contains(original_id))
        self.assertEqual(store.get(original_id).evidence_id, original_id)



class TestThreadSafety(unittest.TestCase):
    """Concurrent writers behave correctly (10 threads same evidence)."""

    def test_10_threads_same_evidence(self):
        store = EvidenceStore()
        ev = make_evidence(content="A", material_id="M")
        results = [None] * 10
        barrier = threading.Barrier(10)

        def worker(index):
            barrier.wait()
            results[index] = store.add(ev)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        statuses = [r.status for r in results]
        self.assertEqual(statuses.count(AddStatus.ADDED), 1)
        self.assertEqual(statuses.count(AddStatus.DUPLICATE), 9)
        self.assertEqual(store.count(), 1)
        ids = {r.evidence_id for r in results}
        self.assertEqual(len(ids), 1)

    def test_concurrent_distinct_evidences(self):
        store = EvidenceStore()
        evs = [make_evidence(content="C%d" % i, material_id="M%d" % i) for i in range(50)]
        results = [None] * 50

        def worker(idx):
            results[idx] = store.add(evs[idx])

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(store.count(), 50)
        self.assertEqual(sum(1 for r in results if r.status == AddStatus.ADDED), 50)

    def test_concurrent_snapshot_is_consistent(self):
        store = EvidenceStore()
        evs = [make_evidence(content="C%d" % i, material_id="M") for i in range(20)]
        store.add_many(evs)
        snapshots = []

        def reader():
            snapshots.append(store.to_dict())

        threads = [threading.Thread(target=reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for snap in snapshots:
            self.assertEqual(len(snap["evidences"]), 20)
        self.assertEqual(snapshots[0], snapshots[-1])


class TestLargeStore(unittest.TestCase):
    """10,000 synthetic Evidence - add, dedup, query, snapshot."""

    def test_10k_inserts_dedups_and_queries(self):
        store = EvidenceStore()
        # 5000 distinct canonical keys, each appearing exactly twice.
        base = [
            make_evidence(
                content="Content-%d" % i,
                material_id="M%d" % (i % 2000),
                evidence_type=EvidenceType.DOCUMENT,
                metadata={"document_id": "doc%d" % (i % 1000)},
            )
            for i in range(5000)
        ]
        evs = [
            make_evidence(
                content="Content-%d" % i,
                material_id="M%d" % (i % 2000),
                evidence_type=EvidenceType.DOCUMENT,
                metadata={"document_id": "doc%d" % (i % 1000)},
            )
            for i in range(5000)
            for _ in range(2)
        ]
        assert len(evs) == 10000
        start = time.perf_counter()
        result = store.add_many(evs)
        insert_time = time.perf_counter() - start
        # 5000 distinct canonical keys -> 5000 ADDED, 5000 DUPLICATE
        self.assertEqual(result.added, 5000)
        self.assertEqual(result.duplicates, 5000)
        self.assertEqual(store.count(), 5000)
        doc_hits = store.get_by_document("doc42")
        self.assertGreater(len(doc_hits), 0)
        start = time.perf_counter()
        snap1 = store.to_dict()
        snap2 = store.to_dict()
        snapshot_time = time.perf_counter() - start
        self.assertEqual(snap1, snap2)
        some_ev = store.all()[0]
        self.assertIsNotNone(store.get(some_ev.evidence_id))
        self.assertLess(insert_time + snapshot_time, 30.0)


class TestRegressionAgainstExtractors(unittest.TestCase):
    """Store must work with real extractor output, not only synthetic data."""

    def test_note_evidence_from_extractor(self):
        from src.evidence_extractor import EvidenceExtractor

        with tempfile.TemporaryDirectory() as tmp:
            txt_path = os.path.join(tmp, "a.txt")
            with open(txt_path, "w", encoding="utf-8") as fh:
                fh.write("Hello class\n\nSecond paragraph.")
            material = Material(
                filename="a.txt", path=txt_path,
                material_type=MaterialType.NOTE, language=Language.UNKNOWN,
            )
            evs = EvidenceExtractor(material).extract()
            self.assertGreater(len(evs), 0)
            store = EvidenceStore()
            result = store.add_many(evs)
            self.assertEqual(result.added, len(evs))
            self.assertEqual(result.duplicates, 0)
            self.assertEqual(store.count(), len(evs))
            result2 = store.add_many(evs)
            self.assertEqual(result2.added, 0)
            self.assertEqual(result2.duplicates, len(evs))
            self.assertEqual(store.count(), len(evs))

    def test_transcript_evidence_roundtrip(self):
        from src.models import TranscriptSegment
        transcript = Transcript(
            material_id="audio-x.mp3",
            language=TranscriptLanguage.SPANISH,
            segments=[TranscriptSegment(text="hola mundo", start=0.0, end=1.0)],
        )
        ev = transcript.to_transcript_evidence()
        store = EvidenceStore()
        result = store.add(ev)
        self.assertEqual(result.status, AddStatus.ADDED)
        fetched = store.get(ev.evidence_id)
        self.assertEqual(fetched.content, ev.content)
        self.assertEqual(store.add(ev).status, AddStatus.DUPLICATE)

    def test_ocr_evidence_roundtrip(self):
        from src.ocr_processor import ocr_to_evidence
        ocr = OCRResult(
            material_id="board-1.png",
            language=OCRLanguage.SPANISH,
            segments=[
                OCRSegment(
                    text="L'equilibri del sistema",
                    bounding_box=BoundingBox(x=10.0, y=10.0, width=200.0, height=20.0),
                    confidence=0.9,
                    language=OCRLanguage.CATALAN,
                    page=1,
                ),
            ],
        )
        evs = ocr_to_evidence(ocr)
        self.assertGreater(len(evs), 0)
        store = EvidenceStore()
        result = store.add_many(evs)
        self.assertEqual(result.added, len(evs))
        self.assertEqual(store.get_by_source("board-1.png")[0].content, evs[0].content)

    def test_pdf_evidence_roundtrip_with_fixture(self):
        from src.document_input import parse_document
        from src.document_evidence import DocumentEvidenceExtractor

        fixture = Path(__file__).parent / "fixtures" / "documents" / "simple.pdf"
        if not fixture.exists():
            self.skipTest("simple.pdf fixture missing")
        parsed = parse_document(str(fixture))
        evs = DocumentEvidenceExtractor().extract(parsed)
        self.assertGreater(len(evs), 0)
        store = EvidenceStore()
        result = store.add_many(evs)
        self.assertEqual(result.added, len(evs))
        result2 = store.add_many(evs)
        self.assertEqual(result2.duplicates, len(evs))
        self.assertEqual(store.count(), len(evs))

    def test_docx_evidence_roundtrip_with_fixture(self):
        from src.document_input import parse_document
        from src.document_evidence import DocumentEvidenceExtractor

        fixture = Path(__file__).parent / "fixtures" / "documents" / "simple.docx"
        if not fixture.exists():
            self.skipTest("simple.docx fixture missing")
        parsed = parse_document(str(fixture))
        evs = DocumentEvidenceExtractor().extract(parsed)
        self.assertGreater(len(evs), 0)
        store = EvidenceStore()
        result = store.add_many(evs)
        self.assertEqual(result.added, len(evs))
        result2 = store.add_many(evs)
        self.assertEqual(result2.duplicates, len(evs))
        self.assertEqual(store.count(), len(evs))

    def test_pdf_traceability_regression(self):
        from src.document_input import parse_document
        from src.document_evidence import DocumentEvidenceExtractor

        fixture = Path(__file__).parent / "fixtures" / "documents" / "simple.pdf"
        if not fixture.exists():
            self.skipTest("simple.pdf fixture missing")
        parsed = parse_document(str(fixture))
        evs = DocumentEvidenceExtractor().extract(parsed)
        store = EvidenceStore()
        store.add_many(evs)
        # evidence -> source_reference -> page -> document chain intact
        for ev in evs:
            stored = store.get(ev.evidence_id)
            self.assertIsNotNone(stored)
            self.assertEqual(stored.metadata.get("document_id"), parsed.document_id)
            self.assertEqual(stored.source_reference.material_id,
                             parsed.material_id or parsed.document_id)

    def test_docx_traceability_regression(self):
        from src.document_input import parse_document
        from src.document_evidence import DocumentEvidenceExtractor

        fixture = Path(__file__).parent / "fixtures" / "documents" / "simple.docx"
        if not fixture.exists():
            self.skipTest("simple.docx fixture missing")
        parsed = parse_document(str(fixture))
        evs = DocumentEvidenceExtractor().extract(parsed)
        store = EvidenceStore()
        store.add_many(evs)
        for ev in evs:
            stored = store.get(ev.evidence_id)
            self.assertIsNotNone(stored)
            self.assertEqual(stored.metadata.get("block_type"), ev.metadata.get("block_type"))

    def test_integrator_and_store_agree_on_dedup(self):
        from src.integration import EvidenceIntegrator

        store = EvidenceStore()
        evs = [make_evidence(content="C%d" % i, material_id="M") for i in range(5)]
        store.add_many(evs)
        integration = EvidenceIntegrator.integrate(evs)
        self.assertEqual(len(integration.evidence), 5)
        self.assertEqual(len(integration.duplicate_groups), 0)
        self.assertEqual(store.count(), 5)

    def test_knowledge_pipeline_regression(self):
        from src.knowledge_pipeline import KnowledgePipeline

        pipeline = KnowledgePipeline()
        evs = [make_evidence(content="X es importante. Y también.", material_id="M%d" % i)
               for i in range(3)]
        result = pipeline.process_evidence(evs)
        self.assertIsNotNone(result.structure)
        store = EvidenceStore()
        store.add_many(evs)
        result2 = pipeline.process_evidence(store.all())
        self.assertEqual(result2.evidence_ids, [e.evidence_id for e in evs])
        self.assertEqual(
            [kp.knowledge_id for kp in result.structure.knowledge_points.values()],
            [kp.knowledge_id for kp in result2.structure.knowledge_points.values()],
        )

    def test_knowledge_review_regression(self):
        from src.knowledge_review import KnowledgeReviewService
        from src.knowledge_structure import KnowledgePoint, KnowledgeStructure

        ev = make_evidence(content="A")
        store = EvidenceStore()
        store.add(ev)
        structure = KnowledgeStructure()
        kp = KnowledgePoint(title="Test", content="X", evidence_refs=[ev.evidence_id])
        structure.add_knowledge_point(kp)
        service = KnowledgeReviewService()
        record = service.confirm(structure, kp.knowledge_id)
        self.assertEqual(record.knowledge_point_id, kp.knowledge_id)
        self.assertEqual(kp.review_status, "confirmed")
        self.assertEqual(store.count(), 1)


class TestBatchResultType(unittest.TestCase):
    """BatchAddResult is a deterministic container."""

    def test_batch_result_type(self):
        store = EvidenceStore()
        result = store.add_many([])
        self.assertIsInstance(result, BatchAddResult)
        self.assertEqual(result.added, 0)
        self.assertEqual(result.duplicates, 0)
        self.assertEqual(result.rejected, 0)
        self.assertEqual(result.evidence_ids, ())

    def test_batch_result_deterministic_for_same_input(self):
        evs = [make_evidence(content="C%d" % i, material_id="M") for i in range(3)]
        r1 = EvidenceStore().add_many(evs)
        r2 = EvidenceStore().add_many(evs)
        self.assertEqual(r1.added, r2.added)
        self.assertEqual(r1.duplicates, r2.duplicates)
        self.assertEqual(r1.rejected, r2.rejected)
        self.assertEqual(r1.evidence_ids, r2.evidence_ids)

    def test_add_result_type(self):
        store = EvidenceStore()
        result = store.add(make_evidence(content="A", material_id="M"))
        self.assertIsInstance(result, EvidenceAddResult)
        self.assertEqual(result.status, AddStatus.ADDED)


class TestScopeAudit(unittest.TestCase):
    """Confirm Task 23 introduces no new forbidden behaviour."""

    def test_no_new_dependencies_in_store_module(self):
        import inspect
        import src.evidence_store as mod
        source = inspect.getsource(mod)
        for name in ["pandas", "numpy", "sqlalchemy", "sqlite3", "redis",
                     "elasticsearch", "openai", "whisper", "transformers"]:
            self.assertNotIn("import %s" % name, source,
                             "Task 23 must not import %s" % name)

    def test_no_network_imports(self):
        import inspect
        import src.evidence_store as mod
        source = inspect.getsource(mod)
        for forbidden in ["socket", "requests", "urllib"]:
            self.assertNotIn("import %s" % forbidden, source)
            self.assertNotIn("from %s" % forbidden, source)

    def test_no_datetime_now_in_store_module(self):
        import inspect
        import src.evidence_store as mod
        source = inspect.getsource(mod)
        # The module must not use wall-clock time or random UUIDs for
        # identity (docstrings may mention the names; imports must not).
        self.assertNotIn("import datetime", source)
        self.assertNotIn("import uuid", source)
        self.assertNotIn("datetime.now(", source)
        self.assertNotIn("uuid.uuid4(", source)

    def test_store_does_not_mutate_evidence(self):
        store = EvidenceStore()
        ev = make_evidence(content="A", material_id="M")
        snapshot_before = ev.to_dict()
        store.add(ev)
        self.assertEqual(ev.to_dict(), snapshot_before)
        ev.content = "MUTATED"
        fetched = store.get(ev.evidence_id)
        self.assertEqual(fetched.content, "A")
        self.assertIsNot(fetched, ev)
        self.assertEqual(
            store.add(make_evidence(content="A", material_id="M")).status,
            AddStatus.DUPLICATE,
        )


if __name__ == "__main__":
    unittest.main()
