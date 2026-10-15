# 课堂助手 · Agent 交接上下文（动手前必读）

> 本文件是给**任何**接手这个仓库的 agent（codex / cline / opencode / freebuff / workbuddy / …）
> 看的「最小但完整」的现状说明。目的是让一个对这个项目**零记忆**的 agent，读完这一份，
> 就能达到与长期协作 agent（workbuddy / 澳龙）相当的理解基线，不靠猜、不靠翻历史对话。
>
> 阅读顺序：**先读本文件**，再读任务计划书（`docs/task-*.md`），再读计划书 §0 里点名的
> 具体源文件。**不要只读任务计划书就动手**——本文件里有改错会返工的环境事实。

---

## 0. 项目一句话

**课堂助手（Classroom Assistant）**：面向 UAB 本科课程的**本地单机**学习助手。
把课堂录音、板书/幻灯片、自己与同学的笔记，变成**可追溯**的知识点、复习材料与复习规划。
核心铁律只有一条：**绝不编造**——每一条知识点都必须能沿「知识点 → 证据 → 原始材料 → 具体位置」走回去。

技术定调（决定所有设计）：

- **单机、本地、单用户、无鉴权**。不要暴露公网、不要引入账号体系。
- **领域层 stdlib-only**；运行时依赖见 `requirements.txt`（全本地离线：ASR= faster-whisper、
  OCR= rapidocr-onnxruntime；无 GPU、无云 API）。
- **前端零构建**：原生 JS 多 `<script>` 按固定顺序引入，无打包器、无 React/Vite、无 npm 依赖。
- **AI 语义分析可选、默认关闭**（`CLASSROOM_AI_ENABLED`），只按 OpenAI 兼容 `/chat/completions`
  契约发请求，key 只读进程环境，绝不到达浏览器前端。

## 1. 仓库与环境（真实现状，动手前先核对）

| 项 | 事实 |
|---|---|
| 仓库路径 | `D:\Project\Clases`（Windows，路径含空格时用引号） |
| 版本控制 | git，分支 `main`，`origin = https://github.com/ShengxuanLing/ClassroomAssistant.git` |
| 最新提交 | 动手前自己 `git log --oneline -5` 核对，**不要用历史对话或本文里的旧提交号当基线** |
| 依赖 | `requirements.txt`（运行时）/ `requirements-dev.txt`（= 运行时 + pytest） |
| 数据真相源 | 运行时的 `classroom-data/`（8 子目录，SQLite WAL + 材料副本 + 日志 + 备份 + temp），**与 `src/application/data_dirs.py` 的 8 目录一字对应** |
| 遗留目录 | 仓库根下的 `input/ output/ audio/ images/ documents/ materials/ backups/` **已废止**，只剩占位 README，**新代码禁止写入** |

### 1.1 Python 解释器（最容易踩的坑之一）

- 仓库自带便携解释器`/Python/`**已不存在**（在 `.gitignore` 里，可重装，不进版本控制）。
- 本机 PATH 上的 `python` = 托管 **3.13.12，无 pytest**。
- 本机**唯一带 pytest** 的是 WindowsApps 的 `python.exe`（3.14.5 + pytest 9.1.1），
  但它缺 pypdf / onnxruntime / faster_whisper ⇒ 依赖这三样的测试会成片 `E`（error）。
- 所以：**改代码后只跑"受影响的"定向测试**，跑全量既慢（20–60 分钟）又会被环境性 error 干扰。

### 1.2 依赖硬约束（改依赖前必读 `requirements.txt` 内注释）

- `rapidocr-onnxruntime==1.2.3` —— 精确固定。1.3.x 声明 `Requires-Python <3.13`，
  3.13/3.14 上唯一可装的就是 1.2.3。
- `ctranslate2` 是 faster-whisper 的传递依赖但**显式声明**，避免干净安装后音频路径悄悄坏掉。
- 版本上限：`faster-whisper<2.0`、`ctranslate2<5.0`、`pypdf<7.0`、`python-docx<2.0`、
  `python-pptx<2.0`、`pytest<10.0`。**不要随手升这些上限**。

## 2. 八条铁律（违反 = 返工）

1. **绝不编造**。知识点必须 evidence-grounded；没有证据支撑就不生成；证据内容**逐字不改**。
2. **证据内容绝不翻译/改写**。原文（西语/加泰语/中文）按原样显示；界面只翻译按钮和标题。
3. **三语同步**。任何用户可见文案 zh / es / ca 三语同加；es / ca 译文**不得出现 CJK 字符**。
4. **冲突绝不自动选一个**。并列所有版本，标注置信度。
5. **审核状态只能由人改**。系统（含 AI）永不自动确认，尤其是冲突项。
6. **缩小范围**。任务计划书没要求的一律不加；不多产出文件、不加 README、不留备份。
7. **数据真相源唯一**。任何路径拼接必须过 `safe_join`；用户数据绝不写入 `src/` 或 `tests/`。
8. **精准修改**。只改必要部分；同一文件的多处 Edit 必须**串行**（前一处的改动会影响后一处的行号/上下文）。

## 3. 验证命令（什么该跑，什么千万别跑）

**只跑受影响的定向测试 + 前端审计。不要跑全量回归。**

```bash
# 定向测试（只挑你改动的模块/测试文件）
PYTHONPATH=/d/Project/Clases "C:/Users/Rafae/AppData/Local/Microsoft/WindowsApps/python.exe" \
  -m pytest tests/test_<改动的>.py -q -p no:cacheprovider

# 前端审计（改前端必跑；它自己崩 = 前端门禁全失效，必须先修）
"C:/Users/Rafae/.workbuddy-ai/binaries/node/versions/22.22.2-6/node.exe" scripts/ui_audit.js

# 编译校验（改 Python 后快速自检）
python -m compileall -q src

# UI 渲染校验（另一条前端门禁）
node scripts/ui_render_check.js
```

**归因标准**：按「失败的名字有没有变」判断是否回归，**不按 failed 是不是 0**。

**已知的环境性失败（别当回归，别去"修"它们）**：

- `test_student_ui.py::TestTheLiveSessionPickerEndToEnd::test_the_live_script_passes`：
  `scripts/e2e_session_picker.js:27` 硬编码已消失的 `Python/pythoncore-3.14-64/python.exe` ⇒ spawn ENOENT。
- `test_production_gate.py` / `test_release_gate.py`：跑到 `[100%]` 但不退出（8 分钟无 summary）。

## 4. Git 纪律（这条反复出事故）

- **Codex 也在改这个仓库**（2026-10-11 起），工作区随时躺着别人的在制品。
- `git add <自己改的文件>`，**绝不 `git add -A` / `git add .`**。
- 提交前：`git diff --cached --stat` 核对暂存区；`git diff --stat` 确认别人的改动仍在未暂存。
- **本机没有 GitHub 凭据** ⇒ `git push` 必 401 失败。**提交能做，推送要用户自己来**，别白试。
- 行尾（`.gitattributes`）：`*.py/*.js/*.css/*.html/*.json/*.md` = **LF**；`*.bat/*.cmd/*.ps1` = CRLF。
  - **别用 `grep -c $'\r'` 判行尾**（会匹配到字母 r，纯 LF 也报满行）。用
    `pathlib.Path(f).read_bytes().count(b'\r\n')` 或 `git ls-files --eol`。
  - 本机 `core.autocrlf=true` ⇒ 工作区可能 CRLF 而 blob 是 LF，`git status` 干净；只有真多出 `\r\n` 的文件要修。

## 5. 前端守卫面（改前端前必读）

- `scripts/ui_audit.js`：三语 key parity（`zh==es==ca`）；es/ca 渲染结果**不得出现 CJK**——**含夹具内容**，夹具必须全 ASCII。
- `tests/test_learning_view.py`：`test_all_three_tables_have_identical_keys`、
  `test_every_used_key_is_defined`（新增 `t('x')` 忘了加表 ⇒ 红）、`test_translations_never_contain_chinese`。
- `tests/test_exercise_ui.py:1419`：`data-i18n` 的 key 必须已定义。
- **vm 沙箱只提供** `window/document/localStorage/fetch/console/setTimeout/clearTimeout/
  URLSearchParams/FormData`：**没有 `navigator`、没有 `FileReader`**；`document.querySelector()` 返回 `null`。
  ⇒ 新代码必须 `typeof X !== 'undefined'` 守卫，**否则审计自己崩**。
- 界面里的**单位一律用符号不用词**（体积用共享的 `fmtBytes()` → `1.2 KB`，不是「字节」），
  符号跨语言通用、无需翻译，用词就得三语各加一个键。
- `src/api/server.py:389`：错误 `detail` **只在 debug 模式（默认 False）回传** ⇒ 前端拿不到
  `detail.protocol_error`，友好提示必须在客户端预检。

## 6. AI 分析的成本与已知缺陷

- 分块 `chunks_for_evidence` **按证据（=按页）切块** ⇒ 幻灯 PDF **1 页 = 1 次 LLM 调用**；
  再加收尾 + 逐 KP 中文解释（每条 1 次）。单次 43–200s。
- **`CLASSROOM_AI_TIMEOUT_SECONDS` 未接线**（已知缺陷）：`analyze` 与 `_translate_kp_once` 都硬编码
  `timeout_seconds=60` ⇒ 慢而正常的调用被判超时 → chunk 失败 → 整份材料失败。
- chunk 缓存**不跨尝试**（每次新建 `AIAnalysisService()` ⇒ 重试要重跑全部 chunk）。
- **同一 `classroom-data` 不能双进程写**（第二个进程秒失败 `StorageError: database is locked`）⇒ 分析**必须串行**。
- 后台任务约 3 小时被静默杀掉（日志无 traceback）。
- PDF 抽文用 pypdf（`src/document_input.py`，懒加载）；**文档摄取不做语言判定** ⇒ 文档证据
  `language` 全 `Unknown` ⇒ 详情页按证据语言请求解释会 400。
- AI 配置在 `.env`；有 key 时 provider 是 **real**。provider 串 `user-pasted` 是**业务逻辑**
  （`workspace.py:2405` 靠它判定 `ai_mode="manual"`），**不要改这个串**。

## 7. 验收纪律（只信 git diff，不信自述）

- **自述/报告/总结一律不可作为证据**（包括 agent 自己说的）。接手必查：
  1. `git status --short` + `git diff --stat`
  2. `python -c "import src.persistence"` 能不能过
  3. `scripts/ui_audit.js` 能不能**跑完**（它自己崩 = 前端门禁全失效）
  4. 行尾是否写反
- **改大 `.md` 后必须结构复核**：「编辑成功」≠「文档完整」。连续多处 Edit 出现过整节被吞、围栏错位。
  三步：① 标题集合对比（`git show HEAD:<f> | grep "^#\{2,4\} "` vs 工作区差集应为空）；
  ② 围栏配对扫描；③ `git diff -U0 | grep '^-'` 逐行看。
- **删功能要连带清掉专门服务它的门面**（脚本、样式、入口、测试、`WEB_FILES` 引用）。
- 用 `git show HEAD:<f>` 取回被误删内容时，拼回用 `Path.write_bytes(...)`
  （`write_text` 在 Windows 会把 `\n` 转 `\r\n`）。

## 8. 关键文档索引（任务计划书最常点名）

| 文档 | 内容 |
|---|---|
| `AGENTS.md`（仓库根） | 项目规范、文件组织、质量标准、证据优先 & 工作流铁律 |
| `README.md` | 项目定位、安装、启动、上手五步、已知限制 |
| `docs/architecture.md` | 分层、依赖守卫、确定性、错误码、测试策略 |
| `docs/configuration.md` | 全部配置项、环境变量、§8 = AI 配置、§安全边界 |
| `docs/data_model.md` | 领域模型、SQLite schema、证据优先不变量 |
| `docs/task-82-optimization-plan.md` | AI 分析/知识地图修复+优化总计划（TASK-82…104，§0.1 滚动进度） |
| `docs/task-105-knowledge-map-entry.md` | 入口改道（§6 列 8 处必须同步的守卫） |
| `docs/task-106-108-cleanup-plan.md` | 下线记忆卡片 / 修「无学生」死路 / 清点无入口页面（§12 接手前必读） |
| `docs/task-109-local-ai-json-upload-plan.md` | 手动 AI → 本地 AI 分析 + 导入改上传 JSON 文件 |
| `docs/status.md` | 逐任务实现记录（巨型文件，按任务号 grep 定位） |

## 9. 任务计划书的使用约定

每个具体任务都有一份 `docs/task-*.md` 计划书（模板见 `docs/agent-task-plan-template.md`）。
执行任务时：

1. **先读本文件**（上下文 + 环境 + 铁律 + 坑）。
2. 再读对应计划书，**尤其 §0 的纪律声明和"不在本次范围"清单**。
3. 计划书里的现象都带 `文件:行` 证据，**实施前先自己核实一遍**——基线可能已经变了。
4. 计划书与仓库现状冲突时，**以当前仓库实际状态为准**，不要假设旧代码/旧文件还在。
5. 完成任务 = 通过计划书 §验收标准 的全部判据 + 通过本文件 §3 的定向验证。**不跑全量。**

---

*本文件是「澳龙 / workbuddy 层」的持久上下文快照。若仓库环境发生实质变化（换解释器、接新依赖、
新 agent 加入并行），应回写本文件，让下一个人不用重新踩坑。*