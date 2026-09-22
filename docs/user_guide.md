# 使用指南

按使用顺序讲清六件事：**材料怎么进来、处理怎么发生、知识怎么被审核、学生怎么学、
练习怎么工作、状态机怎么走**。每节末尾都会指出对应的 API 与界面位置。

---

## 0. 核心不变量（读之前先知道这个）

```text
Material ──→ Evidence ──→ KnowledgePoint ──→ Validation / Review
  材料          证据            知识点              验证 / 审核
```

- **证据**是唯一的事实载体。它保存原文（逐字不改）、语言、以及来源定位
  （音频时间戳 / 页码 / 行号 / 段落）。
- **知识点**由证据组装，不直接由材料生成。没有证据支撑的知识点不会被造出来。
- **验证**与**审核**是两件事：验证是「这条陈述是否有证据支撑」，审核是
  「人是否采信它」。审核状态只能由人改变。
- 任何一步走不通，系统**显式报缺**，不猜、不补、不静默跳过。

---

## 1. 课程与课堂

```text
课程 (Course)    一门课：course_id / name / code / language
课堂 (Session)   一次课：session_id / course_id / session_number / date / title
```

```bash
POST /api/courses   {"name": "Álgebra Lineal", "code": "ALG", "language": "es"}
POST /api/sessions  {"course_id": "...", "session_number": 3, "date": "2026-03-01", "title": "Tema 3"}
```

`course_id` 是**内容寻址**的：同样的输入永远得到同样的 ID，不是随机 UUID。
这样测试可复现，备份/恢复之后引用也不会错位。

课堂是**可选**的组织维度。材料可以不挂到任何课堂上，知识点也不会因此丢失。

界面：**概览**页 → 侧栏课程列表 → 课程页 → 课堂页。

## 2. 材料

### 支持的类型

| 类型 | 扩展名 | 处理方式 |
| --- | --- | --- |
| 音频 | `.mp3` `.wav` `.ogg` `.m4a` … | Whisper 转录 → 带时间戳的证据 |
| 文档 | `.pdf` | pypdf 提取 → 带页码/段落定位的证据 |
| 文档 | `.docx` | python-docx 提取 → 带段落定位的证据 |
| 笔记 | `.txt` `.md` | 直接解析 |
| 图片 | `.png` `.jpg` `.jpeg` `.webp` … | 本地 OCR → 带位置的证据 |

### 上传会发生什么

```bash
POST /api/materials?course_id=...&session_id=...   # multipart，字段名 file
```

1. **校验**：扩展名、大小（默认上限 200 MiB）、非零字节。
   拒绝时给出结构化错误码：`UNSUPPORTED_EXTENSION` / `OVERSIZED_FILE` / `ZERO_BYTE_FILE`。
2. **原子复制**进 `data_dir/{audio|images|documents}/`。文件名**保持原样**。
3. **登记**进课程材料注册表（`materials/<course_id>.json`），记录
   `material_id` / 文件名 / 大小 / 内容哈希 / 受管路径。
4. **去重**：同名 + 同内容只登记一次，第二次返回已有记录并标 `duplicate: true`。

文件名里的路径分隔符、`..`、Windows 非法字符都会被安全处理——**绝不会**写到
`data_dir` 之外。这条由 `tests/test_hardening_input_safety.py::TestFileSafety`
用真实文件系统断言。

### 上传前先看一遍

```bash
GET /api/materials?course_id=...
GET /api/materials/{material_id}
GET /api/processing?course_id=...
GET /api/processing/{material_id}
```

界面：**材料**页（含上传表单、状态、逐条操作按钮）。

### 界面上怎么关联课堂

上传表单里的「课堂 (可选)」是一个**下拉列表**，列出这门课已有的课堂，标签是
课号 + 标题（`第 3 堂 · Tema 3`）。选「不关联课堂」就不挂到任何课堂上 ——
材料照样登记，知识点也不会因此丢失。

- 下拉里**不出现**内部 id。`session_id` 与 `course_id` 一样是内容寻址的
  （`"session-" + sha256(course_id + 课号)[:16]`），手打既猜不出也记不住。
- **课堂目前只能通过 `POST /api/sessions` 创建**，界面上没有建课堂的入口
  （只有读取：`GET /api/sessions?course_id=...`）。一门课还没有任何课堂时，
  下拉下面会提示「还没有课堂。」—— 这时要么先建课堂，要么选「不关联课堂」。
- 材料列表的「课堂」列显示的同样是课号 + 标题，不是内部 id。

## 3. 处理

```bash
POST /api/materials/{material_id}/process     # 单个材料
POST /api/sessions/{session_id}/process       # 整堂处理
```

处理是**串行**的，且遵守两条规则：

1. **单个材料失败不会中断其余材料。** 整堂处理的响应里会报出
   「N 个材料，M 个失败」。
2. **失败分可重试与不可重试。** 解析失败（文件本身坏了）是不可重试的；
   重试会被明确拒绝，而不是无限重试。模型不可用、临时 I/O 错误是可重试的。

```bash
POST /api/materials/{material_id}/retry
```

处理产出**证据**：

```bash
GET /api/materials/{material_id}/evidence?course_id=...
```

### 关于 Mock

如果真实 ASR / OCR 运行时不可用，流程会回落到 Mock，并且**会说出来**：

- `GET /api/health` 里 `asr_mode` / `ocr_mode` 变成 `"mock"`；
- 界面顶部挂出显式横幅；
- `--check` / `health` 的输出里模型可用性标成不可用。

**Mock 的输出不是转录或识别结果**，只用来验证流程。任何地方都不会把 Mock
标成「真实」。

## 4. 知识

```bash
GET /api/knowledge?course_id=...             # 知识点列表
GET /api/knowledge/{knowledge_id}            # 单个
GET /api/knowledge/{knowledge_id}/evidence   # 支撑证据
GET /api/knowledge/{knowledge_id}/trace      # 完整溯源链（核心）
GET /api/course-knowledge?course_id=...      # 主题 / 关系汇总
GET /api/coverage?course_id=...              # 覆盖情况
GET /api/gaps?course_id=...                  # 缺口
GET /api/dependencies?course_id=...          # 依赖关系
GET /api/conflicts?course_id=...             # 冲突
```

### 溯源链

`/api/knowledge/{id}/trace` 返回：

```text
knowledge_point      陈述本身 + 验证状态 + 审核状态 + 原文术语
links[]              每条：evidence（原文 / 语言 / 类型 / 置信度 / 来源定位）
                           + material（文件名 / 哈希 / 受管路径 / 状态）
materials[]          涉及的源材料
complete             证据链是否完整
unresolved_material_ids[]   解析不了的材料引用
language             声明的语言 + 证据里实际出现的语言
dependencies         出边 / 入边 / 声明式关联
topics / sessions / source_sessions
```

**断链会显式报警**。如果某条证据引用的材料不在课程注册表里，`complete` 为
`false`，`unresolved_material_ids` 列出它，界面打出「溯源断链 · 请勿据此下结论，
需人工核查」——不会静默跳过。

这条链的可追溯性由 `tests/test_hardening_traceability.py` 抽样 50 个知识点
（在放大到 90 个知识点的验收数据集上）逐条走完
「知识点 → 证据 → 材料 → 源定位」来断言。

### 语言

- 声明的语言（课程/材料上写的）与**证据里实际出现的语言**分开报。
- 两者不一致时不会「纠正」成一致——不一致本身就是信息。
- 原文一律按原样渲染，界面语言**绝不**翻译任何证据内容。

界面：**知识点**页 → 知识点详情页（核心 UX：溯源链可视化 + 依赖关系 + 人工审核）。

## 5. 人工审核

```bash
GET  /api/reviews?course_id=...              # 待人工决策的清单
GET  /api/reviews/{knowledge_id}
POST /api/reviews/{knowledge_id}/confirm
POST /api/reviews/{knowledge_id}/reject
POST /api/reviews/{knowledge_id}/keep-unverified
POST /api/reviews/{knowledge_id}/resolve-conflict
```

### 两条硬规则

**规则一：审核状态只能由人工改变。**

以下行为**都不会**把一个待审项变成已确认：

- 通过 API / UI **读取**它；
- 重新处理材料；
- 重启进程；
- 重新载入数据库。

由 `tests/test_hardening_truth_safety.py::TestReviewSafety` 逐条断言。

**规则二：冲突项不能直接确认。**

对处于 `CONFLICTED` 的知识点调 `confirm` 会被拒绝
（`INVALID_INPUT`，消息里带 `CONFLICTED`）。必须走 `resolve-conflict` 并**显式
选择**要采信的证据。系统永不替你挑一个版本。

每次成功的审核动作都会留下 `ReviewRecord`（含决策、备注、时间、操作者）。
「已确认的知识点必须有审核历史」也是被断言的不变量。

界面：**待审核**页 + 知识点详情页底部的审核面板。

## 6. 学生与学习

界面：**学生** 页顶部有一个注册表单（学号 / 标识必填，姓名可选），提交后
列表立刻刷新。空课程时表单同样在——第一个学生就是这么建的，不必手写请求。

```bash
POST /api/students   {"course_id": "...", "student_id": "2026-001", "display_name": "Ana"}
GET  /api/students?course_id=...
GET  /api/students/{student_id}
GET  /api/students/{student_id}/state
GET  /api/students/{student_id}/learning-status
GET  /api/students/{student_id}/dashboard
```

### 学习状态机（Task 30）

```text
not_started ──viewed──→ exposed ──practiced──→ practicing ──reviewed──→ reviewing
```

- 学习事件**只能由学生主动触发**，且只能走这三个事件：
  `POST /api/students/{student_id}/learning-events`，`event_type` ∈
  `viewed` / `practiced` / `reviewed`。
- **答题不会推进状态。** 回答了一道题不等于「练习过了」——状态只由显式事件推进。
- 界面不实现任何 mastery 算法；「状态分布」不是「掌握度」。
  界面文案里明确写着这一点，因为把两者混为一谈会误导复习决策。

### 学习计划与路径

```bash
GET /api/study-plans/{student_id}?course_id=...
GET /api/learning-paths/{knowledge_id}?course_id=...&student_id=...
```

学习路径的节点顺序是：

```text
前置 → 知识点 → 练习 → 评估
```

被前置阻塞的知识点会被标出来，而不是假装可以开始。

## 7. 练习、作答、评估

```bash
POST /api/exercises                     # 创建
GET  /api/exercises?course_id=...
GET  /api/exercises/{exercise_id}
GET  /api/students/{student_id}/exercises?course_id=...
GET  /api/students/{student_id}/exercises/{exercise_id}
POST /api/answers
GET  /api/evaluations/{answer_id}
```

### 出题依据是证据

练习携带 `knowledge_point_ids` 与 `evidence_ids`。界面上的练习页会把
「出题依据（证据原文）」直接渲染出来——学生能看到这道题是从哪句原文来的。

### 提交前不泄露答案

`GET /api/students/{id}/exercises/{eid}` 是**学生视角**：提交前响应里**不含**
`correct_choice_id` / `expected_answer` / `fill_blank` / `explanation`。

这不是靠前端隐藏。`scripts/ui_render_check.js` 在 Node 里**真实执行**练习页函数，
断言渲染出的 HTML 里没有答案字符串——比静态断言强。

### 答错不修改知识库

```text
is_fact_verification: false
affects_knowledge_base: false
```

评估是对「学生这一答」的判定，不是对知识的核验。答错之后，
`validation_status` / `review_status` / `knowledge_score` / 证据集 / 溯源链
**逐字节不变**。

但答错**会**改变这个学生自己的学习状态——这两件事在
`tests/test_hardening_truth_safety.py::TestStudentStateSafety` 里被同时断言
（知识不变、学生状态变、别人的状态不受影响）。

### 评估不判语义

填空/简答按**逐字比较**判分，不做语义判分。界面上写着这一点，避免误解。

### 提交幂等

同一份答案（同 `student_id` / `exercise_id` / `submitted_value` / `sequence`）
重复提交返回**同一个** `answer_id` 与同一个 `submitted_at`。

界面：**练习**页（列表 + 单题 + 评估面板）、**学生**页（进度 / 状态分布 /
计划 / 路径 / 缺口）。

## 8. 备份

见 [backup_restore.md](backup_restore.md)。

## 9. 界面语言

右上角切换 **中文 / Español / Català**。约束：

- 语言选择**只影响界面文案与解释请求的语言标签**；
- **绝不**修改任何证据、**绝不**翻译任何原文；
- 三个语言表的键集必须完全一致；
- 每句界面文案在 es / ca 里都必须有译文——漏译是可测的
  （`tests/test_exercise_ui.py::TestI18nTables`），而不是靠人肉检查。

原文（西语 / 加泰语 / 中文）在任何语言模式下都按原样显示。
