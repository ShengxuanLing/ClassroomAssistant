# Persistence Wiring Hardening — Task 48–55 Final Report

生成时间：2026-09-17
适用代码版本：`Classroom Assistant 1.0.1`（`src/application/workspace.py::APPLICATION_VERSION`）
数据库 schema 版本：2（`src/persistence/migrations/`，迁移链 `m001` + `m002`）
本报告覆盖范围：Task 48 – 55（业务持久化真正接线 / 重启恢复 / Production Ready 收口）

---

## Overall Status

**PASS** —— 但本报告**不是一份全是 PASS 的报告**：未能真实验证的项目集中记录在
[BLOCKED / NOT VERIFIED](#blocked--not-verified) 一节，逐条写明"没验什么、为什么、谁能验"。

Task 34–47 的最终判定是 **Beta Ready**，唯一决定性理由写在当时 `docs/final_report.md`
的 Known Limitations 第 1 条：

> 运行时 `data_dir` 里只有材料文件与材料注册表 JSON，**没有 `classroom.sqlite`**；
> Course / ClassSession / Evidence / KnowledgePoint / ReviewRecord / Student / Answer /
> Evaluation / StudyPlan / LearningPath **全部只活在内存里**。用户建了课程、传了材料、
> 审了知识点，**关掉应用就全没了**。

Task 48–55 就是把这条限制消掉。数据流从

```
创建课程 -> 内存 -> 关闭程序 -> 数据消失
```

改成

```
创建课程 -> Application Service -> Repository -> SQLite -> 关闭 -> 重启 -> 恢复
```

并且这句话不是靠"我们觉得改好了"来支撑的，而是靠**真子进程**（`terminate` /
`os._exit(9)` / 干净退出三种结束方式）+ **失败注入**（13 个命名落盘点）+ **变异测试**
（临时拆掉接线，看测试是否真的变红）来支撑。

---

## 那个唯一重要的问题

spec 的最终验收标准只有一句：

> 如果用户今天真的用这个软件上了一整门课，然后关闭电脑，明天重新打开，所有数据还在不在？

回答：**在**。证据是一条完整的真子进程验收（`tests/test_production_gate.py::gate` 夹具）：

| 步骤 | 进程 | PID | 结果 |
| --- | --- | --- | --- |
| 建全部业务对象（AcceptanceHarness 17 步） | 进程 A | 11116 | `all_steps_ok = True`，`all_answered = True` |
| 全新进程，只给 `data_dir`，不给任何 ID | 进程 B | 12580 | 全部对象按内容寻址 ID 逐字段还原 |

进程 B 与进程 A 的 PID 不同，且与测试进程本身的 PID 也不同 —— 这条断言本身也是测试
（`test_the_observation_ran_in_a_different_process`），因为"同进程重启"在这个项目里
没有对应物：关掉再开一个 `Workspace` 仍然共享解释器、内存与文件句柄表。

重启后逐项核对的数字（实测，非估算）：

| 项 | 重启前 | 重启后 | 判据 |
| --- | --- | --- | --- |
| `courses` | 1 | 1 | ID 相同 |
| `sessions` | 1 | 1 | ID 相同 |
| `materials` | 6 | 6 | 且 6 个受管文件全部 `isfile` |
| `material_processing` | 6 | 6 | 处理状态行 |
| `evidence` | 91 | 91 | 逐 `material_id` 的 `evidence_id` 集合一致 |
| `knowledge_points` | 90 | 90 | 集合与顺序都一致 |
| `review_records` | 90 | 90 | 每条的 `[review_id, decision, note, selected_evidence_ids]` 一致 |
| `conflicts` | 1 | 1 | `conflict-31da6a531ef8e7c8` |
| `topics` / `knowledge_memberships` / `knowledge_relations` | 1 / 2 / 1 | 1 / 2 / 1 | 主题标题与关系端点逐字段核对 |
| `session_memberships` | 87 | 87 | |
| `students` / `student_knowledge_state` | 1 / 1 | 1 / 1 | 学习状态 dict 完全一致 |
| `exercises` | 1 | 1 | |
| `student_answers` | 1 | 1 | 答案 ID 由进程 B **从库里重新发现**（不给它） |
| `evaluation_results` | 1 | 1 | 评估视图非空 |
| `study_plans` | 3 | 3 | 快照行 append-only：重启**不**产生新快照 |
| `learning_paths` | 1 | 1 | 存储层审计行 |

健康状态：`integrity_check() == "ok"`、`foreign_key_violations() == []`、
`PRAGMA journal_mode == "wal"`、`health()["status"] == "ok"`、`schema_version == 2`、
`rollbacks == 0`、`material_integrity()` 校验 6 份材料 `missing=0 / hash_mismatch=0 /
path_unknown=0`。

---

## Tasks

`净增` 是本阶段各 Task 完成时新增的测试条数；本阶段共新增 **582 条**，**无一条被删除、
跳过或改写语义**。

| Task | Status | 净增 | 测试文件 | 说明 |
| --- | --- | --- | --- | --- |
| 48 | PASS | 29 | `test_persistence_workspace.py` | Persistent Workspace Bootstrap（open → migrate → load；绝不静默建空库） |
| 49 | PASS | 59 | `test_persistence_courses.py` | Course / Session / Material 写穿与可移植性 |
| 50 | PASS | 96 | `test_persistence_knowledge.py` | Evidence / Knowledge（含显式 `insertion_seq`） |
| 51 | PASS | 102 | `test_persistence_learning.py` | Review / Student / Exercise（真值安全 + 幂等） |
| 52 | PASS | 60 | `test_persistence_plans.py` | StudyPlan / LearningPath / Coverage（stored vs derived） |
| 53 | PASS | 71 | `test_persistence_transactions.py` | Transactional Application Operations（一次业务操作 = 一个事务） |
| 54 | PASS | 90 | `test_restart_recovery.py` | True Restart / Crash / Recovery Acceptance（**全部真子进程**） |
| 55 | PASS | 75 | `test_production_gate.py` | Production Ready Final Gate（本文件 A–J 十道门） |

各 Task 的测试配额：48 ≥20 / 49 ≥40 / 50 ≥70 / 51 ≥70 / 52 ≥40 / 53 ≥50 / 54 ≥35
（必须真 subprocess）。**全部达标，其中 54 是 90 条且 90 条全部真子进程。**

---

## A. Persistence Restart Matrix（13 行）

矩阵不是"表清单"，而是按**落盘步骤**组织，与
`src/application/persistence_wiring.py::FAULT_POINTS` **一一对应且顺序全等**
（`test_the_matrix_matches_the_named_fault_points` 直接断言元组相等）。
这样 Task 53 的"注入点覆盖"与 Task 55 的"重启覆盖"说的是同一组东西 ——
一个点如果在注入点里存在，它就必须在这里也有对应的一行。

| # | 落盘点 | 落盘对象 | 类型 | 重启后判据 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 1 | `course` | `Course` | stored | ID 集合相等 | PASS |
| 2 | `session` | `ClassSession` | stored | ID 集合相等 | PASS |
| 3 | `material` | `Material` + 受管文件 | stored | 记录存在 **且** 文件 `isfile` **且** 无孤儿 | PASS |
| 4 | `evidence` | `Evidence`（+ 材料关联） | stored | 逐材料的 `evidence_id` 集合相等（91 条） | PASS |
| 5 | `knowledge_structure` | `KnowledgePoint` | stored | 90 个 ID 集合与顺序相等 | PASS |
| 6 | `review` | `ReviewRecord` | stored | 90 条 append-only 历史逐条相等 | PASS |
| 7 | `organization` | `Topic` / `Membership` / `Relation` | stored | 主题标题 + 关系端点逐字段核对 | PASS |
| 8 | `student_log` | `Student` + `StudentState` + `LearningEvent` | stored | 状态 dict 完全一致 | PASS |
| 9 | `exercise` | `Exercise` | stored | ID 集合相等 | PASS |
| 10 | `answer` | `StudentAnswer` | stored | **从库里重新发现**的 ID 里能找到 | PASS |
| 11 | `evaluation` | `EvaluationResult` | stored | 评估视图非空 | PASS |
| 12 | `study_plan` | `StudyPlan` **快照行** | stored | 计划 ID 相等，且快照行数**不增** | PASS |
| 13 | `learning_path` | `LearningPath` | **derived** | 值重算出完全一样的结果 | PASS |

三行需要单独说明：

- **第 3 行**是唯一"记录 + 文件"两半的对象。`copytree` 出来的副本里注册表还写着源目录的
  `stored_path` —— 这是 Task 48–52 修过的真实缺陷 #22 的修复行为（`data_dir` 搬迁后
  `stored_path` 会被改写）。因此幂等性测试用**专用目录**夹具，而不是复制目录。
- **第 12 行**区分了"计划的值"与"计划的快照行"：值是 derived，快照行是 stored。
  `study_plan()` 调用本身会**追加**一条快照行（值变了才追加），所以取 `counts()` 的时机
  必须放在所有派生调用**之后** —— 否则"重启不产生新快照"这条断言会永远为真。
- **第 13 行**是唯一的 `derived` 行，有专门的守卫
  （`test_the_matrix_documents_its_own_derived_rows`）断言"派生行集合 == `['learning_path']`"，
  防止有人把漏写的 stored 当成 derived 混过去；同时要求同步 `docs/architecture.md` 21.2。

---

## B. 50 个知识点的 traceability 审计（跨重启）

Task 47.9 的 traceability 审计已经在 90 个知识点的数据集上做过 —— 但它是**同进程**的。
重启之后 provenance 是否还完整是另一件事：溯源链要活下来，必须由**磁盘上的行**重建出来。

规模：验收数据集原始夹具只有 6 份材料、产出 **15** 个知识点，达不到 spec 的抽样下限。
做法是**只放大输入材料**（把板书 OCR 从 5 段扩成 80 段，`BOARD_SEGMENTS = 80`，与
`test_hardening_traceability.py` 同一口径），**不碰任何产品代码**。结果：**90** 个知识点、
**91** 条证据。

| 审计项 | 断言 | 实测 |
| --- | --- | --- |
| 抽样下限 | `>= 50` | 90 |
| 每个知识点都有审核决策痕迹 | 全量，非抽样 | 90 / 90 有 `review_records` |
| 每个知识点的 `evidence_refs` 可解析 | 材料侧关联表交叉验证 | 91 条证据全部可解析 |
| 每条证据挂在一份已登记材料上 | 无悬空证据 | 0 条悬空 |
| 每个知识点重启后仍能取到学习路径 | 全量 | 90 / 90 |
| `KP → Evidence → Material → 源位置` 四级链 | 源位置字段非空 | PASS（`test_traceability_survives_a_real_restart`） |

**traceability 的关键判据不是"对象还在"，而是"重启后仍然能走通同一条证据链"**：
`tests/test_hardening_traceability.py::test_traceability_survives_a_real_restart` 在重启前
逐条快照 `知识点 → evidence_id`（顺序敏感），重启后逐条比对，并要求每条证据的
`source.material_id` 仍在材料注册表里、`source` 仍有 location / page / line /
paragraph / timestamp 之一。

同文件里 `test_reload_limitation_is_pinned_not_silent` 按 spec 55.14 的要求被**改写**成
反向守卫（不是删除）：它断言的是"重启后知识消失"这个旧结论**已经并且必须保持被推翻**。
如果哪天有人把接线拆掉，这里会立刻变红，而不是让文档里的一句话静静过期。
同理，`tests/test_hardening_backup_drill.py::TestDrillGap` 的类名被保留（它是"曾经有缺口"
的历史记录），但里面 6 条断言已经**反过来**：从前断言"恢复不回来"，现在断言"必须恢复回来"，
并且**先证明前提成立**（`test_the_working_database_really_was_destroyed`：演练确实删掉了
所有 `*.sqlite*`，否则"恢复成功"可能只是因为原始数据一直还在）。

---

## C. Truth Safety（重启与学习状态都不许改写知识真值）

| 不变量 | 断言 | 实测 |
| --- | --- | --- |
| `validation_status` 跨重启逐点一致 | 90 个点全等 | PASS |
| `CONFLICTED` 绝不因重启变 `SUPPORTED`/`CONFIRMED` | 2 个 conflicted 点重启后仍是 conflicted | PASS |
| 冲突记录本身存活 | `conflict-31da6a531ef8e7c8` | PASS |
| 审核历史 append-only | 重启后**每条**逐字段相等 | PASS |
| 学生状态不污染知识真值 | 知识状态与学生状态相互独立 | PASS |
| 冲突的解决留下"人选了哪一侧" | `selected_evidence_ids` 非空且 ⊆ 冲突的 `evidence_refs` | PASS |
| 派生视图重算而非读回 | `coverage` / `gaps` 重启前后全等 | PASS |

`validation_status` 分布（实测）：`supported 88 + conflicted 2 = 90`。

**一个重要的领域事实（踩过才知道）**：`CONFLICTED` 在 **`validation_status`** 轴上，
不在 `review_status` 轴上 —— 验收之后 `review_status` 是 `confirmed`，而证据真值轴
仍然是 `conflicted`。早期版本拿 `review_status` 去断言"冲突没被静默确认"，
结果是空转（全是 `confirmed`）。同理，领域层把 `resolve_conflict` 建模成一条
**带证据选择的确认**（`decision == "confirm"` + 非空 `selected_evidence_ids`），
**没有**独立的 `resolve_conflict` 决策值 —— 所以"显式解决"的判据是那个选择本身。

---

## D. Determinism（同输入 → 同 ID，跨进程成立）

| 审计项 | 做法 | 结果 |
| --- | --- | --- |
| 两次独立构建产出相同 ID | 两个真子进程各建一次，逐字段比对 | `course_id` / `session_id` / `topic_id` / `study_plan_id` / `learning_path` / 90 个 `knowledge_ids` 全部相同 |
| ID 是内容寻址而非随机 | 前缀断言 | `course-` / `session-` / `kp-` / `exercise-` / `answer-` / `topic-` / `plan-` / `conflict-` |
| 同一数据库被两个进程读 | 两次观察（去掉 PID）递归相等 | PASS |
| AST 审计覆盖持久化层 | Task 47.2 的审计扫到 `src/persistence` | PASS |

ID 实例：`course-ef88d13aa26cff38` / `session-1e4cf95457e382e9` /
`topic-ddc7f4237c0d105c1ea89d1c` / `plan-d5037dc229a3df1540481ec1` /
`conflict-31da6a531ef8e7c8` / `answer-2264af168eef8a4a519e755f`。
学生 ID 用的是业务自然键 `student-uab-2026-001`（不是哈希）。

唯一允许的非确定性入口仍然是可注入的 `Clock`（`src/application/runtime.py`），
验收全程用 `fixed_clock("2026-09-15T18:00:00+00:00")`。`uuid4` / `datetime.now` /
`random` / 内置 `hash()` 由 AST 审计禁止。

---

## E. Idempotency（1x / 2x / 3x + 跨进程）

在**副本**上（不污染共享夹具）由真子进程把同一批**内容寻址**操作重放 3 轮，
每轮之后报告 `counts()`：

| 判据 | 断言 | 结果 |
| --- | --- | --- |
| 三轮计数完全相同 | `rounds[0] == rounds[1] == rounds[2]` | PASS |
| 第 1 轮本身也不增长 | `rounds[0]` 非空 | PASS |
| 重放在自己的进程里 | `report["pid"] != os.getpid()` | PASS |
| 内容寻址表稳定 | `courses` / `sessions` / `materials` / `evidence` / `knowledge_points` / `students` / `review_records` 各自只有一个取值 | PASS |
| 重放后库仍健康 | `integrity_check == "ok"` | PASS |

判据是"1x 之后与 2x / 3x 之后完全一样"，而不是"没抛异常"。

**跨进程幂等的边界（如实记录）**：Task 54 的 `_REGISTER_AGAIN` 测试证明
"在新进程里重新登记同一批**原始数据集文件**是幂等的"。但重新登记**应用托管的副本**
（文件名已被改成 `mat-<id>.<ext>`）会按 `(course, filename, content_hash)` 得到**新**材料 ——
那是既有身份定义的后果（身份包含文件名），不是缺陷。测试用的是前者，因为那才是
"用户再次拖入同一个文件"的真实场景。

---

## F. Dependency Audit

禁止清单（键是人读的名字，值是真正会被 import 的模块名）：

```
Redis       redis
PostgreSQL  psycopg / psycopg2 / asyncpg / pg8000
MySQL       pymysql / mysql.connector / mysqlclient / MySQLdb
MongoDB     pymongo / motor / mongodb
Kafka       kafka / confluent_kafka / aiokafka
Celery      celery
React       react / react-dom
Vite        vite
Docker      docker
```

同时断言没有 ORM / 迁移框架混入（`sqlalchemy` / `alembic` / `peewee` / `tortoise` /
`databases`），没有**裸版本**依赖（每项都有上下界），没有测试专用依赖混进
`requirements.txt`，没有未使用依赖（每个运行时依赖都在 `src` 里真实被 import）。

运行时依赖仍然是 **7 个本地库**：`faster-whisper` / `ctranslate2` / `pypdf` /
`python-docx` / `av` / `numpy` / `rapidocr-onnxruntime==1.2.3`。
无云服务、无数据库服务、无消息队列、无容器。

**存储引擎是 Python 标准库自带的 `sqlite3`** —— 这不是"又一个依赖"，它是
"本地优先、离线可用、单文件可备份"这个产品定位的直接选择。分层守卫
（`tests/test_persistence_layering.py`）把 `import sqlite3` 限制在
`src/persistence/` 内部；应用层里只有组合根 `bootstrap.py` 与
`persistence_wiring.py` 两个文件允许 `import src.persistence`，白名单**逐文件枚举并全等断言**，
新增业务服务一个都不许碰存储层。

---

## G. Scale and Footprint

spec 点名的生产规模：**1000 知识点 / 100 主题 / 50 课堂 / 500 学生 / 5000 练习 / 20000 作答**。
按这个规模通过**持久化层**建一次库（`tests/test_production_gate.py::scale` 夹具），实测：

| 指标 | 上界 | 实测 | 说明 |
| --- | --- | --- | --- |
| 落库耗时 | 120 s | **1.512 s** | 7 张表一次事务批量写入（含 20000 条答案） |
| **db size（数据库大小）** | —— | **12,259,328 字节 ≈ 11,972 KiB ≈ 11.7 MiB** | `PRAGMA wal_checkpoint(TRUNCATE)` 之后量主库文件 |
| 总行数 | —— | 27,601 | 21 张表计数之和 |
| 每行平均字节 | < 4096 | **444.2** | 线性足迹；膨胀意味着重复存储 |
| 1000 次单点知识点查询 | < 1.0 s | **0.0435 s**（约 43.5 µs / 次） | 唯一按"恒定时间"而非宽松上界断言的一项 |
| 单点查询计划 | 必须 `SEARCH`，不许 `SCAN` | `SEARCH knowledge_points USING COVERING INDEX sqlite_autoindex_knowledge_points_1 (knowledge_id=?)` | 索引退化会立刻红 |

`database size` 的判据是**"足迹与行数成线性关系"**，不是绝对数字 ——
硬编码 MiB 数在不同机器/页大小上必然抖动，而重复存储会突破任何宽松上界。

各表行数（实测）：`knowledge_points 1000` / `knowledge_relations 950` /
`topics 100` / `sessions 50` / `students 500` / `exercises 5000` /
`student_answers 20000` / `courses 1`。

领域层的热路径性能由 `tests/test_performance.py`（Task 47.4，12 条）覆盖，
本门不重复计分（见 BLOCKED / NOT VERIFIED 第 2 条）。

---

## H. File / Database Consistency

材料是唯一"记录 + 文件"两半的对象，两半必须同时活着，而且不许有孤儿记录。

| 审计项 | 断言 | 实测 |
| --- | --- | --- |
| 每条材料记录都有文件 | 6 / 6 `isfile` | PASS |
| 没有孤儿材料记录 | `material_integrity()["findings"] == []` | PASS |
| 计数一致 | 文件数 == 记录数 | 6 == 6 |
| 健康报告与完整性报告一致 | `health()["status"] == "ok"` 且 `database.ok is True` | PASS |

`material_integrity()` 明细：`checked=6 / missing=0 / hash_mismatch=0 / path_unknown=0`。

**一个已修的真实缺陷（#22）**：`data_dir` 搬迁之后，注册表里的 `stored_path` 仍指着源目录。
这条缺陷是被"重启后文件对不上"的测试顶出来的 —— 修复行为是"按当前 `data_dir` 重新推导
并改写 `stored_path`"，代价是"复制目录后的首次重新登记会改写注册表"，因此幂等性测试
必须用专用目录而不是复制目录。这条代价写在了 Task 54 的 status 记录里，不是悄悄绕过去。

---

## I. Build Hygiene

| 审计项 | 判据 | 结果 |
| --- | --- | --- |
| `compileall` | `python -m compileall -q src tests` 退出码 0 | PASS |
| 包可 import 且无副作用 | 子进程 `import src.application.workspace` 并打印版本 | `IMPORT-OK 0.37.0` |
| 仓库根无游离脚本 | 除 `setup.py` / `conftest.py` 外无 `*.py` | PASS |

---

## J. Version and Documentation

版本号从 **0.35.0 升到 0.37.0**，四处一致且由测试钉住：

| 位置 | 值 |
| --- | --- |
| `src/application/workspace.py::APPLICATION_VERSION` | `0.37.0` |
| `src/__init__.py::__version__` | `0.37.0` |
| `tests/__init__.py::__version__` | `0.37.0` |
| `docs/final_report.md` | `0.37.0` |
| `/api/health` 的 `version` 字段（重启后实测） | `0.37.0` |

架构决策已落到 `docs/architecture.md`：21.2 Stored vs Derived（`StudyPlan` 拆两行）、
21.7 One business operation, one transaction、**21.8 Restart, crash and recovery**
（三种结束方式、8 KiB 页缓存、`-wal` 非空判据、`copytree` 不是同一个目录、固定时钟前提、
"重启不许改什么"与"崩溃不许留下什么"）。

真实引擎集成套件**存在且被真的执行**（不是被 skip 掉）：
`test_whisper_provider.py` / `test_audio_segmentation.py` / `test_ocr_provider.py` /
`test_ocr_processor.py` / `test_document_input.py` / `test_integration_pipeline.py`
共 **310 passed**（实测，54.28 s，0 skipped）。

---

## Test Summary

```
本阶段新增测试                        582 条
  48–52 persistence wiring            346
  53  事务化业务操作                    71
  54  真重启 / 崩溃 / 恢复              90   (全部真子进程)
  55  生产就绪终审                      75

Task 55 门禁（本文件 A–J）             75 passed
真实引擎集成套件                      310 passed / 0 skipped   (54.28s)
compileall                            exit 0
```

全量回归数字见下方"最终回归"一节（本报告定稿时重跑）。

**变异测试（证明 Task 54 的 90 条不是空转）**：把 `workspace._restore_registries()`
里加载课程与课堂的两行临时改成返回空列表，重跑 `tests/test_restart_recovery.py`
→ **42 failed / 30 passed / 18 errors**，即 90 条里 **60 条变红**。改回源码后重新全绿。
这就是"重启后数据还在"这句话的证据强度。

---

## 本阶段发现并修复的真实产品缺陷

不是"写测试时顺手改的格式问题"，每一条都是先复现、再定位、再修复、再加回归测试：

| # | 缺陷 | 症状 | 修复 |
| --- | --- | --- | --- |
| 20 | `exercise_evidence` 外键 | 按错误顺序写表导致外键拒绝提交 | `_flush_processing` 固定为 证据 → 材料 → 知识 → 组织 → 学习 |
| 21 | 判断题身份与展示顺序冲突 | 同一道判断题因选项顺序不同被算成两道 | 身份只由内容决定 |
| 22 | `data_dir` 搬迁后 `stored_path` 陈旧 | 复制目录后文件找不到 | 按当前 `data_dir` 重新推导并改写 |
| 23 | `list_sessions` 排序不一致 | 同一课程两次列出顺序不同 | 稳定排序键 |
| 24 | `submit_answer` 重复计数膨胀 | 重复提交同一答案导致统计虚高 | 幂等提交 |

另有一处**文档口径修正**：`StudyPlan` 曾被笼统写成 "derived"，实际是
"**值 derived、快照行 stored**"。Task 52 的第一版测试断言"`StudyPlan` 必须从库里读回来"，
它失败了 —— 失败是对的，错的是那条断言。已按实测修正 `docs/architecture.md` 21.2 与
`docs/status.md`。

---

## BLOCKED / NOT VERIFIED

一份全是 PASS 的报告不可信。以下项目**没有**被真实验证，逐条写明"没验什么、为什么、
谁能验"。这些不是"待办清单"，而是本报告诚实性的边界。

### 1. NOT VERIFIED — 浏览器端到端渲染

本机是 Windows，浏览器自动化工具只支持 macOS / Linux。UI 的验证方式是 HTTP 契约测试
（297 条）+ 在 Node 里用最小 DOM 桩**真实执行**页面函数
（`scripts/ui_audit.js` 93 checks、`scripts/ui_render_check.js` 32 checks）。

**这不解析 CSS 布局、不触发真实事件、不做端到端导航，不声称等价于浏览器测试。**
Task 48–55 没有改变这一点，也没有新增任何浏览器验证。

### 2. NOT VERIFIED — 领域层热路径性能

G 门的规模断言（1000 / 100 / 50 / 500 / 5000 / 20000）**只覆盖持久化层**：
建库、落盘、单点查询、查询计划、足迹。领域层的覆盖率分析 / 依赖分析 / 学习路径 /
Dashboard 聚合等热路径由 `tests/test_performance.py`（Task 47.4，12 条，宽松上界）
覆盖，本门不重复计分。

**未覆盖**：内存占用、并发（本应用是单机单用户顺序处理）、长时间稳定性。
低配机器上的绝对速度不在保证范围内 —— 只保证量级正确。

### 3. NOT VERIFIED — 真实 ENOSPC / 真实处理超时

磁盘满与处理超时是**注入模拟**。真实 ENOSPC 与真实超时依赖操作系统状态，无法在测试里
稳定复现。验证的是**错误被正确分类与上报**（结构化错误码 + `StorageError` 携带 `cause`），
不是内核行为。

### 4. NOT VERIFIED — 崩溃测试只覆盖 Windows 的 `TerminateProcess` / `os._exit` 语义

覆盖的结束方式：干净退出（checkpoint 路径）、`TerminateProcess`（WAL 恢复）、
`os._exit(9)`（WAL 恢复并丢弃未提交帧）。

**未覆盖**：POSIX `SIGKILL`、断电、文件系统损坏、磁盘控制器缓存丢失。
覆盖的是 **WAL 恢复路径**，不是所有可能的存储故障。

崩溃测试的**非平凡化**手段（写在这里以免将来有人以为它必然成立）：
`PRAGMA cache_size = -8`（8 KiB 页缓存）把脏页挤进 `-wal`，判据是
"崩溃后 `-wal` 必须非空"。如果不做这一步，小库的脏页可能还没出内存，
"崩溃"就退化成了"干净退出"。

### 5. BLOCKED — 跨机器 / 跨平台可移植性

本阶段全部验收在 **Windows 11 + 便携 CPython 3.14（`Python/pythoncore-3.14-64/`）** 上完成。
没有在 macOS / Linux 上跑过同一套真子进程重启验收。`os.replace`、文件锁、
`-wal` / `-shm` 清理在不同文件系统上的行为差异**未被验证**。

### 6. BLOCKED — 真实云端 / 真实网络

本产品**刻意没有**云服务、没有数据库服务、没有消息队列。因此"云侧持久化"这一类
验收**不存在且不打算存在** —— 它不是待办，而是设计选择。列在这里是为了防止读者
把"没测"误读成"漏测"。

### 7. NOT VERIFIED — 备份归档的异地副本

备份默认落在 `data_dir/backups/` 里，也就是和被备份的数据在同一个目录。
整目录级丢失（误删、磁盘故障）会连备份一起带走。异地/异盘复制**需要用户自己做**，
本阶段没有实现也没有验证。

### 8. NOT VERIFIED — 多用户 / 鉴权 / TLS

单用户、单机、无鉴权。服务默认只绑回环地址，绑非回环地址必须显式 `--allow-remote`。
**不要把它暴露到公网。** 这与持久化无关，但属于"生产就绪"的完整边界，因此列出。

### 9. 一条被显式接受的跳过

全量回归里有 **1 skipped**：`tests/test_startup.py:276` ——
"faster-whisper is installed; cannot exercise the missing path"。
这是**环境事实**（本机确实装了引擎，无法走"缺引擎"分支），不是被屏蔽的失败。
它是允许的，但必须写在这里，而不是让读者从数字里猜。

---

## Final Verdict

**Production Ready**

理由：

**支持 Production Ready 的**

- spec 的最终验收标准被正面回答：真子进程建库 → 关进程 → 新进程只给 `data_dir` →
  **13 行矩阵全部还原**，`integrity_check == "ok"`、无外键违规、仍是 WAL、`rollbacks == 0`；
- Task 48–55 全部实现，本阶段新增 **582 条**测试，**无一条被删除、跳过或改写语义**；
- 13 个落盘点与 `FAULT_POINTS` 顺序全等，注入点覆盖与重启覆盖口径统一；
- 崩溃验收用生产代码自己的注入点 + `os._exit(9)`，判据是"崩溃后 `-wal` 非空"，
  不是"看起来崩了"；
- 变异测试证明 90 条重启测试里 60 条会因接线被拆而变红 —— 测试是承重的；
- 真值安全、确定性、幂等性、依赖审计、规模足迹、文件/库一致、构建卫生、版本文档
  十道门全绿；
- 生产规模实测：11.7 MiB / 27,601 行 / 444 字节每行 / 单点查询走覆盖索引；
- 5 个真实缺陷被发现、复现、修复并加了回归测试；
- 47.12 的端到端恢复演练（销毁工作库 → 恢复 → 逐项核对）**现在真的跑通了**，
  且先证明"工作库真的被删掉了"。

**不足以支撑的 / 必须与结论一起读的**

- 第 1 条 BLOCKED / NOT VERIFIED 里列的九项**没有**被验证，其中浏览器端到端渲染与
  跨平台可移植性是真实的空白；
- 无鉴权、无 TLS，不可直接暴露到公网；
- 崩溃验收覆盖的是 WAL 恢复路径，不是所有存储故障。

**为什么不是 Blocked**：没有任何一项测试失败或功能缺失；未验证项**边界清晰、
原因明确、已经写在这里**，而不是未知问题。把"未验证"如实标出来，本身就是
"证据优先"这条项目原则的一部分。

---

*本报告与 `docs/final_report.md`（Task 34–47 阶段报告）、`docs/status.md`（逐任务实现记录）
三者互补：本文件只回答"业务数据真的落盘了吗、重启真的还在吗"，不重复其余两份的内容。*
