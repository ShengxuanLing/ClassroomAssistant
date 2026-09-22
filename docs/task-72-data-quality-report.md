# TASK 72 — DATA QUALITY & MATERIAL INGESTION HARDENING

课堂助手 Task 72：真实课堂材料摄取与数据质量硬化。
本阶段不新增大型 parser；缺能力时显式标记 `Unsupported format` / `feature unavailable`，
绝不伪造证据。

---

## 1. 真实材料格式支持

系统对以下格式有真实处理路径（mock 引擎下走确定性桩，真实引擎下走 Whisper/OCR）：

| 格式 | 处理路径 | 真实引擎缺失时的行为 |
|---|---|---|
| TXT / Markdown | 直接读取 | 正常（纯文本） |
| PDF / DOCX | 文档解析 | `feature unavailable`（不伪造） |
| PPT / PPTX | 文档解析（若支持） | `feature unavailable`（不伪造） |
| 音频 (MP3/WAV/OGG) | Whisper ASR | `feature unavailable`（无 ASR 运行时） |
| 图片 / 手写板 | OCR | `feature unavailable`（无 OCR 运行时） |

**不支持的格式**（如 `.xyz`）：摄取层在注册期显式返回
`processing_status = FAILED`, `error = UNSUPPORTED_EXTENSION`。
系统**不会**为了"通过测试"而强行解析未知格式，也不会把不支持的文件假装成功。

> 10 个 integration 测试（真实 Whisper / OCR 模型未下载）按项目既定策略 skip，
> 由 `tests/test_production_gate.py` 断言测试存在、跳过情况如实记录。

---

## 2. 真实材料用例覆盖

| 用例 | 覆盖方式 |
|---|---|
| lecture notes / slides / teacher PDF / student notes | 文档摄取路径 |
| scanned document | OCR 路径（缺引擎时 `feature unavailable`） |
| mixed language document | 多语言标识保留（见 §3） |
| long / short document | 长度不限，空文件 → `ZERO_BYTE_FILE` |
| duplicate upload | content-addressing 判定，`duplicate = True` |
| renamed duplicate | 同内容不同文件名仍判重 |
| same file in different sessions | 仍判重（按内容，不按文件名） |

---

## 3. 多语言保留（72.3）

- `source language` / `original text` / `Evidence` / `Knowledge` / `UI translation`
  作为**不同的轴**分别保存。
- 源内容（西语 / 加泰语 / 中文 / 英语 / 混合）**绝不**被当作 UI 翻译。
- 语言标注遵循 AGENTS.md：`es` / `ca` / `zh` / `en` 显式区分。

---

## 4. OCR / Audio 缺失（72.4）

真实运行环境缺 OCR runtime / ffmpeg / ASR runtime / codec 时，对应能力返回
`feature unavailable`，**不会**生成假的 Evidence，也不会假装提取成功。

---

## 5. 断裂源处理（72.5）

- `deleted / moved / renamed / unreadable / corrupted` 源材料在溯源链上标记为
  `broken trace`（知识点的 evidence 引用了一个不在册的源材料 id）。
- 报告显式显示断链数量，**不**编造源材料。

---

## 6. 重复处理（72.6）

- content-addressing 设计保持不变（Task 72.6 明确要求"不得为测试通过而改变
  content-addressing"）。
- 同一文件 / 同一 session / 同一 course / 不同 session / 不同 course /
  不同文件名 的重复语义由摄取层显式记录（`duplicate = True`）。

---

## 7. 数据质量统计（72.7）

新增 `src/application/data_quality.py::collect_data_quality(workspace)`，从
`Workspace` 读出可观察指标，供本报告的"数据健康度"与 Pilot 自检使用。
统计项（规范点名）：

| 指标 | 含义 |
|---|---|
| `materials_processed` | `processing_status == COMPLETED` 的材料数 |
| `materials_failed` | `processing_status == FAILED` 的材料数（处理期失败，如损坏文件） |
| `materials_pending` | 注册但未完成处理的材料数 |
| `duplicates` | 持久化注册表中标记重复的 material 数（注：摄取期拒绝的重复不入库） |
| `evidence_extracted` | 已提取证据条数（内容寻址，跨课程共享，全局计数） |
| `knowledge_created` | 已创建知识点总数 |
| `unverified` | `validation_status == unverified` 的知识点数 |
| `conflicted` | `validation_status == conflicted` 的知识点数 |
| `broken_traces` | 溯源断链数（知识点引用了已不存在的源材料） |
| `unsupported_formats` | 不被支持的扩展名集合（显式标记，不强行 parser） |

所有读取都在防御性包裹里：单门课程读取异常不影响整体报告，也不让报告崩掉。

---

## 8. 复现方法

```bash
# 用真实 / mock 引擎跑完一轮材料摄取后：
PYTHONPATH=. Python/pythoncore-3.14-64/python.exe -c "
from src.application.workspace import Workspace
from src.application.data_quality import collect_data_quality
ws = Workspace('classroom-data')   # 真实 Pilot 数据目录
import json
print(json.dumps(collect_data_quality(ws), ensure_ascii=False, indent=2, default=str))
ws.close()
"
```

单元测试覆盖：`tests/test_data_quality.py`（空工作区归零、处理/知识计数、断链纯函数、
unsupported 摄取层上报）。

---

## 9. Bugs Found / Fixed

- **未发现伪造证据或静默吞掉不支持格式**。摄取层对不支持格式与缺失运行时的处理
  已符合 72.4 / 72.5 要求。
- **数据隔离加固**（见 Task 71.2）：新增数据目录守卫，确保 pytest / stress / UI audit /
  backup drill 不会在源码树里就地建库、污染真实 Pilot 数据。

## 10. Known Limitations

- 真实 Whisper / OCR 模型未在本机下载，相关 integration 测试 skip（10 个），与 1.0.0
  策略一致。
- 注册期被拒绝（unsupported extension）的材料不进入持久化注册表，因此
  `unsupported_formats` 持久化视图为 0；unsupported 由摄取响应显式上报（见 §1）。
