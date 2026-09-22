"""Tests for the deterministic study planning layer (Task 33)."""

from __future__ import annotations

import pytest

from src.study_plan import (
    RULES_VERSION,
    STUDY_PLAN_SCHEMA_VERSION,
    LearningPath,
    PathStatus,
    StudyItem,
    StudyPlanner,
    StudyPlan,
    StudyPlanErrorCode,
    StudyPlanValidationError,
    StudyReason,
)


def _planner(**kw):
    defaults = dict(
        course_id="math-101",
        course_kps={"kp-a": None, "kp-b": None, "kp-c": None},
        prerequisites={
            "kp-b": ("kp-a",),
            "kp-c": ("kp-b",),
        },
    )
    defaults.update(kw)
    return StudyPlanner(**defaults)


class TestPrerequisitePath:
    def test_root_to_target(self):
        planner = _planner()
        path = planner.build_learning_path("kp-c")
        assert path.status is PathStatus.OK
        assert path.node_ids == ("kp-a", "kp-b", "kp-c")

    def test_target_is_root(self):
        planner = _planner()
        path = planner.build_learning_path("kp-a")
        assert path.node_ids == ("kp-a",)

    def test_multi_hop(self):
        planner = _planner(
            course_kps={"a": None, "b": None, "c": None, "d": None},
            prerequisites={"b": ("a",), "c": ("b",), "d": ("c",)},
        )
        path = planner.build_learning_path("d")
        assert path.node_ids == ("a", "b", "c", "d")

    def test_diamond_includes_once(self):
        planner = _planner(
            course_kps={"a": None, "b": None, "c": None, "d": None},
            prerequisites={"b": ("a",), "c": ("a",), "d": ("b", "c")},
        )
        path = planner.build_learning_path("d")
        assert path.node_ids == ("a", "b", "c", "d")

    def test_cycle_detected(self):
        planner = _planner(
            course_kps={"a": None, "b": None},
            prerequisites={"a": ("b",), "b": ("a",)},
        )
        path = planner.build_learning_path("a")
        assert path.status is PathStatus.DEPENDENCY_CYCLE
        assert path.cycle_node_ids == ("a", "b")
        assert path.node_ids == ()

    def test_unknown_target(self):
        planner = _planner()
        path = planner.build_learning_path("kp-x")
        assert path.status is PathStatus.UNKNOWN_KNOWLEDGE_POINT


class TestUncoveredPrerequisite:
    def test_uncovered_prereq_triggers_reason(self):
        planner = _planner(uncovered_kps=("kp-a",))
        plan = planner.build_plan("stu-1")
        item = next(i for i in plan.items if i.knowledge_point_id == "kp-b")
        assert StudyReason.UNCOVERED_PREREQUISITE in item.reason_codes

    def test_no_weak_claim(self):
        planner = _planner(uncovered_kps=("kp-a",))
        plan = planner.build_plan("stu-1")
        for item in plan.items:
            assert StudyReason.RECENT_INCORRECT not in item.reason_codes


class TestRecentIncorrect:
    def test_recent_incorrect_marks_item(self):
        planner = _planner(recent_incorrect_kps=("kp-b",))
        plan = planner.build_plan("stu-1")
        item = next(i for i in plan.items if i.knowledge_point_id == "kp-b")
        assert StudyReason.RECENT_INCORRECT in item.reason_codes


class TestLowPractice:
    def test_low_practice_triggers(self):
        planner = _planner(practice_counts={"kp-a": 0}, minimum_practice_count=2)
        plan = planner.build_plan("stu-1")
        item = next(i for i in plan.items if i.knowledge_point_id == "kp-a")
        assert StudyReason.LOW_PRACTICE_COUNT in item.reason_codes

    def test_sufficient_practice_no_low_count_reason(self):
        planner = _planner(
            practice_counts={"kp-a": 3, "kp-b": 3, "kp-c": 3},
            minimum_practice_count=2,
        )
        plan = planner.build_plan("stu-1")
        for item in plan.items:
            assert StudyReason.LOW_PRACTICE_COUNT not in item.reason_codes


class TestReviewPending:
    def test_pending_review_triggers(self):
        planner = _planner(review_status={"kp-a": "PENDING"})
        plan = planner.build_plan("stu-1")
        item = next(i for i in plan.items if i.knowledge_point_id == "kp-a")
        assert StudyReason.REVIEW_PENDING in item.reason_codes

    def test_conflicted_content_review_signal_not_weakness(self):
        planner = _planner(validation_status={"kp-b": "CONFLICTED"})
        plan = planner.build_plan("stu-1")
        item = next(i for i in plan.items if i.knowledge_point_id == "kp-b")
        assert StudyReason.CONFLICTED_KNOWLEDGE in item.reason_codes
        assert StudyReason.REVIEW_PENDING in item.reason_codes


class TestUnverified:
    def test_unverified_triggers(self):
        planner = _planner(validation_status={"kp-a": "UNVERIFIED"})
        plan = planner.build_plan("stu-1")
        item = next(i for i in plan.items if i.knowledge_point_id == "kp-a")
        assert StudyReason.UNVERIFIED_KNOWLEDGE in item.reason_codes


class TestCombinedReasons:
    def test_multiple_reasons_on_one_item(self):
        planner = _planner(
            uncovered_kps=("kp-a",),
            recent_incorrect_kps=("kp-c",),
            validation_status={"kp-c": "UNVERIFIED"},
            practice_counts={"kp-c": 0},
            minimum_practice_count=2,
        )
        plan = planner.build_plan("stu-1")
        item_c = next(i for i in plan.items if i.knowledge_point_id == "kp-c")
        assert StudyReason.RECENT_INCORRECT in item_c.reason_codes
        assert StudyReason.LOW_PRACTICE_COUNT in item_c.reason_codes
        assert StudyReason.UNVERIFIED_KNOWLEDGE in item_c.reason_codes
        item_b = next(i for i in plan.items if i.knowledge_point_id == "kp-b")
        assert StudyReason.UNCOVERED_PREREQUISITE in item_b.reason_codes


class TestDeterministicOrdering:
    def test_same_state_same_plan(self):
        p1 = _planner(recent_incorrect_kps=("kp-b",)).build_plan("stu-1")
        p2 = _planner(recent_incorrect_kps=("kp-b",)).build_plan("stu-1")
        assert p1.plan_id == p2.plan_id
        assert p1.items == p2.items

    def test_changed_state_changed_plan(self):
        p1 = _planner().build_plan("stu-1")
        p2 = _planner(recent_incorrect_kps=("kp-b",)).build_plan("stu-1")
        assert p1.plan_id != p2.plan_id

    def test_reason_priority_ordering(self):
        planner = _planner(
            uncovered_kps=("kp-a",),
            recent_incorrect_kps=("kp-b",),
        )
        plan = planner.build_plan("stu-1")
        first = plan.items[0]
        assert StudyReason.UNCOVERED_PREREQUISITE in first.reason_codes


class TestStudentIsolation:
    def test_student_b_unaffected_by_a(self):
        planner = _planner(recent_incorrect_kps=("kp-b",))
        plan_a = planner.build_plan("stu-a")
        plan_b = planner.build_plan("stu-b")
        assert plan_a.plan_id != plan_b.plan_id
        assert plan_a.student_id == "stu-a"
        assert plan_b.student_id == "stu-b"


class TestImmutableSnapshot:
    def test_plan_is_frozen(self):
        plan = _planner().build_plan("stu-1")
        with pytest.raises(AttributeError):
            plan.items = ()

    def test_items_are_frozen(self):
        plan = _planner().build_plan("stu-1")
        for item in plan.items:
            with pytest.raises(AttributeError):
                item.reason_codes = ()


class TestExplainability:
    def test_every_item_has_reasons(self):
        planner = _planner(uncovered_kps=("kp-a",), recent_incorrect_kps=("kp-b",))
        plan = planner.build_plan("stu-1")
        for item in plan.items:
            assert len(item.reason_codes) >= 1


class TestSerialization:
    def test_plan_round_trip(self):
        planner = _planner(recent_incorrect_kps=("kp-b",))
        plan = planner.build_plan("stu-1")
        rebuilt = StudyPlan.from_dict(plan.to_dict())
        assert rebuilt == plan

    def test_path_round_trip(self):
        planner = _planner()
        path = planner.build_learning_path("kp-c")
        rebuilt = LearningPath.from_dict(path.to_dict())
        assert rebuilt == path

    def test_plan_schema_version(self):
        plan = _planner().build_plan("stu-1")
        assert plan.to_dict()["schema_version"] == STUDY_PLAN_SCHEMA_VERSION
        assert plan.rules_version == RULES_VERSION

    def test_tampered_plan_rejected(self):
        plan = _planner().build_plan("stu-1")
        d = plan.to_dict()
        d["student_id"] = "someone-else"
        with pytest.raises(StudyPlanValidationError) as exc:
            StudyPlan.from_dict(d)
        assert exc.value.code is StudyPlanErrorCode.INVALID_STATE


class TestPerformance:
    def test_1000_kps_under_5s(self):
        import time
        kps = {f"kp-{i:04d}" for i in range(1000)}
        prereqs = {f"kp-{i:04d}": (f"kp-{i-1:04d}",) for i in range(1, 1000)}
        planner = _planner(
            course_kps={k: None for k in kps},
            prerequisites=prereqs,
            uncovered_kps=("kp-0000",),
            recent_incorrect_kps=("kp-0500",),
            practice_counts={k: 0 for k in kps},
            minimum_practice_count=2,
        )
        t0 = time.perf_counter()
        plan = planner.build_plan("stu-1")
        t1 = time.perf_counter()
        assert len(plan.items) == 1000
        assert t1 - t0 < 5.0

    def test_1000_kp_path(self):
        kps = {f"kp-{i:04d}" for i in range(1000)}
        prereqs = {f"kp-{i:04d}": (f"kp-{i-1:04d}",) for i in range(1, 1000)}
        planner = _planner(
            course_kps={k: None for k in kps},
            prerequisites=prereqs,
        )
        path = planner.build_learning_path("kp-0999")
        assert path.status is PathStatus.OK
        assert len(path.node_ids) == 1000
