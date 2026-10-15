# TASK-110 Windows 安装包（setup.exe）实施计划

> 状态：**前半段已完成并验证**（打包可行性 + 路径改造 + 日志落盘），
> 后半段（托盘入口 / 正式打包资产 / Inno Setup / Setup.exe / 端到端验证）**未开始**。
> 本文写给接手的 agent：先读 §2 的已验证事实，**不要重做**；再按 §5 逐项实施；§6 的坑先看再动手。

---

## 1. 目标

把「课堂助手」打成 Windows 安装包：用户下载 `ClassroomAssistant-<version>-Setup.exe`，
双击安装 → 开始菜单启动 → 托盘常驻 + 浏览器自动打开界面 → 数据落在用户目录 → 系统卸载器卸载。

用户已拍板的三个决策：

1. **Whisper 模型不进安装包**（首次用音频功能时从 HuggingFace 下载）。
2. **不做代码签名**（会有 SmartScreen「未知发布者」警告，用户接受）。
3. **启动形态 B 档**：无控制台窗口 + 系统托盘图标。

---

## 2. 已验证的事实（有实测证据，不要重做）

| # | 结论 | 证据 |
|---|---|---|
| 1 | PyInstaller **onedir** 能打包此项目 | 构建成功，1m42s~2m20s，产物 **370.6 MB** |
| 2 | **OCR 在冻结环境真能工作** | 冻结 exe 跑 `verify-ocr` → `confidence 0.820281`、3 regions、`TEXT='Classroom\n12345\nHello'`，与普通环境**逐位一致** |
| 3 | 静态资源定位**不需要改** | `_internal/src/web` 16 文件齐全；`default_static_dir()`（`src/api/server.py`）的 `__file__` 推导在冻结态天然成立，`verify-static` 实测 OK |
| 4 | 数据目录改造生效 | 冻结 exe 建出 `%LOCALAPPDATA%\ClassroomAssistant\classroom-data\` 完整 8 目录 |
| 5 | 文件日志落盘生效 | `attach_file_logging()` 实测有内容、幂等、`reset_logging()` 能卸 |
| 6 | cv2 不能整体排除，但视频 IO DLL 可裁 | rapidocr 的 det/cls/rec 三处用 `cv2.imdecode/resize/cvtColor`；裁掉 `opencv_videoio_ffmpeg*` 省 **30 MB** 且 OCR 结果不变 |
| 7 | `ApiServer.start()` 已在后台 daemon 线程跑 `serve_forever` | `src/api/server.py` ⇒ **托盘不需要改并发模型**，主线程跑 pystray 即可 |

**体积**（优化后）：raw 370.6 MB；zlib 估算压缩率 40.6%；Inno Setup 用 LZMA2，预计 Setup.exe **~130–150 MB**。
大头排行：cv2 112 / av.libs 65 / ctranslate2 60 / onnxruntime 43 / numpy.libs 21（单位 MB）。

---

## 3. 已完成的改动（5 个文件，勿重做、勿回滚）

| 文件 | 规模 | 内容 |
|---|---|---|
| `src/application/config.py` | +29/−0 | 新增 `is_frozen()` / `installed_data_root()`；`default_values()` 在冻结态把基准目录换成 `%LOCALAPPDATA%\ClassroomAssistant`。**设计要点：只在冻结态改行为，开发/测试路径逐字不变** |
| `src/application/launcher.py` | +8/−3 | **修一个既有休眠缺陷**：`validate_environment` 的 data directory 检查只查一层父目录，而同一函数第 4 段（数据库检查）已用 `_nearest_existing_writable()` 向上找多级祖先。改为统一用后者 |
| `src/application/logging_setup.py` | +85/−2 | 新增 `attach_file_logging()`（滚动文件 handler）、`LOG_FILENAME="classroom.log"`、`_FILE_HANDLER_MARKER`；`configure_logging` 增加 `sys.stderr is None` 保护（`--noconsole` 下 `StreamHandler(None)` 每次 emit 都抛 AttributeError 并被 logging 静默吞掉）；`reset_logging()` 改为同时移除文件 handler |
| `src/application/bootstrap.py` | +10/−1 | `build_runtime()` 在非 pytest 环境下调用 `attach_file_logging(config.logs_dir, clock=resolved_clock)` |
| `tests/test_startup.py` | +21/−0 | 回归守卫 `test_environment_accepts_multi_level_missing_data_dir` |

**测试基线**（实跑）：`test_startup.py` 47 passed；`test_logging_setup.py`+`test_bootstrap.py` 140 passed, 2 skipped；
`test_config.py`+`test_config_secrets.py` 296 passed。

---

## 4. 环境与工具（精确路径）

- **打包 venv**：`C:/Users/Rafae/.workbuddy-ai/binaries/python/envs/clases-pack`
  （Python 3.13.14；已装 requirements.txt + **pyinstaller 6.22.3** + **pystray 0.19.5** + hooks-contrib 2026.8）
- **Inno Setup**：`D:\Program Files\Inno Setup 7\ISCC.exe`（用户安装的，**用这个**）
- **spike 资产**：`temp/pack_spike/`（`entry.py` / `spike.spec` / `make_icon.py` / `mutate_check.py` / `dist/`）
- **应用图标源**：`temp/pack_spike/app.ico`（多尺寸 16–256，深蓝圆角方块 + 白色三节点知识图）
- ⚠️ 本机**无 Docker、无 7-Zip**
- ⚠️ 本机**无 GitHub 凭据**，`git push` 必失败 ⇒ CI 路线不可用，只能本机构建

---

## 5. 未完成任务（按顺序做）

### 5.1 托盘入口 `src/application/tray.py`（新建）

- 用 `pystray` + `Pillow`（均已在依赖中）。
- 菜单：`打开界面`（default，双击触发）/ `打开数据目录` / 分隔 / `退出`。
- 进程模型：`Launcher(config).start(block=False)` → `icon.run()`（主线程）→ quit 时 `launcher.stop()` + `icon.stop()`。
- 图标路径同时兼容源码态与冻结态（参考 `config.is_frozen()` 的写法）。
- 图标资源放 `src/application/assets/app.ico`（**从 `temp/pack_spike/app.ico` 复制**）。
- 提供 `python -m src.application.tray` 入口。
- **验收**：`python -m src.application.tray` 能起服务并在托盘显示图标；「打开数据目录」能开资源管理器；「退出」后进程干净退出、`classroom-data/.run/` 下的 PID/port 标记被清掉。

### 5.2 正式 PyInstaller spec + 构建脚本

- 把 `temp/pack_spike/spike.spec` 固化为正式资产（建议 `packaging/classroom-assistant.spec`）。
- 入口换成托盘入口（5.1），并加 `console=False`（B 档无控制台）。
- 保留已验证的收集策略：`src/web` 作 datas；`faster_whisper` / `ctranslate2` / `onnxruntime` / `av` / `rapidocr_onnxruntime` 走 `collect_all`。
- 保留已验证的裁剪：排除 `opencv_videoio_ffmpeg*`（30 MB）。
- **新增**：收集 `src/application/assets/app.ico` 到 `src/application/assets/`。
- 写 `packaging/build.py`（或 `.cmd`）串起：复用 venv → pyinstaller → ISCC。
- **验收**：产出无控制台的 `dist/ClassroomAssistant/ClassroomAssistant.exe`；`verify-ocr` / `verify-static` 两个探针入口仍可用且结果与 §2 一致。

### 5.3 Inno Setup 脚本 `packaging/installer.iss`

- 参考 `D:\Program Files\Inno Setup 7\Examples\*.iss`。
- 关键指令：
  - `PrivilegesRequired=lowest`（装到用户目录，**不需要管理员、不弹 UAC**）
  - `DefaultDirName={localappdata}\Programs\ClassroomAssistant`
  - `DefaultGroupName=课堂助手`；建开始菜单（必要时桌面）快捷方式
  - `SetupIconFile` 指向 `app.ico`；`Compression=lzma2/max`
  - `AppVersion` 与 `src/__init__.py` 的 `__version__` 对齐（当前 `1.0.1`）
  - `UninstallDisplayName` / `UninstallDisplayIcon`
- **验收**：`ISCC.exe packaging/installer.iss` 产出 `ClassroomAssistant-1.0.1-Setup.exe`，体积 100–200 MB。

### 5.4 端到端验证（最重要，不许跳过）

1. 真装一遍（静默：`Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-`）。
2. 从开始菜单启动 → 托盘图标出现、浏览器自动打开、界面能渲染。
3. 确认数据目录是 `%LOCALAPPDATA%\ClassroomAssistant\classroom-data`，**不是**安装目录。
4. 确认 `classroom-data/logs/classroom.log` **有内容**（这是日志改造的目的，之前实测为空）。
5. 跑一次**真实 OCR**（上传一张带文字的图）—— 不许只做 import 级检查。
6. 卸载 → 确认程序目录与开始菜单项被清掉；确认 `%LOCALAPPDATA%\ClassroomAssistant` **未被删**
   （设计如此：卸载不删用户数据）—— 若与此意不符，先问用户再改。
7. **每一步都贴实际输出**，不许只报"成功"。

### 5.5 文档

- README 的「怎么安装 / 怎么启动」章节现在是开发者路径（`python -m venv` + `start.bat`），需更新。
- 在 `docs/` 补一条安装包使用说明：下载 → 安装 → 启动 → 数据位置 → 卸载 → Whisper 模型首次下载。

---

## 6. 已知陷阱（踩过的，先看再动手）

1. **行尾**：`.gitattributes` 规定 `*.py = LF`。改完必须用
   `pathlib.Path(f).read_bytes().count(b'\r\n')` 复查 —— `bootstrap.py` 曾被写成**全文件 436 CRLF**（已修）。
   注意本机 `core.autocrlf=true` ⇒ `git status` 干净不代表行尾对，只有真多出 `\r\n` 的文件才要修。
2. **`asr=real ocr=real` 只是 import 级证据**：`is_local_ocr_available()` 只做 `find_spec`。
   要证明 OCR 真能用，必须喂一张真实图片，看 `confidence` 和识别出的文本。
3. **变异测试**：证明回归守卫不是空跑的唯一办法。参考 `temp/pack_spike/mutate_check.py`
   （自带还原，并在 finally 里逐字节校验还原成功）。
4. **外部安装/卸载程序会假成功**：`EXIT=0`/`EXIT=1` 但文件系统没变。必须复查目录。
   （`D:\tools\InnoSetup7` 的卸载就是这么失败的。）
5. **测试大量用 `log_stream=StringIO()` 捕获日志** ⇒ `configure_logging` 的流语义不能改，
   文件日志必须走独立 marker（`_FILE_HANDLER_MARKER`）。

---

## 7. 不属于本任务、不要碰的文件

工作区里这些改动是**另一个 agent** 留下的（它已停止工作），与本任务无关：
`src/web/app.js`、`src/web/index.html`、`src/web/styles.css`、`scripts/ui_audit.js`、
`scripts/ui_render_check.js`、`tests/test_exercise_ui.py`、`tests/test_web_ui_invariants.py`，
以及未跟踪的 `temp_appjs.py`、`docs/agent-handoff-context.md`、`docs/agent-task-plan-template.md`。

⇒ **提交时只 `git add` 本任务的文件，绝不 `git add -A` / `git add .`。**
⇒ 跑测试若发现失败，先用 `git stash`/对比判断是不是这些文件引入的，别当成本任务的回归。

---

## 8. 需要用户处理的事（agent 做不了）

1. `D:\tools\InnoSetup7` 是误装的一份 Inno Setup，`unins000.exe /VERYSILENT` 卸载**失败**（目录仍在）。
   需用户在「设置 → 应用」里手动卸载，或授权 agent 用更强制的办法删。
2. `%LOCALAPPDATA%\ClassroomAssistant\` 是验证期建的数据目录（空库），用户确认是否清理。
3. `git push` 本机不可用；最终发 Release 需用户在有凭据的环境执行。

---

## 9. 验收总标准

一条链跑通：**打包 → 安装 → 启动 → 出界面 → OCR 出结果 → 日志有内容 → 卸载干净**，
每一环都贴得出实际输出。
