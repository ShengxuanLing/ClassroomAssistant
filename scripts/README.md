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
| `ui_audit.js` | 更大范围的 UI 审计：多页面 × zh/es/ca 渲染、未翻译 CJK 检测、空态 / 加载态 / 错误态、长 token 原样渲染、大数据量渲染耗时与请求数上界。`node scripts/ui_audit.js` → `UI audit OK (N checks)`；`--dump <lang>` 可导出渲染快照用于改动前后逐字节比对；`--preview <lang> --out <file.html>` 输出一份**可直接在浏览器打开**的静态 HTML 快照（真实 index.html 骨架 + 内联 `styles.css` + 真实渲染结果，剥掉全部 `<script>`），用于视觉比对 —— 该路径不参与断言、不影响门禁。 |
| `ui_stress_check.js` | 大数据量（1000 KP / 500 students / 5000 exercises）下的渲染耗时与请求数上界检查，抓 N+1 与渲染崩溃。 |
| `e2e_session_picker.js` | 真实 HTTP 层面的端到端探针，覆盖 `ui_audit.js` / `ui_render_check.js` 测不到的一支（如 session 选择器）。 |

## 运行方式

```bat
node scripts/ui_render_check.js
node scripts/ui_audit.js
node scripts/ui_stress_check.js

rem 视觉比对: 生成可打开的静态快照 (不启动服务)
node scripts/ui_audit.js --preview zh --out cache/preview-dashboard-zh.html
```

Node 解释器用 `C:/Users/Rafae/.workbuddy-ai/binaries/node/versions/22.22.2-2/node.exe`
（Windows 上 `node` 可能不在 PATH 里）。
