# 知识地图修复 + 手动 AI 接入 —— 阶段 0 基线与分析报告

- 日期：2026-10-13
- 基线提交：`1cf14b1`（工作树 **clean**，无未提交改动）
- 环境：Python 3.14.5（无 venv，系统解释器）；`classroom-data/` 为真实数据目录
- 实测脚本：`temp/measure_knowledge_map.py`（只读；不写库、不改任何业务对象）
- 状态：**阶段 0 完成**。阶段 1 起按本文 §5 顺序执行。

> 纪律：本文把**实测**与**推断**分开标注。所有缺陷都带 `文件:行` 证据。
> 未执行的压力/规模测试，一律写成"未测"，不写成结论。

---

## 1. 当前架构（实测）

### 1.1 知识地图数据流

```
GET /api/courses/{id}/knowledge-map          src/api/endpoints.py:725-728
  └─ Workspace.knowledge_map(course_id)      src/application/workspace.py:2737
       ├─ knowledge_service.get_knowledge_points(course_id)   (全量 KP dict)
       ├─ self.list_materials(course_id)
       ├─ evidence_counts  = {kp_id: len(evidence_refs)}
       ├─ distinct_evidence_ids → 经 EvidenceStore 逐条解析 → material_to_knowledge
       │    （解析不到的进 unresolved_evidence_ids；dict 形 ref 的 material_id 仅作 store-miss 回退）
       └─ graph = build_knowledge_graph(kps, materials, material_to_knowledge)  src/knowledge_graph.py:139
```

`knowledge_graph.build_knowledge_graph` 是**纯函数**（无 I/O、无随机、输出全排序、概念 ID 内容寻址），
只读三样输入：KP 的 `title` / `content` / `original_terms`。

### 1.2 知识地图前端四视图与实际读取字段

| 视图 | 位置 | 读取的字段 |
|---|---|---|
| `graph`（默认） | `src/web/views/knowledge-graph.js` | `graph.nodes/edges/concepts/unresolved_terms`、`materials` |
| `explore` | `knowledge.js:665-753` | `title` `knowledge_id` `evidence_refs` `validation_status` `review_status` `material_to_knowledge` |
| `list` | `knowledge.js:765-808` | 同上 + `original_terms`（搜索） |
| `materials` | `knowledge.js:810-853` | `materials`、`material_to_knowledge`、`evidence_refs` |

**实测结论**：四个视图**没有任何一处**读取 KP 的 `content` 或 `detail` 正文；知识点详情页走的是**另一条通道**
`GET /api/knowledge/{id}/trace`（`knowledge.js:130-146`）。

### 1.3 关系的三种表示（这是本任务的核心结构问题）

| # | 表示 | 位置 | 方向 | 来源 | 消费者 |
|---|---|---|---|---|---|
| 1 | `knowledge_relations` 表 + `KnowledgeOrganizationService` | `m001_initial_schema.py:283`；`knowledge_organization.py:1516/1578` | **有向 + 有类型**（`KnowledgeRelationType`） | 结构化写入（`add_relation`，带校验） | **无**（真实库 0 行） |
| 2 | `knowledge-map` 的 `relations` | `workspace.py:2821`（`get_dependencies().outgoing`，**只含 PREREQUISITE**） | 有向 | 表示 1 的投影 | **无**（前端 `kmBuildViewModel` 不读该键） |
| 3 | `graph.edges` | `knowledge_graph.py:213-220` | **无向**（`tuple(sorted(...))`） | 知识点正文 `关联:` 行的正则 + 归一化 | 图谱视图（唯一被画的） |

**实测**：真实库 `knowledge_relations = 0` 行、`topics = 0` 行、`knowledge_memberships = 0` 行，
所以今天"地图上的边"**100% 来自表示 3**。而表示 1 的读接口确实存在
（`list_relations` / `list_topics` / `get_knowledge_point_topics`），只是 `KnowledgeService`
没有把它们透出给 `Workspace`（`knowledge_service.py` 只有 `get_knowledge_points` /
`get_course_knowledge` / `get_coverage` / `get_gaps` / `get_dependencies` / `get_conflicts` / `get_evidence_for_knowledge_point`）。

### 1.4 边的**唯一**写入口：知识点正文里的 `关联:` 行

```
validators._candidate_knowledge_content  src/application/ai/validators.py:145-153
    parts.append("关联: " + " / ".join(candidate.relations[:5]))     ← 静默截断 5 条
        ↑ content[:4000]                                            ← 第二重静默截断
knowledge_graph._relation_terms          src/knowledge_graph.py:116-133
    只认 line.startswith("关联") + ASCII ":" 分割 + "/" 分列
```

即：**非 AI 路径（确定性装配 / 手工创建）的知识点永远没有边**，因为只有 AI 抽取器会写这行。

### 1.5 AI Pipeline 全流程（阶段 2/3 要复用的对象）

```
process_material → ingestion → Evidence(store)                      workspace.py
   └─ _maybe_auto_ai (仅当 _ai_enabled)                             workspace.py:1523
        └─ analyze_material_with_ai                                  workspace.py:2386
             └─ AIAnalysisService.analyze                            ai/service.py:102
                  ├─ pipeline.analyze_material                       ai/pipeline.py:405
                  │    ├─ chunks_for_evidence  → chunk-<sha16> 确定性 ID, 且给出
                  │    │      chunk→evidence_id 映射表               ai/chunking.py:216-250
                  │    ├─ Level 1: 每 chunk → provider.generate_structured(prompt)  ai/pipeline.py:355
                  │    │      响应 → parse_structured_response       ai/schemas.py
                  │    │      ★ 强制重写 candidate.chunk_id = 当前 chunk    ai/pipeline.py:355-360
                  │    └─ Level 2: merge 提示词 → MaterialAIResult
                  ├─ pipeline.ground_and_classify  (grounding/去重/冲突)  ai/pipeline.py
                  ├─ map_candidate_to_kp_payload   (knowledge_score 由证据条数派生)  validators.py:645-724
                  ├─ workflow.register_knowledge_point  + _mirror_to_structure   service.py:368/477
                  └─ 报告 → materials/ai-reports/<课>/<材料>.json     workspace.py:1590
```

关键性质（阶段 2/3 的安全基础）：

- **模型永远看不到真实 evidence_id**：它只见 `chunk-*`，真实映射表只在应用侧（`chunking.py:docstring`）。
- 一切校验/grounding/落库都在 **provider 之后**，与 provider 是否出网无关。
- 提示词集中在 `ai/prompts.py`（唯一合法组装点），带版本号并进入 `processing_identity`。

### 1.6 AI 模式与健康检查（实测）

| 项 | 值 | 位置 |
|---|---|---|
| 模式白名单 | `disabled / fake / real` | `workspace.py:2356-2362`（非法值直接 `ValueError`） |
| 装配 | `_build_ai_provider` → `(provider, ai_mode, config)`，`Runtime.ai_mode` | `bootstrap.py:217/261`；`runtime.py`/`bootstrap.py:117` |
| 健康 | `/api/health` 的 `ai_mode` / `ai_enabled` / `ai_timeout_seconds` | `workspace.health` |
| 本机实测 | `ai_enabled=true, ai_mode=real, ai_timeout_seconds=120`（`.env` 已配真实网关） | 本次实测输出 |

> ⚠️ 阶段 2/3 的测试**必须**用 Fake/Replay provider，绝不触发真实出站（本机 `.env` 有真实凭证）。

### 1.7 报告落盘与幂等（实测）

- 路径：`materials/ai-reports/<course>/<material>.json`（`workspace.py:1590`，经 `safe_join`）。
- 版本：`AI_REPORT_VERSION = "ai-report-v2"`；读取时对 v1 做加性补全（`_normalise_ai_report`）。
- 身份：`processing_identity(material_hash, model, prompt_versions, pipeline_version)`；
  KP 的 `aikp-<sha16>` 由 `candidate_knowledge_id(course_id, title, kp_type)` 内容寻址 → 重跑幂等。

---

## 2. 已确认的缺陷（全部带证据）

### A. 结构级

**A1｜边依赖用户可读正文里的特殊格式，且有四处静默丢失。**

| # | 丢失点 | 证据 |
|---|---|---|
| a | 非 AI 路径的 KP 无 `关联:` 行 → 0 边 | `validators.py:145-153`（唯一写入口）；实测 GEO / GAEiR 两门课 `edge_count = 0` |
| b | 每条 KP 关联被静默截断到 5 条 | `validators.py:151` `candidate.relations[:5]` |
| c | **CJK 术语结构性不可连** | `_TOKEN_SPLIT = [^0-9a-z\u00e0-\u00ff]+`（`knowledge_graph.py:69`）→ 中文被整体换成空格 → `canonical_key('资本') == ''`；且 `how != "unresolved"` 使其**连 `unresolved_terms` 都不进**（`knowledge_graph.py:196/213`） |
| d | **全角冒号不匹配** | `_relation_terms` 用 `stripped.split(":", 1)[-1]`（`knowledge_graph.py:127`）；`关联：术语` 切不开 → 术语变成 `关联：术语` → 同样静默消失 |

**A2｜短术语过短判定与索引条件不一致（off-by-one）。** 建索引用 `len(key) > _MIN_TERM_CHARS`（`knowledge_graph.py:183`），
解析用 `len(key) < _MIN_TERM_CHARS`（`:178`）⇒ **恰好 4 字符的术语可解析但永不进标题索引**，
只能落到 token 路径或变成 `too_short`（而 `too_short` 不产生任何诊断）。实测夹具中 `HTML`（4 字符）即此形状。

**A3｜同一页三套关系表示，被画的那套最弱。** 见 §1.3。表现为：
后端计算并传输了权威有向关系（`relations` 键，PAI 实测 2 字节 = `[]`），前端从不消费；
图谱用无向的正则边（PAI 实测 255 条）；详情页用第三条通道（`trace.dependencies`）。
**方向信息在"被画的那条链路"上被 `tuple(sorted(...))` 彻底抹掉。**

**A4｜层级字段是硬编码常量。** `topics: []` / `knowledge_memberships: []` / `topic_count: 0`
（`workspace.py:2826-2836`），而真实组织模型存在且可读（`knowledge_organization.py:1242/1386/1578`）。
实测真实库 0 行 → 结论是**接线缺失**，不是数据缺失。

**A5｜孤立点占比高且无解释入口。** 实测（真实数据）：PAI 380 点中 129 点孤立、Gestió 61 点中 34 点孤立、
GEO/GAEiR 13/13 **全孤立**。默认视图只把孤立点 alpha 压到 0.20（`knowledge-graph.js:388-390`），
"只看有关联的点"把它们整批藏起来 —— 不解释原因、不提供检索入口。

### B. 性能与载荷（实测）

| 课程 | KP | 载荷 B | 其中 KP 正文 B | graph B | 边 | 概念 | 孤立 | 实测耗时 |
|---|---|---|---|---|---|---|---|---|
| PAI | 380 | **576,117** | 432,257（75%） | 105,201 | 255 | 11 | 129 | 86.8 ms |
| Gestió de Projectes | 61 | 85,732 | 61,352（72%） | 18,138 | 23 | 4 | 34 | 20.6 ms |
| GEO | 13 | 44,113 | 35,855（81%） | 3,445 | 0 | 0 | 13 | 7.1 ms |
| Gestió Ambiental | 13 | 18,322 | 11,131（61%） | 2,987 | 0 | 0 | 13 | 3.8 ms |
| Digitalització | 0 | 1,408 | — | 318 | 0 | 0 | 0 | 3.2 ms |

**B1｜载荷 61–81% 是四个视图都不读的 KP 正文**（§1.2 实测）——PAI 单次 432 KB 纯冗余。
**B2｜读路径现算且无缓存**：每个去重证据 `store.get()`（`workspace.py:2796-2807`）；图投影每次做 NFKD 归一 + token 两两比较。
**B3｜canvas 每帧重建邻接表**：`kmGraphDraw` 每帧调 `kmGraphAdjacency()`（`knowledge-graph.js:377`）；`kmGraphPick` 每 mousemove 遍历全部节点（`:443`）。

### C. 界面与可访问性

**C1｜图谱在浅色主题下是一块黑板。** `ctx.fillStyle='#101219'`（`knowledge-graph.js:341`）
+ `.km-graph{background:#101219}`（`styles.css:916`），而应用默认浅色（深色在 `@media`，`styles.css:52`）；
节点/标签/选中色同样硬编码，未使用任何设计 token。
**C2｜canvas 无可访问信息**：`aria-label` 值等于页面 h1（`knowledge-graph.js:242`）；无键盘可达、
无"焦点=hover"等价物；`wheel` 上 `preventDefault`（`:527`）吃掉页面滚动。
**C3｜注释漂移**：`knowledge.js:119` 写明「生成卡片」已随 TASK-106 下线，`:790` 的注释仍称"原样搬进列表视图的行"；
全仓无对应 action（实测 grep 仅命中这两处注释）。

---

## 3. 尚待验证的性能假设（不写成结论）

1. **1000+ 知识点规模未测**。现有报告自认"1000 以上未测"。是否需要在读路径加缓存/索引，必须先跑基准。
2. **合成大课基准**（500 / 1000 / 2000 KP）尚未建立；`test_performance.py` 与 `test_stress_semester.py` 有相关夹具，但未测知识地图端点。
3. **前端布局/绘制帧耗时未测**：需要 `performance.now()` 实测布局一次的成本与 hover 帧成本，再决定是否做邻接表缓存与离线画布。
4. **精简载荷能省多少字节**：只知"正文占 61–81%"，具体去掉 `content` 后的净收益需要实测（阶段 1 任务 1.5 建立前后对比）。

---

## 4. 目标架构

### 4.1 阶段 1：让"地图"名实相符，且不编造

1. **关系解析去静默丢失**：`canonical_key` CJK 感知；ASCII/全角冒号统一；短术语判定统一；
   `too_short` 与 `unresolved` 分别进入可观测诊断；取消无依据的 `[:5]` 截断（内容长度上限保留但可观测）。
2. **权威关系优先**：`KnowledgeService` 增只读透出（`get_relations` / `get_topics` / memberships），
   `knowledge_map` 把它们投影为 `graph.edges` 的**首选**来源（`origin="registered"`、保留方向与 `relation_type`）；
   正文正则边降级为 `origin="content_hint"`（无向、明确标注"推断"），且与已登记边不重复。
   → 旧数据（正文格式）继续可读，但**不再被当成本事实**。
3. **层级字段改为真实投影**：`topics` / `knowledge_memberships` / `topic_count` 从组织模型读，
   真实为空时就是空数组（诚实），并让未分类/孤立点保持可见与可检索。
4. **载荷精简**：地图返回精简 KP 投影（去掉四个视图都不读的正文），
   正文继续由详情/trace 提供；建立精简前后字节基线。
5. **主题与可访问性**：图谱使用设计 token（浅/深两套）；键盘可选点、可获得等价信息；
   canvas 有信息量描述；不再无条件吃掉滚轮；清理失效注释。

### 4.2 阶段 2/3：手动 AI（导出 → 外部模型 → 导入）

- 新增第四态 **`manual`**（不得伪装成 `real`），provider 为 **`ReplayAIProvider`**：
  接口与 `AIProvider` 同形但不发网络请求，按 `request_id` 从用户贴回的响应池取值。
- **导出**：确定性 chunking + 现有 prompts 构建器 → 一个批量任务包
  （人类可读 Markdown + 机器可读 JSON，含 `bundle_id` / 协议版本 / `request_id` / 阶段 / chunk_id / 提示词 / 输出约束）。
  **一次粘贴一份材料**（实测单份材料文本量 8.9–11.2K 字符，放得进一条消息）。
- **导入**：校验 `bundle_id` + 协议版本 + `request_id` 归属 → 逐请求走 `parse_*` → 与真实 API **完全同路**
  （grounding / 去重 / 冲突 / 幂等入库 / 报告落盘）。**不新增任何直接写库捷径。**
- **安全**：严格 `request_id` 对号入座（现有 `analyze_chunk` 会强制改写 chunk 归属，manual 模式必须先验证归属）；
  粘贴内容视为不可信输入（schema + 大小上限）；报告如实标 `provider="user-pasted"`；
  逐请求状态（成功/缺失/重复/未知/格式错误/归属错误/grounding 失败）；部分成功可单独重试且不重复入库。

---

## 5. 阶段实施顺序（与任务书一致）

| 阶段 | 内容 | 状态 |
|---|---|---|
| 0 | 调查 + 报告 + 基线 | ✅ 本次完成 |
| 1 | 知识地图结构正确性（1.1–1.7） | ⏭ 下一步 |
| 2 | 手动 AI 导出 | ⏳ 待阶段 1 通过 |
| 3 | 手动 AI 导入 + 回放 | ⏳ |
| 4 | 端到端验收 | ⏳ |

---

## 6. 数据兼容性与迁移风险

1. **不新增表、不做破坏性迁移**：阶段 1 只增加"读透出"与只读投影；
   `knowledge_relations` / `topics` / `knowledge_memberships` 的 schema（m001）保持不变。
2. **旧数据兼容**：正文 `关联:` 解析器**保留**（降级为 `content_hint`），
   已完成 AI 分析的 467 条 KP 不会因本次改动失去边。
3. **真实库现状**：`knowledge_relations=0` / `topics=0` / `knowledge_memberships=0`
   → 本次"权威优先"在当前数据上不会改变任何既有图的边数，只会给边加 `origin` 标注；
   这是**可验证的**不变量（阶段 4 测试 A 里断言）。
4. **风险点**：`relations` 键语义若从"PREREQUISITE outgoing"扩大到"全部已登记关系"，
   需要同步 `tests/test_knowledge_map.py`（空课程断言 `relations == []` 仍然成立）与前端。
5. **载荷精简风险**：`knowledge_points` 若去掉 `content`，需核对所有消费者
   （前端四视图 + 详情页已走 trace；`ui_audit.js` / `ui_render_check.js` 夹具需同步）。

---

## 7. 测试与验收标准

### 7.1 基线（本次实测，2026-10-13）

| 检查 | 命令 | 结果 |
|---|---|---|
| 知识地图/图谱回归 | `pytest tests/test_knowledge_map.py tests/test_knowledge_graph.py -q -m "not integration"` | **27 passed**（2.46s） |
| UI 审计 | `node scripts/ui_audit.js` | **OK（610 checks）**（文档写 613，属 TASK-106 后的计数漂移，记录不改） |
| UI 渲染 | `node scripts/ui_render_check.js` | **312/314 —— 已红**（见下） |
| git | `git status` | clean @ `1cf14b1` |

> **继承的红色基线（非本任务引入，记录在案）**：`ui_render_check.js` 有 2 条失败 ——
> `#/students` 与 `#/courses/course-test/students/stu-ana` **期望重定向到"我的课程"，
> 实际渲染了"未找到页面"**。成因可在 `1cf14b1`（"Auto-create default student in study flows"）
> 的提交说明里读到：该提交改了口径（"redirects unknown hashes to My Courses instead of
> a bare not-found page"）但重定向实现与这两条哈希不一致。
> 属**学生页路由**，与本任务的知识地图/手动 AI 无关。**本任务不静默修改它**，
> 只在每阶段记录"同 2 条，未新增失败"。

### 7.2 验收标准（阶段 1）

1. 中文 / 中英混合知识点能建立预期关系；全角冒号与 ASCII 冒号一致；短术语不误连。
2. 重复关系不产生重复边；无法匹配的术语进入诊断（带原因），不再静默丢弃。
3. 大量关联不被静默截断（含 `relations[:5]` 的回归断言）。
4. 权威关系在地图与详情语义一致，方向不丢失；正文正则边明确标为推断。
5. 重新分析后布局不再错误复用旧图（指纹含节点/边身份）。
6. 地图不再无条件传输四个视图都不读的正文，且有前后字节实测。
7. 浅色/深色主题均可用；核心操作可键盘完成；canvas 描述有信息量；滚轮不再无条件被吃。
8. 新增回归测试全绿；`ui_audit.js` 不倒退；`ui_render_check.js` 仍只有那 2 条继承失败。

### 7.3 验收标准（阶段 2/3）

- 导出：0 网络调用、0 知识库写入；请求结构可解释、`request_id` 唯一稳定；有版本与 `bundle_id`。
- 导入：错 bundle / 错 request_id / 非法 JSON / 非法 schema / 错位响应 / 未 grounding 内容
  **均不能入库**；重复提交幂等；部分失败可单独重试；报告如实区分手动与真实 API；
  `real` / `fake` / `disabled` 三态不回归。

---

## 8. 阶段 1 实施结果（2026-10-13）

阶段 1 的目标是"让地图名实相符且不编造"。下面每一条都带**文件 + 实测证据**，
实测数字与推断分开写。

### 8.1 改动清单

| 文件 | 改了什么 |
| --- | --- |
| `src/knowledge_graph.py` | CJK 进词字符类（`_CJK_RANGES`，NFKD 安全子集）；全角/半角冒号与斜杠等价；短术语索引与解析共用同一谓词 `_is_matchable`（修掉"可解析却永不进索引"）；`skipped_terms` 带原因（`too_short` / `self_reference`）；新增 `registered` 有向边（`origin` / `directed` / `relation_type`），正文推断边标 `content_hint`，并去重（登记过的对子不再画一条重复边） |
| `src/application/ai/validators.py` | 删除 `relations[:5]` 静默截断；正文里的截断改为可观测（写入条数 = 实际条数，另记截断统计） |
| `src/application/ai/schemas.py` | 候选关系字段的文档与长度约束与"不再固定截断"一致 |
| `src/application/knowledge_service.py` | 新增只读透出 `get_relations` / `get_topics` / `get_topic_memberships`（真实为空就是空） |
| `src/application/workspace.py` | `knowledge_map`：`topics` / `knowledge_memberships` / `topic_count` 从写死的空值改为真实读投影；`relations` 从 `get_dependencies().outgoing`（只含 PREREQUISITE）改为**全部已登记关系**；新增 `_knowledge_map_point` 精简投影 |
| `src/web/views/knowledge.js` | `KM_STATE` 新增 `relations` / `topics` / `memberships`；统计条第七项「已登记关系」；图例把两种边分开说明 |
| `src/web/views/knowledge-graph.js` | 布局指纹含节点/边身份（FNV-1a，按对象缓存）；配色改读 `--graph-*` token；已登记边实线 + 箭头、推断边细线；canvas `role="img"` + 真实计数 `aria-label` + `tabindex="0"`；方向键选点 / 回车开详情 / Esc 取消 / Home 适应窗口；Ctrl/⌘ + 滚轮才缩放；详情卡列出已登记关系（出边/入边 + 类型 + 链接）；`kmGraphFit` 量不到尺寸时返回 false 且不占用"自动适配"名额（修 NaN 视角） |
| `src/web/styles.css` | 新增 `--graph-*` token（浅/深各一份，含 5 色材料调色板）、画布焦点环、已登记关系列表样式；删除"不画 canvas 节点图"的失效注释 |
| `src/web/i18n.js` | zh/es/ca 三语新增/更新 12 个键（`km.statRelations`、`km.graph.relTitle`、`km.graph.openDetail`、`km.graph.a11ySummary`、`km.graph.legendRegistered`、`km.graph.legendInferred` + 提示/图例文案修正）；es/ca 不再把中文字面 `关联` 写进界面文案（CJK 扫描器会命中） |
| `scripts/ui_audit.js` | 610 → **627** 条（+17）：统计第七项、图例分离、canvas 自述计数、键盘可达、`origin` 语义纯函数、指纹三态、方向键/回车/概念节点回车、详情关系列表、滚轮两个方向 |
| `scripts/bench_knowledge_graph.js` | **新增**：加载仓库里那份 `knowledge-graph.js` 跑热路径的基准（真实/合成分开标注） |

### 8.2 实测数字（真实数据，可复现）

载荷精简（`temp/measure_knowledge_map.py`，`json.dumps(ensure_ascii=False)` 字节）：

| 课程 | KP | 精简前 | 精简后 | 降幅 | 其中 KP 部分 |
| --- | --- | --- | --- | --- | --- |
| PAI（大型） | 380 | 593,774 | 311,987 | **−47.5%** | 432,257 → 150,410（−65%） |
| Gestió de Projectes | 61 | 87,875 | 56,391 | −35.8% | 61,352 → 29,808 |
| Bases per a la Geoinformació | 13 | 44,190 | 27,161 | −38.5% | 35,855 → 18,766 |
| Gestió Ambiental… | 13 | 18,399 | 13,501 | −26.6% | 11,131 → 6,173 |

边的口径**没有回归**（精简只动响应形状，不动图）：PAI 255 条（11 概念 + 226 正文推断 +
29 概念连接）、Gestió de Projectes 23 条 —— 与阶段 0 基线逐字相同。真实库里已登记关系
**4 门课全部为 0**（`knowledge_relations` 表有 0 行），所以 `relations` 如实返回空数组、
统计条显示 0：**不为了好看造层级、造关系**。

后端耗时（真实数据，只读投影）：

| 课程 | `knowledge_map` 总 | 图构建 | Evidence 逐条解析（84 条 / 6 条） |
| --- | --- | --- | --- |
| PAI | 49.4 ms | 37.7 ms | 1.1 ms |
| Gestió de Projectes | 9.9 ms | 8.7 ms | 0.2 ms |

前端绘制基准（`node scripts/bench_knowledge_graph.js`，Node v24.18.0）：

| 指标 | 真实载荷 PAI（391 节点 / 255 边） | 合成 2000 节点 / 3200 边（synthetic） |
| --- | --- | --- |
| `kmGraphLayoutKey` 冷算 | 4.87 ms | 18.3 ms |
| `kmGraphLayoutKey` 命中缓存 | **0.006 ms** | 0.003 ms |
| `kmGraphComputeLayout` 一次性 | 735 ms | 20.8 s |
| `kmGraphAdjacency` | 0.20 ms | 1.77 ms |
| `kmGraphDraw` 每帧 | 3.0 ms | 32.4 ms |
| `kmGraphPick` 每次悬停 | 0.62 ms | 4.08 ms |

### 8.3 由基准得出的决定（不是猜测）

1. **指纹必须缓存**。新的指纹要认得出身份变化（旧的只拼三个数字，重分析后 1 换 1
   就会复用旧坐标），冷算在真实数据上是 4.87 ms —— 而 `kmGraphPositions` 每帧都会问一次。
   按 graph 对象身份缓存后是 0.006 ms（对象身份变了就是数据变了：后端每次请求返回新对象）。
   **这是本轮唯一在基准上做的前端优化，且是"不做就会每帧多付 4.8 ms"的那种。**
2. **邻接表不缓存**。每帧重建实测 0.20 ms（真实数据），不值得加缓存与失效逻辑。
3. **大图布局不优化**。合成 2000 点在一次性布局上要 20.8 s —— 但真实最大课程是 391 点
   /735 ms，且布局按指纹缓存、只在数据变化时算一次。按"先基准后优化"的规则，这里**不做**
   复杂改造，只把数字写下来（候选做法：跨迭代复用空间哈希，别每轮重建字符串键的 Map）。
4. **后端不加批量读取**。Evidence 逐条 `store.get` 实测 1.1 ms（84 条），占整条投影 2%
   —— 批量化的收益在这里证明不了。真正的大头是图构建 37.7 ms（术语归一化匹配），
   它会让一次 GET 从 49 ms 降到 10 ms 量级，但改动要碰确定性敏感的匹配路径，
   本轮不动，只记录。

### 8.4 验证（真实执行的命令与结果）

| 检查 | 结果 |
| --- | --- |
| `pytest test_knowledge_graph / test_knowledge_map / test_application_knowledge / test_web_ui / test_web_ui_invariants / test_ai_understanding / test_ai_auto_pipeline -m "not integration"` | **295 passed**，0 failed |
| `node scripts/ui_audit.js` | **OK (627 checks)**（HEAD 基线 610，本轮 +17） |
| `node scripts/ui_render_check.js` | 312/314 —— 与 **HEAD 基线逐字相同** 的 2 条 `#/students` 路由失败（另起 worktree 跑 HEAD 复核过），本任务未新增失败 |
| 真实数据投影 | `python temp/measure_knowledge_map.py`（4 门课逐条输出上表数字） |
| 前端绘制基准 | `node scripts/bench_knowledge_graph.js temp/km-payload-pai.json` 与 `--synth 2000 3200` |

### 8.5 刻意**没有**做的事（以及理由）

- **没有删掉正文 `关联:` 解析器**。它是兼容路径：库里 380/61/13 个知识点的关系目前
  **只**存在于正文，删掉等于让现有地图立刻变成孤立点。现在它被明确标为
  `origin="content_hint"`（推断、无向），权威边有位置就优先用权威边。
- **没有伪造主题/层级**。真实 `topics` / `knowledge_memberships` 全为 0，
  投影返回空数组；未归类知识点仍然可见（「未归类」计数 + 未定位组 + 全部在列表视图）。
- **没有把推断边升格成"已登记关系"**。缺 `origin` 的边按推断处理（有断言钉住）。
- **没有修 `#/students` 那 2 条继承失败**（属学生页路由，与本任务无关）。

---

## 9. 阶段 2/3/4 结果（2026-10-13）

阶段 2（导出）、阶段 3（导入与回放）、阶段 4（端到端验收）的实施与**实测**结果，
以及真实数据上的边界（`knowledge_relations = 0`）、基线失败解释、接口变更说明，
统一写在最终验收报告：

- [`knowledge-map-manual-ai-acceptance.md`](knowledge-map-manual-ai-acceptance.md)

关键结论（详见该报告）：

- 手动 AI 复用**同一条** `AIAnalysisService.analyze` 管线；零出站（E2E 用
  `socket.connect` 白名单强制）、零密钥、报告如实标 `provider=user-pasted`。
- 端到端 **53/53**（真实 HTTP），覆盖错误 bundle / 版本 / 未知与重复 request_id /
  错位响应 / 非法 JSON 与 schema / 超长 / grounding 失败 / 部分失败重试 / 重复提交幂等。
- 精简 payload 实测省 **51.5% / 65.3%**（KP 部分）；绘制 **0.79 ms/帧**（380 点 / 255 边）。
- **真实库 `knowledge_relations` / `topics` / `knowledge_memberships` 全为 0**：
  本次交付「关系存在时能正确、有向、可溯源画出来」，**不是**「已建立真实关系体系」。
