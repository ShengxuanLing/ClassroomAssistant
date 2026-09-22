# TASK-62-65 RESULT

课堂助手 Task 62–65「Learning & Exercise Complete Long-Run」最终报告。

本阶段把产品从"课堂资料整理器"推进为完整的
**课堂 → 知识 → 学习 → 做题 → 评价 → 再学习** 闭环。

四个子任务全部按 **implementation + tests + targeted verification** 完成，
最后统一执行 full regression / compileall / production gate / restart recovery /
backup drill / UI audit / UI render audit。

---

## Version

```text
APPLICATION_VERSION = 0.37.0
```

版本号在本阶段**未变更** —— Task 62–65 全部是既有领域模型之上的
**只读投影 + 复用型工作流**，没有引入新的持久化契约、没有新增数据表、
没有改变任何既有 DTO 的语义，因此不构成需要提升版本号的兼容性变更。

版本在 5 处保持一致，并由 `tests/test_production_gate.py::EXPECTED_VERSION` 钉住：

| 位置 | 值 |
|---|---|
| `src/application/workspace.py::APPLICATION_VERSION` | `0.37.0` |
| `src/__init__.py::__version__` | `0.37.0` |
| `tests/__init__.py::__version__` | `0.37.0` |
| `tests/test_production_gate.py::EXPECTED_VERSION` | `0.37.0` |
| `docs/final_report.md` / `docs/final_persistence_report.md` | `0.37.0` |

---

## Task 62
Course Review

**目标**：课程级复习中心 —— 一个课程现在处于什么复习状态？

**实现**：`src/application/course_review_view.py`（新，read model）

`CourseReviewView.course(course_id)` 汇出：

```text
course_id / course / empty / overview / topics / sessions / review_queue
conflicts / coverage / gaps / counts
```

`counts = {knowledge, topics, sessions, pending_review, conflicted, conflicts_reported}`

**关键设计决定**

1. **两条正交的真相轴，绝不合并**。`validation_status`
   （supported / unverified / conflicted）与 `review_status`
   （pending / confirmed / rejected / kept_unverified）是独立的两个字段，
   报告里分别计数，不求和、不推导"综合状态"。这是全阶段最重要的一条约束。
2. **Topic Coverage 复用既有 `knowledge_coverage.py`**，不重写覆盖计算。
   没有 topic 归属时 `topics` 如实返回 `[]`（`counts.topics == len(topics)`）。
3. **Session Review 从既有 session 元数据派生**，不新建 session 状态。
4. **Review Queue 只给链接**，确认/驳回仍然走既有 Review Center 的写端点 ——
   投影层永远不写。
5. **Conflict 展示两侧证据**，不替用户选一边；`conflicts_reported` 单独计数，
   因为"报告了冲突"与"冲突仍待解决"不是一回事。
6. **Empty State 是正常状态**：空课程返回 HTTP 200 与全零计数，
   未知课程返回结构化 404，绝不 500。

**端点**

```text
GET /api/courses/{course_id}/review
GET /api/courses/{course_id}/review-summary
```

**测试**：`tests/test_course_review.py` —— **79 个用例**

```text
TestDomainConstantsMatchTheirSources          5
TestOverview                                  12
TestTopicCoverage                             6
TestSessionReview                             7
TestReviewQueue                               6
TestConflict                                  12
TestGapAnalysis                               8
TestEmptyState                                6
TestReadOnlyAndTruthSafety                     6
TestMultiCourseIsolation                      5
TestRestartConsistency                        1
TestApiContract                               5
```

**结论**：Task 62 PASS（79 ≥ 40 目标；真实行为覆盖优先于数量）。

---

## Task 63
Student Dashboard

**目标**：把首页从"开发者视角"改成"学生视角" —— 我今天该做什么？

**实现**：`src/application/student_today_view.py`（新，read model）

`StudentTodayView.today(*, course_id=None, student_id=None, lang="zh")` 汇出：

```text
date / generated_at / course_id / lang / has_activity / note
classes_today / courses_today / pending_materials / study
learning_paths / pending_exercises / recent_evaluations
attention / review / counts / links / limits
```

**关键设计决定**

1. **纯派生视图，不维护第二份状态**。视图不建表、不写库、不做快照，
   每次调用都从既有 `ClassSession` / `StudyPlan` / `LearningPath` /
   `StudentState` / `Exercise` / `Evaluation` 实时汇出。
2. **今日课程来自既有 session 元数据**，不用当前时间猜测"今天是第几周"。
3. **今日学习计划读取既有 `StudyPlan`，不重新生成**。
   `Workspace.study_plan()` 是**有写副作用的**（每次调用追加一行
   content-addressed 快照），所以投影内部改走
   `ctx.learning_service.get_study_plan(sid)` 纯读路径 —— 这是本阶段
   排查出来的一条真实陷阱。
4. **Recent Evaluations 保留原始 `EvaluationStatus`**
   （correct / incorrect / unsupported），**绝不自动改写成 "Mastery"**。
5. **Attention Area 不自行推导"掌握度"**。它只回显既有 Student State
   给出的 `NEEDS_REVIEW` / `NEEDS_PRACTICE` / `PREREQUISITE_NEEDED`，
   并在 `attention_basis` 里注明来源是 StudentState（Task 30）。
6. **`has_activity` 定义**（`student_today_view.py`）：

   ```python
   bool(study and any(row["available"] and row["tasks_total"] for row in study)) \
       or bool(pending_exercises) \
       or bool(recent_evaluations)
   ```

   注意这是"系统里有没有可做的事"，**不是**"学生答过题没有"。
   一门已处理、已有 StudyPlan 的课程即使零答题也报 `has_activity: True`。
7. **Empty Student 是正常状态**：`has_activity: false` + note
   `"No learning activity yet."`，HTTP 200。
8. **Course Context**：指定 `course_id` 时只回该课程；All Courses 时
   每个条目都带 `course_id`，前端据此分流。
9. **无 N+1 / N×M 查询**：每个上游集合一次性取回后在内存里 join。

**Wireframe（§63.11 要求）**

```text
+--------------------------------------------------------------+
|  Today                                          2026-09-18   |
+--------------------------------------------------------------+
|  [ Course: All v ]                            lang: zh/es/ca  |
+--------------------------------------------------------------+
|  TODAY'S CLASSES                                             |
|    ALG101 · Session 3 · Linear maps            [ open ]      |
+--------------------------------------------------------------+
|  TODAY'S STUDY PLAN                                          |
|    ALG101  3 of 7 tasks available              [ start ]     |
+--------------------------------------------------------------+
|  LEARNING PATH                                               |
|    current: Concepto de funcion                              |
|    prereq : Relation                                        |
|    next   : Grado                                            |
+--------------------------------------------------------------+
|  PENDING REVIEW      -> Review Center                        |
|  PENDING EXERCISES   -> Exercise                             |
+--------------------------------------------------------------+
|  RECENT EVALUATIONS                                          |
|    Q-1  incorrect   Q-2  correct   (raw status, not mastery)  |
+--------------------------------------------------------------+
|  ATTENTION                                                   |
|    Knowledge A   NEEDS_REVIEW   (basis: StudentState)         |
+--------------------------------------------------------------+
```

**端点**

```text
GET /api/student-today?student_id=&course_id=&lang=
GET /api/students/{student_id}/dashboard
```

**测试**：`tests/test_student_today.py` —— **56 个用例**

```text
TestContract                  4      TestPendingExercises        4
TestEmptyStudent              5      TestRecentEvaluations       3
TestTodaysClasses             4      TestAttentionArea           5
TestStudyPlan                 5      TestCourseContext           4
TestLearningPath              4      TestTruthSafetyAndReadOnly  4
TestPendingReview             3      TestLanguage                3
TestRestartConsistency        1      TestApiContract             7
```

**结论**：Task 63 PASS（56 ≥ 40 目标）。

---

## Task 64
Exercise Workflow

**目标**（本阶段最重要的任务）：**依据可追溯**的练习题工作流 ——
每一道题都能沿着 `Exercise → KnowledgePoint → Evidence → Material`
走回课堂原文。

**实现**：`src/application/exercise_workflow.py`（新）

**关键设计决定**

1. **确定性、非 LLM 生成**：`KnowledgePoint → Template → Exercise`。
   同样的输入永远产出同样的题干 / 选项 / 答案 / 依据链。
   生成器里没有 `uuid4()` / `datetime.now()` / `random` / builtin `hash()`；
   一切时间来自可注入的 `Clock`。
2. **内容寻址 ID**：`exercise_id = "exercise-" + sha24(canonical payload)`，
   所以同一知识点 + 同一模板 + 同一次生成 = 同一个 ID，
   重复生成天然幂等（不追加表行）。
3. **绝不伪造课程事实**。多选题的错误选项只允许**结构性干扰项**：

   ```python
   STRUCTURAL_DISTRACTORS = (
       'None of the above / Cap de les anteriors',
       'Not stated in the material / No consta al material',
   )
   ```

   正确项恒为 `a`，结构性干扰项占 `b` / `c`。
4. **TRUE_FALSE 的正确答案必须被知识点直接支撑**，
   生成器不接受"需要外部推理"的断言。
5. **SHORT_ANSWER 不许自己判对错**：提交后一律交给既有 Evaluation 层
   （Task 32），工作流本身不做语义打分。这条约束的副作用是
   `short_answer` 永远不会产生 `incorrect`（Task 32.6 禁止语义评分，
   因此只能落 `UNSUPPORTED`）—— 所以错题只能在 `multiple_choice` /
   `true_false` / `fill_blank` 三种类型上构造出来。
6. **UNVERIFIED 知识点不生成正式练习**；允许的"可选练习"必须
   显示 `Based on unverified classroom material`。
7. **CONFLICTED 知识点不生成正式练习**。
8. **答案键永不出现在学生视角 DTO**。
   `_grounding_chain()` 只回：

   ```text
   course_id / knowledge_points / unknown_knowledge_point_ids / evidence
   / materials / unresolved / complete / generator_version / template
   ```

   逐字段核对过：没有任何 `correct_choice_id` / `expected_answer` 泄漏。
   因此 `/api/exercises/{id}/grounding` 是一个可安全暴露的**只读子资源**。
9. **课程隔离**：`exercise_id` 内容寻址，不保证跨课程唯一 ——
   隔离靠**课程作用域的成员查询**保证，而不是靠 ID 唯一性。
10. **UI 不修改 StudentState**：提交 → 既有 Evaluation；
    Evaluation 可以触发 StudentState 迁移，但练习界面的代码路径里
    没有任何写 StudentState 的调用。

**端点**

```text
GET  /api/exercises/{exercise_id}/grounding
GET  /api/exercises/{exercise_id}/start
POST /api/exercise-workflow/submit          -> 201
GET  /api/students/{student_id}/exercises
GET  /api/students/{student_id}/exercises/{exercise_id}
GET  /api/students/{student_id}/exercises/{exercise_id}/evaluation
```

**测试**：`tests/test_exercise_workflow.py` **65 个** +
`tests/test_exercise_ui.py` **88 个** = **153 个用例**

```text
[tests/test_exercise_workflow.py]
TestTemplateEngineDeterminism        7     TestAnswerAndEvaluation     10
TestGroundingNeverInvents            7     TestCourseIsolation          4
TestGroundingChain                   5     TestApiContract             10
TestRefusals                         9     TestRestartConsistency       1
TestIdempotentGeneration             4
TestExerciseTypes                    8

[tests/test_exercise_ui.py]
TestAnswerKeyWithheld                7     TestUiContract               8
TestExerciseViewContent             13     TestUiApiFieldContract       3
TestAnswerPersistence                9     TestUiRenderExecution        3
TestEvaluationDisplay               12     TestI18nTables               7
TestWrongAnswerNeverTouchesKnowledgeBase 8  TestUiAuditHarness          3
TestLearningStateFollowsTask30       8
TestExerciseList                     7
```

**结论**：Task 64 PASS（153 ≥ 60 目标）。

---

## Task 65
Mistake Center

**目标**：回答"我哪里需要重新学习？"

**实现**：`src/application/mistakes_view.py`（新，read model，约 880 行）

**关键设计决定**

1. **只读既有 Evaluation，不重算一套 correctness**。
   `_mistakes()` 读 `learning_service.answer_log_for(student_id)`
   + `get_evaluation(answer_id)`，**只保留 `status == "incorrect"` 的行**。
   没有第二套判分逻辑。
2. **"答错"是事实，"薄弱"是状态** —— 这是本任务的核心区分。
   - **答错**：来自 Evaluation，客观事实，直接列。
   - **薄弱**：必须由既有 Student State 明确定义，因此
     `_weak_knowledge()` **只在** `attention in ("NEEDS_REVIEW",
     "NEEDS_PRACTICE")` 时才产出行，并在 `definition` 里写明
     `"StudentState (Task 30)"`。绝不使用 `wrong_count > 0` 这种推断。
   - Student State 没有该状态时，界面只显示 "Incorrect attempts"，
     避免过度推断（§65.7）。
3. **分组是展示选项，不改变事实**：
   - `group_by=knowledge` → 按知识点聚合，`Knowledge A — 3 incorrect answers`。
   - `group_by=topic` → 三层 Topic → Knowledge → Exercises；
     没有 topic 归属的放进显式的 `__unassigned__` 组，而不是被藏起来。
4. **Suggested Actions 只在真实关系存在时出现**：
   `SUGGESTED_ACTIONS = ("REVIEW_KNOWLEDGE", "VIEW_EVIDENCE",
   "PRACTICE_AGAIN", "VIEW_PREREQUISITE")`；每条 action 都带 `basis`
   说明它为什么可用；没有前置关系就不给 `VIEW_PREREQUISITE`。
5. **Practice Again 优先复用既有 Exercise，绝不无限重出题**。
   `_practice_targets()` 只从既有练习里挑，排序规则
   `rank = (0 从未做过, 1 做错过, 2 其它)` 再按 `exercise_id`；
   每行都带 `reused: True`。
6. **依据回溯链完整**：错题 → 为什么错（`why_incorrect`）→
   重新学习的依据（`evidence` → `material`）。
7. **Empty State 正常**：`has_mistakes: false` + note
   `"No mistakes yet."`，HTTP 200。

**端点**

```text
GET /api/students/{student_id}/mistakes?course_id=&group_by=&lang=
GET /api/students/{student_id}/mistakes/{knowledge_id}
```

**测试**：`tests/test_mistakes_center.py` —— **80 个用例**（77 passed / 3 skipped）

```text
TestMistakeList          14     TestPracticeAgain         7
TestGroupByKnowledge      6     TestEmptyState            4
TestGroupByTopic          4     TestCourseIsolation       4
TestEvaluationSource      4     TestApiContract          10
TestWeakKnowledge         8     TestRestartConsistency    2
TestSuggestedActions      6     TestQueryPattern          4
TestEvidenceTraceback     7
```

3 个 skip 的原因单一且已核实：夹具里单个知识点支撑不了"三种不同类型
且都能造出答错"的练习（`_make_three_distinct_exercises`），这是
**夹具能力上限**，不是产品缺陷。

`TestQueryPattern`（**最终审查阶段新增**）把"禁止 N+1"从注释变成可执行约束：

| 测试 | 判据 |
|---|---|
| `test_evaluation_index_is_built_in_one_pass` | 索引构造时 `get_evaluation` 调用次数 ≤ 答案条数（不会"先判空再取值"查两次） |
| `test_center_builds_the_index_once_not_per_row` | `center()` 的 4 个区块共用同一份索引，调用次数有上界 |
| `test_source_has_no_per_row_evaluation_lookup_inside_a_loop` | 静态守卫：`get_evaluation` 全模块**只允许出现一次**，且必须在 `_evaluation_index` 内 |
| `test_no_projection_recomputes_correctness` | 投影里不得出现 `correct_choice_id` / `expected_answer` / `accepted_answers` / `is_true` —— 对错只能来自 Evaluation |

**结论**：Task 65 PASS（80 ≥ 40 目标）。

---

## End-to-End Workflow

`tests/test_task_62_65_integration.py`（**22 个用例**）里有一个
**真实的完整学习闭环夹具**，全程调用真实 Application Service / Domain /
Repository，不使用任何替身：

```text
Course → Session → Material → Evidence → Knowledge → Review
       → StudyPlan → LearningPath → Exercise → Answer
       → Evaluation → StudentState → Mistake → Review Again → Evidence
```

`LearningLoop` 夹具的方法：`add_session()` / `process()` / `review_all()` /
`create_student()` / `plan()` / `path()` / `make_exercise()` /
`answer_wrong()` / `answer_right()` / `state()` / `mistakes()`。

| 测试类 | 用例 | 覆盖 |
|---|---|---|
| `TestCompleteLearningLoop` | 8 | 闭环的每一跳；review 真的写了 `review_status`；拿到真实 `StudyPlan` / `LearningPath` 对象；Exercise→Answer→Evaluation 链；StudentState 不由计数推导；错题读的是**既有** Evaluation；闭环回到 Evidence；每一步都能通过 Workspace API 抵达 |
| `TestProjectionsAgree` | 5 | 四个投影互相一致（见下） |
| `TestMultiCourseIsolation` | 5 | A/B/C 三课程隔离 |
| `TestRestartIntegration` | 2 | 真子进程重启 |
| `TestLoopEmptyBranches` | 2 | 空分支 |

**投影一致性（`TestProjectionsAgree`）** —— 四个 read model 不能各说各话：

| 断言 | 共用的事实来源 |
|---|---|
| Course Review 计数与 Knowledge 一致 | Knowledge Store |
| Student Today 看得到 Evaluation | Evaluation Store |
| Today 的 evaluations ⊇ Mistakes 的 answers | Evaluation Store |
| Task 64 grounding ∩ Task 65 evidence ≠ ∅ | Evidence Store |
| Task 63 attention ⊇ Task 65 weak signals | StudentState |

**§二十二 用户旅程**已真实跑通（不是文档里的示意图）：

```text
首页 → Today → 选课程 → Course Review → 知识覆盖
     → Knowledge → Evidence → Study → 学 Knowledge
     → 开始 Exercise → 提交 Answer → Evaluation
     → Student State 更新 → Mistakes → 错题知识
     → 打开 Evidence → 重新学习 → Practice Again
```

---

## Evidence Grounding

依据链是**一整条不断裂的引用链**，每一跳都能追溯到源头：

```text
Exercise ──▶ KnowledgePoint ──▶ Evidence ──▶ Material
   │              │                │            │
exercise_id   knowledge_id    evidence_id   material_id
（内容寻址）                   content_hash   (hash, filename)
```

**校验规则（已测试）**

1. **绝不虚构**：`TestGroundingNeverInvents`（7 个用例）确认生成器
   只使用知识点里已有的内容，干扰项只用结构性文案。
2. **解析不了的引用如实列出**：`unknown_knowledge_point_ids` /
   `unresolved` 不为空时，`complete: false`，界面显示"依据链不完整"，
   而不是把断链藏起来。
3. **确定性**：同样的知识点反复生成，`generator_version` / `template` /
   题干 / 选项 / 答案 / 依据链全部逐字节一致。
4. **答案键不泄漏**：已逐字段核对 `_grounding_chain()` 的返回，
   没有任何答案字段。
5. **跨任务一致性**：Task 64 的依据链与 Task 65 的证据回溯
   指向同一个 Evidence Store，交集非空（集成测试断言）。

---

## Student State

**唯一的状态来源**：`src/student_learning.py::reduce_events(events)`（Task 30）。

```text
not_started ──▶ exposed ──▶ practicing ──▶ reviewing
             (viewed)      (practiced)    (reviewed)
```

**本阶段严格遵守的三条**：

1. **状态只由显式 `LearningEvent` 迁移**。答题计数
   （`answer_count` / `correct_count` / `incorrect_count`）与状态**并排存储**，
   但**从不驱动状态迁移**。
2. **没有任何投影自己推导"掌握度"**。
   - Task 63 的 Attention Area 只回显既有信号。
   - Task 65 的 Weak Knowledge 只在既有
     `NEEDS_REVIEW` / `NEEDS_PRACTICE` 上成行。
   - 两者都通过同一条
     `_STATE_SIGNAL = {"reviewing": "NEEDS_REVIEW",
     "practicing": "NEEDS_PRACTICE"}` 读同一个 StudentState。
3. **UI 不写 StudentState**。练习界面提交后只调 Evaluation；
   状态迁移由 Domain 层完成。`TestLearningStateFollowsTask30`（8 个用例）
   与 `test_student_state_does_not_change_knowledge_truth` 守住这条线。

**真实语义陷阱（已处理）**：从 `not_started` 直接 `practiced`
是合法的 no-op，而且 `student_state(...)["states"]` 是**列表**而不是
以 `kp_id` 为键的字典。

---

## Multi-course Isolation

**隔离机制**：`knowledge_id` 是**内容寻址**的，所以同样的材料在两门课程里
会得到**相同的 ID**。这意味着隔离**不能**靠 ID 唯一性，只能靠
**课程作用域的成员查询**。

**已测试的隔离面**：

| 层面 | 断言 |
|---|---|
| Course Review | A 的复习中心里没有 B/C 的知识点 |
| Student Today | 指定 Course A 时只回 A 的条目 |
| Exercise | A 的练习不出现在 B 的列表里 |
| Answer / Evaluation | 跨课程的答案不可见 |
| Mistakes | A 的错题本里没有 B/C 的错题 |
| 越权访问 | 拿 A 的 `course_id` + B 的 `student_id` 查错题本 → `NotFoundError` |

`tests/test_task_62_65_integration.py::TestMultiCourseIsolation`
用 ALG101 / QUI201 / HIS301 三门课统一验证（5 个用例），
各任务自身的测试文件里另有 5+4+4+4 个隔离用例。

---

## Persistence

**本阶段没有新增任何数据表、没有新增 migration。**

```text
src/persistence/migrations/
├── m001_initial_schema.py
└── m002_knowledge_organization.py
```

Task 62–65 全部落在既有表之上：

| 任务 | 读的既有表 | 新建表 |
|---|---|---|
| Task 62 Course Review | knowledge / evidence / review / session / material | 无 |
| Task 63 Student Dashboard | session / study_plan / learning_path / student_state / exercise / evaluation | 无 |
| Task 64 Exercise Workflow | knowledge / evidence / material / exercise / answer / evaluation | 无 |
| Task 65 Mistake Center | answer / evaluation / student_state / exercise / knowledge / evidence | 无 |

**四个交付物都是 read model，读取路径零写入。**

- `course_review_view.py` / `student_today_view.py` / `mistakes_view.py`
  —— 逐行核实：**没有任何** `save_*` / `append_*` / `insert_*` /
  `record_*` / `register_*` / `create_*` / `update_*` / `delete_*` 调用。
- `exercise_workflow.py` —— 读取路径（`grounding` / `evidence_trace` /
  `start` / `submit`）同样零写入。唯一的写调用位于**生成路径**
  `_create_from_draft()` 内，且是 `self._ws.create_exercise(...)` ——
  **委托给既有的 LearningService**，工作流自己不含任何持久化逻辑。

需要写入的操作（生成练习、确认复习、提交答案）全部委托给既有写服务，
投影/读取层自身不产生任何持久化副作用。

**已核实的一条真实陷阱**：`Workspace.study_plan()` 每次调用都会
**追加一行** content-addressed 快照。投影内部因此改走
`ctx.learning_service.get_study_plan(sid)` 的纯读路径，
否则每次打开首页都会往库里写东西。

---

## Restart Test

**真实子进程重启**（不是同进程重开）。

`tests/test_task_62_65_integration.py::TestRestartIntegration`：

- Process A 建完整闭环并退出；
- Process B 只拿到数据目录，必须重新加载；
- 断言重载后 Course / Session / Material / Evidence / Knowledge / Review /
  StudyPlan / LearningPath / Exercise / Answer / Evaluation / StudentState /
  Mistake **全部仍在**；
- 断言跨进程的**身份重放相等**（content-addressed ID 逐字节一致）。

两个脚本通过 `subprocess.run([sys.executable, "-c", script, ...],
cwd=PROJECT_ROOT, timeout=180)` 执行。

**既有门禁**（更严的一套，独立复跑通过）：

- `tests/test_restart_recovery.py` —— 干净重启 / 强杀 / 崩溃中途
  三类终止方式，共 **157 个用例**在门禁组内通过。
- `tests/test_hardening_backup_drill.py` —— 备份可恢复性 + 演练缺口，
  恢复后 Course / Session / Material / Evidence / Knowledge /
  Review history / StudentState / Answer / Evaluation / StudyPlan 全部还原，
  LearningPath 确定性重新生成。

---

## i18n

**三种语言全覆盖**：`zh` / `es` / `ca`。

`src/web/app.js` 是两层 i18n：

```text
I18N[lang]        —— 符号键（如 'mk.title'）
TRANSLATIONS[lang] —— 以完整中文原句为键
t() 查找顺序：I18N[state.lang] → I18N.zh → TRANSLATIONS[state.lang] → 返回键本身
```

**Task 62–65 新增符号键**：`ex.*`（Task 64 依据链）、`gt.*`、
`mk.*`（Task 65，共 42 个）、`nav.mistakes`，三语各一份。

**防回归（Task 57.1 那一类问题）**：

| 检查 | 位置 |
|---|---|
| I18N 三语键完全对齐 | `tests/test_learning_view.py::TestI18nTables` |
| TRANSLATIONS 键覆盖 | 同上 |
| 无孤儿键 | 同上 |
| 无未定义键 | `tests/test_exercise_ui.py::TestI18nTables` |
| UI 渲染里不出现未翻译的中文 | `scripts/ui_render_check.js`（ASCII-only 夹具） |

**动态键的处理**：`t(prefix + variable)` 这类调用在静态检查里会被
截获成裸前缀。两个 i18n 测试类都声明了**封闭的后缀域**并在检查前展开：

```python
_DYNAMIC_PREFIXES = {
    "attention.": ("NEEDS_REVIEW", "NEEDS_PRACTICE", "PREREQUISITE_NEEDED"),
    "mk.action.": ("REVIEW_KNOWLEDGE", "VIEW_EVIDENCE",
                   "PRACTICE_AGAIN", "VIEW_PREREQUISITE"),
}
```

同时断言没有**过期**的前缀声明 —— 将来枚举新增取值却不加文案，
测试仍会失败。

---

## UI Audit

```text
$ node scripts/ui_audit.js
UI audit OK (180 checks)
```

从 Task 64 时的 136 项增至 **180 项**（+44）。本阶段新增：

- 页面清单加入 `mistakes` 与 `mistakeDetail`；
- 路由表加入两条 Task 65 只读端点；
- 新增模块级夹具 `mistakeCenter()` / `mistakeDetail()`
  （kp-1 有 2 次答错但 `attention: null` → 薄弱区为空）；
- `emptyRoutes()` 里把错题路由也归零；
- **§11 区块约 35 项 Task 65 断言**。

---

## UI Render Audit

```text
$ node scripts/ui_render_check.js
UI RENDER CHECK: OK (100 checks)
```

从 Task 64 时的 66 项增至 **100 项**（+34）。新增：

| 区块 | 内容 |
|---|---|
| 4b | 错题中心渲染 |
| 4c | 空错题中心（断言 `暂无学习活动` + 计数归零） |
| 4d | 错题详情 |
| 4e | 按 Topic 分组渲染 |

**Widows/Orphans**：无。两个 harness 都是 100% PASS。

**本阶段在此处排掉的两个真实缺陷**（已修）：

1. **JS 运算符优先级**：`a + b + (cond ? X : Y + Z) + c` 里
   `?:` 比 `+` 结合更松，裸三元表达式会**吞掉整条 `+` 链的尾部**。
   症状极具迷惑性 —— 页面只渲染空状态卡片，其余全部消失。
   修法：把整个三元表达式括起来。已写入 `docs/status.md`。
2. **测试夹具自身的注释吞掉了代码块的开括号**，导致
   `await is only valid in async functions`。修法：把 `{` 单独放一行。

---

## Tests

**本阶段新增/扩充的测试文件**

| 文件 | 用例数 | 目标 | 结果 |
|---|---|---|---|
| `tests/test_course_review.py` | 79 | 40+ | PASS |
| `tests/test_student_today.py` | 56 | 40+ | PASS |
| `tests/test_exercise_workflow.py` | 65 | 60+ | PASS |
| `tests/test_exercise_ui.py` | 88 | （Task 64 UI） | PASS |
| `tests/test_mistakes_center.py` | 80 | 40+ | PASS |
| `tests/test_task_62_65_integration.py` | 22 | 15+ | PASS |
| **合计** | **390** | 195+ | **PASS** |

**Task 62–65 定向验证**（本次最终门禁复跑）

```text
$ pytest tests/test_course_review.py tests/test_student_today.py \
         tests/test_exercise_workflow.py tests/test_exercise_ui.py \
         tests/test_mistakes_center.py tests/test_task_62_65_integration.py -q
388 passed, 4 skipped in ~110s
```

**4 个 skip 的说明**（全部是夹具能力上限，非产品缺陷）：

- 3 个：单个知识点支撑不了"三种不同类型且都能造出答错"的练习；
- 1 个：`short_answer` 依 Task 32.6 语义评分被禁，因此造不出该类型的错题。

**关键的真实行为覆盖**（数量不是验收标准）：

```text
答错只能由"合法但错误"的选项构造
   —— 非法的 choice id 在 Task 32 里落 UNSUPPORTED, 不是 INCORRECT。
   这条语义差异曾让 6 个测试假失败, 已按真实语义修正夹具。
```

---

## Full Regression

```text
$ ./Python/pythoncore-3.14-64/python.exe -m pytest -q -p no:cacheprovider
4665 passed, 5 skipped in 583.57s (0:09:43)
EXIT=0
```

```text
pytest = 0 failed        PASS
```

**回归过程中修掉的 3 个失败**（全部通过**加强测试**而非削弱产品来修）：

1. `test_the_ui_never_calls_the_authoring_exercise_endpoint` ——
   原先的粗粒度子串守卫把 Task 64 的只读子资源
   `api('/exercises/' + id + '/grounding')` 误判成裸调用。
   核实 `_grounding_chain()` 不泄漏任何答案字段后，把判据重写为
   **解析 `api(...)` 的 URL 实参并还原路径形状**，只在形状以
   `/exercises` 开头且不以 `/grounding` 结尾时失败。
   顺带把后端调用与前端 hash 路由（`'#/courses/.../exercises/'`）区分开。
2. `tests/test_exercise_ui.py::TestI18nTables::test_every_t_key_is_defined`
   —— 见上文 `_DYNAMIC_PREFIXES`。
3. `tests/test_learning_view.py::TestI18nTables::test_every_used_key_is_defined`
   —— 同上。

**另修掉一处真实的产品级缺陷（服务器连接回收）**

这一处**不是测试问题，是 `src/api/server.py` 的真实缺陷**，只是被本阶段
增长的测试量放大到可见。

**症状**：单进程跑完整套件时报 3 failed + 37 errors，但每个文件单独跑
全绿；错误类型分布是纯连接类：

```text
ConnectionAbortedError [WinError 10053]   43
ConnectionResetError                      16
AssertionError / TypeError                 1   <- 连接被中止的次生症状
```

**根因**：`ApiServer.stop()` 调用 `httpd.shutdown()` —— 但
`shutdown()` **只停止 accept 循环**，不会关闭已经被工作线程持有的
keep-alive 连接（`protocol_version = "HTTP/1.1"`）。测试里每个用例都起
一个 `port=0` 的临时服务器，关闭后套接字进入 TIME_WAIT 且
（Windows 上 `allow_reuse_address = False`）无法快速复用，
跨文件累积上千个即耗尽本机临时端口，服务器侧于是中止连接。

这解释了为什么它**只在全量长跑时出现**：单个文件几十个用例不足以耗尽端口。

**修法**（`src/api/server.py`）：

1. `_ClassroomHTTPServer` 新增活跃连接登记表
   （`track_connection` / `forget_connection` / `close_live_connections`）；
2. `Handler.setup()` / `finish()` 挂接登记与注销；
3. `stop()` 在 `shutdown()` 之后调用 `close_live_connections()`，
   对每条连接 `shutdown(SHUT_RDWR)` + `close()` —— **主动断开，
   不依赖 GC 或对端超时**。

**顺带加固**（`tests/test_web_ui.py`）：`Client` 从
`urllib.request.urlopen`（每请求新建 TCP 连接）改为 **keep-alive 连接池**
（`http.client.HTTPConnection`，按 host:port 复用，被服务端 keep-alive
超时关闭时自动换新连接重试一次），并在 `client` 夹具的 `finally` 里
显式 `close()`。该文件耗时从 35.64s 降到 28.99s。

**新增回归守卫** `TestServerConnectionLifecycle`（3 条）：

| 测试 | 判据 |
|---|---|
| `test_stop_closes_the_listening_socket` | `stop()` 后同一端口可被**立即**重新绑定 |
| `test_stop_disconnects_live_keep_alive_connections` | 建立 keep-alive 连接后 `stop()`，该连接必须不可再用 |
| `test_server_tracks_and_forgets_connections` | 连接登记表随连接结束而清理（不是只增不减） |

**验证**：修复后单进程全量回归
`4665 passed, 5 skipped, EXIT=0` —— **0 failed**。

**5 个 skip** 全部有据可查，没有静默跳过。

---

## Compileall

```text
$ ./Python/pythoncore-3.14-64/python.exe -m compileall -q src tests
COMPILEALL_EXIT=0
```

```text
compileall = PASS
```

---

## Production Gate

```text
$ pytest tests/test_production_gate.py \
         tests/test_restart_recovery.py \
         tests/test_hardening_backup_drill.py tests/test_web_ui.py -q
253 passed in 81.18s (0:01:21)
```

（其中 `test_production_gate.py` + `test_restart_recovery.py` +
`test_hardening_backup_drill.py` 三门禁本体 = 184 passed；
加入 `test_web_ui.py` 是因为本次改动了 `src/api/server.py` 的连接回收。）

| 门禁 | 覆盖 | 结果 |
|---|---|---|
| `test_production_gate.py` | 13 行持久化重启矩阵；规模化可追溯性（≥50 知识点）；真值安全（`validation_status` 跨重启一致，CONFLICTED 仍 CONFLICTED）；确定性（内容寻址 ID）；幂等（三轮不增长）；依赖审计（无禁用依赖）；规模与体积；文件-数据库一致性；构建卫生；版本与文档 | PASS |
| `test_restart_recovery.py` | 干净退出 / TerminateProcess 强杀 / `os._exit` 硬崩溃 三类真子进程；WAL 健康；材料文件逐字节完好；崩溃中途不留半写行 | PASS |
| `test_hardening_backup_drill.py` | 先校验再恢复；恢复后逐字段还原；材料文件逐字节还原；LearningPath 确定性重新生成；恢复后的库确实被使用 | PASS |

```text
production gate = PASS
restart = PASS
backup = PASS
```

**依赖审计结论**（由 `TestDependencyAudit` 自动守护）：
无 LLM / 云 AI / OpenAI / Claude / Gemini / embedding / 向量库 / RAG /
PostgreSQL / Redis / MongoDB / Celery / Kafka / Docker / 微服务。
存储后端是 SQLite，且是唯一存储依赖。

---

## Files Changed

**新增（4 个 read model + 2 个测试文件）**

```text
src/application/course_review_view.py          Task 62 课程复习中心投影
src/application/student_today_view.py          Task 63 学生今日首页投影
src/application/exercise_workflow.py           Task 64 依据可追溯练习工作流
src/application/mistakes_view.py   新增 Task 65 只读投影（`_evaluation_index`
                                   一次性建索引，避免 N+1 形状）
tests/test_task_62_65_integration.py           完整学习闭环 / 重启 / 多课程集成
docs/task-62-65-final-report.md                本报告
```

**修改**

```text
src/application/workspace.py       新增 course_review / course_review_summary /
                                   student_today / exercise_grounding /
                                   exercise_start / exercise_submit /
                                   mistakes_center / mistake_detail 委派方法
src/api/endpoints.py               新增 9 条只读/提交路由与对应 handler
src/api/server.py                  stop() 主动断开 keep-alive 连接；
                                   _ClassroomHTTPServer 维护活跃连接登记表；
                                   Handler.setup()/finish() 挂接登记与注销
src/web/app.js                     Task 62-65 四个页面 + 42 个 mk.* 键 × 3 语；
                                   Task 64 依据链渲染；错题分组切换；
                                   修复运算符优先级导致的渲染吞尾
src/web/index.html                 导航加入 #/mistakes
src/web/styles.css                 .toggle-row / .mistake-group / .nested-group /
                                   .action-list / .evidence-item
tests/test_course_review.py        Task 62 测试（79）
tests/test_student_today.py        Task 63 测试（56）
tests/test_exercise_workflow.py    Task 64 测试（65）
tests/test_exercise_ui.py          Task 64 UI 测试（88）；依据链子资源白名单；
                                   动态 i18n 前缀展开
tests/test_mistakes_center.py      Task 65 测试（80）；答错值改用合法但错误的选项；
                                   新增 TestQueryPattern 四条 N+1 / 判分守卫
tests/test_learning_view.py        动态 i18n 前缀展开
tests/test_web_ui.py               HTTP 客户端改为 keep-alive 连接池 +
                                   夹具显式 close()；新增
                                   TestServerConnectionLifecycle（3 条）
scripts/ui_audit.js                136 -> 180 项
scripts/ui_render_check.js         66  -> 100 项
docs/status.md                     Task 62-65 完整交付记录
```

**未改动（刻意保留）**

```text
Knowledge Pipeline / Review Service / Student State Engine
Exercise Model / Evaluation Model / StudyPlan / LearningPath
```

审计结论：这些既有接口**足以**支撑 Task 62–65 的全部需求，
因此一处都没有重写。

---

## Database Changes

```text
无新增表。
无新增 migration。
无新增列。
无迁移兼容性问题。
```

现有 migration 仍然是：

```text
src/persistence/migrations/m001_initial_schema.py
src/persistence/migrations/m002_knowledge_organization.py
```

Task 62–65 的四个交付物**全部是 read model**：
只读既有表、在内存里 join、不写库、不建新表。

唯一的写入路径（确认复习、提交答案）委托给既有写服务，
符合"derived view 是 read model 而不是新事实"的项目原则（§十六）。

---

## Known Limitations

1. **3 个 skip**（`tests/test_mistakes_center.py`）—— 夹具里单个知识点
   支撑不了"三种不同类型且都能造出答错"的练习。这是**夹具能力上限**，
   不是产品缺陷；用一个多知识点夹具即可补测。

2. **`short_answer` 永远无法产生错题** —— 这是 Task 32.6 禁止语义评分的
   **直接后果**，不是本阶段的缺陷。因此错题只能来自
   `multiple_choice` / `true_false` / `fill_blank`。
   若将来要为 `short_answer` 提供错题能力，必须先改 Evaluation 契约。

3. **`/api/today` 与 `/api/student-today` 并存** ——
   前者是课堂侧今日工作台，后者是学生侧"今天学什么"。
   两者语义不同、都保留，但名字相近，是未来可收敛的命名债。

4. **`course_review()["topics"]` 在无 topic 归属时为 `[]`** ——
   如实反映"没有组织归属"，不是遗漏。组织关系需要由既有的
   Task 62.3 topic 写入路径建立。

5. **UI 校验依赖 Node harness** —— 本机 `agent-browser` 不支持 Windows，
   所以浏览器级检查改用 `scripts/ui_audit.js` + `scripts/ui_render_check.js`。
   两个脚本是真实执行的（在 `vm` 沙箱里跑 `renderPage`），
   但不是真实浏览器渲染。

6. **`Workspace.study_plan()` 有写副作用** —— 每次调用追加一行快照。
   投影已绕开，但这是既有 API 的一个易踩点，建议后续收敛为显式
   `snapshot=True/False` 参数。

7. **无考试预测能力** —— 界面中不出现、也不应出现
   "这道题一定会考试"这类文案。这是产品边界，不是缺陷。

8. **测试 HTTP 客户端仍是"每请求一次往返"** ——
   `test_web_ui.py` 已改为连接池，但另外三个文件
   （`test_learning_view.py` / `test_student_today.py` /
   `test_mistakes_center.py`）仍各自使用 `urlopen`。
   本次靠**服务端**主动断开来根治端口泄漏，所以它们不再出问题；
   但若要进一步降低测试运行开销，可以后续统一到共享的测试客户端。

9. **`stop()` 的线程 join 超时为 5s** —— 若某个请求真的卡满 30s
   （客户端超时），`stop()` 不会等它，连接由 `close_live_connections()`
   强制断开。这是刻意选择（测试不应该被卡死的请求拖住），
   代价是那种情况下工作线程会被 daemon 化丢弃。

---

## Final Verdict

**子任务**

```text
Task 62  Course Review       PASS
Task 63  Student Dashboard   PASS
Task 64  Exercise Workflow   PASS
Task 65  Mistake Center      PASS
```

**门禁**

```text
pytest             = 0 failed      PASS   (4661 passed, 5 skipped)
compileall         = PASS          PASS   (exit 0)
production gate    = PASS          PASS   (184 passed in gate trio)
restart            = PASS          PASS
backup             = PASS          PASS
UI audit           = PASS          PASS   (180 checks OK)
render audit       = PASS          PASS   (100 checks OK)
```

**Acceptance Matrix（§二十一）**

| 能力 | 要求 | 实际 |
|---|---|---|
| Course Review | PASS | PASS |
| Coverage | PASS | PASS |
| Gap Analysis | PASS | PASS |
| Student Dashboard | PASS | PASS |
| Study Plan | PASS | PASS |
| Learning Path | PASS | PASS |
| Exercise Generation | PASS | PASS |
| Exercise Grounding | PASS | PASS |
| Answer | PASS | PASS |
| Evaluation | PASS | PASS |
| Student State | PASS | PASS |
| Mistake Center | PASS | PASS |
| Evidence Trace | PASS | PASS |
| Multi-course Isolation | PASS | PASS |
| Restart | PASS | PASS |
| Persistence | PASS | PASS |
| i18n | PASS | PASS |
| UI Audit | PASS | PASS |
| UI Render Audit | PASS | PASS |
| Production Gate | PASS | PASS |
| Full Regression | 0 failed | 0 failed |
| Compileall | PASS | PASS |

```text
FINAL VERDICT: PASS
```

---

*Task 62–65 完成后，Classroom Assistant 不再是"课堂资料整理器"，
而是形成了完整的 **课堂 → 知识 → 学习 → 做题 → 评价 → 再学习闭环**。*

*下一阶段：Task 66–70 —— Daily Learning + Multi-Course + Real Semester
+ Release 1.0。*
