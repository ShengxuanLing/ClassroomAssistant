/*
 * 材料页 (``#/materials``) 与上传表单接线。
 */
'use strict';

function materialSessionCell(session) {
  if (!session) {
    return '<span class="pill pill-warn">' + esc(t('session.missing')) + '</span>';
  }
  const parts = sessionDisplayParts(session);
  const lines = [
    parts.date,
    [parts.number, parts.time].filter(Boolean).join(' · '),
    [parts.kind, parts.room].filter(Boolean).join(' · '),
  ].filter(Boolean);
  return lines.map((line, index) => (
    '<span class="' + (index === 0 ? '' : 'tiny ') + 'session-line">' + esc(line) + '</span>'
  )).join('<br>');
}

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

  // 统一分析状态 (服务端真相): POST .../analyze 的幂等状态 + 失败阶段
  // + 用户可读错误。刷新页面后状态从这里恢复, 不依赖任何前端 loading。
  const analyses = materialPayload.analysis_statuses || {};
  const rows = materials.map((m) => (
    '<tr><td class="break-all">' + esc(m.filename) +
    '<br><span class="tiny muted mono">' + esc(m.material_id) + '</span></td>' +
    '<td class="small">' + esc(m.material_type || m.source_type || '—') +
    '<br><span class="tiny muted">' + esc(m.extension || '') + ' · ' + fmtBytes(m.size) + '</span></td>' +
    '<td>' + materialAnalysisCell(m, analyses[m.material_id]) + '</td>' +
    '<td class="small">' + (m.session_id
      ? materialSessionCell(sessionById[m.session_id])
      : '<span class="muted">' + esc(t('未关联课堂')) + '</span>') + '</td>' +
    '<td class="small nowrap">' + materialActionButtons(courseId, m, analyses[m.material_id]) +
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
    '<span class="small muted">' + t('materials.aiFlowNote') + '</span></div>' +
    (materials.length
      ? '<table class="data">' + tableCaption(t('材料列表')) + '<thead><tr><th scope="col">' + t('文件') + '</th><th scope="col">' + t('类型') + '</th><th scope="col">' + t('状态') + '</th><th scope="col">' + t('课堂') + '</th>' +
        '<th scope="col">' + t('操作') + '</th></tr></thead><tbody>' + rows + '</tbody></table>'
      : emptyState(t('该课程还没有材料。'))) +
    '</div>' +
    '<div id="ai-panel"></div>'
  );
  // 有进行中的分析才轮询 (1.2s, 与任务坞同量级); 全部终态时一次都不定时。
  scheduleMaterialAnalysisPolling(courseId, materials, analyses);
}

// ---- 统一分析状态 (材料页「AI分析」) --------------------------------------
//
// 状态真相永远在服务端 (POST .../analyze + GET /materials 里的
// analysis_statuses); 这里只负责把它翻成人话。内部 pipeline 阶段
// (处理/重试/证据/摘要) 不再作为按钮暴露 —— 见任务书「材料页重构」。

function materialStageLabel(stage) {
  const key = 'mat.stage.' + stage;
  const label = t(key);
  return label === key ? (stage || '') : label;
}

function analysisKnowledgeHint(analysis) {
  if (!analysis || analysis.knowledge_point_count === undefined ||
      analysis.knowledge_point_count === null) return '';
  const count = Number(analysis.knowledge_point_count);
  if (!isFinite(count)) return '';
  if (analysis.status === 'SKIPPED' || analysis.ai_status === 'disabled') {
    return '<br><span class="tiny muted">' + esc(t('mat.aiSkippedDetail')) + ' (' +
      esc(t('ai.kpSummary')) + ' ' + esc(count) + ')</span>';
  }
  if (analysis.status === 'COMPLETED' && count === 0) {
    return '<br><span class="tiny pill pill-warn">' + esc(t('mat.aiZero')) + '</span>' +
      '<br><span class="tiny muted">' + esc(t('mat.aiZeroNext')) + '</span>';
  }
  if (analysis.status === 'COMPLETED') {
    return '<br><span class="tiny muted">' + esc(t('mat.aiCompletedCount')) + ' ' + esc(count) + '</span>';
  }
  return '';
}

function materialAnalysisCell(m, analysis) {
  const status = analysis ? analysis.status : null;
  let html = pill(m.processing_status) + (m.duplicate ? ' <span class="pill pill-info">' + t('重复') + '</span>' : '');
  if (status === 'PROCESSING') {
    const stage = materialStageLabel(analysis.current_stage);
    html += '<br><span class="pill pill-warn">' + esc(t('ai.analyzing')) + '</span>' +
      '<br><span class="tiny muted">' + esc(t('mat.currentStage')) + ': ' + esc(stage) + '</span>';
  } else if (status === 'FAILED') {
    const stage = materialStageLabel(analysis.current_stage);
    html += '<br><span class="pill pill-bad">' + esc(t('ai.autoFailed')) + '</span>' +
      '<br><span class="tiny muted">' + esc(t('mat.failedStage')) + ': ' + esc(stage) + '</span>';
    if (analysis.error_message) {
      html += '<br><span class="tiny pill pill-bad">' + esc(analysis.error_message) + '</span>';
    }
  } else if (status === 'SKIPPED') {
    html += '<br><span class="pill pill-warn">' + esc(t('mat.aiSkipped')) + '</span>';
  } else if (status === 'COMPLETED') {
    html += '<br><span class="tiny muted">' + esc(t('mat.completed')) + '</span>';
  }
  html += analysisKnowledgeHint(analysis);
  // 摄取层自己的错误行 (FAILED / warning / 零证据) 原样保留 —— 它们是
  // 服务端诊断, 不是用户可执行的内部操作。
  if (m.error) html += '<br><span class="tiny pill pill-bad">' + esc(m.error) + '</span>';
  html += warningRow(m) + zeroEvidenceHint(m);
  return html;
}

function materialActionButtons(courseId, m, analysis) {
  const base = 'data-course="' + esc(courseId) + '" data-material="' + esc(m.material_id) + '"';
  const status = analysis ? analysis.status : null;
  let html = '';
  if (status === 'PROCESSING') {
    // 分析中: 禁用态按钮 (不再是可点的「AI分析」), 后端幂等闸门兜底。
    html += '<button disabled>' + esc(t('ai.analyzing')) + '</button> ';
  } else if (status === 'FAILED') {
    // 失败态恢复操作: 重新分析 (重新跑完整链路, 系统不支持安全断点恢复)。
    html += '<button data-action="analyze-material" ' + base + '>' + esc(t('mat.retry')) + '</button> ';
  } else if (status === 'SKIPPED') {
    // SKIPPED 不是“全部 AI 完成”：按钮明确提示需要启用 AI 后再试。
    html += '<button data-action="analyze-material" ' + base + '>' + esc(t('mat.retryAfterEnable')) + '</button> ';
  } else {
    html += '<button data-action="analyze-material" ' + base + '>' + esc(t('ai.analyze')) + '</button> ';
  }
  html += '<button class="danger" data-action="delete-material" ' + base + '>' + esc(t('mat.delete')) + '</button>';
  return html;
}

let __materialPoller = null;

function stopMaterialAnalysisPolling() {
  if (__materialPoller) {
    clearTimeout(__materialPoller);
    __materialPoller = null;
  }
}

function scheduleMaterialAnalysisPolling(courseId, materials, analyses) {
  // 单一定时器: 重复进入/重绘不叠加 (先清旧的再定新的); 切页时 route()
  // 重绘会重新调度, 离开页面后没有任何轮询路径存活。
  stopMaterialAnalysisPolling();
  const active = materials.some((m) => {
    const a = analyses[m.material_id];
    return a && a.status === 'PROCESSING';
  });
  if (!active || typeof setTimeout !== 'function') return;
  __materialPoller = setTimeout(async () => {
    __materialPoller = null;
    // 页面已被切走 (路由重绘过) -> 停止, 不再拉状态。
    if (!onTaskLane()) return;
    try {
      const payload = await api('/materials', { query: { course_id: courseId } });
      const statuses = payload.analysis_statuses || {};
      const stillActive = (payload.materials || []).some((m) => {
        const a = statuses[m.material_id];
        return a && a.status === 'PROCESSING';
      });
      // 完成或失败 -> 停止轮询并重绘一次 (终态); 否则下一拍。
      if (stillActive) {
        scheduleMaterialAnalysisPolling(courseId, payload.materials || [], statuses);
      } else {
        await route();
      }
    } catch (err) {
      // 网络抖动不终止轮询: 下一拍再试, 与 pollRestoredTask 同口径。
      __materialPoller = setTimeout(
        () => { scheduleMaterialAnalysisPolling(courseId, materials, analyses); },
        2400
      );
    }
  }, 1200);
}


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

function pollMaterialAnalysisTask(courseId, materialId, taskId, ticks) {
  const live = tasks.find((item) => item.id === taskId);
  if (!live || live.status !== 'running') return;
  if (ticks > TASK_POLL_MAX_TICKS) {
    finishTask(taskId, false, t('超过 5 分钟仍未结束，无法确认结局。'));
    return;
  }
  api('/materials/' + encodeURIComponent(materialId) + '/analysis', {
    query: { course_id: courseId },
  }).then((status) => {
    if (status.status === 'COMPLETED') {
      finishTask(taskId, true, t('mat.completed'));
    } else if (status.status === 'SKIPPED') {
      finishTask(taskId, true, t('mat.aiSkipped'));
    } else if (status.status === 'FAILED') {
      const stage = materialStageLabel(status.current_stage);
      finishTask(taskId, false, t('mat.failedStage') + ': ' + stage + ' · ' + (status.error_message || ''));
    } else {
      const current = tasks.find((item) => item.id === taskId);
      if (!current || current.status !== 'running') return;
      current.detail = t('mat.currentStage') + ': ' + materialStageLabel(status.current_stage);
      renderTaskDock();
      __taskPollers[taskId] = setTimeout(
        () => { pollMaterialAnalysisTask(courseId, materialId, taskId, ticks + 1); },
        1200
      );
    }
  }).catch((err) => {
    if (err instanceof ApiError && err.code === 'NOT_FOUND') {
      finishTask(taskId, false, err.message);
      return;
    }
    const current = tasks.find((item) => item.id === taskId);
    if (!current || current.status !== 'running') return;
    current.detail = t('暂时查不到分析状态，正在重试');
    renderTaskDock();
    __taskPollers[taskId] = setTimeout(
      () => { pollMaterialAnalysisTask(courseId, materialId, taskId, ticks + 1); },
      2400
    );
  });
}

async function actionAnalyzeMaterial(courseId, materialId, button) {
  // 一键完整流水线: 后端 ``POST .../analyze`` 串起摄取 → 证据 → 知识 →
  // AI (幂等闸门在服务端: 重复点击返回当前状态, 不创建第二个任务)。
  // 本地只把按钮置为禁用 (视觉反馈), 状态真相随后端 analysis_statuses。
  if (button) button.disabled = true;
  const taskId = startTask(t('ai.analyzing') + ' · ' + materialId);
  try {
    const result = await api('/materials/' + encodeURIComponent(materialId) + '/analyze', {
      method: 'POST', query: { course_id: courseId }, body: {},
    });
    if (result.status === 'COMPLETED') {
      finishTask(taskId, true, t('mat.completed'));
    } else if (result.status === 'SKIPPED') {
      finishTask(taskId, true, t('mat.aiSkipped'));
    } else if (result.status === 'FAILED') {
      const stage = materialStageLabel(result.current_stage);
      finishTask(taskId, false, t('mat.failedStage') + ': ' + stage + ' · ' + (result.error_message || ''));
    } else {
      // already_running / 其它: 绝不能把仍在 processing 的任务标成 done。
      // 同一请求可能来自并发点击; 复用同一 task id 并轮询服务端分析状态。
      const live = tasks.find((item) => item.id === taskId);
      if (live) {
        live.detail = t('ai.analyzing');
        renderTaskDock();
        pollMaterialAnalysisTask(courseId, materialId, taskId, 1);
      }
    }
  } catch (err) {
    finishTask(taskId, false, err.code + ' ' + err.message);
    toast(t('ai.autoFailed') + ' [' + err.code + '] ' + err.message, 'bad');
  }
  // 无论成败都重绘: 状态行与按钮 (AI分析 / 分析中… / 重新分析) 以
  // 服务端为准; 完成时顺带把 AI 报告面板填上。
  await route();
  if (!onTaskLane()) return;
  try {
    const st = await api('/materials/' + encodeURIComponent(materialId) + '/analysis', {
      query: { course_id: courseId },
    });
    if (st.status === 'COMPLETED') await loadAiSummaryIntoPanel(courseId, materialId);
  } catch (err) {
    // 状态查询失败不影响主流程 (列表里已有状态行)。
  }
}

async function actionDeleteMaterial(courseId, materialId, button) {
  // 危险操作必须确认 (confirmDestructive 集中管理哪些动作要确认)。
  const body = t('mat.deleteBody');
  if (!confirmDestructive(t('mat.deleteTitle') + '\n\n' + body)) return;
  if (button) button.disabled = true;
  try {
    const result = await api('/materials/' + encodeURIComponent(materialId), {
      method: 'DELETE', query: { course_id: courseId },
    });
    const removed = (result.removed_knowledge_point_ids || []).length;
    toast(t('mat.deleted') + (result.filename || materialId) +
      (removed ? t(' · 已移除 ') + removed + t(' 个知识点') : ''), 'ok');
  } catch (err) {
    toast(t('mat.deleteFailed') + ' [' + err.code + '] ' + err.message, 'bad');
  }
  await route();
}

// ------------------------------------------------------------ 事件与路由
