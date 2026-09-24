# -*- coding: utf-8 -*-
"""Task 34 — KnowledgeService / ReviewService 单元测试。

覆盖:
- KnowledgeService: get_knowledge_points / get_knowledge_point / get_coverage /
  get_gaps / get_dependencies / get_conflicts / get_evidence_for_knowledge_point /
  get_review_candidates
- ReviewService: confirm / reject / keep_unverified / resolve_conflict /
  get_review_history / get_review_candidates
重点: not found / invalid input / CONFLICTED 必须选证据 / 幂等 / 确定性
"""

from typing import Any, Dict, List

import pytest

from src.models import Course, Evidence, KnowledgePoint, Language
from src.evidence_store import EvidenceStore
from src.knowledge_organization import KnowledgeOrganizationService
from src.knowledge_structure import ConflictRecord, KnowledgeStructure
from src.application.dto import knowledge_point_to_dict
from src.application.errors import InvalidInputError, NotFoundError
from src.application.knowledge_service import KnowledgeService, ReviewService


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------


def _course() -> Course:
    return Course(course_id="course-x", name="X", code="X", language=Language.SPANISH)


def _org_service() -> KnowledgeOrganizationService:
    return KnowledgeOrganizationService(_course())


def _kp(
    kp_id: str,
    *,
    validation_status: str = "unverified",
    evidence_refs: List[str] | None = None,
    needs_verification: bool = False,
    review_status: str = "pending",
    knowledge_score: float = 0.0,
) -> KnowledgePoint:
    return KnowledgePoint(
        knowledge_id=kp_id,
        title=f"title-{kp_id}",
        content=f"content-{kp_id}",
        evidence_refs=list(evidence_refs or []),
        needs_verification=needs_verification,
        validation_status=validation_status,
        knowledge_score=knowledge_score,
        review_status=review_status,
    )


def _register(org: KnowledgeOrganizationService, kp: KnowledgePoint) -> None:
    org.register_knowledge_point(kp)


# ----------------------------------------------------------------------
# KnowledgeService — get_knowledge_points / get_knowledge_point
# ----------------------------------------------------------------------


def test_get_knowledge_points_empty_service():
    svc = KnowledgeService(_org_service())
    assert svc.get_knowledge_points() == []


def test_get_knowledge_points_sorted_by_id():
    org = _org_service()
    _register(org, _kp("b-kp"))
    _register(org, _kp("a-kp"))
    svc = KnowledgeService(org)
    result = svc.get_knowledge_points()
    assert [item["knowledge_id"] for item in result] == ["a-kp", "b-kp"]
    assert isinstance(result[0], dict)


def test_get_knowledge_point_not_found():
    org = _org_service()
    _register(org, _kp("kp1"))
    svc = KnowledgeService(org)
    with pytest.raises(NotFoundError):
        svc.get_knowledge_point("does-not-exist")


def test_get_knowledge_point_requires_nonempty_id():
    svc = KnowledgeService(_org_service())
    with pytest.raises(InvalidInputError):
        svc.get_knowledge_point("")


def test_get_knowledge_point_returns_dto():
    org = _org_service()
    _register(org, _kp("kp1", evidence_refs=["ev-1"]))
    svc = KnowledgeService(org)
    result = svc.get_knowledge_point("kp1")
    assert result["knowledge_id"] == "kp1"
    assert result["evidence_refs"] == ["ev-1"]


def test_generation_mode_distinguishes_ai_summary_from_deterministic_fallback():
    assert knowledge_point_to_dict(_kp("aikp-abc123"))["generation_mode"] == "ai_summary"
    assert (
        knowledge_point_to_dict(_kp("kp-abc123"))["generation_mode"]
        == "deterministic_fallback"
    )
    assert knowledge_point_to_dict(_kp("manual-1"))["generation_mode"] == "manual"


# ----------------------------------------------------------------------
# KnowledgeService — coverage / gaps / dependencies / conflicts
# ----------------------------------------------------------------------


def test_get_coverage_returns_dict():
    org = _org_service()
    _register(org, _kp("kp1", validation_status="supported", knowledge_score=1.0))
    svc = KnowledgeService(org)
    result = svc.get_coverage("course-x")
    assert isinstance(result, dict)


def test_get_coverage_requires_course_id():
    svc = KnowledgeService(_org_service())
    with pytest.raises(InvalidInputError):
        svc.get_coverage("")


def test_get_gaps_returns_dict():
    org = _org_service()
    _register(org, _kp("kp1", validation_status="unverified"))
    svc = KnowledgeService(org)
    result = svc.get_gaps("course-x")
    assert isinstance(result, dict)


def test_get_dependencies_returns_dict():
    org = _org_service()
    _register(org, _kp("kp1"))
    _register(org, _kp("kp2", evidence_refs=[]))
    svc = KnowledgeService(org)
    result = svc.get_dependencies("course-x")
    assert isinstance(result, dict)


def test_get_conflicts_empty_by_default():
    org = _org_service()
    _register(org, _kp("kp1"))
    svc = KnowledgeService(org)
    assert svc.get_conflicts("course-x") == []


def test_get_conflicts_returns_sorted_by_conflict_id():
    # 冲突记录只存在于 KnowledgeStructure 中, 经 register_knowledge_structure
    # 注入时同步到 _structure_by_kp (而非单独 register_knowledge_point)
    from src.knowledge_structure import KnowledgeStructure

    org = _org_service()
    ks = KnowledgeStructure()
    ks.add_knowledge_point(_kp("kp1", evidence_refs=["ev-a"]))
    ks.add_conflict(
        ConflictRecord(
            conflict_id="conf-2",
            evidence_refs=["ev-a", "ev-b"],
            description="d2",
        )
    )
    ks.add_conflict(
        ConflictRecord(
            conflict_id="conf-1",
            evidence_refs=["ev-a", "ev-c"],
            description="d1",
        )
    )
    org.register_knowledge_structure(ks)
    svc = KnowledgeService(org)
    result = svc.get_conflicts("course-x")
    assert [c["conflict_id"] for c in result] == ["conf-1", "conf-2"]


def test_get_evidence_for_knowledge_point_not_found():
    org = _org_service()
    _register(org, _kp("kp1"))
    svc = KnowledgeService(org)
    with pytest.raises(NotFoundError):
        svc.get_evidence_for_knowledge_point("missing")


def test_get_evidence_for_knowledge_point_without_store_returns_empty():
    org = _org_service()
    _register(org, _kp("kp1", evidence_refs=["ev-1"]))
    svc = KnowledgeService(org, store=None)
    assert svc.get_evidence_for_knowledge_point("kp1") == []


def test_get_evidence_for_knowledge_point_with_store():
    org = _org_service()
    _register(org, _kp("kp1", evidence_refs=["ev-1", "ev-missing"]))
    store = EvidenceStore()
    store.add(
        Evidence(evidence_id="ev-1", content="text-1", language=Language.SPANISH)
    )
    svc = KnowledgeService(org, store=store)
    result = svc.get_evidence_for_knowledge_point("kp1")
    assert [e["evidence_id"] for e in result] == ["ev-1"]


# ----------------------------------------------------------------------
# KnowledgeService — review candidates
# ----------------------------------------------------------------------


def test_get_review_candidates_lists_unverified_points():
    org = _org_service()
    _register(org, _kp("kp-unverified", validation_status="unverified"))
    _register(org, _kp("kp-supported-stable", validation_status="supported",
                       knowledge_score=1.0, review_status="confirmed"))
    svc = KnowledgeService(org)
    candidates = svc.get_review_candidates("course-x")
    ids = [c["knowledge_point_id"] for c in candidates]
    assert "kp-unverified" in ids
    assert "kp-supported-stable" not in ids


def test_get_review_candidates_conflicted_has_priority_over_unverified():
    org = _org_service()
    _register(org, _kp("kp-conf", validation_status="conflicted",
                       evidence_refs=["ev-1"]))
    _register(org, _kp("kp-unv", validation_status="unverified"))
    svc = KnowledgeService(org)
    candidates = svc.get_review_candidates("course-x")
    ids = [c["knowledge_point_id"] for c in candidates]
    assert ids.index("kp-conf") < ids.index("kp-unv")


# ----------------------------------------------------------------------
# ReviewService — confirm / reject / keep_unverified
# ----------------------------------------------------------------------


def test_review_service_confirm_unverified_kp():
    org = _org_service()
    _register(org, _kp("kp1", validation_status="unverified"))
    svc = ReviewService(org)
    record = svc.confirm("kp1", note="ok")
    assert record["knowledge_point_id"] == "kp1"
    assert record["decision"] == "confirm"
    assert record["note"] == "ok"


def test_review_service_confirm_kp_not_found():
    org = _org_service()
    _register(org, _kp("kp1"))
    svc = ReviewService(org)
    with pytest.raises(NotFoundError):
        svc.confirm("kp-missing")


def test_review_service_confirm_requires_evidence_when_conflicted():
    org = _org_service()
    _register(org, _kp("kp-conf", validation_status="conflicted",
                       evidence_refs=["ev-1"]))
    svc = ReviewService(org)
    with pytest.raises(InvalidInputError):
        svc.confirm("kp-conf")  # 无 evidence 选择


def test_review_service_confirm_conflicted_with_selected_evidence():
    org = _org_service()
    _register(org, _kp("kp-conf", validation_status="conflicted",
                       evidence_refs=["ev-1", "ev-2"]))
    svc = ReviewService(org)
    record = svc.confirm("kp-conf", selected_evidence_ids=["ev-1"])
    assert record["selected_evidence_ids"] == ["ev-1"]


def test_review_service_confirm_rejects_unrelated_evidence_id():
    org = _org_service()
    _register(org, _kp("kp1", validation_status="supported",
                       evidence_refs=["ev-1"]))
    svc = ReviewService(org)
    with pytest.raises(InvalidInputError):
        svc.confirm("kp1", selected_evidence_ids=["ev-unknown"])


def test_review_service_reject():
    org = _org_service()
    _register(org, _kp("kp1", validation_status="unverified"))
    svc = ReviewService(org)
    record = svc.reject("kp1", note="bad")
    assert record["decision"] == "reject"
    assert record["note"] == "bad"


def test_review_service_reject_kp_not_found():
    org = _org_service()
    _register(org, _kp("kp1"))
    svc = ReviewService(org)
    with pytest.raises(NotFoundError):
        svc.reject("kp-missing")


def test_review_service_keep_unverified():
    org = _org_service()
    _register(org, _kp("kp1", validation_status="unverified"))
    svc = ReviewService(org)
    record = svc.keep_unverified("kp1")
    assert record["decision"] == "keep_unverified"


# ----------------------------------------------------------------------
# ReviewService — resolve_conflict
# ----------------------------------------------------------------------


def test_review_service_resolve_conflict_requires_nonempty_selection():
    org = _org_service()
    _register(org, _kp("kp-conf", validation_status="conflicted",
                       evidence_refs=["ev-1"]))
    svc = ReviewService(org)
    with pytest.raises(InvalidInputError):
        svc.resolve_conflict("kp-conf", selected_evidence_ids=[])


def test_review_service_resolve_conflict_success():
    org = _org_service()
    _register(org, _kp("kp-conf", validation_status="conflicted",
                       evidence_refs=["ev-1", "ev-2"]))
    svc = ReviewService(org)
    record = svc.resolve_conflict(
        "kp-conf", selected_evidence_ids=["ev-2"], note="trust ev-2"
    )
    assert record["selected_evidence_ids"] == ["ev-2"]
    assert record["decision"] == "confirm"


def test_review_service_resolve_conflict_kp_not_found():
    org = _org_service()
    _register(org, _kp("kp1"))
    svc = ReviewService(org)
    with pytest.raises(NotFoundError):
        svc.resolve_conflict("kp-missing", selected_evidence_ids=["ev-1"])


def test_review_service_resolve_conflict_rejects_unrelated_evidence():
    org = _org_service()
    _register(org, _kp("kp-conf", validation_status="conflicted",
                       evidence_refs=["ev-1"]))
    svc = ReviewService(org)
    with pytest.raises(InvalidInputError):
        svc.resolve_conflict("kp-conf", selected_evidence_ids=["ev-unknown"])


# ----------------------------------------------------------------------
# ReviewService — idempotency + review history
# ----------------------------------------------------------------------


def test_review_service_confirm_is_idempotent():
    org = _org_service()
    _register(org, _kp("kp1", validation_status="unverified"))
    svc = ReviewService(org)
    r1 = svc.confirm("kp1", note="first")
    r2 = svc.confirm("kp1", note="first")
    assert r1["review_id"] == r2["review_id"]
    history = svc.get_review_history("kp1")
    assert len(history) == 1


def test_review_service_get_review_history_empty_for_unknown_kp():
    org = _org_service()
    _register(org, _kp("kp1"))
    svc = ReviewService(org)
    # 未提交任何评审记录时, 历史应为空 (即便 KP 存在)
    assert svc.get_review_history("kp1") == []


def test_review_service_get_review_history_requires_nonempty_id():
    org = _org_service()
    _register(org, _kp("kp1"))
    svc = ReviewService(org)
    with pytest.raises(InvalidInputError):
        svc.get_review_history("")


def test_review_service_review_candidates_empty_structure():
    org = _org_service()
    svc = ReviewService(org)
    assert svc.get_review_candidates("course-x") == []


def test_review_service_confirm_updates_review_status():
    org = _org_service()
    _register(org, _kp("kp1", validation_status="unverified"))
    svc = ReviewService(org)
    svc.confirm("kp1")
    kp = org._kp_by_id["kp1"]
    assert kp.review_status == "confirmed"


def test_review_service_reject_updates_review_status():
    org = _org_service()
    _register(org, _kp("kp1", validation_status="unverified"))
    svc = ReviewService(org)
    svc.reject("kp1")
    kp = org._kp_by_id["kp1"]
    assert kp.review_status == "rejected"


def test_review_service_deterministic_review_id():
    org1 = _org_service()
    _register(org1, _kp("kp1", validation_status="unverified"))
    svc1 = ReviewService(org1)
    r1 = svc1.confirm("kp1", selected_evidence_ids=["ev-1"])

    org2 = _org_service()
    _register(org2, _kp("kp1", validation_status="unverified",
                        evidence_refs=["ev-1"]))
    svc2 = ReviewService(org2)
    r2 = svc2.confirm("kp1", selected_evidence_ids=["ev-1"])

    assert r1["review_id"] == r2["review_id"]

