/*
 * 课程总览 (``#/courses``) / 单门课 (``#/courses/<id>``) / 课堂详情。
 */
'use strict';

// ---- 创建入口 ------------------------------------------------------------
//
// 「新建课程」与「新建课堂」是**新用户的第一道门**: 在此之前网页上没有任何
// 创建入口, 零课程时用户只能自己去 curl ``POST /api/courses``。项目历史上
// 修过两次同类问题 (学生注册表单、材料上传表单), 课程 / 课堂是最后一处。
//
// 三条共同约束:
//
// 1. 表单**永远渲染** —— 空态下更是必须的, 否则第一门课永远建不出来
//    (与 ``pageStudents`` 的注册表单同一条理由);
// 2. 空值**不发送** —— 后端把字段缺省当"没给", 把 ``''`` 当"给了个空值",
//    两者在幂等键上不是一回事 (与 ``wireStudentForm`` 同一条规则);
// 3. 幂等由后端保证 (同名同代码 / 同课号返回已有实体, HTTP 200 而不是 201),
//    前端只如实显示结果, 不自己判重。

//: ``POST /api/courses`` 接受的课程语言别名 (ISO 639-1)。
//: 取值域来自 ``course_service._LANG_ALIASES`` —— 多写一个后端会解析成
//: Unknown, 界面却让人以为选到了。``''`` = 交给后端按课程名自动识别。
const COURSE_LANGUAGES = ['es', 'ca', 'zh', 'en'];

/** 新建课程表单。零课程时也必须渲染 —— 见本节的第 1 条约束。 */
function courseForm() {
  return '<div class="card"><div class="card-head"><h2>' + esc(t('新建课程')) +
    '</h2></div>' +
    '<form id="course-form" class="grid grid-3">' +
    '<label class="field"><span>' + esc(t('课程名称')) + '</span>' +
    '<input type="text" name="name" required maxlength="120" placeholder="' +
    esc(t('例如: Álgebra Lineal')) + '"></label>' +
    '<label class="field"><span>' + esc(t('course.code')) + '</span>' +
    '<input type="text" name="code" maxlength="32" placeholder="' +
    esc(t('例如: ALG')) + '"></label>' +
    '<label class="field"><span>' + esc(t('course.language')) + '</span>' +
    '<select name="language">' +
    '<option value="">' + esc(t('自动识别')) + '</option>' +
    COURSE_LANGUAGES.map((code) =>
      '<option value="' + esc(code) + '">' + esc(code) + '</option>').join('') +
    '</select></label>' +
    '<div class="actions"><button class="primary" type="submit">' + esc(t('创建')) +
    '</button><span class="small muted">' +
    esc(t('同名同代码的课程只会创建一次；重复提交返回已有课程。')) + '</span></div>' +
    '</form></div>';
}

/** 新建课堂表单 (内嵌在课程页的「课堂」卡片里, 所以只返回 ``<form>``)。 */
function sessionForm(courseId) {
  return '<form id="session-form" class="grid grid-3" data-course="' +
    esc(courseId) + '">' +
    '<label class="field"><span>' + esc(t('课号')) + '</span>' +
    '<input type="number" name="session_number" required min="0" step="1" placeholder="1"></label>' +
    '<label class="field"><span>' + esc(t('标题')) + '</span>' +
    '<input type="text" name="title" maxlength="120" placeholder="' +
    esc(t('例如: Tema 3')) + '"></label>' +
    '<label class="field"><span>' + esc(t('日期')) + '</span>' +
    '<input type="date" name="date"></label>' +
    '<div class="actions"><button class="primary" type="submit">' + esc(t('创建')) +
    '</button><span class="small muted">' +
    esc(t('课号在课程内唯一；重复提交同一个课号会返回已有课堂，不会新建第二堂。')) +
    '</span></div>' +
    '</form>';
}

async function pageMyCourses() {
  markActiveNav('#/courses');
  const data = await api('/my-courses', {
    query: { lang: state.lang, preferred: state.courseId || undefined },
  });
  const rows = data.courses || [];
  // 后端已经按 course_id 排好; 这里再排一次是为了**不依赖**后端顺序 ——
  // 万一后端换了排序规则, 这一页仍然是确定性的。
  const ordered = rows.slice().sort((a, b) =>
    String(a.course_id).localeCompare(String(b.course_id)));

  const selection = data.selection || {};
  if (selection.course_id && selection.course_id !== state.courseId) {
    setCourse(selection.course_id);
  }

  if (!ordered.length) {
    // 空态也要有创建入口 —— 否则第一门课永远建不出来。
    setView(
      '<div class="page-head"><h1>' + esc(t('mc.title')) + '</h1>' +
      '<p class="subtitle">' + esc(t('mc.subtitle')) + '</p></div>' +
      '<div class="card">' + emptyState(t('mc.empty')) + '</div>' +
      courseForm()
    );
    wireCourseForm();
    return;
  }

  const totals = data.totals || {};
  setView(
    '<div class="page-head"><h1>' + esc(t('mc.title')) + '</h1>' +
    '<p class="subtitle">' + esc(t('mc.subtitle')) + '</p>' +
    '<p class="small muted">' + esc(t('mc.noRanking')) + '</p></div>' +

    '<div class="card"><div class="card-head"><h2>' + esc(t('mc.totals')) + '</h2>' +
    '<span class="tiny muted">' + esc(t('mc.note')) + '</span></div>' +
    '<div class="mc-counts">' + MC_COUNT_KEYS.map((key) => (
      '<span class="pill pill-muted">' + esc(t('mc.count.' + key)) + ' ' +
      esc(fmtNumber((totals.counts || {})[key] || 0)) + '</span>'
    )).join(' ') + '</div>' +
    mcAxisRow(t('mc.axisValidation'), MC_VALIDATION_KEYS, totals.validation) +
    mcAxisRow(t('mc.axisReview'), MC_REVIEW_KEYS, totals.review) +
    '<p class="small muted">' + esc(t('mc.axesNote')) + '</p>' +
    '<p class="small muted">' + esc(t('mc.sharedIdNote')) + '</p>' +
    '</div>' +

    ordered.map((row) => mcCard(row, state.courseId)).join('') +
    courseForm()
  );
  wireCourseForm();
}

function wireCourseForm() {
  const form = document.getElementById('course-form');
  if (!form) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const name = (form.querySelector('input[name="name"]').value || '').trim();
    if (!name) { toast(t('请先填写课程名称。'), 'bad'); return; }
    const body = { name: name };
    const code = (form.querySelector('input[name="code"]').value || '').trim();
    if (code) body.code = code;
    const language = form.querySelector('select[name="language"]').value;
    if (language) body.language = language;
    button.disabled = true;
    try {
      const dto = await api('/courses', { method: 'POST', body: body });
      toast(t('课程已创建: ') + (dto.name || dto.course_id), 'ok');
      // route() **必须 await**: 它重建 #view, 不 await 的话 finally 里
      // button.disabled 落在已经脱离文档的旧节点上, 而新表单已经就位 ——
      // 看起来没事, 但下一次点击会操作到错误的那个按钮。
      await route();
    } catch (err) {
      toast(t('创建失败 [') + err.code + '] ' + err.message, 'bad');
    } finally {
      button.disabled = false;
    }
  });
}

function wireSessionForm() {
  const form = document.getElementById('session-form');
  if (!form) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const raw = (form.querySelector('input[name="session_number"]').value || '').trim();
    if (raw === '') { toast(t('请先填写课号。'), 'bad'); return; }
    const body = {
      course_id: form.getAttribute('data-course'),
      session_number: Number(raw),
    };
    const title = (form.querySelector('input[name="title"]').value || '').trim();
    const date = (form.querySelector('input[name="date"]').value || '').trim();
    if (title) body.title = title;
    if (date) body.date = date;
    button.disabled = true;
    try {
      const dto = await api('/sessions', { method: 'POST', body: body });
      toast(t('课堂已创建: ') + sessionLabel(dto, t('(无标题)')), 'ok');
      await route();
    } catch (err) {
      toast(t('创建失败 [') + err.code + '] ' + err.message, 'bad');
    } finally {
      button.disabled = false;
    }
  });
}

// ---- Guia docent (课程 metadata 的 ``guia_*`` 键) ------------------------
//
// 数据来源是导入脚本写进课程 metadata 的结构化字段 (见
// scripts/dev-archive/import_guia_geo_metadata.py), 经
// ``GET /api/courses/{id}/workspace`` 的 ``metadata`` 原样透传到前端。
//
// 三条约束:
//
// 1. **原文原样渲染** —— guia docent 是加泰罗尼亚语原文, 只做界面标题的翻译,
//    内容一个字节不改写不翻译 (README: "原文一律按原样显示");
// 2. **没有 guia_* 键就不渲染卡片** —— 其余课程 (还没有导入 guia docent)
//    的课程页必须和改动前一模一样, 零视觉影响;
// 3. 所有内容都过 ``esc()`` —— 它来自 API 的 metadata, 与夹具数据同级别的
//    不可信输入。

/** metadata 里是否有任何 ``guia_*`` 键 (决定 guia docent 卡片是否渲染)。 */
function hasGuia(metadata) {
  return Object.keys(metadata || {}).some((key) =>
    key.indexOf('guia_') === 0);
}

/** 列表项 ``<li>`` (跳过空值; 内容逐字渲染, 过 esc() 转义)。 */
function guiaList(items) {
  const list = (items || []).filter((item) => item !== '' && item !== null && item !== undefined);
  if (!list.length) return '';
  return '<ul class="guia-list">' + list.map((item) => '<li>' + esc(item) + '</li>').join('') + '</ul>';
}

/** 一段小节: 小标题 + 正文 (paragraph / list 皆可)。 */
function guiaSection(title, bodyHtml) {
  if (!bodyHtml) return '';
  return '<h3 class="guia-section-title">' + esc(title) + '</h3>' + bodyHtml;
}

/** 评估活动表 (题名 + 权重 + 依据的 guia 行号)。缺权重时显示 — 而不是猜。 */
function guiaAssessmentTable(items, caption) {
  const rows = (items || []).map((item) => (
    '<tr><td>' + esc(item.title || '') + '</td>' +
    '<td class="num">' + dash(item.weight !== undefined && item.weight !== null && item.weight !== '' ? item.weight : null) + '</td></tr>'
  )).join('');
  if (!rows) return '';
  return '<table class="data">' + tableCaption(caption) +
    '<thead><tr><th scope="col">' + esc(t('guia.activity')) + '</th>' +
    '<th scope="col" class="num">' + esc(t('guia.weight')) + ' (%)</th></tr></thead>' +
    '<tbody>' + rows + '</tbody></table>';
}

/**
 * guia docent 卡片 (页头下方, **默认折叠**)。
 *
 * 用户反馈: 全部展开太占空间 (卡片高 3696px)。改为 ``<details>`` 默认收起,
 * summary 行显示标题 + 学年; 正文全部藏进去, 点 summary 或页头的
 * 「查看详情」按钮展开。数据不变, 只是默认不铺开。
 */
function guiaDocentCard(metadata) {
  if (!hasGuia(metadata)) return '';
  const g = metadata;

  // 基本信息: 学分 / 学年 / 学位 / 年级 / 课程代码 (代码可能等于课程 code,
  // 仍按原样展示 —— 两个来源不一致时各显各的, 不替用户挑一个)。
  const facts = [
    g.guia_credits ? '<span><dt>' + esc(t('guia.credits')) + '</dt><dd>' + esc(g.guia_credits) + '</dd></span>' : '',
    g.guia_academic_year ? '<span><dt>' + esc(t('guia.academicYear')) + '</dt><dd>' + esc(g.guia_academic_year) + '</dd></span>' : '',
    g.guia_degree ? '<span><dt>' + esc(t('guia.degree')) + '</dt><dd>' + esc(g.guia_degree) + '</dd></span>' : '',
    g.guia_year ? '<span><dt>' + esc(t('guia.year')) + '</dt><dd>' + esc(g.guia_year) + '</dd></span>' : '',
    g.guia_course_code ? '<span><dt>' + esc(t('guia.courseCode')) + '</dt><dd>' + esc(g.guia_course_code) + '</dd></span>' : '',
  ].filter(Boolean).join('');

  const contact = (g.guia_contact_name || g.guia_contact_email)
    ? '<p class="small">' +
      (g.guia_contact_name
        ? '<strong>' + esc(g.guia_contact_name) + '</strong>' : '') +
      (g.guia_contact_email
        ? (g.guia_contact_name ? ' · ' : '') +
          '<a href="mailto:' + esc(g.guia_contact_email) + '">' + esc(g.guia_contact_email) + '</a>' : '') +
      '</p>' : '';

  const team = guiaList(g.guia_teaching_team);
  const outcomes = guiaList((g.guia_learning_outcomes || []).map((o) =>
    (o && o.code ? o.code + ' — ' : '') + (o && o.text ? o.text : '')));
  const blocks = guiaList((g.guia_syllabus || []).map((b) =>
    (b && b.title ? b.title : '') + (b && b.items && b.items.length
      ? '<ul class="guia-list">' + b.items.map((it) => '<li>' + esc(it) + '</li>').join('') + '</ul>' : '')));
  const hours = (g.guia_teaching_hours || []).length
    ? guiaList(g.guia_teaching_hours.map((h) =>
        (h.title || '') + ' — ' + (h.hours !== undefined ? h.hours : '—') + ' h / ' +
        (h.ects !== undefined ? h.ects : '—') + ' ECTS')) : '';

  return '<div class="card" id="guia-docent">' +
    '<details class="guia-fold">' +
    '<summary><span class="guia-fold-title">' + t('guia.title') + '</span>' +
    (g.guia_academic_year ? '<span class="tiny muted">' + esc(g.guia_academic_year) + '</span>' : '') +
    '<span class="guia-fold-hint">' + t('guia.foldHint') + '</span>' +
    '</summary>' +
    (facts ? '<dl class="kv guia-facts">' + facts + '</dl>' : '') +
    contact +
    guiaSection(t('guia.team'), team) +
    guiaSection(t('guia.prerequisites'), g.guia_prerequisites ? '<p class="small">' + esc(g.guia_prerequisites) + '</p>' : '') +
    guiaSection(t('guia.objectives'), guiaList(g.guia_objectives)) +
    guiaSection(t('guia.outcomes'), outcomes) +
    guiaSection(t('guia.syllabus'), blocks) +
    (g.guia_syllabus_note ? '<p class="tiny muted">' + esc(g.guia_syllabus_note) + '</p>' : '') +
    guiaSection(t('guia.teachingHours'), hours) +
    guiaSection(t('guia.assessment'),
      guiaAssessmentTable(g.guia_assessment_items, t('guia.assessment'))) +
    (g.guia_assessment_note ? '<p class="tiny muted">' + esc(g.guia_assessment_note) + '</p>' : '') +
    guiaSection(t('guia.assessmentItems'), guiaList(g.guia_assessment_items_detail)) +
    guiaSection(t('guia.passRequirements'), g.guia_pass_requirements ? '<p class="small">' + esc(g.guia_pass_requirements) + '</p>' : '') +
    guiaSection(t('guia.recovery'), g.guia_recovery ? '<p class="small">' + esc(g.guia_recovery) + '</p>' : '') +
    guiaSection(t('guia.aiPolicy'), g.guia_ai_policy ? '<p class="small">' + esc(g.guia_ai_policy) + '</p>' : '') +
    guiaSection(t('guia.software'), g.guia_software ? '<p class="small">' + esc(g.guia_software) + '</p>' : '') +
    (g.guia_groups && g.guia_groups.length
      ? guiaSection(t('guia.groups'),
          '<table class="data">' + tableCaption(t('guia.groups')) + '<thead><tr>' +
          '<th scope="col">' + esc(t('guia.groupKind')) + '</th>' +
          '<th scope="col" class="num">' + esc(t('guia.group')) + '</th>' +
          '<th scope="col">' + esc(t('course.language')) + '</th>' +
          '<th scope="col">' + esc(t('course.semester')) + '</th>' +
          '<th scope="col">' + esc(t('guia.shift')) + '</th>' +
          '</tr></thead><tbody>' +
          g.guia_groups.map((row) => (
            '<tr><td>' + esc(row.kind || '') + '</td>' +
            '<td class="num">' + esc(row.group || '') + '</td>' +
            '<td>' + esc(row.language || '') + '</td>' +
            '<td>' + esc(row.semester || '') + '</td>' +
            '<td>' + esc(row.shift || '') + '</td></tr>'
          )).join('') + '</tbody></table>')
      : '') +
    (g.guia_source ? '<p class="tiny muted guia-source">' + esc(g.guia_source) + '</p>' : '') +
    '</details>' +
    '</div>';
}

/**
 * 页头的 guia docent **精简摘要** (替代教师/学期/课程语言三行)。
 *
 * 只放三样最有用的: 学分 + 学年 + 学位 (一行) / 联系人与教学团队 (一行) /
 * 「查看详情」按钮 (展开下面的折叠卡片并滚过去)。课程语言不重复显示 ——
 * 副标题 courseIdentity 已经有 code · language。
 */
function guiaBrief(metadata) {
  if (!hasGuia(metadata)) return '';
  const g = metadata;
  const mainParts = [];
  if (g.guia_credits) mainParts.push(esc(g.guia_credits) + ' ECTS');
  if (g.guia_academic_year) mainParts.push(esc(g.guia_academic_year));
  if (g.guia_degree) {
    const suffix = [g.guia_degree_type, g.guia_year]
      .filter((part) => part !== undefined && part !== null && part !== '')
      .map(esc).join(' · ');
    mainParts.push(esc(g.guia_degree) + (suffix ? ' (' + suffix + ')' : ''));
  }
  const contact = g.guia_contact_name
    ? (g.guia_contact_email
      ? '<a href="mailto:' + esc(g.guia_contact_email) + '">' + esc(g.guia_contact_name) + '</a>'
      : esc(g.guia_contact_name))
    : '';
  const peopleParts = [contact]
    .concat(g.guia_teaching_team || [])
    .filter((part) => part !== '' && part !== null && part !== undefined)
    .map(esc)
    .join(' · ');
  return '<div class="guia-brief">' +
    (mainParts.length ? '<p class="guia-brief-main"><b>' + mainParts.join('</b> · <b>') + '</b></p>' : '') +
    (peopleParts ? '<p class="guia-brief-people small">' + peopleParts + '</p>' : '') +
    '<button type="button" class="small" data-action="open-guia">' +
    t('guia.details') + ' ↓</button>' +
    '</div>';
}

/** 「查看详情」按钮: 展开折叠卡片并滚过去 (summary 本身也可以点, 这是快捷键)。 */
function wireGuiaBrief() {
  const button = document.querySelector('[data-action="open-guia"]');
  const details = document.querySelector('#guia-docent details');
  if (!button || !details) return;
  button.addEventListener('click', () => {
    details.open = true;
    details.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}

// ---- 课程页 (Task 56.1) --------------------------------------------------

async function pageCourse(courseId) {
  setRouteCourse(courseId);
  markActiveNav('');
  const data = await api('/courses/' + encodeURIComponent(courseId) + '/workspace');
  const course = data.course || {};
  const counts = data.counts || {};
  const coverage = data.coverage || {};
  const sessions = data.sessions || [];
  const points = (data.knowledge || {});

  const stat = (label, value, hint) =>
    '<div class="stat"><div class="stat-label">' + esc(label) + '</div>' +
    '<div class="stat-value">' + esc(value) + '</div>' +
    (hint ? '<div class="stat-hint">' + esc(hint) + '</div>' : '') + '</div>';

  const kv = (label, value) =>
    '<dt>' + esc(label) + '</dt><dd>' + esc(value) + '</dd>';

  setView(
    '<div class="page-head"><div class="crumbs"><a href="#/">' + t('概览') + '</a>' + t(' / 课程') + '</div>' +
    '<h1>' + esc(course.name || course.code || '') + '</h1>' +
    '<p class="subtitle mono small">' + esc(courseIdentity(course)) + '</p>' +
    // 有 guia docent 时, 页头三行 (教师/学期/课程语言) 换成 guia 精简摘要 ——
    // 教师/联系人本来就该来自 guia docent, 而且摘要有「查看详情」入口。
    // 没有 guia 数据的课程保持原三行不变。
    (hasGuia(data.metadata)
      ? guiaBrief(data.metadata)
      : '<dl class="kv">' +
        kv(t('course.teacher'), data.teacher || '—') +
        kv(t('course.semester'), data.semester || '—') +
        kv(t('course.language'), course.language || '—') +
        '</dl>') +
    '</div>' +

    '<div class="grid grid-4" style="margin-bottom:16px">' +
    stat(t('course.sessions'), counts.sessions || 0) +
    stat(t('材料'), counts.materials || 0) +
    stat(t('course.knowledge'), counts.knowledge_points || 0) +
    stat(t('common.evidence'), counts.evidence || 0) +
    stat(t('course.pendingReview'), counts.pending_review || 0) +
    stat(t('course.coverage'), coverage.coverage_ratio !== undefined
      ? coverage.coverage_ratio : '—') +
    '</div>' +

    '<div class="card"><div class="card-head"><h2>' + t('course.sessions') + '</h2>' +
    '<span class="small muted">' + t('点击进入课堂页，可整堂处理') + '</span></div>' +
    (sessions.length
      ? '<table class="data">' + tableCaption(t('course.sessions')) + '<thead><tr><th scope="col">#</th><th scope="col">' + t('标题') + '</th><th scope="col">' + t('日期') +
        '</th><th scope="col">' + t('common.status') + '</th><th scope="col" class="num">' + t('材料') +
        '</th><th scope="col" class="num">' + t('course.knowledge') + '</th><th scope="col" class="num">' +
        t('course.pendingReview') + '</th><th scope="col">' + t('common.actions') + '</th></tr></thead><tbody>' +
        sessions.map((s) => {
          const c = s.counts || {};
          return '<tr><td class="num">' + esc(s.session_number) + '</td>' +
            '<td><a href="#/courses/' + encodeURIComponent(courseId) + '/sessions/' +
            encodeURIComponent(s.session_id) + '">' + esc(s.title || t('(无标题)')) + '</a></td>' +
            '<td class="small">' + dash(s.date) + '</td>' +
            '<td>' + pill(sessionStatusLabel(s.status), s.status) + '</td>' +
            '<td class="num">' + esc(c.materials || 0) + '</td>' +
            '<td class="num">' + esc(c.knowledge_points || 0) + '</td>' +
            '<td class="num">' + esc(c.pending_review || 0) + '</td>' +
            '<td><button data-action="process-session" data-course="' + esc(courseId) +
            '" data-session="' + esc(s.session_id) + '">' + t('整堂处理') + '</button></td></tr>';
        }).join('') + '</tbody></table>'
      : emptyState(t('还没有课堂。'))) +
    // 创建入口**永远**渲染 (空态下更是必须的) —— 见本节顶部第 1 条约束。
    '<div class="block-label">' + t('新建课堂') + '</div>' +
    sessionForm(courseId) +
    '</div>' +

    '<div class="grid grid-2">' +
    '<div class="card"><div class="card-head"><h2>' + t('course.recentMaterials') + '</h2>' +
    '<a class="small" href="#/materials">' + t('全部 →') + '</a></div>' +
    ((data.recent_materials || []).length
      ? '<table class="data">' + tableCaption(t('course.recentMaterials')) + '<thead><tr><th scope="col">' + t('文件') + '</th><th scope="col">' + t('类型') +
        '</th><th scope="col">' + t('common.status') + '</th><th scope="col" class="num">' + t('大小') +
        '</th></tr></thead><tbody>' +
        data.recent_materials.map((m) => (
          '<tr><td class="break-all">' + esc(m.filename) + '</td>' +
          '<td>' + esc(m.material_type || m.source_type || '—') + '</td>' +
          '<td>' + pill(m.processing_status) + '</td>' +
          '<td class="num">' + fmtBytes(m.size) + '</td></tr>'
        )).join('') + '</tbody></table>'
      : emptyState(t('该课程还没有材料。'))) +
    '</div>' +

    '<div class="card"><div class="card-head"><h2>' + t('course.pendingReview') + '</h2>' +
    '<a class="small" href="#/reviews">' + t('全部 →') + '</a></div>' +
    ((data.pending_review || []).length
      ? '<ul class="small">' + data.pending_review.slice(0, 12).map((item) => (
          '<li><a href="#/courses/' + encodeURIComponent(courseId) + '/knowledge/' +
          encodeURIComponent(item.knowledge_point_id) + '">' +
          esc(item.knowledge_point_id) + '</a> ' + pill(item.validation_status) + '</li>'
        )).join('') + '</ul>'
      : emptyState(t('没有待审核的知识点。'))) +
    '</div>' +
    '</div>' +

    '<div class="card"><h2>' + t('course.coverage') + '</h2>' +
    '<dl class="kv">' +
    kv(t('知识点'), points.count || 0) +
    // 两条真相轴的标签统一走 val.* / rev.* —— 与「我的课程」页的
    // mcAxisLabels() 同一个词汇表。这里**逐个字面**写 key, 不拼前缀:
    // 拼错前缀时 t() 会原样返回 key, 页面上直接出现 "val.supported",
    // 没有异常、没有日志、静态检查也抓不到 (理由同 dashboard.js:405-418)。
    kv(t('val.supported'), (points.by_validation_status || {}).supported || 0) +
    kv(t('val.unverified'), (points.by_validation_status || {}).unverified || 0) +
    kv(t('val.conflicted'), (points.by_validation_status || {}).conflicted || 0) +
    kv(t('rev.pending'), (points.by_review_status || {}).pending || 0) +
    '</dl>' +
    (((data.gaps || {}).gaps || []).length
      ? '<ul class="small">' + data.gaps.gaps.map((gap) => (
          '<li>' + pill(gap.gap_type || gap.type || 'GAP', 'PENDING') + ' ' +
          esc(gap.description || gap.knowledge_point_id || '') + '</li>'
        )).join('') + '</ul>'
      : emptyState(t('未检测到缺口。'))) +
    '</div>' +

    guiaDocentCard(data.metadata || {})
  );
  wireSessionForm();
  wireGuiaBrief();
}

// ---- 课堂页 (Task 56.2 / 56.3) -------------------------------------------

/** 时间线条目类型 → 人话。
 *
 * 取值域来自后端 ``classroom_view.resolve_timeline()``:
 * ``evidence:transcript|ocr|document|note|other`` 与 ``knowledge``。
 * 未知分组退回 ``session.evidence`` —— 绝不把原始枚举名甩给用户。
 */
function timelineTypeLabel(itemType) {
  const raw = String(itemType || '');
  if (raw === 'knowledge') return t('common.knowledgePoint');
  const group = raw.indexOf(':') >= 0 ? raw.split(':').pop() : 'other';
  if (group === 'transcript') return t('session.transcript');
  if (group === 'ocr') return t('session.ocr');
  if (group === 'document') return t('session.documents');
  if (group === 'note') return t('session.notes');
  return t('session.evidence');
}

/**
 * 课堂时间线 (Task 58, ``GET /api/sessions/{id}/timeline``)。
 *
 * 后端把一节课的**证据与知识点按时间顺序**投影成一条线。前端只如实显示:
 * 不重排、不去重、不把"有时间戳"当成"更可信"。没有时间戳的条目排在最后,
 * 那是后端 ``_timeline_sort_key`` 的既定顺序 (未知时间戳的 sort key 为 1),
 * 不是前端重排的结果。
 *
 * 这里刻意做成**紧凑索引** —— 一行一条, 只放标题与来源 id。完整原文与
 * 来源材料在下面的证据分区里, 不在这里重复渲染第二遍。
 *
 * ``payload.error`` 时显式报错而不是渲染成空表: 时间线是派生视图, 它挂了
 * 不影响本页其余事实, 但"看不见"和"没有"必须能分辨。
 */
function timelineCard(courseId, payload) {
  const items = (payload && payload.items) || [];
  const head = '<div class="card-head"><h2>' + t('时间线') + ' (' +
    esc(items.length) + ')</h2></div>';
  if (payload && payload.error) {
    return '<div class="card">' + head +
      '<p><span class="pill pill-bad">' + esc(payload.error.code) + '</span> ' +
      '<span class="small">' + esc(payload.error.message) + '</span></p>' +
      '<p class="small muted">' + esc(t('时间线加载失败；本页其余内容不受影响。')) + '</p></div>';
  }
  return '<div class="card">' + head +
    '<p class="small muted">' +
    esc(t('按时间顺序并列展示证据与知识点；时间戳直接来自证据本身，没有时间戳的条目排在最后。')) +
    '</p>' +
    (items.length
      ? '<table class="data">' + tableCaption(t('时间线')) + '<thead><tr>' +
        '<th scope="col" class="num">' + t('时间') + '</th>' +
        '<th scope="col">' + t('类型') + '</th>' +
        '<th scope="col">' + t('内容') + '</th>' +
        '</tr></thead><tbody>' +
        items.map((item) => {
          const unknown = item.timestamp === null || item.timestamp === undefined;
          const isKnowledge = item.item_type === 'knowledge';
          const title = isKnowledge
            ? '<a href="#/courses/' + encodeURIComponent(courseId) + '/knowledge/' +
              encodeURIComponent(item.source_id) + '">' + esc(item.title) + '</a>'
            : esc(item.title);
          return '<tr>' +
            // 时间戳**原样**渲染 (不四舍五入、不补零) —— 与下面证据分区里
            // sourceLocation() 的写法一致: 同一个证据在页面上只能有一个数值。
            // 后端产生时间戳时已经做过取整 (见 evidence_store._canonical_float
            // 的说明), 所以这里不会出现浮点尾巴。
            '<td class="num mono tiny">' +
              esc(unknown ? t('ws.timeUnavailable') : item.timestamp) + '</td>' +
            '<td class="small">' + esc(timelineTypeLabel(item.item_type)) + '</td>' +
            '<td>' + title +
              (item.source_id
                ? '<br><span class="tiny muted mono">' + esc(item.source_id) + '</span>'
                : '') + '</td>' +
            '</tr>';
        }).join('') + '</tbody></table>'
      : emptyState(t('这节课还没有可展示的时间线条目。'))) +
    '</div>';
}

async function pageSession(courseId, sessionId) {
  markActiveNav('');
  setRouteCourse(courseId);
  // 时间线是**独立端点** (Task 58), 与 workspace 互不依赖 —— 并行取, 不排队。
  // 它失败只让那一张卡片显式报错: 时间线是派生视图, 不影响本页其余事实。
  const [data, timeline] = await Promise.all([
    api('/courses/' + encodeURIComponent(courseId) +
      '/sessions/' + encodeURIComponent(sessionId) + '/workspace'),
    api('/sessions/' + encodeURIComponent(sessionId) + '/timeline')
      .then((payload) => ({ items: payload.timeline || [] }))
      .catch((err) => ({ items: [], error: err })),
  ]);
  const session = data.session || {};
  const overview = data.overview || {};
  const materials = data.materials || [];
  const jobs = (data.processing || {}).jobs || [];
  const points = (data.knowledge || {}).points || [];
  const review = data.review || {};
  const learning = data.learning || {};

  const stat = (label, value) =>
    '<div class="stat"><div class="stat-label">' + esc(label) + '</div>' +
    '<div class="stat-value">' + esc(value) + '</div></div>';

  const materialName = (id) => {
    const found = materials.filter((m) => m.material_id === id)[0];
    return found ? found.filename : id;
  };

  setView(
    '<div class="page-head"><div class="crumbs">' +
    '<a href="#/">' + t('概览') + '</a> / <a href="#/courses/' + encodeURIComponent(courseId) + '">' +
    esc(courseLabel(courseId)) + '</a>' + t(' / 课堂') + '</div>' +
    // 课堂标签只有一个生产者 (见 sessionLabel): 课号 + 标题, 没有标题就只显示
    // 课号 —— `第 3 堂` 本身已是完整信息, 再挂一个"(无标题)"是噪音。
    '<h1>' + esc(sessionLabel(session, t('(无标题)'))) + '</h1>' +
    // 副标题与课程页对齐 (课程页是 courseIdentity, 不是 course_id): 内容寻址的
    // session_id 对用户零信息量, 而日期是这个页面上唯一的日期来源 —— 留日期。
    '<p class="subtitle mono small">' + esc(session.date || '') + '</p>' +
    '<p class="row">' + pill(sessionStatusLabel(data.status), data.status) +
    ' <span class="small muted">' + esc(data.status_reason || '') + '</span></p>' +
    (data.status === 'READY_TO_STUDY'
      ? '<p class="small muted">' + esc(t('session.readyNotMastery')) + '</p>' : '') +
    '</div>' +

    '<div class="card"><div class="card-head"><h2>' + t('session.overview') + '</h2>' +
    '<button class="primary" data-action="process-session" data-course="' + esc(courseId) +
    '" data-session="' + esc(sessionId) + '">' + t('session.processAll') + '</button></div>' +
    '<div class="grid grid-4">' +
    stat(t('session.materials'), overview.materials || 0) +
    stat(t('session.evidence'), overview.evidence || 0) +
    stat(t('session.knowledge'), overview.knowledge_points || 0) +
    stat(t('session.review'), overview.pending_review || 0) +
    '</div>' +
    '<div class="block-label">' + t('处理步骤') + '</div>' +
    processingStepper((data.processing || {}).stages) +
    '<dl class="kv">' +
    '<dt>' + t('session.transcript') + '</dt><dd>' +
      esc((overview.evidence_by_group || {}).transcript || 0) + '</dd>' +
    '<dt>' + t('session.ocr') + '</dt><dd>' + esc((overview.evidence_by_group || {}).ocr || 0) + '</dd>' +
    '<dt>' + t('session.documents') + '</dt><dd>' +
      esc((overview.evidence_by_group || {}).document || 0) + '</dd>' +
    '<dt>' + t('session.notes') + '</dt><dd>' +
      esc((overview.evidence_by_group || {}).note || 0) + '</dd>' +
    '</dl>' +
    '<p class="small muted">' + t('处理是串行的；单个材料失败不会中断其余材料，失败项可单独重试。') + '</p>' +
    '</div>' +

    timelineCard(courseId, timeline) +

    '<div class="card"><div class="card-head"><h2>' + t('session.materials') + ' (' +
    esc(materials.length) + ')</h2></div>' +
    (materials.length
      ? '<table class="data">' + tableCaption(t('session.materials')) + '<thead><tr><th scope="col">' + t('文件') + '</th><th scope="col">' + t('类型') +
        '</th><th scope="col">' + t('common.status') + '</th><th scope="col" class="num">' + t('大小') +
        '</th><th scope="col" class="num">' + t('ws.evidenceCount') + '</th><th scope="col">' + t('common.actions') +
        '</th></tr></thead><tbody>' +
        materials.map((m) => {
          const failed = m.processing_status === 'FAILED' || (m.error && m.error !== '');
          const errBlock = (failed && m.error_category)
            ? '<div class="tiny muted">' + t('错误类型') + ': ' +
              esc(m.error_category) + '</div>' +
              (m.recommended_action
                ? '<div class="tiny">' + t('建议操作') + ': ' + esc(m.recommended_action) + '</div>'
                : '')
            : '';
          return '<tr><td class="break-all">' + esc(m.filename) +
            (m.error ? ' <span class="pill pill-bad">' + esc(m.error_category || m.error) + '</span>' : '') +
            errBlock + '</td>' +
            '<td>' + esc(m.material_type || '—') + '</td>' +
            '<td>' + pill(m.processing_status) + warningRow(m) + zeroEvidenceHint(m) + '</td>' +
            '<td class="num">' + fmtBytes(m.size) + '</td>' +
            '<td class="num">' + esc((m.evidence_ids || []).length) + '</td>' +
            '<td>' + (
              failed
                ? '<button data-action="retry-material" data-course="' + esc(courseId) +
                  '" data-material="' + esc(m.material_id) + '">' + t('重试') + '</button>'
                : '<button data-action="process-material" data-course="' + esc(courseId) +
                  '" data-material="' + esc(m.material_id) + '">' + t('处理') + '</button>'
            ) + '</td></tr>';
        }).join('') + '</tbody></table>'
      : emptyState(t('session.noMaterials'))) +
    '</div>' +

    '<div class="card"><div class="card-head"><h2>' + t('session.processing') + '</h2></div>' +
    (jobs.length
      ? '<table class="data">' + tableCaption(t('session.processing')) + '<thead><tr><th scope="col">' + t('文件') + '</th><th scope="col">' + t('阶段') +
        '</th><th scope="col">' + t('common.status') + '</th><th scope="col" class="num">' + t('尝试') +
        '</th><th scope="col">' + t('错误类型') + '</th><th scope="col">' + t('建议操作') + '</th></tr></thead><tbody>' +
        jobs.map((job) => (
          '<tr><td class="break-all small">' + esc(materialName(job.material_id)) + '</td>' +
          '<td>' + pill(job.stage, job.status) + '</td>' +
          '<td>' + pill(job.status) + '</td>' +
          '<td class="num">' + esc(job.attempts) + '/' + esc(job.max_attempts) + '</td>' +
          '<td class="small">' +
            (job.error ? esc(job.error_category || job.error) : '—') + '</td>' +
          '<td class="small">' +
            (job.recommended_action ? esc(job.recommended_action) : '—') + '</td></tr>'
        )).join('') + '</tbody></table>'
      : emptyState(t('还没有处理任务。上传材料后点击"处理"。'))) +
    '</div>' +

    evidenceCard(t('session.transcript'), data.transcript, t('session.noTranscript'), materials) +
    evidenceCard(t('session.ocr'), data.ocr, t('session.noOcr'), materials) +
    evidenceCard(t('session.documents'), data.documents, t('session.noDocuments'), materials) +

    '<div class="card"><div class="card-head"><h2>' + t('session.evidence') + ' (' +
    esc((data.evidence || []).length) + ')</h2></div>' +
    ((data.evidence || []).length
      ? evidenceList(data.evidence, materials)
      : emptyState(t('session.noEvidence'))) +
    '</div>' +

    '<div class="card"><div class="card-head"><h2>' + t('session.knowledge') + ' (' +
    esc(points.length) + ')</h2>' +
    '<a class="small" href="#/knowledge">' + t('全部 →') + '</a></div>' +
    (points.length
      ? '<table class="data">' + tableCaption(t('session.knowledge')) + '<thead><tr><th scope="col">' + t('标题') + '</th><th scope="col">' + t('验证') +
        '</th><th scope="col">' + t('审核') + '</th><th scope="col" class="num">' + t('common.score') +
        '</th><th scope="col" class="num">' + t('common.evidence') + '</th></tr></thead><tbody>' +
        points.map((kp) => (
          '<tr><td><a href="#/courses/' + encodeURIComponent(courseId) + '/knowledge/' +
          encodeURIComponent(kp.knowledge_id) + '">' + esc(kp.title) + '</a></td>' +
          '<td>' + pill(kp.validation_status) + '</td>' +
          '<td>' + pill(kp.review_status) + '</td>' +
          '<td class="num">' + fmtNumber(kp.knowledge_score) + '</td>' +
          '<td class="num">' + esc((kp.evidence_refs || []).length) + '</td></tr>'
        )).join('') + '</tbody></table>'
      : emptyState(t('session.noKnowledge'))) +
    '</div>' +

    '<div class="card"><div class="card-head"><h2>' + t('session.review') + ' (' +
    esc(review.pending_count || 0) + ')</h2>' +
    '<a class="small" href="#/reviews">' + t('全部 →') + '</a></div>' +
    ((review.pending || []).length
      ? '<table class="data">' + tableCaption(t('session.review')) + '<thead><tr><th scope="col">' + t('common.knowledgePoint') + '</th><th scope="col">' +
        t('验证') + '</th><th scope="col">' + t('common.score') + '</th><th scope="col" class="num">' +
        t('common.evidence') + '</th><th scope="col">' + t('冲突') + '</th></tr></thead><tbody>' +
        review.pending.map((item) => (
          '<tr><td><a href="#/courses/' + encodeURIComponent(courseId) + '/knowledge/' +
          encodeURIComponent(item.knowledge_point_id) + '">' +
          esc(item.knowledge_point_id) + '</a></td>' +
          '<td>' + pill(item.validation_status) + '</td>' +
          '<td class="num">' + fmtNumber(item.knowledge_score) + '</td>' +
          '<td class="num">' + esc((item.supporting_evidence_ids || []).length) + '</td>' +
          '<td>' + ((item.conflict_ids || []).length
            ? pill((item.conflict_ids || []).length + '', 'CONFLICTED') : '—') + '</td></tr>'
        )).join('') + '</tbody></table>'
      : emptyState(t('session.noReview'))) +
    '</div>' +

    '<div class="card"><div class="card-head"><h2>' + t('session.learning') + '</h2>' +
    (learning.student_id
      ? '<a class="small" href="#/courses/' + encodeURIComponent(courseId) + '/students/' +
        encodeURIComponent(learning.student_id) + '">' + esc(learning.display_name || learning.student_id) +
        ' →</a>'
      : '') + '</div>' +
    (learning.available
      ? '<dl class="kv">' +
        '<dt>' + t('学生') + '</dt><dd>' + esc(learning.display_name || learning.student_id) + '</dd>' +
        '<dt>' + t('student.registered') + '</dt><dd>' + esc((learning.states || []).length) + '</dd>' +
        '<dt>' + t('student.pending') + '</dt><dd>' +
          esc((learning.pending_exercises || []).length) + '</dd>' +
        '</dl>' +
        ((learning.states || []).length
          ? '<ul class="small">' + learning.states.map((s) => (
              '<li><a href="#/courses/' + encodeURIComponent(courseId) + '/knowledge/' +
              encodeURIComponent(s.knowledge_point_id) + '">' +
              esc(s.knowledge_point_id) + '</a> ' + pill(stateLabel(s.state), s.state) + '</li>'
            )).join('') + '</ul>'
          : '<p class="muted small">' + esc(t('student.noStates')) + '</p>')
      : '<p class="muted small">' + esc(learning.note || t('today.noStudy')) + '</p>') +
    '</div>'
  );
}

// ---- 知识点详情 + 溯源链 (核心 UX) ---------------------------------------
