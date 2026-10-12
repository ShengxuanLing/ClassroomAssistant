# TASK-KM-PREMIUM-EXECUTION — 交接状态报告（Handoff）

> 生成目的：上一个 agent 在**阶段 A 核查基本完成、尚未进入修复与原型验证**时被要求停止。
> 本报告记录**已确证的事实**、**唯一的核心阻塞**、以及**下一步该做什么、按什么顺序**。
> 下一个 agent 请先读完本报告再动手。**不要再重复前期的产品研究与 M0–M3 文档工作。**

- 报告日期：2026-10-11
- 分支：`main`（另有一个归档分支 `feature/km-ui` 指向旧的被回滚的 V2 实现 `f744dbf`，**不要**用它）
- 任务原文见用户附件 `TASK-KM-PREMIUM-EXECUTION`。

---

## 0. 一句话结论（TL;DR）

**Knowledge Map Premium 的前端与后端骨架已经写完了，而且架构是对的、不伪造数据；
但它此刻是一个"技术上能渲染、但界面上全是内部 key"的坏版本 —— 与当初导致 V2 被回滚的
缺陷一模一样：`knowledge.js` 用了 37 个 `km.*` i18n key，而 `i18n.js` 里一个都没定义，
且 `t()` 只接收 1 个参数、忽略了插值第二参。**

**唯一的 P0 阻塞 = 补齐 i18n。** 补完之后，原型/正式版的绝大部分就已经能跑。
后端只读投影端点**已存在且已注册**，无需新增。

---

## 1. 已确证的事实（Ground Truth）

### 1.1 后端：已完成，可用，且不伪造数据 ✅

- **HTTP 端点真实存在且已注册**：`src/api/endpoints.py:732`
  ```python
  router.get("/api/courses/{course_id}/knowledge-map", knowledge_map)
  ```
  `knowledge_map(request)` → `workspace.knowledge_map(course_id)`。
- **投影方法**：`src/application/workspace.py:2855` `Workspace.knowledge_map(course_id)`。
  返回结构（**关键**：topics / memberships 恒为空数组，relations 取自 `deps.outgoing`，
  conflicts 真实读取 —— 没有任何编造）：
  ```python
  {
    "course_id", "knowledge_points", "materials",
    "evidence_counts",            # {kp_id: 证据条数}
    "material_to_knowledge",      # {material_id: [kp_id, ...]}  反向索引，来自 evidence.material_id
    "topics": [],                 # 真实为空
    "knowledge_memberships": [],  # 真实为空
    "relations": deps["outgoing"],# 当前数据下预计为空
    "conflicts": [...],
    "stats": {knowledge_point_count, material_count, evidence_count,
              topic_count, relation_count, conflict_count},
    "summary": {...},
  }
  ```
- 这两个文件（`endpoints.py` / `workspace.py`）**已提交**（不在 `git status` 改动列表里），
  说明后端部分是之前就落盘的，不是本次未提交的草稿。
- **注意**：任务里假设的 `merge_proposals` 表**不存在**；合并/审核语义在 `conflicts` /
  `course_conflicts` / `course_review_records`。后端已按真实表读取，方向正确。

**结论：不需要新增任何后端接口。** 前端单次 `GET /api/courses/{id}/knowledge-map`
即可拿到画布需要的全部数据（此前要拼 6~7 个请求）。

### 1.2 前端：架构完整，委托已接线 ✅（除 i18n 外无其它结构性缺陷）

改动集中在 4 个文件（见 §5）：
- `src/web/views/knowledge.js`（+445 行）：KM 页全部实现。
- `src/web/styles.css`（+75 行）：`.km-*` 样式块。
- `src/web/app.js`（+2 行）：路由分支 + 委托分发。
- `src/api/router.py`（+2 行）：路由注册（可能是 CRLF 噪音，见 §5）。

已确证接线的部分：
- **路由**：`app.js:1576`
  `else if (parts[0]==='courses' && parts.length===3 && parts[2]==='knowledge-map') await pageKnowledgeMap(parts[1]);`
  → 即 `#/courses/<id>/knowledge-map`。
- **委托分发**：`app.js:1490`
  `else if (action.indexOf('km-') === 0) { kmAction(action, target); }`
  → 所有 `km-set-view` / `km-search` / `km-clear-search` / `km-filter-material` 都由
  `knowledge.js` 里的 `kmAction()` 统一处理。**这条已通，不是缺陷。**
- **知识点详情**：复用既有 `#/courses/<c>/knowledge/<kid>`（`pageKnowledgeDetail`），
  **没有**新建侧滑抽屉。任务 B2 的"Detail Panel"由此满足（跳到既有详情页），可接受。

`knowledge.js` 内部结构（供定位）：
- `KM_STATE = {courseId, knowledge[], materials[], clusters{}, orphans[], conflicts[], stats, view, query, activeMaterial, ...}`
- `kmLoad(id)` → `api('courses/{id}/knowledge-map')` → 填 `KM_STATE` → `kmBuildClusters()` → `kmRender(id)`
- `kmBuildClusters()`：用 `material_to_knowledge` 建"材料→kp"反向索引进 `KM_STATE.clusters`，并收集 `orphans`
- `kmRender()`：按 `KM_STATE.view` 分发到 `kmRenderExplore / kmRenderList / kmRenderMaterials`
- 探索视图 = "按材料分簇"卡片流（`kmClusterCard` per material + `kmOrphanCard`），
  卡片头显式标注"仅界面分组 · 非课程层级"（`km.uiOnlyGrouping`）—— 这正符合任务
  "不得把 UI 分组伪装成真实层级"的铁律。
- 控件：`kmToolbar()`（受控搜索框 + 视图切换 `aria-pressed` + 来源筛选下拉）、
  `kmStatsBar()`、`kmConflictBanner()`。

### 1.3 唯一 P0 阻塞：i18n 未定义 + 插值被忽略 🔴

实测（`temp/km_probe_final.py`）：
- `knowledge.js` 引用了 **37 个** `km.*` 字面 key（含动态前缀 `'km.view.'`）。
- `i18n.js` 中定义的 `km.*` key = **0**（`I18N` 与 `TRANSLATIONS` 两张表都没有）。
- `t()` 签名为 `function t(key)` —— **只收 1 个参数**。
  但 `knowledge.js` 里有 `t('km.evidenceCount', { n: evCount })`（`:680`）、
  `t('km.supportCount', { n: ids.length })`（`:805`）这类**两参数插值调用**，
  第二个参数被**静默忽略**。

**后果**：KM 页在**所有语言**下都会渲染出原始内部 key（"km.title"、"km.statKnowledge"…），

---

### 1.4 附带发现：句子 key `路径` 未翻译

`knowledge.js:813` 用了 `t('路径')`（CJK 句子 key），但 `路径` 在 `TRANSLATIONS.es` / `.ca`
里**都不存在** → 会触发 `test_sentence_keys_are_translated_in_both_languages` 失败。
（大概率是 KM 代码新引入的，需补 es/ca 译文，或改成符号 key。）

---

## 2. 必然会红的测试守卫（当前树跑不过自家测试）

`tests/test_exercise_ui.py::TestI18nTables`（`test_learning_view.py` 有镜像）会红：
- `test_every_t_key_is_defined`（:1364）—— 37 个 `km.*` 悬空 key。
- `test_sentence_keys_are_translated_in_both_languages`（:1381）—— `路径` 漏译。

> 也就是说：**现在这份工作树连自己的测试套件都过不了。** 下一个 agent 建立"实施前基线"时，
> 预期这两条（及其镜像）为红，其余应保持既有绿。修完 i18n 后它们应变绿。
## 3. 完整 key 清单（37 个 km.* + 动态 km.view.* + 句子 路径）

下表的"建议中文"由调用点上下文推断，供直接落地；es/ca 为**尽力译文，需母语者复核**。
所有 `km.*` 符号 key 必须**同时**进 `I18N.zh / I18N.es / I18N.ca`（三张表 key 必须完全一致，
见 `test_all_three_tables_have_identical_keys`）。句子 key `路径` 只进 `TRANSLATIONS.es/ca`。

| key | 建议中文 | es（待复核） | ca（待复核） | 备注 |
|---|---|---|---|---|
| km.title | 知识地图 | Mapa de conocimiento | Mapa de coneixement | 页标题 |
| km.subtitle | 按来源探索知识点 | Explora los puntos por origen | Explora els punts per origen | 副标题 |
| km.statKnowledge | 知识点 | Puntos de conocimiento | Punts de coneixement | 统计条 |
| km.statClustered | 已归类 | Agrupados | Agrupats | |
| km.statOrphans | 未归类 | Sin grupo | Sense grup | |
| km.statEvidence | 条证据 | evidencias | evidències | |
| km.statMaterials | 份材料 | materiales | materials | |
| km.conflictTag | 冲突 | Conflicto | Conflicte | |
| km.conflictNote | {n} 处冲突待处理 | {n} conflictos pendientes | {n} conflictes pendents | 含 `{n}` |
| km.conflictLink | 前往审核 | Ir a revisión | Anar a revisió | 链到 `#/courses/<c>/review` |
| km.searchLabel | 搜索知识点 | Buscar puntos de conocimiento | Cerca punts de coneixement | a11y label |
| km.searchPlaceholder | 搜索知识点… | Buscar puntos de conocimiento… | Cerca punts de coneixement… | placeholder |
| km.clearSearch | 清除 | Borrar | Esborrar | |
| km.filterAll | 全部来源 | Todos los orígenes | Tots els orígens | |
| km.filterSource | 按来源筛选 | Filtrar por origen | Filtrar per origen | a11y label |
| km.view.explore | 探索 | Explorar | Explorar | 动态 `km.view.`+name |
| km.view.list | 列表 | Lista | Llista | 动态 |
| km.view.materials | 材料 | Materiales | Materials | 动态 |
| km.viewLabel | 视图 | Vista | Vista | a11y group label |
| km.legendTitle | 图例 | Leyenda | Llegenda | |
| km.legendGrouping | 按来源分组 | Agrupado por origen | Agrupat per origen | |
| km.legendNoRelations | 暂无知识关系 | Sin relaciones de conocimiento | Sense relacions de coneixement | |
| km.legendClick | 点击查看详情 | Clic para ver detalles | Clic per veure detalls | |
| km.colSource | 来源 | Origen | Origen | 列表表头 |
| km.evidenceCount | {n} 条证据 | {n} evidencias | {n} evidències | 含 `{n}`，两参调用 |
| km.noEvidence | 暂无证据 | Sin evidencia | Sense evidència | |
| km.clusterEmpty | 暂无知识点 | Sin puntos de conocimiento | Sense punts de coneixement | |
| km.unlocated | 未定位 | Sin ubicar | Sense ubicar | 孤儿卡标题 |
| km.unlocatedNote | 这些知识点尚未关联到任何材料 | Estos puntos aún no se asocian a ningún material | Aquests punts encara no s'associen a cap material | |
| km.uiOnlyGrouping | 仅界面分组 · 非课程层级 | Agrupación solo de interfaz · no es jerarquía del curso | Agrupació només d'interfície · no és jerarquia del curs | **重要**：防误读 |
| km.supports | 支撑的知识点 | Puntos que respalda | Punts que respon | Material Map |
| km.supportsHint | 来自证据来源的反向关联 | Asociación inversa desde el origen de la evidencia | Associació inversa des de l'origen de l'evidència | |
| km.supportsNone | 暂无知识点引用此材料 | Ningún punto cita este material | Cap punt cita aquest material | |
| km.supportCount | {n} 个知识点 | {n} puntos de conocimiento | {n} punts de coneixement | 含 `{n}`，两参调用 |
| km.courseEmpty | （空课程态） | （空态） | （空态） | 需按用途定稿 |
| km.noMaterials | （无材料态） | （空态） | （空态） | |
| km.searchEmpty | （无搜索结果态） | （空态） | （空态） | |
| km.needCourse | 需要选择课程 | Se requiere un curso | Cal un curs | 错误态 |
| km.backToCourse | 返回课程 | Volver al curso | Tornar al curs | |

**动态前缀 `km.view.` 的处理（二选一，推荐 A）**：
- **A（推荐）**：把 `knowledge.js` 里 `['explore','list','materials'].map(name => t('km.view.'+name))`

---

## 4. 下一步：按顺序执行

### 4.1 步骤 1（P0，必须先做）：补齐 i18n
1. 升级 `t()` 支持插值（见 4.2）。
2. 在 `i18n.js` 的 `I18N` 三张子表（zh/es/ca）各加 §3 的 `km.*` 符号 key，**key 集合三语一致**。
   若采用动态前缀方案 A，同时把 `knowledge.js` 的 `'km.view.'+name` 改成显式三键。
3. 在 `TRANSLATIONS.es / .ca` 补 `路径`（及任何其它漏译句子 key）。
4. 跑 `pytest tests/test_exercise_ui.py::TestI18nTables tests/test_learning_view.py -q`
   确认 §2 的两条转绿、且没有引入新的悬空 key / 重复 key / 三表不一致。

### 4.2 步骤 1 附：`t()` 升级（推荐方案）
现状 `i18n.js:1838` `function t(key) {...}`。建议改为支持 `{name}` 占位符替换，
一次性兼容 4.1 里两种调用写法（`.replace('{n}',…)` 可保留也可改）：
```js
function t(key, vars) {
  const table = I18N[state.lang] || I18N.zh;
  let out;
  if (Object.prototype.hasOwnProperty.call(table, key)) out = table[key];
  else if (Object.prototype.hasOwnProperty.call(I18N.zh, key)) out = I18N.zh[key];
  else if (TRANSLATIONS[state.lang] &&
           Object.prototype.hasOwnProperty.call(TRANSLATIONS[state.lang], key)) {
    out = TRANSLATIONS[state.lang][key];
  } else {
    return key; // 第 3 条刻意保留: 缺译退化, 不显示内部 key 之外的意外
  }
  if (vars && typeof out === 'string') {
    out = out.replace(/\{(\w+)\}/g, (m, name) =>
      Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : m);
  }
  return out;
}
```
> 改完务必跑 §2 的 i18n 守卫 + 现有依赖 `t()` 的渲染检查（`scripts/ui_render_check.js`、
> `scripts/ui_audit.js`）确认无回归。`t()` 是全局热点，改动要克制、只加能力不改语义。

### 4.3 步骤 2：建立真实基线 + 启动服务实测 API
- **基线测试**（任务要求）：`pytest -m "not integration"`（或 `run_tests.cmd`）。
  预期仅 §2 的 i18n 几条为红（修复前），其余绿。记录 passed/failed 数。
- **启动服务**：`python -m src.application.launcher start`（`src/application/launcher.py:611`）。
  端口写在 `classroom-data/.run/launcher.port`，默认 host `127.0.0.1`。
  （根目录**没有** start.bat，只有 `run_tests.cmd`。）
- **实测端点**（任务 A1 要求留证据）：
  `curl http://127.0.0.1:<port>/api/courses/course-32dde014868219be/knowledge-map`
  核对：`knowledge_points` 数量（基线 61）、`materials`（2）、`evidence_count`（6）、
  `topics/knowledge_memberships` 为 `[]`、`relations` 实际值、`conflicts` 实际值、
  HTTP 200、空数组/缺失字段不报错。
  另测：不存在的 course_id → 预期 4xx 且不崩；空课程 → 返回 0 计数而非 500。
- 把实测结果写进 `reports/knowledge-map-redesign-audit.md`（**已存在，追加，勿新建重复文档**）。

### 4.4 步骤 3：真实浏览器验收（任务 B3 / 第五节）
用无头浏览器（本仓库既有做法：Edge `--headless --dump-dom`，**必须**配独立
`--user-data-dir`，否则单实例转发输出 0 字节）访问
`#/courses/course-32dde014868219be/knowledge-map`，验证并截图：
初始探索视图 / 搜索定位 / 知识详情跳转 / Evidence 展示 / 材料导航 /
Material Map / List View / 视图切换 / 空态（无证据、无材料、无搜索结果）/ 窄屏。
重点确认 §1.3 修复后**不再出现裸 `km.*` key**。

### 4.5 步骤 4：性能与多语言（任务 C4/C5）
- 61 真实节点 + 合成 200/500/1000 节点验证架构扩展（不要求全部常显）。
- 确认选择/hover/开详情**不触发**整图重算（当前是字符串拼接渲染，需评估）。
- es/ca 界面无 CJK 裸串（已有 `scripts/ui_audit.js` 扫描，可能需更新计数基线）。

### 4.6 步骤 5：最终报告
写到 `reports/knowledge-map-premium-final-report.md`（**尚不存在，届时新建**），
按任务第六节 20 项逐条填，状态只用 `PASS / PARTIAL / BLOCKED`，未实测标 `NOT VERIFIED`。

---

## 5. Git 状态澄清（避免误判）

- `git status` 显示约 50 个文件 `M`，但**绝大多数是 CRLF→LF 行尾噪音**
  （git 警告 "CRLF will be replaced by LF"；每个仅 1~2 行 diff）。
  **不要**以为是整个代码库被重写。
- **真实 KM 改动只有 4 个文件**：
  `src/web/views/knowledge.js`(+445)、`src/web/styles.css`(+75)、
  `src/web/app.js`(+2)、`src/api/router.py`(+2，可能也是行尾噪音，用
  `git diff src/api/router.py` 确认是否是真实路由注册还是纯行尾)。
- 后端 `endpoints.py` / `workspace.py` 已提交（含 knowledge-map 端点与方法）。
- 归档分支 `feature/km-ui` = 旧 V2（`f744dbf`），**忽略**。
- 根目录有遗留垃圾文件：`pytest_out.txt` / `pytest_test.txt` / `result2.txt`（未跟踪），
  可清理；`temp/` 下有多个一次性探针脚本（`km_probe*.py`、`patch_*.py`、`fix_*.py`），
  属于工作痕迹，交付前应清理或移入 `scripts/dev-archive/`。

---

## 6. 风险 / 待确认 / 未验证（NOT VERIFIED）

| # | 事项 | 状态 |
|---|---|---|
| R1 | `router.py` +2 是真实路由注册还是 CRLF 噪音 | 待 `git diff src/api/router.py` 确认 |
| R2 | 真实 HTTP 响应结构、数量、错误处理 | **未实测**（服务未启动），需按 §4.3 补 |
| R3 | 空 relations / 空 conflicts / 空 topics 下前端不崩 | 代码路径已写，**未浏览器实测** |
| R4 | es/ca 译文质量（§3 为尽力译） | **需母语复核** |
| R5 | 1000 节点性能 | **未测** |
| R6 | `km.courseEmpty/noMaterials/searchEmpty` 的中文定稿 | 推断，需按用途定稿 |
| R7 | 任务 A2 要求的"旧版 V2 引用全清单"（`KM_VIEW/kmState/kmLayout/kmViewModel/kmFit` 等） | **未完成**，见下 |

**A2 补充说明**：本次核查发现 `km.*` / `km-*` 引用**只**出现在新的 `knowledge.js`
（在用）与 `i18n.js`（0 处），未发现旧 V2 的 `kmState/kmLayout/kmViewModel/kmFit/KM_VIEW`
残留在生效代码中（它们随 V2 回滚一并删除，仅存于归档分支）。但任务 A2 要求的**逐项分类清单**
（在用/遗留/通用/测试专用/文档/死代码）尚未系统产出，下一个 agent 若要交付完整审计，
需补一次全仓 `git grep` 并落到 `reports/knowledge-map-redesign-audit.md`。

---

## 7. 成功标准（提醒，来自任务第八节）

> 不是"页面出现 61 个节点"，而是：**用户能快速找到一个知识点、理解它、看到真实证据、
> 追溯到相关材料，且整体视觉专业一致清晰。**

当前实现的方向（按来源分簇 + 全局搜索 + 列表 + 材料地图 + 复用既有详情页 + 明确标注
"UI 分组非层级"）**符合**该标准；只差 i18n 这一口气就能变成可用产品。补完 §4.1 即可进入
浏览器验收与正式化。

  改成显式三键 `t('km.view.explore')` 等。`km.view.*` 是**纯前端**概念，
  而 `tests/support.py::dynamic_i18n_prefixes()` 的设计意图是"从**后端常量**导入取值域"，
  不该为它污染那张表。
- B：在 `dynamic_i18n_prefixes()` 注册 `'km.view.': ('explore','list','materials')`。可用但不推荐。

> `路径`（句子 key）：进 `TRANSLATIONS.es = 'Ruta'`、`.ca = 'Ruta'`（与材料页既有译法一致，需核对）。

而不是人话。这正是 V2 被回滚的同一颗雷。

**插值口径不统一（同一文件里两种写法）**：
- `:618` `t('km.conflictNote').replace('{n}', String(...))` —— 手动 `.replace`，
  **只要 key 有定义且值里含 `{n}` 就能工作**。
- `:680 / :805` `t('km.evidenceCount', { n })` —— 期望 `t()` 自己做插值，
  **但 `t()` 不支持**。

→ 必须二选一统一（推荐升级 `t()`，见 §4.2）。

