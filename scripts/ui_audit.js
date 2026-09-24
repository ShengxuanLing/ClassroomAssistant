/* 课堂助手 UI 审计 (Task 47.8)
 *
 * 为什么需要它
 * ------------
 * 本机是 Windows, 而浏览器自动化 (agent-browser) 只支持 macOS / Linux, 所以
 * 没有浏览器端到端测试。但 app.js 的页面函数是纯字符串拼接, 可以在 Node 里用
 * 一个最小 DOM 桩**真实执行**。这个脚本用真实执行覆盖 spec 47.8 的清单:
 *
 *     empty state / loading state / error state / long text /
 *     Spanish / Catalan / Chinese / large knowledge base /
 *     many students / many exercises / mobile-ish narrow viewport
 *
 * 它不声称是浏览器测试。它证明的是: 给定真实的 API 响应形状, 每个页面在三种
 * 界面语言下都能跑通, 且**界面文案里不出现未翻译的中文**。
 *
 * 真实事件冒烟清单 (P1-6 固化边界 —— 已覆 / 未覆)
 * ------------------------------------------------
 * 这条清单是**契约**: 下面"未覆"的项不得因为本脚本通过而被宣称已覆盖。
 *
 * 已覆 (本脚本与 ui_render_check.js 真实执行的部分):
 *   - 页面函数**真实执行**: 不是正则匹配源码, 而是在 Node vm + 最小 DOM 桩里
 *     真的调用 pageDashboard() / pageExercise() / pageCourse() ... 并取回
 *     渲染出的 HTML 串做断言。
 *   - 三语 key parity: es / ca / zh 三张翻译表的 key 集合必须完全一致;
 *     且 es / ca 渲染结果里不得出现任何 CJK 字符 (夹具数据是纯 ASCII,
 *     出现 CJK 只可能是界面漏译 —— 无假阳性)。
 *   - SECRET_ANSWER / SECRET_EXPLANATION 不泄漏: 练习页在作答前不得把
 *     答案或解析渲染进 DOM。
 *   - 原文原样渲染: 西语 / 加泰语 / 中文的 Evidence 与知识点正文不被改写。
 *   - esc() 转义: 所有来自 API 的文本插入 DOM 前必须经 esc()。
 *   - 空 / 加载 / 错误 / 超长文本 / 大量数据 等边界状态。
 *
 * 未覆 (本脚本**不**覆盖, 不得宣称已验证):
 *   - CSS 布局与视觉呈现: 不解析 styles.css, 不做排版/重排/响应式断言。
 *   - 真实点击事件: 只调用页面函数, 不派发真实的 click / submit 事件;
 *     表单接线函数 (wireUploadForm / wireAnswerForm / wireStudentForm) 只做
 *     "是否存在且被接上"的静态断言, 不模拟用户操作。
 *   - 真实浏览器事件: hashchange / DOMContentLoaded / 键盘 / 焦点 / 滚动 /
 *     剪贴板 / 拖拽上传 等均未覆盖。
 *   - 异步竞态与真实网络: fetch 由桩返回固定响应, 不覆盖超时、重试、
 *     并发请求乱序。
 *   - 跨浏览器兼容性 / 可访问性 (a11y) / 打印样式。
 *
 * 语言检查为什么是"es/ca 输出里不得出现 CJK"
 * ------------------------------------------
 * 所有夹具数据都是纯 ASCII。因此 es / ca 模式下渲染出的 HTML 里只要出现
 * 任何一个 CJK 字符, 就只能是**界面文案漏译**。这是一条没有假阳性的断言,
 * 也是能真正抓住"语言选择器只翻译了一半界面"这类缺陷的断言。
 *
 * 用法:
 *   node scripts/ui_audit.js                # 跑全部检查, 退出码 0 = 通过
 *   node scripts/ui_audit.js --dump zh      # 打印全部页面 HTML (用于逐字节比对)
 *   node scripts/ui_audit.js --preview zh --out cache/preview-dashboard-zh.html
 *                                           # 输出可直接打开的静态 HTML 快照
 *                                           # (真实骨架 + 内联 CSS + 真实渲染,
 *                                           #  剥掉 <script>; 不参与断言)
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
const CSS = fs.readFileSync(path.join(ROOT, 'src', 'web', 'styles.css'), 'utf8');

const COURSE_ID = 'course-1';
const SESSION_ID = 'session-1';
const KP_ID = 'kp-1';
const MATERIAL_ID = 'material-1';
const STUDENT_ID = 'student-1';
const EXERCISE_ID = 'exercise-1';

// ---------------------------------------------------------------- 夹具数据
// 全部 ASCII: 任何 CJK 出现在渲染结果里都只能是界面文案。

const LONG_TOKEN = 'A'.repeat(400);

function course() {
  return { course_id: COURSE_ID, name: 'Algebra Lineal', code: 'ALG', language: 'es' };
}

function session() {
  return {
    session_id: SESSION_ID,
    session_number: 3,
    title: 'Tema 3',
    date: '2026-03-01',
  };
}

function material(overrides) {
  return Object.assign(
    {
      material_id: MATERIAL_ID,
      filename: 'clase-03.mp3',
      extension: '.mp3',
      size: 204800,
      material_type: 'audio',
      source_type: 'audio',
      language: 'es',
      processing_status: 'SUCCEEDED',
      session_id: SESSION_ID,
      content_hash: LONG_TOKEN,
      relative_path: 'audio/' + LONG_TOKEN + '.mp3',
      duplicate: false,
      error: null,
    },
    overrides
  );
}

function knowledgePoint(overrides) {
  return Object.assign(
    {
      knowledge_id: KP_ID,
      title: 'Definicion de funcion',
      content: 'Una funcion es una relacion entre dos conjuntos.',
      validation_status: 'supported',
      review_status: 'pending',
      knowledge_score: 7,
      importance: 'high',
      confidence: 'high',
      needs_verification: false,
      original_terms: ['funcio'],
      evidence_refs: ['evid-1'],
      language: 'es',
    },
    overrides
  );
}

function evidence(overrides) {
  return Object.assign(
    {
      evidence_id: 'evid-1',
      content: 'Una funcion es una relacion entre dos conjuntos.',
      language: 'es',
      confidence: 'high',
      evidence_type: 'transcript',
      metadata: {},
      source: {
        material_id: MATERIAL_ID,
        location: 'audio',
        timestamp_start: 12.5,
        timestamp_end: 30.0,
        page: null,
        line: null,
        paragraph: null,
      },
      material_id: MATERIAL_ID,
      material: {
        material_id: MATERIAL_ID,
        filename: 'clase-03.mp3',
        material_type: 'audio',
        source_type: 'audio',
      },
    },
    overrides
  );
}

function courseWorkspace(overrides) {
  return Object.assign(
    {
      course_id: COURSE_ID,
      course: course(),
      teacher: 'Prof. Ana',
      semester: '2026-2',
      language: 'es',
      metadata: { teacher: 'Prof. Ana', semester: '2026-2' },
      counts: {
        sessions: 1,
        materials: 1,
        evidence: 4,
        knowledge_points: 12,
        pending_review: 3,
      },
      sessions: [
        {
          session_id: SESSION_ID,
          course_id: COURSE_ID,
          session_number: 3,
          date: '2026-03-01',
          title: 'Tema 3',
          status: 'REVIEW_REQUIRED',
          counts: { materials: 1, evidence: 4, knowledge_points: 12, pending_review: 3 },
        },
      ],
      coverage: {
        course_id: COURSE_ID,
        coverage_ratio: 0.75,
        assigned_knowledge_points: 9,
        total_knowledge_points: 12,
      },
      gaps: { gaps: [{ gap_type: 'MISSING_PREREQUISITE', description: 'no prerequisite' }] },
      pending_review: [
        { knowledge_point_id: KP_ID, validation_status: 'unverified', knowledge_score: 4 },
      ],
      pending_review_ids: [KP_ID],
      knowledge: {
        count: 12,
        by_validation_status: { supported: 9, unverified: 2, conflicted: 1 },
        by_review_status: { pending: 8, confirmed: 4 },
      },
      recent_materials: [material()],
    },
    overrides
  );
}

function sessionWorkspace(overrides) {
  return Object.assign(
    {
      course_id: COURSE_ID,
      session_id: SESSION_ID,
      course: course(),
      session: session(),
      status: 'REVIEW_REQUIRED',
      status_reason: '3 knowledge point(s) pending human review.',
      overview: {
        materials: 1,
        materials_by_status: { COMPLETED: 1 },
        evidence: 3,
        evidence_by_group: { transcript: 1, ocr: 1, document: 1, note: 0, other: 0 },
        knowledge_points: 12,
        knowledge_by_validation_status: { supported: 10, conflicted: 2 },
        knowledge_by_review_status: { pending: 12 },
        pending_review: 3,
        processing_by_status: { QUEUED: 0, RUNNING: 0, SUCCEEDED: 1, FAILED: 0, CANCELLED: 0 },
      },
      materials: [material()],
      processing: {
        course_id: COURSE_ID,
        session_id: SESSION_ID,
        total: 1,
        by_status: { SUCCEEDED: 1 },
        evidence_total: 3,
        stages: [
          { key: 'PREPARING', done: true },
          { key: 'PROCESSING_MATERIALS', done: true },
          { key: 'EXTRACTING_EVIDENCE', done: true },
          { key: 'ASSEMBLING_KNOWLEDGE', done: true },
          { key: 'VALIDATING', done: true },
          { key: 'CHECKING_CONFLICTS', done: true },
          { key: 'FINISHED', done: true },
        ],
        jobs: [
          {
            job_id: 'job-1',
            material_id: MATERIAL_ID,
            course_id: COURSE_ID,
            session_id: SESSION_ID,
            status: 'SUCCEEDED',
            stage: 'DONE',
            attempts: 1,
            max_attempts: 3,
            error: null,
            evidence_ids: ['evid-1'],
            evidence_count: 1,
            warnings: [],
            quality: null,
          },
        ],
      },
      transcript: [evidence()],
      ocr: [evidence({ evidence_id: 'evid-2', evidence_type: 'ocr' })],
      documents: [evidence({ evidence_id: 'evid-3', evidence_type: 'document' })],
      notes: [],
      evidence: [
        evidence(),
        evidence({ evidence_id: 'evid-2', evidence_type: 'ocr' }),
        evidence({ evidence_id: 'evid-3', evidence_type: 'document' }),
        evidence({ evidence_id: 'evid-4', material: null, material_id: null }),
      ],
      knowledge: {
        count: 12,
        points: [knowledgePoint()],
        knowledge_ids: [KP_ID],
      },
      review: {
        pending: [
          {
            knowledge_point_id: KP_ID,
            validation_status: 'unverified',
            knowledge_score: 4,
            supporting_evidence_ids: ['evid-1'],
            conflict_ids: [],
            needs_verification: true,
            priority: 2,
          },
        ],
        pending_count: 3,
        history: [],
      },
      learning: {
        student_id: STUDENT_ID,
        display_name: 'Ana',
        available: true,
        note: null,
        states: [{ knowledge_point_id: KP_ID, state: 'not_started' }],
        pending_exercises: [{ exercise_id: EXERCISE_ID, prompt: 'El grado es ___' }],
      },
    },
    overrides
  );
}

function today(overrides) {
  return Object.assign(
    {
      date: '2026-03-01',
      generated_at: '2026-03-01T09:00:00+00:00',
      course_id: COURSE_ID,
      courses_today: [
        { course_id: COURSE_ID, name: 'Algebra Lineal', code: 'ALG', language: 'es', sessions_today: 1 },
      ],
      sessions_today: [
        {
          session_id: SESSION_ID,
          course_id: COURSE_ID,
          session_number: 3,
          date: '2026-03-01',
          title: 'Tema 3',
          status: 'REVIEW_REQUIRED',
          counts: { materials: 1, evidence: 3, knowledge_points: 12, pending_review: 3 },
          course_name: 'Algebra Lineal',
          course_code: 'ALG',
        },
      ],
      pending_materials: [material({ processing_status: 'REGISTERED', course_id: COURSE_ID, course_name: 'Algebra Lineal' })],
      pending_review: [
        { knowledge_point_id: KP_ID, validation_status: 'unverified', course_id: COURSE_ID, course_name: 'Algebra Lineal' },
      ],
      study: {
        available: true,
        course_id: COURSE_ID,
        student_id: STUDENT_ID,
        plan_id: 'plan-1',
        rules_version: 'v1',
        note: null,
        items: [{ knowledge_point_id: KP_ID, reason_codes: ['UNVERIFIED'], prerequisite_ids: [] }],
        items_total: 1,
      },
      counts: {
        courses_today: 1,
        sessions_today: 1,
        pending_materials: 1,
        pending_review: 1,
        study_items: 1,
      },
      limits: { pending_materials: 20, pending_review: 20 },
    },
    overrides
  );
}

function dashboard() {
  return {
    course_id: COURSE_ID,
    version: '0.35.0',
    courses: [course()],
    sessions: [session()],
    materials: [material()],
    students: [{ student_id: STUDENT_ID, display_name: 'Ana' }],
    exercises: [
      {
        exercise_id: EXERCISE_ID,
        exercise_type: 'fill_blank',
        prompt: 'El grado de la funcion es ___',
      },
    ],
    review_pending: [{ knowledge_point_id: KP_ID, reason: 'UNVERIFIED' }],
    gaps: { gaps: [{ gap_type: 'MISSING_PREREQUISITE', description: 'no prerequisite' }] },
    knowledge: {
      count: 12,
      points: [knowledgePoint()],
      summary: {
        knowledge_point_count: 12,
        topic_count: 3,
        relation_count: 5,
        validation_summary: { validated: 9, unverified: 2, conflicted: 1 },
        review_summary: { confirmed: 4, pending: 8 },
        coverage: { assigned: 12 },
      },
    },
    processing: {
      by_status: { SUCCEEDED: 3, FAILED: 1 },
      jobs: [
        {
          material_id: MATERIAL_ID,
          stage: 'ocr',
          status: 'SUCCEEDED',
          attempts: 1,
          max_attempts: 3,
        },
      ],
    },
  };
}

/**
 * Task 68: 多课程总览 ``/api/my-courses``。
 *
 * 夹具刻意放两门课, 其中一门**不是**当前课程 —— 这样"当前课程标记"和
 * "逐课程计数"才真的被渲染到, 而不是只有一个空壳。
 */
function myCoursesRow(courseId, name, code, kp, pending, conflicts) {
  return {
    course_id: courseId,
    name: name,
    code: code,
    language: 'es',
    counts: {
      sessions: 5, materials: 10, knowledge_points: kp,
      pending_review: pending, conflicts: conflicts,
      students: 1, exercises: 4, answers: 7,
    },
    validation: { supported: kp - conflicts, unverified: 0, conflicted: conflicts },
    review: { confirmed: 0, pending: kp, rejected: 0, kept_unverified: 0 },
    evidence_total: 12,
    gaps: 0,
    last_session: {
      session_id: SESSION_ID, session_number: 3, title: 'Tema 3', date: '2026-03-01',
    },
  };
}

function myCourses(overrides) {
  return Object.assign(
    {
      view: 'multi-course-workspace-v1',
      language: 'zh',
      course_count: 2,
      selection: {
        preferred: COURSE_ID, course_id: COURSE_ID, reason: 'preferred',
        available: [COURSE_ID, 'course-2'],
      },
      courses: [
        myCoursesRow(COURSE_ID, 'Algebra Lineal', 'ALG', 12, 3, 1),
        myCoursesRow('course-2', 'Calculo', 'CAL', 8, 2, 0),
      ],
      totals: {
        counts: {
          sessions: 10, materials: 20, knowledge_points: 20, pending_review: 5,
          conflicts: 1, students: 2, exercises: 8, answers: 14,
        },
        validation: { supported: 19, unverified: 0, conflicted: 1 },
        review: { confirmed: 0, pending: 20, rejected: 0, kept_unverified: 0 },
        evidence_total: 24,
        gaps: 0,
      },
      note: 'per-course counts, no cross-course merge',
      empty: false,
      empty_note: null,
    },
    overrides
  );
}

/** 空课程表 —— 必须给出明确文案, 不能是空白页。 */
function emptyMyCourses() {
  return myCourses({
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
  });
}

function trace() {
  return {
    complete: true,
    unresolved_material_ids: [],
    knowledge_point: knowledgePoint(),
    materials: [material()],
    topics: [{ topic_id: 'topic-1', name: 'Funciones' }],
    source_sessions: [session()],
    sessions: [session()],
    language: { declared: 'es', evidence_languages: ['es'] },
    dependencies: {
      outgoing: [
        { relation_type: 'PREREQUISITE_OF', target_knowledge_point_id: 'kp-2' },
      ],
      incoming: [
        { relation_type: 'PREREQUISITE_OF', source_knowledge_point_id: 'kp-0' },
      ],
      related_points: ['kp-3'],
    },
    links: [
      {
        evidence: {
          evidence_id: 'evid-1',
          content: 'Una funcion es una relacion.',
          language: 'es',
          evidence_type: 'transcript',
          confidence: 'high',
          source: {
            material_id: MATERIAL_ID,
            timestamp_start: 12.5,
            timestamp_end: 30.0,
            page: 3,
            line: 4,
            paragraph: 'p1',
          },
        },
        material: material(),
      },
    ],
  };
}

function studentDashboard() {
  return {
    course_id: COURSE_ID,
    student: { student_id: STUDENT_ID, display_name: 'Ana' },
    learning_path: {
      nodes: [
        {
          knowledge_point_id: KP_ID,
          title: 'Definicion de funcion',
          state: 'not_started',
          next_event: 'viewed',
          prerequisites: [],
          blocked_by: [],
          activity: { answer_count: 0 },
          exercises: [{ exercise_id: EXERCISE_ID, prompt: 'El grado es ___' }],
          evaluations: [],
        },
      ],
    },
    study_plan: { items: [{ knowledge_point_id: KP_ID, title: 'Definicion' }] },
    exercises: [{ exercise_id: EXERCISE_ID, prompt: 'El grado es ___', submitted: false }],
    evaluations: [
      {
        evaluation_id: 'eval-1',
        exercise_id: EXERCISE_ID,
        status: 'correct',
        score: 1,
        submitted_at: '2026-03-02T10:00:00+00:00',
      },
    ],
    gaps: [{ gap_type: 'MISSING_PREREQUISITE', description: 'no prerequisite' }],
    state_counts: { not_started: 1 },
    average_score: 0.5,
  };
}

function exerciseView() {
  return {
    course_id: COURSE_ID,
    student_id: STUDENT_ID,
    exercise_id: EXERCISE_ID,
    exercise_type: 'fill_blank',
    prompt: 'El grado de la funcion es ___',
    choices: [],
    difficulty: 2,
    answer_format: 'text',
    knowledge_point_ids: [KP_ID],
    knowledge_points: [{ knowledge_id: KP_ID, title: 'Definicion de funcion' }],
    prerequisites: [],
    evidence: [],
    submitted: false,
    submitted_value: null,
    submitted_at: null,
    evaluation: null,
    answer_key_withheld: true,
  };
}

// 课程复习中心页 (`#/review-center/<id>`, Task 62) 已于 2026-09-21 按用户要求
// 从前端整体删除。这份夹具**保留**: 后端 GET /api/courses/{id}/review 投影层仍在
// (tests/test_course_review.py 有自己的契约测试), 夹具是它的形状记录。
// 当前没有任何页面消费它 —— 与同表里的 review-summary 一样, 属于"端点存在、
// 界面不渲染"的条目, 不是漏改。
function courseReview() {
  const session = {
    session_id: SESSION_ID,
    course_id: COURSE_ID,
    session_number: 3,
    date: '2026-03-01',
    title: 'Tema 3',
    evidence_count: 4,
    knowledge_count: 12,
    pending_review: 3,
    validation: { supported: 9, unverified: 2, conflicted: 1 },
  };
  return {
    course_id: COURSE_ID,
    course: course(),
    empty: false,
    overview: {
      total: 12,
      validation: { supported: 9, unverified: 2, conflicted: 1 },
      review: { pending: 3, confirmed: 6, rejected: 1, kept_unverified: 2 },
      pending_review: 3,
      conflicted: 1,
    },
    topics: [
      {
        topic_id: 'topic-1',
        name: 'Vectores',
        order_index: 1,
        knowledge_count: 5,
        validation: { supported: 4, unverified: 1, conflicted: 0 },
        review: { pending: 1, confirmed: 3, rejected: 0, kept_unverified: 1 },
        pending_review: 1,
        coverage_status: 'covered',
      },
    ],
    sessions: [session],
    review_queue: {
      items: [
        {
          knowledge_point_id: KP_ID,
          validation_status: 'unverified',
          review_status: 'pending',
          knowledge_score: 4,
          priority: 1,
        },
      ],
      total: 1,
      path: '/api/reviews',
    },
    conflicts: [
      {
        conflict_id: 'conf-1',
        evidence_refs: ['evid-a', 'evid-b'],
        description: 'order relation mismatch',
        status: 'PENDING',
        knowledge_point_id: KP_ID,
        knowledge_point_ids: [KP_ID],
        sides: [
          { label: 'Evidence A', evidence_id: 'evid-a' },
          { label: 'Evidence B', evidence_id: 'evid-b' },
        ],
        knowledge_detail_path: '/api/knowledge/' + KP_ID,
      },
    ],
    coverage: { course_id: COURSE_ID, coverage_ratio: 0.75 },
    gaps: {
      gaps: [
        { gap_type: 'MISSING_PREREQUISITE', description: 'prerequisite not covered', knowledge_point_id: KP_ID },
      ],
    },
    counts: {
      knowledge: 12,
      topics: 1,
      sessions: 1,
      pending_review: 3,
      conflicted: 1,
      conflicts_reported: 1,
    },
  };
}

/**
 * Task 58 课堂时间线 (``GET /api/sessions/{id}/timeline``)。
 *
 * 夹具刻意覆盖四个分支:
 *
 * 1. 有 ``timestamp`` 的转录条目 —— 正常路径;
 * 2. ``timestamp: null`` 的 OCR 条目 —— 必须渲染成 "Time unavailable",
 *    而不是空白或 ``null``;
 * 3. ``item_type === 'knowledge'`` —— 标题必须是指向知识点详情页的链接;
 * 4. 未知分组 (``evidence:unknown_group``) —— 类型标签必须退回
 *    "Evidencia", 绝不把原始枚举名甩给用户。
 *
 * 全 ASCII: es/ca 下渲染出 CJK 就一定是界面文案漏译。
 */
function sessionTimeline() {
  const evItem = (id, type, timestamp, title) => ({
    item_id: 'ev-' + id,
    session_id: SESSION_ID,
    item_type: 'evidence:' + type,
    timestamp: timestamp,
    end_timestamp: null,
    title: title,
    source_id: id,
    material_id: MATERIAL_ID,
    knowledge_ids: [],
    metadata: {},
  });
  return {
    timeline: [
      evItem('evd-1', 'transcript', 12.5, 'Bienvenida a la sesion'),
      evItem('evd-2', 'ocr', null, 'Pizarra: definicion de funcion'),
      {
        item_id: 'kp-' + KP_ID,
        session_id: SESSION_ID,
        item_type: 'knowledge',
        timestamp: 12.5,
        end_timestamp: 18,
        title: 'Definicion de funcion',
        source_id: KP_ID,
        material_id: null,
        knowledge_ids: [KP_ID],
        metadata: {},
      },
      evItem('evd-3', 'unknown_group', 30, 'Item sin grupo conocido'),
    ],
  };
}

function studentToday() {
  // Task 63: /api/student-today 的纯 ASCII 夹具。
  // 任何中文出现在 es/ca 输出里都会被 CJK 检查抓住 —— 所以这里刻意用 ASCII。
  return {
    date: '2026-03-01',
    generated_at: '2026-03-01T09:00:00+00:00',
    course_id: null,
    lang: 'zh',
    has_activity: true,
    note: null,
    classes_today: [
      {
        session_id: SESSION_ID,
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
          { knowledge_point_id: KP_ID, reason_codes: ['review_pending', 'unverified_knowledge'], prerequisite_ids: [] },
        ],
        tasks_total: 5,
        next_task: {
          knowledge_point_id: KP_ID,
          reason_codes: ['review_pending'],
          prerequisite_ids: [],
        },
        plan_id: 'plan-abc123',
        rules_version: 'rules-v1',
      },
    ],
    learning_paths: [
      {
        course_id: COURSE_ID,
        course_name: 'Algebra Lineal',
        student_id: STUDENT_ID,
        knowledge_point_id: KP_ID,
        current: {
          position: 1,
          knowledge_point_id: KP_ID,
          knowledge_point: { knowledge_id: KP_ID, title: 'Definicion de funcion', validation_status: 'supported', review_status: 'pending', knowledge_score: 0.5 },
          state: 'practicing',
          status: 'IN_PROGRESS',
          status_basis: 'state=practicing (Task 30 LearningState)',
          next_event: 'reviewed',
          activity: { exposure_count: 1, practice_count: 2, answer_count: 2, correct_count: 1, incorrect_count: 1 },
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
        course_name: 'Algebra Lineal',
        student_id: STUDENT_ID,
        exercise_type: 'multiple_choice',
        prompt: 'Cual es la definicion correcta?',
        knowledge_point_ids: [KP_ID],
        difficulty: 'easy',
      },
    ],
    recent_evaluations: [
      {
        answer_id: 'answer-1',
        exercise_id: EXERCISE_ID,
        submitted_value: 'b',
        sequence: 0,
        submitted_at: '2026-03-01T09:00:00+00:00',
        status: 'incorrect',
        score: 0.0,
        feedback: 'expected a',
        course_id: COURSE_ID,
        course_name: 'Algebra Lineal',
        student_id: STUDENT_ID,
      },
    ],
    attention: [
      {
        course_id: COURSE_ID,
        course_name: 'Algebra Lineal',
        student_id: STUDENT_ID,
        knowledge_point_id: KP_ID,
        kind: 'NEEDS_PRACTICE',
        state: 'practicing',
        basis: 'StudentState state=practicing (Task 30)',
      },
    ],
    review: {
      items: [
        {
          knowledge_point_id: KP_ID,
          validation_status: 'unverified',
          knowledge_score: 0.5,
          supporting_evidence_ids: ['evid-a'],
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

function routes() {
  const table = {};
  table['/api/health'] = {
    application: 'classroom-assistant',
    status: 'ok',
    version: '0.35.0',
    storage: { courses: 1 },
    evidence_count: 4,
    asr_mode: 'mock',
    ocr_mode: 'mock',
  };
  table['/api/courses'] = { courses: [course()] };
  table['/api/courses/' + COURSE_ID] = course();
  table['/api/courses/' + COURSE_ID + '/workspace'] = courseWorkspace();
  table['/api/courses/' + COURSE_ID + '/review'] = courseReview();
  table['/api/courses/' + COURSE_ID + '/review-summary'] = {
    course_id: COURSE_ID,
    empty: false,
    knowledge: 12,
    pending_review: 3,
    conflicted: 1,
  };
  table['/api/courses/' + COURSE_ID + '/sessions/' + SESSION_ID + '/workspace'] = sessionWorkspace();
  table['/api/today'] = today();
  table['/api/student-today'] = studentToday();
  // Task 66: 学习流程。夹具全部 ASCII, 所以 es/ca 模式下只要渲染出 CJK,
  // 就一定是界面文案漏译 —— 学习页新增了约 50 条词条, 这条断言是它的
  // 三语完整性检查。
  table['/api/students/' + STUDENT_ID + '/learning/start'] = learningStart();
  table['/api/students/' + STUDENT_ID + '/learning/knowledge/' + KP_ID] =
    learningKnowledge();
  // Task 67: 考前复习集合。同上, 夹具全 ASCII, es/ca 下出现 CJK 即漏译。
  table['/api/students/' + STUDENT_ID + '/review-set'] = reviewSet();
  table['/api/dashboard'] = dashboard();
  // Task 68: 全部课程总览 + 课程选择判定。
  table['/api/my-courses'] = myCourses();
  table['/api/course-selection'] = myCourses().selection;
  table['/api/courses/' + COURSE_ID + '/summary'] = myCourses().courses[0];
  // 材料页的课堂下拉: 选项来自 /api/sessions (与真实端点同形状)。
  table['/api/sessions'] = { sessions: [session()] };
  table['/api/sessions/' + SESSION_ID] = session();
  // Task 58 课堂时间线 (2026-09-21 接进 pageSession)。夹具全 ASCII ——
  // es/ca 下只要渲染出 CJK, 就一定是界面文案漏译。
  table['/api/sessions/' + SESSION_ID + '/timeline'] = sessionTimeline();
  table['/api/knowledge'] = { knowledge_points: [knowledgePoint()] };
  table['/api/course-knowledge'] = { topic_count: 3, relation_count: 5 };
  table['/api/materials'] = { materials: [material()] };
  table['/api/processing'] = { by_status: { SUCCEEDED: 3 } };
  table['/api/reviews'] = { reviews: [{ knowledge_point_id: KP_ID, reason: 'UNVERIFIED', review_status: 'PENDING' }] };
  table['/api/knowledge/' + KP_ID + '/trace'] = trace();
  table['/api/students'] = { students: [{ student_id: STUDENT_ID, display_name: 'Ana' }] };
  table['/api/students/' + STUDENT_ID + '/exercises'] = {
    course_id: COURSE_ID,
    student_id: STUDENT_ID,
    exercises: [
      {
        exercise_id: EXERCISE_ID,
        exercise_type: 'fill_blank',
        prompt: 'El grado de la funcion es ___',
        difficulty: 2,
        answer_format: 'text',
        knowledge_point_ids: [KP_ID],
        knowledge_points: [{ knowledge_id: KP_ID, title: 'Definicion de funcion' }],
        prerequisites: [],
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
  };
  table['/api/students/' + STUDENT_ID + '/exercises/' + EXERCISE_ID] = exerciseView();
  // Task 64: 出题面板与出题依据链
  table['/api/knowledge'] = {
    knowledge_points: [
      knowledgePoint(),
      knowledgePoint({
        knowledge_id: 'kp-unverified-1',
        title: 'Dominio de una funcion',
        validation_status: 'unverified',
        review_status: 'pending',
      }),
    ],
  };
  table['/api/exercises/' + EXERCISE_ID + '/grounding'] = groundingChain();
  // Task 65: 错题与薄弱知识点中心
  table['/api/students/' + STUDENT_ID + '/mistakes'] = mistakeCenter();
  table['/api/students/' + STUDENT_ID + '/mistakes/' + KP_ID] = mistakeDetail();
  table['/api/students/' + STUDENT_ID + '/dashboard'] = studentDashboard();
  return table;
}

/** Task 66: 今天的入口 —— 有可用任务。夹具全 ASCII。 */
function learningStart() {
  const task = {
    course_id: COURSE_ID,
    student_id: STUDENT_ID,
    knowledge_point_id: KP_ID,
    title: 'Definicion de funcion',
    state: 'exposed',
    next_event: 'practiced',
    next_action: 'practiced',
    unmet_prerequisite_ids: [],
  };
  return {
    course_id: COURSE_ID,
    student_id: STUDENT_ID,
    lang: 'es',
    workflow_version: 'daily-learning-workflow-v1',
    has_task: true,
    note: null,
    current_task: task,
    next_task: Object.assign({}, task, { knowledge_point_id: 'kp-2' }),
    progress: {
      by_state: { not_started: 1, exposed: 1, practicing: 0, reviewing: 0 },
      finished: 0,
      orderable: 2,
      total: 2,
      excluded: { rejected: [], conflicted: ['kp-9'] },
    },
    excluded: { rejected: [], conflicted: ['kp-9'] },
    links: {},
  };
}

/** Task 66: 知识点学习页。两个 truth axis 刻意取**不同**的值。 */
function learningKnowledge() {
  return {
    course_id: COURSE_ID,
    student_id: STUDENT_ID,
    lang: 'es',
    knowledge_point: {
      knowledge_id: KP_ID,
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
        evidence_id: 'evidence-1',
        evidence_type: 'document',
        quote: 'Una funcion asocia cada entrada con una salida.',
        material_filename: 'tema1.pdf',
      },
    ],
    materials: [{ material_id: MATERIAL_ID, filename: 'tema1.pdf' }],
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
    next_task: null,
    truth_flags: {},
  };
}

/** Task 67: 考前复习集合。
 *
 * 三个桶**都**要有内容 —— 空桶会让"桶是否渲染"这类断言永远通过。
 * 阻塞项刻意同时带 block_reason 与 attention 项带 attention_reason,
 * 因为前端的 `rs.block.*` / `rs.reason.*` 是动态 key, 少一个就是漏翻译。
 */
function reviewSet() {
  const item = (over) => Object.assign({
    knowledge_id: 'kp-x',
    title: 'Definicion de funcion',
    content: 'Una funcion asocia cada entrada con una salida.',
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
    sort_key: [2, 1, 0, 'kp-x'],
    position: 1,
  }, over);
  return {
    review_mode_version: 'exam-review-mode-v1',
    course_id: COURSE_ID,
    student_id: STUDENT_ID,
    lang: 'es',
    empty: false,
    empty_note: null,
    by_validation_status: { supported: 2, unverified: 1 },
    by_review_status: { pending: 2, confirmed: 1 },
    by_bucket: { blocked: 1, attention: 1, ready: 1 },
    counts: {
      total: 3, blocked: 1, attention: 1, ready: 1, unresolved_conflicts: 1,
    },
    buckets: {
      blocked: ['kp-blocked'],
      attention: ['kp-attention'],
      ready: ['kp-ready'],
    },
    items: [
      item({
        knowledge_id: 'kp-blocked', title: 'Definicion en conflicto',
        validation_status: 'unverified', conflict: true,
        conflict_ids: ['conflict-1'], bucket: 'blocked', position: 1,
        block_reason: 'unresolved_conflict',
        block_note: 'Unresolved conflict: both sides are shown as-is.',
        attention_reason: null, attention_note: null,
        sort_key: [1, 0, 0, 'kp-blocked'],
      }),
      item({
        knowledge_id: 'kp-attention', title: 'Definicion sin confirmar',
        bucket: 'attention', position: 2,
        sort_key: [2, 1, 0, 'kp-attention'],
      }),
      item({
        knowledge_id: 'kp-ready', title: 'Definicion confirmada',
        review_status: 'confirmed', bucket: 'ready', position: 3,
        attention_reason: null, attention_note: null,
        sort_key: [2, 1, 1, 'kp-ready'],
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
    ordering_basis: 'coverage -> evidence status -> unresolved conflict -> student learning state -> knowledge_id',
  };
}

/** Task 67: 空复习集合 (有课程有学生, 但没有知识点)。 */
function reviewSetEmpty() {
  const base = reviewSet();
  return Object.assign(base, {
    empty: true,
    empty_note: 'No review set available yet.',
    by_validation_status: {},
    by_review_status: {},
    by_bucket: { blocked: 0, attention: 0, ready: 0 },
    counts: { total: 0, blocked: 0, attention: 0, ready: 0, unresolved_conflicts: 0 },
    buckets: { blocked: [], attention: [], ready: [] },
    items: [],
    conflicts: [],
    coverage: { total_knowledge_points: 0, covered_knowledge_points: 0, coverage_ratio: 0 },
  });
}

/** Task 65: 错题本视图。错答 2 次, 但学生状态未到 NEEDS_*, 故薄弱为空。 */
function mistakeCenter(overrides) {  const row = {
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
    knowledge_point_ids: [KP_ID],
    knowledge: [{ knowledge_id: KP_ID, title: 'Definicion de funcion' }],
    evaluator_version: 'exact-v1',
  };
  const leaf = {
    exercise_id: EXERCISE_ID,
    prompt: 'El grado de la funcion es ___',
    status: 'incorrect',
    answer_id: 'answer-1',
  };
  return Object.assign(
    {
      schema_version: 1,
      center_version: 'mistake-center-v1',
      course_id: COURSE_ID,
      student_id: STUDENT_ID,
      lang: 'zh',
      group_by: 'knowledge',
      has_mistakes: true,
      note: null,
      mistakes: [row],
      knowledge: [
        {
          knowledge_id: KP_ID,
          title: 'Definicion de funcion',
          excerpt: 'Una funcion asocia cada valor del dominio',
          validation_status: 'supported',
          review_status: 'confirmed',
          language: 'ca',
          incorrect_attempts: 2,
          exercise_ids: [EXERCISE_ID],
          answer_ids: ['answer-1'],
          student_state: 'exposed',
          attention: null,
          attention_basis: null,
          mistakes: [row],
          suggested_actions: [],
          evidence_count: 1,
          practice_targets: [],
        },
      ],
      groups: [
        {
          group_kind: 'knowledge',
          group_id: KP_ID,
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
        review_center: '/api/courses/' + COURSE_ID + '/review',
        exercises: '/api/students/' + STUDENT_ID + '/exercises',
        exercise_workflow: '/api/exercise-generation',
      },
      limits: { mistakes: 200 },
    },
    overrides
  );
}

/** Task 65: 知识点错题详情。 */
function mistakeDetail(overrides) {
  return Object.assign(
    {
      schema_version: 1,
      center_version: 'mistake-center-v1',
      course_id: COURSE_ID,
      student_id: STUDENT_ID,
      knowledge: {
        knowledge_id: KP_ID,
        title: 'Definicion de funcion',
        excerpt: 'Una funcion asocia cada valor del dominio',
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
          evidence_id: 'evid-1',
          evidence_type: 'transcript',
          language: 'ca',
          confidence: 'high',
          content: 'Introduccio al tema (ca)',
          source: { material_id: MATERIAL_ID, location: 'segment-1' },
          material: {
            material_id: MATERIAL_ID,
            filename: 'tema1.txt',
            material_type: 'text',
            language: 'ca',
          },
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
          target: KP_ID,
          href: '/api/knowledge/' + KP_ID,
          basis: 'the knowledge point this mistake is attached to',
        },
        {
          action: 'VIEW_EVIDENCE',
          target: KP_ID,
          href: '/api/knowledge/' + KP_ID + '/evidence-trace',
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
    },
    overrides
  );
}

/** Task 64: Exercise -> KP -> Evidence -> Material 的完整链路。 */
function groundingChain(overrides) {
  return Object.assign(
    {
      course_id: COURSE_ID,
      knowledge_points: [
        {
          knowledge_id: KP_ID,
          title: 'Definicion de funcion',
          validation_status: 'supported',
          review_status: 'confirmed',
        },
      ],
      unknown_knowledge_point_ids: [],
      evidence: [
        {
          evidence_id: 'evid-1',
          evidence_type: 'transcript',
          language: 'ca',
          confidence: 'high',
          content: 'Introduccio al tema (ca)',
          source: { material_id: MATERIAL_ID, page: 1 },
          material: {
            material_id: MATERIAL_ID,
            filename: 'tema1.txt',
            material_type: 'text',
            language: 'ca',
          },
        },
      ],
      materials: [
        {
          material_id: MATERIAL_ID,
          filename: 'tema1.txt',
          material_type: 'text',
          language: 'ca',
        },
      ],
      unresolved: [],
      complete: true,
      generator_version: 'template-v1',
      template: 'TRUE_FALSE_DEFINITION',
    },
    overrides
  );
}

/** 每个页面一个"空"响应: 用于空状态检查。 */
function emptyRoutes() {
  const table = routes();
  table['/api/dashboard'] = {
    course_id: COURSE_ID,
    version: '0.35.0',
    courses: [course()],
    sessions: [],
    materials: [],
    students: [],
    exercises: [],
    review_pending: [],
    gaps: { gaps: [] },
    knowledge: { count: 0, points: [], summary: {} },
    processing: { by_status: {}, jobs: [] },
  };
  table['/api/knowledge'] = { knowledge_points: [] };
  table['/api/course-knowledge'] = { topic_count: 0, relation_count: 0 };
  table['/api/materials'] = { materials: [] };
  // 空课程表: 材料页的课堂下拉这时只剩"不关联课堂"一项, 必须照常渲染。
  table['/api/sessions'] = { sessions: [] };
  // 时间线在空态下也必须给出明确文案 (而不是空表)。
  table['/api/sessions/' + SESSION_ID + '/timeline'] = { timeline: [] };
  table['/api/reviews'] = { reviews: [] };
  table['/api/students'] = { students: [] };
  table['/api/today'] = today({
    courses_today: [], sessions_today: [], pending_materials: [], pending_review: [],
    study: { available: false, course_id: null, student_id: null, note: 'No learning state yet', items: [], items_total: 0 },
    counts: { courses_today: 0, sessions_today: 0, pending_materials: 0, pending_review: 0, study_items: 0 },
  });
  table['/api/courses/' + COURSE_ID + '/workspace'] = courseWorkspace({
    sessions: [], pending_review: [], pending_review_ids: [], recent_materials: [],
    counts: { sessions: 0, materials: 0, evidence: 0, knowledge_points: 0, pending_review: 0 },
    knowledge: { count: 0, by_validation_status: {}, by_review_status: {} },
    gaps: { gaps: [] },
  });
  // Task 68: 空课程表 —— "No courses yet." 是正常状态, 不是错误页。
  table['/api/my-courses'] = emptyMyCourses();
  table['/api/courses/' + COURSE_ID + '/sessions/' + SESSION_ID + '/workspace'] = sessionWorkspace({
    status: 'PLANNED', status_reason: 'No materials registered for this session.',
    materials: [], processing: { jobs: [] }, transcript: [], ocr: [], documents: [], notes: [], evidence: [],
    knowledge: { count: 0, points: [], knowledge_ids: [] },
    review: { pending: [], pending_count: 0, history: [] },
    learning: { student_id: null, available: false, note: 'No learning state yet', states: [], pending_exercises: [] },
    overview: {
      materials: 0, materials_by_status: {}, evidence: 0,
      evidence_by_group: { transcript: 0, ocr: 0, document: 0, note: 0, other: 0 },
      knowledge_points: 0, knowledge_by_validation_status: {}, knowledge_by_review_status: {},
      pending_review: 0,
      processing_by_status: { QUEUED: 0, RUNNING: 0, SUCCEEDED: 0, FAILED: 0, CANCELLED: 0 },
    },
  });
  // Task 65: 空错题本。"No mistakes yet." 必须是正常状态, 不是错误页。
  table['/api/students/' + STUDENT_ID + '/mistakes'] = mistakeCenter({
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
  return table;
}

/** 大量数据: 用于规模检查。 */
function largeRoutes(kpCount, studentCount, exerciseCount) {
  const table = routes();
  const points = [];
  for (let i = 0; i < kpCount; i += 1) {
    points.push(
      knowledgePoint({ knowledge_id: 'kp-' + i, title: 'Punto ' + i, evidence_refs: ['evid-' + i] })
    );
  }
  const students = [];
  for (let i = 0; i < studentCount; i += 1) {
    students.push({ student_id: 'student-' + i, display_name: 'Alumno ' + i });
  }
  const exercises = [];
  for (let i = 0; i < exerciseCount; i += 1) {
    exercises.push({
      exercise_id: 'exercise-' + i,
      exercise_type: 'fill_blank',
      prompt: 'Enunciado ' + i,
      difficulty: 2,
      answer_format: 'text',
      knowledge_point_ids: ['kp-' + (i % Math.max(kpCount, 1))],
      knowledge_points: [{ knowledge_id: 'kp-' + (i % Math.max(kpCount, 1)), title: 'Punto ' + i }],
      prerequisites: [],
      submitted: false,
      answer_id: null,
      submitted_value: null,
      submitted_at: null,
      evaluation_status: null,
      score: null,
    });
  }
  const base = dashboard();
  base.knowledge.count = kpCount;
  base.knowledge.points = points;
  base.students = students;
  base.exercises = exercises;
  table['/api/dashboard'] = base;
  table['/api/knowledge'] = { knowledge_points: points };
  table['/api/students'] = { students: students };
  // pageExercises 会先取第一个学生再取该学生的练习 —— 所以路由要挂在
  // **第一个**学生身上, 否则脚本会拿到 404 而不是被测的页面。
  table['/api/students/' + students[0].student_id + '/exercises'] = {
    course_id: COURSE_ID,
    student_id: students[0].student_id,
    exercises: exercises,
    total: exerciseCount,
    answered: 0,
    unanswered: exerciseCount,
  };
  return table;
}

// ---------------------------------------------------------------- DOM 桩

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

function makeSandbox(routeTable, lang) {
  const elements = new Map();
  const storage = new Map();
  const fetched = [];
  if (lang) storage.set('ca.lang', lang);

  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  };

  const document = {
    getElementById: getElement,
    querySelector: () => null,
    querySelectorAll: () => [],
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
    addEventListener() {},
    scrollTo() {},
    matchMedia: () => ({ matches: false, addEventListener() {} }),
  };

  async function fetchStub(url) {
    fetched.push(url);
    const pathOnly = String(url).split('?')[0];
    const route = routeTable[pathOnly];
    if (!route) {
      return {
        status: 404,
        async json() {
          return {
            success: false,
            error: { code: 'NOT_FOUND', message: 'no route ' + pathOnly },
          };
        },
      };
    }
    return {
      status: 200,
      async json() {
        return { success: true, data: route };
      },
    };
  }

  const sandbox = {
    window,
    document,
    localStorage: window.localStorage,
    fetch: fetchStub,
    console,
    setTimeout,
    clearTimeout,
    URLSearchParams,
    FormData,
    ApiError: class ApiError extends Error {},
    __elements: elements,
    __fetched: fetched,
    __storage: storage,
  };
  sandbox.globalThis = sandbox;
  return sandbox;
}

async function loadApp(routeTable, lang) {
  const sandbox = makeSandbox(routeTable, lang);
  vm.createContext(sandbox);
  // 按加载顺序逐段执行 —— 等价于浏览器里多个 <script> 共享全局作用域。
  for (const source of WEB_SOURCES) {
    vm.runInContext(source.code, sandbox, { filename: source.name });
  }
  return sandbox;
}

function html(sandbox) {
  return sandbox.document.getElementById('view').innerHTML;
}

// ---------------------------------------------------------------- 页面清单

const PAGES = [
  { name: 'dashboard', args: [] },
  { name: 'today', args: [] },
  { name: 'knowledge', args: [] },
  { name: 'materials', args: [] },
  { name: 'reviews', args: [] },
  { name: 'course', args: [COURSE_ID] },
  { name: 'session', args: [COURSE_ID, SESSION_ID] },
  { name: 'knowledgeDetail', args: [COURSE_ID, KP_ID] },
  { name: 'students', args: [] },
  { name: 'exercises', args: [] },
  { name: 'mistakes', args: [] },
  { name: 'mistakeDetail', args: [COURSE_ID, KP_ID] },
  { name: 'exercise', args: [COURSE_ID, EXERCISE_ID, STUDENT_ID] },
  { name: 'student', args: [COURSE_ID, STUDENT_ID] },
  // Task 66: 今天的学习流程 (入口 + 知识点学习页)
  { name: 'learn', args: [] },
  { name: 'learnKnowledge', args: [COURSE_ID, KP_ID] },
  // Task 67: 考前复习模式
  { name: 'review', args: [] },
  // Task 68: 多课程总览
  { name: 'myCourses', args: [] },
];

function pageByName(name) {
  const found = PAGES.filter((p) => p.name === name)[0];
  if (!found) throw new Error('no page named ' + name);
  return found;
}

async function renderPage(page, table, lang, withSidebar) {
  const sandbox = await loadApp(table, lang);
  // route() 的真实顺序是 loadSidebar() -> pageXxx()。需要课程名解析 (courseLabel)
  // 的场合必须先跑侧边栏 —— 否则测到的是"缓存为空时的兜底", 不是用户看到的界面。
  // 注意顺序: 页面函数一旦渲染完, 再补跑侧边栏也不会重画 #view。
  if (withSidebar) await sandbox.loadSidebar();
  await sandbox['page' + page.name[0].toUpperCase() + page.name.slice(1)](...page.args);
  // Task 64: 单题页的"出题依据链"是异步补上的 (页面先渲染骨架, 再拉链路)。
  // 页面内部发起的那个 promise 没有被 await, 所以审计要显式等一次 ——
  // 否则检查到的只是"加载中…"这个中间态。
  const pending = vm.runInContext('__lastGrounding', sandbox, { filename: 'grab' });
  if (pending && typeof pending.then === 'function') await pending;
  return sandbox;
}

// ---------------------------------------------------------------- 检查框架

const failures = [];
let checks = 0;

function check(name, condition, detail) {
  checks += 1;
  if (!condition) failures.push(name + (detail ? ' — ' + detail : ''));
}

const CJK = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;

function firstCjk(text) {
  const match = CJK.exec(text);
  if (!match) return null;
  const at = match.index;
  return text.slice(Math.max(0, at - 60), at + 60).replace(/\s+/g, ' ');
}

// ---------------------------------------------------------------- 主流程

async function dump(lang) {
  const table = routes();
  const chunks = [];
  for (const page of PAGES) {
    // 带上侧边栏 —— 见 renderPage() 的 withSidebar 参数。
    //
    // 为什么 dump 也要这样: courseLabel() 靠 __courseCache 解析课程名, 缓存为空时
    // 退回 course_id。不跑侧边栏 => dump 出来的是**用户永远看不到**的冷缓存态
    // (课程标题显示 course-1 这种兜底哈希), 手工审查时会误判成"没修好" ——
    // 2026-09-20 核对"侧边栏不显示哈希"时就差点栽在这里。
    const sandbox = await renderPage(page, table, lang, true);
    let body = html(sandbox);
    // #view 只覆盖主视图。Task 64 的依据链写在它自己的容器里,
    // 手工审查时必须一起打印, 否则会误判成"没渲染"。
    for (const id of ['grounding-body', 'generation-result', 'evidence-panel', 'answer-result']) {
      const el = sandbox.__elements.get(id);
      if (el && el.innerHTML) body += '\n--- #' + id + ' ---\n' + el.innerHTML;
    }
    chunks.push('===== ' + page.name + ' =====\n' + body);
  }
  process.stdout.write(chunks.join('\n\n') + '\n');
}

/**
 * 渲染一份**可直接在浏览器打开的 HTML 快照**, 用于改动前后的视觉比对。
 *
 * 与 --dump 的分工:
 *   --dump    打印 #view 的 innerHTML, 供逐字节 diff;
 *   --preview 输出完整 HTML 文件 —— 真实 index.html 骨架 + 内联 styles.css
 *             + 真实渲染出的侧边栏与主视图。它不启动服务、不连数据库、
 *             不执行任何 JS, 只是一张静态快照 (页面顶部有明确标注)。
 *
 * 为什么默认夹具是"dashboard + D1/D4 警告材料": 概览页的知识健康度分组
 * 与材料表的 warning / 零证据提示正是视觉改动的对象。用最坏情况
 * (COMPLETED + 警告码 + 0 证据) 才能同时看到这两处; 顺带把同一份材料喂给
 * 材料页, 便于对比 dashboard 的"独立说明行"写法与材料页的 <br> 内联写法。
 *
 * 这条路径**不参与** audit 的断言, 不影响门禁。
 */
async function preview(lang, outfile) {
  const warn = material({
    processing_status: 'COMPLETED',
    warning: 'NO_TEXT_EXTRACTED',
    evidence_ids: [],
    evidence_count: 0,
  });
  const table = routes();
  table['/api/dashboard'] = Object.assign(dashboard(), { materials: [warn] });
  table['/api/materials'] = { materials: [warn] };

  const sandbox = await renderPage(pageByName('dashboard'), table, lang, true);
  const viewHtml = html(sandbox);
  const sidebarEl = sandbox.__elements.get('course-list');
  const sidebarHtml = sidebarEl ? sidebarEl.innerHTML : '';

  // 真实骨架 (index.html) + 内联 CSS + 真实渲染结果。
  let shell = fs.readFileSync(path.join(ROOT, 'src', 'web', 'index.html'), 'utf8');
  shell = shell.replace('<link rel="stylesheet" href="/styles.css">', '<style>\n' + CSS + '\n</style>');
  // 快照是静态的: 剥掉全部 <script>, 免得在浏览器里再跑一次真实 app。
  shell = shell.replace(/<script src="[^"]*"><\/script>\s*/g, '');
  shell = shell.replace(
    /<ul id="course-list" class="course-list">[\s\S]*?<\/ul>/,
    '<ul id="course-list" class="course-list">' + sidebarHtml + '</ul>'
  );
  shell = shell.replace(
    /<main id="view" class="view" tabindex="-1">[\s\S]*?<\/main>/,
    '<main id="view" class="view" tabindex="-1">' + viewHtml + '</main>'
  );
  // 顶部标注: 这是快照, 不是运行中的应用。
  shell = shell.replace(
    '<body>',
    '<body>\n  <div class="banner" style="background:var(--info-soft);color:var(--info)">' +
    '静态渲染快照（非实时应用）· 页面：dashboard · 语言：' + lang +
    ' · 由 scripts/ui_audit.js --preview 生成</div>'
  );

  if (outfile) {
    fs.writeFileSync(outfile, shell, 'utf8');
    // 用 Buffer.byteLength 而不是 shell.length —— 后者是 JS 字符串长度
    // (UTF-16 码元数), 中文在 UTF-8 下占 3 字节, 两个数会差出几千。
    process.stdout.write('preview written: ' + outfile + ' (' +
      Buffer.byteLength(shell, 'utf8') + ' bytes)\n');
  } else {
    process.stdout.write(shell);
  }
}

async function audit() {
  // ---- 1) 每个页面在三种语言下都能渲染, 且 es/ca 界面文案里没有 CJK ----
  for (const lang of ['zh', 'es', 'ca']) {
    for (const page of PAGES) {
      let out;
      try {
        const sandbox = await renderPage(page, routes(), lang);
        out = html(sandbox);
      } catch (err) {
        check('page ' + page.name + ' renders in ' + lang, false, String(err && err.message));
        continue;
      }
      check('page ' + page.name + ' renders in ' + lang, out.length > 0);
      if (lang !== 'zh') {
        const leaked = firstCjk(out);
        check(
          'page ' + page.name + ' has no untranslated CJK in ' + lang,
          leaked === null,
          leaked ? '…' + leaked + '…' : ''
        );
      }
    }
  }

  // ---- 2) empty state: 每个页面都必须给出明确文案, 而不是空白 ----
  {
    const table = emptyRoutes();
    for (const page of PAGES) {
      if (page.name === 'knowledgeDetail' || page.name === 'session' || page.name === 'exercise') {
        continue; // 这三个页面的"空"由数据缺失表示, 不是列表空
      }
      const sandbox = await renderPage(page, table, 'zh');
      const out = html(sandbox);
      check('empty state on ' + page.name + ' is explicit', out.length > 0);
    }
    // dashboard 空课程: 必须给出**可操作**的指引。
    //
    // 2026-09-21 改判据: 旧版把 curl 请求体贴在页面上, 于是这条断言钉的是
    // "POST /api/courses 这几个字出现过"。现在网页上有真正的创建表单
    // (#/courses 的 courseForm), 空态只负责把人送过去 —— 所以断言改成
    // 两件事同时成立: 入口链接在, 且 curl 教学没有回来。
    // 2026-09-22 (任务书 §3): 概览默认是全部课程聚合页 (数据来自
    // /api/my-courses), 所以空态夹具必须同时清空 my-courses —— 否则测到的
    // 是"两个接口口径不一致"而不是空态本身。
    const noCourse = routes();
    noCourse['/api/dashboard'] = Object.assign(dashboard(), { courses: [] });
    noCourse['/api/my-courses'] = emptyMyCourses();
    const sandbox = await renderPage(pageByName('dashboard'), noCourse, 'zh');
    const out = html(sandbox);
    check('empty course list points at the in-app create form',
      out.includes('href="#/courses"'), out);
    check('empty course list no longer teaches raw HTTP',
      out.indexOf('POST /api/courses') === -1, out);
  }

  // ---- 3) error state: 结构化错误码 + 消息, 且绝不泄露堆栈 ----
  {
    const sandbox = await loadApp(routes(), 'zh');
    // 用 app.js **自己**的 ApiError (顶层 class 声明会遮蔽沙箱里的桩)。
    const ApiError = vm.runInContext('ApiError', sandbox);
    sandbox.renderError(new ApiError({ code: 'NOT_FOUND', message: 'course not found' }));
    const out = html(sandbox);
    check('error view shows the code', out.includes('NOT_FOUND'));
    check('error view shows the message', out.includes('course not found'));
    check('error view offers a way back', out.includes('href="#/"'));
    check(
      'error view never prints a stack trace',
      !/Traceback|at Object\.|File "/.test(out)
    );

    // 路由层的兜底错误页
    const fallback = await loadApp(routes(), 'zh');
    fallback.window.location.hash = '#/definitely-not-a-page';
    await fallback.route();
    const fallbackOut = html(fallback);
    check('unknown route renders a not-found page', fallbackOut.length > 0);
    check(
      'unknown route never renders a stack trace',
      !/Traceback|at Object\.|File "/.test(fallbackOut)
    );
  }

  // ---- 4) loading state: 路由必须先给出"加载中", 而不是继续显示上一页 ----
  {
    const sandbox = await loadApp(routes(), 'zh');
    let seenDuringLoad = null;
    const originalFetch = sandbox.fetch;
    sandbox.fetch = async function (url) {
      if (seenDuringLoad === null) seenDuringLoad = html(sandbox);
      return originalFetch(url);
    };
    await sandbox.route();
    check(
      'router shows a loading state before data arrives',
      seenDuringLoad !== null && seenDuringLoad.length > 0,
      'view was empty while fetching'
    );
  }

  // ---- 5) long text: 超长无空白 token 必须原样输出 (交给 CSS 折行) ----
  {
    const table = routes();
    const longMaterial = material({ filename: LONG_TOKEN + '.mp3' });
    table['/api/materials'] = { materials: [longMaterial] };
    table['/api/dashboard'] = Object.assign(dashboard(), { materials: [longMaterial] });
    const sandbox = await renderPage(pageByName('materials'), table, 'zh');
    const out = html(sandbox);
    check('long unbroken filename is rendered verbatim', out.includes(LONG_TOKEN));
    check(
      'long filename is not truncated by the renderer',
      !out.includes('…mp3') && !out.includes('...mp3')
    );
    // 400 字符的内容哈希也必须原样出现 (溯源链靠它)
    const detail = await renderPage(pageByName('knowledgeDetail'), table, 'zh');
    check('long content hash is rendered verbatim', html(detail).includes(LONG_TOKEN));
    // CSS 必须为长 token 提供折行规则, 否则窄视口会横向溢出
    check('css allows long tokens to wrap', /overflow-wrap:\s*anywhere/.test(CSS));
  }

  // ---- 6) large knowledge base / many students / many exercises ----
  {
    const kpCount = 1000;
    const studentCount = 500;
    const exerciseCount = 5000;
    const table = largeRoutes(kpCount, studentCount, exerciseCount);
    const started = Date.now();
    const knowledge = await renderPage(pageByName('knowledge'), table, 'zh');
    const students = await renderPage(pageByName('students'), table, 'zh');
    const exercises = await renderPage(pageByName('exercises'), table, 'zh');
    const dashboardPage = await renderPage(pageByName('dashboard'), table, 'zh');
    const elapsed = Date.now() - started;

    check('large knowledge base renders every point', (html(knowledge).match(/kp-\d+/g) || []).length >= kpCount);
    check('many students render', (html(students).match(/student-\d+/g) || []).length >= studentCount);
    check(
      'many exercises render',
      (html(exercises).match(/exercise-\d+/g) || []).length >= exerciseCount
    );
    check('dashboard stays bounded with a large course', html(dashboardPage).length > 0);
    check('large pages render in reasonable time', elapsed < 10000, elapsed + 'ms');

    // 知识健康度面板: 3 个语义分组 (结构 / 证据支持度 / 人工审核), 每组有
    // 自己的 .kv-group-label 小标题 + 自带的 <dl>. 旧写法把 9 项塞进同一个
    // <dl>, 视觉上挤成一列无层次的清单。分组语义由 i18n 的 dashboard.health.*
    // 三键承担, 三语必须各自存在 (否则 t() 会原样返回 key, 看起来"有数据",
    // 实际漏译)。
    {
      const sandboxApp = await loadApp(routes(), 'zh');
      const labels = vm.runInContext('I18N', sandboxApp);
      for (const lang of ['zh', 'es', 'ca']) {
        const labelKeys = ['structure', 'validation', 'review'];
        const missing = labelKeys.filter(
          (k) => !labels[lang]['dashboard.health.' + k]
        );
        check('dashboard.health.* present in ' + lang, missing.length === 0, missing.join(','));
      }
      const healthOut = html(dashboardPage);
      const groups = (healthOut.match(/<div class="kv-group">/g) || []).length;
      check('knowledge health panel renders 3 kv-groups',
        groups === 3, 'groups=' + groups);
      // 三组必须包在 .kv-groups 网格容器里 —— 只有容器存在, CSS 的两列
      // 网格才生效; 少了它三组会退回"单列贴着左边" (用户报的那个问题)。
      check('knowledge health groups sit in the .kv-groups grid container',
        /<div class="kv-groups">/.test(healthOut),
        'kv-groups container present: ' + /<div class="kv-groups">/.test(healthOut));
      // 两列网格的 CSS 必须在位 (固定 2 列 + 窄屏 1 列), 否则容器也白搭。
      check('css defines the 2-column .kv-groups grid',
        /\.kv-groups\s*\{[^}]*grid-template-columns:\s*repeat\(2,/.test(CSS));
      check('css collapses .kv-groups to one column on narrow screens',
        /@media\s*\(max-width:\s*560px\)[\s\S]{0,200}\.kv-groups\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/.test(CSS));
      const titleMatches = (healthOut.match(/class="kv-group-label">([^<]+)/g) || [])
        .map((s) => s.replace(/^.*>/, ''));
      check('knowledge health panel has 3 group labels in zh',
        titleMatches.length === 3 && titleMatches.indexOf('结构') >= 0
          && titleMatches.indexOf('证据支持度') >= 0
          && titleMatches.indexOf('人工审核') >= 0,
        titleMatches.join(','));
      // 组名不得与**组内某个字段名**同名: "覆盖"组曾经组名 = 字段名 = "覆盖",
      // zh 渲染出 "覆盖 / 覆盖 12"、es 渲染出 "Cobertura / Cobertura 12" ——
      // 看起来像渲染 bug。该指标已并进"结构"组。
      const groupBlocks = healthOut.match(/<div class="kv-group">[\s\S]*?<\/dl><\/div>/g) || [];
      const dupLabels = groupBlocks.filter((block) => {
        const label = (/<div class="kv-group-label">([^<]*)<\/div>/.exec(block) || [])[1];
        const dts = (block.match(/<dt>[^<]*<\/dt>/g) || [])
          .map((s) => s.replace(/<\/?dt>/g, ''));
        return label && dts.indexOf(label) >= 0;
      });
      check('no group label repeats one of its own field names',
        dupLabels.length === 0, dupLabels.join(' | '));
      // 旧"<dl class="kv">" 整个面板只剩一组, 不能再作为整面板容器存在。
      // 但 .kv 在分组内部仍然作为 dl 样式出现 (3 次 <dl class="kv compact">),
      // 所以这条断言**精确**针对"无 .kv-group 的旧写法": 数 <dl class="kv">
      // (没有 "compact") 必须为 0。
      const oldDl = (healthOut.match(/<dl class="kv">/g) || []).length;
      check('knowledge health panel no longer uses the ungrouped <dl class="kv">',
        oldDl === 0, 'old-style dl count=' + oldDl);
    }

    // 请求数必须与数据量无关 (没有 N+1)
    check(
      'large knowledge page issues a bounded number of requests',
      knowledge.__fetched.length <= 5,
      'fetched=' + knowledge.__fetched.length
    );
    check(
      'large exercise page issues a bounded number of requests',
      exercises.__fetched.length <= 5,
      'fetched=' + exercises.__fetched.length
    );
  }

  // ---- 7) mobile-ish narrow viewport: 断点必须把两栏收成一栏 ----
  {
    check('css declares a narrow-viewport breakpoint', /@media\s*\(max-width:\s*\d+px\)/.test(CSS));
    const narrow = /@media\s*\(max-width:\s*900px\)\s*\{([\s\S]*?)\n\}/.exec(CSS);
    check('narrow breakpoint exists', narrow !== null);
    if (narrow) {
      const body = narrow[1];
      check('narrow breakpoint collapses the shell to one column', /grid-template-columns:\s*minmax\(0,\s*1fr\)/.test(body));
      check('narrow breakpoint unsticks the sidebar', /\.sidebar\s*\{[^}]*position:\s*static/.test(body));
    }
    // 只禁止**固定**宽度 (width: 1400px), 不禁止 max-width —— 后者是
    // 响应式的, 而且正是把内容限制在可读行宽上的手段。
    check('layout is not fixed-width', !/(?<![\w-])width:\s*\d{4,}px/.test(CSS));
    check('tables can scroll horizontally when narrow', /overflow-x:\s*auto/.test(CSS));
  }

  // ---- 8) 三语键集必须完全一致 ----
  {
    const sandbox = await loadApp(routes(), 'zh');
    const tables = vm.runInContext('I18N', sandbox);
    const keysOf = (lang) => Object.keys(tables[lang]).sort().join('|');
    check('zh/es key parity', keysOf('zh') === keysOf('es'));
    check('zh/ca key parity', keysOf('zh') === keysOf('ca'));
    // 每个语言都必须有非空值
    for (const lang of ['zh', 'es', 'ca']) {
      const blanks = Object.keys(tables[lang]).filter((k) => !String(tables[lang][k]).trim());
      check(lang + ' has no blank translation', blanks.length === 0, blanks.join(','));
    }
  }

  // ---- 9) Task 63: 学生今日首页必须如实显示, 且不能出现"掌握度"话术 ----
  {
    const sandbox = await renderPage(pageByName('today'), routes(), 'zh');
    const out = html(sandbox);
    // 63.6 / 63.7: 待审核与练习必须可点击进入既有页面
    check('today links to the review center', out.includes('href="#/reviews"'));
    check('today links to the exercise center', out.includes('href="#/exercises"'));
    // 63.11: 四个核心板块都在
    for (const label of ['今日', '待审核', '练习', '最近评估']) {
      check('today renders section ' + label, out.includes(label));
    }
    // 63.9: Attention 只显示 StudentState 已定义的信号, 并给出依据
    check('today shows the attention basis', out.includes('StudentState state=practicing'));
    // 63.8 / 63.9 禁止项: 不得出现掌握度话术
    const forbidden = /mastery|mastered|proficiency|proficient|you are ready|you've mastered|已掌握|掌握度/i;
    check('today never claims mastery', !forbidden.test(out), forbidden.exec(out) ? forbidden.exec(out)[0] : '');
    // 63.12: All Courses 时每条 task 必须带 course_id
    check('today keeps course_id when showing all courses', out.includes(COURSE_ID));

    // 63.10 empty student: "No learning activity yet." 必须正常出现, 不是崩溃
    const empty = routes();
    empty['/api/student-today'] = Object.assign(studentToday(), {
      has_activity: false,
      note: 'No learning activity yet.',
      study: [],
      learning_paths: [],
      pending_exercises: [],
      recent_evaluations: [],
      attention: [],
      review: { items: [], total: 0, path: '/api/reviews' },
    });
    const emptySandbox = await renderPage(pageByName('today'), empty, 'zh');
    const emptyOut = html(emptySandbox);
    check('empty student shows the no-activity note', emptyOut.includes('No learning activity yet.'));
    check('empty student still renders a page', emptyOut.length > 0);
    check(
      'empty student never renders a stack trace',
      !/Traceback|at Object\.|File "/.test(emptyOut)
    );

    // 63.15: 单课程过滤时不再重复显示课程名 (course_id 已固定)
    const single = routes();
    single['/api/student-today'] = Object.assign(studentToday(), { course_id: COURSE_ID });
    const singleSandbox = await renderPage(pageByName('today'), single, 'zh');
    check('single-course today renders', html(singleSandbox).length > 0);
  }

  // ---- 10) Task 64: 出题面板 + 出题依据链必须如实显示 ----
  {
    const sandbox = await renderPage(pageByName('exercises'), routes(), 'zh');
    const out = html(sandbox);
    // 64.15: UI 必须提供出题入口
    check('exercises page renders the generation panel', out.includes('id="generation-result"'));
    check('exercises page renders the generate button', out.includes('id="generate-batch"'));
    check('generate button carries the course id', out.includes('data-course="' + COURSE_ID + '"'));
    // 64.10 / 64.12: 不可出题的知识点必须**显示出来并标明原因**, 不能藏
    check('generation panel lists blocked knowledge points', out.includes('kp-unverified-1'));
    check('blocked knowledge point keeps its status', out.includes('unverified'));
    // 64.15 禁止项: 出题面板不得声称掌握度或保证考什么
    const genForbidden = /mastery|mastered|掌握度|已掌握|this will be on the exam|一定会考/i;
    check('generation panel never claims mastery or exam certainty',
      !genForbidden.test(out), genForbidden.exec(out) ? genForbidden.exec(out)[0] : '');
    check('generation panel never renders a stack trace',
      !/Traceback|at Object\.|File "/.test(out));

    // 64.9: 出题依据链必须是 Exercise -> KP -> Evidence -> Material
    const detail = await renderPage(pageByName('exercise'), routes(), 'zh');
    const detailOut = html(detail);
    const groundingEl = detail.__elements.get('grounding-body');
    const groundOut = groundingEl ? groundingEl.innerHTML : '';
    check('exercise detail renders the grounding card', detailOut.includes('id="grounding-body"'));
    check('exercise detail links the knowledge point', detailOut.includes(KP_ID));
    check('grounding chain resolves the knowledge point', groundOut.includes('Definicion de funcion'));
    check('grounding chain resolves the evidence', groundOut.includes('Introduccio al tema (ca)'));
    check('exercise detail shows the source material', groundOut.includes('tema1.txt'));

    // 64.16: 详情页在提交前绝不能出现正确答案
    check('answer key is withheld before submit',
      !detailOut.includes('accepted_answers') || !detailOut.includes('uno'),
      'possible answer leak');
    check('exercise detail explicitly says it is not fact verification',
      detailOut.includes('不是对知识的核验'));

    // 三语下依据链也必须能渲染 (i18n 覆盖)
    for (const lang of ['es', 'ca']) {
      const localized = await renderPage(pageByName('exercise'), routes(), lang);
      const localizedEl = localized.__elements.get('grounding-body');
      const localizedOut = localizedEl ? localizedEl.innerHTML : '';
      check('grounding chain renders in ' + lang, localizedOut.length > 0);
      check('grounding chain has no untranslated CJK in ' + lang,
        !/[\u4e00-\u9fff]/.test(localizedOut), 'CJK leaked');
    }
  }

  // ---- 11) Task 65: 错题中心必须如实显示, 且不得推断掌握度 ----
  {
    const sandbox = await renderPage(pageByName('mistakes'), routes(), 'zh');
    const out = html(sandbox);

    // 65.2: 错题必须带 Exercise / Question / Answer / Evaluation / Knowledge / Course / Topic
    check('mistake center renders the exercise prompt', out.includes('El grado de la funcion es ___'));
    check('mistake center renders the submitted answer', out.includes('dos'));
    check('mistake center renders the evaluation status', out.includes('incorrect'));
    check('mistake center renders the evaluation id', out.includes('evaluation-1'));
    check('mistake center renders the answer id', out.includes('answer-1'));
    check('mistake center links to the exercise',
      out.includes('#/courses/' + COURSE_ID + '/exercises/' + EXERCISE_ID));
    check('mistake center links to the knowledge point',
      out.includes('#/courses/' + COURSE_ID + '/knowledge/' + KP_ID));

    // 65.3: 按知识点分组必须给出错答次数
    check('mistake center shows the incorrect-attempt count', out.includes('错答次数'));
    check('mistake center offers the topic grouping toggle',
      out.includes('id="mistake-group-toggle"'));
    check('mistake center toggle carries a course id',
      out.includes('data-course="' + COURSE_ID + '"'));

    // 65.6: 错了 2 次但学生状态未到 NEEDS_*, 就**不能**判定薄弱
    check('mistake center does not infer weakness from wrong counts',
      out.includes('当前没有标记为薄弱的知识点'));

    // 65.11: 空态
    const empty = await renderPage(pageByName('mistakes'), emptyRoutes(), 'zh');
    const emptyOut = html(empty);
    check('empty mistake center shows the empty note', emptyOut.includes('暂无学习活动'));
    check('empty mistake center is not an error page', !emptyOut.includes('INTERNAL_ERROR'));

    // 65.9: 详情 = 错题 -> 为什么错 -> 重新学习依据
    const detail = await renderPage(pageByName('mistakeDetail'), routes(), 'zh');
    const detailOut = html(detail);
    check('mistake detail renders the why-incorrect block', detailOut.includes('为什么错'));
    check('mistake detail shows the evaluator that produced the verdict', detailOut.includes('exact-v1'));
    check('mistake detail shows the evidence', detailOut.includes('Introduccio al tema (ca)'));
    check('mistake detail shows the source material', detailOut.includes('tema1.txt'));
    check('mistake detail states it is not a judgement on the knowledge',
      detailOut.includes('不是对知识本身的判定'));
    // 65.8: 建议动作必须带依据
    check('mistake detail renders suggested actions', detailOut.includes('复习该知识点'));
    check('mistake detail states the weakness basis', detailOut.includes('学生状态'));
    // 65.10: 再练一次复用既有练习, 不生成新题
    check('mistake detail reuses an existing exercise', detailOut.includes('曾答错'));

    // 禁止项: 不能说"已掌握"、不能出现 traceback
    const mkForbidden = /mastery|mastered|掌握度|已掌握|proficiency|这一题一定考/i;
    check('mistake center never claims mastery', !mkForbidden.test(out + detailOut),
      mkForbidden.exec(out + detailOut) ? mkForbidden.exec(out + detailOut)[0] : '');
    check('mistake center never renders a stack trace',
      !/Traceback|at Object\.|File "/.test(out + detailOut));
    check('mistake detail never renders a stack trace',
      !/Traceback|at Object\.|File "/.test(detailOut));

    // i18n: es/ca 下错题中心不能漏出中文
    for (const lang of ['es', 'ca']) {
      const localized = await renderPage(pageByName('mistakes'), routes(), lang);
      const localizedOut = html(localized);
      check('mistake center renders in ' + lang, localizedOut.length > 0);
      check('mistake center has no untranslated CJK in ' + lang,
        !/[\u4e00-\u9fff]/.test(localizedOut), 'CJK leaked');

      const localizedDetail = await renderPage(pageByName('mistakeDetail'), routes(), lang);
      const localizedDetailOut = html(localizedDetail);
      check('mistake detail renders in ' + lang, localizedDetailOut.length > 0);
      check('mistake detail has no untranslated CJK in ' + lang,
        !/[\u4e00-\u9fff]/.test(localizedDetailOut), 'CJK leaked');
    }
  }

  // ---- Task 67: 考前复习模式 (67.4 冲突 / 67.5 两轴 / 67.6 禁止预测) ----
  {
    const sandbox = await renderPage(pageByName('review'), routes(), 'zh');
    const out = html(sandbox);
    check('review page renders', out.includes('考前复习'));

    // 67.6 No Prediction Contract —— 六条 spec 点名的中文禁止语, 一条都不能有
    const predicted = ['最可能考', '考试概率', '预测题', '押题', '考点概率', '预测分数'];
    for (const phrase of predicted) {
      check('review page never says ' + phrase, !out.includes(phrase), phrase);
    }
    // 英文侧的禁止词 (前端若引入英文文案也要挡住)
    const englishPredicted = ['most likely exam', 'exam probability', 'predicted question',
      'will be on the exam', 'pass probability'];
    for (const phrase of englishPredicted) {
      check('review page never says "' + phrase + '"',
        !out.toLowerCase().includes(phrase), phrase);
    }
    check('review page has no "mastery" claim',
      !/mastery|mastered|已掌握|掌握度/i.test(out));

    // 67.5 两条真相轴必须**各自**出现在表头, 而不是合成一列
    check('review page lists the validation axis', out.includes('证据状态'));
    check('review page lists the review axis', out.includes('人工审核'));
    check('review page explains the two axes are independent', out.includes('这两条是独立的'));

    // 三个桶都要渲染
    check('review page shows the blocked bucket', out.includes('需先解决'));
    check('review page shows the attention bucket', out.includes('需先确认'));
    check('review page shows the ready bucket', out.includes('可复习'));

    // 67.4 冲突两侧并列, 且明确不预判
    check('review page renders the conflict', out.includes('conflict-1'));
    check('review page shows both conflict sides',
      out.includes('Evidence A') && out.includes('Evidence B'));
    check('review page states it does not pick a side', out.includes('不预判'));
    check('review page never says a conflict was auto-resolved',
      !out.includes('自动解决'));

    // 阻塞原因与注意原因都是**动态 key**, 漏一条就是漏翻译
    check('review page explains the block reason', out.includes('存在未解决冲突'));
    check('review page explains the attention reason', out.includes('还没有人确认过'));

    check('review page states its ordering basis', out.includes('排序依据'));
    check('review page never renders a stack trace',
      !/Traceback|at Object\.|File "/.test(out));

    // 空状态: 有课程有学生但没有知识点
    {
      const table = routes();
      table['/api/students/' + STUDENT_ID + '/review-set'] = reviewSetEmpty();
      const emptySandbox = await renderPage(pageByName('review'), table, 'zh');
      const emptyOut = html(emptySandbox);
      check('empty review set renders the empty note',
        emptyOut.includes('暂时没有可复习的内容'));
      check('empty review set still declares it is not a predictor',
        emptyOut.includes('这不是考试预测'));
      check('empty review set has no stack trace',
        !/Traceback|at Object\./.test(emptyOut));
    }

    // i18n: es/ca 下复习页不能漏出中文
    for (const lang of ['es', 'ca']) {
      const localized = await renderPage(pageByName('review'), routes(), lang);
      const localizedOut = html(localized);
      check('review page renders in ' + lang, localizedOut.length > 0);
      check('review page has no untranslated CJK in ' + lang,
        !/[\u4e00-\u9fff]/.test(localizedOut), 'CJK leaked: ' + firstCjk(localizedOut));
    }
  }

  // ---- Task 68: 多课程总览 (严格隔离 / 无排序意图 / 两轴分开) ----
  {
    const sandbox = await renderPage(pageByName('myCourses'), routes(), 'zh');
    const out = html(sandbox);
    check('my courses page renders', out.length > 0);
    check('my courses page lists every course',
      out.includes('Algebra Lineal') && out.includes('Calculo'));
    check('my courses page links to each course',
      out.includes('#/courses/' + COURSE_ID) && out.includes('#/courses/course-2'));
    // 68: 当前课程必须被标出来 —— 否则用户不知道自己在看哪门课
    check('my courses page marks the current course', out.includes('当前课程'));
    // 68: 两条真相轴必须各自出现, 不合成一列
    check('my courses page lists the validation axis', out.includes('证据校验轴'));
    check('my courses page lists the review axis', out.includes('人工审核轴'));
    check('my courses page explains the axes are independent', out.includes('不相加'));
    // 两条轴的取值域不同; 共用了前缀就会渲染出 val.confirmed 这种原始 key。
    // t() 找不到就原样返回, 页面看起来"有数据", 实际上是漏译 —— 没有异常
    // 可抓, 只能显式检查有没有 key 泄漏出来。
    check('my courses page never leaks a raw i18n key',
      !/>(?:val|rev|mc)\.[a-z_]+/.test(out), />(?:val|rev|mc)\.[a-z_]+/.exec(out) || '');
    check('my courses page renders the review axis labels', out.includes('人工已确认'));
    // 68: 并不要求 knowledge_id 全局唯一 —— 相同内容是合法的
    check('my courses page explains shared knowledge ids', out.includes('knowledge_id'));

    // 禁止项一: 不得出现排序 / 优先级话术 (那需要本产品没有的事实)
    const ranking = [
      'priority', 'recommended', 'suggested order', 'most important',
      '优先级', '推荐顺序', '更重要', '先学这门',
      'orden recomendado', 'más importante', 'ordre recomanat', 'més important',
    ];
    for (const term of ranking) {
      check('my courses page never says "' + term + '"',
        !out.toLowerCase().includes(term.toLowerCase()), term);
    }
    // 禁止项二: 不得声称掌握度 / 考试预测
    const forbidden = /mastery|mastered|已掌握|掌握度|最可能考|考试概率|预测分数/i;
    check('my courses page never claims mastery or predicts exams',
      !forbidden.test(out), forbidden.exec(out) ? forbidden.exec(out)[0] : '');
    check('my courses page never renders a stack trace',
      !/Traceback|at Object\.|File "/.test(out));

    // 空状态: 一门课都没有
    {
      const table = routes();
      table['/api/my-courses'] = emptyMyCourses();
      const emptySandbox = await renderPage(pageByName('myCourses'), table, 'zh');
      const emptyOut = html(emptySandbox);
      check('empty my courses shows the empty note', emptyOut.includes('还没有任何课程'));
      check('empty my courses is not an error page', !emptyOut.includes('INTERNAL_ERROR'));
      check('empty my courses has no stack trace', !/Traceback|at Object\./.test(emptyOut));
    }

    // i18n: es/ca 下不能漏出中文
    for (const lang of ['es', 'ca']) {
      const localized = await renderPage(pageByName('myCourses'), routes(), lang);
      const localizedOut = html(localized);
      check('my courses page renders in ' + lang, localizedOut.length > 0);
      check('my courses page has no untranslated CJK in ' + lang,
        !/[\u4e00-\u9fff]/.test(localizedOut), 'CJK leaked: ' + firstCjk(localizedOut));
    }

    // 顶栏课程切换器: 必须存在, 且切换后写入 localStorage (重启后一致)
    {
      const table = routes();
      const switchSandbox = await loadApp(table, 'zh');
      await switchSandbox.loadSidebar();
      const picker = switchSandbox.__elements.get('course-switch');
      check('course switcher exists in the chrome', picker !== undefined);
      if (picker) {
        check('course switcher lists the courses',
          picker.innerHTML.includes(COURSE_ID), picker.innerHTML.slice(0, 120));
        check('course switcher marks the selected course',
          picker.innerHTML.includes('selected'));
      }
    }
  }

  // ---- guia docent 卡片 (课程页, metadata.guia_* 驱动) ----------------
  //
  // 数据流: 导入脚本把结构化 guia docent 写进课程 metadata (``guia_*`` 键),
  // course_workspace 原样透传, 前端只渲染不改写。守卫四件事:
  //   1. 有 guia_* 键时卡片渲染, 原文逐字出现 (加泰语原文不加翻译不改写);
  //   2. 来自 API 的内容必须过 esc() —— 用含 HTML 的夹具值证明不会注入;
  //   3. es/ca 界面语言下小节标题走 guia.* 词条, 不漏中文 (内容是加泰语,
  //      本来就无 CJK —— 出现 CJK 只可能是界面标题漏译);
  //   4. 没有 guia_* 键的课程页完全不渲染这张卡片 (其余 4 门课零影响)。
  {
    const guiaMetadata = {
      teacher: 'Prof. Ana', semester: '2026-2',
      guia_course_code: '106934',
      guia_credits: '6',
      guia_academic_year: '2026/2027',
      guia_degree: 'Gestió de Ciutats Intel·ligents i Sostenibles',
      guia_degree_type: 'FB',
      guia_year: '2',
      guia_contact_name: 'Marc Castello Bueno',
      guia_contact_email: 'marc.castello.bueno@uab.cat',
      guia_teaching_team: ['Miquel Àngel Vargas Garcia', 'Magda Pla Montferrer'],
      guia_prerequisites: 'No hi ha prerequisits vinculats a aquesta assignatura.',
      guia_objectives: ['Objectiu 1', 'Objectiu 2'],
      guia_learning_outcomes: [
        { code: 'CM09', text: 'Relacionar els coneixements i les habilitats en geomàtica.' },
      ],
      guia_syllabus: [
        { title: 'Bloc 1. Introducció a la cartografia', items: ['El mapa: elements bàsics'] },
        { title: 'Bloc 2. Projeccions cartogràfiques', items: ['La projecció UTM'] },
      ],
      guia_teaching_hours: [
        { title: 'Classes magistrals', hours: '20', ects: '0,8' },
      ],
      guia_assessment_items: [
        { title: 'Exàmens teòrics i pràctics', weight: '40' },
        { title: 'Treball final', weight: '30' },
      ],
      guia_assessment_items_detail: ['Treball final (30%): mapa temàtic urbà'],
      guia_pass_requirements: 'La part teòrica i pràctica s\'ha d\'aprovar per separat.',
      guia_recovery: 'Es podrà recuperar els exàmens teòrics i pràctics.',
      guia_ai_policy: 'Es permet l\'ús de la IA amb transparència.',
      guia_software: 'MiraMon <img src=x onerror=alert(1)>',  // 注入载荷: 必须被 esc()
      guia_groups: [
        { kind: 'TE', group: '61', language: 'Català', semester: 'primer quadrimestre', shift: 'tarda' },
        { kind: 'PLAB', group: '611', language: 'Català', semester: 'primer quadrimestre', shift: 'tarda' },
      ],
      guia_source: 'guia docent PDF (extret 2026-09-22)',
    };

    const guiaRoutes = () => Object.assign({}, routes(), {});
    const table = guiaRoutes();
    table['/api/courses/' + COURSE_ID + '/workspace'] =
      courseWorkspace({ metadata: guiaMetadata });

    const zhSandbox = await renderPage(pageByName('course'), table, 'zh');
    const out = html(zhSandbox);
    check('guia docent card renders on the course page', out.includes('id="guia-docent"'));
    // 原文逐字: 带重音的加泰语原文必须原样出现在 DOM 里 (esc() 不改写非 ASCII)。
    check('guia docent keeps accented Catalan text verbatim',
      out.includes('Introducció a la cartografia') &&
      out.includes('Gestió de Ciutats Intel·ligents i Sostenibles'));
    check('guia docent shows the contact name verbatim',
      out.includes('Marc Castello Bueno'));
    check('guia docent shows syllabus blocks and items',
      out.includes('Bloc 1. Introducció a la cartografia') && out.includes('La projecció UTM'));
    check('guia docent shows the assessment weights',
      out.includes('Exàmens teòrics i pràctics') && out.includes('40'));
    check('guia docent shows the teaching groups table',
      out.includes('PLAB') && out.includes('primer quadrimestre'));
    check('guia docent escapes HTML from metadata (no raw tag)',
      !out.includes('<img src=x'), 'raw tag leaked into DOM');
    check('guia docent escapes HTML from metadata (entity present)',
      out.includes('&lt;img src=x'));
    check('guia docent card carries the source line',
      out.includes('guia docent PDF (extret 2026-09-22)'));

    // 页头精简信息: 标签式字段行替代教师/学期/课程语言三行 + 「查看详情」按钮。
    check('course head shows the guia brief field rows',
      out.includes('guia-brief') && out.includes('6 ECTS'));
    check('guia brief lists contact, team, year and degree as labeled fields',
      out.includes('Marc Castello Bueno') &&
      out.includes('Miquel Àngel Vargas Garcia') &&
      out.includes('2026/2027') &&
      out.includes('Gestió de Ciutats Intel·ligents i Sostenibles'));
    check('guia brief renders the mailto link as a real link (no double-escape)',
      out.includes('href="mailto:marc.castello.bueno@uab.cat"') &&
      !out.includes('&lt;a href='));
    check('guia brief has the open-details button',
      out.includes('data-action="open-guia"'));
    check('course head no longer renders the teacher/semester kv rows when guia exists',
      !/<dt>Profesor\/a<\/dt>/.test(out) && !/<dt>Professor\/a<\/dt>/.test(out) &&
      !/<dt>教师<\/dt><dd>—<\/dd>/.test(out));

    // 弹窗: 默认隐藏 (hidden 类), 全文在 .modal-body 里。
    // DOM 桩的 querySelector 恒返回 null (固化边界, 见 makeSandbox), 所以
    // 接线检查走静态源码断言 —— 与 session_id 接线检查 (本文件末段) 同一模式。
    const coursesSource =
      WEB_SOURCES.filter((s) => s.name === 'views/courses.js')[0].code;
    check('guia modal is hidden by default (hidden class, no open state)',
      out.includes('class="modal-mask hidden"') &&
      !out.includes('class="modal-mask"'));
    check('guia modal carries dialog semantics',
      out.includes('role="dialog"') && out.includes('aria-modal="true"'));
    // 全文 (大纲小节) 必须在 modal-body 开标记之后、modal 收尾之前。
    check('guia full text lives inside the modal body',
      coursesSource.indexOf('"modal-body"') >= 0 &&
      coursesSource.indexOf('"modal-body"') <
      coursesSource.indexOf("guiaSection(t('guia.syllabus')") &&
      coursesSource.indexOf("guiaSection(t('guia.syllabus')") <
      coursesSource.indexOf('guia_source'));
    check('wireGuiaBrief wires open, close, mask click and Escape',
      coursesSource.includes("modal.classList.remove('hidden')") &&
      coursesSource.includes("modal.classList.add('hidden')") &&
      coursesSource.includes("event.target === modal") &&
      coursesSource.includes("'Escape'") &&
      coursesSource.includes('wireGuiaBrief();'));
    // CSS 层守卫 (真实回归: .hidden 写在 .modal-mask 之前, 同优先级被
    // display: flex 覆盖, 弹窗关不上) —— .hidden 的 display:none 必须
    // 出现在 .modal-mask 的 display:flex 之后。
    const idxHiddenRule = CSS.indexOf('.hidden { display: none; }');
    const idxMaskRule = CSS.indexOf('.modal-mask {');
    check('styles.css: .hidden rule comes after .modal-mask (must win the cascade)',
      idxHiddenRule >= 0 && idxMaskRule >= 0 && idxHiddenRule > idxMaskRule);
    // 回归守卫: syllabus 嵌套条目必须渲染成真 <li> (旧代码把拼好的 <ul>
    // 塞进 guiaList 被整体 esc 成可见文本 —— 双重转义, 截图实测发现)。
    // 桩 DOM 不可靠, 这里直接检查渲染输出里的真实标记。
    check('guia syllabus renders nested items as real <li> (no double-escape)',
      out.includes('<li>Bloc 1. Introducció a la cartografia<ul>') &&
      out.includes('<li>La projecció UTM</li>') &&
      !out.includes('&lt;ul class="guia-list"'));
    // 布局守卫: 弹窗顶部 facts 网格的值列必须可收缩 (minmax(0, 1fr)),
    // 长学位名才不会把同行字段挤坏; 旧的 repeat(4, max-content) 固定列
    // 会让 5 对 dt/dd 在 5 轨道里错位流动。
    check('styles.css: .guia-facts value columns are shrinkable (minmax(0,1fr))',
      CSS.includes('.guia-facts { grid-template-columns: auto minmax(0, 1fr) auto minmax(0, 1fr);') &&
      !CSS.includes('repeat(4, max-content)'));

    // es/ca: 小节标题走 guia.* 词条; 内容是加泰语原文 (无 CJK),
    // 所以整页 CJK 检查 = 界面标题漏译检测。
    for (const lang of ['es', 'ca']) {
      const localized = await renderPage(pageByName('course'), table, lang);
      const localizedOut = html(localized);
      check('guia docent card renders in ' + lang,
        localizedOut.includes('id="guia-docent"'));
      check('guia docent localized title in ' + lang,
        localizedOut.includes(lang === 'es' ? 'Guia docente' : 'Guia docent</h2>'));
      check('guia docent has no untranslated CJK in ' + lang,
        !/[\u4e00-\u9fff]/.test(localizedOut),
        'CJK leaked: ' + firstCjk(localizedOut));
    }

    // 没有 guia_* 键: 其余课程页必须完全不渲染这张卡片, 且页头保持原三行。
    const plain = await renderPage(pageByName('course'), routes(), 'zh');
    check('course page without guia_* keys renders no guia card',
      !html(plain).includes('id="guia-docent"'));
    check('course page without guia_* keys keeps the teacher/semester rows',
      /<dt>教师<\/dt><dd>/.test(html(plain)));
  }

  // ---- 课程页: 课堂列表 = 月份切换 + 同日合并 + 紧凑行 (2026-09-22 第二轮) ----
  //
  // 第一轮把 8 列大表格改成"日期分组 + 一节一张卡": 层级清楚了, 但一学期 28 节课
  // 仍旧一次性铺开成一屏多高的长列表, 用户要一直滚才找得到 12 月的课。这一轮加
  // 月份切换 + 同一天合并成一张卡 + 每节课两行。这一节守住新契约:
  //
  //   1. 一屏只画**一个月**: 切换条恰好一个 aria-current, 且月份行说的就是它;
  //   2. 切换月份真的换内容 (走 actionSessionMonth 这个真实入口), 空月份给明确
  //      文案而不是白页;
  //   3. 同一天的课合并进一张卡 (行与行只有分隔线), 整行可点且只命中本行;
  //   4. 每节课两行: 时间 + 类型 / 教师 · 教室 + 状态, 「整堂处理」在最右;
  //   5. 标题里的技术占位符 (图中未显示) **绝不**渲染 —— 夹具故意喂未迁移的旧
  //      数据, 验的是渲染侧防线 (数据源已另行清洗);
  //   6. 计数非 0 才画 meta 行;
  //   7. es/ca 三语与 CSS 接线 (切换条高亮 / 拉伸锚点 / 按钮抬层 / 窄屏换行)。
  //
  // 默认落在哪个月随环境时钟变 (夹具跨 9 / 10 / 12 三个月), 所以"默认"只断言
  // **不变量**, 确定性的内容一律先显式切到某个月再断言 —— 否则这份审计会在某个
  // 真实月份变红, 让人以为是产品坏了。
  {
    const cardSession = (over) => Object.assign({
      session_id: 'session-x', course_id: COURSE_ID, session_number: 1,
      date: '2026-09-09', title: '', status: 'PLANNED',
      counts: { materials: 0, knowledge_points: 0, pending_review: 0 },
    }, over);
    const table = routes();
    table['/api/courses/' + COURSE_ID + '/workspace'] = courseWorkspace({
      counts: { sessions: 6, materials: 2, evidence: 4, knowledge_points: 8, pending_review: 3 },
      sessions: [
        cardSession({
          session_id: SESSION_ID,
          title: '15:00–17:00 Teoria | 图中未显示 | Aula Q1/1007',
        }),
        cardSession({
          session_id: 'session-b',
          title: '17:00–19:00 Pràctiques de Laboratori | Aula Q1/0007',
        }),
        cardSession({
          session_id: 'session-c', date: '2026-09-16', status: 'REVIEW_REQUIRED',
          title: '15:00–17:00 Teoria | Dario Cottava | Aula Q2/1009',
          counts: { materials: 2, knowledge_points: 8, pending_review: 3 },
        }),
        cardSession({ session_id: 'session-d', date: '2026-09-16', title: '' }),
        // 10 月与 12 月: 让切换条跨月, 也让 11 月落在**范围内但没有课** (空月份
        // 必须给明确文案, 不能白页)。
        cardSession({
          session_id: 'session-e', date: '2026-10-21',
          title: '15:00–17:00 Teoria | Dario Cottava | Aula Q2/1009',
        }),
        cardSession({
          session_id: 'session-f', date: '2026-12-02',
          title: '15:00–17:00 Teoria | Dario Cottava | Aula Q2/1009',
        }),
      ],
    });
    // 与真实 route() 同样的顺序: 先 pageCourse(); 之后月份切换走真实的 action
    // (data-action 委托把它接到 actionSessionMonth, 这里直接调同一个函数)。
    const sandbox = await loadApp(table, 'zh');
    sandbox.window.location.hash = '#/courses/' + COURSE_ID;
    await sandbox.pageCourse(COURSE_ID);
    // 标记用 `session-summary">` —— class 属性里它前面还有 small/muted,
    // 搜 `class="session-summary"` 会匹配不到 (第一版就是这么踩的)。
    const regionOf = (text) => {
      const at = text.indexOf('session-summary">');
      const to = text.indexOf('id="session-form"');
      return at >= 0 && to > at ? text.slice(at, to) : '';
    };
    const rows = (area) => area.match(/<div class="session-row">/g) || [];
    const currentTab = (area) => {
      const on = /<button[^>]*aria-current="true"[^>]*>([^<]*)</.exec(area);
      return on ? on[1] : '';
    };
    const currentLabel = (area) => {
      const on = /<button[^>]*aria-current="true"[^>]*title="([^"]*)"/.exec(area);
      return on ? on[1] : '';
    };
    const monthNote = (area) => {
      const note = /session-month-note">([^<]*)</.exec(area);
      return note ? note[1] : '';
    };
    const zh = html(sandbox);
    const region = regionOf(zh);

    check('sessions card renders the summary line', !!region);
    check('summary counts sessions and spans the date range',
      region.includes('6 节课堂 · 2026-09-09 → 2026-12-02'), region.slice(0, 160));
    // —— 默认视图: 只画一个月, 且切换条与月份行互相印证 (与运行时钟无关) ——
    const tabs = region.match(/<button type="button" class="session-month"/g) || [];
    check('the month strip lists every month of the schedule range (gap included)',
      tabs.length === 4, 'tabs=' + tabs.length);
    check('month buttons show short labels with a full-label tooltip',
      region.includes('title="2026年9月">9月</button>')
        && region.includes('title="2026年12月">12月</button>'), region.slice(0, 240));
    check('exactly one month is highlighted',
      (region.match(/aria-current="true"/g) || []).length === 1);
    // 每个按钮都必须带全 course + month: 委托处理器只从 DOM 属性取值 ——
    // 少一个属性, 点下去就是"什么都没发生"(不报错, 也不切月份)。
    const monthButtons = region.match(/<button type="button" class="session-month"[^>]*>/g) || [];
    check('every month button carries its course and month',
      monthButtons.length === 4
        && monthButtons.every((button) => button.includes('data-course="' + COURSE_ID + '"')
          && /data-month="\d{4}-\d{2}"/.test(button)), monthButtons.join(' '));
    const defaultTab = currentTab(region);
    const defaultLabel = currentLabel(region);
    check('the highlighted month is one of the listed months',
      ['9月', '10月', '12月'].indexOf(defaultTab) >= 0, 'tab=' + defaultTab);
    check('the month line names the highlighted month',
      monthNote(region).indexOf(defaultLabel) === 0, monthNote(region));
    check('the month line counts exactly the rows that are drawn',
      (/(\d+)[^\d]*$/.exec(monthNote(region)) || [])[1] === String(rows(region).length),
      monthNote(region) + ' | rows=' + rows(region).length);
    check('the whole semester is not on screen at once',
      rows(region).length < 6, 'rows=' + rows(region).length);
    check('the sessions card contains no table anymore', region.indexOf('<table') === -1);
    check('the technical placeholder never renders',
      zh.indexOf('图中未显示') === -1, zh.slice(zh.indexOf('图中未显示') - 40, 80));

    // —— 切到 9 月: 同一天合并 / 每节两行 / 计数 / 按钮 都在确定的内容上断言 ——
    await sandbox.actionSessionMonth(COURSE_ID, '2026-09');
    const sep = regionOf(html(sandbox));
    const rowOf = (area) => area.split('<div class="session-row">').slice(1);
    const sepRows = rowOf(sep);
    check('switching the month re-renders the list',
      sepRows.length === 4, 'rows=' + sepRows.length);
    check('the month line follows the switch',
      monthNote(sep).indexOf('2026年9月') === 0, monthNote(sep));
    check('the highlight follows the switch',
      currentTab(sep) === '9月' && currentLabel(sep) === '2026年9月',
      currentTab(sep) + ' | ' + currentLabel(sep));
    const days = sep.match(/<section class="session-day">/g) || [];
    check('September renders as 2 date groups', days.length === 2, 'days=' + days.length);
    check('day groups are headed MM/DD + weekday',
      sep.includes('09/09 · 周三') && sep.includes('09/16 · 周三'));
    const dayChunks = sep.split('<section class="session-day">').slice(1);
    const rowsPerDay = dayChunks.map((chunk) => rowOf(chunk).length);
    check('both sessions of one day share a single card',
      rowsPerDay.join(',') === '2,2', 'per day=' + rowsPerDay.join(','));
    check('one day is one card, not one card per session',
      (sep.match(/<div class="session-day-card">/g) || []).length === 2,
      'cards=' + (sep.match(/<div class="session-day-card">/g) || []).length);
    check('a row is exactly two lines of text',
      !!sepRows[0] && sepRows[0].includes('session-row-top')
        && sepRows[0].includes('session-row-sub'),
      sepRows[0] ? sepRows[0].slice(0, 160) : 'no row');
    check('the row keeps its time, kind, room and teacher',
      !!sepRows[0] && sepRows[0].includes('15:00–17:00')
        && sepRows[0].includes('session-kind') && sepRows[0].includes('Aula Q1/1007')
        && sep.includes('Dario Cottava'), sepRows[0] ? sepRows[0].slice(0, 200) : 'no row');
    check('a two-segment title still shows the room', sep.includes('Aula Q1/0007'));
    check('kind badges render once per typed session',
      (sep.match(/session-kind/g) || []).length === 3,
      'kind badges=' + (sep.match(/session-kind/g) || []).length);
    check('empty titles fall back to the untitled-session copy',
      sep.includes('session-title">未命名课堂<'));
    check('zero counts render no metadata line',
      !!sepRows[0] && sepRows[0].indexOf('session-row-meta') === -1);
    check('nonzero counts render one metadata line',
      !!sepRows[2] && sepRows[2].includes('材料 2 · 知识点 8 · 待审核 3'),
      sepRows[2] ? sepRows[2].slice(0, 200) : 'no row');
    check('the process-session button survives on every row',
      (sep.match(/data-action="process-session"/g) || []).length === 4,
      'buttons=' + (sep.match(/data-action="process-session"/g) || []).length);
    check('rows link into the session detail page',
      sep.includes('href="#/courses/' + COURSE_ID + '/sessions/' + SESSION_ID + '"'));
    check('the session status pill survives', sep.includes('尚未添加材料'));

    // —— 切到 12 月: 只剩那个月, 其余月份的课不再画 (换一段, 不是丢数据) ——
    await sandbox.actionSessionMonth(COURSE_ID, '2026-12');
    const dec = regionOf(html(sandbox));
    check('switching to December shows only December',
      rowOf(dec).length === 1 && dec.includes('12/02 · 周三')
        && dec.indexOf('09/09 · 周三') === -1,
      'rows=' + rowOf(dec).length);
    check('the December month line names December',
      monthNote(dec).indexOf('2026年12月') === 0, monthNote(dec));

    // —— 切到 11 月 (在范围内但确实没有课): 明确文案, 不是白页 ——
    await sandbox.actionSessionMonth(COURSE_ID, '2026-11');
    const nov = regionOf(html(sandbox));
    check('a month without sessions says so instead of rendering nothing',
      nov.includes('本月没有安排课堂。') && rowOf(nov).length === 0, nov.slice(0, 200));
    check('the empty month still names itself',
      nov.includes('2026年11月') && monthNote(nov).indexOf('2026年11月') === 0, monthNote(nov));

    // —— 选择留在内存里: 重新画一次页面仍停在同一月, 不是每次重新猜 ——
    await sandbox.pageCourse(COURSE_ID);
    check('the chosen month survives a plain re-render of the page',
      regionOf(html(sandbox)).includes('本月没有安排课堂。'));

    // —— 默认月份规则: 纯函数 + 显式"今天", 与运行时钟无关 ——
    const defaultMonth = vm.runInContext('sessionDefaultMonth', sandbox);
    check('the current month wins when the course has it',
      defaultMonth(['2026-09', '2026-12'], '2026-09') === '2026-09');
    check('a tie prefers the upcoming month',
      defaultMonth(['2026-09', '2026-11'], '2026-10') === '2026-11');
    check('a semester that already ended falls back to its last month',
      defaultMonth(['2026-09', '2026-12'], '2027-03') === '2026-12');
    check('a semester that has not started falls back to its first month',
      defaultMonth(['2026-09', '2026-12'], '2026-06') === '2026-09');
    check('no dated sessions means no default month', defaultMonth([], '2026-09') === '');
    const monthTabs = vm.runInContext('sessionMonthTabs', sandbox);
    check('the tab range fills the months in between and ignores the undated group',
      monthTabs([{ month: '2026-09' }, { month: '2026-12' }, { month: '' }]).join(',')
        === '2026-09,2026-10,2026-11,2026-12');
    check('a course with no dated sessions has no tabs', monthTabs([{ month: '' }]).length === 0);
    const monthLabel = vm.runInContext('sessionMonthLabel', sandbox);
    check('the long month label reads as a calendar month',
      monthLabel('2026-09', true) === '2026年9月', monthLabel('2026-09', true));
    check('the short month label drops the year',
      monthLabel('2026-09', false) === '9月', monthLabel('2026-09', false));
    check('a malformed month never invents a label',
      monthLabel('nope', true) === 'nope' && monthLabel('', true) === '');

    // —— 没有日期的课堂: 不随月份切换隐藏, 永远画在末尾并自带组头 ——
    // (藏在某个月的列表后面而不加说明, 会被读成"这个月的课"。)
    const undatedTable = routes();
    undatedTable['/api/courses/' + COURSE_ID + '/workspace'] = courseWorkspace({
      counts: { sessions: 1, materials: 0, evidence: 0, knowledge_points: 0, pending_review: 0 },
      sessions: [cardSession({ session_id: 'session-nodate', date: '', title: 'Tema suelto' })],
    });
    const undated = html(await renderPage(pageByName('course'), undatedTable, 'zh'));
    check('a session without a date is still listed, under its own head',
      undated.includes('日期未定') && undated.includes('Tema suelto'), undated.slice(0, 200));
    check('a session without a date produces no month strip',
      undated.indexOf('session-months') === -1);

    // 同一条规则的第二种情形: 有月份视图时, 没有日期的那一节**也不能被月切换
    // 吃掉** (藏起来会让用户以为这堂课不存在)。
    const mixedTable = routes();
    mixedTable['/api/courses/' + COURSE_ID + '/workspace'] = courseWorkspace({
      counts: { sessions: 2, materials: 0, evidence: 0, knowledge_points: 0, pending_review: 0 },
      sessions: [
        cardSession({
          session_id: 'session-dated', date: '2026-09-09',
          title: '15:00–17:00 Teoria | Dario Cottava | Aula Q1/1007',
        }),
        cardSession({ session_id: 'session-nodate', date: '', title: 'Tema suelto' }),
      ],
    });
    const mixed = html(await renderPage(pageByName('course'), mixedTable, 'zh'));
    check('the month view still renders the dated session',
      mixed.includes('09/09 · 周三') && mixed.includes('session-months'));
    check('a session without a date survives the month view',
      mixed.includes('日期未定') && mixed.includes('Tema suelto'), mixed.slice(0, 200));

    // —— 接线: 月份按钮必须真的被 app.js 的 data-action 委托接住, 并且真的把
    //    data-month 读出来 (与 session_id 接线检查同一模式: 渲染对了但没人处理、
    //    或读错属性, 点下去都毫无反应, 且不报错) ——
    check('the month buttons are wired to the delegated action',
      /action === 'session-month'/.test(APP_JS)
        && /actionSessionMonth\(courseId, target\.getAttribute\('data-month'\)\)/.test(APP_JS)
        && /function actionSessionMonth/.test(APP_JS));

    for (const lang of ['es', 'ca']) {
      const area = regionOf(html(await renderPage(pageByName('course'), table, lang)));
      check('sessions summary is localized in ' + lang,
        area.includes(lang === 'es' ? '6 sesiones · ' : '6 sessions · '),
        area.slice(0, 160));
      check('untitled-session fallback is localized in ' + lang,
        area.includes(lang === 'es' ? 'Sesión sin título' : 'Sessió sense títol'));
      check('the month line is localized in ' + lang,
        monthNote(area).includes(lang === 'es' ? ' de 2026 · ' : ' del 2026 · ')
          && monthNote(area).indexOf('2026-') === -1, monthNote(area));
      check('sessions card has no untranslated CJK in ' + lang,
        !/[\u4e00-\u9fff]/.test(area), 'CJK leaked: ' + firstCjk(area));
    }

    check('css gives the day card the shared radius',
      /\.session-day-card\s*\{[^}]*border-radius:\s*var\(--radius\)/.test(CSS));
    check('css separates two sessions of one day with a divider, not another card',
      /\.session-row\s*\+\s*\.session-row\s*\{[^}]*border-top/.test(CSS));
    check('css stretches a row link over its own row (not the whole day card)',
      /\.session-row-main::after\s*\{[^}]*inset:\s*0/.test(CSS));
    check('css keeps the action button above the stretched link',
      /\.session-row-action\s*\{[^}]*z-index:\s*1/.test(CSS));
    check('css highlights the current month from the aria attribute itself',
      /\.session-month\[aria-current="true"\]\s*\{/.test(CSS));
    check('css lays the month strip out as a wrapping row',
      /\.session-months\s*\{[^}]*display:\s*flex[^}]*flex-wrap:\s*wrap/.test(CSS));
    check('css lets a session row wrap on narrow screens instead of scrolling',
      /@media\s*\(max-width:\s*560px\)[\s\S]{0,160}\.session-row\s*\{[^}]*flex-wrap:\s*wrap/.test(CSS));
  }

  // ---- 静态检查: app.js 写出的每个 class 都在 styles.css 里有定义 ----
  //
  // 用例来源: 一处真实的欠账。Task 41 的 status.md 声明 ".path-chain /
  // .path-node" 已加入 styles.css，实际从未落地; Task 60 的 ".block-label"、
  // Task 62 的 ".conflict" 同样只有用法没有定义。页面依然可读，所以任何
  // "渲染成功 / 没有 CJK" 的检查都测不出来 —— 需要一个把两侧对起来的检查。
  //
  // 只统计**完整闭合**的 class="..." 字面量 (属性值内不含引号或 + 拼接符)，
  // 因为拼接出来的类名在静态文本里根本不存在, 统计它只会产生假阳性。
  {
    // 必须**先剥掉注释**: 否则一个只在注释里被提到的类名会被误判成"已定义"，
    // 检查就变成了摆设 (这正是首次写它时踩到的坑)。
    const cssCode = CSS.replace(/\/\*[\s\S]*?\*\//g, ' ');

    const cssDefined = new Set();
    const cssRe = /\.(-?[_a-zA-Z][_a-zA-Z0-9-]*)/g;
    let cm;
    while ((cm = cssRe.exec(cssCode)) !== null) cssDefined.add(cm[1]);

    // app.js 同理: 注释里出现的 class="..." 不是真的会渲染出来的。
    const jsCode = APP_JS
      .replace(/\/\*[\s\S]*?\*\//g, ' ')
      .replace(/(^|[^:])\/\/[^\n]*/g, '$1 ');

    const jsUsed = new Set();
    const classRe = /class="([^"'+]*?)"/g;
    let jm;
    while ((jm = classRe.exec(jsCode)) !== null) {
      for (const token of jm[1].split(/\s+/)) {
        if (token) jsUsed.add(token);
      }
    }

    // 先证明这个检查不是"什么都没扫到" —— 空集合会永远通过。
    check('styles.css defines classes', cssDefined.size > 20, 'only ' + cssDefined.size);
    check('app.js emits static class names', jsUsed.size > 30, 'only ' + jsUsed.size);

    const undefinedClasses = [...jsUsed].filter((c) => !cssDefined.has(c)).sort();
    check('every class emitted by app.js is defined in styles.css',
      undefinedClasses.length === 0, undefinedClasses.join(', '));
  }

  // ---- 课程显示名: 界面上一律显示名称, 不显示内容寻址的 course_id ----
  //
  // 用户报的缺陷: 学生页副标题渲染成 "课程 course-32dde014868219be · 0"。
  // course_id 是由课程名等内容派生的内部标识, 对用户没有任何信息量。
  //
  // 这一节必须**先跑 loadSidebar()** —— route() 的真实顺序是
  // loadSidebar() -> pageXxx(), 而 courseLabel() 靠 __courseCache 解析名称。
  // 直接调页面函数测到的是"缓存为空时的兜底", 不是用户看到的界面。
  {
    //: 2026-09-20 起**没有例外**: 身份串也改成了 code · language (哈希对用户零
    //: 信息量, 而 code 已经足够区分同名课程)。页面一律受同一条判据约束。
    //: 侧边栏 / 顶栏切换器在 #view 之外, 由下面单独一节覆盖。
    //:
    //: 各有一处"课程 X"标题 / 面包屑的页面 (2026-09-20 从 course_id 改成课程名的
    //: 所在页面; pageStudent 占两处)。这些页面上**必须**出现课程名。
    //: 2026-09-21 删掉 courseReview 页后, 这份名单随之少一项。
    const SHOWS_THE_COURSE_NAME = [
      'dashboard', 'learn', 'review', 'knowledge', 'materials', 'reviews',
      'session', 'students', 'mistakes', 'student',
    ];
    const COURSE_NAME = course().name;

    for (const page of PAGES) {
      const table = routes();
      const sandbox = await loadApp(table, 'zh');
      await sandbox.loadSidebar();
      await sandbox['page' + page.name[0].toUpperCase() + page.name.slice(1)](...page.args);
      const out = html(sandbox);
      // 去掉标签 => 只剩**可见文本**; href / data-course 这类属性值随之消失。
      const text = out.replace(/<[^>]*>/g, ' ');
      const at = text.indexOf(COURSE_ID);
      check(
        'page ' + page.name + ' never renders the raw course id as text',
        at === -1,
        at === -1 ? '' : text.slice(Math.max(0, at - 40), at + 40).replace(/\s+/g, ' ')
      );
      // 课堂哈希同一条规则: session_id 也是内容寻址的
      // ("session-" + sha256(course_id + 课号)[:16]), 对用户零信息量。
      // 这一条是**运行期**的 —— pytest 那条判据只扫源码里的 esc(...) 参数,
      // 扫不到"拼接后才变成哈希"的写法。
      const sessionAt = text.indexOf(SESSION_ID);
      check(
        'page ' + page.name + ' never renders the raw session id as text',
        sessionAt === -1,
        sessionAt === -1 ? '' : text.slice(Math.max(0, sessionAt - 40), sessionAt + 40).replace(/\s+/g, ' ')
      );
      if (SHOWS_THE_COURSE_NAME.indexOf(page.name) >= 0) {
        check(
          'page ' + page.name + ' shows the course name',
          text.indexOf(COURSE_NAME) >= 0,
          'expected ' + COURSE_NAME
        );
      }
    }

    // 身份串只能由 courseIdentity() 生产。
    //
    // 为什么值得单独一条: 内联复制一份**输出一样**, 所以不会有任何断言变红,
    // 只有等将来往助手加字段时才会暴露 —— 那正是本项目"同一条规则被抄了 N 遍"
    // 那个家族。所以这里盯的是**唯一性**, 不是输出。
    //
    // 指纹只写**历史上真实出现过**的那一个: mcCard 曾经内联拼过
    // `esc(row.code)`。它是不是写对了, 由 temp/_app_before_identity.js 作证
    // (重构前那份源码里必须能搜到这个指纹) —— 凭空编一个从没出现过的指纹,
    // indexOf 永远返回 -1, 这条检查就永远是绿的, 等于没写。
    // 真正的不变量是下面那条计数断言: 每个显示身份串的地方都调助手。
    //: 数"出现几次"之前先剥注释 —— 否则注释里提一句 `courseIdentity()` 就把
    //: 计数抬上去, 断言会随着文档改写而漂移。
    const identityCode = APP_JS
      .replace(/\/\*[\s\S]*?\*\//g, ' ')
      .replace(/(^|[^:])\/\/[^\n]*/g, '$1 ');
    const identitySites = identityCode.split('courseIdentity(').length - 1;
    check('the course identity string has no inline copy',
      identityCode.indexOf("' · ' + esc(row.code)") === -1,
      'courseIdentity() 又被内联复制了一份');
    check('every identity-string site calls courseIdentity()',
      identitySites === 3, 'courseIdentity( 出现 ' + identitySites + ' 次 (定义 1 + 调用 2)');

    // courseLabel() 的两条契约: 有缓存 -> 名称; 没有 -> 退回 id (不是空白)。
    const helper = await loadApp(routes(), 'zh');
    await helper.loadSidebar();
    check('courseLabel resolves a cached course to its name',
      helper.courseLabel(COURSE_ID) === COURSE_NAME, String(helper.courseLabel(COURSE_ID)));
    check('courseLabel falls back to the id for an unknown course',
      helper.courseLabel('course-nope') === 'course-nope',
      String(helper.courseLabel('course-nope')));
    check('courseLabel returns empty for a missing id',
      helper.courseLabel(null) === '', JSON.stringify(helper.courseLabel(null)));
  }

  // ---- 侧边栏 / 顶栏切换器: 显示课程名与**课程代码**, 不是哈希 ----
  //
  // 覆盖缺口 (2026-09-20 补上): 上面那节的判据只读 #view, 而侧边栏写在
  // #course-list、切换器写在 #course-switch —— 两块都在 #view 之外。于是
  // "侧边栏在每门课下面并列一行 course_id" 在用户眼皮底下活了很久, 却没有任何
  // 自动化盯它 (docs/status.md 的 Known limitation 里如实记过这个边界)。
  {
    const NO_CODE_ID = 'course-2';
    const COURSE_NAME = course().name; // 上一节的 const 是块作用域, 这里要自己取
    const table = routes();
    table['/api/courses'] = {
      courses: [
        course(), // Algebra Lineal / ALG / es
        { course_id: NO_CODE_ID, name: 'Calculo', code: '', language: 'es' },
      ],
    };
    const sandbox = await loadApp(table, 'zh');
    await sandbox.loadSidebar();

    const sidebar = sandbox.__elements.get('course-list').innerHTML;
    const switcher = sandbox.__elements.get('course-switch').innerHTML;
    const sidebarText = sidebar.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
    const switcherText = switcher.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();

    check('sidebar renders every course name',
      sidebarText.indexOf(COURSE_NAME) >= 0 && sidebarText.indexOf('Calculo') >= 0, sidebarText);
    check('sidebar renders the course code',
      sidebarText.indexOf(course().code) >= 0, sidebarText);
    check('sidebar never renders the raw course id as text',
      sidebarText.indexOf(COURSE_ID) === -1 && sidebarText.indexOf(NO_CODE_ID) === -1, sidebarText);
    check('sidebar keeps the course id in the href',
      sidebar.indexOf('href="#/courses/' + COURSE_ID + '"') >= 0 &&
      sidebar.indexOf('href="#/courses/' + NO_CODE_ID + '"') >= 0, sidebar);

    // 没有代码的课程只显示名称 —— 不许渲染一行空的 mono 占位。
    const noCodeItem = sidebar.slice(sidebar.indexOf('href="#/courses/' + NO_CODE_ID + '"'));
    const noCodeHtml = noCodeItem.slice(0, noCodeItem.indexOf('</li>'));
    check('a course without a code renders no empty second line',
      noCodeHtml.indexOf('tiny muted mono') === -1, noCodeHtml);
    check('the course with a code renders it as the second line',
      sidebar.indexOf('tiny muted mono">' + course().code + '<') >= 0, sidebar);

    check('switcher renders course names, never the raw id',
      switcherText.indexOf(COURSE_NAME) >= 0 &&
      switcherText.indexOf('Calculo') >= 0 &&
      switcherText.indexOf(COURSE_ID) === -1, switcherText);
    check('switcher keeps the course id as the option value',
      switcher.indexOf('value="' + COURSE_ID + '"') >= 0, switcher);

    // 换一门课之后重绘的那条路径同样不许把哈希带出来 (syncCourseChrome 会重画)。
    await sandbox.setCourse(NO_CODE_ID);
    const afterText = sandbox.__elements.get('course-list').innerHTML
      .replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
    check('sidebar stays free of the raw id after a course switch',
      afterText.indexOf(COURSE_ID) === -1 && afterText.indexOf(NO_CODE_ID) === -1, afterText);
    check('exactly one course stays highlighted after a course switch',
      (sandbox.__elements.get('course-list').innerHTML.match(/class="active"/g) || []).length === 1);
  }

  // ---- 课程切换不断路由 (2026-09-22 解耦) --------------------------------
  //
  // 根因见 app.js switchCourse: 侧边栏原来是直链 `#/courses/<id>`, 顶栏切换器
  // 原来是 `setCourse() + 跳详情` —— 11 个顶层功能页换课都被踢到课程详情。
  // 真执行的行为回归在 ui_render_check.js; 这里只锁三处接线 (标记 / 委托 /
  // 拦截), 防止有人顺手把 data 属性或委托删掉而切课又跳页。
  {
    const table = routes();
    const sandbox = await loadApp(table, 'zh');
    await sandbox.loadSidebar();
    const sidebar = sandbox.__elements.get('course-list').innerHTML;
    check('sidebar marks every course as a context switch link',
      sidebar.indexOf('data-course-switch="' + COURSE_ID + '"') >= 0, sidebar.slice(0, 300));
    check('switchCourse is exposed as the single switch entry',
      typeof sandbox.switchCourse === 'function', typeof sandbox.switchCourse);
    const handler = APP_JS.match(/picker\.addEventListener\('change', \(\) => \{([\s\S]*?)\}\);/);
    check('the course switcher delegates to switchCourse',
      !!handler && handler[1].indexOf('switchCourse(courseId)') >= 0,
      handler ? handler[1].slice(0, 200) : 'handler not found');
    check('the course switcher never navigates on its own',
      !!handler && handler[1].indexOf('location.hash') === -1,
      handler ? handler[1].slice(0, 200) : 'handler not found');
    check('plain left-clicks on switch links are intercepted, not navigated',
      APP_JS.indexOf("closest('a[data-course-switch]')") >= 0 &&
      APP_JS.indexOf('event.preventDefault()') >= 0,
      'delegation missing');
    const switchFn = APP_JS.slice(APP_JS.indexOf('async function switchCourse(courseId)'));
    check('staying on the page re-renders in place instead of navigating',
      switchFn.indexOf('await route()') >= 0, switchFn.slice(0, 120));
    check('course-scoped details still retarget to the new course',
      switchFn.indexOf("'#/courses/' + encodeURIComponent(courseId)") >= 0,
      switchFn.slice(0, 120));
  }

  // ---- 学生注册: 表单必须**始终**在, 而且真的接在 POST /api/students 上 ----
  //
  // 用户报的第二个问题: 注册学生只能手写 HTTP 请求。静态 HTML 里有表单不等于
  // 能提交, 所以两头都查 —— 渲染出来的表单字段 + 接线是否真的存在。
  {
    const cases = [['with students', routes()], ['without students', emptyRoutes()]];
    for (const [label, table] of cases) {
      const sandbox = await loadApp(table, 'zh');
      await sandbox.loadSidebar();
      await sandbox.pageStudents();
      const out = html(sandbox);
      check('students page renders the registration form (' + label + ')',
        out.indexOf('id="student-form"') >= 0);
      check('students page form has a student_id field (' + label + ')',
        out.indexOf('name="student_id"') >= 0);
      check('students page form has a display_name field (' + label + ')',
        out.indexOf('name="display_name"') >= 0);
      check('students page form has a submit button (' + label + ')',
        out.indexOf('type="submit"') >= 0);
    }
    check('the registration form is wired to POST /api/students',
      APP_JS.indexOf("api('/students', { method: 'POST', body })") >= 0);
    check('pageStudents wires the registration form',
      /async function pageStudents\(\)[\s\S]*?wireStudentForm\(\);/.test(APP_JS));
  }

  // ---- 材料页: "课堂 (可选)" 必须是下拉, 而且选项是人话标签而不是哈希 ----
  //
  // 用户报的第三个问题: 上传材料要关联课堂时, 只能由用户手打 `session-…`。
  // session_id 与 course_id 是同一类东西 —— models.ClassSession._generate_stable_id
  // 里 `"session-" + sha256(course_id + 课号)[:16]`, 对用户零信息量。
  //
  // 这一节**必须真的执行 pageMaterials()**: 静态检查只能证明"源码里写了 select",
  // 证明不了渲染出来的表单里真有选项、选项标签是不是哈希。
  {
    const sandbox = await renderPage(pageByName('materials'), routes(), 'zh');
    const out = html(sandbox);

    // 1) 控件本身: 是 select, 不再是"请手打 id"的文本框
    check('materials page renders a session dropdown',
      out.indexOf('<select name="session_id">') >= 0, out.slice(0, 240));
    check('materials page no longer asks the user to type a session id',
      out.indexOf('placeholder="session-') === -1);
    check('the session dropdown offers a "no session" option',
      out.indexOf('<option value="">') >= 0, out.slice(0, 240));

    // 2) 选项来自真实课堂, 标签是课号 + 标题。
    //
    // **必须只看 session 那个 <select>**: 同一张表单里还有语言下拉, 而
    // `第 3 堂 · Tema 3` 在材料列表的"课堂"列里也会出现 —— 搜整页的话, 选项文案
    // 退化成哈希时这条检查仍然是绿的 (变异 B 实测踩过)。
    const selectHtml = out.slice(
      out.indexOf('<select name="session_id">'),
      out.indexOf('</select>', out.indexOf('<select name="session_id">'))
    );
    check('the session dropdown lists the course sessions',
      selectHtml.indexOf('value="' + SESSION_ID + '"') >= 0, selectHtml);
    check('the session dropdown labels a session with its real date, weekday, number and title',
      selectHtml.indexOf('2026-03-01（周日） · 第 3 堂 · Tema 3') >= 0, selectHtml);
    check('the session dropdown never labels an option with the raw id',
      selectHtml.indexOf('>' + SESSION_ID + '<') === -1, selectHtml);

    // 3) session_id 只允许出现在 option 的 value 里 —— 表单里不许出现它的文案
    const formHtml = out.slice(out.indexOf('id="upload-form"'), out.indexOf('</form>'));
    const formText = formHtml.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
    check('the session id never appears as text inside the upload form',
      formText.indexOf(SESSION_ID) === -1, formText);
    check('the upload form keeps the session id as the option value',
      formHtml.indexOf('value="' + SESSION_ID + '"') >= 0, formHtml.slice(0, 240));

    // 4) 材料列表的"课堂"列 —— 用户真正天天看的是这一列, 不是下拉
    const bodyHtml = out.slice(out.indexOf('<tbody>'), out.indexOf('</tbody>'));
    const bodyText = bodyHtml.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
    check('the materials table shows the real session date and label, not its id',
      bodyText.indexOf('2026-03-01（周日）') >= 0
      && bodyText.indexOf('第 3 堂') >= 0
      && bodyText.indexOf('Tema 3') >= 0
      && bodyText.indexOf(SESSION_ID) === -1,
      bodyText);

    // 5) 材料挂在一个**已不存在**的课堂下：明确报告记录缺失，不伪造日期，
    //    也不把内容寻址的 session_id 塞回可见文本。
    {
      const table = routes();
      table['/api/materials'] = { materials: [material({ session_id: 'session-gone' })] };
      const orphan = await renderPage(pageByName('materials'), table, 'zh');
      const orphanText = html(orphan).replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
      check('an orphaned material reports a missing session without showing its id',
        orphanText.indexOf('课堂记录缺失') >= 0
        && orphanText.indexOf('session-gone') === -1, orphanText.slice(0, 240));
    }

    // 6) 一堂课都没有: 下拉必须**仍然渲染** (否则表单直接坏掉), 并说明课堂从哪来
    {
      const emptySandbox = await renderPage(pageByName('materials'), emptyRoutes(), 'zh');
      const emptyOut = html(emptySandbox);
      check('the session dropdown renders even when the course has no sessions',
        emptyOut.indexOf('<select name="session_id">') >= 0, emptyOut.slice(0, 240));
      check('an empty session list still offers the "no session" option',
        emptyOut.indexOf('不关联课堂') >= 0);
      check('an empty session list explains where sessions come from',
        emptyOut.indexOf('还没有课堂。') >= 0);
      check('an empty session list never renders a stack trace',
        !/Traceback|at Object\.|File "/.test(emptyOut));
    }

    // 7) i18n: es/ca 下下拉与提示都不能漏出中文。
    //    夹具是 ASCII, 所以渲染结果里出现 CJK 就只能是界面文案漏译。
    for (const lang of ['es', 'ca']) {
      const localized = await renderPage(pageByName('materials'), routes(), lang);
      const localizedOut = html(localized);
      check('materials session dropdown renders in ' + lang, localizedOut.length > 0);
      check('materials session dropdown has no untranslated CJK in ' + lang,
        !/[\u4e00-\u9fff]/.test(localizedOut), 'CJK leaked: ' + firstCjk(localizedOut));
      // 标签本身也必须真的翻了 —— 只查"没有 CJK"是不够的: 标签里全是数字和
      // 拉丁字母, 万一哪天退化成 `第 3 堂 · Tema 3` 的硬编码中文, CJK 检查会红,
      // 但退化成别的拉丁写法就不会。这里钉住两种语言各自的拼法。
      const localizedLabel = lang === 'ca'
        ? '01/03/2026 (diumenge) · Sessió 3 · Tema 3'
        : '01/03/2026 (domingo) · Sesión 3 · Tema 3';
      check('the session label is localized in ' + lang,
        localizedOut.indexOf(localizedLabel) >= 0,
        localizedOut.slice(0, 480));
    }

    // 8) 接线: 取值不能再写死 input[...] —— 换控件类型时那会静默变成 null.value,
    //    崩在提交路径上, 而提交路径**没有任何自动化**能跑到 (DOM 桩不跑 submit)。
    check('the upload form reads the session field by name, not by tag',
      APP_JS.indexOf('form.querySelector(\'[name="session_id"]\')') >= 0);
    check('the upload form no longer queries an input for the session id',
      APP_JS.indexOf('input[name="session_id"]') === -1);

    // 9) 标签只有一个生产者 —— 盯的是**唯一性**, 不是输出 (内联复制输出一样,
    //    不会有任何断言变红, 只有等将来往标签里加字段才会暴露)。
    const labelCode = APP_JS
      .replace(/\/\*[\s\S]*?\*\//g, ' ')
      .replace(/(^|[^:])\/\/[^\n]*/g, '$1 ');
    check('sessionLabel() is defined exactly once',
      labelCode.split('function sessionLabel(').length - 1 === 1);
    check('the picker uses sessionLabel() and the table uses its shared structured fields',
      labelCode.indexOf('esc(sessionLabel(s))') >= 0
      && labelCode.indexOf('function materialSessionCell(session)') >= 0
      && labelCode.indexOf('const parts = sessionDisplayParts(session);') >= 0
      && labelCode.indexOf('materialSessionCell(sessionById[m.session_id])') >= 0);
    // 每个显示课堂的地方都走共享格式化入口；材料表为了分行渲染直接消费
    // sessionDisplayParts()，其余链接/toast 继续走 sessionLabel()。
    const labelSites = labelCode.split('sessionLabel(').length - 1;
    const displayPartSites = labelCode.split('sessionDisplayParts(').length - 1;
    check('session display sites use the shared formatters',
      labelSites === 7 && displayPartSites === 3,
      'sessionLabel=' + labelSites + ', sessionDisplayParts=' + displayPartSites);

    // 10) D1 / D4: 处理警告与"成功但零证据"必须被看见, 而且**三处一致**。
    //
    // 契约: "文档可解析但没有可提取文本"是合法成功 (COMPLETED + 0 证据),
    // 但旧界面只画 processing_status —— 用户看到"成功"、拿到 0 条证据, 却
    // 看不到任何解释 (见 app.js 的 warningRow / zeroEvidenceHint)。
    //
    // 三处 = 材料页 / 概览的材料卡 / 课堂页的材料表。它们读的是**同一条**
    // material 记录, 所以夹具也只做一份, 免得三处各自漂移。
    {
      const warn = material({
        processing_status: 'COMPLETED',
        warning: 'NO_TEXT_EXTRACTED',
        evidence_ids: [],
        evidence_count: 0,
      });
      const table = routes();
      table['/api/materials'] = { materials: [warn] };
      table['/api/dashboard'] = Object.assign(dashboard(), { materials: [warn] });
      table['/api/courses/' + COURSE_ID + '/sessions/' + SESSION_ID + '/workspace'] =
        sessionWorkspace({ materials: [warn] });

      for (const site of [
        { page: 'materials', label: 'the materials page' },
        { page: 'dashboard', label: 'the dashboard materials card' },
        { page: 'session', label: 'the session materials table' },
      ]) {
        const sandbox = await renderPage(pageByName(site.page), table, 'zh');
        const pageOut = html(sandbox);
        check(site.label + ' shows the warning code verbatim',
          pageOut.includes('NO_TEXT_EXTRACTED'));
        check(site.label + ' shows the human copy next to the code',
          pageOut.includes('扫描件请转成图片重传走 OCR'));
        check(site.label + ' says a zero-evidence success did not fail',
          pageOut.includes('处理成功，但这份材料没有提取到任何内容'));
        check(site.label + ' never leaks a raw warn.* key',
          !/warn\.[A-Z]/.test(pageOut), /warn\.[A-Z._a-z]+/.exec(pageOut) || '');

        // dashboard 把 D1 警告 / D4 零证据提示放在**独立的说明行**
        // (tr.material-note > td[colspan=3]), 而不是塞进状态格。原因: 这张表
        // 是 table-layout: fixed, 状态列被钉死在 160px, 而说明约 150 字符 ——
        // 在 160px 里要折 5~8 行, 把整行撑成一根细长条 (用户截图里的形状)。
        // 占满卡宽后只折 1~2 行, 状态列回到干净的单行。
        // 其它页用的是非 fixed 表格 (列宽自动分配), 不受此影响, 继续走原全局
        // warningRow / zeroEvidenceHint, 不动它们。
        //
        // 注意 match() 在"一个都没匹配到"时返回 **null** 而不是空数组 ——
        // 直接 .length 会抛 TypeError, 把整份 audit 崩掉 (崩溃看起来像
        // 基础设施故障, 实际是断言该报红)。所以先归一成 [] 再数。
        if (site.page === 'dashboard') {
          const statusCell = pageOut.match(/<td>(<span class="pill[^>]*>COMPLETED<\/span>)<\/td>/);
          check(site.label + ' status cell holds the COMPLETED pill alone',
            !!statusCell, statusCell ? statusCell[1].slice(0, 80) : 'statusCell not found');
          // 说明不许再挤回状态格 —— 这正是本次要修的形状。
          check(site.label + ' status cell no longer carries the note',
            !!statusCell && statusCell[0].indexOf('material-detail') === -1,
            statusCell ? statusCell[0].slice(0, 200) : 'statusCell not found');
          // 材料行打上 material-row: CSS 靠它去掉底边, 让说明行贴上来。
          check(site.label + ' material row is marked for its attached note',
            /<tr class="material-row">/.test(pageOut));
          // 说明行必须占满三列, 而不是缩在某一格里。
          const noteRow = pageOut.match(
            /<tr class="material-note"><td colspan="3">([\s\S]*?)<\/td><\/tr>/);
          const noteBody = noteRow ? noteRow[1] : '';
          check(site.label + ' note row spans all three columns',
            !!noteRow, noteRow ? noteRow[0].slice(0, 120) : 'note row not found');
          const detailBlocks = noteBody.match(/<div class="material-detail">[\s\S]*?<\/div>/g) || [];
          check(site.label + ' warning is wrapped in .material-detail',
            /<div class="material-detail">[\s\S]*?NO_TEXT_EXTRACTED[\s\S]*?<\/div>/.test(noteBody),
            noteBody.slice(0, 200));
          check(site.label + ' zero-evidence hint is wrapped in .material-detail',
            detailBlocks.length >= 2,
            'blocks=' + detailBlocks.length + ' | ' + noteBody.slice(0, 200));
          // 样式必须真的把说明行画成"附注": 材料行去底边 + 说明行自带浅底。
          check('css drops the bottom border of a material row that has a note',
            /tr\.material-row\s*>\s*td\s*\{[^}]*border-bottom:\s*0/.test(CSS));
          check('css gives the note row a tinted, tightened cell',
            /tr\.material-note\s*>\s*td\s*\{[^}]*background:\s*var\(--surface-2\)[\s\S]{0,120}?padding-top:\s*0/.test(CSS));
        }
      }

      // 三语: 夹具里的码与文件名都是 ASCII, 所以 es / ca 渲染结果里出现 CJK
      // 只可能是界面文案漏译 —— 警告文案也必须真的翻了。
      for (const lang of ['es', 'ca']) {
        const localized = await renderPage(pageByName('materials'), table, lang);
        const localizedOut = html(localized);
        check('the warning code stays verbatim in ' + lang,
          localizedOut.includes('NO_TEXT_EXTRACTED'));
        check('the warning copy is localized in ' + lang,
          localizedOut.includes(lang === 'ca' ? 'El document no té text extraïble'
            : 'El documento no tiene texto extraíble'),
          localizedOut.slice(0, 480));
        check('the zero-evidence hint is localized in ' + lang,
          localizedOut.includes(lang === 'ca' ? 'El processament ha acabat bé'
            : 'El procesamiento terminó bien'),
          localizedOut.slice(0, 480));
        check('the warning copy has no untranslated CJK in ' + lang,
          !CJK.test(localizedOut), 'CJK leaked: ' + firstCjk(localizedOut));
      }

      // 键面: warning 码是人话的 key 前缀 (不是拼出来的), 所以它在三张表里
      // 都必须存在 —— 少一个语的译文, t() 会退回中文而不是报错。
      const tables = vm.runInContext('I18N', await loadApp(routes(), 'zh'));
      const warnKeys = Object.keys(tables.zh).filter((k) => k.indexOf('warn.') === 0).sort();
      check('every warn.* key exists in all three languages',
        warnKeys.length > 0 && warnKeys.every((k) => tables.es[k] && tables.ca[k]),
        warnKeys.join(','));
      check('no warn.* translation falls back to Chinese',
        warnKeys.every((k) => !CJK.test(tables.es[k]) && !CJK.test(tables.ca[k])),
        warnKeys.filter((k) => CJK.test(tables.es[k]) || CJK.test(tables.ca[k])).join(','));
    }
  }
}

async function main() {
  const args = process.argv.slice(2);
  const dumpAt = args.indexOf('--dump');
  if (dumpAt >= 0) {
    await dump(args[dumpAt + 1] || 'zh');
    return;
  }

  const previewAt = args.indexOf('--preview');
  if (previewAt >= 0) {
    const langArg = args[previewAt + 1];
    const lang = langArg && langArg.indexOf('--') !== 0 ? langArg : 'zh';
    const outAt = args.indexOf('--out');
    await preview(lang, outAt >= 0 ? args[outAt + 1] : null);
    return;
  }

  await audit();

  if (failures.length) {
    console.error('UI audit FAILED (' + failures.length + '/' + checks + ')');
    for (const failure of failures) console.error('  ✗ ' + failure);
    process.exitCode = 1;
    return;
  }
  console.log('UI audit OK (' + checks + ' checks)');
}

main().catch((err) => {
  console.error('ui_audit crashed:', err && err.stack ? err.stack : err);
  process.exitCode = 1;
});
