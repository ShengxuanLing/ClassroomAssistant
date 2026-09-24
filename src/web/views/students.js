/*
 * 学生隐式支撑动作与跨页面共享渲染助手。
 * 学生列表 / 学生详情页面已删除；后端学生 API 仍由学习、练习、错题和复习页使用。
 */
'use strict';

// ---- 跨页面共享渲染助手 ---------------------------------------------------


function kpLink(courseId, knowledgeId, label) {
  return '<a href="#/courses/' + encodeURIComponent(courseId) + '/knowledge/' +
    encodeURIComponent(knowledgeId) + '">' + esc(label || knowledgeId) + '</a>';
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
