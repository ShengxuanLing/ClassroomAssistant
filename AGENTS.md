# AGENTS.md - 课堂助手项目规范

## 项目概述

本项目名为「课堂助手」，服务于巴塞罗那自治大学（Universitat Autònoma de Barcelona, UAB）大二学生。
目标是帮助用户处理西班牙语/加泰罗尼亚语授课课程的课堂音频、板书图片、用户与同学笔记，
生成知识点梳理、复习材料和考前复习规划。

## 项目目标（按优先级）

1. **知识点梳理**：从课堂音频、板书、笔记中提取并结构化核心知识点
2. **复习材料生成**：基于知识点生成可复习的摘要、卡片、思维导图大纲
3. **考前复习规划**：根据考试时间和知识点权重，生成科学的复习计划
4. **多语言支持**：输入为西班牙语/加泰罗尼亚语，必要时以中文输出解释

## 输入材料类型

### 1. 课堂音频
- 格式：MP3, WAV, OGG 等常见格式
- 处理方式：使用 Whisper 或类似工具转录为文本
- 语言识别：自动识别西班牙语或加泰罗尼亚语
- 输出：带时间戳的完整转录文本

### 2. 板书/幻灯片图片
- 格式：PNG, JPG, PDF
- 处理方式：OCR 提取文字 + 关键图表/公式保留
- 语言：西语/加泰语为主
- 注意：图表和数学公式可能需要额外处理

### 3. 用户笔记
- 格式：文本文件（.txt, .md）、图片（手写笔记拍照）
- 处理方式：直接读取文本；图片需 OCR
- 语言：可能是混合语言（西语/加泰语/中文）

### 4. 同学笔记
- 格式：同用户笔记
- 注意：需验证信息的准确性和一致性

### 5. 课程大纲/ syllabus
- 格式：文本或 PDF
- 作用：提供课程框架和评估标准

## 多语言处理原则

### 语言识别
- 优先自动识别文本语言（西班牙语 vs 加泰罗尼亚语）
- 当语言不确定时，标记为 [语言待确认]
- 中文仅作为输出语言使用，不作为输入语言

### 翻译与解释
- 术语保留原文，括号内附中文解释
- 示例：capital（资本）、unció（函数）
- 专有名词（人名、课程名）保持原文
- 不随意翻译文化特定概念，标注文化背景

### 语言规范
- 西语使用 ISO 639-1 代码 s 标识
- 加泰罗尼亚语使用 ISO 639-1 代码 ca 标识
- 混合语言文本标注主要语言

## 证据优先与事实核验

### 核心原则
- **所有知识点必须有源材料支撑**：标注来源（音频时间戳/图片页码/笔记段落）
- **不确定内容必须标记**：使用 [待验证]、[推测]、[不确定]
- **绝不编造知识**：如果源材料不足以支撑某个知识点，明确说明
- **冲突信息处理**：当不同来源矛盾时，列出所有版本并标注置信度

### 事实核验流程
1. 提取知识点 → 2. 追溯至源材料 → 3. 验证一致性 → 4. 标注置信度 → 5. 输出

### 置信度等级
- **高置信度**：多个独立来源一致，或明确来自课程大纲
- **中置信度**：单一来源但表述清晰完整
- **低置信度**：单一来源、表述模糊或存在歧义
- **待验证**：无法从当前材料确认

## 音频处理原则

### 转录要求
- 使用最新可用的 Whisper 模型进行转录
- 转录结果保留说话人标记（如能区分）
- 保留口头禅、停顿等非信息性内容（便于后续判断）
- 标注转录置信度低的段落

### 音频分析
- 识别关键词汇和概念
- 标记教师强调的内容（重复、语速变化、停顿强调）
- 提取问题与回答部分
- 识别板书/幻灯片切换时间点

### 音频质量
- 遇到音频质量问题（噪音、断字）时，标注并提示用户
- 不因音频质量问题跳过内容，而是尽可能提取可获取的信息

## 图片/OCR 处理原则

### OCR 处理
- 使用高置信度 OCR 引擎
- 数学公式使用 LaTeX 或 MathML 格式保留
- 图表标题、坐标轴标签必须完整提取
- 表格结构需保持行列关系

### 图片理解
- 板书照片需考虑角度、光线导致的识别误差
- 关键图示（流程图、思维导图）需用文字描述结构
- 复杂图表可能需要用户辅助确认

## 知识点结构化原则

### 知识单元格式
每个知识点应包含：
`
- 概念名称：[西语/加泰语名称]（中文解释）
- 定义：[完整定义]
- 来源：[音频时间戳 / 图片位置 / 笔记段落]
- 置信度：[高/中/低/待验证]
- 相关概念：[关联知识点列表]
- 示例：[如有]
- 备注：[补充说明或待验证事项]
`

### 知识关系
- 明确知识点之间的依赖关系（前置知识、后续应用）
- 标注知识点的课程章节归属
- 识别核心概念与辅助概念的区别

### 知识分类
按类型分类：
- **定义型**：概念、术语
- **方法型**：算法、解题步骤
- **事实型**：日期、事件、数据
- **原理型**：定理、定律、规则
- **应用型**：案例、使用场景

## 复习规划原则

### 复习计划要素
- 考试日期和科目
- 知识点列表及其权重/重要性
- 每个知识点的建议复习时间
- 复习方法推荐（记忆、练习、教授他人等）
- 休息间隔和里程碑

### 规划规则
1. **间隔重复**：根据艾宾浩斯曲线安排复习间隔
2. **优先级排序**：高权重、薄弱环节优先
3. **时间合理性**：不低估复习所需时间
4. **灵活调整**：预留缓冲时间
5. **主动回忆**：鼓励主动测试而非被动阅读

### 复习材料格式
- 知识点卡片（概念 + 定义 + 示例）
- 错题/难点集合
- 模拟题建议
- 知识框架图（大纲形式）概述

## 文件组织规范

### 真相源（唯一）

课堂助手在磁盘上的**唯一**数据真相源是 `classroom-data/`（由 `src/application/data_dirs.py`
的 `DATA_LAYOUT_DIRS` 定义，固定 8 个子目录）。所有受管材料、数据库、日志、备份都
落在它内部，布局与 `src/application/data_dirs.py:381` 的 8 目录**一字对应**。

### 目录结构（classroom-data/）

```
classroom-data/
├── materials/      # 材料注册索引（每门课程一份 JSON）
├── audio/          # 音频材料副本
├── images/         # 图片/板书材料副本
├── documents/      # 笔记 / PDF / DOCX 材料副本
├── database/       # SQLite 数据库（classroom.sqlite，WAL 模式）
├── logs/           # 运行日志
├── backups/        # 备份归档（Task 43）
├── temp/           # 临时暂存区（绝不长期保留）
└── .run/           # 运行期进程元数据（如 launcher.pid）
```

- 用户原始文件**永远不被就地读写**：进入系统的文件必须先复制到上述目录。
- 任何路径拼接必须经过 `safe_join`，拒绝 `..` 与绝对路径逃逸。
- 用户数据**绝不**写入 `src/` 或 `tests/`。

### 遗留目录（已归档，不再使用）

项目早期规范的 `input/` 与 `output/` 已废止：二者只剩一个占位说明 `README.md`、不含任何业务数据，所有数据都已收口到
`classroom-data/`。它们仅保留为空占位，供历史脚本兼容；新代码**禁止**向其中写入。
从旧布局到新布局的逐项映射见 `docs/legacy-input-output.md`。

### 命名规范

- **受管材料**采用「内容寻址 ID」（确定性哈希），由 `register_material` 分配，不依赖
  文件名；原始文件名通过 `filename=` 逐字保留。
- **导出到外部可读文件**（如手工整理的知识点清单、复习规划草稿）时建议：小写 + 连字符
  文件名（`concepto-funcion-explanation.md`）、避免空格与特殊字符。这只是导出建议，
  **不是**受管数据的命名规则。

### 文件格式

- 正文使用 Markdown；表格使用 Markdown 表格语法。
- 编码统一为 UTF-8。
- **导出文件**（手工整理的可读文档）可使用 YAML frontmatter 记录元数据；受管材料的
  元数据由数据库与内容寻址索引承载，**不**依赖 frontmatter。


## 工作流规范

### 处理流程
1. **材料收集**：确认输入材料完整，识别语言和类型
2. **预处理**：音频转录、OCR 提取、格式统一
3. **内容分析**：提取关键信息，识别知识点
4. **结构化整理**：按知识分类和关系组织
5. **核验**：交叉验证，标记不确定性
6. **输出生成**：生成知识点文档、复习材料、规划
7. **用户确认**：关键输出需用户确认

### 协作流程（涉及同学笔记）
1. 收集同学笔记 → 2. 与用户笔记对比 → 3. 识别差异 → 4. 确认共识点 → 5. 标记分歧点

### 迭代原则
- 先骨架后细节：先输出知识点大纲，再逐步补充
- 用户确认优先：关键判断需用户确认
- 可回溯：保留处理过程的中间结果

## 质量标准

### 内容质量
- 知识点定义准确，无歧义
- 来源可追溯
- 语言规范（语法、拼写、术语）
- 格式统一、结构清晰

### 完整性检查
每次输出前确认：
- [ ] 所有知识点都有源材料支撑
- [ ] [待验证]标记了所有不确定内容
- [ ] 语言标注正确
- [ ] 来源信息完整
- [ ] 无遗漏的重要知识点
- [ ] 文件编码为 UTF-8

### 禁止事项
- **绝不编造**没有源材料支撑的知识点
- **绝不猜测**教师意图或课程要求
- **不忽略**不确定性，而是明确标注
- **不混合**不同课程的知识点
- **不遗漏**语言标注（西语 vs 加泰语）
- **不假设**用户已理解某个概念
- **不跳过**源材料验证步骤

## Codex 自主工作检查清单

### 每次工作前检查
1. 确认当前任务涉及的具体课程和材料
2. 确认输入材料的语言类型
3. 确认用户期望的输出类型和格式

### 处理中检查
1. 每提取一个知识点，检查是否有源材料支撑
2. 遇到不确定信息，标记 [待验证] 并继续处理
3. 遇到多语言混合内容，确认主要语言并标注
4. 定期检查输出格式是否符合规范

### 遇到不确定信息时的处理
- **知识不确定**：标注 [待验证]，列出可能的解释，请用户确认
- **语言不确定**：标注 [语言待确认]，提供可能的语言选项
- **来源不明**：标注 [来源待确认]，说明推测依据
- **格式不确定**：遵循已建立的规范，不自行发明格式

### 工作完成前检查
1. 所有 [待验证] 标记是否已处理（确认或保留标记）
2. 输出是否符合质量标准
3. 文件组织是否符合规范
4. 是否有遗漏的重要内容

## 版本与变更记录

- 创建日期：2026-09-07
- 创建者：用户（UAB 大二学生）
- 状态：初始版本
- 后续变更需在此记录

### 变更记录

- **2026-09-20（P0-1 目录规范统一，决策 A）**：`classroom-data/` 确立为唯一数据真相源；重写「文件组织规范」章，以 8 目录布局为准，删除旧 `input/` `output/` 目录树描述；将「YAML frontmatter / 小写连字符命名」降级为「导出文件建议」（受管材料改用内容寻址 ID）。新增 `docs/legacy-input-output.md` 旧布局对照表。`input/` `output/` 各只剩一个占位说明 `README.md`、不含任何业务数据，新代码禁止写入。

- **2026-09-20（P0-2 文档续写与去过期）**：`docs/project-structure-analysis.md`
  （2026-09-15，声称"无 UI / 无 DB / 无 README / 45 模块"）已在文件头加**已过期、
  仅存档**标记，并提示以 `README.md` / `docs/architecture.md` 为准；已从 README
  文档索引移除。`docs/architecture.md` 中「No Third-Party Dependencies /
  No AI/LLM/Network」两处假陈述已改写为「领域层 stdlib-only + 运行时依赖见
  `requirements.txt`，ASR / OCR 本地离线、无云 API」。`docs/final_persistence_report.md`
  头版本号 0.37.0 → **1.0.1**（与 `src/application/workspace.py`、`src/__init__.py`、
  `tests/__init__.py` 三处对齐；正文 13 行矩阵 / 50 KP trace 口径按要求保持不动）。
  `docs/final_report.md` 追加 Task 62–75 汇总章，并**解释 status.md 尾部 5195 与
  终报 4278 的计数口径差**（两者同为 `-m "not integration"` 口径，integration 均不计入）。

- **2026-09-20（P0-3 工程卫生）**：`.gitignore` 补齐 `.venv/`、SQLite 的
  `*.sqlite-shm` / `*.sqlite-wal`、`classroom-data/.run/`、`backups/*.zip`、
  `temp/`、`logs/`、`.pytest_cache/`、`.idea/` 等。`scripts/` 下 61 项一次性脚本
  移入 `scripts/dev-archive/`，根目录只留生产入口（start / health / stop.bat）
  与 4 个检查脚本，并新增 `scripts/README.md` 说明各自用途。根散落的
  `pytest_*.txt` / `knowledge_*.txt` / `task.txt` 等移入 `logs/regression-archive/`。
  `input/` `output/` `audio/` `images/` `documents/` `materials/` `backups/`
  `database/` 八个遗留目录各加占位说明，并**保留**其中的历史文件不删。
  **未建 git 仓**（是否建仓由用户决定），仅做到可直接建仓的状态。

- **2026-09-20（P1-4 分层收敛）**：`Clock` / `utc_now_iso` / `fixed_clock` 的真源
  收口到新建的零依赖叶子包 **`src/common/clock.py`**；`src/application/runtime.py`
  改为兼容 re-export。（曾尝试放进 `src/persistence/clock.py`，反而让 application
  反向依赖 persistence 触发守卫 —— 共享基础件必须放零依赖叶子包。）
  `ValidationStatus` / `ReviewStatus` 枚举收归 `src/models.py`，删除其 `__post_init__`
  内的懒导入；`src/backup/service.py` 不再懒导入 `application.workspace`。
  `tests/test_persistence_layering.py` 从单向扩展为**双向** AST 守卫。

- **2026-09-20（P1-5 测试补强）**：新增 `tests/test_nightly_asr_ocr.py` 与
  `tests/test_concurrent_writes.py`，均带 `integration` 标记、默认 deselected，
  `run_tests.cmd` 默认口径 `-m "not integration"` **未改动**。nightly 命令为
  `pytest -m integration -k nightly`（见 README「Nightly：真实引擎冒烟」）。
  发布门禁改为**读真实值**断言（迁移链 3 条且连续、仓储内省计数 18），
  不再依赖文档关键字。

- **2026-09-20（P1-6 前端零构建拆分）**：`src/web/app.js`（5288 行单文件）按主题拆为 `api.js` / `i18n.js` / `app.js`（核心工具 + 共享 state + 路由 + 启动）+ `views/*.js` 共 12 个脚本，`index.html` 用多个 `<script>` **按固定顺序**引入。仍然零依赖、零构建、无打包器、无新 npm 依赖。加载顺序不可调换：`i18n.js` 必须在`app.js` 之前（`state` 初始化依赖 `pickInitialLang()` / `UI_LANGUAGES`）。已用等价性验证证明路由分支（11 项）、API 路径（21 项）、页面函数（19 项）、顶层定义（119 项）集合完全不变，且无重复定义。`esc()` 转义与原文原样渲染语义未改动。

- **2026-09-20（D1+D3+D4 修补）**：UI 从不渲染 `warning`（三处只画状态 pill）+ 操作日志里每次成功都记 `success:false`。**D1**：状态 pill 不动，在下方加 warning 行——`app.js` 新增 `warningRow()`（原码 `esc()` 逐字渲染 + `t('warn.<码>')` 人话），`views/materials.js` / `views/dashboard.js` / `views/courses.js` 三处一致接入。**D4**：`COMPLETED/SUCCEEDED + 零证据` 加 `warn.emptySuccessHint` 通用句 + 枚举式按码建议（未知码/无码只说通用句，不编原因）；`views/students.js` 材料证据面板空态同样接入（零证据时多查一次材料记录，查不到则不加）。**D3**：`Workspace.process_material` 的日志口径改读作业字典的 `status`（`JOB_SUCCEEDED` / `JOB_FAILED` 常量），修掉"每次成功都记 false 且无 failure 原因"（旧口径读了材料注册表才有的 `processing_status`）。i18n 新增 9 个 `warn.*` key（zh/es/ca 三语齐备）。守卫：`tests/test_pilot_operation_log.py` +2 条（零证据成功记 `true` / 失败记 `false` 且带原因）、`scripts/ui_render_check.js` 202→219、`scripts/ui_audit.js` 374→396。**未动**：D2（扫描 PDF 自动转 OCR）、`failed/重试` 语义、`attempts`。附带发现：前端源码注释里不能写 `t('…')` 形状的文本——`TestI18nTables` 扫源码不剥注释（见 `app.js` 注释注记）。实测确认：`NO_TEXT_DETECTED` 只活在 OCR provider 结果里、从未进入材料/作业字典；后端 `material.warning` 今天的可达集只有 `NO_TEXT_EXTRACTED`（其余三码的映射已就位，切换 payload 即可生效，属后续任务）。

- **2026-09-21（TASK-76 AI 语义理解）**：新增 `src/application/ai/`（config/provider/schemas/prompts/chunking/validators/merge/pipeline/service，标准库 only，零新增依赖），`Workspace.analyze_material_with_ai` + `POST /api/materials/{id}/ai-analyze` + `GET .../ai-summary` + 材料页 AI 面板。默认关闭（`CLASSROOM_AI_ENABLED`），旧链路零改动；失败 422 且 Evidence 安全可 retry；重复分析幂等（`ai-<sha16>` identity + `aikp-*` 确定性 ID）。**零 DB migration**（候选映射到现有 KP 字段，总结活在报告层）。Key 只读进程环境。`tests/test_ai_understanding.py` 67 条 + 1 条 integration live smoke（无凭证 SKIP）。附带修掉 13 条前人红色测试（es/ca `pack.*` 错位、`摘要` 漏译、注释误伤 i18n 扫描器、`pageReviewPack` 顶栏归属漂移、GBK 子进程编码），全部经 revert 实验证明 pre-existing。详见 `docs/task-76-ai-understanding-report.md`。

- **2026-09-21（TASK-77 自动 AI 材料处理，COMPLETE）**：`process_material` 摄取成功后自动触发 AI（`_maybe_auto_ai`，关闭时作业形状不变），`process_session` 逐份串行，`retry_ai_analysis`（=`POST .../ai-analyze`）为幂等重试入口；AI 失败只记 `job["ai"]`（completed/failed/skipped），材料仍 SUCCEEDED。修掉 1 个真实缺陷：AI KP 只进 org 导致重启丢失，加 `AIAnalysisService._mirror_to_structure` 幂等镜像进 `processing.structure`（零 migration）。报告落盘 `materials/ai-reports/<课>/<材料>.json`。Provider 加 429/5xx 有界退避 + 401 直 fail + 超时必传。`bootstrap` 按 `CLASSROOM_AI_*` 装配（`ai_mode` 进 health）。`tests/test_ai_auto_pipeline.py` 26 条 + `tests/test_live_ai_smoke.py` 2 条（integration）。真实验收：agnes-3.0-flash + 真实 PDF + UAB 讲义 → KP 20（AI 17，手工 0），grounded 17/17。详见 `docs/task-77-report.md` / `docs/task-77-real-material-evaluation.md` / `docs/task-77-pipeline-analysis.md`。


- **2026-09-21（Web UI 全局审查 + 修复）**：先做**只读**审查（`docs/ui-review-2026-09-21.md`，21 项：P0×3 / P1×6 / P2×4 / P3×7，每条带 `文件:行` 证据）。用户指令：删掉「复习中心」栏目、其余全修。**P0-1 改为整体删除**（不是修链接）——顶栏 12→11 项，删 `pageCourseReview`（`views/review.js` 357→190 行）、删路由分支、删 33 行孤儿 i18n；后端 `GET /api/courses/{id}/review` 与 `tests/test_course_review.py` 保留（自有契约测试，属另一决策）。其余 20 项修完：**P0-2** 错题详情路由 `parts.length` 2→3 且链接补课程段；**P0-3** `students.js` 的 `route()` 补 `await`（不 await 时 AI 面板画在旧 DOM 上、随即被重绘冲掉）；**P1** 真相轴标签统一（`val.*`/`rev.*`）、`learn.js` 四处 `showBanner(...,'error')`→`'bad'`、`route()` 开头清空横幅、补「新建课程/新建课堂」表单、接入课堂时间线、概览课程卡改「全部课程」并移位；**P2** 新增 `--on-accent`/`--on-ok` 且浅色 `--accent` `#2f6fed`→`#2c68df`（原值在 `--surface-2` 上只有 4.06:1）、23 张表 +104 处 `scope="col"` +23 处 `<caption class="sr-only">`、新增 `confirmDestructive()`（只加在 `review-reject`/`review-resolve`/`process-session`）、删两个**覆盖**了已翻译可访问名的 `aria-label`；**P3** `loadChrome`/`loadSidebar` 与 `pageMaterials` 三请求改并行（横幅改由 `applyLoadNotes()` 按固定优先级合成，避免"谁后写谁赢"）、任务坞跨刷新恢复（`sessionStorage` + `/api/processing` 轮询，两拍确认"服务端其实没在跑它"、60 拍上限）、顶栏按用途分两组、动态 i18n key 取值域改由 `tests/support.py::dynamic_i18n_prefixes()` **从后端常量导入**、去掉两处 `t(t(...))`、`pageExercise` 按课程校验学生、括号配平。**三条建议未采纳**（理由见审查报告）：`/health` 不加"只拉一次"（唯一活性探测，已并行）、AI 分析不做恢复（后端无"正在分析"的可读状态）、后端 review 投影不删。**顺带修掉同族缺陷**：`route()` 里 5 条分支读了 `parts[1..]` 却不约束 `parts.length`（`#/courses/<id>/learn` 会把 `undefined` 当知识点 id）。守卫：**新文件** `tests/test_web_ui_invariants.py`（10 条）——把 `route()` 条件链解析成可求值谓词、对前端构造的 85 条哈希逐条求值、`index.html` 每个 `href="#/..."` 逐条求值、读了 `parts[1..]` 必须钉死长度、解析器看不懂的条件**报错而非跳过**；`showBanner` 第二实参只能是 `'bad'`；`TASK_TERMINAL_STATUSES` 必须等于后端 `JOB_STATUSES` 减 `QUEUED`/`RUNNING`。已做变异测试证明非空跑。基线：`ui_render_check.js` 219→250、`ui_audit.js` 396→388（删页面 −9、加断言 +1）。全量 `pytest -m "not integration"` 5354 passed / 0 failed（19m53s）。详见 `docs/ui-review-2026-09-21.md` 与 `docs/status.md` 同名区块。

- **2026-09-21（修复：AI 路径把 LLM 自报置信度写进了 knowledge_score）**：用户看着 `#/knowledge` 列表的「分数」列问"这分数有什么实际意义"。查证结果：这一列显示的值**不是**它声称的语义。`docs/architecture.md` 把 `knowledge_score` 定义为「当前证据对该知识点的支持程度」（0 条→0 / 1 条→0.5 / 2 条→0.75 / 3+ 条→0.9，有冲突封顶 0.5），实现是 `src/knowledge_validation.py`；而 AI 提取路径 `src/application/ai/validators.py` 写的是 `round(max(0.0, min(1.0, confidence_value)), 4)` —— 直接把 LLM 自报 confidence 塞进同一字段。AI 落库（`AIAnalysisService.analyze` → `workflow.register_knowledge_point`）**全程不经过** `KnowledgePipeline`，所以 `_apply_validation` 从未有机会跑（全仓仅 `knowledge_pipeline.py` 一处调用它）。实测：库内分布 `1.0×49 / 0.95×6 / 0.5×19`，而公式值域恰为 `{0.0, 0.5, 0.75, 0.9}` —— 1.0 与 0.95 是它**产不出**的值；`course-32dde014868219be`（Gestió de Proyectos）61 个知识点**每个都只有 1 条证据**，按公式应全为 0.5，实际 49×1.0 + 6×0.95（另 6 个 0.5 是非 `aikp-` 前缀、由确定性管线算出）。**修复**：把公式提升为公开单一真源 `knowledge_validation.knowledge_score_from_counts(n_support, n_conflict=0)`，`validators.py` 改为按 grounding 后的证据条数取值，`AIAnalysisService._attach_evidence` 挂新证据后同步重算（否则分数与 `evidence_refs` 脱节）。LLM 置信度不丢，仍在 `confidence` 档位（HIGH/MEDIUM/LOW）与 `metadata.ai_confidence`。**未动** `validation_status` / `needs_verification`：AI 路径在人工确认前不宣称 SUPPORTED 是 `service.py` 的显式设计，且项目自己在 `i18n.js::mc.axesNote` 里写明「证据校验」与「人工审核」是两条独立的轴。**数据修正**：`temp/recompute_ai_knowledge_scores.py` 对已有 55 条 AI 知识点重算（49×1.0 + 6×0.95 → 全部 0.5），真实 HTTP 验证 `GET /api/knowledge?course_id=...` 返回 61 个点全部 0.5。**踩坑记录（值得记住）**：第一版修正脚本只调 `register_knowledge_point`（只更新 org 内存索引）就打印「已落盘」，但 `_flush_knowledge` 写的是 `ctx.processing.structure`，于是**旧值被原样写回数据库** —— 脚本报成功、库里一个字节没变，靠 dry-run 重跑仍报「55 条待修正」才发现。**写入必须两边都改。** 守卫：新增 `tests/test_ai_knowledge_score.py`（14 条，含结构守卫、端到端落库断言，以及「把修复前那一行喂回判据」的自证），三组变异测试（退回旧写法 / attach 不重算 / 写死 1.0）分别精准失败 7 / 1 / 5 条。**另查出三个未修的缺陷**（记录在 status.md 的 Known limitation）：`service.py` 的 `knowledge_validation` 是个空壳 stage（只存在于阶段名里）；`validators.py` 写入的 `metadata` 在 `KnowledgePoint.from_dict` 处被丢弃（`to_dict` 没有 `metadata` 键，落库 payload 只剩 12 个标准字段），所以「这个分数来自哪条路径」事后不可判定；`average_knowledge_score` 后端算了但 `src/web/` 全目录无任何消费者。基线：`ui_render_check.js` 250、`ui_audit.js` 388（未改前端）。详见 `docs/status.md` 同名区块。

- **2026-09-22（初始化 git 仓库）**：`git init`（分支 `main`）并完成首次提交（383 个文件：src / tests / docs / scripts / 根配置）。`.gitignore` 追加四类排除：`classroom-data/`（本地数据真相源，git 只管代码、数据走备份）、`/Python/`（自带可移植解释器，可重装，不进版本控制）、根目录遗留 `/database/*.sqlite`（活数据库）、`/.workbuddy-ai/` 与 `/.freebuff/`（AI 工具会话状态，频繁变动弄脏工作区）。提交前删除根目录 PowerShell 误产物 `$null` 空文件。历史遗留占位目录（input/ output/ audio/ images/ documents/ materials/ backups/）中的占位 README 保留入库。


- **2026-09-22（概览页视觉修复：知识健康度分组 + 材料说明搬进整宽说明行）**：用户目视截图报两处排版问题。**其一**，`#/` 的「知识健康度」9 项指标挤在同一个 `<dl class="kv">` 里，标签与数字贴左缘、右侧空一大片且无语义分组 —— 改为 `.kv-groups` 两列网格（`repeat(2, minmax(0, 1fr))`，`<=560px` 收一列）内三个 `.kv-group`（结构 / 证据支持度 / 人工审核），各带 `dashboard.health.*` 小标题；`coverage.assigned` 并进「结构」组。**只给 dashboard 加容器类**：其余 20 处 `.kv` 是详情页字段列表（值多为长文本），"标签左 / 值右"在那里合适，一个字节没动。两条真相轴（`val.*` / `rev.*`）仍分开，未合并成"健康总分"；面板信息量未变（仍 9 项）。**其二**，材料卡状态列被说明文字撑成细长条 —— 那张表是 `table-layout: fixed`，状态列钉死 180px，而说明约 150 字符；**实测**（无头 Edge 布局引擎报数）说明文字块高 **164px / 10 个行盒**、状态格 **203px**、材料卡 **303px**。**第一版只把 `<br>` 换成 `.material-detail` 块并把状态列 180→240px，仍是"窄列里折 5~8 行"——把预览快照放大后才确认真因是"150 字符塞进 160px 固定列"**。第二版改为**独立整宽说明行**：`<tr class="material-row">`（CSS 去底边）+ `<tr class="material-note"><td colspan="3">`（浅底 `--surface-2`、上内边距收 0、底边收口，视觉上归属上一行）。文案与判据**完全复用** `dashboardWarnRow()` / `dashboardEmptyRow()` → 全局 `warningText()` / `evidenceCountOf()` / 同一份 `t()` 表；全局 `warningRow()` / `zeroEvidenceHint()` 与材料页 / 课堂页（非 fixed 表，列宽自动分配）**一个字节没动**。改后实测：状态格 37px、说明行 910px 宽 × 51px 高 × 2 行、材料卡 **188px（−115px，−38%）**。顺带：类型后缀加 `·` 分隔、`.kv-group .kv dt` 补 `overflow-wrap`（西语标签比中文长一倍）。i18n 新增 3 key × 3 语、删 1 个冗余 key × 3 语。守卫：`ui_audit.js` 388 → **406**（+18：三组三语齐备 ×3、三组渲染、小标题字面值、**组名不得与组内字段名同名**、旧 `<dl class="kv">` 不回归、`.kv-groups` 容器、CSS 两列、CSS 窄屏收一列、状态格只留 pill、**说明不许挤回状态格**、材料行带 `material-row`、说明行 `colspan=3`、警告块、零证据块、CSS 去底边、CSS 浅底收边距）；`ui_render_check.js` **250** 不变。**13 个变异测试全部精准命中**（三组塌回单 dl → 3 failed；删容器 / CSS 改单列 / 删窄屏规则 → 各 1；组名改同名 / 覆盖装回独立组 → 各 1；说明退回状态格 / 丢 `material-row` / colspan 缩 1 / 丢零证据块 / CSS 丢去底边 / CSS 丢浅底 → 各 1），顺带修掉断言自身缺陷（`match()` 返回 `null` 时取 `.length` 会崩掉整份 audit —— 崩溃伪装成基础设施故障，实际是断言该报红）。新增 `ui_audit.js --preview <lang> --out <file>`：输出真实骨架 + 内联 CSS + 真实渲染的静态 HTML 快照（剥 `<script>`、不参与断言、不影响门禁），供视觉比对 —— **两次真缺陷都是靠它发现的**（"覆盖"组名与字段名同名；第一版说明仍细长），自动断言当时都没抓到。本轮另用无头 Edge（`--headless --dump-dom` + 页面内只读测量脚本）补上**真实浏览器验证**：1280×2600 / 520×3000 两档视口、深色与浅色各一版，实测确认两列网格铺满卡宽、520px 下收成一列、说明行占满 910px 且不溢出。Python 全量 `5367 passed, 6 skipped, 50 deselected`（0 failed，20m19s，与第一轮一致）；**skip 5→6 已查清与本次无关** —— 4 个含 skip 的测试文件全部 0 次引用前端源码，读前端的 4 个文件 0 skip（单独跑 105 passed），判据只依赖符号链接特权 / faster-whisper 安装状态 / 夹具数据属性。详见 `docs/status.md` 同名区块。
---

*本规范是项目的基础指导文件。随着项目发展，可根据实际需要补充和细化。核心原则——证据优先、绝不编造、明确标注不确定性——始终不变。*
