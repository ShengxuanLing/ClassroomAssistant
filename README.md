# 课堂助手 (Classroom Assistant)

面向巴塞罗那自治大学（UAB）本科课程的**本地单机**学习助手。

它把课堂录音、板书/幻灯片、你和同学的笔记，变成**可追溯**的知识点、复习材料和
复习规划。核心约束只有一条，但它决定了整个设计：

> **绝不编造。** 每一条知识点都必须能沿着「知识点 → 证据 → 原始材料 → 具体位置」
> 走回去。走不回去的内容不会被生成，而是被明确标记为缺失。

界面可选 **中文 / Español / Català**。原文（西语 / 加泰语 / 中文）一律按原样显示，
界面语言只翻译按钮和标题，**绝不改写或翻译任何证据内容**。

---

## 它做什么

| 能力 | 说明 |
| --- | --- |
| 材料登记 | 上传 PDF / DOCX / TXT / MD / 音频 / 图片；同名同内容只登记一次 |
| 转录与识别 | 音频走本地 Whisper（faster-whisper），文档走 pypdf / python-docx，图片走本地 OCR（rapidocr-onnxruntime）；无 GPU、无云 API |
| 证据提取 | 每条证据保留来源定位（音频时间戳 / 页码 / 行号 / 段落），内容逐字不改 |
| 知识点组装 | 从证据组装知识点，标注验证状态与置信度；无证据支撑就不生成 |
| 冲突检测 | 不同来源矛盾时并列所有版本，**绝不自动挑一个** |
| 人工审核 | 审核状态只能由人改变；系统永不自动确认，尤其是冲突项 |
| 学习闭环 | 学生 → 练习 → 作答 → 评估 → 学习状态 → 学习计划 → 学习路径 |
| 备份恢复 | 一致性快照（SQLite 在线备份 API）+ 归档校验 + 恢复演练 |
| 溯源链 UI | 知识点详情页把「知识点 → 证据 → 源材料」整条链渲染出来，断链显式报警 |
| AI 语义分析（可选，默认关闭） | 开启后处理材料自动分析：自动摘要/主题/知识点落库，高置信度自动加入、低置信度与冲突进待审核；失败不污染材料与证据，可重试；无证据支撑不生成；Key 只读进程环境（`CLASSROOM_AI_ENABLED`，详见 `docs/configuration.md` §8） |

## 怎么安装

需要 Python **3.13 或 3.14**。本仓库自带的可移植解释器是 **3.14.7**，也是唯一
**实测验证**过的版本（全量回归 3716 条都在它上面跑）。3.13 由依赖约束间接覆盖
（见下），更低版本没有测过，也没有 `python_requires` 之类的下限声明，因此不保证可用。

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
pip install -r requirements.txt
```

`requirements.txt` 里有两条与 Python 版本有关的硬约束，改依赖前请先读文件里的注释：

- `rapidocr-onnxruntime==1.2.3` —— 1.3.x 声明 `Requires-Python <3.13`，
  3.13/3.14 上唯一可安装的版本就是 1.2.3，所以是精确固定。
- `ctranslate2` 是 `faster-whisper` 的传递依赖，但**显式声明**了，避免
  「干净安装后音频路径悄悄坏掉」。

首次调用真实 ASR 时，模型会从 Hugging Face 下载到 `~/.cache/huggingface`。

## 怎么启动

Windows 下双击即可：

```text
scripts\start.bat      # 启动本地服务并自动打开浏览器
scripts\health.bat     # 只做环境自检 + 装配快照，不占端口
scripts\stop.bat       # 停止
```

或者直接调命令行：

```bash
python -m src.application.launcher start     # 启动（阻塞）
python -m src.application.launcher health    # 自检
python -m src.application.launcher stop      # 停止
python -m src.application.cli --print-config # 打印最终生效的配置（不开库）
python -m src.application.cli --check        # 完整装配一遍后退出
```

默认监听 `http://127.0.0.1:8765`，浏览器打开即用。默认只允许回环地址；
要绑到其它地址必须显式 `--allow-remote`。

## 上手五步

```text
1. 建课程    POST /api/courses      {"name": "Álgebra Lineal", "code": "ALG"}
2. 建课堂    POST /api/sessions     {"course_id": "...", "session_number": 1}
3. 传材料    POST /api/materials    multipart，带 course_id / session_id
4. 跑处理    POST /api/materials/{material_id}/process
             或 POST /api/sessions/{session_id}/process 整堂处理
5. 看溯源    GET  /api/knowledge/{knowledge_id}/trace
```

界面里的「概览 / 知识点 / 材料 / 待审核 / 学生 / 课程 / 练习」七个页面覆盖同一套流程，
也可以点着用（另有课程详情 / 课堂详情 / 知识点溯源详情等下级页面）。

## 数据放在哪

默认在**当前工作目录**下建 `classroom-data/`，它是本应用**唯一**的数据真相源
（与 `src/application/data_dirs.py` 的 8 目录一字对应）：

```text
classroom-data/
├── materials/    材料注册表（每个课程一个 JSON）
├── audio/        音频材料副本
├── images/       图片材料副本
├── documents/    文档 / 笔记材料副本
├── database/     classroom.sqlite（SQLite，WAL 模式）
├── logs/         运行日志
├── backups/      备份归档（.zip）
└── temp/         处理中间产物（可安全删除）
```

> 早期规范设想的 `input/` 与 `output/` 目录**已废止**：二者只剩一个占位 README.md、不含任何业务数据，
> 新代码禁止写入。从旧布局到新布局的逐项映射见
> [docs/legacy-input-output.md](docs/legacy-input-output.md)。

数据库位置、端口、上传上限等都可以用配置文件 / 环境变量 / 命令行覆盖，
优先级是 **命令行 > 环境变量 > 配置文件 > 默认值**。详见
[docs/configuration.md](docs/configuration.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/getting_started.md](docs/getting_started.md) | 安装、启动、跑通第一条完整流程 |
| [docs/user_guide.md](docs/user_guide.md) | 课程 / 材料 / 处理 / 审核 / 学生 / 练习 / 计划 |
| [docs/configuration.md](docs/configuration.md) | 全部配置项、环境变量、数据目录、安全边界 |
| [docs/architecture.md](docs/architecture.md) | 分层、依赖守卫、确定性、错误码、测试策略 |
| [docs/data_model.md](docs/data_model.md) | 领域模型、SQLite schema、证据优先不变量 |
| [docs/backup_restore.md](docs/backup_restore.md) | 备份归档格式、恢复流程、安全边界 |
| [docs/troubleshooting.md](docs/troubleshooting.md) | 常见故障与处理 |
| [docs/status.md](docs/status.md) | 逐任务实现记录（交付文件、测试、发现并修复的缺陷） |
| [docs/legacy-input-output.md](docs/legacy-input-output.md) | 旧 `input/` `output/` 布局 → `classroom-data/` 对照表（已归档） |
| [docs/final_persistence_report.md](docs/final_persistence_report.md) | Task 48–55 报告：13 行重启矩阵 / 50 知识点 traceability / DB 足迹 / 未验证项 |
| [docs/task-76-ai-understanding-report.md](docs/task-76-ai-understanding-report.md) | Task 76 报告：AI 语义理解管线（24 项交付清单 + 分级结论） |

## 已知限制（明确说明，不掩饰）

这些是**当前真实存在**的限制，不是待办清单的措辞：

1. **【已解决】业务对象现在落盘了。** Task 48–55 把 Course / Session / Material /
   Evidence / KnowledgePoint / ReviewRecord / Student / Answer / Evaluation / StudyPlan /
   LearningPath 真正接进 `classroom.sqlite`（WAL 模式）。运行时 `data_dir/database/`
   **有活库**；创建课程 → 关闭程序 → 重新打开，全部业务对象按内容寻址 ID 逐字段恢复。
   这条限制曾是 Task 34–47 阶段的"Beta Ready 唯一决定性缺口"，现已由
   `tests/test_restart_recovery.py`（90 条，全部真子进程）+
   `tests/test_production_gate.py::TestPersistenceRestartMatrix`（13 行矩阵）钉住。
   原来的缺口断言（`TestDrillGap`、`test_reload_limitation_is_pinned_not_silent`）
   按 spec 55.14 **改写为真正的恢复 PASS 验收**（不是删除）。
2. **没有浏览器端到端测试。** 本机是 Windows，而浏览器自动化工具只支持
   macOS / Linux。UI 的验证方式是：HTTP 契约测试（297 条）+ 在 Node 里用最小
   DOM 桩**真实执行**页面函数（`scripts/ui_audit.js` 388 条、
   `scripts/ui_render_check.js` 250 条）+ Python 侧的**结构不变量**检查
   （`tests/test_web_ui_invariants.py` 10 条：哈希可达性、横幅 kind 取值域、
   任务坞终态集合与后端常量一致）。完整已覆 / 未覆边界固化在
   `scripts/ui_audit.js` 头部的「真实事件冒烟清单」，摘要如下：

   **已覆**：页面函数真实执行（不是正则匹配源码，是 vm + DOM 桩里真的调用并取回
   HTML）、三语 key parity（es / ca / zh 翻译表 key 一致，且 es / ca 输出不得出现
   CJK）、答案与解析在作答前不泄漏进 DOM、原文原样渲染、`esc()` 转义、
   空 / 加载 / 错误 / 超长文本 / 大量数据等边界状态。

   **未覆（不得宣称已验证）**：CSS 布局与视觉呈现、真实点击事件（表单接线只做
   "存在且已接上"的静态断言，不模拟用户操作）、真实浏览器事件（hashchange /
   DOMContentLoaded / 键盘 / 焦点 / 拖拽上传）、异步竞态与真实网络（fetch 由桩
   返回固定响应，不覆盖超时重试与并发乱序）、跨浏览器兼容性与可访问性。
   **不声称等价于浏览器测试。**
3. **Mock 引擎会明说自己不是真的。** 没有安装真实 ASR / OCR 运行时的时候，
   流程会回落到 `MockASRProvider` / `MockOCREngine`，`/api/health` 里
   `asr_mode` / `ocr_mode` 会标成 `mock`，界面顶部也会挂出显式横幅。
   Mock 的输出**不是**转录或识别结果。
4. **备份归档默认落在 `data_dir/backups/` 里**，也就是和被备份的数据在同一个目录。
   整目录级的丢失（误删、磁盘故障）会连备份一起带走。异地/异盘复制需要你自己做。
5. **`--check` / `health` 不是只读的。** 它会建目录并迁移数据库，因此会创建
   `classroom-data/`。
6. **单用户、单机、无鉴权。** 服务默认只绑回环地址，没有账号体系；不要把它暴露到
   公网。

## 开发

```bash
# 完整回归（不含需要真实模型的集成测试）
python -m pytest -q -m "not integration"

# 单个模块
python -m pytest tests/test_persistence_transaction.py -q

# 编译校验
python -m compileall -q src

# UI 渲染与 UI 审计（Node）
node scripts/ui_render_check.js
node scripts/ui_audit.js
```

真实引擎（Whisper / OCR）的测试带 `integration` 标记，默认被取消选择，
需要显式 `-m integration` 才会跑。

### Nightly：真实引擎冒烟

```bash
# 真实 Whisper / OCR 解码冒烟（需要本地模型；缺模型或缺依赖则干净 skip，不计失败）
pytest -m integration -k nightly

# 并发写冒烟（多线程写同一 SQLite 文件，验证不丢提交 / 重读一致）
pytest -m integration tests/test_concurrent_writes.py
```

`tests/test_nightly_asr_ocr.py` 走**真实** Whisper 解码与真实 RapidOCR 识别，
只断三件事：产出非空、语言标记有效、证据定位（时间戳 / bounding box）非空。
模型缺失或缓存损坏时是 **skip 而非 fail** —— 环境缺陷不该被误报成测试失败。
本机目前 whisper `base` 模型缓存损坏，因此该用例表现为 skip；OCR 用例真跑通过。
