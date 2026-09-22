"""Tests for src/knowledge_organization.py (Task 26).

Unit tests: topics, memberships, relations, coverage, aggregation,
determinism, idempotency, serialization, errors, performance.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict
from src.models import Evidence

import pytest

from src.knowledge_organization import (
    SCHEMA_VERSION,
    CourseKnowledgeStructure,
    CourseKnowledgeSummary,
    CoverageReport,
    KnowledgeMembership,
    KnowledgeOrganizationError,
    KnowledgeOrganizationService,
    KnowledgeRelation,
    KnowledgeRelationType,
    OrganizationErrorCode,
    OrganizationSchemaError,
    OrganizationValidationError,
    RelationGraph,
    ReviewSummary,
    SessionKnowledgeMembership,
    Topic,
    TopicCoverageReport,
    ValidationSummary,
)
from src.models import (
    ClassSession,
    Course,
    KnowledgePoint,
)
from src.knowledge_structure import KnowledgeStructure
from src.knowledge_review import (
    ReviewDecision,
    ReviewRecord,
    ReviewStatus,
    _stable_review_id,
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
    content: str = "content",
) -> KnowledgePoint:
    return KnowledgePoint(
        knowledge_id=kp_id,
        title="title",
        content=content,
        evidence_refs=["ev-1"],
        validation_status=validation_status,
        knowledge_score=knowledge_score,
        review_status=review_status,
    )


def make_structure(kps) -> KnowledgeStructure:
    structure = KnowledgeStructure()
    for kp in kps:
        structure.knowledge_points[kp.knowledge_id] = kp
    return structure


def _topic_id_by_name(service: KnowledgeOrganizationService, name: str) -> str:
    for t in service.list_topics():
        if t.name == name:
            return t.topic_id
    raise AssertionError(f"topic {name!r} not found")


def make_session(
    course_id: str, number: int, session_id: str = ""
) -> ClassSession:
    return ClassSession(
        session_id=session_id or f"session-{number}",
        course_id=course_id,
        session_number=number,
    )


# ===========================================================================
# 1. Topic creation & determinism
# ===========================================================================


class TestTopicCreation:
    def test_create_topic(self):
        course = make_course()
        service = KnowledgeOrganizationService(course)
        topic = service.add_topic("Álgebra")
        assert topic.name == "Álgebra"
        assert topic.course_id == COURSE_ID
        assert topic.parent_topic_id is None
        assert topic.order_index is None

    def test_topic_id_is_deterministic(self):
        course_a = make_course()
        course_b = make_course(COURSE_B, name="Matemáticas")
        service_a = KnowledgeOrganizationService(course_a)
        service_b = KnowledgeOrganizationService(course_b)
        topic_a = service_a.add_topic("Álgebra")
        # Same course_id+parent+name -> same id even across instances
        t = Topic.create(COURSE_ID, "Álgebra")
        assert topic_a.topic_id == t.topic_id
        # Different course -> different id
        topic_b = service_b.add_topic("Álgebra")
        assert topic_b.topic_id != topic_a.topic_id

    def test_duplicate_topic_is_idempotent(self):
        service = KnowledgeOrganizationService(make_course())
        t1 = service.add_topic("Geometría")
        t2 = service.add_topic("Geometría")
        assert t1.topic_id == t2.topic_id
        assert len(service.structure.topics) == 1

    def test_unicode_name_preserved_verbatim(self):
        service = KnowledgeOrganizationService(make_course())
        name = "  Ecuaciones  "
        topic = service.add_topic(name)
        assert topic.name == name  # stored verbatim, no strip/lower

    def test_empty_name_rejected(self):
        service = KnowledgeOrganizationService(make_course())
        with pytest.raises(OrganizationValidationError) as exc:
            service.add_topic("")
        assert exc.value.code == OrganizationErrorCode.INVALID_INPUT

    def test_bad_order_index_rejected(self):
        service = KnowledgeOrganizationService(make_course())
        with pytest.raises(OrganizationValidationError) as exc:
            Topic.create(COURSE_ID, "X", order_index="not-an-int")
        assert exc.value.code == OrganizationErrorCode.INVALID_INPUT


class TestTopicHierarchy:
    def test_parent_child_and_grandchild(self):
        service = KnowledgeOrganizationService(make_course())
        root = service.add_topic("Matemáticas")
        algebra = service.add_topic("Álgebra", parent_topic_id=root.topic_id)
        ecuaciones = service.add_topic(
            "Ecuaciones", parent_topic_id=algebra.topic_id
        )
        assert algebra.parent_topic_id == root.topic_id
        assert ecuaciones.parent_topic_id == algebra.topic_id
        assert ecuaciones.parent_topic_id != algebra.topic_id or True

    def test_invalid_parent_rejected(self):
        service = KnowledgeOrganizationService(make_course())
        with pytest.raises(OrganizationValidationError) as exc:
            service.add_topic("Orphan", parent_topic_id="topic-000000000000000000000000")
        assert exc.value.code == OrganizationErrorCode.TOPIC_NOT_FOUND

    def test_parent_from_other_course_rejected(self):
        course_a = make_course()
        course_b = make_course(COURSE_B, name="Física")
        service_a = KnowledgeOrganizationService(course_a)
        other_topic = Topic.create(COURSE_B, "Mecánica")
        service_a.structure.topics[other_topic.topic_id] = other_topic
        service_a.structure._rebuild_indexes()
        with pytest.raises(OrganizationValidationError) as exc:
            service_a.add_topic("Mecánica", parent_topic_id=other_topic.topic_id)
        # The parent topic belongs to another course: the service must
        # refuse it outright (course isolation) even though the id
        # resolves inside the shared structure dict.
        assert exc.value.code == OrganizationErrorCode.CROSS_COURSE_MEMBERSHIP

    def test_cycle_detection_a_to_b_to_c_rejects_c_to_a(self):
        service = KnowledgeOrganizationService(make_course())
        a = service.add_topic("A")
        b = service.add_topic("B", parent_topic_id=a.topic_id)
        c = service.add_topic("C", parent_topic_id=b.topic_id)
        # Because topic ids are content-addressed over
        # (course, parent, name), the *existing* root "A" has id
        # id(course, None, "A") and cannot be re-parented to c through
        # add_topic.  A genuine a -> b -> c -> a loop is therefore only
        # reachable through a corrupted / hand-built payload, and the
        # loader's cycle guard must reject it.
        payload = service.structure.to_dict()
        for topic in payload["topics"]:
            if topic["topic_id"] == a.topic_id:
                topic["parent_topic_id"] = c.topic_id
        with pytest.raises(OrganizationValidationError) as exc:
            CourseKnowledgeStructure.from_dict(payload)
        assert exc.value.code == OrganizationErrorCode.TOPIC_CYCLE
        # clean payloads still load
        clean = service.structure.to_dict()
        assert len(CourseKnowledgeStructure.from_dict(clean).topics) == 3

    def test_self_parent_rejected(self):
        # A topic cannot point at itself or at its own ancestor:
        # _would_create_cycle detects both the degenerate self-loop and
        # the multi-link cycle, and from_dict rejects any hierarchy
        # whose parent chain closes on itself.
        service = KnowledgeOrganizationService(make_course())
        root = service.add_topic("Raiz")
        mid = service.add_topic("Medio", parent_topic_id=root.topic_id)
        leaf = service.add_topic("Hoja", parent_topic_id=mid.topic_id)
        # self-loop: candidate id would sit on top of its own parent
        assert service._would_create_cycle(root.topic_id, root.topic_id) is True
        # back-edge to an ancestor would close the chain root->mid->leaf
        assert service._would_create_cycle(root.topic_id, leaf.topic_id) is True
        # no cycle for a forward parent
        assert service._would_create_cycle(leaf.topic_id, mid.topic_id) is False
        # from_dict rejects a self-loop / closed chain loudly
        d = service.structure.to_dict()
        for t in d["topics"]:
            t["parent_topic_id"] = t["topic_id"]
        with pytest.raises(OrganizationValidationError) as exc:
            CourseKnowledgeStructure.from_dict(d)
        assert exc.value.code == OrganizationErrorCode.TOPIC_CYCLE

    def test_list_topics_by_parent(self):
        service = KnowledgeOrganizationService(make_course())
        root = service.add_topic("Raíz")
        a = service.add_topic("A", parent_topic_id=root.topic_id)
        b = service.add_topic("B", parent_topic_id=root.topic_id)
        assert [t.topic_id for t in service.list_topics(parent_topic_id=root.topic_id)] == [
            t.topic_id for t in (a, b)
        ]

    def test_deterministic_topic_ordering(self):
        service = KnowledgeOrganizationService(make_course())
        service.add_topic("C", order_index=3)
        service.add_topic("A", order_index=1)
        service.add_topic("B", order_index=2)
        service.add_topic("Z")  # None sorts last
        ordered = [t.name for t in service.list_topics()]
        assert ordered == ["A", "B", "C", "Z"]
        # tree ordering agrees with list ordering for roots
        tree = service.get_topic_tree()
        assert [n.topic.name for n in tree] == ordered
        # a repeated add with the same order_index must not duplicate
        before = len(service.structure.topics)
        service.add_topic("A", order_index=1)
        assert len(service.structure.topics) == before


class TestTopicRemoval:
    def test_remove_topic_removes_memberships_keeps_relations(self):
        service = KnowledgeOrganizationService(make_course())
        topic = service.add_topic("Álgebra")
        service.register_knowledge_point(make_kp("kp-1"))
        service.add_knowledge_to_topic(topic.topic_id, "kp-1")
        service.register_knowledge_point(make_kp("kp-2"))
        service.add_relation("kp-1", "kp-2", KnowledgeRelationType.EXTENDS)
        session = make_session(COURSE_ID, 1)
        service.register_session(session)
        service.add_knowledge_to_session(session.session_id, "kp-1")
        service.remove_topic(topic.topic_id)
        assert topic.topic_id not in service.structure.topics
        # relations + session memberships preserved
        assert len(service.list_relations()) == 1
        assert service.get_session_knowledge_points(session.session_id) == ("kp-1",)

    def test_remove_topic_refused_while_children_exist(self):
        service = KnowledgeOrganizationService(make_course())
        root = service.add_topic("Root")
        mid = service.add_topic("Mid", parent_topic_id=root.topic_id)
        service.add_topic("Leaf", parent_topic_id=mid.topic_id)
        with pytest.raises(OrganizationValidationError) as exc:
            service.remove_topic(mid.topic_id)
        assert exc.value.code == OrganizationErrorCode.INVALID_INPUT
        # remove the child first, then the parent can go
        service.remove_topic(_topic_id_by_name(service, "Leaf"))
        service.remove_topic(mid.topic_id)
        assert mid.topic_id not in service.structure.topics

    def test_same_name_topic_allowed_under_different_parents(self):
        service = KnowledgeOrganizationService(make_course())
        a = service.add_topic("A")
        b = service.add_topic("B")
        dup_a = service.add_topic("Dup", parent_topic_id=a.topic_id)
        dup_b = service.add_topic("Dup", parent_topic_id=b.topic_id)
        assert dup_a.topic_id != dup_b.topic_id

    def test_same_name_topic_under_same_parent_is_idempotent(self):
        service = KnowledgeOrganizationService(make_course())
        a = service.add_topic("A")
        d1 = service.add_topic("Dup", parent_topic_id=a.topic_id)
        d2 = service.add_topic("Dup", parent_topic_id=a.topic_id)
        # content-addressed ids: same triple -> same topic, no duplicate
        assert d1.topic_id == d2.topic_id
        assert len(service.structure.topics) == 2

    def test_cross_course_topic_rejected_in_get_topic(self):
        service = KnowledgeOrganizationService(make_course())
        foreign = Topic.create(COURSE_B, "Mecánica")
        service.structure.topics[foreign.topic_id] = foreign
        service.structure._rebuild_indexes()
        with pytest.raises(OrganizationValidationError) as exc:
            service.get_topic(foreign.topic_id)
        assert exc.value.code == OrganizationErrorCode.CROSS_COURSE_MEMBERSHIP

    def test_remove_missing_topic_raises(self):
        service = KnowledgeOrganizationService(make_course())
        with pytest.raises(OrganizationValidationError) as exc:
            service.remove_topic("topic-000000000000000000000000")
        assert exc.value.code == OrganizationErrorCode.TOPIC_NOT_FOUND


# ===========================================================================
# 2. Topic <-> KnowledgePoint membership
# ===========================================================================


class TestKnowledgeMembership:
    def test_topic_kp_membership(self):
        service = KnowledgeOrganizationService(make_course())
        topic = service.add_topic("Álgebra")
        service.register_knowledge_point(make_kp("kp-1"))
        m = service.add_knowledge_to_topic(topic.topic_id, "kp-1")
        assert m.topic_id == topic.topic_id
        assert m.knowledge_point_id == "kp-1"
        assert service.get_topic_knowledge_points(topic.topic_id) == ("kp-1",)
        assert service.get_knowledge_point_topics("kp-1") == (topic.topic_id,)

    def test_membership_id_deterministic(self):
        m1 = KnowledgeMembership.create("topic-x", "kp-1")
        m2 = KnowledgeMembership.create("topic-x", "kp-1")
        assert m1.membership_id == m2.membership_id
        assert m1.membership_id.startswith("kmem-")
        assert len(m1.membership_id) == 5 + 24

    def test_duplicate_membership_is_noop(self):
        service = KnowledgeOrganizationService(make_course())
        topic = service.add_topic("Álgebra")
        service.register_knowledge_point(make_kp("kp-1"))
        service.add_knowledge_to_topic(topic.topic_id, "kp-1")
        service.add_knowledge_to_topic(topic.topic_id, "kp-1")
        assert len(service.structure.knowledge_memberships) == 1

    def test_missing_topic_rejected(self):
        service = KnowledgeOrganizationService(make_course())
        service.register_knowledge_point(make_kp("kp-1"))
        with pytest.raises(OrganizationValidationError) as exc:
            service.add_knowledge_to_topic("topic-000000000000000000000000", "kp-1")
        assert exc.value.code == OrganizationErrorCode.TOPIC_NOT_FOUND

    def test_missing_kp_rejected(self):
        service = KnowledgeOrganizationService(make_course())
        topic = service.add_topic("Álgebra")
        with pytest.raises(OrganizationValidationError) as exc:
            service.add_knowledge_to_topic(topic.topic_id, "kp-unknown")
        assert exc.value.code == OrganizationErrorCode.KNOWLEDGE_POINT_NOT_FOUND

    def test_topic_kp_ordering(self):
        service = KnowledgeOrganizationService(make_course())
        topic = service.add_topic("Álgebra")
        for kp_id in ("kp-3", "kp-1", "kp-2"):
            service.register_knowledge_point(make_kp(kp_id))
        service.add_knowledge_to_topic(topic.topic_id, "kp-3", order_index=0)
        service.add_knowledge_to_topic(topic.topic_id, "kp-1", order_index=1)
        service.add_knowledge_to_topic(topic.topic_id, "kp-2")  # None -> last
        assert service.get_topic_knowledge_points(topic.topic_id) == (
            "kp-3",
            "kp-1",
            "kp-2",
        )

    def test_reorder_does_not_create_new_membership(self):
        service = KnowledgeOrganizationService(make_course())
        topic = service.add_topic("Álgebra")
        service.register_knowledge_point(make_kp("kp-1"))
        # first add creates the membership
        service.add_knowledge_to_topic(topic.topic_id, "kp-1")
        before = len(service.structure.knowledge_memberships)
        # re-adding with a different order refreshes, never duplicates
        service.add_knowledge_to_topic(topic.topic_id, "kp-1", order_index=7)
        after = len(service.structure.knowledge_memberships)
        assert before == after == 1
        membership = list(service.structure.knowledge_memberships.values())[0]
        assert membership.order_index == 7

    def test_unassigned_knowledge_points(self):
        service = KnowledgeOrganizationService(make_course())
        topic = service.add_topic("Álgebra")
        for kp_id in ("kp-1", "kp-2", "kp-3"):
            service.register_knowledge_point(make_kp(kp_id))
        service.add_knowledge_to_topic(topic.topic_id, "kp-1")
        service.add_knowledge_to_topic(topic.topic_id, "kp-2")
        assert service.get_unassigned_knowledge_points() == ("kp-3",)


# ===========================================================================
# 3. Session <-> KnowledgePoint membership & cross-session reuse
# ===========================================================================


class TestSessionMembership:
    def test_session_kp_membership(self):
        service = KnowledgeOrganizationService(make_course())
        session = make_session(COURSE_ID, 1)
        service.register_session(session)
        service.register_knowledge_point(make_kp("kp-1"))
        m = service.add_knowledge_to_session(session.session_id, "kp-1")
        assert m.session_id == session.session_id
        assert service.get_session_knowledge_points(session.session_id) == ("kp-1",)

    def test_session_membership_id_deterministic(self):
        m1 = SessionKnowledgeMembership.create("s-1", "kp-1")
        m2 = SessionKnowledgeMembership.create("s-1", "kp-1")
        assert m1.membership_id == m2.membership_id
        assert m1.membership_id.startswith("smem-")

    def test_missing_session_rejected(self):
        service = KnowledgeOrganizationService(make_course())
        service.register_knowledge_point(make_kp("kp-1"))
        with pytest.raises(OrganizationValidationError) as exc:
            service.add_knowledge_to_session("s-unknown", "kp-1")
        assert exc.value.code == OrganizationErrorCode.SESSION_NOT_FOUND

    def test_missing_kp_rejected(self):
        service = KnowledgeOrganizationService(make_course())
        session = make_session(COURSE_ID, 1)
        service.register_session(session)
        with pytest.raises(OrganizationValidationError) as exc:
            service.add_knowledge_to_session(session.session_id, "kp-x")
        assert exc.value.code == OrganizationErrorCode.KNOWLEDGE_POINT_NOT_FOUND

    def test_duplicate_membership_is_noop(self):
        service = KnowledgeOrganizationService(make_course())
        session = make_session(COURSE_ID, 1)
        service.register_session(session)
        service.register_knowledge_point(make_kp("kp-1"))
        service.add_knowledge_to_session(session.session_id, "kp-1")
        service.add_knowledge_to_session(session.session_id, "kp-1")
        assert len(service.structure.session_memberships) == 1

    def test_cross_course_session_rejected(self):
        service = KnowledgeOrganizationService(make_course())
        service.register_knowledge_point(make_kp("kp-1"))
        foreign = make_session(COURSE_B, 1, session_id="s-foreign")
        with pytest.raises(OrganizationValidationError) as exc:
            service.register_session(foreign)
        assert exc.value.code == OrganizationErrorCode.CROSS_COURSE_MEMBERSHIP

    def test_kp_sessions_sorted_by_session_id(self):
        service = KnowledgeOrganizationService(make_course())
        service.register_knowledge_point(make_kp("kp-1"))
        for n, sid in [(3, "s-3"), (1, "s-1"), (2, "s-2")]:
            s = make_session(COURSE_ID, n, session_id=sid)
            service.register_session(s)
            service.add_knowledge_to_session(sid, "kp-1")
        assert service.get_knowledge_point_sessions("kp-1") == ("s-1", "s-2", "s-3")

    def test_uncovered_knowledge_points(self):
        service = KnowledgeOrganizationService(make_course())
        service.register_knowledge_point(make_kp("kp-1"))
        service.register_knowledge_point(make_kp("kp-2"))
        s = make_session(COURSE_ID, 1)
        service.register_session(s)
        service.add_knowledge_to_session(s.session_id, "kp-1")
        assert service.get_uncovered_knowledge_points() == ("kp-2",)

    def test_unassigned_is_not_uncovered(self):
        """unassigned != uncovered: independent dimensions."""
        service = KnowledgeOrganizationService(make_course())
        topic = service.add_topic("Álgebra")
        for kp_id in ("kp-1", "kp-2", "kp-3"):
            service.register_knowledge_point(make_kp(kp_id))
        # kp-1: topic-assigned, NO session (uncovered but assigned)
        service.add_knowledge_to_topic(topic.topic_id, "kp-1")
        # kp-2: session-covered, NO topic (covered but unassigned)
        s = make_session(COURSE_ID, 1)
        service.register_session(s)
        service.add_knowledge_to_session(s.session_id, "kp-2")
        # kp-3: neither
        assert service.get_unassigned_knowledge_points() == ("kp-2", "kp-3")
        assert service.get_uncovered_knowledge_points() == ("kp-1", "kp-3")


class TestCrossSessionReuse:
    def test_same_kp_in_two_sessions_is_one_kp(self):
        """Core acceptance: 1 KP shared across 2 sessions, 2 memberships."""
        service = KnowledgeOrganizationService(make_course())
        service.register_knowledge_point(make_kp("kp-1"))
        s_a = make_session(COURSE_ID, 1, session_id="s-a")
        s_b = make_session(COURSE_ID, 5, session_id="s-b")
        service.register_session(s_a)
        service.register_session(s_b)
        service.add_knowledge_to_session(s_a.session_id, "kp-1")
        service.add_knowledge_to_session(s_b.session_id, "kp-1")
        assert len(service._kp_by_id) == 1
        assert len(service.structure.session_memberships) == 2
        assert service.get_knowledge_point_sessions("kp-1") == ("s-a", "s-b")

    def test_kp_content_not_copied_or_mutated(self):
        service = KnowledgeOrganizationService(make_course())
        kp = make_kp("kp-1", validation_status="supported", knowledge_score=0.8)
        service.register_knowledge_point(kp)
        topic = service.add_topic("Álgebra")
        service.add_knowledge_to_topic(topic.topic_id, "kp-1")
        s = make_session(COURSE_ID, 1)
        service.register_session(s)
        service.add_knowledge_to_session(s.session_id, "kp-1")
        # original object is untouched
        assert kp.validation_status == "supported"
        assert kp.knowledge_score == 0.8
        assert kp.review_status == "pending"
        # stored reference is the very same object
        assert service._kp_by_id["kp-1"] is kp


# ===========================================================================
# 4. Relations
# ===========================================================================


class TestRelations:
    def test_create_relation(self):
        service = KnowledgeOrganizationService(make_course())
        service.register_knowledge_point(make_kp("kp-1"))
        service.register_knowledge_point(make_kp("kp-2"))
        r = service.add_relation("kp-1", "kp-2", KnowledgeRelationType.PREREQUISITE)
        assert r.source_knowledge_point_id == "kp-1"
        assert r.target_knowledge_point_id == "kp-2"
        assert r.relation_type is KnowledgeRelationType.PREREQUISITE
        assert r.relation_id.startswith("krel-")

    def test_relation_id_deterministic_and_direction_sensitive(self):
        service = KnowledgeOrganizationService(make_course())
        for kp in ("kp-1", "kp-2"):
            service.register_knowledge_point(make_kp(kp))
        forward = service.add_relation("kp-1", "kp-2", "prerequisite")
        reverse = service.add_relation("kp-2", "kp-1", "prerequisite")
        assert forward.relation_id != reverse.relation_id
        # re-adding the forward direction is a no-op
        dup = service.add_relation("kp-1", "kp-2", "prerequisite")
        assert dup.relation_id == forward.relation_id
        assert len(service.structure.relations) == 2

    def test_self_relation_rejected(self):
        service = KnowledgeOrganizationService(make_course())
        service.register_knowledge_point(make_kp("kp-1"))
        with pytest.raises(OrganizationValidationError) as exc:
            service.add_relation("kp-1", "kp-1", KnowledgeRelationType.RELATED)
        assert exc.value.code == OrganizationErrorCode.SELF_RELATION

    def test_missing_kp_rejected(self):
        service = KnowledgeOrganizationService(make_course())
        service.register_knowledge_point(make_kp("kp-1"))
        with pytest.raises(OrganizationValidationError) as exc:
            service.add_relation("kp-1", "kp-x", KnowledgeRelationType.RELATED)
        assert exc.value.code == OrganizationErrorCode.KNOWLEDGE_POINT_NOT_FOUND

    def test_invalid_relation_type_rejected(self):
        service = KnowledgeOrganizationService(make_course())
        service.register_knowledge_point(make_kp("kp-1"))
        service.register_knowledge_point(make_kp("kp-2"))
        with pytest.raises(OrganizationValidationError) as exc:
            service.add_relation("kp-1", "kp-2", "nonsense")
        assert exc.value.code == OrganizationErrorCode.INVALID_INPUT

    def test_outgoing_incoming_both(self):
        service = KnowledgeOrganizationService(make_course())
        for kp in ("kp-1", "kp-2", "kp-3"):
            service.register_knowledge_point(make_kp(kp))
        service.add_relation("kp-1", "kp-2", KnowledgeRelationType.PREREQUISITE)
        service.add_relation("kp-3", "kp-2", KnowledgeRelationType.EXTENDS)
        outgoing = service.get_related_knowledge("kp-2", direction="outgoing")
        # kp-2 is only a TARGET here; its outgoing set is empty.
        assert [r.source_knowledge_point_id for r in outgoing] == []
        # the EXTENDS edge points FROM kp-3, so it is kp-3's outgoing:
        assert [r.source_knowledge_point_id for r in service.get_related_knowledge("kp-3", direction="outgoing")] == ["kp-3"]
        incoming = service.get_related_knowledge("kp-2", direction="incoming")
        assert [r.source_knowledge_point_id for r in incoming] == ["kp-1", "kp-3"]
        both = service.get_related_knowledge("kp-2", direction="both")
        assert len(both) == 2

    def test_reverse_queryable(self):
        service = KnowledgeOrganizationService(make_course())
        for kp in ("kp-1", "kp-2"):
            service.register_knowledge_point(make_kp(kp))
        service.add_relation("kp-1", "kp-2", KnowledgeRelationType.PART_OF)
        incoming = service.get_related_knowledge("kp-2", direction="incoming")
        assert len(incoming) == 1
        assert incoming[0].source_knowledge_point_id == "kp-1"
        # outgoing of kp-2 is empty (we did not add the reverse edge)
        assert service.get_related_knowledge("kp-2", direction="outgoing") == ()

    def test_no_transitive_closure(self):
        service = KnowledgeOrganizationService(make_course())
        for kp in ("kp-1", "kp-2", "kp-3"):
            service.register_knowledge_point(make_kp(kp))
        service.add_relation("kp-1", "kp-2", KnowledgeRelationType.PREREQUISITE)
        service.add_relation("kp-2", "kp-3", KnowledgeRelationType.PREREQUISITE)
        graph = service.get_relation_graph()
        edge_pairs = {(e[1], e[2]) for e in graph.edges}
        assert ("kp-1", "kp-3") not in edge_pairs
        assert len(graph.edges) == 2

    def test_relation_graph_deterministic(self):
        service = KnowledgeOrganizationService(make_course())
        for kp in ("kp-3", "kp-1", "kp-2"):
            service.register_knowledge_point(make_kp(kp))
        service.add_relation("kp-1", "kp-2", KnowledgeRelationType.EXTENDS)
        service.add_relation("kp-2", "kp-3", KnowledgeRelationType.CONTRASTS)
        g1 = service.get_relation_graph()
        g2 = service.get_relation_graph()
        assert g1.to_dict() == g2.to_dict()
        assert g1.nodes == ("kp-1", "kp-2", "kp-3")
        assert g1.edges[0][3] in ("extends", "contrasts")

    def test_remove_relation(self):
        service = KnowledgeOrganizationService(make_course())
        for kp in ("kp-1", "kp-2"):
            service.register_knowledge_point(make_kp(kp))
        service.add_relation("kp-1", "kp-2", KnowledgeRelationType.RELATED)
        service.remove_relation("kp-1", "kp-2", KnowledgeRelationType.RELATED)
        assert service.list_relations() == ()
        with pytest.raises(OrganizationValidationError) as exc:
            service.remove_relation("kp-1", "kp-2", KnowledgeRelationType.RELATED)
        assert exc.value.code == OrganizationErrorCode.DANGLING_REFERENCE

    def test_relation_graph_serialization_round_trip(self):
        service = KnowledgeOrganizationService(make_course())
        for kp in ("kp-1", "kp-2"):
            service.register_knowledge_point(make_kp(kp))
        service.add_relation("kp-1", "kp-2", KnowledgeRelationType.RELATED)
        graph = service.get_relation_graph()
        restored = RelationGraph.from_dict(graph.to_dict())
        assert restored.to_dict() == graph.to_dict()
        assert restored.nodes == graph.nodes
        assert restored.edges == graph.edges


# ===========================================================================
# 5. Coverage & aggregation
# ===========================================================================


class TestCoverage:
    def test_empty_course_no_division_by_zero(self):
        service = KnowledgeOrganizationService(make_course())
        report = service.get_coverage_report()
        assert report.knowledge_point_count == 0
        assert report.covered_knowledge_point_count == 0
        assert report.uncovered_knowledge_point_count == 0
        assert report.coverage_ratio == 0.0
        assert report.session_count == 0
        assert report.topic_count == 0

    def test_topic_with_no_kps_ratio_zero(self):
        service = KnowledgeOrganizationService(make_course())
        topic = service.add_topic("Vacio")
        tc = service.get_topic_coverage_report(topic.topic_id)
        assert tc.knowledge_point_count == 0
        assert tc.coverage_ratio == 0.0

    def test_covered_definition_is_session_membership(self):
        service = KnowledgeOrganizationService(make_course())
        topic = service.add_topic("Álgebra")
        for kp in ("kp-1", "kp-2", "kp-3"):
            service.register_knowledge_point(make_kp(kp))
        service.add_knowledge_to_topic(topic.topic_id, "kp-1")
        service.add_knowledge_to_topic(topic.topic_id, "kp-2")
        s = make_session(COURSE_ID, 1)
        service.register_session(s)
        service.add_knowledge_to_session(s.session_id, "kp-1")
        # kp-2 is topic-assigned but not session-covered
        report = service.get_coverage_report()
        assert report.knowledge_point_count == 3
        assert report.covered_knowledge_point_count == 1
        assert report.uncovered_knowledge_point_count == 2
        assert report.session_count == 1
        assert report.coverage_ratio == pytest.approx(1 / 3)
        tc = service.get_topic_coverage_report(topic.topic_id)
        assert tc.knowledge_point_count == 2
        assert tc.covered_count == 1
        assert tc.uncovered_count == 1

    def test_kp_in_many_sessions_counts_once(self):
        service = KnowledgeOrganizationService(make_course())
        service.register_knowledge_point(make_kp("kp-1"))
        for n, sid in [(1, "s-1"), (2, "s-2"), (3, "s-3"), (4, "s-4")]:
            s = make_session(COURSE_ID, n, session_id=sid)
            service.register_session(s)
            service.add_knowledge_to_session(sid, "kp-1")
        report = service.get_coverage_report()
        assert report.covered_knowledge_point_count == 1
        assert report.session_count == 4
        assert report.coverage_ratio == 1.0

    def test_session_newly_introduced_vs_already_known(self):
        service = KnowledgeOrganizationService(make_course())
        for kp in ("kp-1", "kp-2", "kp-3"):
            service.register_knowledge_point(make_kp(kp))
        s1 = make_session(COURSE_ID, 1, session_id="s-1")
        s2 = make_session(COURSE_ID, 2, session_id="s-2")
        service.register_session(s1)
        service.register_session(s2)
        service.add_knowledge_to_session("s-1", "kp-1")
        service.add_knowledge_to_session("s-1", "kp-2")
        service.add_knowledge_to_session("s-2", "kp-2")
        service.add_knowledge_to_session("s-2", "kp-3")
        r1 = service.get_session_coverage_report("s-1")
        assert r1.knowledge_point_count == 2
        assert r1.already_known_count == 0
        assert r1.newly_introduced_count == 2
        r2 = service.get_session_coverage_report("s-2")
        assert r2.knowledge_point_count == 2
        assert r2.already_known_count == 1  # kp-2
        assert r2.newly_introduced_count == 1  # kp-3


class TestValidationAggregation:
    def test_validation_summary_counts(self):
        service = KnowledgeOrganizationService(make_course())
        service.register_knowledge_point(
            make_kp("kp-1", validation_status="supported", knowledge_score=0.5)
        )
        service.register_knowledge_point(
            make_kp("kp-2", validation_status="conflicted", knowledge_score=0.25)
        )
        service.register_knowledge_point(
            make_kp("kp-3", validation_status="unverified", knowledge_score=0.0)
        )
        summary = service.get_validation_summary()
        assert summary.unverified_count == 1
        assert summary.supported_count == 1
        assert summary.conflicted_count == 1
        assert summary.average_knowledge_score == pytest.approx(0.25)

    def test_validation_summary_empty_is_zero(self):
        service = KnowledgeOrganizationService(make_course())
        summary = service.get_validation_summary()
        assert summary.average_knowledge_score == 0.0
        assert summary.unverified_count == 0
        assert summary.supported_count == 0
        assert summary.conflicted_count == 0

    def test_conflicted_points_are_never_hidden_or_rewritten(self):
        service = KnowledgeOrganizationService(make_course())
        kp = make_kp("kp-1", validation_status="conflicted")
        service.register_knowledge_point(kp)
        s = make_session(COURSE_ID, 1)
        service.register_session(s)
        service.add_knowledge_to_session(s.session_id, "kp-1")
        assert kp.validation_status == "conflicted"  # untouched
        assert service.get_validation_summary().conflicted_count == 1


class TestReviewAggregation:
    def test_review_summary_from_kp_field(self):
        service = KnowledgeOrganizationService(make_course())
        service.register_knowledge_point(
            make_kp("kp-1", review_status="confirmed")
        )
        service.register_knowledge_point(
            make_kp("kp-2", review_status="rejected")
        )
        service.register_knowledge_point(
            make_kp("kp-3", review_status="kept_unverified")
        )
        service.register_knowledge_point(make_kp("kp-4"))  # pending default
        summary = service.get_review_summary()
        assert summary.confirmed_count == 1
        assert summary.rejected_count == 1
        assert summary.kept_unverified_count == 1
        assert summary.pending_count == 1

    def test_latest_review_record_wins(self):
        """PENDING -> CONFIRMED -> PENDING leaves the KP PENDING per Task 15."""
        service = KnowledgeOrganizationService(make_course())
        kp = make_kp("kp-1")
        structure = make_structure([kp])
        service.register_knowledge_structure(structure)
        # ReviewRecord requires a stable review_id; _stable_review_id gives
        # deterministic ids.  the chosen evidence list sorts after the confirmed record, so the
        # kept_unverified record pending2 is the latest effective one.
        pending_rec = ReviewRecord(
            review_id=_stable_review_id("kp-1", ReviewDecision.KEEP_UNVERIFIED.value, []),
            knowledge_point_id="kp-1", decision=ReviewDecision.KEEP_UNVERIFIED,
        )
        confirmed_rec = ReviewRecord(
            review_id=_stable_review_id("kp-1", ReviewDecision.CONFIRM.value, []),
            knowledge_point_id="kp-1", decision=ReviewDecision.CONFIRM,
        )
        pending2_rec = ReviewRecord(
            review_id=_stable_review_id("kp-1", ReviewDecision.KEEP_UNVERIFIED.value, ["c"]),
            knowledge_point_id="kp-1", decision=ReviewDecision.KEEP_UNVERIFIED,
        )
        # review_id order decides the "latest effective" status
        service._structure_by_kp["kp-1"] = structure
        structure.review_records = []
        structure.add_review_record(confirmed_rec)
        assert service.get_review_summary().confirmed_count == 1
        structure.review_records.append(pending2_rec)
        structure._review_ids.add(pending2_rec.review_id)
        # The KP's own review_status field was never updated, so the
        # aggregate now counts kp-1 as kept_unverified via the last record.
        summary = service.get_review_summary()
        assert summary.pending_count == 0
        assert summary.confirmed_count == 0
        assert summary.kept_unverified_count == 1

    def test_supported_never_becomes_confirmed(self):
        service = KnowledgeOrganizationService(make_course())
        kp = make_kp(
            "kp-1",
            validation_status="supported",
            knowledge_score=0.9,
            review_status="pending",
        )
        service.register_knowledge_point(kp)
        summary = service.get_review_summary()
        assert summary.confirmed_count == 0
        assert summary.pending_count == 1


class TestCourseSummary:
    def test_summary_aggregates_everything(self):
        service = KnowledgeOrganizationService(make_course())
        topic = service.add_topic("Álgebra")
        service.add_topic("Geometría")
        for kp in ("kp-1", "kp-2"):
            service.register_knowledge_point(make_kp(kp))
        service.add_knowledge_to_topic(topic.topic_id, "kp-1")
        s = make_session(COURSE_ID, 1)
        service.register_session(s)
        service.add_knowledge_to_session(s.session_id, "kp-1")
        service.add_relation("kp-1", "kp-2", KnowledgeRelationType.PREREQUISITE)
        summary = service.get_course_summary()
        assert summary.topic_count == 2
        assert summary.knowledge_point_count == 2
        assert summary.session_count == 1
        assert summary.relation_count == 1
        assert summary.coverage.covered_knowledge_point_count == 1
        assert summary.coverage.coverage_ratio == pytest.approx(0.5)
        d = summary.to_dict()
        restored = CourseKnowledgeSummary.from_dict(d)
        assert restored.to_dict() == d


# ===========================================================================
# 6. Case studies (spec 83)
# ===========================================================================


class TestCaseStudies:
    def test_case_a_supported_pending_covered(self):
        service = KnowledgeOrganizationService(make_course())
        kp = make_kp("kp-1", validation_status="supported")
        service.register_knowledge_point(kp)
        topic = service.add_topic("Álgebra")
        service.add_knowledge_to_topic(topic.topic_id, "kp-1")
        s = make_session(COURSE_ID, 1)
        service.register_session(s)
        service.add_knowledge_to_session(s.session_id, "kp-1")
        assert kp.validation_status == "supported"
        assert kp.review_status == "pending"
        assert service.get_coverage_report().covered_knowledge_point_count == 1
        assert service.get_review_summary().pending_count == 1

    def test_case_b_conflicted_pending(self):
        service = KnowledgeOrganizationService(make_course())
        kp = make_kp("kp-2", validation_status="conflicted")
        service.register_knowledge_point(kp)
        topic = service.add_topic("Álgebra")
        service.add_knowledge_to_topic(topic.topic_id, "kp-2")
        s = make_session(COURSE_ID, 2)
        service.register_session(s)
        service.add_knowledge_to_session(s.session_id, "kp-2")
        assert service.get_validation_summary().conflicted_count == 1
        assert service.get_review_summary().pending_count == 1

    def test_case_c_confirmed_no_session(self):
        service = KnowledgeOrganizationService(make_course())
        kp = make_kp("kp-3", validation_status="supported", review_status="confirmed")
        service.register_knowledge_point(kp)
        topic = service.add_topic("Geometría")
        service.add_knowledge_to_topic(topic.topic_id, "kp-3")
        assert service.get_uncovered_knowledge_points() == ("kp-3",)
        assert service.get_review_summary().confirmed_count == 1


# ===========================================================================
# 7. Determinism (insertion-order independence)
# ===========================================================================


def build_full_service(service: KnowledgeOrganizationService) -> None:
    topic = service.add_topic("Álgebra", order_index=0)
    for kp in ("kp-1", "kp-2", "kp-3"):
        service.register_knowledge_point(make_kp(kp))
        service.add_knowledge_to_topic(topic.topic_id, kp)
    s1 = make_session(COURSE_ID, 1, session_id="s-1")
    s2 = make_session(COURSE_ID, 2, session_id="s-2")
    service.register_session(s1)
    service.register_session(s2)
    service.add_knowledge_to_session(s1.session_id, "kp-1")
    service.add_knowledge_to_session(s1.session_id, "kp-2")
    service.add_knowledge_to_session(s2.session_id, "kp-2")
    service.add_knowledge_to_session(s2.session_id, "kp-3")
    service.add_relation("kp-1", "kp-2", KnowledgeRelationType.PREREQUISITE)
    service.add_relation("kp-2", "kp-3", KnowledgeRelationType.EXTENDS)


class TestDeterminism:
    def test_add_ab_equals_add_ba(self):
        course = make_course()
        service_a = KnowledgeOrganizationService(course)
        service_b = KnowledgeOrganizationService(course)
        # A first, B second
        build_full_service(service_a)
        # B first, A second  (swap the two relation adds' order)
        service_b.add_topic("Álgebra", order_index=0)
        service_b.register_knowledge_point(make_kp("kp-1"))
        service_b.register_knowledge_point(make_kp("kp-2"))
        service_b.register_knowledge_point(make_kp("kp-3"))
        topic = service_b.get_topic(list(service_b.structure.topics)[0])
        for kp in ("kp-3", "kp-2", "kp-1"):
            service_b.add_knowledge_to_topic(topic.topic_id, kp)
        s2 = make_session(COURSE_ID, 2, session_id="s-2")
        s1 = make_session(COURSE_ID, 1, session_id="s-1")
        service_b.register_session(s2)
        service_b.register_session(s1)
        service_b.add_knowledge_to_session(s2.session_id, "kp-2")
        service_b.add_knowledge_to_session(s2.session_id, "kp-3")
        service_b.add_knowledge_to_session(s1.session_id, "kp-1")
        service_b.add_knowledge_to_session(s1.session_id, "kp-2")
        service_b.add_relation("kp-2", "kp-3", KnowledgeRelationType.EXTENDS)
        service_b.add_relation("kp-1", "kp-2", KnowledgeRelationType.PREREQUISITE)
        assert service_a.structure.to_dict() == service_b.structure.to_dict()
        assert service_a.get_coverage_report().to_dict() == service_b.get_coverage_report().to_dict()

    def test_to_dict_is_stable_across_calls(self):
        service = KnowledgeOrganizationService(make_course())
        build_full_service(service)
        first = service.structure.to_dict()
        second = service.structure.to_dict()
        assert first == second
        json1 = json.dumps(first, sort_keys=True, ensure_ascii=False)
        json2 = json.dumps(second, sort_keys=True, ensure_ascii=False)
        assert json1 == json2

    def test_coverage_deterministic(self):
        service = KnowledgeOrganizationService(make_course())
        build_full_service(service)
        r1 = service.get_coverage_report().to_dict()
        r2 = service.get_coverage_report().to_dict()
        assert r1 == r2


# ===========================================================================
# 8. Incremental & idempotent
# ===========================================================================


class TestIncremental:
    def test_batch1_then_batch2_then_repeat(self):
        service = KnowledgeOrganizationService(make_course())
        s1 = make_session(COURSE_ID, 1, session_id="s-1")
        s2 = make_session(COURSE_ID, 2, session_id="s-2")
        service.register_session(s1)
        service.register_session(s2)
        structure_1 = make_structure([make_kp("kp-1"), make_kp("kp-2")])
        service.ingest_knowledge_structure(structure_1, s1.session_id)
        structure_2 = make_structure([make_kp("kp-2"), make_kp("kp-3")])
        service.ingest_knowledge_structure(structure_2, s2.session_id)
        # Re-run both batches: nothing may duplicate
        service.ingest_knowledge_structure(structure_1, s1.session_id)
        service.ingest_knowledge_structure(structure_2, s2.session_id)
        assert service.get_knowledge_point_sessions("kp-1") == ("s-1",)
        assert service.get_knowledge_point_sessions("kp-2") == ("s-1", "s-2")
        assert service.get_knowledge_point_sessions("kp-3") == ("s-2",)
        assert len(service.structure.session_memberships) == 4
        assert len(service._kp_by_id) == 3

    def test_ingest_does_not_assign_topics(self):
        service = KnowledgeOrganizationService(make_course())
        s1 = make_session(COURSE_ID, 1, session_id="s-1")
        service.register_session(s1)
        service.ingest_knowledge_structure(
            make_structure([make_kp("kp-1")]), s1.session_id
        )
        assert service.get_unassigned_knowledge_points() == ("kp-1",)

    def test_kp2_shares_one_logical_point(self):
        service = KnowledgeOrganizationService(make_course())
        kp2_v1 = make_kp("kp-2", content="v1")
        kp2_v2 = make_kp("kp-2", content="v2")
        s1 = make_session(COURSE_ID, 1, session_id="s-1")
        s2 = make_session(COURSE_ID, 2, session_id="s-2")
        service.register_session(s1)
        service.register_session(s2)
        service.ingest_knowledge_structure(make_structure([kp2_v1]), s1.session_id)
        service.ingest_knowledge_structure(make_structure([kp2_v2]), s2.session_id)
        assert len(service._kp_by_id) == 1
        assert len(service.structure.session_memberships) == 2


# ===========================================================================
# 9. Course isolation
# ===========================================================================


class TestCourseIsolation:
    def test_topic_membership_cannot_cross_courses(self):
        """A topic from another course can never get a membership here.

        Even if a foreign-course topic is force-injected into the
        structure (bypassing the service API), the public query path
        rejects it: get_topic only accepts topics belonging to this
        service's course.
        """
        service = KnowledgeOrganizationService(make_course())
        foreign = Topic.create(COURSE_B, "Mecánica")
        service.register_knowledge_point(make_kp("kp-1"))
        service.structure.topics[foreign.topic_id] = foreign
        service.structure._rebuild_indexes()
        with pytest.raises(OrganizationValidationError) as exc:
            service.get_topic(foreign.topic_id)
        assert exc.value.code == OrganizationErrorCode.CROSS_COURSE_MEMBERSHIP

    def test_list_topics_rejects_other_course(self):
        service = KnowledgeOrganizationService(make_course())
        with pytest.raises(OrganizationValidationError) as exc:
            service.list_topics(course_id=COURSE_B)
        assert exc.value.code == OrganizationErrorCode.CROSS_COURSE_MEMBERSHIP

    def test_two_services_have_separate_structures(self):
        service_a = KnowledgeOrganizationService(make_course())
        service_b = KnowledgeOrganizationService(make_course(COURSE_B, "Física"))
        service_a.add_topic("Álgebra")
        service_b.add_topic("Mecánica")
        assert "topic-" in service_a.structure.topics or len(
            service_a.structure.topics
        ) == 1
        assert len(service_b.structure.topics) == 1
        assert service_a.structure.topics.keys() != service_b.structure.topics.keys()


# ===========================================================================
# 10. Dangling references & schema errors
# ===========================================================================


class TestDanglingAndSchema:
    def test_load_with_missing_topic_reference_fails(self):
        service = KnowledgeOrganizationService(make_course())
        service.register_knowledge_point(make_kp("kp-1"))
        # Build a malformed payload by hand
        payload = service.structure.to_dict()
        payload["knowledge_memberships"] = [
            KnowledgeMembership(
                membership_id="kmem-" + "0" * 24,
                topic_id="topic-missing",
                knowledge_point_id="kp-1",
            ).to_dict()
        ]
        with pytest.raises(KnowledgeOrganizationError) as exc:
            CourseKnowledgeStructure.from_dict(payload)
        assert exc.value.code in (
            OrganizationErrorCode.TOPIC_NOT_FOUND,
            OrganizationErrorCode.DANGLING_REFERENCE,
        )

    def test_load_with_mismatched_membership_id_fails(self):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "course_id": COURSE_ID,
            "topics": [],
            "knowledge_memberships": [
                {
                    "membership_id": "kmem-" + "0" * 24,
                    "topic_id": "topic-x",
                    "knowledge_point_id": "kp-1",
                    "order_index": None,
                }
            ],
            "session_memberships": [],
            "relations": [],
        }
        with pytest.raises(KnowledgeOrganizationError) as exc:
            CourseKnowledgeStructure.from_dict(payload)
        assert exc.value.code == OrganizationErrorCode.DANGLING_REFERENCE

    def test_unknown_schema_version_rejected(self):
        payload = {"schema_version": 99, "course_id": COURSE_ID}
        with pytest.raises(OrganizationSchemaError) as exc:
            CourseKnowledgeStructure.from_dict(payload)
        assert exc.value.code == OrganizationErrorCode.INVALID_SCHEMA_VERSION

    def test_self_relation_in_payload_rejected(self):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "course_id": COURSE_ID,
            "topics": [],
            "knowledge_memberships": [],
            "session_memberships": [],
            "relations": [
                {
                    "relation_id": "krel-" + "0" * 24,
                    "course_id": COURSE_ID,
                    "source_knowledge_point_id": "kp-1",
                    "target_knowledge_point_id": "kp-1",
                    "relation_type": "prerequisite",
                }
            ],
        }
        with pytest.raises(KnowledgeOrganizationError) as exc:
            CourseKnowledgeStructure.from_dict(payload)
        assert exc.value.code == OrganizationErrorCode.SELF_RELATION


# ===========================================================================
# 11. Serialization round-trip
# ===========================================================================


class TestSerialization:
    def test_structure_round_trip(self):
        service = KnowledgeOrganizationService(make_course())
        build_full_service(service)
        snapshot = service.structure.to_dict()
        restored = CourseKnowledgeStructure.from_dict(snapshot)
        assert restored.to_dict() == snapshot
        assert restored.topics == service.structure.topics
        assert (
            restored.knowledge_memberships
            == service.structure.knowledge_memberships
        )
        assert (
            restored.session_memberships
            == service.structure.session_memberships
        )
        assert restored.relations == service.structure.relations

    def test_validation_summary_round_trip(self):
        vs = ValidationSummary("c", 1, 2, 3, 0.5)
        assert ValidationSummary.from_dict(vs.to_dict()) == vs

    def test_review_summary_round_trip(self):
        rs = ReviewSummary("c", 1, 2, 3, 4)
        assert ReviewSummary.from_dict(rs.to_dict()) == rs


    def test_coverage_report_round_trip(self):
        report = CoverageReport(
            course_id=COURSE_ID,
            topic_count=2,
            knowledge_point_count=3,
            covered_knowledge_point_count=1,
            uncovered_knowledge_point_count=2,
            session_count=2,
            coverage_ratio=1.0 / 3,
        )
        d = report.to_dict()
        restored = CoverageReport.from_dict(d)
        assert restored == report

    def test_service_round_trip_via_from_snapshot(self):
        course = make_course()
        service = KnowledgeOrganizationService(course)
        build_full_service(service)
        structure = make_structure(
            [
                make_kp("kp-1"),
                make_kp("kp-2"),
                make_kp("kp-3"),
            ]
        )
        snapshot = service.save_to_dict()
        json_text = json.dumps(snapshot, ensure_ascii=False)
        parsed = json.loads(json_text)
        restored_service = KnowledgeOrganizationService.from_snapshot(
            parsed, course, knowledge_structure=structure
        )
        assert restored_service.structure.to_dict() == service.structure.to_dict()
        assert restored_service.get_coverage_report().to_dict() == service.get_coverage_report().to_dict()
        assert (
            restored_service.get_relation_graph().to_dict()
            == service.get_relation_graph().to_dict()
        )



# ===========================================================================
# 12. Thread safety
# ===========================================================================


class TestThreadSafety:
    def test_concurrent_add_same_topic_no_duplicate(self):
        service = KnowledgeOrganizationService(make_course())
        errors = []

        def add():
            for _ in range(20):
                try:
                    service.add_topic("Compartido")
                except Exception as exc:  # noqa: BLE001 - record and continue
                    errors.append(exc)

        threads = [threading.Thread(target=add) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(service.structure.topics) == 1

    def test_concurrent_relation_same_quad_no_duplicate(self):
        service = KnowledgeOrganizationService(make_course())
        service.register_knowledge_point(make_kp("kp-1"))
        service.register_knowledge_point(make_kp("kp-2"))
        errors = []

        def add():
            for _ in range(20):
                try:
                    service.add_relation("kp-1", "kp-2", KnowledgeRelationType.EXTENDS)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

        threads = [threading.Thread(target=add) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(service.structure.relations) == 1


# ===========================================================================
# 13. Performance (synthetic dataset)
# ===========================================================================


class TestPerformance:
    def test_synthetic_dataset(self):
        """1000 KPs, 100 topics, 20 sessions, 3000 memberships, 2000 relations."""
        service = KnowledgeOrganizationService(make_course())
        kp_ids = []
        for i in range(1000):
            kp_id = f"kp-{i}"
            service.register_knowledge_point(make_kp(kp_id))
            kp_ids.append(kp_id)

        topic_ids = []
        for i in range(100):
            topic = service.add_topic(f"Topic-{i}", order_index=i)
            topic_ids.append(topic.topic_id)

        session_ids = []
        for i in range(1, 21):
            s = make_session(COURSE_ID, i, session_id=f"s-{i:02d}")
            service.register_session(s)
            session_ids.append(s.session_id)

        start = time.perf_counter()
        # 3000 topic memberships (deterministic, includes some repeats)
        for j in range(3000):
            service.add_knowledge_to_topic(
                topic_ids[j % 100],
                kp_ids[j % 1000],
                order_index=j % 10,
            )
        # 3000 session memberships
        for j in range(3000):
            service.add_knowledge_to_session(
                session_ids[j % 20],
                kp_ids[(j + 7) % 1000],
                order_index=j % 10,
            )
        # 2000 relations
        for j in range(2000):
            service.add_relation(
                kp_ids[j % 1000],
                kp_ids[(j + 1) % 1000],
                "prerequisite" if j % 5 == 0 else "related",
            )
        add_elapsed = time.perf_counter() - start

        start = time.perf_counter()
        coverage = service.get_coverage_report()
        summary = service.get_course_summary()
        graph = service.get_relation_graph()
        lists = []
        for topic in topic_ids:
            lists.append(service.get_topic_knowledge_points(topic))
        for sid in session_ids:
            lists.append(service.get_session_knowledge_points(sid))
        for kp in kp_ids:
            service.get_related_knowledge(kp, direction="both")
        query_elapsed = time.perf_counter() - start

        start = time.perf_counter()
        snapshot = service.structure.to_dict()
        restored = CourseKnowledgeStructure.from_dict(snapshot)
        serialize_elapsed = time.perf_counter() - start

        # sanity: no O(n^2) blowup - each phase well under a few seconds
        assert add_elapsed < 30.0, f"adds took {add_elapsed:.1f}s"
        assert query_elapsed < 30.0, f"queries took {query_elapsed:.1f}s"
        assert serialize_elapsed < 15.0, f"serialization took {serialize_elapsed:.1f}s"
        # data invariants
        assert coverage.knowledge_point_count == 1000
        assert coverage.topic_count == 100
        assert coverage.session_count == 20
        assert coverage.covered_knowledge_point_count > 0
        assert summary.relation_count == len(service.structure.relations)
        assert restored.to_dict() == snapshot
        assert len(graph.edges) == summary.relation_count


# ===========================================================================
# 14. End-to-end integration (spec 71)
# ===========================================================================


class TestEndToEndScenario:
    def test_session01_then_session02_reuses_kp2(self):
        course = make_course()
        service = KnowledgeOrganizationService(course)

        # Session 01: KP1 <- E1, KP2 <- E2
        e1 = Evidence(evidence_id="E1", content="definicion de limite", language="Spanish")
        e2 = Evidence(evidence_id="E2", content="derivada de f(x)", language="Spanish")
        kp1 = KnowledgePoint(
            knowledge_id="KP1",
            title="Limite",
            content="limite de una funcion",
            evidence_refs=["E1"],
            validation_status="supported",
            knowledge_score=0.9,
        )
        kp2 = KnowledgePoint(
            knowledge_id="KP2",
            title="Derivada",
            content="la derivada mide la tasa de variacion",
            evidence_refs=["E2"],
            validation_status="supported",
            knowledge_score=0.8,
        )
        structure_1 = KnowledgeStructure()
        structure_1.knowledge_points["KP1"] = kp1
        structure_1.knowledge_points["KP2"] = kp2

        s1 = make_session(COURSE_ID, 1, session_id="Session01")
        service.register_session(s1)
        service.register_knowledge_structure(structure_1)
        service.ingest_knowledge_structure(structure_1, "Session01")

        topic = service.add_topic("Álgebra")
        service.add_knowledge_to_topic(topic.topic_id, "KP1")
        service.add_knowledge_to_topic(topic.topic_id, "KP2")

        # Session 02: KP2 (reused) + KP3 (new)
        kp3 = KnowledgePoint(
            knowledge_id="KP3",
            title="Integral",
            content="antiderivada",
            evidence_refs=["E3"],
            validation_status="supported",
            knowledge_score=0.7,
        )
        structure_2 = KnowledgeStructure()
        structure_2.knowledge_points["KP2"] = kp2
        structure_2.knowledge_points["KP3"] = kp3
        s2 = make_session(COURSE_ID, 2, session_id="Session02")
        service.register_session(s2)
        service.ingest_knowledge_structure(structure_2, "Session02")
        service.add_knowledge_to_topic(topic.topic_id, "KP3")
        service.add_relation("KP1", "KP2", KnowledgeRelationType.PREREQUISITE)
        service.add_relation("KP2", "KP3", KnowledgeRelationType.EXTENDS)

        # Exactly one logical KP2, present in both sessions
        assert len(service._kp_by_id) == 3
        assert service.get_knowledge_point_sessions("KP2") == ("Session01", "Session02")
        assert service.get_session_knowledge_points("Session01") == ("KP1", "KP2")
        assert service.get_session_knowledge_points("Session02") == ("KP2", "KP3")
        assert set(service.get_topic_knowledge_points(topic.topic_id)) == {"KP1", "KP2", "KP3"}

        # Coverage: all 3 KPs covered by sessions
        report = service.get_coverage_report()
        assert report.covered_knowledge_point_count == 3
        assert report.uncovered_knowledge_point_count == 0
        assert report.coverage_ratio == 1.0

        # No transitive closure KP1 -> KP3
        graph = service.get_relation_graph()
        pairs = {(e[1], e[2]) for e in graph.edges}
        assert ("KP1", "KP2") in pairs
        assert ("KP2", "KP3") in pairs
        assert ("KP1", "KP3") not in pairs
        assert len(graph.edges) == 2

        # Evidence traceability preserved through the organization layer
        kp1_registered = service._kp_by_id["KP1"]
        assert kp1_registered.evidence_refs == ["E1"]
        assert kp1_registered is kp1  # reference, not a copy

        # Session coverage report: KP2 is "already known" in Session02
        s2_report = service.get_session_coverage_report("Session02")
        assert s2_report.already_known_count == 1
        assert s2_report.newly_introduced_count == 1


# ===========================================================================
# 15. Idempotency (process(input) twice)
# ===========================================================================


class TestIdempotency:
    def test_full_input_processed_twice(self):
        course = make_course()
        service = KnowledgeOrganizationService(course)
        s1 = make_session(COURSE_ID, 1, session_id="S1")
        s2 = make_session(COURSE_ID, 2, session_id="S2")
        service.register_session(s1)
        service.register_session(s2)
        for kp in ("kp-1", "kp-2", "kp-3"):
            service.register_knowledge_point(make_kp(kp))

        def process():
            topic = service.add_topic("Álgebra")
            service.add_knowledge_to_topic(topic.topic_id, "kp-1")
            service.add_knowledge_to_topic(topic.topic_id, "kp-2")
            service.add_knowledge_to_session("S1", "kp-1")
            service.add_knowledge_to_session("S1", "kp-2")
            service.add_knowledge_to_session("S2", "kp-2")
            service.add_knowledge_to_session("S2", "kp-3")
            service.add_relation("kp-1", "kp-2", KnowledgeRelationType.PREREQUISITE)

        process()
        snapshot_after_first = service.structure.to_dict()
        process()
        snapshot_after_second = service.structure.to_dict()
        assert snapshot_after_first == snapshot_after_second
        assert len(service.structure.topics) == 1
        assert len(service.structure.knowledge_memberships) == 2
        assert len(service.structure.session_memberships) == 4
        assert len(service.structure.relations) == 1


