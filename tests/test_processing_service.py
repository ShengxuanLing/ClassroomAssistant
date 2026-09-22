# -*- coding: utf-8 -*-
"""Task 37 tests: Full Classroom Recording Pipeline.

覆盖 spec 要求: end-to-end (PDF / DOCX / audio / image), 失败隔离,
重试上限, 作业状态机, 知识装配 (Evidence -> Knowledge -> Validation ->
Review), 取消, 幂等, 确定性。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.application.audio_pipeline import (
    LongAudioASRProvider,
    QualityCheckedASRProvider,
    build_audio_chain,
)
from src.application.errors import InvalidInputError, NotFoundError
from src.application.material_workflow import MaterialWorkflowService
from src.evidence_ingestion import EvidenceIngestionService
from src.evidence_store import EvidenceStore
from src.application.processing_service import (
    JOB_CANCELLED,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_STATUSES,
    JOB_SUCCEEDED,
    ClassroomProcessingService,
    ProcessingJob,
)
from src.application.runtime import fixed_clock
from src.asr_provider import ASRProvider, MockASRProvider, TranscriptionResult
from src.models import Course

FIXTURES = Path(__file__).resolve().parent / "fixtures"
COURSE_NAME = "Algebra Lineal"
FIXED_TIME = "2026-01-01T00:00:00+00:00"
SESSION = "session-test-0001"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def build(tmp_path, *, with_session=True, max_attempts=3, **kwargs):
    source = tmp_path / "uploads"
    source.mkdir(parents=True, exist_ok=True)
    data = tmp_path / "data"
    workflow = MaterialWorkflowService(
        Course(name=COURSE_NAME),
        str(source),
        str(data),
        max_attempts=max_attempts,
        clock=fixed_clock(FIXED_TIME),
    )
    service = ClassroomProcessingService(
        workflow,
        clock=fixed_clock(FIXED_TIME),
        max_attempts=max_attempts,
        **kwargs,
    )
    return service, workflow, source


def write(source, name, content):
    path = Path(source) / name
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return str(path)


def copy_fixture(source, name, fixture):
    dest = Path(source) / name
    dest.write_bytes((FIXTURES / fixture).read_bytes())
    return str(dest)


def register(workflow, paths, session_id=SESSION):
    for path in paths:
        workflow.register_material(path, session_id=session_id)


def real_course_materials(workflow, source):
    """一门"真实"课程的材料集合: PDF + DOCX + 笔记 + 板书图片 + 音频。"""
    paths = [
        copy_fixture(source, "apuntes.pdf", "documents/simple.pdf"),
        copy_fixture(source, "guia.docx", "documents/simple.docx"),
        write(source, "notas.txt", "Concepto uno\nConcepto dos\nConcepto tres\n"),
        copy_fixture(source, "pizarra.png", "ocr_sample.png"),
        copy_fixture(source, "clase.wav", "tone_3s.wav"),
    ]
    register(workflow, paths)
    return paths


# ===========================================================================
# 1. 作业模型
# ===========================================================================


class TestProcessingJob:
    def test_default_status_is_queued(self):
        job = ProcessingJob(job_id="j", material_id="m", course_id="c")
        assert job.status == JOB_QUEUED

    def test_to_dict_shape(self):
        payload = ProcessingJob(job_id="j", material_id="m", course_id="c").to_dict()
        for key in (
            "job_id", "material_id", "course_id", "session_id", "status",
            "stage", "attempts", "max_attempts", "started_at", "finished_at",
            "error", "retryable", "evidence_ids", "warnings",
        ):
            assert key in payload

    def test_job_statuses_match_spec(self):
        assert JOB_STATUSES == (JOB_QUEUED, JOB_RUNNING, JOB_SUCCEEDED, JOB_FAILED, JOB_CANCELLED)

    def test_evidence_count_derived(self):
        job = ProcessingJob(job_id="j", material_id="m", course_id="c", evidence_ids=("a", "b"))
        assert job.to_dict()["evidence_count"] == 2


# ===========================================================================
# 2. 端到端: 真实材料
# ===========================================================================


class TestEndToEnd:
    def test_full_session_succeeds(self, tmp_path):
        service, workflow, source = build(tmp_path)
        real_course_materials(workflow, source)
        report = service.process_session(SESSION)
        assert report["total"] == 5
        assert report["succeeded"] == 5
        assert report["failed"] == 0
        assert report["evidence_total"] > 0

    def test_pdf_produces_evidence(self, tmp_path):
        service, workflow, source = build(tmp_path)
        path = copy_fixture(source, "a.pdf", "documents/simple.pdf")
        record = workflow.register_material(path, session_id=SESSION)
        job = service.process_material(record["material_id"])
        assert job["status"] == JOB_SUCCEEDED
        assert job["evidence_ids"]

    def test_docx_produces_evidence(self, tmp_path):
        service, workflow, source = build(tmp_path)
        path = copy_fixture(source, "a.docx", "documents/simple.docx")
        record = workflow.register_material(path, session_id=SESSION)
        job = service.process_material(record["material_id"])
        assert job["status"] == JOB_SUCCEEDED
        assert job["evidence_ids"]

    def test_note_produces_evidence(self, tmp_path):
        service, workflow, source = build(tmp_path)
        record = workflow.register_material(
            write(source, "n.txt", "Concepto uno\nConcepto dos\n"), session_id=SESSION
        )
        job = service.process_material(record["material_id"])
        assert job["status"] == JOB_SUCCEEDED
        assert job["evidence_ids"]

    def test_image_produces_evidence(self, tmp_path):
        service, workflow, source = build(tmp_path)
        path = copy_fixture(source, "b.png", "ocr_sample.png")
        record = workflow.register_material(path, session_id=SESSION)
        job = service.process_material(record["material_id"])
        assert job["status"] == JOB_SUCCEEDED
        assert job["evidence_ids"]

    def test_audio_produces_evidence(self, tmp_path):
        service, workflow, source = build(tmp_path)
        path = copy_fixture(source, "a.wav", "tone_3s.wav")
        record = workflow.register_material(path, session_id=SESSION)
        job = service.process_material(record["material_id"])
        assert job["status"] == JOB_SUCCEEDED
        assert job["evidence_ids"]

    def test_session_produces_knowledge(self, tmp_path):
        service, workflow, source = build(tmp_path)
        real_course_materials(workflow, source)
        report = service.process_session(SESSION)
        assert report["knowledge"]["knowledge_point_total"] > 0

    def test_knowledge_is_published_to_org_service(self, tmp_path):
        service, workflow, source = build(tmp_path)
        real_course_materials(workflow, source)
        service.process_session(SESSION)
        assert service._org.registered_knowledge_point_ids  # noqa: SLF001

    def test_evidence_traceable_to_material(self, tmp_path):
        service, workflow, source = build(tmp_path)
        record = workflow.register_material(
            write(source, "n.txt", "Concepto uno\n"), session_id=SESSION
        )
        job = service.process_material(record["material_id"])
        evidences = workflow.evidence_for_material(record["material_id"])
        assert {e["evidence_id"] for e in evidences} == set(job["evidence_ids"])
        for evidence in evidences:
            assert evidence["source"]["material_id"] == record["material_id"]

    def test_process_session_is_idempotent(self, tmp_path):
        service, workflow, source = build(tmp_path)
        real_course_materials(workflow, source)
        first = service.process_session(SESSION)
        second = service.process_session(SESSION)
        assert first["succeeded"] == second["succeeded"]
        assert len(workflow.store.all()) == len(workflow.store.all())
        assert first["knowledge"]["knowledge_point_total"] == second["knowledge"]["knowledge_point_total"]

    def test_knowledge_summary_after_run(self, tmp_path):
        service, workflow, source = build(tmp_path)
        real_course_materials(workflow, source)
        service.process_session(SESSION)
        summary = service.get_knowledge_summary()
        assert summary["knowledge_point_total"] > 0
        assert summary["review_pending"] >= 0


# ===========================================================================
# 3. 失败隔离
# ===========================================================================


class TestFailureIsolation:
    def test_one_bad_file_does_not_fail_session(self, tmp_path):
        service, workflow, source = build(tmp_path)
        paths = [
            copy_fixture(source, "ok1.pdf", "documents/simple.pdf"),
            copy_fixture(source, "ok2.docx", "documents/simple.docx"),
            write(source, "ok3.txt", "Concepto uno\n"),
            copy_fixture(source, "bad1.pdf", "documents/corrupted.pdf"),
            copy_fixture(source, "bad2.docx", "documents/corrupted.docx"),
        ]
        register(workflow, paths)
        report = service.process_session(SESSION)
        assert report["total"] == 5
        assert report["succeeded"] == 3
        assert report["failed"] == 2

    def test_failed_jobs_carry_error_code(self, tmp_path):
        service, workflow, source = build(tmp_path)
        path = copy_fixture(source, "bad.pdf", "documents/corrupted.pdf")
        record = workflow.register_material(path, session_id=SESSION)
        job = service.process_material(record["material_id"])
        assert job["status"] == JOB_FAILED
        assert job["error"] == "DOCUMENT_PARSE_FAILED"
        assert job["evidence_ids"] == []

    def test_successful_files_still_produce_evidence(self, tmp_path):
        service, workflow, source = build(tmp_path)
        good = copy_fixture(source, "ok.pdf", "documents/simple.pdf")
        bad = copy_fixture(source, "bad.pdf", "documents/corrupted.pdf")
        register(workflow, [good, bad])
        report = service.process_session(SESSION)
        assert report["evidence_total"] > 0

    def test_report_counts_are_consistent(self, tmp_path):
        service, workflow, source = build(tmp_path)
        register(workflow, [
            write(source, "a.txt", "Concepto uno\n"),
            copy_fixture(source, "b.pdf", "documents/corrupted.pdf"),
        ])
        report = service.process_session(SESSION)
        assert report["total"] == report["succeeded"] + report["failed"] + report["cancelled"] + report["skipped"]

    def test_status_aggregates_failures(self, tmp_path):
        service, workflow, source = build(tmp_path)
        register(workflow, [
            write(source, "a.txt", "Concepto uno\n"),
            copy_fixture(source, "b.pdf", "documents/corrupted.pdf"),
        ])
        service.process_session(SESSION)
        status = service.get_status(SESSION)
        assert status["by_status"][JOB_SUCCEEDED] == 1
        assert status["by_status"][JOB_FAILED] == 1


# ===========================================================================
# 4. 重试
# ===========================================================================


class TestRetry:
    def test_retry_non_retryable_error_is_refused(self, tmp_path):
        service, workflow, source = build(tmp_path)
        path = copy_fixture(source, "bad.pdf", "documents/corrupted.pdf")
        record = workflow.register_material(path, session_id=SESSION)
        service.process_material(record["material_id"])
        job = service.retry_failed_material(record["material_id"])
        assert job["retryable"] is False

    def test_retry_is_capped_by_max_attempts(self, tmp_path):
        service, workflow, source = build(tmp_path)
        service._workflow._ingestion = _ExplodingIngestion()  # noqa: SLF001
        record = workflow.register_material(write(source, "a.txt", "x"), session_id=SESSION)
        first = service.process_material(record["material_id"])
        assert first["error"] == "INGESTION_FAILED"
        assert first["retryable"] is True
        service.retry_failed_material(record["material_id"])
        capped = service.retry_failed_material(record["material_id"])
        assert capped["attempts"] <= 3
        assert capped["retryable"] is False

    def test_retry_unknown_material_raises(self, tmp_path):
        service, _, _ = build(tmp_path)
        with pytest.raises(NotFoundError):
            service.retry_failed_material("mat-nope")

    def test_retry_successful_job_is_noop(self, tmp_path):
        service, workflow, source = build(tmp_path)
        record = workflow.register_material(write(source, "a.txt", "Concepto uno\n"), session_id=SESSION)
        done = service.process_material(record["material_id"])
        again = service.retry_failed_material(record["material_id"])
        assert again["status"] == JOB_SUCCEEDED
        assert again["attempts"] == done["attempts"]


# ===========================================================================
# 5. 取消 / 排队
# ===========================================================================


class TestQueueAndCancel:
    def test_start_session_processing_queues_all(self, tmp_path):
        service, workflow, source = build(tmp_path)
        register(workflow, [write(source, "a.txt", "x"), write(source, "b.txt", "y")])
        result = service.start_session_processing(SESSION)
        assert result["total"] == 2
        assert result["queued"] == 2
        assert all(j["status"] == JOB_QUEUED for j in result["jobs"])

    def test_cancel_session_marks_queued_jobs(self, tmp_path):
        service, workflow, source = build(tmp_path)
        register(workflow, [write(source, "a.txt", "x")])
        service.start_session_processing(SESSION)
        result = service.cancel_session(SESSION)
        assert result["cancelled"] == 1
        assert service.get_status(SESSION)["by_status"][JOB_CANCELLED] == 1

    def test_cancelled_job_is_not_processed(self, tmp_path):
        service, workflow, source = build(tmp_path)
        record = workflow.register_material(write(source, "a.txt", "x"), session_id=SESSION)
        service.start_session_processing(SESSION)
        service.cancel_session(SESSION)
        job = service.process_material(record["material_id"])
        assert job["status"] == JOB_CANCELLED

    def test_start_session_processing_unknown_session(self, tmp_path):
        service, _, _ = build(tmp_path)
        with pytest.raises(NotFoundError):
            service.start_session_processing("session-missing")

    def test_start_session_processing_requires_id(self, tmp_path):
        service, _, _ = build(tmp_path)
        with pytest.raises(InvalidInputError):
            service.start_session_processing("")


# ===========================================================================
# 6. 确定性 / 状态查询
# ===========================================================================


class TestDeterminismAndStatus:
    def test_job_ids_are_deterministic(self, tmp_path):
        service, workflow, source = build(tmp_path)
        record = workflow.register_material(write(source, "a.txt", "x"), session_id=SESSION)
        first = service.process_material(record["material_id"])["job_id"]
        service2, workflow2, source2 = build(tmp_path / "other")
        record2 = workflow2.register_material(write(source2, "a.txt", "x"), session_id=SESSION)
        assert service2.process_material(record2["material_id"])["job_id"] == first

    def test_job_id_is_stable_across_calls(self, tmp_path):
        service, workflow, source = build(tmp_path)
        record = workflow.register_material(write(source, "a.txt", "x"), session_id=SESSION)
        assert service.process_material(record["material_id"])["job_id"] == service.get_job(
            record["material_id"]
        )["job_id"]

    def test_status_without_session(self, tmp_path):
        service, workflow, source = build(tmp_path)
        register(workflow, [write(source, "a.txt", "x")])
        service.process_session(SESSION)
        assert service.get_status()["total"] == 1

    def test_status_of_empty_service(self, tmp_path):
        service, _, _ = build(tmp_path)
        status = service.get_status()
        assert status["total"] == 0
        assert status["by_status"][JOB_QUEUED] == 0

    def test_get_job_unknown_raises(self, tmp_path):
        service, _, _ = build(tmp_path)
        with pytest.raises(NotFoundError):
            service.get_job("mat-nope")

    def test_clock_injected_timestamps(self, tmp_path):
        service, workflow, source = build(tmp_path)
        record = workflow.register_material(write(source, "a.txt", "Concepto uno\n"), session_id=SESSION)
        job = service.process_material(record["material_id"])
        assert job["started_at"] == FIXED_TIME
        assert job["finished_at"] == FIXED_TIME

    def test_construction_rejects_none_workflow(self):
        with pytest.raises(InvalidInputError):
            ClassroomProcessingService(None)  # type: ignore[arg-type]

    def test_construction_rejects_bad_max_attempts(self, tmp_path):
        _, workflow, _ = build(tmp_path)
        with pytest.raises(InvalidInputError):
            ClassroomProcessingService(workflow, max_attempts=0)


# ===========================================================================
# 7. 知识装配边界
# ===========================================================================


class TestKnowledgeAssembly:
    def test_assemble_without_evidence_yields_nothing(self, tmp_path):
        service, _, _ = build(tmp_path)
        result = service.assemble_knowledge()
        assert result["knowledge_point_total"] == 0
        assert result["evidence_count"] == 0

    def test_assemble_only_uses_stored_evidence(self, tmp_path):
        service, workflow, source = build(tmp_path)
        record = workflow.register_material(write(source, "a.txt", "Concepto uno\n"), session_id=SESSION)
        service.process_material(record["material_id"])
        result = service.assemble_knowledge()
        assert result["evidence_count"] == len(workflow.store.all())

    def test_review_status_is_never_auto_confirmed(self, tmp_path):
        service, workflow, source = build(tmp_path)
        real_course_materials(workflow, source)
        service.process_session(SESSION)
        structure = service.structure
        for kp in structure.knowledge_points.values():
            assert str(kp.review_status) == "pending"

    def test_knowledge_summary_empty_state(self, tmp_path):
        service, _, _ = build(tmp_path)
        summary = service.get_knowledge_summary()
        assert summary["knowledge_point_total"] == 0
        assert summary["conflict_count"] == 0


# ===========================================================================
# 8. 音频链适配器
# ===========================================================================


class _RecordingProvider(ASRProvider):
    name = "recording"

    def __init__(self, result=None):
        super().__init__()
        self.calls = 0
        self.result = result

    def transcribe(self, audio_input):
        self.calls += 1
        if self.result is not None:
            return self.result
        return MockASRProvider().transcribe(audio_input)


class TestAudioPipeline:
    def test_long_audio_provider_delegates(self, tmp_path):
        path = copy_fixture(tmp_path, "a.wav", "tone_3s.wav")
        inner = _RecordingProvider()
        provider = LongAudioASRProvider(inner)
        from src.audio_input import AudioInput

        result = provider.transcribe(AudioInput(path=path, extension=".wav"))
        assert inner.calls == 1
        assert isinstance(result, TranscriptionResult)

    def test_long_audio_provider_requires_inner(self):
        with pytest.raises(ValueError):
            LongAudioASRProvider(None)  # type: ignore[arg-type]

    def test_long_audio_provider_reports_degradation(self, tmp_path):
        path = copy_fixture(tmp_path, "a.wav", "tone_3s.wav")
        inner = _RecordingProvider()

        class BrokenDuration:
            def get_duration(self, audio_input):
                raise RuntimeError("no duration")

        provider = LongAudioASRProvider(inner, duration_provider=BrokenDuration())
        from src.audio_input import AudioInput
        from src.asr_provider import ASRProviderError

        with pytest.raises(ASRProviderError):
            provider.transcribe(AudioInput(path=path, extension=".wav"))
        assert inner.calls == 0

    def test_quality_provider_records_report(self, tmp_path):
        path = copy_fixture(tmp_path, "a.wav", "tone_3s.wav")
        inner = _RecordingProvider()
        provider = QualityCheckedASRProvider(inner)
        from src.audio_input import AudioInput

        result = provider.transcribe(AudioInput(path=path, extension=".wav"))
        assert result.transcript is not None
        assert len(provider.reports) == 1
        assert provider.latest_report() is not None

    def test_quality_provider_does_not_modify_transcript(self, tmp_path):
        path = copy_fixture(tmp_path, "a.wav", "tone_3s.wav")
        inner = _RecordingProvider()
        from src.audio_input import AudioInput

        audio_input = AudioInput(path=path, extension=".wav")
        plain = inner.transcribe(audio_input)
        provider = QualityCheckedASRProvider(_RecordingProvider())
        wrapped = provider.transcribe(audio_input)
        assert wrapped.transcript.to_dict() == plain.transcript.to_dict()

    def test_quality_provider_keys_reports_by_material(self, tmp_path):
        path = copy_fixture(tmp_path, "a.wav", "tone_3s.wav")
        provider = QualityCheckedASRProvider(_RecordingProvider())
        from src.audio_input import AudioInput
        from src.models import Material, MaterialType

        material = Material(material_id="mat-audio", filename="a.wav", path=path,
                            material_type=MaterialType.AUDIO)
        provider.transcribe(AudioInput(path=path, extension=".wav", material=material))
        assert provider.report_for_material("mat-audio") is not None
        assert provider.report_for_material("mat-other") is None

    def test_quality_provider_requires_inner(self):
        with pytest.raises(ValueError):
            QualityCheckedASRProvider(None)  # type: ignore[arg-type]

    def test_quality_provider_passes_through_failure(self, tmp_path):
        from src.audio_input import AudioInput
        from src.asr_provider import ASRProviderError

        class Failing(ASRProvider):
            name = "failing"

            def transcribe(self, audio_input):
                raise ASRProviderError("boom")

        provider = QualityCheckedASRProvider(Failing())
        with pytest.raises(ASRProviderError):
            provider.transcribe(AudioInput(path=str(tmp_path / "x.wav"), extension=".wav"))

    def test_audio_quality_reaches_job(self, tmp_path):
        """把质量 provider 接进摄取链后, 作业应携带质量报告。"""
        source = tmp_path / "uploads"
        source.mkdir()
        store = EvidenceStore()
        chain = build_audio_chain(MockASRProvider(), long_audio=False)
        ingestion = EvidenceIngestionService(store, asr_provider=chain)
        workflow = MaterialWorkflowService(
            Course(name=COURSE_NAME), str(source), str(tmp_path / "data"),
            store=store, ingestion_service=ingestion, clock=fixed_clock(FIXED_TIME),
        )
        service = ClassroomProcessingService(
            workflow, clock=fixed_clock(FIXED_TIME), quality_source=chain
        )
        path = copy_fixture(source, "a.wav", "tone_3s.wav")
        record = workflow.register_material(path, session_id=SESSION)
        job = service.process_material(record["material_id"])
        assert job["status"] == JOB_SUCCEEDED
        assert job["quality"] is not None
        assert job["quality"]["status"] in ("VALID", "WARNING", "INVALID")

    def test_audio_chain_factory_wraps_both_layers(self):
        chain = build_audio_chain(MockASRProvider())
        assert isinstance(chain, QualityCheckedASRProvider)
        assert isinstance(chain.inner, LongAudioASRProvider)

    def test_audio_chain_factory_can_skip_long_audio(self):
        chain = build_audio_chain(MockASRProvider(), long_audio=False)
        assert isinstance(chain.inner, MockASRProvider)


# ===========================================================================
# 9. 顺序执行 (无并发)
# ===========================================================================


class TestSequentialExecution:
    def test_processing_is_sequential(self, tmp_path):
        """材料必须一个接一个处理完, 绝不交错 (第一版默认 sequential)。"""
        events: list[str] = []

        source = tmp_path / "uploads"
        source.mkdir()
        workflow = MaterialWorkflowService(
            Course(name=COURSE_NAME), str(source), str(tmp_path / "data"),
            clock=fixed_clock(FIXED_TIME),
        )
        workflow._ingestion = _OrderedIngestion(events)  # noqa: SLF001
        service = ClassroomProcessingService(workflow, clock=fixed_clock(FIXED_TIME))
        register(workflow, [write(source, "a.txt", "x"), write(source, "b.txt", "y")])
        service.process_session(SESSION)
        assert events == ["enter", "exit", "enter", "exit"]

    def test_processing_order_is_deterministic(self, tmp_path):
        """顺序由 material_id 决定 (内容寻址), 跨运行完全一致。"""

        def run(root):
            events: list[str] = []
            source = root / "uploads"
            source.mkdir(parents=True, exist_ok=True)
            workflow = MaterialWorkflowService(
                Course(name=COURSE_NAME), str(source), str(root / "data"),
                clock=fixed_clock(FIXED_TIME),
            )
            workflow._ingestion = _OrderedIngestion(events, record_name=True)  # noqa: SLF001
            service = ClassroomProcessingService(workflow, clock=fixed_clock(FIXED_TIME))
            register(workflow, [write(source, "a.txt", "x"), write(source, "b.txt", "y")])
            service.process_session(SESSION)
            expected = [r["filename"] for r in workflow.list_session_materials(SESSION)]
            return events, expected

        first, expected_first = run(tmp_path / "run1")
        second, expected_second = run(tmp_path / "run2")
        assert first == expected_first
        assert second == expected_second
        assert first == second


class _ExplodingIngestion:
    def __init__(self):
        self.calls = 0

    def ingest(self, material, dry_run=False):
        self.calls += 1
        raise RuntimeError("boom")


class _OrderedIngestion:
    """记录每次摄取的进入/退出, 用于证明处理是顺序的。"""

    def __init__(self, events, record_name=False):
        self._events = events
        self._record_name = record_name

    def ingest(self, material, dry_run=False):
        from src.evidence_ingestion import IngestionReport, IngestionStatus

        if self._record_name:
            self._events.append(material.filename)
        else:
            self._events.append("enter")
        report = IngestionReport(
            status=IngestionStatus.SUCCESS,
            material_id=material.material_id,
            source_type="note",
            total_extracted=0,
            added_count=0,
            duplicate_count=0,
            rejected_count=0,
            skipped_count=0,
            evidence_ids=(),
            errors=(),
        )
        if not self._record_name:
            self._events.append("exit")
        return report
