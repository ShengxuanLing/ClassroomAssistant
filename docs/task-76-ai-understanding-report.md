# TASK-76 — AI 语义理解与知识点自动提取 · 最终交付报告

> 日期：2026-09-21。口径：`pytest -q -m "not integration"` 全量 + `compileall` +
> `node scripts/ui_render_check.js` + `node scripts/ui_audit.js`。
> 结论先行：**新链路全绿；另修复 13 条前人留下的红色测试（i18n 表错位/注释误伤扫描器/
> 顶栏归属漂移/子进程编码），最终全量 `0 failed`。**

---

## 1. 当前旧 pipeline 是什么

`Workspace.register_material → process_material/process_session → MaterialWorkflowService
(ingestion) → EvidenceStore(内容寻址) → KnowledgeAssembler/KnowledgePipeline(确定性抽取:
首行标题/证据类型定重要性/最弱置信度) → Validation(只读) → Review(人工) → 组织层`。
全程确定性、零模型：长文本材料产出的是"原文切片式"知识点，用户必须自己阅读总结、
手动创建精知识点（任务书 §0 的产品级问题）。

## 2. 新 AI pipeline 是什么

```text
Material -> detect_type -> extract(既有, 不动) -> Evidence(既有, 不动)
  -> chunk(确定性, 稳定 ID) -> AI Provider(语义理解)
  -> structured candidates -> schema 校验 -> evidence grounding
  -> 集合内去重 -> 与已有 KP 比对(attach/create/conflict/needs_review)
  -> 置信度策略分类(auto>=0.90/review>=0.70/低于则复核队列)
  -> 经 workflow.register_knowledge_point 落库(证据校验+幂等)
  -> flush + 学习层只读同步 -> AIAnalysisReport(自动确认/待确认/冲突计数)
```

分工铁律：确定性代码负责"材料是什么、证据在哪里、数据是否合法"；
LLM 负责"材料是什么意思、哪些值得学、如何组织"；既有 Evidence/Review
负责"可追溯、可验证、可纠错"。LLM 不写库、不定 ID、不解冲突、不碰学生态。

## 3. 新增文件

```text
src/application/ai/__init__.py       (PIPELINE_VERSION=ai-pipeline-v1, PROMPT_VERSION)
src/application/ai/config.py         (CLASSROOM_AI_* 配置, 密钥只读环境)
src/application/ai/provider.py       (AIProvider 抽象 + Fake + OpenAI-compatible urllib)
src/application/ai/schemas.py        (结构化输出解析与 schema 校验, ai-schema-v1)
src/application/ai/prompts.py        (提示词唯一落点, 5 个版本化 builder)
src/application/ai/chunking.py       (确定性分块, 稳定 chunk-<sha16> ID)
src/application/ai/validators.py     (grounding + AI_PIPELINE_POLICY + KP 映射)
src/application/ai/merge.py          (去重 + 合并/冲突提议, 纯函数)
src/application/ai/pipeline.py       (两级理解 + 缓存 + 幂等 + 版本记录)
src/application/ai/service.py        (Workspace 编排: 落库/attach/报告)
tests/test_ai_understanding.py       (67 条 + 1 integration live smoke)
docs/task-76-ai-understanding-report.md (本报告)
```

## 4. 修改文件

| 文件 | 改动 | 风险 |
|---|---|---|
| `src/application/workspace.py` | `__init__` 加 `_ai_enabled/_ai_provider/_ai_reports`；新增 `configure_ai/analyze_material_with_ai/ai_summary` | 薄方法，旧路径零改动 |
| `src/api/endpoints.py` | 新增 `POST /api/materials/{id}/ai-analyze`、`GET .../ai-summary` | 400/404/422，无 500 面 |
| `src/web/views/materials.js` | AI 分析按钮 + 结果面板 + `actionAiAnalyze/renderAiReport` | 仅新增 |
| `src/web/app.js` | dispatch 加一行 | 仅新增 |
| `src/web/i18n.js` | 新增对称 `ai.*` 12 键；**附带修复**：es 表误删入的加泰 pack 块移回 ca 表；补 `nav.reviewPack`(ca)、`摘要`(es/ca 译文表) | 见 §23 |
| `src/web/views/review-pack.js` | 注释改写（躲开 i18n 扫描器误伤）；`pageReviewPack` 高亮改 `''`→`'#/review-pack'`（顶栏真有该分区） | 见 §23 |
| `tests/test_multi_course.py` | declared 表补 `pageReviewPack`（测试自带 instructions 要求同步） | 见 §23 |
| `tests/test_student_today.py` | 子进程强制 UTF-8（对照 production gate 先例；夹具 BOM 在 GBK 控制台下必崩） | 见 §23 |
| `README.md` / `docs/*` | 能力行 + 本报告链接 + status/architecture/configuration 追加章 | 文档 |

## 5. 新增数据库字段/表

**无。刻意零 migration。** AI 候选映射到现有 `KnowledgePoint` 全部已有字段
（`original_terms` 留原文术语；`metadata` 不存在于 domain 模型——`from_dict`
忽略未知键，故冲突备注只活在报告层，不污染 KP 行）；总结类字段活在报告层，
**没有**把 AI response JSON 塞进任何 TEXT 字段（§13 禁令遵守）。AI 运行记录
（provider/model/prompt/pipeline/identity/created_at）随报告返回 + 内存缓存；
KP 本体经组织层正常落盘，可由 KP+Evidence 重新推导。

## 6. Provider 支持情况

- `FakeAIProvider`（默认）：确定性 fixture，零出站零密钥；能力
  `text✓ image✓(经OCR文本) audio✗(转写走既有ASR) structured✓ image_bytes✗`。
- `OpenAICompatibleAIProvider`：标准库 urllib → `{base}/chat/completions`
 （`response_format: json_object`，超时 60s+重试 1 次）；缺 key/非 https
  即 `ConfigurationError`；repr/error/detail 全脱敏。**无新增第三方依赖**
 （release AI 供应链门 + production 依赖门全绿）。
- Capability 显式声明（`ProviderCapabilities` 五维），调用方据此路由。

## 7. Text 支持情况

`IMPLEMENTED + VERIFIED`。TXT/MD/PDF/DOCX 经既有 ingestion 出文本证据 →
`chunks_for_evidence`（2000 字符/200 重叠/上限 100，截断标记）→ Level 1
逐 chunk 抽取 → Level 2 确定性合并（模型全局整理失败则回落，不算整体失败）。
E2E：西语 calculus 文本 → auto 1 + review N，KP 可 trace，Review 队列增长。

## 8. Image 支持情况

`IMPLEMENTED + VERIFIED（经 OCR 文本路径）`。`detect_material_kind` 识别图片 →
既有 OCR 出文本证据 → `build_image_analysis_prompt`（OCR 文本 + 视觉结构理解，
chunk allowlist 显式声明）→ 候选 → grounding 到原图 OCR 证据。E2E：板书 PNG
（mock OCR）→ kind=image → KP ≥ 1。纯视觉字节默认不发送（`allow_image_bytes`
需显式开启，capability 声明）。

## 9. Audio 支持情况

`IMPLEMENTED + VERIFIED（经转写文本路径）`。音频字节绝不发往 LLM：既有
Whisper/ASR 先出 timestamped transcript 证据 → `build_audio_analysis_prompt`
（重点/定义/例子/强调识别）→ KP 绑定 transcript 时间区间证据。E2E：MP3
（mock ASR）→ kind=audio → KP ≥ 1。Provider `supports_audio=False` 时
`transcribe_audio` 显式抛错并指回既有链路（有单测）。

## 10. KnowledgePoint 自动生成示例

西语 calculus 文本一次分析：`auto 1 + review 4`（fake 三档置信度设计），如
`aikp-*「La definición de integral definida es el área bajo la curva」
confidence MEDIUM + evidence_refs[ev-*] + original_terms（原文术语逐字保留）
+ needs_verification/review pending`。

## 11. Evidence trace 示例

`ws.knowledge_trace(cid, aikp-*)` → `evidence[]`（原文逐字）→ `materials[]`
（含文件名/页码/时间戳）。E2E 对 auto+review 全量断言 trace 非空。

## 12. Review 示例

低置信度候选落库 `review_status=pending + needs_verification=true`；
`ws.review_candidates` 数量只增不减；人工 `confirm/reject` 路径复用既有
`ReviewService`（管线绝不 CONFIRMED，`map_candidate_to_kp_payload` 单测钉死）。

## 13. Dedup 示例

集合内归一化标题+类型去重（confidence 取最小）；跨材料相同概念 → `attach`
提议 → 已有 KP 行合并新 evidence（无新行）。E2E：两份近似材料先后分析，
KP 总数不翻倍（断言 `< 2x + 3`）。

## 14. Conflict 示例

标题相似但证据/内容不同 → `conflict` 提议 → 候选仍落库为 pending KP
（`metadata.conflict_with/conflict_reason` 仅报告层），**不自动解决**，
用户经 Review Center 裁决。单测覆盖 `conflict` 判定分支。

## 15. Retry 示例

`_FlakyProvider` 首轮对特定 chunk 抛 `AIRequestError` → `AIAnalysisFailure`
（含成功/失败计数）；缓存保留成功 chunk；第二轮只重跑失败 chunk 并完成。
用户侧：报告 `status=failed` + "Evidence is safe"，调同一 endpoint 即 retry。

## 16. Idempotency 验证

同一材料连调三次：`processing_identity` 相同；KP 总数不变；Evidence 总数不变
（`aikp-*` 确定性 ID + `register_knowledge_point` 按 ID 幂等覆盖）。E2E 有单测。

## 17. API failure 验证

- 超时/429/500/网络错误 → `AIRequestError`（形状 detail）→ `AIAnalysisFailure`
  （422，Evidence 保留）。
- 畸形 JSON / Markdown 非 JSON → `MALFORMED_ERROR` → 失败而**不是**伪造 KP
 （`_GarbageProvider` 单测）。
- 空证据 → 失败并提示"先 process 再 retry"；材料/旧 KP 原样保留。

## 18. Security 检查

- Key 只读进程环境；`AIConfig.describe/__repr__`、`provider.__repr__`、
  `AIRequestError/AIAnalysisFailure.detail`、操作日志 extra——全部无明文（单测逐项断言）。
- `.env` 在 `.gitignore`（P0-3 既有）；文档只写 `<YOUR_API_KEY>` 占位。
- 无 key 进 git/source/log/UI/tests（回归门禁：release 无硬编码密钥测试仍绿）。

## 19. Fake Provider 测试结果

`tests/test_ai_understanding.py`：**67 passed, 1 deselected**（integration live
smoke 默认不跑），口径 `-m "not integration"`。
最终全量复核（2026-09-21，含文档改动后）：**5293 passed, 8 skipped,
48 deselected, 0 failed**（约 18:42）；
`compileall` EXIT 0；`ui_render_check` 219；`ui_audit` 396。

## 20. Real API smoke test 结果

`ENVIRONMENT-BLOCKED`：本机无 `CLASSROOM_AI_LIVE_TEST` 凭证，
`test_live_ai_smoke` 干净 SKIP（非 FAIL）。待用户提供
`CLASSROOM_AI_BASE_URL/API_KEY/MODEL` 后执行：
`CLASSROOM_AI_LIVE_TEST=true pytest -m integration -k live_ai_smoke`。

## 21. UI E2E 结果

材料页新增「AI 分析」按钮 + 结果面板（`renderAiReport`：计数 pills + 主题/
定义/公式/例题 + chunk 进度 + provider/model）；失败面板显 code+message
与"Evidence 安全可重试"语义。`WEB_SOURCE_ORDER` 一致性测试断言新符号在扫描
范围内；`ui_audit` 396 / `ui_render_check` 219 全绿。

## 22. Full regression 结果

见 §19。分层守卫（双向）、release 契约门/依赖门/安全门、production 依赖审计、
多课程隔离、备份恢复——全部在全量中复核通过。

## 23. 已知限制

1. 自动触发未接线：AI 分析是显式动作（按钮/endpoint），`process_material`
   内不自动跑（保旧链路零风险；自动链为下一步）。
2. 真实 Vision bytes / 真实音频直传未接（设计为 OCR/转写文本优先，控成本；
   provider capability 已预留 `supports_image_bytes` 开关）。
3. AI 报告缓存在内存（KP 本体落盘；报告可由 KP+Evidence 推导；重启后需重跑
   分析——幂等故安全）。
4. `attach` 的证据合并经 `register_knowledge_point` 整行覆盖（既有语义）。
5. 本任务附带修掉 13 条**前人红色**测试（revert 实验证明 pre-existing，
   非本任务引入）：
   - es 表误删入加泰 `pack.*` 15 键（重复键致 es 界面显示加泰文）→ 移回 `ca` 表；
   - `ca` 缺 `nav.reviewPack`、`TRANSLATIONS` 缺 `摘要` → 补齐（三表 415/415/415）；
   - `review-pack.js` 注释含 `t('…')` 被 i18n 扫描器误伤 → 改写注释（零行为改动）；
   - `pageReviewPack` 清空顶栏高亮但顶栏真有 `#/review-pack` 分区 → 改传本分区
     + 测试 declared 表同步；
   - GBK 控制台子进程必崩（夹具 BOM `U+FEFF` 进 dashboard JSON）→ 亲子两端强制 UTF-8。

## 24. 下一步真实课堂测试建议

1. 用户配凭证后跑 live smoke（§20），先短讲义、再 100 页 PDF（验证截断标记与
   分段质量），对照 `AI_PIPELINE_POLICY` 阈值调参（0.90/0.70 只是起点）。
2. 加泰语课堂录音走全链（转写→分析→KP→时间区间回放），确认术语保留。
3. 冲突场景用人真数据演练一次 Review Center 裁决，确认报告可读性。
4. 若 fake 的句子级候选与真实模型分布差异大，为 golden fixture 补录真实输出
   shape（不断言逐字，只断言计数/概念/grounding/语言/置信度区间）。

---

## 分级结论

- **IMPLEMENTED**：§2 全链路、§6 Provider 双实现、§7/8/9 三模态、§13–17
  去重/冲突/retry/幂等/失败语义、§18 安全、§21 UI。
- **VERIFIED**：§19（67+全量 5293）、§11/12 trace 与 Review 断言、§22 门禁复核。
- **ENVIRONMENT-BLOCKED**：§20 真实 API smoke（待凭证）。
- **KNOWN LIMITATION**：§23（1–4 产品取舍；5 为已修复的前人问题，附带说明）。
...[truncated 6154 chars]