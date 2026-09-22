/*
 * 练习列表 (``#/exercises``) / 单题作答 / 出题依据链 / 判分面板。
 */
'use strict';

async function pageExercises() {
  markActiveNav('#/exercises');
  const courseId = await requireCourse();
  const students = (await api('/students', { query: { course_id: courseId } })).students || [];
  const kps = (await api('/knowledge', { query: { course_id: courseId } })).knowledge_points || [];

  // Task 64: 出题前提是知识点已核验。这里如实分开显示, 而不是把
  // 不可出题的知识点藏起来 —— "为什么不能出题"本身就是信息。
  const generatable = kps.filter((kp) => kp.validation_status === 'supported');
  const blocked = kps.filter((kp) => kp.validation_status !== 'supported');

  const generation = '<div class="card"><div class="card-head"><h2>' +
    t('gen.title') + '</h2>' +
    '<button id="generate-batch" data-course="' + esc(courseId) + '">' +
    t('gen.generate') + '</button></div>' +
    '<p class="small muted">' + t('gen.note') + '</p>' +
    '<p class="small">' + t('gen.eligible') + ': ' + esc(generatable.length) + ' · ' +
    t('gen.blocked') + ': ' + esc(blocked.length) + '</p>' +
    (blocked.length
      ? '<details><summary class="small">' + t('gen.blockedWhy') + '</summary>' +
        '<ul class="small">' + blocked.slice(0, 20).map((kp) => (
          '<li>' + kpLink(courseId, kp.knowledge_id, kp.title) + ' ' +
          pill(kp.validation_status, kp.validation_status) + '</li>'
        )).join('') + '</ul></details>'
      : '') +
    '<div id="generation-result"></div>' +
    '</div>';

  if (!students.length) {
    setView('<div class="page-head"><h1>' + esc(t('ex.title')) + '</h1></div>' +
      '<div class="card">' + noStudentsCard() + '</div>' +
      generation);
    return;
  }
  const studentId = students[0].student_id;
  const data = await api('/students/' + encodeURIComponent(studentId) + '/exercises', {
    query: { course_id: courseId },
  });
  const items = data.exercises || [];
  setView(
    '<div class="page-head"><h1>' + esc(t('ex.title')) + '</h1>' +
    '<p class="subtitle">' + esc(data.student_id) + ' · ' +
    esc(t('ex.answered')) + ' ' + esc(data.answered) + ' / ' + esc(data.total) + '</p></div>' +
    '<div class="card">' +
    (items.length
      ? '<table class="data">' + tableCaption(t('ex.title')) + '<thead><tr><th scope="col">' + esc(t('ex.prompt')) +
        '</th><th scope="col">' + esc(t('ex.type')) + '</th><th scope="col">' +
        esc(t('ex.knowledgePoints')) + '</th><th scope="col">' + esc(t('ex.prerequisites')) +
        '</th><th scope="col">' + esc(t('common.status')) + '</th></tr></thead><tbody>' +
        items.map((item) => (
          '<tr><td>' + exerciseLink(courseId, item.exercise_id, item.prompt) + '</td>' +
          '<td><span class="pill pill-muted">' + esc(item.exercise_type) + '</span></td>' +
          '<td>' + ((item.knowledge_points || []).map((kp) =>
            kpLink(courseId, kp.knowledge_id, kp.title)).join('<br>') ||
            esc(t('common.none'))) + '</td>' +
          '<td class="tiny">' + ((item.prerequisites || []).map((id) =>
            kpLink(courseId, id)).join('<br>') || esc(t('common.none'))) + '</td>' +
          '<td>' + (item.submitted
            ? pill(t('ex.answered'), 'COMPLETED') +
              (item.evaluation_status
                ? '<br>' + pill(statusLabel(item.evaluation_status), String(item.evaluation_status).toUpperCase()) +
                  ' <span class="tiny muted">' + esc(fmtNumber(item.score)) + '</span>'
                : '')
            : pill(t('ex.unanswered'), 'AVAILABLE')) + '</td></tr>'
        )).join('') + '</tbody></table>'
      : emptyState(t('ex.noExercises'))) +
    '</div>' +
    generation
  );
}

async function pageExercise(courseId, exerciseId, studentId) {
  markActiveNav('#/exercises');
  setRouteCourse(courseId);
  // 路由里的学生段优先; 没有就退回"当前学生" —— 但**必须先确认它属于这门课**。
  //
  // 旧写法是 `studentId || state.studentId`, 而 state.studentId 是**全局**的
  // (存储键 ca.student, 不带课程前缀)。于是从今日页点开 B 课程的待作答练习
  // (那条 href 不带学生段) 时, 会以 A 课程记住的那个学生身份作答 ——
  // 而作答会写 StudentState, 也就是把成绩记到了别人名下。
  //
  // 其它页面 (learn / mistakes / review / students) 早就是"先按课程校验再退回
  // 第一个"的写法, 这里当时漏了。
  const students = (await api('/students', { query: { course_id: courseId } })).students || [];
  const sid = studentId || (
    state.studentId && students.some((s) => s.student_id === state.studentId)
      ? state.studentId
      : (students[0] ? students[0].student_id : null)
  );
  if (!sid) {
    setView('<div class="card"><h1>' + esc(t('ex.title')) + '</h1>' +
      noStudentsCard() + '</div>');
    return;
  }
  setStudent(sid);
  const view = await api(
    '/students/' + encodeURIComponent(sid) + '/exercises/' + encodeURIComponent(exerciseId),
    { query: { course_id: courseId } }
  );

  const kpIds = view.knowledge_point_ids || [];
  const choices = view.choices || [];
  let answerControl;
  if (view.answer_format === 'choice' || view.answer_format === 'true_false') {
    answerControl = '<div class="choices">' + choices.map((choice) => (
      '<label class="choice"><input type="radio" name="answer_value" value="' +
      esc(choice.choice_id) + '"> <span>' + esc(choice.text) + '</span>' +
      ' <span class="tiny muted mono">' + esc(choice.choice_id) + '</span></label>'
    )).join('') + '</div><p class="tiny muted">' + esc(t('ex.chooseHint')) + '</p>';
  } else {
    answerControl = '<label class="field"><span>' + esc(t('ex.answer')) + '</span>' +
      '<input type="text" name="answer_value" autocomplete="off" ' +
      'placeholder="' + esc(t('ex.typeHint')) + '"></label>' +
      '<p class="tiny muted">' + esc(t('ex.typeHint')) + '</p>';
  }

  const evidenceBlock = (view.evidence || []).length
    ? view.evidence.map((ev) => (
        '<div class="trace-node" style="margin-bottom:8px">' +
        '<div class="node-kind">' + esc(ev.evidence_type) + ' · ' + esc(ev.language) +
        ' · ' + esc(ev.confidence) + '</div>' +
        originalBlock(ev.content, ev.language) +
        '<div class="tiny muted mono">' + esc(sourceLocation(ev.source)) + '</div></div>'
      )).join('')
    : emptyState(t('common.none'));

  setView(
    '<div class="page-head"><div class="crumbs">' +
    '<a href="#/">' + esc(t('nav.overview')) + '</a> / ' +
    '<a href="#/exercises">' + esc(t('ex.title')) + '</a> / ' +
    esc(view.exercise_type) + '</div>' +
    '<h1>' + esc(view.prompt) + '</h1>' +
    '<p class="subtitle"><span class="mono tiny">' + esc(view.exercise_id) + '</span> · ' +
    esc(t('ex.type')) + ': <span class="pill pill-muted">' + esc(view.exercise_type) +
    '</span> · ' + esc(t('ex.difficulty')) + ': ' + dash(view.difficulty) + '</p>' +
    // 作答身份**必须写在页面上** —— 这一页会写 StudentState, 而学生段是可以
    // 缺省的 (链接不带学生段时会退回当前课程的第一个学生)。不写出来, 用户
    // 就没有任何办法知道自己是以谁的身份在答题。
    '<p class="small muted">' + esc(t('学生')) + ': <span class="mono tiny">' +
    esc(sid) + '</span></p></div>' +

    (view.evidence_complete ? '' :
      '<div class="banner banner-bad">' + esc(t('ex.brokenEvidence')) + ': ' +
      esc((view.unresolved_evidence_ids || []).join(', ')) + '</div>') +

    '<div class="grid grid-2">' +
    '<div class="card"><div class="card-head"><h2>' + esc(t('ex.answer')) + '</h2>' +
    (view.submitted
      ? '<span class="pill pill-ok">' + esc(t('ex.yourAnswer')) + '</span>'
      : '<span class="pill pill-warn">' + esc(t('ex.notAnswered')) + '</span>') +
    '</div>' +
    '<form id="answer-form" data-course="' + esc(courseId) + '" data-student="' + esc(sid) +
    '" data-exercise="' + esc(view.exercise_id) + '">' +
    (view.submitted
      ? '<dl class="kv"><dt>' + esc(t('ex.yourAnswer')) + '</dt><dd>' +
        originalBlock(view.submitted_value, null) + '</dd>' +
        '<dt>answer_id</dt><dd class="mono tiny">' + esc(view.answer_id) + '</dd>' +
        '<dt>submitted_at</dt><dd class="mono tiny">' + esc(view.submitted_at) + '</dd>' +
        '<dt>sequence</dt><dd>' + esc(view.sequence) + '</dd></dl>' +
        '<details><summary class="small">' + esc(t('ex.resubmit')) + '</summary>' +
        answerControl +
        '<div class="actions"><button class="primary" type="submit">' +
        esc(t('ex.resubmit')) + '</button></div></details>'
      : answerControl +
        '<div class="actions"><button class="primary" type="submit">' +
        esc(t('ex.submit')) + '</button></div>') +
    '</form>' +
    '<div id="answer-result"></div>' +
    '</div>' +

    '<div class="card"><h2>' + esc(t('ex.knowledgePoints')) + '</h2>' +
    ((view.knowledge_points || []).length
      ? '<ul class="small">' + view.knowledge_points.map((kp) =>
          '<li>' + kpLink(courseId, kp.knowledge_id, kp.title) + ' ' +
          pill(kp.validation_status) + pill(kp.review_status) + '</li>').join('') + '</ul>'
      : emptyState(t('common.none'))) +
    '<h3>' + esc(t('ex.prerequisites')) + '</h3>' +
    ((view.prerequisites || []).length
      ? '<ul class="small">' + view.prerequisites.map((id) =>
          '<li>' + kpLink(courseId, id) + '</li>').join('') + '</ul>'
      : '<p class="muted small">' + esc(t('common.none')) + '</p>') +
    '</div>' +
    '</div>' +

    '<div class="card"><div class="card-head"><h2>' + esc(t('ex.basis')) + '</h2>' +
    '<span class="small muted">' + esc((view.evidence || []).length) + ' ' +
    esc(t('ex.evidenceCount')) + '</span></div>' +
    evidenceBlock +
    '</div>' +

    renderGroundingChain(view, courseId) +

    renderEvaluationPanel(view) +

    (kpIds.length
      ? '<p class="small muted">' + esc(t('ex.notFactVerification')) + '</p>'
      : '')
  );
  wireAnswerForm();
  // 依据链是补充信息, 不该阻塞题目本身的渲染。但 promise 必须留引用,
  // 否则 (a) 未处理的 rejection 会静默丢失, (b) UI 审计无法等到渲染结束。
  __lastGrounding = loadGroundingChain(courseId, exerciseId);
}

// Task 64: 出题依据链。练习 → 知识点 → 证据 → 材料。
// 链路由后端拼装（exercise_grounding），前端不去猜、不去补——
// 解析不了的引用会被如实列在"未解析的引用"里，而不是被隐藏。
function renderGroundingChain(view, courseId) {
  return '<div class="card" id="grounding-card" data-course="' + esc(courseId) +
    '" data-exercise="' + esc(view.exercise_id) + '">' +
    '<div class="card-head"><h2>' + esc(t('ground.title')) + '</h2>' +
    '<span class="small muted mono">' + esc(t('ground.chain')) + '</span></div>' +
    '<p class="small muted">' + esc(t('ground.deterministic')) + '</p>' +
    '<div id="grounding-body"><p class="small muted">' + esc(t('common.loading')) + '</p></div>' +
    '</div>';
}

async function loadGroundingChain(courseId, exerciseId) {
  const body = document.getElementById('grounding-body');
  if (!body) return;
  try {
    // 只读的子资源路径一次算好再传进去。这样静态检查能一眼看出
    // 调用的是 /grounding 子资源, 而不是作者视角的 /exercises/{id} DTO
    // (后者带 correct_choice_id / expected_answer, 绝不能进浏览器)。
    const groundingPath = '/exercises/' + encodeURIComponent(exerciseId) + '/grounding';
    const chain = await api(groundingPath, {
      query: { course_id: courseId },
    });
    const kps = chain.knowledge_points || [];
    const evidence = chain.evidence || [];
    const materials = chain.materials || [];
    const unresolved = chain.unresolved || [];
    // 只有后端确实报告了生成器版本时才声称"模板生成"。
    const provenance = chain.generator_version
      ? esc(chain.generator_version) + (chain.template ? ' · ' + esc(chain.template) : '')
      : esc(t('ground.handAuthored'));

    body.innerHTML =
      '<p class="small">' +
      (chain.complete
        ? '<span class="pill pill-ok">' + esc(t('ground.complete')) + '</span>'
        : '<span class="pill pill-warn">' + esc(t('ground.incomplete')) + '</span>') +
      ' <span class="muted">' + esc(t('ground.kind')) + ': ' + provenance + '</span></p>' +

      '<h3>' + esc(t('ground.knowledge')) + ' (' + esc(kps.length) + ')</h3>' +
      (kps.length
        ? '<ul class="small">' + kps.map((kp) => (
            '<li>' + kpLink(courseId, kp.knowledge_id, kp.title) + ' ' +
            pill(kp.validation_status) + pill(kp.review_status) + '</li>'
          )).join('') + '</ul>'
        : '<p class="muted small">' + esc(t('common.none')) + '</p>') +

      '<h3>' + esc(t('ground.evidence')) + ' (' + esc(evidence.length) + ')</h3>' +
      (evidence.length
        ? evidence.map((ev) => (
            '<div class="trace-node" style="margin-bottom:8px">' +
            '<div class="node-kind">' + esc(ev.evidence_type) + ' · ' + esc(ev.language) +
            t(' · 置信度 ') + esc(ev.confidence) + '</div>' +
            originalBlock(ev.content, ev.language) +
            '<div class="tiny muted mono">' + esc(sourceLocation(ev.source)) + '</div>' +
            (ev.material
              ? '<div class="small">' + esc(t('ground.material')) + ': ' +
                esc(ev.material.filename || ev.material.material_id) + '</div>'
              : '') +
            '</div>'
          )).join('')
        : '<p class="muted small">' + esc(t('common.none')) + '</p>') +

      '<h3>' + esc(t('ground.material')) + ' (' + esc(materials.length) + ')</h3>' +
      (materials.length
        ? '<ul class="small">' + materials.map((m) => (
            '<li class="mono tiny">' + esc(m.filename || m.material_id) +
            ' <span class="muted">' + esc(m.material_type || '') + ' · ' +
            esc(m.language || '') + '</span></li>'
          )).join('') + '</ul>'
        : '<p class="muted small">' + esc(t('common.none')) + '</p>') +

      (unresolved.length
        ? '<div class="banner banner-bad">' + esc(t('ground.unresolved')) + ': ' +
          esc(unresolved.join(', ')) + '</div>'
        : '');
  } catch (err) {
    body.innerHTML = '<p class="pill pill-bad">' + esc(err.code) + '</p>' +
      '<p class="small">' + esc(err.message) + '</p>';
  }
}

function exerciseLink(courseId, exerciseId, label) {
  return '<a href="#/courses/' + encodeURIComponent(courseId) + '/exercises/' +
    encodeURIComponent(exerciseId) + '">' + esc(label || exerciseId) + '</a>';
}

/** 评估面板: score / status / feedback / knowledge_point / evidence (Task 41)。 */
function renderEvaluationPanel(view) {
  const evaluation = view.evaluation;
  const body = evaluation
    ? '<dl class="kv">' +
      '<dt>' + esc(t('ex.score')) + '</dt><dd>' + esc(fmtNumber(evaluation.score)) + '</dd>' +
      '<dt>' + esc(t('ex.status')) + '</dt><dd>' +
        pill(statusLabel(evaluation.status), String(evaluation.status).toUpperCase()) + '</dd>' +
      '<dt>' + esc(t('ex.feedback')) + '</dt><dd>' +
        (evaluation.feedback ? esc(evaluation.feedback) : '<span class="muted">—</span>') + '</dd>' +
      '<dt>' + esc(t('ex.evaluator')) + '</dt><dd class="mono tiny">' +
        esc(evaluation.evaluator_version) + '</dd>' +
      '<dt>evaluation_id</dt><dd class="mono tiny">' +
        esc(evaluation.evaluation_id) + '</dd>' +
      '<dt>' + esc(t('ex.knowledgePoints')) + '</dt><dd>' +
        ((evaluation.knowledge_points || []).map((kp) =>
          kpLink(view.course_id, kp.knowledge_id, kp.title)).join('<br>') ||
          esc(t('common.none'))) + '</dd>' +
      '</dl>' +
      '<h3>' + esc(t('ex.expected')) + '</h3>' +
      (evaluation.expected
        ? '<dl class="kv">' +
          (evaluation.expected.correct_choice_id
            ? '<dt>correct_choice_id</dt><dd class="mono">' +
              esc(evaluation.expected.correct_choice_id) + '</dd>' : '') +
          (evaluation.expected.expected_answer
            ? '<dt>expected_answer</dt><dd>' +
              originalBlock(evaluation.expected.expected_answer, null) + '</dd>' : '') +
          ((evaluation.expected.accepted_answers || []).length
            ? '<dt>accepted_answers</dt><dd>' +
              evaluation.expected.accepted_answers.map((a) =>
                '<span class="pill pill-muted">' + esc(a) + '</span>').join(' ') + '</dd>'
            : '') +
          (evaluation.expected.explanation
            ? '<dt>explanation</dt><dd>' +
              originalBlock(evaluation.expected.explanation, null) + '</dd>' : '') +
          '</dl>'
        : '<p class="muted small">' + esc(t('common.none')) + '</p>') +
      '<p class="small muted">' + esc(t('ex.notFactVerification')) + '</p>'
    : (view.answer_key_withheld
        ? '<p class="empty">' + esc(t('ex.noEvaluation')) + '</p>' +
          '<p class="small muted">' + esc(t('ex.answerKeyWithheld')) + '</p>'
        : '<p class="empty">' + esc(t('ex.noEvaluation')) + '</p>');

  return '<div class="card" id="evaluation-panel"><div class="card-head"><h2>' +
    esc(t('ex.evaluation')) + '</h2>' +
    '<span class="tiny muted">Task 32 · evaluation ≠ fact verification</span></div>' +
    body + '</div>';
}

function wireAnswerForm() {
  const form = document.getElementById('answer-form');
  if (!form) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const courseId = form.getAttribute('data-course');
    const studentId = form.getAttribute('data-student');
    const exerciseId = form.getAttribute('data-exercise');
    const selected = form.querySelector('input[name="answer_value"]:checked');
    const text = form.querySelector('input[type="text"][name="answer_value"]');
    const value = selected ? selected.value : (text ? text.value : '');
    if (!value || !String(value).trim()) { toast(t('ex.chooseHint'), 'bad'); return; }
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      // 提交走 Task 32 的 /api/answers —— UI 不实现任何判分逻辑。
      await api('/answers', {
        method: 'POST',
        body: {
          course_id: courseId,
          student_id: studentId,
          exercise_id: exerciseId,
          submitted_value: String(value),
        },
      });
      toast(t('ex.evaluation') + ' ✓', 'ok');
      await route();
    } catch (err) {
      toast('[' + err.code + '] ' + err.message, 'bad');
      const box = document.getElementById('answer-result');
      if (box) {
        box.innerHTML = '<p class="pill pill-bad">' + esc(err.code) + '</p>' +
          '<p class="small">' + esc(err.message) + '</p>';
      }
    } finally {
      button.disabled = false;
    }
  });
}
