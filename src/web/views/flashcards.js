/*
 * Evidence-grounded flashcards (``#/flashcards``).
 */
'use strict';

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

  const body = cards.length
    ? '<div class="grid grid-2">' + cards.map((card) => (
        '<article class="card">' +
        '<div class="row-between"><h2>' + esc(card.front) + '</h2>' +
        pill(card.state || 'new', card.state === 'review' ? 'COMPLETED' : 'PENDING') + '</div>' +
        '<div class="original">' + esc(card.back) + '</div>' +
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
