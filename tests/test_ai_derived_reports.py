# -*- coding: utf-8 -*-
"""Regression tests for derived AI report layers (summary_zh/glossary/course)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.application.ai.pipeline import AIUnderstandingPipeline
from src.application.ai.prompts import (
    GLOSSARY_PROMPT_VERSION,
    SUMMARY_ZH_PROMPT_VERSION,
    build_glossary_prompt,
    build_summary_zh_prompt,
)
from src.application.ai.provider import FakeAIProvider
from src.application.ai.schemas import (
    MALFORMED_ERROR,
    MAX_GLOSSARY_TERM_CHARS,
    MAX_GLOSSARY_ZH_CHARS,
    GlossaryEntry,
    parse_glossary_response,
    parse_summary_zh_response,
)
from src.application.ai.validators import ground_glossary
from src.application.errors import NotFoundError
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace
from tests.test_ai_understanding import (
    TEXT_ES,
    _register_text,
    _workspace,
)


def test_derived_prompts_keep_original_terms_and_are_versioned():
    summary = build_summary_zh_prompt(
        "La integración explica el método.",
        ["integración"],
        [],
        "calculo.txt",
    )
    glossary = build_glossary_prompt(
        "CHUNK c1\nLa integración explica el método.",
        ["integración"],
        "calculo.txt",
    )
    assert SUMMARY_ZH_PROMPT_VERSION.endswith("summary-zh-v1")
    assert GLOSSARY_PROMPT_VERSION.endswith("glossary-v1")
    assert "capital（资本）" in summary
    assert "Keep every Spanish/Catalan technical term verbatim" in summary
    assert "EVIDENCE DIGEST" in glossary
    assert "character-for-character" in glossary
    assert "lang=[语言待确认]" in glossary


def test_derived_schema_is_strict_but_tolerates_json_fence():
    result, error = parse_summary_zh_response(
        "```json\n" + json.dumps({"summary_zh": "中文总结", "topics_zh": ["主题"]}) + "\n```"
    )
    assert error is None and result is not None
    assert result.summary_zh == "中文总结"
    for payload in (
        {},
        {"summary_zh": "x"},
        {"summary_zh": "x", "topics_zh": "not-a-list"},
        {"summary_zh": "", "topics_zh": []},
    ):
        parsed, error = parse_summary_zh_response(json.dumps(payload, ensure_ascii=False))
        assert parsed is None and error == MALFORMED_ERROR
    parsed, error = parse_glossary_response(
        json.dumps(
            {
                "glossary": [
                    {
                        "term": "capital",
                        "lang": "es",
                        "zh": "资本",
                        "evidence_refs": ["c1"],
                    }
                ]
            },
            ensure_ascii=False,
        )
    )
    assert error is None and parsed and parsed[0].term == "capital"

    budgeted, error = parse_glossary_response(
        json.dumps(
            {
                "glossary": [
                    {
                        "term": "x" * 200,
                        "lang": "es",
                        "zh": "中" * 40,
                        "evidence_refs": ["c1"],
                    }
                ]
            }
        )
    )
    assert error is None and budgeted
    assert len(budgeted[0].term) == MAX_GLOSSARY_TERM_CHARS
    assert len(budgeted[0].zh) == MAX_GLOSSARY_ZH_CHARS


def test_glossary_grounding_rejects_one_letter_mutation_and_marks_unknown_language():
    entries = [
        GlossaryEntry(term="capital", lang="es", zh="资本", evidence_refs=["c1"]),
        GlossaryEntry(term="capita", lang="es", zh="资本", evidence_refs=["c1"]),
        GlossaryEntry(term="factor", lang="xx", zh="因素", evidence_refs=["c1"]),
    ]
    accepted, rejected = ground_glossary(
        entries,
        chunk_to_evidence={"c1": "ev-1"},
        material_evidence_ids=["ev-1"],
        evidence_texts={"ev-1": "El capital es un factor de producción."},
        course_id="course-1",
    )
    assert [item.term for item in accepted] == ["capital", "factor"]
    assert accepted[1].lang == "[语言待确认]"
    assert any("verbatim" in (item.get("reject_reason") or "") for item in rejected)


class _MalformedDerivedProvider(FakeAIProvider):
    def generate_structured(self, prompt, **kwargs):
        if "SUMMARY INPUT:" in prompt:
            return "not-json"
        return super().generate_structured(prompt, **kwargs)


class _CountingFakeProvider(FakeAIProvider):
    def __init__(self):
        super().__init__()
        self.calls = 0
        self.prompts = []

    def generate_structured(self, prompt, **kwargs):
        self.calls += 1
        self.prompts.append(str(prompt))
        return super().generate_structured(prompt, **kwargs)


class _CourseFailingProvider(_CountingFakeProvider):
    def generate_structured(self, prompt, **kwargs):
        if "COURSE REPORTS DIGEST:" in prompt:
            self.calls += 1
            self.prompts.append(str(prompt))
            raise RuntimeError("course synthesis unavailable")
        return super().generate_structured(prompt, **kwargs)


def test_derived_stage_failure_does_not_block_kp_persistence(tmp_path):
    ws = _workspace(tmp_path)
    course_id = ws.create_course("C", "C1", "es")["course_id"]
    record = _register_text(ws, course_id, TEXT_ES)
    ws.process_material(course_id, record["material_id"])
    ws.configure_ai(enabled=True, provider=_MalformedDerivedProvider())
    report = ws.analyze_material_with_ai(course_id, record["material_id"])
    assert report["status"] == "completed"
    assert report["summary_zh"] == ""
    assert report["summary_zh_status"] == "skipped"
    assert ws.knowledge_points(course_id)


def test_fake_material_analysis_produces_summary_glossary_and_course_cache(tmp_path):
    ws = _workspace(tmp_path)
    course_id = ws.create_course("Cálculo", "CAL", "es")["course_id"]
    record = _register_text(ws, course_id, TEXT_ES, filename="calc.txt")
    missing = _register_text(
        ws,
        course_id,
        TEXT_ES + " Un segundo material sin analisis.",
        filename="missing.txt",
    )
    ws.process_material(course_id, record["material_id"])
    ws.configure_ai(enabled=True, provider=FakeAIProvider())
    report = ws.analyze_material_with_ai(course_id, record["material_id"])

    assert report["summary_zh"]
    assert report["topics_zh"]
    assert report["summary_zh_grounded"] is True
    assert report["summary_zh_evidence_ids"]
    assert report["glossary"]
    assert report["glossary_status"] == "completed"
    for item in report["glossary"]:
        assert item["evidence_ids"]
        assert item["glossary_id"].startswith("gls-")

    overview = ws.course_ai_overview(course_id)
    assert overview["course_id"] == course_id
    assert overview["status"] in {"completed", "partial"}
    assert overview["coverage"]["materials_with_reports"] == 1
    assert overview["coverage"]["materials_total"] == 2
    assert overview["coverage"]["gaps"] == [missing["material_id"]]
    assert missing["material_id"] not in json.dumps(overview["materials"])

    # The normal read is bounded; the UI's explicit show-all read returns the
    # complete persisted list without changing the route or invoking a model.
    entries = [
        {
            "term": "term-%d" % index,
            "lang": "es",
            "zh": "术语",
            "evidence_ids": ["ev-1"],
        }
        for index in range(25)
    ]
    ws._ai_reports[(course_id, record["material_id"])] = dict(
        report, glossary=entries, glossary_total=25
    )
    bounded = ws.ai_summary(course_id, record["material_id"])
    expanded = ws.ai_summary(course_id, record["material_id"], glossary_limit=25)
    assert len(bounded["glossary"]) == 20
    assert bounded["glossary_total"] == 25
    assert bounded["glossary_complete"] is False
    assert len(expanded["glossary"]) == 25
    assert expanded["glossary_complete"] is True


def test_course_overview_404_before_report_and_read_does_not_call_provider(tmp_path):
    ws = _workspace(tmp_path)
    course_id = ws.create_course("C", "C1", "es")["course_id"]
    record = _register_text(ws, course_id, TEXT_ES)
    with pytest.raises(NotFoundError):
        ws.course_ai_overview(course_id)

    ws.process_material(course_id, record["material_id"])
    provider = _CountingFakeProvider()
    ws.configure_ai(enabled=True, provider=provider)
    ws.analyze_material_with_ai(course_id, record["material_id"])
    before = provider.calls
    assert ws.course_ai_overview(course_id)["source_report_count"] == 1
    assert ws.course_ai_overview(course_id)["source_report_count"] == 1
    assert provider.calls == before


def test_course_aggregation_filters_other_courses_and_falls_back_without_llm():
    reports = [
        {
            "course_id": "course-a",
            "material_id": "mat-a",
            "summary": "Resumen A",
            "topics": ["Tema A"],
        },
        {
            "course_id": "course-b",
            "material_id": "mat-b",
            "summary": "Resumen B",
            "topics": ["Tema B"],
        },
    ]
    provider = _CourseFailingProvider()
    pipeline = AIUnderstandingPipeline(provider)
    result = pipeline.synthesize_course_overview(
        json.dumps([{"material_id": "mat-a", "summary": "Resumen A", "topics": ["Tema A"]}]),
        course_id="course-a",
        reports=reports,
        missing_material_ids=["mat-gap"],
        provider=provider,
    )
    assert result["status"] == "partial"
    assert [row["material_id"] for row in result["materials"]] == ["mat-a"]
    assert result["gaps"] == ["mat-gap"]
    assert "mat-b" not in json.dumps(result)
    assert "Resumen B" not in provider.prompts[-1]


def test_v1_report_projection_defaults_derived_fields(tmp_path):
    data_dir = tmp_path / "persistent"
    ws = Workspace(
        str(data_dir),
        clock=fixed_clock("2026-09-25T00:00:00+00:00"),
        asr_mode="mock",
        ocr_mode="mock",
    )
    course_id = ws.create_course("C", "C1", "es")["course_id"]
    record = _register_text(ws, course_id, TEXT_ES)
    target = ws._ai_report_path(course_id, record["material_id"])
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(
            {
                "material_id": record["material_id"],
                "course_id": course_id,
                "status": "completed",
                "summary": "legacy",
            }
        ),
        encoding="utf-8",
    )
    summary = ws.ai_summary(course_id, record["material_id"])
    assert summary["report_version"] == "ai-report-v1"
    assert summary["summary_zh"] == ""
    assert summary["glossary"] == []
    ws.close()
