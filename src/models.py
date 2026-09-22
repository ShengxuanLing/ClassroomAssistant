"""Core data models for the Classroom Assistant project.

This module defines all fundamental data structures used throughout the
project. It is a pure data-definition module with basic serialization
capabilities - no business logic, processing, or AI calls here.
"""

from __future__ import annotations
import hashlib
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

class Language(str, Enum):
    SPANISH = "Spanish"
    CATALAN = "Catalan"
    CHINESE = "Chinese"
    ENGLISH = "English"
    UNKNOWN = "Unknown"
    @classmethod
    def from_string(cls, value: str) -> Language:
        for lang in cls:
            if lang.value.lower() == value.strip().lower(): return lang
        return cls.UNKNOWN

class MaterialType(str, Enum):
    AUDIO = "audio"
    IMAGE = "image"
    TEXT = "text"
    NOTE = "note"
    SYLLABUS = "syllabus"
    @classmethod
    def from_string(cls, value: str) -> MaterialType:
        for mt in cls:
            if mt.value.lower() == value.strip().lower(): return mt
        return cls.TEXT

class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNCERTAIN = "UNCERTAIN"
    @classmethod
    def from_string(cls, value: str) -> Confidence:
        for c in cls:
            if c.value.lower() == value.strip().lower(): return c
        return cls.UNCERTAIN

class EvidenceType(str, Enum):
    TRANSCRIPT = "transcript"
    OCR = "OCR"
    PERSONAL_NOTE = "personal_note"
    CLASSMATE_NOTE = "classmate_note"
    TEACHER_STATEMENT = "teacher_statement"
    EXTRACTED_FACT = "extracted_fact"
    DOCUMENT = "document"
    OTHER = "other"
    @classmethod
    def from_string(cls, value: str) -> EvidenceType:
        for et in cls:
            if et.value.lower() == value.strip().lower(): return et
        return cls.OTHER

class VerificationStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    @classmethod
    def from_string(cls, value: str) -> VerificationStatus:
        for vs in cls:
            if vs.value.lower() == value.strip().lower(): return vs
        return cls.PENDING

class ValidationStatus(str, Enum):
    """校验状态: 一个知识点相对于**当前** Evidence 的一致性判定。

    - UNVERIFIED: 支撑证据不足, 或根本没有证据。
    - SUPPORTED: 有一条或多条不同 Evidence 支撑该点, 且没有未决冲突触达它。
      这只说明当前材料是自洽的 —— **不是**对"事实为真"的断言。
    - CONFLICTED: 至少一条未决冲突触达该点。冲突两边都保留。

    注意: 此枚举原定义于 ``src.knowledge_validation`` 与 ``src.knowledge_review``,
    为消除领域核心 (``src.models``) 对上层模块的懒导入而将真源收口到这里
    (P1-4)。原模块保留兼容 re-export。
    """
    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    CONFLICTED = "conflicted"

    @classmethod
    def from_string(cls, value: Any) -> "ValidationStatus":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            for st in cls:
                if st.value.lower() == value.strip().lower():
                    return st
        return cls.UNVERIFIED


class ReviewStatus(str, Enum):
    """人工审核状态 (与 ValidationStatus 相互独立)。

    - PENDING: 尚未收到明确的人决策。
    - CONFIRMED: 用户明确确认了该点 (冲突时一并确认信任哪一侧)。
    - REJECTED: 用户明确拒绝。
    - KEPT_UNVERIFIED: 用户明确决定暂时保留未校验状态 (禁止自动确认)。

    达到 CONFIRMED / REJECTED / KEPT_UNVERIFIED 必须通过 KnowledgeReviewService
    的显式人决策; 确定性管线里的任何东西都不得自动产生这些状态。
    """
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    KEPT_UNVERIFIED = "kept_unverified"

    @classmethod
    def from_string(cls, value: Any) -> "ReviewStatus":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            for st in cls:
                if st.value.lower() == value.strip().lower():
                    return st
        return cls.PENDING


@dataclass
class SourceReference:
    material_id: str = ""
    location: Optional[str] = None
    timestamp_start: Optional[float] = None
    timestamp_end: Optional[float] = None
    page: Optional[int] = None
    line: Optional[int] = None
    paragraph: Optional[str] = None
    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in [("material_id", self.material_id), ("location", self.location), ("timestamp_start", self.timestamp_start), ("timestamp_end", self.timestamp_end), ("page", self.page), ("line", self.line), ("paragraph", self.paragraph)]}
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceReference":
        if data is None:
            return cls()
        return cls(material_id=data.get("material_id", ""), location=data.get("location"), timestamp_start=data.get("timestamp_start"), timestamp_end=data.get("timestamp_end"), page=data.get("page"), line=data.get("line"), paragraph=data.get("paragraph"))

@dataclass
class Material:
    material_id: str = ""
    filename: str = ""
    path: str = ""
    material_type: MaterialType = MaterialType.TEXT
    language: Language = field(default_factory=lambda: Language.UNKNOWN)
    created_at: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    def __post_init__(self) -> None:
        if not self.material_id: self.material_id = str(uuid.uuid4())
        if isinstance(self.material_type, str): self.material_type = MaterialType.from_string(self.material_type)
        if isinstance(self.language, str): self.language = Language.from_string(self.language)
    def to_dict(self) -> dict[str, Any]:
        return {"material_id": self.material_id, "filename": self.filename, "path": self.path, "material_type": self.material_type.value, "language": self.language.value, "created_at": self.created_at, "metadata": self.metadata}
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Material:
        return cls(material_id=data["material_id"], filename=data["filename"], path=data["path"], material_type=data["material_type"], language=data["language"], created_at=data.get("created_at"), metadata=data.get("metadata", {}))

@dataclass
class Evidence:
    evidence_id: str = ""
    content: str = ""
    language: Language = field(default_factory=lambda: Language.UNKNOWN)
    source_reference: SourceReference = None
    confidence: Confidence = Confidence.UNCERTAIN
    evidence_type: EvidenceType = EvidenceType.OTHER
    metadata: dict[str, Any] = field(default_factory=dict)
    def __post_init__(self) -> None:
        if not self.evidence_id: self.evidence_id = str(uuid.uuid4())
        if isinstance(self.language, str): self.language = Language.from_string(self.language)
        if isinstance(self.confidence, str): self.confidence = Confidence.from_string(self.confidence)
        if isinstance(self.evidence_type, str): self.evidence_type = EvidenceType.from_string(self.evidence_type)
        if not isinstance(self.source_reference, SourceReference): self.source_reference = SourceReference.from_dict(self.source_reference)
    def to_dict(self) -> dict[str, Any]:
        return {"evidence_id": self.evidence_id, "content": self.content, "language": self.language.value, "source_reference": self.source_reference.to_dict(), "confidence": self.confidence.value, "evidence_type": self.evidence_type.value, "metadata": self.metadata}
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Evidence:
        ref = data.get("source_reference")
        return cls(evidence_id=data["evidence_id"], content=data["content"], language=data["language"], source_reference=ref if ref is not None else {}, confidence=data["confidence"], evidence_type=data["evidence_type"], metadata=data.get("metadata", {}))

@dataclass
class KnowledgePoint:
    knowledge_id: str = ""
    title: str = ""
    content: str = ""
    original_terms: list[str] = field(default_factory=list)
    importance: str = "medium"
    confidence: Confidence = Confidence.UNCERTAIN
    evidence_refs: list[str] = field(default_factory=list)
    related_points: list[str] = field(default_factory=list)
    needs_verification: bool = False
    validation_status: str = "unverified"
    knowledge_score: float = 0.0
    review_status: str = "pending"
    def __post_init__(self) -> None:
        if not self.knowledge_id: self.knowledge_id = str(uuid.uuid4())
        if isinstance(self.confidence, str): self.confidence = Confidence.from_string(self.confidence)
        if isinstance(self.importance, str): self.importance = self.importance.lower()
        if isinstance(self.validation_status, str):
            self.validation_status = ValidationStatus.from_string(self.validation_status).value
        if isinstance(self.review_status, str):
            self.review_status = ReviewStatus.from_string(self.review_status).value
        try:
            self.knowledge_score = float(self.knowledge_score)
        except (TypeError, ValueError):
            self.knowledge_score = 0.0
        if self.knowledge_score != self.knowledge_score or self.knowledge_score in (float("inf"), float("-inf")):
            self.knowledge_score = 0.0
        if self.knowledge_score < 0.0:
            self.knowledge_score = 0.0
        if self.knowledge_score > 1.0:
            self.knowledge_score = 1.0
    def to_dict(self) -> dict[str, Any]:
        return {"knowledge_id": self.knowledge_id, "title": self.title, "content": self.content, "original_terms": self.original_terms, "importance": self.importance, "confidence": self.confidence.value, "evidence_refs": self.evidence_refs, "related_points": self.related_points, "needs_verification": self.needs_verification, "validation_status": self.validation_status, "knowledge_score": self.knowledge_score, "review_status": self.review_status}
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgePoint:
        return cls(knowledge_id=data["knowledge_id"], title=data["title"], content=data["content"], original_terms=data.get("original_terms", []), importance=data.get("importance", "medium"), confidence=data.get("confidence", Confidence.UNCERTAIN), evidence_refs=data.get("evidence_refs", []), related_points=data.get("related_points", []), needs_verification=data.get("needs_verification", False), validation_status=data.get("validation_status", "unverified"), knowledge_score=data.get("knowledge_score", 0.0), review_status=data.get("review_status", "pending"))

@dataclass
class Course:
    course_id: str = ""
    name: str = ""
    code: str = ""
    language: Language = field(default_factory=lambda: Language.UNKNOWN)
    metadata: dict[str, Any] = field(default_factory=dict)
    def __post_init__(self) -> None:
        if not self.course_id:
            self.course_id = self._generate_stable_id()
        if isinstance(self.language, str):
            self.language = Language.from_string(self.language)
    def _generate_stable_id(self) -> str:
        raw = (self.name.strip().lower() + self.code.strip().lower()).encode("utf-8")
        return "course-" + hashlib.sha256(raw).hexdigest()[:16]
    def to_dict(self) -> dict[str, Any]:
        return {"course_id": self.course_id, "name": self.name, "code": self.code, "language": self.language.value, "metadata": self.metadata}
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Course:
        return cls(course_id=data["course_id"], name=data.get("name", ""), code=data.get("code", ""), language=data.get("language", Language.UNKNOWN), metadata=data.get("metadata", {}))

@dataclass
class ClassSession:
    session_id: str = ""
    course_id: str = ""
    session_number: int = 0
    date: str = ""
    title: str = ""
    material_refs: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    knowledge_point_refs: list[str] = field(default_factory=list)
    verification_refs: list[str] = field(default_factory=list)
    def __post_init__(self) -> None:
        if not self.session_id:
            self.session_id = self._generate_stable_id()
        if isinstance(self.session_number, str):
            try:
                self.session_number = int(self.session_number)
            except ValueError:
                self.session_number = 0
    def _generate_stable_id(self) -> str:
        if self.course_id and self.session_number > 0:
            raw = f"{self.course_id}-{self.session_number:04d}".encode("utf-8")
        else:
            raw = f"{self.course_id}-{self.date}-{len(self.material_refs)}".encode("utf-8")
        return "session-" + hashlib.sha256(raw).hexdigest()[:16]
    def to_dict(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "course_id": self.course_id, "session_number": self.session_number, "date": self.date, "title": self.title, "material_refs": self.material_refs, "evidence_refs": self.evidence_refs, "knowledge_point_refs": self.knowledge_point_refs, "verification_refs": self.verification_refs}
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClassSession:
        return cls(session_id=data["session_id"], course_id=data["course_id"], session_number=data.get("session_number", 0), date=data.get("date", ""), title=data.get("title", ""), material_refs=data.get("material_refs", []), evidence_refs=data.get("evidence_refs", []), knowledge_point_refs=data.get("knowledge_point_refs", []), verification_refs=data.get("verification_refs", []))

@dataclass
class VerificationItem:
    verification_id: str = ""
    description: str = ""
    reason: str = ""
    related_evidence_refs: list[str] = field(default_factory=list)
    related_knowledge_refs: list[str] = field(default_factory=list)
    status: VerificationStatus = VerificationStatus.PENDING
    def __post_init__(self) -> None:
        if not self.verification_id: self.verification_id = str(uuid.uuid4())
        if isinstance(self.status, str): self.status = VerificationStatus.from_string(self.status)
    def to_dict(self) -> dict[str, Any]:
        return {"verification_id": self.verification_id, "description": self.description, "reason": self.reason, "related_evidence_refs": self.related_evidence_refs, "related_knowledge_refs": self.related_knowledge_refs, "status": self.status.value}
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationItem:
        return cls(verification_id=data["verification_id"], description=data["description"], reason=data["reason"], related_evidence_refs=data.get("related_evidence_refs", []), related_knowledge_refs=data.get("related_knowledge_refs", []), status=data.get("status", VerificationStatus.PENDING))

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
    language: TranscriptLanguage = field(default_factory=lambda: TranscriptLanguage.UNKNOWN)
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
    language: TranscriptLanguage = field(default_factory=lambda: TranscriptLanguage.UNKNOWN)
    segments: list[TranscriptSegment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    def __post_init__(self) -> None:
        if isinstance(self.language, str): self.language = TranscriptLanguage.from_string(self.language)
        if not self.transcript_id: self.transcript_id = self._generate_stable_id()
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
        # Task 45 修复的真实缺陷: 这里原本写的是 "\\n" (字面反斜杠 + n), 而不是
        # "\n" (换行)。后果是三重的, 而且都不显眼:
        #   1) 证据正文里出现的是转义序列本身 —— 界面上用户会看到 "\n" 字符;
        #   2) 领域层用**真实换行**来切分文本 (见 knowledge_pipeline._entity_anchor
        #      的 content.split("\n", 1)[0]), 文档路径产出的正是真实换行, 只有
        #      转写路径不是 —— 两条路径不一致;
        #   3) 顺序关系抽取 (integration._extract_order_relations) 的正则里 "."
        #      不匹配换行, 于是整段转写被当成**一行**, 左右实体各自吞掉半篇讲稿,
        #      教师与学生的顺序矛盾因此**检测不出来**。
        # 用真实课堂数据跑验收 (Task 45) 时这三条同时暴露, 所以在这里修掉。
        content = "\n".join(seg.text for seg in self.segments if seg.text)
        source_ref = SourceReference(material_id=self.material_id, timestamp_start=self.segments[0].start if self.segments else None, timestamp_end=self.segments[-1].end if self.segments else None)
        return Evidence(content=content, language=self.language, source_reference=source_ref, confidence=Confidence.HIGH if self.segments and all(s.confidence>0.5 for s in self.segments) else Confidence.MEDIUM, evidence_type=EvidenceType.TRANSCRIPT, metadata={"transcript_id": self.transcript_id, "segment_count": len(self.segments)})

# ============================================================
# OCR / Board Photo Layer Models (Task 11)
# ============================================================

@dataclass
class BoundingBox:
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    def __post_init__(self) -> None:
        if self.x < 0:
            raise ValueError(f'BoundingBox x cannot be negative, got {self.x}')
        if self.y < 0:
            raise ValueError(f'BoundingBox y cannot be negative, got {self.y}')
        if self.width < 0:
            raise ValueError(f'BoundingBox width cannot be negative, got {self.width}')
        if self.height < 0:
            raise ValueError(f'BoundingBox height cannot be negative, got {self.height}')

    def to_dict(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BoundingBox:
        return cls(
            x=data.get("x", 0.0),
            y=data.get("y", 0.0),
            width=data.get("width", 0.0),
            height=data.get("height", 0.0),
        )


class OCRLanguage(str, Enum):
    SPANISH = "Spanish"
    CATALAN = "Catalan"
    CHINESE = "Chinese"
    ENGLISH = "English"
    UNKNOWN = "Unknown"

    @classmethod
    def from_string(cls, value: str) -> OCRLanguage:
        for lang in cls:
            if lang.value.lower() == value.strip().lower():
                return lang
        return cls.UNKNOWN


@dataclass
class OCRSegment:
    text: str = ""
    bounding_box: BoundingBox = field(default_factory=BoundingBox)
    confidence: float = 0.0
    language: OCRLanguage = field(default_factory=lambda: OCRLanguage.UNKNOWN)
    page: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            self.text = ""
        if self.confidence < 0:
            self.confidence = 0.0
        if self.confidence > 1:
            self.confidence = 1.0
        if isinstance(self.language, str):
            self.language = OCRLanguage.from_string(self.language)
        if isinstance(self.bounding_box, dict):
            self.bounding_box = BoundingBox.from_dict(self.bounding_box)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "bounding_box": self.bounding_box.to_dict(),
            "confidence": self.confidence,
            "language": self.language.value,
            "page": self.page,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OCRSegment:
        lang = data.get("language", OCRLanguage.UNKNOWN)
        if isinstance(lang, str):
            lang = OCRLanguage.from_string(lang)
        bb = data.get("bounding_box", {})
        if isinstance(bb, dict):
            bb = BoundingBox.from_dict(bb)
        return cls(
            text=data.get("text", ""),
            bounding_box=bb,
            confidence=data.get("confidence", 0.0),
            language=lang,
            page=data.get("page"),
        )


@dataclass
class OCRResult:
    ocr_result_id: str = ""
    material_id: str = ""
    language: OCRLanguage = field(default_factory=lambda: OCRLanguage.UNKNOWN)
    segments: list[OCRSegment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.language, str):
            self.language = OCRLanguage.from_string(self.language)
        validated: list[OCRSegment] = []
        for seg in self.segments:
            if isinstance(seg, dict):
                seg = OCRSegment.from_dict(seg)
            validated.append(seg)
        self.segments = validated
        if not self.ocr_result_id:
            self.ocr_result_id = self._generate_stable_id()

    def _generate_stable_id(self) -> str:
        raw = (self.material_id + self.language.value).encode("utf-8")
        for seg in self.segments:
            raw += seg.text.encode("utf-8")
        return "ocr-" + hashlib.sha256(raw).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ocr_result_id": self.ocr_result_id,
            "material_id": self.material_id,
            "language": self.language.value,
            "segments": [s.to_dict() for s in self.segments],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OCRResult:
        lang = data.get("language", OCRLanguage.UNKNOWN)
        if isinstance(lang, str):
            lang = OCRLanguage.from_string(lang)
        return cls(
            ocr_result_id=data.get("ocr_result_id", ""),
            material_id=data["material_id"],
            language=lang,
            segments=data.get("segments", []),
            metadata=data.get("metadata", {}),
        )
