# -*- coding: utf-8 -*-
"""Task 34 - AppService end-to-end facade tests."""

import pytest

from src.models import Course, Language
from src.application.app_service import AppService
from src.application.errors import ApplicationError, InvalidInputError, NotFoundError


def _app_service() -> AppService:
    course = Course(course_id="course-app", name="App", code="A", language=Language.SPANISH)
    return AppService(course)


def _kp_payload(kp_id="kp-1") -> dict:
    return {"knowledge_id": kp_id, "title": "t-"+kp_id, "content": "c-"+kp_id}


def test_app_service_requires_course():
    with pytest.raises(InvalidInputError):
        AppService(None)


def test_app_service_rejects_explicit_none_course():
    with pytest.raises(InvalidInputError):
        AppService(None)


def test_app_service_exposes_sub_services():
    app = _app_service()
    assert app.course_id == "course-app"
    assert app.course_service is not None
    assert app.material_service is not None
    assert app.knowledge_service is not None
    assert app.review_service is not None
    assert app.learning_service is not None
    assert app.store is not None
    assert app.ingestion_service is not None
    assert app.org_service is not None


def test_register_knowledge_point_returns_dto():
    app = _app_service()
    result = app.register_knowledge_point(_kp_payload())
    assert result["knowledge_id"] == "kp-1"
    assert result["title"] == "t-kp-1"


def test_register_knowledge_point_is_idempotent():
    app = _app_service()
    r1 = app.register_knowledge_point(_kp_payload())
    r2 = app.register_knowledge_point(_kp_payload())
    assert r1["knowledge_id"] == r2["knowledge_id"] == "kp-1"
    assert len(app.list_knowledge_points()) == 1


def test_register_knowledge_point_requires_knowledge_id():
    app = _app_service()
    with pytest.raises(InvalidInputError):
        app.register_knowledge_point({"title": "t", "content": "c"})


def test_register_knowledge_point_requires_title():
    app = _app_service()
    with pytest.raises(InvalidInputError):
        app.register_knowledge_point({"knowledge_id": "kp", "content": "c"})


def test_register_knowledge_point_requires_content():
    app = _app_service()
    with pytest.raises(InvalidInputError):
        app.register_knowledge_point({"knowledge_id": "kp", "title": "t"})


def test_register_knowledge_point_rejects_non_mapping():
    app = _app_service()
    with pytest.raises(InvalidInputError):
        app.register_knowledge_point("not-a-mapping")


def test_list_knowledge_points_empty_initially():
    assert _app_service().list_knowledge_points() == []


def test_list_knowledge_points_sorted_by_id():
    app = _app_service()
    app.register_knowledge_point(_kp_payload("b"))
    app.register_knowledge_point(_kp_payload("a"))
    ids = [kp["knowledge_id"] for kp in app.list_knowledge_points()]
    assert ids == ["a", "b"]


def test_map_error_passthrough_application_error():
    err = NotFoundError("x")
    assert AppService.map_error(err) is err


def test_map_error_internal_error_for_unknown_exception():
    mapped = AppService.map_error(RuntimeError("boom"))
    assert isinstance(mapped, ApplicationError)
    assert mapped.code == "INTERNAL_ERROR"


def test_end_to_end_learning_flow():
    app = _app_service()
    course = app.course_service.create_course("App", code="A")
    assert course["course_id"]
    material = app.material_service.register_material(
        course_id=course["course_id"],
        session_id=None,
        filename="lesson-01.txt",
        material_type="text",
    )
    assert material["material_id"]
    ingestion = app.material_service.ingest_material(material["material_id"])
    assert ingestion["material_id"] == material["material_id"]
    kp = app.register_knowledge_point(_kp_payload("kp-1"))
    assert kp["knowledge_id"] == "kp-1"
    student = app.learning_service.create_student("student-1")
    assert student["student_id"] == "student-1"
    exercise = app.learning_service.create_exercise(
        "multiple_choice",
        "Capital of Spain?",
        ["kp-1"],
        choices=[
            {"choice_id": "a", "text": "Madrid"},
            {"choice_id": "b", "text": "Lisbon"},
        ],
        correct_choice_id="a",
    )
    assert exercise["exercise_id"].startswith("exercise-")
    answer = app.learning_service.submit_answer("student-1", exercise["exercise_id"], "a")
    assert answer["evaluation_status"] == "correct"
    evaluation = app.learning_service.get_evaluation(answer["answer_id"])
    assert evaluation["status"] == "correct"
    plan = app.learning_service.get_study_plan("student-1")
    assert isinstance(plan, dict)


def test_end_to_end_review_flow():
    # ReviewService 在构造时快照 KP 视图; 先注册 KP, 再构造 AppService
    course = Course(course_id="course-app", name="App", code="A", language=Language.SPANISH)
    from src.knowledge_organization import KnowledgeOrganizationService
    org = KnowledgeOrganizationService(course)
    from src.models import KnowledgePoint
    org.register_knowledge_point(KnowledgePoint(knowledge_id="kp-rev", title="t", content="c"))
    app = AppService(course, org_service=org)
    record = app.review_service.confirm("kp-rev", note="ok")
    assert record["knowledge_point_id"] == "kp-rev"
    assert record["decision"] == "confirm"
    history = app.review_service.get_review_history("kp-rev")
    assert len(history) == 1


def test_app_service_knowledge_service_maps_errors():
    app = _app_service()
    with pytest.raises(NotFoundError):
        app.knowledge_service.get_knowledge_point("missing")
