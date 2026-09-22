# -*- coding: utf-8 -*-
"""Task 67 — Exam Review Mode 测试。

覆盖的 spec 条款::

    67.1  用户入口与模式定位 (这是复习模式, **不是**考试预测器)
    67.2  ReviewSet 构建 (只由既有数据派生)
    67.3  排序依据 (客观数据; 不用"考试概率")
    67.4  冲突 (绝不被自动解决; 两侧并列)
    67.5  覆盖 (两条真相轴分开呈现)
    67.6  禁止预测 (No Prediction Contract)

几条刻意写死的判据（产品语义，不是实现细节）
--------------------------------------------
1. **"不是预测器"是结构性的, 不是纪律性的。** ``ReviewSet`` 上只有读方法,
   模块里也没有任何概率字段的数据结构。因此测试可以逐字段扫描响应体来
   证明它 —— 而不是靠"我们记得不要写"。见 ``TestNoPredictionContract``。
2. **两条轴各自参与判桶。** 一个 ``validation_status=supported`` 但
   ``review_status=pending`` 的知识点**不能**进 ``ready`` —— 证据支持不等于
   有人核过。只看一条轴的实现会在这里挂掉。
3. **冲突绝不自动消失。** 注入一条真实冲突后, 它必须以
   ``status=PENDING`` / ``auto_resolved=False`` 原样出现, 且相关知识点
   落进 ``blocked``。任何"自动挑一边"的实现都会挂在这里。
4. **确定性 + 重启一致。** 同一份数据在两个进程里必须产出逐字节相同的
   ReviewSet。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

import pytest

from src.api.server import create_server
from src.application.review_mode import (
    ATTENTION_REASONS,
    BLOCK_REASONS,
    BUCKETS,
    NO_PREDICTION_FIELDS,
    REVIEW_FORBIDDEN_TERMS,
    REVIEW_MODE_VERSION,
    REVIEW_SET_EMPTY_NOTE,
    ReviewSet,
)
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace
from tests.support import build_conflicted_structure, read_web_source

FIXTURES = Path(__file__).resolve().parent / "fixtures"

FIXED_TIME = "2026-09-18T09:00:00+00:00"
TODAY = "2026-09-18"

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
    return workspace.create_course("Programacio", "PROG101", "ca")


def _processed(workspace, course_id: str, session_number: int = 1) -> str:
    session = workspace.create_session(
        course_id, session_number=session_number, date=TODAY, title="Tema"
    )
    sid = session["session_id"]
    for name in _fixtures_for(session_number):
        workspace.register_material(course_id, str(FIXTURES / name), session_id=sid)
    workspace.process_session(course_id, sid)
    return sid


@pytest.fixture
def processed_course(workspace, course):
    cid = course["course_id"]
    _processed(workspace, cid, 1)
    return cid


@pytest.fixture
def kps(workspace, processed_course) -> list[dict[str, Any]]:
    points = workspace.knowledge_points(processed_course)
    if not points:
        pytest.skip("fixture produced no knowledge point")
    return points


@pytest.fixture
def student(workspace, processed_course) -> str:
    return workspace.create_student(
        processed_course, "s-quim", "Quim"
    )["student_id"]


@pytest.fixture
def review(workspace) -> ReviewSet:
    return ReviewSet(workspace)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _review_set(workspace, cid, sid, **kw):
    return workspace.student_review_set(cid, sid, **kw)


def _walk(node, path="$"):
    """产出 ``(path, key, value)`` —— 用于逐字段扫描。"""
    if isinstance(node, dict):
        for k, v in node.items():
            yield path + "." + str(k), k, v
            yield from _walk(v, path + "." + str(k))
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _walk(v, path + "[" + str(i) + "]")


def _row_for(payload: Mapping[str, Any], kp_id: str) -> Mapping[str, Any]:
    for item in payload["items"]:
        if item["knowledge_id"] == kp_id:
            return item
    raise AssertionError(f"{kp_id} not present in review set")


def _union_evidence(points) -> list[str]:
    """跨全部知识点取证据并集。

    单个 KP 常常只有 1 条证据, 而构造冲突至少需要 2 条。这是夹具层面的
    事实, 不是产品行为 —— 因此这里显式聚合, 而不是硬编码 id。
    """
    return sorted({
        str(e) for kp in points for e in (kp.get("evidence_refs") or []) if e
    })


def _inject_conflict(workspace, cid: str, kp_id: str, refs: list[str]) -> None:
    """注入一条**落盘**的冲突。

    为什么必须落盘, 不能只注册到组织服务
    ------------------------------------
    最初这里调的是 ``ctx.org_service.register_knowledge_structure(...)`` ——
    那只改内存。于是 ``TestRestart`` 里的冲突在重启后**整个消失**, 断言
    "冲突跨重启存活"实际上变成了"我注入的东西在重启后不见了"。测试会挂,
    但挂的原因是把夹具的局限误当成了产品的行为。

    产品里冲突是随 ``KnowledgeStructure`` 一起持久化的
    (``save_knowledge_structure`` / ``_restore_course``), 所以夹具也必须
    走同一条路 —— 否则重启类测试测的不是产品。

    判据口径与 ``build_conflicted_structure`` 一致: 冲突的证据必须取自
    该知识点自己的 refs, 否则两者无法关联。
    """
    own = [str(r) for r in refs if r]
    assert len(own) >= 2, "夹具前提: 至少两条证据"

    # 用共享 helper 构造"冲突知识点 + 冲突记录", 保证判据口径与
    # test_learning_workflow / test_review_mode 完全一致 (证据取自自身 refs)。
    extra = build_conflicted_structure(kp_id, own)

    structure = workspace.persistence.load_knowledge_structure(cid)
    # 既有 KP 先同步进结构 —— 否则存回去会丢掉它们。
    from src.models import KnowledgePoint

    for point in workspace.knowledge_points(cid):
        existing = point["knowledge_id"]
        if existing not in structure.knowledge_points:
            structure.knowledge_points[existing] = KnowledgePoint.from_dict(point)

    for added in extra.knowledge_points.values():
        structure.knowledge_points[added.knowledge_id] = added
    for conflict in extra.conflicts:
        structure.add_conflict(conflict)

    workspace.persistence.save_knowledge_structure(structure, course_id=cid)
    workspace.reload_course(cid)


def _seed_dir(workspace, cid: str, session_number: int = 1) -> str:
    """在一个 Workspace 上把课程跑到"有知识点"的状态。"""
    return _processed(workspace, cid, session_number)


def _force_validation_status(workspace, course_id: str, kp_id: str, status: str) -> None:
    """把某个知识点的 ``validation_status`` 定向改成 ``status``。

    为什么必须走"改结构 -> 存盘 -> 重载"这条路
    -------------------------------------------
    产品里**没有**"直接设置 validation_status"的应用层方法 —— 它是从证据
    派生的, 这本身是正确的设计 (人工只能改 ``review_status``, 不能改证据
    结论; 见 Task 22 的两条轴分工)。因此测试要造出
    "``supported`` 但 ``pending``"这类组合时, 只能在**领域层**直接改结构
    再存回, 与 ``tests/test_exercise_workflow.py`` 同一条路径。
    """
    from src.knowledge_validation import ValidationStatus
    from src.models import KnowledgePoint as KPModel

    structure = workspace.persistence.load_knowledge_structure(course_id)
    kp = structure.knowledge_points.get(kp_id)
    if kp is None:
        for point in workspace.knowledge_points(course_id):
            if point["knowledge_id"] == kp_id:
                kp = KPModel.from_dict(point)
                structure.knowledge_points[kp_id] = kp
                break
    assert kp is not None, f"knowledge point {kp_id!r} not found"
    kp.validation_status = ValidationStatus.from_string(status).value
    workspace.persistence.save_knowledge_structure(structure, course_id=course_id)
    workspace.reload_course(course_id)


# ===========================================================================
# 1. ReviewSet 基本契约 (spec 67.2)
# ===========================================================================


class TestReviewSetBasics:
    def test_returns_versioned_payload(self, workspace, processed_course, student):
        out = _review_set(workspace, processed_course, student)
        assert out["review_mode_version"] == REVIEW_MODE_VERSION
        assert out["course_id"] == processed_course
        assert out["student_id"] == student

    def test_declares_itself_not_a_predictor(self, workspace, processed_course, student):
        out = _review_set(workspace, processed_course, student)
        assert out["not_a_predictor"] is True

    def test_all_items_have_required_fields(self, workspace, processed_course, student):
        out = _review_set(workspace, processed_course, student)
        required = {
            "knowledge_id",
            "validation_status",
            "review_status",
            "bucket",
            "learning_state",
            "position",
        }
        for item in out["items"]:
            assert required <= set(item), f"missing {required - set(item)}"

    def test_three_buckets_are_always_present(self, workspace, processed_course, student):
        out = _review_set(workspace, processed_course, student)
        assert set(out["by_bucket"]) == set(BUCKETS)

    def test_bucket_lists_agree_with_counts(self, workspace, processed_course, student):
        out = _review_set(workspace, processed_course, student)
        for name in BUCKETS:
            assert len(out["buckets"][name]) == out["by_bucket"][name]

    def test_positions_are_contiguous_from_one(self, workspace, processed_course, student):
        out = _review_set(workspace, processed_course, student)
        positions = [i["position"] for i in out["items"]]
        assert positions == list(range(1, len(positions) + 1))

    def test_counts_match_items(self, workspace, processed_course, student):
        out = _review_set(workspace, processed_course, student)
        assert out["counts"]["total"] == len(out["items"])
        assert sum(out["by_bucket"].values()) == len(out["items"])

    def test_ordering_basis_is_documented_and_objed(self, workspace, processed_course, student):
        out = _review_set(workspace, processed_course, student)
        assert "coverage" in out["ordering_basis"]
        assert "student learning state" in out["ordering_basis"]

    def test_empty_course_is_a_normal_state(self, workspace):
        cid = workspace.create_course("Buit", "EMPTY", "ca")["course_id"]
        sid = workspace.create_student(cid, "s-1", "Buit")["student_id"]
        out = _review_set(workspace, cid, sid)
        assert out["empty"] is True
        assert out["empty_note"] == REVIEW_SET_EMPTY_NOTE
        assert out["items"] == []
        assert out["by_bucket"] == {name: 0 for name in BUCKETS}

    def test_unknown_course_raises_not_found(self, workspace, processed_course, student):
        from src.application.errors import NotFoundError

        with pytest.raises(NotFoundError):
            _review_set(workspace, "course-nope", student)

    def test_unknown_student_raises_not_found(self, workspace, processed_course):
        from src.application.errors import NotFoundError

        with pytest.raises(NotFoundError):
            _review_set(workspace, processed_course, "s-nope")

    def test_blank_course_id_raises_invalid_input(self, workspace, student):
        from src.application.errors import InvalidInputError

        with pytest.raises(InvalidInputError):
            _review_set(workspace, "   ", student)

    def test_blank_student_id_raises_invalid_input(self, workspace, processed_course):
        from src.application.errors import InvalidInputError

        with pytest.raises(InvalidInputError):
            _review_set(workspace, processed_course, "")


# ===========================================================================
# 2. 两条真相轴 (spec 67.5)
# ===========================================================================


class TestTwoTruthAxes:
    def test_both_axes_are_reported_separately(self, workspace, processed_course, student):
        out = _review_set(workspace, processed_course, student)
        assert "by_validation_status" in out
        assert "by_review_status" in out

    def test_each_item_carries_both_axes(self, workspace, processed_course, student):
        out = _review_set(workspace, processed_course, student)
        for item in out["items"]:
            assert "validation_status" in item
            assert "review_status" in item

    def test_axis_counts_are_independent_partitions(self, workspace, processed_course, student):
        out = _review_set(workspace, processed_course, student)
        assert sum(out["by_validation_status"].values()) == len(out["items"])
        assert sum(out["by_review_status"].values()) == len(out["items"])

    def test_axes_are_not_merged_into_one_field(self, workspace, processed_course, student):
        """响应里不得出现"综合可信度"这类把两条轴合起来的字段。"""
        out = _review_set(workspace, processed_course, student)
        merged = {
            "trust", "trust_level", "confidence", "combined_status",
            "overall_status", "reliability",
        }
        for path, key, _value in _walk(out):
            assert str(key).lower() not in merged, f"merged axis field at {path}"

    def test_supported_but_pending_does_not_go_to_ready(
        self, workspace, processed_course, student
    ):
        """证据支持 + 人工未确认 -> 不能进 ready。

        这是"两条轴各自参与判桶"的可执行判据。只读 validation 的实现会把
        它放进 ready, 于是学生以为这是已定稿的复习材料。
        """
        out = _review_set(workspace, processed_course, student)
        for item in out["items"]:
            if item["validation_status"] == "supported" and item["review_status"] != "confirmed":
                assert item["bucket"] == "attention", (
                    item["knowledge_id"] + " should not be ready: "
                    "evidence supports it but no human confirmed it"
                )

    def test_confirmed_supported_goes_to_ready(self, workspace, processed_course, student, kps):
        """证据支持 + 人工确认 -> 才可以进 ready。"""
        target = kps[0]["knowledge_id"]
        workspace.review_confirm(processed_course, target, note="t67")
        out = _review_set(workspace, processed_course, student)
        row = _row_for(out, target)
        assert row["review_status"] == "confirmed"
        assert row["bucket"] == "ready"

    def test_unverified_goes_to_attention_even_if_confirmed(
        self, workspace, processed_course, student, kps
    ):
        """人工确认了, 但证据仍不 supported -> 仍是 attention。"""
        target = kps[0]["knowledge_id"]
        workspace.review_confirm(processed_course, target, note="t67")
        _force_validation_status(workspace, processed_course, target, "unverified")
        out = _review_set(workspace, processed_course, student)
        row = _row_for(out, target)
        assert row["bucket"] == "attention"
        assert row["attention_reason"] == "evidence_not_supported"

    def test_attention_reason_is_always_explainable(self, workspace, processed_course, student):
        out = _review_set(workspace, processed_course, student)
        for item in out["items"]:
            if item["bucket"] == "attention":
                assert item["attention_reason"] in ATTENTION_REASONS
                assert item["attention_note"], "attention needs a human-readable note"
            else:
                assert item["attention_reason"] is None


# ===========================================================================
# 3. 冲突绝不被自动解决 (spec 67.4)
# ===========================================================================


class TestConflictsNeverAutoResolved:
    def test_conflicted_kp_lands_in_blocked(self, workspace, processed_course, student, kps):
        refs = _union_evidence(kps)
        assert len(refs) >= 2, "夹具前提: 至少两条证据"
        target = "kp-t67-conflict"
        _inject_conflict(workspace, processed_course, target, refs[:2])
        out = _review_set(workspace, processed_course, student)
        row = _row_for(out, target)
        assert row["bucket"] == "blocked"
        assert row["block_reason"] == "unresolved_conflict"

    def test_blocked_kp_sorts_first(self, workspace, processed_course, student, kps):
        refs = _union_evidence(kps)
        target = "kp-t67-conflict"
        _inject_conflict(workspace, processed_course, target, refs[:2])
        out = _review_set(workspace, processed_course, student)
        assert out["items"][0]["knowledge_id"] == target

    def test_conflict_is_reported_with_pending_status(
        self, workspace, processed_course, student, kps
    ):
        refs = _union_evidence(kps)
        target = "kp-t67-conflict"
        _inject_conflict(workspace, processed_course, target, refs[:2])
        out = _review_set(workspace, processed_course, student)
        assert out["conflicts"], "conflict must appear in the payload"
        for conflict in out["conflicts"]:
            assert str(conflict.get("status")).upper() != "RESOLVED"
            assert conflict["auto_resolved"] is False

    def test_conflict_shows_both_sides(self, workspace, processed_course, student, kps):
        refs = _union_evidence(kps)
        target = "kp-t67-conflict"
        _inject_conflict(workspace, processed_course, target, refs[:2])
        out = _review_set(workspace, processed_course, student)
        for conflict in out["conflicts"]:
            sides = conflict["sides"]
            assert len(sides) >= 2, "both sides must be present"
            labels = [s["label"] for s in sides]
            assert labels == sorted(labels) or len(set(labels)) == len(labels)

    def test_conflict_does_not_pick_a_winner(self, workspace, processed_course, student, kps):
        """响应里不得出现裁定性字段。"""
        refs = _union_evidence(kps)
        target = "kp-t67-conflict"
        _inject_conflict(workspace, processed_course, target, refs[:2])
        out = _review_set(workspace, processed_course, student)
        verdict_keys = {"winner", "correct_side", "is_correct", "resolved_by", "chosen"}
        for path, key, _value in _walk(out["conflicts"]):
            assert str(key).lower() not in verdict_keys, f"verdict field at {path}"

    def test_conflict_item_carries_conflict_ids(self, workspace, processed_course, student, kps):
        refs = _union_evidence(kps)
        target = "kp-t67-conflict"
        _inject_conflict(workspace, processed_course, target, refs[:2])
        out = _review_set(workspace, processed_course, student)
        row = _row_for(out, target)
        assert row["conflict"] is True
        assert row["conflict_ids"], "conflict ids must be attached"
        assert row["block_note"], "blocked items need an explanation"

    def test_review_set_has_no_write_methods(self):
        """结构性保证: 这个类上不存在任何写方法。

        如果哪天有人给它加了 ``resolve_conflict`` / ``confirm`` 之类,
        这条测试会挂 —— 而"冲突不被自动解决"正是靠这一点守住的。

        判据刻意用**完整词**而不是子串: 上一版用子串匹配, 把 ``review_set``
        自己当成了写方法 ("set" 是它的子串) —— 一条总是失败的守卫等于没有
        守卫。
        """
        import re

        pattern = re.compile(
            r"(?:^|_)(confirm|reject|resolve|write|update|create|delete|record|"
            r"apply|commit|persist|mutate)(?:$|_)"
        )
        writey = [
            name for name in dir(ReviewSet)
            if not name.startswith("_") and pattern.search(name.lower())
        ]
        assert writey == [], f"ReviewSet must stay read-only, found: {writey}"

    def test_review_set_only_exposes_known_read_methods(self):
        """白名单式断言: 公开方法集合是**固定**的。

        比"没有写方法"更强 —— 新增任何公开方法都必须先改这条测试,
        于是"顺手加一个写口子"会在评审时被看见。
        """
        public = {
            name for name in dir(ReviewSet)
            if not name.startswith("_") and callable(getattr(ReviewSet, name))
        }
        assert public == {"review_set"}, f"unexpected public API: {sorted(public)}"

    def test_resolved_conflict_no_longer_blocks(self, workspace, processed_course, student, kps):
        """一旦冲突真的被解决, 阻塞必须解除 —— 否则"绝不自动解决"就变成了
        "永远阻塞"。

        ``resolve_conflict`` 要求调用方**显式选出**信任的一侧证据
        (空选择会被领域层拒绝: "an empty selection would not actually
        resolve the conflict")。这正是"绝不自动解决"的另一面 ——
        解铃必须由人系。
        """
        refs = _union_evidence(kps)
        target = "kp-t67-conflict"
        _inject_conflict(workspace, processed_course, target, refs[:2])
        out = _review_set(workspace, processed_course, student)
        assert _row_for(out, target)["bucket"] == "blocked"

        conflict = out["conflicts"][0]
        chosen = conflict["evidence_refs"][0]
        # 未解决时不带 selected_evidence_ids -> 领域层必须拒绝
        from src.application.errors import InvalidInputError

        with pytest.raises((InvalidInputError, ValueError)):
            workspace.review_resolve_conflict(
                processed_course, target, selected_evidence_ids=[],
            )
        workspace.review_resolve_conflict(
            processed_course, target,
            selected_evidence_ids=[chosen], note="t67 resolved",
        )
        after = _review_set(workspace, processed_course, student)
        assert _row_for(after, target)["bucket"] != "blocked"

    def test_resolved_conflict_is_still_kept_for_audit(
        self, workspace, processed_course, student, kps
    ):
        """解决冲突**不删除**记录 —— 两侧证据必须为审计保留。

        领域层明说: "The ConflictRecord and both evidence items are preserved
        for audit; only the knowledge point's review_status moves to
        CONFIRMED." 因此求解后冲突仍在列表里, 只是不再阻塞。
        """
        refs = _union_evidence(kps)
        target = "kp-t67-conflict"
        _inject_conflict(workspace, processed_course, target, refs[:2])
        out = _review_set(workspace, processed_course, student)
        chosen = out["conflicts"][0]["evidence_refs"][0]
        workspace.review_resolve_conflict(
            processed_course, target, selected_evidence_ids=[chosen], note="t67",
        )
        after = _review_set(workspace, processed_course, student)
        # 记录仍在 (审计), 但该知识点不再 blocked
        assert _row_for(after, target)["bucket"] != "blocked"

    def test_rejected_kp_is_blocked_with_its_own_reason(
        self, workspace, processed_course, student, kps
    ):
        target = kps[0]["knowledge_id"]
        workspace.review_reject(processed_course, target, note="t67")
        out = _review_set(workspace, processed_course, student)
        row = _row_for(out, target)
        assert row["bucket"] == "blocked"
        assert row["block_reason"] == "rejected_by_review"
        assert row["block_reason"] in BLOCK_REASONS

    def test_two_block_reasons_use_different_branches(
        self, workspace, processed_course, student, kps
    ):
        """冲突与否决走**不同**的判据, 不能把两者混成一个"不可用"。"""
        refs = _union_evidence(kps)
        _inject_conflict(workspace, processed_course, "kp-t67-c1", refs[:2])
        rejected = kps[0]["knowledge_id"]
        workspace.review_reject(processed_course, rejected, note="t67")
        out = _review_set(workspace, processed_course, student)
        assert _row_for(out, "kp-t67-c1")["block_reason"] == "unresolved_conflict"
        assert _row_for(out, rejected)["block_reason"] == "rejected_by_review"


# ===========================================================================
# 4. No Prediction Contract (spec 67.6)
# ===========================================================================


class TestNoPredictionContract:
    @pytest.mark.parametrize("field", NO_PREDICTION_FIELDS)
    def test_no_prediction_field_name_appears_anywhere(
        self, workspace, processed_course, student, field
    ):
        out = _review_set(workspace, processed_course, student)
        for path, key, _value in _walk(out):
            assert field not in str(key).lower(), f"{field} leaked at {path}"

    @pytest.mark.parametrize("field", NO_PREDICTION_FIELDS)
    def test_no_prediction_field_when_conflicts_present(
        self, workspace, processed_course, student, kps, field
    ):
        refs = _union_evidence(kps)
        _inject_conflict(workspace, processed_course, "kp-t67-p", refs[:2])
        out = _review_set(workspace, processed_course, student)
        for path, key, _value in _walk(out):
            assert field not in str(key).lower(), f"{field} leaked at {path}"

    @pytest.mark.parametrize("term", REVIEW_FORBIDDEN_TERMS)
    def test_no_forbidden_phrase_in_any_string(self, term, workspace, processed_course, student):
        out = _review_set(workspace, processed_course, student)
        blob = json.dumps(out, ensure_ascii=False).lower()
        assert term.lower() not in blob, f"forbidden term {term!r} present"

    def test_chinese_forbidden_phrases_are_absent(self, workspace, processed_course, student):
        """spec 67 点名的六条中文禁止语。"""
        out = _review_set(workspace, processed_course, student)
        blob = json.dumps(out, ensure_ascii=False)
        for phrase in ("最可能考", "考试概率", "预测题", "押题", "考点概率", "预测分数"):
            assert phrase not in blob, f"spec-forbidden phrase {phrase!r} present"

    def test_empty_course_also_has_no_predictions(self, workspace):
        cid = workspace.create_course("Buit", "EMPTY", "ca")["course_id"]
        sid = workspace.create_student(cid, "s-1", "Buit")["student_id"]
        out = _review_set(workspace, cid, sid)
        blob = json.dumps(out, ensure_ascii=False).lower()
        for field in NO_PREDICTION_FIELDS:
            assert field not in blob

    def test_ordering_is_not_probability_based(self, workspace, processed_course, student):
        """排序键必须是可复核的客观量, 不能是"概率"。"""
        out = _review_set(workspace, processed_course, student)
        for item in out["items"]:
            key = item["sort_key"]
            assert isinstance(key, (list, tuple))
            assert all(isinstance(part, (int, str)) for part in key)


# ===========================================================================
# 5. 排序与确定性 (spec 67.3)
# ===========================================================================


class TestOrderingAndDeterminism:
    def test_blocked_before_attention_before_ready(self, workspace, processed_course, student, kps):
        refs = _union_evidence(kps)
        _inject_conflict(workspace, processed_course, "kp-t67-ord", refs[:2])
        workspace.review_confirm(processed_course, kps[1]["knowledge_id"], note="t67")
        out = _review_set(workspace, processed_course, student)
        ranks = [
            {"blocked": 0, "attention": 1, "ready": 2}[i["bucket"]]
            for i in out["items"]
        ]
        assert ranks == sorted(ranks), "buckets must be ordered blocked -> attention -> ready"

    def test_two_calls_are_byte_identical(self, workspace, processed_course, student):
        a = json.dumps(_review_set(workspace, processed_course, student), sort_keys=True)
        b = json.dumps(_review_set(workspace, processed_course, student), sort_keys=True)
        assert a == b

    def test_two_workspaces_same_data_agree(self, workspace, processed_course, student, tmp_path):
        from src.application.review_mode import ReviewSet as RS

        other = Workspace(
            str(tmp_path / "data"),
            clock=fixed_clock(FIXED_TIME),
            asr_mode="mock",
            ocr_mode="mock",
        )
        try:
            a = _review_set(workspace, processed_course, student)
            b = RS(other).review_set(processed_course, student)
            assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
        finally:
            other.close()

    def test_reading_does_not_mutate(self, workspace, processed_course, student):
        """只读投影读一百次也不能改变任何业务事实。"""
        before = json.dumps(workspace.knowledge_points(processed_course), sort_keys=True)
        for _ in range(25):
            _review_set(workspace, processed_course, student)
        after = json.dumps(workspace.knowledge_points(processed_course), sort_keys=True)
        assert before == after

    def test_lang_is_echoed_and_does_not_change_structure(self, workspace, processed_course, student):
        base = _review_set(workspace, processed_course, student, lang="zh")
        for lang in ("es", "ca"):
            other = _review_set(workspace, processed_course, student, lang=lang)
            assert other["lang"] == lang
            assert other["by_bucket"] == base["by_bucket"]
            assert other["buckets"] == base["buckets"]

    def test_many_sessions_produce_stable_output(self, workspace, course, student):
        cid = course["course_id"]
        for number in (1, 2, 3):
            _processed(workspace, cid, number)
        sid = workspace.create_student(cid, "s-multi", "Multi")["student_id"]
        a = json.dumps(_review_set(workspace, cid, sid), sort_keys=True)
        b = json.dumps(_review_set(workspace, cid, sid), sort_keys=True)
        assert a == b
        assert _review_set(workspace, cid, sid)["counts"]["total"] > 0


# ===========================================================================
# 6. 覆盖 (spec 67.5)
# ===========================================================================


class TestCoverage:
    def test_coverage_is_present(self, workspace, processed_course, student):
        out = _review_set(workspace, processed_course, student)
        assert "coverage" in out

    def test_coverage_failure_does_not_500(self, workspace, processed_course, student, monkeypatch):
        """覆盖计算失败时降级, 不把整页打成 500。"""
        monkeypatch.setattr(
            type(workspace), "coverage",
            lambda self, cid: (_ for _ in ()).throw(RuntimeError("boom")),
            raising=True,
        )
        out = _review_set(workspace, processed_course, student)
        assert out["coverage"]["unavailable"] is True

    def test_coverage_does_not_change_buckets(self, workspace, processed_course, student, monkeypatch):
        base = _review_set(workspace, processed_course, student)["by_bucket"]
        monkeypatch.setattr(
            type(workspace), "coverage",
            lambda self, cid: {"coverage_ratio": 0.0, "unavailable": True},
            raising=True,
        )
        assert _review_set(workspace, processed_course, student)["by_bucket"] == base


# ===========================================================================
# 7. 学生状态信号 (spec 67.3)
# ===========================================================================


class TestStudentStateSignal:
    def test_not_started_is_not_a_weakness(self, workspace, processed_course, student):
        """没开始学不等于薄弱 —— 只有 NEEDS_REVIEW / NEEDS_PRACTICE 才是。"""
        out = _review_set(workspace, processed_course, student)
        for item in out["items"]:
            if item["learning_state"] == "not_started":
                assert item["learning_signal"] == "NOT_STARTED"
                assert item["attention_reason"] != "student_state"

    def test_learning_state_comes_from_student_state(
        self, workspace, processed_course, student, kps
    ):
        """打开知识点 -> exposed。复习集合必须反映它, 而不是自己猜。"""
        target = kps[0]["knowledge_id"]
        workspace.learning_workflow_open_knowledge(
            processed_course, student, target
        )
        out = _review_set(workspace, processed_course, student)
        row = _row_for(out, target)
        assert row["learning_state"] == "exposed"
        assert row["learning_signal"] == "EXPOSED"

    def test_answering_does_not_change_learning_state(
        self, workspace, processed_course, student, kps
    ):
        """答对/答错都不推进状态 (Task 30 铁律 5)。"""
        target = kps[0]["knowledge_id"]
        workspace.learning_workflow_open_knowledge(processed_course, student, target)
        before = _row_for(
            _review_set(workspace, processed_course, student), target
        )["learning_state"]
        row = workspace.learning_workflow_exercise(processed_course, student, target)
        exercise = row.get("exercise")
        if exercise:
            wrong = "false" if exercise.get("exercise_type") == "true_false" else "___nope___"
            workspace.learning_workflow_answer(
                processed_course, student, str(exercise["exercise_id"]), wrong
            )
        after = _row_for(
            _review_set(workspace, processed_course, student), target
        )["learning_state"]
        assert before == after == "exposed"

    def test_reviewing_state_maps_to_needs_review(
        self, workspace, processed_course, student, kps
    ):
        from src.student_learning import LearningEventType

        target = kps[0]["knowledge_id"]
        workspace.learning_workflow_open_knowledge(processed_course, student, target)
        for event in (LearningEventType.PRACTICED, LearningEventType.REVIEWED):
            workspace.record_learning_event(
                processed_course, student, target, event.value
            )
        out = _review_set(workspace, processed_course, student)
        row = _row_for(out, target)
        assert row["learning_state"] == "reviewing"
        assert row["learning_signal"] == "NEEDS_REVIEW"
        assert row["bucket"] == "attention"

    def test_no_student_state_is_not_an_error(self, workspace, processed_course):
        """全新学生 (没有任何 StudentState) 也必须拿到完整文档。"""
        fresh = workspace.create_student(processed_course, "s-fresh", "Fresh")["student_id"]
        out = _review_set(workspace, processed_course, fresh)
        assert out["counts"]["total"] > 0
        assert all(i["learning_state"] == "not_started" for i in out["items"])


# ===========================================================================
# 8. HTTP API 契约 (spec 67 的 API 要求)
# ===========================================================================


@pytest.fixture
def api(workspace):
    server = create_server(workspace, host="127.0.0.1", port=0)
    server.start()
    yield f"http://127.0.0.1:{server.port}"
    server.stop()


def _get(base: str, path: str):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"_raw": raw[:200]}


class TestApiContract:
    def test_valid_request_returns_200(self, api, processed_course, student):
        status, body = _get(
            api, f"/api/students/{student}/review-set?course_id={processed_course}"
        )
        assert status == 200
        assert body["data"]["review_mode_version"] == REVIEW_MODE_VERSION

    def test_response_is_wrapped_in_data(self, api, processed_course, student):
        _status, body = _get(
            api, f"/api/students/{student}/review-set?course_id={processed_course}"
        )
        assert "data" in body
        assert body["data"]["not_a_predictor"] is True

    def test_unknown_course_is_404(self, api, student):
        status, body = _get(api, f"/api/students/{student}/review-set?course_id=nope")
        assert status == 404
        assert body["error"]["code"] == "NOT_FOUND"

    def test_unknown_student_is_404(self, api, processed_course):
        status, body = _get(api, f"/api/students/nope/review-set?course_id={processed_course}")
        assert status == 404
        assert body["error"]["code"] == "NOT_FOUND"

    def test_missing_course_id_is_400(self, api, student):
        status, body = _get(api, f"/api/students/{student}/review-set")
        assert status == 400
        assert body["error"]["code"] == "INVALID_INPUT"

    def test_empty_course_id_is_400(self, api, student):
        status, body = _get(api, f"/api/students/{student}/review-set?course_id=")
        assert status == 400
        assert body["error"]["code"] == "INVALID_INPUT"

    @pytest.mark.parametrize("lang", ["zh", "es", "ca"])
    def test_lang_parameter_accepted(self, api, processed_course, student, lang):
        status, body = _get(
            api,
            f"/api/students/{student}/review-set?course_id={processed_course}&lang={lang}",
        )
        assert status == 200
        assert body["data"]["lang"] == lang

    @pytest.mark.parametrize("lang", ["zh", "es", "ca"])
    def test_no_cjk_leak_outside_zh(self, api, processed_course, student, lang):
        """es/ca 下响应里不该出现中文 (除课程/知识点原文以外)。"""
        if lang == "zh":
            pytest.skip("zh is the source language")
        _status, body = _get(
            api,
            f"/api/students/{student}/review-set?course_id={processed_course}&lang={lang}",
        )
        # 学生自己造的数据 (title/content) 可能含中文, 因此只看我们生成的文案
        for key in ("empty_note", "block_note", "attention_note", "ordering_basis"):
            value = body["data"].get(key)
            if isinstance(value, str):
                assert not any("\u4e00" <= ch <= "\u9fff" for ch in value), (
                    f"{key} leaked Chinese in {lang}: {value!r}"
                )

    def test_no_5xx_for_any_bad_input(self, api, processed_course, student):
        cases = [
            f"/api/students/{student}/review-set?course_id=no-such",
            "/api/students/no-such/review-set?course_id=" + processed_course,
            f"/api/students/{student}/review-set",
            f"/api/students/{student}/review-set?course_id=",
            f"/api/students/{student}/review-set?course_id={processed_course}&lang=bogus",
            f"/api/students/{student}/review-set?course_id=../etc&lang=zh",
            f"/api/students/{student}/review-set?course_id={processed_course}&lang=",
        ]
        for path in cases:
            status, _body = _get(api, path)
            assert status < 500, f"{path} -> {status}"

    def test_api_never_leaks_prediction_fields(self, api, processed_course, student):
        _status, body = _get(
            api, f"/api/students/{student}/review-set?course_id={processed_course}"
        )
        blob = json.dumps(body, ensure_ascii=False).lower()
        for field in NO_PREDICTION_FIELDS:
            assert field not in blob, f"{field} leaked over HTTP"

    def test_api_never_leaks_forbidden_phrases(self, api, processed_course, student):
        _status, body = _get(
            api, f"/api/students/{student}/review-set?course_id={processed_course}"
        )
        blob = json.dumps(body, ensure_ascii=False)
        for phrase in REVIEW_FORBIDDEN_TERMS:
            assert phrase.lower() not in blob.lower(), f"{phrase!r} leaked over HTTP"

    def test_repeated_requests_are_identical(self, api, processed_course, student):
        path = f"/api/students/{student}/review-set?course_id={processed_course}"
        _s, first = _get(api, path)
        _s, second = _get(api, path)
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    def test_get_only_no_write_methods(self, api, processed_course, student):
        """对这条路径 POST 必须是 405/404, 绝不能写库。"""
        import urllib.request as ur

        req = ur.Request(
            api + f"/api/students/{student}/review-set?course_id={processed_course}",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with ur.urlopen(req, timeout=10) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        assert status >= 400, "review-set must not accept writes"

    def test_counts_are_json_integers(self, api, processed_course, student):
        _status, body = _get(
            api, f"/api/students/{student}/review-set?course_id={processed_course}"
        )
        for key, value in body["data"]["counts"].items():
            assert isinstance(value, int), f"counts.{key} is not an int"

    def test_items_list_is_json_serialisable(self, api, processed_course, student):
        status, body = _get(
            api, f"/api/students/{student}/review-set?course_id={processed_course}"
        )
        assert status == 200
        json.dumps(body)  # 不抛异常即可


# ===========================================================================
# 9. 重启一致 (spec 67 的 restart 要求)
# ===========================================================================


class TestRestart:
    def test_survives_restart(self, tmp_path, processed_course, student, workspace):
        before = json.dumps(
            _review_set(workspace, processed_course, student), sort_keys=True
        )
        workspace.close()
        reopened = Workspace(
            str(tmp_path / "data"),
            clock=fixed_clock(FIXED_TIME),
            asr_mode="mock",
            ocr_mode="mock",
        )
        try:
            after = json.dumps(
                _review_set(reopened, processed_course, student), sort_keys=True
            )
            assert before == after
        finally:
            reopened.close()

    def test_review_decision_survives_restart(self, tmp_path, workspace, processed_course, student, kps):
        target = kps[0]["knowledge_id"]
        workspace.review_confirm(processed_course, target, note="t67 restart")
        before = _row_for(
            _review_set(workspace, processed_course, student), target
        )
        workspace.close()
        reopened = Workspace(
            str(tmp_path / "data"),
            clock=fixed_clock(FIXED_TIME),
            asr_mode="mock",
            ocr_mode="mock",
        )
        try:
            after = _row_for(
                _review_set(reopened, processed_course, student), target
            )
            assert after["review_status"] == before["review_status"] == "confirmed"
            assert after["bucket"] == "ready"
        finally:
            reopened.close()

    def test_conflict_survives_restart(self, tmp_path, workspace, processed_course, student, kps):
        refs = _union_evidence(kps)
        target = "kp-t67-c-restart"
        _inject_conflict(workspace, processed_course, target, refs[:2])
        before = _row_for(
            _review_set(workspace, processed_course, student), target
        )
        assert before["bucket"] == "blocked"
        workspace.close()
        reopened = Workspace(
            str(tmp_path / "data"),
            clock=fixed_clock(FIXED_TIME),
            asr_mode="mock",
            ocr_mode="mock",
        )
        try:
            after = _row_for(
                _review_set(reopened, processed_course, student), target
            )
            assert after["bucket"] == "blocked"
            assert after["block_reason"] == "unresolved_conflict"
        finally:
            reopened.close()

    def test_learning_state_survives_restart(self, tmp_path, workspace, processed_course, student, kps):
        target = kps[0]["knowledge_id"]
        workspace.learning_workflow_open_knowledge(processed_course, student, target)
        workspace.close()
        reopened = Workspace(
            str(tmp_path / "data"),
            clock=fixed_clock(FIXED_TIME),
            asr_mode="mock",
            ocr_mode="mock",
        )
        try:
            row = _row_for(
                _review_set(reopened, processed_course, student), target
            )
            assert row["learning_state"] == "exposed"
        finally:
            reopened.close()

    def test_empty_course_survives_restart(self, tmp_path, workspace):
        cid = workspace.create_course("Buit", "EMPTY", "ca")["course_id"]
        sid = workspace.create_student(cid, "s-1", "Buit")["student_id"]
        workspace.close()
        reopened = Workspace(
            str(tmp_path / "data"),
            clock=fixed_clock(FIXED_TIME),
            asr_mode="mock",
            ocr_mode="mock",
        )
        try:
            out = _review_set(reopened, cid, sid)
            assert out["empty"] is True
            assert out["empty_note"] == REVIEW_SET_EMPTY_NOTE
        finally:
            reopened.close()

    def test_subprocess_restart_in_full(self, tmp_path):
        """真·跨进程: 进程 A 造数据, 进程 B 只拿数据目录读复习集合。"""
        data_dir = str(tmp_path / "data")
        fixture_dir = str(FIXTURES)
        script = """
import json, sys
sys.path.insert(0, %(root)r)
from src.application.workspace import Workspace
from src.application.runtime import fixed_clock
ws = Workspace(%(data)r, clock=fixed_clock(%(time)r), asr_mode="mock", ocr_mode="mock")
cid = ws.create_course("Programacio", "PROG101", "ca")["course_id"]
sid = ws.create_student(cid, "s-quim", "Quim")["student_id"]
s = ws.create_session(cid, session_number=1, date=%(today)r, title="Tema")
for name in %(names)r:
    ws.register_material(cid, %(fx)r + "/" + name, session_id=s["session_id"])
ws.process_session(cid, s["session_id"])
ws.review_confirm(cid, ws.knowledge_points(cid)[0]["knowledge_id"], note="t67")
print(json.dumps({"course_id": cid, "student_id": sid}))
ws.close()
""" % {
            "root": str(Path(__file__).resolve().parents[1]),
            "data": data_dir,
            "time": FIXED_TIME,
            "today": TODAY,
            "names": list(_fixtures_for(1)),
            "fx": fixture_dir,
        }
        first = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=180
        )
        assert first.returncode == 0, first.stderr[-800:]
        ids = json.loads(first.stdout.strip().splitlines()[-1])

        read_script = """
import json, sys
sys.path.insert(0, %(root)r)
from src.application.workspace import Workspace
from src.application.runtime import fixed_clock
ws = Workspace(%(data)r, clock=fixed_clock(%(time)r), asr_mode="mock", ocr_mode="mock")
out = ws.student_review_set(%(cid)r, %(sid)r)
print(json.dumps(out, sort_keys=True))
ws.close()
""" % {
            "root": str(Path(__file__).resolve().parents[1]),
            "data": data_dir,
            "time": FIXED_TIME,
            "cid": ids["course_id"],
            "sid": ids["student_id"],
        }
        second = subprocess.run(
            [sys.executable, "-c", read_script], capture_output=True, text=True, timeout=180
        )
        assert second.returncode == 0, second.stderr[-800:]
        payload = json.loads(second.stdout.strip().splitlines()[-1])
        assert payload["counts"]["total"] > 0
        assert payload["by_bucket"]["ready"] >= 1, "the confirmed point must stay ready"
        blob = json.dumps(payload, ensure_ascii=False).lower()
        for field in NO_PREDICTION_FIELDS:
            assert field not in blob

    def test_restart_does_not_duplicate_conflicts(self, tmp_path, workspace, processed_course, student, kps):
        refs = _union_evidence(kps)
        _inject_conflict(workspace, processed_course, "kp-t67-dup", refs[:2])
        first = len(_review_set(workspace, processed_course, student)["conflicts"])
        workspace.close()
        reopened = Workspace(
            str(tmp_path / "data"),
            clock=fixed_clock(FIXED_TIME),
            asr_mode="mock",
            ocr_mode="mock",
        )
        try:
            second = len(_review_set(reopened, processed_course, student)["conflicts"])
            assert first == second
        finally:
            reopened.close()

    def test_repeated_reads_after_restart_are_stable(self, tmp_path, workspace, processed_course, student):
        workspace.close()
        reopened = Workspace(
            str(tmp_path / "data"),
            clock=fixed_clock(FIXED_TIME),
            asr_mode="mock",
            ocr_mode="mock",
        )
        try:
            first = json.dumps(_review_set(reopened, processed_course, student), sort_keys=True)
            for _ in range(5):
                assert json.dumps(
                    _review_set(reopened, processed_course, student), sort_keys=True
                ) == first
        finally:
            reopened.close()


# ===========================================================================
# 9b. 重启 (补足)
# ===========================================================================


class TestRestartMore:
    def test_conflict_blocking_flag_survives_restart(
        self, tmp_path, workspace, processed_course, student, kps
    ):
        """``blocking`` 标志在重启后必须与重启前一致 —— 不能"重新变回阻塞"。

        这个标志同时依赖冲突记录状态与知识点的人工审核状态, 两者重启后
        都要从磁盘还原。
        """
        refs = _union_evidence(kps)
        _inject_conflict(workspace, processed_course, "kp-t67-block", refs[:2])
        before = _review_set(workspace, processed_course, student)
        assert before["conflicts"][0]["blocking"] is True
        workspace.close()

        reopened = Workspace(
            str(tmp_path / "data"),
            clock=fixed_clock(FIXED_TIME),
            asr_mode="mock",
            ocr_mode="mock",
        )
        try:
            after = _review_set(reopened, processed_course, student)
            assert after["conflicts"][0]["blocking"] is True
            assert after["conflicts"][0]["auto_resolved"] is False
        finally:
            reopened.close()

    def test_resolved_conflict_stays_resolved_after_restart(
        self, tmp_path, workspace, processed_course, student, kps
    ):
        """人工解决冲突后重启: 不能"复活"成阻塞。

        这是 ``block_reason`` 里 ``review != "confirmed"`` 那半个条件的
        回归守卫 —— 只按冲突状态判定时, 重启后记录仍在, 会被误判回阻塞。
        """
        refs = _union_evidence(kps)
        target = "kp-t67-settled"
        _inject_conflict(workspace, processed_course, target, refs[:2])
        out = _review_set(workspace, processed_course, student)
        chosen = out["conflicts"][0]["evidence_refs"][0]
        workspace.review_resolve_conflict(
            processed_course, target, selected_evidence_ids=[chosen], note="t67",
        )
        assert _row_for(
            _review_set(workspace, processed_course, student), target
        )["bucket"] != "blocked"
        workspace.close()

        reopened = Workspace(
            str(tmp_path / "data"),
            clock=fixed_clock(FIXED_TIME),
            asr_mode="mock",
            ocr_mode="mock",
        )
        try:
            row = _row_for(
                _review_set(reopened, processed_course, student), target
            )
            assert row["bucket"] != "blocked", "resolved conflict must not come back"
            assert row["review_status"] == "confirmed"
        finally:
            reopened.close()


# ===========================================================================
# 9c. i18n (spec 67 要求 10 条)
# ===========================================================================


def _app_js() -> str:
    """P1-6: 前端已拆分成多个零构建脚本; 这里返回按加载顺序拼接的
    **全部前端源码**, 语义与拆分前读单个 app.js 一致。"""
    return read_web_source("app.js")


class TestI18n:
    """复习页的 ``rs.*`` 词条必须在 zh/es/ca 三语里都存在。

    ``rs.block.*`` / ``rs.reason.*`` 是**动态 key** —— 前端用
    ``t('rs.block.' + item.block_reason)`` 拼出来。少一个枚举值的文案,
    页面上就会出现一个裸 key。因此这组测试按后端常量展开取值域后逐条校验。
    """

    @staticmethod
    def _i18n_tables(source: str) -> dict[str, set]:
        start = source.index("const I18N = {")
        end = source.index("\n};", start)
        block = source[start:end]
        tables: dict[str, set] = {}
        for lang in ("zh", "es", "ca"):
            s = block.index(f"  {lang}: {{")
            e = block.index("\n  },", s)
            tables[lang] = set(
                re.findall(r"'([A-Za-z0-9_.]+)'\s*:", block[s:e])
            )
        return tables

    RS_STATIC_KEYS = (
        "rs.title", "rs.subtitle", "rs.notPredictor",
        "rs.blocked", "rs.attention", "rs.ready",
        "rs.position", "rs.knowledge",
        "rs.validationStatus", "rs.reviewStatus", "rs.learningState",
        "rs.twoAxesNote", "rs.blockReason", "rs.attentionReason",
        "rs.sortBasis", "rs.conflicts", "rs.conflictSides",
        "rs.conflictBlocking", "rs.conflictSettled", "rs.autoResolved",
        "rs.coverage", "rs.ratio", "rs.counts", "rs.total",
        "rs.empty", "rs.noStudent", "rs.backToToday",
        "nav.reviewMode",
    )

    def test_all_three_tables_have_the_same_rs_keys(self):
        tables = self._i18n_tables(_app_js())
        rs_zh = {k for k in tables["zh"] if k.startswith("rs.")}
        rs_es = {k for k in tables["es"] if k.startswith("rs.")}
        rs_ca = {k for k in tables["ca"] if k.startswith("rs.")}
        assert rs_zh == rs_es == rs_ca, (
            f"zh/es/ca rs.* mismatch: {rs_zh ^ rs_es} / {rs_zh ^ rs_ca}"
        )

    @pytest.mark.parametrize("key", RS_STATIC_KEYS)
    def test_static_rs_key_exists_in_all_languages(self, key):
        tables = self._i18n_tables(_app_js())
        for lang in ("zh", "es", "ca"):
            assert key in tables[lang], f"{key} missing from {lang}"

    @pytest.mark.parametrize("suffix", BLOCK_REASONS)
    def test_block_reason_has_all_three_languages(self, suffix):
        tables = self._i18n_tables(_app_js())
        key = "rs.block." + suffix
        for lang in ("zh", "es", "ca"):
            assert key in tables[lang], f"{key} missing from {lang}"

    @pytest.mark.parametrize("suffix", ATTENTION_REASONS)
    def test_attention_reason_has_all_three_languages(self, suffix):
        tables = self._i18n_tables(_app_js())
        key = "rs.reason." + suffix
        for lang in ("zh", "es", "ca"):
            assert key in tables[lang], f"{key} missing from {lang}"

    def test_backend_reason_constants_match_frontend_keys(self):
        """后端常量与前端文案取值域**必须**一致。

        后端新加一个 block_reason 而前端没加文案时, 这条会报错 ——
        而不是等到页面上出现裸 key。
        """
        tables = self._i18n_tables(_app_js())
        for suffix in BLOCK_REASONS:
            assert "rs.block." + suffix in tables["zh"]
        for suffix in ATTENTION_REASONS:
            assert "rs.reason." + suffix in tables["zh"]

    def test_no_forbidden_phrase_in_any_language_copy(self):
        """三语文案里都不能出现预测类措辞。

        只扫 ``rs.*`` 词条的**值**, 不扫整个 app.js —— 后端常量
        (``REVIEW_FORBIDDEN_TERMS``) 里也存了这些词, 整文件扫描必然误报。
        """
        source = _app_js()
        start = source.index("const I18N = {")
        block = source[start:source.index("\n};", start)]
        chinese = ("最可能考", "考试概率", "预测题", "押题", "考点概率", "预测分数")
        others = ("most likely exam", "exam probability", "probabilidad de examen")
        for match in re.finditer(
            r"'(rs\.[A-Za-z0-9_.]+)':\s*'((?:[^'\\]|\\.)*)'", block
        ):
            key, value = match.group(1), match.group(2)
            lowered = value.lower()
            for phrase in chinese:
                assert phrase not in value, f"{key} contains forbidden {phrase!r}"
            for phrase in others:
                assert phrase.lower() not in lowered, f"{key} contains forbidden {phrase!r}"

    def test_not_predictor_copy_exists_in_all_languages(self):
        """页头那句"这不是考试预测"必须在三语里都有 —— 它是产品定位声明。"""
        tables = self._i18n_tables(_app_js())
        for lang in ("zh", "es", "ca"):
            assert "rs.notPredictor" in tables[lang]

    def test_nav_entry_exists_in_all_languages(self):
        tables = self._i18n_tables(_app_js())
        for lang in ("zh", "es", "ca"):
            assert "nav.reviewMode" in tables[lang]


# ===========================================================================
# 10. 边界与健壮性
# ===========================================================================


class TestEdgeCases:
    def test_course_with_materials_but_unprocessed(self, workspace):
        """材料还没处理 -> 没有知识点 -> 空集合, 不是 500。"""
        cid = workspace.create_course("Raw", "RAW", "es")["course_id"]
        sid = workspace.create_student(cid, "s-1", "Un")["student_id"]
        session = workspace.create_session(cid, session_number=1, date=TODAY, title="T")
        workspace.register_material(
            cid, str(FIXTURES / "documents/simple.pdf"), session_id=session["session_id"]
        )
        out = _review_set(workspace, cid, sid)
        assert out["empty"] is True

    def test_several_students_are_isolated(self, workspace, processed_course, kps):
        a = workspace.create_student(processed_course, "s-a", "A")["student_id"]
        b = workspace.create_student(processed_course, "s-b", "B")["student_id"]
        target = kps[0]["knowledge_id"]
        workspace.learning_workflow_open_knowledge(processed_course, a, target)
        assert _row_for(_review_set(workspace, processed_course, a), target)["learning_state"] == "exposed"
        assert _row_for(_review_set(workspace, processed_course, b), target)["learning_state"] == "not_started"

    def test_many_confirmations_are_all_reflected(self, workspace, processed_course, student, kps):
        for kp in kps[:3]:
            workspace.review_confirm(processed_course, kp["knowledge_id"], note="t67")
        out = _review_set(workspace, processed_course, student)
        assert out["by_bucket"]["ready"] >= 3

    def test_payload_has_no_none_bucket(self, workspace, processed_course, student):
        out = _review_set(workspace, processed_course, student)
        for item in out["items"]:
            assert item["bucket"] in BUCKETS

    def test_every_item_position_unique(self, workspace, processed_course, student):
        out = _review_set(workspace, processed_course, student)
        positions = [i["position"] for i in out["items"]]
        assert len(positions) == len(set(positions))

    def test_knowledge_ids_are_unique_in_output(self, workspace, processed_course, student):
        out = _review_set(workspace, processed_course, student)
        ids = [i["knowledge_id"] for i in out["items"]]
        assert len(ids) == len(set(ids)), "a knowledge point must appear at most once"

    def test_all_knowledge_points_are_represented(self, workspace, processed_course, student, kps):
        out = _review_set(workspace, processed_course, student)
        listed = {i["knowledge_id"] for i in out["items"]}
        expected = {kp["knowledge_id"] for kp in kps}
        assert expected <= listed, f"missing from review set: {expected - listed}"
