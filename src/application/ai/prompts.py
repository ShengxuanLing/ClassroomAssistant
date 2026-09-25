# -*- coding: utf-8 -*-
"""AI 提示词集中管理 (TASK-76 §26)。

Prompt 不散落在业务代码里: 本模块是唯一允许组装发往 provider 的
提示词文本的地方。每个 builder 明确要求模型:

1. 只能根据给定的 Evidence 文本作答;
2. 不知道就明确说不知道 (低 confidence), 不编造;
3. 不允许创造引用 / Evidence ID / 页码 / 时间戳;
4. 不允许修改事实 (原文术语逐字保留);
5. 输出严格 JSON schema (见 ``schemas.py``);
6. 保留原始专业术语 (西语/加泰语术语不翻译, 只追加解释);
7. 每个知识点返回 confidence (0..1) 与 evidence_refs (chunk id 引用);
8. 返回内容语言由调用方 ``content_language`` 指定, Evidence 原文不翻译。
"""

from __future__ import annotations

import json
from typing import Any, Optional, Sequence

from src.application.ai import PROMPT_VERSION

__all__ = [
    "CHUNK_EXTRACTION_PROMPT_VERSION",
    "MERGE_PROMPT_VERSION",
    "SUMMARY_PROMPT_VERSION",
    "SUMMARY_ZH_PROMPT_VERSION",
    "GLOSSARY_PROMPT_VERSION",
    "COURSE_OVERVIEW_PROMPT_VERSION",
    "IMAGE_PROMPT_VERSION",
    "AUDIO_PROMPT_VERSION",
    "ALLOWED_KNOWLEDGE_TYPES",
    "build_chunk_extraction_prompt",
    "build_merge_prompt",
    "build_summary_prompt",
    "build_summary_zh_prompt",
    "build_glossary_prompt",
    "build_course_overview_prompt",
    "build_image_analysis_prompt",
    "build_audio_analysis_prompt",
]

#: 各提示词版本 (进入 processing identity; 改任一 prompt 文本必须 bump 对应版本)。
#: v3 (2026-09-24): 应用层会拒绝与 Evidence 原文相同/异常高相似的候选；
#: 提示词明确 summary、knowledge content 与 source evidence 是三个独立角色。
#: v2 只要求"抽取/浓缩"，模型仍可能把证据段落当 description/summary。
CHUNK_EXTRACTION_PROMPT_VERSION = PROMPT_VERSION + ":extract-v3"
MERGE_PROMPT_VERSION = PROMPT_VERSION + ":merge-v3"
SUMMARY_PROMPT_VERSION = PROMPT_VERSION + ":summary-v3"
#: Derived report stages are versioned independently.  They never change the
#: Evidence/KP extraction contract, but a changed translation/glossary prompt
#: must still be visible in the report processing identity.
SUMMARY_ZH_PROMPT_VERSION = PROMPT_VERSION + ":summary-zh-v1"
GLOSSARY_PROMPT_VERSION = PROMPT_VERSION + ":glossary-v1"
COURSE_OVERVIEW_PROMPT_VERSION = PROMPT_VERSION + ":course-overview-v1"
IMAGE_PROMPT_VERSION = PROMPT_VERSION + ":image-v3"
AUDIO_PROMPT_VERSION = PROMPT_VERSION + ":audio-v3"

#: 允许的知识点类型 (超集; 落库时映射到现有 domain 模型, 见 validators.py)。
ALLOWED_KNOWLEDGE_TYPES = (
    "concept",
    "definition",
    "formula",
    "procedure",
    "example",
    "relationship",
    "fact",
    "principle",
)


_GROUNDING_RULES = """\
GROUNDING RULES (must follow exactly):
1. Use ONLY the EVIDENCE text given below. Do not use outside knowledge.
2. If the evidence does not support a point, say so with low confidence (<0.5)
   instead of inventing content.
3. NEVER invent evidence references, page numbers, timestamps, or evidence IDs.
   "evidence_refs" may only contain chunk IDs listed under AVAILABLE CHUNKS.
4. NEVER modify facts, numbers, formulas, or terminology from the evidence.
   Keep original Spanish/Catalan technical terms verbatim.
5. Output STRICT JSON matching the requested schema. No markdown, no prose
   outside the JSON object.
6. Every knowledge point must carry "confidence" (0.0-1.0) and "evidence_refs".
7. Keep "original_terms" as the verbatim terms from the evidence.
8. "summary"/"topics" are report-layer abstractions; never paste a source
   passage there. Keep the verbatim source only in the evidence store.
"""


#: 浓缩改写规则 (v2): KP 是"考前能背的一句话", 不是原文复读机。
#: 没有这组规则时模型直接复制证据句子, 导致 KP 与证据一字不差。
_DISTILL_RULES = """\
DISTILLATION RULES (exam-review oriented, must follow exactly):
1. "title" is the concept name only (2-8 words), never a full sentence.
2. "description" must be a CONDENSED paraphrase in 1-3 sentences: what the
   concept IS / states / requires, in words a student can memorize before
   an exam. NEVER copy evidence sentences verbatim; short quotes (<=15
   words) are allowed only inside "examples".
3. If the evidence contains a flattened table / comparison (e.g. "A...B..."
   column pairs like year-vs-year or before-vs-after), INTERPRET it into
   explicit facts ("X rose from A to B"), never paste the flattened cells.
4. Administrative noise (exam dates, minimum grades, room numbers) is at
   most "importance": "low" and type "fact" — never "high".
5. One concept per knowledge point. A definition, its formula, and its
   example are three separate points, not one pasted paragraph.
6. Verbatim source wording belongs ONLY in "original_terms" and short
   clearly-labelled "examples". It must not be used as "title" or as the main
   "description" copied from a source passage.
7. Treat "summary" as a report-layer synthesis, never as a replacement for or
   duplicate of EVIDENCE TEXT. Knowledge content and source evidence are separate
   roles even when they discuss the same concept.
"""


def build_chunk_extraction_prompt(
    *,
    chunk_id: str,
    chunk_text: str,
    material_label: str = "",
    content_language: Optional[str] = None,
) -> str:
    """单 chunk 抽取提示词 (Level 1: local concepts / facts / definitions)。"""
    lang_line = (
        f'Write "title"/"description" in language "{content_language}", but keep '
        '"original_terms" verbatim in the evidence language.\n'
        if content_language
        else "Write \"title\"/\"description\" in the evidence language.\n"
    )
    return (
        "You are a classroom-assistant knowledge extractor for university courses "
        "(languages: Spanish/Catalan input; explanations may be Chinese).\n"
        "Your output is a CONDENSED exam-review sheet, not a copy of the input.\n"
        + _GROUNDING_RULES
        + _DISTILL_RULES
        + lang_line
        + "Allowed knowledge types: %s.\n" % (", ".join(ALLOWED_KNOWLEDGE_TYPES),)
        + "MATERIAL: %s\nAVAILABLE CHUNKS: [%s]\n\nEVIDENCE TEXT:\n%s\n" % (
            material_label or "course material",
            chunk_id,
            chunk_text,
        )
        + '\nReturn JSON: {"summary": str, "topics": [str], '
        + '"knowledge_points": [{"title": str, "description": str, '
        + '"type": str, "importance": "high"|"medium"|"low", '
        + '"confidence": number, "evidence_refs": [chunk ids], '
        + '"relations": [str], "examples": [str], "original_terms": [str]}]}'
    )


def build_merge_prompt(
    *,
    chunk_summaries: str,
    material_label: str = "",
    content_language: Optional[str] = None,
) -> str:
    """多 chunk 合并提示词 (Level 2: merge / dedup / global concepts)。"""
    lang_line = (
        f'Write the merged output in language "{content_language}".\n'
        if content_language
        else ""
    )
    return (
        "You are a classroom-assistant knowledge merger. Merge per-chunk extraction "
        "results into material-level concepts.\n"
        "Keep every description CONDENSED (1-3 sentences, exam-memorable); "
        "merging must never glue copied source paragraphs back together.\n"
        + _GROUNDING_RULES
        + _DISTILL_RULES
        + lang_line
        + "Rules: merge duplicates referring to the same concept; keep distinct "
        + "concepts separate; never drop evidence_refs; take the minimum confidence "
        + "when merging.\nMATERIAL: %s\n\nCHUNK RESULTS:\n%s\n" % (
            material_label or "course material",
            chunk_summaries,
        )
        + '\nReturn JSON: {"summary": str, "topics": [str], '
        + '"knowledge_points": [{"title": str, "description": str, '
        + '"type": str, "importance": str, "confidence": number, '
        + '"evidence_refs": [chunk ids], "relations": [str], '
        + '"examples": [str], "original_terms": [str]}]}'
    )


def build_summary_prompt(
    *,
    material_text: str,
    material_label: str = "",
    content_language: Optional[str] = None,
) -> str:
    """材料级总结提示词 (summary / topics / definitions / formulas / ...)。"""
    lang_line = (
        f'Write the summary in language "{content_language}".\n'
        if content_language
        else ""
    )
    return (
        "You are a classroom-assistant summarizer. Write a CONDENSED exam-review "
        "summary: the fewest sentences that still cover every key topic.\n"
        + _GROUNDING_RULES
        + lang_line
        + "MATERIAL: %s\n\nEVIDENCE TEXT:\n%s\n" % (
            material_label or "course material",
            material_text,
        )
        + '\nReturn JSON: {"summary": str, "topics": [str], '
        + '"definitions": [str], "formulas": [str], "examples": [str], '
        + '"prerequisites": [str], "difficulties": [str]}'
    )


def build_summary_zh_prompt(
    summary: str = "",
    topics: Optional[Sequence[Any]] = None,
    definitions: Optional[Sequence[Any]] = None,
    material_label: str = "",
    *,
    content_language: Optional[str] = None,
) -> str:
    """Build the grounded Chinese-learning summary stage.

    This stage receives the already merged report digest, never the raw
    Evidence.  The original term stays visible in the Chinese explanation;
    only the explanatory language changes.  Keeping this input small is both
    a cost guard and a useful boundary against accidental full-text
    translation.
    """
    digest = json.dumps(
        {
            "summary": str(summary or ""),
            "topics": list(topics or []),
            "definitions": list(definitions or []),
        },
        ensure_ascii=False,
    )
    language_note = (
        "The source report language is %s; the requested explanation language is zh."
        % content_language
        if content_language
        else "The requested explanation language is zh (Chinese)."
    )
    return (
        "You are a classroom-assistant translator. Produce a concise Chinese "
        "learning summary from the merged report digest below.\n"
        "Use ONLY facts present in SUMMARY INPUT. Do not add outside knowledge, "
        "examples, numbers, or conclusions.\n"
        "Keep every Spanish/Catalan technical term verbatim and add a short "
        "Chinese explanation in parentheses, for example capital（资本）. "
        "Do not translate person names or course names. Mark a culture-specific "
        "concept with [文化概念] when needed.\n"
        "The output is an explanation layer, not a verbatim translation of a "
        "source passage. Keep it exam-oriented and do not claim evidence that "
        "is absent from the digest.\n"
        + language_note
        + "\nMATERIAL: %s\n\nSUMMARY INPUT:\n%s\n" % (material_label or "course material", digest)
        + '\nReturn strict JSON: {"summary_zh": str, "topics_zh": [str]}. '
        'Do not wrap it in markdown or prose outside the JSON object.'
    )


def build_glossary_prompt(
    evidence_digest: str = "",
    kp_titles: Optional[Sequence[Any]] = None,
    material_label: str = "",
) -> str:
    """Build the evidence-grounded bilingual glossary stage prompt."""
    titles = json.dumps(list(kp_titles or []), ensure_ascii=False)
    return (
        "You are a classroom-assistant terminology extractor. Build a compact "
        "Spanish/Catalan -> Chinese glossary from EVIDENCE DIGEST only.\n"
        "GROUNDING RULES:\n"
        "1. Every term MUST be copied character-for-character from the evidence "
        "digest (validator performs a case/accent-insensitive substring check).\n"
        "2. Use one term per card; do not translate a whole sentence. Keep the "
        "term's original spelling and accents.\n"
        "3. lang must be exactly es or ca. If the language is uncertain, return "
        "lang=[语言待确认].\n"
        "4. zh is a concise Chinese explanation (at most 20 Chinese characters), "
        "not a sentence and not a claim beyond the evidence.\n"
        "5. evidence_refs may contain only chunk IDs listed in the digest. Never "
        "invent an evidence ID, page, or timestamp.\n"
        "6. Do not include personal names, course names, or administrative noise.\n"
        "Return strict JSON: {\"glossary\": [{\"term\": str, \"lang\": str, "
        "\"zh\": str, \"evidence_refs\": [str]}]}. No markdown or prose outside "
        "the JSON object.\nMATERIAL: %s\nAVAILABLE KP TITLES: %s\n\n"
        "EVIDENCE DIGEST:\n%s\n"
        % (
            material_label or "course material",
            titles,
            str(evidence_digest or "")[:12000],
        )
    )


def build_course_overview_prompt(
    course_reports_digest: str = "",
    material_label: str = "course",
) -> str:
    """Build the one-shot, read-only course synthesis prompt."""
    return (
        "You are a classroom-assistant course synthesizer. Synthesize only the "
        "material reports in COURSE REPORTS DIGEST; do not use outside knowledge "
        "and never include another course.\n"
        "Keep source claims attributable to the supplied material reports. "
        "Return a concise course overview, a topic-to-material map, and explicit "
        "coverage gaps. Do not invent a gap that is not represented by missing "
        "reports.\n"
        "Return strict JSON: {\"overview\": str, \"topic_map\": [{\"topic\": str, "
        "\"material_ids\": [str], \"summary\": str}], \"gaps\": [str]}. "
        "No markdown or prose outside the JSON object.\n"
        "COURSE: %s\n\nCOURSE REPORTS DIGEST:\n%s\n"
        % (material_label or "course", str(course_reports_digest or "")[:16000])
    )


def build_image_analysis_prompt(
    *,
    ocr_text: str,
    chunk_id: str = "",
    material_label: str = "",
    content_language: Optional[str] = None,
) -> str:
    """图片/Vision 分析提示词 (OCR 文字 + 视觉结构理解, 见 pipeline 组合 Evidence)。"""
    lang_line = (
        f'Write "title"/"description" in language "{content_language}".\n'
        if content_language
        else ""
    )
    chunks_line = "AVAILABLE CHUNKS: [%s]\n" % (chunk_id or "chunk") if chunk_id else ""
    return (
        "You are a classroom-assistant vision analyst. The input is OCR text from "
        "a classroom image (slide / blackboard / diagram / formula / handwritten "
        "note / table). Reconstruct the page meaning: text, formulas, structure.\n"
        "When image pixels are also provided, read layout directly (table columns, "
        "diagram arrows, formula shapes) instead of guessing from flattened OCR.\n"
        "Output is a CONDENSED exam-review sheet, not a copy of the OCR dump.\n"
        + _GROUNDING_RULES
        + _DISTILL_RULES
        + lang_line
        + "MATERIAL: %s\n%s\nOCR TEXT:\n%s\n" % (
            material_label or "classroom image",
            chunks_line,
            ocr_text,
        )
        + '\nReturn JSON: {"summary": str, "topics": [str], '
        + '"knowledge_points": [{"title": str, "description": str, '
        + '"type": str, "importance": str, "confidence": number, '
        + '"evidence_refs": [chunk ids], "relations": [str], '
        + '"examples": [str], "original_terms": [str]}]}'
    )


def build_audio_analysis_prompt(
    *,
    transcript_text: str,
    chunk_id: str = "",
    material_label: str = "",
    content_language: Optional[str] = None,
) -> str:
    """音频/课堂转写分析提示词 (识别讲解重点 / 定义 / 例子 / 强调内容)。"""
    lang_line = (
        f'Write "title"/"description" in language "{content_language}".\n'
        if content_language
        else ""
    )
    chunks_line = "AVAILABLE CHUNKS: [%s]\n" % (chunk_id or "chunk") if chunk_id else ""
    return (
        "You are a classroom-assistant lecture analyst. The input is a classroom "
        "transcript with timestamps. Identify key teaching points, definitions, "
        "concepts, examples, and emphasized content.\n"
        "Output is a CONDENSED exam-review sheet: filler and repetition removed, "
        "each point memorizable in one breath.\n"
        + _GROUNDING_RULES
        + _DISTILL_RULES
        + lang_line
        + "MATERIAL: %s\n%s\nTRANSCRIPT:\n%s\n" % (
            material_label or "classroom recording",
            chunks_line,
            transcript_text,
        )
        + '\nReturn JSON: {"summary": str, "topics": [str], '
        + '"knowledge_points": [{"title": str, "description": str, '
        + '"type": str, "importance": str, "confidence": number, '
        + '"evidence_refs": [chunk ids], "relations": [str], '
        + '"examples": [str], "original_terms": [str]}]}'
    )
