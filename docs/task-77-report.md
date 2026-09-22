# TASK-77 Final Report — Automatic AI Material Processing & Real-World Multimodal Pilot

日期：2026-09-21。产品角色转换完成：机器负责理解和整理，人只确认不确定内容。

## 43.1 Old Pipeline（TASK-76 结束时）

```text
upload -> ingestion -> Evidence -> manual KnowledgePoint
                                -> (manual click) AI Analyze -> summary/KP
```

AI 是显式按钮，用户仍承担"读原文、总结、建 KP"的主要工作。

## 43.2 New Pipeline（TASK-77 完成后）

```text
upload -> ingestion -> Evidence -> automatic AI -> summary -> KnowledgePoint
      -> Evidence grounding -> validation -> dedup -> conflict/confidence
      -> High ──────> auto accept ──> Knowledge Base
         Low/Conflict > Review Center ──> human decision
```

`Workspace.process_material()` 摄取成功后自动分析；`process_session()` 逐份
串行自动分析；失败只污染 `job["ai"]`，材料保持 SUCCEEDED。

## 43.3 Files Changed

| 文件 | 改动 |
| --- | --- |
| `src/application/workspace.py` | `process_material/process_session/retry_material` 自动触发；`retry_ai_analysis`；`_maybe_auto_ai(_session)`；`_ai_report_path/_persist/_load/_brief`；`ai_summary` 落盘回退；`processing_job` 富化；`AI_AUTO_*`/`AI_REPORT_SUBDIR` 常量；`import json`+`atomic_write_bytes` |
| `src/application/ai/service.py` | `_mirror_to_structure`：AI KP 镜像进 `processing.structure`（重启可恢复，§15 实测缺口） |
| `src/application/ai/provider.py` | `_post_json`：429/5xx 有界退避重试（0.5s→1s→2s 上限），401/403 直 fail，畸形响应可诊断，timeout 必传参；detail 只有 host/model/http_status |
| `src/application/bootstrap.py` | `_build_ai_provider`（env→disabled/fake/real，Key 只读环境）；`Runtime.ai_mode`；`describe_runtime` 加 `ai_mode/ai_enabled`；`build_runtime` 自动 `configure_ai` |
| `src/web/views/materials.js` | `renderAiStages` 清单；`aiAutoLine`；报告兼容总结视图计数形状；`prerequisites/difficulties` 区；`loadAiSummaryIntoPanel`；修掉"报告闪一下就没"（先 `route()` 再回填）；失败卡片+安全注+重试按钮 |
| `src/web/views/students.js` | `actionProcessMaterial` 播报自动结局；处理后回填 AI 面板 |
| `src/web/i18n.js` | `ai.retry/autoDone/autoFailed/safeNote/notAnalyzed/stages/kpSummary/prereqs/difficulties` × zh/es/ca（415→424 key/表，三表一致） |
| `tests/test_ai_auto_pipeline.py` | 新增 26 条（§38 全覆盖，见 43.13） |
| `tests/test_live_ai_smoke.py` | 新增 2 条 integration（默认 SKIP/deselected） |
| `docs/task-77-pipeline-analysis.md` | 新增（§3 八项调用链记录） |
| `docs/task-77-real-material-evaluation.md` | 新增（人工质检 7 条 KP + 总结 + 5 局限） |
| `docs/configuration.md` | §8 补 `CLASSROOM_AI_LIVE_TEST` + `ai_mode` 健康态说明 |
| `docs/architecture.md` | AI 章补自动触发/落盘/退避三段 |
| `docs/status.md` | 追加 TASK-77 条目 |
| `README.md` | AI 行改为自动语义 |
| `AGENTS.md` | 变更记录追加 TASK-77 |

`tests/test_ai_understanding.py`：**未删除未改动**（67 条全绿）。

## 43.4 Database Changes

```text
none（零 migration，与 TASK-76 一致）
```

- KP/Evidence/Review 走既有仓储；总结走报告层。
- 新增的只是**衍生缓存文件** `materials/ai-reports/<course>/<material>.json`
 （原子写，内存未命中时回退，丢了可由 KP+Evidence 重推）。

## 43.5 AI Provider

- `FakeAIProvider`（默认，确定性 fixture，零出站）：回归 + 无凭证体验。
- `OpenAICompatibleAIProvider`（stdlib `urllib`，`{base}/chat/completions` +
  `response_format: json_object`，零新增依赖，release 门禁仍绿）。
- Key 只读进程环境；`repr`/`describe`/异常/`detail` 全脱敏（有测试钉死）。

## 43.6 Automatic Trigger

```text
process_material() -> AI：已完成
process_session()  -> AI（逐份串行）：已完成
CLASSROOM_AI_ENABLED=false：作业形状与 TASK-76 完全一致（无 ai 键），旧链路零改动
```

## 43.7 Multimodal

| 通道 | 状态 | 说明 |
| --- | --- | --- |
| PDF | IMPLEMENTED + VERIFIED（live） | 真实 2 页 PDF → 摄取 → 自动分析（auto 3/review 2） |
| DOCX/TXT | IMPLEMENTED + VERIFIED（live） | 12 句讲义 → auto 12，主题 7/7 命中 §9 |
| Image | IMPLEMENTED + VERIFIED（Fake 自动） | OCR 文本 → LLM（Vision 字节默认不发送，`allow_image_bytes` 显式才行） |
| Audio | IMPLEMENTED + VERIFIED（Fake 自动） | Mock 转写 → LLM；trace 到 `.mp3` 材料；timestamp 由转写证据携带 |

## 43.8 Real API

```text
LIVE API: PASS（agnes-3.0-flash @ apihub.agnes-ai.com，2026-09-21 实测）
- test_live_ai_smoke.py：2 passed（SKIP 逻辑保留，无凭证仍干净跳过）
- test_ai_understanding.py::test_live_ai_smoke：1 passed
- 初测 401 定位到用户粘贴的 key 自带 <> 占位括号，去掉即 200（网关原文 Invalid token）
```

## 43.9 Real Material

```text
REAL MATERIAL: PASS（live 模型 + 真实 PDF + UAB 风格讲义）
```

## 43.10 Automatic KnowledgePoint

```text
manual creation: 0（pilot 脚本与 HTTP E2E 全程无手工 KP，审计见测试与脚本）
AI generated: 17（TXT 12 + PDF 5）
```

## 43.11 Evidence Grounding

```text
grounded: 17/17（knowledge_trace 逐条验证 evidence + materials 非空）
```

## 43.12 Review

```text
auto accepted: 15（TXT 12 + PDF 3）
review: 2（PDF：考试日期/最低分，行政信息等人裁决）
conflict: 0（本次材料无事实冲突；冲突路径由单测 + Fake 链路覆盖）
```

## 43.13 Regression（完整输出）

```text
pytest -m "not integration"（全量，含新增）：
  5293 passed, 8 skipped, 48 deselected（16.5 min）
  + tests/test_ai_auto_pipeline.py：26 passed
  + tests/test_ai_understanding.py：67 passed, 1 deselected
  + tests/test_live_ai_smoke.py：SKIP（无凭证）/ 2 passed（有凭证）
python -m compileall -q src tests：EXIT 0
node scripts/ui_render_check.js：OK (219 checks)
node scripts/ui_audit.js：OK (396 checks)
Browser E2E：Windows 无浏览器环境，沿用 Node + DOM harness（上）+ 真实服务
  HTTP E2E（upload -> process -> auto AI -> KP -> trace -> review，见
  TestHttpAutoPipeline，PASS）
```

新增测试无删除/skip/xfail/弱化（§41）；13 条 pre-existing 红色是 TASK-76 已修，本次零新增失败。

## Definition of Done（§44，逐项）

- [x] `process_material` 自动触发 AI
- [x] AI disabled 时旧 pipeline 正常（作业形状一致）
- [x] AI failure 不损坏 Evidence（SUCCEEDED + evidence 保留 + 可重试）
- [x] Retry 正常（`retry_ai_analysis` = `POST .../ai-analyze`，幂等，chunk 缓存只补失败部分）
- [x] AI idempotency 正常（`ai-<sha16>` + `aikp-*`，三连击零重复）
- [x] Summary 自动生成（材料页直接显示）
- [x] KnowledgePoint 自动生成（17 个，手工 0）
- [x] 全部 Evidence-grounded（17/17）
- [x] Dedup 正常（同句跨材料 attach，无 Derivative×2）
- [x] Conflict 进入 Review（规则单测 + 管线集成；本次 live 零冲突是材料性质）
- [x] Low confidence 进入 Review（0.70–0.90 → pending + confirm/reject/keep）
- [x] Course isolation（`aikp-*` 课程命名空间，跨课零交集）
- [x] Student state 不变（快照前后完全一致）
- [x] API Key 安全（repr/describe/异常/job 四处断言无 key）
- [x] PDF 自动分析（live PASS）
- [x] Image pipeline 自动分析（Fake 自动 PASS，Vision 可选）
- [x] Audio pipeline 自动分析（Fake 自动 PASS，timestamp 随转写证据）
- [x] Real API smoke test（PASS，见 43.8）
- [x] Real UAB material test（PASS，见 43.9–43.12）
- [x] 用户无需手动创建 KnowledgePoint（0）
- [x] UI 显示处理进度（stages 清单 ✓/○）
- [x] UI 显示 Summary（topics/定义/公式/例题/前置/难点）
- [x] UI 显示自动 KnowledgePoints（auto/review/conflict 三桶）
- [x] Review Center 可处理结果（confirm/reject/keep，不断言自动解决）
- [x] Full regression 通过（见 43.13）

## 最终验收（§45）

```text
真实 API（agnes-3.0-flash）+ 真实 UAB 学习材料（讲义 + 真实 PDF）
+ 真实 Classroom Assistant：
Upload -> Automatic Processing -> Automatic AI Analysis
-> Automatic Summary -> Automatic KnowledgePoint (17)
-> Evidence Trace (17/17) -> Review（2 pending）
Manual KnowledgePoint Creation = 0
```

**TASK-77 COMPLETE。**

## 附录 A（2026-09-21 晚）：网关 json_object 犯病后的兼容修复

Live 验收通过约 1 小时后，同一网关对 `response_format: json_object`
开始 HTTP 200 包错（`{"type":"error","error":{"message":"An internal
error occurred when running the model."}}`），导致 22/22 chunk 失败；
而纯文本模式始终正常（对照实测）。与 key 无关（旧 key 仍 200，模型仍在
`/models` 列表），系 hub 侧波动/退化。

修复（标准库 only，零 migration，+4 条测试，均绿）：

- `generate_structured` 识别 200 包错并报出内部原因，不再沉没为一句
  含糊的"N of N chunks failed"；
- 新增 `CLASSROOM_AI_JSON_MODE`（默认 true；false 时不发
  `response_format`，纯文本 + 严格 schema 校验——`schemas.py` 本就容忍
  围栏）；`bootstrap` 透传；非法值直接 `ConfigurationError`；
- 用户侧 `scripts/ai-env.bat` 置 `false` 后重启即恢复（纯文本模式已用
  真实网关实证：合法 JSON + 2 个真实候选，schema 一次通过）。

## 附录 B（2026-09-21 晚）：提示词 v2（浓缩改写）+ 可选 vision

用户实测指出两点（均为合理产品纠偏）：① 课堂材料无隐私顾虑，
vision 可用；② KP 描述与证据原文一字不差，要的是考前能背的精简总结。

- 病因：v1 提示词只要求"抽取"，没有任何浓缩改写要求，模型自然复述原文。
- 修复：新增 `_DISTILL_RULES`（标题仅概念名；描述 1–3 句浓缩改写，
  禁止复述原句；压扁的表格必须解读成明确事实；行政噪音最多 low；
  原文只许进 `original_terms`/`examples`），chunk/merge/image/audio
  四个提示词全接入，版本 bump 到 `*-v2`（identity 随之更新；同标题 KP
  按 `aikp-*` ID 幂等覆盖，重试即自愈，改名残留的旧条在 Review 里拒绝）。
- Vision（默认仍关闭）：`CLASSROOM_AI_ALLOW_IMAGE_BYTES=true` 时图片材料
  OCR + 原图（`image_url` data URL，上限 8MB）一起发 vision 模型；
  三重门（图片材料 + capability + 文件可读），失败记 chunk 失败不静默降级；
  `bootstrap` 透传；`FakeAIProvider` 保持纯 OCR（回归形状不变）。
- Live 实证（agnes-3.0-flash + 真实黑板照片 `tema1-pissarra.png`）：
  模型读的是图（算法定义/O(n)复杂度/Dijkstra步骤/二分查找前提），
  4 个候选描述全部是浓缩改写句，schema 一次通过。
- 测试：+9 条（提示词 v2 token/版本、vision 载荷形状/超限/未知格式/
  文本材料不发字节/service 只给图片发字节/失败不静默/开关解析）。

## 附录 C（2026-09-21 晚）：常驻任务坞（task dock）

用户在等 AI 分析时要切到别的模块：`index.html` 新增 `#view` 之外的
`#task-dock`（左下角，`route()` 碰不到）；`app.js` 新增
`startTask/finishTask/renderTaskDock`（最多保留 5 条）；
处理/AI 分析/整节处理/上传四个长动作全接线（完成/失败必结算，
切页后用新鲜面板查找 + `onTaskLane()` 守卫，绝不写过期节点、不把人拽回）。
`tests/test_task_dock.py` 6 条静态契约；i18n `task.running/done/failed`
三表齐备；既有 UI 门禁（138 条 + render 219 + audit 396）全绿。

## 附：修掉的 1 个真实缺陷 + 2 处测试校准（非弱化）

1. **AI KP 重启丢失**（真缺陷，§15）：`_ai_reports` 内存 + KP 只进 org，
   `_flush_knowledge` 只存 `processing.structure` → 重启后 AI KP 全丢。
   修：`AIAnalysisService._mirror_to_structure` 幂等镜像（无 migration），
   重启测试 `6 == 6` 通过。失败在前、修复在后，全程有测试为证。
2. `test_auto_skipped_when_ingestion_failed`：初版用不存在文件构造 FAILED，
   但 `_failed_record` 无 `material_id`（注册表外记录，不可处理）——改用
   monkeypatch 摄取抛错，断言的是同一条"摄取失败→AI 跳过"语义。
3. `test_duplicate...`：初版把确定性 `kp-*` 也计入同标题比较；AI 去重域是
   `aikp-*` 命名空间，改為只断言该域（恰好证明双链路不互踩）。
