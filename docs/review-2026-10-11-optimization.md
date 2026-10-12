# 审查报告对账与优化记录（2026-10-11）

- 依据：外部审查报告 `ClassroomAssistant main 更新审查与后续开发计划`（2026-10-11，
  基线 `f3f38ea148f7a62a2a67b6d035d9f2a555f93457`，已核验上游 `origin/main`
  `24d4e731a1e811834e4f21c6c1b85e8858680c66`，隔离回归 1536 passed / 2 failed）。
- 本文件记录**逐条对账结果与本轮实际修改**，不重复报告全文。原则与项目铁律一致：
  证据优先、不把部分通过写成整体通过。

## 一、对账结论总表

| 报告条目 | 对账结果 | 本轮动作 |
| --- | --- | --- |
| P0 知识地图入口：路由调用未定义的 `pageKnowledgeMap` | **已闭环**（工作树在制品）：`src/web/app.js:1577` 路由 + `src/web/views/knowledge.js:868` 定义 + 后端路由 + 8 条测试全绿 | 仅验证，不改动 |
| P1 知识探索与证据回溯（API 契约、空数据语义） | **已闭环**：`Workspace.knowledge_map`（`src/application/workspace.py:2855`）只读投影；`unresolved_evidence_ids` 承载断链证据；空课程返回空数组 | 仅验证，不改动 |
| P1 音频证据起始时间偏差（0.7 vs 断言 0.4） | **已不复现**：`tests/test_evidence_ingestion.py::TestProvenance::test_audio_timestamps_preserved` 当前通过（5.2s 内三个目标测试 10 passed） | 不改动（避免"仅放宽断言掩盖误差"） |
| P1 OCR/PDF 静默零证据成功 | **部分缓解、语义重设计未做**：`_document_outcome`（`material_workflow.py:669`）区分 `DOCUMENT_PARSE_FAILED` 与 `NO_TEXT_EXTRACTED`；UI 有 warning 行 + 可操作文案（D1/D4）；纯图 PPTX 走 OCR 分支 | 记录为后续任务，不改契约 |
| P2 测试硬编码开发机路径 | **真实存在**：`tests/test_learning_workflow.py` 子进程测试写死 `D:/Project/Clases`（Windows 碰巧通过，Linux/CI 必红） | **已修复**（见 §二） |
| P2 ca/es 学生场景与浏览器验收 | 翻译表测试通过（12 passed）；`ui_audit.js` 613 / `ui_render_check.js` 314 全过；真实浏览器 E2E 非门禁（项目既有已知限制） | 不改动 |
| Bruce 4：AI 配置文档绝对化表述 | **真实存在**：「任何 OpenAI 兼容的服务都行」「key 不会泄露」 | **已修复**（见 §三） |
| 上游三项本地路径冲突（`src/web/app.js` / `styles.css` / `scripts/backup.bat`） | 属合并决策，需用户/维护者定夺 | 不动，提示见 §四 |

## 二、P2 修复：跨平台子进程测试

`tests/test_learning_workflow.py::TestResume::test_restart_in_subprocess_matches_in_process`：

- 旧写法：子进程脚本 `sys.path.insert(0, r'D:/Project/Clases')`，`subprocess.run(..., cwd="D:/Project/Clases")`。
- 新写法：`repo_root = str(Path(__file__).resolve().parents[1])`，`sys.path.insert` 与 `cwd`
  都用它；字符串拼接从混用 f-string 改为统一 `%` 格式化（与
  `tests/test_multi_course.py::test_restart_in_a_real_subprocess_agrees`、
  `tests/test_student_today.py::test_dashboard_survives_a_real_subprocess_restart` 同一先例）。
- 验证：Windows 上该测试通过；Linux/macOS 下不再有 `FileNotFoundError` 的绝对路径来源
  （本机无法直接跑 Linux，此点按代码推理成立：路径不再写死）。

## 三、AI 配置文档收窄（Bruce 第 4 条）

`README.md` §「可选：开启 AI 语义分析」与 `docs/getting_started.md` §1 同名小节：

- 「任何 OpenAI 兼容的服务都行（……）」→「provider 只按 OpenAI 兼容的 `/chat/completions`
  契约发请求（标准库 urllib 实现，超时与 429/5xx 退避内置）；……按这个契约实现的都能接，
  但『兼容』到什么程度以各家网关的实际行为为准，本项目只保证这一个契约」。
  依据实现：`src/llm_provider.py` 的 `OpenAICompatibleProvider`（标准库 urllib、禁 SDK）。
- 「key 不会泄露」→「key 只从进程环境读入 provider，**不到达浏览器前端**（`--check`、启动
  日志、操作日志、HTTP 响应里只有『有 / 无』两种形状）。剩下的风险面请自行评估：本机进程
  内存、运行日志、以及你自建网关那一侧的审计」。依据：`docs/configuration.md` §「密钥识别
  与脱敏」与 `tests/test_config_secrets.py` 的边界（脱敏是日志/输出层行为，不是密钥管理服务）。

## 四、明确未做（需要单独授权或设计）

1. OCR「成功且 0 证据」状态语义重设计（报告 P1 第 3 条）：新增 `needs_ocr` 类状态会改动
   材料/作业契约与三语 i18n 表，报告自身也写明「应单独设计状态语义」。
2. 真实 OCR/ASR 引擎与外部 AI 服务验收：本机无真实引擎；Mock 结果不得当作模型质量证明。
3. 浏览器 E2E 常态化（当前是 `reports/km-shots/` 的一次性截图证据，非门禁）。
4. 上游三项本地路径冲突的保留/合并方案（`src/web/app.js`、`src/web/styles.css`、
   `scripts/backup.bat`）：涉及用户自己的 WIP 与上游更新的取舍，须由用户决定。

## 五、本轮验证记录（2026-10-11，本机 Windows）

- `tests/test_knowledge_map.py` + `test_evidence_ingestion.py::TestProvenance::test_audio_timestamps_preserved`
  + `test_learning_workflow.py::TestResume::test_restart_in_subprocess_matches_in_process`：
  **10 passed**（含 P2 修复后的子进程测试）。
- 前端审计：`scripts/ui_audit.js` **613 checks OK**；`scripts/ui_render_check.js`
  **314 checks OK**。
- 未做全量回归（约 5300+ 条，单次运行超出工具时限）；按项目既有做法只跑受影响文件
  并如实记录范围。
