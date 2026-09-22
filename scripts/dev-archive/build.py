import hashlib,uuid
from dataclasses import dataclass,field
from enum import Enum
from typing import Any

# Read models.py
with open(r"D:\Project\Clases\src\models.py","r",encoding="utf-8") as fh:
    mcontent = fh.read()

# Add new classes
new_classes = """
@dataclass
class TranscriptLanguage(str, Enum):
    SPANISH = "Spanish"
    CATALAN = "Catalan"
    CHINESE = "Chinese"
    ENGLISH = "English"
    UNKNOWN = "Unknown"
    @classmethod
    def from_string(cls, value: str) -> TranscriptLanguage:
        for lang in cls:
            if lang.value.lower() == value.strip().lower():
                return lang
        return cls.UNKNOWN

@dataclass
class TranscriptSegment:
    start: float = 0.0
    end: float = 0.0
    text: str = ""
    speaker: str = ""
    language: TranscriptLanguage = TranscriptLanguage.UNKNOWN
    confidence: float = 0.0
    def __post_init__(self) -> None:
        if self.start < 0: self.start = 0.0
        if self.end < self.start: self.end = self.start
        if not self.text.strip(): self.text = ""
    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "text": self.text, "speaker": self.speaker, "language": self.language.value, "confidence": self.confidence}
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TranscriptSegment:
        lang = data.get("language", TranscriptLanguage.UNKNOWN)
        if isinstance(lang, str): lang = TranscriptLanguage.from_string(lang)
        return cls(start=data.get("start",0.0), end=data.get("end",0.0), text=data.get("text",""), speaker=data.get("speaker",""), language=lang, confidence=data.get("confidence",0.0))

@dataclass
class Transcript:
    transcript_id: str = ""
    material_id: str = ""
    language: TranscriptLanguage = TranscriptLanguage.UNKNOWN
    segments: list[TranscriptSegment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    def __post_init__(self) -> None:
        if not self.transcript_id: self.transcript_id = self._generate_stable_id()
        if isinstance(self.language, str): self.language = TranscriptLanguage.from_string(self.language)
        validated: list[TranscriptSegment] = []
        for seg in self.segments:
            if isinstance(seg, dict): seg = TranscriptSegment.from_dict(seg)
            validated.append(seg)
        self.segments = validated
    def _generate_stable_id(self) -> str:
        raw = (self.material_id + str(len(self.segments)) + self.language.value).encode("utf-8")
        for seg in self.segments:
            raw += str(seg.start).encode("utf-8") + str(seg.end).encode("utf-8") + seg.text.encode("utf-8")
        return "transcript-" + hashlib.sha256(raw).hexdigest()[:16]
    def to_dict(self) -> dict[str, Any]:
        return {"transcript_id": self.transcript_id, "material_id": self.material_id, "language": self.language.value, "segments": [s.to_dict() for s in self.segments], "metadata": self.metadata}
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transcript:
        lang = data.get("language", TranscriptLanguage.UNKNOWN)
        if isinstance(lang, str): lang = TranscriptLanguage.from_string(lang)
        return cls(transcript_id=data["transcript_id"], material_id=data["material_id"], language=lang, segments=data.get("segments",[]), metadata=data.get("metadata",{}))
    def to_transcript_evidence(self) -> Evidence:
        content = "\n".join(seg.text for seg in self.segments if seg.text)
        source_ref = SourceReference(material_id=self.material_id, timestamp_start=self.segments[0].start if self.segments else None, timestamp_end=self.segments[-1].end if self.segments else None)
        return Evidence(content=content, language=self.language, source_reference=source_ref, confidence=Confidence.HIGH if self.segments and all(s.confidence>0.5 for s in self.segments) else Confidence.MEDIUM, evidence_type=EvidenceType.TRANSCRIPT, metadata={"transcript_id": self.transcript_id, "segment_count": len(self.segments)})
"""

new_content = mcontent.rstrip() + "\n" + new_classes + "\n"
with open(r"D:\Project\Clases\src\models.py","w",encoding="utf-8") as fh:
    fh.write(new_content)
print("models.py updated successfully")
