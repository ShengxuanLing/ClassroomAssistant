# -*- coding: utf-8 -*-
"""知识点列表密度回归：标题列绝不能被挤成单字宽。

2026-09-21 实测：状态列 nowrap + 自动表格布局把标题压成每行一个字符。
修复：table-layout: fixed + colgroup 显式列宽 + 标题正常换词、ID 另行折断。
本文件断言该结构一直在（审计另有“ID 全文在 HTML 里”的计数要求，
见 scripts/ui_audit.js large knowledge base 一节，两者缺一不可）。
"""

from __future__ import annotations

from tests.support import WEB_DIR, read_web_source


class TestKnowledgeDensity:
    def test_list_table_has_fixed_layout(self):
        source = read_web_source("views/knowledge.js")
        assert "table-layout: fixed" in (WEB_DIR / "styles.css").read_text(encoding="utf-8")
        assert "<colgroup>" in source
        assert source.count("<col") >= 4

    def test_title_cell_wraps_words_while_id_breaks_anywhere(self):
        source = read_web_source("views/knowledge.js")
        assert "kp-title" in source
        assert "mono break-all" in source
        css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
        assert "td.kp-title" in css

    def test_full_ids_still_in_html(self):
        # 与 ui_audit 的 kp-N 计数断言同向：ID 展示可以小，但不能删。
        source = read_web_source("views/knowledge.js")
        assert "esc(kp.knowledge_id)" in source
