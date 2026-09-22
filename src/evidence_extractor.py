"""Evidence extraction pipeline for the Classroom Assistant project.

Orchestrates different parsers based on Material type to produce
structured Evidence objects. Supports TXT/Markdown notes via
NoteParser and PDF/DOCX documents via the Task 22
DocumentEvidenceExtractor; unsupported types return an empty list
safely.
"""

from __future__ import annotations
import os
from typing import List

from src.models import Language, Material, Evidence
from src.note_parser import NoteParser
from src.document_evidence import DocumentEvidenceExtractor
from src.document_input import parse_document


class EvidenceExtractor:
    """Unified Evidence extraction pipeline."""

    _SUPPORTED_EXTENSIONS = {'.txt', '.md', '.markdown'}
    _DOCUMENT_EXTENSIONS = {'.pdf', '.docx'}

    def __init__(self, material: Material) -> None:
        self.material = material

    def extract(self) -> List[Evidence]:
        ext = os.path.splitext(self.material.path)[1].lower()
        if ext in self._DOCUMENT_EXTENSIONS:
            parsed = parse_document(self.material.path)
            return DocumentEvidenceExtractor().extract(parsed)
        if ext not in self._SUPPORTED_EXTENSIONS:
            return []
        try:
            parser = NoteParser(self.material)
            evidences = parser.parse()
        except Exception:
            return []
        if self.material.language != Language.UNKNOWN:
            for ev in evidences:
                ev.language = self.material.language
        return evidences

    @staticmethod
    def extract_all(materials: List[Material]) -> List[Evidence]:
        all_evidence: List[Evidence] = []
        for material in materials:
            extractor = EvidenceExtractor(material)
            evidence = extractor.extract()
            all_evidence.extend(evidence)
        return all_evidence