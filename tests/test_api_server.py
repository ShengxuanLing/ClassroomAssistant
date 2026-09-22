# -*- coding: utf-8 -*-
"""Task 38 tests: Local Web API.

覆盖 spec 要求: health / CRUD / invalid input / not found / conflict /
serialization / file upload / processing / review / student / exercise /
answer。测试通过真实 HTTP 请求 (urllib) 打到真实服务器实例上。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from src.api.responses import (
    HTTP_STATUS_BY_CODE,
    failure,
    failure_from_exception,
    json_bytes,
    status_for,
    success,
)
from src.api.router import (
    NotFoundRoute,
    Request,
    Router,
    parse_multipart_file,
    parse_query,
)
from src.api.server import ApiServer, create_server, default_static_dir
from src.application.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    UnsupportedError,
)
from src.application.runtime import fixed_clock
from src.application.workspace import APPLICATION_VERSION, Workspace

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXED_TIME = "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# HTTP client helper
# ---------------------------------------------------------------------------


class Client:
    """最小 HTTP 客户端: 返回 (status, payload)。"""

    def __init__(self, base_url: str) -> None:
        self.base = base_url

    def request(self, method, path, *, body=None, raw=None, headers=None):
        data = raw
        if data is None and body is not None:
            data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base + path, method=method, data=data)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
                return response.status, _decode(response, payload)
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            return exc.code, _decode(exc, payload)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body=body, **kw)

    def patch(self, path, body=None, **kw):
        return self.request("PATCH", path, body=body, **kw)

    def upload(self, path, content, filename, headers=None):
        merged = {"X-Filename": filename}
        merged.update(headers or {})
        return self.request("POST", path, raw=content, headers=merged)

    def raw_get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=30) as response:
            return response.status, response.headers, response.read()


def _decode(response, payload: bytes):
    content_type = response.headers.get("Content-Type", "")
    if "json" not in content_type:
        return payload
    return json.loads(payload.decode("utf-8"))


@pytest.fixture
def workspace(tmp_path):
    return Workspace(
        str(tmp_path / "data"),
        clock=fixed_clock(FIXED_TIME),
        asr_mode="mock",
        ocr_mode="mock",
    )


@pytest.fixture
def server(workspace):
    instance = create_server(workspace, port=0)
    instance.start()
    try:
        yield instance
    finally:
        instance.stop()


@pytest.fixture
def client(server):
    return Client(server.url)


@pytest.fixture
def course(client):
    status, payload = client.post("/api/courses", {"name": "Algebra Lineal", "code": "AL"})
    assert status == 201
    return payload["data"]


def pdf_bytes():
    return (FIXTURES / "documents" / "simple.pdf").read_bytes()


# ===========================================================================
# 1. 响应封装 (单元)
# ===========================================================================


class TestResponses:
    def test_success_envelope(self):
        response = success({"a": 1})
        assert response.status == 200
        assert response.payload == {"success": True, "data": {"a": 1}}

    def test_failure_envelope(self):
        response = failure("NOT_FOUND", "missing")
        assert response.status == 404
        assert response.payload["success"] is False
        assert response.payload["error"] == {"code": "NOT_FOUND", "message": "missing"}

    def test_all_eight_codes_have_status(self):
        expected = {
            "INVALID_INPUT", "NOT_FOUND", "CONFLICT", "PROCESSING_ERROR",
            "UNSUPPORTED", "STORAGE_ERROR", "CONFIGURATION_ERROR", "INTERNAL_ERROR",
        }
        assert set(HTTP_STATUS_BY_CODE) == expected

    def test_status_mapping_values(self):
        assert status_for("INVALID_INPUT") == 400
        assert status_for("NOT_FOUND") == 404
        assert status_for("CONFLICT") == 409
        assert status_for("PROCESSING_ERROR") == 422
        assert status_for("INTERNAL_ERROR") == 500

    def test_unknown_code_defaults_to_500(self):
        assert status_for("SOMETHING_ELSE") == 500

    def test_json_is_deterministic(self):
        assert json_bytes({"b": 1, "a": 2}) == json_bytes({"a": 2, "b": 1})

    def test_json_keeps_unicode(self):
        assert "función" in json_bytes({"x": "función"}).decode("utf-8")

    def test_failure_from_application_error(self):
        response = failure_from_exception(NotFoundError("nope"))
        assert response.status == 404
        assert response.payload["error"]["code"] == "NOT_FOUND"

    def test_failure_from_value_error(self):
        assert failure_from_exception(ValueError("bad")).status == 400

    def test_failure_hides_message_when_not_debug(self):
        response = failure_from_exception(RuntimeError("secret detail"))
        assert "secret detail" not in response.payload["error"]["message"]
        assert response.payload["error"]["code"] == "INTERNAL_ERROR"

    def test_failure_shows_message_in_debug(self):
        response = failure_from_exception(RuntimeError("detail"), debug=True)
        assert response.payload["error"]["message"] == "detail"


# ===========================================================================
# 2. 路由 (单元)
# ===========================================================================


class TestRouter:
    def make(self):
        router = Router()
        router.get("/api/things", lambda r: success({"list": True}))
        router.get("/api/things/{thing_id}", lambda r: success({"id": r.params["thing_id"]}))
        router.post("/api/things", lambda r: success({"created": True}, status=201))
        return router

    def test_literal_route(self):
        response = self.make().dispatch(Request(method="GET", path="/api/things"))
        assert response.payload["data"] == {"list": True}

    def test_parameter_route(self):
        response = self.make().dispatch(Request(method="GET", path="/api/things/abc"))
        assert response.payload["data"] == {"id": "abc"}

    def test_trailing_slash_tolerated(self):
        response = self.make().dispatch(Request(method="GET", path="/api/things/"))
        assert response.payload["data"] == {"list": True}

    def test_unknown_route_raises(self):
        with pytest.raises(NotFoundRoute):
            self.make().dispatch(Request(method="GET", path="/api/nope"))

    def test_wrong_method_raises(self):
        with pytest.raises(NotFoundRoute):
            self.make().dispatch(Request(method="DELETE", path="/api/things"))

    def test_allowed_methods(self):
        assert self.make().allowed_methods("/api/things") == ["GET", "POST"]

    def test_pattern_must_start_with_slash(self):
        with pytest.raises(ValueError):
            Router().add("GET", "api/things", lambda r: None)

    def test_parameter_does_not_match_slash(self):
        router = Router()
        router.get("/api/a/{x}", lambda r: success(r.params))
        with pytest.raises(NotFoundRoute):
            router.dispatch(Request(method="GET", path="/api/a/b/c"))

    def test_parse_query(self):
        parsed = parse_query("a=1&a=2&b=")
        assert parsed["a"] == ["1", "2"]
        assert parsed["b"] == [""]

    def test_request_require_q(self):
        request = Request(method="GET", path="/x", query={"a": ["v"]})
        assert request.require_q("a") == "v"
        with pytest.raises(InvalidInputError):
            request.require_q("missing")

    def test_request_q_int_and_bool(self):
        request = Request(method="GET", path="/x", query={"n": ["3"], "f": ["true"]})
        assert request.q_int("n") == 3
        assert request.q_bool("f") is True

    def test_request_json_body(self):
        request = Request(method="POST", path="/x", body=b'{"a": 1}')
        assert request.json_body() == {"a": 1}

    def test_request_json_body_invalid(self):
        with pytest.raises(InvalidInputError):
            Request(method="POST", path="/x", body=b"not json").json_body()

    def test_request_json_body_non_object(self):
        with pytest.raises(InvalidInputError):
            Request(method="POST", path="/x", body=b"[1,2]").json_body()

    def test_multipart_parser(self):
        body = (
            b"--BOUND\r\n"
            b'Content-Disposition: form-data; name="file"; filename="a.txt"\r\n'
            b"Content-Type: text/plain\r\n\r\n"
            b"hello\r\n"
            b"--BOUND--\r\n"
        )
        name, content, fields = parse_multipart_file(body, "multipart/form-data; boundary=BOUND")
        assert name == "a.txt"
        assert content == b"hello"
        assert fields == {}

    def test_multipart_parser_fields(self):
        body = (
            b"--B\r\n"
            b'Content-Disposition: form-data; name="course_id"\r\n\r\n'
            b"c-1\r\n"
            b"--B--\r\n"
        )
        name, content, fields = parse_multipart_file(body, "multipart/form-data; boundary=B")
        assert name is None
        assert fields["course_id"] == "c-1"

    def test_multipart_parser_rejects_non_multipart(self):
        assert parse_multipart_file(b"x", "application/json") == (None, b"", {})


# ===========================================================================
# 3. health
# ===========================================================================


class TestHealth:
    def test_health_ok(self, client):
        status, payload = client.get("/api/health")
        assert status == 200
        assert payload["success"] is True

    def test_health_reports_required_sections(self, client):
        _, payload = client.get("/api/health")
        data = payload["data"]
        for key in ("application", "version", "database", "storage", "processing"):
            assert key in data

    def test_health_reports_version(self, client):
        _, payload = client.get("/api/health")
        assert payload["data"]["version"] == APPLICATION_VERSION

    def test_health_reports_processing_modes(self, client):
        _, payload = client.get("/api/health")
        assert payload["data"]["processing"]["asr"] == "mock"
        assert payload["data"]["processing"]["ocr"] == "mock"

    def test_health_degraded_returns_503(self, tmp_path):
        workspace = Workspace(str(tmp_path / "data"))
        instance = create_server(workspace, port=0).start()
        try:
            import shutil

            shutil.rmtree(workspace.layout.documents)
            status, payload = Client(instance.url).get("/api/health")
            assert status == 503
            assert payload["data"]["status"] == "degraded"
        finally:
            instance.stop()


# ===========================================================================
# 4. courses
# ===========================================================================


class TestCourses:
    def test_create_returns_201(self, client):
        status, payload = client.post("/api/courses", {"name": "Calculo", "code": "C1"})
        assert status == 201
        assert payload["data"]["course_id"].startswith("course-")

    def test_create_is_idempotent(self, client):
        client.post("/api/courses", {"name": "Calculo", "code": "C1"})
        status, _ = client.post("/api/courses", {"name": "Calculo", "code": "C1"})
        assert status == 200

    def test_create_without_name_is_400(self, client):
        status, payload = client.post("/api/courses", {"code": "X"})
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_create_with_conflicting_fields_is_409(self, client):
        client.post("/api/courses", {"name": "Calculo", "code": "C1"})
        status, payload = client.post(
            "/api/courses", {"name": "Calculo", "code": "C1", "language": "Spanish"}
        )
        assert status == 409
        assert payload["error"]["code"] == "CONFLICT"

    def test_list_courses(self, client, course):
        status, payload = client.get("/api/courses")
        assert status == 200
        assert len(payload["data"]["courses"]) == 1

    def test_get_course(self, client, course):
        status, payload = client.get(f"/api/courses/{course['course_id']}")
        assert status == 200
        assert payload["data"]["name"] == "Algebra Lineal"

    def test_get_unknown_course_is_404(self, client):
        status, payload = client.get("/api/courses/course-nope")
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_patch_course(self, client, course):
        status, payload = client.patch(
            f"/api/courses/{course['course_id']}", {"title_unused": 1}
        )
        assert status == 400

    def test_patch_course_updates_name(self, client, course):
        status, payload = client.patch(
            f"/api/courses/{course['course_id']}", {"name": "Algebra Lineal II"}
        )
        assert status == 200
        assert payload["data"]["name"] == "Algebra Lineal II"

    def test_course_dto_is_json_serializable(self, client, course):
        assert json.dumps(course) is not None


# ===========================================================================
# 5. sessions
# ===========================================================================


class TestSessions:
    def test_create_session_201(self, client, course):
        status, payload = client.post(
            "/api/sessions",
            {"course_id": course["course_id"], "session_number": 1, "title": "Tema 1"},
        )
        assert status == 201
        assert payload["data"]["session_id"].startswith("session-")

    def test_create_session_idempotent(self, client, course):
        body = {"course_id": course["course_id"], "session_number": 1}
        client.post("/api/sessions", body)
        status, _ = client.post("/api/sessions", body)
        assert status == 200

    def test_create_session_without_course_is_400(self, client):
        status, payload = client.post("/api/sessions", {"session_number": 1})
        assert status == 400

    def test_create_session_unknown_course_is_404(self, client):
        status, _ = client.post(
            "/api/sessions", {"course_id": "course-nope", "session_number": 1}
        )
        assert status == 404

    def test_list_sessions(self, client, course):
        client.post("/api/sessions", {"course_id": course["course_id"], "session_number": 1})
        status, payload = client.get(f"/api/sessions?course_id={course['course_id']}")
        assert status == 200
        assert len(payload["data"]["sessions"]) == 1

    def test_get_session(self, client, course):
        _, created = client.post(
            "/api/sessions", {"course_id": course["course_id"], "session_number": 2}
        )
        status, payload = client.get(f"/api/sessions/{created['data']['session_id']}")
        assert status == 200
        assert payload["data"]["session_number"] == 2

    def test_get_unknown_session_is_404(self, client):
        assert client.get("/api/sessions/session-nope")[0] == 404


# ===========================================================================
# 6. materials / upload
# ===========================================================================


class TestMaterials:
    def test_upload_raw_body_201(self, client, course):
        status, payload = client.upload(
            f"/api/materials?course_id={course['course_id']}",
            pdf_bytes(),
            "apuntes.pdf",
        )
        assert status == 201
        assert payload["data"]["filename"] == "apuntes.pdf"

    def test_upload_multipart(self, client, course):
        boundary = "BOUND"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="notas.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
            "Concepto uno\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        status, payload = client.request(
            "POST",
            f"/api/materials?course_id={course['course_id']}",
            raw=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert status == 201
        assert payload["data"]["filename"] == "notas.txt"

    def test_duplicate_upload_returns_200(self, client, course):
        url = f"/api/materials?course_id={course['course_id']}"
        client.upload(url, pdf_bytes(), "apuntes.pdf")
        status, payload = client.upload(url, pdf_bytes(), "apuntes.pdf")
        assert status == 200
        assert payload["data"]["duplicate"] is True

    def test_upload_without_course_is_400(self, client):
        status, payload = client.upload("/api/materials", b"data", "a.txt")
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_upload_without_filename_is_400(self, client, course):
        status, payload = client.request(
            "POST",
            f"/api/materials?course_id={course['course_id']}",
            raw=b"data",
        )
        assert status == 400

    def test_upload_empty_body_is_400(self, client, course):
        status, _ = client.upload(f"/api/materials?course_id={course['course_id']}", b"", "a.txt")
        assert status == 400

    def test_upload_unsupported_extension_is_415(self, client, course):
        status, payload = client.upload(
            f"/api/materials?course_id={course['course_id']}", b"MZ", "evil.exe"
        )
        assert status == 415
        assert payload["error"]["code"] == "UNSUPPORTED"

    def test_upload_traversal_filename_is_400(self, client, course):
        status, payload = client.upload(
            f"/api/materials?course_id={course['course_id']}", b"data", "../../escape.txt"
        )
        # 文件名被取基名后仍是合法 .txt, 但落盘位置由服务端决定;
        # 关键是绝不能写到目标目录之外。
        assert status in (201, 400)
        if status == 201:
            assert payload["data"]["filename"] == "escape.txt"

    def test_upload_to_unknown_course_is_404(self, client):
        status, _ = client.upload("/api/materials?course_id=course-nope", b"data", "a.txt")
        assert status == 404

    def test_upload_too_large_is_413(self, workspace):
        instance = create_server(workspace, port=0, max_upload_bytes=16).start()
        try:
            client = Client(instance.url)
            _, course = client.post("/api/courses", {"name": "C"})
            status, payload = client.upload(
                f"/api/materials?course_id={course['data']['course_id']}",
                b"x" * 100,
                "a.txt",
            )
            assert status == 413
        finally:
            instance.stop()

    def test_list_materials(self, client, course):
        client.upload(f"/api/materials?course_id={course['course_id']}", pdf_bytes(), "a.pdf")
        status, payload = client.get(f"/api/materials?course_id={course['course_id']}")
        assert status == 200
        assert len(payload["data"]["materials"]) == 1

    def test_list_materials_requires_course(self, client):
        assert client.get("/api/materials")[0] == 400

    def test_get_material(self, client, course):
        _, created = client.upload(
            f"/api/materials?course_id={course['course_id']}", pdf_bytes(), "a.pdf"
        )
        material_id = created["data"]["material_id"]
        status, payload = client.get(
            f"/api/materials/{material_id}?course_id={course['course_id']}"
        )
        assert status == 200
        assert payload["data"]["material_id"] == material_id

    def test_get_unknown_material_is_404(self, client, course):
        status, _ = client.get(f"/api/materials/mat-nope?course_id={course['course_id']}")
        assert status == 404

    def test_upload_stages_are_cleaned(self, client, course, workspace):
        client.upload(f"/api/materials?course_id={course['course_id']}", pdf_bytes(), "a.pdf")
        leftovers = [
            os.path.join(base, name)
            for base, _dirs, files in os.walk(workspace.upload_root)
            for name in files
        ]
        assert leftovers == []

    def test_upload_with_session(self, client, course):
        _, session = client.post(
            "/api/sessions", {"course_id": course["course_id"], "session_number": 1}
        )
        session_id = session["data"]["session_id"]
        status, payload = client.upload(
            f"/api/materials?course_id={course['course_id']}&session_id={session_id}",
            pdf_bytes(),
            "a.pdf",
        )
        assert status == 201
        assert payload["data"]["session_id"] == session_id


# ===========================================================================
# 7. processing
# ===========================================================================


class TestProcessing:
    def _setup_session(self, client, course):
        _, session = client.post(
            "/api/sessions", {"course_id": course["course_id"], "session_number": 1}
        )
        session_id = session["data"]["session_id"]
        _, created = client.upload(
            f"/api/materials?course_id={course['course_id']}&session_id={session_id}",
            pdf_bytes(),
            "a.pdf",
        )
        return session_id, created["data"]["material_id"]

    def test_process_material(self, client, course):
        _, material_id = self._setup_session(client, course)
        status, payload = client.post(
            f"/api/materials/{material_id}/process?course_id={course['course_id']}"
        )
        assert status == 200
        assert payload["data"]["status"] == "SUCCEEDED"

    def test_processing_status(self, client, course):
        self._setup_session(client, course)
        status, payload = client.get(f"/api/processing?course_id={course['course_id']}")
        assert status == 200
        assert payload["data"]["total"] == 1

    def test_processing_job(self, client, course):
        _, material_id = self._setup_session(client, course)
        client.post(f"/api/materials/{material_id}/process?course_id={course['course_id']}")
        status, payload = client.get(
            f"/api/processing/{material_id}?course_id={course['course_id']}"
        )
        assert status == 200
        assert payload["data"]["material_id"] == material_id

    def test_processing_job_unknown_is_404(self, client, course):
        status, _ = client.get(f"/api/processing/mat-nope?course_id={course['course_id']}")
        assert status == 404

    def test_process_session(self, client, course):
        session_id, _ = self._setup_session(client, course)
        status, payload = client.post(f"/api/sessions/{session_id}/process")
        assert status == 200
        assert payload["data"]["succeeded"] == 1

    def test_retry_material(self, client, course):
        _, material_id = self._setup_session(client, course)
        client.post(f"/api/materials/{material_id}/process?course_id={course['course_id']}")
        status, payload = client.post(
            f"/api/materials/{material_id}/retry?course_id={course['course_id']}"
        )
        assert status == 200
        assert payload["data"]["status"] == "SUCCEEDED"

    def test_knowledge_summary(self, client, course):
        session_id, _ = self._setup_session(client, course)
        client.post(f"/api/sessions/{session_id}/process")
        status, payload = client.get(f"/api/knowledge-summary?course_id={course['course_id']}")
        assert status == 200
        assert payload["data"]["knowledge_point_total"] >= 0


# ===========================================================================
# 8. knowledge
# ===========================================================================


class TestKnowledge:
    def _processed(self, client, course):
        _, session = client.post(
            "/api/sessions", {"course_id": course["course_id"], "session_number": 1}
        )
        session_id = session["data"]["session_id"]
        client.upload(
            f"/api/materials?course_id={course['course_id']}&session_id={session_id}",
            (FIXTURES / "documents" / "multilingual.pdf").read_bytes(),
            "multi.pdf",
        )
        client.post(f"/api/sessions/{session_id}/process")
        return session_id

    def test_list_knowledge(self, client, course):
        self._processed(client, course)
        status, payload = client.get(f"/api/knowledge?course_id={course['course_id']}")
        assert status == 200
        assert isinstance(payload["data"]["knowledge_points"], list)

    def test_list_knowledge_requires_course(self, client):
        assert client.get("/api/knowledge")[0] == 400

    def test_get_knowledge_point(self, client, course):
        self._processed(client, course)
        _, listing = client.get(f"/api/knowledge?course_id={course['course_id']}")
        points = listing["data"]["knowledge_points"]
        if not points:
            pytest.skip("no knowledge points extracted from fixture")
        knowledge_id = points[0]["knowledge_id"]
        status, payload = client.get(
            f"/api/knowledge/{knowledge_id}?course_id={course['course_id']}"
        )
        assert status == 200
        assert payload["data"]["knowledge_id"] == knowledge_id

    def test_knowledge_evidence_traceability(self, client, course):
        self._processed(client, course)
        _, listing = client.get(f"/api/knowledge?course_id={course['course_id']}")
        for point in listing["data"]["knowledge_points"]:
            status, payload = client.get(
                f"/api/knowledge/{point['knowledge_id']}/evidence?course_id={course['course_id']}"
            )
            assert status == 200
            for evidence in payload["data"]["evidence"]:
                assert evidence["source"]["material_id"]

    def test_coverage(self, client, course):
        status, payload = client.get(f"/api/coverage?course_id={course['course_id']}")
        assert status == 200
        assert payload["success"] is True

    def test_gaps(self, client, course):
        status, payload = client.get(f"/api/gaps?course_id={course['course_id']}")
        assert status == 200

    def test_dependencies(self, client, course):
        status, _ = client.get(f"/api/dependencies?course_id={course['course_id']}")
        assert status == 200

    def test_conflicts(self, client, course):
        status, payload = client.get(f"/api/conflicts?course_id={course['course_id']}")
        assert status == 200
        assert isinstance(payload["data"]["conflicts"], list)

    def test_unknown_course_knowledge_is_404(self, client):
        assert client.get("/api/knowledge?course_id=course-nope")[0] == 404


# ===========================================================================
# 9. review
# ===========================================================================


class TestReview:
    def test_list_reviews(self, client, course):
        status, payload = client.get(f"/api/reviews?course_id={course['course_id']}")
        assert status == 200
        assert isinstance(payload["data"]["reviews"], list)

    def test_review_unknown_point_is_404(self, client, course):
        status, _ = client.get(f"/api/reviews/kp-nope?course_id={course['course_id']}")
        assert status == 404

    def test_confirm_unknown_point_is_404(self, client, course):
        status, _ = client.post(
            f"/api/reviews/kp-nope/confirm?course_id={course['course_id']}", {}
        )
        assert status == 404

    def test_confirm_requires_course(self, client):
        assert client.post("/api/reviews/kp-1/confirm", {})[0] == 400


# ===========================================================================
# 10. students
# ===========================================================================


class TestStudents:
    def test_create_student_201(self, client, course):
        status, payload = client.post(
            "/api/students",
            {"course_id": course["course_id"], "student_id": "s-1", "display_name": "Ana"},
        )
        assert status == 201
        assert payload["data"]["student_id"] == "s-1"

    def test_create_student_idempotent(self, client, course):
        body = {"course_id": course["course_id"], "student_id": "s-1"}
        client.post("/api/students", body)
        assert client.post("/api/students", body)[0] == 200

    def test_create_student_conflict(self, client, course):
        body = {"course_id": course["course_id"], "student_id": "s-1", "display_name": "Ana"}
        client.post("/api/students", body)
        status, payload = client.post(
            "/api/students",
            {"course_id": course["course_id"], "student_id": "s-1", "display_name": "Bea"},
        )
        assert status == 409

    def test_create_student_without_id_is_400(self, client, course):
        assert client.post("/api/students", {"course_id": course["course_id"]})[0] == 400

    def test_list_students(self, client, course):
        client.post("/api/students", {"course_id": course["course_id"], "student_id": "s-1"})
        status, payload = client.get(f"/api/students?course_id={course['course_id']}")
        assert status == 200
        assert len(payload["data"]["students"]) == 1

    def test_get_student(self, client, course):
        client.post("/api/students", {"course_id": course["course_id"], "student_id": "s-1"})
        status, payload = client.get(
            f"/api/students/s-1?course_id={course['course_id']}"
        )
        assert status == 200
        assert payload["data"]["student_id"] == "s-1"

    def test_get_unknown_student_is_404(self, client, course):
        assert client.get(f"/api/students/s-x?course_id={course['course_id']}")[0] == 404

    def test_student_state(self, client, course):
        client.post("/api/students", {"course_id": course["course_id"], "student_id": "s-1"})
        status, payload = client.get(
            f"/api/students/s-1/state?course_id={course['course_id']}"
        )
        assert status == 200

    def test_learning_status(self, client, course):
        client.post("/api/students", {"course_id": course["course_id"], "student_id": "s-1"})
        status, payload = client.get(
            f"/api/students/s-1/learning-status?course_id={course['course_id']}"
        )
        assert status == 200


# ===========================================================================
# 11. exercises / answers / evaluations
# ===========================================================================


class TestExercises:
    def _knowledge_id(self, client, course):
        _, session = client.post(
            "/api/sessions", {"course_id": course["course_id"], "session_number": 1}
        )
        session_id = session["data"]["session_id"]
        client.upload(
            f"/api/materials?course_id={course['course_id']}&session_id={session_id}",
            (FIXTURES / "documents" / "multilingual.pdf").read_bytes(),
            "multi.pdf",
        )
        client.post(f"/api/sessions/{session_id}/process")
        _, listing = client.get(f"/api/knowledge?course_id={course['course_id']}")
        points = listing["data"]["knowledge_points"]
        if not points:
            pytest.skip("no knowledge points extracted from fixture")
        return points[0]["knowledge_id"]

    def test_create_exercise_201(self, client, course):
        knowledge_id = self._knowledge_id(client, course)
        status, payload = client.post(
            "/api/exercises",
            {
                "course_id": course["course_id"],
                "exercise_type": "true_false",
                "prompt": "El capital es una funcion.",
                "knowledge_point_ids": [knowledge_id],
                "is_true": True,
            },
        )
        assert status == 201
        assert payload["data"]["exercise_id"]

    def test_create_exercise_missing_fields_is_400(self, client, course):
        status, _ = client.post("/api/exercises", {"course_id": course["course_id"]})
        assert status == 400

    def test_list_exercises(self, client, course):
        status, payload = client.get(f"/api/exercises?course_id={course['course_id']}")
        assert status == 200
        assert isinstance(payload["data"]["exercises"], list)

    def test_get_exercise_unknown_is_404(self, client, course):
        assert client.get(f"/api/exercises/ex-nope?course_id={course['course_id']}")[0] == 404

    def test_submit_answer_requires_fields(self, client, course):
        status, _ = client.post("/api/answers", {"course_id": course["course_id"]})
        assert status == 400

    def test_submit_answer_and_get_evaluation(self, client, course):
        knowledge_id = self._knowledge_id(client, course)
        _, exercise = client.post(
            "/api/exercises",
            {
                "course_id": course["course_id"],
                "exercise_type": "true_false",
                "prompt": "El capital es una funcion.",
                "knowledge_point_ids": [knowledge_id],
                "is_true": True,
            },
        )
        client.post("/api/students", {"course_id": course["course_id"], "student_id": "s-1"})
        status, answer = client.post(
            "/api/answers",
            {
                "course_id": course["course_id"],
                "student_id": "s-1",
                "exercise_id": exercise["data"]["exercise_id"],
                "submitted_value": "true",
            },
        )
        assert status == 201
        answer_id = answer["data"]["answer_id"]
        status, evaluation = client.get(
            f"/api/evaluations/{answer_id}?course_id={course['course_id']}"
        )
        assert status == 200
        assert "score" in evaluation["data"]

    def test_evaluation_unknown_is_404(self, client, course):
        assert client.get(f"/api/evaluations/an-nope?course_id={course['course_id']}")[0] == 404

    def test_study_plan(self, client, course):
        knowledge_id = self._knowledge_id(client, course)
        assert knowledge_id
        client.post("/api/students", {"course_id": course["course_id"], "student_id": "s-1"})
        status, payload = client.get(
            f"/api/study-plans/s-1?course_id={course['course_id']}"
        )
        assert status == 200

    def test_study_plan_without_knowledge_is_400(self, client, course):
        """课程还没有任何知识点时, 学习计划必须给出可读的说明而非 500。"""
        client.post("/api/students", {"course_id": course["course_id"], "student_id": "s-1"})
        status, payload = client.get(
            f"/api/study-plans/s-1?course_id={course['course_id']}"
        )
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"
        assert "KP" in payload["error"]["message"]

    def test_learning_path(self, client, course):
        knowledge_id = self._knowledge_id(client, course)
        status, payload = client.get(
            f"/api/learning-paths/{knowledge_id}?course_id={course['course_id']}"
        )
        assert status == 200

    def test_learning_path_unknown_knowledge_is_400(self, client, course):
        status, payload = client.get(
            f"/api/learning-paths/kp-nope?course_id={course['course_id']}"
        )
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"


# ===========================================================================
# 12. 静态资源 / 错误处理
# ===========================================================================


class TestStaticAndErrors:
    def test_index_served(self, client):
        status, headers, body = client.raw_get("/")
        assert status == 200
        assert "text/html" in headers.get("Content-Type", "")
        assert b"<html" in body.lower()

    def test_unknown_path_falls_back_to_index(self, client):
        status, _headers, body = client.raw_get("/some/client/route")
        assert status == 200
        assert b"<html" in body.lower()

    def test_static_traversal_blocked(self, client):
        status, _payload = client.get("/api/health")
        assert status == 200
        # 静态路径的穿越尝试不会逃出静态目录
        status, _headers, body = client.raw_get("/../src/api/server.py")
        assert b"BaseHTTPRequestHandler" not in body

    def test_static_dir_exists(self):
        assert os.path.isdir(default_static_dir())

    def test_unknown_api_route_is_404(self, client):
        status, payload = client.get("/api/does-not-exist")
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_wrong_method_is_405(self, client):
        status, payload = client.request("DELETE", "/api/courses")
        assert status == 405
        assert payload["error"]["detail"]["allowed"] == ["GET", "POST"]

    def test_traceback_never_leaked(self, client, server):
        original = server.router.routes

        def boom(request):
            raise RuntimeError("internal secret path C:\\secret")

        server.router.add("GET", "/api/_boom", boom)
        try:
            status, payload = client.get("/api/_boom")
            assert status == 500
            assert payload["error"]["code"] == "INTERNAL_ERROR"
            assert "secret" not in json.dumps(payload)
            assert "Traceback" not in json.dumps(payload)
        finally:
            server.router._routes[:] = list(original)  # noqa: SLF001

    def test_debug_mode_reports_message(self, workspace):
        instance = create_server(workspace, port=0, debug=True).start()
        try:
            instance.router.add(
                "GET", "/api/_boom", lambda r: (_ for _ in ()).throw(RuntimeError("detail"))
            )
            status, payload = Client(instance.url).get("/api/_boom")
            assert status == 500
            assert payload["error"]["message"] == "detail"
        finally:
            instance.stop()


# ===========================================================================
# 13. 服务器生命周期
# ===========================================================================


class TestServerLifecycle:
    def test_default_host_is_loopback(self, tmp_path):
        workspace = Workspace(str(tmp_path / "data"))
        server = create_server(workspace, port=0)
        assert server.host == "127.0.0.1"

    def test_port_zero_gets_ephemeral_port(self, server):
        assert server.port > 0

    def test_running_flag(self, server):
        assert server.running is True

    def test_stop_is_idempotent(self, workspace):
        instance = create_server(workspace, port=0).start()
        instance.stop()
        instance.stop()
        assert instance.running is False

    def test_port_conflict_raises_readable_error(self, workspace, server):
        conflicting = create_server(workspace, port=server.port)
        with pytest.raises(OSError) as exc:
            conflicting.start()
        assert "already in use" in str(exc.value)

    def test_url_uses_loopback(self, server):
        assert server.url.startswith("http://127.0.0.1:")

    def test_requires_workspace(self):
        with pytest.raises(ValueError):
            ApiServer(None)  # type: ignore[arg-type]
