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

// 2026-10-12 (TASK-105): 顶栏那一项的文字改成「知识地图」, 它指向的路由
// (`#/knowledge`) 与 href **一字未动** —— 只把**渲染体**从旧平铺表换成知识
// 地图页 (图谱/探索/列表/材料 四个视图, 默认图谱)。
//
// 旧平铺页独有的能力里, 只有一项搬进了知识地图页, 不是丢掉:
//   ① 一键补翻 + 费用提示 → kmPaint() 的 actions 块
// (「记忆卡片」入口与每行「生成卡片」按钮原样搬来过, TASK-106 已随功能一起下线。)
// 顶栏高亮仍归 `#/knowledge` 分区, 归属声明在这里; 取数与渲染交给共享的
// kmOpenCourse (它自己不写高亮, 见其注释)。
async function pageKnowledge() {
  markActiveNav('#/knowledge');
  await kmOpenCourse(await requireCourse());
}

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
    '<a href="#/knowledge">' + t('km.title') + '</a>' + t(' / 详情') + '</div>' +
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

// ============================================================================
// TASK-KM-PREMIUM-EXECUTION: Knowledge Map Premium (知识点探索器)
// ============================================================================
//
// 设计约束 (来自任务书与 AGENTS.md, 逐条对应):
//
// 1. 不伪造数据。真实课程里 topics / knowledge_memberships / relations 全都
//    是空数组 (实测 61 KP / 2 材料 / 6 独立证据 / 61 处引用 / 0 关系 / 0 主题)。
//    所以本视图
//    **不画**层级树、不画关系边。布局是 UI-only 分组 (按证据来源分簇), 界面上
//    明确标注"按来源分组, 不是课程层级"。
// 2. 画布不画 61 个节点。61 个真实知识点挤在一屏只会变成"一排小矩形"。
//    本视图默认是 **材料分组卡片**: 真实数据里 evidence_refs 指向
//    material_id, 服务端 knowledge-map 已给出 material_to_knowledge 反查
//    索引 —— 这是当前数据下唯一真实的、可导航的分组结构。
// 3. 详情复用现有详情页 (#/courses/<id>/knowledge/<kp>), 不在画布里重造
//    一份"简化版详情"。画布里只放定位所需的最小信息。
// 4. 中文仅解释界面; 知识点标题/正文/证据原文逐字渲染, 不改写。
// 5. 列表视图与画布**共享同一份** view model, 切换视图不重新请求 API。
//
// view model (km 前缀) 与业务对象分离:
//   knowledge[]  ← 后端 KnowledgePoint 原样
//   materials[]  ← 后端 Material 原样
//   clusters     ← derived: material_id -> knowledge ids (真实反查)
//   orphans      ← derived: 无证据的知识点 (真实情况, 不隐藏)
//   ui           ← 纯 UI 状态: query / activeMaterial / view

const KM_STATE = {
  knowledge: [],
  materials: [],
  clusters: {},      // material_id -> [knowledge_id]
  orphans: [],       // 无证据的知识点
  conflicts: [],
  relations: [],     // 已登记的有向关系 (权威; 图表两处都读它)
  topics: [],        // 课程自己登记的主题 (真实为空就是空)
  memberships: [],   // topic_id -> knowledge_point_id
  stats: {},
  graph: null,       // 图谱投影 (nodes/edges/concepts), 见 views/knowledge-graph.js
  courseId: '',
  query: '',
  activeMaterial: 'all',
  view: 'graph',     // graph | explore | list | materials
  loaded: false,
};

// ---- 数据加载与 view model 装配 ----------------------------------------

function kmKnowledgeById() {
  const byId = {};
  KM_STATE.knowledge.forEach((kp) => { byId[String(kp.knowledge_id)] = kp; });
  return byId;
}

// 从 API 响应装配 view model。
//
// 关键: 分组结构**完全来自后端** (material_to_knowledge 是服务端算好的反查),
// 前端不推断、不重排、不补全。evidence_refs 是 KP 记录上的原始字段, 直接读。
function kmBuildViewModel(payload) {
  const data = payload || {};
  KM_STATE.knowledge = Array.isArray(data.knowledge_points) ? data.knowledge_points.slice() : [];
  KM_STATE.materials = Array.isArray(data.materials) ? data.materials.slice() : [];
  KM_STATE.conflicts = Array.isArray(data.conflicts) ? data.conflicts.slice() : [];
  // 已登记关系 / 主题 / 成员: 后端自 2026-10-13 起给真实读投影 (以前这三项
  // 分别是 PREREQUISITE-only 的依赖出边、写死的 [] 和写死的 [])。前端从
  // 不读 relations 的话, 权威关系就白算了 —— 现在图谱详情面板直接读它。
  KM_STATE.relations = Array.isArray(data.relations) ? data.relations.slice() : [];
  KM_STATE.topics = Array.isArray(data.topics) ? data.topics.slice() : [];
  KM_STATE.memberships = Array.isArray(data.knowledge_memberships)
    ? data.knowledge_memberships.slice() : [];
  KM_STATE.stats = data.stats || {};
  // 图谱投影 (后端 src/knowledge_graph.py): 节点/边/未归类概念/未匹配术语。
  // 缺字段时置 null —— 图谱视图会给出明确空态, 而不是画一张假图。
  KM_STATE.graph = data.graph && Array.isArray(data.graph.nodes) ? data.graph : null;

  const reverse = data.material_to_knowledge || {};
  const clusters = {};
  Object.keys(reverse).forEach((materialId) => {
    clusters[materialId] = (reverse[materialId] || []).slice();
  });
  KM_STATE.clusters = clusters;

  // 无证据的知识点: 数据真实存在的一部分 (目标课程 61 条每条 1 条引用, 但另两门课
  // 有 0 证据的)。列为"未定位"组, 明确告知缺证据, 不隐藏也不编造。
  KM_STATE.orphans = KM_STATE.knowledge
    .filter((kp) => !(kp.evidence_refs || []).length)
    .map((kp) => String(kp.knowledge_id));

  KM_STATE.loaded = true;
}

async function kmLoad(courseId) {
  KM_STATE.courseId = courseId || '';
  KM_STATE.query = '';
  KM_STATE.activeMaterial = 'all';
  // 默认进图谱: 这一页叫"知识地图", 首屏就该是图 (2026-10-12 用户实测反馈:
  // 之前默认是"按材料分组的卡片", 名字叫地图、形态是列表)。
  KM_STATE.view = 'graph';
  KM_STATE.loaded = false;
  const payload = await api('/courses/' + encodeURIComponent(courseId) + '/knowledge-map');
  kmBuildViewModel(payload);
}

// ---- 过滤: 全局搜索 + 来源筛选 --------------------------------------------
//
// 搜索只在"标题 / 原文术语"上做子串匹配。不引入模糊匹配打分 —— 真实数据里
// 标题是完整短语, 子串已经够用; 引入打分只会让"为什么这条排前面"更难解释。

function kmMatches(kp, needle) {
  if (!needle) return true;
  const haystack = [kp.title || '', kp.knowledge_id || '']
    .concat(kp.original_terms || [])
    .join('\n')
    .toLowerCase();
  return haystack.indexOf(needle) >= 0;
}

function kmVisibleKnowledge() {
  const needle = KM_STATE.query.trim().toLowerCase();
  return KM_STATE.knowledge.filter((kp) => {
    if (KM_STATE.activeMaterial !== 'all') {
      const materialId = KM_STATE.activeMaterial;
      if ((KM_STATE.clusters[materialId] || []).indexOf(String(kp.knowledge_id)) < 0) return false;
    }
    return kmMatches(kp, needle);
  });
}

// ---- 渲染: 小部件 --------------------------------------------------------

function kmMaterialLabel(material) {
  if (!material) return '';
  return String(material.filename || material.material_id || '');
}

// 统计条: 只报真实计数, 不说"已建立知识图谱"这类没数据支撑的话。
//
// 口径 (TASK-KM-PREMIUM-QA P0, 与后端 knowledge_map 同源):
//   证据 = 去重后的独立证据数 (后端 stats.evidence_count)
//   引用 = 知识点→证据的关联总数 (后端 stats.evidence_ref_count)
// 真实案例: 61 个 KP 各 1 条引用, 但只对应 6 条独立证据 —— 两数都对,
// 口径不同, 必须同时展示, 不得把引用数标成证据数。
function kmDistinctEvidenceTotal() {
  const seen = {};
  KM_STATE.knowledge.forEach((kp) => {
    (kp.evidence_refs || []).forEach((ref) => {
      const id = (ref && typeof ref === 'object') ? ref.evidence_id : ref;
      if (id) seen[String(id)] = true;
    });
  });
  return Object.keys(seen).length;
}

function kmStatsBar() {
  const stats = KM_STATE.stats || {};
  const kpTotal = KM_STATE.knowledge.length;
  // 后端新字段优先; 旧 payload / 单测 mock 无该字段时用本地计数回退。
  const relTotal = (stats.relation_count != null)
    ? stats.relation_count
    : (KM_STATE.relations || []).length;
  const clustered = KM_STATE.knowledge.length - KM_STATE.orphans.length;
  // 后端新字段优先; 旧 payload / 单测 mock 无该字段时回退本地计算。
  const evTotal = (stats.evidence_count != null) ? stats.evidence_count : kmDistinctEvidenceTotal();
  const refTotal = (stats.evidence_ref_count != null)
    ? stats.evidence_ref_count
    : KM_STATE.knowledge.reduce((sum, kp) => sum + (kp.evidence_refs || []).length, 0);
  return '<div class="km-stats">' +
    '<span class="km-stat"><b>' + esc(kpTotal) + '</b>' + esc(t('km.statKnowledge')) + '</span>' +
    '<span class="km-stat"><b>' + esc(clustered) + '</b>' + esc(t('km.statClustered')) + '</span>' +
    '<span class="km-stat"><b>' + esc(KM_STATE.orphans.length) + '</b>' + esc(t('km.statOrphans')) + '</span>' +
    '<span class="km-stat"><b>' + esc(evTotal) + '</b>' + esc(t('km.statEvidence')) + '</span>' +
    '<span class="km-stat"><b>' + esc(refTotal) + '</b>' + esc(t('km.statEvidenceRefs')) + '</span>' +
    '<span class="km-stat"><b>' + esc(stats.material_count || KM_STATE.materials.length) + '</b>' + esc(t('km.statMaterials')) + '</span>' +
    // 已登记关系: 这一项只数 ``knowledge_relations`` 里的一等事实, 由正文
    // "关联:" 行推断出的边**不算** (它们无向、无类型, 不是已登记的知识关系)。
    // 两个数字在图例里分别解释, 不合成一个模糊的"关系数"。
    '<span class="km-stat"><b>' + esc(relTotal) + '</b>' + esc(t('km.statRelations')) + '</span>' +
    '</div>';
}

function kmConflictBanner() {
  if (!KM_STATE.conflicts.length) return '';
  return '<div class="km-conflicts">' +
    '<span class="pill pill-warn">' + esc(t('km.conflictTag')) + '</span> ' +
    '<span class="small">' + esc(t('km.conflictNote').replace('{n}', String(KM_STATE.conflicts.length))) + '</span> ' +
    '<a class="small" href="#/courses/' + encodeURIComponent(KM_STATE.courseId) + '/review">' +
    esc(t('km.conflictLink')) + '</a></div>';
}

// 搜索框 + 视图切换 + 来源筛选。
//
// 搜索框是**受控输入**: 每次 input 只改 view model 再局部重绘, 不打 API。
// 视图切换按钮用 aria-pressed 表达"当前是哪个视图", 不靠 class 名单独承载状态。
function kmToolbar() {  const viewButtons = [
    '<button type="button" class="km-view-btn" data-action="km-set-view" data-view="graph" aria-pressed="' + (KM_STATE.view === 'graph' ? 'true' : 'false') + '">' + esc(t('km.view.graph')) + '</button>',
    '<button type="button" class="km-view-btn" data-action="km-set-view" data-view="explore" aria-pressed="' + (KM_STATE.view === 'explore' ? 'true' : 'false') + '">' + esc(t('km.view.explore')) + '</button>',
    '<button type="button" class="km-view-btn" data-action="km-set-view" data-view="list" aria-pressed="' + (KM_STATE.view === 'list' ? 'true' : 'false') + '">' + esc(t('km.view.list')) + '</button>',
    '<button type="button" class="km-view-btn" data-action="km-set-view" data-view="materials" aria-pressed="' + (KM_STATE.view === 'materials' ? 'true' : 'false') + '">' + esc(t('km.view.materials')) + '</button>',
  ].join('');
  const materialOptions = ['<option value="all">' + esc(t('km.filterAll')) + '</option>']
    .concat(KM_STATE.materials.map((m) => (
      '<option value="' + esc(m.material_id) + '"' +
      (KM_STATE.activeMaterial === String(m.material_id) ? ' selected' : '') + '>' +
      esc(kmMaterialLabel(m)) + '</option>'
    )));
  return '<div class="km-toolbar">' +
    '<div class="km-search">' +
    '<label class="sr-only" for="km-search-input">' + esc(t('km.searchLabel')) + '</label>' +
    '<input type="text" id="km-search-input" data-action="km-search" autocomplete="off" ' +
    'placeholder="' + esc(t('km.searchPlaceholder')) + '" value="' + esc(KM_STATE.query) + '">' +
    (KM_STATE.query
      ? ' <button type="button" class="km-clear" data-action="km-clear-search">' +
        esc(t('km.clearSearch')) + '</button>'
      : '') +
    '</div>' +
    '<div class="km-filters">' +
    '<label class="sr-only" for="km-filter-material">' + esc(t('km.filterSource')) + '</label>' +
    '<select id="km-filter-material" data-action="km-filter-material">' +
    materialOptions.join('') + '</select>' +
    '<div class="km-view-switch" role="group" aria-label="' + esc(t('km.viewLabel')) + '">' +
    viewButtons + '</div>' +
    '</div></div>';
}

// ---- 渲染: 探索视图 (默认) ----------------------------------------------
//
// 布局 = "按材料分簇"的卡片流。每张卡 = 一个真实材料 + 归属它的知识点列表。
// 这是当前数据下**唯一真实**的空间化关联, 不需要任何推断。
//
// 卡片头显式写 "按来源分组 · 不是课程层级" —— 防止用户把 UI 分组误读成
// 系统已经知道的知识层级 (项目铁律: 不伪造数据)。

function kmClusterCard(materialId, visibleIds) {
  const material = KM_STATE.materials.filter((m) => String(m.material_id) === materialId)[0];
  // 防御: 后端修复前曾把证据 ID 当材料 ID 发下来, 那种键在这里查不到材料。
  // 现在后端只发真实材料 ID, 这里保留兜底 —— 有数据绝不隐藏, 只明确标注。
  const title = material ? kmMaterialLabel(material) : String(materialId);
  const unknownNote = material
    ? ''
    : ' <span class="pill pill-warn tiny">' + esc(t('km.unknownMaterial')) + '</span>';
  const ids = KM_STATE.clusters[materialId] || [];
  const byId = kmKnowledgeById();
  // 性能: 可见集由调用方一次算好传进来 (见 kmRenderExplore)。直接调
  // kmVisibleKnowledge() 会让每行都重算一次全量过滤 —— 61 条无感,
  // 1000 条时搜索从 ~50ms 涨到 ~1700ms (实测)。不传时回退旧路径。
  const inScope = (typeof visibleIds === 'object' && visibleIds !== null)
    ? (kid) => !!visibleIds[kid]
    : (kid) => kmVisibleKnowledge().some((kp) => String(kp.knowledge_id) === kid);
  const visible = ids.filter(inScope);
  const rows = visible.map((kid) => {
    const kp = byId[kid];
    if (!kp) return '';
    const evCount = (kp.evidence_refs || []).length;
    return '<li class="km-kp-row">' +
      '<a class="km-kp-title" href="#/courses/' + encodeURIComponent(KM_STATE.courseId) +
      '/knowledge/' + encodeURIComponent(kid) + '">' + esc(kp.title || kid) + '</a>' +
      knowledgeModeBadge(kp) +
      '<span class="km-kp-meta">' +
      ' <span class="pill pill-muted tiny">' + esc(t('km.evidenceCount', { n: evCount })) + '</span>' +
      (evCount ? '' : ' <span class="pill pill-warn tiny">' + esc(t('km.noEvidence')) + '</span>') +
      '</span></li>';
  }).join('');

  return '<div class="card km-cluster">' +
    '<div class="card-head"><h2>' + esc(title) + '</h2>' + unknownNote +
    '<span class="km-cluster-note">' + esc(t('km.uiOnlyGrouping')) + '</span></div>' +
    (visible.length
      ? '<ul class="km-kp-list">' + rows + '</ul>'
      : emptyState(t('km.clusterEmpty'))) +
    '</div>';
}

function kmOrphanCard(visibleIds) {
  const byId = kmKnowledgeById();
  const inScope = (typeof visibleIds === 'object' && visibleIds !== null)
    ? (kid) => !!visibleIds[kid]
    : (kid) => kmVisibleKnowledge().some((kp) => String(kp.knowledge_id) === kid);
  const visible = KM_STATE.orphans
    .filter(inScope)
    .map((kid) => {
      const kp = byId[kid] || {};
      return '<li class="km-kp-row">' +
        '<a class="km-kp-title" href="#/courses/' + encodeURIComponent(KM_STATE.courseId) +
        '/knowledge/' + encodeURIComponent(kid) + '">' + esc(kp.title || kid) + '</a>' +
        knowledgeModeBadge(kp) +
        '</li>';
    }).join('');
  if (!visible) return '';
  return '<div class="card km-cluster km-cluster-orphan">' +
    '<div class="card-head"><h2>' + esc(t('km.unlocated')) + '</h2></div>' +
    '<p class="small muted">' + esc(t('km.unlocatedNote')) + '</p>' +
    (visible
      ? '<ul class="km-kp-list">' + visible + '</ul>'
      : emptyState(t('km.clusterEmpty'))) +
    '</div>';
}

function kmRenderExplore() {
  // 可见集只算一次, 分发给各卡片 (性能, 见 kmClusterCard 注释)。
  const visibleList = kmVisibleKnowledge();
  const visibleIds = {};
  visibleList.forEach((kp) => { visibleIds[String(kp.knowledge_id)] = true; });
  if (!visibleList.length) {
    return KM_STATE.query.trim() || KM_STATE.activeMaterial !== 'all'
      ? emptyState(t('km.searchEmpty'))
      : emptyState(t('km.courseEmpty'));
  }
  const parts = [];
  const clusterIds = Object.keys(KM_STATE.clusters).filter((id) => (KM_STATE.clusters[id] || []).length);
  clusterIds.forEach((id) => parts.push(kmClusterCard(id, visibleIds)));
  const orphanCard = kmOrphanCard(visibleIds);
  if (orphanCard) parts.push(orphanCard);
  return parts.join('');
}

// ---- 渲染: 列表视图 ------------------------------------------------------
//
// 列表与画布共享同一份 view model (KM_STATE), 切换视图**不重新请求 API**。
// 表格列与知识点页一致 (标题 / 状态 / 证据数 / 归属材料), 只补一列"来源材料" ——
// 这是从 material_to_knowledge 反查出来的真实归属, 不是前端猜的。

function kmMaterialNamesFor(knowledgeId) {
  const names = [];
  Object.keys(KM_STATE.clusters).forEach((materialId) => {
    if ((KM_STATE.clusters[materialId] || []).indexOf(knowledgeId) >= 0) {
      const material = KM_STATE.materials.filter((m) => String(m.material_id) === materialId)[0];
      names.push(kmMaterialLabel(material) || materialId);
    }
  });
  return names;
}

function kmRenderList() {
  const visible = kmVisibleKnowledge();
  if (!visible.length) {
    return KM_STATE.query.trim() || KM_STATE.activeMaterial !== 'all'
      ? emptyState(t('km.searchEmpty'))
      : emptyState(t('km.courseEmpty'));
  }
  return '<div class="card"><table class="data compact fixed">' +
    tableCaption(t('km.title')) +
    '<colgroup><col style="width:32%"><col style="width:130px"><col style="width:70px">' +
    '<col></colgroup>' +
    '<thead><tr>' +
    '<th scope="col">' + t('标题') + '</th>' +
    '<th scope="col">' + t('状态') + '</th>' +
    '<th scope="col" class="num">' + t('证据') + '</th>' +
    '<th scope="col">' + t('km.colSource') + '</th>' +
    '</tr></thead><tbody>' +
    visible.map((kp) => {
      const kid = String(kp.knowledge_id);
      const evCount = (kp.evidence_refs || []).length;
      const sources = kmMaterialNamesFor(kid);
      return '<tr>' +
        '<td class="kp-title"><a href="#/courses/' + encodeURIComponent(KM_STATE.courseId) +
        '/knowledge/' + encodeURIComponent(kid) + '">' + esc(kp.title || kid) + '</a>' +
        knowledgeModeBadge(kp) +
        // TASK-105: 旧平铺页每行的「生成卡片」入口, 原样搬进列表视图的行
        // (有证据才给按钮, 没证据给灰字 —— 与旧页同一判据)。
        '<br><span class="tiny muted mono break-all">' + esc(kid) + '</span></td>' +
        '<td class="nowrap">' + pill(kp.validation_status) + ' ' + pill(kp.review_status) + '</td>' +
        '<td class="num">' + esc(evCount) + '</td>' +
        '<td class="small break-all">' +
        (sources.length
          ? sources.map((name) => esc(name)).join('<br>')
          : '<span class="muted">' + esc(t('km.unlocated')) + '</span>') +
        '</td></tr>';
    }).join('') +
    '</tbody></table></div>';
}

// ---- 渲染: Material Map ---------------------------------------------------
//
// 材料列在左, 它支撑的知识点列在右, 每行带真实证据条数。这是"知识点 → 材料"
// 这张关系在当前数据里**唯一可画**的方向, 服务端已给出反查索引。
// 没有证据的材料如实写 "0 条证据", 不隐藏。

function kmRenderMaterials() {
  if (!KM_STATE.materials.length) return emptyState(t('km.noMaterials'));
  const rows = KM_STATE.materials.map((m) => {
    const materialId = String(m.material_id);
    const ids = KM_STATE.clusters[materialId] || [];
    const byId = kmKnowledgeById();
    const links = ids.map((kid) => {
      const kp = byId[kid] || {};
      return '<li><a href="#/courses/' + encodeURIComponent(KM_STATE.courseId) +
        '/knowledge/' + encodeURIComponent(kid) + '">' + esc(kp.title || kid) + '</a></li>';
    }).join('');
    return '<div class="card km-material-row">' +
      '<div class="card-head"><h2>' + esc(kmMaterialLabel(m)) + '</h2>' +
      '<span class="km-material-meta">' +
      '<span class="pill ' + (ids.length ? 'pill-info' : 'pill-warn') + ' tiny">' +
      esc(t('km.supportCount', { n: ids.length })) + '</span>' +
      ' <span class="tiny muted">' + esc(t('km.supportsHint')) + '</span>' +
      '</span></div>' +
      '<dl class="kv compact">' +
      '<dt>' + t('类型 / 大小') + '</dt><dd>' +
      esc(m.material_type || m.source_type || '—') + ' · ' + fmtBytes(m.size) + '</dd>' +
      '<dt>' + t('状态') + '</dt><dd>' + pill(m.processing_status) + '</dd>' +
      '<dt>material_id</dt><dd class="mono tiny break-all">' + esc(materialId) + '</dd>' +
      '<dt>' + t('路径') + '</dt><dd class="mono tiny break-all">' +
      dash(m.relative_path || m.stored_path) + '</dd>' +
      '</dl>' +
      (links ? '<h3>' + esc(t('km.supports')) + '</h3><ul class="small km-kp-list">' + links + '</ul>'
             : '<p class="small muted">' + esc(t('km.supportsNone')) + '</p>') +
      '</div>';
  }).join('');
  return rows;
}

// ---- 页面入口 ------------------------------------------------------------
//
// 错误语义分两档 (不混为一谈):
//   ApiError  (NETWORK_ERROR / NOT_FOUND / INTERNAL_ERROR) → renderError 整页错误态,
//     因为这是"读不到数据", 用户能做的只有重试或换课程。
//   渲染期异常 → 也走 renderError, 但先 showBanner 提示重试。

// 打开某门课程的知识地图页。**不写顶栏高亮** —— 归属由调用方声明:
//   pageKnowledge    → '#/knowledge' (顶栏有这一项; 2026-10-12 TASK-105 起它渲染本页)
//   pageKnowledgeMap → ''            (课程内深链, 顶栏没有对应分区)
// 两个调用方各自恰好写一次 markActiveNav(), 与 app.js 的顶栏归属表一致。
// 不抽成共享函数的话, 顶栏高亮会被本函数里的清空声明覆盖掉。
async function kmOpenCourse(courseId) {
  setRouteCourse(courseId);
  showBanner('');
  showLoading();
  try {
    await kmLoad(courseId);
  } catch (err) {
    if (err instanceof ApiError) renderError(err);
    else renderError(new ApiError({ code: 'INTERNAL_ERROR', message: String((err && err.message) || err) }));
    return;
  }
  kmPaint();
}

async function pageKnowledgeMap(courseId) {
  const id = courseId;
  if (!id) {
    renderError(new ApiError({ code: 'NOT_FOUND', message: t('km.needCourse') }));
    return;
  }
  markActiveNav('');
  await kmOpenCourse(id);
}

// kmPaint: 只重绘 #view 的内容区, 不触碰 chrome (顶栏 / 侧栏)。
// 搜索 / 切换视图 / 筛选都走这条路径 —— 没有新的网络请求。
function kmPaint() {
  if (!KM_STATE.loaded) {
    setView('<div class="card"><p class="muted">' + esc(t('common.loading')) + '</p></div>');
    return;
  }
  const body = KM_STATE.view === 'graph' ? kmRenderGraph()
    : KM_STATE.view === 'list' ? kmRenderList()
      : KM_STATE.view === 'materials' ? kmRenderMaterials()
        : kmRenderExplore();
  setView(
    '<div class="page-head">' +
    '<div class="crumbs"><a href="#/courses/' + encodeURIComponent(KM_STATE.courseId) + '">' +
    esc(t('km.backToCourse')) + '</a></div>' +
    '<h1>' + esc(t('km.title')) + '</h1>' +
    '<p class="subtitle">' + esc(t('km.subtitle', { course: courseLabel(KM_STATE.courseId) })) +
    '</div>' +
    // TASK-81 §A2 / TASK-105: 一键补翻 —— 旧平铺页的独有入口, 换渲染体时
    // 必须一起搬 (否则是静默删功能)。费用提示与旧页逐字相同。
    // 注意: 搜索/切视图/换筛选都会局部重绘本页, 这个按钮节点随之被替换;
    // 处理函数 finally 里对**已脱离文档的**旧节点恢复 disabled —— 无副作用
    // (不报错、不闪烁), 对用户不可见, 不需要额外代码。
    '<div class="actions"><button data-action="kp-backfill" data-course="' +
    esc(KM_STATE.courseId) + '">' + esc(t('kpZh.backfill')) + '</button>' +
    '<span class="tiny muted">' + esc(t('kpZh.backfillHint')) + '</span></div>' +
    kmConflictBanner() +
    kmStatsBar() +
    kmToolbar() +
    '<div id="km-body">' + body + '</div>' +
    kmLegend() +
    '</div>'
  );
  // 图谱画在 canvas 上: HTML 注入 DOM 之后才能量尺寸、跑布局、绑事件。
  if (KM_STATE.view === 'graph') kmGraphMount();
}

// 图例随视图变: 图谱视图解释节点/边/概念, 其它视图解释"按来源分组"。
// (旧文案 "暂无知识关系" 在图谱视图里是错的 —— 图上有边。)
function kmLegend() {
  if (KM_STATE.view === 'graph') {
    return '<div class="card km-legend">' +
      '<h2>' + esc(t('km.legendTitle')) + '</h2>' +
      '<p class="small">' + esc(t('km.graph.legendKp')) + '</p>' +
      '<p class="small">' + esc(t('km.graph.legendConcept')) + '</p>' +
      // 两种边分开说明。不合并成一句"边 = 关联": 它们一个是权威事实 (有向、
      // 有类型、可审核), 一个是文本推断 (无向、仅供参照), 混为一谈就把
      // 推断升格成事实了。
      '<p class="small">' + esc(t('km.graph.legendRegistered')) + '</p>' +
      '<p class="small">' + esc(t('km.graph.legendInferred')) + '</p>' +
      '<p class="small">' + esc(t('km.graph.legendEdge')) + '</p>' +
      '</div>';
  }
  return '<div class="card km-legend">' +
    '<h2>' + esc(t('km.legendTitle')) + '</h2>' +
    '<p class="small">' + esc(t('km.legendGrouping')) + '</p>' +
    '<p class="small">' + esc(t('km.legendNoRelations')) + '</p>' +
    '<p class="small">' + esc(t('km.legendClick')) + '</p>' +
    '</div>';
}

// ---- 事件 (委托到 window 上, 不依赖渲染完成时机) ------------------------

function kmHandleSearch(event) {
  KM_STATE.query = String(event.target.value || '');
  // 图谱视图: 只重绘 canvas (不重建 DOM) —— 否则每敲一个字都重挂 canvas,
  // 输入框会掉焦点。搜索在图谱里表现为"命中节点加高亮圈"。
  if (KM_STATE.view === 'graph' && typeof kmGraphSearchChanged === 'function') {
    kmGraphSearchChanged();
    return;
  }
  kmPaint();
}

function kmHandleFilter(event) {
  KM_STATE.activeMaterial = String(event.target.value || 'all');
  kmPaint();
}

function kmSetView(view) {
  const allowed = ['graph', 'explore', 'list', 'materials'];
  const next = allowed.indexOf(view) >= 0 ? view : 'graph';
  KM_STATE.view = next;
  kmPaint();
}

// 统一出口: 全局 data-action 派发 (app.js 的 click 委托会先到这里)。
function kmAction(action, node) {
  if (action === 'km-search' || action === 'km-filter-material') return;  // input 类自己管
  if (action === 'km-clear-search') {
    KM_STATE.query = '';
    kmPaint();
    const input = document.getElementById('km-search-input');
    if (input) input.focus();
    return;
  }
  if (action === 'km-set-view') {
    kmSetView(node.getAttribute('data-view'));
    return;
  }
  // 图谱自己的控件 (适应窗口 / 只看有关联 / 概念 / 标签) —— 只重绘 canvas。
  if (action.indexOf('km-graph-') === 0 && typeof kmGraphAction === 'function') {
    kmGraphAction(action, node);
  }
}

// input / change 类事件是"逐字符触发", 不适合走 click 委托。
window.addEventListener('input', (event) => {
  const node = event.target;
  if (!node || !node.getAttribute) return;
  if (node.getAttribute('data-action') === 'km-search') kmHandleSearch(event);
});
window.addEventListener('change', (event) => {
  const node = event.target;
  if (!node || !node.getAttribute) return;
  if (node.getAttribute('data-action') === 'km-filter-material') kmHandleFilter(event);
});
