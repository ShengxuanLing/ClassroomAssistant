/* 课堂助手 UI 渲染检查 (Task 41)
 *
 * 为什么需要它
 * ------------
 * 本机是 Windows, 而浏览器自动化 (agent-browser) 只支持 macOS / Linux,
 * 所以无法做浏览器端到端测试。但 app.js 的页面函数**是纯字符串拼接**,
 * 可以在 Node 里用一个最小 DOM 桩真实执行 —— 这能抓到静态断言抓不到的东西:
 * 模板运行时错误 (undefined 字段)、以及"答案是否真的没被渲染出来"。
 *
 * 这不是浏览器测试, 也不声称是。它只证明: 给定真实的 API 响应形状,
 * 页面函数能跑通, 且提交前渲染出的 HTML 里**没有答案**。
 *
 * 用法: node scripts/ui_render_check.js   (退出码 0 = 全部通过)
 */

'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');

// ---------------------------------------------------------------- 前端源码
//
// P1-6: 前端已拆成多个零构建脚本, index.html 按下面的顺序 <script> 引入。
// 浏览器里多个 <script> 共享同一个全局作用域; 在 vm 里对应的是**同一个
// context 顺序执行多个 script** —— 顶层 function / const 互相可见, 跨文件
// 调用安全, 但顶层初始化的顺序依赖仍然保持 (见 index.html 里的注释)。

const WEB_FILES = [
  'api.js',
  'i18n.js',
  'app.js',
  'views/dashboard.js',
  'views/learn.js',
  'views/review.js',
  'views/knowledge.js',
  'views/materials.js',
  'views/courses.js',
  'views/students.js',
  'views/exercises.js',
  'views/mistakes.js',
  'views/review-pack.js',
];

const WEB_SOURCES = WEB_FILES.map((rel) => ({
  name: rel,
  code: fs.readFileSync(path.join(ROOT, 'src', 'web', rel), 'utf8'),
}));

// 全部前端源码 (按加载顺序拼接)。本文件里对源码做的 indexOf / 正则断言
// 一律针对它 —— 与拆分前针对单个 app.js 断言等价。
const APP_JS = WEB_SOURCES.map((source) => source.code).join('\n');

// ---------------------------------------------------------------- 测试数据

const SECRET_ANSWER = 'RESPUESTA_SECRETA_123';
const SECRET_EXPLANATION = 'EXPLICACION_SECRETA_456';
const COURSE_ID = 'course-test';
const STUDENT_ID = 'stu-ana';
const EXERCISE_ID = 'exercise-test';

function baseView(overrides) {
  return Object.assign(
    {
      course_id: COURSE_ID,
      student_id: STUDENT_ID,
      exercise_id: EXERCISE_ID,
      exercise_type: 'fill_blank',
      prompt: 'El grado de la funcion es ___',
      choices: [],
      difficulty: 2,
      answer_format: 'text',
      knowledge_point_ids: ['kp-1'],
      knowledge_points: [
        {
          knowledge_id: 'kp-1',
          title: 'Definicion de funcion',
          validation_status: 'supported',
          review_status: 'pending',
          knowledge_score: 0.5,
        },
      ],
      prerequisites: ['kp-0'],
      evidence: [
        {
          evidence_id: 'ev-1',
          evidence_type: 'document',
          language: 'Unknown',
          confidence: 'UNCERTAIN',
          content: 'Introduccio al tema (ca). Definicion de funcion (es).',
          source: { material_id: 'mat-1', page: 1, location: 'pdf-page-1-block-0' },
        },
      ],
      unresolved_evidence_ids: [],
      evidence_complete: true,
      submitted: false,
      answer_id: null,
      submitted_value: null,
      submitted_at: null,
      sequence: null,
      evaluation: null,
      answer_key_available: false,
      answer_key: null,
      answer_key_withheld: true,
    },
    overrides || {}
  );
}

function evaluatedView() {
  return baseView({
    submitted: true,
    answer_id: 'answer-1',
    submitted_value: 'uno',
    submitted_at: '2026-01-01T00:00:00+00:00',
    sequence: 0,
    answer_key_available: true,
    answer_key_withheld: false,
    answer_key: {
      blank_id: 'b1',
      accepted_answers: [SECRET_ANSWER],
      explanation: SECRET_EXPLANATION,
    },
    evaluation: {
      evaluation_id: 'evaluation-1',
      answer_id: 'answer-1',
      status: 'incorrect',
      score: 0.0,
      feedback: 'comparacion exacta',
      evaluator_version: 'exact-v1',
      knowledge_point_ids: ['kp-1'],
      knowledge_points: [
        {
          knowledge_id: 'kp-1',
          title: 'Definicion de funcion',
          validation_status: 'supported',
          review_status: 'pending',
          knowledge_score: 0.5,
        },
      ],
      evidence: [
        {
          evidence_id: 'ev-1',
          evidence_type: 'document',
          language: 'Unknown',
          confidence: 'UNCERTAIN',
          content: 'Introduccio al tema (ca). Definicion de funcion (es).',
          source: { material_id: 'mat-1', page: 1 },
        },
      ],
      expected: {
        blank_id: 'b1',
        accepted_answers: [SECRET_ANSWER],
        explanation: SECRET_EXPLANATION,
      },
      is_fact_verification: false,
      affects_knowledge_base: false,
    },
  });
}

function studentToday() {
  // Task 63 夹具: 纯 ASCII, 免得 i18n 检查被内容字符串干扰。
  return {
    date: '2026-03-01',
    generated_at: '2026-03-01T09:00:00+00:00',
    course_id: null,
    lang: 'zh',
    has_activity: true,
    note: null,
    classes_today: [
      {
        session_id: 'session-1',
        course_id: COURSE_ID,
        session_number: 3,
        date: '2026-03-01',
        title: 'Tema 3',
        status: 'READY_TO_STUDY',
        counts: { materials: 2, evidence: 4, knowledge_points: 12, pending_review: 3 },
        course_name: 'Algebra Lineal',
        course_code: 'ALG',
      },
    ],
    courses_today: [
      { course_id: COURSE_ID, name: 'Algebra Lineal', code: 'ALG', language: 'ca', sessions_today: 1 },
    ],
    pending_materials: [],
    study: [
      {
        course_id: COURSE_ID,
        course_name: 'Algebra Lineal',
        student_id: STUDENT_ID,
        available: true,
        note: null,
        tasks_today: [
          { knowledge_point_id: 'kp-1', reason_codes: ['review_pending'], prerequisite_ids: [] },
        ],
        tasks_total: 5,
        next_task: { knowledge_point_id: 'kp-1', reason_codes: ['review_pending'], prerequisite_ids: [] },
        plan_id: 'plan-abc123',
        rules_version: 'rules-v1',
      },
    ],
    learning_paths: [
      {
        course_id: COURSE_ID,
        student_id: STUDENT_ID,
        knowledge_point_id: 'kp-1',
        current: {
          position: 1,
          knowledge_point_id: 'kp-1',
          knowledge_point: { knowledge_id: 'kp-1', title: 'Definicion de funcion' },
          state: 'practicing',
          status: 'IN_PROGRESS',
          status_basis: 'state=practicing (Task 30 LearningState)',
          next_event: 'reviewed',
          activity: {},
          prerequisite_ids: ['kp-prereq-1'],
          unmet_prerequisite_ids: [],
          exercises: [],
          evaluations: [],
        },
        prerequisites: ['kp-prereq-1'],
        unmet_prerequisites: [],
        next: null,
        next_event: 'reviewed',
        nodes_total: 2,
      },
    ],
    pending_exercises: [
      {
        exercise_id: EXERCISE_ID,
        course_id: COURSE_ID,
        student_id: STUDENT_ID,
        exercise_type: 'fill_blank',
        prompt: 'El grado de la funcion es ___',
        knowledge_point_ids: ['kp-1'],
        difficulty: 2,
      },
    ],
    recent_evaluations: [
      {
        answer_id: 'answer-1',
        exercise_id: EXERCISE_ID,
        submitted_value: 'uno',
        sequence: 0,
        submitted_at: '2026-03-01T09:00:00+00:00',
        status: 'incorrect',
        score: 0.0,
        feedback: 'comparacion exacta',
        course_id: COURSE_ID,
        student_id: STUDENT_ID,
      },
    ],
    attention: [
      {
        course_id: COURSE_ID,
        student_id: STUDENT_ID,
        knowledge_point_id: 'kp-1',
        kind: 'NEEDS_PRACTICE',
        state: 'practicing',
        basis: 'StudentState state=practicing (Task 30)',
      },
    ],
    review: {
      items: [
        {
          knowledge_point_id: 'kp-1',
          validation_status: 'unverified',
          knowledge_score: 0.5,
          supporting_evidence_ids: ['ev-1'],
          conflict_ids: [],
          needs_verification: true,
          priority: 1,
          course_id: COURSE_ID,
          course_name: 'Algebra Lineal',
        },
      ],
      total: 7,
      path: '/api/reviews',
    },
    counts: {
      classes_today: 1,
      study_tasks: 5,
      learning_paths: 1,
      pending_exercises: 6,
      recent_evaluations: 1,
      attention: 1,
      review: 7,
      pending_materials: 0,
    },
    links: { review_center: '/api/reviews', exercises: '/api/exercises' },
    limits: { items: 20 },
  };
}

/**
 * Task 68: ``/api/my-courses`` 的响应夹具。
 *
 * 数值彼此不同 (12 vs 9 个知识点), 这样"每门课各算一份"才是**可验证**的:
 * 如果实现把两门课的数字合并或者互相覆盖, 页面上一定对不上。
 */
function myCoursesPayload(overrides) {
  return Object.assign(
    {
      view: 'multi-course-workspace-v1',
      language: 'zh',
      course_count: 2,
      selection: {
        preferred: COURSE_ID, course_id: COURSE_ID, reason: 'preferred',
        available: [COURSE_ID, 'course-other'],
      },
      courses: [
        {
          course_id: COURSE_ID,
          name: 'Algebra Lineal',
          code: 'ALG',
          language: 'es',
          counts: {
            sessions: 5, materials: 10, knowledge_points: 12, pending_review: 3,
            conflicts: 1, students: 1, exercises: 4, answers: 7,
          },
          validation: { supported: 11, unverified: 0, conflicted: 1 },
          review: { confirmed: 0, pending: 12, rejected: 0, kept_unverified: 0 },
          evidence_total: 12,
          gaps: 0,
          last_session: {
            session_id: 'session-1', session_number: 3, title: 'Tema 3',
            date: '2026-03-01',
          },
        },
        {
          course_id: 'course-other',
          name: 'Calculo',
          code: 'CAL',
          language: 'es',
          counts: {
            sessions: 4, materials: 8, knowledge_points: 9, pending_review: 2,
            conflicts: 0, students: 2, exercises: 3, answers: 5,
          },
          validation: { supported: 9, unverified: 0, conflicted: 0 },
          review: { confirmed: 0, pending: 9, rejected: 0, kept_unverified: 0 },
          evidence_total: 8,
          gaps: 0,
          last_session: null,
        },
      ],
      totals: {
        counts: {
          sessions: 9, materials: 18, knowledge_points: 21, pending_review: 5,
          conflicts: 1, students: 3, exercises: 7, answers: 12,
        },
        validation: { supported: 20, unverified: 0, conflicted: 1 },
        review: { confirmed: 0, pending: 21, rejected: 0, kept_unverified: 0 },
        evidence_total: 20,
        gaps: 0,
      },
      note: 'per-course counts',
      empty: false,
      empty_note: null,
    },
    overrides
  );
}

/** 学生首页 (Task 40) 的快照形状。 */
function studentDashboard(overrides) {
  return Object.assign(
    {
      course_id: COURSE_ID,
      student_id: STUDENT_ID,
      display_name: 'Ana',
      progress: {
        states: [], state_counts: {}, registered_knowledge_points: [],
        course_knowledge_points: ['kp-1'], not_started_knowledge_points: ['kp-1'],
        exercise_count: 1, answered_count: 0, practice_counts: {},
        recent_incorrect_kps: [], evaluation_counts: {}, average_score: null,
      },
      study_plan: { items: [] },
      learning_paths: [],
      pending_exercises: [{
        exercise_id: EXERCISE_ID, exercise_type: 'fill_blank',
        prompt: 'El grado de la funcion es ___', knowledge_point_ids: ['kp-1'], difficulty: 2,
      }],
      recent_evaluations: [],
      knowledge_gaps: {
        student_gaps: [], not_started_knowledge_points: ['kp-1'], course_gaps: [],
      },
    },
    overrides
  );
}

const ROUTES = {
  '/api/courses': { data: { courses: [{ course_id: COURSE_ID, name: 'Algebra Lineal' }] } },
  // Task 68: 全部课程总览。两门课, 其中一门不是当前课程 —— 这样"当前
  // 课程标记"和"逐课程计数"才真的被渲染到。
  '/api/my-courses': { data: myCoursesPayload() },
  '/api/students': {
    data: {
      students: [{ student_id: STUDENT_ID, display_name: 'Ana' }],
    },
  },
  [`/api/students/${STUDENT_ID}/exercises/${EXERCISE_ID}`]: { data: baseView() },
  [`/api/students/${STUDENT_ID}/exercises`]: {
    data: {
      course_id: COURSE_ID,
      student_id: STUDENT_ID,
      exercises: [
        {
          exercise_id: EXERCISE_ID,
          exercise_type: 'fill_blank',
          prompt: 'El grado de la funcion es ___',
          difficulty: 2,
          answer_format: 'text',
          knowledge_point_ids: ['kp-1'],
          knowledge_points: [{ knowledge_id: 'kp-1', title: 'Definicion de funcion' }],
          prerequisites: ['kp-0'],
          submitted: false,
          answer_id: null,
          submitted_value: null,
          submitted_at: null,
          evaluation_status: null,
          score: null,
        },
      ],
      total: 1,
      answered: 0,
      unanswered: 1,
    },
  },
  // Task 64: 出题面板需要知识点列表来判断"哪些可出题"。
  '/api/knowledge': {
    data: {
      course_id: COURSE_ID,
      knowledge_points: [
        {
          knowledge_id: 'kp-1',
          title: 'Definicion de funcion',
          validation_status: 'supported',
          review_status: 'confirmed',
          evidence_count: 1,
        },
        {
          knowledge_id: 'kp-2',
          title: 'Dominio de una funcion',
          validation_status: 'unverified',
          review_status: 'pending',
          evidence_count: 1,
        },
      ],
    },
  },
  // Task 64: 出题依据链
  [`/api/exercises/${EXERCISE_ID}/grounding`]: {
    data: {
      course_id: COURSE_ID,
      knowledge_points: [
        {
          knowledge_id: 'kp-1',
          title: 'Definicion de funcion',
          validation_status: 'supported',
          review_status: 'confirmed',
        },
      ],
      unknown_knowledge_point_ids: [],
      evidence: [
        {
          evidence_id: 'ev-1',
          evidence_type: 'transcript',
          language: 'ca',
          confidence: 'high',
          content: 'Introduccio al tema (ca)',
          source: { material_id: 'mat-1', page: 1 },
          material: { material_id: 'mat-1', filename: 'tema1.txt', material_type: 'text', language: 'ca' },
        },
      ],
      materials: [
        { material_id: 'mat-1', filename: 'tema1.txt', material_type: 'text', language: 'ca' },
      ],
      unresolved: [],
      complete: true,
      generator_version: 'template-v1',
      template: 'TRUE_FALSE_DEFINITION',
    },
  },
  [`/api/materials/mat-1`]: { data: { material_id: 'mat-1', filename: 'tema1.txt' } },

  // Task 65: 错题与薄弱知识点中心
  [`/api/students/${STUDENT_ID}/mistakes`]: { data: mistakeCenter() },
  [`/api/students/${STUDENT_ID}/mistakes/kp-1`]: { data: mistakeDetail() },
};

// ---------------------------------------------------------------- Task 66 夹具
//
// 学习流程有三条路径必须分别验证: 有任务 / 无证据 / 无任务。用三张路由表
// 而不是一张带开关的表, 是因为"缺字段"本身就是被测对象 —— 让响应里
// 真的没有那个字段, 才能证明页面**没有**偷偷补一个默认解释。

const LEARN_TASK = {
  course_id: COURSE_ID,
  student_id: STUDENT_ID,
  knowledge_point_id: 'kp-1',
  title: 'Definicion de funcion',
  state: 'exposed',
  next_event: 'practiced',
  next_action: 'practiced',
  unmet_prerequisite_ids: [],
};

function learnStart(overrides) {
  return Object.assign(
    {
      course_id: COURSE_ID,
      student_id: STUDENT_ID,
      lang: 'zh',
      workflow_version: 'daily-learning-workflow-v1',
      has_task: true,
      note: null,
      current_task: LEARN_TASK,
      next_task: Object.assign({}, LEARN_TASK, { knowledge_point_id: 'kp-2' }),
      progress: {
        by_state: { not_started: 1, exposed: 1, practicing: 0, reviewing: 0 },
        finished: 0,
        orderable: 2,
        total: 2,
        excluded: { rejected: [], conflicted: ['kp-9'] },
      },
      excluded: { rejected: [], conflicted: ['kp-9'] },
      links: {},
    },
    overrides
  );
}

function learnKnowledge(overrides) {
  return Object.assign(
    {
      course_id: COURSE_ID,
      student_id: STUDENT_ID,
      lang: 'zh',
      knowledge_point: {
        knowledge_id: 'kp-1',
        title: 'Definicion de funcion',
        content: 'Una funcion asocia cada entrada con una salida.',
        original_terms: ['funcio'],
        validation_status: 'supported',
        review_status: 'pending',
        needs_verification: false,
        knowledge_score: 0.5,
        importance: 'medium',
      },
      source_language: 'es',
      evidence: [
        {
          evidence_id: 'ev-1',
          evidence_type: 'document',
          quote: 'Una funcion asocia cada entrada con una salida.',
          material_filename: 'tema1.pdf',
        },
      ],
      materials: [{ material_id: 'mat-1', filename: 'tema1.pdf' }],
      evidence_available: true,
      evidence_note: null,
      grounded_explanation: {
        text: 'Una funcion asocia cada entrada con una salida.',
        requested_language: 'es',
      },
      student_state: 'exposed',
      student_activity: {
        exposure_count: 1,
        practice_count: 0,
        answer_count: 1,
        correct_count: 1,
        incorrect_count: 0,
      },
      prerequisites: ['kp-0'],
      unmet_prerequisites: ['kp-0'],
      next_event: 'practiced',
      next_task: LEARN_TASK,
      truth_flags: {},
    },
    overrides
  );
}

const LEARN_BASE = {
  '/api/courses': { data: { courses: [{ course_id: COURSE_ID, name: 'Algebra Lineal' }] } },
  '/api/students': {
    data: { students: [{ student_id: STUDENT_ID, display_name: 'Ana' }] },
  },
};

// ---- Task 67: 考前复习集合 -------------------------------------------------

const REVIEW_ITEM = (over) =>
  Object.assign(
    {
      knowledge_id: 'kp-r',
      title: 'Dominio de una funcion',
      content: '',
      session_id: 'session-1',
      validation_status: 'supported',
      review_status: 'pending',
      conflict: false,
      conflict_ids: [],
      learning_state: 'not_started',
      learning_signal: 'NOT_STARTED',
      bucket: 'attention',
      block_reason: null,
      block_note: null,
      attention_reason: 'review_pending',
      attention_note: 'No human has confirmed this yet.',
      sort_key: [2, 1, 0, 'kp-r'],
      position: 1,
    },
    over
  );

function reviewSet(overrides) {
  return Object.assign(
    {
      review_mode_version: 'exam-review-mode-v1',
      course_id: COURSE_ID,
      student_id: STUDENT_ID,
      lang: 'zh',
      empty: false,
      empty_note: null,
      by_validation_status: { supported: 2, unverified: 1 },
      by_review_status: { pending: 2, confirmed: 1 },
      by_bucket: { blocked: 1, attention: 1, ready: 1 },
      counts: {
        total: 3, blocked: 1, attention: 1, ready: 1, unresolved_conflicts: 1,
      },
      buckets: { blocked: ['kp-b'], attention: ['kp-a'], ready: ['kp-ok'] },
      items: [
        REVIEW_ITEM({
          knowledge_id: 'kp-b',
          title: 'Definicion en conflicto',
          validation_status: 'unverified',
          conflict: true,
          conflict_ids: ['conflict-1'],
          bucket: 'blocked',
          position: 1,
          block_reason: 'unresolved_conflict',
          block_note: 'Unresolved conflict: both sides are shown as-is.',
          attention_reason: null,
          attention_note: null,
          sort_key: [1, 0, 0, 'kp-b'],
        }),
        REVIEW_ITEM({
          knowledge_id: 'kp-a', title: 'Definicion sin confirmar', position: 2,
        }),
        REVIEW_ITEM({
          knowledge_id: 'kp-ok',
          title: 'Definicion confirmada',
          review_status: 'confirmed',
          bucket: 'ready',
          position: 3,
          attention_reason: null,
          attention_note: null,
          sort_key: [2, 1, 1, 'kp-ok'],
        }),
      ],
      coverage: {
        total_knowledge_points: 3,
        covered_knowledge_points: 2,
        uncovered_knowledge_points: 1,
        coverage_ratio: 0.67,
      },
      conflicts: [
        {
          conflict_id: 'conflict-1',
          evidence_refs: ['evidence-1', 'evidence-2'],
          description: 'Dos fuentes no coinciden.',
          status: 'PENDING',
          auto_resolved: false,
          blocking: true,
          sides: [
            { label: 'Evidence A', evidence_id: 'evidence-1' },
            { label: 'Evidence B', evidence_id: 'evidence-2' },
          ],
        },
      ],
      not_a_predictor: true,
      ordering_basis:
        'coverage -> evidence status -> unresolved conflict -> student learning state -> knowledge_id',
    },
    overrides
  );
}

const REVIEW_ROUTES = Object.assign({}, LEARN_BASE, {
  [`/api/students/${STUDENT_ID}/review-set`]: { data: reviewSet({}) },
});

// 空集合: 有课程有学生, 但没有知识点。
const REVIEW_ROUTES_EMPTY = Object.assign({}, LEARN_BASE, {
  [`/api/students/${STUDENT_ID}/review-set`]: {
    data: reviewSet({
      empty: true,
      empty_note: 'No review set available yet.',
      by_validation_status: {},
      by_review_status: {},
      by_bucket: { blocked: 0, attention: 0, ready: 0 },
      counts: {
        total: 0, blocked: 0, attention: 0, ready: 0, unresolved_conflicts: 0,
      },
      buckets: { blocked: [], attention: [], ready: [] },
      items: [],
      conflicts: [],
      coverage: {
        total_knowledge_points: 0, covered_knowledge_points: 0, coverage_ratio: 0,
      },
    }),
  },
});

const LEARN_ROUTES = Object.assign({}, LEARN_BASE, {
  [`/api/students/${STUDENT_ID}/learning/start`]: { data: learnStart({}) },
  [`/api/students/${STUDENT_ID}/learning/knowledge/kp-1`]: {
    data: learnKnowledge({}),
  },
});

// 有知识条目但**没有任何证据**: 页面必须明说缺失。
const LEARN_ROUTES_NO_EVIDENCE = Object.assign({}, LEARN_BASE, {
  [`/api/students/${STUDENT_ID}/learning/start`]: { data: learnStart({}) },
  [`/api/students/${STUDENT_ID}/learning/knowledge/kp-2`]: {
    data: learnKnowledge({
      knowledge_point: {
        knowledge_id: 'kp-2',
        title: 'Dominio de una funcion',
        content: '',
        original_terms: [],
        validation_status: 'unverified',
        review_status: 'pending',
        needs_verification: true,
        knowledge_score: 0.0,
        importance: 'low',
      },
      evidence: [],
      materials: [],
      evidence_available: false,
      evidence_note: 'Evidence unavailable.',
      grounded_explanation: { text: '', requested_language: 'es' },
      student_state: 'not_started',
      student_activity: {
        exposure_count: 0, practice_count: 0, answer_count: 0,
        correct_count: 0, incorrect_count: 0,
      },
      prerequisites: [],
      unmet_prerequisites: [],
      next_event: 'viewed',
      next_task: null,
    }),
  },
});

// 全部知识点都到终态: has_task 必须是 false, 且措辞是事实性的。
const LEARN_ROUTES_NO_TASK = Object.assign({}, LEARN_BASE, {
  [`/api/students/${STUDENT_ID}/learning/start`]: {
    data: learnStart({
      has_task: false,
      note: 'No learning task available.',
      current_task: null,
      next_task: null,
      progress: {
        by_state: { not_started: 0, exposed: 0, practicing: 0, reviewing: 2 },
        finished: 2,
        orderable: 2,
        total: 2,
        excluded: { rejected: [], conflicted: [] },
      },
    }),
  },
});

// ---------------------------------------------------------------- Task 65 夹具

/**
 * 错题本视图。刻意让"错答"和"薄弱"**不同步** ——
 * kp-1 有 2 次错答但 attention 为 null（学生状态还没走到 NEEDS_*），
 * 这正是 spec 65.6 要防的过度推断: 错了但没被判定薄弱。
 */
function mistakeCenter(overrides) {
  const wrongRow = {
    answer_id: 'answer-1',
    evaluation_id: 'evaluation-1',
    exercise_id: EXERCISE_ID,
    exercise_type: 'fill_blank',
    prompt: 'El grado de la funcion es ___',
    submitted_value: 'dos',
    sequence: 1,
    submitted_at: '2026-01-01T00:00:00+00:00',
    status: 'incorrect',
    score: 0,
    feedback: null,
    knowledge_point_ids: ['kp-1'],
    knowledge: [{ knowledge_id: 'kp-1', title: 'Definicion de funcion' }],
    evaluator_version: 'exact-v1',
  };
  const leaf = {
    exercise_id: EXERCISE_ID,
    prompt: 'El grado de la funcion es ___',
    status: 'incorrect',
    answer_id: 'answer-1',
  };
  return Object.assign({
    schema_version: 1,
    center_version: 'mistake-center-v1',
    course_id: COURSE_ID,
    student_id: STUDENT_ID,
    lang: 'zh',
    group_by: 'knowledge',
    has_mistakes: true,
    note: null,
    mistakes: [wrongRow],
    knowledge: [
      {
        knowledge_id: 'kp-1',
        title: 'Definicion de funcion',
        excerpt: 'Una funcion asocia cada valor',
        validation_status: 'supported',
        review_status: 'confirmed',
        language: 'ca',
        incorrect_attempts: 2,
        exercise_ids: [EXERCISE_ID],
        answer_ids: ['answer-1'],
        student_state: 'exposed',
        attention: null,
        attention_basis: null,
        mistakes: [wrongRow],
        suggested_actions: [],
        evidence_count: 1,
        practice_targets: [],
      },
    ],
    groups: [
      {
        group_kind: 'knowledge',
        group_id: 'kp-1',
        title: 'Definicion de funcion',
        incorrect_attempts: 2,
        attention: null,
        attention_basis: null,
        children_kind: 'exercise',
        children: [leaf],
      },
    ],
    weak_knowledge: [],
    counts: {
      mistakes: 1,
      knowledge_with_mistakes: 1,
      groups: 1,
      weak_knowledge: 0,
      incorrect_attempts: 2,
    },
    links: {
      review_center: `/api/courses/${COURSE_ID}/review`,
      exercises: `/api/students/${STUDENT_ID}/exercises`,
      exercise_workflow: '/api/exercise-generation',
    },
    limits: { mistakes: 200 },
  }, overrides || {});
}

/** 按主题分组: 三层 topic -> knowledge -> exercise。 */
function mistakeCenterByTopic() {
  return mistakeCenter({
    group_by: 'topic',
    groups: [
      {
        group_kind: 'topic',
        group_id: 'topic-1',
        title: 'Funcions',
        unassigned: false,
        incorrect_attempts: 2,
        children_kind: 'knowledge',
        children: [
          {
            group_kind: 'knowledge',
            group_id: 'kp-1',
            title: 'Definicion de funcion',
            incorrect_attempts: 2,
            attention: null,
            children_kind: 'exercise',
            children: [
              {
                exercise_id: EXERCISE_ID,
                prompt: 'El grado de la funcion es ___',
                status: 'incorrect',
                answer_id: 'answer-1',
              },
            ],
          },
        ],
      },
    ],
  });
}

/** 空错题本。 */
function mistakeCenterEmpty() {
  return mistakeCenter({
    has_mistakes: false,
    note: 'No mistakes yet.',
    mistakes: [],
    knowledge: [],
    groups: [],
    weak_knowledge: [],
    counts: {
      mistakes: 0,
      knowledge_with_mistakes: 0,
      groups: 0,
      weak_knowledge: 0,
      incorrect_attempts: 0,
    },
  });
}

/** 知识点错题详情: 错题 -> 为什么错 -> 重新学习依据。 */
function mistakeDetail(overrides) {
  return Object.assign({
    schema_version: 1,
    center_version: 'mistake-center-v1',
    course_id: COURSE_ID,
    student_id: STUDENT_ID,
    knowledge: {
      knowledge_id: 'kp-1',
      title: 'Definicion de funcion',
      excerpt: 'Una funcion asocia cada valor',
      validation_status: 'supported',
      review_status: 'confirmed',
      language: 'ca',
    },
    why_incorrect: [
      {
        answer_id: 'answer-1',
        exercise_id: EXERCISE_ID,
        prompt: 'El grado de la funcion es ___',
        submitted_value: 'dos',
        status: 'incorrect',
        score: 0,
        feedback: null,
        evaluator_version: 'exact-v1',
      },
    ],
    incorrect_attempts: 2,
    student_state: 'exposed',
    attention: null,
    attention_basis: null,
    evidence: [
      {
        evidence_id: 'ev-1',
        evidence_type: 'transcript',
        language: 'ca',
        confidence: 'high',
        content: 'Introduccio al tema (ca)',
        source: { material_id: 'mat-1', location: 'segment-1' },
        material: { material_id: 'mat-1', filename: 'tema1.txt', material_type: 'text', language: 'ca' },
      },
    ],
    prerequisites: [],
    practice_targets: [
      {
        exercise_id: EXERCISE_ID,
        exercise_type: 'fill_blank',
        prompt: 'El grado de la funcion es ___',
        difficulty: null,
        reused: true,
        previously_attempted: true,
        previously_incorrect: true,
      },
    ],
    suggested_actions: [
      {
        action: 'REVIEW_KNOWLEDGE',
        target: 'kp-1',
        href: '/api/knowledge/kp-1',
        basis: 'the knowledge point this mistake is attached to',
      },
      {
        action: 'VIEW_EVIDENCE',
        target: 'kp-1',
        href: '/api/knowledge/kp-1/evidence-trace',
        basis: '1 evidence row(s) resolved',
      },
      {
        action: 'PRACTICE_AGAIN',
        target: EXERCISE_ID,
        targets: [EXERCISE_ID],
        href: '/api/exercises/' + EXERCISE_ID,
        basis: '1 existing exercise(s) reused (no new generation)',
      },
    ],
  }, overrides || {});
}

// ---------------------------------------------------------------- DOM 桩

/**
 * 顶栏导航的 href 列表 (与 index.html 的 `<nav class="topnav">` 一致)。
 *
 * 为什么要在桩里给出这组元素: 顶栏的 `active` **完全由页面函数设置**
 * (index.html 里没有任何硬编码), 而 `markActiveNav()` 只操作
 * `document.querySelectorAll('.topnav a')`。桩返回空数组的话, 它就永远是个
 * 空转调用 —— "高亮的位置不是用户所在的位置" 这类缺陷于是测不出来。
 * 实测代价: `pageLearnKnowledge()` 漏设高亮活了很久, 刷新后整条导航无高亮。
 *
 * 顺序必须与 index.html 的 `<nav class="topnav">` **逐项相同** —— 2026-09-21
 * 按用途分了两组 (P3-3), 概览从第三位提到第一位, 中间多了一个纯装饰的
 * `<span class="topnav-sep">` (它不匹配 `.topnav a`, 所以不进这个数组)。
 * 2026-09-22: 4 个未实现入口在顶栏里是无 href 的 `<span class="nav-disabled">`
 * (不可点击、不触发路由、永远进不了 active), 它们同样不进这个数组 ——
 * `markActiveNav()` 只扫 `.topnav a`, 与真实 DOM 的行为一致。
 */
const TOPNAV = [
  '#/', '#/today', '#/reviews',
  '#/courses', '#/materials', '#/knowledge', '#/students',
];

function makeNavLink(href) {
  const classes = new Set();
  return {
    href,
    getAttribute: (name) => (name === 'href' ? href : null),
    classList: {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      contains: (name) => classes.has(name),
      toggle: (name) => (classes.has(name) ? classes.delete(name) : classes.add(name)),
    },
    __classes: classes,
  };
}

function makeElement(id) {
  return {
    id,
    innerHTML: '',
    textContent: '',
    className: '',
    value: '',
    style: {},
    dataset: {},
    title: '',
    addEventListener() {},
    removeEventListener() {},
    setAttribute() {},
    getAttribute() {
      return null;
    },
    appendChild() {},
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    closest() {
      return null;
    },
  };
}

/**
 * @param {object} routeTable 桩 API 的路由表。
 * @param {object} [initialStorage] 预先写进 localStorage 的值。
 * @param {object} [initialSession] 预先写进 sessionStorage 的值 (任务坞的
 *   跨刷新恢复用它模拟"上一次会话留下的运行中任务", 见 P3-2)。
 *
 * `initialStorage` 必须在 app.js **执行之前**写好 —— 因为 `const state = {...}`
 * 在加载时就从 localStorage 读初始值。先建 sandbox 再 setItem 是无效的:
 * `state` 已经读完一轮了 (这一点很隐蔽, 会让"初始值"的用例变成空跑)。
 */
function makeSandbox(routeTable, initialStorage, initialSession) {
  const elements = new Map();
  const storage = new Map(Object.entries(initialStorage || {}));
  const session = new Map(Object.entries(initialSession || {}));
  const fetched = [];
  const posted = [];
  const navLinks = TOPNAV.map(makeNavLink);

  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  };

  const document = {
    getElementById: getElement,
    querySelector: () => null,
    querySelectorAll: (selector) => (selector === '.topnav a' ? navLinks : []),
    addEventListener() {},
    createElement: (tag) => makeElement(tag),
    documentElement: makeElement('html'),
  };

  const window = {
    location: { hash: '#/' },
    localStorage: {
      getItem: (key) => (storage.has(key) ? storage.get(key) : null),
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: (key) => storage.delete(key),
    },
    sessionStorage: {
      getItem: (key) => (session.has(key) ? session.get(key) : null),
      setItem: (key, value) => session.set(key, String(value)),
      removeItem: (key) => session.delete(key),
    },
    addEventListener() {},
    scrollTo() {},
    matchMedia: () => ({ matches: false, addEventListener() {} }),
  };

  // 可控的定时器。前端只有两处用 setTimeout: toast 的自动消失, 以及任务坞
  // 恢复后的"下一拍"轮询。后者每拍间隔 5 秒 —— 用真实定时器的话, 断言
  // "连续两拍都没人动过这个作业" 就得真的等 10 秒, 而"5 分钟上限"根本没法测。
  // 所以排进队列由测试显式 flush (见 flushTimers), 且 queue 空时进程立刻退出。
  const timers = [];
  let timerSeq = 0;
  const fakeSetTimeout = (fn, ms) => { timers.push({ id: ++timerSeq, fn, ms }); return timerSeq; };
  const fakeClearTimeout = (id) => {
    const index = timers.findIndex((timer) => timer.id === id);
    if (index >= 0) timers.splice(index, 1);
  };

  async function fetchStub(url, init) {
    fetched.push(url);
    const method = String((init && init.method) || 'GET').toUpperCase();
    if (method !== 'GET') posted.push(method + ' ' + url);
    const pathOnly = String(url).split('?')[0];
    const route = routeTable[pathOnly];
    if (!route) {
      return {
        status: 404,
        async json() {
          return { success: false, error: { code: 'NOT_FOUND', message: 'no route ' + pathOnly } };
        },
      };
    }
    return { status: 200, async json() { return { success: true, data: route.data }; } };
  }

  const sandbox = {
    window,
    document,
    localStorage: window.localStorage,
    sessionStorage: window.sessionStorage,
    fetch: fetchStub,
    console,
    setTimeout: fakeSetTimeout,
    clearTimeout: fakeClearTimeout,
    URLSearchParams,
    FormData,
    // 与 api.js 的 ApiError **同形** —— 必须把 code 从 payload 里取出来。
    // 曾经是个空壳 (构造器忽略实参), 于是 `err.code` 恒为 undefined: 任何
    // "按错误码分支"的前端逻辑在桩里都走不到, 测试于是测不到它。
    ApiError: class ApiError extends Error {
      constructor(error) {
        super((error && error.message) || 'request failed');
        this.code = (error && error.code) || 'INTERNAL_ERROR';
        this.detail = (error && error.detail) || null;
      }
    },
    __elements: elements,
    __fetched: fetched,
    __posted: posted,
    __nav: navLinks,
    __session: session,
    __timers: timers,
  };
  sandbox.globalThis = sandbox;
  return sandbox;
}

/**
 * 把沙箱里排队中的定时器回调全部跑掉, 直到队列空或推进了 `rounds` 轮。
 *
 * 任务坞的每一拍都会在**自己结束时**再排下一拍, 所以"取空队列"不等于
 * "没有下一拍了" —— 必须轮着来。每轮之间让出一轮微任务, 好让被唤醒的
 * 那拍真正跑到它注册下一拍的位置。
 */
async function flushTimers(sandbox, rounds) {
  const limit = rounds || 10;
  for (let round = 0; round < limit && sandbox.__timers.length; round += 1) {
    const due = sandbox.__timers.splice(0, sandbox.__timers.length);
    due.forEach((timer) => timer.fn());
    await new Promise((resolve) => setImmediate(resolve));
  }
}

async function loadApp(routeTable, initialStorage, initialSession) {
  const sandbox = makeSandbox(routeTable, initialStorage, initialSession);
  vm.createContext(sandbox);
  // 按加载顺序逐段执行 —— 等价于浏览器里多个 <script> 共享全局作用域。
  for (const source of WEB_SOURCES) {
    vm.runInContext(source.code, sandbox, { filename: source.name });
  }
  return sandbox;
}

/**
 * 从 vm 上下文里取一个顶层函数。
 *
 * vm.runInContext 会把顶层 `function` 声明挂到 context 上, 但
 * `async function` 与 `const` 在 `runInContext` 下不一定会成为
 * context 的自有属性 —— 取决于脚本顶层词的解析方式。用
 * `vm.runInContext('fnName', ctx)` 求值最稳: 它走的是同一套作用域,
 * 拿到的就是 app.js 里那个函数本身。
 */
function grab(sandbox, name) {
  return vm.runInContext(name, sandbox, { filename: 'grab:' + name });
}

// ---------------------------------------------------------------- 断言

const failures = [];
let checks = 0;

function check(name, condition, detail) {
  checks += 1;
  if (!condition) failures.push(name + (detail ? ' — ' + detail : ''));
}

function html(sandbox) {
  return sandbox.document.getElementById('view').innerHTML;
}

/** 当前处于 `active` 的顶栏项 (应当**恰好一个**, 且与所在页面一致)。 */
function activeNav(sandbox) {
  return sandbox.__nav.filter((link) => link.__classes.has('active')).map((link) => link.href);
}

// ---------------------------------------------------------------- 用例

async function main() {
  // 1) 单题页: 提交前 —— 渲染出的 HTML 里绝不能有答案
  {
    const sandbox = await loadApp(ROUTES);
    await sandbox.pageExercise(COURSE_ID, EXERCISE_ID, STUDENT_ID);
    const out = html(sandbox);

    check('exercise page renders the prompt', out.includes('El grado de la funcion es ___'));
    check('exercise page renders the type', out.includes('fill_blank'));
    check('exercise page renders the linked knowledge point', out.includes('Definicion de funcion'));
    check('exercise page renders the prerequisite link', out.includes('kp-0'));
    check('exercise page renders the grounding evidence', out.includes('Introduccio al tema (ca)'));
    check('exercise page renders an answer input', out.includes('name="answer_value"'));
    check('exercise page renders a submit button', out.includes('answer-form'));
    check('exercise page shows the answer is withheld', out.includes('answer_key_withheld') || out.includes('答案已隐藏'));
    check('answer is NOT in the rendered HTML', !out.includes(SECRET_ANSWER), 'leaked accepted_answers');
    check('explanation is NOT in the rendered HTML', !out.includes(SECRET_EXPLANATION), 'leaked explanation');
    check('no evaluation panel data before submit', !out.includes('evaluation-1'));
  }

  // 2) 单题页: 提交后 —— 答案作为反馈出现
  {
    const routes = Object.assign({}, ROUTES);
    routes[`/api/students/${STUDENT_ID}/exercises/${EXERCISE_ID}`] = { data: evaluatedView() };
    const sandbox = await loadApp(routes);
    await sandbox.pageExercise(COURSE_ID, EXERCISE_ID, STUDENT_ID);
    const out = html(sandbox);

    check('evaluation panel is rendered', out.includes('evaluation-panel'));
    check('evaluation shows the score', out.includes('0'));
    check('evaluation shows the status', out.toLowerCase().includes('incorrect'));
    check('evaluation shows the evaluator version', out.includes('exact-v1'));
    check('reference answer appears as feedback', out.includes(SECRET_ANSWER));
    check('explanation appears as feedback', out.includes(SECRET_EXPLANATION));
    check('explicitly states it is not fact verification', out.includes('不是对知识的核验'));
    check('shows the submitted answer', out.includes('uno'));
    check('shows the submission timestamp', out.includes('2026-01-01T00:00:00+00:00'));
  }

  // 3) 练习列表
  {
    const sandbox = await loadApp(ROUTES);
    await sandbox.pageExercises();
    const out = html(sandbox);
    check('exercise list renders the row', out.includes('El grado de la funcion es ___'));
    check('exercise list links to the exercise', out.includes('#/courses/' + COURSE_ID + '/exercises/' + EXERCISE_ID));
    check('exercise list shows the unanswered pill', out.includes('未作答'));
  }

  // 3b) Task 64: 出题面板 —— 可出题/不可出题分开显示, 不隐藏被挡住的知识点
  {
    const sandbox = await loadApp(ROUTES);
    await sandbox.pageExercises();
    const out = html(sandbox);
    check('generation panel is rendered', out.includes('id="generation-result"'));
    check('generation button is rendered', out.includes('id="generate-batch"'));
    check('generation button carries the course id', out.includes('data-course="' + COURSE_ID + '"'));
    check(
      'the knowledge list is fetched for the generation panel',
      sandbox.__fetched.some((u) => u.indexOf('/api/knowledge') >= 0),
      sandbox.__fetched.join(', ')
    );
    check('unverified knowledge is listed as blocked', out.includes('kp-2'));
    check('unverified knowledge keeps its status pill', out.includes('unverified'));
  }

  // 3c) Task 64: 出题依据链 (Exercise -> KP -> Evidence -> Material)
  {
    const sandbox = await loadApp(ROUTES);
    await sandbox.pageExercise(COURSE_ID, EXERCISE_ID, STUDENT_ID);
    await grab(sandbox, 'loadGroundingChain')(COURSE_ID, EXERCISE_ID);
    const body = sandbox.__elements.get('grounding-body');
    const out = body ? body.innerHTML : '';
    check('grounding card is rendered', sandbox.__elements.has('grounding-body'));
    check(
      'grounding chain is requested from the exercise endpoint',
      sandbox.__fetched.some((u) => u.indexOf('/exercises/' + EXERCISE_ID + '/grounding') >= 0),
      sandbox.__fetched.join(', ')
    );
    check('grounding chain shows the resolved knowledge point', out.includes('Definicion de funcion'));
    check('grounding chain shows the evidence', out.includes('Introduccio al tema (ca)'));
    check('grounding chain shows the source material', out.includes('tema1.txt'));
    check('grounding chain reports it is complete', out.includes('链路完整'));
    check('grounding chain names the generator version', out.includes('template-v1'));
    check('grounding chain does not leak an unresolved banner', !out.includes('ground.unresolved'));
  }

  // 4) 提交走 Task 32 的端点 (UI 不自己判分)
  {
    const sandbox = await loadApp(ROUTES);
    await sandbox.pageExercise(COURSE_ID, EXERCISE_ID, STUDENT_ID);
    const before = sandbox.__fetched.length;
    // Task 64 后单题页是「学生视角的题目本体 + 出题依据链」两个请求;
    // 2026-09-21 修 P3-6 (作答学生可能串到别的课程) 时, 前面补了一次
    // 「先按课程拉学生列表」的校验请求 —— 于是固定三个只读 GET。
    // 数量必须稳定, 否则说明页面产生了重复请求。
    check('exercise page issues exactly three requests', before === 3, 'fetched=' + before);
    check(
      'the first request resolves the students of this course',
      sandbox.__fetched[0].split('?')[0] === '/api/students' &&
        sandbox.__fetched[0].indexOf('course_id=' + COURSE_ID) >= 0,
      sandbox.__fetched[0]
    );
    check(
      'the second request goes to the student-facing endpoint',
      sandbox.__fetched[1].indexOf('/students/' + STUDENT_ID + '/exercises/' + EXERCISE_ID) >= 0,
      sandbox.__fetched[1]
    );
    check(
      'the third request is the grounding chain',
      sandbox.__fetched[2].split('?')[0] === '/api/exercises/' + EXERCISE_ID + '/grounding',
      sandbox.__fetched[2]
    );
    check(
      'the page never writes through the authoring endpoint',
      sandbox.__fetched.every((u) => u.split('?')[0] !== '/api/exercises'),
      sandbox.__fetched.join(', ')
    );
    check(
      'the exercise page never POSTs on load',
      sandbox.__posted.length === 0,
      sandbox.__posted.join(', ')
    );
  }

  // 4b) Task 65: 错题本 —— 错题表 / 分组 / 薄弱 / 建议动作
  {
    const sandbox = await loadApp(ROUTES);
    await sandbox.pageMistakes();
    const out = html(sandbox);

    check('mistake center renders the title', out.includes('错题与薄弱知识点'));
    check('mistake center renders the exercise column', out.includes('El grado de la funcion es ___'));
    check('mistake center renders the submitted answer', out.includes('dos'));
    check('mistake center renders the evaluation status', out.includes('incorrect'));
    check('mistake center renders the evaluation id', out.includes('evaluation-1'));
    check('mistake center links to the exercise', out.includes('#/courses/' + COURSE_ID + '/exercises/' + EXERCISE_ID));
    check('mistake center links to the knowledge point', out.includes('#/courses/' + COURSE_ID + '/knowledge/kp-1'));
    check('mistake center renders the incorrect-attempt count', out.includes('2'));
    check('mistake center fetched the mistake endpoint',
      sandbox.__fetched.some((u) => u.split('?')[0] === '/api/students/' + STUDENT_ID + '/mistakes'),
      sandbox.__fetched.join(', '));
    check('mistake center requests group_by=knowledge by default',
      sandbox.__fetched.some((u) => u.indexOf('group_by=knowledge') >= 0),
      sandbox.__fetched.join(', '));
    check('mistake center never POSTs', sandbox.__posted.length === 0, sandbox.__posted.join(', '));
    // spec 65.6: 错了 2 次但学生状态没到 NEEDS_*, 就**不能**出现在薄弱区
    check('mistake center does not infer weak knowledge from wrong counts',
      out.includes('当前没有标记为薄弱的知识点'));
    // 不能说"已掌握"
    check('mistake center never claims mastery', !/掌握|mastered|domina/i.test(out));
  }

  // 4c) Task 65: 空错题本 —— "No mistakes yet." 正常显示, 不是错误页
  {
    const routes = Object.assign({}, ROUTES);
    routes[`/api/students/${STUDENT_ID}/mistakes`] = { data: mistakeCenterEmpty() };
    const sandbox = await loadApp(routes);
    await sandbox.pageMistakes();
    const out = html(sandbox);
    check('empty mistake center shows the empty note', out.includes('暂无学习活动'));
    check('empty mistake center explains the empty state', out.includes('做完练习并提交作答后'));
    check('empty mistake center shows zeroed counts',
      out.includes('错答</div><div class="stat-value">0</div>'));
    check('empty mistake center is not an error page', !out.includes('INTERNAL_ERROR'));
    check('empty mistake center shows no traceback', !/Traceback|at Object\./.test(out));
  }

  // 4d) Task 65: 知识点错题详情 —— 错题 -> 为什么错 -> 证据 -> 动作 -> 复习
  {
    const sandbox = await loadApp(ROUTES);
    await sandbox.pageMistakeDetail(COURSE_ID, 'kp-1');
    const out = html(sandbox);

    check('mistake detail renders the why-incorrect section', out.includes('为什么错'));
    check('mistake detail shows the existing feedback source', out.includes('exact-v1'));
    check('mistake detail renders the evidence section', out.includes('Introduccio al tema (ca)'));
    check('mistake detail names the source material', out.includes('tema1.txt'));
    check('mistake detail renders the suggested actions', out.includes('复习该知识点'));
    check('mistake detail renders the practice section', out.includes('再练一次'));
    check('mistake detail marks a previously wrong exercise', out.includes('曾答错'));
    check('mistake detail states the weakness basis', out.includes('学生状态'));
    check('mistake detail fetched the detail endpoint',
      sandbox.__fetched.some((u) => u.split('?')[0] === '/api/students/' + STUDENT_ID + '/mistakes/kp-1'),
      sandbox.__fetched.join(', '));
    check('mistake detail never POSTs', sandbox.__posted.length === 0, sandbox.__posted.join(', '));
    check('mistake detail never claims mastery', !/掌握|mastered|domina/i.test(out));
    check('mistake detail shows no traceback', !/Traceback|at Object\./.test(out));
  }

  // 4e) Task 65: 按主题分组 —— Topic -> Knowledge -> Exercise 三层
  {
    const routes = Object.assign({}, ROUTES);
    routes[`/api/students/${STUDENT_ID}/mistakes`] = { data: mistakeCenterByTopic() };
    const sandbox = await loadApp(routes);
    await sandbox.pageMistakes();
    const out = html(sandbox);
    check('topic grouping shows the topic title', out.includes('Funcions'));
    check('topic grouping nests the knowledge point', out.includes('Definicion de funcion'));
    check('topic grouping nests the exercise', out.includes('El grado de la funcion es ___'));
    check('topic grouping fetched group_by=topic',
      sandbox.__fetched.some((u) => u.indexOf('group_by=topic') >= 0),
      sandbox.__fetched.join(', '));
  }

  // 5) 学生首页 (Task 40) 仍可渲染, 且练习可点进详情
  {
    const routes = Object.assign({}, ROUTES);
    routes[`/api/students/${STUDENT_ID}/dashboard`] = { data: studentDashboard() };
    routes['/api/courses'] = { data: { courses: [] } };
    const sandbox = await loadApp(routes);
    await sandbox.pageStudent(COURSE_ID, STUDENT_ID);
    const out = html(sandbox);
    check('student page still renders', out.includes('Ana') || out.includes('student'));
    check(
      'pending exercise links to the exercise page',
      out.includes('#/courses/' + COURSE_ID + '/exercises/' + EXERCISE_ID)
    );
  }

  // 6) i18n: 三语表 key 完全一致
  {
    const sandbox = await loadApp(ROUTES);
    // I18N 是 const, 不会挂到 sandbox 对象上, 只能在同一个 vm 上下文里求值。
    const keysOf = (lang) =>
      vm.runInContext(`Object.keys(I18N.${lang}).sort().join('|')`, sandbox);
    check('zh/es key parity', keysOf('zh') === keysOf('es'));
    check('zh/ca key parity', keysOf('zh') === keysOf('ca'));
    check(
      't() resolves a real key',
      vm.runInContext("t('ex.title') !== 'ex.title'", sandbox)
    );
    check(
      't() falls back to the key when missing',
      vm.runInContext("t('no.such.key') === 'no.such.key'", sandbox)
    );
  }

  // 7) Task 63: 学生今日首页 —— 派生视图, 只读, 不得出现掌握度话术
  {
    const todayRoutes = Object.assign({}, ROUTES);
    todayRoutes['/api/student-today'] = { data: studentToday() };
    const sandbox = await loadApp(todayRoutes);
    await sandbox.pageToday();
    const out = html(sandbox);

    check('today page renders', out.length > 0);
    check(
      'today page reads the derived student view',
      sandbox.__fetched.some((u) => u.indexOf('/api/student-today') === 0),
      sandbox.__fetched.join(', ')
    );
    check(
      'today page issues a bounded number of requests',
      sandbox.__fetched.length <= 3,
      'fetched=' + sandbox.__fetched.length
    );
    // 63.6 / 63.7: 必须能点进既有页面
    check('today links to the review center', out.includes('href="#/reviews"'));
    check('today links to the exercise center', out.includes('href="#/exercises"'));
    check(
      'today links to the exercise detail',
      out.includes('#/courses/' + COURSE_ID + '/exercises/' + EXERCISE_ID)
    );
    // 63.4: 学习任务来自 StudyPlan, 并给出 Continue Learning
    check('today renders the study block', out.includes('plan-abc123'));
    check('today offers continue learning', out.includes('继续学习'));
    // 63.5: 学习路径 Current / Prerequisite / Next
    check('today renders the learning path node', out.includes('Definicion de funcion'));
    check('today renders the prerequisite', out.includes('kp-prereq-1'));
    // 63.9: Attention 必须带 basis
    check('today renders the attention basis', out.includes('StudentState state=practicing'));
    // 63.8: Recent Evaluations 必须显示为评估, 不是掌握度
    check('today renders the recent evaluation row', out.includes('answer-1') || out.includes(EXERCISE_ID));
    check('today states evaluations are not fact verification', out.includes('不是对知识的核验'));
    // 禁止项
    const forbidden = /mastery|mastered|proficiency|proficient|已掌握|掌握度/i;
    check('today never claims mastery', !forbidden.test(out));
    check('today never renders a stack trace', !/Traceback|at Object\.|File "/.test(out));
  }

  // 8) Task 63.10: 空学生 —— "No learning activity yet." 必须正常, 不能崩
  {
    const emptyRoutes = Object.assign({}, ROUTES);
    emptyRoutes['/api/student-today'] = {
      data: Object.assign(studentToday(), {
        has_activity: false,
        note: 'No learning activity yet.',
        study: [],
        learning_paths: [],
        pending_exercises: [],
        recent_evaluations: [],
        attention: [],
        review: { items: [], total: 0, path: '/api/reviews' },
        counts: {
          classes_today: 0, study_tasks: 0, learning_paths: 0, pending_exercises: 0,
          recent_evaluations: 0, attention: 0, review: 0, pending_materials: 0,
        },
      }),
    };
    const sandbox = await loadApp(emptyRoutes);
    await sandbox.pageToday();
    const out = html(sandbox);
    check('empty student shows the no-activity note', out.includes('No learning activity yet.'));
    check('empty student still renders a page', out.length > 0);
    check('empty student never renders a stack trace', !/Traceback|at Object\.|File "/.test(out));
  }

  // ---------------------------------------------- Task 66: 今天的学习流程
  //
  // 这一组要抓的是"渲染层会自己下结论"这类问题。spec 66.8 明令禁止
  // "You mastered this topic." 式的措辞 —— 而这类句子最容易在渲染层
  // 被顺手加上, 因为渲染层手里正好有 correct_count。
  {
    const sandbox = await loadApp(LEARN_ROUTES);
    await sandbox.pageLearn();
    const out = html(sandbox);

    check('learn page renders the entry title', out.includes('今天的学习'));
    check('learn page shows the current task', out.includes('当前任务'));
    check('learn page never renders a stack trace', !/Traceback|at Object\.|File "/.test(out));
    // 铁律: 渲染层不得替产品宣布"掌握"
    check(
      'learn page never claims mastery',
      !/mastery|mastered|proficient|You mastered/i.test(out)
    );
    check(
      'learn page never predicts exam outcomes',
      !/probabilidad|most likely|probable en el examen/i.test(out)
    );
  }

  // 两个 truth axis 必须分别出现在页面上, 不能被合成一个徽章。
  {
    const sandbox = await loadApp(LEARN_ROUTES);
    await sandbox.pageLearnKnowledge(COURSE_ID, 'kp-1');
    const out = html(sandbox);

    check('knowledge page shows validation status', out.includes('证据支持度'));
    check('knowledge page shows review status', out.includes('人工审核状态'));
    check(
      'knowledge page explains the two axes are independent',
      out.includes('两个独立的轴')
    );
    check('knowledge page lists evidence', out.includes('支撑证据'));
    check('knowledge page shows the grounded explanation', out.includes('基于既有材料的解释'));
    check('knowledge page explains counts are not mastery', out.includes('计数是事实，不是掌握度'));
    check(
      'knowledge page never says you mastered it',
      !/你已掌握|已经掌握|mastery|mastered/i.test(out)
    );
    check('knowledge page never renders a stack trace', !/Traceback|at Object\.|File "/.test(out));
  }

  // 无证据必须**明说**缺失, 而不是编一段解释。
  {
    const sandbox = await loadApp(LEARN_ROUTES_NO_EVIDENCE);
    await sandbox.pageLearnKnowledge(COURSE_ID, 'kp-2');
    const out = html(sandbox);
    check('no-evidence page says evidence is unavailable', out.includes('Evidence unavailable.'));
    check(
      'no-evidence page does not invent contents',
      !/EXPLICACION_SECRETA/.test(out)
    );
  }

  // 没有任务时必须说 "No learning task available.", 不得说"你学完了"。
  {
    const sandbox = await loadApp(LEARN_ROUTES_NO_TASK);
    await sandbox.pageLearn();
    const out = html(sandbox);
    check('no-task page uses the factual wording', out.includes('No learning task available.'));
    check(
      'no-task page never congratulates the student',
      !/你已经学完|全部掌握|all done|you have finished learning/i.test(out)
    );
  }

  // 今日页必须真的有「开始今天的学习」入口 (词条存在但从未接线是真实缺陷)。
  {
    const todayRoutes = Object.assign({}, ROUTES);
    todayRoutes['/api/student-today'] = { data: studentToday() };
    const sandbox = await loadApp(todayRoutes);
    await sandbox.pageToday();
    const out = html(sandbox);
    check('today page links to the daily learning flow', out.includes('#/learn'));
    check('today page shows the start button label', out.includes('开始今天的学习'));
  }

  // ------------------------------------------------------ Task 67 复习模式
  //
  // 这一组要抓的是"复习模式滑向考试预测器"。spec 67 点名六条中文禁止语;
  // 它们最容易被写进副标题或"为什么推荐这个"这类解释文案里。
  {
    const sandbox = await loadApp(REVIEW_ROUTES);
    await sandbox.pageReview();
    const out = html(sandbox);

    check('review page renders the title', out.includes('考前复习'));
    check(
      'review page declares it is not a predictor',
      out.includes('这不是考试预测')
    );

    for (const phrase of ['最可能考', '考试概率', '预测题', '押题', '考点概率', '预测分数']) {
      check('review page never says ' + phrase, !out.includes(phrase), phrase);
    }
    check(
      'review page never claims mastery',
      !/mastery|mastered|已掌握|掌握度/i.test(out)
    );

    // 两条真相轴各自成列
    check('review page lists the validation axis', out.includes('证据状态'));
    check('review page lists the review axis', out.includes('人工审核'));
    check(
      'review page explains the two axes are independent',
      out.includes('这两条是独立的')
    );

    // 三个桶都要真的出现
    check('review page shows the blocked bucket', out.includes('需先解决'));
    check('review page shows the attention bucket', out.includes('需先确认'));
    check('review page shows the ready bucket', out.includes('可复习'));

    // 冲突: 两侧并列 + 不预判
    check('review page renders the conflict', out.includes('conflict-1'));
    check(
      'review page shows both conflict sides',
      out.includes('Evidence A') && out.includes('Evidence B')
    );
    check('review page says it does not pick a side', out.includes('不预判'));
    check('review page never says auto-resolved', !out.includes('自动解决'));

    // 动态 key 的两类原因都要落地
    check('review page explains the block reason', out.includes('存在未解决冲突'));
    check('review page explains the attention reason', out.includes('还没有人确认过'));
    check('review page states its ordering basis', out.includes('排序依据'));
    check(
      'review page never renders a stack trace',
      !/Traceback|at Object\.|File "/.test(out)
    );
  }

  // 空复习集合必须是正常状态, 不是空白页也不是 500。
  {
    const sandbox = await loadApp(REVIEW_ROUTES_EMPTY);
    await sandbox.pageReview();
    const out = html(sandbox);
    check('empty review set says so', out.includes('暂时没有可复习的内容'));
    check(
      'empty review set still disclaims prediction',
      out.includes('这不是考试预测')
    );
    check(
      'empty review set has no stack trace',
      !/Traceback|at Object\./.test(out)
    );
  }

  // ---- Task 68: 多课程总览 ----------------------------------------------
  {
    const sandbox = await loadApp(ROUTES);
    await sandbox.pageMyCourses();
    const out = html(sandbox);

    check('my courses renders both courses',
      out.includes('Algebra Lineal') && out.includes('Calculo'));
    // 逐课程计数: 两门课的知识点数不同, 必须各自显示各自的 12 / 9
    check('my courses shows per-course knowledge counts',
      out.includes('12') && out.includes('9'));
    check('my courses links to each course',
      out.includes('#/courses/' + COURSE_ID) && out.includes('#/courses/course-other'));
    check('my courses marks the current course', out.includes('当前课程'));
    // 两条真相轴分别显示, 不合成一列
    check('my courses lists the validation axis', out.includes('证据校验轴'));
    check('my courses lists the review axis', out.includes('人工审核轴'));
    check('my courses explains the axes are independent', out.includes('不相加'));
    check('my courses renders the review axis labels', out.includes('人工已确认'));
    check('my courses never leaks a raw i18n key',
      !/>(?:val|rev|mc)\.[a-z_]+/.test(out), />(?:val|rev|mc)\.[a-z_]+/.exec(out) || '');
    // 无排序意图
    for (const term of ['优先级', '推荐顺序', '更重要', '先学这门', 'most important']) {
      check('my courses never says "' + term + '"', !out.includes(term), term);
    }
    check('my courses never renders a stack trace',
      !/Traceback|at Object\.|File "/.test(out));
  }

  // 空课程表必须是正常状态, 不是空白页也不是 500。
  {
    const routes = Object.assign({}, ROUTES);
    routes['/api/my-courses'] = {
      data: myCoursesPayload({
        course_count: 0,
        selection: { preferred: null, course_id: null, reason: 'no_courses', available: [] },
        courses: [],
        totals: {
          counts: {
            sessions: 0, materials: 0, knowledge_points: 0, pending_review: 0,
            conflicts: 0, students: 0, exercises: 0, answers: 0,
          },
          validation: { supported: 0, unverified: 0, conflicted: 0 },
          review: { confirmed: 0, pending: 0, rejected: 0, kept_unverified: 0 },
          evidence_total: 0,
          gaps: 0,
        },
        empty: true,
        empty_note: 'No courses yet.',
      }),
    };
    const sandbox = await loadApp(routes);
    await sandbox.pageMyCourses();
    const out = html(sandbox);
    check('empty my courses says so', out.includes('还没有任何课程'));
    check('empty my courses is not an error page', !out.includes('INTERNAL_ERROR'));
    check('empty my courses has no stack trace', !/Traceback|at Object\./.test(out));
  }

  // ---- D1 / D4: 处理警告与"成功但零证据"必须被看见 ------------------------
  //
  // 契约: "文档可解析但没有可提取文本"是**合法成功**
  // (material_workflow._document_outcome: "属于合法成功, 但必须让用户看见"),
  // 而旧的材料页只画 processing_status —— 用户看到"成功", 拿到 0 条证据,
  // 却看不到任何解释。这里钉四件事:
  //
  //   1. 状态 pill 不变 (成功就是 pill-ok, 警告不改变颜色语义);
  //   2. warning 的**原码逐字**出现 (它是后端契约的一部分, 也是断言对象);
  //   3. 原码旁有人话; 零证据时再补一句"这不是失败"和按码给出的下一步;
  //   4. 未知码只显示码本身 —— 不猜含义 (证据优先)。
  //
  // 夹具里的 warning 码取值域与后端一致 (见 app.js 的 warningText) ——
  // 它不在测试里穷举后端, 只钉"前端对已知码说什么 / 对未知码不说什么"。
  {
    const warnMaterial = (overrides) => Object.assign(
      {
        material_id: 'mat-empty',
        filename: 'ThreeHorizonsTemplate.pdf',
        extension: '.pdf',
        size: 44958,
        material_type: 'text',
        source_type: 'document',
        session_id: null,
        processing_status: 'COMPLETED',
        error: null,
        warning: 'NO_TEXT_EXTRACTED',
        evidence_ids: [],
        evidence_count: 0,
      },
      overrides || {}
    );
    const renderMaterials = async (materials) => {
      const routes = Object.assign({}, ROUTES, {
        '/api/materials': { data: { materials: materials } },
        '/api/sessions': { data: { sessions: [] } },
        '/api/processing': { data: { by_status: { COMPLETED: 1 } } },
      });
      const sandbox = await loadApp(routes);
      await sandbox.pageMaterials();
      return html(sandbox);
    };

    const out = await renderMaterials([warnMaterial()]);
    const statusPill = /<span class="pill ([a-z-]+)">COMPLETED<\/span>/.exec(out);
    check('a warning-only success still renders the status pill', !!statusPill);
    check('a warning never repaints the status pill',
      !!statusPill && statusPill[1] !== 'pill-warn' && statusPill[1] !== 'pill-bad',
      statusPill ? statusPill[1] : 'no status pill found');
    check('the warning code is rendered verbatim inside a warning pill',
      out.includes('<span class="tiny pill pill-warn">NO_TEXT_EXTRACTED</span>'),
      out.slice(out.indexOf('pill-warn') - 120, out.indexOf('pill-warn') + 120));
    check('the warning code carries human copy',
      out.includes('扫描件请转成图片重传走 OCR'));
    check('a zero-evidence success says it did not fail',
      out.includes('处理成功，但这份材料没有提取到任何内容'));
    check('a NO_TEXT_EXTRACTED zero-evidence success gives the next step',
      out.includes('把页面导成图片后重新上传，走 OCR 提取'));
    check('no raw i18n key leaks into the warning row',
      !out.includes('warn.'), /warn\.[A-Z._a-z]+/.exec(out) || '');
    check('the warning fixture never leaks exercise secrets',
      !out.includes(SECRET_ANSWER) && !out.includes(SECRET_EXPLANATION));

    // 未知码: 原码照显示, 但**不编**解释 (没有文案就只说通用句)。
    const unknown = await renderMaterials([warnMaterial({ warning: 'SOMETHING_NEW' })]);
    check('an unknown warning code is still rendered verbatim',
      unknown.includes('SOMETHING_NEW'));
    check('an unknown warning code gets no invented explanation',
      !unknown.includes('扫描件') && !unknown.includes('转写') && !unknown.includes('照片'));
    check('an unknown warning code still gets the generic hint',
      unknown.includes('处理成功，但这份材料没有提取到任何内容'));

    // 无 warning 的零证据成功: 只说通用句, 不归因。
    const noCode = await renderMaterials([warnMaterial({ warning: null })]);
    check('a zero-evidence success without a warning still says it did not fail',
      noCode.includes('处理成功，但这份材料没有提取到任何内容'));
    check('a zero-evidence success without a warning invents no cause',
      !noCode.includes('扫描件') && !noCode.includes('转写') && !noCode.includes('照片'));

    // 有证据 + warning: 警告照显示, 但**不能**说"没提取到内容"。
    const withEvidence = await renderMaterials([
      warnMaterial({ evidence_ids: ['ev-1'], evidence_count: 1 }),
    ]);
    check('an evidence-bearing material still shows its warning code',
      withEvidence.includes('NO_TEXT_EXTRACTED'));
    check('an evidence-bearing material gets no zero-evidence hint',
      !withEvidence.includes('处理成功，但这份材料没有提取到任何内容'));

    // 失败材料: 有 error pill, 且绝不能被说成"处理成功"。
    const failed = await renderMaterials([
      warnMaterial({ processing_status: 'FAILED', error: 'DOCUMENT_PARSE_FAILED', warning: null }),
    ]);
    check('a failed material shows its error code', failed.includes('DOCUMENT_PARSE_FAILED'));
    check('a failed material is never told it succeeded',
      !failed.includes('处理成功，但这份材料没有提取到任何内容'));
  }

  // ---- Task 68.1: "当前课程"的三处显示必须同源 ---------------------------
  //
  // 回归: 用户从 A 课点进 B 课。route() 的顺序是 loadSidebar() -> pageXxx(),
  // 所以侧边栏是先画的 (那时 state.courseId 还是 A), 而课程页要到之后才从
  // 路由里拿到 B 并 setCourse(B)。如果 setCourse() 不重绘侧边栏与顶栏切换器,
  // 用户看到的就是"主视图是 B, 左侧高亮的却是 A" —— 选中的课和高亮的课不一致。
  {
    const OTHER = 'course-other';
    const routes = Object.assign({}, ROUTES, {
      '/api/courses': {
        data: {
          courses: [
            { course_id: COURSE_ID, name: 'Algebra Lineal' },
            { course_id: OTHER, name: 'Calculo' },
          ],
        },
      },
      '/api/course-selection': {
        data: {
          preferred: COURSE_ID, course_id: COURSE_ID, reason: 'preferred',
          available: [COURSE_ID, OTHER],
        },
      },
      ['/api/courses/' + OTHER + '/workspace']: {
        data: {
          course: { course_id: OTHER, name: 'Calculo', language: 'es' },
          counts: {}, coverage: {}, sessions: [], knowledge: {},
          recent_materials: [], pending_review: [],
        },
      },
    });
    const sandbox = await loadApp(routes);
    const loadSidebar = grab(sandbox, 'loadSidebar');
    const pageCourse = grab(sandbox, 'pageCourse');

    await loadSidebar();               // 侧边栏先画: 高亮 COURSE_ID
    const before = sandbox.document.getElementById('course-list').innerHTML;
    check('sidebar highlights the first course before navigation',
      /<a class="active" href="#\/courses\/course-test"/.test(before), before);

    await pageCourse(OTHER);           // 页面函数后到: setCourse(OTHER)

    const sidebar = sandbox.document.getElementById('course-list').innerHTML;
    const switcher = sandbox.document.getElementById('course-switch').innerHTML;
    const viewHtml = html(sandbox);

    check('course page renders the course from the route',
      viewHtml.includes('Calculo'));
    check('sidebar highlight follows the course page (not the stale one)',
      /<a class="active" href="#\/courses\/course-other"/.test(sidebar), sidebar);
    check('top-bar switcher follows the course page (not the stale one)',
      /<option value="course-other" selected>/.test(switcher), switcher);
    check('exactly one course stays highlighted',
      (sidebar.match(/class="active"/g) || []).length === 1, sidebar);
    check('no raw i18n key leaks after the course switch',
      !/>(?:course|common|mc)\.[a-z_]+/.test(sidebar + switcher));
  }

  // ---- 路由里的 course_id 不存在时, 不得污染"当前课程" -------------------
  //
  // 手输错、失效的书签、课程被删之后的深链都会给出一个不存在的 id。
  // 直接 setCourse() 把它写进 state / localStorage 的后果是: 侧边栏没有任何
  // 高亮、顶栏切换器退回显示第一项 (显示的又不是"当前课程"), 且此后每个页面
  // 都拿着脏 id 去请求 —— 用户被卡在 404 上, 除非重新点一门课。
  {
    const OTHER = 'course-other';
    const routes = Object.assign({}, ROUTES, {
      '/api/courses': {
        data: {
          courses: [
            { course_id: COURSE_ID, name: 'Algebra Lineal' },
            { course_id: OTHER, name: 'Calculo' },
          ],
        },
      },
      '/api/course-selection': {
        data: {
          preferred: COURSE_ID, course_id: COURSE_ID, reason: 'preferred',
          available: [COURSE_ID, OTHER],
        },
      },
      '/api/course-knowledge': { data: { course_id: COURSE_ID, topic_count: 0, relation_count: 0 } },
    });
    const sandbox = await loadApp(routes);
    const loadSidebar = grab(sandbox, 'loadSidebar');
    const pageCourse = grab(sandbox, 'pageCourse');
    const requireCourse = grab(sandbox, 'requireCourse');
    const pageKnowledge = grab(sandbox, 'pageKnowledge');

    await loadSidebar();
    check('unknown-course setup: sidebar starts on the real course',
      /<a class="active" href="#\/courses\/course-test"/.test(
        sandbox.document.getElementById('course-list').innerHTML));

    let threw = null;
    try {
      await pageCourse('course-missing');
    } catch (err) {
      threw = err;
    }
    check('unknown route course still surfaces the backend error', !!threw);

    const sidebar = sandbox.document.getElementById('course-list').innerHTML;
    const switcher = sandbox.document.getElementById('course-switch').innerHTML;
    check('unknown route course keeps the sidebar highlight on the real course',
      /<a class="active" href="#\/courses\/course-test"/.test(sidebar), sidebar);
    check('unknown route course keeps the switcher on the real course',
      /<option value="course-test" selected>/.test(switcher), switcher);
    check('unknown route course is not persisted as the current course',
      sandbox.window.localStorage.getItem('ca.course') === COURSE_ID,
      String(sandbox.window.localStorage.getItem('ca.course')));

    // 关键: 用户不会被脏 id 卡住 —— 后续页面仍然拿有效课程正常工作。
    check('requireCourse never returns an unknown course',
      (await requireCourse()) === COURSE_ID);
    await pageKnowledge();
    const out = html(sandbox);
    check('a later page still works after an unknown course id',
      !out.includes('请求失败') && !out.includes('NOT_FOUND'), out.slice(0, 200));
  }

  // ---- 顶栏高亮必须跟随"用户所在的页面" ---------------------------------
  //
  // 回归: `pageLearnKnowledge()` 曾经是 19 个页面函数里**唯一**不调用
  // `markActiveNav()` 的。而 index.html 里没有任何硬编码 `active` —— 顶栏高亮
  // 完全由页面函数设置, 谁不设谁就继承上一页。后果:
  //   * 在 `#/learn/<kp>` 上刷新 -> 整条导航都没有高亮;
  //   * 从别的页面点进来        -> 高亮停在上一页。
  // 两种都是"高亮的位置不是用户所在的位置"。
  //
  // 这组用例能存在, 靠的是桩里补上了 `.topnav a` —— 之前 `querySelectorAll`
  // 恒返回空数组, `markActiveNav()` 是个空转调用, 整个顶栏高亮都没被测过。
  {
    const routes = Object.assign({}, ROUTES, {
      '/api/course-knowledge': {
        data: { course_id: COURSE_ID, topic_count: 1, relation_count: 0 },
      },
      [`/api/students/${STUDENT_ID}/learning/knowledge/kp-1`]: { data: learnKnowledge({}) },
      [`/api/students/${STUDENT_ID}/learning/start`]: { data: learnStart({}) },
    });

    // 每个页面函数都必须把高亮设成**恰好一项**, 且指向自己所在的页面。
    // (2026-09-22: 只抽仍在顶栏可用的分区 —— 考前复习/复习包/练习/错题本
    // 已改为无 href 的禁用 span, 真实 DOM 里本就进不了 active。)
    for (const [page, expected] of [
      ['pageMyCourses', '#/courses'],
      ['pageKnowledge', '#/knowledge'],
      ['pageLearn', '#/today'],
    ]) {
      const sandbox = await loadApp(routes);
      await grab(sandbox, 'loadSidebar')();
      await grab(sandbox, page)();
      const active = activeNav(sandbox);
      check(page + ' leaves exactly one nav entry highlighted',
        active.length === 1, active.join(','));
      check(page + ' highlights its own nav entry',
        active.join(',') === expected, active.join(','));
    }

    // 回归本体 (a): 刷新后深链到知识点学习页 —— 高亮必须跟着流程入口 `#/learn`。
    {
      const sandbox = await loadApp(routes);
      await grab(sandbox, 'loadSidebar')();
      await grab(sandbox, 'pageLearnKnowledge')(COURSE_ID, 'kp-1');
      check('a fresh deep link to the learn page highlights the flow entry',
        activeNav(sandbox).join(',') === '#/today', activeNav(sandbox).join(','));
    }

    // 回归本体 (b): 从别的页面进学习页 —— 高亮必须移动, 不能继承上一页。
    {
      const sandbox = await loadApp(routes);
      await grab(sandbox, 'loadSidebar')();
      await grab(sandbox, 'pageKnowledge')();
      check('setup: the knowledge page highlights itself',
        activeNav(sandbox).join(',') === '#/knowledge', activeNav(sandbox).join(','));
      await grab(sandbox, 'pageLearnKnowledge')(COURSE_ID, 'kp-1');
      check('navigating to the learn page moves the highlight off the previous page',
        activeNav(sandbox).join(',') === '#/today', activeNav(sandbox).join(','));
    }
  }

  // ---- "当前学生"的内存与持久化必须同步 ---------------------------------
  //
  // 回归: 6 处页面函数各自写一遍 `state.studentId` + `localStorage`, 其中
  // `pageExercise()` **漏了持久化那一步**。从错题本点「Practice Again」进
  // `#/courses/<c>/exercises/<e>/<sid>` 之后, 内存里是 sid, localStorage 里还是
  // 上一个学生 —— 刷新一下学生就悄悄换人了。
  {
    const OTHER_STUDENT = 'stu-bob';
    const routes = Object.assign({}, ROUTES, {
      [`/api/students/${OTHER_STUDENT}/exercises/${EXERCISE_ID}`]: { data: baseView() },
      [`/api/students/${OTHER_STUDENT}/dashboard`]: { data: studentDashboard({ student_id: OTHER_STUDENT }) },
    });

    const open = async (page, ...args) => {
      // `ca.student` 必须在 app.js 执行**之前**写好 —— `const state = {...}`
      // 加载时就读它。先建 sandbox 再 setItem 的话 `state.studentId` 还是 null,
      // "不带 sid 时沿用当前学生" 那条用例就成了空跑。
      const sandbox = await loadApp(routes, { 'ca.student': STUDENT_ID });
      await grab(sandbox, 'loadSidebar')();
      await grab(sandbox, page)(...args);
      return sandbox;
    };
    const stored = (sandbox) => sandbox.window.localStorage.getItem('ca.student');
    const inMemory = (sandbox) => grab(sandbox, 'state.studentId');
    // 注意: 这里必须**真的比较两个值**。"内存等于期望值" 是另一回事 ——
    // 只写 state 不写 localStorage 的旧写法同样能让它通过。
    const inSync = (sandbox) =>
      inMemory(sandbox) === stored(sandbox) &&
      inMemory(sandbox) !== undefined &&
      inMemory(sandbox) !== null;

    // 练习页: 路由显式给了另一个学生 —— 必须持久化, 否则刷新就切回去。
    {
      const sandbox = await open('pageExercise', COURSE_ID, EXERCISE_ID, OTHER_STUDENT);
      check('the exercise page persists an explicitly routed student',
        stored(sandbox) === OTHER_STUDENT, String(stored(sandbox)));
      check('the exercise page keeps memory and storage in sync',
        inSync(sandbox), inMemory(sandbox) + ' vs ' + stored(sandbox));
    }
    // 学生页: 同一条路径, 本来就持久化 —— 对照组。
    {
      const sandbox = await open('pageStudent', COURSE_ID, OTHER_STUDENT);
      check('the student page persists an explicitly routed student',
        stored(sandbox) === OTHER_STUDENT, String(stored(sandbox)));
      check('the student page keeps memory and storage in sync',
        inSync(sandbox), inMemory(sandbox) + ' vs ' + stored(sandbox));
    }
    // 练习页不带 sid 时, 沿用当前学生, 不能凭空改。
    {
      const sandbox = await open('pageExercise', COURSE_ID, EXERCISE_ID);
      check('the exercise page keeps the current student when the route has none',
        stored(sandbox) === STUDENT_ID, String(stored(sandbox)));
      check('the current student is the one loaded from storage',
        inMemory(sandbox) === STUDENT_ID, String(inMemory(sandbox)));
      check('no route without a student desyncs memory and storage',
        inSync(sandbox), inMemory(sandbox) + ' vs ' + stored(sandbox));
    }
  }

  // ---- 存储的界面语言必须先校验再使用 -----------------------------------
  //
  // `ca.lang` 是**存储值**, 不是既成事实 (可能是旧版本写的 / 手工改的 /
  // 来自一个曾支持更多语言的版本)。`setLang()` 一直有校验, 初始读取此前没有。
  // 不校验的后果不是"界面显示错" —— 而是那个非法值被**原样发给后端**,
  // 而 `normalize_language()` 会抛 InvalidInputError: 用户看到一个他从没选过的
  // 语言的错误页。
  {
    const BAD = 'fr';
    const bad = await loadApp(ROUTES, { 'ca.lang': BAD });
    check('an invalid stored language falls back to zh',
      grab(bad, 'state.lang') === 'zh', String(grab(bad, 'state.lang')));
    await grab(bad, 'pageMyCourses')();
    check('an invalid stored language is never sent to the backend',
      !bad.__fetched.some((url) => url.indexOf('lang=' + BAD) >= 0),
      bad.__fetched.filter((url) => url.indexOf('lang=') >= 0).join(' '));
    check('the request carries a supported language instead',
      bad.__fetched.some((url) => /[?&]lang=zh\b/.test(url)),
      bad.__fetched.filter((url) => url.indexOf('lang=') >= 0).join(' '));

    const good = await loadApp(ROUTES, { 'ca.lang': 'ca' });
    check('a supported stored language is kept',
      grab(good, 'state.lang') === 'ca', String(grab(good, 'state.lang')));
    await grab(good, 'pageMyCourses')();
    check('a supported stored language is sent through',
      good.__fetched.some((url) => /[?&]lang=ca\b/.test(url)),
      good.__fetched.filter((url) => url.indexOf('lang=') >= 0).join(' '));

    // 空 / 缺失 也要落到 zh (而不是空串 —— 空串发给后端同样是非法值)。
    const empty = await loadApp(ROUTES, { 'ca.lang': '' });
    check('an empty stored language falls back to zh',
      grab(empty, 'state.lang') === 'zh', JSON.stringify(grab(empty, 'state.lang')));
  }

  // ---- P3-2: 任务坞的跨刷新恢复 ------------------------------------------
  //
  // 服务端的处理是**同步**的 —— 音频转写可能持续数分钟, 而坞原先只在内存里。
  // 用户等待期间按 F5: 坞被清空、服务端仍在跑, 他手上没有任何信号。
  //
  // 这一组用 sessionStorage 桩模拟"上一次会话留下的运行中任务", 调
  // restoreTaskDock(), 然后断言坞里的**结局来自服务端投影**: 服务端说什么
  // 就显示什么, 服务端不知道就说不知道 —— 绝不自己宣布一个结局。
  {
    const MAT = 'mat-restore';
    const SES = 'ses-restore';
    const matSeed = JSON.stringify([
      { label: '处理 · ' + MAT, courseId: COURSE_ID, targetId: MAT, targetKind: 'material' },
    ]);
    const sesSeed = JSON.stringify([
      { label: '处理 · ' + SES, courseId: COURSE_ID, targetId: SES, targetKind: 'session' },
    ]);
    const dockHtml = (sandbox) => {
      const dock = sandbox.__elements.get('task-dock');
      return dock ? dock.innerHTML : '';
    };
    const store = (sandbox) => String(sandbox.__session.get('ca.tasks') || '');
    const withRoutes = (extra) => Object.assign({}, ROUTES, extra);

    // (1) 服务端仍在跑 → 留在坞里, 且**不**宣布任何结局。
    {
      const sandbox = await loadApp(
        withRoutes({
          ['/api/processing/' + MAT]: {
            data: { material_id: MAT, status: 'RUNNING', stage: 'EVIDENCE', evidence_ids: [] },
          },
        }),
        undefined,
        { 'ca.tasks': matSeed }
      );
      await grab(sandbox, 'restoreTaskDock')();
      const out = dockHtml(sandbox);
      check('a restored running task is shown in the dock',
        out.indexOf('task-running') >= 0, out);
      check('the restored task keeps the label it was started with',
        out.indexOf('处理 · ' + MAT) >= 0, out);
      check('the restored task is resolved through the server projection',
        sandbox.__fetched.some((url) => url.split('?')[0] === '/api/processing/' + MAT),
        sandbox.__fetched.join(', '));
      check('the restore request carries the course scope',
        sandbox.__fetched.some((url) => url.indexOf('course_id=' + COURSE_ID) >= 0),
        sandbox.__fetched.join(', '));
      check('a running task never claims an outcome',
        out.indexOf('task-done') === -1 && out.indexOf('task-failed') === -1, out);
      check('a running task reports the stage the server gave',
        out.indexOf('服务端仍在处理') >= 0 && out.indexOf('EVIDENCE') >= 0, out);
      check('a still-running task is kept for the next reload',
        store(sandbox).indexOf(MAT) >= 0, store(sandbox));
    }

    // (2) 服务端说成功了 → 坞里是完成, 证据条数**取自服务端**。
    {
      const sandbox = await loadApp(
        withRoutes({
          ['/api/processing/' + MAT]: {
            data: {
              material_id: MAT, status: 'SUCCEEDED', stage: 'KNOWLEDGE',
              evidence_ids: ['ev-1', 'ev-2', 'ev-3'],
            },
          },
        }),
        undefined,
        { 'ca.tasks': matSeed }
      );
      await grab(sandbox, 'restoreTaskDock')();
      const out = dockHtml(sandbox);
      check('a restored finished task is marked done', out.indexOf('task-done') >= 0, out);
      check('the evidence count comes from the server, not from the client',
        out.indexOf('3 条证据') >= 0, out);
      check('a finished task is dropped from the storage',
        store(sandbox).indexOf(MAT) === -1, store(sandbox));
    }

    // (3) 服务端说失败了 → 坞里是失败, 且带上服务端给的原因。
    {
      const sandbox = await loadApp(
        withRoutes({
          ['/api/processing/' + MAT]: {
            data: {
              material_id: MAT, status: 'FAILED', stage: 'INGESTING',
              error: 'DECODE_FAILED', evidence_ids: [],
            },
          },
        }),
        undefined,
        { 'ca.tasks': matSeed }
      );
      await grab(sandbox, 'restoreTaskDock')();
      const out = dockHtml(sandbox);
      check('a restored failed task is marked failed', out.indexOf('task-failed') >= 0, out);
      check('the failure reason comes from the server',
        out.indexOf('DECODE_FAILED') >= 0, out);
      check('a failed task is dropped from the storage',
        store(sandbox).indexOf(MAT) === -1, store(sandbox));
    }

    // (4) 服务端没有这个作业 (重启过 / 材料被删) → 404 是**确定的**结论。
    //     必须报"没有记录", 而不是留一个假进度条, 也不是宣布成功。
    {
      const sandbox = await loadApp(ROUTES, undefined, { 'ca.tasks': matSeed });
      await grab(sandbox, 'restoreTaskDock')();
      const out = dockHtml(sandbox);
      check('an unknown job is reported as no record, not as a success',
        out.indexOf('task-failed') >= 0, out);
      check('the no-record message names the reason',
        out.indexOf('服务端没有这个任务的记录。') >= 0, out);
      check('an unknown job is dropped from the storage',
        store(sandbox).indexOf(MAT) === -1, store(sandbox));
    }

    // (5) 没有留下任何任务 → 一个请求都不该发。
    {
      const sandbox = await loadApp(ROUTES);
      await grab(sandbox, 'restoreTaskDock')();
      check('no stored task means no restore request',
        !sandbox.__fetched.some((url) => url.indexOf('/processing') >= 0),
        sandbox.__fetched.join(', '));
      check('an empty task dock stays empty', dockHtml(sandbox) === '', dockHtml(sandbox));
    }

    // (6) 整堂处理: 按 session 作用域轮询, 剩余为 0 时收尾。
    {
      const sandbox = await loadApp(
        withRoutes({
          '/api/processing': {
            data: {
              total: 2, evidence_total: 4,
              by_status: { QUEUED: 0, RUNNING: 0, SUCCEEDED: 1, FAILED: 1, CANCELLED: 0 },
              jobs: [],
            },
          },
        }),
        undefined,
        { 'ca.tasks': sesSeed }
      );
      await grab(sandbox, 'restoreTaskDock')();
      const out = dockHtml(sandbox);
      check('a session-level task is polled with the session scope',
        sandbox.__fetched.some((url) => url.indexOf('session_id=' + SES) >= 0),
        sandbox.__fetched.join(', '));
      check('a session with nothing left to run is finished',
        out.indexOf('task-failed') >= 0, out);
      check('the session outcome counts come from the server',
        out.indexOf('2 个材料, 1 个失败') >= 0, out);
    }

    // (7) 整堂处理: 还有作业在排队且**确实被 enqueued 过** → 继续等。
    {
      const sandbox = await loadApp(
        withRoutes({
          '/api/processing': {
            data: {
              total: 3, evidence_total: 4,
              by_status: { QUEUED: 1, RUNNING: 1, SUCCEEDED: 1, FAILED: 0, CANCELLED: 0 },
              jobs: [{ enqueued: true, status: 'QUEUED' }],
            },
          },
        }),
        undefined,
        { 'ca.tasks': sesSeed }
      );
      await grab(sandbox, 'restoreTaskDock')();
      const out = dockHtml(sandbox);
      check('a session with enqueued work keeps waiting',
        out.indexOf('task-running') >= 0, out);
      check('the pending count comes from the server',
        out.indexOf('2 个待处理') >= 0, out);
      check('a waiting session is kept for the next reload',
        store(sandbox).indexOf(SES) >= 0, store(sandbox));
    }

    // (8) 整堂处理: 有 QUEUED 但**没有任何作业被 enqueued 过** → 服务端其实
    //     没在跑它 (典型成因是服务端重启过, 而 /api/processing 会为每个已注册
    //     材料补建一个 QUEUED 作业, 于是"看起来还在排队")。给一拍宽限 ——
    //     用户可能刚好在 POST 到达前按了 F5 —— 第二拍仍然没人动过就收尾。
    {
      const sandbox = await loadApp(
        withRoutes({
          '/api/processing': {
            data: {
              total: 1, evidence_total: 0,
              by_status: { QUEUED: 1, RUNNING: 0, SUCCEEDED: 0, FAILED: 0, CANCELLED: 0 },
              jobs: [{ enqueued: false, status: 'QUEUED' }],
            },
          },
        }),
        undefined,
        { 'ca.tasks': sesSeed }
      );
      await grab(sandbox, 'restoreTaskDock')();
      check('a session nothing ever enqueued gets a second chance',
        dockHtml(sandbox).indexOf('task-running') >= 0, dockHtml(sandbox));
      await flushTimers(sandbox, 1);
      const out = dockHtml(sandbox);
      check('a session the server never started is reported as no record',
        out.indexOf('服务端没有这个任务的记录。') >= 0, out);
      check('the no-record session is dropped from the storage',
        store(sandbox).indexOf(SES) === -1, store(sandbox));
    }

    // (9) 上限: 永远不收敛的作业不能无限转圈, 也不能被"凑巧"判成成功。
    {
      const sandbox = await loadApp(
        withRoutes({
          ['/api/processing/' + MAT]: {
            data: { material_id: MAT, status: 'RUNNING', stage: 'EVIDENCE', evidence_ids: [] },
          },
        }),
        undefined,
        { 'ca.tasks': matSeed }
      );
      await grab(sandbox, 'restoreTaskDock')();
      await flushTimers(sandbox, 70);
      const out = dockHtml(sandbox);
      check('a task that never settles stops polling at the cap',
        sandbox.__timers.length === 0, String(sandbox.__timers.length));
      check('a task that never settles is reported as unknown, not as success',
        out.indexOf('超过 5 分钟仍未结束，无法确认结局。') >= 0, out);
      check('an unsettled task is dropped from the storage',
        store(sandbox).indexOf(MAT) === -1, store(sandbox));
    }
  }

  // ---- 课程切换保持当前页面 (2026-09-22 解耦) ---------------------------
  //
  // 根因: 侧边栏原来是直链 `#/courses/<id>`, 顶栏切换器原来是
  // `setCourse() + location.hash = '#/courses/<id>'` —— 于是 11 个顶层功能页
  // 换课都会被踢到课程详情。这里用两门课证明统一契约:
  //   Page = unchanged (hash 一字不动) / Course = changed / Content = changed。
  //
  // 桩按 pathOnly 路由而忽略 query, 所以"只带 query、不带路径"的端点靠改表
  // 区分两门课 —— 改表即换课, 与真实后端"按 preferred 回显"的语义一致。
  {
    const GP = 'course-gp';
    const GEO = 'course-geo';
    const GP_NAME = 'Gestio de Projectes';
    const GEO_NAME = 'Bases per a la Geoinformacio';
    const twoCourses = [
      { course_id: GP, name: GP_NAME, code: 'GP', language: 'es' },
      { course_id: GEO, name: GEO_NAME, code: 'GEO', language: 'ca' },
    ];

    function useCourse(table, id) {
      const tag = id === GP ? 'GP' : 'GEO';
      const slow = tag.toLowerCase();
      table['/api/course-selection'] = {
        data: { preferred: id, course_id: id, reason: 'preferred', available: [GP, GEO] },
      };
      table['/api/dashboard'] = {
        data: {
          course_id: id, courses: twoCourses, version: 't',
          knowledge: { count: 1, summary: {} }, gaps: { gaps: [] },
          processing: { by_status: {}, jobs: [] },
          materials: [{
            material_id: 'm-' + tag, filename: 'FILE-' + tag + '-dash.pdf',
            processing_status: 'REGISTERED', material_type: 'text', size: 10,
          }],
          sessions: [], review_pending: [], students: [], exercises: [],
        },
      };
      // student-today: course_id=null = 全部课程聚合响应 (2026-09-22 起今日
      // 默认全局口径); 备注仍按 tag 区分, 供"哪门课的数据在屏上"断言用。
      table['/api/student-today'] = {
        data: {
          course_id: null, date: '2026-03-01', has_activity: false,
          note: 'NOTE-' + tag + '-today', counts: {},
          classes_today: [], study: [], learning_paths: [],
          pending_exercises: [], recent_evaluations: [], attention: [],
          review: { items: [], total: 0 },
        },
      };
      table['/api/students'] = {
        data: { students: [{ student_id: 'stu-' + slow, display_name: 'NAME-' + tag }] },
      };
      table['/api/reviews'] = {
        data: { reviews: [{ knowledge_point_id: 'KP-' + tag, reason: 'R-' + tag, review_status: 'PENDING' }] },
      };
      table['/api/my-courses'] = {
        data: {
          view: 'multi-course-workspace-v1', language: 'zh', course_count: 2,
          selection: { preferred: id, course_id: id, reason: 'preferred', available: [GP, GEO] },
          courses: twoCourses.map((c) => ({
            course_id: c.course_id, name: c.name, code: c.code, language: c.language,
            counts: {}, validation: {}, review: {},
            evidence_total: 0, gaps: 0, last_session: null,
          })),
          totals: { counts: {}, validation: {}, review: {}, evidence_total: 0, gaps: 0 },
          empty: false,
        },
      };
      table['/api/materials'] = {
        data: {
          materials: [{
            material_id: 'm-' + tag, filename: 'FILE-' + tag + '-mat.pdf',
            material_type: 'text', processing_status: 'REGISTERED', size: 5,
          }],
        },
      };
      table['/api/sessions'] = { data: { sessions: [] } };
      table['/api/processing'] = { data: { by_status: {}, jobs: [] } };
      table['/api/knowledge'] = {
        data: {
          course_id: id,
          knowledge_points: [{
            knowledge_id: 'kp-' + slow, title: 'TITLE-' + tag,
            validation_status: 'supported', review_status: 'pending',
            evidence_refs: [], original_terms: [],
          }],
        },
      };
      table['/api/course-knowledge'] = {
        data: { course_id: id, topic_count: 0, relation_count: 0 },
      };
    }

    function switchTable() {
      // 路径里自带课程的端点: 两门课的响应可以静态共存, 不用改表。
      const table = {
        '/api/health': {
          data: {
            status: 'ok', application: 'classroom', version: 't',
            processing: { asr: 'real', ocr: 'real', evidence_count: 0 },
            storage: { courses: 2 },
          },
        },
        '/api/courses': { data: { courses: twoCourses } },
      };
      useCourse(table, GP);
      for (const id of [GP, GEO]) {
        const tag = id === GP ? 'GP' : 'GEO';
        const slow = tag.toLowerCase();
        table['/api/students/stu-' + slow + '/review-set'] = {
          data: {
            counts: { total: 0 }, ordering_basis: 'ORDER-' + tag,
            buckets: {}, items: [], conflicts: [],
            coverage: { unavailable: true }, empty: true,
          },
        };
        table['/api/students/stu-' + slow + '/exercises'] = {
          data: {
            student_id: 'stu-' + slow, answered: 0, total: 1,
            exercises: [{
              exercise_id: 'ex-' + slow, prompt: 'PROMPT-' + tag + '-prompt',
              exercise_type: 'fill_blank', knowledge_points: [],
              prerequisites: [], submitted: false,
            }],
          },
        };
        table['/api/students/stu-' + slow + '/mistakes'] = {
          data: {
            counts: {}, has_mistakes: false, groups: [],
            mistakes: [], weak_knowledge: [], knowledge: [],
          },
        };
        table['/api/courses/' + id + '/review-pack'] = {
          data: {
            llm_mode: 'mock',
            digests: [{
              material_id: 'm-' + tag, filename: 'FILE-' + tag + '-pack.pdf',
              summary: { text: 'TEXT-' + tag, needs_verification: true, model: 'm', prompt_version: 'v' },
              conflicts: [], evidence: [],
            }],
            conflicts: [],
          },
        };
        table['/api/courses/' + id + '/workspace'] = {
          data: {
            course: { course_id: id, name: id === GP ? GP_NAME : GEO_NAME, code: tag, language: 'es' },
            counts: {}, coverage: {}, sessions: [], knowledge: {},
            recent_materials: [], pending_review: [], metadata: {},
            gaps: { gaps: [] }, teacher: 'T', semester: 'S',
          },
        };
      }
      return table;
    }

    // 在 hash 页渲染 GP, 切到 GEO, 断言: 路由不动 / 状态与持久化是 GEO /
    // 视图是 GEO 内容 / 查询真的带上了 GEO / 顶栏高亮仍是本页。
    async function switchStaysOn(table, hash, nav, markerBefore, markerAfter) {
      const sandbox = await loadApp(table, { 'ca.course': GP });
      const route = grab(sandbox, 'route');
      const switchCourse = grab(sandbox, 'switchCourse');
      sandbox.window.location.hash = hash;
      useCourse(table, GP);
      await route();
      const before = html(sandbox);
      check(hash + ' renders the GP content before switching',
        before.indexOf(markerBefore) >= 0, before.slice(0, 200));
      const fetchedBefore = sandbox.__fetched.length;
      useCourse(table, GEO);
      await switchCourse(GEO);
      check(hash + ' keeps the route after switching course',
        sandbox.window.location.hash === hash, sandbox.window.location.hash);
      check(hash + ' switches the course state',
        grab(sandbox, 'state').courseId === GEO,
        String(grab(sandbox, 'state').courseId));
      check(hash + ' persists the new course for reload',
        sandbox.window.localStorage.getItem('ca.course') === GEO,
        String(sandbox.window.localStorage.getItem('ca.course')));
      const fresh = sandbox.__fetched.slice(fetchedBefore).join(' | ');
      check(hash + ' re-queries with the new course',
        fresh.indexOf(GEO) >= 0, fresh.slice(0, 300));
      check(hash + ' keeps the top-nav highlight on this page',
        JSON.stringify(activeNav(sandbox)) === JSON.stringify([nav]),
        JSON.stringify(activeNav(sandbox)));
      const bar = sandbox.document.getElementById('course-list').innerHTML;
      check(hash + ' moves the sidebar highlight to the new course',
        (bar.match(/class="active"/g) || []).length === 1 &&
        new RegExp('<a class="active" href="#/courses/' + GEO + '"').test(bar), bar.slice(0, 200));
      const after = html(sandbox);
      check(hash + ' renders the GEO content after switching',
        after.indexOf(markerAfter) >= 0, after.slice(0, 200));
      check(hash + ' no longer renders the GP content after switching',
        after.indexOf(markerBefore) === -1, after.slice(0, 300));
      // 幂等: 已经是当前课程时不碰路由、不发请求。
      const fetchedAgain = sandbox.__fetched.length;
      await switchCourse(GEO);
      check(hash + ' ignores switching to the course already selected',
        sandbox.window.location.hash === hash && sandbox.__fetched.length === fetchedAgain,
        sandbox.window.location.hash + ' / +' + (sandbox.__fetched.length - fetchedAgain));
    }

    // 2026-09-22: 只覆盖仍在顶栏可用的分区。考前复习/复习包/练习/错题本的
    // 路由与页面函数原样保留 (深链仍可达), 但顶栏已改为禁用 span —— 高亮断言
    // 只对可用分区有意义, 故从本表移除四项。
    // 2026-09-22 (任务书 §5): '#/' 与 '#/today' 是**全局页**, 换课语义不同
    // (视图不跟随当前课程), 单独在下面的专用块里测, 不进这张课程页表。
    const PAGES = [
      ['#/reviews', '#/reviews', 'KP-GP', 'KP-GEO'],
      ['#/materials', '#/materials', 'FILE-GP-mat.pdf', 'FILE-GEO-mat.pdf'],
      ['#/knowledge', '#/knowledge', 'TITLE-GP', 'TITLE-GEO'],
      ['#/students', '#/students', 'NAME-GP', 'NAME-GEO'],
    ];
    for (const [hash, nav, markerBefore, markerAfter] of PAGES) {
      await switchStaysOn(switchTable(), hash, nav, markerBefore, markerAfter);
    }

    // 概览 / 今日 = **全局页** (2026-09-22 任务书 §5/§11/§13): 左侧换课只换
    // course context, 视图默认仍是"全部课程"聚合, **绝不**变成单课程内容。
    // 路由不动 / 状态与持久化是 GEO / 顶栏高亮仍是本页 —— 这些与课程页一致;
    // 差别在"视图内容": 课程页换成 GEO 内容, 全局页换课前后都是聚合视图,
    // 且选择器保持「全部课程」。
    for (const [hash, globalMarker, courseMarker] of [
      // 概览: 聚合行里有 GP 课程名; 单课程标记 = 旧 dashboard 夹具的材料名。
      ['#/', 'Gestio de Projectes', 'FILE-GP-dash.pdf'],
      // 今日: 全局视图显示夹具备注; 单课程标记 = 副标题里的课程名 + 回全部链接
      // (pageToday 只有 scope 筛选时才把课程名放进副标题)。
      ['#/today', 'NOTE-GP-today', 'Gestio de Projectes · <a'],
    ]) {
      const table = switchTable();
      const sandbox = await loadApp(table, { 'ca.course': GP });
      const route = grab(sandbox, 'route');
      const switchCourse = grab(sandbox, 'switchCourse');
      sandbox.window.location.hash = hash;
      useCourse(table, GP);
      await route();
      const before = html(sandbox);
      // 副标题必须是"全部课程"口径; 聚合视图来自不筛课的请求。
      check(hash + ' opens as the all-courses view',
        before.indexOf('全部课程') >= 0 && before.indexOf(globalMarker) >= 0,
        before.slice(0, 200));
      check(hash + ' does not render single-course content by default',
        before.indexOf(courseMarker) === -1, before.slice(0, 200));
      // 全局页换课: 上下文换到 GEO, 视图仍聚合 (数据还是不筛课的请求)。
      useCourse(table, GEO);
      await switchCourse(GEO);
      check(hash + ' keeps the route after switching course on a global page',
        sandbox.window.location.hash === hash, sandbox.window.location.hash);
      check(hash + ' keeps the all-courses view after switching course',
        html(sandbox).indexOf('全部课程') >= 0, html(sandbox).slice(0, 200));
      check(hash + ' does not become a single-course view after switching',
        html(sandbox).indexOf(courseMarker) === -1, html(sandbox).slice(0, 300));
      check(hash + ' switches the course state on a global page',
        grab(sandbox, 'state').courseId === GEO,
        String(grab(sandbox, 'state').courseId));
      // 选择器语义 = 查看范围: 保持「全部课程」, 不跟随当前课程。
      const picker = sandbox.document.getElementById('course-switch').innerHTML;
      check(hash + ' keeps the switcher on all-courses after a course switch',
        /<option value="" selected>/.test(picker), picker.slice(0, 200));
    }

    // 全局页的单课程筛选 (任务书 §6/§11): 选器选 GEO 后本页显示 GEO 的数据,
    // 且**不**碰 course context / localStorage —— 筛选不是课程上下文。
    // 概览的筛选视图给一个回全部的出口 (scope.viewAll 链接)。
    {
      const table = switchTable();
      const sandbox = await loadApp(table, { 'ca.course': GP });
      const route = grab(sandbox, 'route');
      const setScope = grab(sandbox, 'setScope');
      // 桩的 course-selection 判定钉在 GP: 模拟"用户偏好仍是 GP, 筛选只是
      // 本页查看范围"。否则桩的判定会把上下文翻成 GEO, 掩盖真正的契约。
      function pinSelectionToGp() {
        table['/api/course-selection'] = {
          data: { preferred: GP, course_id: GP, reason: 'preferred', available: [GP, GEO] },
        };
      }
      sandbox.window.location.hash = '#/today';
      useCourse(table, GEO);
      pinSelectionToGp();
      await route();
      const fetchedBefore = sandbox.__fetched.length;
      setScope(GEO);
      await route();
      const todayFetched = sandbox.__fetched.slice(fetchedBefore).join(' | ');
      check('#/today scoped filter re-queries with the scoped course',
        todayFetched.indexOf('course_id=' + GEO) >= 0, todayFetched.slice(0, 300));
      const filtered = html(sandbox);
      check('#/today scoped to GEO renders that course\'s data',
        filtered.indexOf('NOTE-GEO-today') >= 0 &&
        filtered.indexOf('NOTE-GP-today') === -1,
        filtered.slice(0, 200));
      check('#/today scoping does not touch the course context',
        grab(sandbox, 'state').courseId === GP &&
        sandbox.window.localStorage.getItem('ca.course') === GP,
        String(grab(sandbox, 'state').courseId) + ' / ' +
        String(sandbox.window.localStorage.getItem('ca.course')));
      // 概览的筛选视图: 数据走单课程 dashboard 接口 (course_id=GEO), 页面
      // 给出回全部的出口。
      sandbox.window.location.hash = '#/';
      useCourse(table, GEO);
      pinSelectionToGp();
      await route();
      setScope(GEO);
      sandbox.__fetched.length = 0;
      await route();
      const dashFetched = sandbox.__fetched.join(' | ');
      check('#/ scoped dashboard re-queries with the scoped course',
        dashFetched.indexOf('course_id=' + GEO) >= 0, dashFetched.slice(0, 300));
      const dashOut = html(sandbox);
      check('#/ scoped dashboard renders that course\'s dashboard',
        dashOut.indexOf('FILE-GEO-dash.pdf') >= 0,
        dashOut.slice(0, 200));
      check('#/ scoped dashboard offers a way back to all courses',
        dashOut.indexOf('查看全部课程') >= 0, dashOut.slice(0, 200));
      check('#/ scoped dashboard does not touch the course context',
        grab(sandbox, 'state').courseId === GP &&
        sandbox.window.localStorage.getItem('ca.course') === GP,
        String(grab(sandbox, 'state').courseId) + ' / ' +
        String(sandbox.window.localStorage.getItem('ca.course')));
    }

    // 我的课程 (#/courses): 列表页本身渲染两门课, 断言"当前"标记搬到新课程。
    {
      const table = switchTable();
      const sandbox = await loadApp(table, { 'ca.course': GP });
      const route = grab(sandbox, 'route');
      sandbox.window.location.hash = '#/courses';
      useCourse(table, GP);
      await route();
      useCourse(table, GEO);
      await grab(sandbox, 'switchCourse')(GEO);
      check('#/courses keeps the route after switching course',
        sandbox.window.location.hash === '#/courses', sandbox.window.location.hash);
      const out = html(sandbox);
      const at = out.indexOf('card-current');
      check('#/courses marks exactly one current course',
        at >= 0 && out.indexOf('card-current', at + 1) === -1, out.slice(0, 200));
      // 'card-current' 长在卡片 div 上, 卡名 (h3) 在它后面 —— 向后找:
      // 离它最近的那个卡名就是被标为当前的卡。
      check('#/courses marks the NEW course as current',
        at >= 0 && out.indexOf(GEO_NAME, at) >= 0 &&
        out.indexOf(GEO_NAME, at) < out.indexOf(GP_NAME, at),
        out.slice(at, at + 200));
    }

    // 课程详情族: courseId 长在 URL 里, 切换后必须换到新课程的详情页
    // (否则下一次路由的 setRouteCourse 会把状态翻回旧课程)。
    {
      const table = switchTable();
      const sandbox = await loadApp(table, { 'ca.course': GP });
      const route = grab(sandbox, 'route');
      sandbox.window.location.hash = '#/courses/' + GP;
      useCourse(table, GP);
      await route();
      check('course detail renders the GP course',
        html(sandbox).indexOf(GP_NAME) >= 0, html(sandbox).slice(0, 200));
      useCourse(table, GEO);
      await grab(sandbox, 'switchCourse')(GEO);
      check('switching course on a course detail retargets to the new course',
        sandbox.window.location.hash === '#/courses/' + GEO, sandbox.window.location.hash);
      await route();
      check('the retargeted course detail renders the GEO course',
        html(sandbox).indexOf(GEO_NAME) >= 0, html(sandbox).slice(0, 200));
    }

    // 错题详情 `#/mistakes/<旧课>/<kp>`: id 在新课程下必然 404, 退到列表。
    {
      const table = switchTable();
      const sandbox = await loadApp(table, { 'ca.course': GP });
      sandbox.window.location.hash = '#/mistakes/' + GP + '/kp-1';
      useCourse(table, GEO);
      await grab(sandbox, 'switchCourse')(GEO);
      check('switching course on a mistake detail falls back to the list',
        sandbox.window.location.hash === '#/mistakes', sandbox.window.location.hash);
      await grab(sandbox, 'route')();
      check('the fallback list renders the GEO course',
        html(sandbox).indexOf('stu-geo') >= 0, html(sandbox).slice(0, 200));
    }

    // 侧边栏: 课程项带 data-course-switch (纯左键拦截、不导航), href 仍是
    // 有效的课程详情深链 (修饰键/中键打开与无 JS 的 fallback)。
    {
      const table = switchTable();
      const sandbox = await loadApp(table, { 'ca.course': GP });
      await grab(sandbox, 'loadSidebar')();
      const bar = sandbox.document.getElementById('course-list').innerHTML;
      check('sidebar marks switchable courses',
        bar.indexOf('data-course-switch="' + GP + '"') >= 0 &&
        bar.indexOf('data-course-switch="' + GEO + '"') >= 0, bar.slice(0, 300));
      check('sidebar keeps the detail URL as the href fallback',
        bar.indexOf('href="#/courses/' + GP + '"') >= 0 &&
        bar.indexOf('href="#/courses/' + GEO + '"') >= 0, bar.slice(0, 300));
    }
  }

  // ------------------------------------------------------------- 汇总

  if (failures.length) {
    console.error('UI RENDER CHECK: FAILED');
    failures.forEach((f) => console.error('  - ' + f));
    console.error(checks - failures.length + '/' + checks + ' checks passed');
    process.exit(1);
  }
  console.log('UI RENDER CHECK: OK (' + checks + ' checks)');
}

main().catch((err) => {
  console.error('UI RENDER CHECK: ERROR');
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
