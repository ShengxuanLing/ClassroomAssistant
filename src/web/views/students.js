/*
 * 学生列表 (``#/students``) / 学生详情 / 学习路径链 / 各类动作接线。
 */
'use strict';

async function pageStudents() {
  markActiveNav('#/students');
  const courseId = await requireCourse();
  const students = (await api('/students', { query: { course_id: courseId } })).students || [];

  const listCard =
    '<div class="card">' +
    (students.length
      ? '<table class="data">' + tableCaption(t('nav.students')) + '<thead><tr><th scope="col">ID</th><th scope="col">' + esc(t('common.actions')) +
        '</th></tr></thead><tbody>' +
        students.map((student) => (
          '<tr><td><a href="#/courses/' + encodeURIComponent(courseId) + '/students/' +
          encodeURIComponent(student.student_id) + '">' +
          esc(student.display_name || student.student_id) + '</a>' +
          '<br><span class="tiny muted mono">' + esc(student.student_id) + '</span></td>' +
          '<td><a class="btn" href="#/courses/' + encodeURIComponent(courseId) +
          '/students/' + encodeURIComponent(student.student_id) + '">' +
          esc(t('student.dashboard')) + '</a></td></tr>'
        )).join('') + '</tbody></table>'
      : emptyState(t('student.none'))) +
    '</div>';

  // 注册表单**永远**渲染 —— 空课程时更是必须的: 否则第一个学生永远建不出来,
  // 空态只能把用户打发去手写 HTTP 请求 (用户报的正是这一条)。
  const registerCard =
    '<div class="card"><div class="card-head"><h2>' + esc(t('student.register')) +
    '</h2></div>' +
    '<form id="student-form" class="grid grid-3" data-course="' + esc(courseId) + '">' +
    '<label class="field"><span>' + esc(t('student.idLabel')) + '</span>' +
    '<input type="text" name="student_id" required maxlength="64" placeholder="' +
    esc(t('student.idPlaceholder')) + '"></label>' +
    '<label class="field"><span>' + esc(t('student.nameLabel')) + '</span>' +
    '<input type="text" name="display_name" maxlength="64"></label>' +
    '<div class="actions"><button class="primary" type="submit">' +
    esc(t('student.registerAction')) + '</button>' +
    '<span class="small muted">' + esc(t('student.registerNote')) + '</span></div>' +
    '</form></div>';

  setView(
    '<div class="page-head"><h1>' + esc(t('nav.students')) + '</h1>' +
    '<p class="subtitle">' + esc(t('nav.courses')) + ' <strong>' + esc(courseLabel(courseId)) +
    '</strong> · ' + esc(students.length) + '</p></div>' +
    listCard + registerCard
  );

  wireStudentForm();
}

// ---- 练习页 (Task 41) ----------------------------------------------------


function kpLink(courseId, knowledgeId, label) {
  return '<a href="#/courses/' + encodeURIComponent(courseId) + '/knowledge/' +
    encodeURIComponent(knowledgeId) + '">' + esc(label || knowledgeId) + '</a>';
}

/** 学习路径节点: 前置 -> 知识点 -> 练习 -> 评估 (spec Task 40)。 */
function renderPathChain(courseId, studentId, path) {
  const nodes = path.nodes || [];
  if (!nodes.length) return emptyState(t('path.noPath'));
  return '<ol class="path-chain">' + nodes.map((node) => {
    const kp = node.knowledge_point || {};
    const title = kp.title || node.knowledge_point_id;
    const prereq = (node.prerequisite_ids || []).length
      ? (node.prerequisite_ids || []).map((id) =>
          (node.unmet_prerequisite_ids || []).indexOf(id) >= 0
            ? '<span class="pill pill-bad">' + esc(id) + '</span>'
            : '<span class="pill pill-ok">' + esc(id) + '</span>').join(' ')
      : '<span class="muted">' + esc(t('common.none')) + '</span>';
    const exercises = (node.exercises || []);
    const evaluations = (node.evaluations || []);
    const nextEvent = node.next_event;
    const eventLabel = {
      viewed: t('path.markViewed'),
      practiced: t('path.markPracticed'),
      reviewed: t('path.markReviewed'),
    }[nextEvent];
    return (
      '<li class="path-node">' +
      '<div class="trace-node">' +
      '<div class="row-between">' +
      '<div><span class="node-kind">' + esc(t('path.knowledge')) + ' #' +
      esc(node.position) + '</span><br>' + kpLink(courseId, node.knowledge_point_id, title) +
      '<br><span class="tiny muted mono">' + esc(node.knowledge_point_id) + '</span></div>' +
      '<div class="row">' +
      pill(statusLabel(node.status), node.status) +
      '<span class="pill pill-muted">' + esc(t('path.rawState')) + ': ' +
      esc(stateLabel(node.state)) + '</span>' +
      '</div></div>' +

      '<dl class="kv" style="margin-top:8px">' +
      '<dt>' + esc(t('path.prerequisite')) + '</dt><dd>' + prereq + '</dd>' +
      '<dt>' + esc(t('path.exercise')) + '</dt><dd>' +
        (exercises.length
          ? exercises.map((ex) => esc(ex.prompt || ex.exercise_id) +
              ' <span class="tiny muted mono">' + esc(ex.exercise_type || '') + '</span>').join('<br>')
          : '<span class="muted">' + esc(t('common.none')) + '</span>') + '</dd>' +
      '<dt>' + esc(t('path.evaluation')) + '</dt><dd>' +
        (evaluations.length
          ? evaluations.map((ev) => pill(String(ev.status), String(ev.status).toUpperCase()) +
              ' ' + esc(t('common.score')) + ' ' + fmtNumber(ev.score)).join('<br>')
          : '<span class="muted">' + esc(t('common.none')) + '</span>') + '</dd>' +
      '<dt>' + esc(t('common.answer')) + '</dt><dd>' +
        esc(node.activity.answer_count) + ' / ' + esc(t('common.none')) +
        ' (' + esc(node.activity.correct_count) + ' ✓ · ' +
        esc(node.activity.incorrect_count) + ' ✗)</dd>' +
      '</dl>' +

      '<div class="actions">' +
      '<a class="btn" href="#/courses/' + encodeURIComponent(courseId) + '/knowledge/' +
      encodeURIComponent(node.knowledge_point_id) + '">' + esc(t('expl.title')) + '</a>' +
      (nextEvent
        ? '<button data-action="learning-event" data-course="' + esc(courseId) +
          '" data-student="' + esc(studentId) + '" data-knowledge="' +
          esc(node.knowledge_point_id) + '" data-event="' + esc(nextEvent) + '">' +
          esc(eventLabel) + '</button>'
        : '<span class="pill pill-ok">' + esc(t('path.done')) + '</span>') +
      '</div>' +
      (node.unmet_prerequisite_ids && node.unmet_prerequisite_ids.length
        ? '<p class="small"><span class="pill pill-bad">' + esc(t('path.blockedBy')) +
          '</span> ' + esc(node.unmet_prerequisite_ids.join(', ')) + '</p>'
        : '') +
      '</div></li>'
    );
  }).join('') + '</ol>';
}

// ---- Task 65: 错题与薄弱知识点中心 --------------------------------------
//
// 这个页面是**只读投影**。它不重算对错（对错只来自既有 Evaluation），
// 也不推导掌握度（薄弱只来自 StudentState 的既有语义）。页面上出现的
// 每一个"关注"信号都必须带着它的依据。

/** 一种动作 -> 前端 href。REVIEW_KNOWLEDGE 走知识点页（那里有解释/证据）。 */

async function pageStudent(courseId, studentId) {
  markActiveNav('');
  setRouteCourse(courseId);
  setStudent(studentId);
  const data = await api('/students/' + encodeURIComponent(studentId) + '/dashboard', {
    query: { course_id: courseId },
  });
  const courses = (await api('/courses')).courses || [];
  const progress = data.progress || {};
  const states = progress.states || [];
  const plan = data.study_plan || {};
  const planItems = plan.items || [];
  const paths = data.learning_paths || [];
  const pending = data.pending_exercises || [];
  const recent = data.recent_evaluations || [];
  const gaps = data.knowledge_gaps || {};

  const stat = (label, value) =>
    '<div class="stat"><div class="stat-label">' + esc(label) + '</div>' +
    '<div class="stat-value">' + esc(value) + '</div></div>';

  setView(
    '<div class="page-head"><div class="crumbs">' +
    '<a href="#/">' + esc(t('nav.overview')) + '</a> / <a href="#/courses/' +
    encodeURIComponent(courseId) + '">' + esc(courseLabel(courseId)) + '</a> / ' +
    esc(t('student.dashboard')) + '</div>' +
    '<h1>' + esc(data.display_name || studentId) + '</h1>' +
    '<p class="subtitle mono small">' + esc(studentId) + ' · ' + esc(courseLabel(courseId)) + '</p></div>' +

    '<div class="grid grid-4" style="margin-bottom:16px">' +
    stat(t('student.registered'), (progress.registered_knowledge_points || []).length) +
    stat(t('student.notStarted'), (progress.not_started_knowledge_points || []).length) +
    stat(t('student.answered'), progress.answered_count || 0) +
    stat(t('student.average'), progress.average_score === null ||
      progress.average_score === undefined ? '—' : fmtNumber(progress.average_score)) +
    '</div>' +

    '<div class="grid grid-2">' +

    '<div class="card"><h2>' + esc(t('student.courses')) + '</h2>' +
    (courses.length
      ? '<ul class="small">' + courses.map((course) =>
          '<li>' + (course.course_id === courseId ? '<strong>' : '') +
          '<a href="#/courses/' + encodeURIComponent(course.course_id) + '">' +
          esc(course.name) + '</a>' +
          (course.course_id === courseId ? ' ✓</strong>' : '') + '</li>').join('') + '</ul>'
      : emptyState(t('common.none'))) +
    '</div>' +

    '<div class="card"><h2>' + esc(t('student.progress')) + '</h2>' +
    '<p class="small muted">' + esc(t('student.stateCounts')) + '</p>' +
    (Object.keys(progress.state_counts || {}).length
      ? '<p class="row small">' + Object.keys(progress.state_counts).sort().map((key) =>
          pill(stateLabel(key) + ' ' + progress.state_counts[key], key)).join('') + '</p>'
      : '') +
    (states.length
      ? '<table class="data">' + tableCaption(t('student.progress')) + '<thead><tr><th scope="col">' + esc(t('common.knowledgePoint')) +
        '</th><th scope="col">' + esc(t('common.status')) + '</th><th scope="col" class="num">✓</th>' +
        '<th scope="col" class="num">✗</th></tr></thead><tbody>' +
        states.map((record) => (
          '<tr><td>' + kpLink(courseId, record.knowledge_point_id) + '</td>' +
          '<td>' + pill(stateLabel(record.state), record.state) + '</td>' +
          '<td class="num">' + esc(record.correct_count) + '</td>' +
          '<td class="num">' + esc(record.incorrect_count) + '</td></tr>'
        )).join('') + '</tbody></table>'
      : emptyState(t('student.noStates'))) +
    '</div>' +

    '<div class="card"><h2>' + esc(t('student.studyPlan')) + '</h2>' +
    (planItems.length
      ? '<table class="data">' + tableCaption(t('student.studyPlan')) + '<thead><tr><th scope="col">' + esc(t('common.knowledgePoint')) +
        '</th><th scope="col">' + esc(t('common.status')) + '</th></tr></thead><tbody>' +
        planItems.map((item) => (
          '<tr><td>' + kpLink(courseId, item.knowledge_point_id) +
          ((item.prerequisite_ids || []).length
            ? '<br><span class="tiny muted">' + esc(t('path.prerequisite')) + ': ' +
              esc((item.prerequisite_ids || []).join(', ')) + '</span>' : '') + '</td>' +
          '<td class="small">' + ((item.reason_codes || []).map((code) =>
            '<span class="pill pill-info">' + esc(code) + '</span>').join(' ') ||
            esc(t('common.none'))) + '</td></tr>'
        )).join('') + '</tbody></table>' +
        '<p class="tiny muted mono">plan_id ' + esc(plan.plan_id || '') + '</p>'
      : emptyState(t('common.none'))) +
    '</div>' +

    '<div class="card"><h2>' + esc(t('student.pending')) + '</h2>' +
    (pending.length
      ? '<ul class="small">' + pending.map((item) =>
          '<li><span class="pill pill-warn">' + esc(item.exercise_type) + '</span> ' +
          exerciseLink(courseId, item.exercise_id, item.prompt) +
          ' <span class="tiny muted mono">' +
          esc((item.knowledge_point_ids || []).join(', ')) + '</span></li>').join('') + '</ul>'
      : emptyState(t('common.none'))) +
    '</div>' +

    '<div class="card"><h2>' + esc(t('student.recentEval')) + '</h2>' +
    (recent.length
      ? '<table class="data">' + tableCaption(t('student.recentEval')) + '<thead><tr><th scope="col">' + esc(t('common.exercise')) +
        '</th><th scope="col">' + esc(t('common.answer')) + '</th><th scope="col">' +
        esc(t('common.status')) + '</th><th scope="col" class="num">' + esc(t('common.score')) +
        '</th></tr></thead><tbody>' +
        recent.map((item) => (
          '<tr><td>' + exerciseLink(courseId, item.exercise_id) + '</td>' +
          '<td>' + esc(item.submitted_value) +
          (item.submitted_at ? '<br><span class="tiny muted">' + esc(item.submitted_at) +
            '</span>' : '') + '</td>' +
          '<td>' + pill(String(item.status), String(item.status).toUpperCase()) + '</td>' +
          '<td class="num">' + fmtNumber(item.score) + '</td></tr>'
        )).join('') + '</tbody></table>'
      : emptyState(t('common.none'))) +
    '</div>' +

    '<div class="card"><h2>' + esc(t('student.gaps')) + '</h2>' +
    (((gaps.student_gaps || []).length || (gaps.course_gaps || []).length)
      ? '<ul class="small">' +
        (gaps.student_gaps || []).map((gap) =>
          '<li><span class="pill pill-bad">' + esc(gap.reason) + '</span> ' +
          kpLink(courseId, gap.knowledge_point_id) + '</li>').join('') +
        (gaps.course_gaps || []).slice(0, 8).map((gap) =>
          '<li><span class="pill pill-warn">COURSE_GAP</span> ' +
          esc(gap.description || gap.gap_type || '') + '</li>').join('') +
        '</ul>'
      : emptyState(t('common.none'))) +
    '</div>' +

    '<div class="card" style="grid-column:1/-1"><h2>' + esc(t('student.learningPath')) +
    '</h2><p class="small muted">' + esc(t('path.prerequisite')) + ' ↓ ' +
    esc(t('path.knowledge')) + ' ↓ ' + esc(t('path.exercise')) + ' ↓ ' +
    esc(t('path.evaluation')) + '</p>' +
    (paths.length
      ? paths.map((entry) =>
          '<h3>' + kpLink(courseId, entry.target_knowledge_point_id) + '</h3>' +
          renderPathChain(courseId, studentId, entry.path)).join('')
      : emptyState(t('path.noPath'))) +
    '</div>' +

    '</div>'
  );
}

// ---- 知识解释 (Grounded explanation, Task 29) ---------------------------

async function loadExplanation(courseId, knowledgeId, language) {
  const panel = document.getElementById('explanation-panel');
  if (!panel) return;
  const lang = language || state.lang;
  panel.innerHTML = '<div class="card"><p class="muted">' + esc(t('common.loading')) + '</p></div>';
  let data;
  try {
    data = await api('/knowledge/' + encodeURIComponent(knowledgeId) + '/explanation', {
      query: { course_id: courseId, language: lang },
    });
  } catch (err) {
    panel.innerHTML = '<div class="card"><h2>' + esc(t('expl.title')) + '</h2>' +
      '<p class="pill pill-bad">' + esc(err.code) + '</p><p class="small">' +
      esc(err.message) + '</p></div>';
    return;
  }
  const rep = data.representation;
  const picker = '<label class="field"><span>' + esc(t('expl.request')) + '</span>' +
    '<select id="explain-language">' +
    UI_LANGUAGES.map((code) =>
      '<option value="' + esc(code) + '"' + (code === lang ? ' selected' : '') + '>' +
      esc(code) + '</option>').join('') +
    '</select></label>';

  let body;
  if (data.available && rep) {
    body =
      '<div class="row-between"><h3>' + esc(rep.title || '') + '</h3>' +
      '<span class="tiny muted mono">' + esc(rep.representation_id) + '</span></div>' +
      '<p class="tiny muted">' + esc(t('expl.original')) + ' · ' +
      esc(t('expl.request')) + ': ' + esc(rep.language) + '</p>' +
      originalBlock(rep.explanation, null) +
      ((rep.key_points || []).length
        ? '<h3>' + esc(t('expl.keyPoints')) + '</h3><ul class="small">' +
          rep.key_points.map((point) => '<li>' + esc(point) + '</li>').join('') + '</ul>'
        : '') +
      ((rep.examples || []).length
        ? '<h3>' + esc(t('expl.examples')) + '</h3><ul class="small">' +
          rep.examples.map((item) => '<li>' + esc(item) + '</li>').join('') + '</ul>'
        : '') +
      ((rep.claims || []).length
        ? '<h3>' + esc(t('expl.claims')) + '</h3><ul class="small">' +
          rep.claims.map((claim) => '<li>' + esc(claim.text) + ' <span class="tiny muted mono">' +
            esc((claim.evidence_ids || []).join(', ')) + '</span></li>').join('') + '</ul>'
        : '');
  } else {
    body = '<p class="empty">' + esc(data.message || t('expl.unavailable')) + '</p>' +
      '<p class="small muted">status=' + esc(data.status) + ' · ' +
      esc(t('expl.evidenceLangs')) + ': ' +
      esc((data.evidence_languages || []).join(', ') || t('common.none')) +
      ((data.supported_languages || []).length
        ? ' · ' + esc(t('common.evidence')) + ': ' +
          esc(data.supported_languages.join(', '))
        : '') + '</p>';
  }

  panel.innerHTML =
    '<div class="card"><div class="card-head"><h2>' + esc(t('expl.title')) + '</h2>' +
    '<span class="tiny muted">' + t('Task 29 · 无证据即明确报缺，绝不生成内容') + '</span></div>' +
    picker + body +
    '<h3>' + esc(t('common.evidence')) + '</h3>' +
    ((data.evidence || []).length
      ? data.evidence.map((ev) => (
          '<div class="trace-node" style="margin-bottom:8px">' +
          '<div class="node-kind">' + esc(ev.evidence_type) + ' · ' + esc(ev.language) +
          ' · ' + esc(ev.confidence) + '</div>' +
          originalBlock(ev.content, ev.language) +
          '<div class="tiny muted mono">' + esc(sourceLocation(ev.source)) + '</div></div>'
        )).join('')
      : emptyState(t('expl.unavailable'))) +
    '<dl class="kv" style="margin-top:10px">' +
    '<dt>' + esc(t('expl.related')) + '</dt><dd>' +
      ((data.related_concepts || []).length
        ? data.related_concepts.map((id) => kpLink(courseId, id)).join(', ')
        : esc(t('common.none'))) + '</dd>' +
    '<dt>' + esc(t('expl.prerequisites')) + '</dt><dd>' +
      ((data.prerequisites || []).length
        ? data.prerequisites.map((id) => kpLink(courseId, id)).join(', ')
        : esc(t('common.none'))) + '</dd>' +
    '</dl></div>';

  const select = document.getElementById('explain-language');
  if (select) {
    select.addEventListener('change', () => {
      // 只改变解释的请求语言标签; 绝不修改任何 Evidence。
      loadExplanation(courseId, knowledgeId, select.value);
    });
  }
}

// ------------------------------------------------------------------ 动作

// Task 40: 学习事件只能由学生主动触发, 且只能走 Task 30 的状态机
// (viewed / practiced / reviewed)。前端不实现任何 mastery 算法。
async function actionLearningEvent(courseId, studentId, knowledgeId, eventType, button) {
  if (!courseId || !studentId || !knowledgeId || !eventType) {
    toast(t('缺少必要参数'), 'bad');
    return;
  }
  button.disabled = true;
  try {
    const record = await api('/students/' + encodeURIComponent(studentId) + '/learning-events', {
      method: 'POST',
      query: { course_id: courseId },
      body: { knowledge_id: knowledgeId, event_type: eventType },
    });
    toast(t('学习事件已记录: ') + eventType + ' → ' + (record.state || '?'), 'ok');
    route();
  } catch (err) {
    toast(t('学习事件被拒绝 [') + err.code + '] ' + err.message, 'bad');
  } finally {
    button.disabled = false;
  }
}

async function actionProcessMaterial(courseId, materialId, button) {
  // 可恢复 (P3-2): 服务端对这个材料有一条能回答"跑完了吗"的读接口
  // (GET /api/processing/{material_id})。音频转写可能持续数分钟, 用户中途
  // 按 F5 不该丢掉全部信号。
  const taskId = startTask(t('处理') + ' · ' + materialId, {
    courseId,
    targetId: materialId,
    targetKind: 'material',
  });
  button.disabled = true;
  try {
    const job = await api('/materials/' + encodeURIComponent(materialId) + '/process', {
      method: 'POST', query: { course_id: courseId },
    });
    finishTask(taskId, job.status === 'SUCCEEDED',
      job.status + ' (' + (job.evidence_ids || []).length + t(' 条证据)'));
    toast(t('处理完成: ') + job.status + ' (' + job.evidence_ids.length + t(' 条证据)'),
      job.status === 'SUCCEEDED' ? 'ok' : 'bad');
    // TASK-77: 自动 AI 的结局只读 job.ai —— 材料状态不受它影响。
    if (job.ai && job.ai.status === 'completed') {
      toast(t('ai.autoDone') + ': ' + t('ai.autoAccepted') + ' ' + job.ai.auto_accepted +
        ' · ' + t('ai.needsReview') + ' ' + job.ai.needs_review +
        ' · ' + t('ai.conflicts') + ' ' + job.ai.conflicts, 'ok');
    } else if (job.ai && job.ai.status === 'failed') {
      toast(t('ai.autoFailed') + ' · ' + t('ai.safeNote'), 'bad');
    }
  } catch (err) {
    finishTask(taskId, false, err.code + ' ' + err.message);
    toast(t('处理失败 [') + err.code + '] ' + err.message, 'bad');
  } finally {
    button.disabled = false;
  }
  // 处理页重绘后, 若自动分析已完成, 把只读总结直接画进 AI 面板
  // (材料页才有该面板, 其它页没有时静默跳过)。
  //
  // route() **必须 await**: 它重建 #view, AI 面板是新建的空壳。不 await 的话
  // 下一行的 loadAiSummaryIntoPanel() 查到的还是**旧 DOM** 里的面板, 画上去的
  // 内容紧接着被重绘冲掉 —— 用户看到的是"报告闪一下就没"。
  // 写法与 materials.js 的 actionAiAnalyze() 逐字对齐 (那边注释记了 TASK-77
  // 修的就是同一个竞态)。
  await route();
  try {
    await loadAiSummaryIntoPanel(courseId, materialId);
  } catch (err) {
    void err;
  }
}

async function actionRetryMaterial(courseId, materialId, button) {
  button.disabled = true;
  try {
    const job = await api('/materials/' + encodeURIComponent(materialId) + '/retry', {
      method: 'POST', query: { course_id: courseId },
    });
    toast(t('重试结果: ') + job.status, job.status === 'SUCCEEDED' ? 'ok' : 'bad');
  } catch (err) {
    toast(t('重试被拒绝 [') + err.code + '] ' + err.message, 'bad');
  } finally {
    button.disabled = false;
    route();
  }
}

async function actionMaterialEvidence(courseId, materialId) {
  const panel = document.getElementById('evidence-panel');
  if (!panel) return;
  try {
    const evidence = (await api('/materials/' + encodeURIComponent(materialId) + '/evidence', {
      query: { course_id: courseId },
    })).evidence || [];
    // D4: "零证据"有两种完全不同的处境 —— 处理成功但什么都没提取到, 和
    // 还没处理过。只看证据列表分不出来, 所以零证据时**再查一次材料记录**;
    // 查不到就什么都不加 (证据优先: 宁可不解释, 也不猜一个原因)。
    let emptyHint = '';
    if (!evidence.length) {
      try {
        const material = await api('/materials/' + encodeURIComponent(materialId), {
          query: { course_id: courseId },
        });
        emptyHint = zeroEvidenceHint(material);
      } catch (err) {
        // 材料记录读不到 -> 没有判据 -> 不加提示 (不是错误路径)。
        emptyHint = '';
      }
    }
    panel.innerHTML =
      '<div class="card"><div class="card-head"><h2>' + t('材料证据 (') + esc(evidence.length) + ')</h2>' +
      '<span class="mono tiny">' + esc(materialId) + '</span></div>' +
      (evidence.length
        ? evidence.map((ev) => (
            '<div class="trace-node" style="margin-bottom:8px">' +
            '<div class="node-kind">' + esc(ev.evidence_type) + ' · ' + esc(ev.language) +
            t(' · 置信度 ') + esc(ev.confidence) + '</div>' +
            originalBlock(ev.content, null) +
            '<div class="tiny muted mono">' + esc(sourceLocation(ev.source)) + '</div></div>'
          )).join('')
        : emptyState(t('该材料还没有生成证据。')) + emptyHint) +
      '</div>';
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (err) {
    toast(t('读取证据失败 [') + err.code + '] ' + err.message, 'bad');
  }
}

async function actionProcessSession(courseId, sessionId, button) {
  // 整堂处理会为这节课的**全部**材料启动处理任务, 可能持续数分钟 ——
  // 属于"代价高"的批量动作, 点一次要确认 (判据见 confirmDestructive 的注释)。
  if (!confirmDestructive(t('确认要整堂处理吗？这会为这节课的全部材料启动处理任务，可能持续数分钟。'))) {
    return;
  }
  // 可恢复 (P3-2): 整堂处理是本页最长的任务, 而 GET /api/processing 支持
  // session_id 过滤, 能回答"这节课还剩几份没处理完"。按 targetKind='session'
  // 走另一条恢复分支 (见 app.js 的 pollRestoredTask)。
  const taskId = startTask(t('处理') + ' · ' + sessionId, {
    courseId,
    targetId: sessionId,
    targetKind: 'session',
  });
  button.disabled = true;
  try {
    const report = await api('/sessions/' + encodeURIComponent(sessionId) + '/process', {
      method: 'POST',
    });
    const failed = (report.jobs || []).filter((j) => j.status !== 'SUCCEEDED').length;
    finishTask(taskId, !failed,
      (report.jobs || []).length + t(' 个材料, ') + failed + t(' 个失败'));
    toast(t('整堂处理完成: ') + (report.jobs || []).length + t(' 个材料, ') + failed + t(' 个失败'),
      failed ? 'bad' : 'ok');
  } catch (err) {
    finishTask(taskId, false, err.code + ' ' + err.message);
    toast(t('整堂处理失败 [') + err.code + '] ' + err.message, 'bad');
  } finally {
    button.disabled = false;
    route();
  }
}

async function actionReview(courseId, knowledgeId, kind, button) {
  // 「拒绝」与「解决冲突」写的是**人工终态** —— 点错了要再走一次人工决策
  // 才能改回, 审核历史里还会留下一次本不存在的决策。两者都二次确认。
  // 「确认」与「保持未验证」是正向路径, 不弹窗 (判据见 confirmDestructive)。
  if (kind === 'reject' &&
      !confirmDestructive(t('确认要拒绝这个知识点吗？这会写入人工审核终态，只能再由一次人工决策改回。'))) {
    return;
  }
  if (kind === 'resolve' &&
      !confirmDestructive(t('确认要解决这个冲突吗？这会写入人工审核终态。'))) {
    return;
  }
  const noteEl = document.getElementById('review-note');
  const note = noteEl ? noteEl.value.trim() : '';
  const selected = Array.prototype.slice
    .call(document.querySelectorAll('input[name="selected_evidence"]:checked'))
    .map((input) => input.value);
  const body = {};
  if (note) body.note = note;
  if (kind === 'confirm' && selected.length) body.selected_evidence_ids = selected;
  if (kind === 'resolve') body.selected_evidence_ids = selected;
  const endpoint = { confirm: 'confirm', keep: 'keep-unverified', reject: 'reject', resolve: 'resolve-conflict' }[kind];
  button.disabled = true;
  try {
    const record = await api('/reviews/' + encodeURIComponent(knowledgeId) + '/' + endpoint, {
      method: 'POST', query: { course_id: courseId }, body,
    });
    toast(t('审核已记录: ') + record.decision, 'ok');
    route();
  } catch (err) {
    toast(t('审核失败 [') + err.code + '] ' + err.message, 'bad');
    const box = document.getElementById('review-result');
    if (box) {
      box.innerHTML = '<p class="pill pill-bad">' + esc(err.code) + '</p><p class="small">' +
        esc(err.message) + '</p>';
    }
  } finally {
    button.disabled = false;
  }
}

// Task 64: 出题动作。前端只负责发起与展示 —— 出题逻辑完全在
// domain/application 层，前端不构造题干、不挑选干扰项、不判断对错。
// "拒绝"是正常结果（未核验 / 证据冲突 / 没有可用陈述），必须如实展示原因，
// 而不是当成错误吞掉。
async function actionGenerateExercises(button) {
  const courseId = button.getAttribute('data-course') || state.courseId;
  const box = document.getElementById('generation-result');
  if (!courseId) { toast(t('缺少必要参数'), 'bad'); return; }
  button.disabled = true;
  if (box) box.innerHTML = '<p class="small muted">' + esc(t('gen.running')) + '</p>';
  try {
    const report = await api('/exercise-generation/batch', {
      method: 'POST',
      body: { course_id: courseId },
    });
    if (!box) return;
    const items = report.items || [];
    const refusals = report.refusals || [];
    const summary = '<p class="small"><span class="pill pill-ok">' +
      esc(t('gen.created')) + ' ' + esc(report.created) + '</span> ' +
      '<span class="pill pill-muted">' + esc(t('gen.reused')) + ' ' + esc(report.reused) + '</span> ' +
      '<span class="pill pill-warn">' + esc(t('gen.refused')) + ' ' + esc(report.refused) + '</span> ' +
      '<span class="muted">' + esc(t('gen.requested')) + ' ' + esc(report.requested) + '</span></p>';

    const createdList = items.length
      ? '<ul class="small">' + items.map((item) => (
          '<li>' + (item.banner ? '<span class="pill pill-warn">' + esc(t('gen.banner')) + '</span> ' : '') +
          exerciseLink(courseId, item.exercise_id, item.prompt || item.exercise_id) +
          ' <span class="tiny muted mono">' + esc(item.exercise_type || '') + '</span></li>'
        )).join('') + '</ul>'
      : '<p class="muted small">' + esc(t('gen.noEligible')) + '</p>';

    // 拒绝原因用 i18n 符号键渲染；未知原因回落到后端原文（绝不隐藏）。
    const refusalList = refusals.length
      ? '<details open><summary class="small">' + esc(t('gen.refusalHeader')) +
        ' (' + esc(refusals.length) + ')</summary><ul class="small">' +
        refusals.map((r) => {
          const reasonKey = 'gen.' + (r.reason || '');
          const reasonText = t(reasonKey);
          const reason = reasonText === reasonKey
            ? esc(String(r.reason || '')) + (r.detail ? ' — ' + esc(r.detail) : '')
            : esc(reasonText);
          return '<li>' + kpLink(courseId, r.knowledge_point_id, r.knowledge_point_id) +
            '<br><span class="tiny">' + reason + '</span>' +
            (r.recommended_action
              ? '<br><span class="tiny muted">' + esc(t('gen.recommended')) + ': ' +
                esc(r.recommended_action) + '</span>'
              : '') + '</li>';
        }).join('') + '</ul></details>'
      : '';

    box.innerHTML = summary + createdList + refusalList;
    toast(t('gen.resultOk') + (report.created || 0) + ' / ' + (report.requested || 0),
      report.refused ? 'warn' : 'ok');
  } catch (err) {
    if (box) {
      box.innerHTML = '<p class="pill pill-bad">' + esc(err.code) + '</p>' +
        '<p class="small">' + esc(err.message) + '</p>';
    }
  } finally {
    button.disabled = false;
  }
}

/**
 * 学生注册表单。
 *
 * 静态 HTML 里有表单不等于能提交 —— 用户报的第二个问题就是"注册学生只能靠
 * POST /api/students", 所以这条路径必须真的接上, 并且提交后立刻重绘 (与上传
 * 表单同一条路径): 新学生马上出现在列表里, 不需要用户手动刷新。
 *
 * 重复注册同一个 student_id 是幂等的 (后端 200); 但**换一个姓名**重复注册会被
 * 后端判成 CONFLICT。前端不自己造这条判定, 也不自己编错误文案 —— 错误来自
 * 后端 JSON。
 */
function wireStudentForm() {
  const form = document.getElementById('student-form');
  if (!form) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const idInput = form.querySelector('input[name="student_id"]');
    const nameInput = form.querySelector('input[name="display_name"]');
    const studentId = (idInput.value || '').trim();
    const displayName = (nameInput.value || '').trim();
    if (!studentId) { toast(t('student.idRequired'), 'bad'); return; }
    const body = {
      course_id: form.getAttribute('data-course'),
      student_id: studentId,
    };
    // 姓名是可选的。空字符串**不发送** —— 后端把 None 当"没给", 把 '' 当
    // "给了个空名", 两者在幂等判定上不是一回事。
    if (displayName) body.display_name = displayName;
    button.disabled = true;
    try {
      const dto = await api('/students', { method: 'POST', body });
      toast(t('student.registeredOk') + (dto.display_name || dto.student_id), 'ok');
      route();
    } catch (err) {
      toast(t('student.registerFailed') + err.code + '] ' + err.message, 'bad');
    } finally {
      button.disabled = false;
    }
  });
}
