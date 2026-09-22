/*
 * 材料页 (``#/materials``) 与上传表单接线。
 */
'use strict';

async function pageMaterials() {
  markActiveNav('#/materials');
  const courseId = await requireCourse();
  // 材料列表 / 课堂列表 / 处理状态三个请求互不依赖, 并行发 —— 曾经是三次
  // 串行 await, 每次进这一页都要白等两跳本地往返 (P3-1)。
  const [materialPayload, sessionPayload, status] = await Promise.all([
    api('/materials', { query: { course_id: courseId } }),
    api('/sessions', { query: { course_id: courseId } }),
    api('/processing', { query: { course_id: courseId } }),
  ]);
  const materials = materialPayload.materials || [];
  // 课堂列表: 上传表单的选择器与材料列表的"课堂"列都要靠它把内容寻址的
  // session_id 翻成人话 (见 sessionLabel)。一次请求, 两处使用。
  const sessions = sessionPayload.sessions || [];
  const sessionById = {};
  sessions.forEach((s) => { sessionById[s.session_id] = s; });
  const sessionOptions = sessions.map((s) =>
    '<option value="' + esc(s.session_id) + '">' + esc(sessionLabel(s)) + '</option>'
  ).join('');
  const counts = status.by_status || {};
  const activeCounts = Object.keys(counts).sort().filter((k) => counts[k] > 0);

  const rows = materials.map((m) => (
    '<tr><td class="break-all">' + esc(m.filename) +
    '<br><span class="tiny muted mono">' + esc(m.material_id) + '</span></td>' +
    '<td class="small">' + esc(m.material_type || m.source_type || '—') +
    '<br><span class="tiny muted">' + esc(m.extension || '') + ' · ' + fmtBytes(m.size) + '</span></td>' +
    '<td>' + pill(m.processing_status) + (m.duplicate ? ' <span class="pill pill-info">' + t('重复') + '</span>' : '') +
    (m.error ? '<br><span class="tiny pill pill-bad">' + esc(m.error) + '</span>' : '') +
    // 成功但有话要说 (warning) + 成功但零证据: 都不改上面的状态 pill,
    // 只在下面各加一行 (见 app.js 的 warningRow / zeroEvidenceHint)。
    warningRow(m) + zeroEvidenceHint(m) + '</td>' +
    '<td class="small">' + (m.session_id
      ? esc(sessionLabel(sessionById[m.session_id], m.session_id)) : '—') + '</td>' +
    '<td class="small nowrap">' +
    '<button data-action="process-material" data-course="' + esc(courseId) + '" data-material="' + esc(m.material_id) + '">' + t('处理') + '</button> ' +
    '<button data-action="retry-material" data-course="' + esc(courseId) + '" data-material="' + esc(m.material_id) + '">' + t('重试') + '</button> ' +
    '<button data-action="material-evidence" data-course="' + esc(courseId) + '" data-material="' + esc(m.material_id) + '">' + t('证据') + '</button> ' +
    '<button data-action="material-digest" data-course="' + esc(courseId) + '" data-material="' + esc(m.material_id) + '">' + t('摘要') + '</button> ' +
    '<button data-action="ai-analyze" data-course="' + esc(courseId) + '" data-material="' + esc(m.material_id) + '">' + t('ai.analyze') + '</button>' +
    '</td></tr>'
  )).join('');

  setView(
    '<div class="page-head"><h1>' + t('材料') + '</h1>' +
    '<p class="subtitle">' + t('课程 ') + '<strong>' + esc(courseLabel(courseId)) + '</strong>' + t(' · 共 ') + esc(materials.length) +
    t(' 个 · ') + activeCounts.map((k) => esc(k) + ' ' + esc(counts[k])).join(' · ') + '</p></div>' +

    '<div class="card"><div class="card-head"><h2>' + t('上传材料') + '</h2></div>' +
    '<form id="upload-form" class="grid grid-3">' +
    '<label class="field"><span>' + t('文件') + '</span><input type="file" name="file" required></label>' +
    '<label class="field"><span>' + t('课堂 (可选)') + '</span>' +
    '<select name="session_id">' +
    '<option value="">' + t('不关联课堂') + '</option>' + sessionOptions +
    '</select>' +
    // 一堂课都没有时给出提示: 否则用户只看到一个只有"不关联课堂"的下拉,
    // 不知道课堂该从哪儿来 (课堂目前只能通过 POST /api/sessions 创建)。
    (sessions.length ? '' : '<span class="tiny muted">' + t('还没有课堂。') + '</span>') +
    '</label>' +
    '<label class="field"><span>' + t('语言 (可选)') + '</span><select name="language">' +
    '<option value="">' + t('自动 / 不指定') + '</option><option value="es">' + t('es · 西班牙语') + '</option>' +
    '<option value="ca">' + t('ca · 加泰罗尼亚语') + '</option><option value="zh">' + t('zh · 中文') + '</option>' +
    '</select></label>' +
    '<div class="actions"><button class="primary" type="submit">' + t('上传并登记') + '</button>' +
    '<span class="small muted">' + t('支持 pdf / docx / txt / md / 音频 / 图片。同名同内容只登记一次。') + '</span></div>' +
    '</form></div>' +

    '<div class="card"><div class="card-head"><h2>' + t('材料列表') + '</h2>' +
    '<span class="small muted">' + t('处理顺序按 material_id 升序，串行执行') + '</span></div>' +
    (materials.length
      ? '<table class="data">' + tableCaption(t('材料列表')) + '<thead><tr><th scope="col">' + t('文件') + '</th><th scope="col">' + t('类型') + '</th><th scope="col">' + t('状态') + '</th><th scope="col">' + t('课堂') + '</th>' +
        '<th scope="col">' + t('操作') + '</th></tr></thead><tbody>' + rows + '</tbody></table>'
      : emptyState(t('该课程还没有材料。'))) +
    '</div>' +
    '<div id="evidence-panel"></div>' +
    '<div id="digest-panel"></div>' +
    '<div id="ai-panel"></div>'
  );
}

// ---- 待审核 --------------------------------------------------------------


function wireUploadForm() {
  const form = document.getElementById('upload-form');
  if (!form) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const fileInput = form.querySelector('input[type="file"]');
    const file = fileInput.files && fileInput.files[0];
    if (!file) { toast(t('请选择文件'), 'bad'); return; }
    const data = new FormData();
    data.append('file', file, file.name);
    // 课堂是 <select> 不是 <input> —— 按 name 取, 不要写死 input[...],
    // 否则换控件类型时这里会静默变成 null.value 而崩在提交路径上。
    const sessionId = form.querySelector('[name="session_id"]').value.trim();
    const language = form.querySelector('select[name="language"]').value;
    if (sessionId) data.append('session_id', sessionId);
    if (language) data.append('language', language);
    button.disabled = true;
    // 上传**不**登记可恢复任务 (P3-2): 目标 id 是服务端在响应里给的, 而
    // "用户按 F5"恰好意味着那个响应还没到 —— 此刻没有任何 id 可存。
    const taskId = startTask(t('上传并登记') + ' · ' + file.name);
    try {
      const record = await api('/materials', {
        method: 'POST', query: { course_id: state.courseId }, body: data,
      });
      finishTask(taskId, true, record.filename);
      toast(t('已登记: ') + record.filename + (record.duplicate ? t(' (重复, 复用已有副本)') : ''), 'ok');
      form.reset();
      route();
    } catch (err) {
      finishTask(taskId, false, err.code + ' ' + err.message);
      toast(t('上传被拒绝 [') + err.code + '] ' + err.message, 'bad');
    } finally {
      button.disabled = false;
    }
  });
}

// ---- AI 语义分析 (TASK-76: 显式触发; TASK-77: 处理成功后自动触发) ----
// 进度可见, 失败可重试。自动触发的结果随 process 响应里的 job.ai 回来,
// 这里只负责把它画出来 —— 状态真相永远在后端。

function renderAiStages(report) {
  const stages = report.stages || [];
  if (!stages.length) return '';
  const items = stages.map((st) => {
    const done = st.state === 'done';
    const mark = done ? '✓' : '○';
    const cls = done ? 'pill-ok' : 'pill-warn';
    return '<li><span class="pill ' + cls + '">' + esc(mark) + '</span> ' +
      '<span class="mono tiny">' + esc(st.stage || '') + '</span>' +
      (st.detail ? ' <span class="tiny muted">' + esc(st.detail) + '</span>' : '') + '</li>';
  }).join('');
  return '<p class="small"><strong>' + t('ai.stages') + '</strong></p>' +
    '<ul class="small">' + items + '</ul>';
}

function aiAutoLine(ai) {
  if (!ai || ai.status === 'disabled') return '';
  if (ai.status === 'completed') {
    return '<span class="tiny muted">' + esc(t('ai.autoDone')) + ': ' +
      esc(t('ai.autoAccepted')) + ' ' + esc(ai.auto_accepted || 0) + ' · ' +
      esc(t('ai.needsReview')) + ' ' + esc(ai.needs_review || 0) + ' · ' +
      esc(t('ai.conflicts')) + ' ' + esc(ai.conflicts || 0) + '</span>';
  }
  if (ai.status === 'failed') {
    return '<span class="tiny pill pill-bad">' + esc(t('ai.autoFailed')) + '</span> ' +
      '<span class="tiny muted">' + esc(ai.error || '') + ' · ' + esc(t('ai.safeNote')) + '</span>';
  }
  return '<span class="tiny muted">' + esc(t('ai.notAnalyzed')) + '</span>';
}

function renderAiReport(report) {
  const kpRow = function (kp) {
    return '<li><strong>' + esc(kp.title || kp.knowledge_id) + '</strong> ' +
      '<span class="tiny muted mono">' + esc(kp.knowledge_id || '') + '</span> ' +
      '<span class="tiny muted">' + esc(kp.confidence || '') + ' · ' +
      esc((kp.evidence_refs || []).length) + ' evidence</span></li>';
  };
  const listOf = function (items) {
    return items.length ? '<ul class="small">' + items.map(kpRow).join('') + '</ul>' : '';
  };
  let html = '<div class="card"><div class="card-head"><h2>' + t('ai.summaryTitle') + '</h2>' +
    '<span class="small muted">' + esc(report.provider || '') + ' / ' + esc(report.model || '') + '</span></div>';
  if (report.summary) html += '<p class="small">' + esc(report.summary) + '</p>';
  html += renderAiStages(report);
  if ((report.topics || []).length) {
    html += '<p class="small"><strong>' + t('ai.topics') + '</strong>: ' +
      report.topics.map((x) => esc(x)).join(' · ') + '</p>';
  }
  if (report.knowledge_points_total !== undefined && report.knowledge_points_total !== null) {
    html += '<p class="small"><strong>' + t('ai.kpSummary') + '</strong>: ' +
      esc(report.knowledge_points_total) + '</p>';
  }
  const groups = [
    ['ai.autoAccepted', report.auto_accepted, 'pill-ok'],
    ['ai.needsReview', report.needs_review, 'pill-warn'],
    ['ai.conflicts', report.conflicts, 'pill-bad'],
  ];
  groups.forEach(function (group) {
    // 两种形状: 完整报告里是数组, 只读总结视图里是计数 (后端 ai_summary)。
    const raw = group[1];
    const items = Array.isArray(raw) ? raw : [];
    const count = Array.isArray(raw) ? raw.length : (raw || 0);
    html += '<p><span class="pill ' + group[2] + '">' + t(group[0]) + ' ' + esc(count) + '</span></p>' + listOf(items);
  });
  if ((report.definitions || []).length) {
    html += '<p class="small"><strong>' + t('ai.definitions') + '</strong></p><ul class="small">' +
      report.definitions.map((x) => '<li>' + esc(x) + '</li>').join('') + '</ul>';
  }
  if ((report.formulas || []).length) {
    html += '<p class="small"><strong>' + t('ai.formulas') + '</strong></p><ul class="small">' +
      report.formulas.map((x) => '<li>' + esc(x) + '</li>').join('') + '</ul>';
  }
  if ((report.examples || []).length) {
    html += '<p class="small"><strong>' + t('ai.examples') + '</strong></p><ul class="small">' +
      report.examples.map((x) => '<li>' + esc(x) + '</li>').join('') + '</ul>';
  }
  if ((report.prerequisites || []).length) {
    html += '<p class="small"><strong>' + t('ai.prereqs') + '</strong></p><ul class="small">' +
      report.prerequisites.map((x) => '<li>' + esc(x) + '</li>').join('') + '</ul>';
  }
  if ((report.difficulties || []).length) {
    html += '<p class="small"><strong>' + t('ai.difficulties') + '</strong></p><ul class="small">' +
      report.difficulties.map((x) => '<li>' + esc(x) + '</li>').join('') + '</ul>';
  }
  html += '<p class="tiny muted">chunks ' + esc(report.chunk_succeeded) + '/' + esc(report.chunk_total) +
    ' · prompt ' + esc(report.prompt_version || '') + '</p></div>';
  return html;
}

async function loadAiSummaryIntoPanel(courseId, materialId) {
  const panel = document.getElementById('ai-panel');
  if (!panel) return;
  try {
    const summary = await api('/materials/' + encodeURIComponent(materialId) + '/ai-summary', {
      query: { course_id: courseId },
    });
    panel.innerHTML = renderAiReport(summary);
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (err) {
    if (err.code !== 'NOT_FOUND') {
      panel.innerHTML = '<div class="card"><p class="tiny"><span class="pill pill-bad">' +
        esc(err.code) + '</span> <span class="small">' + esc(err.message) + '</span></p></div>';
    }
  }
}

async function actionAiAnalyze(courseId, materialId, button) {
  // AI 分析**不**登记可恢复任务 (P3-2): 服务端没有"正在分析"这个可读状态
  // —— /processing/{id} 的 job.ai 与 /ai-summary 都只暴露**已经存在**的
  // 报告。存了它就只能要么永远转圈、要么替服务端编一个结局。
  const taskId = startTask(t('ai.analyzing') + ' · ' + materialId);
  const panel = document.getElementById('ai-panel');
  if (button) button.disabled = true;
  if (panel) panel.innerHTML = '<p class="muted">' + t('ai.analyzing') + '</p>';
  try {
    const report = await api('/materials/' + encodeURIComponent(materialId) + '/ai-analyze', {
      method: 'POST', query: { course_id: courseId }, body: {},
    });
    const auto = report.auto_accepted || [];
    const review = report.needs_review || [];
    const conflicts = report.conflicts || [];
    const autoCount = Array.isArray(auto) ? auto.length : (auto || 0);
    const reviewCount = Array.isArray(review) ? review.length : (review || 0);
    const conflictCount = Array.isArray(conflicts) ? conflicts.length : (conflicts || 0);
    finishTask(taskId, true, t('ai.autoAccepted') + ' ' + autoCount + ' · ' +
      t('ai.needsReview') + ' ' + reviewCount + ' · ' + t('ai.conflicts') + ' ' + conflictCount);
    // 用户若已切到别的模块: 只 toast + 任务坞, 不把人拽回来; 材料页数据
    // 下次进页面时自然是最新的 (pageMaterials 每次都重拉)。
    if (!onTaskLane()) {
      toast(t('ai.autoDone') + ': ' + t('ai.autoAccepted') + ' ' + autoCount, 'ok');
      return;
    }
    // renderAiReport 之后再 route: 路由重绘会重建空面板, 必须重绘完
    // 之后用只读总结视图把它填回去 (TASK-77 修掉"报告闪一下就没"的 bug)。
    if (panel) panel.innerHTML = renderAiReport(report);
    await route();
    await loadAiSummaryIntoPanel(courseId, materialId);
  } catch (err) {
    finishTask(taskId, false, err.code + ' ' + err.message);
    // 面板可能已随路由重建: 永远用新鲜查找, 切页后只 toast。
    const fresh = document.getElementById('ai-panel');
    if (fresh && onTaskLane()) {
      // 失败只画通用卡片: 状态码 + 可读原因 + 材料证据完好 + 重试按钮。
      // 绝不把 traceback / 500 堆栈甩给用户, 也绝不回显任何密钥形状。
      fresh.innerHTML = '<div class="card"><div class="card-head"><h2>' +
        t('ai.summaryTitle') + '</h2></div>' +
        '<p><span class="pill pill-bad">' + esc(err.code) + '</span> ' +
        '<span class="small">' + esc(err.message) + '</span></p>' +
        '<p class="small muted">' + esc(t('ai.safeNote')) + '</p>' +
        '<p><button data-action="ai-analyze" data-course="' + esc(courseId) +
        '" data-material="' + esc(materialId) + '">' + t('ai.retry') + '</button></p></div>';
    } else {
      toast(t('ai.autoFailed') + ' [' + err.code + '] ' + err.message, 'bad');
    }
  } finally {
    if (button) button.disabled = false;
  }
}

// ------------------------------------------------------------ 事件与路由
