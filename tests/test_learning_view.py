# -*- coding: utf-8 -*-
"""Task 40 tests: Student Learning UI (学习首页 / 学习路径 / 知识解释)。

验证方式的说明 (重要, 不掩饰限制)
----------------------------------
本机是 Windows, 而浏览器自动化 (agent-browser) **只支持 macOS / Linux**,
因此本任务**没有**浏览器端到端测试。绝不假装做过。

替代验证策略 (真实可执行的四层):
  1. 纯函数层: ``normalize_language`` 的语言标签校验与归一化 (含注入尝试)。
  2. 领域一致层: 本层新增的映射 (导航状态 / 下一事件) 必须与 Task 30 的
     领域状态机**逐项一致** —— 直接读领域私有 ``_TRANSITIONS`` 做漂移守卫,
     防止 UI 层偷偷长出一套自己的 mastery 规则。
  3. 服务层: 直接对 ``LearningViewService`` 断言 "无证据 -> 固定文案"、
     "语言不匹配 -> 绝不翻译" 等硬规则。
  4. API 契约层: 5 个新 endpoint 的真实 HTTP 行为 (含错误码 / 幂等 / 只读性)。

UI 交互 (点击按钮、hash 路由、语言下拉) 由 app.js 实现, 这里通过静态断言 +
API 契约断言覆盖其依赖面, 但不声称执行了 DOM 事件。

核心不变量 (每个都至少有一个测试盯着):
  - progress 只来自 Task 30/32 已有数据, 不新增 mastery 推断。
  - 导航状态与 Task 30 原始状态同时返回, 不允许只有前者。
  - 学习状态只能由 Task 30 的事件推进; 答题 (answered) 永不推进状态。
  - 学生答错**绝不**修改知识库 (validation_status / review_status / 证据集)。
  - 没有证据 -> 恰好 ``No grounded explanation available.``, 前端不生成内容。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from src.api.server import create_server, default_static_dir
from src.application.errors import InvalidInputError, NotFoundError
from src.application.learning_service import LearningService
from src.application.learning_view import (
    COMPLETION_STATES,
    DEFAULT_LANGUAGE,
    PATH_AVAILABLE,
    PATH_BLOCKED,
    PATH_COMPLETED,
    PATH_IN_PROGRESS,
    PATH_STATUSES,
    STATE_TO_PATH_STATUS,
    UI_LANGUAGES,
    LearningViewService,
    language_code,
    normalize_language,
)
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace
from src.models import Evidence, KnowledgePoint, Language
from src.student_learning import (
    _STATE_FOR_EVENT,
    _TRANSITIONS,
    LearningEventType,
    LearningState,
)

from tests.support import dynamic_i18n_prefixes, read_web_source

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXED_TIME = "2026-01-01T00:00:00+00:00"
WEB_DIR = Path(default_static_dir())
NO_EXPLANATION = "No grounded explanation available."

#: 应用层对"当前状态下唯一合法的下一事件"的映射 (Task 30 状态机的投影)。
NEXT_LEARNING_EVENT = LearningService.NEXT_LEARNING_EVENT


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
                payload = response.read()
                return response.status, json.loads(payload.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body=body, **kw)

    def upload(self, path, content, filename):
        return self.request(
            "POST", path, raw=content, headers={"X-Filename": filename}
        )


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
    """真实跑一遍: 建课堂 -> 上传 PDF -> 整堂处理 -> 拿到知识点。"""
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
        "knowledge_ids": [p["knowledge_id"] for p in points],
        "points": points,
    }


@pytest.fixture
def student(client, knowledge):
    course_id = knowledge["course_id"]
    status, payload = client.post(
        "/api/students?course_id=" + course_id,
        {"student_id": "stu-ana", "display_name": "Ana"},
    )
    assert status == 201, payload
    return payload["data"]


def make_exercise(client, course_id, kp_id, *, prompt="¿La función es lineal?", is_true=True):
    status, payload = client.post(
        "/api/exercises",
        {
            "course_id": course_id,
            "exercise_type": "true_false",
            "prompt": prompt,
            "knowledge_point_ids": [kp_id],
            "is_true": is_true,
        },
    )
    assert status in (200, 201), payload
    return payload["data"]


def submit_answer(client, course_id, student_id, exercise_id, value, sequence=0):
    status, payload = client.post(
        "/api/answers",
        {
            "course_id": course_id,
            "student_id": student_id,
            "exercise_id": exercise_id,
            "submitted_value": value,
            "sequence": sequence,
        },
    )
    assert status == 201, payload
    return payload["data"]


def record_event(client, course_id, student_id, kp_id, event_type):
    return client.post(
        f"/api/students/{student_id}/learning-events?course_id={course_id}",
        {"knowledge_id": kp_id, "event_type": event_type},
    )


def learning_path(client, course_id, student_id, kp_id):
    return client.get(
        f"/api/students/{student_id}/learning-paths/{kp_id}?course_id={course_id}"
    )


def dashboard(client, course_id, student_id):
    return client.get(f"/api/students/{student_id}/dashboard?course_id={course_id}")


def explanation(client, course_id, kp_id, language=None):
    query = {"course_id": course_id}
    if language is not None:
        query["language"] = language
    encoded = urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
    return client.get(f"/api/knowledge/{kp_id}/explanation?{encoded}")


def read_asset(name: str) -> str:
    """读取前端资源。

    P1-6: 前端已拆分成多个零构建脚本; ``read_asset("app.js")`` 返回按加载
    顺序拼接的**全部前端源码**, 语义与拆分前一致 (见 ``tests/support.py``)。
    """
    return read_web_source(name)


# ===========================================================================
# 1. 语言标签校验与归一化 (纯函数)
# ===========================================================================


class TestNormalizeLanguage:
    """``language`` 会写进 LearningRepresentation.language, 而 representation_id
    是内容寻址的 —— 因此必须在入口处校验并归一化, 否则用户输入会直接进入
    领域对象 (实测 ``language=../../etc`` 曾被原样接受)。"""

    def test_none_falls_back_to_default(self):
        assert normalize_language(None) == DEFAULT_LANGUAGE == "es"

    def test_empty_string_falls_back_to_default(self):
        assert normalize_language("") == "es"

    def test_whitespace_only_falls_back_to_default(self):
        assert normalize_language("   \t ") == "es"

    def test_custom_default_is_honoured(self):
        assert normalize_language(None, default="ca") == "ca"

    def test_uppercase_is_lowercased(self):
        assert normalize_language("ES") == "es"

    def test_region_subtag_is_lowercased(self):
        assert normalize_language("ES-ES") == "es-es"

    def test_script_subtag_is_accepted(self):
        assert normalize_language("zh-Hans") == "zh-hans"

    def test_all_ui_languages_are_accepted(self):
        for code in UI_LANGUAGES:
            assert normalize_language(code) == code

    def test_three_letter_primary_subtag_is_accepted(self):
        assert normalize_language("cat") == "cat"

    @pytest.mark.parametrize(
        "payload",
        [
            "../../etc",
            "..\\..\\windows",
            "es'--",
            'es"; rm -rf /',
            "es/ca",
            "es ca",
            "<script>",
            "e",
            "a" * 40,
            "es-",
            "-es",
            "1es",
        ],
    )
    def test_malformed_tags_are_rejected(self, payload):
        with pytest.raises(InvalidInputError):
            normalize_language(payload)

    @pytest.mark.parametrize("payload", [123, [], {}, True, b"es"])
    def test_non_string_is_rejected(self, payload):
        with pytest.raises(InvalidInputError):
            normalize_language(payload)

    def test_normalization_is_idempotent(self):
        once = normalize_language("ES-ES")
        assert normalize_language(once) == once


class TestLanguageCode:
    """证据 DTO 里的 ``language`` 是枚举显示值 (``"Spanish"``), 请求语言是
    ISO 码 (``"es"``)。两者不归一化就会被误判成"语言不匹配", 从而错误地
    拒绝一个本来可用的解释 —— 这是实测到的真实缺陷。"""

    @pytest.mark.parametrize(
        "display,expected",
        [
            ("Spanish", "es"),
            ("Catalan", "ca"),
            ("Chinese", "zh"),
            ("English", "en"),
            ("spanish", "es"),
            ("CATALAN", "ca"),
        ],
    )
    def test_display_values_map_to_iso_codes(self, display, expected):
        assert language_code(display) == expected

    @pytest.mark.parametrize("value", ["Unknown", "unknown", "UND", "", None, "  "])
    def test_unknown_is_not_a_language(self, value):
        """未知 != 另一种语言: 必须返回 None (无语言约束), 不能返回一个码。"""
        assert language_code(value) is None

    def test_already_normalized_codes_pass_through(self):
        assert language_code("es") == "es"
        assert language_code("es-ES") == "es-es"

    def test_garbage_is_not_a_language(self):
        assert language_code("../../etc") is None
        assert language_code("es'--") is None

    def test_iso_code_is_not_the_display_value(self):
        """这个断言是缺陷的根因记录: 显示值绝不能当作请求码使用。"""
        assert Language.SPANISH.value == "Spanish"
        assert Language.SPANISH.value != "es"
        assert language_code(Language.SPANISH.value) == "es"


# ===========================================================================
# 2. 与 Task 30 领域状态机的一致性 (漂移守卫)
# ===========================================================================


class TestDomainConsistency:
    """本层只能**投影** Task 30 的状态机, 不能重实现。这些测试直接对照领域
    私有常量, 一旦有人改了领域规则或本地映射不同步, 立刻失败。"""

    def test_state_to_path_status_covers_every_learning_state(self):
        domain_states = {state.value for state in LearningState}
        assert set(STATE_TO_PATH_STATUS) == domain_states

    def test_every_path_status_value_is_declared(self):
        assert set(STATE_TO_PATH_STATUS.values()) <= set(PATH_STATUSES)

    def test_path_statuses_are_unique_and_exhaustive(self):
        assert len(set(PATH_STATUSES)) == len(PATH_STATUSES)
        assert set(PATH_STATUSES) == {
            PATH_AVAILABLE,
            PATH_IN_PROGRESS,
            PATH_COMPLETED,
            PATH_BLOCKED,
        }

    def test_completion_states_are_real_learning_states(self):
        domain_states = {state.value for state in LearningState}
        assert set(COMPLETION_STATES) <= domain_states
        assert COMPLETION_STATES, "至少要有一个状态代表'前置已满足'"

    def test_next_learning_event_matches_domain_transitions(self):
        """逐状态对照领域 _TRANSITIONS: 唯一合法事件必须与本层映射一致。"""
        for state in LearningState:
            legal = _TRANSITIONS[state]
            expected = None
            if len(legal) == 1:
                expected = next(iter(legal)).value
            elif len(legal) > 1:  # pragma: no cover - 当前领域是单事件状态机
                raise AssertionError("领域状态机出现多事件分支, 本层映射需重新设计")
            assert NEXT_LEARNING_EVENT[state.value] == expected, state

    def test_next_learning_event_covers_every_state(self):
        assert set(NEXT_LEARNING_EVENT) == {s.value for s in LearningState}

    def test_reviewing_is_terminal(self):
        assert _TRANSITIONS[LearningState.REVIEWING] == frozenset()
        assert NEXT_LEARNING_EVENT["reviewing"] is None

    def test_answered_is_never_a_transition(self):
        """答题是评估事实, 不是状态推进 —— Task 30 明确 ANSWERED 不改变状态。"""
        for legal in _TRANSITIONS.values():
            assert LearningEventType.ANSWERED not in legal
        assert LearningEventType.ANSWERED not in set(_STATE_FOR_EVENT)

    def test_next_learning_event_never_returns_answered(self):
        assert LearningEventType.ANSWERED.value not in set(NEXT_LEARNING_EVENT.values())

    def test_classmethod_matches_module_mapping(self):
        for state in LearningState:
            assert (
                LearningService.next_learning_event(state.value)
                == NEXT_LEARNING_EVENT[state.value]
            )

    def test_unknown_state_has_no_next_event(self):
        assert LearningService.next_learning_event("bogus") is None

    def test_state_to_path_status_mapping_is_exactly_as_designed(self):
        assert STATE_TO_PATH_STATUS == {
            "not_started": PATH_AVAILABLE,
            "exposed": PATH_IN_PROGRESS,
            "practicing": PATH_IN_PROGRESS,
            "reviewing": PATH_COMPLETED,
        }


# ===========================================================================
# 3. 学习路径投影 (导航状态 + 原始状态并存)
# ===========================================================================


class TestLearningPathView:
    def test_fresh_student_path_is_available(self, client, knowledge, student):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        status, payload = learning_path(client, course_id, student["student_id"], kp_id)
        assert status == 200, payload
        data = payload["data"]
        assert data["student_id"] == student["student_id"]
        assert data["target_knowledge_point_id"] == kp_id
        assert kp_id in data["node_ids"]
        node = next(n for n in data["nodes"] if n["knowledge_point_id"] == kp_id)
        assert node["state"] == "not_started"
        assert node["status"] == PATH_AVAILABLE
        assert node["next_event"] == "viewed"

    def test_positions_are_zero_based_and_ordered(self, client, knowledge, student):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        _, payload = learning_path(client, course_id, student["student_id"], kp_id)
        nodes = payload["data"]["nodes"]
        assert [n["position"] for n in nodes] == list(range(len(nodes)))
        assert [n["knowledge_point_id"] for n in nodes] == payload["data"]["node_ids"]

    def test_viewed_moves_node_to_in_progress(self, client, knowledge, student):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        status, payload = record_event(client, course_id, sid, kp_id, "viewed")
        assert status == 200, payload
        assert payload["data"]["state"] == "exposed"
        _, view = learning_path(client, course_id, sid, kp_id)
        node = next(n for n in view["data"]["nodes"] if n["knowledge_point_id"] == kp_id)
        assert node["state"] == "exposed"
        assert node["status"] == PATH_IN_PROGRESS

    def test_practiced_stays_in_progress(self, client, knowledge, student):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        record_event(client, course_id, sid, kp_id, "viewed")
        record_event(client, course_id, sid, kp_id, "practiced")
        _, view = learning_path(client, course_id, sid, kp_id)
        node = next(n for n in view["data"]["nodes"] if n["knowledge_point_id"] == kp_id)
        assert node["state"] == "practicing"
        assert node["status"] == PATH_IN_PROGRESS
        assert node["next_event"] == "reviewed"

    def test_reviewed_becomes_completed(self, client, knowledge, student):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        for event in ("viewed", "practiced", "reviewed"):
            record_event(client, course_id, sid, kp_id, event)
        _, view = learning_path(client, course_id, sid, kp_id)
        node = next(n for n in view["data"]["nodes"] if n["knowledge_point_id"] == kp_id)
        assert node["state"] == "reviewing"
        assert node["status"] == PATH_COMPLETED
        assert node["next_event"] is None

    def test_status_basis_names_the_task30_state(self, client, knowledge, student):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        _, view = learning_path(client, course_id, student["student_id"], kp_id)
        node = next(n for n in view["data"]["nodes"] if n["knowledge_point_id"] == kp_id)
        assert "Task 30" in node["status_basis"]
        assert node["state"] in node["status_basis"]

    def test_raw_state_is_always_returned_alongside_navigation_status(
        self, client, knowledge, student
    ):
        """导航状态是投影, 绝不能取代原始状态 —— 否则会被误读成掌握度。"""
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        _, view = learning_path(client, course_id, student["student_id"], kp_id)
        for node in view["data"]["nodes"]:
            assert node["state"] in {s.value for s in LearningState}
            assert node["status"] in PATH_STATUSES
        assert set(view["data"]["states_used"]) == set(view["data"]["node_ids"])

    def test_prerequisite_not_completed_blocks_the_successor(
        self, client, workspace, knowledge, student
    ):
        """显式注册 prerequisite 关系后, 未走完的前置必须阻塞后继。"""
        course_id = knowledge["course_id"]
        kp_ids = knowledge["knowledge_ids"]
        if len(kp_ids) < 2:
            pytest.skip("fixture produced only one knowledge point")
        first, second = kp_ids[0], kp_ids[1]
        org = workspace.context(course_id).org_service
        org.add_relation(first, second, "prerequisite")

        sid = student["student_id"]
        _, view = learning_path(client, course_id, sid, second)
        nodes = {n["knowledge_point_id"]: n for n in view["data"]["nodes"]}
        assert first in nodes and second in nodes
        assert nodes[second]["status"] == PATH_BLOCKED
        assert first in nodes[second]["unmet_prerequisite_ids"]
        assert "BLOCKED" in nodes[second]["status_basis"]

        # 前置走完 -> 阻塞解除
        for event in ("viewed", "practiced", "reviewed"):
            record_event(client, course_id, sid, first, event)
        _, view2 = learning_path(client, course_id, sid, second)
        nodes2 = {n["knowledge_point_id"]: n for n in view2["data"]["nodes"]}
        assert nodes2[second]["status"] != PATH_BLOCKED
        assert nodes2[second]["unmet_prerequisite_ids"] == []

    def test_node_reports_prerequisite_ids(self, client, workspace, knowledge, student):
        course_id = knowledge["course_id"]
        kp_ids = knowledge["knowledge_ids"]
        if len(kp_ids) < 2:
            pytest.skip("fixture produced only one knowledge point")
        org = workspace.context(course_id).org_service
        org.add_relation(kp_ids[0], kp_ids[1], "prerequisite")
        _, view = learning_path(client, course_id, student["student_id"], kp_ids[1])
        node = next(n for n in view["data"]["nodes"] if n["knowledge_point_id"] == kp_ids[1])
        assert kp_ids[0] in node["prerequisite_ids"]

    def test_unknown_student_is_not_found(self, client, knowledge):
        status, payload = learning_path(
            client, knowledge["course_id"], "stu-missing", knowledge["knowledge_ids"][0]
        )
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_unknown_knowledge_point_is_not_found(self, client, knowledge, student):
        status, payload = learning_path(
            client, knowledge["course_id"], student["student_id"], "kp-does-not-exist"
        )
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_missing_course_id_is_invalid_input(self, client, knowledge, student):
        status, payload = client.get(
            f"/api/students/{student['student_id']}/learning-paths/"
            + knowledge["knowledge_ids"][0]
        )
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_path_is_deterministic(self, client, knowledge, student):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        _, first = learning_path(client, course_id, student["student_id"], kp_id)
        _, second = learning_path(client, course_id, student["student_id"], kp_id)
        assert first == second

    def test_prerequisite_chain_actually_reaches_the_path(
        self, client, workspace, knowledge, student
    ):
        """缺陷回归: 装配层曾把空的前置图喂给 Task 33 规划器, 于是
        Learning Path 永远是 [target] 单节点, 前置链形同不存在。"""
        course_id = knowledge["course_id"]
        kp_ids = knowledge["knowledge_ids"]
        if len(kp_ids) < 2:
            pytest.skip("fixture produced only one knowledge point")
        first, second = kp_ids[0], kp_ids[1]
        org = workspace.context(course_id).org_service
        org.add_relation(first, second, "prerequisite")
        _, payload = learning_path(client, course_id, student["student_id"], second)
        node_ids = payload["data"]["node_ids"]
        assert first in node_ids and second in node_ids, "前置必须出现在路径中"
        assert node_ids.index(first) < node_ids.index(second), "路径必须 root-first"

    def test_non_prerequisite_relations_are_not_prerequisites(
        self, client, workspace, knowledge, student
    ):
        """Task 33 规则: RELATED / CONTRASTS / EXTENDS 永不算前置。"""
        course_id = knowledge["course_id"]
        kp_ids = knowledge["knowledge_ids"]
        if len(kp_ids) < 2:
            pytest.skip("fixture produced only one knowledge point")
        first, second = kp_ids[0], kp_ids[1]
        org = workspace.context(course_id).org_service
        for relation_type in ("related", "contrasts", "extends"):
            org.add_relation(first, second, relation_type)
        _, payload = learning_path(client, course_id, student["student_id"], second)
        assert first not in payload["data"]["node_ids"]

    def test_prerequisite_cycle_is_reported_not_hung(
        self, client, workspace, knowledge, student
    ):
        course_id = knowledge["course_id"]
        kp_ids = knowledge["knowledge_ids"]
        if len(kp_ids) < 2:
            pytest.skip("fixture produced only one knowledge point")
        first, second = kp_ids[0], kp_ids[1]
        org = workspace.context(course_id).org_service
        org.add_relation(first, second, "prerequisite")
        org.add_relation(second, first, "prerequisite")
        status, payload = learning_path(
            client, course_id, student["student_id"], second
        )
        assert status == 200
        assert payload["data"]["status"] == "dependency_cycle"
        assert payload["data"]["cycle_node_ids"]
        assert payload["data"]["nodes"] == []

    def test_study_plan_sees_the_prerequisite_relation(
        self, client, workspace, knowledge, student
    ):
        """同一处接线也修复了学习计划: 未满足的前置必须能被规划器看见。"""
        course_id = knowledge["course_id"]
        kp_ids = knowledge["knowledge_ids"]
        if len(kp_ids) < 2:
            pytest.skip("fixture produced only one knowledge point")
        org = workspace.context(course_id).org_service
        org.add_relation(kp_ids[0], kp_ids[1], "prerequisite")
        _, payload = dashboard(client, course_id, student["student_id"])
        items = {item["knowledge_point_id"]: item for item in payload["data"]["study_plan"]["items"]}
        dependent = items[kp_ids[1]]
        assert kp_ids[0] in dependent["prerequisite_ids"]


# ===========================================================================
# 4. Grounded explanation (Task 29 投影)
# ===========================================================================


class _StubKnowledge:
    """只实现 grounded_explanation 真正用到的两个读取方法。"""

    def __init__(self, kp: KnowledgePoint, evidence=()):
        self._kp = kp
        self._evidence = list(evidence)

    def get_knowledge_point(self, knowledge_point_id):
        if knowledge_point_id != self._kp.knowledge_id:
            raise NotFoundError(f"knowledge point {knowledge_point_id!r} not found")
        return self._kp.to_dict()

    def get_evidence_for_knowledge_point(self, knowledge_point_id):
        return [ev.to_dict() for ev in self._evidence]


class _StubOrg:
    def __init__(self, prerequisites=()):
        self._prerequisites = list(prerequisites)

    def get_related_knowledge(self, knowledge_point_id, direction):
        return []


class _StubLearning:
    def __init__(self):
        self._submitted: dict[str, str] = {}

    def next_learning_event(self, state):
        return NEXT_LEARNING_EVENT.get(state)


def make_service(kp, evidence=(), *, store=None, prerequisites=()):
    return LearningViewService(
        "course-x",
        org_service=_StubOrg(prerequisites),
        store={} if store is None else store,
        learning_service=_StubLearning(),
        knowledge_service=_StubKnowledge(kp, evidence),
        clock=fixed_clock(FIXED_TIME),
    )


class TestGroundedExplanationService:
    def test_no_evidence_yields_exact_fallback_message(self):
        kp = KnowledgePoint(
            knowledge_id="kp-empty", title="Sin evidencia", content="", evidence_refs=[]
        )
        result = make_service(kp).grounded_explanation("kp-empty", "es")
        assert result["available"] is False
        assert result["status"] == "not_available"
        assert result["message"] == NO_EXPLANATION
        assert result["representation"] is None

    def test_no_evidence_never_invents_content(self):
        kp = KnowledgePoint(
            knowledge_id="kp-empty", title="Sin evidencia", content="", evidence_refs=[]
        )
        result = make_service(kp).grounded_explanation("kp-empty", "ca")
        assert result["representation"] is None
        assert result["evidence"] == []

    def test_language_mismatch_does_not_translate(self):
        kp = KnowledgePoint(
            knowledge_id="kp-es",
            title="Ecuación lineal",
            content="Una ecuación lineal tiene grado uno.",
            evidence_refs=["ev-es"],
        )
        evidence = [
            Evidence(
                evidence_id="ev-es",
                content="Una ecuación lineal tiene grado uno.",
                language=Language.SPANISH,
                evidence_type="transcript",
            )
        ]
        result = make_service(
            kp, evidence, store={"ev-es": evidence[0]}
        ).grounded_explanation("kp-es", "ca")
        assert result["available"] is False
        assert result["status"] == "language_not_available"
        assert result["representation"] is None
        assert "no automatic translation" in result["message"]
        # 原文照原样返回, 没有被翻译成加泰语
        assert result["evidence"][0]["content"] == "Una ecuación lineal tiene grado uno."
        assert result["evidence"][0]["language"] == "Spanish"

    def test_matching_language_is_available(self):
        kp = KnowledgePoint(
            knowledge_id="kp-es",
            title="Ecuación lineal",
            content="Una ecuación lineal tiene grado uno.",
            evidence_refs=["ev-es"],
        )
        evidence = Evidence(
            evidence_id="ev-es",
            content="Una ecuación lineal tiene grado uno.",
            language=Language.SPANISH,
            evidence_type="transcript",
        )
        result = make_service(kp, [evidence], store={"ev-es": evidence}).grounded_explanation(
            "kp-es", "ES"
        )
        assert result["available"] is True
        assert result["status"] == "ok"
        assert result["message"] == ""
        assert result["requested_language"] == "es"  # 已归一化
        assert result["representation"]["language"] == "es"

    def test_unknown_evidence_language_is_not_a_mismatch(self):
        """Unknown 表示'不知道', 不是'另一种语言', 不能否决请求。"""
        kp = KnowledgePoint(
            knowledge_id="kp-unk",
            title="Tema",
            content="Contenido.",
            evidence_refs=["ev-unk"],
        )
        evidence = Evidence(
            evidence_id="ev-unk",
            content="Contenido.",
            language=Language.UNKNOWN,
            evidence_type="document",
        )
        result = make_service(kp, [evidence], store={"ev-unk": evidence}).grounded_explanation(
            "kp-unk", "zh"
        )
        assert result["available"] is True
        assert result["evidence_languages"] == ["Unknown"]
        assert result["supported_languages"] == []

    def test_invalid_language_is_rejected_before_any_read(self):
        kp = KnowledgePoint(
            knowledge_id="kp-x", title="t", content="c", evidence_refs=[]
        )
        with pytest.raises(InvalidInputError):
            make_service(kp).grounded_explanation("kp-x", "../../etc")

    def test_empty_language_uses_default(self):
        kp = KnowledgePoint(
            knowledge_id="kp-x", title="t", content="c", evidence_refs=[]
        )
        result = make_service(kp).grounded_explanation("kp-x", "")
        assert result["requested_language"] == "es"

    def test_unknown_knowledge_point_raises_not_found(self):
        kp = KnowledgePoint(knowledge_id="kp-x", title="t", content="c", evidence_refs=[])
        with pytest.raises(NotFoundError):
            make_service(kp).grounded_explanation("kp-other", "es")

    def test_empty_knowledge_point_id_is_invalid_input(self):
        kp = KnowledgePoint(knowledge_id="kp-x", title="t", content="c", evidence_refs=[])
        with pytest.raises(InvalidInputError):
            make_service(kp).grounded_explanation("   ", "es")


class TestGroundedExplanationApi:
    def test_explanation_available_for_real_pipeline(self, client, knowledge):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        status, payload = explanation(client, course_id, kp_id, "es")
        assert status == 200, payload
        data = payload["data"]
        assert data["available"] is True
        assert data["status"] == "ok"
        assert data["representation"]["language"] == "es"
        assert data["representation"]["knowledge_point_id"] == kp_id

    @pytest.mark.parametrize("language", ["es", "ca", "zh"])
    def test_all_ui_languages_supported(self, client, knowledge, language):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        status, payload = explanation(client, course_id, kp_id, language)
        assert status == 200
        assert payload["data"]["requested_language"] == language
        assert payload["data"]["available"] is True

    def test_language_defaults_to_es(self, client, knowledge):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        status, payload = explanation(client, course_id, kp_id)
        assert status == 200
        assert payload["data"]["requested_language"] == "es"

    def test_evidence_is_returned_verbatim(self, client, knowledge):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        _, payload = explanation(client, course_id, kp_id, "es")
        data = payload["data"]
        assert data["evidence"], "fixture KP must have evidence"
        rep = data["representation"]
        # 解释正文 = 已有内容, 不是新生成的
        assert rep["explanation"] == data["evidence"][0]["content"]
        for item in data["evidence"]:
            assert item["evidence_id"]
            assert item["source"]["material_id"]

    def test_switching_language_does_not_mutate_evidence(self, client, knowledge):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        _, es = explanation(client, course_id, kp_id, "es")
        _, ca = explanation(client, course_id, kp_id, "ca")
        _, zh = explanation(client, course_id, kp_id, "zh")
        assert es["data"]["evidence"] == ca["data"]["evidence"]
        assert es["data"]["evidence"] == zh["data"]["evidence"]
        assert es["data"]["evidence_languages"] == zh["data"]["evidence_languages"]

    def test_case_variant_language_yields_same_representation(self, client, knowledge):
        """ES 与 es 必须落到同一个 representation_id, 否则内容寻址会被破坏。"""
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        _, lower = explanation(client, course_id, kp_id, "es")
        _, upper = explanation(client, course_id, kp_id, "ES")
        assert (
            lower["data"]["representation"]["representation_id"]
            == upper["data"]["representation"]["representation_id"]
        )

    @pytest.mark.parametrize(
        "payload", ["../../etc", "es'--", "a" * 40, "es ca", "es/ca"]
    )
    def test_invalid_language_returns_400(self, client, knowledge, payload):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        status, body = explanation(client, course_id, kp_id, payload)
        assert status == 400
        assert body["error"]["code"] == "INVALID_INPUT"
        assert body["success"] is False

    def test_missing_course_id_returns_400(self, client, knowledge):
        status, payload = client.get(
            f"/api/knowledge/{knowledge['knowledge_ids'][0]}/explanation"
        )
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_unknown_knowledge_point_returns_404(self, client, knowledge):
        status, payload = explanation(client, knowledge["course_id"], "kp-nope", "es")
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_explanation_includes_related_concepts_and_prerequisites(
        self, client, knowledge
    ):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        _, payload = explanation(client, course_id, kp_id, "es")
        assert isinstance(payload["data"]["related_concepts"], list)
        assert isinstance(payload["data"]["prerequisites"], list)

    def test_explanation_is_deterministic(self, client, knowledge):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        _, first = explanation(client, course_id, kp_id, "es")
        _, second = explanation(client, course_id, kp_id, "es")
        assert first == second


# ===========================================================================
# 5. 学习事件 (只能走 Task 30 状态机)
# ===========================================================================


class TestLearningEvents:
    def test_full_sequence_advances_through_the_state_machine(
        self, client, knowledge, student
    ):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        expected = [
            ("viewed", "exposed", "practiced"),
            ("practiced", "practicing", "reviewed"),
            ("reviewed", "reviewing", None),
        ]
        for event, state, next_event in expected:
            status, payload = record_event(client, course_id, sid, kp_id, event)
            assert status == 200, payload
            assert payload["data"]["state"] == state
            assert payload["data"]["next_event"] == next_event

    def test_out_of_order_event_is_recorded_but_does_not_advance(
        self, client, knowledge, student
    ):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        status, payload = record_event(client, course_id, sid, kp_id, "reviewed")
        assert status == 200
        assert payload["data"]["state"] == "not_started"

    def test_repeated_event_is_idempotent(self, client, knowledge, student):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        _, first = record_event(client, course_id, sid, kp_id, "viewed")
        _, second = record_event(client, course_id, sid, kp_id, "viewed")
        assert first["data"]["state"] == second["data"]["state"] == "exposed"

    def test_event_type_is_case_insensitive(self, client, knowledge, student):
        status, payload = record_event(
            client, knowledge["course_id"], student["student_id"],
            knowledge["knowledge_ids"][0], "VIEWED",
        )
        assert status == 200
        assert payload["data"]["event_type"] == "viewed"

    def test_unknown_event_type_is_invalid_input(self, client, knowledge, student):
        status, payload = record_event(
            client, knowledge["course_id"], student["student_id"],
            knowledge["knowledge_ids"][0], "bogus",
        )
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_missing_knowledge_id_is_invalid_input(self, client, knowledge, student):
        status, payload = client.post(
            f"/api/students/{student['student_id']}/learning-events?course_id="
            + knowledge["course_id"],
            {"event_type": "viewed"},
        )
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_missing_event_type_is_invalid_input(self, client, knowledge, student):
        status, payload = client.post(
            f"/api/students/{student['student_id']}/learning-events?course_id="
            + knowledge["course_id"],
            {"knowledge_id": knowledge["knowledge_ids"][0]},
        )
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_unknown_student_is_not_found(self, client, knowledge):
        status, payload = record_event(
            client, knowledge["course_id"], "stu-nobody",
            knowledge["knowledge_ids"][0], "viewed",
        )
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_answering_does_not_advance_learning_state(
        self, client, knowledge, student
    ):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        exercise = make_exercise(client, course_id, kp_id, is_true=True)
        submit_answer(client, course_id, sid, exercise["exercise_id"], "true")
        _, view = learning_path(client, course_id, sid, kp_id)
        node = next(n for n in view["data"]["nodes"] if n["knowledge_point_id"] == kp_id)
        assert node["state"] == "not_started", "答题不是状态推进 (Task 30)"
        assert node["activity"]["answer_count"] >= 1


# ===========================================================================
# 6. 学生首页 (只读投影; 不新增 mastery)
# ===========================================================================


class TestStudentDashboard:
    def test_dashboard_has_every_required_section(self, client, knowledge, student):
        status, payload = dashboard(
            client, knowledge["course_id"], student["student_id"]
        )
        assert status == 200, payload
        data = payload["data"]
        assert set(data) == {
            "course_id",
            "student_id",
            "display_name",
            "progress",
            "study_plan",
            "learning_paths",
            "pending_exercises",
            "recent_evaluations",
            "knowledge_gaps",
        }

    def test_progress_reports_only_existing_state_facts(self, client, knowledge, student):
        status, payload = dashboard(
            client, knowledge["course_id"], student["student_id"]
        )
        assert status == 200
        progress = payload["data"]["progress"]
        assert set(progress["state_counts"]) <= {s.value for s in LearningState}
        assert sum(progress["state_counts"].values()) == len(
            progress["registered_knowledge_points"]
        )

    def test_state_counts_track_recorded_events(self, client, knowledge, student):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        record_event(client, course_id, sid, kp_id, "viewed")
        _, payload = dashboard(client, course_id, sid)
        assert payload["data"]["progress"]["state_counts"] == {"exposed": 1}
        assert payload["data"]["progress"]["states"][0]["knowledge_point_id"] == kp_id

    def test_not_started_is_exactly_the_complement_of_touched(
        self, client, knowledge, student
    ):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        _, before = dashboard(client, course_id, sid)
        record_event(client, course_id, sid, kp_id, "viewed")
        _, after = dashboard(client, course_id, sid)
        assert kp_id in before["data"]["progress"]["not_started_knowledge_points"]
        assert kp_id not in after["data"]["progress"]["not_started_knowledge_points"]
        assert set(after["data"]["progress"]["course_knowledge_points"]) == set(
            after["data"]["progress"]["registered_knowledge_points"]
        ) | set(after["data"]["progress"]["not_started_knowledge_points"])

    def test_no_invented_mastery_fields(self, client, knowledge, student):
        """本层只能投影已有事实: 不得出现自造的掌握度字段。"""
        _, payload = dashboard(client, knowledge["course_id"], student["student_id"])
        blob = json.dumps(payload, ensure_ascii=False).lower()
        for forbidden in ("mastery", "proficiency", "estimated_ability", "predicted"):
            assert forbidden not in blob

    def test_pending_exercises_exclude_answered_ones(self, client, knowledge, student):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        exercise = make_exercise(client, course_id, kp_id)
        _, before = dashboard(client, course_id, sid)
        assert exercise["exercise_id"] in [
            item["exercise_id"] for item in before["data"]["pending_exercises"]
        ]
        submit_answer(client, course_id, sid, exercise["exercise_id"], "true")
        _, after = dashboard(client, course_id, sid)
        assert exercise["exercise_id"] not in [
            item["exercise_id"] for item in after["data"]["pending_exercises"]
        ]

    def test_recent_evaluations_carry_submitted_at(self, client, knowledge, student):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        exercise = make_exercise(client, course_id, kp_id)
        submit_answer(client, course_id, sid, exercise["exercise_id"], "true")
        _, payload = dashboard(client, course_id, sid)
        recent = payload["data"]["recent_evaluations"]
        assert recent
        assert recent[0]["exercise_id"] == exercise["exercise_id"]
        assert recent[0]["submitted_at"] == FIXED_TIME
        assert recent[0]["status"]
        assert recent[0]["score"] is not None

    def test_study_plan_is_a_projection_of_task33(self, client, knowledge, student):
        _, payload = dashboard(client, knowledge["course_id"], student["student_id"])
        plan = payload["data"]["study_plan"]
        assert plan is not None
        assert "items" in plan
        for item in plan["items"]:
            assert item["knowledge_point_id"]
            assert "reason_codes" in item

    def test_knowledge_gaps_reuse_existing_facts(self, client, knowledge, student):
        _, payload = dashboard(client, knowledge["course_id"], student["student_id"])
        gaps = payload["data"]["knowledge_gaps"]
        assert set(gaps) == {"student_gaps", "not_started_knowledge_points", "course_gaps"}
        for gap in gaps["student_gaps"]:
            assert gap["reason"] == "STUDENT_RECENT_INCORRECT"

    def test_dashboard_is_deterministic(self, client, knowledge, student):
        course_id, sid = knowledge["course_id"], student["student_id"]
        _, first = dashboard(client, course_id, sid)
        _, second = dashboard(client, course_id, sid)
        assert first == second

    def test_unknown_student_is_not_found(self, client, knowledge):
        status, payload = dashboard(client, knowledge["course_id"], "stu-missing")
        assert status == 404
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_missing_course_id_is_invalid_input(self, client, student):
        status, payload = client.get(f"/api/students/{student['student_id']}/dashboard")
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"


# ===========================================================================
# 7. submitted_at 幂等 + 评估不污染知识库
# ===========================================================================


def kp_snapshot(client, course_id, kp_id):
    status, payload = client.get(f"/api/knowledge/{kp_id}?course_id={course_id}")
    assert status == 200, payload
    return payload["data"]


class TestAnswerTimestampsAndIsolation:
    def test_submitted_at_comes_from_the_injected_clock(self, client, knowledge, student):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        exercise = make_exercise(client, course_id, kp_id)
        answer = submit_answer(
            client, course_id, student["student_id"], exercise["exercise_id"], "true"
        )
        assert answer["submitted_at"] == FIXED_TIME

    def test_resubmitting_the_same_answer_keeps_the_first_timestamp(
        self, client, knowledge, student
    ):
        """幂等的定义是"同一份答案": sequence 是身份的一部分, 因此必须同序重提。"""
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        exercise = make_exercise(client, course_id, kp_id)
        first = submit_answer(client, course_id, sid, exercise["exercise_id"], "true")
        second = submit_answer(client, course_id, sid, exercise["exercise_id"], "true")
        assert second["answer_id"] == first["answer_id"]
        assert second["submitted_at"] == first["submitted_at"]

    def test_a_later_attempt_is_a_distinct_answer(self, client, knowledge, student):
        """sequence 参与身份: 第二次作答是**另一份**答案, 不能被误当成重复。"""
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        exercise = make_exercise(client, course_id, kp_id)
        first = submit_answer(client, course_id, sid, exercise["exercise_id"], "true")
        second = submit_answer(
            client, course_id, sid, exercise["exercise_id"], "true", sequence=1
        )
        assert second["answer_id"] != first["answer_id"]
        assert second["submitted_at"] == first["submitted_at"]  # 同一个固定时钟

    def test_submitted_at_is_not_part_of_the_answer_identity(
        self, client, knowledge, student
    ):
        """确定性要求: 时间不参与身份。同一答案在同一/不同时钟下 id 相同。"""
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        sid = student["student_id"]
        exercise = make_exercise(client, course_id, kp_id)
        answer = submit_answer(client, course_id, sid, exercise["exercise_id"], "true")
        assert answer["answer_id"]
        assert FIXED_TIME not in answer["answer_id"]
        assert not re.search(r"\d{4}-\d{2}-\d{2}", answer["answer_id"])

    def test_wrong_answer_does_not_change_validation_status(
        self, client, knowledge, student
    ):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        before = kp_snapshot(client, course_id, kp_id)
        exercise = make_exercise(client, course_id, kp_id, is_true=True)
        result = submit_answer(
            client, course_id, student["student_id"], exercise["exercise_id"], "false"
        )
        assert result["evaluation_status"] == "incorrect"
        after = kp_snapshot(client, course_id, kp_id)
        assert after["validation_status"] == before["validation_status"]
        assert after["review_status"] == before["review_status"]
        assert after["knowledge_score"] == before["knowledge_score"]

    def test_wrong_answer_does_not_add_evidence(self, client, knowledge, student):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        _, before = client.get(f"/api/knowledge/{kp_id}/evidence?course_id={course_id}")
        exercise = make_exercise(client, course_id, kp_id, is_true=True)
        submit_answer(
            client, course_id, student["student_id"], exercise["exercise_id"], "false"
        )
        _, after = client.get(f"/api/knowledge/{kp_id}/evidence?course_id={course_id}")
        assert after == before

    def test_wrong_answer_does_not_break_the_traceability_chain(
        self, client, knowledge, student
    ):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        _, before = client.get(f"/api/knowledge/{kp_id}/trace?course_id={course_id}")
        exercise = make_exercise(client, course_id, kp_id, is_true=True)
        submit_answer(
            client, course_id, student["student_id"], exercise["exercise_id"], "false"
        )
        _, after = client.get(f"/api/knowledge/{kp_id}/trace?course_id={course_id}")
        assert after["data"]["complete"] is True
        assert after["data"]["links"] == before["data"]["links"]

    def test_evaluation_status_is_separate_from_review_status(
        self, client, knowledge, student
    ):
        """评估是'学生答得对不对', 审核是'知识是否成立' —— 两者绝不同源。"""
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        exercise = make_exercise(client, course_id, kp_id, is_true=True)
        result = submit_answer(
            client, course_id, student["student_id"], exercise["exercise_id"], "false"
        )
        assert result["evaluation_status"] in {
            "correct",
            "incorrect",
            "partial",
            "unsupported",
        }
        kp = kp_snapshot(client, course_id, kp_id)
        assert kp["review_status"] == "pending"

    def test_evaluation_is_readable_after_submission(self, client, knowledge, student):
        course_id, kp_id = knowledge["course_id"], knowledge["knowledge_ids"][0]
        exercise = make_exercise(client, course_id, kp_id, is_true=True)
        answer = submit_answer(
            client, course_id, student["student_id"], exercise["exercise_id"], "true"
        )
        status, payload = client.get(
            f"/api/evaluations/{answer['answer_id']}?course_id={course_id}"
        )
        assert status == 200
        assert payload["data"]["evaluation_id"]


# ===========================================================================
# 8. UI 契约 (静态断言; 不声称执行了 DOM 事件)
# ===========================================================================


class TestUiContract:
    def test_app_js_keeps_shared_student_support_endpoints(self):
        """隐式学生支撑页仍读学生 API；已删除的详情端点不再由前端调用。"""
        source = read_asset("app.js")
        for fragment in ("/students/", "/explanation"):
            assert fragment in source, fragment
        assert "/students/' + encodeURIComponent(studentId) + '/dashboard'" not in source
        assert "/learning-events" not in source

    def test_removed_student_detail_renderer_is_absent(self):
        source = read_asset("app.js")
        assert "renderPathChain" not in source
        assert "async function pageStudent(" not in source
        assert "async function pageStudents(" not in source

    def test_app_js_contains_the_exact_fallback_message(self):
        assert NO_EXPLANATION in read_asset("app.js")

    def test_app_js_never_translates_original_text(self):
        source = read_asset("app.js")
        # 界面语言只作用于 data-i18n 节点, 且注释里明确写了不翻译原文
        assert "data-i18n" in source
        assert "绝不" in source

    def test_index_html_declares_the_language_picker(self):
        html = read_asset("index.html")
        assert 'id="ui-language"' in html
        for code in UI_LANGUAGES:
            assert f'value="{code}"' in html

    def test_index_html_does_not_expose_student_pages(self):
        html = read_asset("index.html")
        assert 'href="#/students"' not in html
        assert "data-i18n" in html

    def test_learning_event_action_is_not_exposed_without_a_student_page(self):
        source = read_asset("app.js")
        assert "learning-event" not in source
        assert "actionLearningEvent" not in source

    def test_student_page_routes_are_not_registered(self):
        source = read_asset("app.js")
        assert "parts[0] === 'students'" not in source
        assert "parts[2] === 'students'" not in source

    def test_explanation_panel_mount_point_exists(self):
        assert "explanation-panel" in read_asset("app.js")


CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


class TestI18nTables:
    """三语表必须 key 完全对齐, 且 t() 用到的 key 全部存在 (否则界面会漏字)。

    Task 47.8 之后 ``app.js`` 的 ``t()`` 是**两层**的 (详见
    ``tests/test_exercise_ui.py::TestI18nTables`` 的类文档):

    1. ``I18N[lang]`` —— 可枚举项的符号 key;
    2. ``TRANSLATIONS[lang]`` —— 整句界面文案, key 就是中文原文。

    所以"key 是否存在"必须按**两层合并**判断, 否则整句文案会被误报成悬空 key。
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
            keys = set(re.findall(r"'([A-Za-z0-9_.]+)'\s*:", block[s:e]))
            tables[lang] = keys
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

    #: ``t('prefix.' + variable)`` 形式的动态 key。
    #:
    #: 这类调用不能简单用正则抓前缀就完事 —— 前缀本身**不是**一个 key,
    #: 真正的 key 是"前缀 + 某个枚举值的字符串"。所以要显式声明每个
    #: 前缀可能的取值域, 然后把**展开后的 key 集合**拿去校验。
    #:
    #: 2026-09-21 (P3-4) 起取值域由 ``support.dynamic_i18n_prefixes()``
    #: **从后端常量导入**, 不再在这里手抄。手抄的取值域是一条会漂移的规则:
    #: 后端加一个新枚举值, 抄来的元组不会自己长大 —— 测试依然绿, 而用户
    #: 看到的是页面上多出一个 ``rs.block.SOME_NEW_VALUE``。这正是 ``t()``
    #: 回落规则最危险的地方: 原样返回 key, 没有异常、没有日志。
    @classmethod
    def _dynamic_prefixes(cls) -> dict:
        return dynamic_i18n_prefixes()

    @classmethod
    def _expand_dynamic_keys(cls, used):
        """把动态前缀展开为真实 key, 返回 ``(expanded, prefixes)``。"""
        dynamic = cls._dynamic_prefixes()
        expanded: set = set()
        prefixes: set = set()
        for key in used:
            for prefix, suffixes in dynamic.items():
                if key == prefix:
                    prefixes.add(prefix)
                    expanded.update(prefix + s for s in suffixes)
        return expanded, prefixes

    def test_all_three_tables_have_identical_keys(self):
        _, tables = self._tables()
        assert tables["zh"] == tables["es"] == tables["ca"]
        assert len(tables["zh"]) > 40

    def test_every_used_key_is_defined(self):
        """每个 ``t('...')`` 都必须能被**某一层**解析, 不能有悬空 key。

        动态前缀 (``t('attention.' + kind)``) 先按其取值域展开, 再校验
        展开后的每一个 key —— 这样既不会误报前缀, 也不会放过真正的
        悬空 key（新枚举值没加文案时会被抓到）。
        """
        source, tables = self._tables()
        translations = self._translations()
        used = self._used_keys(source)
        dynamic = self._dynamic_prefixes()
        expanded, _ = self._expand_dynamic_keys(used)
        # 静态 key（去掉动态前缀本身）+ 展开后的动态 key
        checked = (used - set(dynamic)) | expanded
        missing = sorted(
            checked - tables["zh"] - translations["es"] - translations["ca"]
        )
        assert not missing, f"i18n keys used but not defined: {missing}"
        # 动态前缀必须真的被用到 (否则取值域声明该删了)
        _, prefixes = self._expand_dynamic_keys(used)
        stale = sorted(set(dynamic) - prefixes)
        assert not stale, f"stale dynamic prefix declarations: {stale}"

    def test_sentence_keys_are_translated_in_both_languages(self):
        """整句界面文案 (key = 中文原文) 在 es / ca 里必须都有译文。"""
        source, _ = self._tables()
        translations = self._translations()
        used = {key for key in self._used_keys(source) if CJK.search(key)}
        assert used, "sentence-level copy must exist"
        assert not sorted(used - translations["es"]), "es 漏译"
        assert not sorted(used - translations["ca"]), "ca 漏译"

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
            assert not leaked, f"{lang} 译文里混入中文: {leaked[:5]}"

    def test_state_and_status_keys_cover_the_domain(self):
        _, tables = self._tables()
        for state in LearningState:
            assert f"state.{state.value}" in tables["zh"]
        for status in PATH_STATUSES:
            assert f"status.{status}" in tables["zh"]

    def test_ui_language_picker_matches_the_service_whitelist(self):
        """界面语言清单在**三个**地方各有一份, 必须逐项一致。

        后端白名单 (``UI_LANGUAGES``) / ``index.html`` 的选择器选项 /
        ``app.js`` 的 ``UI_LANGUAGES`` —— 三者不一致的后果不是"多一个选项":
        前端会接受一种后端不认的语言, 而 ``normalize_language()`` 会抛
        ``InvalidInputError``, 用户看到错误页, 却从没选过那种语言。

        原断言只数了 ``<option>`` 的**个数** —— 把某个选项的值从 ``zh`` 改成
        ``en``（个数不变）也能通过。这里改成比对**取值**, 并把前端那份清单也
        纳进来 (它此前完全没有守卫)。
        """
        assert tuple(UI_LANGUAGES) == ("es", "ca", "zh")
        expected = set(UI_LANGUAGES)

        html = read_asset("index.html")
        picker = html[html.index('id="ui-language"'):]
        picker = picker[: picker.index("</select>")]
        values = set(re.findall(r'<option value="([^"]*)"', picker))
        assert values == expected, (values, expected)

        js = read_asset("app.js")
        match = re.search(r"const UI_LANGUAGES = \[([^\]]*)\]", js)
        assert match, "app.js 里找不到 UI_LANGUAGES"
        frontend = set(re.findall(r"'([^']*)'", match.group(1)))
        assert frontend == expected, (frontend, expected)

    def test_the_stored_language_is_validated_before_use(self):
        """``ca.lang`` 是**存储值**, 不是既成事实 —— 用之前必须校验。

        与路由里的 ``course_id`` 同一个形状 (缺陷 B): 存储值可能是旧版本写的、
        被手工改过的、或来自一个曾经支持更多语言的版本。不校验的话
        ``state.lang`` 会是一个非法值, 而它会被
        1. 原样发给后端 (``?lang=<非法值>``) —— ``normalize_language()`` 抛
           ``InvalidInputError``, 用户看到一个他从没选过的语言的错误页;
        2. 原样赋给语言选择器 —— 没有匹配项时浏览器显示第一项, 于是
           "选择器显示的语言"只是**碰巧**等于 ``t()`` 回落的中文。

        ``setLang()`` 一直有校验, 初始读取此前没有。
        """
        js = read_asset("app.js")
        # 初始值走 pickInitialLang(), 而不是裸的 getItem。
        assert "lang: pickInitialLang()," in js
        assert "lang: window.localStorage.getItem('ca.lang')" not in js
        # 校验规则只有一条: 在 UI_LANGUAGES 里才算数。
        body = js[js.index("function pickInitialLang()"):]
        body = body[: body.index("\n}")]
        assert "UI_LANGUAGES.indexOf(stored) >= 0" in body
        # 而且 UI_LANGUAGES 必须定义在 state 之前 —— 否则初始读取拿到的是 TDZ。
        assert js.index("const UI_LANGUAGES = [") < js.index("const state = {")
