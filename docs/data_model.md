# 数据模型

两层：**领域模型**（`src/models.py` 等，Python dataclass）与**持久化模型**
（`src/persistence/`，SQLite）。持久化层刻意采用「规范列 + 权威 payload」的
双轨结构，理由见下文第 3 节。

---

## 1. 核心链路

```text
Material ──→ Evidence ──→ KnowledgePoint ──→ Validation / Review
  材料          证据            知识点              验证 / 审核
   │                              │
   └── 受管副本 (audio/ images/ documents/)    └── Topic / Relationship / Conflict
```

`SourceReference` 是**定位**：它让「这条证据来自哪」成为机器可查的事实，而不是
一句描述。音频有 `timestamp_start` / `timestamp_end`，文档有 `page` / `line` /
`paragraph`。

## 2. 枚举

### `Language`

`Spanish` / `Catalan` / `Chinese` / `English` / `Unknown`

`from_string` 大小写不敏感，识别不了就返回 `Unknown`——**不猜**。
（项目里曾经有一个真实缺陷：把 `Language.SPANISH.value`（`"Spanish"`）拿去和
`"es"` 比较，于是恒为假。这是「能力存在但没接线」的典型。）

### `MaterialType`

`audio` / `image` / `text` / `note` / `syllabus`

### `EvidenceType`

`transcript` / `OCR` / `personal_note` / `classmate_note` / `teacher_statement` /
`extracted_fact` / `document` / `other`

区分 `personal_note` 与 `classmate_note` 是刻意的：两者的可信度不同，
冲突检测需要知道谁说的。

### `Confidence`

`HIGH` / `MEDIUM` / `LOW` / `UNCERTAIN`

### `ValidationStatus`（验证）

| 值 | 含义 |
| --- | --- |
| `unverified` | 支撑证据不足，或完全没有证据 |
| `supported` | 有一条或多条独立证据支撑，且没有未解决的冲突 |
| `conflicted` | 至少有一条未解决的冲突碰到它，**两边都保留** |

`supported` **只表示「当前材料是自洽的」，不是「客观为真」**。这个区别写在
代码注释里，也写在界面上。

### `ReviewStatus`（审核）

| 值 | 含义 |
| --- | --- |
| `pending` | 还没收到明确的人工决策 |
| `confirmed` | 用户显式确认（冲突项还要指定采信了哪一侧） |
| `rejected` | 用户显式拒绝 |
| `kept_unverified` | 用户显式决定暂时保持未验证 |

**进入后三个状态必须经过 `KnowledgeReviewService` 的显式人工决策。**
确定性流水线里的任何东西——证据条数、`knowledge_score`、`SUPPORTED`——
都**不会**把它推进到 `confirmed`。

## 3. 持久化模型：规范列 + 权威 payload

每张表有两种信息：

1. **规范列**：身份、外键、以及需要被查询/排序/约束的字段
   （`course_id` / `language` / `status` / `sequence` …）。它们让「关系」成为
   数据库能自己保证的事：外键、唯一约束、索引。
2. **`payload` 列**：领域对象自己 `to_dict()` 出来的 JSON。

### 为什么不把每个字段都拆成列

项目的核心不变量是**「绝不丢任何信息」**：溯源（`SourceReference` 的
`timestamp_start` / `page` / `line` / `paragraph`）、`metadata` 这类开放字典、
以及将来新增的领域字段，只要拆列就一定会漏。用领域自己的 `to_dict()` 作为权威
表示，可以保证「读回来和写进去逐字节一致」。

### 为什么不只用 JSON

那样就退化成「把文件塞进数据库」——外键、唯一性、去重、索引全部消失，
而规范明确要求事务、幂等、provenance 关系不丢。

**规范列负责关系与约束，payload 负责完整性。** 两者的一致性由仓储保证：
规范列从领域对象派生，绝不手写。

### `payload_version`

每张表带 `payload_version`，记录该行 payload 的序列化版本。领域模型将来变化时
可以据此识别旧行并迁移，而不是「猜着反序列化」。

## 4. 表清单

### 迁移 001（初始 schema）

| 分组 | 表 |
| --- | --- |
| 课程 / 课堂 | `courses`、`sessions` |
| 材料 | `materials`、`material_processing` |
| 证据 | `evidence`、`material_evidence` |
| 知识 | `knowledge_points`、`knowledge_point_evidence` |
| 关系 / 冲突 | `relationships`、`conflicts`、`conflict_evidence` |
| 审核 | `review_records` |
| 组织 | `topics`、`knowledge_memberships`、`session_memberships`、`knowledge_relations` |
| 学生 | `students`、`learning_events`、`student_knowledge_state` |
| 练习 | `exercises`、`exercise_knowledge_points`、`exercise_evidence` |
| 作答 / 评估 | `student_answers`、`evaluation_results` |
| 计划 / 路径 | `study_plans`、`learning_paths` |

### 迁移 002（知识组织）

- 新增 `database_meta`（应用版本、schema 元信息）
- `ALTER TABLE material_processing` 增加处理时间戳列
- 新增索引 `idx_kp_course_title`

### 为什么 `materials` 与 `material_processing` 是两张表

规范点名的原子对是「Material registration + processing status 不能写一半」。
**拆成两行写入，才真正需要一个事务来保证原子性**；若塞进同一行，「原子」就成了
同义反复，也就无从测试。所以这是刻意的设计，不是过度拆分。

## 5. 迁移规则

- 每个迁移是一个 `Migration`，携带**严格递增**的整数 `version`。
- 迁移**只做加法**：新增表 / 新增列 / 建索引 / 数据回填。
  **绝不 `DROP TABLE` 已有业务表**——那等于删库重建。这条由
  `tests/test_persistence_migration.py::test_no_migration_drops_a_table` 断言。
- 每条迁移在**自己的事务**里执行，失败即整体回滚，不会留下半迁移的 schema。
- 迁移名一旦发布就不许改（它写进了台账，改名会让审计对不上）。
- 库版本**高于**代码支持的上限 → `UnsupportedSchemaVersionError`，**绝不降级或猜测**。

### 版本号在代码里，不在数据库里

`version` 是代码的事实，台账（`schema_version` 表）只是「已经执行过哪些」的流水。
这样「代码里有 001/002/003，库里只到 002」就自然表示「还差 003」，迁移器按序
补齐即可，不需要任何外部状态文件。

### `migrate()` 在最新库上必须完全不写盘

这不是省事。`migrate()` 每次打开库都会跑；如果它在「已经是最新版本」这条最常见
的路径上写一次盘，就要抢一次写锁，而写锁会被任何正在进行的**长事务**（导入、
批量落库）挡住。本项目是浏览器轮询的本地服务，「打开库 = 可能等 5 秒然后失败」
是不能接受的。

实现方式是先**只读地**看一眼 `schema_version` 表在不在，不在才去建。
这条承诺由 `tests/test_hardening_database_safety.py::
TestMigrationLeavesUpToDateDatabasesAlone::
test_migrate_on_an_up_to_date_database_does_not_touch_the_file`
用文件 mtime 与 WAL 大小验证。

## 6. 事务与并发

- 仓储方法只发 SQL，**不自己 `BEGIN`**。这样它们既能被单独调用（自动提交），
  也能被组合进更大的事务。
- 如果每个仓储自己开事务，「材料 + 状态」这类跨表原子性就永远做不到——
  内层提交会把外层的回滚变成谎言。
- 事务入口只有一处：`Repositories.transaction()`（跨仓储的工作单元）。
  嵌套安全（用 SAVEPOINT）。
- 文件库使用 **WAL** 模式：读写不互斥，因此并发读成立。内存库不支持 WAL。
- 一次业务操作 = 一个事务：应用层在 `src/application/persistence_wiring.py` 暴露
  `atomic()`（`BEGIN IMMEDIATE`，`BaseException` 也回滚），每个写操作包在它里面，
  保证跨表成对写入不留半写。详见 `docs/architecture.md` 21.7。

### 落盘对象：stored vs derived

数据库存**输入与审计历史**，不存答案；重启时由磁盘上的行重建一切派生视图。

| 对象 | 落盘性质 | 说明 |
| --- | --- | --- |
| `Course` / `Session` / `Material` + 受管文件 | stored | 记录 + 文件两半，同活 |
| `Evidence` | stored | 全字段 + 材料关联 |
| `KnowledgePoint` | stored | 含 `validation_status` / `review_status` |
| `ReviewRecord` | stored | 追加式，逐条一致（含人显式选的证据侧） |
| `Topic` / `Membership` / `Relation` | stored | 组织层 |
| `Student` + `StudentState` + `LearningEvent` | stored | |
| `Exercise` / `StudentAnswer` / `EvaluationResult` | stored | |
| `StudyPlan` **快照行** | stored | 值重算，内容寻址保证同一 `plan_id` |
| `LearningPath` | **derived** | 表是 append-only 审计；值由产品重算 |
| `Coverage` / `Gaps` / `Dependencies` / `Dashboard` | **derived** | 不落盘，重启重算 |

这张表的口径与 `FAULT_POINTS` / `RESTART_MATRIX` 一致，详见
`docs/final_persistence_report.md` 第 A / B 两节与 `docs/architecture.md` 21.2。

### 一个真实的陷阱：事务开着时不能快照

SQLite 在线备份 API 要在源连接上取得读锁，而 `BEGIN IMMEDIATE` 已持有写锁，
于是永久挂死（连空事务也会）。`Database.backup_to()` 因此**显式拒绝**事务未提交
时快照，并且**绝不**替调用方 commit。详见
[backup_restore.md](backup_restore.md#一个真实的陷阱事务开着时快照会永久挂起)。

## 7. 标识符的确定性

**业务 ID 不允许来自 `uuid4()` / `datetime.now()` / `random` / `hash()`。**

为什么：同一个输入必须永远得到同一个 ID。否则测试不可复现、备份恢复之后引用会
错位、diff 会充满噪声。

- `course_id` / `material_id` / `evidence_id` / `knowledge_id` 都是**内容寻址**的
  （SHA-256 派生）。
- 唯一允许的非确定性来源是 `src/application/runtime.py` 里的**可注入 `Clock`**。
- 少数几个 `uuid4()` 是**兜底分支**，只在领域对象没拿到 ID 时才走，
  并且有语义守卫（`if not self.knowledge_id:`）。

这条由 `tests/test_determinism_audit.py`（10 条）用 AST 扫描强制：
`datetime.now` / `utcnow` 只允许出现在 `runtime.py`；`random.*` 与内建 `hash()`
完全禁止；`uuid4` 只有白名单文件可以出现，且每一处还要通过逐行的语义检查。

## 8. 错误码

8 个结构化错误码，所有层都往上映射到它们：

| 码 | 含义 |
| --- | --- |
| `INVALID_INPUT` | 调用方参数不合法 |
| `NOT_FOUND` | 目标不存在 |
| `CONFLICT` | 与现有状态冲突（重复、并发修改） |
| `PROCESSING_ERROR` | 处理过程失败 |
| `UNSUPPORTED` | 能力/格式不支持 |
| `STORAGE_ERROR` | 存储层失败 |
| `CONFIGURATION_ERROR` | 配置不合法 |
| `INTERNAL_ERROR` | 兜底 |

持久化层有自己更细的码（`DUPLICATE_RECORD` / `STORAGE_CORRUPTED_DATABASE` /
`STORAGE_MIGRATION_FAILED` / `STORAGE_TRANSACTION_FAILED` / `INVALID_SCHEMA_VERSION`），
由 `map_application_error` 依据领域异常的 `.code` 映射成上面这 8 个。

数据库约束冲突的**翻译是仓储层的责任**：`UNIQUE constraint failed` →
`DuplicateRecordError`（→ `CONFLICT`），`FOREIGN KEY` / `NOT NULL` / `CHECK` →
`PersistenceValidationError`（→ `INVALID_INPUT`）。原始 SQLite 文本被保留在消息里，
便于诊断。

## 9. 备份模型

见 [backup_restore.md](backup_restore.md)。要点：归档由 `manifest.json` +
`database.sqlite` + 材料文件组成；清单里带 `backup_version` 与 `schema_version`，
后者用于在**第一个破坏性步骤之前**拒绝来自更新版本的归档。
