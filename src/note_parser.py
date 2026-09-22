"""Note Parser module for the Classroom Assistant project.

Parses TXT and Markdown note files into structured Evidence objects.
Follows the task specification for Task 4.
"""

from __future__ import annotations
import os
import re
from typing import List, Optional

from src.models import (
    Confidence, Evidence, EvidenceType, Language, Material, SourceReference
)


class NoteParser:
    """Parses note files (TXT, Markdown) into Evidence objects."""

    def __init__(self, material: Material) -> None:
        self.material = material
        self._content: str = ""
        self._filepath: str = material.path if material.path else ""

    def parse(self) -> List[Evidence]:
        self._load_file()
        if not self._content.strip():
            return []
        ext = os.path.splitext(self._filepath)[1].lower()
        if ext == ".md":
            return self._parse_markdown()
        else:
            return self._parse_txt()

    def _load_file(self) -> None:
        full_path = self._get_full_path()
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"Note file not found: {full_path}")
        with open(full_path, "r", encoding="utf-8") as f:
            self._content = f.read()

    def _get_full_path(self) -> str:
        if os.path.isabs(self._filepath):
            return self._filepath
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        full = os.path.join(project_root, self._filepath)
        if os.path.exists(full):
            return full
        return self._filepath

    def _detect_language(self, text: str) -> Language:
        if re.search(r"[\u4e00-\u9fff]", text):
            return Language.CHINESE
        if re.search(r"[\u00e0-\u00f6\u00f8-\u00ff]", text):
            if re.search(r"\b(fotos\u00ed|fotosi|proc\u00e8|proc\u00f2|llibre|escola|govern|consell)\b", text, re.IGNORECASE):
                return Language.CATALAN
        if re.search(r"[\u00e1-\u00f6\u00f1\u00fc]", text):
            if re.search(r"\b(el|la|los|las|un|una|del|de|que|y|en|por|con|para|como|pero|m?s|ya|sobre)\b", text, re.IGNORECASE):
                return Language.SPANISH
        return Language.UNKNOWN

    def _create_evidence(self, content: str, line: Optional[int] = None, paragraph: Optional[str] = None, evidence_type: EvidenceType = EvidenceType.PERSONAL_NOTE) -> Evidence:
        # location must be path-FREE (filename + line/paragraph), never the
        # absolute upload path: it is hashed into the evidence_id by
        # evidence_ingestion._ensure_evidence_ref, so an absolute path would
        # make note evidence ids (and everything assembled from them) differ
        # between two runs in different directories. The material_id already
        # carries the provenance link back to the material.
        source_ref = SourceReference(
            material_id=self.material.material_id,
            location=os.path.basename(self._filepath or ""),
            line=line,
            paragraph=paragraph,
        )
        lang = self._detect_language(content)
        return Evidence(content=content.strip(), language=lang, source_reference=source_ref, confidence=Confidence.MEDIUM, evidence_type=evidence_type, metadata={})

    def _parse_txt(self) -> List[Evidence]:
        lines = self._content.split("\n")
        evidences: List[Evidence] = []
        current_para_lines: List[str] = []
        current_para_start = 0
        para_idx = 0
        for i, line in enumerate(lines):
            if line.strip() == "":
                if current_para_lines:
                    evidences.append(self._create_evidence(content="\n".join(current_para_lines), line=current_para_start, paragraph=f"paragraph_{para_idx}"))
                    current_para_lines = []
                    para_idx += 1
            else:
                if not current_para_lines:
                    current_para_start = i + 1
                current_para_lines.append(line)
        if current_para_lines:
            evidences.append(self._create_evidence(content="\n".join(current_para_lines), line=current_para_start, paragraph=f"paragraph_{para_idx}"))
        return evidences

    def _parse_markdown(self) -> List[Evidence]:
        lines = self._content.split("\n")
        evidences: List[Evidence] = []
        current_block: List[str] = []
        current_type: Optional[str] = None
        current_start = 0
        heading_level = 0
        def _flush() -> None:
            nonlocal current_block, current_type, current_start, heading_level
            if not current_block:
                return
            content = "\n".join(current_block)
            if current_type == "heading":
                pid = f"heading_h{heading_level}"
            elif current_type == "bullet":
                pid = "bullet_list"
            elif current_type == "numbered":
                pid = "numbered_list"
            else:
                pid = "paragraph"
            evidences.append(self._create_evidence(content=content, line=current_start, paragraph=pid))
            current_block = []
            current_type = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            hm = re.match(r"^(#{1,6})\s+(.+)$", stripped)
            if hm:
                _flush()
                current_block = [line]
                current_type = "heading"
                heading_level = len(hm.group(1))
                current_start = i + 1
                continue
            bm = re.match(r"^\s*[-*+]\s+", stripped)
            nm = re.match(r"^\s*\d+[.)]\s+", stripped)
            if bm or nm:
                if current_type == "bullet" and bm:
                    current_block.append(line)
                elif current_type == "numbered" and nm:
                    current_block.append(line)
                elif current_type == "bullet" and nm:
                    _flush()
                    current_block = [line]
                    current_type = "numbered"
                    current_start = i + 1
                elif current_type == "numbered" and bm:
                    _flush()
                    current_block = [line]
                    current_type = "bullet"
                    current_start = i + 1
                elif current_type is None or current_type == "paragraph":
                    _flush()
                    current_block = [line]
                    current_type = "bullet" if bm else "numbered"
                    current_start = i + 1
                else:
                    current_block.append(line)
                continue
            if stripped == "":
                _flush()
                continue
            if current_type is None:
                current_block = [line]
                current_type = "paragraph"
                current_start = i + 1
            elif current_type == "paragraph":
                current_block.append(line)
            elif current_type == "heading":
                _flush()
                current_block = [line]
                current_type = "paragraph"
                current_start = i + 1
            else:
                current_block.append(line)
        _flush()
        return evidences

    @staticmethod
    def parse_file(filepath: str) -> List[Evidence]:
        material = Material(filename=os.path.basename(filepath), path=filepath, material_type="note", language=Language.UNKNOWN)
        parser = NoteParser(material)
        return parser.parse()
