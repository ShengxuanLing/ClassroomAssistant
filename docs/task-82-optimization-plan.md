# 知识地图与 AI 分析 —— 修复与优化任务计划书（TASK-82 起）

- 版本：v1（2026-10-12）
- 依据：`reports/pai-knowledge-map-feedback.md`（真实课件端到端实测）、
  `docs/knowledge-map-product-spec.md`（M2）、`docs/knowledge-map-design-system.md`（M3）、
  `docs/task-76-ai-understanding-report.md`、`docs/task-77-report.md`、
  以及本计划书 §2 的 8 项外部调研
- 状态：**待评审**（实施前需先与仓库里 Codex 的未提交在制品对齐，见 §9.1）

> **本计划书的定位**：不是"再写一遍知识地图"，而是**把已经跑通的东西修正确、跑便宜、跑得让人愿意用**。
> 知识地图的骨架（后端只读投影 + 零构建前端 + 三视图 + 真实浏览器验证）已经成立；
> 本计划处理的是它**周围**的四类问题：接线缺陷、成本与等待、数据质量、人机交互。

---

## 0. 摘要（给只想读一页的人）

三份真实课件（84 页幻灯 PDF）跑完一次完整 AI 分析，得到 380 个知识点、84 条证据、387 处引用、
覆盖度 100%、结构断言 10/10 通过。**功能是通的，但代价与噪声都很大**：

| 事实 | 现状 | 本计划的目标 |
|---|---|---|
| 一次分析的 LLM 调用数 | **84 次**（1 页 = 1 次） | **≈ 25 次**（按预算打包；实测三份文本量：8.9–11.2K 字符 / 份） |
| 一次分析墙钟时间 | **25–50 分钟**（同步阻塞 HTTP） | 异步 + 可离开页面 + 有 ETA |
| chunk 失败率 | **24%**（7/29）→ 加驱动层重试才降到 ~0 | **< 1%**（产品层重试 + 缓存续跑） |
| 冲突条数 / 真冲突 | 15 条，其中 **11 条是页脚样板误报** | 误报率 < 20%，只报真矛盾 |
| 知识点里的版面条目 | 51 个确定性 KP 含作者名/页码 | **0 个**（样板剥离 + 质量闸门） |
| 中文解释覆盖 | **43 / 329** | 100%（异步补齐 + 可中断续跑） |
| 关系边（概念关联） | **0 条** | 真实边（归一化后写入 `knowledge_relations`） |
| 证据语言标注 | 84 条全 `Unknown` | 与材料声明一致（`ca`） |

---

## 0.1 实施进度（2026-10-12 晚，滚动更新）

| 任务 | 状态 | 证据 |
|---|---|---|
| **TASK-82** 接线 AI 超时配置 | ✅ **已由并行协作者实现** | `ai/service.py::analyze(timeout_seconds: Optional[int] = None)` 与 `workspace.py::_translate_kp_once` 均改为读 `load_ai_config().timeout_seconds`，注释里直接引用 TASK-82 |
| **TASK-93** 关系边落地（投影部分） | ✅ **已实现** | 新模块 `src/knowledge_graph.py`（纯函数、确定性、只读）+ `workspace.knowledge_map` 返回 `graph` 字段；测试 `tests/test_knowledge_graph.py`（19 条，含"概念节点绝不进知识点计数"红线）；实测 PAI 380 点 / 255 边 / 11 概念，Gestió 61 / 23 / 4 |
| **TASK-93** 写入 `knowledge_relations` 表 | ⏳ 未做 | 目前是**只读投影**（不改库、不迁移）。要让关系成为领域事实、被复习路径消费，仍需这一步 |
| **TASK-95** 知识地图的图谱视图 | ✅ **已实现（产品内）** | 新文件 `src/web/views/knowledge-graph.js` + 图谱为默认视图 + 三语 key + 样式；真实浏览器 10/10 通过（canvas 真有像素 3.7%、391 节点布局、边数与 API 一致、概念不计数、列表/探索视图未回归） |
| TASK-83…94、96…104 | ⏳ 未做 | 见下文各任务 |

> 本次实施**未改任何既有语义**：`knowledge-map` 的旧字段一个没动，只是**新增** `graph`；
> 旧视图（探索/列表/材料）原样保留，图谱只是**新增的第 4 个视图并成为默认**。

---

## 1. 现状与问题总览

### 1.1 数据现状（2026-10-12 实测，课程 `course-bd4f98777c97a0d2`）

```
knowledge_points  380   (AI 329 / 确定性装配 51)
evidence           84   (HTML 29 / CSS 33 / JS 22，每页 1 条)
evidence_refs     387   (断链 0)
conflicts          15   (其中 11 条引用页脚样板文字)
relations           0
kp-translations    43 / 329
```

### 1.2 问题清单（编号沿用反馈报告 D1–D15）

**A 类 · 工程接线缺陷**（本项目头号 bug 家族：「能力早就实现好了，装配层从没接上」）

| # | 问题 | 位置 | 实测证据 |
|---|---|---|---|
| D1 | `CLASSROOM_AI_TIMEOUT_SECONDS` 未接线，AI 服务路径硬编码 60s | `ai/service.py::analyze(timeout_seconds=60)`、`application/workspace.py::_translate_kp_once(timeout_seconds=60)` | `.env` 写 120s；29 chunk 失败 7 个 |
| D2 | 网关偶发畸形响应不重试（设计如此，但实测是瞬时的） | `ai/provider.py::_post_json` 的 `except (ValueError, KeyError, ...)` 分支 | 4 个大页探针 1 个遇到；重发即成功 |
| D3 | chunk 缓存不跨尝试（`AIAnalysisService()` 每次新建） | `ai/service.py:90`、`application/workspace.py:2534` | 失败一次 = 重跑全部 29/33/22 次调用 |
| D5 | 同一 `data_dir` 双进程写 → `database is locked` | `persistence/database.py`（**已有** `busy_timeout=5000`，缺单写者守卫） | 并行两驱动时第二个摄取 5.6s 后失败（≈5s 超时耗尽） |

**B 类 · 成本与等待**

| # | 问题 | 实测 |
|---|---|---|
| D4 | `analyze` 同步阻塞，前端 `await` 它 | `src/web/views/materials.js:460`；单份 PDF 挂起 25–50 分钟 |
| — | **按页分块**：1 页 = 1 chunk = 1 次调用 | `ai/chunking.py::chunks_for_evidence` 逐条证据切块；84 页 = 84 次调用 |
| D14 | 中文解释 / AI 报告未跑完即中断 | 43/329 翻译；3 份材料 `ai-reports/*.json` 均未落盘 |

**C 类 · 数据质量**

| # | 问题 | 实测 |
|---|---|---|
| D6 | 证据语言全 `Unknown`（材料声明 Catalan） | 84/84 |
| D8 | 冲突检测误报（样板文字 + 标题前缀相似） | 15 条里 11 条引页脚；55 个 KP 被标 `conflicted` |
| D9 | 确定性装配把幻灯片标题当 KP（含作者名/页码） | `Marc Esteve i Garcia`、`Marc Esteve i Garcia21` 等 51 个 |
| D10 | 封面页元数据被当 KP | `Nom de l'assignatura`、`Professor responsable`、`Primer Curs`… |
| D11 | 加泰语课程混入英文标题 | `Type selector`、`HTML document structure`… |
| D12 | 287/380 KP 正文含硬编码中文标签「例子:/关联:」 | `ai/validators.py:148-149`（设计行为，但字段混排三语） |
| D13 | 11 个 KP 与证据用词重合 < 30% | 多为封面元数据类 |

**D 类 · 结构性缺失**

| # | 问题 | 实测 |
|---|---|---|
| D7 | 概念关联没有落成图上的边 | `relation_count == 0`；AI 的 relations 只拼进正文 |
| D15 | 知识点类型（定义型/方法型…）未落库 | `metadata.kp_type` 全 `None` |

### 1.3 产品级发现：叫「知识地图」的东西不是地图（2026-10-12 用户实测反馈）

用户打开知识地图后反馈：**「你管这玩意叫地图么？」** —— 他看到的是**一份按材料分组的标题列表**。
这不是渲染 bug，是**产品定义与数据现实的脱节**：

| 事实 | 证据 |
|---|---|
| 后端投影**没有关系层** | `GET /courses/{id}/knowledge-map` → `relations: []`、`topics: []`、`knowledge_memberships: []`；`knowledge_relations` 表 0 行 |
| 前端忠实实现了 M2 的"探索视图优先"妥协 | `docs/knowledge-map-product-spec.md` §1 写明"从扁平知识点起步，等数据演化再加关系" —— 于是只能列标题 |
| **但关系数据其实存在**：AI 抽出的关联术语被塞进了 KP 正文 | 250/380 个 KP 带 `关联:` 行；382 条关联实例、228 个去重术语 |
| 归一化后**可成边** | 117/228 术语能精确命中已有 KP 标题；补齐模糊匹配后 **254 条边**（PAI）、23 条（Gestió） |
| 没人做那一步「术语 → 边」的归一化 | 缺 EDC 的 Canonicalize 环节（§2.5）；AI 的 `relations` 从未写入 `knowledge_relations` |

**已交付的验证物**（同一份真实数据，不动产品代码）：

- `reports/km-map-pai-graph.html` —— PAI 课：380 知识点 / 254 条边 / 11 个未归类概念节点 / 250 个有关联 / 130 个孤立
- `reports/km-map-gestio-graph.html` —— Gestió 课（用户截图那门）：61 / 23 / 4 / 27 / 34
- 截图 `reports/km-shots/poc-map-pai.png`、`poc-map-gestio.png`

PoC 用**真实数据**（不是示意图）：三材料分簇、边=归一化后的真实关联、
**未匹配术语不画边**（列在右侧）；被 ≥2 个知识点引用且匹配不到的术语物化为
**「未归类概念」虚线节点**（原文明示，绝不冒充知识点）。

> **结论**：`知识地图` 这个名字**当前名不副实**。要么把关系层做出来（本计划 TASK-93 + TASK-95），
> 要么把功能改名为「知识点列表」。本计划选择前者。

### 1.4 已关闭的历史待办（M2 §7）

`docs/knowledge-map-product-spec.md` §7 的三项待办已由 2026-10-11/12 实测关闭：
`GET /courses/<id>/knowledge-map` 存在且可用（本次真实 HTTP 验证）；V2 残留已清理；
前端技术路线确定为「零构建 Vanilla JS 最小探索视图」。本计划从这一基线上继续。

---

## 2. 外部调研：成熟产品的可复用做法

> 每条都给出**出处**与**在本项目约束下的落地方式**。项目硬约束：单机、本地、单用户、
> Windows 优先、零构建前端、**禁止新增重型依赖**（无 React/Vite/Redis/Celery/Kafka/PostgreSQL/Docker）。
> 所以下面的落地方式一律是「用标准库 + SQLite + 手写 SVG/Canvas 复刻成熟模式」，而不是照搬它们的栈。

### 2.1 长任务的进度、恢复与"不要让人干等"

| 做法 | 出处 | 落地方式 |
|---|---|---|
| **Progress file**：每个会话结束写一份"做完了什么/还剩什么/卡在哪"的纯文本，下个会话读它恢复 | Anthropic《Effective harnesses for long-running agents》；agentpatterns.ai | `classroom-data/.run/jobs/<job_id>.json`：阶段、进度、失败 chunk 列表；进程重启后读它续跑 |
| **Feature list 作为目标契约**：每个可测单元标 `passing/failing`，用客观计数阻止"提前宣布完成" | 同上 | 分析的"完成"判据 = 所有 chunk 有终态 + 报告落盘；**不允许**把"跳过 AI"记成"完成"（项目已有 `AI_DISABLED` 语义，保持） |
| **Pre-completion checklist**：退出前强制逐条核对规格 | LangChain harness engineering | `analyze` 收尾前逐项断言（证据齐、KP 有证据、报告已落盘），缺一项不标 `COMPLETED` |
| **Loop detection**：同一文件被改 N 次就注入"换个方法" | 同上 | 同一 chunk 连续失败 ≥ 3 次 → 记 `chunk_stuck`，跳过并**如实报告**（不静默重试到天荒地老） |
| **可取消 / 可离开 / 有 ETA** | aiuxdesign.guide 的 Agent Status Monitoring | 异步 job + 任务中心（§4 TASK-86/99） |

### 2.2 分块与文档预处理（成本与质量的地基）

**分块策略基准**（Ertas AI 2026 对照实验，企业文档语料，Recall@5）：

| 策略 | 准确率 | 备注 |
|---|---|---|
| 固定大小 512 token | 71.3% | 最常见默认值，**最差** |
| 递归字符切分 | 78.6% | LangChain 默认 |
| 语义分块 | 83.2% | 索引耗时 4–5× |
| **文档感知分块** | **86.7%** | 结构化文档最优，且**最省 token** |

可直接引用的三条结论：

1. **"chunks smaller than 200 tokens lose context"** —— 本项目按页切出的 chunk 只有
   96–1068 字符（≈ 25–270 token），**多数低于 200 token 的底线**。这是"成本高 + 上下文碎"的同一根因。
2. **甜区 256–768 token**；>1000 token 会稀释相关性。
3. **给每个 chunk 附元数据（标题/章节/页码）可提升 8–12%**；混合策略（结构化用文档感知 + 非结构化用语义）可达 89–92%。

**文档预处理**（bthek1《Document Pre-processing for RAG》）：

- 样板剥离两条启发式：**出现在 >80% 页面的行 = 样板**；**PDF 固定位置重复文本 = 页眉页脚**。
- 质量闸门示例：`len(text.strip()) < 50` 丢弃；`alpha_ratio < 0.4` 视为乱码。
- 统一 Unicode 归一化（`NFKC`）—— 多语言文档必需。
- 元数据字段：`source / page / section / language / doc_type`。

→ 直接命中 D9/D10/D13（版面条目）与「按页分块」的成本问题。

### 2.3 溯源与引用 UX

| 做法 | 出处 | 落地方式 |
|---|---|---|
| **行内引用 + 悬停预览 + 点击跳源**（答案里的角标 → 浮层显示原文片段 → 点击定位到原文页） | NotebookLM、Perplexity | 知识点详情页：每条证据一个角标；hover 显示证据原文前 N 字；点击展开"证据 → 材料 → 页码"链（后端 `/api/knowledge/{id}/trace` 已有） |
| **源侧高亮**：跳到原文时把对应段落高亮 | NotebookLM | PDF 无文本层坐标时退化为"页号 + 片段"，**不假装能高亮**（Evidence-first） |
| **引用必须可验证**：每个论断都能点回来源 | 两家的共同底线 | 项目已有铁律；补一条 UI 断言：KP 详情页每条证据都可点且落到存在的材料 |

### 2.4 人机协同审核（HITL）

agentic-design.ai 的 HITL 模式给出的关键约束，逐条对应我们的审核队列：

- **按置信度阈值决定是否升级**（不要全量升级）。→ 已有 `auto/review/conflict` 三分类，保持；
  补"置信度阈值可调 + 阈值变更要重分类"。
- **人会成为瓶颈；队列增长快于审查能力** → 会造成积压或草率判定。→ 必须提供
  批量接受/拒绝、键盘驱动、按置信度分批。
- **"单页审阅 + 键盘快捷键 + 历史记录"** → 审核工作台（§4 TASK-97）。
- **指标**：升级率、人类决策质量、响应时间、每次决策成本。→ 落地成 `review_metrics` 只读投影。
- **反面清单**：不要把每个决策都升级；不要在监督足够时还用 HITL；不要让审阅者超载。

→ 直接命中 D8（55 个 `conflicted` 是误报，等于让用户做无用功）。

### 2.5 关系与实体归一（让"概念关联"真正成为边）

EDC 框架（arXiv 2404.03868《Extract, Define, Canonicalize》）：
LLM 抽出的三元组**不能直接入图**，必须经 **Canonicalize（归一化）** 步骤把同义/别名实体
与关系收敛到同一节点与同一关系类型，否则图会碎成一堆孤立别名。

→ 落地：AI 的 `relations` 是自由文本术语（`Model de caixa CSS`），需要
**归一化 → 匹配已有 KP → 写入 `knowledge_relations`**（TASK-93）。
匹配用"归一化字符串精确命中 → 标题相似度 → （可选）向量相似度阈值"三级回退，
**匹配不到的不造边**（Evidence-first）。

### 2.6 大图导航：Shneiderman 的可视化信息检索箴言

> **Overview first, zoom and filter, then details-on-demand.**（Shneiderman, 1996）
> 扩展任务：relate / history / extract。

→ 落地为知识地图的信息架构：默认给"总览"（课程统计 + 材料分簇），
筛选（来源/状态/生成方式），再逐级进入详情；补 relate（关系边）、
history（最近浏览）、extract（导出复习清单）。380 → 5000 个知识点时这是唯一不崩的路线。

### 2.7 双语阅读（面向中文母语者的加泰语/西语材料）

沉浸式翻译（Immersive Translate）的核心做法：**原文下方逐段显示译文**、
**术语库统一译名**、**可一键切换回纯原文**。

→ 落地：知识点详情页的"中文解释"从"一个隐藏按钮"升级为
**原文 / 中文对照（逐段）+ 术语表**；术语一致性来自已有的 glossary 阶段。
搜索需**重音不敏感**（`Càlcul` 应能被 `calcul` 命中）——这对加泰语是硬需求。

### 2.8 单机数据安全

SQLite WAL 的公认实践：**WAL 允许一写多读，但写入仍串行**；遇到 `SQLITE_BUSY` 必须
**设 `busy_timeout`**，并且**应用层要保证单写者**（多进程写同一库在单机场景应被显式拒绝，
而不是靠重试硬扛）。

→ 落地：`data_dir` 级文件锁（`msvcrt.locking` / 独占锁文件）+ `busy_timeout` +
明确错误码（TASK-85）。**绝不用"多进程并行分析"换速度**（本次实测已证明会失败）。

---

## 3. 目标与成功标准

### 3.1 产品目标（用户视角）

一个中文母语的 UAB 大二学生，拿到一门课的三份加泰语课件，应当能：

1. **一键导入**，然后**关掉页面去干别的**，回来时收到"分析完成（用时 X，花费 Y）"的通知；
2. 在**知识地图**上先看总览（多少知识点、来自哪几份材料、哪些待确认），再按来源/状态筛选；
3. 点开任一知识点，**看到它来自哪一页的哪一段**，并读到**原文 + 中文对照**；
4. 遇到真正矛盾的知识点时被**明确告知**，并且**一次点击就能批量处理**；
5. 全程**不看到任何内部标识、裸 key、`undefined` 或语言错乱**。

### 3.2 可测指标（before → after）

| 指标 | 现状 | 目标 | 测量方式 |
|---|---|---|---|
| 单份幻灯 PDF 的 LLM 调用数 | 29–33（1 页 1 次） | **≤ 12**（1200 字符预算；三份实测文本 8.9–11.2K 字符 → 约 8–10 个 chunk） | 驱动层计数 + 服务端请求日志 |
| 单份 PDF 的墙钟时间（同网关） | 25–50 min | ≤ 10 min | 端到端计时 |
| chunk 失败率（真实网关） | 24% → 0%（靠驱动层重试） | **产品层 < 1%** | 20 次重复分析统计 |
| `analyze` 请求阻塞时长 | 25–50 min | < 1 s（异步） | HTTP 响应时间 |
| 确定性 KP 中的版面条目 | 51 个（含 2 个作者名） | **0** | 断言：标题不含作者名/纯页码/空串 |
| 冲突误报率 | 73%（11/15） | < 20% | 人工标注 20 条对照 |
| 证据语言标注正确率 | 0%（全 Unknown） | 100% | 断言 evidence.language == material.language |
| 关系边数量 | 0 | > 0 且可溯源 | `relation_count` + 每条边可点回两侧 KP |
| 中文解释覆盖 | 43/329 | 100%（可中断续跑） | 缓存文件计数 |
| 首屏（380 KP） | 未测 | < 1.5 s | 浏览器实测 |
| UI 裸 key / undefined / 哈希泄漏 | 0（已达标，需守卫） | 保持 0 | `ui_audit.js` + 新增断言 |

### 3.3 非目标（明确不做）

- 不引入图数据库、向量数据库、任务队列中间件（Celery/Redis）、前端框架。
- 不做多用户/云同步/协作。
- 不做 OCR 视觉理解（本机缺 `rapidocr`；图片材料的语义分析不在本轮）。
- 不做"自动解决冲突"——冲突**永远**由人裁决（项目铁律）。
- 不改领域模型的核心语义（`Material → Evidence → KnowledgePoint → Validation/Review`）。

---

## 4. 任务分解

> **编号说明**：以下用 `TASK-82 … TASK-104` 占位。仓库里有并行未提交的在制品
> （见 §9.1），**开工前必须先 `git status` + `grep -n '^## ' docs/status.md | tail`
> 对齐真实编号**，避免与在飞任务撞号。纯缺陷修复也可用项目既有的
> `## 修复 — <一句话>` 区块格式。
>
> 每个任务都必须交付：实现 + **定向测试** + **变异验证**（证明新断言不是空跑）+
> `docs/status.md` 区块。**不跑全量回归**（用户 2026-10-10 明确要求）。

### 阶段 P0 · 正确性与可靠性（先做，否则后面都在沙子上盖楼）

#### TASK-82 — 接线 AI 超时配置，并让"生效配置"可见

- **依据**：D1（本项目头号 bug 家族：能力存在但未接线）
- **改动**
  - `src/application/ai/service.py::AIAnalysisService.analyze`：`timeout_seconds` 改为从
    `load_ai_config().timeout_seconds` 取值（保留显式参数覆盖）。
  - `src/application/workspace.py::_translate_kp_once` 与 `_kp_translation_stage`：同上。
  - `src/application/bootstrap.py`：把 `ai.timeout_seconds` 放进 `/api/health` 的 `data.ai`
    （与 `asr_mode` / `ocr_mode` 同一"自曝"原则）。
- **验收**
  - 断言：`ai_config.timeout_seconds = 120` 时，`analyze` 实际传下去的是 120（用假 provider 记录入参）。
  - 断言：`/api/health` 的 `data.ai.timeout_seconds == 120`。
  - **变异**：把硬编码 60 改回去 → 上述两条必须红。
- **规模** S ｜ **风险** 低

#### TASK-83 — 瞬时失败的有界重试与退避（产品层，不靠驱动脚本）

- **依据**：D2（畸形响应/5xx/超时不重试，实测瞬时）
- **改动**：`src/application/ai/provider.py`
  - 把「畸形响应」纳入可重试类（`MALFORMED`），退避 `0.5s → 1s → 2s`（与既有 429/5xx 同口径）。
  - 新增可配置上限 `CLASSROOM_AI_MAX_RETRIES` 已存在，复用。
  - **绝不**对 401/403 重试；**绝不**无限重试；每次重试记结构化日志（`event=ai_retry`）。
- **验收**
  - 假 provider 依次返回 `[畸形, 畸形, 正常]` → 第三次成功，且**只调用 3 次**。
  - 假 provider 永远畸形 → 抛 `AIRequestError`，**调用次数 == max_retries + 1**。
  - **变异**：去掉 malformed 分支 → 第一条红。
- **规模** S ｜ **风险** 低（注意别把 4xx 也重试了）

#### TASK-84 — chunk 结果持久化 / 断点续跑

- **依据**：D3（缓存不跨尝试，失败一次代价翻倍）
- **改动**
  - 新表 `ai_chunk_cache(material_hash, chunk_hash, model, prompt_version, payload, created_at)`，
    主键 = 前四元组（**内容寻址，天然幂等**）。
  - `ai/pipeline.py::analyze_chunk` 先查持久化缓存再调用模型；成功后写入。
  - 提供 `Workspace.clear_ai_cache(course_id)`（供"强制重跑"）。
- **验收**
  - 同一材料分析两次 → 第二次**零模型调用**（假 provider 计数 == 0）。
  - 中途 `kill -9` 后重跑 → 只补未完成的 chunk（计数 == 剩余数）。
  - **变异**：让缓存键漏掉 `model` → 换模型后仍命中（必须红）。
- **规模** M ｜ **风险** 中（新表需迁移 `m005`；遵循"规范列 + payload"约定）

#### TASK-85 — 单写者守卫 + 可操作的"数据目录被占用"错误

- **依据**：D5（双进程 → `database is locked`；`busy_timeout=5000` **已存在**，
  实测第二个进程等满 5s 后失败 —— 问题不在超时，而在**没有守卫、也没有可读的报错**）
- **改动**
  - 新增 `data_dir` 级独占锁（`classroom-data/.run/workspace.lock`，`msvcrt.locking` / `fcntl`）；
    启动时获取失败 → 抛 `StorageError`，`detail` 带"另一个实例正在使用该数据目录"，
    `code` 复用既有结构化码（**不新增**第 9 个码）。
  - 长事务友好：分析类批写在**单个短事务**内提交（现在是摄取一个事务、KP 一个事务），
    减少持有写锁的时长；`busy_timeout` 保持可配（不擅自改大）。
  - `launcher` 启动失败时输出**可操作**提示（"先关掉另一个实例，或换 `--data-dir`"）。
- **验收**
  - 同一 `data_dir` 起两个 Workspace → 第二个抛 `StorageError`（**不是** `sqlite3.OperationalError`），
    且错误信息含可操作下一步。
  - 断言：锁在正常退出/异常退出后都能释放（幂等释放 + `try/finally`）。
  - **变异**：去掉锁 → 第二个进程能起来（必须红）。
- **规模** M ｜ **风险** 中（Windows 锁语义；释放必须幂等）

#### TASK-86 — 分析作业异步化：进度、ETA、取消、恢复

- **依据**：D4（同步阻塞 25–50 分钟）+ §2.1 长任务模式
- **改动**
  - 新表 `analysis_jobs(job_id, course_id, material_id, state, stage, done, total, started_at,
    finished_at, error_code, payload)`；`job_id` 确定性（`job-<sha16(course,material,attempt)>`）。
  - `POST /api/materials/{id}/analyze` **立即返回** `{job_id, state: "queued"}`；
    实际执行放到受控后台线程（**进程内单 worker 队列**，不引入 Celery）。
  - `GET /api/analysis-jobs/{job_id}`：进度（chunk 已完成/总数、当前阶段）、ETA（基于已用时长外推）、
    已失败 chunk 数、是否可取消。
  - `POST /api/analysis-jobs/{job_id}/cancel`：设取消标志；**在 chunk 边界检查**，
    已提交的 KP/证据**不回滚**（与既有"证据安全"语义一致）。
  - 进程重启后：`queued/processing` 的作业标 `interrupted`，可一键续跑（依赖 TASK-84 缓存）。
- **验收**
  - `analyze` 的 HTTP 响应 < 1s（假 provider 慢速版）。
  - 进度单调递增；`done/total` 与真实 chunk 数一致。
  - 取消后不再发起新模型调用（计数冻结），且已产出的 KP 仍在。
  - **变异**：把 worker 改成同步执行 → 响应时间断言红。
- **规模** L ｜ **风险** 高（并发、线程安全、状态机）—— **本阶段最大的一个任务**

#### TASK-87 — 阶段产物落盘：AI 报告与"部分完成"如实呈现

- **依据**：D14（3 份材料报告未落盘）
- **改动**
  - `_persist_ai_report` 提前到"KP 已提交"之后立即写，**翻译阶段单独记**（`kp_zh_auto` 可后补）。
  - 报告里新增 `partial: true/false` 与 `stages[]` 逐阶段状态（已有 `stages`，补 `partial`）。
  - 前端材料页：`COMPLETED` 但翻译未完成 → 显示"知识点已就绪，中文解释 X/Y（可后台补齐）"。
- **验收**
  - 模拟翻译阶段失败 → 报告仍落盘且 `partial == true`；KP 数不变。
  - 断言：不存在"KP 已提交但无报告"的状态。
- **规模** S ｜ **风险** 低

### 阶段 P1 · 分块与预处理（成本与质量的地基）

#### TASK-88 — 分块策略改造：按预算打包 + 页边界 + 元数据

- **依据**：§2.2（<200 token 丢上下文；256–768 token 甜区；元数据 +8–12%）
- **改动**：`src/application/ai/chunking.py::chunks_for_evidence`
  - 改为**累积同一材料的多条证据直到接近预算**（默认 1200 字符，可配），
    **优先在页边界断开**，仅当单页超预算才切页内。
  - 每个 chunk 记录 `page_start/page_end/evidence_ids`（元数据随 chunk 传递到提示词）。
  - 预算、重叠、上限全部走配置（`CLASSROOM_CHUNK_CHARS` 等），并进 health 自曝。
- **验收**
  - 三份 84 页输入 → chunk 总数 ≤ 30（现状 84）；单份 ≤ 12。
  - 断言：任何 chunk 的字符数 ≥ 200（除最后一页）且 ≤ 预算 × 1.2。
  - 断言：`chunk_to_evidence` 映射仍能把模型引用的 chunk 翻回真实证据（既有契约不破）。
  - **变异**：把预算改回 0（逐页）→ chunk 数断言红。
- **规模** M ｜ **风险** 中（会改变 AI 输出 → 需 TASK-88b 的迁移策略）

#### TASK-88b — 重新分析的迁移策略（版本化 + 差异预览 + 人工确认）

- **背景**：chunk 变了 → AI 输出变了 → 内容寻址的 `aikp-*` ID 变了。
  **不能**让新旧 KP 悄悄共存（会重复），也**不能**静默删掉用户的审核结果。
- **改动**
  - 提升 `PIPELINE_VERSION`；KP payload 记 `pipeline_version`（已有字段，确认必填）。
  - 新端点 `POST /api/courses/{id}/reanalyze?dry_run=true`：返回
    "将新增 N / 将更新 M / 将保留 K（人工已确认的永不自动删）"的**差异预览**。
  - `dry_run=false` 时执行；**人工确认过的 KP（`review_status=confirmed` 且人工来源）不删除**，
    仅挂新证据。
  - UI：材料页提供"用新版本重新分析"按钮 + 差异确认对话框。
- **验收**
  - dry-run 不写任何数据（断言：分析前后 `knowledge_points` 行数不变）。
  - 执行后：无重复标题（相似度 > 阈值者被合并）；人工确认项仍在。
  - **变异**：让 dry-run 真的写库 → 断言红。
- **规模** M ｜ **风险** 高（数据迁移）

#### TASK-89 — 版面样板剥离与质量闸门

- **依据**：D9/D10/D13 + §2.2（>80% 页面重复 = 样板；`min 50 chars`；`alpha_ratio ≥ 0.4`）
- **改动**
  - `src/document_input.py`（或证据装配层）：抽出每页文本后，做**跨页行频统计**，
    把出现在 >80% 页面的行（作者名、页眉页脚、页码、课程名样板）标为 `boilerplate`，
    从**证据正文**中剥离，但**保留在原文副本里**（原文不可改，见铁律 3）。
  - 质量闸门：剥离后 `len < 50` 或 `alpha_ratio < 0.4` 的页面证据标 `low_signal`，
    **不进 AI 分块**（但仍可在证据页查看）。
  - 归一化：`unicodedata.normalize("NFKC", ...)`（多语言文档）。
- **验收**
  - 对三份真实 PDF：剥离行数 > 0；`Marc Esteve i Garcia` 不再出现在任何证据正文。
  - 断言：原文副本逐字节未变（`sha256` 相同）。
  - 断言：`low_signal` 页面**不出现在** AI 分块输入里。
  - **变异**：关掉剥离 → 作者名重新出现在 KP 标题（红）。
- **规模** M ｜ **风险** 中（剥离过度会丢内容 → 阈值可配 + 抽样人工核对）

#### TASK-90 — 语言判定与字段语言标注

- **依据**：D6（84 条证据全 `Unknown`，连带详情页 400）
- **改动**
  - 文档证据：优先**继承材料声明语言**（上传时已给 `language=Catalan`）；
    若声明缺失，再用轻量启发式（停用词/重音字符集）判定 `ca` / `es`，判不出就 `Unknown`（不猜）。
  - `evidence.language` 落库；`language_code()` 归一化链保持（`Unknown → None`）。
- **验收**
  - 三份材料的 84 条证据 `language == "Catalan"`。
  - 详情页请求解释**不再 400**（`language_not_available` 消失）。
  - **变异**：把语言写死 `Unknown` → 断言红。
- **规模** S ｜ **风险** 低

### 阶段 P2 · 数据质量与关系

#### TASK-91 — 抽取约束：排除封面元数据、强制标题语言、去重阈值

- **依据**：D10/D11
- **改动**
  - `ai/prompts.py` 的抽取提示词新增：
    - **排除**课件封面/版权页元数据（课程名、学位、校区、教师姓名、学年）；
    - 标题必须与 `content_language` 一致（现在是"建议"，改成"必须"）；
    - 单页最多 N 个候选（防过度抽取）。
  - `ai/validators.py` 新增后校验：标题语言不符 → 该候选降级为 `review` 或丢弃（可配）；
    标题命中"元数据模式"（纯年份、`Grau en …`、人名 + 页码）→ 丢弃。
- **验收**
  - 对三份真实 PDF 重跑：`Nom de l'assignatura` / `Professor responsable` / `Primer Curs` 等**不再出现**。
  - 标题非加泰语的比例从现状降到 < 5%。
  - **变异**：关掉后校验 → 元数据 KP 回归（红）。
- **规模** M ｜ **风险** 中（提示词改动会影响所有材料 → 与 TASK-88b 的版本化一起做）

#### TASK-92 — 内容字段结构化：examples/relations 移出 content，kp_type 落库

- **依据**：D12/D15 + §2.5
- **改动**
  - `ai/validators.py::_candidate_knowledge_content`：`content` **只放正文**；
    `examples` / `relations` 进 KP payload 的**结构化字段**。
  - 中文标签（`例子:`/`关联:`）改为**语言中立的键**（结构化字段天然无标签）；
    若 UI 需要展示，由渲染层按界面语言加标签（符合"转义/文案是渲染层职责"）。
  - `metadata.kp_type` 落库（定义型/方法型/事实型/原理型/应用型，来自候选的 `type`）。
- **验收**
  - 断言：任何 KP 的 `content` **不含** CJK 标签（287 → 0）。
  - 断言：`metadata.kp_type ∈ {definition, method, fact, principle, application}`（或明确 `unknown`）。
  - 断言：`content` 逐字保留原文术语（不改写、不翻译）。
  - **变异**：把标签拼回 content → 第一条红。
- **规模** M ｜ **风险** 中（前端需同步改渲染；i18n 需加 `kp.examples` / `kp.relations` 三语 key）

#### TASK-93 — 关系边落地：归一化 → 匹配 → 写 `knowledge_relations`（把地图的**边**做出来）

- **依据**：D7 + §1.3（"知识地图不是地图"）+ §2.5（EDC 的 Canonicalize 步骤）
- **已实测的可行性与上限**（PoC：`temp/km_build_map.py`，真实数据，未动产品代码）

  | 课程 | 知识点 | 可成边 | 关联实例 | 有关联的点 | 孤立点 | 未匹配术语 |
  |---|---|---|---|---|---|---|
  | PAI | 380 | **254** | 382 | 250 | 130 | 61 |
  | Gestió | 61 | 23 | — | 27 | 34 | 40 |

- **改动**
  - 新模块 `src/knowledge_relations.py`（领域层，纯函数）：
    `canonicalize(term) -> key`（NFKC + 去重音 + 小写 + 去停用词 + 保守词干化）。
  - 应用层三级回退解析：`精确归一化命中标题 → 精确命中 original_terms → token Jaccard ≥ 0.6`；
    **解析不到就不造边**，如实记入 `unresolved_relations`（只读可见，**不编造**）。
  - **未匹配术语的物化**（PoC 已验证有效）：被 **≥2 个**知识点引用且解析不到的术语，
    物化为**「未归类概念」节点**（`kind=concept`，UI 用虚线 + 明确文案"原文明示，非知识点"），
    与引用它的 KP 连边。PAI 因此多出 **11 个概念节点 / 29 条概念边**，Gestió 多出 4 / 9。
    **红线**：概念节点必须与知识点在数据与视觉上都可区分，绝不允许混入 `knowledge_points`。
  - 写入既有 `knowledge_relations`（`RelationType.RELATED`，定义在
    `src/knowledge_structure.py:13`，**不**冒用 `prerequisite`）。
- **验收**
  - 对真实课程：`relation_count ≥ 200`（PAI）/ `≥ 20`（Gestió），且**每条边两侧节点都存在**（无悬空边）。
  - 断言：`knowledge_points` 的行数**不因概念节点增加**（概念不得进 KP 表）。
  - 断言：**没有**把 `RELATED` 写成 `prerequisite`（守卫，见 M2 §3 禁令）。
  - 断言：边是**确定性**的（同一输入两次 → 同一结果；PoC 的布局也已用固定种子保证可复现）。
  - **变异**：把阈值调到 1.0（只允许精确）→ 边数降到 ~117 但仍 > 0（证明回退链在工作，不是空跑）。
- **规模** L ｜ **风险** 中（相似度阈值需真实数据调参；概念节点的"不冒充 KP"必须有守卫）

#### TASK-94 — 冲突检测重构：样板黑名单 + 近重复合并 + 只报真矛盾

- **依据**：D8（15 条里 11 条误报）+ §2.4
- **改动**：`src/integration.py`
  - 比较前**剔除样板 token**（复用 TASK-89 的样板集合），并要求重合词中**非样板实词 ≥ N**。
  - 先做**近重复合并**（同一事实出现在两页/两材料 ≠ 冲突）；只有"否定 vs 肯定 + 高重合"才报。
  - 冲突记录新增 `reason_codes` 与 `confidence`（供 UI 排序），保留 `PENDING`（永不自动解决）。
- **验收**
  - 对三份真实 PDF：冲突数从 15 降到 ≤ 3，且逐条人工核对**无一条是样板误报**。
  - 断言：构造一对"真矛盾"（`X 是 Y` vs `X 不是 Y`）仍能报出（防止矫枉过正）。
  - **变异**：去掉样板黑名单 → 误报回归（红）。
- **规模** M ｜ **风险** 中（漏报 vs 误报的权衡要留真实用例）

### 阶段 P3 · 人性化交互

> 这一阶段的每条都对应 §5 的设计原则，验收里必须有**真实浏览器（Edge CDP）断言**，
> 而不只是 `ui_audit.js` 的静态渲染。

#### TASK-95 — 知识地图：把「图」做进产品（总览 → 筛选 → 详情，Shneiderman 箴言）

- **依据**：§1.3（当前是列表，名不副实）+ §2.6 + M2 §2
- **改动**（`src/web/views/knowledge.js`，**需与 Codex 在制品对齐后动手**）
  - 新增**图谱视图**（默认），参照已交付的 PoC `reports/km-map-pai-graph.html`：
    - 节点 = 知识点（颜色 = 材料，大小 = 关联数，红圈 = 冲突，黄圈 = 待审），
      **虚线节点 = 未归类概念**（来自 TASK-93）；
    - 边 = `knowledge_relations`（跨材料边加重显示）；
    - **分材料分簇布局**（固定质心 + 簇内斥力，确定性种子）；
    - 缩放/平移/悬停高亮邻域/点击进详情；搜索定位（**重音不敏感**）；筛选（材料、状态、生成方式、只看有关联）。
  - **孤立即实呈现**：无关联的点默认弱化并可一键隐藏，**计数在统计条里如实显示**（不藏）。
  - 现有的"按材料分组的卡片列表"**保留为「列表」视图**（它有用，只是不该叫地图）。
  - 每个 KP 卡片补**来源徽章**（`AI 总结` / `版面提取` / 人工确认）——现在 `generation_mode=None`
    的确定性 KP **没有任何徽章**（D9 的可见面）。
  - 图例写明口径："仅界面分组 · 非课程层级"（已有，保留）+ "虚线节点非知识点"。
- **验收**（真实浏览器 CDP 断言，不是静态渲染）
  - 图谱视图的节点数 == API 知识点数；边数 == `knowledge_relations` 行数。
  - 断言：**每个 KP 都有来源徽章**（含 `generation_mode=None` 的确定性 KP）。
  - 断言：虚线概念节点**不出现在** KP 计数里（计数口径与 §1.3 一致）。
  - 断言：380 → 1000 个节点时首屏 < 1.5s（合成数据，不写库）；缩放/悬停**零新请求**。
  - 断言：图例与提示里**没有**把 UI 分组说成课程层级。
- **规模** L ｜ **风险** 中（Codex 在制品重叠 → 见 §9.1）

#### TASK-96 — 溯源阅读器：KP → 证据 → 原文页

- **依据**：§2.3
- **改动**
  - 详情页：每条证据 = 一个可点角标；hover 浮层显示证据原文前 160 字；
    点击展开"证据 → 材料 → 页码"链，并提供"打开材料"入口。
  - 材料视图：材料 → 支持的知识点（已有反向索引），补"该页产出了哪些 KP"。
  - **不做**假高亮：没有文本层坐标就显示页码 + 片段，并说明"未定位到行"。
- **验收**
  - 浏览器断言：每条证据角标可点，落到的材料确实存在（不 404）。
  - 断言：浮层文本是证据原文的**子串**（不是二次生成）。
- **规模** M ｜ **风险** 低

#### TASK-97 — 审核工作台：批量、键盘、置信度分批、撤销、审计

- **依据**：§2.4（HITL：避免全量升级、避免审阅者超载、留痕）
- **改动**
  - 新视图 `#/courses/<c>/review-workbench`：单页、列表 + 快捷键
    （`A` 接受 / `R` 拒绝 / `J/K` 上下 / `U` 撤销 / `?` 帮助）；
  - **按置信度分批**：默认先审"AI 没把握的那批"（`needs_verification` / `confidence ≤ LOW`）；
  - **批量动作**（按筛选条件一次处理 N 条），但**每条都留审计记录**（谁、何时、从什么状态到什么状态）；
  - **撤销**最近一次批量动作（基于审计记录，不删历史）；
  - **指标卡片**：升级率、待审积压、平均处理时长（只读投影，不新增判定）。
- **验收**
  - 断言：批量动作后审计记录条数 == 影响条数。
  - 断言：撤销后状态逐条还原（逐字段比对，不只看计数）。
  - 断言：投影里不出现 `mastery` / `proficiency` / `predicted` 之类自造字段（项目既有禁令）。
- **规模** L ｜ **风险** 中（键盘可达性 + 撤销语义）

#### TASK-98 — 双语阅读：原文 / 中文对照 + 术语表 + 按需补齐

- **依据**：§2.7（沉浸式翻译模式）+ D14
- **改动**
  - 详情页：`原文 | 中文` 双栏（窄屏切换为上下），逐段对齐；一键切回纯原文。
  - 术语表：来自既有 glossary 阶段，展示 `原文术语 → 中文`（含证据引用）。
  - 未翻译时：显示"翻译此条"按钮 + 批量"补齐本课中文解释"（走异步作业，可中断续跑）。
  - 搜索**重音不敏感**（`calcul` 命中 `Càlcul`）。
- **验收**
  - 断言：搜索 `calcul` 能命中含 `Càlcul` 的 KP（现状会 miss）。
  - 断言：批量补齐后 `kp-translations` 计数 == AI KP 数。
  - 断言：中文栏内容不污染 `content` 字段（与 TASK-92 一致）。
- **规模** L ｜ **风险** 中（翻译调用成本 → 必须异步 + 可中断）

#### TASK-99 — 任务中心与成本透明

- **依据**：§2.1
- **改动**
  - 顶栏"任务"入口：进行中/已完成/已失败的分析作业，含**阶段、进度、ETA、用时、估算调用次数**；
  - 失败任务给出**可操作**的下一步（重试 / 跳过该材料 / 查看日志片段）；
  - 页面可关闭，回来仍在跑（服务端状态为准，前端只投影）。
- **验收**
  - 断言：离开页面再回来，进度连续（不重置）。
  - 断言：失败任务展示的错误码是 8 个结构化码之一（不是 traceback）。
- **规模** M ｜ **风险** 低

#### TASK-100 — 空态 / 错误态 / 无障碍 / 窄屏 全链路打磨

- **依据**：M2 §5、M3 §9/§10
- **改动**
  - 空课程引导（"先上传材料"）、零证据、零 KP、零搜索结果、材料无文本（`NO_TEXT_EXTRACTED`）各有明确文案与下一步；
  - `aria-live` 播报分析进度；焦点管理（打开详情/浮层后焦点可回）；
  - `prefers-reduced-motion` 已有，补断言；
  - 窄屏 520px：工具栏不遮挡、表格可横向滚动（已有部分）。
- **验收**
  - `ui_audit.js` 新增断言：每个空态/错误态**都有**非空标题与可操作入口（禁止空 `<h2>`）。
  - 浏览器断言：窄屏 `scrollWidth == innerWidth`（无横向溢出）。
- **规模** M ｜ **风险** 低

### 阶段 P4 · 质量保障与交付

#### TASK-101 — 变异测试与守卫补齐

- 每个 P0–P3 的修复都要有一条**变异**：把修复回退 → 对应用例必须红（本项目既有纪律）。
- 新增结构守卫：
  - 硬编码超时/阈值/预算**零出现**（全部走配置）；
  - `_candidate_knowledge_content` 里**没有**中文标签字面量；
  - `RELATED` 不得写入 `prerequisite`；
  - 单写者锁存在且唯一。
- **规模** M

#### TASK-102 — 性能与规模

- 1000 / 5000 个 KP：首屏时间、DOM 节点数、请求数上界（抓 N+1）、搜索响应（现状 O(K)）。
- 断言：视图切换/搜索**零新请求**（既有断言保持）。
- **规模** M

#### TASK-103 — 文档与运维

- `docs/status.md` 逐任务区块（项目格式）；
- `docs/user_guide.md` 补"如何导入一门课 / 如何读知识地图 / 如何审冲突 / 如何看双语"；
- `docs/troubleshooting.md` 补本次踩的坑（网关畸形响应、单写者冲突、后台任务时长上限）；
- `docs/configuration.md` 补所有新配置项（chunk 预算、重试、超时、语言阈值）；
- `/api/health` 自曝所有生效配置。
- **规模** M

#### TASK-104 — 端到端验收（真实数据）

- 用三份真实课件重跑完整链路（注册 → 分析 → 知识地图 → 冲突 → 审核 → 双语）；
- 真实 Edge headless + CDP 断言（本计划已验证此路径可行，见 §附录 C）；
- 产出 `reports/task-82-104-acceptance.md`，逐项对照 §3.2 指标表，**before/after 都给数字**。
- **规模** M

---

## 5. 人性化优化专章（原则 → 落地）

> 「人性化」不是加动效，而是**替用户承担本该由机器承担的认知负担**。
> 下面 8 条原则各自绑定到具体任务与**可执行断言**。

| # | 原则 | 用户痛点（真实场景） | 落地 | 任务 | 可执行断言 |
|---|---|---|---|---|---|
| 1 | **不让人干等** | 点了"AI 分析"后 25 分钟白屏 | 异步作业 + 进度/ETA + 可离开 | 86/99 | 响应 < 1s；离开再回进度连续 |
| 2 | **不让人做机器该做的事** | 55 个误报冲突要人一条条点 | 误报过滤 + 批量 + 按置信度分批 + 撤销 | 94/97 | 冲突 ≤ 3；批量后审计 == 影响数 |
| 3 | **不让人读到机器内部** | 裸 key / 哈希 / `undefined` / 语言错乱 | 守卫 + 三语 + 语言标注 | 90/92/100 | `ui_audit` 裸 key == 0；哈希只进属性 |
| 4 | **不让人失去掌控** | 分析失败只给一串英文错误 | 8 个结构化错误码 + 可操作下一步 + 取消/续跑 | 83/85/86/99 | 失败态含错误码与下一步 |
| 5 | **不让噪声淹没信号** | 380 个 KP 里混着作者名和封面信息 | 样板剥离 + 元数据过滤 + 来源徽章/筛选 | 89/91/95 | 版面条目 == 0；徽章数 == 来源数 |
| 6 | **不让语言成为障碍** | 加泰语原文看不懂、术语不统一 | 原文/中文对照 + 术语表 + 重音不敏感搜索 | 92/98 | `calcul` 命中 `Càlcul`；术语表一致 |
| 7 | **让人看见"为什么"** | 不知道这个知识点凭什么算知识点 | 溯源阅读器（KP→证据→页码）+ hover 原文 | 96 | 每条证据可点且不 404；浮层是原文子串 |
| 8 | **不让人被一次性成本吓退** | 不知道要花多久/多少钱 | 成本与调用数透明 + 预估 | 99 | 任务卡片含用时与估算调用数 |

补充的"小而真实"的人性化细节（都在 TASK-95–100 范围内）：

- **重音容错搜索**（加泰语硬需求：`calcul` / `Càlcul` / `càlcul` 等价）；
- **中文解释优先展示**（中文母语者先看中文，原文可对照）——但**绝不**用中文替换原文；
- **键盘可达**：审核工作台全键盘操作；知识地图 `J/K` 移动、`Enter` 打开；
- **窄屏可用**：手机上也能查一个知识点（520px 断点已有基础）；
- **失败可自救**：错误信息里给出"该点哪个按钮"；
- **首次使用引导**：空课程时告诉用户"上传材料 → 分析 → 看图"三步；
- **术语一致性**：同一术语在全课译文里保持同一译法（来自 glossary，避免"积分/积分值/积分运算"乱翻）。

---

## 6. 里程碑与节奏

| 里程碑 | 内容 | 交付物 | 门禁（全部必须绿） |
|---|---|---|---|
| **M1 · 可靠** | P0（82–87） | 异步作业 + 重试 + 缓存续跑 + 单写者 | 定向测试 + 变异验证；一次真实 PDF 分析**不靠驱动脚本**跑通 |
| **M2 · 便宜且干净** | P1+P2（88–94） | 分块改造 + 样板剥离 + 关系边 + 冲突重构 | 单份 PDF 调用数 ≤ 12；版面条目 0；关系边可溯源；冲突 ≤ 3 |
| **M3 · 好用** | P3（95–100） | 信息架构 + 溯源阅读器 + 审核台 + 双语 + 任务中心 | 浏览器断言全过；无障碍/窄屏通过 |
| **M4 · 收口** | P4（101–104） | 变异守卫 + 性能 + 文档 + 验收报告 | 指标表 before/after 逐项有数字 |

**节奏建议**：M1 是全计划的关键路径（异步化是后续一切体验的前提），建议**单独一个迭代**做完并冻结；
M2 的 TASK-88b（迁移策略）必须在改分块**之前**就设计好，否则重跑会污染用户已有的审核结果。

---

## 7. 度量与验收矩阵

| 指标 | 现状 | 目标 | 测量方式 | 任务 |
|---|---|---|---|---|
| LLM 调用数 / 30 页 PDF | 30 | ≤ 8 | provider 计数（假 provider 单测 + 真实网关统计） | 88 |
| 墙钟时间 / 份 | 25–50 min | ≤ 10 min | 端到端计时 | 88/86 |
| chunk 失败率 | 24% | < 1% | 20 次重复统计 | 83/84 |
| `analyze` 阻塞 | 25–50 min | < 1 s | HTTP 响应时间 | 86 |
| 确定性版面条目 | 51 | 0 | 断言（作者名/纯页码/空串） | 89 |
| 冲突误报率 | 73% | < 20% | 20 条人工标注 | 94 |
| 证据语言正确率 | 0% | 100% | 断言 | 90 |
| 关系边 | 0 | ≥ 200（PAI）/ ≥ 20（Gestió），可溯源、无悬空边 | `relation_count` + 悬空边检查 | 93 |
| 图谱视图 | 不存在（只有列表） | 默认图谱 + 列表并存，节点数 == KP 数 | 浏览器 CDP 断言 | 95 |
| 中文解释覆盖 | 43/329 | 100% | 缓存计数 | 98 |
| 首屏（380 KP） | 未测 | < 1.5 s | 浏览器实测 | 95/102 |
| 裸 key / undefined | 0 | 0（守卫） | `ui_audit.js` | 100 |
| 重音容错搜索 | 不通过 | 通过 | 断言 | 98 |

---

## 8. 风险登记册

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | **与 Codex 未提交在制品冲突**（`src/web/views/knowledge.js`、`src/web/i18n.js`、`src/web/app.js`、`src/application/workspace.py` 等） | 高（改同一文件） | 开工前先交接/冻结；本计划不动对方文件；按文件划分所有权（§9.1） |
| R2 | 改分块 → AI 输出变化 → KP ID 变化，污染已有审核结果 | 高 | TASK-88b：版本化 + dry-run 差异预览 + 人工确认项不删 |
| R3 | 异步化引入并发缺陷（线程安全、状态机） | 高 | 单 worker 队列 + 明确状态机 + 结构守卫 + 并发测试（项目有 `test_concurrent_writes.py` 先例） |
| R4 | 重跑全部材料的**成本**（真实网关按量计费） | 中 | 先 dry-run 估调用数；分材料重跑；用缓存续跑避免重复 |
| R5 | 提示词改动导致质量回退（过度过滤掉真知识点） | 中 | 用三份真实 PDF 做 before/after 对照；保留"低信号但可见"而非直接删 |
| R6 | 本机环境：便携解释器缺失、后台任务约 3 小时被杀 | 中 | 长任务拆小；用 `run_in_background` + 文件日志；不依赖 `/Python/` |
| R7 | 关系边引入后 UI 复杂度上升 | 低 | 默认折叠；"关系只在真实存在时呈现"（M2 §3） |
| R8 | 单写者锁在 Windows 上的语义差异 | 中 | `msvcrt.locking` 与 `fcntl` 双实现 + 跨平台单测（项目已有 Windows 优先惯例） |

---

## 9. 协作与工程纪律（本项目特有，必须遵守）

### 9.1 与并行协作者的边界

- 仓库里有**未提交的在制品**（Codex 的 Knowledge Map Premium / review-restart-state-fix）。
  **开工前**：`git status` + `git log --oneline -5` + `grep -n '^## ' docs/status.md | tail` 对齐。
- **提交只提交自己的文件**（`git add <文件名>`，**绝不** `git add -A` / `git add .`）。
- 推送前看 `git log origin/main..HEAD`——本地可能有**别人**的未推送提交，遇到就停下来问用户。
- 本机**没有 GitHub 凭据**：commit 可以做，**push 要用户自己来**。

### 9.2 测试纪律

- **不跑全量回归**（一轮 20–60 分钟，用户 2026-10-10 明确要求）。只跑**受影响的**定向测试 +
  前端审计：`pytest tests/test_<受影响>.py -q -p no:cacheprovider` + `node scripts/ui_audit.js`
  + `node scripts/ui_render_check.js`。
- 判回归按**失败的名字有没有变**，不按 `failed` 是否为 0。
- **每个修复都要有变异验证**：把修复回退 → 对应断言必须红。没有变异验证的断言视为空跑。
- 前端改动后 `node --check`；三语 key 对齐由 `ui_audit.js` 守卫。

### 9.3 已知的"写文档/写代码"陷阱（本项目实测）

- **反引号**：要写的内容里可能出现反引号时，**先 Write 到文件，再用 Python 读文件写**；
  不要用 `python -c "..."` 或 bash heredoc（会被命令替换静默吃掉）。
- **同一文件的多处 Edit 必须串行**（并行编辑会静默吞掉前一处改动，而工具两次都报 success）。
- **行尾**：`.gitattributes` 规定代码用 LF、`*.bat/*.cmd` 用 CRLF；改完别弄反。
- **长文档追加**先写临时文件再 Python 追加（同反引号理由）。
- 探针脚本要带 `PYTHONPATH`；`curl 127.0.0.1` 必须 `--noproxy '*'`。

### 9.4 交付纪律

- **要求文档是唯一依据**：文档没要求的不加（不多产出文件、不加 README、不留备份）。
- **精准修改**：只改必要的部分。
- **功能下线要连带清掉门面**（脚本/样式/入口）。
- 每个任务完成后追加 `docs/status.md` 区块（格式：`## TASK-NN — 标题` 或 `## 修复 — …`，
  照抄最近一个区块的写法）与 `.workbuddy-ai/memory/YYYY-MM-DD.md`。

---

## 10. 附录

### 附录 A · 代码位置索引（缺陷 → 位置）

| 缺陷 | 文件 | 位置/符号 |
|---|---|---|
| D1 超时未接线 | `src/application/ai/service.py` | `AIAnalysisService.analyze(timeout_seconds=60)` |
| D1 超时未接线 | `src/application/workspace.py` | `_translate_kp_once(timeout_seconds=60)`、`_kp_translation_stage` |
| D2 畸形不重试 | `src/application/ai/provider.py` | `_post_json` 的 `except (ValueError, KeyError, IndexError, TypeError)` |
| D3 缓存不跨尝试 | `src/application/ai/service.py:90` / `workspace.py:2534` | `AIAnalysisService()` 每次新建 |
| D4 同步阻塞 | `src/web/views/materials.js:460` | `await api('.../analyze')` |
| D5 双进程锁 | `src/persistence/database.py` | 无单写者守卫 |
| D6 语言 Unknown | `src/document_input.py` + 证据装配 | 文档证据不做语言判定 |
| D7 关系无边 | `src/application/ai/validators.py:148` | relations 只拼进 content |
| D8 冲突误报 | `src/integration.py` | `_negation_conflict_from_statements` / `_is_contradiction` |
| D9 版面 KP | 确定性装配路径（`knowledge_assembly`） | 幻灯片标题 → KP 标题 |
| D10 封面元数据 | `src/application/ai/prompts.py` | 抽取提示词未排除封面 |
| D11 英文标题 | 同上 + `validators.py` | 标题语言未强制 |
| D12 中文标签 | `src/application/ai/validators.py:148-149` | `_candidate_knowledge_content` |
| D14 报告未落盘 | `src/application/workspace.py` | `_persist_ai_report` 在翻译阶段之后 |
| 分块成本 | `src/application/ai/chunking.py` | `chunks_for_evidence`（逐条证据切块） |

### 附录 B · 调研出处

| 主题 | 出处 |
|---|---|
| 长任务进度/恢复/目标契约 | Anthropic《Effective harnesses for long-running agents》；agentpatterns.ai《Goal Monitoring and Progress Tracking》 |
| 长任务状态 UI | aiuxdesign.guide《Agent Status Monitoring》 |
| 分块策略基准（86.7% vs 71.3%、256–768 token 甜区、<200 token 丢上下文、元数据 +8–12%） | Ertas AI《RAG Chunking Strategy Benchmark》 |
| 文档预处理（样板 >80% 规则、固定位置重复、质量闸门 `min 50` / `alpha_ratio ≥ 0.4`、NFKC） | bthek1《Document Pre-processing for RAG》 |
| HITL（置信度阈值升级、避免全量升级、审阅者过载、升级率指标、单页 + 键盘） | agentic-design.ai《人在回路中 (HITL)》 |
| 知识图谱归一化（Extract–Define–Canonicalize） | arXiv 2404.03868 |
| 可视化信息检索箴言（overview → zoom/filter → details-on-demand；relate/history/extract） | Shneiderman 1996（InfoVis:Wiki 条目） |
| 双语对照阅读（原文下方逐段译文、术语库、可切换） | 沉浸式翻译（Immersive Translate）官方博客/使用文档 |
| 溯源引用 UX | NotebookLM、Perplexity 的产品行为（行内引用 + 悬停预览 + 跳源） |
| SQLite 并发（WAL 一写多读、`busy_timeout`、应用层单写者） | SQLite 官方 locking 文档 + 多篇工程实践总结 |

### 附录 C · 复现命令（本次实测验证过的路径）

```bash
# 1) 只读拉取知识地图与交叉视图（私有端口，真实数据）
"C:/Users/Rafae/AppData/Local/Microsoft/WindowsApps/python.exe" temp/km_final_fetch.py
# 2) 结构 + 覆盖度 + 质量校验（10 条结构断言 + 61 项概念清单）
"C:/Users/Rafae/AppData/Local/Microsoft/WindowsApps/python.exe" temp/km_validate.py
# 3) 真实浏览器渲染校验（Edge headless + CDP，含截图）
"C:/Users/Rafae/AppData/Local/Microsoft/WindowsApps/python.exe" temp/km_browser_check.py
# 4) 注册 + 分析驱动（支持按材料名过滤，可断点重跑）
"C:/Users/Rafae/AppData/Local/Microsoft/WindowsApps/python.exe" temp/km_pai_driver.py "CSS.pdf"
# 5) 从真实知识地图 JSON 生成「真图谱」PoC（归一化术语 → 边 + 分簇布局 + 交互）
"C:/Users/Rafae/AppData/Local/Microsoft/WindowsApps/python.exe" temp/km_build_map.py
# 6) 用 Edge headless 给生成的 HTML 截图（验证渲染）
"C:/Users/Rafae/AppData/Local/Microsoft/WindowsApps/python.exe" temp/km_shot_maps.py
```

> **注**：`temp/` 下的脚本是本次实测的工作痕迹，按项目惯例交付前可移入 `scripts/dev-archive/`；
> 其中 `km_validate.py` / `km_browser_check.py` 建议**升级为常驻验收脚本**放进 `scripts/`
> （TASK-104 的一部分），因为它们是本计划 §3.2 指标表的测量工具。
