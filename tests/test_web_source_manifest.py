# -*- coding: utf-8 -*-
"""前端源码清单的**一致性守卫** (P1-6 的收尾钉子)。

为什么需要它
--------------------------------------------------------------------
P1-6 把单个 ``app.js`` 拆成 12 个零构建脚本之后, "前端由哪些文件、按什么顺序
组成"这件事实同时存在于 **6 个地方**:

1. ``src/web/index.html`` 里的 ``<script src="...">`` 顺序 (浏览器真实加载依据)
2. ``tests/support.py::WEB_SOURCE_ORDER`` (Python 静态断言拼接顺序)
3. ``scripts/ui_audit.js::WEB_FILES``
4. ``scripts/ui_render_check.js::WEB_FILES``
5. ``scripts/ui_stress_check.js::WEB_FILES``
6. ``scripts/e2e_session_picker.js::WEB_FILES``

任何一处漂移都不会立刻报错, 而是表现为**静默失去覆盖**: 新增的 view 文件若
忘了加进某个清单, 那边的静态断言就再也搜不到它里面的代码 —— 断言仍然"绿",
但它守的东西已经不在扫描范围里了。这类假阴性正是本项目最警惕的失败模式,
所以它必须被一条测试钉住, 而不是靠注释提醒。

本文件只做静态解析, 不起服务、不跑浏览器、不依赖 node —— 快且稳。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.support import WEB_DIR, WEB_SOURCE_ORDER

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = WEB_DIR / "index.html"

#: 每个 JS 检查脚本都自己维护一份 WEB_FILES; 它们必须与 index.html 完全一致。
JS_MANIFEST_SCRIPTS = (
    "scripts/ui_audit.js",
    "scripts/ui_render_check.js",
    "scripts/ui_stress_check.js",
    "scripts/e2e_session_picker.js",
)


def scripts_from_index_html() -> list[str]:
    """从 index.html 里按出现顺序取出 ``<script src="/xxx">`` 的路径。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    return [
        match.group(1)
        for match in re.finditer(r'<script\s+src="/([^"]+)"', html)
    ]


def web_files_from_js(rel_path: str) -> list[str]:
    """从 JS 脚本里取出 ``const WEB_FILES = [ ... ]`` 的字符串数组。"""
    text = (ROOT / rel_path).read_text(encoding="utf-8")
    match = re.search(r"const\s+WEB_FILES\s*=\s*\[(.*?)\]", text, re.DOTALL)
    assert match is not None, f"{rel_path} 里找不到 `const WEB_FILES = [...]`"
    return re.findall(r"'([^']+)'", match.group(1))


class TestWebSourceManifest:
    """所有清单必须与 index.html 的加载顺序逐项相同。"""

    def test_index_html_is_the_single_source_of_truth(self):
        """Python 侧的拼接顺序 == 浏览器真实的 <script> 顺序。"""
        expected = scripts_from_index_html()
        assert list(WEB_SOURCE_ORDER) == expected, (
            "tests/support.py::WEB_SOURCE_ORDER 与 index.html 的 <script> 顺序不一致。\n"
            f"  support.py : {list(WEB_SOURCE_ORDER)}\n"
            f"  index.html : {expected}"
        )

    @pytest.mark.parametrize("rel_path", JS_MANIFEST_SCRIPTS)
    def test_every_js_check_script_agrees_with_index_html(self, rel_path):
        """每个 JS 检查脚本的 WEB_FILES 都必须与 index.html 一致。

        少一个文件 = 那个脚本扫不到它的源码; 顺序不同 = 顶层初始化顺序错,
        vm 里会直接 ReferenceError (TDZ)。
        """
        expected = scripts_from_index_html()
        actual = web_files_from_js(rel_path)
        assert actual == expected, (
            f"{rel_path} 的 WEB_FILES 与 index.html 不一致。\n"
            f"  {rel_path}  : {actual}\n"
            f"  index.html  : {expected}"
        )

    def test_every_listed_file_exists(self):
        """清单里不能有指向不存在文件的条目。

        服务器上少一个文件会静默回落到 index.html (SPA 路由), 浏览器把 HTML
        当 JS 解析 —— 表现为"页面全白但 HTTP 200", 极难排查。
        """
        missing = [rel for rel in WEB_SOURCE_ORDER if not (WEB_DIR / rel).is_file()]
        assert not missing, f"清单指向了不存在的文件: {missing}"

    def test_no_duplicate_entries(self):
        """重复引入同一个文件会让顶层 const / class 二次声明 -> SyntaxError。"""
        dupes = sorted(
            {rel for rel in WEB_SOURCE_ORDER
             if list(WEB_SOURCE_ORDER).count(rel) > 1}
        )
        assert not dupes, f"清单里有重复条目: {dupes}"

    def test_views_directory_is_fully_listed(self):
        """``views/`` 下的每个 .js 都必须在清单里 —— 新增页面忘了登记, 这里就红。

        这条直接堵住"新 view 文件没进清单 => 静态断言静默失去覆盖"的假阴性。
        """
        listed = set(WEB_SOURCE_ORDER)
        on_disk = {p.relative_to(WEB_DIR).as_posix()
                   for p in (WEB_DIR / "views").glob("*.js")}
        unlisted = sorted(on_disk - listed)
        assert not unlisted, (
            f"views/ 下的文件没有登记进加载清单: {unlisted}。"
            "请同时更新 index.html、tests/support.py::WEB_SOURCE_ORDER "
            "和四个 scripts/*.js 的 WEB_FILES。"
        )
