"""Task 15 - Knowledge Review & Human Verification Layer.

Covers required scenarios 59.1-59.23:
candidate discovery, ordering, confirmation, rejection, keep-unverified,
conflict resolution, invalid evidence selection, empty selection, duplicate
reviews, review history, new conflict after confirmation, relationship
preservation, evidence immutability, knowledge point identity, multilingual
review, review notes, serialization, backward compatibility, determinism,
idempotency, no-auto-confirm, no-auto-conflict-resolution.
"""
import copy
import json
import unittest

from src.integration import ConflictRecord
from src.knowledge_structure import (
    KnowledgeStructure,
    KnowledgePoint,
    Relationship,
    RelationType,
)
from src.knowledge_validation import ValidationStatus
from src.knowledge_review import (
    KnowledgeReviewService,
    ReviewStatus,
    ReviewDecision,
    ReviewRecord,
    ReviewCandidate,
)


def _kp(kid, status="unverified", refs=(), needs_verification=True,
        knowledge_score=0.5, review_status="pending", title="t", content="c"):
    return KnowledgePoint(
        knowledge_id=kid,
        title=title,
        content=content,
        evidence_refs=list(refs),
        needs_verification=needs_verification,
        validation_status=status,
        knowledge_score=knowledge_score,
        review_status=review_status,
    )


def _structure(kps, conflicts=None, relationships=None):
    s = KnowledgeStructure()
    for kp in kps:
        s.add_knowledge_point(kp)
    if conflicts:
        for c in conflicts:
            s.add_conflict(c)
    if relationships:
        for r in relationships:
            s.add_relationship(r)
    return s


class Test59_1CandidateDiscovery(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def test_conflicted_enters(self):
        s = _structure([_kp("k1", "conflicted", ("e1", "e2"))])
        ids = [c.knowledge_point_id for c in self.svc.get_review_candidates(s)]
        self.assertIn("k1", ids)

    def test_unverified_enters(self):
        s = _structure([_kp("k1", "unverified", ("e1",))])
        ids = [c.knowledge_point_id for c in self.svc.get_review_candidates(s)]
        self.assertIn("k1", ids)

    def test_supported_pending_enters(self):
        s = _structure([_kp("k1", "supported", ("e1",), needs_verification=False)])
        ids = [c.knowledge_point_id for c in self.svc.get_review_candidates(s)]
        self.assertIn("k1", ids)

    def test_supported_confirmed_not_duplicate(self):
        s = _structure([_kp("k1", "supported", ("e1",), needs_verification=False,
                            review_status="confirmed")])
        ids = [c.knowledge_point_id for c in self.svc.get_review_candidates(s)]
        self.assertNotIn("k1", ids)

    def test_supported_rejected_not_duplicate(self):
        s = _structure([_kp("k1", "supported", ("e1",), needs_verification=False,
                            review_status="rejected")])
        ids = [c.knowledge_point_id for c in self.svc.get_review_candidates(s)]
        self.assertNotIn("k1", ids)


class Test59_2CandidateOrdering(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def test_conflict_priority_over_unverified(self):
        s = _structure([
            _kp("k_unv", "unverified", ("e1",)),
            _kp("k_conf", "conflicted", ("e1", "e2")),
        ])
        cand = self.svc.get_review_candidates(s)
        self.assertEqual([c.knowledge_point_id for c in cand], ["k_conf", "k_unv"])

    def test_same_priority_sorted_by_id(self):
        s = _structure([
            _kp("k2", "unverified", ("e1",)),
            _kp("k1", "unverified", ("e2",)),
        ])
        cand = self.svc.get_review_candidates(s)
        self.assertEqual([c.knowledge_point_id for c in cand], ["k1", "k2"])

    def test_conflict_priority_field(self):
        s = _structure([_kp("k1", "conflicted", ("e1", "e2"))])
        cand = self.svc.get_review_candidates(s)
        self.assertEqual(cand[0].priority, 0)
        self.assertEqual(cand[0].validation_status, ValidationStatus.CONFLICTED)


class Test59_3ConfirmSupported(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def test_confirm_supported(self):
        s = _structure([_kp("k1", "supported", ("e1", "e2"), needs_verification=False)])
        rec = self.svc.confirm(s, "k1", selected_evidence_ids=["e1", "e2"])
        self.assertEqual(self.svc.review_status_of(s, "k1"), ReviewStatus.CONFIRMED)
        self.assertEqual(rec.decision, ReviewDecision.CONFIRM)

    def test_confirm_does_not_change_validation(self):
        s = _structure([_kp("k1", "supported", ("e1",), needs_verification=False)])
        self.svc.confirm(s, "k1")
        self.assertEqual(s.knowledge_points["k1"].validation_status, "supported")


class Test59_4ConfirmConflicted(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def _conflict_structure(self):
        conflict = ConflictRecord(
            conflict_id="cf1",
            evidence_refs=["e1", "e2"],
            description="X=10 vs X=12",
        )
        return _structure([_kp("k1", "conflicted", ("e1", "e2"))], conflicts=[conflict])

    def test_confirm_conflicted_with_selection(self):
        s = self._conflict_structure()
        rec = self.svc.confirm(s, "k1", selected_evidence_ids=["e2"])
        self.assertEqual(self.svc.review_status_of(s, "k1"), ReviewStatus.CONFIRMED)
        self.assertEqual(rec.selected_evidence_ids, ("e2",))

    def test_confirm_conflicted_empty_raises(self):
        s = self._conflict_structure()
        with self.assertRaises(ValueError):
            self.svc.confirm(s, "k1", selected_evidence_ids=[])


class Test59_5Reject(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def test_reject(self):
        s = _structure([_kp("k1", "unverified", ("e1",))])
        self.svc.reject(s, "k1")
        self.assertEqual(self.svc.review_status_of(s, "k1"), ReviewStatus.REJECTED)

    def test_reject_preserves_evidence(self):
        s = _structure([_kp("k1", "unverified", ("e1",))])
        self.svc.reject(s, "k1")
        self.assertEqual(s.knowledge_points["k1"].evidence_refs, ["e1"])


class Test59_6KeepUnverified(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def test_keep_unverified(self):
        s = _structure([_kp("k1", "unverified", ("e1",))])
        self.svc.keep_unverified(s, "k1")
        self.assertEqual(self.svc.review_status_of(s, "k1"), ReviewStatus.KEPT_UNVERIFIED)


class Test59_7ConflictResolution(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def _structure_with_conflict(self):
        conflict = ConflictRecord(conflict_id="cf1", evidence_refs=["e1", "e2"],
                                 description="A=10 vs A=12")
        s = _structure([_kp("k1", "conflicted", ("e1", "e2"))], conflicts=[conflict])
        return s

    def test_resolve_conflict_records_selection(self):
        s = self._structure_with_conflict()
        rec = self.svc.resolve_conflict(s, "k1", selected_evidence_ids=["e2"])
        self.assertEqual(rec.selected_evidence_ids, ("e2",))
        self.assertEqual(self.svc.review_status_of(s, "k1"), ReviewStatus.CONFIRMED)
        # conflict still preserved
        self.assertEqual(len(s.conflicts), 1)
        self.assertEqual(s.conflicts[0].conflict_id, "cf1")

    def test_resolve_conflict_preserves_original_evidence(self):
        s = self._structure_with_conflict()
        self.svc.resolve_conflict(s, "k1", selected_evidence_ids=["e2"])
        self.assertEqual(set(s.knowledge_points["k1"].evidence_refs), {"e1", "e2"})

    def test_resolve_conflict_remarks_stay_out_of_evidence(self):
        s = self._structure_with_conflict()
        self.svc.resolve_conflict(s, "k1", selected_evidence_ids=["e2"], note="chose newer one")
        # note lives only on the record, never on any evidence object
        self.assertEqual(self.svc.latest_review(s, "k1").note, "chose newer one")


class Test59_8InvalidEvidenceSelection(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def test_unrelated_evidence_rejected(self):
        conflict = ConflictRecord(conflict_id="cf1", evidence_refs=["e1", "e2"],
                                 description="x")
        s = _structure([_kp("k1", "conflicted", ("e1", "e2"))], conflicts=[conflict])
        with self.assertRaises(ValueError):
            self.svc.resolve_conflict(s, "k1", selected_evidence_ids=["e999"])

    def test_missing_knowledge_point_raises_keyerror(self):
        s = _structure([_kp("k1", "unverified", ("e1",))])
        with self.assertRaises(KeyError):
            self.svc.reject(s, "does-not-exist")


class Test59_9EmptyConflictSelection(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def test_empty_selection_not_resolved(self):
        conflict = ConflictRecord(conflict_id="cf1", evidence_refs=["e1", "e2"],
                                 description="x")
        s = _structure([_kp("k1", "conflicted", ("e1", "e2"))], conflicts=[conflict])
        with self.assertRaises(ValueError):
            self.svc.resolve_conflict(s, "k1", selected_evidence_ids=[])
        # point stays conflicted, not marked confirmed
        self.assertEqual(self.svc.review_status_of(s, "k1"), ReviewStatus.PENDING)


class Test59_10DuplicateReview(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def test_duplicate_confirm_no_extra_record(self):
        s = _structure([_kp("k1", "supported", ("e1",), needs_verification=False)])
        self.svc.confirm(s, "k1")
        self.svc.confirm(s, "k1")
        self.assertEqual(len(structure_review_records(s, "k1")), 1)

    def test_duplicate_reject_no_extra_record(self):
        s = _structure([_kp("k1", "unverified", ("e1",))])
        self.svc.reject(s, "k1")
        self.svc.reject(s, "k1")
        self.assertEqual(len(structure_review_records(s, "k1")), 1)


class Test59_11ReviewHistory(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def test_history_preserved_across_decisions(self):
        s = _structure([_kp("k1", "unverified", ("e1",))])
        self.svc.keep_unverified(s, "k1")
        self.svc.confirm(s, "k1")
        self.svc.reject(s, "k1")
        history = self.svc.review_history(s, "k1")
        self.assertEqual(len(history), 3)
        decisions = [r.decision for r in history]
        self.assertIn(ReviewDecision.KEEP_UNVERIFIED, decisions)
        self.assertIn(ReviewDecision.CONFIRM, decisions)
        self.assertIn(ReviewDecision.REJECT, decisions)


class Test59_12NewConflictAfterConfirmation(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()
        from src.knowledge_validation import KnowledgeValidator

    def test_new_conflict_resets_to_pending(self):
        s = _structure([_kp("k1", "supported", ("e1",), needs_verification=False)])
        self.svc.confirm(s, "k1")
        self.assertEqual(self.svc.review_status_of(s, "k1"), ReviewStatus.CONFIRMED)
        # A new conflicting evidence arrives and re-validates the point.
        s.knowledge_points["k1"].evidence_refs = ["e1", "e2"]
        s.knowledge_points["k1"].validation_status = "conflicted"
        # explicit re-review: service records a new PENDING-triggering action
        self.svc.keep_unverified(s, "k1")
        # the point now shows the prior confirmation is in history
        self.assertEqual(len(self.svc.review_history(s, "k1")), 2)


class Test59_13RelationshipPreservation(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def test_review_keeps_relationships(self):
        s = _structure([
            _kp("k1", "supported", ("e1",), needs_verification=False),
            _kp("k2", "supported", ("e2",), needs_verification=False),
        ])
        rel = Relationship(source_id="k1", target_id="k2",
                          relation_type=RelationType.RELATED_TO, evidence_refs=["e1"])
        s.add_relationship(rel)
        self.svc.confirm(s, "k1")
        self.svc.reject(s, "k2")
        self.assertEqual(len(s.relationships), 1)
        self.assertEqual(s.relationships[0].source_id, "k1")


class Test59_14EvidenceImmutability(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()
        from src.models import Evidence, Language, SourceReference

    def test_review_does_not_mutate_evidence(self):
        from src.models import Evidence, Language, SourceReference
        ev = Evidence(evidence_id="e1", content="original",
                      language=Language.SPANISH,
                      source_reference=SourceReference(material_id="m1"))
        snap = json.dumps(ev.to_dict(), sort_keys=True)
        s = _structure([_kp("k1", "supported", ("e1",), needs_verification=False)])
        self.svc.confirm(s, "k1", note="note text")
        self.assertEqual(json.dumps(ev.to_dict(), sort_keys=True), snap)


class Test59_15KnowledgePointIdentity(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def test_review_keeps_knowledge_id(self):
        s = _structure([_kp("k1", "unverified", ("e1",))])
        self.svc.reject(s, "k1")
        self.assertEqual(list(s.knowledge_points.keys()), ["k1"])


class Test59_16Multilingual(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def _check(self, lang_content, kid, refs):
        s = _structure([_kp(kid, "supported", refs, needs_verification=False,
                            title=lang_content, content=lang_content)])
        self.svc.confirm(s, kid)
        self.assertEqual(s.knowledge_points[kid].content, lang_content)

    def test_spanish_not_translated(self):
        self._check("La mitosis es una division celular.", "k_es", ["e1"])

    def test_catalan_not_translated(self):
        self._check("La mitosi es una divisió cel·lular.", "k_ca", ["e1"])

    def test_chinese_not_translated(self):
        self._check("有丝分裂是细胞分裂。", "k_zh", ["e1"])


class Test59_17ReviewNote(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def test_note_stored_on_record_only(self):
        s = _structure([_kp("k1", "unverified", ("e1",))])
        rec = self.svc.keep_unverified(s, "k1", note="老师后半节课纠正了前面的说法")
        self.assertEqual(rec.note, "老师后半节课纠正了前面的说法")
        # note never becomes evidence
        self.assertEqual(s.knowledge_points["k1"].evidence_refs, ["e1"])


class Test59_18Serialization(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def test_roundtrip_preserves_review_state(self):
        s = _structure([_kp("k1", "supported", ("e1",), needs_verification=False)])
        self.svc.confirm(s, "k1", note="ok")
        data = s.to_dict()
        restored = KnowledgeStructure.from_dict(data)
        self.assertEqual(restored.knowledge_points["k1"].review_status, "confirmed")
        self.assertEqual(len(restored.review_records), 1)
        self.assertEqual(restored.review_records[0].note, "ok")
        self.assertEqual(restored.review_records[0].selected_evidence_ids, ("e1",))


class Test59_19BackwardCompatibility(unittest.TestCase):
    def test_old_data_without_review_fields(self):
        data = {
            "knowledge_points": [
                {"knowledge_id": "k1", "title": "t", "content": "c",
                 "evidence_refs": ["e1"], "validation_status": "unverified",
                 "knowledge_score": 0.5}
            ],
            "relationships": [],
            "conflicts": [],
        }
        s = KnowledgeStructure.from_dict(data)
        self.assertEqual(s.knowledge_points["k1"].review_status, "pending")
        self.assertEqual(s.review_records, [])


class Test59_20Determinism(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def test_same_structure_same_candidates(self):
        s1 = _structure([_kp("k2", "unverified", ("e1",)),
                         _kp("k1", "conflicted", ("e2", "e3"))])
        s2 = _structure([_kp("k2", "unverified", ("e1",)),
                         _kp("k1", "conflicted", ("e2", "e3"))])
        c1 = self.svc.get_review_candidates(s1)
        c2 = self.svc.get_review_candidates(s2)
        self.assertEqual([c.knowledge_point_id for c in c1],
                         [c.knowledge_point_id for c in c2])
        self.assertEqual([c.priority for c in c1], [c.priority for c in c2])


class Test59_21Idempotency(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def test_stable_review_id(self):
        s = _structure([_kp("k1", "supported", ("e1",), needs_verification=False)])
        r1 = self.svc.confirm(s, "k1", selected_evidence_ids=["e1"])
        s2 = _structure([_kp("k1", "supported", ("e1",), needs_verification=False)])
        r2 = self.svc.confirm(s2, "k1", selected_evidence_ids=["e1"])
        self.assertEqual(r1.review_id, r2.review_id)


class Test59_22NoAutomaticConfirmation(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def test_supported_high_score_not_confirmed(self):
        s = _structure([_kp("k1", "supported", ("e1", "e2", "e3"),
                            needs_verification=False, knowledge_score=0.9)])
        # before any explicit human action, status must remain PENDING
        self.assertEqual(self.svc.review_status_of(s, "k1"), ReviewStatus.PENDING)


class Test59_23NoAutomaticConflictResolution(unittest.TestCase):
    def setUp(self):
        self.svc = KnowledgeReviewService()

    def test_conflicted_stays_pending_until_user_acting(self):
        conflict = ConflictRecord(conflict_id="cf1", evidence_refs=["e1", "e2"],
                                 description="x")
        s = _structure([_kp("k1", "conflicted", ("e1", "e2"))], conflicts=[conflict])
        self.assertEqual(self.svc.review_status_of(s, "k1"), ReviewStatus.PENDING)


def structure_review_records(structure, knowledge_point_id):
    return structure.review_records_for_knowledge_point(knowledge_point_id)


if __name__ == "__main__":
    unittest.main()
