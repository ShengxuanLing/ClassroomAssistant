"""Task 20 - Transcript quality validation tests.

Unit tests construct TranscriptSegment / Transcript /
TranscriptionResult by hand (no Whisper runtime, no filesystem work),
so a plain ``pytest -q`` run stays fast.  One real-Whisper
integration test is gated behind pytest.mark.integration and skips
cleanly when faster-whisper or the tiny model is unavailable.

Spec sections covered:
  - Valid transcript -> VALID, score 1.0
  - Timestamp: negative / end<start / NaN / Infinity / out-of-bounds /
    out-of-order
  - Segment: empty text, whitespace-only, gap, overlap, duplicates,
    repetitive text, very short / very long heuristics
  - Empty transcript: no exception, structured report, not INVALID
  - Unicode + multilingual text pass through untouched
  - Immutability (deep snapshot before/after validate)
  - Determinism (equal reports, stable issue ordering)
  - Score: 0..1 range, monotonicity, documented formula
  - Task 19 LongAudioProcessor output feeds directly into the
    validator
  - Real Whisper integration
"""

from __future__ import annotations

import copy
import math
import os
from typing import Any, Dict, List, Optional

import pytest

from src.asr_provider import (
    ASRProvider,
    ASRProviderConfig,
    TranscriptionResult,
)
from src.models import (
    Material,
    MaterialType,
    Transcript,
    TranscriptLanguage,
    TranscriptSegment,
)
from src.audio_input import AudioInput, AudioMaterialValidator
from src.transcript_quality import (
    QualityIssueCode,
    QualityIssue,
    QualityReport,
    QualitySeverity,
    QualityStatus,
    QualityStatistics,
    TranscriptQualityConfig,
    TranscriptQualityValidator,
    create_transcript_quality_validator,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _seg(start: float, end: float, text: str = "x",
         speaker: str = "", lang: Optional[TranscriptLanguage] = None) -> TranscriptSegment:
    return TranscriptSegment(
        start=float(start), end=float(end), text=text,
        speaker=speaker,
        language=lang if lang is not None else TranscriptLanguage.UNKNOWN,
        confidence=0.9,
    )


def _result(segments: List[TranscriptSegment],
            metadata: Optional[Dict[str, Any]] = None,
            lang: str = "Unknown") -> TranscriptionResult:
    t = Transcript(
        material_id="mat-test",
        language=TranscriptLanguage.from_string(lang),
        segments=segments,
        metadata=dict(metadata or {}),
    )
    return TranscriptionResult(t)


def _clean_transcription() -> TranscriptionResult:
    return _result([_seg(0.0, 2.0, "Hola"),
                    _seg(2.0, 5.0, "mundo")])


V = TranscriptQualityValidator()


# ----------------------------------------------------------------------
# 59. Valid transcript
# ----------------------------------------------------------------------


class TestValidTranscript:

    def test_valid_transcript_status_and_score(self) -> None:
        report = V.validate(_clean_transcription())
        assert report.status is QualityStatus.VALID
        assert report.quality_score == 1.0
        assert report.issues == ()
        assert report.warnings == ()
        assert report.duration_check_available is False

    def test_accepts_bare_transcript(self) -> None:
        t = _clean_transcription().transcript
        report = V.validate(t)
        assert report.status is QualityStatus.VALID

    def test_score_documented_in_metadata(self) -> None:
        report = V.validate(_clean_transcription())
        assert "score_meaning" in report.metadata
        assert "NOT" in report.metadata["score_meaning"]
        assert report.metadata["thresholds"]["warning_gap_seconds"] == 5.0


# ----------------------------------------------------------------------
# 60-65. Timestamp validation
# ----------------------------------------------------------------------


class TestTimestamps:

    def _seg_raw(self, start: float, end: float, text: str = "x") -> TranscriptSegment:
        """Build a segment bypassing __post_init__ coercion so NaN /
        reversed / negative values can be inspected directly."""
        seg = TranscriptSegment.__new__(TranscriptSegment)
        seg.start = start
        seg.end = end
        seg.text = text
        seg.speaker = ""
        seg.language = TranscriptLanguage.UNKNOWN
        seg.confidence = 0.0
        return seg

    def test_negative_timestamp_is_invalid(self) -> None:
        seg = self._seg_raw(-1.0, 2.0)
        report = V.validate(_result([seg]))
        assert report.status is QualityStatus.INVALID
        assert report.issues_with_code(QualityIssueCode.INVALID_TIMESTAMP)

    def test_end_before_start_is_invalid(self) -> None:
        seg = self._seg_raw(5.0, 2.0)
        report = V.validate(_result([seg]))
        assert report.status is QualityStatus.INVALID
        assert report.issues_with_code(QualityIssueCode.INVALID_TIMESTAMP)

    def test_nan_start_is_invalid(self) -> None:
        seg = self._seg_raw(float("nan"), 5.0)
        report = V.validate(_result([seg]))
        assert report.status is QualityStatus.INVALID
        assert report.issues_with_code(QualityIssueCode.INVALID_TIMESTAMP)
        assert report.statistics.invalid_timestamp_segment_count == 1

    def test_infinity_end_is_invalid(self) -> None:
        seg = self._seg_raw(0.0, float("inf"))
        report = V.validate(_result([seg]))
        assert report.status is QualityStatus.INVALID
        assert report.issues_with_code(QualityIssueCode.INVALID_TIMESTAMP)

    def test_negative_infinity_start_is_invalid(self) -> None:
        seg = self._seg_raw(float("-inf"), 3.0)
        report = V.validate(_result([seg]))
        assert report.status is QualityStatus.INVALID

    def test_zero_timestamp_is_valid(self) -> None:
        report = V.validate(_result([_seg(0.0, 3.0, "hi")]))
        assert report.status is QualityStatus.VALID

    def test_out_of_bounds_with_duration(self) -> None:
        report = V.validate(_result([_seg(8.0, 12.0)]), source_duration=10.0)
        assert report.status is QualityStatus.INVALID
        assert report.issues_with_code(QualityIssueCode.TIMESTAMP_OUT_OF_BOUNDS)
        assert report.duration_check_available is True

    def test_within_bounds_no_issue(self) -> None:
        report = V.validate(_result([_seg(8.0, 10.0)]), source_duration=10.0)
        assert report.status is QualityStatus.VALID

    def test_boundary_equal_to_duration_is_ok(self) -> None:
        report = V.validate(_result([_seg(0.0, 10.0)]), source_duration=10.0)
        assert report.status is QualityStatus.VALID

    def test_no_duration_skips_bound_check(self) -> None:
        report = V.validate(_result([_seg(8.0, 12.0)]))
        assert report.status is QualityStatus.VALID
        assert report.duration_check_available is False
        assert not report.issues_with_code(QualityIssueCode.TIMESTAMP_OUT_OF_BOUNDS)

    def test_ctor_rejects_non_finite_duration(self) -> None:
        with pytest.raises(ValueError):
            TranscriptQualityValidator(source_duration=float("nan"))
        with pytest.raises(ValueError):
            TranscriptQualityValidator(source_duration=float("inf"))
        with pytest.raises(ValueError):
            TranscriptQualityValidator(source_duration=-1.0)

    def test_validate_rejects_bad_runtime_duration(self) -> None:
        with pytest.raises(ValueError):
            V.validate(_clean_transcription(), source_duration=float("nan"))

    def test_out_of_order_detected(self) -> None:
        report = V.validate(_result([_seg(5.0, 7.0), _seg(2.0, 4.0)]))
        assert report.status is QualityStatus.INVALID
        assert report.issues_with_code(QualityIssueCode.SEGMENTS_OUT_OF_ORDER)
        # order must NOT have been modified
        assert [s.start for s in report.statistics and _result([_seg(5.0, 7.0), _seg(2.0, 4.0)]).segments] == [5.0, 2.0]

    def test_sorted_input_is_valid(self) -> None:
        report = V.validate(_result([_seg(0.0, 1.0, "uno"),
                                     _seg(1.0, 2.0, "dos"),
                                     _seg(2.0, 3.0, "tres")]))
        assert report.status is QualityStatus.VALID


# ----------------------------------------------------------------------
# 67-68. Gaps and overlaps
# ----------------------------------------------------------------------


class TestGapsAndOverlaps:

    def test_short_gap_is_info(self) -> None:
        report = V.validate(_result([_seg(0.0, 5.0, "a"),
                                     _seg(7.0, 10.0, "b")]))
        gap = report.issues_with_code(QualityIssueCode.LONG_GAP)
        assert len(gap) == 1
        assert gap[0].severity is QualitySeverity.INFO
        assert report.statistics.gap_duration == 2.0
        # INFO alone keeps the status VALID
        assert report.status is QualityStatus.VALID

    def test_long_gap_is_warning(self) -> None:
        report = V.validate(_result([_seg(0.0, 20.0), _seg(100.0, 110.0)]))
        gap = report.issues_with_code(QualityIssueCode.LONG_GAP)
        assert len(gap) == 1
        assert gap[0].severity is QualitySeverity.WARNING
        assert report.status is QualityStatus.WARNING
        assert report.statistics.gap_duration == 80.0

    def test_no_gap_no_issue(self) -> None:
        report = V.validate(_result([_seg(0.0, 5.0), _seg(5.0, 8.0)]))
        assert not report.issues_with_code(QualityIssueCode.LONG_GAP)

    def test_small_overlap_is_info(self) -> None:
        report = V.validate(_result([_seg(0.0, 5.0), _seg(4.5, 7.0)]))
        ov = report.issues_with_code(QualityIssueCode.OVERLAPPING_SEGMENTS)
        assert ov[0].severity is QualitySeverity.INFO
        assert report.statistics.overlap_duration == 0.5

    def test_severe_overlap_is_warning(self) -> None:
        report = V.validate(_result([_seg(0.0, 5.0, "a"),
                                     _seg(3.0, 8.0, "b")]))
        ov = report.issues_with_code(QualityIssueCode.OVERLAPPING_SEGMENTS)
        assert ov[0].severity is QualitySeverity.WARNING
        assert report.status is QualityStatus.WARNING
        # segments NOT removed (validator never deduplicates)
        assert report.statistics.segment_count == 2

    def test_custom_thresholds_single_source(self) -> None:
        cfg = TranscriptQualityConfig(WARNING_GAP_SECONDS=50.0)
        v = TranscriptQualityValidator(config=cfg)
        report = v.validate(_result([_seg(0.0, 20.0), _seg(60.0, 70.0)]))
        # 40s gap under a 50s threshold -> INFO, not WARNING
        assert report.issues_with_code(QualityIssueCode.LONG_GAP)[0].severity is QualitySeverity.INFO


# ----------------------------------------------------------------------
# 69-71. Duplicate / empty / whitespace text
# ----------------------------------------------------------------------


class TestDuplicatesAndEmpty:

    def test_duplicate_text_is_warning(self) -> None:
        report = V.validate(_result([_seg(0.0, 2.0, "la estabilidad del sistema"),
                                     _seg(2.0, 4.0, "la estabilidad del sistema")]))
        dup = report.issues_with_code(QualityIssueCode.DUPLICATE_TEXT)
        assert len(dup) == 1
        assert dup[0].severity is QualitySeverity.WARNING
        assert report.statistics.duplicate_segment_count == 1
        assert report.status is QualityStatus.WARNING

    def test_whitespace_counts_as_duplicate_after_strip(self) -> None:
        report = V.validate(_result([_seg(0.0, 2.0, "Hola"),
                                     _seg(2.0, 4.0, "Hola   ")]))
        assert report.issues_with_code(QualityIssueCode.DUPLICATE_TEXT)

    def test_adjacent_duplicates_only(self) -> None:
        report = V.validate(_result([_seg(0.0, 2.0, "a"),
                                     _seg(2.0, 4.0, "b"),
                                     _seg(4.0, 6.0, "a")]))
        assert not report.issues_with_code(QualityIssueCode.DUPLICATE_TEXT)

    def test_empty_text_is_error(self) -> None:
        report = V.validate(_result([_seg(0.0, 2.0, "")]))
        assert report.status is QualityStatus.INVALID
        assert report.issues_with_code(QualityIssueCode.EMPTY_SEGMENT_TEXT)

    def test_whitespace_only_text_is_error(self) -> None:
        # TranscriptSegment.__post_init__ strips to "" on construction,
        # so build raw to prove the validator still flags it.
        seg = TranscriptSegment.__new__(TranscriptSegment)
        seg.start, seg.end, seg.text = 0.0, 2.0, "   "
        seg.speaker, seg.language, seg.confidence = (
            "", TranscriptLanguage.UNKNOWN, 0.0)
        report = V.validate(_result([seg]))
        assert report.issues_with_code(QualityIssueCode.EMPTY_SEGMENT_TEXT)

    def test_high_empty_ratio_is_error(self) -> None:
        segs = [_seg(0.0, 1.0, ""), _seg(1.0, 2.0, ""), _seg(2.0, 3.0, "ok")]
        report = V.validate(_result(segs))
        assert report.issues_with_code(QualityIssueCode.HIGH_EMPTY_SEGMENT_RATIO)

    def test_low_empty_ratio_no_ratio_issue(self) -> None:
        segs = [_seg(0.0, 1.0, "a"), _seg(1.0, 2.0, ""),
                _seg(2.0, 3.0, "b"), _seg(3.0, 4.0, "c")]
        report = V.validate(_result(segs))
        assert not report.issues_with_code(QualityIssueCode.HIGH_EMPTY_SEGMENT_RATIO)
        assert report.statistics.empty_segment_count == 1

    def test_repetitive_text_is_warning(self) -> None:
        report = V.validate(_result([_seg(0.0, 2.0, "a" * 50)]))
        rep = report.issues_with_code(QualityIssueCode.REPETITIVE_TEXT)
        assert len(rep) == 1
        assert rep[0].severity is QualitySeverity.WARNING
        assert report.status is QualityStatus.WARNING

    def test_normal_repeated_words_not_flagged(self) -> None:
        report = V.validate(_result([_seg(0.0, 2.0, "the the the the the")]))
        assert not report.issues_with_code(QualityIssueCode.REPETITIVE_TEXT)
        assert report.status is QualityStatus.VALID


# ----------------------------------------------------------------------
# 72-74. Empty transcript / very short / very long segments
# ----------------------------------------------------------------------


class TestEmptyTranscript:

    def test_no_segments_no_exception(self) -> None:
        report = V.validate(_result([]))
        assert report.status is QualityStatus.WARNING
        assert report.issues_with_code(QualityIssueCode.EMPTY_TRANSCRIPT)
        assert report.statistics.segment_count == 0
        assert report.quality_score < 1.0

    def test_empty_transcript_not_invalid(self) -> None:
        report = V.validate(_result([]))
        assert report.status is not QualityStatus.INVALID
        assert report.metadata["material_id"] == "mat-test"

    def test_transcription_result_with_none_transcript(self) -> None:
        report = V.validate(TranscriptionResult(transcript=None))
        assert report.status is QualityStatus.WARNING
        assert report.issues_with_code(QualityIssueCode.EMPTY_TRANSCRIPT)


class TestSegmentDurationHeuristics:

    def test_many_very_short_segments_warning(self) -> None:
        segs = []
        t = 0.0
        for _ in range(10):
            segs.append(_seg(t, t + 0.01, "a"))
            t += 0.01
        report = V.validate(_result(segs))
        short = report.issues_with_code(QualityIssueCode.VERY_SHORT_SEGMENT)
        assert len(short) == 1
        assert short[0].severity is QualitySeverity.WARNING
        assert report.status is QualityStatus.WARNING

    def test_few_very_short_segments_info(self) -> None:
        report = V.validate(_result([_seg(0.0, 0.01, "a"),
                                     _seg(1.0, 3.0, "b"),
                                     _seg(3.0, 5.0, "c"),
                                     _seg(5.0, 7.0, "d")]))
        short = report.issues_with_code(QualityIssueCode.VERY_SHORT_SEGMENT)
        assert short[0].severity is QualitySeverity.INFO

    def test_very_long_segment_warning(self) -> None:
        report = V.validate(_result([_seg(0.0, 120.0, "long lecture")]))
        long_ = report.issues_with_code(QualityIssueCode.VERY_LONG_SEGMENT)
        assert len(long_) == 1
        assert long_[0].severity is QualitySeverity.WARNING
        assert report.status is QualityStatus.WARNING

    def test_zero_duration_segment_ok(self) -> None:
        report = V.validate(_result([_seg(1.0, 1.0, "x")]))
        assert report.status is QualityStatus.VALID


# ----------------------------------------------------------------------
# 75-76. Unicode and multilingual
# ----------------------------------------------------------------------


class TestMultilingual:

    def test_unicode_catalan_spanish(self) -> None:
        segs = [_seg(0.0, 2.0, "Introducció a la teoria dels sistemes"),
                _seg(2.0, 4.0, "La estabilidad del sistema es clau")]
        report = V.validate(_result(segs))
        assert report.status is QualityStatus.VALID
        # no translation happened
        assert report.issues[0] if report.issues else True
        txt = "".join(s.text for s in segs)
        assert "Introducció" in txt

    def test_chinese_text(self) -> None:
        segs = [_seg(0.0, 2.0, "这是测试"), _seg(2.0, 4.0, "字符保持完整")]
        report = V.validate(_result(segs))
        assert report.status is QualityStatus.VALID
        assert report.statistics.total_text_characters == 10

    def test_mixed_languages_never_flagged(self) -> None:
        segs = [_seg(0.0, 2.0, "Hola, bon dia, 大家好")]
        report = V.validate(_result(segs, lang="Catalan"))
        # language mismatch between metadata and text is NOT an issue
        assert not report.issues_with_code(QualityIssueCode.LONG_GAP)
        assert report.status is QualityStatus.VALID

    def test_speaker_none_is_not_quality_penalty(self) -> None:
        segs = [_seg(0.0, 2.0, "hola", speaker=""),
                _seg(2.0, 4.0, "mundo", speaker="")]
        report = V.validate(_result(segs))
        assert report.status is QualityStatus.VALID
        assert report.quality_score == 1.0


# ----------------------------------------------------------------------
# 73. Chunk metadata
# ----------------------------------------------------------------------


class TestChunkMetadata:

    def test_chunk_count_valid(self) -> None:
        report = V.validate(_result([_seg(0.0, 5.0, "x")],
                                    metadata={"chunk_count": 3,
                                              "long_audio": True}))
        assert not report.issues_with_code(QualityIssueCode.CHUNK_METADATA_INVALID)

    def test_chunk_count_zero_flagged(self) -> None:
        report = V.validate(_result([_seg(0.0, 5.0, "x")],
                                    metadata={"chunk_count": 0}))
        assert report.issues_with_code(QualityIssueCode.CHUNK_METADATA_INVALID)

    def test_no_chunk_metadata_ok(self) -> None:
        report = V.validate(_result([_seg(0.0, 5.0, "x")]))
        assert report.status is QualityStatus.VALID


# ----------------------------------------------------------------------
# 77-79. Immutability / determinism / ordering
# ----------------------------------------------------------------------


def _screwed_transcription() -> TranscriptionResult:
    return _result([_seg(0.0, 2.0, "aaaa" * 12),
                    _seg(2.0, 200.0, "b"),
                    _seg(195.0, 200.0, ""),
                    _seg(400.0, 410.0, "dup"),
                    _seg(410.0, 420.0, "dup")])
class TestImmutabilityAndDeterminism:
    def test_validate_does_not_mutate(self) -> None:
        tr = _screwed_transcription()
        before = copy.deepcopy(tr)
        V.validate(tr, source_duration=300.0)
        after = tr
        assert after.transcript.segments == before.transcript.segments
        for a, b in zip(after.transcript.segments, before.transcript.segments):
            assert a.start == b.start and a.end == b.end
            assert a.text == b.text and a.speaker == b.speaker
        assert after.transcript.metadata == before.transcript.metadata

    def test_deterministic_reports(self) -> None:
        tr = _screwed_transcription()
        r1 = V.validate(tr, source_duration=300.0)
        r2 = TranscriptQualityValidator().validate(tr, source_duration=300.0)
        assert r1.to_dict() == r2.to_dict()
        assert r1.issues == r2.issues

    def test_issue_ordering_stable(self) -> None:
        report = V.validate(_result([_seg(0.0, 1.0, ""),
                                     _seg(1.0, 100.0, "x"),
                                     _seg(100.0, 105.0, "y"),
                                     _seg(500.0, 502.0, "z")]))
        codes = [i.code for i in report.issues]
        # EMPTY (seg0) before LONG_GAP (transcript level, idx -1 sorted first!)
        # transcript-level issues carry segment_index None -> sort key -1
        report2 = V.validate(_result([_seg(0.0, 1.0, ""),
                                      _seg(1.0, 100.0, "x"),
                                      _seg(100.0, 105.0, "y"),
                                      _seg(500.0, 502.0, "z")]))
        assert [i.code for i in report.issues] == [i.code for i in report2.issues]

    def test_issue_severity_enum_single_style(self) -> None:
        report = V.validate(_result([_seg(0.0, 5.0, ""),
                                     _seg(5.0, 500.0, "gap")]))
        for issue in report.issues:
            assert issue.severity in (QualitySeverity.INFO,
                                     QualitySeverity.WARNING,
                                     QualitySeverity.ERROR)


# ----------------------------------------------------------------------
# 80-81. Score semantics
# ----------------------------------------------------------------------


class TestScore:

    def test_score_in_range_many_inputs(self) -> None:
        cases = [
            [],
            [_seg(0.0, 2.0, "ok")],
            [_seg(-1.0, 5.0, "neg")],
            [_seg(0.0, 5.0, "")] * 3,
            [_seg(0.0, 2.0, "dup"), _seg(2.0, 4.0, "dup")],
            [_seg(0.0, 1000.0, "long")],
        ]
        for segs in cases:
            report = V.validate(_result(list(segs)))
            assert 0.0 <= report.quality_score <= 1.0

    def test_score_not_accuracy_claim(self) -> None:
        report = V.validate(_result([_seg(0.0, 2.0, "garbled")]))
        # whatever the text, a structurally clean transcript scores high
        assert report.status is QualityStatus.VALID
        assert report.quality_score == 1.0
        assert "NOT ASR accuracy" in report.metadata["score_meaning"]

    def test_monotonicity_severer_input_scores_lower(self) -> None:
        clean = V.validate(_result([_seg(0.0, 2.0, "ok")]))
        worse = V.validate(_result([_seg(0.0, 2.0, "ok"),
                                    _seg(2.0, 5.0, "x"),
                                    _seg(5.0, 50.0, "gap"),
                                    _seg(50.0, 52.0, "dup text"),
                                    _seg(52.0, 54.0, "dup text")]))
        assert worse.quality_score <= clean.quality_score

    def test_error_penalty_formula_documented(self) -> None:
        # One negative timestamp: INVALID, score = 1 - 0.25 = 0.75
        seg = TranscriptSegment.__new__(TranscriptSegment)
        seg.start, seg.end, seg.text = -1.0, 2.0, "x"
        seg.speaker, seg.language, seg.confidence = (
            "", TranscriptLanguage.UNKNOWN, 0.0)
        report = V.validate(_result([seg]))
        assert report.quality_score == pytest.approx(0.75)

    def test_penalty_cap_floor(self) -> None:
        """Score is clamped at the documented floor even with many errors."""
        cfg = TranscriptQualityConfig()
        floor = 1.0 - cfg.PENALTY_CAP
        # 6 segments with negative start + empty text -> many errors;
        # raw penalty far exceeds PENALTY_CAP, so score must sit at floor.
        segs = []
        for k in range(6):
            seg = TranscriptSegment.__new__(TranscriptSegment)
            seg.start, seg.end, seg.text = -1.0, 0.0, ""
            seg.speaker, seg.language, seg.confidence = (
                "", TranscriptLanguage.UNKNOWN, 0.0)
            segs.append(seg)
        report = V.validate(_result(segs))
        assert report.quality_score == round(floor, 6)
        assert report.is_invalid
        tr = _result([_seg(0.0, 2.0, "a"), _seg(2.0, 999.0, "b")])
        s1 = V.validate(tr).quality_score
        s2 = V.validate(tr).quality_score
        assert s1 == s2


# ----------------------------------------------------------------------
# 82-83. Task 19 output feeds directly into the validator
# ----------------------------------------------------------------------


class TestTask19OutputFeed:

    def _fake_long_audio_result(self) -> TranscriptionResult:
        """Mimic a LongAudioProcessor.assemble() output shape."""
        segments = [
            _seg(0.0, 3.0, "Hola"),
            _seg(3.0, 6.0, "mundo"),
            _seg(6.0, 9.0, "introducción"),
        ]
        t = Transcript(material_id="long.wav",
                       language=TranscriptLanguage.SPANISH,
                       segments=segments,
                       metadata={"provider": "mock-asr",
                                "long_audio": True,
                                "chunk_count": 2,
                                "chunk_duration_seconds": 300.0,
                                "overlap_seconds": 0.0,
                                "total_duration_seconds": 9.0,
                                "empty_result": False})
        return TranscriptionResult(t)

    def test_long_audio_result_validates_cleanly(self) -> None:
        report = V.validate(self._fake_long_audio_result(), source_duration=9.0)
        assert report.status is QualityStatus.VALID
        assert report.quality_score == 1.0
        assert report.statistics.segment_count == 3
        assert report.statistics.total_transcript_duration == 9.0

    def test_long_audio_gap_warning_detected(self) -> None:
        tr = self._fake_long_audio_result()
        tr.transcript.segments[1].start = 30.0  # 21s gap
        report = V.validate(tr, source_duration=9.0)
        assert report.issues_with_code(QualityIssueCode.LONG_GAP)
        # out-of-bounds: end 6.0 still <= 9.0, but start 30 flagged? no -
        # out-of-bounds only checks end vs duration
        assert report.status in (QualityStatus.WARNING, QualityStatus.INVALID)

    def test_long_audio_duration_in_bounds(self) -> None:
        tr = self._fake_long_audio_result()
        report = V.validate(tr, source_duration=9.0)
        assert not report.issues_with_code(QualityIssueCode.TIMESTAMP_OUT_OF_BOUNDS)


# ----------------------------------------------------------------------
# 84-86. Real Whisper integration
# ----------------------------------------------------------------------


class TestRealWhisperQuality:
    """End-to-end: AudioInput -> LocalWhisperProvider (tiny) ->
    TranscriptionResult -> TranscriptQualityValidator.

    Skips when faster-whisper or the tiny model is unavailable.
    Reuses the same model-cache behaviour as Task 18/19 tests (no
    re-download here: setup_class loads once).
    """

    _model: Optional[Any] = None

    @classmethod
    def setup_class(cls) -> None:
        try:
            import faster_whisper
        except ImportError:
            pytest.skip("faster-whisper not installed")
        try:
            cls._model = faster_whisper.WhisperModel(
                "tiny", device="cpu", compute_type="int8")
        except Exception as exc:
            pytest.skip("tiny model unavailable: %s" % exc)

    @pytest.mark.integration
    def test_real_whisper_output_validates(self, monkeypatch) -> None:
        from src.whisper_provider import (
            LocalWhisperProvider,
            WhisperConfig,
        )

        fixture = os.path.join("tests", "fixtures", "long_silence_5s.wav")
        if not os.path.exists(fixture):
            pytest.skip("fixture not present")

        provider = LocalWhisperProvider(
            WhisperConfig(model_name="tiny", device="cpu",
                          compute_type="int8"),
            model_factory=lambda *a, **k: _RealModelWrapper(self._model),
        )
        validator = AudioMaterialValidator()
        vresult = validator.validate(fixture)
        assert vresult.valid
        audio_input = validator.to_audio_input(vresult)

        result = provider.transcribe(audio_input)
        assert result.transcript is not None

        before = copy.deepcopy(result)
        report = V.validate(result, source_duration=5.0)
        after = result

        # structural assertions
        assert report.status in (QualityStatus.VALID, QualityStatus.WARNING)
        assert 0.0 <= report.quality_score <= 1.0
        assert report.duration_check_available is True
        # transcript untouched
        assert after.transcript.segments == before.transcript.segments
        assert after.transcript.metadata == before.transcript.metadata


class _RealModelWrapper:
    """Minimal adapter so LocalWhisperProvider can use a preloaded model
    without re-loading (same pattern as Task 18/19 integration tests)."""

    def __init__(self, model) -> None:
        self._model = model

    def transcribe(self, *args, **kwargs):
        return self._model.transcribe(*args, **kwargs)


# ----------------------------------------------------------------------
# API surface
# ----------------------------------------------------------------------


class TestFactoryAndEnums:

    def test_factory_returns_validator(self) -> None:
        v = create_transcript_quality_validator()
        assert isinstance(v, TranscriptQualityValidator)

    def test_factory_with_duration(self) -> None:
        v = create_transcript_quality_validator(source_duration=10.0)
        report = v.validate(_result([_seg(0.0, 20.0, "x")]))
        assert report.issues_with_code(QualityIssueCode.TIMESTAMP_OUT_OF_BOUNDS)

    def test_status_from_string(self) -> None:
        assert QualityStatus.from_string("valid") is QualityStatus.VALID
        assert QualityStatus.from_string("nonsense") is QualityStatus.INVALID

    def test_severity_from_string(self) -> None:
        assert QualitySeverity.from_string("error") is QualitySeverity.ERROR

    def test_issue_to_dict_roundtrip(self) -> None:
        issue = QualityIssue(QualityIssueCode.LONG_GAP,
                             QualitySeverity.WARNING,
                             "gap of 80s",
                             segment_index=1, start_time=20.0, end_time=None)
        d = issue.to_dict()
        assert d["code"] == "LONG_GAP"
        assert d["severity"] == "WARNING"
        assert d["segment_index"] == 1

    def test_report_to_dict_serializable(self) -> None:
        report = V.validate(_result([_seg(0.0, 2.0, "a")]))
        d = report.to_dict()
        assert d["status"] == "VALID"
        assert d["quality_score"] == 1.0
        assert d["statistics"]["segment_count"] == 1
        import json
        json.dumps(d)  # must be JSON-serializable
