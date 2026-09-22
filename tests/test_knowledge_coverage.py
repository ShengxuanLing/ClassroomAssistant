"""Tests for src/knowledge_coverage.py (Task 27).

Pure, deterministic coverage / gap analysis layer on top of Task 26.
Covers: enums, reports, analyzer, determinism, idempotency, snapshot
isolation, course isolation, relation/topic/evidence independence,
multilingual preservation, matrix consistency, empty states, end-to-end
scenarios (spec 96-99), performance, and serialization round-trips.
"""
from __future__ import annotations

import time

import pytest

from src.models import Course, KnowledgePoint, ClassSession
from src.knowledge_organization import KnowledgeOrganizationService
from src.knowledge_coverage import (
    KnowledgeCoverageStatus,
    KnowledgeGapType,
    KnowledgeGap,
    KnowledgeCoverageReport,
    TopicCoverageReport,
    SessionCoverageReport,
    CourseCoverageTimeline,
    KnowledgeFrequency,
    CourseGapReport,
    ConflictCoverageItem,
    KnowledgeOrphanReport,
    KnowledgeEvidenceCoverage,
    ValidationReviewMatrix,
    CourseKnowledgeStatusSummary,
    KnowledgeCoverageAnalyzer,
    CoverageAnalysisError,
    CoverageErrorCode,
    CoverageSchemaError,
)

COURSE_ID = "course-mat"
COURSE_B = "course-fis"


def make_course(course_id: str = COURSE_ID, name: str = "Matemáticas") -> Course:
    return Course(course_id=course_id, name=name, code="MAT26")


def make_kp(
    kp_id: str,
    *,
    validation_status: str = "unverified",
    knowledge_score: float = 0.0,
    review_status: str = "pending",
    evidence_refs=None,
) -> KnowledgePoint:
    return KnowledgePoint(
        knowledge_id=kp_id,
        title="title",
        content="content",
        evidence_refs=list(evidence_refs or []),
        validation_status=validation_status,
        knowledge_score=knowledge_score,
        review_status=review_status,
    )


def make_session(course_id: str, number: int, session_id: str = "") -> ClassSession:
    return ClassSession(
        session_id=session_id or f"session-{number}",
        course_id=course_id,
        session_number=number,
    )


def build_service(
    kps=None,
    topic_names=(),
    topic_kps=None,
    sessions=(),
    session_kps=None,
    course_id: str = COURSE_ID,
):
    """Convenience builder: register KPs, create topics, link
    memberships and sessions, and return the service + topic ids."""
    course = make_course(course_id)
    svc = KnowledgeOrganizationService(course)
    for kp in kps or []:
        svc.register_knowledge_point(kp)
    topic_ids = {}
    for name in topic_names:
        topic_ids[name] = svc.add_topic(name).topic_id
    for name, kp_id in (topic_kps or []):
        svc.add_knowledge_to_topic(topic_ids[name], kp_id)
    for number in sessions:
        s = make_session(course_id, number)
        svc.register_session(s)
    for sid, kp_id in (session_kps or []):
        svc.add_knowledge_to_session(sid, kp_id)
    return svc, topic_ids


class TestEnums:
    def test_coverage_status_values(self):
        assert KnowledgeCoverageStatus.COVERED.value == "covered"
        assert KnowledgeCoverageStatus.UNCOVERED.value == "uncovered"
        assert set(KnowledgeCoverageStatus.__members__) == {"COVERED", "UNCOVERED"}

    def test_coverage_status_from_count(self):
        assert KnowledgeCoverageStatus.from_membership_count(0) is KnowledgeCoverageStatus.UNCOVERED
        assert KnowledgeCoverageStatus.from_membership_count(1) is KnowledgeCoverageStatus.COVERED

    def test_no_mastery_states_in_enum(self):
        values = {s.value for s in KnowledgeCoverageStatus}
        for bad in ("mastered", "learned", "understood", "weak", "strong"):
            assert bad not in values

    def test_gap_type_declaration_order(self):
        assert [g.name for g in KnowledgeGapType] == [
            "UNASSIGNED", "UNCOVERED", "CONFLICTED", "UNVERIFIED", "REVIEW_PENDING",
        ]
        values = {g.value for g in KnowledgeGapType}
        assert "student_gap" not in values

    def test_error_code_values(self):
        assert CoverageErrorCode.INVALID_STRUCTURE.value == "INVALID_STRUCTURE"
        assert CoverageErrorCode.MISSING_TOPIC.value == "MISSING_TOPIC"
        assert CoverageErrorCode.MISSING_SESSION.value == "MISSING_SESSION"
        assert CoverageErrorCode.INVALID_SCHEMA_VERSION.value == "INVALID_SCHEMA_VERSION"

    def test_exception_carries_code(self):
        err = CoverageAnalysisError(CoverageErrorCode.INVALID_STRUCTURE, "boom")
        assert err.code is CoverageErrorCode.INVALID_STRUCTURE
        assert isinstance(err, CoverageAnalysisError)


class TestCourseCoverageReport:
    def test_empty_course_zero_ratios(self):
        svc, _ = build_service()
        rep = KnowledgeCoverageAnalyzer(svc).analyze_course()
        assert rep.total_knowledge_points == 0
        assert rep.covered_knowledge_points == 0
        assert rep.coverage_ratio == 0.0
        assert rep.assignment_ratio == 0.0

    def test_all_covered_and_assigned(self):
        svc, _ = build_service(
            kps=[make_kp("kp-1"), make_kp("kp-2")],
            topic_names=["Algebra"],
            topic_kps=[("Algebra", "kp-1"), ("Algebra", "kp-2")],
            sessions=[1],
            session_kps=[("session-1", "kp-1"), ("session-1", "kp-2")],
        )
        rep = KnowledgeCoverageAnalyzer(svc).analyze_course()
        assert rep.total_knowledge_points == 2
        assert rep.covered_knowledge_points == 2
        assert rep.uncovered_knowledge_points == 0
        assert rep.assigned_knowledge_points == 2
        assert rep.coverage_ratio == 1.0
        assert rep.assignment_ratio == 1.0

    def test_none_covered(self):
        svc, _ = build_service(kps=[make_kp("kp-1")])
        rep = KnowledgeCoverageAnalyzer(svc).analyze_course()
        assert rep.covered_knowledge_points == 0
        assert rep.uncovered_knowledge_points == 1
        assert rep.coverage_ratio == 0.0

    def test_partial_coverage_ratio(self):
        svc, _ = build_service(
            kps=[make_kp(f"kp-{i}") for i in range(10)],
            sessions=[1],
            session_kps=[(f"session-1", f"kp-{i}") for i in range(6)],
        )
        rep = KnowledgeCoverageAnalyzer(svc).analyze_course()
        assert rep.covered_knowledge_points == 6
        assert rep.uncovered_knowledge_points == 4
        assert rep.coverage_ratio == pytest.approx(0.6)

    def test_counts_conflicted_unverified_pending(self):
        svc, _ = build_service(
            kps=[
                make_kp("kp-1", validation_status="conflicted", review_status="confirmed"),
                make_kp("kp-2", validation_status="unverified"),
                make_kp("kp-3", validation_status="supported", review_status="confirmed"),
                make_kp("kp-4", review_status="pending"),
            ]
        )
        rep = KnowledgeCoverageAnalyzer(svc).analyze_course()
        assert rep.conflicted_knowledge_points == 1
        assert rep.unverified_knowledge_points == 2
        assert rep.review_pending_knowledge_points == 2

    def test_unique_counting_across_sessions(self):
        svc, _ = build_service(
            kps=[make_kp("kp-1")],
            sessions=[1, 2, 3],
            session_kps=[
                ("session-1", "kp-1"), ("session-2", "kp-1"), ("session-3", "kp-1"),
            ],
        )
        rep = KnowledgeCoverageAnalyzer(svc).analyze_course()
        assert rep.total_knowledge_points == 1
        assert rep.covered_knowledge_points == 1
        assert rep.coverage_ratio == 1.0


class TestTopicCoverage:
    def test_topic_with_no_kps_ratio_zero(self):
        svc, topic_ids = build_service(topic_names=["Vacio"])
        rep = KnowledgeCoverageAnalyzer(svc).analyze_topic(topic_ids["Vacio"])
        assert rep.total_knowledge_points == 0
        assert rep.coverage_ratio == 0.0

    def test_topic_missing_raises(self):
        svc, _ = build_service()
        with pytest.raises(CoverageAnalysisError) as exc:
            KnowledgeCoverageAnalyzer(svc).analyze_topic("nope")
        assert exc.value.code is CoverageErrorCode.MISSING_TOPIC

    def test_topic_coverage_counts(self):
        svc, topic_ids = build_service(
            kps=[make_kp("kp-1"), make_kp("kp-2"), make_kp("kp-3")],
            topic_names=["Algebra", "Funciones"],
            topic_kps=[("Algebra", "kp-1"), ("Algebra", "kp-2"), ("Funciones", "kp-2")],
            sessions=[1],
            session_kps=[("session-1", "kp-1")],
        )
        alg = KnowledgeCoverageAnalyzer(svc).analyze_topic(topic_ids["Algebra"])
        assert alg.total_knowledge_points == 2
        assert alg.covered_knowledge_points == 1
        assert alg.uncovered_knowledge_points == 1
        assert alg.coverage_ratio == pytest.approx(0.5)
        fn = KnowledgeCoverageAnalyzer(svc).analyze_topic(topic_ids["Funciones"])
        assert fn.total_knowledge_points == 1
        assert fn.covered_knowledge_points == 0

    def test_kp_in_two_topics_counted_once_at_course_level(self):
        svc, _ = build_service(
            kps=[make_kp("kp-1")],
            topic_names=["A", "B"],
            topic_kps=[("A", "kp-1"), ("B", "kp-1")],
        )
        rep = KnowledgeCoverageAnalyzer(svc).analyze_course()
        assert rep.total_knowledge_points == 1
        assert rep.assigned_knowledge_points == 1


class TestSessionCoverage:
    def test_session_missing_raises(self):
        svc, _ = build_service()
        with pytest.raises(CoverageAnalysisError) as exc:
            KnowledgeCoverageAnalyzer(svc).analyze_session("nope")
        assert exc.value.code is CoverageErrorCode.MISSING_SESSION

    def test_new_vs_repeated(self):
        svc, _ = build_service(
            kps=[make_kp("kp-1"), make_kp("kp-2"), make_kp("kp-3")],
            sessions=[1, 2],
            session_kps=[
                ("session-1", "kp-1"),
                ("session-1", "kp-2"),
                ("session-2", "kp-2"),
                ("session-2", "kp-3"),
            ],
        )
        an = KnowledgeCoverageAnalyzer(svc)
        r1 = an.analyze_session("session-1")
        assert r1.knowledge_point_count == 2
        assert r1.new_knowledge_point_count == 2
        assert r1.repeated_knowledge_point_count == 0
        r2 = an.analyze_session("session-2")
        assert r2.knowledge_point_count == 2
        assert r2.new_knowledge_point_count == 1
        assert r2.repeated_knowledge_point_count == 1

    def test_unnumbered_sessions_sort_last(self):
        svc, _ = build_service(kps=[make_kp("kp-1")])
        s_num = make_session(COURSE_ID, 1, session_id="s-num")
        s_un = make_session(COURSE_ID, 0, session_id="s-un")
        svc.register_session(s_num)
        svc.register_session(s_un)
        svc.add_knowledge_to_session("s-un", "kp-1")
        svc.add_knowledge_to_session("s-num", "kp-1")
        tl = KnowledgeCoverageAnalyzer(svc).analyze_coverage_timeline()
        assert [p.session_id for p in tl.sessions] == ["s-num", "s-un"]
        assert tl.sessions[0].new_knowledge_point_count == 1
        assert tl.sessions[0].repeated_knowledge_point_count == 0
        assert tl.sessions[1].new_knowledge_point_count == 0
        assert tl.sessions[1].repeated_knowledge_point_count == 1

    def test_duplicate_session_memberships_counted_once(self):
        svc, _ = build_service(kps=[make_kp("kp-1")], sessions=[1])
        svc.add_knowledge_to_session("session-1", "kp-1", order_index=1)
        svc.add_knowledge_to_session("session-1", "kp-1", order_index=2)
        r = KnowledgeCoverageAnalyzer(svc).analyze_session("session-1")
        assert r.knowledge_point_count == 1
        assert r.unique_knowledge_point_count == 1


class TestGapAnalysis:
    def _gap_by_kp(self, report):
        return {g.knowledge_point_id: g for g in report.gaps}

    def test_first_scenario_5kp_2topics_2sessions(self):
        svc, _ = build_service(
            kps=[make_kp(f"kp-{i}") for i in range(1, 6)],
            topic_names=["Algebra", "Geometría"],
            topic_kps=[
                ("Algebra", "kp-1"), ("Algebra", "kp-2"), ("Algebra", "kp-3"),
                ("Geometría", "kp-4"),
            ],
            sessions=[1, 2],
            session_kps=[
                ("session-1", "kp-1"), ("session-1", "kp-2"),
                ("session-2", "kp-3"), ("session-2", "kp-4"),
            ],
        )
        an = KnowledgeCoverageAnalyzer(svc)
        rep = an.analyze_course()
        assert rep.covered_knowledge_points == 4
        assert rep.uncovered_knowledge_points == 1
        assert rep.assigned_knowledge_points == 4
        assert rep.unassigned_knowledge_points == 1
        gaps = self._gap_by_kp(an.analyze_gaps())
        assert "kp-5" in gaps
        assert KnowledgeGapType.UNASSIGNED in gaps["kp-5"].gap_types
        assert KnowledgeGapType.UNCOVERED in gaps["kp-5"].gap_types

    def test_no_gap_for_clean_confirmed_point(self):
        svc, _ = build_service(
            kps=[make_kp("kp-1", validation_status="supported", review_status="confirmed")],
            topic_names=["Algebra"],
            topic_kps=[("Algebra", "kp-1")],
            sessions=[1],
            session_kps=[("session-1", "kp-1")],
        )
        assert KnowledgeCoverageAnalyzer(svc).analyze_gaps().gaps == ()

    def test_second_scenario_single_kp_three_sessions(self):
        svc, _ = build_service(
            kps=[make_kp("kp-1", validation_status="supported", review_status="confirmed")],
            topic_names=["Algebra"],
            topic_kps=[("Algebra", "kp-1")],
            sessions=[1, 2, 3],
            session_kps=[
                ("session-1", "kp-1"),
                ("session-2", "kp-1"),
                ("session-3", "kp-1"),
            ],
        )
        an = KnowledgeCoverageAnalyzer(svc)
        freq = an.analyze_frequency()
        assert len(freq) == 1
        assert freq[0].session_count == 3
        rep = an.analyze_course()
        assert rep.total_knowledge_points == 1
        assert rep.coverage_ratio == 1.0
        assert an.analyze_gaps().gaps == ()

    def test_third_scenario_unassigned_covered_unverified_pending(self):
        svc, _ = build_service(
            kps=[make_kp("kp-x", validation_status="unverified", review_status="pending")],
            sessions=[1],
            session_kps=[("session-1", "kp-x")],
        )
        gaps = self._gap_by_kp(KnowledgeCoverageAnalyzer(svc).analyze_gaps())
        assert "kp-x" in gaps
        assert KnowledgeGapType.UNASSIGNED in gaps["kp-x"].gap_types
        assert KnowledgeGapType.UNCOVERED not in gaps["kp-x"].gap_types
        assert KnowledgeGapType.UNVERIFIED in gaps["kp-x"].gap_types
        assert KnowledgeGapType.REVIEW_PENDING in gaps["kp-x"].gap_types
        assert KnowledgeGapType.CONFLICTED not in gaps["kp-x"].gap_types

    def test_fourth_scenario_assigned_uncovered_conflicted_pending(self):
        svc, _ = build_service(
            kps=[make_kp("kp-y", validation_status="conflicted", review_status="pending")],
            topic_names=["Algebra"],
            topic_kps=[("Algebra", "kp-y")],
        )
        gaps = self._gap_by_kp(KnowledgeCoverageAnalyzer(svc).analyze_gaps())
        assert "kp-y" in gaps
        assert KnowledgeGapType.UNCOVERED in gaps["kp-y"].gap_types
        assert KnowledgeGapType.CONFLICTED in gaps["kp-y"].gap_types
        assert KnowledgeGapType.REVIEW_PENDING in gaps["kp-y"].gap_types
        assert KnowledgeGapType.UNASSIGNED not in gaps["kp-y"].gap_types
        assert KnowledgeGapType.UNVERIFIED not in gaps["kp-y"].gap_types

    def test_gap_type_declaration_order_not_alphabetical(self):
        svc, _ = build_service(
            kps=[make_kp("kp-1", validation_status="unverified", review_status="pending")]
        )
        gaps = KnowledgeCoverageAnalyzer(svc).analyze_gaps().gaps
        assert gaps[0].gap_types == (
            KnowledgeGapType.UNASSIGNED,
            KnowledgeGapType.UNCOVERED,
            KnowledgeGapType.UNVERIFIED,
            KnowledgeGapType.REVIEW_PENDING,
        )

    def test_conflicted_knowledge_points_items(self):
        svc, _ = build_service(
            kps=[
                make_kp("kp-1", validation_status="conflicted", review_status="confirmed"),
                make_kp("kp-2", validation_status="conflicted"),
            ]
        )
        items = KnowledgeCoverageAnalyzer(svc).get_conflicted_knowledge_points()
        assert [i.knowledge_point_id for i in items] == ["kp-1", "kp-2"]
        assert items[0].review_status == "confirmed"
        assert items[0].conflict_count == 1

    def test_unverified_and_pending_lists(self):
        svc, _ = build_service(
            kps=[
                make_kp("kp-1", validation_status="unverified", review_status="pending"),
                make_kp("kp-2", validation_status="supported", review_status="confirmed"),
                make_kp("kp-3", review_status="pending"),
            ]
        )
        an = KnowledgeCoverageAnalyzer(svc)
        assert an.get_unverified_knowledge_points() == ("kp-1", "kp-3")
        assert an.get_review_pending_knowledge_points() == ("kp-1", "kp-3")

    def test_unassigned_and_uncovered_lists(self):
        svc, _ = build_service(
            kps=[make_kp("kp-1"), make_kp("kp-2")],
            topic_names=["A"],
            topic_kps=[("A", "kp-1")],
            sessions=[1],
            session_kps=[("session-1", "kp-1")],
        )
        an = KnowledgeCoverageAnalyzer(svc)
        assert an.get_unassigned_knowledge_points() == ("kp-2",)
        assert an.get_uncovered_knowledge_points() == ("kp-2",)
        orphan = an.get_orphan_report()
        assert orphan.unassigned_knowledge_point_ids == ("kp-2",)
        assert orphan.uncovered_knowledge_point_ids == ("kp-2",)

    def test_conflict_resolver_counts_unresolved_conflicts(self):
        svc, _ = build_service(
            kps=[make_kp("kp-1", validation_status="conflicted",
                         evidence_refs=["ev-1", "ev-2"])]
        )

        def resolver(kp_id, evidence_refs):
            return 2 if kp_id == "kp-1" else 0

        an = KnowledgeCoverageAnalyzer(svc, conflict_resolver=resolver)
        items = an.get_conflicted_knowledge_points()
        assert items[0].conflict_count == 2

    def test_conflict_resolver_rejects_non_callable(self):
        svc, _ = build_service()
        with pytest.raises(CoverageAnalysisError) as exc:
            KnowledgeCoverageAnalyzer(svc, conflict_resolver="x")
        assert exc.value.code is CoverageErrorCode.INVALID_INPUT


class TestFrequency:
    def test_repeated_and_single_occurrence(self):
        svc, _ = build_service(
            kps=[make_kp("kp-a"), make_kp("kp-b"), make_kp("kp-c")],
            sessions=[1, 2],
            session_kps=[
                ("session-1", "kp-a"), ("session-1", "kp-b"),
                ("session-2", "kp-a"),
            ],
        )
        an = KnowledgeCoverageAnalyzer(svc)
        repeated = an.get_repeated_knowledge_points()
        assert [f.knowledge_point_id for f in repeated] == ["kp-a"]
        single = an.get_single_occurrence_knowledge_points()
        assert [f.knowledge_point_id for f in single] == ["kp-b"]

    def test_frequency_sorted_by_session_count_desc_then_id(self):
        svc, _ = build_service(
            kps=[make_kp("kp-a"), make_kp("kp-b"), make_kp("kp-c")],
            sessions=[1, 2, 3],
            session_kps=[
                ("session-1", "kp-a"),
                ("session-2", "kp-a"), ("session-2", "kp-b"),
                ("session-3", "kp-a"),
            ],
        )
        freq = KnowledgeCoverageAnalyzer(svc).analyze_frequency()
        assert [f.knowledge_point_id for f in freq] == ["kp-a", "kp-b", "kp-c"]
        assert freq[0].session_count == 3
        assert freq[1].session_count == 1
        assert freq[2].session_count == 0

    def test_evidence_coverage_counts_unique_refs(self):
        svc, _ = build_service(
            kps=[
                make_kp("kp-1", evidence_refs=["ev-1", "ev-2", "ev-1", ""]),
                make_kp("kp-2", evidence_refs=[]),
            ]
        )
        cov = KnowledgeCoverageAnalyzer(svc).get_knowledge_evidence_coverage()
        by_kp = {c.knowledge_point_id: c.evidence_count for c in cov}
        assert by_kp["kp-1"] == 2
        assert by_kp["kp-2"] == 0


class TestValidationReviewMatrix:
    def test_matrix_total_equals_kp_count(self):
        svc, _ = build_service(
            kps=[
                make_kp("kp-1", validation_status="supported", review_status="confirmed"),
                make_kp("kp-2", validation_status="conflicted", review_status="pending"),
                make_kp("kp-3", review_status="rejected"),
                make_kp("kp-4", review_status="kept_unverified"),
            ]
        )
        m = KnowledgeCoverageAnalyzer(svc).analyze_validation_review()
        assert m.total == 4
        assert m.counts["supported"]["confirmed"] == 1
        assert m.counts["conflicted"]["pending"] == 1
        assert m.counts["unverified"]["rejected"] == 1
        assert m.counts["unverified"]["kept_unverified"] == 1

    def test_matrix_empty_course(self):
        m = KnowledgeCoverageAnalyzer(KnowledgeOrganizationService(make_course())).analyze_validation_review()
        assert m.total == 0
        assert all(v == 0 for row in m.counts.values() for v in row.values())

    def test_matrix_rejects_missing_keys(self):
        with pytest.raises(CoverageAnalysisError):
            ValidationReviewMatrix(counts={"unverified": {}})

    def test_matrix_rejects_unknown_keys_in_from_dict(self):
        data = ValidationReviewMatrix.build().to_dict()
        data["counts"]["bogus"] = {"pending": 1}
        with pytest.raises(CoverageAnalysisError) as exc:
            ValidationReviewMatrix.from_dict(data)
        assert exc.value.code is CoverageErrorCode.INVALID_STRUCTURE


class TestStatusSummary:
    def test_summary_ratios(self):
        svc, _ = build_service(
            kps=[
                make_kp("kp-1", validation_status="supported", review_status="confirmed"),
                make_kp("kp-2", validation_status="conflicted"),
                make_kp("kp-3"),
                make_kp("kp-4"),
            ],
            topic_names=["A"],
            topic_kps=[("A", "kp-1"), ("A", "kp-2")],
            sessions=[1],
            session_kps=[("session-1", "kp-1")],
        )
        s = KnowledgeCoverageAnalyzer(svc).get_status_summary()
        assert s.total_knowledge_points == 4
        assert s.coverage_ratio == pytest.approx(0.25)
        assert s.assignment_ratio == pytest.approx(0.5)
        assert s.conflicted_ratio == pytest.approx(0.25)
        assert s.unverified_ratio == pytest.approx(0.5)
        assert s.review_pending_ratio == pytest.approx(0.75)

    def test_summary_empty(self):
        s = KnowledgeCoverageAnalyzer(KnowledgeOrganizationService(make_course())).get_status_summary()
        assert s.total_knowledge_points == 0
        assert s.coverage_ratio == 0.0


class TestDeterminism:
    def test_insertion_order_independent_serialization(self):
        def build(order):
            # Identical final structure; only the *insertion order* of KP
            # registrations and memberships varies.  Reports must be
            # insertion-order independent.
            topic_kps = []
            session_kps = []
            if order == "fwd":
                kps = [make_kp(f"kp-{i}") for i in range(4)]
                topic_kps = [("Algebra", "kp-0"), ("Algebra", "kp-1"),
                              ("Geometría", "kp-2"), ("Geometría", "kp-3")]
                session_kps = [("session-1", "kp-0"), ("session-2", "kp-3")]
            else:
                kps = [make_kp(f"kp-{i}") for i in (3, 2, 1, 0)]
                topic_kps = [("Geometría", "kp-3"), ("Geometría", "kp-2"),
                              ("Algebra", "kp-1"), ("Algebra", "kp-0")]
                session_kps = [("session-2", "kp-3"), ("session-1", "kp-0")]
            svc, _ = build_service(
                kps=kps,
                topic_names=["Algebra", "Geometría"],
                topic_kps=topic_kps,
                sessions=[1, 2],
                session_kps=session_kps,
            )
            an = KnowledgeCoverageAnalyzer(svc)
            return {
                "course": an.analyze_course().to_dict(),
                "gaps": an.analyze_gaps().to_dict(),
                "freq": [f.to_dict() for f in an.analyze_frequency()],
                "timeline": an.analyze_coverage_timeline().to_dict(),
                "matrix": an.analyze_validation_review().to_dict(),
                "orphans": an.get_orphan_report().to_dict(),
                "summary": an.get_status_summary().to_dict(),
            }

        a = build("fwd")
        b = build("rev")
        assert a["course"] == b["course"]
        assert a["gaps"] == b["gaps"]
        assert a["freq"] == b["freq"]
        assert a["timeline"] == b["timeline"]
        assert a["matrix"] == b["matrix"]
        assert a["orphans"] == b["orphans"]
        assert a["summary"] == b["summary"]

    def test_idempotent_reanalysis(self):
        svc, _ = build_service(
            kps=[make_kp("kp-1")],
            topic_names=["A"],
            topic_kps=[("A", "kp-1")],
            sessions=[1],
            session_kps=[("session-1", "kp-1")],
        )
        an = KnowledgeCoverageAnalyzer(svc)
        r1 = an.analyze_course()
        r2 = an.analyze_course()
        assert r1 == r2
        assert r1.to_dict() == r2.to_dict()

    def test_snapshot_isolation_from_later_mutation(self):
        svc, _ = build_service(kps=[make_kp("kp-1")], sessions=[1],
                               session_kps=[("session-1", "kp-1")])
        an = KnowledgeCoverageAnalyzer(svc)
        before = an.analyze_course()
        assert before.covered_knowledge_points == 1
        svc.register_knowledge_point(make_kp("kp-2"))
        svc.add_knowledge_to_session("session-1", "kp-2")
        after = an.analyze_course()
        assert after == before
        an2 = KnowledgeCoverageAnalyzer(svc)
        assert an2.analyze_course().total_knowledge_points == 2

    def test_gap_reports_immutable(self):
        svc, _ = build_service(kps=[make_kp("kp-1")])
        gap = KnowledgeCoverageAnalyzer(svc).analyze_gaps().gaps[0]
        with pytest.raises(Exception):
            gap.knowledge_point_id = "other"

    def test_gap_serialization_roundtrip_preserves_order(self):
        gap = KnowledgeGap(
            knowledge_point_id="kp-1",
            gap_types=(
                KnowledgeGapType.UNCOVERED,
                KnowledgeGapType.UNASSIGNED,
                KnowledgeGapType.REVIEW_PENDING,
            ),
        )
        restored = KnowledgeGap.from_dict(gap.to_dict())
        assert restored.gap_types == (
            KnowledgeGapType.UNASSIGNED,
            KnowledgeGapType.UNCOVERED,
            KnowledgeGapType.REVIEW_PENDING,
        )


class TestIndependence:
    def _svc(self):
        return build_service(
            kps=[make_kp("kp-1"), make_kp("kp-2")],
            sessions=[1],
            session_kps=[("session-1", "kp-1"), ("session-1", "kp-2")],
        )[0]

    def test_relations_do_not_change_coverage(self):
        svc = self._svc()
        before = KnowledgeCoverageAnalyzer(svc).analyze_course()
        svc.add_relation("kp-1", "kp-2", "prerequisite")
        svc.add_relation("kp-1", "kp-2", "extends")
        after = KnowledgeCoverageAnalyzer(svc).analyze_course()
        assert before == after

    def test_evidence_refs_do_not_change_coverage(self):
        svc = self._svc()
        before = KnowledgeCoverageAnalyzer(svc).analyze_course()
        svc.register_knowledge_point(make_kp("kp-1", evidence_refs=["ev-a", "ev-b"]))
        after = KnowledgeCoverageAnalyzer(svc).analyze_course()
        assert before.covered_knowledge_points == after.covered_knowledge_points
        assert before.coverage_ratio == after.coverage_ratio

    def test_validation_status_does_not_change_coverage(self):
        svc = self._svc()
        assert KnowledgeCoverageAnalyzer(svc).analyze_course().covered_knowledge_points == 2
        svc.register_knowledge_point(make_kp("kp-1", validation_status="conflicted"))
        assert KnowledgeCoverageAnalyzer(svc).analyze_course().covered_knowledge_points == 2


class TestCourseIsolation:
    def test_reports_never_cross_courses(self):
        course_a = make_course(COURSE_ID)
        course_b = make_course(COURSE_B, name="Física")
        svc_a = KnowledgeOrganizationService(course_a)
        svc_a.register_knowledge_point(make_kp("kp-a"))
        svc_a.register_session(make_session(COURSE_ID, 1))
        svc_a.add_knowledge_to_session("session-1", "kp-a")

        svc_b = KnowledgeOrganizationService(course_b)
        rep_b = KnowledgeCoverageAnalyzer(svc_b).analyze_course()
        assert rep_b.course_id == COURSE_B
        assert rep_b.total_knowledge_points == 0
        assert rep_b.coverage_ratio == 0.0
        assert KnowledgeCoverageAnalyzer(svc_b).analyze_gaps().gaps == ()

    def test_session_course_mismatch_rejected_at_org_layer(self):
        from src.knowledge_organization import KnowledgeOrganizationError
        svc = KnowledgeOrganizationService(make_course(COURSE_ID))
        foreign = make_session(COURSE_B, 1)
        with pytest.raises(KnowledgeOrganizationError):
            svc.register_session(foreign)


class TestMultilingual:
    def test_unicode_ids_and_names_survive_roundtrip(self):
        svc, topic_ids = build_service(
            kps=[
                KnowledgePoint(
                    knowledge_id="límit-del-épsilon-delta",
                    title="Límite ε-δ",
                    content="Definició / Definición",
                    review_status="confirmed",
                    validation_status="supported",
                ),
            ],
            topic_names=["Funcions catalanes"],
        )
        svc.add_knowledge_to_topic(topic_ids["Funcions catalanes"], "límit-del-épsilon-delta")
        an = KnowledgeCoverageAnalyzer(svc)
        rep = an.analyze_course()
        assert KnowledgeCoverageReport.from_dict(rep.to_dict()) == rep
        tc = an.analyze_topic(topic_ids["Funcions catalanes"])
        assert TopicCoverageReport.from_dict(tc.to_dict()) == tc
        gap_ids = [g.knowledge_point_id for g in an.analyze_gaps().gaps]
        assert "límit-del-épsilon-delta" in gap_ids  # uncovered (no session)

    def test_unicode_topic_coverage_report_roundtrip(self):
        svc, topic_ids = build_service(
            kps=[make_kp("kp-1")],
            topic_names=["Càlcul diferencial"],
            topic_kps=[("Càlcul diferencial", "kp-1")],
        )
        tc = KnowledgeCoverageAnalyzer(svc).analyze_topic(topic_ids["Càlcul diferencial"])
        restored = TopicCoverageReport.from_dict(tc.to_dict())
        assert restored == tc
        assert restored.topic_id == topic_ids["Càlcul diferencial"]


class TestSerialization:
    def _svc(self):
        return build_service(
            kps=[
                make_kp("kp-1", validation_status="conflicted", review_status="pending"),
                make_kp("kp-2", validation_status="supported", review_status="confirmed"),
            ],
            topic_names=["Algebra"],
            topic_kps=[("Algebra", "kp-1")],
            sessions=[1],
            session_kps=[("session-1", "kp-1"), ("session-1", "kp-2")],
        )[0]

    def test_roundtrip_all_report_types(self):
        svc = self._svc()
        an = KnowledgeCoverageAnalyzer(svc)
        cases = [
            (an.analyze_course(), KnowledgeCoverageReport),
            (an.analyze_topic(svc.list_topics()[0].topic_id), TopicCoverageReport),
            (an.analyze_session("session-1"), SessionCoverageReport),
            (an.analyze_coverage_timeline(), CourseCoverageTimeline),
            (an.analyze_gaps(), CourseGapReport),
            (an.get_orphan_report(), KnowledgeOrphanReport),
            (an.analyze_validation_review(), ValidationReviewMatrix),
            (an.get_status_summary(), CourseKnowledgeStatusSummary),
        ]
        for obj, cls in cases:
            restored = cls.from_dict(obj.to_dict())
            assert restored == obj, f"round-trip failed for {cls.__name__}"
        for freq in an.analyze_frequency():
            assert KnowledgeFrequency.from_dict(freq.to_dict()) == freq
        for item in an.get_conflicted_knowledge_points():
            assert ConflictCoverageItem.from_dict(item.to_dict()) == item
        for ev in an.get_knowledge_evidence_coverage():
            assert KnowledgeEvidenceCoverage.from_dict(ev.to_dict()) == ev
        for gap in an.analyze_gaps().gaps:
            assert KnowledgeGap.from_dict(gap.to_dict()) == gap

    def test_from_dict_rejects_unknown_schema_version(self):
        svc = self._svc()
        data = KnowledgeCoverageAnalyzer(svc).analyze_course().to_dict()
        data["schema_version"] = 99
        with pytest.raises(CoverageAnalysisError) as exc:
            KnowledgeCoverageReport.from_dict(data)
        assert exc.value.code is CoverageErrorCode.INVALID_SCHEMA_VERSION
        assert isinstance(exc.value, CoverageSchemaError)

    def test_from_dict_rejects_non_mapping(self):
        with pytest.raises(CoverageAnalysisError) as exc:
            KnowledgeCoverageReport.from_dict(["not", "a", "mapping"])
        assert exc.value.code is CoverageErrorCode.INVALID_STRUCTURE


class TestPerformance:
    def test_synthetic_1000kp_dataset(self):
        svc = KnowledgeOrganizationService(make_course())
        kp_ids = [f"kp-{i}" for i in range(1000)]
        for kp in kp_ids:
            svc.register_knowledge_point(make_kp(kp))
        topic_ids = [svc.add_topic(f"Topic-{i}", order_index=i).topic_id for i in range(100)]
        session_ids = []
        for i in range(1, 51):
            s = make_session(COURSE_ID, i, session_id=f"s-{i:02d}")
            svc.register_session(s)
            session_ids.append(s.session_id)
        start = time.perf_counter()
        for j in range(3000):
            svc.add_knowledge_to_topic(topic_ids[j % 100], kp_ids[j % 1000], order_index=j % 10)
        for j in range(5000):
            svc.add_knowledge_to_session(session_ids[j % 50], kp_ids[(j + 7) % 1000])
        for j in range(2000):
            svc.add_relation(
                kp_ids[j % 1000], kp_ids[(j + 1) % 1000],
                "prerequisite" if j % 5 == 0 else "related",
            )
        add_elapsed = time.perf_counter() - start

        start = time.perf_counter()
        an = KnowledgeCoverageAnalyzer(svc)
        course = an.analyze_course()
        gaps = an.analyze_gaps()
        freq = an.analyze_frequency()
        tl = an.analyze_coverage_timeline()
        matrix = an.analyze_validation_review()
        _ = an.get_status_summary()
        for tid in topic_ids:
            an.analyze_topic(tid)
        for sid in session_ids:
            an.analyze_session(sid)
        query_elapsed = time.perf_counter() - start

        assert add_elapsed < 30.0, f"adds took {add_elapsed:.1f}s"
        assert query_elapsed < 30.0, f"queries took {query_elapsed:.1f}s"
        assert course.total_knowledge_points == 1000
        assert course.covered_knowledge_points + course.uncovered_knowledge_points == 1000
        assert matrix.total == 1000
        assert len(freq) == 1000
        assert len(tl.sessions) == 50
        assert len(gaps.gaps) > 0
