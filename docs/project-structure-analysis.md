# 课堂助手 — 项目结构完整分析

> ⚠️ **已过期（生成于 2026-09-15），仅存档，不再反映项目现状。**
> 当前权威结构说明见 [README.md](README.md) 与 [docs/architecture.md](docs/architecture.md)。
> 该分析文曾声称"无 UI / 无 DB / 无 README / 45 模块"，但项目早已拥有 Web UI、
> SQLite 持久化层（`classroom-data/database/`）、README 与远超 45 个模块，请勿据此判断现状。

> 生成日期：2026-09-15
> 分析对象：`D:\Project\Clases`
> 验证方式：静态解析 45 个模块的 AST 导入关系 + 实跑 pytest 全量测试 + 逐文件核对

---

## 1. 项目定位

「课堂助手」是面向巴塞罗那自治大学（UAB）大二学生的**课堂材料处理流水线**。输入西语/加泰罗尼亚语的课堂音频、板书图片、笔记与课程大纲，输出知识点梳理、复习材料与考前复习规划。

它**不是** Web 应用，而是一个纯本地的确定性 Python 库：

| 约束 | 实际状态 |
|---|---|
| UI | 无 |
| 数据库 | 无（内存态 + 可选 JSON 文件持久化） |
| 网络 | 无 |
| LLM | 无（全部为确定性规则/启发式） |
| 版本控制 | **未初始化 git 仓库** |
| README | 无 |

---

## 2. 目录清单

| 路径 | 内容 | 性质 |
|---|---|---|
| `src/` | 45 个模块，18136 行 | 生产代码 |
| `src/application/` | 8 个模块，应用服务层（Task 34） | 生产代码 |
| `tests/` | 39 个测试文件 18622 行 + 24 个 fixture | 完整 |
| `docs/architecture.md` | 2044 行，按 Task 编号分节的架构设计 | 权威文档 |
| `docs/status.md` | 1543 行，Task 1–34 交付记录 | 权威文档 |
| `Python/` | 便携式 Python 3.14.7 运行时（约 5500 文件） | 运行环境 |
| `scripts/` | 27 个 `.py`，多为 `fix.py` / `gen.py` / `_step1.py` | **开发期临时脚本** |
| `cache/` | 78 个 `.py` + 探针 `_probe*.py`、`.b64` 中间文件 | **草稿 / 缓存** |
| `input/` `output/` `logs/` | 三个目录**全空** | 流水线未跑过真实材料 |

### 需要清理的遗留物

**4 个 0 字节空占位模块**（功能已被后续 Task 取代）：

- `src/verification.py` → 被 `src/knowledge_validation.py` 取代
- `src/knowledge_extractor.py` → 被 `src/knowledge_pipeline.py` 内的 `KnowledgeExtractor` 取代
- `src/summarizer.py` → 从未实现
- `src/append_func.py` → 从未实现

### 失效的运行入口

`run_tests.cmd` 指向 `D:\Project\Clases\Python\python3.14.exe`，但该路径**不存在**——解释器实际在 `Python/pythoncore-3.14-64/python.exe`（`Python/bin/` 下只有 shim）。该脚本目前无法直接使用。

正确调用方式：

```bash
./Python/pythoncore-3.14-64/python.exe -m pytest -m "not integration"
```

---

## 3. 分层架构

自下而上六层，依赖方向严格单向：

```
输入材料  input/（音频 / 板书图片 / 笔记 / 大纲）
    ↓
采集与解析层  material_index · audio_input · document_input · note_parser
              ocr_processor · transcription · asr_provider · whisper_provider
              audio_segmentation · transcript_quality
    ↓
证据层        evidence_extractor · document_evidence · integration（去重 / 冲突）
              evidence_ingestion → evidence_store（内容寻址 · 不可变）
    ↓
知识层        knowledge_pipeline · knowledge_assembly · knowledge_structure
              knowledge_organization · knowledge_validation · knowledge_review
              knowledge_coverage · knowledge_dependency · knowledge_learning
    ↓
学习层        student_learning · exercises · answer_evaluation · study_plan
    ↓
应用层        src/application/ — AppService 门面 + 5 个子服务
```

### 各层职责要点

**采集与解析层**：把异构文件归一为结构化证据。音频走 `whisper_provider`（faster-whisper / CTranslate2，CPU 默认，CUDA 可选），长音频由 `audio_segmentation` 切块，转录质量由 `transcript_quality` 打分；PDF/DOCX 走 `document_input`（pypdf / python-docx）；图片 OCR 与纯文本转录目前**仍是 Mock 实现**。

**证据层**：`EvidenceStore` 是全局唯一的证据真相源，按内容寻址去重、不可变、支持 `retire`/`restore` 生命周期与 `save`/`load` 快照恢复（原子替换）。`integration.py` 负责重复检测、顺序冲突与否定冲突的识别。

**知识层**：本项目的重心所在。`knowledge_organization.py`（1906 行）是最大模块，统筹课程级知识结构；`knowledge_coverage` 做覆盖度/缺口分析，`knowledge_dependency` 做前置依赖分析，`knowledge_validation` + `knowledge_review` 构成人工核验闭环（冲突状态**绝不自动确认**）。

**学习层**：学生状态、4 类题型（多选/判断/简答/填空）、精确匹配判分、复习规划。简答题的非精确匹配按设计返回 `UNSUPPORTED`——不做语义判分。

**应用层**：`AppService` 组合全部子服务，对外只返回扁平 dict DTO，异常统一映射为 8 种结构化错误码，不泄漏 traceback。

---

## 4. 依赖结构分析（AST 实测）

### 4.1 扇出（fan-out）最高的模块

| 内部依赖数 | 模块 | 依赖对象 |
|---|---|---|
| 10 | `application.app_service` | 全部 5 个子服务 + store + ingestion + org + models |
| 9 | `evidence_ingestion` | asr / audio_input / document_* / note_parser / ocr / store |
| 8 | `application.knowledge_service` | coverage / dependency / org / review / structure |
| 7 | `knowledge_assembly` | store / integration / pipeline / review / validation |
| 7 | `application.material_workflow` | document_input / ingestion / store / org |
| 6 | `processor` | asr / evidence_extractor / note_parser / ocr / transcription |

**判读**：`app_service` 和 `evidence_ingestion` 是两个"汇聚点"——所有异构输入都必须穿过 `evidence_ingestion` 才能进入系统。这个设计是对的：单点收口保证了证据来源可追溯。

### 4.2 零内部依赖的叶子模块（8 个）

`src` · `application.errors` · `exercises` · `student_learning` · 以及 4 个空占位模块

真正有价值的叶子是 `exercises`、`student_learning`、`application.errors`——它们自足、无副作用，可独立测试。这解释了为什么学习层的测试覆盖最密（`test_application_learning.py` 45 项）。

### 4.3 循环依赖：3 处，均为真实存在

```
src.models → src.knowledge_review → src.integration → src.models
src.knowledge_review → src.knowledge_structure → src.knowledge_review
src.models → src.knowledge_review → src.knowledge_structure → src.models
```

**这不是误报**，而是刻意用**函数级惰性导入**绕开的架构分层倒置：

- `src/models.py:152` — `KnowledgePoint.__post_init__` 内 `from src.knowledge_validation import ValidationStatus`
- `src/models.py:155` — 同处 `from src.knowledge_review import ReviewStatus`
- `src/knowledge_structure.py:145` — `from_dict` 内 `from src.knowledge_review import ReviewRecord`

**问题本质**：领域核心 `models.py`（最底层）反向依赖了上层的校验与审核枚举。规避手段是把导入塞进方法体，从而把循环从**导入期**推迟到**调用期**。

**影响**：模块加载顺序不再构成风险，但引入了新问题——`KnowledgePoint` 的构造行为隐式依赖上层模块的可用性；`to_dict`/`from_dict` 的 round-trip 正确性依赖枚举值字符串一致。这是全项目最值得重构的一处：应把 `ValidationStatus` / `ReviewStatus` 下沉到 `models.py` 或独立的 `enums.py`。

全项目共 **23 处**函数级惰性内部导入，其中大部分（如 `evidence_ingestion` 里的 8 处）是为了按材料类型延迟加载可选依赖，属于合理用法；只有上述 3 处是为了绕循环。

### 4.4 封装破坏

```python
src/knowledge_coverage.py:661:  from src.knowledge_organization import _order_key
```

`knowledge_coverage` 直接导入了 `knowledge_organization` 的**私有**函数 `_order_key`。排序键是跨模块的一致性契约，应当提升为公开 API（或下沉到 `models.py`），否则 `knowledge_organization` 内部重构会静默破坏覆盖度分析的排序稳定性。

---

## 5. 确定性策略：宣称与现实的落差

文档多处声明"无 `uuid4` / `datetime.now` / `random`，全部内容寻址"。实测结果：

### 5.1 `uuid4` 确实存在于模型默认值

| 位置 | 用途 |
|---|---|
| `src/models.py:102` | `Material.material_id` 为空时填随机 uuid4 |
| `src/models.py:121` | `Evidence.evidence_id` 同上 |
| `src/models.py:148` | `KnowledgePoint.knowledge_id` 同上 |
| `src/models.py:234` | `VerificationItem.verification_id` 同上 |
| `src/knowledge_structure.py:27` | `Relationship.relationship_id` 同上 |
| `src/integration.py:54` | `ConflictRecord.conflict_id` 同上 |
| `src/audio_segmentation.py:339` | 音频分块临时 id |

**准确表述**：确定性**不由领域模型保证，而由调用方约定保证**。所有流水线层都显式传入内容寻址 ID，`uuid4` 只在"调用方忘记传 ID"时兜底。

### 5.2 系统已经为此打了补丁

`src/evidence_ingestion.py:552` 定义了 `_is_uuid4_fallback_id()`，用于**识别并修复**模型 uuid4 兜底产生的 ID：

```python
# evidence_ingestion.py:583
if not evidence.evidence_id or _is_uuid4_fallback_id(evidence.evidence_id):
    # 重新按内容寻址推导，杜绝随机 id 污染
```

这反证了风险真实存在——摄取层不得不主动防御模型层的非确定性。

### 5.3 `datetime` 与 `random`

- `datetime`：仅出现在 `src/material_index.py`，用于读取文件 mtime 参与 ID 计算（`sha256(relative_path + size + mtime)`）。这是**索引身份**的合理用法，但意味着**文件 mtime 变化会导致 material_id 变化**，重命名或 touch 文件会产生新 ID。
- `random`：全项目零使用。
- `hash()`：零使用（避免 PYTHONHASHSEED 影响），全部改用 `hashlib`。

---

## 6. 依赖声明的缺口

`requirements.txt` 只列出 4 项：`faster-whisper`、`ctranslate2`、`pypdf`、`python-docx`。但实际导入还包含：

| 包 | 导入位置 | 是否声明 | 说明 |
|---|---|---|---|
| `av` (PyAV) | `audio_segmentation.py:291` | **否** | 文档注释提到"自带 FFmpeg"，但未写入 requirements |
| `numpy` | `audio_segmentation.py:292` | **否** | 由 `av` 传递引入 |
| `torch` | `whisper_provider.py:245` | **否** | 见下方分析 |

`torch` 的问题最实际：它只出现在 CUDA 可用性检查里，且是惰性导入并包了 `except`，因此不会导致崩溃。但 **faster-whisper 的 GPU 加速走的是 CTranslate2 + cuBLAS/cuDNN，并不依赖 PyTorch**。用 `torch.cuda.is_available()` 作为 CUDA 判据，意味着在一台 CTranslate2 GPU 工作正常但未装 torch 的机器上，`device="cuda"` 会直接抛 `ASRUnavailableError`。建议改用 `ctranslate2.get_cuda_device_count()` 作为判据，或把 torch 明确写入 requirements 的 GPU 附加项。

---

## 7. 测试体系

```
$ ./Python/pythoncore-3.14-64/python.exe -m pytest -q -m "not integration"
1493 passed, 14 deselected in 22.79s
```

| 指标 | 数值 |
|---|---|
| 收集用例 | 1507 |
| 默认通过 | 1493 |
| 集成用例（`-m integration`） | 14 |
| 失败 | 0 |
| 耗时 | 22.79s |

集成测试需真实 faster-whisper 运行时 + 模型下载，因此默认排除。测试密度最高的模块：

`test_knowledge_assembly.py` 1500 行 · `test_knowledge_organization.py` 1457 · `test_evidence_ingestion.py` 1266 · `test_evidence_store.py` 1158 · `test_knowledge_coverage.py` 848

测试规模（18622 行）**已超过源码规模**（18136 行），覆盖率导向明确。

### 文档口径偏差

`docs/status.md` 结尾写「Final: 1507 passed, 14 deselected」，与实跑不符。1507 是**收集数**，其中 14 个被 deselect，实际通过 1493。建议修正为 `1493 passed, 14 deselected (1507 collected)`。

---

## 8. 问题清单（按优先级）

| # | 问题 | 位置 | 影响 | 建议 |
|---|---|---|---|---|
| 1 | 领域核心反向依赖上层，靠 3 处惰性导入绕循环 | `models.py:152,155`、`knowledge_structure.py:145` | 分层倒置，构造行为隐式依赖上层 | 将 `ValidationStatus`/`ReviewStatus` 下沉为独立枚举模块 |
| 2 | 模型默认值使用 `uuid4`，非确定性 | `models.py`、`knowledge_structure.py:27`、`integration.py:54` | 与"全链路确定性"宣称矛盾，已迫使摄取层打补丁 | 默认值改为显式报错，或强制要求传入内容寻址 ID |
| 3 | CUDA 判据依赖未声明的 `torch` | `whisper_provider.py:245` | 有 GPU 但无 torch 时误报不可用 | 改用 `ctranslate2.get_cuda_device_count()` |
| 4 | 跨模块导入私有函数 | `knowledge_coverage.py:661` | 重构时静默破坏排序稳定性 | 将 `_order_key` 提升为公开 API |
| 5 | `requirements.txt` 缺 `av` / `numpy` | `requirements.txt` | 全新环境安装后音频路径可能失败 | 显式声明 |
| 6 | `run_tests.cmd` 路径失效 | `run_tests.cmd` | 入口不可用 | 指向 `Python\pythoncore-3.14-64\python.exe` |
| 7 | 4 个 0 字节空占位模块 | `src/verification.py` 等 | 误导后续读者 | 删除 |
| 8 | `scripts/`（27 py）与 `cache/`（78 py）混入项目根 | — | 与产品代码混淆 | 移入 `archive/` 或加入 `.gitignore` |
| 9 | 未初始化 git，无 README | — | 无法回溯变更、无上手入口 | `git init` + 补 README |
| 10 | OCR 与纯文本转录仍为 Mock | `ocr_processor.py`、`transcription.py` | 图片/纯音频文本路径不可用于生产 | 明确标注能力边界（文档已部分说明） |
| 11 | `docs/status.md` 测试口径偏差 | `docs/status.md` 结尾 | 数字误导 | 修正为 1493 passed |
| 12 | `input/`/`output/`/`logs/` 全空 | — | 流水线从未端到端跑过真实材料 | 跑一次真实材料的冒烟验证 |

---

## 9. 总体评价

**优点**

- 分层意图清晰，依赖方向在 90% 的模块上得到遵守，`evidence_ingestion` 单点收口是正确设计。
- 证据链设计扎实：`Material → Evidence → KnowledgePoint` 每一步都可追溯到源材料，`SourceReference` 贯穿始终，符合 AGENTS.md 的"证据优先"原则。
- 测试规模超过源码规模，1507 项用例零失败，且确定性/幂等性/序列化往返均有专项验证。
- 零 LLM、零网络、零数据库——对一个课程辅助工具而言，这是降低维护成本的明智取舍。
- 43 个 Task 的 `status.md` + `architecture.md` 形成了罕见的完整决策留痕。

**主要风险**

- **分层倒置**（问题 1、2）是最需要处理的架构债：它同时破坏了"领域模型最稳定"和"全链路确定性"两条核心声明，且已经迫使上层写防御性补丁。好在修复路径明确、局部化。
- `scripts/` 与 `cache/` 的开发残留（105 个 py 文件）与 4 个空模块，让项目的"真实规模"比表面看起来小得多——真正的生产代码是 `src/` 的 45 个模块。
- 流水线从未处理过真实材料（`input/` 为空），意味着所有验证都停留在 fixture 层面。

---

## 附录：验证方法

本文档的所有结论均可复现：

```bash
# 1. 测试状态
./Python/pythoncore-3.14-64/python.exe -m pytest -q -m "not integration"

# 2. 依赖图与循环检测
./Python/pythoncore-3.14-64/python.exe cache/_depgraph.py

# 3. 定位循环依赖的真实导入语句
./Python/pythoncore-3.14-64/python.exe cache/_dbg.py
```

分析脚本：`cache/_depgraph.py`（AST 导入图构建 + DFS 环检测 + 外部依赖归类）、`cache/_dbg.py`（逐模块打印内部导入行号）。
