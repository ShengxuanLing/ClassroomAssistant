# 界面设计重构（2026-10-14）

- 范围：`src/web/styles.css`（重写为 token 化设计系统）、`src/web/app.js`（顶栏实测高度 + 顶栏归属表接线不变）、
  `scripts/ui_audit.js`（新守卫 + `--preview --page`）、本文件
- 状态：已交付，含真实浏览器验证与变异测试
- 基线：`ui_audit.js` 627 条 → **676 条**（全过）；`ui_render_check.js` **312/314**（2 条红色为**既有**路由缺陷，见 §7）

---

## 1. 参考了什么，怎么用

用户给的参考是 [`VoltAgent/awesome-design-md`](https://github.com/VoltAgent/awesome-design-md) —— 它是一份
**DESIGN.md 目录**（每个条目是某个真实产品的设计语言拆解），README 本身**不给**统一的设计规范，
它的用法是"挑一份 DESGIN.md，让 AI 按那套语言做页面"。

它给出的**体例**（Stitch 格式，9 节）被本项目直接借用，成为本文件与 `docs/knowledge-map-design-system.md` 的组织方式：

1. 视觉主题与氛围 2. 颜色与角色 3. 排版规则 4. 组件样式 5. 布局原则
6. 层次与投影 7. 该做/不该做 8. 响应式 9. 给 Agent 的提示词

在目录里挑语言时，本项目**不适合**照搬任何单一品牌：分析型工具（Linear/Vercel/Sentry 那类 dashboard）、
阅读型产品（Mintlify/Notion）、多语言学术材料（中文 + 西语 + 加泰语）三者的约束同时成立。所以最终取的是**做法**而不是皮肤：

| 借用的做法 | 出处（目录条目） | 在本项目落到哪 |
|---|---|---|
| 层级靠字号 / 间距 / 留白，不靠颜色；边框极细 | Linear、Vercel | `--fs-*` 五档比例、`--sp-*` 4 的倍数刻度、`--border` 一根 hairline 取代"每卡一投影" |
| 阅读面是"一张纸"，不是"浮起来的盒子" | Mintlify、Notion | `.card` 去掉 `box-shadow`，投影只留给弹窗 / toast / 任务坞 |
| 语义状态 = 软底 + 同色系细线 + 饱和文字（chip） | Radix、Sanity 的 coral-red 单一强调色策略 | `--*-soft` / `--*-line` 成对出现，`.pill-*` 不再用饱和色描边 |
| 顶栏半透明 + 背景模糊，而不是一条硬阴影 | Raycast、Vercel | `--topbar-bg` + `backdrop-filter` |
| 焦点态是设计的一部分（键盘可达） | Linear（键盘优先） | 全局 `:focus-visible` 焦点环 |

明确**没有**采纳：任何满屏渐变、玻璃拟态卡片、发光边框、巨大宣传字、动效炫技 ——
项目自己的设计文档（`docs/knowledge-map-design-system.md` §1）已经把这些列为禁止项，本轮沿用该结论。

---

## 2. Token 层（本轮新建的唯一"新东西"）

`styles.css` 顶部现在是唯一取值来源，组件里不再出现魔数：

| 组 | token | 说明 |
|---|---|---|
| 颜色角色 | `--bg` `--surface` `--surface-2` `--surface-3` `--border` `--border-strong` `--text` `--text-muted` `--text-soft` | `--surface-3`（悬停面）与 `--text-soft`（占位/极弱提示）此前**只在设计文档里存在**，实现里缺失 |
| 语义色 | `--ok/--warn/--bad/--info` + `--*-soft` + `--*-line` | `--*-line` 是本轮新增的"chip 细线"档 |
| 强调色 | `--accent`（文字/边框/焦点环）、`--accent-soft`（软底）、`--accent-ink`（软底之上的文字）、`--accent-line`、`--accent-hover` | `--accent-ink` 的存在理由见 §5 |
| 前景对 | `--on-accent` `--on-ok` | 沿用 2026-09-21 的修法（深色模式下强调色是浅色，"压在上面的字"必须另给） |
| 排版 | `--fs-2xs/--fs-xs/--fs-sm/--fs-base/--fs-md/--fs-lg/--fs-xl` + `--lh-tight/--lh-body` | 11 / 12 / 13 / 15 / 14 / 16 / 24 px |
| 间距 | `--sp-1 … --sp-7` | 4 / 8 / 12 / 16 / 24 / 32 / 48 px |
| 圆角 | `--radius-xs/sm/--radius/--radius-lg/--radius-pill` | 6 / 8 / 12 / 16 / 999 px |
| 高度层级 | `--shadow-1/2/3`（`--shadow` = 2 兼容旧引用） | 只有三层，且卡片默认**不是**任何一层 |
| 动效 | `--dur-1`(120ms) `--dur-2`(180ms) `--ease` | 设计文档 §7.3 的 120–240ms 下沿 |
| 布局 | `--shell-max` `--sidebar-w` `--topbar-h` `--sticky-top` `--rail` | `--topbar-h` 由 JS 实测写入 |
| 画布 | `--graph-*` | 沿用 2026-10-13 的双主题取值，**一字未改**（canvas 像素不继承 CSS） |

**深色不是"把浅色反过来"**：底色压到 `#101317`，表面按 4% 亮度逐级抬升（`#171a1f / #1e222a / #252a33`），
边框改用半透明白（纯灰 1px 线在深底上显得比在浅底上重）。两张主题表都要给出表面三档（有守卫，见 §6）。

---

## 3. 视觉上实际改了什么

按"用户能看见"的顺序：

1. **顶栏**
   - 单行/双行稳定：品牌 + 导航一整行，控件 + 状态 pill 折到第二行。**实测** 1440 上页眉 85px（改动前 101–118px）。
   - 半透明 + 背景模糊，滚动时读得出"这是页眉"而不必打阴影。
   - 紧凑化的控件标签（`.lang-picker` 从"标签一行 / 下拉一行"改为一行式）。
   - **当前分区高亮**（见 §4，这是一处真实缺失的修复）。
2. **卡片**：细线边框取代投影；`--radius` 10→12px；卡片头改为垂直居中（原来 `baseline` 会让 pill 与标题错位）。
3. **页头**：标题 + 副标题下方加一条收口细线，把"页头"与"第一张卡"分开。
4. **表格**：表头变成一道底色带（长表里唯一的列定位锚点）；行悬停用 `--surface-3`；单元格加高到 8px。
5. **指标块**（`.stat`）：数字 22px/600 + `tabular-nums`（跨卡竖排对齐）；`.grid-4` 步长 170→210px，
   把列数从 5 压到 4 —— 8 个指标从 5+3 变成 4+4，6 个从 5+1 变成 4+2。
6. **状态 chip**：软底 + 同色系细线 + 饱和文字，一屏十几个饱和色圆环的噪声消失。
7. **表单**：`.grid-3` 底对齐（提交按钮不再浮在控件上方半行）。
8. **焦点环**：全局 `:focus-visible`（2px 强调色 + 2px offset）。
9. **减少动态**：`prefers-reduced-motion` 统一压掉动画与过渡（`.task-spin` 是**无限循环**动画，此前无任何处理）。
10. **细节**：`:selection` 用强调软底；滚动条收窄并跟随主题；placeholder 颜色钉住（Windows 上默认值差异极大）。

---

## 4. 顺带修掉的两处"看不见的缺失"

两者都属于**同一个成因**：`ui_audit.js` 的"每个 class 都在 styles.css 里有定义"只扫 `app.js` 里的
`class="..."` **字面量**，于是 (a) 由 `classList.add()` 加上的类名、(b) 写在 `index.html` 骨架里的类名，
都不在任何守卫的视野里。

| 缺失 | 症状 | 修法 |
|---|---|---|
| `.topnav a.active` 从未定义 | `markActiveNav()` 一直在给当前导航项加 `.active`，但样式表里没有这条规则 —— "我在哪一页"**完全不可见**，用户只能从 `#view` 的标题反推 | 新增规则，取值与本项目既有的"当前项"一致（`--accent-soft` 底 + `--accent-ink` 文字），不新造第二套选中语言 |
| `.lang-picker` 从未定义 | 顶栏两个 `<select>` 的标签壳是块级：标签文字与下拉框各占一行，控件组高度凭空翻倍 | 定义成一行式（flex row），标签降级为次级说明色 |

两条都已进入守卫（`ui_audit.js`：CSS 侧 + JS 侧各一条断言）。

---

## 5. 被测量抓出来的三个真问题

这一轮的所有结论都不是"看着还行"，而是**跑出来**的。

### 5.1 顶栏 sticky 偏移写死 → 侧栏顶端会缩进页眉底下

第一版用 `--sticky-top: 66px` 常量。实测页眉会随视口与**用户数据**折行
（1440 上 85–101px，控件折行时 123px），写死的偏移在滚动后会让侧栏的标题被
sticky 页眉（`z-index: 20`）盖住。
现在由 `app.js::syncTopbarHeight()` 把**实测**高度写进 `--topbar-h`，CSS 只读它。
第一版只在 `DOMContentLoaded` 量一次也是错的：状态 pill 的文案是 `loadChrome()` 异步填的，
量到的是**折行之前**的 49px（实测偏移 61px vs 真实 97px）—— 改为 `ResizeObserver` 盯住页眉尺寸。

### 5.2 选中态文字压强调软底只有 4.42:1（低于 AA）

`--accent`（#2c68df）压在 `--accent-soft`（#e8f0ff）上只有 **4.42:1**，而"软底 + 强调色文字"
恰好是本项目的既有选中语言（侧栏当前课程、课表当前月份、图谱视图按钮）。
这**不是本轮引入**的（侧栏与月份标签一直如此，只是没人算过），但本轮新增了顶栏高亮、
又把它扩散了一处。修法：新增 `--accent-ink`（浅色 #245ac7 = **5.45:1**；深色下 `--accent`
本身已是 5.85:1，故同值），**只**用于"有软底"的那几处；链接 / 主按钮 / 焦点环 / 左侧竖线仍用 `--accent`
（它们在 `--surface` / `--bg` 上是 5.06 / 4.76，达标）。

### 5.3 窄视口横向溢出（既有缺陷，本轮修掉）

520px 视口 + 真实数据（`course-32dde014868219be`）：页眉控件组的最小内容宽度 **917px**，
把整页撑出横向滚动条。

- **对照实验证明是既有缺陷**：把 `styles.css` 换回提交版（`git show HEAD`）实测同样溢出 **417px**；
  本轮改动把它降到 323px（pill 收窄 + 控件标签紧凑化）后仍不合格。
- **根因**：三个状态 pill 的文案是服务端给的、**没有长度上界**（实测一个 pill 249px），
  而控件组是 flex 行、默认 `nowrap`。
- **修法**：控件组内部换行（`.topbar-meta { flex-wrap: wrap }`）+ 页眉内长 pill 允许折行 +
  下拉框允许收缩（`min-width: 0 / max-width: 100%`）+ 窄屏导航项收紧一档。
  **不截断文字**：状态文案属于"必须让人看见的原始事实"，截断等于把事实藏起来。
- **结果**：520px 实测 `docW 509 ≤ 520`，溢出元素 0 个；导航 3 行→2 行；代价是页眉 213px→238px。

---

## 6. 守卫与变异测试

新增 **49 条**断言（`ui_audit.js`，627→676）：

| 组 | 条数 | 关键判据 |
|---|---|---|
| token 层齐备 | 1 | 21 个 token 全部在 `:root` 里声明 |
| 深色主题完整 | 1 | `--surface-3` / `--topbar-bg` / `--accent-line` / `--shadow-2` 都被覆写 |
| 自定义属性自洽 | 3 | 引用集合 ⊆ 定义集合（+ 两侧非空证明） |
| 焦点可见 | 2 | **裸选择器** `:focus-visible` 带 2px 强调色环；全表无 `outline: none` |
| 减少动态 | 2 | 媒体查询存在且同时压掉 `animation-duration` 与 `transition-duration` |
| 顶栏高亮 | 2 | CSS 有 `.topnav a.active`，且 `app.js` 仍在 `classList.add('active')` |
| 高度层级 | 2 | `.card` 无 `box-shadow`；`.modal` / `.toast` 用 `--shadow-3` |
| 无亮度滤镜 | 1 | 全表无 `filter: brightness(` |
| chip 细线 | 1 | `.pill-ok` / `.pill-bad` 用 `--*-line` |
| 表头底色带 | 1 | `table.data th` 有 `--surface-2` 底 |
| sticky 偏移 | 2 | CSS 侧读 `--topbar-h`，JS 侧写它（缺一半就不成立） |
| **对比度** | 31 | 两张主题表 × 15 对"真实同屏出现"的前景/背景，按 WCAG 2.1 相对亮度**算**，全部 ≥ 4.5:1（+ 非空跑计数） |

**对比度那一节**从 CSS 里解析 token 取值（而不是在脚本里再抄一份颜色常量 —— 那样有人改了颜色它就变成谎话），
并按 CSS 自己的层叠规则查表：深色块覆写了就取深色值，未覆写才继承 `:root`。
（第一版把两块字符串拼起来取"第一个匹配"，于是深色表读的是浅色值 —— 断言看着绿，实际没验过深色；
变异测试里"只改浅色值却让深色也报红"暴露了它，已修。）

**变异测试**（`scripts/ui_mutation_check.py`，18 个变异全部精准捕获；这个 harness 本身也是本轮新增的，因为"断言通过"与"断言有效"是两件事）：

| 变异 | 结果 |
|---|---|
| 删掉顶栏当前分区样式 | ✗ css styles the active top-nav item |
| 给卡片加回投影 | ✗ cards stay flat |
| 删掉全局焦点环 | ✗ css gives focus a visible ring |
| 把 outline 设成 none | ✗ css never removes the focus outline |
| 删掉 `prefers-reduced-motion` 整块 | ✗ 2 条 |
| chip 边框退回饱和色 | ✗ status chips use the soft line tokens |
| 表头底色带撤掉 | ✗ table headers render as a tinted band |
| 删掉一个动效 token | ✗ css defines the design-token layer（--dur-2）|
| 深色主题漏掉顶栏底色 | ✗ css keeps a full dark-theme token block |
| 引用一个拼错的变量 | ✗ every custom property referenced is defined |
| 悬停改回 brightness 滤镜 | ✗ css never lightens controls with a brightness filter |
| 弹窗撤掉最高层级投影 | ✗ overlays carry the top elevation token |
| CSS 侧把 sticky 偏移写死 | ✗ css drives the sticky sidebar offset |
| `app.js` 不再写 `--topbar-h` | ✗ app.js writes the measured header height |
| 浅色 / 深色分别把 `--accent-ink` 调淡 | ✗ 各 1 条（**证明两张主题表是分开求值的**）|
| 把 `--ok` / `--text-soft` 调淡 | ✗ 2 条（ok chip + 对勾）/ 1 条 |

```bash
python scripts/ui_mutation_check.py   # → 全部 18 个变异都被精准捕获 (文件已还原)
```

**"删掉全局焦点环"这条变异第一次是 MISS 的** —— 因为判据写成了
`/:focus-visible\s*\{[^}]*outline: 2px solid var\(--accent\)/`，画布那条
`.km-graph canvas:focus-visible` 也满足它，于是"全局焦点环"这条断言形同虚设。
改成要求**行首裸选择器**（`/^:focus-visible\s*\{…/m`）后才真正有分辨力。
这正是项目里反复强调的那类问题：断言不是"通过就行"，而要能用变异证明它不是空跑。

---

## 7. 真实浏览器验证

用 `127.0.0.1:8765` 上的**真实服务 + 真实课程数据**（`course-32dde014868219be`，Catalan/Spanish 材料）
在 panel 里逐页实测（读的是 `getComputedStyle` / `getBoundingClientRect` 的真实值，不是截图观感）：

| 检查 | 实测结果 |
|---|---|
| 顶栏当前分区（10 条顶层路由 × 中文） | 每条的 active 文字与 `#view` 的标题一致（概览/今日/待审核/我的课程/材料/知识地图/考前复习/复习包/练习/错题本） |
| 顶栏当前分区（西语，5 条路由） | `Hoy` / `Materiales` / `Mapa de conocimiento` / `Mis asignaturas` 均与标题一致；课程内深链按"归属表"清空高亮（既有设计） |
| 顶栏实测高度 ↔ sticky 偏移 | 1440：`--topbar-h` 85px = 实测 85px，侧栏 `top: 97px`（> 85）✓；西语下页眉折到 123px，侧栏跟着 135px ✓ |
| 焦点环（真实键盘/控件焦点） | `select` 聚焦后 `matches(':focus-visible') === true`，`outline: 2px solid rgb(122,167,255)`，offset 2px；下拉框边框转为强调色 ✓（页眉/页面必须先获得文档焦点，否则 `document.hasFocus()` 为 false、`:focus-visible` 不成立 —— 这一步也记下来） |
| 对比度（浅色，真实渲染） | 正文 16.77 / 副标题 5.64 / 卡片标题 17.82 / 次级 5.44 / 顶栏高亮 **5.45** / 侧栏高亮 **5.45** / ok 4.73 / warn 4.72；最小值 4.72 ✓ |
| 对比度（深色，真实渲染） | 正文 15.84 / 次级 6.95 / 顶栏高亮 5.85 / ok 9.08 / warn 8.40；最小值 5.85 ✓ |
| 横向溢出（1440 / 1280 / 520，× zh/es/ca） | 全部 `scrollWidth ≤ innerWidth`（差值 −11px，来自滚动条占位）✓ |
| 课程页（真实 28 节课） | 4 个月份切换 tab、恰 1 个 `aria-current`、10 张日卡 / 10 行、表头底色 = `--surface-2` ✓ |
| 知识地图（真实 61 KP） | canvas 945×520 **真有像素**（非透明采样 9000）、画布底色 = `--graph-bg` 深色值 ✓ |
| 520px 窄屏（真实数据） | 溢出元素 0 个；导航 2 行；`.grid-4` 收成 2 列；侧栏 `position: static` ✓ |

`ui_audit.js --preview --page`（本轮新增）：不启服务、不跑 JS 地输出任意页面的静态快照
（真实骨架 + 内联 CSS + 真实渲染），用于逐页视觉比对：

```bash
node scripts/ui_audit.js --preview zh  --page dashboard      --out cache/after-dashboard-zh.html
node scripts/ui_audit.js --preview es  --page course         --out cache/after-course-es.html
```

---

## 8. 明确**没有**改的东西

- `--graph-*` 画布 token 与 `knowledge-graph.js` 的取色逻辑（画布像素有独立验证链）。
- 任何文案、i18n key、路由、接口、`.km-*` / `.session-*` / `.material-*` 结构与行为。
- "当前项"的语义来源（仍由页面函数 `markActiveNav()` 声明，不反解析 hash）。
- 窄屏表格的列宽策略（`table-layout: fixed` + 单元格折行）：520px 下长词仍会断行，
  这是既有取向，改它属于另一个课题。
- 三条已知的既有红色/限制项不属本轮（见 §9）。

---

## 9. 遗留

1. `ui_render_check.js` 有 **2 条既有红色**（`#/students` 与 `#/courses/<c>/students/<s>`
   期望"重定向到我的课程"，实际渲染成"未找到页面"）。与样式无关，属路由行为；
   本轮只记录，未改（改它会动到路由与 `test_web_ui.py` 的既有断言）。
2. 520px 下页眉 238px（sticky）。已把溢出（真缺陷）修掉；进一步压缩需要在窄屏隐藏诊断 pill，
   而"不隐藏状态事实"是项目铁律，故不做，作为已知取舍记录在此。
3. `.grid` 的 `style="margin-bottom:16px"` 内联魔数仍在 4 个 view 文件里（5 处）。属于
   "JS 里写版式魔数"的欠账，改它要动 4 个 view 且会带来既有间距漂移，本轮不动。
