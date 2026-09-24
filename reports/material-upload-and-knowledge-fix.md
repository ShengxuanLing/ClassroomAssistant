# Material Upload & Knowledge Fix

## 1. 当前问题

本报告记录当前工作树在 2026-09-24 的源码排查结果。材料上传和 AI 分析是现有功能，但任务坞、AI 候选校验和课堂标签各有独立缺口：

1. 任务坞会把完成卡片保留到内存数组中，且普通任务没有按目标去重。
2. AI 管线能取得结构化候选和证据引用，但未把“生成内容不得复制证据”设为硬校验；模型不遵守提示词时可能把原文落库。
3. API 已返回课堂真实日期，但材料上传选项和材料列表的显示函数没有把日期、星期、时间、类型和教室完整呈现。

## 2. 问题一：Notification 不消失

### Root Cause

`src/web/app.js::finishTask()` 只把 `task.status` 改成 `done` 或 `failed`，清除恢复轮询器并调用 `renderTaskDock()`；没有从 `tasks` 移除，也没有终态定时 cleanup。因此任务坞左下角会永久保留完成项。`startTask()` 只有传入 `poll.targetId` 时才去重持久化恢复记录，普通上传/AI 任务在当前页面中仍可重复登记，造成多个相同目标卡片。状态控制是 `running` 持续渲染；`done` 表示成功终态；`failed` 表示失败终态；`taskTerminalStatus()` 只判断服务端作业是否 `SUCCEEDED`/`FAILED` 等终态，不负责前端卡片删除。

### Relevant Files

- `src/web/app.js`: `startTask()`, `finishTask()`, `renderTaskDock()`, 恢复轮询。
- `src/web/views/materials.js`: 上传和 AI 操作登记任务的调用点。
- `tests/test_task_dock.py`: 任务坞行为测试。

### Current Flow

```text
上传/分析操作 -> startTask(running) -> API 请求 -> finishTask(done/failed)
                                              |
                                              +-- 当前只改 status + render
                                                  （不删除）
```

跨刷新任务通过 `targetId` 在 sessionStorage 中去重；普通页面内任务没有同等的目标键。

### Proposed Fix

为页面内任务增加稳定的 target key（已有 `targetId` 时使用它，否则由 label/kind/目标字段组成），登记时按 key 替换旧任务而不是追加。成功任务保留短暂可读时间后删除；失败任务保留更久，并保留失败详情与现有 retry 能力；运行中任务不删除。删除时同时清理该任务的 timer，避免定时器闭包持有已删除任务。

## 3. 问题二：Knowledge Point 等于原文

### Root Cause

当前 QGIS 数据实际可通过确定性 `kp-*` 路径产生；该路径把 Evidence 正文同时作为知识点正文，属于明确的内容混用。AI 路径的结构是 `KnowledgeCandidate(title, description, evidence_refs)`，通过 `ground_candidates()` 绑定真实 Evidence 后由 `map_candidate_to_kp_payload()` 写入 `KnowledgePoint`。但现有 grounding 主要检查引用存在、证据归属和置信度，不检查 `title`/`description` 是否复制 Evidence。故模型输出复制原文时仍可能以普通候选落库。

### AI Pipeline

```text
材料上传 -> 既有解析/文本提取 -> Evidence -> chunk
         -> AI structured candidates -> grounding
         -> title/content/evidence 分层 -> validation
         -> KnowledgePoint persistence -> API/UI
```

### Prompt Problem

Prompt 已要求概念化标题、自己的语言解释和独立 evidence，但这是模型指令，不是应用级硬约束；不能依赖模型必然遵守。

### Schema Problem

`KnowledgeCandidate` 的 `description` 与 `evidence_refs` 形状本身分离；问题在于缺少“生成内容与引用证据不得高度重叠”的语义校验，而不是字段名不存在。持久化 payload 也必须保持 `title/content` 与 evidence 分开。

### Fallback Problem

AI 失败应保留 Evidence 并报告失败/可重试，不能把原文作为成功的 AI 总结返回。现有 AI 失败异常和报告层应继续使用；确定性提取必须显式标记为确定性/fallback，而不是冒充 AI 抽象结果。

### Proposed Fix

在 AI grounding 前后增加应用级复制检测：规范化空白和大小写后，完全相等或异常高 token 重叠的 title/description 候选拒绝并记录 `reject_reason`；Evidence 仍可保留。Prompt 同步明确要求按概念合并、避免逐段生成。持久化只接受通过语义校验的 AI 候选；确定性路径的正文与证据角色分开标识。对 QGIS 等真实材料，验收必须读取 KnowledgePoint 和 Evidence，而不是只看 provider/model 字段。

## 4. 问题三：Session 没有日期

### Root Cause

课堂 API/模型已有 `date`、`session_number`、时间和 `title` 字段；材料页将 Session 映射成 `<option value=session_id>`，但显示调用 `sessionLabel()`，该函数优先拼接“第 N 堂 + title”，没有日期/星期。材料表也复用同一旧标签，因此关联 ID 稳定但日期信息不可见。材料未关联时当前使用短横线，未提供用户可读的“未关联课程”。

### Existing Schedule Data

课程创建/课堂 API 接受并返回真实日期；材料页按 `course_id` 请求 `/api/sessions`，用返回的 `session_id` 作为提交值。修复只能格式化已有 `date`，不得猜测或重算课程日期，也不改变后端 session ID。

### Proposed Fix

共享 Session 标签显示：本地化日期、星期、课节编号、起止时间、类型、教室；缺少字段时逐项省略。材料列表和上传下拉复用同一格式化函数；`<option value>` 仍传真实 `session_id`。无关联材料显示本地化“未关联课程”，不伪造 Session。

## 5. Files To Change

- `reports/material-upload-and-knowledge-fix.md`
- `src/web/app.js`
- `src/web/i18n.js`
- `src/web/views/materials.js`
- `src/application/ai/validators.py`
- `src/application/ai/prompts.py`（如现有提示词未明确表达摘要边界）
- `src/knowledge_pipeline.py`（若确定性正文与 Evidence 仍共用同一字段）
- `tests/test_task_dock.py`
- `tests/test_ai_understanding.py`
- `tests/test_student_ui.py`

## 6. Tests

- Notification：running 可见；done 短暂可见并自动删除；重复 target 不产生重复；failed 保留更久；timer cleanup。
- AI abstraction：QGIS 风格 source 的 title/content 不是完整原文；Evidence 保留原文；content 与 evidence.text 分离；复制候选被拒绝并有原因。
- Session display：日期、星期、编号、起止时间、类型、教室；提交值仍是原 session_id。
- Unassociated material：空 session_id 上传仍成功，显示未关联课程。
- Regression：聚焦测试、前端 `ui_audit.js`、完整非 integration 套件，以及仓库实际提供的编译/静态检查。

## 7. Risks

- 严格复制检测可能拒绝合法的短定义；阈值必须作用于规范化后的内容/证据，并有测试和明确 reject reason。
- Session 标题可能把类型/教室塞在自由文本中，解析失败时只能回退到真实字段并保留未解析部分，不能编造日期。
- 真实 AI 凭证、QGIS 文件或模型不可用时，只能报告离线/模拟验证，不能宣称真实模型通过。
- 工作树已有其他材料删除/分析 UI 改动；本修复不回退、不覆盖这些改动。

## 8. Verification

完成后记录：源码 diff、聚焦测试结果、`ui_audit.js`、完整测试、实际存在的编译/lint/build 命令，以及真实 QGIS 材料的 parse → AI → validation → persistence → API 页面证据。任何未执行或缺少凭证的项目明确标为未验证。

## 9. Implementation Result

待实施与验证后填写实际文件、测试数字、真实 QGIS 结果和剩余限制。
