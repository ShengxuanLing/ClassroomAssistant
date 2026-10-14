/*
 * 知识图谱绘制基准 (只读, 不进门禁, 不改任何东西)。
 *
 * 为什么存在: 任务书 §1.6 要求"先基准, 再决定要不要优化" —— 不允许凭理论复杂度
 * 声称解决了性能问题。这里直接加载**仓库里那份** src/web/views/knowledge-graph.js,
 * 用真实 knowledge-map 载荷跑热路径, 不复制任何绘制逻辑 (复制的基准会漂)。
 *
 * 用法:
 *   node scripts/bench_knowledge_graph.js --synth 380 255     # 合成 (标注 synthetic)
 *   node scripts/bench_knowledge_graph.js <payload.json>      # 真实载荷 (标注 real)
 *   # 真实载荷由 python temp/measure_knowledge_map.py --dump <path> [course] 产出
 *
 * 数据来源如实标注: 真实 vs 合成, 两者分开报, 不混为一谈。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');

function makeCanvas(width, height) {
  const calls = { stroke: 0, fill: 0, fillText: 0, arc: 0 };
  const ctx = {
    lineWidth: 1, strokeStyle: '', fillStyle: '', font: '', globalAlpha: 1,
    setTransform() {}, clearRect() {}, fillRect() {}, save() {}, restore() {},
    translate() {}, scale() {}, beginPath() {}, moveTo() {}, lineTo() {},
    setLineDash() {},
    stroke() { calls.stroke += 1; }, arc() { calls.arc += 1; },
    fill() { calls.fill += 1; }, fillText() { calls.fillText += 1; },
  };
  return {
    width, height, clientWidth: width, clientHeight: height, __kmDpr: 1, __calls: calls,
    getContext: () => ctx,
    getBoundingClientRect: () => ({ left: 0, top: 0, width, height, right: width, bottom: height }),
    addEventListener() {}, setAttribute() {}, getAttribute: () => null,
  };
}

function makeElement(id) {
  return { id, innerHTML: '', textContent: '', className: '', style: {}, hidden: false,
    setAttribute() {}, getAttribute: () => null, addEventListener() {}, remove() {} };
}

function synthPayload(nodes, edges) {
  const knowledge = [];
  const graphNodes = [];
  const graphEdges = [];
  for (let i = 0; i < nodes; i += 1) {
    const id = 'kp-' + String(i).padStart(5, '0');
    knowledge.push({ knowledge_id: id, title: 'Punto de conocimiento numero ' + i,
      generation_mode: i % 3 === 0 ? 'ai_summary' : 'deterministic_fallback',
      validation_status: i % 7 === 0 ? 'conflicted' : 'unverified',
      review_status: 'pending', evidence_refs: ['evid-' + (i % 40)],
      original_terms: ['termino ' + i] });
    graphNodes.push({ id, kind: 'kp', label: knowledge[i].title,
      material_id: 'material-' + (i % 6), degree: 0,
      generation: knowledge[i].generation_mode, status: knowledge[i].validation_status });
  }
  for (let e = 0; e < edges; e += 1) {
    const a = e % nodes;
    const b = (e * 7 + 3) % nodes;
    if (a === b) continue;
    graphEdges.push({ source: graphNodes[a].id, target: graphNodes[b].id, kind: 'kp',
      origin: e % 5 === 0 ? 'registered' : 'content_hint',
      directed: e % 5 === 0, relation_type: e % 5 === 0 ? 'PREREQUISITE' : '' });
  }
  return { course_id: 'synthetic-course', knowledge_points: knowledge,
    materials: Array.from({ length: 6 }, (_, i) => ({ material_id: 'material-' + i,
      filename: 'material-' + i + '.pdf', material_type: 'document', size: 1024 })),
    relations: [], topics: [], knowledge_memberships: [], conflicts: [],
    stats: { knowledge_point_count: nodes, material_count: 6, evidence_count: 40,
      evidence_ref_count: nodes, topic_count: 0, relation_count: 0, conflict_count: 0 },
    graph: { nodes: graphNodes, edges: graphEdges, concepts: [],
      unresolved_terms: [], skipped_terms: [],
      stats: { knowledge_point_count: nodes, material_count: 6,
        edge_count: graphEdges.length, concept_edge_count: 0, concept_count: 0,
        linked_knowledge_point_count: nodes, isolated_knowledge_point_count: 0,
        relation_instance_count: 0, registered_relation_count: 0,
        content_hint_edge_count: graphEdges.length, dropped_relation_count: 0,
        unresolved_term_count: 0, skipped_term_count: 0 } } };
}

function makeSandbox(payload, canvas) {
  const elements = new Map([['km-graph-canvas', canvas],
    ['km-graph-tip', makeElement('km-graph-tip')],
    ['km-graph-detail', makeElement('km-graph-detail')]]);
  const style = { getPropertyValue: () => '' };
  const sandbox = {
    console, Math, JSON, Object, Array, Number, String, Boolean, Set, Map,
    Float64Array, isFinite, Infinity,
    document: {
      getElementById: (id) => {
        if (!elements.has(id)) elements.set(id, makeElement(id));
        return elements.get(id);
      },
      querySelectorAll: () => [], addEventListener() {},
      documentElement: makeElement('html'),
    },
    window: {
      devicePixelRatio: 1,
      location: { hash: '#/courses/bench/knowledge' },
      addEventListener() {},
      matchMedia: () => ({ matches: false, addEventListener() {} }),
      getComputedStyle: () => style,
    },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    KM_STATE: {
      knowledge: payload.knowledge_points || [], materials: payload.materials || [],
      clusters: {}, orphans: [], conflicts: payload.conflicts || [],
      relations: payload.relations || [], topics: payload.topics || [],
      memberships: payload.knowledge_memberships || [], stats: payload.stats || {},
      graph: payload.graph, courseId: payload.course_id || 'bench', query: '',
      activeMaterial: 'all', view: 'graph', loaded: true,
    },
    esc: (value) => String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;'),
    t: (key, vars) => {
      if (!vars) return key;
      return key.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? String(vars[name]) : m));
    },
    pill: (value) => '<span class="pill">' + String(value) + '</span>',
    kmMaterialLabel: (material) => String((material || {}).filename || ''),
    tableCaption: () => '', dash: (v) => String(v == null ? '-' : v),
    fmtBytes: () => '0 B', emptyState: (text) => '<p>' + text + '</p>',
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  const source = fs.readFileSync(path.join(ROOT, 'src/web/views/knowledge-graph.js'), 'utf8');
  vm.runInContext(source, sandbox, { filename: 'src/web/views/knowledge-graph.js' });
  return sandbox;
}

function timeIt(label, fn, iterations) {
  fn();
  const started = process.hrtime.bigint();
  for (let i = 0; i < iterations; i += 1) fn();
  const elapsedMs = Number(process.hrtime.bigint() - started) / 1e6;
  const perCall = elapsedMs / iterations;
  console.log('  ' + label.padEnd(32) + elapsedMs.toFixed(1).padStart(9) +
    ' ms total' + perCall.toFixed(3).padStart(9) + ' ms/call');
  return Number(perCall.toFixed(4));
}

function run(label, payload, size) {
  const canvas = makeCanvas(size[0], size[1]);
  const sandbox = makeSandbox(payload, canvas);
  const graph = payload.graph;
  console.log('\n' + label + ': ' + graph.nodes.length + ' nodes / ' +
    graph.edges.length + ' edges / ' + (payload.materials || []).length + ' materials');
  const out = {};
  out.layoutKeyCold = timeIt('kmGraphLayoutKey (cold)', () => {
    vm.runInContext('KM_GRAPH_STATE.keyOf = null; KM_GRAPH_STATE.keyValue = "";', sandbox);
    vm.runInContext('kmGraphLayoutKey(KM_STATE.graph);', sandbox);
  }, 200);
  out.layoutKeyWarm = timeIt('kmGraphLayoutKey (cached)', () => {
    vm.runInContext('kmGraphLayoutKey(KM_STATE.graph);', sandbox);
  }, 2000);
  out.layoutOnce = timeIt('kmGraphComputeLayout (one-off)', () => {
    vm.runInContext('KM_GRAPH_STATE.pos = null; KM_GRAPH_STATE.layoutKey = "";', sandbox);
    vm.runInContext('kmGraphComputeLayout(KM_STATE.graph);', sandbox);
  }, 3);
  out.adjacency = timeIt('kmGraphAdjacency', () => {
    vm.runInContext('kmGraphAdjacency(KM_STATE.graph);', sandbox);
  }, 300);
  out.drawFrame = timeIt('kmGraphDraw (per frame)', () => {
    vm.runInContext('kmGraphDraw(document.getElementById("km-graph-canvas"), KM_STATE.graph);', sandbox);
  }, 200);
  out.pick = timeIt('kmGraphPick (per hover)', () => {
    vm.runInContext('kmGraphPick(document.getElementById("km-graph-canvas"), KM_STATE.graph, 600, 400);', sandbox);
  }, 200);
  out.detail = timeIt('kmGraphDrawDetail', () => {
    vm.runInContext('kmGraphDrawDetail(KM_STATE.graph);', sandbox);
  }, 200);
  console.log('  canvas ops: stroke=' + canvas.__calls.stroke + ' fill=' + canvas.__calls.fill +
    ' arc=' + canvas.__calls.arc + ' fillText=' + canvas.__calls.fillText);
  return out;
}

function main() {
  const args = process.argv.slice(2);
  let synth = null;
  let file = '';
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === '--synth') { synth = [Number(args[i + 1]), Number(args[i + 2])]; i += 2; }
    else file = args[i];
  }
  console.log('node ' + process.version + ' · ' + new Date().toISOString());
  if (synth) {
    console.log('\n# source = synthetic (合成数据, 明确标注)');
    console.log(JSON.stringify({ synthetic: run('SYNTHETIC ' + synth[0] + '/' + synth[1],
      synthPayload(synth[0], synth[1]), [1200, 820]) }, null, 2));
    return;
  }
  if (!file) {
    console.error('usage: node scripts/bench_knowledge_graph.js <payload.json> | --synth N E');
    process.exitCode = 2;
    return;
  }
  const payload = JSON.parse(fs.readFileSync(path.resolve(ROOT, file), 'utf8'));
  console.log('\n# source = real payload ' + file);
  console.log(JSON.stringify({ real: run('REAL ' + file, payload, [1200, 820]) }, null, 2));
}

main();
