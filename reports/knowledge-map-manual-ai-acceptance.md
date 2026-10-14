# 知识地图修复 + 手动 AI 接入 —— 最终验收报告

日期：2026-10-13 · 仓库：`D:\Project\Clases` · 分支：`main`（未提交，工作树改动 = 本次交付）

本报告只写**真实执行过的命令与实测数字**。凡未验证的，一律标「未验证」并列入剩余问题，
不写成完成。阶段 0/1 的分析与实施细节见
[`knowledge-map-manual-ai-analysis.md`](knowledge-map-manual-ai-analysis.md)。

---

## 0. 交付物清单与状态

| # | 交付物 | 状态 | 证据 |
|---|--------|------|------|
| 1 | 正式分析报告 | 完成 | `reports/knowledge-map-manual-ai-analysis.md`（368 行，阶段 0/1） |
| 2 | 知识地图修复代码 | 完成 | 见 §3 文件清单 |
| 3 | 数据迁移/兼容 | **无需迁移** | 见 §6（零 migration 的口径与理由） |
| 4 | 手动 AI 导出模块 | 完成 | `src/application/ai/manual.py`（新建）+ API + 前端 |
| 5 | 手动 AI 导入与回放 | 完成 | `ReplayAIProvider` + `import_manual_ai_responses` |
| 6 | 前端交互 + i18n | 完成 | `views/materials.js` + `app.js` + `i18n.js`（manual.* 共 45 键 × zh/es/ca，三表键集完全一致） |
| 7 | 自动化测试 | 完成 | `tests/test_manual_ai_bundle.py`（44 条） |
| 8 | 协议与使用文档 | 完成 | 包内 `instructions_markdown` + 本报告 §5 |
| 9 | 最终验收报告 | 本文 | — |

---

## 1. 阶段完成状态

| 阶段 | 状态 | 说明 |
|------|------|------|
| 阶段 0 基线调查 + 分析报告 | 完成 | 阶段 1 前完成，结论均带 `文件:行` 证据 |
| 阶段 1 知识地图结构正确性 | 完成 | 见分析报告 §8；本报告 §2 复核 |
| 阶段 2 手动 AI 导出 | 完成 | 零出站、零写入，有实测（§4） |
| 阶段 3 手动 AI 导入与回放 | 完成 | 逐请求状态、部分失败可重试、幂等（§4） |
| 阶段 4 端到端验收 | 完成 | **53/53 通过**（`temp/manual-ai-e2e-results.txt`） |

---

## 2. 阶段 1 复核（知识地图）

以下为**本次重新实测**，不是沿用阶段 1 的旧数字。

### 2.1 精简 payload（真实数据，实测）

| 课程 | 知识点 | 地图整包 | 完整 KP 列表整包 | KP 部分精简 |
|------|--------|----------|------------------|-------------|
| `course-32dde014868219be`（Gestió de Projectes） | 61 | 53,523 B | 59,349 B | 29,686 B → 61,230 B，**省 51.5%** |
| `course-bd4f98777c97a0d2`（Bases per a la Geoinformació） | 380 | 295,151 B | 411,515 B | 149,650 B → 431,497 B，**省 65.3%** |

- 地图点字段恰为 7 个：`evidence_refs, generation_mode, knowledge_id, original_terms,
  review_status, title, validation_status`；完整 DTO 是 15 个（多出 `content` /
  `confidence` / `metadata` 等）。**`content`（正文）不再出现在地图响应里。**
- 端点耗时（3 次取中位，含服务端构图）：地图 **32.9 ms / 68.9 ms**，裸列表
  **15.6 ms / 31.8 ms**。地图端点**更慢**——它多做构图（术语归一化 + 边匹配）。
  优化目标是**传输量**，不是端点耗时；不宣称"地图更快"。

### 2.2 绘制与布局（真实浏览器，无头 Edge 同源复现）

以真实服务（`127.0.0.1:8791`，真实 `classroom-data`）加载 `#/courses/<id>/knowledge-map`：

| 课程 | 节点 / 边 | 单帧绘制 | 布局指纹 |
|------|-----------|----------|----------|
| 61 点课程 | 65 / 23 | **2.09 ms**（20 帧均值） | `65:23:2:f5bb8bc2` |
| 380 点课程 | 391 / 255 | **0.79 ms**（20 帧均值） | `391:255:3:e78b0ebd` |

布局缓存失效判据（真实页面上直接验证）：

- 只改**一条边的 target**（节点数/边数不变）→ 指纹变化 ✅
- 只改**一个节点的 material_id**（数量不变）→ 指纹变化 ✅
- 同一份数据重复读 → 指纹稳定（缓存命中）✅

### 2.3 主题适配（决定性验证）

- 深色：`--graph-bg: #101219` → 画布像素 `[16,18,25]`。
- 浅色：`--graph-bg: #f7f9fc` → 画布像素 `[247,249,252]`（`matchMedia` change 监听触发重绘）。
- **决定性**：临时把 `--graph-bg` 改成 `#ff0000` 并重画 → 像素变 `[255,0,0]`；
  撤销后恢复 `[247,249,252]`。→ 画布**确实在读 CSS token**，不是写死的调色板。

### 2.4 可访问性（真实页面）

- canvas：`role="img"`、`tabindex="0"`，`aria-label` 为实测的
  「图中 61 个知识点、0 条已登记关系、14 条推断关联、4 个未归类概念。用方向键在节点间移动，回车查看详情。」
- 键盘：`Tab` 聚焦画布 → `ArrowRight` 后，`aria-live` 详情卡更新为
  「Gobernanza y comunicación / 1. Group Work.pdf / 关联数: 0 / 打开知识点详情」。
- 工具条 8 个按钮，可切换项带 `aria-pressed`；列表视图作为完整替代入口保留。

---

## 3. 实际修改的文件

**新增（未跟踪）**

| 文件 | 作用 |
|------|------|
| `src/application/ai/manual.py` | 手动 AI 协议：bundle / 校验 / `ReplayAIProvider` |
| `tests/test_manual_ai_bundle.py` | 44 条测试（协议 / 校验 / 回放 / 端到端） |
| `reports/knowledge-map-manual-ai-analysis.md` | 阶段 0/1 分析报告 |
| `scripts/bench_knowledge_graph.js` | 前端绘制/布局基准（可复跑） |
| `temp/accept_manual_ai_e2e.py` | 端到端验收 harness（53 条断言） |
| `temp/manual-ai-e2e-results.txt` | 上一次运行的完整输出 |

**修改**（`git diff --stat` 当前实测：**23 个文件，+2399 / −152**；其中第 23 个是
`docs/status.md` 的变更记录条目。**代码/测试/脚本改动面 = 22 个文件，+2333 / −152**）

```
 scripts/ui_audit.js                  | 145 +   （610 → 627 检查）
 src/api/endpoints.py                 |  46 +   （导出/导入两条路由）
 src/application/ai/config.py         |  25 +   （manual 模式）
 src/application/ai/pipeline.py       | 125 +-  （提示词分派 + request_key + 容错）
 src/application/ai/prompts.py        |  38 +   （build_chunk_prompt 单一分派）
 src/application/ai/provider.py       |  18 +-  （request_key 可选参数）
 src/application/ai/schemas.py        |   6 +
 src/application/ai/service.py        |   9 +   （tolerate_chunk_failures）
 src/application/ai/validators.py     |  28 +-
 src/application/bootstrap.py         |  19 +-  （CLASSROOM_AI_MODE=manual）
 src/application/knowledge_service.py |  41 +   （关系/主题/成员的读投影）
 src/application/workspace.py         | 373 +-  （地图投影 + 手动 AI 编排）
 src/knowledge_graph.py               | 197 +-  （规范化 + 诊断 + 有向边）
 src/web/app.js                       |   4 +   （三条 action 分派）
 src/web/i18n.js                      | 176 +-  （manual.* 三语）
 src/web/styles.css                   | 107 +-  （--graph-* 双主题 token）
 src/web/views/knowledge-graph.js     | 510 +-
 src/web/views/knowledge.js           |  23 +
 src/web/views/materials.js           | 204 +   （手动 AI 面板）
 tests/test_ai_understanding.py       |  46 +   （阶段 1 新增判据）
 tests/test_knowledge_graph.py        | 208 +
 tests/test_knowledge_map.py          | 137 +
```

---

## 4. 端到端验收（真实 HTTP，53/53）

命令：`PYTHONIOENCODING=utf-8 python temp/accept_manual_ai_e2e.py`
完整输出：`temp/manual-ai-e2e-results.txt`（退出码 0）。

harness 用 `create_server(ws, port=0)` 起**真实 HTTP 服务**，`socket.socket.connect`
被替换为「只放行 loopback」——任何真实出站会当场抛错（不是"看起来没发请求"）。

### A. 手动模式链路

- `health.ai_mode == "manual"`、`ai_enabled == True`。
- `process_material` 返回的 `job.ai.status == "skipped"`，`reason` 明写
  「AI mode is 'manual': no request was sent; use the manual export/import panel」。
  → **manual 模式不会自动出站**。

### B. 导出（只读）

- 200；`bundle_id` 形如 `mab-<sha16>`；`protocol_version` 如实；4 条请求、`request_id` 唯一；
  每条都带完整 `prompt`；`instructions_markdown` 随包返回；
  **包内不含 `classroom-data`（不泄露本机路径）**；导出后知识点集合**逐一不变**。

### C. 導入 + 入库 + 溯源

- 4/4 `applied`、0 失败、`provider == "user-pasted"`、`mode == "manual"`、≥1 条自动确认。
- `GET /api/knowledge` 出现 4 条导入知识点（`title` 前缀 `Gradiente`），带 `evidence_refs`。
- `GET /api/knowledge/<id>/trace`：`complete == True`，证据 → 材料链表恰为
  `{mat-3244…}`（**不可能跨材料归属**）。
- `GET /api/courses/<id>/knowledge-map`：地图点字段 = 7 个精简字段（无正文），
  4 个导入点全部出现在图谱节点（8 个节点）。

### E. 错误场景（逐条实测）

| 场景 | 实测结果 |
|------|----------|
| 错误 `bundle_id` | 400 `INVALID_INPUT` |
| 不支持的协议版本 `manual-ai/v99` | 400 |
| 未知 `request_id` | 逐条状态 `unknown_request_ids`，0 入库（**不是**整包 400） |
| 重复 `request_id` | `duplicate_request_ids` 列出，只认第一条 |
| 非法 JSON `{not json` | `malformed_json` |
| JSON 数组（非对象） | `malformed_json`（schema 拒绝） |
| 对象但无可用字段 | `empty`（0 候选），**零入库** |
| 超长输入（30 万字符） | `too_large` |
| 照抄原文 | 该请求 `grounding_failed`，被拒内容**未入库** |
| 两 chunk 响应互换 | 只在它自己的 request 上 `applied`，被错位的记为 `missing` |
| 缺一条响应 | `requests_failed == 1`，`retryable_request_ids == [缺的那条]` |
| 补上后重复提交 | 0 失败，知识点标题 **9 个 / 9 唯一**（不产生重复） |

**一个如实记录的语义细节**：schema 层只把「不是 JSON 对象」判为硬错误；
一个字段全不认识的对象会走到 `empty`（0 候选）而不是 `schema_error`。
两条路径的**共同结果都是零入库**，所以验收目的达成；区别只在逐请求状态的文案。

### F. 报告来源标识

`report["source"]`：`mode == "manual"`、`provider == "user-pasted"`、
`protocol_version == "manual-ai/v1"`、`bundle_id` 非空。
→ 事后看一份报告就能分辨「粘贴回来的」与「真实 API 调用」。

---

## 5. 导出的协议与外部模型用法

导出包（JSON）关键字段：`protocol_version` / `bundle_id` / `prompt_version` /
`course_id` / `material{material_id,filename,content_hash,material_hash,kind,language}` /
`requests[]（request_id, stage, chunk_id, chunk_index, page, section, char_start,
char_end, prompt, response_format, response_schema, evidence_scope, prompt_version）` /
`chunking{chunk_count,material_chunk_count,truncated,request_limit}` /
`limits{max_response_chars:20000,max_requests:200}` / `notes[]`。

`request_id = req-<sha16(bundle|stage|prompt_version|chunk_id)>`、
`bundle_id = mab-<sha16(protocol|prompt_version|material_id|material_hash|chunk_ids)>`
—— 全部内容寻址，**无时间戳、无随机数**，所以同一材料 + 同一配置 ⇒ 同一包，
重复提交天然幂等。

导入外壳：

```json
{
  "protocol_version": "manual-ai/v1",
  "bundle_id": "mab-…",
  "model_name": "（可选，仅记录，不作为已验证身份）",
  "responses": [
    {"request_id": "req-…", "content": "{…模型对该请求返回的 JSON…}"}
  ]
}
```

安全要点：导入时 `bundle_id` 与每个 `request_id` **按当前材料重算**并与包里比对，
包里的值不是判据；模型自报的 `evidence_refs` 会被重写为本请求的 chunk；
`content`、`model_name`、`evidence_refs` 一律按不可信输入处理。

---

## 6. 数据迁移与兼容性

- **零 DB migration**：未新增表/列。手动导入复用既有 KP 表与 Evidence 存储，
  报告沿用既有落盘路径（`materials/ai-reports/<课>/<材料>.json`）。
- 无破坏性迁移 ⇒ 无需回滚脚本；旧知识点、旧报告、旧 AI 模式（`real`/`fake`/`disabled`）
  行为未改（§8 相关测试全绿）。
- **一处接口扩展需要说明（潜在破坏性，已缓解）**：
  `AIProvider.generate_structured` 新增可选关键字 `request_key`。仓库内 3 个**旧签名**
  测试替身（`test_ai_understanding.py:142`、`test_ai_auto_pipeline.py:129`、
  `test_material_delete_analyze.py:77/89`）以及任何用户自带 provider 会因此
  `TypeError`。缓解方式：管线侧 `_provider_accepts_request_key()` 按**类**缓存地
  检查签名，仅在 provider 接受（含 `**kwargs`）时才传 `request_key`。
  代价：旧 provider 拿不到「回放定位」能力（手动导入会如实报 `missing`），
  但材料分析链不会崩。**这是刻意的取舍，不是静默降级**——状态如实可见。

---

## 7. 未通过的测试与基线失败（逐条解释，与本次改动分离）

以下 3 项**在改动前的 `HEAD` 上同样失败**。验证方式：`git archive HEAD` 导出到
`/tmp/headcheck`（纯 tracked 文件，不含本次工作树改动）后重跑。

| 失败项 | HEAD 复现 | 根因 | 与本次改动的关系 |
|--------|-----------|------|------------------|
| `scripts/ui_render_check.js` **312/314** | 是（同样 2 条） | harness 期望 `#/students` **重定向到「我的课程」**，而 `route()` 里从来没有这条重定向（落到「未找到页面」）。该判据由 `1cf14b1` 引入 | 无关。本次只给 `app.js` 加了 3 行 action 分派，**未触碰路由**（`git diff` 可验）；HEAD 的 `ui_audit.js` 610、工作树 627（+17 为本轮新增检查） |
| `tests/test_exercise_ui.py::TestUiRenderExecution::test_ui_render_harness_passes` | 是 | 它断言上面的 harness 退出码为 0 | 同上，非本次回归 |
| `tests/test_production_gate.py::TestBuildHygiene::test_no_stray_python_file_outside_src_and_tests` | 是 | 仓库根有一个**已提交**的散落脚本 `fix_ex.py`（`1cf14b1` 引入，mtime `2026-10-13 05:12`，早于本轮）。未删除：不是本轮产物 | 无关（未触碰） |
| `tests/test_student_ui.py::TestTheLiveSessionPickerEndToEnd::test_the_live_script_passes` | 是（同样 ENOENT） | 脚本要 spawn `D:\Project\Clases\Python\pythoncore-3.14-64\python.exe`，本机未安装该可携带解释器（`.gitignore` 内的 `/Python/`） | 环境缺失，非本次回归 |

**未做**：为了让这几项变绿而修改无关代码或删断言。理由见任务纪律 §2.1/§5。

---

## 8. 测试命令与真实结果

| 命令 | 结果 |
|------|------|
| `pytest tests/test_manual_ai_bundle.py -m "not integration"` | **44 passed** |
| `pytest tests/test_manual_ai_bundle.py tests/test_ai_understanding.py tests/test_ai_auto_pipeline.py tests/test_ai_knowledge_score.py tests/test_ai_derived_reports.py tests/test_knowledge_map.py tests/test_knowledge_graph.py tests/test_material_delete_analyze.py tests/test_kp_translation.py -m "not integration"` | **292 passed**, 1 deselected（13.97s） |
| `pytest tests/test_application_knowledge.py tests/test_bootstrap.py tests/test_knowledge_graph.py tests/test_hardening_traceability.py tests/test_api_server.py -m "not integration"` | **262 passed, 2 skipped**（59.66s） |
| `pytest tests/test_exercise_ui.py tests/test_web_ui.py tests/test_web_ui_invariants.py tests/test_web_source_manifest.py tests/test_learning_view.py -m "not integration"` | **329 passed**, 1 failed（`test_ui_render_harness_passes`，见 §7 基线）、1 warning |
| `pytest tests/test_student_ui.py tests/test_knowledge_density.py tests/test_production_gate.py -m "not integration"` | **133 passed**, 2 failed（均见 §7 基线） |
| `node scripts/ui_audit.js` | **627 checks OK**（HEAD 610） |
| `node scripts/ui_render_check.js` | **312/314**（2 条基线失败，与 HEAD 逐字相同） |
| `pytest tests/test_exercise_ui.py::TestI18nTables` | **12 passed** |
| `python temp/accept_manual_ai_e2e.py` | **53/53 OK**，退出码 0 |

**未执行**：全量 `pytest -m "not integration"`。理由：本机单次全量历史耗时 ~20 分钟，
超出本轮工具时限；改为按**受影响面**分组覆盖（上表 6 组，合计 1060 条）。这是覆盖
取舍，**不声称全量通过**。

---

## 9. 性能数据（测量条件）

所有数字均在**本机**（Windows / Python 3.14 / Node v24.18.0 / 无头 Edge）实测，
数据来自真实 `classroom-data/database/classroom.sqlite`（5 门课、467 个知识点、
126 条证据）。样本：61 点课程与 380 点课程各 1 门；耗时取 3 次中位数（端点）
或 20 次均值（绘制）。**不是**合成数据。

- 传输：KP 部分精简 **51.5% / 65.3%**。
- 绘制：380 点 / 255 边 **0.79 ms/帧**；61 点 / 23 边 **2.09 ms/帧**。
- 布局指纹：节点数/边数不变时，改一个边端点或一个节点属性都会换指纹。

---

## 10. 剩余问题与风险（明确区分「已登记关系 / 文本提示关系 / 未匹配关系」）

### 10.1 真实数据里没有已登记的知识关系（最重要的一条）

实测 `GET /api/health` 的业务对象计数：`knowledge_points: 467`、
**`knowledge_relations: 0`、`topics: 0`、`knowledge_memberships: 0`、`relationships: 0`**。

知识地图里因此**一条有向边都没有**。两门课程的真实投影：

| 课程 | 已登记关系 | 文本提示边（`content_hint`） | 概念边（`concept`） | 未匹配术语 | 未归类概念 |
|------|-----------|------------------------------|---------------------|------------|------------|
| 61 点课程 | **0** | 14 | 9 | 40 | 4 |
| 380 点课程 | **0** | 226 | 29 | 59 | 11 |

- 全部 255 / 23 条边 `directed: false`、`direction: null`、`origin ∈ {content_hint, concept}`。
- `graph.stats.registered_relation_count == 0`、`content_hint_edge_count`、`concept_edge_count`、
  `dropped_relation_count`、`relation_instance_count`、`isolated_knowledge_point_count` 均如实分开报。
- canvas 的 `aria-label` 也如实说「**0 条已登记关系**、14 条推断关联」。

**结论（不得含糊）**：前端的**有向边渲染路径已实现并被测试覆盖**（`origin="registered"`
或 `directed=true` 时画箭头），但在**真实数据上无法演示任何真实有向关系**，因为
权威关系存储是空的。本次交付的是「**当关系存在时能正确、有向、可溯源地画出来**」，
**不是**「已经建立真实的知识关系体系」。把 `content_hint` 边当成真实关系展示会
违反证据优先原则——它们当前标注为推断、无向、虚线语义。

### 10.2 未做的功能

- **关系写入链路（结构化 `knowledge_relations` 的生产）**：本次只做了**读投影**与
  诊断；没有新增写入器，也没有把正文 `关联:` 行提升为已登记关系。理由：任务要求
  「不得将正则提取的结果直接全部认定为可信关系」，且需要先定义唯一性约束与
  迁移方案——属于独立设计。
- **主题/层级**：`topics` / `knowledge_memberships` 同样只有读投影，真实为空。
  未伪造主题、未自动造边（任务明确禁止）。
- **真实 OCR / ASR 引擎验收**：本机 OCR 回落 Mock（`runtime_note` 可见），未做。
- **浏览器 E2E 常态化**：本轮为一次性人工/无头验证，未纳入 CI。
- **端到端验收里的二次重启持久化**：harness 使用内存态工作区（`persistence=None`），
  未验证「重启后手动导入的报告仍在盘上」；该路径由既有
  `tests/test_ai_auto_pipeline.py` / `test_kp_translation.py` 的持久化用例覆盖，
  但**手动导入 + 重启**的组合未单独实测。
- **`unresolved_terms` 的前端交互**：已渲染（真实 40/59 条 + 计数），未做点击定位。

### 10.3 已知风险

- `ReplayAIProvider.model` 固定空串（用户自报的模型名不进处理身份，保证幂等）；
  代价是**同一份响应换个模型名仍是同一身份**——这是刻意设计，报告里用
  `source.model_reported` 单独记录。
- 导入包上限 `MAX_RESPONSE_CHARS=20000` / `MAX_REQUESTS=200` / 整包 8 MiB。
  超限是**逐条** `too_large`（保留其余成功），不是整包拒绝。
- 若材料在导出后被重新处理，`bundle_id` 重算不匹配 → 400（实测 E 场景 + 单测 44 条内覆盖）。

---

## 11. 停止条件核对

- ✅ 核心测试通过（受影响面 1060 条，除 §7 的 4 项基线失败与 1 条基线 harness 失败）。
- ✅ 基线失败得到解释（HEAD 复现，逐条给根因与「与本次无关」的证据）。
- ✅ 端到端链路有可复现证据（`temp/accept_manual_ai_e2e.py`，53/53，含零出站强制）。
- ✅ 报告准确反映剩余问题（§10 明确写出「真实关系为 0，不得据此宣称已建立关系体系」）。
- ⛔ 未遇到破坏性迁移 / 证据归属不确定 / 核心架构与预期不符 → 无需暂停。
  唯一「核心数据模型与预期不一致」之处（真实库里关系/主题全空）已按 §10.1 如实报告，
  并按任务要求**不伪造**。

---

## 12. 前端交互的真实验证（真实浏览器 + 真实数据）

服务：`python -m src.application.cli --port 8791`（真实 `classroom-data`，5 门课 / 467 个知识点 /
126 条证据）。浏览器：本线程 Preview（Chromium）。

### 12.1 材料页手动 AI 面板（`#/materials`）

- 每个材料行都渲染出「手动 AI」按钮：3 个材料 → 3 个按钮，`data-course` / `data-material` 正确。
- 点击后（材料 `mat-15a8ab35ec46952a4b54a572`，22 个 chunk）面板实测：
  - 标题「手动 AI 分析」，meta `mab-c8292290705a4261 · manual-ai/v1`；
  - 提示「导出不调用任何 AI 服务、不写入知识点或证据；导入会走与真实调用同一条校验与入库流程。」；
  - 任务包 `<textarea readonly>` 内容可 `JSON.parse`：`protocol_version=manual-ai/v1`、
    22 条 `requests`、每条含 `request_id/chunk_id/prompt/response_schema/evidence_scope`、
    并随包返回 `instructions_markdown`；
  - 粘贴区 + 两个按钮 `manual-ai-copy` / `manual-ai-import` 就位。

### 12.2 导入错误路径（真实点击，写入零副作用）

- 粘贴 `{"bundle_id":"mab-deadbeefdeadbeef", ...}` 后点击「导入响应」：
  - 网络实测 `POST /api/materials/…/manual-ai/import?course_id=… → 400`；
  - 界面实测 toast：`导入失败 [INVALID_INPUT] this response belongs to a different bundle;
    recompute it by exporting again`；
  - **数据库未变**：该课程仍 380 个知识点，无新增；`responses` 为空时也不渲染逐请求表（正确）。
- 成功率路径的**端到端写入**在 `temp/accept_manual_ai_e2e.py` 中对**临时工作区**完成
  （53/53），不在真实库上做写操作。

### 12.3 逐请求状态表（渲染层，真实 DOM）

用一段合成的导入结果驱动同一个渲染函数（**只渲染，不发请求、不写库**）实测：

- 表 caption「逐请求状态」，4 行 4 列（请求 / 状态 / 说明 / 候选数）；
- 状态 pill 本地化为中文：`已入库` / `无知识点` / `证据校验未通过` / `缺少响应`；
- 来源行如实写「来源：用户粘贴（不是真实 API 调用）　user-pasted · modelo-web · mab-…」。

### 12.4 环境观察（与本轮改动无关，如实记录）

- 真实环境下 `GET /api/health` 返回 **HTTP 503**、`status: "degraded"`——
  原因是本机 OCR 回落 Mock（`runtime_note` 明示）与 `llm_mode: mock`。
  这导致页面周期性的 health 轮询在控制台留下 503 记录；**不是**本轮引入。
- 本轮**没有**在真实库上执行任何写入（导入/处理/翻译/删除）。
