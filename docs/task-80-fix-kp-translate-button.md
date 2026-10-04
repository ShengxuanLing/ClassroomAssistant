# TASK-80 · 修「翻译成中文」按钮点了没反应

> 状态：**COMPLETE**（2026-10-03）
> 性质：TASK-79 的**真机缺陷修复**。功能契约不变，坏的是链路。
> 非目标（明确不做）：不动 prompt / 长度上限、不动 FSRS、不动 DB、不做自动翻译、不改 m004。

---

## 1. 现象（用户实测，截图为证）

页面 `#/courses/<id>/knowledge/<kp>` → 中文解释区显示「还没有中文解释」+ 「翻译成中文」
按钮 + 术语对照表 0 条。

点按钮 → **无任何反应**：无 toast、无 loading、无网络请求。

对照组说明这不是"AI 没开 / 没证据"：

- 顶部 AI 绿灯（AI 已启用）；
- 溯源链正常显示「1 条证据 · 1 个源材料」。

即：**证据在、AI 在，只有按钮链路是断的。**

## 2. 根因（两层，先后关系）

### B1 —— 按钮的 `data-*` 是空的，点击直接 early-return（主因）

| 位置 | 事实 |
|---|---|
| `views/knowledge.js` `renderKpZhPanel` | 按钮身份取自 `report ? report.course_id : ''` |
| `views/knowledge.js` `loadKpZh` | 无缓存时 GET 返回 404 → `report = null` → 传入空串 |
| `app.js` 点击委托 | `target.getAttribute('data-course' / 'data-knowledge')` |
| `views/knowledge.js` `actionTranslateKp` | 首行 `if (!courseId \|\| !knowledgeId) return;` —— 静默返回 |

所以**「翻过一次的能点，没翻过的点不动」**：有缓存时 `report` 非空，恰好把参数喂对了；
无缓存时按钮渲染出 `data-course="" data-knowledge=""`，点击被首行吃掉，连请求都不发。

### B2 —— POST 传参与后端对不上（B1 修完立刻 400，必现第二跳）

- 前端 POST 只带 `body: {course_id, target_lang}`，**无 query**；
- `api.js` 里 query 与 body 是分开拼的 —— body 里的 `course_id` 永远不会出现在 URL 上；
- 后端 `kp_translate` 当时只有 `course_id = request.require_q("course_id")`（**单读 query**）。

对照：`create_flashcard` 用的是 `body.get(...) or request.require_q(...)` 双读，唯独本接口单读。

### 为什么测试全绿

`tests/test_kp_translation.py` 的 HTTP 用例全部写成
`POST .../translate?course_id=...`，与前端真实形状不一致 —— 断言覆盖的是"接口自己和自己说话"，
盖不住任何一侧的真实接线。

## 3. 改动

| # | 落点 | 改法 |
|---|---|---|
| T1 | `src/web/views/knowledge.js` | `renderKpZhPanel(report, glossary, courseId, knowledgeId)` 新增两个入参；按钮身份改用 `courseId \|\| report.course_id`。`loadKpZh` 与 `actionTranslateKp` 成功重绘处都透传自己的 `(courseId, knowledgeId)` |
| T2a | `src/web/views/knowledge.js` | POST 补 `query: { course_id: courseId }`，与同文件两个 GET 完全同口径 |
| T2b | `src/api/endpoints.py` | `course_id = str(body.get("course_id") or request.require_q("course_id"))`，与 `create_flashcard` 同口径 |

**T2 两端都做**：只修一端的话，另一端 + 旧缓存页面 / 只带 body 的外部调用继续坏。

### 顺手修掉的一个自伤点

失败恢复原本是 `button.outerHTML = previous`（TASK-79 遗留）。outerHTML 是一份**陈旧快照**：
它会把本任务修掉的 B1 变成永久故障 —— 一次失败之后按钮就再也点不动。改成用本次调用的
入参重写 `data-course` / `data-knowledge` 并还原文案：**按钮身份只由入参决定，不依赖 DOM 快照**。

### 不变量（逐条核对过）

- GET 仍**绝不**调 LLM（未动 `kp_translation_read` / `kp_glossary`）；
- 缓存身份与落盘路径未动（`kpzh-*` → `classroom-data/materials/ai-reports/<课>/kp-translations/`，经 `safe_join`）；
- 原文 / 证据 / FSRS / DB / prompt 一字未改。

## 4. 守卫

### `tests/test_kp_translation.py`（19 → 21）

1. `test_http_translate_accepts_course_id_in_body_only` —— 用**前端真实形状**（无 query，
   `course_id` 只在 body）打后端：200，且 `translation_identity` 与带 query 时**完全一致**
   （证明两种形状落同一份缓存，不是两个缓存）；顺带复查原文与证据逐字未动。
2. `test_http_translate_without_any_course_id_is_still_400` —— 双读不得把 `course_id`
   变成**可选**：两处都没有仍是 400 `INVALID_INPUT`，body 非 JSON 对象仍 400 不是 500。

第 2 条是给 T2b 上的枷：没有它，把 `require_q` 兜底删掉（"双读"退化成"随便读"）不会有任何红灯。

### `scripts/ui_audit.js`（556 → 558）

沙箱新增两样能力（都不是产品改动）：

- `__requests`：记下每个 fetch 的 method / body。`__fetched` 只有 URL，分不出
  GET `/translate` 与 POST `/translate` —— 而这两个正是要钉死的两种形状；
- `document.body`：`toast()` 往它挂节点。之前失败路径在审计里直接抛 TypeError，
  等于失败路径根本没法测。

两条新断言：

1. **无缓存态按钮带齐 id**：夹具抽掉 `/api/knowledge/<kp>/translate` 路由
   （fetchStub 自然 404，与真机"还没翻过"同路径），断言
   `button[data-action=kp-translate]` 的 `data-course` / `data-knowledge` **非空且等于当前课 / KP**。
2. **点一下真的会发生什么**：直接调 `actionTranslateKp(COURSE_ID, KP_ID, null)`
   （等价于委托转发），断言**唯一那条 POST** 的 URL 带 `course_id=`，且成功后
   面板真的重绘出中文 —— B1 与 B2 的联合证据。

### 变异自证（断言真的会红）

| 变异 | 结果 |
|---|---|
| M1 回退 T1（按钮 id 改回只从 report 取） | `UI audit FAILED (1/558)` — 详情里正是 `data-course="" data-knowledge=""` |
| M2 回退 T2a（POST 去掉 query） | `UI audit FAILED (1/558)` — 记录到的 POST 是 `/api/knowledge/kp-1/translate`，URL 上没有 course_id |
| M3 回退 T2b（后端改回单读 query） | `tests/test_kp_translation.py` 1 failed（body-only 用例） |

## 5. 复现与验收

复现（本机服务 + 任一**有证据未翻译**的知识点）：

1. 打开 `#/courses/<课>/knowledge/<kp>`，F12 看 `#kp-zh-panel button`：
   修前 `data-course="" data-knowledge=""`；修后等于当前课 / KP。
2. 点按钮：
   - 修前：无任何请求；
   - 修后：`POST /api/knowledge/<id>/translate?course_id=…` → 200 → 面板出现中文 + 术语表。

验收：

- [x] 无缓存态按钮 `data-*` 非空且可点；
- [x] 点击出现「翻译中…」，成功刷新为中文 + 术语；
- [x] 失败 toast 带 `[422]/[400]` + 原因，按钮恢复可重试；
- [x] 原文陈述与溯源链 `originalBlock` 一字未动（旧审计断言仍绿）；
- [x] `tests/test_kp_translation.py` 全绿；`ui_audit.js` 全绿（+2）；`ui_render_check.js` 不变。

## 6. 基线

```text
tests/test_kp_translation.py     21 passed                  ← 19
scripts/ui_audit.js              UI audit OK (558 checks)   ← 556
scripts/ui_render_check.js       UI RENDER CHECK: OK (314 checks)   不变
```

## 7. 风险与回滚

风险低：两处一行级改动，不碰 prompt / 缓存 / DB / 调度。回滚 = revert 本任务改动。

真正值得记的教训不是这两行，而是**测试形状必须等于调用方形状**：TASK-79 的 HTTP 用例
全部自己带 query，于是"前端只传 body"这一整类接线错误在测试里不可见。
这类缺陷只有真机能看见，而真机验证成本远高于把夹具改成真实形状。