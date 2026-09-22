/*
 * 学习视图 (``#/learn``): 知识点学习 / 练习 / 出题依据 / 判分。
 */
'use strict';

function learnStateLabel(value) {
  const key = 'learn.state.' + String(value || '');
  const label = t(key);
  return label === key ? String(value || '') : label;
}

function learnEvaluationLabel(status) {
  const value = String(status || '').toLowerCase();
  if (value === 'correct') return t('learn.evaluationCorrect');
  if (value === 'incorrect') return t('learn.evaluationIncorrect');
  if (value === 'unsupported') return t('learn.evaluationUnsupported');
  return String(status || '');
}

function learnKpHref(courseId, kpId) {
  return '#/courses/' + encodeURIComponent(courseId) +
    '/learn/' + encodeURIComponent(kpId);
}

/** 两个 truth axis 各显示一个徽章, 中间不做任何"换算"。 */
function learnTruthAxes(kp) {
  return '<dl class="kv">' +
    '<dt>' + esc(t('learn.validationStatus')) + '</dt>' +
    '<dd>' + pill(statusLabel(kp.validation_status), kp.validation_status) + '</dd>' +
    '<dt>' + esc(t('learn.reviewStatus')) + '</dt>' +
    '<dd>' + pill(statusLabel(kp.review_status), kp.review_status) + '</dd>' +
    '</dl>' +
    '<p class="tiny muted">' + esc(t('learn.twoAxesNote')) + '</p>';
}

function learnProgressPanel(progress) {
  const byState = (progress && progress.by_state) || {};
  const excluded = (progress && progress.excluded) || {};
  const stateKeys = ['not_started', 'exposed', 'practicing', 'reviewing'];
  return '<div class="card"><div class="card-head"><h2>' + t('learn.progress') + '</h2></div>' +
    '<div class="grid grid-4">' + stateKeys.map((key) =>
      '<div class="stat"><div class="stat-label">' + esc(learnStateLabel(key)) + '</div>' +
      '<div class="stat-value">' + esc(byState[key] || 0) + '</div></div>'
    ).join('') + '</div>' +
    (Object.keys(excluded).length
      ? '<details class="small" style="margin-top:12px"><summary>' +
        esc(t('learn.excluded')) + '</summary>' +
        '<ul class="small">' + Object.keys(excluded).sort().map((reason) => {
          const ids = excluded[reason] || [];
          if (!ids.length) return '';
          return '<li>' + esc(t('learn.excluded.' + reason)) + '（' + esc(ids.length) + '）：' +
            ids.slice(0, 10).map((id) => esc(id)).join(', ') +
            (ids.length > 10 ? ' …' : '') + '</li>';
        }).join('') + '</ul></details>'
      : '') +
    '</div>';
}

function learnTaskCard(title, task) {
  if (!task) return '';
  return '<div class="card"><div class="card-head"><h2>' + esc(title) + '</h2></div>' +
    '<p><a href="' + learnKpHref(task.course_id, task.knowledge_point_id) + '">' +
    esc(task.title || task.knowledge_point_id) + '</a></p>' +
    '<dl class="kv">' +
    '<dt>' + esc(t('learn.nextAction')) + '</dt>' +
    '<dd>' + esc(task.next_action || task.next_event || dash(null)) + '</dd>' +
    (task.unmet_prerequisite_ids && task.unmet_prerequisite_ids.length
      ? '<dt>' + esc(t('learn.unmetPrerequisites')) + '</dt>' +
        '<dd>' + task.unmet_prerequisite_ids.map((id) => esc(id)).join(', ') + '</dd>'
      : '') +
    '</dl></div>';
}

/**
 * 「开始今天的学习」—— 今天的入口页。
 *
 * 后端没有可用任务时**如实说明** (``No learning task available.``),
 * 不显示任何"你已经学完了 / 你已掌握"的结论 —— 那会是一个产品替学生做
 * 出的判断, 而系统只被允许报告事实。
 */
async function pageLearn() {
  markActiveNav('#/today');
  const courseId = await requireCourse();
  const students = (await api('/students', { query: { course_id: courseId } })).students || [];
  if (!students.length) {
    setView('<div class="page-head"><h1>' + esc(t('learn.title')) + '</h1></div>' +
      '<div class="card">' + emptyState(t('learn.noStudent')) + '</div>');
    return;
  }
  const studentId = state.studentId &&
    students.some((s) => s.student_id === state.studentId)
    ? state.studentId : students[0].student_id;
  setStudent(studentId);

  const data = await api(
    '/students/' + encodeURIComponent(studentId) + '/learning/start',
    { query: { course_id: courseId, lang: state.lang } }
  );

  const head = '<div class="page-head"><h1>' + esc(t('learn.title')) + '</h1>' +
    '<p class="subtitle">' + esc(studentId) + ' · ' + esc(courseLabel(courseId)) + '</p></div>';

  const body = data.has_task
    ? learnTaskCard(t('learn.currentTask'), data.current_task) +
      learnTaskCard(t('learn.nextTask'), data.next_task)
    : '<div class="card">' + emptyState(t('learn.noTask')) + '</div>';

  setView(head + body + learnProgressPanel(data.progress) +
    '<p class="small"><a href="#/today">' + esc(t('learn.backToToday')) + '</a></p>');
}

/** 知识点学习页: 证据 -> 解释 -> 状态 -> 练习。 */
async function pageLearnKnowledge(courseId, knowledgeId) {
  // 这一页属于「今天的学习流程」, 顶栏高亮跟着流程入口 `#/learn` 走。
  //
  // 不能省: 顶栏的 active 完全由页面函数设置 (index.html 里没有任何硬编码),
  // 谁不设谁就继承上一页 —— 用户从「知识点」点进来, 高亮会一直指着「知识点」;
  // 在 `#/learn/<kp>` 上刷新, 则变成整条导航都没有高亮。两种都是"高亮的位置
  // 不是用户所在的位置"。
  markActiveNav('#/today');
  const students = (await api('/students', { query: { course_id: courseId } })).students || [];
  if (!students.length) {
    setView('<div class="card">' + emptyState(t('learn.noStudent')) + '</div>');
    return;
  }
  const studentId = state.studentId &&
    students.some((s) => s.student_id === state.studentId)
    ? state.studentId : students[0].student_id;
  setStudent(studentId);
  // 走 setCourse() 而不是直接写 localStorage: 否则 state.courseId 会与
  // 持久化的值脱钩, 侧边栏高亮也就跟着停在上一门课上。
  // 这里用 setRouteCourse(): courseId 来自路由 (`#/courses/<id>/learn/<kp>`)。
  setRouteCourse(courseId);

  const data = await api(
    '/students/' + encodeURIComponent(studentId) + '/learning/knowledge/' +
      encodeURIComponent(knowledgeId),
    { query: { course_id: courseId, lang: state.lang, language: state.lang } }
  );
  setView(renderLearnKnowledge(courseId, studentId, data));
  await wireLearnForms(courseId, studentId, knowledgeId);
}

function renderLearnKnowledge(courseId, studentId, data) {
  const kp = data.knowledge_point || {};
  const activity = data.student_activity || {};
  const explanation = data.grounded_explanation || {};
  // 知识点 ID 一律取自后端响应, 不用调用方传进来的那一个 —— 两者在
  // 重定向/别名的情况下可能不同, 而按钮必须操作后端认可的那个 ID。
  const knowledgeId = kp.knowledge_id || '';

  const evidenceHtml = data.evidence_available
    ? '<ul class="small">' + (data.evidence || []).slice(0, 20).map((row) =>
        '<li>' + esc(row.quote || row.text || row.content || row.evidence_id || '') +
        (row.material_filename || row.filename
          ? ' <span class="tiny muted">' + esc(row.material_filename || row.filename) + '</span>'
          : '') + '</li>'
      ).join('') + '</ul>'
    : '<p class="small muted">' + esc(t('learn.evidenceUnavailable')) + '</p>';

  const explanationText = explanation.text || explanation.explanation || '';
  const explanationHtml = '<div class="card"><div class="card-head"><h2>' +
    t('learn.groundedExplanation') + '</h2></div>' +
    (explanationText
      ? '<p>' + esc(explanationText) + '</p>'
      : '<p class="small muted">' + esc(t('learn.evidenceUnavailable')) + '</p>') +
    '<p class="tiny muted">' + esc(t('learn.groundedNote')) + '</p></div>';

  const prereqIds = data.prerequisites || [];
  const unmet = data.unmet_prerequisites || [];

  const activityHtml = '<div class="card"><div class="card-head"><h2>' +
    t('learn.studentActivity') + '</h2></div>' +
    '<div class="grid grid-4">' +
    '<div class="stat"><div class="stat-label">' + esc(t('learn.exposureCount')) + '</div>' +
    '<div class="stat-value">' + esc(activity.exposure_count || 0) + '</div></div>' +
    '<div class="stat"><div class="stat-label">' + esc(t('learn.practiceCount')) + '</div>' +
    '<div class="stat-value">' + esc(activity.practice_count || 0) + '</div></div>' +
    '<div class="stat"><div class="stat-label">' + esc(t('learn.answerCount')) + '</div>' +
    '<div class="stat-value">' + esc(activity.answer_count || 0) + '</div></div>' +
    '<div class="stat"><div class="stat-label">' + esc(t('learn.correctCount')) + '</div>' +
    '<div class="stat-value">' + esc(activity.correct_count || 0) + '</div></div>' +
    '<div class="stat"><div class="stat-label">' + esc(t('learn.incorrectCount')) + '</div>' +
    '<div class="stat-value">' + esc(activity.incorrect_count || 0) + '</div></div>' +
    '</div>' +
    '<p class="small muted">' + esc(t('learn.activityNote')) + '</p></div>';

  return '<div class="page-head"><h1>' + esc(kp.title || knowledgeId) + '</h1>' +
    '<p class="subtitle">' + esc(kp.knowledge_id || knowledgeId) + '</p></div>' +

    '<div class="card"><div class="card-head"><h2>' + t('learn.knowledgeTitle') + '</h2>' +
    '<span>' + pill(learnStateLabel(data.student_state), data.student_state) + '</span></div>' +
    (kp.content ? '<p>' + esc(kp.content) + '</p>' : '') +
    (kp.original_terms && kp.original_terms.length
      ? '<p class="tiny muted">' + kp.original_terms.map((v) => esc(v)).join(' · ') + '</p>'
      : '') +
    learnTruthAxes(kp) + '</div>' +

    '<div class="card"><div class="card-head"><h2>' + t('learn.evidence') + '</h2></div>' +
    evidenceHtml +
    (data.evidence_note ? '<p class="tiny muted">' + esc(data.evidence_note) + '</p>' : '') +
    '</div>' +

    explanationHtml +

    '<div class="card"><div class="card-head"><h2>' + t('learn.prerequisites') + '</h2></div>' +
    (prereqIds.length
      ? '<ul class="small">' + prereqIds.map((id) =>
          '<li><a href="' + learnKpHref(courseId, id) + '">' + esc(id) + '</a>' +
          (unmet.includes(id) ? ' ' + pill(t('learn.unmetPrerequisites'), 'PENDING') : '') +
          '</li>'
        ).join('') + '</ul>'
      : '<p class="small muted">' + esc(t('learn.noPrerequisite')) + '</p>') +
    '</div>' +

    activityHtml +

    '<div class="card" id="learn-exercise-card"><div class="card-head"><h2>' +
    t('learn.exercise') + '</h2></div>' +
    '<button type="button" id="learn-exercise-btn" data-course="' + esc(courseId) +
    '" data-student="' + esc(studentId) + '" data-knowledge="' + esc(knowledgeId) + '">' +
    esc(t('learn.startExercise')) + '</button>' +
    '<div id="learn-exercise-body"></div></div>' +

    '<p class="small"><a href="#/learn">' + esc(t('learn.backToToday')) + '</a></p>';
}

/** 接线练习按钮与作答表单（每次渲染后调用一次）。 */
async function wireLearnForms(courseId, studentId, knowledgeId) {
  const btn = document.getElementById('learn-exercise-btn');
  if (!btn) return;
  const body = document.getElementById('learn-exercise-body');
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    try {
      const data = await api(
        '/students/' + encodeURIComponent(studentId) + '/learning/knowledge/' +
          encodeURIComponent(knowledgeId) + '/exercise',
        { query: { course_id: courseId } }
      );
      renderLearnExercise(courseId, studentId, knowledgeId, data);
    } catch (err) {
      if (err instanceof ApiError) showBanner(err.message, 'bad');
      else showBanner(String(err && err.message || err), 'bad');
    } finally {
      btn.disabled = false;
    }
  });
}

function renderLearnExercise(courseId, studentId, knowledgeId, data) {
  const body = document.getElementById('learn-exercise-body');
  if (!body) return;
  const exercise = data.exercise || {};
  if (!exercise.exercise_id) {
    body.innerHTML = emptyState(t('learn.evidenceUnavailable'));
    return;
  }
  const choices = exercise.choices || [];
  // 复用既有练习页的控件与样式 (.choices / label.choice / label.field),
  // 不新造类名 —— 新造类名会渲染成没有样式的裸控件。
  const input = choices.length
    ? '<div class="choices">' + choices.map((choice) =>
        '<label class="choice"><input type="radio" name="learn-choice" value="' +
        esc(choice.choice_id) + '"> <span>' +
        esc(choice.text || choice.label || choice.choice_id) + '</span></label>'
      ).join('') + '</div>'
    : '<label class="field"><span>' + esc(t('learn.yourAnswer')) + '</span>' +
      '<input type="text" id="learn-answer-input" autocomplete="off"></label>';

  body.innerHTML =
    '<p class="mono small">' + esc(exercise.exercise_id) + '</p>' +
    '<p>' + esc(exercise.prompt || '') + '</p>' +
    (data.reused ? '<p class="tiny muted">' + esc(t('learn.activityNote')) + '</p>' : '') +
    '<form id="learn-answer-form" data-course="' + esc(courseId) +
    '" data-student="' + esc(studentId) + '" data-exercise="' + esc(exercise.exercise_id) + '">' +
    input +
    '<p><button type="submit">' + esc(t('learn.submitAnswer')) + '</button></p></form>' +
    '<div id="learn-evaluation"></div>' +
    renderLearnGrounding(courseId, data.grounding);

  const form = document.getElementById('learn-answer-form');
  if (form) {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const choice = form.querySelector('input[name="learn-choice"]:checked');
      const text = document.getElementById('learn-answer-input');
      const value = choice ? choice.value : (text ? text.value : '');
      try {
        const result = await api(
          '/students/' + encodeURIComponent(studentId) + '/learning/answer',
          {
            method: 'POST',
            body: {
              course_id: courseId,
              student_id: studentId,
              exercise_id: exercise.exercise_id,
              submitted_value: value,
            },
          }
        );
        renderLearnEvaluation(courseId, result);
      } catch (err) {
        if (err instanceof ApiError) showBanner(err.message, 'bad');
        else showBanner(String(err && err.message || err), 'bad');
      }
    });
  }
}

/** 出题依据链: Exercise -> KnowledgePoint -> Evidence -> Material。 */
function renderLearnGrounding(courseId, grounding) {
  if (!grounding) return '';
  const unresolved = grounding.unresolved || [];
  return '<div class="card"><div class="card-head"><h2>' + t('learn.grounding') + '</h2></div>' +
    ((grounding.knowledge_points || []).length
      ? '<ul class="small">' + grounding.knowledge_points.map((kp) =>
          '<li><a href="' + learnKpHref(courseId, kp.knowledge_id) + '">' +
          esc(kp.title || kp.knowledge_id) + '</a></li>'
        ).join('') + '</ul>'
      : '') +
    ((grounding.evidence || []).length
      ? '<ul class="small">' + grounding.evidence.slice(0, 10).map((ev) =>
          '<li>' + esc(ev.quote || ev.evidence_id || '') + '</li>'
        ).join('') + '</ul>'
      : '') +
    (unresolved.length
      ? '<p class="small muted">' + esc(t('learn.evidenceUnavailable')) + ' ' +
        unresolved.slice(0, 5).map((v) => esc(v)).join(', ') + '</p>'
      : '') +
    '</div>';
}

/** 评估是**事实**; 它不推进学习状态 (铁律 5)。 */
function renderLearnEvaluation(courseId, result) {
  const box = document.getElementById('learn-evaluation');
  if (!box) return;
  const state = result.student_state || {};
  const activity = (state && !Array.isArray(state)) ? state : {};
  box.innerHTML =
    '<div class="card"><div class="card-head"><h2>' + t('learn.evaluation') + '</h2>' +
    '<span>' + pill(learnEvaluationLabel(result.evaluation_status),
      String(result.evaluation_status || '').toUpperCase()) + '</span></div>' +
    '<dl class="kv">' +
    '<dt>' + esc(t('learn.yourAnswer')) + '</dt>' +
    '<dd>' + esc((result.answer || {}).submitted_value || '') + '</dd>' +
    '<dt>' + esc(t('learn.nextAction')) + '</dt>' +
    '<dd>' + esc(state.next_event || dash(null)) + '</dd>' +
    '<dt>' + esc(t('learn.answerCount')) + '</dt>' +
    '<dd>' + esc(activity.answer_count || 0) + '</dd>' +
    '</dl>' +
    '<p class="small muted">' + esc(t('learn.evaluationNote')) + '</p></div>' +
    (result.next_task
      ? learnTaskCard(t('learn.nextTask'), result.next_task)
      : '');
  // 状态可能变了 (练习/复习事件), 让顶部徽章跟上 —— 但**只显示后端返回的事实**。
  const badge = document.querySelector('.page-head + .card .card-head span');
  if (badge && state.state) badge.textContent = learnStateLabel(state.state);
}

// ---- 考前复习模式 (Task 67) -----------------------------------------------

/**
 * 桶的中文/西语/加语标签。
 *
 * 桶名 (blocked / attention / ready) 是后端契约的一部分, 前端只负责翻译 ——
 * 不在客户端"重新判断"某个知识点属于哪个桶。判断依据 (证据状态、人工审核、
 * 学习状态) 全部来自后端, 前端不让它变暗。
 */
