# 任务计划书：下线「记忆卡片」· 修「无学生」死路 · 清点无入口页面（TASK-106 / 107 / 108）

- 版本：**v2**（2026-10-13；v1 的全部决策点已与用户逐条拍板 → 见 §0.1 决策台账）
- 提出人：用户（凌总）
- 状态：**TASK-106 ✅ 已完成**（提交 `6ef7bee`）；**TASK-107 / TASK-108 待实施**
- 规模：**M**（跨前端 + 后端 + 持久化 + 迁移 + 守卫脚本，三个任务可独立交付）
- 风险：**中**（TASK-106 的不可逆数据决策**已执行并兜住**，见 §12.6；TASK-107 未复现前不得改代码）
- 决策：**D1–D10 全部已拍板**（§0.1）—— 实施者**不要再问**，按"结论"列执行

> **本计划书的一句话**：三件事互相独立，可以分三次交付 ——  
> ① 把一个**没有实际用途**的功能（记忆卡片）从产品面到持久化层**整条**拆掉（✅ 已完成）；  
> ② 修掉「该课程暂无学生 → 请先添加学生 → 但全站没有添加入口」这条**死路**；  
> ③ 把「哪些页面/端点没有入口」**一次性查清楚并列表**，按 §0.1 的 **D6** 逐条处置。

---

## 0. 摘要（给只想读一页的人）

用户三句原话 → 三个任务：

| 用户原话                                                  | 任务           | 我的核查结论                                                                                                                                                                                                                                                                           |
| ----------------------------------------------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 「删掉知识卡片这个功能，没有实际用途」                                   | **TASK-106** | 功能完整存在（前端页 + 4 个 API + 仓储 + 表 + 迁移 + FSRS 调度器 + 测试 + 审计）。**入口只有 1 处**（知识地图副标题里一个链接），确实接近"没有实际用途"。**✅ 已按方案 A 拆完**（用户拍板删表，见 §0.1 D1 / §12.6）。                                                                                                                                                 |
| 「为什么有时候还会跳转到之前已经删除的学生页面？还显示没有学生？如果删不了或者不适合删就默认有一个学生啊」 | **TASK-107** | **不存在 `#/students` 路由残留**（已 grep 证明，且有 2 条反向守卫钉着）。**根因已用数据确证**：库里 5 门课**只有 1 门有学生**，另外 **4 门课 0 学生** ⇒ 切到那 4 门课，「练习 / 错题本 / 考前复习 / 今日学习」**必然**渲染空态，文案写着「**先为这门课添加学生**」，但**全站没有任何添加入口** —— `POST /api/students` 后端注册着（`endpoints.py:917`），前端**零调用**。所谓"有时候"= "切到那 4 门课的时候"（§4.1）。**方案已定为 P-A 默认学生**（§0.1 D2）。 |
| 「现在项目有很多隐藏的没有入口的页面和功能，这挺离谱的」                          | **TASK-108** | 已用**前缀可达性求解**跑出第一版清单：**16 个后端端点在前端无任何消费者**；路由入口矩阵里 **2 个页面只有 1 个入口且不在顶栏**（`#/flashcards` 已随 TASK-106 消失）。本任务先出**只读审查报告 + 处置清单**；**处置方式已按 §0.1 D6 定死**（默认标 `deprecated` 保留）。                                                                                                          |

**三条纪律（贯穿全篇）**：

1. **不跑全量回归**（用户 2026-10-10 明确要求）。只跑受影响的定向测试 + 两个前端审计。
2. **不可逆的操作（删表/删数据）先报告再动手**，不许"顺手删干净"。
3. **TASK-107 必须先复现再修**。用户说"有时候"，没有复现步骤就没有修好的判据。

### 0.1 决策台账（**全部已拍板，实施者不要再问**）

2026-10-13 与用户逐条确认完毕。**实施者按此表执行，不要重新论证方案**。

| 编号 | 决策点 | 结论 | 备注 |
| --- | --- | --- | --- |
| **D1** | §3.1 记忆卡片删到什么深度 | **方案 A**：代码全删 + `m005` `DROP TABLE` 两张表 | ✅ **已执行完毕**，见 §12.6（提交 `6ef7bee`） |
| **D2** | §4.2 「无学生」走哪个方案 | **P-A 纯默认学生**：0 学生时自动建一个默认学生 | **P-B 不采纳** —— 不补回「添加学生」表单；P-C 不采纳 |
| **D3** | §4.3-C 默认学生的显示名 | **C1**：`display_name` 固定为 `"Me"`（**与界面语言无关**）；`#/learn` / `#/review` 页头改为优先显示 `display_name` | 不要把界面语言写进数据 |
| **D4** | §4.3-D 旧深链兜底 | 落点 = **`#/courses`（我的课程）**；范围 = **`#/students*` 与 `#/flashcards` 都重定向** | `#/flashcards` 是 TASK-106 之后新增的死路由 |
| **D5** | §5.3-4 `views/students.js` 改名 | **批准**：改名 `views/shared.js`，同步 6 处清单 | 纯改名，无行为变化 |
| **D6** | §5.3-5 16 条无消费者端点 | **默认标 `deprecated` 保留**：加代码注释 + 测试白名单（附理由） | 只在确认能力已被另一端点**完全覆盖**时才下线 |
| **D7** | §5.3-6 `PATCH /api/courses/{id}` 无入口 | **批准加入口**：课程页加「课程改名」按钮 | 现在建了课程就改不了名 |
| **D8** | §5.4 新增守卫 | **3 条全部批准** | TASK-108 的真正产出 |
| **D9** | §5.1-2 审查脚本去向 | **留成 `scripts/audit_orphans.py`**（正式入仓，只读可重跑） | 新守卫要长期靠它复现结论 |
| **D10** | §5.3-2 `#/courses/<id>/review` 补入口 | **❌ 不做，转待办 B1** | 用户原话：「审核这部分我也想改，这个以后再说，记待办，目前任务做完了我们再商量」 |

**待办（不属 TASK-106/107/108，另起一轮）**

- **B1 —— 课程级审核（`#/courses/<id>/review`）整体改版。** 用户明确说「审核这部分我也想改」。
  本计划书**只登记现状**（§2.3 的入口矩阵、§5.3-2），**本轮一行都不动**；
  等 TASK-107 / TASK-108 落地后单开一份任务书。

---

## 1. 需求（用户原话 → 可验收的表述）

| #  | 需求                               | 可验收标准                                                                                      |
| -- | -------------------------------- | ------------------------------------------------------------------------------------------ |
| R1 | 知识卡片功能从产品面消失                     | 全站不再有 `#/flashcards` 路由、不再有「记忆卡片」文案、不再有「生成卡片」按钮；`GET/POST /api/flashcards*` 不再注册           |
| R2 | 知识卡片不留"半截"                       | 前端文件、i18n key、审计夹具、测试**同步清理**，不留孤儿译文（`test_translation_tables_have_no_orphan_entries` 必须绿） |
| R3 | 用户旧书签/历史里的 `#/students` 不再看到裸错误页 | 落到一个**有解释**的落点（重定向 + 提示），而不是「未找到页面」                                                        |
| R4 | 「没有学生」不再是一条死路                    | 依赖学生的功能页在 0 学生时**仍然可用**（有默认学生），或**给得出添加入口**；空态文案与真实能力一致                                    |
| R5 | 出一份「无入口页面/功能」清单                  | `docs/` 里有一份带 `文件:行` 的审查报告；每条给出「保留 / 加入口 / 下线」的处置建议                                        |
| R6 | 三语一致                             | zh/es/ca 三语 key 集合始终对齐；es/ca 界面无 CJK 泄漏（`ui_audit` 的 CJK 检查）                               |

**边界（用户没要求，一律不做）**：

- 不重构与本任务无关的前端页面。
- 不因为"顺手"删掉 TASK-108 清单里的任何东西 —— 处置方式已由 **D6** 定死（默认标 `deprecated`
  保留），只有确认能力被另一端点**完全覆盖**才下线。
- 不动 `classroom-data/` 里任何**非 flashcards** 的数据。
- 不新增前端文件（**D2 = P-A 已采纳**，助手放进已存在的 `views/shared.js`，不建新文件）。

---

## 2. 现状盘点（全部带 `文件:行`，2026-10-13 快照，实施时一律先 `grep -n` 复核）

### 2.1 「记忆卡片」（flashcards）全栈足迹

> **✅ 下表所列的每一处都已在 2026-10-13 清理完毕**（提交 `6ef7bee`）——
> 保留它是为了让下一个人看懂"当时拆的是什么"，**不要再照着它去 grep 复核**（会 0 命中）。
> 实测记录见 §12.6 与 `docs/status.md` 的 TASK-106 区块。

| 层          | 位置                                                                                                                                                                     | 规模                                                                                                          |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| 前端页        | `src/web/views/flashcards.js`                                                                                                                                          | 157 行（`pageFlashcards` / `actionReviewFlashcard` / `actionCreateFlashcard` / `loadCardZh` / `renderCardZh`） |
| 前端加载清单     | `src/web/index.html:116`                                                                                                                                               | `<script src="/views/flashcards.js">`                                                                       |
| 前端路由       | `src/web/app.js:1553`                                                                                                                                                  | `else if (parts[0] === 'flashcards') await pageFlashcards();`                                               |
| 前端动作派发     | `src/web/app.js:1496-1501`                                                                                                                                             | `create-flashcard` / `review-flashcard` 两个分支                                                                |
| 前端高亮注释表    | `src/web/app.js:1355`                                                                                                                                                  | `#/knowledge  #/knowledge, #/flashcards`                                                                    |
| **入口（唯一）** | `src/web/views/knowledge.js:905`                                                                                                                                       | 知识地图副标题末尾 `· 记忆卡片入口已移除（TASK-106）`                                                                           |
| 入口（动作）     | `src/web/views/knowledge.js:794`                                                                                                                                       | 列表视图每行「生成卡片」按钮 `data-action="create-flashcard"`                                                             |
| 中文预览复用     | `src/web/app.js:563` 注释、`flashcards.js:17-40`                                                                                                                          | 读 `/knowledge/{id}/glossary` + `/knowledge/{id}/translate`（**只读，不调 LLM**）                                   |
| i18n       | `src/web/i18n.js` `fc.*`                                                                                                                                               | zh 36-43 / es 567-574 / ca 1081-1088，各 13 个 key                                                             |
| 样式         | `src/web/styles.css:392-399`                                                                                                                                           | `.card-zh`（**只被 flashcards.js 使用**，见 §3.4 核对项）                                                              |
| API        | `src/api/endpoints.py:735-805`                                                                                                                                         | `GET/POST /api/flashcards`、`GET /api/flashcards/{id}`、`POST /api/flashcards/{id}/review`                    |
| 应用层        | `src/application/workspace.py:2281-2400`                                                                                                                               | `list_flashcards` / `create_flashcard` / `get_flashcard` / `review_flashcard`                               |
| 调度器接线      | `workspace.py:220,289,406-407,4216-4223`                                                                                                                               | `flashcard_scheduler` 注入 + `/api/health` 输出 FSRS backend                                                    |
| 领域模型       | `src/models.py:234-335`                                                                                                                                                | `class Flashcard`                                                                                           |
| 表规格        | `src/persistence/models/tables.py:113-118,167-171`                                                                                                                     | `TABLES["flashcards"]`、`LINK_TABLES["flashcard_evidence"]`                                                  |
| 仓储         | `src/persistence/repositories/flashcard.py`（125 行）+ `repositories/__init__.py:34,71,105,159`                                                                           | 绑定 + `__all__` + `counts()["flashcards"]`                                                                   |
| 迁移         | `src/persistence/migrations/m004_flashcards.py`（55 行）+ `migrations/__init__.py:62`                                                                                     | 迁移链第 4 条                                                                                                    |
| 装配         | `src/application/persistence_wiring.py:704-718`                                                                                                                        | `save_flashcards` / `load_flashcards`                                                                       |
| 调度器        | `src/scheduling/fsrs.py`（302 行）+ `src/scheduling/__init__.py`                                                                                                          | **只服务记忆卡片**（§3.4 核对项）                                                                                       |
| 测试         | `tests/test_flashcards.py`（260 行）、`tests/test_fsrs_scheduling.py`（98 行）                                                                                                | 定向                                                                                                          |
| 测试守卫       | `tests/test_production_gate.py:1354-1406`（迁移链 4 条 / 仓储 19 个）、`tests/test_multi_course.py:1418-1420`（顶栏归属表）、`tests/test_student_ui.py:955-959`（`noStudentsCard()` 计数 6） | **会红，必须同步**                                                                                                 |
| 测试清单       | `tests/support.py:50` + `scripts/{ui_audit,ui_render_check,ui_stress_check,e2e_session_picker}.js` 的 `WEB_FILES`                                                       | 5 处必须同步（`test_web_source_manifest.py` 守卫）                                                                   |
| 前端审计       | `scripts/ui_audit.js:83,106,771-800,1164,1926,3798-4055`；`scripts/ui_render_check.js:2665-2691`                                                                        | 夹具 + PAGES + 约 12 条 check                                                                                   |
| 文档         | `README.md:45-47`（fsrs 说明）                                                                                                                                             | 需同步                                                                                                         |

> **实测口径**：`git show 0b01124 --stat` 是引入记忆卡片的提交  
> （`feat(cards): m004 flashcards full stack + gate numbers 3->4 / 18->19`）——  
> 这次下线要把那次的 4→3、19→18 反向走一遍，且**不要**把 m004 从链上摘掉（§3.1）。

### 2.2 学生页面：已删的事实 + 真实的死路

**已删（有反向守卫钉着，不要再"修"一次）**：

- `pageStudents` / `pageStudent` / `wireStudentForm` / `renderPathChain` 均不存在  
  （`tests/test_student_ui.py:934-947`）。
- `route()` 里没有 `parts[0] === 'students'` 分支（`app.js:1533-1591` 全表已核）。
- 全站没有任何 `href="#/students"`（`grep -rn 'href="#/students"' src/web` → 0 命中）。
- `scripts/ui_render_check.js:1441-1450` 与 `scripts/ui_audit.js:3443-3450` 有反向断言，  
  钉住"学生列表/详情页面函数不存在"。
- `src/web/views/students.js` 已被**改造成**共享助手文件（`kpLink` / `loadExplanation` /  
  `actionProcessMaterial` …），**不是**学生页 —— 文件名有误导性（§5.3 处置建议第 4 条）。
  > **✅ D5 已批准改名**为 `views/shared.js`（同步 6 处清单）。实施 TASK-107 前**先做这个改名**，
  > 之后全篇的 `views/students.js` 都读作 `views/shared.js`。

**真实的死路（用户说的"显示没有学生"）**：

0 学生的课程下，以下页面渲染空态：

| 页面       | 路由                                        | 空态出处                                            | 文案                             |
| -------- | ----------------------------------------- | ----------------------------------------------- | ------------------------------ |
| 练习列表     | `#/exercises`                             | `views/exercises.js:34-39` → `noStudentsCard()` | 「该课程暂无学生。先为这门课添加学生…」           |
| 单题作答     | `#/courses/<c>/exercises/<e>[/<sid>]`     | `views/exercises.js:97-101`                     | 同上                             |
| 错题本      | `#/mistakes`                              | `views/mistakes.js:266-272`                     | 同上                             |
| 错题详情     | `#/mistakes/<c>/<kp>`                     | `views/mistakes.js:364-367`                     | 同上                             |
| 考前复习     | `#/review`                                | `views/review.js:125-131`                       | `rs.noStudent` +「先为这门课添加学生…」   |
| 今日学习     | `#/learn`                                 | `views/learn.js:85-89`                          | `learn.noStudent`（「请先选择一个学生。」） |
| 知识点学习    | `#/learn/<kp>`、`#/courses/<c>/learn/<kp>` | `views/learn.js:122-125`                        | 同上                             |
| ~~记忆卡片~~ | `#/flashcards`                            | `views/flashcards.js:59-63`                     | 随 TASK-106 一起消失                |

`noStudentsCard()` 定义在 `app.js:551-555`，链接指向 `#/courses`（我的课程）——  
**那里也没有添加入口**。

> **D2 = P-A 之后会怎样**：这 7 个空态分支变成**不可达**（0 学生时先自动建一个默认学生再渲染）。
> 但 `noStudentsCard()` 本身**保留**为防御性兜底（`POST` 仍可能失败，例如课程不存在），
> 文案按 **§4.3-B** 改口径（不再承诺一个不存在的入口）。

**根因（已用 grep 证明）**：

- `POST /api/students` 注册在 `src/api/endpoints.py:917`，实现  
  `workspace.create_student(course_id, student_id, display_name)`（`workspace.py:3100-3111`）。
- 前端 `src/web/**` 里**没有任何一处**对它发 POST：  
  `grep -rn "method: 'POST'" src/web` 的 15 处命中里，`/students` 一个都没有。
- ⇒ **能力在后端完整存在，产品面没有入口**。这与本项目的头号 bug 家族  
  （"能力早就实现好了，装配层从没接上"）是同一族。

**次要问题（不是"没有学生"，但同源）**：

- `state.studentId` 存在 `localStorage['ca.student']`，**全局、不带课程前缀**  
  （`app.js:687`）。换课程后旧 id 失效时，各页退回 `students[0]`  
  （`exercises.js:92-96`、`learn.js:90-92`、`mistakes.js:273-274`、`review.js:132-134`），  
  页头仍会显示一个 `student_id`。`pageExercise()` 已经修过一轮（`exercises.js:82-90` 有长注释），  
  但**根因（全局单值）没变**。

### 2.3 路由入口矩阵（TASK-108 的核心产出之一）

`route()` 全表（`app.js:1533-1591`）对真实入口的求解结果：

| 路由                                    | 页面函数                  | 入口数    | 入口在哪                                          |
| ------------------------------------- | --------------------- | ------ | --------------------------------------------- |
| `#/`                                  | `pageDashboard`       | 顶栏     | 顶栏「概览」                                        |
| `#/today`                             | `pageToday`           | 顶栏     | 顶栏「今日」                                        |
| `#/learn`                             | `pageLearn`           | 2      | 今日页按钮（`dashboard.js:559,661`）                 |
| `#/learn/<kp>`                        | `pageLearnKnowledge`  | 1      | 今日页卡片（`learn.js:22`）                          |
| `#/courses/<c>/learn/<kp>`            | `pageLearnKnowledge`  | 1      | 复习页行（`review.js:29`）                          |
| `#/review`                            | `pageReview`          | 顶栏     | 顶栏「考前复习」                                      |
| `#/review-pack`                       | `pageReviewPack`      | 顶栏     | 顶栏「复习包」                                       |
| `#/knowledge`                         | `pageKnowledge`       | 顶栏 + 5 | 顶栏「知识地图」等                                     |
| ~~`#/flashcards`~~                    | ~~`pageFlashcards`~~  | **0**  | **TASK-106 已删该路由**；旧深链按 **D4** 重定向到 `#/courses` |
| `#/materials`                         | `pageMaterials`       | 顶栏     | 顶栏「材料」                                        |
| `#/reviews`                           | `pageReviews`         | 顶栏     | 顶栏「待审核」                                       |
| `#/exercises`                         | `pageExercises`       | 顶栏     | 顶栏「练习」                                        |
| `#/mistakes`                          | `pageMistakes`        | 顶栏     | 顶栏「错题本」                                       |
| `#/mistakes/<c>/<kp>`                 | `pageMistakeDetail`   | 1      | 错题本行（`mistakes.js:345`）                       |
| `#/courses`                           | `pageMyCourses`       | 顶栏     | 顶栏「我的课程」                                      |
| `#/courses/<id>`                      | `pageCourse`          | 1      | 侧边栏课程列表（`app.js:1080`）                        |
| `#/courses/<id>/sessions/<s>`         | `pageSession`         | 1      | 课程页课堂表                                        |
| `#/courses/<id>/knowledge/<kp>`       | `pageKnowledgeDetail` | 多      | `kpLink()` 全站复用                               |
| **`#/courses/<id>/knowledge-map`**    | `pageKnowledgeMap`    | **1**  | **课程页页头（`courses.js:864`）**                   |
| **`#/courses/<id>/review`**           | `pageCourseReview`    | **1**  | **知识地图页（`knowledge.js:619`）**                 |
| `#/courses/<c>/exercises/<e>[/<sid>]` | `pageExercise`        | 3      | 练习列表 / 今日页 / 错题本                              |

> **没有任何一条死链**（每个路由都至少 1 个入口）—— 这解释了为什么既有守卫  
> （`test_web_ui_invariants.py` 的 href 可达性）全绿：它测的是"**可达**"，  
> 不是"**好找**"。加粗那 **2 行**就是用户说的"隐藏的没有入口的页面"：  
> **入口存在，但只有一处、且不在顶栏**。  
> （原本是 3 行 —— `#/flashcards` 随 TASK-106 消失，见上表删除线那一行；  
> 它留下的旧深链由 **D4** 兜底。）

---

## 3. TASK-106：下线「记忆卡片」（✅ 已完成）

> **本节已执行完毕（2026-10-13）**，提交 `6ef7bee`。实测记录见 **§12.6** 与
> `docs/status.md` 的 TASK-106 区块。下面保留为**决策依据与实施规格**，
> 实施者**不需要再动手** —— 除非发现回归。

### 3.1 决策点 D1 —— 删到什么深度？（✅ 已决 = 方案 A，且已执行）

> ### ✅ D1 已决（2026-10-13 01:45，用户拍板）→ 已于同日执行完毕
>
> **采用方案 A**：代码全删 + 追加 `m005` DROP 两张表。  
> 依据是 §3.1.1 的实测盘点（库里只有 **1** 张卡、`state='new'`、从未复习过 ⇒ 损失 ≈ 0）。  
> **`MIGRATION_005` 保留在迁移链上，不要摘链。**  
> 副产物：修好 P0-1 语法错误后的**第一次开库即执行 DROP** —— 这是**预期行为**，不是事故。
> **实测**：`flashcard*` 表 = `[]`、`schema_version` 最大 = 5；迁移前 `VACUUM INTO` 快照
> 逐表比对，只有两张卡片表消失、其余 33 张表行数未变（§12.6）。

数据库里已经有真实卡片（TASK-105 验收时在真机上建过至少 1 张，见 `docs/status.md` 尾部  
"真机验证新建的 1 张记忆卡片留在本地库里"）。而 `POST /api/flashcards` **没有删除端点**，  
所以那些卡片只能靠改库清掉。

三个方案：

| 方案         | 做什么                                                                                                      | 数据            | 迁移链       | 遗留                                           |
| ---------- | -------------------------------------------------------------------------------------------------------- | ------------- | --------- | -------------------------------------------- |
| **A（推荐）**  | 前端 + API + 应用层 + 仓储 + 模型 + 调度器 + 表规格**全删**，并**追加 `m005` 迁移 `DROP TABLE flashcards, flashcard_evidence`** | **永久删除**（不可逆） | 4 → **5** | 无                                            |
| **B（保守）**  | 只删产品面（前端 + API + 应用层 + 调度器接线），**保留**表 / 仓储 / 模型 / m004                                                   | 原样保留          | 4（不变）     | 表与仓储变成**死代码**（与用户"隐藏功能很离谱"的诉求同构）             |
| **C（最保守）** | 只删前端入口与页面                                                                                                | 原样保留          | 4         | 后端一整套无消费者的 API —— **不建议**，反而制造 TASK-108 那类问题 |

**推荐 A**，理由：用户明确说"没有实际用途"；卡片是**从知识点派生的可重建数据**，  
不是用户手写的原始材料；且 A 是唯一"不留孤儿"的做法。  
**但 A 必须先执行 §3.1.1 的前置动作**：

#### 3.1.1 前置动作（✅ 已于 2026-10-13 执行完毕 —— 保留为记录，实施者不用重做）

> **盘点结果（2026-10-13 01:30 已执行，只读 `sqlite3 ... ?mode=ro` 打开，未写任何字节）**
>
> | 项                    | 实测值                                                                           |
> | -------------------- | ----------------------------------------------------------------------------- |
> | `schema_version`     | 1 → 2 → 3 → 4，链连续，当前 **4**（`004_flashcards` 于 2026-09-27 应用）                  |
> | `flashcards` 总行数     | **1**                                                                         |
> | 所属课程 / 学生            | `course-32dde014868219be`（Gestió de Projectes）/ `1798439`（Shengxuan Ling）     |
> | 该卡片                  | `flashcard-2746b4f18bbac8f5a487`，`due = 2026-10-12T22:14:05Z`，`state = 'new'` |
> | `flashcard_evidence` | **1** 行                                                                       |
> | 对照规模                 | 5 门课 / 467 知识点 / 126 证据 / 1 学生                                                |
>
> **结论**：就是 TASK-105 验收时在真机上点出来的那 1 张卡（`docs/status.md` 尾部有自述），  
> `state='new'` ⇒ **从未被复习过**，且是从知识点派生的可重建数据。  
> ⇒ **方案 A（`m005` DROP）的实际数据损失 ≈ 0**。  
> 执行前仍按下面第 2 步取得用户**一句**确认（这是项目纪律，不是形式）。


1. **只读盘点**（已执行，命令留档以便复核）：报告当前库里卡片数量与所属课程。
   ```bash
   PYTHONPATH=/d/Project/Clases "C:/Users/Rafae/AppData/Local/Microsoft/WindowsApps/python.exe" -c \
   "import sqlite3;c=sqlite3.connect('classroom-data/database/classroom.sqlite');\
   ```

print(c.execute('select count(*) from flashcards').fetchone());  
print(c.execute('select course_id,count(*) from flashcards group by course_id').fetchall())"

````
2. **把数字报给用户，取得显式确认**后才执行 `m005` 的 DROP。  
   > ✅ **已完成**：数字（1 张卡 / 1 行 link / 损失 ≈ 0）已报，用户确认"删"。
3. ~~若用户说"保留数据" → 改走方案 B~~ —— ❌ **未发生**：用户选的是**方案 A**，
   所以 `m005` 保留在链上、已执行完毕（§12.6）。方案 B 的后续动作**不需要做**。

> **不许**用 `sqlite3` 命令行手工 DROP —— 必须走 `m005` 迁移，否则
> `tests/test_persistence_migration.py:322`（"库里每张表都必须在 `TABLES|LINK_TABLES` 里登记"）
> 会红，且新老库 schema 版本不一致。

### 3.2 逐文件改动清单（方案 A）

> 行号为 2026-10-13 快照；实施时先 `grep -n` 复核。

#### A. 删除整个文件（3 个）

1. `src/web/views/flashcards.js`
2. `src/persistence/repositories/flashcard.py`
3. `src/scheduling/fsrs.py` —— **先做 §3.4 核对项 ③**，确认它只服务记忆卡片。

#### B. 前端

| 文件 | 改动 |
|---|---|
| `src/web/index.html:116` | 删 `<script src="/views/flashcards.js"></script>` |
| `src/web/app.js:1553` | 删 `else if (parts[0] === 'flashcards') await pageFlashcards();` |
| `src/web/app.js:1496-1501` | 删 `create-flashcard` / `review-flashcard` 两个派发分支 |
| `src/web/app.js:1355` | 注释表删 `, #/flashcards` |
| `src/web/app.js:563` | 注释里「flashcards 页复用术语表」改为「材料页与知识点详情页共用」 |
| `src/web/views/knowledge.js:794` | 删列表视图每行的「生成卡片」按钮（整块 ternary） |
| `src/web/views/knowledge.js:905` | 删副标题末尾 `· <a href="#/flashcards">…</a>` |
| `src/web/i18n.js` | 删 `fc.*` 全部 13 个 key × 3 语（zh 36-43 / es 567-574 / ca 1081-1088）。**必须删**，否则 `test_translation_tables_have_no_orphan_entries` 红 |
| `src/web/styles.css:392-399` | 删 `.card-zh` 三条规则 —— **先做 §3.4 核对项 ①** |

#### C. 后端

| 文件 | 改动 |
|---|---|
| `src/api/endpoints.py:735-805` | 删 `list_flashcards` / `create_flashcard` / `get_flashcard` / `review_flashcard` 四个 handler 与 4 条 `router.*` 注册；顺带删 `endpoints.py:613` 里引用 `create_flashcard` 的注释 |
| `src/application/workspace.py:2281-2400` | 删 4 个方法 |
| `workspace.py:220,289,406-407` | 删 `flashcard_scheduler` 参数 / 字段 / property |
| `workspace.py:4216-4223` | 删 `/api/health` 里的 FSRS backend 输出（**注意**：这会改变 health 响应形状，见 §3.4 核对项 ④） |
| `src/application/persistence_wiring.py:704-718` | 删 `save_flashcards` / `load_flashcards` |
| `src/persistence/repositories/__init__.py:34,71,105,159` | 删 import / `__all__` 项 / 绑定 / `counts()["flashcards"]` |
| `src/persistence/models/tables.py:113-118,167-171` | 删 `TABLES["flashcards"]` 与 `LINK_TABLES["flashcard_evidence"]` |
| `src/models.py:234-335` | 删 `class Flashcard`（注意 `models/__init__` 若有导出也要同步） |
| `src/scheduling/__init__.py` | 删 fsrs 导出（若 §3.4 核对项 ③ 判定可删） |

#### D. 迁移（方案 A 才做）

新增 `src/persistence/migrations/m005_drop_flashcards.py`：

- `MIGRATION_005`：`DROP TABLE IF EXISTS flashcard_evidence;` → `DROP TABLE IF EXISTS flashcards;`
（先删 link 表，避免外键顺序问题；`DROP TABLE` 前用 `PRAGMA foreign_keys` 已由 Database 管理，
实施时按 `m004` 的写法对齐）。
- `migrations/__init__.py:62` 之后追加 `from ...m005_drop_flashcards import MIGRATION_005`，
并把 `MIGRATIONS` 元组接上。
- **m004 保留不动** —— 迁移链是 append-only 的 release 契约，摘掉会让旧库
`schema_version=4 > latest_version()` 直接报错。

#### E. 测试与审计（**最容易漏的一节**）

| # | 文件 | 位置 | 处置 |
|---|---|---|---|
| ① | `tests/test_flashcards.py` | 整个文件（260 行） | **删除** |
| ② | `tests/test_fsrs_scheduling.py` | 整个文件（98 行） | **删除**（若 §3.4 核对项 ③ 判定 fsrs 只服务卡片） |
| ③ | `tests/test_production_gate.py` | `:1354-1370` 迁移链「恰好 4 条」 | 改 **5**，并更新 docstring（把 m004 的历史保留，加 m005 的说明） |
| ④ | `tests/test_production_gate.py` | `:1374-1406` 仓储 19 个 | 改 **18**，并从 `:1406` 的名字清单里去掉 `"flashcards"` |
| ⑤ | `tests/test_multi_course.py` | `:1418-1420` 顶栏归属表 `"pageFlashcards": "#/knowledge"` | **删这一项**（函数不存在了，守卫按函数名清点） |
| ⑥ | `tests/test_student_ui.py` | `:955-959` `source.count("noStudentsCard()") == 6` | 改 **5**，并更新注释（去掉 flashcards 那一项） |
| ⑦ | `tests/support.py` | `:50` `WEB_SOURCE_ORDER` 里的 `views/flashcards.js` | **删** |
| ⑧ | `scripts/ui_audit.js` | `:83` `WEB_FILES` | **删** |
| ⑨ | `scripts/ui_render_check.js` | `:40` `WEB_FILES` | **删** |
| ⑩ | `scripts/ui_stress_check.js` | `:48` `WEB_FILES` | **删** |
| ⑪ | `scripts/e2e_session_picker.js` | `:43` `WEB_FILES` | **删** |
| ⑫ | `scripts/ui_audit.js` | `:106` `VIEWS_FLASHCARDS`、`:771-800` 夹具、`:1164` 路由表项、`:1926` PAGES 项、`:3798-4055` 约 12 条 check（含 `:3983-3985` 的「per-row flashcard affordance」、`:3994-3997` 的空态 check） | **全删** |
| ⑬ | `scripts/ui_render_check.js` | `:2665-2666`「#/knowledge still links to the flashcards page」、`:2690-2691`「list view keeps the per-row flashcard button」 | **全删**（2 条 check） |
| ⑭ | `README.md` | `:45-47` fsrs 段落 | 删掉该段（或改写为"不再使用 fsrs"） |
| ⑮ | `docs/status.md` | 追加 TASK-106 区块 | 见 §9 |

> ⑤⑦⑧⑨⑩⑪ 六处必须**同批**改，否则 `test_web_source_manifest.py` 会红
> （它逐字比对 `index.html` 的 `<script>` 顺序与 `WEB_SOURCE_ORDER`）。

### 3.3 数据处置

见 §3.1.1。方案 A 下卡片数据随 `m005` 永久消失；**执行前必须报告数量并取得确认**。
备份：`scripts/backup.bat`（见 `docs/backup_restore.md`）可以在动手前打一份归档。

### 3.4 四个必须人工核对的点（**别猜，去 grep**）

① **`.card-zh` 是否只被 flashcards 用？**
```bash
grep -rn "card-zh" src/web --include=*.js --include=*.html
````

2026-10-13 快照：只有 `views/flashcards.js:44` 用（`renderCardZh`）。若实施时发现别的页面也用，  
**保留样式**只删文件。

② **`fc.*` key 是否被非 flashcards 的页面引用？**

```bash
grep -rn "t('fc\." src/web --include=*.js
```

快照：`knowledge.js:905`（随 TASK-106 删）、`flashcards.js` 内部、`ui_render_check.js`。  
`i18n.js` 里 `fc.title` 也被 `knowledge.js` 用过 —— 一并删干净。

③ **`fsrs.py` / `src/scheduling/` 是否只服务记忆卡片？**

```bash
grep -rn "fsrs\|FSRS\|Scheduler" src/ tests/ scripts/ --include=*.py --include=*.js | grep -v "^src/scheduling/"
```

若发现 `study_plan.py` / `review_mode.py` 也在用同一个调度器 → **保留 `src/scheduling/`**，  
只删记忆卡片侧的接线（`flashcard_scheduler`）。这条决定 ② 号测试文件删不删。

④ **`/api/health` 的 FSRS 字段有没有消费者？**

```bash
grep -rn "fsrs\|flashcard" src/web --include=*.js
```

顶栏 pill（`app.js:995` 起的 `loadChrome`）目前只读 `asr_mode` / `ocr_mode` / `ai_mode`。  
若无人读 FSRS 字段 → 删；有 → 保留并只去掉 flashcards 的部分。

### 3.5 验证（TASK-106）

```bash
PYTHONPATH=/d/Project/Clases "C:/Users/Rafae/AppData/Local/Microsoft/WindowsApps/python.exe" \
  -m pytest tests/test_web_source_manifest.py tests/test_production_gate.py \
            tests/test_multi_course.py tests/test_student_ui.py tests/test_web_ui.py \
            tests/test_exercise_ui.py tests/test_persistence_migration.py \
  -q -p no:cacheprovider

"C:/Users/Rafae/.workbuddy-ai/binaries/node/versions/22.22.2-6/node.exe" scripts/ui_render_check.js
"C:/Users/Rafae/.workbuddy-ai/binaries/node/versions/22.22.2-6/node.exe" scripts/ui_audit.js
```

- 基线：2026-10-13 实测 `ui_render_check` = **OK (316)**、`ui_audit` = **OK (629)**。  
  **开工前自己再跑一遍记下当时的数字**（这两个数字会随开发漂移，别照抄本文）。  
  本次**会降**（删掉的 check 比新增多），**降多少要在 `status.md` 里写清楚并解释**  
  —— 本项目允许 check 数减少，但必须说明"减的是被删功能的专用断言"。
- 已知环境性失败（与本任务无关）：  
  `tests/test_student_ui.py::TestTheLiveSessionPickerEndToEnd::test_the_live_script_passes`  
  （`scripts/e2e_session_picker.js:27` 硬编码的 `Python/pythoncore-3.14-64/python.exe` 目录已不存在）。

**变异测试（证明守卫真在盯）**：

| 变异                                                   | 期望                                    |
| ---------------------------------------------------- | ------------------------------------- |
| 把 `fc.title` 从 `i18n.js` 删掉但 `knowledge.js:905` 还引用  | `ui_audit` 的 CJK / 空引用 check 红        |
| 在 `tables.py` 里恢复 `flashcards` 但库里没这张表               | `test_persistence_migration.py` 红     |
| 保留 `WEB_SOURCE_ORDER` 里的 `views/flashcards.js` 但删了文件 | `test_web_source_manifest.py` 红（文件缺失） |
| `noStudentsCard()` 计数不改（仍写 6）                        | `test_student_ui.py:959` 红            |

**人工验证**（TASK-106 当轮已完成）：启动服务 → 顶栏「知识地图」→ 副标题**没有**记忆卡片链接 → 列表视图每行**没有**生成卡片按钮 → 手输 `#/flashcards` → **当轮落在「未找到页面」**（路由已删，符合当时的预期）。

> **后续（D4）**：`#/flashcards` 与 `#/students*` 一起由 §4.3-D 的重定向分支接管，落到 `#/courses`。
> **这条重定向属 TASK-107 的范围，TASK-106 当轮不实现**（`#/flashcards` 现在仍是死路由）。

---

## 4. TASK-107：修「无学生」死路

### 4.1 诊断：三个假设 + 判别实验（**先复现，再改**）

用户说"**有时候**还会跳转"。三个候选假设，必须用真实浏览器逐条排除：

| #      | 假设                                                               | 判别实验                                                 | 若成立，现象是                                                       |
| ------ | ---------------------------------------------------------------- | ---------------------------------------------------- | ------------------------------------------------------------- |
| **H1** | 浏览器**历史/书签**里的旧 `#/students` 或 `#/courses/<c>/students/<sid>` 深链 | 手输 `#/students` 与 `#/courses/<任意有效课>/students/x`     | `route()` 落到 `app.js:1591` 的「未找到页面」。**注意：这解释"跳转"，解释不了"没有学生"** |
| **H2** | 依赖学生的功能页在 0 学生时渲染空态（§2.2 那张表）                                    | 建一门**新课程**（0 学生）→ 依次点顶栏「练习」「错题本」「考前复习」「今日 → 开始今天的学习」 | 「该课程暂无学生。**先为这门课添加学生**…」+ 指向 `#/courses` 的链接 —— **而那里没有添加入口** |
| **H3** | `localStorage['ca.student']` 里的**脏 studentId**（课程删了/学生换了）        | 在 A 课作答 → 删掉 A 课或换到 B 课 → 刷新                         | 页头仍显示一个 `student_id`（各页退回 `students[0]`），不会显示"没有学生"           |

**H2 已确证（2026-10-13 01:30 只读取证，不需要再猜）**

`course_students` 表**只有 1 行**，即整个库里只有 1 个学生：

| 课程                                                                      | 学生数                               | 依赖页表现  |
| ----------------------------------------------------------------------- | --------------------------------- | ------ |
| `course-32dde014868219be` Gestió de Projectes                           | **1**（`1798439` / Shengxuan Ling） | 正常     |
| `course-3fd6392d1fd78e87` Bases per a la Geoinformació                  | **0**                             | **空态** |
| `course-680f27ea0e304d6d` Gestió Ambiental de l'Energia i Dels Recursos | **0**                             | **空态** |
| `course-6ea554f295431036` Digitalització i Microcontroladors            | **0**                             | **空态** |
| `course-bd4f98777c97a0d2` Programació d'Aplicacions a Internet          | **0**                             | **空态** |

⇒ 用户的「**有时候**」不是随机，而是「**侧边栏切到那 4 门课的时候**」。

**精确复现步骤（实施者照抄，直接写进验收记录）**：  
侧边栏点「Bases per a la Geoinformació」→ 顶栏点「练习」→ 页面显示  
「该课程暂无学生。先为这门课添加学生…」→ 点其中「我的课程」链接 →  
**那里没有任何添加入口**。同一条路径在「错题本」「考前复习」  
「今日 → 开始今天的学习」上重复一遍即可。

> **两个必须遵守的推论**：
>
> 1. `1798439` 是**学号**、`display_name` 是「Shengxuan Ling」—— 这个学生是当初通过  
>    **已删除的注册表单**建的真实数据。所以 §4.3-A 的 `ensureStudent()` **绝不能覆盖它**  
>    （先 GET，非空就原样返回；这也是计划书里幂等性测试的判据之一）。
> 2. 修好之后要**回头验证那 4 门课**：切过去点「练习」，应当不再出现空态，  
>    而是看到一个默认学生 —— 且**不能**在 Gestió de Projectes 上多建一个学生。

**复现要求（H1 仍需实测）**：实施者必须记录「起点 hash → 点击序列 → 终点 hash → 截图」。  
H2 已由数据确证，但 **H1 必须实测** —— 用户既然说"跳转"，就大概率点过某个把他带走的链接。

### 4.2 决策点 D2 —— 默认学生，还是补回添加入口？（✅ 已决 = **P-A**）

> ### ✅ D2 已决（2026-10-13，用户拍板）
>
> **采用 P-A（纯默认学生）**：依赖学生的页面发现本课 0 学生时，**自动建一个默认学生再继续**。
> **P-B 不采纳** —— **不补回**「添加学生」表单；空态卡里不加任何输入框。
> P-C 不采纳。
>
> ⇒ §4.3-B 的空态文案**必须**改成「本课程会自动创建一个默认学生，你无需手动添加」这类口径，
> 因为文案**不能**再承诺一个不存在的入口。

用户原话给了方向：「**如果删不了或者不适合删就默认有一个学生啊**」。  
学生是**数据模型的中心**（`course_students` 表 + 40 余个 `/api/students/{id}/...` 端点），  
删不干净 ⇒ 走"默认有一个学生"。

| 方案               | 做法                                              | 代价                       | 结论            |
| ---------------- | ----------------------------------------------- | ------------------------ | ------------- |
| **P-A 默认学生** | 依赖页发现 `students.length === 0` 时，**先建一个默认学生再继续** | 需要一处幂等的"取或建"助手           | ✅ **已采纳**（用户拍板） |
| P-B 补回添加入口       | 空态卡里加一个最小表单（一个输入框 + 提交 → `POST /api/students`）  | 要重做被删的注册 UI；文案"先添加学生"才成立 | ❌ **不采纳**（D2） |
| P-C 彻底去掉学生概念     | 后端固定一个隐式学生                                      | 伤数据模型，改动巨大               | ❌ 不采纳         |

### 4.3 实施方案（P-A）+ 三处配套修正（**全部已定，见 §0.1 的 D2 / D3 / D4**）

#### A. 「取或建默认学生」助手（**唯一写入口，必须收口**）

> **放哪个文件**：D5 已批准把 `src/web/views/students.js` 改名为 `views/shared.js`。
> **建议先做 D5 的改名**（纯 churn、零依赖），再让 TASK-107 往 `views/shared.js` 里加下面这段。
> 若顺序反过来，改名时记得把新加的两个函数一起搬（§10-9 已按此调整顺序）。

在 `src/web/views/shared.js`（原名 `views/students.js`，它已经是共享助手文件）新增：

```js
//: 默认学生的显示名。**刻意用与界面语言无关的固定串** —— 显示名是**数据**，
//: 不该随用户当前的界面语言变化（D3 = C1）。页头负责把它显示出来。
const DEFAULT_STUDENT_NAME = 'Me';

/**
 * 取本课程的当前学生；一个都没有时**建一个默认学生**。
 *
 * 背景 (TASK-107)：学生列表 / 注册页在 2026-09-24 被删除，但 8 个功能页
 * 仍然依赖"至少有一个学生"。空态文案写着「先为这门课添加学生」，而全站
 * 没有任何添加入口 —— 用户被指到一条死路。
 *
 * 幂等性由**调用方保证**：先 GET，为空才 POST。POST 用的是**确定性 id**
 * （见 defaultStudentId），同一门课重复调用只会命中同一个学生。
 * 后端 `POST /api/students` 自己也幂等：已存在 → 200，新建 → 201
 * （`src/api/endpoints.py:814-818`）。
 */
function defaultStudentId(courseId) {
  // 课程作用域：course_students 的主键是 (course_id, student_id)，
  // 所以固定名在每门课里都合法，且不依赖任何随机数（确定性）。
  return 'student-default';
}

async function ensureStudent(courseId) {
  let students = (await api('/students', { query: { course_id: courseId } })).students || [];
  if (students.length) return students;
  await api('/students', {
    method: 'POST',
    query: { course_id: courseId },
    body: { student_id: defaultStudentId(courseId), display_name: DEFAULT_STUDENT_NAME },
  });
  return (await api('/students', { query: { course_id: courseId } })).students || [];
}
```

> **绝不覆盖已有的真实学生**：先 GET，非空直接返回。库里 `course-32dde014868219be`
> 上的 `1798439` / 「Shengxuan Ling」是真实数据，动它就是数据事故。

**接线点（7 处，逐条替换 `const students = (await api('/students', ...))`）**：

`views/exercises.js:9`、`:91`；`views/mistakes.js:263`、`:363`；  
`views/learn.js:84`、`:121`；`views/review.js:124`。

> 计划书初稿列的是 8 处，第 8 处 `views/flashcards.js:58` **随 TASK-106 一起删掉了**
> （✅ 已删），所以现在是 7 处。实施时先 `grep -n "api('/students'" src/web/views/` 复核一遍。

替换后，`students.length === 0` 的兜底分支**变成不可达**。**不要直接删那些分支** ——  
改为"理论上到不了"的防御性报错（`toast` + 说明），因为 `POST` 仍可能失败（例如课程不存在）。

#### B. 空态文案与真实能力一致

- `i18n.js` 的 `student.prepare`（zh 74 / es 605 / ca 1103）：「先为这门课添加学生，再开始练习和错题记录。」  
  → 改成不再承诺一个不存在的入口。建议：
  - zh：`本课程会自动创建一个默认学生，你无需手动添加。`
  - es：`El curso crea automáticamente un estudiante por defecto; no hace falta añadir ninguno.`
  - ca：`El curs crea automàticament un estudiant per defecte; no cal afegir-ne cap.`
- `learn.noStudent`（zh 325 / es / ca 354 对应区）：「请先选择一个学生。」→ 同上口径。
- `rs.noStudent` 同。
- **三语必须同时改**，且 es/ca 不得出现 CJK。

#### C. 默认学生的显示名（`display_name`）（✅ 已决 = **C1**）

`#/learn`（`learn.js:101`）与 `#/review`（`review.js:145`）的副标题直接显示 `student_id`，  
`student-default` 这种机器名很难看。

> **✅ D3 已决（2026-10-13）：采用 C1。**
> 做法：`POST /api/students` 带 `display_name: DEFAULT_STUDENT_NAME`（= `"Me"`，见 §4.3-A），
> 并把 `learn.js:101`、`review.js:145` 改成 `display_name || student_id`。
> **不要**按界面语言生成显示名 —— 显示名是**数据**，界面语言是**界面状态**，两者不能混。

- **C1（已采纳）**：`POST` 时带 `display_name`，值用**与界面语言无关**的固定串 `"Me"`，  
  并让页头**优先显示 `display_name`**（`learn.js:101`、`review.js:145` 改 `display_name || student_id`）。
- ~~**C2**：不动，页头继续显示 `student-default`。~~ ❌ **不采纳**。

#### D. 深链兜底（R3）（✅ 已决 = 落点 **`#/courses`**，且 `#/flashcards` 一并兜底）

> **✅ D4 已决（2026-10-13）**：落点 = **`#/courses`（我的课程）**；
> 范围 = **`#/students*` 与 `#/flashcards` 都重定向**（后者是 TASK-106 之后新产生的死路由）。

`route()` 的 else 之前加一条显式分支（`app.js:1591` 前）：

```js
// TASK-107 (D4): 旧书签 / 浏览器历史里的死路由不该落到裸的「未找到页面」——
// 那里没有任何线索告诉用户"这个页面曾经存在、现在去哪了"。
//   ① `#/students*`  —— 学生列表 / 详情页在 2026-09-24 被删除
//   ② `#/flashcards` —— 记忆卡片页随 TASK-106 下线（2026-10-13）
// 两者都重定向到「我的课程」（用户拍板 D4）。`return` 必须留着。
else if (parts[0] === 'students' || parts[0] === 'flashcards' || parts[2] === 'students') {
  window.location.replace('#/courses');
  return;
}
```

**注意**：

- `window.location.replace` 会触发 `hashchange` → `route()` 再跑一次，所以 `return` 是必须的
  （否则本次渲染会与重定向竞争）。
- `#/courses/<c>/students/<sid>`（4 段）靠 `parts[0] === 'students'` 抓不到，所以上面多带了
  一个 `parts[2] === 'students'`。
- 落点已定为 **`#/courses`**（不是 `#/`），别再改。

### 4.4 守卫同步（TASK-107）

| # | 文件                                | 位置                                                          | 处置                                                                                         |
| - | --------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| ① | `tests/test_student_ui.py`        | `:934-947` `TestRemovedStudentPages`                        | **保留**（反向契约不变：函数仍然不存在）。**D4 已采纳** ⇒ **新增**一条断言：`route()` 里有 `parts[0] === 'students'`（含 `'flashcards'` 与 `parts[2] === 'students'`）的重定向分支，落点 `#/courses` |
| ② | `tests/test_student_ui.py`        | `:961-964` `test_backend_student_endpoints_are_not_removed` | 保留                                                                                         |
| ③ | `tests/test_student_ui.py`        | `:955-959` `noStudentsCard()` 计数                            | 若 P-A 让空态分支变得不可达、进而删掉部分调用点 → 同步改计数（**现值 5**，TASK-106 已从 6 改到 5） |
| ④ | `scripts/ui_audit.js`             | `:3443-3450` 学生页移除的反向 check                                 | 保留                                                                                         |
| ⑤ | **新增**                            | 默认学生的幂等性测试                                                  | 见 §4.5                                                                                     |
| ⑥ | `tests/test_web_ui_invariants.py` | 路由可达性                                                       | **D4 已采纳** ⇒ 确认 `students` / `flashcards` 的重定向分支不被"未找到页面"断言误伤                              |
| ⑦ | **新增**                            | 默认学生的显示名                                                     | **D3 = C1** ⇒ 断言 `ensureStudent` 的 POST body 带 `display_name`，且页头渲染 `display_name \|\| student_id` |

### 4.5 验证（TASK-107）

**新增测试（必须有，否则"默认学生"这条线没有守卫）**：

- `tests/test_student_ui.py` 新增类 `TestDefaultStudent`：
  - 0 学生的课程 → 渲染 `#/exercises` → 断言渲染出的 HTML **不含** `student.none`，  
    且 `__fetched` 里出现了 `POST /api/students`（用 `scripts/ui_render_check.js` 的桩式，或  
    用真实 HTTP client）。
  - 调两次 → `POST` 只发生一次（幂等）。
  - 已有一个学生的课程 → **不发** `POST`（不许覆盖用户已有的学生）。
  - **D3 = C1**：`POST` 的 body 带 `display_name`，值为 `"Me"`（与界面语言无关）；  
    把界面切到 es / ca 再跑一次，`display_name` **仍然是 `"Me"`**（证明没把界面语言写进数据）。
  - **D4**：`route()` 里存在 `parts[0] === 'students' || parts[0] === 'flashcards'` 的重定向分支，
    且 `window.location.replace('#/courses')` 后 `return`。

**真实浏览器验证（必须做）**：

1. 新建一门空课程 → 顶栏依次点「练习」「错题本」「考前复习」「今日 → 开始今天的学习」→  
   四页都**能正常用**，不再出现「该课程暂无学生」。
2. 侧边栏 / 概览的「学生」计数从 0 变成 1（并解释这 1 个是谁）。
3. 手输 `#/students`、`#/courses/<有效课>/students/x` 与 `#/flashcards` → **三者都重定向到
   `#/courses`**（D4），**不是**裸错误页。
4. 三语各切一次 → 空态文案与页头显示名一致，无 CJK 泄漏。

---

## 5. TASK-108：清点「无入口的页面与功能」

> **本任务是只读审查 + 处置清单，不擅自删任何东西。** 用户明确说"这挺离谱的"，  
> 但"离谱"不等于"可以删"—— 处置要用户点头（**处置方式已于 2026-10-13 拍板，见 §0.1 的 D6**）。

### 5.1 产出物

1. `docs/ui-orphan-audit.md` —— 审查报告（**不是**本计划书的一部分，是 TASK-108 的交付物）。
2. `scripts/audit_orphans.py` —— **可重跑的只读脚本**。  
   > **✅ D9 已拍板（2026-10-13）：正式入仓**，不留 `temp/`。理由：§5.4 的"孤儿守卫"
   > 要长期靠它复现结论，脚本进仓库才好维护。脚本**必须只读**（不写库、不发写请求），
   > 且 `--help` 要说清它的判定口径（前缀可达性 + 已知误报来源，见附录 A 的注解）。
3. 处置建议表（§5.3），每条带 `文件:行` + 建议 + 代价。

### 5.2 已跑出的第一版结果

#### (a) 路由入口矩阵 → 见 §2.3。**2 个页面只有 1 个入口且不在顶栏**：

`#/courses/<id>/knowledge-map`、`#/courses/<id>/review`。

> `#/flashcards` **已随 TASK-106 消失**（✅），它的旧深链由 **D4** 重定向兜底。
> 所以原本的 3 个现在只剩 2 个 —— 其中 `#/courses/<id>/review` 按 **D10** 转待办 B1，
> 本轮不动。

#### (b) 后端注册、前端**无任何消费者**的端点（前缀可达性求解，16 条）

| 端点                                                                         | 推测用途                                     | 处置建议                                    |
| -------------------------------------------------------------------------- | ---------------------------------------- | --------------------------------------- |
| `GET /api/today`                                                           | 与 `GET /api/student-today` 重复            | **核对**：若确是旧版被取代 → 标记 deprecated 并加注释，不删 |
| `GET /api/knowledge-summary`                                               | 概览页改用 `/api/my-courses` 后失去消费者           | 同上                                      |
| `GET /api/course-knowledge`                                                | TASK-105 换渲染体后失去消费者（`docs/status.md` 已记） | 同上                                      |
| `GET /api/conflicts` / `/api/coverage` / `/api/dependencies` / `/api/gaps` | 知识健康度的**细粒度**端点，前端只用了聚合值                 | **候选"加入口"**（概览页有「知识健康度」卡，但只显示 4 个数字）    |
| `GET /api/exercises`、`GET /api/exercises/{id}`、`/start`、`/grounding`       | 前端全走 `/api/students/{id}/exercises/...`  | 核对是否同一能力的两种口径                           |
| `GET /api/exercise-generation/preview`                                     | 批量出题用了 `/batch`，没用 preview               | 核对                                      |
| `POST /api/exercise-workflow/submit`                                       | 前端走 `/api/students/{id}/learning/answer` | 核对（两套作答入口？）                             |
| `GET /api/evaluations/{id}`                                                | 评估结果内嵌在单题视图里                             | 核对                                      |
| `GET /api/learning-paths/{id}`、`GET /api/study-plans/{id}`                 | 前端未用                                     | 核对                                      |

**验证方法（实施者必须做，不要照抄本表）**：

```bash
# 1) 起真实服务（绕代理，见 vanilla-js-ui-audit skill 第 5 步）
# 2) 逐个 curl 这 16 条，确认返回 200 而不是 404
# 3) 对每条给出"它的能力是否已经由另一个被前端调用的端点覆盖"
```

#### (c) 动词级别的缺口（人工 grep 复核过的**确定项**）

- **`POST /api/students` 前端零调用** ⇒ 这就是 TASK-107 的根因，不是"孤儿端点"而是**缺入口**。
- `PATCH /api/courses/{id}`（改课程名）前端零调用 ⇒ 建了课程就改不了名。
- `POST /api/exercises`（手工出题）前端零调用 ⇒ 只能走 `/exercise-generation/batch`。

> 上面 (b) 的 16 条是**脚本第一版**输出。`temp/_audit_endpoints.py` 已把它跑出来  
> （脚本见附录 A）。**误报来源已知**：前端大量拼接式调用 `api('/students/' + id + '/mistakes')`  
> 静态只能抓到前缀，所以脚本用的是"前缀可达"判定；`ui_audit.js` 的桩夹具也可能覆盖到  
> 脚本没看到的分支。**结论必须逐条人工确认后才写进报告。**

### 5.3 处置建议（**每条已拍板，见 §0.1 的 D5–D10；实施者照"结论"列执行**）

| # | 对象 | 建议 | 代价 / 备注 | **结论（2026-10-13 用户拍板）** |
| - | --- | --- | --- | --- |
| 1 | `#/courses/<id>/knowledge-map` 只有 1 个入口 | 加入口：顶栏「知识地图」已经是它（TASK-105）；课程页那个深链保留 | 0 改动 | ✅ **无需动作**（已解决） |
| 2 | `#/courses/<id>/review` 只有 1 个入口（知识地图页里） | 加入口：课程页页头与知识地图并列再放一个，或课程页「待审核」卡加「全部 →」 | 1 行 | ❌ **不做，转待办 B1** |
| 3 | `#/learn` 只有 2 个入口（今日页） | 保持（今日页是它的家） | — | ✅ 保持 |
| 4 | `src/web/views/students.js` **文件名误导**（里面没有学生页） | 重命名为 `views/shared.js`，同步 **6 处**清单（`index.html` / `tests/support.py` / 4 个 `scripts/*.js` 的 `WEB_FILES`） | 6 处同步，纯改名 | ✅ **批准（D5）** |
| 5 | 16 条无消费者端点 | 逐条核对 → 分三类 | — | ✅ **默认标 `deprecated` 保留（D6）**：加代码注释 + 测试白名单（附理由）；只在确认能力已被另一端点**完全覆盖**时才下线 |
| 6 | `PATCH /api/courses/{id}` 无入口 | 加入口（课程页改名按钮） | — | ✅ **批准加入口（D7）** |
| 7 | 旧 `input/` `output/` 等 8 个遗留目录 | 已在 `AGENTS.md` 归档说明，保持 | — | ✅ 保持 |

> **第 2 条是本表唯一"不批准"的项** —— 不是否决，是**推迟**：用户要整体改版课程级审核
> （`#/courses/<id>/review`），所以本轮**一行都不动**，登记为待办 **B1**（§0.1 末尾）。
> TASK-108 的报告里要如实写"现状 + 待办"，**不要**顺手把入口加上去。
>
> **第 4 条（改名）建议最先做** —— 它纯 churn、零依赖，做完之后 TASK-107 直接往
> `views/shared.js` 里加助手（见 §4.3-A 与 §10-9 的顺序）。

### 5.4 新增守卫（**✅ D8 已批准，3 条全部采纳** —— 这是 TASK-108 的真正产出）

1. **每个 `pageXxx()` 必须至少被 1 个 `href`/`window.location` 生成点触达**，  
   且生成点清单要**机械可枚举**（现有守卫只测"href 可达"，不测"有没有人指向它"）。  
   → 这条能一次性锁死"孤儿页面"这一类。
2. **每个注册的 API 端点必须在前端有调用点，或在白名单里显式标注 `deprecated`。**  
   白名单写在测试文件里，附理由。
3. **每个 `POST/PATCH/DELETE` 端点必须在 `scripts/ui_audit.js` 里有至少一次真实调用**  
   （这条会直接抓出 `POST /api/students` 那类"能力存在但没入口"）。

> **实施注意（与 D6 / D7 联动）**：
>
> - 守卫 ②的白名单**会很长** —— 因为 D6 决定那 16 条**默认标 `deprecated` 保留**。
>   白名单每条都要写理由（"能力已被 X 端点覆盖，保留仅为兼容"），**不要**写成空的占位条目，
>   否则这条守卫就退化成摆设。
> - 守卫 ③在 TASK-107 做完后，`POST /api/students` 会被真实覆盖（`ensureStudent` 会发它）。
>   其余写端点若仍无调用，同样进白名单并注明原因。
> - 三条守卫都要**做变异测试**证明它们真的会红（§7.4）：例如给 `PAGES` 加一个没有任何入口的
>   页面 → 守卫 ① 必须失败。

---

## 6. 非目标（明确不做）

1. **不**重构与本任务无关的页面（概览 / 材料 / 课程 / 错题本的非学生部分）。
2. **不**擅自删 TASK-108 清单里的东西 —— 处置方式已由 **D6** 定死（默认标 `deprecated` 保留），
   只有确认能力已被另一端点**完全覆盖**才下线。
3. **不**动 `dashboard.js` / `courses.js` 里指向 `#/knowledge` 的既有文案（TASK-105 §7-4 的结论继续有效）。
4. **不**改知识地图的默认视图（保持 `graph`）。
5. **不**跑全量回归。
6. **不**新增前端文件（TASK-107 的助手放进已存在的 `views/shared.js` —— 原名 `views/students.js`，由 D5 改名）。
7. **不碰课程级审核（`#/courses/<id>/review`）** —— 用户明确说要整体改版，本轮**只登记现状**，一行不动（待办 **B1**，见 §0.1）。

---

## 7. 验证方案（**只跑定向，不跑全量回归**）

> 用户 2026-10-10 明确要求：**不许跑全量**（一轮 20–60 分钟，不值当）。  
> 只跑受影响文件 + 前端审计（秒级）。归因按「**失败的名字有没有变**」，不按「failed 是不是 0」。

### 7.1 解释器（本机实测）

- 本机唯一带 pytest 的是 **`C:/Users/Rafae/AppData/Local/Microsoft/WindowsApps/python.exe`（3.14.5 + pytest 9.1.1）**。
- 托管 3.13.12 **没有 pytest**；便携解释器 `/Python/` 已丢失。
- 需要 `pypdf` / `onnxruntime` / `faster_whisper` 的测试在这个解释器下会成片报 `E`（环境性，不是回归）。
- node 用 `C:/Users/Rafae/.workbuddy-ai/binaries/node/versions/22.22.2-6/node.exe`。

### 7.2 定向测试（每个任务各自跑，别合并成一大坨）

**TASK-107 / TASK-108 的受影响面**：

```bash
PYTHONPATH=/d/Project/Clases "C:/Users/Rafae/AppData/Local/Microsoft/WindowsApps/python.exe" \
  -m pytest tests/test_student_ui.py tests/test_web_ui_invariants.py \
            tests/test_web_ui.py tests/test_web_source_manifest.py \
            tests/test_multi_course.py tests/test_exercise_ui.py \
            tests/test_learning_view.py tests/test_production_gate.py \
  -q -p no:cacheprovider
```

> **TASK-106 已完成**，它当轮跑的是 13 个文件、结果 **709 passed / 1 failed（环境）**，
> 已记进 `docs/status.md`。**不要重跑那一轮**（要 4–5 分钟）。

**本机长期存在的环境性失败（与本次改动无关，出现即为环境）**：  
`tests/test_student_ui.py::TestTheLiveSessionPickerEndToEnd::test_the_live_script_passes`  
—— `scripts/e2e_session_picker.js:27` 硬编码的 `Python/pythoncore-3.14-64/python.exe` 目录已不存在 ⇒ `spawn ENOENT`。

### 7.3 前端审计（两个都要跑，必须看完退出码）

```bash
"C:/Users/Rafae/.workbuddy-ai/binaries/node/versions/22.22.2-6/node.exe" scripts/ui_render_check.js
"C:/Users/Rafae/.workbuddy-ai/binaries/node/versions/22.22.2-6/node.exe" scripts/ui_audit.js
```

- **基线（2026-10-13，TASK-106 之后）**：`ui_render_check` **OK (314)**、`ui_audit` **OK (610)**。  
  **开工前先自己跑一遍记下当时的数字**（会漂移，别照抄本文），收工再跑一遍比对。
- TASK-107 应**升**（默认学生 / 重定向的新 check）；TASK-108 也应**升**（3 条新守卫）。
  **无论升降，必须在 `status.md` 里给出前后数字并解释每一条增减。**
- TASK-106 的历史：`ui_audit` **629 → 610**（−19）、`ui_render_check` **316 → 314**（−2），
  增减已逐条解释（`docs/status.md`）。

### 7.4 变异测试（见各任务小节）

### 7.5 人工/浏览器验证

每个任务各自的浏览器验证清单见 §3.5 / §4.5。**TASK-107 的复现记录是硬门槛**。

---

## 8. 风险与回滚

| 风险                                | 概率           | 缓解                                                                            |
| --------------------------------- | ------------ | ----------------------------------------------------------------------------- |
| ~~TASK-106 方案 A 误删用户数据~~           | ✅ **已发生且已兜住** | 盘点（1 张卡 / 损失 ≈ 0）已报用户确认；迁移前 `VACUUM INTO` 快照 + 逐表比对（§12.6）                  |
| ~~摘掉 m004 导致旧库 `schema_version` 对不上~~ | ✅ 未发生        | **m004 一律保留**，只追加 m005 —— 已按此执行                                                 |
| ~~脚本清单漏改一处~~                       | ✅ 未发生        | `test_web_source_manifest.py` 绿（TASK-106 当轮已验证）                                  |
| **TASK-107 没复现就改，改错方向**           | 中            | §4.1 三假设 + 判别实验；H2 是主因但 **H1 必须实测**                                             |
| **默认学生被反复创建**                     | 中            | `defaultStudentId()` 用确定性 id + 调用方先 GET 再 POST；后端本身幂等（200/201）；新增幂等测试（§4.5）    |
| **默认学生覆盖已有的真实学生**                 | **中**（数据事故）   | `ensureStudent` 先 GET、非空直接返回；新增测试"已有学生的课程**不发** POST"                            |
| **重定向分支与 `route()` 竞争**           | 低            | `window.location.replace(...)` + 立即 `return`；`#/courses/<c>/students/<x>` 记得带 `parts[2]` |
| **新守卫 ②的白名单写成空壳**                 | 中            | 每条白名单必须带理由（§5.4「实施注意」）；做变异测试证明会红                                               |
| **顺手把 TASK-108 清单里的东西删了，或给 B1 加了入口** | 中            | §6-2、§6-7 明令禁止；B1 只登记、不动代码                                                    |
| **D5 改名漏改一处清单**                   | 中            | 6 处：`index.html` / `tests/support.py` / 4 个 `scripts/*.js` 的 `WEB_FILES`；`test_web_source_manifest.py` 会红 |

**回滚**：

- **TASK-106** → `git revert 6ef7bee`（纯代码）。**注意 `m005` 的 DROP 不可逆** ——
  要恢复那 1 张卡只能靠迁移前的快照（`temp/pre-m005-snapshot.sqlite`，不在版本控制里）。
  这也是为什么 §3.1.1 被定为硬前置。
- **TASK-107 / TASK-108** → 纯前端 + 只读审查，`git revert` 即可，无数据风险。  
TASK-107 / 108 全部可 `git revert`。

---

## 9. 交付物与验收标准

### 交付物

1. **TASK-106 ✅ 已完成**（提交 `6ef7bee`）：前端 5 个文件 + 后端 7 个文件 + 删除 5 个文件/包 + 迁移 `m005`（方案 A）+ 同步后的 8 处守卫/脚本 + `README.md` + 顺带清掉 3 个游离补丁脚本。详见 §12.6。
2. **TASK-107**：`views/shared.js`（新助手，**D5 改名后**）+ **7 个 view** 的接线 + 三语文案（§4.3-B）+
   `app.js` 的重定向分支（**D4**：`#/students*` + `#/flashcards` → `#/courses`）+ `display_name`（**D3**）+ 新增测试。
3. **TASK-108**：`docs/ui-orphan-audit.md`（审查报告）+ `scripts/audit_orphans.py`（**D9：正式入仓**）+
   处置建议表（§5.3 已逐条拍板）+ **3 条新守卫（D8）**。
4. `docs/status.md` 追加 **TASK-107 / 108** 两个区块，沿用现有区块格式  
   （依据 / 改动 / 验收 / 规模 / 风险）。
   > TASK-106 的区块**已经写好了**（2026-10-13），不用重写。
5. 实测输出粘贴：两个审计脚本的**完整尾部**（含 check 数）+ 定向 pytest 的 summary 行。

### 验收清单（逐条打勾）

**TASK-106（✅ 全部已打勾 —— 实测记录见 §12.6 与 `docs/status.md` 的 TASK-106 区块）**

- [x] `grep -rn -E "flashcard|fsrs" src/ tests/ scripts/` → 只剩 m004/m005 迁移文件自身的表名与说明性注释
- [x] `grep -rn "fc\." src/web --include=*.js` → 0 命中
- [x] `src/web/index.html` 的 `<script>` 数量与 `WEB_SOURCE_ORDER` 一致（`test_web_source_manifest.py` 绿）
- [x] 迁移链 = **5**（方案 A）；仓储数 = **18**
- [x] `test_translation_tables_have_no_orphan_entries` 绿
- [x] `ui_audit` **OK (610)** ← 629；`ui_render_check` **OK (314)** ← 316，增减已逐条解释
- [x] 库里卡片数（1 张）已报用户并取得确认；迁移前 `VACUUM INTO` 快照已打；m005 已执行并逐表比对

**TASK-107**

- [ ] 有一份**复现记录**（起点 hash → 点击序列 → 终点 hash → 截图/文字），并指出命中的是 H1/H2/H3 中的哪条
- [ ] 新建空课程 → 练习 / 错题本 / 考前复习 / 今日学习四页**都能用**（**D2 = P-A 默认学生**）
- [ ] `#/students`、`#/courses/<c>/students/<x>` 与 `#/flashcards` **都重定向到 `#/courses`**（**D4**）
- [ ] 幂等性测试：两次调用只发一次 `POST /api/students`
- [ ] 已有学生的课程**不会**被塞进一个默认学生（Gestió de Projectes 上仍是 `1798439`）
- [ ] `POST` 带 `display_name = "Me"`（与界面语言无关），页头渲染 `display_name || student_id`（**D3**）
- [ ] 三语文案已改且无 CJK 泄漏（§4.3-B）

**TASK-108**

- [ ] `docs/ui-orphan-audit.md` 每条结论都带 `文件:行`
- [ ] 16 条无消费者端点**逐条**给出核对结论（不是照抄本表），并按 **D6** 标 `deprecated` + 白名单理由
- [ ] `scripts/audit_orphans.py` 已入仓、只读、`--help` 写清判定口径与已知误报（**D9**）
- [ ] **3 条新守卫（D8）**已落地，且各自做过变异测试证明会红
- [ ] `PATCH /api/courses/{id}` 的课程改名入口已加（**D7**）
- [ ] `views/students.js` → `views/shared.js` 改名完成，**6 处**清单同步（**D5**）
- [ ] 课程级审核（`#/courses/<id>/review`）**一行未动**（**D10**，待办 B1）

---

## 10. 给实施者（下一个 agent）的操作纪律

1. **仓库是 git 仓库**（`main` 分支）。改动前先 `git status` / `git log --oneline -5` 看清工作区。
2. **Codex 可能在并行改同一个仓库**。做法：`git add <自己改的文件名>`，  
   **绝不用 `git add -A` / `git add .`**；提交前 `git diff --cached --stat` 核对暂存区只有自己的文件。
3. **推送（`git push`）会失败**：本机没有 GitHub 凭据（`401` + 读不到 Username）。  
   提交可以，推送交给用户自己。**不要反复试 push。**
4. **行尾**：`.gitattributes` 规定 `*.py/*.js/*.css/*.html/*.json/*.md` 用 **LF**，  
   `*.bat/*.cmd/*.ps1` 用 CRLF。别把行尾弄反。
5. **同一文件的多处修改必须串行**（一次一个 Edit，看结果再下一个）。
6. **精准修改**：只改本计划书列出的位置。发现无关的死代码，**只报告不删除**。
7. **不跑全量回归**（§7 顶部）。只跑定向 + 两个前端审计。
8. **决策已全部拍板** —— 见 **§0.1 决策台账**（D1–D10）。**不要重新论证**，按"结论"列执行。
   实施中若撞上**台账里没有的**新决策点，先问用户，不要自己定。
9. **顺序建议**（已按决策调整）：

   1. **先做 D5 的改名**（`views/students.js` → `views/shared.js`，同步 6 处清单）——
      纯 churn、零依赖。做完之后 TASK-107 直接往新文件里加助手，省一次搬迁。
   2. **TASK-107**（依赖 §4.1 的复现）：D2 默认学生 + D3 显示名 + D4 重定向。
   3. **TASK-108 其余**（只读审查 → 报告 + 脚本 + 3 条新守卫 + D7 的改名入口）。

   > **TASK-106 已完成**（提交 `6ef7bee`），不用再做。它已经把"无学生"的页面数从 8 减到 7，
   > 所以 TASK-107 的验证面比计划书初稿更小。

---

## 11. 参考

- `docs/task-105-knowledge-map-entry.md` —— 记忆卡片入口搬进知识地图的那次改动（TASK-106 已把它反向走完）
- `docs/task-82-optimization-plan.md` —— TASK-82…104 总计划；§0.1 是滚动进度
- `docs/status.md` —— 逐任务实现记录（**尾部**有 TASK-105 与 TASK-106 的完整区块）
- `AGENTS.md` —— 项目规范（证据优先、绝不编造、数据真相源）
- `docs/architecture.md` / `docs/data_model.md` —— 分层守卫、SQLite schema（§5 迁移规则）
- `scripts/ui_audit.js` 头部 —— 「真实事件冒烟清单」（哪些已覆 / 未覆，别宣称等价于浏览器测试）
- `docs/review-2026-10-11-optimization.md` —— 上一轮优化评审
- 技能 `vanilla-js-ui-audit`（`~/.workbuddy-ai/skills/vanilla-js-ui-audit/SKILL.md`）
  —— TASK-108 的方法论出处（路由可达性求解 / 端点差集 / 起真实服务只读探测）

---

## 附录 A：端点差集脚本（TASK-108 的第一版，可直接复用）

> 2026-10-13 实测：后端注册 86 条路径、前端 15 个字面量前缀、**16 条无消费者**。  
> **已知误报**：前端拼接式调用（`api('/students/' + id + '/mistakes')`）静态只能抓到前缀，  
> 所以判定规则必须是"前缀可达"；`ui_audit.js` 的桩也可能覆盖到脚本看不到的分支。  
> **结论必须逐条人工确认。**

```python
"""只读审查：后端已注册端点 vs 前端 api() 调用面 的差集（前缀可达性）。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def norm(p: str) -> str:
    p = re.sub(r"\{[^}]*\}", "{X}", p)
    if not p.startswith("/api"):
        p = "/api" + p
    return p.rstrip("/") or "/api"

# 后端注册面
ep = (ROOT / "src" / "api" / "endpoints.py").read_text(encoding="utf-8")
backend: dict[str, set[str]] = {}
for m in re.finditer(r'router\.(get|post|patch|delete)\(\s*"([^"]+)"', ep):
    backend.setdefault(norm(m.group(2)), set()).add(m.group(1).upper())

# 前端字面量前缀
lits: set[str] = set()
for f in sorted((ROOT / "src" / "web").rglob("*.js")):
    src = f.read_text(encoding="utf-8")
    for m in re.finditer(r"\bapi\(\s*(['\"])(.+?)\1", src):
        lits.add(norm(m.group(2)))

def reachable(p: str) -> bool:
    # L == P  或  L 是 P 的前缀段  或  P 是 L 的前缀段（拼接式补全）
    return any(l == p or l.startswith(p + "/") or p.startswith(l + "/") for l in lits)

print("【A】后端注册、前端不可达：")
for p in sorted(backend):
    if not reachable(p):
        print(f"   {sorted(backend[p])} {p}")

print("\n【B】前端字面量前缀、后端无匹配（疑似死调用）：")
for l in sorted(lits):
    if not any(l == p or l.startswith(p + "/") or p.startswith(l + "/") for p in backend):
        print(f"   {l}")
```

---

## 12. ⚠️ 当前工作区状态（2026-10-13 01:40 实测，**接手前必读**）

> **✅ 2026-10-13 已收尾** —— 本节记录的破损状态**已全部修复**，m005 也已执行并复核。
> 结论与实测数字见 **§12.6**；本节保留作为"接手前的现场记录"，**不要再按它判断当前状态**。

TASK-106 已被**另一个 agent 开工到一半**，而且**把仓库改坏了**。它自己的叙述是  
"No changes to existing files"（连说 19 次），但 `git status` 有 **22 个文件被改**。  
**这句话与事实不符，不要相信它的自述，一律以 `git diff` 为准。**

### 12.1 它已经做完的部分（可保留）

| 文件                                                                              | 改动                                 | 评价                         |
| ------------------------------------------------------------------------------- | ---------------------------------- | -------------------------- |
| `src/persistence/migrations/m005_drop_flashcards.py`                            | **新增**（DROP 两张表，先删 link 表）         | ✅ 内容正确                     |
| `src/persistence/migrations/__init__.py`                                        | 链接上 `MIGRATION_005`（1→5）           | ✅ 正确                       |
| `src/api/endpoints.py`                                                          | −77 行（4 个 handler + 4 条 router 注册） | ✅                          |
| `src/application/workspace.py`                                                  | −137 行                             | ⚠️ 但**整文件被写成 CRLF**，见 12.3 |
| `src/application/persistence_wiring.py`                                         | −24 行                              | ✅                          |
| `src/models.py`                                                                 | −111 行（`class Flashcard`）          | ✅                          |
| `src/persistence/models/tables.py`                                              | −11 行（两个表规格）                       | ✅                          |
| `src/web/index.html`                                                            | −1（script 标签）                      | ✅                          |
| `src/web/knowledge.js`                                                          | −6（两处入口）                           | ✅                          |
| `src/web/styles.css`                                                            | −10（`.card-zh`）                    | ✅                          |
| 4 个 `scripts/*.js` + `tests/support.py`                                         | 各 −1（清单项）                          | ✅                          |
| `tests/test_production_gate.py` / `test_multi_course.py` / `test_student_ui.py` | 已同步数字                              | ✅（未验证）                     |

### 12.2 它**没做完 / 做坏**的部分（7 项，按严重度排序）

| #        | 缺陷                                                                                                                                                                        | 位置                                                                                                                                                                                                                                        | 后果                                                                                                                                      |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| **P0-1** | **Python 语法错误**：两行被并成一行                                                                                                                                                   | `src/persistence/repositories/__init__.py:107`<br />`self.study_plans = StudyPlanRepository(database)        self.learning_paths = LearningPathRepository(database)`                                                                      | **整个包 import 失败** ⇒ 应用起不来、**所有测试在 `conftest.py` 就挂**。实测：`pytest` 直接 `SyntaxError`                                                       |
| **P0-2** | 同一个文件里 `FlashcardRepository` 的 **import 被删了，但 `self.flashcards = FlashcardRepository(database)`（`:103`）还在**                                                               | 同上                                                                                                                                                                                                                                        | 修好 P0-1 后会立刻变成 **NameError**                                                                                                            |
| **P0-3** | `scripts/ui_audit.js` **整个脚本崩**：`WEB_FILES` 删了 `views/flashcards.js`，但 `:106` 的 `VIEWS_FLASHCARDS = viewSource('views/flashcards.js')` 还在 ⇒ `viewSource()` 返回 `undefined` | `scripts/ui_audit.js:102,106`                                                                                                                                                                                                             | `TypeError: Cannot read properties of undefined (reading 'code')` ⇒ **前端门禁全部失效**                                                        |
| **P1-4** | `i18n.js` 的 `fc.*` **只删了 es 一档**（zh 36-43、ca 1073-1080 完整留着）                                                                                                              | `src/web/i18n.js`                                                                                                                                                                                                                         | ① **三语 key 不对齐** ② 孤儿译文。实测 `ui_render_check.js` 报 `zh/es key parity` FAILED                                                             |
| **P1-5** | `ui_render_check.js` 的两条**旧断言没删**（计划书 §3.2-E-⑬ 要求删）                                                                                                                       | `scripts/ui_render_check.js:2665-2666,2690-2691`                                                                                                                                                                                          | 实测 FAILED：`#/knowledge still links to the flashcards page`、`#/knowledge list view keeps the per-row flashcard button` ⇒ **313/316**     |
| **P1-6** | `app.js` 的两个 `data-action` 派发分支**没删**，且指向**已不存在**的函数（`flashcards.js` 已被掏空）                                                                                                | `src/web/app.js:1496-1503`                                                                                                                                                                                                                | `actionCreateFlashcard` / `actionReviewFlashcard` 均**无定义** ⇒ 一旦触发即 `ReferenceError`。且那段代码被 `// removed by TASK-106` 注释切得很难看（虽然仍能 parse） |
| **P2-7** | **孤儿文件未清理**（计划书 §3.2-A 要求删）                                                                                                                                               | `src/web/views/flashcards.js`（掏空成 9 行空壳，**没删**）<br />`src/persistence/repositories/flashcard.py`（125 行，已无人 import）<br />`src/scheduling/fsrs.py`（302 行）<br />`tests/test_flashcards.py`（260 行）<br />`tests/test_fsrs_scheduling.py`（98 行） | ① 无守卫能抓"文件存在但未登记" ⇒ 会长期留成死代码<br />② 后两个测试引用的 API 已被删 ⇒ 修好 P0 后**必然报错**                                                                  |

### 12.3 行尾污染

`src/application/workspace.py` 被写成 **CRLF（4098 行）**，违反 `.gitattributes`  
（`*.py` 必须 **LF**）。git 已在警告：  
`in the working copy of 'src/application/workspace.py', CRLF will be replaced by LF`。  
**修的时候顺手统一成 LF**，别把行尾弄反（§10-4）。

### 12.4 m005 已上膛 —— D1 已确认，已按预期执行

`m005` **已经接进迁移链**（`migrations/__init__.py` 的 `_chain()` 返回 1→5）。

- **D1 已由用户拍板 = 方案 A**（§3.1），所以**不要摘链**。
- **预期行为**：修好 P0-1 的语法错误后，**第一次打开数据库就会执行 `DROP TABLE flashcards`
  / `flashcard_evidence`** —— 那 1 张卡永久消失。这是**被批准的**，不是事故。
- **接手者要做的**：执行完之后**复核一次**，确认库里确实没有这两张表了：

  ```bash
  PYTHONPATH=/d/Project/Clases "C:/Users/Rafae/AppData/Local/Microsoft/WindowsApps/python.exe" -c \
  "import sqlite3;c=sqlite3.connect('file:classroom-data/database/classroom.sqlite?mode=ro',uri=True);print([r[0] for r in c.execute(\"select name from sqlite_master where type='table' and name like 'flashcard%'\")]);print(c.execute('select max(version) from schema_version').fetchone())"
  ```

  期望：表名列表 `[]`、`schema_version` 最大版本 = **5**。
- 若用户之后改主意要保住数据 → 只能从动手前的备份恢复（迁移前已用 `VACUUM INTO` 打过快照，
  见 §12.6）。

### 12.5 接手者的最短路径（✅ 8 步已全部执行完毕）

1. 先修 **P0-1 / P0-2**（`repositories/__init__.py`）→ 确认 `python -c "import src.persistence"` 能过。
2. 修 **P0-3**（`ui_audit.js` 的 `VIEWS_FLASHCARDS` 与夹具、PAGES 项、约 12 条 check）→ 确认 `ui_audit.js` 能跑完。
3. 修 **P1-4**（i18n 三语 `fc.*` 全删）+ **P1-5**（删两条旧断言）→ 确认 `ui_render_check.js` 回到 OK。
4. 修 **P1-6**（删 `app.js` 两个派发分支与 `flashcardId` / `flashcardRating` 两个变量）。
5. 做 **P2-7**（删 5 个孤儿文件 —— 其中 `fsrs.py` 先按 §3.4 核对项③判定）。
6. 清 **§12.3** 的 CRLF。
7. 跑 §7.2 的定向测试 + §7.3 的两个前端审计，把**前后 check 数**记进 `docs/status.md`。
8. **D1 已确认（方案 A）** ⇒ `MIGRATION_005` **保持接在链上**。
   修完 P0-1 后第一次开库会执行 DROP（预期）；按 §12.4 的命令复核一次
   （表名列表为空 + `schema_version` 最大版本 = 5），把输出粘进 `docs/status.md`。

### 12.6 ✅ 收尾记录（2026-10-13，已完成）

§12.5 的 8 步**全部执行完毕**，实测数字：

| 步骤 | 结果 |
|---|---|
| 1 P0-1 / P0-2 `repositories/__init__.py` | 语法错误与悬空 `FlashcardRepository` 绑定已修；`import src.persistence` 通过 |
| 2 P0-3 `ui_audit.js` | 崩溃已修（删 `VIEWS_FLASHCARDS`、夹具、路由表项、`PAGES` 项、12 条 check）→ **OK (610 checks)** |
| 3 P1-4 / P1-5 | i18n 三语 `fc.*` 已删净；两条旧断言已删 → `ui_render_check.js` **OK (314 checks)** |
| 4 P1-6 `app.js` | `create-flashcard` 分支 + `flashcardId` / `flashcardRating` 两个悬空变量已删 |
| 5 P2-7 孤儿文件 | `flashcards.js` / `flashcard.py` / `test_flashcards.py` / `test_fsrs_scheduling.py` / `src/scheduling/` 全删 |
| 6 CRLF | `workspace.py` 已统一为 LF（`git diff --numstat` 前后都是 `2 138`，纯行尾） |
| 7 定向测试 + 前端审计 | **709 passed / 1 failed（环境）**；审计 check 数已记进 `docs/status.md` |
| 8 m005 复核 | `flashcard*` 表 = `[]`，`schema_version` 最大 = **5** ✅ |

定向 pytest 唯一那条 failed 是本机长期存在的**环境性**失败
（`test_student_ui.py::TestTheLiveSessionPickerEndToEnd::test_the_live_script_passes` ——
`scripts/e2e_session_picker.js:27` 硬编码的 `Python/pythoncore-3.14-64/python.exe` 不存在），
**失败的名字与改动前一致**，与 TASK-106 无关。

迁移前用 `VACUUM INTO` 打了快照（`temp/pre-m005-snapshot.sqlite`，不在版本控制里），逐表比对：
只有 `flashcards` / `flashcard_evidence` 消失、`schema_version` 4→5，其余 33 张表行数一字未变。

**计划书没预见到、实施时必须补的 5 处**（详见 `docs/status.md` 的 TASK-106 区块「额外发现」）：
`test_persistence_migration.py` 的两条守卫（`_APPROVED_DROPS` 例外 + 版本号 4→5）、
仓库根目录 3 个游离补丁脚本（`5c49df0` 用 `git add -A` 提交进来的）、`workspace.py` 的行尾。

**TASK-106 已完成**；**TASK-107 / TASK-108 仍未做** —— 但决策已全部拍板（§0.1），可以直接交接。
