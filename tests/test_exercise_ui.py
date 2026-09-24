# -*- coding: utf-8 -*-
"""Task 41 tests: Exercise / Answer / Evaluation UI（完整学习闭环）。

闭环 (spec Task 41)::

    Exercise -> Student Answer -> Evaluation -> Learning State
             -> Study Plan -> Learning Path

验证方式的说明 (重要, 不掩饰限制)
----------------------------------
本机是 Windows, 而浏览器自动化 (agent-browser) **只支持 macOS / Linux**,
因此本任务**没有**浏览器端到端测试。绝不假装做过。

本任务的核心不变量 (每个都至少有一个测试盯着)
----------------------------------------------
1. **提交前绝不泄露答案**。``/api/students/{id}/exercises/{eid}`` 是学生视角,
   提交前不含 ``correct_choice_id`` / ``expected_answer`` / ``fill_blank`` /
   ``explanation``; 提交后答案作为**反馈**出现。
2. **evaluation ≠ fact verification**。评估是"学生这一答对不对", 与"知识是否
   成立"是两件事。响应里显式带 ``is_fact_verification: false`` /
   ``affects_knowledge_base: false``。
3. **答错绝不修改知识库**。validation_status / review_status / knowledge_score /
   证据集 / 溯源链 在答错后逐字节不变。
4. **学习状态只走 Task 30 的规则**。UI 层不实现任何 mastery 算法; 答题
   (answered) 永不推进状态。
5. **提交幂等**。同一份答案 (同 student / exercise / value / sequence) 重提
   返回同一 answer_id 与同一 submitted_at。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from src.api.server import create_server, default_static_dir
from src.application.learning_view import ANSWER_FORMATS
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace
from src.exercises import ExerciseType

from tests.support import dynamic_i18n_prefixes, read_web_source

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXED_TIME = "2026-01-01T00:00:00+00:00"
WEB_DIR = Path(default_static_dir())

#: 提交前必须被隐藏的字段 (与 learning_view._ANSWER_KEY_FIELDS 一致)。
ANSWER_KEY_FIELDS = ("correct_choice_id", "expected_answer", "fill_blank", "explanation")

#: 中日韩表意文字。用于两条断言: 整句界面文案的 key 是中文原文 (47.8),
#: 以及译文表里不得出现中文 (出现即等于没翻)。
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


# ---------------------------------------------------------------------------
# HTTP 客户端 (最小实现; 不依赖第三方库)
# ---------------------------------------------------------------------------


class Client:
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
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body=body, **kw)

    def upload(self, path, content, filename):
        return self.request("POST", path, raw=content, headers={"X-Filename": filename})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


@pytest.fixture
def knowledge(client, course):
    """真实跑一遍流水线, 拿到至少一个 Evidence-backed 知识点。"""
    course_id = course["course_id"]
    _, session = client.post(
        "/api/sessions", {"course_id": course_id, "session_number": 1, "title": "Tema 1"}
    )
    session_id = session["data"]["session_id"]
    status, uploaded = client.upload(
        f"/api/materials?course_id={course_id}&session_id={session_id}",
        (FIXTURES / "documents" / "multilingual.pdf").read_bytes(),
        "multilingual.pdf",
    )
    assert status == 201, uploaded
    client.post(f"/api/sessions/{session_id}/process")
    _, listing = client.get(f"/api/knowledge?course_id={course_id}")
    points = listing["data"]["knowledge_points"]
    assert points, "fixture must produce at least one knowledge point"
    return {
        "course_id": course_id,
        "session_id": session_id,
        "material_id": uploaded["data"]["material_id"],
        "knowledge_ids": [p["knowledge_id"] for p in points],
        "points": points,
    }


@pytest.fixture
def student(client, knowledge):
    status, payload = client.post(
        "/api/students?course_id=" + knowledge["course_id"],
        {"student_id": "stu-ana", "display_name": "Ana"},
    )
    assert status == 201, payload
    return payload["data"]


def create_exercise(client, course_id, kp_id, **body):
    payload = {
        "course_id": course_id,
        "exercise_type": "true_false",
        "prompt": "¿La función es lineal?",
        "knowledge_point_ids": [kp_id],
        "is_true": True,
    }
    payload.update(body)
    status, response = client.post("/api/exercises", payload)
    assert status in (200, 201), response
    return response["data"]


@pytest.fixture
def exercise(client, knowledge):
    return create_exercise(
        client,
        knowledge["course_id"],
        knowledge["knowledge_ids"][0],
        explanation="Porque el grado es uno.",
    )


def exercise_view(client, course_id, student_id, exercise_id):
    return client.get(
        f"/api/students/{student_id}/exercises/{exercise_id}?course_id={course_id}"
    )


def exercise_list(client, course_id, student_id):
    return client.get(f"/api/students/{student_id}/exercises?course_id={course_id}")


def evaluation_view(client, course_id, student_id, exercise_id):
    return client.get(
        f"/api/students/{student_id}/exercises/{exercise_id}/evaluation"
        f"?course_id={course_id}"
    )


def submit(client, course_id, student_id, exercise_id, value, sequence=0):
    return client.post(
        "/api/answers",
        {
            "course_id": course_id,
            "student_id": student_id,
            "exercise_id": exercise_id,
            "submitted_value": value,
            "sequence": sequence,
        },
    )


def read_asset(name: str) -> str:
    """读取前端资源。

    P1-6: 前端已拆分成多个零构建脚本; ``read_asset("app.js")`` 返回按加载
    顺序拼接的**全部前端源码**, 语义与拆分前一致 (见 ``tests/support.py``)。
    """
    return read_web_source(name)


def strip_js_comments(source: str) -> str:
    """去掉 ``//`` 与 ``/* */`` 注释, 只留可执行代码。

    静态断言必须只看代码: 注释里出现 "mastery" 之类的词是**说明**, 不是实现。
    """
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)//[^\n]*", "", without_block)


def kp_snapshot(client, course_id, kp_id):
    status, payload = client.get(f"/api/knowledge/{kp_id}?course_id={course_id}")
    assert status == 200, payload
    return payload["data"]


# ===========================================================================
# 1. 答案泄露防护 (提交前绝不给出答案)
# ===========================================================================


class TestAnswerKeyWithheld:
    def test_exercise_view_hides_every_answer_key_field(
        self, client, knowledge, student, exercise
    ):
        status, payload = exercise_view(
            client, knowledge["course_id"], student["student_id"], exercise["exercise_id"]
        )
        assert status == 200, payload
        view = payload["data"]
        for field in ANSWER_KEY_FIELDS:
            assert field not in view, f"{field} must not be exposed before submission"

    def test_exercise_view_flags_the_withholding_explicitly(
        self, client, knowledge, student, exercise
    ):
        _, payload = exercise_view(
            client, knowledge["course_id"], student["student_id"], exercise["exercise_id"]
        )
        assert payload["data"]["answer_key_withheld"] is True
        assert payload["data"]["answer_key_available"] is False
        assert payload["data"]["answer_key"] is None

    def test_exercise_list_hides_every_answer_key_field(
        self, client, knowledge, student, exercise
    ):
        status, payload = exercise_list(
            client, knowledge["course_id"], student["student_id"]
        )
        assert status == 200
        assert payload["data"]["exercises"]
        for item in payload["data"]["exercises"]:
            for field in ANSWER_KEY_FIELDS:
                assert field not in item, field

    def test_answer_key_is_revealed_only_after_submission(
        self, client, knowledge, student, exercise
    ):
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        _, before = exercise_view(client, course_id, sid, eid)
        assert before["data"]["answer_key"] is None

        status, _ = submit(client, course_id, sid, eid, "true")
        assert status == 201

        _, after = exercise_view(client, course_id, sid, eid)
        assert after["data"]["answer_key_withheld"] is False
        assert after["data"]["answer_key_available"] is True
        assert after["data"]["answer_key"]["correct_choice_id"] == "true"

    def test_answer_key_carries_the_author_supplied_rationale(
        self, client, knowledge, student, exercise
    ):
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        submit(client, course_id, sid, eid, "true")
        _, payload = exercise_view(client, course_id, sid, eid)
        assert payload["data"]["answer_key"]["explanation"] == "Porque el grado es uno."

    def test_the_ui_never_calls_the_authoring_exercise_endpoint(self):
        """UI 必须走学生视角端点。

        ``/api/exercises`` 是**作者/教师**端点, 它的 DTO 带 ``correct_choice_id``
        与 ``expected_answer`` —— 用它渲染题目等于把答案发到浏览器。

        ``/api/exercises/{id}/grounding`` 是一个**独立的只读子资源**, 它只回
        ``course_id / knowledge_points / evidence / materials / unresolved /
        complete / generator_version / template`` —— 没有任何答案字段
        (Task 64.9 的依据链), 所以它是被允许的。

        判据 (只针对 ``api(...)`` 的 URL **实参**, 不做整份源码的子串扫描):

        1. 取每个 ``api(`` 调用的 URL 实参 —— 可能是内联字面量, 也可能是
           在调用点之前赋给 ``const xxxPath`` 的变量;
        2. 把字面量按 ``+`` 拼起来、把 ``encodeURIComponent(...)`` 这类变量
           拼成占位符 ``*``, 还原成"最终路径形状";
        3. 形状以 ``/exercises`` 开头 (作者视角) 时, 只有以 ``/grounding``
           子资源结尾才放行; 其它一律失败。

        这样既保住"答案键不进浏览器"的原始意图, 又允许 Task 64 的只读
        依据链, 还不会把 ``'#/courses/.../exercises/'`` 这类**前端路由
        片段**误判成后端调用。
        """
        import re

        source = read_asset("app.js")

        def _shape(argument: str) -> str:
            """把 URL 实参还原成路径形状: 变量段 -> ``*``, 常量段原样保留。"""
            pieces = [piece.strip() for piece in argument.split("+")]
            out = []
            for piece in pieces:
                if piece.startswith("'") and piece.endswith("'") and len(piece) >= 2:
                    out.append(piece[1:-1])
                else:
                    out.append("*")
            return "".join(out)

        def _first_argument(call_text: str) -> str:
            """取 api(...) 的第一个实参: 从 api( 后扫到第一个顶层逗号。"""
            depth = 0
            for index, char in enumerate(call_text):
                if char in "([{":
                    depth += 1
                elif char in ")]}":
                    depth -= 1
                elif char == "," and depth == 0:
                    return call_text[:index].strip()
            return call_text.strip()

        # 收集 const xxxPath = '...'; 这样 api(xxxPath) 也能解析出形状。
        assignments = {}
        for match in re.finditer(r"const\s+([A-Za-z_$][\w$]*)\s*=\s*([^;]+);", source):
            name, expr = match.group(1), match.group(2).strip()
            if not expr.startswith("'"):
                continue
            assignments[name] = _shape(expr)

        authoring_calls = []
        for match in re.finditer(r"\bapi\(", source):
            argument = _first_argument(source[match.end():])
            if argument.startswith("'"):
                shape = _shape(argument)
            elif argument in assignments:
                shape = assignments[argument]
            else:
                continue
            if not shape.startswith("/exercises"):
                continue
            # 作者视角的 /exercises 根资源: 只有 /grounding 子资源允许。
            if not shape.endswith("/grounding"):
                authoring_calls.append(shape)

        assert authoring_calls == [], (
            "UI must not read the authoring exercise DTO; offending api() calls: %r"
            % authoring_calls
        )
        # 依据链与学生视角端点都必须在。
        assert "/grounding" in source, "UI is expected to show the grounding chain"
        assert "'/students/'" in source

    def test_authoring_endpoint_still_returns_the_key_for_teachers(self, client, exercise):
        """作者/教师端点保持完整 (Task 31 的 DTO), 只是 UI 不用它渲染题目。"""
        status, payload = client.get(
            f"/api/exercises/{exercise['exercise_id']}?course_id={exercise['course_id']}"
        )
        assert status == 200
        assert payload["data"]["correct_choice_id"] == "true"


# ===========================================================================
# 2. Exercise UI 要求显示的内容
# ===========================================================================


class TestExerciseViewContent:
    def test_view_shows_prompt_and_type(self, client, knowledge, student, exercise):
        _, payload = exercise_view(
            client, knowledge["course_id"], student["student_id"], exercise["exercise_id"]
        )
        view = payload["data"]
        assert view["prompt"] == "¿La función es lineal?"
        assert view["exercise_type"] == ExerciseType.TRUE_FALSE.value

    def test_view_shows_linked_knowledge_points(self, client, knowledge, student, exercise):
        _, payload = exercise_view(
            client, knowledge["course_id"], student["student_id"], exercise["exercise_id"]
        )
        view = payload["data"]
        assert view["knowledge_point_ids"] == [knowledge["knowledge_ids"][0]]
        assert view["knowledge_points"]
        assert view["knowledge_points"][0]["knowledge_id"] == knowledge["knowledge_ids"][0]
        assert view["knowledge_points"][0]["title"]

    def test_view_shows_prerequisites(self, client, workspace, knowledge, student):
        kp_ids = knowledge["knowledge_ids"]
        if len(kp_ids) < 2:
            pytest.skip("fixture produced only one knowledge point")
        first, second = kp_ids[0], kp_ids[1]
        org = workspace.context(knowledge["course_id"]).org_service
        org.add_relation(first, second, "prerequisite")
        exercise = create_exercise(client, knowledge["course_id"], second)
        _, payload = exercise_view(
            client, knowledge["course_id"], student["student_id"], exercise["exercise_id"]
        )
        assert first in payload["data"]["prerequisites"]

    def test_view_shows_the_evidence_the_question_is_based_on(
        self, client, knowledge, student, exercise
    ):
        _, payload = exercise_view(
            client, knowledge["course_id"], student["student_id"], exercise["exercise_id"]
        )
        view = payload["data"]
        assert view["evidence"], "exercise must expose its grounding evidence"
        for item in view["evidence"]:
            assert item["evidence_id"]
            assert item["content"]
            assert item["source"]["material_id"]

    def test_view_reports_evidence_completeness(self, client, knowledge, student, exercise):
        _, payload = exercise_view(
            client, knowledge["course_id"], student["student_id"], exercise["exercise_id"]
        )
        view = payload["data"]
        assert view["evidence_complete"] is True
        assert view["unresolved_evidence_ids"] == []

    def test_view_reports_broken_evidence_instead_of_hiding_it(
        self, client, knowledge, student, exercise
    ):
        """练习引用了一个不存在的 evidence_id -> 必须显式暴露断链。"""
        broken = create_exercise(
            client,
            knowledge["course_id"],
            knowledge["knowledge_ids"][0],
            evidence_ids=["evidence-does-not-exist"],
        )
        _, payload = exercise_view(
            client, knowledge["course_id"], student["student_id"], broken["exercise_id"]
        )
        view = payload["data"]
        assert view["evidence_complete"] is False
        assert view["unresolved_evidence_ids"] == ["evidence-does-not-exist"]

    def test_true_false_offers_two_choices_without_revealing_the_key(
        self, client, knowledge, student, exercise
    ):
        _, payload = exercise_view(
            client, knowledge["course_id"], student["student_id"], exercise["exercise_id"]
        )
        view = payload["data"]
        assert view["answer_format"] == "true_false"
        assert [c["choice_id"] for c in view["choices"]] == ["true", "false"]
        assert all(c["text"] for c in view["choices"])

    def test_multiple_choice_exposes_all_options(self, client, knowledge, student):
        exercise = create_exercise(
            client,
            knowledge["course_id"],
            knowledge["knowledge_ids"][0],
            exercise_type="multiple_choice",
            prompt="¿Cuál es el grado?",
            choices=[{"choice_id": "c1", "text": "Uno"}, {"choice_id": "c2", "text": "Dos"}],
            correct_choice_id="c1",
        )
        _, payload = exercise_view(
            client, knowledge["course_id"], student["student_id"], exercise["exercise_id"]
        )
        view = payload["data"]
        assert view["answer_format"] == "choice"
        assert [c["choice_id"] for c in view["choices"]] == ["c1", "c2"]

    def test_text_answer_types_use_the_text_format(self, client, knowledge, student):
        for exercise_type, extra in (
            ("short_answer", {"expected_answer": "uno"}),
            ("fill_blank", {"blank_id": "b1", "accepted_answers": ["uno"]}),
        ):
            exercise = create_exercise(
                client,
                knowledge["course_id"],
                knowledge["knowledge_ids"][0],
                exercise_type=exercise_type,
                prompt=f"Pregunta {exercise_type}",
                **extra,
            )
            _, payload = exercise_view(
                client, knowledge["course_id"], student["student_id"], exercise["exercise_id"]
            )
            assert payload["data"]["answer_format"] == "text", exercise_type

    def test_answer_format_map_covers_every_exercise_type(self):
        for exercise_type in ExerciseType:
            assert exercise_type.value in ANSWER_FORMATS, exercise_type

    def test_unknown_exercise_is_not_found(self, client, knowledge, student):
        status, payload = exercise_view(
            client, knowledge["course_id"], student["student_id"], "exercise-nope"
        )
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_unknown_student_is_not_found(self, client, knowledge, exercise):
        status, payload = exercise_view(
            client, knowledge["course_id"], "stu-nobody", exercise["exercise_id"]
        )
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_missing_course_id_is_invalid_input(self, client, student, exercise):
        status, payload = client.get(
            f"/api/students/{student['student_id']}/exercises/{exercise['exercise_id']}"
        )
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"


# ===========================================================================
# 3. Answer 保存与幂等
# ===========================================================================


class TestAnswerPersistence:
    def test_answer_saves_all_four_required_fields(
        self, client, knowledge, student, exercise
    ):
        """spec: student_id / exercise_id / answer / submitted_at。"""
        status, payload = submit(
            client, knowledge["course_id"], student["student_id"],
            exercise["exercise_id"], "true",
        )
        assert status == 201, payload
        answer = payload["data"]
        assert answer["student_id"] == student["student_id"]
        assert answer["exercise_id"] == exercise["exercise_id"]
        assert answer["submitted_value"] == "true"
        assert answer["submitted_at"] == FIXED_TIME

    def test_answer_is_idempotent(self, client, knowledge, student, exercise):
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        first = submit(client, course_id, sid, eid, "true")[1]["data"]
        second = submit(client, course_id, sid, eid, "true")[1]["data"]
        assert second["answer_id"] == first["answer_id"]
        assert second["submitted_at"] == first["submitted_at"]
        assert second["evaluation_id"] == first["evaluation_id"]

    def test_resubmitting_does_not_duplicate_the_answer_log(
        self, client, knowledge, student, exercise
    ):
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        for _ in range(3):
            submit(client, course_id, sid, eid, "true")
        _, payload = exercise_list(client, course_id, sid)
        items = [i for i in payload["data"]["exercises"] if i["exercise_id"] == eid]
        assert len(items) == 1
        assert payload["data"]["answered"] == 1

    def test_answer_is_visible_through_the_view(self, client, knowledge, student, exercise):
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        submitted = submit(client, course_id, sid, eid, "true")[1]["data"]
        _, payload = exercise_view(client, course_id, sid, eid)
        view = payload["data"]
        assert view["submitted"] is True
        assert view["answer_id"] == submitted["answer_id"]
        assert view["submitted_value"] == "true"
        assert view["submitted_at"] == FIXED_TIME

    def test_a_later_attempt_is_a_distinct_answer(self, client, knowledge, student, exercise):
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        first = submit(client, course_id, sid, eid, "true", sequence=0)[1]["data"]
        second = submit(client, course_id, sid, eid, "false", sequence=1)[1]["data"]
        assert second["answer_id"] != first["answer_id"]
        _, payload = exercise_view(client, course_id, sid, eid)
        # 视图展示的是最新一次 (sequence 更大)
        assert payload["data"]["submitted_value"] == "false"
        assert payload["data"]["sequence"] == 1

    def test_answer_does_not_leak_into_another_student(
        self, client, knowledge, student, exercise
    ):
        course_id = knowledge["course_id"]
        client.post(
            "/api/students?course_id=" + course_id,
            {"student_id": "stu-bob", "display_name": "Bob"},
        )
        submit(client, course_id, student["student_id"], exercise["exercise_id"], "true")
        _, payload = exercise_view(client, course_id, "stu-bob", exercise["exercise_id"])
        assert payload["data"]["submitted"] is False
        assert payload["data"]["answer_key"] is None

    def test_unknown_exercise_cannot_be_answered(self, client, knowledge, student):
        status, payload = submit(
            client, knowledge["course_id"], student["student_id"], "exercise-nope", "true"
        )
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_answer_for_unknown_student_is_rejected(self, client, knowledge, exercise):
        status, payload = submit(
            client, knowledge["course_id"], "stu-nobody", exercise["exercise_id"], "true"
        )
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_missing_fields_are_invalid_input(self, client, knowledge, student, exercise):
        for missing in ("student_id", "exercise_id", "submitted_value"):
            body = {
                "course_id": knowledge["course_id"],
                "student_id": student["student_id"],
                "exercise_id": exercise["exercise_id"],
                "submitted_value": "true",
            }
            del body[missing]
            status, payload = client.post("/api/answers", body)
            assert status == 400, missing
            assert payload["error"]["code"] == "INVALID_INPUT", missing


# ===========================================================================
# 4. Evaluation 显示 + evaluation ≠ fact verification
# ===========================================================================


class TestEvaluationDisplay:
    def test_evaluation_shows_all_five_required_fields(
        self, client, knowledge, student, exercise
    ):
        """spec: score / status / feedback / knowledge_point / evidence。"""
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        submit(client, course_id, sid, eid, "true")
        status, payload = evaluation_view(client, course_id, sid, eid)
        assert status == 200, payload
        evaluation = payload["data"]["evaluation"]
        assert evaluation["score"] == 1.0
        assert evaluation["status"] == "correct"
        assert "feedback" in evaluation
        assert evaluation["knowledge_point_ids"] == [knowledge["knowledge_ids"][0]]
        assert evaluation["knowledge_points"]
        assert evaluation["evidence"]

    def test_evaluation_declares_it_is_not_fact_verification(
        self, client, knowledge, student, exercise
    ):
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        submit(client, course_id, sid, eid, "true")
        _, payload = evaluation_view(client, course_id, sid, eid)
        data = payload["data"]
        assert data["is_fact_verification"] is False
        assert data["affects_knowledge_base"] is False
        assert data["evaluation"]["is_fact_verification"] is False
        assert data["evaluation"]["affects_knowledge_base"] is False
        assert "not a fact verification" in data["note"]

    def test_correct_answer_is_graded_correct(self, client, knowledge, student, exercise):
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        submit(client, course_id, sid, eid, "true")
        _, payload = evaluation_view(client, course_id, sid, eid)
        assert payload["data"]["evaluation"]["status"] == "correct"
        assert payload["data"]["evaluation"]["score"] == 1.0

    def test_wrong_answer_is_graded_incorrect(self, client, knowledge, student, exercise):
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        submit(client, course_id, sid, eid, "false")
        _, payload = evaluation_view(client, course_id, sid, eid)
        assert payload["data"]["evaluation"]["status"] == "incorrect"
        assert payload["data"]["evaluation"]["score"] == 0.0

    def test_evaluation_reveals_the_reference_answer_as_feedback(
        self, client, knowledge, student, exercise
    ):
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        submit(client, course_id, sid, eid, "false")
        _, payload = evaluation_view(client, course_id, sid, eid)
        expected = payload["data"]["evaluation"]["expected"]
        assert expected["correct_choice_id"] == "true"

    def test_evaluation_carries_the_evaluator_version(
        self, client, knowledge, student, exercise
    ):
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        submit(client, course_id, sid, eid, "true")
        _, payload = evaluation_view(client, course_id, sid, eid)
        assert payload["data"]["evaluation"]["evaluator_version"]

    def test_evaluation_before_submission_is_not_found(
        self, client, knowledge, student, exercise
    ):
        status, payload = evaluation_view(
            client, knowledge["course_id"], student["student_id"], exercise["exercise_id"]
        )
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"
        assert "no answer submitted" in payload["error"]["message"]

    def test_short_answer_is_exact_match_only(self, client, knowledge, student):
        """Task 32.8: 自由文本**绝不做语义判分** —— 逐字相等才判对。"""
        exercise = create_exercise(
            client,
            knowledge["course_id"],
            knowledge["knowledge_ids"][0],
            exercise_type="short_answer",
            prompt="Explica el grado",
            expected_answer="uno",
        )
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        submit(client, course_id, sid, eid, "uno")
        _, payload = evaluation_view(client, course_id, sid, eid)
        assert payload["data"]["evaluation"]["status"] == "correct"

    def test_short_answer_never_guesses_semantic_equivalence(
        self, client, knowledge, student
    ):
        """语义等价的答案也不能判对 (spec: "Madrid" vs "马德里" 永不相等)。"""
        exercise = create_exercise(
            client,
            knowledge["course_id"],
            knowledge["knowledge_ids"][0],
            exercise_type="short_answer",
            prompt="¿Cuál es el grado?",
            expected_answer="uno",
        )
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        submit(client, course_id, sid, eid, "Uno")  # 只是大小写不同
        _, payload = evaluation_view(client, course_id, sid, eid)
        assert payload["data"]["evaluation"]["status"] == "unsupported"
        assert payload["data"]["evaluation"]["score"] == 0.0
        assert payload["data"]["evaluation"]["feedback"]

    def test_short_answer_without_expected_answer_is_rejected_at_creation(
        self, client, knowledge
    ):
        """没有参考答案的 short_answer 无法判分, 因此在创建时就被拒绝 ——
        不能让一个注定 unsupported 的题目进入题库。"""
        status, payload = client.post(
            "/api/exercises",
            {
                "course_id": knowledge["course_id"],
                "exercise_type": "short_answer",
                "prompt": "Pregunta abierta",
                "knowledge_point_ids": [knowledge["knowledge_ids"][0]],
            },
        )
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"
        assert "expected_answer" in payload["error"]["message"]

    def test_fill_blank_uses_explicit_accepted_answers(self, client, knowledge, student):
        exercise = create_exercise(
            client,
            knowledge["course_id"],
            knowledge["knowledge_ids"][0],
            exercise_type="fill_blank",
            prompt="El grado es ___",
            blank_id="b1",
            accepted_answers=["uno"],
        )
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        submit(client, course_id, sid, eid, "uno")
        _, payload = evaluation_view(client, course_id, sid, eid)
        assert payload["data"]["evaluation"]["status"] == "correct"

    def test_evaluation_is_deterministic(self, client, knowledge, student, exercise):
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        submit(client, course_id, sid, eid, "true")
        _, first = evaluation_view(client, course_id, sid, eid)
        _, second = evaluation_view(client, course_id, sid, eid)
        assert first == second


# ===========================================================================
# 5. 答错绝不修改知识库 (核心不变量)
# ===========================================================================


class TestWrongAnswerNeverTouchesKnowledgeBase:
    def test_wrong_answer_leaves_the_kp_untouched(self, client, knowledge, student, exercise):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        before = kp_snapshot(client, course_id, kp_id)
        submit(client, course_id, student["student_id"], exercise["exercise_id"], "false")
        after = kp_snapshot(client, course_id, kp_id)
        assert after == before

    def test_wrong_answer_does_not_change_review_status(
        self, client, knowledge, student, exercise
    ):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        before = kp_snapshot(client, course_id, kp_id)["review_status"]
        submit(client, course_id, student["student_id"], exercise["exercise_id"], "false")
        after = kp_snapshot(client, course_id, kp_id)["review_status"]
        assert after == before

    def test_wrong_answer_does_not_add_evidence(self, client, knowledge, student, exercise):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        _, before = client.get(f"/api/knowledge/{kp_id}/evidence?course_id={course_id}")
        submit(client, course_id, student["student_id"], exercise["exercise_id"], "false")
        _, after = client.get(f"/api/knowledge/{kp_id}/evidence?course_id={course_id}")
        assert after == before

    def test_wrong_answer_does_not_break_traceability(
        self, client, knowledge, student, exercise
    ):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        _, before = client.get(f"/api/knowledge/{kp_id}/trace?course_id={course_id}")
        submit(client, course_id, student["student_id"], exercise["exercise_id"], "false")
        _, after = client.get(f"/api/knowledge/{kp_id}/trace?course_id={course_id}")
        assert after["data"]["complete"] is True
        assert after["data"]["links"] == before["data"]["links"]

    def test_repeated_wrong_answers_never_create_knowledge_points(
        self, client, knowledge, student, exercise
    ):
        course_id = knowledge["course_id"]
        _, before = client.get(f"/api/knowledge?course_id={course_id}")
        for sequence in range(4):
            submit(
                client, course_id, student["student_id"],
                exercise["exercise_id"], "false", sequence=sequence,
            )
        _, after = client.get(f"/api/knowledge?course_id={course_id}")
        assert len(after["data"]["knowledge_points"]) == len(
            before["data"]["knowledge_points"]
        )

    def test_wrong_answer_does_not_open_a_review_candidate(
        self, client, knowledge, student, exercise
    ):
        course_id = knowledge["course_id"]
        _, before = client.get(f"/api/reviews?course_id={course_id}")
        submit(client, course_id, student["student_id"], exercise["exercise_id"], "false")
        _, after = client.get(f"/api/reviews?course_id={course_id}")
        assert after == before

    def test_wrong_answer_does_not_change_the_grounded_explanation(
        self, client, knowledge, student, exercise
    ):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        _, before = client.get(
            f"/api/knowledge/{kp_id}/explanation?course_id={course_id}&language=es"
        )
        submit(client, course_id, student["student_id"], exercise["exercise_id"], "false")
        _, after = client.get(
            f"/api/knowledge/{kp_id}/explanation?course_id={course_id}&language=es"
        )
        assert after == before

    def test_evaluation_is_recorded_against_the_answer_not_the_kp(
        self, client, knowledge, student, exercise
    ):
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        answer = submit(client, course_id, sid, eid, "false")[1]["data"]
        _, payload = evaluation_view(client, course_id, sid, eid)
        assert payload["data"]["evaluation"]["answer_id"] == answer["answer_id"]
        assert payload["data"]["answer_id"] == answer["answer_id"]
        # 评估 id 与知识点 id 是不同的命名空间
        assert payload["data"]["evaluation"]["evaluation_id"].startswith("evaluation-")


# ===========================================================================
# 6. 学习状态只能走 Task 30-33 的规则
# ===========================================================================


class TestLearningStateFollowsTask30:
    def test_answering_does_not_advance_the_learning_state(
        self, client, knowledge, student, exercise
    ):
        """答题是评估事实; Task 30 的 ANSWERED 事件不是状态转移。"""
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        submit(client, course_id, sid, exercise["exercise_id"], "true")
        status, payload = client.get(
            f"/api/students/{sid}/learning-paths/{kp_id}?course_id={course_id}"
        )
        assert status == 200
        node = next(
            n for n in payload["data"]["nodes"] if n["knowledge_point_id"] == kp_id
        )
        assert node["state"] == "not_started"
        assert node["next_event"] == "viewed"

    def test_state_advances_only_through_explicit_events(
        self, client, knowledge, student, exercise
    ):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        submit(client, course_id, sid, exercise["exercise_id"], "true")
        for event, expected in (("viewed", "exposed"), ("practiced", "practicing"),
                                ("reviewed", "reviewing")):
            status, payload = client.post(
                f"/api/students/{sid}/learning-events?course_id={course_id}",
                {"knowledge_id": kp_id, "event_type": event},
            )
            assert status == 200, payload
            assert payload["data"]["state"] == expected

    def test_answering_does_record_practice_activity(
        self, client, knowledge, student, exercise
    ):
        """状态不推进, 但活动计数必须真实反映"答过题"。"""
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        submit(client, course_id, sid, exercise["exercise_id"], "true")
        _, payload = client.get(
            f"/api/students/{sid}/learning-paths/{kp_id}?course_id={course_id}"
        )
        node = next(
            n for n in payload["data"]["nodes"] if n["knowledge_point_id"] == kp_id
        )
        assert node["activity"]["answer_count"] >= 1

    def test_wrong_answer_marks_the_kp_as_recent_incorrect(
        self, client, knowledge, student, exercise
    ):
        """Task 30/32 已有的事实: 答错进 recent_incorrect, 用于学习计划。"""
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        submit(client, course_id, sid, exercise["exercise_id"], "false")
        _, payload = client.get(f"/api/students/{sid}/dashboard?course_id={course_id}")
        assert kp_id in payload["data"]["progress"]["recent_incorrect_kps"]
        reasons = [
            gap["reason"] for gap in payload["data"]["knowledge_gaps"]["student_gaps"]
        ]
        assert "STUDENT_RECENT_INCORRECT" in reasons

    def test_the_study_plan_reflects_the_wrong_answer(
        self, client, knowledge, student, exercise
    ):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        submit(client, course_id, sid, exercise["exercise_id"], "false")
        _, payload = client.get(f"/api/students/{sid}/dashboard?course_id={course_id}")
        items = {
            item["knowledge_point_id"]: item
            for item in payload["data"]["study_plan"]["items"]
        }
        assert kp_id in items

    def test_learning_path_links_exercise_and_evaluation(
        self, client, knowledge, student, exercise
    ):
        """闭环: Exercise -> Answer -> Evaluation 必须出现在路径节点上。"""
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        submit(client, course_id, sid, exercise["exercise_id"], "true")
        _, payload = client.get(
            f"/api/students/{sid}/learning-paths/{kp_id}?course_id={course_id}"
        )
        node = next(
            n for n in payload["data"]["nodes"] if n["knowledge_point_id"] == kp_id
        )
        assert exercise["exercise_id"] in [
            e["exercise_id"] for e in node["exercises"]
        ]
        assert node["evaluations"], "evaluation must be reachable from the path"
        assert node["evaluations"][0]["status"] == "correct"

    def test_student_dashboard_reflects_the_full_loop(
        self, client, knowledge, student, exercise
    ):
        course_id, sid = knowledge["course_id"], student["student_id"]
        submit(client, course_id, sid, exercise["exercise_id"], "true")
        _, payload = client.get(f"/api/students/{sid}/dashboard?course_id={course_id}")
        data = payload["data"]
        assert data["progress"]["answered_count"] >= 1
        assert data["progress"]["average_score"] == 1.0
        assert data["recent_evaluations"]
        assert data["recent_evaluations"][0]["submitted_at"] == FIXED_TIME
        # 已作答 -> 不再出现在待作答里
        assert exercise["exercise_id"] not in [
            item["exercise_id"] for item in data["pending_exercises"]
        ]

    def test_no_mastery_algorithm_in_the_ui_layer(self):
        """UI 层不得自造掌握度: 只允许展示 Task 30 的状态与 Task 32 的评估。"""
        source = strip_js_comments(read_asset("app.js"))
        for forbidden in ("mastery", "proficiency", "estimatedAbility", "predicted"):
            assert forbidden not in source, forbidden
        # 作答必须提交给后端 (Task 32), 不能在前端比对答案
        assert "api('/answers'" in source
        assert "submitted_value === " not in source
        assert "correct_choice_id ===" not in source
        assert "expected_answer ===" not in source


# ===========================================================================
# 7. 练习列表
# ===========================================================================


class TestExerciseList:
    def test_list_reports_totals(self, client, knowledge, student, exercise):
        create_exercise(
            client, knowledge["course_id"], knowledge["knowledge_ids"][0],
            prompt="Segunda pregunta",
        )
        status, payload = exercise_list(client, knowledge["course_id"], student["student_id"])
        assert status == 200
        data = payload["data"]
        assert data["total"] == 2
        assert data["answered"] == 0
        assert data["unanswered"] == 2
        assert data["answered"] + data["unanswered"] == data["total"]

    def test_list_marks_answered_exercises(self, client, knowledge, student, exercise):
        course_id, sid = knowledge["course_id"], student["student_id"]
        submit(client, course_id, sid, exercise["exercise_id"], "true")
        _, payload = exercise_list(client, course_id, sid)
        data = payload["data"]
        assert data["answered"] == 1
        item = next(
            i for i in data["exercises"] if i["exercise_id"] == exercise["exercise_id"]
        )
        assert item["submitted"] is True
        assert item["evaluation_status"] == "correct"
        assert item["score"] == 1.0
        assert item["submitted_at"] == FIXED_TIME

    def test_list_is_sorted_deterministically(self, client, knowledge, student):
        for index in range(3):
            create_exercise(
                client, knowledge["course_id"], knowledge["knowledge_ids"][0],
                prompt=f"Pregunta {index}",
            )
        _, payload = exercise_list(client, knowledge["course_id"], student["student_id"])
        ids = [item["exercise_id"] for item in payload["data"]["exercises"]]
        assert ids == sorted(ids)

    def test_list_includes_knowledge_points_and_prerequisites(
        self, client, knowledge, student, exercise
    ):
        _, payload = exercise_list(client, knowledge["course_id"], student["student_id"])
        item = payload["data"]["exercises"][0]
        assert item["knowledge_points"]
        assert "prerequisites" in item

    def test_empty_course_returns_an_empty_list(self, client, course, student):
        """学生属于课程 A, 课程 B 没有练习 -> 空列表而不是错误。"""
        status, payload = exercise_list(client, course["course_id"], student["student_id"])
        assert status == 200
        assert payload["data"]["exercises"] == []
        assert payload["data"]["total"] == 0

    def test_unknown_student_is_not_found(self, client, knowledge):
        status, payload = exercise_list(client, knowledge["course_id"], "stu-nobody")
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_list_is_deterministic(self, client, knowledge, student, exercise):
        _, first = exercise_list(client, knowledge["course_id"], student["student_id"])
        _, second = exercise_list(client, knowledge["course_id"], student["student_id"])
        assert first == second


# ===========================================================================
# 8. UI 契约 (静态断言; 不声称执行了 DOM 事件)
# ===========================================================================


class TestUiContract:
    def test_app_js_calls_the_student_exercise_endpoints(self):
        """学生页只打一个请求: 单题视图 (评估结果内嵌其中), 避免 N+1。"""
        source = read_asset("app.js")
        assert "'/students/'" in source
        assert "'/exercises'" in source
        assert "'/exercises/'" in source

    def test_app_js_reads_the_evaluation_from_the_exercise_view(self):
        source = read_asset("app.js")
        assert "view.evaluation" in source or "evaluation.expected" in source

    def test_app_js_submits_answers_to_the_task32_endpoint(self):
        source = read_asset("app.js")
        assert "api('/answers'" in source

    def test_exercise_routes_are_registered(self):
        source = read_asset("app.js")
        assert "parts[0] === 'exercises'" in source
        assert "parts[2] === 'exercises'" in source
        assert "pageExercise(" in source
        assert "pageExercises(" in source

    def test_index_html_nav_exposes_exercises(self):
        # 2026-09-22: 练习入口暂时禁用 —— 仍显示文字但改为无 href 的禁用 span
        # (不可点击、不触发路由), 路由分支与页面函数原样保留。重启用时换回 <a>。
        html = read_asset("index.html")
        assert 'data-i18n="nav.exercises"' in html
        assert 'href="#/exercises"' not in html
        assert 'class="nav-disabled"' in html

    def test_app_js_renders_the_evaluation_panel_with_an_explicit_warning(self):
        source = read_asset("app.js")
        assert "renderEvaluationPanel" in source
        assert "ex.notFactVerification" in source

    def test_app_js_renders_the_answer_key_only_after_submission(self):
        source = read_asset("app.js")
        assert "answer_key_withheld" in source
        assert "evaluation.expected" in source

    def test_app_js_renders_type_aware_answer_controls(self):
        source = read_asset("app.js")
        assert "answer_format" in source
        assert "name=\"answer_value\"" in source
        assert "input type=\"radio\"" in source or 'type="radio"' in source


class TestUiApiFieldContract:
    """自动漂移检测: app.js 读取的每个字段都必须真实存在于 API 响应里。

    这比手写字段清单更耐用 —— JS 改了字段名而 API 没跟上 (或反过来) 会立刻失败。
    """

    @staticmethod
    def _ui_region() -> str:
        source = read_asset("app.js")
        start = source.index("async function pageExercise(")
        end = source.index("function wireAnswerForm(")
        return source[start:end]

    def test_every_view_field_the_ui_reads_exists_in_the_api(
        self, client, knowledge, student, exercise
    ):
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        submit(client, course_id, sid, eid, "true")  # 提交后字段最全
        _, payload = exercise_view(client, course_id, sid, eid)
        view = payload["data"]
        fields = set(re.findall(r"\bview\.([a-zA-Z_][a-zA-Z0-9_]*)", self._ui_region()))
        assert fields, "UI region must reference the view object"
        assert not sorted(f for f in fields if f not in view)

    def test_every_evaluation_field_the_ui_reads_exists_in_the_api(
        self, client, knowledge, student, exercise
    ):
        course_id, sid = knowledge["course_id"], student["student_id"]
        eid = exercise["exercise_id"]
        submit(client, course_id, sid, eid, "true")
        _, payload = exercise_view(client, course_id, sid, eid)
        evaluation = payload["data"]["evaluation"]
        assert evaluation is not None
        fields = set(
            re.findall(r"\bevaluation\.([a-zA-Z_][a-zA-Z0-9_]*)", self._ui_region())
        )
        assert fields, "UI region must reference the evaluation object"
        assert not sorted(f for f in fields if f not in evaluation)

    def test_the_ui_reads_the_five_required_evaluation_fields(self):
        """spec Task 41: score / status / feedback / knowledge_point / evidence。"""
        region = self._ui_region()
        for field in ("score", "status", "feedback", "knowledge_points", "evidence"):
            assert field in region, field


class TestUiRenderExecution:
    """真实**执行** app.js 的页面函数 (Node + 最小 DOM 桩)。

    这不是浏览器测试: 它不解析 CSS、不触发真实事件、不做端到端导航。
    它证明的是别的东西 —— 给定真实形状的 API 响应, 页面函数能跑通 (抓模板
    运行时错误), 且**渲染出来的 HTML 里没有答案** (抓泄露, 比静态断言强)。
    """

    @staticmethod
    def _node() -> str:
        candidates = [
            os.environ.get("CLASSROOM_NODE"),
            "node",
            r"C:/Users/Rafae/.workbuddy-ai/binaries/node/versions/22.22.2-2/node.exe",
            r"C:/Program Files/nodejs/node.exe",
        ]
        for candidate in candidates:
            if not candidate:
                continue
            found = shutil.which(candidate) if not os.path.isabs(candidate) else (
                candidate if Path(candidate).exists() else None
            )
            if found:
                return found
        pytest.skip("node runtime not available for the render harness")

    def test_ui_render_harness_passes(self):
        script = Path(__file__).resolve().parent.parent / "scripts" / "ui_render_check.js"
        assert script.exists(), "render harness must exist"
        completed = subprocess.run(
            [self._node(), str(script)],
            capture_output=True,
            text=True,
            cwd=str(script.parent.parent),
            timeout=120,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        assert completed.returncode == 0, output
        assert "UI RENDER CHECK: OK" in output

    def test_render_harness_actually_checks_the_answer_is_hidden(self):
        """确认 harness 真的在断言"答案没被渲染", 而不是空跑。"""
        script = Path(__file__).resolve().parent.parent / "scripts" / "ui_render_check.js"
        source = script.read_text(encoding="utf-8")
        assert "SECRET_ANSWER" in source
        assert "answer is NOT in the rendered HTML" in source
        assert "explanation is NOT in the rendered HTML" in source

    def test_render_harness_covers_all_three_pages(self):
        script = Path(__file__).resolve().parent.parent / "scripts" / "ui_render_check.js"
        source = script.read_text(encoding="utf-8")
        for page in ("pageExercise", "pageExercises", "pageStudent"):
            assert page in source, page


class TestI18nTables:
    """两层界面文案机制 (Task 47.8)。

    ``app.js`` 的 ``t()`` 有两层:

    1. ``I18N[lang]`` —— **可枚举项**的符号 key (``state.*`` / ``status.*`` /
       ``nav.*`` / ``ex.*`` ...), 代码里本来就有符号名;
    2. ``TRANSLATIONS[lang]`` —— **整句界面文案**, key 就是中文原文 (中文是
       基准语言, 代码里写的就是中文)。

    第 2 层存在的理由: 整句话没有"名字", 硬起一个符号 key 只会多一层需要同步
    维护的间接层。直接用原文当 key 之后, **漏译是可测的** —— 某个页面忘了翻,
    下面的 ``test_sentence_keys_are_translated_in_both_languages`` 就会亮。
    """

    @staticmethod
    def _tables():
        source = read_asset("app.js")
        start = source.index("const I18N = {")
        end = source.index("\n};", start)
        block = source[start:end]
        tables = {}
        for lang in ("zh", "es", "ca"):
            s = block.index(f"  {lang}: {{")
            e = block.index("\n  },", s)
            tables[lang] = set(re.findall(r"'([A-Za-z0-9_.]+)'\s*:", block[s:e]))
        return source, tables

    @staticmethod
    def _translations():
        """解析 ``TRANSLATIONS`` 的 es / ca 两张表 (key 是中文原文)。"""
        source = read_asset("app.js")
        start = source.index("const TRANSLATIONS = {")
        end = source.index("\n};", start)
        block = source[start:end]
        tables = {}
        for lang in ("es", "ca"):
            s = block.index(f"  {lang}: {{")
            e = block.index("\n  },", s)
            body = block[s:e]
            tables[lang] = {
                match.group(1).replace("\\'", "'").replace("\\\\", "\\")
                for match in re.finditer(r"^\s*'((?:[^'\\]|\\.)*)':", body, re.M)
            }
        return tables

    @staticmethod
    def _used_keys(source):
        return set(re.findall(r"\bt\(\s*'([^']+)'", source))

    #: ``t('prefix.' + variable)`` 形式的动态 key。前缀本身不是 key,
    #: 真实 key = 前缀 + 枚举值字符串。取值域由
    #: ``support.dynamic_i18n_prefixes()`` **从后端常量导入** (2026-09-21,
    #: P3-4) —— 手抄的取值域在后端新增枚举时不会自己长大, 于是测试依然绿
    #: 而页面上多出一个没译文的 key。同一张表在 ``test_learning_view`` 里
    #: 也有一份检查, 两处共用 support 里那**一个**定义。
    @classmethod
    def _dynamic_prefixes(cls) -> dict:
        return dynamic_i18n_prefixes()

    @classmethod
    def _expand_dynamic_keys(cls, used):
        expanded: set = set()
        prefixes: set = set()
        for key in used:
            for prefix, suffixes in cls._dynamic_prefixes().items():
                if key == prefix:
                    prefixes.add(prefix)
                    expanded.update(prefix + s for s in suffixes)
        return expanded, prefixes

    def test_all_three_tables_have_identical_keys(self):
        _, tables = self._tables()
        assert tables["zh"] == tables["es"] == tables["ca"]
        assert len(tables["zh"]) > 80

    def test_every_t_key_is_defined(self):
        """每个 ``t('...')`` 都必须能被**某一层**解析, 不能有悬空 key。

        动态前缀先按其取值域展开再校验 —— 既不误报前缀, 也不放过
        真正的悬空 key。
        """
        source, tables = self._tables()
        translations = self._translations()
        used = self._used_keys(source)
        dynamic = self._dynamic_prefixes()
        expanded, _ = self._expand_dynamic_keys(used)
        checked = (used - set(dynamic)) | expanded
        unknown = sorted(
            checked - tables["zh"] - translations["es"] - translations["ca"]
        )
        assert not unknown, unknown

    def test_sentence_keys_are_translated_in_both_languages(self):
        """整句界面文案 (key = 中文原文) 在 es / ca 里必须都有译文。

        这是 47.8 的核心断言。修复前的实测情况: 语言选择器只对 Task 40/41 的
        学生页 / 练习页生效, Task 39 的 7 个页面 (概览 / 知识点 / 材料 / 待审核 /
        课程 / 课堂 / 溯源详情) 正文全是硬编码中文 —— 选了 Español 也只换掉顶栏。
        """
        source, _ = self._tables()
        translations = self._translations()
        used = {key for key in self._used_keys(source) if CJK.search(key)}
        assert used, "sentence-level copy must exist"
        assert not sorted(used - translations["es"]), "es 漏译"
        assert not sorted(used - translations["ca"]), "ca 漏译"

    def test_translation_tables_have_no_orphan_entries(self):
        """译文表里不能有代码已经不再使用的 key, 否则表会慢慢腐化。"""
        source, _ = self._tables()
        translations = self._translations()
        used = {key for key in self._used_keys(source) if CJK.search(key)}
        for lang in ("es", "ca"):
            orphans = sorted(translations[lang] - used)
            assert not orphans, f"{lang} 有孤儿译文: {orphans}"

    def test_translations_never_contain_chinese(self):
        """译文里出现中文 = 某条被原样抄进了译文表, 等于没翻。"""
        source = read_asset("app.js")
        start = source.index("const TRANSLATIONS = {")
        end = source.index("\n};", start)
        block = source[start:end]
        for lang in ("es", "ca"):
            s = block.index(f"  {lang}: {{")
            e = block.index("\n  },", s)
            body = block[s:e]
            values = re.findall(r":\s*'((?:[^'\\]|\\.)*)'", body)
            assert values
            leaked = [value for value in values if CJK.search(value)]
            assert not leaked, f"{lang} 译文里有中文: {leaked[:3]}"

    def test_every_data_i18n_key_is_defined(self):
        _, tables = self._tables()
        html = read_asset("index.html")
        keys = set(re.findall(r'data-i18n="([^"]+)"', html))
        assert keys
        assert not sorted(keys - tables["zh"])

    def test_exercise_keys_cover_every_answer_format(self):
        _, tables = self._tables()
        for key in ("ex.title", "ex.prompt", "ex.type", "ex.knowledgePoints",
                    "ex.prerequisites", "ex.answer", "ex.submit", "ex.evaluation",
                    "ex.score", "ex.feedback", "ex.notFactVerification"):
            assert key in tables["zh"], key


class TestUiAuditHarness:
    """Task 47.8 —— UI 审计 harness (``scripts/ui_audit.js``)。

    在 Node + 最小 DOM 桩里**真实执行**全部 11 个页面函数, 覆盖 spec 47.8 的
    清单: empty / loading / error state、long text、三语、大数据量、窄视口。

    仍然不是浏览器测试: 不解析 CSS 布局、不触发真实事件。CSS 相关的检查是
    对样式表的静态断言 (断点存在 / 长 token 可折行 / 表格可横向滚动)。
    """

    @staticmethod
    def _node() -> str:
        return TestUiRenderExecution._node()

    def test_ui_audit_passes(self):
        script = Path(__file__).resolve().parent.parent / "scripts" / "ui_audit.js"
        assert script.exists(), "ui_audit.js must exist"
        completed = subprocess.run(
            [self._node(), str(script)],
            capture_output=True,
            text=True,
            cwd=str(script.parent.parent),
            timeout=180,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        assert completed.returncode == 0, output
        assert "UI audit OK" in output

    def test_ui_audit_covers_every_spec_item(self):
        """审计脚本必须真的覆盖 47.8 清单里的 11 项, 而不是空跑。"""
        script = Path(__file__).resolve().parent.parent / "scripts" / "ui_audit.js"
        source = script.read_text(encoding="utf-8")
        for marker in (
            "empty state",
            "loading state",
            "error state",
            "long",
            "zh",
            "es",
            "ca",
            "large knowledge base",
            "many students",
            "many exercises",
            "narrow",
        ):
            assert marker in source, marker

    def test_ui_audit_would_catch_an_untranslated_page(self):
        """反向验证: 审计的核心断言是"es/ca 渲染结果里不得出现 CJK"。

        夹具数据全是 ASCII, 所以 CJK 只能来自漏译的界面文案 —— 这条断言没有
        假阳性。它正是修复前把"7 个页面没翻译"抓出来的那条。
        """
        script = Path(__file__).resolve().parent.parent / "scripts" / "ui_audit.js"
        source = script.read_text(encoding="utf-8")
        assert "no untranslated CJK in" in source
        assert "const CJK" in source
