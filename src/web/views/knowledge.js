/*
 * 知识点列表 (``#/knowledge``) 与知识点详情 (``#/courses/<id>/knowledge/<kp>``)。
 */
'use strict';

// 原文术语列 (列表 + 详情) 的渲染。
//
// 关键坑: 真实数据里 `original_terms` 经常**不是离散术语**, 而是一整段 / 一整页的
// 源材料摘录 (带换行的大块西语 / 加泰文本, 见 DB: 一个元素就是一整页讲义)。把这种
// 大块整段整段塞进 .pill, 会生成一个撑满整列的"大圆角块 / 圈" —— 既难看又误导。
// 所以只有在它确实像术语标签 (短且无换行) 时才用 pill; 否则降级成截断的小字预览
// (+ 浮层全文), 详情页则给足空间展示完整文本。
function knowledgeGenerationMode(kp) {
  const explicit = String((kp || {}).generation_mode || '');
  if (explicit) return explicit;
  const id = String((kp || {}).knowledge_id || '');
  if (id.indexOf('aikp-') === 0) return 'ai_summary';
  if (id.indexOf('kp-') === 0) return 'deterministic_fallback';
  return 'manual';
}

function knowledgeModeBadge(kp) {
  const mode = knowledgeGenerationMode(kp);
  if (mode === 'ai_summary') {
    return ' <span class="pill pill-ok tiny">' + esc(t('knowledge.aiSummaryBadge')) + '</span>';
  }
  if (mode === 'deterministic_fallback') {
    return ' <span class="pill pill-warn tiny">' + esc(t('knowledge.fallbackBadge')) + '</span>';
  }
  return '';
}

function knowledgeModeNotice(kp) {
  const mode = knowledgeGenerationMode(kp);
  if (mode === 'ai_summary') {
    return '<div class="banner">' + esc(t('knowledge.aiSummaryNotice')) + '</div>';
  }
  if (mode === 'deterministic_fallback') {
    return '<div class="banner">' + esc(t('knowledge.fallbackNotice')) + '</div>';
  }
  return '';
}

function renderTerms(terms, opts) {
  opts = opts || {};
  const limit = opts.limit || 3;
  const full = !!opts.full;
  if (!terms || !terms.length) return '—';
  const shown = terms.slice(0, limit).map((raw) => {
    const term = String(raw == null ? '' : raw);
    if (term.length <= 40 && !/[\n\r]/.test(term)) {
      return '<span class="pill pill-muted tiny">' + esc(term) + '</span>';
    }
    const flat = term.replace(/[\n\r]+/g, ' ').replace(/\s{2,}/g, ' ').trim();
    const text = full ? flat : (flat.slice(0, 120) + (flat.length > 120 ? '…' : ''));
    return '<span class="' + (full ? 'small break-all' : 'small muted break-all') +
      '" title="' + esc(term) + '">' + esc(text) + '</span>';
  }).join(' ');
  const more = (terms.length > limit)
    ? ' <span class="tiny muted" title="' + esc(terms.join(', ')) + '">+' +
      esc(terms.length - limit) + '</span>'
    : '';
  return shown + more;
}

// ---- TASK-81 §C: 解释语言由证据决定 (不再由用户选) ----------------------
//
// 后端 ``learning_view`` 只在"存在该语言的证据"时才给解释 (否则
// ``language_not_available``), 所以语言**必须**从证据推出来, 而不是问用户:
// 用户能选的值里没有一个能凭空造出证据。
//
// 顺序: 证据语言 (溯源包已声明) → 材料声明语言 → 后端同款默认 es。取不到就
// 回落而不是报错: 解释面板本来就允许显示"无解释"的合法空态。
function explanationLanguage(trace) {
  const declared = ((trace || {}).language || {}).evidence_languages || [];
  for (const code of declared) {
    if (code) return String(code);
  }
  const materials = (trace || {}).materials || [];
  for (const material of materials) {
    const code = String((material || {}).language || '');
    if (code) return code;
  }
  return 'es';
}

// ---- TASK-81 §A2: 一键补翻历史知识点 ------------------------------------
//
// 自动阶段只覆盖"分析之后"的知识点; 更早的那批永远没有中文。这个按钮是它们
// 唯一的批量补法, 幂等 (已缓存的不重复花钱), 耗时可能到分钟级 —— 任务坞占位
// + toast 报计数, 页面本身不等它。
async function actionBackfillKpTranslations(courseId, button) {
  if (!courseId) return;
  const taskId = startTask(t('kpZh.backfillRunning') + ' · ' + courseLabel(courseId));
  if (button) button.disabled = true;
  try {
    const result = await api('/courses/' + encodeURIComponent(courseId) +
      '/kp-translations/backfill', { method: 'POST', body: {} });
    const detail = t('kpZh.backfillDone') + ': ' + result.translated + ' / ' +
      result.total + ' · ' + t('kpZh.backfillCached') + ' ' + result.cached +
      ' · ' + t('kpZh.backfillSkipped') + ' ' + result.skipped +
      ' · ' + t('kpZh.backfillFailed') + ' ' + result.failed;
    finishTask(taskId, !result.failed, detail);
    toast(detail, result.failed ? 'bad' : 'ok');
  } catch (err) {
    finishTask(taskId, false, err.code + ' ' + err.message);
    toast(t('kpZh.backfillFailedToast') + ' [' + err.code + '] ' + err.message, 'bad');
  } finally {
    if (button) button.disabled = false;
  }
}

// ==================================================================
// TASK-KM-UI-V2: 课程知识地图 (Course Knowledge Map)
//
// 从 "Graph View" 重构为 "Course Knowledge Map":
//
//     Course ── Topic ── Canonical Knowledge ── (Sub Knowledge)
//
// 设计原则 (spec §1):
//   * parent/child 是**主布局关系**, relation 是**辅助关系**;
//   * 默认不展开全部节点, 按数量阈值折叠 (spec §7);
//   * 层级边 (实线, 低调) 与语义关系边 (虚线, 默认关闭) 必须区分 (spec §11);
//   * Canvas = 导航, Detail Panel = 阅读 (spec §39)。
//
// 技术选型 (spec §2 / §9): 本项目是零构建 / 零依赖的 Vanilla JS + SVG。
// 引入 ELK.js / Dagre 会违反 "不加不必要依赖" 铁律, 也会破坏既有的
// "前端资源自包含 (无 CDN / 无构建链)" 门禁。因此本任务在现有 SVG 栈内
// **自研分层树布局** (LR), 实现同等的 hierarchical layout / expand-collapse /
// zoom / pan —— 这正是 spec §2 允许的路线。
//
// 布局与数据模型分离: 后端 Canonical Knowledge 语义不动, 前端只做
//   projection -> layout -> interaction -> visualization -> navigation。
//
// 为什么不是 canvas: 200 个节点以内 SVG 完全够用, 节点文字天然可选中 /
// 可无障碍朗读; 缩放/拖动只改一个 <g> 的 transform, 不碰 DOM 结构。
// ==================================================================

// ---- 布局 / 展开阈值常量 (集中一处, 禁止散落 —— spec §7) ------------------

//: Topic 下知识点 <= 此数: 默认展开。
const KM_EXPAND_MAX_CHILDREN = 8;
//: 9..20 与 >20: 默认折叠 (spec §7 的分档上界, 供"摘要"文案使用)。
const KM_SUMMARY_MAX_CHILDREN = 20;
//: 节点宽度范围 (spec §13)。
const KM_NODE_MIN_W = 180;
const KM_NODE_MAX_W = 280;
//: 节点高度 (容纳 2 行标题 + 1 行计数)。
const KM_NODE_H = 64;
//: 相邻层之间的水平间距。
const KM_LEVEL_GAP_X = 96;
//: 同层相邻行的垂直间距。
const KM_ROW_GAP_Y = 18;
//: 画布内边距。
const KM_PAD = 48;
//: 语义关系边超过此数进一步弱化 (spec §33)。
const KM_RELATION_EDGE_THRESHOLD = 40;
//: 缩放下限 / 上限。
const KM_MIN_SCALE = 0.12;
const KM_MAX_SCALE = 3;
//: fitView 允许的最小缩放 —— 再小节点就完全不可读了。
const KM_FIT_MIN_SCALE = 0.2;

const KM_VIEW = { x: 0, y: 0, k: 1, width: 1200, height: 600 };
let kmState = null;

/**
 * 带插值的翻译。
 *
 * ``t(key)`` 只接受一个 key —— 它不认第二个参数, 所以 ``t('km.sources', {n: 3})``
 * 只会把 key 原样吐出来, 页面上就会写着字面量 ``km.sources``。插值必须自己做,
 * 而且要**在翻译之后**做: 译文里的数字顺序未必和 key 一致。
 */
function kmT(key, vars) {
  let text = t(key);
  if (!vars) return text;
  Object.keys(vars).forEach(function (name) {
    text = text.split('{' + name + '}').join(String(vars[name]));
  });
  return text;
}

function kmEsc(value) {
  return esc(value == null ? '' : String(value));
}

function kmShort(text, max) {
  const flat = String(text || '').replace(/\s+/g, ' ').trim();
  return flat.length > max ? flat.slice(0, max - 1) + '…' : flat;
}

function kmReset() {
  kmState = {
    mode: 'map',
    courseId: '',
    // 课程显示名由 ``pageKnowledge()`` 在入口处解析一次 (``courseLabel()``),
    // 地图的标题行只负责渲染它 —— 与其余页面"入口解析、视图渲染"的分工一致,
    // 也满足"course_id 绝不当成可见文案"的静态守卫。
    courseLabel: '',
    data: null,
    list: [],
    loading: false,
    error: '',
    selected: '',
    query: '',
    topicId: '',
    materialId: '',
    sessionId: '',
    hasEvidence: '',
    // spec §11 / §33: 语义关系边**默认关闭**, 用户显式打开才显示。
    showRelations: false,
    collapsed: {},
    focus: '',
    fitted: false,
    // spec §25: 布局缓存 —— key = courseId + visibleNodeSet。
    layoutCache: null,
    centeredFor: '',
  };
  KM_VIEW.x = 0;
  KM_VIEW.y = 0;
  KM_VIEW.k = 1;
}

async function pageKnowledge() {
  markActiveNav('#/knowledge');
  const courseId = await requireCourse();
  kmReset();
  kmState.courseId = courseId;
  kmState.courseLabel = courseLabel(courseId);
  // spec §27: 加载态不能是空白页。地图数据只拉一次; 三种视图 + 所有筛选
  // 都在本地完成 —— 200 个节点的 JSON 也就几百 KB, 换来的是"点一下视图 /
  // 筛一下"零延迟。
  kmState.loading = true;
  setView(kmShell());
  try {
    const data = await api('/courses/' + encodeURIComponent(courseId) + '/knowledge-map');
    kmState.data = data;
    kmState.list = (await api('/knowledge', { query: { course_id: courseId } })).knowledge_points || [];
    kmState.loading = false;
    kmState.error = '';
    // 默认展开策略 (spec §7): 数据到手后按阈值算一次折叠集。
    kmState.collapsed = kmDefaultCollapsed(kmViewModel());
    kmRender();
  } catch (err) {
    // spec §28: API 失败不能让整页崩掉, 给一个可重试的错误态。
    kmState.loading = false;
    kmState.error = err;
    setView(kmShell());
    kmBind();
  }
}

// ==================================================================
// View Model (spec §5-6) —— 不把 API 返回原样塞给渲染器
// ==================================================================

const KM_ROOT_ID = 'km:root';

/**
 * 把 ``GET /courses/<id>/knowledge-map`` 投影成一张**明确的** View Model。
 *
 * 后端给的是扁平结构 (topics[] + knowledgeNodes[] + relations[])。这里把它
 * 组织成 Course → Topic → Canonical Knowledge → Sub Knowledge 的层级,
 * 并给每个 UI 节点补齐 spec §5 要求的字段。
 *
 * **不在这里做任何语义判断**: 不做 dedup / canonicalization / 合并 ——
 * 那些是后端的职责 (spec §4)。这里只投影 + 组织。
 */
function kmViewModel() {
  const data = kmState.data || {};
  const rawNodes = data.knowledgeNodes || [];
  const rawTopics = data.topics || [];
  const relations = data.relations || [];
  const stats = data.stats || {};

  const byId = {};
  const nodes = [];
  const edges = [];
  const rawIds = {};
  rawNodes.forEach(function (n) { rawIds[n.id] = n; });

  // ---- Level 0: Course Root ----
  const root = {
    id: KM_ROOT_ID,
    type: 'course',
    level: 0,
    parentId: '',
    topicId: '',
    title: kmState.courseLabel || (data.course || {}).name || kmState.courseId,
    shortTitle: '',
    childCount: 0,
    visibleChildCount: 0,
    materialCount: stats.materials || 0,
    evidenceCount: 0,
    hasChildren: true,
    expanded: true,
    canonicalId: '',
    metadata: data.course || null,
  };
  nodes.push(root);
  byId[root.id] = root;

  // ---- knowledge -> knowledge 父边 (Sub Knowledge, spec §6) ----
  //
  // 只认 ``parent-child`` 类型的关系, 且两端都必须是真实知识点。这是
  // 后端**已经存在**的信号 (词法包含 + 上位概念), 不是前端编出来的层级。
  const kParent = {};
  relations.forEach(function (rel) {
    if (rel.type !== 'parent-child') return;
    if (!rel.source || !rel.target || rel.source === rel.target) return;
    if (!rawIds[rel.source] || !rawIds[rel.target]) return;
    kParent[rel.target] = rel.source;
  });
  // 断环: 一个环会让 level 计算无限递归。断在最后一条边上, 不丢节点。
  Object.keys(kParent).forEach(function (start) {
    const seen = {};
    let cur = start;
    while (kParent[cur]) {
      if (seen[cur]) { delete kParent[cur]; break; }
      seen[cur] = true;
      cur = kParent[cur];
    }
  });

  // ---- Level 1: Topic ----
  // 只有**实际收下 >=1 个知识点**的主题才成节点 —— 否则会出现一个空主题,
  // 看起来像数据丢了。分类由后端 ``topicId`` 决定, 前端不重新分类。
  const topicOf = {};
  const topicCount = {};
  rawNodes.forEach(function (n) {
    if (kParent[n.id]) return; // 它是某个知识点的子知识点, 不直接挂主题
    const tid = n.topicId || '';
    if (!tid) return;
    const ui = 'topic:' + tid;
    topicOf[n.id] = ui;
    topicCount[ui] = (topicCount[ui] || 0) + 1;
  });
  const topicMeta = {};
  rawTopics.forEach(function (item) { topicMeta['topic:' + item.id] = item; });
  Object.keys(topicCount).sort().forEach(function (ui) {
    const meta = topicMeta[ui] || { id: ui.replace(/^topic:/, ''), title: ui, source: 'none' };
    const node = {
      id: ui,
      type: 'topic',
      level: 1,
      parentId: KM_ROOT_ID,
      topicId: meta.id,
      title: meta.title || meta.id,
      shortTitle: '',
      childCount: topicCount[ui],
      visibleChildCount: 0,
      materialCount: 0,
      evidenceCount: 0,
      hasChildren: true,
      expanded: true,
      canonicalId: '',
      metadata: meta,
    };
    nodes.push(node);
    byId[node.id] = node;
    edges.push({ source: KM_ROOT_ID, target: ui, kind: 'hierarchy' });
  });

  // ---- Level 2/3: Canonical Knowledge / Sub Knowledge ----
  rawNodes.forEach(function (n) {
    const parentId = kParent[n.id];
    const isSub = !!parentId;
    const node = {
      id: n.id,
      type: isSub ? 'subknowledge' : 'knowledge',
      level: isSub ? 3 : 2,
      parentId: isSub ? parentId : (topicOf[n.id] || KM_ROOT_ID),
      topicId: n.topicId || '',
      title: n.title || n.id,
      shortTitle: '',
      childCount: 0,
      visibleChildCount: 0,
      materialCount: n.materialCount || 0,
      evidenceCount: n.evidenceCount || 0,
      hasChildren: false,
      expanded: true,
      canonicalId: n.conceptId || n.id,
      metadata: n,
    };
    nodes.push(node);
    byId[node.id] = node;
  });

  // 挂边 + 统计 childCount。子知识点若其父不在图里 (父被合并掉), 回落到主题。
  nodes.forEach(function (node) {
    if (node.type === 'course' || node.type === 'topic') return;
    if (!byId[node.parentId] || node.parentId === node.id) {
      node.parentId = topicOf[node.id] || KM_ROOT_ID;
      node.level = 2;
      node.type = 'knowledge';
    }
  });
  const childCount = {};
  nodes.forEach(function (node) {
    if (node.type === 'course' || node.type === 'topic') return;
    childCount[node.parentId] = (childCount[node.parentId] || 0) + 1;
    edges.push({ source: node.parentId, target: node.id, kind: 'hierarchy' });
  });
  nodes.forEach(function (node) {
    const count = childCount[node.id] || 0;
    node.childCount = node.type === 'course' || node.type === 'topic' ? node.childCount : count;
    node.hasChildren = node.childCount > 0;
  });
  root.childCount = nodes.filter(function (n) { return n.type === 'topic'; }).length;

  // ---- 语义关系边 (spec §11) ----
  // 只保留**非层级**的关系 (related / extends / …)。parent-child 已经变成
  // 层级边, 不再重复画一遍。
  const semantic = [];
  relations.forEach(function (rel) {
    if (rel.type === 'parent-child') return;
    if (!byId[rel.source] || !byId[rel.target]) return;
    semantic.push({
      source: rel.source,
      target: rel.target,
      type: rel.type,
      confidence: rel.confidence,
      reason: rel.reason,
    });
  });

  // childrenOf 索引 —— 布局 / 可见性 / 折叠都从这里取, 避免重复遍历 edges。
  const childrenOf = {};
  edges.forEach(function (edge) {
    (childrenOf[edge.source] = childrenOf[edge.source] || []).push(edge.target);
  });

  return {
    course: data.course || {},
    rootId: KM_ROOT_ID,
    nodes: nodes,
    byId: byId,
    topics: nodes.filter(function (n) { return n.type === 'topic'; }),
    edges: edges,
    childrenOf: childrenOf,
    relations: semantic,
    materials: data.materials || [],
    evidence: data.evidence || [],
    stats: stats,
  };
}

/** spec §7: 按阈值算默认折叠集。<=8 展开, >8 折叠。 */
function kmDefaultCollapsed(vm) {
  const collapsed = {};
  vm.nodes.forEach(function (node) {
    if (node.type !== 'topic') return;
    if (node.childCount > KM_EXPAND_MAX_CHILDREN) collapsed[node.id] = true;
  });
  return collapsed;
}

// ---- 筛选 ---------------------------------------------------------------

/** ``{material_id: session_id}`` —— 课程节次过滤与材料地图共用。 */
function kmSessionOfMaterial() {
  const map = {};
  ((kmState.data || {}).materials || []).forEach(function (material) {
    if (material.session) map[material.id] = material.session;
  });
  return map;
}

function kmSessions() {
  const seen = {};
  ((kmState.data || {}).materials || []).forEach((item) => {
    if (item.session) seen[item.session] = item.sessionTitle || item.session;
  });
  return Object.keys(seen).sort().map((id) => ({ id: id, label: seen[id] || id }));
}

function kmMatches(node, needle) {
  const meta = node.metadata || {};
  const hay = [node.title, (meta.aliases || []).join(' '), meta.summary || '']
    .join(' ').toLowerCase();
  return hay.indexOf(needle) >= 0;
}

/**
 * 一个知识点是否通过当前的 topic / material / session / evidence 筛选。
 *
 * **搜索不在这里** —— spec §16 明确要求搜索是"导航"而不是"过滤":
 * 不匹配的节点要**保留并降权**, 而不是删除。所以 ``kmState.query`` 只影响
 * 高亮 / 降权 / 自动展开 / 居中, 不参与可见性判定。
 */
function kmPassesFilters(node) {
  const meta = node.metadata || {};
  if (kmState.topicId && node.topicId !== kmState.topicId) return false;
  if (kmState.materialId && (meta.materialIds || []).indexOf(kmState.materialId) < 0) return false;
  if (kmState.sessionId) {
    const sessionOf = kmSessionOfMaterial();
    const fromSession = (meta.materialIds || []).some(function (id) {
      return sessionOf[id] === kmState.sessionId;
    });
    if (!fromSession) return false;
  }
  if (kmState.hasEvidence === 'yes' && !node.evidenceCount) return false;
  if (kmState.hasEvidence === 'no' && node.evidenceCount) return false;
  return true;
}

// ---- 可见性投影 (spec §8: 不删数据, 只投影) ------------------------------

/**
 * 被折叠节点 -> 应隐藏的后代。
 *
 * 必须递归到叶, 不能只看一层 —— 三层树里只藏一层的话, 用户会以为节点
 * 凭空消失了。搜索时, 命中项的祖先**临时展开**, 但不改动用户的折叠状态。
 */
function kmHiddenSet(vm) {
  const collapsed = kmState.collapsed || {};
  const forced = kmState.query.trim() ? kmAncestorsOfMatches(vm) : {};
  const hidden = {};
  const queue = Object.keys(collapsed).filter(function (id) { return collapsed[id]; });
  while (queue.length) {
    const current = queue.shift();
    if (forced[current]) continue;
    (vm.childrenOf[current] || []).forEach(function (child) {
      if (hidden[child]) return;
      hidden[child] = true;
      queue.push(child);
    });
  }
  return hidden;
}

/** 命中搜索的节点 + 它们的全部祖先 (搜索必须自动展开路径 —— spec §16)。 */
function kmAncestorsOfMatches(vm) {
  const needle = kmState.query.trim().toLowerCase();
  const keep = {};
  if (!needle) return keep;
  vm.nodes.forEach(function (node) {
    if (node.type === 'course' || node.type === 'topic') return;
    if (!kmMatches(node, needle)) return;
    keep[node.id] = true;
    let parent = vm.byId[node.parentId];
    let guard = 0;
    while (parent && guard < 12) {
      keep[parent.id] = true;
      parent = vm.byId[parent.parentId];
      guard += 1;
    }
  });
  return keep;
}

/** 当前应该渲染的节点集合。 */
function kmVisibleSet(vm) {
  const hidden = kmHiddenSet(vm);
  const out = {};
  const ordered = vm.nodes.slice().sort(function (a, b) { return a.level - b.level; });
  // 第 1 步: 哪些知识点通过**筛选** (与折叠无关 —— 折叠是渲染投影, 不是筛选)。
  // 这一步决定了"一个 Topic 还算不算存在": 它的知识点被筛掉才是空主题;
  // 被折叠只是暂时不画, 主题本身仍要在图上, 并显示隐藏数量。
  const filterOk = {};
  ordered.forEach(function (node) {
    if (node.type === 'course' || node.type === 'topic') return;
    if (!kmPassesFilters(node)) return;
    if (node.level === 3 && !filterOk[node.parentId]) return;
    filterOk[node.id] = true;
  });
  // 第 2 步: 课程根永远在; 主题只要有 >=1 个通过筛选的知识点就在。
  out[vm.rootId] = true;
  vm.nodes.forEach(function (node) {
    if (node.type !== 'topic') return;
    if ((vm.childrenOf[node.id] || []).some(function (id) { return filterOk[id]; })) {
      out[node.id] = true;
    }
  });
  // 第 3 步: 知识点可见 = 通过筛选 且 未被折叠隐藏 且 父知识点可见。
  ordered.forEach(function (node) {
    if (node.type === 'course' || node.type === 'topic') return;
    if (!filterOk[node.id] || hidden[node.id]) return;
    if (node.level === 3 && !out[node.parentId]) return;
    out[node.id] = true;
  });
  // 记录每个父节点的可见子节点数 (spec §5 visibleChildCount)。
  vm.nodes.forEach(function (node) { node.visibleChildCount = 0; });
  Object.keys(out).forEach(function (id) {
    const node = vm.byId[id];
    if (node && vm.byId[node.parentId]) vm.byId[node.parentId].visibleChildCount += 1;
  });
  return out;
}

// ---- 布局 (spec §9: hierarchical, 不是固定列) ----------------------------

/** 节点宽度随标题长度变化, 夹在 [KM_NODE_MIN_W, KM_NODE_MAX_W] (spec §13)。 */
function kmNodeWidth(node) {
  if (node.type === 'course') return 260;
  const length = String(node.title || '').length;
  const width = 44 + Math.min(length, 32) * 7.4;
  return Math.max(KM_NODE_MIN_W, Math.min(KM_NODE_MAX_W, Math.round(width)));
}

/** 一行最多能放几个字符 (给标题折行用)。 */
function kmMaxChars(node) {
  return Math.max(8, Math.floor((kmNodeWidth(node) - 24) / 7.2));
}

/** 标题折成最多 2 行, 超出用省略号 (spec §13)。 */
function kmWrapTitle(text, max) {
  const flat = String(text || '').replace(/\s+/g, ' ').trim();
  if (!flat) return [''];
  if (flat.length <= max) return [flat];
  let cut = flat.lastIndexOf(' ', max);
  if (cut < Math.floor(max * 0.5)) cut = max;
  const first = flat.slice(0, cut).trim();
  let rest = flat.slice(cut).trim();
  if (rest.length > max) rest = rest.slice(0, max - 1).trim() + '…';
  return [first, rest];
}

/** 同层子节点的稳定排序 —— 主题按知识点数降序, 知识点按标题升序。 */
function kmSortSiblings(vm, a, b) {
  const left = vm.byId[a];
  const right = vm.byId[b];
  if (!left || !right) return 0;
  if (left.type === 'topic' && right.type === 'topic') {
    if (right.childCount !== left.childCount) return right.childCount - left.childCount;
  }
  return left.title < right.title ? -1 : (left.title > right.title ? 1 : 0);
}

/**
 * 分层树布局 (LR)。经典 tidy tree:
 *   - x 由层深决定 (每层一个列, 列宽 = 该层最大节点宽 + 层间距);
 *   - y 由叶子的行序决定, 父节点垂直居中于其子节点之间。
 *
 * 这样**不会**出现"多个主题强行排成数列 + 大量连线穿越": 同一父节点的
 * 子树占据一段连续的 y 区间, 兄弟之间天然不交叉。
 */
function kmLayout(vm, visible) {
  const childrenOf = {};
  vm.edges.forEach(function (edge) {
    if (edge.kind !== 'hierarchy') return;
    if (!visible[edge.source] || !visible[edge.target]) return;
    (childrenOf[edge.source] = childrenOf[edge.source] || []).push(edge.target);
  });
  Object.keys(childrenOf).forEach(function (id) {
    childrenOf[id].sort(function (a, b) { return kmSortSiblings(vm, a, b); });
  });

  const widthOf = {};
  const levelPitch = {};
  let acc = KM_PAD;
  for (let level = 0; level <= 3; level += 1) {
    let maxWidth = 0;
    vm.nodes.forEach(function (node) {
      if (!visible[node.id] || node.level !== level) return;
      const width = kmNodeWidth(node);
      widthOf[node.id] = width;
      if (width > maxWidth) maxWidth = width;
    });
    if (maxWidth > 0) {
      levelPitch[level] = acc;
      acc += maxWidth + KM_LEVEL_GAP_X;
    }
  }

  const positions = {};
  let row = KM_PAD;
  const place = function (id, level, guard) {
    const x = levelPitch[level] != null ? levelPitch[level] : KM_PAD;
    const width = widthOf[id] || KM_NODE_MIN_W;
    const kids = (childrenOf[id] || []).filter(function (child) { return !guard[child]; });
    if (!kids.length) {
      positions[id] = { x: x, y: row, w: width, level: level };
      row += KM_NODE_H + KM_ROW_GAP_Y;
      return positions[id];
    }
    const placed = kids.map(function (child) {
      const next = Object.assign({}, guard);
      next[id] = true;
      return place(child, level + 1, next);
    });
    const y = (placed[0].y + placed[placed.length - 1].y) / 2;
    positions[id] = { x: x, y: y, w: width, level: level };
    return positions[id];
  };
  if (visible[vm.rootId]) place(vm.rootId, 0, {});

  const height = row + KM_PAD;
  const width = acc - KM_LEVEL_GAP_X + KM_PAD;
  return {
    positions: positions,
    childrenOf: childrenOf,
    width: Math.max(width, 480),
    height: Math.max(height, 240),
  };
}

/** spec §25: 布局缓存 —— 只有可见节点集合变化才重算, 选中/详情/悬停不重算。 */
function kmLayoutCached(vm, visible) {
  const ids = Object.keys(visible).sort().join(',');
  const key = kmState.courseId + '|' + ids;
  if (kmState.layoutCache && kmState.layoutCache.key === key) return kmState.layoutCache.layout;
  const layout = kmLayout(vm, visible);
  kmState.layoutCache = { key: key, layout: layout };
  return layout;
}

// ---- 边的路径 -----------------------------------------------------------

/** 层级边: 正交折线 (smoothstep 的直角版), 从父右中到子左中 (spec §10)。 */
function kmHierarchyPath(from, to) {
  const x1 = from.x + from.w;
  const y1 = from.y + KM_NODE_H / 2;
  const x2 = to.x;
  const y2 = to.y + KM_NODE_H / 2;
  const mid = (x1 + x2) / 2;
  return 'M' + x1 + ' ' + y1 + ' H' + mid + ' V' + y2 + ' H' + x2;
}

// ---- 搜索高亮 / 聚焦 -----------------------------------------------------

function kmHighlightIds(vm) {
  const needle = kmState.query.trim().toLowerCase();
  if (!needle) return {};
  const out = {};
  vm.nodes.forEach(function (node) {
    if (node.type === 'course' || node.type === 'topic') return;
    if (kmMatches(node, needle)) out[node.id] = true;
  });
  return out;
}

/**
 * 聚焦集: 只留当前节点 + 全部祖先 + 全部后代 + 直接关联邻居 (spec §17)。
 * 返回 ``null`` 表示没有聚焦 (全部可见)。
 */
function kmFocusSet(vm) {
  if (!kmState.focus || !vm.byId[kmState.focus]) return null;
  const keep = {};
  keep[kmState.focus] = true;
  let parent = vm.byId[kmState.focus].parentId;
  let guard = 0;
  while (parent && guard < 12) {
    keep[parent] = true;
    parent = (vm.byId[parent] || {}).parentId;
    guard += 1;
  }
  const queue = [kmState.focus];
  while (queue.length) {
    const current = queue.shift();
    (vm.childrenOf[current] || []).forEach(function (child) {
      if (keep[child]) return;
      keep[child] = true;
      queue.push(child);
    });
  }
  vm.relations.forEach(function (rel) {
    if (rel.source === kmState.focus) keep[rel.target] = true;
    if (rel.target === kmState.focus) keep[rel.source] = true;
  });
  return keep;
}

// ---- SVG 绘制 -----------------------------------------------------------

function kmNodeSvg(vm, node, pos, highlight, focusSet) {
  if (!pos) return '';
  const width = pos.w;
  const maxChars = kmMaxChars(node);
  const lines = kmWrapTitle(node.title, maxChars);
  const isFocus = kmState.focus === node.id;
  const isSelected = kmState.selected === node.id;
  const dim = focusSet && !focusSet[node.id];
  const searching = !!kmState.query.trim();
  const hit = !!highlight[node.id];
  const opacity = dim ? 0.12 : (searching && !hit && node.type !== 'course' && node.type !== 'topic' ? 0.35 : 1);

  let rectFill = 'var(--surface)';
  let rectStroke = 'var(--border-strong)';
  let strokeWidth = 1;
  if (node.type === 'course') { rectFill = 'var(--surface-2)'; rectStroke = 'var(--accent)'; strokeWidth = 2; }
  else if (node.type === 'topic') { rectFill = 'var(--surface-2)'; rectStroke = 'var(--border-strong)'; }
  if (hit) rectFill = 'var(--accent-soft)';
  if (isFocus || isSelected) { rectStroke = 'var(--accent)'; strokeWidth = 2.5; }

  const parts = [];
  parts.push('<g class="km-node km-node-' + node.type + (isFocus ? ' is-focus' : '') +
    '" data-km-node="' + kmEsc(node.id) + '" transform="translate(' + pos.x + ',' + pos.y +
    ')" opacity="' + opacity + '" tabindex="0" role="button" aria-label="' +
    kmEsc(node.title) + '">');
  parts.push('<rect width="' + width + '" height="' + KM_NODE_H + '" rx="' +
    (node.type === 'course' ? 12 : 8) + '" fill="' + rectFill + '" stroke="' + rectStroke +
    '" stroke-width="' + strokeWidth + '"/>');

  if (node.type === 'course') {
    parts.push('<text x="14" y="19" class="km-node-eyebrow">' + esc(t('km.courseEyebrow')) + '</text>');
    parts.push('<text x="14" y="39" class="km-node-title">' + kmEsc(kmShort(lines[0], maxChars)) + '</text>');
    parts.push('<text x="14" y="56" class="km-node-meta">' + esc(kmT('km.courseMeta', {
      topics: node.childCount, concepts: (vm.stats.nodes || 0),
    })) + '</text>');
  } else if (node.type === 'topic') {
    parts.push('<text x="14" y="19" class="km-node-eyebrow">' + esc(t('km.topicEyebrow')) + '</text>');
    parts.push('<text x="14" y="39" class="km-node-title">' + kmEsc(kmShort(lines[0], maxChars)) + '</text>');
    parts.push('<text x="14" y="56" class="km-node-meta">' + esc(kmT('km.topicCount', { n: node.childCount })) + '</text>');
  } else {
    parts.push('<circle cx="15" cy="17" r="4" fill="var(--accent)"/>');
    parts.push('<text x="27" y="21" class="km-node-title"><tspan x="27">' + kmEsc(lines[0]) + '</tspan>' +
      (lines[1] ? '<tspan x="27" dy="15">' + kmEsc(lines[1]) + '</tspan>' : '') + '</text>');
    parts.push('<text x="14" y="' + (KM_NODE_H - 9) + '" class="km-node-meta">' + esc(kmT('km.knowledgeMeta', {
      materials: node.materialCount, evidence: node.evidenceCount,
    })) + '</text>');
  }
  parts.push('</g>');

  // 折叠 / 展开按钮 (spec §8)。数字 = 隐藏的 child 数。
  if (node.hasChildren) {
    const collapsed = !!kmState.collapsed[node.id];
    const cx = pos.x + width + 14;
    const cy = pos.y + KM_NODE_H / 2;
    parts.push('<g class="km-toggle" data-km-collapse="' + kmEsc(node.id) + '" transform="translate(' +
      cx + ',' + cy + ')">' +
      '<circle r="10" fill="var(--surface)" stroke="var(--border-strong)"/>' +
      '<text x="0" y="4" text-anchor="middle" class="km-toggle-sign">' +
      (collapsed ? '+' : '−') + '</text>' +
      '<title>' + esc(collapsed ? t('km.expandNode') : t('km.collapseNode')) + '</title></g>');
    if (collapsed) {
      // spec §8: 折叠后节点旁显示被隐藏的 child 数。
      parts.push('<text x="' + (cx + 16) + '" y="' + (cy + 4) + '" class="km-node-count">' +
        esc(String(node.childCount)) + '</text>');
    }
  }
  return parts.join('');
}

function kmRenderMap() {
  const vm = kmViewModel();
  const visible = kmVisibleSet(vm);
  const layout = kmLayoutCached(vm, visible);
  const highlight = kmHighlightIds(vm);
  const focusSet = kmFocusSet(vm);

  const showNode = function (id) { return visible[id] && (!focusSet || focusSet[id]); };

  // 层级边 (实线, 低调) —— 永远开。
  const hierarchy = vm.edges.filter(function (edge) {
    if (edge.kind !== 'hierarchy') return false;
    if (!showNode(edge.source) || !showNode(edge.target)) return false;
    return layout.positions[edge.source] && layout.positions[edge.target];
  }).map(function (edge) {
    return '<path d="' +
      kmHierarchyPath(layout.positions[edge.source], layout.positions[edge.target]) +
      '" class="km-edge km-edge-hierarchy"/>';
  }).join('');

  // 语义关系边 (虚线, 默认关闭 —— spec §11 / §33)。
  let relations = '';
  if (kmState.showRelations) {
    const rels = vm.relations.filter(function (rel) {
      return showNode(rel.source) && showNode(rel.target) &&
        layout.positions[rel.source] && layout.positions[rel.target];
    });
    const weak = rels.length > KM_RELATION_EDGE_THRESHOLD;
    relations = rels.map(function (rel) {
      const from = layout.positions[rel.source];
      const to = layout.positions[rel.target];
      return '<line class="km-edge km-edge-relation' + (weak ? ' is-weak' : '') +
        '" x1="' + (from.x + from.w / 2) + '" y1="' + (from.y + KM_NODE_H / 2) +
        '" x2="' + (to.x + to.w / 2) + '" y2="' + (to.y + KM_NODE_H / 2) + '"/>';
    }).join('');
  }

  const boxes = vm.nodes.filter(function (node) { return showNode(node.id); })
    .map(function (node) { return kmNodeSvg(vm, node, layout.positions[node.id], highlight, focusSet); })
    .join('');

  KM_VIEW.width = layout.width;
  KM_VIEW.height = layout.height;

  // 空态判据: 没有任何知识点**通过筛选** —— 折叠不是"没有", 折叠时只是暂时不画。
  const passing = vm.nodes.filter(function (node) {
    return (node.type === 'knowledge' || node.type === 'subknowledge') && kmPassesFilters(node);
  }).length;
  return '<div class="km-stage" id="km-stage">' +
    '<svg id="km-svg" viewBox="0 0 1200 600" width="100%" height="100%" ' +
    'preserveAspectRatio="xMidYMid meet" role="group" aria-label="' + esc(t('km.title')) + '">' +
    '<g id="km-viewport" transform="translate(' + KM_VIEW.x + ',' + KM_VIEW.y + ') scale(' + KM_VIEW.k + ')">' +
    relations + hierarchy + boxes +
    '</g></svg>' +
    '<div class="km-legend">' +
    '<span class="km-legend-item"><span class="km-legend-line km-legend-hierarchy"></span>' +
    esc(t('km.legendHierarchy')) + '</span>' +
    (kmState.showRelations
      ? '<span class="km-legend-item"><span class="km-legend-line km-legend-relation"></span>' +
        esc(t('km.legendRelation')) + '</span>'
      : '') +
    '</div>' +
    '<div class="km-controls">' +
    '<button type="button" data-km="zoom-in" aria-label="' + esc(t('km.zoomIn')) + '">+</button>' +
    '<button type="button" data-km="zoom-out" aria-label="' + esc(t('km.zoomOut')) + '">−</button>' +
    '<button type="button" data-km="fit">' + esc(t('km.fitView')) + '</button>' +
    '<button type="button" data-km="reset">' + esc(t('km.reset100')) + '</button>' +
    '</div>' +
    (passing ? '' : kmEmptyState()) +
    '</div>';
}

/**
 * 空态 (spec §26)。两种"空"是不同的:
 *   * 课程本身没有知识点 -> 引导上传材料 + 跑 AI 分析;
 *   * 有知识点但当前筛选/搜索把它们全滤掉了 -> 说"当前筛选下没有"。
 * 后者说成"没有知识地图"会误导用户以为数据丢了。
 */
function kmEmptyState() {
  const total = ((kmState.data || {}).knowledgeNodes || []).length;
  if (total) {
    return '<div class="km-empty"><p class="muted">' + esc(t('km.empty')) + '</p></div>';
  }
  return '<div class="km-empty"><div><p><strong>' + esc(t('km.noMap')) + '</strong></p>' +
    '<p class="small muted">' + esc(t('km.noMapHint')) + '</p></div></div>';
}

// ---- 材料地图 -----------------------------------------------------------

function kmRenderMaterialMap() {
  const data = kmState.data || {};
  const materials = (data.materials || []).filter(function (item) {
    if (kmState.materialId && item.id !== kmState.materialId) return false;
    return true;
  });
  if (!materials.length) {
    return '<div class="card">' + emptyState(t('km.noMaterials')) + '</div>';
  }
  const rows = materials.map((material) => {
    const nodeIds = material.knowledgeIds || [];
    const chips = nodeIds.slice(0, 24).map((id) => {
      const node = (data.knowledgeNodes || []).filter((item) => item.id === id)[0];
      const label = node ? node.title : id;
      return '<button type="button" class="km-chip" data-km-select="' + kmEsc(id) + '">' +
        kmEsc(kmShort(label, 30)) + '</button>';
    }).join('');
    const more = nodeIds.length > 24
      ? ' <span class="tiny muted">' + esc(kmT('km.more', { n: nodeIds.length - 24 })) + '</span>'
      : '';
    const session = material.sessionKnown
      ? (material.sessionTitle || material.session)
      : t('km.sessionUnknown');
    return '<tr data-km-material="' + kmEsc(material.id) + '">' +
      '<td><strong>' + kmEsc(material.title) + '</strong><br>' +
      '<span class="tiny muted">' + kmEsc(material.date || t('km.dateUnknown')) +
      ' · ' + kmEsc(session) + '</span></td>' +
      '<td class="nowrap">' + pill(material.status || 'unknown') +
      ' <span class="tiny muted">' + esc(kmT('km.evidenceN', { n: material.evidenceCount })) + '</span></td>' +
      '<td><span class="tiny muted">' + esc(kmT('km.knowledgeN', { n: material.knowledgeCount })) + '</span><br>' +
      (chips || '<span class="tiny muted">' + esc(t('km.noContribution')) + '</span>') + more + '</td>' +
      '</tr>';
  }).join('');
  return '<div class="card"><table class="data compact"><caption class="caption">' +
    esc(t('km.materialMap')) + '</caption>' +
    '<thead><tr><th scope="col">' + esc(t('km.material')) + '</th>' +
    '<th scope="col">' + esc(t('km.analysis')) + '</th>' +
    '<th scope="col">' + esc(t('km.contributed')) + '</th></tr></thead><tbody>' +
    rows + '</tbody></table></div>';
}

// ---- 列表 (保留原样, 作为调试视图 —— spec §30) ---------------------------

function kmRenderList() {
  const courseId = kmState.courseId;
  const points = kmState.list || [];
  return '<div class="actions"><button data-action="kp-backfill" data-course="' +
    kmEsc(courseId) + '">' + esc(t('kpZh.backfill')) + '</button>' +
    '<span class="tiny muted">' + esc(t('kpZh.backfillHint')) + '</span></div>' +
    '<div class="card">' +
    (points.length
      ? '<table class="data compact fixed">' + tableCaption(t('知识点')) + '<colgroup><col style="width:36%"><col style="width:200px">' +
        '<col style="width:60px"><col></colgroup>' +
        '<thead><tr><th scope="col">' + t('标题') + '</th><th scope="col">' + t('状态') + '</th>' +
        '<th scope="col" class="num">' + t('证据') + '</th><th scope="col">' + t('原文术语') + '</th></tr></thead><tbody>' +
        points.map((kp) => (
            '<tr><td class="kp-title"><a href="#/courses/' + encodeURIComponent(courseId) +
            '/knowledge/' + encodeURIComponent(kp.knowledge_id) + '">' +
            esc(kp.title || kp.knowledge_id) + '</a>' + knowledgeModeBadge(kp) +
            ((kp.evidence_refs || []).length
              ? ' <button class="small" data-action="create-flashcard" data-course="' +
                esc(courseId) + '" data-knowledge="' + esc(kp.knowledge_id) + '">' +
                esc(t('fc.create')) + '</button>'
              : ' <span class="tiny muted">' + esc(t('fc.noEvidence')) + '</span>') +
            '<br><span class="tiny muted mono break-all">' + esc(kp.knowledge_id) + '</span></td>' +
            '<td class="nowrap">' + pill(kp.validation_status) + ' ' + pill(kp.review_status) + '</td>' +
            '<td class="num">' + esc((kp.evidence_refs || []).length) + '</td>' +
            '<td class="small terms-cell">' + renderTerms(kp.original_terms) + '</td></tr>'
          )).join('') + '</tbody></table>'
      : emptyState(t('该课程还没有知识点。上传材料并处理后会生成。'))) +
    '</div>';
}

// ---- 详情面板 (spec §18: Canvas = 导航, Detail = 阅读) -------------------

function kmRenderDetail() {
  const data = kmState.data || {};
  const id = kmState.selected;
  if (!id) {
    return '<aside class="km-detail"><p class="small muted">' + esc(t('km.selectHint')) + '</p></aside>';
  }
  const node = (data.knowledgeNodes || []).filter((item) => item.id === id)[0];
  if (!node) {
    return '<aside class="km-detail"><p class="small muted">' + esc(t('km.selectHint')) + '</p></aside>';
  }
  const materials = {};
  (data.materials || []).forEach((item) => { materials[item.id] = item; });
  const evidence = (data.evidence || []).filter(function (row) {
    return (row.knowledgeIds || []).indexOf(id) >= 0;
  });

  const relations = (data.relations || []).map(function (rel) {
    const otherId = rel.source === id ? rel.target : (rel.target === id ? rel.source : '');
    if (!otherId) return '';
    const other = (data.knowledgeNodes || []).filter((item) => item.id === otherId)[0];
    if (!other) return '';
    const arrow = rel.source === id ? '→' : '←';
    return '<li><button type="button" class="km-link" data-km-select="' + kmEsc(otherId) + '">' +
      esc(arrow + ' ' + kmShort(other.title, 40)) + '</button> ' +
      '<span class="tiny muted">' + esc(rel.type) + '</span></li>';
  }).join('');

  const sourceList = (node.materialIds || []).map(function (materialId) {
    const material = materials[materialId];
    if (!material) return '';
    // 材料可点击 —— 点材料跳到材料详情 (spec §18/§19)。
    // 指向材料页这条**真实存在**的路由, 由 tests/test_web_ui_invariants.py
    // 的可达性守卫盯着。
    return '<li><a href="#/materials">' +
      kmEsc(material.title) + '</a> <span class="tiny muted">' +
      kmEsc(material.sessionKnown ? (material.sessionTitle || material.session) : t('km.sessionUnknown')) +
      '</span></li>';
  }).join('');

  const evidenceList = evidence.map(function (row) {
    const material = materials[row.materialId];
    const where = [row.location, row.page ? kmT('km.page', { n: row.page }) : '',
      row.paragraph ? kmT('km.paragraph', { n: row.paragraph }) : ''].filter(Boolean).join(' · ');
    return '<li><span class="mono tiny">' + kmEsc(row.id) + '</span> ' +
      kmEsc(material ? material.title : (row.materialId || t('km.brokenTrace'))) +
      (where ? ' <span class="tiny muted">' + kmEsc(where) + '</span>' : '') + '</li>';
  }).join('');

  const mergeNote = node.isMerged
    ? '<p class="km-merge-note">' + esc(kmT('km.mergedFrom', { n: (node.aliases || []).length })) +
      ((node.aliases || []).length
        ? '<br><span class="tiny muted">' + kmEsc((node.aliases || []).join(' · ')) + '</span>'
        : '') + '</p>'
    : '';

  const proposals = (data.mergeProposals || []).filter(function (item) {
    return item.source_id === id || item.target_id === id;
  });
  const proposalList = proposals.length
    ? '<h4>' + esc(t('km.pendingMerges')) + '</h4><ul class="km-list">' + proposals.map(function (item) {
        const otherId = item.source_id === id ? item.target_id : item.source_id;
        const otherTitle = item.source_id === id ? item.target_title : item.source_title;
        return '<li>' + esc(t('km.suggestMerge')) + ' <b>' + kmEsc(otherTitle) + '</b> ' +
          '<span class="tiny muted">' + esc(item.band) + ' ' + item.score.toFixed(2) + '</span>' +
          '<br><span class="tiny muted">' + kmEsc((item.reasons || []).join('; ')) + '</span>' +
          '<br><button type="button" class="km-link" data-km-select="' + kmEsc(otherId) + '">' +
          esc(t('km.goToOther')) + '</button></li>';
      }).join('') + '</ul>'
    : '';

  return '<aside class="km-detail">' +
    '<h2>' + kmEsc(node.title) + '</h2>' +
    mergeNote +
    '<p class="tiny muted mono break-all">' + kmEsc(node.id) + '</p>' +
    '<div class="km-actions">' +
    '<button type="button" class="small" data-km-focus="' + kmEsc(node.id) + '">' +
    esc(t('km.focusThis')) + '</button>' +
    '<button type="button" class="small" data-km-unfocus>' + esc(t('km.clearFocus')) + '</button>' +
    '<a class="small" href="#/courses/' + encodeURIComponent(kmState.courseId) +
    '/knowledge/' + encodeURIComponent(node.id) + '">' + esc(t('km.openDetail')) + '</a>' +
    '</div>' +
    '<p class="km-badges">' + pill(node.validationStatus) + ' ' + pill(node.reviewStatus) +
    ' <span class="pill pill-muted tiny">' + esc(kmT('km.sources', { n: node.materialCount })) + '</span>' +
    ' <span class="pill pill-muted tiny">' + esc(kmT('km.evidenceN', { n: node.evidenceCount })) + '</span></p>' +
    '<h4>' + esc(t('km.aiSummary')) + '</h4>' +
    '<p class="small km-summary">' + (node.summary ? kmEsc(node.summary) : '<span class="muted">' +
      esc(t('km.noSummary')) + '</span>') + '</p>' +
    ((node.aliases || []).length
      ? '<h4>' + esc(t('km.coreConcepts')) + '</h4><ul class="km-list">' +
        node.aliases.map((alias) => '<li>' + kmEsc(alias) + '</li>').join('') + '</ul>'
      : '') +
    (relations ? '<h4>' + esc(t('km.relations')) + '</h4><ul class="km-list">' + relations + '</ul>' : '') +
    proposalList +
    '<h4>' + esc(t('km.materialsTitle')) + '</h4>' +
    '<ul class="km-list">' + (sourceList || '<li class="muted small">' + esc(t('km.noMaterials')) + '</li>') + '</ul>' +
    '<h4>' + esc(t('km.evidenceTitle')) + '</h4>' +
    '<ul class="km-list">' + (evidenceList || '<li class="muted small">' + esc(t('km.noEvidence')) + '</li>') + '</ul>' +
    '</aside>';
}

// ---- 外壳 (spec §21: 工具栏不要堆按钮) -----------------------------------

function kmShell() {
  // spec §27 加载态 / §28 错误态 —— 都不能是空白页。
  if (kmState.loading) {
    return '<div class="page-head"><h1>' + esc(t('km.title')) + '</h1></div>' +
      '<div class="card"><p class="muted">' + esc(t('km.loadingMap')) + '</p></div>';
  }
  if (kmState.error) {
    return '<div class="page-head"><h1>' + esc(t('km.title')) + '</h1></div>' +
      '<div class="card"><p class="pill pill-bad">' + esc(t('km.loadFailed')) + '</p>' +
      '<p class="small muted">' + kmEsc(kmState.error.code + ' ' + kmState.error.message) + '</p>' +
      '<div class="actions"><button type="button" data-km="retry">' + esc(t('km.retry')) + '</button></div></div>';
  }

  const data = kmState.data || {};
  const stats = data.stats || {};
  const topics = data.topics || [];
  const materials = data.materials || [];
  const modes = [
    { id: 'list', label: t('km.listView') },
    { id: 'map', label: t('km.knowledgeMap') },
    { id: 'materials', label: t('km.materialMap') },
  ];
  const tabs = modes.map((mode) =>
    '<button type="button" class="km-tab' + (kmState.mode === mode.id ? ' is-active' : '') +
    '" data-km-mode="' + mode.id + '" aria-pressed="' + (kmState.mode === mode.id) + '">' +
    esc(mode.label) + '</button>'
  ).join('');

  const topicOptions = ['<option value="">' + esc(t('km.allTopics')) + '</option>'].concat(
    topics.map((topic) => '<option value="' + kmEsc(topic.id) + '"' +
      (kmState.topicId === topic.id ? ' selected' : '') + '>' +
      kmEsc(kmShort(topic.title, 40)) + ' (' + topic.knowledgeCount + ')</option>')
  ).join('');
  const materialOptions = ['<option value="">' + esc(t('km.allMaterials')) + '</option>'].concat(
    materials.map((material) => '<option value="' + kmEsc(material.id) + '"' +
      (kmState.materialId === material.id ? ' selected' : '') + '>' +
      kmEsc(kmShort(material.title, 40)) + '</option>')
  ).join('');
  const sessionOptions = ['<option value="">' + esc(t('km.allSessions')) + '</option>'].concat(
    kmSessions().map((session) => '<option value="' + kmEsc(session.id) + '"' +
      (kmState.sessionId === session.id ? ' selected' : '') + '>' + kmEsc(session.label) + '</option>')
  ).join('');

  const isMap = kmState.mode === 'map';
  const body = isMap
    ? '<div class="km-body"><div class="km-main">' + kmRenderMap() + '</div>' + kmRenderDetail() + '</div>'
    : (kmState.mode === 'materials' ? kmRenderMaterialMap() : kmRenderList());

  // spec §15: 合并建议不再是污染主地图的大 banner, 而是一个很小的状态提示。
  const mergeChip = (stats.pendingMerges || 0)
    ? '<span class="km-status-chip" title="' + esc(t('km.pendingBanner', { n: stats.pendingMerges })) +
      '">' + esc(kmT('km.mergeChip', { n: stats.pendingMerges })) + '</span>'
    : '';

  return '<div class="page-head"><h1>' + esc(t('km.title')) + '</h1>' +
    '<p class="subtitle">' + t('课程 ') + '<strong>' +
    esc(kmState.courseLabel || courseLabel(kmState.courseId)) + '</strong>' +
    ' · ' + esc(kmT('km.summaryLine', {
      nodes: stats.nodes || 0,
      topics: stats.topics || 0,
      relations: stats.relations || 0,
      raw: stats.knowledgePoints || 0,
      merged: stats.mergedNodes || 0,
    })) + ' · <a href="#/flashcards">' + esc(t('fc.title')) + '</a></p></div>' +
    // TASK-81 §A2: 补翻历史知识点的唯一入口, 知识点页改成知识地图后不能丢。
    '<div class="actions"><button data-action="kp-backfill" data-course="' +
    kmEsc(kmState.courseId) + '">' + esc(t('kpZh.backfill')) + '</button>' +
    '<span class="tiny muted">' + esc(t('kpZh.backfillHint')) + '</span></div>' +
    '<div class="km-toolbar">' + tabs + mergeChip +
    '<input id="km-search" type="search" placeholder="' + esc(t('km.searchPlaceholder')) +
    '" value="' + kmEsc(kmState.query) + '" aria-label="' + esc(t('km.searchPlaceholder')) + '">' +
    (isMap
      ? '<select id="km-topic" aria-label="' + esc(t('km.allTopics')) + '">' + topicOptions + '</select>' +
        '<select id="km-evidence" aria-label="' + esc(t('km.onlyEvidence')) + '">' +
        '<option value="">' + esc(t('km.allEvidence')) + '</option>' +
        '<option value="yes"' + (kmState.hasEvidence === 'yes' ? ' selected' : '') + '>' + esc(t('km.onlyEvidence')) + '</option>' +
        '<option value="no"' + (kmState.hasEvidence === 'no' ? ' selected' : '') + '>' + esc(t('km.onlyNoEvidence')) + '</option>' +
        '</select>' +
        '<select id="km-material" aria-label="' + esc(t('km.allMaterials')) + '">' + materialOptions + '</select>' +
        '<select id="km-session" aria-label="' + esc(t('km.allSessions')) + '">' + sessionOptions + '</select>' +
        '<button type="button" data-km="expand-all">' + esc(t('km.expandAll')) + '</button>' +
        '<button type="button" data-km="collapse-all">' + esc(t('km.collapseAll')) + '</button>' +
        '<button type="button" data-km="relations" aria-pressed="' + kmState.showRelations + '">' +
        esc(kmState.showRelations ? t('km.hideRelations') : t('km.showRelations')) + '</button>' +
        '<button type="button" data-km="fit">' + esc(t('km.fitView')) + '</button>'
      : '') +
    '</div>' +
    body;
}

/** 搜索的第一个命中节点 (在**当前可见**集合内)。 */
function kmSearchTarget() {
  if (kmState.mode !== 'map') return null;
  const needle = kmState.query.trim().toLowerCase();
  if (!needle) return null;
  const vm = kmViewModel();
  const visible = kmVisibleSet(vm);
  return vm.nodes.filter(function (node) {
    return visible[node.id] && node.type !== 'course' && node.type !== 'topic' &&
      kmMatches(node, needle);
  })[0] || null;
}

function kmRender() {
  const wasFitted = kmState.fitted;
  // spec §16 / §43C: 搜索是**导航** —— 自动展开命中项的路径 (kmHiddenSet 的
  // forced 集), 选中第一个命中项 (右侧详情随之打开), 居中, 高亮, 其余降权。
  // 不匹配的节点**保留在图上** (降权), 不删除。
  const target = kmSearchTarget();
  if (target) {
    const needle = kmState.query.trim().toLowerCase();
    const current = kmState.selected ? kmViewModel().byId[kmState.selected] : null;
    if (!current || !kmMatches(current, needle)) kmState.selected = target.id;
  }
  setView(kmShell());
  kmBind();
  // 只有"第一次进入地图"才自动适配 (spec §22)。后续的筛选/选中/折叠都保留
  // 当前的缩放与位置 —— 否则点一下节点, 整张图就跳回原处。
  if (kmState.mode === 'map' && !wasFitted) {
    kmState.fitted = true;
    kmFit();
  }
  // 搜索命中后自动居中到第一个命中项 (spec §16)。
  if (target && kmState.centeredFor !== kmState.query) {
    kmState.centeredFor = kmState.query;
    kmCenterOn(target.id);
  }
  if (!kmState.query.trim()) kmState.centeredFor = '';
}

// ---- 缩放 / 平移 / 居中 --------------------------------------------------

/**
 * 元素的屏幕尺寸。没有布局 API 的宿主 (本仓库自己的 DOM 夹具就是) 返回
 * 一组保守的默认值 —— **不抛异常**。地图的全部缩放数学都能在尺寸为 0 的
 * 环境下安全降级 (见 `kmZoom` 的 clamp 与 `kmFit` 的下限)。
 */
function kmRect(element, fallbackWidth, fallbackHeight) {
  if (!element || typeof element.getBoundingClientRect !== 'function') {
    return { width: fallbackWidth || 800, height: fallbackHeight || 600 };
  }
  const rect = element.getBoundingClientRect();
  return {
    width: rect.width || fallbackWidth || 800,
    height: rect.height || fallbackHeight || 600,
  };
}

function kmSyncViewBox() {
  const svg = document.getElementById('km-svg');
  if (!svg) return;
  const size = kmRect(svg, 800, 600);
  svg.setAttribute('viewBox', '0 0 ' + size.width + ' ' + size.height);
}

function kmApplyTransform() {
  const group = document.getElementById('km-viewport');
  if (!group) return;
  group.setAttribute('transform',
    'translate(' + KM_VIEW.x + ',' + KM_VIEW.y + ') scale(' + KM_VIEW.k + ')');
}

function kmZoom(factor, originX, originY) {
  const svg = document.getElementById('km-svg');
  if (!svg) return;
  const rect = kmRect(svg, 800, 600);
  const previous = KM_VIEW.k;
  const next = Math.min(KM_MAX_SCALE, Math.max(KM_MIN_SCALE, previous * factor));
  if (next === previous) return;
  const px = originX != null ? originX : rect.width / 2;
  const py = originY != null ? originY : rect.height / 2;
  // 以指针为锚点: 图上那一点必须停在指针下, 否则每次放大都会"漂"。
  KM_VIEW.x = px - ((px - KM_VIEW.x) / previous) * next;
  KM_VIEW.y = py - ((py - KM_VIEW.y) / previous) * next;
  KM_VIEW.k = next;
  kmApplyTransform();
}

/**
 * 适应视图 —— 尽量让**整张图**进视口 (spec §22: 第一次进入不能只看到一小部分)。
 *
 * 缩放 = min(宽比, 高比), 再取**整幅宽度可见**作为下限, 最后夹在
 * [KM_FIT_MIN_SCALE, 1]。加这条下限的原因: 16 个主题这种"高图"用纯
 * min(宽比,高比) 会缩到 0.25 左右, 12.5px 的标题在屏幕上只剩 3px ——
 * 与 V2 的目标(5-10 秒看懂层级)相悖。保证横向四个层级都进视口, 纵向溢出
 * 交给平移即可。小图不受影响(此时 widthFit 与 min(宽比,高比) 相等或更小)。
 *
 * 这里**不能**在 SVG 还没进 DOM 时调用 (拿不到 stage 宽度), 所以适配发生在
 * kmRender() 之后。
 */
function kmFit() {
  const stage = document.getElementById('km-stage');
  if (!stage) return;
  kmSyncViewBox();
  const size = kmRect(stage, 800, 600);
  const graphWidth = KM_VIEW.width || 1200;
  const graphHeight = KM_VIEW.height || 600;
  const wholeGraph = Math.min(
    size.width / Math.max(graphWidth, 1),
    size.height / Math.max(graphHeight, 1));
  const widthFit = Math.min(1, size.width / Math.max(graphWidth, 1));
  const scale = Math.max(KM_FIT_MIN_SCALE,
    Math.min(1, Math.max(wholeGraph, widthFit)));
  KM_VIEW.k = scale;
  KM_VIEW.x = (size.width - graphWidth * scale) / 2;
  KM_VIEW.y = Math.max(8, (size.height - graphHeight * scale) / 2);
  kmApplyTransform();
}

/** 把某个节点居中到视口 (搜索后自动执行)。 */
function kmCenterOn(id) {
  const vm = kmViewModel();
  const visible = kmVisibleSet(vm);
  const layout = kmLayoutCached(vm, visible);
  const pos = layout.positions[id];
  const stage = document.getElementById('km-stage');
  if (!pos || !stage) return;
  const size = kmRect(stage, 800, 600);
  KM_VIEW.x = size.width / 2 - (pos.x + pos.w / 2) * KM_VIEW.k;
  KM_VIEW.y = size.height / 2 - (pos.y + KM_NODE_H / 2) * KM_VIEW.k;
  kmApplyTransform();
}

// ---- 交互 ---------------------------------------------------------------

function kmToggle(id) {
  kmState.collapsed[id] = !kmState.collapsed[id];
  kmRender();
}

function kmExpandAll() {
  kmState.collapsed = {};
  kmRender();
}

function kmCollapseAll() {
  const vm = kmViewModel();
  const next = {};
  vm.nodes.forEach(function (node) {
    if (node.hasChildren) next[node.id] = true;
  });
  kmState.collapsed = next;
  kmRender();
}

function kmSelect(id) {
  kmState.selected = id;
  if (kmState.mode !== 'map') kmState.mode = 'map';
  kmRender();
}

function kmBind() {
  const svg = document.getElementById('km-svg');
  const stage = document.getElementById('km-stage');

  if (stage) {
    // 拖动 = 平移。用 pointer 事件而不是 mouse/touch 三套, 桌面与触屏一份代码。
    let dragging = false;
    let startX = 0;
    let startY = 0;
    let originX = 0;
    let originY = 0;
    stage.addEventListener('pointerdown', function (event) {
      if (event.target.closest('[data-km-node]')) return;
      if (event.target.closest('.km-controls')) return;
      dragging = true;
      startX = event.clientX;
      startY = event.clientY;
      originX = KM_VIEW.x;
      originY = KM_VIEW.y;
      if (stage.setPointerCapture) stage.setPointerCapture(event.pointerId);
      stage.classList.add('is-panning');
    });
    stage.addEventListener('pointermove', function (event) {
      if (!dragging) return;
      // viewBox 就是屏幕像素, 所以平移量 1:1, 不需要除以缩放。
      KM_VIEW.x = originX + (event.clientX - startX);
      KM_VIEW.y = originY + (event.clientY - startY);
      kmApplyTransform();
    });
    const stop = function () {
      dragging = false;
      stage.classList.remove('is-panning');
    };
    stage.addEventListener('pointerup', stop);
    stage.addEventListener('pointercancel', stop);
    stage.addEventListener('wheel', function (event) {
      event.preventDefault();
      const rect = kmRect(svg, 800, 600);
      kmZoom(event.deltaY < 0 ? 1.12 : 1 / 1.12, event.clientX - rect.left, event.clientY - rect.top);
    }, { passive: false });
  }

  const root = document.getElementById('view') || document;
  root.addEventListener('click', function (event) {
    const mode = event.target.closest('[data-km-mode]');
    if (mode) {
      kmState.mode = mode.getAttribute('data-km-mode');
      kmRender();
      return;
    }
    const control = event.target.closest('[data-km]');
    if (control) {
      const kind = control.getAttribute('data-km');
      if (kind === 'zoom-in') return kmZoom(1.2);
      if (kind === 'zoom-out') return kmZoom(1 / 1.2);
      if (kind === 'fit') return kmFit();
      if (kind === 'reset') { KM_VIEW.k = 1; return kmApplyTransform(); }
      if (kind === 'expand-all') return kmExpandAll();
      if (kind === 'collapse-all') return kmCollapseAll();
      if (kind === 'relations') { kmState.showRelations = !kmState.showRelations; return kmRender(); }
      if (kind === 'retry') return pageKnowledge();
    }
    const collapse = event.target.closest('[data-km-collapse]');
    if (collapse) {
      // 必须在 [data-km-node] 之前判定: 折叠按钮就长在节点方框旁边,
      // 顺序反了会让"展开"永远变成"选中节点"。
      return kmToggle(collapse.getAttribute('data-km-collapse'));
    }
    const select = event.target.closest('[data-km-select]');
    if (select) return kmSelect(select.getAttribute('data-km-select'));
    const focus = event.target.closest('[data-km-focus]');
    if (focus) {
      kmState.focus = focus.getAttribute('data-km-focus');
      kmState.selected = kmState.focus;
      return kmRender();
    }
    if (event.target.closest('[data-km-unfocus]')) {
      kmState.focus = '';
      return kmRender();
    }
    const node = event.target.closest('[data-km-node]');
    if (node) {
      kmState.selected = node.getAttribute('data-km-node');
      kmRender();
    }
  });

  // spec §36: 双击节点 = 展开/折叠 (单击 = 选中并打开详情)。
  root.addEventListener('dblclick', function (event) {
    const node = event.target.closest('[data-km-node]');
    if (!node) return;
    event.preventDefault();
    kmToggle(node.getAttribute('data-km-node'));
  });

  // 节点是 ``<g role="button" tabindex="0">``: 读屏器会把它念成一个按钮,
  // Tab 也能停上来 —— 那就必须**能用键盘激活**。
  root.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter' && event.key !== ' ' && event.key !== 'Spacebar') return;
    const collapse = event.target.closest('[data-km-collapse]');
    if (collapse) {
      event.preventDefault();
      return kmToggle(collapse.getAttribute('data-km-collapse'));
    }
    const select = event.target.closest('[data-km-select]');
    if (select) {
      event.preventDefault();
      return kmSelect(select.getAttribute('data-km-select'));
    }
    const node = event.target.closest('[data-km-node]');
    if (node) {
      event.preventDefault();
      kmState.selected = node.getAttribute('data-km-node');
      kmRender();
    }
  });

  const search = document.getElementById('km-search');
  if (search) {
    search.addEventListener('input', function () {
      kmState.query = search.value || '';
      // 搜索时自动聚焦到第一个命中项: 用户只想知道"在哪儿", 不想自己找。
      const vm = kmViewModel();
      const visible = kmVisibleSet(vm);
      const first = vm.nodes.filter(function (node) {
        return visible[node.id] && node.type !== 'course' && node.type !== 'topic' &&
          kmMatches(node, kmState.query.trim().toLowerCase());
      })[0];
      if (first && !kmState.selected) kmState.selected = first.id;
      kmRender();
      const again = document.getElementById('km-search');
      if (again) {
        again.focus();
        if (again.setSelectionRange) again.setSelectionRange(again.value.length, again.value.length);
      }
    });
  }
  ['topic', 'material', 'session', 'evidence'].forEach(function (name) {
    const element = document.getElementById('km-' + name);
    if (!element) return;
    element.addEventListener('change', function () {
      if (name === 'topic') kmState.topicId = element.value;
      if (name === 'material') kmState.materialId = element.value;
      if (name === 'session') kmState.sessionId = element.value;
      if (name === 'evidence') kmState.hasEvidence = element.value;
      kmState.focus = '';
      kmRender();
    });
  });

  // spec §37: Esc 关闭 Focus / 详情; Ctrl/Cmd+F 聚焦搜索。
  if (!kmState.__keysBound) {
    kmState.__keysBound = true;
    document.addEventListener('keydown', function (event) {
      if (!document.getElementById('km-stage')) return;
      if (event.key === 'Escape') {
        if (kmState.focus || kmState.selected) {
          kmState.focus = '';
          kmState.selected = '';
          kmRender();
        }
        return;
      }
      if ((event.ctrlKey || event.metaKey) && (event.key === 'f' || event.key === 'F')) {
        const box = document.getElementById('km-search');
        if (box) { event.preventDefault(); box.focus(); }
      }
    });
  }
}

// 旧的整页知识点列表页 (``pageKnowledgeListOnly``) 已在知识地图落地时删除:
// 它从未被 ``route()`` 接住, 也没有任何调用点 —— 是死代码, 而且绕开了
// "每个 pageXxx() 必须恰好调用一次 markActiveNav()、且归属表与实现一致"的
// 静态守卫。列表视图现在由地图内的"列表"标签 (``kmRenderList``) 提供。

// ---- 材料列表 ------------------------------------------------------------


async function pageKnowledgeDetail(courseId, knowledgeId) {
  markActiveNav('');
  setRouteCourse(courseId);
  const trace = await api('/knowledge/' + encodeURIComponent(knowledgeId) + '/trace', {
    query: { course_id: courseId },
  });
  // Source Material 节点里的"课堂"是人话标签位置 (与"文件""状态""语言"同组),
  // 不能显示内容寻址的 session_id —— 见 sessionLabel()。
  const sessions = (await api('/sessions', { query: { course_id: courseId } })).sessions || [];
  const sessionById = {};
  sessions.forEach((s) => { sessionById[s.session_id] = s; });
  const kp = trace.knowledge_point;
  const links = trace.links || [];
  const deps = trace.dependencies || {};

  const chainNodes = links.map((link) => {
    const ev = link.evidence || {};
    const material = link.material;
    const broken = !material;
    const source = ev.source || {};
    // 证据原文默认折叠: 140 字预览 + 点开展开全文。全文仍在 HTML 里
    // (溯源审计按原文断言)，只是不占首屏。
    const fullContent = String(ev.content || '');
    const preview = fullContent.length > 140
      ? '<details class="fold"><summary>' + esc(fullContent.slice(0, 140)) + '…</summary>' +
        originalBlock(fullContent, ev.language) + '</details>'
      : originalBlock(fullContent, ev.language);
    return (
      '<li' + (broken ? ' class="trace-broken"' : '') + '>' +
      '<div class="trace-node">' +
      '<div class="node-kind">Evidence</div>' +
      preview +
      '<dl class="kv compact">' +
      '<dt>evidence_id</dt><dd class="mono tiny">' + esc(ev.evidence_id) + '</dd>' +
      '<dt>' + t('类型') + '</dt><dd>' + esc(ev.evidence_type) + '</dd>' +
      '<dt>' + t('置信度') + '</dt><dd>' + esc(ev.confidence) + '</dd>' +
      '<dt>' + t('定位') + '</dt><dd>' + esc(sourceLocation(source)) + '</dd>' +
      '</dl></div>' +

      '<div style="margin-top:8px"></div>' +
      '<div class="trace-node">' +
      '<div class="node-kind">Source Material</div>' +
      (material
        ? '<dl class="kv">' +
          '<dt>' + t('文件') + '</dt><dd><strong>' + esc(material.filename) + '</strong></dd>' +
          '<dt>material_id</dt><dd class="mono tiny">' + esc(material.material_id) + '</dd>' +
          '<dt>' + t('类型 / 大小') + '</dt><dd>' + esc(material.material_type || material.source_type || '—') +
            ' · ' + fmtBytes(material.size) + '</dd>' +
          '<dt>' + t('课堂') + '</dt><dd>' + dash(sessionLabel(sessionById[material.session_id], material.session_id)) + '</dd>' +
          '<dt>' + t('状态') + '</dt><dd>' + pill(material.processing_status) + '</dd>' +
          '<dt>' + t('内容哈希') + '</dt><dd class="mono tiny break-all">' + dash(material.content_hash) + '</dd>' +
          '<dt>' + t('受管路径') + '</dt><dd class="mono tiny break-all">' + dash(material.relative_path || material.stored_path) + '</dd>' +
          '<dt>' + t('语言') + '</dt><dd>' + dash(material.language) + '</dd>' +
          '</dl>'
        : '<p class="pill pill-bad">' + t('溯源断链') + '</p>' +
          '<p class="small muted">' + t('该证据引用的材料 ') +
          '<span class="mono">' + esc(source.material_id || t('(空)')) + '</span>' +
          t(' 在本课程材料注册表中不存在。请勿据此下结论，需人工核查。') + '</p>') +
      '</div></li>'
    );
  }).join('');

  const relationList = (rels, direction) => {
    if (!rels.length) return '<li class="muted small">' + t('无') + '</li>';
    return rels.map((rel) => {
      const other = direction === 'outgoing' ? rel.target_knowledge_point_id : rel.source_knowledge_point_id;
      return '<li>' + pill(rel.relation_type, 'REGISTERED') + ' ' +
        '<a href="#/courses/' + encodeURIComponent(courseId) + '/knowledge/' +
        encodeURIComponent(other) + '">' + esc(other) + '</a>' +
        (direction === 'outgoing' ? ' <span class="tiny muted">' + t('→ 后继') + '</span>' : ' <span class="tiny muted">' + t('← 前置') + '</span>') +
        '</li>';
    }).join('');
  };

  const evidenceCheckboxes = links.map((link, index) => {
    const ev = link.evidence || {};
    return '<label class="small" style="display:block">' +
      '<input type="checkbox" name="selected_evidence" value="' + esc(ev.evidence_id) + '"> ' +
      esc(ev.evidence_id) + ' <span class="muted">#' + (index + 1) + '</span></label>';
  }).join('');

  setView(
    '<div class="page-head"><div class="crumbs">' +
    '<a href="#/">' + t('概览') + '</a> / <a href="#/courses/' + encodeURIComponent(courseId) + '">' + t('课程') + '</a> / ' +
    '<a href="#/knowledge">' + t('知识点') + '</a>' + t(' / 详情') + '</div>' +
    '<h1>' + esc(kp.title || kp.knowledge_id) + '</h1>' +
    '<p class="subtitle"><span class="mono tiny">' + esc(kp.knowledge_id) + '</span></p></div>' +

    (trace.complete ? '' :
      '<div class="banner banner-bad">' + t('该知识点的证据链不完整：') +
      esc((trace.unresolved_material_ids || []).length) + t(' 个材料引用无法解析。') + '</div>') +
    knowledgeModeNotice(kp) +

    '<div class="card"><div class="card-head"><h2>' + t('陈述') + '</h2>' +
    '<span class="row">' + pill(kp.validation_status) + pill(kp.review_status) + '</span></div>' +
    originalBlock(kp.content, null) +
    '<dl class="kv compact" style="margin-top:10px">' +
    '<dt>' + t('语言') + '</dt><dd>' +
      (trace.language.declared
        ? esc(trace.language.declared)
        : '<span class="muted">' + t('未声明') + '</span>' + t('（证据语言：') +
          ((trace.language.evidence_languages || []).map(esc).join(', ') || t('无')) + '）') +
      '</dd>' +
    '<dt>' + t('验证状态') + '</dt><dd>' + pill(kp.validation_status) + '</dd>' +
    '<dt>' + t('审核状态') + '</dt><dd>' + pill(kp.review_status) + '</dd>' +
    '<dt>' + t('重要度') + '</dt><dd>' + dash(kp.importance) + '</dd>' +
    '<dt>' + t('置信度') + '</dt><dd>' + dash(kp.confidence) + '</dd>' +
    '<dt>' + t('待验证') + '</dt><dd>' + (kp.needs_verification ? t('是') : t('否')) + '</dd>' +
    '<dt>' + t('原文术语') + '</dt><dd>' + renderTerms(kp.original_terms, { full: true, limit: 999 }) + '</dd>' +
    '<dt>' + t('主题') + '</dt><dd>' + (trace.topics.length
        ? trace.topics.map((t) => esc(t.name) + ' <span class="tiny muted mono">' + esc(t.topic_id) + '</span>').join('<br>')
        : '<span class="muted">' + t('未归入主题') + '</span>') + '</dd>' +
    '<dt>' + t('来源课堂') + '</dt><dd>' + (trace.source_sessions.length
        ? trace.source_sessions.map((s) => '<a href="#/courses/' + encodeURIComponent(courseId) +
            '/sessions/' + encodeURIComponent(s.session_id) + '">' +
            esc(sessionLabel(s)) + '</a>').join('<br>') +
          ' <span class="tiny muted">' + t('（由证据链推导）') + '</span>'
        : '<span class="muted">' + t('未关联课堂') + '</span>') + '</dd>' +
    '<dt>' + t('组织归属') + '</dt><dd>' + (trace.sessions.length
        ? trace.sessions.map((s) => '<a href="#/courses/' + encodeURIComponent(courseId) +
            '/sessions/' + encodeURIComponent(s.session_id) + '">' +
            esc(sessionLabel(s, s.session_id)) + '</a>').join('<br>')
        : '<span class="muted">' + t('未显式归入课堂') + '</span>') + '</dd>' +
    '</dl></div>' +

    // TASK-79: 中文解释层。画在原文陈述之后、溯源链之前 —— 学生先读到
    // 原文 (逐字, 不改), 需要时才往下看中文; 两个面板都可以单独重绘而不
    // 互相影响。
    '<div id="kp-zh-panel"></div>' +

    '<div class="card"><div class="card-head"><h2>' + t('溯源链：知识点 → 证据 → 源材料') + '</h2>' +
    '<span class="small muted">' + esc(links.length) + t(' 条证据 · ') +
    esc(trace.materials.length) + t(' 个源材料') + '</span></div>' +
    (links.length
      ? '<ul class="trace">' + chainNodes + '</ul>'
      : emptyState(t('该知识点没有支撑证据。没有证据就没有知识 —— 请勿据此下结论。'))) +
    '</div>' +

    // Task 40 + TASK-81 §C: 解释面板由 Task 29 的 grounded explanation 驱动,
    // 语言取**证据首选** (不再由用户选), 绝不改写证据原文; 无可用解释时显示
    // 固定文案, 前端不生成任何内容。
    '<div id="explanation-panel"></div>' +

    '<div class="grid grid-2">' +
    '<div class="card"><h2>' + t('依赖关系') + '</h2>' +
    '<h3>' + t('出边 (本点 → 后继)') + '</h3><ul class="small">' + relationList(deps.outgoing || [], 'outgoing') + '</ul>' +
    '<h3>' + t('入边 (前置 → 本点)') + '</h3><ul class="small">' + relationList(deps.incoming || [], 'incoming') + '</ul>' +
    '<h3>' + t('声明式关联') + '</h3>' +
    (((deps.related_points || []).length)
      ? '<ul class="small">' + deps.related_points.map((p) =>
          '<li><a href="#/courses/' + encodeURIComponent(courseId) + '/knowledge/' +
          encodeURIComponent(p) + '">' + esc(p) + '</a></li>').join('') + '</ul>'
      : '<p class="muted small">' + t('无') + '</p>') +
    '</div>' +'<div class="card"><h2>' + t('人工审核') + '</h2>' +
    // TASK-81 §B: 模型有把握的那批知识点已自动确认 (review_status=confirmed),
    // 人工卡因此只剩**否决**与**解决冲突**两条路 —— 没有新证据可确认的东西,
    // 不该让"点一下确认"看起来像一种验证。需要正向推进时, 拒绝/解决冲突
    // 仍会把该知识点送回人工队列 (/reviews)。
    '<p class="small muted">' + t('模型有把握的知识点已自动确认；这里只剩模型自己存疑的项，以及你随时可以否决的误入项。') + '</p>' +
    '<label class="field"><span>' + t('备注') + '</span><input type="text" id="review-note" placeholder="' + t('可选') + '"></label>' +
    (links.length
      ? '<div style="margin-bottom:10px"><div class="small muted">' + t('选择证据（解决冲突时必选）') + '</div>' +
        evidenceCheckboxes + '</div>'
      : '') +
    '<div class="actions">' +
    '<button class="danger" data-action="review-reject" data-course="' + esc(courseId) +
    '" data-knowledge="' + esc(knowledgeId) + '">' + t('拒绝') + '</button>' +
    '<button data-action="review-resolve" data-course="' + esc(courseId) +
    '" data-knowledge="' + esc(knowledgeId) + '">' + t('解决冲突') + '</button>' +
    '</div>' +
    '<div id="review-result"></div>' +
    '</div>' +
    '</div>'
  );

  // 面板挂载完成后才可加载解释。语言**证据优先**: 证据声明了什么语言才可能
  // 有解释 (后端 learning_view 的判定), 其次回落到材料声明的语言, 最后 es。
  // 以前这里是一个语言下拉, 选 zh 必然得到 language_not_available。
  await loadExplanation(courseId, knowledgeId, explanationLanguage(trace));
  // TASK-79: 中文层是**另一个**独立面板, 与 Task 29 的解释面板互不覆盖。
  await loadKpZh(courseId, knowledgeId);
}

// ---- TASK-79: 知识点级中文解释 -------------------------------------------
//
// 三条原则在**前端**同样成立:
//
// 1. 中文只是解释层。原文 (`kp.content` / 证据块) 已经用 originalBlock()
//    逐字渲染在前, 中文卡不改它、也不替换它; 卡里自带一条固定声明。
// 2. 按需。没有缓存就**只**给一个按钮, 不自动调 LLM (否则每次打开知识点
//    都产生一笔费用, 而用户可能只是想看一眼原文)。
// 3. 术语表先行。GET .../glossary 零 LLM 成本 (读材料报告里已落盘的条目),
//    所以它在翻译按钮**之前**出现 —— 很多情况下用户看术语表就够了。

function kpZhTranslateButton(courseId, knowledgeId, label) {
  return '<button class="small" data-action="kp-translate" data-course="' +
    esc(courseId) + '" data-knowledge="' + esc(knowledgeId) + '">' +
    esc(label || t('kpZh.translate')) + '</button>';
}

function renderKpZhPanel(report, glossary, courseId, knowledgeId) {
  const text = String((report || {}).translation_zh || '');
  // TASK-80: 按钮身份来自**闭包入参**, 不再从 report 里取。
  // 为什么: report 只在"已经翻过"时非空, 而"还没翻过 → 点一下翻译"才是主路径,
  // 那时从 report 取到的是空串, 按钮渲染出 data-course="" data-knowledge="",
  // 委托里 actionTranslateKp 的首行 if (!courseId || !knowledgeId) return 会
  // 静默吞掉整次点击 —— 无 toast、无请求, 用户只看到按钮点不动。
  // 入参是页面 URL 里本来就有的那两个 id, 空值兜底仍兼容旧调用形状。
  const course = courseId || (report || {}).course_id || '';
  const kp = knowledgeId || (report || {}).knowledge_id || '';
  let html = renderTranslationZhCard(report || {}, {
    meta: text ? (report.model || '') : '',
    button: kpZhTranslateButton(
      course, kp,
      text ? t('kpZh.retry') : t('kpZh.translate')),
  });
  html += renderGlossaryCard(kpZhTerms(report, glossary), true);
  return html;
}

/**
 * 知识点术语表 = 本知识点翻译里的术语 **+** 材料报告里归属它的术语。
 *
 * 为什么两个来源都要: 翻译的 `terms_zh` 是针对**这一条**知识点逐字校验过的,
 * 最贴题; 材料报告的 glossary 是**零成本**的 (读已落盘文件), 所以即使还没点
 * 翻译也能给出一张表。两者都有 ``glossary_id`` / term 去重, 先到先得 ——
 * 翻译的在前, 因为它的措辞是配合这句中文解释的。
 */
function kpZhTerms(report, glossary) {
  const out = [];
  const seen = {};
  const push = (entry) => {
    if (!entry || !entry.term) return;
    const key = String(entry.glossary_id || entry.term);
    if (seen[key]) return;
    seen[key] = true;
    out.push(entry);
  };
  ((report || {}).terms_zh || []).forEach(push);
  ((glossary || {}).glossary || []).forEach(push);
  return { glossary: out, glossary_total: out.length, glossary_complete: true };
}

async function loadKpZh(courseId, knowledgeId) {
  const panel = document.getElementById('kp-zh-panel');
  if (!panel) return;
  panel.innerHTML = '<div class="card"><p class="muted">' +
    esc(t('common.loading')) + '</p></div>';
  let glossary = { glossary: [], glossary_total: 0 };
  let report = null;
  // 术语表与翻译缓存是两个独立的读接口, 互不依赖; 任一失败都不该把另一个
  // 一起拖下水, 所以分开兜底而不是共用一个 try。
  try {
    glossary = await api('/knowledge/' + encodeURIComponent(knowledgeId) + '/glossary', {
      query: { course_id: courseId },
    });
  } catch (err) {
    glossary = { glossary: [], glossary_total: 0 };
  }
  try {
    report = await api('/knowledge/' + encodeURIComponent(knowledgeId) + '/translate', {
      query: { course_id: courseId },
    });
  } catch (err) {
    // 404 = 还没翻过。这是**正常**状态 (按需翻译), 不是错误。
    report = null;
  }
  if (report) {
    report.course_id = courseId;
    report.knowledge_id = knowledgeId;
  }
  panel.innerHTML = renderKpZhPanel(report, glossary, courseId, knowledgeId);
}

async function actionTranslateKp(courseId, knowledgeId, button) {
  if (!courseId || !knowledgeId) return;
  const panel = document.getElementById('kp-zh-panel');
  const previousLabel = button ? button.textContent : '';
  if (button) {
    button.disabled = true;
    button.textContent = t('kpZh.translating');
  }
  try {
    const report = await api('/knowledge/' + encodeURIComponent(knowledgeId) + '/translate', {
      method: 'POST',
      // TASK-80: course_id 必须**同时**进 query 与 body。
      // 后端历史上只读 query, 而这里的 query/body 是分开拼的 —— body 里的
      // course_id 永远不会出现在 URL 上, 于是"前端只传 body"的形状必现 400。
      // 带上 query 是为了与同文件的 GET (glossary / translate) 完全同口径;
      // 后端也改成 body || query 双读, 两端各一行, 避免只修一端、另一端的
      // 旧页面 / 外部调用继续坏。
      query: { course_id: courseId },
      body: { course_id: courseId, target_lang: 'zh' },
    });
    let glossary = { glossary: [], glossary_total: 0 };
    try {
      glossary = await api('/knowledge/' + encodeURIComponent(knowledgeId) + '/glossary', {
        query: { course_id: courseId },
      });
    } catch (err) { /* 术语表读失败不影响已拿到的中文解释 */ }
    report.course_id = courseId;
    report.knowledge_id = knowledgeId;
    if (panel) panel.innerHTML = renderKpZhPanel(report, glossary, courseId, knowledgeId);
  } catch (err) {
    toast(t('kpZh.failed') + ' [' + err.code + '] ' + err.message, 'bad');
    // 失败后把按钮恢复成**可重试**状态 —— 原文与已渲染的证据一个字节都没动。
    //
    // TASK-80: 这里不再用 button.outerHTML = previous。outerHTML 是一份
    // 陈旧快照: 它把"渲染那一刻的属性"原样搬回来, 正好会把本任务修掉的
    // B1 (data-* 为空) 变成永久性故障 —— 一次失败之后按钮就再也点不动了。
    // 改成用本次调用的入参重写属性: 按钮身份只由入参决定, 不依赖 DOM 快照。
    if (button) {
      button.disabled = false;
      button.textContent = previousLabel;
      button.setAttribute('data-course', courseId);
      button.setAttribute('data-knowledge', knowledgeId);
    }
  }
}

// ---- 学生页 (Task 40 会展开) ---------------------------------------------
