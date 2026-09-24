# -*- coding: utf-8 -*-
"""前端的两类**机械不变量** —— 靠人眼守不住, 靠这一份守。

为什么单开一个文件
------------------
``scripts/ui_render_check.js`` / ``ui_audit.js`` 覆盖的是"某个页面渲染出什么"。
本文件覆盖的是另一类东西: **结构上的对应关系**。它们的特点是

  - 不写在任何一处代码里, 而是分布在两处、靠"两边写对了才成立";
  - 破坏时**不会抛异常**, 只会让某个链接点下去变成"未找到页面", 或者让页面上
    凭空多出一个没译文的 key;
  - 因此 code review 抓不到, 只有机械比对能抓。

本文件守两条:

**一、hash 路由的可达性 (2026-09-21 审查 P0-2 的推广)**

  ``route()`` 是一条 if/else-if 链, 每条分支用 ``parts[i] === '...'`` 和
  ``parts.length === N`` 描述自己接受哪种形状的哈希。而前端有 80+ 处
  ``'#/...' + 变量`` 在**构造**这些哈希。两边必须逐项对上。

  真实代价: 错题详情的分支曾经写成 ``parts.length === 2``, 而构造端写的是
  ``'#/mistakes/' + courseId + '/' + kpId`` (3 段)。于是正确的链接全部掉进
  "未找到页面", 而 ``#/mistakes/<kp>`` 这种两段哈希反而被接住, 把知识点 id
  当课程 id 传下去。**没有任何测试报错**, 因为两边各自都"没错"。

  这里的做法: 把 ``route()`` 的条件链解析成可求值的谓词, 把前端构造出的每一条
  哈希解析成段数, 然后**真的求值**, 断言每条构造出来的哈希都至少被一条分支接住。

**二、取值域必须来自后端常量**

  ``showBanner()`` 的 ``kind`` 只有 ``'bad'`` 一个值 (其余一律按警告色渲染);
  任务坞的 ``TASK_TERMINAL_STATUSES`` 必须恰好是后端 ``JOB_STATUSES`` 里
  非在途的那几个。这两处都是"传错了会**静默降级**"的地方 —— 传 ``'error'``
  只是颜色不对, 传错终态只是让坞多转一会儿 —— 没有异常、没有日志。
"""

from __future__ import annotations

import re

import pytest

from tests.support import WEB_DIR, WEB_SOURCE_ORDER

# ---------------------------------------------------------------- 前端源码


def _web_sources():
    """按加载顺序返回 [(名字, 源码)]。"""
    return [
        (rel, (WEB_DIR / rel).read_text(encoding="utf-8")) for rel in WEB_SOURCE_ORDER
    ]


def _frontend() -> str:
    return "\n".join(source for _name, source in _web_sources())


def _function_body(source: str, header: str) -> str:
    """取出 ``header`` 那个函数/方法的大括号体 (括号配平, 非正则)。"""
    start = source.index(header)
    open_brace = source.index("{", start)
    depth = 0
    i = open_brace
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace + 1 : i]
        i += 1
    raise AssertionError(f"unbalanced braces after {header!r}")


# ---------------------------------------------------------------- JS 词法
#
# 只需要两件事: "哪些位置是字符串字面量" 与 "注释在哪里"。写一个最小状态机,
# 而不是用正则 —— 注释里出现的 ``#/mistakes`` 与字符串里的 ``//`` 都必须
# 分得清, 否则扫描结果会既有漏报又有误报。


def _string_literals(source: str):
    """返回 ``[(start, end, content)]``, ``end`` 是闭引号之后的位置。"""
    out = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            nl = source.find("\n", i)
            if nl < 0:
                break
            i = nl
            continue
        if ch == "/" and i + 1 < n and source[i + 1] == "*":
            close = source.find("*/", i)
            if close < 0:
                break
            i = close + 2
            continue
        if ch in "'\"":
            j = i + 1
            while j < n:
                if source[j] == "\\":
                    j += 2
                    continue
                if source[j] == ch or source[j] == "\n":
                    break
                j += 1
            if j < n and source[j] == ch:
                out.append((i, j + 1, source[i + 1 : j]))
                i = j + 1
                continue
            i += 1
            continue
        i += 1
    return out


#: href 值结束的标志。出现其中之一, 说明后面已经是 HTML 收尾或标签了。
_HREF_STOP = '"<>'


def _read_expression(source: str, i: int):
    """从 ``i`` 起读一段 JS 表达式, 停在深度 0 的 ``+``。"""
    depth = 0
    j = i
    while j < len(source):
        ch = source[j]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and ch in "+;,\n":
            break
        j += 1
    return source[i:j].strip(), j


def _hash_chain(source: str, literals, index: int):
    """从含 ``#/`` 的那个字面量起, 拼出这条哈希的完整形状。

    返回 ``chain``: 字面量原样拼接, 每一段插值统一记作 ``X``。
    """
    _start, end, content = literals[index]
    chain = content[content.index("#/") :]
    k = index
    while True:
        cut = [chain.index(c) for c in _HREF_STOP if c in chain]
        if cut:
            return chain[: min(cut)]
        nxt = literals[k + 1] if k + 1 < len(literals) else None
        window_end = nxt[0] if nxt else min(len(source), end + 400)
        gap = source[end:window_end]
        if not gap.lstrip().startswith("+"):
            return chain
        at = end + (len(gap) - len(gap.lstrip())) + 1
        while at < len(source) and source[at] in " \t\r\n":
            at += 1
        expr, stop = _read_expression(source, at)
        if expr == "":
            # 纯拼接: 下一个字面量直接接上
            if nxt is None or source[end:].lstrip()[1:].lstrip()[:1] not in "'\"":
                return chain
            k += 1
            _start, end, content = literals[k]
            chain += content
            continue
        chain += "X"
        if source[stop : stop + 1] != "+":
            return chain
        after = stop + 1
        while after < len(source) and source[after] in " \t\r\n":
            after += 1
        if nxt is not None and after == nxt[0]:
            k += 1
            _start, end, content = literals[k]
            chain += content
            continue
        return chain


def _segments(chain: str):
    parts = [p for p in chain.split("/") if p != ""]
    if parts and parts[0] == "#":
        parts = parts[1:]
    return parts


def _built_hashes():
    """前端**构造**出来的每一条哈希, 返回 ``[(位置, 段列表)]``。"""
    found = []
    for name, source in _web_sources():
        literals = _string_literals(source)
        for idx, (start, _end, content) in enumerate(literals):
            if "#/" not in content:
                continue
            chain = _hash_chain(source, literals, idx)
            parts = _segments(chain)
            line = source[:start].count("\n") + 1
            found.append((f"{name}:{line}", parts))
    return found


# ---------------------------------------------------------------- 路由解析


class _Parts(list):
    """越界读返回 None 而不是抛 —— ``route()`` 的条件就是按这个语义写的。"""

    def __getitem__(self, index):
        try:
            return list.__getitem__(self, index)
        except IndexError:
            return None


def _route_conditions():
    """解析 ``route()`` 的 if/else-if 链, 返回 [条件原文]。

    只扫到 ``catch`` 之前 —— 异常分支里也有 ``if``, 但它不属于路由形状。
    """
    source = _frontend()
    body = _function_body(source, "async function route()")
    cut = body.find("} catch (")
    if cut >= 0:
        body = body[:cut]
    conditions = []
    cursor = 0
    pattern = re.compile(r"(?:else\s+)?if\s*\(")
    while True:
        match = pattern.search(body, cursor)
        if match is None:
            break
        start = match.end() - 1
        depth = 0
        i = start
        while i < len(body):
            if body[i] == "(":
                depth += 1
            elif body[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        conditions.append(body[start + 1 : i])
        cursor = i + 1
    return conditions


def _compile_condition(condition: str):
    """把 JS 条件翻成可求值的 Python 谓词。

    翻不动就**报错**, 而不是跳过 —— 静默跳过等于这条分支从此不再被覆盖,
    而那正是本文件存在的理由。
    """
    expr = condition
    for js, py in (("&&", " and "), ("||", " or "), ("===", " == "), ("!==", " != ")):
        expr = expr.replace(js, py)
    expr = expr.replace("parts.length", "len(parts)")
    # 条件可以跨行 (route() 里就有), 折叠空白后 'and' 才不会悬在行尾
    expr = re.sub(r"\s+", " ", expr).strip()
    leftover = expr
    leftover = re.sub(r"len\(parts\)", "", leftover)
    leftover = re.sub(r"parts\[\d+\]", "", leftover)
    leftover = re.sub(r"'[^']*'", "", leftover)
    leftover = re.sub(r"\d+", "", leftover)
    leftover = re.sub(r"\b(and|or)\b", "", leftover)
    leftover = re.sub(r"==|!=", "", leftover)
    leftover = re.sub(r"[()\s]", "", leftover)
    assert not leftover, (
        "route() 里出现了本测试看不懂的条件片段 "
        f"{leftover!r} (完整条件: {condition!r})。"
        "请同步扩展 tests/test_web_ui_invariants.py 的解析器 —— "
        "不能让它静默跳过, 否则这条分支从此不再被覆盖。"
    )
    code = compile(expr, "<route-condition>", "eval")
    return lambda parts: bool(
        eval(code, {"__builtins__": {}, "len": len}, {"parts": _Parts(parts)})
    )


def _route_branches():
    """返回 ``[(条件原文, 谓词)]``。"""
    return [(cond, _compile_condition(cond)) for cond in _route_conditions()]


def _matches_any(branches, parts) -> bool:
    return any(predicate(parts) for _cond, predicate in branches)


def _concrete(parts):
    """把形状里的 ``X`` 占位换成具体值, 得到一条真实的哈希段列表。"""
    out = []
    counter = 0
    for part in parts:
        if part == "X":
            counter += 1
            out.append("v" + str(counter))
        else:
            out.append(part)
    return out


# ---------------------------------------------------------------- 用例


class TestHashRoutesAreReachable:
    """每一条前端能构造出来的哈希, 都必须被 ``route()`` 的某条分支接住。"""

    def test_the_scanner_actually_finds_the_links(self):
        """反空跑: 扫描器必须真的扫到东西。

        扫描器坏了 (正则没匹配上 / 词法状态机提前退出) 的表现是"一条都没找到",
        而那种情况下下面两条断言会**全部通过** —— 这是最危险的假绿。
        """
        built = _built_hashes()
        assert len(built) >= 60, f"只扫到 {len(built)} 条构造出来的哈希, 扫描器可能坏了"
        prefixes = {parts[0] for _where, parts in built if parts}
        for expected in (
            "courses", "today", "learn", "review", "review-pack", "knowledge",
            "materials", "reviews", "exercises", "mistakes",
        ):
            assert expected in prefixes, f"没扫到 {expected!r} 开头的哈希: {sorted(prefixes)}"

    def test_removed_student_pages_are_not_registered(self):
        """学生列表与详情是刻意删除的路由, 不能再被静态守卫漏掉。"""
        source = _frontend()
        assert "pageStudents" not in source
        assert "pageStudent" not in source
        assert "parts[0] === 'students'" not in source
        assert "parts[2] === 'students'" not in source

    def test_today_classes_use_session_cards_instead_of_a_table(self):
        """Task 78: 今日课程卡必须从结构上不再是表格。"""
        source = (WEB_DIR / "views/dashboard.js").read_text(encoding="utf-8")
        body = _function_body(source, "function pageToday()")
        row_at = body.index("function todaySessionRow(")
        today_classes_end = body.index("const classesHtml", row_at)
        classes_body = body[row_at:today_classes_end]
        assert "session-day-card" in classes_body
        assert "session-row" in classes_body
        assert "<table" not in classes_body
        assert "class=\"data\"" not in classes_body

    def test_every_built_hash_is_routable(self):
        branches = _route_branches()
        unreachable = []
        for where, parts in _built_hashes():
            if not _matches_any(branches, _concrete(parts)):
                unreachable.append(f"{where} -> #/{'/'.join(parts)} ({len(parts)} 段)")
        assert not unreachable, (
            "这些哈希前端会构造出来, 但 route() 没有分支接住它们 "
            "(点下去就是「未找到页面」):\n  " + "\n  ".join(unreachable)
        )

    def test_every_static_nav_link_is_routable(self):
        """``index.html`` 里写死的 ``href="#/..."`` 必须条条可达。

        这一条曾经抓到过真的东西: 顶栏的「复习中心」指向 ``#/review-center``,
        而对应的路由分支已经不存在了 —— 死链, 且没有任何测试报错。
        """
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        hrefs = re.findall(r'href="#(/[^"]*)"', html)
        assert hrefs, "index.html 里没找到任何 hash 链接"
        branches = _route_branches()
        unreachable = []
        for href in hrefs:
            parts = [p for p in href.split("/") if p != ""]
            if not _matches_any(branches, parts):
                unreachable.append(href)
        assert not unreachable, f"index.html 里的死链: {unreachable}"

    def test_branches_that_read_later_segments_pin_the_length(self):
        """读了 ``parts[1]`` 或更后面, 就必须把 ``parts.length`` 钉死。

        只写 ``parts[0] === 'courses' && parts[2] === 'knowledge'`` 而不写长度
        的分支, 会接住 ``#/courses/<id>/knowledge`` (3 段) 并把 ``undefined``
        当知识点 id 传下去 —— 与"错题详情长度写错"是同一族缺陷。

        取值域必须**恰好覆盖**被读到的最深下标: 读 ``parts[4]`` 的分支至少要
        接受 5 段。
        """
        problems = []
        for condition in _route_conditions():
            reads = {int(i) for i in re.findall(r"parts\[(\d+)\]", condition)}
            lengths = {int(n) for n in re.findall(r"parts\.length === (\d+)", condition)}
            deeper = reads - {0}
            if deeper and not lengths:
                problems.append(f"读了 parts{ sorted(deeper) } 却没约束 parts.length: {condition!r}")
                continue
            if lengths and max(lengths) < max(reads, default=-1) + 1:
                problems.append(
                    f"读到 parts[{max(reads)}] 却只接受 {sorted(lengths)} 段: {condition!r}"
                )
        assert not problems, "\n".join(problems)


class TestBannerKindDomain:
    """``showBanner()`` 的 ``kind`` 只有 ``'bad'`` 一个值。"""

    def test_the_only_recognized_kind_is_bad(self):
        body = _function_body(_frontend(), "function showBanner(text, kind)")
        assert "kind === 'bad'" in body, (
            "showBanner() 的 kind 判定变了 —— 本测试与 app.js 的文档注释"
            "都按「只有 'bad' 一个值」写的, 请一起更新"
        )

    def test_no_caller_passes_an_unknown_kind(self):
        """传一个不存在的 kind 只会**静默降级成警告色** —— 没有异常、没有日志。

        真实代价: ``views/learn.js`` 的四处错误分支曾经传 ``'error'``, 于是
        取题 / 交卷失败显示的是警告色而不是错误色。
        """
        source = _frontend()
        offenders = []
        for match in re.finditer(r"\bshowBanner\s*\(", source):
            if re.search(r"function\s+$", source[: match.start()]):
                continue  # 定义本身
            args = _split_arguments(source, match.end() - 1)
            if len(args) < 2:
                continue
            kind = args[1].strip()
            # 允许: 省略 / 'bad' / undefined / 只在 'bad' 与 undefined 之间取值的
            # 三元式 (applyLoadNotes 就是这么合成的)。判据是"这个实参里出现的
            # 字符串字面量只能是 'bad'" —— 多一个别的值就会静默降级成警告色。
            literals = set(re.findall(r"'[^']*'", kind))
            if literals - {"'bad'"}:
                line = source[: match.start()].count("\n") + 1
                offenders.append(f"line {line}: showBanner(..., {kind})")
        assert not offenders, (
            "showBanner() 的 kind 取值域只有 'bad', 其余一律静默降级成警告色:\n  "
            + "\n  ".join(offenders)
        )


def _split_arguments(source: str, open_paren: int):
    """把一次调用的实参按深度 0 的逗号切开。"""
    depth = 0
    args = []
    current = ""
    i = open_paren
    while i < len(source):
        ch = source[i]
        if ch == "(":
            depth += 1
            if depth == 1:
                i += 1
                continue
        elif ch == ")":
            depth -= 1
            if depth == 0:
                args.append(current)
                return args
        elif ch == "," and depth == 1:
            args.append(current)
            current = ""
            i += 1
            continue
        current += ch
        i += 1
    raise AssertionError("unbalanced call")


class TestTaskDockTerminalStatuses:
    """任务坞的"终态"集合必须恰好是后端作业状态里非在途的那几个。"""

    @staticmethod
    def _declared():
        source = _frontend()
        match = re.search(r"const TASK_TERMINAL_STATUSES = \[([^\]]*)\];", source)
        assert match, "app.js 里找不到 TASK_TERMINAL_STATUSES"
        return set(re.findall(r"'([^']+)'", match.group(1)))

    def test_matches_the_backend_job_statuses(self):
        """后端加一个作业状态时, 这条必须红 —— 否则新状态会被当成终态。"""
        from src.application.processing_service import (
            JOB_QUEUED,
            JOB_RUNNING,
            JOB_STATUSES,
        )

        expected = set(JOB_STATUSES) - {JOB_QUEUED, JOB_RUNNING}
        assert self._declared() == expected, (
            "TASK_TERMINAL_STATUSES 与后端 JOB_STATUSES 不一致。"
            f"前端: {sorted(self._declared())}; 后端非在途: {sorted(expected)}。"
            "后端新增作业状态时必须显式决定它算不算终态。"
        )

    def test_the_in_flight_statuses_are_handled_separately(self):
        """在途的两个状态由 session 分支单独计数, 不能混进终态集合。"""
        source = _frontend()
        assert "counts.QUEUED" in source
        assert "counts.RUNNING" in source
        assert not (self._declared() & {"QUEUED", "RUNNING"})


class TestRestoreHookIsWired:
    """跨刷新恢复必须在启动路径上被调用 —— 定义了却没人调等于没做。"""

    def test_restore_is_called_on_startup(self):
        source = _frontend()
        # route() 必须仍然可解析 —— 恢复是**独立**的一步, 不该混进路由里
        # (它一次网络往返都不该挡在首屏前面)。
        _function_body(source, "async function route()")
        assert "restoreTaskDock();" in source, "restoreTaskDock() 没有被调用"
        assert "addEventListener('DOMContentLoaded'" in source

    def test_the_store_key_is_only_used_by_the_dock(self):
        """sessionStorage 里只有任务坞这一个键, 且键名有前缀防止撞车。"""
        source = _frontend()
        keys = set(
            re.findall(r"sessionStorage\.(?:get|set|remove)Item\(\s*([A-Za-z0-9_]+)", source)
        )
        assert keys, "没找到任何 sessionStorage 用法"
        assert keys == {"TASK_STORE_KEY"}, keys
        assert "const TASK_STORE_KEY = 'ca.tasks';" in source
