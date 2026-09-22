/*
 * 错题本 (``#/mistakes``) 与错题详情 (``#/mistakes/<course>/<kp>``)。
 */
'use strict';

function mistakeActionHref(courseId, studentId, action) {
  const name = action.action;
  if (name === 'REVIEW_KNOWLEDGE') {
    return '#/courses/' + encodeURIComponent(courseId) + '/knowledge/' +
      encodeURIComponent(action.target);
  }
  if (name === 'VIEW_EVIDENCE') {
    return '#/courses/' + encodeURIComponent(courseId) + '/knowledge/' +
      encodeURIComponent(action.target);
  }
  if (name === 'PRACTICE_AGAIN') {
    return '#/courses/' + encodeURIComponent(courseId) + '/exercises/' +
      encodeURIComponent(action.target) + '/' + encodeURIComponent(studentId);
  }
  if (name === 'VIEW_PREREQUISITE') {
    return '#/courses/' + encodeURIComponent(courseId) + '/knowledge/' +
      encodeURIComponent(action.target);
  }
  return '';
}

/** 建议操作列表。没有依据的动作后端根本不会下发, 这里只负责显示。 */
function renderMistakeActions(courseId, studentId, actions) {
  if (!actions || !actions.length) {
    return '<p class="muted small">' + esc(t('mk.noActions')) + '</p>';
  }
  return '<ul class="small action-list">' + actions.map((action) => {
    const label = t('mk.action.' + action.action);
    const href = mistakeActionHref(courseId, studentId, action);
    return '<li>' +
      '<span class="pill pill-info">' + esc(label) + '</span> ' +
      (href ? '<a href="' + esc(href) + '">' + esc(action.target) + '</a>'
            : '<span class="mono tiny">' + esc(action.target) + '</span>') +
      '<br><span class="tiny muted">' + esc(t('mk.weakBasis')) + ': ' +
      esc(action.basis || '') + '</span></li>';
  }).join('') + '</ul>';
}

/** 错题表。列严格按 spec 65.2: Exercise / Question / Answer / Evaluation /
 *  Knowledge / Course / Topic。 */
function mistakeTable(courseId, rows, kpIndex) {
  return '<table class="data">' + tableCaption(t('mk.listTitle')) + '<thead><tr>' +
    '<th scope="col">' + esc(t('mk.exercise')) + '</th>' +
    '<th scope="col">' + esc(t('mk.question')) + '</th>' +
    '<th scope="col">' + esc(t('mk.answer')) + '</th>' +
    '<th scope="col">' + esc(t('mk.evaluation')) + '</th>' +
    '<th scope="col">' + esc(t('mk.knowledgeTitle')) + '</th>' +
    '<th scope="col">' + esc(t('mk.topic')) + '</th>' +
    '</tr></thead><tbody>' +
    rows.map((row) => {
      const kps = row.knowledge || [];
      return '<tr>' +
        '<td class="mono tiny">' + exerciseLink(courseId, row.exercise_id) +
          '<br><span class="pill pill-muted">' + esc(row.exercise_type) + '</span></td>' +
        '<td>' + exerciseLink(courseId, row.exercise_id, row.prompt) + '</td>' +
        '<td>' + esc(row.submitted_value) +
          '<br><span class="tiny muted mono">' + esc(row.answer_id) + '</span></td>' +
        '<td>' + pill(statusLabel(row.status), String(row.status).toUpperCase()) +
          ' <span class="tiny muted">' + esc(fmtNumber(row.score)) + '</span>' +
          '<br><span class="tiny muted mono">' + esc(row.evaluation_id) + '</span></td>' +
        '<td>' + (kps.length
          ? kps.map((kp) => kpLink(courseId, kp.knowledge_id, kp.title)).join('<br>')
          : esc(t('common.none'))) + '</td>' +
        '<td class="small muted">' + esc(t('mk.topic')) + ' ' +
          esc(kpIndex.topicOf(row.knowledge_point_ids[0]) || t('mk.unassigned')) + '</td>' +
        '</tr>';
    }).join('') + '</tbody></table>';
}

/** 从已有的 topic 分组里反查知识点所属主题（不额外发请求）。 */
function topicIndexOf(topicGroups) {
  const map = {};
  (topicGroups || []).forEach((group) => {
    (group.children || []).forEach((child) => {
      map[child.group_id] = group.unassigned ? t('mk.unassigned') : (group.title || group.group_id);
    });
  });
  return { topicOf: (kpId) => map[kpId] };
}

/** 分组渲染: knowledge 是两层, topic 是三层。 */
function renderMistakeGroups(courseId, groupBy, groups) {
  if (!groups || !groups.length) return emptyState(t('mk.emptyGroup'));
  if (groupBy === 'topic') {
    return groups.map((group) => {
      const title = group.unassigned ? t('mk.unassigned') : (group.title || group.group_id);
      return '<details class="mistake-group" open><summary>' +
        '<strong>' + esc(title) + '</strong> ' +
        '<span class="pill pill-warn">' + esc(t('mk.mistakeCount')) + ' ' +
        esc(group.incorrect_attempts) + '</span></summary>' +
        (group.children || []).map((child) =>
          '<div class="nested-group">' +
          '<h4>' + esc(child.title || child.group_id) + ' ' +
          '<span class="pill pill-warn">' + esc(child.incorrect_attempts) + '</span></h4>' +
          '<ul class="small">' + (child.children || []).map((leaf) =>
            '<li>' + exerciseLink(courseId, leaf.exercise_id, leaf.prompt) +
            ' ' + pill(statusLabel(leaf.status), String(leaf.status).toUpperCase()) +
            '</li>').join('') + '</ul></div>').join('') +
        '</details>';
    }).join('');
  }
  return groups.map((group) =>
    '<div class="nested-group">' +
    '<h3>' + kpLink(courseId, group.group_id, group.title) + ' ' +
    '<span class="pill pill-warn">' + esc(t('mk.incorrectAttempts')) + ' ' +
    esc(group.incorrect_attempts) + '</span>' +
    (group.attention
      ? ' <span class="pill pill-warn">' + esc(group.attention) + '</span>'
      : '') + '</h3>' +
    (group.attention_basis
      ? '<p class="tiny muted">' + esc(t('mk.weakBasis')) + ': ' +
        esc(group.attention_basis) + '</p>'
      : '') +
    '<ul class="small">' + (group.children || []).map((leaf) =>
      '<li>' + exerciseLink(courseId, leaf.exercise_id, leaf.prompt) +
      ' ' + pill(statusLabel(leaf.status), String(leaf.status).toUpperCase()) +
      '</li>').join('') + '</ul></div>').join('');
}

/** 薄弱知识点区块。注意: 这里的**每一行都来自 StudentState**。 */
function renderWeakKnowledge(courseId, rows) {
  if (!rows || !rows.length) {
    return '<p class="muted small">' + esc(t('mk.noWeak')) + '</p>';
  }
  return '<table class="data">' + tableCaption(t('mk.weakTitle')) + '<thead><tr>' +
    '<th scope="col">' + esc(t('mk.knowledgeTitle')) + '</th>' +
    '<th scope="col">' + esc(t('mk.attention')) + '</th>' +
    '<th scope="col" class="num">' + esc(t('mk.incorrectAttempts')) + '</th>' +
    '<th scope="col">' + esc(t('mk.definitionBasis')) + '</th>' +
    '</tr></thead><tbody>' +
    rows.map((row) => (
      '<tr><td>' + kpLink(courseId, row.knowledge_id, row.title) +
        '<br><span class="tiny muted">' + esc(row.attention_basis) + '</span></td>' +
      '<td>' + pill(row.signal, row.signal) + '</td>' +
      '<td class="num">' + esc(row.incorrect_attempts) + '</td>' +
      '<td class="tiny muted mono">' + esc(row.definition) + '</td></tr>'
    )).join('') + '</tbody></table>';
}

/** 知识点错题详情（spec 65.9: 错题 -> 为什么错 -> 重新学习依据）。 */
function renderMistakeDetail(courseId, studentId, detail) {
  const kp = detail.knowledge || {};
  const why = detail.why_incorrect || [];
  const evidence = detail.evidence || [];
  const practice = detail.practice_targets || [];
  const prereq = detail.prerequisites || [];

  return '<div class="page-head"><div class="crumbs">' +
    '<a href="#/mistakes">' + esc(t('nav.mistakes')) + '</a> / ' +
    esc(kp.title || kp.knowledge_id) + '</div>' +
    '<h1>' + esc(t('mk.detailTitle')) + '</h1>' +
    '<p class="subtitle">' + kpLink(courseId, kp.knowledge_id, kp.title) + ' · ' +
    esc(studentId) + '</p></div>' +

    '<div class="grid grid-2">' +

    '<div class="card"><h2>' + esc(t('mk.whyIncorrect')) + '</h2>' +
    '<p class="small muted">' + esc(t('mk.whyIncorrectNote')) + '</p>' +
    (why.length
      ? whoList(why)
      : emptyState(t('mk.empty'))) +
    '</div>' +

    '<div class="card"><h2>' + esc(t('mk.evidenceTitle')) + '</h2>' +
    '<p class="small muted">' + esc(t('mk.evidenceNote')) + '</p>' +
    (evidence.length
      ? evidence.map((row) =>
          '<div class="evidence-item">' +
          '<p class="small">' + esc(row.content) + '</p>' +
          '<p class="tiny muted mono">' + esc(row.evidence_id) + ' · ' +
          esc(row.evidence_type) +
          (row.material ? ' · ' + esc(row.material.filename) : '') +
          (row.source && row.source.location ? ' · ' + esc(row.source.location) : '') +
          '</p></div>').join('')
      : emptyState(t('mk.noEvidence'))) +
    '</div>' +

    '<div class="card"><h2>' + esc(t('mk.suggestedActions')) + '</h2>' +
    renderMistakeActions(courseId, studentId, detail.suggested_actions) +
    '</div>' +

    '<div class="card"><h2>' + esc(t('mk.practiceTitle')) + '</h2>' +
    '<p class="small muted">' + esc(t('mk.practiceNote')) + '</p>' +
    (practice.length
      ? '<ul class="small">' + practice.map((item) =>
          '<li>' + exerciseLink(courseId, item.exercise_id, item.prompt) + ' ' +
          '<span class="pill pill-muted">' + esc(item.exercise_type) + '</span> ' +
          (item.previously_incorrect
            ? '<span class="pill pill-bad">' + esc(t('mk.previouslyIncorrect')) + '</span>'
            : (item.previously_attempted
              ? '<span class="pill pill-warn">' + esc(t('mk.previouslyAttempted')) + '</span>'
              : '<span class="pill pill-ok">' + esc(t('mk.notAttempted')) + '</span>')) +
          '</li>').join('') + '</ul>'
      : emptyState(t('mk.noPractice'))) +
    '</div>' +

    '<div class="card"><h2>' + esc(t('mk.prerequisiteTitle')) + '</h2>' +
    (prereq.length
      ? '<ul class="small">' + prereq.map((id) => '<li>' + kpLink(courseId, id) + '</li>').join('') + '</ul>'
      : emptyState(t('mk.noPrerequisite'))) +
    '</div>' +

    '<div class="card"><h2>' + esc(t('mk.studentState')) + '</h2>' +
    '<dl class="kv">' +
    '<dt>' + esc(t('mk.attention')) + '</dt><dd>' +
      (detail.attention ? pill(detail.attention, detail.attention) : esc(t('mk.attentionNone'))) + '</dd>' +
    '<dt>' + esc(t('mk.weakBasis')) + '</dt><dd class="tiny muted mono">' +
      esc(detail.attention_basis || '—') + '</dd>' +
    '<dt>' + esc(t('mk.incorrectAttempts')) + '</dt><dd>' + esc(detail.incorrect_attempts) + '</dd>' +
    '</dl></div>' +

    '</div>';
}

function whoList(rows) {
  return '<table class="data">' + tableCaption(t('mk.whyIncorrect')) + '<thead><tr>' +
    '<th scope="col">' + esc(t('mk.question')) + '</th>' +
    '<th scope="col">' + esc(t('mk.answer')) + '</th>' +
    '<th scope="col">' + esc(t('mk.status')) + '</th>' +
    '<th scope="col">' + esc(t('ex.feedback')) + '</th>' +
    '</tr></thead><tbody>' +
    rows.map((row) => (
      '<tr><td>' + esc(row.prompt) + '</td>' +
      '<td>' + esc(row.submitted_value) + '</td>' +
      '<td>' + pill(statusLabel(row.status), String(row.status).toUpperCase()) +
        ' <span class="tiny muted">' + esc(fmtNumber(row.score)) + '</span></td>' +
      '<td>' + (row.feedback ? esc(row.feedback) : '<span class="muted">' +
        esc(t('mk.noFeedback')) + '</span>') +
        '<br><span class="tiny muted mono">' + esc(row.evaluator_version) + '</span></td>' +
      '</tr>'
    )).join('') + '</tbody></table>';
}

/** 错题本主页。group_by 只影响分组方式, 数据源完全相同。 */
async function pageMistakes() {
  markActiveNav('#/mistakes');
  const courseId = await requireCourse();
  const students = (await api('/students', { query: { course_id: courseId } })).students || [];
  const groupBy = state.mistakeGroupBy === 'topic' ? 'topic' : 'knowledge';

  if (!students.length) {
    setView('<div class="page-head"><h1>' + esc(t('mk.title')) + '</h1>' +
      '<p class="subtitle">' + esc(t('mk.subtitle')) + '</p></div>' +
      '<div class="card">' + emptyState(t('mk.empty')) + '</div>');
    return;
  }
  const studentId = state.studentId &&
    students.some((s) => s.student_id === state.studentId)
    ? state.studentId : students[0].student_id;
  setStudent(studentId);

  const view = await api('/students/' + encodeURIComponent(studentId) + '/mistakes', {
    query: { course_id: courseId, group_by: groupBy, lang: state.lang },
  });
  const topicView = groupBy === 'topic'
    ? view
    : await api('/students/' + encodeURIComponent(studentId) + '/mistakes', {
        query: { course_id: courseId, group_by: 'topic', lang: state.lang },
      });
  const kpIndex = topicIndexOf(topicView.groups);
  const counts = view.counts || {};

  const stat = (label, value) =>
    '<div class="stat"><div class="stat-label">' + esc(label) + '</div>' +
    '<div class="stat-value">' + esc(value) + '</div></div>';

  const groupTitle = (name) => t(name === 'topic' ? 'mk.byTopic' : 'mk.byKnowledge');
  const other = groupBy === 'topic' ? 'knowledge' : 'topic';
  const toggle = '<div class="toggle-row">' +
    '<span class="small muted">' + esc(t('mk.groupBy')) + ':</span> ' +
    '<span class="pill pill-ok">' + esc(groupTitle(groupBy)) + '</span> ' +
    '<button type="button" id="mistake-group-toggle" data-group="' + esc(other) +
    '" data-course="' + esc(courseId) + '">' + esc(groupTitle(other)) + '</button>' +
    '</div>';

  const empty = !view.has_mistakes;

  setView(
    '<div class="page-head"><h1>' + esc(t('mk.title')) + '</h1>' +
    '<p class="subtitle">' + esc(t('mk.subtitle')) + ' · ' +
    esc(studentId) + ' · ' + esc(courseLabel(courseId)) + '</p>' +
    '<p class="small muted">' + esc(t('mk.note')) + '</p></div>' +

    '<div class="grid grid-4" style="margin-bottom:16px">' +
    stat(t('mk.mistakeCount'), counts.incorrect_attempts || 0) +
    stat(t('mk.knowledgeTitle'), counts.knowledge_with_mistakes || 0) +
    stat(t('mk.groupTitle'), counts.groups || 0) +
    stat(t('mk.weakTitle'), counts.weak_knowledge || 0) +
    '</div>' +

    (empty
      ? '<div class="card"><h2>' + esc(t('mk.listTitle')) + '</h2>' +
        emptyState(t('mk.empty')) +
        '<p class="small muted">' + esc(t('mk.emptyHint')) + '</p></div>'
      : toggle +
        '<div class="card"><h2>' + esc(t('mk.listTitle')) + '</h2>' +
        mistakeTable(courseId, view.mistakes || [], kpIndex) + '</div>' +

        '<div class="card"><h2>' + esc(t('mk.groupTitle')) + ' · ' +
        esc(groupTitle(groupBy)) + '</h2>' +
        renderMistakeGroups(courseId, groupBy, view.groups || []) + '</div>') +

    '<div class="card"><h2>' + esc(t('mk.weakTitle')) + '</h2>' +
    '<p class="small muted">' + esc(t('mk.weakNote')) + '</p>' +
    renderWeakKnowledge(courseId, view.weak_knowledge || []) +
    '</div>' +

    '<div class="card"><h2>' + esc(t('mk.suggestedActions')) + '</h2>' +
    '<p class="small muted">' + esc(t('mk.note')) + '</p>' +
    '<ul class="small">' + (view.knowledge || []).map((row) =>
      '<li>' + kpLink(courseId, row.knowledge_id, row.title) + ' ' +
      '<span class="pill pill-warn">' + esc(t('mk.incorrectAttempts')) + ' ' +
      esc(row.incorrect_attempts) + '</span> ' +
      '<a href="#/mistakes/' + encodeURIComponent(courseId) + '/' +
      encodeURIComponent(row.knowledge_id) + '">' +
      esc(t('mk.detailTitle')) + '</a></li>').join('') + '</ul>' +
    ((view.knowledge || []).length ? '' : '<p class="muted small">' +
      esc(t('mk.noActions')) + '</p>') +
    '</div>' +

    '<div class="card"><h2>' + esc(t('mk.evidenceTitle')) + '</h2>' +
    '<p class="small muted">' + esc(t('mk.evidenceNote')) + '</p>' +
    '<p class="small"><a href="#/reviews">' +
    esc(t('nav.reviews')) + '</a> · ' +
    '<a href="#/exercises">' + esc(t('nav.exercises')) + '</a></p></div>'
  );
}

async function pageMistakeDetail(courseId, knowledgeId) {
  markActiveNav('#/mistakes');
  setRouteCourse(courseId);
  const students = (await api('/students', { query: { course_id: courseId } })).students || [];
  if (!students.length) {
    setView('<div class="card"><h1>' + esc(t('mk.detailTitle')) + '</h1>' +
      '<p class="muted">' + esc(t('mk.empty')) + '</p></div>');
    return;
  }
  const studentId = state.studentId &&
    students.some((s) => s.student_id === state.studentId)
    ? state.studentId : students[0].student_id;
  const detail = await api(
    '/students/' + encodeURIComponent(studentId) + '/mistakes/' + encodeURIComponent(knowledgeId),
    { query: { course_id: courseId } }
  );
  setView(renderMistakeDetail(courseId, studentId, detail));
}
