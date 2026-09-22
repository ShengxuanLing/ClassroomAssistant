# -*- coding: utf-8 -*-
"""确定性文本分块 (TASK-76 §10)。

不能把 200 页 PDF 一次性塞进模型::

    Document -> section detection -> paragraph boundaries
        -> token/character budget -> overlap -> chunks

chunk 拥有稳定 ID::

    material_id / page / section / chunk_index / content_hash

同一文件重复处理 -> 同一 chunk identity (幂等与 chunk caching 的基础)。
纯确定性: 无随机、无模型调用、无证据改写。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

__all__ = [
    "DEFAULT_CHUNK_CHARS",
    "DEFAULT_CHUNK_OVERLAP_CHARS",
    "DEFAULT_MAX_CHUNKS",
    "TextChunk",
    "chunk_text",
    "chunk_identity",
]

#: 默认分块预算 (字符数; 按中文/西语混合文本的保守估计, 约 < 1k tokens)。
DEFAULT_CHUNK_CHARS = 2000
#: 相邻 chunk 重叠 (保留跨边界语义)。
DEFAULT_CHUNK_OVERLAP_CHARS = 200
#: 单材料 chunk 上限 (成本熔断: 超出则截断并标记 truncated)。
DEFAULT_MAX_CHUNKS = 100

_SECTION_HEADING = re.compile(
    r"^(#{1,4}\s+|第.+章|Tema\s+\d+|Lecci[oó]n\s+\d+|\d+(?:\.\d+)*\s+\S+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TextChunk:
    """一个确定性文本分块。"""

    chunk_id: str
    material_id: str
    chunk_index: int
    page: Optional[int]
    section: str
    text: str
    content_hash: str
    char_start: int
    char_end: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "material_id": self.material_id,
            "chunk_index": self.chunk_index,
            "page": self.page,
            "section": self.section,
            "text": self.text,
            "content_hash": self.content_hash,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }


def chunk_identity(
    *,
    material_id: str,
    page: Optional[int],
    section: str,
    chunk_index: int,
    content_hash: str,
) -> str:
    """稳定 chunk ID: ``chunk-<sha16(material|page|section|index|hash)>``。"""
    raw = "|".join(
        [
            str(material_id),
            "" if page is None else str(page),
            str(section or ""),
            str(chunk_index),
            str(content_hash),
        ]
    ).encode("utf-8")
    return "chunk-" + hashlib.sha256(raw).hexdigest()[:16]


def _split_paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if parts:
        return parts
    # 无空行分隔时按单换行回落 (转写文本常见)。
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def chunk_text(
    text: str,
    *,
    material_id: str,
    page: Optional[int] = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
) -> tuple[list[TextChunk], bool]:
    """把文本切成确定性 chunk, 返回 ``(chunks, truncated)``。

    - 空文本 -> ``([], False)`` (调用方决定是跳过还是记 failure, 不在这里编证据)。
    - 同一输入永远产出同一 chunk 序列 (含 ID)。
    """
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return [], False
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be positive")
    overlap = max(0, min(int(overlap_chars), chunk_chars // 2))

    chunks: list[TextChunk] = []
    section = ""
    current: list[str] = []
    current_len = 0
    char_cursor = 0
    truncated = False

    def _flush(is_last: bool = False) -> None:
        nonlocal current, current_len, char_cursor, section
        if not current:
            return
        body = "\n\n".join(current)
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        index = len(chunks)
        start = char_cursor
        end = char_cursor + len(body)
        chunks.append(
            TextChunk(
                chunk_id=chunk_identity(
                    material_id=material_id,
                    page=page,
                    section=section,
                    chunk_index=index,
                    content_hash=content_hash,
                ),
                material_id=material_id,
                chunk_index=index,
                page=page,
                section=section,
                text=body,
                content_hash=content_hash,
                char_start=start,
                char_end=end,
            )
        )
        char_cursor = end
        if overlap and not is_last:
            tail = body[-overlap:]
            current = [tail] if tail.strip() else []
            current_len = len(tail)
        else:
            current = []
            current_len = 0

    for para in paragraphs:
        if _SECTION_HEADING.match(para.splitlines()[0] if para else ""):
            if current:
                _flush()
            section = (para.splitlines()[0] or "")[:80]
        if current_len + len(para) + 2 > chunk_chars and current:
            _flush()
            if len(chunks) >= max_chunks:
                truncated = True
                break
        current.append(para)
        current_len += len(para) + 2
    if not truncated and current:
        _flush(is_last=True)
    if len(chunks) > max_chunks:
        truncated = True
        return chunks[:max_chunks], True
    return chunks, truncated


def chunks_for_evidence(
    evidences: Sequence[Any],
    *,
    material_id: str,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
) -> tuple[list[TextChunk], dict[str, str], bool]:
    """把同一材料的 Evidence 正文拼成 chunk 序列。

    返回 ``(chunks, chunk_to_evidence, truncated)``: 每个 chunk 记住它来自
    哪条 Evidence (``chunk_to_evidence[chunk_id] = evidence_id``) —— 这是
    后续 evidence grounding 把"模型引用的 chunk"翻回"真实 Evidence" 的
    唯一映射表, 模型本身永远接触不到真实 Evidence ID。
    """
    merged: list[tuple[str, str, Optional[int]]] = []
    for ev in evidences or ():
        content = getattr(ev, "content", "") or ""
        if not content.strip():
            continue
        ref = getattr(ev, "source_reference", None)
        page = getattr(ref, "page", None) if ref is not None else None
        merged.append((str(getattr(ev, "evidence_id", "")), content, page))
    if not merged:
        return [], {}, False
    all_chunks: list[TextChunk] = []
    mapping: dict[str, str] = {}
    truncated_any = False
    for evidence_id, content, page in merged:
        chunks, truncated = chunk_text(
            content,
            material_id=material_id,
            page=page,
            chunk_chars=chunk_chars,
            overlap_chars=overlap_chars,
            max_chunks=max(1, max_chunks - len(all_chunks)),
        )
        truncated_any = truncated_any or truncated
        for chunk in chunks:
            all_chunks.append(chunk)
            mapping[chunk.chunk_id] = evidence_id
        if len(all_chunks) >= max_chunks:
            truncated_any = True
            break
    return all_chunks[:max_chunks], mapping, truncated_any
