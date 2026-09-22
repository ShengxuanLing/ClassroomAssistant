import hashlib
from dataclasses import dataclass, field
from typing import List

from src.models import (
    Evidence, EvidenceType, Language, Confidence,
    SourceReference, Transcript, TranscriptSegment, TranscriptLanguage,
)

class TranscriptionEngine:
    def transcribe(self, audio_path: str, language: str = "unknown") -> Transcript:
        raise NotImplementedError
    def transcribe_segments(self, audio_path: str, segments: List[dict]) -> List[TranscriptSegment]:
        raise NotImplementedError

class MockTranscriber(TranscriptionEngine):
    def __init__(self) -> None:
        self._default_language = TranscriptLanguage.UNKNOWN
    def transcribe(self, audio_path: str, language: str = "unknown") -> Transcript:
        lang = TranscriptLanguage.from_string(language)
        segments: List[TranscriptSegment] = []
        path_hash = hashlib.sha256(audio_path.encode("utf-8")).hexdigest()
        num_segments = (int(path_hash[:2], 16) % 5) + 1
        for i in range(num_segments):
            seg_start = round(i * 10.0 + (int(path_hash[2+i*2:4+i*2], 16) % 10) * 0.1, 2)
            seg_end = round(seg_start + 5.0 + (int(path_hash[4+i*2:6+i*2], 16) % 5) * 0.1, 2)
            text = "Segment " + str(i+1) + " from " + audio_path
            confidence = 0.5 + (int(path_hash[6+i*2:8+i*2], 16) % 50) / 100.0
            confidence = min(confidence, 1.0)
            segments.append(TranscriptSegment(start=seg_start, end=seg_end, text=text, speaker="Speaker_"+str(i%3), language=lang, confidence=round(confidence, 2)))
        return Transcript(material_id=audio_path, language=lang, segments=segments)
    def transcribe_segments(self, audio_path: str, segments: List[dict]) -> List[TranscriptSegment]:
        result: List[TranscriptSegment] = []
        for seg_data in segments:
            seg = TranscriptSegment(start=seg_data.get("start", 0.0), end=seg_data.get("end", 0.0), text=seg_data.get("text", ""), speaker=seg_data.get("speaker", ""), language=TranscriptLanguage.from_string(seg_data.get("language", "unknown")), confidence=seg_data.get("confidence", 0.5))
            result.append(seg)
        return result

def transcript_to_evidence(transcript: Transcript) -> Evidence:
    return transcript.to_transcript_evidence()

def create_transcript_from_segments(material_id, segments_data, language="unknown") -> Transcript:
    lang = TranscriptLanguage.from_string(language)
    segments: List[TranscriptSegment] = []
    for seg_data in segments_data:
        seg = TranscriptSegment(start=seg_data.get("start", 0.0), end=seg_data.get("end", 0.0), text=seg_data.get("text", ""), speaker=seg_data.get("speaker", ""), language=lang, confidence=seg_data.get("confidence", 0.5))
        segments.append(seg)
    return Transcript(material_id=material_id, language=lang, segments=segments)
