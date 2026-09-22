# -*- coding: utf-8 -*-
"""Task 64 — Evidence-Grounded Exercise Workflow 测试。

覆盖的 spec 条款::

    64.2  Exercise Source       Exercise -> KnowledgePoint -> Evidence -> Material
    64.3  Exercise Generation   确定性模板生成 (禁止 LLM)
    64.4  Determinism           相同输入 -> 相同 question / options / answer / grounding
    64.5  Exercise Types        TRUE_FALSE / MULTIPLE_CHOICE / SHORT_ANSWER (+ FILL_BLANK)
    64.6  TRUE_FALSE            答案必须由 Knowledge 直接支持
    64.7  Multiple Choice       干扰项不得伪造新的课程事实
    64.8  Short Answer          Evaluation 继续调用既有评估层
    64.9  Evidence IDs          Exercise 保留 knowledge_id + evidence_ids
    64.10 Unverified            默认不生成正式 Exercise
    64.11 Optional Practice     未验证内容必须明确标注
    64.12 Conflicted            不生成, 优先解决冲突
    64.13 Duplicate Protection  幂等生成
    64.14 Course Isolation      绝不跨课程出题
    64.15 Exercise UI           list / detail / start / submit / evaluation / next
    64.16 Exercise Detail       默认不暴露答案
    64.17 Answer                提交后调用既有 Evaluation
    64.18 Evaluation            复用 feedback / score / correctness
    64.19 Student State         UI 不自己改 StudentState
    64.20 Tests                 60+

几条刻意写死的判据（产品语义，不是实现细节）
--------------------------------------------
1. **生成器不能发明句子**: 判断题的题干必须是 KP 文本里**逐字存在**的
   子串; 多选题的正确选项同理。这条断言直接抓 "LLM 换了句话" 这一类
   最危险的漂移 —— 一旦生成器开始改写, 它就再也不能保证 grounding。
2. **草稿 id 就是幂等键**: 同一 (KP, 配置) 生成两次必须得到同一个
   ``draft_id``; 落库两次必须得到同一个 ``exercise_id``, 且练习表只多一行。
3. **拒绝是正常结果**: ``unverified`` / ``conflicted`` 返回结构化 refusal,
   不是异常, 也不是"降级成一道正式题"。``allow_unverified`` 只放宽
   unverified, **绝不放宽** conflicted。
4. **grounding 链必须完整且可核**: 每一跳都能在既有存储里找到;
   找不到的引用进 ``unresolved``, 而不是被悄悄丢掉。
5. **课程隔离由成员关系保证**: knowledge_id 是内容寻址的, 两门课里
   完全相同的材料会产生相同 id —— 所以隔离断言必须落在**成员集合**上,
   而不是 id 的字符串前缀。
6. **重启后一切照旧**: 进程 A 生成/作答/评估, 进程 B 只拿到数据目录,
   必须能重放出同一个 exercise_id / answer_id / evaluation_id。
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from src.api.server import create_server
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace
from src.exercise_generation import (
    GENERATOR_VERSION,
    STRUCTURAL_DISTRACTORS,
    UNVERIFIED_PRACTICE_BANNER,
    ExerciseBasis,
    ExerciseTemplateGenerator,
    GenerationConfig,
    GenerationRefusal,
    RefusalReason,
    TemplateId,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

FIXED_TIME = "2026-09-18T09:00:00+00:00"
TODAY = "2026-09-18"

#: 每节课用不同夹具: material_id = f(内容, 文件名), 复用同一份文件会被去重。
SESSION_FIXTURE_SETS = (
    ("documents/simple.pdf", "documents/simple.docx", "notes/spanish.md"),
    ("documents/multilingual.pdf", "notes/catalan.md"),
    ("documents/tables.pdf", "notes/mixed.md"),
)


def _fixtures_for(session_number: int) -> tuple[str, ...]:
    return SESSION_FIXTURE_SETS[(session_number - 1) % len(SESSION_FIXTURE_SETS)]


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path):
    ws = Workspace(
        str(tmp_path / "data"),
        clock=fixed_clock(FIXED_TIME),
        asr_mode="mock",
        ocr_mode="mock",
    )
    yield ws
    ws.close()


@pytest.fixture
def course(workspace) -> dict[str, Any]:
    return workspace.create_course("Estructura de Dades", "EDA201", "ca")


def _register(workspace, course_id: str, session_id: str, names) -> None:
    for name in names:
        workspace.register_material(
            course_id, str(FIXTURES / name), session_id=session_id
        )


def _processed(workspace, course_id: str, session_number: int = 1) -> str:
    session = workspace.create_session(
        course_id, session_number=session_number, date=TODAY, title="Tema"
    )
    sid = session["session_id"]
    _register(workspace, course_id, sid, _fixtures_for(session_number))
    workspace.process_session(course_id, sid)
    return sid


@pytest.fixture
def processed_course(workspace, course):
    cid = course["course_id"]
    _processed(workspace, cid, 1)
    return cid


@pytest.fixture
def supported_kp(workspace, processed_course) -> dict[str, Any]:
    """一个 ``supported`` 的知识点（出题的合法前提）。"""
    for kp in workspace.knowledge_points(processed_course):
        if kp["validation_status"] == "supported":
            return kp
    pytest.skip("fixture produced no supported knowledge point")


@pytest.fixture
def student(workspace, processed_course) -> str:
    return workspace.create_student(processed_course, "s-adal", "Ada")["student_id"]


def _set_validation(workspace, course_id: str, kp_id: str, status: str) -> None:
    """把某个 KP 的 validation_status 改成目标值（走真实持久化 + 刷新）。

    直接改内存里的 KP 是**不够的**: ``register_knowledge_structure`` 用
    ``setdefault``, 已经注册过的 KP 不会被新 structure 覆盖。必须写
    knowledge structure 并让 ``reload_course`` 丢掉旧快照。
    """
    from src.knowledge_validation import ValidationStatus

    structure = workspace.persistence.load_knowledge_structure(course_id)
    kp = structure.knowledge_points.get(kp_id)
    if kp is None:
        # 有些 KP 只存在于组织层; 先把它同步进 structure。
        for point in workspace.knowledge_points(course_id):
            if point["knowledge_id"] == kp_id:
                from src.models import KnowledgePoint as KPModel

                kp = KPModel.from_dict(point)
                structure.knowledge_points[kp_id] = kp
                break
    assert kp is not None, f"knowledge point {kp_id!r} not found"
    kp.validation_status = ValidationStatus.from_string(status).value
    workspace.persistence.save_knowledge_structure(structure, course_id=course_id)
    workspace.reload_course(course_id)


# ---------------------------------------------------------------------------
# 1) 模板引擎：确定性 / 纯 ASCII 判据
# ---------------------------------------------------------------------------


class TestTemplateEngineDeterminism:
    def test_same_input_yields_the_same_draft_id(self, workspace, processed_course, supported_kp):
        kp_id = supported_kp["knowledge_id"]
        first = workspace.preview_exercise(processed_course, kp_id)
        second = workspace.preview_exercise(processed_course, kp_id)
        assert first["generated"] is True
        assert first["draft"]["draft_id"] == second["draft"]["draft_id"]

    def test_same_input_yields_the_same_every_field(
        self, workspace, processed_course, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        a = workspace.preview_exercise(processed_course, kp_id)["draft"]
        b = workspace.preview_exercise(processed_course, kp_id)["draft"]
        for field in (
            "prompt",
            "choices",
            "correct_choice_id",
            "expected_answer",
            "blank_id",
            "accepted_answers",
            "grounding",
            "evidence_ids",
        ):
            assert a[field] == b[field], field

    def test_generator_version_is_pinned(self, processed_course, supported_kp):
        gen = ExerciseTemplateGenerator()
        draft = gen.generate(supported_kp, [])
        assert not isinstance(draft, GenerationRefusal)
        assert draft.generator_version == GENERATOR_VERSION

    def test_no_wall_clock_or_randomness_in_the_identity(
        self, workspace, processed_course, supported_kp
    ):
        """同一份数据在两个不同工作区里必须给出同一个 draft_id。

        这是"没有 uuid4 / datetime.now"的可执行断言: 只要身份里掺进
        运行时的任何一点不稳定输入, 这条就会挂。
        """
        kp_id = supported_kp["knowledge_id"]
        import tempfile

        clone = Workspace(
            str(Path(tempfile.mkdtemp(prefix="t64clone-")) / "data"),
            clock=fixed_clock("2020-01-01T00:00:00+00:00"),
            asr_mode="mock",
            ocr_mode="mock",
        )
        try:
            cid = clone.create_course("Estructura de Dades", "EDA201", "ca")["course_id"]
            _processed(clone, cid, 1)
            clone_kp = next(
                k for k in clone.knowledge_points(cid)
                if k["knowledge_id"] == kp_id
            )
            here = workspace.preview_exercise(processed_course, kp_id)["draft"]
            there = clone.preview_exercise(cid, clone_kp["knowledge_id"])["draft"]
            assert here["draft_id"] == there["draft_id"]
        finally:
            clone.close()

    def test_seed_selects_the_template_reproducibly(self, supported_kp):
        gen = ExerciseTemplateGenerator()
        for seed in (0, 1, 2, 3):
            a = gen.generate(supported_kp, [], config=GenerationConfig(seed=seed))
            b = gen.generate(supported_kp, [], config=GenerationConfig(seed=seed))
            assert not isinstance(a, GenerationRefusal)
            assert not isinstance(b, GenerationRefusal)
            assert a.template == b.template
            assert a.draft_id == b.draft_id

    def test_different_seeds_can_select_different_templates(self, supported_kp):
        gen = ExerciseTemplateGenerator()
        seen = set()
        for seed in range(len(TemplateId)):
            draft = gen.generate(supported_kp, [], config=GenerationConfig(seed=seed))
            if not isinstance(draft, GenerationRefusal):
                seen.add(draft.template)
        assert len(seen) >= 2

    def test_batch_order_is_sorted_and_stable(self, workspace, processed_course):
        first = workspace.generate_exercises(processed_course, limit=3)
        second = workspace.generate_exercises(processed_course, limit=3)
        first_ids = [row["draft"]["knowledge_point_id"] for row in first["items"]]
        second_ids = [row["draft"]["knowledge_point_id"] for row in second["items"]]
        assert first_ids == sorted(first_ids)
        assert first_ids == second_ids


# ---------------------------------------------------------------------------
# 2) Grounding：生成器绝不能发明句子
# ---------------------------------------------------------------------------


class TestGroundingNeverInvents:
    def test_true_false_stem_is_a_verbatim_substring_of_the_knowledge(
        self, workspace, processed_course, supported_kp
    ):
        """题干去掉模板后缀后, 必须逐字出现在 KP 的文本里。"""
        draft = workspace.preview_exercise(processed_course, supported_kp["knowledge_id"])["draft"]
        assert draft["exercise_type"] == "true_false"
        stem = draft["prompt"].replace(" — True / False?", "")
        haystack = f"{supported_kp.get('title') or ''}\n{supported_kp.get('content') or ''}"
        assert stem in haystack, stem

    def test_multiple_choice_correct_option_is_verbatim(
        self, workspace, processed_course, supported_kp
    ):
        draft = workspace.preview_exercise(
            processed_course,
            supported_kp["knowledge_id"],
            config=GenerationConfig(template=TemplateId.MULTIPLE_CHOICE_DEFINITION),
        )["draft"]
        correct = next(
            c for c in draft["choices"] if c["choice_id"] == draft["correct_choice_id"]
        )
        haystack = f"{supported_kp.get('title') or ''}\n{supported_kp.get('content') or ''}"
        assert correct["text"] in haystack

    def test_multiple_choice_distractors_are_structural_only(
        self, workspace, processed_course, supported_kp
    ):
        """非正确选项只能来自固定的结构干扰项池, 不得是伪造的课程事实。"""
        draft = workspace.preview_exercise(
            processed_course,
            supported_kp["knowledge_id"],
            config=GenerationConfig(template=TemplateId.MULTIPLE_CHOICE_DEFINITION),
        )["draft"]
        for choice in draft["choices"]:
            if choice["choice_id"] == draft["correct_choice_id"]:
                continue
            assert choice["text"] in STRUCTURAL_DISTRACTORS, choice["text"]

    def test_short_answer_reference_is_the_knowledge_statement(
        self, workspace, processed_course, supported_kp
    ):
        draft = workspace.preview_exercise(
            processed_course,
            supported_kp["knowledge_id"],
            config=GenerationConfig(template=TemplateId.SHORT_ANSWER_DEFINITION),
        )["draft"]
        haystack = f"{supported_kp.get('title') or ''}\n{supported_kp.get('content') or ''}"
        assert draft["expected_answer"] in haystack

    def test_grounding_mentions_only_real_sources(
        self, workspace, processed_course, supported_kp
    ):
        draft = workspace.preview_exercise(processed_course, supported_kp["knowledge_id"])["draft"]
        allowed_prefixes = ("knowledge.", "template:", "evidence:")
        for field_name, source in draft["grounding"].items():
            assert source.startswith(allowed_prefixes), (field_name, source)

    def test_evidence_ids_come_from_the_knowledge_point(
        self, workspace, processed_course, supported_kp
    ):
        draft = workspace.preview_exercise(processed_course, supported_kp["knowledge_id"])["draft"]
        declared = {str(r) for r in (supported_kp.get("evidence_refs") or ())}
        assert set(draft["evidence_ids"]) <= declared
        assert draft["evidence_ids"], "a formal exercise must keep at least one evidence id"

    def test_generated_exercise_persists_its_evidence_ids(
        self, workspace, processed_course, supported_kp
    ):
        result = workspace.generate_exercise(processed_course, supported_kp["knowledge_id"])
        assert result["generated"] is True
        exercise = result["exercise"]
        assert exercise["evidence_ids"] == list(result["draft"]["evidence_ids"])
        assert exercise["knowledge_point_ids"] == [supported_kp["knowledge_id"]]


# ---------------------------------------------------------------------------
# 3) 完整 grounding 链
# ---------------------------------------------------------------------------


class TestGroundingChain:
    def test_chain_is_complete_kp_to_evidence_to_material(
        self, workspace, processed_course, supported_kp
    ):
        result = workspace.generate_exercise(processed_course, supported_kp["knowledge_id"])
        chain = workspace.exercise_grounding(processed_course, result["exercise_id"])
        assert chain["complete"] is True
        assert chain["unresolved"] == []
        assert [k["knowledge_id"] for k in chain["knowledge_points"]] == [
            supported_kp["knowledge_id"]
        ]
        assert chain["evidence"], "the chain must include the supporting evidence"
        assert chain["materials"], "every evidence must resolve to its material"

    def test_chain_material_matches_the_evidence_source(
        self, workspace, processed_course, supported_kp
    ):
        result = workspace.generate_exercise(processed_course, supported_kp["knowledge_id"])
        chain = workspace.exercise_grounding(processed_course, result["exercise_id"])
        material_ids = {m["material_id"] for m in chain["materials"]}
        for row in chain["evidence"]:
            assert (row["source"] or {}).get("material_id") in material_ids

    def test_broken_evidence_reference_is_reported_not_hidden(
        self, workspace, processed_course, supported_kp
    ):
        """故意造一道引用了不存在证据的练习: 链必须报 unresolved。"""
        exercise = workspace.create_exercise(
            processed_course,
            "true_false",
            "Referencia rota?",
            [supported_kp["knowledge_id"]],
            is_true=True,
            evidence_ids=["evid-does-not-exist"],
        )
        chain = workspace.exercise_grounding(processed_course, exercise["exercise_id"])
        assert "evidence:evid-does-not-exist" in chain["unresolved"]
        assert chain["complete"] is False

    def test_evidence_trace_reuses_the_same_chain(
        self, workspace, processed_course, supported_kp
    ):
        trace = workspace.knowledge_evidence_trace(
            processed_course, supported_kp["knowledge_id"]
        )
        assert trace["knowledge_point"]["knowledge_id"] == supported_kp["knowledge_id"]
        assert trace["evidence"]
        assert all("material" in row for row in trace["evidence"])

    def test_grounding_endpoint_rejects_unknown_exercise(
        self, workspace, processed_course
    ):
        from src.application.errors import NotFoundError

        with pytest.raises(NotFoundError):
            workspace.exercise_grounding(processed_course, "exercise-nope")


# ---------------------------------------------------------------------------
# 4) 拒绝路径：unverified / conflicted
# ---------------------------------------------------------------------------


class TestRefusals:
    def test_unverified_knowledge_is_refused_by_default(
        self, workspace, processed_course, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        _set_validation(workspace, processed_course, kp_id, "unverified")
        result = workspace.generate_exercise(processed_course, kp_id)
        assert result["generated"] is False
        assert result["refusal"]["reason"] == RefusalReason.UNVERIFIED_KNOWLEDGE.value
        assert result["refusal"]["recommended_action"]

    def test_unverified_refusal_returns_200_and_creates_nothing(
        self, workspace, processed_course, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        _set_validation(workspace, processed_course, kp_id, "unverified")
        before = len(workspace.list_exercises(processed_course))
        result = workspace.generate_exercise(processed_course, kp_id)
        assert result["generated"] is False
        assert len(workspace.list_exercises(processed_course)) == before

    def test_conflicted_knowledge_is_refused(
        self, workspace, processed_course, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        _set_validation(workspace, processed_course, kp_id, "conflicted")
        result = workspace.generate_exercise(processed_course, kp_id)
        assert result["generated"] is False
        assert result["refusal"]["reason"] == RefusalReason.CONFLICTED_KNOWLEDGE.value
        assert "resolve" in result["refusal"]["recommended_action"].lower()

    def test_allow_unverified_does_not_relax_conflict(
        self, workspace, processed_course, supported_kp
    ):
        """spec 64.11 只放宽 unverified；冲突必须先解决（64.12）。"""
        kp_id = supported_kp["knowledge_id"]
        _set_validation(workspace, processed_course, kp_id, "conflicted")
        result = workspace.generate_exercise(
            processed_course,
            kp_id,
            config=GenerationConfig(allow_unverified=True),
        )
        assert result["generated"] is False
        assert result["refusal"]["reason"] == RefusalReason.CONFLICTED_KNOWLEDGE.value

    def test_optional_practice_on_unverified_is_labeled(
        self, workspace, processed_course, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        _set_validation(workspace, processed_course, kp_id, "unverified")
        result = workspace.generate_exercise(
            processed_course,
            kp_id,
            config=GenerationConfig(allow_unverified=True),
        )
        assert result["generated"] is True
        assert result["draft"]["basis"] == ExerciseBasis.UNVERIFIED_MATERIAL.value
        assert result["banner"] == UNVERIFIED_PRACTICE_BANNER

    def test_formal_exercise_carries_no_warning_banner(
        self, workspace, processed_course, supported_kp
    ):
        result = workspace.generate_exercise(processed_course, supported_kp["knowledge_id"])
        assert result["draft"]["basis"] == ExerciseBasis.FORMAL.value
        assert result["banner"] is None

    def test_empty_knowledge_point_is_refused(self):
        gen = ExerciseTemplateGenerator()
        draft = gen.generate(
            {
                "knowledge_id": "kp-empty",
                "title": "",
                "content": "",
                "validation_status": "supported",
            },
            [],
        )
        assert isinstance(draft, GenerationRefusal)
        assert draft.reason == RefusalReason.NO_USABLE_STATEMENT

    def test_missing_knowledge_id_is_refused(self):
        gen = ExerciseTemplateGenerator()
        draft = gen.generate({"content": "Algo es algo mas largo."}, [])
        assert isinstance(draft, GenerationRefusal)
        assert draft.reason == RefusalReason.MISSING_KNOWLEDGE_POINT

    def test_refusal_is_a_result_not_an_exception(
        self, workspace, processed_course, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        _set_validation(workspace, processed_course, kp_id, "conflicted")
        # 不抛异常就是通过；返回值必须是可序列化的结构。
        result = workspace.generate_exercise(processed_course, kp_id)
        json.dumps(result)


# ---------------------------------------------------------------------------
# 5) 幂等 / 去重
# ---------------------------------------------------------------------------


class TestIdempotentGeneration:
    def test_generating_three_times_creates_exactly_one_exercise(
        self, workspace, processed_course, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        before = len(workspace.list_exercises(processed_course))
        first = workspace.generate_exercise(processed_course, kp_id)
        second = workspace.generate_exercise(processed_course, kp_id)
        third = workspace.generate_exercise(processed_course, kp_id)
        assert first["created"] is True
        assert second["created"] is False
        assert third["created"] is False
        assert first["exercise_id"] == second["exercise_id"] == third["exercise_id"]
        assert len(workspace.list_exercises(processed_course)) == before + 1

    def test_batch_twice_does_not_inflate_the_exercise_table(
        self, workspace, processed_course
    ):
        first = workspace.generate_exercises(processed_course)
        after_first = len(workspace.list_exercises(processed_course))
        second = workspace.generate_exercises(processed_course)
        after_second = len(workspace.list_exercises(processed_course))
        assert after_first == after_second
        assert second["created"] == 0
        assert first["created"] > 0

    def test_different_templates_produce_different_exercises(
        self, workspace, processed_course, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        ids = set()
        for template in (
            TemplateId.TRUE_FALSE_DEFINITION,
            TemplateId.MULTIPLE_CHOICE_DEFINITION,
            TemplateId.SHORT_ANSWER_DEFINITION,
        ):
            result = workspace.generate_exercise(
                processed_course, kp_id, config=GenerationConfig(template=template)
            )
            assert result["generated"] is True
            ids.add(result["exercise_id"])
        assert len(ids) == 3

    def test_preview_never_persists(self, workspace, processed_course, supported_kp):
        before = len(workspace.list_exercises(processed_course))
        for _ in range(5):
            workspace.preview_exercise(processed_course, supported_kp["knowledge_id"])
        assert len(workspace.list_exercises(processed_course)) == before


# ---------------------------------------------------------------------------
# 6) 三种题型
# ---------------------------------------------------------------------------


class TestExerciseTypes:
    def test_true_false_shape(self, workspace, processed_course, supported_kp):
        draft = workspace.preview_exercise(
            processed_course,
            supported_kp["knowledge_id"],
            config=GenerationConfig(template=TemplateId.TRUE_FALSE_DEFINITION),
        )["draft"]
        assert draft["exercise_type"] == "true_false"
        assert draft["correct_choice_id"] == "true"
        assert [c["choice_id"] for c in draft["choices"]] == ["true", "false"]

    def test_true_false_answer_is_the_verbatim_statement(
        self, workspace, processed_course, supported_kp
    ):
        draft = workspace.preview_exercise(
            processed_course,
            supported_kp["knowledge_id"],
            config=GenerationConfig(template=TemplateId.TRUE_FALSE_DEFINITION),
        )["draft"]
        assert draft["grounding"]["correct_choice_id"] == "knowledge.content"

    def test_multiple_choice_shape(self, workspace, processed_course, supported_kp):
        draft = workspace.preview_exercise(
            processed_course,
            supported_kp["knowledge_id"],
            config=GenerationConfig(template=TemplateId.MULTIPLE_CHOICE_DEFINITION),
        )["draft"]
        assert draft["exercise_type"] == "multiple_choice"
        assert len(draft["choices"]) >= 2
        ids = [c["choice_id"] for c in draft["choices"]]
        assert len(ids) == len(set(ids))
        assert draft["correct_choice_id"] in ids

    def test_multiple_choice_respects_max_options(self, workspace, processed_course, supported_kp):
        draft = workspace.preview_exercise(
            processed_course,
            supported_kp["knowledge_id"],
            config=GenerationConfig(
                template=TemplateId.MULTIPLE_CHOICE_DEFINITION, max_options=2
            ),
        )["draft"]
        assert len(draft["choices"]) == 2

    def test_short_answer_shape(self, workspace, processed_course, supported_kp):
        draft = workspace.preview_exercise(
            processed_course,
            supported_kp["knowledge_id"],
            config=GenerationConfig(template=TemplateId.SHORT_ANSWER_DEFINITION),
        )["draft"]
        assert draft["exercise_type"] == "short_answer"
        assert draft["expected_answer"]
        assert draft["choices"] == []

    def test_fill_blank_requires_an_occurring_term(self):
        gen = ExerciseTemplateGenerator()
        draft = gen.generate(
            {
                "knowledge_id": "kp-fb",
                "title": "Definicion",
                "content": "Un algoritmo transforma una entrada en una salida.",
                "original_terms": ["algoritmo"],
                "validation_status": "supported",
            },
            [],
            config=GenerationConfig(template=TemplateId.FILL_BLANK_TERM),
        )
        assert not isinstance(draft, GenerationRefusal)
        assert draft.exercise_type == "fill_blank"
        assert "____" in draft.prompt
        assert draft.accepted_answers == ("algoritmo",)

    def test_all_three_required_types_are_persistable(
        self, workspace, processed_course, supported_kp
    ):
        kp_id = supported_kp["knowledge_id"]
        for template, expected in (
            (TemplateId.TRUE_FALSE_DEFINITION, "true_false"),
            (TemplateId.MULTIPLE_CHOICE_DEFINITION, "multiple_choice"),
            (TemplateId.SHORT_ANSWER_DEFINITION, "short_answer"),
        ):
            result = workspace.generate_exercise(
                processed_course, kp_id, config=GenerationConfig(template=template)
            )
            assert result["generated"] is True, template
            assert result["exercise"]["exercise_type"] == expected
            # 落库后能被既有接口读回 —— 证明不是自娱自乐的 DTO。
            read_back = workspace.get_exercise(processed_course, result["exercise_id"])
            assert read_back["exercise_id"] == result["exercise_id"]

    def test_every_persisted_exercise_resolves_its_grounding(
        self, workspace, processed_course
    ):
        workspace.generate_exercises(processed_course)
        for exercise in workspace.list_exercises(processed_course):
            chain = workspace.exercise_grounding(processed_course, exercise["exercise_id"])
            assert chain["knowledge_points"], exercise["exercise_id"]
            assert chain["evidence"], exercise["exercise_id"]


# ---------------------------------------------------------------------------
# 7) 作答 -> 评估 -> StudentState（全部委托既有层）
# ---------------------------------------------------------------------------


class TestAnswerAndEvaluation:
    def test_true_false_correct_answer_is_evaluated_correct(
        self, workspace, processed_course, supported_kp, student
    ):
        result = workspace.generate_exercise(
            processed_course,
            supported_kp["knowledge_id"],
            config=GenerationConfig(template=TemplateId.TRUE_FALSE_DEFINITION),
        )
        submitted = workspace.exercise_submit(
            processed_course, student, result["exercise_id"], "true"
        )
        assert submitted["evaluation"]["status"] == "correct"
        assert submitted["evaluation"]["score"] == 1.0

    def test_true_false_wrong_answer_is_evaluated_incorrect(
        self, workspace, processed_course, supported_kp, student
    ):
        result = workspace.generate_exercise(
            processed_course,
            supported_kp["knowledge_id"],
            config=GenerationConfig(template=TemplateId.TRUE_FALSE_DEFINITION),
        )
        submitted = workspace.exercise_submit(
            processed_course, student, result["exercise_id"], "false"
        )
        assert submitted["evaluation"]["status"] == "incorrect"

    def test_short_answer_uses_the_existing_evaluator(
        self, workspace, processed_course, supported_kp, student
    ):
        result = workspace.generate_exercise(
            processed_course,
            supported_kp["knowledge_id"],
            config=GenerationConfig(template=TemplateId.SHORT_ANSWER_DEFINITION),
        )
        expected = result["exercise"]["expected_answer"]
        ok = workspace.exercise_submit(
            processed_course, student, result["exercise_id"], expected
        )
        assert ok["evaluation"]["status"] == "correct"

    def test_multiple_choice_uses_the_existing_evaluator(
        self, workspace, processed_course, supported_kp, student
    ):
        result = workspace.generate_exercise(
            processed_course,
            supported_kp["knowledge_id"],
            config=GenerationConfig(template=TemplateId.MULTIPLE_CHOICE_DEFINITION),
        )
        right = result["exercise"]["correct_choice_id"]
        ok = workspace.exercise_submit(
            processed_course, student, result["exercise_id"], right
        )
        assert ok["evaluation"]["status"] == "correct"

    def test_submission_response_carries_the_grounding_chain(
        self, workspace, processed_course, supported_kp, student
    ):
        result = workspace.generate_exercise(processed_course, supported_kp["knowledge_id"])
        submitted = workspace.exercise_submit(
            processed_course, student, result["exercise_id"], "true"
        )
        assert submitted["grounding"]["knowledge_points"]
        assert submitted["grounding"]["evidence"]

    def test_submitting_twice_is_idempotent(
        self, workspace, processed_course, supported_kp, student
    ):
        result = workspace.generate_exercise(processed_course, supported_kp["knowledge_id"])
        first = workspace.exercise_submit(
            processed_course, student, result["exercise_id"], "true"
        )
        second = workspace.exercise_submit(
            processed_course, student, result["exercise_id"], "true"
        )
        assert first["answer"]["answer_id"] == second["answer"]["answer_id"]
        assert first["evaluation"]["evaluation_id"] == second["evaluation"]["evaluation_id"]

    def test_evaluation_records_attempts_without_rewriting_the_state_machine(
        self, workspace, processed_course, supported_kp, student
    ):
        """作答 -> 评估会登记计数, 但**不会**把它当成"掌握"。

        这是 Task 30 的刻意语义: ``state`` 只由学习事件
        (``viewed`` / ``practiced`` / ``reviewed``) 驱动, 作答计数单独
        存放。所以"答对一题"不等于"已掌握" —— 本测试正是钉住这一点。
        """
        kp_id = supported_kp["knowledge_id"]

        def record_of(payload) -> dict[str, Any]:
            for row in payload.get("states") or ():
                if row.get("knowledge_point_id") == kp_id:
                    return row
            return {}

        before = record_of(workspace.student_state(processed_course, student))
        result = workspace.generate_exercise(processed_course, kp_id)
        workspace.exercise_submit(processed_course, student, result["exercise_id"], "true")
        after = record_of(workspace.student_state(processed_course, student))

        assert after.get("answer_count") == 1
        assert after.get("correct_count") == 1
        assert after.get("state") == "not_started", (
            "作答绝不能自行把状态推到 practicing/reviewing"
        )
        assert before.get("answer_count", 0) == 0

    def test_explicit_learning_events_are_what_move_the_state(
        self, workspace, processed_course, supported_kp, student
    ):
        """状态机只认学习事件 —— 补上合法事件链后状态才变。

        注意 ``practiced`` **不能**从 ``not_started`` 直接跳: Task 30 的
        ``_TRANSITIONS`` 只允许 ``viewed -> practiced -> reviewed``。
        这正好进一步证明出题层没有绕过既有状态机。
        """
        kp_id = supported_kp["knowledge_id"]

        def state_of() -> str:
            for row in workspace.student_state(processed_course, student).get("states") or ():
                if row.get("knowledge_point_id") == kp_id:
                    return str(row.get("state") or "not_started")
            return "not_started"

        workspace.generate_exercise(processed_course, kp_id)
        # 答题本身不推进状态
        assert state_of() == "not_started"
        # 非法跳转不生效 (viewed 是 practiced 的前置)
        workspace.record_learning_event(processed_course, student, kp_id, "practiced")
        assert state_of() == "not_started"
        # 合法链才生效
        workspace.record_learning_event(processed_course, student, kp_id, "viewed")
        assert state_of() in ("exposed", "practicing")
        workspace.record_learning_event(processed_course, student, kp_id, "practiced")
        assert state_of() == "reviewing" or state_of() == "practicing"

    def test_workflow_never_writes_student_state_itself(
        self, workspace, processed_course, supported_kp, student
    ):
        """只生成不出题 -> 学生状态一动不动（出题不是学习行为）。"""
        before = workspace.student_state(processed_course, student)
        workspace.generate_exercise(processed_course, supported_kp["knowledge_id"])
        after = workspace.student_state(processed_course, student)
        assert before == after

    def test_evaluation_does_not_touch_the_knowledge_base(
        self, workspace, processed_course, supported_kp, student
    ):
        before = workspace.knowledge_points(processed_course)
        result = workspace.generate_exercise(processed_course, supported_kp["knowledge_id"])
        workspace.exercise_submit(processed_course, student, result["exercise_id"], "false")
        after = workspace.knowledge_points(processed_course)
        assert before == after


# ---------------------------------------------------------------------------
# 8) 课程隔离
# ---------------------------------------------------------------------------


class TestCourseIsolation:
    @pytest.fixture
    def two_courses(self, workspace):
        first = workspace.create_course("Curso A", "A1", "es")["course_id"]
        _register(
            workspace, first, _new_session(workspace, first, 1), _fixtures_for(1)
        )
        workspace.process_session(
            first, workspace.list_sessions(first)[-1]["session_id"]
        )
        second = workspace.create_course("Curso B", "B1", "ca")["course_id"]
        _register(
            workspace, second, _new_session(workspace, second, 1), _fixtures_for(2)
        )
        workspace.process_session(
            second, workspace.list_sessions(second)[-1]["session_id"]
        )
        return first, second

    def test_generated_exercise_belongs_to_its_course(self, workspace, two_courses):
        first, second = two_courses
        kp = next(
            k for k in workspace.knowledge_points(first)
            if k["validation_status"] == "supported"
        )
        result = workspace.generate_exercise(first, kp["knowledge_id"])
        if not result["generated"]:
            pytest.skip("fixture produced no generatable knowledge point")
        assert result["exercise"]["course_id"] == first
        # B 课看不见它
        assert result["exercise_id"] not in {
            e["exercise_id"] for e in workspace.list_exercises(second)
        }

    def test_cross_course_generation_is_rejected(self, workspace, two_courses):
        """用 A 课的 KP id 去 B 课出题 -> 404, 绝不静默生成。"""
        from src.application.errors import NotFoundError

        first, second = two_courses
        kp_a = workspace.knowledge_points(first)[0]["knowledge_id"]
        if kp_a in {k["knowledge_id"] for k in workspace.knowledge_points(second)}:
            pytest.skip("fixture shares this knowledge point between courses")
        with pytest.raises(NotFoundError):
            workspace.generate_exercise(second, kp_a)

    def test_identical_material_in_two_courses_keeps_membership_disjoint(
        self, workspace
    ):
        """knowledge_id 是内容寻址的, 所以两课 id 可能相同 —— 隔离靠成员关系。"""
        cid_a = workspace.create_course("Same A", "SA", "es")["course_id"]
        sid_a = _new_session(workspace, cid_a, 1)
        _register(workspace, cid_a, sid_a, _fixtures_for(1))
        workspace.process_session(cid_a, sid_a)

        cid_b = workspace.create_course("Same B", "SB", "es")["course_id"]
        sid_b = _new_session(workspace, cid_b, 1)
        _register(workspace, cid_b, sid_b, _fixtures_for(1))
        workspace.process_session(cid_b, sid_b)

        ids_a = {k["knowledge_id"] for k in workspace.knowledge_points(cid_a)}
        ids_b = {k["knowledge_id"] for k in workspace.knowledge_points(cid_b)}
        # 相同材料 -> 相同 id（记录这个事实, 免得日后误以为隔离靠 id）
        assert ids_a == ids_b
        # 但练习永远归属创建它的那门课
        kp_id = sorted(ids_a)[0]
        result = workspace.generate_exercise(cid_a, kp_id)
        if result["generated"]:
            assert result["exercise"]["course_id"] == cid_a
            assert workspace.list_exercises(cid_b) == []

    def test_grounding_chain_never_leaks_another_course(self, workspace, two_courses):
        first, second = two_courses
        kp = workspace.knowledge_points(first)[0]
        workspace.generate_exercise(first, kp["knowledge_id"])
        for exercise in workspace.list_exercises(first):
            chain = workspace.exercise_grounding(first, exercise["exercise_id"])
            assert chain["course_id"] == first
            for material in chain["materials"]:
                assert material["material_id"] in {
                    m["material_id"] for m in workspace.list_materials(first)
                }


def _new_session(workspace, course_id: str, number: int) -> str:
    return workspace.create_session(
        course_id, session_number=number, date=TODAY, title="Tema"
    )["session_id"]


# ---------------------------------------------------------------------------
# 9) HTTP 契约
# ---------------------------------------------------------------------------


class _Client:
    def __init__(self, base: str) -> None:
        self.base = base

    def request(self, path: str, *, method: str = "GET", body: Any = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def get(self, path: str):
        return self.request(path)

    def post(self, path: str, body: Any):
        return self.request(path, method="POST", body=body)


@pytest.fixture
def client(workspace):
    instance = create_server(workspace, port=0)
    instance.start()
    try:
        yield _Client(instance.url)
    finally:
        instance.stop()


class TestApiContract:
    def test_preview_endpoint(self, client, workspace, processed_course, supported_kp):
        status, payload = client.get(
            f"/api/exercise-generation/preview?course_id={processed_course}"
            f"&knowledge_point_id={supported_kp['knowledge_id']}"
        )
        assert status == 200, payload
        assert payload["data"]["generated"] is True
        assert payload["data"]["draft"]["draft_id"]

    def test_generate_endpoint_returns_201_then_200(
        self, client, processed_course, supported_kp
    ):
        path = "/api/exercise-generation"
        body = {
            "course_id": processed_course,
            "knowledge_point_id": supported_kp["knowledge_id"],
        }
        status, payload = client.post(path, body)
        assert status == 201, payload
        assert payload["data"]["created"] is True
        status2, payload2 = client.post(path, body)
        assert status2 == 200, payload2
        assert payload2["data"]["created"] is False
        assert payload2["data"]["exercise_id"] == payload["data"]["exercise_id"]

    def test_batch_endpoint(self, client, processed_course):
        status, payload = client.post(
            "/api/exercise-generation/batch",
            {"course_id": processed_course, "limit": 3},
        )
        assert status == 200, payload
        assert payload["data"]["requested"] <= 3

    def test_grounding_endpoint(self, client, processed_course, supported_kp):
        _, created = client.post(
            "/api/exercise-generation",
            {
                "course_id": processed_course,
                "knowledge_point_id": supported_kp["knowledge_id"],
            },
        )
        eid = created["data"]["exercise_id"]
        status, payload = client.get(
            f"/api/exercises/{eid}/grounding?course_id={processed_course}"
        )
        assert status == 200, payload
        assert payload["data"]["knowledge_points"]

    def test_start_endpoint_withholds_the_answer(self, client, processed_course, supported_kp):
        _, created = client.post(
            "/api/exercise-generation",
            {
                "course_id": processed_course,
                "knowledge_point_id": supported_kp["knowledge_id"],
            },
        )
        eid = created["data"]["exercise_id"]
        status, payload = client.get(
            f"/api/exercises/{eid}/start?course_id={processed_course}"
        )
        assert status == 200, payload
        assert payload["data"]["answer_withheld"] is True

    def test_submit_endpoint_returns_the_evaluation(
        self, client, processed_course, supported_kp, student
    ):
        _, created = client.post(
            "/api/exercise-generation",
            {
                "course_id": processed_course,
                "knowledge_point_id": supported_kp["knowledge_id"],
            },
        )
        status, payload = client.post(
            "/api/exercise-workflow/submit",
            {
                "course_id": processed_course,
                "student_id": student,
                "exercise_id": created["data"]["exercise_id"],
                "submitted_value": "true",
            },
        )
        assert status == 201, payload
        assert payload["data"]["evaluation"]["status"] in ("correct", "incorrect")

    def test_unknown_generation_option_is_400(self, client, processed_course, supported_kp):
        status, payload = client.post(
            "/api/exercise-generation",
            {
                "course_id": processed_course,
                "knowledge_point_id": supported_kp["knowledge_id"],
                "use_llm": True,
            },
        )
        assert status == 400, payload
        assert "use_llm" in json.dumps(payload)

    def test_unknown_template_is_400(self, client, processed_course, supported_kp):
        status, _ = client.post(
            "/api/exercise-generation",
            {
                "course_id": processed_course,
                "knowledge_point_id": supported_kp["knowledge_id"],
                "template": "nope",
            },
        )
        assert status == 400

    def test_refusal_is_200_not_an_error(self, client, workspace, processed_course, supported_kp):
        kp_id = supported_kp["knowledge_id"]
        _set_validation(workspace, processed_course, kp_id, "conflicted")
        status, payload = client.post(
            "/api/exercise-generation",
            {"course_id": processed_course, "knowledge_point_id": kp_id},
        )
        assert status == 200, payload
        assert payload["data"]["generated"] is False
        assert payload["data"]["refusal"]["reason"] == "conflicted_knowledge"

    def test_evidence_trace_endpoint(self, client, processed_course, supported_kp):
        status, payload = client.get(
            f"/api/knowledge/{supported_kp['knowledge_id']}/evidence-trace"
            f"?course_id={processed_course}"
        )
        assert status == 200, payload
        assert payload["data"]["evidence"]


# ---------------------------------------------------------------------------
# 10) 重启一致性（真子进程）
# ---------------------------------------------------------------------------


class TestRestartConsistency:
    def test_generated_exercises_survive_a_real_subprocess_restart(
        self, workspace, processed_course, supported_kp, student
    ):
        generated = workspace.generate_exercise(
            processed_course, supported_kp["knowledge_id"]
        )
        assert generated["generated"] is True
        submitted = workspace.exercise_submit(
            processed_course, student, generated["exercise_id"], "true"
        )
        expected = {
            "exercise_id": generated["exercise_id"],
            "draft_id": generated["draft"]["draft_id"],
            "answer_id": submitted["answer"]["answer_id"],
            "evaluation_id": submitted["evaluation"]["evaluation_id"],
        }
        data_dir = str(workspace.data_dir)
        workspace.close()

        script = _RESTART_SCRIPT.format(
            data_dir=json.dumps(data_dir),
            course_id=json.dumps(processed_course),
            kp_id=json.dumps(supported_kp["knowledge_id"]),
            fixed_time=json.dumps(FIXED_TIME),
            project=json.dumps(str(Path(__file__).resolve().parents[1])),
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr
        replayed = json.loads(completed.stdout.strip().splitlines()[-1])
        assert replayed["exercise_id"] == expected["exercise_id"]
        assert replayed["draft_id"] == expected["draft_id"]
        assert replayed["answer_id"] == expected["answer_id"]
        assert replayed["evaluation_id"] == expected["evaluation_id"]
        assert replayed["grounding_complete"] is True


_RESTART_SCRIPT = """
import json, sys
sys.path.insert(0, {project})
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace

ws = Workspace({data_dir}, clock=fixed_clock({fixed_time}), asr_mode="mock", ocr_mode="mock")
try:
    course_id = {course_id}
    kp_id = {kp_id}
    # 1) 重新生成同一道题: 幂等必须跨进程成立
    regen = ws.generate_exercise(course_id, kp_id)
    chain = ws.exercise_grounding(course_id, regen["exercise_id"])
    # 2) 既有评估层读回的 answer / evaluation 必须与进程 A 一致
    sid = ws.list_students(course_id)[0]["student_id"]
    recent = ws.context(course_id).learning_view._recent_evaluations(sid)
    answer_id = recent[0]["answer_id"] if recent else None
    evaluation_id = ws.get_evaluation(course_id, answer_id)["evaluation_id"] if answer_id else None
    print(json.dumps({{
        "exercise_id": regen["exercise_id"],
        "draft_id": regen["draft"]["draft_id"],
        "answer_id": answer_id,
        "evaluation_id": evaluation_id,
        "grounding_complete": chain["complete"],
    }}))
finally:
    ws.close()
"""
