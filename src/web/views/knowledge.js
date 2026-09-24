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

async function pageKnowledge() {
  markActiveNav('#/knowledge');
  const courseId = await requireCourse();
  const points = (await api('/knowledge', { query: { course_id: courseId } })).knowledge_points || [];
  const summary = await api('/course-knowledge', { query: { course_id: courseId } });

  setView(
    '<div class="page-head"><h1>' + t('知识点') + '</h1>' +
    '<p class="subtitle">' + t('课程 ') + '<strong>' + esc(courseLabel(courseId)) + '</strong>' + t(' · 共 ') +
    esc(points.length) + t(' 个 · 主题 ') + esc(summary.topic_count || 0) +
    t(' · 关系 ') + esc(summary.relation_count || 0) + '</p></div>' +
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
            '<br><span class="tiny muted mono break-all">' + esc(kp.knowledge_id) + '</span></td>' +
            '<td class="nowrap">' + pill(kp.validation_status) + ' ' + pill(kp.review_status) + '</td>' +
            '<td class="num">' + esc((kp.evidence_refs || []).length) + '</td>' +
            '<td class="small terms-cell">' + renderTerms(kp.original_terms) + '</td></tr>'
          )).join('') + '</tbody></table>'
      : emptyState(t('该课程还没有知识点。上传材料并处理后会生成。'))) +
    '</div>'
  );
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

    '<div class="card"><div class="card-head"><h2>' + t('溯源链：知识点 → 证据 → 源材料') + '</h2>' +
    '<span class="small muted">' + esc(links.length) + t(' 条证据 · ') +
    esc(trace.materials.length) + t(' 个源材料') + '</span></div>' +
    (links.length
      ? '<ul class="trace">' + chainNodes + '</ul>'
      : emptyState(t('该知识点没有支撑证据。没有证据就没有知识 —— 请勿据此下结论。'))) +
    '</div>' +

    // Task 40: 解释面板由 Task 29 的 grounded explanation 驱动, 语言仅切换"请求语言",
    // 绝不改写证据原文; 无可用解释时显示固定文案, 前端不生成任何内容。
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
    '</div>' +

    '<div class="card"><h2>' + t('人工审核') + '</h2>' +
    '<p class="small muted">' + t('审核状态只能由人工决策改变，系统永不自动确认。') + '</p>' +
    '<label class="field"><span>' + t('备注') + '</span><input type="text" id="review-note" placeholder="' + t('可选') + '"></label>' +
    (links.length
      ? '<div style="margin-bottom:10px"><div class="small muted">' + t('选择证据（确认冲突项 / 解决冲突时必选）') + '</div>' +
        evidenceCheckboxes + '</div>'
      : '') +
    '<div class="actions">' +
    '<button class="primary" data-action="review-confirm" data-course="' + esc(courseId) +
      '" data-knowledge="' + esc(knowledgeId) + '">' + t('确认') + '</button>' +
    '<button data-action="review-keep" data-course="' + esc(courseId) +
      '" data-knowledge="' + esc(knowledgeId) + '">' + t('保持未验证') + '</button>' +
    '<button class="danger" data-action="review-reject" data-course="' + esc(courseId) +
      '" data-knowledge="' + esc(knowledgeId) + '">' + t('拒绝') + '</button>' +
    '<button data-action="review-resolve" data-course="' + esc(courseId) +
      '" data-knowledge="' + esc(knowledgeId) + '">' + t('解决冲突') + '</button>' +
    '</div>' +
    '<div id="review-result"></div>' +
    '</div>' +
    '</div>'
  );

  // 面板挂载完成后才可加载解释; 语言选择器只改变"请求语言"。
  await loadExplanation(courseId, knowledgeId);
}

// ---- 学生页 (Task 40 会展开) ---------------------------------------------
