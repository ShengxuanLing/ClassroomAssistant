/*
 * 知识图谱视图 (km.view.graph) —— 把 knowledge-map 投影里的 ``graph`` 画成图。
 *
 * 为什么单独一个文件
 * ------------------
 * 知识地图此前只有"按材料分组的卡片/列表", 没有关系层, 于是名字叫地图、形态是
 * 列表 (用户 2026-10-12 实测反馈)。关系数据一直存在 (AI 把候选的 relations 拼进
 * 了知识点正文的 "关联: …" 行), 缺的是把它归一化成边 —— 那一步现在在后端
 * ``src/knowledge_graph.py`` 里做, 前端只负责**画**。
 *
 * 2026-10-13: 边有**两类来源**, 画法必须能分辨
 * --------------------------------------------
 * 后端的边带 ``origin`` 字段, 这是本文件唯一区分权威与推断的依据:
 *
 * - ``origin="registered"`` (``directed=true``): ``knowledge_relations`` 里的
 *   一等事实, **有向有类型**, 用实线 + 箭头画, 并在详情面板里按出边/入边列出。
 * - ``origin="content_hint"`` / ``"concept"``: 由正文 "关联: " 行归一化或
 *   未归类概念推出的**推断** (无向), 用细线画, 不画箭头。
 *
 * ``origin`` 缺失时按推断处理 —— 缺一个来源标记的边不是"已登记关系", 不能
 * 靠默认值把它升级成权威事实。
 *
 * 三条底线
 * --------
 * 1. **不编造**: 边全部来自后端投影; 节点分两种 (kind="kp" / kind="concept"),
 *    概念节点用虚线且明确标注"原文明示, 非知识点", 绝不混进知识点计数。
 * 2. **确定性**: 布局用固定种子的伪随机 + 固定迭代次数, 同一份数据每次得到同一张图。
 * 3. **不重复请求**: 视图切换 / 搜索 / 筛选只重绘, 不发起新请求 (与其它视图同口径)。
 *
 * 布局策略 (参考 Shneiderman 的"总览优先"): 先按材料分成若干**叶瓣**(固定质心 +
 * 簇内斥力), 再在叶瓣内跑弹簧-电荷模型; 孤立点默认弱化并可一键隐藏 —— 380 个点
 * 的"毛球"不是地图。
 */
'use strict';


const KM_GRAPH_STATE = {
  hover: -1,
  selected: -1,
  showLabels: false,
  onlyLinked: false,
  showConcepts: true,
  dimIsolated: true,
  matOn: null,          // Set<number> | null (null = 全部)
  pos: null,            // Float64Array, 缓存的布局坐标
  layoutKey: '',        // 布局对应的数据指纹 (数据变了才重算)
  fittedKey: '',        // 已经"适应窗口"过的指纹 (每份数据只自动适配一次)
  keyOf: null,          // 指纹缓存: 算过指纹的那个 graph 对象
  keyValue: '',         // 上面那个对象的指纹
  theme: null,          // 画布配色 (从 CSS 变量读, 见 kmGraphTheme)
  view: { x: 0, y: 0, k: 1 },
  boundCanvas: null,
  dragging: false,
};

// ---- 数据访问 -------------------------------------------------------------

function kmGraph() {
  const graph = KM_STATE.graph;
  if (!graph || !Array.isArray(graph.nodes) || !graph.nodes.length) return null;
  return graph;
}

function kmGraphMaterialIndex() {
  // graph 节点只带 material_id; 这里翻成 KM_STATE.materials 的下标 (取色用)。
  const index = {};
  (KM_STATE.materials || []).forEach((material, i) => {
    index[String(material.material_id)] = i;
  });
  return index;
}

// 边的来源 (与后端 src/knowledge_graph.py 的三个 EDGE_ORIGIN_* 常量一一对应)。
// 缺失时按推断处理: 没标来源的边绝不是"已登记关系"。
function kmGraphEdgeOrigin(edge) {
  if (edge && edge.origin) return String(edge.origin);
  return (edge && edge.kind === 'concept') ? 'concept' : 'content_hint';
}

function kmGraphEdgeRegistered(edge) {
  return kmGraphEdgeOrigin(edge) === 'registered';
}

// 有向性以 origin 为准, 不只看 directed 字段: 后端已注册边必然 directed=true,
// 但"断言 directed 就必须画箭头"会在 payload 缺 directed 时把权威边画成无向。
function kmGraphEdgeDirected(edge) {
  return kmGraphEdgeRegistered(edge) || (edge && edge.directed === true);
}

// FNV-1a (32 位): 确定性、无依赖、够短。指纹只用于"要不要重算布局", 不用于
// 安全判定, 所以不需要加密哈希。
function kmGraphHash(text) {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16);
}

/**
 * 布局指纹 —— 必须认得出"节点/边的身份变了, 但数量没变"。
 *
 * 旧口径只拼 ``节点数:边数:材料数``, 于是**重新分析后**: 某个知识点被替换成
 * 另一个 (同样 1 个点)、某条关联的端点换了人 (同样 1 条边) —— 三个数字一字不差,
 * 指纹相同, 缓存命中, 地图继续画**上一版**数据的坐标。图上节点用新标签、坐标
 * 却是旧的, 这种错位最难发现。
 *
 * 现在把节点身份 (id/kind/material) 与每条边的**端点 + 来源**都算进系列, 再取
 * 定长哈希。无关字段 (标题文字、证据条数、状态) 刻意**不**参与 —— 它们不影响
 * 拓扑, 改一个字就重算整张布局是白费。
 *
 * 缓存: 同一份投影对象只算一次。后端每次请求都返回**新对象**, 所以"对象身份
 * 变了"就是"数据变了"的正确信号 (kmBuildViewModel 直接存放响应里的 graph)。
 */
function kmGraphLayoutKey(graph) {
  if (KM_GRAPH_STATE.keyOf === graph) return KM_GRAPH_STATE.keyValue;
  const parts = [];
  (graph.nodes || []).forEach((node) => {
    parts.push(String(node.id) + '\u0001' + String(node.kind) + '\u0001' + String(node.material_id));
  });
  (graph.edges || []).forEach((edge) => {
    parts.push(String(edge.source) + '\u0002' + String(edge.target) + '\u0002' +
      String(edge.kind) + '\u0002' + kmGraphEdgeOrigin(edge) + '\u0002' + String(edge.relation_type || ''));
  });
  const key = [
    (graph.nodes || []).length,
    (graph.edges || []).length,
    (KM_STATE.materials || []).length,
    kmGraphHash(parts.join('|')),
  ].join(':');
  KM_GRAPH_STATE.keyOf = graph;
  KM_GRAPH_STATE.keyValue = key;
  return key;
}

// ---- 配色 (跟随应用主题) --------------------------------------------------
//
// 画布以前把深色写死在代码里 (#101219 底 / #dfe4f2 字), 浅色模式下就是
// "白页面里嵌一块黑板", 卡片边框还是浅色的 —— 既不协调, 也不满足"节点/边/文字
// 在两种主题下都够对比"。现在颜色全部来自 CSS 变量 (styles.css 的 --graph-*),
// 浅深两套token 各给一份, 画布只负责**读**。
//
// 回退值同样成对给出: 读不到 CSS 变量时 (无头审计的 DOM 桩没有 getComputedStyle,
// 或变量名被改掉) 依据 matchMedia 选一套, 绝不出现"深色页面配浅色文字"。

const KM_GRAPH_THEME_LIGHT = {
  bg: '#f7f9fc', text: '#1c2024', muted: '#5c6570', node: '#6b7482',
  edge: 'rgba(28,32,36,.22)', edgeRegistered: 'rgba(44,104,223,.85)',
  edgeConcept: 'rgba(92,101,112,.35)', edgeFocus: 'rgba(44,104,223,.95)',
  edgeDim: 'rgba(28,32,36,.06)', select: '#1c2024', search: '#0f7a53',
  conflict: '#b02a2a', unverified: '#96601a', concept: '#5c6570',
  conceptFill: 'rgba(92,101,112,.12)',
  palette: ['#2c68df', '#c05621', '#167a4a', '#7a3fb8', '#8a6d1f'],
};

const KM_GRAPH_THEME_DARK = {
  bg: '#101219', text: '#dfe4f2', muted: '#9aa3b8', node: '#8c95ac',
  edge: 'rgba(255,255,255,.13)', edgeRegistered: 'rgba(122,162,255,.95)',
  edgeConcept: 'rgba(140,149,172,.16)', edgeFocus: 'rgba(122,162,255,.95)',
  edgeDim: 'rgba(255,255,255,.04)', select: '#ffffff', search: '#7affc9',
  conflict: '#ff6b6b', unverified: '#f2c14b', concept: '#9aa3b8',
  conceptFill: 'rgba(154,163,184,.14)',
  palette: ['#5b8cff', '#ff9a5b', '#3ecf8e', '#c98bff', '#f2c14b'],
};

//: CSS 变量名 -> 主题键 (缺一个就回退到上面那套)。
const KM_GRAPH_THEME_TOKENS = [
  ['--graph-bg', 'bg'], ['--graph-text', 'text'], ['--graph-muted', 'muted'],
  ['--graph-node', 'node'], ['--graph-edge', 'edge'],
  ['--graph-edge-registered', 'edgeRegistered'], ['--graph-edge-concept', 'edgeConcept'],
  ['--graph-edge-focus', 'edgeFocus'], ['--graph-edge-dim', 'edgeDim'],
  ['--graph-select', 'select'], ['--graph-search', 'search'],
  ['--graph-conflict', 'conflict'], ['--graph-unverified', 'unverified'],
  ['--graph-concept', 'concept'], ['--graph-concept-fill', 'conceptFill'],
  ['--graph-c1', 'palette.0'], ['--graph-c2', 'palette.1'], ['--graph-c3', 'palette.2'],
  ['--graph-c4', 'palette.3'], ['--graph-c5', 'palette.4'],
];

function kmGraphPrefersDark() {
  try {
    return !!(typeof window !== 'undefined' && window.matchMedia &&
      window.matchMedia('(prefers-color-scheme: dark)').matches);
  } catch (err) {
    return false;
  }
}

function kmGraphTheme() {
  if (KM_GRAPH_STATE.theme) return KM_GRAPH_STATE.theme;
  const theme = Object.assign({}, kmGraphPrefersDark() ? KM_GRAPH_THEME_DARK : KM_GRAPH_THEME_LIGHT);
  theme.palette = theme.palette.slice();
  try {
    const root = (typeof document !== 'undefined' && document.documentElement) || null;
    const styles = (root && typeof window !== 'undefined' && typeof window.getComputedStyle === 'function')
      ? window.getComputedStyle(root) : null;
    if (styles && typeof styles.getPropertyValue === 'function') {
      KM_GRAPH_THEME_TOKENS.forEach(([name, key]) => {
        const value = String(styles.getPropertyValue(name) || '').trim();
        if (!value) return;
        if (key.indexOf('palette.') === 0) theme.palette[Number(key.slice(8))] = value;
        else theme[key] = value;
      });
    }
  } catch (err) { /* 主题读不到就用回退值 —— 画布不能因为读样式而整个不画 */ }
  KM_GRAPH_STATE.theme = theme;
  return theme;
}

function kmGraphInvalidateTheme() {
  KM_GRAPH_STATE.theme = null;
}

// 系统主题切换时重读 token 并重画 (画布上的颜色是**画上去的**, CSS 换变量不会
// 自动更新已经绘制的像素)。
if (typeof window !== 'undefined' && window.matchMedia) {
  try {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    if (media && typeof media.addEventListener === 'function') {
      media.addEventListener('change', () => {
        kmGraphInvalidateTheme();
        const canvas = document.getElementById('km-graph-canvas');
        const graph = kmGraph();
        if (canvas && graph) kmGraphDraw(canvas, graph);
      });
    }
  } catch (err) { /* 监听不上就退化为"下次挂载时用新主题" */ }
}

// ---- 确定性布局 -----------------------------------------------------------
//
// 固定种子线性同余发生器 —— 不用 Math.random, 保证"同一份数据同一张图"。

function kmGraphLcg(seed) {
  let state = seed >>> 0;
  return function next() {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

function kmGraphComputeLayout(graph) {
  const nodes = graph.nodes;
  const count = nodes.length;
  const width = 1200;
  const height = 820;
  const materialIndex = kmGraphMaterialIndex();
  const lobes = {};
  nodes.forEach((node) => {
    const key = materialIndex[String(node.material_id)];
    const bucket = key === undefined ? 'none' : String(key);
    if (!lobes[bucket]) lobes[bucket] = { cx: 0, cy: 0, n: 0 };
    lobes[bucket].n += 1;
  });
  const keys = Object.keys(lobes).sort();
  const radius = Math.min(width, height) * 0.30;
  keys.forEach((key, i) => {
    const angle = (i / Math.max(1, keys.length)) * Math.PI * 2 - Math.PI / 2;
    lobes[key].cx = width / 2 + Math.cos(angle) * radius;
    lobes[key].cy = height / 2 + Math.sin(angle) * radius;
  });

  const random = kmGraphLcg(20261012);
  const pos = new Float64Array(count * 2);
  const vel = new Float64Array(count * 2);
  const lobeOf = new Array(count);
  nodes.forEach((node, i) => {
    const key = materialIndex[String(node.material_id)];
    const bucket = key === undefined ? 'none' : String(key);
    lobeOf[i] = lobes[bucket] || { cx: width / 2, cy: height / 2 };
    const angle = random() * Math.PI * 2;
    const r = 20 + random() * Math.min(120, 16 * Math.sqrt(Math.max(1, lobeOf[i].n)));
    pos[i * 2] = lobeOf[i].cx + Math.cos(angle) * r;
    pos[i * 2 + 1] = lobeOf[i].cy + Math.sin(angle) * r;
  });

  const idIndex = {};
  nodes.forEach((node, i) => { idIndex[String(node.id)] = i; });
  const edgeIndex = [];
  graph.edges.forEach((edge) => {
    const a = idIndex[String(edge.source)];
    const b = idIndex[String(edge.target)];
    if (a === undefined || b === undefined) return;
    edgeIndex.push([a, b, edge.kind === 'concept']);
  });

  const iterations = count > 260 ? 300 : 460;
  for (let step = 0; step < iterations; step += 1) {
    const k = Math.sqrt((width * height) / Math.max(1, count)) * 0.5;
    const temperature = 34 * (1 - step / iterations) + 1.5;
    // 斥力: 网格分桶, 只算邻近单元 —— 380 个点的 O(n^2) 会卡。
    const cell = 70;
    const grid = new Map();
    for (let i = 0; i < count; i += 1) {
      const gx = Math.floor(pos[i * 2] / cell);
      const gy = Math.floor(pos[i * 2 + 1] / cell);
      const key = gx + ',' + gy;
      if (!grid.has(key)) grid.set(key, []);
      grid.get(key).push(i);
    }
    for (let i = 0; i < count; i += 1) {
      const gx = Math.floor(pos[i * 2] / cell);
      const gy = Math.floor(pos[i * 2 + 1] / cell);
      for (let dx = -1; dx <= 1; dx += 1) {
        for (let dy = -1; dy <= 1; dy += 1) {
          const bucket = grid.get((gx + dx) + ',' + (gy + dy));
          if (!bucket) continue;
          for (let b = 0; b < bucket.length; b += 1) {
            const j = bucket[b];
            if (j <= i) continue;
            let vx = pos[i * 2] - pos[j * 2];
            let vy = pos[i * 2 + 1] - pos[j * 2 + 1];
            let d2 = vx * vx + vy * vy;
            if (d2 < 1e-6) { vx = 0.1; vy = 0.1; d2 = 0.02; }
            const d = Math.sqrt(d2);
            const f = (k * k) / d2;
            vx /= d; vy /= d;
            vel[i * 2] += vx * f; vel[i * 2 + 1] += vy * f;
            vel[j * 2] -= vx * f; vel[j * 2 + 1] -= vy * f;
          }
        }
      }
    }
    // 引力: 边把两端拉近 (概念边弱一些, 免得概念节点把簇拽变形)。
    for (let e = 0; e < edgeIndex.length; e += 1) {
      const a = edgeIndex[e][0];
      const b = edgeIndex[e][1];
      let vx = pos[b * 2] - pos[a * 2];
      let vy = pos[b * 2 + 1] - pos[a * 2 + 1];
      const d = Math.max(1e-3, Math.sqrt(vx * vx + vy * vy));
      const f = ((d * d) / k) * (edgeIndex[e][2] ? 0.12 : 0.30);
      vx /= d; vy /= d;
      vel[a * 2] += vx * f; vel[a * 2 + 1] += vy * f;
      vel[b * 2] -= vx * f; vel[b * 2 + 1] -= vy * f;
    }
    for (let i = 0; i < count; i += 1) {
      // 叶瓣重力(强) + 全局居中(弱): 材料必须各自成瓣, 不能揉成一个球。
      vel[i * 2] += (lobeOf[i].cx - pos[i * 2]) * 0.030 + (width / 2 - pos[i * 2]) * 0.0015;
      vel[i * 2 + 1] += (lobeOf[i].cy - pos[i * 2 + 1]) * 0.030 + (height / 2 - pos[i * 2 + 1]) * 0.0015;
      const m = Math.hypot(vel[i * 2], vel[i * 2 + 1]);
      if (m > temperature) {
        vel[i * 2] = (vel[i * 2] / m) * temperature;
        vel[i * 2 + 1] = (vel[i * 2 + 1] / m) * temperature;
      }
      pos[i * 2] += vel[i * 2];
      pos[i * 2 + 1] += vel[i * 2 + 1];
      vel[i * 2] *= 0.85;
      vel[i * 2 + 1] *= 0.85;
    }
  }
  KM_GRAPH_STATE.pos = pos;
  KM_GRAPH_STATE.layoutKey = kmGraphLayoutKey(graph);
  return pos;
}

function kmGraphPositions(graph) {
  if (KM_GRAPH_STATE.pos && KM_GRAPH_STATE.layoutKey === kmGraphLayoutKey(graph)) {
    return KM_GRAPH_STATE.pos;
  }
  return kmGraphComputeLayout(graph);
}

// ---- 视图状态 -------------------------------------------------------------

function kmGraphMaterialOn(index) {
  if (!KM_GRAPH_STATE.matOn) return true;
  return KM_GRAPH_STATE.matOn.has(index);
}

function kmGraphVisible(graph, index) {
  const node = graph.nodes[index];
  const materialIndex = kmGraphMaterialIndex();
  const key = materialIndex[String(node.material_id)];
  if (key !== undefined && !kmGraphMaterialOn(key)) return false;
  if (!KM_GRAPH_STATE.showConcepts && node.kind === 'concept') return false;
  if (KM_GRAPH_STATE.onlyLinked && !node.degree) return false;
  // 工具栏的"按来源筛选"同样作用于图谱。
  if (KM_STATE.activeMaterial && KM_STATE.activeMaterial !== 'all') {
    if (String(node.material_id) !== String(KM_STATE.activeMaterial)) return false;
  }
  return true;
}

function kmGraphAdjacency(graph) {
  const idIndex = {};
  graph.nodes.forEach((node, i) => { idIndex[String(node.id)] = i; });
  const adjacency = new Map();
  graph.edges.forEach((edge) => {
    const a = idIndex[String(edge.source)];
    const b = idIndex[String(edge.target)];
    if (a === undefined || b === undefined) return;
    if (!adjacency.has(a)) adjacency.set(a, []);
    if (!adjacency.has(b)) adjacency.set(b, []);
    adjacency.get(a).push(b);
    adjacency.get(b).push(a);
  });
  return adjacency;
}

// ---- 渲染 -----------------------------------------------------------------

function kmRenderGraph() {
  const graph = kmGraph();
  if (!graph) {
    return '<div class="card"><p class="muted">' + esc(t('km.graph.empty')) + '</p></div>';
  }
  return '' +
    '<div class="km-graph" id="km-graph">' +
    // role="img" + aria-label: 一句话说清"图里有什么"。只说"知识地图"等于没说,
    // 所以 desc 报真实计数 (节点/已登记关系/推断关联/未归类概念)。
    // tabindex="0": 画布本身要能拿到焦点, 否则方向键无处可挂 —— 没有键盘入口
    // 的 canvas 就是一张图片, 而这张图是**可以**逐点浏览的。
    '<canvas id="km-graph-canvas" role="img" tabindex="0" aria-label="' +
    esc(kmGraphA11ySummary(graph)) + '"></canvas>' +
    '<div class="km-graph-toolbar">' +
    '<button type="button" class="km-view-btn" data-action="km-graph-fit">' + esc(t('km.graph.fit')) + '</button>' +
    '<button type="button" class="km-view-btn" data-action="km-graph-only-linked" aria-pressed="' +
    (KM_GRAPH_STATE.onlyLinked ? 'true' : 'false') + '">' + esc(t('km.graph.onlyLinked')) + '</button>' +
    '<button type="button" class="km-view-btn" data-action="km-graph-concepts" aria-pressed="' +
    (KM_GRAPH_STATE.showConcepts ? 'true' : 'false') + '">' + esc(t('km.graph.showConcepts')) + '</button>' +
    '<button type="button" class="km-view-btn" data-action="km-graph-labels" aria-pressed="' +
    (KM_GRAPH_STATE.showLabels ? 'true' : 'false') + '">' + esc(t('km.graph.showLabels')) + '</button>' +
    '</div>' +
    '<div class="km-graph-tip" id="km-graph-tip" hidden></div>' +
    '<div class="km-graph-hint">' + esc(t('km.graph.hint')) + '</div>' +
    '</div>' +
    '<div class="card km-graph-detail">' +
    '<h2>' + esc(t('km.graph.selected')) + '</h2>' +
    // aria-live: 键盘选中节点时, 这张卡是页面里唯一会变的内容 —— 没有它,
    // 屏幕阅读器用户按方向键什么也听不到 (而键盘操作正是要求里的").
    '<div id="km-graph-detail" class="small muted" aria-live="polite">' +
    esc(t('km.graph.noSelection')) + '</div>' +
    '</div>' +
    '<div class="card km-graph-unresolved">' +
    '<h2>' + esc(t('km.graph.unresolved')) + ' (' + (graph.unresolved_terms || []).length + ')</h2>' +
    '<p class="small muted">' + esc(t('km.graph.unresolvedNote')) + '</p>' +
    '<ul class="plain">' + (graph.unresolved_terms || []).slice(0, 40).map((item) =>
      '<li><span>' + esc(item.term) + '</span><span>×' + item.count + '</span></li>').join('') +
    '</ul>' +
    '</div>';
}

// kmGraphMount: 在 kmPaint 把 HTML 注入 DOM **之后**调用 (canvas 需要真实尺寸)。
function kmGraphMount() {
  const graph = kmGraph();
  const canvas = document.getElementById('km-graph-canvas');
  if (!graph || !canvas) return;
  // 每次挂载重读一次主题: 用户可能在两次渲染之间切了系统浅/深色 (监听不到
  // 时也不会画错色) —— 挂载是低频事件, 读一次 getComputedStyle 不心疼。
  kmGraphInvalidateTheme();
  kmGraphPositions(graph);
  if (KM_GRAPH_STATE.boundCanvas !== canvas) {
    kmGraphBind(canvas, graph);
    KM_GRAPH_STATE.boundCanvas = canvas;
  }
  kmGraphResize(canvas, graph);
  // 每份数据自动"适应窗口"一次 —— 否则首屏停在 (0,0)/1x, 簇被推到画布
  // 边缘外, 看起来像没画。之后用户自己缩放/平移, 重绘不再重置视角。
  // 只有**真的**适配成功才记住指纹: 否则"量不到尺寸的那一次"会把自动
  // 适配名额用掉, 之后尺寸有了也不会再适配 (首屏停在 (0,0)/1x)。
  if (KM_GRAPH_STATE.fittedKey !== KM_GRAPH_STATE.layoutKey && kmGraphFit(canvas, graph)) {
    KM_GRAPH_STATE.fittedKey = KM_GRAPH_STATE.layoutKey;
  }
  kmGraphDrawDetail(graph);
}

function kmGraphResize(canvas, graph) {
  const dpr = window.devicePixelRatio || 1;
  const parent = canvas.parentNode;
  const width = canvas.clientWidth || (parent && parent.clientWidth) || 0;
  const height = canvas.clientHeight || (parent && parent.clientHeight) || 0;
  // 无真实布局时 (无头审计沙箱 / 页面还没上屏) 直接跳过: 宁可不画, 也不能
  // 让整页崩在 canvas 的尺寸上。
  if (!width || !height) return;
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  canvas.__kmDpr = dpr;
  kmGraphDraw(canvas, graph);
}

/**
 * 适应窗口。返回**真的按了没有** —— 画布没有真实尺寸时不是“按完了”, 是
 * “没法按”, 调用方据此决定要不要把这次的指纹记成已适配 (见 kmGraphMount)。
 */
function kmGraphFit(canvas, graph) {
  const pos = kmGraphPositions(graph);
  let minX = Infinity; let maxX = -Infinity; let minY = Infinity; let maxY = -Infinity;
  for (let i = 0; i < graph.nodes.length; i += 1) {
    if (!kmGraphVisible(graph, i)) continue;
    minX = Math.min(minX, pos[i * 2]); maxX = Math.max(maxX, pos[i * 2]);
    minY = Math.min(minY, pos[i * 2 + 1]); maxY = Math.max(maxY, pos[i * 2 + 1]);
  }
  if (!isFinite(minX)) return false;
  const dpr = canvas.__kmDpr || 1;
  const cw = (canvas.width || 0) / dpr;
  const ch = (canvas.height || 0) / dpr;
  // 画布还没有真实尺寸 (页面未上屏 / 无头审计的 DOM 桩 / 容器被隐藏) 时
  // **不**动视角: 否则 k = 0/0 = NaN, 之后每次坐标换算都是 NaN —— 画布一片
  // 空白, 而屏幕上不显示任何原因。
  if (!isFinite(cw) || !isFinite(ch) || cw <= 0 || ch <= 0) return false;
  const pad = 50;
  const k = Math.min(cw / (maxX - minX + pad * 2), ch / (maxY - minY + pad * 2));
  KM_GRAPH_STATE.view.k = k;
  KM_GRAPH_STATE.view.x = cw / 2 - ((minX + maxX) / 2) * k;
  KM_GRAPH_STATE.view.y = ch / 2 - ((minY + maxY) / 2) * k;
  kmGraphDraw(canvas, graph);
  return true;
}

function kmGraphMaterialColor(materialId) {
  const index = kmGraphMaterialIndex()[String(materialId)];
  const theme = kmGraphTheme();
  if (index === undefined) return theme.node;
  return theme.palette[index % theme.palette.length];
}

/**
 * 画布的无障碍描述: 报**真实计数**, 不复述标题。
 *
 * 三个数字分别取自后端的 graph.stats (权威口径), 缺字段时才回退到本地数边 ——
 * 与统计条同一原则: 数字要么来自后端, 要么来自同一份投影, 不另外估一个。
 */
function kmGraphA11ySummary(graph) {
  const stats = (graph && graph.stats) || {};
  const nodes = (stats.knowledge_point_count != null)
    ? stats.knowledge_point_count
    : (graph.nodes || []).filter((node) => node.kind !== 'concept').length;
  const registered = (stats.registered_relation_count != null)
    ? stats.registered_relation_count
    : kmGraphEdgeCountByOrigin(graph, 'registered');
  const inferred = (stats.content_hint_edge_count != null)
    ? stats.content_hint_edge_count
    : kmGraphEdgeCountByOrigin(graph, 'content_hint');
  const concepts = (stats.concept_count != null)
    ? stats.concept_count
    : (graph.concepts || []).length;
  return t('km.graph.a11ySummary', {
    nodes: nodes, registered: registered, inferred: inferred, concepts: concepts,
  });
}

function kmGraphEdgeCountByOrigin(graph, origin) {
  return (graph.edges || []).filter((edge) => kmGraphEdgeOrigin(edge) === origin).length;
}

// 箭头 (已登记关系 = 有向事实, 必须看得出方向)。尺寸按 1/view.k 折算,
// 于是缩放时箭头在屏幕上大小恒定。
function kmGraphDrawArrow(ctx, fromX, fromY, toX, toY, size) {
  const angle = Math.atan2(toY - fromY, toX - fromX);
  const spread = 0.42;
  ctx.beginPath();
  ctx.moveTo(toX, toY);
  ctx.lineTo(toX - size * Math.cos(angle - spread), toY - size * Math.sin(angle - spread));
  ctx.moveTo(toX, toY);
  ctx.lineTo(toX - size * Math.cos(angle + spread), toY - size * Math.sin(angle + spread));
  ctx.stroke();
}

function kmGraphDraw(canvas, graph) {
  if (!canvas || typeof canvas.getContext !== 'function') return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  const theme = kmGraphTheme();
  const pos = kmGraphPositions(graph);
  const dpr = canvas.__kmDpr || 1;
  const view = KM_GRAPH_STATE.view;
  const cw = (canvas.width || 0) / dpr;
  const ch = (canvas.height || 0) / dpr;
  if (!cw || !ch) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cw, ch);
  ctx.fillStyle = theme.bg;
  ctx.fillRect(0, 0, cw, ch);
  ctx.save();
  ctx.translate(view.x, view.y);
  ctx.scale(view.k, view.k);

  const adjacency = kmGraphAdjacency(graph);
  const focus = KM_GRAPH_STATE.hover >= 0 ? KM_GRAPH_STATE.hover : KM_GRAPH_STATE.selected;
  const focusSet = new Set();
  if (focus >= 0) {
    focusSet.add(focus);
    (adjacency.get(focus) || []).forEach((j) => focusSet.add(j));
  }
  const hasFocus = focusSet.size > 0;
  const needle = String(KM_STATE.query || '').trim().toLowerCase();
  const idIndex = {};
  graph.nodes.forEach((node, i) => { idIndex[String(node.id)] = i; });

  // 边: 已登记关系 (实线 + 箭头) 与推断关联 (细线) 分开画, 视觉上不混。
  // 已登记边在后端数组里本来就在前, 这里再显式分两趟 —— 顺序不依赖后端,
  // 且推断边永远画在下面 (细线不该盖住权威边)。
  (graph.edges || []).forEach((edge) => {
    const a = idIndex[String(edge.source)];
    const b = idIndex[String(edge.target)];
    if (a === undefined || b === undefined) return;
    if (!kmGraphVisible(graph, a) || !kmGraphVisible(graph, b)) return;
    const origin = kmGraphEdgeOrigin(edge);
    const registered = origin === 'registered';
    const inFocus = hasFocus && focusSet.has(a) && focusSet.has(b);
    const x1 = pos[a * 2]; const y1 = pos[a * 2 + 1];
    const x2 = pos[b * 2]; const y2 = pos[b * 2 + 1];
    ctx.strokeStyle = inFocus
      ? theme.edgeFocus
      : (hasFocus ? theme.edgeDim
        : (registered ? theme.edgeRegistered
          : (origin === 'concept' ? theme.edgeConcept : theme.edge)));
    ctx.lineWidth = (registered ? 1.7 : 1) / view.k;
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
    if (kmGraphEdgeDirected(edge)) {
      kmGraphDrawArrow(ctx, x1, y1, x2, y2, 7 / view.k);
    }
  });
  ctx.lineWidth = 1 / view.k;

  for (let i = 0; i < graph.nodes.length; i += 1) {
    if (!kmGraphVisible(graph, i)) continue;
    const node = graph.nodes[i];
    const x = pos[i * 2];
    const y = pos[i * 2 + 1];
    const dim = (hasFocus && !focusSet.has(i))
      || (KM_GRAPH_STATE.dimIsolated && !node.degree && !hasFocus);
    ctx.globalAlpha = dim ? 0.20 : 1;
    if (node.kind === 'concept') {
      const r = 4.2 / view.k;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.setLineDash([3 / view.k, 2.4 / view.k]);
      ctx.lineWidth = 1.3 / view.k;
      ctx.strokeStyle = theme.concept;
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = theme.conceptFill;
      ctx.fill();
    } else {
      const r = (3 + Math.min(9, node.degree) * 0.8) / Math.sqrt(view.k);
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = kmGraphMaterialColor(node.material_id);
      ctx.fill();
      if (node.status === 'conflicted') {
        ctx.lineWidth = 1.7 / view.k; ctx.strokeStyle = theme.conflict; ctx.stroke();
      } else if (node.status === 'unverified') {
        ctx.lineWidth = 1.2 / view.k; ctx.strokeStyle = theme.unverified; ctx.stroke();
      }
    }
    if (i === KM_GRAPH_STATE.selected) {
      ctx.lineWidth = 2 / view.k; ctx.strokeStyle = theme.select; ctx.stroke();
    }
    if (needle && String(node.label || '').toLowerCase().indexOf(needle) >= 0) {
      ctx.lineWidth = 2.2 / view.k; ctx.strokeStyle = theme.search; ctx.stroke();
    }
    if (KM_GRAPH_STATE.showLabels || i === KM_GRAPH_STATE.hover || i === KM_GRAPH_STATE.selected) {
      ctx.font = (11 / view.k) + "px 'Segoe UI',sans-serif";
      ctx.fillStyle = theme.text;
      ctx.fillText(String(node.label || '').slice(0, 40), x + 6 / view.k, y + 3.5 / view.k);
    }
    ctx.globalAlpha = 1;
  }

  // 叶瓣标签 (材料名) —— 让人一眼看出"这是按材料分瓣的", 而不是一团。
  ctx.font = (13 / view.k) + "px 'Segoe UI',sans-serif";
  const materialIndex = kmGraphMaterialIndex();
  (KM_STATE.materials || []).forEach((material, i) => {
    if (!kmGraphMaterialOn(i)) return;
    let sx = 0; let sy = 0; let n = 0;
    graph.nodes.forEach((node, index) => {
      if (materialIndex[String(node.material_id)] !== i) return;
      if (!kmGraphVisible(graph, index)) return;
      sx += pos[index * 2]; sy += pos[index * 2 + 1]; n += 1;
    });
    if (!n) return;
    ctx.fillStyle = kmGraphMaterialColor(material.material_id);
    ctx.globalAlpha = 0.85;
    ctx.fillText(String(material.filename || material.material_id || '').slice(0, 34),
      sx / n - 60, sy / n - 118);
    ctx.globalAlpha = 1;
  });
  ctx.restore();
}

function kmGraphPick(canvas, graph, clientX, clientY) {
  const rect = canvas.getBoundingClientRect();
  const view = KM_GRAPH_STATE.view;
  const x = (clientX - rect.left - view.x) / view.k;
  const y = (clientY - rect.top - view.y) / view.k;
  const pos = kmGraphPositions(graph);
  let best = -1;
  let bestDistance = 12 / view.k;
  for (let i = 0; i < graph.nodes.length; i += 1) {
    if (!kmGraphVisible(graph, i)) continue;
    const d = Math.hypot(pos[i * 2] - x, pos[i * 2 + 1] - y);
    if (d < bestDistance) { bestDistance = d; best = i; }
  }
  return best;
}

function kmKnowledgeHref(knowledgeId) {
  return '#/courses/' + encodeURIComponent(KM_STATE.courseId) +
    '/knowledge/' + encodeURIComponent(String(knowledgeId));
}

/**
 * 某个知识点的**已登记**关系 (按出边 / 入边分开)。
 *
 * 数据是后端回传的 ``relations`` (与详情页 ``trace.dependencies`` 同一张表),
 * 这里只做投影, 不推断、不补全: 没有就是空数组, 面板就不出现那一块。
 */
function kmGraphRegisteredRelationsFor(knowledgeId) {
  const id = String(knowledgeId || '');
  const out = { outgoing: [], incoming: [] };
  if (!id) return out;
  (KM_STATE.relations || []).forEach((relation) => {
    const source = String((relation || {}).source_knowledge_point_id || '');
    const target = String((relation || {}).target_knowledge_point_id || '');
    if (source !== id && target !== id) return;
    const entry = {
      other: source === id ? target : source,
      relation_type: String((relation || {}).relation_type || ''),
    };
    if (source === id) out.outgoing.push(entry);
    else out.incoming.push(entry);
  });
  return out;
}

function kmGraphDrawDetail(graph) {
  const box = document.getElementById('km-graph-detail');
  if (!box) return;
  const index = KM_GRAPH_STATE.selected;
  if (index < 0 || !graph.nodes[index]) {
    box.className = 'small muted';
    box.textContent = t('km.graph.noSelection');
    return;
  }
  const node = graph.nodes[index];
  const adjacency = kmGraphAdjacency(graph);
  const neighbours = (adjacency.get(index) || []).map((j) => {
    const other = graph.nodes[j];
    return '<li><span>' + esc(String(other.label).slice(0, 40)) + '</span><span>' +
      (other.kind === 'concept' ? esc(t('km.graph.nodeConcept')) : '') + '</span></li>';
  }).join('');
  // 已登记关系单独分段: 与上面的"相连的节点"不是同一种东西 —— 上面那个是
  // 画布上真有的边 (含推断), 这里是**有向有类型**的权威事实。
  const registered = node.kind === 'concept'
    ? { outgoing: [], incoming: [] }
    : kmGraphRegisteredRelationsFor(node.id);
  const relationRow = (entry, outgoing) =>
    '<li><span>' + (outgoing ? '→ ' : '← ') +
    '<a href="' + esc(kmKnowledgeHref(entry.other)) + '">' + esc(entry.other) + '</a></span>' +
    '<span>' + (entry.relation_type ? pill(entry.relation_type) : '—') + '</span></li>';
  const registeredBlock = (registered.outgoing.length || registered.incoming.length)
    ? '<div class="tiny muted" style="margin-top:6px">' + esc(t('km.graph.relTitle')) +
      ' (' + esc(registered.outgoing.length + registered.incoming.length) + ')</div>' +
      '<ul class="plain km-graph-relations">' +
      registered.outgoing.map((entry) => relationRow(entry, true)).join('') +
      registered.incoming.map((entry) => relationRow(entry, false)).join('') +
      '</ul>'
    : '';
  box.className = '';
  box.innerHTML =
    '<div class="km-graph-title">' + esc(node.label) + '</div>' +
    (node.kind === 'concept'
      ? '<div class="tiny muted">' + esc(t('km.graph.nodeConcept')) + '</div>'
      : '<div class="tiny muted">' + esc(kmMaterialLabel(
        (KM_STATE.materials || []).filter(
          (m) => String(m.material_id) === String(node.material_id))[0])) + '</div>') +
    '<div class="tiny muted">' + esc(t('km.graph.degree')) + ': ' + node.degree + '</div>' +
    // 真实链接 (不是 location.hash 赋值): 选中的知识点必须在键盘和鼠标下
    // 都能进详情页 —— 画布里不重造一份详情。概念节点没有详情页, 所以不给链接。
    (node.kind === 'concept'
      ? ''
      : '<div class="tiny" style="margin-top:4px"><a href="' + esc(kmKnowledgeHref(node.id)) +
        '">' + esc(t('km.graph.openDetail')) + '</a></div>') +
    registeredBlock +
    (neighbours
      ? '<div class="tiny muted" style="margin-top:6px">' + esc(t('km.graph.connected')) + '</div>' +
        '<ul class="plain">' + neighbours + '</ul>'
      : '');
}

function kmGraphOpenNode(node) {
  if (!node || node.kind === 'concept' || !node.id) return false;
  const target = kmKnowledgeHref(node.id);
  if (window.location.hash !== target) window.location.hash = target;
  return true;
}

/**
 * 滚轮处理 —— **只有 Ctrl/⌘ + 滚轮才缩放**。
 *
 * 返回"这次到底处理了没有", 而不是默默改变行为: 调用方与审计都能直接看到
 * 判断结果, 不需要去猜 ``defaultPrevented``。普通滚轮返回 false **且不调
 * preventDefault** —— 页面照常滚动。
 */
function kmGraphWheel(canvas, graph, event) {
  if (!canvas || !graph || !event) return false;
  if (!(event.ctrlKey || event.metaKey)) return false;
  if (typeof event.preventDefault === 'function') event.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = event.clientX - rect.left;
  const my = event.clientY - rect.top;
  const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
  const view = KM_GRAPH_STATE.view;
  view.x = mx - (mx - view.x) * factor;
  view.y = my - (my - view.y) * factor;
  view.k *= factor;
  kmGraphDraw(canvas, graph);
  return true;
}

// ---- 键盘导航 -------------------------------------------------------------
//
// 画布是**可键盘浏览**的: Tab 聚焦 → 方向键在节点间跳 → 回车进详情。
// 为什么需要几何方向选择而不是"下一个节点": 图上节点的"下一个"没有自然顺序,
// 而用户按 → 时心里想的是"右边那个点", 所以按坐标挑.
//
// 全部确定性: 同样数据 + 同样起点 + 同样按键 → 同样终点。

function kmGraphVisibleIndexes(graph) {
  const out = [];
  for (let i = 0; i < graph.nodes.length; i += 1) {
    if (kmGraphVisible(graph, i)) out.push(i);
  }
  return out;
}

function kmGraphMoveSelection(graph, key) {
  const dirs = {
    ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1],
  };
  const dir = dirs[key];
  if (!dir) return KM_GRAPH_STATE.selected;
  const visible = kmGraphVisibleIndexes(graph);
  if (!visible.length) return -1;
  const current = KM_GRAPH_STATE.selected;
  if (current < 0 || visible.indexOf(current) < 0) return visible[0];
  const pos = kmGraphPositions(graph);
  const ox = pos[current * 2];
  const oy = pos[current * 2 + 1];
  let best = -1;
  let bestScore = Infinity;
  visible.forEach((i) => {
    if (i === current) return;
    const dx = pos[i * 2] - ox;
    const dy = pos[i * 2 + 1] - oy;
    const along = dx * dir[0] + dy * dir[1];
    if (along <= 0) return;                    // 只往按键那个方向走
    const side = Math.abs(dx * dir[1] - dy * dir[0]);
    if (side > along * 1.6) return;            // 夹角太大不箸这个方向 (约 ±58°)
    const score = along + side * 2;
    if (score < bestScore) { bestScore = score; best = i; }
  });
  return best < 0 ? current : best;
}

// 把选中节点拉回可视区 (否则方向键选中的点可能在屏幕外, 看着像没反应)。
function kmGraphRevealNode(canvas, graph, index) {
  const pos = kmGraphPositions(graph);
  const dpr = canvas.__kmDpr || 1;
  const cw = (canvas.width || 0) / dpr;
  const ch = (canvas.height || 0) / dpr;
  if (!cw || !ch) return;
  const view = KM_GRAPH_STATE.view;
  const sx = pos[index * 2] * view.k + view.x;
  const sy = pos[index * 2 + 1] * view.k + view.y;
  const margin = 60;
  let dx = 0;
  let dy = 0;
  if (sx < margin) dx = margin - sx;
  else if (sx > cw - margin) dx = (cw - margin) - sx;
  if (sy < margin) dy = margin - sy;
  else if (sy > ch - margin) dy = (ch - margin) - sy;
  if (!dx && !dy) return;
  view.x += dx;
  view.y += dy;
  kmGraphDraw(canvas, graph);
}

/**
 * 键盘处理 (名字导出而不是内联闭包 —— 行为要能被直接验证, 而不是只能看代码)。
 * 返回 true 表示这个键被画布接管了。
 */
function kmGraphKey(canvas, graph, event) {
  if (!canvas || !graph || !event) return false;
  const key = String(event.key || '');
  if (key === 'Enter' || key === ' ' || key === 'Spacebar') {
    // 回车 = 打开选中知识点的详情页。概念节点没有详情页 (它不是知识点),
    // 所以这里**什么也不做** —— 而不是随便找个页面跳过去。
    const index = KM_GRAPH_STATE.selected;
    const node = index >= 0 ? graph.nodes[index] : null;
    if (node && node.kind !== 'concept' && typeof event.preventDefault === 'function') {
      event.preventDefault();
    }
    return kmGraphOpenNode(node);
  }
  if (key === 'Escape') {
    KM_GRAPH_STATE.selected = -1;
    kmGraphDraw(canvas, graph);
    kmGraphDrawDetail(graph);
    return true;
  }
  if (key === 'Home') {
    if (typeof event.preventDefault === 'function') event.preventDefault();
    kmGraphFit(canvas, graph);
    return true;
  }
  if (key.indexOf('Arrow') === 0) {
    // preventDefault 只拦**我们真的处理了**的键: 画布有焦点时方向键属于
    // 这张图, 页面不该同时滚。
    if (typeof event.preventDefault === 'function') event.preventDefault();
    const next = kmGraphMoveSelection(graph, key);
    if (next < 0 || next === KM_GRAPH_STATE.selected) return true;
    KM_GRAPH_STATE.selected = next;
    KM_GRAPH_STATE.hover = -1;
    kmGraphDraw(canvas, graph);
    kmGraphDrawDetail(graph);
    kmGraphRevealNode(canvas, graph, next);
    return true;
  }
  return false;
}

function kmGraphBind(canvas, graph) {
  canvas.addEventListener('mousemove', (event) => {
    if (KM_GRAPH_STATE.dragging) {
      KM_GRAPH_STATE.view.x += event.movementX;
      KM_GRAPH_STATE.view.y += event.movementY;
      kmGraphDraw(canvas, graph);
      return;
    }
    const index = kmGraphPick(canvas, graph, event.clientX, event.clientY);
    if (index !== KM_GRAPH_STATE.hover) {
      KM_GRAPH_STATE.hover = index;
      kmGraphDraw(canvas, graph);
    }
    const tip = document.getElementById('km-graph-tip');
    if (!tip) return;
    if (index < 0) { tip.hidden = true; return; }
    const node = graph.nodes[index];
    const rect = canvas.getBoundingClientRect();
    tip.hidden = false;
    tip.style.left = Math.min(event.clientX - rect.left + 14, Math.max(0, rect.width - 340)) + 'px';
    tip.style.top = (event.clientY - rect.top + 14) + 'px';
    tip.innerHTML = '<div class="km-graph-title">' + esc(node.label) + '</div>' +
      '<div class="tiny muted">' + (node.kind === 'concept'
        ? esc(t('km.graph.nodeConcept'))
        : esc(t('km.graph.degree')) + ': ' + node.degree) + '</div>';
  });
  canvas.addEventListener('mouseleave', () => {
    KM_GRAPH_STATE.hover = -1;
    const tip = document.getElementById('km-graph-tip');
    if (tip) tip.hidden = true;
    kmGraphDraw(canvas, graph);
  });
  canvas.addEventListener('mousedown', () => { KM_GRAPH_STATE.dragging = true; });
  window.addEventListener('mouseup', () => { KM_GRAPH_STATE.dragging = false; });
  canvas.addEventListener('keydown', (event) => { kmGraphKey(canvas, graph, event); });
  canvas.addEventListener('wheel', (event) => {
    // 2026-10-13: 只有 Ctrl/⌘ + 滚轮才缩放, 普通滚轮到页面滚动。
    //
    // 以前无条件 ``event.preventDefault()``: 鼠标只要落在画布上就无法滚页
    // —— 画布高 520px 且在页面中部, 等于把整页滚动截成两段。键盘/触控板的
    // 普通滚动是绝大多数人的翻页方式, 而缩放只是偶发操作。
    kmGraphWheel(canvas, graph, event);
  }, { passive: false });
  canvas.addEventListener('click', (event) => {
    KM_GRAPH_STATE.selected = kmGraphPick(canvas, graph, event.clientX, event.clientY);
    kmGraphDraw(canvas, graph);
    kmGraphDrawDetail(graph);
  });
}

function kmGraphAction(action, node) {
  const canvas = document.getElementById('km-graph-canvas');
  const graph = kmGraph();
  if (!graph) return false;
  if (action === 'km-graph-fit' && canvas) {
    kmGraphFit(canvas, graph);
    return true;
  }
  if (action === 'km-graph-only-linked') {
    KM_GRAPH_STATE.onlyLinked = !KM_GRAPH_STATE.onlyLinked;
    if (node) node.setAttribute('aria-pressed', KM_GRAPH_STATE.onlyLinked ? 'true' : 'false');
    if (canvas) kmGraphFit(canvas, graph);
    return true;
  }
  if (action === 'km-graph-concepts') {
    KM_GRAPH_STATE.showConcepts = !KM_GRAPH_STATE.showConcepts;
    if (node) node.setAttribute('aria-pressed', KM_GRAPH_STATE.showConcepts ? 'true' : 'false');
    if (canvas) kmGraphDraw(canvas, graph);
    return true;
  }
  if (action === 'km-graph-labels') {
    KM_GRAPH_STATE.showLabels = !KM_GRAPH_STATE.showLabels;
    if (node) node.setAttribute('aria-pressed', KM_GRAPH_STATE.showLabels ? 'true' : 'false');
    if (canvas) kmGraphDraw(canvas, graph);
    return true;
  }
  return false;
}

// 工具栏搜索框在图谱视图下逐字符触发: 只重绘 canvas, 不重建 DOM
// (重建会让输入框掉焦点)。搜索命中表现为节点加高亮圈。
function kmGraphSearchChanged() {
  const canvas = document.getElementById('km-graph-canvas');
  const graph = kmGraph();
  if (!graph || !canvas) return;
  kmGraphDraw(canvas, graph);
}
