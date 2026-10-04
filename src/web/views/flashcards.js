/*
 * Evidence-grounded flashcards (``#/flashcards``).
 *
 * TASK-79: 卡片**不删**, 但降级为“背题入口”。原来的标题是纯文本
 * (`<h2>`), 学生看到了一个不懂的西语概念却无处可去; 现在标题就是知识点详情
 * 链接, 下方再挂一行中文预览 (只读、零 LLM —— 读已落盘的术语表/翻译缓存,
 * 没有就是没有, 不自动花钱)。FSRS 打分逻辑一个字节没动。
 */
'use strict';

/**
 * 读一张卡的知识点已有中文层 (术语表 + 已缓存的整句解释)。
 *
 * **只读**: 两个 GET 都不调 LLM。404 / 失败一律当“没有中文”而不是报错 ——
 * 一张记忆卡因翻译不可用而整页报错是荒谬的。
 */
async function loadCardZh(courseId, knowledgeId) {
  if (!courseId || !knowledgeId) return { translation_zh: '', terms: [] };
  let text = '';
  let terms = [];
  try {
    const glossary = await api('/knowledge/' + encodeURIComponent(knowledgeId) + '/glossary', {
      query: { course_id: courseId },
    });
    const entries = Array.isArray(glossary && glossary.glossary) ? glossary.glossary : [];
    terms = entries.slice(0, 4).map((entry) =>
      String(entry.term || '') + '（' + String(entry.zh || '') + '）');
  } catch (err) {
    terms = [];
  }
  try {
    const report = await api('/knowledge/' + encodeURIComponent(knowledgeId) + '/translate', {
      query: { course_id: courseId },
    });
    text = String((report && report.translation_zh) || '');
  } catch (err) {
    text = '';
  }
  return { translation_zh: text, terms: terms };
}

function renderCardZh(zh) {
  if (!zh || (!zh.translation_zh && !(zh.terms || []).length)) return '';
  let out = '<div class="card-zh">';
  if (zh.translation_zh) {
    out += '<p class="small">' + esc(zh.translation_zh) + '</p>';
  }
  if ((zh.terms || []).length) {
    out += '<p class="tiny muted break-all">' +
      esc(zh.terms.join(' · ')) + '</p>';
  }
  return out + '<p class="tiny muted">' + esc(t('kpZh.disclaimer')) + '</p></div>';
}

async function pageFlashcards() {
  markActiveNav('#/knowledge');
  const courseId = await requireCourse();
  const students = (await api('/students', { query: { course_id: courseId } })).students || [];
  if (!students.length) {
    setView('<div class="page-head"><h1>' + esc(t('fc.title')) + '</h1></div>' +
      '<div class="card">' + noStudentsCard() + '</div>');
    return;
  }
  const studentId = state.studentId && students.some((item) => item.student_id === state.studentId)
    ? state.studentId
    : students[0].student_id;
  setStudent(studentId);
  const cards = (await api('/flashcards', {
    query: { course_id: courseId, student_id: studentId },
  })).flashcards || [];

  // 中文预览: 按知识点去重后并行读, 再回填到每一张卡 (两张卡可以共享一个 KP)。
  // 显式限流 20 个知识点 —— 一页卡片不该发出无上限的请求串。
  const kpIds = [];
  cards.forEach((card) => {
    const kpId = String(card.kp_id || '');
    if (kpId && kpIds.indexOf(kpId) === -1) kpIds.push(kpId);
  });
  const zhByKp = {};
  await Promise.all(kpIds.slice(0, 20).map(async (kpId) => {
    zhByKp[kpId] = await loadCardZh(courseId, kpId);
  }));

  const body = cards.length
    ? '<div class="grid grid-2">' + cards.map((card) => (
        '<article class="card">' +
        '<div class="row-between"><h2>' + kpLink(courseId, card.kp_id, card.front) +
        '</h2>' +
        pill(card.state || 'new', card.state === 'review' ? 'COMPLETED' : 'PENDING') + '</div>' +
        '<div class="original">' + esc(card.back) + '</div>' +
        renderCardZh(zhByKp[String(card.kp_id || '')]) +
        (card.example ? '<p class="small muted">' + esc(t('fc.example')) + ': ' +
          esc(card.example) + '</p>' : '') +
        '<dl class="kv compact">' +
        '<dt>' + esc(t('fc.due')) + '</dt><dd class="tiny mono">' + esc(card.due || '—') + '</dd>' +
        '<dt>' + esc(t('fc.source')) + '</dt><dd>' +
        (card.source_refs || []).map((ref) => kpLink(courseId, card.kp_id, ref)).join('<br>') +
        '</dd></dl><div class="row">' +
        '<button class="small" data-action="review-flashcard" data-course="' + esc(courseId) +
        '" data-student="' + esc(studentId) + '" data-flashcard="' + esc(card.flashcard_id) +
        '" data-rating="again">' + esc(t('fc.again')) + '</button>' +
        '<button class="small" data-action="review-flashcard" data-course="' + esc(courseId) +
        '" data-student="' + esc(studentId) + '" data-flashcard="' + esc(card.flashcard_id) +
        '" data-rating="good">' + esc(t('fc.good')) + '</button>' +
        '<button class="small" data-action="review-flashcard" data-course="' + esc(courseId) +
        '" data-student="' + esc(studentId) + '" data-flashcard="' + esc(card.flashcard_id) +
        '" data-rating="easy">' + esc(t('fc.easy')) + '</button>' +
        '</div></article>'
      )).join('') + '</div>'
    : emptyState(t('fc.empty')) + '<p class="small">' + esc(t('fc.emptyGuide')) + ' ' +
      '<a href="#/knowledge">' + esc(t('nav.knowledge')) + '</a></p>';

  setView(
    '<div class="page-head"><h1>' + esc(t('fc.title')) + '</h1>' +
    '<p class="subtitle">' + esc(t('fc.student')) + ': <span class="mono tiny">' +
    esc(studentId) + '</span> · ' + esc(cards.length) + esc(t('fc.cards')) + '</p></div>' + body
  );
}

async function actionReviewFlashcard(courseId, studentId, flashcardId, rating, button) {
  try {
    button.disabled = true;
    const card = await api('/flashcards/' + encodeURIComponent(flashcardId) + '/review', {
      method: 'POST',
      body: { course_id: courseId, student_id: studentId, rating: rating },
    });
    toast(t('fc.reviewed') + ' · ' + card.due, 'ok');
    route();
  } catch (err) {
    toast(t('fc.reviewFailed') + ' [' + err.code + '] ' + err.message, 'bad');
    button.disabled = false;
  }
}

async function actionCreateFlashcard(courseId, knowledgeId, button) {
  try {
    const students = (await api('/students', { query: { course_id: courseId } })).students || [];
    if (!students.length) {
      toast(t('student.none'), 'bad');
      return;
    }
    const studentId = state.studentId && students.some((item) => item.student_id === state.studentId)
      ? state.studentId
      : students[0].student_id;
    setStudent(studentId);
    button.disabled = true;
    const card = await api('/flashcards', {
      method: 'POST',
      body: { course_id: courseId, student_id: studentId, kp_id: knowledgeId },
    });
    toast(t('fc.created') + ' ' + card.front, 'ok');
  } catch (err) {
    toast(t('fc.createFailed') + ' [' + err.code + '] ' + err.message, 'bad');
  } finally {
    button.disabled = false;
  }
}
