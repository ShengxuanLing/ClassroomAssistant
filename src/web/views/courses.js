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
// 1. 表单**永远渲染** —— 空态下更是必须的, 否则第一门课永远建不出来;
// 2. 空值**不发送** —— 后端把字段缺省当"没给", 把 ``''`` 当"给了个空值",
//    两者在幂等键上不是一回事;
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
 * 「查看详情」按钮打开。数据不变, 只是装进弹窗。
 */
function guiaDocentModal(metadata) {
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
  // Bloc 列表是「标题 + 可选嵌套条目」的两层结构, 不能把拼好的 <ul> 塞进
  // guiaList (它会把整个字符串 esc 成可见文本 —— 双重转义)。这里直接构建,
  // 每个动态片段单独转义; 嵌套 <ul> 继承 .guia-list ul 的样式。
  const blocks = (g.guia_syllabus || []).length
    ? '<ul class="guia-list">' +
      g.guia_syllabus.map((b) =>
        '<li>' + esc((b && b.title) || '') +
        (b && b.items && b.items.length
          ? '<ul>' + b.items.map((it) => '<li>' + esc(it) + '</li>').join('') + '</ul>'
          : '') +
        '</li>').join('') +
      '</ul>'
    : '';
  const hours = (g.guia_teaching_hours || []).length
    ? guiaList(g.guia_teaching_hours.map((h) =>
        (h.title || '') + ' — ' + (h.hours !== undefined ? h.hours : '—') + ' h / ' +
        (h.ects !== undefined ? h.ects : '—') + ' ECTS')) : '';

  return '<div class="modal-mask hidden" id="guia-docent" role="dialog" aria-modal="true" ' +
    'aria-label="' + esc(t('guia.title')) + '">' +
    '<div class="modal">' +
    '<div class="modal-head"><h2>' + t('guia.title') + '</h2>' +
    (g.guia_academic_year ? '<span class="tiny muted">' + esc(g.guia_academic_year) + '</span>' : '') +
    '<button type="button" class="modal-close" data-action="close-guia" aria-label="' +
    esc(t('common.close')) + '">\u00d7</button>' +
    '</div>' +
    '<div class="modal-body">' +
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
    guiaSection(t('guia.assessmentRequirements'),
      g.guia_assessment_requirements ? '<p class="small">' + esc(g.guia_assessment_requirements) + '</p>' : '') +
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
    (g.guia_groups_note ? '<p class="tiny muted">' + esc(g.guia_groups_note) + '</p>' : '') +
    (g.guia_source ? '<p class="tiny muted guia-source">' + esc(g.guia_source) + '</p>' : '') +
    '</div>' +
    '</div>' +
    '</div>';
}

/**
 * 页头的 guia docent **精简信息** (替代教师/学期/课程语言三行)。
 *
 * 用户要求「具体信息, 直白明确」: 做成带标签的字段行 (教师 / 教学团队 /
 * 学年 / 学分 / 学位), 每个值都是原文, 不再塞成一整段。联系人带 mailto
 * 链接 —— **先拼好 HTML, 整行不再重复转义**: 上一版把拼好的 ``<a>``
 * 又 esc() 一遍, 页面直接显示 ``<a href=...>`` 原文 (真实回归, 已修)。
 */
function guiaBrief(metadata) {
  if (!hasGuia(metadata)) return '';
  const g = metadata;
  const row = (label, valueHtml) =>
    valueHtml ? '<span><dt>' + esc(label) + '</dt><dd>' + valueHtml + '</dd></span>' : '';

  const contactHtml = g.guia_contact_email
    ? '<a href="mailto:' + esc(g.guia_contact_email) + '">' +
      esc(g.guia_contact_name || g.guia_contact_email) + '</a>'
    : esc(g.guia_contact_name || '');
  const teamHtml = (g.guia_teaching_team || []).map((name) => esc(name)).join(' · ');

  return '<dl class="kv guia-brief">' +
    row(t('course.teacher'), contactHtml) +
    row(t('guia.team'), teamHtml) +
    row(t('guia.academicYear'), esc(g.guia_academic_year || '')) +
    row(t('guia.credits'), g.guia_credits ? esc(g.guia_credits) + ' ECTS' : '') +
    row(t('guia.degree'), esc(g.guia_degree || '')) +
    '</dl>' +
    '<button type="button" class="small" data-action="open-guia">' +
    t('guia.details') + '</button>';
}

/**
 * 弹窗的开关与关闭。
 *
 * 四条真实接线: 「查看详情」打开; 右上角 × 关闭; 直接点在遮罩上关闭;
 * Esc 键关闭。处理器每次渲染都重新挂 —— route() 重绘 #view 后旧节点已
 * 脱离文档, 不清理的话按钮会越积越多。
 */
function wireGuiaBrief() {
  const open = document.querySelector('[data-action="open-guia"]');
  const modal = document.getElementById('guia-docent');
  if (!modal) return;
  const close = () => { modal.classList.add('hidden'); };
  if (open) {
    open.addEventListener('click', () => modal.classList.remove('hidden'));
  }
  const closeButton = modal.querySelector('[data-action="close-guia"]');
  if (closeButton) closeButton.addEventListener('click', close);
  modal.addEventListener('click', (event) => {
    if (event.target === modal) close();
  });
  modal.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') close();
  });
}

// ---- 课堂列表 (2026-09-22 两轮重设计: 8 列大表格 → 日期分组卡片 → 月度视图) --
//
// 第一轮: 旧版课堂列表是一张大表格 —— 一次画几十行, 每行 8 列 (# / 标题 /
// 日期 / 状态 / 材料 / 知识点 / 待审核 / 操作), 行与行没有视觉层次, 后台字段
// 挤在一起。用户反馈"更像数据库管理台, 不像课堂助手", 于是改成"日期分组 +
// 卡片", 层级是清楚了。
//
// 第二轮 (本版): 卡片版仍然**一学期一次性铺开** —— 28 节课排成一屏多高的长
// 列表, 用户要一直滚才找得到 12 月的课; 每张卡又各自顶着一圈留白。课表是
// **有明确日期范围**的数据, 不是无穷流: 该让用户自己选时间范围, 而不是靠
// 无限滚动去猜他要找哪一段。所以这一版做三件事:
//
//   1. **月份切换** (sessionMonthNav): 一屏只画一个月, 顶部一排月份按钮,
//      默认落在当前月 (当前月没课就落最近的有课月份);
//   2. **同一天合并成一张卡** (sessionDayCard): 一天两三节课不再各占一张卡,
//      行与行之间只用一条分隔线;
//   3. **每节课压成两行** (sessionRow): 时间 + 类型 / 教师 · 教室 + 材料状态,
//      按钮移到行的右侧 —— 与需求给的目标版式逐项对应。
//
// 信息层级 (自上而下):
//
//     28 节课堂 · 2026-09-09 → 2026-12-09      ← 整门课 (始终可见)
//     [ 9月 ] [ 10月 ] [ 11月 ] [ 12月 ]        ← 月份切换, 当前月高亮
//     2026年9月 · 8 节课堂                      ← 现在看的是哪一段
//     09/09 · 周三                              ← 日期分组 (沿用)
//     +----------------------------------------------+
//     | 15:00–17:00 [Teoria]             整堂处理     | ← 时间 + 类型 + 操作
//     | Dario Cottava · Aula Q1/1007  尚未添加材料    | ← 教师 · 教室 + 状态
//     +----------------------------------------------+
//     | 17:00–19:00 [Pràctiques]         整堂处理     |
//     | Dario Cottava · Aula Q1/0011  尚未添加材料    |
//     +----------------------------------------------+
//
// 功能不减: 整行链接进课堂详情 (href 与旧表格完全一致)、「整堂处理」按钮
// (data-action="process-session", app.js 的 confirmDestructive 接线不动)、
// 材料 / 知识点 / 待审核计数全部保留 —— 只是不再各占一行/一列。
//
// 月份完全由前端从 ``date`` 派生: 后端只给每个课堂一个 ``YYYY-MM-DD``,
// 没有"学期""月份"这类字段, 也不需要新增 (见下方 uiLocale / monthOrdinal)。
//
// 标题数据: ClassSession 只有 session_number / date / title 三个可写字段,
// 课表种子脚本把 "时间 类型 | 教师 | 教室" 整个编码进 title。parseSessionTitle
// 把它拆回结构化字段; 更重要的是它**绝不渲染技术占位符** —— 旧种子脚本曾在
// 教师位写过技术占位串 (转录课表图时教师栏不可见), 数据源已修 (脚本不再产出,
// 库内 28 条已由 temp/fix_session_titles.py 清洗), 这里再兜一层尚未迁移的旧库。

// 标题解析器与 ``SESSION_TITLE_PLACEHOLDER`` 已上移到 ``app.js``: 课程页与
// 今日页共享同一实现, 标题规则只有一份真源。

/**
 * 按 ``date`` 分组: 有日期的组按日期升序 (ISO 字符串比较 = 时间序),
 * 组内保持后端顺序 (list_sessions 已按 session_number 全序排列);
 * 没有日期的排最后单独一组, 且不画组头 —— 它们没有可分组的键。
 */
function sessionDayGroups(sessions) {
  const byDate = [];
  const indexOf = {};
  sessions.forEach((s) => {
    const date = String(s.date || '');
    const key = /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : '';
    if (!(key in indexOf)) {
      indexOf[key] = byDate.length;
      byDate.push({ date: key, items: [] });
    }
    byDate[indexOf[key]].items.push(s);
  });
  const dated = byDate.filter((group) => group.date)
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  return dated.concat(byDate.filter((group) => !group.date));
}

/**
 * 日期组头: ``09/09 · 周三``。
 *
 * 日期部分取原文 (ISO 切片, 与界面语言无关); 星期按界面语言走 Intl ——
 * 失败只丢星期, 绝不丢日期。用年月日构造**本地时区**日期:
 * ``new Date('2026-09-09')`` 是 UTC 午夜, 西半球会算成前一天的星期。
 */
function sessionDayLabel(date) {
  const short = String(date).slice(5).replace('-', '/');
  let weekday = '';
  try {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(date));
    if (m && typeof Intl !== 'undefined' && Intl.DateTimeFormat) {
      weekday = Intl.DateTimeFormat(uiLocale(), { weekday: 'short' })
        .format(new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
    }
  } catch (err) {
    weekday = '';
  }
  return short + (weekday ? ' · ' + weekday : '');
}

/** 顶部一行 summary: ``28 节课堂 · 2026-09-09 → 2026-12-09``。只有一个日期
 *  时不画箭头; 一个日期都没有时只报数量, 不留悬空的分隔符。 */
function sessionListSummary(sessions) {
  const dates = sessions
    .map((s) => String(s.date || ''))
    .filter((date) => /^\d{4}-\d{2}-\d{2}$/.test(date))
    .sort();
  let range = '';
  if (dates.length) {
    range = dates[0] === dates[dates.length - 1]
      ? dates[0]
      : dates[0] + ' → ' + dates[dates.length - 1];
  }
  return esc(String(sessions.length)) + esc(t(' 节课堂')) +
    (range ? ' · ' + esc(range) : '');
}

// ---- 月份导航 (2026-09-22) ------------------------------------------------
//
// 月份是**本地时区下的日历月**, 不是"学期"这类业务概念: 后端只给每个课堂一个
// ``YYYY-MM-DD``, 月份完全由前端从日期派生。因此这一段**不碰后端、不存库**,
// 只是把同一份 sessions 数组换个粒度分组。

/** 界面语言 → BCP 47 locale。日期/月份标签一律走 Intl: 手写月份名会在
 *  (setembre / septiembre) 这类同形不同词的月份上立刻出错。
 *  sessionDayLabel 也用这一个 (原先各写一份 locale 表)。 */
function uiLocale() {
  return { zh: 'zh-CN', es: 'es-ES', ca: 'ca-ES' }[state.lang] || 'zh-CN';
}

/** ``YYYY-MM-DD`` → ``YYYY-MM``。不是 ISO 日期就返回 '' —— 归不了月的课堂
 *  另有去处 (见 sessionMonthView 的"日期未定"), 这里**不猜**月份。 */
function sessionMonthKey(date) {
  const text = String(date || '');
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text.slice(0, 7) : '';
}

/** 今天的月份键。用**本地时区** —— ``toISOString()`` 是 UTC, 东八区在每月
 *  1 日的 08:00 之前会算成上个月 (与 sessionDayLabel 同一条教训)。 */
function currentMonthKey(now) {
  const date = now || new Date();
  const month = date.getMonth() + 1;
  return date.getFullYear() + '-' + (month < 10 ? '0' : '') + month;
}

/** ``YYYY-MM`` → 自 1970-01 起的月序号 (整数)。有了它, "隔了几个月"与
 *  "下一个月"都是普通整数运算, 不用管月末、闰年和跨年。非法值返回 NaN。 */
function monthOrdinal(month) {
  const m = /^(\d{4})-(\d{2})$/.exec(String(month || ''));
  if (!m) return NaN;
  const number = Number(m[2]);
  if (number < 1 || number > 12) return NaN;
  return Number(m[1]) * 12 + (number - 1);
}

/** monthOrdinal 的逆运算 (只用于 1970 年之后的月份, 与课表数据的取值域一致)。 */
function monthKeyOf(ordinal) {
  const year = Math.floor(ordinal / 12);
  const month = ordinal - year * 12 + 1;
  return year + '-' + (month < 10 ? '0' : '') + month;
}

/**
 * 按月份分组: ``[{ month: '2026-09', items: [...] }, ...]``, 月份升序。
 * 没有可用日期的课堂归到 ``month: ''`` 一组, 永远排在最后 —— 它们归不了月,
 * 但也**绝不隐藏** (见 sessionMonthView)。
 *
 * 组内保持后端顺序: ``list_sessions`` 已按 session_number 全序排列, 同一天里
 * 的先后就是课表顺序, 这里不再重排。
 */
function sessionMonthGroups(sessions) {
  const byMonth = [];
  const indexOf = {};
  sessions.forEach((s) => {
    const key = sessionMonthKey(s.date);
    if (!(key in indexOf)) {
      indexOf[key] = byMonth.length;
      byMonth.push({ month: key, items: [] });
    }
    byMonth[indexOf[key]].items.push(s);
  });
  const dated = byMonth.filter((group) => group.month)
    .sort((a, b) => (a.month < b.month ? -1 : a.month > b.month ? 1 : 0));
  return dated.concat(byMonth.filter((group) => !group.month));
}

/**
 * 可切换的月份清单: 从**第一个有课的月**逐月排到**最后一个有课的月**。
 *
 * 为什么不是"只列有课的月份": 需求要求"点 12 月而该课 12 月没有课时, 显示
 * '本月没有安排课堂'而不是空白页"。只列有课的月份时那条分支永远不可达, 而且
 * 跳过的月份会让用户以为课表漏了一段。首末月份来自真实数据, 中间的空月是
 * **课表的真实形状** (寒暑假、停课周), 不是编出来的。
 */
function sessionMonthTabs(groups) {
  const months = groups.map((group) => group.month).filter(Boolean);
  if (!months.length) return [];
  const first = monthOrdinal(months[0]);
  const last = monthOrdinal(months[months.length - 1]);
  const tabs = [];
  for (let i = first; i <= last; i += 1) tabs.push(monthKeyOf(i));
  return tabs;
}

//: 月份标签的 Intl options。中文要 "2026年9月" —— 用 ``month: 'numeric'`` 会
//: 得到 "2026/9" (Intl 把"年月都是数字"当成日期格式), 必须用 'short'。西语/
//: 加泰语反过来: 'short' 只有 "sept", 带年份要 'long' 才读得出月份名。
const SESSION_MONTH_LABELS = {
  zh: { year: 'numeric', month: 'short' },
  es: { year: 'numeric', month: 'long' },
  ca: { year: 'numeric', month: 'long' },
};

/**
 * 月份标签。``withYear === false`` 时只给月份 (切换条上的短标签)。
 *
 * Intl 不可用或抛异常时退化为 ``YYYY-MM`` —— 绝不抛、绝不猜, 也**不退回中文**:
 * es/ca 界面上出现中文就是漏译 (scripts/ui_audit.js 有一条专门扫这个)。
 */
function sessionMonthLabel(month, withYear) {
  const text = String(month || '');
  const m = /^(\d{4})-(\d{2})$/.exec(text);
  if (!m) return text;
  try {
    if (typeof Intl === 'undefined' || !Intl.DateTimeFormat) return text;
    const lang = SESSION_MONTH_LABELS[state.lang] ? state.lang : 'zh';
    const options = withYear === false ? { month: 'short' } : SESSION_MONTH_LABELS[lang];
    return new Intl.DateTimeFormat(uiLocale(), options)
      .format(new Date(Number(m[1]), Number(m[2]) - 1, 1));
  } catch (err) {
    return text;
  }
}

/**
 * 默认展示哪个月: 当前月有课就直接用它; 否则取**离今天最近**的有课月份, 同距
 * 时取更晚的那个 —— 学期还没开始时应该显示即将到来的月份, 而不是几个月前那个
 * 已经上完的月份。**不默认展开整个学期** (需求三)。
 */
function sessionDefaultMonth(months, todayKey) {
  if (!months.length) return '';
  const now = monthOrdinal(todayKey);
  let best = months[0];
  let bestGap = Math.abs(monthOrdinal(months[0]) - now);
  months.slice(1).forEach((month) => {
    const gap = Math.abs(monthOrdinal(month) - now);
    if (gap < bestGap || (gap === bestGap && monthOrdinal(month) > monthOrdinal(best))) {
      best = month;
      bestGap = gap;
    }
  });
  return best;
}

//: 课程页当前浏览的月份 (``{ course_id: 'YYYY-MM' }``)。
//:
//: **只活在内存里**: 月份是"我正在看课表的哪一段", 不是跨会话偏好。写
//: localStorage 会让用户下次打开时落在一个几周前的月份上, 而"打开课程页先看
//: 当前/最近的月份"才是默认预期 (需求三)。刷新即回到默认, 是可预期的。
let sessionMonthPicks = {};

/**
 * 决定画哪个月: 用户选过且**仍在可切换范围内**就用它, 否则按"当前月 / 离今天最近
 * 的月份"算默认 (需求三: 进来先看当前月, 不要默认铺开整学期)。
 *
 * 刻意**不写回**默认值: 默认月份每次渲染都按当时的数据与时钟重算 (今天跨月了就该
 * 显示新的当前月), 只有用户真的点了月份才记进 sessionMonthPicks —— 于是"哪些状态
 * 是用户设的"始终一目了然, 不会被一次渲染悄悄改写。
 *
 * 旧选择落空的处理同理: 课表删掉一整段之后, 画一个不存在的月份不如回到默认。
 */
function sessionMonthPlan(courseId, groups) {
  const tabs = sessionMonthTabs(groups);
  const picked = sessionMonthPicks[courseId];
  const selected = tabs.indexOf(picked) >= 0
    ? picked
    : sessionDefaultMonth(
      groups.map((group) => group.month).filter(Boolean), currentMonthKey());
  return { tabs: tabs, selected: selected };
}

/** 月份切换条: 当前月份高亮 (``aria-current`` —— 视觉与语义同一处取值, CSS
 *  直接选 ``[aria-current="true"]``, 不再另加一个"选中"类名去同步)。跨年时
 *  标签带上年份, 否则 12 月和 1 月看起来像同一个学期里的相邻两个月。 */
function sessionMonthNav(courseId, tabs, selected) {
  const multiYear = tabs.some((month) => month.slice(0, 4) !== tabs[0].slice(0, 4));
  return '<div class="session-months">' + tabs.map((month) => {
    const on = month === selected;
    return '<button type="button" class="session-month"' +
      ' data-action="session-month" data-course="' + esc(courseId) + '"' +
      ' data-month="' + esc(month) + '"' +
      (on ? ' aria-current="true"' : '') +
      ' title="' + esc(sessionMonthLabel(month, true)) + '">' +
      esc(sessionMonthLabel(month, multiYear)) + '</button>';
  }).join('') + '</div>';
}

/** 一堂课的一行 (2026-09-22 第二版: 两行, 按钮在右侧)。整行可点 (stretched
 *  link 见 styles.css), 「整堂处理」是兄弟节点 —— 按钮**不嵌在 <a> 里**,
 *  否则一次点击同时触发按钮与跳转。 */
function sessionRow(courseId, session) {
  const parsed = parseSessionTitle(session.title);
  const counts = session.counts || {};
  // 第一层的主标签: 课表种子里的类型 (Teoria / Pràctiques d'Aula) 优先; 手工建
  // 的课堂 (如 "Tema 3") 没有类型段, 退回整段标题。
  const primary = parsed.kind || parsed.head || '';
  // 第二层: 教师段 (mid) 与教室段 (room) 拼一行, 缺一段就只画另一段。
  // 教师不再单独占一行 —— 它和教室属于同一层信息。
  const who = [parsed.mid, parsed.room].filter(Boolean).join(' · ');
  const href = '#/courses/' + encodeURIComponent(courseId) +
    '/sessions/' + encodeURIComponent(session.session_id);
  const meta = [];
  if (counts.materials) {
    meta.push(esc(t('材料')) + ' ' + esc(String(counts.materials)));
  }
  if (counts.knowledge_points) {
    meta.push(esc(t('course.knowledge')) + ' ' + esc(String(counts.knowledge_points)));
  }
  if (counts.pending_review) {
    meta.push(esc(t('course.pendingReview')) + ' ' + esc(String(counts.pending_review)));
  }
  return '<div class="session-row">' +
    '<a class="session-row-main" href="' + href + '">' +
    '<div class="session-row-top">' +
    (parsed.time ? '<span class="session-time">' + esc(parsed.time) + '</span>' : '') +
    // 类型画成徽章 (一眼分辨 Teoria / Pràctiques); 自由标题没有类型可言, 就画
    // 成普通文本 —— 给 "Tema 3" 套一个类型徽章会假装它是课程类型。
    (parsed.kind
      ? '<span class="pill pill-accent session-kind">' + esc(parsed.kind) + '</span>'
      : '<span class="session-title">' + esc(primary || t('未命名课堂')) + '</span>') +
    '</div>' +
    '<div class="session-row-sub">' +
    (who ? '<span class="session-who">' + esc(who) + '</span>' : '') +
    pill(sessionStatusLabel(session.status), session.status) +
    '</div>' +
    // 计数只在非 0 时出现 —— "材料 0 · 知识点 0" 是噪音, 不是信息。
    (meta.length
      ? '<div class="session-row-meta tiny muted">' + meta.join(' · ') + '</div>'
      : '') +
    '</a>' +
    '<button type="button" class="session-row-action" data-action="process-session"' +
    ' data-course="' + esc(courseId) + '" data-session="' + esc(session.session_id) + '">' +
    t('整堂处理') + '</button>' +
    '</div>';
}

/**
 * 同一天的所有课堂合并成**一张卡**: 行与行之间只有一条分隔线。
 *
 * 这是页面高度的大头 —— 一天两三节课时, 原先的"一节一张卡"会把 3 张卡的上下
 * 留白与外框全叠起来。整行可点仍然成立: 拉伸锚点的定位祖先是 ``.session-row``
 * 而不是这张卡, 所以第 2 行的链接不会盖住第 1 行的命中区。
 *
 * 没有日期的课堂 (parseSessionTitle 也救不回日期) 由 ``日期未定`` 组头兜底:
 * 它们排在所选月份之后, 不加说明就会被读成"这个月的课"。
 */
function sessionDayCard(courseId, group) {
  const head = group.date ? sessionDayLabel(group.date) : t('日期未定');
  return '<section class="session-day">' +
    '<h3 class="session-day-head">' + esc(head) + '</h3>' +
    '<div class="session-day-card">' +
    group.items.map((s) => sessionRow(courseId, s)).join('') +
    '</div></section>';
}

/**
 * 课堂列表主体: 整门课的 summary → 月份切换条 → 当前月份 → 日期分组。
 *
 * 用户在任何时刻都能回答三个问题: 这门课一共多少节、我正在看哪个月、这个月
 * 多少节 —— 前两个由顶部两行给出, 第三个由当前月份那一行给出。
 */
function sessionMonthView(courseId, sessions) {
  const groups = sessionMonthGroups(sessions);
  const plan = sessionMonthPlan(courseId, groups);
  const ofMonth = (month) => {
    const found = groups.filter((group) => group.month === month)[0];
    return found ? found.items : [];
  };
  let out = '<p class="small muted session-summary">' + sessionListSummary(sessions) + '</p>';
  if (plan.tabs.length) out += sessionMonthNav(courseId, plan.tabs, plan.selected);
  if (plan.selected) {
    const items = ofMonth(plan.selected);
    out += '<p class="small muted session-month-note">' +
      esc(sessionMonthLabel(plan.selected, true)) + ' · ' +
      esc(String(items.length)) + esc(t(' 节课堂')) + '</p>';
    if (items.length) {
      out += sessionDayGroups(items).map((group) => sessionDayCard(courseId, group)).join('');
    } else {
      // 空月份 (寒暑假 / 停课周) 给明确文案, 不留白页 —— 组头同时回答"这是哪个月"。
      out += '<section class="session-day">' +
        '<h3 class="session-day-head">' + esc(sessionMonthLabel(plan.selected, true)) + '</h3>' +
        emptyState(t('本月没有安排课堂。')) +
        '</section>';
    }
  }
  // 没有日期的课堂归不了月: 它们**不随月份切换隐藏**, 而是永远画在末尾并带
  // 自己的组头。藏起来会让用户以为这些课不存在 (证据优先: 宁可不整齐, 不隐瞒)。
  const undated = groups.filter((group) => !group.month);
  if (undated.length) {
    out += sessionDayGroups(undated[0].items)
      .map((group) => sessionDayCard(courseId, group)).join('');
  }
  return out;
}

/**
 * 月份切换: 只改"看哪一段", 不发任何写请求 (与错题本的分组切换同一模式)。
 *
 * ``data-month`` 不是合法月份时直接忽略 —— 按钮是前端自己画出来的, 但
 * 委托处理器拿到的是 DOM 属性, 宁可什么都不做也不猜。
 */
async function actionSessionMonth(courseId, month) {
  const key = String(month || '');
  if (!/^\d{4}-\d{2}$/.test(key)) return;
  if (sessionMonthPicks[courseId] === key) return; // 点当前月份: 不必重画重取
  sessionMonthPicks[courseId] = key;
  await route();
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
    // 月份切换 + 日期分组 + 紧凑行 —— 结构与月份解析见本文件上方「课堂列表」
    // 一节。渲染收在 sessionMonthView() 里, 页面只决定"有课就画列表, 没课就给
    // 空态"; 卡片区不再是表格, 也不再一次性铺开整个学期。
    (sessions.length
      ? sessionMonthView(courseId, sessions)
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

    guiaDocentModal(data.metadata || {})
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
      ? '<span class="small">' + esc(learning.display_name || learning.student_id) + '</span>'
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
