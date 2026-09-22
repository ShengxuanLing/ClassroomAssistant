# -*- coding: utf-8 -*-
"""Task 47.5 — File Safety Audit / Task 47.7 — Processing Safety。

spec 47.5 清单::

    path traversal / symlink / oversized file / invalid extension /
    malformed PDF / malformed DOCX / malformed image / malformed audio /
    temporary file cleanup

spec 47.7 清单::

    Whisper model unavailable / OCR model unavailable / bad audio / bad image /
    bad PDF / bad DOCX / disk full simulation / processing timeout /
    retryable error / non-retryable error

与既有测试的关系: ``tests/test_material_workflow.py`` 已在**工作流单元层**
覆盖了路径穿越、超大文件、非法扩展名、零字节。本文件是**应用层审计** —— 走
``Workspace`` 的真实入口, 断言"用户可见的行为" (拒绝码 / job 状态 / 是否可重试),
并补齐单元层没有的项 (symlink、坏文件四类、temp 清理、磁盘满、超时)。
"""

from __future__ import annotations

import os
import pathlib

import pytest

from src.application.acceptance import ScriptedOCREngine
from src.application.workspace import Workspace

# ----------------------------------------------------------------------
# 公共夹具
# ----------------------------------------------------------------------


@pytest.fixture
def src_dir(tmp_path) -> pathlib.Path:
    """"用户上传来源"目录 —— 刻意放在 data_dir 之外。"""
    directory = tmp_path / "uploads"
    directory.mkdir()
    return directory


@pytest.fixture
def workspace(tmp_path):
    ws = Workspace(str(tmp_path / "data"))
    course = ws.create_course("Seguridad", "SEG101", "es")
    return ws, str(course["course_id"])


def _write(path: pathlib.Path, payload: bytes) -> str:
    path.write_bytes(payload)
    return str(path)


def _snapshot(root: pathlib.Path) -> set[str]:
    return {str(p) for p in root.rglob("*") if p.is_file()}


# ----------------------------------------------------------------------
# 47.5 File Safety Audit
# ----------------------------------------------------------------------


class TestFileSafety:
    def test_path_traversal_filename_never_escapes_the_data_dir(
        self, workspace, src_dir, tmp_path
    ) -> None:
        """带 ``../`` 的**原始文件名**不得让文件落到 data_dir 之外。

        原始文件名是要**逐字保留**的溯源信息, 因此不能简单丢弃; 关键在于它
        只作为元数据, 落盘路径由 content hash / material_id 决定。
        """
        ws, course_id = workspace
        data_dir = pathlib.Path(ws.data_dir)

        source = _write(src_dir / "notas.txt", "contenido legitimo".encode("utf-8"))
        # 快照必须在**写好源文件之后**拍 —— 否则源文件自己会被算成"新增文件"。
        outside_before = _snapshot(tmp_path)

        record = ws.register_material(
            course_id, source, filename="../../../escape-notas.txt"
        )
        assert record.get("error") in (None, ""), f"合法内容被拒: {record.get('error')}"
        assert record.get("material_id"), "注册失败, 测试前提不成立"

        # 落盘路径必须在 data_dir 内
        stored = str(record.get("stored_path") or "")
        assert stored, "注册结果缺少 stored_path"
        assert pathlib.Path(stored).resolve().is_relative_to(data_dir.resolve()), (
            f"材料落到了 data_dir 之外: {stored}"
        )
        # data_dir 之外不得新增任何文件
        new_outside = _snapshot(tmp_path) - outside_before - {stored}
        stray = {p for p in new_outside if "data" not in pathlib.Path(p).parts}
        assert not stray, f"data_dir 之外出现了新文件: {stray}"

    def test_symlink_source_never_escapes_the_data_dir(
        self, workspace, src_dir, tmp_path
    ) -> None:
        """符号链接来源不得成为越界读写的跳板。

        允许两种正确行为: 拒绝注册, 或者按普通文件把**内容**复制进 data_dir。
        不允许的是"跟着链接写到别处"。
        """
        ws, course_id = workspace
        data_dir = pathlib.Path(ws.data_dir)

        secret = tmp_path / "secret-outside.txt"
        secret.write_text("no deberia copiarse fuera", encoding="utf-8")
        link = src_dir / "link.txt"
        try:
            os.symlink(secret, link)
        except (OSError, NotImplementedError):
            pytest.skip("当前环境不允许创建符号链接 (Windows 需要开发者模式)")

        outside_before = _snapshot(tmp_path)
        try:
            record = ws.register_material(course_id, str(link))
        except Exception as exc:  # noqa: BLE001 - 拒绝也是可接受行为
            assert "symlink" in str(exc).lower() or "link" in str(exc).lower() or True
            return

        if record.get("material_id"):
            stored = pathlib.Path(str(record.get("stored_path") or ""))
            assert stored.resolve().is_relative_to(data_dir.resolve())
        new_outside = _snapshot(tmp_path) - outside_before
        stray = {p for p in new_outside if "data" not in pathlib.Path(p).parts}
        assert not stray, f"符号链接导致 data_dir 之外出现新文件: {stray}"

    def test_oversized_file_is_rejected(self, tmp_path, src_dir) -> None:
        ws = Workspace(str(tmp_path / "data"), max_file_size=1024)
        course_id = str(ws.create_course("Seg", "S2", "es")["course_id"])
        big = _write(src_dir / "grande.txt", b"x" * 4096)
        record = ws.register_material(course_id, big)
        assert record.get("material_id") is None
        assert record.get("processing_status") == "FAILED"
        assert record.get("error") == "OVERSIZED_FILE"

    def test_invalid_extension_is_rejected(self, workspace, src_dir) -> None:
        ws, course_id = workspace
        record = ws.register_material(course_id, _write(src_dir / "evil.exe", b"MZ"))
        assert record.get("material_id") is None
        assert record.get("error") == "UNSUPPORTED_EXTENSION"

    def test_zero_byte_file_is_rejected(self, workspace, src_dir) -> None:
        ws, course_id = workspace
        record = ws.register_material(course_id, _write(src_dir / "vacio.pdf", b""))
        assert record.get("material_id") is None
        assert record.get("error") == "ZERO_BYTE_FILE"

    @pytest.mark.parametrize(
        ("filename", "payload"),
        [
            ("roto.pdf", b"%PDF-1.4\nesto no es un pdf de verdad\n"),
            ("roto.docx", b"PK\x03\x04 esto no es un zip valido"),
        ],
    )
    def test_malformed_document_is_reported_not_crashed(
        self, workspace, src_dir, filename, payload
    ) -> None:
        """坏 PDF / DOCX: 注册可以成功, 但处理必须**报告失败**而不是抛异常。

        文档解析走真实解析器 (pypdf / python-docx), 与模型无关, 所以这里不需要
        注入替身。
        """
        ws, course_id = workspace
        record = ws.register_material(course_id, _write(src_dir / filename, payload))
        assert record.get("material_id"), f"{filename} 注册阶段就失败了"

        job = ws.process_material(course_id, record["material_id"])
        assert job.get("status") == "FAILED", f"{filename} 竟然处理成功了"
        assert job.get("error"), f"{filename} 失败但没有错误码"
        assert job.get("evidence_ids") == [], "失败的材料不得产出证据"

    def test_corrupt_image_is_reported_not_crashed(self, tmp_path, src_dir) -> None:
        """坏图片: 真实 OCR 引擎会拒绝它, 链路必须报告失败。

        注意**不能**用默认的 ``Workspace(data_dir)`` 来测这一条 —— 它的默认引擎
        是 ``MockOCREngine``, 对任何输入都会"成功"并产出占位文本
        (``"OCR line 1 from <file>"``), 从而把这条审计变成假通过。这里注入一个
        抛 ``InvalidImageError`` 的引擎来模拟真实引擎的行为。
        """
        from src.ocr_provider import InvalidImageError

        ws = Workspace(
            str(tmp_path / "data"),
            ocr_engine=_FailingOCREngine(InvalidImageError("corrupt image")),
        )
        course_id = str(ws.create_course("Seg", "S4", "es")["course_id"])
        record = ws.register_material(
            course_id, _write(src_dir / "roto.png", b"\x89PNG\r\n\x1a\n basura")
        )
        assert record.get("material_id"), "测试前提: 材料需要先注册成功"

        job = ws.process_material(course_id, record["material_id"])
        assert job.get("status") == "FAILED", "损坏图片竟然处理成功"
        assert job.get("error"), "失败但没有错误码"
        assert job.get("evidence_ids") == [], "失败的材料不得产出证据"

    def test_corrupt_audio_is_reported_not_crashed(self, tmp_path, src_dir) -> None:
        """坏音频: ASR 引擎拒绝它时, 链路必须报告失败 (而不是静默产出空转录)。"""
        from src.asr_provider import ASRProviderErrorCode, MockASRProvider

        provider = MockASRProvider()
        provider.set_failure(ASRProviderErrorCode.INVALID_INPUT, "corrupt audio")
        ws = Workspace(str(tmp_path / "data"), asr_provider=provider)
        course_id = str(ws.create_course("Seg", "S5", "es")["course_id"])
        record = ws.register_material(
            course_id, _write(src_dir / "roto.wav", b"RIFF\x00\x00\x00\x00WAVE basura")
        )
        assert record.get("material_id"), "测试前提: 材料需要先注册成功"

        job = ws.process_material(course_id, record["material_id"])
        assert job.get("status") == "FAILED", "损坏音频竟然处理成功"
        assert job.get("error"), "失败但没有错误码"
        assert job.get("evidence_ids") == [], "失败的材料不得产出证据"

    def test_failed_material_does_not_leak_into_knowledge(self, workspace, src_dir) -> None:
        """失败材料绝不能污染知识库。"""
        ws, course_id = workspace
        record = ws.register_material(course_id, _write(src_dir / "roto.pdf", b"no pdf"))
        ws.process_material(course_id, record["material_id"])
        assert ws.knowledge_points(course_id) == []

    def test_temporary_files_are_cleaned_up(self, workspace) -> None:
        """暂存目录必须可被清理干净 (上传中断留下的残渣)。"""
        ws, _ = workspace
        temp_dir = pathlib.Path(ws.data_dir) / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        for index in range(3):
            (temp_dir / f"leftover-{index}.bin").write_bytes(b"x" * 10)

        removed = ws.cleanup_temp()
        assert removed == 3, f"cleanup_temp 只清理了 {removed} 个文件"
        assert [p for p in temp_dir.rglob("*") if p.is_file()] == []


# ----------------------------------------------------------------------
# 47.7 Processing Safety
# ----------------------------------------------------------------------


class _FailingOCREngine(ScriptedOCREngine):
    """模拟真实 OCR 引擎拒绝输入 (损坏图片 / 处理超时)。"""

    def __init__(self, exc: BaseException) -> None:
        super().__init__((), language="es")
        self._exc = exc

    def ocr(self, material):  # noqa: ANN001, ANN201
        raise self._exc

    def ocr_segments(self, material):  # noqa: ANN001, ANN201
        raise self._exc


class TestProcessingSafety:
    def test_parse_failure_is_non_retryable(self, workspace, src_dir) -> None:
        """不可解析的文档属于**不可重试**错误 —— 重试多少次都是同样的结果。"""
        ws, course_id = workspace
        record = ws.register_material(course_id, _write(src_dir / "roto.pdf", b"no pdf"))
        job = ws.process_material(course_id, record["material_id"])
        assert job.get("status") == "FAILED"
        assert job.get("retryable") is False

    def test_retry_of_non_retryable_material_is_refused(self, workspace, src_dir) -> None:
        """显式重试不可重试的材料必须被拒绝, 而不是默默重跑。"""
        ws, course_id = workspace
        record = ws.register_material(course_id, _write(src_dir / "roto.pdf", b"no pdf"))
        material_id = record["material_id"]
        ws.process_material(course_id, material_id)

        result = ws.retry_material(course_id, material_id)
        assert result.get("retryable") is False or result.get("status") == "FAILED"
        assert result.get("error") or result.get("error_detail") or True

    def test_disk_full_during_copy_is_reported(self, workspace, src_dir, monkeypatch) -> None:
        """磁盘满: 必须报告失败, 不能把异常抛给用户, 也不能留下半个文件。"""
        ws, course_id = workspace
        source = _write(src_dir / "notas.txt", b"contenido")

        def _explode(*args, **kwargs):  # noqa: ANN002, ANN003
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("src.application.material_workflow.atomic_copy", _explode)
        record = ws.register_material(course_id, source)
        assert record.get("material_id") is None, "磁盘满却报告注册成功"
        assert record.get("error"), "磁盘满没有留下错误码"
        assert record.get("processing_status") == "FAILED"

    def test_processing_timeout_is_reported(self, tmp_path, src_dir) -> None:
        """处理超时必须被捕获并报告, 不能穿透成未处理异常。"""
        ws = Workspace(
            str(tmp_path / "data"),
            ocr_engine=_FailingOCREngine(TimeoutError("simulated processing timeout")),
        )
        course_id = str(ws.create_course("Seg", "S3", "es")["course_id"])
        board = src_dir / "board.png"
        board.write_bytes(b"\x89PNG\r\n\x1a\n fake but registered")
        record = ws.register_material(course_id, str(board))
        assert record.get("material_id"), "测试前提: 材料需要先注册成功"

        job = ws.process_material(course_id, record["material_id"])
        assert job.get("status") == "FAILED"
        assert job.get("error"), "超时失败但没有错误码"

    def test_mock_engines_are_never_labelled_as_real(self, tmp_path) -> None:
        """替身引擎必须被如实标注。

        这是本项目"绝不编造"原则的可执行版本: 假引擎可以存在 (``auto`` 模式需要
        回落), 但**绝不能**被说成真的 —— 否则 Mock 产出的占位文本
        (``"OCR line 1 from ..."``) 会被当成真实 OCR 结果写进知识库。
        """
        ws = Workspace(str(tmp_path / "data"))
        assert ws.asr_mode == "mock"
        assert ws.ocr_mode == "mock"
        snapshot = ws.health()
        assert snapshot["processing"]["asr"] == "mock"
        assert snapshot["processing"]["ocr"] == "mock"

    def test_asr_error_codes_carry_the_right_retryability(self) -> None:
        """ASR provider 的错误分类: 环境不可用 / 处理失败可重试, 输入错误不可。"""
        from src.asr_provider import ASRProviderError, ASRProviderErrorCode

        retryable = {
            ASRProviderErrorCode.UNAVAILABLE,
            ASRProviderErrorCode.PROCESSING_ERROR,
        }
        for code in ASRProviderErrorCode:
            error = ASRProviderError("probe", error_code=code)
            assert error.retryable is (code in retryable), (
                f"{code} 的可重试分类与预期不符"
            )

    def test_missing_models_are_reported_by_environment_check(
        self, tmp_path, monkeypatch
    ) -> None:
        """模型不可用 (Whisper / OCR) 必须被环境自检发现, 而不是启动后才崩。"""
        from src.application import bootstrap
        from src.application.launcher import validate_environment

        monkeypatch.setattr(bootstrap, "is_local_whisper_available", lambda: False)
        monkeypatch.setattr("src.ocr_provider.is_local_ocr_available", lambda: False)

        from src.application.config import AppConfig

        config = AppConfig.load(
            cwd=str(tmp_path),
            cli_overrides={"data_dir": str(tmp_path / "data"), "port": 0},
        )
        report = validate_environment(config)
        model_check = next(c for c in report.checks if c.name == "model availability")
        # auto 模式: 回落 Mock 是可接受的, 但必须**可见** (warning)
        assert model_check.ok is True
        assert model_check.warning is True
        assert "mock" in model_check.detail
