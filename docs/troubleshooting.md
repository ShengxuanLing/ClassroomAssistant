# 故障排查

先跑一次自检：

```text
scripts\health.bat
```

它打印六项 `[OK]` / `[FAIL]`（Python 版本、依赖、数据目录、数据库、模型可用性、
端口）以及一份装配快照，**不占端口**。多数问题看这一屏就能定位。

想确认最终生效的配置（不开数据库）：

```bash
python -m src.application.cli --print-config
```

---

## 启动类

### `start.bat` 一闪而过 / 没有任何反应

`.bat` 里 `cd` 到仓库根目录后调 `python -m src.application.launcher start`。
如果 Python 找不到，脚本会回落到裸 `python`，而裸 `python` 可能是 Windows 商店的
占位程序（它什么都不做就退出）。

排查：

```text
scripts\health.bat
```

如果 health 也一闪而过，说明解释器没找到。显式指定：

```bash
Python\pythoncore-3.14-64\python.exe -m src.application.launcher health
```

### 端口被占用（退出码 3）

```
PortInUseError
```

`launcher` 会把 `OSError` 翻成友好提示而不是 traceback。三种处理：

```bash
python -m src.application.launcher start --port 0        # 由系统分配一个空闲端口
python -m src.application.launcher start --port 8877     # 换一个端口
scripts\stop.bat                                         # 停掉本应用占用的实例
```

`--port 0` 时实际端口写在 `<data_dir>/.run/launcher.port` 里，
`launcher` 会把它读出来告诉你。

### `stop` 说 "Refusing to kill PID ..."

这是**保护**，不是故障：

```
stale PID file removed: no classroom-assistant service is listening on
127.0.0.1:8765. Refusing to kill PID 9200 (that PID may have been reused by
an unrelated program). Re-run 'stop --force' to kill it anyway.
```

原因：PID 文件里的那个进程已经不在了，而这个号码被系统**复用**给了一个无关程序。
修复前 `stop` 会照着 PID 直接杀，实测确实会杀掉一个无辜的 `time.sleep(300)` 进程。

现在的 `stop` 会先握手：读 `launcher.port`，对 `/api/health` 发一次请求，
递归确认响应里的 `application` 字段是本应用。确认不了就只清理陈旧标记、报错退出
（退出码 3）。

确实要强杀（比如服务已经半死、health 不响应但进程确实是你启动的）：

```text
scripts\stop.bat --force
```

### 启动成功但浏览器没打开

浏览器打开走 `webbrowser.open`，失败会被静默吞掉（尽力而为，不该因此让启动失败）。
手动打开 `http://127.0.0.1:8765/` 即可。

### 绑定非回环地址被拒绝

默认只允许 `127.0.0.1` / `localhost` / `::1`。这是刻意的：本服务没有鉴权。
真要绑：

```bash
python -m src.application.launcher start --host 0.0.0.0 --allow-remote
```

绑之前请确认网络环境可信。

### `data_dir` 被拒绝

```
data_dir must not live inside the project's src/ directory
```

这是 AGENTS.md「用户数据绝不写入 `src/` 或 `tests/`」的可执行版本，
不是误报。换一个位置：

```bash
python -m src.application.launcher start --data-dir D:\classroom-data
```

## 处理类

### 音频 / 图片处理出了 Mock 结果

`GET /api/health` 里看 `asr_mode` / `ocr_mode`：

```json
{ "processing": { "asr": "mock", "ocr": "mock" } }
```

`mock` 表示真实运行时不可用，流程回落到 Mock。**Mock 输出不是转录或识别结果。**

排查：

```bash
python -m src.application.cli --check
```

- ASR 不可用 → 检查 `faster-whisper` / `ctranslate2` 是否装上；
  `pip install -r requirements.txt`。首次使用需要联网下载模型到
  `~/.cache/huggingface`。
- OCR 不可用 → 检查 `rapidocr-onnxruntime==1.2.3`。**版本必须精确是 1.2.3**：
  1.3.x 声明 `Requires-Python <3.13`，在 3.13/3.14 上装不上。

### 材料处理失败，重试被拒绝

失败分两类：

| 类别 | 例子 | 可重试 |
| --- | --- | --- |
| 临时 | 模型不可用、磁盘暂时满、超时 | 是 |
| 永久 | PDF / DOCX 本身损坏、格式不支持 | 否 |

永久失败重试会被明确拒绝——这是**刻意的**，避免无限重试掩盖真正的问题。
检查 `GET /api/processing/{material_id}` 里的 `error` / `error_detail` /
`retryable`。

### 上传被拒绝

| 错误码 | 含义 | 处理 |
| --- | --- | --- |
| `UNSUPPORTED_EXTENSION` | 扩展名不在允许集合里 | 看 `docs/user_guide.md` 的支持类型表 |
| `OVERSIZED_FILE` | 超过 `max_upload_size`（默认 200 MiB） | 调大 `CLASSROOM_MAX_UPLOAD_SIZE` 或切分文件 |
| `ZERO_BYTE_FILE` | 0 字节 | 源文件就是空的，重新导出 |

### 上传成功但看起来"没有新增"

**同名 + 同内容只登记一次。** 第二次上传会返回已有记录并标 `duplicate: true`。
这不是丢文件，是去重。

### 磁盘满

处理中途磁盘满时，错误是 `PROCESSING_ERROR` 且带 `OSError(28)`。
`temp/` 里的中间产物可以安全删除；材料副本在 `audio/` `images/` `documents/`。

## 知识类

### 知识点显示「溯源断链」

页面上的含义：

```
溯源断链
该证据引用的材料 <material_id> 在本课程材料注册表中不存在。
请勿据此下结论，需人工核查。
```

意思是某条证据引用的材料**不在课程注册表里**。可能原因：

1. 材料注册表 JSON（`materials/<course_id>.json`）被手工改过或删过；
2. 材料文件在磁盘上，但注册表里没有；
3. 恢复备份之后注册表没跟着回来（见下面的「恢复后看不到课程」）。

**不要**据此下结论——这正是这句话存在的意义。修复方式是把材料重新登记，
或者修好注册表。

### 想确认「是不是所有知识点都能追溯」

```bash
python -m pytest tests/test_hardening_traceability.py -q
```

它在放大到 90 个知识点的验收数据集上抽样 50 个，逐条走
「知识点 → 证据 → 材料 → 源定位」。

### 冲突项无法确认

对 `CONFLICTED` 的知识点调 `confirm` 会被拒绝（`INVALID_INPUT`，消息里带
`CONFLICTED`）。必须用：

```bash
POST /api/reviews/{knowledge_id}/resolve-conflict
```

并**显式选择**采信哪一侧的证据。系统永不替你挑——这是设计，不是 bug。

### 审核状态自己变了

不会。以下行为都**不会**把待审项变成已确认：读取、重新处理、重启、重新载入
数据库。只有显式的人工审核动作会。如果你观察到了相反的情况，那是真 bug，
`tests/test_hardening_truth_safety.py::TestReviewSafety` 是复现起点。

## 备份 / 恢复类

### 恢复后看不到课程 / 知识点 / 学生

如果恢复之后确实看不到业务对象，那是**真故障**，不是已知限制。Task 48–55 之后运行时
`data_dir/database/classroom.sqlite` 是**活库**，业务对象全部落盘；正常恢复的库应当
逐字段还原课程 / 知识点 / 审核记录 / 学生 / 作答 / 评估 / 计划 / 路径。

应先排查：备份归档是否真的包含数据库快照（`list_backups()` 看条目）、恢复是否真的替换了
`database/` 下的文件、`-wal` / `-shm` 边车是否被清理干净。复现起点是
`tests/test_hardening_backup_drill.py`（含 `TestDrillGap`，已改写为真正的恢复验收）与
`tests/test_production_gate.py::TestFileDatabaseConsistency`。
细节见 [backup_restore.md](backup_restore.md#7-已知限制明确说明不掩饰)。

### 备份"卡住"不返回

已修复，但要知道原理：SQLite 在线备份 API 在**事务未提交**时会永久挂起
（连空事务也会）。现在 `Database.backup_to()` 会显式报
`STORAGE_TRANSACTION_FAILED`。看到这个错误就说明有事务没关——
先 commit 或 rollback 再备份。

### 恢复被拒绝

```
backup failed validation, refusing to restore: ...
```

`validate_backup` 会在碰任何现有数据**之前**把问题逐条列出来。常见原因：
CRC 不符、清单里的 SHA-256 对不上、路径穿越、zip bomb 超限
（`MAX_UNCOMPRESSED_BYTES = 8 GiB` / `MAX_COMPRESSION_RATIO = 2000`）。

想先看报告而不恢复：

```python
from src.backup import validate_backup
report = validate_backup("classroom-data/backups/x.zip")
print(report.ok, report.errors)
```

### 恢复时 `os.replace` 失败（Windows `WinError 5`）

活库上还开着 SQLite 连接时，Windows 会拒绝替换文件。现在会翻成
`DatabaseInUseError` 而不是含糊的 `OSError`。先停掉服务再恢复。

### 恢复来自更新版本的归档

```
backup schema version 3 is newer than this build supports (2)
```

**这是保护。** 以前这里只在"需要升版"时检查版本，于是"未来版本写的备份"会
静默恢复成功——用户以为数据回来了，实际上库里是一份本代码不认识的 schema，
而原数据已经被替换掉了。现在这个检查在第一个破坏性步骤之前发生。

升级到能读那个版本的程序，或者从更早的归档恢复。

## 界面类

### 切换语言后页面还是中文

已修复（Task 47.8）。修复前语言选择器只对**学生页 / 练习页**生效，
概览 / 知识点 / 材料 / 待审核 / 课程 / 课堂 / 溯源详情这 7 个页面的正文全是
硬编码中文。

现在的保证是：**es / ca 模式下渲染出的 HTML 里不出现任何 CJK 字符**。
所有测试夹具数据都是纯 ASCII，所以 CJK 只可能来自漏译的界面文案——
这条断言没有假阳性。由 `scripts/ui_audit.js` 断言，共 93 项检查。

如果又出现了，跑：

```bash
node scripts/ui_audit.js
```

它会指出是哪个页面、哪一段漏译了。

### 窄视口下页面横向溢出

表格单元格里的文件名 / `content_hash` / `material_id` 是**超长且无空格**的
token。现在有两层处理：

- `table.data` 的单元格有 `overflow-wrap: anywhere`（长 token 折行）；
- `@media (max-width: 900px)` 下 `.card` 变成横向滚动容器
  （表格在自己卡片里滚动，而不是把整个页面撑宽）。

由 `scripts/ui_audit.js` 的 narrow viewport 检查断言。

### 页面切换时短暂显示上一页

已修复。`route()` 现在在发起请求**之前**先切到加载态
（`common.loading`）。修复前视图里会残留上一页的内容，没有任何"正在加载"的信号
——用户看到的是一份已经过期的页面。

### 练习页看不到答案

这是**设计**。提交前后端**根本不返回**答案
（`correct_choice_id` / `expected_answer` / `fill_blank` / `explanation`），
不是靠前端隐藏。`scripts/ui_render_check.js` 用真实执行断言了这一点。

## 测试类

### `python` 找不到 pytest / pypdf / onnxruntime

系统 Python 上没装依赖。用仓库自带的可移植解释器：

```bash
Python\pythoncore-3.14-64\python.exe -m pytest -q -m "not integration"
```

### 真实引擎的测试被跳过了

带 `integration` 标记的测试默认被取消选择（`pytest.ini` 里注册了该标记）。
要跑：

```bash
Python\pythoncore-3.14-64\python.exe -m pytest -q -m integration
```

需要真实模型可用，否则会跳过。

### 想跑 UI 渲染 / 审计

```bash
node scripts/ui_render_check.js    # 练习页：答案不泄露、页面函数能跑通
node scripts/ui_audit.js           # 47.8 的 11 项 UI 审计（93 项检查）
```

需要 Node。`tests/test_exercise_ui.py` 会自己找 node，找不到就 skip（不会假通过）。
