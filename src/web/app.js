/*
 * 设计约束:
 * - 零构建链: 手写 Vanilla JS, 无框架、无打包器 (spec 原则: 不引入不必要依赖)。
 * - 只通过 /api/* 访问数据, 绝不直接读文件、绝不内联业务规则。
 * - 所有来自 API 的文本一律经 esc() 转义后再插入 DOM (原文是任意用户内容)。
 * - 原文 (西语 / 加泰语 / 中文) 一律按原样渲染, 不做任何改写或翻译。
 *
 * 路由使用 hash, 因此刷新 / 深链都不需要服务器端配合。
 *
 * P1-6 拆分: 本文件只保留**核心工具 + 共享状态 + 路由/启动**。
 * 各页面函数已按主题移到 views/*.js。加载顺序 (index.html 与此一致):
 *   api.js -> i18n.js -> app.js -> views/dashboard.js -> views/learn.js
 *   -> views/review.js -> views/knowledge.js -> views/materials.js
 *   -> views/courses.js -> views/students.js -> views/exercises.js
 *   -> views/mistakes.js
 * 多个 <script> 共享同一个全局作用域, 因此跨文件互相调用是安全的;
 * 但 **顶层** 初始化代码有顺序依赖 (见 i18n.js 头注释)。
 */
'use strict';

// ---------------------------------------------------------------- 通用工具

function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function dash(value) {
  if (value === null || value === undefined || value === '') return '—';
  return esc(value);
}

function fmtBytes(size) {
  const n = Number(size);
  if (!isFinite(n) || n < 0) return '—';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / (1024 * 1024)).toFixed(1) + ' MB';
}

function fmtNumber(value) {
  const n = Number(value);
  if (!isFinite(n)) return '—';
  return n.toFixed(2).replace(/\.00$/, '');
}

function pillClass(status) {
  const key = String(status || '').toUpperCase();
  if (['SUCCEEDED', 'COMPLETED', 'CONFIRMED', 'VALIDATED', 'OK', 'VERIFIED'].indexOf(key) >= 0) {
    return 'pill-ok';
  }
  if (['FAILED', 'REJECTED', 'CONFLICTED', 'ERROR'].indexOf(key) >= 0) return 'pill-bad';
  if (['QUEUED', 'PENDING', 'UNVERIFIED', 'RUNNING', 'NEEDS_VERIFICATION'].indexOf(key) >= 0) {
    return 'pill-warn';
  }
  if (['REGISTERED', 'VALIDATING', 'PROCESSING', 'CANCELLED'].indexOf(key) >= 0) return 'pill-info';
  return 'pill-muted';
}

function pill(label, status) {
  const text = label === undefined || label === null ? status : label;
  return '<span class="pill ' + pillClass(status) + '">' + esc(text) + '</span>';
}

// ---- 处理警告 (D1) 与"成功但零证据" (D4) --------------------------------
//
// 后端把"处理成功但有话要说"表达为材料记录上的 ``warning`` 码:
//
//   NO_TEXT_EXTRACTED          文档可解析但没有可提取文本 (扫描件 / 无文本层)
//   NO_TEXT_DETECTED           图片里没有可识别的文字区域
//   TRANSCRIPT_QUALITY_INVALID 转写质量判定不合格 (音频)
//   TRANSCRIPT_QUALITY_WARNING 转写质量偏低 (音频)
//
// 它**不是**错误。``COMPLETED`` + 零证据在契约里是合法成功
// (material_workflow._document_outcome: "属于合法成功, 但必须让用户看见"),
// 所以这里**不碰**状态 pill —— 它的类名与文案由 pill() 决定 (材料页是单参数
// 调用, 因此是中性色; 作业表是双参数调用, 会按状态着色, 那是既有语义),
// 只在下面加一行, 让"成功"和"什么都没提取到"同时可见。
//
// 码 -> 文案的映射逐个字面写出, 不做"前缀 + 码"的拼接: 拼前缀时
// 后端新增一个码而前端没加文案, ``t()`` 会**原样返回 key** (页面上多出一个
// ``warn.XXX``), 没有异常、没有日志; 静态 i18n 检查也抓不到 (源码里根本没有
// 那个字面 key)。未知码走 default: 只渲染码本身, 不猜含义。
//
// 注: 连**注释**里也不能出现 ``t(`` + 引号的形状 —— i18n 静态检查
// (tests/test_learning_view.py::TestI18nTables) 扫的是整份前端源码, 不做
// 注释剥离, 注释里的假 key 会被当成"用了但没定义"而报错 (实测踩过)。

function warningText(code) {
  switch (code) {
    case 'NO_TEXT_EXTRACTED': return t('warn.NO_TEXT_EXTRACTED');
    case 'NO_TEXT_DETECTED': return t('warn.NO_TEXT_DETECTED');
    case 'TRANSCRIPT_QUALITY_INVALID': return t('warn.TRANSCRIPT_QUALITY_INVALID');
    case 'TRANSCRIPT_QUALITY_WARNING': return t('warn.TRANSCRIPT_QUALITY_WARNING');
    default: return '';
  }
}

/**
 * 状态 pill 下面那行警告: ``原码`` (pill-warn) + 一句人话。
 *
 * 原码**逐字**渲染 (它是后端契约的一部分, 也是断言对象), 一律经 esc() ——
 * 来自 API 的字符串与原文同级对待。没有 warning 时返回空串。
 *
 * 人话为什么不在 pill 里: ``.pill`` 带 ``white-space: nowrap``, 整句西语
 * 会把"状态"列撑到几百像素; 放在 pill 外的 ``tiny muted`` 里可以正常折行。
 */
function warningRow(material) {
  const code = (material || {}).warning;
  if (!code) return '';
  const text = warningText(code);
  return '<br><span class="tiny pill pill-warn">' + esc(code) + '</span>' +
    (text && text !== code ? ' <span class="tiny muted">' + esc(text) + '</span>' : '');
}

/**
 * 材料记录里的证据条数; 字段缺失时返回 ``null`` (= **未知**)。
 *
 * ``evidence_ids`` 是数组 (材料注册表的写法), ``evidence_count`` 是整数
 * (作业字典的写法)。两者都没有时我们**不知道**证据数, 于是下面的提示
 * 选择不说 —— 猜一个"零证据"出来就等于编造结论。
 */
function evidenceCountOf(material) {
  const record = material || {};
  if (Array.isArray(record.evidence_ids)) return record.evidence_ids.length;
  const count = record.evidence_count;
  return typeof count === 'number' ? count : null;
}

/**
 * ``COMPLETED`` (或作业 ``SUCCEEDED``) **且零证据**时的一行提示。
 *
 * 用户看到"COMPLETED + 0 条证据"只会以为界面坏了, 所以要同时说清两件事:
 *   1. 这不是失败 —— 通用句 (``warn.emptySuccessHint``);
 *   2. 按 warning 码给一句可执行的话 —— 枚举穷举, 未知码/无码只说通用句。
 *
 * 判据是"API 给的证据数 == 0", 不是"没有证据字段": 字段缺失时不知道, 不说。
 */
function zeroEvidenceHint(material) {
  const record = material || {};
  const status = String(record.status || record.processing_status || '').toUpperCase();
  if (status !== 'COMPLETED' && status !== 'SUCCEEDED') return '';
  if (evidenceCountOf(record) !== 0) return '';
  let detail = '';
  switch (record.warning) {
    case 'NO_TEXT_EXTRACTED': detail = t('warn.empty.NO_TEXT_EXTRACTED'); break;
    case 'NO_TEXT_DETECTED': detail = t('warn.empty.NO_TEXT_DETECTED'); break;
    case 'TRANSCRIPT_QUALITY_INVALID': detail = t('warn.empty.TRANSCRIPT_QUALITY_INVALID'); break;
    case 'TRANSCRIPT_QUALITY_WARNING': detail = t('warn.empty.TRANSCRIPT_QUALITY_WARNING'); break;
    default: detail = '';
  }
  return '<br><span class="tiny muted">' + esc(t('warn.emptySuccessHint')) +
    (detail ? ' ' + esc(detail) : '') + '</span>';
}

function toast(message, kind) {
  const el = document.createElement('div');
  el.className = 'toast' + (kind ? ' toast-' + kind : '');
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => { el.remove(); }, kind === 'bad' ? 7000 : 3500);
}

// ---- 常驻任务坞 ----------------------------------------------------------
//
// 长任务 (处理 / AI 分析 / 整节处理 / 上传) 点下按钮就登记, fetch 在后台跑,
// 用户切到别的模块时任务不丢: 坞挂在 #view 之外, route() 重绘碰不到它。
// 完成/失败时更新状态 + toast; 面板回填只在用户还停在材料页时做。
//
// 2026-09-21 (P3-2): 服务端的处理是**同步**的 —— 音频转写可能持续数分钟, 而
// `tasks` 只在内存里。用户等待期间按 F5: 坞被清空、服务端仍在跑, 他手上没有
// 任何信号, 只能自己回材料页翻状态列。
//
// 现在**可轮询**的任务会把 (课程 + 目标 id) 落到 sessionStorage, 重载后按
// 后端投影把结局接回来。可轮询的判据是"服务端存在一个能回答'它跑完了吗'的
// 读接口":
//
//   material  GET /api/processing/{material_id}?course_id=   → job.status
//   session   GET /api/processing?course_id=&session_id=      → by_status
//
// 上传与 AI 分析**不**入存储, 理由不同且都是确定的:
//   上传   —— 目标 id 是服务端返回的, 而"用户按 F5"恰好意味着那个响应还没到,
//             此时没有任何 id 可存;
//   AI 分析 —— 读接口只暴露**已经存在**的报告 (job.ai / ai-summary), 没有
//             "正在分析"这个状态。存了它就只能要么永远转圈、要么替服务端
//             编一个结局 —— 两条都违反"绝不编造"。

const tasks = [];
let __taskSeq = 0;

//: sessionStorage 键。只存"运行中且可轮询"的任务, 终态即删。
const TASK_STORE_KEY = 'ca.tasks';

//: 作业终态。取值域与后端 processing_service.JOB_STATUSES 的差集一致
//: (JOB_STATUSES = QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED) —— 由
//: tests/test_web_ui_invariants.py 的静态断言盯着, 后端加枚举时它会红。
const TASK_TERMINAL_STATUSES = ['SUCCEEDED', 'FAILED', 'CANCELLED'];

//: 恢复后的轮询节奏与上限: 5 秒一拍、最多 60 拍 (5 分钟)。到点仍未终态就按
//: "结局未知"收尾 —— 绝不留一个永远转圈的坞, 也绝不替服务端宣布成功。
const TASK_POLL_INTERVAL_MS = 5000;
const TASK_POLL_MAX_TICKS = 60;

// 终态通知仍要短暂可见, 供用户读到结果/错误, 但不能永久占住任务坞。
// 两个时长是独立常量, Node harness 可替换 setTimeout 后精确推进, 不必真的等待。
const TASK_SUCCESS_VISIBLE_MS = 3000;
const TASK_FAILURE_VISIBLE_MS = 15000;

//: taskId → setTimeout 句柄。轮询与终态清理分开, 避免两套定时器互相覆盖。
const __taskPollers = {};
const __taskCleanupTimers = {};

function clearTaskTimer(timers, id) {
  if (timers[id] !== undefined) {
    clearTimeout(timers[id]);
    delete timers[id];
  }
}

function clearTaskCleanupTimer(id) {
  if (__taskCleanupTimers[id] !== undefined) {
    if (typeof window.clearTimeout === 'function') window.clearTimeout(__taskCleanupTimers[id]);
    delete __taskCleanupTimers[id];
  }
}

function clearTaskTimers(id) {
  clearTaskTimer(__taskPollers, id);
  clearTaskCleanupTimer(id);
}

function taskStoreRead() {
  try {
    const raw = window.sessionStorage.getItem(TASK_STORE_KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list : [];
  } catch (err) {
    // 存储被禁用 (隐私模式 / 企业策略) 或内容损坏 —— 都降级成"没有可恢复的
    // 任务"。坞里空着是正常状态, 但绝不因此把整个页面拖崩。
    void err;
    return [];
  }
}

function taskStoreWrite(list) {
  try {
    if (list.length) window.sessionStorage.setItem(TASK_STORE_KEY, JSON.stringify(list));
    else window.sessionStorage.removeItem(TASK_STORE_KEY);
  } catch (err) { void err; }
}

function taskStoreRemove(targetId) {
  taskStoreWrite(taskStoreRead().filter((item) => item.targetId !== targetId));
}

function taskTerminalStatus(status) {
  return TASK_TERMINAL_STATUSES.indexOf(String(status || '')) >= 0;
}
function taskKey(label, poll) {
  const metadata = poll || {};
  if (metadata.targetId !== undefined && metadata.targetId !== null && metadata.targetId !== '') {
    return 'target:' + String(metadata.targetId);
  }
  if (metadata.key !== undefined && metadata.key !== null && metadata.key !== '') {
    return 'key:' + String(metadata.key);
  }
  // 当前少数非持久化任务尚未单独传 key (上传文件名 / AI 材料 id 已在 label 中),
  // 所以用规范化后的 label 作为最后一道稳定身份。显式 key/targetId 始终优先。
  return 'label:' + String(label || '').trim().replace(/\s+/g, ' ');
}

/** 登记一个任务。可轮询任务给出 {courseId, targetId, targetKind}。 */
function startTask(label, poll) {
  const key = taskKey(label, poll);
  const existing = tasks.find((item) => item.key === key);
  if (existing) {
    // 同一处理中目标再次登记仍返回原 id。第二个请求即使先结束, 也会收尾这张卡,
    // 不会制造重复通知, 更不会留下一个永远等不到 finishTask 的孤儿 id。
    if (existing.status === 'running') return existing.id;
    // 终态重试不是重复通知: 清掉旧定时器/卡片, 立即恢复为新的 running 卡片。
    removeTask(existing.id);
  }
  const task = {
    id: ++__taskSeq,
    key: key,
    label: String(label || ''),
    status: 'running',
    detail: '',
  };
  if (poll && poll.targetId) {
    task.targetId = poll.targetId;
    taskStoreWrite(
      taskStoreRead()
        .filter((item) => item.targetId !== poll.targetId)
        .concat([{
          label: task.label,
          courseId: poll.courseId,
          targetId: poll.targetId,
          targetKind: poll.targetKind || 'material',
        }])
    );
  }
  tasks.unshift(task);
  // 只留最近 5 条。被挤掉的那条**不**从存储里删 —— 它可能还在服务端跑着,
  // 删了就等于把用户唯一的信号丢掉。它会在下次刷新时回到坞里并自行收敛。
  while (tasks.length > 5) removeTask(tasks[tasks.length - 1].id, false);
  renderTaskDock();
  return task.id;
}

function removeTask(id, render) {
  const index = tasks.findIndex((item) => item.id === id);
  clearTaskTimers(id);
  if (index >= 0) tasks.splice(index, 1);
  if (render !== false) renderTaskDock();
}

function finishTask(id, ok, detail) {
  const task = tasks.find((item) => item.id === id);
  // 即便卡片已因 5 条上限被挤掉, 也要保住旧契约: 收到结局仍停止该 id 的轮询。
  clearTaskTimer(__taskPollers, id);
  if (!task) return;
  if (task.targetId) taskStoreRemove(task.targetId);
  // 第一次终态是权威结果。重复 fetch / 并发请求不得重置 cleanup 定时器,
  // 否则同一个完成通知会被不断延后清理, 也会把 failed 改写成 succeeded.
  if (task.status !== 'running') return;
  task.status = ok ? 'done' : 'failed';
  task.detail = String(detail || '');
  // window timer 与 polling timer 分域: 旧审计桩只排轮询, 不会顺手清掉失败详情。
  if (typeof window.setTimeout === 'function') {
    clearTaskCleanupTimer(id);
    __taskCleanupTimers[id] = window.setTimeout(() => { removeTask(id); },
      ok ? TASK_SUCCESS_VISIBLE_MS : TASK_FAILURE_VISIBLE_MS);
  }
  renderTaskDock();
}




function renderTaskDock() {
  const dock = document.getElementById('task-dock');
  if (!dock) return;
  if (!tasks.length) { dock.innerHTML = ''; dock.hidden = true; return; }
  dock.hidden = false;
  dock.innerHTML = tasks.map((task) => {
    const pill = task.status === 'running'
      ? '<span class="pill pill-warn"><span class="task-spin">●</span> ' + esc(t('task.running')) + '</span>'
      : (task.status === 'done'
        ? '<span class="pill pill-ok">' + esc(t('task.done')) + '</span>'
        : '<span class="pill pill-bad">' + esc(t('task.failed')) + '</span>');
    return '<div class="task-item task-' + task.status + '">' + pill +
      '<span class="task-label">' + esc(task.label) +
      (task.detail ? '<br><span class="tiny muted">' + esc(task.detail) + '</span>' : '') +
      '</span></div>';
  }).join('');
}

function onTaskLane() {
  return (window.location.hash || '#/').indexOf('#/materials') === 0;
}

// ---- 任务坞的跨刷新恢复 (P3-2) -------------------------------------------

/** 作业终态 → 坞条目的 detail。成功报证据条数, 其余报状态 + 错误码。 */
function taskOutcomeDetail(job) {
  if (job.status === 'SUCCEEDED') {
    return '(' + ((job.evidence_ids || []).length) + t(' 条证据)');
  }
  return String(job.status || '') + (job.error ? ' · ' + job.error : '');
}

/**
 * 把一条已恢复的任务推进到下一个状态。``target`` 是
 * ``{id, courseId, targetId, targetKind, idleTicks}``。
 *
 * 三种结局, 三种都**有出处**, 没有一种靠猜:
 *   - 终态      —— 服务端直接回答的 (SUCCEEDED/FAILED/CANCELLED, 或
 *                  session 的 pending 归零);
 *   - 无记录    —— material 的读接口对未知材料**抛 404** (它不补建作业),
 *                  session 的读接口则补建, 所以后者要靠"连续两拍没有任何
 *                  作业被 enqueued"来判断 —— 服务端一旦收到 POST 就会先把
 *                  enqueued 置位再干活, 所以这个窗口是确定的;
 *   - 仍未结束  —— 继续等, 但最多 60 拍 (5 分钟), 到点按"结局未知"收尾。
 */
async function pollRestoredTask(target, ticks) {
  if (ticks > TASK_POLL_MAX_TICKS) {
    finishTask(target.id, false, t('超过 5 分钟仍未结束，无法确认结局。'));
    return;
  }
  try {
    if (target.targetKind === 'session') {
      const status = await api('/processing', {
        query: { course_id: target.courseId, session_id: target.targetId },
      });
      const counts = status.by_status || {};
      const pending = (counts.QUEUED || 0) + (counts.RUNNING || 0);
      if (pending === 0) {
        const total = status.total || 0;
        const failed = counts.FAILED || 0;
        finishTask(target.id, failed === 0, total + t(' 个材料, ') + failed + t(' 个失败'));
        return;
      }
      const touched = (status.jobs || []).some((job) => job.enqueued || job.status === 'RUNNING');
      if (!touched) {
        target.idleTicks = (target.idleTicks || 0) + 1;
        if (target.idleTicks >= 2) {
          finishTask(target.id, false, t('服务端没有这个任务的记录。'));
          return;
        }
      } else {
        target.idleTicks = 0;
      }
      const live = tasks.find((item) => item.id === target.id);
      if (live) live.detail = t('服务端仍在处理') + ' · ' + pending + t(' 个待处理');
    } else {
      const job = await api('/processing/' + encodeURIComponent(target.targetId), {
        query: { course_id: target.courseId },
      });
      if (taskTerminalStatus(job.status)) {
        finishTask(target.id, job.status === 'SUCCEEDED', taskOutcomeDetail(job));
        return;
      }
      const live = tasks.find((item) => item.id === target.id);
      if (live) live.detail = t('服务端仍在处理') + ' · ' + (job.stage || job.status);
    }
    renderTaskDock();
  } catch (err) {
    // 404 = 服务端**没有**这个作业 (重启过 / 材料被删)。这是确定的结论,
    // 按失败收尾并说明原因, 而不是留一个永远转圈的假进度条。
    if (err instanceof ApiError && err.code === 'NOT_FOUND') {
      finishTask(target.id, false, t('服务端没有这个任务的记录。'));
      return;
    }
    // 其它错误 (网络 / 服务不可用) 不构成任何关于结局的结论: 不编, 下一拍再试。
    const live = tasks.find((item) => item.id === target.id);
    if (live) live.detail = t('暂时查不到状态，正在重试');
    renderTaskDock();
  }
  if (typeof setTimeout === 'function') {
    __taskPollers[target.id] = setTimeout(
      () => { pollRestoredTask(target, ticks + 1); },
      TASK_POLL_INTERVAL_MS
    );
  }
}

/**
 * 重载后把刷新前仍在跑的任务接回坞里。只在启动时调一次。
 *
 * 先取走存储再逐条核对: 若这一步中途抛错, 也不会让同一批任务每刷新一次
 * 就重放一次。仍在中途的那些会在核对后**重新写回**存储, 所以恢复本身是
 * 幂等的 —— 连按两次 F5 只会得到同一条坞记录。
 */
async function restoreTaskDock() {
  const saved = taskStoreRead();
  if (!saved.length) return;
  taskStoreWrite([]);
  for (const entry of saved) {
    if (!entry || !entry.targetId) continue;
    const target = {
      courseId: entry.courseId,
      targetId: entry.targetId,
      targetKind: entry.targetKind || 'material',
      idleTicks: 0,
    };
    // label 用的是任务启动那一刻的界面语言 —— 重载后若用户换过语言, 它会
    // 保持旧语言。这是刻意取舍: 换一个"当前语言"的标签就只能靠 id 重新
    // 编一个人话, 而那正是本项目禁止的事。
    target.id = startTask(entry.label || entry.targetId, target);
    const live = tasks.find((item) => item.id === target.id);
    if (live) live.detail = t('刷新前启动的任务，正在向服务端核对状态…');
    renderTaskDock();
    await pollRestoredTask(target, 1);
  }
}

/**
 * 页顶横幅 —— **全局单例** (``index.html`` 里只有一个 ``#banner``)。
 *
 * ``kind`` 的取值域只有 ``'bad'`` 一个, 其余一律按警告色渲染。所以传一个
 * 不存在的 kind 会**静默降级成黄色** —— 没有异常、没有日志。2026-09-21 之前
 * ``views/learn.js`` 的四处错误分支传的就是 ``'error'``, 于是取题 / 交卷失败
 * 显示的是警告色而不是错误色。取值域现在由 ``tests/test_web_ui_invariants.py``
 * 的静态断言守着。
 *
 * 写入点只有两处: ``route()`` 开头的清空, 与 ``applyLoadNotes()`` 的合成。
 * 别处**不再**直接调用它 —— 一旦有人绕过 applyLoadNotes 自己写横幅, 并行
 * 加载的两条结论就重新变成"谁后写谁赢"。
 *
 * ``text`` 为空 = 清空并隐藏。清空点只有一处: ``route()`` 开头 —— 横幅曾经
 * 从不被清空, 一条「课程选择判定失败」会跨所有后续路由一直挂在页顶。
 */
function showBanner(text, kind) {
  const el = document.getElementById('banner');
  if (!text) { el.hidden = true; el.textContent = ''; return; }
  el.className = 'banner' + (kind === 'bad' ? ' banner-bad' : '');
  el.textContent = text;
  el.hidden = false;
}

function emptyState(text) {
  return '<p class="empty">' + esc(text) + '</p>';
}

/**
 * 表格的**无障碍名字** (``<caption class="sr-only">``)。
 *
 * 为什么需要它: ``scope="col"`` 只能告诉屏幕阅读器"这一格属于第 3 列",
 * 说不出"第 3 列是**哪张表**的"。卡片标题 (``<h2>``) 在 ``<table>``
 * **外面**, 所以进入表格后那个上下文就丢了 —— 课程页的课堂表有 8 列,
 * 错题表有 7 列, 逐格朗读时会完全失去参照。
 *
 * 标签一律**复用卡片标题用的同一个 key**, 不另起一套词汇; ``sr-only``
 * 让它视觉上不出现, 因此不会与卡片上的 h2 重复显示同一句话。
 */
function tableCaption(label) {
  return '<caption class="sr-only">' + esc(label) + '</caption>';
}

/**
 * 破坏性动作的二次确认。
 *
 * 只加在**同时**满足两条的动作上:
 *
 * 1. 写下去的是**人工终态** —— 点错了必须再走一次人工决策才能改回,
 *    而且审核历史里会留下一次本不存在的决策;
 * 2. 代价高 —— 例如批量启动一个可能持续数分钟的处理任务。
 *
 * 正向路径 (确认 / 保持未验证) 与可重试的动作 (重试 / 单份处理) **不加** ——
 * 每次都弹窗只会让人条件反射地点"确定", 反而让真正危险的那次失去意义。
 *
 * 包一层而不是直接写 ``window.confirm`` 的两个理由: 让"哪些动作要确认"
 * 集中可见; 以及**不在 DOM 桩里做静默降级** —— 桩没有 ``confirm`` 就让它
 * 直接抛, 而不是假装用户点了"确定"。
 */
function confirmDestructive(message) {
  return window.confirm(message);
}

/**
 * "该课程还没有学生" 的统一空态。
 *
 * 注册入口**只有一处** (学生页的表单), 所以这里只放一个链接, 不再在练习页
 * 复刻一份表单 —— 同一条规则抄两遍, 迟早有一遍会漏。
 */
function noStudentsCard() {
  return '<p class="muted">' + esc(t('student.none')) + '</p>' +
    '<p><a class="btn" href="#/students">' + esc(t('student.register')) + '</a></p>';
}

function sourceLocation(source) {
  if (!source) return '—';
  const parts = [];
  if (source.page !== null && source.page !== undefined) parts.push('p.' + source.page);
  if (source.line !== null && source.line !== undefined) parts.push('line ' + source.line);
  if (source.paragraph !== null && source.paragraph !== undefined) {
    parts.push('¶' + source.paragraph);
  }
  if (source.timestamp_start !== null && source.timestamp_start !== undefined) {
    parts.push(source.timestamp_start + (source.timestamp_end ? '–' + source.timestamp_end : ''));
  }
  if (source.location) parts.push(source.location);
  return parts.length ? parts.join(' · ') : '—';
}

function originalBlock(text, language) {
  return (
    '<div class="original">' + esc(text || '') +
    (language ? ' <span class="lang-tag">[' + esc(language) + ']</span>' : '') +
    '</div>'
  );
}

// ------------------------------------------------------------ 全局界面状态

//: UI 可选语言。**必须与后端白名单一致** ——
//: ``src/application/learning_view.py`` 的 ``UI_LANGUAGES``。
//: 前端多一种后端不认的语言, 后果不是"多一个选项", 而是
//: ``normalize_language()`` 抛 ``InvalidInputError`` —— 用户会看到一个错误页,
//: 而他从没选过那种语言。两份清单由
//: ``test_ui_language_picker_matches_the_service_whitelist`` 逐项核对。

const state = {
  courseId: window.localStorage.getItem('ca.course') || null,
  // 2026-09-22: 全局页 (概览/今日) 的**查看范围** —— 'all' 或某门课的 id。
  // 刻意**不持久化**: 用户回到概览时默认仍然是"全部课程" (任务书 §11),
  // 所以它只活在内存里, 每次开新标签页都回到默认值。
  scope: 'all',
  lang: pickInitialLang(),
  studentId: window.localStorage.getItem('ca.student') || null,
  // Task 65: 错题本分组方式。只影响显示, 不改变任何数据。
  mistakeGroupBy: window.localStorage.getItem('ca.mistakeGroup') || 'knowledge',
  health: null,
};

// Task 64: 单题页最后发起的"出题依据链"请求。
// 保留引用是为了让 UI 测试可以等它结束, 也避免未处理的 rejection 静默丢失。
let __lastGrounding = null;

/**
 * 当前正在渲染的页面 (由 route() 与页面函数**声明**, 不隨时反解析 hash)。
 *
 * 顶栏选择器在全局页 / 课程页上有两种语义 (见 renderCourseSwitcher), 它需要
 * 知道"现在屏幕上是哪一页"。反解析 hash 看似等价, 实际有两个洞:
 *   1. 页面函数可以被直接调用 (脚本/审计/测试就是这么做的), 此时 hash 还停
 *      在上一页 —— 视图与 hash 短暂不一致, 反解析读到的是**上一页**;
 *   2. `#/today` 与 `#/learn` 共享同一条顶栏高亮, hash 前缀分不清"哪一页
 *      真正在渲染"。
 * 所以沿用 markActiveNav() 的同一条架构规则: **界面归属由页面自己声明**。
 * 初值 null = "还没有任何页面渲染" —— 此时选择器按课程页语义处理 (与旧版
 * 行为一致), 只有真的渲染出全局页才切换语义。
 */
let __activeRoute = null;

/** 声明"现在渲染的是这个 hash"。与 markActiveNav() 同一条声明式契约。 */
function declareRoute(hash) {
  __activeRoute = hash || '#/';
}

/**
 * 这条路由是不是**全局页** —— 内容不跟随"当前课程" (state.courseId)。
 *
 * 页面层级 (2026-09-22, 任务书 §2):
 *   全局页   ``#/`` (概览) / ``#/today`` —— 默认按全部课程聚合, 顶栏选择器
 *            在这两页上只作**本页筛选** (state.scope), 不改 course context;
 *   课程页   其余全部 —— 数据跟随 state.courseId。
 *
 * switchCourse() 用它来决定换课后要不要把 scope 归位; renderCourseSwitcher()
 * 用它来决定选择器的语义 (课程上下文 vs 本页筛选)。
 */
function isGlobalRoute() {
  if (__activeRoute === null) return false;
  const parts = hashParts(__activeRoute);
  return parts.length === 0 || parts[0] === 'today';
}

/** parseHash() 的纯函数版: 解析任意 hash 字符串, 不读 window。 */
function hashParts(raw) {
  return String(raw || '')
    .replace(/^#/, '')
    .split('/')
    .filter((part) => part.length > 0)
    .map(decodeURIComponent);
}

/** 当前页实际生效的"查看范围": 全局页用 state.scope, 课程页恒为当前课程。 */
function currentScope() {
  if (!isGlobalRoute()) return state.courseId;
  return state.scope === 'all' ? null : state.scope;
}

/**
 * 全局页的查看范围。唯一写入口 (与 setCourse / setStudent 同一条收口规则)。
 * 只改内存 —— 刻意不持久化, 见 state.scope 上的注释。
 */
function setScope(courseId) {
  state.scope = courseId || 'all';
}

// ---------------------------------------------------------------- 界面语言
//
// spec Task 40: UI 可选 Spanish / Catalan / Chinese。
// 硬性约束: 语言选择**只影响界面文案与解释请求的语言标签**,
// 绝不修改任何 Evidence、绝不翻译任何原文。


function stateLabel(value) {
  const key = 'state.' + String(value || '');
  const label = t(key);
  return label === key ? String(value || '') : label;
}

function statusLabel(value) {
  const key = 'status.' + String(value || '');
  const label = t(key);
  return label === key ? String(value || '') : label;
}

/**
 * 课堂状态 (Task 56.3) 的人话标签。
 *
 * 六个状态在后端按固定判据推导, 前端**只做显示** —— 绝不因为"看起来像
 * 处理完了"就把它显示成"已确认"。特别注意 ``READY_TO_STUDY``: 它只表示
 * 没有挂起的人工审核, **不是** 掌握度。
 */
function sessionStatusLabel(value) {
  const key = 'session.' + String(value || '');
  const label = t(key);
  return label === key ? String(value || '') : label;
}

/**
 * 处理阶段标签 (Task 57.1): 7 个阶段的可本地化名称。
 * 阶段 key 由后端 ``processing.stages`` 给出, 前端只做映射 + 翻译, 不发明阶段。
 */
function stageLabel(value) {
  const map = {
    PREPARING: '准备',
    PROCESSING_MATERIALS: '处理材料',
    EXTRACTING_EVIDENCE: '提取证据',
    ASSEMBLING_KNOWLEDGE: '组装知识',
    VALIDATING: '校验',
    CHECKING_CONFLICTS: '冲突检查',
    FINISHED: '完成',
  };
  const key = map[String(value || '')] || String(value || '');
  const label = t(key);
  return label === key ? String(value || '') : label;
}

/**
 * 7 阶段处理步进器 (Task 57.1)。
 * 每个阶段由后端 ``stages[].done`` 决定完成态; 前端不自己判断"是否完成",
 * 否则"可观测状态"会变成第二真相源 (违反 Invariant 7)。
 */
function processingStepper(stages) {
  if (!stages || !stages.length) {
    return '<p class="muted small">' + esc(t('没有处理步骤可显示。')) + '</p>';
  }
  return '<ol class="stepper">' + stages.map((st) => {
    const cls = st.done ? 'done' : 'pending';
    return '<li class="' + cls + '"><span class="dot"></span>' +
      esc(stageLabel(st.key)) + '</li>';
  }).join('') + '</ol>';
}

/** 课程身份串: code · language (都是已有字段, 不拼新的事实)。
 *
 * **不含 course_id**。它曾经是 course_id · code · language, 靠哈希在两门同名
 * 课程之间区分; 但哈希对用户零信息量, 而 code 已经足够: course_id 由
 * (name, code) 派生 (见 models.Course._generate_stable_id), 所以两门不同的课
 * 必然在 name 或 code 上不同 —— 名称在上一行, 代码在这一行, 合起来是**完整**
 * 的判别依据。侧边栏 (courseListHtml) 按同一规则显示代码。
 *
 * 于是"用户可见文本里不出现内容寻址的 course_id"是一条**没有例外**的规则,
 * 由 tests/test_student_ui.py 的静态判据 + scripts/ui_audit.js 的渲染判据盯着。
 *
 * 这是身份串的**唯一**生产者: 需要显示身份串的地方都调这里, 不要内联再拼一份
 * (我的课程卡片曾经内联复制过一遍 —— 两份输出当时一样, 所以没有任何测试会红,
 * 但往这里加字段时那份副本就会悄悄漏掉)。
 */
function courseIdentity(course) {
  const parts = [];
  if (course && course.code) parts.push(course.code);
  if (course && course.language) parts.push(course.language);
  return parts.join(' · ');
}

/**
 * 一条证据的展示: 原文 + 类型 + 源位置 + 来源材料。
 *
 * ``material`` 为 null 时**必须**显式报 "Traceability broken" —— 断链不能
 * 被渲染成"没有来源"。
 */
function evidenceList(items, materials) {
  return '<ul class="small">' + (items || []).map((item) => {
    const loc = sourceLocation(item.source);
    return '<li>' + originalBlock(item.content, item.language) +
      '<div class="tiny muted mono">' + pill(item.evidence_type, 'REGISTERED') + ' ' +
      esc(loc === '—' ? t('ws.timeUnavailable') : loc) +
      ' · ' + esc(t('ws.material')) + ': ' +
      (item.material
        ? esc(item.material.filename || item.material.material_id)
        : '<span class="pill pill-bad">' + esc(t('ws.linkBroken')) + '</span>') +
      '</div></li>';
  }).join('') + '</ul>';
}

/** 证据分区卡片 (Transcript / OCR / Documents)。 */
function evidenceCard(title, items, emptyText, materials) {
  const list = items || [];
  return '<div class="card"><div class="card-head"><h2>' + esc(title) + ' (' +
    esc(list.length) + ')</h2></div>' +
    (list.length ? evidenceList(list, materials) : emptyState(emptyText)) + '</div>';
}

/** 只翻译带 data-i18n 的界面文案, 绝不触碰任何原文内容。 */

function setCourse(courseId) {
  state.courseId = courseId || null;
  if (courseId) window.localStorage.setItem('ca.course', courseId);
  else window.localStorage.removeItem('ca.course');
  syncCourseChrome();
}

/**
 * 路由里带过来的 course_id —— 只在它确实存在于课程列表时才认作"当前课程"。
 *
 * 路由里的 id 是**请求**, 不是既成事实: 手输错、失效的书签、课程被删之后的
 * 深链都会给出一个不存在的 id。直接 setCourse() 把它写进 state 与 localStorage
 * 的后果是三重的, 而且都属于"选中的课和高亮的课不是同一门"这一类:
 *   1. 侧边栏**没有任何**高亮;
 *   2. 顶栏切换器找不到匹配项, 退回显示第一项 —— 显示的和"当前课程"不是同一门;
 *   3. 此后每个页面 (概览 / 知识点 / 材料 …) 都拿着这个脏 id 去请求, 全部 404,
 *      用户被卡在错误页上直到他重新点一门课。
 *
 * 判断不花任何请求: 课程列表刚由 loadSidebar() 放进 __courseCache, 而列表在
 * 每次路由时都会重新拉取, 所以它对当前这次路由是新的。列表还没拿到 (加载失败)
 * 时不阻断 —— 没有依据就不下判断, 照常提交。
 *
 * 返回是否认下了这个 id。
 */
function setRouteCourse(courseId) {
  if (__courseCache.length && !__courseCache.some((c) => c.course_id === courseId)) {
    return false;
  }
  setCourse(courseId);
  return true;
}

/**
 * 课程上下文切换的**唯一**入口 (2026-09-22): 只换 ``currentCourseId``,
 * 不碰 ``currentRoute``。
 *
 * 根因: 侧边栏原来是直链 ``#/courses/<id>``, 顶栏切换器原来是
 * ``setCourse()`` + ``location.hash = '#/courses/<id>'`` —— 于是"换一门课"
 * 恒等于"进入课程详情", 用户在概览/今日/材料等 11 个顶层功能页换课后都
 * 被踢到课程详情, 还得再点一次顶部导航。
 *
 * 规则 (顶层功能页的 courseId 只活在全局 state 里, 不在 URL 里):
 *   - 顶层功能页 (``#/`` / ``#/today`` / ``#/materials`` … / ``#/courses``):
 *     hash 一字不动, 原地 ``route()`` 重渲染 —— 数据查询自动带上新课程。
 *   - 课程作用域详情 (``#/courses/<旧id>(/…)``): courseId 长在 URL 里,
 *     不换 URL 的话下一次路由的 ``setRouteCourse()`` 会把状态翻回旧课程,
 *     所以换到新课程的详情页 (``#/courses/<新id>``, 同一次 hash 赋值即
 *     push 一条历史, 与修复前用的 ``location.hash =`` 语义一致)。
 *   - 错题详情 (``#/mistakes/<旧id>/<kp>``): 同样把 courseId 绣进 URL,
 *     但那节的 id 在新课程下必然 404, 所以退到错题本列表 (同功能区)。
 *
 * 历史记录: 原地重渲染**不**产生新历史 (route 本来就没变, 不该污染
 * Back/Forward); 详情族的换 URL 沿用 ``location.hash =`` (push), 与修复前一致。
 * 刷新: 选择持久化在 ``ca.course`` (``setCourse()``), 与修复前一致。
 */
async function switchCourse(courseId) {
  if (!courseId) return;
  if (courseId === state.courseId) return;
  setCourse(courseId);
  // scope 筛选**不跟着换课走**: 课程页换课 = 换上下文 (本页没有筛选);
  // 全局页换课 = 只换上下文、视图回"全部课程" —— 任务书 §5: "当前课程 =
  // Gestió de Projectes 不应该导致概览 = 只显示 Gestió de Projectes"。
  // 要单课程视图, 用顶栏的查看范围选择器 (state.scope)。
  setScope('all');
  if (isGlobalRoute()) {
    await route();
    return;
  }
  const parts = parseHash();
  if (parts[0] === 'courses' && parts.length >= 2) {
    const target = '#/courses/' + encodeURIComponent(courseId);
    if (window.location.hash !== target) window.location.hash = target;
    else await route();
  } else if (parts[0] === 'mistakes' && parts.length === 3) {
    window.location.hash = '#/mistakes';
  } else {
    await route();
  }
}

/**
 * 当前学生。和 ``setCourse()`` 同理 —— 唯一的写入口, 内存与持久化一起改。
 *
 * 为什么必须收口: 原来 6 处页面函数各自写一遍 ``state.studentId`` +
 * ``localStorage``, 其中 ``pageExercise()`` **漏了持久化那一步**。后果是
 * 从错题本点「Practice Again」进 `#/courses/<c>/exercises/<e>/<sid>` 之后,
 * 内存里是 sid, localStorage 里还是上一个学生 —— 刷新一下, 学生就悄悄换人了。
 * 同一个规则写 6 遍, 漏掉其中一遍几乎是必然的。
 */
function setStudent(studentId) {
  state.studentId = studentId || null;
  if (studentId) window.localStorage.setItem('ca.student', studentId);
  else window.localStorage.removeItem('ca.student');
}

// ------------------------------------------------------------------ 顶栏

/**
 * 页顶横幅的**唯一**写入口之一 (另一个是 ``route()`` 开头的清空)。
 *
 * ``loadChrome`` / ``loadSidebar`` 现在**并行**跑 (P3-1), 而横幅是全局单例
 * —— 谁后写谁赢就变成竞态。所以两者都不再直接写横幅, 而是把结论返回, 由
 * ``route()`` 交给这里按固定顺序合成。顺序即严重度, 最重的排最前。
 *
 * ``kind`` 取其中最严重的一个: 只要有一条是 ``'bad'``, 整条横幅就是错误色。
 */
function applyLoadNotes(notes) {
  const kept = notes.filter(Boolean);
  if (!kept.length) return;
  const bad = kept.some((note) => note.kind === 'bad');
  showBanner(kept.map((note) => note.text).join(' '), bad ? 'bad' : undefined);
}

/**
 * 顶栏的两个 pill + 页脚版本号。
 *
 * @returns {?{text: string, kind?: string}} 需要报给用户的一句话, 或 null。
 *   **不再直接写横幅** —— 见 ``applyLoadNotes``。
 */
async function loadChrome() {
  // 界面语言只作用于带 data-i18n 的界面文案; 原文 / 证据内容永不翻译。
  applyI18n();
  try {
    const health = await api('/health');
    state.health = health;
    const ok = health.status === 'ok';
    const el = document.getElementById('pill-health');
    el.className = 'pill ' + (ok ? 'pill-ok' : 'pill-warn');
    el.textContent = health.application + ' v' + health.version + ' · ' + health.status;
    const modes = document.getElementById('pill-modes');
    const asr = (health.processing && health.processing.asr) || '?';
    const ocr = (health.processing && health.processing.ocr) || '?';
    modes.className = 'pill ' + (asr === 'mock' || ocr === 'mock' ? 'pill-warn' : 'pill-ok');
    modes.textContent = 'ASR ' + asr + ' · OCR ' + ocr;

    const aiHealth = health.ai || {};
    const aiMode = String(aiHealth.mode || health.ai_mode || 'unknown');
    const aiEnabled = aiHealth.enabled !== undefined
      ? Boolean(aiHealth.enabled)
      : Boolean(health.ai_enabled);
    const aiPill = document.getElementById('pill-ai');
    if (aiPill) {
      const aiClass = aiMode === 'real' && aiEnabled
        ? 'pill-ok'
        : (aiMode === 'fake' || aiMode === 'disabled' ? 'pill-warn' : 'pill-muted');
      aiPill.className = 'pill ' + aiClass;
      aiPill.textContent = 'AI ' + aiMode;
    }

    document.getElementById('footer-app').textContent =
      health.application + ' v' + health.version;
    const count = health.processing && health.processing.evidence_count;
    const storage = health.storage && health.storage.courses;
    modes.title = t('证据总数 ') + count + t(' · 课程数 ') + storage;
    const notes = [];
    if (asr === 'mock' || ocr === 'mock') {
      notes.push(t('ai.healthMock'));
    }
    if (aiMode === 'disabled' || !aiEnabled) {
      notes.push(t('ai.healthDisabled'));
    } else if (aiMode === 'fake') {
      notes.push(t('ai.healthFake'));
    } else if (aiMode !== 'real') {
      notes.push(t('ai.healthUnknown'));
    }
    return notes.length ? { text: notes.join(' ') } : null;
  } catch (err) {
    const el = document.getElementById('pill-health');
    el.className = 'pill pill-bad';
    el.textContent = t('服务不可用');
    return { text: t('无法访问本机 API: ') + err.message, kind: 'bad' };
  }
}

// ---- Task 68: 侧边栏课程列表 ---------------------------------------------

// 已加载的课程列表缓存。``setCourse()`` 要靠它重绘"当前课程"标记 —— 页面函数
// 在 loadSidebar() 之后才拿到路由里的 course_id, 缓存让那次重绘成为可能。
let __courseCache = [];

/** 侧边栏课程列表的 HTML。高亮项 = state.courseId, 与主视图同源。
 *
 * 每个课程项两行: **课程名** + **课程代码** (GP / MC / ...)。第二行**不是**
 * course_id —— 那是内容寻址的内部标识, 对用户零信息量 (用户 2026-09-20 报的
 * 就是"界面上出现 course-32dde014868219be")。显示代码同样能区分同名课程:
 * course_id 由 (name, code) 派生 (见 models.Course._generate_stable_id),
 * 所以两门不同的课必然在 name 或 code 上不同。
 * course_id 只进 href 属性, 不进可见文本。
 *
 * 课程上下文与当前页面解耦 (2026-09-22): 侧边栏是**课程选择器**, 不是导航。
 * 纯左键点击由文档级的 ``a[data-course-switch]`` 拦截, 只换 course context
 * (``switchCourse()``)、hash 一字不动; ``href`` 保留课程详情地址, 只做两件事:
 * 修饰键/中键/新标签页打开时的有效 fallback (仍是合法深链), 以及无 JS 时的
 * 可访问性。真正的"查看详情"入口是「我的课程」卡片上的按钮。
 */
function courseListHtml(courses) {
  return courses
    .map((course) => {
      const active = course.course_id === state.courseId ? ' class="active"' : '';
      // 没有代码的课程就不渲染第二行 —— 名称本身已经够区分 (见上面的注释)。
      const code = course.code
        ? '<br><span class="tiny muted mono">' + esc(course.code) + '</span>'
        : '';
      return (
        '<li><a' + active + ' href="#/courses/' + encodeURIComponent(course.course_id) + '"' +
        ' data-course-switch="' + esc(course.course_id) + '">' +
        esc(course.name) + code + '</a></li>'
      );
    })
    .join('');
}

/**
 * 把"当前课程"的两处显示同步到 state.courseId (侧边栏高亮 + 顶栏切换器)。
 *
 * 只改显示, 不碰任何数据, 也不发任何请求 —— 课程列表已经在 __courseCache 里。
 * 缓存为空 (列表还没加载 / 加载失败) 时什么都不做: 这时候没有任何可同步的显示,
 * 而贸然重绘反而会把错误提示覆盖成空列表。
 */
function syncCourseChrome() {
  if (!__courseCache.length) return;
  const list = document.getElementById('course-list');
  if (list) list.innerHTML = courseListHtml(__courseCache);
  renderCourseSwitcher(__courseCache);
}

/**
 * 课程的**显示名** —— 用户界面里一律显示名称, 不显示内容寻址的 course_id。
 *
 * ``course-3fd6392d1fd78e87`` 是内部标识 (由课程名等内容派生), 对用户没有任何
 * 信息量; 同名课程由**课程代码**区分 (侧边栏与 courseIdentity() 都显示 code)。
 * 主视图里 "课程 <X>" 这类**标题性**位置只显示名称。
 *
 * 名称来自 ``__courseCache`` (每次路由由 ``loadSidebar()`` 刷新), 不额外发请求。
 * 缓存里没有这门课时退回 course_id —— 宁可显示一个不漂亮的标识, 也不要显示
 * 空白, 更不要自造一个"未命名"。
 */
function courseLabel(courseId) {
  if (!courseId) return '';
  const found = __courseCache.filter((c) => c.course_id === courseId)[0];
  return (found && found.name) || courseId;
}

/** ``YYYY-MM-DD`` / ``YYYY-MM-DDTHH:mm`` -> 本地化日期与星期。
 * 日期取 API 的日历值，星期只从该真实值派生；非法 / 空日期原样返回，不猜。
 * 课表日期必须用年月日构造本地时区 Date；``new Date('2026-09-24')`` 是 UTC
 * 午夜，在西半球会算错星期。 */
function sessionDateLabel(date) {
  const value = String(date || '').trim();
  if (!value) return '';
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T\s].*)?$/.exec(value);
  if (!match || typeof Intl === 'undefined' || !Intl.DateTimeFormat) {
    return value;
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const localDate = new Date(year, month - 1, day);
  if (
    localDate.getFullYear() !== year
    || localDate.getMonth() !== month - 1
    || localDate.getDate() !== day
  ) return value;
  try {
    const locale = { zh: 'zh-CN', es: 'es-ES', ca: 'ca-ES' }[state.lang] || 'zh-CN';
    let dateText = new Intl.DateTimeFormat(locale, {
      year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(localDate);
    let weekday = new Intl.DateTimeFormat(locale, { weekday: 'long' })
      .format(localDate);
    if (state.lang === 'zh') {
      dateText = dateText.replace(/\//g, '-');
      weekday = weekday.replace(/^星期/, '');
      // Chromium/Node 的 zh-CN long weekday 可能是「星期四」或「四」；
      // 用户要求统一显示「周四」，不要依赖运行时的简写差异。
      if (!/^周[日一二三四五六]$/.test(weekday)) weekday = '周' + weekday;
      return dateText + '（' + weekday + '）';
    }
    return dateText + ' (' + weekday.toLowerCase() + ')';
  } catch (err) {
    return value;
  }
}

/** Structured display fields shared by the picker and the materials table. */
function sessionDisplayParts(session) {
  if (!session) return { date: '', number: '', time: '', kind: '', mid: '', room: '' };
  const parsed = parseSessionTitle(session.title);
  const number = Number(session.session_number || 0);
  return {
    date: sessionDateLabel(session.date),
    number: number > 0 ? t('第 ') + String(number) + t(' 堂') : '',
    time: parsed.time,
    kind: parsed.kind || (!parsed.time ? parsed.head : ''),
    mid: parsed.mid,
    room: parsed.room,
  };
}

/** The one human-readable Session label used by every selector and link. */
function sessionLabel(session, fallback) {
  if (!session) return fallback || '';
  const parts = sessionDisplayParts(session);
  const fields = [parts.date, parts.number, parts.time, parts.kind, parts.mid, parts.room]
    .filter(Boolean);
  return fields.length ? fields.join(' · ') : (fallback || '');
}

/**
 * 侧边栏课程列表 + 「该看哪门课」的判定。
 *
 * @returns {?{text: string, kind?: string}} 需要报给用户的一句话, 或 null。
 *   与 ``loadChrome`` 一样**不直接写横幅** (两者并行, 见 applyLoadNotes)。
 *   列表本身通过 ``__courseCache`` 与 DOM 生效, 不靠返回值。
 */
async function loadSidebar() {
  const list = document.getElementById('course-list');
  let courses = [];
  try {
    courses = (await api('/courses')).courses || [];
  } catch (err) {
    __courseCache = [];
    list.innerHTML = '<li class="muted small">' + t('课程列表加载失败') + '</li>';
    renderCourseSwitcher([]);
    // 服务不可用时 ``loadChrome`` 已经报了「无法访问本机 API」—— 这里不再叠
    // 一条同因的横幅 (横幅只有一条, 重复只会挤掉别的话)。
    return null;
  }
  // 先入缓存再走选择判定: 判定里的 setCourse() 需要它来重绘侧边栏。
  __courseCache = courses;
  if (!courses.length) {
    list.innerHTML = '<li class="muted small">' + t('暂无课程') + '</li>';
    renderCourseSwitcher([]);
    return null;
  }
  // Task 68: "该看哪门课"由**后端规则**判定 (preferred / preferred_missing /
  // first_course / no_courses), 前端不再自己写一遍。
  //
  // 为什么不能前端自己写: localStorage 里的 course_id 可能指向一门已经被
  // 删掉的课。那种情况要退回第一门课 —— 这条规则写两遍 (前端一遍后端一遍)
  // 迟早会不一致, 而它一旦不一致, 用户看到的就是 404 而不是自己的课程表。
  let note = null;
  try {
    const selection = await api('/course-selection', {
      query: { preferred: state.courseId || undefined },
    });
    if (selection.course_id && selection.course_id !== state.courseId) {
      setCourse(selection.course_id);
    }
  } catch (err) {
    // 选择请求失败不该让整个侧边栏消失 —— 列表已经拿到了, 照常渲染。
    note = { text: t('课程选择判定失败，已按列表顺序显示。') };
  }
  syncCourseChrome();
  return note;
}

// Task 68: 顶栏课程切换器。
//
// 用户更常见的动作是"我现在想换一门课看", 这时候要一个不离开当前页结构
// 就能换的控件。选中即 ``switchCourse()`` (2026-09-22 前是 ``setCourse()``
// + 跳课程详情, 见 switchCourse 上面的根因) —— 与侧边栏点击走的是同一个
// 上下文入口, 持久化仍收口在 ``setCourse()`` 里, 所以刷新/重启之后的选择
// 是一致的 (数据来源是 localStorage, 且由 /api/course-selection
// 判定这个偏好是否还成立)。
let __courseSwitchWired = false;

function renderCourseSwitcher(courses) {
  const picker = document.getElementById('course-switch');
  if (!picker) return;
  // 选择器的语义随页面而变 (2026-09-22):
  //   全局页 (概览/今日) —— 它是**本页的查看范围**。第一项是「全部课程」
  //     (value='') 且是默认选中项; 选一门课只把这一页筛成那门课
  //     (setScope + route), 不改 course context。
  //   其余页面 —— 它仍是**课程上下文**切换器, 行为与 2026-09-22 解耦修复
  //     后一致: 选中即 switchCourse()。
  // 全局页与课程页共用同一个 <select> (index.html 里只有一个), 选项与选中项
  // 每次重绘都按当前页面重建; 只接一次线 (同 switchCourse 的理由)。
  const globalMode = isGlobalRoute();
  const selectedId = globalMode ? (state.scope === 'all' ? '' : state.scope) : state.courseId;
  picker.innerHTML = (globalMode
    ? [{ id: '', label: t('scope.all') }]
    : []).concat(courses.map((course) => ({
      id: course.course_id,
      // 名称缺失时退回代码 —— 绝不显示内容寻址的 course_id。转义在这里做
      // 一次 (option 文案位置), 内层 map 只拼壳, 不再出现第二次来源。
      label: esc(course.name || course.code || ''),
    })))
    .map((item) => (
      '<option value="' + esc(item.id) + '"' +
      (item.id === selectedId ? ' selected' : '') + '>' +
      item.label + '</option>'
    ))
    .join('');
  // 只接一次线。每次重绘都 addEventListener 的话, 一次切换会触发 N 次跳转。
  if (!__courseSwitchWired) {
    __courseSwitchWired = true;
    picker.addEventListener('change', () => {
      const courseId = picker.value || null;
      if (isGlobalRoute()) {
        // 查看范围: 只筛本页, 不碰课程上下文, 也不写 localStorage ——
        // 筛选不持久化, 回到概览默认仍是"全部课程" (任务书 §11)。
        if (state.scope === (courseId || 'all')) return;
        setScope(courseId);
        route();
        return;
      }
      if (!courseId) return;
      // 只换课程上下文, 不强制进课程详情 —— 去留由 switchCourse 按当前
      // 路由决定 (顶层功能页原地重渲染, 课程详情族才换 URL)。
      switchCourse(courseId);
    });
  }
}

/**
 * 顶栏导航的高亮。
 *
 * 高亮**完全由页面函数声明** —— ``index.html`` 的 topnav 里没有任何硬编码
 * ``active``。所以"没人调用它"和"高亮留在上一页"是同一件事: 漏调一个页面函数,
 * 那一页的高亮就继承上一页; 若是刷新后直接落在这一页, 整条导航都没有高亮。
 * 因此**每个** pageXxx() 都必须恰好调用一次 (由
 * ``test_every_page_function_sets_the_top_nav_highlight`` 清点)。
 *
 * 归属规则 —— **跟随用户所在页面所属的顶栏分区; 不属于任何分区的页面清空**:
 *
 *   顶栏项              归属它的路由
 *   #/today             #/today, #/learn, #/learn/<kp>, #/courses/<c>/learn/<kp>
 *   #/review            #/review
 *   #/                  #/
 *   #/knowledge         #/knowledge
 *   #/materials         #/materials
 *   #/reviews           #/reviews
 *   #/courses           #/courses
 *   #/students          #/students
 *   #/exercises         #/exercises, #/courses/<c>/exercises/<e>/<sid>
 *   #/mistakes          #/mistakes, #/mistakes/<c>/<k>
 *   #/review-pack       #/review-pack
 *
 *   清空 ('')           #/courses/<c>, #/courses/<c>/sessions/<s>,
 *                       #/courses/<c>/knowledge/<k>, #/courses/<c>/students/<sid>
 *
 * 清空的那四页是"课程内部"的详情, 顶栏没有对应分区, 而且它们各有多条进入路径
 * (课程页 / 知识点 / 今日 / 学生列表 / 错题本…) —— 跟随"上一页"会得到一个由
 * 用户上一秒在看哪页决定的高亮, 那不是"确定"。**关键不是"选哪一个", 而是"确定"。**
 *
 * 这张表由 ``test_the_top_nav_ownership_table_is_exactly_as_declared`` 逐项锁住;
 * 改任何一个目标都必须同步改那张表 —— 让改动是**刻意**的, 而不是顺手漂移的。
 */
function markActiveNav(hash) {
  document.querySelectorAll('.topnav a').forEach((link) => {
    const href = link.getAttribute('href') || '';
    if (href === hash) link.classList.add('active');
    else link.classList.remove('active');
  });
}

// ------------------------------------------------------------------- 渲染

function view() { return document.getElementById('view'); }

function setView(html) {
  view().innerHTML = html;
  window.scrollTo({ top: 0 });
}

function renderError(err) {
  const detail = err.detail ? '<pre class="mono small">' + esc(JSON.stringify(err.detail, null, 2)) + '</pre>' : '';
  setView(
    '<div class="card">' +
    // 这里曾经是 t(t('请求失败')) —— 内层已经返回译文, 外层再查一次必然查不到,
    // 靠 t() 的"找不到就原样返回"才没出错。一旦某条译文恰好等于另一个 key,
    // 就会二次翻译。2026-09-21 去掉外层。
    '<h1>' + t('请求失败') + '</h1>' +
    '<p><span class="pill pill-bad">' + esc(err.code) + '</span></p>' +
    '<p>' + esc(err.message) + '</p>' + detail +
    '<p><a href="#/">' + t('返回概览') + '</a></p>' +
    '</div>'
  );
}

/**
 * 路由的加载态。
 *
 * 没有它的话, 从点击导航到数据返回这段时间里, 视图里仍然是**上一页**的内容 ——
 * 用户看到的是一份已经过期的页面, 且没有任何"正在加载"的信号。本机服务通常
 * 很快, 但 /api/dashboard 在一个大课程上并不是瞬间返回的, 而慢的恰恰是那种
 * 最容易被误读成"数据就是这样"的时刻。
 *
 * 它只替换视图, 不碰任何数据; 页面函数随后会用自己的内容覆盖它。
 */
function showLoading() {
  setView('<div class="card"><p class="muted">' + esc(t('common.loading')) + '</p></div>');
}

async function requireCourse() {
  if (!state.courseId) {
    const courses = (await api('/courses')).courses || [];
    if (!courses.length) throw new ApiError({ code: 'NOT_FOUND', message: t('尚未创建任何课程') });
    setCourse(courses[0].course_id);
  }
  return state.courseId;
}

// ---- 概览 ----------------------------------------------------------------


document.addEventListener('click', (event) => {
  // 课程上下文切换 (2026-09-22, 见 switchCourse): 侧边栏课程项的纯左键点击
  // 只换 course context, 不进课程详情 —— hash 一字不动, 当前功能页原地重渲染。
  // 修饰键/中键/右键放行 (中键、新标签页打开仍走 href 的课程详情, 是合法深链)。
  const switchLink = event.target.closest('a[data-course-switch]');
  if (switchLink) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    const courseId = switchLink.getAttribute('data-course-switch');
    if (courseId) switchCourse(courseId);
    return;
  }
  // Task 64: 出题按钮不是 [data-action] 委托的一部分 —— 它有自己的 id，
  // 而且必须在没有任何学生的情况下也能工作（出题与"谁在答"无关）。
  const generateButton = event.target.closest('#generate-batch');
  if (generateButton) {
    actionGenerateExercises(generateButton);
    return;
  }
  // Task 65: 错题本的分组切换。只改显示方式, 不发任何写请求。
  const groupToggle = event.target.closest('#mistake-group-toggle');
  if (groupToggle) {
    const next = groupToggle.getAttribute('data-group') === 'topic' ? 'topic' : 'knowledge';
    state.mistakeGroupBy = next;
    window.localStorage.setItem('ca.mistakeGroup', next);
    route();
    return;
  }
  const target = event.target.closest('[data-action]');
  if (!target) return;
  const action = target.getAttribute('data-action');
  const courseId = target.getAttribute('data-course');
  const materialId = target.getAttribute('data-material');
  const sessionId = target.getAttribute('data-session');
  const knowledgeId = target.getAttribute('data-knowledge');
  // 材料页 (重构后): 一键分析 + 删除。课程页时间线仍用 process/retry
  // (views/courses.js), 保留那两个分支; evidence/digest/ai-analyze 仅
  // 材料页在用, 材料页移除后分支一并移除。
  if (action === 'process-material') actionProcessMaterial(courseId, materialId, target);
  else if (action === 'retry-material') actionRetryMaterial(courseId, materialId, target);
  else if (action === 'analyze-material') actionAnalyzeMaterial(courseId, materialId, target);
  else if (action === 'delete-material') actionDeleteMaterial(courseId, materialId, target);
  else if (action === 'process-session') actionProcessSession(courseId, sessionId, target);
  // 课程页的月份切换 (2026-09-22): 只改"课表看哪一段", 不发任何写请求。
  // 它不是路由 —— 月份是课程页内部的浏览位置, 不该进 hash (深链进课程页永远
  // 落在当前/最近的月份, 见 views/courses.js 的 sessionMonthPlan)。
  else if (action === 'session-month') actionSessionMonth(courseId, target.getAttribute('data-month'));
  else if (action === 'review-confirm') actionReview(courseId, knowledgeId, 'confirm', target);
  else if (action === 'review-reject') actionReview(courseId, knowledgeId, 'reject', target);
  else if (action === 'review-keep') actionReview(courseId, knowledgeId, 'keep', target);
  else if (action === 'review-resolve') actionReview(courseId, knowledgeId, 'resolve', target);
  else if (action === 'learning-event') {
    actionLearningEvent(
      courseId,
      target.getAttribute('data-student'),
      target.getAttribute('data-knowledge'),
      target.getAttribute('data-event'),
      target
    );
  }
});

function parseHash() {
  const raw = (window.location.hash || '#/').replace(/^#/, '');
  return raw.split('/').filter((part) => part.length > 0).map(decodeURIComponent);
}

async function route() {
  const parts = parseHash();
  // 先声明"现在渲染的是这一页" —— 顶栏选择器的全局/课程语义、以及页面函数
  // 里的 currentScope() 都以这份声明为准 (见 __activeRoute 上的注释)。
  declareRoute(window.location.hash || '#/');
  // 横幅的生命周期跟着**路由**走。
  //
  // 曾经从不被清空: 只要出现过一次「课程选择判定失败，已按列表顺序显示。」,
  // 它就会一直挂在页顶、跨越之后所有路由 —— 用户会以为当前这一页也出了问题。
  //
  // 位置刻意放在两个加载**之前**: loadChrome() / loadSidebar() 会报出本次
  // 加载的真实结论 (mock 模式提示、API 不可达、课程选择判定失败), 那些必须
  // 活到下一次路由。
  showBanner('');
  // 立刻切到加载态。否则从点击导航到数据返回这段时间里, 视图里还是**上一页**
  // 的内容 —— 用户看到的是一份已经过期的页面, 且没有任何"正在加载"的信号。
  showLoading();
  try {
    // 顶栏 (健康状态) 与侧边栏 (课程列表 + 选择判定) 互不依赖, 并行跑。
    // 曾经是串行的, 于是每次点导航都要先等一次 /health 再等 /courses ——
    // 这两跳的延迟纯粹是白等的。横幅的合成顺序由 applyLoadNotes 定死,
    // 所以并行不会引入"谁后写谁赢"的竞态。
    applyLoadNotes(await Promise.all([loadChrome(), loadSidebar()]));
    if (parts.length === 0) await pageDashboard();
    else if (parts[0] === 'today') await pageToday();
    // Task 66: 今天的学习流程。`#/learn` 是入口, `#/learn/<kp>` 是知识点学习页。
    else if (parts[0] === 'learn' && parts.length === 1) await pageLearn();
    else if (parts[0] === 'learn' && parts.length === 2) {
      await pageLearnKnowledge(await requireCourse(), parts[1]);
    }
    // ---- 下面 5 条都**必须**带 parts.length 约束 -------------------------
    // 判据: 只要读了 parts[1] 或更后面, 就必须把长度钉死。否则
    // `#/courses/<id>/learn` (3 段) 这种哈希会被接住, 然后把 parts[3] 的
    // ``undefined`` 当知识点 id 传下去 —— 和 2026-09-21 修掉的错题详情
    // (`parts.length === 2` 写错) 是同一族缺陷, 只是那次是长度写错, 这次是
    // 根本没写。可达性由 tests/test_web_ui_invariants.py 机械守着。
    else if (parts[0] === 'courses' && parts.length === 4 && parts[2] === 'learn') {
      await pageLearnKnowledge(parts[1], parts[3]);
    }
    // Task 67: 考前复习模式。`#/review` 是按学生看的复习集合。
    else if (parts[0] === 'review') await pageReview();
    else if (parts[0] === 'review-pack') await pageReviewPack();
    else if (parts[0] === 'knowledge') await pageKnowledge();
    else if (parts[0] === 'materials') await pageMaterials();
    else if (parts[0] === 'reviews') await pageReviews();
    else if (parts[0] === 'students') await pageStudents();
    else if (parts[0] === 'exercises') await pageExercises();
    else if (parts[0] === 'mistakes' && parts.length === 1) await pageMistakes();
    // 错题详情是 ``#/mistakes/<course>/<kp>`` —— **三段**。旧条件是
    // ``parts.length === 2``, 于是正确链接掉进"未找到页面", 而
    // ``#/mistakes/<kp>`` 这种两段哈希反而被接住、把知识点 id 当成 courseId
    // 传下去 (knowledgeId 是 undefined)。2026-09-21 修正。
    else if (parts[0] === 'mistakes' && parts.length === 3) {
      await pageMistakeDetail(parts[1], parts[2]);
    }
    // Task 68: `#/courses` 是**全部课程**的总览, `#/courses/<id>` 才是单门课。
    else if (parts[0] === 'courses' && parts.length === 1) await pageMyCourses();
    else if (parts[0] === 'courses' && parts.length === 2) await pageCourse(parts[1]);
    else if (parts[0] === 'courses' && parts.length === 4 && parts[2] === 'sessions') {
      await pageSession(parts[1], parts[3]);
    }
    else if (parts[0] === 'courses' && parts.length === 4 && parts[2] === 'knowledge') {
      await pageKnowledgeDetail(parts[1], parts[3]);
    }
    else if (parts[0] === 'courses' && parts.length === 4 && parts[2] === 'students') {
      await pageStudent(parts[1], parts[3]);
    }
    // 练习页的学生段是**可选**的 (``#/courses/<c>/exercises/<e>`` 与
    // ``#/courses/<c>/exercises/<e>/<sid>`` 都在用), 所以这条接受 4 与 5。
    else if (parts[0] === 'courses' && (parts.length === 4 || parts.length === 5) &&
             parts[2] === 'exercises') {
      await pageExercise(parts[1], parts[3], parts[4]);
    }
    else setView('<div class="card"><h1>' + t('未找到页面') + '</h1><p><a href="#/">' + t('返回概览') + '</a></p></div>');
  } catch (err) {
    if (err instanceof ApiError) renderError(err);
    else renderError(new ApiError({ code: 'INTERNAL_ERROR', message: String((err && err.message) || err) }));
  } finally {
    // 表单是渲染产物, 每次路由后都要重新接线 (且只接一次)。
    wireUploadForm();
  }
}

window.addEventListener('hashchange', () => { route(); });
window.addEventListener('DOMContentLoaded', () => {
  const picker = document.getElementById('ui-language');
  if (picker) {
    picker.value = state.lang;
    picker.addEventListener('change', () => {
      setLang(picker.value);
      // 界面语言切换后重绘, 但绝不改动任何 Evidence / 原文。
      route();
    });
  }
  // 刷新前仍在跑的任务接回坞里 (P3-2)。**不 await** —— 它是一次额外的
  // 网络往返, 不能挡在首屏渲染前面; 坞是挂在 #view 之外的, 晚一点出现不影响
  // 页面本身。
  restoreTaskDock();
  route();
});
