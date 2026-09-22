# Task 69 — Real Semester Stress 压力报告

日期：2026-09-18
范围：`tests/test_stress_semester.py`（60 条）+ `scripts/ui_stress_check.js`
结论：**真实学期规模跑得动，且跑动过程中查出并修掉了 4 个真实缺陷。**

---

## 一、为什么要有一个"真实学期"的测试

前面几个任务的功能测试都建立在"几份材料、几十条作答"的夹具上。那种规模
下**跑不出**的一类缺陷是：实现里的每一条查询、每一次落盘都乘以了数据量。
功能测试全绿，但一个学生认真学习一个学期之后，系统就会退化成 O(N²)。

这一轮刻意把规模拉到真实学期：

```text
课程            5
课堂            75     (5 × 15)
材料            750    (5 × 15 × 10)
知识点          3000
主题            100
学生            501    (500 合成 + 1 真人风格)
练习            5000
作答            20003  (合成 20000 + 真人风格学生 3)
数据库          47.9 MB
材料文件        165 KB
```

整学期构建（75 次 `process_session` + 全部作答）**124 秒**。

---

## 二、这个文件刻意不做什么

**1. 不压测绝对耗时。** 本机同时跑着 IDE 和其它进程，毫秒数抖动很大，把
"`my_courses()` 必须小于 50ms"写成断言只会得到一条随机失败的测试。真正
能抓住缺陷的判据是**复杂度**：

- 同一个操作的 SQL 条数必须与数据量**无关**（大库小库相等）；
- 单条作答的 SQL 条数必须是**常数**（不随课内数据量、也不随这个学生自己的
  历史增长）。

SQL 条数用 `sqlite3.Connection.set_trace_callback` 统计，而不是给
`Database` 打猴子补丁 —— 后者漏得掉 `query_one` / `scalar`。

**2. 不为了规模而牺牲语义。** 20000 条作答里约 1/3 是错的
（`is_true=(i % 3 != 0)`、作答值按 `i % 2` 交替），所以错题中心、学生状态、
复习集在压力规模下仍有真实内容，不是一堆全对的空壳。真人风格学生每一步
**故意答错**，这样"答错不等于薄弱"这条语义也有真实数据可查。

**3. 重启/备份测试不改共享夹具。** 恢复一律恢复到**另一个** `data_dir`
（灾难恢复语义），既能验证"换台机器也能还原"，又不会把模块级夹具写成
别的测试读不到的状态。

---

## 三、查出的 4 个真实缺陷（全部已修，全部钉了断言）

### 缺陷 1 —— 提交一条作答会重写整门课

`Workspace._flush_learning()` 无条件写整门课：全部练习、全部学生的日志、
全部答案与评估。单条作答 68 ms，20000 条作答是 O(N²) 条 SQL。

修法：给 `_flush_learning` 三个增量作用域参数
`student_ids` / `exercise_ids` / `answer_ids`，每个调用点只写自己改动过的
对象。**空列表 = "一个都不写"，与 `None` = "全部写"严格区分**（提交答案不改
练习，所以那里给 `exercise_ids=[]`）。

窄化是安全的，前提是查过 `ExerciseRepository.save`：它是按 id 幂等 upsert
的，**不会**先按课程清空再插入，所以写子集不会删掉没写的那些。

效果：68.1 ms → 1.89 ms/作答。

### 缺陷 2 —— 冷启动按知识点查溯源链（N+1）

`KnowledgeRepository.load_structure` 对每个知识点调一次
`course_evidence.rights_for()`。一门课 600 个知识点就是 600 条 SQL，五门课
的 `my_courses` 冷启动 3000 条。

修法：新增 `CourseLinkRepository.rights_for_many()`，一条
`WHERE course_id = ? AND knowledge_id IN (...)` 分批（每批 500 个 id）拿回。

效果：冷启动语句数 **96（大库）/ 24（小库）→ 恒定 17**，与知识点数无关。

### 缺陷 3 —— 冷启动按学生读学习日志（N+1）

`Workspace._load_course_state` 逐个学生调 `p.load_student_log()`，每个学生
3 条 SQL（身份 + 事件 + 状态） —— 一门课 500 个学生 1500 条。

修法：新增 `LearningEventRepository.load_for_course` /
`StudentKnowledgeStateRepository.load_for_course`，以及
`snapshot.load_student_logs()`：3 条 SQL + 内存分组。两个批量读的排序与
逐学生读**完全一致**，所以重建出来的日志逐个字段相同。

效果：冷启动语句数 **81（20 个学生）/ 21（1 个学生）→ 恒定 21**。

### 缺陷 4 —— 提交一条作答会重写这个学生自己的全部历史

`snapshot.save_student_log` 把该学生**全部历史事件**逐条 upsert 一遍。
实测（一个学生连续作答）：

```text
答到第   1 条 ->  12 条 SQL   答到第 100 条 -> 110 条 SQL
答到第   2 条 ->  10 条 SQL   答到第 200 条 -> 210 条 SQL
答到第  10 条 ->  20 条 SQL   答到第 300 条 -> 310 条 SQL
答到第  50 条 ->  60 条 SQL   答到第 400 条 -> 410 条 SQL
```

**学生的历史是无界的**，所以认真学一个学期的学生就能把整个系统拖成
O(N²) —— 和缺陷 1 是同一类问题，只是维度从"课内有多少别人"变成"我自己
答过多少"。

修法：事件**追加且不可变**，且 `event_id` 完全由内容派生
（`(student, course, kp, type, sequence)` 的 SHA），同一个 `event_id` 的
payload 永远相同 —— 所以"跳过已落盘的事件"与"再 upsert 一遍"是**逐字节
等价**的，不是近似。新增 `LearningEventRepository.existing_ids()`
（1 条 SQL）取回已落盘 id，只写新的。状态记录会变（计数递增），不能按 id
跳过，改为 `rows_for()` 读回现状逐个比对，只写真变了的那些。

效果：单条作答 **410 → 恒定 10 条 SQL**；连续 400 次作答
**2.40 s → 0.32 s**；学期规模下每条作答 **58 → 10 条 SQL、4.61 → 2.78 ms**。

> 这条一开始被写进测试类的文档字符串里，说成"事件流 append-only 的固有
> 语义，不是缺陷"。那是判断错误：append-only 要求的是"已有的不会被改写"，
> 并不要求"每次都把已有的再写一遍"。发现之后连同那段文档一起改掉了。

---

## 四、压力规模下的实测读数

| 操作                       | 实测       | 说明                     |
| -------------------------- | ---------- | ------------------------ |
| 重开整个学期（冷）         | 0.270 s    | 5 门课 / 20003 条作答    |
| `my_courses()`（全部 5 门）| 0.153 s    | 含每门课的完整冷加载     |
| `course_summary()`         | 0.015 s    | 600 知识点 / 4000 作答   |
| `mistakes_center()`        | 0.010 s    | 单个学生                 |
| `knowledge_trace()`        | 0.002 s    | 知识点 → 证据 → 材料     |
| 单条作答                   | 10 条 SQL  | 2.78 ms                  |

这些数是**读数**，不是断言。断言是"条数为常数"，见上节。

---

## 五、10 项验收，逐条对应

| spec | 内容                      | 覆盖                                                        |
| ---- | ------------------------- | ----------------------------------------------------------- |
| 69.1 | 5×15×10 = 750 份材料      | `TestSemesterScale`（14 条）                                |
| 69.2 | 1000+ 知识点 / 100 主题   | 实测 3000 / 100                                             |
| 69.3 | 500 合成 + 1 真人风格学生 | 学号用**课程序号**生成；真人风格学生走完整每日流程并答错     |
| 69.4 | 5000 题 / 20000 作答      | 实测 5000 / 20003                                           |
| 69.5 | 10 次重启闭环             | `TestRestartCycles`（6 条）：指纹、计数、答案历史逐次不变   |
| 69.6 | 3 次备份 / 3 次恢复       | `TestBackupRestore`（5 条）：恢复到**另一个** data_dir      |
| 69.7 | 100 知识点溯源审计        | `TestTraceabilityAudit`（6 条）：全部解析到证据与本课材料   |
| 69.8 | 跨课程泄漏审计            | `TestCrossCourseLeakage`（9 条）：id 逐类互斥               |
| 69.9 | 数据库不得 N+1            | `TestNoNPlusOne`（5 条）+ `TestWriteAmplification`（6 条）  |
| 69.10| 10 个页面 UI 压力审计     | `TestUiStress`（2 条）+ `scripts/ui_stress_check.js`（12 页）|

合计 **60 条**。

UI 压力审计渲染 12 个页面函数（dashboard / today / my-courses / course /
session / knowledge / knowledge-detail / students / exercises / exercise /
mistakes / review），每页断言：不抛异常、HTML ≥ 200 字节、不含
`undefined`、不含 `NaN`。它跑在**真实 HTTP** 上（`ui_render_check.js`
是桩路由，这个不是）。

---

## 六、踩过的坑（都有对应守卫）

- **学号用 `course_id[:6]` 生成会撞车**：内容寻址的课程 id 前缀不保证唯一，
  第二门课的答案会指向第一门课的学生。改成用**课程序号** `c{i}`。
- **`for cid in course_ids:` 忘了 `enumerate`**：`course_index` 残留上一轮的
  值。两个坑合起来的表现是 `NotFoundError: student ... not found`。
- **同一秒内连做三次备份会撞 `DuplicateBackupError`**：归档名来自时钟。给
  每一轮注入一个不同的固定时钟值 —— 三轮必须是**三个**归档，不是一个文件
  被覆盖三次。
- **`create_backup()` 返回 `BackupResult`（字段 `archive_path`），
  `list_backups()` 返回 `BackupInfo`（字段 `path`）**：两个类型不是一个，
  按 `archive_path` 去 `BackupInfo` 上找会 `AttributeError`。
- **归档里的数据库叫 `database.sqlite`**，不是运行时的 `classroom.sqlite`。
- **API 端点 `/api/knowledge` `/api/students` `/api/exercises`
  `/api/students/{id}/mistakes` 都必须带 `course_id`**：不带是 400（用户
  输入错误），不是 500。
- **第一次写某张表时 sqlite 驱动会先探一次表结构**（`PRAGMA table_info`），
  把首条测量的语句数抬高 6 条左右。数 SQL 的测试必须先**预热**。
- **按 `course_id` 字典序 `enumerate` 会把课程序号对错位**：材料标签里的
  `c{i}` 是**创建顺序**，排序后每门课的知识点都"带着别门的标签"—— 假阳性。
- **`answer_id` 是内容寻址的**：只按 `i % n` 循环会让四元组重复，答案日志
  按 id 去重之后 20000 条只剩 1000 条 —— "20000 条作答"变成谎言。必须用
  `sequence = i // len(own)` 做成双射。
- **`IN (...)` 分批（500 一批）会让大库比小库多一两条语句**。这是 O(N/500)
  的常量级差异，不是 N+1；真正的 N+1 差值是 600 条。所以 N+1 判据用
  `abs(big - small) <= 2` 而不是 `==`，并把这个容差的理由写进常量注释。

---

## 七、门禁结果

```text
tests/test_stress_semester.py            60 passed            (10:39)
pytest (full)                            5125 passed, 10 skipped  (18:40)
compileall -q src tests                  exit 0
scripts/ui_audit.js                      275 checks  OK
scripts/ui_render_check.js               163 checks  OK
```

---

## 八、变更文件

**生产代码（全部是缺陷修复，没有为了压力测试而加的开关）**

- `src/application/workspace.py` —— `_flush_learning` 增量作用域；5 个调用
  点窄化；4 处冗余整课 flush 删除；`_load_course_state` 批量读学生日志
- `src/persistence/repositories/base.py` —— `CourseLinkRepository.rights_for_many`
- `src/persistence/repositories/knowledge.py` —— `load_structure` 批量取溯源链
- `src/persistence/repositories/student.py` —— `load_for_course` ×2、
  `existing_ids`、`rows_for`
- `src/persistence/snapshot.py` —— `load_student_logs`（批量读）、
  `save_student_log`（增量写）
- `src/application/persistence_wiring.py` —— `load_student_logs`

**测试**

- `tests/test_stress_semester.py`（新增，60 条）
- `scripts/ui_stress_check.js`（新增，真实 HTTP 的 12 页 UI 压力检查）
- `tests/test_persistence_transactions.py` —— 回滚测试从"数写了几条"改成
  "断言必须写到哪些落盘点"（次数会随实现变化，"这次操作必须写答案和评估"
  才是业务契约）

---

## 九、遗留说明

- 本报告里的耗时读数是**单次实测**，受本机负载影响；它们只用于说明量级，
  不是验收判据。验收判据是第三节的"语句条数为常数"。
- 整学期构建 124 秒会进入全量回归的总时长。这是刻意的：缩小规模就测不到
  本任务要测的东西。
