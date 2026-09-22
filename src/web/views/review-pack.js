/*
 * 复习包页 (`#/review-pack`) —— 材料摘要 + 课程合并包 (P1/§2, 只读)。
 *
 * 铁律: Summary 是"待人工核验"的草稿 (needs_verification=true 永不自动
 * confirm); 证据原文逐字渲染、绝不翻译; 冲突只并列, 页面上没有"裁决"按钮。
 *
 * 注: 注释里不能出现译文函数加单引号包裹省略号的那种形状
 * (i18n 静态检查用正则扫源码、不剥注释, 见 app.js 注记)。
 */
'use strict';

function packEvidenceChain(evidence) {
  return (evidence || []).map(function (ev) {
    return '<li><span class="tiny mono">' + esc(ev.evidence_id) + '</span>' +
      ' <span class="tiny muted">' + esc(ev.evidence_type || '') + '</span>' +
      '<pre class="small mono break-all">' + esc(ev.content || '') + '</pre></li>';
  }).join('');
}

function packSummaryBlock(digest) {
  const s = digest.summary || {};
  let html = '<p class="small">' + esc(s.text || '') + '</p>';
  const flags = [];
  if (s.needs_verification) {
    flags.push('<span class="pill pill-warn">' + t('pack.needsVerification') + '</span>');
  }
  if (s.fallback) flags.push('<span class="pill pill-warn">' + t('pack.fallback') + '</span>');
  if (s.truncated) flags.push('<span class="pill pill-muted">' + t('pack.truncated') + '</span>');
  if (flags.length) html += '<p>' + flags.join(' ') + '</p>';
  html += '<p class="tiny muted">' + t('pack.model') + ': ' + esc(s.model || '') +
    ' · prompt ' + esc(s.prompt_version || '') + '</p>';
  if ((digest.conflicts || []).length) {
    html += '<p><span class="pill pill-warn">' + t('pack.conflicts') + ' ' +
      esc(digest.conflicts.length) + '</span></p><ul class="small">' +
      digest.conflicts.map((c) => '<li><span class="mono tiny">' + esc(c.conflict_id) +
        '</span> <span class="muted small">' + esc(c.description || '') + '</span></li>').join('') +
      '</ul><p class="tiny muted">' + t('pack.conflictNote') + '</p>';
  }
  if ((digest.evidence || []).length) {
    html += '<details><summary class="small">' + t('pack.evidenceChain') + ' (' +
      esc(digest.evidence.length) + ')</summary><ul class="small">' +
      packEvidenceChain(digest.evidence) + '</ul></details>';
  }
  return html;
}

async function pageReviewPack() {
  // 顶栏有 #/review-pack 分区 (index.html), 故高亮它而不是清空
  // (与 app.js markActiveNav 上方注释表一致; 空字符串只留给无分区的详情页)。
  markActiveNav('#/review-pack');
  const courseId = await requireCourse();
  const pack = await api('/courses/' + encodeURIComponent(courseId) + '/review-pack', {
    query: { course_id: courseId },
  });
  const digests = pack.digests || [];
  setView(
    '<div class="page-head"><h1>' + t('pack.title') + '</h1>' +
    '<p class="subtitle">' + t('pack.subtitle') + ' · <span class="pill ' +
    (pack.llm_mode === 'real' ? 'pill-ok' : 'pill-warn') + '">' +
    esc(pack.llm_mode || 'mock') + '</span></p>' +
    '<p class="tiny muted">' + t('pack.note') + '</p></div>' +
    '<div class="card"><div class="card-head"><h2>' + t('pack.materials') + '</h2>' +
    '<span class="small muted">' + esc(digests.length) + '</span></div>' +
    (digests.length
      ? digests.map((d) => (
          '<div class="card"><h3>' + esc(d.filename || d.material_id) + '</h3>' +
          packSummaryBlock(d) + '</div>'
        )).join('')
      : emptyState(t('pack.empty'))) +
    '</div>' +
    ((pack.conflicts || []).length
      ? '<div class="card"><h2>' + t('pack.courseConflicts') + '</h2><ul class="small">' +
        pack.conflicts.map((c) => '<li><span class="mono tiny">' + esc(c.conflict_id) +
          '</span> ' + esc(c.description || '') + '</li>').join('') + '</ul>' +
        '<p class="tiny muted">' + t('pack.conflictNote') + '</p></div>'
      : '')
  );
}

// ---- 单材料摘要面板 (材料页"摘要"按钮) ------------------------------------

async function actionMaterialDigest(courseId, materialId) {
  const panel = document.getElementById('digest-panel');
  if (!panel) return;
  panel.innerHTML = '<p class="muted">' + t('common.loading') + '</p>';
  try {
    const digest = await api('/materials/' + encodeURIComponent(materialId) + '/digest', {
      query: { course_id: courseId },
    });
    panel.innerHTML = '<div class="card"><div class="card-head"><h2>' +
      t('pack.digestFor') + ' ' + esc(digest.filename || digest.material_id) + '</h2>' +
      '<span class="pill ' + (digest.summary.fallback ? 'pill-warn' : 'pill-ok') + '">' +
      esc(digest.summary.model) + '</span></div>' +
      packSummaryBlock(digest) + '</div>';
  } catch (err) {
    panel.innerHTML = '<div class="card"><p class="tiny"><span class="pill pill-bad">' +
      esc(err.code) + '</span> <span class="small">' + esc(err.message) + '</span></p></div>';
  }
}
