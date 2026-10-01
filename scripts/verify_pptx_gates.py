# -*- coding: utf-8 -*-
"""变异自证: 逐个退回 PPTX 的门岗, 确认对应测试**精准变红**。

用法::

    python scripts/verify_pptx_gates.py

每一条 mutation 都是"把这一道门岗退回改动前的状态" (例如把 ``.pptx``
从白名单里删掉), 然后只跑**该门岗自己的那条测试**。期望:
红的必须是它, 绿的是全套。退出码非 0 说明有一道门岗没有测试兜住。

这不是单元测试的一部分 (它故意把源码改坏), 所以不在 ``tests/`` 下。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (门岗名, 文件, (退回前片段, 退回后片段), 必须变红的测试)
MUTATIONS = [
    (
        "document_input 白名单",
        "src/document_input.py",
        (
            'SUPPORTED_DOCUMENT_EXTENSIONS: tuple[str, ...] = (".pdf", ".docx", ".pptx")',
            'SUPPORTED_DOCUMENT_EXTENSIONS: tuple[str, ...] = (".pdf", ".docx")',
        ),
        "tests/test_document_input.py::TestPptxWhitelist::test_pptx_is_a_supported_document_extension",
    ),
    (
        "material_workflow 白名单",
        "src/application/material_workflow.py",
        (
            "_SUPPORTED = (\n    _NOTE_EXTS | _AUDIO_EXTS | _IMAGE_EXTS | _PDF_EXTS | _DOCX_EXTS | _PPTX_EXTS\n)",
            "_SUPPORTED = _NOTE_EXTS | _AUDIO_EXTS | _IMAGE_EXTS | _PDF_EXTS | _DOCX_EXTS",
        ),
        "tests/test_material_workflow.py::TestPptxWhitelistGate::test_pptx_is_in_the_supported_whitelist",
    ),
    (
        "material_workflow 分类表",
        "src/application/material_workflow.py",
        (
            "for _e in _PPTX_EXTS:\n    _EXTENSION_CATEGORIES[_e] = \"document\"",
            "for _e in _PPTX_EXTS:\n    _EXTENSION_CATEGORIES[_e] = \"note\"",
        ),
        "tests/test_material_workflow.py::TestPptxWhitelistGate::test_pptx_is_categorised_as_a_document",
    ),
    (
        "ingestion 路由",
        "src/evidence_ingestion.py",
        (
            '    if ext == ".pptx":\n        return SOURCE_PPTX\n',
            "",
        ),
        "tests/test_evidence_ingestion.py::TestPptxGate::test_pptx_adapter_extracts_documents",
    ),
    (
        "ingestion 适配器注册",
        "src/evidence_ingestion.py",
        (
            "            SOURCE_PPTX: PPTXExtractorAdapter(ocr_engine),\n",
            "",
        ),
        "tests/test_evidence_ingestion.py::TestPptxGate::test_pptx_adapter_is_registered_by_default",
    ),
    (
        "ai.pipeline 材料类型",
        "src/application/ai/pipeline.py",
        (
            'if source in ("note", "pdf", "docx", "pptx", "document")',
            'if source in ("note", "pdf", "docx", "document")',
        ),
        "tests/test_ai_understanding.py::TestAIProviderSelection::test_detect_material_kind_knows_pptx",
    ),
    (
        "遗留 evidence_extractor",
        "src/evidence_extractor.py",
        (
            "_DOCUMENT_EXTENSIONS = {'.pdf', '.docx', '.pptx'}",
            "_DOCUMENT_EXTENSIONS = {'.pdf', '.docx'}",
        ),
        "tests/test_evidence_extractor.py::TestEvidenceExtractorPPTX::test_pptx_is_a_document_extension",
    ),
    (
        "遗留 material_index",
        "src/material_index.py",
        (
            "    '.pptx': MaterialType.SYLLABUS,\n",
            "",
        ),
        "tests/test_material_index.py::TestMaterialIndex::test_pptx_recognition",
    ),
    (
        "document_evidence 定位符",
        "src/document_evidence.py",
        (
            "    if block.location:\n        return block.location\n",
            "",
        ),
        "tests/test_document_evidence.py::TestPptxEvidence::test_location_is_the_pptx_locator_not_a_docx_paragraph",
    ),
]


def run(test: str) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header", "-x"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode


def main() -> int:
    backup_dir = Path(tempfile.mkdtemp(prefix="pptx-gates-"))
    failures: list[str] = []
    try:
        for name, relative, (old, new), test in MUTATIONS:
            source = ROOT / relative
            original = source.read_text(encoding="utf-8")
            if old not in original:
                print(f"[SKIP] {name}: 在 {relative} 里找不到待退回的片段")
                failures.append(f"{name} (片段未找到)")
                continue
            backup = backup_dir / relative.replace("/", "__")
            shutil.copy2(source, backup)
            try:
                source.write_text(original.replace(old, new, 1), encoding="utf-8")
                code = run(test)
            finally:
                shutil.copy2(backup, source)
            status = "变红 OK" if code != 0 else "仍然绿 MISS"
            print(f"[{status}] {name} -> {test}")
            if code == 0:
                failures.append(f"{name} -> {test}")
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)

    if failures:
        print("\n以下门岗退回后测试仍然绿 —— 缺测试兜住:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\n全部门岗都有测试兜住。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
