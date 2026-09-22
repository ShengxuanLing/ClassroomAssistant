"""Long audio segmentation and transcript assembly layer (Task 19).

Sits between AudioInput and the existing ASRProvider contract:

    AudioInput
        |
    LongAudioProcessor
        |
    AudioChunk (deterministic fixed time window)
        |
    ASRProvider.transcribe(AudioInput)
        |
    TranscriptionResult (per chunk)
        |
    Global timestamp remapping + deterministic assembly
        |
    TranscriptionResult (complete, global timeline)

Constraints (Task 19 scope only):
- Fixed time windows.  No VAD, no silence detection, no semantic
  chunking, no speaker diarization, no automatic retry, no parallel
  ASR, no LLM, no translation.
- All timestamps use seconds (the unit of the existing transcription
  layer: TranscriptSegment.start/end are float seconds).
- A single ASRProvider instance is reused for every chunk, so a
  real LocalWhisperProvider loads its model exactly once.
- Chunk failures are never swallowed: the whole operation fails with
  a clear error that names the failing chunk index.
- Empty chunk results are NOT errors.  An all-empty result is a
  valid TranscriptionResult with empty segments.
- Text is never modified: no translation, no summarising, no
  rewriting.  Only timestamp, ordering and assembly happen here.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple

from src.asr_provider import (
    ASRProvider,
    ASRProviderError,
    ASRProviderErrorCode,
    ASRProcessingError,
    ASRUnavailableError,
    TranscriptionResult,
)
from src.audio_input import AudioInput
from src.models import Transcript, TranscriptLanguage, TranscriptSegment


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

# Single source of truth for the default window size.
DEFAULT_CHUNK_DURATION_SECONDS = 300.0
# Single source of truth for the default overlap.  The first version
# ships with 0 to keep dedup fully deterministic; a non-zero value is
# still configurable and tested.
DEFAULT_OVERLAP_SECONDS = 0.0


class LongAudioConfigError(Exception):
    """Stable configuration error for long-audio parameters."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class LongAudioConfig:
    """Configuration for deterministic long-audio segmentation.

    - chunk_duration_seconds: window width C (must be > 0).
    - overlap_seconds: overlap O between adjacent windows,
      0 <= O < C.  step = C - O.
    """

    chunk_duration_seconds: float = DEFAULT_CHUNK_DURATION_SECONDS
    overlap_seconds: float = DEFAULT_OVERLAP_SECONDS

    def validate(self) -> None:
        """Fail fast on invalid configuration.

        Rejects chunk_duration <= 0, overlap < 0, and
        overlap >= chunk_duration (which would make the window step
        zero or negative and loop forever).
        """
        c = self.chunk_duration_seconds
        o = self.overlap_seconds
        if isinstance(c, bool) or not isinstance(c, (int, float)):
            raise LongAudioConfigError(
                "chunk_duration_seconds must be a number, got %r" % (c,))
        if isinstance(o, bool) or not isinstance(o, (int, float)):
            raise LongAudioConfigError(
                "overlap_seconds must be a number, got %r" % (o,))
        c = float(c)
        o = float(o)
        if c <= 0:
            raise LongAudioConfigError(
                "chunk_duration_seconds must be > 0, got %r" % (c,))
        if o < 0:
            raise LongAudioConfigError(
                "overlap_seconds must be >= 0, got %r" % (o,))
        if o >= c:
            raise LongAudioConfigError(
                "overlap_seconds must be < chunk_duration_seconds "
                "(%r >= %r would produce a non-advancing window)"
                % (o, c,))
        self.chunk_duration_seconds = c
        self.overlap_seconds = o

    def step_seconds(self) -> float:
        """Advancing distance between consecutive chunk starts."""
        return self.chunk_duration_seconds - self.overlap_seconds

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_duration_seconds": self.chunk_duration_seconds,
            "overlap_seconds": self.overlap_seconds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LongAudioConfig":
        return cls(
            chunk_duration_seconds=float(
                data.get("chunk_duration_seconds",
                         DEFAULT_CHUNK_DURATION_SECONDS)),
            overlap_seconds=float(
                data.get("overlap_seconds", DEFAULT_OVERLAP_SECONDS)),
        )


# ----------------------------------------------------------------------
# Chunk model
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class AudioChunk:
    """One deterministic slice of the source audio timeline.

    start_time/end_time are in seconds on the ORIGINAL audio
    timeline.  chunk_index is 0-based and strictly increasing.
    source_audio keeps a reference back to the original AudioInput
    for traceability.
    """

    chunk_index: int
    start_time: float
    end_time: float
    source_audio: Optional[AudioInput] = None

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


def generate_chunks(
    duration_seconds: float,
    config: Optional[LongAudioConfig] = None,
    source_audio: Optional[AudioInput] = None,
) -> Tuple[AudioChunk, ...]:
    """Deterministic fixed-window chunk generation.

    For D = duration, C = chunk duration, O = overlap:
        step = C - O
        start_k = k * step
        end_k   = min(start_k + C, D)
    Windows advance until D is covered.  No empty chunk and no
    window past the end of the audio is ever produced.

    Raises:
        ValueError: if duration is negative, NaN or infinite.
        LongAudioConfigError: if the config is invalid.
    """
    if isinstance(duration_seconds, bool):
        raise ValueError("audio duration must be a number, got %r"
                         % (duration_seconds,))
    d = float(duration_seconds)
    if d != d or d in (float("inf"),):
        raise ValueError(
            "audio duration must be a finite number, got %r"
            % (duration_seconds,))
    if d < 0:
        raise ValueError(
            "audio duration must be >= 0, got %r" % (duration_seconds,))
    if config is None:
        config = LongAudioConfig()
    config.validate()

    step = config.step_seconds()
    chunks: List[AudioChunk] = []
    index = 0
    start = 0.0
    while start < d:
        end = min(start + config.chunk_duration_seconds, d)
        chunks.append(AudioChunk(
            chunk_index=index,
            start_time=start,
            end_time=end,
            source_audio=source_audio,
        ))
        index += 1
        start += step
    return tuple(chunks)


# ----------------------------------------------------------------------
# Duration provider
# ----------------------------------------------------------------------

class AudioDurationProvider(Protocol):
    """Reports the duration (seconds) of an audio input.

    Deliberately separate from ASRProvider: duration is a metadata
    concern, transcription is a content concern.
    """

    def get_duration(self, audio_input: AudioInput) -> float:
        ...


class PyAvDurationProvider(AudioDurationProvider):
    """Duration from container metadata via PyAV.

    Reads only container/stream metadata; never decodes the whole
    audio into memory.  PyAV is already the ASR runtime's audio
    stack, so no new dependency is introduced.
    """

    def get_duration(self, audio_input: AudioInput) -> float:
        import av  # lazy: no module-level runtime dependency

        path = getattr(audio_input, "path", "") or ""
        if not path:
            raise ASRUnavailableError("AudioInput has no audio path")
        if not os.path.exists(path):
            raise ASRUnavailableError(
                "Audio file no longer exists: %s" % path)
        try:
            container = av.open(path)
        except Exception as exc:
            raise ASRUnavailableError(
                "Unable to open audio container for duration "
                "metadata: %s" % path) from exc
        try:
            duration = container.duration
            if duration is None:
                for stream in container.streams:
                    if stream.duration is not None:
                        duration = stream.duration
                        break
            if duration is None:
                raise ASRUnavailableError(
                    "Audio container exposes no duration metadata: %s"
                    % path)
            seconds = float(duration) / 1_000_000.0
            if seconds < 0:
                raise ASRUnavailableError(
                    "Audio duration is negative: %s (%r)"
                    % (path, seconds))
            return seconds
        finally:
            container.close()


# ----------------------------------------------------------------------
# Chunk materialisation (temp files, unique naming, cleanup)
# ----------------------------------------------------------------------

SAMPLE_RATE = 16000  # whisper target sample rate


def build_long_audio_chunk(
    chunk: AudioChunk,
    source_audio: AudioInput,
) -> AudioInput:
    """Slice the source audio into a uniquely named temporary WAV.

    The temp file lives in the system temp dir, never in the
    repository.  The original AudioInput's material identity is
    carried over so the final transcript stays traceable to the
    original file.
    """
    import av
    import wave
    import numpy as np

    path = source_audio.path
    if not os.path.exists(path):
        raise ASRUnavailableError("Audio file no longer exists: %s" % path)

    container = av.open(path)
    try:
        audio_stream = next(
            (s for s in container.streams if s.type == "audio"), None)
        if audio_stream is None:
            raise ASRUnavailableError(
                "No audio stream found in %s" % path)

        resampler = av.audio.resampler.AudioResampler(
            format="s16", layout="mono", rate=SAMPLE_RATE)

        target_start = chunk.start_time
        target_end = chunk.end_time

        if target_start > 0:
            seek_frame = max(0, int((target_start - 0.5) * SAMPLE_RATE))
            container.seek(
                seek_frame, any_frame=True, backward=True,
                stream=audio_stream)

        sample_offset = int(round(target_start * SAMPLE_RATE))
        sample_count = int(
            round((target_end - target_start) * SAMPLE_RATE))

        decoded: List[Any] = []
        for frame in container.decode(audio_stream):
            for res_frame in resampler.resample(frame):
                array = res_frame.to_ndarray()
                mono = array[0] if array.ndim > 1 else array
                decoded.append(mono)
    finally:
        container.close()

    audio_data = (np.concatenate(decoded) if decoded
                  else np.array([], dtype=np.int16))
    if len(audio_data) > sample_offset:
        audio_data = audio_data[sample_offset:]
    if len(audio_data) > sample_count:
        audio_data = audio_data[:sample_count]

    tmp_name = "clases_chunk_%s_%d.wav" % (
        uuid.uuid4().hex, chunk.chunk_index)
    tmp_path = os.path.join(tempfile.gettempdir(), tmp_name)
    with wave.open(tmp_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(
            audio_data.tobytes() if audio_data.size else b"")

    chunk_audio = AudioInput(
        path=tmp_path,
        extension=os.path.splitext(path)[1] or ".wav",
        material=source_audio.material,
        metadata=dict(source_audio.metadata),
    )
    # Keep traceability back to the original file (sections 27/46).
    chunk_audio.metadata["source_audio_path"] = path
    chunk_audio.metadata["chunk_index"] = chunk.chunk_index
    return chunk_audio


# Keep a reference to the real builder so the integration test can
# monkeypatch back to it after the unit-test fixture has replaced it.
_real_build_long_audio_chunk = build_long_audio_chunk


def cleanup_chunk_audio(chunk_audio: Optional[AudioInput]) -> None:
    """Remove a temporary chunk file (safe to call always)."""
    if chunk_audio is None:
        return
    tmp_path = getattr(chunk_audio, "path", "") or ""
    if tmp_path and os.path.isabs(tmp_path) and os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# ----------------------------------------------------------------------
# Global timestamp remapping and deterministic assembly
# ----------------------------------------------------------------------

def _remap_chunk_segments(
    chunk: AudioChunk,
    chunk_result: TranscriptionResult,
    previous_global_end: float,
    total_duration: float,
    overlap_active: bool,
) -> List[TranscriptSegment]:
    """Map local segment timestamps onto the global timeline.

    global_start = chunk.start_time + local_start
    global_end   = chunk.start_time + local_end

    - No rounding: the runtime's precision is preserved.
    - Timestamps beyond the source duration are clamped to it
      (explicit, testable boundary rule).
    - With overlap active, a segment that starts at or before the
      previous chunk's global end AND ends at or before it, whose
      text exactly matches the immediately preceding kept segment,
      is treated as an exact overlap duplicate and dropped
      (deterministic string-equality only; no fuzzy matching).
    """
    offset = chunk.start_time
    out: List[TranscriptSegment] = []
    for seg in chunk_result.segments:
        g_start = seg.start + offset
        g_end = seg.end + offset
        if g_end > total_duration:
            g_end = total_duration
        if g_start < 0:
            g_start = 0.0
        if g_end < g_start:
            g_end = g_start
        out.append(TranscriptSegment(
            start=g_start,
            end=g_end,
            text=seg.text,
            speaker=seg.speaker,
            language=seg.language,
            confidence=seg.confidence,
        ))
    if out and overlap_active:
        filtered: List[TranscriptSegment] = []
        for i, seg in enumerate(out):
            prev = out[i - 1] if i > 0 else None
            if (prev is not None
                    and seg.start < previous_global_end
                    and seg.end <= previous_global_end
                    and prev.text == seg.text):
                continue
            filtered.append(seg)
        out = filtered
    return out


# ----------------------------------------------------------------------
# Processor
# ----------------------------------------------------------------------

class LongAudioProcessor:
    """Orchestrates deterministic long-audio transcription.

    Sequential processing: chunk N+1 only begins after chunk N's
    ASR call has fully returned.  One ASRProvider instance is
    reused for every chunk, so a LocalWhisperProvider loads its
    model exactly once across the whole run.
    """

    def __init__(
        self,
        asr_provider: ASRProvider,
        duration_provider: AudioDurationProvider,
        config: Optional[LongAudioConfig] = None,
    ) -> None:
        self._asr = asr_provider
        self._duration_provider = duration_provider
        self._config = config or LongAudioConfig()
        self._config.validate()
        self._temp_chunk_paths: List[str] = []

    @property
    def config(self) -> LongAudioConfig:
        return self._config

    def process(self, audio_input: AudioInput) -> TranscriptionResult:
        """Run the full pipeline; returns ONE TranscriptionResult on
        the original global timeline.

        Raises a provider error naming the failing chunk index when
        any chunk fails; never returns a partially-complete
        transcript.
        """
        self._config.validate()

        duration = self._duration_provider.get_duration(audio_input)
        if duration < 0:
            raise ValueError(
                "audio duration must be >= 0, got %r" % duration)

        chunks = generate_chunks(duration, self._config, audio_input)

        all_segments: List[TranscriptSegment] = []
        previous_global_end = 0.0
        overlap_active = self._config.overlap_seconds > 0

        for chunk in chunks:
            chunk_input: Optional[AudioInput] = None
            made_temp = False
            try:
                if (chunk.chunk_index == 0
                        and chunk.start_time == 0
                        and chunk.end_time == duration):
                    chunk_input = audio_input
                else:
                    chunk_input = build_long_audio_chunk(
                        chunk, audio_input)
                    made_temp = True
                    self._temp_chunk_paths.append(chunk_input.path)

                try:
                    chunk_result = self._asr.transcribe(chunk_input)
                except ASRProviderError as exc:
                    fail_msg = "chunk %d: %s" % (
                        chunk.chunk_index, exc.message)
                    code = getattr(exc, "error_code",
                                    ASRProviderErrorCode.PROCESSING_ERROR)
                    if code == ASRProviderErrorCode.UNAVAILABLE:
                        raise ASRUnavailableError(
                            fail_msg,
                            error_code=ASRProviderErrorCode.UNAVAILABLE,
                            retryable=exc.retryable,
                        ) from exc
                    raise ASRProcessingError(
                        fail_msg,
                        error_code=ASRProviderErrorCode.PROCESSING_ERROR,
                    ) from exc

                segs = _remap_chunk_segments(
                    chunk, chunk_result, previous_global_end,
                    duration, overlap_active)
                all_segments.extend(segs)
                if segs:
                    previous_global_end = segs[-1].end
            finally:
                if made_temp:
                    cleanup_chunk_audio(chunk_input)

        all_segments.sort(key=lambda s: (s.start, s.end))

        material_id = (
            getattr(audio_input, "material_id", None)
            or audio_input.path
        )

        lang_counts: Dict[str, int] = {}
        for seg in all_segments:
            key = seg.language.value if seg.language else "Unknown"
            if key != "Unknown":
                lang_counts[key] = lang_counts.get(key, 0) + 1
        if lang_counts:
            top_lang = max(lang_counts.items(), key=lambda kv: kv[1])
            language = TranscriptLanguage.from_string(top_lang[0])
        else:
            language = TranscriptLanguage.UNKNOWN

        metadata = {
            "provider": getattr(self._asr, "name",
                                 type(self._asr).__name__),
            "long_audio": True,
            "chunk_count": len(chunks),
            "chunk_duration_seconds":
                self._config.chunk_duration_seconds,
            "overlap_seconds": self._config.overlap_seconds,
            "total_duration_seconds": duration,
            "empty_result": len(all_segments) == 0,
        }
        transcript = Transcript(
            material_id=material_id,
            language=language,
            segments=all_segments,
            metadata=metadata,
        )
        return TranscriptionResult(transcript=transcript)


# ----------------------------------------------------------------------
# Public factory
# ----------------------------------------------------------------------

def create_long_audio_processor(
    asr_provider: ASRProvider,
    duration_provider: AudioDurationProvider,
    config: Optional[LongAudioConfig] = None,
) -> LongAudioProcessor:
    """Factory mirroring create_local_whisper_provider /
    create_mock_asr_provider."""
    return LongAudioProcessor(
        asr_provider, duration_provider, config)