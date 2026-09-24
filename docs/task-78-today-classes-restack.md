# Task 78 — 今日课程卡改用两行 session-row

日期：2026-09-24

## 目标

`#/today` 的「今天的课程」位于半宽 `grid-2` 卡片内。旧版使用 4 列表格：

- 课堂
- 状态
- 知识点
- 待审核

窄卡片与 `overflow-wrap: anywhere` 叠加后，中文表头会逐字竖排，种子标题
`15:00–17:00 Teoria | Dario Cottava | Aula Q2/1009` 也会被挤成多行。问题不是
颜色或局部 padding，而是表格结构本身允许列无限变窄。

本次把该区域改成课程详情页已经过两轮验证的 `.session-row` 版式，从结构上移除
表格与表头。

## 实现

### 1. 标题解析器只保留一份

`parseSessionTitle()` 与 `SESSION_TITLE_PLACEHOLDER` 从 `views/courses.js` 原样上移到
`app.js`。`app.js` 比各页面视图先加载，因此课程页和今日页均调用同一实现；没有第二份
正则，也没有第二份占位符文案。

`Aulas?` 教室识别正则保持逐字不变：

```js
/^Aulas?(?![A-Za-z0-9])/i
```

### 2. 今日课堂改为两行卡片行

`pageToday()` 新增：

- `todaySessionRow(courseId, session)`
- `todayClassesCard(rows)`

每行结构：

```text
时间 + 类型徽章
教师 · 教室 + 状态 pill
材料 / 知识点 / 待审核（仅非 0 时另起一行）
                                      整堂处理
```

复用现有 `.session-day-card` / `.session-row` / `.session-row-main` /
`.session-row-action` 样式；未新增 CSS 设计语言。

### 3. 分组与链接保持

- 全部课程口径继续按课程分组，课程名仍由 `.today-group > h3` 显示。
- 单课程筛选继续省略重复的课程名组头。
- 每行链接仍为 `#/courses/<course_id>/sessions/<session_id>`。
- 「整堂处理」按钮继续使用 `data-action="process-session"`，并带齐
  `data-course` / `data-session`。
- 按钮是链接的兄弟节点，不嵌套在 `<a>` 内；既有委托与 `confirmDestructive` 语义不变。
- 今日页不再逐行重画日期；页副标题已明确给出当天日期。

## 守卫

### `scripts/ui_audit.js`

新增运行期渲染断言，覆盖：

1. 今日课程区存在 `.session-day-card`；
2. 今日课程区不存在 `<table>`；
3. 行数等于真实课堂行数；
4. 全部课程分组课程名仍存在；
5. 每行链接包含完整 course/session 两段；
6. 每个处理按钮均带 course/session；
7. 按钮在锚点闭合标签之后，是锚点兄弟节点；
8. 每行均有两层结构 `session-row-top` / `session-row-sub`；
9. 种子标题被解析，不直接显示 pipe 分隔原串；
10. `图中未显示` 不渲染；
11. 非 0 计数显示在全 0 行之外；
12. 旧表头计数列不再存在；
13. 空课堂显示 `today.noSessions`；
14. 单课程口径仍成卡且无组头；
15. es/ca 输出无未翻译 CJK。

### `scripts/ui_render_check.js`

新增：

- 今日课程渲染出 session row；
- 深链接与 process-session 属性保留；
- `classes_today: []` 显示 `today.noSessions`，不生成空卡；
- 单课程筛选仍成卡、保留处理动作。

### `tests/test_web_ui_invariants.py`

新增结构不变量：今日课堂渲染体必须同时包含 `session-day-card` / `session-row`，
且不得包含 `<table>` / `class="data"`。

## 验证结果

### 自动门禁

```text
node scripts/ui_audit.js
  UI audit OK (517 checks)

node scripts/ui_render_check.js
  UI RENDER CHECK: OK (314 checks)

node --check src/web/app.js
node --check src/web/views/dashboard.js
node --check src/web/views/courses.js
  PASS

pytest -q tests/test_web_ui_invariants.py
  12 passed

pytest -q -m "not integration" \
  tests/test_student_today.py \
  tests/test_web_ui.py \
  tests/test_web_ui_invariants.py \
  tests/test_multi_course.py \
  tests/test_exercise_ui.py \
  tests/test_learning_view.py
  520 passed in 144.36s
```

### 真实服务与浏览器

使用本机真实服务 `http://127.0.0.1:8765`、真实课程数据、Chromium 布局引擎，
在 `#/today` 实测：

- 1280 × 1200：今日课程卡 `clientWidth = 468`, `scrollWidth = 468`；3 行均无
  横向溢出；页面无横向溢出。
- 520 × 1400：今日课程卡 `clientWidth = 462`, `scrollWidth = 462`；3 行
  `scrollWidth == clientWidth`，处理按钮仍在卡片内；卡片内部无横向溢出。
- 深色与浅色各检查一版，版式一致；浅色、深色均无控制台错误。
- 真实数据：3 节课、2 个课程组、3 个 session-day-card、3 个 session-row、
  3 个完整 session 深链接、3 个处理按钮，按钮均不在锚点内。
- DOM 文本中无原始 `|`，无 `图中未显示`；今日课程区 table 数为 0。

> 520px 整个文档仍有既有顶栏健康 pill 导致的横向溢出（`document.scrollWidth` 大于
> `clientWidth`），但今日课程卡与其三行的溢出量均为 0。该顶栏问题不属于 Task 78，
> 本次没有扩范围修改。

## 未完成 / 限制

- 全量 `pytest -m "not integration"` 已启动，但在工具 600 秒上限到达前未返回结果；
  不能把它记为通过。相关 520 条子集已通过。
- 本次没有新增 i18n key，继续复用 `材料` / `course.knowledge` /
  `course.pendingReview` / `未命名课堂` / `整堂处理`。
- 后端、数据库、迁移、复习/练习/评估/关注卡片、材料页与课堂详情表格均未改。
