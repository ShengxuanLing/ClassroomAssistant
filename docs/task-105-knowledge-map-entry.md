# 任务计划书：把顶栏「知识点」入口改为「知识地图」（TASK-105）

- 版本：v1（2026-10-12）
- 提出人：用户（凌总）
- 状态：**待实施**（未开工；实施前先与仓库里并行的 Codex 在制品对齐，见 §11）
- 规模：**S**（1 个前端文件为主 + 2 处文案 + 4 处守卫/审计同步）
- 风险：**低**（不改后端、不改数据、不改路由表、不改哈希）

> **一句话**：`#/knowledge` 这个路由**不新增、不删除、不改 href**，
> 只把它的**渲染体**从「平铺知识点表」换成「知识地图页」（那页自带 图谱/探索/列表/材料 四个视图），
> 再把顶栏那一项的字从「知识点」改成「知识地图」，并把它原来独有的 3 个能力**搬进**知识地图页。

---

## 0. 摘要（给只想读一页的人）

用户诉求（原话）：
> 「我需要把知识地图放在更方便进入的位置，我现在的想法是把知识点这个入口直接改成知识地图，
> 反正在那个页面也可以切换显示方式。」

用户的判断是**对的**，但有一个前提**不成立**，必须先讲清楚（§3.3）：

| 事实 | 现状 |
|---|---|
| 顶栏「知识点」指向 | `#/knowledge` → `pageKnowledge()`，一张**平铺全量表格** |
| 知识地图在哪 | `#/courses/<c>/knowledge-map` → `pageKnowledgeMap()`，**全站只有 1 个入口**：课程页页头一个不起眼的小链接（`views/courses.js:864`） |
| 知识地图有几种视图 | 4 种：图谱（默认）/ 探索 / 列表 / 材料 |
| 知识地图的「列表」视图 ≈ 旧平铺表？ | **大部分是**（标题/状态/证据数 都在，多了一列"来源材料"，少了"原文术语"列） |
| 但旧页有 **3 项能力**，知识地图页**一个都没有** | ①「一键补翻全部知识点为中文」按钮 + 费用提示 ②「记忆卡片」入口 ③ 每行「生成卡片」按钮 |

**所以本任务 = 换渲染体 + 换文案 + 把上面 3 项能力搬过去**（不做就是静默删功能，违反项目纪律）。

---

## 1. 背景（顺带回答用户当场提的另一个问题）

### 1.1 顶栏那条黄色横幅是什么

截图里的 `提示：当前 ASR / OCR 使用 Mock 实现；Mock 输出不是真实转录或识别结果。`

- **出处**：`src/web/app.js:1031`（`loadChrome()` 内）——
  `if (asr === 'mock' || ocr === 'mock') notes.push(t('ai.healthMock'))`，
  再经 `applyLoadNotes()`（`app.js:982`）合成到 `#banner`。
- **文案**：`src/web/i18n.js:482`（zh）/ `1000`（es）/ `1514`（ca），三语齐备。
- **触发条件**：`GET /api/health` 返回的 `processing.asr === 'mock'` **或** `processing.ocr === 'mock'`。
  截图右上角 pill 写的是 `ASR real · OCR mock` ⇒ 只有 OCR 是 Mock ⇒ 亮黄。
- **它有什么用**：Mock 返回占位文本，不是真实转录/识别结果。它在提醒：**此刻由 ASR/OCR 产出的证据不可信**，
  与项目「证据优先、绝不编造」是同一条线。`kind` 不是 `'bad'`，所以是**提示色**不是错误色（`app.js:986`）。
- **怎么消失**：`processing.ocr` 不再是 `mock` 时自动消失。属**配置/环境状态**，不是缺陷。
- **本任务不涉及它**，写在这里只是免得下一个人以为它是 bug。

---

## 2. 需求（用户原话 → 可验收的表述）

| # | 需求 | 验收标准（可复核） |
|---|---|---|
| R1 | 顶栏那一项从「知识点」改成「知识地图」 | 中文界面顶栏第 6 项显示「知识地图」；`href` **仍是** `#/knowledge` |
| R2 | 点它直接进知识地图页（能切显示方式） | 从任意页面点该入口，落地页 = 知识地图（4 个视图按钮可见），默认视图为**图谱** |
| R3 | 旧「知识点」平铺页不再是入口落点 | `#/knowledge` 不再渲染平铺表格 |
| R4 | **不丢功能** | 旧页那 3 项能力在知识地图页上仍可触达（§4.3） |
| R5 | 三语一致 | zh/es/ca 三语下入口文案都已更新，且 es/ca 界面不出现中文（`ui_audit` 的 CJK 检查） |

**边界（用户没要求，一律不做）**：
- 不删 `#/courses/<c>/knowledge-map` 路由，不删 `pageKnowledgeMap` 函数。
- 不改后端 `/api/knowledge`、`/api/course-knowledge`、`/api/courses/<id>/knowledge-map` 任何一个端点。
- 不新增前端文件（不碰 `index.html` 的 `<script>` 清单、`tests/support.py::WEB_SOURCE_ORDER`、4 个 `scripts/*.js` 的 `WEB_FILES`）。
- 不重构 `views/knowledge.js` 的其它部分。

---

## 3. 现状盘点（含精确位置，实施时以 `grep` 复核行号）

### 3.1 `#/knowledge` —— 旧「知识点」页

- 路由：`src/web/app.js:1552` → `else if (parts[0] === 'knowledge') await pageKnowledge();`
- 函数：`src/web/views/knowledge.js:113-153`（`async function pageKnowledge()`）
  - `markActiveNav('#/knowledge')`（第 114 行）
  - `await requireCourse()` → 取当前课程
  - `GET /api/knowledge?course_id=` + `GET /api/course-knowledge?course_id=`
  - 渲染：页头（`h1` = `知识点`、副标题含 `esc(courseLabel(courseId))`）
    + **一键补翻按钮**（`data-action="kp-backfill"` + `kpZh.backfillHint`，第 127-129 行）
    + 平铺表格（列：标题 / 状态 / 证据 / 原文术语，第 130-151 行）
    + 每行 **生成卡片** 按钮（`data-action="create-flashcard"`，第 141-143 行）
  - 副标题里还有 **记忆卡片** 链接（`<a href="#/flashcards">` + `t('fc.title')`，第 123-124 行）

### 3.2 `#/courses/<c>/knowledge-map` —— 知识地图页

- 路由：`src/web/app.js:1576`（TASK-KM-PREMIUM-EXECUTION 段）
- 函数：`src/web/views/knowledge.js:875-893`（`pageKnowledgeMap(courseId)`）
  - `markActiveNav('')`（第 881 行，**刻意清空** —— 顶栏没有它的分区）
  - `setRouteCourse(id)` → `showBanner('')` → `showLoading()` → `kmLoad(id)` → `kmPaint()`
- 渲染：`kmPaint()`（第 897-922 行）
  - 页头：面包屑「返回课程」+ `h1` = `t('km.title')`（= **知识地图**）+ 副标题 `km.subtitle`（含 `courseLabel(KM_STATE.courseId)`）
  - `kmConflictBanner()` + `kmStatsBar()`（计数）+ `kmToolbar()`（**4 个视图按钮** + 来源筛选 + 搜索）+ `#km-body` + `kmLegend()`
- 视图：`KM_STATE.view`，取值 `graph | explore | list | materials`（`views/knowledge.js:518`），默认 **`graph`**
- **全站入口只有 1 个**：`src/web/views/courses.js:864`（课程页页头 `<a href="#/courses/<id>/knowledge-map">知识地图 →</a>`）

### 3.3 能力差集（**本任务的核心风险点**）

| 旧「知识点」页有 | 知识地图页有吗 | 处置 |
|---|---|---|
| 平铺全量表格（标题/状态/证据/原文术语） | ✅ 列表视图（标题/状态/证据/**来源材料**）；少「原文术语」列 | 接受（产品规格 M2 的列表视图就是这样定义的） |
| 「一键补翻全部知识点为中文」按钮 + 费用提示 | ❌ **没有** | **必须搬过去**（§4.3-A） |
| 「记忆卡片」入口（→ `#/flashcards`） | ❌ **没有** | **必须搬过去**（§4.3-B） |
| 每行「生成卡片」按钮（`create-flashcard`） | ❌ **没有** | **搬进列表视图的行**（§4.3-C） |
| 主题数 / 关系数 / 卡片数计数 | ✅ `kmStatsBar()` 已有（口径更强） | 无需处理 |
| 面包屑「概览 / 课程 / 知识点 / 详情」在详情页 | — | 见 §5-F（文案要跟着改） |

> **纪律依据**：用户长期要求「功能下线要连带清掉专门服务于它的门面」的反面同样成立 ——
> **换入口不能顺手把入口背后的能力丢掉**。这三项都是用户真实在用的（补翻按钮旁边写着费用），
> 静默消失等于 bug。

---

## 4. 设计方案（推荐方案 A，唯一推荐）

### 4.1 路由与页面函数：不新增路由，改渲染体

**关键洞察**：`#/knowledge` 是**全局路由**（不含 courseId），而知识地图页是**课程级**的。
但 `pageKnowledge()` 里本来就有 `await requireCourse()` —— 它已经把「当前课程」解析出来了。
所以只要把 `requireCourse()` 的结果喂给知识地图的渲染路径即可，**不需要任何新路由、不需要动 `index.html` 的 href**。

这样做的**额外好处**（很重要，直接决定改动量）：

- `index.html` 的 `href="#/knowledge"` 一字不动 ⇒
  - `tests/test_web_ui.py::test_shell_navigation_covers_dashboard_course_knowledge_pages`（要求 `href="#/knowledge"` 存在）✅ 不变
  - `tests/test_web_ui_invariants.py::test_every_static_nav_link_is_routable`（href 必须可达）✅ 不变
  - `tests/test_multi_course.py::test_the_top_nav_ownership_table_is_exactly_as_declared` 里 `"pageKnowledge": "#/knowledge"` ✅ 不变
  - `scripts/ui_render_check.js:995` 的 `TOPNAV` 桩数组 ✅ 不变
- 全站 6 个指向 `#/knowledge` 的入口（§4.5）**全部自动改道**，不用逐个改链接。

### 4.2 抽出共享打开函数（**必须做**，否则顶栏高亮守卫会红）

`pageKnowledgeMap()` 里的 `markActiveNav('')` 与 `pageKnowledge()` 需要的 `markActiveNav('#/knowledge')` **冲突**。
所以把「打开某课程的知识地图」抽成一个**不写顶栏高亮**的函数，由两个入口各自声明归属：

```js
// views/knowledge.js —— 打开某门课程的知识地图页。
// **不写顶栏高亮** —— 归属由调用方声明（pageKnowledge → '#/knowledge'，
// pageKnowledgeMap → ''），与 app.js markActiveNav() 上方那张归属表一致。
async function kmOpenCourse(courseId) {
  setRouteCourse(courseId);
  showBanner('');
  showLoading();
  try {
    await kmLoad(courseId);
  } catch (err) {
    if (err instanceof ApiError) renderError(err);
    else renderError(new ApiError({
      code: 'INTERNAL_ERROR', message: String((err && err.message) || err),
    }));
    return;
  }
  kmPaint();
}
```

两个入口变成薄壳：

```js
async function pageKnowledge() {
  markActiveNav('#/knowledge');
  await kmOpenCourse(await requireCourse());
}

async function pageKnowledgeMap(courseId) {
  if (!courseId) {
    renderError(new ApiError({ code: 'NOT_FOUND', message: t('km.needCourse') }));
    return;
  }
  markActiveNav('');
  await kmOpenCourse(courseId);
}
```

> **为什么必须抽**：`tests/test_multi_course.py::test_the_top_nav_ownership_table_is_exactly_as_declared`
> 要求每个 `pageXxx()` 函数体内**恰好一次** `markActiveNav('<字面量>')`。
> 若 `pageKnowledge` 直接调 `pageKnowledgeMap()`，那条函数里的 `markActiveNav('')` 会把高亮清空 ⇒ 守卫红。
> 抽出的 `kmOpenCourse` **名字不以 `page` 开头**，所以不在这条守卫的扫描范围（它只扫 `^async function (page[A-Za-z]+)\(`）。

### 4.3 能力保全（3 项，逐项落到具体位置）

#### A. 一键补翻按钮 + 费用提示 → 放进 `kmPaint()` 的页头下方

在 `kmPaint()`（`views/knowledge.js:906`）的 `'</div>'`（页头结束）之后、`kmConflictBanner()` 之前，
插入与旧页**逐字相同**的 actions 块：

```js
'<div class="actions"><button data-action="kp-backfill" data-course="' +
esc(KM_STATE.courseId) + '">' + esc(t('kpZh.backfill')) + '</button>' +
'<span class="tiny muted">' + esc(t('kpZh.backfillHint')) + '</span></div>' +
```

- `data-action="kp-backfill"` 的派发在 `app.js:1492`，处理函数 `actionBackfillKpTranslations(courseId, button)`（`views/knowledge.js:92`）—— **一行都不用改**。
- **好处**：`scripts/ui_audit.js:3922-3924` 那两条断言（按钮存在 + 费用提示存在）**原样继续通过**，无需改审计。
- ⚠️ 小坑：`kmPaint()` 在搜索/切视图时**局部重绘**，按钮 DOM 会被替换；
  `actionBackfillKpTranslations` 的 `finally { button.disabled = false }` 会写到一个已脱离文档的节点上。
  **无副作用**（不报错、不闪），但要在代码注释里写明这一点，别让下一个人以为漏了状态恢复。

#### B. 「记忆卡片」入口 → 放进页头副标题（与旧页同位置）

旧页副标题末尾是 `· <a href="#/flashcards">记忆卡片</a>`。知识地图页的副标题是
`km.subtitle`（`'按来源探索知识点 · {course}'`）。**不要改 `km.subtitle` 的文案**（它被 `ui_audit` 的
`t('km.subtitle', ...)` 插值用例盯着），而是**在 `kmStatsBar()` 里或页头再追加一个独立链接**：

```js
'<p class="subtitle">' + esc(t('km.subtitle', { course: courseLabel(KM_STATE.courseId) })) +
' · <a href="#/flashcards">' + esc(t('fc.title')) + '</a></p>'
```

#### C. 每行「生成卡片」按钮 → 加进 `kmRenderList()` 的行模板

`kmRenderList()`（`views/knowledge.js:793-827`）第 814-817 行是标题单元格。照抄旧页的写法
（旧页在 `views/knowledge.js:140-144`）：有证据时给按钮，没证据时给 `fc.noEvidence` 灰字：

```js
(kp.evidence_refs || []).length
  ? ' <button class="small" data-action="create-flashcard" data-course="' +
    esc(KM_STATE.courseId) + '" data-knowledge="' + esc(kid) + '">' +
    esc(t('fc.create')) + '</button>'
  : ' <span class="tiny muted">' + esc(t('fc.noEvidence')) + '</span>'
```

- `data-action="create-flashcard"` 的派发在 `app.js:1497` 附近，处理函数无需改动。
- 只在**列表视图**加即可（图谱视图是 canvas，没有行；探索/材料视图是卡片/材料维度，不是逐 KP 行）。

### 4.4 默认视图：保持 `graph`

知识地图页的 `KM_STATE.view` 默认 `graph`（`views/knowledge.js:518`、`566`）。
用户说「反正在那个页面也可以切换显示方式」⇒ 落地即图谱，符合"知识地图"这个名字。
**不改默认值。** （如果用户事后说"我想落地就看列表"，那是 1 行改动，届时再改。）

### 4.5 其它 6 个指向 `#/knowledge` 的入口：处置表

| 位置 | 现文案 | 改道后落点 | 处置 |
|---|---|---|---|
| `index.html:34` | 知识点 | 知识地图 | **改文案**（§5-A） |
| `views/knowledge.js:242`（详情页面包屑） | 知识点 / 详情 | 知识地图 | **改文案**为 `t('km.title')`（§5-F）—— 不改就是"点面包屑到知识地图"却写着知识点，属本次改动引入的不一致 |
| `views/dashboard.js:280` | 知识点 → | 知识地图 | **保持不动**。语义仍读得通（该卡是"知识健康度"，点进去看地图/列表） |
| `views/courses.js:1172`（课堂页「本堂知识点」卡） | 全部 → | 知识地图 | **保持不动**。同上 |
| `views/exercises.js:73`（空态引导） | `t('nav.knowledge')` | 知识地图 | **自动跟随**（用的是同一个 key），无需改 |
| `views/flashcards.js:111`（空态引导） | `t('nav.knowledge')` | 知识地图 | **自动跟随**，无需改 |

> 若实施者认为 `dashboard.js` / `courses.js` 那两处文案也该跟着改，**先问用户**再动 ——
> 用户只要求改"入口"，其余属"顺手改"，本项目的纪律是不做。

---

## 5. 逐文件改动清单

> 行号为 **2026-10-12 快照**，实施时一律先 `grep -n` 复核。

### A. `src/web/index.html`

```diff
-      <a href="#/knowledge" data-i18n="nav.knowledge">知识点</a>
+      <a href="#/knowledge" data-i18n="nav.knowledge">知识地图</a>
```
**只改显示文本，`href` 与 `data-i18n` 一字不动。**

### B. `src/web/i18n.js`（三处值，**key 名不改**）

```diff
- 'nav.knowledge': '知识点',        // 第 44 行 (zh)
+ 'nav.knowledge': '知识地图',
- 'nav.knowledge': 'Conocimiento',  // 第 575 行 (es)
+ 'nav.knowledge': 'Mapa de conocimiento',
- 'nav.knowledge': 'Coneixement',   // 第 1089 行 (ca)
+ 'nav.knowledge': 'Mapa de coneixement',
```
- **key 名保留 `nav.knowledge`**（改名要连带改 `index.html` + `exercises.js` + `flashcards.js` 三处引用，是纯 churn；项目不禁止 key 名与文案不同名）。
- **三语必须同时改**：`tests/test_learning_view.py::TestI18nTables` 等有"三语 key 集合必须对齐"的守卫；
  值不改全不影响对齐，但 es/ca 的 `ui_audit` 有 **CJK 泄漏检查**，zh 值改了而 es/ca 没改不会红，但会**文案不一致**。

### C. `src/web/views/knowledge.js`（主战场）

1. **新增** `kmOpenCourse(courseId)`（§4.2），放在 `pageKnowledgeMap` 上方。
2. **重写** `pageKnowledge()`（第 113-153 行）→ 3 行薄壳（§4.2）。
   - **删除**：旧平铺表渲染体（第 116-152 行的 `api('/knowledge')` + `api('/course-knowledge')` + 表格 HTML）。
   - ⚠️ 删掉后，`pageKnowledge` 函数体内**不再出现 `esc(courseLabel(courseId))`** ⇒ 见 §6-③。
3. **重写** `pageKnowledgeMap(courseId)`（第 875-893 行）→ 薄壳（§4.2）。
4. **`kmPaint()`**（第 897-922 行）：页头后插入补翻 actions（§4.3-A），副标题追加记忆卡片链接（§4.3-B）。
5. **`kmRenderList()`**（第 793-827 行）：行模板加「生成卡片」按钮（§4.3-C）。

### D. `src/web/views/knowledge.js:242`（详情页面包屑）

```diff
-    '<a href="#/knowledge">' + t('知识点') + '</a>' + t(' / 详情') + '</div>' +
+    '<a href="#/knowledge">' + t('km.title') + '</a>' + t(' / 详情') + '</div>' +
```

### E. **不改**的文件（明确列出，避免下一个人顺手动）

- `src/web/app.js` —— 路由分支（1552 / 1576）、`data-action` 派发（1492 / 1497）、`markActiveNav` 注释表（1355 / 1364）：**全部不动**。
  - 注释表里 `#/knowledge` 仍归 `#/knowledge` 分区，语义未变。
- `src/web/index.html` 的 `<script>` 清单、`tests/support.py::WEB_SOURCE_ORDER`、4 个 `scripts/*.js` 的 `WEB_FILES`：**不动**（没有新增文件）。
- `src/web/styles.css`：复用既有 `.actions` / `.km-*` 类，**不动**（若发现样式缺口，只补最小规则并说明）。
- 后端 `src/api/`、`src/application/`：**不动**。

---

## 6. 守卫与审计脚本同步（**这是最容易漏的一节，逐条核**）

| # | 文件 | 位置 | 会不会红 | 处置 |
|---|---|---|---|---|
| ① | `tests/test_multi_course.py` | `:1417` `"pageKnowledge": "#/knowledge"`、`:1396` 归属表守卫 | ❌ 不会 | 保持（薄壳仍写 `'#/knowledge'`） |
| ② | `tests/test_web_ui_invariants.py` | `:361` 静态 nav 链接可达、`:378` 顶栏学习入口 | ❌ 不会 | 保持（href 未变） |
| ③ | `tests/test_student_ui.py` | `:70` `COURSE_LABEL_PAGES` 含 `"async function pageKnowledge("` | ✅ **会红** | **必须改**，见下方「③ 详情」 |
| ④ | `tests/test_student_ui.py` | `:437` `source.count("esc(courseLabel(courseId))") >= 8` | ✅ **会红**（少 1） | 实测新值后改阈值 + 更新注释 |
| ⑤ | `tests/test_web_ui.py` | `:467` 要求 `href="#/knowledge"` 存在 | ❌ 不会 | 保持 |
| ⑥ | `scripts/ui_audit.js` | `:3922-3924` 知识点页补翻按钮 + 费用提示 | ❌ 不会（**前提是采纳 §4.3-A**） | 保持；若不做 §4.3-A 就必须改它（= 删功能，不推荐） |
| ⑦ | `scripts/ui_audit.js` | `:1926` PAGES 含 `knowledgeMap` | ❌ 不会 | 保持 |
| ⑧ | `scripts/ui_audit.js` | `:1151-1152` `/api/knowledge`、`/api/course-knowledge` 夹具 | ❌ 不会 | **保留**（`pageExercises` 出题面板仍在用 `/api/knowledge`） |
| ⑨ | `scripts/ui_render_check.js` | 主 `ROUTES` 表（`:370` 起）**没有** `knowledge-map` 夹具 | ✅ **会红** | **必须加**夹具，见下方「⑨ 详情」 |
| ⑩ | `scripts/ui_render_check.js` | `:2088` / `:2114` 高亮断言 `['pageKnowledge', '#/knowledge']` | ❌ 不会 | 保持 |
| ⑪ | `scripts/ui_render_check.js` | `:2596` 切课表 `['#/knowledge', '#/knowledge', 'TITLE-GP', 'TITLE-GEO']` | ✅ **会红** | **必须改**，见下方「⑪ 详情」 |
| ⑫ | `scripts/ui_render_check.js` | `:995` `TOPNAV` 桩数组 | ❌ 不会 | 保持 |
| ⑬ | `scripts/ui_stress_check.js` | `:191` PAGES 含 `pageKnowledge` | ⚠️ **待实测** | 它跑**真实服务器**；换渲染体后需实测不抛异常、无 `undefined`/`NaN` |

### ③ 详情：`COURSE_LABEL_PAGES` 的等价替换（**不允许只删不加**）

`tests/test_student_ui.py:421-425` 断言：清单里每个函数体内都有 `courseLabel(courseId)` 字面量。
`pageKnowledge` 薄壳化后不再有它 ⇒ 必须改。**但不能只删** —— 那条守卫的意图是
「用户可见的课程标题只经 `courseLabel()` 一处产出」，删了等于降覆盖。做法：

1. 从 `COURSE_LABEL_PAGES` 移除 `"async function pageKnowledge("`。
2. **同文件新增**一条等价断言，覆盖知识地图页的课程标题：
   ```python
   def test_the_knowledge_map_heading_goes_through_the_shared_helper(self):
       """知识点入口现在渲染知识地图页；课程标题由 km.subtitle 经 courseLabel 产出。"""
       source = strip_js_comments(read_asset("views/knowledge.js"))
       body = function_body(source, "function kmPaint()")
       assert "courseLabel(KM_STATE.courseId)" in body
   ```
   （`function_body` / `read_asset` / `strip_js_comments` 本文件已有，直接复用。）
3. `:437` 的 `>= 8` → 实测新值（预计 **7**）。**必须实测**，不要猜；并同步更新该方法 docstring 里
   「下限是实测值」的说明。

### ⑨ 详情：`ui_render_check.js` 主 ROUTES 表要加夹具

`:2062` 有 `await pageKnowledge();`，随后断言 `a later page still works after an unknown course id`
（`!out.includes('请求失败') && !out.includes('NOT_FOUND')`）。
换渲染体后 `pageKnowledge` 会请求 `/api/courses/<COURSE_ID>/knowledge-map`，而主 ROUTES 表**没有这个键**
⇒ fetch 桩返回 404（`ui_render_check.js:1110`）⇒ 断言红。

**做法**：在主 `ROUTES`（`:370`）里补一条，形状与 `ui_audit.js:1120` 的 `knowledgeMap()` 夹具同源：

```js
['/api/courses/' + COURSE_ID + '/knowledge-map']: {
  data: {
    course_id: COURSE_ID,
    knowledge_points: [ /* 至少 1 条，字段与 /api/knowledge 夹具一致 */ ],
    materials: [],
    conflicts: [],
    stats: {},
    graph: { nodes: [], edges: [] },   // 无 graph 时 kmLoad 会回落，见 views/knowledge.js:542
  },
},
```
> 注：`views/knowledge.js:542` 对 `graph` 做了 `data.graph && Array.isArray(data.graph.nodes)` 保护，
> 所以 `graph` 可以省略；但**建议给空结构**，免得图谱视图在桩里走另一条分支而降低覆盖。

### ⑪ 详情：切课表那一行（**这里有个必须避开的假红坑**）

`:2596` 的 `['#/knowledge', '#/knowledge', 'TITLE-GP', 'TITLE-GEO']`：
`switchStaysOn()`（`:2544`）会在换课前/后断言页面上出现 `markerBefore` / `markerAfter` 文本。
`TITLE-GP` / `TITLE-GEO` 来自 `switchTable()` 里 `/api/knowledge` 的夹具标题（`:2464-2473`）。

**换渲染体后这些文本不会出现在 HTML 里** —— 因为知识地图**默认视图是图谱（canvas）**，
知识点标题只画在 canvas 上，DOM 里没有文本 ⇒ 直接换 marker 会**假红**（不是真缺陷）。

**做法（二选一，推荐 B）**：
- **A**：给 `switchTable()` 的每门课补 `/api/courses/<id>/knowledge-map` 夹具，
  marker 改用**图谱视图下确实在 DOM 里的文本** —— 即 `t('km.title')`（页头 `h1`）或
  副标题里的课程名（`courseLabel(KM_STATE.courseId)`）。`markerBefore` / `markerAfter` 可分别用
  `GP_NAME` / `GEO_NAME`。
- **B（推荐）**：把这一页从「文本 marker」断言改为「请求 + 状态」断言，更贴合真实语义：
  换课后断言 `sandbox.__fetched` 里出现了 `/api/courses/<GEO>/knowledge-map`
  且 `grab(sandbox, 'KM_STATE').courseId === GEO`，同时顶栏高亮仍是 `#/knowledge`。
  `switchStaysOn()` 现有签名只支持文本 marker，需要给这一页单独写一段（**不要**改 `switchStaysOn` 本身，
  它还被 `#/reviews`、`#/materials` 两页复用）。

> 无论 A/B，`switchTable()` **都必须**为两门课补 `knowledge-map` 夹具，否则换课请求 404。

---

## 7. 非目标（明确不做，避免范围蔓延）

1. **不**改知识地图的默认视图（保持 `graph`）。
2. **不**把 `#/courses/<c>/knowledge-map` 路由删掉（它是合法深链，`ui_audit:4200` 有深链用例）。
3. **不**改 `pageKnowledgeMap` 的顶栏高亮（保持 `''`；它确实不属于任何顶栏分区）。
4. **不**动 `dashboard.js` / `courses.js` 里的入口文案（§4.5）。
5. **不**动 `#/flashcards` 的顶栏高亮（`flashcards.js:56` 仍是 `'#/knowledge'` —— 卡片挂在知识分区下，语义未变）。
6. **不**新增前端文件、不新增 i18n key（`km.title`、`fc.title`、`kpZh.backfill*` 全部已存在且三语齐备）。
7. **不**改后端与数据库。

---

## 8. 验证方案（**只跑定向，不跑全量回归**）

> 用户 2026-10-10 明确要求：**不许跑全量**（一轮 20–60 分钟，不值当）。
> 只跑受影响文件 + 前端审计（秒级）。归因按「**失败的名字有没有变**」，不按「failed 是不是 0」。

### 8.1 定向 Python 测试

```bash
PYTHONPATH=/d/Project/Clases "C:/Users/Rafae/AppData/Local/Microsoft/WindowsApps/python.exe" \
  -m pytest tests/test_web_ui_invariants.py tests/test_multi_course.py \
            tests/test_student_ui.py tests/test_web_ui.py tests/test_knowledge_map.py \
  -q -p no:cacheprovider
```

- **本机长期存在的环境性失败**（与本次改动无关，出现即为环境）：
  `tests/test_student_ui.py::TestTheLiveSessionPickerEndToEnd::test_the_live_script_passes`
  —— `scripts/e2e_session_picker.js:27` 硬编码的 `Python/pythoncore-3.14-64/python.exe` 目录**已不存在** ⇒ `spawn ENOENT`。
- 注意：本机唯一带 pytest 的是 WindowsApps 的 3.14.5；托管 3.13.12 **没有 pytest**。

### 8.2 前端审计（两个都要跑，且必须看完退出码）

```bash
"C:/Users/Rafae/.workbuddy-ai/binaries/node/versions/22.22.2-6/node.exe" scripts/ui_render_check.js
"C:/Users/Rafae/.workbuddy-ai/binaries/node/versions/22.22.2-6/node.exe" scripts/ui_audit.js
```

- `ui_render_check.js` 基线 **OK (314 checks)**；`ui_audit.js` 基线 **UI audit OK (567 checks)**。
  数字只增不减（本次会加若干 check）。
- 若 `ui_stress_check.js` 要跑，它需要**真实服务器**在跑；先确认再跑，别对着空端口跑出假红。

### 8.3 变异测试（证明守卫真的在盯这条线）

| 变异 | 期望 |
|---|---|
| 把 `pageKnowledge()` 里的 `markActiveNav('#/knowledge')` 改成 `''` | `test_the_top_nav_ownership_table_is_exactly_as_declared` **必须红** |
| 把 `kmOpenCourse` 里 `kmLoad(courseId)` 改成 `kmLoad('')` | `ui_render_check` 的 `pageKnowledge` 相关断言 **必须红** |
| 从 `kmPaint()` 里删掉补翻 actions 块 | `ui_audit.js:3922` 的 `the knowledge list offers a one-click Chinese backfill` **必须红** |
| 把 `index.html` 的 href 改成 `#/knowledge-map` | `test_every_static_nav_link_is_routable` **必须红**（证明 href 必须保持不变） |

### 8.4 人工/浏览器验证（必须做，不能只靠桩）

1. 启动服务，中文界面点顶栏第 6 项 → 落地是**知识地图**（图谱视图、4 个视图按钮、补翻按钮、记忆卡片链接都在）。
2. 切到「列表」视图 → 每行有「生成卡片」按钮；点一个 → 卡片真的建出来。
3. 点「一键补翻全部知识点为中文」→ 任务坞出现进度，toast 报计数（幂等：二刷 `translated=0`）。
4. 切到 es / ca 界面 → 入口文案是 `Mapa de conocimiento` / `Mapa de coneixement`，页面无中文泄漏。
5. 从**详情页面包屑**、`#/flashcards` 空态、`#/exercises` 空态、概览「知识健康度」卡、课堂页「全部 →」
   五处分别点进 `#/knowledge` → 全部落到知识地图，**且顶栏「知识地图」保持高亮**。
6. 课程页页头那个 `知识地图 →` 深链（`#/courses/<c>/knowledge-map`）**仍然可用**（顶栏无高亮，符合归属表）。

---

## 9. 风险与回滚

| 风险 | 概率 | 缓解 |
|---|---|---|
| 漏掉 §6 的 ⑨/⑪，前端审计假红/真红 | **高** | §6 已逐条列出行号；实施后先跑 `ui_render_check.js` 再看别的 |
| 薄壳化后 `pageKnowledge` 触发 `COURSE_LABEL_PAGES` 守卫红 | **确定会** | §6-③ 给了等价替换写法，**不允许只删不加** |
| 补翻按钮放进 `kmPaint()` 后被局部重绘替换 DOM | 低 | 无功能影响（§4.3-A 已说明），加注释即可 |
| 图谱视图在审计沙箱里 canvas 无尺寸导致整页崩 | 低 | 既有代码已做「拿不到尺寸就 return」保护（`views/knowledge-graph.js`），别破坏它 |
| 顺手把 `dashboard.js`/`courses.js` 文案也改了 | 中 | §7-4 明令不动；要改先问用户 |

**回滚**：本任务全部改动都在前端，`git revert <commit>` 即可；无数据迁移、无库变更。

---

## 10. 交付物与验收标准（**必须给出可复核的数字**）

### 交付物
1. 改动后的 `src/web/index.html`、`src/web/i18n.js`、`src/web/views/knowledge.js`。
2. 同步后的 `tests/test_student_ui.py`、`scripts/ui_render_check.js`（+ 视情况 `scripts/ui_audit.js` 加一条 nav 文案断言）。
3. `docs/status.md` 追加 **TASK-105** 区块（沿用现有区块格式：依据 / 改动 / 验收 / 规模 / 风险）。
4. 实测输出粘贴：`ui_render_check.js` 与 `ui_audit.js` 的**完整尾部**（含 check 数）+ 定向 pytest 的 summary 行。

### 验收清单（逐条打勾，缺一不可）
- [ ] `grep -c 'href="#/knowledge"' src/web/index.html` == **1**（href 未变）
- [ ] 中文界面顶栏第 6 项文本 == `知识地图`；es/ca 分别为 `Mapa de conocimiento` / `Mapa de coneixement`
- [ ] `pageKnowledge` 函数体内 `markActiveNav` 恰好一次，实参为 `'#/knowledge'`
- [ ] `pageKnowledgeMap` 函数体内 `markActiveNav` 恰好一次，实参为 `''`
- [ ] 知识地图页 DOM 里同时存在 `data-action="kp-backfill"`、`kpZh.backfillHint` 文案、`href="#/flashcards"`
- [ ] `kmRenderList()` 行模板里有 `data-action="create-flashcard"`
- [ ] `ui_render_check.js` = OK，check 数 **≥ 314**
- [ ] `ui_audit.js` = OK，check 数 **≥ 567**
- [ ] 定向 pytest 中，失败项**只有** §8.1 列出的那条环境性 e2e 用例（或零失败）
- [ ] §8.3 的 4 条变异全部按预期变红
- [ ] §8.4 的 6 条浏览器验证全部通过（附截图或文字记录）

---

## 11. 给实施者（下一个 agent）的操作纪律

1. **仓库是 git 仓库**（`main` 分支），改动前先 `git status` 看清工作区；**改动前不要 reset/revert 别人的东西**。
2. **Codex 可能在并行改同一个仓库**。做法：`git add <自己改的文件名>`，**绝不用 `git add -A` / `git add .`**；
   提交前 `git diff --cached --stat` 核对暂存区只有自己的文件。
3. **推送（`git push`）会失败**：本机没有 GitHub 凭据（`401` + 读不到 Username）。
   提交可以，推送交给用户自己（`gh auth login` 或本地终端 `git push origin main`）。**不要反复试 push**。
4. **行尾**：`.gitattributes` 规定 `*.py/*.js/*.css/*.html/*.json/*.md` 用 **LF**。别把行尾弄反。
5. **同一文件的多处修改必须串行**（一次一个 Edit，看结果再下一个）。
6. **精准修改**：只改 §5 列出的位置。发现无关的死代码，**只报告不删除**。
7. **不跑全量回归**（§8 顶部）。只跑定向 + 两个前端审计。
8. **有疑问先问用户**：尤其是 §4.5 的两处入口文案（`dashboard.js:280`、`courses.js:1172`）要不要一起改。

---

## 12. 参考

- `docs/knowledge-map-product-spec.md`（M2，知识地图四视图的产品定义）
- `docs/knowledge-map-design-system.md`（M3，设计 token）
- `docs/task-82-optimization-plan.md`（TASK-82…104，知识地图的修复与优化总计划；§0.1 是滚动进度）
- `reports/pai-knowledge-map-feedback.md`（真实课件端到端实测反馈）
- `src/web/app.js:1340-1379`（`markActiveNav` 上方那张**顶栏归属表**，改高亮必须同步它）
