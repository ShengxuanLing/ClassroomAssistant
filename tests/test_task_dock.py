# -*- coding: utf-8 -*-
"""常驻任务坞 (task dock) 契约与生命周期行为测试。

用户在等 AI 分析等长任务时要切到别的模块：任务状态必须挂在一个路由
切换不重建的角落里（#view 之外）。处理中同一目标只显示一次；成功短暂可见
后自动移除，失败保留更久再自动清理。Node 虚拟时钟直接执行 app.js 的真实代码。
"""

from __future__ import annotations

import json
import re
import subprocess

from tests.support import WEB_DIR, read_web_source



def _run_task_lifecycle(expression: str) -> dict:
    """Execute the real lifecycle block with a virtual clock and return its JSON value."""
    app = read_web_source("app.js")
    start = app.index("const tasks = [];")
    end = app.index("function showBanner(")
    lifecycle = app[start:end]
    prefix = r"""
const storage = new Map();
const window = {
  sessionStorage: {
    getItem: (key) => storage.has(key) ? storage.get(key) : null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  },
  location: { hash: '#/' },
  setTimeout: (fn, delay) => setTimeout(fn, delay),
  clearTimeout: (id) => clearTimeout(id),
};
const dock = { hidden: true, innerHTML: '' };
const document = { getElementById: (id) => id === 'task-dock' ? dock : null };
function t(value) { return value; }
function esc(value) { return String(value === null || value === undefined ? '' : value); }
class ApiError extends Error { constructor(payload) { super(payload.message); this.code = payload.code; } }
async function api() { throw new Error('unexpected api call'); }
let nextTimerId = 1;
const timers = [];
function setTimeout(fn, delay) {
  const entry = { id: nextTimerId++, fn, delay };
  timers.push(entry);
  return entry.id;
}
function clearTimeout(id) {
  const index = timers.findIndex((entry) => entry.id === id);
  if (index >= 0) timers.splice(index, 1);
}
function runTimersThrough(maxDelay) {
  for (;;) {
    const index = timers.findIndex((entry) => entry.delay <= maxDelay);
    if (index < 0) return;
    const [entry] = timers.splice(index, 1);
    entry.fn();
  }
}
function resetLifecycle() {
  tasks.splice(0, tasks.length);
  timers.splice(0, timers.length);
  storage.clear();
  Object.keys(__taskPollers).forEach((key) => delete __taskPollers[key]);
  Object.keys(__taskCleanupTimers).forEach((key) => delete __taskCleanupTimers[key]);
}
function assert(condition, message) { if (!condition) throw new Error(message); }
"""
    script = prefix + lifecycle + "\nconst result = (" + expression + ");\n" \
        "console.log(JSON.stringify(result));\n"
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=WEB_DIR.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout)






def _i18n_tables() -> dict[str, set[str]]:
    source = read_web_source("i18n.js")
    start = source.index("const I18N = {")
    end = source.index("\n};", start)
    block = source[start:end]
    tables = {}
    for lang in ("zh", "es", "ca"):
        s = block.index(f"  {lang}: {{")
        e = block.index("\n  },", s)
        tables[lang] = set(re.findall(r"'([A-Za-z0-9_.]+)'\s*:", block[s:e]))
    return tables


class TestTaskDockContract:
    def test_dock_element_exists_outside_view(self):
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        assert 'id="task-dock"' in html
        # 坞在 #view 之外：route() 只重绘 #view，坞自然存活。
        assert html.index('id="task-dock"') > html.index('id="view"')

    def test_tracker_functions_exist(self):
        app = read_web_source("app.js")
        for name in ("function startTask(", "function finishTask(",
                     "function renderTaskDock(", "function onTaskLane("):
            assert name in app, name

    def test_tracker_keeps_bounded_history(self):
        app = read_web_source("app.js")
        assert "tasks.length > 5" in app

    def test_long_actions_report_to_dock(self):
        materials = read_web_source("views/materials.js")
        students = read_web_source("views/students.js")
        assert "startTask(" in materials and "finishTask(" in materials
        assert "startTask(" in students and "finishTask(" in students
        # 并发重复请求返回 PROCESSING 时必须继续轮询，不能调用 finishTask(..., true)。
        assert "function pollMaterialAnalysisTask(" in materials
        assert "pollMaterialAnalysisTask(courseId, materialId, taskId, 1)" in materials
        assert "finishTask(taskId, true, t('ai.analyzing'))" not in materials
        # 切页后不写过期面板：用新鲜查找 + 频道判断。
        assert "onTaskLane()" in materials
        assert 'getElementById(\'ai-panel\')' in materials

    def test_dock_style_is_pinned_corner(self):
        css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
        assert ".task-dock" in css
        assert "position: fixed" in css

    def test_dock_keys_in_all_three_tables(self):
        tables = _i18n_tables()
        for key in ("task.running", "task.done", "task.failed"):
            assert key in tables["zh"], key
            assert key in tables["es"], key
            assert key in tables["ca"], key



class TestTaskLifecycleBehavior:
    def test_same_target_and_fallback_keys_do_not_duplicate_running_cards(self):
        result = _run_task_lifecycle(r"""(() => {
          resetLifecycle();
          const targetId = startTask('处理 · mat-1', {
            courseId: 'course-1', targetId: 'mat-1', targetKind: 'material',
          });
          const sameTarget = startTask('不同显示文案 · mat-1', {
            courseId: 'course-1', targetId: 'mat-1', targetKind: 'material',
          });
          const explicitA = startTask('上传 a.pdf', { key: 'upload:a.pdf' });
          const explicitB = startTask('上传   a.pdf', { key: 'upload:a.pdf' });
          const fallbackA = startTask('AI 分析 · mat-2');
          const fallbackB = startTask('AI   分析 · mat-2');
          const unrelated = startTask('上传 b.pdf');
          return {
            sameId: targetId === sameTarget,
            count: tasks.length,
            statuses: tasks.map((task) => task.status),
            explicitSameId: explicitA === explicitB,
            fallbackSameId: fallbackA === fallbackB,
            unrelatedDifferent: unrelated !== fallbackA,
          };
        })()""")
        assert result == {
            "sameId": True,
            "count": 4,
            "statuses": ["running", "running", "running", "running"],
            "explicitSameId": True,
            "fallbackSameId": True,
            "unrelatedDifferent": True,
        }

    def test_success_is_visible_then_removed_and_failure_lasts_longer(self):
        result = _run_task_lifecycle(r"""(() => {
          resetLifecycle();
          const successId = startTask('成功', { key: 'success' });
          finishTask(successId, true, 'OK');
          const successImmediate = {
            status: tasks[0].status,
            detail: tasks[0].detail,
            delay: timers[0].delay,
          };
          runTimersThrough(TASK_SUCCESS_VISIBLE_MS - 1);
          const successBeforeDeadline = tasks.length;
          runTimersThrough(TASK_SUCCESS_VISIBLE_MS);
          const successAfterDeadline = tasks.length;

          const failureId = startTask('失败', { key: 'failure' });
          finishTask(failureId, false, 'ERROR');
          const failureImmediate = {
            status: tasks[0].status,
            detail: tasks[0].detail,
            delay: timers[0].delay,
          };
          runTimersThrough(TASK_SUCCESS_VISIBLE_MS);
          const failureAfterSuccessWindow = tasks.length;
          runTimersThrough(TASK_FAILURE_VISIBLE_MS);
          const failureAfterDeadline = tasks.length;
          return {
            successImmediate,
            successBeforeDeadline,
            successAfterDeadline,
            failureImmediate,
            failureAfterSuccessWindow,
            failureAfterDeadline,
            successConstant: TASK_SUCCESS_VISIBLE_MS,
            failureConstant: TASK_FAILURE_VISIBLE_MS,
          };
        })()""")
        assert result["successImmediate"] == {
            "status": "done", "detail": "OK", "delay": 3000,
        }
        assert result["successBeforeDeadline"] == 1
        assert result["successAfterDeadline"] == 0
        assert result["failureImmediate"] == {
            "status": "failed", "detail": "ERROR", "delay": 15000,
        }
        assert result["failureAfterSuccessWindow"] == 1
        assert result["failureAfterDeadline"] == 0
        assert result["failureConstant"] > result["successConstant"]

    def test_duplicate_terminal_result_is_idempotent_and_does_not_extend_cleanup(self):
        result = _run_task_lifecycle(r"""(() => {
          resetLifecycle();
          const id = startTask('同一任务', { key: 'same-task' });
          finishTask(id, true, '第一次完成');
          const firstCleanupId = __taskCleanupTimers[id];
          const firstTimerCount = timers.length;
          // 模拟重复 fetch / 并发请求乱序到达: 后续终态不得产生第二张卡，
          // 也不得把成功改写为失败或重新开始 3 秒计时。
          finishTask(id, true, '重复完成');
          finishTask(id, false, '迟到的失败');
          return {
            count: tasks.length,
            status: tasks[0].status,
            detail: tasks[0].detail,
            timerCount: timers.length,
            firstTimerCount,
            sameCleanupTimer: firstCleanupId === __taskCleanupTimers[id],
          };
        })()""")
        assert result == {
            "count": 1,
            "status": "done",
            "detail": "第一次完成",
            "timerCount": 1,
            "firstTimerCount": 1,
            "sameCleanupTimer": True,
        }

    def test_terminal_retry_replaces_card_and_cancels_old_cleanup_timer(self):
        result = _run_task_lifecycle(r"""(() => {
          resetLifecycle();
          const firstId = startTask('材料处理', {
            courseId: 'course-1', targetId: 'mat-1', targetKind: 'material',
          });
          finishTask(firstId, false, '第一次失败');
          const retryId = startTask('材料处理', {
            courseId: 'course-1', targetId: 'mat-1', targetKind: 'material',
          });
          return {
            firstId,
            retryId,
            count: tasks.length,
            status: tasks[0].status,
            detail: tasks[0].detail,
            pendingTimers: timers.length,
            oldPollTimerCleared: __taskPollers[firstId] === undefined,
            oldCleanupTimerCleared: __taskCleanupTimers[firstId] === undefined,
          };
        })()""")
        assert result["retryId"] != result["firstId"]
        assert result["count"] == 1
        assert result["status"] == "running"
        assert result["detail"] == ""
        assert result["pendingTimers"] == 0
        assert result["oldPollTimerCleared"]
        assert result["oldCleanupTimerCleared"]

    def test_cleanup_cancels_polling_timer_and_five_card_cap_is_unchanged(self):
        result = _run_task_lifecycle(r"""(() => {
          resetLifecycle();
          for (let index = 0; index < 6; index += 1) {
            startTask('任务 ' + index, { key: 'task-' + index });
          }
          const cappedLabels = tasks.map((task) => task.label);
          const taskId = tasks[0].id;
          __taskPollers[taskId] = setTimeout(() => {
            __taskPollers[taskId].unexpected = true;
          }, TASK_POLL_INTERVAL_MS);
          finishTask(taskId, true, 'OK');
          const pollerCancelledAfterFinish = timers.every(
            (entry) => entry.delay !== TASK_POLL_INTERVAL_MS
          );
          runTimersThrough(TASK_SUCCESS_VISIBLE_MS);
          return {
            cappedLabels,
            pollerCancelledAfterFinish,
            remaining: tasks.length,
            pollerEntryCleared: __taskPollers[taskId] === undefined,
            cleanupEntryCleared: __taskCleanupTimers[taskId] === undefined,
          };
        })()""")
        assert result["cappedLabels"] == ["任务 5", "任务 4", "任务 3", "任务 2", "任务 1"]
        assert result["pollerCancelledAfterFinish"]
        assert result["remaining"] == 4
        assert result["pollerEntryCleared"]
        assert result["cleanupEntryCleared"]
