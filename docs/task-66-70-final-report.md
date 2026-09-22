# TASK-66-70 RESULT

课堂助手 Task 66–70
「Daily Learning Workflow → Exam Review → Multi-Course → Real Semester Stress → Release 1.0」
最终报告。

这一阶段把产品从"课堂资料整理器 + 学习闭环"推进到**可以对外发布**的状态：
先把每日学习流程串起来，再给它加考前复习（且**结构上**不做押题），然后让它
同时容纳多门课程，接着把它压到真实学期规模验证不会退化，最后按发布门禁做
整体验收。

---

## 1. Version & Release

```text
APPLICATION_VERSION = 1.0.0
```

版本号从 `0.37.0` 升到 `1.0.0`。理由不是"凑个整数"，而是这一阶段**改了对外
契约**：

- Task 68 新增 3 条 GET 路由 + 课程作用域的持久化归属表（增量迁移 m003）；
- Task 69 改了持久化写路径的语义（增量落盘）—— 行为等价，但落盘范围不再
  是"整课重写"；
- Task 70 新增发布级契约：用户输入错误不得产生 500。

版本在 5 处保持一致，并由两处测试钉住
（`tests/test_production_gate.py::EXPECTED_VERSION` 与
`tests/test_release_gate.py::TestVersionRelease`）：

| 位置 | 值 |
|---|---|
| `src/application/workspace.py::APPLICATION_VERSION` | `1.0.0` |
| `src/__init__.py::__version__` | `1.0.0` |
| `tests/__init__.py::__version__` | `1.0.0` |
| `tests/test_production_gate.py::EXPECTED_VERSION` | `1.0.0` |
| `/api/health` 的 `version` 字段（实测） | `1.0.0` |

---

## 2. Task 66 — Daily Learning Workflow

把既有链路 `Today → StudyPlan → LearningPath → Knowledge → Exercise → Answer
→ Evaluation → StudentState` 串成**可中断、可恢复、可重复**的每日流程。
不新建任何领域能力，只做"选择 + 投影 + 串接"。

关键约束与实现：

- **任务选择是确定性的**，不掺随机，也**不按"考试概率"排序**（本产品没有
  概率这个概念）。
- **状态只能由合法的 `LearningEvent` 驱动**：状态机在 `src/student_learning.py`
  里，`_TRANSITIONS` 白名单之外的迁移直接拒绝。
- **"下一任务"文案是事实性的**。Task 66 点名禁止 `You mastered this topic.`
  —— 它由 `tests/test_stress_semester.py::TestRealStudent::test_next_task_never_claims_mastery`
  与 Task 66 自己的套件双重钉住。
- 派生视图全是**只读投影**（`LearningWorkflow`），不写知识、不改状态。

```text
tests/test_learning_workflow.py    102 passed
5 个依赖套件                       412 passed, 3 skipped
scripts/ui_audit.js                195 checks  OK
scripts/ui_render_check.js         119 checks  OK
```

---

## 3. Task 67 — Exam Review Mode

回答的问题是 **"考试快到了，我该按什么顺序复习？"**，而**不是**"哪些最可能考？"。

后者在本模块里**结构上无法被回答**：`ReviewSet` 没有概率字段，类上也没有任何
写方法。禁止词（最可能考 / 考试概率 / 预测题 / 押题 / 考点概率 / 预测分数）
由"No Prediction Contract"套件扫描源码与全部三语文案。

- 覆盖度展示的是**两条独立的真相轴**（`validation_status` 与
  `review_status`），永不合并成一个"完成度"。
- `ReviewSet` 只用已有数据构造；冲突**永不**自动消解。

```text
tests/test_review_mode.py          162 passed, 1 skipped
tests/test_learning_workflow.py    102 passed   (Task 66 未回归)
scripts/ui_audit.js                235 checks  OK
scripts/ui_render_check.js         145 checks  OK
```

---

## 4. Task 68 — Multi-Course Workspace

spec 里最关键的一句：**内容寻址的 Knowledge ID 跨课程碰撞是合法的** —— 两门
课用了同一份讲义，就会得到同一个 `knowledge_id`。

这句话把实现方向整个翻过来了：要做的**不是**"让 id 全局唯一"，而是"每一次
查询都必须带 `course_id`"。所以存储层是**加一张课程作用域的归属表**，而不是
改主键（改主键在 SQLite 里只能重建表，会让五张子表的外键在运行期报
`foreign key mismatch`，已实测）。

- `src/persistence/migrations/m003_course_scoped_identity.py`：纯增量迁移。
- `MultiCourseWorkspace`：只读投影，`my_courses` / `course_summary` /
  `resolve_selection` 三个方法；每个计数都是**带 `course_id` 的独立查询**。
- `_totals()` 把两条真相轴**分开**求和，永不合成"完成度"。

```text
tests/test_multi_course.py         136 passed
pytest (full)                      5065 passed, 10 skipped
scripts/ui_audit.js                275 checks  OK
scripts/ui_render_check.js         163 checks  OK
```

---

## 5. Task 69 — Real Semester Stress

完整报告见 `docs/task-69-stress-report.md`。规模（实测）：

```text
5 门课 / 75 节课 / 750 份材料 / 3000 知识点 / 100 主题
501 个学生 / 5000 道题 / 20003 条作答 / 数据库 47.9 MB
整学期构建 124 秒，重开整个学期 0.270 秒
```

这一轮查出并修掉 **4 个真实缺陷**，全是"每一条查询/每一次落盘都乘以了数据量"
—— 功能测试在小夹具上完全看不见它们：

| # | 缺陷 | 效果 |
|---|---|---|
| 1 | 提交一条作答重写**整门课** | 68 → 1.89 ms/条 |
| 2 | 冷启动按知识点查溯源链 | 冷启动 96 → 恒定 17 条 SQL |
| 3 | 冷启动按学生读学习日志 | 冷启动 81 → 恒定 21 条 SQL |
| 4 | 提交一条作答重写**该学生自己的全部历史** | 410 → 恒定 10 条 SQL |

缺陷 4 一开始被写成"append-only 的固有语义，不是缺陷"。那是判断错误：
append-only 要求"已有的不会被改写"，**不**要求"每次都把已有的再写一遍"，而
`event_id` 由内容派生，跳过与重写**逐字节等价**。已连同那段文档一起改掉。

```text
tests/test_stress_semester.py      60 passed   (10:39)
pytest (full)                      5125 passed, 10 skipped  (18:40)
```

---

## 6. End-to-End Workflow（全新安装 → 发布）

`src/application/acceptance.py::AcceptanceHarness` 跑的是完整链路：
建课 → 建课堂 → 上传文档/音频/板书图 → 处理 → 证据 → 知识 → 校验 → 复核 →
覆盖度 → 学生 → 出题 → 作答 → 评价 → 学习计划 → 学习路径；另有
重复材料 / 失败材料 / 重启 / 备份 / 学生学习 五个场景。

Task 70 额外钉了**全新安装**这一格（`tests/test_release_gate.py::TestFreshInstall`）：

- 空目录起服务 → `/api/health` 返回 `status: ok`；
- `/api/courses` 返回空数组（全新安装不该有任何课程）；
- 能建出第一门课；
- 数据库文件**真的**生成在 `<data_dir>/database/classroom.sqlite` 且非空。

```text
tests/test_acceptance.py           105 passed
```

---

## 7. Human Review（人工复核）

复核是**人**做的决定，产品只负责把它记下来并且不再偷偷改：

- `validation_status`（`unverified` / `supported` / `conflicted`）与
  `review_status`（`pending` / `confirmed` / `rejected` / `kept_unverified`）
  是**两条独立的轴**，永不合并、永不求和。
- 复核记录 append-only；冲突**永不**自动消解。
- **人工决定是黏的**：重新处理材料不会把已经做出的复核决定清掉
  （`tests/test_hardening_truth_safety.py::test_reprocessing_does_not_reopen_a_human_decision`）。

```text
tests/test_hardening_truth_safety.py   14 passed
tests/test_acceptance.py（含复核环节） 105 passed
```

---

## 8. Student Learning & Mistake

- 学习状态只能由合法 `LearningEvent` 驱动（`src/student_learning.py` 的
  `_TRANSITIONS` 白名单）。
- **`wrong_count > 0` 不等于"薄弱"**：压力测试里的真人风格学生每一步都
  故意答错，测试断言其状态**不是**"薄弱判定"
  （`TestRealStudent::test_the_real_student_state_is_not_a_weakness_verdict`）。
- 错题中心按证据分组，可追溯到材料；同一知识点的错题聚合在一起，不重排序、
  不打分。

```text
tests/test_learning_workflow.py    102 passed
tests/test_mistakes_center.py       80 passed
```

---

## 9. Multi-course Isolation

- 每次查询都带 `course_id`；`my_courses` 的每个计数都是独立查询，绝不
  "先合并再分组"。
- 学生 / 材料 / 练习 / 作答 / 知识点 id 逐类**互斥**（压力规模下实测）
  —— `TestCrossCourseLeakage`（9 条）。
- **知识点 id 允许跨课程碰撞**（同一份讲义），这是合法的；要的是
  "课程作用域查询"，不是"全局唯一"。

```text
tests/test_multi_course.py          136 passed
tests/test_stress_semester.py::TestCrossCourseLeakage   9 passed
```

---

## 10. Truth Safety

- 证据优先：知识点必须能追溯到材料，追不到的进 `unresolved_material_ids`
  并在页面上显示"溯源断链"，**不**假装正常。
- 两条真相轴分开呈现。
- 学生状态**不会**改变知识真相（生产门禁
  `TestTruthSafety::test_student_state_does_not_change_knowledge_truth`）。
- 冲突保留人选择的那一侧证据，重启后仍是冲突。

```text
tests/test_hardening_truth_safety.py   14 passed
tests/test_production_gate.py::TestTruthSafety   （含在全量回归内）
```

---

## 11. Persistence

SQLite 是唯一存储后端；写穿落盘，`Workspace` 的写操作一律
`with self._atomic():` + `_flush_*()`。

Task 69 把落盘从"整课重写"改成**增量写**：`_flush_learning` 带
`student_ids` / `exercise_ids` / `answer_ids` 三个作用域参数
（空列表 = 一个都不写，与 `None` = 全部写严格区分），`save_student_log`
只写新增/变更的事件与状态。

```text
tests/test_persistence_*.py       （8 个文件，含在全量回归内）
tests/test_persistence_transactions.py   71 passed
```

---

## 12. Restart

- 10 次重启闭环：指纹、计数、答案历史逐次不变
  （`TestRestartCycles`，6 条）。
- 重启后重开整个学期 0.270 秒，且**重开代价与数据量无关**。
- 生产门禁 A 段是 13 行的持久化重启矩阵。

```text
tests/test_restart_recovery.py      90 passed
tests/test_stress_semester.py::TestRestartCycles   6 passed
```

---

## 13. Backup & Restore

- 3 次备份 / 3 次恢复，一律恢复到**另一个** `data_dir`（灾难恢复语义）。
- 恢复后：课程集合、知识点/材料/练习/学生/作答计数一致；材料文件**逐字节**
  相同；作答仍然课程隔离；指纹在三轮"备份→恢复→再备份→再恢复"里一路不变。
- 归档里**真的装着**数据库 + 全部 750 份材料（判据是"归档里有什么"，不是
  "字节数大于某个常数"）。

```text
tests/test_backup_recovery.py        49 passed
tests/test_hardening_backup_drill.py 19 passed
tests/test_stress_semester.py::TestBackupRestore   5 passed
```

---

## 14. i18n（zh / es / ca）

三语词条**逐键对齐**，各 370 条，无缺译、无裸 key 漏到界面上：

```text
zh keys = 370
es keys = 370
ca keys = 370
```

两个 Node 检查会扫"页面上不得出现未翻译的裸 key"（形如 `val.confirmed`、
`mc.count.*`），并且**否定句里也不得出现被禁的排序话术**（Task 68 踩过：
es 副标题写"sin orden recomendado"来声明"没有排序推荐"，结果被无排序话术
扫描判违规 —— 改成不出现被禁词，意思交给 `mc.noRanking`）。

---

## 15. UI Audit

```text
scripts/ui_audit.js    275 checks  OK
```

覆盖 i18n 键完整性、三语对齐、禁用话术扫描、路由与页面函数对应、
不得出现裸 key 等。

## 16. UI Render Audit

```text
scripts/ui_render_check.js    163 checks  OK
```

在 Node VM 里加载 `src/web/app.js`，用最小 DOM 桩渲染每个页面函数，断言
不抛异常、产出 HTML、无 `undefined` / `NaN`。

Task 69 另加了 `scripts/ui_stress_check.js`：跑在**真实 HTTP** 上（上面那个
是桩路由），渲染 12 个页面函数（dashboard / today / my-courses / course /
session / knowledge / knowledge-detail / students / exercises / exercise /
mistakes / review）。

---

## 17. API Contract Gate

**用户输入错误永远不得产生 500。** 500 意味着"用户的输入把服务器打崩了"，
既是可用性问题也是安全问题（内部信息可能随之泄漏）。

这条是**穷举**式的，不是挑几个端点写几条断言 —— 挑着写会漏掉新加的路由。
实测路由表：58 GET / 19 POST / 1 PATCH。

| 扫描 | 请求数 | 结果 |
|---|---|---|
| 每个带占位符的 GET × 13 个坏值 | 481 | 0 个 500 |
| 每条 GET 路由不带必填 query | 58 | 0 个 500 |
| 每条写路由 × 4 个畸形 body | 76 | 0 个 500 |

坏值包括空串、空白、`..%2F..%2F..%2Fetc%2Fpasswd`、`%00`、300 字符长串、
`course' OR 1=1`。同时断言：4xx 响应必须是标准信封（`success: false`）、
且**不含 Python 堆栈**；未知 `/api/` 路由是 404 不是 500；正常路径仍然 200。

> 非 `/api/` 的未知路径会回退到 SPA 首页，那是**故意**的前端路由，
> 不是错误 —— 所以门禁只判 `/api/` 前缀，并单独断言 SPA 回退返回外壳页面。

---

## 18. Security Gate

- **无长得很像密钥的字面量**：扫 `sk-...` / `AKIA...` / `ghp_...` 前缀
  （判据刻意保守，避免天天误报然后被人关掉）。
- **服务只监听回环地址**：`127.0.0.1` / `localhost` / `::1`。
- **静态目录不得被 `..` 穿越**：判据看**内容**（`requirements.txt` 里有
  `faster-whisper`，响应体里没有它就没被读到）。
- **登记 data_dir 之外的材料时，产品把它收进 data_dir**：材料是副本不是指针，
  所以备份能真正带走内容，data_dir 是唯一需要关心的位置。
- 密钥脱敏由 `tests/test_config_secrets.py` 守卫。

---

## 19. Dependency Gate

**不得引入 LLM / 云 API / embedding / 向量库。** 全部能力必须本地可跑、
可审计、可复现。

`tests/test_production_gate.py::FORBIDDEN_TECHNOLOGY` 守的是数据库/消息队列/
前端构建链那一侧；Task 70 新增的 `tests/test_release_gate.py` 补的是
**AI 供应链**这一侧，并且是**全 `src/` 扫描**：LLM API（openai / anthropic /
cohere / …）、LLM 框架（langchain / llama_index / transformers /
sentence_transformers / …）、embedding 与向量库（faiss / chromadb / pinecone /
qdrant / weaviate / milvus / lancedb / …）、云 SDK（boto3 / google.cloud /
azure.*）。扫描结果：**0 命中**。

唯一的例外是 `torch`：它只在 `src/whisper_provider.py` 的 **CUDA 分支**里被
惰性 import，用来探测本机有没有可用 GPU，**不参与推理**（推理由
faster-whisper / CTranslate2 完成）。这条例外被单独钉住 —— 断言每一处
`torch` 引用的上下文里都必须出现 `cuda`，否则"例外"会悄悄变成"口子"。

`requirements.txt` 里也没有上述任何一项；存储后端仍是 SQLite 独占。

```text
tests/test_dependency_audit.py      12 passed（既有）
tests/test_release_gate.py::TestDependencyGate   4 passed
```

---

## 20. Tests

| 套件 | 条数 |
|---|---|
| `tests/test_learning_workflow.py`（66） | 102 |
| `tests/test_review_mode.py`（67） | 163 |
| `tests/test_multi_course.py`（68） | 136 |
| `tests/test_stress_semester.py`（69） | 60 |
| `tests/test_release_gate.py`（70） | 22 |
| `tests/test_acceptance.py` | 105 |
| `tests/test_restart_recovery.py` | 90 |
| `tests/test_backup_recovery.py` | 49 |
| `tests/test_mistakes_center.py` | 80 |
| `tests/test_hardening_truth_safety.py` | 14 |
| `tests/test_hardening_backup_drill.py` | 19 |
| `tests/test_determinism_audit.py` | 12 |

---

## 21. Full Regression & Compileall

```text
pytest (full)              5147 passed, 10 skipped   (约 20 分钟)
compileall -q src tests    exit 0
scripts/ui_audit.js        275 checks  OK
scripts/ui_render_check.js 163 checks  OK
```

10 个 skip 全部是"真实引擎模型未下载"一类（Whisper / OCR 集成测试），由
`test_production_gate.py` 断言**测试存在**，跳过情况记录在
`docs/final_persistence_report.md`。

---

## 22. Test Stability Investigation

发布前专门查了一遍"会不会随机失败"：

**静态检查**

- `tests/` 里唯一的 `import random` 在 `test_hardening_traceability.py`，
  用的是**固定种子** `random.Random(47)` —— 既是"随机抽样"又可复现。
  项目对**产品代码**禁止 `random`，测试里的确定性抽样不在此列。
- 产品代码里的 `datetime.now` / `uuid4` 由 `tests/test_determinism_audit.py`
  （12 条）守卫：`uuid4` 只允许出现在白名单文件的空值兜底或临时文件名位置。
- 唯一的**等待型**逻辑在 `test_hardening_database_safety.py`：子进程握手轮询，
  超时 120 秒。这是在等"子进程写完标记文件"，不是等一个固定时间，负载高时
  只会更慢不会误判。

**动态检查**：全量回归连续跑两遍：

```text
第 1 遍   5146 passed, 10 skipped, 1 failed   (20:19)
第 2 遍   5147 passed, 10 skipped, 0 failed   (21:06)
```

第 1 遍那 1 个失败是**已知的、自指的那一条**：
`test_release_gate.py::test_the_release_report_exists_and_states_the_verdict`
断言"最终报告必须存在"，而报告要等回归数字出来才写得完。写完报告之后第 2 遍
全绿。两遍的 skip 集合相同（10 个，全是"真实引擎模型未下载"）。

**结论**：没有发现随机失败的来源。压力套件（最吃时序的那一组，10:39）在两遍
里都通过。

---

## 23. Known Limitations

如实记录，不粉饰：

1. **绝对耗时不做断言。** 本机同时跑着 IDE 与其它进程，毫秒数抖动很大。
   性能门禁断言的是**复杂度**（SQL 条数为常数 / 与数据量无关），不是"多少
   毫秒"。报告里的耗时是单次实测读数，只用于说明量级。
2. **10 个 skip 是环境性的**：真实 Whisper / OCR 模型未下载。测试**存在**
   且被门禁断言存在，跳过情况如实记录，但没有在本机跑过真实模型。
3. **整学期构建 124 秒进入全量回归总时长**（总计约 20 分钟）。这是刻意的
   —— 缩小规模就测不到 Task 69 要测的东西。
4. **`torch` 例外**：只在 CUDA 分支惰性 import，不参与推理。若将来有人把它
   挪到常规路径，`test_release_gate.py` 会立刻失败。
5. **备份归档名来自时钟**：同一秒内连做多次备份会撞 `DuplicateBackupError`
   （幂等保护）。批量备份的调用方需要各自传入不同时间戳。
6. **前端是单文件 `src/web/app.js`**，无构建链（这是硬约束，不是省事）。
   规模再增长时需要按模块拆分，但拆法必须保持"零构建"。

---

## 24. Final Verdict

| Gate | Result |
|---|---|
| Fresh Install | PASS |
| Human Review | PASS |
| Student Learning | PASS |
| Mistake & Weak Knowledge | PASS |
| Multi-course Isolation | PASS |
| Truth Safety | PASS |
| Persistence | PASS |
| Restart | PASS |
| Backup & Restore | PASS |
| i18n (zh / es / ca) | PASS |
| UI Audit | PASS |
| UI Render Audit | PASS |
| API Contract Gate | PASS |
| Security Gate | PASS |
| Dependency Gate | PASS |
| Test Stability | PASS |
| Full Regression | 0 failed |
| Compileall | PASS |

```text
FINAL VERDICT: PASS
RELEASE: 1.0.0
```

---

*Task 66–70 完成后，Classroom Assistant 是：一个**全新安装即可用**、
**多课程**、**能扛住真实学期规模**、**不押题**、**重启与灾难恢复都一致**的
本地课堂学习助手。*
