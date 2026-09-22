# Classroom Assistant - Architecture Design

## Overview

The Classroom Assistant project provides a structured pipeline for processing multilingual classroom materials (audio, images, notes) and generating knowledge structures and review materials for UAB students.

## Core Data Model

The architecture is built around two primary domain objects: **Course** and **ClassSession**, supported by supporting models for materials, evidence, and knowledge points.

### Course

- **course_id**: Stable ID generated via SHA-256 hash of `name + code` (`course-<16 chars>`)
- **name**: Course name (e.g., "Economia")
- **code**: Course code (e.g., "ECO2026")
- **language**: Primary language (Spanish, Catalan, Chinese, English, Unknown)
- **metadata**: Flexible key-value store

### ClassSession

- **session_id**: Stable ID generated via SHA-256 hash of `course_id + session_number` (`session-<16 chars>`)
- **course_id**: References the parent Course
- **session_number**: Integer session number (handles same-day multiple sessions)
- **date**: Session date
- **title**: Session title
- **material_refs**: List of material IDs
- **evidence_refs**: List of evidence IDs
- **knowledge_point_refs**: List of knowledge point IDs
- **verification_refs**: List of verification IDs

```
Course âââ¬ââ ClassSession (1..N)
         â     âââ Material refs
         â     âââ Evidence refs
         â     âââ KnowledgePoint refs
         âââ CourseContext (manages all courses and sessions)
               âââ add_course()
               âââ add_session()
               âââ add_material_to_session()
               âââ add_evidence_to_session()
               âââ add_knowledge_point_to_session()
```

## Supporting Models

- **Material**: Represents input files (audio, images, text, notes, syllabi)
- **Evidence**: Extracted factual content from materials
- **KnowledgePoint**: Structured knowledge units extracted from evidence
- **SourceReference**: Traceability metadata (material_id, location, timestamps, page)
- **VerificationItem**: Items requiring human review

## Processing Pipeline

```
Input Materials âââ material_index.py (scan & index)
                   âââ note_parser.py (TXT/Markdown parsing)
                   âââ evidence_extractor.py (unified extraction)
                   âââ knowledge_structure.py (knowledge organization)
                         â
                         â¼
              CourseContext (orchestration)
                         â
                         â¼
              Output: Knowledge structures, review materials
```

## Key Design Principles

1. **Stable IDs**: All entities use SHA-256-based deterministic IDs (no UUID defaults)
2. **Deep Copy Isolation**: CourseContext uses `copy.deepcopy` to avoid modifying upstream objects
3. **领域层标准库优先**: 领域 / 分析层（src/models.py、knowledge_*、evidence_*、integration 等）只依赖 Python 标准库，保持纯函数、可独立测试。
4. **运行时依赖本地离线**: ASR（faster-whisper / ctranslate2）与 OCR（rapidocr-onnxruntime==1.2.3）在本地离线运行，**无云 API、无网络调用**；其余运行时依赖见 `requirements.txt`（pypdf / python-docx / av / numpy 等）。
5. **Evidence-First**: Every knowledge point must trace back to source material


## Audio Input Validation Layer (Task 16)

Task 16 adds a file-level audio input boundary between untrusted filesystem input and the future ASR layer.

Pipeline position

`	ext
Audio File
      |
AudioMaterialValidator (src/audio_input.py)
      |
AudioValidationResult (structured VALID / INVALID + stable error codes)
      |
AudioInput (file-level identity: path, extension, Material, metadata)
      |
TranscriptionEngine / future ASR  (Task 17+)
`

Components

- AudioMaterialValidator
    - validate(target): accepts a Material (reusing its indexed
      metadata: size/sha256/mtime/relative_path) or a raw path/PathLike.
    - Checks: existence, regular file, non-empty, supported
      extension (case-insensitive), readability. All read-only;
      the file contents are never read.
- AudioValidationResult: structured outcome with valid, status
  (VALID/INVALID), stable error codes (FILE_NOT_FOUND,
  NOT_A_FILE, UNSUPPORTED_EXTENSION, EMPTY_FILE, UNREADABLE),
  warnings, plus file-level metadata (path, extension, file_size,
  modified_time, material, metadata). to_dict/from_dict round-trip.
- AudioInput: lightweight input object for the ASR layer. Carries only
  file-level identity. No transcript/segments/speaker/language/
  confidence fields. Builds via validator.to_audio_input(result);
  raises ValueError on an INVALID result.
- SUPPORTED_AUDIO_EXTENSIONS: authoritative (.mp3, .m4a, .wav, .ogg),
  kept in sync with MaterialIndex._EXT_TO_TYPE and
  ClassSessionProcessor._AUDIO_EXTENSIONS.

Invariants

- Deterministic: same input + same file state -> equal results.
- No mutation: the validator never creates, modifies, moves, or
  deletes any file; Unicode/Spanish/Catalan filenames and
  metadata are preserved verbatim.
- No new dependencies: stdlib only (pathlib, dataclasses, enum,
  os, stat). No Whisper, ffmpeg, VAD, LLM, network, or database.
- File size is metadata, not a rejection criterion.


Task 16 implements file-level audio validation and the AudioInput contract only. Real ASR is not implemented and remains the subject of Task 17+. Audio decoding, segmentation, VAD, language identification, and evidence generation are out of scope for this layer.

## Audio Transcription Pipeline


## Image / Board Photo OCR Layer (Task 11)

`	ext
Image
   ↓
OCR abstraction
   ↓
OCRResult
   ↓
Evidence
`

### Data Structures

- **OCRResult**: Contains ocr_result_id (stable, based on material_id + language + segments), material_id, language, and segments list
- **OCRSegment**: Represents a recognized text area with 	ext, ounding_box (x, y, width, height), confidence, language, and optional page
- **BoundingBox**: Coordinate system with origin at top-left, x increases right, y increases down. Values: x, y, width, height (all >= 0)

### OCR Engine

- **MockOCREngine**: Deterministic mock using SHA-256 hash of material_id for reproducible segment generation
- **No real OCR**: This layer provides data structures only; real OCR engine (Tesseract, EasyOCR, etc.) to be integrated separately in the future
- **Material validation**: Only IMAGE type materials accepted; AUDIO, TEXT, PDF materials rejected with ValueError

### OCR → Evidence Conversion

- ocr_to_evidence(ocr_result) converts each OCRSegment to an Evidence object with EvidenceType.OCR
- Bounding box coordinates preserved in Evidence metadata
- SourceReference created with material_id, location, line, and paragraph
- Confidence mapping: > 0.8 → HIGH, >= 0.5 → MEDIUM, < 0.5 → LOW

### Anti-Hallucination Principles

- No text generated from filenames
- No text generated from metadata alone
- No automatic translation of OCR text
- No automatic correction of OCR text
- No automatic summary generation
- No automatic KnowledgePoint generation from OCR alone

### Material Type Validation

- ✅ IMAGE (.jpg, .jpeg, .png, .webp): Allowed
- ❌ AUDIO (.mp3, .m4a, .wav, .ogg): Rejected
- ❌ TEXT (.txt, .md): Rejected
- ❌ PDF: Rejected

### Multilingual Support

- Spanish, Catalan, Chinese, and other Unicode text preserved without auto-translation
- Language field set based on input material language
- Mixed language text handled correctly

### Test Coverage

- 12 test cases covering: OCRResult creation, OCRSegment creation, BoundingBox validation, MockOCREngine determinism, material type rejection, anti-hallucination, and OCR→Evidence conversion
- All 219 tests pass across all tasks
- compileall: PASS

### Overview
The Audio-Transcription-Evidence pipeline processes classroom audio files and converts them into structured evidence for knowledge extraction.

Audio Files (MP3, WAV, OGG)
        --> TranscriptionEngine (abstract)
        --> MockTranscriber (deterministic, no real API)
        --> Transcript (with stable ID, segments)
        --> transcript_to_evidence() / to_transcript_evidence()
        --> Evidence (EvidenceType.TRANSCRIPT)

### Components

- TranscriptionEngine: Abstract base class with transcribe() and transcribe_segments() methods
- MockTranscriber: Deterministic transcription using SHA-256 hash of audio path; generates 1-5 segments; no external API calls
- Transcript: Dataclass with stable ID generation (SHA-256), language preservation, segment validation
- TranscriptSegment: Dataclass with start/end timestamps, text, speaker, language, confidence; auto-clamps invalid values
- TranscriptLanguage: Enum for Spanish, Catalan, Chinese, English, Unknown; case-insensitive from_string()
- transcript_to_evidence(): Converts Transcript to Evidence with EvidenceType.TRANSCRIPT; confidence HIGH if all segments >0.5 else MEDIUM
- create_transcript_from_segments(): Factory function to create Transcript from segment data dicts

### Design Principles

1. No Real API: MockTranscriber uses deterministic hash-based generation; no network calls
2. Stable IDs: Transcript IDs are SHA-256 based, deterministic for same inputs
3. Evidence-First: Every transcript converts to Evidence for traceability
4. Domain-Layer Purity: transcript models and transcript-to-evidence conversion use
   only the Python standard library (deterministic, no third-party imports)
5. Local-Only Engines: the real ASR engine (faster-whisper / ctranslate2) runs
   **offline on this machine** — no cloud API, no network calls. Runtime
   dependencies are declared in requirements.txt; the domain/analysis layers stay
   stdlib-only (see the corrected statement in "Design Principles" at the top of
   this document)


---

## Task 12 — Unified Evidence Pipeline

### Overview
The Unified Evidence Pipeline (src/processor.py, ClassSessionProcessor) is the single
entry point that turns heterogeneous classroom materials into a unified Evidence list,
ready to be handed to the Integration layer. It is a pure orchestration layer: it routes
each material to the existing handler for its type, collects the Evidence results, and
returns them in stable input order. It does NOT generate knowledge, summaries, or plans.

### Routing
Material
   |
   +-- Note (TXT/MD) ---+--- NoteParser / EvidenceExtractor -------------------+
   |                                                             |
   +-- Audio (.mp3/.m4a/.wav/.ogg) ---+--- TranscriptionEngine (MockTranscriber)
   |                                       -> transcript_to_evidence() --------+
   |                                                                             |
   +-- Image (.jpg/.jpeg/.png/.webp) ---+--- OCREngine (MockOCREngine)
   |                                       -> ocr_to_evidence() ----------------+
   |                                                                                 |
   +-- Syllabus / unsupported (PDF, DOCX, ...) --+--- safely skipped (no fabricated
   |                                               Evidence)
   v
Unified Evidence List (list[Evidence])
   |
   v
EvidenceIntegrator.integrate(evidence)  (downstream semantic combination, unchanged)

### Layering
- Processor = orchestration only (route -> call -> merge -> sort by input order -> return)
- Evidence = unified intermediate representation
- Integration (EvidenceIntegrator) = downstream semantic combination; NOT re-implemented here

### Components
- ClassSessionProcessor (src/processor.py)
  - __init__(transcription_engine=None, ocr_engine=None): DI for test/real engines;
    defaults to MockTranscriber / MockOCREngine
  - process_session(session, materials) -> list[Evidence]; also process_materials(materials)
  - _process_note -> EvidenceExtractor.extract()
  - _process_audio -> engine.transcribe() then transcript_to_evidence() (single Evidence)
  - _process_image -> engine.ocr() then ocr_to_evidence() (one Evidence per OCR segment)
  - SYLLABUS / unknown types return [] (safe skip, no fabricated evidence)

### Constraints honored
- Real ASR: NOT implemented (MockTranscriber / injected TranscriptionEngine only)
- Real OCR: NOT implemented (MockOCREngine / injected OCREngine only)
- LLM / Embedding / Knowledge generation: NOT used
- Network calls: NONE; no new third-party dependencies added
- Determinism: identical input -> identical output order and content; material_id,
  SourceReference (timestamps, page, line, paragraph), OCR bounding boxes, and
  original multilingual text are preserved unchanged

### Tests
- tests/test_processor.py: 28 tests (Note routing + language preservation, Audio
  timestamps/DI, Image page/bbox/material_id/DI, routing, data integrity,
  no-real-ASR/OCR/LLM guards). Full project suite: 266 passed; compileall on
  processor.py + test_processor.py: PASS

## Task 13 — Incremental KnowledgePoint Integration

### Overview
The Knowledge Pipeline (src/knowledge_pipeline.py) is a thin orchestration layer
that wires the existing modules together so the unified Evidence list produced
by Task 12's ClassSessionProcessor can be turned into a KnowledgeStructure
incrementally, without re-implementing any of the underlying logic.

```
ClassSessionProcessor
        ↓
Unified Evidence
        ↓
KnowledgePipeline
        ↓
EvidenceIntegrator
        ↓
KnowledgeExtractor
        ↓
KnowledgeStructure
```

### Responsibilities
- Processor = Material → Evidence
- Integration = semantic relations between Evidence (duplicate / supporting / conflict)
- KnowledgePipeline = orchestration of Evidence → KnowledgeStructure
- KnowledgeExtractor = Evidence → KnowledgePoint extraction
- KnowledgeStructure = KnowledgePoint + Relationships + Conflicts

### Key Design Decisions
1. No re-implementation: EvidenceIntegrator and KnowledgeExtractor logic are
   called directly; no new algorithm is invented here.
2. Content-addressable KnowledgePoint IDs: knowledge_id is a SHA-256 hash of
   (content anchor + evidence_type), so resubmitting the same evidence always
   maps to the same ID — enabling idempotent incremental merges.
3. Supporting-group clustering: evidences in the same supporting group are
   merged into a single KnowledgePoint rather than split across two.
4. Conflict propagation: ConflictRecords are registered into
   KnowledgeStructure.conflicts and the involved KnowledgePoints are flagged
   needs_verification=True.
5. No LLM / no network / no database / no new third-party dependencies.

### Components
- KnowledgeExtractor (src/knowledge_pipeline.py)
  - extract(evidences, integration=None) -> list[KnowledgePoint]
  - Content-anchor clustering, deterministic ordering by knowledge_id
- KnowledgePipeline (src/knowledge_pipeline.py)
  - process_evidence(evidence, structure=None) -> PipelineResult
  - process_session(session, materials, structure=None) -> PipelineResult
  - PipelineResult: evidence_ids, new/updated KP ids, new relationship ids,
    conflict_ids, structure
- KnowledgeStructure (src/knowledge_structure.py)
  - Now also holds conflicts: list[ConflictRecord] and add_conflict()
  - to_dict/from_dict round-trip preserves KPs, relationships, conflicts
- ConflictRecord (src/integration.py)
  - to_dict/from_dict added; stable conflict_id derived from content so
    resubmitted equivalent evidence does not create duplicate records

### Incremental Behavior
- Empty structure + new Evidence: creates all KPs fresh
- Existing structure + new Evidence: only affected KPs are updated (evidence_refs
  merged, needs_verification escalated); untouched KPs remain unchanged
- Duplicate evidence resubmission: KP count unchanged, evidence_refs updated
- Idempotency: repeated identical input → no duplicate KPs, relationships, or conflicts

### Files Changed
- src/knowledge_pipeline.py (created)
- src/knowledge_structure.py (added conflicts field, add_conflict(), to_dict/from_dict extensions)
- src/integration.py (ConflictRecord.to_dict/from_dict; stable conflict_id in _detect_conflicts)
- tests/test_knowledge_pipeline.py (created: 33 tests)
- docs/architecture.md (this section)
- docs/status.md (Task 13 entry)

### Tests
- New tests: 33
- Full project suite: 299/299 passed
- compileall src tests: PASS
## Task 14 — Knowledge Validation & Evidence Conflict Resolution

### Pipeline (extended)
```text
ClassSession
      ↓
ClassSessionProcessor
      ↓
Evidence
      ↓
EvidenceIntegrator
      ↓
KnowledgeExtractor
      ↓
KnowledgeStructure
      ↓
KnowledgeValidator
      ↓
Validation Status / KnowledgeScore / Needs-Verification
```

### Responsibilities
- KnowledgeExtractor = Evidence → KnowledgePoint (unchanged)
- EvidenceIntegrator = duplicate / supporting / conflict relations between Evidence
  (one minimal bug fix: resubmitted identical evidence no longer conflicts
  with itself, because relation detection de-duplicates by evidence_id)
- KnowledgeValidator = for each KnowledgePoint, determine current support,
  conflict state, and deterministic knowledge_score from EXISTING evidence.
  It NEVER judges real-world truth.

### ValidationStatus semantics
- UNVERIFIED: no (or not enough) supporting evidence
- SUPPORTED: ≥1 distinct supporting Evidence and no unresolved conflict.
  This means "current material is consistent" — NOT "fact is proven true".
- CONFLICTED: ≥1 unresolved ConflictRecord touches the point's evidence.
  Both sides are preserved; no automatic resolution is performed.

### knowledge_score (deterministic)
- 0 supporting evidence → 0.0
- 1 supporting evidence → 0.5
- 2 supporting evidence → 0.75
- 3+ supporting evidence → 0.9
- any conflict → capped at 0.5
- always clamped to [0.0, 1.0]; NaN/inf reset to 0.0

knowledge_score measures "how much of the current Evidence supports this
KnowledgePoint", not "probability that the fact is true in the real world".

**Both write paths share this one formula (fixed 2026-09-21).** The
deterministic pipeline (`KnowledgeValidator` ->
`KnowledgePipeline._apply_validation`) and the AI extraction path
(`src/application/ai/validators.py::map_candidate_to_kp_payload`) both take
their value from `knowledge_validation.knowledge_score_from_counts(n_support,
n_conflict)`. Before that fix the AI path wrote the LLM's self-reported
`confidence` straight into this field, producing values (1.0, 0.95) that the
formula above can never emit -- its range is exactly 0.0 / 0.5 / 0.75 / 0.9.
The same database column therefore carried two incompatible meanings, and a
point with a single piece of evidence displayed 1.0 next to a `unverified`
badge. The LLM's own confidence is still preserved, but only where it belongs:
the `confidence` tier (HIGH/MEDIUM/LOW) and `metadata.ai_confidence`. On the
AI path `n_conflict` stays 0 -- evidence-level conflict detection belongs to
the deterministic assembly (`EvidenceIntegrator`), not to candidate extraction.

### needs_verification mapping
- UNVERIFIED → True
- SUPPORTED  → False
- CONFLICTED → True

### Components
- KnowledgeValidator / ValidationStatus / ValidationResult (src/knowledge_validation.py)
  - validate_knowledge_point(kp, evidence, conflicts) -> ValidationResult
  - validate_structure(structure, evidence, conflicts=None) -> dict
- KnowledgePipeline._apply_validation (src/knowledge_pipeline.py)
  - runs after extraction, read-only over Evidence
  - writes validation_status / knowledge_score / needs_verification onto KPs
- KnowledgeStructure helpers (src/knowledge_structure.py)
  - supporting_evidence_ids / supporting_evidence_count / conflict_count
- KnowledgePoint model fields (src/models.py)
  - validation_status: str (default "unverified")
  - knowledge_score: float (default 0.0, clamped to [0,1])

### Conflict handling
- Conflict detection: unchanged (EvidenceIntegrator order-reversal / negation)
- ConflictRecord: stable conflict_id reused; duplicate resubmissions dedup
- No automatic resolution; both sides preserved
- CONFLICTED state can be entered incrementally (SUPPORTED → CONFLICTED
  when a conflicting evidence arrives)
- Existing conflicts are never deleted by adding unrelated KPs

### Incremental behavior
- Old structure + new evidence → only affected KPs re-validated
- New supporting evidence updates knowledge_score and can move UNVERIFIED→SUPPORTED
- New conflict moves a point to CONFLICTED (needs_verification=True)
- Unrelated KPs and relationships untouched
- Duplicate evidence / duplicate conflict produce no duplicate state

### Idempotency
- update(update(S, E), E) ≡ update(S, E)
- no duplicate KPs, no duplicate relationships, no duplicate conflicts
- knowledge_score and validation_status do not drift

### Determinism
- No randomness, no timestamps, no object-id dependence
- supporting_evidence_ids ordered by first-seen input order
- conflict_ids sorted; KP iteration ordered by knowledge_id

### Serialization
- KnowledgePoint.to_dict / from_dict preserve validation_status and
  knowledge_score; missing keys default to "unverified" / 0.0
  (backward compatible with Task 13 data)
- KnowledgeStructure round-trip preserves validation state and conflicts
- No raw Python Enum objects in serialized output (values are plain strings)

### Tests
- tests/test_knowledge_validation.py: 39 new tests
- Full project suite: 338/338 passed
- compileall on all Task 14 modules: PASS

### Constraints
- No LLM / no network / no database / no new third-party dependencies
- No real ASR / OCR (mocks from Task 11/12 used)
- No conflict resolution or fact-checking

---

# Task 15 — Knowledge Review & Human Verification Layer

## Pipeline position

```text
KnowledgeStructure
        |
KnowledgeValidator (Task 14, evidence-based)
        |
ValidationStatus: UNVERIFIED / SUPPORTED / CONFLICTED
        |
KnowledgeReviewService (Task 15, human decisions)
        |
ReviewCandidates  (deterministic projection)
        |
Human Decision: CONFIRM / REJECT / KEEP_UNVERIFIED / resolve_conflict
        |
ReviewRecord (append-only history, stable IDs)
        |
Updated KnowledgeStructure (review_status + review_records)
```

## Responsibilities

- **KnowledgeValidator** = Evidence-based validation. It only looks at
  Evidence / ConflictRecord and writes `validation_status`,
  `knowledge_score`, `needs_verification`.
- **KnowledgeReviewService** = Human-decision management. It turns
  stored validation facts into a deterministic review queue and records
  explicit user decisions. It never re-runs the extractor.

## Key semantic separation

```text
ValidationStatus  -> describes the EVIDENCE relationship
ReviewStatus      -> describes the HUMAN decision
```

- CONFIRMED / REJECTED / KEPT_UNVERIFIED can only be produced by an
  explicit user action. Counts, scores, SUPPORTED state, or consistency
  between sources never auto-confirm. CONFLICTED never auto-rejects.
- A confirmed point that receives a new conflict re-enters review:
  `get_review_candidates` flips its `review_status` back to PENDING
  while keeping the whole `ReviewRecord` history.

## Components

- `ReviewStatus` enum (PENDING / CONFIRMED / REJECTED / KEPT_UNVERIFIED)
- `ReviewDecision` enum (CONFIRM / REJECT / KEEP_UNVERIFIED)
- `ReviewRecord` (frozen, stable `review_id` = SHA-256 over
  `knowledge_point_id | decision | sorted(selected evidence)`, note kept
  apart from Evidence)
- `ReviewCandidate` (frozen projection of a KP: status, score,
  supporting ids, conflict ids, priority)
- `KnowledgeReviewService`
  - `get_review_candidates(structure)` - CONFLICTED priority 0,
    UNVERIFIED 1, other needs-verification 2; tie-break by
    knowledge_point_id. Skips stable+reviewed SUPPORTED points;
    re-opens PENDING on regression.
  - `record_decision` / `confirm` / `reject` / `keep_unverified` /
    `resolve_conflict` - validate existence (KeyError), evidence
    ownership (ValueError), require explicit selection on CONFLICTED;
    only `kp.review_status` is mutated.
  - `review_status_of` / `review_history` / `latest_review` /
    `has_conflict` helpers.
- `KnowledgePoint.review_status` (str, default "pending", backward
  compatible in from_dict)
- `KnowledgeStructure.review_records` (append-only, idempotent add,
  to_dict / from_dict round-trip)

## Invariants

- No LLM / no web / no database / no UI / no new dependencies.
- Review operations never modify Evidence or delete ConflictRecords.
- Review notes are audit context, never written into Evidence.
- KnowledgePoint IDs and relationships are preserved through review.
- Deterministic ordering everywhere; stable business IDs for
  idempotency.

## ASR Provider Layer (Task 17)

The ASR provider layer abstracts all audio transcription behind a
replaceable provider contract:

```text
Path
  ->
AudioMaterialValidator
  ->
AudioInput
  ->
ASRProvider
  ->
TranscriptionResult
```

### Contract

- `ASRProvider` (abstract base class) defines the core method:
  `transcribe(audio_input: AudioInput) -> TranscriptionResult`
- Providers must not re-validate files; that is the responsibility of
  `AudioMaterialValidator` (Task 16).
- `TranscriptionResult` carries an optional `Transcript` and a tuple of
  structured error dicts; an empty transcript is a valid result, not an
  error.

### Error Model

- `ASRProviderErrorCode`: `INVALID_INPUT`, `UNAVAILABLE`,
  `CONFIGURATION_ERROR`, `PROCESSING_ERROR`, `UNSUPPORTED`
- `ASRProviderError` hierarchy with per-code subclasses and a
  `retryable` metadata flag:
  - retryable: `UNAVAILABLE`, `PROCESSING_ERROR`
  - not retryable: `INVALID_INPUT`, `CONFIGURATION_ERROR`, `UNSUPPORTED`
- No retry engine is implemented; `retryable` is metadata only.

### Mock

- `MockASRProvider` is deterministic (sha256-of-path based), produces
  timestamps but does NOT fake language detection or speaker
  identification.  Supports preconfigured transcripts/segments and
  failure simulation via `set_failure` / `fail_with`.

### Integration

- `ASRTranscriptionEngineAdapter` adapts any `ASRProvider` to the legacy
  `TranscriptionEngine` API so `ClassSessionProcessor` keeps working
  unchanged.
- `ClassSessionProcessor(asr_provider=...)` accepts an `ASRProvider`
  directly; the existing `transcription_engine` parameter remains for
  backward compatibility.  No default real provider is chosen; the
  processor still falls back to `MockTranscriber` when nothing is
  injected.

Task 17:

Provider abstraction only.

Real ASR:

Not implemented.

Whisper:

Not implemented.

## Local Whisper Provider (Task 18)

The first real ASR provider on top of the Task 17 contract:

AudioMaterialValidator
        |
AudioInput
        |
ASRProvider
        |
LocalWhisperProvider
        |
TranscriptionResult
        |
Evidence

### Runtime

- Selected runtime: faster-whisper (CTranslate2); openai-whisper is
  NOT used. Audio decoding goes through PyAV, which bundles FFmpeg -
  no system ffmpeg binary is required.
- Default model base, device cpu, compute type int8: the CPU path is
  the supported path and works without a GPU.
- device=cuda is supported as an optional path; when CUDA is
  explicitly configured but unavailable, the provider raises a stable
  UNAVAILABLE error. There is no silent fallback to CPU.

### Configuration

- WhisperConfig extends ASRProviderConfig and carries only
  Whisper-specific fields: model_name, device, compute_type,
  language. These fields do not leak into the generic
  ASRProviderConfig.
- Configuration is validated at provider construction time:
  unsupported model names, devices, compute types, or language codes
  fail fast with CONFIGURATION_ERROR, before any transcribe() call.
- language is an explicit ISO 639-1/639-2 setting (e.g. es, ca).
  When set it is passed through to the runtime. No automatic language
  detection is performed and language is never guessed from file
  names. Unknown codes map to TranscriptLanguage UNKNOWN.

### Model lifecycle

- The model is loaded lazily on the first transcribe() and reused
  for every subsequent call on the same provider instance
  (model_load_count tracks this; guarded by a lock so concurrent
  first calls still load exactly once).
- close() releases the model. The provider does not destroy the
  shared model after each transcription.
- Model files are downloaded by the runtime into the Hugging Face
  cache outside the repository and are never committed.

### Output mapping

- Whisper segments map to TranscriptSegment: text is stripped and
  kept in the original language (no translation, no rewriting),
  start/end map to the segment timestamps, chronological order is
  preserved, speaker is always empty (no diarization, no guessing),
  and fully empty segments are filtered out.
- An empty recognition result is a legal TranscriptionResult with an
  empty segment list; a runtime failure is a stable provider error
  (PROCESSING_ERROR). The two are never conflated.
- Invalid timestamps returned by the runtime (start < 0 or end <
  start) are rejected with a processing error, never silently
  repaired.

### Error mapping (Task 17 model)

- non-AudioInput or path-less input -> INVALID_INPUT (runtime is
  never called for invalid input)
- missing file after validation / model or runtime load failure /
  CUDA explicitly requested but unavailable -> UNAVAILABLE
- runtime inference failure -> PROCESSING_ERROR
- task 17 ASRProviderError subclasses pass through unwrapped

Task 18 capability status:

Local Whisper: Implemented

Long audio segmentation: Not implemented

VAD: Not implemented

Diarization: Not implemented

Language auto-detection: Not implemented

## Long Audio Processing Layer (Task 19)

### Overview
Deterministic fixed-window segmentation and transcript assembly for audio
longer than a single ASR call can comfortably handle. Sits between
`AudioInput` and the `ASRProvider` contract: the provider is reused for
every chunk, so a `LocalWhisperProvider` loads its model exactly once.
No VAD, no speaker diarization, no automatic retry, no parallel ASR,
no LLM, no translation. Text is never modified — only timestamps,
ordering, and assembly.

### Pipeline
```
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
```

### Configuration
`LongAudioConfig(chunk_duration_seconds, overlap_seconds)` with
`validate()` failing fast on C <= 0, O < 0, O >= C (LongAudioConfigError).
Single source of truth: `DEFAULT_CHUNK_DURATION_SECONDS = 300.0` and
`DEFAULT_OVERLAP_SECONDS = 0.0`. All timestamps in seconds.

### Components
- `AudioChunk` (frozen dataclass): chunk_index, start_time, end_time,
  source_audio reference for traceability.
- `generate_chunks(duration, config, source_audio)`: deterministic
  windows; step = C - O; start_k = k * step; end_k = min(start_k + C, D).
  No empty tail, no window past the end. ValueError on negative/NaN/
  infinite duration.
- `AudioDurationProvider` (Protocol) + `PyAvDurationProvider`:
  metadata-only duration read via PyAV container/stream duration;
  lazy `av` import; no full decode.
- `build_long_audio_chunk(chunk, source_audio)`: slices source audio
  into a uniquely named temp WAV in the system temp dir
  (`clases_chunk_<uuid>_<idx>.wav`), 16 kHz mono s16 via PyAV
  resampler, seek optimization for non-zero start. Preserves original
  material and metadata (source_audio_path, chunk_index).
- `cleanup_chunk_audio(chunk_audio)`: removes the temp file, safe to
  call always.
- `_remap_chunk_segments(chunk, chunk_result, previous_global_end,
  total_duration, overlap_active)`: global = chunk.start + local;
  clamps end to total duration; no rounding; deterministic exact-text
  overlap dedup only when overlap > 0.
- `LongAudioProcessor(process)`: sequential, reuses one ASRProvider for
  all chunks, chunk 0 full-duration shortcut reuses the original
  AudioInput (no temp file), failure re-raises with "chunk N: ..."
  message and stops. Empty chunk OK; all-empty -> valid
  TranscriptionResult(segments=[]). Segments sorted by (start, end).
  Language = majority non-Unknown. metadata includes chunk_count,
  config, total_duration, empty_result. material_id preserved.
- `create_long_audio_processor()`: factory mirroring
  `create_local_whisper_provider` / `create_mock_asr_provider`.

### Capability Status
| Capability | Status |
|---|---|
| Fixed-window chunking | Implemented |
| VAD | Not implemented |
| Speaker diarization | Not implemented |
| Automatic retry | Not implemented |
| Parallel ASR | Not implemented |
| LLM / translation | Not implemented |

### Constraints
- Do not modify `knowledge_*.py`; no Evidence / KnowledgePoint /
  Review / translation / LLM / CLI / WebUI changes.
- ASRProvider contract unchanged; the provider is unaware of chunks.
- Chunk 0 shortcut fires only when chunk 0 spans the full duration
  (start == 0 and end == duration).
- Overlap dedup is exact-text only; no fuzzy matching.
- Temp files live in the system temp dir, never in the repository.


---

## ASR Quality Validation Layer (Task 20)

### Purpose

Independent, read-only quality-diagnostics layer that sits between the
Task 18/19 transcription pipeline and any downstream Evidence layer.
It answers one question:

> "Does this TranscriptionResult have obvious structural problems?"

It does NOT answer:

> "Did Whisper hear the teacher correctly?"

Without a human ground-truth transcript, no system can claim to know
whether Whisper's output is linguistically accurate.  The quality
score therefore measures machine-observable structural quality
(timestamp validity, structure, text sanity) — never ASR accuracy.

### Pipeline

```
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
Future Evidence Layer
```

### Components

`TranscriptQualityValidator`
- `validate(transcription, source_duration=None) -> QualityReport`
- `transcription` may be a `TranscriptionResult` (Task 18/19 output)
  or a raw `Transcript`.
- `source_duration` (optional, seconds) enables the out-of-bounds
  check; when omitted the check is skipped and the report records
  `duration_check_available = False`.
- The input is never mutated (immutability is a documented invariant).
- No audio I/O, no ASR call, no network, no LLM, no NLP.

`QualityReport`
- `status`: `VALID` | `WARNING` | `INVALID`
- `quality_score`: deterministic float in [0.0, 1.0]
- `issues`: tuple of `QualityIssue`, in stable order
- `warnings`: subset of issues where severity == WARNING
- `statistics`: `QualityStatistics` (O(n) observable aggregates)
- `duration_check_available`: bool
- `metadata`: material_id, transcript_id, language, thresholds,
  score_meaning

`QualityIssue`
- `code`: `QualityIssueCode` (stable enum)
- `severity`: `QualitySeverity` (INFO / WARNING / ERROR)
- `message`: human-readable string
- `segment_index`, `start_time`, `end_time`: optional, None for
  transcript-level issues

`QualityStatistics`
- `segment_count`, `non_empty_segment_count`, `empty_segment_count`
- `total_text_characters`, `total_transcript_duration`,
  `covered_duration`, `gap_duration`, `overlap_duration`
- `duplicate_segment_count`, `invalid_timestamp_segment_count`

`TranscriptQualityConfig`
- Single source of truth for all heuristic thresholds.
- `to_dict()` / `from_dict()` for serialisation.

### Issue Codes

| Code | Severity | Meaning |
|---|---|---|
| INVALID_TIMESTAMP | ERROR | non-finite, negative, or end < start |
| TIMESTAMP_OUT_OF_BOUNDS | ERROR | end > source_duration (only when duration_check_available) |
| SEGMENTS_OUT_OF_ORDER | ERROR | adjacent segment starts earlier than previous |
| EMPTY_TRANSCRIPT | WARNING | segments list is empty |
| EMPTY_SEGMENT_TEXT | ERROR | segment text is empty or whitespace-only |
| HIGH_EMPTY_SEGMENT_RATIO | ERROR | > 50% of segments have empty text |
| LONG_GAP | INFO / WARNING | total gap; INFO when within 5s, WARNING when above |
| OVERLAPPING_SEGMENTS | INFO / WARNING | total overlap; INFO when within 1s, WARNING when above |
| DUPLICATE_TEXT | WARNING | adjacent non-empty segments with identical text |
| REPETITIVE_TEXT | WARNING | single character repeated 40+ times in one segment |
| VERY_SHORT_SEGMENT | INFO / WARNING | < 0.1s; transcript-level WARNING when >= 50% of segments are short |
| VERY_LONG_SEGMENT | WARNING | > 60s per segment |
| CHUNK_METADATA_INVALID | WARNING | metadata chunk_count is not an integer >= 1 |

### Score Formula

```
score = 1.0
score -= num_error_issues * 0.25        (PENALTY_ERROR)
score -= num_distinct_warning_codes * 0.05  (PENALTY_WARNING)
if empty_ratio > 0.5: score -= 0.10     (EMPTY_RATIO_PENALTY)
score = clamp(score, 1.0 - 0.85, 1.0)   (PENALTY_CAP = 0.85 -> floor 0.15)
score = round(score, 6)
```

- 1.0 = no observable issue detected.
- The score is deterministic: same input -> same score.
- It is explicitly NOT an ASR accuracy estimate; no ground-truth
  claim is made anywhere in the report.
- One penalty per distinct WARNING code (not per occurrence), so a
  wall of identical per-segment warnings does not dominate the score.

### Constraints

- No modification of `Transcript`, `TranscriptSegment`, or
  `TranscriptionResult`.
- No automatic repair, re-transcription, retry, or re-chunking.
- No language identification or language-correctness checking.
- No LLM, NLP, embedding, or semantic evaluation.
- No VAD, speaker diarization.
- No Evidence, KnowledgePoint, or Review generation.
- Stdlib only; zero new third-party dependencies.

### Capability Status

| Capability | Status |
|---|---|
| Structural quality validation | Implemented |
| Timestamp validation | Implemented |
| Gap / overlap / duplicate diagnostics | Implemented |
| Empty / whitespace text diagnostics | Implemented |
| Deterministic quality score | Implemented |
| Structured QualityReport | Implemented |
| Automatic transcript repair | Not implemented |
| LLM quality evaluation | Not implemented |
| Semantic accuracy evaluation | Not implemented |
| Ground-truth accuracy measurement | Not implemented |

### Tests

- `tests/test_transcript_quality.py`: 65 tests
  - 64 deterministic unit tests (no Whisper runtime required)
  - 1 real-Whisper integration test gated with `@pytest.mark.integration`
  - Baseline before Task 20: 546 tests
  - Final suite: 546 + 65 = 611 tests


## Task 21 - PDF / DOCX Document Input & Parsing Layer

### Purpose

Deterministic, evidence-first reading of PDF and DOCX course materials
(syllabi, lecture notes, handouts). Answers: "What is in this file,
page by page / paragraph by paragraph?"

### Pipeline position

```
PDF / DOCX file
  |
DocumentMaterialValidator    (file-level checks only, no content reads)
  |
DocumentValidationResult     (stable error codes; valid / invalid)
  |
DocumentInput                (file identity: path, type, material link)
  |
create_document_parser(type) (dispatch: PDF | DOCX)
  |
ParsedDocument               (blocks + metadata + status + errors)
  |
DocumentBlock                (text + location + deterministic id)
```

### Deterministic ids

- document_id: "document-" + sha256(document_type | posix_path | file_size)[:16]
- block_id:    "docblock-" + sha256(document_id | block_type | location_key | text)[:24]

No uuid4 is used in any business id.  mtime is intentionally excluded
from document_id so an unchanged file's identity is stable across
re-indexes.

### Block granularity

- PDF: one DocumentBlock per page (page-level, block_index 0, type TEXT).
  No heading inference in v1.
- DOCX: one block per non-empty paragraph; paragraph_index advances
  past empty paragraphs.  HEADING only when the paragraph style name
  is "Heading N" (case-insensitive prefix match) or "Title".  One TABLE
  block per table; cells joined " || " within a row, rows joined " | ".
  Image parts (media/*.png/.jpg/...) are counted into metadata
  (image_count) without OCR or content extraction.

### Status values

- PARSED: at least one non-empty block extracted.
- PARSED_EMPTY: parse succeeded, zero content (scanned PDF, empty DOCX).
  A scanned-or-no-text-layer PDF is PARSED_EMPTY with
  metadata["scanned_or_no_text_layer"] = True - not an error.
- FAILED: parser-level problem; errors[] carries a stable
  DocumentParserErrorCode.

### Stable parser error codes

INVALID_DOCUMENT, PASSWORD_PROTECTED, PARSER_ERROR,
PARSER_UNAVAILABLE, UNSUPPORTED_FORMAT.

### Dependencies

pypdf (PDF text extraction) + python-docx (DOCX body).  Both are
local, offline, deterministic text-extraction libraries.  Added to
requirements.txt: pypdf>=3.0,<7.0, python-docx>=1.0,<2.0.

### Explicitly NOT done here (by design)

LLM, OCR, ASR, translation, NLP, embedding, semantic extraction,
KnowledgePoint / KnowledgeStructure / Review generation, automatic
repair / reordering / cleanup of source text.  No modification of the
Knowledge / Review / Evidence / ASR / Whisper modules.

### Constraints

- File-level validation is read-only: no bytes read, no parser run.
- Same input file + same file state -> equal ParsedDocument (ids,
  block order, texts, status, errors) on repeated calls.
- Parser failures are carried in ParsedDocument.status + errors, never
  raised; only missing / non-file / unsupported-extension inputs to
  parse_document() raise ValueError.
- material_type is MaterialType.SYLLABUS for both .pdf and .docx,
  matching material_index._EXT_TO_TYPE.

### Capability Status

| Capability | Status |
|---|---|
| PDF text extraction (per page) | Implemented |
| DOCX paragraph / heading / table extraction | Implemented |
| Deterministic document / block ids | Implemented |
| Password-protected PDF detection | Implemented |
| Corrupt-file structured failure | Implemented |
| Scanned / no-text-layer diagnosis | Implemented |
| DOCX image-part counting (no OCR) | Implemented |
| OCR of scanned pages | Not implemented |
| LLM / semantic extraction | Not implemented |

### Tests

- `tests/test_document_input.py`: 79 tests
  - 76 deterministic unit tests (no network, no model download)
  - 3 integration tests gated with `@pytest.mark.integration`
  - Baseline before Task 21: 611 tests
  - Final suite: 611 + 79 = 690 tests

## Task 22 - Document Evidence Extraction & Traceability

### Pipeline position

```
ParsedDocument (Task 21 output, in-memory)
  |
DocumentEvidenceExtractor          (src/document_evidence.py)
  |
Evidence                           (EvidenceType.DOCUMENT,
                                   deterministic doc-evidence-<24-hex> ids)
  |
existing Evidence integration      (EvidenceExtractor routing,
                                    EvidenceIntegrator, KnowledgePipeline)
```

### Component: DocumentEvidenceExtractor

- `DocumentEvidenceExtractor.extract(parsed_document) -> list[Evidence]`
  and `extract_as_result(...) -> DocumentEvidenceResult` (carries a
  structured status + statistics, e.g. skipped_empty_blocks).
- Accepts a ParsedDocument object, a ParsedDocument.to_dict() mapping
  (re-hydrated via ParsedDocument.from_dict), or None.  None and
  non-document inputs yield [] + INVALID_INPUT status, never a raw
  AttributeError.
- PDF / DOCX blocks: one non-empty DocumentBlock -> one Evidence, in
  original document order (never re-sorted by text / hash / length).
- Empty or whitespace-only blocks are skipped and counted in
  statistics; they never produce Evidence.
- ParsedDocument.status == FAILED or PARSED_EMPTY -> no Evidence
  (PARSED_EMPTY scanned PDFs stay PARSED_EMPTY, no OCR attempted).

### Deterministic Evidence identity

```
evidence_id = "doc-evidence-" + sha256(
    document_id | document_type | block_type | location_key | text
)[:24]
```

- Built from the same components as the deterministic DocumentBlock
  id, extended with document_type so identical PDF/DOCX blocks cannot
  collide.  No uuid4, no datetime.now(), no randomness.
- Deduplication is exact-deterministic only: repeated blocks with the
  same (document_id + location + text) map to one Evidence.  Same text
  at different locations (cross-page, cross-document) stays distinct.
  No fuzzy / semantic / embedding dedup.

### Source traceability

Each Evidence carries:

- source_reference (Task 21 SourceReference-compatible fields):
  material_id (falls back to document_id when the ParsedDocument has no
  material link), page (PDF), paragraph (DOCX), line (block index),
  location (pdf-page-N-block-0 / docx-paragraph-N / table).
- minimal metadata: block_id, block_type, document_type, document_id,
  location label.  The metadata is a traceability pointer, not a copy
  of the DocumentBlock.

### Guarantees / invariants

- DocumentEvidenceExtractor preserves original document text:
  DocumentBlock.text == Evidence.content (no translation, no summary,
  no auto-correction, no auto-added context sentences).
- Document evidence is source-traceable to ParsedDocument blocks and,
  through block location, to the original PDF/DOCX position.
- No semantic interpretation, translation, LLM, OCR, ASR, embedding,
  or network access.  No KnowledgePoint generation.
- The input ParsedDocument (and its blocks) is never mutated by
  extract(); both ParsedDocument and Evidence round-trip through
  to_dict()/from_dict() losslessly.
- Reused Task 21 parsing: the extractor never re-reads the source
  file; it only consumes the ParsedDocument in memory.

### Unified extractor wiring

`EvidenceExtractor.extract()` (src/evidence_extractor.py) now routes
`.pdf` / `.docx` materials through parse_document() +
DocumentEvidenceExtractor() so the existing multi-source pipeline
produces DOCUMENT-type evidence; note / audio / image behaviour is
unchanged.

### EvidenceType addition

`EvidenceType.DOCUMENT = "document"` added to src/models.py.  Existing
Evidence values are untouched; from_string("document") resolves to
DOCUMENT, all legacy values keep their mapping.

### Constraints

- No new third-party dependencies: stdlib only (hashlib, dataclasses,
  enum, typing, copy-free design).
- No re-parsing: extractor consumes Task 21 output exclusively.
- Ordering: document order preserved; duplicate blocks collapse to a
  single deterministic Evidence.
- Status semantics: FAILED -> [] ; PARSED_EMPTY -> [] ; PARSED ->
  evidence for each non-empty block.


## Task 23 - Unified Evidence Store & Evidence Lifecycle

A single, process-wide EvidenceStore (src/evidence_store.py) now acts as
the authoritative in-memory Evidence registry for the whole project.  All
extractors (audio/transcript, OCR, notes, PDF, DOCX) feed the same store;
there are no per-source parallel stores.

### Pipeline

`	ext
                    +-------------------+
                    | Audio / Transcript|
                    +-------------------+
  board image ->    +-------------------+
                    | OCR               |
  notes     ->      +-------------------+
  PDF       -> EvidenceExtractor -> +-------------------+
  DOCX      -> (Task 21/22 wiring)   |  EvidenceStore    |
                                     +---------+---------+
                                               |
                          +-------------------+-----------------+
                          |                     |                 |
                    canonical key        lifecycle          statistics
                    (dedup)        (ACTIVE / RETIRED)   (deterministic)
`

### Canonical identity

`	ext
canonical_key = "evidence-key-" + sha256(
    evidence_type | source identity | source location | exact content
)
`

Hashed inputs are the explicit canonical fields of Evidence +
SourceReference:

`	ext
evidence_type.value
content (verbatim, exact bytes)
source_reference.material_id
source_reference.location
source_reference.timestamp_start
source_reference.timestamp_end
source_reference.page
source_reference.line
source_reference.paragraph
`

Canonicalization rules (documented in src/evidence_store.py):

- JSON canonical form: sort_keys=True, ensure_ascii=False,
  separators=(",", ":").
- No str()/repr() of the whole object: only the explicit fields above
  enter the hash; stable across runs, platforms, and Python versions.
- No datetime.now(), no uuid4, no randomness anywhere in the module.
- Numeric timestamps are canonicalized to their 16-significant-digit
  decimal form so that 1.0 and 1.00 map to the same key, while the
  stored Evidence field is never rewritten.

Identity semantics:

- evidence_id  = stable external identity (set by the producer).
- canonical_key = business / dedup identity (computed, deterministic).
- The two must agree: an incoming Evidence whose evidence_id already
  exists under a *different* canonical key is REJECTED (first-seen
  record wins); the store never silently overwrites.

### Deduplication policy

`	ext
Same source + same location + same content -> 1 Evidence (DUPLICATE)
Same text from different sources/sources-locations -> distinct records
Content-only merge (PDF "x" + DOCX "x")            -> never performed
`

Cross-source provenance is preserved by design: two evidences that
carry the same text but different material_ids stay separate records,
because the source itself is information.  No semantic, fuzzy,
embedding-based, or "similar" deduplication exists anywhere in the
store.  Reprocessing the same material (e.g. re-running PDF parse +
extract) adds zero new records; the batch is fully idempotent.

### Mutation protection

Evidence objects are deep-copied into the store on add() and deep-copied
out of the store on every read/query (get, all, get_by_*).  Mutating
an Evidence after store.add() can neither change the stored content,
the canonical-key index, nor the stored evidence_id.  The store exposes
no public index mutation surface (all indexes are private; only the
documented API methods touch them).

### Query API

All queries are read-only, deterministic, and return insertion order
(filtered).  Returned objects are defensive copies.

`	ext
get(evidence_id)             -> Evidence | None   (any lifecycle state)
contains(evidence_id)        -> bool
all(active_only=True)       -> list[Evidence]     (insertion order)
count(active_only=True)     -> int
get_by_type(type)           -> list[Evidence]
get_by_source(material_id)  -> list[Evidence]
get_by_document(doc_id)     -> list[Evidence]    (via metadata)
get_by_session(session_id)  -> list[Evidence]     (via metadata)
get_by_location(page)       -> list[Evidence]
`

No full-text search, no SQL-like query language, no semantic search.

### Statistics

`	ext
total_count, count_by_type, count_by_material,
count_by_document, unique_materials, unique_documents
`

All counts, no Evidence objects; deterministic (sorted key order);
independent of evidence identity (statistics never feed back into the
canonical key).

### Snapshot / restore

`	ext
store.to_dict()
  -> {"schema_version": 1, "evidences": [ {evidence fields...,
      "state"} in insertion order ]}
`

- Snapshot contains Evidence data only; derived indexes are never
  serialized and are rebuilt on restore.
- from_dict() validates everything first, builds a temporary store,
  and only on full success returns it (atomic restore; no half-built
  store escapes on malformed input).
- Malformed snapshots raise EvidencePersistenceError:
  non-mapping data, wrong/missing schema_version, evidences not a
  list, non-mapping items, malformed Evidence fields, unknown state,
  whitespace-only content, duplicate canonical key, duplicate
  evidence_id.

### File persistence

`	ext
store.save(path) / EvidenceStore.load(path)
`

- JSON, UTF-8, ensure_ascii=False (Chinese / Spanish / Catalan text
  round-trips verbatim).
- Atomic replace: write .tmp in the same directory, flush, then
  os.replace() (atomic on Windows/NTFS and POSIX).
- load() of a missing or corrupted file raises EvidencePersistenceError;
  no partial store is ever returned.

### Lifecycle

`	ext
ACTIVE  = Evidence is current and queryable (default)
RETIRED = kept for traceability, hidden from default active queries
`

- retire() / restore() are idempotent; ID, content, source reference,
  and history are preserved; physical deletion of stored Evidence is
  not provided (only clear() empties the store outright, which is a
  deliberate "wipe" operation, not a lifecycle transition).
- RETIRED records stay retrievable by get(id) so history is preserved;
  all()/count()/statistics() default to active_only=True.
- Evidence lifecycle is fully independent from Knowledge Review:
  REJECTED / CONFIRMED and other ReviewStatus values are never
  lifecycle states of Evidence.

### Thread safety

All mutating operations (add, add_many, retire, restore, clear,
snapshot build, index rebuild on restore) take an RLock.  Ten
concurrent threads adding the same Evidence yield exactly one stored
record (verified by test).  Read-only queries build a consistent
snapshot under the same lock.

### Integration compatibility

- EvidenceIntegrator (src/integration.py) keeps deduping relations by
  evidence_id; the store's canonical-key dedup coexists with it without
  changing its behaviour.
- KnowledgePipeline / KnowledgeReviewService continue to operate on
  list[Evidence] structures; the store adds stable identity underneath
  them without rewriting either.
- Regression tests in tests/test_evidence_store.py drive real extractor
  output (NoteEvidenceExtractor, Transcript.to_transcript_evidence(),
  ocr_to_evidence, DocumentEvidenceExtractor over simple.pdf /
  simple.docx) through the store and verify idempotent re-ingestion
  and document traceability.

### Invariants

1. Stable identity: evidence_id + canonical_key survive query,
   snapshot, restore, and process restart; they never change.
2. Source-aware: different provenance (material, page, timestamp range)
   always yields distinct Evidence, even for identical text.
3. Content preservation: Evidence.content round-trips add -> get ->
   snapshot -> restore byte-for-byte; the store never rewrites it.
4. No reasoning: the store is identity + storage + retrieval +
   lifecycle only; it does not judge truth, similarity, contradiction,
   or source priority.
5. Duplicate is not contradiction: the store handles repeated
   ingestion; it never reconciles conflicting content.

### Constraints

- Stdlib only: hashlib, json, threading, dataclasses, enum, copy,
  pathlib, typing.  No new third-party dependency, no database, no
  network.
- schema_version = 1 (extensible; migration not in scope for Task 23).
- Memory model (dict/list); no full-text or inverted index; a 10,000
  Evidence store is handled comfortably (benchmarked in status.md).
## Task 24 - Unified Evidence Ingestion & Production Readiness

A single ingestion service (src/evidence_ingestion.py) now sits between
the source-specific extractors and the EvidenceStore (Task 23).  It
accepts raw Materials of any supported type, routes them to the
appropriate extractor adapter, writes results through the store
(`store.add_many`), and returns deterministic, serializable reports.

### Pipeline

```text
MATERIALS
   |
   +-- NOTE ----+  NoteParser (paragraph grouping on blank lines)
   +-- AUDIO ----+  ASRProvider -> Transcript -> Transcript.to_transcript_evidence()
   +-- IMAGE ----+  OCREngine -> OcrResult -> ocr_to_evidence
   +-- PDF/DOCX --+  DocumentParser -> ParsedDocument -> DocumentEvidenceExtractor
   |
   v
EvidenceIngestionService  (routing + orchestration, no content mutation)
   |
   v
EvidenceStore             (identity, dedup, persistence, lifecycle - Task 23)
   |
   v
IngestionReport / BatchIngestionReport
   |
   v
KnowledgePipeline (downstream, interpretation) -> Knowledge Review (downstream)
```

Note materials are grouped into evidence units on blank-line paragraph
breaks; audio evidence carries timestamp provenance from the
transcription; OCR evidence preserves engine/report provenance; document
evidence carries page/paragraph/table metadata from Task 21/22.

### Identity

Evidence canonical identity (evidence_id + canonical_key) is defined by
Task 23 and is not redefined here.  The ingestion service only
re-scaffolds missing or uuid4-fallback evidence refs to deterministic
`evid-<sha256(canonical inputs)[:16]>` ids; it never normalizes content.

### Dedup

EvidenceStore is the authoritative deduplication layer.  The service
delegates all duplicate handling to `store.add_many()` and performs no
content-based checks of its own.

### Responsibility

```text
Extractor        = extraction (source-specific, pre-existing)
Ingestion        = orchestration (routing, batch execution, reporting)
Store            = persistence / dedup / identity / lifecycle (Task 23)
KnowledgePipeline= interpretation (downstream, out of scope here)
Review           = human validation (downstream, out of scope here)
```

### Failure isolation

Batch ingestion isolates failures per material: one failing source never
aborts or corrupts the batch.  Each material yields exactly one
IngestionReport with status + error code (EXTRACTOR_FAILURE,
STORE_REJECTION, UNSUPPORTED_SOURCE, MALFORMED_OUTPUT); error codes are
typed, structured, and retryable-flagged.  Reports and history contain
no wall-clock timestamps or random ids, so output is fully
deterministic and re-ingestable (reports.to_dict / from_dict).

### Invariants

```text
Invariant 1: Every stored Evidence has valid provenance.
Invariant 2: EvidenceStore is the authoritative deduplication layer.
Invariant 3: Ingestion never performs semantic deduplication.
Invariant 4: Ingestion never modifies Evidence content.
Invariant 5: Repeated ingestion is idempotent.
Invariant 6: Cross-source identical text remains separate.
Invariant 7: Knowledge generation is downstream of EvidenceStore.
Invariant 8: Human review remains downstream of Knowledge generation.
```


## Task 25 - Evidence-Backed Knowledge Assembly & Incremental Generation

A single assembly facade (src/knowledge_assembly.py) now sits between
the EvidenceStore (Task 23) and the KnowledgeStructure (Task 13),
replacing direct pipeline use with an incremental, store-driven API.
It also makes the end-to-end pipeline fast enough to assemble a
1000-evidence store in well under the 120 s no-obvious-O(n^2) bar.

### Assembly pipeline

```text
EvidenceStore (Task 23)
   |
   +-- store.all(active_only)  deterministic insertion order
   |
   v
KnowledgeAssembler (dedup + pipeline delegation)
   |
   +-- EvidenceIntegrator.integrate()  duplicates / conflicts / supporting
   +-- KnowledgeExtractor.extract()    stable kp-<sha256> knowledge ids
   +-- structure.add_conflict / add_knowledge_point / add_relationship
   +-- KnowledgeValidator.validate_structure()  read-only recompute
   |
   v
AssemblyResult (new / updated / conflict ids + structure reference)
```

```text
KnowledgePipeline.process_evidence()
   |
   +-- EvidenceIntegrator.integrate()          duplicate / conflict / supporting
   +-- KnowledgeExtractor.extract()            KP creation / merge
   +-- structure.add_relationship()            supporting-group relationships
   +-- _apply_validation(validate_structure)   read-only status / score
   |
   v
PipelineResult
```

KnowledgeAssembler.process_store() and .process_evidence() share a single
KnowledgePipeline instance, so store-driven and list-driven assembly are
byte-identical.

### Knowledge identity

Knowledge ids are \`kp-<sha256(entity_anchor|language|material|content)[:16>]\`.
Two evidences produce the same KP only when all four anchor fields match;
a 50-evidence rebuild via store vs. direct list therefore yields the same
KP set (verified by \`TestPerformance::test_rebuild_equivalence_50\`).

### Evidence binding

Every KnowledgePoint carries \`evidence_refs\` (the evidence ids that
grounded it) and \`needs_verification\` / \`validation_status\` /
\`knowledge_score\` set read-only by \`KnowledgeValidator\`.
The validator iterates the structure's KPs and recomputes status from
the current evidence base and the structure's conflict records; it never
mutates Evidence or ConflictRecord objects.

### Incremental assembly

`process_store()` / `process_evidence()` are idempotent: resubmitting
the same store leaves the structure unchanged and the result reports
zero new / zero updated ids.  New evidence is merged into existing KPs
(evidence_refs union) when the knowledge anchor matches; otherwise a new
KP is created.  Supporting groups of >= 2 evidence ids produce
RELATED_TO relationships between the two KPs they ground.

### Conflict detection

`EvidenceIntegrator._detect_conflicts()` pre-computes per-evidence
features (normalized text, negated statements, order relations) once,
then runs the all-pairs loop with a fast short-circuit: a pair only
reaches the regex-heavy helpers when BOTH sides carry an order
relation or negated statements.  Each ConflictRecord is assigned its
stable \`conflict_id\` at creation time (no post-pass).  The result
also carries a \`conflict_refs_by_id\` map (conflict_id ->
frozenset of the two evidence refs) so downstream validation can do
O(1) relevance checks instead of re-reading \`evidence_refs\`.

### Validation performance

`KnowledgeValidator.validate_structure()` builds one evidence map and
one inverted conflict index (evidence_id -> [ConflictRecord]) per call
and hands both to every \`validate_knowledge_point()\`.  Each KP now
only inspects conflicts that reference one of its own evidence refs,
which keeps per-point work proportional to the KP's refs rather than
the whole conflict list.  The \`_detect_supporting()\` word-window
scan was likewise pre-computed (per-normalized-text word lists, no
repeated \`str.split()\` in the inner loop).

### Provenance

AssemblyResult exposes \`evidence_ids\`, \`new_knowledge_point_ids\`,
\`updated_knowledge_point_ids\`, \`new_relationship_ids\`,
\`conflict_ids\`, and \`structure\`.  \`KnowledgeAssembler.
evidence_map_for(store)\` and the per-KP material / document / session
helpers give O(1) provenance lookups over the store without re-scanning
it per KP.

### Persistence

`save_structure()` / `load_structure()` round-trip a
KnowledgeStructure (KPs, relationships, conflicts, validation fields)
to / from JSON.  The load path re-validates integrity so a corrupted
or stale file is rejected before it can poison downstream consumers.

### Tests

tests/test_knowledge_assembly.py (created; 14 classes, 104 tests):
assembler construction, store / list API, evidence dedup, KP identity,
incremental idempotency, relationship creation, conflict registration,
needs_verification flags, multilingual non-merge, statistics,
end-to-end fixture chain, save / load round-trip, conflict_refs_by_id
contract, and a TestPerformance class (1000-evidence assembly < 120 s,
repeated-store stability, 50-evidence store-vs-direct rebuild
equivalence).

### Performance

- 1000-evidence assembly (500 unique + 500 duplicate patterns) now
  completes in ~5-6 s wall-clock (was 135+ s before the conflict-refs
  and inverted-index optimizations), comfortably under the 120 s bar.
- The no-obvious-O(n^2) assertion in
  \`TestPerformance::test_1000_evidence_assembly_no_obvious_n2\`
  passes.
- 50-evidence store-driven and direct-list assembly produce
  byte-identical structures (KP id sets, relationship sets, conflict
  sets, validation fields all equal).

### Known limitations

- \`_detect_conflicts\` and \`_detect_supporting\` remain all-pairs
  over the evidence list by design (deterministic, language-agnostic,
  no semantic matching); the performance work eliminates the
  per-KP / per-call overhead, not the quadratic pair scan itself.
- The inverted conflict index only takes effect when
  \`conflicts\` is an \`EvidenceIntegrationResult\` carrying
  \`conflict_refs_by_id\`; a plain \`list[ConflictRecord]\`
  falls back to the reference-set path, and a list without
  pre-computed sets falls back to the original O(n_conflicts) scan.
- \`KnowledgeAssembler.statistics()\` counts KPs and relationships;
  it does not score, rank, or summarise.  Scoring remains
  \`KnowledgeValidator\`'s job.


## Task 26 - Knowledge Organization & Course-Level Knowledge Structure

Organizes EXISTING KnowledgePoints into a stable, queryable,
incrementally updatable course structure. No new KP content is created:
the layer stores reference ids only.

src/knowledge_organization.py

Models
- Topic: deterministic id = "topic-" + sha256(course, parent, name)[:24].
  parent must exist in the same course; cycles rejected via
  _would_create_cycle (walks the parent chain) and via from_dict
  (per-topic parent-chain closure check).
- KnowledgeMembership: Topic -> KP, id = "kmem-" + sha256(topic, kp).
  order_index is ordering-only, never identity; re-ordering refreshes
  the existing membership (same id), never creates a second one.
- SessionKnowledgeMembership: ClassSession -> KP, id = "smem-" +
  sha256(session, kp). Same order_index semantics.
- KnowledgeRelation: directed KP -> KP, id = "krel-" +
  sha256(course, source, target, type)[:24]. Types: PREREQUISITE,
  RELATED, EXTENDS, CONTRASTS, PART_OF (explicit, no inference).
  Self-relations rejected (SELF_RELATION); duplicate quadruple is a
  no-op; reverse direction is a distinct relation.
- CourseKnowledgeStructure: frozen container with
  to_dict()/from_dict() (schema_version = 1; unknown version,
  cross-course topics, dangling refs, cycles, self-relations,
  membership id mismatch, and duplicate entities all raise
  OrganizationValidationError/OrganizationSchemaError before anything
  is returned).

Service
- KnowledgeOrganizationService(course) wraps one Course plus a
  CourseKnowledgeStructure and holds two internal indexes
  (KP -> structure, session -> KP set) rebuilt after every mutation.
- Registration: register_knowledge_structure / _knowledge_point /
  _session are idempotent; session registration is course-isolated
  (CROSS_COURSE_MEMBERSHIP on foreign sessions).
- Topics: add_topic (idempotent; refreshes description/order without
  duplicating), remove_topic (refused while children exist),
  get_topic, list_topics (by optional parent), get_topic_tree
  (roots seeded with an empty path set; children sorted by
  (order_index, topic_id); TOPIC_CYCLE raised if a stored hierarchy
  ever closes on itself).
- Topic<->KP and session<->KP memberships: add/remove/get with
  DANGLING_REFERENCE when absent; get_unassigned_knowledge_points /
  get_uncovered_knowledge_points are set differences over registered
  KPs.
- Relations: add_relation / remove_relation / list_relations /
  get_related_knowledge (outgoing: source==kp; incoming: target==kp;
  both: outgoing then incoming; sorted by relation_id; no transitive
  closure) / get_relation_graph (deterministic node+edge snapshot).
- Coverage: get_coverage_report (course: covered = KP owns >=1
  SessionKnowledgeMembership in this course; ratio =
  covered/total, 0.0 when total is 0), get_topic_coverage_report,
  get_session_coverage_report. Coverage is a session concept only and
  says nothing about validation or review.
- Aggregation: get_validation_summary (counts of unverified /
  supported / conflicted + average knowledge_score over registered
  KPs) and get_review_summary (per-KP latest effective review: the
  last review record in stable review_id order via
  review_records_for_knowledge_point; without records the KP's own
  review_status applies; decisions CONFIRM/REJECT/KEEP_UNVERIFIED
  map to CONFIRMED/REJECTED/KEPT_UNVERIFIED). The four aggregates
  (organization, validation, review, coverage) never leak into each
  other.
- Ingest: ingest_knowledge_structure (registers the structure's KPs
  and links all to one session, idempotent, no topic assignment, no
  validation/review mutation), ingest_session_knowledge_points.
- Serialization: save_to_dict() -> snapshot dict (structure dict +
  registered KP / session id lists); classmethod from_snapshot()
  restores and re-validates via CourseKnowledgeStructure.from_dict.
- Thread safety: every mutating op takes an RLock (EvidenceStore
  convention); read-only queries return frozen copies.
- Determinism: every business id is prefix + sha256(canonical_json)[:24];
  no uuid4 / datetime.now / random / builtin hash(). All listings are
  sorted; serialization is key-stable.


## Task 27 - Course Knowledge Coverage & Gap Analysis Layer (`src/knowledge_coverage.py`)

Pure, deterministic analysis layer on top of Task 26
(`KnowledgeOrganizationService`).  It reads the existing course
knowledge organization (topics, memberships, sessions, KPs, and their
validation/review state) and produces immutable structural reports.
No auto-fix, no inference, no semantic matching, no student model,
no recommendations, no translation, no LLM, no embedding, no database.

### Module structure

- `KnowledgeCoverageStatus` (COVERED / UNCOVERED) - structural
  session coverage only; deliberately no mastery semantics.
- `KnowledgeGapType` (UNASSIGNED, UNCOVERED, CONFLICTED, UNVERIFIED,
  REVIEW_PENDING, in that declaration order) - data-state markers
  only, never claims about student deficiency.
- `KnowledgeGap`, `KnowledgeCoverageReport`, `TopicCoverageReport`,
  `SessionCoverageReport`, `SessionCoveragePoint`,
  `CourseCoverageTimeline`, `KnowledgeFrequency`, `CourseGapReport`,
  `ConflictCoverageItem`, `KnowledgeOrphanReport`,
  `KnowledgeEvidenceCoverage`, `ValidationReviewMatrix`,
  `CourseKnowledgeStatusSummary` - frozen, serializable reports
  (`schema_version = 1`; unknown versions raise
  `CoverageSchemaError`).
- `KnowledgeCoverageAnalyzer` - the read-only entry point.  It
  builds one snapshot of the service per instance; all reports run on
  that snapshot, so re-analysis is idempotent and later service
  mutations never leak into already-returned reports.

### Coverage semantics

- Covered = a KnowledgePoint owns at least one
  `SessionKnowledgeMembership` in this course.
- Assigned = a KnowledgePoint owns at least one Topic membership.
- Uncovered / unassigned / conflicted / unverified / review-pending
  are independent data states; coverage says nothing about
  ValidationStatus, ReviewStatus, or student mastery.
- `conflict_count` on `ConflictCoverageItem` counts unresolved
  ConflictRecords touching the KP's evidence refs when a
  `conflict_resolver` is supplied to the analyzer; otherwise it
  degrades to a deterministic state marker (1 when CONFLICTED, else
  0) without re-implementing conflict detection.

### Deterministic ordering guarantees

- KP ids: ascending string order.
- Topics: `(order_index, topic_id)`, `None` sorts last (Task 26
  `_order_key` semantics).
- Sessions: positive `session_number` ascending; unnumbered sessions
  after all numbered ones; tie-break by `session_id`.
- Gap types inside a `KnowledgeGap`: declaration order of
  `KnowledgeGapType`, never alphabetical.
- Sets are used for counting only; every public list/tuple is
  deterministically sorted, so reports are insertion-order
  independent.

### Known limitations

1. Coverage describes structural course/session coverage only.
2. It does not measure student mastery.
3. Uncovered does not mean unnecessary.
4. Unverified does not mean false.
5. Conflicted does not trigger automatic resolution.
6. Relations are not used for automatic transitive inference.
7. No semantic similarity is performed.
8. No automatic topic assignment is performed.
9. No learning recommendations are generated.
10. Session chronology depends on the ordering semantics available in
    ClassSession (`session_number`); calendar dates are not used.

## Task 28 - Knowledge Dependency & Prerequisite Analysis Layer (\src/knowledge_dependency.py\)

Pure, deterministic dependency analysis on top of Task 26
(\KnowledgeOrganizationService\).  It reads the existing
PREREQUISITE relation graph (source = prerequisite, target =
dependent) and produces immutable analysis reports.  No
relations are created, no auto-fix, no student inference, no
recommendations, no LLM, no database.

### Module structure

- \DependencyStatus\ (OK / CYCLE_DETECTED) - analysis state;
  cycle members and their downstream get a CYCLE_DETECTED state
  with depth -1, never an infinite or growing depth.
- \DependencyCycle\ - frozen cycle report; deterministic
  \cycle-<sha256(course_id + canonical node path)[:24]>\ id,
  rotated to start at its smallest node; relation ids close the
  loop (first..last edge back to the start node).
- \DependencyCoverageItem\, \DependencyCoverageReport\ -
  per-KP prerequisite coverage: which prerequisites are covered
  by >=1 session membership vs uncovered; explicitly NOT a
  student-mastery statement.
- \DependencyAnalysis\ - full course analysis bundle: status,
  cycles, depth map, coverage report; all frozen with
  \schema_version = 1\ (unknown versions raise
  \DependencySchemaError\).
- \KnowledgeDependencyAnalyzer\ - read-only entry point; one
  lazy frozen \_DependencySnapshot\ per instance (Tarjan SCC
  over the PREREQUISITE subgraph plus per-KP adjacency), so
  re-analysis is idempotent and later service mutations never
  leak into already-returned reports.

### Analysis semantics

- \get_prerequisites\ / \get_dependents\ - direct PREREQUISITE
  neighbors, sorted; only explicit relations are read, never
  auto-created transitive edges.
- \get_prerequisite_chain\ / \get_dependent_chain\ - one hop
  in the direction implied by the relation orientation.
- \get_prerequisite_closure\ / \get_dependent_closure\ -
  transitive reachability (analysis result only; no new
  relations, no materialization).
- \detect_cycles\ - strongly connected components with more
  than one node, each rendered as a deterministic
  \DependencyCycle\; a self-loop (A -> A) is handled as a
  two-entry cycle, never unbounded recursion.
- \get_dependency_depth\ - longest prerequisite chain on
  cycle-free regions (memoized); cycle members and anything
  reaching a cycle return (CYCLE_DETECTED, -1).
- \get_prerequisite_coverage_report\ / \nalyze_course\ -
  coverage + full bundle; cross-course and unknown-schema
  payloads are rejected with stable errors.

### Determinism & isolation

- Cycle ids, depth maps, coverage items: all derived from
  sorted iteration; insertion-order independent.
- Course isolation: snapshot is built from the analyzer's
  service; relations from other courses never mix in.
- Reconstruction: \DependencyAnalysis.save_to_dict\ /
  \rom_snapshot\ round-trip; unknown schema versions raise
  \DependencySchemaError\.
- 1000-KP / 2000-relation performance: single O(N + R) snapshot
  build, chain/closure queries O(N + R) via memoized DFS.


## Task 29: Knowledge Learning Representation & Grounded Explanation Layer

- **Module**: `src/knowledge_learning.py`
- **Purpose**: deterministic, evidence-grounded *readable* representation of a
  single `KnowledgePoint` for one explicitly-specified language. No LLM, no
  auto-translation, no invented facts.
- **Key types**
  - `LearningErrorCode` (INVALID_INPUT, KNOWLEDGE_POINT_NOT_FOUND, EVIDENCE_NOT_FOUND,
    EMPTY_CLAIM_TEXT, EMPTY_EVIDENCE_REF, INVALID_SCHEMA_VERSION, INVALID_STATE,
    LANGUAGE_NOT_AVAILABLE, NOT_AVAILABLE)
  - `LearningRepresentationError` / `LearningValidationError` / `LearningSchemaError`
    (stable `.code` + `.message`)
  - `BuildStatus` (OK / NOT_AVAILABLE / LANGUAGE_NOT_AVAILABLE)
  - `LearningClaim` (frozen): `claim_id = claim-<sha256(stripped text + ":" +
    sorted-deduped evidence ids)[:24]>`; `create()` validates non-empty text and
    non-empty evidence refs; dedupes + sorts evidence ids; `to_dict`/`from_dict`
    with tamper guard (stored id must match recomputed).
  - `LearningRepresentation` (frozen): `representation_id = representation-<sha256([kp_id,
    lang, sorted claim ids, sorted union evidence ids, version])[:24]>`;
    `source_evidence_ids` = deterministic union of every claim's evidence;
    claims deduped by `claim_id` and order-independent; `schema_version = 1` in
    `to_dict`; `from_dict` rejects unknown schema and corrupted ids.
  - `GroundedKnowledgeBuilder(registry, language_index=...)`: registry may be an
    `EvidenceStore` (`.contains()`), a Mapping, or an iterable of `Evidence`.
    `build(kp, lang, ...) -> (BuildStatus, Optional[LearningRepresentation])`:
    - no evidence refs -> `NOT_AVAILABLE`
    - missing evidence id in registry -> raises `EVIDENCE_NOT_FOUND`
    - requested language not backed by any language-known evidence ->
      `LANGUAGE_NOT_AVAILABLE` (never auto-translates)
    - all content (title/explanation/key_points/examples/claims) is caller-supplied;
      the builder only validates, computes the deterministic id, and assembles
      the frozen object.
    - `build_with_citations` raises `LearningValidationError` instead of returning
      the status tuple.
  - `LearningRepresentationProvider`: stable structural seam for a future
    (non-LLM or remote) provider; not implemented in this task.
- **Test**: `tests/test_knowledge_learning.py` (32 tests: deterministic ids,
  insertion-order independence, multilingual ES/CA/ZH Unicode round-trip,
  no-hallucination, missing/invalid evidence, empty evidence -> NOT_AVAILABLE,
  language mismatch -> LANGUAGE_NOT_AVAILABLE, serialization round-trip +
  tamper guards, frozen immutability, EvidenceStore-compatible registry).

## Task 30: Student & Learning State Layer

- **Module**: `src/student_learning.py`
- **Purpose**: first student-facing layer; event-log source of truth,
  derived state snapshots; strict data-boundary separation from the
  knowledge layer.
- **Key types**
  - `StudentLearningErrorCode` (INVALID_INPUT, UNKNOWN_EVENT_TYPE,
    INVALID_SEQUENCE, KNOWLEDGE_POINT_NOT_REGISTERED,
    INVALID_SCHEMA_VERSION, INVALID_STATE)
  - `StudentLearningError` / `StudentLearningValidationError` /
    `StudentLearningSchemaError` (stable `.code` + `.message`)
  - `Student` (frozen, identity-only: `student_id` caller-supplied,
    `display_name` optional; no auth/email/permissions)
  - `LearningState` (NOT_STARTED / EXPOSED / PRACTICING / REVIEWING --
    deliberately NO mastered/weak/strong; no mastery inference)
  - `LearningEventType` (VIEWED / PRACTICED / ANSWERED / REVIEWED)
  - `LearningEvent` (frozen): `event_id = event-<sha256(student|course|kp|type|sequence)[:24]>`;
    deterministic; ANSWERED is a non-advancing observation event.
  - `StudentKnowledgeRecord` (frozen): derived snapshot with sequence-based
    `first_seen_at` / `last_activity_at` (no datetime.now()),
    exposure/practice/answer/correct/incorrect counters.
  - `reduce_events(events)`: deterministic reducer; legal state machine
    NOT_STARTED -VIEWED-> EXPOSED -PRACTICED-> PRACTICING -REVIEWED->
    REVIEWING (terminal); illegal transitions are rejected (state stays
    put); sequence-ordered with event_id tie-break.
  - `StudentLearningLog(student)`: append-only, idempotent log;
    course/kp-scoped sequences; `record_event` / `record_answer`
    (is_correct counters); `events_for` / `all_events`;
    `derive_state` returns a fresh record; `to_dict` / `from_dict`
    with `schema_version = 1` and unknown-version rejection.
- **Data-boundary guarantees**
  - Course isolation: same KP id in two courses is independent.
  - KP isolation: sibling KPs do not share state.
  - Student isolation: one student's log never affects another's.
  - No mutation: the log never writes to `KnowledgePoint` fields
    (validation_status / review_status / content / evidence_refs).
- **Test**: `tests/test_student_learning.py` (34 tests: deterministic
  ids, idempotent duplicate events, legal/illegal transitions, course
  and KP isolation, multiple students, ANSWERED counters without mastery
  inference, serialization round-trip + replay, tamper guard, Unicode
  preservation, KnowledgePoint non-mutation).

## 12. Exercise Layer (`src/exercises.py`, Task 31)

Deterministic, caller-supplied question layer on top of the knowledge
layer:

- **`Exercise`** (frozen, content-addressed): `exercise_id =
  "exercise-" + sha256(canonical payload)[:24]` over course, type,
  prompt, sorted/deduped KP ids, sorted choices, correct choice,
  expected answer, fill-blank payload, explanation, sorted/deduped
  evidence ids, difficulty. Unknown `schema_version` -> stable
  `ExerciseSchemaError`; tampered payload -> `INVALID_STATE`.
- **Types**: MULTIPLE_CHOICE (>= 2 choices, unique ids, correct id
  must exist), TRUE_FALSE (canonical "true"/"false" choice list),
  SHORT_ANSWER (non-empty `expected_answer`; never auto-graded here),
  FILL_BLANK (`FillBlank(blank_id, accepted_answers)` tuple, exact
  matching only in Task 32).
- **Grounding**: `CourseExerciseValidator(course_knowledge_evidence)`
  rejects KP ids missing from the course (`MISSING_KNOWLEDGE_POINT`)
  and, in strict default mode, evidence owned by other KPs
  (`CROSS_KP_EVIDENCE`); `allow_cross_kp=True` accepts any course-
  known evidence and still rejects unknown ids (`MISSING_EVIDENCE`).
- **No auto-generation**: all content is caller-supplied via
  `ExplicitExerciseBuilder`; `ExerciseGenerator.generate()` raises
  `NotImplementedError` (reserved, non-LLM seam).
- **Constraints**: frozen immutability; es/ca/zh content verbatim;
  no uuid/datetime.now/random/hash(); `StudentAnswer -> Evidence`
  remains forbidden (answers are evaluated in Task 32, not stored as
  course evidence).
- **Tests**: `tests/test_exercises.py` (40 tests).

## 13. Answer Evaluation Layer (`src/answer_evaluation.py`, Task 32)

Deterministic, LLM-free grading of student submissions against the
exercise layer:

- **`StudentAnswer`** (frozen): `answer_id = "answer-" +
  sha256(student|exercise|sequence|submitted_value)[:24]`.
  `submitted_value` is kept verbatim (es/ca/zh) -- no
  normalisation.
- **`EvaluationResult`** (frozen): `evaluation_id =
  "evaluation-" + sha256(answer_id|evaluator_version|result)[:24]`;
  `evaluator_version = "exact-v1"` (bump the constant when rules
  change; historical ids stay stable).
- **`ExactEvaluator`**: per-type deterministic rules (spec 32.6):
  MULTIPLE_CHOICE exact choice id; TRUE_FALSE "true"/"false" only;
  FILL_BLANK exact match against the explicit `accepted_answers`
  tuple (no trim / casefold); SHORT_ANSWER exact equality only --
  semantic equivalence ("Madrid" vs "马德里") is UNSUPPORTED, never
  inferred. `PARTIAL` is never emitted (no rubric in this layer).
  Unknown exercise ids -> `UNKNOWN_EXERCISE` (spec 32.12).
- **`AnswerEvaluationLog`**: append-only, idempotent duplicates,
  per-student views (spec 32.11 isolation is structural).
  `answered_event(...)` returns a plain description dict so state
  flows through the Task 30 reducer, never by direct record
  mutation (spec 32.10). Answers/evaluations are NEVER written to
  EvidenceStore or KnowledgePoint (spec 32.9 / global rule 7).
- **Tests**: `tests/test_answer_evaluation.py` (27 tests).

## 14. Study Planning Layer (`src/study_plan.py`, Task 33)

Deterministic, rule-based planning snapshot over the knowledge +
student state (spec 33.x):

- **`StudyReason`**: UNCOVERED_PREREQUISITE / RECENT_INCORRECT /
  LOW_PRACTICE_COUNT / REVIEW_PENDING / UNVERIFIED_KNOWLEDGE /
  CONFLICTED_KNOWLEDGE — hardcoded priority order; no mastery model,
  no LLM ranking, no "weakness" claims.
- **`StudyItem` / `StudyPlan`** (frozen, `plan-<sha24>` ids,
  tamper guard): every item explains itself via explicit
  `reason_codes` + `prerequisite_ids` (spec 33.20); plan is an
  immutable snapshot (spec 33.13).
- **`LearningPath`**: PREREQUISITE-only chains (spec 33.15),
  DEPENDENCY_CYCLE with cycle ids (spec 33.16), uncovered
  prerequisites included but never labelled "weak" (spec 33.17).
- **`StudyPlanner`**: recent-incorrect window and
  minimum_practice_count are caller-supplied (specs 33.9/33.10);
  CONFLICTED feeds REVIEW_PENDING as a content-review signal
  (spec 33.6); deterministic ordering by reason priority ->
  dependency depth -> kp id (spec 33.21).
- **Tests**: `tests/test_study_plan.py` (28 tests, incl. 1000-KP
  performance).


## 15. Unified Application Service / API Layer (Task 34)

A thin, deterministic application layer (package `src/application/`)
that composes the existing domain services and exposes flat-dict DTOs.
It is the intended entry point for any future UI, CLI, or API surface
without leaking domain objects across the boundary.

### 15.1 Modules

- **`errors.py`** — `ApplicationError` base + 8 concrete subclasses
  (`NOT_FOUND`, `INVALID_INPUT`, `ALREADY_EXISTS`, `CONFLICT`,
  `UNAUTHORIZED`, `RATE_LIMITED`, `RESOURCE_UNAVAILABLE`,
  `INTERNAL_ERROR`) and `map_application_error(exc)` which maps
  domain exceptions (`KeyError` -> NOT_FOUND, `ValueError` ->
  INVALID_INPUT, `.code`-tagged errors) to structured
  `ApplicationError` instances. No traceback / message leaks to
  callers beyond the stable, human-readable detail field.

- **`dto.py`** — 8 `*_to_dict` helpers
  (`course_to_dict`, `session_to_dict`, `material_to_dict`,
  `knowledge_point_to_dict`, `evidence_to_dict`,
  `conflict_to_dict`, `review_record_to_dict`,
  `exercise_to_dict`, ...) plus `to_jsonable()`. All return
  plain nested dict/list/scalar structures, never domain objects.

- **`course_service.py`** — `CourseService`: in-memory,
  content-addressed registries for `Course`, `ClassSession`,
  `Material`. All three registries are idempotent (same conceptual
  key -> same stable id). `list_sessions` / `list_materials`
  filter via metadata fields and return sorted DTO lists.

- **`knowledge_service.py`** — `KnowledgeService` (read-only
  projections over `KnowledgeOrganizationService` + coverage/gap /
  dependency / conflict / evidence / review-candidate queries) and
  `ReviewService` (confirm / reject / keep_unverified /
  resolve_conflict / get_review_history, all routed through
  `KnowledgeReviewService` over a per-call KP view). Conflict
  resolution requires explicit evidence selection (CONFLICTED state
  never auto-confirms).

- **`material_service.py`** — `MaterialService`: thin wrapper over
  `CourseService.register_material` + `EvidenceIngestionService`
  (`register`, `ingest`, `batch_ingest`, `status`); all
  idempotent.

- **`learning_service.py`** — `LearningService`: student
  registration, exercise creation (4 types: multiple_choice /
  true_false / short_answer / fill_blank, deterministic content-
  addressed `exercise_id`), answer submission (auto-evaluated via
  `ExactEvaluator`; auto-registered into the student's learning
  log), evaluation lookup, study plan and learning path generation.
  No semantic grading: SHORT_ANSWER non-exact matches are
  UNSUPPORTED by design (spec 32.8).

- **`app_service.py`** — `AppService` facade: composes
  `CourseService`, `MaterialService`, `KnowledgeService`,
  `ReviewService`, `LearningService` over one
  `KnowledgeOrganizationService` + `EvidenceStore` +
  `EvidenceIngestionService`. Exposes `register_knowledge_point`
  (dict in -> DTO out) and `map_error`.

### 15.2 Design Invariants

- No `uuid4` / `datetime.now` / `random` anywhere in
  `src/application/`; all ids are SHA-256 content-addressed
  (domain-generated) so repeated identical calls are exact no-ops.
- Every public method returns a flat dict (never a domain object);
  every error path returns a structured `ApplicationError`, never
  a raw traceback.
- `ReviewService` and `KnowledgeService` snapshot the
  organization layer's registered KPs at construction time; to see
  later-registered KPs, construct the services after those KPs exist
  (documented in `app_service.py` docstring).

### 15.3 Tests

- `tests/test_application_core.py` (28)
- `tests/test_application_knowledge.py` (35)
- `tests/test_application_learning.py` (45)
- `tests/test_application_app.py` (14)
- Total: 122, all passing alongside the 1385 baseline suite
  (14 integration tests still deselected by `-m "not integration"`).

---

## 16. Persistence Layer (`src/persistence/`, Task 42)

### 16.1 Why a layer at all

The domain (`src/*.py`) must not know *where* its objects are stored.
That is not a stylistic preference: the whole evidence-first pipeline is
replayable only because domain objects are pure values, so the same
inputs always produce byte-identical outputs. The moment a domain class
learns about a row id or a connection, replayability is gone.

### 16.2 The layering contract, and how it is enforced

The dependency direction is strictly:

```
api / web  ->  application  ->  domain  ->  (nothing)
                    |
                 backup
                    |
              persistence  ->  domain
```

`persistence` may import the domain (it has to serialise domain
objects); nothing may import `persistence` except the two documented
consumers. This is enforced by `tests/test_persistence_layering.py`
(**26 tests**), not by convention:

| Guard | Mechanism |
| --- | --- |
| `sqlite3` confined to one package | `test_sqlite3_is_imported_only_inside_the_persistence_package` AST-scans every module under `src/` |
| Domain never touches storage | `test_no_domain_module_imports_the_persistence_package` — zero `src/*.py` files may import it |
| Application services never touch storage | `test_application_services_do_not_reach_into_the_storage_layer` — file-level, so a new service cannot "just import it" |
| Top-level consumers pinned | `test_only_the_documented_packages_depend_on_the_persistence_package` — **set equality**, not a subset check |
| No external DB driver | `test_persistence_does_not_import_an_external_database_driver` — bans `psycopg2` / `pymysql` / `redis` / `pymongo` / `sqlalchemy` |
| Package shape | `test_required_persistence_submodules_exist` (`database.py`, `repositories/`, `migrations/`, `models/`), `test_every_repository_module_declares_a_repository_class` |

The two whitelists live in the test file, not in `bootstrap.py`:

```python
ALLOWED_PERSISTENCE_CONSUMERS: dict[str, str] = {
    "backup":      "备份 / 恢复层 (Task 43): 需要 Database.backup_to 的一致性快照 …",
    "application": "装配层 (Task 44): src/application/bootstrap.py 是组合根 …",
}

APPLICATION_PERSISTENCE_ENTRYPOINTS: dict[str, str] = {
    "bootstrap.py": "组合根: 按配置打开 / 迁移 SQLite 库 …",
}
```

Two deliberate design choices here:

1. **Enum + equality assertion, not a directory exclusion.** A subset
   check ("nothing outside this list") silently passes when a new
   consumer appears. Equality fails on *both* an extra and a missing
   entry, so the whitelist cannot rot into stale documentation.
2. **File granularity for the application layer.** `application` is a
   legitimate consumer, but only `bootstrap.py` — the composition root —
   is allowed to open a database. Every business service is still
   forbidden, and a new one cannot sneak in without editing the table.

### 16.3 `database.py` — connection, transaction, migration

`Database` wraps a single `sqlite3.Connection` and exposes
`begin/commit/rollback/transaction()`, `execute/executemany/query`,
`scalar`, `table_names`, `has_table`, `schema_version`,
`applied_migrations`, `migrate`, `integrity_check`,
`foreign_key_violations`, `is_healthy`, and `backup_to`.

Non-obvious behaviours, each with a reason:

- **WAL mode for file databases.** Readers no longer block the writer.
  `:memory:` databases have no WAL and no file — the mode is skipped
  there, not faked.
- **`migrate()` is read-only on an up-to-date database.** It first checks
  the `schema_version` table *read-only*; only if a migration is actually
  pending does it take the write lock. Without this, merely opening the
  app would contend for the write lock on every start, which on a slow
  disk is a visible stall and under concurrency a timeout.
- **Transaction depth is tracked**, and `_abandon_transaction()` exists
  separately from `rollback()` so that a connection which is already
  broken can be unwound without pretending it is healthy.
- **`MAX_SUPPORTED_SCHEMA_VERSION = 999`** is a sanity ceiling, not the
  current version. The current version comes from
  `src.persistence.migrations.latest_version()`.

### 16.4 "Canonical columns + authoritative payload"

Every table has both real columns *and* a `payload` column:

- **Columns** carry everything the database must enforce: foreign keys,
  `UNIQUE` constraints (e.g. `idx_materials_course_filename_hash`),
  `NOT NULL`, and indexes. They are what makes integrity a property of
  the storage engine rather than of Python code.
- **`payload`** is the domain object's own `to_dict()`, serialised. It is
  authoritative for the round-trip: loading a row and re-saving it must
  reproduce the same bytes. Columns are for *querying and constraining*;
  the payload is for *fidelity*.
- **`payload_version`** is stored per row so a future field rename can be
  migrated row-by-row without a table rewrite.

The alternative — deriving the domain object purely from columns — was
rejected because it makes every domain field addition a schema change
plus a hand-written mapper, and hand-written mappers are exactly where
"the field silently defaults to empty after a reload" bugs live.

### 16.5 Error mapping

`sqlite3` raises flat `IntegrityError` strings. `repositories/base.py`
holds `_INTEGRITY_ERRORS`, which maps them to typed persistence errors:

| SQLite text | Persistence error |
| --- | --- |
| `UNIQUE constraint failed` | `DuplicateRecordError` (`code == "DUPLICATE_RECORD"`) |
| `PRIMARY KEY constraint failed` | `DuplicateRecordError` |
| `NOT NULL` / `FOREIGN KEY` / `CHECK` | `PersistenceValidationError` |

Persistence has finer codes than the application's 8
(`DUPLICATE_RECORD`, `STORAGE_CORRUPTED_DATABASE`,
`STORAGE_MIGRATION_FAILED`, `STORAGE_TRANSACTION_FAILED`,
`INVALID_SCHEMA_VERSION`). `map_application_error` collapses them upward
into the 8 public codes, so the storage engine's vocabulary never leaks
to an HTTP caller.

### 16.6 Repositories

`repositories/` holds `base.py` plus **13 concrete repository modules**
(`course`, `session`, `material`, `evidence`, `knowledge`,
`organization`, `review`, `student`, `learning_path`, `exercise`,
`answer`, `evaluation`, `study_plan`). Each declares at least one
`*Repository` class — asserted by test, so an empty stub file cannot be
committed.

`snapshot.py` provides the whole-store save/load helpers
(`save_evidence_store` / `load_evidence_store`,
`save_knowledge_structure` / `load_knowledge_structure`,
`save_organization_structure` / `load_organization_structure`,
`save_student_log` / `load_student_log`), returning counts so callers can
report what was persisted.

### 16.7 Migrations

`migrations/__init__.py` defines the `Migration` dataclass
(`version`, `name`, `statements`, optional `hook`) and builds the chain
via `_chain()`; `latest_version()` is the single source of truth for the
current version. Two migrations exist:

- `m001_initial_schema.py` — the 27 tables listed in
  `docs/data_model.md`.
- `m002_knowledge_organization.py` — adds `database_meta`, alters
  `material_processing`, adds `idx_kp_course_title`.

A migration runs inside a transaction, so a crash mid-migration rolls
that migration back rather than leaving a half-applied schema — proven by
`tests/test_hardening_database_safety.py::TestUnexpectedShutdown::test_crash_during_migration_rolls_that_migration_back`.

### 16.8 The snapshot deadlock (real defect #16)

`Database.backup_to()` previously **hung forever** when a transaction was
open on the same connection. The online-backup API waits for a read lock
that this connection's own write transaction already holds. It is not a
slow path — a bare `db.begin()` with **zero writes** was enough to wedge
it, and the shell's own `timeout 30` had to kill the process.

The fix is an unconditional refusal that never commits on the caller's
behalf:

```python
if self._depth > 0:
    raise TransactionError(
        "cannot snapshot the database while a transaction is open on this "
        "connection: the online-backup API waits forever for a read lock "
        "that this connection's own write transaction already holds. "
        "Commit or roll back first, or snapshot from a separate "
        "Database instance (which is what the backup layer does)."
    )
```

Committing for the caller was rejected as strictly worse than refusing: it
would silently turn a caller's uncommitted work into committed work at a
point the caller did not choose. The production path was never affected
(`BackupService._snapshot_database` opens a *separate* `Database`), but
`backup_to` is public API, and a future caller would have wedged the local
HTTP service with an unreturnable request **while holding the write
lock** — i.e. every other request would queue behind it.

---

## 17. Backup Layer (`src/backup/`, Task 43)

### 17.1 Why not `shutil.copy`

Under WAL, a committed transaction can live entirely in the `-wal`
sidecar until a checkpoint. Copying `classroom.sqlite` alone therefore
produces an archive that **opens successfully and is silently missing
data** — the worst possible failure shape. The only correct snapshot
mechanism is SQLite's online backup API (`sqlite3.Connection.backup`),
which is what `Database.backup_to` wraps.

### 17.2 Modules

| Module | Responsibility |
| --- | --- |
| `manifest.py` | `BackupManifest`, `ManifestFile`, `build_manifest`, `manifest_to_bytes` / `manifest_from_bytes`. `BACKUP_VERSION = 1`, `MAX_SUPPORTED_BACKUP_VERSION = 1`, `MANIFEST_ENTRY_NAME = "manifest.json"`, `DATABASE_ENTRY_NAME = "database.sqlite"` |
| `archive.py` | Untrusted-input hardening for reading archives |
| `service.py` | `BackupService` (create / list / validate / restore), `validate_archive`, `BackupInfo`, `BackupValidation`, `BackupResult`, `RestoreResult` |
| `errors.py` | `BackupError` + 12 concrete subclasses behind `BackupErrorCode` |

### 17.3 An archive is untrusted input

`archive.py` enforces, before extracting anything:

- `is_safe_archive_name` / `check_entry_names` — no absolute paths, no
  `..`, no drive letters, no backslash tricks.
- `is_symlink_entry` — symlink entries rejected (a symlink can redirect a
  later entry's write outside the destination).
- `check_archive_limits` — `MAX_ARCHIVE_ENTRIES = 200_000`,
  `MAX_UNCOMPRESSED_BYTES = 8 GiB`, `MAX_COMPRESSION_RATIO = 2000`
  (a 2000:1 ratio is a zip bomb, not a backup).
- `verify_zip_integrity` — CRC check, plus SHA-256 per file against the
  manifest (`ChecksumMismatchError`).

### 17.4 Restore is a five-step sequence

1. **Validate first.** A corrupt archive must be rejected *before*
   anything on disk is touched.
2. **Version pre-check.** An archive newer than
   `MAX_SUPPORTED_BACKUP_VERSION` is refused
   (`UnsupportedBackupVersionError`) — downgrading silently would corrupt
   the live database.
3. **Safety copy** into `previous/`.
4. **Clear `-wal` / `-shm`.** Stale sidecars belonging to the *replaced*
   database must not be left next to the new one.
5. **Atomic replace** via `os.replace`. On Windows this fails with
   `WinError 5` if any process holds the file open;
   `_translate_replace_error` converts that into `DatabaseInUseError`
   with an actionable message rather than a bare `OSError`.

Restore is deliberately a **Python API**, not an HTTP route. The endpoint
table (below) has no backup route, and the CLI has no backup command. This
is documented in `docs/backup_restore.md` rather than papered over with a
route that would let a web request replace the database.

### 17.5 Tests

`test_backup_service.py` (125), `test_backup_manifest.py` (129),
`test_backup_archive.py` (81), `test_backup_recovery.py` (49).

---

## 18. Configuration, Runtime, Launcher, and Packaging (Tasks 44, 46)

### 18.1 `config.py` — one resolved `AppConfig`

Precedence is **CLI > environment > config file > default**. Every value
has a single owner; there is no "read the env var in three places"
pattern. `--print-config` dumps the fully resolved result, which is the
only reliable way to answer "why is it using that path?".

CLI flags default to `None` rather than to the default value, so "the user
did not pass it" is distinguishable from "the user passed the default".
Without that, CLI defaults would always outrank the config file and the
file would be dead weight.

Validation is strict where a wrong value is destructive:
`database_path` must be inside `data_dir`; `data_dir` must not be under
`src/` or `tests/`; a non-loopback host requires explicit
`allow_remote`; `max_upload_size` must be ≥ 1 KiB.

Unknown `CLASSROOM_*` environment variables produce a warning (a typo in a
variable name otherwise fails silently by falling back to the default).

### 18.2 `runtime.py` — the only sanctioned non-determinism

Deterministic business identity is enforced by
`tests/test_determinism_audit.py` (10 tests): no `uuid4`, no
`datetime.now`, no `random`, no builtin `hash()` anywhere in business
code. All ids are SHA-256 content-addressed. The **only** exception is
`src/application/runtime.py`, which exposes an injectable `Clock`
(`fixed_clock` in tests). Time enters the system at one place, so it can
be pinned in tests and audited in production.

### 18.3 `data_dirs.py` — one layout, one set of safe primitives

`ensure_data_layout(data_dir)` returns a frozen `DataLayout` with
**absolute** paths and guarantees the directories exist. Eight sub-
directories: `materials/`, `audio/`, `images/`, `documents/`,
`database/`, `logs/`, `backups/`, `temp/`. `bucket_for(category)` maps a
material category to its directory and **falls back to `documents/`** for
unknown categories, so a caller can never receive `None` and write to the
CWD.

Path and file safety primitives live here too:
`contains_traversal`, `safe_join`, `sanitize_filename` (strips
`<>:"/\|?*`, Windows reserved stems, caps stems at 120 chars),
`file_sha256`, `atomic_write_bytes` / `atomic_write_text` /
`atomic_copy` (write to a temp sibling, then `os.replace`),
`remove_quietly`, `clear_directory`, `iter_files`.

### 18.4 `bootstrap.py` — the composition root

`build_runtime(config)` returns a `Runtime` dataclass
(`config`, `layout`, `database`, `backup`, `workspace`, `server`,
`logger`, `asr_mode`, `ocr_mode`, `notes`). It is the **only** file in
`src/application/` allowed to import `src.persistence` (§16.2). The
`server` is constructed but not started — binding the port is `main()`'s
job, so tests can assemble a full runtime without occupying a port.

`Runtime` is a context manager (`close_runtime` on `__exit__`), which is
what makes the "assemble a whole app in a test" pattern leak-free.

Mock honesty is enforced here: `_build_asr_provider` /
`_build_ocr_engine` set `asr_mode` / `ocr_mode` to `"mock"` when the real
runtime is absent, and `notes` gains an explicit sentence stating that
the output is **not** a real transcript / recognition result. A mock is
never labelled real.

### 18.5 `launcher.py` — one-command startup

`Launcher` + `validate_environment(config)` → `EnvironmentReport` of
`CheckResult`s, plus `main(argv)`. Supporting primitives:

- **PID / port markers** (`write_runtime_markers`, `remove_runtime_markers`,
  `find_running_pid`, `read_running_port`) stored under the data dir.
- **`_process_alive(pid)`** and **`kill_pid(pid, timeout=5.0)`**. On
  Windows `os.kill(pid, SIGTERM)` does not work for non-console children,
  so the launcher uses the platform-appropriate mechanism and then
  *verifies* the process is gone.
- **`is_port_free(host, port)`** and **`is_our_service(host, port)`** —
  the latter probes the service and checks that the response actually
  contains this application's name. `_marker_points_at_our_service`
  combines the marker file with that probe.
- **`stop` refuses to kill a PID it cannot confirm is ours.** A stale PID
  file is common (crash, manual delete, PID reuse); killing a reused PID
  would terminate an unrelated user process. Refusing with a clear message
  is the only safe behaviour.
- **`_open_browser(url)`** is best-effort; failure is reported, not fatal.

### 18.6 Packaging (Task 46)

Two options were evaluated:

| | Source distribution | PyInstaller one-file exe |
| --- | --- | --- |
| Reproducibility | High — same source, same behaviour | Lower — hidden imports, bundled interpreter, per-build variance |
| Size | Small | 100 MB+ (and larger with Whisper models) |
| Model/data files | Stay on disk, user-visible | Must be bundled or located at runtime |
| Antivirus | No issue | Frequent false positives on unsigned one-file exes |
| Dev install | Unchanged | Requires a separate build step to keep in sync |
| Debuggability | Full tracebacks | Bundled interpreter obscures traces |

**Decision: source distribution with one-command launch scripts.** The
project already ships `scripts/start.bat`, `stop.bat`, `health.bat` plus
the portable interpreter at `Python/pythoncore-3.14-64/`, so "one command
to start" is satisfied without a bundler. PyInstaller is not rejected on
principle — it is deferred, because it would add a build artefact that
must be rebuilt on every change while providing no capability the launch
scripts lack. The evaluation and its reasoning are recorded rather than
silently skipped.

---

## 19. UI Layers (Tasks 39–41) and the Two-Layer i18n (Task 47.8)

### 19.1 Structure

`src/web/` is plain static assets — `index.html`, `app.js`, `styles.css`
— served by `src/api/server.py`. The HTTP layer is `src/api/`:
`server.py` (socket + static files), `router.py`, `responses.py`,
`endpoints.py`. `build_router(workspace)` registers **53 routes**
(38 `GET`, 14 `POST`, 1 `PATCH`) over one `Workspace`, which is itself a
thin façade over `AppService`. The UI never talks to the database: the
layering test set pins the allowed persistence consumers to exactly
`{backup, application}`, so `api` cannot import it.

### 19.2 The two-layer translation mechanism

`app.js` has a `t(key)` with two layers, and the split is load-bearing:

1. **`I18N[lang]`** — symbolic keys for *enumerable* things:
   `state.*`, `status.*`, `nav.*`, `ex.*`. These have natural names
   because the code already refers to them symbolically.
2. **`TRANSLATIONS[lang]`** — keyed by **the Chinese source sentence
   itself**. Chinese is the base language, so the code literally contains
   Chinese strings; giving each of 170 sentences an invented symbolic name
   would add a second thing to keep in sync for no benefit. And because
   `zh` mode resolves a missing key by returning the key verbatim, the
   Chinese rendering is unchanged by construction — while a *missing*
   translation becomes **testable** instead of silently rendering an
   internal key.

```js
function t(key) {
  const table = I18N[state.lang] || I18N.zh;
  if (Object.prototype.hasOwnProperty.call(table, key)) return table[key];
  const fallback = I18N.zh;
  if (Object.prototype.hasOwnProperty.call(fallback, key)) return fallback[key];
  const translated = TRANSLATIONS[state.lang];
  if (translated && Object.prototype.hasOwnProperty.call(translated, key)) {
    return translated[key];
  }
  return key;
}
```

**Why layer 2 exists (real defect #17).** The language picker only
affected the Task 40/41 pages (students / exercises). The seven Task 39
pages — dashboard, knowledge, materials, reviews, course, session,
knowledge-detail — had **hard-coded Chinese** body copy: **193 distinct
CJK literals** outside the table. Selecting *Español* changed the top bar
and nothing else. The fix added `TRANSLATIONS` with **171 es + 171 ca**
entries and rewrote the literals via a codemod
(`cache/_i18n_apply.py`), so no Chinese was ever hand-transcribed and no
transcription error is possible. The tables are generated by
`cache/_i18n_translate.py`, which asserts its key set exactly matches the
extracted keys and fails loudly on any missing or extra entry.

The refactor is provably side-effect-free on Chinese: a pre-change `zh`
render dump (16991 bytes) was captured, and re-dumped after the codemod
and again after the translation tables were written — **byte-identical
both times**.

### 19.3 The cross-lingual leak detector

Every test fixture is pure ASCII. Therefore any CJK appearing in `es` /
`ca` rendered HTML can only have come from untranslated UI copy. That
makes the assertion zero-false-positive:

```python
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
```

This single property is what turns "did we translate everything?" from a
manual review into a test.

### 19.4 Node harnesses — and what they are not

Two Node scripts execute `app.js` page functions under a minimal DOM stub
via `vm.runInContext`:

- `scripts/ui_render_check.js` (Task 41) — answer-leak check.
- `scripts/ui_audit.js` (Task 47.8, **93 checks**) — renders all 11 pages
  in zh / es / ca, and asserts empty / loading / error states, long-token
  rendering, large-data behaviour (1000 KPs, 500 students, 5000
  exercises, bounded request counts so an N+1 is caught), narrow-viewport
  behaviour, and no untranslated CJK. `--dump <lang>` prints every page
  for byte comparison.

**These are not browser tests.** They run the page functions against a
stub DOM; they do not exercise layout, real events, or a rendering engine.
They are described that way everywhere, including in
`docs/troubleshooting.md`, because claiming browser coverage that does not
exist would be worse than having none.

One trap worth recording: `app.js` declares `class ApiError` at top level
inside the `vm` context, which **shadows** any stub of the same name in
the sandbox. `new sandbox.ApiError(...)` therefore produced an object the
renderer could not read, and the error-state check failed for a
harness reason, not a product reason. The fix is
`const ApiError = vm.runInContext('ApiError', sandbox);`.

---

## 20. Production Hardening (Task 47)

### 20.1 The hardening test files

| File | Tests | Focus |
| --- | --- | --- |
| `test_hardening_database_safety.py` | 19 | Unexpected shutdown, partial write, rollback, backup, restore, migration, corrupt DB, duplicate records |
| `test_hardening_input_safety.py` | 18 | Untrusted filenames / paths / media, traversal, oversized and malformed input |
| `test_hardening_truth_safety.py` | 13 | "Never fabricate": unverified content stays unverified, mocks self-label, no silent confirmation |
| `test_hardening_traceability.py` | 6 | Every knowledge point traces to evidence, and evidence to a source material |
| `test_hardening_backup_drill.py` | 14 | Destroy-and-restore drill against the real data directory |

**Why the shutdown tests use subprocesses.** A deliberate rollback and a
hard kill are *different failure modes*: a controlled rollback lets Python
and SQLite finish cleanly, whereas a hard kill relies on WAL recovery
replaying a file on disk. Testing only the first would leave the second
unverified. The children hand-shake via a **marker file**, not stdout —
on Windows a pipe plus a blocking `readline` risks a hang, which is
exactly the class of bug the file is meant to catch. Each crash child
writes a 3 MB uncommitted payload so the row is genuinely flushed to
`-wal` before the kill, and the migration child writes its marker from
inside a migration hook, proving the `CREATE TABLE` had already executed
when the process died.

`TestPartialWrite` deliberately asserts the **bad** behaviour on one
path — that a batch without a transaction leaves a partial write — so that
the transaction is demonstrably load-bearing rather than assumed to be.

### 20.2 The signature defect class

The recurring defect in this project is **"capability exists but was never
wired"** — roughly 17 instances. A capability is implemented, unit-tested
in isolation, and then never called from any real path, so the tests pass
and the product does not have the feature. A variant appears on dormant
code paths: a defect that only manifests when a long-default-branch
optional parameter is finally exercised.

This is why the hardening work favours **end-to-end assertions over unit
assertions**: a test that goes through the real entry point cannot pass
while the feature is unwired. It is also why `docs/status.md` records
"发现并修复的真实缺陷" per task — the list of these is the project's most
useful quality signal.

### 20.3 Test-environment facts

- The portable interpreter `./Python/pythoncore-3.14-64/python.exe`
  (Python 3.14.7) is the only one with all dependencies installed.
- `pytest.ini` registers the `integration` marker; real-engine tests are
  deselected by default with `-m "not integration"`.
- A full regression takes ≈ 4m20s–5m and must be run in the background.
- Three documentation claims were **corrected against the code** during
  Task 47 rather than shipped: backup has no HTTP endpoint and no CLI
  entry; `health` / `--check` are not read-only (they create the data
  directory and database as a side effect); and `run_tests.cmd`'s paths
  were verified working (14 passed, rc=0) after an earlier note claimed
  they were broken.

## 21. Persistence Wiring (Tasks 48–55)

### 21.1 The defect this section fixes

Until Task 48 the application services held every business object **in
memory only**. The persistence layer (`src/persistence/`, Task 42) was
complete and well tested, but nothing called it from the product path:

```
create course -> in memory -> close the program -> data is gone
```

`src/application/persistence_wiring.py` is the missing wire. It is the
only module in the application layer, besides the composition root
(`bootstrap.py`), that is allowed to import `src.persistence`; the
allow-list in `tests/test_persistence_layering.py` names both files and
enforces that no new service reaches into the storage layer.

The wiring is **write-through**: every mutating application operation
flushes to SQLite before returning, rather than buffering until
`close()`. A process can be killed at any moment (Task 54's crash harness
does exactly that), so "flush on exit" would silently lose data.

### 21.2 What is stored, and what is derived

This distinction was decided deliberately in Task 52. The rule is:

> **If a value is a pure function of other stored data, derive it.**
> Storing a derived value creates a second source of truth that can drift,
> and a drift between two "authoritative" copies is far worse than a
> recomputation cost.

| Object | Kind | Rationale |
| --- | --- | --- |
| `Course`, `ClassSession` | **Stored** | user-authored input; no other data implies them |
| Material registry record | **Stored** | user-authored input + the pointer to the stored file |
| `Evidence`, `KnowledgePoint`, `ConflictRecord` | **Stored** | the knowledge truth; provenance lives here |
| `ReviewRecord` | **Stored**, append-only | human decisions are audit history |
| `Student`, `StudentLearningLog`, `StudentAnswer`, `EvaluationResult` | **Stored** | process data; cannot be reconstructed |
| `Exercise` | **Stored** | authored content, including the answer key |
| `StudyPlan` — the **value** | **Derived** | pure function of the knowledge structure + the student's learning log |
| `StudyPlan` — the **snapshot rows** | **Stored**, append-only | audit: which plan the system actually served, and against which student history |
| `LearningPath` | **Derived** | pure function of the dependency graph + target point |
| `Coverage`, `Gaps`, `Dependencies` | **Derived** | pure aggregates over the knowledge structure |
| `StudentDashboard` | **Derived** | a projection composed from the above |

Note the two-line entry for `StudyPlan`. It is the one object whose
*value* and whose *durable representation* have different kinds, and
conflating them would be a real mistake:

- The **value** is recomputed on every call. A snapshot taken before new
  material was ingested describes a world that no longer exists; reading
  it back would hide the change from the student. Deriving is what makes
  `test_the_study_plan_changes_when_the_student_answers` true.
- The **rows** are an append-only audit trail of plans that were
  *served*. They are never deleted and never read back as the answer.

Because `plan_id` is content-addressed, the archive is **verifiable**
rather than merely plausible: recomputing from the same state reproduces
the same `plan_id`, so a row in `study_plans` either matches the
recomputation or it is provably stale. That is the whole point of the
archive, and it is why no clock or counter may enter the id.

The general rule this generalises to: **the database stores inputs and
audit history; it never stores the answer.** No derived value is ever
read back.

Consequences that are pinned by tests
(`tests/test_persistence_plans.py`):

- The `learning_paths` table stays **empty** in normal operation. The
  repository and `save_learning_paths()` exist and are exercised
  directly, but no product path writes a derived value into durable
  storage. `tests/test_persistence_plans.py` asserts the table stays
  empty *and* that the path is recomputed byte-identically after a
  restart — so if someone later "helpfully" starts caching paths, the
  test that documents the decision turns red.
- Tampering with a stored `study_plans` payload cannot change the plan
  the product serves: `study_plan()` recomputes, and the write-through
  flush then repairs the tampered row in place.
- `StudyPlan` snapshots are **never deleted**. Two plans exist after the
  acceptance run because the student state changed between them; both
  rows survive.
- `learning_path_before == learning_path_after` holds across a restart
  for every knowledge point, not just the first one.

### 21.3 Two failure modes that are explicitly forbidden

**Silently creating an empty database.** If the file at the configured
path exists but cannot be opened or is not a SQLite database, `Workspace`
raises `StorageError` and leaves the file untouched. Creating a fresh
empty database would show the user "your data is gone" with no error at
all — the worst possible outcome, and the reason
`test_persistence_workspace.py` asserts the exception *and* that the
original bytes are still on disk.

**Silently dropping a record that points at a missing file.** When a
material record exists but its file does not, the record is **kept** and
`material_integrity()` reports `MATERIAL_FILE_MISSING`; `health()`
reports `degraded` with a `missing` count. Deleting the record would
destroy the provenance chain that every knowledge point depends on.

### 21.4 Storage paths are portable

A material record carries both `stored_path` (absolute, as of the moment
it was written) and `relative_path` (relative to `data_dir`). Restoring a
backup into a different directory invalidates every absolute path, so
resolution always prefers `relative_path` and only falls back to
`stored_path`. `restore_records()` re-resolves on load, which is why
validation and ingestion still work after the data directory is moved.

The identity fields (`material_id`, `content_hash`) are **never**
recomputed during that re-resolution: changing an identity would break
every evidence reference pointing at the material.

### 21.5 Where course isolation happens

`ConflictRecord`, `ReviewRecord` and `SessionKnowledgeMembership` have no
`course_id` column, because their identity is not course-scoped. Course
isolation therefore happens **at load time**, and it is derived from
**evidence ownership** rather than from the `knowledge_points.course_id`
column: knowledge points are content-addressed and can legitimately be
shared by two courses, so whichever course wrote last would otherwise
"own" the column and steal the point from the other.

A knowledge point whose every evidence reference has disappeared is not
surfaced as knowledge at all — "no evidence, no knowledge" is the rule
that keeps the product from inventing facts. The row itself is left in
place; the loader does not destroy data.

### 21.6 Exercises: the identity/presentation split

`Exercise._validate_choices` keeps the author's choice order, while the
content-addressed `exercise_id` is computed from a **canonical**
(sorted) choice list. Both properties are required and they pull in
opposite directions:

- presentation order must follow the author (`true` before `false` for a
  true/false question, A/B/C/D for a multiple-choice one);
- identity must be insensitive to that order, otherwise the same question
  entered twice with the options reordered becomes two questions.

Keeping them separate fixed a real defect found while wiring Task 51: the
auto-generated true/false choices were `(true, false)` while explicitly
supplied choices were sorted to `(false, true)`, so the two paths derived
**different** `exercise_id` values. `Exercise.from_dict` then rejected the
stored payload as corrupt, which meant true/false exercises could not be
read back out of SQLite at all. The defect had been invisible for ten
tasks because nothing had ever round-tripped an exercise through durable
storage.

### 21.7 One business operation, one transaction

Write-through (§21.1) makes every successful operation durable, but
durability alone is not enough: an operation that writes to five tables
and fails on the fourth would leave a **half-built business object** in
the database. The user would then see a workspace that looks fine but is
silently missing content, with no error to follow.

So every mutating application method runs inside
`WorkspacePersistence.atomic()`:

```python
def create_session(self, course_id, ...):
    with self._atomic():              # -> persistence.atomic() -> BEGIN
        dto = self.course_service.create_session(...)
        self._register_course_sessions(ctx)
        self._flush_course(dto["course_id"])
        self._flush_organization(dto["course_id"])
    return dto
```

- All of it commits, or none of it does. The half-built state is not
  "unlikely" — it is **unrepresentable**.
- On failure the exception propagates unchanged. `PersistenceError`
  becomes a structured `StorageError`; domain errors keep their own code;
  anything else (including `KeyboardInterrupt` and `SystemExit`) is
  re-raised as-is. Nothing is ever swallowed.
- Nesting is safe (`SAVEPOINT`), so `save_material_records` keeps its own
  inner transaction and still works when called standalone.

The layers divide cleanly: `WorkspacePersistence.save_*` are
**composable write steps** (they do not open transactions), and
`atomic()` is the **operation boundary**. Calling a `save_*` outside any
`atomic()` block autocommits each statement — that is correct for a bare
repository call, and `test_persistence_transactions.py` pins the
distinction so nobody has to guess where the boundary is.

**Memory is not rolled back.** Domain objects are mutated outside the
transaction, so a failed operation leaves the in-memory state ahead of
the database. The operation *failed* (the caller got an exception), and
the database holds the last consistent state. The two meet again at the
next restart, where memory is rebuilt from the database — which is
exactly why "restart" is a first-class acceptance scenario rather than a
convenience.

**The filesystem is not rolled back either, and the order matters.**
`register_material` copies the bytes into the managed directory *before*
writing the record, so a rolled-back registration can leave an **orphan
file**. That direction is the safe one: an orphan file is referenced by
nothing, whereas an orphan *record* is a broken provenance chain —
`material_integrity()` reports `MATERIAL_FILE_MISSING` and `health()`
goes `degraded`. Reversing the order would turn a harmless leftover into
a data-integrity defect.

#### Failure injection

`WorkspacePersistence` accepts an optional `fault_injector` — a
`(point, ordinal) -> None` callable invoked **after** each successful
write step, where `point` is one of `FAULT_POINTS` and `ordinal` is the
1-based index of that step within the current operation. It exists so the
rollback property can be *proved* rather than asserted, and it follows
the same idiom as the injectable `Clock`: production passes nothing, and
the default is `None`.

`tests/test_persistence_transactions.py` (71 tests) does two things with
it:

1. **Every named point** is shown to be reachable on a real path, and
   failing there rolls the whole operation back.
2. **Exhaustively**, for each write operation, it counts the write steps
   `N`, then injects a failure at every `k` in `1..N` and asserts the
   database is row-for-row identical to the pre-operation state. Across
   the six operations tallied in one test that is 67 distinct injection
   points; the full file covers well over a hundred.

Row-for-row comparison is the only assertion worth trusting here:
"the evidence table is still empty" proves one specific defect is absent,
whereas "the whole database is logically unchanged" proves the property
itself — including for tables added later, because the snapshot is taken
by walking `table_names()` rather than a hand-written list.

Two differences are explicitly normalised in that comparison, and only
those two: `material_processing.attempts` (a retry legitimately processes
twice) and absolute paths under `data_dir` (two sandbox copies have
different directory names). Both sandboxes run on a fixed injected clock,
so `created_at` / `last_attempt_at` cannot masquerade as behavioural
differences.

### 21.8 Restart, crash and recovery

"Restart" is a first-class acceptance scenario, not an afterthought, and
it cannot be tested honestly inside one process. Closing a `Workspace`
and opening another in the same interpreter still shares the module
cache, the process memory, and the operating system's file-handle table —
so a defect where "the bytes never reached disk" passes anyway. The only
honest test is a **second process**: data survives only if it is on disk.

`tests/test_restart_recovery.py` (90 tests) runs the real 17-step
pipeline in one subprocess and reads it back in another. Three ways a
process can end are covered separately, because each exercises a
different recovery mechanism:

| How the process ends | Cleanup | What saves the data |
| --- | --- | --- |
| Clean exit (`close()`) | WAL checkpoint, connection closed | Normal shutdown |
| Hard kill (`TerminateProcess` / `SIGKILL`) | none — `-wal` / `-shm` left behind | WAL recovery |
| `os._exit(9)` mid-operation | none, and the transaction is open | WAL recovery discarding uncommitted frames |

Only the first is ever covered by accident, which is why the other two
are named explicitly.

#### Making the crash test non-trivial

The easiest way to write a worthless crash test is to let the uncommitted
pages stay in memory: "uncommitted data disappeared" is then a statement
about the page cache, not about the recovery protocol. The crash child
therefore sets `PRAGMA cache_size = -8` (8 KiB) before the operation, so
any business write immediately spills dirty pages into `-wal`. The
assertion that keeps this honest is that **`-wal` must be non-empty after
the crash** — otherwise the test declares itself void.

The crash point itself comes from the production code: the `FaultInjector`
from 21.7, whose injector calls `os._exit(9)`. No exception, no `finally`,
no rollback, no close.

#### Two subtleties that make the assertions trustworthy

**A copy of a data directory is not the same directory.** `shutil.copytree`
does not rewrite the material registry, so a copied workspace still has
`stored_path` values pointing at the *source* directory; re-registering
rewrites them to the new one (that re-derivation is the fix for defect
#22). Any "the files are byte-identical" assertion must therefore either
use a dedicated directory or normalise the `data_dir` prefix — including
its JSON-escaped form, since the registry is JSON. Getting this wrong
turns a strong assertion into noise.

**Fixed clocks are a precondition.** The acceptance harness runs on
`fixed_clock("2026-09-15T18:00:00+00:00")`, so `created_at` cannot
masquerade as a behavioural difference. Without it, no byte-level
comparison can ever pass.

#### What a restart must not change

- `validation_status` — a `CONFLICTED` knowledge point stays
  `CONFLICTED`. Restarting is not a way to resolve a conflict.
- Review history — append-only, decision by decision, including the
  evidence side a human explicitly chose.
- Evidence provenance — every knowledge point's chain back to a material
  and a source location still resolves.
- Derived views — `learning_path`, `coverage`, `gaps`, `dependencies` are
  recomputed and must come out *identical* to the in-memory values, since
  they are deterministic functions of stored state.
- The study plan's identity — the value is recomputed, and content
  addressing means the recomputation reproduces the same `plan_id`.

#### What a crash must not leave behind

A business operation that died mid-transaction leaves **nothing**: not the
rows it already wrote, and not the `material_processing` attempt counter
either, because that write is inside the same transaction. The database
is `ok`, `integrity_check()` is `ok`, `foreign_key_violations()` is empty,
and the next process can still write.

The one thing that does *not* roll back is the filesystem (21.7): a
crashed `register_material` may leave an orphan file. That direction is
safe — an orphan file is referenced by nothing, whereas an orphan
*record* is a broken provenance chain. Restart is where memory and disk
re-converge, which is exactly why it is tested rather than assumed.

---

## 22. AI Understanding Pipeline (TASK-76, optional, default off)

Deterministic ingestion stays untouched. After Evidence exists, an *explicit*
AI layer may run: `Evidence -> chunk (stable chunk-<sha16> IDs) -> AI Provider
-> structured candidates -> schema validation -> evidence grounding ->
dedup -> merge/conflict proposals -> confidence policy
(auto >= 0.90 / review >= 0.70) -> KnowledgePoint via the existing
`register_knowledge_point` path (evidence-checked, idempotent) -> Review queue`.

The division of labour is structural, not advisory:

- Deterministic code owns "what the material is, where the evidence is,
  whether the data is legal" (file identity, MIME, extraction, chunking,
  validation, persistence).
- The LLM owns "what the material means, what is worth learning, how to
  organise it" (summary, topics, candidates, relations, importance).
- The existing Evidence/Review system owns traceability and correction.
  The LLM is never a source of fact: a candidate without legal Evidence
  can never be auto-accepted, and conflicts are never auto-resolved.

Implementation notes (all enforced by `tests/test_ai_understanding.py`):

- `src/application/ai/` is stdlib-only (real provider = `urllib` to an
  OpenAI-compatible `/chat/completions` with `response_format: json_object`).
  No new third-party dependency, so the release AI-supply-chain gate stays green.
- API keys come only from the process environment (`CLASSROOM_AI_API_KEY`,
  falling back to `CLASSROOM_LLM_API_KEY`); `repr` / logs / errors / operation
  log carry presence-only shapes.
- Chunk cache key `(chunk_hash, model, prompt_version)`; retry re-runs only
  failed chunks; `processing_identity = ai-<sha16(material|pipeline|prompt|model)>`
  plus deterministic `aikp-<sha16(course|title|type)>` IDs make repeated
  analysis idempotent (no `KnowledgePoint x 3`).
- **Zero DB migration**: candidates map onto existing `KnowledgePoint` fields;
  summaries live on the analysis report, never stuffed into a TEXT column.
- Feature flag `CLASSROOM_AI_ENABLED` (default off); AI failures are
  `PROCESSING_ERROR` (422) with Evidence preserved and retryable.
- Endpoints `POST /api/materials/{id}/ai-analyze` (explicit trigger, also the
  idempotent AI-retry entry) and `GET .../ai-summary` (cached report, never
  re-calls the model on page open).

TASK-77 automatic processing (all enforced by
`tests/test_ai_auto_pipeline.py`, 26 cases):

- `Workspace.process_material()` auto-triggers AI after deterministic
  ingestion commits; `process_session()` auto-analyzes each succeeded
  material sequentially (no Celery/Redis). Disabled mode returns the exact
  TASK-76 job shape (no `ai` key). AI failure only enriches `job["ai"]`
  (`completed` / `failed` / `skipped`); the material stays `SUCCEEDED`
  with Evidence intact.
- AI-produced KPs are mirrored into `processing.structure`
  (`AIAnalysisService._mirror_to_structure`, idempotent) so the existing
  `_flush_knowledge` persists them — otherwise they vanish on restart
  (found by test, fixed without migration). Successful reports are also
  cached on disk at `materials/ai-reports/<course>/<material>.json`.
- Real provider: bounded backoff retry on 429/5xx (`0.5s → 1s → 2s` cap,
  `1 + max_retries` attempts), immediate fail on 401/403, always with a
  connect/read timeout; error `detail` carries host/model/http_status only.
