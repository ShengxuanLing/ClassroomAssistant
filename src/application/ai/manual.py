# -*- coding: utf-8 -*-
"""手动 AI 协议: 导出任务包 → 网页模型 → 导入响应 (阶段 2/3)。

为什么需要
----------

``OpenAICompatibleAIProvider`` 需要 API Key 与出站网络。用户手上可能只有网页版
ChatGPT / Claude, 也可能只是**不想**把课堂材料发给任何服务商。手动模式把同一件
事拆成两段:

    导出 (任务包)  →  用户自己把材料与任务交给外部模型  →  导入 (响应包)

**同一条管线, 只换一个 provider。** 导入走的是 ``AIAnalysisService.analyze`` +
``ReplayAIProvider``: 校验、grounding、去重、冲突检测、幂等入库、报告落盘全部
复用既有实现 —— 不新增任何"直接写库"的捷径。本模块只负责协议本身 (打包、
校验、把响应按 ``request_key`` 交给管线)。

协议 (v1)
---------

导出包 (JSON, 机器可读)::

    {
      "protocol_version": "manual-ai/v1",
      "bundle_id": "mab-<sha16>",
      "prompt_version": "...:extract-v3",
      "course_id": "...",
      "material": {material_id, filename, content_hash, material_hash, kind, language},
      "requests": [
        {"request_id": "req-<sha16>", "stage": "chunk_extraction",
         "chunk_id": "chunk-<sha16>", "chunk_index": 0, "page": 3,
         "section": "...", "char_start": 0, "char_end": 2000,
         "prompt": "<与真实调用逐字相同的提示词>",
         "response_schema": {"format": "json_object", "fields": {...}},
         "evidence_scope": {"material_id": "...", "chunk_id": "..."}}
      ],
      "limits": {"max_response_chars": 20000, "max_requests": 100}
    }

响应包 (用户粘回来的)::

    {
      "protocol_version": "manual-ai/v1",
      "bundle_id": "mab-<sha16>",
      "responses": [{"request_id": "req-<sha16>", "content": "<模型返回的 JSON 文本>"}],
      "model_name": "可选 (用户自报, **不计为已验证身份**)"
    }

不可信的输入
------------

响应包是**用户粘贴的外部文本**, 与网络响应同一信任级别, 因此:

1. ``bundle_id`` 在导入时**重新计算** (材料 id + 材料哈希 + 全部 chunk id +
   两个协议版本号), 与包里的值逐字比对 —— 包里的 id 不是判据, 重算才是。
   材料被重新处理 / 换了一份材料 / 协议或提示词版本变了, 都会重算不出同一个 id。
2. 每个 ``request_id`` 也重新计算, 模型或用户自报的引用一律不作数。
3. ``content`` 先过长度上限, 再过 ``parse_structured_response`` 的**同一套**
   schema 校验; 模型自报的 ``evidence_refs`` 照旧被重写为本 chunk
   (见 ``pipeline.analyze_chunk``), 不可能指到别的材料上。
4. 身份不含任何时间戳: 同一材料 + 同一配置 -> 同一 ``bundle_id``, 所以重复提交
   天然幂等 (KP ID 由标题确定性派生)。

确定性
------

本模块无 ``random`` / ``uuid4`` / ``time`` / ``hash()``: 全部标识都是内容寻址的
``sha256`` 前缀。同一输入永远得到同一个包。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from src.application.ai.chunking import chunks_for_evidence
from src.application.ai.prompts import (
    ALLOWED_KNOWLEDGE_TYPES,
    CHUNK_EXTRACTION_PROMPT_VERSION,
    build_chunk_prompt,
)
from src.application.ai.provider import AIProvider, AIRequestError, ProviderCapabilities
from src.application.ai.schemas import parse_structured_response
from src.application.errors import InvalidInputError

__all__ = [
    "MANUAL_PROTOCOL_VERSION",
    "MANUAL_PROTOCOL_VERSION_ALIASES",
    "MANUAL_STAGE_CHUNK_EXTRACTION",
    "MANUAL_MODE",
    "USER_PASTED_PROVIDER_NAME",
    "MAX_RESPONSE_CHARS",
    "MAX_REQUESTS",
    "MAX_BUNDLE_BYTES",
    "ManualProtocolError",
    "ManualRequest",
    "ManualBundle",
    "ParsedResponses",
    "ReplayAIProvider",
    "manual_bundle_id",
    "manual_request_id",
    "build_manual_bundle",
    "render_instructions_markdown",
    "parse_response_payload",
    "responses_by_chunk",
    "build_request_report",
]

#: 当前协议版本。不支持的版本必须明确报错, 不得"尽力而为"地解析。
MANUAL_PROTOCOL_VERSION = "manual-ai/v1"

#: 历史上同一协议曾用过的自报名 (导入时接受, 只用于兼容自己发行的包)。
MANUAL_PROTOCOL_VERSION_ALIASES = frozenset({MANUAL_PROTOCOL_VERSION, "v1"})

MANUAL_STAGE_CHUNK_EXTRACTION = "chunk_extraction"
MANUAL_MODE = "manual"

#: 导入报告里的 provider 标识 —— 明确区分"用户粘贴"与"真实 API 调用"。
USER_PASTED_PROVIDER_NAME = "user-pasted"

#: 单条响应字符上限 (超过即判超长, 不截断后继续 —— 半截 JSON 不是结果)。
MAX_RESPONSE_CHARS = 20000

#: 一次导入最多处理的请求数 (与分块上限同量级; 超出明确报错而非静默丢弃)。
MAX_REQUESTS = 200

#: 整个响应包字节上限 (防超大粘贴把服务打挂)。
MAX_BUNDLE_BYTES = 8 * 1024 * 1024

#: 单请求状态 (逐请求反馈的口径, 前端按它分类展示)。
STATUS_APPLIED = "applied"
STATUS_EMPTY = "empty"
STATUS_GROUNDING_FAILED = "grounding_failed"
STATUS_MISSING = "missing"
STATUS_UNKNOWN_REQUEST = "unknown_request"
STATUS_DUPLICATE_REQUEST = "duplicate_request"
STATUS_MALFORMED_JSON = "malformed_json"
STATUS_SCHEMA_ERROR = "schema_error"
STATUS_TOO_LARGE = "too_large"
STATUS_FAILED = "failed"

#: 状态 -> 是否算"这条请求已经成功处理" (可重试集合 = 其余)。
SUCCESS_STATUSES = frozenset({STATUS_APPLIED, STATUS_EMPTY})

#: 导入包覆盖的管线阶段; 其余阶段在手动路径上按既有回落处理 (见 build_request_report)。
LLM_STAGES_IN_SCOPE = (MANUAL_STAGE_CHUNK_EXTRACTION,)
LLM_STAGES_OUT_OF_SCOPE = ("merge_llm", "summary_zh", "glossary")


class ManualProtocolError(InvalidInputError):
    """协议级错误 (不可重试的结构问题: 版本、bundle、响应形状) → HTTP 400。

    继承 ``InvalidInputError`` 而不是裸 ``ValueError``: 这是用户输入问题 (贴错
    包、贴了别的材料的包), 该有稳定的 400 错误码, 不能掉进 500。具体原因放
    ``protocol_error``, 也进 ``detail``, 前端可以据此说人话。
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        detail: Optional[Mapping[str, Any]] = None,
    ) -> None:
        merged = {"protocol_error": str(code)}
        if detail:
            merged.update(dict(detail))
        super().__init__(message, detail=merged)
        self.protocol_error = str(code)


def _sha16(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def manual_bundle_id(
    *,
    material_id: str,
    material_hash: str,
    chunk_ids: Sequence[str],
    prompt_version: str = CHUNK_EXTRACTION_PROMPT_VERSION,
    protocol_version: str = MANUAL_PROTOCOL_VERSION,
) -> str:
    """内容寻址的任务包 ID (无时间、无随机)。

    材料身份 + 材料哈希 + **全部 chunk id** + 协议/提示词版本。任何一项变了
    (重新处理材料、改了分块预算、bump 提示词) 都会得到不同的 ID —— 这正是
    导入时能拒绝"旧包用到新材料上"的依据。
    """
    raw = "|".join(
        [
            str(protocol_version),
            str(prompt_version),
            str(material_id),
            str(material_hash),
            ",".join(str(c) for c in chunk_ids),
        ]
    )
    return "mab-" + _sha16(raw)


def manual_request_id(
    *, bundle_id: str, stage: str, chunk_id: str, prompt_version: str = CHUNK_EXTRACTION_PROMPT_VERSION
) -> str:
    """请求 ID = ``req-<sha16(bundle|stage|prompt_version|chunk)>``。

    与 ``bundle_id`` 一样可重算: 导入时不信任包里的 ID, 而是按本函数重算,
    于是"把 A 请求的响应贴到 B 请求上"最多只能命中 A, 不可能凭空命中一个
    不相干的请求。
    """
    raw = "|".join([str(bundle_id), str(stage), str(prompt_version), str(chunk_id)])
    return "req-" + _sha16(raw)


#: 导出包要求模型返回的字段与类型 (与 prompt 末尾的 "Return JSON" 逐项一致)。
RESPONSE_FIELDS: Mapping[str, Any] = {
    "summary": "string",
    "topics": ["string"],
    "knowledge_points": [
        {
            "title": "string (concept name, 2-8 words)",
            "description": "string (1-3 condensed sentences)",
            "type": "one of: " + ", ".join(ALLOWED_KNOWLEDGE_TYPES),
            "importance": "high | medium | low",
            "confidence": "number 0.0-1.0",
            "evidence_refs": ["chunk id listed in the request"],
            "relations": ["string"],
            "examples": ["string"],
            "original_terms": ["string (verbatim from the evidence)"],
        }
    ],
}


@dataclass(frozen=True)
class ManualRequest:
    """一个待外部模型回答的请求。"""

    request_id: str
    stage: str
    chunk_id: str
    chunk_index: int
    page: Optional[int]
    section: str
    char_start: int
    char_end: int
    prompt: str
    evidence_scope: Mapping[str, str]
    prompt_version: str = CHUNK_EXTRACTION_PROMPT_VERSION
    response_format: str = "json_object"

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "stage": self.stage,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "page": self.page,
            "section": self.section,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "prompt": self.prompt,
            "response_format": self.response_format,
            "response_schema": {
                "format": self.response_format,
                "fields": RESPONSE_FIELDS,
            },
            "evidence_scope": dict(self.evidence_scope),
            "prompt_version": self.prompt_version,
        }


@dataclass(frozen=True)
class ManualBundle:
    """导出包 (机器可读部分)。人类可读说明见 ``render_instructions_markdown``。"""

    bundle_id: str
    course_id: str
    material: Mapping[str, Any]
    requests: Sequence[ManualRequest]
    protocol_version: str = MANUAL_PROTOCOL_VERSION
    prompt_version: str = CHUNK_EXTRACTION_PROMPT_VERSION
    chunking: Mapping[str, Any] = field(default_factory=dict)
    limits: Mapping[str, Any] = field(default_factory=dict)
    notes: Sequence[str] = ()

    @property
    def request_ids(self) -> list[str]:
        return [request.request_id for request in self.requests]

    def request_by_id(self) -> dict[str, ManualRequest]:
        return {request.request_id: request for request in self.requests}

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "bundle_id": self.bundle_id,
            "prompt_version": self.prompt_version,
            "course_id": self.course_id,
            "material": dict(self.material),
            "chunking": dict(self.chunking),
            "requests": [request.to_dict() for request in self.requests],
            "request_ids": self.request_ids,
            "limits": dict(self.limits),
            "notes": list(self.notes),
        }


def build_manual_bundle(
    *,
    course_id: str,
    material: Mapping[str, Any],
    evidences: Sequence[Any],
    material_hash: str = "",
    kind: str = "text",
    content_language: Optional[str] = None,
    max_chunks: Optional[int] = None,
) -> ManualBundle:
    """按**与真实调用完全相同**的分块与提示词生成任务包 (纯函数, 零出站)。

    ``max_chunks`` 只用于把包控制在一次粘贴得完的规模; 截断会明确记进
    ``chunking.truncated`` 与 notes, 绝不静默少给几个请求。
    """
    material_id = str(material.get("material_id") or "")
    filename = str(material.get("filename") or material_id)
    chunks, _chunk_to_evidence, truncated = chunks_for_evidence(
        evidences, material_id=material_id
    )
    limit = MAX_REQUESTS if max_chunks is None else max(1, int(max_chunks))
    limited = list(chunks[:limit])
    limit_hit = truncated or len(chunks) > len(limited)
    if limit_hit:
        # 参数的含义是"最多处理多少个 chunk"; 这里显式取前缀而不是随机取样,
        # 并且如实标注 —— 用户必须知道包是不完整的。
        truncated = True

    bundle_id = manual_bundle_id(
        material_id=material_id,
        material_hash=material_hash,
        chunk_ids=[chunk.chunk_id for chunk in limited],
    )
    requests = tuple(
        ManualRequest(
            request_id=manual_request_id(
                bundle_id=bundle_id,
                stage=MANUAL_STAGE_CHUNK_EXTRACTION,
                chunk_id=chunk.chunk_id,
            ),
            stage=MANUAL_STAGE_CHUNK_EXTRACTION,
            chunk_id=chunk.chunk_id,
            chunk_index=int(chunk.chunk_index),
            page=chunk.page,
            section=str(chunk.section or ""),
            char_start=int(chunk.char_start),
            char_end=int(chunk.char_end),
            prompt=build_chunk_prompt(
                kind=kind,
                chunk_id=chunk.chunk_id,
                chunk_text=chunk.text,
                material_label=filename,
                content_language=content_language,
            ),
            evidence_scope={"material_id": material_id, "chunk_id": chunk.chunk_id},
        )
        for chunk in limited
    )
    notes: list[str] = [
        "导出不会调用任何 AI 服务, 也不会写入知识点或证据。",
        "导入时 bundle_id 会按材料当前内容重新计算: 材料重新处理过后, 旧包会被拒绝。",
        "模型自报的 evidence_refs 会被重写为本请求的 chunk; 不能引用其它材料。",
    ]
    if limit_hit:
        notes.append(
            "本包被 %d 个请求的上限截断 (材料共 %d 个 chunk): 只有前 %d 个包含在内。"
            % (limit, len(chunks), len(limited))
        )
    if not requests:
        notes.append("这份材料当前没有可分析的文本 (没有证据或证据正文为空)。")
    return ManualBundle(
        bundle_id=bundle_id,
        course_id=str(course_id),
        material={
            "material_id": material_id,
            "filename": filename,
            "content_hash": str(material.get("content_hash") or ""),
            "material_hash": str(material_hash or ""),
            "kind": str(kind or ""),
            "language": str(material.get("language") or ""),
        },
        requests=requests,
        chunking={
            "chunk_count": len(requests),
            "material_chunk_count": len(chunks),
            "truncated": bool(truncated),
            "request_limit": limit,
        },
        limits={"max_response_chars": MAX_RESPONSE_CHARS, "max_requests": MAX_REQUESTS},
        notes=tuple(notes),
    )


def render_instructions_markdown(bundle: ManualBundle, *, response_wrapper: bool = True) -> str:
    """人类可读的操作说明 (第二部分)。只讲流程与协议, 不夹带内部路径/密钥。"""
    lines: list[str] = []
    lines.append("# 本地 AI 分析任务包")
    lines.append("")
    lines.append("- 任务包 ID: `%s`" % bundle.bundle_id)
    lines.append("- 协议版本: `%s`" % bundle.protocol_version)
    lines.append("- 提示词版本: `%s`" % bundle.prompt_version)
    lines.append("- 材料: `%s`" % (bundle.material.get("filename") or ""))
    lines.append("- 请求数: **%d**" % len(bundle.requests))
    lines.append("- 单条响应上限: %d 字符" % int(bundle.limits.get("max_response_chars") or MAX_RESPONSE_CHARS))
    lines.append("")
    lines.append("## 怎么用")
    lines.append("")
    lines.append("1. 把随包导出的原始材料**和** `requests[].prompt` 里的提示词一起交给本地模型")
    lines.append("   （任何能按 `/chat/completions` 契约发请求、且你能在本机运行的模型均可）。")
    lines.append("2. 一次把所有请求发完（不要一条一条发）。若模型上下文放不下，先换更大的窗口，")
    lines.append("   或者只发一部分请求——但**回复里的 request_id 必须与请求一一对应**。")
    lines.append("3. **让模型把结果写成一个 JSON 文件保存下来**（不要只把 JSON 贴在对话里）。")
    lines.append("   文件名建议 `manual-ai-<bundle_id>.json`，编码用 UTF-8。")
    lines.append("   直接输出 JSON 本体即可；如果模型习惯用「三反引号 + json 标注」包起来也能用，")
    lines.append("   但不要依赖它。")
    lines.append("4. 把模型保存的 JSON 文件上传回本面板（见下方「上传模型返回的 JSON 文件」）。")
    lines.append("")
    lines.append("## 回复格式")
    lines.append("")
    lines.append("每个请求一条 JSON 对象（字段与提示词末尾要求的完全一致）：")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(RESPONSE_FIELDS, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    if response_wrapper and bundle.requests:
        lines.append("导入时使用下面这个外壳——**把它原样保存为 `.json` 文件**，")
        lines.append("只需把每个 `content` 替换成模型对该请求返回的 JSON 文本即可：")
        lines.append("")
        lines.append("```json")
        lines.append(
            json.dumps(
                {
                    "protocol_version": bundle.protocol_version,
                    "bundle_id": bundle.bundle_id,
                    "model_name": "",
                    "responses": [
                        {
                            "request_id": request.request_id,
                            "content": "",
                        }
                        for request in bundle.requests
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        lines.append("```")
        lines.append("")
    lines.append("## 请求清单")
    lines.append("")
    for request in bundle.requests:
        lines.append("- `%s` · chunk `%s` · 字符 %d-%d%s" % (
            request.request_id,
            request.chunk_id,
            request.char_start,
            request.char_end,
            (" · 第 %s 页" % request.page) if request.page is not None else "",
        ))
    lines.append("")
    lines.append("## 导入后的处理")
    lines.append("")
    lines.append("- 包里的 `bundle_id` 会按材料**当前**内容重算并逐字比对：不匹配直接拒绝。")
    lines.append("- 模型自报的引用会被重写为本请求的 chunk；无法在材料里定位的内容不会入库。")
    lines.append("- 逐请求给出状态（成功 / 缺响应 / 未知请求 / 重复 / 格式错误 / grounding 失败），")
    lines.append("  可以只重试失败的那几条。")
    lines.append("- 重复导入同一份响应是幂等的，不会重复产生知识点。")
    lines.append("- 本地模型返回的文件会如实记为 `%s`，不会冒充真实 API 调用。" % USER_PASTED_PROVIDER_NAME)
    return "\n".join(lines) + "\n"


@dataclass
class ParsedResponses:
    """响应包的**初步**校验结果 (还没进管线)。"""

    bundle_id: str = ""
    protocol_version: str = ""
    model_reported: str = ""
    #: chunk_id -> 模型返回的 JSON 文本 (只含通过初筛的)。
    responses: dict[str, str] = field(default_factory=dict)
    #: request_id -> 初筛状态。
    statuses: dict[str, str] = field(default_factory=dict)
    #: 初筛问题 (request_id / status / detail), 供前端逐条展示。
    problems: list[dict[str, Any]] = field(default_factory=list)
    #: 重复出现过的 request_id (保留第一条, 重复的记为 duplicate)。
    duplicates: list[str] = field(default_factory=list)
    #: 不属于本包的 request_id。
    unknown: list[str] = field(default_factory=list)

    @property
    def statuses_in_bundle_order(self) -> dict[str, str]:
        return dict(self.statuses)


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def parse_response_payload(payload: Any, *, bundle: ManualBundle) -> ParsedResponses:
    """校验用户粘贴的响应包, 返回初筛结果 (不抛异常的部分进 ``responses``)。

    结构性错误 (协议版本 / bundle / responses 形状) 抛 ``ManualProtocolError``:
    这些情况下**整包都不该被接受**。逐条问题 (超长 / 畸形 JSON / schema /
    未知 / 重复 / 缺失) 则记进 ``statuses``, 允许部分成功。
    """
    if not isinstance(payload, Mapping):
        raise ManualProtocolError(
            "response payload must be a JSON object",
            code="RESPONSES_INVALID",
        )
    version = str(payload.get("protocol_version") or "").strip()
    if version not in MANUAL_PROTOCOL_VERSION_ALIASES:
        raise ManualProtocolError(
            "unsupported manual protocol version: %r" % (version or "<empty>",),
            code="PROTOCOL_VERSION_UNSUPPORTED",
            detail={"supported": MANUAL_PROTOCOL_VERSION, "received": version},
        )
    claimed = str(payload.get("bundle_id") or "").strip()
    if not claimed:
        raise ManualProtocolError("bundle_id is required", code="BUNDLE_MISSING")
    if claimed != bundle.bundle_id:
        # 包里自报的 ID 与"按材料当前内容重算的 ID"不一致 —— 拒绝整包。
        raise ManualProtocolError(
            "this response belongs to a different bundle; recompute it by exporting again",
            code="BUNDLE_MISMATCH",
            detail={"expected": bundle.bundle_id, "received": claimed},
        )
    raw_responses = payload.get("responses")
    if not isinstance(raw_responses, Sequence) or isinstance(raw_responses, (str, bytes)):
        raise ManualProtocolError(
            "responses must be a list of {request_id, content}",
            code="RESPONSES_INVALID",
        )
    if len(raw_responses) > MAX_REQUESTS:
        raise ManualProtocolError(
            "too many responses: %d > %d" % (len(raw_responses), MAX_REQUESTS),
            code="TOO_MANY_RESPONSES",
            detail={"max_requests": MAX_REQUESTS, "received": len(raw_responses)},
        )

    parsed = ParsedResponses(
        bundle_id=claimed,
        protocol_version=version,
        model_reported=_as_text(payload.get("model_name") or payload.get("model") or "").strip()[:120],
    )
    by_request = bundle.request_by_id()
    for request in bundle.requests:
        parsed.statuses[request.request_id] = STATUS_MISSING

    for entry in raw_responses:
        if not isinstance(entry, Mapping):
            parsed.problems.append({"request_id": "", "status": STATUS_SCHEMA_ERROR,
                                    "detail": "response entry must be an object"})
            continue
        request_id = str(entry.get("request_id") or "").strip()
        if request_id not in by_request:
            parsed.unknown.append(request_id or "<empty>")
            parsed.problems.append({"request_id": request_id, "status": STATUS_UNKNOWN_REQUEST,
                                    "detail": "request_id does not belong to this bundle"})
            continue
        if parsed.statuses.get(request_id) in (
            STATUS_APPLIED, STATUS_EMPTY, STATUS_MALFORMED_JSON, STATUS_SCHEMA_ERROR,
            STATUS_TOO_LARGE, STATUS_DUPLICATE_REQUEST,
        ):
            # 同一条请求出现两次: 只认第一条, 第二条记为重复 (不静默覆盖)。
            parsed.duplicates.append(request_id)
            parsed.problems.append({"request_id": request_id, "status": STATUS_DUPLICATE_REQUEST,
                                    "detail": "duplicate request_id; the first one is kept"})
            continue
        content = entry.get("content")
        if isinstance(content, Mapping) or isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
            # 允许直接贴对象: 规范化成 JSON 文本再过同一套校验 (不改变判据)。
            try:
                content = json.dumps(content, ensure_ascii=False)
            except (TypeError, ValueError):
                parsed.statuses[request_id] = STATUS_SCHEMA_ERROR
                parsed.problems.append({"request_id": request_id, "status": STATUS_SCHEMA_ERROR,
                                        "detail": "content is not serialisable"})
                continue
        if not isinstance(content, str) or not content.strip():
            parsed.statuses[request_id] = STATUS_SCHEMA_ERROR
            parsed.problems.append({"request_id": request_id, "status": STATUS_SCHEMA_ERROR,
                                    "detail": "content must be a non-empty string"})
            continue
        if len(content) > MAX_RESPONSE_CHARS:
            parsed.statuses[request_id] = STATUS_TOO_LARGE
            parsed.problems.append({"request_id": request_id, "status": STATUS_TOO_LARGE,
                                    "detail": "content exceeds %d chars" % MAX_RESPONSE_CHARS})
            continue
        # 与网络路径**同一套** schema 校验 (只做解析, 不落库、不改写引用语义)。
        result, error = parse_structured_response(content, by_request[request_id].chunk_id)
        if error is not None or result is None:
            parsed.statuses[request_id] = STATUS_MALFORMED_JSON
            parsed.problems.append({"request_id": request_id, "status": STATUS_MALFORMED_JSON,
                                    "detail": str(error or "malformed")[:160]})
            continue
        parsed.responses[request_id] = content
        parsed.statuses[request_id] = STATUS_APPLIED
    return parsed


def responses_by_chunk(parsed: ParsedResponses, *, bundle: ManualBundle) -> dict[str, str]:
    """``chunk_id -> 响应文本``: ``ReplayAIProvider`` 的查表 (按重算后的 id 定位)。"""
    by_request = bundle.request_by_id()
    out: dict[str, str] = {}
    for request_id, content in parsed.responses.items():
        request = by_request.get(request_id)
        if request is None:
            continue
        out[request.chunk_id] = content
    return out


class ReplayAIProvider(AIProvider):
    """把用户粘贴的响应当模型输出回放的 provider (零网络)。

    查表键是调用方给的 ``request_key`` (= 确定性 ``chunk_id``)。找不到就
    **报错**, 绝不"用别的请求的回答顶上" —— 错位响应不能被静默接受。

    ``name`` 是 ``user-pasted``: 报告、日志、健康检查都能看出这不是真实 API
    调用。``model`` 固定为空 (用户自报的模型名不进身份, 否则同一份响应会因为
    多打一个模型名而得到不同 identity, 破坏幂等)。
    """

    name = USER_PASTED_PROVIDER_NAME

    def __init__(
        self,
        responses: Optional[Mapping[str, str]] = None,
        *,
        bundle_id: str = "",
    ) -> None:
        self._responses = {str(k): str(v) for k, v in dict(responses or {}).items()}
        self._bundle_id = str(bundle_id or "")
        self._served: list[str] = []
        self._missed: list[str] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        # 纯文本结构化输出; 不声明 image/audio —— 手动模式只回放抽取阶段。
        return ProviderCapabilities(
            supports_text=True,
            supports_image=False,
            supports_audio=False,
            supports_structured_output=True,
            supports_image_bytes=False,
        )

    @property
    def model(self) -> str:
        """固定空串: 用户自报的模型名**不**参与处理身份。"""
        return ""

    @property
    def bundle_id(self) -> str:
        return self._bundle_id

    @property
    def served_keys(self) -> list[str]:
        return list(self._served)

    @property
    def missed_keys(self) -> list[str]:
        return list(self._missed)

    def generate_structured(
        self,
        prompt: str,
        *,
        timeout_seconds: int = 60,
        max_output_chars: int = 8000,
        request_key: Optional[str] = None,
    ) -> str:
        void = (prompt, timeout_seconds, max_output_chars)
        _ = void
        key = str(request_key or "")
        if key and key in self._responses:
            self._served.append(key)
            return self._responses[key]
        self._missed.append(key or "<no-request-key>")
        raise AIRequestError(
            "no pasted response for this request; export the bundle again and "
            "answer every request_id",
            detail={
                "provider": self.name,
                "bundle_id": self._bundle_id,
                "request_key": key or "<none>",
            },
        )

    def __repr__(self) -> str:
        # 脱敏: 响应正文绝不进 repr (可能含材料原文)。
        return "ReplayAIProvider(bundle_id=%r, responses=%d)" % (
            self._bundle_id,
            len(self._responses),
        )


def build_request_report(
    *,
    bundle: ManualBundle,
    parsed: ParsedResponses,
    analysis: Optional[Mapping[str, Any]] = None,
    extra_statuses: Optional[Mapping[str, str]] = None,
) -> list[dict[str, Any]]:
    """逐请求状态表 (导入结果的唯一口径)。

    优先级: 初筛问题 (缺失/未知/重复/畸形/超长) > 管线失败 > grounding 结果。
    ``analysis`` 是 ``AIAnalysisReport.to_dict()``; 缺字段时按"没跑管线"处理,
    如实标 ``failed``, 绝不把没跑的请求报成成功。
    """
    report = dict(analysis or {})
    failed_chunks = dict(report.get("failed_chunks") or {})
    candidate_counts = dict(report.get("chunk_candidate_counts") or {})
    rejected_by_chunk: dict[str, int] = {}
    for item in report.get("rejected") or []:
        chunk_id = str((item or {}).get("chunk_id") or "")
        if chunk_id:
            rejected_by_chunk[chunk_id] = rejected_by_chunk.get(chunk_id, 0) + 1
    extra = dict(extra_statuses or {})

    rows: list[dict[str, Any]] = []
    for request in bundle.requests:
        status = parsed.statuses.get(request.request_id, STATUS_MISSING)
        detail = ""
        if request.request_id in extra:
            status = extra[request.request_id]
        if status in (STATUS_MISSING, STATUS_UNKNOWN_REQUEST, STATUS_DUPLICATE_REQUEST,
                      STATUS_MALFORMED_JSON, STATUS_SCHEMA_ERROR, STATUS_TOO_LARGE):
            detail = next(
                (
                    str(problem.get("detail") or "")
                    for problem in parsed.problems
                    if str(problem.get("request_id") or "") == request.request_id
                ),
                "",
            )
        elif request.chunk_id in failed_chunks:
            status = STATUS_FAILED
            detail = str(failed_chunks[request.chunk_id])[:200]
        elif status == STATUS_APPLIED:
            count = int(candidate_counts.get(request.chunk_id, 0))
            if count <= 0:
                status = STATUS_EMPTY
                detail = "the response contained no knowledge points"
            elif rejected_by_chunk.get(request.chunk_id, 0) >= count:
                # 解析出候选, 但一条都没通过 grounding: 这是"看起来成功、实际
                # 什么都没入库"的那类结果, 必须单独报, 不能算 applied。
                status = STATUS_GROUNDING_FAILED
                detail = "no candidate could be grounded in this chunk"
        rows.append(
            {
                "request_id": request.request_id,
                "stage": request.stage,
                "chunk_id": request.chunk_id,
                "chunk_index": request.chunk_index,
                "page": request.page,
                "status": status,
                "detail": detail,
                "candidates": int(candidate_counts.get(request.chunk_id, 0)),
            }
        )
    return rows
