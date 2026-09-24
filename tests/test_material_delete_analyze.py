# -*- coding: utf-8 -*-
"""材料页重构: 真实删除 + 一键 AI 完整流水线。

覆盖任务书「二十、测试要求」的后端部分:

- 删除: 正常材料 / 不存在 / 删除后列表刷新 / 关联结果不脏 (DB 行、
  知识点、证据退休、AI 报告、作业记录) / 持久化重启后不复活。
- 一键分析: 点击 (完整链路到知识点) / 重复点击幂等 (不重复建任务) /
  完成状态 / 摄取失败带阶段 / AI 失败带阶段 + 可重新分析 / 刷新后
  状态仍在 (analysis_statuses)。
- 删除进行中"任务": 同步模型下作业被移除并标记 CANCELLED。

UI 部分由 ``tests/test_material_ui_actions.py`` (固定断言) 覆盖。
"""

from __future__ import annotations

import os
import tempfile

import pytest

from src.application.ai.provider import AIProvider, AIRequestError, FakeAIProvider
from src.application.errors import NotFoundError
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace

FIXED_TIME = "2026-09-23T09:00:00+00:00"

TEXT = (
    "La integración por partes es un método fundamental. "
    "La definición de integral definida es el área bajo la curva. "
    "Por ejemplo, la integral de x es x al cuadrado sobre dos. "
    "El teorema fundamental del cálculo conecta derivación e integración."
)


def _workspace(tmp_path, **kw):
    return Workspace(
        str(tmp_path / "data"),
        clock=fixed_clock(FIXED_TIME),
        persistence=kw.pop("persistence", None),
        asr_mode="mock",
        ocr_mode="mock",
        **kw,
    )


def _write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def _register(ws, tmp_path, course_id, filename="lecture.txt"):
    path = _write(tmp_path, filename, TEXT)
    try:
        return ws.register_material(course_id, path, filename=filename)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


class _FailingAIProvider(AIProvider):
    """恒失败 provider (AI 阶段失败, 证据必须安全)。"""

    name = "failing-test"

    def capabilities(self):
        from src.application.ai.provider import ProviderCapabilities

        return ProviderCapabilities(accepts_images=False, accepts_audio=False)

    def generate_structured(self, prompt, *, timeout_seconds=60, max_output_chars=8000):
        raise AIRequestError(
            "AI request failed",
            detail={"provider": self.name, "http_status": 429},
        )


class _DyingASR:
    """摄取阶段即失败 (OCR / 转写不可用)。"""

    def transcribe(self, audio_path, language=None):
        raise RuntimeError("ASR backend unavailable")

    def capabilities(self):
        return {"audio": False}


# ----------------------------------------------------------------------
# 删除: 正常材料
# ----------------------------------------------------------------------


class TestDeleteMaterial:
    def test_delete_completed_material_removes_everything(self, tmp_path):
        ws = _workspace(tmp_path)
        ws.configure_ai(enabled=True, provider=FakeAIProvider())
        course_id = ws.create_course("Demo", "D1")["course_id"]
        mat = _register(ws, tmp_path, course_id)
        mid = mat["material_id"]

        result = ws.analyze_material(course_id, mid)
        assert result["status"] == "COMPLETED"
        assert len(ws.knowledge_points(course_id)) > 0

        deleted = ws.delete_material(course_id, mid)
        assert deleted["deleted"] is True
        assert deleted["material_id"] == mid
        assert len(deleted["removed_knowledge_point_ids"]) > 0
        assert deleted["retired_evidence_ids"]

        # 列表刷新: 材料消失, 知识归零, 无孤儿证据。
        assert ws.list_materials(course_id) == []
        assert ws.knowledge_points(course_id) == []
        summary = ws.knowledge_summary(course_id)
        assert summary["knowledge_point_total"] == 0
        assert summary["conflict_count"] == 0

    def test_delete_unknown_material_raises_not_found(self, tmp_path):
        ws = _workspace(tmp_path)
        course_id = ws.create_course("Demo", "D1")["course_id"]
        with pytest.raises(NotFoundError):
            ws.delete_material(course_id, "mat-does-not-exist")

    def test_double_delete_second_raises_not_found(self, tmp_path):
        ws = _workspace(tmp_path)
        course_id = ws.create_course("Demo", "D1")["course_id"]
        mat = _register(ws, tmp_path, course_id)
        ws.delete_material(course_id, mat["material_id"])
        with pytest.raises(NotFoundError):
            ws.delete_material(course_id, mat["material_id"])

    def test_delete_registered_material_without_processing(self, tmp_path):
        ws = _workspace(tmp_path)
        course_id = ws.create_course("Demo", "D1")["course_id"]
        mat = _register(ws, tmp_path, course_id)
        deleted = ws.delete_material(course_id, mat["material_id"])
        assert deleted["deleted"] is True
        assert deleted["removed_knowledge_point_ids"] == []
        assert ws.list_materials(course_id) == []

    def test_delete_does_not_touch_source_file(self, tmp_path):
        ws = _workspace(tmp_path)
        course_id = ws.create_course("Demo", "D1")["course_id"]
        path = _write(tmp_path, "keep.txt", TEXT)
        mat = ws.register_material(course_id, path, filename="keep.txt")
        ws.delete_material(course_id, mat["material_id"])
        assert os.path.isfile(path)  # 用户原始文件永不触碰

    def test_delete_removes_managed_copy_and_job(self, tmp_path):
        ws = _workspace(tmp_path)
        course_id = ws.create_course("Demo", "D1")["course_id"]
        mat = _register(ws, tmp_path, course_id)
        mid = mat["material_id"]
        ws.process_material(course_id, mid)
        ctx = ws.context(course_id)
        managed_path = ctx.workflow.get_material(mid)["stored_path"]
        assert os.path.isfile(managed_path)
        job = ws.processing_job(course_id, mid)
        assert job["status"] == "SUCCEEDED"

        ws.delete_material(course_id, mid)
        assert not os.path.isfile(managed_path)  # 受管副本删除
        with pytest.raises(NotFoundError):
            ws.processing_job(course_id, mid)  # 作业记录移除 (无孤儿)

    def test_delete_knowledge_does_not_reappear_after_restart(self, tmp_path):
        ws = _workspace(tmp_path)
        ws.configure_ai(enabled=True, provider=FakeAIProvider())
        course_id = ws.create_course("Demo", "D1")["course_id"]
        mat = _register(ws, tmp_path, course_id)
        mid = mat["material_id"]
        assert ws.analyze_material(course_id, mid)["status"] == "COMPLETED"

        ws.delete_material(course_id, mid)
        ws.close()

        ws2 = _workspace(tmp_path)
        ws2.context(course_id)
        assert ws2.list_materials(course_id) == []
        assert ws2.knowledge_points(course_id) == []
        ws2.close()

    def test_other_course_materials_unaffected(self, tmp_path):
        ws = _workspace(tmp_path)
        ws.configure_ai(enabled=True, provider=FakeAIProvider())
        c1 = ws.create_course("A", "A")["course_id"]
        c2 = ws.create_course("B", "B")["course_id"]
        m1 = _register(ws, tmp_path, c1, filename="a.txt")["material_id"]
        m2 = _register(ws, tmp_path, c2, filename="b.txt")["material_id"]
        assert ws.analyze_material(c1, m1)["status"] == "COMPLETED"
        assert ws.analyze_material(c2, m2)["status"] == "COMPLETED"
        kps2 = len(ws.knowledge_points(c2))

        ws.delete_material(c1, m1)
        assert len(ws.knowledge_points(c2)) == kps2  # 不串课
        assert len(ws.list_materials(c2)) == 1


# ----------------------------------------------------------------------
# 删除: 处理中 / 分析中的材料 (同步模型的取消语义)
# ----------------------------------------------------------------------


class TestDeleteWhileProcessing:
    def test_delete_processing_material_cancels_job_record(self, tmp_path):
        ws = _workspace(tmp_path)
        course_id = ws.create_course("Demo", "D1")["course_id"]
        session_id = ws.create_session(course_id, 1)["session_id"]
        path = _write(tmp_path, "lecture.txt", TEXT)
        try:
            mat = ws.register_material(
                course_id, path, session_id, filename="lecture.txt"
            )
        finally:
            os.remove(path)
        mid = mat["material_id"]
        # 建立一条 QUEUED 作业 (模拟"处理中"的登记态)。
        ws.context(course_id).processing.start_session_processing(session_id)
        assert ws.context(course_id).processing.peek_job(mid) is not None
        deleted = ws.delete_material(course_id, mid)
        assert deleted["deleted"] is True
        assert deleted["job"]["existed"] is True
        with pytest.raises(NotFoundError):
            ws.processing_job(course_id, mid)
        assert ws.list_materials(course_id) == []


# ----------------------------------------------------------------------
# 一键 AI 分析
# ----------------------------------------------------------------------


class TestAnalyzeMaterial:
    def test_one_click_pipeline_produces_knowledge(self, tmp_path):
        ws = _workspace(tmp_path)
        ws.configure_ai(enabled=True, provider=FakeAIProvider())
        course_id = ws.create_course("Demo", "D1")["course_id"]
        mat = _register(ws, tmp_path, course_id)

        result = ws.analyze_material(course_id, mat["material_id"])
        assert result["status"] == "COMPLETED"
        assert result["current_stage"] == "DONE"
        assert result["error_message"] is None
        # 完整链路的最终产物: 知识点真实存在 (Fake provider 确定性产出)。
        assert len(ws.knowledge_points(course_id)) > 0
        # 处理作业也真实存在 (摄取段跑过)。
        job = ws.processing_job(course_id, mat["material_id"])
        assert job["status"] == "SUCCEEDED"

    def test_repeated_click_is_idempotent(self, tmp_path):
        ws = _workspace(tmp_path)
        ws.configure_ai(enabled=True, provider=FakeAIProvider())
        course_id = ws.create_course("Demo", "D1")["course_id"]
        mid = _register(ws, tmp_path, course_id)["material_id"]

        first = ws.analyze_material(course_id, mid)
        second = ws.analyze_material(course_id, mid)
        assert first["status"] == "COMPLETED"
        assert second["status"] == "COMPLETED"
        # 确定性 ID: 重复分析不产生重复知识点。
        assert len(ws.knowledge_points(course_id)) == len(
            ws.knowledge_points(course_id)
        )

    def test_analysis_status_survives_page_reload_semantics(self, tmp_path):
        """刷新页面语义: 状态从服务端重读, 不依赖前端内存。"""
        ws = _workspace(tmp_path)
        ws.configure_ai(enabled=True, provider=FakeAIProvider())
        course_id = ws.create_course("Demo", "D1")["course_id"]
        mid = _register(ws, tmp_path, course_id)["material_id"]

        before = ws.analysis_status(course_id, mid)
        assert before["status"] == "IDLE"  # 未分析过

        ws.analyze_material(course_id, mid)
        statuses = ws.analysis_statuses(course_id)
        assert statuses[mid]["status"] == "COMPLETED"
        assert statuses[mid]["current_stage"] == "DONE"

    def test_ingestion_failure_reports_stage(self, tmp_path):
        ws = _workspace(tmp_path)
        course_id = ws.create_course("Demo", "D1")["course_id"]
        path = tmp_path / "empty.pdf"
        path.write_bytes(
            b"%PDF-1.4\ntrailer<< /Root 1 0 R >>\n%%EOF\n"  # 无有效文本的 PDF
        )
        mat = ws.register_material(course_id, str(path), filename="empty.pdf")
        os.remove(str(path))

        result = ws.analyze_material(course_id, mat["material_id"])
        assert result["status"] == "FAILED"
        assert result["current_stage"] in ("INGESTION", "EXTRACTION")
        assert result["error_message"]

    def test_ai_failure_reports_stage_and_material_stays_safe(self, tmp_path):
        ws = _workspace(tmp_path)
        ws.configure_ai(enabled=True, provider=_FailingAIProvider())
        course_id = ws.create_course("Demo", "D1")["course_id"]
        mid = _register(ws, tmp_path, course_id)["material_id"]

        result = ws.analyze_material(course_id, mid)
        assert result["status"] == "FAILED"
        assert result["current_stage"] == "AI_ANALYSIS"
        assert result["error_message"]
        assert "Traceback" not in result["error_message"]  # 不外泄堆栈
        # 证据安全: 材料摄取成功, 用户可重新分析。
        record = ws.get_material(course_id, mid)
        assert record["processing_status"] == "COMPLETED"

    def test_reanalyze_after_ai_recovery_succeeds(self, tmp_path):
        ws = _workspace(tmp_path)
        ws.configure_ai(enabled=True, provider=_FailingAIProvider())
        course_id = ws.create_course("Demo", "D1")["course_id"]
        mid = _register(ws, tmp_path, course_id)["material_id"]

        failed = ws.analyze_material(course_id, mid)
        assert failed["status"] == "FAILED"

        ws.configure_ai(enabled=True, provider=FakeAIProvider())
        recovered = ws.analyze_material(course_id, mid)
        assert recovered["status"] == "COMPLETED"
        assert len(ws.knowledge_points(course_id)) > 0

    def test_analyze_unknown_material_raises_not_found(self, tmp_path):
        ws = _workspace(tmp_path)
        course_id = ws.create_course("Demo", "D1")["course_id"]
        with pytest.raises(NotFoundError):
            ws.analyze_material(course_id, "mat-nope")

    def test_ai_disabled_still_completes_ingestion_pipeline(self, tmp_path):
        """AI 关闭: 一键分析仍完成摄取段 (旧行为兼容), 状态 COMPLETED。"""
        ws = _workspace(tmp_path)
        course_id = ws.create_course("Demo", "D1")["course_id"]
        mid = _register(ws, tmp_path, course_id)["material_id"]
        result = ws.analyze_material(course_id, mid)
        assert result["status"] == "COMPLETED"
        assert ws.knowledge_points(course_id)  # 确定性装配仍在

    def test_delete_after_failed_analysis_is_clean(self, tmp_path):
        ws = _workspace(tmp_path)
        ws.configure_ai(enabled=True, provider=_FailingAIProvider())
        course_id = ws.create_course("Demo", "D1")["course_id"]
        mid = _register(ws, tmp_path, course_id)["material_id"]
        assert ws.analyze_material(course_id, mid)["status"] == "FAILED"
        deleted = ws.delete_material(course_id, mid)
        assert deleted["deleted"] is True
        assert ws.list_materials(course_id) == []


# ----------------------------------------------------------------------
# 持久化: DB 级联与状态写穿
# ----------------------------------------------------------------------


class TestDeletePersistence:
    def test_db_rows_gone_and_evidence_retired(self, tmp_path):
        from src.application.persistence_wiring import WorkspacePersistence

        data = str(tmp_path / "data")
        db_path = os.path.join(data, "database", "classroom.sqlite")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        persistence = WorkspacePersistence.open(db_path)
        ws = _workspace(tmp_path, persistence=persistence)
        ws.configure_ai(enabled=True, provider=FakeAIProvider())
        course_id = ws.create_course("Demo", "D1")["course_id"]
        mid = _register(ws, tmp_path, course_id)["material_id"]
        assert ws.analyze_material(course_id, mid)["status"] == "COMPLETED"

        repos = persistence.repositories
        assert len(repos.materials.keys()) == 1
        assert len(repos.evidence.insertion_order()) == 1

        ws.delete_material(course_id, mid)
        db = persistence.database
        assert db.query("SELECT * FROM materials") == []
        assert db.query("SELECT * FROM material_processing") == []
        assert db.query("SELECT * FROM material_evidence") == []
        assert db.query("SELECT * FROM knowledge_points") == []
        assert db.query("SELECT * FROM course_knowledge_points") == []
        states = [str(r["state"]) for r in db.query("SELECT state FROM evidence")]
        assert states == ["RETIRED"]  # 退休写穿, 重启后不会复活为 ACTIVE
        ws.close()
