# -*- coding: utf-8 -*-
"""Task 35 tests: Real Classroom Material Workflow.

覆盖 spec 要求: PDF / DOCX / TXT / audio / image / duplicate /
invalid extension / zero byte / oversized / path traversal / missing
file / processing failure / retry / idempotency / cleanup。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.application.data_dirs import (
    CATEGORY_TO_DIR,
    DATA_LAYOUT_DIRS,
    atomic_copy,
    atomic_write_bytes,
    contains_traversal,
    ensure_data_layout,
    safe_join,
)
from src.application.errors import InvalidInputError, NotFoundError
from src.application.material_workflow import (
    COMPLETED,
    DEFAULT_MAX_ATTEMPTS,
    FAILED,
    MATERIAL_STATUSES,
    NON_RETRYABLE_ERROR_CODES,
    PROCESSING,
    REGISTERED,
    REGISTRY_SCHEMA_VERSION,
    RETRYABLE_ERROR_CODES,
    MaterialWorkflowService,
    VALIDATING,
)
from src.application.runtime import fixed_clock
from src.evidence_ingestion import (
    IngestionError,
    IngestionErrorCode,
    IngestionReport,
    IngestionStatus,
)
from src.models import Course

FIXTURES = Path(__file__).resolve().parent / "fixtures"
COURSE_NAME = "Algebra Lineal"
FIXED_TIME = "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_service(tmp_path, *, max_file_size=200 * 1024 * 1024, max_attempts=3, course=None):
    source = tmp_path / "uploads"
    source.mkdir(parents=True, exist_ok=True)
    data = tmp_path / "data"
    service = MaterialWorkflowService(
        course or Course(name=COURSE_NAME),
        str(source),
        str(data),
        max_file_size=max_file_size,
        max_attempts=max_attempts,
        clock=fixed_clock(FIXED_TIME),
    )
    return service, source


def write(source, name, content):
    p = Path(source) / name
    p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return str(p)


def copy_fixture(source, name, fixture):
    dest = Path(source) / name
    dest.write_bytes((FIXTURES / fixture).read_bytes())
    return str(dest)


class FlakyIngestionService:
    """注入用的摄取服务: 前 ``fail_times`` 次失败, 之后成功。"""

    def __init__(self, store, fail_times=1, code="EXTRACTOR_ERROR"):
        self.store = store
        self.fail_times = fail_times
        self.code = code
        self.calls = 0

    def ingest(self, material, dry_run=False):
        self.calls += 1
        if self.calls <= self.fail_times:
            return IngestionReport(
                status=IngestionStatus.FAILED,
                material_id=material.material_id,
                source_type="note",
                total_extracted=0,
                added_count=0,
                duplicate_count=0,
                rejected_count=0,
                skipped_count=0,
                evidence_ids=(),
                errors=(
                    IngestionError(code=self.code, message="transient failure", fatal=True),
                ),
            )
        return IngestionReport(
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


class ExplodingIngestionService:
    def __init__(self):
        self.calls = 0

    def ingest(self, material, dry_run=False):
        self.calls += 1
        raise RuntimeError("boom")


# ===========================================================================
# 1. 数据目录布局
# ===========================================================================


class TestDataLayout:
    def test_creates_all_spec_dirs(self, tmp_path):
        layout = ensure_data_layout(str(tmp_path / "data"))
        for name in DATA_LAYOUT_DIRS:
            assert os.path.isdir(getattr(layout, name))

    def test_spec_dir_names(self):
        assert DATA_LAYOUT_DIRS == (
            "materials",
            "audio",
            "images",
            "documents",
            "database",
            "logs",
            "backups",
            "temp",
        )

    def test_idempotent(self, tmp_path):
        first = ensure_data_layout(str(tmp_path / "data"))
        marker = os.path.join(first.documents, "keep.txt")
        Path(marker).write_text("keep", encoding="utf-8")
        second = ensure_data_layout(str(tmp_path / "data"))
        assert first == second
        assert os.path.isfile(marker)

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            ensure_data_layout("")

    def test_bucket_mapping(self, tmp_path):
        layout = ensure_data_layout(str(tmp_path / "data"))
        assert layout.bucket_for("note") == layout.documents
        assert layout.bucket_for("document") == layout.documents
        assert layout.bucket_for("audio") == layout.audio
        assert layout.bucket_for("image") == layout.images

    def test_bucket_unknown_falls_back(self, tmp_path):
        layout = ensure_data_layout(str(tmp_path / "data"))
        assert layout.bucket_for("nonsense") == layout.documents
        assert layout.bucket_for(None) == layout.documents

    def test_as_dict_and_relative(self, tmp_path):
        layout = ensure_data_layout(str(tmp_path / "data"))
        payload = layout.as_dict()
        assert set(payload) == {"root", *DATA_LAYOUT_DIRS}
        rel = layout.relative(os.path.join(layout.audio, "a", "b.wav"))
        assert rel == "audio/a/b.wav"

    def test_category_to_dir_covers_all_buckets(self):
        assert set(CATEGORY_TO_DIR.values()) <= {"documents", "audio", "images"}

    def test_contains_traversal_variants(self):
        assert contains_traversal("../x")
        assert contains_traversal("a/../../x")
        assert contains_traversal(r"a\..\x")
        assert not contains_traversal("a/b/c.txt")
        assert not contains_traversal(None)

    def test_safe_join_accepts_nested(self, tmp_path):
        root = str(tmp_path)
        joined = safe_join(root, "a", "b.txt")
        assert joined == os.path.join(os.path.abspath(root), "a", "b.txt")

    def test_safe_join_rejects_traversal(self, tmp_path):
        with pytest.raises(ValueError):
            safe_join(str(tmp_path), "..", "escape.txt")

    def test_safe_join_rejects_absolute(self, tmp_path):
        with pytest.raises(ValueError):
            safe_join(str(tmp_path), os.path.abspath(os.sep + "windows"))

    def test_safe_join_rejects_empty_part(self, tmp_path):
        with pytest.raises(ValueError):
            safe_join(str(tmp_path), "")

    def test_atomic_write_leaves_no_temp(self, tmp_path):
        target = str(tmp_path / "out" / "f.txt")
        atomic_write_bytes(target, b"hello")
        assert Path(target).read_bytes() == b"hello"
        assert os.listdir(os.path.dirname(target)) == ["f.txt"]

    def test_atomic_copy_leaves_no_temp(self, tmp_path):
        src = write(tmp_path, "s.txt", "data")
        target = str(tmp_path / "dst" / "t.txt")
        atomic_copy(src, target)
        assert Path(target).read_text(encoding="utf-8") == "data"
        assert os.listdir(os.path.dirname(target)) == ["t.txt"]


# ===========================================================================
# 2. 注册成功路径
# ===========================================================================


class TestRegisterSuccess:
    def test_register_txt(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "notes.txt", "hola\n"))
        assert record["processing_status"] == REGISTERED
        assert record["error"] is None
        assert record["material_id"].startswith("mat-")
        assert record["filename"] == "notes.txt"
        assert record["extension"] == ".txt"
        assert record["size"] > 0
        assert record["source_type"] == "note"
        assert record["course_id"].startswith("course-")
        assert record["duplicate"] is False

    def test_register_md(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "nota.md", "# t\nc"))
        assert record["source_type"] == "note"
        assert record["extension"] == ".md"

    def test_register_markdown_extension(self, tmp_path):
        service, source = make_service(tmp_path)
        assert service.register_material(write(source, "n.markdown", "t"))["source_type"] == "note"

    def test_register_audio_mp3(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "clase.mp3", b"\x49\x44\x33"))
        assert record["source_type"] == "audio"
        assert record["material_type"] == "audio"

    def test_register_audio_wav(self, tmp_path):
        service, source = make_service(tmp_path)
        assert service.register_material(write(source, "c.wav", b"RIFF"))["extension"] == ".wav"

    def test_register_image_png(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "pizarra.png", b"\x89PNG"))
        assert record["source_type"] == "image"
        assert record["material_type"] == "image"

    def test_register_image_jpg(self, tmp_path):
        service, source = make_service(tmp_path)
        assert service.register_material(write(source, "f.jpg", b"\xff\xd8\xff"))["extension"] == ".jpg"

    def test_register_image_webp(self, tmp_path):
        service, source = make_service(tmp_path)
        assert service.register_material(write(source, "f.webp", b"RIFFWEBP"))["source_type"] == "image"

    def test_register_pdf(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "apuntes.pdf", b"%PDF-1.4"))
        assert record["source_type"] == "document"
        assert record["extension"] == ".pdf"

    def test_register_docx(self, tmp_path):
        service, source = make_service(tmp_path)
        assert service.register_material(write(source, "d.docx", b"PK"))["source_type"] == "document"

    def test_register_stores_content_hash(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "a.txt", "contenido"))
        assert len(record["content_hash"]) == 64

    def test_register_copies_into_data_dir(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "x.txt", "datos"))
        assert os.path.isfile(record["stored_path"])
        assert record["stored_path"] != str(Path(source) / "x.txt")
        assert os.path.basename(record["stored_path"]).startswith(record["material_id"])
        assert record["stored_path"].startswith(service.layout.root)

    def test_register_routes_by_type_bucket(self, tmp_path):
        service, source = make_service(tmp_path)
        txt = service.register_material(write(source, "a.txt", "x"))
        png = service.register_material(write(source, "b.png", b"\x89PNG"))
        wav = service.register_material(write(source, "c.wav", b"RIFF"))
        assert txt["stored_path"].startswith(service.layout.documents)
        assert png["stored_path"].startswith(service.layout.images)
        assert wav["stored_path"].startswith(service.layout.audio)

    def test_register_original_file_untouched(self, tmp_path):
        service, source = make_service(tmp_path)
        path = write(source, "x.txt", "datos")
        service.register_material(path)
        assert Path(path).read_text(encoding="utf-8") == "datos"

    def test_register_with_session_and_language(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "x.txt", "a"), session_id="ses1", language="es")
        assert record["session_id"] == "ses1"
        assert record["language"] == "es"

    def test_register_metadata_fields_complete(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "x.txt", "a"))
        expected = {
            "material_id", "course_id", "session_id", "filename", "extension",
            "size", "created_at", "source_type", "processing_status", "error",
        }
        assert expected.issubset(record.keys())

    def test_register_is_deterministic(self, tmp_path):
        service, source = make_service(tmp_path)
        a = service.register_material(write(source, "same.txt", "identical"))
        service2, source2 = make_service(tmp_path / "other")
        b = service2.register_material(write(source2, "same.txt", "identical"))
        assert a["material_id"] == b["material_id"]
        assert a["content_hash"] == b["content_hash"]

    def test_created_at_uses_injected_clock(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "x.txt", "a"))
        assert record["created_at"] == FIXED_TIME

    def test_unicode_filename_preserved(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "课堂笔记-ñ.txt", "áéí"))
        assert record["filename"] == "课堂笔记-ñ.txt"
        assert os.path.isfile(record["stored_path"])

    def test_unicode_content_preserved(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "u.txt", "función çatalà 中文"))
        assert Path(record["stored_path"]).read_text(encoding="utf-8") == "función çatalà 中文"


# ===========================================================================
# 3. 注册拒绝路径 (安全)
# ===========================================================================


class TestRegisterRejection:
    def test_unsupported_extension(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "evil.exe", b"MZ"))
        assert record["processing_status"] == FAILED
        assert record["error"] == "UNSUPPORTED_EXTENSION"
        assert record["material_id"] is None

    def test_no_extension(self, tmp_path):
        service, source = make_service(tmp_path)
        assert service.register_material(write(source, "README", b"x"))["error"] == "UNSUPPORTED_EXTENSION"

    def test_zero_byte(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "empty.txt", b""))
        assert record["error"] == "ZERO_BYTE_FILE"
        assert record["material_id"] is None

    def test_oversized(self, tmp_path):
        service, source = make_service(tmp_path, max_file_size=10)
        record = service.register_material(write(source, "big.txt", b"x" * 11))
        assert record["error"] == "OVERSIZED_FILE"

    def test_exactly_at_limit_accepted(self, tmp_path):
        service, source = make_service(tmp_path, max_file_size=10)
        record = service.register_material(write(source, "ok.txt", b"x" * 10))
        assert record["processing_status"] == REGISTERED

    def test_missing_file(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(str(Path(source) / "nope.txt"))
        assert record["error"] == "FILE_NOT_FOUND"

    def test_directory_is_rejected(self, tmp_path):
        service, source = make_service(tmp_path)
        nested = Path(source) / "folder.txt"
        nested.mkdir()
        assert service.register_material(str(nested))["error"] == "FILE_NOT_FOUND"

    def test_path_traversal_dotdot(self, tmp_path):
        service, source = make_service(tmp_path)
        evil = str(Path(source) / ".." / "escape.txt")
        record = service.register_material(evil)
        assert record["error"] == "PATH_TRAVERSAL"

    def test_path_traversal_windows_separators(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material("C:\\uploads\\..\\..\\windows\\system32\\evil.txt")
        assert record["error"] == "PATH_TRAVERSAL"

    def test_path_traversal_not_copied(self, tmp_path):
        service, source = make_service(tmp_path)
        (tmp_path / "escape.txt").write_text("secret", encoding="utf-8")
        service.register_material(str(Path(source) / ".." / "escape.txt"))
        assert service.list_materials() == []
        assert os.listdir(service.layout.documents) == []

    def test_empty_path_raises(self, tmp_path):
        service, _ = make_service(tmp_path)
        with pytest.raises(InvalidInputError):
            service.register_material("")

    def test_non_string_path_raises(self, tmp_path):
        service, _ = make_service(tmp_path)
        with pytest.raises(InvalidInputError):
            service.register_material(None)

    def test_rejected_record_is_not_registered(self, tmp_path):
        service, source = make_service(tmp_path)
        service.register_material(write(source, "evil.exe", b"MZ"))
        assert service.list_materials() == []
        assert service.get_processing_status()["total"] == 0


# ===========================================================================
# 4. 重复与幂等
# ===========================================================================


class TestDuplicates:
    def test_same_file_twice_is_duplicate(self, tmp_path):
        service, source = make_service(tmp_path)
        path = write(source, "a.txt", "same")
        first = service.register_material(path)
        second = service.register_material(path)
        assert second["material_id"] == first["material_id"]
        assert second["duplicate"] is True
        assert len(service.list_materials()) == 1

    def test_same_content_same_name_different_dir(self, tmp_path):
        service, source = make_service(tmp_path)
        other = tmp_path / "elsewhere"
        other.mkdir()
        first = service.register_material(write(source, "a.txt", "same"))
        second = service.register_material(write(other, "a.txt", "same"))
        assert second["material_id"] == first["material_id"]
        assert second["duplicate"] is True

    def test_same_content_different_name_reuses_copy(self, tmp_path):
        service, source = make_service(tmp_path)
        first = service.register_material(write(source, "a.txt", "same"))
        second = service.register_material(write(source, "b.txt", "same"))
        assert second["material_id"] != first["material_id"]
        assert second["duplicate"] is True
        assert second["duplicate_of"] == first["material_id"]
        assert second["stored_path"] == first["stored_path"]
        # 只有一份物理副本
        copies = [
            f for f in os.listdir(service.layout.documents)
            if os.path.isdir(os.path.join(service.layout.documents, f))
        ]
        assert len(copies) == 1
        stored = [
            os.path.join(base, name)
            for base, _d, files in os.walk(service.layout.documents)
            for name in files
        ]
        assert len(stored) == 1

    def test_different_content_same_name_not_duplicate(self, tmp_path):
        service, source = make_service(tmp_path)
        first = service.register_material(write(source, "a.txt", "one"))
        second = service.register_material(write(source, "a.txt", "two"))
        assert second["duplicate"] is False
        assert second["material_id"] != first["material_id"]
        assert len(service.list_materials()) == 2

    def test_register_three_times_still_one(self, tmp_path):
        service, source = make_service(tmp_path)
        path = write(source, "a.txt", "same")
        for _ in range(3):
            service.register_material(path)
        assert len(service.list_materials()) == 1

    def test_duplicate_across_courses_is_separate(self, tmp_path):
        source = tmp_path / "uploads"
        source.mkdir()
        data = tmp_path / "data"
        path = write(source, "a.txt", "same")
        s1 = MaterialWorkflowService(Course(name="A"), str(source), str(data), clock=fixed_clock(FIXED_TIME))
        s2 = MaterialWorkflowService(Course(name="B"), str(source), str(data), clock=fixed_clock(FIXED_TIME))
        assert s1.register_material(path)["material_id"] != s2.register_material(path)["material_id"]

    def test_batch_counts_duplicates(self, tmp_path):
        service, source = make_service(tmp_path)
        a = write(source, "a.txt", "one")
        report = service.register_material_batch([a, a, write(source, "b.txt", "two")])
        assert report["total"] == 3
        assert report["accepted"] == 3
        assert report["duplicates"] == 1


# ===========================================================================
# 5. 校验状态机
# ===========================================================================


class TestValidation:
    def test_validate_advances_to_validating(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "a.txt", "x"))
        validated = service.validate_material(record["material_id"])
        assert validated["processing_status"] == VALIDATING

    def test_validate_missing_material_raises(self, tmp_path):
        service, _ = make_service(tmp_path)
        with pytest.raises(NotFoundError):
            service.validate_material("mat-nope")

    def test_validate_empty_id_raises(self, tmp_path):
        service, _ = make_service(tmp_path)
        with pytest.raises(InvalidInputError):
            service.validate_material("")

    def test_validate_detects_missing_copy(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "a.txt", "x"))
        os.remove(record["stored_path"])
        assert service.validate_material(record["material_id"])["error"] == "STORAGE_ERROR"

    def test_validate_detects_size_mismatch(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "a.txt", "x"))
        Path(record["stored_path"]).write_bytes(b"much longer content")
        assert service.validate_material(record["material_id"])["error"] == "STORAGE_ERROR"

    def test_status_machine_values(self):
        assert MATERIAL_STATUSES == (REGISTERED, VALIDATING, PROCESSING, COMPLETED, FAILED)


# ===========================================================================
# 6. 处理 (Ingestion -> Evidence)
# ===========================================================================


class TestProcessing:
    def test_process_note_produces_evidence(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "n.txt", "Concepto uno\nConcepto dos\n"))
        done = service.process_material(record["material_id"])
        assert done["processing_status"] == COMPLETED
        assert done["evidence_count"] > 0
        assert done["error"] is None

    def test_process_pdf_fixture(self, tmp_path):
        service, source = make_service(tmp_path)
        path = copy_fixture(source, "simple.pdf", "documents/simple.pdf")
        record = service.register_material(path)
        done = service.process_material(record["material_id"])
        assert done["processing_status"] == COMPLETED
        assert done["evidence_count"] > 0

    def test_process_docx_fixture(self, tmp_path):
        service, source = make_service(tmp_path)
        path = copy_fixture(source, "simple.docx", "documents/simple.docx")
        record = service.register_material(path)
        done = service.process_material(record["material_id"])
        assert done["processing_status"] == COMPLETED
        assert done["evidence_count"] > 0

    def test_process_image_fixture(self, tmp_path):
        service, source = make_service(tmp_path)
        path = copy_fixture(source, "board.png", "board.png")
        record = service.register_material(path)
        done = service.process_material(record["material_id"])
        assert done["processing_status"] == COMPLETED
        assert done["evidence_count"] > 0

    def test_process_audio_fixture(self, tmp_path):
        service, source = make_service(tmp_path)
        path = copy_fixture(source, "tone_3s.wav", "tone_3s.wav")
        record = service.register_material(path)
        done = service.process_material(record["material_id"])
        assert done["processing_status"] == COMPLETED
        assert done["evidence_count"] > 0

    def test_evidence_is_traceable_to_material(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "n.txt", "Concepto uno\n"))
        service.process_material(record["material_id"])
        evidences = service.evidence_for_material(record["material_id"])
        assert evidences
        for ev in evidences:
            assert ev["source"]["material_id"] == record["material_id"]

    def test_process_is_idempotent(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "n.txt", "Concepto uno\n"))
        first = service.process_material(record["material_id"])
        second = service.process_material(record["material_id"])
        assert first["evidence_ids"] == second["evidence_ids"]
        assert first["attempts"] == second["attempts"]
        assert len(service.store.all()) == len(first["evidence_ids"])

    def test_process_unknown_material_raises(self, tmp_path):
        service, _ = make_service(tmp_path)
        with pytest.raises(NotFoundError):
            service.process_material("mat-nope")

    def test_corrupt_pdf_fails_processing(self, tmp_path):
        service, source = make_service(tmp_path)
        path = copy_fixture(source, "corrupted.pdf", "documents/corrupted.pdf")
        record = service.register_material(path)
        done = service.process_material(record["material_id"])
        assert done["processing_status"] == FAILED
        assert done["error"] == "DOCUMENT_PARSE_FAILED"
        assert done["evidence_ids"] == []

    def test_empty_pdf_completes_with_warning(self, tmp_path):
        service, source = make_service(tmp_path)
        path = copy_fixture(source, "empty.pdf", "documents/empty.pdf")
        record = service.register_material(path)
        done = service.process_material(record["material_id"])
        assert done["processing_status"] == COMPLETED
        assert done["warning"] == "NO_TEXT_EXTRACTED"

    def test_exploding_ingestion_is_contained(self, tmp_path):
        service, source = make_service(tmp_path)
        service._ingestion = ExplodingIngestionService()  # noqa: SLF001 - 注入失败场景
        record = service.register_material(write(source, "n.txt", "x"))
        done = service.process_material(record["material_id"])
        assert done["processing_status"] == FAILED
        assert done["error"] == "INGESTION_FAILED"
        assert done["error_detail"] == "RuntimeError"

    def test_processing_marks_attempts(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "n.txt", "x"))
        done = service.process_material(record["material_id"])
        assert done["attempts"] == 1

    def test_completed_material_is_not_reprocessed(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "n.txt", "Concepto\n"))
        service.process_material(record["material_id"])
        again = service.process_material(record["material_id"])
        assert again["attempts"] == 1


# ===========================================================================
# 7. 重试
# ===========================================================================


class TestRetry:
    def test_retry_succeeds_after_transient_failure(self, tmp_path):
        service, source = make_service(tmp_path)
        service._ingestion = FlakyIngestionService(service.store, fail_times=1)  # noqa: SLF001
        record = service.register_material(write(source, "n.txt", "Concepto\n"))
        failed = service.process_material(record["material_id"])
        assert failed["processing_status"] == FAILED
        assert failed["retryable"] is True
        retried = service.retry_material(record["material_id"])
        assert retried["processing_status"] == COMPLETED
        assert retried["attempts"] == 2

    def test_retry_is_bounded_by_max_attempts(self, tmp_path):
        service, source = make_service(tmp_path, max_attempts=2)
        service._ingestion = ExplodingIngestionService()  # noqa: SLF001
        record = service.register_material(write(source, "n.txt", "x"))
        service.process_material(record["material_id"])
        service.retry_material(record["material_id"])
        third = service.retry_material(record["material_id"])
        assert third["attempts"] == 2
        assert third["retryable"] is False

    def test_structural_rejection_never_retried(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "evil.exe", b"MZ"))
        # 被拒绝的文件没有 material_id, 直接确认错误码不可重试
        assert record["error"] in NON_RETRYABLE_ERROR_CODES

    def test_retryable_and_non_retryable_disjoint(self):
        assert RETRYABLE_ERROR_CODES.isdisjoint(NON_RETRYABLE_ERROR_CODES)

    def test_retry_completed_material_is_noop(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "n.txt", "Concepto\n"))
        done = service.process_material(record["material_id"])
        assert service.retry_material(record["material_id"])["attempts"] == done["attempts"]

    def test_retry_unknown_material_raises(self, tmp_path):
        service, _ = make_service(tmp_path)
        with pytest.raises(NotFoundError):
            service.retry_material("mat-nope")

    def test_default_max_attempts_is_three(self):
        assert DEFAULT_MAX_ATTEMPTS == 3

    def test_non_retryable_error_clears_flag(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "n.txt", "Concepto\n"))
        service._get_record(record["material_id"])["error"] = "PATH_TRAVERSAL"  # noqa: SLF001
        service._get_record(record["material_id"])["processing_status"] = FAILED  # noqa: SLF001
        result = service.retry_material(record["material_id"])
        assert result["retryable"] is False
        assert result["processing_status"] == FAILED


# ===========================================================================
# 8. 注册表持久化 (重启)
# ===========================================================================


class TestRegistryPersistence:
    def test_registry_file_written(self, tmp_path):
        service, source = make_service(tmp_path)
        service.register_material(write(source, "a.txt", "x"))
        assert os.path.isfile(service.registry_path)

    def test_registry_payload_shape(self, tmp_path):
        service, source = make_service(tmp_path)
        service.register_material(write(source, "a.txt", "x"))
        payload = json.loads(Path(service.registry_path).read_text(encoding="utf-8"))
        assert payload["schema_version"] == REGISTRY_SCHEMA_VERSION
        assert payload["course_id"] == service.course_id
        assert len(payload["materials"]) == 1

    def test_registry_is_deterministic(self, tmp_path):
        service, source = make_service(tmp_path)
        service.register_material(write(source, "a.txt", "x"))
        first = Path(service.registry_path).read_bytes()
        service._persist_registry()  # noqa: SLF001
        assert Path(service.registry_path).read_bytes() == first

    def test_reload_after_restart(self, tmp_path):
        source = tmp_path / "uploads"
        source.mkdir()
        data = str(tmp_path / "data")
        course = Course(name=COURSE_NAME)
        first = MaterialWorkflowService(course, str(source), data, clock=fixed_clock(FIXED_TIME))
        record = first.register_material(write(source, "a.txt", "x"))
        second = MaterialWorkflowService(Course(name=COURSE_NAME), str(source), data, clock=fixed_clock(FIXED_TIME))
        reloaded = second.get_material(record["material_id"])
        assert reloaded["filename"] == "a.txt"
        assert reloaded["content_hash"] == record["content_hash"]

    def test_duplicate_detected_after_restart(self, tmp_path):
        source = tmp_path / "uploads"
        source.mkdir()
        data = str(tmp_path / "data")
        path = write(source, "a.txt", "x")
        first = MaterialWorkflowService(Course(name=COURSE_NAME), str(source), data, clock=fixed_clock(FIXED_TIME))
        first.register_material(path)
        second = MaterialWorkflowService(Course(name=COURSE_NAME), str(source), data, clock=fixed_clock(FIXED_TIME))
        assert second.register_material(path)["duplicate"] is True

    def test_corrupt_registry_is_tolerated(self, tmp_path):
        source = tmp_path / "uploads"
        source.mkdir()
        data = tmp_path / "data"
        service = MaterialWorkflowService(Course(name=COURSE_NAME), str(source), str(data), clock=fixed_clock(FIXED_TIME))
        Path(service.registry_path).write_text("{not json", encoding="utf-8")
        reloaded = MaterialWorkflowService(Course(name=COURSE_NAME), str(source), str(data), clock=fixed_clock(FIXED_TIME))
        assert reloaded.list_materials() == []

    def test_foreign_course_registry_ignored(self, tmp_path):
        source = tmp_path / "uploads"
        source.mkdir()
        data = str(tmp_path / "data")
        a = MaterialWorkflowService(Course(name="A"), str(source), data, clock=fixed_clock(FIXED_TIME))
        a.register_material(write(source, "a.txt", "x"))
        b = MaterialWorkflowService(Course(name="B"), str(source), data, clock=fixed_clock(FIXED_TIME))
        assert b.list_materials() == []

    def test_registry_snapshot(self, tmp_path):
        service, source = make_service(tmp_path)
        service.register_material(write(source, "a.txt", "x"))
        snapshot = service.registry_snapshot()
        assert snapshot["course_id"] == service.course_id
        assert snapshot["schema_version"] == REGISTRY_SCHEMA_VERSION


# ===========================================================================
# 9. 批量
# ===========================================================================


class TestBatch:
    def test_batch_isolates_rejection(self, tmp_path):
        service, source = make_service(tmp_path)
        report = service.register_material_batch(
            [
                write(source, "ok.txt", "fine"),
                write(source, "bad.exe", b"MZ"),
                write(source, "empty.txt", b""),
            ]
        )
        assert report["total"] == 3
        assert report["accepted"] == 1
        assert report["rejected"] == 2

    def test_batch_empty_list(self, tmp_path):
        service, _ = make_service(tmp_path)
        report = service.register_material_batch([])
        assert report["total"] == 0

    def test_batch_rejects_string_input(self, tmp_path):
        service, _ = make_service(tmp_path)
        with pytest.raises(InvalidInputError):
            service.register_material_batch("a.txt")

    def test_batch_handles_blank_entry(self, tmp_path):
        service, _ = make_service(tmp_path)
        report = service.register_material_batch(["", write(tmp_path / "uploads", "a.txt", "x")])
        assert report["rejected"] == 1
        assert report["materials"][0]["error"] == "EMPTY_PATH"

    def test_batch_is_idempotent(self, tmp_path):
        service, source = make_service(tmp_path)
        paths = [write(source, "a.txt", "1"), write(source, "b.txt", "2")]
        service.register_material_batch(paths)
        report = service.register_material_batch(paths)
        assert report["duplicates"] == 2
        assert len(service.list_materials()) == 2


# ===========================================================================
# 10. 状态 / 查询 / 清理
# ===========================================================================


class TestStatusAndCleanup:
    def test_processing_status_counts(self, tmp_path):
        service, source = make_service(tmp_path)
        service.register_material(write(source, "a.txt", "one"))
        service.register_material(write(source, "b.txt", "two"))
        status = service.get_processing_status()
        assert status["total"] == 2
        assert status["by_status"][REGISTERED] == 2
        assert status["pending"] == 2

    def test_status_after_processing(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "a.txt", "Concepto\n"))
        service.process_material(record["material_id"])
        status = service.get_processing_status()
        assert status["completed"] == 1
        assert status["evidence_total"] > 0

    def test_list_materials_sorted(self, tmp_path):
        service, source = make_service(tmp_path)
        service.register_material(write(source, "b.txt", "bbb"))
        service.register_material(write(source, "a.txt", "aaa"))
        ids = [m["material_id"] for m in service.list_materials()]
        assert ids == sorted(ids)

    def test_list_session_materials(self, tmp_path):
        service, source = make_service(tmp_path)
        service.register_material(write(source, "a.txt", "1"), session_id="s1")
        service.register_material(write(source, "b.txt", "2"), session_id="s2")
        assert len(service.list_session_materials("s1")) == 1

    def test_list_session_materials_requires_id(self, tmp_path):
        service, _ = make_service(tmp_path)
        with pytest.raises(InvalidInputError):
            service.list_session_materials("")

    def test_list_rejected(self, tmp_path):
        service, source = make_service(tmp_path)
        service.register_material(write(source, "bad.exe", b"MZ"))
        # 被拒绝的文件不进入注册表, 因此 rejected 列表只含处理失败的
        assert service.list_rejected() == []

    def test_cleanup_removes_copies_not_originals(self, tmp_path):
        service, source = make_service(tmp_path)
        path = write(source, "a.txt", "datos")
        record = service.register_material(path)
        service.cleanup()
        assert not os.path.exists(record["stored_path"])
        assert os.path.isfile(path)
        assert service.list_materials() == []

    def test_cleanup_removes_registry(self, tmp_path):
        service, source = make_service(tmp_path)
        service.register_material(write(source, "a.txt", "x"))
        service.cleanup()
        assert not os.path.exists(service.registry_path)

    def test_cleanup_is_idempotent(self, tmp_path):
        service, source = make_service(tmp_path)
        service.register_material(write(source, "a.txt", "x"))
        service.cleanup()
        assert service.cleanup()["removed"] == []

    def test_cleanup_temp(self, tmp_path):
        service, _ = make_service(tmp_path)
        stray = Path(service.layout.temp) / "leftover.part"
        stray.write_bytes(b"junk")
        assert service.cleanup_temp() == 1
        assert not stray.exists()

    def test_evidence_for_unknown_material_raises(self, tmp_path):
        service, _ = make_service(tmp_path)
        with pytest.raises(NotFoundError):
            service.evidence_for_material("mat-nope")

    def test_no_temp_leftovers_after_full_flow(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "a.txt", "Concepto\n"))
        service.process_material(record["material_id"])
        assert os.listdir(service.layout.temp) == []

    def test_no_part_files_anywhere(self, tmp_path):
        service, source = make_service(tmp_path)
        service.register_material(write(source, "a.txt", "x"))
        leftovers = [
            os.path.join(base, name)
            for base, _d, files in os.walk(service.layout.root)
            for name in files
            if name.endswith(".part")
        ]
        assert leftovers == []


# ===========================================================================
# 11. 构造期校验
# ===========================================================================


class TestConstruction:
    def test_rejects_none_course(self, tmp_path):
        with pytest.raises(InvalidInputError):
            MaterialWorkflowService(None, str(tmp_path), str(tmp_path / "d"))

    def test_rejects_empty_source_root(self, tmp_path):
        with pytest.raises(InvalidInputError):
            MaterialWorkflowService(Course(name="A"), "", str(tmp_path / "d"))

    def test_rejects_bad_max_file_size(self, tmp_path):
        with pytest.raises(InvalidInputError):
            MaterialWorkflowService(Course(name="A"), str(tmp_path), str(tmp_path / "d"), max_file_size=0)

    def test_rejects_bad_max_attempts(self, tmp_path):
        with pytest.raises(InvalidInputError):
            MaterialWorkflowService(Course(name="A"), str(tmp_path), str(tmp_path / "d"), max_attempts=0)

    def test_layout_is_created(self, tmp_path):
        service, _ = make_service(tmp_path)
        assert os.path.isdir(service.layout.root)
        assert service.layout.root.startswith(str(tmp_path))


# ===========================================================================
# 12. 知识注册边界 (不允许伪造事实)
# ===========================================================================


class TestKnowledgeRegistration:
    def test_unknown_evidence_ref_rejected(self, tmp_path):
        service, _ = make_service(tmp_path)
        with pytest.raises(NotFoundError):
            service.register_knowledge_point(
                {
                    "knowledge_id": "kp-1",
                    "title": "t",
                    "content": "c",
                    "evidence_refs": ["ev-does-not-exist"],
                }
            )

    def test_missing_knowledge_id_rejected(self, tmp_path):
        service, _ = make_service(tmp_path)
        with pytest.raises(InvalidInputError):
            service.register_knowledge_point({"title": "t", "content": "c"})

    def test_non_mapping_rejected(self, tmp_path):
        service, _ = make_service(tmp_path)
        with pytest.raises(InvalidInputError):
            service.register_knowledge_point(["not", "a", "mapping"])

    def test_registration_with_real_evidence(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "n.txt", "Concepto uno\n"))
        service.process_material(record["material_id"])
        evidences = service.evidence_for_material(record["material_id"])
        assert evidences
        kp = service.register_knowledge_point(
            {
                "knowledge_id": "kp-1",
                "title": "Concepto",
                "content": "Concepto uno",
                "evidence_refs": [evidences[0]["evidence_id"]],
            }
        )
        assert kp["knowledge_id"] == "kp-1"

    def test_registration_is_idempotent(self, tmp_path):
        service, source = make_service(tmp_path)
        record = service.register_material(write(source, "n.txt", "Concepto uno\n"))
        service.process_material(record["material_id"])
        evidences = service.evidence_for_material(record["material_id"])
        payload = {
            "knowledge_id": "kp-1",
            "title": "Concepto",
            "content": "Concepto uno",
            "evidence_refs": [evidences[0]["evidence_id"]],
        }
        service.register_knowledge_point(payload)
        service.register_knowledge_point(payload)
        assert service._org.registered_knowledge_point_ids == frozenset({"kp-1"})  # noqa: SLF001
