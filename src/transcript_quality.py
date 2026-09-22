"""ASR quality and transcript validation layer (Task 20).

Independent quality-diagnostics layer on top of the Task 18/19
transcription pipeline:

    AudioInput
        |
    LongAudioProcessor
        |
    TranscriptionResult
        |
    TranscriptQualityValidator
        |
    QualityReport
        |
    (future) Evidence / Knowledge

Scope (Task 20 only) - check and report, never repair:
    - Structural integrity of the ASR output (segments, timestamps,
      text)
    - Timestamp validation (non-finite, negative, end < start,
      out-of-bounds vs source duration, out-of-order)
    - Gap / overlap / duplicate / empty-text / repetitive-text
      diagnostics
    - Deterministic quality score in [0.0, 1.0] with documented,
      observable (not ground-truth) semantics
    - Structured, stable-ordered QualityReport

Explicitly NOT in scope (and never done by this module):
    - modifying, sorting, deduplicating or repairing the transcript
    - automatic re-transcription, retry, or re-chunking
    - language identification / language-correctness checking
    - LLM, NLP, embedding, or semantic evaluation
    - Evidence / KnowledgePoint / Review generation
    - VAD, speaker diarization
    - re-opening or decoding the audio file

The quality score measures machine-observable output quality
(timestamp validity, structure, text sanity), NOT real ASR accuracy:
without a human ground-truth transcript, no system can claim to know
whether Whisper heard the teacher correctly.

Stdlib only; zero new third-party dependencies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.models import Transcript


class QualityStatus(str, Enum):
    """High-level outcome of transcript quality validation."""

    VALID = "VALID"
    WARNING = "WARNING"
    INVALID = "INVALID"

    @classmethod
    def from_string(cls, value: str) -> "QualityStatus":
        for s in cls:
            if s.value.lower() == str(value).strip().lower():
                return s
        return cls.INVALID


class QualitySeverity(str, Enum):
    """Severity of a single QualityIssue (single project-wide style)."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"

    @classmethod
    def from_string(cls, value: str) -> "QualitySeverity":
        for s in cls:
            if s.value.lower() == str(value).strip().lower():
                return s
        return cls.INFO


class QualityIssueCode(str, Enum):
    """Stable, system-independent issue codes."""

    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    TIMESTAMP_OUT_OF_BOUNDS = "TIMESTAMP_OUT_OF_BOUNDS"
    SEGMENTS_OUT_OF_ORDER = "SEGMENTS_OUT_OF_ORDER"
    EMPTY_TRANSCRIPT = "EMPTY_TRANSCRIPT"
    EMPTY_SEGMENT_TEXT = "EMPTY_SEGMENT_TEXT"
    HIGH_EMPTY_SEGMENT_RATIO = "HIGH_EMPTY_SEGMENT_RATIO"
    LONG_GAP = "LONG_GAP"
    OVERLAPPING_SEGMENTS = "OVERLAPPING_SEGMENTS"
    DUPLICATE_TEXT = "DUPLICATE_TEXT"
    REPETITIVE_TEXT = "REPETITIVE_TEXT"
    VERY_SHORT_SEGMENT = "VERY_SHORT_SEGMENT"
    VERY_LONG_SEGMENT = "VERY_LONG_SEGMENT"
    CHUNK_METADATA_INVALID = "CHUNK_METADATA_INVALID"


def _is_invalid_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return True
    return not math.isfinite(float(value))


@dataclass
class QualityIssue:
    """One structured diagnostic.  All fields are deterministic.

    segment_index / start_time / end_time are None when the issue is
    transcript-level rather than tied to a single segment.
    """

    code: QualityIssueCode
    severity: QualitySeverity
    message: str
    segment_index: Optional[int] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "message": self.message,
            "segment_index": self.segment_index,
            "start_time": self.start_time,
            "end_time": self.end_time,
        }


@dataclass
class QualityStatistics:
    """Observable, O(n) statistics computed from the transcript.

    Durations contributed by segments with invalid (non-finite or
    reversed) timestamps are excluded from the aggregates;
    invalid_timestamp_segment_count records how many segments were
    skipped for that reason.
    """

    segment_count: int = 0
    non_empty_segment_count: int = 0
    empty_segment_count: int = 0
    total_text_characters: int = 0
    total_transcript_duration: float = 0.0
    covered_duration: float = 0.0
    gap_duration: float = 0.0
    overlap_duration: float = 0.0
    duplicate_segment_count: int = 0
    invalid_timestamp_segment_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "segment_count": self.segment_count,
            "non_empty_segment_count": self.non_empty_segment_count,
            "empty_segment_count": self.empty_segment_count,
            "total_text_characters": self.total_text_characters,
            "total_transcript_duration": round(
                self.total_transcript_duration, 6),
            "covered_duration": round(self.covered_duration, 6),
            "gap_duration": round(self.gap_duration, 6),
            "overlap_duration": round(self.overlap_duration, 6),
            "duplicate_segment_count": self.duplicate_segment_count,
            "invalid_timestamp_segment_count": (
                self.invalid_timestamp_segment_count),
        }


@dataclass
class QualityReport:
    """Structured result of TranscriptQualityValidator.

    quality_score is in [0.0, 1.0] and is deterministic for a given
    input; it expresses machine-observable structural quality, NOT
    ASR accuracy.  1.0 means no observable issue was detected.
    """

    status: QualityStatus
    quality_score: float
    issues: Tuple[QualityIssue, ...]
    warnings: Tuple[QualityIssue, ...]
    statistics: QualityStatistics
    duration_check_available: bool
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return self.status is QualityStatus.VALID

    @property
    def is_warning(self) -> bool:
        return self.status is QualityStatus.WARNING

    @property
    def is_invalid(self) -> bool:
        return self.status is QualityStatus.INVALID

    def issues_with_code(self, code: QualityIssueCode) -> Tuple[QualityIssue, ...]:
        return tuple(i for i in self.issues if i.code is code)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "quality_score": round(self.quality_score, 6),
            "issues": [i.to_dict() for i in self.issues],
            "warnings": [i.to_dict() for i in self.warnings],
            "statistics": self.statistics.to_dict(),
            "duration_check_available": self.duration_check_available,
            "metadata": dict(self.metadata),
        }

@dataclass
class TranscriptQualityConfig:
    """Documented heuristic thresholds (single source of truth).

    Every threshold is a named constant so behaviour is deterministic
    and testable.  These are heuristics, not claims about ASR
    accuracy.
    """

    WARNING_GAP_SECONDS: float = 5.0
    OVERLAP_TOLERANCE_SECONDS: float = 1.0
    HIGH_EMPTY_SEGMENT_RATIO: float = 0.5
    VERY_SHORT_SEGMENT_SECONDS: float = 0.1
    VERY_LONG_SEGMENT_SECONDS: float = 60.0
    VERY_SHORT_SEGMENT_RATIO: float = 0.5
    REPETITIVE_MIN_CHAR_REPEAT: int = 40
    PENALTY_ERROR: float = 0.25
    PENALTY_WARNING: float = 0.05
    EMPTY_RATIO_PENALTY: float = 0.10
    PENALTY_CAP: float = 0.85

    def to_dict(self) -> Dict[str, Any]:
        return {
            "warning_gap_seconds": self.WARNING_GAP_SECONDS,
            "overlap_tolerance_seconds": self.OVERLAP_TOLERANCE_SECONDS,
            "high_empty_segment_ratio": self.HIGH_EMPTY_SEGMENT_RATIO,
            "very_short_segment_seconds": self.VERY_SHORT_SEGMENT_SECONDS,
            "very_long_segment_seconds": self.VERY_LONG_SEGMENT_SECONDS,
            "very_short_segment_ratio": self.VERY_SHORT_SEGMENT_RATIO,
            "repetitive_min_char_repeat": self.REPETITIVE_MIN_CHAR_REPEAT,
            "penalty_error": self.PENALTY_ERROR,
            "penalty_warning": self.PENALTY_WARNING,
            "empty_ratio_penalty": self.EMPTY_RATIO_PENALTY,
            "penalty_cap": self.PENALTY_CAP,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TranscriptQualityConfig":
        defaults = cls()
        return cls(
            WARNING_GAP_SECONDS=float(data.get(
                "warning_gap_seconds", defaults.WARNING_GAP_SECONDS)),
            OVERLAP_TOLERANCE_SECONDS=float(data.get(
                "overlap_tolerance_seconds",
                defaults.OVERLAP_TOLERANCE_SECONDS)),
            HIGH_EMPTY_SEGMENT_RATIO=float(data.get(
                "high_empty_segment_ratio",
                defaults.HIGH_EMPTY_SEGMENT_RATIO)),
            VERY_SHORT_SEGMENT_SECONDS=float(data.get(
                "very_short_segment_seconds",
                defaults.VERY_SHORT_SEGMENT_SECONDS)),
            VERY_LONG_SEGMENT_SECONDS=float(data.get(
                "very_long_segment_seconds",
                defaults.VERY_LONG_SEGMENT_SECONDS)),
            VERY_SHORT_SEGMENT_RATIO=float(data.get(
                "very_short_segment_ratio",
                defaults.VERY_SHORT_SEGMENT_RATIO)),
            REPETITIVE_MIN_CHAR_REPEAT=int(data.get(
                "repetitive_min_char_repeat",
                defaults.REPETITIVE_MIN_CHAR_REPEAT)),
            PENALTY_ERROR=float(data.get(
                "penalty_error", defaults.PENALTY_ERROR)),
            PENALTY_WARNING=float(data.get(
                "penalty_warning", defaults.PENALTY_WARNING)),
            EMPTY_RATIO_PENALTY=float(data.get(
                "empty_ratio_penalty", defaults.EMPTY_RATIO_PENALTY)),
            PENALTY_CAP=float(data.get(
                "penalty_cap", defaults.PENALTY_CAP)),
        )


class TranscriptQualityValidator:
    """Deterministic, read-only quality diagnostics for a
    TranscriptionResult.

    Usage::

        validator = TranscriptQualityValidator()
        report = validator.validate(
            transcription,        # TranscriptionResult or Transcript
            source_duration=10.0,
        )

    Guarantees:
      - the input (and its Transcript.segments) is never mutated;
      - the same input always yields an equal report, with issues in a
        stable order;
      - no audio I/O, no ASR call, no network, no LLM, no NLP.

    Status semantics:
      - VALID:   no structural problems detected.
      - WARNING: usable, but observable quality issues exist.
      - INVALID: structural problems present; do not feed directly to
        downstream Evidence processing.
    """

    def __init__(self,
                 config: Optional[TranscriptQualityConfig] = None,
                 source_duration: Optional[float] = None) -> None:
        self.config = config or TranscriptQualityConfig()
        self.source_duration = self._coerce_duration(source_duration)

    @staticmethod
    def _coerce_duration(value: Optional[float]) -> Optional[float]:
        """Reject non-finite / negative durations up front.

        None means "duration unknown": the out-of-bounds check is
        skipped and recorded as such; it is NOT treated as a failure.
        """
        if value is None:
            return None
        if _is_invalid_number(value):
            raise ValueError(
                "source_duration must be a finite number, got %r"
                % (value,))
        value = float(value)
        if value < 0.0:
            raise ValueError(
                "source_duration must be >= 0, got %r" % (value,))
        return value

    def validate(
        self,
        transcription: Any,
        source_duration: Optional[float] = None,
    ) -> QualityReport:
        """Validate one transcription and return a QualityReport.

        transcription may be a TranscriptionResult (Task 18/19
        output) or a raw Transcript.  source_duration (optional,
        seconds) enables the out-of-bounds check; when omitted the
        check is skipped and the report records
        duration_check_available = False.
        """
        transcript = self._as_transcript(transcription)

        if source_duration is not None:
            duration = self._coerce_duration(source_duration)
        else:
            duration = self.source_duration

        issues: List[QualityIssue] = []
        stats = QualityStatistics()

        segments: Sequence[Any] = (
            transcript.segments if transcript is not None else [])
        stats.segment_count = len(segments)

        if transcript is None or len(segments) == 0:
            issues.append(QualityIssue(
                code=QualityIssueCode.EMPTY_TRANSCRIPT,
                severity=QualitySeverity.WARNING,
                message="Transcript contains no segments; this is a "
                        "legal result (e.g. silent audio) but "
                        "downstream evidence will have no content.",
            ))
            return self._build_report(issues, stats, duration, transcript)

        valid_mask: List[bool] = []
        for idx, seg in enumerate(segments):
            seg_ok = True
            start_bad = _is_invalid_number(seg.start)
            end_bad = _is_invalid_number(seg.end)
            if start_bad or end_bad:
                issues.append(QualityIssue(
                    code=QualityIssueCode.INVALID_TIMESTAMP,
                    severity=QualitySeverity.ERROR,
                    message="Segment %d has a non-finite or missing "
                            "timestamp (start=%r, end=%r)."
                            % (idx, seg.start, seg.end),
                    segment_index=idx,
                    start_time=None if start_bad else float(seg.start),
                    end_time=None if end_bad else float(seg.end),
                ))
                seg_ok = False
            elif float(seg.start) < 0.0 or float(seg.end) < 0.0:
                issues.append(QualityIssue(
                    code=QualityIssueCode.INVALID_TIMESTAMP,
                    severity=QualitySeverity.ERROR,
                    message="Segment %d has a negative timestamp "
                            "(start=%s, end=%s)."
                            % (idx, seg.start, seg.end),
                    segment_index=idx,
                    start_time=float(seg.start),
                    end_time=float(seg.end),
                ))
                seg_ok = False
            elif float(seg.end) < float(seg.start):
                issues.append(QualityIssue(
                    code=QualityIssueCode.INVALID_TIMESTAMP,
                    severity=QualitySeverity.ERROR,
                    message="Segment %d ends before it starts "
                            "(start=%s, end=%s)."
                            % (idx, seg.start, seg.end),
                    segment_index=idx,
                    start_time=float(seg.start),
                    end_time=float(seg.end),
                ))
                seg_ok = False
            valid_mask.append(seg_ok)
        stats.invalid_timestamp_segment_count = (
            len(segments) - sum(1 for ok in valid_mask if ok))

        duration_check = duration is not None
        if duration_check:
            for idx, seg in enumerate(segments):
                if not valid_mask[idx]:
                    continue
                end = float(seg.end)
                if end > float(duration) + 1e-9:
                    issues.append(QualityIssue(
                        code=QualityIssueCode.TIMESTAMP_OUT_OF_BOUNDS,
                        severity=QualitySeverity.ERROR,
                        message="Segment %d ends at %s, beyond source "
                                "duration %s."
                        % (idx, seg.end, duration),
                        segment_index=idx,
                        start_time=float(seg.start),
                        end_time=end,
                    ))

        total_overlap = 0.0
        total_gap = 0.0
        out_of_order_count = 0
        prev_valid = -1
        for idx in range(len(segments)):
            if not valid_mask[idx]:
                continue
            if prev_valid >= 0:
                prev = segments[prev_valid]
                cur = segments[idx]
                if float(cur.start) < float(prev.start):
                    out_of_order_count += 1
                if float(cur.start) > float(prev.end):
                    total_gap += float(cur.start) - float(prev.end)
                else:
                    total_overlap += max(0.0,
                                        float(prev.end) - float(cur.start))
            prev_valid = idx

        if out_of_order_count > 0:
            issues.append(QualityIssue(
                code=QualityIssueCode.SEGMENTS_OUT_OF_ORDER,
                severity=QualitySeverity.ERROR,
                message="%d adjacent segment pair(s) start earlier than "
                        "the previous segment; the transcript is out "
                        "of chronological order. No re-ordering "
                        "performed." % out_of_order_count,
            ))

        if total_gap > 0.0:
            if total_gap > self.config.WARNING_GAP_SECONDS:
                issues.append(QualityIssue(
                    code=QualityIssueCode.LONG_GAP,
                    severity=QualitySeverity.WARNING,
                    message="Total gap between segments is %.3fs, "
                            "above the %.3fs heuristic threshold. "
                            "Gaps may be normal pauses; no automatic "
                            "repair is performed."
                    % (total_gap, self.config.WARNING_GAP_SECONDS),
                ))
            else:
                issues.append(QualityIssue(
                    code=QualityIssueCode.LONG_GAP,
                    severity=QualitySeverity.INFO,
                    message="Total gap between segments is %.3fs, "
                            "within the %.3fs heuristic tolerance."
                    % (total_gap, self.config.WARNING_GAP_SECONDS),
                ))

        if total_overlap > 0.0:
            if total_overlap > self.config.OVERLAP_TOLERANCE_SECONDS:
                issues.append(QualityIssue(
                    code=QualityIssueCode.OVERLAPPING_SEGMENTS,
                    severity=QualitySeverity.WARNING,
                    message="Total overlap between adjacent segments is "
                            "%.3fs, above the %.3fs tolerance. No "
                            "segments were removed."
                    % (total_overlap,
                       self.config.OVERLAP_TOLERANCE_SECONDS),
                ))
            else:
                issues.append(QualityIssue(
                    code=QualityIssueCode.OVERLAPPING_SEGMENTS,
                    severity=QualitySeverity.INFO,
                    message="Total overlap between adjacent segments is "
                            "%.3fs, within the %.3fs tolerance."
                    % (total_overlap,
                       self.config.OVERLAP_TOLERANCE_SECONDS),
                ))

        empty_count = 0
        duplicate_count = 0
        short_count = 0
        total_chars = 0
        covered = 0.0
        min_start: Optional[float] = None
        max_end: Optional[float] = None
        prev_kept_norm: Optional[str] = None

        for idx, seg in enumerate(segments):
            text = seg.text if isinstance(seg.text, str) else ""
            norm = self._normalize_text(text)
            total_chars += len(norm)
            seg_start = float(seg.start) if valid_mask[idx] else None
            seg_end = float(seg.end) if valid_mask[idx] else None
            if norm == "":
                empty_count += 1
                issues.append(QualityIssue(
                    code=QualityIssueCode.EMPTY_SEGMENT_TEXT,
                    severity=QualitySeverity.ERROR,
                    message="Segment %d has empty or whitespace-only "
                            "text." % idx,
                    segment_index=idx,
                    start_time=seg_start,
                    end_time=seg_end,
                ))
            elif norm == prev_kept_norm:
                duplicate_count += 1
                issues.append(QualityIssue(
                    code=QualityIssueCode.DUPLICATE_TEXT,
                    severity=QualitySeverity.WARNING,
                    message="Segment %d has the same text as the "
                            "previous non-empty segment (exact "
                            "duplicate after whitespace trimming; the "
                            "speaker may genuinely have repeated the "
                            "phrase). No segments were removed." % idx,
                    segment_index=idx,
                    start_time=seg_start,
                    end_time=seg_end,
                ))
            else:
                prev_kept_norm = norm
                compressed = self._collapse_repeats(norm)
                if (len(compressed) == 1 and len(norm)
                        >= self.config.REPETITIVE_MIN_CHAR_REPEAT):
                    issues.append(QualityIssue(
                        code=QualityIssueCode.REPETITIVE_TEXT,
                        severity=QualitySeverity.WARNING,
                        message="Segment %d is a single character "
                                "repeated %d times (possible ASR "
                                "glitch). No repair performed."
                                % (idx, len(norm)),
                        segment_index=idx,
                        start_time=seg_start,
                        end_time=seg_end,
                    ))
            if valid_mask[idx]:
                dur = float(seg.end) - float(seg.start)
                covered += dur
                if min_start is None or float(seg.start) < min_start:
                    min_start = float(seg.start)
                if max_end is None or float(seg.end) > max_end:
                    max_end = float(seg.end)
                if dur > self.config.VERY_LONG_SEGMENT_SECONDS:
                    issues.append(QualityIssue(
                        code=QualityIssueCode.VERY_LONG_SEGMENT,
                        severity=QualitySeverity.WARNING,
                        message="Segment %d lasts %.3fs, above the "
                                "%.0fs heuristic. No automatic "
                                "splitting performed."
                        % (idx, dur,
                           self.config.VERY_LONG_SEGMENT_SECONDS),
                        segment_index=idx,
                        start_time=float(seg.start),
                        end_time=float(seg.end),
                    ))
                elif 0.0 < dur < self.config.VERY_SHORT_SEGMENT_SECONDS:
                    short_count += 1

        if short_count > 0:
            share = short_count / stats.segment_count
            if share >= self.config.VERY_SHORT_SEGMENT_RATIO:
                issues.append(QualityIssue(
                    code=QualityIssueCode.VERY_SHORT_SEGMENT,
                    severity=QualitySeverity.WARNING,
                    message="%d of %d segments are shorter than %.3fs; "
                            "this may indicate ASR over-segmentation."
                    % (short_count, stats.segment_count,
                       self.config.VERY_SHORT_SEGMENT_SECONDS),
                ))
            else:
                issues.append(QualityIssue(
                    code=QualityIssueCode.VERY_SHORT_SEGMENT,
                    severity=QualitySeverity.INFO,
                    message="%d segment(s) shorter than %.3fs (minor "
                            "heuristic observation)."
                    % (short_count,
                       self.config.VERY_SHORT_SEGMENT_SECONDS),
                ))

        stats.non_empty_segment_count = stats.segment_count - empty_count
        stats.empty_segment_count = empty_count
        stats.total_text_characters = total_chars
        stats.duplicate_segment_count = duplicate_count
        stats.gap_duration = total_gap
        stats.overlap_duration = total_overlap
        if min_start is not None and max_end is not None:
            stats.total_transcript_duration = max(0.0, max_end - min_start)
        stats.covered_duration = covered

        if empty_count > 0:
            ratio = empty_count / stats.segment_count
            if ratio > self.config.HIGH_EMPTY_SEGMENT_RATIO:
                issues.append(QualityIssue(
                    code=QualityIssueCode.HIGH_EMPTY_SEGMENT_RATIO,
                    severity=QualitySeverity.ERROR,
                    message="%.1f%% of segments have empty text; the "
                            "transcript structure is unreliable."
                            % (ratio * 100.0),
                ))

        md = getattr(transcript, "metadata", None) or {}
        if "chunk_count" in md:
            cc = md.get("chunk_count")
            if not isinstance(cc, int) or isinstance(cc, bool) or cc < 1:
                issues.append(QualityIssue(
                    code=QualityIssueCode.CHUNK_METADATA_INVALID,
                    severity=QualitySeverity.WARNING,
                    message="Transcript metadata declares "
                            "chunk_count=%r, expected an integer "
                            ">= 1." % (cc,),
                ))

        issues.sort(key=lambda i: (
            i.segment_index if i.segment_index is not None else -1,
            i.start_time if i.start_time is not None else 0.0,
            i.code.value,
        ))

        return self._build_report(issues, stats, duration, transcript)

    @staticmethod
    def _as_transcript(transcription: Any) -> Optional[Transcript]:
        if transcription is None:
            return None
        if isinstance(transcription, Transcript):
            return transcription
        # Duck-typed TranscriptionResult (Task 18/19 output): it always
        # carries a "transcript" attribute which may be None (a failed
        # or empty provider result) -> treat as an empty transcript,
        # which is a legal validation input, not an error.
        if hasattr(transcription, "transcript") and hasattr(
                transcription, "errors"):
            return transcription.transcript
        raise TypeError(
            "validate() expects a TranscriptionResult or a Transcript, "
            "got %s" % type(transcription).__name__)

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Whitespace-trimmed normalization for duplicate detection.

        Unicode-safe: no transliteration, no ASCII folding.  The
        original text is never modified.
        """
        if text is None:
            return ""
        return str(text).strip()

    @staticmethod
    def _collapse_repeats(norm: str) -> str:
        """Collapse runs of identical characters; O(n), read-only."""
        out = []
        prev = None
        for ch in norm:
            if ch != prev:
                out.append(ch)
            prev = ch
        return "".join(out)

    def _build_report(
        self,
        issues: List[QualityIssue],
        stats: QualityStatistics,
        duration: Optional[float],
        transcript: Optional[Transcript],
    ) -> QualityReport:
        warnings = tuple(
            i for i in issues if i.severity is QualitySeverity.WARNING)
        errors = [i for i in issues
                  if i.severity is QualitySeverity.ERROR]

        if errors:
            status = QualityStatus.INVALID
        elif warnings:
            status = QualityStatus.WARNING
        else:
            status = QualityStatus.VALID

        # Deterministic score: 1.0 with no observable issue.  The
        # score measures machine-observable structural quality only;
        # it is explicitly NOT an ASR accuracy estimate.
        score = 1.0
        score -= len(errors) * self.config.PENALTY_ERROR
        warn_codes = sorted({i.code for i in warnings})
        score -= len(warn_codes) * self.config.PENALTY_WARNING
        if stats.empty_segment_count > 0:
            ratio = (stats.empty_segment_count
                     / max(1, stats.segment_count))
            if ratio > self.config.HIGH_EMPTY_SEGMENT_RATIO:
                score -= self.config.EMPTY_RATIO_PENALTY
        floor = 1.0 - self.config.PENALTY_CAP
        score = round(max(floor, min(1.0, score)), 6)

        return QualityReport(
            status=status,
            quality_score=score,
            issues=tuple(issues),
            warnings=warnings,
            statistics=stats,
            duration_check_available=duration is not None,
            metadata={
                "material_id": (transcript.material_id
                                if transcript is not None else ""),
                "transcript_id": (transcript.transcript_id
                                  if transcript is not None else ""),
                "language": (transcript.language.value
                             if transcript is not None else "Unknown"),
                "thresholds": self.config.to_dict(),
                "score_meaning": (
                    "Deterministic 0-1 measure of machine-observable "
                    "structural quality (timestamp validity, "
                    "structure, text sanity). It is NOT ASR accuracy "
                    "and makes no ground-truth claim."),
            },
        )


def create_transcript_quality_validator(
        config: Optional[TranscriptQualityConfig] = None,
        source_duration: Optional[float] = None,
) -> TranscriptQualityValidator:
    """Mirror of the project's provider factories."""
    return TranscriptQualityValidator(
        config=config, source_duration=source_duration)
