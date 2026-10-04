# TASK-79 · 知识点级中文解释（按需翻译 + 术语表）

> 状态：**COMPLETE**（2026-10-02）
> 前置：TASK-76（AI 语义理解）/ TASK-77（自动 AI 材料处理）
> 续作：自动翻译 stage、`metadata.kp_zh` 落库（见 §7 预留项）

---

## 1. 出发点：中文只存在于材料级

动手之前的实测结论（不是推测，是逐层核对的结果）：

| 层 | 中文？ | 证据 |
|---|---|---|
| 材料级 | ✅ 有 | `prompts.py` 的 `build_summary_zh_prompt` / `build_glossary_prompt` → 报告层 `summary_zh` / `glossary` → `views/materials.js` 展示 |
| 知识点级 | ❌ 无 | `KnowledgePoint`（`src/models.py`）**没有任何 zh 字段**；`views/knowledge.js` 只有 `originalBlock(kp.content)` + Task 29 的解释面板 |
| 卡片级 | ❌ 无 | `Flashcard`（`src/models.py`）同样没有 zh 字段；`#/flashcards` 只有西语原文直渲 |

而且 Task 29 的 `src/application/learning_view.py` 里写死了这条边界：证据语言
是 `es`/`ca`，请求 `zh` 直接返回 `language_not_available`。这不是遗漏，是当时的
**显式设计** —— 系统不会自动把任何东西翻成中文。

于是用户在 `#/flashcards` 上看到的是 `front=Gobernanza y comunicación`、
`back=一整段西语`，**零中文，也无处可去**。

## 2. 目标与非目标

用户已确认的三条：

1. **每个知识点可点进详情学中文**：整句中文解释 + 术语表 `term（原文）→ zh`；
   证据原文逐字保留、不翻译。
2. **先按需翻，后自动翻**：本轮只做按需（点按钮调 LLM，可缓存）。
3. **记忆卡片不删，降级为背题入口**：标题可点进详情，卡片只做 FSRS 打分。

非目标（明确不做）：

- 不删 `m004_flashcards` 表；
- 不动 `src/scheduling/fsrs.py`；
- 不翻译 / 改写证据；
- 不做全文段落对照（那等于把 Evidence 复制一份中文）。

## 3. 落点

```
知识点 (title + content + original_terms + evidence_refs)
  → POST /api/knowledge/{id}/translate {target_lang: zh}      ← 唯一的 LLM 入口
  → OpenAICompatibleAIProvider.generate_structured + build_kp_zh_prompt
  → parse_kp_zh_response (形状) → ground_kp_zh (语义/逐字校验)
  → 缓存 classroom-data/materials/ai-reports/<course>/kp-translations/<kp>.json
  → 前端详情页与卡片页共用同一套渲染器

GET /api/knowledge/{id}/glossary   ← 零成本：按 kp_id / 证据过滤已有报告的术语表
GET /api/knowledge/{id}/translate  ← 只读缓存，永不现场补算
```

## 4. 任务分解与处置

| # | 任务 | 落点 |
|---|---|---|
| T1 | `build_kp_zh_prompt` + `KP_ZH_PROMPT_VERSION` + `KP_ZH_MAX_INPUT_CHARS=2000` | `src/application/ai/prompts.py` |
| T2 | `KpZhResult` / `parse_kp_zh_response` / `ground_kp_zh` / `kp_translation_id` | `schemas.py`、`validators.py` |
| T3 | `AIUnderstandingPipeline.build_kp_zh` + FakeAIProvider 夹具分支 | `pipeline.py`、`provider.py` |
| T4 | `Workspace.translate_kp` / `kp_translation` / `kp_glossary` + 缓存读写 | `src/application/workspace.py` |
| T5 | 三个端点（POST 翻译 / GET 缓存 / GET 术语表） | `src/api/endpoints.py` |
| T6 | 渲染器从 `views/materials.js` 提到 `app.js`（`renderGlossaryCard` / `renderTranslationZhCard` / `renderGlossaryTable`） | `src/web/app.js` |
| T7 | 详情页 `<div id="kp-zh-panel">` + `loadKpZh` / `actionTranslateKp` | `src/web/views/knowledge.js` |
| T8 | 卡片标题改 `kpLink()` + `renderCardZh` 预览行 | `src/web/views/flashcards.js` |
| T9 | `kpZh.*` 9 个 key × 3 语 | `src/web/i18n.js` |
| T10 | `tests/test_kp_translation.py`（19 条） | `tests/` |
| T11 | `scripts/ui_audit.js`（530 → 554） | `scripts/` |

## 5. 关键设计决定

### 5.1 缓存身份：`kpzh-<sha16(kp + content_hash + prompt_version)>`

`content_hash` **只**覆盖真正进入 prompt 的字段（title / content /
original_terms / evidence_refs）。不含 `validation_status` / `review_status` ——
人工审核推进一次就让几百条中文解释全部失效重翻，是纯粹的浪费。

改知识点内容或换 prompt 版本 → 身份变 → 自动失效，不会拿旧解释冒充新的。

### 5.2 无证据 = 不翻

`build_kp_zh` 在**调模型之前**就检查 `evidence_refs`：为空直接返回
`status="skipped", reason="no_evidence"`，一次 LLM 都不发。`ground_kp_zh` 里
还有第二道同样的门 —— 中文必须锚在证据上，否则就是"没有出处的中文"。

### 5.3 术语必须逐字出现在知识点自身文本里

复用术语表那套大小写/重音不敏感的子串检查（`_glossary_term_is_present`），
作用域换成知识点自己的 title + content + original_terms。模型凭空造的术语被
放进 `terms_rejected` 诊断里，**不进** UI。

`lang` 只接受 `es` / `ca`；拿不准就是可见的 `[语言待确认]`，不猜。

### 5.4 `translation_zh` 故意**不**做相似度校验

术语卡要逐字命中，但中文解释不能 —— 一段正确的中文翻译和西语原文的相似度
必然低。要求它像原文，等于要求它别翻译。因此校验收窄到三条可检查的：
知识点非空、有证据、术语逐字命中。原文零改动由**"翻译根本不碰原文字段"**
保证（`translate_kp` 全程只读 KP）。

### 5.5 读接口绝不调 LLM

`GET .../translate` 只读落盘缓存，没有缓存就是 404。这是 §36.5/36.6
（禁止每次打开页面重新调用 LLM）的老规矩，`ai_summary` 早就这么做。

术语表更进一步：它读的是**材料报告里已经落盘**的 glossary 卡片，
所以零 LLM 成本，而且**不需要先翻译**。很多情况下它就是足够的那一层。

### 5.6 前端只保留一份渲染器

材料页与知识点详情页看到的是同一份中文，必须长得一样。所以三个渲染器
（`renderGlossaryTable` / `renderGlossaryCard` / `renderTranslationZhCard`）
搬进 `app.js`（它先于所有 `views/*.js` 加载），两页只传数据。
`ui_audit.js` 里有一条守卫钉住"只有一份"。

## 6. 守卫

### `tests/test_kp_translation.py`（19 条）

无证据不翻 / 幂等不重复调模型 / 缓存落盘（复现 TASK-77 的"只写内存"坑）/
缓存拒绝跨课程跨知识点 / 内容变化即失效 / 原文逐字未动 / 术语幻觉被拒收 /
未知语言标 `[语言待确认]` / 422 可重试 / 400 vs 422 的口径区分 /
结构守卫：翻译阶段**不得**混进 `analyze_material_with_ai`。

**变异测试**（证明断言真的会红）：

| 变异 | 失败条数 |
|---|---|
| M1 去掉缓存落盘（退回 TASK-77 的内存 bug） | 4 |
| M2 去掉术语逐字校验 | 1 |
| M3 把 provider 异常收窄回 `AIRequestError`（让 500 穿透） | 1 |

M3 是本轮改出来的**真实修复**：`build_summary_zh` / `build_glossary` 那种
best-effort 阶段可以吞掉异常，但这里失败必须暴露成 422。原来的窄 catch
会让一个真实 provider 的 socket / ssl 异常穿透成 HTTP 500。

### `scripts/ui_audit.js`（530 → 554）

- 详情页仍把证据渲染成 `originalBlock`（**证据原文零改动**）；
- `#kp-zh-panel` 挂载 + 渲染缓存解释 + 渲染术语 + 带免责声明；
- 卡片标题是 `<a href="#/courses/…/knowledge/…">`，且不再有纯文本 `<h2>`；
- 卡片仍渲染三个 FSRS 打分按钮（评分逻辑未动）；
- 一页卡片对每个知识点只发一次只读请求（两张卡共享一个 KP 不是 4 次）；
- `kpZh.*` 九个 key 三语齐备，es/ca 下不出现 CJK；
- 三个渲染器全仓各一份。

变异测试：标题退回纯文本 → 2 failed；删掉 `#kp-zh-panel` → 7 failed；
在 `materials.js` 里复制一份 `renderGlossaryCard` → 2 failed。

**夹具为什么是 ASCII**：审计第 1 组那条"es/ca 渲染结果里不得出现 CJK"的断言，
全部力量来自"夹具数据全是 ASCII"这一个前提。中文解释的夹具若填真中文，
断言会把**数据**误判成**界面漏译**。所以中文层用 ASCII 占位串，
"中显不显示"由上面那几条单独管。

### `scripts/ui_render_check.js`：314，不变。

## 7. 预留（本轮不做）

- **自动翻译 stage**：在 `service.py` 的 grounding 之后仿 `summary_zh` 加一段，
  让分析材料时顺带产出每条 KP 的中文。前提是先有成本上限策略
  （一学期几百个知识点 × 每次重新分析 = 几十次调用）。
- **`metadata.kp_zh` 落库**：把中文固化进 KP 行，好处是列表页也能显示；
  代价是 KP 表变宽、且要处理缓存失效与 schema 演进。
- **`PROMPT_VERSION` bump**：`kp-zh-v1` 只参与它自己的缓存 identity，
  没有改动材料级提取契约，因此**没有**动全局 `PROMPT_VERSION`
  （动了会让全部已落盘报告的 identity 失效）。

另立任务。

## 8. 风险

| 风险 | 处置 |
|---|---|
| LLM 编中文 / 编术语 | prompt 约束 + schema 严格形状 + 术语逐字校验 + 无证据不翻 + UI 固定免责声明 |
| 成本失控 | 按需（点按钮）+ 内容哈希缓存 + 2000 字截断；术语表先行且零成本 |
| 中文被误当权威 | 界面文案明确写"中文仅为解释层"；原文 `originalBlock` 一字未改；审计断言钉住 |
| TASK-77 旧坑（只写内存不落盘） | 落盘在事务之外且**不吞**失败的影响（它本来就不写数据库）；M1 变异钉住 |

## 9. 基线

```
tests/test_kp_translation.py     19 passed
scripts/ui_audit.js              UI audit OK (554 checks)   ← 530
scripts/ui_render_check.js       UI RENDER CHECK: OK (314 checks)
pytest -m "not integration"      5465 passed / 0 failed
```