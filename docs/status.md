# Project Status

## Task 1 completed

### Summary
- Cleaned root temporary/experimental files
- Cleaned archive directory
- Removed Python cache files
- Fixed src/__init__.py syntax error
- Created .gitignore
- compileall: PASS
- import src: PASS

### Project Structure
- src/ contains placeholder modules
- docs/architecture.md contains the architecture design
- AGENTS.md contains project specifications
- input/output directories are empty (ready for materials)
- tests/, cache/, logs/ are empty directories

## Task 2 completed

### Summary
- Implemented src/models.py with all core data models
- Models: Language, MaterialType, Confidence, EvidenceType, VerificationStatus, SourceReference, Material, Evidence, KnowledgePoint, ClassSession, VerificationItem
- All 28 model tests pass

## Task 3 completed

### Summary
- Implemented src/material_index.py with scan_materials, save_index, build_index functions
- Supports audio (mp3, m4a, wav, ogg), images (jpg, jpeg, png, webp), notes (txt, md), syllabi (pdf, docx)
- Stable ID generation using SHA-256 of (relative_path + size + mtime)
- Content hash (SHA-256) stored in metadata for duplicate detection
- Unsupported files are skipped
- Unicode filenames handled correctly
- Language defaults to UNKNOWN (conservative)
- Output: output/index/materials.json
- All 41 tests pass (28 Task 2 + 13 Task 3)
- No third-party dependencies added

## Task 4 completed

### Summary
- Implemented src/note_parser.py with NoteParser class
- Supports TXT and Markdown parsing
- Parses notes into structured Evidence objects with SourceReference
- Preserves original text content without modification
- Simple language detection (Chinese, Spanish, Catalan, Unknown)
- Handles empty files, whitespace-only files, Unicode characters
- Evidence type: PERSONAL_NOTE
- Stable behavior on repeated parsing

### Files Changed
- src/note_parser.py (created)
- tests/test_note_parser.py (created)
- tests/fixtures/notes/spanish.md (created)
- tests/fixtures/notes/catalan.md (created)
- tests/fixtures/notes/chinese.md (created)
- tests/fixtures/notes/mixed.md (created)
- tests/fixtures/notes/example.txt (created)
- docs/status.md (updated)

### Test Results
- All 62 tests pass (Task 2: 28 + Task 3: 13 + Task 4: 21)
- compileall: PASS

### Note Parser Features
- TXT parsing: reads UTF-8, preserves original text, groups lines into paragraphs
- Markdown parsing: preserves headings, paragraphs, bullet lists, numbered lists
- SourceReference: material_id, location, line, paragraph
- Language detection: simple heuristic-based detection
- Error handling: FileNotFoundError for missing files, empty list for empty files
- No AI, OCR, ASR, or summarization calls

## Task 5 completed

### Summary
- Implemented src/evidence_extractor.py with EvidenceExtractor class
- Unified extraction pipeline orchestrates NoteParser for TXT/Markdown materials
- Unsupported material types (.mp3, .m4a, .wav, .ogg, images, PDF) return empty list safely
- Batch extraction via extract_all() preserves order and isolates errors
- SourceReference fully preserved from NoteParser output
- Language preservation: material language is preserved, not re-detected
- Evidence content not modified, not translated
- 81 total tests pass (Task 2: 28 + Task 3: 13 + Task 4: 21 + Task 5: 19)
- compileall: PASS

### Files Changed
- src/evidence_extractor.py (created)
- tests/test_evidence_extractor.py (already existed with tests)
- docs/status.md (updated)

### Note
- EvidenceExtractor delegates to NoteParser for TXT/Markdown parsing
- Does not modify NoteParser behavior
- No new third-party dependencies added

## Task 8 completed

### Summary
- Implemented src/knowledge_structure.py with KnowledgeStructure, Relationship, and RelationType classes
- Created tests/test_knowledge_structure.py with 26 test cases
- RelationType enum: RELATED_TO, PRE_REQUSITE_OF, TOPIC
- Relationship dedup: A related_to B == B related_to A (order-independent); A prerequisite_of B != B prerequisite_of A
- Validation: rejects nonexistent source/target, self-reference, invalid relation_type, duplicate relationships, invalid evidence refs
- Serialization: to_dict() and from_dict() for KnowledgeStructure and Relationship
- All 26 tests pass, 0 failures, 0 errors
- compileall: PASS
- Total: 123 tests passed (Task 2: 28 + Task 3: 13 + Task 4: 21 + Task 5: 19 + Task 6: 14 + Task 8: 26)

## Task 9 completed

### Summary
- Added Course dataclass to src/models.py with stable ID generation (SHA-256 hash of name + code)
- Modified ClassSession in src/models.py to use stable session IDs based on course_id + session_number
- Added session_number field to ClassSession
- Created src/course_context.py with CourseContext class
- CourseContext manages Courses and ClassSessions with add_course, add_session, add_material_to_session, add_evidence_to_session, add_knowledge_point_to_session
- CourseContext uses deepcopy to avoid modifying upstream objects
- Created tests/test_course_context.py with 40 test cases covering all requirements
- All 160 tests pass (123 original + 40 new Task 9 tests - 3 duplicate determinism tests removed)
- compileall: PASS
- Temp files cleaned up (src/_models_new.py, src/write_parser.py, tests/test_det_tmp.py)

### Files Changed
- src/models.py (added Course class, modified ClassSession for stable IDs)
- src/course_context.py (created)
- tests/test_course_context.py (created)
- docs/status.md (updated)
- docs/architecture.md (updated)




## Task 11 completed

### Summary
- Implemented OCR/Board Photo Layer (Task 11)
- Added OCRResult, OCRSegment, and BoundingBox data structures in src/models.py
- Created src/ocr_processor.py with MockOCREngine (deterministic, no real OCR)
- Implemented ocr_to_evidence() function to convert OCR results to Evidence objects
- Added material validation: only IMAGE type materials allowed, AUDIO/TEXT/PDF rejected
- Multilingual support: Spanish, Catalan, Chinese text preserved without auto-translation
- Anti-hallucination: no text generated from filenames, metadata, or automatic correction
- All 219 tests pass (160 original + 47 Task 10 + 12 new Task 11 tests)
- compileall: PASS
- No third-party OCR dependencies added

### Files Changed
- src/models.py (added OCRResult, OCRSegment, BoundingBox classes)
- src/ocr_processor.py (created with MockOCREngine and processing functions)
- tests/test_ocr_processor.py (created with 12 test cases)
- docs/status.md (updated)
- docs/architecture.md (updated)

### Test Results
- All 12 OCR processor tests pass
- All 219 tests pass across all tasks
- compileall: PASS

### Note
- Real OCR / Tesseract / PaddleOCR / cloud OCR: NOT IMPLEMENTED
- This layer provides the data structure layer only; real OCR engine to be integrated separately
- OCRResult ID is stable based on (material_id + language + segment text)
- BoundingBox uses top-left origin coordinate system (x right, y down)
## Task 10 completed

### Summary
- Added TranscriptLanguage, TranscriptSegment, Transcript dataclasses to src/models.py with stable ID generation (SHA-256 hash of material_id + len(segments) + language.value + segment data)
- Created src/transcription.py with TranscriptionEngine (abstract), MockTranscriber (deterministic), transcript_to_evidence(), create_transcript_from_segments()
- Created tests/test_transcription.py with 47 test cases across 7 test classes
- Transcript dataclass: transcript_id starts with \ transcript-\ + 16 hex chars (27 chars total)
- MockTranscriber.transcribe(audio_path): generates 1-5 segments based on SHA-256 hash; deterministic output
- TranscriptSegment.__post_init__: clamps negative start to 0.0, sets end to start if end < start, clears whitespace-only text
- Transcript.to_transcript_evidence(): returns Evidence with EvidenceType.TRANSCRIPT, confidence HIGH if all segments >0.5 else MEDIUM
- TranscriptLanguage.from_string() matches case-insensitively, returns UNKNOWN for unmatched
- All 47 tests pass, compileall: PASS
- Total: 160+ tests pass (123 original + 40 Task 9 tests + 47 Task 10 tests)

### Files Changed
- src/models.py (added TranscriptLanguage, TranscriptSegment, Transcript dataclasses, fixed __post_init__ ordering)
- src/transcription.py (created)
- tests/test_transcription.py (created with 47 test cases)
- docs/status.md (updated)
- docs/architecture.md (updated)

### Test Results
- All 47 transcription tests pass
- compileall: PASS
- Total: 207 tests pass across all tasks

## Task 12 — Unified Evidence Pipeline
Status: PASS

### Implemented
- src/processor.py: ClassSessionProcessor, a pure orchestration layer that routes each
  Material to its existing handler (Note -> EvidenceExtractor; Audio -> TranscriptionEngine
  + transcript_to_evidence(); Image -> OCREngine + ocr_to_evidence()), merges the results
  into a single stable-ordered Evidence list, and safely skips SYLLABUS / unsupported types.
- Constructor dependency injection for TranscriptionEngine / OCREngine (defaults to the
  existing MockTranscriber / MockOCREngine).
- tests/test_processor.py: 28 new tests covering note audio image routing, language
  preservation (Spanish/Catalan/Chinese), audio timestamps + material id, OCR page/bbox/
  material id + determinism, session handling, data integrity, and no-real-ASR/OCR/LLM guards.
- docs/architecture.md updated with the Unified Evidence Pipeline section.

### Tests
- New tests: 28
- Full project suite: 266/266 passed
- compileall (processor.py + test_processor.py): PASS

### Data Integrity
- Evidence ID: preserved (transcript_/ocr_ stable ids carried in metadata; source material_id intact)
- SourceReference: full (material_id, location, timestamp_start/end, page, line, paragraph)
- OCR bounding box: preserved (bounding_box dict in Evidence.metadata)
- OCR page: preserved (SourceReference.page)
- Audio timestamp: preserved (SourceReference.timestamp_start/end)
- Material ID: not lost
- Multilingual preservation: Spanish/Catalan/Chinese originals unmodified

### Integration
- Processor output feeds directly into EvidenceIntegrator.integrate(evidence) with no changes to Integration
- No re-implementation of Integration; no KnowledgePoint generated in this layer

### Constraints
- Real ASR: NOT IMPLEMENTED
- Real OCR: NOT IMPLEMENTED
- LLM: NOT USED
- Network: NOT USED
- New dependencies: NONE

### Files Changed
- src/processor.py (created: ClassSessionProcessor)
- tests/test_processor.py (created: 28 tests)
- docs/architecture.md (appended Unified Evidence Pipeline section)
- docs/status.md (this entry)

### Known Limitations
- Evidence.evidence_id uses uuid4 (inherited from the existing Evidence model), so the
  random evidence_id differs between runs; the deterministic guarantees apply to output
  order, content, and all source/identity fields (material_id, transcript_id, ocr_result_id,
  timestamps, page, bounding boxes, language). This is a property of the existing model, not
  of the processor.
- PDF/DOCX (SYLLABUS) and other unsupported types are intentionally safe-skipped and produce
  no Evidence (by design, to avoid fabricated content).
- Pre-existing stray files in src/ (func_def.py, new_func.py, ocr_processor_part1.py, temp.py,
  temp2.py, etc.) contain syntax errors but are not imported by any module or test and do not
  affect the 266 passing tests; left untouched as out-of-scope for Task 12.

## Task 13 — Incremental KnowledgePoint Integration
Status: PASS

### Implemented
- src/knowledge_pipeline.py: KnowledgePipeline (process_evidence / process_session)
  + KnowledgeExtractor (deterministic, content-addressable KnowledgePoint extraction)
  + PipelineResult dataclass for diagnostics
- KnowledgeStructure extended with conflicts: list[ConflictRecord] and add_conflict()
- ConflictRecord now has to_dict/from_dict and a stable conflict_id so resubmitted
  equivalent evidence does not create duplicate conflict records
- Pipeline wires existing EvidenceIntegrator and KnowledgeExtractor without
  re-implementing either

### Pipeline
ClassSession
→ ClassSessionProcessor
→ Evidence
→ KnowledgePipeline
→ EvidenceIntegrator
→ KnowledgeExtractor
→ KnowledgeStructure

### Incremental Behavior
- Initial structure (empty + new Evidence): PASS
- Existing KnowledgePoint update (evidence_refs merged, needs_verification escalated): PASS
- Supporting evidence (merged into one KnowledgePoint): PASS
- Duplicate evidence (no duplicate KnowledgePoint created): PASS
- Conflict handling (conflict recorded, KPs flagged needs_verification): PASS
- Relationship preservation (existing relationships untouched on incremental run): PASS
- Idempotency (re-submit identical evidence → no duplicates): PASS

### Traceability
- KnowledgePoint → Evidence: PASS (evidence_refs populated)
- Evidence → Material: PASS (source_reference.material_id preserved)
- Material → ClassSession: PASS (process_session wires end-to-end)

### Multilingual
- Spanish: PASS
- Catalan: PASS
- Chinese: PASS

### Tests
- New tests: 33
- Full test suite: 299/299 passed
- compileall (src + tests): PASS

### Constraints
- Real ASR: NOT IMPLEMENTED (MockTranscriber used via processor)
- Real OCR: NOT IMPLEMENTED (MockOCREngine used via processor)
- LLM: NOT USED
- Database: NOT USED
- Network: NOT USED
- New dependencies: NONE

### Files Changed
- src/knowledge_pipeline.py (created)
- src/knowledge_structure.py (conflicts field, add_conflict, to_dict/from_dict extensions)
- src/integration.py (ConflictRecord.to_dict/from_dict; stable conflict_id in _detect_conflicts)
- tests/test_knowledge_pipeline.py (created: 33 tests)
- docs/architecture.md (appended Task 13 section)
- docs/status.md (this entry)

### Known Limitations
- KnowledgePoint ID is content-addressable: two distinct evidences whose
  first-sentence anchors happen to be identical will be merged into one
  KnowledgePoint. This is intentional (deduplication) but means very similar
  content from different topics may cluster under a single KP.
- Supporting-group detection uses a word-subsequence heuristic; it does not
  detect all semantic relationships, only near-duplicate / extended statements.
- Conflict detection (order-reversal and negation patterns) is limited to
  Spanish/English phrasing; other language patterns may be missed.

## Task 14 — Knowledge Validation & Evidence Conflict Resolution
Status: PASS

### Implemented
- src/knowledge_validation.py: ValidationStatus enum, ValidationResult
  frozen dataclass, KnowledgeValidator (validate_knowledge_point /
  validate_structure), ValidationReport; deterministic knowledge_score
  (0/1/2/3+ supporting → 0.0/0.5/0.75/0.9, conflict cap 0.5, clamp [0,1])
- src/models.py KnowledgePoint extended with validation_status (default
  "unverified") and knowledge_score (default 0.0, clamped); from_dict
  backward compatible; invalid status coerced to "unverified"
- src/knowledge_pipeline.py: KnowledgePipeline._apply_validation writes
  validation fields onto KPs after extraction (read-only over Evidence);
  process_evidence now ends with a validation pass
- src/knowledge_structure.py: supporting_evidence_ids / supporting_
  evidence_count / conflict_count helpers (unique-ID based, stable order)
- src/integration.py minimal bug fix: integrate() de-duplicates by
  evidence_id before relation detection so a resubmitted evidence item
  cannot conflict with itself (duplicate-resubmission idempotency)
- tests/test_knowledge_validation.py: 39 tests covering all 37.1-37.20
  scenarios

### Validation
- ValidationStatus: UNVERIFIED / SUPPORTED / CONFLICTED — supported means
  "current Evidence is consistent", NOT real-world verified
- KnowledgeScore: deterministic rule from support/conflict counts, always
  in [0.0, 1.0]
- Needs verification: UNVERIFIED/CONFLICTED → True, SUPPORTED → False

### Evidence Support
- Supporting evidence: PASS
- Duplicate evidence handling: PASS (dedup by evidence_id)
- Source traceability: PASS (KP → Evidence → Material preserved; OCR page /
  audio timestamps untouched)

### Conflict
- Conflict detection: PASS (reused EvidenceIntegrator)
- ConflictRecord: PASS
- Stable conflict ID: PASS (dedup by conflict_id)
- Conflict deduplication: PASS
- Automatic resolution: NOT IMPLEMENTED (by design)

### Incremental Behavior
- New supporting evidence: PASS
- New conflict escalates to CONFLICTED: PASS
- Existing conflict preservation: PASS
- Idempotency: PASS
- Relationship preservation: PASS

### Multilingual
- Spanish: PASS
- Catalan: PASS
- Chinese: PASS
- Cross-language wording is NOT treated as conflict (no new translation
  or semantic matcher introduced; language difference alone never
  produces CONFLICTED)

### Serialization
- to_dict/from_dict: PASS
- Backward compatibility: PASS (missing fields default unverified / 0.0)

### Tests
- New tests: 39
- Full test suite: 338/338 passed
- compileall (Task 14 modules): PASS

### Constraints
- Real ASR: NOT IMPLEMENTED (mocks)
- Real OCR: NOT IMPLEMENTED (mocks)
- LLM: NOT USED
- Web verification: NOT USED
- Database: NOT USED
- Network: NOT USED
- New dependencies: NONE

### Files Changed
- src/knowledge_validation.py (created)
- src/models.py (KnowledgePoint: + validation_status, + knowledge_score,
  clamping/coercion in __post_init__, to_dict/from_dict)
- src/knowledge_pipeline.py (_apply_validation + call in process_evidence)
- src/knowledge_structure.py (supporting_evidence_ids/count, conflict_count)
- src/integration.py (integrate() evidence_id de-dup before detection)
- tests/test_knowledge_validation.py (created: 39 tests)
- docs/architecture.md (this section)
- docs/status.md (this entry)

### Known Limitations
- knowledge_score is an evidence-support degree, explicitly NOT a real-
  world truth probability
- Conflict detection inherits Task 13 order-reversal / negation coverage
  (Spanish/English patterns); other languages may miss conflicts
- SUPPORTED ≠ factually correct; it only means current materials agree
- The same content-addressable KnowledgePoint merging limitation from
  Task 13 still applies (similar first-sentence anchors cluster)

---

## Task 15 — Knowledge Review & Human Verification Layer
Status: PASS

### Implemented
- ReviewStatus (PENDING / CONFIRMED / REJECTED / KEPT_UNVERIFIED) as an
  independent dimension of ValidationStatus
- ReviewDecision (CONFIRM / REJECT / KEEP_UNVERIFIED)
- ReviewRecord (frozen, stable SHA-256 business ID; note kept out of
  Evidence; append-only, dedup by ID)
- ReviewCandidate (deterministic projection; CONFLICTED priority 0,
  UNVERIFIED 1, other needs-verification 2; tie-break knowledge_point_id)
- KnowledgeReviewService: get_review_candidates, confirm, reject,
  keep_unverified, resolve_conflict, review_status_of, review_history,
  latest_review, has_conflict
- KnowledgePoint.review_status (+ to_dict / from_dict, default "pending")
- KnowledgeStructure.review_records (+ to_dict / from_dict, backward
  compatible, idempotent add_review_record)

### Review Candidates
- Candidate discovery: PASS (UNVERIFIED / CONFLICTED / flagged SUPPORTED
  enter; stable reviewed SUPPORTED does not)
- Candidate ordering: PASS (priority, then knowledge_point_id)
- Conflict prioritization: PASS (CONFLICTED always first)

### Review Status
- PENDING: PASS
- CONFIRMED: PASS (explicit human action only)
- REJECTED: PASS (explicit human action only)
- KEPT_UNVERIFIED: PASS (explicit human action only)
- No automatic CONFIRMED / REJECTED: PASS

### Human Decisions
- Confirm: PASS (non-conflicted confirm pins the point's own evidence
  refs as selection basis)
- Reject: PASS
- Keep unverified: PASS
- Conflict resolution: PASS (explicit selected evidence required;
  empty selection on CONFLICTED raises ValueError)
- Invalid evidence selection: PASS (ValueError for evidence outside the
  KP's refs and its conflicts' refs)

### Conflict
- Automatic resolution: NOT IMPLEMENTED (by design)
- Human resolution: PASS
- ConflictRecord preservation: PASS (never deleted by review ops)
- Original Evidence preservation: PASS

### Auditability
- ReviewRecord: PASS
- Review history: PASS (append-only; re-review on regression keeps all
  prior records)
- Evidence traceability: PASS (records reference evidence IDs only,
  never copies content)
- Review note separation: PASS (notes never enter Evidence)

### Incremental Behavior
- New conflict after confirmation: PASS (review_status flips back to
  PENDING, history preserved)
- Re-review: PASS
- Relationship preservation: PASS
- Idempotency: PASS (same point+decision+selection -> same review_id,
  no duplicate records, stable final state)

### Multilingual
- Spanish: PASS
- Catalan: PASS
- Chinese: PASS
- No translation / rewriting of Evidence during review: PASS

### Serialization
- to_dict/from_dict: PASS (review status + records + conflicts survive
  round-trip)
- Backward compatibility: PASS (old structures without review fields
  load with review_status="pending", review_records=[])

### Tests
- New tests: 38 (tests/test_knowledge_review.py, scenarios 59.1-59.23)
- Full test suite: 376/376 passed
- compileall: all Task 15 modules PASS

### Constraints
- Real ASR: NOT IMPLEMENTED
- Real OCR: NOT IMPLEMENTED
- LLM: NOT USED
- Web Search: NOT USED
- Database: NOT USED
- UI: NOT IMPLEMENTED
- Network: NOT USED
- New dependencies: NONE

### Files Changed
- src/knowledge_review.py (created: ReviewStatus, ReviewDecision,
  ReviewRecord, ReviewCandidate, KnowledgeReviewService)
- src/models.py (KnowledgePoint: + review_status, to_dict/from_dict)
- src/knowledge_structure.py (+ review_records, add_review_record,
  review_records_for_knowledge_point, conflict_ids_for_knowledge_point,
  to_dict/from_dict)
- tests/test_knowledge_review.py (created: 38 tests)
- docs/architecture.md (Task 15 section)
- docs/status.md (this entry)

### Known Limitations
- 9 legacy files with pre-existing syntax errors (create_func.py,
  func_def.py, func_part.py, new_func.py, ocr_processor_new.py,
  ocr_processor_part1.py, ocr_processor_tail.py, temp.py, temp2.py)
  fail `python -m compileall`; they are historical junk outside Task
  15 scope and were not cleaned up per project policy.
- resolve_conflict records a CONFIRM decision with explicit selection;
  validation_status is intentionally left unchanged (semantic
  separation of Evidence validation vs human decision).
- No user identity system (per spec, reviewer is not modeled).


## Task 16 — Audio Material Validation & Input Layer
Status: PASS

### Implemented
- src/audio_input.py: AudioMaterialValidator (validate + to_audio_input),
  AudioValidationResult (structured VALID/INVALID + stable error codes),
  AudioInput (file-level identity for the future ASR layer),
  SUPPORTED_AUDIO_EXTENSIONS (.mp3/.m4a/.wav/.ogg, case-insensitive).
- Checks: existence, regular file, non-empty, supported extension,
  readability. Read-only, deterministic, stdlib only.
- Material input reuses indexed metadata (size/sha256/mtime/relative_path).

### Validation Coverage
- Missing files / directories / empty files / unsupported extensions
- Unicode filenames (Chinese + Spanish/Catalan) preserved verbatim
- Extension case-insensitivity without mutating the path
- Metadata preservation, determinism, no mutation, no size cap
- No decoder/ASR dependencies (whisper/torch/librosa/pydub: NONE)

### Tests
- New tests: 37 (tests/test_audio_input.py)
- Full suite before Task 16: 376
- Final suite: 413/413 passed
- compileall: new/changed modules PASS
  (9 pre-existing historical temp files in src/ still fail compileall,
  unchanged and out of scope per project policy)

### ASR Boundary
- Real ASR: NOT IMPLEMENTED
- Whisper: NOT IMPLEMENTED
- Audio decoding: NOT IMPLEMENTED
- Audio segmentation / VAD: NOT IMPLEMENTED
- Language identification: NOT IMPLEMENTED

### Dependencies
- No new third-party dependencies (stdlib only)

### Files Changed
- src/audio_input.py (created)
- tests/test_audio_input.py (created: 37 tests)
- docs/architecture.md (Task 16 section)
- docs/status.md (this entry)

## Task 17 - ASR Provider Interface Hardening

Status: PASS

### Implemented
- src/asr_provider.py (created):
  - ASRProviderErrorCode (INVALID_INPUT / UNAVAILABLE /
    CONFIGURATION_ERROR / PROCESSING_ERROR / UNSUPPORTED)
  - ASRProviderError hierarchy with retryable metadata
  - ASRProviderConfig (provider_name, timeout)
  - ASRProviderCapabilities (timestamps / speakers / language / word
    timestamps; honest default False)
  - TranscriptionResult (optional Transcript + structured error tuple)
  - ASRProvider (abstract base class)
  - MockASRProvider (deterministic, preconfigured, failure simulation)
  - ASRTranscriptionEngineAdapter (legacy TranscriptionEngine bridge)
  - create_mock_asr_provider factory
- src/processor.py (minimal change):
  - ClassSessionProcessor accepts an optional `asr_provider` parameter;
    provider is wrapped via ASRTranscriptionEngineAdapter so the
    existing engine path keeps working
  - `transcription_engine` parameter unchanged for backward
    compatibility
- tests/test_asr_provider.py (created: 33 tests covering provider
  contract, mock behaviour, error model, capabilities, determinism,
  provider replacement, no-network / no-ASR-SDK dependency checks,
  error-leakage checks, legacy adapter, and processor integration)
- docs/architecture.md (Task 17 section appended)

### Tests
- New tests: 33 (tests/test_asr_provider.py)
- Full suite before Task 17: 413
- Final suite: 446/446 passed
- compileall: src/asr_provider.py and tests/test_asr_provider.py PASS;
  9 pre-existing historical broken files in src/ still fail compileall
  (out of scope, unchanged)

### ASR Boundary
- Real ASR: NOT IMPLEMENTED
- Whisper: NOT IMPLEMENTED
- Audio decoding / segmentation / VAD / diarization / language
  auto-detection: NOT IMPLEMENTED
- No network access, no API keys, no LLM, no new third-party
  dependencies

### Files Changed
- src/asr_provider.py (created)
- src/processor.py (minimal: added `asr_provider` constructor param)
- tests/test_asr_provider.py (created: 33 tests)
- docs/architecture.md (Task 17 section)
- docs/status.md (this entry)

## Task 18 — Local Whisper ASR Provider
Status: PASS

### Selected Runtime
- faster-whisper 1.1.0 (CTranslate2), default model base, device cpu, compute int8
- openai-whisper NOT used; audio decoding via PyAV (bundled FFmpeg), no system ffmpeg binary needed
- First run downloads the model via Hugging Face Hub into ~/.cache/huggingface (outside the repository; never committed)

### Implemented
- src/whisper_provider.py: LocalWhisperProvider + WhisperConfig (extends ASRProviderConfig) + create_local_whisper_provider factory
- Lazy model loading, loaded exactly once per provider instance (model_load_count, lock-guarded); close() releases the model
- AudioInput -> Whisper segments -> TranscriptionResult: text stripped and kept in the original language, start/end timestamps mapped, chronological order preserved, speaker always empty (no diarization), empty text segments filtered
- Empty recognition = legal empty TranscriptionResult; runtime failure = stable PROCESSING_ERROR; the two are never conflated
- Invalid timestamps (start < 0, end < start) rejected with a processing error, never silently repaired
- Invalid input -> INVALID_INPUT; missing file / model load failure / CUDA requested but unavailable -> UNAVAILABLE; runtime failure -> PROCESSING_ERROR; ASRProviderError subclasses pass through unwrapped
- language is an explicit ISO code setting (es/ca/zh/en mapped, unknown -> UNKNOWN); no automatic detection, no filename guessing
- CUDA explicit but unavailable raises a stable error; no silent fallback to CPU

### Tests
- tests/test_whisper_provider.py: 53 tests (52 fake-runtime unit tests + 1 real-tiny-model integration test)
- Baseline before Task 18: 446 tests
- Final suite: 499/499 passed, 0 failed
- Real ASR verification: faster-whisper 1.1.0 installed, tiny model loaded on CPU int8, end-to-end transcription executed and validated

### Files Changed
- src/whisper_provider.py (created)
- tests/test_whisper_provider.py (created)
- requirements.txt (created; faster-whisper>=1.0,<2.0, ctranslate2>=4.0,<5.0)
- docs/architecture.md (Task 18 section appended)
- docs/status.md (this entry)
- No Knowledge layer, Review layer, or unrelated modules modified

### Known Limitations
- Long audio segmentation: NOT IMPLEMENTED
- VAD: NOT IMPLEMENTED
- Diarization: NOT IMPLEMENTED
- Language auto-detection: NOT IMPLEMENTED
- compileall: new/changed modules PASS; the 9 pre-existing historical broken temp files in src/ remain unchanged

## Task 19 - Long Audio Segmentation & Transcript Assembly
Status: PASS

### Configuration
- Default chunk duration: 300.0 seconds (DEFAULT_CHUNK_DURATION_SECONDS)
- Default overlap: 0.0 seconds (DEFAULT_OVERLAP_SECONDS, single source of truth)
- Timestamps in seconds throughout (unit of the existing transcription layer)
- Windowing: fixed, deterministic; step = C - O; start_k = k * step; end_k = min(start_k + C, D)

### Implemented
- src/audio_segmentation.py: LongAudioConfig + validate() (rejects C<=0, O<0, O>=C; LongAudioConfigError)
- AudioChunk frozen dataclass: chunk_index, start_time, end_time (seconds), source_audio
- generate_chunks(duration, config, source_audio): deterministic fixed windows, no empty tail, no window past end; ValueError on negative/NaN/infinite duration
- AudioDurationProvider Protocol + PyAvDurationProvider: metadata-only duration read via PyAV container/stream duration, lazy av import, no full decode
- build_long_audio_chunk(chunk, source_audio): slices source audio into a uniquely named temp WAV in the system temp dir (clases_chunk_<uuid>_<idx>.wav), 16 kHz mono s16 via PyAV resampler, seek optimization for non-zero start, preserves original material + metadata (source_audio_path, chunk_index); _real_build_long_audio_chunk reference kept for integration test use
- cleanup_chunk_audio(chunk_audio): removes temp file, safe to call always
- _remap_chunk_segments: global = chunk.start + local; clamps end to total duration; no rounding; deterministic exact-text overlap dedup only when overlap > 0; within a chunk's remapped list, an exact duplicate of the immediately preceding kept segment falling inside the previous chunk's global tail is dropped
- LongAudioProcessor(process): sequential (no parallelism), reuses one ASRProvider for all chunks, chunk 0 full-duration shortcut reuses the original AudioInput (no temp file), failure re-raises ASRUnavailableError/ASRProcessingError with "chunk N: ..." message and stops processing, empty chunk OK, all-empty -> valid TranscriptionResult(segments=[]), segments sorted by (start, end), language = majority non-Unknown, metadata includes chunk_count/config/total_duration/empty_result, material_id preserved from original AudioInput
- create_long_audio_processor() factory mirroring create_local_whisper_provider / create_mock_asr_provider

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

### Capability Status
- Fixed-window chunking: Implemented
- VAD: Not implemented
- Speaker diarization: Not implemented
- Automatic retry: Not implemented
- Parallel ASR: Not implemented
- LLM / translation: Not implemented

### Tests
- tests/test_audio_segmentation.py: 47 tests (46 deterministic unit tests with fakes + 1 real-Whisper integration test)
- Baseline before Task 19: 499 tests
- New tests: 47
- Final suite: 546/546 passed, 0 failed
- Real ASR verification: faster-whisper 1.2.1 installed; tiny model loaded on CPU int8; 5 s fixture split into 3 chunks of 2 s; model loaded exactly once; all segments within 0..5.5 s; temp files cleaned
- Integration test gated with @pytest.mark.integration; skips cleanly if faster-whisper or the tiny model is unavailable

### Files Changed
- src/audio_segmentation.py (created; PyAV time_base line removed to match PyAV 18 API)
- tests/test_audio_segmentation.py (created)
- tests/fixtures/long_silence_5s.wav (created; 5 s 16 kHz mono s16 silence)
- tests/fixtures/tone_3s.wav (created; 3 s 440 Hz sine)
- pytest.ini (created; registers integration marker, testpaths = tests)
- docs/architecture.md (Task 19 section appended)
- docs/status.md (this entry)
- No Knowledge layer, Review layer, or unrelated modules modified

### Known Limitations
- Overlap dedup is exact-text only within a chunk's remapped list; cross-chunk dedup is not implemented
- No VAD, no diarization, no automatic retry, no parallel ASR
- Chunk 0 shortcut only fires when chunk 0 spans the full duration (start == 0 and end == duration)
- PyAV AudioResampler.time_base attribute was removed in PyAV 18; the builder now relies on the resampler's internal time-base handling
- compileall: Task 19 files PASS; pre-existing broken temp files in src/ remain unchanged


---

## Task 20 — ASR Quality & Transcript Validation

**Status: PASS**

### Implemented

- `TranscriptQualityValidator` — deterministic, read-only quality diagnostics
- `QualityReport` — structured output with status, score, issues, statistics
- `QualityIssue` / `QualityIssueCode` / `QualitySeverity` / `QualityStatus`
- `QualityStatistics` — O(n) observable aggregates
- `TranscriptQualityConfig` — single source of truth for heuristic thresholds
- Timestamp validation: non-finite, negative, end < start, out-of-bounds, out-of-order
- Gap / overlap / duplicate / empty-text / repetitive-text diagnostics
- Very short / very long segment heuristics
- Deterministic quality score in [0.0, 1.0] (not ASR accuracy)
- `create_transcript_quality_validator()` factory

### Files Changed

- `src/transcript_quality.py` (created)
- `tests/test_transcript_quality.py` (created; 65 tests incl. 1 real-Whisper integration)
- `docs/architecture.md` (Task 20 section appended)
- `docs/status.md` (this entry)
- No Knowledge / Review / Evidence modules modified

### Quality Rules (Summary)

| Rule | Severity | Notes |
|---|---|---|
| INVALID_TIMESTAMP | ERROR | non-finite / negative / end < start |
| TIMESTAMP_OUT_OF_BOUNDS | ERROR | only when source_duration supplied |
| SEGMENTS_OUT_OF_ORDER | ERROR | adjacent out-of-chronological-order |
| EMPTY_SEGMENT_TEXT | ERROR | empty or whitespace-only |
| HIGH_EMPTY_SEGMENT_RATIO | ERROR | > 50% empty segments |
| LONG_GAP | INFO / WARNING | > 5s total gap |
| OVERLAPPING_SEGMENTS | INFO / WARNING | > 1s total overlap |
| DUPLICATE_TEXT | WARNING | adjacent exact-duplicate |
| REPETITIVE_TEXT | WARNING | single char repeated 40+ times |
| VERY_SHORT_SEGMENT | INFO / WARNING | < 0.1s; WARNING when >= 50% short |
| VERY_LONG_SEGMENT | WARNING | > 60s |
| CHUNK_METADATA_INVALID | WARNING | chunk_count not int >= 1 |

### Tests

- Baseline: 546
- New tests: 65
- Final total: 611
- Integration test: 1 (gated `@pytest.mark.integration`, skips when model unavailable)

### Known Limitations

- No ground-truth ASR accuracy measurement (by design)
- No semantic quality evaluation
- No automatic repair
- No VAD, diarization, LLM, NLP, or embedding

## Task 21 completed

### Summary

- Implemented src/document_input.py: deterministic PDF / DOCX
  validation and parsing layer.
- Validators: DocumentMaterialValidator (file-level checks, stable
  error codes, no content reads) + validate_document() helper.
- Parsers: PDFDocumentParser (pypdf, one block per page) and
  DOCXDocumentParser (python-docx, non-empty paragraphs + headings +
  tables + image-part count).  Factory create_document_parser() and
  convenience parse_document().
- Structured results: DocumentValidationResult / DocumentInput /
  ParsedDocument / DocumentBlock / ParserError, all with to_dict() /
  from_dict() and deterministic document- / docblock- ids (no uuid4).
- Status values PARSED / PARSED_EMPTY / FAILED; stable error codes
  INVALID_DOCUMENT, PASSWORD_PROTECTED, PARSER_ERROR,
  PARSER_UNAVAILABLE, UNSUPPORTED_FORMAT.
- Added 19 test fixtures under tests/fixtures/documents/ (PDF + DOCX,
  generated offline by scripts/gen_task21_fixtures.py).
- Added pypdf + python-docx to requirements.txt.
- No Knowledge / Review / Evidence / ASR / Whisper modules modified.

### Files Changed

- `src/document_input.py` (created)
- `tests/test_document_input.py` (created; 79 tests, 3 integration)
- `tests/fixtures/documents/*` (created; 19 fixtures)
- `scripts/gen_task21_fixtures.py` (created; offline fixture generator)
- `requirements.txt` (added pypdf>=3.0,<7.0 and python-docx>=1.0,<2.0)
- `docs/architecture.md` (Task 21 section appended)
- `docs/status.md` (this entry)

### Tests

- Baseline: 611
- New tests: 79
- Final total: 690
- Integration tests: 3 (gated `@pytest.mark.integration`)

### Known Limitations

- No OCR of scanned PDF pages (PARSED_EMPTY + diagnostic instead)
- No LLM, NLP, embedding, or semantic extraction
- No image / content extraction inside DOCX (image parts are counted only)
- PDF heading detection not implemented (page-level TEXT blocks only)

## Task 22 - Document Evidence Extraction & Traceability

Status: PASS

### Summary

- DocumentEvidenceExtractor (src/document_evidence.py) converts Task 21
  ParsedDocument blocks into traceable Evidence objects:
  - one non-empty block -> one Evidence, in original document order
  - empty / whitespace blocks skipped (counted in statistics)
  - FAILED / PARSED_EMPTY documents -> no Evidence
- Evidence identity is deterministic and stable:
  doc-evidence-<sha256(document_id|document_type|block_type|location|text)[:24]>
  No uuid4, no current time, no randomness.
- Exact-text preservation: DocumentBlock.text == Evidence.content
  (no translation, no summarization, no mutation of the ParsedDocument).
- Deduplication: exact-deterministic only (same document + location +
  text -> one Evidence; cross-page / cross-document duplicates stay
  distinct).
- SourceReference traceability: page (PDF) / paragraph (DOCX) /
  block location + document_type + block_id + document_id preserved.
- Unified extractor routing: EvidenceExtractor now handles .pdf /
  .docx via parse_document() + DocumentEvidenceExtractor.
- EvidenceType.DOCUMENT added to src/models.py; legacy values unchanged.
- ParsedDocument and Evidence round-trip through to_dict()/from_dict()
  losslessly; extract() accepts dict input too.

### Files changed

- src/models.py (EvidenceType.DOCUMENT added)
- src/document_evidence.py (created; extractor + result + deterministic id)
- src/evidence_extractor.py (routes .pdf/.docx to DocumentEvidenceExtractor)
- tests/test_document_evidence.py (created; 54 tests)
- docs/architecture.md (Task 22 section appended)
- docs/status.md (this entry)

### Tests

- Baseline: 690
- New tests: 54
- Final total: 744
- Integration tests: 5 (gated @pytest.mark.integration, unchanged from Task 21)
- All 744 tests pass (739 non-integration + 5 integration, 0 failed).

### Known limitations

- No OCR of scanned / image-only PDFs (PARSED_EMPTY + diagnostic only)
- No LLM / ASR / embedding / semantic / translation / summarization
- No KnowledgePoint generation in this layer
- Table blocks have location_key "none" in their deterministic id
  (no paragraph_index/page_number); location is carried via
  metadata["table_index"] and source_reference.location == "table"


## Task 23 - Unified Evidence Store & Evidence Lifecycle

Status: PASS

### Files changed

- src/evidence_store.py (created; unified store, canonical identity,
  dedup, query, statistics, snapshot/restore, JSON persistence,
  ACTIVE/RETIRED lifecycle, thread safety)
- tests/test_evidence_store.py (created; 104 tests: identity, dedup,
  query, statistics, lifecycle, serialization, persistence,
  immutability, thread safety, 10k-scale, extractor/integrator/
  knowledge regression, scope audit)
- docs/architecture.md (Task 23 section appended)
- docs/status.md (this entry)

### Tests

- Baseline: 744
- New tests: 104
- Final total: 848
- Integration tests: 5 (gated @pytest.mark.integration, unchanged)
- All 848 tests pass (843 non-integration + 5 integration, 0 failed,
  0 skipped).
- compileall: PASS on src/ and tests/ (9 stale partial files from
  earlier tasks were removed as part of the Task 23 hygiene pass).

### Performance (10,000 synthetic Evidence, 5,000 distinct keys x2)

- 10k batch insert (5,000 ADDED + 5,000 DUPLICATE): ~156 ms
- get_by_id: ~0.013 ms average (O(1), 1,000 lookups in ~13 ms total)
- all() over 5,000 ACTIVE records: ~68 ms (single full scan)
- get_by_type() over 5,000 records: ~59 ms
- statistics() over 5,000 records: ~348 ms (single call, worst case)
- Single snapshot to_dict() of 5,000 records: ~12 ms
- Full restore from_dict() (5,000 records + index rebuild): ~207 ms
- Well within the "no extreme performance required" bar of spec 116.

### Known limitations

- No semantic / fuzzy / embedding deduplication: cross-source or
  near-duplicate content is intentionally kept separate (provenance
  preservation).
- No Evidence truth validation, contradiction detection, source
  priority, or any Knowledge/Review semantics.
- No database, no network, no new dependencies: stdlib JSON files and
  in-memory dict/list structures only.
- Query by type/source/material is O(n) filtered scan over insertion
  order (no inverted index); acceptable at current scale, flagged for
  re-evaluation only if the store grows into the millions.

## Task 24 - Unified Evidence Ingestion & Production Readiness

Status: PASS

### Files changed

- src/evidence_ingestion.py (created; ~1027 lines: source routing,
  extractor adapters for Note/Audio/OCR/Document, single + batch +
  session ingestion, IngestionReport / BatchIngestionReport,
  IngestionHistory, IngestionStatistics, deterministic re-scaffolding
  of evidence refs, store.add_many() as the only dedup path)
- tests/test_evidence_ingestion.py (created; 80 tests: routing,
  single-material cases A-H, batch, idempotency, provenance,
  Unicode round-trip, determinism, history/statistics, session,
  store independence, concurrency + 1000-material performance,
  end-to-end real-fixture chains)
- docs/architecture.md (Task 24 section appended)
- docs/status.md (this entry)

### Tests

- Baseline: 848
- New tests: 80
- Final total: 928
- Integration tests: 5 passed (0 failed, unchanged gated suite)
- All 928 tests pass (923 non-integration + 5 integration, 0 failed,
  0 skipped; full suite ~8.5 s).
- compileall: PASS on src/ and tests/.

### Performance (tests/test_evidence_ingestion.py)

- 1000-material batch ingestion: completes well under the no-obvious-
  O(n^2) bar; per-material isolation keeps failures local.
- Repeated (idempotent) re-ingestion of the same batch: 0 new records.
- 5-thread concurrent ingestion of 50 shared materials: consistent
  counts, no duplicate Evidence in the store.

### Known limitations

- No real ASR/OCR: service defaults to MockASRProvider / MockOCREngine
  when none are injected; WhisperProvider is used only when supplied.
- MaterialType.TEXT materials without a parser route to
  UNSUPPORTED_SOURCE (structured result, not an error crash).
- DOCX table blocks carry location "table" (no page number in
  provenance); PDF carries page numbers.
- No semantic interpretation, knowledge generation, translation, or
  review behavior introduced; the downstream pipeline is untouched.


## Task 25 - Evidence-Backed Knowledge Assembly & Incremental Generation

Status: PASS

### Files changed

- src/knowledge_assembly.py (created; KnowledgeAssembler facade over
  EvidenceStore + KnowledgePipeline: process_store / process_evidence /
  process_evidences, AssemblyResult, O(1) evidence_map_for, per-KP
  material / document / session / source-type provenance helpers,
  validate_integrity, statistics, save_structure / load_structure with
  integrity re-validation on load)
- src/integration.py (_detect_conflicts pre-computes per-evidence
  features and short-circuits the all-pairs scan; ConflictRecord ids
  assigned at creation; EvidenceIntegrationResult now carries
  conflict_refs_by_id for O(1) downstream relevance; _detect_supporting
  pre-computes per-text word lists; _negation_conflict_from_statements
  pre-computes word sets)
- src/knowledge_validation.py (validate_knowledge_point accepts
  evidence_map and an optional inverted conflict_index;
  validate_structure builds both once and hands them to every KP call,
  keeping per-point work proportional to the KP's evidence refs)
- src/knowledge_pipeline.py (PipelineResult carries the integration
  result; _apply_validation passes it through so validation reuses the
  pre-computed conflict refs and inverted index instead of re-scanning)
- tests/test_knowledge_assembly.py (created; 14 classes, 104 tests:
  construction, store / list API, dedup, KP identity, incremental
  idempotency, relationships, conflicts, needs_verification flags,
  multilingual non-merge, statistics, end-to-end fixture chain,
  save / load round-trip, conflict_refs_by_id contract, TestPerformance
  with the 1000-evidence < 120 s bar, repeated-store stability, and
  50-evidence store-vs-direct rebuild equivalence)
- docs/architecture.md (Task 25 section appended)
- docs/status.md (this entry)

### Tests

- Baseline: 928
- New tests: 104 (test_knowledge_assembly.py); 1022 -> 1027 non-integration
- Final total: 1027 non-integration + 5 integration = 1032
- All tests pass: 1027 passed / 5 deselected (non-integration), 5
  passed / 1027 deselected (integration); 0 failed, 0 skipped.
- compileall: PASS on src/ and tests/.

### Performance (tests/test_knowledge_assembly.py::TestPerformance)

- 1000-evidence assembly (500 unique + 500 duplicate patterns, store
  driven): ~5-6 s wall-clock, under the 120 s no-obvious-O(n^2) bar
  (was 135+ s before the conflict_refs_by_id + inverted-index +
  pre-computed-feature optimizations).
- 50-evidence rebuild equivalence: store-driven and direct-list
  assembly produce byte-identical structures.
- Repeated process_store stability: second run over the same 200
  evidences changes neither KP count, validation fields, nor
  statistics.

### Known limitations

- _detect_conflicts / _detect_supporting remain all-pairs by design
  (deterministic, language-agnostic, no semantic matching); the Task 25
  work removes the per-KP and per-call overhead, not the quadratic
  pair scan itself.
- The inverted conflict index only activates when conflicts is an
  EvidenceIntegrationResult carrying conflict_refs_by_id; a plain
  list of ConflictRecord falls back to the reference-set path, and a
  list without pre-computed sets falls back to the original
  O(n_conflicts) scan.
- KnowledgeAssembler.statistics() counts only; it does not score,
  rank, or summarise KPs.
- No ASR / OCR / LLM behavior introduced; this is deterministic
  assembly over already-ingested Evidence only.

## Task 26 - Knowledge Organization & Course-Level Knowledge Structure

Status: PASS

### Files changed

- src/knowledge_organization.py (created; Topic,
  KnowledgeMembership, SessionKnowledgeMembership, KnowledgeRelation,
  report dataclasses, TopicNode, RelationGraph,
  CourseKnowledgeStructure with schema_version=1 to_dict/from_dict
  validation, and KnowledgeOrganizationService with registration,
  topic / membership / relation APIs, coverage reports,
  validation / review aggregation, ingest helpers, save_to_dict /
  from_snapshot; RLock-guarded mutations, deterministic ids only)
- tests/test_knowledge_organization.py (created; 87 tests:
  determinism of all four entity ids, topic hierarchy + cycle
  rejection, topic / KP / session membership idempotency and
  re-ordering, cross-session KP reuse, relations (direction,
  dedup, self-relation, reverse queryable, graph), course / topic /
  session coverage, validation / review aggregation with
  latest-record-wins, unassigned / uncovered queries, dangling
  reference protection, course isolation, from_dict / to_dict
  round-trip and rejection of unknown schema version / cross-course /
  dangling / cycle payloads, incremental update semantics,
  determinism property checks, end-to-end case studies A/B/C,
  and no-inference scope checks)
- docs/architecture.md (Task 26 section appended)
- docs/status.md (this entry)

### Tests

- Baseline: 1032 (1027 non-integration + 5 integration, unchanged
  from Task 25)
- New tests: 87 (test_knowledge_organization.py)
- Final total: 1115 non-integration + 5 integration = 1120
- All tests pass: 1115 passed / 5 deselected (non-integration),
  5 passed / 1115 deselected (integration); 0 failed.
- compileall: PASS on src/ and tests/.

### Determinism

- Topic / membership / relation / structure ids: all content-addressed
  sha256 digests; re-adding identical entities is a no-op; ordering in
  every list / graph / summary output is sort-stable.

### Known limitations

- Coverage is strictly a session concept (KP covered iff it owns at
  least one SessionKnowledgeMembership in this course); a covered
  point is not a supported or confirmed point.
- Relations are explicit only: no transitive closure, no inferred
  relations, no graph inference of any kind.
- get_review_summary reads review records only when the registered
  structure exposes review_records_for_knowledge_point; otherwise the
  KP's own review_status field applies.
- No LLM / embedding / vector DB / graph DB / translation / automatic
  inference introduced; the layer is purely organizational.


## Task 27 completed

### Summary
- Implemented `src/knowledge_coverage.py` - pure, deterministic coverage & gap
  analysis layer on top of Task 26 `KnowledgeOrganizationService`
- New models: `KnowledgeCoverageStatus`, `KnowledgeGapType`, `KnowledgeGap`,
  `KnowledgeCoverageReport`, `TopicCoverageReport`, `SessionCoverageReport`,
  `SessionCoveragePoint`, `CourseCoverageTimeline`, `KnowledgeFrequency`,
  `CourseGapReport`, `ConflictCoverageItem`, `KnowledgeOrphanReport`,
  `KnowledgeEvidenceCoverage`, `ValidationReviewMatrix`,
  `CourseKnowledgeStatusSummary`
- New analysis APIs on `KnowledgeCoverageAnalyzer`:
  `analyze_course`, `analyze_topic`, `analyze_session`,
  `analyze_coverage_timeline`, `analyze_gaps`, `analyze_frequency`,
  `analyze_validation_review`, `get_unassigned_knowledge_points`,
  `get_uncovered_knowledge_points`, `get_conflicted_knowledge_points`,
  `get_unverified_knowledge_points`, `get_review_pending_knowledge_points`,
  `get_repeated_knowledge_points`, `get_single_occurrence_knowledge_points`,
  `get_knowledge_evidence_coverage`, `get_orphan_report`,
  `get_status_summary`
- Coverage semantics: covered = >=1 session membership; assigned = >=1 topic
  membership; independent of validation/review/student mastery
- Gap semantics: UNASSIGNED, UNCOVERED, CONFLICTED, UNVERIFIED,
  REVIEW_PENDING - data states only, never student-deficiency claims
- Determinism: KP ids ascending; topics (order_index, topic_id) with None
  last; sessions numbered ascending then unnumbered, tie-break by id; gap
  types in declaration order; sets used for counting only
- Snapshot idempotency: one frozen snapshot per analyzer; later service
  mutations do not change already-returned reports; re-analysis is
  idempotent
- No LLM / embedding / semantic inference / student model / recommendation /
  translation / database introduced

### Performance
Dataset: 1000 KP, 100 topics, 50 sessions, ~3000 topic memberships,
~5000 session memberships, ~2000 relations
Result: adds < 30s; all report queries < 30s; single O(N+T+S+M+R)
snapshot per analyzer, no repeated O(N x M) scans

### Tests
Baseline (Task 26): 1119
Added (Task 27): 56
Final: 1175
Passed: 1175
Failed: 0

### Compile
python -m compileall src tests
Result: PASS

### Documentation
- docs/architecture.md
- docs/status.md

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
    ClassSession (session_number); calendar dates are not used.

## Task 28 completed

### Summary
- Implemented \src/knowledge_dependency.py\ - pure, deterministic
  knowledge dependency & prerequisite analysis layer on top of
  Task 26 \KnowledgeOrganizationService- New models: \DependencyStatus\ (OK / CYCLE_DETECTED),
  \DependencyCycle\, \DependencyCoverageItem\,
  \DependencyCoverageReport\, \DependencyAnalysis\ - all
  frozen, serializable (\schema_version = 1\; unknown versions
  raise \DependencySchemaError\)
- New analysis APIs on \KnowledgeDependencyAnalyzer\:
  \get_prerequisites\, \get_dependents\,
  \get_prerequisite_chain\, \get_dependent_chain\,
  \get_prerequisite_closure\, \get_dependent_closure\,
  \detect_cycles\, \get_dependency_depth\,
  \get_prerequisite_coverage_report\, \nalyze_course\,
  \save_to_dict\, \rom_snapshot- Semantics: only explicit PREREQUISITE relations are read;
  closures are analysis results, never new relations;
  coverage = >=1 session membership, explicitly not a student
  mastery statement
- Cycles: deterministic \cycle-<sha256(course_id + node path)[:24]>  ids rotated to smallest node; cycle members and downstream get
  (CYCLE_DETECTED, depth -1), no unbounded recursion; self-loops
  handled as two-entry cycles
- Snapshot idempotency: one frozen \_DependencySnapshot\ per
  analyzer; re-analysis is idempotent; reconstruction round-trip
  via \save_to_dict\ / \rom_snapshot- No LLM / embedding / student model / recommendation /
  auto-relation-creation / database introduced

### Performance
Dataset: 1000 KP, 2000 prerequisite relations
Result: single O(N + R) snapshot build; chain/closure queries
memoized; no repeated full-graph scans per call

### Tests
Baseline (Task 27): 1175
Added (Task 28): 40
Final: 1215
Passed: 1210 non-integration + 5 integration
Failed: 0

### Compile
python -m compileall src tests
Result: PASS

### Documentation
- docs/architecture.md
- docs/status.md

### Known limitations
1. Closure analysis does not create or persist any new
   relations.
2. Coverage reports say nothing about student mastery.
3. Cycle depth is reported as a CYCLE_DETECTED state, not a
   numeric depth.
4. Only PREREQUISITE relations feed the dependency graph;
   RELATED / EXTENDS / CONTRASTS / PART_OF are ignored by
   design.
5. No study recommendation or auto-fix is produced.


## Task 29: Knowledge Learning Representation & Grounded Explanation Layer

- **Result**: PASS
- **Module**: `src/knowledge_learning.py` (LearningClaim / LearningRepresentation /
  GroundedKnowledgeBuilder / BuildStatus / stable error codes, schema_version=1,
  deterministic content-addressed ids, no LLM / no translation / no invented facts).
- **Tests**: `tests/test_knowledge_learning.py` — 32/32 passed.
- **Regression**: full non-integration suite green after adding Task 29
  (baseline + 40 Task 28 tests + 32 Task 29 tests, 0 failed); `compileall` PASS.
- **Documentation**: `docs/architecture.md` + `docs/status.md` updated.
- **Known limitations**: `GroundedKnowledgeBuilder` uses evidence language
  heuristics (evidence.registry entries may have UNKNOWN language -> no
  availability constraint unless the caller supplies `language_index`).
  Future: concrete `LearningRepresentationProvider` implementations.

## Task 30: Student & Learning State Layer

- **Result**: PASS
- **Module**: `src/student_learning.py` (Student / LearningState /
  LearningEventType / LearningEvent / StudentKnowledgeRecord /
  StudentLearningLog / reduce_events; schema_version=1, deterministic
  content-addressed event ids, legal state machine, event-log source of
  truth, derived immutable records, no mastery inference).
- **Tests**: `tests/test_student_learning.py` — 34/34 passed.
- **Regression**: full non-integration suite green after adding Task 30
  (1276 total, 0 failed); `compileall` PASS.
- **Documentation**: `docs/architecture.md` + `docs/status.md` updated.
- **Known limitations**: `record_event` with an explicit caller-built
  event is accepted even if its sequence is *newer* than the log head
  (gaps in the sequence are allowed; only duplicate event_ids are
  no-ops). No mastery / weakness model by design (spec 30.14).

## Task 31: Exercise Layer

- **Result**: PASS
- **Module**: `src/exercises.py` (ExerciseType MULTIPLE_CHOICE /
  TRUE_FALSE / SHORT_ANSWER / FILL_BLANK; frozen, content-addressed
  `exercise-<sha24>` ids; per-type payload validation; stable
  `ExerciseErrorCode`s; `CourseExerciseValidator` with strict
  cross-KP-evidence rejection and an explicit permissive mode;
  `ExplicitExerciseBuilder` with caller-supplied content only;
  `ExerciseGenerator` reserved seam that raises `NotImplementedError`;
  `to_dict`/`from_dict` schema_version=1 with tamper guard).
- **Tests**: 40 tests in `tests/test_exercises.py` (all pass).
- **Regression**: 1316 passed, 0 failed (non-integration);
  compileall PASS.
- **Notes**: no LLM, no automatic question generation, no semantic
  grading in this layer; content (es/ca/zh) preserved verbatim;
  StudentAnswer->Evidence and Evaluation->KP.content remain forbidden.

## Task 32: Answer Evaluation Layer

- **Result**: PASS
- **Module**: `src/answer_evaluation.py` (`StudentAnswer` with
  deterministic `answer-<sha24>` id; `EvaluationStatus`
  CORRECT/INCORRECT/PARTIAL/UNSUPPORTED; `EvaluationResult` with
  `evaluation-<sha24>` id and `evaluator_version = "exact-v1"`;
  `ExactEvaluator` — MCQ exact choice match, T/F direct comparison,
  FILL_BLANK exact match (no trim / casefold, documented), SHORT_ANSWER
  exact-only with semantic non-matches UNSUPPORTED; `AnswerEvaluationLog`
  append-only, idempotent, student-isolated, with `answered_event()`
  describing Task-30 ANSWERED events without mutating records).
- **Tests**: 27 tests in `tests/test_answer_evaluation.py` (all pass).
- **Regression**: 1343 passed, 0 failed (non-integration);
  compileall PASS.
- **Notes**: no LLM, no network, no semantic grading; student answers
  never enter EvidenceStore; unknown exercise ids rejected; duplicate
  answers / evaluations are no-ops; es/ca/zh submitted values kept
  verbatim.

## Task 33: Study Planning & Learning Path

- **Result**: PASS
- **Module**: `src/study_plan.py` (`StudyReason` — 6 rule-trigger
  codes, no WEAK_KNOWLEDGE; `StudyItem` / `StudyPlan` frozen with
  deterministic `plan-<sha24>` ids + tamper guard; `LearningPath`
  with PREREQUISITE-only chains, DEPENDENCY_CYCLE handling, unknown-
  target status; `StudyPlanner` — hardcoded rule priorities:
  prerequisite gaps > recent incorrect > low practice > review
  pending > unverified/conflicted; conflicted = content review
  signal, never student weakness; caller-defined recent-incorrect
  window; no datetime.now / LLM / ranking).
- **Tests**: 28 tests in `tests/test_study_plan.py` (all pass:
  paths, multi-hop, diamond, cycle, uncovered/covered prereqs,
  recent incorrect, low practice, review pending, conflicted,
  combined reasons, deterministic ordering, same/changed state,
  student isolation, immutability, explainability, serialization +
  tamper, 1000-KP performance).
- **Regression**: 1371 passed, 0 failed (non-integration);
  compileall PASS.

## Tasks 28–33 + Integration + Performance: Long-Run Phase complete

### Final Long-Run Report (spec section 28 format)

```text
========================================
Long-Run Phase 28-33 Result
========================================

Task 28: PASS
Task 29: PASS
Task 30: PASS
Task 31: PASS
Task 32: PASS
Task 33: PASS

----------------------------------------
Architecture
----------------------------------------

Dependency:
PASS

Learning Representation:
PASS

Student State:
PASS

Exercise:
PASS

Evaluation:
PASS

Study Planning:
PASS

----------------------------------------
Tests
----------------------------------------

Baseline (Task 1-27): 1175
Task 28 added: 40
Task 29 added: 32
Task 30 added: 34
Task 31 added: 40
Task 32 added: 27
Task 33 added: 28
Integration + Performance added: 9

Final: 1385
Passed: 1385
Failed: 0

Integration: 9/9 (all -m integration tests pass)

----------------------------------------
Validation
----------------------------------------

Determinism: PASS (content-addressed ids throughout)
Traceability: PASS (all KPs trace to evidence; claims to sources)
Incremental: PASS (append-only logs; idempotent re-adds)
Idempotency: PASS (duplicate events / answers / evaluations are no-ops)
Serialization: PASS (to_dict / from_dict round-trips + tamper guards)
Multilingual: PASS (es / ca / zh stored verbatim; no auto-translation)
Course isolation: PASS (events / relations / sessions are course-scoped)
Student isolation: PASS (logs and plans are per-student; no cross-contamination)

----------------------------------------
Compile
----------------------------------------

python -m compileall src tests
PASS (exit 0)

----------------------------------------
Dependencies
----------------------------------------

New external dependencies:
NONE (standard library only; no networkx / pandas / numpy / LLM / vector DB)

LLM:
NOT REQUIRED

Network:
NOT REQUIRED

Database:
NOT REQUIRED

----------------------------------------
Documentation
----------------------------------------

docs/architecture.md: UPDATED
docs/status.md: UPDATED

----------------------------------------
Known Limitations
----------------------------------------

- StudentAnswer never enters EvidenceStore (by design, spec 32.9).
- Evaluation produces no PARTIAL status (no explicit rubric in this
  layer; spec 32.7).
- SHORT_ANSWER semantic grading is UNSUPPORTED by design (spec 32.8);
  only exact string equality is graded.
- CONFLICTED surfaces as a content-review signal, NOT as student
  weakness (spec 33.6); no mastery model anywhere in this phase.
- LearningPath returns DEPENDENCY_CYCLE without a per-cycle walk when
  any back edge is found (deterministic, spec 33.16).

----------------------------------------
Scope Confirmation
----------------------------------------

No UI
No database
No authentication
No automatic mastery inference
No semantic grading
No automatic translation
No hallucinated knowledge
No automatic conflict resolution
========================================
```

========================================
Task 34 - Unified Application Service / API layer
========================================

Date: 2026-09-15
Base (after Task 1-33): 1385 passed

----------------------------------------
New Modules
----------------------------------------

src/application/:
- errors.py: ApplicationError (8 error codes) + map_application_error()
- dto.py: 8 *_to_dict helpers + to_jsonable()
- course_service.py: CourseService (course/session/material registry)
- knowledge_service.py: KnowledgeService + ReviewService (org + review projection)
- material_service.py: MaterialService (register/ingest/batch/status, idempotent)
- learning_service.py: LearningService (student/exercise/answer/evaluation/study plan)
- app_service.py: AppService facade (composes all sub-services + register_knowledge_point)
- __init__.py: public API exports

Key fixes in this task:
- course_service.list_sessions/list_materials: removed invalid tuple unpacking
  (sorted() returns objects, not (key,value) pairs)
- knowledge_service.get_gaps: returns flat {course_id, gaps[]} dict
- knowledge_service.get_conflicts: reads per-KP KnowledgeStructure
  (org._structure_by_kp), not CourseKnowledgeStructure (no .conflicts)
- knowledge_service.get_evidence_for_knowledge_point: uses _kp_dict
  (CourseKnowledgeStructure has no .knowledge_points dict)

----------------------------------------
Tests added (Task 34)
----------------------------------------

tests/test_application_core.py: 28 (Course/Session/Material + error mapping)
tests/test_application_knowledge.py: 35 (Knowledge + Review service)
tests/test_application_learning.py: 45 (Learning service)
tests/test_application_app.py: 14 (AppService facade end-to-end)

Total Task 34 tests: 122

----------------------------------------
Test Summary
----------------------------------------

Baseline (Task 1-33): 1385 passed
Task 34 added: 122 passed
Final: 1507 passed, 14 deselected (integration)
Failed: 0

Note: the 14 deselected are integration/performance tests
(-m integration), excluded from the standard run.

----------------------------------------
Validation
----------------------------------------

Determinism: PASS (content-addressed IDs; no uuid4/datetime.now/random in app layer)
Idempotency: PASS (course/session/material/student/exercise/answer/evaluation)
DTO boundary: PASS (all service methods return flat dicts, never domain objects)
Error mapping: PASS (domain exceptions -> structured ApplicationError, no traceback leak)
Compile: PASS (python -m compileall src tests, exit 0)
No new external dependencies.

----------------------------------------
Scope Confirmation
----------------------------------------

No UI / No database / No auth / No LLM / No network / No DB.

========================================
Task 35 - Real Classroom Material Workflow
========================================

Date: 2026-09-15
Base (after Task 34): 1493 passed

----------------------------------------
New / Changed Modules
----------------------------------------

src/application/data_dirs.py (new):
- DATA_LAYOUT_DIRS: materials / audio / images / documents / database /
  logs / backups / temp  (spec layout, created idempotently)
- DataLayout dataclass + ensure_data_layout(data_dir)
- safe_join(): rejects ".." segments and absolute path parts, verifies the
  resolved path stays inside the root
- atomic_write_bytes / atomic_copy: temp file -> fsync -> os.replace, so a
  crash never leaves a half-written file and failures never leak temp files
- file_sha256 / remove_quietly / clear_directory / iter_files

src/application/runtime.py (new):
- utc_now_iso(), new_runtime_token(), fixed_clock()
- the ONLY sanctioned home for non-deterministic values; explicitly barred
  from business identity (Task 47.2 audit target)

src/application/material_workflow.py (rewritten):
- status machine REGISTERED -> VALIDATING -> PROCESSING -> COMPLETED/FAILED
- extension table -> category -> storage bucket routing
  (documents / audio / images)
- file identity = sha256(course_id | filename | content_hash); same content
  under a different name reuses the existing copy (duplicate_of) instead of
  duplicating bytes on disk
- registry persisted atomically to data/materials/<course_id>.json, so
  materials survive a process restart and re-upload is still detected
- retry_material(): only RETRYABLE_ERROR_CODES, capped by max_attempts=3;
  structural rejections (path traversal / unsupported / zero byte /
  oversized / missing) are never retried
- failure isolation in register_material_batch (one bad file cannot abort
  the batch); register_knowledge_point refuses evidence_refs that do not
  exist in the store (no fabricated facts)
- get_processing_status() / list_rejected() / list_session_materials() /
  cleanup() / cleanup_temp()

src/application/app_service.py:
- optional data_dir= / source_root= / max_file_size= / max_attempts= /
  clock= keyword arguments; when data_dir is absent the workflow service is
  deliberately NOT created (files must never be read in place)

----------------------------------------
Security model
----------------------------------------

- User originals are never opened for writing and never processed in place;
  every accepted file is copied into data_dir first.
- Path traversal rejected before the file is touched (both separators).
- Zero-byte, oversized, unsupported-extension, missing-file and directory
  inputs are rejected as structured data (no exception to the caller).
- Destination paths are built from content hashes and joined through
  safe_join(), so no user-controlled string reaches the filesystem layout.
- Cleanup only removes data_dir copies; source_root is never touched.

----------------------------------------
Tests added (Task 35)
----------------------------------------

tests/test_material_workflow.py: 118 tests
  data layout 15 / registration 20 / rejection 13 / duplicates 7 /
  validation 6 / processing 13 / retry 8 / registry+restart 8 /
  batch 5 / status+cleanup 13 / construction 5 / knowledge boundary 5

Spec required >= 50.

----------------------------------------
Test Summary
----------------------------------------

Baseline (Task 1-34): 1493 passed
Task 35 added: 118 passed
Final: 1611 passed, 14 deselected (integration)
Failed: 0

----------------------------------------
Validation
----------------------------------------

Determinism: PASS (content-addressed material_id; clock injected; created_at
  is runtime metadata only, never part of identity)
Idempotency: PASS (re-upload, batch re-run, reprocess, re-register KP)
Compile: PASS (python -m compileall src tests, exit 0)
No new external dependencies.

========================================
Task 36 - Production OCR Provider
========================================

Date: 2026-09-15
Base (after Task 35): 1611 passed

----------------------------------------
Engine selection
----------------------------------------

Chosen: rapidocr-onnxruntime 1.2.3 (RapidOCR / PP-OCRv4 ONNX).

Why this engine:
- Runs fully local, offline, CPU by default; no cloud API, no API key.
- ONNX models ship INSIDE the wheel -> no first-run download, no model
  cache to manage, no network dependency at OCR time.
- No PyTorch / CUDA stack: the only native dependency is onnxruntime,
  which was already present in the environment.
- 12 MB wheel, ~1.4 s model load, ~1 s per 1100x330 page on CPU.

Version constraint discovered during implementation:
  rapidocr-onnxruntime 1.3.x and 1.4.x declare ``Requires-Python <3.13``.
  On the project runtime (Python 3.14.7) the ONLY installable release is
  1.2.3, therefore requirements.txt pins it exactly rather than using a
  range. This is a real constraint, not a preference, and is documented
  in requirements.txt.

Alternative engines rejected:
- pytesseract: needs a system Tesseract binary (external installer,
  not reproducible from pip alone).
- easyocr: pulls the full PyTorch stack (>2 GB) for a single-user local
  app; violates the "no oversized GPU stack" rule.

----------------------------------------
New Modules
----------------------------------------

src/ocr_provider.py:
- OCRProvider ABC (name / is_available() / ocr())
- ImageInput (path / material_id / page / language_hint) + from_material()
- OCRRegion (text / confidence / bounding_box / language)
- OCRProviderResult (provider / material_id / text / confidence / language /
  regions / bounding_box / source_reference / provider_version / model /
  warnings) + to_dict()
- MockOCRProvider: deterministic placeholder, explicitly labelled
  (warnings=("MOCK_OCR_OUTPUT",), text contains "mock") so it can never be
  mistaken for real extraction
- LocalOCRProvider: lazy model loading, injectable engine_loader,
  min_confidence threshold filter, structured error mapping
- ProviderOCREngine: adapts OCRProvider -> the existing domain OCREngine
  protocol, so the Task 24 ingestion pipeline can use real OCR without any
  change to the domain layer
- is_local_ocr_available() / create_ocr_provider(kind, require_real=...)
- _split_engine_output / _coerce_confidence / _polygon_to_bbox

Engine quirks handled (verified against the real engine):
- RapidOCR 1.2.x returns the confidence as a STRING ("0.9081..."), so a
  faithful type coercion is applied; garbage / NaN / inf -> None.
- The engine's return contract is (result, elapse) where result is None
  when nothing was detected. None is a legitimate EMPTY SUCCESS
  (warnings=("NO_TEXT_DETECTED",)), not an error.
- The engine does NOT perform language identification, so ``language``
  is None in every result. It is never guessed and the caller's
  language_hint is never echoed back as if it were a detection.

Determinism:
- regions are sorted by (y, x, text); text is the newline join in that
  order; confidence is the mean of region confidences rounded to 6 dp.
- repeated invocation on the same image returns byte-identical dicts
  (verified against the real engine, not just the fake one).

Prohibited and NOT implemented: auto-translation, LLM post-processing,
automatic fact correction, silent fallback from real to mock.

----------------------------------------
Dependencies
----------------------------------------

Added to requirements.txt:
- rapidocr-onnxruntime==1.2.3  (exact pin; Python <3.13 restriction)
- av>=10.0, numpy>=1.24  (declared explicitly; previously only transitive)

Optional: onnxruntime-gpu + CUDA provider for acceleration. CPU is the
default and needs nothing extra.

----------------------------------------
Tests added (Task 36)
----------------------------------------

tests/test_ocr_provider.py: 91 tests (85 unit + 6 real-engine integration)
  errors 4 / ImageInput 6 / region+result 7 / mock provider 8 /
  local provider (fake engine) 28 / invalid images 6 / normalisation 11 /
  factory 8 / domain adapter 9 / real local OCR 6 (@pytest.mark.integration)

Spec required >= 40. The 6 real-engine tests were also run explicitly
(-m integration) and pass against rapidocr-onnxruntime 1.2.3.

New test fixture: tests/fixtures/ocr_sample.png (1100x330, Latin with
accents + Catalan + CJK), generated once and committed so the integration
test does not depend on a font at runtime.

----------------------------------------
Test Summary
----------------------------------------

Baseline (Task 1-35): 1611 passed
Task 36 added: 85 (non-integration) / 91 (including integration)
Final: 1696 passed, 20 deselected (integration)
Failed: 0

----------------------------------------
Validation
----------------------------------------

Real OCR: PASS (latin + Catalan + CJK recognised; deterministic)
Lazy loading: PASS (model not loaded until first ocr() call)
Model load failure: PASS (structured OCRProviderUnavailableError, no traceback)
Invalid image: PASS (structured InvalidImageError before engine load)
Empty image: PASS (empty success + NO_TEXT_DETECTED warning)
Unicode: PASS (byte-exact preservation through the mapping layer)
No fabrication: PASS (language=None, missing fields=None)
Compile: PASS (python -m compileall src tests, exit 0)


========================================
Task 37 - Full Classroom Recording Pipeline
========================================

Date: 2026-09-15
Base (after Task 36): 1696 passed

----------------------------------------
New Modules
----------------------------------------

src/application/processing_service.py:
- ClassroomProcessingService orchestrating
  Material -> Ingestion -> Evidence -> Knowledge -> Validation -> Review
  -> Course Knowledge
- ProcessingJob (job_id / material_id / course_id / session_id / status /
  stage / attempts / max_attempts / started_at / finished_at / error /
  error_detail / retryable / evidence_ids / warnings / quality)
- Job status machine QUEUED -> RUNNING -> SUCCEEDED / FAILED / CANCELLED
  (spec set), plus an observable stage field
  (QUEUED / INGESTING / EVIDENCE / KNOWLEDGE / DONE)
- API: start_session_processing / process_material / process_session /
  get_status / get_job / retry_failed_material / cancel_session /
  assemble_knowledge / get_knowledge_summary
- job_id is deterministic: sha256(course_id | material_id)[:20]

src/application/audio_pipeline.py:
- LongAudioASRProvider: wraps any ASRProvider with Task 18's
  LongAudioProcessor (deterministic chunking + sequential ASR + remap to
  the global timeline). Explicit, recorded degradation
  (DURATION_PROVIDER_UNAVAILABLE) when PyAV is missing - never a silent
  fake success.
- QualityCheckedASRProvider: runs TranscriptQualityValidator AFTER
  transcription, records reports (globally and per material_id), and
  provably does not alter the transcript.
- build_audio_chain(): single place that defines the audio chain
  ASR -> LongAudio -> QualityCheck, returned object is both the ingestion
  asr_provider and the processing service's quality_source.

src/application/material_workflow.py:
- new public org_service property (lazily creates and caches the
  KnowledgeOrganizationService so the workflow and the processing service
  share exactly one instance)
- DOCUMENT_PARSE_FAILED moved to NON_RETRYABLE_ERROR_CODES: a corrupt
  document is a property of the file, retrying cannot change the outcome

src/application/app_service.py:
- creates ClassroomProcessingService alongside MaterialWorkflowService
  when data_dir is supplied (optional quality_source= injection)

----------------------------------------
Behaviour guarantees
----------------------------------------

Failure isolation: 5 materials with 1 corrupt PDF and 1 corrupt DOCX
  -> total=5, succeeded=3, failed=2; the session never fails as a whole.
Bounded retry: only RETRYABLE_ERROR_CODES, capped by max_attempts=3;
  structural rejections and non-retryable errors are refused outright.
Sequential by default: no multiprocessing / Celery / queue - verified by
  an interleaving test (enter/exit/enter/exit).
No fabricated knowledge: assemble_knowledge() only consumes Evidence that
  actually exists in the EvidenceStore; the assembler never creates
  ReviewRecords, so review_status stays "pending" after a full run.
Traceability: every job's evidence_ids match the material's evidence in
  the store, and each evidence carries its source material_id.

----------------------------------------
Tests added (Task 37)
----------------------------------------

tests/test_processing_service.py: 54 tests
  job model 4 / end-to-end 12 (PDF, DOCX, note, image, audio, knowledge,
  traceability, idempotency) / failure isolation 5 / retry 4 /
  queue+cancel 5 / determinism+status 8 / knowledge assembly 4 /
  audio pipeline 10 / sequential execution 2

Spec required >= 20 end-to-end tests.

----------------------------------------
Test Summary
----------------------------------------

Baseline (Task 1-36): 1696 passed
Task 37 added: 54 passed
Final: 1750 passed, 20 deselected (integration)
Failed: 0

----------------------------------------
Validation
----------------------------------------

End-to-end: PASS (real fixtures: simple.pdf, simple.docx, tone_3s.wav,
  ocr_sample.png, .txt)
Failure isolation: PASS
Retry bound: PASS (max_attempts=3, centralised)
Determinism: PASS (deterministic job ids and processing order)
Compile: PASS (python -m compileall src tests, exit 0)
No new external dependencies.

========================================
Task 38 - Local Web API
========================================

Date: 2026-09-15
Base (after Task 37): 1750 passed

----------------------------------------
Framework decision (spec rule 9)
----------------------------------------

Chosen: Python standard library only (http.server.ThreadingHTTPServer).

Justification: the target is single-machine, local, single-user, Windows
first. A framework (FastAPI/Flask) would pull in an ASGI server, a
validation library and their transitive dependency trees to provide
routing + JSON + one upload endpoint. That is not proportionate, so the
API is built on ThreadingHTTPServer plus ~250 lines of routing code.
Zero new dependencies.

Security defaults:
- binds 127.0.0.1 by default; 0.0.0.0 is never the default
- allow_reuse_address is disabled on Windows, otherwise two processes can
  silently bind the same port and "port already in use" is never detected
- Content-Length above the upload limit -> 413 without reading the body
- static path resolution normalises and rejects any escape from src/web

----------------------------------------
New Modules
----------------------------------------

src/application/workspace.py:
- Workspace: multi-course composition root (AppService is single-course)
- CourseContext: per-course org service, workflow, processing, knowledge,
  review and learning services
- Shared globally: EvidenceStore + EvidenceIngestionService
  Isolated per course: KnowledgeOrganizationService (course knowledge is
  never mixed)
- Single material registry (the course workflow); CourseService's own
  material registry is deliberately not used on this path
- asr_mode / ocr_mode are explicit and surface in /api/health; a mock is
  never presented as real Whisper/OCR
- health() returns application / version / database / storage / processing

src/api/responses.py:
- success/failure envelopes; 8 structured error codes -> HTTP status
- json_bytes: sorted keys, UTF-8, non-ASCII preserved (deterministic)
- failure_from_exception: never leaks a traceback

src/api/router.py:
- Router with {param} path segments, 404 / 405 discrimination
- Request with query/JSON body helpers
- parse_multipart_file: minimal multipart/form-data file extraction
  (stdlib only; a single file field is all the UI needs)

src/api/endpoints.py:
- the spec endpoint list (flat, course_id as a query parameter):
  health / courses / sessions / materials (+ upload, process, retry,
  evidence) / processing / knowledge (+ evidence, coverage, gaps,
  dependencies, conflicts) / reviews (+ confirm, reject,
  keep-unverified, resolve-conflict) / students (+ state,
  learning-status) / exercises / answers / evaluations /
  study-plans / learning-paths
- upload accepts raw body + X-Filename OR multipart/form-data
- unsupported extension -> 415, oversized -> 413, traversal filename is
  reduced to its basename and the destination is chosen by the server

src/api/server.py:
- ApiServer / create_server: start/stop/serve_forever, ephemeral port
  support (port=0) for tests, readable "Port XXXX is already in use"
- static serving of the Web UI from src/web with traversal protection
- all non-ApplicationError exceptions pass through map_application_error,
  so domain KeyErrors become 404 rather than 500

----------------------------------------
Bugs found and fixed while testing
----------------------------------------

1. Upload staging renamed the file to its content hash, so the material
   record lost the user's original filename (provenance loss). The stage
   directory is now hash-named but keeps the original basename.
2. GET /api/processing reported 0 jobs for registered-but-unprocessed
   materials. get_status() now materialises QUEUED jobs for every
   registered material.
3. KeyError from the knowledge layer surfaced as 500 INTERNAL_ERROR; the
   server now routes every exception through map_application_error.
4. Workspace.review_history called a method that lives on ReviewService,
   not KnowledgeService (AttributeError -> 500).
5. Port conflicts were undetectable on Windows (SO_REUSEADDR semantics).
6. LearningService had no way to see the course knowledge base, so
   study plans failed with "course must have at least one KP". Added
   register_course_knowledge_points() and a one-way sync
   knowledge base -> learning layer in Workspace.
7. GET /api/reviews/{id} returned an empty history for a non-existent
   knowledge point; it now 404s after verifying existence.

----------------------------------------
Tests added (Task 38)
----------------------------------------

tests/test_api_server.py: 121 tests, all over real HTTP against a real
server instance (urllib):
  responses 11 / router 16 / health 5 / courses 10 / sessions 7 /
  materials 15 / processing 7 / knowledge 9 / review 4 / students 9 /
  exercises+answers+evaluations 11 / static+errors 8 / lifecycle 7

Spec required >= 50.

----------------------------------------
Test Summary
----------------------------------------

Baseline (Task 1-37): 1750 passed
Task 38 added: 121 passed
Final: 1871 passed, 20 deselected (integration)
Failed: 0

----------------------------------------
Validation
----------------------------------------

HTTP semantics: PASS (200/201/400/404/405/409/413/415/422/500/503)
No traceback leak: PASS (500 responses contain no message or path detail
  unless debug=True)
Serialization: PASS (all DTOs are flat JSON dicts)
Determinism: PASS (sorted-key JSON; deterministic course/session/material ids)
Compile: PASS (python -m compileall src tests, exit 0)
No new external dependencies.

========================================
Task 39 - Web Dashboard
========================================

Goal
----------------------------------------

建立第一版真正可用的 Web UI: 用浏览器访问本机服务, 从 Dashboard 一路下钻到
"知识点 -> 证据 -> 源材料"的完整溯源链。

设计取舍
----------------------------------------

1. 前端技术: HTML + 手写 CSS + Vanilla JS, **零构建链**。
   没有 React / Vue / Vite / webpack / npm 依赖, 因此:
   - 部署就是"复制 src/web 三个文件";
   - 离线可用, 不需要 node_modules, 不需要 CDN;
   - `src/web` 里只有 index.html / styles.css / app.js, 没有构建产物。
   代价: 没有组件化与类型检查; 但本项目单用户、页面数量有限, 收益大于成本。

2. 路由: hash 路由 (`#/courses/{id}/knowledge/{kid}`)。
   服务端对未命中的静态路径回落到 index.html (SPA fallback), 因此深链
   刷新可以直接打开, 不需要服务端路由表。

3. 数据访问: 只通过 `/api/*`。UI 不读文件、不含业务规则、不碰 domain 对象。
   分层保持 UI -> HTTP API -> Application Service -> Domain -> Persistence。

4. 安全: 所有来自 API 的文本 (证据原文、文件名、标题 —— 都是任意用户内容)
   一律先经 `esc()` 转义再进 DOM。API 层不做转义, 而是**逐字保留**原始
   字节, 由渲染层负责转义 —— 职责边界清晰。

新增 endpoint (及理由)
----------------------------------------

Task 38 的 endpoint 都是细粒度资源接口; UI 首页与溯源页需要一次请求拿到
完整快照, 否则会出现 8+ 个并发请求且部分失败难以呈现。因此新增两个
**纯只读投影** endpoint:

- `GET /api/dashboard?course_id=`
  一次返回 Courses / Sessions / Materials / Processing status /
  Knowledge points / Review pending / Students / Exercises / Learning gaps
  (spec 对首页的 9 项要求全部覆盖)。未指定 course_id 时选择 course_id
  最小的课程 (确定性); 没有任何课程时返回空快照 —— 空状态也是合法状态。
- `GET /api/knowledge/{knowledge_id}/trace?course_id=`
  核心 UX: 一次拿到 `knowledge_point` + `evidence` + `links` +
  `materials` + `sessions` / `source_sessions` + `topics` +
  `dependencies` + `language` + `complete`。

两者都**不写任何状态**, 且把"溯源断链"显式暴露:
`unresolved_material_ids` 列出所有无法解析的材料引用, `complete=false`。
证据链断了必须看得见, 而不是静默丢弃。

关于 KnowledgePoint 的 `language` 字段
----------------------------------------

KnowledgePoint DTO **没有** language 字段。UI 规格要求显示 language, 但
绝不臆造: trace 返回
`language = {declared: null, evidence_languages: [...], note: "..."}`,
页面显示"未声明 (证据语言: ...)"。这是诚实的状态报告。

关于 `session` 与 `topic`
----------------------------------------

- `source_sessions`: 由证据链**推导**的课堂归属 (证据 -> 材料 -> 材料的
  session_id)。这是证据优先的事实。
- `sessions`: 组织层**显式声明**的归属 (KnowledgeOrganizationService)。
- `topics`: 组织层显式主题归属。

三者分开报告, 全部可能为空。UI 对空值显示"未关联课堂 / 未归入主题",
不假装有数据。当前流水线不会自动分配 topic —— 主题归类是人工组织行为,
`KnowledgeCoverageAnalyzer` 本身也把"未分配 topic"报告为 gap。

交付文件
----------------------------------------

新增 (静态资源):
- src/web/index.html   页面外壳 (顶栏 / 侧边课程栏 / #view 挂载点 / 页脚)
- src/web/styles.css   手写样式, 跟随系统浅色/深色偏好, 含溯源链样式
- src/web/app.js       API 客户端 + hash 路由 + 各页面渲染 + 动作处理

修改 (应用/API 层):
- src/application/workspace.py      新增 knowledge_trace() / dashboard()
- src/api/endpoints.py              新增 /api/dashboard 与 /trace 路由;
                                    上传暂存改用 sanitize_filename
- src/application/data_dirs.py      新增 sanitize_filename()
- src/application/material_workflow.py
                                    register_material(..., filename=) 显式
                                    文件名通道; 校验并只取基名
- src/api/router.py                 multipart 部件头部改为 UTF-8 优先解码
- src/application/knowledge_service.py
                                    修复审核视图陈旧缺陷 (见下)

页面清单
----------------------------------------

- Dashboard (`#/`): 9 项统计卡 + 处理状态表 + 知识健康度 + 材料表 +
  待审核 + 学习缺口; Mock 模式时顶部常驻警示条。
- 课程页 (`#/courses/{id}`): 课堂表 (含"整堂处理"按钮) + 知识点表 +
  学生 + 练习 + 缺口。
- 课堂页 (`#/courses/{id}/sessions/{sid}`): 本堂材料 + 整堂处理 +
  本堂知识点。
- 知识点页 (`#/knowledge`): 全课程知识点表 (验证/审核/分数/证据数/原文术语)。
- 知识点详情 (`#/courses/{id}/knowledge/{kid}`) —— 核心页:
  陈述 (原文) + 语言/验证/审核/分数/重要度/置信度/原文术语/主题/来源课堂/
  组织归属 + **溯源链可视化** (每个证据节点下挂源材料节点, 含文件名、
  material_id、类型、大小、课堂、状态、内容哈希、受管路径; 断链节点标红)
  + 依赖关系 (出边/入边/声明式关联) + 人工审核面板 (确认 / 保持未验证 /
  拒绝 / 解决冲突, 含证据勾选与备注)。
- 材料页 (`#/materials`): 上传表单 (FormData, 支持 pdf/docx/txt/md/音频/图片)
  + 材料表 + 处理/重试/查看证据。
- 待审核页 (`#/reviews`): 待审候选 + 跳转到知识点审核面板。

发现并修复的真实缺陷 (全部由本任务的测试暴露)
----------------------------------------

1. **审核视图陈旧 -> 所有审核动作 404** (src/application/knowledge_service.py)
   `ReviewService.__init__` 只投影一次 KP 集合, 而课程上下文在**还没有任何
   知识点**时就被创建, 于是审核层永远认为"知识点不存在", confirm / reject /
   keep_unverified / resolve_conflict 全部返回 NOT_FOUND。
   修复: 把视图改成"按需刷新 KP 集合、保留审核记录" ——
   `_kp_view_for_review(service, view)` 支持原地刷新, `_review_structure`
   变成 property。审核记录与冲突仍落在同一个持久对象上, 历史不丢。
   影响: Task 39 审核面板、Task 47.10 审核安全审计都依赖此修复。

2. **上传暂存使用原始文件名 -> Windows 上 500** (src/api/endpoints.py)
   `<` `>` `:` `"` `|` `?` `*` 在 HTTP 里合法但在 NTFS 文件名里非法。
   上传 `Tema<1>: notas?.txt` 会直接 500。
   修复: 暂存名经 `sanitize_filename()` 净化 (确定性: 非法字符替换为 `_`,
   去尾部点/空格, 处理 Windows 保留设备名与 255 字符上限, 保留扩展名),
   同时通过 `register_material(..., filename=原始名)` 把**原始文件名逐字
   保留**在溯源字段里。落盘安全与溯源保真两不误。

3. **multipart 文件名按 latin-1 解码 -> 非 ASCII 溯源乱码** (src/api/router.py)
   WHATWG 规定浏览器把 FormData 文件名以 UTF-8 字节写进 Content-Disposition,
   而 HTTP 头部默认 latin-1。上传 `Tema 1 - Introducció a la funció.txt`
   会得到乱码文件名, 直接破坏溯源。
   修复: `_decode_header_text()` UTF-8 优先、失败退回 latin-1
   (纯 ASCII 头两者等价, 既有行为不变)。
   已知未支持: RFC 5987 `filename*=UTF-8''...` —— 浏览器不为 FormData 生成它。

4. **dashboard 处理状态字段名错误** (`processing.counts` 不存在)
   实际字段是 `by_status`; UI 会显示空统计。修复后 UI 只显示非零状态。

5. **导航高亮逻辑错误**: `href.indexOf('#/') === 0` 让所有导航项同时高亮;
   改为精确匹配。

6. **`/api/dashboard` 缺少处理汇总初值**: 无课程时 `processing` 结构不完整,
   UI 读取会拿到 undefined。补齐 `total / by_status / jobs / evidence_total`。

测试清单 (66 tests)
----------------------------------------

tests/test_web_ui.py (66 tests)

  1. 静态资源 HTTP 语义与路径安全 (9)
     index/styles/app.js 状态码 + Content-Type + charset + no-store +
     nosniff; 未知路径回落 shell (SPA 深链); 7 组路径逃逸 payload 断言
     **绝不泄露 src/web 之外的文件字节** (断言安全属性而非状态码, 因为
     SPA 回落本身就是设计行为)。
  2. 资源自包含 / 无构建链 / 无外部依赖 (10)
     index.html 只引用本地资源; app.js 无 http(s)/cdn/import/require/
     node_modules; styles.css 无 @import/url(); 挂载点齐全; 导航覆盖
     Dashboard/知识点/材料/待审核; 路由函数齐全; `esc()` 存在且被用于
     文件名与证据原文; 溯源链样式存在; UTF-8 且保留中文; 只使用 /api/。
  3. /api/dashboard (14)
     全部 12 个 section 存在; 空状态合法; 未指定 course_id 时选择最小
     course_id (确定性); 显式 course_id; 未知课程 404; 应用标识; 处理后
     反映真实数据; 处理汇总 (by_status/evidence_total/jobs); 知识点投影
     字段精确匹配; 待审列表; gap 归属课程; **两次调用完全一致**; 调用
     不改变状态; 学生与练习计数。
  4. /api/knowledge/{id}/trace (14)
     缺 course_id -> 400; 未知知识点 -> 404; 12 个字段齐全; KP 字段齐全;
     **每条证据都解析到源材料且 material_id 双向一致**; materials 与
     evidence sources 集合相等且 complete=true; 原始文件名保留; 证据
     内容/语言/置信度/类型逐字保留; source_sessions 由证据链推导且等于
     上传时的 session; language 不臆造 (declared=null + 说明); 依赖结构;
     两次调用一致; **断链显式暴露** (伪造 mat-does-not-exist 的证据 ->
     complete=false + unresolved_material_ids + links[].material=null);
     **无证据时不编造证据** (evidence=[] 且 complete=true)。
  5. 溯源链完整性 (4)
     对课程内**每一个**知识点逐一校验溯源链; 材料证据接口与知识点溯源链
     指向同一批材料; UI 用到的审核动作真实可用 (keep-unverified + 历史
     回读, note 一致); 处理动作幂等; 课程页面数据按课程隔离。
  6. 数据保真与安全 (9)
     multipart 上传保留加泰语/西语文件名逐字; 保留中文文件名; X-Filename
     路径不改写文件名; 百分号编码 query 文件名保留 Unicode; **Windows
     非法字符不再导致 500** 且 stored_path 已净化; sanitize_filename 确定性
     单测 (非法字符/尾部空格/保留设备名/空名/路径成分/长度上限); 含 HTML
     的文件名逐字保留 (转义是渲染层职责); API 永不返回 HTML; 错误载荷
     不泄露 traceback; src/web 目录只含 3 个静态文件。
  7. UI <-> API 契约 (4)
     app.js 用到的 10 个路径片段全部被契约清单覆盖 (JS 改了路径而测试没
     跟上会直接失败); 29 个 endpoint 真实可达 (含 Task 40/41 将接入的
     exercises/answers/evaluations/study-plans); 未知课程返回结构化 404;
     所有无参 GET endpoint 都返回 JSON Content-Type。

Test Summary
----------------------------------------

Base (after Task 38): 1871 passed
Task 39 added: 66 passed
Final: 1937 passed, 20 deselected (integration)
Failed: 0

Validation
----------------------------------------

Static assets: PASS (200 / correct Content-Type + charset / no-store / nosniff)
Path safety: PASS (7 traversal payloads, no file outside src/web is reachable)
Offline/self-contained: PASS (no CDN, no build chain, no external deps)
Dashboard: PASS (12 sections, deterministic, read-only)
Traceability: PASS (KP -> Evidence -> Source Material resolves for every KP)
Broken provenance surfaced: PASS (complete=false, unresolved_material_ids)
Determinism: PASS (dashboard & trace byte-identical across calls)
Data fidelity: PASS (Unicode filenames and original text preserved verbatim)
Windows filename safety: PASS (illegal chars no longer 500)
Review actions: PASS (previously 404 for every KP; fixed)
UI/API contract: PASS (29 endpoints reachable)
Compile: PASS (python -m compileall src tests, exit 0)
JS syntax: PASS (node --check src/web/app.js)
No new external dependencies.

Known limitation (explicit, not hidden)
----------------------------------------

本机是 Windows, 而浏览器自动化 (agent-browser) 只支持 macOS / Linux, 因此
本任务**没有**浏览器端到端测试。UI 交互 (点击、hash 路由、表单提交) 由
app.js 实现, 通过"静态断言 + API 契约断言 + 真实 HTTP 静态资源断言"覆盖
其依赖面, 但不声称执行过 DOM 事件。逐像素的视觉验证仍未进行。

## Task 40 — Student Learning UI（学习首页 / 学习路径 / 知识解释）

Goal
----------------------------------------

把 Task 29–33 已经算好的结果**投影**成学生真正能用的页面：学习首页（进度 /
学习计划 / 学习路径 / 待作答练习 / 最近评估 / 学习缺口）、逐知识点的前置学习
路径、以及 Task 29 的 grounded explanation。本任务**不新增任何 mastery 算法**。

设计取舍
----------------------------------------

1. **本层只做投影，不做判定。**
   `src/application/learning_view.py` 的 `LearningViewService` 是纯只读投影
   服务。`progress` 只统计 Task 30 的 `StudentLearningLog` 状态记录与 Task 32
   的评估结果；`learning_paths` 只包装 Task 33 的 `LearningPath`；解释只调用
   Task 29 的 `GroundedKnowledgeBuilder`。测试里有一条断言直接扫 JSON，禁止
   `mastery` / `proficiency` / `estimated_ability` / `predicted` 这类自造字段
   出现在响应里。

2. **导航状态与原始状态必须同时出现。**
   `AVAILABLE / IN_PROGRESS / COMPLETED / BLOCKED` 是**导航**语义，由
   `STATE_TO_PATH_STATUS` 从 Task 30 的四个状态映射而来；`BLOCKED` 额外由
   "前置未走完" 触发。为避免把它误读成掌握度，每个节点同时返回 `state`
   （Task 30 原始状态）与 `status_basis`（人类可读的依据，形如
   `state=reviewing (Task 30 LearningState)` 或 `BLOCKED: unmet prerequisites ...`），
   UI 两者并排显示。

3. **状态推进只走 Task 30 的事件。**
   `POST /api/students/{id}/learning-events` 接收 `viewed / practiced / reviewed`，
   内部调用领域层的 `LearningEventType` + `StudentLearningLog.record_event`。
   越序事件（如从 `not_started` 直接 `reviewed`）**被记录但不推进状态**，与领域
   行为一致。`answered` 是评估事实，永远不推进状态。测试直接对照领域私有常量
   `_TRANSITIONS` / `_STATE_FOR_EVENT` 做漂移守卫，一旦有人改了领域规则而本层
   映射不同步，立刻失败。

4. **`submitted_at` 是运行时元数据，不参与身份。**
   领域层的 `StudentAnswer` 没有时间戳（确定性要求）。spec Task 41 要求保存
   `submitted_at`，因此它由可注入的 `Clock` 提供、**不进入 `answer_id`**，且
   同一份答案重复提交时用 `dict.setdefault` 保留**首次**时间（幂等）。测试断言
   `answer_id` 中不含日期形状的字符串。

5. **`language` 必须在入口校验并归一化。**
   请求语言会写进 `LearningRepresentation.language`，而 `representation_id` 是
   内容寻址的 —— 放任任意字符串等于把用户输入直接写进领域对象。因此新增
   `normalize_language()`：空白 -> 缺省 `es`；形状非法（含路径分隔符、引号、
   超长）-> `INVALID_INPUT`；合法则统一小写，保证 `ES` 与 `es` 落到同一个
   `representation_id`。

6. **UI 语言只作用于界面文案。**
   `I18N` 表提供 zh / es / ca 三语，`applyI18n()` 只翻译带 `data-i18n` 的静态
   节点。原文、证据、知识点陈述**永不翻译**。`No grounded explanation available.`
   是三语共用的固定文案（不随界面语言变化），因为它描述的是"知识库里没有"这个
   事实。

新增 endpoint
----------------------------------------

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/students/{student_id}/dashboard` | 学生首页快照（9 个区块，单次请求） |
| GET | `/api/students/{student_id}/learning-paths/{knowledge_id}` | 单目标前置学习路径 |
| POST | `/api/students/{student_id}/learning-events` | 记录学习事件（`viewed`/`practiced`/`reviewed`） |
| GET | `/api/knowledge/{knowledge_id}/explanation` | Task 29 grounded explanation |

学生首页的 `learning_paths` **内嵌**完整路径对象，因此页面只打一个 dashboard
请求即可渲染路径，避免 N+1；`/learning-paths/{kp}` 供深链与外部调用。

交付文件
----------------------------------------

新增：
- `src/application/learning_view.py` — 只读投影服务 `LearningViewService`
  （学生首页 / 学习路径 / grounded explanation / `normalize_language` /
  `language_code`）
- `tests/test_learning_view.py` — 141 个测试

修改：
- `src/application/learning_service.py` — `Clock` 注入 + `submitted_at`
  运行时元数据、`answer_log_for` / `submitted_at_for`、`NEXT_LEARNING_EVENT`
  与 `next_learning_event`、`record_learning_event`、**`prerequisite_provider`
  注入点**（修复前置图恒为空）
- `src/application/workspace.py` — `CourseContext.learning_view`、
  `student_dashboard` / `student_learning_path` / `grounded_explanation` /
  `record_learning_event`；装配层从 `org.list_relations()` 构建前置图；
  顺手修掉 `KnowledgeService` 被创建两次的疏漏
- `src/api/endpoints.py` — 4 个新端点
- `src/knowledge_learning.py` — `_UNKNOWN_LANGUAGE_CODES`（`Unknown` 不再被
  当成"另一种语言"）
- `src/application/__init__.py` — 导出 `LearningViewService` / `normalize_language`
- `src/web/index.html` — `#/students` 导航、`#ui-language` 语言选择器、
  `data-i18n` 挂点
- `src/web/app.js` — zh/es/ca 三语表、`applyI18n`、`#/students` 路由、
  `pageStudents` / `pageStudent`、`renderPathChain`、`loadExplanation`、
  `actionLearningEvent`
- `src/web/styles.css` — `.path-chain` / `.path-node`

页面清单
----------------------------------------

- `#/students` — 学生列表（按课程）
- `#/courses/{id}/students/{student_id}` — 学生首页：课程 / 学习进度（含
  Task 30 状态分布）/ 当前学习计划 / 学习路径 / 待作答练习 / 最近评估 / 学习缺口
- `#/courses/{id}/knowledge/{kp}` — 知识点详情新增 "知识解释" 面板：
  请求语言选择器 + 解释正文 + 要点 / 示例 / 带证据的断言 + 证据原文（按原样）
  + 相关概念 / 前置；无可用解释时显示 `No grounded explanation available.`

发现并修复的真实缺陷
----------------------------------------

1. **语言可用性判断被显示值污染（严重）。**
   证据 DTO 的 `language` 是 `Language` 枚举的**显示值**（`"Spanish"`），而请求
   语言是 ISO 码（`"es"`）。`grounded_explanation` 直接把显示值塞进
   `language_index`，于是 `"Spanish" in (None, "es")` 为假，**任何语言已被识别
   为西语/加泰语的证据都会被误判为 `language_not_available`** —— 也就是说，
   只有语言检测失败（`Unknown`）的材料才能拿到解释。之前探针没发现，是因为
   mock OCR 产出的语言恰好是 `Unknown`。
   修复：新增 `language_code()`，把显示值 / ISO 码统一归一到小写 ISO 码，未知
   值返回 `None`（= 无语言约束，而不是"不匹配"）。

2. **学习路径的前置链永远是空的（严重）。**
   `LearningService._planner()` 把 `{}` 当作前置图传给 Task 33 的 `StudyPlanner`，
   导致 `build_learning_path` 永远只返回 `[target]` 单节点 —— Task 33 的整个
   前置链能力在应用层**从未被接线**，学习路径功能形同虚设，学习计划也看不到
   未满足的前置。
   修复：`LearningService` 新增 `prerequisite_provider` 注入点，装配层
   （`Workspace._build_context`）从知识组织服务读取**显式 PREREQUISITE 关系**
   （`RELATED` / `CONTRASTS` / `EXTENDS` 永不算前置）构建 `dependent -> (prereqs,)`。
   顺带修掉 `_build_context` 里 `KnowledgeService` 被创建两次、`ctx.knowledge_service`
   与传给 `LearningViewService` 的实例不是同一个的疏漏。

3. **不存在的知识点返回 200 + 空路径。**
   `build_learning_path` 对未注册 KP 返回 `status=unknown_knowledge_point`，接口
   原样 200 返回。这与 `grounded_explanation`（未注册 -> 404）不一致，且客户端
   无法区分"没有前置"与"没有这个知识点"。修复：`learning_path_view` 显式抛
   `NotFoundError`。

4. **学生列表为空时显示错的提示文案。**
   `pageStudents` 复用了 `student.noStates`（"该学生还没有任何学习状态记录"），
   语义不符。修复：新增 `student.none`（三语），文案改为"该课程暂无学生"。

测试分布（141 tests）
----------------------------------------

1. 语言标签校验与归一化 (27)
   缺省回退 / 自定义缺省 / 大小写归一 / 区域与文字子标签 / 三语白名单 /
   12 种畸形标签（含 `../../etc`、`es'--`、超长）/ 5 种非字符串 / 幂等性。
2. 显示值 -> ISO 码归一 (15)
   西/加/中/英显示值映射、大小写、`Unknown`/`UND`/空值 -> `None`（未知不是语言）、
   已是 ISO 码的直通、垃圾输入 -> `None`、以及记录根因的断言
   `Language.SPANISH.value != "es"`。
3. 与 Task 30 领域状态机的一致性（漂移守卫）(12)
   状态集合完全覆盖 / 导航状态取值合法且唯一 / `COMPLETION_STATES` 是真实状态 /
   **逐状态对照领域 `_TRANSITIONS`** / `reviewing` 是终态 / `ANSWERED` 永不是转移 /
   类方法与模块映射一致 / 未知状态无下一事件 / 映射内容精确固定。
4. 学习路径投影 (17)
   fresh -> AVAILABLE / viewed -> IN_PROGRESS / practiced 仍 IN_PROGRESS /
   reviewed -> COMPLETED / position 与 node_ids 顺序 / `status_basis` 点名 Task 30 /
   原始状态与导航状态并存 / **前置未走完 -> BLOCKED 且列出 `unmet_prerequisite_ids`，
   前置走完后解除** / 前置链真的出现在路径里且 root-first / 非 PREREQUISITE 关系
   不算前置 / 环 -> `dependency_cycle` 且不挂死 / 学习计划能看到前置 / 未知学生与
   未知知识点 404 / 缺 course_id 400 / 确定性。
5. Grounded explanation 服务层 (9)
   无证据 -> 恰好 `No grounded explanation available.` 且不生成内容 /
   语言不匹配 -> 不翻译且原文照原样返回 / 匹配语言可用 / `ES` 归一为 `es` /
   未知证据语言不构成不匹配 / 非法语言在读任何数据前就被拒绝 / 空语言用缺省 /
   未知知识点 `NotFoundError` / 空 id `InvalidInputError`。
6. Grounded explanation API (17)
   真实流水线可用 / 三语参数化 / 缺省 es / 证据逐字返回且解释正文 = 已有内容 /
   **切换语言不改变任何证据字节** / `ES` 与 `es` 同一 `representation_id` /
   5 种非法语言 400 / 缺 course_id 400 / 未知 KP 404 / 相关概念与前置字段 /
   确定性。
7. 学习事件 (9)
   完整序列推进 / 越序事件被记录但不推进 / 重复事件幂等 / 大小写不敏感 /
   非法事件类型 400 / 缺 knowledge_id 400 / 缺 event_type 400 / 未知学生 404 /
   **答题不推进状态**。
8. 学生首页 (12)
   9 个区块齐全 / `state_counts` 只来自 Task 30 记录且与已登记 KP 数一致 /
   事件后计数正确 / 未开始 = 课程 KP 减去已接触（并与另两者构成划分）/
   **禁止自造掌握度字段** / 待作答排除已作答 / 最近评估带 `submitted_at` /
   学习计划是 Task 33 投影 / 缺口只复用已有事实 / 确定性 / 未知学生 404 /
   缺 course_id 400。
9. `submitted_at` 与知识库隔离 (9)
   时间来自注入 Clock / 同序重提保留首次时间 / **不同 sequence 是另一份答案** /
   时间不参与身份 / **答错不改变 `validation_status`/`review_status`/`knowledge_score`** /
   答错不新增证据 / 答错不破坏溯源链 / 评估状态与审核状态不同源 /
   评估可回读。
10. UI 契约与 i18n (14)
    app.js 调用 dashboard / learning-events / explanation；路径从 dashboard
    内嵌 payload 渲染；导航状态与原始状态并排显示；固定文案存在；
    界面语言只作用于 `data-i18n`；语言选择器三选项；`#/students` 路由与
    `learning-event` 动作已接线；解释面板挂载点存在；**三语表 key 完全一致**；
    `t()` 用到的 key 全部有定义；`state.*` / `status.*` 覆盖领域全集。

Test Summary
----------------------------------------

Base (after Task 39): 1937 passed
Task 40 added: 141 passed
Final: 2078 passed, 20 deselected (integration)
Failed: 0

Validation
----------------------------------------

Language validation: PASS（12 种畸形标签全部 400，`../../etc` 不再进入领域对象）
Language normalization: PASS（`ES` 与 `es` 同一 representation_id）
Display-value bug: PASS（`"Spanish"` 现在正确映射为 `es`）
Prerequisite wiring: PASS（前置链真的出现在路径中，root-first）
Cycle safety: PASS（环被报告为 `dependency_cycle`，无死循环）
State machine drift guard: PASS（逐状态对照领域 `_TRANSITIONS`）
Read-only projection: PASS（无自造掌握度字段；`progress` 只统计已有事实）
Evaluation isolation: PASS（答错不修改知识库、不新增证据、不破坏溯源链）
`submitted_at` idempotency: PASS（同序重提保留首次时间，不参与身份）
i18n parity: PASS（zh / es / ca 三语表 key 完全一致，无缺失 key）
Original text fidelity: PASS（切换语言不改变证据字节）
Error codes: PASS（400 INVALID_INPUT / 404 NOT_FOUND 语义一致）
Compile: PASS（python -m compileall src tests，exit 0）
JS syntax: PASS（node --check src/web/app.js）
No new external dependencies.

Known limitation（明确说明，不掩饰）
----------------------------------------

1. 本机是 Windows，而浏览器自动化（agent-browser）只支持 macOS / Linux，因此本
   任务**没有**浏览器端到端测试。UI 交互（点击、hash 路由、语言下拉）由 app.js
   实现，通过"静态断言 + API 契约断言"覆盖其依赖面，但**不声称**执行过 DOM 事件。
2. `language_index` 的语言归一依赖 `src/models.py` 的 `Language` 显示值映射；
   若将来新增语言枚举值，需要同步 `learning_view._LANGUAGE_CODES`（已有测试
   盯着西/加/中/英四种，新增值不会被静默接受 —— 会退化为 `None`，即"未知"）。
3. `/learning-paths/{knowledge_id}` 目前只被测试与外部调用使用；学生首页走
   dashboard 内嵌路径。若将来路径数量超过 dashboard 的 `path_limit=5`，需要
   改为逐条拉取。

## Task 41 — Exercise / Answer / Evaluation UI（完整学习闭环）

Goal
----------------------------------------

打通并**在 UI 上真正可见**的完整闭环：

```
Exercise -> Student Answer -> Evaluation -> Learning State -> Study Plan -> Learning Path
```

学生能看到题目 / 题型 / 关联知识点 / 前置 / 出题依据，提交作答，拿到
`score / status / feedback / knowledge_point / evidence`；这条作答又会通过
Task 30–33 已有的规则影响学习状态、学习计划与学习路径。

设计取舍
----------------------------------------

1. **答案键在提交前必须隐藏（本任务最重要的设计决定）。**
   Task 31 的练习 DTO（`/api/exercises`）包含 `correct_choice_id` /
   `expected_answer` / `fill_blank` / `explanation` —— 它是**作者/教师**端点。
   拿它渲染题目等于把答案直接发到浏览器，练习就失去意义。因此新增**学生视角**
   端点 `/api/students/{id}/exercises/{eid}`，它：
   - 提交前**不含**上述四个字段，并显式返回 `answer_key_withheld: true` /
     `answer_key: null`；
   - 提交后答案作为**反馈**出现（`answer_key` + `evaluation.expected`），
     与评估结果一起，而不是混在题目里。
   `explanation`（答案的理由）与答案键同类，因此一并隐藏。
   作者端点保持完整不动 —— 只是 UI 不再使用它（有测试盯着这一点）。

2. **`evaluation` 与 `fact verification` 在数据结构上就分开。**
   评估回答的是"学生这一答对不对"；事实核验回答的是"知识是否成立"。两者
   一旦混在一起，就会出现"学生答错 -> 知识被改"的荒谬后果。因此评估视图
   在**两个层级**都显式声明 `is_fact_verification: false` /
   `affects_knowledge_base: false`，并附一句人类可读的说明；UI 把这句话直接
   渲染在评估面板里。这样即使将来有人误用，也绕不过这个显式标记。

3. **UI 不做任何判分、不做任何掌握度推断。**
   作答一律 `POST /api/answers`（Task 32 的 `ExactEvaluator`），UI 不比对
   `submitted_value` 与答案键；状态推进只能通过 Task 30 的
   `viewed / practiced / reviewed` 事件。测试用 `strip_js_comments()` 去掉注释
   后断言 app.js 里不存在 `submitted_value ===` / `correct_choice_id ===`
   这类前端比对，也禁止出现 mastery 类标识符。

4. **题型决定作答控件，而不是让用户猜。**
   新增 `ANSWER_FORMATS` 映射（`multiple_choice`/`true_false` -> `choice`，
   `short_answer`/`fill_blank` -> `text`），API 返回 `answer_format`，UI 据此
   渲染单选或文本框。有测试断言这个映射覆盖**每一个** `ExerciseType` 成员，
   将来新增题型会立刻失败而不是静默退化成文本框。

5. **断链显式暴露，不静默吞掉。**
   练习引用了解析不到的 `evidence_id` 时，返回
   `evidence_complete: false` + `unresolved_evidence_ids`，UI 顶部弹红色
   横幅。与 Task 39 的溯源链处理一致。

6. **页面只打一个请求。**
   单题视图内嵌 `evaluation`，因此提交后重绘不需要第二次请求；独立的
   `/evaluation` 端点供深链与外部调用（保持 Task 41 要求的字段契约）。

新增 endpoint
----------------------------------------

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/students/{student_id}/exercises` | 练习列表（学生视角，不含答案） |
| GET | `/api/students/{student_id}/exercises/{exercise_id}` | 单题视图（提交前无答案，提交后答案作为反馈） |
| GET | `/api/students/{student_id}/exercises/{exercise_id}/evaluation` | 评估视图（score/status/feedback/knowledge_point/evidence） |

页面清单
----------------------------------------

- `#/exercises` — 练习列表：题目 / 题型 / 关联知识点 / 前置 / 已作答状态
- `#/courses/{id}/exercises/{exercise_id}` — 单题页：
  题目 + 题型 + 难度 + 关联知识点 + 前置 + 出题依据（证据原文）+
  作答控件（单选 / 文本）+ 提交 + 评估面板
- 学生首页的「待作答练习」与「最近评估」现在都可以点进单题页（闭环入口）

关于验证方式（重要，不掩饰限制）
----------------------------------------

本机是 Windows，而浏览器自动化（agent-browser）只支持 macOS / Linux，因此
**没有**浏览器端到端测试。本任务在 Task 39/40 的三层验证之外**新增了第四层**：

`scripts/ui_render_check.js` —— 用 Node + 最小 DOM 桩**真实执行** app.js 的页面
函数。app.js 的页面函数是纯字符串拼接，因此可以在 Node 里跑：注入桩
`document` / `window` / `fetch`（按 URL 路由到固定响应），调用
`pageExercise` / `pageExercises` / `pageStudent`，然后对**渲染出来的 HTML**
做断言。它抓到了静态断言抓不到的东西，并直接证明了最关键的一点：

```
提交前渲染出的 HTML 里, 既没有 accepted_answers, 也没有 explanation。
```

harness 共 32 项检查，覆盖：单题页（提交前 / 提交后）、练习列表、学生首页、
"只打一个请求且不碰作者端点"、以及三语表 key 一致性。它**不是**浏览器测试，
也不声称是 —— 它不解析 CSS、不触发真实事件、不做端到端导航。

交付文件
----------------------------------------

新增：
- `src/application/learning_view.py` 中的 Task 41 部分：
  `exercise_list_view` / `exercise_view` / `exercise_evaluation_view`
  与 helpers（`_evidence_for` / `_latest_answer_for` / `_evaluation_view` /
  `_prerequisites_of` / `_knowledge_point_summaries`）、`ANSWER_FORMATS`、
  `_answer_key()`、`_answer_format()`
- `tests/test_exercise_ui.py` — 82 个测试
- `tests/test_answer_history.py` — 25 个测试（作答历史回归，见缺陷 5）
- `scripts/ui_render_check.js` — Node 渲染 harness（32 项检查）

修改：
- `src/application/workspace.py` — `exercise_list_view` / `exercise_view` /
  `exercise_evaluation_view`
- `src/answer_evaluation.py` — `AnswerEvaluationLog.set_evaluator()`（纯增量）
- `src/application/learning_service.py` — 提交路径不再重建答案日志（缺陷 5）
- `src/api/endpoints.py` — 3 个新端点
- `src/web/index.html` — 导航新增 `#/exercises`
- `src/web/app.js` — `pageExercises` / `pageExercise` / `renderEvaluationPanel` /
  `wireAnswerForm` / `exerciseLink`；`state.studentId`；练习相关 i18n（三语）；
  学生页的待作答与最近评估改为可点链接
- `src/web/styles.css` — `.choices` / `label.choice` / `details > summary`

发现并修复的真实缺陷
----------------------------------------

1. **学生视角会拿到答案（严重，设计缺陷）。**
   Task 31 的 `/api/exercises` 返回 `correct_choice_id` / `expected_answer` /
   `fill_blank` / `explanation`。若 UI 用它渲染题目，浏览器里就能看到答案，
   练习完全失去意义。修复：新增学生视角端点并在提交前隐藏这四个字段
   （`_ANSWER_KEY_FIELDS`），提交后作为反馈返回；测试断言这四个字段在提交前
   不出现在单题视图与列表里，且 UI **从不**调用作者端点。

2. **练习与评估之间没有可显示的关联。**
   Task 32 的评估 DTO 只有 `evaluation_id / answer_id / status / score /
   feedback / evaluator_version`，缺少 spec Task 41 明确要求的
   `knowledge_point` 与 `evidence`。修复：新增评估投影，把
   `exercise.knowledge_point_ids` 与出题证据 join 进来，并显式声明
   `is_fact_verification: false`。

3. **测试 harness 的 Client.post 丢了请求体（本任务自己的测试缺陷）。**
   第一版 `Client.post` 写成了 `self.request("POST", path, **kw)`，漏传
   `body=body`，导致 59 个测试在 fixture 阶段就 400 失败。修复后重跑全绿。
   （记录在案：测试辅助函数的 bug 与产品 bug 一样要如实报告。）

4. **`test_no_mastery_algorithm_in_the_ui_layer` 第一版误报。**
   它直接在 app.js 源码里搜 "mastery"，结果命中的是**注释**里那句
   "前端不实现任何 mastery 算法"。修复：新增 `strip_js_comments()`，静态断言
   只看可执行代码 —— 注释里出现某个词是说明，不是实现。

5. **作答历史被整体抹掉（严重，交付后才发现）。**
   `LearningService.submit_answer` 每次提交都执行
   `self._answer_log = AnswerEvaluationLog(evaluator)` —— **重建**了
   Task 32 设计的追加式日志。实测：学生连续正确作答 3 道题后，
   `answer_log_for()` 只返回 1 条，`learning_status.answered_count` 报 **1**。
   这正是本项目最典型的缺陷类 —— **能力存在，但从未被接线**：Task 32 的
   历史能力被 Task 40 的调用方抹掉了，而"200 + 字段存在"式的断言完全发现
   不了它（`answered_count` 字段一直在，只是值错了）。
   修复：日志只构造一次，提交路径改为刷新 evaluator
   （`self._answer_log.set_evaluator(ExactEvaluator(self._exercises))`）。
   之所以必须刷新而不是复用：`ExactEvaluator` 在构造时**复制**练习目录，
   长期存活的日志若不刷新，"先提交、后新建练习"的顺序会让新练习无法评估。
   因此 `AnswerEvaluationLog` 新增了公开方法 `set_evaluator()`（纯增量，
   不动引擎），其 docstring 明确写出"刷新 evaluator 不得丢历史"。
   修复后实测：3 道题 -> `answer_log_for()` 3 条、`answered_count` 3。

测试分布（82 + 25 tests）
----------------------------------------

1. 答案泄露防护 (7)
   单题视图与列表都不含四个答案键字段 / 显式 `answer_key_withheld` /
   提交后才出现答案键与作者给出的理由 / **UI 从不调用作者端点** /
   作者端点本身保持完整（教师仍能用）。
2. Exercise UI 要求显示的内容 (13)
   题目 / 题型 / 关联知识点 / 前置（真实注册 prerequisite 后可见）/
   出题依据证据（含 source.material_id）/ 证据完整性 /
   **断链显式暴露** / true_false 两个选项 / multiple_choice 全部选项 /
   文本题型的 `answer_format` / `ANSWER_FORMATS` 覆盖每个 `ExerciseType` /
   未知练习与未知学生 404 / 缺 course_id 400。
3. Answer 保存与幂等 (9)
   四个必需字段齐全（`student_id`/`exercise_id`/`submitted_value`/`submitted_at`）/
   同序重提幂等（同 answer_id、同 evaluation_id、同时间戳）/
   重提不产生重复记录 / 作答可从视图读到 /
   换 sequence 是另一份答案且视图展示最新 / **不串到别的学生** /
   未知练习与学生 404 / 三个必需字段各自缺失都 400。
4. Evaluation 显示 + 语义边界 (12)
   spec 五个字段齐全 / **两层都声明 `is_fact_verification: false`** /
   答对 correct / 答错 incorrect / 参考答案作为反馈出现 /
   `evaluator_version` / 提交前评估 404（且消息明确 "no answer submitted"）/
   `short_answer` 逐字判对 / **语义等价（仅大小写不同）必须 unsupported，绝不猜** /
   无参考答案的 short_answer 在创建时就被拒绝 /
   `fill_blank` 用显式 `accepted_answers` / 评估确定性。
5. 答错绝不修改知识库 (8)
   KP 快照逐字段不变 / `review_status` 不变 / 证据集不变 /
   溯源链 `complete` 与 links 不变 / 反复答错不产生新知识点 /
   不产生新的审核候选 / grounded explanation 逐字节不变 /
   评估挂在 answer 而非 KP 上（id 命名空间不同）。
6. 学习状态只走 Task 30–33 (8)
   答题**不推进**状态（仍是 `not_started`，`next_event=viewed`）/
   状态只能由显式事件推进 / 答题确实计入 `activity.answer_count` /
   答错进 `recent_incorrect_kps` 并出现在 `STUDENT_RECENT_INCORRECT` 缺口 /
   学习计划反映该 KP / **路径节点能同时看到练习与评估（闭环）** /
   学生首页反映完整闭环 / **UI 层不存在掌握度算法与前端判分**。
7. 练习列表 (7)
   总数统计自洽 / 已作答标记（含评估状态与分数）/ 确定性排序 /
   含知识点与前置 / 空课程返回空列表 / 未知学生 404 / 确定性。
8. UI 契约 + 渲染执行 + i18n (15)
   调用学生视角端点 / 只读内嵌评估 / 路由与导航已接线 /
   评估面板带显式警示 / 答案键只在提交后渲染 / 题型驱动控件 /
   **UI↔API 字段自动漂移检测**（正则提取 app.js 里 `view.X` / `evaluation.X`
   并与真实响应比对）/ spec 五个评估字段确实被读取 /
   **Node 渲染 harness 通过（32 项检查，含"提交前 HTML 里没有答案"）** /
   harness 自身非空跑 / harness 覆盖三个页面 /
   三语表 key 完全一致 / `t()` 与 `data-i18n` 的 key 全部有定义 /
   练习相关 key 齐全。

9. 作答历史回归 (25) —— `tests/test_answer_history.py`
   历史累积（3 道题 -> 3 条 / `answered_count` 单调递增 1,2,3 / id 互异 /
   顺序稳定）/ 幂等（同值重提同 answer_id、同 submitted_at、不产生重复评估；
   同题不同值才追加）/ 评估历史（早先的评估在后续提交后仍可取、状态逐条正确）/
   evaluator 刷新（后建练习仍可评估、`set_evaluator` 保留历史）/
   学生隔离（两人不串、未知学生 404）/ 闭环不受影响（不写知识库、
   `practice_counts` 与提交次数一致、答错进 `recent_incorrect_kps`）/
   **结构守卫**（源码里 `AnswerEvaluationLog(` 只能出现一次；提交路径必须
   用 `set_evaluator`）—— 只要有人再次重建日志，测试立即失败。

Test Summary
----------------------------------------

Base (after Task 40): 2078 passed
Task 41 added: 82 passed
Answer-history fix added: 25 passed
Final: 2185 passed, 20 deselected (integration)
Failed: 0

Validation
----------------------------------------

Answer withholding: PASS（提交前 HTML 与 JSON 均无答案键；Node harness 实测）
Authoring endpoint untouched by UI: PASS（app.js 不出现 api('/exercises'）
Answer idempotency: PASS（同序重提同 answer_id / evaluation_id / submitted_at）
Answer history retained: PASS（3 道题 -> 3 条历史；answered_count 1,2,3 单调递增）
Answer log never rebuilt: PASS（源码守卫：AnswerEvaluationLog( 仅出现一次）
Required answer fields: PASS（student_id / exercise_id / submitted_value / submitted_at）
Evaluation display fields: PASS（score / status / feedback / knowledge_point / evidence）
evaluation ≠ fact verification: PASS（响应两层显式 false + UI 渲染警示文案）
Wrong answer isolation: PASS（KP 快照 / 证据 / 溯源链 / 解释 全部逐字节不变）
Learning state rules: PASS（答题不推进状态；只能由 Task 30 事件推进）
No mastery algorithm in UI: PASS（去注释后无 mastery 标识符，无前端比对）
Answer format coverage: PASS（ANSWER_FORMATS 覆盖每个 ExerciseType）
Broken evidence surfaced: PASS（evidence_complete=false + unresolved_evidence_ids）
UI↔API field drift: PASS（自动比对 app.js 读取的字段与真实响应）
UI render execution: PASS（node scripts/ui_render_check.js，32/32）
i18n parity: PASS（zh / es / ca 三语表 91 个 key 完全一致）
Compile: PASS（python -m compileall src tests，exit 0）
JS syntax: PASS（node --check src/web/app.js、scripts/ui_render_check.js）
No new external dependencies.

Known limitation（明确说明，不掩饰）
----------------------------------------

1. 本机是 Windows，浏览器自动化只支持 macOS / Linux，因此**没有**浏览器端到端
   测试。本任务新增的 Node 渲染 harness 真实执行了页面函数并对渲染结果断言，
   但它不解析 CSS、不触发真实 DOM 事件、不做端到端导航。逐像素的视觉验证仍未
   进行。
2. harness 用的是**固定形状的响应**（形状由 pytest 的 API 测试保证与真实服务
   一致）。若 API 形状变了而 harness 的 fixture 没跟上，harness 不会失败 ——
   这正是 `TestUiApiFieldContract` 那条自动漂移检测要补的位。
3. `short_answer` 按 spec Task 32.8 只做逐字比较，语义等价一律 `unsupported`。
   这是**有意为之**（"Madrid" vs "马德里" 永不相等），不是缺陷。
4. 单题页目前只展示"最新一次"作答与评估；历史尝试现在确实完整保存在
   answer log 里（缺陷 5 修复后成立，`answer_log_for` 返回全部），但 UI
   尚未提供历史列表（`sequence` 递增会生成不同 answer_id）。
5. 缺陷 5 的修复引入了一个新的不变量：提交路径必须刷新 evaluator。若将来
   有人为了"省一次构造"而复用旧 evaluator，后建的练习会无法评估 ——
   已有 `test_exercise_created_after_history_still_evaluates` 盯着。

## Task 42 — SQLite 本地持久化（数据落库 / 重启不丢 / 幂等）

Goal
----------------------------------------

给整条证据链一个**真正的落盘位置**，并让"重启之后什么都没丢"成为可测的
性质，而不是一句承诺：

```
Material -> Evidence -> KnowledgePoint -> Validation/Review
        -> Student State -> Exercise -> Answer -> Evaluation -> Plan -> Path
```

选择 SQLite 的理由与规范一致：本地、单用户、零独立数据库服务、Windows
友好、可备份、稳定、够用。**没有**引入任何新依赖（`sqlite3` 是标准库）。

设计取舍
----------------------------------------

1. **"规范列 + 权威 payload"——让"不丢字段"成为结构性质（本任务最重要的设计决定）。**
   每张表既有类型化的列（身份、外键、要查询/排序的字段），又有一个
   `payload` 列，内容是**领域对象自己的 `to_dict()`**，经规范化 JSON 编码。
   - 仓储**不允许**手写字段映射，因此不可能漏掉某个字段：溯源
     (`source_reference`)、开放的 `metadata` 字典、以及将来新增的字段都
     自动跟随。
   - 外键 / UNIQUE / 索引仍然齐全，因此关系由数据库强制，而不是靠约定。
   - 每行带 `payload_version`，为将来的形状演进留出判据。
   测试用 `to_dict()` **深度相等**来验收"无损"——这是唯一不依赖"我记得哪些
   字段"的判据。

2. **`sqlite3` 只出现在 `src/persistence/` 内。**
   既有回归明确禁止 `src.evidence_store` 与 `src.knowledge_pipeline` 出现
   `sqlite3`（提取/分析层必须保持纯函数）。把持久化放进独立包后，那两条约束
   继续成立：存储是纯函数层**旁边**的一层，不是它里面的一部分。方向是单向的
   ——`persistence` 依赖领域模型，领域模型完全不知道数据库存在。

3. **领域层是唯一的读写入口，存储层不重写规则。**
   - 读回对象一律走领域层自己的 `from_dict`
     （`EvidenceStore.from_dict` 拒绝损坏快照；`KnowledgeStructure.from_dict`
     拒绝悬空引用/重复实体/自环；`CourseKnowledgeStructure.from_dict` 还会
     重算并交叉校验每个 id）。
   - 学生的状态记录由 `log.derive_state()` 派生 —— 是**领域层自己的归约**，
     不是存储层重新算一遍。因此"事件说 3 次对、状态说 3 次对"是结构性保证。
   - `OrganizationRepository.load_structure` 只组装 payload 然后交给
     `CourseKnowledgeStructure.from_dict`，**不调用任何私有方法**。
   结论："从数据库读回来"和"从文件读回来"受到的检查完全一样，存储层不可能
   比文件层更宽松。

4. **原子性写在需要它的地方，并用真实的失败注入证明。**
   规范点名"材料注册 + 处理状态不得半写"。做法是把它拆成
   `materials` + `material_processing` **两张表**，这样"两表必须一起成功"
   才是一个真命题，可以被注入的异常验证（而不是靠单表事务白拿）。跨仓储的
   回滚、嵌套事务、延迟外键同样有失败注入测试。

5. **确定性不因落盘而破功。**
   - 迁移台账的时间戳复用 `src.application.runtime` 的可注入 `Clock`
     ——"全项目只有一处非确定性来源"这条规则在存储层继续成立。
   - 证据的插入顺序**显式落列**（`insertion_seq`）：Task 23 的查询依赖插入
     顺序，而 SQLite 不保证无 `ORDER BY` 的行序。
   - 所有 `order_by` 都包含主键，保证**全序**，因此并列行的顺序不会漂移。

6. **不发明领域没有的身份。**
   `learning_paths` 用自然键 `(course_id, student_id, target_knowledge_point_id)`
   做主键，**没有**造一个 `path_id`——因为 `LearningPath` 本来就没有 id。
   同理没有给 `LearningEvent` 加 `is_correct`、没有给 `ReviewRecord` 加
   `status`。schema 跟着领域模型走，不反过来。

Schema 概览
----------------------------------------

迁移链：`001_initial_schema`（全部业务表）+ `002_knowledge_organization`
（`database_meta` 表、`material_processing.last_attempt_at` 列、一个索引）
—— 第二条是**纯增量**的，用来证明升级机制真的能保留已有数据，而不是靠
"重建库"蒙过去。

28 张表 / 21 个 payload 表 + 5 个有序关系表：

| 层 | 表 |
| --- | --- |
| 课程 / 课堂 | `courses`, `sessions` |
| 材料 | `materials`, `material_processing`, `material_evidence` |
| 证据 | `evidence`（`canonical_key` UNIQUE）, `material_evidence` |
| 知识 | `knowledge_points`, `knowledge_point_evidence`, `relationships`, `conflicts`, `conflict_evidence` |
| 审核 | `review_records` |
| 组织 | `topics`, `knowledge_memberships`, `session_memberships`, `knowledge_relations` |
| 学生 | `students`, `learning_events`, `student_knowledge_state` |
| 练习 / 作答 / 评估 | `exercises`, `exercise_knowledge_points`, `exercise_evidence`, `student_answers`, `evaluation_results` |
| 规划 | `study_plans`, `learning_paths` |

新增模块
----------------------------------------

```
src/persistence/
    errors.py            结构化错误（命名即映射，见下）
    database.py          连接 / 事务 / 迁移执行器 / 健康检查
    migrations/          编号迁移链 001–002 + 链校验
    models/codec.py      规范化 JSON 编解码 + 标量转换
    models/tables.py     表规格注册表（21 payload 表 + 5 关系表）
    repositories/        12 个仓储模块 + Repositories 容器
    snapshot.py          跨聚合整体往返（只做编排，不发 SQL）
```

错误码**靠命名映射**，不需要胶水层：`INVALID_*` → `INVALID_INPUT`、
`*_NOT_FOUND` → `NOT_FOUND`、`DUPLICATE_*` → `CONFLICT`、
`STORAGE_*` → `STORAGE_ERROR`。因此新增一个存储错误时，只要按这套前缀命名，
HTTP 状态码就自动正确。

事务实现要点
----------------------------------------

- 每线程一条连接（`check_same_thread=False`），进程级写锁（`RLock`），
  `BEGIN IMMEDIATE` 最外层 + 嵌套 `SAVEPOINT sp_N`。
- `PRAGMA busy_timeout / foreign_keys=ON / journal_mode=WAL / synchronous=NORMAL`
  （内存库不开 WAL）。
- **失败的 COMMIT 会留下一个隐藏的开放事务**（SQLite 的行为）——因此
  `commit()` 失败后显式 `_abandon_transaction()` 回滚，否则后续写入会被
  静默吞掉。
- `_verify_readable()` 探测非空文件，垃圾/截断文件报
  `CorruptedDatabaseError`，而不是等到某次查询才炸。

测试
----------------------------------------

```
381 persistence tests   (规范要求 >= 70)
```

| 文件 | 数量 | 覆盖 |
| --- | --- | --- |
| `tests/test_persistence_database.py` | 52 | 打开/创建、损坏文件、PRAGMA/健康、SQL、事务与三层嵌套、线程、内存库、迁移台账 |
| `tests/test_persistence_migration.py` | 35 | 链定义、台账、增量 001→002 保数据、失败回滚、版本校验、**schema↔规格漂移守卫** |
| `tests/test_persistence_serialization.py` | 44 | 规范化 JSON、编解码、校验和、标量转换、逐字节一致 |
| `tests/test_persistence_repositories.py` | 135 | 每个仓储的 CRUD/排序/幂等/去重/关系链 |
| `tests/test_persistence_transaction.py` | 31 | 材料+状态原子对、跨仓储回滚、嵌套、延迟外键、约束错误翻译 |
| `tests/test_persistence_roundtrip.py` | 62 | **restart / corrupted DB / concurrent read / 整体无损往返** |
| `tests/test_persistence_layering.py` | 22 | **分层守卫**（见下） |

规范点名的十项覆盖对照：

| 要求 | 位置 |
| --- | --- |
| CRUD | repositories (135) |
| transaction | database / transaction |
| rollback | database / transaction |
| **restart** | roundtrip（含"两次重启逐字节相同"） |
| migration | migration |
| serialization | serialization |
| duplicate | repositories / transaction |
| idempotency | repositories / roundtrip（"读回来再存一遍行数不变"） |
| **concurrent read** | database / roundtrip（读不被写阻塞、读只见已提交快照） |
| **corrupted DB handling** | database / roundtrip（文件级 + payload 级 + 隔离性） |

Test Summary
----------------------------------------

Base (after Task 41): 2185 passed, 20 deselected
Task 42 added: 381 passed
Final: 2566 passed, 20 deselected (integration)
Failed: 0

Validation
----------------------------------------

Schema migrated: PASS（001 + 002，`schema_version = 2`，二次 migrate 应用 0 条）
Idempotent migrations: PASS（重开不重复应用，台账不重复）
No information loss: PASS（证据/知识/组织/学生/练习/作答/评估/计划/路径 全链路 `to_dict()` 深度相等）
Evidence insertion order: PASS（显式 `insertion_seq`；重启后逐位相同）
Provenance chain order: PASS（`knowledge_point_evidence` 覆盖 payload 顺序，逐位相同）
Review history retained: PASS（含顺序；`latest_for` 与领域层规则一致）
Review history course isolation: PASS（不跨课程泄漏）
Conflict records retained: PASS（含证据链与状态查询）
Student state counters: PASS（计数落库；且"作答不推进状态机"这条领域规则原样保留）
Exercise / answer / evaluation retained: PASS（含内容寻址 `evaluation_id` 与 `submitted_at`）
Study plan / learning path retained: PASS（计划 id 内容寻址；路径节点顺序保持）
Material + status atomicity: PASS（失败注入后两表都不留痕）
Cross-repository rollback: PASS（多仓储一起写，异常时全部丢弃）
Constraint translation: PASS（UNIQUE/PK → `DuplicateRecordError`；NOT NULL/FK/CHECK → `PersistenceValidationError`）
Failed COMMIT leaves no hidden transaction: PASS
Restart durability: PASS（schema / 行 / 顺序 / 状态 / 关系 / 两次重启逐字节一致）
Uncommitted work discarded on close: PASS（模拟崩溃不留半写数据）
Corrupted database file rejected: PASS（垃圾 / 截断 / 结构化错误码）
Corrupted payload reported: PASS（非 JSON / NULL / 非对象 一律报错，绝不静默返回 `{}`）
Corruption isolation: PASS（坏行不影响好行；行级损坏不误判为文件级不可用）
Concurrent read: PASS（8 线程读同一份数据；读不被写阻塞；只读到已提交快照）
Open while a writer holds a transaction: PASS（最新版库打开不抢写锁）
Course isolation: PASS（知识组织 / 评审历史 / 学生日志 均不跨课程混合）
Unknown student: PASS（返回 None，不抛错也不造空对象）
Layering — sqlite3 只在 persistence 内: PASS（`ast` 解析真实 import；并用反向断言防止守卫空跑）
Layering — 领域/应用/web 层不依赖 persistence: PASS（依赖方向单向）
Layering — 无外部数据库驱动: PASS（禁 psycopg/mysql/redis/mongo/sqlalchemy）
Layering — 规范点名的包结构存在: PASS（database.py / repositories / migrations / models）
Compile: PASS（`python -m compileall src/persistence tests`，exit 0）
No new external dependencies.

过程中发现并修复的真实缺陷
----------------------------------------

1. **`migrate()` 在库已是最新版本时仍然抢写锁（真实产品缺陷）。**
   `_ensure_ledger()` 无条件执行 `CREATE TABLE IF NOT EXISTS` —— 逻辑上是空
   操作，在 SQLite 里仍是一条**写语句**，会申请写锁。后果：任何正在进行的
   长事务（导入、批量落库）都会让"打开数据库"卡满 `busy_timeout` 然后失败。
   本项目是浏览器轮询的本地服务，"打开库 = 可能等 5 秒然后失败" 不可接受。
   修复：先**只读地**用 `has_table()` 判断台账在不在，不在才去写。现在
   `migrate()` 在最新版库上完全不写盘 —— 有测试断言"写事务开着时第二个连接
   也能打开"。

2. **知识组织层的 `session_memberships` 跨课程泄漏（真实数据缺陷）。**
   `SessionKnowledgeMembership` 只有 `session_id` + `knowledge_point_id`，
   **没有** `course_id`，而 `load_structure(course_id)` 原来无条件
   `load_all()`。后果：加载 course-1 会把 course-2 的课堂归属一起端出来。
   领域层的 `from_dict` 查不出来（它手里只有一份 payload）。修复：由
   `sessions` 表回答"哪些 session 属于这门课"，先过滤再交给领域层。
   这直接违反项目铁律"不混合不同课程的知识点"，因此按缺陷处理。

3. **评审历史跨课程泄漏（同一类缺陷）。**
   `load_knowledge_structure(course_id)` 原来追加**全部** `review_records`，
   而 `review_records` 没有 `course_id` 列。修复：新增
   `ReviewRepository.load_for_knowledge_points()`，只取本结构里那些知识点的
   评审记录。

4. **失败的 COMMIT 会留下隐藏的开放事务。**
   SQLite 在 COMMIT 失败后事务仍然开着，后续写入被静默吞进那个事务，直到
   某次莫名其妙的丢失。修复：`commit()` 失败即 `_abandon_transaction()` 回滚。

我自己写错并改正的测试前提（记录在案，因为它们暴露了领域的真实严格度）
----------------------------------------

| 我原本的假设 | 领域的真实行为 |
| --- | --- |
| 外键违约要等到 `foreign_key_check` 才发现 | 插入时**立即**被拒（`sqlite3.IntegrityError`） |
| `Exercise.create` 保持传入的 kp 顺序 | 会**排序并去重** |
| `ConflictRecord` 可以传空 id | `__post_init__` 自动补 uuid4 |
| `EvaluationResult` / `Topic` 的 id 可以手写 | **内容寻址**，`from_dict` 重算并拒绝不一致 |
| `StudyItem` 有 `reason` / `priority` | 实际是 `reason_codes` / `prerequisite_ids` |
| `StudyPlan(plan_id="")` 就是合法计划 | id 由内容派生，必须让领域层算出来 |
| 答对 3 次状态就是 `practicing` | `ANSWERED` **不是**迁移事件，状态仍是 `not_started` |
| 约束错误翻译发生在 `Database` 层 | 翻译是**仓储层**职责；裸 SQL 保持原生异常 |
| `migrate()` 返回已应用迁移的列表 | 返回条数（`int`） |

最后两条不是缺陷而是分层结论：**翻译只发生在仓储层**，因此绕过仓储直接执行
SQL 时拿到的是原生 `sqlite3.IntegrityError` —— 这一条被写成测试固定下来。

Known limitation（明确说明，不掩饰）
----------------------------------------

1. **证据 / 冲突无法按课程过滤。** `evidence` 是内容寻址的，`Evidence` 与
   `ConflictRecord` 都没有 `course_id`，因此 `evidence`、`conflicts` 两张表
   也没有。这是刻意的（同一段证据可以被多门课引用），但后果是：全局查询
   `load_all()` 会返回所有课程的证据与冲突，按课程过滤只能在应用层靠
   `material_id` / 知识点归属间接推导。知识点、评审、学生状态、组织归属这些
   **有**课程归属的实体已经全部做了隔离并有测试。
2. **没有并发写测试。** 规范只要求 `concurrent read`。写路径有进程级
   `RLock` + `BEGIN IMMEDIATE` + `busy_timeout` 的保护，但"多进程同时写"的
   行为没有被测试覆盖，也没有做跨进程锁。本项目是单机单用户，这符合规范
   前提，但不等于已被验证。
3. **没有做真实的大数据量性能测试。** 落库路径是逐条 upsert，没有批量优化。
   本阶段的验收是正确性与不丢数据，性能留到 Task 47 的硬化阶段评估。
4. **`payload` 与规范列之间没有一致性约束。** 列是从 payload 派生出来的，
   写入时由仓储保证一致；但如果有人手工 `UPDATE` 改了 payload 而没改列，
   查询字段就会与内容不符。这是"权威 payload + 便于查询的列"这一设计的
   固有代价，权衡后接受（测试里有 `payload_checksum` 可以用来发现改动）。
5. **领域层 `RelationType.PRE_REQUSITE_OF` 的成员名拼错了**（值为
   `'prerequisite_of'`，正确）。属于既有的公开枚举，改名会波及 Task 36–40 的
   代码与测试，因此本任务**不改**，只在此记录。存储层用的是 `.value`，
   落库内容正确。

## Task 43 — Backup / Recovery / Data Migration（备份 / 恢复 / 版本迁移）

Goal
----------------------------------------

让"用户的数据可以丢一次, 但不可以丢两次"成为可测的性质。规范点名的四个
操作全部落地, 并严格按

```
validate -> restore to temporary location -> verify -> atomic replace
```

实现恢复, 使"如果 restore 失败, 原数据库不能被破坏"这句话有**真实异常注入**
的测试撑着, 而不是一句承诺。

备份是**独立的一层** (`src/backup/`), 不是持久化层的一部分:

```
src/backup  ->  src/persistence         一致性快照 / schema 版本 / 迁移链
src/backup  ->  src/application         目录布局 / 可注入时钟
```

`src/persistence` 与领域层**不知道**备份层的存在。

设计取舍
----------------------------------------

1. **为什么不能"直接把数据库文件复制一份"（本任务最重要的决定）。**
   本库跑在 **WAL** 模式。WAL 下已提交的数据可能还躺在 `-wal` 边车文件里,
   主库文件本身是**过期的** —— 直接 `shutil.copy` 会静默备份出一份**丢数据**
   的库, 而且看起来一切正常 (能打开、能查询、只是少了最近的东西)。
   做法: 新增 `Database.backup_to()`, 用 SQLite 的**在线备份 API**
   (`sqlite3.Connection.backup`) 生成一致性快照。因此备份层必须依赖
   `src.persistence` —— 在备份层重写这段逻辑等于把 WAL 的知识复制一份。

2. **归档格式: 保留应用自己的四棵树, 不引入路径翻译表。**
   规范的建议格式里列了 `materials/`。但本应用里 `materials/` 是**材料注册
   索引** (每门课一份 JSON), 材料本体在 `audio/` `images/` `documents/`。
   把两者混进一个目录会让"哪份文件是什么"变得不可判定。因此归档保留
   `materials/ audio/ images/ documents/` 四个目录名, 恢复就是**纯逆操作**
   —— 不需要一张"归档路径 → 目标路径"的翻译表, 而翻译表是 bug 的温床。
   `database.sqlite` 放在归档根目录 (**照规范执行**), 让归档自描述。
   清单里记录 `config.backed_up_dirs`, 所以"该替换哪些树"由归档自己回答。

3. **清单不把自己算进去。**
   `file_count` 与 `total_bytes` 都**不含 `manifest.json`**。清单里写着这两个
   数字, 如果它们又取决于"清单本身多大", 就形成了自指关系。两者的定义因此
   统一为"数据库 + 全部材料文件"。`build_manifest()` 自己算出这两个数字,
   **不接受调用方传入** —— 于是"数字与实际内容不一致"在构造期就不可能发生。

4. **`validate_archive` 的契约是"报告", 不是"抛异常"。**
   归档是**不可信输入**, 用户会从别处拷来、会随手挑一个 zip。所以
   "这份归档哪里有问题"必须能被逐条读出来 (`ok=False` + `errors`), 而不是
   变成一次异常。只有"调用方参数本身非法"(空路径 / 文件不存在) 才抛异常
   —— 那不是归档的问题, 是调用的问题。`restore_backup` 自己先校验, 不合格
   就抛 `RestoreError` 并把原因带在 `detail["errors"]` 里。

5. **恢复的"挪"用 `os.replace` / `os.rename`, 不用复制。**
   现有数据先挪到 `temp/restore-*/previous/`, 新数据再挪进来; 任何一步失败
   就把 `previous/` 挪回去。**回滚路径上绝不用复制** —— 错误路径上再出一次
   "复制到一半失败"会让原数据也坏掉, 那就彻底违背了规范那条硬要求。
   同卷 rename 是原子的, 也不需要搬动几百 MB 的音频。

6. **必须先清掉 `-wal` / `-shm` 再替换主库。**
   WAL 模式下这两个文件里可能存着旧库已提交的数据。只替换主库文件会让
   SQLite 把**旧 WAL 重放到新库上** —— 那是最坏的一类数据损坏: 静默混合
   两份数据, 事后无法分辨哪些行来自哪一份。

7. **"归档声明的空树"要还原成空树, "归档没声明的树"要原样不动。**
   两种情形的正确行为不同, 所以 `_restore_plan()` 除了返回"替换哪些树",
   还返回"这个列表是否来自归档自己的声明":
   - 声明了 (`config.backed_up_dirs` 存在): 忠实还原。某棵树在归档里是空的,
     恢复后就该是空的 —— 否则"恢复"变成了"恢复 + 保留后来的新增", 不可预期。
   - 没声明 (老归档 / 手工做的归档): 只替换它**确实含有文件**的树, 其余
     不动并给出 warning。信息不足时选择不破坏。

8. **确定性: "同一份数据 + 同一时钟 = 逐字节相同的归档"。**
   - 所有条目的 `date_time` 都取自清单的 `created_at` (来自注入的 `Clock`),
     而不是文件系统的 mtime;
   - 条目顺序固定 (清单 → 数据库 → 材料, 材料按路径排序);
   - 清单是规范化 JSON (键排序 + 紧凑分隔符 + 不转义非 ASCII), 与持久化层
     同一套规则。
   这条性质有一个漂亮的验收测试: **恢复之后再备份一次, 归档逐字节相同** ——
   这是"备份与恢复互为逆操作"的最强证据。

9. **`schema_version` 是一道闸门, 不只是元数据。**
   来自**更新版本**的备份必须在碰任何现有数据之前被拒绝。以前只在"需要升版"
   时检查版本 (`< latest_version()`), 于是"未来版本写的备份"会**静默恢复
   成功**: 用户以为数据回来了, 实际上库里是一份本代码不认识的 schema, 而原
   数据已经被替换掉。现在清单与库内迁移台账**两处**都检查 (清单可以被改,
   它就是个 JSON 文件; 真正的判据是库里的台账)。

新增模块
----------------------------------------

```
src/backup/
    errors.py     13 个结构化错误码 (命名即映射到 8 个用户可见错误码)
    manifest.py   manifest.json 的定义 / 构造 / 严格校验 / 规范化序列化
    archive.py    zip 读写 + 路径穿越 / 符号链接 / 重复条目 / zip 炸弹防护
    service.py    四个操作 + 原子替换 + 回滚 + 版本闸门
```

归档内容:

```
backup-YYYYMMDDTHHMMSSZ[-label].zip
    manifest.json       清单 (6 个必需字段 + 增强字段)
    database.sqlite     SQLite 一致性快照 (在线备份 API 生成)
    materials/          材料注册索引
    audio/ images/ documents/   材料本体
```

**不备份**: `logs/` `temp/` `backups/` (运行日志 / 临时暂存 / 备份目录自身
——备份它们只会让归档膨胀并引入自引用)。材料树里的 `__pycache__` 也跳过。

清单字段:

| 字段 | 来源 | 作用 |
| --- | --- | --- |
| `backup_version` | 常量 `BACKUP_VERSION` | 归档格式版本; 更高即拒绝, 绝不猜着读 |
| `schema_version` | 活库的迁移台账 | 恢复时决定"要不要迁移"与"能不能迁移" |
| `created_at` | 注入的 `Clock` | 归档名与所有条目时间戳的唯一来源 |
| `application_version` | `src.application.workspace` (懒加载) | 便于事后判断"哪版应用写的" |
| `file_count` | `build_manifest` 算出 | 数据库 + 材料; 不含清单自己 |
| `database_checksum` | 快照的 sha256 | "归档没被改过"的**唯一硬证据** |
| `database_size` / `total_bytes` | 同上 | 恢复前估算所需空间 |
| `material_files[]` | 逐个文件 | 每份材料的 path / size / sha256 |
| `config` | `reconstruction_config()` | 重建所需的结构信息, **不含绝对路径** |
| `label` | 调用方 (白名单字符) | 人工识别用 |

威胁模型 (归档是不可信输入)
----------------------------------------

| 威胁 | 防护 |
| --- | --- |
| 路径穿越 (`../etc/passwd`, `C:\Windows\...`) | 条目名逐条校验 + 解压时 `safe_join` **二次防线** |
| 符号链接条目 (名字完全正常) | 检查 Unix mode 位 `stat.S_ISLNK` |
| 重复条目名 (校验读到一个、解压写入另一个) | 一律拒绝 |
| zip 炸弹 | **三重**上限: 条目数 / 解压后总字节 / 单条目压缩比 |
| CRC 损坏 (传输中坏掉) | `testzip()` 逐条目校验 |
| 归档在"校验"与"落盘"之间被换掉 | 落盘后再逐个复核校验和, 对不上就拒绝恢复 |

测试
----------------------------------------

```
384 backup tests   (规范要求 >= 40)
```

| 文件 | 数量 | 覆盖 |
| --- | --- | --- |
| `tests/test_backup_manifest.py` | 129 | 6 个必需字段、类型校验、版本拒绝、`file_count`/`total_bytes` 派生、规范化 JSON、自洽校验、**13 个错误码 → 用户可见错误码映射** |
| `tests/test_backup_archive.py` | 81 | 条目名安全、目录条目、重复条目、符号链接、三重 zip 炸弹上限、CRC 损坏、`extract_to` 的二次防线 |
| `tests/test_backup_service.py` | 125 | 构造校验、重建配置、create/list/validate/delete/prune、**每一类损坏各构造一份真实归档**、模块级便捷函数、分层守卫 |
| `tests/test_backup_recovery.py` | 49 | 往返、灾难恢复、**失败注入回滚**、Windows 文件占用、WAL 边车、**old schema → current schema**、未来版本拒绝、端到端无损 |

规范点名要求对照:

| 要求 | 位置 |
| --- | --- |
| zip integrity | archive (`testzip` / CRC 损坏条目) |
| manifest | manifest (129) + service (缺字段 / 非 JSON / 版本更高) |
| database checksum | service (篡改校验和 → `ok=False`) |
| expected paths | service (声明文件缺失 / 未声明条目) |
| no path traversal | archive (`../` / 绝对路径 / 盘符 / 反斜杠 / 控制字符 / 归一化不一致) |
| validate → temp → verify → atomic replace | recovery (无 `temp/restore-*` 残留 + 逐字节不变) |
| 原数据库不能被破坏 | recovery (3 种真实异常注入) |
| old schema → current schema | recovery (schema 1 归档 → `schema_version = 2`) |
| backup / restore / list / validate | service |

Test Summary
----------------------------------------

```
Base (after Task 42): 2566 passed, 20 deselected
Task 43 added: 384 backup tests + 2 layering guards = 386 passed
Final: 2952 passed, 20 deselected (integration)
Failed: 0
```

Validation
----------------------------------------

```
Zip integrity: PASS（testzip() 逐条目 CRC；损坏条目报 STORAGE_BACKUP_CORRUPTED）
Manifest required fields: PASS（6 个缺一即拒，且一次列全；bool 不能被当成 int）
Manifest type validation: PASS（版本/时间/计数/校验和/材料条目逐字段）
Backup version gate: PASS（更高版本 → INVALID_BACKUP_VERSION，绝不猜着读）
Database checksum: PASS（归档内快照 sha256 与清单逐字节比对；篡改即报）
Material checksums: PASS（每份材料逐个记录并复核 sha256 + size）
Expected paths: PASS（声明文件必须在；未声明条目即报 unexpected entry）
No path traversal: PASS（6 类逃逸名 + 解压期 safe_join 二次防线）
Symlink entries: PASS（按 Unix mode 位识别，正常名字也拦得住）
Duplicate entries: PASS（校验与解压可能看到不同数据 → 拒绝）
Zip bomb: PASS（条目数 / 解压总字节 / 单条目压缩比 三重上限）
Validate reports instead of raising: PASS（普通 zip / 穿越 / 符号链接 / 重复条目 / 炸弹 一律 ok=False）
Restore atomicity: PASS（validate -> temp -> verify -> atomic replace；无 temp/restore-* 残留）
Restore failure keeps original data: PASS（验证阶段 / 材料替换阶段 / 目录被占用 三种注入，活库字节摘要与材料逐文件摘要均不变）
Rollback covers every material tree: PASS（第一棵树已挪走后失败 → 全部挪回）
WAL sidecars cleared before replace: PASS（陈旧 -wal/-shm 被清除；恢复后 is_healthy()）
Windows file lock: PASS（活库仍被打开时 os.replace 失败 → STORAGE_DATABASE_IN_USE + 可执行提示；关掉句柄即成功）
Old schema -> current schema: PASS（schema 1 归档恢复后 schema_version = 2，migrated_from=1）
Migrate=False keeps the old schema: PASS（migrated_from/to 均为 None，库仍是 1）
Newer schema refused: PASS（清单声称更高版本 → 在碰任何数据前拒绝；库内台账更高 → _verify_staged_database 拒绝）
Idempotency: PASS（同一时钟 + 同一数据 → 同名同字节；重复恢复结果不变）
Determinism: PASS（条目顺序固定；所有 date_time 取自注入时钟；清单为规范化 JSON）
Backup -> restore -> backup byte identity: PASS（恢复后再备份，归档逐字节相同）
Disaster recovery into a fresh data_dir: PASS（换目录 / 从别处拷来的归档）
Lossless full cycle: PASS（材料逐文件摘要回到备份时；课程内容一致）
Error code mapping: PASS（13 个备份错误码全部映射到 8 个用户可见错误码之一；无 INVALID_*_NOT_FOUND 歧义名）
Layering — sqlite3 不在 backup 层: PASS（ast 解析真实 import；反向断言防空跑）
Layering — persistence 消费者白名单: PASS（全等断言：多一个包失败，少一个包也失败）
No new external dependencies: PASS（zipfile / shutil / hashlib 均为标准库）
Compile: PASS（python -m compileall src/backup src/persistence/database.py tests/test_backup_*.py，exit 0）
```

发现并修复的真实缺陷
----------------------------------------

1. **`validate_archive` 遇到恶意 / 无关归档时抛异常, 而不是报告（真实契约违背）。**
   契约 (写在函数 docstring 里) 是"把问题逐条列出来, 不抛异常"。但捕获列表
   只写了 `(CorruptedBackupError, UnsupportedBackupVersionError)`, 而
   `open_archive` 实际会抛 `UnsafeArchivePathError` (路径穿越 / 符号链接 /
   重复条目) 与 `ArchiveTooLargeError`; `read_manifest_bytes` 在缺
   `manifest.json` 时抛 `MissingArchiveEntryError` —— 这些都**不是**
   `CorruptedBackupError` 的子类, 于是全部穿透出去。
   后果: 用户随手挑一个 zip 当备份, 得到的是一个异常而不是"这份归档不是
   备份"; 更糟的是**最该被报告**的情形 (路径穿越、zip 炸弹) 走了异常路径。
   修复: 两处捕获都改为基类 `BackupError`, 并写清楚为什么必须用基类。

2. **`DatabaseInUseError` 被包成泛泛的 `RestoreError`（精确错误码被降级）。**
   `restore_backup` 的错误路径原来是
   `if isinstance(exc, (RestoreError, UnsupportedBackupVersionError)): raise`。
   `DatabaseInUseError` 是 `RestoreError` 的**兄弟**而不是子类, 于是它被
   重新包成 `STORAGE_RESTORE_FAILED`, 而那条真正可执行的提示
   ("先把应用关掉, 所有数据库连接都要关") 只存在于**嵌套的消息字符串**里。
   修复: 改为 `isinstance(exc, BackupError)` —— 备份层所有错误都带稳定 code,
   没有再包一层的理由。有测试断言"错误码不被降级、消息里不出现 rolled back"。

3. **来自更新 schema 的备份会被静默恢复（真实数据风险）。**
   版本检查原来是 `if migrate and manifest.schema_version < latest_version()`,
   只在**需要升版**时才检查。于是 `schema_version = 99` 的归档会**恢复成功**
   (`migrated_from=None`), 静默把现有数据换成一份本代码不认识的 schema。
   修复: 在 `restore_backup` 拿到清单后、**创建任何临时目录之前**加一道版本
   闸门。有测试断言"拒绝且一个字节都没动"。

4. **`_verify_staged_database` 只看健康、不看版本（清单可以被改）。**
   清单是个 JSON 文件, 谁都能改。真正的判据是**库内迁移台账的最大版本号**。
   修复: `_verify_staged_database` 同时检查 `probe.schema_version() >
   latest_version()`, 把"清单撒谎"这条路也堵上。

5. **`_replace_database` 没清 `-wal` / `-shm` 就替换主库。**
   WAL 下这两个文件里可能存着旧库已提交的数据, 只换主库会让 SQLite 把旧 WAL
   重放到新库上 —— 静默混合两份数据, 事后无法分辨。修复: 替换前先清边车。

6. **`_rollback` 用 `shutil.copy2` 恢复数据库（错误路径上自己制造风险）。**
   回滚本身就在错误路径上, 再出一次"复制到一半失败"会让原数据也坏掉 ——
   那正好违背规范那条硬要求。修复: 改用同卷 `os.replace` / `os.rename`
   (原子、不搬大文件), 复制只作为最后的兜底。

7. **"归档声明的空树"被当成"归档没描述这棵树"（语义错误）。**
   原来对没有文件的树统一给 warning 并跳过, 于是"备份时 `images/` 是空的"
   这种正常情况恢复后反而**保留了目标目录里的旧图片** —— 那不是恢复到那个
   时间点。修复: `_restore_plan()` 额外返回"这个列表是否来自归档自己的声明",
   声明了就忠实还原成空目录, 没声明才 warning + 不动。

8. **Task 42 的分层守卫范围过宽（守卫本身的问题, 但它拦住了正确的架构）。**
   `test_no_domain_module_imports_the_persistence_package` 扫的是"除
   `src/persistence/` 以外的所有 `src/`", 于是备份层依赖持久化层被判为违规。
   但备份层**必须**依赖它: 一致性快照、schema 版本、迁移链都只存在于持久化层,
   重写等于复制 WAL 知识。修复: 把守卫拆成
   (a) 领域层 (`src/*.py`) 不许依赖 persistence、
   (b) `src/application/`、`src/web/` 各自的守卫 (原有)、
   (c) **新增**"依赖 persistence 的顶层包必须与白名单全等"。
   (c) 比原来的写法**更强**: 新增任何消费者都必须显式登记并写理由。

我自己写错并改正的测试前提（记录在案, 因为它们暴露了实现的真实语义）
----------------------------------------

| 我原本的假设 | 实现的真实行为 |
| --- | --- |
| `total_bytes` 等于归档里所有条目之和 | **不含 `manifest.json`** —— 清单不能把自己算进去 (自指) |
| 活库文件字节 == 归档里快照的字节 | 内容相同但**字节不同** (SQLite 页分配 / 文件头计数), 数据库要用内容断言 |
| `sorted(["Álgebra", "Càlcul"])` 是 `Álgebra` 在前 | `C`(0x43) < `Á`(0xC1), 所以 **`Càlcul` 在前** |
| `_replace_materials` 每棵树调一次 | **一次调用**内部循环处理全部树 (所以"第二次调用失败"这个注入点不存在) |
| 清空数据库后还能查 `courses` | 文件不存在 → 新库没有表 → `no such table: courses` |
| `_seed_database(..., migrations=(001,))` 只建到 001 | 它内部会 `migrate()` 到最新 —— 测"迁移老归档"时必须把链一起传进去 |
| `ZipFile.namelist()` 在 close 后报错 | 只有需要 `fp` 的操作才报错 (`read` / `open`) |
| `os.path.relpath` 可以用来构造相对 data_dir | 跨盘符 (tmp 在 `C:`, 工作目录在 `D:`) 直接 `ValueError` |

Known limitation（明确说明, 不掩饰）
----------------------------------------

1. **没有做跨进程的备份 / 恢复互斥。** 备份与恢复都假定是**离线操作**:
   若另一个进程正在写库, `backup_to` 会拿到一个**一致快照**(安全), 但恢复的
   `os.replace` 会被占用挡住并报 `DatabaseInUseError`(明确报错, 不会静默损坏)。
   没有实现"恢复前自动停掉服务"。本项目是单机单用户, 这符合规范前提, 但不等于
   已被验证。
2. **归档没有加密。** 备份里含课堂原文、板书图片与音频, 任何拿到文件的人都
   能读。规范未要求加密, 本任务不做, 但明确记录 —— 备份文件的存放位置本身
   就是一项安全边界。
3. **只有全量备份, 没有增量 / 差异备份。** 课堂音频动辄数百 MB, 多次全量会
   占磁盘。`prune_backups(keep=N)` 只按**数量**清理, 没有按总大小清理, 也没有
   自动清理策略 (需要调用方显式调用)。
4. **zip 炸弹的默认阈值没有端到端验证。** `MAX_UNCOMPRESSED_BYTES` (8 GiB) 与
   `MAX_COMPRESSION_RATIO` (2000:1) 的**默认值**没有用真实的大归档验证过 ——
   造一个 8 GiB 的解压炸弹不现实。测试覆盖的是限流逻辑本身 (传入缩小后的
   限额), 以及"压缩比 < 1 的不可压缩条目不会被误判"。
5. **恢复是整份替换, 没有"选择性恢复"。** 不能只恢复某一门课或某一份材料。
   规范未要求, 但真实使用中"只找回一份误删的音频"是常见诉求。
6. **恢复前没有磁盘空间预检。** 解压需要大约等于"归档解压后大小"的空闲空间,
   空间不足会在解压中途失败 —— 此时原数据仍未改动 (解压发生在临时目录),
   有回滚测试覆盖, 但没有前置预检与友好提示。
7. **版本闸门依赖清单 + 库内台账两处。** 如果两者都被人为改成当前版本、而内容
   其实是未来 schema 的数据, 就无法识别。这属于"恶意篡改", 不在本任务的威胁
   模型内 (威胁模型针对的是**无意**的损坏与**搬运**来的文件)。
8. **`os.replace` 的原子性保证的是"同卷"。** 自定义 `backups_dir` 与自定义
   `data_dir` 若跨卷, 恢复时 `temp/` 与正式位置仍在同一 `data_dir` 下, 因此
   仍满足同卷; 但 `create_backup` 写 `.part` 再改名这一步在 `backups_dir` 内,
   同样同卷。**没有**跨卷场景的测试。

---

## Task 44 — Configuration / Secrets / Production Error Handling（配置 / 密钥 / 生产错误处理）

Goal
----------------------------------------

把"开发时随手能跑"变成"稳定运行环境"。三件事:

1. 一个唯一配置对象 `AppConfig`, 覆盖规范点名的 9 个字段, 并**记录每个值来自哪一层**。
2. 明确的优先级 `defaults -> config file -> environment variables -> CLI overrides` (逐键覆盖)。
3. 密钥与隐私的**唯一定义** —— 什么算 secret、日志里怎么脱敏、生产/开发两种错误呈现。

本任务**不**引入 secret manager: 规范明确要求"当前没有外部 API 的话, 不要为了'以后'
增加 secret manager"。本任务做的是**反方向**的事 —— 把密钥在当前版本里的每一个
藏身之处逐个堵上, 并逐条测试。

设计取舍
----------------------------------------

1. **配置值带来源 (`AppConfig.sources`), 而不只是最终值。** 只看最终值的话, "CLI 覆盖了
   环境变量"与"环境变量根本没生效"在结果上可能长得一模一样, 优先级就成了不可验证的
   口号。`source_of(key)` 让测试直接断言"这个值来自哪一层"。
2. **`database_path` 由 `data_dir` 派生, 来源标记为 `DERIVED`。** 不硬编码一个默认绝对
   路径; "没被任何一层指定"与"被指定成了某个值"是两件事。
3. **配置文件与 CLI 的未知键是硬错误; 环境变量的未知变量只记名字。** 前两者由我们自己
   解析, 拼错键名却静默忽略会变成"我明明配了却没生效"; 环境变量是继承来的共享命名空间,
   无法区分"拼错"与"别的工具留下的", 因此记入 `unknown_env_vars`(**只记名字, 绝不记值**)
   让它可见即可。
4. **`None` = 该开关没给, 键缺席 = 该层不管, 键出现且为 `null` = 显式置空。** 这三件事被
   明确区分, 并有对应测试。CLI 侧靠 argparse 的 `default=None` 实现 (`--debug` 与
   `--no-debug` 必须能区分"没给"与"给了 False")。
5. **配置文件只在"工作目录 + 显式指定"两处找。** 最初的实现是"在 data_dir 下找", 但
   配置文件的首要职责之一就是说明 `data_dir` 在哪 —— 再去 data_dir 里找它在用户看来
   是个循环。改成工作目录后 `_effective_data_dir_guess` 这个补丁被整块删掉。
6. **`database_path` 必须在 `data_dir` 内。** 否则 Task 43 的备份/恢复 (只覆盖 data_dir)
   会**静默漏掉**这个库 —— 最坏的一类数据丢失。这是跨任务不变式, 由配置层强制。
7. **`data_dir` 不许落在项目的 `src/` 或 `tests/` 内。** AGENTS.md 的硬性规则变成可执行
   检查, 而不是文档里的一句话。
8. **`host` 默认只允许回环地址, 非回环必须显式 `allow_remote=True`。** 单机单用户应用
   不该因为一次手滑就绑上 `0.0.0.0`。错误信息里直接点名开关名。
9. **`max_upload_size` 的单位是字节, 小于 1 KiB 直接报错并提示"是不是想写 MB"。**
   `200` 这个值几乎必然是"想写 200 MB", 与其让它变成"每个上传都超限"的谜题, 不如在
   启动时就说清楚。
10. **whisper 校验复用 domain 层的 `WhisperConfig.validate()`, 不抄一份支持列表。**
    抄一份的后果是配置层与 provider 层各自漂移, 出现"配置通过了但 provider 拒绝"的错位。
    原始 `ASRConfigurationError` 作为 `cause` 保留。
11. **密钥的判定基于"词"而不是"子串"。** 子串匹配会把 `tokenizer_path` (Whisper 里非常
    正常的配置项) 判成密钥, 于是一份合法配置被直接拒绝。词级判定同时认得
    `x-api-key` / `apiKey` / `API_KEY` / `apikey`, 又不误伤 `tokenizer_path` / `author` /
    `cache_key_id` / `monkey`。
12. **密钥有四个堵点: 源码 / 配置 / 日志 / 输出。** 其中"输出"包括 `repr()` ——
    dataclass 默认生成的 `repr` 会原样打印字段值, 而 `repr` 会出现在 traceback、pytest
    失败输出、调试器面板里, 这些内容经常被直接复制进日志或 issue。因此 `AppConfig`
    自定义了脱敏的 `__repr__`。
13. **日志字段名固定为 `event` / `message_text` / `detail` / `component`, 不用
    `extra={"message": ...}`。** 见下面"发现并修复的真实缺陷"第 1 条 —— 那是本项目里
    真实踩过的坑。
14. **隐私过滤分两档: 密钥型键名整体替换, 整段内容型键名只留摘要。** 密钥不能做摘要
    (摘要会留下密钥的前若干字符); 而"学生答案/课堂原文"做摘要比直接丢弃更好 —— 日志
    仍然能告诉你"当时确实有这么一段、有多长"。
15. **`src.api` 在 `bootstrap` 里延迟 import。** `src.api.server` 依赖
    `src.application.workspace`; 若在模块顶层 import, 一旦将来有人把 `bootstrap` 加进
    `src.application.__init__`, 就形成 `application -> bootstrap -> api ->
    application.workspace` 的环。延迟 import 同时让 `--print-config` 不必为 HTTP 层付出
    import 成本。
16. **`bootstrap` 刻意不从 `src.application` 再导出**, 并由子进程断言守住 (上面那个环)。

新增模块
----------------------------------------

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `src/application/config.py` | ~1000 | `AppConfig` / `ConfigSource` / 四层合并 / 校验 / 密钥与隐私原语 |
| `src/application/logging_setup.py` | ~400 | 5 级日志 / `StructuredFormatter` / `PrivacyFilter` / `configure_logging` / `log_event` |
| `src/application/bootstrap.py` | ~330 | 组合根: 目录布局 + 打开迁移数据库 + ASR/OCR provider + Workspace + ApiServer + BackupService |
| `src/application/cli.py` | ~380 | argparse / `--print-config` / `--check` / Windows 控制台编码安全 / 退出码 |
| `src/api/server.py` | 改 | `log_message` 改用 `log_event` (**修复每个响应都崩的缺陷**) |
| `src/application/__init__.py` | 改 | 再导出 `AppConfig` / `ConfigSource` / `load_config` / 日志入口 (不含 bootstrap) |

配置项 (规范点名的 9 项全部覆盖)
----------------------------------------

| 配置键 | 默认值 | 环境变量 | 校验 |
| --- | --- | --- | --- |
| `data_dir` | `<cwd>/classroom-data` | `CLASSROOM_DATA_DIR` | 非空、非文件、不在 `src/`/`tests/` 内 |
| `database_path` | 派生 `data_dir/database/classroom.sqlite` | `CLASSROOM_DATABASE_PATH` | 必须位于 `data_dir` 内 |
| `host` | `127.0.0.1` | `CLASSROOM_HOST` | 非回环需 `allow_remote` |
| `port` | `8765` | `CLASSROOM_PORT` | `0..65535` (0 = 系统分配) |
| `max_upload_size` | `200 MiB` | `CLASSROOM_MAX_UPLOAD_SIZE` | 正整数且 ≥ 1 KiB |
| `whisper_model` | `base` | `CLASSROOM_WHISPER_MODEL` | 复用 `WhisperConfig.validate()` |
| `whisper_device` | `cpu` | `CLASSROOM_WHISPER_DEVICE` | `cpu`/`cuda` |
| `whisper_compute_type` | `int8` | `CLASSROOM_WHISPER_COMPUTE_TYPE` | 4 种受支持值 |
| `whisper_language` | 无 | `CLASSROOM_WHISPER_LANGUAGE` | ISO 639-1/639-2; 空串 = 显式置空 |
| `ocr_config` | `{"kind": "auto"}` | `CLASSROOM_OCR_CONFIG` (JSON) | 递归拒绝密钥型键名; 必须可 JSON 序列化 |
| `log_level` | `INFO` | `CLASSROOM_LOG_LEVEL` | 规范点名的 5 级 |
| `asr_mode` | `auto` | `CLASSROOM_ASR_MODE` | `auto`/`real`/`mock` |
| `debug` | `False` | `CLASSROOM_DEBUG` | 布尔 |
| `allow_remote` | `False` | `CLASSROOM_ALLOW_REMOTE` | 布尔 |

优先级规则 (逐条可测)
----------------------------------------

1. 层内**出现的键 = 该层显式指定**; 未出现的键 = 该层不管。
2. 高优先级层逐键覆盖低优先级层 (不是整层替换)。
3. 配置文件位置: `--config` / `CLASSROOM_CONFIG` (必须存在), 否则 `<cwd>/classroom-assistant.json`。
4. 相对路径按**进程工作目录**解析 (标准 CLI 行为); `cwd` 参数只用于定位默认 data_dir 与配置文件。
5. 枚举型值先规范化大小写再校验 (`log_level` → 大写, `whisper_*`/`asr_mode` → 小写)。

密钥边界 (规范: 不得出现在 source code / database plaintext / logs / UI)
----------------------------------------

| 藏身之处 | 处理方式 | 对应测试 |
| --- | --- | --- |
| 源码 | 不引入 secret manager (反向断言) + 扫描 `sk-` 字面量 | `test_config_module_does_not_import_a_secret_manager` / `test_no_api_key_literal_lives_in_the_source_tree` |
| 配置文件 | `ocr_config` **递归**拒绝密钥型键名 (含嵌套与列表) | `test_ocr_config_rejects_a_nested_secret` |
| 数据库明文 | 配置对象里根本不可能有密钥, 因此不会落库; `ocr_config` 必须 JSON 可序列化 | `test_ocr_config_must_be_json_serialisable` |
| 日志 | 键名命中 → 整体替换; 整段内容 → 有界摘要; 消息与 detail 走值形状脱敏 | `test_a_secret_in_extra_never_reaches_the_log` |
| UI / 输出 | `to_dict` / `describe` / `repr` 全部脱敏 | `test_config_repr_and_describe_never_leak_a_secret` |
| 诊断信息 | 未知环境变量**只记名字** | `test_unknown_env_var_values_are_never_stored` |

测试
----------------------------------------

本任务新增 **485** 个测试 (规范要求 ≥ 40):

| 文件 | 数量 | 覆盖 |
| --- | --- | --- |
| `tests/test_config.py` | 207 | 默认值 / 四层优先级与来源 / 配置文件 / 环境变量 / 类型转换 / 校验 / 值对象语义 / 输出 / 单一真相常量 |
| `tests/test_config_secrets.py` | 86 | 密钥键名判定 / 文本脱敏 / 截断 / 映射脱敏 / 嵌套密钥路径 / 配置层与输出层不泄露 |
| `tests/test_logging_setup.py` | 88 | 5 字段 5 级 / 人类可读与 JSON 形态 / 时钟注入 / 隐私过滤 / 幂等安装 / `log_event` / **ApiServer 注入 logger 的回归测试** |
| `tests/test_cli.py` | 53 | 开关映射 / `--no-*` 与"没给" / `--print-config` / `--check` / 退出码 / 生产 vs debug / Windows 编码 |
| `tests/test_bootstrap.py` | 49 | 装配 / 配置项真的驱动运行时 / ASR-OCR 模式与回落可见性 / 快照与关闭 / HTTP 端到端 / import 方向 |
| `tests/test_persistence_layering.py` | +2 | 应用层只有 `bootstrap.py` 可依赖 persistence (枚举 + 全等) |

规范要求 → 测试映射:

| 规范条目 | 对应测试 |
| --- | --- |
| `AppConfig` 至少 9 个字段 | `test_required_spec_fields_are_present` |
| 优先级 defaults→file→env→CLI | `test_all_four_layers_apply_at_once` 等 |
| 明确规则并测试 | `test_sources_record_the_winning_layer` / `test_absent_key_...` / `test_explicit_null_...` |
| 密钥不得出现在源码 | `test_config_module_does_not_import_a_secret_manager` |
| 不要 secret manager | 同上 (反向断言) |
| 5 个日志级别 | `test_the_five_log_levels_are_exactly_the_spec_ones` |
| 日志含 5 个字段 | `test_json_output_contains_every_required_field_by_name` |
| 日志禁写完整答案 | `test_the_full_answer_never_reaches_the_log` |
| 日志禁写 API key / secret | `test_a_secret_in_extra_never_reaches_the_log` |
| 日志禁写不必要的课堂原文 | `test_an_over_long_message_is_truncated` |
| 生产: 友好错误 + 详细日志 | `test_invalid_configuration_returns_the_config_exit_code` |
| 开发: 可暴露 debug | `test_debug_mode_exposes_the_traceback` |
| ≥ 40 tests | 485 |

Test Summary
----------------------------------------

```text
Base (after Task 43): 2952 passed, 20 deselected
Task 44 added:        485 tests (5 新文件 + 2 个新分层守卫)
Final:                3437 passed, 20 deselected
Failed:               0
```

Validation
----------------------------------------

以下每一条都是实际执行过的检查 (而非声称):

- `pytest tests/test_config.py` -> `207 passed`
- `pytest tests/test_config_secrets.py` -> `86 passed`
- `pytest tests/test_logging_setup.py` -> `88 passed`
- `pytest tests/test_cli.py` -> `53 passed`
- `pytest tests/test_bootstrap.py` -> `49 passed`
- `pytest tests/test_persistence_layering.py` -> `26 passed`
- `pytest -q -m "not integration"` (全量回归) -> PASS, 0 failed
- `python -m compileall -q src tests` -> 退出码 0
- 优先级: `port` 同时被配置文件/环境变量/CLI 指定 -> 取 CLI, 且 `source_of("port")` 为 `cli`
- `database_path` 未被任何一层指定 -> 派生为 `data_dir/database/classroom.sqlite`, 来源 `DERIVED`
- `CLASSROOM_API_KEY=<真实值>` 注入环境 -> 值不出现在 `to_dict()` / `repr()` / `describe()` / `unknown_env_vars` 的任何位置
- `ocr_config={"engine": {"api_key": "..."}}` -> `ConfigurationError`, detail 指明 `engine.api_key`
- `ocr_config={"note": "api_key=sk-live-..."}` -> 通过校验 (键名无害), 但 `to_dict()` 输出已脱敏
- 5000 字的学生答案作为 `answer=` 传入日志 -> 输出中只有前 200 字 + `truncated 4800 chars`
- 注入 logger 的 `ApiServer` 处理 `GET /api/health` -> HTTP 200, 日志含 `event=http_request`, 无 `KeyError`
- `python -c "import src.api.server; import src.application.bootstrap"` 与反序 -> 均退出码 0 (无 import 环)
- `python -c "import src.application; print('src.application.bootstrap' in sys.modules)"` -> `False`
- `--print-config` -> 不创建目录、不打开数据库、不绑定端口
- `--check` -> 建库并迁移到 schema v2, 输出含 `database_schema_version` / `asr_mode` / `ocr_mode`
- `--check` 与"该端口已被占用" -> 仍然成功 (`--check` 不绑定端口)
- 非法配置 -> 退出码 2, stderr 只有 `[CONFIGURATION_ERROR] ...`, 无 traceback
- 同一个非法配置 + `--debug` -> stderr 含 traceback
- 只能写 ASCII 的假控制台 + 含中文/重音的 `data_dir` -> 输出降级替换, 不抛 `UnicodeEncodeError`
- `asr_mode="real"` 且真实运行时缺失 -> `ConfigurationError` ("not installed"), **不**静默给 Mock
- `asr_mode="auto"` 且真实运行时缺失 -> Mock + 一条 `WARNING runtime_note`
- `ocr_config.kind="auto"` 且本地引擎缺失 -> `ocr_mode="mock"` (由实际 provider 类型推导), 并有警告
- `build_runtime` -> 数据库在 `config.database_path` 处创建, `BackupService` 可对其实拍备份
- 同一个 `data_dir` 上重复装配 (模拟重启) -> schema 版本一致, 无异常

发现并修复的真实缺陷
----------------------------------------

1. **`ApiServer` 注入 logger 后, 每一个 HTTP 响应都崩。** `log_message` 调用
   `logger.info("http_request", extra={"message": fmt % args})`, 而 `message` 是
   `logging` 的**保留属性名** -> `makeRecord` 抛
   `KeyError: "Attempt to overwrite 'message' in LogRecord"`。致命之处在于
   `log_message` 位于 `BaseHTTPRequestHandler.send_response()` 的调用路径上, 于是
   异常发生在**发响应头之前**: 客户端看到的是 `RemoteDisconnected`, 服务端只有一条
   traceback。这个缺陷一直潜伏着, 因为此前**没有任何测试给 `ApiServer` 传过 logger**
   (默认 `logger=None`)。Task 44 的运行时装配第一次真的传了 logger, 于是它立刻暴露。
   修复: 改用 `log_event` (字段名固定为 `event` / `message_text`)。回归测试:
   `test_api_server_with_an_injected_logger_still_serves_requests`。
2. **`repr(AppConfig)` 泄露密钥。** `to_dict()` 与 `describe()` 都做了脱敏, 但 dataclass
   自动生成的 `__repr__` 直接打印字段值 —— 而 `repr` 会出现在 traceback、pytest 失败
   输出与调试器面板里。修复: 自定义 `__repr__`, 走同一套脱敏。
3. **`tokenizer_path` 被误判为密钥。** 最初的判定是"归一化后子串匹配", 于是
   `tokenizerpath` 含 `token` -> 判为密钥 -> 一份完全合法的 Whisper 配置被拒绝。
   修复: 改为词级判定 (驼峰/连字符/下划线先分词, 再匹配词与相邻词对), 并补测试
   `test_ordinary_keys_are_not_flagged`。
4. **嵌套密钥能绕过检查。** 最初只检查 `ocr_config` 的顶层键名, 于是
   `{"engine": {"api_key": "..."}}` 直接走过去。修复: 递归 `secret_paths()`, 并在错误
   detail 里给出完整路径。
5. **配置文件在 data_dir 下查找造成循环。** 配置文件的首要职责之一就是告诉程序
   `data_dir` 在哪, 再去 data_dir 里找它是循环。修复: 只在工作目录与显式指定两处查找,
   并删掉了为此而写的 `_effective_data_dir_guess` 补丁 (以及它的两处测试)。
6. **`whisper_language: null` 被拒绝。** 文档写着"层内出现且为 null = 显式置空", 但
   `_as_text` 对 `None` 直接报错, 规则与实现不一致。修复: 该字段是唯一的 Optional,
   显式接受 `None`; 其它字段写 `null` 仍然报错 (它们没有"空"这个合法状态)。
7. **`log_event(**extra)` 传进来的字段被静默丢弃。** formatter 只渲染
   `event`/`message`/`detail`, 其余 extra 字段一个都不输出 —— 调用方传了字段却什么也
   看不到, 会以为日志系统坏了。修复: 归入 `extra={...}` 一并渲染 (已被 PrivacyFilter
   处理过), 并给 `json.dumps` 加 `default=str` 保证日志不会因不可序列化字段而崩。
8. **`log_event` 保留键名的报错路径不通。** `RESERVED_EXTRA_KEYS` 含 `message`, 但
   `message` 是 `log_event` 的**正式参数**, 传 `message=` 根本走不到 `**extra`, 于是
   测试期望的 `ConfigurationError` 变成了 `TypeError: got multiple values`。修复:
   明确"`message=` 就是推荐用法", 测试只覆盖其余保留名。

我自己写错并改正的测试前提
----------------------------------------

| 我原来的写法 | 错在哪 | 改正 |
| --- | --- | --- |
| `logging.LogRecord(..., extra=extra)` | `extra` 不是 `LogRecord.__init__` 的参数, 那是 `Logger.makeRecord` 的活 | 构造后手工 `setattr`, 模拟真实路径 |
| 断言 `"timestamp" in <人类可读输出>` | 人类可读格式按**固定位置**排列, 不含字段名字面量 | 字段名断言改用 JSON 形态; 人类可读形态断言**顺序** |
| 断言 `stream.count("only once") == 1` | 该字符串合法地出现两次 (`event=` 与 message) | 改为断言行数为 1 |
| `database_path` 覆盖用 `<cwd>/custom/db.sqlite` | 它在 data_dir **外**, 被"必须在 data_dir 内"的校验正确拒绝 | 改成 `data_dir` 内的路径 |
| `data_dir=None` 期望报错 | `None` 的语义是"这个开关没给", 应当沿用下层 | 拆成独立测试断言来源为 `default` |
| `data_dir="relative-data"` 期望相对 `cwd` 解析 | 相对路径按**进程工作目录**解析才是标准 CLI 行为 | 断言相对 `os.getcwd()` |
| `describe()` 里比较 `path.replace("\\","/")` | `describe` 用 `repr()` 输出路径, 反斜杠被转义成 `\\`, 替换后变成 `//` | 直接比较 `repr(path)` |
| `--ocr-config` 期望 `parse_cli` 返回 dict | 解析 JSON 属于配置层职责, CLI 层保持原样才是正确的分层 | 同时断言两层: CLI 层是字符串, 配置层是 dict |
| 注入 logger 时断言输出为空 | 注入的 logger **应该**收到 `runtime_ready` | 改为断言 handler 列表未被改动 |
| `test_redaction_happens_before_truncation` 断言占位符完整存在 | 占位符本身也被截断了 (安全性质仍然成立) | 调整 limit 让占位符落在截断点之前 |

Known limitation（明确说明, 不掩饰）
----------------------------------------

1. **业务对象仍然在内存里, 没有被 SQLite 持久化。** 本任务让配置**真的驱动**了数据库的
   创建/迁移与备份 (`database_path` 不再是摆设字段, 备份归档里的库是真实、可迁移、可恢复
   的), 但 Course / Material / Evidence / KnowledgePoint / Student 仍由内存中的领域服务
   持有。把领域对象映射到 SQLite 属于**既有引擎**的持久化改造, 不在本任务范围内 ——
   规范的核心原则之一是"不重写核心引擎"。因此: **重启进程会丢失业务数据** (数据库里
   目前只有 schema 台账)。
2. **没有真正的秘密需要保护。** 当前版本没有任何外部 API, 所以"密钥"这条规则是通过
   **拒绝**密钥进入配置来实现的, 而不是通过加密或密钥管理。如果将来接入外部 API,
   需要重新设计 (届时才应该引入 secret manager)。
3. **`data_dir` 的 `src/`/`tests/` 检查绑定在本仓库布局上。** 它是 AGENTS.md 规则的可执行
   版本, 依赖 `config.py` 相对仓库根的位置。项目若被打包分发到别处, 这条检查会退化为
   "不触发" (无害, 但也不再提供保护)。
4. **配置文件只支持 JSON。** 不支持 TOML/YAML。这是刻意取舍 (一个格式, 标准库解析,
   没有"解析器读的是另一个分支"的可能), 但用户若写了 `.toml` 只会得到"文件不存在"。
5. **`asr_mode="auto"` 的可用性探测只看"包装没装"。** 用 `importlib.util.find_spec`
   判断, 不加载模型。因此 `faster_whisper` 装了但模型权重缺失时, 仍会报告 `asr_mode="real"`,
   失败会推迟到第一次转录。这是刻意的 (启动时加载模型太慢), 但没有前置校验。
6. **隐私过滤只覆盖"约定好的渠道"。** `log_event` 的 `extra` 字段、`detail` 映射、以及最终
   消息字符串都会被脱敏; 但若有人绕过 `log_event` 直接用 `logging` 打一条含整段课堂原文的
   消息, 长度截断会生效 (formatter 层), 语义层面的"这是不必要的课堂原文"则无法自动判断。
   规范要求的是"禁止写入", 本项目用"约定 + 有界摘要 + 键名黑名单"来实现, 不是内容识别。
7. **没有做日志轮转。** `configure_logging` 只往流里写 (默认 stderr), 不写文件、不轮转。
   `logs_dir` 已由配置提供, 但文件日志与保留策略留给后续任务。
8. **`--check` 不验证端口是否可用。** 它刻意不绑定端口 (否则会和主进程打架), 因此
   "端口被占用"只会在真正启动时报出来。Task 46 的 `health` 脚本需要自行处理这一点。
9. **`max_upload_size` 只有一个上限, 没有按材料类型区分。** 课堂音频与一张板书照片
   用同一个上限, 大音频会撞上限而小图片浪费额度。
10. **`allow_remote=True` 只是解除了绑定限制, 没有配套的认证。** 一旦真的绑到
    `0.0.0.0`, 任何能访问该端口的人都能读写全部课程数据。规范把本应用定位为单机单用户,
    因此这是"用户明确要求才可能发生"的边界, 但值得写在这里。

---

## Task 45 — Real Course End-to-End Acceptance（已完成）

**回归: 3542 passed / 20 deselected / 0 failed**（Base after Task 44: 3437；
+105 验收测试；本次会话顺带修掉的 8 个回归也已归零）

首个**真实课堂材料**端到端验收：用一套真实课程夹具
（`tests/fixtures/acceptance/`，含 6 份材料）驱动整条流水线，按 spec
「PDF / DOCX / audio / image / duplicate / failure / restart / backup /
review / student learning」10 个强制场景跑 ≥30 条端到端测试。ASR/OCR 走
`MockASRProvider` / `ScriptedOCREngine`（标注 `asr_mode="mock"` /
`ocr_mode="mock"`），保证可复现。

### 验收夹具与覆盖

| 角色 | 文件 | 类型 | 场景 |
| --- | --- | --- | --- |
| handout | `tema1-guia-docent.pdf` | document | PDF / 证据溯源 |
| student_notes | `tema1-apunts-alumne.docx` | document | DOCX / 证据溯源 |
| audio | `tema1-classe.wav` | audio | 转写证据 / 真实换行 |
| board_image | `tema1-pissarra.png` | image | OCR 证据 |
| student_summary | `tema1-resum-alumne.md`（会话外） | note | 跨会话/外部材料 |
| broken_document | `tema1-document-trencat.pdf` | — | failure / 错误处理 |

10 个强制场景全部有对应 `Test*` 类（`TestDataset` / `TestStepFlow` /
`TestAcceptanceQuestions` / `TestMaterialKinds` / `TestDuplicateScenario` /
`TestFailureScenario` / `TestRestartScenario` / `TestBackupScenario` /
`TestReviewScenario` / `TestStudentLearningScenario`），外加 `TestHonestyAndDeterminism`
与 `TestDefectRegressions`（#1–#13）。合计 **105 条**端到端测试，全部绿。

### 实测方法（确定性核验）

`AcceptanceHarness(dataset, data_dir).run()` 产出冻结的 `AcceptanceReport`
（17 步 + 7 问 + 材料/知识点/冲突/覆盖/缺口/证据索引/学生/练习/评测/
学习态前后/计划前后/学习路径）。确定性用递归 diff 验证：两份**独立**运行
（不同 `data_dir`）的 report 逐字段比较 → **`TOTAL DIFFS: 0`**。

### 本次会话发现并修复的真实缺陷（spec §一：现实与基线不符就修，不掩饰）

| 缺陷 | 根因 | 修复 |
| --- | --- | --- |
| **#12 document_id 依赖绝对路径** | `document_input._compute_document_stable_id` 用 `type\|path\|size` 寻址，绝对路径被哈希进 `doc-evidence-*` 与下游知识点 ID → 同一文档放到不同目录得到不同 ID，`test_two_independent_runs_produce_identical_reports` 失败 | 改为内容寻址：有 `material_id` 时 `raw = f"{type}\|{material_id}"`（material_id 本身已由文件 SHA256 内容寻址）；仅 `material_id` 缺失的遗留直解析路径回落 `type\|path\|size` |
| **#12（续）note 证据 location 带绝对路径** | `note_parser` 把 `location` 设为上传绝对路径，被哈希进 `evid-*` ID，只有 MD 笔记跨目录不确定 | `location = os.path.basename(...)`，去除路径（location 是哈希输入，必须路径无关） |
| **#13 审查决定被静默重置** | `get_review_candidates` 每次扫描把任何 CONFLICTED 的非 PENDING 点重置回 PENDING，人类 `resolve_conflict` 的决定不粘滞 → 跨重跑/重启确定性破裂 | 人类决定粘滞；非 PENDING 点**仅当**「证据/冲突上下文指纹自决定后发生变化」（真有新增证据/新冲突）才重新打开。指纹存于 `ReviewRecord.evidence_fingerprint`（含 evidence_refs / 冲突 / validation_status / needs_verification），见 `_review_context_fingerprint` |
| 修复 #12/#13 引发的 8 个测试回归（本会话顺带修） | （a）`TestDocumentStableId` 仍按旧 3 参签名调 `_compute_document_stable_id`；（b）`DocumentMaterialValidator.compute_material_id` 漏传 `material_id=""`；（c）`test_content_preservation` / `test_audio_custom_segments_timestamps` 断言字面 `"\\n"`，而设计用真实换行（`models.to_transcript_evidence` 的真实换行缺陷已修） | （a）（b）更新为 4 参签名 / 补 `""`；（c）改为断言真实换行 `'\n'` |

> 备注：缺陷 #1–#11 在上一会话的验收中发现并修复（均为真实阻塞缺陷，非文档问题），
> 本会话在此基础上补 #12 / #13 及 note 确定性泄漏，并把 #12/#13 修复引入的 8 个
> 单测回归全部归零。

### 我自己写错并改正的测试前提（本会话）

| 我原来的写法 | 错在哪 | 改正 |
| --- | --- | --- |
| `material_id` 未传给 `_compute_document_stable_id` | 旧 3 参签名 → 路径寻址，跨目录不确定 | 4 参 `(material_id, path, size, mtime)`；遗留路径回落保留 |
| 断言 `"\\n" in ev.content`（字面反斜杠 n） | 设计用真实换行，字面 `\n` 永远不在内容里 | 改 `'\n'`（真实换行） |
| `#13` 一刀切「所有非 PENDING 点都不进候选队列」 | 把「真有新增冲突才应重新打开」也误杀，`test_new_conflicting_evidence_resets_confirmation` 失败 | 指纹门控：上下文变了才重新打开，没变则粘滞 |

### 已知局限（明确说明，不掩饰）

1. **审查「重新打开」依赖指纹比较，不依赖时序。** 若同一份证据被重复摄取导致
   `evidence_refs` 顺序变化但集合不变，指纹稳定（已排序去重），不会误重新打开。
2. **`compute_material_id`（直解析路径）仍按 `type|path|size` 寻址**，不是内容寻址；
   这只影响「绕过 material_workflow 的直接解析调用」，验收夹具走 `material_workflow`
   的内容寻址 `material_id`，不受影响。
3. **失败材料（`broken_document`）只验证错误路径不崩，不产出知识点** —— 符合 spec 的
   failure 场景要求（错误被捕获、报告、不污染其余材料）。
4. **ASR/OCR 为 Mock**。真实 Whisper/OCR 的端到端正确性不在本任务范围（spec Task 18/20
   已单测引擎接口）；本任务验证的是「真实材料形状下的流水线正确性」，非引擎准确率。

### Validation

以下每一条都是实际执行过的检查（而非声称）：

- `pytest tests/test_acceptance.py` -> `105 passed`
- `pytest -q -m "not integration"`（全量回归）-> `3542 passed, 20 deselected, 0 failed`
- `python -m compileall -q src tests` -> 退出码 0
- 递归 diff 两次独立 `AcceptanceHarness.run()`（不同 `data_dir`）-> `TOTAL DIFFS: 0`
- `test_two_independent_runs_produce_identical_reports` -> 通过（#12 修复的直接证据）
- `test_review_candidates_are_produced`（`review_candidates_before > 0`）-> 通过
- `test_new_conflicting_evidence_resets_confirmation` -> 通过（#13 指纹门控的直接证据：
  真有新增冲突才重新打开）
- `TestMaterialKinds` 覆盖 `document / audio / image` 三种 source_type -> 通过
- `TestDuplicateScenario` / `TestFailureScenario` / `TestRestartScenario` /
  `TestBackupScenario` / `TestStudentLearningScenario` -> 全绿
- `test_defect_transcript_evidence_uses_real_newlines` -> 通过（真实换行，非字面 `\n`）

## Task 46 — Windows Packaging / One-Command Startup（Windows 打包 / 一键启动）

Goal
----------------------------------------

让不碰命令行的用户也能用起来: 双击 `scripts/start.bat` 起本地服务并自动打开
浏览器, 双击 `stop.bat` 停掉, 双击 `health.bat` 做启动前自检。三个 `.bat` 都是
**薄壳**, 逻辑全部在 `src/application/launcher.py` —— 这样 Windows 专有的东西
(含空格的路径、Unicode 路径、控制台代码页、进程终止语义、浏览器打开) 都能被
pytest 直接测到, 而不是只能靠手工点。

设计取舍
----------------------------------------

1. **逻辑在 `.py`, `.bat` 只做转调。** `.bat` 里只留 `chcp 65001`、便携解释器
   定位 (`%~dp0..\Python\pythoncore-3.14-64\python.exe`, 找不到才回落 `python`)、
   `cd /d "%~dp0.."` 和一次 `-m src.application.launcher <cmd>`。把逻辑写进批处理
   会让它彻底无法被测试覆盖 —— 而本项目对"未验证的代码路径"是有教训的 (见
   Task 44 的休眠缺陷)。

2. **端口占用是"启动失败", 不是"配置错"。** `PortInUseError` 同时继承
   `ApplicationError` 与 `OSError`: 前者让 CLI 的错误分派能识别它, 后者让既有的
   `pytest.raises(OSError)` 断言继续成立。消息固定为 `Port XXXX is already in use.`,
   **不夹带** `[PORT_IN_USE]` 错误码前缀 —— 普通用户不需要知道我们的错误码体系。

3. **环境自检分"硬失败"与"警告"两档。** 缺 `faster-whisper` / OCR 引擎在
   `auto` 模式下会回落 Mock, 属于可接受降级 ⇒ 记 `warning`, 不计入
   `report.ok`; 但 `asr_mode="real"` 或 `ocr.require_real` 时同一个缺失就是
   硬失败。同一个事实在不同配置下严重性不同, 不能一刀切。

4. **`health` 装配一遍运行时, 但不绑端口、不写 PID 文件。** 目的是在真正启动
   之前验证"库能建、迁移能跑、配置能被消费"; 用 `try/finally` + `close_runtime`
   保证装配出的数据库一定被关掉 (否则 Windows 上文件句柄会挡住后续操作)。

5. **Windows 进程终止走 `taskkill /F`。** `os.kill(pid, SIGTERM)` 在 Windows 上
   走 console-ctrl-event, 对非控制台子进程常常**无效** —— 这是实测结论, 不是
   理论风险。POSIX 上仍用 `SIGTERM`。

6. **进程存活判定用 `tasklist`, 且不解码文本。** `os.kill(pid, 0)` 在 Windows 上
   语义不稳; 而 `tasklist` 的输出按系统代码页编码, 强行 UTF-8 解码会让 reader
   线程抛 `UnicodeDecodeError`。因此只做 ASCII 字节包含判断。

7. **控制台输出做编码降级。** 内容含 `Álgebra` 这类字符而控制台是 GBK/cp1252 时,
   按目标编码 `replace` 后重写, 绝不抛 `UnicodeEncodeError` 让启动崩在打印上。

8. **运行态标记落在 `data_dir/.run/`。** PID 与端口分开两个文件; 端口文件不是
   冗余 —— `stop` 需要用它握手确认身份 (见 Task 47 的 PID 复用防护)。

9. **端口探测用独立 socket 且显式 `SO_REUSEADDR=0`。** 复用地址会让"端口已被
   占用"探测不出来; `port == 0` (交给 OS 分配) 永远算空闲。探测只是 best-effort,
   最终裁决仍在 `ApiServer` 真正 bind 时。

10. **浏览器打开用注入的回调, 而不是模块内直接调 `webbrowser`。** 测试可以换成
    哑实现 (否则每跑一次测试都会拉起系统浏览器), 产品路径由 `main()` 显式传入
    真实实现 (见"发现并修复的真实缺陷"第 1 条)。

交付文件
----------------------------------------

新增:

- `src/application/launcher.py` — 启动器 (环境自检 / 启停 / PID 标记 / 浏览器打开 / CLI 子命令)
- `scripts/start.bat` / `scripts/stop.bat` / `scripts/health.bat` — 三个双击入口
- `tests/test_startup.py` — 交付时 37 条 (Task 47 又追加 9 条, 现共 46 条)

修改:

- `src/application/errors.py` — 新增 `PortInUseError`
- `run_tests.cmd` — 改用便携解释器 (`Python\pythoncore-3.14-64\python.exe`) 并设置
  `PYTHONPATH`, 跑非集成全套

测试（37 tests, Task 46 交付时）
----------------------------------------

- **健康检查 (3)**: 快照含 application/version/data_dir/port/host/asr_mode/ocr_mode;
  health 不占端口; health 不写 PID 文件
- **启动 / 停止 (6)**: 非阻塞启动、`url` 形状、浏览器打开回调收到 URL、只调一次、
  无回调不报错、stop 关服务并清标记、stop 幂等
- **端口占用 (3)**: 错误消息字面量、`Launcher.start` 透传 `PortInUseError`、
  CLI 路径打印友好提示且退出码为 `EXIT_STARTUP_ERROR`
- **端口探测 (3)**: 空闲端口 / `port=0` / 已占用端口
- **环境自检 (10)**: python / dependencies / data directory / database / port 五项
  各自 ok 与失败分支、模型回落是 warning 而非 failure、`asr_mode="real"` 缺引擎是
  failure、`report.ok` 与 `as_dict()` 形状
- **PID / 端口文件 (1)**: 写-读-删往返
- **进程停止 (2)**: 真实子进程被终止; 不存在的 PID 安全返回 True
- **Windows 路径 (4)**: 含空格 / Unicode (`Álgebra 代数`) / 长路径 / 系统临时目录
- **UTF-8 控制台 (1)**: 模拟 ascii 控制台, 非 ASCII 内容必须降级替换而不是抛异常
- **子命令 (2)**: `health` 子命令输出环境自检与快照; 无实例时 `stop` 报错
- **bat 脚本 (1)**: 三个 `.bat` 存在、都调用 `src.application.launcher`、且含自身命令名

发现并修复的真实缺陷
----------------------------------------

**`start.bat` 承诺的"浏览器自动打开"从未接线。**（本次会话发现并修复）

- 现象: `scripts/start.bat` 的注释与 `launcher.py` 的模块文档都写着"浏览器自动
  打开 `http://127.0.0.1:<port>`", 但实际双击 `start.bat` 只会起服务, 浏览器不会
  打开。
- 根因: `Launcher.browser_opener` 参数从 Task 46 起就存在, 测试也覆盖了"回调收到
  URL", 但 `main()` 的 `start` 分支一直构造 `Launcher(config)` —— opener 走默认
  `None`, 于是产品路径上**从不打开**。测试全绿是因为测的是"传了回调时行为正确",
  从没有一条测试盯"产品路径到底传没传"。
- 修复: 新增 `_open_browser()` (`webbrowser.open`, 异常吞掉 —— 打不开浏览器不该
  让已经绑定成功的服务崩掉), `main()` 的 `start` 分支显式传入。
- 配套测试 (2 条): `test_main_start_passes_browser_opener` (monkeypatch `Launcher`
  后断言 `browser_opener is _open_browser`, 直接盯接线, 而不是盯行为) +
  `test_open_browser_swallows_failure`。
- 这是本项目第 6 个"能力存在但未接线"实例 (前五个见 Task 39/40/41/44 的同类
  记录), 形状完全一致: **接口齐全、单测覆盖了被注入的实现, 唯独装配层没接。**

Packaging 评估（spec Task 46 明确要求）
----------------------------------------

规格要求评估 `source distribution` 与 `PyInstaller executable` 两条路线, **优先保证
可重复构建**, 且**不要为了生成 exe 而破坏开发安装**。

| 维度 | source distribution（现状） | PyInstaller one-file exe |
| --- | --- | --- |
| 可重复构建 | 高 —— 同一份源码, 行为一致 | 较低 —— 隐藏导入、捆绑解释器, 每次构建都可能漂移 |
| 产物体积 | 小 | 100 MB+（再捆 Whisper 模型更大） |
| 模型 / 数据文件 | 留在磁盘上, 用户可见可替换 | 必须一起捆绑, 或改成运行时定位 |
| 杀毒软件 | 无影响 | 未签名的 one-file exe 常被误报 |
| 开发安装 | 不受影响 | 需额外构建步骤, 否则产物与源码脱节 |
| 可调试性 | 完整 traceback | 捆绑解释器会模糊栈信息 |

**结论: 采用 source distribution + 一键启动脚本, 不引入 PyInstaller。**

理由: 规格的首要约束是"可重复构建"与"不破坏开发安装", 这两条 source distribution
都天然满足, 而 PyInstaller 两条都更差; 同时"一键启动"的目标已经由
`scripts/start.bat` / `stop.bat` / `health.bat` + 便携解释器
（`Python/pythoncore-3.14-64/python.exe`）达成 —— 用户仍然是"双击一下就起服务"。

需要说明的是: **PyInstaller 不是被否定, 而是被推迟。** 它会引入一个必须在每次代码
变更后重建的构建产物, 却不提供启动脚本之外的任何能力。将来若确实要发给完全不带
Python 的用户, 再补一条 `scripts/build.py` 式的构建链即可, 不需要改动现有架构。

Test Summary
----------------------------------------

Base (after Task 45): 3542 passed
Task 46 added (tests/test_startup.py): 37 passed
Task 46 + Task 47 全量 (上次会话): 3604 passed, 1 skipped, 20 deselected
Task 47 续做 (追加 9 条在 test_startup.py): +9 passed
Final: 3613 passed, 1 skipped, 20 deselected
Failed: 0

Validation
----------------------------------------

- `pytest tests/test_startup.py` -> `45 passed, 1 skipped` (skip = 本机装了
  faster-whisper, 无法走"缺引擎"分支)
- `pytest -q -m "not integration"` (全量, 本次会话实测) -> `3613 passed, 1 skipped,
  20 deselected, 0 failed` (262s)
- `python -m compileall -q src tests` -> 退出码 0
- **端到端实测 (本次会话补做, 此前只有类级测试)**: 真实执行 `scripts/start.bat`
  → `/` `200 text/html; charset=utf-8`、`/app.js` `200 application/javascript; charset=utf-8`
  (89557B)、`/styles.css` `200 text/css; charset=utf-8` (11566B)、
  `/api/health` `200 application/json; charset=utf-8`、`/api/dashboard` 同上;
  再执行 `scripts/stop.bat` → 退出码 0、输出 `stopped.`、服务进程退出、PID 标记被清理
- `python -m src.application.launcher health` -> 六项全 `[OK  ]`
  (python 3.14.7 / dependencies ok / data directory / database / model availability
  `asr=real ocr=real` / port 127.0.0.1:8765), 快照 `version=0.35.0`,
  `database_schema_version=2`
- `run_tests.cmd tests/test_determinism_audit.py tests/test_dependency_audit.py`
  -> `14 passed`, 退出码 0 (便携解释器路径有效)

Known limitation（明确说明, 不掩饰）
----------------------------------------

1. **`stop` 走 `taskkill /F` 是强杀, 服务端不会走优雅关闭。** 实测 `start.bat`
   被 `stop.bat` 停掉时退出码为 1 (被强制终止), 数据库连接不经过 `close_runtime`。
   SQLite 自身的崩溃恢复能兜住 (WAL + 原子提交), 但"正在进行的写事务"不会被
   主动收尾。这是 Windows 进程模型的取舍, 不是遗漏。
2. **`health` 不是纯只读的。** 它按配置创建 `data_dir` 目录树与数据库文件
   (实测在 `D:\Project\Clases\classroom-data\` 下建出 8 个子目录 + `classroom.sqlite`)。
   这是"装配一遍运行时"的直接结果, 幂等可重建; 但脚本注释只说"不绑定端口",
   没提这一点, 用户若以为它是只读检查会意外发现磁盘上多了目录。
3. **`is_port_free` 只覆盖 IPv4 与 `::1`。** `host` 是其他 IPv6 写法时族选择会错。
4. **浏览器自动打开的端到端路径未做真实浏览器验证** —— 接线由单测盯住
   (`test_main_start_passes_browser_opener`), 但"真的弹出浏览器窗口"依赖用户桌面
   环境, 自动化测试里刻意不触发。
5. **未支持 RFC 5987 `filename*=`** (沿用 Task 40 的既有边界, 与本任务无关)。

## Task 47 — Production Hardening（最终生产硬化）

Goal
----------------------------------------

交付前的最后一轮硬化: 用**自动化审计**替代"人工检查一遍"，把"我们觉得它是
确定的 / 依赖是干净的 / 规模上来不会退化"变成可重复执行的断言。本次落地四类:

- **47.1 全测** —— 全量回归必须绿 (见 Test Summary)
- **47.2 确定性审计** —— 业务身份不得来自 `datetime.now` / `random` / `hash` / `uuid4`
- **47.3 依赖审计** —— 生产 requirements 无重复、无裸版本、无测试专用依赖、无未使用依赖
- **47.4 性能审计** —— 接近真实规模的负载下热路径不得出现 O(N²)

并在审计过程中修掉暴露出来的真实缺陷 (见下)。

设计取舍
----------------------------------------

1. **确定性审计扫 AST 的"调用", 不扫文本。** 用正则搜 `datetime.now` 会把
   注释、docstring、字符串常量里的提及全部算成违规 —— 而这些地方提到它往往
   正是在**说明"为什么不能用"**。因此解析成 AST 后只看 `ast.Call` 节点,
   且用 `ast.unparse` 回报精确位置。

2. **`uuid4` 不是"一律禁止", 而是"白名单 + 语义二次校验"。** 本项目确实有
   合法用途: `__post_init__` 里 `if not self.x:` 的空值兜底、临时音频块文件名。
   所以先按文件白名单收敛, 再对白名单内逐行校验**语义**(必须是
   `if not self.` 守卫, 或含 `tmp_name` / `uuid.uuid4().hex` 的临时名),
   防止"进了白名单就随便用"。

3. **依赖审计要求"每个运行时依赖都在 `src` 里真实被 import"。** 只查"能不能装"
   是不够的 —— 无用依赖会静默增大部署体积并引入漏洞面。`ctranslate2` 是
   `faster-whisper` 的传递依赖, 显式钉版是**有意**的 (避免被拉到不兼容版本),
   因此登记为已知允许项并单测该钉版存在。

4. **性能审计用"规模模型 + 宽松上界", 不用微基准。** 硬编码一个毫秒数在不同
   机器上必然抖动; 而 O(N²) 在 1000 KP / 5000 练习 / 20000 答案的规模下会
   **突破任何宽松上界**。所以规模固定 (spec 47.4 的六个数)、上界宽松
   (如 1000 次单点查询总耗时 ≤ 50ms), 判据是"算法量级错了就红", 不是"比上次快"。

5. **单一知识点查询必须是 O(1)。** 这是唯一按"恒定时间"而非"上界"断言的一项 ——
   索引结构一旦退化成线性扫描, 数据集长大后会成为整个 UI 的瓶颈。

6. **`stop` 不信任 PID 文件。** 操作系统会复用 PID, 服务异常退出后残留的标记
   可能已指向无关程序 (见"发现并修复的真实缺陷"第 1 条)。杀之前必须用端口标记
   握手确认对面是本应用。

交付文件
----------------------------------------

新增:

- `tests/test_determinism_audit.py` — 47.2 确定性审计 (10 条)
- `tests/test_dependency_audit.py` — 47.3 依赖审计 (4 条)
- `tests/test_performance.py` — 47.4 性能审计 (12 条)

修改:

- `src/application/launcher.py` — PID 复用防护: 新增 `read_running_port()` /
  `is_our_service()` / `_marker_points_at_our_service()`, `stop` 分支加身份确认
  与 `--force` 逃生门, `__all__` 与模块文档同步更新
- `tests/test_startup.py` — 追加 9 条 (7 条 PID 复用防护 + 2 条浏览器接线)

发现并修复的真实缺陷
----------------------------------------

**1. `stop` 会误杀 PID 被复用的无关进程。**（本次会话发现并修复 —— 生产安全隐患）

- 复现路径: 用户双击 `start.bat` 起服务 → 直接关掉 cmd 窗口 (服务进程随之死亡,
  但 `data_dir/.run/launcher.pid` 残留) → Windows 把该 PID 复用给别的程序
  (例如用户的 Office) → 用户双击 `stop.bat` → `taskkill /F` 打到无辜进程上。
- 实测确证: 起一个无关的 `time.sleep(300)` 子进程, 把它的 PID 写进
  `launcher.pid`、端口文件指向一个没有服务的端口, 再执行 `stop` ——
  输出 `stopped.`、退出码 **0**、**该无关进程被杀死**。
  也就是说: 命令报告成功, 用户却丢了另一个程序的数据。
- 根因: `stop` 的唯一判据是"PID 文件里的数字" + `tasklist` 查"这个 PID 有没有
  进程" —— 后者只能证明**存在**一个进程, 无法证明**是哪一个**。
- 修复: 杀之前先握手。新增 `is_our_service(host, port)` (请求 `/api/health`,
  递归确认响应里出现 `APPLICATION_NAME`; 通配地址 `0.0.0.0` / `::` 先映射成可
  连接的地址) 与 `_marker_points_at_our_service(config)` (读端口标记 + 握手)。
  确认不了就**只清理陈旧标记并报错**, 不杀; 另加 `stop --force` 作为用户明确
  知情的逃生门 (`--force` 在传给 `cli.parse_cli` 之前摘出, 因为 argparse 不认识它)。
- 修复后同一探针: 退出码 3, stderr 为
  `stale PID file removed: no Classroom Assistant service is listening on
  127.0.0.1:8765. Refusing to kill PID 9200 (that PID may have been reused by
  an unrelated program). Re-run 'stop --force' to kill it anyway.`
  —— 无关进程存活。
- 配套测试 (7 条): 端口标记往返、非法端口值拒绝、无服务时 `is_our_service` 为假、
  真实服务时握手为真、**拒绝误杀**(核心回归)、`--force` 仍能杀、
  **真实服务仍能被正常停掉**(防止身份确认把正常路径也挡掉 —— 这条必须用子进程
  跑服务, 因为 `Launcher.start` 记录的是 `os.getpid()`)。

**2. `start.bat` 承诺的"浏览器自动打开"从未接线。** 详见 Task 46 同名小节
(跨两个任务的缺陷, 归档在发现它的位置)。

测试（35 tests）
----------------------------------------

- **确定性审计 (10)**: `datetime.now/utcnow` 只允许在 `runtime.py`; `random.*`
  调用禁止; 内置 `hash()` 禁止; `uuid4` 白名单 + 逐行语义校验 (兜底守卫 / 临时
  文件名); 非确定性来源 (`datetime.now` / `secrets.token_urlsafe`) 只住在
  `runtime.py`; 5 个业务 ID 的 `uuid4` 兜底语句存在性 (parametrize)
- **依赖审计 (4)**: requirements 可解析、无重名、每项都有版本约束; 生产
  requirements 不含 pytest/pylint/flake8/mypy/coverage/tox/ruff; 每个运行时依赖
  都在 `src` 里真实被 import; `ctranslate2` 钉版存在 (parametrize)
- **性能审计 (12)**: 规模模型自检 (1000 KP / 100 topic / 50 session / 500 student /
  5000 exercise / 20000 answer); 装配耗时上界; **单点查询恒定时间**; 覆盖率分析、
  依赖分析、学习路径、dashboard 聚合、练习列表各自上界; 答案提交吞吐
  (每条均耗时上限); 数据库批量保存 / 加载; 备份+恢复
- **PID 复用防护 (7)**: 见上
- **浏览器接线 (2)**: `start` 子命令必须传真实 opener (monkeypatch `Launcher`
  后断言 `browser_opener is _open_browser` —— 盯"接线"而不是盯行为);
  打不开浏览器时不抛异常

Test Summary
----------------------------------------

Base (after Task 45): 3542 passed
Task 47 added (determinism 10 + dependency 4 + performance 12): 26 passed
Task 47 续做 (PID 复用防护 7 + 浏览器接线 2): +9 passed
Task 46 + 47 全量: 3604 -> 3613 passed, 1 skipped, 20 deselected
Failed: 0

Validation
----------------------------------------

- `pytest tests/test_determinism_audit.py tests/test_dependency_audit.py` -> `14 passed`
- `pytest tests/test_startup.py` -> `45 passed, 1 skipped`
- `pytest -q -m "not integration"` (全量) -> `3611 passed, 1 skipped, 20 deselected,
  0 failed` (260s); 浏览器接线修复后重跑 -> `3613 passed, 1 skipped, 20 deselected, 0 failed`
- `python -m compileall -q src tests` -> 退出码 0
- **PID 复用缺陷**: 修复前探针复现 (无关进程被杀, stop 退出码 0) → 修复后探针
  不再复现 (无关进程存活, stop 退出码 3 且报错明确)
- `test_stop_kills_running_service` -> 通过 (真实子进程服务能被正常停掉, 退出码 0)
- `pytest --collect-only` 计数: startup 44 (含 7 条追加) / determinism 10 /
  dependency 4 / performance 12

Known limitation（明确说明, 不掩饰）
----------------------------------------

1. ~~**Task 47 的完整子项清单不在项目内。**~~ **【已解决 —— 见下方「Task 47（续）」。】**
   本区块写作时, 项目文档里能确证的只有 47.1 全测、47.2 确定性审计、47.3 依赖审计、
   47.4 性能审计 (后三者的编号写在各测试文件的 docstring 里), 以及 **47.10
   「审核安全审计」** (在 Task 39 区块里被点名引用), 当时**没有规格文本可供对照**,
   因此 47.5 – 47.10 都记作"未实现"。后续会话拿到了规格原文
   (`cache/pasted-task34-47.txt`), 已把 **47.5 – 47.14 全部逐条落地** —— 详见下方
   续区块。这条保留在此是为了记录当时的真实状态, 不是当前结论。
2. **确定性审计是静态的。** AST 扫描能证明"代码里没有非确定性来源", 不能证明
   "运行时输出确定"。后者由 Task 45 的两次独立 `AcceptanceHarness.run()` 递归
   diff (`TOTAL DIFFS: 0`) 覆盖 —— 两者互补, 缺一不可。
3. **性能断言是"规模模型 + 宽松上界", 不是真实负载基准。** 它保证"量级没写错",
   不保证"在低配机器上够快"; 也没做内存占用、并发 (本应用是单机单用户顺序处理)
   或长时间稳定性测试。
4. **`stop` 的身份确认依赖服务端能响应 `/api/health`。** 若服务进程卡死 (端口
   不响应) 但进程仍在, `stop` 会拒绝执行并提示 `--force` —— 这是有意的取舍
   (宁可让用户多打一个参数, 也不要误杀), 但确实让"卡死时一键停止"变得不直接。
5. **`is_our_service` 的判据是"响应里出现应用名"。** 一个恰好也返回
   `"application": "Classroom Assistant"` 的其他程序会被误认 —— 在本机单用户
   场景下这不构成实际风险, 但它不是密码学意义的身份证明。
6. **Task 44 的已知硬伤仍然存在**: Course / Material / Evidence / KnowledgePoint
   仍在内存里, 配置驱动了建库/迁移/备份但没有把业务对象映射进 SQLite ⇒
   **重启会丢业务数据**。这属于改写核心引擎, 不在 Task 47 的硬化范围内。

## Task 47（续） — Production Hardening 子项 47.5 – 47.14

Goal
----------------------------------------

上一区块只落地了 47.1 – 47.4（全测 / 确定性 / 依赖 / 性能），并诚实记录了
"47.5 – 47.14 未实现，且规格文本不在项目内"。本次会话拿到了规格原文
（`cache/pasted-task34-47.txt`），因此把剩下十个子项**逐条**落地：

- **47.5 File Safety Audit** —— 路径穿越 / 符号链接 / 超大文件 / 非法扩展名 /
  畸形 PDF·DOCX·图片·音频 / 临时文件清理
- **47.6 Database Safety Audit** —— 意外关闭 / 部分写入 / 回滚 / 备份 / 恢复 /
  迁移 / 损坏库 / 重复记录
- **47.7 Processing Safety** —— 模型缺失 / 坏媒体 / 磁盘满 / 超时 / 可重试 vs 不可重试
- **47.8 UI Audit** —— 空态 / 加载态 / 错误态 / 长文本 / 三语 / 大数据量 / 窄视口
- **47.9 Evidence Traceability Audit** —— 随机抽 50 个知识点走通四级追溯链
- **47.10 Review Safety Audit** —— CONFLICTED 绝不因 API / UI / 处理 / 重启 / 重载而自动变 CONFIRMED
- **47.11 Student State Safety Audit** —— 学生答案不得改写知识点真值
- **47.12 Backup Restore Drill** —— 完整破坏-恢复演练
- **47.13 Documentation Finalization** —— README + 8 份 docs
- **47.14 Final Repository Hygiene** —— 清理本阶段探针 / 补丁 / 调试产物

设计取舍
----------------------------------------

1. **硬化测试盯"真实入口"，不盯被注入的实现。** 本项目反复出现"能力存在但未接线"
   （约 17 例），单测全绿而产品没有该功能。因此 47.5 – 47.12 一律走 `Workspace` /
   `AcceptanceHarness` 这些真实入口，而不是直接调被注入的 mock。一个只测"传了回调
   时行为正确"的测试，永远发现不了"装配层根本没传回调"。

2. **"崩溃"必须真的崩溃，不能用受控回滚代替。** 受控 `rollback()` 让 Python 与
   SQLite 从容收尾；硬杀（`TerminateProcess` / `SIGKILL`）才依赖 WAL 恢复重放磁盘
   上的文件。两者是**不同的失败模式**，只测前者会留下后者完全未验证。所以 47.6
   用真子进程 + 真硬杀。

3. **子进程握手用标记文件，不用 stdout。** Windows 上管道 + 阻塞 `readline` 有挂死
   风险 —— 而"挂死"恰恰是本文件要抓的那类 bug。标记文件无此风险，且"子进程提前
   退出"能被明确诊断为"启动失败"而不是超时。

4. **故意断言"坏行为"来证明机制是承重的。** `TestPartialWrite` 里有一条**故意**
   断言"无事务批量写会留下半写"，与"有事务时什么都不写"形成对照。否则"事务保护了
   原子性"只是注释里的一句话，不是被测过的事实。

5. **47.8 的文案用两层 key，整句以中文原文为 key。** 整句话没有"名字"，硬起符号
   key 只会多一层需要同步维护的间接层。用原文当 key 之后，中文模式按构造不变
   （缺 key 就原样返回），而**漏译变成可测的**。

6. **跨语言泄漏检测靠"夹具全 ASCII"这一性质。** es / ca 渲染结果里出现任何 CJK，
   只可能来自未翻译的界面文案 —— 零误报，不需要维护"哪些中文是合法的"白名单。

7. **47.12 的缺口钉成断言，不假装通过。** spec 要求恢复 courses / knowledge /
   students / answers / evaluations / study plans / learning paths；当前架构下
   **这些业务对象从未落盘**，所以"恢复"无物可恢复。与其把这句话写在文档里等它过期，
   不如写成 `TestDrillGap` —— 将来持久化真接线了，这 6 条会失败，强制我们回来更新结论。

8. **47.14 只删本阶段产物。** 规格原文明确要求"不要删除历史文件，除非明确属于本
   阶段且确认不再需要"。因此保留全部规格文本（`pasted-task34-47.txt` /
   `longrun_spec.txt` / `_spec_full.txt` 等）与测试实际引用的资源
   （`scripts/ui_audit.js` / `ui_render_check.js`），只删探针、补丁、调试、转码、
   基线快照与 0 字节垃圾。

交付文件
----------------------------------------

新增:

- `tests/test_hardening_database_safety.py` — 47.6（19 条）
- `scripts/ui_audit.js` — 47.8 UI 审计 harness（93 项检查）
- `README.md` — 47.13 项目入口文档
- `docs/getting_started.md` — 47.13 上手
- `docs/user_guide.md` — 47.13 用户指南
- `docs/configuration.md` — 47.13 配置参考
- `docs/troubleshooting.md` — 47.13 故障排查
- `docs/data_model.md` — 47.13 数据模型
- `docs/backup_restore.md` — 47.13 备份与恢复

修改:

- `src/persistence/database.py` — `backup_to()` 在事务开启时**拒绝执行**（真实缺陷 #16）
- `src/web/app.js` — 两层 `t()` + `TRANSLATIONS`（171 es + 171 ca）；新增 `showLoading()`
- `src/web/styles.css` — 长 token 折行 + 窄视口断点
- `tests/test_exercise_ui.py` — i18n 断言改为两层；新增 `TestUiAuditHarness`（3 条）
- `tests/test_learning_view.py` — 同一处 i18n 断言同步改为两层（47.8 的**第二份**副本）
- `docs/architecture.md` — 新增第 16–20 章（持久化 / 备份 / 配置与启动 / UI 与 i18n / 生产硬化）
- `docs/status.md` — 本区块

发现并修复的真实缺陷
----------------------------------------

**#16 `Database.backup_to()` 在事务开启时永久挂死。**（47.6 发现并修复）

- 现象: 探针里 `db.begin()` 之后调 `backup_to()`，打印了 `calling backup_to ...`
  就**再也不返回** —— 不是慢，是永久挂死，连 shell 的 `timeout 30` 都得来杀进程。
- 边界实测: 无事务 → 备份正常；只要 `db.begin()` 开着（**零写入**）→ 挂死。
  所以判据不是"有没有脏数据"，而是"这个连接有没有持有写事务"。
- 根因: 在线备份 API 需要拿读锁，而该连接自己的写事务正握着写锁 ⇒ 自锁等待。
- 生产路径**未受影响**（`BackupService._snapshot_database` 另开一个 `Database`
  实例），但 `backup_to` 是公开 API: 任何未来的调用者都会让本地 HTTP 服务带着写锁
  卡住一个永不返回的请求，后面所有请求排队。
- 修复: 无条件**拒绝**，并且**绝不**替调用方 commit。替调用方提交比挂死更糟 ——
  它会把调用方未提交的工作在调用方没选择的时点变成已提交。
- 回归测试: `TestBackupSafety::test_backup_refuses_while_a_transaction_is_open`
  —— 断言抛 `TransactionError` 且 `code == "STORAGE_TRANSACTION_FAILED"`，
  回滚后备份成功且内容正确。

**#17 语言选择器对 11 个页面里的 7 个完全无效。**（47.8 发现并修复）

- 现象: 选 `Español` / `Català` 只换掉顶栏，Task 39 的 7 个页面（概览 / 知识点 /
  材料 / 待审核 / 课程 / 课堂 / 溯源详情）正文仍是中文。
- 量化: `I18N` 表之外存在 **193 个不同的硬编码中文字面量**。
- 修复: 新增 `TRANSLATIONS`（171 es + 171 ca），并用一次性 codemod
  （`cache/_i18n_apply.py`）把含中文的字符串字面量按 HTML 标记边界切开、只对含中文
  的文本段包 `t('…')` —— **共改写 207 处字面量、170 个 key**。中文从不手抄，
  因此不存在转写错误。
- 无副作用证明: 改动前抓一份中文渲染快照（16991 B），codemod 后再抓一次、译文表
  写入后再抓一次 —— **两次都逐字节相同**。
- 回归测试: `test_sentence_keys_are_translated_in_both_languages` /
  `test_translation_tables_have_no_orphan_entries` /
  `test_translations_never_contain_chinese`，加上 `scripts/ui_audit.js` 的三语渲染
  + CJK 泄漏检查。
- 附带发现: 同一处 i18n 断言在 `tests/test_exercise_ui.py` 与
  `tests/test_learning_view.py` 里**各有一份**。修复第一份后全量回归才暴露出第二份
  仍然失败 —— 说明"改了一处机制就要全仓搜同一断言"，不能只看当前文件。

测试（70 tests + 93 Node checks）
----------------------------------------

- **47.5 File Safety (11)**: 路径穿越文件名不逃出 data_dir；符号链接源不逃出；
  超大文件拒绝；非法扩展名拒绝；零字节拒绝；畸形 PDF / DOCX、坏图片、坏音频一律
  **报错而不崩**；失败材料不得渗进知识点；临时文件被清理
- **47.6 Database Safety (19)**: 见上，真子进程 + 真硬杀；已提交存活 / 未提交消失；
  硬杀后库健康（`integrity_check() == "ok"`、无外键违规、`schema_version()` 为最新）；
  可再次写入；恢复在 3 次重开中稳定；跨表成对写入不留半写；迁移中崩溃回滚该迁移；
  批量原子性（有事务 0 写入 / 无事务半写 —— 故意断言坏行为）；重复键重存更新而不
  累积；UNIQUE 冲突抛 `DuplicateRecordError`；备份 5 条边界（事务中拒绝 / 拒绝覆盖
  已存在目标 / 拒绝已关闭库 / 拒绝内存库 / 恢复副本完整健康）；"库已最新时
  `migrate()` 不写盘"（用 `mtime` + `-wal` 大小验证）
- **47.7 Processing Safety (7)**: 解析失败是**不可重试**；不可重试材料重试被拒；
  拷贝时磁盘满被上报；处理超时被上报；Mock 引擎**绝不**被标成 real；ASR 错误码
  携带正确的可重试性；缺模型由环境自检上报
- **47.8 UI Audit (93 Node checks + 3 pytest)**: 11 个页面 × zh / es / ca 渲染；
  es / ca 无未翻译 CJK；空态显式；错误态显示错误码 / 消息 / 返回路径且**不含栈回溯**；
  未知路由渲染 not-found；路由切换先显示加载态；400 字符无空格长 token 与 400 字符
  哈希原样渲染不截断；CSS 允许长 token 折行；大数据量（1000 KP / 500 students /
  5000 exercises）10 秒内渲染完且**请求数有上界**（抓 N+1）；窄视口断点收起侧栏；
  布局无固定 px 宽；表格可横向滚动；三语 key 对齐且无空译
- **47.9 Evidence Traceability (6)**: 放大数据集（80 段板书 → **90 个知识点**）跑完
  整流水线；抽样下限 50；**随机抽 50 个**（`random.Random(47)`，可复现）逐条走通
  KP → Evidence → Material → source location；全量检查"无证据的知识点 = 编造"；
  把 Task 44 的重启局限钉成断言；证据来源覆盖多种材料类型
- **47.10 Review Safety (8)**: 夹具确实含冲突；CONFLICTED 初始为 pending 而非
  confirmed；经 API 读取不确认；对冲突点直接 confirm 被拒；重新处理不确认；
  **重启不确认**；只有显式 ReviewRecord 才确认；每个 confirmed 点都有 ReviewRecord
- **47.11 Student State Safety (5)**: 正确 / 错误回答都不改写知识点真值；反复错答
  **永不**累积成真值；错误回答只影响学习状态；学习状态按学生隔离
- **47.12 Backup Restore Drill (14)**: 8 条"现在就能恢复" —— 归档先校验后恢复、
  恢复无警告、恢复后库文件存在且可读一致、每份材料逐字节存活、材料注册表逐条一致、
  归档默认落在 data_dir 内、材料文件数与 manifest 相符；6 条 `TestDrillGap` 钉住
  spec 要求但当前**不满足**的部分（见 Known limitation 第 1 条）

Test Summary
----------------------------------------

Base (after Task 46 + Task 47 前四项): 3613 passed
Task 47.5–47.12 新增 pytest: +70
  （input_safety 18 = 47.5 的 11 + 47.7 的 7 / database_safety 19 /
   traceability 6 / truth_safety 13 = 47.10 的 8 + 47.11 的 5 / backup_drill 14）
Task 47.8 追加 pytest (TestUiAuditHarness): +3
Task 47.8 修复 i18n 时 test_learning_view 新增 2 条: +2
全量回归: 3688 passed (1 failed) -> 3691 passed, 1 skipped, 20 deselected
Failed: 0

Validation
----------------------------------------

- `pytest -q -m "not integration"`（全量，本区块最终实测）→
  `3691 passed, 1 skipped, 20 deselected, 0 failed`（553s）
- **47.14 清理后复跑全量** → `3691 passed, 1 skipped, 20 deselected, 0 failed`（493s）
  —— 与清理前**逐项相同**, 证明删掉的 103 个文件 + 21 个探针目录没有被任何测试引用
- `node scripts/ui_audit.js` → `UI audit OK (93 checks)`; `node scripts/ui_render_check.js`
  → `UI RENDER CHECK: OK (32 checks)`（两者都在清理后复验通过）
- `pytest tests/test_hardening_database_safety.py` → `19 passed`（2.59s）
- `pytest tests/test_learning_view.py` → `143 passed`（42.30s）
- `node scripts/ui_audit.js` → `UI audit OK`（93 checks）
- **缺陷 #16 前后对照**: 修复前 `db.begin()` + `backup_to()` 永久挂死（需外部
  timeout 杀进程）→ 修复后抛 `TransactionError` / `STORAGE_TRANSACTION_FAILED`，
  回滚后备份成功
- **缺陷 #17 前后对照**: `node scripts/ui_audit.js --dump es` 修复前有 14 处未翻译
  CJK 页面 → 修复后 0 处；中文渲染快照在改动前后**逐字节相同**（16991 B）
- `python -m compileall -q src tests` → 退出码 0

Known limitation（明确说明, 不掩饰）
----------------------------------------

1. **47.12 的完整目标未达成 —— 这是当前架构的真实缺口，不是测试没写。** spec 要求
   恢复 courses / sessions / materials / evidence / knowledge / review history /
   students / answers / evaluations / study plans / learning paths。实测: 归档里
   **材料文件确实在**（逐字节存活），但 Course / KnowledgePoint / ReviewRecord /
   Student / Answer / Evaluation / StudyPlan / LearningPath **从未落盘** —— 它们只
   活在内存的 Application Service 里（Task 44 的 Known limitation 第 13 条）。所以
   "destroy working database"这一步在当前架构下**无物可毁**（`data_dir` 里只有材料
   文件 + 材料注册表 JSON，数据库是 `BackupService` 生成归档时才创建的）。症状很
   具体: 恢复后材料文件在磁盘上，但通过 API **读不到**，因为
   `list_materials(course_id)` 要先解析课程上下文，而课程注册表是空的。补齐它等于
   改写核心引擎（把业务对象映射进 SQLite），不在硬化任务范围内 —— 已由
   `TestDrillGap` 6 条断言钉住，接线后会自动变红。
2. **47.8 的 UI 审计不是浏览器测试。** `scripts/ui_audit.js` 在 Node + 最小 DOM 桩里
   用 `vm.runInContext` **真实执行**页面函数，覆盖状态与文案；但它不经过真实渲染
   引擎，不验证布局像素、真实事件、焦点与滚动。项目里**没有**浏览器端到端测试。
3. **47.6 的"磁盘满"与"处理超时"是注入模拟，不是真实资源耗尽。** 真实 ENOSPC 与
   真实超时依赖操作系统状态，无法在单元测试里稳定复现；这两条验证的是**错误被正确
   分类与上报**，不是内核行为。
4. **47.6 的崩溃测试在 Windows 上验证的是 `TerminateProcess` 语义。** POSIX 上的
   `SIGKILL`、断电、文件系统损坏不在覆盖范围内 —— 覆盖的是 WAL 恢复路径，不是所有
   可能的存储故障。
5. **47.9 的追溯链是"进程内"的。** 每条证据都带 `material_id` + source location，
   链本身完整；但跨进程重启后无法复现（同第 1 条），已用
   `test_reload_limitation_is_pinned_not_silent` 钉住。
6. **47.9 的抽样是"确定性随机"。** 用固定种子 `random.Random(47)` 满足"随机抽取"
   且可复现；但它不是每次运行都换样本 —— 固定种子能复现失败，代价是长期只覆盖同一
   组 50 个知识点。
7. **`health` / `--check` 不是只读的。** 它们会按配置创建 `data_dir` 目录树与数据库
   文件。已在 `docs/troubleshooting.md` 与 Task 46 的 Known limitation 里写明，未在
   本次硬化中改变行为。
8. **备份没有 HTTP 端点，也没有 CLI 入口。** 恢复是一个会替换数据库的高危操作，只以
   Python API（`create_backup` / `list_backups` / `restore_backup`）暴露。
   `docs/getting_started.md` 初稿曾写成 `curl -X POST /api/backups`，经核对
   `src/api/endpoints.py`（53 条路由，无备份路由）与 CLI 后**已改正**。

### 47.14 Final Repository Hygiene —— 实际清理清单

删除（本阶段产生的探针 / 补丁 / 调试 / 转码 / 基线快照，共 **104** 个文件 + 21 个目录）:

- `cache/` — `_probe*.py`(50) / `probe*.py`(10) / `patch*.py`(6) / `_dbg.py` /
  `_depgraph.py` / `_apidump.txt` / `_bigds_probe.py` / `_dbsafety_probe.py` /
  `_drill_probe.py` / `_e2e_startup.py` / `_pid_reuse_probe.py` / `_safety_probe.py` /
  `_truth_probe.py` / `_i18n_apply.py` / `_i18n_translate.py` / `_i18n_keys.txt` /
  `_ui_baseline_zh.txt` / `_app_before_i18n.js` / `task46_block.md` / `task47_block.md` /
  `regress40.txt` / `r34.py` / `dump34.py` / `gen.py` / `build_ks.py` / `build_full.py` /
  `ks_gen.py` / `ks_content_gen.py` / `read_attach.py` / `read_task34.py` /
  `write_ks.py` / 以及 `_errors_b64.txt` / `pasted-full.txt` / `ks_content.b64` 三个
  0 字节文件
- `temp/` — 全部 21 个 `_*` 探针目录（`_accprobe` / `_badmedia*` / `_bigacc` /
  `_bigds` / `_dbp`~`_dbp4` / `_dbsafety_probe` / `_drill_*` / `_pidtest` /
  `_pre_review` / `_safety_probe` / `_sp2` / `_sp3` / `_student_probe` / `_tree` /
  `_tsprobe`）
- 仓库根 — 一个 0 字节垃圾文件与 `classroom-data/`。前者值得记一笔: 它的文件名是
  `固定文案、\uf00a     语言不匹配` —— 分隔符**不是空格, 而是私用区字符 U+F00A**
  （终端里显示成换行, 所以第一次按"换行 + 空格"去删**没删掉**, 用 `repr()` 打出真实
  文件名才删成功）。这是一次 shell 重定向失误的产物。`classroom-data/` 是 `health`
  自检的副产物: 实测 28 张表里只有 `schema_version` 有 2 行, **无任何业务数据**。

保留（明确判定为历史文件或测试依赖）:

- 规格文本: `cache/pasted-task34-47.txt`、`cache/pasted-task34-47-raw.bin`、
  `cache/longrun_spec.txt`、`cache/_spec_full.txt`、`cache/_spec_t34.py`、
  `cache/_t34_utf8.txt`
- 测试实际引用的 harness: `scripts/ui_audit.js`（`test_exercise_ui.py` 3 条测试依赖）、
  `scripts/ui_render_check.js`（3 条测试依赖）
- 历史生成脚本: `scripts/gen.py` / `fix.py` / `mod.py` / `gen_task21_fixtures.py` 等
  （属更早任务，非本阶段产物）、`scripts/_tiny.png`（被 `gen_task21_fixtures.py` 引用）
- 仓库既有数据目录骨架: `audio/` `backups/` `database/` `documents/` `images/`
  `materials/` `logs/`（均为空目录，属仓库结构而非本阶段产物）

### 47 终审补充 —— 规格第六 / 九节收尾（Definition of Done 核对 + Final Report）

规格 2844 行里 **Task 47 之后还有 6 节**（四 最终架构目标 / 五 最终数据流 /
六 最终 Definition of Done / 七 用户体验目标 / 八 执行顺序 / **九 最终报告格式**）。
其中第九节是硬性交付物："完成全部 Task 后必须输出"一份固定格式的
`Productization Phase 34–47 Final Report`。已产出 `docs/final_report.md`。

第六节要求逐条核对 DoD，其中 Technical 一项是字面意义的
**`pytest -q → 0 failures`** —— **不带** `-m "not integration"`，即 20 条真实
Whisper / OCR 集成测试也要一起跑。已单独执行并记录（见下）。

#### 终审又发现 2 个真实缺陷（都已修 + 加守卫）

**#18 测试夹具用内置 `hash()` 造 id ⇒ 偶发失败。**

- 现象: `pytest -q -m "not integration"` 出现 1 failed ——
  `tests/test_evidence_store.py::TestLargeStore::test_10k_inserts_dedups_and_queries`
  在 `assertEqual(result.added, 5000)` 失败；但同一次会话的完整 `pytest -q` 通过，
  单独跑该测试 **40 个随机 `PYTHONHASHSEED` 全绿** —— 典型的顺序 / 环境相关 flake。
- 根因（**不在产品代码，在测试夹具**）: `make_evidence` 用
  `abs(hash(content + material_id + str(page))) % 10**10` 造 `evidence_id`。
  内置 `hash()` 对 `str` 按进程随机化（`PYTHONHASHSEED`），而 10^10 个桶装 5000 个
  条目约有 **0.125%** 概率撞 id；撞上时 `EvidenceStore` 把"同一 `evidence_id` 但
  规范化身份不同"的后一条 **REJECTED**，于是 `added < 5000`。
- 修复: 改用 `hashlib.sha256` 截 128 位（碰撞概率可当 0，且跨进程稳定）。
- 验证: 6 个不同种子下 id 序列摘要**完全一致**、5000 个 id 零碰撞；
  30 个随机种子复跑 **0 失败**。
- **为什么以前没被发现**: `tests/test_determinism_audit.py` 明确禁止内置 `hash()`，
  但它只扫 `src/`，**从不扫 `tests/`** —— 那条守卫一直以为自己是绿的。
  已把该禁令扩展到 `tests/`（**只扩展 `hash()`**；`random.Random(47)` 这类固定种子
  抽样是有意的，不能一起禁）。新增 `test_no_builtin_hash_call_in_tests`。

**#19 三个 `.py` 文件带 UTF-8 BOM。**

- 上面那条新审计**一上线就顶出来了**: `tests/test_ocr_processor.py`
  （另有 `scripts/build_models.py`、`scripts/test.py`）。
- 危害不是洁癖: 带 BOM 的文件用 `encoding="utf-8"` 读出来会以 U+FEFF 开头，
  `ast.parse` 直接报 `SyntaxError: invalid non-printable character U+FEFF` ——
  **编码问题伪装成语法错误**，极难定位。
- 修复: 三个文件存成无 BOM 的 UTF-8；新增 `test_no_utf8_bom`（覆盖 `src/` 与
  `tests/`）；审计的 `_parse()` 改用 `encoding="utf-8-sig"` 读，避免同类失败再以
  "语法错误"的面目出现。

#### 另两处口径修正

- `src/api/endpoints.py` 的路由数是 **53**（38 GET + 14 POST + 1 PATCH），此前文档
  误写成 52。已修正 `docs/architecture.md`、本文件与技能文档。
- `src/__init__.py` / `tests/__init__.py` 里写死的 `__version__ = "2.0.0"` 与产品实际
  版本（`APPLICATION_VERSION = "0.35.0"`）**矛盾且从未被任何代码使用**。已对齐，并
  新增 `tests/test_bootstrap.py::TestVersionMetadata`（3 条）钉住一致性 —— 版本号仍是
  三处，但没有单一来源，这一点记在 `docs/final_report.md` 的 Known Limitations 第 12 条。

#### Test Summary（终审后最终数字）

```
pytest -q                     3716 passed, 1 skipped, 0 failed    (278s)   ← DoD 字面要求
pytest -q -m "not integration" 3696 passed, 1 skipped, 20 deselected, 0 failed  (244s)
compileall                    退出码 0
node scripts/ui_audit.js      UI audit OK (93 checks)
```

#### Final Verdict

**Beta Ready**（不是 Production Ready）。理由与完整论证见 `docs/final_report.md`。
一句话: 所有测试都绿、Task 34–47 全部落地，但**业务对象从未落盘 ⇒ 重启丢全部业务
数据**（本文件 Known limitation 第 1 条），所以"测试全绿"不等于"可以上线"。

## Tasks 48–52 — Persistence Wiring（业务对象真正落盘 / 重启恢复）

### 这一阶段修的是什么

Task 42 把 `src/persistence/` 建好了（14 个仓储、385 条测试），Task 44 也
按配置打开并迁移了数据库。但**业务对象从来没有被写进那些表**：

```
创建课程 -> 内存 -> 关闭程序 -> 数据消失
```

`src/application/persistence_wiring.py`（新文件）就是那条缺失的接线。它是
应用层里**除组合根 `bootstrap.py` 之外唯一**允许 `import src.persistence` 的
文件，白名单由 `tests/test_persistence_layering.py` 逐文件枚举并全等断言 ——
新增业务服务一个都不许碰存储层。

写策略是**写穿 (write-through)**：每个业务写操作在返回前就落盘，而不是攒到
`close()`。进程随时可能被强杀（Task 54 的 crash harness 就是干这个的），
"退出时统一保存"会静默丢数据。

### 落盘顺序（不是随意排的）

`_flush_processing` 的顺序是 **证据 → 材料 → 知识 → 组织 → 学习**：

- 知识点引用证据，组织层的课堂归属引用知识点，学习层的作答引用练习与知识点；
- 而 `material_evidence` 关系表对 `evidence` 有**外键**。

按依赖顺序写，外键在每一步都是可满足的，因此提交时不会被数据库拒绝 —— 不需要
推迟外键检查。这个顺序是踩出来的：早期版本先写材料，`material_evidence` 的
外键直接报错。

### 各 Task 验收

| Task | 内容 | 测试 |
| --- | --- | --- |
| 48 | Persistent Workspace Bootstrap | `tests/test_persistence_workspace.py` — 29 |
| 49 | Course / Session / Material | `tests/test_persistence_courses.py` — 59 |
| 50 | Evidence / Knowledge | `tests/test_persistence_knowledge.py` — 96 |
| 51 | Review / Student / Exercise | `tests/test_persistence_learning.py` — 102 |
| 52 | StudyPlan / LearningPath / Coverage | `tests/test_persistence_plans.py` — 60 |

**Task 48** 钉住的是生命周期与"绝不静默新建空库"：打开顺序必须是
open → migrate → load state；文件存在但不是 SQLite 库时抛 `StorageError`
且**原文件一个字节都不动**（`test_persistence_workspace.py` 同时断言异常与
磁盘上的原始字节）。`close()` 幂等。

**Task 49** 钉住 Course / Session / Material 的写穿与可移植性：
- `Session` 引用未知课程 → `StorageError`（不是静默的幽灵课堂）；
- 材料记录全字段溯源往返，`content_hash` / `material_id` 在重新解析路径时
  **绝不重算**（改了身份会打断所有指向它的证据引用）；
- 重复注册同一材料只留 1 行；
- 文件被手工删除 → 记录**保留**，`material_integrity()` 报
  `MATERIAL_FILE_MISSING`，`health()` 变 `degraded`，二次重启仍然报；
- `data_dir` 搬迁到别处后，`stored_path` 必须**重解析**（见下面缺陷 #2）。

**Task 50** 钉住证据链：Evidence 全字段 + 显式 `insertion_seq`（Task 23 的
查询依赖插入顺序，而 SQLite 不保证无 `ORDER BY` 的行序）；90 个知识点的
50 条抽样溯源在重启前后各验一遍；损坏 payload 在打开时就抛；
课程隔离按**证据归属**而不是 `knowledge_points.course_id` 列。

**Task 51** 钉住真值安全与幂等：答错**不改变** `KnowledgePoint.statement` /
`Evidence.content` / `ReviewRecord`（内存 + SQLite 原始 payload + 重启三重
验证）；1x/2x/3x 重复提交不膨胀；评审历史 append-only、不被压平。

**Task 52** 钉住 stored / derived 的判定（见下）。

### Task 52 的判定：什么存，什么不存

规则是：**如果它是别的已存储数据的纯函数，就重新算。**

| 对象 | 性质 | 依据 |
| --- | --- | --- |
| `Course` / `ClassSession` | Stored | 用户输入，没有别的东西能推出它们 |
| 材料注册表记录 | Stored | 用户输入 + 指向存储文件的指针 |
| `Evidence` / `KnowledgePoint` / `ConflictRecord` | Stored | 知识真值，溯源住在这里 |
| `ReviewRecord` | Stored，append-only | 人的决定是审计历史 |
| `Student` / 学习日志 / `StudentAnswer` / `EvaluationResult` | Stored | 过程数据，推不出来 |
| `Exercise` | Stored | 作者写的内容，含答案 |
| `StudyPlan` — **值** | **Derived** | 知识结构 + 学生学习日志的纯函数 |
| `StudyPlan` — **快照行** | **Stored**，append-only | 审计："当时服务了哪份计划" |
| `LearningPath` | **Derived** | 依赖图 + 目标知识点的纯函数 |
| `Coverage` / `Gaps` / `Dependencies` | **Derived** | 知识结构的纯聚合 |
| `StudentDashboard` | **Derived** | 上述结果的投影 |

`StudyPlan` 是唯一"值"与"落盘表示"性质**不同**的对象，所以占两行。
把它写成一个 "STORED" 会误导读者以为 `study_plan()` 在读缓存 —— 它没有，
也不该：一份在摄取新教材之前拍下的快照描述的是一个**已经不存在了的世界**。

- `learning_paths` 表在正常运行中**保持为空**。仓储与 `save_learning_paths()`
  都在且被直接测过，但没有任何产品路径把派生值写进持久存储 —— 有人"好心"
  加缓存的话，记录这条决定的测试会立刻变红。
- `study_plans` 行**永不删除**，也**永不读回**。内容寻址让归档**可核对**
  而非仅"看起来合理"：从同一状态重算必然复现同一 `plan_id`，所以归档行要么
  与重算一致，要么可证伪为过期。这正是审计价值所在，也是 id 里不许出现时钟
  或计数器的原因。
- 篡改一条已存的 `study_plans` payload **改不了**产品给出的计划；写穿顺带
  把那一行修回正确内容（同一 `plan_id` 的 upsert），无需人工修复。
- 一句话规则：**数据库存输入与审计历史，不存答案。**

判定写进了 `docs/architecture.md` 第 21 节，并由
`tests/test_persistence_plans.py` 直接断言文档文本 —— 文档漂移和代码漂移
一样是真实的维护成本。

### 顺手发现的 5 个真实缺陷（都已修 + 加守卫）

**#20 `exercise_evidence` 外键把合法的"悬空证据引用"变成写入失败。**

- 现象：全量回归 1 failed —— `test_exercise_ui.py::test_view_reports_broken_evidence_instead_of_hiding_it`，
  `FOREIGN KEY constraint failed`。
- 根因：**领域层刻意允许**练习引用不存在的证据（UI 要如实报
  `unresolved_evidence_ids`）。`ExerciseRepository.save` 的关系表 `replace()`
  把这份合法的领域状态变成了数据库层的拒绝 —— 持久化层偷偷收紧了领域语义。
- 修复：`LinkRepository.replace()` 新增 `only_existing=(表, 列)` 开关与
  `_split_existing()`：关系表是**查询索引**，不得比 source of truth
  （payload）更严格。权威引用列表完整留在 payload 里。
- 修复后 `tests/test_exercise_ui.py` 88 passed。

**#21 判断题**根本无法从数据库读回来**（潜伏 10 个 task）。**

- 现象：`Exercise.from_dict` 报 `exercise_id does not match content (corrupted payload)`。
- 根因：自动生成的判断题选项是 `(true, false)`，而显式传入的选项经
  `_validate_choices` 排序成 `(false, true)` → 两条路径算出**两个不同的
  `exercise_id`**。
- 修复：把**身份**与**展示**分开 —— 身份用规范化（排序）选项列表，
  展示保留作者顺序（判断题 `true` 在前、选择题 A/B/C/D）。两个性质都必需，
  且方向相反。
- **为什么以前没被发现**：练习从来没有真正落盘再读回。10 个 task 里所有
  练习测试都在内存里跑，往返缺陷无从暴露。

**#22 `data_dir` 搬迁后材料 `stored_path` 陈旧。**

- 根因：`_load_registry()` 先加载 JSON 注册表（里面的绝对路径已失效），
  `restore_records()` 因 `key in self._materials` 直接 `continue`，从不修正。
- 危害：把备份恢复到别的目录后，校验与摄取会**假报** `STORAGE_ERROR` ——
  文件明明在，产品说不在。
- 修复：新增 `resolve_material_path()`（优先 `relative_path`，回退
  `stored_path`）与 `_adopt_record()`；`restore_records()` 对已存在记录也
  **刷新**存储位置。`material_id` / `content_hash` 绝不重算。

**#23 `list_sessions` 内存与磁盘两种排序。**

- 根因：内存按 `session_id`（哈希）排，`tables.py` 的 `order_by` 按
  `(course_id, session_number)` 排。同一份数据在重启前后顺序不同。
- 修复：内存排序键改为 `(course_id, int(session_number), session_id)`，与
  存储层完全一致。

**#24 `submit_answer` 重复提交让计数膨胀（真实幂等缺陷）。**

- 根因：学习日志登记与 planner 快照计数没有"这条答案是不是新的"守卫。
- 修复：`is_new_answer = answer.answer_id not in self._evaluation_by_answer`，
  登记与计数限制在 `if is_new_answer:` 内。

### 一处**口径修正**（不是缺陷，是文档在说谎）

Task 52 的第一版测试里有一条断言"`StudyPlan` 必须从库里读回来"。它**失败了**，
而且失败得很有价值：产品是**重算**的。

追下去发现是**文档错了**：`docs/architecture.md` 21.2 把 `StudyPlan` 写成
"**Stored**, immutable snapshot"，混淆了"值"与"落盘表示"。

- 值必须重算：一份在新教材摄取之前拍下的快照描述的是已经不存在了的世界；
  读回它会向学生**隐藏变化**。
- 行是 stored：append-only 的"当时服务了哪份计划"审计记录，只被**核对**，
  不被读。

修正的是文档与那条测试的断言方向，**不是**把测试删掉 —— 那条测试现在守的是
"篡改历史快照改不了答案"，比原来那条更强。

### Test Summary（本阶段收口时）

```
pytest -q -p no:cacheprovider   4068 passed, 1 skipped, 0 failed   (341s)
pytest tests/test_persistence_*  507 passed  (workspace/courses/knowledge/learning/plans/layering/repositories)
compileall src tests             退出码 0
```

新增测试 346 条（29 + 59 + 96 + 102 + 60），全部为本阶段新增，无一条被删除或
`skip`。

## Task 53 — Transactional Application Operations（事务化业务操作）

### 为什么"写穿"还不够

Task 48–52 让每个成功的业务操作立刻落盘。但**落盘不等于原子**: 一个操作
往往要写五张表 —— ``process_material`` 依次写证据、材料、知识点、组织、
学习 —— 如果第 4 步失败, 前 3 步已经提交的话, 数据库里就留下了一个
**半成品**: 有证据但没有知识点, 或者有知识点但组织层不知道它们属于哪节课。

用户看到的是一个"看起来正常但内容缺失"的工作区, 而且**没有任何错误可追**。
这比直接报错糟得多。

### 做法: 一次业务操作 = 一个事务

`Workspace` 的每个写方法都跑在 `WorkspacePersistence.atomic()` 里:

```python
def create_session(self, course_id, ...):
    with self._atomic():              # -> persistence.atomic() -> BEGIN IMMEDIATE
        dto = self.course_service.create_session(...)
        self._register_course_sessions(ctx)
        self._flush_course(dto["course_id"])
        self._flush_organization(dto["course_id"])
    return dto
```

- 要么全成, 要么一行都不动。半成品不是"不太可能", 而是**不可表示**。
- 失败时异常**原样向上传播**:
  - `PersistenceError` -> 结构化 `StorageError` (8 码规范之一), 底层异常挂在
    `cause` 上供日志使用;
  - 领域错误保持自己的错误码 (不许被改写成 `STORAGE_ERROR`);
  - 其他异常 (含 `KeyboardInterrupt` / `SystemExit`) 原样重抛。
  绝不吞掉。
- 嵌套安全 (`SAVEPOINT`), 所以 `save_material_records` 保留自己的内层事务,
  单独调用时仍然原子。

### 边界在哪 (明确写下来)

`WorkspacePersistence.save_*` 是**可组合的落盘步骤** —— 它们**不**开事务;
`atomic()` 才是**操作边界**。在没有 `atomic()` 的情况下单独调一个 `save_*`,
每条语句各自自动提交。这不是缺陷, 而是分工, 并且由
`test_a_write_outside_an_operation_is_autocommitted` 钉住 —— 将来谁把
`atomic()` 从某个写方法里删掉, 穷尽回滚测试会立刻变红。

### 两条**不会**被回滚的东西 (诚实记录)

**内存状态不回滚。** 领域对象是在事务之外被修改的, 所以失败的操作会让内存
跑在数据库前面。那个操作对调用方来说是**失败**的 (异常), 而数据库停在操作
前的状态。两者在**下一次重启**交汇 —— 内存从数据库重建, 于是自动回到那个
一致的过去。这正是"重启"必须是一等验收场景的原因。

**文件系统不回滚, 而且顺序是刻意的。** `register_material` 先把字节复制进
受管目录, **再**写数据库记录。所以被回滚的登记可能留下一个**孤儿文件**。
这个方向是安全的: 孤儿文件不会被任何东西引用; 而孤儿**记录**是断掉的溯源链
(`material_integrity()` 报 `MATERIAL_FILE_MISSING`, `health()` 变 `degraded`)。
把顺序反过来会把一个无害的残留变成数据完整性缺陷。

### 失败注入 (spec 53 点名要求)

`WorkspacePersistence` 接受一个可选的 `fault_injector`: `(point, ordinal) -> None`,
在**每次成功落盘之后**被调用。`point` 是 `FAULT_POINTS` 之一, `ordinal` 是该次
落盘在当前操作内的 1-based 序号。注入器想模拟失败就抛异常。

- 生产代码永远不装它 (默认 `None`), 与项目既有的可注入 `Clock` 同一套惯用法。
- 13 个命名注入点: `course` / `session` / `material` / `evidence` /
  `knowledge_structure` / `review` / `organization` / `student_log` /
  `exercise` / `answer` / `evaluation` / `study_plan` / `learning_path`。
  名字是**落盘步骤**而不是表名 —— 一个步骤可能写多张表 (例如 `material`
  同时写 `materials` + `material_processing` + `material_evidence`)。
- `test_every_named_fault_point_is_reachable` 走一遍完整生命周期, 把实际
  出现过的点名与常量对表 —— 常量不是愿望清单。

### 测试: `tests/test_persistence_transactions.py` (71 条)

| 组 | 内容 |
| --- | --- |
| `TestTransactionBoundary` | 事务不残留、写锁还回去、嵌套安全、注入点可达 |
| `TestFaultInjectionPoints` | 每个命名点各一条: 在该点失败 -> 整体回滚 |
| `TestExhaustiveRollback` | 每个写操作 `k = 1..N` **全部**注入一次 |
| `TestFailureIsNeverSwallowed` | 错误码 / 异常类型 / `BaseException` / 诊断 |
| `TestAfterAFailure` | 重启后状态、重试等价、无孤儿行、健康报告 |
| `TestExplicitTransactionEntry` | 显式 `transaction()` 入口与连接所有权 |

**核心判据是"逐行相同", 不是"某张表还是 0 行"。** 断言"证据表是空的"只能
证明某一个具体缺陷不存在; 断言**整个数据库的逻辑内容与操作前完全一致**
才能证明性质本身 —— 包括将来新增的表, 因为快照是遍历 `table_names()` 得到的,
不是手写的表清单。

穷尽循环覆盖的注入点数量 (仅一条 tally 测试统计的 6 个操作) 是 **67**;
整份文件远超一百个。`test_the_write_count_is_deterministic` 钉住"落盘点数量
可复现" —— 否则"穷尽"就是碰运气。

比较时**显式归一化且仅归一化两项**:

- `material_processing.attempts` —— 重试路径合法地处理了两次;
- `data_dir` 下的绝对路径 (`stored_path`) —— 两份沙箱副本的目录名不同, 那是
  测试自己造成的差异。

两份沙箱都跑在**固定的注入时钟**上, 所以 `created_at` / `last_attempt_at`
不可能伪装成行为差异 (时间戳是唯一会让"两条路径逐行比较"失败的东西)。

### 顺带钉住的两条语义

- **回滚不会抹掉"尝试过"这件事**: 重试后数据库里的 `attempts` 与从未失败的
  路径**完全一致** —— 因为写 `material_processing` 的那一步也在被回滚的范围
  内。这是"数据库是 source of truth"的直接后果。
- **回滚是设计行为, 不是损坏**: 一次失败之后 `health().database.ok` 仍然是
  `true`、`status` 仍然是 `ok`、`integrity_check()` 仍然是 `ok`、
  `foreign_key_violations()` 仍然是空。回滚计数作为 `database.rollbacks`
  单独上报 —— 一个持续增长的数字说明有东西在反复失败, 运维需要看得见。

### Test Summary

```
pytest tests/test_persistence_transactions.py   71 passed   (18s)
compileall src tests                            退出码 0
```

spec 53 的配额是 ≥50, 实际 71。无一条被删除或 `skip`。

## Task 54 — True Restart / Crash / Recovery Acceptance（真重启验收）

### 为什么"重启"必须跨进程

"重启"这个词在同一个进程里**没有对应物**。同进程能测到的最强东西是"关掉
`Workspace` 再开一个"—— 但那仍然共享三样东西:

- 同一个解释器（模块缓存、已 import 的类对象、任何 `lru_cache`）；
- 同一个进程内存（任何模块级字典、任何逃逸出去的引用）；
- 同一份**操作系统文件句柄表** —— 所以"文件其实没落盘"这类缺陷照样能过。

真子进程把这三样全部切断：数据要活下来，只能靠**磁盘上的字节**。这就是
本 Task 单列的原因，也是它不能被"同进程重启"替代的原因。

### 三种"结束"方式，三种恢复机制

| 结束方式 | 有没有收尾 | 靠什么活下来 |
| --- | --- | --- |
| 干净退出（`close()`） | WAL checkpoint + 关连接 | 正常关闭 |
| 硬终止（`TerminateProcess` / `SIGKILL`） | **没有** —— `-wal` / `-shm` 留在盘上 | WAL 恢复 |
| 操作中途 `os._exit(9)` | 没有，而且**事务开着** | WAL 恢复丢弃未提交帧 |

只测第一种会漏掉整整两类缺陷，所以三种各测一遍。

### 崩溃场景怎么做到"真的有未提交数据在盘上"

这是最容易自欺的地方。默认页缓存约 2 MB，一个小事务的未提交页**根本没离开
内存**，于是"崩溃后未提交数据不见了"只是因为它们从没写出去 —— 那测的是内存，
不是恢复协议。

所以崩溃子进程在动手前先把页缓存压到 8 KiB（`PRAGMA cache_size = -8`），
任何一次业务写都会立刻把脏页挤进 `-wal`。让这条测试自证成立的判据是
**崩溃后 `-wal` 必须非空**（实测 37 KB）。

崩溃点用**生产代码自己的**失败注入点（Task 53 的 `FaultInjector`），注入器里
调 `os._exit(9)`：不抛异常、不 finally、不回滚、不关连接。

### 测试：`tests/test_restart_recovery.py`（90 条）

| 组 | 条数 | 内容 |
| --- | --- | --- |
| `TestTheHarnessIsReal` | 6 | 用 PID 证明"真的是两个进程"，不是代码读起来像 |
| `TestCleanRestart` | 21 | 干净退出后逐项恢复（含派生视图与审核历史） |
| `TestHardTermination` | 9 | 被硬杀之后数据在、文件在、库还能写 |
| `TestCrashDuringAnOperation` | 11 | 单条写崩 + 批量处理崩，都不许留痕 |
| `TestRecoveryIsDurable` | 6 | 连续重开结果一致（恢复不是读取时的障眼法） |
| `TestTheRestartedWorkspaceIsLive` | 8 | 重启后继续写、跨进程幂等 |
| `TestTruthSurvivesRestart` | 12 | 真值不被重启污染 |
| `TestNoSilentEmptyDatabase` | 6 | 损坏库绝不静默新建空库 |
| `TestRestartDeterminism` | 6 | 两次独立构建产出同一套 ID |
| `TestBackupDrillAcrossProcesses` | 5 | 47.12 演练的跨进程版本 |

`TestTheHarnessIsReal` 是**前提检查**：子进程把 `os.getpid()` 写进结果，父进程
（pytest 自己）比对。没有这一组，"到底是不是两个进程"就只能靠读代码相信。

### 两个让断言可信的细节

**数据目录的副本不是同一个目录。** `shutil.copytree` 不会重写材料注册表，所以
副本里的 `stored_path` 仍然指着**源**目录；重新登记会把它们改写成新目录
（那正是 Task 48-52 修过的缺陷 #22 的行为）。于是"文件逐字节一致"这条断言
必须要么用**专用目录**（本文件的 `register_run` 就是这么做的），要么把
`data_dir` 前缀归一化 —— 而且必须包含它的 **JSON 转义形式**，因为注册表是
JSON。搞错这一点，一条强断言就退化成噪音。

**固定时钟是前提。** 验收流水线跑在
`fixed_clock("2026-09-15T18:00:00+00:00")` 上，`created_at` 不可能伪装成行为
差异。没有它，任何逐字节比较永远为假。

### 重启不许改变什么

- `validation_status` —— `CONFLICTED` 的知识点重启后**还是** `CONFLICTED`；
  重启不是解决冲突的手段。领域层把"解决冲突"建模成一条**带证据选择**的确认
  （`decision == "confirm"` + 非空 `selected_evidence_ids`），判据是那个选择。
- 审核历史 —— 追加式，逐条一致，包括人显式选的那一侧证据。
- 证据溯源 —— 每个知识点回到材料与源位置的链条仍然可解析。
- 派生视图 —— `learning_path` / `coverage` / `gaps` / `dependencies` 重算后
  与内存里的值**完全相同**。
- 学习计划的身份 —— 值重算，内容寻址保证重算出同一个 `plan_id`。

### 崩溃不许留下什么

死在事务中途的业务操作**什么都不留**：已经写过的行不留，`material_processing`
的尝试计数也不留（那一步写在同一事务里）。库 `ok`、`integrity_check()` 为
`ok`、`foreign_key_violations()` 为空，下一个进程照样能写。

唯一**不**回滚的是文件系统（见 Task 53）：崩掉的 `register_material` 可能留下
一个孤儿文件。这个方向是安全的 —— 孤儿文件不被任何东西引用，而孤儿**记录**
是断掉的溯源链。重启是内存与磁盘重新交汇的地方，所以它必须被验证，而不是被假设。

### 变异测试（证明这 90 条不是空转）

把 `Workspace._restore_registries()` 里加载课程/课堂注册表的两行改成返回空列表，
然后重跑本文件：

```
42 failed, 30 passed, 18 errors
```

90 条里 60 条变红。改回源码后重新全绿。这就是"重启后数据还在"这句话的证据强度。

### Test Summary

```
pytest tests/test_restart_recovery.py                    90 passed   (35s)
pytest tests/test_hardening_backup_drill.py              19 passed   (含 47.12 真演练)
compileall src tests                                     退出码 0
```

spec 54 的配额是 ≥35 条且**必须真 subprocess**，实际 90 条，全部真子进程。
无一条被删除或 `skip`。

---

## Task 55 — Production Ready Final Gate（生产就绪终审）

### 这一阶段修的是什么

Task 48–54 已经把业务对象真正接进 SQLite、把一次业务操作变成一个事务、并且用真子进程
验证过重启与崩溃。Task 55 不新增功能 —— 它回答一个问题：

> 这些结论**能不能被一次性、可重复地复验**？还是只活在若干条分散的测试里？

`tests/test_production_gate.py`（75 条）就是那个复验入口，按十道门组织：

```
门                          判据
============================  ====================================================
A  持久化重启矩阵            13 行，与 FAULT_POINTS 顺序全等，逐行跨真子进程
B  追溯审计 (跨重启)          >= 50 个知识点，KP -> Evidence -> Material -> 源位置
C  真值安全                   CONFLICTED 不变，审核历史 append-only，学生不污染真值
D  确定性                     两次独立构建同 ID；两个进程读同一个库一致
E  幂等性                     1x / 2x / 3x 三轮计数完全相同，跨进程
F  依赖审计                   禁止 Redis/PG/MySQL/Mongo/Kafka/Celery/React/Vite/Docker
G  规模与足迹                 1000/100/50/500/5000/20000 + EXPLAIN QUERY PLAN
H  文件与库一致               每条材料记录有文件，无孤儿记录
I  构建卫生                   compileall exit 0，无游离脚本
J  版本与文档                 0.36.0 四处一致，报告存在且含未验证项
```

### 为什么 A 门要用真子进程

同进程里"重启"**没有对应物** —— 关掉再开一个 `Workspace` 仍然共享解释器、内存与
文件句柄表。真子进程是唯一能把"数据必须落在磁盘上"这句话变成可证伪命题的做法。

这与 `tests/test_restart_recovery.py` 是同一套方法，但那里验的是**行为**
（三种结束方式），这里验的是**覆盖面**（13 个落盘点一个不漏）。

### 13 行矩阵：为什么按"落盘点"而不是"表名"组织

13 个名字来自 `persistence_wiring.FAULT_POINTS`，它们是**落盘步骤**，不是表名 ——
一个步骤可能写多张表（例如 `material` 同时写 `materials` + `material_processing` +
`material_evidence`）。矩阵按同一口径组织，于是 Task 53 的"注入点覆盖"与 Task 55 的
"重启覆盖"说的是**同一组东西**：

```python
assert tuple(row[0] for row in RESTART_MATRIX) == tuple(FAULT_POINTS)
```

这条断言让两处口径不可能各自漂移。矩阵里唯一的 `derived` 行是 `learning_path`，
有专门守卫断言"派生行集合 == `['learning_path']`"，防止有人把漏写的 stored 当成
derived 混过去。

### 50 个知识点的追溯审计：为什么必须放大夹具

验收数据集原始夹具只有 6 份材料、产出 **15** 个知识点，达不到 spec 的抽样下限 ——
在 15 个点上做"随机抽 50 个"的审计会直接退化成空转。

做法是**只放大输入材料**：把板书 OCR 从 5 段扩成 80 段
（`BOARD_SEGMENTS = 80`，与 `test_hardening_traceability.py` 同一口径），
**不碰任何产品代码**。结果 90 个知识点、91 条证据。

关键判据不是"对象还在"，而是"重启后仍然能走通同一条证据链"：重启前逐条快照
`知识点 → evidence_id`（顺序敏感），重启后逐条比对，并要求每条证据的
`source.material_id` 仍在材料注册表里、`source` 仍有源位置。

### 三个"踩过才知道"的领域事实

1. **`CONFLICTED` 在 `validation_status` 轴上，不在 `review_status` 轴上。**
   验收之后 `review_status` 是 `confirmed`，而证据真值轴仍然是 `conflicted`。
   早期版本拿 `review_status` 去断言"冲突没被静默确认"，结果是**空转**（全是
   `confirmed`）。实测分布：`supported 88 + conflicted 2 = 90`。

2. **领域层把"解决冲突"建模成一条带证据选择的确认。**
   `decision == "confirm"` + 非空 `selected_evidence_ids`，**没有**独立的
   `resolve_conflict` 决策值。所以"显式解决"的判据是那个选择本身，
   而且那些证据必须 ⊆ 冲突的 `evidence_refs`。

3. **`study_plan()` 自己会追加一条快照行。**
   如果在它之前取 `counts()`，"重启不产生新快照"这条断言就**永远为真**。
   构建脚本里 `study_plan_id` 与 `counts` 必须放在所有派生调用**之后**。

### 实测数字（写进 `docs/final_persistence_report.md`）

```
门禁夹具 (真子进程, 90 个知识点)
  建库进程 PID 11116  ->  观察进程 PID 12580   (两者都 != 测试进程)
  courses 1 / sessions 1 / materials 6 / material_processing 6
  evidence 91 / knowledge_points 90 / review_records 90 / conflicts 1
  topics 1 / knowledge_memberships 2 / knowledge_relations 1 / session_memberships 87
  students 1 / student_knowledge_state 1 / exercises 1 / student_answers 1
  evaluation_results 1 / study_plans 3 / learning_paths 1
  integrity_check ok / foreign_key_violations [] / journal_mode wal
  health ok / version 0.36.0 / schema_version 2 / rollbacks 0
  material_integrity: checked 6, missing 0, hash_mismatch 0, path_unknown 0

规模门 (持久化层, spec 47.4 规模)
  db size 12,259,328 字节 = 11,972 KiB = 11.7 MiB
  总行数 27,601   每行平均 444.2 字节   (上界 4096)
  落库耗时 1.512 s                      (上界 120 s)
  1000 次单点查询 0.0435 s (43.5 us/次)  (上界 1.0 s)
  查询计划 SEARCH knowledge_points USING COVERING INDEX ... (无 SCAN)
```

`db size` 的判据是"足迹与行数成**线性关系**"，不是绝对数字 —— 硬编码 MiB 数在不同
机器/页大小上必然抖动，而重复存储会突破任何宽松上界。

### Test Summary

```
pytest tests/test_production_gate.py                75 passed   (17s)
pytest tests/test_restart_recovery.py               90 passed   (35s)
pytest tests/test_persistence_transactions.py       71 passed   (18s)
真实引擎集成套件 (whisper/ocr/document/pipeline)    310 passed / 0 skipped  (54s)
compileall src tests                                退出码 0
```

### 未验证项：如实标注，不掩盖

`docs/final_persistence_report.md` 里有一节 **BLOCKED / NOT VERIFIED**（9 条），
其中真实的空白是：

- **浏览器端到端渲染** —— 本机 Windows，浏览器自动化只支持 macOS / Linux；
  UI 靠 HTTP 契约测试（297 条）+ Node 里的 DOM 桩（125 checks），
  **不解析 CSS 布局、不触发真实事件**。
- **领域层热路径性能** —— G 门只覆盖持久化层；领域层由 `test_performance.py` 覆盖。
  内存占用、并发、长时间稳定性**未覆盖**。
- **跨机器 / 跨平台可移植性** —— 全部验收在 Windows 11 + 便携 CPython 3.14 上完成。
- **真实 ENOSPC / 真实超时** —— 是注入模拟，验的是错误被正确分类与上报。
- **崩溃只覆盖 `TerminateProcess` / `os._exit` 语义** —— 断电、文件系统损坏不在范围内。

一条被显式接受的跳过：全量回归里 **1 skipped** 是
`tests/test_startup.py:276`（"faster-whisper is installed; cannot exercise the
missing path"）—— 环境事实，不是被屏蔽的失败，但必须写出来而不是让读者从数字里猜。

### 变异测试（Task 55 也复核了 Task 54 的承重性）

把 `workspace._restore_registries()` 里加载课程/课堂的两行临时改成返回空列表，
重跑 `tests/test_restart_recovery.py` → **42 failed / 30 passed / 18 errors**，
90 条里 **60 条变红**。改回源码后重新全绿。

### 版本与文档

- 版本号 **0.35.0 → 0.36.0**，四处一致（`workspace.py` / `src/__init__.py` /
  `tests/__init__.py` / `docs/final_report.md`），并由 J 门逐处断言。
- 新增 `docs/final_persistence_report.md`（Task 48–55 阶段的固定格式最终报告）。
- `docs/final_report.md` 的 Known Limitations 第 1 条（"业务对象从未落盘"）已按事实
  **推翻并改写** —— 它不再是限制，而是本阶段修掉的东西。

### 本阶段累计发现的真实缺陷

Task 48–55 全程共发现并修复 **5 个真实产品缺陷**（#20 `exercise_evidence` 外键顺序 /
#21 判断题身份与展示顺序冲突 / #22 `data_dir` 搬迁后 `stored_path` 陈旧 /
#23 `list_sessions` 排序不一致 / #24 `submit_answer` 重复计数膨胀），
外加一处文档口径修正（`StudyPlan` 是"值 derived、快照行 stored"）。
每一条都先复现、再定位、再修复、再加回归测试。

---

## Task 56 — Real Classroom Workspace（真实课堂工作台）

### 这一阶段交付的是什么

Task 1–55 把引擎（Evidence / Knowledge / Validation / Review / Coverage /
Dependency / Student / Exercise / Evaluation / Study Plan / Learning Path /
Persistence）全部做扎实了，但用户第一眼看到的仍是"数据库结构"级别的概念
（course_id / session_id / material_id）。Task 56 在引擎之上盖了一层**只读投影**，
让第一层概念变成：

```
课程
 ↓
课堂
 ↓
今日
```

不重新实现任何引擎逻辑 —— 全部复用 `Workspace` 上已有的
`course_workspace` / `session_workspace` / `today` 方法，前端也只是如实显示。

### 新增的只读投影层

`src/application/classroom_view.py`（纯函数，无副作用）：

- `resolve_session_status(...)` —— 把六个状态机状态变成**可判定的真值表**，
  而不是散落在各处的 if/else。
- `_status_reason(...)` —— 每个状态都带一句人类可读的"为什么是这个状态"，
  前端原样显示，避免用户去猜。
- `_classify_evidence(...)` —— 把证据按来源分组（transcript / ocr / document /
  note / other），时间线视图（Task 58 会用到）复用同一分组。

### Task 56.3 的六个状态：全部从真实数据推导

| 状态 | 触发条件（全部可证伪） |
|---|---|
| `PLANNED` | 一节课材料都还没有 |
| `MATERIALS_ADDED` | 有材料，但没有任何一个作业成功过（也没在跑） |
| `PROCESSING` | 还有作业处于 QUEUED / RUNNING |
| `PROCESSED` | 处理完了，但这节课还没产出知识点 |
| `REVIEW_REQUIRED` | 有知识点，且其中存在待审核候选 |
| `READY_TO_STUDY` | 有知识点，且没有待审核候选 |

`MATERIALS_ADDED` 与 `PROCESSING` 之前无法区分（都"有材料、没知识点"）。
修复方式：给 `ProcessingJob` 加了一个显式 `enqueued: bool` 标记 ——
`start_session_processing` 把作业从 `REGISTERED` 抬成 `QUEUED` 时置 `True`，
于是"材料已落库但还没被排进队列"与"正在排队/处理"能被区分。六个状态全部用
验收夹具跑通（见 `test_classroom_workspace.py` 状态机测试）。

### Task 56.4 的"今天"由谁定

`today()` 不接受客户端传日期 —— "今天"由 `Workspace` 的时钟决定。
否则同一个快照在不同浏览器时区下会给出不同的"今天"，那就不叫同一份数据了。
而且 `today` 里的"今日学习"**必须来自 `study_plan()`**，前端不自己挑"今天该学什么"
（spec 63.3 的硬约束提前在这里落地）。没有学习计划就明说 `No learning state yet`。

### 前端

- 新增导航项 **Today**（默认进入页，比 Dashboard 更贴近"每天打开看什么"）。
- 重写 **Course 页**：教师 / 学期 / 语言 + 六个 stat（sessions / materials /
  evidence / knowledge / pending_review / coverage）+ 课堂表（可直接"整堂处理"）
  + 最近材料 + 待审核 + 覆盖/缺口卡。
- 重写 **Session 页**：统一九区块（Overview / Materials / Processing / Transcript /
  OCR / Documents / Evidence / Knowledge / Review / Learning），每区块严格显示
  推导状态与原因。
- `READY_TO_STUDY` 旁边明确标注"可开始学习 ≠ 已掌握"，防误读。

### 真值安全（Task 56 也提前守住了几条后续不变量的雏形）

- 计数 ≠ 掌握度：状态徽章严格区分 `UNVERIFIED / SUPPORTED / CONFLICTED /
  PENDING REVIEW`，不在前端把 `PENDING REVIEW` 显示成"已确认"。
- 数据隔离：`course_workspace` / `session_workspace` 只回传该课程的数据，
  不涉及跨课程查询（Task 68 的隔离不变量的基础）。

### Test Summary

```
tests/test_classroom_workspace.py   61 passed   (7s)
  - 投影层（course/session/today 形状与字段）
  - 六态状态机可达性（每个状态都用真实夹具触发）
  - 幂等：重复调用不增计
  - 时间线分组（Task 58 复用）
  - 空课程 / 空课堂边界
  - 真实引擎端到端（whisper/ocr/document 产出可观测的 KnowledgePoint）
node scripts/ui_audit.js            99 checks  OK
compileall src tests                退出码 0
```

### 版本与文档

- 版本号 **0.36.0 → 0.37.0**（新阶段 Task 56–70 起点），五处一致
  （`workspace.py` / `src/__init__.py` / `tests/__init__.py` /
  `docs/final_report.md` / `tests/test_production_gate.py` 的 `EXPECTED_VERSION`），
  由 J 门逐处断言。
- 新增 `src/application/classroom_view.py`（只读投影层）。
- 新增 `tests/test_classroom_workspace.py`（Task 56 验收，61 条）。
- 前端 `src/web/app.js` 新增 `pageToday`、重写 `pageCourse` / `pageSession`；
  `scripts/ui_audit.js` 新增 today / courseWorkspace / sessionWorkspace 夹具与
  today 路由页。

---

## Task 62 — Course Review Center（课程复习中心）

课程级的复习总览。**只读投影**：不新增表、不写任何业务事实、不重定义状态。

### 62.1 起点：先把基线修干净

拿到任务时仓库并**不是**干净的 —— `pytest -q` 给的是
`9 failed, 4181 passed, 1 skipped, 155 errors`，与任务书声称的
"Task 57.1 / 58–61 PASS" 不符（`docs/status.md` 实际只记到 Task 56）。

用探针直接打印 HTTP 响应体定位到**唯一**根因：

```
TypeError: Workspace.knowledge_points() got an unexpected keyword argument 'validation_status'
```

`KnowledgeService.get_knowledge_points()` 早就支持 5 个过滤参数，
`/api/knowledge` 端点也一直在传，但中间的装配层 `Workspace.knowledge_points()`
只转发了 `session_id` / `topic_id` —— 于是 `TypeError` 被映射成 400，
所有"先建知识点"的夹具全部在 setup 阶段炸掉（155 个 error + 大部分 failure）。
这是本项目第 1 类缺陷的又一次现身：**两端都有能力，装配层没接线**。

顺带修了 `conflict` 参数的类型陷阱：HTTP 查询串里它永远是字符串，
`"false" != False` 会让"不筛选"被当成"筛选 false"，静默返回空列表。
`_as_optional_bool()` 把 `"true"/"false"/"yes"/"no"/"1"/"0"/"on"/"off"`
归一为三态；无法识别时返回 `None`（不筛选）—— 筛错比不筛更糟。

另外 `tests/test_production_gate.py` 的 BuildHygiene 因 7 个遗留在仓库根的
`.py` 脚本而失败（上一个会话 19:54–21:20 产出，引用 `Workspace()` /
`CourseService()` 的错误签名）。确认无任何测试引用后移入
`temp/scratch-from-previous-session/`。

修复后基线：**4345 passed, 1 skipped, 20 deselected, 0 failed（368s）**。

### 62.2–62.7 投影层

新增 `src/application/course_review_view.py`（`CourseReviewView`），
一次取数、内存分组，**无 N+1**；课堂覆盖复用
`KnowledgeCoverageAnalyzer.analyze_topic`，绝不重写覆盖算法。

- **Overview**：`validation` 与 `review` 是**两个独立的轴**，并列返回，
  永不合并、永不相加。`pending_review` 只是便捷计数。
- **Topic Coverage**：主题 → 知识点数 / supported / unverified / conflicted /
  待审核 + `coverage_status`（领域只有 `covered` / `uncovered` 两个值，
  不存在"部分覆盖"——缺口类型也直接用领域那 5 个）。
- **Session Review**：每节课的 evidence / knowledge / pending / unverified /
  conflicted，带 `timeline_path` 指向既有 Session Timeline。
- **Review Queue**：透传既有 `ReviewService` 的候选（Task 45 已按
  `conflict_id` 去重），带 `path` 指向既有 Review Center，**不复制服务**。
- **Conflict**：`sides` 数组显式标注 `Evidence A` / `Evidence B`，
  确定性排序，绝不折叠成"某一方是对的"。
- **Gap Analysis**：`by_type` 覆盖全部 5 个领域缺口类型。
- **Empty State**：空课程 `empty: true`、计数全 0、HTTP 200（不是 500）。

### 62.6 修掉的两个真实缺陷

**(a) 冲突 → 知识点的归属根本不存在。**
领域层的 `ConflictRecord` 只带 `evidence_refs` / `description` / `status`，
**没有**知识点字段（冲突的语义是"两条证据互相矛盾"，不是"某个知识点错了"）。
但 UI 要从冲突卡片点进 Knowledge Detail，而证据↔知识点的关联只存在于
`KnowledgePoint.evidence_refs`。原投影里写的 `knowledge_detail_path` 因此
**永远是死代码**。

修复：在 `KnowledgeService.get_conflicts()` 的同一次遍历里收集"证据 refs 与
该冲突 refs 有交集的 KP"，判定口径与 `KnowledgeReviewService.
_allowed_evidence_refs`（审核层判断"该冲突触及这个 KP"）**逐字一致** ——
不一致会把人带到错误的知识点。收集的是**全部**触及的 KP 而非第一个
（一条冲突可同时触及多个知识点，丢掉其余就是伪造的确定性），
`knowledge_point_id` 取排序后第一个作为跳转目标。

**(b) 没有受支持的方式刷新内存里的课程上下文。**
`CourseContext` 是有状态的（处理服务持有装配结果、组织服务持有已登记 KP、
审核服务持有审核记录）。正常写入都经上下文方法，内存与库一致；
但任何**绕过上下文**的持久化写入（以仓储为唯一真源的维护操作、外部脚本）
之后，内存快照就过期了 —— 而在此之前只能重建整个 `Workspace`，
那会连带丢掉其他课程的缓存。

修复：新增 `Workspace.reload_course(course_id)`，只丢弃**这一门课**的上下文，
下次访问走完整的 `_build_context` + `_restore_course_state` 重建（幂等、只读、
不写任何业务事实）；空 id 抛 `InvalidInputError`，未知课程抛 `NotFoundError`。

这两条都是夹具"假装正确"时暴露的：注入冲突后 review 里始终是空数组，
如果只把断言改松就会掩盖缺陷。

### 62.10 i18n

`I18N` 新增 `nav.reviewCenter`（zh/es/ca）；`TRANSLATIONS` 的 es / ca 各补
28 条整句。面包屑里的分隔符写在 `t()` 之外（没有 `' / '` 这个键，
塞进去会漏翻 —— `ui_audit` 的 CJK 检查当场抓到了）。

### 前端

新增 `pageCourseReview(courseId)`，路由 `#/courses/{course_id}/review`，
顶栏新增「复习中心」入口。九区块：概览（两轴并列）/ 主题覆盖 / 按课堂复习 /
待审核队列 / 冲突（两侧证据）/ 缺口分析。所有跳转都进既有页面。

### Test Summary

```
tests/test_course_review.py        79 passed   (16s)   要求 40+
  - 领域常量漂移守卫 (VALIDATION/REVIEW/COVERAGE/GAP 与领域枚举逐字比对)
  - Overview / Topic / Session / Queue / Conflict / Gap / Empty
  - 只读性: 连续调用三次, 知识点 / 审核记录 / 计划快照计数一个不变
  - 真值安全: 响应 JSON 里搜不到 mastery / proficiency / predicted
  - 多课程隔离 A/B/C (含"同内容两门课 id 相同但归属不串"的边界)
  - 真实子进程重启后概览计数一致
  - API 契约 (200 形状 / 404 结构化 JSON / 绝不回 HTML 或 traceback)
node scripts/ui_audit.js           105 checks OK    (新增 courseReview 页)
node scripts/ui_render_check.js     32 checks OK
回归 tests/test_application_knowledge.py + test_knowledge_review.py +
     test_knowledge_validation.py + test_knowledge_coverage.py +
     test_persistence_roundtrip.py + test_persistence_repositories.py +
     test_knowledge_assembly.py     469 passed
回归 tests/test_api_server.py + test_exercise_ui.py + test_learning_view.py +
     test_web_ui.py + test_classroom_workspace.py   479 passed
```

### 变更文件

- `src/application/course_review_view.py`（新增，只读投影层）
- `src/application/workspace.py`（`knowledge_points` 接线 / `_as_optional_bool`
  / `course_review` / `course_review_summary` / `reload_course`）
- `src/application/knowledge_service.py`（`get_conflicts` 推导知识点归属）
- `src/application/dto.py`（`conflict_to_dict` 说明为什么不带知识点字段）
- `src/api/endpoints.py`（`/api/courses/{id}/review`、`/review-summary`）
- `src/application/material_workflow.py` + `processing_service.py`
  （跨课堂重注册同一材料时的 `session_id` 重指派）
- `src/web/app.js` / `src/web/index.html`（复习中心页 + i18n + 导航）
- `scripts/ui_audit.js`（courseReview 夹具与页面）
- `tests/test_course_review.py`（新增，79 条）

---

## Task 63 — Student Daily Dashboard（学生今日首页）

### 目标

把开发者视角的首页改成学生视角的「Today」，并且它是一个**派生视图**：
从 Course / ClassSession / StudyPlan / LearningPath / StudentState /
Exercise / Evaluation / Review 这些既有事实上投影出来，
**不维护第二份状态**（spec 63.2）。

### 交付

新增只读投影层 `src/application/student_today_view.py`（`StudentTodayView`），
经 `Workspace.student_today(course_id=None, student_id=None, lang="zh")`
暴露，HTTP 侧为 `GET /api/student-today`。

七个板块全部来自既有层，没有一处重算：

| 板块 | 数据来源 | 约束 |
| --- | --- | --- |
| Today's Classes | `Workspace._classroom_view().today()`（Task 56.4） | 复用，不重算"今天" |
| Today's Study | `learning_service.get_study_plan(sid)` | **不重新生成** StudyPlan |
| Learning Path | `learning_view.learning_path_view()` | Current / Prerequisite / Next |
| Pending Review | `Workspace.review_candidates(cid)` | 课程级，与学生无关 |
| Pending Exercises | `learning_view._pending_exercises()` | 点击进入现有练习页 |
| Recent Evaluations | `learning_view._recent_evaluations()` | 保持"最近提交在前"的顺序 |
| Attention | `StudentState`（Task 30 状态机） | 只透传已定义信号 + `basis` |

### 实现中确认的三件事（都写在代码注释里）

1. **`study_plan()` 有写副作用** —— 每调用一次就追加一行 content-addressed
   快照。投影层必须走 `ctx.learning_service.get_study_plan(sid)`，
   否则"首页刷新 5 次"会静默产生 5 条计划快照。已用测试锁住：
   `student_today` 调用 3 次后计划行数不变，而 `workspace.study_plan()`
   会 +1。
2. **待审核是课程级事实，不是学生属性** —— 最初把 `review_candidates`
   放进学生循环，导致"课上有 7 条待审核、首页显示 0"（学生还没注册时）。
   已改为按课程收集，与学生无关。
3. **`_recent_evaluations` 的顺序本身就是语义** —— 它按"提交顺序的逆序"
   给出。用 `answer_id` 重排会抹掉"最近"这个信息，只保留跨课程的
   `course_id` 稳定分组。

### 真实缺陷（本轮发现并修复）

- **`Workspace.knowledge_points()` 没有转发过滤器** ——
  `KnowledgeService.get_knowledge_points()` 与 `/api/knowledge` 早已支持
  `validation_status` / `review_status` / `conflict` / `language` / `search`，
  但装配层没接线，任何带过滤的调用都会 `TypeError` → 400。
  这是仓库里 155 个 error + 9 个 failure 的**唯一根因**。
- **`POST /api/knowledge` 的 `conflict` 是字符串** ——
  `"false"` 在 Python 里是真值。新增 `_as_optional_bool()`：认识
  true/false/1/0/yes/no，无法识别时返回 `None`（= 不过滤），
  避免"拼错一个词就静默换了语义"。
- **同一材料跨课堂重注册不生效** ——
  用相同 (内容, 文件名) 注册到另一个课堂时，返回的还是旧记录，
  新课堂里一份材料都没有。已在 `material_workflow.py` 重指派
  `session_id`，并在 `processing_service._ensure_job` 里重新同步。

### UI

`src/web/app.js` 的 `pageToday()` 改为消费 `/api/student-today`，
按 spec 63.11 的线框渲染六个统计卡 + 七个板块：

- 63.6 待审核卡带「去审核 →」直链 `#/reviews`
- 63.7 练习卡带「去练习 →」直链 `#/exercises`，每条直链练习详情
- 63.8 Recent Evaluations 显示为**评估**，并附
  "这是对学生作答的评估，不是对知识的核验"
- 63.9 Attention 每条都显示 `basis`（`StudentState state=... (Task 30)`）
- 63.10 空学生显示 `No learning activity yet.`，页面正常
- 63.12 "All Courses" 时每条都带课程名；选定课程后不再重复

新增 i18n 键 22 个 × 3 语言（zh / es / ca），键集三语一致。
新增 CSS：`.badge`、`.subblock`（此前被引用但未定义）。

### 测试

```text
tests/test_student_today.py                                    58 passed
tests/test_course_review.py + test_student_today.py           137 passed
```

覆盖：新学生 / 已有学生 / 学习计划 / 学习路径 / 复习 / 练习 /
评估 / 空状态 / 课程过滤 / 真实子进程重启 / 三语 / 只读性。

UI 审计（Windows 上无浏览器自动化，用 Node + DOM 桩）：

```text
scripts/ui_audit.js          118 checks  OK   (Task 62 时为 105)
scripts/ui_render_check.js    50 checks  OK   (Task 62 时为 32)
```

Task 63 新增断言：读的是派生视图端点、请求数有界、
四个板块存在、必需直链存在、Attention 带 basis、
**任何掌握度话术（mastery / proficiency / 已掌握）出现即失败**、
空学生不崩且不泄露堆栈。

### 变更文件

- `src/application/student_today_view.py`（新增，只读投影）
- `src/application/workspace.py`（`student_today` + `_student_today` 字段）
- `src/api/endpoints.py`（`/api/student-today`）
- `src/web/app.js`（`pageToday` 重写 + 22 个 i18n 键 × 3 语言）
- `src/web/styles.css`（`.badge` / `.subblock`）
- `scripts/ui_audit.js`（`studentToday()` 夹具 + 9 组断言）
- `scripts/ui_render_check.js`（`studentToday()` 夹具 + 18 条断言）
- `tests/test_student_today.py`（新增，58 条）

---

## Task 64 — Evidence-Grounded Exercise Workflow（证据驱动的出题流程）

### 目标

建立一条**确定性、非 LLM**的出题流水线：`KnowledgePoint → Template → Exercise`，
并且每一道题都能沿 `Exercise → KnowledgePoint → Evidence → Material`
回溯到原始课堂材料（spec 64.2 / 64.3 / 64.9）。

"确定性"是硬约束不是修饰词：同一输入 → 同一题干、同一选项、同一答案、
同一依据链（spec 64.4）。因此这条链路里**不允许**出现
`uuid4()` / `datetime.now()` / `random` / 内建 `hash()`。

### 交付

| 层 | 文件 | 职责 |
| --- | --- | --- |
| Domain | `src/exercise_generation.py`（新增） | 模板引擎：选模板、渲染、判可否出题 |
| Application | `src/application/exercise_workflow.py`（新增） | 装配：依据链、幂等落库、委托评估 |
| Composition | `src/application/workspace.py` | 7 个转发方法 + `_atomic` / `_flush_learning` |
| HTTP | `src/api/endpoints.py` | 7 条路由 |
| UI | `src/web/app.js` | 出题面板 + 依据链卡片 |

出题引擎 `ExerciseTemplateGenerator` 的四个模板：

| 模板 | exercise_type | 答案来源 |
| --- | --- | --- |
| `TRUE_FALSE_DEFINITION` | `true_false` | 陈述**原文照抄**，`correct_choice_id="true"` |
| `MULTIPLE_CHOICE_DEFINITION` | `multiple_choice` | 正确项 = 陈述原文；干扰项 = 结构式 |
| `SHORT_ANSWER_DEFINITION` | `short_answer` | `expected_answer` = 陈述原文 |
| `FILL_BLANK_TERM` | `fill_blank` | 挖掉一个 `original_terms` 词 |

### 关键设计决定（都写在代码注释里）

1. **TRUE_FALSE 绝不翻转事实** —— 判断题永远是"陈述 + True / False?"，
   答案是 True。要把一句话改写成假命题，就必须**编造**一个课程里不存在的
   说法，这正是 spec 64.7 禁止的。所以这里选择"不翻转"，
   把"这题是不是太简单"这个问题留给未来版本，而不是用编造换难度。
2. **干扰项只能是结构式的** —— `STRUCTURAL_DISTRACTORS`
   （`None of the above / Cap de les anteriors`、
   `Not stated in the material / No consta al material`）。
   它们对任何题目都为假，因此不可能与答案冲突，也不可能引入新事实。
3. **拒绝是一等公民** —— `can_generate()` 返回 `None` 或
   `GenerationRefusal`（带 `reason` + `recommended_action`）。
   `generate()` 对合法的领域情形**不抛异常**：未核验 → 拒；
   证据冲突 → 拒（**即使开了 `allow_unverified` 也拒**）；
   没有可用陈述 → 拒。HTTP 侧一律 200，UI 必须显示原因。
4. **幂等是继承来的，不是重写的** —— `Exercise.create` 用内容派生
   `exercise_id`，`LearningService.create_exercise` 遇到已存在的 id 直接
   返回旧记录。所以"生成三次"天然只有一道题（spec 64.13）。
   测试用 3 次调用 → 1 条记录锁住这一点。
5. **评估完全委托** —— `submit()` 调 `Workspace.submit_answer`，
   再 `get_evaluation`。应用层不判断"学生说得对不对"（spec 64.8）。
6. **`student_state` 语义** —— 内部 `student_state()["states"]` 是
   **list of dicts**，不是以 kp_id 为键的 dict。踩过两次，已固化在测试里。

### 实现中发现的一件事（领域语义，非缺陷）

**Task 30 的状态机只由学习事件驱动。** `reduce_events(events)` 是
`state` 的唯一来源；`answer_count` / `correct_count` / `incorrect_count`
是**旁路记录**，永远不触发状态迁移。而且迁移是**有序**的：
`not_started → exposed (viewed) → practicing (practiced) → reviewing (reviewed)`。
从 `not_started` 直接投 `practiced` 是**合法但无效**的 no-op。

实测：

```text
practiced only            -> not_started
viewed                    -> exposed
viewed + practiced        -> practicing
viewed + practiced + reviewed -> reviewing
```

这正好落实了 spec 64.19 的"答题 ≠ 掌握"：
答对一道题不会把学生标成已掌握，只有显式学习事件才会。
已改写为两条测试分别锁住"答题只记计数"和"显式事件才迁移状态"。

### UI

`pageExercises()` 新增**出题面板**：

- 如实分开显示"可出题 / 不可出题"（spec 64.10 / 64.12）
- 不可出题的列表**展开可见**并带 `validation_status` 药丸 ——
  "为什么不能出题"本身就是信息，不能藏
- `#generate-batch` 按钮 → `POST /api/exercise-generation/batch`
- 结果区分 `created` / `reused` / `refused`，拒绝项列出
  `reason`（走 i18n 符号键）+ `recommended_action`

`pageExercise()` 新增**出题依据链卡片**
（`loadGroundingChain` → `GET /api/exercises/{id}/grounding`）：

- 四段：练习 → 知识点 → 证据 → 材料
- `complete` / `unresolved` 如实显示，解析不了的引用列在
  "未解析的引用"横幅里，**不隐藏**
- 生成来源只在后端确实报告 `generator_version` 时才声称"模板生成"；
  手写练习显示"人工编写的练习（无生成器来源）"

新增 i18n 键 27 个 × 3 语言（zh / es / ca），键集三语一致。
新增 CSS：`.toast.toast-warn`。

### 测试

```text
tests/test_exercise_workflow.py     64 passed, 1 skipped
```

10 个测试类：模板确定性 / 不编造 / 依据链 / 拒绝矩阵 /
幂等 / 题型 / 作答与评估 / 课程隔离 / API 契约 / 重启一致性。

关键断言：

- 判断题题干必须是知识点 title/content 的**逐字子串**
- 选择题干扰项必须全部落在 `STRUCTURAL_DISTRACTORS` 里
- 同输入生成 3 次 → 1 条记录；批量跑两次不膨胀
- `allow_unverified` **不能**放宽冲突拒绝
- 跨课程出题被拒（404）
- 同一材料在两门课里 id 相同但成员关系不相交
- `preview` 不落库（调用前后计划行数不变）
- 依据链在引用损坏时报告而不隐藏
- 真实子进程重启后 `exercise_id` / `draft_id` / `answer_id` /
  `evaluation_id` 全部可重放

### UI 审计

```text
scripts/ui_audit.js          136 checks  OK   (Task 63 时为 118)
scripts/ui_render_check.js    66 checks  OK   (Task 63 时为 50)
```

Task 64 新增断言：出题面板存在、按钮带 `course_id`、
不可出题知识点**可见**、无掌握度话术、无堆栈、
依据链解析出知识点/证据/材料、三语下均无未翻译 CJK。
渲染审计额外加了 `__posted` 追踪：单题页加载时**不得**有任何非 GET 请求。

### 变更文件

- `src/exercise_generation.py`（新增，模板引擎）
- `src/application/exercise_workflow.py`（新增，装配层）
- `src/application/workspace.py`（7 个转发方法 + 注入）
- `src/api/endpoints.py`（7 条路由 + `_generation_config`）
- `src/web/app.js`（出题面板 + 依据链 + `actionGenerateExercises` + 27 个 i18n 键）
- `src/web/styles.css`（`.toast.toast-warn`）
- `scripts/ui_audit.js`（Task 64 夹具 + 断言，含 `dump` 打印附属容器）
- `scripts/ui_render_check.js`（Task 64 夹具 + 断言 + `__posted`）
- `tests/test_exercise_workflow.py`（新增，64 条）

---

## Task 65 — Mistake & Weak Knowledge Center（错题与薄弱知识点中心）

目标：回答学生的问题 **"我哪里需要重新学习？"** —— 而不是给出一份
"掌握度报表"。整个中心是一个**只读投影**：它读既有 `Evaluation` 判对错，
读既有 `StudentState` 判薄弱，自己既不重算对错，也不推导掌握度。

### 交付

| 交付项 | 位置 | 说明 |
| --- | --- | --- |
| 投影层 | `src/application/mistakes_view.py` | `MistakesView`，约 880 行，无任何写操作 |
| 应用层转发 | `src/application/workspace.py` | `mistakes_center()` / `mistake_detail()` |
| HTTP API | `src/api/endpoints.py` | 2 条只读 GET 路由 |
| 学生界面 | `src/web/app.js` | 错题本主页 + 知识点错题详情 |
| 测试 | `tests/test_mistakes_center.py` | 76 条（73 passed / 3 skipped） |

### 两条只读路由

```text
GET /api/students/{student_id}/mistakes?course_id=&group_by=&lang=
GET /api/students/{student_id}/mistakes/{knowledge_id}?course_id=
```

### 核心设计决定

1. **对错只有一个来源。** `_mistakes()` 逐条读
   `learning_service.answer_log_for(student_id)`，对每条调
   `get_evaluation(answer_id)`，**只**保留 `status == "incorrect"` 的行。
   没有评估的答案被跳过 —— 不猜。答对的答案**完全不出现**在错题表里
   （不是"标成对"，是不出现）。这是 spec 65.5 的可执行形式。

2. **薄弱必须来自 `StudentState`。** `_weak_knowledge()` 只在
   `attention in ("NEEDS_REVIEW", "NEEDS_PRACTICE")` 时产出一行，
   并带上 `definition: "StudentState (Task 30)"`。
   一个知识点哪怕错了 5 次，只要 Task 30 没给它这两个状态，它就出现在
   `knowledge` 里（带 `incorrect_attempts=5`）而**不在** `weak_knowledge` 里。
   这是 spec 65.6 "不允许 `wrong_count > 0` 就等于 weak" 的可执行形式。

3. **薄弱信号与错题计数不同步是常态。** 同一份数据里
   `knowledge[].incorrect_attempts = 2` 而 `weak_knowledge = []` —— 这是
   正确行为：错答是**事实**，薄弱是**状态**，两者由不同机制产生。

4. **建议动作必须有真实依据。** `SUGGESTED_ACTIONS` 四项各自带 `basis`：
   `REVIEW_KNOWLEDGE` 恒有；`VIEW_EVIDENCE` 仅在证据链能解析时出现；
   `VIEW_PREREQUISITE` 仅在声明了前置时出现；`PRACTICE_AGAIN` 仅在
   有可复用练习时出现。**绝不出现点了没反应的按钮。**

5. **"再练一次"复用既有练习，零生成。** `_practice_targets()` 调
   `list_exercises()` 并按 `(0=从未作答, 1=答过且错, 2=答过)` 排序，
   返回 `reused: True`。测试里断言调用前后 `list_exercises()` 长度
   **完全一致** —— 这是 spec 65.10 的可执行形式。

6. **分组不改变数据源。** `group_by=knowledge` 是两层
   （知识点 → 练习），`group_by=topic` 是三层
   （主题 → 知识点 → 练习）。两者读同一批 mistake 行，
   无主题的知识点进入显式的 `__unassigned__` 组，而不是被丢掉。

### 一个真实的产品约束：自由文本造不出"答错"

Task 32 的评估器对 `short_answer` **只做字面比较**（spec 32.6 禁止语义
判分），字面不等即 `unsupported`，而不是 `incorrect`。同理：
`true_false` 的非法值、`multiple_choice` 的非法 `choice_id` 都返回
`unsupported`。

也就是说，**只有 `multiple_choice`（合法但错的选项）/ `true_false`
（`"false"`）/ `fill_blank`（任何不在 `accepted_answers` 里的串）**
这三种情形能产生 `incorrect`。这不是缺陷，而是"没有语义判分就没有
'答错'这个概念"的直接后果，也是为什么测试 helper 必须**按题型**
构造错值（`_wrong_value()`），而不能用一个通用字符串。

### UI

- 导航新增 **错题本**（`#/mistakes`）。页面顶部直接回答
  "我哪里需要重新学习？"。
- 计数条：错答次数 / 涉及知识点 / 分组数 / 薄弱知识点。
- 错题表列严格按 spec 65.2：练习 / 题目 / 我的作答 / 评估 / 知识点 / 主题。
- 分组切换按钮只改 `state.mistakeGroupBy`（存 localStorage），
  发的是同一条只读 GET —— 切换分组**不产生任何写请求**。
- 详情页四块：为什么错（既有评估的反馈 + 评分器版本）→ 重新学习依据
  （证据 + 源材料文件名 + 定位）→ 建议操作（每条带依据）→
  再练一次（复用既有题，标注"曾答错/练习过/未练习"）。
- 空态：`No mistakes yet.` 走正常空状态渲染，不是错误页。

### 一个值得记下的前端陷阱

`a + b + (cond ? X : Y + Z) + c` 里，**`?:` 的优先级低于 `+`**，
所以裸写的三元会把整条 `+` 链的后半段吞进 `:` 分支 ——
表现是"页面只渲染了空态卡片，其余全部消失"。
本任务里 `pageMistakes()` 踩到过这个坑，修法是**给三元整体加括号**。
已记入本文件，后续拼接型渲染一律加括号。

### 测试

```text
tests/test_mistakes_center.py   73 passed, 3 skipped
```

12 个测试类覆盖 spec 65.12 要求的全部维度：
`TestMistakeList` / `TestGroupByKnowledge` / `TestGroupByTopic` /
`TestEvaluationSource` / `TestWeakKnowledge` / `TestSuggestedActions` /
`TestEvidenceTraceback` / `TestPracticeAgain` / `TestEmptyState` /
`TestCourseIsolation` / `TestApiContract` / `TestRestartConsistency`。

3 条 skip 都在 `_make_three_distinct_exercises`：单个知识点只支持两种
可判错的题型，造不出三道不同的题。这是夹具的能力上限，不是实现缺陷。

关键断言：

- 答对的答案 `answer_id` **不在**错题集合里
- 错 5 次但无 `NEEDS_*` → 不进 `weak_knowledge`
- `PRACTICE_AGAIN` 前后 `list_exercises()` 长度不变
- 未知学生 → 404；`group_by` 非法 → 400；缺 `course_id` → 400
- 跨课程隔离落在**成员关系**上（`knowledge_id` 是内容寻址的，
  两门课里相同材料会产生相同 id）
- 真实子进程重启后错题集合逐字段可重放

### UI 审计

```text
scripts/ui_audit.js          180 checks  OK   (Task 64 时为 136)
scripts/ui_render_check.js   100 checks  OK   (Task 64 时为 66)
```

Task 65 新增断言：错题表六列齐全、评估 id 可见、知识点/练习链接正确、
错答次数可见、分组切换按钮存在且带 `course_id`、
**不因错答次数推断薄弱**、空态正常、详情四块齐全、
三语下（es/ca）均无未翻译 CJK、全程无 `mastery` / `掌握度` 话术、
无堆栈、无 `__posted`（页面加载不发任何写请求）。

### 变更文件

- `src/application/mistakes_view.py`（新增，只读投影）
- `src/application/workspace.py`（2 个转发方法 + 注入）
- `src/api/endpoints.py`（2 条只读路由）
- `src/web/app.js`（错题本主页 + 详情页 + 分组切换 + 42 个 i18n 键 × 3 语言）
- `src/web/index.html`（导航新增 `#/mistakes`）
- `src/web/styles.css`（错题分组 / 建议动作 / 证据条样式）
- `scripts/ui_audit.js`（Task 65 夹具 + 断言）
- `scripts/ui_render_check.js`（Task 65 夹具 + 断言）
- `tests/test_mistakes_center.py`（新增，76 条）

---

## Task 62–65 最终审查（final review）

统一门禁执行完毕，并按 §二十五 的 Final Verdict 逐项核对。审查阶段
主动搜寻遗漏时发现并修复了 1 处查询模式问题、修补了 3 处测试断言过于
粗粒度的问题。

### 修复 1：错题列表的 N+1 形状（真实改进）

`MistakesView._mistakes()` 原本在 `for answer in log:` 循环体内逐条调用
`learning_service.get_evaluation(answer_id)`。

经核实，`get_evaluation` 的实现是纯内存字典查找
（`self._evaluation_by_answer.get(aid)`），**不构成数据库层面的 N+1**，
所以这不是性能缺陷。但它确实是规范明令禁止的**形状**
（"不得出现 N+1 / N×M 查询"），而且把"缺评估"表达成"异常控制流程"
（`try/except NotFoundError: continue`）本身也不好读。

修法：抽出 `_evaluation_index(answers)`，一次性建
`answer_id -> evaluation` 索引，"缺评估"表达成"索引里没有这一项"。
调用点从 N 次降为仍为 N 次（内存查找无法真正"批量"），
但**形状**收敛到一处、语义更清晰，并且可以被静态守卫约束。

### 修复 2：新增 `TestQueryPattern`（4 条守卫测试）

把"禁止 N+1"和"投影不得自己判对错"从注释变成可执行约束：

- `test_evaluation_index_is_built_in_one_pass` —— 索引构造时
  `get_evaluation` 调用次数 ≤ 答案条数（防止"先判空再取值"查两次）；
- `test_center_builds_the_index_once_not_per_row` —— `center()` 的
  4 个区块共用同一份索引，调用次数有上界，不是 4 × 答案数；
- `test_source_has_no_per_row_evaluation_lookup_inside_a_loop` ——
  静态守卫：`get_evaluation` 全模块**只允许出现一次**，
  且必须落在 `_evaluation_index` 内；
- `test_no_projection_recomputes_correctness` —— 投影里不得出现
  `correct_choice_id` / `expected_answer` / `accepted_answers` / `is_true`，
  对错只能来自 Evaluation（spec 65.5）。

**这条守卫的判据本身也修正过一次**：最初写成"循环里出现查询就是 N+1"，
结果把 `_evaluation_index` 里那个**合法的** O(n) 建索引遍历也判成违规。
N+1 的正确定义不是"循环里有一次查询"，而是**同一批数据在多个阶段被
反复查**（列表阶段一遍、分组阶段一遍、薄弱判断再一遍）。
判据改为"调用点只允许出现一次，且必须在索引构造函数里"。

### 修复 3：`/grounding` 子资源的静态守卫判据过粗（回归修复）

`test_the_ui_never_calls_the_authoring_exercise_endpoint` 原先用
"源码里不出现 `api('/exercises'`"这种子串判据。Task 64 引入
`api('/exercises/' + id + '/grounding')` 后它误报，
而我先后三次尝试收紧都没成功（`['/exercises/']` → `['/exercises']`
→ `['#/courses/.../exercises/']` 之类的假阳性）。

**根因**：JS 源码里的字符串字面量大量是**路径片段**，
`'/exercises/'` 既出现在后端调用里，也出现在前端 hash 路由
（`'#/courses/' + id + '/exercises/'`）里。任何"扫字面量"的判据都分不清这两者。

**最终修法**：改为**解析 `api(...)` 的 URL 实参** ——
用括号配平取出第一个实参，把常量段保留、变量段替换成 `*`，
还原成"最终路径形状"，再判断形状是否以 `/exercises` 开头且不以
`/grounding` 结尾。同时支持
`const groundingPath = '/exercises/' + ... + '/grounding';` 后
`api(groundingPath, ...)` 的写法。

这样判据同时满足四点：允许只读依据链、禁止作者视角 DTO、
不误伤前端路由、能看懂"先拼路径再调用"的写法。

### 修复 4：服务器不回收 keep-alive 连接（真实产品缺陷）

这一处**不是测试问题，是 `src/api/server.py` 的真实缺陷**。

**症状**：单进程跑完整套件时报 3 failed + 37 errors，但每个文件单独跑
全绿。错误类型分布是纯连接类：

```text
ConnectionAbortedError [WinError 10053]   43
ConnectionResetError                      16
AssertionError / TypeError                 1   <- 连接被中止的次生症状
```

**根因**：`ApiServer.stop()` 调用 `httpd.shutdown()` —— 但
`shutdown()` **只停止 accept 循环**，不会关闭已经被工作线程持有的
keep-alive 连接（handler 声明了 `protocol_version = "HTTP/1.1"`）。
每个用例都起一个 `port=0` 临时服务器，关闭后套接字进入 TIME_WAIT，
而 Windows 上 `allow_reuse_address = False` 使端口无法快速复用；
跨文件累积上千个即耗尽临时端口，服务器侧于是中止连接。

**为什么只在全量长跑出现**：单个文件几十个用例不足以耗尽动态端口范围。

**修法**（`src/api/server.py`）：

1. `_ClassroomHTTPServer` 新增活跃连接登记表
   （`track_connection` / `forget_connection` / `close_live_connections`）；
2. `Handler.setup()` / `finish()` 挂接登记与注销；
3. `stop()` 在 `shutdown()` 之后调用 `close_live_connections()`，
   对每条连接 `shutdown(SHUT_RDWR)` + `close()` —— 主动断开，
   不依赖 GC 或对端超时。

**顺带加固**：`tests/test_web_ui.py` 的 `Client` 从
`urllib.request.urlopen`（每请求新建连接）改为 keep-alive 连接池，
夹具 `finally` 里显式 `close()`；该文件耗时 35.64s → 28.99s。

**新增回归守卫** `TestServerConnectionLifecycle`（3 条）：
关闭后端口可立即重绑、stop() 后 keep-alive 连接不可再用、
连接登记表随连接结束而清理（不是只增不减）。

### 门禁复跑结果（最终）

```text
pytest (full, 单进程)              4665 passed, 5 skipped   EXIT=0
compileall -q src tests            exit 0
production gate + restart + backup
  + web_ui                         253 passed
scripts/ui_audit.js                180 checks  OK
scripts/ui_render_check.js         100 checks  OK
```

### 临时文件清理

删除本阶段产生的调试脚本与探测文件：

```text
temp/dbg64.js  temp/dbg65*.js (9 个)  temp/probe65.py
temp/_harness_probe*.cjs  temp/course_review_dto.json
temp/pytest_full65*.txt  temp/task6{2,3}_status.md  temp/insert_i18n.py
cache/probe49..54*.py  cache/probe62*.py  cache/probe63*.py  cache/probe64*.py
```

清理后复验：compileall exit 0、两个 harness 仍全绿。

### 变更文件（补充）

- `src/application/mistakes_view.py`（`_evaluation_index` 抽取）
- `tests/test_mistakes_center.py`（+4 条 `TestQueryPattern` 守卫）
- `tests/test_exercise_ui.py`（依据链子资源判据重写为路径形状解析）
- `docs/task-62-65-final-report.md`（新增，最终报告）

## Task 66 — Daily Learning Workflow（每日学习流程）

目标：把既有链路
`Today → StudyPlan → LearningPath → Knowledge → Exercise → Answer → Evaluation → StudentState`
串成一条**可中断、可恢复、可重复**的每日流程。本任务不新建任何领域能力，
只做"选择 + 投影 + 串接"。

### 交付

| 交付项 | 位置 | 说明 |
| --- | --- | --- |
| 工作流投影层 | `src/application/learning_workflow.py` | `LearningWorkflow`，只读投影 + 确定性选择，无新领域逻辑 |
| 应用层转发 | `src/application/workspace.py` | 5 个 façade 方法，写操作包 `_atomic()` + `_flush_learning()` |
| HTTP API | `src/api/endpoints.py` | 5 条路由（3 GET / 2 POST） |
| 学生界面 | `src/web/app.js` | `#/learn` 今日入口页 + `#/courses/{id}/learn/{kp}` 知识点学习页 |
| 样式 | `src/web/styles.css` | Task 66 区块 + 回填 3 个历史欠账类名 |
| 测试 | `tests/test_learning_workflow.py` | 102 条 |
| UI 审计 | `scripts/ui_audit.js` | 180 → 195 checks |
| UI 渲染 | `scripts/ui_render_check.js` | 100 → 119 checks |

### 五条路由

```text
GET  /api/students/{student_id}/learning/start?course_id=
GET  /api/students/{student_id}/learning/knowledge/{knowledge_id}?course_id=
POST /api/students/{student_id}/learning/knowledge/{knowledge_id}
GET  /api/students/{student_id}/learning/knowledge/{knowledge_id}/exercise?course_id=
POST /api/students/{student_id}/learning/answer
```

`course_id` 在 `POST` 上**同时**接受 body 与 query（两条 POST 一致，
见下方缺陷 3）。路径与 body 的 `student_id` 不一致时返回
`INVALID_INPUT`；未知课程 / 学生 / 知识点返回 `NOT_FOUND`（404）。

### 核心设计决定

1. **任务选择是确定性的，且与"考试概率"无关。**
   `_candidates()` 排序键为
   `(未满足前置数, 状态秩, 会话序号, 知识点 ID)`。
   没有随机数、没有时间戳、没有概率字段。同一份数据在任何时刻、
   任何进程里选出的"下一个任务"都相同。spec 明令禁止按考试概率排序 ——
   本模块里根本不存在概率这个概念（见下方 No-Prediction 常量）。`

2. **状态只能被合法 `LearningEvent` 推进。**
   答对/答错**不改变** `LearningState`：`ANSWERED` 刻意不在 `_TRANSITIONS`
   的转移表里。`answer()` 只写 `LearningEvent` 并读回状态，永不写状态字段。
   测试 `test_correct_answer_updates_counts_but_not_state` 断言
   答对后状态仍为 `exposed`、`practice_count` 仍为 0，
   而 `correct_count` 变为 1 —— 计数与状态是两回事。

3. **两条真相轴分开排除，绝不合并。**
   `EXCLUDED_VALIDATION_STATUSES = {"conflicted"}` 与
   `EXCLUDED_REVIEW_STATUSES = {"rejected"}` 各自独立生效，
   输出里 `validation_status` / `review_status` 也分列两个字段。
   合并成"一个综合状态"会让"证据有冲突但人已确认"这类事实消失。

4. **工作流必须能终止。** `_candidates()` 会跳过
   `next_learning_event(state) is None` 的知识点 —— 也就是已经走到
   `reviewing` 终点、没有任何合法后继事件的那些。没有这一条，
   "下一个任务"会在终点上永远指向同一个知识点，流程不收敛。

5. **无证据时不编造解释。** 证据链解析不出来时
   `grounded_explanation` 为 `None`，`evidence_note` 为
   `"Evidence unavailable."`。UI 原样显示这句话，不补一句"建议查阅教材"之类
   没有来源的内容。

6. **措辞是事实性的。** 常量 `WORKFLOW_FORBIDDEN_TERMS` 列出
   `mastery` / `mastered` / `proficiency` / `predicted` / `exam_probability`
   等词；无任务可做时输出 `"No learning task available."`，
   而不是 `"You mastered this topic."`。spec 点名禁止的那句话在
   UI 渲染检查里有专项断言。

### 本轮修出的真实产品缺陷

**缺陷 1 — `knowledge_point_to_dict()` 从不输出 `conflict` 键。**
两个下游消费者（`KnowledgeService` 过滤、`LearningWorkflow` 排除）
都在读这个键，但它从来没被写过。结果是"冲突知识点应被排除"这条规则
实际上**永远不生效**（读到的一直是 `None`）。
修法：给 `knowledge_point_to_dict` 加 `conflict` 关键字参数，
并加 `_coerce_conflict_flag()` 归一化（HTTP 查询串里永远是字符串，
`"false"` 必须被当成假）。

**缺陷 2 — `GET /api/knowledge` 的 5 个过滤器全部抛 5xx。**
`KnowledgeService.get_knowledge_points` 对领域对象
`KnowledgePoint`（dataclass，没有 `.get()`）调用了 `kp.get(...)`，
所以 `validation_status` / `review_status` / `conflict` / `language` /
`search` **任何一个**过滤器一开就 `AttributeError`。
之所以长期没被发现：`tests/` 里**没有任何一处**带参数调用过这个方法。
修法：改用属性访问重写过滤块，并把证据语言的读取做成
`Mapping` / 对象双兼容。

**缺陷 3 — 两条 POST 端点的参数来源不一致。**
`learning_answer` 从 body 或 query 取 `course_id`，
而 `learning_open_knowledge` 只认 query。把 `course_id` 放进 body 会得到
400。修法：统一为"body 优先，回落 query"。

**缺陷 4 — 三个 CSS 类名只有用法没有定义。**
`app.js` 写出了 `.block-label`（Task 60）、`.conflict`（Task 62）、
`.path-chain` / `.path-node`（Task 41），但 `styles.css` 里**都没有** ——
其中 `.path-chain` / `.path-node` 还被 `docs/status.md` 声明为"已加入
styles.css"。页面只是"没有样式"而非"报错"，因此所有既有检查
（渲染成功、无 CJK、无 traceback）都测不出来。
修法：补齐 4 处样式；并给 `ui_audit.js` 增加一条
**"app.js 写出的每个 class 都在 styles.css 里有定义"**的静态检查。

### 新增的静态守卫（缺陷 4 的根因修补）

`scripts/ui_audit.js` 新增 3 条检查，把 UI 两侧对起来：

1. `styles.css defines classes`（>20）—— 证明扫描不是空集；
2. `app.js emits static class names`（>30）—— 同上；
3. `every class emitted by app.js is defined in styles.css` —— 逐名比对。

两个集合都**先剥掉注释**再统计。第一版没剥注释，于是类名只要在
CSS 注释里被提过就算"已定义"—— 变异测试（把 `.block-label` 改名）
当时**没有报错**，暴露了这个假阴性。剥注释后同一变异体被正确捕获：

```text
MUTATED: renamed .block-label -> .block-labe1
UI audit FAILED (1/195)
  ✗ every class emitted by app.js is defined in styles.css — block-label
```

这是"检查是否有牙齿"的可执行证据，而不是"检查存在"的声明。

### 边界情况覆盖

| # | 边界 | 断言要点 |
| --- | --- | --- |
| 1 | 无任何知识点 | `has_task=False`，`NO_TASK_NOTE`，不抛异常 |
| 2 | 全部知识点已 `conflicted` | 全部排除，仍返回可读的 progress |
| 3 | 全部知识点已 `rejected` | 同上，且与 `conflicted` 走**不同**的排除分支 |
| 4 | 知识点证据不足 | `evidence_available=False`，不编造解释 |
| 5 | 答对 | 状态不变、`correct_count` +1 |
| 6 | 答错 | 状态不变、`incorrect_count` +1 |
| 7 | 评估不可判定（`UNSUPPORTED`） | **既不 +1 correct 也不 +1 incorrect** |
| 8 | 重复提交同一答案 | 幂等，计数不重复累加 |
| 9 | 重复取练习 | 复用既有练习，练习数不膨胀 |
| 10 | 重复打开知识点 | `viewed` 幂等，`exposure_count` 不重复累加 |
| 11 | 前置未满足 | 排在候选末位，且 `unmet_prerequisites` 如实列出 |
| 12 | 未打开页面直接答题 | 状态仍 `not_started`（没有 `viewed` 就没有 `exposed`） |
| 13 | 未知课程 / 学生 / 知识点 | 404 `NOT_FOUND` |
| 14 | 缺 `course_id` | 400 `INVALID_INPUT` |
| 15 | 路径与 body `student_id` 不一致 | 400 `INVALID_INPUT` |
| 16 | 重启后恢复 | 状态分布、计数、练习数逐字段一致 |

### 门禁复跑结果

```text
tests/test_learning_workflow.py                102 passed
5 个依赖套件 (application_knowledge / learning_view
  / exercise_ui / mistakes_center / web_ui)    412 passed, 3 skipped
pytest (full)                                  见本节末
compileall -q src tests                        exit 0
scripts/ui_audit.js                            195 checks  OK
scripts/ui_render_check.js                     119 checks  OK
```

### 变更文件

- `src/application/learning_workflow.py`（新增）
- `src/application/dto.py`（新增 `conflict` 参数 + `_coerce_conflict_flag`）
- `src/application/knowledge_service.py`（重写 5 个过滤器 + `_conflict_ids_by_kp`）
- `src/application/workspace.py`（`CourseContext.learning_workflow` + 5 个 façade）
- `src/api/endpoints.py`（5 条路由）
- `src/web/app.js`（`learn.*` 三语表 + 入口页 + 知识点学习页 + 路由）
- `src/web/styles.css`（Task 66 区块 + 回填 4 个类名）
- `tests/test_learning_workflow.py`（新增，102 条）
- `scripts/ui_audit.js`（+2 页面 +3 静态检查）
- `scripts/ui_render_check.js`（+19 检查）

## Task 67 — Exam Review Mode（考前复习模式）

目标：回答学生的问题 **"考试快到了，我该按什么顺序复习？"** ——
而**不是**"哪些最可能考？"。后者在本模块里**结构上无法被回答**：
`ReviewSet` 没有概率字段，`ReviewSet` 类上也没有任何写方法。

### 交付

| 交付项 | 位置 | 说明 |
| --- | --- | --- |
| 投影层 | `src/application/review_mode.py` | `ReviewSet`，只读，公开方法只有一个 `review_set()` |
| 应用层转发 | `src/application/workspace.py` | `student_review_set()` |
| HTTP API | `src/api/endpoints.py` | 1 条只读 GET 路由 |
| 学生界面 | `src/web/app.js` | `#/review` 复习页 + `rs.*` 三语词条 |
| 导航 | `src/web/index.html` | 顶部"考前复习"入口 |
| 测试 | `tests/test_review_mode.py` | 163 条（162 passed / 1 skipped） |
| 共享夹具 | `tests/support.py` | `build_conflicted_structure`（66/67 共用） |
| UI 审计 | `scripts/ui_audit.js` | 195 → 235 checks |
| UI 渲染 | `scripts/ui_render_check.js` | 119 → 145 checks |

### 路由

```text
GET /api/students/{student_id}/review-set?course_id=&lang=
```

未知课程 / 学生 → 404 `NOT_FOUND`；缺或空 `course_id` → 400 `INVALID_INPUT`。
对这条路径 **POST** 不被接受（只读契约）。

### 核心设计决定

1. **"不是预测器"是结构性的，不是纪律性的。**
   `ReviewSet` 上只有读方法，模块里也不存在任何概率类数据结构。所以测试
   可以**逐字段扫描响应体**来证明它（42 条 `TestNoPredictionContract`），
   而不是靠"我们记得不要写"。还有一个白名单断言
   `test_review_set_only_exposes_known_read_methods`：公开方法集合必须
   恰好是 `{"review_set"}` —— 谁想"顺手加一个写口子"都得先改这条。

2. **两条真相轴各自参与判桶。**
   一个 `validation_status=supported` 但 `review_status=pending` 的知识点
   **不能**进 `ready`。只读 validation 的实现会把它放进 ready，于是学生
   以为拿到了已定稿的复习材料 —— 而事实上没有第二个人核过。
   判桶规则里那句 `if review != "confirmed": return "attention"` 就是这个
   要求的可执行形式。

3. **冲突绝不被自动解决，也绝不会"永远阻塞"。**
   两个方向都要守住，只守一边就是 bug：
   - `resolve_conflict` 要求调用方**显式选出**信任的一侧（空选择被领域层
     拒绝："an empty selection would not actually resolve the conflict"）；
   - 但领域层解决冲突时**只**把 `review_status` 推到 `CONFIRMED`，
     **不会**把 `ConflictRecord.status` 改成 `RESOLVED`（记录按设计为审计
     保留）。

   所以只按 `conflict.status` 判定会让已解决的冲突**永远**把知识点按在
   `blocked` 上。最终判据是两个条件的合取：

   ```
   conflicts and review != "confirmed"  ->  unresolved_conflict
   ```

   这条是本次开发中发现的真实缺陷（见下）。

4. **已解决的冲突仍返回，只是 `blocking=False`。**
   `get_conflicts` 按设计返回全部冲突（去重但不筛状态），因为
   "The ConflictRecord and both evidence items are preserved for audit"。
   因此响应体里加了一个 `blocking` 标志，让 UI 能区分"还在拦着"与
   "已处理但记录留档"。

5. **`attention` 必须说清原因。**
   每个 attention 项都带 `attention_reason`（三选一）+ `attention_note`，
   而不是笼统地标一个"低优先级"。

6. **确定性。** 排序键为
   `(证据状态秩, 冲突有无, 学习状态, 知识点 ID)`，无随机数无时间戳。

### 桶的语义

| 桶 | 含义 | 判据 |
| --- | --- | --- |
| `blocked` | 先解决，不能当复习材料 | 有未解决冲突，或被人工否决 |
| `attention` | 先确认/补证据再看 | StudentState 说要看 / 证据不到 supported / 人工未确认 |
| `ready` | 可以正常复习 | 证据支持 **且** 人工已确认 **且** 无需重看 |

### 本轮修出的真实缺陷

**缺陷 1 — 已解决的冲突会永久阻塞。**
最初 `block_reason` 只看 `conflicts` 非空。但领域层的 `resolve_conflict`
不改 `ConflictRecord.status`（记录要留档审计），只推 `review_status`。
于是人工一旦解决冲突，知识点会**永远**停在 `blocked` ——
"绝不自动解决"变成了"永远无法解决"。
修法：判据改为 `conflicts and review != "confirmed"`，并新增
`test_resolved_conflict_no_longer_blocks` 与
`test_resolved_conflict_stays_resolved_after_restart` 两条守卫。

**缺陷 2 — 冲突夹具只改内存，重启类测试测了个寂寞。**
最初的 `_inject_conflict` 调 `org_service.register_knowledge_structure()`，
那只改内存。于是 `TestRestart` 里的冲突在重启后**整个消失**，断言
"冲突跨重启存活"实际上失败在一个夹具局限上，而不是产品行为上。
修法：改用 `persistence.load_knowledge_structure()` → 加冲突 →
`save_knowledge_structure()` → `reload_course()`，即产品真实路径。
（`probe67f.py` 先确认了真实处理流程产出的冲突本来就是持久化的，
所以这确实是夹具问题而非产品问题。）

**缺陷 3 — 只读守卫自己产生了假阳性。**
`test_review_set_has_no_write_methods` 用子串匹配字段里的
`"confirm|reject|...|set|..."`，结果把 `review_set` 自己当成了写方法
（"set" 是它的子串）。一条总是失败的守卫等于没有守卫。
修法：改用完整词正则 + 一条白名单断言。

**缺陷 4 — 三个 CSS 类名只有用法没有定义（Task 66 顺带修出）。**
`.block-label`（Task 60）、`.conflict`（Task 62）、
`.path-chain` / `.path-node`（Task 41，`status.md` 声称已加其实没加）
在 `app.js` 里被写出但 `styles.css` 里没有。页面只是"没样式"不是"报错"，
所以既有检查全测不出来。已补齐，并新增静态检查（见 Task 66 一节）。

### 新增/加强的守卫

- `ui_audit.js` 新增 `every class emitted by app.js is defined in styles.css`
  （3 条：两个"扫描非空"前提 + 一条逐名比对）。**变异测试证明有效**：
  把 `.block-label` 改名后正确报出。第一版没剥 CSS 注释导致假阴性，
  剥注释后同一变异体被捕获。
- `ui_audit.js` / `ui_render_check.js` 各新增复习页检查：六条中文禁止语、
  英文预测词、两条轴分列、三桶齐全、冲突两侧并列且不预判、不称"自动解决"。
  **变异测试证明有效**：把 `rs.subtitle` 改成"按考试概率排序，最可能考的在前"
  后，两条检查立刻报错并点名。
- i18n 动态前缀 `rs.block.*` / `rs.reason.*` 已注册到两个 i18n 守卫的
  取值域里。**变异测试证明有效**：删掉 es 的 `rs.twoAxesNote` 后，
  三语 key parity 与 es CJK 泄漏两条同时报错。

### 门禁复跑结果

```text
tests/test_review_mode.py                      162 passed, 1 skipped
tests/test_learning_workflow.py (Task 66)      102 passed
compileall -q src tests                        exit 0
scripts/ui_audit.js                            235 checks  OK
scripts/ui_render_check.js                     145 checks  OK
pytest (full)                                  见本节末
```

### 变更文件

- `src/application/review_mode.py`（新增）
- `src/application/workspace.py`（`CourseContext.review_mode` + `student_review_set`）
- `src/api/endpoints.py`（1 条 GET 路由）
- `src/web/app.js`（`rs.*` 三语表、复习页、`#/review` 路由）
- `src/web/index.html`（导航项）
- `tests/test_review_mode.py`（新增，163 条）
- `tests/support.py`（新增，共享冲突夹具）
- `tests/test_learning_workflow.py`（本地冲突 helper 改为复用共享夹具）
- `tests/test_learning_view.py` / `tests/test_exercise_ui.py`（注册动态前缀）
- `scripts/ui_audit.js`（复习页 + 40 检查）
- `scripts/ui_render_check.js`（复习页 + 26 检查）

## Task 68 — Multi-Course Workspace（多课程工作台）

### spec 里最关键的一句，以及它决定了什么

> 内容寻址的 Knowledge ID 跨课程**碰撞是合法的** —— 两门课用了同一份讲义，
> 就会得到同一个 `knowledge_id`。

这一句把实现方向整个翻过来了：要做的**不是**"让 id 全局唯一"，而是
"每一次查询都必须带 `course_id`"。所以本任务的存储层是**加一张课程作用域的
归属表**，而不是把 `knowledge_points` 的主键改成复合主键 —— 后者在 SQLite 里
只能重建表，而重建会让五张子表的外键在运行期报 `foreign key mismatch`
（已实测，见"踩过的坑"）。

### 实现

- `src/persistence/migrations/m003_course_scoped_identity.py`（新增）：
  纯增量迁移，新增 `course_knowledge_points` / `course_knowledge_evidence` /
  `course_students` / `course_review_records` / `course_conflicts` 五张表，
  回填全部用 `INSERT ... SELECT ... ON CONFLICT(...) DO NOTHING`。
  冲突表回填的 `course_id` 由证据链反推（`conflict_evidence →
  material_evidence → materials.course_id`）。
- `src/application/multi_course.py`（新增，只读投影）：对外只有
  `my_courses` / `course_summary` / `resolve_selection` 三个方法。
  每个计数都是一条**带 `course_id` 的独立查询**，绝不是"先合并再分组"。
  `_totals()` 把两条真相轴**分开**求和，永不合成"完成度"。
- `src/api/endpoints.py`：`GET /api/my-courses`、`GET /api/course-selection`、
  `GET /api/courses/{course_id}/summary`。
- UI：`#/courses` 我的课程页 + 顶栏课程切换器 + 三语 `mc.*` / `val.*` /
  `rev.*` 文案 + `.mc-counts` / `.mc-axis` / `.card-current` 样式。
  切换规则由后端 `/api/course-selection` 判定，前端不写第二遍。

### 修掉的四个真实缺陷

**缺陷 1 —— 跨课程知识点泄漏（本任务最严重的一条）。**
三门结构完全相同的课，知识点数分别是 **28 / 48 / 68**：后建的课凭空包含了
先建的课的全部知识点。根因是 `process_session` 调 `assemble_knowledge()`
不传作用域，走 `process_store` 装配的是**整个共享证据库** —— 证据库是全局的
（内容相同的材料产出同一个 `evidence_id`），所以作用域只能由调用方给。
修法：`assemble_knowledge()` 的**默认作用域 = 本门课**。修完 28 / 28 / 28。

**缺陷 2 —— 作用域一收窄，冲突就消失了（31 条测试挂掉）。**
第一次修是把作用域收到"本次作业产出的证据"，串课修好了，但验收数据集里的
CONFLICTED 全部消失：同一门课里跨材料 / 跨节课的矛盾证据组不成冲突了。
冲突不是可选项，是 spec 要求的真值状态。修法：默认作用域取**整门课**
（既能防串课，又能组出课内冲突）。两个方向现在都有守卫：
`test_every_course_has_the_same_number_of_knowledge_points`（防串课）与
`test_fixture_actually_contains_a_conflict`（防冲突丢失）。

**缺陷 3 —— 重新处理会把人工复核决定悄悄清空（顺带修出）。**
旧的 `process_store(self._store)` **不传 structure**，等于每次装配都从空结构
重建，知识点对象换新的，`review_status` 回到 `pending` —— 用户重跑一次"处理"，
之前逐条确认过的知识点全部回到待审，人工劳动被静默丢弃。领域层
`KnowledgeReviewService.get_review_candidates` 的规定是相反的：
human decisions are sticky，只有新证据 / 新冲突才重新开队列。
现在装配复用已有 structure，决定留得住。
`tests/test_hardening_truth_safety.py::test_reprocessing_does_not_reopen_a_human_decision`
把这条钉死。

**缺陷 4 —— 重启丢知识点 / 悬空证据被当成知识（自己引入又修掉）。**
`_restore_course_state` 改写时删掉了 spec 50 的硬规则"证据没了就不是知识"。
修法：`KnowledgeRepository._surviving_evidence()` + `load_structure()` 过滤。
刻意**不**回到旧的"由证据反推课程归属"写法 —— 那正是丢数据的原因。
过滤是课程无关的：只问"这条证据还在不在"。

### 踩过的坑（都有对应守卫）

- **复合主键 + SQLite 外键**：改 `knowledge_points` 主键 = 重建表 = 五张子表
  运行期 `foreign key mismatch`。已实测，因此走增量迁移。
  `test_no_migration_drops_a_table` / `test_no_migration_recreates_an_existing_business_table`
  把"只能增量"钉死。
- **迁移之后，写老表的测试会静默失效**：`TestCorruptedKnowledgeData` 只破坏
  `knowledge_points`，而 `load_all(course_id=...)` 读的是
  `course_knowledge_points`。已引入 `_KNOWLEDGE_PAYLOAD_TABLES` 两张都破坏。
- **写死"迁移链有 2 条"的断言**：改成从 `MIGRATIONS` / `latest_version()` 推导。
- **`t('val.' + key)` 拼前缀**：漏译时 `t()` 原样返回 key，界面上出现
  `val.confirmed` 而静态检查抓不到。改成逐个字面写出，并在两个 Node 检查里
  加了"my courses 页面不得漏出裸 key"的正则。
- **否定句里出现被禁词**：es 副标题写"sin orden recomendado"来声明"没有排序
  推荐"，结果被无排序话术扫描判违规。改成不出现被禁词，意思交给 `mc.noRanking`。
- **模块级夹具被写测试污染**：`test_a_new_course_appears_after_a_restart`
  往 `big` 夹具的课程目录里加了一门课，后面的只读断言看到 4 门课。
- **直接调 service 层不会落盘**：只有 `Workspace` 这层才 `_flush_*`。
  重启类测试必须走 `workspace.record_learning_event(...)`。
- **验收夹具把流程跑了两遍**：`harness` 跑一次，`report` 又跑一次，而第二次
  跑在修复后拿不到复核候选。已改为 `harness.last_report`，并在夹具文档里
  写明为什么不能再跑一遍。

### 门禁复跑结果

```text
tests/test_multi_course.py                      136 passed
pytest (full)                                   5065 passed, 10 skipped
compileall -q src tests                         exit 0
scripts/ui_audit.js                             275 checks  OK
scripts/ui_render_check.js                      163 checks  OK
```

### 变更文件

- `src/persistence/migrations/m003_course_scoped_identity.py`（新增）
- `src/persistence/migrations/__init__.py`（迁移链 2 → 3）
- `src/persistence/repositories/{base,knowledge,review,student}.py`
- `src/persistence/models/{tables,snapshot}.py`、`src/application/persistence_wiring.py`
- `src/application/multi_course.py`（新增）
- `src/application/workspace.py`（3 个 façade + 装配作用域 + 证据存活过滤）
- `src/application/processing_service.py`（`assemble_knowledge` 默认课程作用域）
- `src/application/acceptance.py`（`last_report`）
- `src/api/endpoints.py`（3 条 GET 路由）
- `src/web/app.js` / `index.html` / `styles.css`
- `scripts/ui_audit.js`（+40 检查）/ `scripts/ui_render_check.js`（+18 检查）
- `tests/test_multi_course.py`（新增，136 条）
- `tests/test_hardening_truth_safety.py`（+1 条：人工决定是黏的）
- `tests/test_acceptance.py`（`report` 夹具不再重跑流程）
- `tests/test_persistence_{knowledge,roundtrip,database,workspace}.py`
- `tests/test_learning_view.py` / `test_exercise_ui.py`（注册 `mc.count.*`）

## Task 69 — Real Semester Stress（真实学期规模压力）

完整报告见 `docs/task-69-stress-report.md`。这里记结论与决策。

### 规模（实测，不是估算）

```text
5 门课 / 75 节课 / 750 份材料 / 3000 知识点 / 100 主题
501 个学生 / 5000 道题 / 20003 条作答 / 数据库 47.9 MB
整学期构建 124 秒，重开整个学期 0.270 秒
```

### 这一轮最大的收获：4 个真实缺陷，全是"每一条查询都乘以了数据量"

功能测试跑不出这类缺陷 —— 几份材料的夹具上它们完全不可见，但学生认真学
一个学期之后就会退化成 O(N²)。四个缺陷按发现顺序：

1. **提交一条作答重写整门课**（`_flush_learning`）：68 ms/条 → 1.89 ms/条。
   修法是给 `_flush_learning` 加 `student_ids` / `exercise_ids` /
   `answer_ids` 三个增量作用域参数，**空列表 = 一个都不写**，与
   `None` = 全部写严格区分。
2. **冷启动按知识点查溯源链**（`load_structure`）：600 知识点 = 600 条 SQL。
   修法是 `CourseLinkRepository.rights_for_many` 一条 `IN (...)` 分批拿回。
   冷启动 96 → 恒定 17 条。
3. **冷启动按学生读学习日志**（`_load_course_state`）：3 条 SQL/学生，
   500 个学生 = 1500 条。修法是 `load_student_logs` 批量读。
   冷启动 81 → 恒定 21 条。
4. **提交一条作答重写这个学生自己的全部历史**（`save_student_log`）：
   实测答到第 400 条时提交下一条要 410 条 SQL。修法是事件按
   `existing_ids` 跳过已落盘的（`event_id` 由内容派生，跳过与重写
   **逐字节等价**），状态读回比对只写真变了的。410 → 恒定 10 条。

### 一个判断错误，已纠正

缺陷 4 一开始被写进测试类的文档字符串，说成"事件流 append-only 的固有
语义，不是缺陷"。这是错的：append-only 要求的是"已有的不会被改写"，**不**
要求"每次都把已有的再写一遍"。学生的历史是无界的，所以这一条和缺陷 1
是同一类问题，只是维度从"课内有多少别人"变成"我自己答过多少"。发现之后
连同那段文档一起改掉了。

### 判据的选择：为什么不压测绝对耗时

本机同时跑着 IDE 和其它进程，毫秒数抖动很大，把"`my_courses()` 必须小于
50ms"写成断言只会得到一条随机失败的测试。真正能抓住缺陷的判据是**复杂度**：

- 同一操作的 SQL 条数必须与数据量无关（大库小库相等）；
- 单条作答的 SQL 条数必须是常数。

SQL 条数用 `sqlite3.Connection.set_trace_callback` 统计 —— 给 `Database`
打猴子补丁会漏掉 `query_one` / `scalar`。

### 踩过的坑（都有对应守卫）

- 学号用 `course_id[:6]` 生成会撞车（内容寻址的前缀不保证唯一），必须用
  **课程序号**；`for cid in course_ids:` 忘了 `enumerate` 会让序号残留。
- 同一秒内三次备份撞 `DuplicateBackupError`（归档名来自时钟）—— 每轮注入
  不同的固定时钟值。
- `create_backup()` 返回 `BackupResult`（`archive_path`），`list_backups()`
  返回 `BackupInfo`（`path`）—— 不是一个类型。归档里的数据库叫
  `database.sqlite`，不是运行时的 `classroom.sqlite`。
- `answer_id` 是内容寻址的：只按 `i % n` 循环会让四元组重复，20000 条作答
  去重后只剩 1000 条。必须 `sequence = i // len(own)` 做成双射。
- 第一次写某张表时 sqlite 驱动会先探一次表结构（`PRAGMA table_info`），
  把首条测量抬高 6 条左右 —— 数 SQL 的测试必须先预热。
- 按 `course_id` 字典序 `enumerate` 会把课程序号对错位（标签 `c{i}` 是
  创建顺序），表现为"每门课的知识点都带着别门的标签"—— 假阳性。
- `IN (...)` 分批（500/批）会让大库比小库多一两条语句。这是 O(N/500) 的
  常量级差异，不是 N+1（真 N+1 差 600 条），所以判据是
  `abs(big - small) <= 2` 而不是 `==`，理由写进常量注释。

### 门禁复跑结果

```text
tests/test_stress_semester.py            60 passed              (10:39)
pytest (full)                            5125 passed, 10 skipped  (18:40)
compileall -q src tests                  exit 0
scripts/ui_audit.js                      275 checks  OK
scripts/ui_render_check.js               163 checks  OK
```

### 变更文件

- `src/application/workspace.py`（`_flush_learning` 增量作用域 + 5 处调用点
  窄化 + 4 处冗余整课 flush 删除 + `_load_course_state` 批量读）
- `src/persistence/repositories/base.py`（`rights_for_many`）
- `src/persistence/repositories/knowledge.py`（`load_structure` 批量取溯源链）
- `src/persistence/repositories/student.py`（`load_for_course` ×2、
  `existing_ids`、`rows_for`）
- `src/persistence/snapshot.py`（`load_student_logs` 批量读、
  `save_student_log` 增量写）
- `src/application/persistence_wiring.py`（`load_student_logs`）
- `tests/test_stress_semester.py`（新增，60 条）
- `scripts/ui_stress_check.js`（新增，真实 HTTP 的 12 页 UI 压力检查）
- `tests/test_persistence_transactions.py`（回滚测试从"数写了几条"改成"断言
  必须写到哪些落盘点"）

## Task 70 — Release 1.0（发布终审）

完整报告见 `docs/task-66-70-final-report.md`（24 章）。这里记结论与新增门禁。

### 版本号升到 1.0.0

`0.37.0 → 1.0.0`。理由不是"凑整数"，而是这一阶段**改了对外契约**：Task 68
新增路由 + 课程作用域归属表（增量迁移 m003），Task 69 改了落盘范围语义，
Task 70 新增"用户输入错误不得产生 500"的发布级契约。五处真源一致，由
`test_production_gate.py::EXPECTED_VERSION` 与
`test_release_gate.py::TestVersionRelease` 双重钉住。

### 新增的门禁：tests/test_release_gate.py（22 条）

`test_production_gate.py` 已有 A–J 十个门（持久化重启矩阵 / 50 知识点溯源 /
真值安全 / 确定性 / 幂等 / 依赖 / 规模足迹 / 文件与库一致 / 构建卫生 / 版本与
文档）。Task 70 补的是它**没覆盖**的那一层：

1. **API 契约门** —— 用户输入错误**永远**是 4xx + 标准信封，绝不能是 500。
   这条是**穷举**式的（挑几个端点写几条断言会漏掉新加的路由）。实测路由表
   58 GET / 19 POST / 1 PATCH：
   - 每个带占位符的 GET × 13 个坏值 = 481 次请求
   - 每条 GET 路由不带必填 query = 58 次
   - 每条写路由 × 4 个畸形 body = 76 次
   合计 615 次，**0 个 500**。同时断言 4xx 必须是标准信封且不含 Python
   堆栈。
   > 非 `/api/` 的未知路径会回退到 SPA 首页，那是**故意**的前端路由不是
   > 错误，所以门禁只判 `/api/` 前缀，并单独断言 SPA 回退返回外壳页面。
2. **依赖门（AI 供应链）** —— 全 `src/` 扫描 LLM API / LLM 框架 /
   embedding 与向量库 / 云 SDK，**0 命中**。`test_production_gate.py` 的
   `FORBIDDEN_TECHNOLOGY` 守的是数据库与消息队列那一侧，这里补的是另一侧。
   `torch` 是唯一例外（只在 `whisper_provider.py` 的 CUDA 分支里惰性
   import，不参与推理），这条例外被**单独钉住**：每处 `torch` 引用的上下文
   里都必须出现 `cuda`，否则"例外"会悄悄变成"口子"。
3. **安全门** —— 无疑似密钥字面量；服务只监听回环；静态目录不得被 `..`
   穿越（判据看**内容**，不是状态码）；登记 data_dir 之外的材料时产品把它
   **收进** data_dir（材料是副本不是指针，备份才真能带走内容）。
4. **全新安装门** —— 空目录起服务 → health `ok` → courses 为空 → 能建课 →
   数据库文件真的生成。
5. **版本门** —— 1.0.0 在每一处都是同一个值，并列出全部真源。

### 测试稳定性调查

- 静态：`tests/` 里唯一的 `import random` 用的是**固定种子**
  `random.Random(47)`；产品代码的 `datetime.now` / `uuid4` 由
  `test_determinism_audit.py` 守卫；唯一的等待型逻辑是子进程握手轮询
  （超时 120 秒，等标记文件而非等固定时间）。
- 动态：全量回归连跑两遍 —— 第 1 遍 `5146 passed, 10 skipped, 1 failed`
  （那 1 个是自指的：断言"最终报告必须存在"，而报告要等回归数字），第 2 遍
  `5147 passed, 10 skipped, 0 failed`。skip 集合相同。
- 结论：没有发现随机失败的来源。

### 门禁复跑结果

```text
tests/test_release_gate.py            22 passed
pytest (full)                         5147 passed, 10 skipped  (21:06)
compileall -q src tests               exit 0
scripts/ui_audit.js                   275 checks  OK
scripts/ui_render_check.js            163 checks  OK
```

### 变更文件

- `tests/test_release_gate.py`（新增，22 条）
- `docs/task-66-70-final-report.md`（新增，24 章）
- `src/application/workspace.py` / `src/__init__.py` / `tests/__init__.py`
  / `tests/test_production_gate.py` / `docs/final_report.md`（版本 1.0.0）

### 结论

```text
FINAL VERDICT: PASS
RELEASE: 1.0.0
```

## Task 71–75 — Real-World Pilot & 1.0.1 Stabilization

完整报告见 `docs/task-71-75-final-report.md`（24 章）。数据质量见
`docs/task-72-data-quality-report.md`，稳定性 / 恢复 / 性能见
`docs/task-74-stability-report.md`。这里记结论与新增门禁。

本阶段优先级：**真实用户问题 > 数据正确性 > 稳定性 > 恢复能力 > 性能 > UX >
新功能**。停止无目的扩展功能，只在发现真实缺陷时修复。

### 版本号升到 1.0.1

`1.0.0 → 1.0.1`。理由是存在**真实用户影响**的修复（数据隔离守卫、Pilot 操作
日志、数据质量报告），不是"凑版本号"。五处真源一致：`src/__init__.py`、
`src/application/workspace.py`、`tests/__init__.py`、
`tests/test_production_gate.py::EXPECTED_VERSION`、`tests/test_release_gate.py`。

### 修复的真实缺陷：测试在仓库根就地建库

`tests/test_api_server.py` 的 `Workspace(".")` 会在项目根就地创建 `database/`
`materials/` 等目录，**污染源码树与真实 Pilot 数据**。修复为 `tmp_path` 隔离，
并加数据目录守卫（`src/application/data_dirs.py` / `config.py`）使任何代码路径
都无法把仓库根 / `src` / `tests` 当成数据目录。

> 这条顺带解释了仓库根的 `database/classroom.sqlite` 从何而来 —— 它是修复前的
> 遗留产物，**不是**真相源（真相源是 `classroom-data/database/classroom.sqlite`）。

### 新增能力

- **71.2 数据隔离** —— Pilot（`classroom-data`）与测试（`data-test` /
  `tmp_path`）彻底分离。测试 `tests/test_pilot_data_isolation.py`。
- **71.5 错误 UX** —— 用户可见错误统一为结构化信封（code + message），绝不暴露
  `KeyError` / `TypeError` / `Traceback` / `SQLiteError`。测试
  `tests/test_pilot_error_ux.py`。
- **71.6 处理可见性** —— 材料状态机 `REGISTERED / VALIDATING / PROCESSING /
  COMPLETED / FAILED`，绝不永久 `loading...`。测试
  `tests/test_processing_visibility.py`。
- **71.7 Pilot 操作日志** —— 新增 `src/application/operation_log.py`，记录
  `operation / timestamp / course / session / material / success / duration`；
  隐私安全（密钥整体替换、内容 200 字摘要、写入失败绝不抛出）。测试
  `tests/test_pilot_operation_log.py`。
- **72 数据质量** —— 新增 `src/application/data_quality.py::collect_data_quality`，
  统计 `materials processed/failed`、`evidence`、`knowledge`、`unverified`、
  `conflicted`、`broken traces`、`duplicates`、`unsupported formats`。测试
  `tests/test_data_quality.py`。

摄取层的硬约束：**不支持格式 → `UNSUPPORTED_EXTENSION`（显式失败，不伪造）；
缺失 OCR / ASR 运行时 → `feature unavailable`（不伪造证据）。**

### 终审门禁（21 项全 PASS）

Fresh Install / Human Review / Student Learning / Mistake & Weak Knowledge /
Multi-course Isolation / Truth Safety / Data Isolation / Persistence / Restart /
Backup & Restore / i18n / UI Audit / UI Render Audit / API Contract / Security /
Dependency / Test Stability / Full Regression / Compileall / Pilot Operation Log /
Data Quality Report。

- i18n：`zh == es == ca` 逐键对齐（各 370 条），无缺译 / 裸 key / 误翻中文。
- 完整非 integration 回归：**5148 passed, 5 skipped, 0 failed**（该发布时点的
  实测），权威结果见 `logs/regression-archive/pytest_101_final.txt`。
  > 口径说明：这是 Task 71–75 发布时点的数字。后续工作（前端零构建拆分与本次
  > 六项收敛）把套件增长到 5227 passed，见本文末尾区块。

```text
FINAL VERDICT: PASS
RELEASE: 1.0.1
```

### 已知限制（照录，不掩饰）

1. 绝对耗时不做断言（本机抖动大）；性能门禁断言的是**复杂度**（SQL 条数为
   常数、与数据量无关），不是毫秒。
2. 10 个 integration 测试（真实 Whisper / OCR 模型未下载）skip，与 1.0.0 一致。
3. 注册期拒绝的不支持格式不进入持久化注册表，因此持久化视图
   `unsupported_formats` 为 0；unsupported 由摄取响应显式上报。

## 修复 — 高亮 / "当前选择"与所在位置不一致（课程选择 / 顶栏导航 / 当前学生）

Goal
----------------------------------------
修四个同族缺陷，第一个由用户截图报出，另三个是顺着同一族查出来的：
**"选中的/所在的"和"高亮的"不是同一个**，或**内存状态与持久化状态脱钩**。

**缺陷 A（用户截图）**：侧边栏高亮 `Bases per a la Geoinformació`
(`course-3fd6392d1fd78e87`)，顶栏"切换课程"也停在同一门，但主视图标题是
`Digitalització i Microcontroladors` (`course-6ea554f295431036`)。
用户点的是后者 —— 高亮和切换器停在了**上一门课**。

**缺陷 B**：路由里的 course_id 若不存在（手输错 / 失效书签 / 课程被删之后的深链），
它会被写进 `state.courseId` 与 `localStorage`，后果三重：侧边栏**没有任何**高亮；
顶栏切换器找不到匹配项、退回显示第一项（显示的又不是"当前课程"）；此后每个页面
都拿着脏 id 去请求，**全部 404**，用户被卡在错误页上直到重新点一门课。

**缺陷 C**：顶栏导航的高亮脱钩。`pageLearnKnowledge()` 是 19 个页面函数里**唯一**
不调用 `markActiveNav()` 的，而 `index.html` 的 topnav 里没有任何硬编码 `active`
—— 于是：在 `#/learn/<kp>` 上**刷新**，整条导航都没有高亮；从别的页面点进来，
高亮停在上一页。两种都是"高亮的位置不是用户所在的位置"。

**缺陷 D**：当前学生的内存状态与持久化状态脱钩。6 处页面函数各自写一遍
`state.studentId` + `localStorage`，其中 `pageExercise()` **漏了持久化那一步**。
从错题本点「Practice Again」（链接是 `#/courses/<c>/exercises/<e>/<sid>`，带学生 id）
进去之后，内存里是 sid、`localStorage` 里还是上一个学生 —— **刷新一下学生就换人了**。

四个缺陷的形状是同一个：**同一条规则被抄了 N 遍，其中一遍抄漏了。**
所以修法一律是"收口 + 声明表"，而不是"把漏的那一遍补上"（补上只是修好第 N 份副本）。
另有**两项预防性**改动（不修任何现存缺陷，只是把不变量写下来并让它可断言），
见「设计取舍」第 10、11 条 —— 如实分开标注，不混进上面 4 个缺陷里。

缺陷 A 的根因
----------------------------------------
`route()` 的执行顺序是 `loadChrome()` → `loadSidebar()` → `pageXxx()`：

1. `loadSidebar()` 先画侧边栏与顶栏切换器，高亮取 `state.courseId`（此刻还是
   上一门课 A）；
2. 课程页 / 复习中心这类页面函数要到**之后**才从路由里拿到 course_id 并
   `setCourse(B)`；
3. 而 `setCourse()` 只写 `state.courseId` 与 `localStorage`，**不重绘**已经画好的
   侧边栏与切换器。

于是主视图换了课，左侧高亮与顶栏选择器却留在 A —— 且这个不一致是**稳定**的，
不是一闪而过，直到下一次路由（点别的导航）才会自己纠正。

同一缺陷的第二个来源：`pageLearnKnowledge()` 与 `pageReview()` 绕过
`setCourse()` **直接写** `localStorage.setItem('ca.course', courseId)`，
`state.courseId` 与持久化的值就此脱钩。

缺陷 B 的根因
----------------------------------------
`setCourse()` 修好之后，**所有**从路由拿 course_id 的页面函数都会把那个 id
无条件提交为"当前课程"。但路由里的 id 是**请求**，不是既成事实 ——
`/api/courses/<不存在的 id>/workspace` 实测返回 `404 NOT_FOUND`，
`/api/dashboard?course_id=<不存在的 id>` 同样 404，而 `state.courseId` 已经被污染。

缺陷 C 的根因
----------------------------------------
顶栏的 `active` **完全由页面函数设置** —— `index.html` 里没有任何硬编码
（第一个导航项是 `<a href="#/today">今日</a>`，干净）。`markActiveNav(hash)`
只会"把匹配项加上 active、把其余去掉"，**没人调用它就什么都不发生**。
所以漏调一个页面函数 = 那一页的顶栏高亮继承上一页；若是刷新后直接落在这一页，
则整条导航都没有高亮。

缺陷 D 的根因
----------------------------------------
"当前学生"这条规则被**抄了 6 遍**，而 6 遍里有 1 遍抄漏了：
5 处写成"设 state + 写 localStorage"两行，`pageExercise()` 只写了 `state.studentId`
那一行。同一个规则写 N 遍，漏掉其中一遍几乎是必然的 —— 这和缺陷 A 的第二个来源
（两处绕过 `setCourse()` 直接写 localStorage）是同一个形状。

设计取舍
----------------------------------------
1. **让 `setCourse()` 成为唯一的课程选择入口，并让它负责重绘。**
   没有选"在 route() 末尾补一次重绘"：`setCourse()` 还被表单提交、切换器
   change、`requireCourse()` 回落等路径调用，把不变量钉在它身上，一处收口、
   处处成立。备选方案（在 `pageCourse` 里把 `setCourse` 挪到 fetch 之后）被否掉 ——
   那会让"高亮"要等数据回来才更新，把现有的即时反馈拖慢。

2. **抽出 `courseListHtml(courses)`，高亮规则只留一份。**
   原来侧边栏的高亮是在 `loadSidebar()` 里内联算的；要在 `setCourse()` 里重绘，
   就必须把它抽出来。顺手消掉了"同一规则写两遍迟早漂移"的隐患。

3. **`__courseCache` 缓存课程列表。**
   重绘不能再发一次 `/api/courses` 请求 —— 那是把一个纯显示动作变成一次网络往返，
   也会在失败时把侧边栏换成错误态。缓存只存 `loadSidebar()` 已经拿到的那份列表。

4. **缓存为空时 `syncCourseChrome()` 直接返回。**
   列表还没加载 / 加载失败时没有任何可同步的显示，而贸然重绘会把"课程列表加载失败"
   的提示覆盖成空列表 —— 那是把错误藏起来。

5. **路由来的 course_id 走 `setRouteCourse()`，多一道"存在性"闸门。**
   备选方案是"先 fetch，成功了再 `setCourse`"（不依赖缓存）。被否掉的理由是它有
   三处早退路径（`pageExercise` 无学生、`pageMistakeDetail` 无学生、
   `pageLearnKnowledge` 无学生）在早退前就要渲染课程相关的空态，改成"成功后提交"
   反而要把这些路径单独处理一遍。闸门用 `__courseCache` 判断，**不花任何请求**，
   且列表在每次路由时都会重新拉取 —— 对当前这次路由它是新的。
   缓存为空（列表加载失败）时**不阻断**：没有依据就不下判断。
   *（已知的窄边界：若在 `loadSidebar()` 与页面函数之间，另一个进程恰好新建了
   这门课，闸门会误拒一次。产品里没有建课 UI（建课走 API/CLI），窗口是毫秒级，
   且下一次路由自动纠正。已记录，不为此加一次额外请求。）*

6. **`setCourse()` 重绘的时机与"当前课程"的语义保持一致**：顶栏切换器的 change
   处理器本来就是 `setCourse()` 后跳路由，所以这次改动让两条入口（侧边栏点击 /
   顶栏切换）第一次真正走同一条收口路径。

7. **顶栏高亮的归属规则：跟随用户所在页面所属的顶栏分区；不属于任何分区的清空。**
   `pageLearnKnowledge` 属于「今天的学习流程」（`#/today` / `#/learn` / `#/learn/<kp>` /
   `#/courses/<c>/learn/<kp>` 都是这个分区），所以它跟 `#/today`。
   `pageMistakeDetail`（`#/mistakes/<c>/<k>`）跟 `#/mistakes`、
   `pageExercise`（`#/courses/<c>/exercises/<e>/<sid>`）跟 `#/exercises`、
   `pageCourseReview`（`#/review-center/<c>`）跟 `#/review-center`。
   而 `pageCourse` / `pageSession` / `pageKnowledgeDetail` / `pageStudent`
   （`#/courses/<c>`、`…/sessions/<s>`、`…/knowledge/<k>`、`…/students/<sid>`）
   是"课程内部"的详情页，**顶栏里没有对应的分区**，且各有多条进入路径
   （课程页 / 知识点 / 今日 / 学生列表 / 错题本…），于是保持 `markActiveNav('')`
   —— 清空。**关键不是"选哪一个"，而是"确定"**：不能再由"用户上一秒在看哪页"决定。

   这条规则**同时落在三个地方**，缺一就会漂移：
   1. `app.js` 里 `markActiveNav()` 上方的注释表（读代码的人能看到）；
   2. 每个页面函数里那一次调用（唯一执行处）；
   3. `test_the_top_nav_ownership_table_is_exactly_as_declared` 里的 `declared`
      字典（逐项钉死，任何一处改动都必须**刻意**同步）。
   第 3 条是这次补的：光有"恰好调用一次"的清点，目标本身仍可能悄悄变 ——
   而"被抄 N 遍的规则迟早有一遍漂移"正是这一族缺陷的成因。

8. **把 `.topnav a` 补进 harness 的 DOM 桩，而不是只加静态断言。**
   桩原本 `querySelectorAll` 恒返回空数组，`markActiveNav()` 在测试里是个空转
   调用 —— 整个顶栏高亮**从来没被验证过**。这正是缺陷 C 能活下来的原因：
   不是没写测试，是那块状态根本不可观测。补桩之后，"高亮恰好一项、且指向
   所在页面"才第一次成为可断言的事实。

9. **`setStudent()` 收口（缺陷 D）。**
   与 `setCourse()` 完全同构：内存与持久化一起改，唯一写入口。没有选"在
   `pageExercise` 里补一行 localStorage"—— 那只是修好第 6 份副本，第 7 份
   迟早还会漏；收口之后，"6 处各写一遍"这个形状本身消失了。
   顺带用 `test_the_student_choice_has_exactly_one_write_entry` 把
   `state.studentId = ` 的出现次数钉成 1（只在 `setStudent()` 里）。

10. **把"唯一写入口"从两条具体守卫升级成一条全量不变量（预防性，不是修缺陷）。**
    前两条守卫只盯 `course` 与 `student`。但这一族的形状是"某条规则被抄 N 遍"，
    所以真正该锁的是**不变量本身**：4 个 `ca.*` 持久化键各只有 1 个写入点、
    未登记的键会被抓出、5 个 `state.*` 字段各只有 1 个赋值点。
    实测扫描结果：`state.{lang, courseId, studentId, health, mistakeGroupBy}`
    **全部恰好 1 个赋值点**，`ca.{course, student, lang, mistakeGroup}` 也各 1 个
    —— 也就是说 `ca.lang` / `ca.mistakeGroup` **今天是对的**，但在此之前只有人工
    核对保证它是对的。**"目前是对的"不是"不会再错"**，所以把它们也钉住。
    这一步**没有修任何现存缺陷**，纯粹是把不变量写下来并让它可断言 ——
    如实标注，不把它算进上面那 4 个缺陷里。

11. **界面语言：存储值必须先校验，清单必须三处一致（预防性，不是修缺陷）。**
    两件事，都属于"同一族但今天没有可观测后果"，所以**单独标注、不算进那 4 个缺陷**：

    a. **`state.lang` 的初始读取此前没有校验**（`setLang()` 一直有）。
       `ca.lang` 是存储值 —— 可能是旧版本写的、手工改的、或来自一个曾支持更多
       语言的版本。不校验的后果不是"界面显示错"，而是那个非法值被**原样发给
       后端**，而 `normalize_language()` 会抛 `InvalidInputError`：用户看到一个
       他从没选过的语言的错误页。修法：抽出 `pickInitialLang()`，
       并把 `UI_LANGUAGES` 前移到 `const state` **之前**（否则初始读取落在 TDZ 上）。
       *为什么算预防性*：产品里没有任何路径会写出非法值，只有篡改 / 降级才会 ——
       今天不可达。但"不可达"靠的是"没有那条路径"，不是"不会出错"。

    b. **语言清单被声明了三遍，而守卫只盖住了两遍。**
       后端 `learning_view.py` 的 `UI_LANGUAGES` / `index.html` 的选项 /
       `app.js` 的 `UI_LANGUAGES`。既有守卫叫
       `test_ui_language_picker_matches_the_service_whitelist`，名字说的是
       "选择器要与服务端白名单一致"，实际**只数了 `<option>` 的个数**，
       而且**完全没检查前端那份清单**。于是：把某个选项的值从 `zh` 改成 `en`
       （个数不变）照样通过；给前端多添一种语言也照样通过 —— 而后者会让前端
       接受一种后端不认的语言，正好触发 (a) 里的那个错误页。
       修法：逐项比对三处的**取值**。

    这两条都没修任何现存缺陷（今天的界面语言选择完全正常），纯粹是把
    "不变量"写下来并让它可断言。

交付文件
----------------------------------------
修改：

- `src/web/app.js`
  - `setCourse()`：新增第 3 项职责（重绘"当前课程"两处显示），补上解释为什么
    它不是装饰的注释；明确写"唯一入口，不要绕过"。
  - 新增 `__courseCache` / `courseListHtml(courses)` / `syncCourseChrome()`。
  - 新增 `setRouteCourse(courseId)`：路由来的 id 先确认存在才提交。
  - `loadSidebar()`：改用 `courseListHtml()`；课程列表先入缓存再走选择判定；
    末尾统一 `syncCourseChrome()`。
  - 8 个从路由拿 course_id 的页面函数改用 `setRouteCourse()`：
    `pageCourse` / `pageCourseReview` / `pageSession` / `pageKnowledgeDetail` /
    `pageExercise` / `pageMistakeDetail` / `pageStudent` / `pageLearnKnowledge`。
  - `pageLearnKnowledge()`：补上 `markActiveNav('#/today')`（缺陷 C）。
  - 新增 `setStudent(studentId)`；6 处设"当前学生"的页面函数改用它（缺陷 D）：
    `pageLearn` / `pageLearnKnowledge` / `pageReview` / `pageExercise` /
    `pageMistakes` / `pageStudent`。
  - `pageLearnKnowledge()` / `pageReview()`：`localStorage.setItem('ca.course', ...)`
    → `setCourse()`（消掉第二个不一致来源）。
  - 新增 `pickInitialLang()`；`UI_LANGUAGES` 前移到 `const state` 之前
    （`state.lang` 的初始读取从"裸读存储"改成"校验后读"）。
  - `markActiveNav()` 上方补上归属规则注释表（19 个页面函数 → 11 个顶栏项）。
- `scripts/ui_render_check.js`
  - DOM 桩补上 `.topnav a`（`TOPNAV` / `makeNavLink` / `activeNav()`），
    让顶栏高亮第一次成为可观测、可断言的状态。
  - `makeSandbox()` / `loadApp()` 新增 `initialStorage` 参数 —— 必须在 app.js
    执行**之前**写 localStorage，否则"初始值"类用例是空跑（`const state` 已经读完）。
  - 把内联的学生首页 payload 抽成 `studentDashboard()` 夹具（新增用例要复用）。
  - 新增五组回归用例，共 **+39 checks**（163 → 202）：
    "当前课程的三处显示必须同源" 6 条、
    "路由里的 course_id 不存在时不得污染当前课程" 7 条、
    "顶栏高亮必须跟随用户所在的页面" 13 条、
    "当前学生的内存与持久化必须同步" 7 条、
    "存储的界面语言必须先校验再使用" 6 条。
- `tests/test_multi_course.py`：`TestUi` 新增 10 条结构守卫（136 → 146）。
- `tests/test_learning_view.py`：新增 1 条（143 → 144）+ **加强** 1 条既有守卫
  （语言清单 parity：从"数 `<option>` 个数"改成"逐项比对三处的取值"）。
- `docs/status.md`：本区块。

发现并修复的真实缺陷
----------------------------------------
**1. 主缺陷：`setCourse()` 不重绘"当前课程"的两处显示。**
根因见上。修复：`setCourse()` 收口 + `syncCourseChrome()`。

**2. 同族缺陷：两处绕过 `setCourse()` 直接写 `ca.course`。**
`pageLearnKnowledge()` / `pageReview()` 让 `state.courseId` 与 `localStorage`
脱钩。修复：改为调用 `setCourse()`；并用
`test_the_course_choice_has_exactly_one_write_entry` 锁住"只有一个写入点"。

**3. 同族缺陷：不存在的 course_id 会污染"当前课程"，并把用户卡在 404 上。**
实测（真实服务，只发 GET）：

```text
导航到 #/courses/course-does-not-exist
  修复前: 侧边栏高亮 (none) / 切换器选中 (none) / localStorage=course-does-not-exist
          再点"概览" → 抛出 NOT_FOUND  (用户被卡住)
  修复后: 侧边栏高亮 course-3fd6… / 切换器选中 course-3fd6… / localStorage=course-3fd6…
          再点"概览" → 正常
```

修复：`setRouteCourse()`。

**4. 同族缺陷：`pageLearnKnowledge()` 漏设顶栏高亮，且整块状态在测试里不可观测。**
实测（固定夹具，19 个页面函数里唯一漏掉的那个）：

```text
#/learn 入口 (#/learn)           : #/today
刷新后深链 #/learn/<kp>          : (无高亮)      <- 修复前
从 #/knowledge 点进 #/learn/<kp> : #/knowledge -> #/knowledge   <- 高亮没跟着走
                                  修复后: #/today 两处都对
```

修复：补 `markActiveNav('#/today')`；并把 `.topnav a` 补进 harness 的 DOM 桩
（在此之前 `markActiveNav()` 在测试里是空转 —— 这才是缺陷能活下来的真正原因）。

**5. 同族缺陷：`pageExercise()` 设了 `state.studentId` 却不持久化。**
实测（固定夹具）：

```text
上一个学生 = Ana, 从错题本点 Practice Again 进 Bob 的练习页
  修复前: 内存=stu-bob / localStorage=stu-ana   -> 不一致 (刷新后切回 ana)
  修复后: 内存=stu-bob / localStorage=stu-bob   -> 一致
对照 (学生详情页, 同样从路由拿 studentId)
  修复前/后: 内存=stu-bob / localStorage=stu-bob  -> 一直是一致的
```

修复：`setStudent()` 收口，6 处统一走它。

**6. 预防性（不算缺陷）：存储的界面语言未校验 + 语言清单的守卫只盖住两遍。**
今天**没有可观测后果**，所以如实标注为预防性，不混进上面 4 个缺陷：
`state.lang` 的初始读取没有校验（`setLang()` 有），非法值会被原样发给后端；
而"界面语言清单三处一致"的守卫只数了 `<option>` 个数、且完全没检查前端那份清单。
详见「设计取舍」第 11 条。

**7. 修复前先证明缺陷真实存在（不是靠读代码猜）。**
在 Node + 最小 DOM 桩里执行真实 `app.js`：

```text
缺陷 A  修复前: view=course-bbb / 高亮=course-aaa / 切换器=course-aaa  → INCONSISTENT
        修复后: view=course-bbb / 高亮=course-bbb / 切换器=course-bbb  → CONSISTENT
缺陷 B  修复前: 高亮 (none) / 切换器 (none) / 存储=脏 id / 再点概览 404
        修复后: 高亮=真实课程 / 切换器=真实课程 / 存储未污染 / 再点概览正常
缺陷 C  修复前: 刷新后无高亮; 从别的页面进来高亮停在上一页
        修复后: 两种都指向 #/today
缺陷 D  修复前: 内存=stu-bob / 存储=stu-ana
        修复后: 内存=stu-bob / 存储=stu-bob
```

测试清单（渲染 harness +39 checks / 结构守卫 +11 tests；另有 43 条真实端到端见下）
----------------------------------------
- `scripts/ui_render_check.js`（+39 checks，163 → 202）
  - 组 1「当前课程的三处显示必须同源」（6 条）：侧边栏在导航前高亮持久化的那门课 /
    课程页渲染的是路由里的课 / **侧边栏高亮跟随课程页（不是停在上一次）** /
    **顶栏切换器跟随课程页（不是停在上一次）** / 有且只有一门课高亮 /
    切换后不泄漏裸 i18n key
  - 组 2「路由里的 course_id 不存在时不得污染当前课程」（7 条）：
    后端错误仍然照实呈现 / 侧边栏高亮留在真实课程上 / 切换器留在真实课程上 /
    脏 id 不被持久化 / `requireCourse()` 不返回不存在的课 /
    后续页面照常工作（不被卡在 404）/ 概览页仍渲染真实课程
  - 组 3「顶栏高亮必须跟随用户所在的页面」（13 条）：
    5 个页面各"恰好一项高亮"且"指向自己的导航项"（`pageMyCourses` / `pageMistakes` /
    `pageExercises` / `pageKnowledge` / `pageLearn`）+ 刷新后深链到学习页高亮流程入口 +
    从知识点页进学习页高亮跟着移动（含前置对照）
  - 组 4「当前学生的内存与持久化必须同步」（3 条 → 7 条）：
    练习页从路由拿到 sid 后内存与存储一致 / 学生详情页同样一致（对照）/
    **内存与存储必须真的相等**（不止"内存等于期望值" —— 那两回事）/
    路由不带 sid 时沿用当前学生（且"当前学生"确实是从存储读来的 ——
    这条此前是**空跑**的，见下）/ 没有 sid 的路由也不会让两者脱钩 /
    没有任何路径会让存储留在上一个学生上
  - 组 5「存储的界面语言必须先校验再使用」（6 条）：
    非法 `ca.lang` 落回 `zh` / 非法值**绝不发给后端** /
    请求里带的必须是受支持的语言 / 受支持的值不被改写（`ca` 仍是 `ca`）/
    受支持的值出现在请求里（`lang=ca`）/ 空串也落回 `zh`

  > **顺带修掉两个"空跑 / 名不副实"的用例（值得单独记一笔）**：
  >
  > 1. 组 4 里 "路由不带 sid 时沿用当前学生"原本是**先 `loadApp()` 再
  >    `localStorage.setItem('ca.student', ...)`** —— 而 `const state = {...}`
  >    在 app.js 执行时就读完了 localStorage，所以 `state.studentId` 一直是 `null`，
  >    断言"存储没变"永远成立。给 `makeSandbox()` 加了 `initialStorage` 参数
  >    （在 app.js 执行**之前**写入），这条用例才第一次真的在测东西。
  > 2. "keeps memory and storage in sync" 第一版写成 `inMemory === 期望学生`，
  >    **根本没有比较内存与存储** —— 而"只写 state 不写 storage"正是要抓的那个
  >    缺陷，它偏偏能通过。改成真的比较之后才有效（由变异 4 发现）。
  >
  > **同一个坑在这个项目里已经出现过三次**（第三次是 DOM 桩的
  > `querySelectorAll` 恒返回 `[]`）——**"测试通过"不等于"被测到了"**。
- `tests/test_multi_course.py::TestUi`（+10 tests，136 → 146）
  - `test_the_course_choice_has_exactly_one_write_entry`
  - `test_set_course_repaints_the_current_course_chrome`
  - `test_the_sidebar_and_the_switcher_share_one_highlight_rule`
  - `test_route_supplied_course_ids_are_validated`
  - `test_every_route_driven_page_uses_the_validating_entry`
  - `test_every_page_function_sets_the_top_nav_highlight`（全量清点 19 个页面函数）
  - `test_the_top_nav_ownership_table_is_exactly_as_declared`
    （19 个页面函数 → 顶栏目标的**逐项**对照表，含"页面函数增减了"的检查）
  - `test_the_student_choice_has_exactly_one_write_entry`
  - `test_every_page_that_picks_a_student_goes_through_set_student`
  - `test_every_persisted_ui_choice_has_exactly_one_write_entry`
    （**全量**：4 个 `ca.*` 键各 1 个写入点 + 未登记的键会被抓出 +
    5 个 `state.*` 字段各 1 个赋值点）
- `tests/test_learning_view.py`（+1 test，143 → 144；另**加强**了 1 条既有守卫）
  - `test_the_stored_language_is_validated_before_use`（新增）：
    初始读取必须走 `pickInitialLang()`、规则必须是"在 `UI_LANGUAGES` 里才算数"、
    且 `UI_LANGUAGES` 必须定义在 `state` **之前**（否则初始读取落在 TDZ 上）。
  - `test_ui_language_picker_matches_the_service_whitelist`（加强）：
    原名就说"选择器要与服务端白名单一致"，但**只数了 `<option>` 的个数**，
    而且**完全没检查前端那份 `UI_LANGUAGES`**。现在逐项比对三处的**取值**：
    后端白名单 / `index.html` 的选项值 / `app.js` 的 `UI_LANGUAGES`。
    （原断言下，把某个选项的值从 `zh` 改成 `en`、个数不变，照样通过。）

变异测试（证明上面的断言不是空跑）
----------------------------------------
把每个修复逐条回退，再跑 harness（变异 1–5）与 Python 结构守卫（变异 6–7）。
下面所有通过数都是**用当前 harness（202 checks）重跑**的结果 —— 不是各次修复
当天的口径，否则数字会对不上。

```text
变异 1: 删掉 setCourse() 里的 syncCourseChrome()
  UI RENDER CHECK: FAILED
    - sidebar highlight follows the course page (not the stale one)
    - top-bar switcher follows the course page (not the stale one)
  200/202 checks passed

变异 2: 让 setRouteCourse() 不再校验、直接提交
  UI RENDER CHECK: FAILED
    - unknown route course keeps the sidebar highlight on the real course
    - unknown route course keeps the switcher on the real course
    - unknown route course is not persisted as the current course
    - requireCourse never returns an unknown course
  198/202 checks passed

变异 3: 删掉 pageLearnKnowledge() 的 markActiveNav('#/today')
  UI RENDER CHECK: FAILED
    - a fresh deep link to the learn page highlights the flow entry
    - navigating to the learn page moves the highlight off the previous page
  200/202 checks passed
  （Python 侧同样验证: 清点守卫精准报出 ['pageLearnKnowledge']）

变异 4: 把 pageExercise() 的 setStudent(sid) 换回只写 state
  UI RENDER CHECK: FAILED
    - the exercise page persists an explicitly routed student
    - the exercise page keeps memory and storage in sync
  200/202 checks passed

变异 5: 初始语言读取不校验（退回旧写法）
  UI RENDER CHECK: FAILED
    - an invalid stored language falls back to zh
    - an invalid stored language is never sent to the backend
    - the request carries a supported language instead
  199/202 checks passed

变异 6: "持久化选择只有一个写入点" 那条全量守卫（把变异源码字符串喂给同一套判据）
  基线 (未变异)                              -> PASS  (state 字段 5 个全部 1 个赋值点)
  在 setCourse() 之外再写一次 ca.course      -> FAIL  (写入点数量: ca.course=2)
  新增一个未登记的选择键 ca.theme            -> FAIL  (未登记的选择键: {'ca.theme'})
  在 setStudent() 之外再写一次 state.studentId -> FAIL (state.studentId 有 2 个赋值点)
  在 setLang() 之外再写一次 ca.lang          -> FAIL  (写入点数量: ca.lang=2)

变异 7: 把 pageExercise() 的顶栏归属从 '#/exercises' 改成 ''（只动归属表）
  pytest -k top_nav_ownership: FAILED
    AssertionError: 顶栏高亮的归属表变了: {'pageExercise': ''}
  1 failed, 144 deselected
  （脚本自带还原 + 逐字节校验: 5060 CRLF / 0 裸 LF）
```

**变异 4 顺带抓出一个名不副实的检查。** 第一版写成
`inMemory === OTHER_STUDENT`，名字却叫 "keeps memory and storage in sync" ——
它只断言了内存。变异 4（只写 state、不写 storage）跑出来的失败项里**没有它**，
因为内存确实等于 `OTHER_STUDENT`。改成真的比较 `inMemory(sandbox) === stored(sandbox)`
之后，变异 4 的失败项才从 1 条变成 2 条。
**这是第三次遇到同一类问题**（另两次：DOM 桩的 `querySelectorAll` 恒返回 `[]`、
先 `loadApp()` 再 `setItem` 导致初始值用例空跑）——**"测试通过"不等于"被测到了"**，
而只有变异测试能把这种空跑逼出来。

七次变异失败的都**正好**是被修的那些断言，其余不受影响 —— 断言精准命中缺陷。
变异 5 / 6 / 7 单独值得一提：它们证明守卫抓的是**漂移**而不是"改什么都挂" ——
只有被改的那一项报出来。

真实端到端（打真实运行中的本机服务，只发 GET）
----------------------------------------
服务在 `127.0.0.1:8765` 跑着、库里有用户那 5 门真实课程（加泰语课名）。取
**服务器实际提供**的 `/app.js`（241320 字符 / UTF-8 下 265052 字节，与磁盘逐字节
相同）在 DOM 桩里执行真实页面函数，**43/43 全过**：

- **课程维度（缺陷 A / B）**
  - 正反两个方向切换课程：主视图 / 侧边栏高亮 / 顶栏切换器三者一致
  - 复习中心入口（另一条会提交 course_id 的路径）同样一致
  - 刷新后（localStorage 有值 + 深链）仍然一致，且只高亮一门
  - 路由到不存在的课：后端错误照实呈现，但"当前课程"不被污染，后续页面正常
- **顶栏维度（缺陷 C）**
  - `#/learn` 入口 → 恰好高亮 `#/today`
  - **刷新后深链到学习页 → 恰好高亮 `#/today`**（修复前这里是**零高亮**）
  - 从知识点页进学习页 → 高亮从 `#/knowledge` 移到 `#/today`（含前置对照）
  - 练习 / 错题本 / 我的课程 → 各自的导航项，且**任何时刻恰好一项**
- **学生维度（缺陷 D）**
  - 练习页从路由拿到 sid → 内存与 `localStorage` 一致（修复前存储留在上一个学生）
  - 学生详情页同样一致（对照）
- **界面语言维度（预防项）**
  - 非法 `ca.lang`（`fr`）**不出现在任何请求里**，实际带的是受支持的语言
  - 受支持的值（`ca`）照常透传
- **上线证明**：服务端提供的就是修好的那份（`syncCourseChrome` / `setRouteCourse` /
  `setStudent` / `pickInitialLang` 都在，`ca.course` / `ca.student` / `ca.lang` /
  `state.studentId` 各只有 1 个写入点）

**为什么 C / D / 语言项也能打真实服务**：这些修复的代码（`markActiveNav` /
`setRouteCourse` / `setStudent` / `pickInitialLang`）都在**任何 fetch 之前**执行
—— 所以即使真实库里那几门课没有知识点、没有学生（当前就是如此，实测 5 门课全是
`kp=0 stu=0`），高亮、持久化与语言校验照样可验证。后续 fetch 404 不影响已经写下的
状态，而这正是这些缺陷的回归点。

脚本：`temp/e2e_course_sync.js`（需服务在 8765 上跑着）。
支持 `E2E_APP_SOURCE=<path>` 指向变异副本 —— 变异测试用，避免在回归运行期间
改动 `src/web/app.js`（静态断言是执行时读盘的，改到一半会报假失败）。

E2E 自身的变异测试（证明这 43 条不是空跑）
----------------------------------------
在 `temp/` 里生成变异副本，用 `E2E_APP_SOURCE` 指过去跑（**app.js 一个字节没动**）：

```text
变异 E2E-1: 删掉 pageLearnKnowledge() 的 markActiveNav('#/today')
  失败 2 项: nav: a fresh deep link to the learn page highlights the flow entry
             nav: navigating to the learn page moves the highlight off the previous page
变异 E2E-2: pageExercise() 的 setStudent(sid) 换回只写 state 的旧写法
  失败 2 项: student: the exercise page persists the student from the route
             served app.js has a single state.studentId writer
变异 E2E-3: 删掉 setCourse() 里的 syncCourseChrome()
  失败 6 项: sidebar / switcher follows the route (场景 A)
             reverse: sidebar / switcher follows the route (场景 B)
             review center: sidebar / switcher follows the route (场景 C)
变异 E2E-4: 初始语言读取不校验（退回旧写法）
  失败 2 项: lang: an invalid stored language is not sent to the backend
             lang: the request carries a supported language instead
```

四次都**恰好**失败被修的那几项，与预期逐字一致；跑完校验 `app.js`
（5060 CRLF / 0 裸 LF）未被触碰。

Test Summary
----------------------------------------
```text
Base (1.0.1, 修复前):        5148 passed, 5 skipped
修复 A 后:                   +3 passed  → 5151 passed, 5 skipped
修复 B 后:                   +2 passed  → 5153 passed, 5 skipped
修复 C 后:                   +1 passed  → 5154 passed, 5 skipped
修复 D 后:                   +2 passed  → 5156 passed, 5 skipped
归属表 + 全量写入点守卫:      +2 passed  → 5158 passed, 5 skipped
语言校验 + 清单 parity 守卫:  +1 passed  → 5159 passed, 5 skipped
Final:                      5159 passed, 5 skipped, 42 deselected (integration)
Failed:                     0
```

（`5148` 是改动 `src/web/app.js` 之后、加任何新测试之前跑出来的 ——
与 1.0.1 基线**逐字相同**，证明修复零回归。）

Validation
----------------------------------------
```text
node --check src/web/app.js            PASS
scripts/ui_render_check.js             202 checks  OK   (修复前 163)
scripts/ui_audit.js                    275 checks  OK
tests/test_multi_course.py             146 passed          (修复前 136)
tests/test_learning_view.py            144 passed          (修复前 143)
真实端到端 (live server, 只读)          43/43 OK
变异测试 (渲染 harness)                 5 次, 每次都精准命中 (202 checks 口径重跑)
变异测试 (真实端到端)                   4 次, 每次都精准命中
变异测试 (Python 结构守卫)              2 条守卫 / 6 项判据, 结论全对
pytest -q -m "not integration"         5159 passed, 5 skipped, 0 failed
compileall -q src tests                exit 0
```

Known limitation (explicit, not hidden)
----------------------------------------
- 本机是 Windows，**没有**浏览器端到端测试（agent-browser 只支持 macOS / Linux）。
  所以"三处显示一致"是在 Node + 最小 DOM 桩里执行真实 `app.js` 验证的，外加一次
  打真实服务、真实数据的只读端到端。它不解析 CSS 布局、不触发真实点击事件 ——
  用户手动点击时的**瞬时**中间态（`showLoading()` 那一帧里侧边栏还是旧高亮、
  主视图是"加载中…"）没有被覆盖。那一帧里两者不构成矛盾（没有"内容属于另一门课"
  可被误读），本机服务下通常不可见，故未处理。
- `setRouteCourse()` 的闸门依赖 `__courseCache`。若在 `loadSidebar()` 与页面函数
  之间另一个进程恰好新建了该课程，会误拒一次；产品无建课 UI，窗口是毫秒级，
  下一次路由自动纠正。没有为此引入额外请求。
- 路由到不存在的课仍然会**发一次注定 404 的请求**（错误信息的唯一来源保持在后端）。
  没有在客户端提前造一个错误 —— 那需要新增三语文案，且会绕开"用户可见错误永远来自
  后端 JSON"这条既有约束。
- 顶栏高亮的归属规则是"跟随用户所在页面所属的顶栏分区；不属于任何分区的清空"。
  所以 `pageCourse` / `pageSession` / `pageKnowledgeDetail` / `pageStudent`
  （`#/courses/<c>`、`…/sessions/<s>`、`…/knowledge/<k>`、`…/students/<sid>`）
  在顶栏上**没有任何高亮** —— 这是刻意的确定行为（它们是"课程内部"的详情，
  顶栏没有对应分区，且各有多条进入路径，跟随"上一页"会得到不确定的高亮），
  不是遗漏。
  这张表被 `test_the_top_nav_ownership_table_is_exactly_as_declared` **逐项**锁住，
  所以它不会再**悄悄**变。但要诚实说清边界：自动化锁住的是"与声明的表一致"，
  **不是**"这个选择更恰当"。例如"进入学生详情页时高亮 学生"也可以是一种合理
  设计 —— 那种取舍仍然需要人读代码判断，测试只能保证它一旦被定下就不再漂移。
- **界面语言那两项是预防性的，不是修好的缺陷。** 今天的界面语言选择完全正常：
  产品里没有任何路径会写出非法的 `ca.lang`，只有手工篡改 localStorage 或从
  "曾经支持更多语言"的旧版本降级才会。所以 (a) 语言校验与 (b) 清单 parity
  都**没有**可复现的用户可见缺陷 —— 它们锁的是"如果哪天有了那条路径"。
  如实标注，不把它们算成第 5、第 6 个缺陷。
- **语言清单现在仍是三份物理副本**（后端元组 / `index.html` 选项 / `app.js` 数组）。
  这次做的是让守卫**逐项比对取值**，而不是消除重复 —— 消除重复需要引入一层
  生成或注入（例如由后端渲染一份 JSON 给前端读），那会把一个纯静态资源变成
  运行时依赖。对三种语言、三处声明来说，守卫的性价比更高。若将来语言变多、
  或出现第四处副本，就该改成单一来源了。

## 修复 — 课程显示成内容哈希 + 学生只能靠 API 注册

Goal
----------------------------------------
修用户截图报出的两个问题，以及顺着第一个问题查出的**同一族**缺陷。

**问题 1（用户截图）**：**学生** 页的副标题显示
`课程 course-32dde014868219be · 0`。那串是内容寻址的 `course_id`，
用户看到的应该是课程名 `Gestió de Projectes`。同一个写法在 **12 处**出现过
（概览 / 知识点 / 材料 / 待审核 / 复习中心 / 课堂 / 学生 / 错题本 / 学生首页
的标题与面包屑），全部是"把哈希当文案渲染"。

**问题 2（用户截图）**：注册学生只能靠 `POST /api/students` —— 空态文案
直接把一条 HTTP 指令甩给用户，界面里**没有任何**建学生的入口。

**同族缺陷（顺着问题 1 查出来的）**：`#/today` 在"全部课程"模式下，
**待作答 / 最近评估 / 关注区 / 学习路径** 四块列表各自把 `course_id` 显示出来。
根因不在前端：`/api/student-today` 只给 `study` 与 `review.items` 带了
`course_name`，另外四块只有 `course_id`，前端 `courseTag()` 的
`row.course_name || row.course_id` 于是退回去渲染哈希。

前三个问题的形状是同一个（本项目第 6 次）：**同一条规则被抄了 N 遍，其中一遍漏了。**
所以修法一律是"收口 + 声明表"，而不是"把漏的那一遍补上"。

写 `RAW_ID_ALLOWED` 声明表时又发现第 4 处同族代码：`mcCard` 内联复制了一份
`courseIdentity()`。这一份**没有漏**（两份输出一样），所以它没有用户可见症状 ——
但它是同一个家族，一并收口（见"发现并修复的真实缺陷"第 6 条）。

设计取舍
----------------------------------------
1. **课程显示名收口到 `courseLabel(courseId)`。** 名称来自 `__courseCache`
   （`loadSidebar()` 每次路由刷新），不额外发请求；缓存里没有就退回 `course_id`
   —— 宁可显示一个不漂亮的标识，也不要空白，更不要自造一个"未命名"。
   被否掉的方案：让每个页面自己去 `GET /api/courses` 查名字 —— 那是 N+1 个请求，
   而 `route()` 里侧边栏本来就已经拿到全量课程列表了。

2. **`course_id` 允许出现在哪里，由一条可执行的判据钉死。**
   它只允许出现在**属性值**里（`data-course="…"` / `href="…"`）。
   `tests/test_student_ui.py::test_the_course_id_is_never_rendered_as_visible_text`
   扫全文件：任何一个 `esc(courseId)` 前面 80 个字符里没有未闭合的属性赋值，
   就是"把哈希当文案"。这条判据在改动**前**精确命中 12 处、改动**后**为 0 ——
   准确率是实测的，不是估的。

3. **"技术身份串"是刻意的例外，逐项登记，而且只能有「一个」生产者。**
   `pageCourse` 与 `pageMyCourses` 在 h1 / h3（已经是课程名）下面有一行
   `tiny muted mono` 的 `course_id · code · language`，用来在两门同名课程之间区分。
   这条约定本来就写在 `app.js` 的 `courseIdentity()` 注释里（`课程身份串:
   course_id · code · language (都是已有字段, 不拼新的事实)`），所以这次把它当成
   **唯一生产者**来收口：

   - `mcCard`（我的课程卡片）原本**内联复制**了一遍拼接逻辑，输出与
     `courseIdentity()` 完全一样 —— 所以没有任何测试会红，改动是纯收口。
     用 `temp/_diff_mccard.js` 逐字节证明重构前后渲染结果相同
     （`#view` 3365 字节、`#course-list` 230 字节、`#course-switch` 110 字节，
     全部逐字节一致），并有阴性对照证明这个比对器不是空跑。
   - 新增两条检查钉住**唯一性**（不是输出）：内联指纹不许出现 +
     `courseIdentity(` 至少出现 3 次（1 处定义 + 2 处调用）。
   - 这个收口让 `tests/test_multi_course.py::test_page_escapes_course_names` 里那条
     **字面量**断言失效 —— 它钉的是 `esc(row.course_id)` 这个**拼法**，而不是
     "用户数据必须转义"这条**保证**（全量回归实测报出：`1 failed, 5188 passed`）。
     改成钉 `esc(courseIdentity(row, row.course_id))`：语义不变，并且补了变异验证
     （去掉那层 `esc()` → 该断言报红），证明它仍然抓得住"忘记转义"。
     **这是本次唯一一处改动既有测试的地方**，如实记在这里，不藏在"顺手清理"里。
   - 侧边栏是**另一种**写法（只放 `course_id` 本身，不并列 `code` / `language`），
     而且不在 `PAGES` 里 —— 这个边界如实写进 Known limitation，不假装被覆盖了。

   例外清单写进 `scripts/ui_audit.js` 的 `RAW_ID_ALLOWED`（键 = 页面名，值 = 理由），
   其余页面一个都不许出现。理由里原本引用了一句"这是既有设计（Task 56 / 68）"，
   在 `docs/status.md` 里查不到出处 —— 已改成引用 `courseIdentity()` 自己的注释。

4. **注册入口只有一处，空课程时更要渲染。**
   表单永远在 **学生** 页上（`#student-form`），一个学生都没有的时候也在 ——
   否则第一个学生永远建不出来。练习页 / 单题页的"还没有学生"空态改成
   `noStudentsCard()`：一句文案 + 一个通往学生页的链接，**不再复刻第二份表单**。

5. **错误文案仍然只来自后端。** 换姓名重复注册会被后端判成 `CONFLICT`，
   前端只把 `[CONFLICT] <message>` 原样报出来，不自己编判定、不自己造文案
   （与上传表单同一条既有约定）。

6. **投影层补齐 `course_name`，而不是在前端加一层查表。**
   `classroom_view.today()` 早就给每一行带 `course_name`，`student_today_view`
   只在两处带了 —— 补齐剩下四处，与既有约定一致。这样"每一行都能自己说清
   自己属于哪门课"，前端不需要知道课程列表。

7. **`ui_audit.js` 里必须真的执行 `loadSidebar()`。**
   `route()` 的真实顺序是 `loadSidebar()` → `pageXxx()`，而 `courseLabel()`
   靠 `__courseCache` 解析名称。直接调页面函数测到的是"缓存为空时的兜底"，
   不是用户看到的界面 —— 所以新那一节显式先跑侧边栏（这也是第一次
   有检查真的走这条顺序）。

8. **端到端断言不许假设"真实库永远是 0 个学生"。**
   第一版写的 `students page still shows the explicit empty state` 当天就翻了 ——
   收尾复跑时报 `E2E FAILED (1/26)`。原因不是回归：**用户用新表单把自己注册进去了**
   （`Shengxuan Ling`；改动前那次端到端里这门课是 0 个学生）。这既证明注册路径在
   真实服务上真的可用，也说明那条断言过期了。
   已改成**按实际数据分支**：0 个学生时断言空态，>0 时断言列出学生且不出现空态。
   **教训：端到端打真实数据时，任何依赖"数据恰好是某个形状"的断言都是定时炸弹。**
   注意"按数据分支"不等于"放宽" —— 两个分支各自都有明确断言，而不是删掉断言。

   **连带的一处覆盖边界要说清**：「空课程时表单也要渲染」这件事，真实端到端
   **已经覆盖不到了**（那门课不再为空）。它现在由 `scripts/ui_audit.js` 的两组夹具
   覆盖（`['with students', routes()]` / `['without students', emptyRoutes()]`）。
   这是正确的分工：**夹具层控制数据形状，真实端到端只负责"数据真的长这样时也对"。**
   变异脚本里那条期望串也随之从 `students page renders the registration form
   on an empty course` 改成 `students page renders the registration form`。

交付文件
----------------------------------------
新增：
- `tests/test_student_ui.py` —— 课程显示名静态判据 / 注册表单契约 / 三语文案 /
  真实端点的载荷形状（含幂等与 CONFLICT）/ 渲染 harness 覆盖。

修改：
- `src/web/app.js` —— 新增 `courseLabel()`；12 处标题与面包屑改用课程名；
  新增 `noStudentsCard()`；`pageStudents()` 加注册表单；新增 `wireStudentForm()`；
  三语各加 9 条文案，`student.none` 去掉 "POST /api/students"；
  `mcCard` 的身份串改调 `courseIdentity()`（去掉内联副本），并把
  "唯一生产者"这条约定写进 `courseIdentity()` 的注释。
- `src/application/student_today_view.py` —— `_learning_paths` / `_attention`
  增加 `course_name` 参数；`pending_exercises` / `recent_evaluations` 每行补
  `course_name`；文件头把这条约定写下来。
- `tests/test_student_ui.py` —— 整份文件都是本次新增，共 28 条（见下）。
- `tests/test_student_today.py` —— 加 2 条：All Courses 时每一行都带课程名；
  待作答 / 最近评估 / 关注区 / 今日课堂同样。
- `tests/test_multi_course.py` —— 1 条：`test_page_escapes_course_names` 里的
  字面量断言 `esc(row.course_id)` 改成钉转义保证（`esc(courseIdentity(...))`），
  原因见"设计取舍"第 3 条。
- `scripts/ui_audit.js` —— 新增两节检查（19 个页面 × "不渲染哈希"、
  11 个页面 × "显示课程名"、`courseLabel` 三条契约、注册表单两组场景、
  身份串"唯一生产者"两条）；`studentToday()` 夹具补上 `course_name`，
  并修掉夹具内部的自相矛盾（同一个 `COURSE_ID` 在 `/api/courses` 里叫
  `Algebra Lineal`、在 `/api/student-today` 里叫 `Programacio`）。
- `docs/getting_started.md` / `docs/user_guide.md` —— 注册学生的说明；
  顺带修掉一个**真实的文档缺陷**：原来的 curl 示例缺了必填的 `student_id`。

发现并修复的真实缺陷
----------------------------------------
1. **12 处把 `course_id` 当文案渲染**（用户报的那一处是其中之一）。
2. **学生页没有注册入口**（用户报的第二件事）。
3. **`/api/student-today` 有四块列表只带 `course_id` 不带 `course_name`**
   —— 在"全部课程"模式下，`#/today` 会把哈希直接显示给用户。
   这条是写新检查时被抓出来的，不是用户报的。
4. **`docs/getting_started.md` 的注册示例缺必填字段**（`student_id`），照抄必然 400。
5. **`scripts/ui_audit.js` 的 `studentToday()` 夹具自相矛盾**：同一个
   `COURSE_ID` 在 `/api/courses` 里叫 `Algebra Lineal`，在 `/api/student-today`
   里叫 `Programacio`。它不是产品缺陷，但会让"显示的是课程名"这类断言没法推理，
   所以一并修掉，并如实记在这里。
6. **`mcCard` 内联复制了一份 `courseIdentity()`**（**潜在**缺陷，不是用户可见的）。
   两份输出当时完全一样，所以没有任何测试会红；但将来往 `courseIdentity()` 里
   加字段时，我的课程卡片会悄悄漏掉。已收口成"调助手"，并加了两条检查钉住
   **唯一性**。如实标注：这一条不是用户报的问题，也没有可复现的界面症状。

测试清单
----------------------------------------
`tests/test_student_ui.py`（新增）

- `TestCourseNameIsShownInsteadOfItsId`（5）—— 全文件"哈希不当文案"判据 /
  12 处标题都走 `courseLabel()` / 助手只有一个定义 / 不发请求且退回 id /
  身份串只有一个生产者。
- `TestRegistrationFormContract`（8）—— 表单字段 / 空课程时也在 / 真的 POST
  `/api/students` / 渲染后接线且接线点唯一 / 空学号先拦下 / 空姓名不发送 /
  空态不再出现 curl 指令 / `noStudentsCard()` 只有一份定义、两处使用。
- `TestI18n`（3）—— 9 个新 key 三语齐全且 key 集合相等 / 无悬空 key /
  符号 key 没有混进整句译文表。
- `TestRegistrationEndpointAcceptsTheFormPayload`（9）—— 无姓名注册 / 有姓名注册 /
  `course_id` 从 body 读 / 重复注册幂等（201→200 且返回同一条）/
  换姓名是 CONFLICT(409) / 空学号是 INVALID_INPUT(400) /
  注册后出现在列表里 / 学生不跨课程泄漏 / 姓名逐字保留（`Gestió d'Álgebra — 张三`）。
- `TestUiAuditCoversTheTwoReportedProblems`（3）—— 审计脚本里确有这两节检查 /
  真的执行 `loadSidebar()` / 真的跑一次审计并断言 `UI audit OK`。

`tests/test_student_today.py`（新增 2 条）

- `test_all_courses_keeps_course_name_on_every_task` —— `study` / `learning_paths` /
  `review.items` 每一行的 `course_name` 必须等于该课程真实的名字。
- `test_every_course_scoped_row_carries_the_course_name` ——
  `pending_exercises` / `recent_evaluations` / `attention` / `classes_today` 同样。

Test Summary
----------------------------------------
```text
Base (before this change): 5159 passed, 5 skipped, 42 deselected (integration)
collected after:           5194/5236 tests collected (42 deselected)
this change added:          +30 passed  → 5189 passed, 5 skipped
Failed:                     0
```

Validation
----------------------------------------
```text
node --check src/web/app.js                    PASS
node --check scripts/ui_audit.js               PASS
scripts/ui_audit.js                            318 checks  OK   (修复前 275)
scripts/ui_render_check.js                     202 checks  OK   (零回归)
tests/test_student_ui.py                        28 passed          (新增)
tests/test_student_today.py                     60 passed          (含新增 2 条)
真实服务只读端到端 (temp/e2e_student_ui.js)     26 checks OK  — 5 live courses
   阴性对照 (未修复的 app.js)                   E2E FAILED (17/23), 逐字复现用户截图
   真实数据上的实证                            改完后库里出现 1 个学生 (用户自己注册的)
重构零行为变化 (temp/_diff_mccard.js)           3 个元素逐字节相同 + 阴性对照报红
变异测试 (temp/_mutation_student_ui.py)         10/10 全部 HIT, app.js 逐字节未变
pytest -q -m "not integration"                 5189 passed, 5 skipped, 0 failed
compileall -q src tests                        exit 0
```

Known limitation (explicit, not hidden)
----------------------------------------
- 本机是 Windows，**没有**浏览器端到端测试（agent-browser 只支持 macOS / Linux）。
  "表单能提交"这件事**没有被真实点击验证过** —— 验证到的是三层：
  (a) `scripts/ui_audit.js` 真的执行 `pageStudents()` 并断言渲染出的 HTML 里有
  表单字段；(b) `tests/test_student_ui.py` 断言 `wireStudentForm()` 存在、只有
  一个定义、只被 `pageStudents()` 调用一次；(c) 用**真实 HTTP** 把表单将要发送
  的那份载荷（`{course_id, student_id}` / 再加 `display_name`）打给
  `POST /api/students`，验证 201 / 200 / 409 / 400 四种结果。
  提交事件的**触发本身**（DOM 事件 → handler）不在覆盖范围内。
- `courseLabel()` 的兜底是"缓存里没有就显示 `course_id`"。生产路径上
  `route()` 一定先跑 `loadSidebar()`，所以只有在 `/api/courses` 失败时才会看到
  那串哈希 —— 这是刻意的：宁可显示一个不漂亮的标识，也不要显示空白。
- `pageCourse` 与 `pageMyCourses` **仍然**显示 `course_id`（在 h1 / h3 下面
  那一行 `tiny muted mono` 的技术身份串里）。这不是遗漏，是**登记在案的例外**：
  约定出自 `app.js` 的 `courseIdentity()` 注释，`RAW_ID_ALLOWED` 里逐项写着理由，
  并且身份串现在只有 `courseIdentity()` 一个生产者（两条检查钉住）。
  （初稿在这里引用了"既有设计（Task 56 / 68）"—— 在 `docs/status.md` 里查不到
  这个出处，属于未经核实的引用，已删掉。）
- **侧边栏没有被自动化覆盖到。** 它也在课程名下面并列一行 `tiny muted mono` 的
  `course_id`（`courseListHtml()`），但它**只放 `course_id` 本身**，不并列
  `code` / `language` —— 和身份串不是同一种写法；而且侧边栏不在 `ui_audit.js` 的
  `PAGES` 里，`temp/e2e_student_ui.js` 也不扫它。所以"侧边栏那行哈希该不该留"
  是一个**尚未决定的**产品取舍（它在每门课下面都显示一串无信息量的哈希），
  本次没有动它 —— 用户报的是主视图的副标题，改侧边栏是另一个决定。
  边界写在这里，不假装它被验证过。
- 前端的 `row.course_name || row.course_id` 兜底仍然保留。后端补齐之后它在
  真实数据上不再触发，但删掉它会让"后端某天漏了 `course_name`"直接变成空白。
  真正防住后端回归的是 `tests/test_student_today.py` 里那两条契约测试，
  不是前端的兜底。
## 修复 — 用户可见文本里的课程哈希（侧边栏 / 身份串 / 6 处兜底）

Goal
----------------------------------------
把「用户可见文本里不出现内容寻址的 ``course_id``」这条规则从**带例外**变成
**没有例外**，并补上它一直没有覆盖到的地方。

上一次修的是"12 处标题/面包屑把哈希当文案"（用户截图报的学生页副标题）。那次留下
三块尾巴，本次一并收口：

1. **侧边栏**在每门课下面并列一行 ``course_id`` —— 每个页面、每门课都显示一串哈希；
2. **身份串**（``course_id · code · language``）在课程页 / 我的课程页各显示一次；
3. **6 处 ``名称 || course_id`` 兜底**（顶栏切换器 / student-today 的课程标签 /
   classes_today 副行 / study 块标题 / 我的课程卡片 h3 / 课程页 h1）——
   平时不触发，但形状与用户报的缺陷完全一样。

根因不是"漏了一处"，而是**判据本身太窄**：旧判据只扫字面量 ``esc(courseId)``，
于是 ``esc(course.course_id)``（侧边栏）与 ``esc(course.name || course.course_id)``
（兜底）全在它之外。所以这次先修判据，再让判据把代码逼到合规。

设计取舍
----------------------------------------
1. **判据按 ``esc(...)`` 的参数扫，不按字面量扫。** 配对括号取参数即可（app.js 里
   ``esc()`` 的参数都是单层表达式，不需要 HTML 解析器），再用 80 字符窗口区分
   **属性值**（``data-course="…"`` / ``href="…"`` / ``value="…"`` —— 允许）与
   **可见文本**（违规）。实测：加强后的判据在**修复前**那份 app.js 上精确命中
   **9 处**，修复后 **0 处**（这两个数字就是判据在工作、而不是恒真的证据）。

2. **侧边栏显示课程代码，而不是哈希。** 代码是有信息量的（``GP`` / ``MC`` / ``GEO``），
   而且**同样能区分同名课程** —— ``course_id`` 由 ``(name, code)`` 派生
   （``models.Course._generate_stable_id``），所以两门不同的课必然在 name 或 code 上
   不同；名称在上一行、代码在这一行，合起来是**完整**的判别依据。哈希一个字节都
   不多给用户。被否掉的方案：侧边栏只留名称 —— 那样两门同名课程（例如同名的两个
   学期）就分不出来了。

3. **身份串从 ``course_id · code · language`` 改成 ``code · language``。**
   它原来是"登记在案的例外"，理由是"在两门同名课程之间区分"—— 而这个理由在
   ``code`` 面前不成立（同上）。去掉之后，``scripts/ui_audit.js`` 里那张
   ``RAW_ID_ALLOWED`` 例外表就**整体删掉**了：19 个页面一律受同一条判据约束。
   **"零例外"比"登记在案的例外"强**：白名单会随代码增长静默失效，而"只有唯一入口"
   可以被一句话判据钉住。

4. **例外收成"唯一入口"，不是列成白名单。** 现在全文件只有 ``courseLabel()``
   允许把 id 当最后手段（缓存里没有这门课时的兜底 —— 这是**既有**决定，
   "宁可显示一个不漂亮的标识，也不要空白"，本次保留），判据里写成一句
   ``"courseLabel(" not in argument``，另加一条结构守卫
   ``source.count("|| courseId") == 1`` 把"兜底只能存在一处"钉死。

5. **6 处兜底改成 code 或留空**：能拿到课程对象的地方退回 ``course.code``
   （有信息量）；只有 ``course_name`` 的行（student-today 的三处）留空 ——
   投影层已经保证每行都带 ``course_name``（``tests/test_student_today.py`` 两条契约
   盯着），前端不需要那个哈希当安全网。

6. **顺带修掉一个"手工审查会看错"的工具缺陷**：``ui_audit.js --dump`` 走的是
   ``renderPage()``（**不跑** ``loadSidebar()``），所以 dump 出来的页面是
   **冷缓存态** —— 课程标题显示的是 ``course-1`` 这串兜底 id。核对时我一度以为
   知识点页还在显示哈希。修法是给 ``renderPage()`` 加一个 ``withSidebar`` 参数
   （``dump()`` 传 true）：先跑 ``loadSidebar()`` 再渲染页面。**顺序很关键** ——
   页面函数渲染完之后再补跑侧边栏只会重画侧边栏，``#view`` 不会重画（第一版就写错
   了顺序，dump 里仍然是 ``course-1``，改对之后才变成 ``Algebra Lineal``）。

交付文件
----------------------------------------
修改：
- ``src/web/app.js`` —— ``courseListHtml()`` 第二行改成课程代码（无代码则不渲染
  这一行）；``courseIdentity(course)`` 去掉 ``course_id``；6 处 ``|| course_id``
  兜底改成 ``|| code`` / 留空；``courseLabel()`` 与 ``courseIdentity()`` 的注释
  按新规则重写。
- ``scripts/ui_audit.js`` —— 删掉 ``RAW_ID_ALLOWED``（页面级判据对 19 个页面一律生效）；
  **新增一节侧边栏 / 顶栏切换器检查**（10 条：名称、代码、无哈希、href 里仍有 id、
  无代码的课程不渲染空行、切换课程后重绘路径同样干净）；身份串唯一性检查改成在
  **剥注释后**的源码上数（否则注释里提一句就把计数抬上去）；
  ``renderPage()`` 加 ``withSidebar`` 参数并让 ``dump()`` 用上（见设计取舍第 6 条）。
- ``tests/test_student_ui.py`` —— 判据抽成模块级 ``esc_arguments()`` /
  ``course_id_text_offenders()`` 并加强；新增 ``REMOVED_SHAPES`` /
  ``ALLOWED_SHAPES`` 两组自证；新增 ``TestTheSidebarShowsTheCodeNotTheId``（5 条）。
- ``tests/test_multi_course.py`` —— ``test_page_escapes_course_names`` 里的字面量
  ``esc(courseIdentity(row, row.course_id))`` 改成 ``esc(courseIdentity(row))``
  （**本次第二处、也是最后一处改动既有测试的地方**，理由与变异验证见下）。
- ``docs/status.md`` —— 本区块。

发现并修复的真实缺陷
----------------------------------------
1. **侧边栏每门课下面并列一行 ``course_id``**（用户报的那一族；此前**没有任何
   自动化覆盖它** —— 页面级判据只读 ``#view``，而侧边栏写在 ``#course-list``）。
2. **身份串里的 ``course_id``**（课程页 / 我的课程页各一处）。
3. **6 处 ``名称 || course_id`` 兜底渲染**（列在 Goal 里）。
4. **``ui_audit.js --dump`` 不跑 ``loadSidebar()``** —— 不是产品缺陷，但会让手工
   审查看到用户永远看不到的状态（冷缓存兜底），我核对时就差点误判成"没修好"。
5. **真实端到端脚本用原始课名比渲染文本**（``temp/e2e_student_ui.js`）——
   ``esc()`` 把撇号写成 ``&#39;``，真实课名 ``…de l'Energia…`` 于是永远比不中，
   报出两条**假失败**。夹具全是 ASCII（不含撇号），只有真实数据能暴露它。
   已加 ``decodeEntities()``，比的是浏览器实际显示的字符。**这不是产品缺陷**。

测试清单（13 tests 新增 / 2 处既有断言改写）
----------------------------------------
``tests/test_student_ui.py``（+6）

- ``TestCourseNameIsShownInsteadOfItsId::test_the_course_id_is_never_rendered_as_visible_text``
  —— 加强后的全文件判据（属性值允许，可见文本 0 处）。
- ``TestCourseNameIsShownInsteadOfItsId::test_the_criterion_catches_every_shape_we_removed``
  —— **判据不是空跑**：5 种"修复前真实存在过的写法"逐条必须报红 + 6 种合法写法
  （属性值 / ``courseLabel()`` / 身份串 / 代码兜底）必须一条都不报（阴性对照）。
- ``TestTheSidebarShowsTheCodeNotTheId``（5）—— 侧边栏渲染 ``course.code`` 而不是
  ``course.course_id``；没有代码时不渲染第二行；id 仍然进 ``href``；切换器退回
  代码而不是 id；全文件只有 ``courseLabel()`` 一处 ``|| courseId``。

``scripts/ui_audit.js``（+10 条检查，318 → 330）

- 侧边栏：显示每门课的名称 / 显示课程代码 / 可见文本里没有哈希 / id 仍在 ``href``；
  无代码的课程不渲染空的 mono 行；有代码的课程确实渲染了那一行。
- 顶栏切换器：显示名称、没有哈希；``value`` 仍是 id。
- 切换课程后（``syncCourseChrome()`` 重绘路径）：侧边栏仍然干净、且恰好一项高亮。
- 另外 2 条是删掉例外表之后**新增受约束的页面**（``course`` / ``myCourses``）。

``temp/e2e_student_ui.js``（+7 条断言，26 → 36，真实服务 / 5 门课）

- 侧边栏 / 切换器：不出现哈希、显示全部 5 门真实课名、显示真实课程代码
  （``GEO`` / ``MC`` / ``GA`` / ``GP`` / ``PA``）、id 仍在 ``href``；
  ``courseIdentity()`` 在真实课程对象上只输出 ``code · language``。
- ``course`` / ``myCourses`` 两个页面也纳入"不许出现哈希"（此前在例外表里）。

Test Summary
----------------------------------------
```text
Base (before this change): 5189 passed, 5 skipped, 42 deselected (integration)
this change added:         +6 passed  → 5195 passed, 5 skipped
Failed:                    0
```

Validation
----------------------------------------
```text
node --check src/web/app.js                    PASS
node --check scripts/ui_audit.js               PASS
scripts/ui_audit.js                            330 checks  OK   (修复前 318)
scripts/ui_render_check.js                     202 checks  OK   (零回归)
ui_audit.js --dump zh                          课程标题现在是 "Algebra Lineal"
                                               (修前是冷缓存兜底 "course-1");
                                               身份串是 "ALG · es"
tests/test_student_ui.py                       34 passed          (28 → 34)
tests/test_multi_course.py                     146 passed
定向回归 (7 个文件)                             553 passed
读 ui_audit.js 的 4 个测试文件                  347 passed (改 dump 之后重跑)
判据实测 (temp/_verify_criterion.py)            修复前 9 处 → 现在 0 处
变异测试 (temp/_mutation_course_id.py)          10/10 全部 HIT, app.js 逐字节未变
真实服务只读端到端 (temp/e2e_student_ui.js)     36 checks OK — 5 live courses
pytest -q -m "not integration"                 5195 passed, 5 skipped, 0 failed
compileall -q src tests                        exit 0
```

判据实测明细（``temp/_verify_criterion.py``，输入是**修复前**那份 app.js）：
``[1795 侧边栏, 1887 切换器, 2954 courseTag, 2968 classes_today, 2983 study 块,
3226 mcCard h3, 3230 mcCard 内联身份串, 3315 课程页 h1, 3316 课程页身份串]``
—— 9 处全部命中；当前 app.js 为 ``[]``。

变异测试明细（每一条都断言"改完后 app.js 逐字节未变"）：

| 变异 | pytest | ui_audit | e2e |
|---|---|---|---|
| A 侧边栏重新显示 ``course_id`` | HIT | HIT | HIT |
| B 身份串重新包含 ``course_id`` | HIT | HIT | HIT |
| C 切换器退回 ``名称 \|\| course_id`` | HIT | （夹具课名非空，渲染层看不到）| （同左）|

C 只有静态判据抓得住 —— 这正是"静态判据 + 渲染判据"两层都要有的理由。

Known limitation (explicit, not hidden)
----------------------------------------
- 本机是 Windows，**没有**浏览器端到端测试（``agent-browser`` 只支持 macOS /
  Linux）。"侧边栏长什么样"验证到的是三层：静态判据（源码）+
  ``scripts/ui_audit.js``（真实执行 ``courseListHtml()`` 并断言渲染结果）+
  ``temp/e2e_student_ui.js``（打真实服务的 5 门课）。CSS 是否好看、真机字号、
  换行后的观感都不在覆盖范围内。
- ``courseLabel()`` **仍然**会在"缓存里没有这门课"时显示 ``course_id``
  （``/api/courses`` 失败、或深链指向一门不在列表里的课）。这是既有决定，
  本次保留；判据里把它写成**唯一**允许的例外。所以"用户可见文本里不出现哈希"
  严格说是"只在唯一一个兜底入口出现，且它由 ``courseLabel()`` 独占"。
- **其他实体的内容寻址 id 仍然显示在界面上**（``evidence_id`` /
  ``knowledge_point_id`` / ``session_id`` / ``exercise_id`` / ``plan_id`` …）。
  这是本项目**追溯性**设计的一部分（证据链要能引用具体标识），与"课程显示名"
  不是同一件事，本次**没有**动。其中 ``#/review-center`` 的"待审核队列"用的是
  ``knowledge_point_id`` 当链接文字（``pending_review`` 载荷里**没有** title 字段，
  要改得先在投影层补标题）—— 这是另一个产品决定，边界写在这里，不假装它被处理过。
- ``docs/getting_started.md`` / ``user_guide.md`` 里没有提到"侧边栏第二行是课程
  代码"。本次没有改文档 —— 那不是用户会照着做的操作步骤，加进去只是噪音。

## 修复 — 材料页「课堂 (可选)」是手打 id 的文本框（下拉列表 + 同一规则收口）

Goal
----------------------------------------
用户第三条报告（截图 + 一句话）：

> 这里的课堂选择做成下拉列表的形式

澄清后确认位置是**材料页的上传表单**：要关联某堂课，用户只能往一个
``<input type="text" name="session_id" placeholder="session-…">`` 里手打内部 id。

而 ``session_id`` 与 ``course_id`` 是**同一类东西** —— ``ClassSession._generate_stable_id``
里 ``"session-" + sha256(course_id + 课号)[:16]``，内容寻址、用户既猜不出也记不住。
所以这不是"换个控件"的小事，是上一轮那条规则（**内容寻址的句柄不得当用户可读文案**）
漏在了一整个实体上。

目标因此定为两件：
1. 把那个文本框换成下拉列表，选项来自当前课程的课堂，标签是 ``第 3 堂 · Tema 3``；
2. 把 ``session_id`` 纳入同一条判据，并把它**已经漏到界面上的每一处**收干净。

设计取舍
----------------------------------------
1. **不新增后端接口。** ``GET /api/sessions?course_id=…`` 已经存在
   （``endpoints.list_sessions`` → ``course_service.list_sessions`` →
   ``session_to_dict``），返回 ``session_id / session_number / date / title / …``
   —— 下拉要用的三个字段一个不缺。前端只是**多取一次**，没有改契约。

2. **标签收口成一个助手 ``sessionLabel(session, fallback)``，而不是在下拉里内联拼。**
   ``第 N 堂 · 标题`` 这个拼法此前已经在 app.js 里被抄了**两遍**（课堂页 h1、
   知识点详情页的溯源链），加下拉就是第三遍 —— 正是本项目反复吃亏的
   "同一条规则被抄了 N 遍"：抄三遍不会让任何断言变红，只有等将来往标签里加字段
   （比如加日期）才会暴露，而那时三处已经各自漂移。所以本次把既有两处一并改调助手，
   并让"唯一性"本身变成断言（见变异 H）。

3. **三语走既有两层表。** ``'第 '`` / ``' 堂 · '`` 早就存在（es: ``"Sesión "`` /
   ``" · "``；ca: ``"Sessió "`` / ``" · "``），只补两个：
   ``' 堂'`` → ``''``（西语里 ``第 3 堂`` 就是 ``Sesión 3``，没有对应量词，
   **空串是正确译文**）与 ``'不关联课堂'``。顺带删掉因此变成孤儿的 ``' 堂 '``
   （孤儿译文由 ``test_translation_tables_have_no_orphan_entries`` 强制清掉）。

4. **"不关联课堂"是合法状态**，不是空态兜底：上传一份课件时本来就没有对应课堂。
   所以空选项永远在，且 ``value=""``（不传 ``session_id``，与修复前行为一致）。

5. **一堂课都没有时下拉照常渲染**，只多一行 ``还没有课堂。``（复用既有译文）。
   被否掉的方案：没有课堂就把下拉禁掉或换成文本框 —— 那样表单在"新课程"这个
   **最常见**的状态下直接残废。

6. **材料列表的"课堂"列一起改。** 它写的是 ``esc(m.session_id || '—')`` ——
   用户天天看的是这一列，而不是下拉。下拉 + 列表列是同一个决定的两面。

7. **"课堂"这个字有三种含义，判据必须只看 session 那个 ``<select>``。**
   同一张表单里还有语言下拉（4 个选项），材料列表表头也叫"课堂"。第一版把
   ``out.indexOf('第 3 堂 · Tema 3')`` 搜整页，于是选项文案退化成哈希时它**仍然是绿的**
   ——变异 B 实测踩到。现在先切出 ``<select name="session_id">…</select>`` 再断言。

交付文件
----------------------------------------
修改：
- ``src/web/app.js``
  - 新增 ``sessionLabel(session, fallback)``（紧挨 ``courseLabel()``，契约一致：
    记录缺失时退回句柄本身，而不是空白）；
  - ``pageMaterials()``：多取一次 ``/api/sessions``，预拼 ``sessionOptions``；
    文本框 → ``<select name="session_id">``；材料列表的"课堂"列改用助手；
  - ``wireUploadForm()``：取值从 ``input[name="session_id"]`` 改成
    ``[name="session_id"]``（换控件类型时前者会静默变成 ``null.value``，
    而提交路径**没有任何自动化**能跑到）；
  - ``pageSession()`` 副标题：去掉 ``session_id``，只留日期（与课程页副标题是
    ``courseIdentity()`` 而不是 ``course_id`` 对齐；日期是那个页面上唯一的日期来源）；
  - ``pageKnowledgeDetail()``：多取一次 ``/api/sessions``；
    溯源链的"来源课堂"、"组织归属"、Source Material 节点的"课堂"行三处都改用人话标签；
  - 三语表补 ``' 堂'`` / ``'不关联课堂'``，删孤儿 ``' 堂 '``。
- ``scripts/ui_audit.js`` —— 新增一节材料页课堂下拉检查（12 条）；夹具补
  ``/api/sessions``（``routes()`` 与 ``emptyRoutes()`` 各一条）；页面级检查**新增
  "不出现原始 session_id"**（19 个页面 × 1 条，运行期）。
- ``tests/test_student_ui.py`` —— 判据从"只扫 ``esc(...)``"扩到
  **``esc(...)`` + ``dash(...)``**（见下）；``session_id_text_offenders()``；
  新增 ``TestTheSessionPickerIsADropdown``（8 条）、
  ``TestTheSessionIdIsNeverRenderedAsVisibleText``（4 条）、
  ``TestThePickerValueIsWhatTheUploadEndpointConsumes``（4 条，真实 HTTP 往返）。
- ``temp/e2e_student_ui.js`` —— 新增 3c 节（10 条，数据驱动：下拉选项必须与
  ``/api/sessions`` 的真实响应逐条对应）；页面级新增"不出现原始 session_id"；
  只读检查把 ``pageMaterials()`` 也纳进来。
- **新增** ``scripts/e2e_session_picker.js`` —— 自带服务的真实端到端（见下）。
- ``docs/user_guide.md`` —— 新增「界面上怎么关联课堂」一节（下拉怎么选 / 为什么
  下拉里没有 id / 课堂目前只能走 API 创建）。
- ``docs/status.md`` —— 本区块。

发现并修复的真实缺陷
----------------------------------------
1. **上传表单要用户手打 ``session-…``**（用户报的那一处）。
2. **材料列表的"课堂"列显示原始 ``session_id``**（``esc(m.session_id || '—')``）。
3. **课堂页副标题显示原始 ``session_id``**（``session-32dde… · 2026-03-01``）——
   课程页同一位置显示的是 ``code · language``，两页不一致。
4. **知识点详情页溯源链的"组织归属"显示原始 ``session_id``** —— 而紧挨着的
   "来源课堂"显示的是人话标签，同一个 ``<dl>`` 里两种写法。
5. **知识点详情页 Source Material 节点的"课堂"行显示原始 ``session_id``**，
   而这一行的 ``<dt>`` 是**人话标签**，与它同组的"文件 / 类型 · 大小 / 状态 / 语言"
   也都是人话（真正按设计显示句柄的是 ``material_id`` / ``内容哈希`` / ``受管路径``
   那三行，标签本身就是技术字段名）。

   **第 5 条是"判据之外的缺陷"**：它写的是 ``dash(material.session_id)``，
   **没有 ``esc`` 包装**，而当时的静态判据只扫 ``esc(...)`` 的参数 —— 于是它
   完全在判据视野之外。抓到它的是 ``scripts/ui_audit.js`` 新加的**运行期**页面检查。
   所以本次把判据的扫描范围从 ``esc(...)`` 扩到 ``esc(...) + dash(...)``
   （``_TEXT_HELPERS``），并把"两个助手都要扫"写成一条独立断言
   （``test_the_criterion_scans_dash_as_well_as_esc``），免得下次又只记得一个。

6. **``ui_audit.js`` 的选项标签检查搜了整页**（工具缺陷，不是产品缺陷）：
   ``第 3 堂 · Tema 3`` 在材料列表里也会出现，于是选项文案退化成哈希时它仍然绿。
   变异 B 实测暴露，改成只在 session 那个 ``<select>`` 里找。

测试清单（20 tests 新增）
----------------------------------------
``tests/test_student_ui.py``（34 → 54）

- ``TestTheSessionPickerIsADropdown``（8）—— 是 ``<select>`` 而不是文本框；
  有"不关联课堂"空选项；选项来自 ``/api/sessions``；``value`` 是 id 而文案是
  人话标签；空课堂时照常渲染并给提示；材料列表的"课堂"列不显示 id；
  接线按 ``name`` 取而不是按标签；标签助手只有一个生产者；课堂页副标题不再打印 id。
- ``TestTheSessionIdIsNeverRenderedAsVisibleText``（4）—— 全文件判据；
  4 种"修复前真实存在过的写法"逐条必须报红 + 5 种合法写法（属性值 / 两个助手的
  最后手段）必须一条都不报；``esc`` + ``dash`` 两个助手都扫；
  兜底只能存在于助手一处。
- ``TestThePickerValueIsWhatTheUploadEndpointConsumes``（4，真实 HTTP）——
  ``/api/sessions`` 真的给出下拉标签要用的 ``session_number`` / ``title``；
  课堂不跨课程泄漏；把 ``<option value>`` 原样发回 ``POST /api/materials``
  材料确实挂到了那堂课上；``value=""`` 那一支材料照样登记。
- ``TestTheLiveSessionPickerEndToEnd``（1）—— 跑 ``scripts/e2e_session_picker.js``，
  并断言脚本自己打印的成功行（不只是退出码 0，否则脚本把 check 写成 no-op 也能绿）。

``scripts/ui_audit.js``（+44 条检查，330 → 374）

- 材料页课堂下拉（12）：控件是 select / 不再要手打 id / 有空选项 /
  选项来自真实课堂 / 选项文案是"第 3 堂 · Tema 3" / **选项文案不是哈希** /
  表单内可见文本里没有 session_id / id 仍在 option 的 ``value`` 里 /
  材料列表显示标签而不是 id / 材料挂在已不存在的课堂下时退回句柄 /
  空课堂时照常渲染 + 提示 + 无堆栈 / es·ca 三语各自拼法。
- 页面级新增 1 条 × 19 个页面：**运行期**断言可见文本里不出现 ``session-1``。
- 接线 2 条 + 标签助手唯一性 3 条（定义一次 / 下拉与列表都调它 / 调用点计数 = 7）。

``temp/e2e_student_ui.js``（+20 条断言，36 → 56，真实服务 / 5 门课）

- 每个页面额外断言"不出现原始 ``session-`` 哈希"（+10）。
- 3c 节（10）：下拉存在 / 不再要手打 id / 有空选项 / 选项数与真实课堂数**逐条相等** /
  每个真实课堂都能按 id 选中 / 选项文案里没有 ``session-`` 哈希 /
  每个真实课堂都带课号与标题 / 材料列表里没有哈希 / 空课堂时下拉照常 + 提示。
- 只读检查把 ``pageMaterials()`` 也纳入（它现在发 3 个 GET）。

``scripts/e2e_session_picker.js``（**新增**，16 checks，自带服务 + 临时数据目录）

- 自己 ``spawn`` 一个用 ``temp/_e2e_session_picker_data/`` 的 launcher（不碰
  ``classroom-data/``），在里面建 1 门课 + 2 堂课 + 1 份材料，渲染**真实**材料页，
  跑完 kill 服务、删目录。
- 覆盖 ``temp/e2e_student_ui.js`` 覆盖不到的一支：**真实库里 ``sessions`` 表是空的**，
  而那支 e2e 的契约是只读，所以它只能测"空课堂"分支；"有课堂时选项渲染成
  ``第 1 堂 · Tema 1``" 此前只有 ui_audit 的夹具证明过，没有真实 HTTP 响应证明过。
- 放在 ``scripts/`` 而不是 ``temp/``：它自带服务，不需要外部先起 8765，
  所以能进常规测试套件。

Test Summary
----------------------------------------
```text
Base (before this change): 5195 passed, 5 skipped, 42 deselected (integration)
this change added:         +20 passed
Final measured run:        5219 passed, 5 skipped, 47 deselected, 0 failed
Failed:                    0
```

数字对不齐（5215 + 20 ≠ 5219）不是算错：**回归期间有另一个会话并行改了这个项目**
（见下一个区块）。5219 是最终那一次实测；"deselected" 从 42 涨到 47 也是那次并行
改动新增的 integration 测试。我的改动自带的 20 条逐个在
``tests/test_student_ui.py`` 里单跑验证过（34 → 54 passed）。

Validation
----------------------------------------
```text
node --check src/web/app.js                    PASS
node --check scripts/ui_audit.js               PASS
node --check scripts/e2e_session_picker.js     PASS
node --check temp/e2e_student_ui.js            PASS
scripts/ui_audit.js                            374 checks  OK   (修复前 330)
scripts/ui_render_check.js                     202 checks  OK   (零回归)
tests/test_student_ui.py                       54 passed          (34 → 54)
定向回归 (4 个文件: multi_course / exercise_ui /
          learning_view / course_review)       457 passed
变异测试 (temp/_mutation_session_picker.py)    8/8 全部 HIT, app.js 逐字节未变
真实服务只读端到端 (temp/e2e_student_ui.js)    56 checks OK — 5 live courses
自带服务端到端 (scripts/e2e_session_picker.js) 16 checks OK — 2 sessions, temp data dir
pytest -q -m "not integration"                 5219 passed, 5 skipped, 47 deselected,
                                               0 failed   (最终实测, 22m01s)
compileall -q src tests                        exit 0
```

（同一份代码在这之前的两次全量回归：5214 passed / 0 failed，以及
5215 passed + 2 failed —— 那 2 条是并行改动留下的，见下一个区块。）

变异测试明细（每一条都断言"改完后 app.js 逐字节未变"）：

| 变异 | pytest | ui_audit | e2e |
|---|---|---|---|
| A 下拉改回文本框 | HIT | HIT | HIT（3 条）|
| B 选项文案改回哈希 | HIT | HIT | （真实库里 0 堂课，没有选项可测）|
| C 材料列表的课堂列改回哈希 | HIT | HIT | （同上）|
| D 接线改回 ``input[name=]`` | HIT | HIT | （提交路径不执行）|
| E 空课堂提示去掉 | HIT | HIT | HIT |
| F 课堂页副标题改回哈希 | HIT | HIT | （该页不在 e2e 的 PAGES 里）|
| G 知识点详情页的课堂行改回哈希（只有 ``dash``，没有 ``esc``）| HIT | HIT | （同上）|
| H 内联复制标签拼法（**输出一模一样**）| HIT | HIT | （渲染层看不出）|

H 是本轮最值得记的一条：内联复制的输出与助手**完全一致**，所以任何渲染断言都不会红，
只有"唯一性"那两条抓得住。它同时也是"为什么要收口而不是就地补一处"的可执行证据。

Known limitation (explicit, not hidden)
----------------------------------------
- **用户自己的**库里一堂课都没有（``classroom-data/`` 的 ``sessions`` 表为空），
  所以 ``temp/e2e_student_ui.js`` 的 3c 节实际走的是"空课堂"那一支。
  "有课堂"那一支由 ``scripts/e2e_session_picker.js`` 在**临时数据目录**里证明
  （真实 HTTP + 真实渲染，2 堂课），**不是**在用户自己的数据上证明的 ——
  往用户的库里写东西需要他本人授权，不做。
- 因此"下拉在用户实际数据下的观感"（课堂多了要不要滚动、标题长了怎么截）
  仍然没有覆盖：临时目录里只建了 2 堂短标题的课。
- 本机是 Windows，**没有**浏览器端到端测试（``agent-browser`` 只支持 macOS /
  Linux）。下拉在真机上长什么样（宽度、选项数量多时是否要滚动）不在覆盖范围内。
- ``sessionLabel()`` 与 ``courseLabel()`` 一样，会在"记录缺失"时显示原始句柄。
  这是**唯一**允许的兜底入口，判据里写成 ``"sessionLabel(" not in argument``
  + 一条结构守卫（``|| s.session_id`` / ``|| m.session_id`` / ``|| sessionId``
  全文件各 0 处）。所以严格说是"只在唯一一个兜底入口出现"。
- **其他实体的句柄仍然显示在界面上**（``evidence_id`` / ``knowledge_point_id`` /
  ``material_id`` / ``exercise_id`` / ``plan_id`` …），包括 Source Material 节点里的
  ``material_id`` / ``内容哈希`` / ``受管路径`` 三行。这是本项目**追溯性**设计的一部分
  （证据链要能引用具体标识），本次**没有**动；本次的边界是"**人话标签位置**不许出现
  句柄"，而不是"界面上不许出现任何 id"。``#/review-center`` 的"待审核队列"用
  ``knowledge_point_id`` 当链接文字（``pending_review`` 载荷里没有 title 字段）
  仍在这个边界之外。
- 界面上**没有建课堂的入口**（app.js 只发 ``GET /api/sessions``，从不 POST）。
  所以"先建课堂再关联"这一步仍然只能走 API —— 与修复前的"手打 session_id"
  同源，只是从"要记住 id"变成"要写 HTTP 请求"。下拉下面那句「还没有课堂。」
  就是为这个状态准备的提示。要不要给课堂也做一个创建表单，是**另一个**产品
  决定（与上一轮给学生做注册表单同型），本次没做，边界写在这里。
- 已补上 ``docs/user_guide.md`` 的「界面上怎么关联课堂」一节（下拉怎么选 /
  为什么下拉里没有 id / 课堂目前只能走 API 创建）。

## 修复 — 确定性审计没跟上时钟真源搬家（并行改动留下的红灯）

Goal
----------------------------------------
课堂下拉那轮全量回归跑出 **2 条失败**，而它们**不在**本轮改动的文件里：

```text
FAILED tests/test_determinism_audit.py::test_no_datetime_now_outside_runtime
FAILED tests/test_determinism_audit.py::test_runtime_is_only_nondeterminism_source
  → datetime.now/utcnow used outside runtime.py:
    ['D:/Project/Clases/src/common/clock.py:34 (_dt.datetime.now)']
```

红灯必须归因到具体改动，不能"看着像环境问题"就略过。

归因（有出处，不是猜）
----------------------------------------
按文件 mtime 排查，``16:40:01 – 16:43:13`` 之间有 **8 个 src 文件被改**，
全部与我的改动无关（我最后一次写 ``src/web/app.js`` 是 ``16:28:59``）：

```text
16:40:01  src/knowledge_validation.py
16:40:15  src/knowledge_review.py
16:40:29  src/backup/service.py
16:42:40  src/persistence/database.py
16:42:47  src/application/runtime.py      ← 只剩兼容 re-export
16:43:00  src/common/clock.py             ← 新建
16:43:07  src/common/__init__.py          ← 新建
16:43:13  src/persistence/__init__.py
```

即一次 **P1-4「时钟真源收口」重构**：``Clock`` / ``utc_now_iso`` / ``fixed_clock``
的真源从 ``src/application/runtime.py`` 搬到中立的 ``src/common/clock.py``
（``src.persistence`` 的迁移台账与 ``src.application`` 的日志都需要注入式时钟，
但分层禁止这两个包互相依赖，所以真源放进零依赖的 ``src.common``）。

重构本身是对的，``runtime.py`` 的模块 docstring 也写明了这是兼容 re-export。
但它**没同步** ``tests/test_determinism_audit.py`` —— 那条审计把
``"application/runtime.py"`` 当成挂钟时间的**唯一**放行路径，真源一搬家，
它就把一次正确的重构报成违规。

修法（跟着语义走，不是放宽判据）
----------------------------------------
审计的语义一直是"**挂钟时间只能住在时钟真源里**"，所以放行名单跟着真源走，
新增一个具名常量并让两条断言都用它：

```python
_CLOCK_SOURCE_FILES = ("src/application/runtime.py", "src/common/clock.py")
```

放的是**两个**文件而不是只放行新真源：``runtime.py`` 仍然是历史调用方的入口，
它的 re-export 不是"另一处墙钟时间"。断言文案也从 ``outside runtime.py`` 改成
``outside the clock sources`` —— 否则下次真源再搬家，报错信息会把人指向一个
已经不含墙钟时间的文件。

**没有**动的：``secrets.token_urlsafe`` / ``random`` / ``hash()`` / ``uuid4``
四条禁令一条未放宽（``secrets`` 仍在 ``runtime.py``，没有搬家）。

Known limitation (explicit, not hidden)
----------------------------------------
- **这个项目当时正被另一个会话并行编辑**（同一台机器上有 Codex 会话在跑）。
  所以我这次的"全量回归数字"只对**我改动的文件 + 我读过的判据**负责；
  那次并行重构自身的正确性（``src/persistence`` 的迁移台账时间戳等）**不在**
  本次验证范围内，我只修好了它对审计造成的红灯。
- 审计是**静态 AST 扫描**，它证明的是"墙钟时间的调用点位置"，**不是**"运行期
  真的没有非确定性来源"。后者由 E2E 验收
  （``test_two_independent_runs_produce_identical_reports``，TOTAL DIFFS: 0）覆盖。

Test Summary
----------------------------------------
```text
Base:    tests/test_determinism_audit.py  12 passed（其中 2 条在并行重构后变红）
Fixed:   12 passed, 0 failed
```

Validation
----------------------------------------
```text
pytest tests/test_determinism_audit.py            12 passed（修前 10 passed + 2 failed）
pytest tests/test_determinism_audit.py
       tests/test_student_ui.py                   66 passed（12 + 54）
pytest -q -m "not integration"                    5219 passed, 5 skipped, 47 deselected,
                                                  0 failed（修完这条之后的实测）
```

最后一次全量回归**开始前与结束后**各做了一次 ``src tests scripts`` 的 mtime 快照比对，
md5 一致 —— 即这 22 分钟里没有别的东西再动过文件，数字可信。

## 收敛 — 六项工程债清理（P0-1 / P0-2 / P0-3 / P1-4 / P1-5 / P1-6）

本轮不是新功能，是把积累的工程债一次性收敛。每一项都有独立的验收判据。

### P0-1 目录规范统一（决策 A）

`classroom-data/` 确立为**唯一**数据真相源（8 个子目录，与
`src/application/data_dirs.py::DATA_LAYOUT_DIRS` 一字对应）。`AGENTS.md`
「文件组织规范」章按此重写；「YAML frontmatter / 小写连字符命名」降级为**导出
文件建议**（受管材料改用内容寻址 ID）。新增 `docs/legacy-input-output.md`
旧布局对照表。`input/` `output/` 等 8 个遗留目录各加占位说明，
**未删除任何文件** —— 其中根 `database/classroom.sqlite` 是 Task 71–75 修复
`Workspace(".")` 之前的遗留产物（见上文 Task 71–75 区块），按存档保留。

### P0-2 文档续写与去过期

- `docs/final_persistence_report.md` 头版本号 `0.37.0 → 1.0.1`（三处对齐）；
  正文 13 行矩阵 / 50 KP trace 口径按要求保持不动。
- `docs/architecture.md` 两处「No Third-Party Dependencies / No AI/LLM/Network」
  假陈述改写为「领域层 stdlib-only + 运行时依赖见 `requirements.txt`，ASR / OCR
  本地离线、无云 API」。（第一处在开头 Design Principles，第二处在 Task 11
  转录层章节 —— 后者是复查时补的。）
- `docs/project-structure-analysis.md`（2026-09-15，"无 UI / 无 DB / 无 README
  / 45 模块"）已加「已过期、仅存档」标记并从 README 文档索引移除。
- `docs/final_report.md` 追加 Task 62–75 汇总章，**含 status.md 尾部 5195 与
  终报 4278 的计数口径差解释**（两者同为 `-m "not integration"` 口径，
  integration 均不计入）。
- **Task 62–75 回写总表**：经核实 status.md 此前有 Task 62–70 区块但**缺 Task
  71–75**，已据三份源报告补上（本文件上文）。

### P0-3 工程卫生

`.gitignore` 补齐 `.venv/` / `*.sqlite-shm` / `*.sqlite-wal` /
`classroom-data/.run/` / `backups/*.zip` / `temp/` / `logs/` / `.pytest_cache/` /
`.idea/` 等。`scripts/` 下 61 项一次性脚本移入 `scripts/dev-archive/`，根目录只
留生产入口（start / health / stop.bat）与 4 个检查脚本，新增 `scripts/README.md`。
根散落 `pytest_*.txt` / `knowledge_*.txt` / `task.txt` 等移入
`logs/regression-archive/`。**未建 git 仓**（是否建仓由用户决定）。

### P1-4 分层收敛

`Clock` / `utc_now_iso` / `fixed_clock` 真源收口到**新建的零依赖叶子包**
`src/common/clock.py`；`src/application/runtime.py` 改为兼容 re-export。

> 中途返工：最初下沉到 `src/persistence/clock.py`，结果 `runtime.py` 一做
> re-export 就让 **application 反向依赖 persistence**，触发守卫。结论是共享基础
> 件必须放**零依赖叶子包**，不能放任何一层的地盘里。

`ValidationStatus` / `ReviewStatus` 枚举收归 `src/models.py`，删除其
`__post_init__` 内的懒导入；`src/backup/service.py` 不再懒导入
`application.workspace`。`tests/test_persistence_layering.py` 从单向扩展为
**双向** AST 守卫。

### P1-5 测试补强

- 新增 `tests/test_nightly_asr_ocr.py`（真实 Whisper / OCR 冒烟）与
  `tests/test_concurrent_writes.py`（多线程并发写），均带 `integration` 标记、
  默认 deselected。`run_tests.cmd` 默认口径 `-m "not integration"` **未改动**。
  nightly 命令：`pytest -m integration -k nightly`（见 README）。
- 发布门禁改为**读真实值**断言：迁移链 3 条且版本连续、仓储内省计数 18。
  不再依赖文档关键字。
- 两个实测结论：① 本机 cached whisper `base` 模型**损坏**
  （`File model.bin is incomplete`）而 `is_local_whisper_available()` 仍返回
  True，已加模型加载错误识别 → **clean skip 而非 fail**；② 并发**不能**共享
  一个 `Database` 实例（`_depth` 是实例级，跨线程踩踏 savepoint 编号），
  正确模型是每线程独立实例写同一文件。

### P1-6 前端零构建拆分

`src/web/app.js`（5288 行单文件）按主题拆为 `api.js` / `i18n.js` / `app.js`
（核心工具 + 共享 state + 路由 + 启动，703 行）+ `views/*.js`，共 **12 个脚本**，
`index.html` 用多个 `<script>` **按固定顺序**引入。零依赖、零构建、无新 npm
依赖。顺序不可调换：`i18n.js` 必须在 `app.js` 之前（`state` 初始化依赖
`pickInitialLang()` / `UI_LANGUAGES`）。

- 等价性验证：路由分支 11 项 / API 路径 21 项 / 页面函数 19 项 / 顶层定义
  119 项**集合完全不变**，且无重复定义。
- 拆分前先取基线，拆后 `ui_render_check` **202** / `ui_audit` **374**
  与基线**逐条一致**。
- 拆分暴露的坑：6 个 Python 测试文件用 `read_asset("app.js")` 对源码做静态断言，
  拆分后**一次跑出 115 failed**。修法是在 `tests/support.py` 加
  `read_web_source()` —— `"app.js"` 返回按加载顺序拼接的**全部前端源码**。

### 收尾：把拆分引入的风险钉住

"前端由哪些文件、按什么顺序组成"这个事实分散在 **6 处**（index.html 的
`<script>` 顺序、`tests/support.py::WEB_SOURCE_ORDER`、四个 `scripts/*.js` 的
`WEB_FILES`）。任一处漂移**不会报错**，而是**静默失去覆盖**（新增 view 忘了登记，
那边的断言就搜不到它的代码但仍"绿"）。新增
`tests/test_web_source_manifest.py`（8 条）钉住六处一致。

**做了变异测试证明守卫不是假绿**：① 造一个未登记的 `views/_tmp_*.js` → 转红并
点名；② 从 `ui_audit.js` 的 `WEB_FILES` 删一项 → 转红并点名该文件（try/finally
保证还原，已验证 374 checks 复现）。

Validation
----------------------------------------
```text
pytest -q -m "not integration"     5227 passed, 5 skipped, 47 deselected, 0 failed
python -m compileall -q src tests  EXIT 0
node scripts/ui_render_check.js    OK (202 checks)
node scripts/ui_audit.js           OK (374 checks)
pytest -m integration -k nightly   2 passed, 1 skipped  (模型缓存损坏 -> clean skip)
```

完整回归跑了**三遍**且无一次 failed：5219 → 5219（文档改完后复核）→ 5227
（+8 条清单守卫）。已知限制：`compileall` 的门禁口径是**只扫 `src` 和 `tests`**，
`scripts/dev-archive/` 里的废弃临时脚本有语法错误属预期，不参与构建。

---

## TASK-76 — AI 语义理解与知识点自动提取（2026-09-21）

### 交付

- 新包 `src/application/ai/`（10 个文件：config/provider/schemas/prompts/
  chunking/validators/merge/pipeline/service + `__init__`），零新增第三方依赖
  （真实 provider 只用标准库 `urllib`，release AI 供应链门全绿）。
- `Workspace.configure_ai / analyze_material_with_ai / ai_summary`（默认关闭，
  显式开启；失败 422 且 Evidence 安全、可 retry；重复调用幂等）。
- `POST /api/materials/{id}/ai-analyze` + `GET .../ai-summary`（400/404/422，
  无 500 面；release 契约门全绿）。
- 材料页 AI 分析按钮 + 结果面板（自动确认/待确认/冲突计数 + 定义/公式/例题），
  `ai.*` 三语 key 三表对称（415/415/415）。
- **零 DB migration**：候选映射到现有 `KnowledgePoint` 字段；总结活在报告层。
- `tests/test_ai_understanding.py`（67 条 + 1 条 integration live smoke，
  无凭证时干净 SKIP）。

### 附带修复（revert 实验证明全部 pre-existing，非本任务引入）

1. `i18n.js`：加泰 `pack.*` 15 键被误粘进 `es` 表（重复键导致 es 界面显示加泰文）、
   `ca` 表缺失 → 移回 `ca`；补 `ca/nav.reviewPack`；译文表补 `摘要`（es/ca）。
2. `review-pack.js` 注释含 `t('…')` 形状被 i18n 扫描器误伤 → 改写注释（零行为改动）。
3. `pageReviewPack` 清空顶栏高亮，但顶栏真有 `#/review-pack` 分区 → 改传本分区；
   `test_multi_course.py` declared 表同步补 `pageReviewPack`。
4. `test_student_today.py` 子进程在 GBK 控制台下必崩（夹具 BOM `U+FEFF` 进 dashboard
   JSON）→ 亲子两端强制 UTF-8（对照 production gate 先例）。

### Validation（最终数字以本节末尾的全量复核为准）

```text
tests/test_ai_understanding.py     67 passed, 1 deselected (integration live smoke)
python -m compileall -q src tests  EXIT 0
node scripts/ui_render_check.js    OK (219 checks)
node scripts/ui_audit.js           OK (396 checks)
```

详见 `docs/task-76-ai-understanding-report.md`（24 项交付清单 + 分级结论）。

---

## TASK-77 —— 自动 AI 材料处理与真实多模态 Pilot（2026-09-21）

### 结论

- `Workspace.process_material()` 摄取成功后自动 AI 分析；`process_session()` 逐份串行；关闭时作业形状与 TASK-76 一字不差
- AI 失败只污染 `job["ai"]`（completed/failed/skipped），材料保持 SUCCEEDED，Evidence 安全，可经 `POST .../ai-analyze` 重试（幂等）
- 修掉 1 个真实缺陷：AI KP 只进 org、不进 `processing.structure`，重启后全丢；加 `_mirror_to_structure` 幂等镜像（零 migration），重启测试 6 == 6
- 真实 API（agnes-3.0-flash）+ 真实 PDF + UAB 风格讲义：KP 20（AI 17，手工 0），grounded 17/17，auto 15 / review 2 / conflict 0
- Key 全链路脱敏（repr/describe/异常/job 四处断言）；401/429/超时/畸形各有可诊断形状；429/5xx 有界退避（0.5s→2s 上限）
- `tests/test_ai_auto_pipeline.py` 26 条 + `tests/test_live_ai_smoke.py` 2 条（integration，默认 SKIP）；`test_ai_understanding.py` 未动 67 条全绿

### Validation（以本机实测为准）

```text
pytest -m "not integration"（全量）: 5293 passed, 8 skipped, 48 deselected
tests/test_ai_auto_pipeline.py:      26 passed
tests/test_ai_understanding.py:      67 passed, 1 deselected
tests/test_live_ai_smoke.py:         2 passed（有凭证）/ 2 skipped（无凭证）
python -m compileall -q src tests:   EXIT 0
node scripts/ui_render_check.js:     OK (219 checks)
node scripts/ui_audit.js:            OK (396 checks)
HTTP E2E（upload→process→auto AI→KP→trace→review）: PASS，手工 KP = 0
```

详见 `docs/task-77-report.md`（43.x 交办清单 + DoD 全勾）与 `docs/task-77-real-material-evaluation.md`（人工质检 7 条 + 5 局限）。

---

## Web UI 全局审查 + 修复（2026-09-21，同日）

### 结论

- 先做**只读**审查 → `docs/ui-review-2026-09-21.md`（21 项：P0×3 / P1×6 / P2×4 / P3×7，
  每条带 `文件:行` 证据 + 影响 + 建议修法）。审查期间未改任何 `src/` 文件。
- 用户指令：删掉「复习中心」栏目，其余全修。**已全部处理完毕。**
- **P0-1 改为整体删除**（不是修链接）：顶栏 12 → 11 项，删 `pageCourseReview`
  （`views/review.js` 357 → 190 行）、删路由分支、删 33 行孤儿 i18n 条目；
  后端 `GET /api/courses/{id}/review` 与 `tests/test_course_review.py`（1106 行）**保留**
  —— 那套投影有自己的契约测试，删它属于另一个决策。
- 其余 20 项逐项修完。三条"**没有照做**"的建议及理由写在审查报告文末：
  ① `/health` 不加"只拉一次"标志位（它是前端唯一的活性探测，且已与 `/courses` 并行、
  不在关键路径上，延迟代价已归零）；
  ② AI 分析不做跨刷新恢复（后端没有"正在分析"的可读状态 —— `job.ai` 与
  `/ai-summary` 都只暴露**已存在**的报告，存了它只能永远转圈或替服务端编结局）；
  ③ 后端 review 投影不删（有自己的契约测试）。

### 新增的机械守卫

- `tests/test_web_ui_invariants.py`（**新文件**，10 条）：
  - `TestHashRoutesAreReachable` —— 把 `route()` 的条件链解析成可求值谓词，
    对前端**构造**出来的 85 条哈希逐条求值；`index.html` 的每个 `href="#/..."`
    逐条求值；读了 `parts[1..]` 的分支必须钉死 `parts.length`。
    解析器遇到看不懂的条件片段**报错而不是跳过**（静默跳过 = 该分支从此不再被覆盖）。
  - `TestBannerKindDomain` —— `showBanner` 第二实参里出现的字符串字面量只能是 `'bad'`
    （传别的值只会静默降级成警告色）。
  - `TestTaskDockTerminalStatuses` —— `TASK_TERMINAL_STATUSES` 必须恰好等于
    后端 `JOB_STATUSES` 减 `QUEUED` / `RUNNING`。
  - `TestRestoreHookIsWired` —— 恢复钩子必须在启动路径上被调用；`sessionStorage`
    只允许一个键。
- **变异测试**（证明不是空跑）：错题链接改回 2 段 / `index.html` 加一条死链 /
  去掉 `sessions` 分支的长度约束 → 三条断言分别转红，还原后复绿。
- P3-4 的变异测试：给 `SUGGESTED_ACTIONS` 加一个枚举值 →
  `test_learning_view` 与 `test_exercise_ui` 两张 i18n 表**同时**报
  `mk.action.MUTANT_ACTION` 没有译文（改前：这个新枚举会静默漏出 key，
  页面上凭空多出一个 `mk.action.XXX`，无异常、无日志）。

### 顺带修掉的同族缺陷

`route()` 里有 **5 条**分支读了 `parts[1..]` 却从不约束 `parts.length`
（`courses/<c>/learn|sessions|knowledge|students|exercises`）。旧条件下
`#/courses/<id>/learn`（3 段）会被接住、把 `parts[3]` 的 `undefined` 当知识点 id
传下去 —— 与 P0-2 是同一族（那次是长度**写错**，这次是**根本没写**）。已全部补上。

### Validation（本机实测）

```text
pytest -m "not integration"（全量）: 5354 passed, 5 skipped, 50 deselected（19m53s，0 failed）
tests/test_web_ui_invariants.py:     10 passed
test_learning_view + test_exercise_ui 的 i18n 表: 14 passed
python -m compileall -q src tests:   EXIT 0
node scripts/ui_render_check.js:     OK (250 checks)   ← 审查基线 219
node scripts/ui_audit.js:            OK (388 checks)   ← 审查基线 396（删页面 −9，加断言 +1）
node scripts/e2e_session_picker.js:  OK (28 checks)    ← 真实 HTTP
真实服务探针（临时脚本，已删）:        OK (14 checks)    ← 未知作业 404/NOT_FOUND、
                                                          服务端返回的 nav 顺序与新资源
回归前后 mtime 快照比对:              无外部改动（结果可归因到本次改动）
```

全量总数相对 TASK-77 记录的 5293 多了 61 条 —— 本次只**新增 10 条**（新文件），
其余差额来自同期其它改动，未逐条归因。

详见 `docs/ui-review-2026-09-21.md`（21 项逐条结果 + 三条未采纳建议 + 基线数字变化表）。

## 修复 — 「分数」列显示的不是它声称的东西（AI 路径把 LLM 自报置信度写进 knowledge_score）

Goal
----------------------------------------
用户看着 `#/knowledge` 列表截图问「这里的分数有什么实际意义吗，证据呢」。查证
结论：**这一列显示的值不是它声称的语义**。`docs/architecture.md` 把
`knowledge_score` 定义为「当前证据对该知识点的支持程度」，公式在
`src/knowledge_validation.py`（0 条→0.0 / 1 条→0.5 / 2 条→0.75 / 3+ 条→0.9，
有冲突封顶 0.5）；而 AI 提取路径 `src/application/ai/validators.py` 写的是
**LLM 自报的 confidence**，且永远不会被确定性重算覆盖。本区块记录根因、修复、
数据修正与守卫。

设计取舍
----------------------------------------
**为什么不是"把 AI 路径的分数也重算一遍"这么简单。** AI 落库
（`AIAnalysisService.analyze` → `workflow.register_knowledge_point`）不经过
`KnowledgePipeline`，所以 `_apply_validation` 从来没有机会跑。可选的三条路：

1. **在 AI 落库后调 `KnowledgeValidator` 重算三个字段**
   （`validation_status` / `knowledge_score` / `needs_verification`）。否掉了：
   `service.py` 对冲突候选显式设 `needs_verification = True`，重算会把它抹成
   `False`（`_needs_verification(SUPPORTED)`）；而 `validation_status` 从
   `unverified` 翻成 `supported` 会连带影响学习 / 复习候选集，超出本次范围。
2. **只在 UI 上把列名改成「AI 置信度」**。否掉了：那只是让显示诚实，字段本身
   仍承载两套语义，下游读到的还是脏数据。
3. **让两条路径共用同一个公式，只重算 `knowledge_score`**（采用）。
   理由：用户报的症状就是这一个字段；「人工确认前不宣称 SUPPORTED」是
   `service.py` 的显式设计，且项目自己在 `src/web/i18n.js` 里写明「证据校验」
   与「人工审核」是**两条独立的轴**，不该在本次一并改动。

公式因此被提升为公开的单一真源 `knowledge_validation.knowledge_score_from_counts`，
`validators.py` 与 `KnowledgeValidator` 都经它取值。AI 路径的 `n_conflict` 恒为 0：
证据层冲突检测属于确定性装配（`EvidenceIntegrator`），不属于候选抽取。

交付文件
----------------------------------------
- `src/knowledge_validation.py` —— 新增公开包装
  `knowledge_score_from_counts(n_support, n_conflict=0)`（单一真源；
  `_knowledge_score` 保持原样，避免破坏既有调用方与测试）。
- `src/application/ai/validators.py` —— `map_candidate_to_kp_payload` 的
  `knowledge_score` 改为按 grounding 后的证据条数取值；docstring 写明「不是
  LLM 自报 confidence」。
- `src/application/ai/service.py` —— `_attach_evidence` 挂载新证据后同步重算。
- `tests/test_ai_knowledge_score.py` —— 新增 14 条守卫（见下）。
- `docs/architecture.md` —— `knowledge_score (deterministic)` 一节补「两条写入
  路径共用同一公式」。
- `temp/recompute_ai_knowledge_scores.py` —— 一次性数据修正脚本（dry-run / `--apply`）。
- `temp/` 下的只读调查脚本：`check_kp_score.py` / `check_kp_origin.py` /
  `check_score_semantics.py` / `check_payload_keys.py` / `check_two_tables.py` /
  `verify_score_fix.py` / `probe_knowledge_api.py` / `mutate_score_guard.py` /
  `backup_db.py`。

发现并修复的真实缺陷
----------------------------------------
**缺陷 1（用户报的）：AI 路径把 LLM 自报置信度写进 `knowledge_score`。**
`validators.py` 原为 `"knowledge_score": round(max(0.0, min(1.0, confidence_value)), 4)`。
实测 `confidence=1.0 → 1.0`、`0.95 → 0.95`，而确定性公式的值域恰为
`{0.0, 0.5, 0.75, 0.9}` —— 1.0 与 0.95 是它**产不出**的值。

**缺陷 2：`knowledge_validation` 是一个空壳 stage。**
`service.py` 的流程里从 TASK-76 起就有一个名为 `knowledge_validation` 的阶段，
但它的 `detail` 恒为空串、不调用任何验证器 —— 「验证」只存在于阶段名里。
本次没有改这个 stage 的名字（它现在描述的是 payload 生成时已完成的取值），
但它正是缺陷 1 能长期存在的原因：流程看起来"验证过了"。

**缺陷 3：`metadata` 在落库时被丢弃，值的来源不可追溯。**
`validators.py` 写入的 `metadata`（`origin` / `ai_confidence` / `ai_decision` /
`material_id`）在 `KnowledgePoint.from_dict` 处被丢掉 ——
`KnowledgePoint.to_dict()` 根本没有 `metadata` 键。实测落库 `payload` 只剩 12 个
标准字段。后果：事后**无法分辨**一个 0.5 是 LLM 给的还是验证器算的。
本次**未修**（属于另一个决策），记录在 Known limitation。

**缺陷 4：`average_knowledge_score` 是死字段。**
`knowledge_organization.py` 算了它，`src/web/` 全目录**没有任何地方消费**。
本次未动（不是用户报的症状），记录在 Known limitation。

**数据实测（`classroom-data/database/classroom.sqlite`，修正前）**

| 课程 | 知识点 | score 分布 | 每条证据条数 |
|---|---|---|---|
| `course-32dde014868219be` Gestió de Proyectos | 61 | `1.0×49` / `0.95×6` / `0.5×6` | 全部 1 |
| `course-680f27ea0e304d6d` Gestió Ambiental | 13 | `0.5×13` | 全部 1 |

61 个知识点**每个都只有 1 条证据**，按公式应全部 0.5。其中 55 条 `aikp-` 前缀
（AI 路径：49×1.0 + 6×0.95），6 条由确定性管线算出（0.5，本来就对）。
另有 52/61 条 content 带 AI 映射函数独有的 `例子:` / `关联:` 前缀，可独立佐证来源。

**修数据时踩到的坑（值得记住）**
第一版修正脚本只调 `ctx.workflow.register_knowledge_point(payload)` 就打印
「已落盘」—— 但那只更新 org 服务的内存索引，而 `_flush_knowledge` 写的是
`ctx.processing.structure`，于是**旧值被原样写回数据库**。脚本报成功、库里一个
字节没变，是 dry-run 重跑仍报「55 条待修正」才暴露出来。
**写入必须两边都改**：org 索引 + `processing.structure`。

测试清单（14 tests）
----------------------------------------
`tests/test_ai_knowledge_score.py`：

- `TestAiPathUsesTheDeterministicFormula`（5 条）—— 对 confidence ∈ {0, .35, .5,
  .7, .9, .95, .99, 1} × 证据条数 0–5 的 48 个组合断言取值落在确定性值域内；
  与 `KnowledgeValidator` 逐点比对；同证据条数下 confidence 不影响分数；
  confidence 仍保留在 `confidence` 档位与 `metadata.ai_confidence`；
  「1 条证据 = 0.5 而不是 1.0」（用户报的那个具体数字）。
- `TestTheSingleSourceOfTruth`（2 条）—— 公开包装与私有公式逐点相等；默认
  `n_conflict=0`。
- `TestStructuralGuards`（5 条）—— `validators.py` 必须经共享公式；正则禁止
  「把 confidence clamp 进 knowledge_score」这一形状；**自证不是空跑**（把修复前
  真实存在的那一行喂给同一条判据必须命中）；`_attach_evidence` 必须含重算语句；
  `map_candidate_to_kp_payload` 的 docstring 必须写明分数不是 LLM 置信度。
- `TestEndToEndIngestionKeepsTheScoreHonest`（2 条）—— 走完整链路
  `register_material` → `process_material`（自动 AI）→ 读回：落库后的分数必须
  落在确定性值域内，且**必须等于它自己那几条证据算出来的值**（最强的不变式：
  分数是证据条数的函数）。这一条防的是"算对了但存坏了"—— 本项目反复出现过的
  失败形状。

**变异测试（三组，全部精准命中，源码逐字节还原）**

| 变异 | 结果 |
|---|---|
| AI 路径退回旧写法（`round(clamp(confidence_value))`） | 7 failed / 7 passed，含两条端到端断言 |
| `_attach_evidence` 不再重算 | 1 failed / 13 passed（正是 `test_attach_path_rescores_too`） |
| AI 路径写死 `1.0`（用户报的症状） | 5 failed / 9 passed，含两条端到端断言 |

Test Summary
----------------------------------------
Base (before this fix): 5354 passed, 5 skipped, 50 deselected
Added: 14 passed (`tests/test_ai_knowledge_score.py`)
Final: 5368 passed, 5 skipped, 50 deselected (integration)
Failed: 0

Validation
----------------------------------------
- 公式值域穷举：`_knowledge_score` 对 n_support∈[0,7] × n_conflict∈[0,3] 的全部
  输出 = `[0.0, 0.5, 0.75, 0.9]`；AI 路径 48 个组合无一越界。**PASS**
- 两条路径逐点一致：n_evidence = 0/1/2/3 → 0.0/0.5/0.75/0.9，验证器与 AI 路径
  完全相同。**PASS**
- 数据修正：55 条 `aikp-` 全部 1.0/0.95 → 0.5；两张表
  （`knowledge_points` / `course_knowledge_points`）一致；重跑幂等（0 条待修正）。**PASS**
- 真实 HTTP：`GET /api/knowledge?course_id=course-32dde014868219be` → 61 个知识点
  全部 `knowledge_score = 0.5`、每条 1 条证据；截图里的 5 条样例逐条对应。**PASS**
- 编译：`compileall -q src tests` → EXIT 0。**PASS**
- 前端基线：`ui_render_check.js` 250、`ui_audit.js` 388（未改前端，逐项不变）。**PASS**
- 全量回归：见 Test Summary。**PASS**
- 备份：修正前用 SQLite 在线备份 API 存
  `classroom-data/backups/classroom-pre-scorefix-20260921-225734.sqlite`（74 行）。**PASS**

Known limitation (explicit, not hidden)
----------------------------------------
1. **AI 路径的 `n_conflict` 恒为 0。** 证据层冲突检测属于确定性装配，AI 抽取
   路径不做这件事。所以一个 AI 知识点的证据若参与了证据层冲突，它的分数不会被
   封顶到 0.5，直到确定性装配跑过（`process_material`）。
2. **`validation_status` / `needs_verification` 未一并重算。** AI 知识点仍是
   `unverified`，于是会出现「score = 0.5 且 unverified」—— 这在
   `_resolve_status` 的规则下（`n_support≥1 且 score≥0.5 → SUPPORTED`）是
   确定性管线产不出的组合。这是刻意的：AI 路径在人工确认前不宣称 SUPPORTED。
   两条轴的语义见 `src/web/i18n.js` 的 `mc.axesNote`。
3. **`metadata` 仍在落库时被丢弃**（缺陷 3），所以「这个分数来自哪条路径」事后
   不可判定 —— 修正脚本只能按 `aikp-` 前缀区分。
4. **数据修正脚本在 `temp/`，不是长期资产。** `temp/` 按项目规范不长期保留；
   若将来又出现绕过公式的历史数据，需要重写（或把它提升到 `scripts/`）。
5. **未做真实浏览器端到端验证**（本机 Windows，浏览器自动化工具不支持）。
   列表页「分数」列的显示由真实 HTTP 探针 + 前端基线覆盖，不声称点过按钮。
6. **修正后 Gestió de Proyectos 的「分数」列会全部显示 0.5** —— 因为 61 个知识点
   每个都只有 1 条证据。这是诚实的结果，不是新的缺陷：随着证据积累（同一知识点
   被多份材料支持）分数会自然分化到 0.75 / 0.9。另需明确，这一列**不参与任何排序
   或判定** —— `src/application/learning_workflow.py` 写明「`importance` /
   `knowledge_score` 都只是**展示**字段，不参与排序」，全仓 grep 也确认没有权重
   逻辑消费它。它现在的价值是"让人一眼看出这条知识点有几条证据在撑"。

## 修复 — 概览页「知识健康度」挤在左边 + 材料说明挤在窄列里（2026-09-22）

Goal
----------------------------------------
用户看着 `#/`（概览）页截图报两处视觉问题，都出在 dashboard：

1. **「知识健康度」面板内容全挤在左边。** 9 项指标（知识点 / 主题 / 关系 /
   覆盖 / 证据已支持 / 尚未验证 / 证据冲突 / 人工已确认 / 待人工审核）写在
   同一个 `<dl class="kv">` 里，标签与数字都紧贴卡片左缘，右侧空出一大片；
   9 行之间也没有任何语义分组，读起来是一条无层次的长列。
2. **「材料」卡的状态列被说明文字撑成一根细长条。** `COMPLETED` 徽章下面紧接
   `NO_TEXT_EXTRACTED` 徽章 + 警告说明 + 零证据说明，全部用 `<br>` 硬折在
   同一个状态格里。那张表是 `table-layout: fixed`，状态列被钉死在 180px ——
   实测（见「改前/改后实测」）说明文字块高 **164px、10 个行盒**，把整个状态格
   撑到 **203px**，材料卡被顶到 **303px**。

两处都是 **D1/D4（2026-09-20）与真相轴统一（2026-09-21）之后的遗留视觉债**：
内容本身是对的，排版没有跟上。上一轮 `docs/ui-review-2026-09-21.md` 的
21 项**没有记录这两条**（那份清单聚焦功能 / 链接 / a11y / 对比度），所以
这是用户目视发现的新问题。

**材料这条是分两步修好的，第二步才是真因。** 第一版只把 `<br>` 换成
`.material-detail` 块（状态列 180px → 240px），形状从"糊成一坨"变成"一列
整齐的小字"—— 但仍然是**在一个被钉死的窄列里折 5~8 行**，材料行依旧细长。
把预览快照放大看过之后才确认：真正的矛盾不是"块不块"，而是**说明文字有
~150 字符，却只能在一个 160~180px 的固定列里排版**。所以第二版把说明搬到
**独立的整宽说明行**（`<tr class="material-note"><td colspan="3">`）。
本区块记录的是**最终形态**，第一版的两处取值（240px、块级塞在状态格里）
已被取代。

设计取舍
----------------------------------------
**为什么知识健康度用「三组 × 两列网格」，而不是只分组或只铺开。**
单列分组（每组加小标题 + 分隔线）解决了"没有层次"，但没有解决"挤在左边" ——
标签与数字仍贴着左缘。只把 `dd` 右对齐（`text-align: right`）能铺开，却会
影响**全部** `.kv` 使用点（`courses.js` / `knowledge.js` / `learn.js` /
`mistakes.js` / `review.js` 等 20 处），那些是详情页的字段列表，值多为长
文本，右对齐反而更乱。所以选择**只给 dashboard 加容器类** `.kv-groups`
（`repeat(2, minmax(0, 1fr))`，窄屏 `<=560px` 收一列）：三组落进两列网格
铺满卡宽，语义分组与铺开一次解决，其余 20 处 `.kv` **一个字节没动**。

三组的切法是"是什么"，不是"好不好"：

| 组 | 内容 | 语义 |
|---|---|---|
| 结构 | 知识点 / 主题 / 关系 / 覆盖 | 这门课有什么、装配了多少 |
| 证据支持度 | 证据已支持 / 尚未验证 / 证据冲突 | 真相轴 1（`val.*`）|
| 人工审核 | 人工已确认 / 待人工审核 | 真相轴 2（`rev.*`）|

**两条真相轴仍然分开**，没有合并成"健康总分" —— 这是项目在
`src/web/i18n.js::mc.axesNote` 里写死的语义，本次不动。面板的**信息量没变**，
仍是原来那 9 项。

**「覆盖」为什么并进"结构"组，而不是单独成第四组。** 第一版实现是四组
（覆盖单独一组），组名走 `dashboard.health.coverage`、字段名走 `t('覆盖')`，
两者在 zh / es 下**是同一个词** —— 实测渲染出 `覆盖 / 覆盖 12`、
`Cobertura / Cobertura 12`，看起来像渲染 bug。而 `coverage.assigned` 的语义
（有多少知识点装配进了课程结构）本来就属于"结构"。所以并进结构组，并删掉
冗余的 `dashboard.health.coverage` key（三语各一条）。这个缺陷**是被预览
快照抓出来的** —— 自动断言当时只看"四组是否存在"，没看"组名与字段名是否
同名"；现已补上一条专门断言（见守卫表）。

**为什么材料说明要搬到独立的整宽说明行，而不是继续留在状态格里。**
根因是 `table-layout: fixed`：这张表的列宽由 `<colgroup>` 钉死，状态列
160px，而说明文字约 150 字符。`overflow-wrap: anywhere` 能保证不溢出，却
会把 150 字符折成 **~9 行**（实测 164px），整个状态格 203px、材料卡 303px ——
用户看到的"细长条"就是这个。改法是把说明从"某一格的内容"变成"这一行材料
的附注"：

- **结构**：`<tr class="material-row">`（材料行，CSS 去掉底边）+ 
  `<tr class="material-note"><td colspan="3">`（说明行，整宽 910px）；
- **视觉归属**：说明行浅底（`--surface-2`）、上内边距收 0、底边收口，
  让"材料行 + 说明行"看起来是一组，而不是两条材料；
- **文案来源与判据完全复用**：仍是本地 `dashboardWarnRow()` /
  `dashboardEmptyRow()`，内部走全局 `warningText()` / `evidenceCountOf()` /
  同一份 `t()` 表，**没有第二份文案真源**；
- 全局 `warningRow()` / `zeroEvidenceHint()` 与材料页 / 课堂页的渲染代码
  **一个字节没动**（那两张是非 fixed 表格，列宽自动分配，不受此影响）。

顺带一处微调：材料类型后缀加 `·` 分隔（原来 `x.pdf text` 空格相接，看起来
像文件名的一部分）。另给 `.kv-group .kv dt` 补了 `overflow-wrap: anywhere` ——
西语标签（`Respaldado por evidencia` / `Confirmado por revisión`）比中文长
一倍，两列之后每列只剩 ~200px，dt 默认 `max-content` 且没有折行规则
（全局 `.kv` 只给 `dd` 加了）。

改前 / 改后实测（浏览器布局引擎报数，不是估算）
----------------------------------------
用无头 Edge（`--headless --dump-dom`）在预览快照里跑一段只读测量脚本，
取 `getBoundingClientRect()` 与 `Range.getClientRects()` 的真实值。
"改前"= 把 `dashboard.js` 临时改回**用户截图时的原状**（状态格内
`pill + warningRow() + zeroEvidenceHint()`，状态列 180px），量完逐字节还原
（sha256 `67ae79610146698e` 前后一致）。视口 1280×2600，浅色主题。

| 指标 | 改前 | 改后 |
|---|---|---|
| 状态列宽 | 180 px | 160 px |
| 状态格高 | **203 px** | **37 px**（只剩一个 pill）|
| 说明文字块高 | 164 px | — |
| 说明行高 | — | 51 px |
| 说明可用宽度 | 180 px | **910 px** |
| 说明文字行盒数 | **10**（含警告 pill 自身 1 个 → 约 9 行）| **2 行** |
| 材料表高 | 234 px | 119 px |
| **材料卡高** | **303 px** | **188 px**（−115 px，−38%）|

即：说明文字从"180px 宽里折约 9 行、把状态格撑到 203px"，变成"910px 宽里
2 行、51px 高的独立说明行"。这是**量出来的**，不是目测。

交付文件
----------------------------------------
- `src/web/views/dashboard.js` —— 知识健康度改为 `.kv-groups` 内三个
  `.kv-group`（各带 `dashboard.health.*` 小标题，"覆盖"并入"结构"组）；
  材料表新增独立说明行（`material-row` + `material-note[colspan=3]`）；
  类型后缀加 `·`。
- `src/web/styles.css` —— 新增 `.kv-groups`（固定两列网格 + `<=560px`
  单列）、`.kv-group-label`、`.kv-group .kv`（含 dt 折行）、`.material-detail`、
  `tr.material-row > td`（去底边）、`tr.material-note > td`（浅底 + 收上边距）。
- `src/web/i18n.js` —— 新增 3 个 key × 3 语（`dashboard.health.structure` /
  `.validation` / `.review`），共 9 条；删除 1 个冗余 key × 3 语
  （`dashboard.health.coverage`）。
- `scripts/ui_audit.js` —— 新增 18 条断言（见下）；新增
  `--preview <lang> --out <file>` 模式。
- `scripts/README.md` —— 记录 `--preview` 用法。

**`--preview` 是什么、不是什么。** 它输出一份可直接在浏览器打开的静态
HTML 快照：真实 `src/web/index.html` 骨架 + 内联 `styles.css` + **真实渲染
结果**（走与 `--dump` 同一条 `renderPage()` 路径），剥掉全部 `<script>`，
顶部有一条标注写明"静态快照（非实时应用）"。夹具用最坏情况
（`COMPLETED` + `NO_TEXT_EXTRACTED` + 0 证据），这样知识健康度分组与材料
说明行两处改动**同时**可见。它**不参与任何断言、不影响门禁**，只是把真实
渲染结果交给人的眼睛看 —— 本项目的 `ui_audit.js` 明确声明不解析 CSS 布局
（见其头部「未覆」清单），所以 CSS 网格与折行的实际像素只能靠人看。
**它已经兑现了两次价值**：① "组名 = 字段名"的重复缺陷是看 es 快照时发现的；
② 第一版"块级塞在状态格里"仍然细长，也是把快照放大后确认的 —— 两次自动
断言都没抓到。

守卫（`scripts/ui_audit.js`，388 → 406，+18）
----------------------------------------
| 断言 | 抓什么 |
|---|---|
| `dashboard.health.* present in zh/es/ca` × 3 | 三组小标题三语齐备（少一个语 `t()` 会退回中文，不报错、不抛异常） |
| `knowledge health panel renders 3 kv-groups` | 三组真的渲染出来 |
| `knowledge health panel has 3 group labels in zh` | 三个小标题的字面值正确 |
| `no group label repeats one of its own field names` | **组名不得与组内字段名同名**（"覆盖 / 覆盖 12" 那类缺陷） |
| `knowledge health panel no longer uses the ungrouped <dl class="kv">` | 旧的一平到底写法不会回来 |
| `groups sit in the .kv-groups grid container` | 容器存在（少了它 CSS 网格不生效，三组退回贴左边） |
| `css defines the 2-column .kv-groups grid` | CSS 两列规则在位 |
| `css collapses .kv-groups to one column on narrow screens` | 窄屏收一列在位 |
| `status cell holds the COMPLETED pill alone` | 状态格里只有状态 pill |
| `status cell no longer carries the note` | **说明不许再挤回状态格**（本次要修的形状） |
| `material row is marked for its attached note` | 材料行带 `material-row`（CSS 靠它去底边） |
| `note row spans all three columns` | 说明行 `colspan=3` 真的占满整宽 |
| `warning is wrapped in .material-detail` | 警告在说明行里、且是块级 |
| `zero-evidence hint is wrapped in .material-detail` | 零证据提示也在（两块都在） |
| `css drops the bottom border of a material row that has a note` | 材料行去底边（否则说明行像另一条材料） |
| `css gives the note row a tinted, tightened cell` | 说明行浅底 + 收上边距（视觉归属） |

**变异测试（13 个变异，全部精准命中，源码逐字节还原）**

知识健康度（第一轮）：

| 变异 | 结果 |
|---|---|
| A：三组塌回一个 `<dl class="kv">` | 3 failed（kv-groups 计数 / group labels / ungrouped dl） |
| C1：删掉 `.kv-groups` 容器 | 1 failed（grid container） |
| C2：CSS 两列改回单列 | 1 failed（2-column grid） |
| C3：CSS 删掉 560px 窄屏规则 | 1 failed（collapses to one column） |
| D1：结构组组名改成与组内 dt 同名 | 1 failed（no group label repeats） |
| D2：把"覆盖"重新装回独立组（组名 = 字段名） | 1 failed（no group label repeats） |

材料说明行（第二轮，一次性脚本 `cache/mutate_note_row.py`，跑完已清理）：

| 变异 | 结果 |
|---|---|
| M1：说明行退回状态格（colspan 结构整个撤销） | 1 failed（status cell no longer carries the note） |
| M2：材料行丢掉 `material-row` 标记 | 1 failed（material row is marked） |
| M3：`colspan` 从 3 缩成 1 | 1 failed（note row spans all three columns） |
| M4：说明行不再带零证据提示（只剩警告块） | 1 failed（zero-evidence hint wrapped） |
| M5：CSS 不再去掉材料行底边 | 1 failed（css drops the bottom border） |
| M6：CSS 说明行丢掉浅底 | 1 failed（css gives the note row a tinted cell） |

另有一个**第一轮的变异 B**（材料行退回全局 `warningRow()` /
`zeroEvidenceHint()`）在第二轮被 M1 覆盖（M1 就是这个变异的更完整版本）。

变异 B 顺带修掉一个**测试自身的缺陷**：`cellTail.match(...)` 在"一个都没匹配到"
时返回 `null` 而不是空数组，直接取 `.length` 会抛 `TypeError` 把整份 audit
崩掉 —— **崩溃看起来像基础设施故障，实际是断言该报红**。已归一成 `|| []`
再计数。变异 / 测量脚本都是一次性验证工具，跑完即清理（`cache/` 已进 `.gitignore`，
按项目规范不长期保留）；上面两张表就是它们的**结果记录**，含每个变异被
哪几条断言抓住、以及改前/改后的确切数字。**路径只作溯源，文件已不在。**

Test Summary
----------------------------------------
- Base (before this change): 前端 `ui_render_check.js` 250 / `ui_audit.js` 388；
  Python `5368 passed, 5 skipped, 50 deselected`
- Final: 前端 `ui_render_check.js` **250**（未动）/ `ui_audit.js` **406**（+18）；
  Python **`5367 passed, 6 skipped, 50 deselected`**（20m19s，integration 口径）
- Failed: **0**

**关于 `passed 5368→5367` / `skipped 5→6`（差 1 条，已查清，不是本次改动）**

总数不变（`5368+5 = 5367+6 = 5373`），说明是**同一条测试**由 passed 变
skipped，而非增删。用 `-rs` 拿到完整 skip 名单，逐条归因：

| skip 位置 | 原因 | 类别 |
|---|---|---|
| `test_hardening_input_safety.py:116` | 当前环境不允许创建符号链接（Windows 需开发者模式） | 环境 |
| `test_mistakes_center.py:295` ×3 | 知识点支撑的练习少于 3 个 | 夹具数据属性 |
| `test_review_mode.py:882` | zh 是源语言 | 夹具数据属性 |
| `test_startup.py:276` | faster-whisper 已安装，测不到"缺失路径" | 环境 |

判据都只依赖**环境状态**（符号链接特权、faster-whisper 安装与否）或**夹具
数据属性**（练习数、源语言），与 `src/web/*` / `scripts/*` 无关。已实测确认
当前环境 `os.symlink` 报 `WinError 1314`（客户端没有所需的特权），所以那条
在开发者模式关闭后就会 skip。

决定性验证：这 4 个含 skip 的测试文件**全部 0 次**引用前端源码
（`grep -c "src/web" tests/<file>.py` 皆为 0）；而真正读前端源码的
`tests/support.py` / `test_persistence_layering.py` / `test_web_source_manifest.py` /
`test_web_ui.py` 里**一条 skip 都没有**。单独跑后三者 → **105 passed, 0 skipped**。
即：本次改动与 skip 数量变化**无因果关系**。第二轮改动（材料说明行）之后
重跑全量，结果仍是 `5367 passed, 6 skipped` —— 与第一轮一致，未引入新 skip。

Validation
----------------------------------------
- 前端门禁：`ui_render_check.js` → `OK (250 checks)`；`ui_audit.js` → `OK (406 checks)`。**PASS**
- 三语渲染：`dashboard.health.*` 三键齐备；es 预览小标题实测为
  `Estructura` / `Validación` / `Revisión`。**PASS**
- 变异测试：13 个变异全部被对应断言抓住（见上表）。**PASS**
- **真实浏览器验证（本轮新增，补上第一轮 Known limitation #1）**：无头 Edge
  渲染预览快照，1280×2600 浅色 + 520×3000 窄屏两档，深色（系统偏好）与浅色
  （注入 `:root` 覆盖）各一版；并用布局引擎报数得到改前/改后对比表。实测确认：
  ① 两列网格真的铺满卡宽、三组按 2+1 落位；② 说明行真的占满 910px、2 行；
  ③ 520px 下 `.kv-groups` 真的收成一列、说明行仍不溢出；④ 材料卡 303 → 188px。
  **PASS**
- 静态结构：预览快照 `html`/`head`/`body`/`main`/`aside`/`table`/`thead`/`tbody`/
  `dl`/`div`/`tr`/`td`/`colgroup` 开闭**全部配对**（zh / es 各 1 份）；
  `.kv-groups` × 1、`.kv-group` × 3、`.material-detail` × 2、`.material-row` × 1、
  `.material-note` × 1。**PASS**
- 语法：`node --check` 三个改动 JS 全部 OK。**PASS**
- 编译：`compileall -q src tests` → EXIT 0。**PASS**
- 全量回归：见 Test Summary（5367 passed / 0 failed / 6 skipped 全部环境相关）。**PASS**
- 视觉快照：`scripts/ui_audit.js --preview zh --out cache/preview-dashboard-zh.html`
  → 33867 bytes；`--preview es` → 34111 bytes（均为 UTF-8 真实字节数，由
  `Buffer.byteLength` 得出 —— 初版误用 `shell.length`（UTF-16 码元数）并把
  结果标成 bytes，中文下差出约 4KB，已修）。**PASS**

Known limitation (explicit, not hidden)
----------------------------------------
1. **浏览器验证是一次性脚本，不是门禁。** 无头 Edge 跑的是**静态快照**
   （固定夹具：一份 `COMPLETED + NO_TEXT_EXTRACTED + 0 证据` 的材料），不是
   跑起真实服务、喂真实数据的端到端验证；而且测量脚本与截图都不参与
   `ui_audit.js` 的断言，**下次改动不会自动重跑**。`ui_audit.js` 依旧不解析
   `styles.css` 的布局结果（头部「未覆」清单未变）。所以 CSS 回归仍靠人工
   目视 + `--preview` 快照。
2. **三组在两列网格里有一格留白。** 第一行放"结构 + 证据支持度"，第二行只有
   "人工审核"，右下为空。这是 3 组 + 2 列的算术结果，不是缺陷；若将来补上
   第四个语义组（例如把 `review_summary` 的 `rejected` / `kept_unverified`
   也显示出来，与「我的课程」页的 `MC_REVIEW_KEYS` 对齐），网格会自然填满。
   **本次没有动信息量** —— 面板仍只显示原来的 9 项指标。
3. **`.kv` 的其余 20 处使用点未动。** 它们是详情页的字段列表（值多为长
   文本），"标签左 / 值右"的紧凑写法在那里是合适的。本次只针对 dashboard
   的统计面板。
4. **说明行的 160px 状态列宽与 560px 断点是经验值。** 没有针对每个视口宽度
   逐一调过；已实测 1280px 与 520px 两档正常，更窄的视口由
   `overflow-wrap: anywhere` 与卡片横向滚动兜底。
5. **说明文字本身没有缩短。** 那段文案（`warn.emptySuccessHint` +
   `warn.empty.NO_TEXT_EXTRACTED`）是 i18n 表里的三语条目，本次只改容器不改
   文案 —— 缩短它会影响三语与既有断言，属另一件事。**两段说明在语义上确有
   重叠**（都建议"扫描件转图片走 OCR"），那是 D1 与 D4 两条规则各自触发的
   结果，材料页 / 课堂页同样如此，本次未动。
6. **材料页 / 课堂页的"逐字节不变"是推断，不是断言。** 依据是：`app.js` 与
   `views/materials.js`、`views/courses.js` 本次**未编辑**（`node --check`
   通过），且 `ui_audit.js` 里"三处一致"的 4 条断言 × 3 个站点全部通过。
   没有 golden 文件做字节级 diff。

---

# TASK-78 今日课程卡两行重排（2026-09-24）

## 问题与目标

`#/today` 的「今天的课程」在半宽 `grid-2` 卡内仍用 4 列表格（课堂 / 状态 /
知识点 / 待审核）。中文表头因此会逐字竖排，种子标题
`15:00–17:00 Teoria | Dario Cottava | Aula Q2/1009` 被挤成多行。

本次目标是把该区域改为课程页已经过两轮验证的 `.session-row` 版式，从结构上
去掉表格与表头。课程分组、课堂深链、右侧「整堂处理」、failed / retry 语义、
复习 / 练习 / 评估 / 关注卡片及后端均不变。

## 实现

- `parseSessionTitle` 与 `SESSION_TITLE_PLACEHOLDER` 从
  `views/courses.js` 原样上移到 `app.js`。`app.js` 先于页面视图加载，课程页与
  今日页共用唯一标题解析实现；`Aulas?` 教室识别正则逐字保持不变。
- `pageToday()` 用 `todaySessionRow()` / `todayClassesCard()` 替换
  `classesTable()`：复用 `.session-day-card/.session-row/.session-row-main/
  .session-row-action`，每行两行文字、右侧处理按钮，非 0 计数才另起 meta 行。
- 全部课程继续按课程分组并保留 h3；单课程筛选省略重复组头。今日页不再逐行
  重画日期，页面副标题已给出同一天。
- 零后端改动、零 migration、零新 i18n key、零新增 CSS。

## 守卫

- `scripts/ui_audit.js`：今日课程区无 table；有 session card / row；行数、
  分组 h3、完整深链、按钮双属性、按钮为锚点兄弟节点、两层结构、标题解析、
  占位符不泄漏、非 0 / 全 0 计数、空态、单课程口径、es/ca 无 CJK 均有断言。
- `scripts/ui_render_check.js`：覆盖 session-row、深链接、process-session、
  `classes_today: []` 的 `today.noSessions`、单课程筛选。
- `tests/test_web_ui_invariants.py`：新增结构不变量，今日课堂渲染体不得含
  `<table>` 或 `class="data"`。

## 验证

```text
node scripts/ui_audit.js
  UI audit OK (517 checks)

node scripts/ui_render_check.js
  UI RENDER CHECK: OK (314 checks)

node --check src/web/app.js
node --check src/web/views/dashboard.js
node --check src/web/views/courses.js
  PASS

pytest -q tests/test_web_ui_invariants.py
  12 passed

pytest -q -m "not integration" tests/test_student_today.py \
  tests/test_web_ui.py tests/test_web_ui_invariants.py \
  tests/test_multi_course.py tests/test_exercise_ui.py tests/test_learning_view.py
  520 passed in 144.36s
```

真实服务 `127.0.0.1:8765` + 真实课程数据，在 `#/today` 实测：1280px 下今日卡
`scrollWidth == clientWidth == 468`，3 行无溢出，页面无溢出；520px 下今日卡
`scrollWidth == clientWidth == 462`，3 行均无溢出，处理按钮仍位于卡内；深色 /
浅色各检查一版，无控制台错误。真实 DOM 为 2 个课程组 / 3 张日卡 / 3 行 / 3
个完整 session 链接 / 3 个处理按钮，今日区 table=0、原始 `|`=0、`图中未显示`=0。

全量 `pytest -m "not integration"` 已启动，但在工具 600 秒上限前没有返回结果；
明确记为**未完成**，未宣称全量通过。520px 整个文档仍有既有顶栏健康 pill 横向
溢出，但今日课程卡和每行自身溢出均为 0；该顶栏问题不属于本任务。

完整记录见 `docs/task-78-today-classes-restack.md`。
