# -*- coding: utf-8 -*-
"""Task 47.10 — Review Safety Audit / Task 47.11 — Student State Safety Audit。

spec 47.10 原文::

    CONFLICTED 绝不能因为 API / UI / processing / restart / database reload
    而自动变成 CONFIRMED, 除非存在明确 ReviewRecord。

spec 47.11 原文::

    student answer 不能直接修改 KnowledgePoint truth。
    学生错误回答只能影响学习状态 / evaluation。

实现说明: 本文件直接推进 ``AcceptanceHarness`` 的步骤方法, 而不是调
``run()`` —— 因为审计需要**审核之前**的状态快照, 而 ``run()`` 是一口气跑完
17 步 (含自动审核) 的原子操作。harness 本身就是给测试用的验收工具, 其步骤
是确定性的。
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from src.application.acceptance import AcceptanceHarness, ClassroomDataset
from src.application.errors import InvalidInputError
from src.application.workspace import Workspace

DATASET_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "acceptance"

#: 推进到"证据/校验完成、尚未做任何人工审核"为止。
STEPS_TO_VALIDATION = (
    "_step_create_course",
    "_step_create_session",
    "_step_upload_documents",
    "_step_upload_audio",
    "_step_upload_board_image",
    "_step_process",
    "_step_evidence",
    "_step_knowledge",
    "_step_validation",
)

#: 再往前推到"已有练习、尚未答题"。
STEPS_TO_EXERCISE = STEPS_TO_VALIDATION + (
    "_step_review",
    "_step_coverage",
    "_step_student",
    "_step_exercise",
)

PENDING = "pending"
CONFIRMED = "confirmed"
CONFLICTED = "conflicted"


@pytest.fixture(scope="module")
def dataset() -> ClassroomDataset:
    return ClassroomDataset.from_directory(str(DATASET_DIR))


def _advance(harness: AcceptanceHarness, steps: tuple[str, ...]) -> None:
    for name in steps:
        getattr(harness, name)()


@pytest.fixture
def pre_review(tmp_path, dataset) -> AcceptanceHarness:
    """跑到 validation 为止 (尚无任何人工审核)。"""
    harness = AcceptanceHarness(dataset, data_dir=str(tmp_path / "data"))
    _advance(harness, STEPS_TO_VALIDATION)
    return harness


@pytest.fixture
def pre_answer(tmp_path, dataset) -> AcceptanceHarness:
    """跑到"有练习、尚未答题"为止 (审核已完成)。"""
    harness = AcceptanceHarness(dataset, data_dir=str(tmp_path / "data"))
    _advance(harness, STEPS_TO_EXERCISE)
    return harness


def _conflicted_points(harness: AcceptanceHarness) -> list[dict[str, Any]]:
    return [
        kp
        for kp in harness.workspace.knowledge_points(harness.course_id)
        if str(kp["validation_status"]) == CONFLICTED
    ]


def _knowledge_truth(harness: AcceptanceHarness) -> dict[str, tuple]:
    """知识点的**真值**快照: 内容 / 校验状态 / 置信度 / 分数 / 证据引用。

    刻意不含学习态相关字段 —— 那些本来就该随学生行为变化。
    """
    return {
        kp["knowledge_id"]: (
            kp["content"],
            str(kp["validation_status"]),
            str(kp["confidence"]),
            kp["knowledge_score"],
            tuple(kp.get("evidence_refs") or ()),
        )
        for kp in harness.workspace.knowledge_points(harness.course_id)
    }


# ----------------------------------------------------------------------
# 47.10 Review Safety Audit
# ----------------------------------------------------------------------


def test_fixture_actually_contains_a_conflict(pre_review) -> None:
    """审计前提: 夹具必须真的产出 CONFLICTED 知识点, 否则整套断言是空转。"""
    conflicted = _conflicted_points(pre_review)
    assert conflicted, "夹具没有产生任何冲突, 47.10 的审计无从谈起"
    assert pre_review.workspace.conflicts(pre_review.course_id)


def test_conflicted_points_start_pending_not_confirmed(pre_review) -> None:
    """没有任何 ReviewRecord 时, 冲突点必须是 PENDING。"""
    conflicted = _conflicted_points(pre_review)
    assert conflicted
    for kp in conflicted:
        assert str(kp["review_status"]) == PENDING, (
            f"{kp['knowledge_id']} 在无人审核时就不是 PENDING"
        )
        assert kp["needs_verification"] is True


def test_reading_through_the_api_does_not_confirm(pre_review) -> None:
    """API / UI 的**读**操作不得改变审核状态 (读不能产生副作用)。"""
    before = {kp["knowledge_id"]: str(kp["review_status"]) for kp in _conflicted_points(pre_review)}
    # UI 首页 + 审核面板会反复读这些端点
    for _ in range(3):
        pre_review.workspace.review_candidates(pre_review.course_id)
        pre_review.workspace.knowledge_points(pre_review.course_id)
        pre_review.workspace.knowledge_summary(pre_review.course_id)
        pre_review.workspace.dashboard(pre_review.course_id)
    after = {kp["knowledge_id"]: str(kp["review_status"]) for kp in _conflicted_points(pre_review)}
    assert after == before
    assert set(after.values()) == {PENDING}


def test_confirm_is_rejected_for_a_conflicted_point(pre_review) -> None:
    """核心防线: 冲突点不能用 confirm 处理, 必须显式选择信任哪一侧证据。"""
    conflicted = _conflicted_points(pre_review)
    assert conflicted
    for kp in conflicted:
        with pytest.raises(InvalidInputError) as excinfo:
            pre_review.workspace.review_confirm(
                pre_review.course_id, kp["knowledge_id"], note="should be refused"
            )
        assert "CONFLICTED" in str(excinfo.value)

    # 被拒绝之后状态不能被"顺手"改掉
    for kp in _conflicted_points(pre_review):
        assert str(kp["review_status"]) == PENDING


def test_reprocessing_does_not_confirm(pre_review) -> None:
    """processing 重跑 (幂等) 不得把冲突点"处理"成 CONFIRMED。"""
    before = {kp["knowledge_id"]: str(kp["review_status"]) for kp in _conflicted_points(pre_review)}
    pre_review.workspace.process_session(pre_review.course_id, pre_review.session_id)
    for material in pre_review.workspace.list_materials(pre_review.course_id):
        pre_review.workspace.process_material(pre_review.course_id, material["material_id"])
    after = {kp["knowledge_id"]: str(kp["review_status"]) for kp in _conflicted_points(pre_review)}
    assert after == before
    assert set(after.values()) == {PENDING}


def test_reprocessing_does_not_reopen_a_human_decision(pre_review) -> None:
    """人工复核决定是"黏"的: 重跑处理不得把已确认的知识点打回待审。

    反过来的行为 —— 每次重新处理都把复核队列清空 —— 是真实缺陷: 用户重跑
    一次"处理", 之前逐条确认过的知识点全部回到 PENDING, 人工劳动被静默
    丢弃, 而且没有任何提示。领域层 ``KnowledgeReviewService`` 对此的规定是
    明确的 (``get_review_candidates`` 的 docstring): 只有**新证据或新冲突**
    到达时才把决定重新打开。

    这条测试是 Task 68 装配作用域修复的对照面 —— 作用域改小之后, 装配不再
    每次从空结构重建, 人工决定才真正留得住。
    """
    workspace = pre_review.workspace
    course_id = pre_review.course_id
    supported = [
        kp
        for kp in workspace.knowledge_points(course_id)
        if str(kp["validation_status"]) == "supported"
    ]
    assert supported, "夹具里应当有 SUPPORTED 的知识点可供人工确认"
    target = str(supported[0]["knowledge_id"])
    workspace.review_confirm(course_id, target, note="human decision")

    # 同一批材料再处理一遍 (整节课 + 逐个材料, 覆盖两条装配入口)。
    workspace.process_session(course_id, pre_review.session_id)
    for material in workspace.list_materials(course_id):
        workspace.process_material(course_id, material["material_id"])

    after = {
        str(kp["knowledge_id"]): str(kp["review_status"])
        for kp in workspace.knowledge_points(course_id)
    }
    assert after[target] == CONFIRMED, "重跑处理把人的确认打回去了"
    reopened = {
        str(c.get("knowledge_point_id") or c.get("knowledge_id"))
        for c in workspace.review_candidates(course_id)
    }
    assert target not in reopened


def test_restart_does_not_confirm(pre_review, dataset) -> None:
    """restart / database reload 之后, 冲突点绝不允许"自动"变成 CONFIRMED。

    重启侧是一个**全新的 Workspace** 打开同一个 data_dir, 重建课程并重放处理。
    无论 ReviewRecord 是否落盘, 结论都只能是"仍是待审" —— 因为重启本身不是
    一次人工决定。
    """
    restarted = Workspace(
        pre_review.data_dir,
        asr_provider=pre_review.asr_provider,
        ocr_engine=pre_review.ocr_engine,
        asr_mode="mock",
        ocr_mode="mock",
    )
    course = restarted.create_course(
        dataset.course_name, dataset.course_code, dataset.course_language
    )
    session = restarted.create_session(
        str(course["course_id"]),
        session_number=dataset.session_number,
        date=dataset.session_date,
        title=dataset.session_title,
    )
    restarted.process_session(str(course["course_id"]), str(session["session_id"]))
    for material in restarted.list_materials(str(course["course_id"])):
        restarted.process_material(str(course["course_id"]), material["material_id"])

    reloaded = restarted.knowledge_points(str(course["course_id"]))
    assert reloaded, "重启侧没有重建出任何知识点, 断言会退化成空转"
    conflicted = [kp for kp in reloaded if str(kp["validation_status"]) == CONFLICTED]
    assert conflicted, "重启侧丢失了冲突状态本身"
    for kp in conflicted:
        assert str(kp["review_status"]) != CONFIRMED, (
            f"{kp['knowledge_id']} 在重启后自动变成了 CONFIRMED"
        )
        assert str(kp["review_status"]) == PENDING


def test_only_an_explicit_review_record_confirms_a_conflicted_point(pre_review) -> None:
    """唯一合法的转正路径: 显式 resolve_conflict, 且必须留下 ReviewRecord。"""
    kp = _conflicted_points(pre_review)[0]
    knowledge_id = kp["knowledge_id"]
    assert pre_review.workspace.review_history(pre_review.course_id, knowledge_id) == []

    conflict = pre_review.workspace.conflicts(pre_review.course_id)[0]
    selected = sorted(str(ref) for ref in conflict["evidence_refs"])[:1]
    assert selected, "冲突里没有可选证据侧, 测试前提不成立"

    pre_review.workspace.review_resolve_conflict(
        pre_review.course_id,
        knowledge_id,
        selected_evidence_ids=selected,
        note="47.10 audit",
    )

    refreshed = pre_review.workspace.knowledge_point(pre_review.course_id, knowledge_id)
    assert str(refreshed["review_status"]) == CONFIRMED
    history = pre_review.workspace.review_history(pre_review.course_id, knowledge_id)
    assert history, "确认之后必须存在 ReviewRecord"


def test_every_confirmed_point_has_a_review_record(tmp_path, dataset) -> None:
    """全量检查: 任何一个 CONFIRMED 的知识点都必须有人工审核记录。"""
    harness = AcceptanceHarness(dataset, data_dir=str(tmp_path / "data"))
    harness.run()
    confirmed = [
        kp
        for kp in harness.workspace.knowledge_points(harness.course_id)
        if str(kp["review_status"]) == CONFIRMED
    ]
    assert confirmed, "验收流水线应当产生已确认的知识点"
    missing = [
        kp["knowledge_id"]
        for kp in confirmed
        if not harness.workspace.review_history(harness.course_id, kp["knowledge_id"])
    ]
    assert not missing, f"{len(missing)} 个知识点被确认却没有 ReviewRecord: {missing[:5]}"


# ----------------------------------------------------------------------
# 47.11 Student State Safety Audit
# ----------------------------------------------------------------------


def test_correct_answer_does_not_mutate_knowledge_truth(pre_answer) -> None:
    """答题 (即使是正确答案) 也不得改动知识点真值。"""
    before = _knowledge_truth(pre_answer)
    assert before, "没有知识点, 断言会退化成空转"

    pre_answer.workspace.submit_answer(
        pre_answer.course_id, pre_answer.student_id, pre_answer.exercise_id, "a", sequence=1
    )
    assert _knowledge_truth(pre_answer) == before


def test_wrong_answer_does_not_mutate_knowledge_truth(pre_answer) -> None:
    """spec 点名: **错误**回答只能影响学习状态 / evaluation, 不能改真值。"""
    before = _knowledge_truth(pre_answer)
    result = pre_answer.workspace.submit_answer(
        pre_answer.course_id, pre_answer.student_id, pre_answer.exercise_id, "b", sequence=1
    )
    assert str(result.get("evaluation_status")) in {"incorrect", "partial", "unsupported"}
    assert _knowledge_truth(pre_answer) == before


def test_repeated_wrong_answers_never_accumulate_into_truth(pre_answer) -> None:
    """连错多次也不得"累积"成对真值的修改 (每次都要重比)。"""
    before = _knowledge_truth(pre_answer)
    for sequence in range(1, 6):
        pre_answer.workspace.submit_answer(
            pre_answer.course_id,
            pre_answer.student_id,
            pre_answer.exercise_id,
            "b",
            sequence=sequence,
        )
        assert _knowledge_truth(pre_answer) == before, f"第 {sequence} 次错误回答改动了真值"


def test_wrong_answer_only_touches_learning_state(pre_answer) -> None:
    """错误回答必须**确实**产生学习侧影响 —— 否则这条安全边界是"什么都没做"。"""
    before_state = pre_answer.workspace.student_state(
        pre_answer.course_id, pre_answer.student_id
    )
    pre_answer.workspace.submit_answer(
        pre_answer.course_id, pre_answer.student_id, pre_answer.exercise_id, "b", sequence=1
    )
    after_state = pre_answer.workspace.student_state(
        pre_answer.course_id, pre_answer.student_id
    )
    assert after_state != before_state, "错误回答没有留下任何学习侧痕迹"


def test_student_state_is_scoped_to_the_answering_student(pre_answer) -> None:
    """一个学生的答题不得改动另一个学生的学习状态。"""
    other = pre_answer.workspace.create_student(
        pre_answer.course_id, "student-47-11-other", "Otro Estudiante"
    )
    other_before = pre_answer.workspace.student_state(
        pre_answer.course_id, other["student_id"]
    )
    pre_answer.workspace.submit_answer(
        pre_answer.course_id, pre_answer.student_id, pre_answer.exercise_id, "b", sequence=1
    )
    other_after = pre_answer.workspace.student_state(
        pre_answer.course_id, other["student_id"]
    )
    assert other_after == other_before
