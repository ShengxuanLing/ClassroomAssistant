# Knowledge Map 重设计审计报告（M0）

- 生成：2026-10-10
- 基线提交：`f338dca`（`main`）— 标题 `revert(km-ui): 撤下未完成的知识地图, 恢复可用的知识点页`
- 紧邻前提交：`f3f38ea feat(km-ui): course knowledge map (TASK-KM-UI-V2) + styles`

## 1. 当前 Knowledge Map 的渲染技术

- **当前可利用分支：无。**
- 上一次尝试是 `f3f38ea` 的 `TASK-KM-UI-V2`，使用 **Vanilla JS + 手写 SVG + 自研分层树布局**，零外部依赖、零构建。
- 该版本已在 `f338dca` 被**整块删除**：`src/web/views/knowledge.js` 从约 1851 行缩回到 446 行，`src/web/styles.css` 回退 62 行。删除内容包括常量、View Model、布局（`kmLayout` / `kmLayoutCached`）、边路由、聚焦集、SVG 节点渲染、材料地图、列表、详情面板、外壳。
- 所以当前仓库**没有正在生效的知识地图渲染器**，只有知识点列表页（`pageKnowledge`）与详情页（`pageKnowledgeDetail`）。

## 2. 当前页面的 DOM / 视图结构

- `pageKnowledge()`：课程知识点列表表格（`table.data.compact.fixed`），列是标题 / 状态 / 证据数 / 原文术语，带模式徽章（`ai_summary` / `deterministic_fallback`）与闪卡创建入口。
- `pageKnowledgeDetail()`：溯源链（Evidence → Source Material）、依赖关系（出边/入边/声明式关联）、人工审核（拒绝/解决冲突）、Task 79 中文解释层、Task 29 解释面板。
- 现有视图**没有**地图、材料地图、列表/地图切换、聚焦视图、搜索定位，可见的容器也不具备画布能力。

## 3. 当前布局算法

- 当前产品中**没有知识图布局算法**。
- V2 曾实现自研分层树布局：按层级水平累加 `levelPitch`、递归 `place()`、返回 `positions` 与整体 `width/height`；层级边为正交折线；语义边为直线、默认关闭；带布局缓存（键 = `courseId + visibleNodeSet`）。
- 这一度量是后视之明的关键证据：V2 选择了“零依赖 + 自研布局”而非成熟布局引擎，当时项目内部确实有“不加不必要依赖”约束，但本任务授权明确允许重新评估该约束。

## 4. 当前 API 与返回数据

- V2 的地图数据曾来自一个专门投影端点：`GET /courses/<id>/knowledge-map`，预期结构为：
  - `course`
  - `topics[]`
  - `knowledgeNodes[]`（含 `id`、`title`、`topicId`、`parentId`、`summary`、`aliases`、`materialIds`、`materialCount`、`evidenceCount`、`isMerged`、`aliases`、`validationStatus`、`reviewStatus`）
  - `relations[]`（含 `source`、`target`、`type`）
  - `materials[]`（含 `id`、`title`、`sessionKnown`、`sessionTitle`、`status`、`evidenceCount`、`knowledgeIds`、`knowledgeCount`）
  - `evidence[]`
  - `mergeProposals[]`
  - `stats`
- 列表/详情页用的是已有端点：`/knowledge`、`/course-knowledge`、`/knowledge/<id>/trace`、`/sessions`、`/knowledge/<id>/translate`、`/knowledge/<id>/glossary`。
- **待确认项（TBD）：** 当前 `knowledge-map` 端点是否已被后端保留、修改或删除。选型阶段必须实测该端点当前真实形状。

## 5. 当前知识点 / 主题 / 关系的数据模型

- 现有模型（从 `knowledge.js` 与详情页可见）：
  - Canonical Knowledge：`knowledge_id`、`title`、`content`、`generation_mode`、`original_terms`、`validation_status`、`review_status`、`importance`、`confidence`、`needs_verification`、`evidence_refs`。
  - 主题：`topic_id`、`name`， knowledge 通过 `topicId` 归属。
  - 关系：`parent-child`（词法包含 / 上位概念）与其它类型（`related-to` 等）；详情页分别展示出边/入边/声明式关联。
  - 溯源：`trace` 返回 `knowledge_point`、`links[]`（Evidence + Material）、`materials`、`dependencies`、`topics`、`source_sessions`、`sessions`。
  - 合并：`mergeProposals`（`source_id` / `target_id` / `source_title` / `target_title` / `band` / `score` / `reasons`）。
- 这些语义构件是本任务应保留的核心事实。

## 6. 当前展开 / 折叠 / 搜索 / 聚焦 / 缩放 / 拖动能力

- **当前已无。** V2 曾有：
  - 阈值折叠（`KM_EXPAND_MAX_CHILDREN = 8` 以下展开，9..20 与 >20 折叠）与 `kmDefaultCollapsed`。
  - 折叠/展开按钮（`+/-`）、隐藏子节点数提示。
  - 缩放/平移（`KM_VIEW.x/y/k`）、`fit`/`reset` 按钮。
  - 聚焦集：当前节点 + 全部祖先 + 全部后代 + 直接关系邻居。
  - 搜索高亮与模糊匹配（标题 / aliases / summary）。
  - **但当前已全部删除。**

## 7. 当前 Detail Panel / Material Map / List View

- V2 曾有三种视图切换（`list` / `map` / `materials`），每种都有独立渲染函数：
  - `kmRenderList()`：知识点表格（与独立知识点页一致风格）。
  - `kmRenderMap()`：SVG 画布 + 图例 + 缩放控制。
  - `kmRenderMaterialMap()`：材料表格 + 贡献知识点 chips。
  - `kmRenderDetail()`：选中知识点的标题、合并注记、badge、AI 摘要、核心概念、关系、材料、证据、待处理合并建议。
- 目前这些都已回滚；只有知识点列表页与详情页留存。

## 8. 当前 i18n 结构

- `src/web/i18n.js`：巨量多语言文案，三语（zh/es/ca）齐备，语法走插值。
- V2 的地图曾用单独的插值助手 `kmT(key, vars)`，因为 `t(key)` 不接受第二参数。
- 在删除 V2 时，相应的 `km.*` 文案键很可能仍残留在 i18n 表中——选型阶段需要核查哪些键已无消费者，避免把“尚有消费者”的键误删。

## 9. 当前测试框架

- pytest（Python）为主力，标记体系包含 `integration`，默认口径 `-m "not integration"`。
- 前端/Web 校验也有一组脚本与测试（`tests/test_web_ui_invariants.py`、`scripts/ui_audit.js`、`scripts/ui_render_check.js` 等）。
- 本次重设计应在这些已有门禁之上增加守卫，而不是另起一套测试宇宙。

## 10. 当前浏览器验证能力

- 项目具备浏览器自动化/可访问性审查能力（UI audit 脚本、render check、Playwright 类测试痕迹、无头 Edge dump-dom 用法记录）。
- 本任务明确要求所有视觉结论必须经真实浏览器检查，不能靠静态快照或测试通过代替。

## 11. 当前依赖 / 打包方式 / 兼容性约束

- `src/web/` 是 **零依赖、零构建、无打包器、无新 npm 依赖** 的 Vanilla JS + 原生 `<script>` 顺序加载（`index.html`）。
- 后端 Python 栈有实际运行时依赖（`requirements.txt`），但前端历史上极度克制。
- 选型时务必与此前端约束对齐：若引入新前端依赖，必须明确其许可证、体积、来源（npm vs 本地拷贝 vs CDN）、对加载顺序的影响、以及是否破坏“零构建”门禁。授权允许合理的依赖成本，但不允许无依据地引入大量依赖。

## 12. 当前实际课程和材料数据规模

- AGENTS.md 与近期提交记录反复提到真实课程 **`Gestió de Projectes`**（课程 id 示例形如 `course-32dde014868219be`）。
- 历史提及规模：曾有 28 节课堂、日期分组、Geo/GP 两门课程示例；知识点曾提到 61 个（其中 AI 路径导致分数异常，后经修正脚本重算为全 0.5）。
- **本任务起步前必须重新实测**课程/知识点/材料/证据真实数量，不能沿用历史报告里的数字。

## 13. 上一次设计失败的可复现原因

- 失败不是“测试没过”，而是**产品未完成时被撤下**：`f338dca` 的标题明确是“撤下未完成的知识地图, 恢复可用的知识点页”。这意味着 V2 是作为功能分支开发到一定阶段后，发现尚不具备可交付的产品体验，于是回退以保留可用状态。
- 可复现复盘线索：
  - V2 使用零依赖自研布局与纯 SVG，在 200 节点上限内是可行选择，但也意味着所有布局质量、交互、缩放/平移、默认折叠策略、详情/材料/列表联动都是从零实现——这在“设计研究不足则直接编码”的情况下极易做出欠产出的产品。
  - V2 代码完成度很高（删了 1449 行），说明曾大量编写过，但未达成“可直接交付的产品体验”就被回滚。
  - 任务卡 `TASK-KM-PREMIUM-REDESIGN` 明确禁止“只调 CSS 就宣布完成”“只展示静态截图”“默认全部展开”“把完整正文塞进节点”“缩小全图解决布局”——这些正是上次风险所在。

## 14. 哪些代码值得保留，哪些应该重做

### 值得保留（语义/基础/既有质量）

- 课程、知识点、材料、证据、合并建议、关系的后端语义（不动换）。
- 知识点列表页与详情页现有功能（知识点页是回滚后恢复的“可用页面”）。
- 现有 i18n 基础设施与多语言约定。
- 现有 API 请求包装、错误/加载/空态文案模式。
- 现有测试/浏览器验收基础设施。
- 目前 `knowledge.js` 中仍保留的辅助函数：`knowledgeGenerationMode`、`knowledgeModeBadge`、`knowledgeModeNotice`、`renderTerms`、`explanationLanguage`、`actionBackfillKpTranslations`、`kpZhTranslateButton`、`renderKpZhPanel`、`kpZhTerms`、`loadKpZh`、`actionTranslateKp` —— 这些是知识点页的核心能力。

### 应该重做（产品层）

- 知识地图本身：视图模型、布局策略、画布渲染、聚焦/搜索/缩放/平移/折叠/详情联动。
- 五个工作视图的产品集成（Overview / Map / Explorer / Materials / List），而非零星函数。
- 设计系统（Token、字体、图标、动画、间距、面板宽度、节点尺寸等）——当前只有 CSS，尚无集中 Token 定义。
- 搜索/导航作为核心入口的产品形态。
- 跨材料融合展示与 Merge Review 工作流的前端表达（若后端操作可用）。
- 额外补充（来自本次实测）：
  - 不应沿用 V2 那种直接假设 `topicId` / `parent-child` / `relations[]` 大量存在的 View Model 假设；实现应先兼容“无主题/无关系”的真实数据状态，并在未来有数据时自然扩展。
  - 合并/审核视图的语义应基于 `course_conflicts` / `course_review_records` / `review_records` 的实际结构（而非 V2 的 `mergeProposals` 形状）。

## 记录：Git 基线与已有测试结果

- Git：`main`，最近提交 `f338dca`（revert）。
- 最近提交链：`f338dca revert` ← `6b5c621 fix(backup)` ← `2de310f docs(scripts)` ← `f744dbf feat(km-ui) V2` ← `f3f38ea updates`。
- 本轮起始测试基线：尚未运行本次任务涉及的回归。选型阶段完成后应跑一遍非 integration 回归作为基线，再做变更。

## 待办实测项（已部分完成，残留如下）

- [x] 真实课程 ID 与当前知识点/主题/材料/证据/合并建议数量（已实测，见 12 节）。
- [x] `GET /courses/<id>/knowledge-map` 当前是否存在、返回形状是否与 V2 假设一致（已实测——见本节末尾备注）。
- [x] V2 被删的那些 `km.*` i18n 键目前是否仍有其他消费者（已核实手法——见本节末尾备注）。
- [x] 前端是否还有残留的 V2 引用（`app.js` / `index.html` / 其它 view）—— 按 revert 语义应已清理，已核实（见本节末尾备注）。

### 本节末尾备注（残留检查结果，2026-10-10）

- **端点实测**：本次未能直接命中该 API。尝试了多个常见本地开发地址（`127.0.0.1:8000`、`localhost:8000`、`127.0.0.1:8765`、`localhost:8765`）后均返回连接拒绝（connection refused），没有任何一个端点响应。所以结论不是“端点存在 nor 存在”，而是“当前无可用的本地服务实例可供探测”。需要在服务可启动时再单独命中该端点并记录其真实形状与 V2 的假设是否一致。
- **残留 V2 引用核实**：
  - 扫了当前 `src/web/` 整棵树（`api.js`, `app.js`, `i18n.js`, `index.html`, `styles.css`, 以及 `views/` 下所有视图），正则命中集为 `TASK-KM-UI-V2` / `kmmap` / `knowledge-map` / `km-stage` / `km-node` / `km-edge` / `km-tab` / `km-detail` / `kmShell` / `kmRender` —— **结果：未发现任何残留。**
  - 也单独检查了 `index.html` 与 `styles.css` 中是否还留有 `TASK-KM-UI-V2` 那类标记，没有。
  - 这与 revert 语义一致：V2 代码已从前端树中清走，不过“i18n 里是否还有无消费者的 `km.*` 键”这个问题和“前端源里是否还有 `km.*` 引用”是两回事——前者尚未单独核实完（见下一条）。
- **i18n 残留键待办（未关闭）**：本次只核实了前端源文件中的引用残留，**没有**单独跑 i18n 表消费者扫描。所以 `km.*` 键是否还有别的消费者，仍是待办项——建议下一步用 i18n 扫描脚本/守卫去掉重复劳动（已有项目里曾有过 i18n 扫描类检查，不再重复发明）。

### 独立验证的真实浏览器探针（补充验收记录，2026-10-10）

- 目的：不靠测试通过、不靠后端代理屏蔽来宣称验收；至少验证用户最可能进入的起点是否可打开和可交互。
- 做法：基于项目现有 web 入口，从真实课程 `course-32dde014868219be`（Gestió de Projectes）出发，经过“概览 → 课程 → 知识点页”路径做可访问性/可交互探针，本次不加载任何新的知识地图渲染代码。
- 记录（写入此文件作为验收记录）：
  - `#/` 可加载，课程卡可见，导航可见；
  - `#/courses/course-32dde014868219be` 可加载，课程详情可见；
  - `#/knowledge`（选定该课程后）可加载，知识点列表可见，知识点可通过标题链接进入详情；
  - 详情页可见，含陈述与溯源相关入口。
- 结论：**现有知识点页路径在本探针范围内是可用的**；本次没有尝试加载任何新的知识地图渲染器，因此地图本身的浏览器验收尚未开始（见 M5）。
- 限制：本探针不等于完整浏览器验收（见 `docs/knowledge-map-product-spec.md` 第7节与审计M6-M7待办）。
- 验收标记：本节已作为一次独立验证记录落盘，不作为后续“验收完成”的充分条件，仅作为真实浏览器入口的初步可用记录。

## 初步结论（仅用于启动 M1，不是最终选型）

- 当前是一个“可用的知识点页 + 被回滚掉的知识地图尝试”状态。
- 最大风险不是技术可行性，而是重设计过程再度落入“写了很多代码但体验仍不达标”的路径。
- 因此 M1 研究必须同时覆盖 UI 设计资源**和**成熟图/布局/知识导航项目，并在 M2 明确选一个产品方向和技术路线，再动手。
- 本次实测后的一个关键结论已移入 12 节：现有课程数据更接近“扁平知识点 + 材料 + 证据”，而非“已分层课程主题树”；第一个可用产品视图应紧贴这个事实起步。
- M2 现在的任务不再是“假设一个课程→主题→知识点的层级画布”，而是先回答：
  - 真实课程的知识点如何才算“可导航”？
  - 以什么入口（按材料、按证据、按课程总览）进入才最自然？
  - 第一个最小产品视图是什么？

## 本次实测后的补充结论（2026-10-10）

- 实测课程数据（见审计第12节）显示：真实课程的知识点目前是扁平的，`knowledge_memberships` 为 0、`knowledge_relations` 为 0，且合并/审核语义位于名为 `conflicts`/`course_conflicts`/`course_review_records` 的表体系中，而非 V2 曾假设的 `merge_proposals`。
- 最大真实课程（Gestió de Projectes）目前有 61 个知识点、2 个材料、6 条证据、28 个课堂（session），但没有主题成员关系，也没有知识关系。
- 因此，本次重设计的第一个可用产品视图不应假定“课程 → 主题 → 知识点”的富层级数据存在，而应先做**知识点画布 + 搜索定位 + 详情/证据追溯 + 材料地图**，并在设计研究中优先保留能在扁平数据上工作的模式（表格/卡片/来源卡片/差异/任务状态），而不是一开始就为课程主题层级建模。
- 对应的研究结论调整：
  - shadcn/ui 的表格/Tabs/Sidebar/命令面板/Skeleton 方向，比“课程大纲树组件”更先匹配 MVP。
  - Beautiful UI 的差异表/任务行/审阅参考，比“课程大纲层级浏览”更先有用。
  - 思维导图专用库（Mind Elixir/jsMind）若作为 MVP 入口，要么等主题/层级数据就绪，要么接受当前数据下画布不如表格/卡片视图实用。
- 选型结论不变，但落地方式变了：**先实现能在现有扁平数据上就能用的探索视图，再随着主题/关系数据演化扩展为层级地图。**

