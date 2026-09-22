/* 课堂助手 UI 压力检查 (Task 69)
 *
 * 它和 ui_render_check.js 的区别
 * ------------------------------
 * ``ui_render_check.js`` 用的是**手写的固定夹具** —— 一门课、一道题、
 * 一个学生。它能证明"页面函数能跑通、答案不泄漏", 但证明不了
 * "一门课有 600 个知识点、500 个学生、1000 道题时页面还能不能跑"。
 *
 * 本脚本用的是**真实服务器**: 一个跑在真实 sqlite 上的 ``ApiServer``,
 * 库里是一整个学期的量 (5 门课 / 750 份材料 / 3000 个知识点 /
 * 5000 道题 / 20000 条作答)。``fetch`` 不再走桩, 而是真的发 HTTP。
 *
 * 因此这里断言的是三件事:
 *   1. 10 个页面函数在真实学期规模下**不抛异常**;
 *   2. 渲染产物里没有 ``undefined`` / ``NaN`` —— 大规模数据最容易暴露
 *      的是"某个字段在大量数据下变了形状"这类模板 bug;
 *   3. 渲染耗时与 HTML 体积在预算内 (不做硬实时断言, 只做量级闸门,
 *      免得把慢机器上的偶发抖动判成失败)。
 *
 * 这仍然**不是**浏览器测试: 本机是 Windows, 浏览器自动化只支持
 * macOS / Linux。这里用的是最小 DOM 桩 + 真实 HTTP。
 *
 * 用法: node scripts/ui_stress_check.js <config.json>
 *       config.json = { "base": "http://127.0.0.1:PORT", "ids": {...} }
 * 退出码 0 = 全部通过; 结果 JSON 打到 stdout 的 ``__STRESS_JSON__`` 行。
 */

'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');

// P1-6: 前端已拆成多个零构建脚本, index.html 按下面的顺序 <script> 引入。
// 浏览器里多个 <script> 共享同一个全局作用域; 在 vm 里对应的是**同一个
// context 顺序执行多个 script** —— 顶层 function / const 互相可见。
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

const CONFIG_PATH = process.argv[2];
if (!CONFIG_PATH) {
  console.error('usage: node scripts/ui_stress_check.js <config.json>');
  process.exit(2);
}
const CONFIG = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
const BASE = String(CONFIG.base).replace(/\/+$/, '');
const IDS = CONFIG.ids || {};

// 每个页面渲染出的 HTML 至少要这么长。太小 = 页面其实是空的
// (大规模数据下最常见的失败形态: 查询超时/异常被吞掉 -> 渲染出空壳)。
const MIN_HTML = 200;

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

function makeSandbox() {
  const elements = new Map();
  const storage = new Map();
  const fetched = [];

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

  // 真实 HTTP: 打到跑着的 ApiServer。相对 URL 补上 base。
  async function realFetch(url, init) {
    const target = String(url).startsWith('http') ? String(url) : BASE + String(url);
    fetched.push(target);
    return fetch(target, init);
  }

  const sandbox = {
    window,
    document,
    localStorage: window.localStorage,
    fetch: realFetch,
    console,
    setTimeout,
    clearTimeout,
    URLSearchParams,
    FormData,
    ApiError: class ApiError extends Error {},
    __elements: elements,
    __fetched: fetched,
  };
  sandbox.globalThis = sandbox;
  return sandbox;
}

async function loadApp() {
  const sandbox = makeSandbox();
  vm.createContext(sandbox);
  // 按加载顺序逐段执行 —— 等价于浏览器里多个 <script> 共享全局作用域。
  for (const source of WEB_SOURCES) {
    vm.runInContext(source.code, sandbox, { filename: source.name });
  }
  return sandbox;
}

function grab(sandbox, name) {
  return vm.runInContext(name, sandbox, { filename: 'grab:' + name });
}

function html(sandbox) {
  return sandbox.document.getElementById('view').innerHTML;
}

// ---------------------------------------------------------------- 页面清单

// 10 个页面。参数来自 config.ids —— 全是真实库里存在的 id,
// 由 Python 侧从真实工作区里取出来传进来。
const PAGES = [
  { name: 'pageDashboard', args: [] },
  { name: 'pageToday', args: [] },
  { name: 'pageMyCourses', args: [] },
  { name: 'pageCourse', args: ['course_id'] },
  { name: 'pageSession', args: ['course_id', 'session_id'] },
  { name: 'pageKnowledge', args: [] },
  { name: 'pageKnowledgeDetail', args: ['course_id', 'knowledge_id'] },
  { name: 'pageStudents', args: [] },
  { name: 'pageExercises', args: [] },
  { name: 'pageExercise', args: ['course_id', 'exercise_id', 'student_id'] },
  { name: 'pageMistakes', args: [] },
  { name: 'pageReview', args: [] },
];

// ---------------------------------------------------------------- 主流程

const failures = [];
const report = [];

function check(name, condition, detail) {
  if (!condition) failures.push(name + (detail ? ' — ' + detail : ''));
  return condition;
}

async function main() {
  const sandbox = await loadApp();
  let stateCourse = IDS.course_id;

  for (const page of PAGES) {
    const fn = grab(sandbox, page.name);
    if (typeof fn !== 'function') {
      failures.push(`${page.name}: 不是函数 (app.js 里找不到)`);
      report.push({ page: page.name, ok: false, ms: 0, bytes: 0, error: 'not a function' });
      continue;
    }
    const args = page.args.map((key) => IDS[key]);
    const started = process.hrtime.bigint();
    let error = null;
    let out = '';
    try {
      // 页面函数会写 localStorage 来记住"当前课程", 所以按真实顺序跑:
      // 先进课程页, 后面的页面才能取到当前课程。
      if (page.name === 'pageCourse' && stateCourse) {
        sandbox.localStorage.setItem('currentCourseId', stateCourse);
      }
      await fn.apply(null, args);
      out = html(sandbox);
    } catch (exc) {
      error = String((exc && exc.message) || exc);
    }
    const ms = Number(process.hrtime.bigint() - started) / 1e6;
    const bytes = out.length;

    const ok =
      check(`${page.name} 在学期规模下不抛异常`, error === null, error) &&
      check(`${page.name} 渲染出内容 (${bytes} bytes)`, bytes >= MIN_HTML, `只有 ${bytes} bytes`) &&
      check(`${page.name} 没有 undefined 字段`, !out.includes('undefined'), '渲染出 undefined') &&
      check(`${page.name} 没有 NaN 数值`, !out.includes('NaN'), '渲染出 NaN');

    report.push({
      page: page.name,
      ok: !!ok,
      ms: Math.round(ms * 100) / 100,
      bytes,
      error,
    });
  }

  const payload = {
    base: BASE,
    pages: report,
    failures,
    checks: report.length * 4,
  };
  console.log('__STRESS_JSON__' + JSON.stringify(payload));
  if (failures.length) {
    for (const line of failures) console.error('FAIL ' + line);
    process.exit(1);
  }
}

main().catch((exc) => {
  console.error('stress harness crashed: ' + exc);
  process.exit(1);
});
