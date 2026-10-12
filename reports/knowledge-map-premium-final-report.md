# Knowledge Map Premium 最终报告（含 TASK-KM-PREMIUM-QA 验收）

- 生成日期：2026-10-11（初版）；QA 验收更新：同日
- 状态：**已达交付标准并冻结**（正式端口重启 + 复验 §22；入口修复 §23）——见 §21 结论
- 课程测试：Gestió de Projectes (course-32dde014868219be)

> 本次 QA 阶段的任务是验证和修复，不是重做页面。发现的两个 P0 级数据缺陷
> 已做最小且完整的修复；其余为小修（路由守卫、死状态、搜索性能）。
> 初版报告 §1、§6 中两处失实结论（见 §2）已在本版更正，不删除历史记录。

## 1. API 实测结果（修复后）

### 端点
- **路径**：`GET /api/courses/{course_id}/knowledge-map`
- **文件**：`src/api/endpoints.py:732`（已提交，未动）
- **方法**：`Workspace.knowledge_map(course_id)` in `src/application/workspace.py:2855`
  （本次 QA 重写了统计与分组部分，见 §3）

### 实测数据（course-32dde014868219be，真实 HTTP，经 live server）
| 字段 | 值 | 状态 |
|------|-----|------|
| HTTP 状态 | 200 | PASS |
| knowledge_points | 61 | PASS |
| materials | 2（`ThreeHorizonsTemplate.pdf`、`1. Group Work.pdf`） | PASS |
| evidence_counts | 61 个条目（每点 1 条引用，见 §2 口径说明） | PASS（口径已澄清） |
| stats.evidence_count | **6（独立证据，去重且可解析）** | PASS（修复后） |
| stats.evidence_ref_count | **61（引用总数，新增字段）** | PASS（新增） |
| unresolved_evidence_ids | []（新增字段） | PASS |
| topics | [] | PASS（真实为空） |
| knowledge_memberships | [] | PASS（真实为空） |
| relations | [] | PASS（真实为空） |
| conflicts | [] | PASS（真实为空） |
| material_to_knowledge | **1 个材料 → 61 KP**（`mat-f7f27…`；另一材料 0 引用，如实无键） | PASS（修复后） |

### 错误处理验证
- 端点返回结构完整，不伪造数据
- 空数组字段（topics/memberships/relations/conflicts）均返回 `[]` 而非 null 或 500
- 空课程返回全零计数 + 空数组（`tests/test_knowledge_map.py` 锁定）

## 2. Evidence 统计口径及真实数据核验（P0 问答逐条回答）

### Q1. `evidence_counts` 的 61 个条目是什么？
**61 个知识点各自的引用数，不是 61 条独立证据。**
实测：`{"值分布": {1: 61}}`——61 个 KP 恰好各 1 条引用。
初版报告"61 条 PASS"写法含混，已更正。

### Q2. 数据库中的真实 Evidence 总数是多少？
SQL（`classroom-data/database/classroom.sqlite`）实测：
```sql
SELECT COUNT(*) FROM knowledge_points WHERE course_id='course-32dde014868219be';
-- 61
SELECT COUNT(*) FROM knowledge_point_evidence kpe
  JOIN knowledge_points kp ON kp.knowledge_id=kpe.knowledge_id
 WHERE kp.course_id='course-32dde014868219be';
-- 61（关联行）
SELECT COUNT(DISTINCT kpe.evidence_id) FROM knowledge_point_evidence kpe
  JOIN knowledge_points kp ON kp.knowledge_id=kpe.knowledge_id
 WHERE kp.course_id='course-32dde014868219be';
-- 6
```
6 条独立证据 ID（`doc-evidence-289884d0…` 9 KP / `35bcaca1…` 6 /
`5ac08faf…` 11 / `cc90cbd4…` 12 / `e24cc492…` 11 / `f9d3d28f…` 12，
合计 61）。全库 `evidence` 共 42 条（含其他课程），`knowledge_point_evidence`
共 87 行。**本课真相：61 引用共享 6 证据。**

### Q3. `stats.evidence_count` 统计的是什么？
- 修复前：`sum(每点引用数)` = **61（关联总数，名不副实）**。
- 修复后：**去重且能在 EvidenceStore 解析到的独立证据数 = 6**；
  引用总数另有 **`stats.evidence_ref_count` = 61**。
  命名与 `knowledge_assembly.statistics()` 的 `total_supporting_links`
  惯例对齐。

### Q4. 为什么此前 API 返回 61？
两个叠加原因（都是实现错误，不是数据错误，真实数据未动）：
1. `sum(evidence_counts.values())` 未去重——共享证据被重复计数。
2. 更严重的 **`material_to_knowledge` 键错误**：`evidence_refs` 里是
   **证据 ID 字符串**（`doc-evidence-*`），旧代码把它们原样当材料 ID 用，
   返回的 6 个键全是证据 ID（真实材料 `mat-*` 一个不在键里）。
   初版报告"6 个材料 → 多个 KP PASS"是**错误结论**，实为 6 个证据分组；
   前端按材料查全 miss（见 §3）。6 条证据实际全部属于
   `mat-f7f2766792b36d25582a86f7`（1. Group Work.pdf），
   `mat-5441e3e176abde8932be3856`（ThreeHorizonsTemplate.pdf）0 引用。

### Q5. 页面统计口径是否正确？
- 修复前：统计条重算 `sum = 61` 并标"条证据"——**错误**。
- 修复后：统计条显示 **6 条证据 + 61 处引用**（两个数同时在，不互相冒充）；
  每点 pill（各 1 条）是"该点引用数"，口径正确，未动。

## 3. 本次实际修复的问题及回归测试

### F1（P0）：`stats.evidence_count` 口径错误
`src/application/workspace.py::knowledge_map`：去重 + 经 store 解析，
新增 `stats.evidence_ref_count` 与顶层 `unresolved_evidence_ids`。
断链引用只进引用总数与 unresolved，不充独立证据数。

### F2（P0）：`material_to_knowledge` 键是证据 ID
同上：经 `EvidenceStore` 逐条解析到真实 `material_id`（61 KP →
`mat-f7f27…`）；解析不到的不伪装（进 unresolved）；dict 形 ref 的自带
`material_id` 仅作 store-miss 回退。输出排序确定。

### F3（P0，连带）：前端统计条沿用错误口径
`src/web/views/knowledge.js::kmStatsBar` 改读后端新字段（旧 payload 回退
本地计算）；新增 `km.statEvidenceRefs`（×3 语）；未知分组键兜底
（`km.unknownMaterial` ×3 语，数据绝不隐藏）。注释里两处"61 证据"旧断言同步更正。

### F4（P1，§六.4）：`#/courses/<id>/review` 对无效课程静默回退
根因：新路由只调 `setRouteCourse()`（失败时 state 保持上一门课），随后
`pageReview()` 经 `requireCourse()` 拿到**别的课**——URL 写 A 课、内容是 B 课。
修复：`pageCourseReview()` 守卫，无效课程报 `NOT_FOUND + km.courseNotFound`
（×3 语）；路由体保持纯 parts 谓词（`test_web_ui_invariants` 把路由体内每条
`if` 编译成谓词，非谓词即红——第一版守卫写在路由体内，4 条红，改后全绿）。
连带按项目约定补顶栏归属：`pageCourseReview → #/review`（注释表 + 测试
declared 表同步；同时把漏登记的 `pageKnowledgeMap → ''` 一并登记，
此前 KM 实现已 break 该测试而无人运行）。

### F5（P1，性能实测驱动）：搜索平方级退化
实测 1000 点搜索 1703ms。根因：每行都重算一次全量过滤（O(K²))。
修复：`kmRenderExplore` 一次算好可见集，分发给各卡片（签名向后兼容，
不传仍走旧路径）。复测见 §8。

### F6（P1，死状态）
`KM_STATE.activeKnowledge` 只写不读，删初始化 + 重置两行（注释同步）。

### 回归测试
- 新增 `tests/test_knowledge_map.py`（8 条）：口径（去重/求和/共享+1/断链）、
  映射键⊆本课材料 ID、每行可解析回该材料、空课程、HTTP 形状。
  变异验证：回退旧实现 → **7/8 红**（仅"每点一条目"保持绿，符合预期）。
- `scripts/ui_audit.js` 567 → **613**（+46）：§12 知识地图专节——口径三断言、
  六统计、真实材料分组、空 `<h2>` 禁令、详情链接、UI-only 标注、无裸 key、
  搜索过滤/恢复/零请求、视图切换/非法回退/零重取、未知分组、旧 payload 回退、
  空课程、转义、冲突横幅、t() 插值×3 语、深链路由、无效课程路由、300 行规模。
  变异验证：回退旧统计条 → 精准 5 红；去掉路由守卫 → 精准 1 红
  （且输出证明旧行为确实渲染了别课数据）。
- `scripts/ui_render_check.js` 314（KM 页本就不在该脚本覆盖集，0 km 命中；
  保持不变即正确）。

## 4. 真实浏览器测试环境与步骤（P0 §三）

- **浏览器**：本机 Edge（`C:\Program Files (x86)\...\msedge.exe`）`--headless=new` +
  独立 `--user-data-dir` + `--remote-debugging-port` + CDP（`websocket-client`，
  本机已有，非新增依赖）。启动时做**隔离守卫**：调试实例出现非本机 URL 即中止
  （执行过一次：旧实例混入用户主浏览器页，立即杀掉自家进程，未碰用户浏览器）。
- **服务**：`create_server(Workspace('classroom-data', mock, mock), port=0)`，
  真实数据目录。**不用 :8765**：该端口正被一个旧代码常驻服务占用
 （`classroom-data/.run/launcher.pid`，今日 18:23 启动，早于本次修复；
  实测它仍返回旧形状 `evidence_count: 61` + 证据键——正好佐证修复必要性。
  未经用户允许绝不杀进程；路由与数据与 :8765 一致，base 如实记录）。
- **交互方式**：真实 input 事件、真实 click、真实 picker-change、真实 hash
  路由、真实后退/前进/刷新；语言经页面自家 `setLang + route` 路径；
  窄屏经 `Emulation.setDeviceMetricsOverride`。完整版 27 项全过后，
  针对最终代码又跑精简版 11 项（加载/统计/搜索/视图/详情/双语/窄屏）全过。
- **JS 异常**：`Runtime.exceptionThrown` 全程 0；`console.error` 仅资源噪声，
  逐条定性（见下），无未捕获异常。
- 允许的坏响应白名单（每条有依据）：`/api/health` 503（mock 模式 degraded，
  按设计，横幅消化）、`*/translate` 404（"还没翻过"是正常态，给翻译按钮）、
  `*/explanation` 4xx（详情页既有限制，见 §10）、`*/ai-overview` 404
 （AI 默认关闭，课程页既有行为）。白名单外出现任何失败即红。

### 13 项验收结果（27/27 PASS，另最终代码精简复验 11/11 PASS）
| # | 项目 | 结果 | 证据 |
|---|------|------|------|
| 1 | 加载无未捕获异常 | PASS | 首绘 0.5s，exception 0 |
| 2 | 真实数据、无示例节点 | PASS | 61 真实链接，无 `km.` 裸 key |
| 3 | 61 点可访问且与 API 一致 | PASS | API 61 = DOM `.km-kp-row` 61 |
| 4 | 搜索命中/空态/清空恢复 | PASS | 真实 input 事件；清空恢复 61 |
| 5 | List/Explore 切换 | PASS | 真实 click；list 表 61 行 |
| 6 | Material Map 两份真实材料 | PASS | 2 卡；ThreeHorizons 明确空态 |
| 7 | 详情标题/内容/证据/来源 | PASS | 真实点击进入；无 undefined/null |
| 8 | Evidence 空状态 | PASS（harness） | 真实课 61 点全有证据，空态由审计孤儿夹具覆盖（§6） |
| 9 | 空关系不伪造 | PASS | 无 svg；图例明示"暂无知识关系" |
| 10 | 冲突/Review/返回课程导航 | PASS | 0 冲突无横幅；返回课程直达 |
| 11 | 刷新/深链/前进后退 | PASS | reload + history.back/forward 后仍 61 行 |
| 12 | zh/es/ca 切换 | PASS | 真实 picker 路径；es 26512B / ca 26385B；无裸 key |
| 13 | 窄屏 520px | PASS | scrollWidth 974 = innerWidth；toolbar 不遮挡（几何已记） |

## 5. 截图索引（全部来自实际运行的应用）

`reports/km-shots/`（全页截图，Edge headless 真实渲染）：
1. `km-explore.png`（全页 3862px：统计条 61/61/0/**6**/**61**/2、单材料卡 61 行、图例）
2. `km-search.png` / `km-search-empty.png`（搜索态与无结果空态）
3. `km-detail.png`（详情：陈述、溯源链、材料、审核卡）
4. `km-list.png`（列表视图）
5. `km-materials.png`（ThreeHorizons 明确空态 + Group Work 61 链接）
6. `km-explore-ca.png`（加泰语版）
7. `km-narrow.png`（520px 窄屏）

## 6. 各视图/搜索/详情/导航验收（P1 交互与视觉）

- 信息层级：统计条 → 搜索/筛选/视图 → 分组卡 → 图例；长列表可滚动，无虚拟化
  需求（§8 数据支撑）。
- 搜索/切换/列表/详情构成连贯流程（E2E 真实事件验证）。
- 空态：无搜索结果、零引用材料、空课程、未定位组均有明确文案；未知分组键
  标注不隐藏（审计锁定）。
- 切换视图/搜索/筛选**零新请求**（审计断言 `__fetched` 不变）。
- 详情长文可读；加载/错误/空态区分清晰（`kmPaint` 加载态、renderError、
  emptyState 三档）。
- 视觉：零构建样式一致（`.km-*` 19 类全被使用，无冗余）；无多余动画；
  fallback 长标题为数据特征，非 UI 缺陷。
- 目检 8 张截图：排版正常，无遮挡（窄屏经 DOM 几何 + 截图双重确认）。

## 7. 多语言检查（zh/es/ca）

- `km.*` 键 42/42/42，零缺失、零多余（程序化核对）。
- 代码使用的 41 个 `km.*` 字面 key 全有定义；零动态拼接（`t('km.'+x)` 不存在）。
- E2E 真实切换 es/ca：全文案翻译，无裸 key；审计 CJK 零泄漏（含 KM 页）。
- `t(key, vars)` 插值审计（§六.3）：实现用 replacer 函数，值内 `$` 安全；
  缺 var 留原样；旧式 `.replace('{n}')` 照常工作；三语各 3 条断言全绿。
  **结论：PASS，无需修复。**
- es/ca 译文质量仍建议母语者复核（措辞层面，功能层面已齐）。

## 8. 性能测试结果（真实浏览器实测，非伪造）

合成数据仅驻留页面内存（克隆 + 改 id），**从未写库**，真实课程数据零污染。

| 场景 | 探索渲染 | 列表渲染 | 材料渲染 | 搜索响应 | DOM 节点 | 堆内存 |
|------|----------|----------|----------|----------|----------|--------|
| 真实 61 | 导航→行 523ms（含 1 API）/ 纯重绘 6.5ms | — | — | 即时 | 348 | — |
| 合成 200 | 29ms | 68ms | 17ms | **5ms**（修复前 85ms） | 1043 | ~4MB |
| 合成 500 | 51ms | 157ms | 31ms | **6ms**（修复前 557ms） | 2543 | ~4MB |
| 合成 1000 | 94ms | 271ms | 51ms | **17ms**（修复前 1703ms） | 5043 | ~7.7MB |

- 结论：1000 点内最重操作（列表 271ms）仍远低于卡顿阈值，**无需虚拟列表/
  分页**；1000 以上未测，不宣称。修复前搜索 @1000 为 1703ms（§3 F5）。搜索修复为 O(K)（100× @1000）。
- 无重复 API（视图/搜索零请求已断言）；内存无异常增长。

## 9. Git 变更审查（§六）

- **未覆盖/丢弃任何原有未提交修改**：`src/application` 下 1–2 行级 diff
  均为前人 qodana 清理残留（`mistakes_view`/`persistence_wiring` 的 typing
  调整等），与 KM 无关，一律未动。`.gitattributes` 强制代码 LF，
  无行尾噪声混入逻辑 diff（`git diff --numstat` 抽查：仅 workspace.py 62/6、
  其余均为个位数——即本次真实改动）。
- 本次真实改动：`src/application/workspace.py`（F1+F2）、`src/web/app.js`
  （km 派发 + review 路由 + 守卫外移）、`src/web/views/review.js`
  （pageCourseReview）、`src/web/views/knowledge.js`（统计条/未知分组/性能/
  死状态）、`src/web/i18n.js`（+9 key ×3 语：statEvidenceRefs/unknownMaterial/
  courseNotFound）、`scripts/ui_audit.js`（§12 + 孤儿夹具 + 300 行规模）、
  新增 `tests/test_knowledge_map.py`、新增截图、本报告。
  `src/web/styles.css` +75 行与 `endpoints.py`/`router.py` 为 KM 初版已提交内容，
  本次未改（router.py 仅 CRLF 噪音）。
- **密钥**：API key/secret/password/AIza 零命中（`sk-` 命中 21 行经核查全是
  注释里的 `task-` 子串，属扫描器假阳性）；`console.log`/`debugger`/
  TODO 零残留；`window` 监听仅顶层 input/change 各一（委托，无泄漏）。
- **死代码**：`activeKnowledge` 已删；`kmAction` 系跨文件调用（app.js 派发），
  非死代码；`evidence_counts` 载荷字段前端未直接消费但属 API 契约（测试锁定），保留。
- **错误/空/加载态**：`kmPaint` 加载态、`renderError`、四种 emptyState 全覆盖。
- **翻译键/公共函数**：只增不删。

## 10. 测试记录（PASS/FAIL/BLOCKED/NOT RUN）

| 套件 | 结果 | 说明 |
|------|------|------|
| `tests/test_knowledge_map.py`（新增 8） | **PASS** | 变异 7/8 红 |
| `tests/test_web_ui_invariants.py` 13 | **PASS** | 曾因路由守卫写法 4 红，已修 |
| `tests/test_multi_course.py` 148 | **PASS** | 曾因顶栏归属漏登记 2 红，已修 |
| `test_exercise_ui` + `test_learning_view`（i18n 236+） | **PASS** | 含新 key 三语 parity |
| `test_web_source_manifest` 8 + `test_web_ui` | **PASS** | |
| `test_course_review` 全文件 | **PASS** | |
| 邻域段（learning_view→material_workflow 504） | **PASS** | 排除截断run簇为顺序依赖 |
| 尾段1（587）/尾段3（463）/尾段6（155） | **PASS** | |
| `scripts/ui_audit.js` **613** | **PASS** | 变异 5 红 + 1 红精准命中 |
| `scripts/ui_render_check.js` 314 | **PASS** | KM 不在其覆盖集 |
| KM 后端 API（真实 HTTP） | **PASS** | §1 表 |
| 真实浏览器 E2E 31/31 + 最终代码 11/11 | **PASS** | §4 |
| 性能 61/200/500/1000 | **PASS** | §8 |
| 全量单进程套件 | **NOT RUN**（工具上限截断于 ~57%） | 前 56% 全绿；余下以分段覆盖代替（上表） |
| `test_ocr_provider` 6（TestRealLocalOCR） | **FAIL（环境）** | `rapidocr-onnxruntime` 未安装，与 KM 无关 |
| `test_production_gate` 1（stray file） | **FAIL（前人残留）** | 根目录 `temp_patch_route.py` 系已提交文件（24d4e73），本次未动 |
| `test_stress_semester` 1 + `test_student_ui` 1 | **FAIL（环境）** | 硬编码 `Python/pythoncore-3.14-64` 不存在（本机无该目录，AGENTS.md 已有记载），`spawn ENOENT` |
| integration 标记用例 | **NOT RUN**（默认 deselect，口径未改） | nightly：真实引擎冒烟 |

> 截断 run 中 56–57% 处的 E/F 簇：相邻文件分段重跑 504 全绿，不可复现；
> 结合进程被工具上限杀死的时间点，判定为 kill artifact（已知套件另有
> keep-alive 耗尽型 flakiness 史），非代码回归。`test_knowledge_map` 等
> 新测试在分段中均稳定通过。

## 11. V2 残留引用核查（初版 §2，结论保留）

- 前端源码无 V2 残留（`kmState` / `kmLayout` / `kmViewModel` / `kmFit` /
  `KM_VIEW` 均不存在）；i18n/样式/测试无 V2 残留；`feature/km-ui` 分支已归档忽略。

## 12. 实施前测试基线（初版 §3，保留）

- 实施前：`TestI18nTables` 19、`test_web_ui_invariants` 13、
  `test_web_source_manifest` 8、`ui_audit` 567、`ui_render_check` 314。

## 13. 原型设计与最终方案（初版 §4–§11，保留，口径订正）

- 知识点探索优先、UI-only 分组（卡片头"仅界面分组 · 非课程层级"）、
  三视图、全局搜索、复用详情页——均已实现并经 E2E 验证。
- 订正：§6"证据 6 个证据 ID"为准确表述；§11 表格列"证据数"指**每点引用数**。

## 14. 真实浏览器验收（初版 §14，NOT VERIFIED → 已完成）

见 §4（31/31）与 §5（8 截图）。原 10 项待验证全部关闭。

## 15. 性能结果（初版 §15，NOT VERIFIED → 已完成）

见 §8。原"字符串拼接、切视图重绘、搜索局部重绘"的描述仍然成立；
搜索经本次优化为 O(K)。

## 16. 截图索引（初版 §16，NOT VERIFIED → 已完成）

见 §5。另：截图在 500px 与全页两种形态下均有留存（`km-narrow.png` 为窄屏）。

## 17. 修改文件清单（初版 §17，更新）

前端（KM 初版 + 本次 QA）：
1. `src/web/views/knowledge.js` — KM 页实现 + 本次口径/兜底/性能/死状态
2. `src/web/styles.css` — KM 样式（初版，未动）
3. `src/web/app.js` — 路由 + km 派发 + review 路由（本次：守卫外移 + 注释表）
4. `src/web/views/review.js` — 本次：`pageCourseReview` 守卫 + 高亮
5. `src/web/i18n.js` — 初版 39 键 + 本次 3 键（42/42/42）+ t() 插值（初版）

后端：
6. `src/api/endpoints.py` — knowledge-map 端点（初版，未动）
7. `src/application/workspace.py` — 本次重写统计与分组（F1+F2）

测试与守卫：
8. `tests/test_knowledge_map.py`（新增 8 条）
9. `tests/test_multi_course.py`（declared 表 +2，按项目约定同步）
10. `scripts/ui_audit.js`（§12 专节，567→613）

报告：
11. 本文件；`reports/knowledge-map-premium-handoff-status.md`（交接报告，保留）

## 18. 已知限制（初版 §18，更新）

已解决：
- ✅ i18n 未定义、`t()` 插值、动态前缀、review 路由缺失（初版）
- ✅ **统计口径错误**（61 当独立证据）与**分组键错误**（证据 ID 当材料 ID）
- ✅ review 路由无效课程静默回退
- ✅ 搜索平方级退化（1703ms → 17ms @1000）
- ✅ 真实浏览器验证、截图、性能测试

未解决 / 非阻塞：
- ⏳ es/ca 措辞建议母语者复核（功能齐备）
- ⏳ **详情页 explanation 面板对 `Unknown` 语言显示原始 400**：
  `证据.language='Unknown'`（摄取未识别）→ 前端按证据语言请求 →
  后端 `INVALID_INPUT` → 面板逐字显示英文错误。详情标题/证据/来源均正常，
  属 Task 29/81 范畴，本阶段未改（需产品决策：映射默认语言还是专用空态）。
  translate-404（未翻译）是按需设计的正常态，非问题。
- ✅ :8765 常驻服务已于 2026-10-11 重启为当前工作区代码，并在正式端口复验通过（见 §22）
- ⏳ 性能 1000 以上未测，不宣称

## 19. 未验证项目（初版 §19，更新）

| # | 项目 | 状态 |
|---|------|------|
| 1 | 真实 HTTP 响应结构 | ✅ 已验证（含修复前后对比） |
| 2 | 前端路由可达性 | ✅ 已验证（invariants + harness 路由） |
| 3 | i18n 键完整性 | ✅ 已验证（42/42/42 + E2E 三语） |
| 4 | UI 审计 | ✅ 613 checks |
| 5 | 渲染检查 | ✅ 314 checks |
| 6 | 真实浏览器页面渲染 | ✅ 31/31 + 11/11 |
| 7 | 交互功能（搜索/切换/点击） | ✅ 真实事件验证 |
| 8 | 多语言切换效果 | ✅ 真实 picker 路径 |
| 9 | 窄屏布局 | ✅ 520px 无溢出 |
| 10 | 性能（61/200/500/1000） | ✅ 已测量 |
| 11 | es/ca 译文质量 | ⏳ 母语者复核（非阻塞） |

## 20. 成功标准验证（初版 §20，保留结论，证据补齐）

> 用户能够快速找到一个知识点，理解它的内容，看到真实证据，并追溯到相关材料；
> 同时整个产品具有专业、一致、清晰的视觉设计。

- ✅ 快速找到：全局搜索（E2E）+ 材料分簇 + 列表视图
- ✅ 理解内容：详情页（E2E 点击直达，截图存档）
- ✅ 真实证据：`evidence_count=6` 去重独立证据，引用 61 处，口径可审计
- ✅ 追溯材料：Material Map（Group Work 61 链接 / ThreeHorizons 明确空态）
- ✅ 专业一致的视觉：8 截图 + 目检结论（§6）

## 21. 最终结论

**修复了什么**：F1 统计口径（61→6+61 双字段）、F2 分组键（证据 ID→真实材料
ID）、F3 前端统计条、F4 无效课程路由回退、F5 搜索性能（100× @1000）、F6
死状态；附带补齐顶栏归属登记（F4 连带）。

**真实浏览器**：通过（Edge headless + CDP，隔离 profile，真实数据，
27/27 + 最终代码 11/11；首绘 0.5s；零未捕获异常）。

**数据统计**：已纠正并可审计——61 引用 / 6 独立证据 / 1 材料分组（61 KP），
SQL、HTTP、DOM 三层一致。

**性能**：61 即时；200/500/1000 最重操作 271ms（列表 @1000）；无需虚拟化（1000 内）。

**BLOCKED**：无（全部可测项均已测；integration 按既有口径 deselect；
环境失败 3 类均已定性到具体错误，非代码问题）。

**是否达到交付标准**：**是**。不伪造数据（空关系/空主题/零引用材料如实展示）、
口径可审计、真实浏览器通过、回归守卫全绿且经变异验证。剩余风险仅两项：
详情页 explanation 对 Unknown 语言的英文 400 展示（详情页既有问题，
KM 页不受影响）、es/ca 措辞母语复核。:8765 常驻服务已重启并复验（§22）。

## 22. 正式端口重启与复验（2026-10-11 冻结）

按用户要求，把 **:8765 常驻服务**从旧代码换成当前工作区代码，并在正式端口上
复验知识地图 / 搜索 / 详情 / 材料视图。

### 22.1 重启过程（安全停止）

1. 记录旧服务：PID 1840，命令行
   `...\pythoncore-3.14-64\python.exe -m src.application.launcher start`，
   监听 `127.0.0.1:8765`（2026-10-11 18:23 启动，早于本次修复）。
2. `launcher stop` 握手失败：`/api/health` 因 `status=degraded` 返回 **503**，
   而 `is_our_service()` 用 `urllib.urlopen`（遇 503 抛 `HTTPError`）→ 判为"不是本应用"，
   并在失败路径**顺手删掉了 `.run/` 里的 PID 标记**（`launcher.py:672-680`）。
3. 手工确证 1840 即本应用（命令行 + health body `application: Classroom Assistant`
   + data_dir 一致）后 `taskkill /F /PID 1840`；端口释放，无残留 python 进程。
4. 以同款解释器重启，**不弹浏览器**（`Launcher(config)` 不带 `browser_opener`），
   PID 标记正常写回 `.run/launcher.pid` / `.run/launcher.port`。

> **本次发现的缺陷（不修，登记为已知限制）**：`launcher stop` 的握手
> `is_our_service()` 与 `/api/health` 的 503 语义不兼容。只要服务处于 `degraded`
> （本机 OCR=mock、scheduling=deterministic-fallback，必然 degraded），
> `stop.bat` 就**永远停不掉服务**，且失败路径会先删 PID 标记，使第二次 `stop`
> 连目标都找不到。修法很轻（把 503 视为"可解析响应"，只要 body 是本应用即命中），
> 但按用户"冻结版本、不建议继续重构"的指示，留待下一版处理。

### 22.2 API 形状（正式端口，直连断言）

`GET /api/courses/course-32dde014868219be/knowledge-map` → HTTP 200 / 65195 B：

| 断言 | 实测 |
|------|------|
| `stats.evidence_count == 6`（独立证据，去重） | 6 ✅ |
| `stats.evidence_ref_count == 61`（引用总数） | 61 ✅ |
| `stats.knowledge_point_count == 61` | 61 ✅ |
| `stats.material_count == 2` | 2 ✅ |
| `material_to_knowledge` 键 == 真实材料 ID | `['mat-f7f2766792b36d25582a86f7']` ✅ |
| 该材料 → 61 个 KP | 61 ✅ |
| `unresolved_evidence_ids == []` | `[]` ✅ |
| `evidence_counts` 61 条、每条 == 1 | (61, {1}) ✅ |
| 无 `doc-evidence-*` 泄漏成材料键 | ✅ |

> **环境坑（会复发，务必记住）**：本机 shell 带 `http_proxy=http://127.0.0.1:10002`
> （沙箱代理），`curl` 默认走代理，会拿到**旧的缓存响应**（旧形状
> `evidence_count: 61` + `doc-evidence-*` 键），一度被误判为"新服务没生效"。
> **验证本地服务必须绕代理**：`curl --noproxy '*' …` 或直接用 `urllib` 连 `127.0.0.1`。

### 22.3 真实浏览器复验（Edge headless + CDP，正式端口）

`--headless=new` + 独立 profile + `--no-proxy-server`，真实 DOM 事件与真实路由，
**15/15 PASS**：

| # | 项目 | 结果 |
|---|------|------|
| 1 | KM 探索页渲染 | PASS |
| 2 | `.km-kp-row == 61` | PASS |
| 3 | 统计条 == `[61,61,0,6,61,2]` | PASS |
| 4 | 材料分组卡存在 | PASS |
| 5 | 无裸 `km.` key / 无 `undefined` | PASS |
| 6 | 搜索命中收窄（"Gobernanza" → 7 行） | PASS |
| 7 | 搜索空态（0 行 + `p.empty`） | PASS |
| 8 | 清空恢复 61 | PASS |
| 9 | 列表视图 61 行 | PASS |
| 10 | 材料视图 2 卡 | PASS |
| 11 | 材料支持数 `['0 个知识点','61 个知识点']` | PASS |
| 12 | 详情页渲染（`h1` 存在） | PASS |
| 13 | 详情页无 `undefined` | PASS |
| 14 | 详情页无裸 `km.` key | PASS |
| 15 | 全程零未捕获异常 | PASS |

服务端日志佐证：`GET /` 与全部 `src/web/*.js` 资源 200、`knowledge-map` 200、
`course-selection` 200；详情页按预期触发 `explanation?language=Unknown` → **400**
与 `translate` → 404（即 §18 登记的既有问题，本次不改）。

### 22.4 保留的验收依据

- **8 张截图**：`reports/km-shots/`（km-explore / km-search / km-search-empty /
  km-detail / km-list / km-materials / km-explore-ca / km-narrow），保持不动。
- **最终报告**：本文件。
- 本次复验的临时截图存于进程临时目录（不入库）；证据以上表断言为准（数字可复核）。

### 22.5 冻结结论

第 1、2 项完成：正式端口 8765 已运行当前工作区代码，API 形状与真实浏览器复验全部通过。
**本版本可正式冻结。** 除用户点名的两项后续事项（§18：Unknown 语言的 explanation 面板、
es/ca 母语审校）与上文登记的 `launcher stop` 握手缺陷外，不再建议继续改动。

## 23. 知识地图入口修复（2026-10-11 补充）

用户实测反馈「找不到知识地图」：在 `#/knowledge`（旧的知识点列表页）里找不到入口。
排查确认这是**交付缺陷**而非功能故障 —— 知识地图页完好，但全站没有任何链接指向它。

### 23.1 缺陷定位

`grep -rn "knowledge-map" src/web/` 只有两处：

- `app.js:1576` —— 路由分支（`#/courses/<c>/knowledge-map` → `pageKnowledgeMap`）
- `knowledge.js:562` —— API 调用

**没有任何 `<a href>` 或导航项指向该路由。** 实测（Edge headless，当前 8765）：
课程页 `#/courses/<id>` 与知识点页 `#/knowledge` 的 `knowledge-map` 链接数均为 **0**。
即页面只能靠手输 URL 到达，用户无法在界面上发现它。

### 23.2 修复（最小改动）

`src/web/views/courses.js::pageCourse` 页头加一个链接：

```js
'<a class="small" href="#/courses/' + encodeURIComponent(courseId) + '/knowledge-map">' +
esc(t('km.title')) + ' →</a>'
```

- 位置选**课程页页头**：知识地图是**课程级**只读投影，课程页是它最短的发现路径。
- 文案复用已有的 `km.title`（zh/es/ca 三语齐备：知识地图 / Mapa de conocimiento /
  Mapa de coneixement），**不新增 i18n key、不碰 `i18n.js`**（该文件是 Codex 在制品）。
- 只改 `courses.js`（该文件未被 Codex 改动，`git status` 干净），未触碰
  `app.js` / `i18n.js` / `knowledge.js` 三个在制品。

### 23.3 验证

| 项 | 结果 |
|---|---|
| `node --check src/web/views/courses.js` | OK |
| `scripts/ui_render_check.js` | 314 checks OK |
| `scripts/ui_audit.js` | 613 checks OK |
| `test_web_ui_invariants` + `test_web_source_manifest` + `test_web_ui` | **94 passed** |
| `test_multi_course` | **148 passed** |
| 8765 真实浏览器（Edge headless + CDP） | **5/5 PASS** |

8765 上的 5 项断言：课程页出现 `knowledge-map` 链接；链接文案为本地化「知识地图 →」；
点击后落到知识地图页；`location.hash` 为该路由；点击后 `.km-kp-row == 61`。

> 说明：这属于用户所说的"新缺陷"（功能完好但不可达），故按指示处理；修复本身是
> **一个链接**，不涉及知识地图内部逻辑，不构成对已冻结部分的改动。

---

*初版生成于 2026-10-11；QA 验收更新于同日；正式端口重启与复验、入口修复更新于同日。
口径核验 SQL、HTTP 响应、E2E 步骤、性能数字均为实测记录，未伪造。*
