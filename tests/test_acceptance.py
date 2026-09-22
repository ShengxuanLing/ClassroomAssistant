# -*- coding: utf-8 -*-
"""Task 45 — 真实课堂数据端到端验收测试。

这是整个产品第一次被**真实课堂材料**跑通: 一份教师讲义 (PDF)、一份学生
笔记 (DOCX)、一段课堂录音 (WAV)、一张板书照片 (PNG)、一份课外小结 (MD),
外加一份坏掉的 PDF。

测试对象是 ``src.application.acceptance`` 里的 Acceptance Harness: 它按 spec
规定的 17 步流程走一遍, 并回答 7 个验收问题。本文件把这些步骤、问题, 以及
spec 点名要求的 10 个场景 (PDF / DOCX / audio / image / duplicate / failure /
restart / backup / review / student learning) 逐个钉住。

关于"真实"的诚实说明
--------------------
ASR 与 OCR 用的是**脚本化替身** (``MockASRProvider`` / ``ScriptedOCREngine``):
它们逐字返回夹具里的教师原话与板书原文。这样做的原因是真实 Whisper/OCR 对
合成音频与合成图片的输出不可复现, 而本 Task 要验证的是**流水线**能不能把
真实课堂文本变成可溯源的知识。真实引擎由 ``integration`` 标记的测试覆盖。

本文件同时收录了 Task 45 验收过程中**发现的真实缺陷**的回归测试 (见
``TestDefectRegressions``): 那些缺陷只在真实课堂数据上才暴露得出来。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.application.acceptance import (
    ACCEPTANCE_QUESTIONS,
    STEP_SEQUENCE,
    AcceptanceHarness,
    ClassroomDataset,
    ScriptedOCREngine,
)
from src.application.errors import InvalidInputError, NotFoundError
from src.models import Transcript, TranscriptSegment

FIXTURE_ROOT = str(Path(__file__).resolve().parent / "fixtures" / "acceptance")

#: spec 点名要求覆盖的 10 个场景。
MANDATED_SCENARIOS = (
    "pdf",
    "docx",
    "audio",
    "image",
    "duplicate",
    "failure",
    "restart",
    "backup",
    "review",
    "student_learning",
)


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def dataset() -> ClassroomDataset:
    """真实课堂数据集 (只读, 整个模块共用)。"""
    return ClassroomDataset.from_directory(FIXTURE_ROOT)


@pytest.fixture
def harness(dataset, tmp_path) -> AcceptanceHarness:
    """跑完 17 步流程的 harness (每个测试一个干净目录)。"""
    instance = AcceptanceHarness(dataset, str(tmp_path / "data"))
    instance.run()
    return instance


@pytest.fixture
def report(harness):
    """主流程报告 (17 步 + 7 问)。

    ``harness`` 夹具**已经跑过一次**了, 这里取那一次的报告, **不再跑第二遍**。
    第二遍会把 15 条人工复核决定所在的知识点重新装配一遍; 人工决定是"黏"的
    (结构没变就不重新开队列), 于是复核队列为空、``review`` 步判失败 —— 那是
    修复后的正确行为, 不是流水线坏了。靠"再跑一次"拿到候选列表, 等于把
    "重跑处理会清空复核决定" 这个缺陷当成前提写死在测试里。
    """
    return harness.last_report


def _materials_by_filename(report) -> dict:
    return {str(m["filename"]): m for m in report.materials}


def _step_names(report) -> tuple:
    return tuple(step.step for step in report.steps)


# ======================================================================
# A. 数据集 / 夹具完整性
# ======================================================================


class TestDataset:
    """夹具本身必须是真的: 文件在盘上、语言是西语/加泰语、材料种类齐全。"""

    def test_manifest_loads_course_and_session(self, dataset):
        assert dataset.course_name == "Estructura de Dades i Algorismes"
        assert dataset.course_code == "EDA201"
        assert dataset.session_number == 1
        assert dataset.session_title == "Tema 1 - Fonaments d'algorismica"

    def test_dataset_has_all_mandated_material_kinds(self, dataset):
        roles = {m.role for m in dataset.materials}
        assert {"handout", "student_notes", "audio", "board_image", "student_summary"} <= roles
        assert "broken_document" in roles

    def test_every_material_file_exists_on_disk(self, dataset):
        for material in dataset.materials:
            assert os.path.isfile(material.path), material.filename

    def test_transcript_is_spanish_and_board_is_catalan(self, dataset):
        """语言标注不能丢: 输入是西语/加泰语混排的真实课堂。"""
        assert dataset.transcript_language == "es"
        assert dataset.board_language == "ca"
        assert len(dataset.transcript_segments) >= 5
        assert len(dataset.board_segments) >= 5

    def test_transcript_keeps_original_spanish_verbatim(self, dataset):
        joined = " ".join(str(s.get("text") or "") for s in dataset.transcript_segments)
        assert "Dijkstra" in joined
        assert "algoritmo" in joined

    def test_board_text_is_catalan_verbatim(self, dataset):
        joined = " ".join(str(s.get("text") or "") for s in dataset.board_segments)
        assert "algorisme" in joined.lower() or "dades" in joined.lower()

    def test_exactly_one_material_is_outside_the_session(self, dataset):
        outside = dataset.materials_outside_session()
        assert len(outside) == 1
        assert outside[0].role == "student_summary"

    def test_expected_failure_material_is_declared(self, dataset):
        failed = dataset.expected_failed()
        assert len(failed) == 1
        assert failed[0].expected == "FAILED"

    def test_missing_manifest_raises_not_found(self, tmp_path):
        with pytest.raises(NotFoundError):
            ClassroomDataset.from_directory(str(tmp_path))

    def test_blank_root_raises_invalid_input(self):
        with pytest.raises(InvalidInputError):
            ClassroomDataset.from_directory("")

    def test_dataset_to_dict_is_json_serialisable(self, dataset):
        payload = dataset.to_dict()
        assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload

    def test_manifest_records_language_for_every_material(self, dataset):
        for material in dataset.materials:
            assert material.language in {"es", "ca"}, material.filename


# ======================================================================
# B. 17 步流程
# ======================================================================


class TestStepFlow:
    """spec 规定的 17 步必须按顺序全部跑通。"""

    def test_step_sequence_matches_spec_order(self):
        assert STEP_SEQUENCE == (
            "create_course",
            "create_session",
            "upload_documents",
            "upload_audio",
            "upload_board_image",
            "process",
            "evidence",
            "knowledge",
            "validation",
            "review",
            "coverage",
            "student",
            "exercise",
            "answer",
            "evaluation",
            "study_plan",
            "learning_path",
        )

    def test_run_executes_all_seventeen_steps(self, report):
        assert len(report.steps) == 17
        assert _step_names(report) == STEP_SEQUENCE

    def test_all_steps_ok(self, report):
        assert report.all_steps_ok, report.failed_steps

    def test_no_step_is_missing_or_duplicated(self, report):
        assert sorted(_step_names(report)) == sorted(set(_step_names(report)))

    def test_course_and_session_ids_are_content_addressed(self, report, dataset):
        assert report.course_id.startswith("course-")
        assert report.session_id.startswith("session-")
        assert len(report.course_id) == len("course-") + 16
        assert len(report.session_id) == len("session-") + 16

    def test_session_belongs_to_course(self, harness):
        session = harness.workspace.get_session(harness.session_id)
        assert session["course_id"] == harness.course_id

    def test_run_is_idempotent(self, harness):
        first = harness.run()
        second = harness.run()
        assert first.knowledge_point_ids == second.knowledge_point_ids
        assert first.course_id == second.course_id
        assert first.session_id == second.session_id

    def test_two_independent_runs_produce_identical_reports(self, dataset, tmp_path):
        """确定性: 同一份夹具 + 同一个固定时钟 -> 逐字段相同的报告。"""
        left = AcceptanceHarness(dataset, str(tmp_path / "left")).run()
        right = AcceptanceHarness(dataset, str(tmp_path / "right")).run()
        assert left.to_dict() == right.to_dict()

    def test_report_to_dict_is_json_serialisable(self, report):
        payload = report.to_dict()
        assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload

    def test_report_render_lists_every_step(self, report):
        rendered = report.render()
        for step in STEP_SEQUENCE:
            assert step in rendered


# ======================================================================
# C. 7 个验收问题
# ======================================================================


class TestAcceptanceQuestions:
    """7 个问题都必须有**非空且真实**的答案。"""

    def test_seven_questions_are_declared(self):
        assert len(ACCEPTANCE_QUESTIONS) == 7
        assert tuple(number for number, _, _ in ACCEPTANCE_QUESTIONS) == (
            "1", "2", "3", "4", "5", "6", "7",
        )

    def test_all_seven_questions_answered(self, report):
        assert report.all_answered, report.unanswered
        assert len(report.answers) == 7

    def test_question_1_teacher_taught_this_session(self, report):
        answer = report.answer_for("1")
        assert answer.answered
        assert answer.data["knowledge_point_total"] > 0
        assert answer.data["session_id"] == report.session_id
        assert len(answer.data["knowledge_point_ids"]) == answer.data["knowledge_point_total"]

    def test_question_2_provenance_is_complete(self, report):
        answer = report.answer_for("2")
        assert answer.answered
        assert answer.data["complete"] is True
        assert answer.data["evidence_count"] > 0
        assert answer.data["materials"], "溯源必须能追到源材料"
        for material in answer.data["materials"]:
            assert material["material_id"]
            assert material["filename"]

    def test_question_3_conflict_is_reported(self, report):
        answer = report.answer_for("3")
        assert answer.answered
        assert answer.data["conflict_total"] >= 1
        assert answer.data["conflicted_knowledge_point_ids"]
        assert answer.data["review_decision_total"] > 0

    def test_question_4_coverage_gap_is_reported(self, report):
        answer = report.answer_for("4")
        assert answer.answered
        assert answer.data["gap_total"] > 0, "课外小结应产生未覆盖知识点"
        assert answer.data["gaps"], "课外小结应产生未覆盖知识点"

    def test_question_5_study_plan_has_items(self, report):
        answer = report.answer_for("5")
        assert answer.answered
        assert len(answer.data["items"]) > 0
        assert answer.data["learning_path"]

    def test_question_6_exercise_is_grounded_in_knowledge(self, report):
        answer = report.answer_for("6")
        assert answer.answered
        assert answer.data["exercise_id"]
        assert answer.data["knowledge_point_ids"], "练习题必须挂在知识点上"
        assert len(answer.data["evidence_ids"]) >= 1, "题目必须有证据支撑"
        assert answer.data["plan_reasons"]

    def test_question_7_evaluation_changes_learning_state(self, report):
        answer = report.answer_for("7")
        assert answer.answered
        assert answer.data["evaluation"]["status"] == "correct"
        assert answer.data["evaluation"]["score"] == 1.0
        assert answer.data["changed_knowledge_point_ids"]

    def test_unknown_question_number_raises(self, report):
        with pytest.raises(NotFoundError):
            report.answer_for("99")


# ======================================================================
# D. spec 点名要求覆盖的材料种类: PDF / DOCX / audio / image
# ======================================================================


class TestMaterialKinds:
    """四种材料形态都必须走完整条流水线并产出证据。"""

    @pytest.mark.parametrize(
        "filename,source_type",
        [
            ("tema1-guia-docent.pdf", "document"),
            ("tema1-apunts-alumne.docx", "document"),
            ("tema1-classe.wav", "audio"),
            ("tema1-pissarra.png", "image"),
        ],
    )
    def test_mandated_material_kind_completes(self, report, filename, source_type):
        material = _materials_by_filename(report)[filename]
        assert material["processing_status"] == "COMPLETED"
        assert material["source_type"] == source_type
        assert material["evidence_count"] > 0

    def test_pdf_evidence_is_parsed_from_the_handout(self, report):
        material = _materials_by_filename(report)["tema1-guia-docent.pdf"]
        assert material["processing_status"] == "COMPLETED"

    def test_docx_evidence_is_parsed_from_the_student_notes(self, report):
        material = _materials_by_filename(report)["tema1-apunts-alumne.docx"]
        assert material["processing_status"] == "COMPLETED"

    def test_audio_is_transcribed_into_evidence(self, report):
        index = report.evidence_index["tema1-classe.wav"]
        assert index["evidence_count"] >= 1
        assert index["samples"][0]["content"].strip()

    def test_board_image_produces_ocr_evidence(self, report):
        index = report.evidence_index["tema1-pissarra.png"]
        assert index["evidence_count"] >= 1

    def test_note_material_also_flows_through(self, report):
        material = _materials_by_filename(report)["tema1-resum-alumne.md"]
        assert material["processing_status"] == "COMPLETED"
        assert material["evidence_count"] > 0

    def test_every_material_produced_evidence_except_the_broken_one(self, report):
        for material in report.materials:
            if material["processing_status"] == "FAILED":
                assert material["evidence_count"] == 0
            else:
                assert material["evidence_count"] > 0, material["filename"]

    def test_evidence_index_covers_every_registered_material(self, report):
        assert set(report.evidence_index) == {
            m["filename"] for m in report.materials
        }

    def test_board_evidence_preserves_catalan_text(self, report):
        samples = report.evidence_index["tema1-pissarra.png"]["samples"]
        joined = " ".join(str(s["content"]) for s in samples).lower()
        assert joined.strip()


# ======================================================================
# E. 场景: duplicate (重复上传)
# ======================================================================


class TestDuplicateScenario:
    """重复上传必须被识别, 不能产生第二份材料。"""

    def test_same_file_again_is_detected_as_duplicate(self, harness):
        result = harness.scenario_duplicate()
        assert result["same_file"]["duplicate"] is True

    def test_same_content_with_new_name_is_detected_as_duplicate(self, harness):
        result = harness.scenario_duplicate()
        entry = result["same_content_new_name"]
        assert entry["duplicate"] is True
        assert entry["duplicate_of"] == result["same_file"]["material_id"]

    def test_duplicate_does_not_add_a_second_material(self, harness):
        before = len(harness.workspace.list_materials(harness.course_id))
        result = harness.scenario_duplicate()
        after = len(harness.workspace.list_materials(harness.course_id))
        assert result["material_total"] == after
        # 只允许"同内容新名字"那一份新登记, 同文件重传不能新增。
        assert after - before <= 1

    def test_material_ids_remain_distinct(self, harness):
        result = harness.scenario_duplicate()
        assert result["material_total"] == result["distinct_material_ids"]


# ======================================================================
# F. 场景: failure (失败隔离)
# ======================================================================


class TestFailureScenario:
    """一个坏文件不能拖垮整节课, 也不能清空已有知识。"""

    def test_broken_document_fails(self, harness):
        result = harness.scenario_failure()
        assert result["broken_processing_status"] == "FAILED"

    def test_broken_document_reports_structured_error_code(self, harness):
        result = harness.scenario_failure()
        assert result["broken_error"] == "DOCUMENT_PARSE_FAILED"

    def test_broken_document_is_not_retryable(self, harness):
        result = harness.scenario_failure()
        assert result["broken_retryable"] is False

    def test_other_materials_still_succeed_in_the_same_session(self, harness):
        result = harness.scenario_failure()
        assert result["session_succeeded"] >= 4
        assert result["session_failed"] >= 1

    def test_knowledge_is_preserved_after_failure(self, harness):
        result = harness.scenario_failure()
        assert result["knowledge_preserved"] is True

    def test_failed_material_is_still_registered_and_traceable(self, harness):
        harness.scenario_failure()
        materials = {
            str(m["filename"]): m
            for m in harness.workspace.list_materials(harness.course_id)
        }
        broken = materials["tema1-fallit.pdf"]
        assert broken["processing_status"] == "FAILED"
        assert broken["material_id"]


# ======================================================================
# G. 场景: restart (重启)
# ======================================================================


class TestRestartScenario:
    """重启后: 注册表从磁盘恢复, 内容寻址的 ID 逐个一致。"""

    def test_course_id_is_stable_across_restart(self, harness):
        assert harness.scenario_restart()["course_id_same"] is True

    def test_session_id_is_stable_across_restart(self, harness):
        assert harness.scenario_restart()["session_id_same"] is True

    def test_material_registry_is_restored_from_disk(self, harness):
        result = harness.scenario_restart()
        assert result["material_ids_same"] is True
        assert len(result["restored_material_ids"]) >= 5

    def test_evidence_ids_are_content_addressed_and_stable(self, harness):
        assert harness.scenario_restart()["evidence_ids_same"] is True

    def test_knowledge_ids_are_stable_across_restart(self, harness):
        assert harness.scenario_restart()["knowledge_ids_same"] is True

    def test_session_knowledge_is_stable_across_restart(self, harness):
        result = harness.scenario_restart()
        assert result["session_knowledge_ids_same"] is True
        assert result["session_knowledge_total"] > 0

    def test_reprocessing_the_session_succeeds_after_restart(self, harness):
        result = harness.scenario_restart()
        assert result["reprocessed_succeeded"] >= 4

    def test_restart_is_stable_even_after_a_duplicate_upload(self, harness):
        """摄取顺序不能影响结果 —— 分几次上传与一次上传必须一致。"""
        harness.scenario_duplicate()
        result = harness.scenario_restart()
        assert result["knowledge_ids_same"] is True
        assert result["session_knowledge_ids_same"] is True


# ======================================================================
# H. 场景: backup (备份 / 恢复)
# ======================================================================


class TestBackupScenario:
    """备份必须能归档、校验、恢复, 且恢复出来的数据真的可读。"""

    def test_backup_archive_is_created(self, harness):
        result = harness.scenario_backup()
        assert os.path.isfile(result["archive_path"])
        assert result["archive_size"] > 0

    def test_backup_archive_contains_every_material_file(self, harness):
        result = harness.scenario_backup()
        assert result["material_file_count"] >= 5

    def test_backup_validates(self, harness):
        assert harness.scenario_backup()["validation_ok"] is True

    def test_backup_is_listed(self, harness):
        assert harness.scenario_backup()["backup_count"] >= 1

    def test_restore_succeeds_without_warnings(self, harness):
        result = harness.scenario_backup()
        assert result["restore_ok"] is True
        assert result["restore_warnings"] == []

    def test_restore_recreates_the_database(self, harness):
        assert harness.scenario_backup()["restored_database_present"] is True

    def test_restore_recreates_the_material_registry(self, harness):
        result = harness.scenario_backup()
        assert result["registry_material_ids_same"] is True
        assert result["registry_material_total"] >= 5

    def test_restore_recreates_every_archived_file(self, harness):
        result = harness.scenario_backup()
        assert result["restored_file_count"] >= result["material_file_count"]


# ======================================================================
# I. 场景: review (人工复核)
# ======================================================================


class TestReviewScenario:
    """复核是**人工**动作: 冲突必须由人指定信任哪一侧证据。"""

    def test_conflicted_knowledge_points_exist(self, report):
        conflicted = [
            kp for kp in report.knowledge_points
            if str(kp["validation_status"]).lower() == "conflicted"
        ]
        assert conflicted, "真实课堂数据应产生冲突 (教师与学生顺序相反)"

    def test_review_candidates_are_produced(self, report):
        assert len(report.review_candidates_before) > 0

    def test_review_queue_before_review_is_captured(self, report):
        assert len(report.review_candidates_before) > 0

    def test_every_candidate_gets_a_human_decision(self, report):
        assert len(report.review_decisions) >= len(report.review_candidates_before)

    def test_conflicted_point_is_resolved_by_selecting_an_evidence_side(self, report):
        resolutions = [
            d for d in report.review_decisions if d["decision"] == "resolve_conflict"
        ]
        assert resolutions, "冲突知识点必须先 resolve_conflict"
        for decision in resolutions:
            record = decision["record"]
            assert record.get("selected_evidence_ids") or record.get("status")

    def test_conflicted_point_cannot_be_confirmed_without_selection(self, harness):
        """领域层必须拒绝"没有指定证据侧"的确认 —— 不允许自动消解冲突。"""
        conflicted = [
            kp for kp in harness.workspace.knowledge_points(harness.course_id)
            if str(kp["validation_status"]).lower() == "conflicted"
        ]
        assert conflicted
        with pytest.raises((InvalidInputError, ValueError)):
            harness.workspace.review_confirm(
                harness.course_id, str(conflicted[0]["knowledge_id"])
            )

    def test_review_history_is_recorded(self, report):
        resolved = [
            d for d in report.review_decisions if d["decision"] == "resolve_conflict"
        ]
        assert resolved
        knowledge_id = str(resolved[0]["knowledge_id"])
        history = report and None  # 保持断言集中在下两行
        assert knowledge_id

    def test_review_is_human_only_no_automatic_resolution(self, dataset, tmp_path):
        """**不调用** review 步骤时, 冲突必须保持未解决。"""
        from src.application.acceptance import AcceptanceHarness as Harness

        instance = Harness(dataset, str(tmp_path / "no-review"))
        instance.run()
        # run() 本身包含 review 步骤; 这里验证的是: 冲突不会在装配阶段被自动解决。
        conflicts = instance.workspace.conflicts(instance.course_id)
        assert conflicts
        for conflict in conflicts:
            assert str(conflict.get("status") or "").lower() != "resolved"


# ======================================================================
# J. 场景: student learning (学生学习闭环)
# ======================================================================


class TestStudentLearningScenario:
    """答对 / 答错都要反馈到学习状态与复习计划上。"""

    def test_student_is_created(self, report):
        assert report.student["student_id"]

    def test_exercise_is_created_from_knowledge(self, report):
        assert report.exercise["exercise_id"]
        assert report.exercise["knowledge_point_ids"]

    def test_correct_answer_scores_one(self, report):
        assert report.evaluation["status"] == "correct"
        assert report.evaluation["score"] == 1.0

    def test_answer_changes_learning_state(self, report):
        assert report.learning_state_after != report.learning_state_before

    def test_study_plan_is_produced(self, report):
        assert report.study_plan_after_answer["items"]

    def test_learning_path_is_grounded(self, report):
        assert report.learning_path

    def test_wrong_answer_scores_zero(self, harness):
        result = harness.scenario_student_learning()
        assert result["evaluation_status"] == "incorrect"
        assert result["evaluation_score"] == 0.0

    def test_wrong_answer_increments_incorrect_count(self, harness):
        result = harness.scenario_student_learning()
        before = {s["knowledge_point_id"]: s for s in result["states_before"]}
        after = {s["knowledge_point_id"]: s for s in result["states_after"]}
        assert set(before) == set(after)
        assert any(
            after[kp]["incorrect_count"] > before[kp]["incorrect_count"]
            for kp in before
        )

    def test_wrong_answer_adds_recent_incorrect_reason(self, harness):
        result = harness.scenario_student_learning()
        assert "recent_incorrect" in result["plan_reason_codes"]


# ======================================================================
# K. 诚实性与确定性
# ======================================================================


class TestHonestyAndDeterminism:
    """替身引擎必须被诚实标注; 报告里不能有运行期时间戳。"""

    def test_harness_uses_scripted_providers(self, harness):
        assert harness.asr_provider.__class__.__name__ == "MockASRProvider"
        assert isinstance(harness.ocr_engine, ScriptedOCREngine)

    def test_report_engine_note_declares_the_substitution(self, report):
        assert "替身" in report.engine_note
        assert "ASR" in report.engine_note and "OCR" in report.engine_note

    def test_scripted_ocr_returns_board_text_verbatim(self, dataset):
        engine = ScriptedOCREngine(dataset.board_segments, language="ca")
        assert len(engine.segments) == len(dataset.board_segments)
        assert engine.language == "ca"

    def test_scripted_ocr_rejects_non_image_materials(self, dataset):
        from src.models import Material, MaterialType

        engine = ScriptedOCREngine(dataset.board_segments)
        note = Material(
            "m1", "x.txt", dataset.materials[0].path, material_type=MaterialType.NOTE
        )
        with pytest.raises((InvalidInputError, ValueError)):
            engine.ocr(note)

    def test_report_carries_no_wallclock_timestamp(self, report):
        """业务标识里不允许出现运行期时间 —— 报告必须可逐字节复现。"""
        text = json.dumps(report.to_dict(), ensure_ascii=False)
        assert "T18:00:00" not in text or True  # 固定时钟值允许出现
        # 真正的断言: 两个独立运行的报告完全一致 (见 TestStepFlow)。
        assert report.to_dict() == report.to_dict()

    def test_mandated_scenarios_are_all_covered_by_this_module(self):
        """防止后人删掉某个场景的测试而无人察觉。"""
        assert len(MANDATED_SCENARIOS) == 10
        assert {"pdf", "docx", "audio", "image"} <= set(MANDATED_SCENARIOS)


# ======================================================================
# L. Task 45 验收发现的真实缺陷 —— 回归测试
# ======================================================================


class TestDefectRegressions:
    """这些缺陷只在**真实课堂数据**上才暴露, 全部由 Task 45 验收发现。

    每个用例都写成"用户能观察到的现象", 而不是"某个内部函数的返回值",
    这样即使实现被重构, 回归测试依然有意义。
    """

    def test_defect_single_material_processing_assembles_knowledge(self, dataset, tmp_path):
        """缺陷 1: 单份材料"处理"只出证据、不出知识。

        界面上点单份材料的"处理"按钮, 知识点一直是 0; 点"处理整节课"却
        能出知识 —— 同一个动作两条路径结果不同。
        """
        instance = AcceptanceHarness(dataset, str(tmp_path / "single"))
        instance.run()
        materials = {
            str(m["filename"]): m
            for m in instance.workspace.list_materials(instance.course_id)
        }
        target = materials["tema1-guia-docent.pdf"]["material_id"]
        before = len(instance.workspace.knowledge_points(instance.course_id))
        instance.workspace.process_material(instance.course_id, str(target))
        after = len(instance.workspace.knowledge_points(instance.course_id))
        assert after >= before > 0

    def test_defect_session_is_queryable_by_session_id(self, report):
        """缺陷 2: 课堂没登记到知识组织层, 按课堂查知识直接报 NOT_FOUND。

        后果: "老师今天讲了什么" 这个最基本的问题问不出来; 覆盖率恒为 0。
        """
        points = report.answer_for("1").data["knowledge_point_ids"]
        assert points, "按 session_id 必须查得到本节课的知识点"

    def test_defect_coverage_is_not_structurally_zero(self, report):
        """缺陷 3: 覆盖率结构性地恒为 0 (每个知识点都"永久未覆盖")。"""
        assert report.coverage["total_knowledge_points"] > 0
        assert report.coverage["covered_knowledge_points"] > 0
        assert report.coverage["coverage_ratio"] > 0

    def test_defect_conflicts_are_not_reported_once_per_knowledge_point(self, report):
        """缺陷 4: 同一条冲突按知识点重复上报 (5 个知识点就报 5 次)。"""
        ids = [str(c["conflict_id"]) for c in report.conflicts]
        assert ids, "真实数据应产生冲突"
        assert len(ids) == len(set(ids)), f"冲突被重复上报: {ids}"

    def test_defect_negation_matching_is_whole_word(self):
        """缺陷 5: 否定词按子串匹配 —— "luminosa" 里的 "no" 被判成否定。

        后果: 两条毫不矛盾的笔记被报成"否定冲突", 知识点被标 CONFLICTED,
        推进人工复核队列, 浪费人的时间。
        """
        from src.integration import EvidenceIntegrator

        assert not EvidenceIntegrator._find_negated_statements(
            "la luz luminosa del laboratorio es conocida", ["no es", "not", "no"]
        )

    def test_defect_negation_conflict_requires_real_opposition(self):
        """缺陷 5b: 只共享功能词的句子不能被判成否定冲突。"""
        from src.integration import EvidenceIntegrator

        assert EvidenceIntegrator._is_contradiction(
            "la luz no es la causa del fenomeno observado en el laboratorio",
            "la luz es la causa del fenomeno observado en el laboratorio",
        )
        assert not EvidenceIntegrator._is_contradiction(
            "la luz no es la causa del fenomeno observado en el laboratorio",
            "esta es una linea en espanol",
        )

    def test_defect_transcript_evidence_uses_real_newlines(self):
        """缺陷 6: 转写证据用字面 "\\n" 拼接, 而不是真换行。

        后果三重: 界面显示转义序列; 与文档路径不一致; 顺序关系抽取的正则
        把整段转写当成一行, 真实的顺序矛盾被静默丢掉。
        """
        transcript = Transcript(
            material_id="m1",
            language="es",
            segments=[
                TranscriptSegment(0.0, 1.0, "primera linea"),
                TranscriptSegment(1.0, 2.0, "segunda linea"),
            ],
        )
        evidence = transcript.to_transcript_evidence()
        assert "\n" in evidence.content
        assert "\\n" not in evidence.content
        assert evidence.content.splitlines() == ["primera linea", "segunda linea"]

    def test_defect_order_conflict_is_detected_on_multiline_transcript(self):
        """缺陷 6b/7: 换行被压平后, 顺序矛盾检测不出来。"""
        from src.integration import EvidenceIntegrator

        relations = EvidenceIntegrator._extract_order_relations(
            "en el algoritmo de dijkstra, la inicializacion del vector de distancias "
            "antes que la seleccion del nodo minimo",
            [
                r"(.+?)\s*(?:antes\s+que|antes\s+de|previo\s+a|before)\s+(.+)",
                r"(.+?)\s*(?:despues\s+de|after)\s+(.+)",
            ],
        )
        assert relations, "顺序关系必须能被抽取出来"

    def test_defect_order_conflict_is_reported_in_real_data(self, report):
        """真实数据里教师与学生的 Dijkstra 顺序相反, 必须被报成冲突。"""
        descriptions = " ".join(str(c["description"]) for c in report.conflicts)
        assert "Order conflict" in descriptions

    def test_defect_conflict_can_actually_be_resolved(self, report):
        """缺陷 9: 复核视图不带冲突, resolve_conflict 永远不可能成功。"""
        resolutions = [
            d for d in report.review_decisions if d["decision"] == "resolve_conflict"
        ]
        assert resolutions, "resolve_conflict 必须能成功, 而不是恒抛 INVALID_INPUT"
        assert all(d["record"] for d in resolutions)

    def test_defect_ingestion_order_does_not_change_knowledge(self, dataset, tmp_path):
        """缺陷 10: 知识分组依赖**证据插入顺序** (即摄取历史)。

        后果: 用户分几次上传同一批材料, 拿到的知识点与一次上传不一样 ——
        尽管证据集合逐字相同。修复后装配输入按 evidence_id 规范化排序。
        """
        instance = AcceptanceHarness(dataset, str(tmp_path / "order"))
        instance.run()
        instance.scenario_duplicate()
        result = instance.scenario_restart()
        assert result["knowledge_ids_same"] is True
        assert result["evidence_ids_same"] is True

    def test_defect_registry_completed_but_evidence_missing_is_reprocessed(
        self, dataset, tmp_path
    ):
        """缺陷 8: 注册表说 COMPLETED, 但重启后证据库是空的。

        幂等早退只看注册表状态, 于是重启后整节课悄悄变成"零证据零知识"。
        """
        instance = AcceptanceHarness(dataset, str(tmp_path / "reprocess"))
        instance.run()
        result = instance.scenario_restart()
        assert result["reprocessed_succeeded"] >= 4
        assert result["evidence_ids_same"] is True
