/*
 * 待审核 (``#/reviews``) 与考前复习 (``#/review``)。
 *
 * 曾经还有一个课程级「复习中心」页 (``#/review-center/<id>``, Task 62),
 * 2026-09-21 按用户要求整体删除 —— 它与「复习包」「待审核」「考前复习」
 * 三者语义重叠, 而入口本身是个死链。后端的
 * ``GET /api/courses/{id}/review`` 投影层保留 (有自己的契约测试)。
 */
'use strict';

function rsBucketLabel(bucket) {
  if (bucket === 'blocked') return t('rs.blocked');
  if (bucket === 'attention') return t('rs.attention');
  if (bucket === 'ready') return t('rs.ready');
  return bucket || dash(null);
}

function rsBucketPill(bucket) {
  const kind = bucket === 'blocked' ? 'pill-bad'
    : (bucket === 'attention' ? 'pill-warn' : 'pill-ok');
  return '<span class="pill ' + kind + '">' + esc(rsBucketLabel(bucket)) + '</span>';
}

/** 复习集合项的一条: 两条真相轴**分开**列, 不合成为一个"可信度"。 */
function rsItemRow(item, courseId) {
  return '<tr>' +
    '<td class="num">' + esc(item.position) + '</td>' +
    '<td><a href="#/courses/' + encodeURIComponent(courseId) +
      '/learn/' + encodeURIComponent(item.knowledge_id) + '">' +
      esc(item.title || item.knowledge_id) + '</a>' +
      '<br><span class="tiny muted mono">' + esc(item.knowledge_id) + '</span></td>' +
    '<td>' + rsBucketPill(item.bucket) +
      (item.block_note
        ? '<br><span class="tiny muted">' + esc(t('rs.block.' + item.block_reason)) + '</span>'
        : '') +
      (item.attention_note
        ? '<br><span class="tiny muted">' + esc(t('rs.reason.' + item.attention_reason)) + '</span>'
        : '') +
    '</td>' +
    '<td>' + pill(statusLabel(item.validation_status), item.validation_status) + '</td>' +
    '<td>' + pill(statusLabel(item.review_status), item.review_status) + '</td>' +
    '<td><span class="tiny muted">' + esc(learnStateLabel(item.learning_state)) + '</span></td>' +
    '</tr>';
}

function rsBucketCard(name, payload, courseId) {
  const ids = (payload.buckets && payload.buckets[name]) || [];
  const rows = (payload.items || []).filter((i) => i.bucket === name);
  return '<div class="card"><div class="card-head"><h2>' + esc(rsBucketLabel(name)) +
    ' (' + esc(ids.length) + ')</h2></div>' +
    (rows.length
      ? '<table class="data">' + tableCaption(rsBucketLabel(name)) + '<thead><tr>' +
        '<th scope="col" class="num">' + t('rs.position') + '</th>' +
        '<th scope="col">' + t('rs.knowledge') + '</th>' +
        '<th scope="col">' + t('common.status') + '</th>' +
        '<th scope="col">' + t('rs.validationStatus') + '</th>' +
        '<th scope="col">' + t('rs.reviewStatus') + '</th>' +
        '<th scope="col">' + t('rs.learningState') + '</th>' +
        '</tr></thead><tbody>' +
        rows.map((row) => rsItemRow(row, courseId)).join('') + '</tbody></table>'
      : emptyState(t('rs.empty'))) +
    '</div>';
}

/** 冲突: 两侧证据并列展示, 绝不显示"哪一方是对的"。 */
function rsConflictCard(payload) {
  const rows = payload.conflicts || [];
  if (!rows.length) return '';
  return '<div class="card"><div class="card-head"><h2>' + t('rs.conflicts') +
    ' (' + esc(rows.length) + ')</h2>' +
    '<span class="small muted">' + t('rs.conflictSides') + '</span></div>' +
    rows.map((row) =>
      '<div class="conflict"><p class="mono small">' + esc(row.conflict_id) + '</p>' +
      '<p class="small">' + esc(row.description || '') + '</p>' +
      '<ul class="small">' + (row.sides || []).map((side) =>
        '<li><strong>' + esc(side.label) + '</strong> <span class="mono">' +
        esc(side.evidence_id) + '</span></li>').join('') + '</ul>' +
      '<p class="tiny muted">' + esc(row.blocking
        ? t('rs.conflictBlocking') : t('rs.conflictSettled')) + '</p>' +
      '</div>').join('') +
    '</div>';
}

function rsCoverageCard(payload) {
  const coverage = payload.coverage || {};
  if (coverage.unavailable) return '';
  return '<div class="card"><div class="card-head"><h2>' + t('rs.coverage') + '</h2></div>' +
    '<dl class="kv">' +
    '<dt>' + t('rs.ratio') + '</dt><dd>' +
      esc(coverage.coverage_ratio === undefined ? dash(null) : coverage.coverage_ratio) + '</dd>' +
    (coverage.total_knowledge_points !== undefined
      ? '<dt>' + t('rs.total') + '</dt><dd>' + esc(coverage.total_knowledge_points) + '</dd>'
      : '') +
    '</dl></div>';
}

/**
 * 考前复习页。
 *
 * 页面上**不出现**任何"最可能考 / 考试概率 / 押题"之类的内容 —— 后端响应里
 * 也没有这类字段 (见 ReviewSet 的 No Prediction Contract)。顶部那句
 * ``rs.notPredictor`` 是刻意放的: 学生需要知道这个页面的定位。
 */
async function pageReview() {
  markActiveNav('#/review');
  const courseId = await requireCourse();
  const students = (await api('/students', { query: { course_id: courseId } })).students || [];
  if (!students.length) {
    setView('<div class="page-head"><h1>' + esc(t('rs.title')) + '</h1></div>' +
      '<div class="card">' + emptyState(t('rs.noStudent')) + '</div>');
    return;
  }
  const studentId = state.studentId &&
    students.some((s) => s.student_id === state.studentId)
    ? state.studentId : students[0].student_id;
  setStudent(studentId);
  // 同上: 当前课程只有一个写入口。
  setCourse(courseId);

  const data = await api(
    '/students/' + encodeURIComponent(studentId) + '/review-set',
    { query: { course_id: courseId, lang: state.lang } }
  );

  const head = '<div class="page-head"><h1>' + esc(t('rs.title')) + '</h1>' +
    '<p class="subtitle">' + esc(studentId) + ' · ' + esc(courseLabel(courseId)) + '</p>' +
    '<p class="small muted">' + esc(t('rs.subtitle')) + '</p></div>';

  const body = data.empty
    ? '<div class="card">' + emptyState(t('rs.empty')) + '</div>'
    : ['blocked', 'attention', 'ready'].map((name) => rsBucketCard(name, data, courseId)).join('');

  setView(head +
    '<div class="card"><p class="small">' + esc(t('rs.notPredictor')) + '</p>' +
    '<dl class="kv">' +
    '<dt>' + t('rs.total') + '</dt><dd>' + esc(data.counts.total) + '</dd>' +
    '<dt>' + t('rs.sortBasis') + '</dt><dd>' + esc(data.ordering_basis) + '</dd>' +
    '</dl></div>' +
    body + rsConflictCard(data) + rsCoverageCard(data) +
    '<p class="small muted">' + esc(t('rs.twoAxesNote')) + '</p>' +
    '<p class="small"><a href="#/today">' + esc(t('rs.backToToday')) + '</a></p>');
}

// ---- 知识点列表 ----------------------------------------------------------


async function pageReviews() {
  markActiveNav('#/reviews');
  const courseId = await requireCourse();
  const candidates = (await api('/reviews', { query: { course_id: courseId } })).reviews || [];

  setView(
    '<div class="page-head"><h1>' + t('待审核') + '</h1>' +
    '<p class="subtitle">' + t('课程 ') + '<strong>' + esc(courseLabel(courseId)) + '</strong> · ' + esc(candidates.length) +
    t(' 项待人工决策 · 审核状态永远不会被自动改写') + '</p></div>' +
    '<div class="card">' +
    (candidates.length
      ? '<table class="data">' + tableCaption(t('待审核')) + '<thead><tr><th scope="col">' + t('知识点') + '</th><th scope="col">' + t('原因') + '</th><th scope="col">' + t('当前审核状态') + '</th><th scope="col">' + t('操作') + '</th></tr></thead><tbody>' +
        candidates.map((c) => {
          const kpId = c.knowledge_point_id || c.knowledge_id;
          const link = '#/courses/' + encodeURIComponent(courseId) + '/knowledge/' + encodeURIComponent(kpId);
          return '<tr><td><a href="' + link + '">' + esc(kpId) + '</a></td>' +
            '<td class="small">' + dash(c.reason) + '</td>' +
            '<td>' + pill(c.review_status || c.decision || 'PENDING', 'PENDING') + '</td>' +
            '<td><a class="btn" href="' + link + '">' + t('去审核') + '</a></td></tr>';
        }).join('') + '</tbody></table>'
      : emptyState(t('没有待审核的知识点。'))) +
    '</div>'
  );
}

// ---- 今日 (Task 56.4 / Task 63) -------------------------------------------

/**
 * 学生视角的今日首页 (Task 63)。
 *
 * 这个页面**是一个派生视图** —— 后端 ``/api/student-today`` 从
 * Course / ClassSession / StudyPlan / LearningPath / StudentState /
 * Exercise / Evaluation / Review 这些既有事实上投影出来, 不维护第二份
 * 状态 (spec 63.2)。前端在这里只做一件事: **如实显示**, 一个数字都不算。
 *
 * 几条硬约束 (全部由后端保证):
 *
 * 1. **"今天" 由本机时钟决定** —— 不接受浏览器传日期 (spec 63.3)。
 * 2. **今日学习必须来自 StudyPlan** —— 前端不自己挑"今天该学什么",
 *    也不重新生成一套计划 (spec 63.4)。没有计划就显示
 *    "No learning activity yet."。
 * 3. **计数不等于掌握度** —— 页面里不出现 mastery / proficiency /
 *    "你已掌握" 这类词 (spec 63.9 禁止项)。Attention 区只透传
 *    StudentState 已经定义的信号, 并显示其 ``basis``。
 * 4. **Recent Evaluations 就是评估** —— 不自动改写成 Mastery
 *    (spec 63.8)。
 * 5. **Course Context** —— 选了某门课就只看那门课; "All Courses" 时
 *    每一条都带 ``course_id`` (spec 63.12)。
 */
