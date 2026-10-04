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

async function pageKnowledge() {
  markActiveNav('#/knowledge');
  const courseId = await requireCourse();
  const points = (await api('/knowledge', { query: { course_id: courseId } })).knowledge_points || [];
  const summary = await api('/course-knowledge', { query: { course_id: courseId } });

  setView(
    '<div class="page-head"><h1>' + t('知识点') + '</h1>' +
    '<p class="subtitle">' + t('课程 ') + '<strong>' + esc(courseLabel(courseId)) + '</strong>' + t(' · 共 ') +
    esc(points.length) + t(' 个 · 主题 ') + esc(summary.topic_count || 0) +
    t(' · 关系 ') + esc(summary.relation_count || 0) + ' · <a href="#/flashcards">' +
    esc(t('fc.title')) + '</a></p></div>' +
    // TASK-81 §A2: 费用是明写的 —— 已缓存的不重复花钱, 但缺失的那部分每条
    // 一次模型调用, 所以按钮旁边必须把"约一次调用 / 条"写在脸上。
    '<div class="actions"><button data-action="kp-backfill" data-course="' +
    esc(courseId) + '">' + esc(t('kpZh.backfill')) + '</button>' +
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
