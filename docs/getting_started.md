# 快速开始

目标：从零到跑通一条完整的「材料 → 证据 → 知识点 → 溯源链」，并看到审核界面。

---

## 1. 环境

- Python **3.10+**（仓库自带可移植解释器 `Python/pythoncore-3.14-64/python.exe`，3.14.7）
- 操作系统：Windows 优先（启动脚本是 `.bat`）；Linux / macOS 可用命令行方式
- 不需要 GPU，不需要 FFmpeg 可执行文件（PyAV 自带库），不需要联网（模型下载除外）

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
```

### 关于「真实引擎」和「Mock 引擎」

装齐 `requirements.txt` 后，ASR 和 OCR 都是**真实**的：

| 能力 | 实现 | 是否随包提供模型 |
| --- | --- | --- |
| 音频转录 | `faster-whisper` + CTranslate2 | 否，首次使用从 Hugging Face 下载 |
| 图片 OCR | `rapidocr-onnxruntime` | 是，PP-OCR ONNX 模型打进 wheel，完全离线 |
| PDF | `pypdf` | — |
| DOCX | `python-docx` | — |

如果这些依赖缺失或加载失败，运行时**不会**静默降级成假数据：它会回落到 Mock，
并把 `asr_mode` / `ocr_mode` 标成 `"mock"`、在界面上挂显式横幅。**Mock 的输出不是
转录或识别结果**，只用来验证流程。

## 2. 启动

```text
scripts\start.bat
```

脚本做四件事：环境自检 → 建数据目录 → 迁移数据库 → 绑定端口并打开浏览器。
浏览器会停在 `http://127.0.0.1:8765/`。

不想开浏览器、只想确认环境是否健康：

```text
scripts\health.bat
```

它打印六项 `[OK]` / `[FAIL]` 自检（Python 版本、依赖、数据目录、数据库、
模型可用性、端口）以及一份装配快照，然后退出，**不占端口**。

停止：

```text
scripts\stop.bat
```

`stop` 会先确认「记录在 PID 文件里的进程确实是本应用」再杀。确认不了就只清理
陈旧标记并报错退出，不会去杀一个可能已经复用了同一 PID 的无关程序。
确实要强杀用 `stop --force`。

## 3. 创建第一门课程

界面：概览页在没有任何课程时会显示一段可直接复制的 API 指引。

命令行：

```bash
curl -X POST http://127.0.0.1:8765/api/courses \
  -H "Content-Type: application/json" \
  -d '{"name": "Álgebra Lineal", "code": "ALG", "language": "es"}'
```

响应里会给出 `course_id`（内容寻址、确定性生成，不是随机 UUID）。

再建一堂课：

```bash
curl -X POST http://127.0.0.1:8765/api/sessions \
  -H "Content-Type: application/json" \
  -d '{"course_id": "<course_id>", "session_number": 1, "date": "2026-03-01", "title": "Tema 1"}'
```

## 4. 上传材料

界面：**材料**页 → 「上传材料」表单，可选课堂和语言。

命令行（multipart）：

```bash
curl -X POST "http://127.0.0.1:8765/api/materials?course_id=<course_id>&session_id=<session_id>" \
  -F "file=@clase-01.mp3"
```

支持 `pdf / docx / txt / md / 音频 / 图片`。要点：

- 上传后文件被**复制**进 `classroom-data/{audio|images|documents}/`，
  文件名**保持原样**（含中文、重音字符、空格）。
- **同名 + 同内容**只登记一次：第二次上传会返回已有记录并标 `duplicate: true`。
- 超出 `max_upload_size`（默认 200 MiB）、扩展名不支持、零字节文件都会被拒绝，
  并给出结构化错误码（`OVERSIZED_FILE` / `UNSUPPORTED_EXTENSION` / `ZERO_BYTE_FILE`）。

## 5. 处理

单个材料：

```bash
curl -X POST http://127.0.0.1:8765/api/materials/<material_id>/process
```

整堂处理：

```bash
curl -X POST http://127.0.0.1:8765/api/sessions/<session_id>/process
```

处理是**串行**的：单个材料失败不会中断其余材料，失败项可以单独重试
（`POST /api/materials/{id}/retry`）。重试只会对**可重试**的失败生效；
解析失败（文件本身坏了）是非可重试的，重试会被明确拒绝而不是无限重试。

处理产出的是**证据**，不是知识点。查看某个材料的证据：

```bash
curl "http://127.0.0.1:8765/api/materials/<material_id>/evidence?course_id=<course_id>"
```

## 6. 看知识点与溯源链

```bash
curl "http://127.0.0.1:8765/api/knowledge?course_id=<course_id>"
curl "http://127.0.0.1:8765/api/knowledge/<knowledge_id>/trace?course_id=<course_id>"
```

界面上的**知识点详情页**就是这条链的可视化：知识点 → 每条证据（含原文、语言、
定位）→ 每条证据引用的源材料（文件名、哈希、受管路径）。

如果某条证据引用的材料不在课程注册表里，页面会显式打出「溯源断链」并说明
「请勿据此下结论，需人工核查」——不会静默跳过。

## 7. 人工审核

`GET /api/reviews?course_id=...` 列出所有需要人工决策的知识点。

四种动作：

```text
POST /api/reviews/{knowledge_id}/confirm
POST /api/reviews/{knowledge_id}/reject
POST /api/reviews/{knowledge_id}/keep-unverified
POST /api/reviews/{knowledge_id}/resolve-conflict
```

两条硬规则：

1. **审核状态只能由人工改变。** 读取、重新处理、重启、重新载入数据库都不会把
   一个待审项变成已确认。
2. **冲突项不能直接确认。** 对处于 `CONFLICTED` 的知识点调 `confirm` 会被拒绝，
   必须走 `resolve-conflict` 并显式选择要采信的证据。系统永不替你挑。

## 8. 学生与练习

**注册学生在界面上就能做：** 打开 **学生** 页，页面顶部有一个注册表单
（学号 / 标识必填，姓名可选）。提交后列表立刻刷新，不需要手写 HTTP 请求。

`student_id` 是学生在这门课里的身份。同一个 `student_id` 重复注册是**幂等**的
（返回 200 与同一条记录）；但**换一个姓名**重复注册会被判成 `CONFLICT`（409）
—— 那说明要么学号写错了，要么这个学号已经属于别人。

同样的事也可以直接调 API：

```bash
# 注册学生
curl -X POST http://127.0.0.1:8765/api/students \
  -H "Content-Type: application/json" \
  -d '{"course_id": "<course_id>", "student_id": "2026-001", "display_name": "Ana"}'

# 建练习（出题依据是证据，不是模型凭空生成）
curl -X POST http://127.0.0.1:8765/api/exercises -H "Content-Type: application/json" -d '{...}'

# 提交答案
curl -X POST http://127.0.0.1:8765/api/answers -H "Content-Type: application/json" \
  -d '{"course_id": "...", "student_id": "...", "exercise_id": "...", "submitted_value": "..."}'

# 看评估
curl http://127.0.0.1:8765/api/evaluations/<answer_id>
```

**答错不会修改知识库。** 评估是对「学生这一答」的判定，不是对知识的核验；
`validation_status` / `review_status` / `knowledge_score` / 证据集在答错后逐字节不变。
评估响应里也显式带着 `is_fact_verification: false` / `affects_knowledge_base: false`。

界面上的**练习页**在提交前**不会**把答案渲染进 HTML——这不是靠 CSS 隐藏，
而是后端根本不返回，`scripts/ui_render_check.js` 用真实执行断言了这一点。

## 9. 备份

**注意：备份目前没有 HTTP 端点，也没有命令行入口。** 它是一层 Python API
（`src.backup`）：

```python
from src.backup import create_backup, list_backups, restore_backup

result = create_backup("classroom-data", label="before-exam")
print(result.archive_path, result.manifest.material_file_count)

for info in list_backups("classroom-data"):
    print(info.archive_path, info.created_at)

restore_backup(result.archive_path, "classroom-data-restored")
```

归档落在 `classroom-data/backups/*.zip`。格式、校验规则与恢复流程详见
[backup_restore.md](backup_restore.md)。

## 10. 下一步

- 想改端口 / 数据目录 / 上传上限 / Whisper 模型 → [configuration.md](configuration.md)
- 想知道为什么这样分层、这样测试 → [architecture.md](architecture.md)
- 想知道业务数据如何真正落盘、重启如何恢复、哪些没有验证 →
  [final_persistence_report.md](final_persistence_report.md)
- 出问题了 → [troubleshooting.md](troubleshooting.md)
- 想知道哪些限制是真实存在的 → [README 的「已知限制」](../README.md#已知限制明确说明不掩饰)
