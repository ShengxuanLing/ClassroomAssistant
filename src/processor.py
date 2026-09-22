from __future__ import annotations

import os
from typing import List, Optional

from src.models import (
    Material,
    MaterialType,
    Evidence,
    Language,
    Confidence,
    EvidenceType,
    SourceReference,
    ClassSession,
)
from src.note_parser import NoteParser
from src.evidence_extractor import EvidenceExtractor
from src.transcription import TranscriptionEngine, MockTranscriber, transcript_to_evidence
from src.ocr_processor import OCREngine, MockOCREngine, ocr_to_evidence
from src.asr_provider import ASRProvider, ASRTranscriptionEngineAdapter


class ClassSessionProcessor:
    """Unified evidence pipeline processor for classroom sessions.

    Orchestrates different handlers based on Material type to produce
    structured Evidence objects. This is an orchestration layer that
    delegates to existing processors - it does NOT implement real ASR,
    OCR, LLM, or knowledge generation.

    Pipeline:
        Material
          |
          +-- note (TXT/MD) --> NoteParser --> Evidence
          +-- audio --> TranscriptionEngine --> transcript_to_evidence() --> Evidence
          +-- image --> OCREngine --> ocr_to_evidence() --> Evidence
                              |
                              v
                        Unified Evidence List
    """

    # Extensions mapped to material types
    _NOTE_EXTENSIONS = {'.txt', '.md', '.markdown'}
    _AUDIO_EXTENSIONS = {'.mp3', '.m4a', '.wav', '.ogg'}
    _IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}

    def __init__(
        self,
        transcription_engine: Optional[TranscriptionEngine] = None,
        ocr_engine: Optional[OCREngine] = None,
        asr_provider: Optional[ASRProvider] = None,
    ) -> None:
        if asr_provider is not None:
            engine = ASRTranscriptionEngineAdapter(asr_provider)
            self._transcription_engine: TranscriptionEngine = (
                transcription_engine or engine or MockTranscriber()
            )
        else:
            self._transcription_engine = transcription_engine or MockTranscriber()
        self._ocr_engine: OCREngine = ocr_engine or MockOCREngine()

    def process_session(
        self,
        session: Optional[ClassSession],
        materials: List[Material],
    ) -> List[Evidence]:
        """Process materials belonging to a session and return a unified Evidence list.

        Args:
            session: The ClassSession these materials belong to (may be None).
            materials: List of Material objects to process.

        Returns:
            A stable-ordered list of Evidence objects.
        """
        all_evidence: List[Evidence] = []
        for material in materials:
            evidence = self._process_material(material)
            all_evidence.extend(evidence)
        return all_evidence

    def process_materials(self, materials: List[Material]) -> List[Evidence]:
        """Convenience method: process a list of Materials without a session."""
        return self.process_session(None, materials)

    def _process_material(self, material: Material) -> List[Evidence]:
        """Process a single material, routing by its MaterialType."""
        if material.material_type == MaterialType.NOTE:
            return self._process_note(material)
        elif material.material_type == MaterialType.AUDIO:
            return self._process_audio(material)
        elif material.material_type == MaterialType.IMAGE:
            return self._process_image(material)
        else:
            # SYLLABUS or unsupported - safe skip, no fabricated evidence
            return []

    def _process_note(self, material: Material) -> List[Evidence]:
        """Route note/txt/md to the existing EvidenceExtractor."""
        extractor = EvidenceExtractor(material)
        return extractor.extract()

    def _process_audio(self, material: Material) -> List[Evidence]:
        """Route audio to the TranscriptionEngine, then transcript_to_evidence().

        transcript_to_evidence() returns a single Evidence; wrap it in a list.
        """
        transcript = self._transcription_engine.transcribe(
            material.path or '', language='unknown'
        )
        single = transcript_to_evidence(transcript)
        return [single] if single is not None else []

    def _process_image(self, material: Material) -> List[Evidence]:
        """Route image to the OCREngine, then ocr_to_evidence()."""
        ocr_result = self._ocr_engine.ocr(material)
        return ocr_to_evidence(ocr_result)
