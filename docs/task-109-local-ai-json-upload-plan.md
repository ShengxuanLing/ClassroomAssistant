# TASK-109 任务计划书 —— 「手动 AI」改名「本地 AI 分析」+ 导入改为上传 JSON 文件

- 日期：2026-10-15
- 基线：`cbd0f6b`（`Refine tokenized web UI and design guards`），分支 `main`
- 依据：用户 2026-10-15 的口述需求（三张截图：知识地图页 / 材料列表页 / 手动 AI 面板页）
- 状态：**待实施**。本文只是计划书，不含任何代码改动。
- 交付物：**一份文档**。不新增 README、不新增脚本、不留备份。

> **纪律声明**
> 1. 本文把**实测**（带 `文件:行` 证据）与**推断**分开写。推断一律标「推断」。
> 2. 实施者**不得扩大范围**：§9 明确列了"不在本次范围"的清单。
> 3. 用户原话是唯一依据。文档没要求的一律不加。

---

## 0. 一句话目标

材料页那个「手动 AI」入口，**用户看到的文案**改成「本地 AI 分析」；
面板里的「粘贴模型回复」文本框改成**上传 JSON 文件**；
并且任务包（复制给模型的那份）里要**明确要求模型把结果输出成一个 JSON 文件**，
让用户拿到文件就能直接上传。

**机制一行不改**：还是「导出任务包 → 外部模型 → 导入响应」，还是同一条
`AIAnalysisService.analyze` 管线、零出站、零密钥。

---

## 1. 需求原文 → 可验收的拆解

| # | 用户原话 | 拆解成什么 | 验收判据（可复核） |
|---|---|---|---|
| R1 | 「手动ai改成本地ai分析」 | 材料页入口按钮 + 面板标题等**用户可见文案**改成「本地 AI 分析」 | 材料表那一列不再出现「手动 AI」字样；面板标题为「本地 AI 分析」；三语同步 |
| R2 | 「我更希望这里设计成上传json文档的形式」 | 导入响应的**主路径**变成选一个 `.json` 文件 | 面板里有 `<input type="file">`；选中合法文件后能导入成功；选非法文件有明确人话错误 |
| R3 | 「在复制任务包的时候直接要求本地模型给的反馈是json文件格式，方便用户直接上传」 | 任务包里的说明必须**显式要求模型输出 JSON 文件**，并给出可直接照抄的完整骨架 | `instructions_markdown` 里出现"保存为 .json 文件"这类要求；骨架里预填了全部 `request_id` |

R2 与 R3 是一体的：R3 让模型产出可上传的东西，R2 让用户能把它传回来。
**只做 R2 不做 R3，用户拿到的还是"一段聊天里的 JSON"，仍然要手工拼壳。**

---

## 2. 现状（全部实测，带证据）

### 2.1 入口按钮

`src/web/views/materials.js:173`

```js
html += '<button data-action="manual-ai-open" ' + base + '>' + esc(t('manual.open')) + '</button> ';
```

`t('manual.open')` = `手动 AI`（`src/web/i18n.js:562`，es `:1120`，ca `:1678`）。

点击走全局委托：`src/web/app.js:1500-1517`（`closest('[data-action]')` → 分支表）。
另外两个分支是 `manual-ai-copy` / `manual-ai-import`。

### 2.2 面板

`src/web/views/materials.js:604-643` `renderManualAiPanel(state)`，容器是
`#manual-ai-panel`（`materials.js:90` 预置的空 div）。当前结构：

| 元素 | 行 | 说明 |
|---|---|---|
| 复制按钮 | `:624` | `data-action="manual-ai-copy"` |
| 导入按钮 | `:625` | `data-action="manual-ai-import"` |
| 任务包只读 textarea | `:632-633` | `id="manual-ai-bundle"`，内容是 `JSON.stringify(bundle, null, 2)` |
| 操作说明 `<details>` | `:634-635` | 只展示，**不可复制**（但 `instructions_markdown` 本身是 bundle 的一个字段，随复制一起走） |
| **粘贴响应 textarea** | `:636-637` | `id="manual-ai-responses"` ← **本次要换掉的对象** |
| 导入结果摘要 + 逐请求状态表 | `:638-639` | `manualImportSummary` / `manualRequestRows` |

三个动作函数：`actionManualAiOpen`（`:645`）、`actionManualAiCopy`（`:667`）、
`actionManualAiImport`（`:682`）。

### 2.3 导入链路（实测，逐跳）

```
actionManualAiImport                        materials.js:682
  └─ JSON.parse(文本域内容)                    :690   ← 非法 JSON 在发请求前就拦下
  └─ POST /api/materials/{id}/manual-ai/import?course_id=…
       ├─ endpoints.import_manual_ai_responses  src/api/endpoints.py:424
       └─ workspace.import_manual_ai_responses  src/application/workspace.py:2582
            ├─ build_manual_bundle（按材料**当前**内容重算 bundle_id）  :2636
            ├─ parse_response_payload(payload, bundle)   manual.py:505   ← 整包校验
            ├─ responses_by_chunk                        manual.py:608
            ├─ ReplayAIProvider（零网络，按 chunk_id 查表） manual.py:620
            └─ AIAnalysisService().analyze(...)          :2659  ← 与真实调用同一条管线
```

**协议（v1）**，`src/application/ai/manual.py:18-47`：

```json
{
  "protocol_version": "manual-ai/v1",
  "bundle_id": "mab-<sha16>",
  "model_name": "可选（用户自报，不计为已验证身份）",
  "responses": [{"request_id": "req-<sha16>", "content": "<模型返回的 JSON 文本>"}]
}
```

**整包拒绝**的结构性错误（抛 `ManualProtocolError` → HTTP 400）：协议版本、
`bundle_id` 缺失/不匹配、`responses` 形状不对、条数 > `MAX_REQUESTS`（200）。
逐条问题（缺失/未知/重复/畸形 JSON/schema/超长）记进逐请求状态，允许部分成功。

### 2.4 用户可见的"手动 AI"文案清单（实测 grep）

| 位置 | 现文案 |
|---|---|
| `i18n.js:562/1120/1678` `manual.open` | 手动 AI / IA manual / IA manual |
| `i18n.js:563/1121/1679` `manual.title` | 手动 AI 分析 / Análisis IA manual / Anàlisi IA manual |
| `i18n.js:564` `manual.intro` | 没有 API Key 也能用：导出任务包，交给网页版模型，再把回复粘贴回来。 |
| `i18n.js:573/1131/1689` `manual.responsesTitle` | 粘贴模型回复 / … |
| `i18n.js:588/1146/1704` `manual.sourceNote` | 来源：用户粘贴（不是真实 API 调用） |
| `i18n.js:595/1153/1711` `manual.emptyPaste` | 请先粘贴模型回复。 |
| `manual.py:407` | `# 手动 AI 分析任务包`（`instructions_markdown` 一级标题） |
| `manual.py:423` | 「按下面的格式把结果贴回「手动 AI 分析」面板并导入。」 |
| `manual.py:474` | 「手动导入的来源会如实记为 `user-pasted`…」 |
| `docs/status.md:8869` | 历史记录，**不要改**（见 §9） |

### 2.5 守卫 / 测试面（谁会被这次改动碰到）

| 守卫 | 位置 | 约束 |
|---|---|---|
| 三语 key 集合必须完全相同 | `scripts/ui_audit.js:2305-2306`；`tests/test_learning_view.py:1377` | 任何新 i18n key **必须三语同加** |
| 用到的 key 必须已定义 | `tests/test_learning_view.py:1382-1399` | 新增 `t('manual.x')` 忘了加表 → 红 |
| `data-i18n` 的 key 必须已定义 | `tests/test_exercise_ui.py:1419` | 本次不用 `data-i18n`，不受影响 |
| es/ca 渲染结果里不得出现 CJK | `scripts/ui_audit.js:2069-2083` | 译文里不许有中文；**夹具内容也不许有中文** |
| 译文里不得含中文 | `tests/test_learning_view.py:1414` | 同上 |
| `instructions_markdown` 的可读性断言 | `tests/test_manual_ai_bundle.py:245-254` | 必须仍含 `bundle_id` / `protocol_version` / `request_id`，且不含 `classroom-data` |
| 手动 AI 单元/集成测试 | `tests/test_manual_ai_bundle.py`（950 行） | 断言 `provider == USER_PASTED_PROVIDER_NAME`（`:485`、`:643`） |

### 2.6 两个顺手发现（**不是**本次需求，但必须写下来）

**发现 A：`MAX_BUNDLE_BYTES` 是个死常量。**
`src/application/ai/manual.py:130` 定义了 `MAX_BUNDLE_BYTES = 8 * 1024 * 1024`，
并在 `__all__`（`:96`）里导出，但**全仓没有任何使用点**（实测
`grep -rn MAX_BUNDLE_BYTES src/ tests/` 只命中定义与导出两行）。
HTTP 层真正的体积闸门是 `src/api/server.py:289` 的 `server.max_upload_bytes`，
而它的默认值是 `endpoints.MAX_UPLOAD_BYTES = 200 * 1024 * 1024`（`endpoints.py:35`）
—— 也就是说，导入端点实际**没有** 8 MiB 那层保护。

**发现 B：前端审计完全不覆盖手动 AI 面板。**
实测 `grep -rn "manual" scripts/ui_audit.js scripts/ui_render_check.js` **零命中**。
原因是面板只有在 `__manualAi[materialId]` 里有 bundle 时才渲染
（`materials.js:608-612`），而审计从不构造那个状态。
⇒ 本次必须补上这个空白，否则"改了面板但没人看得见"。

---

## 3. 目标设计

### 3.1 R1 —— 改名：改什么、**绝不**改什么

**只改用户可见字符串。** 下表是全部改动点（三语同步）：

| key | 现值（zh） | 新值（zh） |
|---|---|---|
| `manual.open` | 手动 AI | **本地 AI 分析** |
| `manual.title` | 手动 AI 分析 | **本地 AI 分析** |
| `manual.intro` | 没有 API Key 也能用：导出任务包，交给网页版模型，再把回复粘贴回来。 | **没有 API Key 也能用：导出任务包交给本地模型，把它返回的 JSON 文件上传回来。** |
| `manual.responsesTitle` | 粘贴模型回复 | **上传模型返回的 JSON 文件** |
| `manual.sourceNote` | 来源：用户粘贴（不是真实 API 调用） | **来源：本地模型返回的文件（不是真实 API 调用）** |
| `manual.emptyPaste` | 请先粘贴模型回复。 | **请先选择要上传的 JSON 文件。** |

es / ca 对应改（**不得出现任何 CJK 字符**）：

| key | es | ca |
|---|---|---|
| `manual.open` | `Análisis IA local` | `Anàlisi IA local` |
| `manual.title` | `Análisis IA local` | `Anàlisi IA local` |
| `manual.intro` | `Sin clave de API: exporta el paquete, entrégalo a un modelo local y sube aquí el archivo JSON que devuelva.` | `Sense clau d'API: exporta el paquet, dona'l a un model local i puja aquí el fitxer JSON que retorni.` |
| `manual.responsesTitle` | `Sube el archivo JSON devuelto por el modelo` | `Puja el fitxer JSON retornat pel model` |
| `manual.sourceNote` | `Origen: archivo devuelto por el modelo local (no es una llamada real a la API)` | `Origen: fitxer retornat pel model local (no és una crida real a l'API)` |
| `manual.emptyPaste` | `Selecciona primero el archivo JSON que quieres subir.` | `Selecciona primer el fitxer JSON que vols pujar.` |

服务端 `src/application/ai/manual.py::render_instructions_markdown` 里两处：

- `:407` `# 手动 AI 分析任务包` → `# 本地 AI 分析任务包`
- `:423` 「按下面的格式把结果贴回「手动 AI 分析」面板并导入。」 → 见 §3.3 的新文案
- `:474` 「手动导入的来源会如实记为 `user-pasted`…」 → 「本地模型返回的文件会如实记为
  `user-pasted`，不会冒充真实 API 调用。」（**保留那个 token**，理由见下）

**绝不改（改名禁区）** —— 这些是机器标识 / 已落盘 / 已被断言：

| 不动的对象 | 位置 | 理由 |
|---|---|---|
| 协议版本 `manual-ai/v1` | `manual.py:112` | 改了会**拒掉用户手上所有已导出的包**，且 `test_manual_ai_bundle.py` 直接断言 |
| 端点路径 `/manual-ai/export`、`/manual-ai/import` | `endpoints.py:471/474` | 改了要连带改端点差集文档、前端、临时验收脚本 |
| provider 串 `user-pasted` | `manual.py:121` | ① 已落进历史报告；② `test_manual_ai_bundle.py:485/643` 断言；③ **是业务逻辑**——`workspace.py:2405` 靠 `"user-pasted" in name_hint` 判定 `ai_mode="manual"`，改了这个串会把手动模式误判成 `real` |
| 模块名 `manual.py`、常量 `MANUAL_*`、`ReplayAIProvider` | — | 纯内部命名，改名只有 churn，没有收益 |
| i18n **key 名** `manual.*` | — | key 名是内部标识，改它要动 3 张表 + 所有 `t()` 调用点 |
| `data-action="manual-ai-*"` | `materials.js:624/625`、`app.js:1515-1517` | 同上 |
| 测试函数名、报告文件名 | — | 见 §9 |

> **为什么不改 `user-pasted`：** 面板标题叫「本地 AI 分析」而报告里写
> `provider=user-pasted`，读起来像不一致。但那个串的语义是"**没有发生任何外部 API 调用**"，
> 而且它同时是模式判定依据（`workspace.py:2405`）。**只改展示文案，不动存储标识。**

### 3.2 R2 —— 导入改为上传 JSON 文件

#### 交互（推荐流程）

```
[ 上传模型返回的 JSON 文件 ]
   <input type="file" id="manual-ai-file" accept=".json,application/json">
   提示行：把模型返回的内容保存成 .json 文件再选它（UTF-8 编码）。
   摘要行：已选择：manual-ai-mab-xxxx.json · 1.2 KB · 22 条响应
[ 导入响应 ]   ← 仍然需要再点一次
```

**选文件 ≠ 立即导入。** 理由：① 用户能在提交前看到"读到了几条响应"；
② 导入失败时文件还选着，可以直接重试；③ 与项目"显式触发"的一贯风格一致
（上传材料、一键分析都是显式按钮）。

#### 读取与解析（关键：容错必须有边界）

前端新增一个**纯函数** `manualNormalizeResponseText(text)`，只做输入卫生，
**不做任何字段填充**：

1. 去掉 UTF-8 BOM（`\uFEFF`）。**Windows 记事本 / 很多本地模型落盘的文件带 BOM**，
   `JSON.parse` 会直接失败 —— 这是最可能的真实故障。
2. 去掉一层最外层 markdown 代码围栏（模型常用的「三反引号 + `json` 标注」那种，
   以及不带语言标注的三反引号）。
   模型极常把 JSON 包在围栏里，用户复制保存后就成了围栏文件。
3. 两侧 `trim()`。
4. **到此为止。** 不补 `bundle_id`、不补 `protocol_version`、不把裸数组包成外壳。
   缺字段就报缺字段 —— 那是 §2.3 里"整包拒绝"的机制，不能在前端抹掉。

然后 `JSON.parse` → 得到 payload。失败 → 明确的"不是合法 JSON"提示
（附 `err.message` 便于排错，但**不要把文件原文渲染进 DOM**）。

#### 选文件时的形状预检（**必须在客户端做**，理由见下）

实测发现：`src/api/server.py:389` 是

```python
detail = dict(mapped.detail) if (self.debug and mapped.detail) else None
```

而 `debug` 默认 `False`（`server.py:119`）。也就是说
**后端把 `protocol_error` 放在 `detail` 里，而 `detail` 只在 debug 模式下才回传前端。**
前端拿到的只有 `err.code == "INVALID_INPUT"`（`errors.py:96`）和一段英文 message。
⇒ **靠 `err.detail.protocol_error` 做友好提示这条路走不通**（除非改后端的错误回传策略，
那超出本次范围）。所以友好提示必须在**发请求之前**、在客户端用已经加载的 bundle 做预检：

| 预检 | 判据 | 提示 key |
|---|---|---|
| 顶层不是对象 | `Array.isArray(payload)` 或 `typeof !== 'object'` | `manual.file.bare` ——「这看起来是模型的直接输出，缺少外层包裹（protocol_version / bundle_id / responses）。请使用任务包「回复格式」里给出的完整骨架。」 |
| 像任务包 | 有 `requests` 且没有 `responses` | `manual.file.isBundle` ——「这是任务包本身，不是模型回复。」 |
| 缺 `protocol_version` | — | `manual.file.missingProtocol` |
| 缺 `bundle_id` | — | `manual.file.missingBundle` |
| `bundle_id` ≠ 当前面板的 bundle | 有 `state.bundle.bundle_id` 才比 | `manual.file.bundleMismatch` ——「这份回复属于另一个任务包（材料或版本不同）。请重新导出任务包并让模型重做。」 |
| `responses` 不是数组 | — | `manual.file.missingResponses` |
| 协议版本不认识 | 值 ∉ `{"manual-ai/v1", "v1"}` | `manual.file.badProtocol` |

预检**只是提示，不是闸门**：预检不通过时**不发送请求**（省一次往返，且错误信息更清楚）；
预检通过后照常发请求，**服务端仍是唯一权威**（`bundle_id` 重算比对那套一个字都不动）。

#### 状态保存（重绘不丢）

`__manualAi[materialId]` 增加一个 `file` 字段：

```js
state.file = {
  name: 'xxx.json',      // 文件名
  bytes: 12345,          // 体积
  payload: {...},        // 解析后的对象（预检通过才有）
  errorKey: '',          // 预检/解析失败的 i18n key
  errorDetail: '',       // 失败原因（err.message），只展示文本
  responseCount: 22,     // 读到的响应条数（便于用户确认没读错文件）
};
```

**必须存在 state 里**，因为 `renderManualAiPanel` 每次都重写 `panel.innerHTML`
（`materials.js:641`）—— 存在 DOM 上的东西一重绘就没了。

#### 事件接线（不能走 click 委托）

现有的 `[data-action]` 委托是 **click** 委托（`app.js:1500`），文件输入要的是
**change** 事件，而且给 `<input type="file">` 挂 `data-action` 会让"点一下打开选择框"
误触发一个 click 分支。所以：

- `<input>` 用 `id="manual-ai-file"`，**不挂 `data-action`**；
- 在 `renderManualAiPanel` 末尾调用 `wireManualAiFileInput(panel)`，
  它自己 `querySelector('#manual-ai-file')` + `addEventListener('change', …)`。
  这与既有 `wireUploadForm()`（`materials.js:224-260`）的写法一致。

**这个接线函数必须能在审计沙箱里安全地什么都不做**（见 §6.3）：
沙箱的 `querySelector()` 返回 `null`（`ui_audit.js:1763`），
所以第一行就要 `if (!input || typeof input.addEventListener !== 'function') return;`。
同理 `FileReader` 在沙箱里**不存在**，读取路径要 `typeof FileReader === 'undefined'` 时
给出 `manual.file.readUnsupported` 而不是抛 `ReferenceError`。
（这是 `knowledge-graph.js` 那条"canvas 拿不到尺寸就 return，别让整页崩"教训的同一类问题。）

#### `actionManualAiImport` 的改动（尽量小）

保持函数签名与调用点不变，只改"从哪里取 payload"：

```js
// 优先用选中的文件；没有再回落到粘贴框（若保留，见 §8-D1）
let payload = state.file && state.file.payload;
if (!payload) {
  const raw = (粘贴框 ? 粘贴框.value : '').trim();
  if (!raw) { toast(t('manual.emptyPaste'), 'bad'); return; }
  try { payload = JSON.parse(manualNormalizeResponseText(raw)); }
  catch (err) { toast(t('manual.invalidJson'), 'bad'); return; }
}
```

后面的 `startTask` / `POST` / `finishTask` / `route()` / 结果渲染**逐字不动**。

### 3.3 R3 —— 任务包要求模型返回 JSON 文件

改 `src/application/ai/manual.py::render_instructions_markdown`（`:404-475`）。
要点（**必须保留 `tests/test_manual_ai_bundle.py:245-254` 的四条断言**：
含 `bundle_id`、含 `protocol_version`、含 `request_id`、不含 `classroom-data`）：

1. 「怎么用」第 3 步从"贴回面板"改成：

   > 3. **让模型把结果写成一个 JSON 文件保存下来**（不要只把 JSON 贴在对话里）。
   >    文件名建议 `manual-ai-<bundle_id>.json`，编码用 UTF-8。
   >    直接输出 JSON 本体即可；如果模型习惯用「三反引号 + json 标注」包起来也能用，
   >    但不要依赖它。

2. 「回复格式」那一节，把现在**只给一条示例**的 `response_wrapper`
   （`manual.py:433-455`）改成**给出完整骨架**：`protocol_version` + `bundle_id`
   照填，`responses` 数组里**把全部 `request_id` 预填好、`content` 留空字符串**：

   ```json
   {
     "protocol_version": "manual-ai/v1",
     "bundle_id": "mab-<真实值>",
     "model_name": "",
     "responses": [
       {"request_id": "req-<真实值1>", "content": ""},
       {"request_id": "req-<真实值2>", "content": ""}
     ]
   }
   ```

   **这是让"直接上传"成立的关键**：用户把这个骨架原样丢给模型，
   模型只要往 `content` 里填东西，产出的文件天然就是导入器要的形状。
   代价是 `instructions_markdown` 变大（实测每请求约 100 字节，22 请求 ≈ 2.2 KB，
   `MAX_REQUESTS` 上限 200 时 ≈ 20 KB）—— 可接受，且它是**确定性**的，
   不含任何时间戳，不影响 `bundle_id`（`bundle_id` 只由材料/版本/chunk 决定，
   见 `manual.py:178-201`；`instructions_markdown` 是 `workspace.py:2562`
   在算完 `bundle_id` **之后**才挂上去的）。

3. 面板上也给一行可见提示（`manual.file.hint`）：
   「把模型返回的内容保存为 .json 文件后，在下面选择它。」

> **不做的事**：不把骨架做成单独的可下载文件（`<a download>`）。
> 那是额外功能，见 §8-D3。

---

## 4. 逐文件改动清单

| # | 文件 | 位置 | 改什么 |
|---|---|---|---|
| 1 | `src/web/i18n.js` | `:562-564`、`:573`、`:588`、`:595`（zh）；`:1120-1122`、`:1131`、`:1146`、`:1153`（es）；`:1678-1680`、`:1689`、`:1704`、`:1711`（ca） | 改 §3.1 的 6 个键；新增下面 9 个键（**三语同加**） |
| 2 | `src/web/views/materials.js` | `:604-643` `renderManualAiPanel` | 把 `#manual-ai-responses` 文本框换成文件输入 + 提示行 + 摘要行 |
| 3 | 同上 | 新增 `manualNormalizeResponseText(text)` | 纯函数：去 BOM、去一层围栏、trim |
| 4 | 同上 | 新增 `manualFileProblem(payload, bundle)` | 纯函数：返回 §3.2 表格里的提示 key（或 `''`） |
| 5 | 同上 | 新增 `wireManualAiFileInput(panel)` | change 监听 + `FileReader` 读取 + 写 `state.file` + 重绘 |
| 6 | 同上 | `:682-719` `actionManualAiImport` | 只改 payload 来源（§3.2 末） |
| 7 | `src/application/ai/manual.py` | `:404-475` `render_instructions_markdown` | 改标题与"怎么用"文案；`response_wrapper` 改成完整骨架 |
| 8 | `scripts/ui_audit.js` | 新增检查块 | 见 §6.3 |
| 9 | `tests/test_manual_ai_bundle.py` | `:245-254` | 追加断言：说明里要求保存成 `.json` 文件；骨架里**预填了全部 `request_id`** |

**不需要改的**（明确写出来防止实施者顺手改）：

- `src/web/app.js` —— 委托分支 `manual-ai-import` 不变，函数签名不变。
- `src/api/endpoints.py` / `src/application/workspace.py` —— 导入链路一个字不动。
- `src/web/index.html`、`tests/support.py::WEB_SOURCE_ORDER`、`scripts/*.js` 的
  `WEB_FILES` —— **本次不新增任何前端文件**，所以这四处**不用同步**
  （`test_web_source_manifest` 守卫不会红）。

**新增 i18n 键（9 个，zh 示例；es/ca 必须同加且无 CJK）：**

```js
'manual.fileTitle': '上传模型返回的 JSON 文件',
'manual.fileHint': '把模型返回的内容保存为 .json 文件（UTF-8）后，在下面选择它。',
'manual.fileNone': '尚未选择文件。',
'manual.fileChosen': '已选择：',
'manual.fileResponses': '条响应',
'manual.fileTooLarge': '这个文件太大（超过 8 MB），请确认选对了文件。',
'manual.fileUnreadable': '读不出这个文件，请重新保存为 UTF-8 编码的 .json 后重试。',
'manual.fileNotJson': '这个文件不是合法的 JSON。',
'manual.fileBare': '这看起来是模型的直接输出，缺少外层包裹（protocol_version / bundle_id / responses）。请使用任务包「回复格式」里给出的完整骨架。',
'manual.fileIsBundle': '这是任务包本身，不是模型回复。',
'manual.fileMissingProtocol': '缺少 protocol_version。',
'manual.fileMissingBundle': '缺少 bundle_id。',
'manual.fileBundleMismatch': '这份回复属于另一个任务包（材料或版本不同）。请重新导出任务包，让模型重做一次。',
'manual.fileMissingResponses': '缺少 responses 数组。',
'manual.fileBadProtocol': '协议版本不认识（应为 manual-ai/v1）。',
'manual.fileReadUnsupported': '当前环境无法读取本地文件，请改用粘贴。',
```

> 键名数量以实施时的实际需要为准；**每一把用到的键都必须三语齐全**，
> 否则 `ui_audit` 的 key parity（`:2305`）与 `test_learning_view.py:1382` 会红。
> 若 §8-D1 决定删掉粘贴框，则 `manual.emptyPaste`、`manual.responsesPlaceholder`
> 变成无消费者的死键 —— 一并删掉（三语同删）。

---

## 5. 必须覆盖的边界与错误路径

每一条都要有对应的实现分支，并在 §6.3 的审计检查里钉住（纯函数部分）。

| # | 输入 | 期望行为 |
|---|---|---|
| E1 | 没选文件就点「导入响应」 | `manual.emptyPaste` 提示，**不发请求** |
| E2 | 选了 `.json`，内容带 UTF-8 BOM | **正常导入**（BOM 被剥掉） |
| E3 | 内容被「三反引号 + json 标注」围栏包住 | **正常导入**（围栏被剥掉一层） |
| E4 | 内容不是 JSON（例如纯文本 / 半个 JSON） | `manual.fileNotJson`，不发请求 |
| E5 | 内容是裸数组 `[{request_id, content}, …]` | `manual.file.bare`，不发请求 |
| E6 | 内容就是导出的任务包（有 `requests`） | `manual.fileIsBundle`，不发请求 |
| E7 | 顶层对象缺 `bundle_id` | `manual.fileMissingBundle`，不发请求 |
| E8 | `bundle_id` 是**别的材料**的 | `manual.fileBundleMismatch`，不发请求 |
| E9 | `protocol_version` = `manual-ai/v9` | `manual.fileBadProtocol`，不发请求 |
| E10 | 缺 `responses` | `manual.fileMissingResponses`，不发请求 |
| E11 | 文件 > 8 MB | `manual.fileTooLarge`，不发请求（**不读**，先看 `file.size`） |
| E12 | `FileReader` 报错 / 环境无 `FileReader` | `manual.fileUnreadable` / `manual.fileReadUnsupported` |
| E13 | 一切正常，22 条响应 | 走原路径；摘要行显示 `22 条响应`；导入结果与今天的粘贴路径**逐字段相同** |
| E14 | 响应内容含某条超长 / 畸形 / 未知 request_id | 服务端逐条状态照旧（`manual.status.*`），**不受本次改动影响** |
| E15 | 选文件后**又点了一次**「导出任务包」（重绘面板） | 摘要行仍在（状态在 `state` 里，不在 DOM 里） |

> **实测注意**：E13 是回归红线 —— 用同一份响应分别走"粘贴"与"上传文件"，
> 两次的 `requests_applied` / `auto_accepted_count` / 落库 KP 数必须**完全一致**。

---

## 6. 守卫与测试同步

### 6.1 定向 pytest（**不许跑全量回归**）

```bash
PYTHONPATH=/d/Project/Clases "C:/Users/Rafae/AppData/Local/Microsoft/WindowsApps/python.exe" \
  -m pytest tests/test_manual_ai_bundle.py tests/test_learning_view.py -q -p no:cacheprovider
```

判据：**失败的名字有没有变**，不看 failed 是不是 0。本机长期存在的环境性失败项见
`docs/status.md` 与 `.workbuddy-ai/memory/MEMORY.md`（`test_student_ui.py` 那条
`spawn ENOENT`，与本次无关）。

### 6.2 前端审计

```bash
"C:/Users/Rafae/.workbuddy-ai/binaries/node/versions/22.22.2-6/node.exe" scripts/ui_audit.js
"C:/Users/Rafae/.workbuddy-ai/binaries/node/versions/22.22.2-6/node.exe" scripts/ui_render_check.js
```

- `ui_audit.js`：check 数**只增不减**（当前基线 627）。新增的检查必须能跑完
  （脚本自己崩 = 前端门禁全失效）。
- `ui_render_check.js`：当前 312/314，有 **2 条继承失败**（`#/students` 与
  `#/students/stu-ana` 的路由重定向，见
  `reports/knowledge-map-manual-ai-acceptance.md`）。判据是**"仍是同样那 2 条"**，
  不是"必须 314/314"。

### 6.3 新增的 `ui_audit.js` 检查（补上 §2.6 发现 B 的空白）

新增一个检查块，**不放进** `PAGES` 的三语循环（避免污染全局 CJK 断言）：

```
// 构造一份最小 fixture bundle（全 ASCII！）
const MANUAL_BUNDLE_FIXTURE = {
  bundle_id: 'mab-fixture00000000',
  protocol_version: 'manual-ai/v1',
  prompt_version: 'ai-prompts-v1:extract-v3',
  material: { filename: 'FIXTURE-material.pdf' },
  requests: [{ request_id: 'req-fixture00000000', chunk_id: 'chunk-fixture0000',
               chunk_index: 0, page: 1, char_start: 0, char_end: 10,
               prompt: 'FIXTURE PROMPT', evidence_scope: {} }],
  limits: { max_response_chars: 20000, max_requests: 200 },
  chunking: { chunk_count: 1, material_chunk_count: 1, truncated: false, request_limit: 200 },
  notes: ['FIXTURE note'],
  instructions_markdown: 'FIXTURE instructions',
};
```

⚠️ **fixture 必须全 ASCII**：`ui_audit.js:2069-2083` 那条"es/ca 渲染结果里不得出现
CJK"的断言会覆盖面板渲染出来的内容。夹具里放中文（比如 `instructions_markdown`）
会让这条断言**误报**。面板自己的文案（`t()` 出来的）不在此列 —— 那正是要检查的。

要钉住的检查（建议 8–10 条）：

1. 面板里存在 `type="file"` 的输入，且 `accept` 含 `json`；
2. **不存在** `id="manual-ai-responses"` 的粘贴框（若 §8-D1 决定删除）；
3. 三语渲染面板后，es/ca 的**界面文案**无 CJK（复用 `firstCjk`）；
4. `manualNormalizeResponseText` 剥 BOM：`'\uFEFF{"a":1}'` → `'{"a":1}'`；
5. 剥一层围栏：输入 = 三反引号 + `json` 换行 + `{"a":1}` 换行 + 三反引号 → 输出 `{"a":1}`；
6. **不**多剥：双层围栏只剥一层（防过度容错）；
7. `manualFileProblem` 对裸数组返回 `manual.file.bare`；
8. 对"任务包"返回 `manual.fileIsBundle`；
9. 对 `bundle_id` 不匹配返回 `manual.fileBundleMismatch`；
10. 对合法 payload 返回 `''`。

**变异测试（必须做，作为"检查真的有效"的证据）**：把文件输入换回 textarea →
第 1 条必须红；把 `manualNormalizeResponseText` 里的 BOM 剥离删掉 → 第 4 条必须红。
**报数时把变异前后两次的 check 数与失败名一并写出来。**

### 6.4 真实浏览器手工验证（改动完必须做一次）

```bash
python -m src.application.cli serve      # 或项目既有的启动入口
```

1. 打开 `#/materials`，确认按钮文案是「本地 AI 分析」，且**不再有**「手动 AI」。
2. 点开面板 → 复制任务包 → 检查复制到的 JSON 里
   `instructions_markdown` 确实要求"保存为 .json 文件"，且骨架里**预填了全部 request_id**。
3. 按骨架手工造一个 `.json`（1–2 条响应即可）→ 上传 → 确认逐请求状态表照常出现。
4. 故意上传任务包本身 → 确认提示是「这是任务包本身，不是模型回复」而不是裸错误码。
5. 用记事本另存一份带 BOM 的同内容文件 → 上传 → 确认**能导入**。
6. 关掉自己打开的所有窗口（浏览器标签、服务进程）。

---

## 7. 验收标准（逐条可复核）

- [ ] `grep -rn "手动 AI\|手动ai" src/web/` **零命中**（`docs/`、`reports/` 的历史记录除外）。
- [ ] 三语 `manual.*` 键集合完全相同（`ui_audit.js` key parity 绿）。
- [ ] `manual.open` / `manual.title` 三语均为「本地 AI 分析」/ `Análisis IA local` / `Anàlisi IA local`。
- [ ] 面板里没有 `#manual-ai-responses` 文本框（若按 D1 默认执行），有 `#manual-ai-file`。
- [ ] E1–E15 全部有对应分支；E13 的"粘贴 vs 上传"结果逐字段一致。
- [ ] `instructions_markdown` 里：含 `bundle_id`、`protocol_version`、`request_id`，
      不含 `classroom-data`，且**明确要求保存为 `.json` 文件**。
- [ ] 骨架里预填的 `request_id` 条数 == `len(bundle.requests)`。
- [ ] `pytest tests/test_manual_ai_bundle.py tests/test_learning_view.py` 无**新增**失败。
- [ ] `node scripts/ui_audit.js` 跑完且 check 数 > 627。
- [ ] `node scripts/ui_render_check.js` 仍是那 2 条继承失败。
- [ ] 变异测试：撤掉文件输入 → 新检查红；撤掉 BOM 剥离 → 新检查红。
- [ ] 所有改动文件行尾为 **LF**（`.gitattributes` 规定 `*.js`/`*.py`/`*.md` 用 LF）。
      判据用字节级：`python -c "from pathlib import Path; print(Path(f).read_bytes().count(b'\r\n'))"`。
      **不要**用 `grep -c $'\r'`（Git Bash 里会误判）。
- [ ] `git diff --cached --stat` 里**只有自己的文件**（见 §10.1）。

---

## 8. 决策点（需凌总拍板；未拍板则按「默认」执行）

| # | 问题 | 选项 | **默认（未答复时照此执行）** |
|---|---|---|---|
| D1 | 粘贴框留还是删？ | (a) 删掉，只留上传 (b) 保留为 `<details>` 折叠备选 | **(b) 保留折叠**。理由：粘贴路径是已验收的 53/53 E2E 覆盖的路径，删掉会让"本地模型只给文本"的场景无路可走；成本约 5 行。若凌总要求只留上传，则删 `#manual-ai-responses` + `manual.emptyPaste` + `manual.responsesPlaceholder`（三语同删） |
| D2 | 选完文件是否自动导入？ | (a) 自动 (b) 再点一次「导入响应」 | **(b) 再点一次**。理由见 §3.2 |
| D3 | 是否加"下载响应骨架文件"按钮？ | (a) 加 (b) 不加 | **(b) 不加**（P2 候选）。§3.3 已经把骨架放进 `instructions_markdown`，够用；额外加按钮属于需求外功能 |
| D4 | `provider` 串要不要跟着改？ | (a) 改 (b) 不改 | **(b) 不改**。理由见 §3.1（它还是模式判定依据） |
| D5 | 上传是否支持**多个** JSON 文件合并？ | (a) 支持 (b) 单文件 | **(b) 单文件**。模型上下文放不下时用户会分批发，多文件合并需要"合并预览 + 去重"一整套 UI，属于需求外功能；单文件 + 逐请求状态已能表达"哪几条缺响应" |

---

## 9. 不在本次范围（明确写出，防止实施者自行发挥）

1. **不动知识地图**。用户原话：「目前知识地图的生成和人工材料分析部分做得都还可以」。
2. **不把「AI 分析」也改名**（比如改成「云端 AI 分析」）。用户只要求改「手动 AI」。
   虽然两个按钮并排时语义更清楚，但那是需求外改动。
3. **不改协议版本、端点路径、provider 串、模块名、i18n key 名、`data-action` 名**（§3.1 禁区表）。
4. **不改 `docs/status.md` 与 `reports/` 里的历史记录**。它们是当时的实测快照，
   改了就变成伪造历史。新记录**追加**在 `status.md` 末尾。
5. **不改工作区里那份未提交的 `materials.js` 改动**（见 §10.1）。
6. **不动 `MAX_BUNDLE_BYTES`**（§2.6 发现 A）。要不要把它接到
   `parse_response_payload` 里是**另一个任务**，本次只把它写进文档。
7. **不动 `src/api/server.py:389` 的错误 detail 回传策略**（§3.2）。
   靠客户端预检解决，不改后端契约。

---

## 10. 风险与坑

### 10.1 工作区已有一份**不是本任务**的未提交改动（实测）

```
$ git status --short
 M src/web/views/materials.js

$ git diff --stat
 src/web/views/materials.js | 11 ++++++-----
 1 file changed, 6 insertions(+), 5 deletions(-)
```

内容是纯 UI 样式/注释调整（`.tiny pill` → `.pill` + `style="font-size:0.85em"`、
给 `aiAutoLine` 的计数加 pill、补一条 design token 注释），**与本任务无关**。

⇒ 实施时：

- **绝不 `git add -A` / `git add .`**，只 `git add <自己改的文件>`；
- 提交前用 `git diff --cached --stat` 核对暂存区；
- **不要回滚它**（那会抹掉别人的在制品）。

### 10.2 别的坑

| 坑 | 说明 |
|---|---|
| 行尾 | `.gitattributes`：`*.py/*.js/*.css/*.html/*.json/*.md` = LF。本机 `core.autocrlf=true`，`git status` 干净也可能工作区是 CRLF；**只有真的多出 `\r\n` 的那个文件要修** |
| `Write` 大文件 | 改完大文件后做结构复核（标题集合对比 / 围栏配对 / 逐行看 `git diff -U0` 的删除行）。本次文件都不大，但 `i18n.js` 有三张大表，改错位置会静默错位 |
| 沙箱没有 `navigator` / `FileReader` | `ui_audit.js` 的 vm 沙箱（`makeSandbox`，`:1784-1870`）只提供 `window/document/localStorage/fetch/console/setTimeout/clearTimeout/URLSearchParams/FormData`。新代码必须 `typeof X !== 'undefined'` 守卫，否则审计**自己崩** |
| 沙箱 `querySelector()` 返回 `null` | `ui_audit.js:1763`。接线函数必须容错（§3.2） |
| 面板重绘 | `renderManualAiPanel` 每次重写 `innerHTML`（`:641`）。任何选择状态必须存 `state`，不能存 DOM |
| 夹具里放中文 | 会触发 es/ca 的 CJK 断言误报（§6.3） |
| `err.detail` 拿不到 | `server.py:389` 只在 debug 下回传 detail。别指望 `protocol_error` |
| `manual.sourceNote` 的措辞 | 它渲染在导入结果里，是"如实标注来源"的一部分。改文案可以，但**不能让它看起来像真实 API 调用** |

---

## 11. 实施顺序（建议）

| 步 | 做什么 | 验证 |
|---|---|---|
| 1 | 改 `i18n.js` 三语（6 改 + 9 增） | `node scripts/ui_audit.js` key parity 绿 |
| 2 | 改 `manual.py::render_instructions_markdown`（标题 + 怎么用 + 完整骨架） | `pytest tests/test_manual_ai_bundle.py -q`；手工打印一份 markdown 读一遍 |
| 3 | 追加 `tests/test_manual_ai_bundle.py` 的断言 | 同上，新断言绿 |
| 4 | 改 `materials.js`：新增 3 个函数 + 改面板 + 改 `actionManualAiImport` | 手工在浏览器跑 E1–E15 |
| 5 | 加 `ui_audit.js` 检查块 + 夹具 | `node scripts/ui_audit.js`；check 数上升 |
| 6 | 变异测试（撤文件输入 / 撤 BOM 剥离） | 两次都必须红 |
| 7 | 在 `docs/status.md` **末尾追加**一条 TASK-109 记录（格式照既有条目：依据 / 改动 / 验收 / 规模 / 风险） | — |
| 8 | `git add <自己的文件>` → 提交（**不 push**，本机无 GitHub 凭据） | `git diff --cached --stat` 只含自己的文件 |

---

## 12. 参考

- `src/application/ai/manual.py` —— 协议本体（本文 §2.3 / §3.3 的唯一依据）
- `reports/knowledge-map-manual-ai-acceptance.md` —— 手动 AI 的 53/53 端到端验收与边界
- `docs/task-82-optimization-plan.md` —— 该项目 AI/知识地图的总体优化计划（本次与其无关）
- `docs/status.md:8848` 起 —— 手动 AI 的落地记录（历史，不改）
