/* 真实端到端 (带课堂的那一支): 自己起一个**临时数据目录**的本机服务, 在里面建
 * 课程 + 两堂课 + 一份材料, 然后渲染真实的材料页, 断言下拉的选项与标签。
 *
 * 为什么需要单独一个脚本: temp/e2e_student_ui.js 打的是**用户自己的数据**, 而且
 * 契约是**只读** —— 而用户库里 `sessions` 表是空的, 于是那支 e2e 只能覆盖"空课堂"
 * 分支。"有课堂时选项渲染成 `第 3 堂 · Tema 3`" 此前只在 ui_audit.js 的夹具上
 * 证明过, 没有在真实 HTTP 响应上证明过。
 *
 * 为什么放在 scripts/ 而不是 temp/: 它**自带服务**(spawn 自己的 launcher, 用临时
 * 数据目录), 所以不需要外部先起服务, 可以进常规测试套件
 * (tests/test_student_ui.py::test_the_live_session_picker_end_to_end_passes)。
 * temp/e2e_student_ui.js 需要用户先在 8765 上起服务, 所以留在 temp/。
 *
 * 写的是**临时目录** (temp/_e2e_session_picker_data/), 跑完就删; 不碰
 * classroom-data/。服务由本脚本 spawn, 结束前 kill。
 *
 * 用法: node scripts/e2e_session_picker.js
 */
'use strict';

const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const PYTHON = path.join(ROOT, 'Python', 'pythoncore-3.14-64', 'python.exe');
const DATA_DIR = path.join(ROOT, 'temp', '_e2e_session_picker_data');
const PORT = 8766;
const BASE = 'http://127.0.0.1:' + PORT;

// P1-6: 前端零构建拆分的加载顺序 —— 必须与 src/web/index.html 里的
// <script> 顺序一致 (顶层初始化有顺序依赖, 见 index.html 里的注释)。
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

let checks = 0;
const failures = [];

function check(name, ok, detail) {
  checks += 1;
  if (!ok) failures.push(name + (detail ? ' — ' + detail : ''));
}

const safe = (fn) => {
  try { return { ok: true, value: fn() }; }
  catch (err) { return { ok: false, error: err && err.message ? err.message : String(err) }; }
};

// ---------------------------------------------------------------- DOM 桩

function makeElement(id) {
  return {
    id, innerHTML: '', textContent: '', className: '', value: '',
    style: {}, dataset: {}, title: '', hidden: false,
    addEventListener() {}, removeEventListener() {},
    setAttribute() {}, getAttribute() { return null; },
    appendChild() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    closest() { return null; },
  };
}

function makeSandbox(appSource, initialCourse) {
  const elements = new Map();
  const storage = new Map();
  if (initialCourse) storage.set('ca.course', initialCourse);
  const requests = [];

  const document = {
    getElementById: (id) => {
      if (!elements.has(id)) elements.set(id, makeElement(id));
      return elements.get(id);
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    createElement: (tag) => makeElement(tag),
    documentElement: makeElement('html'),
  };

  const window = {
    location: { hash: '#/' },
    localStorage: {
      getItem: (k) => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
      removeItem: (k) => storage.delete(k),
    },
    addEventListener() {},
    scrollTo() {},
    matchMedia: () => ({ matches: false, addEventListener() {} }),
  };

  const sandbox = {
    window, document, localStorage: window.localStorage,
    fetch: async (url, init) => {
      const full = String(url).startsWith('http') ? String(url) : BASE + String(url);
      requests.push(((init && init.method) || 'GET') + ' ' + full);
      return fetch(full, init);
    },
    console, setTimeout, clearTimeout, URLSearchParams, FormData,
    ApiError: class ApiError extends Error {},
    __elements: elements, __storage: storage, __requests: requests,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(appSource, sandbox, { filename: 'app.js' });
  return sandbox;
}

const html = (sandbox) => sandbox.document.getElementById('view').innerHTML;
const visibleText = (out) => out.replace(/<[^>]*>/g, ' ');
const decodeEntities = (text) => text
  .replace(/&#39;/g, "'").replace(/&quot;/g, '"')
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');

// ---------------------------------------------------------------- HTTP

async function api(method, urlPath, body) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const response = await fetch(BASE + urlPath, init);
  const payload = await response.json();
  if (payload.success !== true) {
    throw new Error(method + ' ' + urlPath + ' -> ' + JSON.stringify(payload.error));
  }
  return payload.data;
}

async function postBytes(urlPath, bytes, contentType) {
  const response = await fetch(BASE + urlPath, {
    method: 'POST', headers: { 'Content-Type': contentType }, body: bytes,
  });
  const payload = await response.json();
  if (payload.success !== true) {
    throw new Error('POST ' + urlPath + ' -> ' + JSON.stringify(payload.error));
  }
  return payload.data;
}

async function waitForServer(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(BASE + '/api/health');
      if (response.status === 200) return true;
    } catch (err) { /* 还没起来 */ }
    await new Promise((r) => setTimeout(r, 300));
  }
  return false;
}

// ---------------------------------------------------------------- 主流程

(async () => {
  fs.rmSync(DATA_DIR, { recursive: true, force: true });
  const server = spawn(PYTHON, [
    '-m', 'src.application.launcher', 'start',
    '--data-dir', DATA_DIR, '--port', String(PORT),
    // 本脚本不跑任何处理, 只登记材料 —— 用 mock 就不必加载真实引擎。
    '--asr-mode', 'mock',
  ], { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'] });

  const log = [];
  server.stdout.on('data', (chunk) => log.push(String(chunk)));
  server.stderr.on('data', (chunk) => log.push(String(chunk)));

  try {
    const up = await waitForServer(60000);
    if (!up) throw new Error('server did not come up:\n' + log.join(''));

    // P1-6: 前端已拆成多个零构建脚本。**每一个**都必须被服务端原样提供 ——
    // 少一个就会在浏览器里静默拿到 index.html 的回退 (SPA 路由), 表现为
    // "页面全白但 HTTP 200"。所以逐文件做 byte-identical 校验。
    const served = [];
    for (const rel of WEB_FILES) {
      const fromServer = await (await fetch(BASE + '/' + rel)).text();
      const onDisk = fs.readFileSync(path.join(ROOT, 'src', 'web', rel), 'utf8');
      check('the served /' + rel + ' is byte-identical to the one on disk',
        fromServer === onDisk, fromServer.length + ' vs ' + onDisk.length);
      served.push(fromServer);
    }
    // 按加载顺序拼接 —— 等价于浏览器里多个 <script> 共享全局作用域。
    const appSource = served.join('\n');

    // ---- 造数据: 一门课 + 两堂课 (一堂没有标题) + 一份挂在第 1 堂下的材料 ----
    const course = await api('POST', '/api/courses', { name: 'Algebra Lineal', code: 'ALG' });
    const courseId = course.course_id;
    const first = await api('POST', '/api/sessions', {
      course_id: courseId, session_number: 1, title: 'Tema 1', date: '2026-03-01',
    });
    const second = await api('POST', '/api/sessions', {
      course_id: courseId, session_number: 2, date: '2026-03-08',
    });

    const live = (await api('GET', '/api/sessions?course_id=' + encodeURIComponent(courseId)))
      .sessions;
    check('the live workspace really has two sessions', live.length === 2,
      live.map((s) => s.session_id).join(', '));

    await postBytes(
      '/api/materials?course_id=' + encodeURIComponent(courseId)
        + '&session_id=' + encodeURIComponent(first.session_id) + '&filename=apuntes.txt',
      Buffer.from('Una funcio es una relacio.', 'utf-8'), 'text/plain');

    // ---- 渲染真实的材料页 ----
    const sandbox = makeSandbox(appSource, courseId);
    await sandbox.loadSidebar();
    await sandbox.pageMaterials();
    const out = html(sandbox);

    const start = out.indexOf('<select name="session_id">');
    const selectHtml = out.slice(start, out.indexOf('</select>', start));
    check('the upload form renders a session dropdown', start >= 0, out.slice(0, 240));
    check('the dropdown no longer asks the user to type a session id',
      out.indexOf('placeholder="session-') === -1);

    const options = selectHtml.match(/<option value="[^"]*">[^<]*<\/option>/g) || [];
    const sessionOptions = options.filter((o) => o.indexOf('value=""') === -1);
    check('the dropdown has exactly one option per live session',
      sessionOptions.length === 2, sessionOptions.join(' | '));
    check('the no-session option is still offered',
      selectHtml.indexOf('<option value="">') >= 0);
    check('every live session is selectable by its id',
      live.every((s) => selectHtml.indexOf('value="' + s.session_id + '"') >= 0),
      selectHtml);

    const labels = decodeEntities(sessionOptions
      .map((o) => o.replace(/^<option value="[^"]*">/, '').replace(/<\/option>$/, ''))
      .join(' | '));
    check('a session with a title is labelled with real date, number and title',
      labels.indexOf('2026-03-01（周日） · 第 1 堂 · Tema 1') >= 0, labels);
    check('a session without a title is labelled with date and number only',
      labels.indexOf('2026-03-08（周日） · 第 2 堂') >= 0
      && labels.indexOf('2026-03-08（周日） · 第 2 堂 ·') === -1, labels);
    check('the dropdown never labels an option with the raw id',
      (labels.match(/session-[0-9a-f]{8,}/g) || []).length === 0, labels);

    // ---- 材料列表的"课堂"列 ----
    const bodyHtml = out.slice(out.indexOf('<tbody>'), out.indexOf('</tbody>'));
    const bodyText = decodeEntities(visibleText(bodyHtml)).replace(/\s+/g, ' ');
    check('the materials table shows the real session date, number and title instead of its id',
      bodyText.indexOf('2026-03-01（周日）') >= 0
      && bodyText.indexOf('第 1 堂') >= 0
      && bodyText.indexOf('Tema 1') >= 0
      && (bodyText.match(/session-[0-9a-f]{8,}/g) || []).length === 0, bodyText);

    // ---- 整页可见文本里不许出现课堂哈希 (与 e2e_student_ui.js 同一条规则) ----
    const pageText = visibleText(out);
    check('the materials page never shows a raw session id as text',
      (pageText.match(/session-[0-9a-f]{8,}/g) || []).length === 0,
      (pageText.match(/session-[0-9a-f]{8,}/g) || []).slice(0, 2).join(', '));

    // ---- 课堂页副标题不再打印 id, 但保留日期 ----
    {
      const sessionPage = makeSandbox(appSource, courseId);
      await sessionPage.loadSidebar();
      await sessionPage.pageSession(courseId, first.session_id);
      const sessionOut = html(sessionPage);
      const subtitle = sessionOut.slice(sessionOut.indexOf('class="subtitle'));
      check('the session page subtitle keeps the date',
        subtitle.slice(0, 120).indexOf('2026-03-01') >= 0, subtitle.slice(0, 120));
      check('the session page subtitle no longer prints the raw id',
        sessionOut.indexOf(first.session_id) === -1
        || visibleText(sessionOut).indexOf(first.session_id) === -1,
        visibleText(sessionOut).slice(0, 160));
    }

    // ---- 只读契约: 渲染过程只发 GET ----
    const writes = sandbox.__requests.filter((r) => !r.startsWith('GET '));
    check('rendering the page issues no write requests', writes.length === 0, writes.join(', '));

    const second_id = second.session_id;
    check('the two live sessions have different ids', first.session_id !== second_id);
  } finally {
    server.kill();
    await new Promise((r) => setTimeout(r, 500));
    fs.rmSync(DATA_DIR, { recursive: true, force: true });
  }

  if (failures.length) {
    console.error('E2E (live, with sessions) FAILED (' + failures.length + '/' + checks + ')');
    for (const failure of failures) console.error('  ✗ ' + failure);
    process.exitCode = 1;
    return;
  }
  console.log('E2E (live, with sessions) OK (' + checks + ' checks) — 2 sessions, temp data dir');
})().catch((err) => {
  const stack = err && err.stack ? err.stack : String(err);
  failures.push('the run crashed — ' + stack.split('\n')[0]);
  console.error('E2E (live, with sessions) FAILED (' + failures.length + '/' + checks + ')');
  for (const failure of failures) console.error('  ✗ ' + failure);
  console.error(stack);
  process.exitCode = 1;
});
