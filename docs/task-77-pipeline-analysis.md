# TASK-77 Pipeline Analysis (analysis phase, no code changed)

Date: 2026-09-21. Source of truth: code read at `src/` HEAD (TASK-76 state).

## 1. Material registration entry

- Facade: `src/application/workspace.py:709`
  `register_material(course_id, path, session_id=None, language=None, *, filename=None) -> dict`
  delegates to `context(course_id).workflow.register_material(...)` inside `_atomic()`,
  then `_flush_materials + _flush_evidence + _record_op(register_material)`.
- Real impl: `src/application/material_workflow.py:273`
  `register_material(path, session_id, language, *, filename)`.
  `_validate_and_register()` (`:327`): PATH_TRAVERSAL / UNSUPPORTED_EXTENSION /
  FILE_NOT_FOUND / ZERO_BYTE / OVERSIZED -> `_failed_record()` (`:477`,
  `processing_status=FAILED`, no throw). SHA256 (`:359`), deterministic
  `mat-<sha24(course|filename|hash)>` (`:832`), dup-reassign (`:382ff`),
  `atomic_copy` to `layout.bucket_for(category)/<course>/<mat><ext>>` (`:441`),
  registry `data/materials/<course>.json` (`:885`).
- HTTP: `src/api/endpoints.py:304` `POST /api/materials` stages upload under
  `upload_root/<hash>/`, calls `workspace.register_material(...)`, removes staged copy.

## 2. Material processing entry (unified API entry = Workspace)

There is no single function; there is a 3-layer delegation and `Workspace`
is the unified **API** entry:

```text
POST /api/materials/{id}/process
-> endpoints.py:354 process_material():357 workspace.process_material(course, mid)
-> workspace.py:799 process_material(course_id, material_id)
-> processing_service.py:284 ClassroomProcessingService.process_material(material_id)
-> material_workflow.py:544 MaterialWorkflowService.process_material(material_id)
-> evidence_ingestion.py:680 ingest(Material) -> IngestionReport
-> back: job SUCCEEDED/FAILED + _sync_course_knowledge + _flush_processing
```

- `workspace.py:799-832`: `_atomic`, `ctx.processing.process_material`,
  `_sync_course_knowledge` (SUCCEEDED only), `_flush_processing`. D3 fix reads
  `job["status"]` (`JOB_SUCCEEDED`/`JOB_FAILED`), not registry `processing_status`.
- `processing_service.py:284-329`: idempotent SUCCEEDED/CANCELLED early-return,
  `JOB_RUNNING/STAGE_INGESTING`, `workflow.process_material()`,
  COMPLETED -> SUCCEEDED + evidence_ids + warnings + quality, else FAILED.
- `material_workflow.py:544-608`: idempotent COMPLETED + `_evidence_present`
  early-return (Task 45 restart fix), FAILED non-retryable / max_attempts guard,
  `attempts+=1`, `_to_material(:688)` -> `ingestion.ingest(:571)`,
  `total==0` -> `_document_outcome(:656)` (DOCUMENT_PARSE_FAILED vs
  NO_TEXT_EXTRACTED warning), else COMPLETED + evidence_ids.
- `retry_material`: `workspace.py:849` -> `processing.retry_failed_material`
  (`processing_service.py:348`), then flush materials/evidence/knowledge/org.
- `process_session`: `workspace.py:834` -> `processing.process_session`
  (`processing_service.py:331`, per-material isolation + course-scope
  `assemble_knowledge()`), then session link + flush.

## 3. Evidence creation

- Entry: `src/evidence_ingestion.py:680 ingest(material, dry_run=False)`,
  `:713 ingest_many`, `:745 ingest_session`.
- Adapters (`:657ff`): Note / Audio (asr_provider) / OCR (ocr_engine) /
  Document extractors -> `List[Evidence]`.
- Domain: `src/evidence_extractor.py:29 extract()`, `src/document_evidence.py:260`,
  `src/models.py:355 to_transcript_evidence()`, providers
  `src/asr_provider.py`, `src/ocr_provider.py`, `src/document_input.py:parse_document()`.
- Store: `src/evidence_store.py:293 add() -> ADDED/DUPLICATE/REJECTED`,
  canonical `evidence-key-<sha256(source+location+content)>`, `351:add_many`.
- Persistence: `src/persistence/repositories/evidence.py:60 save/80 save_many`
  (canonical_key UNIQUE), `src/application/persistence_wiring.py:423
  save_evidence_store`, `workspace.py:1985 _flush_evidence`.

## 4. Existing deterministic extraction (no LLM)

- `src/knowledge_pipeline.py:50 KnowledgeExtractor.extract` (cluster by
  supporting groups, `kp-<sha16(anchor,type)>`), `:148 KnowledgePipeline`
  (integrate -> conflicts -> extract -> validation).
- `src/knowledge_assembly.py:235 process_evidence / 268 process_store`
  (canonical `sort(evidence_id)`, idempotent).
- `processing_service.py:385 assemble_knowledge()` default **course** scope via
  `524:_course_material_ids()` (Task 68 cross-course leak fix), never whole store.

## 5. AI analyze entry (TASK-76, explicit trigger, opt-in)

- `workspace.py:990 configure_ai(enabled, provider)`, `:1012 ai_enabled`,
  `:1016 analyze_material_with_ai(course_id, material_id, *, content_language,
  provider)` (`:1037 require_enabled`, `:1040 get_material`,
  `:1042 AIAnalysisService().analyze()`, `:1050 _sync_learning_knowledge`,
  `:1051 _flush_knowledge+_flush_organization`, `:1054 _ai_reports[(course,mat)]`,
  op log), `:1070 ai_summary()` read-only cache, missing -> NotFoundError 404.
- `src/application/ai/service.py:96 analyze(ctx, record, ...)`:
  evidence read-only (`get_by_source`), `detect_material_kind`,
  `pipeline.analyze_material`, `ground_and_classify`, `kp_payloads(auto/review)`
  + conflict payloads, `workflow.register_knowledge_point` (idempotent),
  `_attach_evidence`, `processing_identity -> AIAnalysisReport`.
- `src/application/ai/pipeline.py:169 processing_identity -> ai-<sha16>`,
  `:187 detect_material_kind -> text|image|audio|unknown`,
  `:266 analyze_chunk` (cache `(chunk_hash,model,prompt)`), `:340 analyze_material`,
  `:409 _merge_chunk_results`, `:493 build_summary`, `:547 ground_and_classify`,
  `:612 kp_payloads`.
- `chunking.py:188 chunks_for_evidence` (model never sees real evidence_id),
  `validators.py:91 ground_candidates / 196 map_candidate_to_kp_payload`
  (0.90 auto / 0.70 review, `AI_PIPELINE_POLICY`), `merge.py:60 dedup / 97
  propose_merge_with_existing (create/attach/conflict)`,
  `provider.py: FakeAIProvider (deterministic fixture) /
  OpenAICompatibleAIProvider (stdlib urllib, {base}/chat/completions,
  response_format json_object)`, `config.py (CLASSROOM_AI_* env, key env-only)`.
- Routes: `endpoints.py:380 ai_analyze_material (POST .../ai-analyze, 422 on
  failure)`, `:394 ai_material_summary (GET .../ai-summary, never calls LLM)`.
- UI: `src/web/views/materials.js:39 button data-action=ai-analyze, :161
  actionAiAnalyze (POST ai-analyze -> renderAiReport:118 -> route())`,
  `app.js:720` delegation.

## 6. KnowledgePoint persistence

- Model `src/models.py:188 KnowledgePoint` (+ `ValidationStatus/ReviewStatus`).
- Write: `material_workflow.py:710 register_knowledge_point(payload)` requires
  `evidence_refs ⊆ store` else NotFoundError, `org.register_knowledge_point`
  idempotent. Called by deterministic assemble AND AI `ai/service.py:250/253/256`.
- Read: `application/knowledge_service.py:119 get_knowledge_points(...)`,
  `workspace.py:873 knowledge_points (conflict normalisation), :919
  knowledge_point, :922 knowledge_evidence`.
- Store: `persistence/repositories/knowledge.py:122 save / 141 save_many /
  165 save_structure / 188 load` (`course_knowledge_points PK(course,knowledge)`),
  flush `workspace.py:1992 _flush_knowledge, :2009 _flush_organization`,
  `:1619 _sync_learning_knowledge -> register_course_knowledge_points`
  (one-way KP -> learning, never writes student events).

## 7. Review persistence

- Domain `src/knowledge_review.py:17 ReviewDecision, :88 ReviewRecord`,
  `models.py:102 ReviewStatus(pending/confirmed/rejected/kept_unverified)`.
- Service `application/knowledge_service.py:411 ReviewService:434
  get_review_candidates, :440 confirm, :473 reject, :493 keep_unverified` —
  never auto-confirms CONFLICTED. Flush `:2005 save_review_records`.
- Store `persistence/repositories/review.py:48 save / 74 save_many / 85 load`
  + `22:CourseReviewRepository` (per-course review_id isolation).
- AI feeds queue: `map_candidate_to_kp_payload -> review_status=pending,
  needs_verification=True`; conflict payloads carry `conflict_with/reason` in
  metadata, never auto-resolved.

## 8. Material UI refresh path

- `src/web/index.html:87-99` fixed order `api.js -> i18n.js -> app.js ->
  views/*.js` (zero-build, shared globals).
- `api.js:21 api(path, {query, method, body})`, `ApiError{code, message}`.
- `views/materials.js:6 pageMaterials()` — `GET /materials + /sessions +
  /processing`, rows show `filename/material_id/type/status/warningRow/
  zeroEvidenceHint` + buttons `process/retry/evidence/digest/ai-analyze`,
  panels `#upload-form/#evidence-panel/#digest-panel/#ai-panel`. `:83
  wireUploadForm (POST /materials FormData -> toast -> route())`.
- Actions `views/students.js:398 actionProcessMaterial (POST .../process ->
  toast status+evidence count -> route()), :414 actionRetryMaterial, :429
  actionMaterialEvidence`; `materials.js:161 actionAiAnalyze`.
- `app.js:692` click delegation, `:742 route() (showLoading -> loadChrome/
  loadSidebar -> page* -> wireUploadForm)`, every mutation ends with full
  `route()` re-fetch, no local cache.

## 9. Conclusion for TASK-77 auto-trigger design

- Unified auto-trigger point: `Workspace.process_material()` (and
  `Workspace.process_session()` for batch). `ClassroomProcessingService` and
  `MaterialWorkflowService` stay deterministic (layering guard forbids
  `src.application.ai` -> persistence imports; AI lives in Workspace thin
  method, same shape as `analyze_material_with_ai`).
- Non-blocking: AI failure must enrich the returned job dict with an `ai`
  sub-object and never flip material `SUCCEEDED` -> `FAILED`.
- Idempotency already exists (`ai-<sha16>` + `aikp-*`); auto-trigger reuses it.
- Persistence gap: `_ai_reports` is memory-only; KP/Evidence/Review already
  persist via DB. Summary needs file-level persistence without DB migration
  (report layer, re-derivable from KP + Evidence).
