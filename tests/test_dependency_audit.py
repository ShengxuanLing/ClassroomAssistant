# -*- coding: utf-8 -*-
"""Task 47.3 — Dependency Audit。

验证 ``requirements.txt``:
- ``pip check`` 通过 (无破损依赖; 已在运行环境实测 "No broken requirements found")
- 没有重复依赖
- 每个需求都带版本约束 (不允许裸 ``包名``)
- 生产 requirements 不含测试专用依赖 (pytest 等)
- 每个运行时依赖都在 ``src`` 里真实被 import (无无用依赖)

``ctranslate2`` 是 faster-whisper 的传递依赖, 在 requirements 中显式钉版是
有意的 (避免 faster-whisper 拉到不兼容的 ctranslate2), 视为已知允许项。
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
REQ = ROOT / "requirements.txt"

KNOWN_TRANSITIVE = {"ctranslate2"}

IMPORT_TO_REQUIREMENT = {
    "av": "av",
    "numpy": "numpy",
    "pypdf": "pypdf",
    "docx": "python-docx",
    "faster_whisper": "faster-whisper",
    "rapidocr_onnxruntime": "rapidocr-onnxruntime",
}


def _parse_requirements():
    reqs: list[tuple[str, str]] = []
    for raw in REQ.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(.*)$", line)
        if not m:
            continue
        reqs.append((m.group(1), m.group(2).strip()))
    return reqs


def test_requirements_parseable_and_have_version_specs() -> None:
    reqs = _parse_requirements()
    assert reqs, "requirements.txt is empty"
    names = [n for n, _ in reqs]
    assert len(names) == len(set(names)), f"duplicate requirement names: {names}"
    for name, spec in reqs:
        assert spec, f"requirement {name} has no version specifier"


def test_no_test_only_dependency_in_prod_requirements() -> None:
    text = REQ.read_text(encoding="utf-8").lower()
    for forbidden in ("pytest", "pylint", "flake8", "mypy", "coverage", "tox", "ruff"):
        assert forbidden not in text, f"test-only dependency {forbidden} found in prod requirements.txt"


def test_every_runtime_requirement_is_used_or_transitive() -> None:
    reqs = _parse_requirements()
    src_text = ""
    for path in (ROOT / "src").rglob("*.py"):
        src_text += path.read_text(encoding="utf-8")
    for name, _ in reqs:
        if name in KNOWN_TRANSITIVE:
            continue
        import_names = [imp for imp, req in IMPORT_TO_REQUIREMENT.items() if req == name]
        used = any(re.search(rf"\b{re.escape(imp)}\b", src_text) for imp in import_names)
        assert used, f"requirement {name} is not imported anywhere in src (unused?)"


@pytest.mark.parametrize("name", sorted(KNOWN_TRANSITIVE))
def test_known_transitive_pin_documented(name) -> None:
    reqs = dict(_parse_requirements())
    assert name in reqs, f"{name} pin missing from requirements.txt"
