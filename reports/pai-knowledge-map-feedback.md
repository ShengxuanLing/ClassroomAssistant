# 知识地图效果反馈报告 —— Programació d'Aplicacions a Internet

- 课程：`Programació d'Aplicacions a Internet`（`course-bd4f98777c97a0d2`，声明语言 Catalan）
- 材料：`2 HTML.pdf`（29 页）、`CSS.pdf`（33 页）、`Introducció al JavaScript.pdf`（22 页）
- 日期：2026-10-12　版本：课堂助手 1.0.1
- 结论一句话：**三份课件已全部整合进课程并跑通真实 AI 分析；知识地图 380 个知识点全部有证据链、覆盖度 100%、
  真实浏览器渲染通过。发现 3 个工程缺陷（其中 1 个直接拖垮分析成功率）+ 若干数据质量问题，逐条列在 §6。**

---

## 1. 执行摘要（可复核的数字）

| 项目 | 实测值 |
|---|---|
| 材料注册 | 3 份（内容寻址 ID，逐字保留原始文件名） |
| 证据（Evidence） | **84** 条（HTML 29 + CSS 33 + JS 22，每页 1 条） |
| 知识点（KP） | **380** 个 = 329 个 AI 生成（`aikp-*`）+ 51 个确定性装配（`kp-*`） |
| 引用总数 | **387** 处（KP→证据） |
| 断链引用 | **0**（`unresolved_evidence_ids == []`） |
| 概念覆盖度 | **61 / 61 = 100%**（对照人工概念清单，见 §4） |
| 结构完整性断言 | **10 / 10 PASS**（见 §5） |
| 真实浏览器 | **380 行 DOM = 380 个 API KP**；3 个材料簇；无裸 key、无 undefined（见 §7） |
| 关系边（KP↔KP） | **0**（`relation_count == 0`，见 §6 D7） |
| 冲突 | 15 条课程级冲突（11 条引用了页脚样板文字）；55 个 KP 被标 `conflicted` |
| 中文解释缓存 | 43 / 329（按需翻译模型，见 §6 D14） |

产物文件：

- `reports/pai-knowledge-map-feedback.md`（本报告）
- `reports/km-shots/pai-km-live.png`（真实 Edge headless 截图）
- `temp/km_map.json` / `km_knowledge.json` / `km_evidence_*.json` / `km_coverage.json` / `km_gaps.json` /
  `km_conflicts.json` / `km_validation_report.json` / `km_browser_check.json`（原始证据，可复核）

---

## 2. 执行过程实录

### 2.1 AI 分析用的是真实模型，不是本地 fixture

`.env` 中已配置真实网关（`CLASSROOM_AI_ENABLED=true`，`CLASSROOM_AI_BASE_URL=https://api.kilo.ai/api/gateway`，
`CLASSROOM_AI_MODEL=kilo-auto/efficient`）。启动时 `load_local_environment()` 把它读入进程环境，
bootstrap 判定为 `has_credentials=True` → 使用 `OpenAICompatibleAIProvider`（**real 模式**），
而非 `FakeAIProvider`。`/api/health` 的 `ai.mode` 亦为 `real`。

### 2.2 调用方式（走真实 HTTP，非直调内部方法）

私有端口起真实服务（真实 `classroom-data` 数据目录），全程 HTTP：

1. `POST /api/materials`（multipart，`course_id` + `language=Catalan`）→ 3 份材料注册
2. `POST /api/materials/{id}/analyze` → 摄取（pypdf 抽文 → 证据 → 知识装配）→ AI 语义分析 → 落库 KP
3. `GET /api/courses/{id}/knowledge-map` → 知识地图投影
4. `GET /api/knowledge`、`/api/materials/{id}/evidence`、`/api/coverage`、`/api/gaps`、`/api/conflicts` → 交叉核对

### 2.3 单份幻灯课件的真实成本（重要）

管线是**按证据分块**的（`chunking.py::chunks_for_evidence`），而 PDF 摄取**每页产出 1 条证据**，
于是 **1 页 = 1 个 chunk = 1 次 LLM 调用**：

| 材料 | 页数 = chunk 数 | 实测单次调用 | 仅分块阶段的墙钟时间 |
|---|---|---|---|
| 2 HTML.pdf | 29 | 43s（合成样本探针）/ 60–120s（真实页，含重试） | ≈ 21–50 分钟 |
| CSS.pdf | 33 | 同上 | ≈ 24–55 分钟 |
| Introducció al JavaScript.pdf | 22 | 同上 | ≈ 16–40 分钟 |

分析完成后还有 3 次收尾调用（合并 / 中文摘要 / 术语表），以及 **TASK-81 的逐知识点中文解释**
（每条 1 次调用，实测约 30–200s/条）。这解释了为什么一次 `analyze` 要跑几十分钟。

> 结论：**当前实现下，84 页幻灯课件的一次完整分析 ≈ 84 次分块调用 + 3 次收尾 + N 次逐点翻译。**

---

## 3. 知识地图生成结果

```
GET /api/courses/course-bd4f98777c97a0d2/knowledge-map   → HTTP 200
stats = {
  knowledge_point_count: 380, material_count: 3,
  evidence_count: 84,        evidence_ref_count: 387,
  topic_count: 0,            relation_count: 0,
  conflict_count: 15
}
```

按材料分组（`material_to_knowledge`，键全部是真实 `mat-*` 材料 ID，无证据 ID 泄漏）：

| 材料 | KP 总数 | AI 生成 | 确定性装配 |
|---|---|---|---|
| 2 HTML.pdf | 135 | 116 | 19 |
| CSS.pdf | 132 | 115 | 17 |
| Introducció al JavaScript.pdf | 113 | 98 | 15 |

质量分布：

- 重要度：high 195 / medium 148 / low 37
- 校验状态：`supported` 227 / `unverified` 98 / `conflicted` 55
- 复核状态：`confirmed` 274 / `pending` 106
- 置信度：HIGH 285 / MEDIUM 27 / LOW 17 / UNCERTAIN 51
- `needs_verification` 标记：64 个

---

## 4. 覆盖度评估：61 / 61 = 100%

方法：先把三份 PDF 逐页抽文（pypdf）并人工归纳出**概念清单**（HTML 19 项 / CSS 21 项 / JS 21 项），
再用概念专属短语在全部 380 个 KP 的「标题 + 正文 + 原文术语」里做匹配。

| 文档 | 覆盖 | 说明 |
|---|---|---|
| 2 HTML.pdf | **19 / 19** | 定义、非编程语言、文档结构、标签语法、浏览器、元素分组、注释、h1–h6、p、格式标签、a/href、列表、div/span、img、语义化标签、良好实践、VS Code、CodePen |
| CSS.pdf | **21 / 21** | 定义、内容/表现分离、规则格式、浏览器默认样式、内联/嵌入/外部三种引入、类型/后代/类/id/分组选择器、盒模型、div、height/width、display、float、clear、圆角、阴影、img/alt |
| Introducció al JavaScript.pdf | **21 / 21** | 定义、与 Java 的关系、script 集成、大小写敏感、注释、语句分隔、变量、命名规则、let、const、数据类型、引号、算术/字符串/逻辑/比较/条件/位运算符、优先级、类型转换、prompt/alert 等 |

命中示例（证明是精准命中而非关键词蒙对）：

- 「HTML 不是编程语言」← KP `HTML no és llenguatge de programació`
- 「圆角」← KP `Definició de cantonades amb CSS`
- 「id 选择器唯一性」← KP `Unicitat de l'atribut id`、`Selector d'ID en CSS`
- 「严格相等 ===」← KP `Operador d'igualtat estricta (===)`、`Recomanació d'ús d'operadors estrictes`

> 覆盖度结论：**无概念遗漏**。三份课件的核心知识点被完整提取，且每个 KP 都能追溯到具体页码。

---

## 5. 关联质量评估：结构 10/10 PASS

| 断言 | 结果 |
|---|---|
| `stats.material_count == 3` | PASS |
| `stats.knowledge_point_count == len(knowledge_points)` | PASS |
| `material_to_knowledge` 的键 ⊆ 真实材料 ID | PASS |
| `unresolved_evidence_ids == []` | PASS |
| 每个 KP 至少 1 条证据引用 | PASS（380/380） |
| `evidence_counts[kp] == len(evidence_refs)` | PASS |
| 每条证据引用都能在证据库中解析到 | PASS |
| 每个 KP 出现在它所引用的材料的映射里 | PASS |
| `stats.evidence_ref_count == Σ 引用数` | PASS（387） |
| `stats.evidence_count == 去重后独立证据数` | PASS（84） |

证据链抽查（随机取 HTML 材料 6 个 KP）：

| KP | 引用证据 | 证据正文开头 |
|---|---|---|
| `Nom de l'assignatura` | `doc-evidence-f4d6bb25…` | 「HTML Programació d'Aplicacions a Internet Grau en Gest…」(p1) |
| `Funció principal d'HTML` | `doc-evidence-6d90643e…` | 「HTML són les sigles de HyperText Markup Language…」(p3) |
| `Paràgraf p` | 对应页证据 | 「<p> defineix un paràgraf…」 |

→ **知识点 → 证据 → 原始材料 → 具体页码** 四级链路完整，无一处指向不存在的证据。

---

## 6. 发现的错误与遗漏

按严重程度分级。D1–D3 是**工程缺陷**（影响可用性），D4–D13 是**数据/内容质量问题**，
D14–D15 是**本次执行的状态说明**。

### D1 🔴 `CLASSROOM_AI_TIMEOUT_SECONDS` 配置未接线（直接影响分析成功率）

`.env` 配置 `CLASSROOM_AI_TIMEOUT_SECONDS=120`，但 AI 服务的两条路径都用**硬编码 60s 默认值**：

- `AIAnalysisService.analyze(..., timeout_seconds: int = 60)`
- `Workspace._translate_kp_once(..., timeout_seconds: int = 60)`

实测证据：`2 HTML.pdf` 首次分析 **29 个 chunk 失败 7 个**（超时类），加了 120s 下限后降到 6 个、
再加重试降级到 2 个。配置里写着 120s，实际按 60s 跑 —— 配置与行为不一致。

### D2 🔴 网关偶发「畸形响应」不重试

`OpenAICompatibleAIProvider._post_json` 对非 JSON / 缺 `choices[0].message.content` 的响应**直接报错不重试**
（设计注释写「重试无用」）。实测这类失败是**瞬时**的：同一页重发即成功（4 个大页探针里 1 个遇到畸形响应）。
代价很大：任一 chunk 失败 → 整份材料分析失败 → 全量重跑。

### D3 🟠 chunk 缓存不跨尝试（重试等于重跑全部）

`AIAnalysisService()` 每次调用新建，其 pipeline 的 `_chunk_cache` 随之清空。所以一次失败重试要
**重新调用全部 chunk**（29/33/22 次），而不是只补失败的那几个。这是"失败一次代价翻倍"的根因。

### D4 🟠 `analyze` 同步阻塞，前端 `await` 它

`src/web/views/materials.js:460` 是 `await api('.../analyze')`。对幻灯 PDF 这意味着**首次点击后
浏览器请求挂起 25–50 分钟**。服务端有 `analysis_statuses` 轮询兜底，但只在并发重复点击时生效。

### D5 🟠 同一数据目录不允许双进程写

实测：两个驱动进程同时对 `classroom-data` 跑分析 → 后启动者的摄取**瞬间失败**：
`StorageError: failed to begin transaction: database is locked`。没有单写者守卫或友好报错。

### D6 🟡 证据语言全部识别为 `Unknown`

三份材料都声明 `Catalan`，但 **84 条证据的 `language` 全是 `Unknown`**（文档摄取未做语言判定）。
连带影响：详情页按证据语言请求解释时会拿到 `400`（KM QA 报告 §18 已登记同源问题）。

### D7 🟡 概念关联没有落成图上的边（`relation_count == 0`）

AI 返回的 `relations` 是**自由文本术语**（如「Model de caixa CSS」），由 `validators.py:148` 拼进 KP 正文
（前缀「关联: 」），**不会**写成 `knowledge_relations` 里的 KP↔KP 边。所以知识地图的"关系层"是空的
（图例显示「暂无知识关系」）。用户要的"概念关联"目前只以正文内的文本列表存在。

### D8 🟡 冲突检测误报率高（15 条里 11 条引用页脚样板文字）

`integration.py` 的否定冲突判定：把证据拆句 → 否定句 vs 肯定句 → 要求重合词 ≥ 短句的 60%。
在幻灯课件上，每页都带「Marc Esteve i Garcia + 页码」样板文字，且相邻页标题前缀高度相似
（如「conversió entre tipus **explícita**」vs「**implícita**」—— 本是两张不同主题的幻灯片），
于是被判成"矛盾"。**15 条冲突里至少 11 条是样板文字/标题相似导致的误报，真正的矛盾≈0**，
但它们让 55 个 KP 变成 `conflicted` 并推入人工复核队列。

### D9 🟡 确定性装配把幻灯片标题当知识点（含作者名）

51 个确定性 KP 的标题直接取幻灯片首行，包括：

- `Marc Esteve i Garcia`、`Marc Esteve i Garcia21`（**作者名 + 页码**，2 个）
- `El llenguatge HTML:`、`<!doctype html>`、`Eines d'edición online`、`Títols de nivell 1 i nivell 2 : <h1> i <h2>`

这些行是**版面元素**，不是知识点。它们还与 AI KP 混在同一材料簇里，且 `generation_mode` 为 `None`
（`knowledgeModeBadge()` 只认 `ai_summary` / `deterministic_fallback`），**界面上没有任何徽章区分**。

### D10 🟡 封面页元数据被当成知识点

AI 从封面页抽出了 `Nom de l'assignatura`、`Grau i curs`、`Campus i universitat`、`Professor responsable`、
`Autor del document`、`Primer Curs`、`Nombre del curso`、`Curso académico` 等 —— 这些是课件封面信息，
对复习没有价值。属**过度抽取**。

### D11 🟡 加泰语课程里混入英文标题

部分 KP 标题是英文：`Type selector`、`Descendant selector`、`Separation of content and presentation`、
`HTML document structure`、`Paragraph (p)`、`Anchor link (a)`、`Float property for div positioning`、
`Main attributes of <img> tag`… 提示词要求用 `Catalan` 写标题，模型**多数遵守但偶发漏切**。

### D12 🟡 287/380 个 KP 正文含中文小节标签（设计行为，但影响机器可读性）

`validators.py::_candidate_knowledge_content` **有意**把例子与关联拼进正文：

```python
if candidate.examples:  parts.append("例子: " + " / ".join(candidate.examples[:5]))
if candidate.relations: parts.append("关联: " + " / ".join(candidate.relations[:5]))
```

于是 287 个 KP 的 `content` 形如
`"CSS permet aplicar estils a les cantonades…\n例子: …\n关联: Model de caixa CSS"`。
中文标签是硬编码的，正文其余部分是加泰语/英语 → **单字段混排三语**。对中文用户可读，
但违背 AGENTS.md「受管材料的元数据由数据库承载、不依赖自由文本」的精神，也不利于下游结构化消费。

### D13 🟡 11 个 KP 与其证据的用词重合度 < 30%

主要是封面元数据类 KP（`Primer Curs` 重合 0.0、`Nombre del curso` 0.14、`Nom de l'assignatura` 0.14），
另有 `Etiquetes contenidores HTML`（0.09）、`Exemples de concatenació implícita`（0.08）。
这类 KP 的"证据支撑"最弱，属应被过滤的噪声。

### D14 ⚪ 中文解释与 AI 报告未跑完（本次执行状态，非缺陷）

- **中文解释**：43 / 329 已缓存（HTML 的前 43 条）。TASK-81 的自动翻译阶段按「每条 1 次调用」执行，
  按实测速率（30–200s/条）全部跑完需数小时。产品本身是**按需翻译**模型（详情页有翻译按钮），
  缺的条目不影响知识地图。
- **AI 报告 JSON**：三份材料的 `ai-reports/<material_id>.json` **均未落盘** —— 分析在"KP 已提交、
  翻译阶段进行中"时被中止，`_persist_ai_report` 未执行。KP 与证据已完整落库（知识地图的数据源），
  报告可由 KP + 证据重新推导（项目文档明确报告是衍生缓存），重跑 `analyze` 即补。

### D15 ⚪ 知识点类型字段未落库

AGENTS.md 要求的知识单元含「知识分类」（定义型/方法型/事实型/原理型/应用型），
但 380 个 KP 的 `metadata.kp_type` **全为 `None`**（重要性 `importance` 有值）。分类维度缺失。

---

## 7. 真实浏览器实测

环境：本机 Edge `--headless=new` + 独立 `--user-data-dir` + CDP（`websocket-client`），
真实服务（真实数据目录），访问 `#/courses/course-bd4f98777c97a0d2/knowledge-map`。

| # | 项目 | 结果 |
|---|---|---|
| 1 | API 知识点数 = DOM 行数 | **380 = 380** PASS |
| 2 | 统计条文案 | `380 知识点 / 380 已归类 / 0 未归类 / 84 条证据 / 387 处引用 / 3 份材料` PASS |
| 3 | 材料分组卡 | 3 张（HTML / CSS / JS）PASS |
| 4 | 裸 `km.*` key | 0 PASS |
| 5 | `undefined` / `null` 字样 | 0 / 0 PASS |
| 6 | 搜索「CSS」 | 收窄到 49 行 PASS |
| 7 | 清空搜索 | 恢复 380 行 PASS |
| 8 | 列表视图 | 380 行 PASS |
| 9 | 冲突横幅 | 「15 处冲突待处理 → 前往审核」PASS |
| 10 | 布局几何 | main 929×15697、统计条 y=287、首行 y=425，无横向溢出 PASS |

截图：`reports/km-shots/pai-km-live.png`（可见侧边课程列表、知识地图标题、冲突横幅、统计条、
搜索/筛选/三视图按钮、`Introducció al JavaScript.pdf` 材料簇及每个 KP 的「AI 总结 / 1 条证据」徽章）。

---

## 8. 优化建议（按收益排序）

### P0 — 直接影响能否跑完

1. **接线 `CLASSROOM_AI_TIMEOUT_SECONDS`**：把 `ai_config.timeout_seconds` 传进
   `AIAnalysisService.analyze` 与 `_translate_kp_once`（各一行）。配置写了 120s 就应按 120s 执行。
2. **对瞬时失败做有界重试**：在 provider 或服务层把「畸形响应 / 5xx / 超时」纳入重试
   （本次实测：加 4 次重试 + 1.5× 超时递级后，chunk 失败率从 24% 降到接近 0）。
3. **让 chunk 缓存跨尝试存活**（按 material_hash 持久化或复用同一 service 实例），
   使重试只补失败 chunk —— 当前一次失败要重跑全部 29/33/22 次调用。

### P1 — 成本与体验

4. **按预算打包证据再分块**：`chunks_for_evidence` 目前逐条证据分块。若把同一材料的小页合并到
   接近 `DEFAULT_CHUNK_CHARS`（2000 字符）再切，84 页可从 **84 次调用压到 ~15–20 次**（约 4–5× 降本）。
5. **把 `analyze` 改成异步作业**：立即返回 job 状态，前端复用已有的 `analysis_statuses` 轮询
   （轮询代码已在，只差服务端不阻塞）。
6. **单写者守卫**：对 `data_dir` 加进程锁，第二个进程启动即给出明确错误，而不是 `database is locked`。

### P2 — 数据质量

7. **过滤版面样板**：证据入库前剥离重复出现的作者行/页脚/页码；确定性装配跳过空标题与纯标签标题
   （可直接消掉 D9 的 `Marc Esteve i Garcia` 类 KP）。
8. **提示词 + 后校验排除封面元数据**（D10），并要求标题语言必须等于声明语言，否则重问（D11）。
9. **把 `examples` / `relations` 移出 `content`**，改为结构化字段（D12）；同时把候选 relations 解析到
   已存在的 KP id 并写入 `knowledge_relations`，让知识地图的**关系层真正有边**（D7）。
10. **冲突检测加样板词黑名单 + 先做近似重复合并**（D8）：要求重合词中至少 N 个非样板实词。
11. **证据语言**：文档摄取时按材料声明语言或简单启发式赋值（D6），消除详情页 `Unknown` → 400。

### P3 — 呈现

12. 知识地图对确定性 KP 也显示来源徽章（`generation_mode` 为 `None` 时按「版面提取」标注），
    并提供「仅看 AI 知识点」筛选，避免 51 个版面条目稀释信号（D9）。
13. 冲突横幅补一句「疑似样板文字误报，可批量忽略」，降低 55 个 `conflicted` 带来的人工负担（D8）。

---

## 9. 未完成 / 未验证事项（如实登记）

| # | 事项 | 状态 |
|---|---|---|
| 1 | 中文解释全量缓存 | 43 / 329（按需翻译模型，缺的条目在详情页可手动翻译） |
| 2 | 三份材料的 `ai-reports/*.json` | 未落盘（分析中止于翻译阶段）；重跑 `analyze` 即补，KP/证据不受影响 |
| 3 | 关系边、主题（topics）、知识点类型 | 结构性缺失，见 D7 / D15 |
| 4 | 全量测试套件 | **未跑**（按项目约定只跑受影响的定向测试；本次未改产品代码，仅用数据） |
| 5 | es/ca 译文母语审校 | 未做（KM QA 报告已登记的遗留项） |

---

## 10. 复核方法（别人怎么验）

```bash
# 1) 拉取知识地图与交叉视图（只读，私有端口）
"C:/Users/Rafae/AppData/Local/Microsoft/WindowsApps/python.exe" temp/km_final_fetch.py
# 2) 结构 + 覆盖度 + 质量校验
"C:/Users/Rafae/AppData/Local/Microsoft/WindowsApps/python.exe" temp/km_validate.py
# 3) 真实浏览器渲染校验（Edge headless + CDP）
"C:/Users/Rafae/AppData/Local/Microsoft/WindowsApps/python.exe" temp/km_browser_check.py
# 4) 直接查库核对（KP/证据/材料）
sqlite3 classroom-data/database/classroom.sqlite \
  "SELECT COUNT(*) FROM knowledge_points WHERE course_id='course-bd4f98777c97a0d2';"   -- 380
```

> 说明：本次未修改任何产品代码。所有断言基于真实 HTTP 响应、真实 SQLite 数据与真实浏览器 DOM；
> 数字均可由上述脚本复现。
