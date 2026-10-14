# scripts/ — 入口与检查脚本

本目录只放**生产入口**与**前端检查 harness**。`dev-archive/` 收纳历史上用过、现已
不再被任何代码 / 测试引用的临时脚本（`_tmp_*.js`、`fix*.py`、`gen*.py`、`*.b64`、
`_tiny.png` 等），仅供追溯，不保证可运行。

## 生产入口（3 个 .bat）

| 脚本 | 作用 |
|---|---|
| `start.bat` | 启动课堂助手服务（默认 `127.0.0.1:8765`），按配置创建 `classroom-data/` 目录树与数据库。 |
| `health.bat` | 对运行中的服务做一次健康检查（等价于 `--check` / `health`），只读探针。 |
| `stop.bat` | 优雅停止服务：先发停止信号，再 `join` 工作线程（5s 超时后强制断开活连接）。 |

> 这些 `.bat` 只能从 Windows 直接双击或在 `cmd` 里跑；它们依赖 `run_tests.cmd` 同款的
> 便携解释器解析逻辑。

## 前端检查 harness（零构建，Node 直接跑）

前端是**零构建**的：浏览器按 `<script>` 顺序加载 `src/web/*.js`，没有打包步骤。
下面三个 Node 脚本在 CI / 本地用最小 DOM 桩**真实执行** `app.js` 的页面函数，是
"改了 UI 到底有没有破坏中文渲染 / XSS 防护 / 路由"的承重测试。

| 脚本 | 作用 |
|---|---|
| `ui_render_check.js` | 真实执行各页面渲染函数，对渲染出的 HTML 字符串做断言（含 `esc()` 转义、证据原文逐字渲染）。`node scripts/ui_render_check.js` → `UI RENDER CHECK: OK (N checks)`。 |
| `ui_audit.js` | 更大范围的 UI 审计：多页面 × zh/es/ca 渲染、未翻译 CJK 检测、空态 / 加载态 / 错误态、长 token 原样渲染、大数据量渲染耗时与请求数上界，以及 **设计系统守卫**（token 齐全、深色覆写完整、引用即定义、焦点环、`prefers-reduced-motion`、卡片无投影、顶栏高亮、**WCAG 对比度逐对实算**）。`node scripts/ui_audit.js` → `UI audit OK (N checks)`；`--dump <lang>` 可导出渲染快照用于改动前后逐字节比对；`--preview <lang> [--page <name>] --out <file.html>` 输出一份**可直接在浏览器打开**的静态 HTML 快照（真实 index.html 骨架 + 内联 `styles.css` + 真实渲染结果，剥掉全部 `<script>`），用于视觉比对 —— 该路径不参与断言、不影响门禁。`--page` 取 `ui_audit.js` 里 `PAGES` 的名字（`dashboard` / `course` / `materials` / `knowledgeMap` / …），名字写错会**当场报错并列出可选值**（静默退回 dashboard 会让人对着一张“以为是课程页”的快照改半天 CSS）。 |
| `ui_stress_check.js` | 大数据量（1000 KP / 500 students / 5000 exercises）下的渲染耗时与请求数上界检查，抓 N+1 与渲染崩溃。 |
| `e2e_session_picker.js` | 真实 HTTP 层面的端到端探针，覆盖 `ui_audit.js` / `ui_render_check.js` 测不到的一支（如 session 选择器）。 |
| `ui_mutation_check.py` | **变异测试**（Python）：故意改坏 `styles.css` / `app.js` 的 18 处视觉不变量（删掉焦点环、卡片加回投影、token 拼错、对比度调淡、sticky 偏移写死…），逐条断言 `ui_audit.js` 必须**报红且报对**。断言通过不等于断言有效 —— 2026-10-14 就有过一条“焦点环”断言被画布规则意外满足、整条删掉仍全绿。它会临时改写 `src/web/` 下的真实文件（try/finally 还原），**不要与其它检查同时跑**。 |

## 运行方式

```bat
node scripts/ui_render_check.js
node scripts/ui_audit.js
node scripts/ui_stress_check.js

rem 视觉比对: 生成可打开的静态快照 (不启动服务), --page 选页面
node scripts/ui_audit.js --preview zh --out cache/preview-dashboard-zh.html
node scripts/ui_audit.js --preview es --page course --out cache/preview-course-es.html

rem 断言有效性: 故意改坏 18 处视觉不变量, 看审计是否精准报红
python scripts/ui_mutation_check.py
```

Node 解释器用 `C:/Users/Rafae/.workbuddy-ai/binaries/node/versions/22.22.2-2/node.exe`
（Windows 上 `node` 可能不在 PATH 里）。
