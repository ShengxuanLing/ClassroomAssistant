# TASK-77 真实材料质量评价（人工检查，非自动测试）

日期：2026-09-21。材料：自拟 UAB 风格西语讲义《Cálculo II — Integración》
（12 句，覆盖 §9 全部 5 个概念）+ 真实 PDF（`tests/fixtures/documents/simple.pdf`，
Cálculo I，2 页，`pypdf` 实测）。
Provider：真实 OpenAI-compatible 模型（`agnes-3.0-flash`，via `CLASSROOM_AI_*`）。
以下判断均为人工阅读原文与 KP/证据后写下，**不是测试断言**。

## 1. 运行指标（§19 口径）

| 项 | TXT（自拟讲义） | PDF（真实文件） |
| --- | --- | --- |
| 文件大小 | 1,214 bytes | 1,363 bytes |
| 页数 | —（纯文本） | 2 |
| 处理耗时 | ~130 s | ~130 s |
| chunk 数 | 1 / 1 成功 | 2 / 2 成功 |
| AI 调用 | chunk 抽取 + 合并 + 总结（含 1 次重试预算，未触发） | 同左 |
| 作业状态 | SUCCEEDED | SUCCEEDED |
| AI 状态 | completed（auto 12 / review 0 / conflict 0） | completed（auto 3 / review 2 / conflict 0） |
| Evidence | 1 条 | 2 条 |
| 手工 KP | 0 | 0 |

全课程：KP 共 20，其中 AI 生成 17，Evidence 绑定 17/17，Review 队列 20（含确定性链路 KP）。

## 2. 逐条质检（抽 7 条，§34 七问）

### KP-1「Integración por partes」[es]（分部积分法）

1. 标题合理：是。2. 总结正确：是（基于乘积求导法则，原文如此）。
3. 真来自材料：是（第 1 句）。4. Evidence 正确：绑定到材料唯一证据，粗但对。
5. 重复：否（全库仅 1 个该标题的 `aikp-*`）。6. 遗漏：否，这是核心方法。
7. 幻觉：无。结论：**通过**。

### KP-2「Teorema fundamental del cálculo」[es]（微积分基本定理）

七问同上，全部通过。术语保留原文，中文解释由 UI 语言层承担，
Evidence 原文未被翻译。结论：**通过**。

### KP-3「Descomposición en fracciones parciales」[es]（部分分式分解）

七问通过。注意它与 KP-1 同属"方法型"，系统正确区分为两个独立 KP，
没有合成一条。结论：**通过**。

### KP-4「Regla de Barrow」[es]（Barrow 法则/牛顿-莱布尼茨公式在西语授课中的常用名）

标题是课程语境下的真实术语（原文第 12 句），非模型编造；
归类为核心概念正确。结论：**通过**。

### KP-5「Fecha del examen」[es]（考试日期，来自 PDF）

有 Evidence 支撑（syllabus 页原文），非幻觉；但属于**行政信息**，
不是可学习的知识点（§36：不要把非知识提升为正式知识）。
系统没有自动替用户删除它，而是以 `pending` 留在 Review 队列等人裁决——
这个处理是对的，但评价记一条**改进项**：行政型候选未来可默认进
Review（即使置信度高），见 §5。

### KP-6「Nota mínima」[es]（最低分要求，来自 PDF）

同 KP-5。结论：** grounded 但待人工拒绝，系统行为正确**。

### KP-7「Integral de x al cuadrado」[es]（例题衍生）

来自"Por ejemplo…"例句句。标题是合理的概念实例，但 §36 要求警惕
"把例题答案当通用知识点"。本例中模型抽的是概念本身而非答案数值，
可接受；且 `needs_verification=False`（HIGH 自动接受）。记一条
**观察项**：`example` 型候选即使高置信度，是否一律进 Review 更稳妥，
留作后续策略讨论，本任务不改阈值（§11 不重新发明）。

## 3. 总结质量（§35）

TXT 总结覆盖 6 种方法 + 定/反常积分性质 + 基本定理，与原文一一对应，
无虚构、无脱离证据、无术语翻译、无"口头语转正"。PDF 总结正确区分了
教学内容与行政信息（单独列出考试日期/最低分），没有把例子当结论。
结论：**通过**。

## 4. 已确认的局限（诚实记录，非失败项）

1. **证据粒度粗**：TXT 整篇只产出 1 条 Evidence（单 chunk），12 个 KP
   共享它。Trace 能到"材料级"，到不了"段落级"。PDF（2 条）与音频
   （timestamp 段）天然更细。这是 ingestion 切分策略的后续优化点，
   不是 AI 层幻觉——标题本身全部可溯源。
2. **全 HIGH 置信**：真实模型对本批短文本一律打高分，`review` 档只在
   PDF 出现 2 条。阈值策略（0.90/0.70）是确定性的，行为符合设计；
   长难材料上的分布有待后续真实学期数据观察。
3. **Fake 的 merge 回声**（`Return JSON:…` 标题 KP）只出现在 Fake 链路，
   真实模型未出现；系 TASK-76 既有行为，本任务不改（报告中有编号）。
4. **全部 `review_status=pending`**：TASK-76 映射把 auto 与 review 都记
   `pending`，以 `needs_verification` 区分。Review 队列因此包含自动项；
   读数时以管线报告的 auto/review/conflict 三数为准。本任务沿用，不另起
   第二套状态。
5. **耗时**：真实模型下每份材料约 130 s（含多次往返），同步架构下用户
   需要等待；失败可重试、材料本身先 READY，体验可接受。大课批量上传的
   排队策略是后续任务。

## 5. 总评

自动 KP 标题准确、定义正确、零幻觉、17/17 证据绑定、零手工创建。
行政/例题边缘项被诚实送入 Review 而不是悄悄丢弃或自动解决。
**真实材料验收：PASS（live 模型）**。
