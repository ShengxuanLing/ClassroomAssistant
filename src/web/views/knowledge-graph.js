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

function kmGraphLayoutKey(graph) {
  // 数据指纹: 节点数 + 边数 + 材料数。变了就重算布局。
  return [graph.nodes.length, graph.edges.length, (KM_STATE.materials || []).length].join(':');
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
    '<canvas id="km-graph-canvas" aria-label="' + esc(t('km.title')) + '"></canvas>' +
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
    '<div id="km-graph-detail" class="small muted">' + esc(t('km.graph.noSelection')) + '</div>' +
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
  kmGraphPositions(graph);
  if (KM_GRAPH_STATE.boundCanvas !== canvas) {
    kmGraphBind(canvas, graph);
    KM_GRAPH_STATE.boundCanvas = canvas;
  }
  kmGraphResize(canvas, graph);
  // 每份数据自动"适应窗口"一次 —— 否则首屏停在 (0,0)/1x, 簇被推到画布
  // 边缘外, 看起来像没画。之后用户自己缩放/平移, 重绘不再重置视角。
  if (KM_GRAPH_STATE.fittedKey !== KM_GRAPH_STATE.layoutKey) {
    kmGraphFit(canvas, graph);
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

function kmGraphFit(canvas, graph) {
  const pos = kmGraphPositions(graph);
  let minX = Infinity; let maxX = -Infinity; let minY = Infinity; let maxY = -Infinity;
  for (let i = 0; i < graph.nodes.length; i += 1) {
    if (!kmGraphVisible(graph, i)) continue;
    minX = Math.min(minX, pos[i * 2]); maxX = Math.max(maxX, pos[i * 2]);
    minY = Math.min(minY, pos[i * 2 + 1]); maxY = Math.max(maxY, pos[i * 2 + 1]);
  }
  if (!isFinite(minX)) return;
  const dpr = canvas.__kmDpr || 1;
  const cw = canvas.width / dpr;
  const ch = canvas.height / dpr;
  const pad = 50;
  const k = Math.min(cw / (maxX - minX + pad * 2), ch / (maxY - minY + pad * 2));
  KM_GRAPH_STATE.view.k = k;
  KM_GRAPH_STATE.view.x = cw / 2 - ((minX + maxX) / 2) * k;
  KM_GRAPH_STATE.view.y = ch / 2 - ((minY + maxY) / 2) * k;
  kmGraphDraw(canvas, graph);
}

function kmGraphMaterialColor(materialId) {
  const index = kmGraphMaterialIndex()[String(materialId)];
  const palette = ['#5b8cff', '#ff9a5b', '#3ecf8e', '#c98bff', '#f2c14b'];
  if (index === undefined) return '#8c95ac';
  return palette[index % palette.length];
}

function kmGraphDraw(canvas, graph) {
  if (!canvas || typeof canvas.getContext !== 'function') return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  const pos = kmGraphPositions(graph);
  const dpr = canvas.__kmDpr || 1;
  const view = KM_GRAPH_STATE.view;
  const cw = (canvas.width || 0) / dpr;
  const ch = (canvas.height || 0) / dpr;
  if (!cw || !ch) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cw, ch);
  ctx.fillStyle = '#101219';
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

  ctx.lineWidth = 1 / view.k;
  graph.edges.forEach((edge) => {
    const a = idIndex[String(edge.source)];
    const b = idIndex[String(edge.target)];
    if (a === undefined || b === undefined) return;
    if (!kmGraphVisible(graph, a) || !kmGraphVisible(graph, b)) return;
    const inFocus = hasFocus && focusSet.has(a) && focusSet.has(b);
    const isConceptEdge = edge.kind === 'concept';
    ctx.strokeStyle = inFocus
      ? 'rgba(122,162,255,.95)'
      : (hasFocus ? 'rgba(255,255,255,.04)'
        : (isConceptEdge ? 'rgba(140,149,172,.16)' : 'rgba(255,255,255,.13)'));
    ctx.beginPath();
    ctx.moveTo(pos[a * 2], pos[a * 2 + 1]);
    ctx.lineTo(pos[b * 2], pos[b * 2 + 1]);
    ctx.stroke();
  });

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
      ctx.strokeStyle = '#9aa3b8';
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = 'rgba(154,163,184,.14)';
      ctx.fill();
    } else {
      const r = (3 + Math.min(9, node.degree) * 0.8) / Math.sqrt(view.k);
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = kmGraphMaterialColor(node.material_id);
      ctx.fill();
      if (node.status === 'conflicted') {
        ctx.lineWidth = 1.7 / view.k; ctx.strokeStyle = '#ff6b6b'; ctx.stroke();
      } else if (node.status === 'unverified') {
        ctx.lineWidth = 1.2 / view.k; ctx.strokeStyle = '#f2c14b'; ctx.stroke();
      }
    }
    if (i === KM_GRAPH_STATE.selected) {
      ctx.lineWidth = 2 / view.k; ctx.strokeStyle = '#ffffff'; ctx.stroke();
    }
    if (needle && String(node.label || '').toLowerCase().indexOf(needle) >= 0) {
      ctx.lineWidth = 2.2 / view.k; ctx.strokeStyle = '#7affc9'; ctx.stroke();
    }
    if (KM_GRAPH_STATE.showLabels || i === KM_GRAPH_STATE.hover || i === KM_GRAPH_STATE.selected) {
      ctx.font = (11 / view.k) + "px 'Segoe UI',sans-serif";
      ctx.fillStyle = '#dfe4f2';
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
  box.className = '';
  box.innerHTML =
    '<div class="km-graph-title">' + esc(node.label) + '</div>' +
    (node.kind === 'concept'
      ? '<div class="tiny muted">' + esc(t('km.graph.nodeConcept')) + '</div>'
      : '<div class="tiny muted">' + esc(kmMaterialLabel(
        (KM_STATE.materials || []).filter(
          (m) => String(m.material_id) === String(node.material_id))[0])) + '</div>') +
    '<div class="tiny muted">' + esc(t('km.graph.degree')) + ': ' + node.degree + '</div>' +
    (neighbours
      ? '<div class="tiny muted" style="margin-top:6px">' + esc(t('km.graph.connected')) + '</div>' +
        '<ul class="plain">' + neighbours + '</ul>'
      : '');
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
  canvas.addEventListener('wheel', (event) => {
    event.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
    const view = KM_GRAPH_STATE.view;
    view.x = mx - (mx - view.x) * factor;
    view.y = my - (my - view.y) * factor;
    view.k *= factor;
    kmGraphDraw(canvas, graph);
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
