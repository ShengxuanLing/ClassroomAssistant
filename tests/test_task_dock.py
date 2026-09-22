# -*- coding: utf-8 -*-
"""常驻任务坞 (task dock) 静态契约测试。

用户在等 AI 分析等长任务时要切到别的模块：任务状态必须挂在一个路由
切换不重建的角落里（#view 之外），完成/失败可追溯。本文件只做静态
断言（源码形状 + i18n 三表），行为由 tests/test_student_ui.py 的
Node harness 覆盖。
"""

from __future__ import annotations

import re

from tests.support import WEB_DIR, read_web_source


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
