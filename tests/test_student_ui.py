# -*- coding: utf-8 -*-
"""学生注册 UI + 课程显示名 + 课堂下拉 (2026-09-20)。

用户报的三件事
--------------
1. **课程显示不对**。学生页的副标题直接渲染内容寻址的 ``course_id``
   (``course-32dde014868219be``), 而不是课程名 (``Gestió de Projectes``)。
   同一个写法在 12 处出现过 (概览 / 知识点 / 材料 / 待审核 / 复习中心 /
   课堂 / 学生 / 错题本 / 学生首页 …) —— 典型的"同一条规则被抄了 N 遍"。
2. **注册学生只能靠 API**。空态写着"请先通过 POST /api/students 注册",
   用户必须手写 HTTP 请求才能建第一个学生。
3. **材料页的"课堂 (可选)"是个要手打 id 的文本框**。用户看到的是一个
   ``placeholder="session-…"`` 的输入框, 而 ``session_id`` 与 ``course_id``
   同源 —— ``ClassSession._generate_stable_id`` 里
   ``"session-" + sha256(course_id + 课号)[:16]``, 用户既猜不出也记不住。
    改成下拉列表，选项标签使用共享 ``sessionLabel()`` 显示真实日期、星期、课号及课表信息。

修法 (不是"把漏的那遍补上")
--------------------------
- 课程显示名收口到 **一个** ``courseLabel(courseId)``, 名称来自
  ``__courseCache`` (每次路由由 ``loadSidebar()`` 刷新), 缓存里没有就退回
  ``course_id`` —— 宁可显示一个不漂亮的标识, 也不要空白或自造的"未命名"。
- 注册入口收口到 **一处** (学生页的 ``#student-form``)。练习页/单题页的
  "还没有学生" 空态只放一个通往它的链接 (``noStudentsCard()``), 不再复刻表单。

本文件盯住的不变量
------------------
- ``course_id`` 与 ``session_id`` 都只允许出现在**属性值**里
  (``data-course="…"`` / ``href="…"`` / ``<option value="…">``); 一旦被当成
  文案渲染 (包括 ``名称 || course_id`` 这种兜底),
  ``test_the_course_id_is_never_rendered_as_visible_text`` 就红。
  两者同源: 都是内容寻址的 sha256 句柄, 对用户零信息量。
- 课堂显示成人话标签（日期、星期、课号及课表信息）只有一个生产者 ``sessionLabel()``。
- 课程显示名只有一个来源 ``courseLabel()``; 身份串 (``code · language``) 只有一个
  生产者 ``courseIdentity()``; 侧边栏显示**课程代码**而不是哈希。
- 学生页**始终**渲染注册表单 —— 包括一个学生都没有的时候 (否则第一个学生
  永远建不出来)。这一条由 ``scripts/ui_audit.js`` 真实执行页面函数来证明。
- 表单提交的载荷形状与 ``POST /api/students`` 的真实契约一致 (含幂等与 CONFLICT)。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from src.api.server import create_server, default_static_dir
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace

from tests.support import read_web_source

WEB_DIR = Path(default_static_dir())
FIXED_TIME = "2026-01-01T00:00:00+00:00"

#: 仍有课程标题 / 面包屑的页面, 以及它们各自的函数头。
#: 学生列表 / 详情页面已删除, 不再属于课程标题清单。
#: 2026-09-22 (任务书 §3): 概览默认是全部课程聚合页, 单课程标题随 scope 筛选
#: 视图移到了 pageDashboardCourseBody (渲染体与取数分离的同一处拆分)。
COURSE_LABEL_PAGES = (
    "function pageDashboardCourseBody(",
    "async function pageLearn(",
    "async function pageReview(",
    "async function pageKnowledge(",
    "async function pageMaterials(",
    "async function pageReviews(",
    "async function pageSession(",
    "async function pageMistakes(",
)

#: 删除学生页专属的三语 key; student.none 仍被依赖页的空态使用。
REMOVED_STUDENT_UI_KEYS = (
    "student.dashboard",
    "student.courses",
    "student.progress",
    "student.studyPlan",
    "student.recentEval",
    "student.gaps",
    "student.notStarted",
    "student.answered",
    "student.average",
    "student.stateCounts",
    "student.register",
    "student.registerNote",
    "student.idLabel",
    "student.idPlaceholder",
    "student.nameLabel",
    "student.registerAction",
    "student.idRequired",
    "student.registeredOk",
    "student.registerFailed",
)
RETAINED_STUDENT_KEYS = ("student.none",)


# ---------------------------------------------------------------------------
# 最小 HTTP 客户端 (与 test_exercise_ui.py 同一套写法, 不依赖第三方库)
# ---------------------------------------------------------------------------


class Client:
    def __init__(self, base_url: str) -> None:
        self.base = base_url

    def request(self, method, path, *, body=None, headers=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base + path, method=method, data=data)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body=body, **kw)

    def post_bytes(self, path, payload: bytes, *, content_type="application/octet-stream"):
        """原样发字节 —— 上传材料走的是 ``POST /api/materials`` 的裸 body 分支。

        非 multipart 时端点用 ``request.body`` 当文件内容、``filename`` 查询参数当
        文件名, 与浏览器发的 multipart 表单**落到同一个字段集**上。
        """
        request = urllib.request.Request(self.base + path, method="POST", data=payload)
        request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture
def workspace(tmp_path):
    return Workspace(
        str(tmp_path / "data"),
        clock=fixed_clock(FIXED_TIME),
        asr_mode="mock",
        ocr_mode="mock",
    )


@pytest.fixture
def server(workspace):
    instance = create_server(workspace, port=0)
    instance.start()
    try:
        yield instance
    finally:
        instance.stop()


@pytest.fixture
def client(server):
    return Client(server.url)


@pytest.fixture
def course(client):
    status, payload = client.post(
        "/api/courses", {"name": "Gestió de Projectes", "code": "GP"}
    )
    assert status == 201, payload
    return payload["data"]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def read_asset(name: str) -> str:
    """读取前端资源。

    P1-6: 前端已拆分成多个零构建脚本; ``read_asset("app.js")`` 返回按加载
    顺序拼接的**全部前端源码**, 语义与拆分前一致 (见 ``tests/support.py``)。
    """
    return read_web_source(name)


def strip_js_comments(source: str) -> str:
    """去掉 ``//`` 与 ``/* */`` 注释, 只留可执行代码。"""
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)//[^\n]*", "", without_block)


def function_body(source: str, header: str) -> str:
    """从 ``header`` 起到下一个顶层 ``function`` / ``async function`` 为止。

    app.js 里所有顶层函数都在第 0 列, 所以这个边界是可靠的。
    """
    start = source.index(header)
    rest = source[start + len(header):]
    match = re.search(r"\r?\n(?:async )?function ", rest)
    assert match, f"could not find the end of {header!r}"
    return source[start:start + len(header) + match.start()]


#: 属性赋值的尾部: ``data-course="`` / ``href="`` / ``value="`` / ``id="`` …
#: 之后到 ``esc(`` 之间不再出现 ``"``, 说明它就是该属性的值。
_ATTRIBUTE_VALUE = re.compile(r'(?:data-[a-z-]+|href|value|id|name|for|action)="[^"]*$')

#: 原始课程 id 的两种写法 (``course_id`` / ``courseId``)。
_COURSE_ID = re.compile(r"\bcourse_?[Ii]d\b")

#: 原始课堂 id 的两种写法。``session_id`` 与 ``course_id`` 同源 ——
#: ``ClassSession._generate_stable_id`` 里
#: ``"session-" + sha256(course_id + 课号)[:16]``, 同样是内容寻址的句柄。
_SESSION_ID = re.compile(r"\bsession_?[Ii]d\b")


#: 把值渲染成**可见文本**的两个助手。判据必须两个都扫 ——
#: 只扫 ``esc()`` 会漏掉 ``dash(material.session_id)`` 这种没有 esc 的渲染,
#: 2026-09-20 实测就漏过一处 (由 ``scripts/ui_audit.js`` 的运行期检查抓到)。
_TEXT_HELPERS = ("esc", "dash")


def text_render_arguments(source: str) -> list[tuple[int, str, bool]]:
    """逐个 ``esc(...)`` / ``dash(...)`` 调用 -> ``(行号, 参数, 是不是属性值)``。

    app.js 里这两个助手的参数都是单层表达式, 所以配对括号就够, 不需要 HTML 解析器。
    "是不是属性值"用 80 字符窗口判断 —— 属性值总是紧贴在调用前面
    (``data-course="' + esc(courseId)``), 而文案位置前面是 ``'<strong>' + `` 这类拼接。
    """
    out: list[tuple[int, str, bool]] = []
    for helper in _TEXT_HELPERS:
        for match in re.finditer(r"\b%s\(" % helper, source):
            depth, index = 1, match.end()
            while index < len(source) and depth:
                if source[index] == "(":
                    depth += 1
                elif source[index] == ")":
                    depth -= 1
                index += 1
            window = source[max(0, match.start() - 80): match.start()]
            out.append((
                source[: match.start()].count("\n") + 1,
                source[match.end(): index - 1],
                bool(_ATTRIBUTE_VALUE.search(window)),
            ))
    return sorted(out)


def raw_id_text_offenders(source: str, pattern, allowed: tuple[str, ...]) -> list[int]:
    """把 ``pattern`` 匹配到的内部句柄渲染成**可见文本**的行号 (空 = 合规)。

    扫描范围是 ``esc(...)`` **与** ``dash(...)`` 的参数 —— 这两个是把值变成可见
    文本的助手 (见 ``_TEXT_HELPERS``)。只扫前者会漏掉
    ``dash(material.session_id)`` 这种没有 esc 的渲染, 2026-09-20 实测漏过一处。

    允许的两种情形:
    - **属性值**: ``href="#/courses/<id>"`` / ``data-course="<id>"`` /
      ``<option value="<id>">`` —— 那是技术标识, 不是给用户读的文案;
    - ``allowed`` 里列出的助手: 句柄在缓存/记录缺失时退回 id, 那是 app.js 注释里
      写明的**最后手段**, 而且只允许存在这一个入口。

    其余任何位置 (含 ``esc(course.name || course.course_id)`` 这类兜底) 都违规 ——
    2026-09-20 之前判据只扫字面量 ``esc(courseId)``, 于是侧边栏与 6 处兜底全漏了。
    """
    return [
        line
        for line, argument, is_attribute in text_render_arguments(source)
        if not is_attribute
        and not any(entry in argument for entry in allowed)
        and pattern.search(argument)
    ]


def course_id_text_offenders(source: str) -> list[int]:
    """课程哈希当可见文本的行号。唯一允许的兜底入口是 ``courseLabel()``。"""
    return raw_id_text_offenders(source, _COURSE_ID, ("courseLabel(",))


def session_id_text_offenders(source: str) -> list[int]:
    """课堂哈希当可见文本的行号。唯一允许的兜底入口是 ``sessionLabel()``。

    为什么和课程用同一条规则: ``session_id`` 就是
    ``"session-" + sha256(course_id + 课号)[:16]``, 与 ``course_id`` 是同一类东西。
    用户报的文本框 (``placeholder="session-…"``) 正是它漏到界面上的地方。
    """
    return raw_id_text_offenders(source, _SESSION_ID, ("sessionLabel(",))


def node_runtime() -> str:
    """找到 node 解释器; 找不到就 skip (本机没装 node 不该让测试变红)。"""
    candidates = [
        shutil.which("node"),
        r"C:/Users/Rafae/.workbuddy-ai/binaries/node/versions/22.22.2-2/node.exe",
        r"C:/Program Files/nodejs/node.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    pytest.skip("node runtime not available for the render harness")


def run_node_script(script: Path, timeout: int = 300):
    """在项目根目录下跑一个 node harness。

    ``timeout`` 是硬性的: 自带服务的脚本 (``e2e_session_picker.js``) 万一卡在
    等端口上, 宁可失败也不要挂住整个回归。
    """
    return subprocess.run(
        [node_runtime(), str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(script.parent.parent),
        timeout=timeout,
    )


def run_node_code(source: str, timeout: int = 30):
    """执行一小段真实前端代码；用于直接求值共享渲染助手。"""
    return subprocess.run(
        [node_runtime(), "-e", source],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(WEB_DIR.parent.parent),
        timeout=timeout,
    )


def i18n_tables() -> dict[str, set[str]]:
    """解析 ``app.js`` 的 ``I18N`` 三语表 (符号 key -> 存在)。"""
    source = read_asset("app.js")
    start = source.index("const I18N = {")
    end = source.index("\n};", start)
    block = source[start:end]
    tables: dict[str, set[str]] = {}
    for lang in ("zh", "es", "ca"):
        s = block.index(f"  {lang}: {{")
        e = block.index("\n  },", s)
        tables[lang] = set(re.findall(r"'([A-Za-z0-9_.]+)'\s*:", block[s:e]))
    return tables


# ===========================================================================
# 1. 课程显示名
# ===========================================================================


class TestCourseNameIsShownInsteadOfItsId:
    """用户报的第 1 条: 界面上显示的是 ``course-32dde014868219be``。"""

    #: 修复前**真实存在过**的写法 (2026-09-20 的 temp/_app_before_identity.js
    #: 里逐字可查)。判据必须逐条报红 —— 否则就是空跑。
    REMOVED_SHAPES = (
        # 侧边栏: 每门课下面并列一行 course_id
        "'<br><span class=\"tiny muted mono\">' + esc(course.course_id) + '</span></a></li>'",
        # 顶栏切换器的选项文本
        "esc(course.name || course.course_id) + '</option>'",
        # student-today 的课程标签
        "' <span class=\"tiny muted\">' + esc(row.course_name || row.course_id || '') + '</span>'",
        # 课程页 h1
        "'<h1>' + esc(course.name || courseId) + '</h1>'",
        # 身份串的旧签名
        "esc(courseIdentity(row, row.course_id))",
    )

    #: 合法写法 (阴性对照): 属性值里的 id + courseLabel() 兜底 + 身份串 + 代码兜底。
    ALLOWED_SHAPES = (
        "'data-course=\"' + esc(courseId) + '\"'",
        "'<option value=\"' + esc(course.course_id) + '\"'",
        "'<a href=\"#/courses/' + encodeURIComponent(course.course_id) + '\">'",
        "esc(courseLabel(courseId))",
        "esc(courseIdentity(row))",
        "esc(course.name || course.code || '')",
    )

    def test_the_course_id_is_never_rendered_as_visible_text(self):
        """把整个文件扫一遍: 可见文本里不许出现原始 course_id。

        用户报的那一处 (``<strong>course-32dde014868219be</strong>``) 之所以能被
        一眼看出不对, 就是因为它出现在**文本**位置。判据见
        ``course_id_text_offenders()``。

        **这条判据 2026-09-20 被加强过。** 旧版只扫字面量 ``esc(courseId)``, 于是
        侧边栏的 ``esc(course.course_id)`` 与 6 处 ``名称 || course_id`` 兜底
        (``esc(course.name || course.course_id)`` 这类) 全在判据之外 —— 用户报的那
        一串哈希正是从这里漏出去的。加强后前者命中 12 处、后者命中 6 处; 现在 0。
        判据本身不是空跑, 由 ``test_the_criterion_catches_every_shape_we_removed``
        逐条证明。
        """
        offenders = course_id_text_offenders(read_asset("app.js"))
        assert not offenders, (
            "course_id 被当成可见文案渲染 (行号): %r —— 应改用 courseLabel() 或课程代码"
            % (offenders,)
        )

    def test_the_criterion_catches_every_shape_we_removed(self):
        """判据**不是空跑**: 修复前的每一种写法喂进去都必须报红。

        为什么值得单独一条: "不许出现 X"这类断言只要指纹写错就永远绿, 等于没写。
        这里的输入是**修复前真实存在过的源码片段**, 而不是我凭印象编的形状。
        配一组阴性对照, 证明它也不是"见谁报谁"。
        """
        for shape in self.REMOVED_SHAPES:
            assert course_id_text_offenders(shape) == [1], shape
        for shape in self.ALLOWED_SHAPES:
            assert course_id_text_offenders(shape) == [], shape

    def test_every_course_heading_goes_through_the_shared_helper(self):
        """8 处标题 / 面包屑全部走 ``courseLabel()``, 没有一处漏掉。"""
        source = read_asset("app.js")
        for header in COURSE_LABEL_PAGES:
            body = function_body(source, header)
            assert "courseLabel(courseId)" in body, (
                f"{header} 仍然直接渲染 course_id"
            )

    def test_the_helper_is_defined_exactly_once(self):
        """显示名只能有一个来源 —— 抄第二遍迟早会漂移。

        下限是**实测值**: 2026-09-21 删掉课程复习中心页 (``pageCourseReview``)、
        本次删掉学生列表 / 详情页后, 课程标题清单剩 8 项。这条是
        ``test_every_course_heading_goes_through_the_shared_helper`` 之外的冗余网 ——
        那个测试只覆盖清单里的页面, 这条盯的是"总数没有悄悄变少"。
        """
        source = strip_js_comments(read_asset("app.js"))
        assert source.count("function courseLabel(") == 1
        assert source.count("esc(courseLabel(courseId))") >= 8


    def test_the_course_identity_string_has_exactly_one_producer(self):
        """身份串 (``code · language``) 只能由 ``courseIdentity()`` 生产。

        2026-09-20 起身份串**不含 course_id** —— 理由写在 ``courseIdentity()`` 的
        注释里: 哈希对用户零信息量, 而 code 已经足够区分同名课程 (course_id 由
        ``(name, code)`` 派生)。于是"用户可见文本里不出现 course_id"成了一条
        **没有例外**的规则, ``scripts/ui_audit.js`` 里那张 ``RAW_ID_ALLOWED``
        例外表也随之删掉了。

        唯一性仍然要盯: ``mcCard`` 曾经内联复制了一遍拼接逻辑, 输出与
        ``courseIdentity()`` 完全一样 —— 所以没有任何测试会红, 只有等将来往助手
        加字段时 (比如加学期) 才会暴露。这条断言盯的是**唯一性**, 不是输出。
        """
        source = strip_js_comments(read_asset("app.js"))
        assert source.count("function courseIdentity(") == 1
        #: 内联副本的指纹 —— 只写**历史上真实出现过**的那一个。它是不是写对了,
        #: 由 temp/_app_before_identity.js 作证 (重构前那份源码里搜得到)。凭空
        #: 编一个从没出现过的指纹, 这条断言就永远是绿的, 等于没写。
        assert "' · ' + esc(row.code)" not in source
        #: 1 处定义 + 课程详情页 + 我的课程卡片 (注释里那次提及不算: 已剥注释)。
        assert source.count("courseIdentity(") == 3
        body = function_body(source, "function courseIdentity(course) {")
        assert "course_id" not in body, "身份串不得包含内容寻址的 id"
        assert "course.code" in body and "course.language" in body

    def test_the_name_comes_from_the_sidebar_cache_and_falls_back_to_the_id(self):
        """不额外发请求; 缓存里没有就退回 id, 不显示空白也不自造名称。"""
        source = read_asset("app.js")
        body = function_body(source, "function courseLabel(courseId) {")
        assert "__courseCache" in body
        assert "|| courseId" in body
        assert "api(" not in body, "courseLabel 不得发请求 (页面渲染是同步的)"


# ===========================================================================
# 1b. 侧边栏: 显示课程代码, 不显示哈希
# ===========================================================================


class TestTheSidebarShowsTheCodeNotTheId:
    """侧边栏曾经在每门课下面并列一行 ``course_id`` —— 用户看到的就是一串哈希。

    2026-09-20 改成显示**课程代码** (GP / MC / …): 有信息量, 而且同样能区分同名
    课程 (course_id 由 (name, code) 派生)。这同时补上一个覆盖缺口 —— 侧边栏不在
    ``scripts/ui_audit.js`` 的 ``PAGES`` 里, 页面级判据只读 ``#view``。
    """

    def test_the_sidebar_renders_the_course_code(self):
        body = function_body(read_asset("app.js"), "function courseListHtml(courses) {")
        assert "esc(course.code)" in body
        # 2026-09-22 解耦: 哈希多了一个属性位 (data-course-switch, 点击委托的
        # 取值处) —— 仍不是文案。用与切换器测试同一套属性感知的判据, 而不是
        # 字面量 "esc(course.course_id) 不出现" (那个一刀切连属性位一起杀)。
        assert course_id_text_offenders(body) == []

    def test_the_code_line_is_omitted_when_the_course_has_no_code(self):
        """没有代码就只显示名称 —— 不要渲染一行空的第二行。"""
        body = function_body(read_asset("app.js"), "function courseListHtml(courses) {")
        assert "course.code" in body
        assert ": '';" in body

    def test_the_sidebar_keeps_the_course_id_in_attributes_only(self):
        """id 只进非文本属性, 不当文案 (2026-09-22 解耦后有两个属性位)。

        ``href`` 是课程详情深链 (修饰键/中键打开与无 JS 的 fallback);
        ``data-course-switch`` 是点击委托的取值处 (纯左键只换 context、不导航)。
        """
        body = function_body(read_asset("app.js"), "function courseListHtml(courses) {")
        assert "encodeURIComponent(course.course_id)" in body
        assert "data-course-switch" in body
        assert course_id_text_offenders(body) == []

    def test_the_switcher_falls_back_to_the_code_not_the_id(self):
        body = function_body(
            read_asset("app.js"), "function renderCourseSwitcher(courses) {"
        )
        assert "esc(course.name || course.code || '')" in body
        #: 判据直接复用在函数体上: 属性值里的 course_id (value=…) 允许, 文案位置不许。
        assert course_id_text_offenders(body) == []

    def test_the_id_can_only_reach_the_screen_through_the_one_helper(self):
        """全文件只有 ``courseLabel()`` 允许把 id 当最后手段。

        这是"没有例外"这句话的**可执行**版本: 兜底只允许存在一处, 而且必须在
        那个助手里。多一处就说明又有人开始把哈希往界面上塞。
        """
        source = strip_js_comments(read_asset("app.js"))
        assert source.count("|| courseId") == 1
        body = function_body(source, "function courseLabel(courseId) {")
        assert "|| courseId" in body


# ===========================================================================
# 1c. 课堂下拉: 材料页的"课堂 (可选)"
# ===========================================================================


class TestTheSessionPickerIsADropdown:
    """用户报的第 3 条: 上传材料时"关联哪一堂课"要用户手打 ``session-…``。

    渲染层的真实证明在 ``scripts/ui_audit.js`` ("materials page renders a
    session dropdown") —— 那里真的执行了 ``pageMaterials()``。这里钉住形状与接线。
    """

    @staticmethod
    def _page() -> str:
        return function_body(read_asset("app.js"), "async function pageMaterials(")

    def test_the_form_renders_a_select_not_a_text_input(self):
        body = self._page()
        assert '<select name="session_id">' in body
        # 修复前的写法: 一个要用户手打内部 id 的文本框。
        assert 'name="session_id" placeholder=' not in body

    def test_the_picker_offers_a_no_session_option(self):
        """不关联课堂是合法状态 —— 上传的是一份课件时本来就没有对应课堂。"""
        body = self._page()
        assert '\'<option value="">\' + t(\'不关联课堂\') + \'</option>\'' in body

    def test_the_options_come_from_the_sessions_endpoint(self):
        body = self._page()
        assert "api('/sessions', { query: { course_id: courseId } })" in body

    def test_the_option_value_is_the_id_and_the_label_is_human(self):
        """值必须是 session_id (上传接口认这个), 文案必须是人话标签。"""
        body = self._page()
        assert (
            "'<option value=\"' + esc(s.session_id) + '\">' + esc(sessionLabel(s)) + '</option>'"
            in body
        )

    def test_an_empty_session_list_still_renders_and_explains_itself(self):
        """一堂课都没有时下拉不能消失 —— 否则整张上传表单直接坏掉。"""
        body = self._page()
        assert "sessions.length ? '' :" in body
        assert "t('还没有课堂。')" in body

    def test_the_materials_table_shows_the_session_label_not_its_id(self):
        """用户天天看的是这一列。修复前它是 ``session-32dde014868219be``。"""
        body = self._page()
        assert "materialSessionCell(sessionById[m.session_id])" in body
        assert "esc(sessionLabel(sessionById[m.session_id], m.session_id))" not in body
        assert "esc(m.session_id || '—')" not in body

    def test_an_unlinked_material_says_so_instead_of_showing_a_dash(self):
        """空 session_id 是合法业务状态，不能只留一个无解释的 ``—``。"""
        body = self._page()
        assert "t('未关联课堂')" in body
        assert "? esc(sessionLabel(sessionById[m.session_id], m.session_id)) : '—'" not in body

    def test_the_form_reads_the_session_field_by_name_not_by_tag(self):
        """换控件类型时 ``input[name=…]`` 会静默变成 ``null.value``。

        提交路径**没有任何自动化**能跑到 (DOM 桩不执行 submit), 所以这条只能静态钉。
        """
        body = function_body(read_asset("app.js"), "function wireUploadForm(")
        assert "form.querySelector('[name=\"session_id\"]')" in body
        assert 'input[name="session_id"]' not in body

    def test_the_label_helper_has_exactly_one_producer(self):
        """课表字段只在 ``sessionDisplayParts()`` 解析一次，再由标签/材料表复用。"""
        source = strip_js_comments(read_asset("app.js"))
        assert source.count("function sessionDisplayParts(") == 1
        assert source.count("function sessionLabel(") == 1
        #: sessionLabel 定义 1 处 + 下拉选项 + 课堂页 h1 + 溯源链两处
        #: + Source Material 节点一处 + 新建课堂 toast = 7；材料表走结构化 helper。
        assert source.count("sessionLabel(") == 7
        parts_body = function_body(source, "function sessionDisplayParts(session) {")
        assert "session_id" not in parts_body, "助手本身不得感知句柄"
        assert "sessionDateLabel(session.date)" in parts_body
        assert "parseSessionTitle(session.title)" in parts_body
        for field in ("parsed.time", "parsed.kind", "parsed.room"):
            assert field in parts_body
        assert source.count("t('第 ')") == 1
        assert "t('第 ')" in parts_body and "t(' 堂')" in parts_body

    def test_the_shared_label_renders_the_real_schedule_and_localizes_the_weekday(self):
        """直接执行真实前端助手：真实课表字段一个不少，普通标题仍保留旧前缀。"""
        script = r"""
const fs = require('fs');
const vm = require('vm');
const root = process.cwd();
const storage = new Map();
const element = () => ({
  innerHTML: '', value: '', className: '', style: {},
  addEventListener() {}, removeEventListener() {}, appendChild() {},
});
const sandbox = {
  console,
  window: {
    localStorage: {
      getItem: (key) => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: (key) => storage.delete(key),
    },
    location: { hash: '#/' },
    addEventListener() {},
  },
  document: {
    documentElement: element(),
    addEventListener() {},
    getElementById: () => element(),
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => element(),
  },
  setTimeout() {},
  clearTimeout() {},
};
vm.createContext(sandbox);
for (const file of ['i18n.js', 'app.js', 'views/courses.js']) {
  vm.runInContext(fs.readFileSync(root + '/src/web/' + file, 'utf8'), sandbox, { filename: file });
}
function label(lang, session) {
  return vm.runInContext(
    'state.lang = ' + JSON.stringify(lang) + '; sessionLabel(' + JSON.stringify(session) + ')',
    sandbox,
  );
}
const full = {
  session_number: 1,
  date: '2026-09-24',
  title: '15:00–17:00 Teoria | Hiyern Yoon; Genís Riba | Aula Q2/1009',
};
const simple = { session_number: 3, date: '2026-03-01', title: 'Tema 3' };
console.log(JSON.stringify({
  full_zh: label('zh', full),
  full_es: label('es', full),
  full_ca: label('ca', full),
  datetime_zh: vm.runInContext(
    "state.lang = 'zh'; sessionDateLabel('2026-09-24T15:00')", sandbox,
  ),
  simple_zh: label('zh', simple),
  unlinked: ['zh', 'es', 'ca'].map((lang) => vm.runInContext(
    'state.lang = ' + JSON.stringify(lang) + '; t("未关联课堂")', sandbox,
  )),
}));
"""
        result = run_node_code(script)
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert payload["full_zh"] == (
            "2026-09-24（周四） · 第 1 堂 · 15:00–17:00 · Teoria · "
            "Hiyern Yoon; Genís Riba · Aula Q2/1009"
        )
        assert payload["full_es"] == (
            "24/09/2026 (jueves) · Sesión 1 · 15:00–17:00 · Teoria · "
            "Hiyern Yoon; Genís Riba · Aula Q2/1009"
        )
        assert payload["full_ca"] == (
            "24/09/2026 (dijous) · Sessió 1 · 15:00–17:00 · Teoria · "
            "Hiyern Yoon; Genís Riba · Aula Q2/1009"
        )
        for field in ("2026-09-24", "15:00–17:00", "Teoria", "Aula Q2/1009"):
            assert field in payload["full_zh"]
        for field in ("24/09/2026", "15:00–17:00", "Teoria", "Aula Q2/1009"):
            assert field in payload["full_es"]
            assert field in payload["full_ca"]
        for lang in ("es", "ca"):
            assert not re.search(r"[\u4e00-\u9fff]", payload["full_" + lang])
        assert payload["datetime_zh"] == "2026-09-24（周四）"
        assert payload["simple_zh"] == "2026-03-01（周日） · 第 3 堂 · Tema 3"
        assert payload["unlinked"] == ["未关联课堂", "Sin sesión asociada", "Sense sessió associada"]

    def test_the_materials_page_uses_the_full_label_and_keeps_the_real_option_value(self):
        """真实执行 pageMaterials：下拉值仍是 session_id，列表/下拉显示完整课表，未关联可读。"""
        script = r"""
const fs = require('fs');
const vm = require('vm');
const root = process.cwd();
const elements = new Map();
const storage = new Map([['ca.course', 'course-1']]);
const element = (id) => {
  if (!elements.has(id)) {
    elements.set(id, {
      id, innerHTML: '', value: '', className: '', style: {},
      addEventListener() {}, removeEventListener() {}, appendChild() {},
    });
  }
  return elements.get(id);
};
const session = {
  session_id: 'session-real-123',
  session_number: 1,
  date: '2026-09-24',
  title: '15:00–17:00 Teoria | Hiyern Yoon; Genís Riba | Aula Q2/1009',
};
const sandbox = {
  console,
  window: {
    localStorage: {
      getItem: (key) => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: (key) => storage.delete(key),
    },
    location: { hash: '#/materials' }, addEventListener() {}, scrollTo() {},
  },
  document: {
    documentElement: element('html'), addEventListener() {},
    getElementById: element, querySelector: () => null, querySelectorAll: () => [],
    createElement: element,
  },
  setTimeout() {}, clearTimeout() {},
};
vm.createContext(sandbox);
for (const file of ['i18n.js', 'app.js', 'views/materials.js', 'views/courses.js']) {
  vm.runInContext(fs.readFileSync(root + '/src/web/' + file, 'utf8'), sandbox, { filename: file });
}
sandbox.api = async (path) => {
  if (path === '/materials') return {
    materials: [
      {
        material_id: 'mat-linked', filename: 'linked.txt', extension: '.txt', size: 10,
        material_type: 'document', processing_status: 'SUCCEEDED', session_id: session.session_id,
      },
      {
        material_id: 'mat-unlinked', filename: 'unlinked.txt', extension: '.txt', size: 10,
        material_type: 'document', processing_status: 'SUCCEEDED', session_id: null,
      },
    ],
    analysis_statuses: {},
  };
  if (path === '/sessions') return { sessions: [session] };
  if (path === '/processing') return { by_status: {} };
  throw new Error('unexpected route ' + path);
};
(async () => {
  const out = {};
  for (const lang of ['zh', 'es', 'ca']) {
    vm.runInContext('state.lang = ' + JSON.stringify(lang), sandbox);
    await sandbox.pageMaterials();
    out[lang] = element('view').innerHTML;
  }
  console.log(JSON.stringify(out));
})().catch((error) => { console.error(error.stack); process.exitCode = 1; });
"""
        result = run_node_code(script)
        assert result.returncode == 0, result.stdout + result.stderr
        rendered = json.loads(result.stdout)
        expected = {
            "zh": "2026-09-24（周四） · 第 1 堂 · 15:00–17:00 · Teoria · Hiyern Yoon; Genís Riba · Aula Q2/1009",
            "es": "24/09/2026 (jueves) · Sesión 1 · 15:00–17:00 · Teoria · Hiyern Yoon; Genís Riba · Aula Q2/1009",
            "ca": "24/09/2026 (dijous) · Sessió 1 · 15:00–17:00 · Teoria · Hiyern Yoon; Genís Riba · Aula Q2/1009",
        }
        material_parts = {
            "zh": ("2026-09-24（周四）", "第 1 堂 · 15:00–17:00", "Teoria · Aula Q2/1009"),
            "es": ("24/09/2026 (jueves)", "Sesión 1 · 15:00–17:00", "Teoria · Aula Q2/1009"),
            "ca": ("24/09/2026 (dijous)", "Sessió 1 · 15:00–17:00", "Teoria · Aula Q2/1009"),
        }
        unlinked = {
            "zh": "未关联课堂",
            "es": "Sin sesión asociada",
            "ca": "Sense sessió associada",
        }
        for lang, html in rendered.items():
            assert '<option value="session-real-123">' in html
            assert ">" + expected[lang] + "</option>" in html
            for part in material_parts[lang]:
                assert ">" + part + "<" in html
            assert unlinked[lang] in html
            assert "session-real-123</td>" not in html
            if lang != "zh":
                assert not re.search(r"[\u4e00-\u9fff]", html)

    def test_the_session_page_no_longer_prints_the_raw_id(self):
        """课堂页副标题与课程页对齐: 课程页显示 ``code · language``, 不是 course_id。

        日期必须留着 —— 它是那个页面上**唯一**的日期来源。
        """
        body = function_body(read_asset("app.js"), "async function pageSession(")
        assert "esc(session.session_id)" not in body
        assert "esc(session.date || '')" in body


# ===========================================================================
# 1d. session_id: 与 course_id 同一条规则
# ===========================================================================


class TestTheSessionIdIsNeverRenderedAsVisibleText:
    """``session_id`` 与 ``course_id`` 同源, 所以规则也同一条。

    ``ClassSession._generate_stable_id`` 里
    ``"session-" + sha256(course_id + 课号)[:16]`` —— 用户既猜不出也记不住, 这正是
    用户报的文本框 (``placeholder="session-…"``) 的根源。
    """

    #: 修复前**真实存在过**的写法 (temp/_app_before_session_picker.js 里逐字可查)。
    REMOVED_SHAPES = (
        # 材料列表的"课堂"列
        "'<td class=\"small\">' + esc(m.session_id || '—') + '</td>'",
        # 课堂页副标题
        "'<p class=\"subtitle mono small\">' + esc(session.session_id) +",
        # 溯源链的"组织归属"
        "'\">' + esc(s.session_id) + '</a>').join('<br>')",
        # 知识点详情页 Source Material 节点的"课堂"行 —— **没有 esc**,
        # 所以只有把 dash() 也纳入扫描才抓得到 (实测漏过一处)。
        "'<dt>' + t('课堂') + '</dt><dd>' + dash(material.session_id) + '</dd>'",
    )

    #: 上传表单那处不是 ``esc(...)`` (是个 placeholder), 判据扫不到 —— 它由
    #: ``TestTheSessionPickerIsADropdown.test_the_form_renders_a_select_not_a_text_input``
    #: 单独钉住。这里如实说明边界, 免得读者以为这一组已经覆盖了它。

    #: 阴性对照: 属性值 + 两个助手的最后手段。
    ALLOWED_SHAPES = (
        "'<option value=\"' + esc(s.session_id) + '\">'",
        "'/sessions/' + encodeURIComponent(s.session_id) + '\">'",
        "' data-session=\"' + esc(sessionId) + '\"'",
        "esc(sessionLabel(s, s.session_id))",
        "esc(sessionLabel(sessionById[m.session_id], m.session_id))",
    )

    def test_no_session_id_is_rendered_as_visible_text(self):
        offenders = session_id_text_offenders(read_asset("app.js"))
        assert not offenders, (
            "session_id 被当成可见文案渲染 (行号): %r —— 应改用 sessionLabel() "
            "或把它放进属性值" % (offenders,)
        )

    def test_the_criterion_catches_every_shape_we_removed(self):
        """判据**不是空跑**: 修复前的每一种写法喂进去都必须报红。

        "不许出现 X"这类断言只要指纹写错就永远绿, 等于没写。输入是修复前真实存在
        过的源码片段, 配一组阴性对照证明它也不是"见谁报谁"。
        """
        for shape in self.REMOVED_SHAPES:
            assert session_id_text_offenders(shape) == [1], shape
        for shape in self.ALLOWED_SHAPES:
            assert session_id_text_offenders(shape) == [], shape

    def test_the_criterion_scans_dash_as_well_as_esc(self):
        """判据扫的是"把值渲染成可见文本"的助手, 不止 ``esc()``。

        为什么单独一条: 2026-09-20 实测漏过一处 ``dash(material.session_id)`` ——
        它没有 ``esc`` 包装, 旧判据完全看不见, 是 ``scripts/ui_audit.js`` 的运行期
        检查抓到的。这条断言把"两个助手都要扫"钉成事实, 而不是靠记忆。
        """
        assert _TEXT_HELPERS == ("esc", "dash")
        assert text_render_arguments("'<dd>' + dash(x.session_id) + '</dd>'") == [(1, "x.session_id", False)]
        assert text_render_arguments("'<dd>' + esc(x.session_id) + '</dd>'") == [(1, "x.session_id", False)]
        #: 属性值里的 dash 同样是合法的 (虽然现在一处都没有, 规则要一致)。
        assert text_render_arguments("'value=\"' + dash(x.session_id) + '\"'") == [(1, "x.session_id", True)]

    def test_the_course_criterion_still_holds(self):
        """两条判据共用同一个扫描器 —— 顺带确认没有把课程那条弄坏。"""
        assert course_id_text_offenders(read_asset("app.js")) == []
        for shape in TestCourseNameIsShownInsteadOfItsId.REMOVED_SHAPES:
            assert course_id_text_offenders(shape) == [1], shape

    def test_the_id_can_only_reach_the_screen_through_the_one_helper(self):
        """全文件只有 ``sessionLabel()`` 允许把句柄当最后手段。

        这是"没有例外"这句话的**可执行**版本: 兜底只允许存在一处, 而且必须在那个
        助手里。多一处就说明又有人开始把哈希往界面上塞。
        """
        source = strip_js_comments(read_asset("app.js"))
        body = function_body(source, "function sessionLabel(session, fallback) {")
        assert "return fallback || '';" in body
        #: 助手之外的任何地方都不许出现 `|| <id>` 这种兜底。
        assert source.count("|| sessionId") == 0
        assert source.count("|| s.session_id") == 0
        assert source.count("|| m.session_id") == 0


# ===========================================================================
# 2. 学生页面删除 (反向契约)
# ===========================================================================


class TestRemovedStudentPages:
    """前端不再提供学生列表 / 注册 / 详情, 后端学生 API 不受此影响。"""

    def test_student_page_functions_are_removed(self):
        source = strip_js_comments(read_asset("app.js"))
        assert "async function pageStudents(" not in source
        assert "async function pageStudent(" not in source
        assert "function wireStudentForm(" not in source
        assert "function renderPathChain(" not in source

    def test_student_page_routes_and_nav_link_are_removed(self):
        source = read_asset("app.js")
        html = read_asset("index.html")
        assert "parts[0] === 'students'" not in source
        assert "parts[2] === 'students'" not in source
        assert 'href="#/students"' not in html
        assert "nav.students" not in html

    def test_the_empty_state_is_text_only(self):
        source = strip_js_comments(read_asset("app.js"))
        body = function_body(source, "function noStudentsCard(")
        assert "student.none" in body
        assert 'href="#/students"' not in body
        assert "student.register" not in body
        assert source.count("function noStudentsCard(") == 1
        assert source.count("noStudentsCard()") == 3

    def test_backend_student_endpoints_are_not_removed(self):
        """反向契约不能误伤后端 API。"""
        source = read_asset("app.js")
        assert "'/students/'" in source


# ===========================================================================
# 3. 文案 (三语)
# ===========================================================================


class TestI18n:
    def test_student_none_remains_in_all_three_languages(self):
        tables = i18n_tables()
        for lang in ("zh", "es", "ca"):
            missing = sorted(k for k in RETAINED_STUDENT_KEYS if k not in tables[lang])
            assert not missing, f"{lang} 缺少: {missing}"
        assert tables["zh"] == tables["es"] == tables["ca"]

    def test_removed_student_detail_keys_are_gone(self):
        tables = i18n_tables()
        for lang in ("zh", "es", "ca"):
            present = sorted(k for k in REMOVED_STUDENT_UI_KEYS if k in tables[lang])
            assert not present, f"{lang} 仍保留已删除的详情 key: {present}"

    def test_every_used_student_key_still_resolves(self):
        source = read_asset("app.js")
        tables = i18n_tables()
        for key in re.findall(r"\bt\(\s*'(student\.[A-Za-z0-9_]+)'", source):
            assert key in tables["zh"], f"悬空 key: {key}"

    def test_detail_keys_do_not_reappear_in_sentence_translations(self):
        source = read_asset("app.js")
        block = source[source.index("const TRANSLATIONS = {"):]
        for key in REMOVED_STUDENT_UI_KEYS:
            assert f"'{key}':" not in block, f"{key} 不该出现在整句译文表里"


# ===========================================================================
# 4. 真实端点: 表单的载荷形状
# ===========================================================================


class TestRegistrationEndpointAcceptsTheFormPayload:
    """UI 发什么, 端点就得收什么 —— 这一组用真实 HTTP 走一遍。"""

    def test_a_student_without_a_name_can_be_registered(self, client, course):
        status, payload = client.post(
            "/api/students", {"course_id": course["course_id"], "student_id": "2026-001"}
        )
        assert status == 201, payload
        assert payload["data"]["student_id"] == "2026-001"
        assert payload["data"]["display_name"] is None

    def test_a_student_with_a_name_can_be_registered(self, client, course):
        status, payload = client.post(
            "/api/students",
            {
                "course_id": course["course_id"],
                "student_id": "2026-002",
                "display_name": "Rafae",
            },
        )
        assert status == 201, payload
        assert payload["data"]["display_name"] == "Rafae"

    def test_the_course_id_is_read_from_the_body(self, client, course):
        """表单把 course_id 放在 body 里 (查询串只留给 GET)。"""
        status, payload = client.post(
            "/api/students", {"course_id": course["course_id"], "student_id": "2026-003"}
        )
        assert status == 201, payload

    def test_registering_the_same_student_twice_is_idempotent(self, client, course):
        body = {"course_id": course["course_id"], "student_id": "2026-004"}
        first_status, first = client.post("/api/students", body)
        second_status, second = client.post("/api/students", body)
        assert (first_status, second_status) == (201, 200)
        assert first["data"] == second["data"]

    def test_changing_the_name_on_a_second_registration_is_a_conflict(
        self, client, course
    ):
        """UI 必须把这条原样报给用户, 而不是自己编一个判定。"""
        course_id = course["course_id"]
        client.post(
            "/api/students",
            {"course_id": course_id, "student_id": "2026-005", "display_name": "Ana"},
        )
        status, payload = client.post(
            "/api/students",
            {"course_id": course_id, "student_id": "2026-005", "display_name": "Bea"},
        )
        assert status == 409, payload
        assert payload["error"]["code"] == "CONFLICT"

    def test_a_blank_student_id_is_rejected(self, client, course):
        status, payload = client.post(
            "/api/students", {"course_id": course["course_id"], "student_id": ""}
        )
        assert status == 400, payload
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_the_registered_student_appears_in_the_list_the_page_renders(
        self, client, course
    ):
        course_id = course["course_id"]
        client.post(
            "/api/students", {"course_id": course_id, "student_id": "2026-006"}
        )
        status, payload = client.get(f"/api/students?course_id={course_id}")
        assert status == 200, payload
        assert [s["student_id"] for s in payload["data"]["students"]] == ["2026-006"]

    def test_students_do_not_leak_across_courses(self, client, course):
        other_status, other = client.post("/api/courses", {"name": "Altres", "code": "AL"})
        assert other_status == 201, other
        client.post(
            "/api/students", {"course_id": course["course_id"], "student_id": "2026-007"}
        )
        _, payload = client.get(f"/api/students?course_id={other['data']['course_id']}")
        assert payload["data"]["students"] == []

    def test_the_display_name_is_preserved_verbatim(self, client, course):
        """原文 (加泰语 / 西语 / 中文) 逐字保留, 不做任何改写。"""
        name = "Gestió d'Álgebra — 张三"
        status, payload = client.post(
            "/api/students",
            {
                "course_id": course["course_id"],
                "student_id": "2026-008",
                "display_name": name,
            },
        )
        assert status == 201, payload
        assert payload["data"]["display_name"] == name
        _, listing = client.get(f"/api/students?course_id={course['course_id']}")
        assert listing["data"]["students"][0]["display_name"] == name


# ===========================================================================
# 4b. 真实端点: 下拉的 option value 就是上传接口认得的东西
# ===========================================================================


class TestThePickerValueIsWhatTheUploadEndpointConsumes:
    """静态断言只能证明"渲染出来的是 ``session_id``"。

    这一组用真实 HTTP 证明**把那个值原样发回去, 材料确实挂到了那堂课上** ——
    否则下拉做得再好看, 选完也是白选。
    """

    @staticmethod
    def _create_session(client, course_id: str, number: int, title: str) -> str:
        status, payload = client.post(
            "/api/sessions",
            {"course_id": course_id, "session_number": number, "title": title},
        )
        assert status == 201, payload
        return payload["data"]["session_id"]

    def test_the_sessions_endpoint_gives_the_picker_everything_it_labels_with(
        self, client, course
    ):
        """标签用的是 ``session_number`` + ``title`` —— 这两个字段必须真的在响应里。

        少了它们 ``sessionLabel()`` 会静默退化成"只显示日期"甚至"显示哈希", 而界面
        看起来仍然"有选项" —— 没有异常可抓, 只能显式断言。
        """
        course_id = course["course_id"]
        session_id = self._create_session(client, course_id, 3, "Tema 3")
        status, payload = client.get(f"/api/sessions?course_id={course_id}")
        assert status == 200, payload
        rows = payload["data"]["sessions"]
        assert [row["session_id"] for row in rows] == [session_id]
        assert rows[0]["session_number"] == 3
        assert rows[0]["title"] == "Tema 3"

    def test_sessions_do_not_leak_across_courses(self, client, course):
        """下拉只能列出**当前课程**的课堂 —— 否则用户会把材料挂到别的课上。"""
        other_status, other = client.post("/api/courses", {"name": "Altres", "code": "AL"})
        assert other_status == 201, other
        self._create_session(client, course["course_id"], 1, "Tema 1")
        _, payload = client.get(f"/api/sessions?course_id={other['data']['course_id']}")
        assert payload["data"]["sessions"] == []

    def test_the_option_value_links_the_material_to_that_session(self, client, course):
        """``<option value="<session_id>">`` 那一支。"""
        course_id = course["course_id"]
        session_id = self._create_session(client, course_id, 1, "Tema 1")
        status, payload = client.post_bytes(
            f"/api/materials?course_id={course_id}&session_id={session_id}"
            "&filename=apuntes-tema1.txt",
            "Una funció és una relació.".encode("utf-8"),
            content_type="text/plain",
        )
        assert status == 201, payload
        assert payload["data"]["session_id"] == session_id
        _, listing = client.get(f"/api/materials?course_id={course_id}")
        assert listing["data"]["materials"][0]["session_id"] == session_id

    def test_choosing_no_session_leaves_the_material_unlinked(self, client, course):
        """``<option value="">`` 那一支: 不传 ``session_id``, 材料照样登记。"""
        course_id = course["course_id"]
        status, payload = client.post_bytes(
            f"/api/materials?course_id={course_id}&filename=temari.txt",
            "Temari del curs.".encode("utf-8"),
            content_type="text/plain",
        )
        assert status == 201, payload
        assert not payload["data"]["session_id"]
        _, listing = client.get(f"/api/materials?course_id={course_id}")
        assert not listing["data"]["materials"][0]["session_id"]


# ===========================================================================
# 5. 渲染 harness
# ===========================================================================


class TestUiAuditCoversTheReportedProblems:
    """``scripts/ui_audit.js`` 必须真的执行页面函数来证明这几件事。"""

    @staticmethod
    def _script() -> str:
        return (Path(__file__).resolve().parent.parent / "scripts" / "ui_audit.js").read_text(
            encoding="utf-8"
        )

    def test_the_audit_checks_the_course_name_instead_of_the_id(self):
        script = self._script()
        assert "never renders the raw course id as text" in script
        assert "loadSidebar" in script, "必须先跑侧边栏, 否则 courseLabel 拿到的是空缓存"

    def test_the_audit_checks_the_removed_student_pages(self):
        script = self._script()
        assert "pageStudents" in script
        assert "student list and detail page functions are absent" in script
        assert "registration form is not wired" in script

    def test_the_audit_checks_the_session_dropdown(self):
        """静态检查只能证明"源码里写了 select", 证明不了表单里真有选项。

        所以审计必须真的渲染 ``pageMaterials()``, 而且夹具里要有课堂列表 ——
        少了 ``/api/sessions`` 路由, 页面直接 404, 检查会因为异常而"红得有道理",
        但红的原因不是产品缺陷, 而是夹具缺了东西。
        """
        script = self._script()
        assert "materials page renders a session dropdown" in script
        assert "the session dropdown lists the course sessions" in script
        assert "the materials table shows the real session date and label, not its id" in script
        assert "table['/api/sessions']" in script

    def test_the_audit_passes(self):
        script = Path(__file__).resolve().parent.parent / "scripts" / "ui_audit.js"
        result = run_node_script(script)
        assert result.returncode == 0, result.stdout + result.stderr


# ===========================================================================
# 6. 真实服务端到端 (自带服务的那个)
# ===========================================================================


class TestTheLiveSessionPickerEndToEnd:
    """``scripts/e2e_session_picker.js`` 覆盖 ``temp/e2e_student_ui.js`` 覆盖不到的一支。

    用户库里的 ``sessions`` 表是空的, 而那支 e2e 的契约是**只读** —— 所以它只能测
    "空课堂"分支。"有课堂时选项渲染成 ``第 1 堂 · Tema 1``" 这件事, 此前只在
    ``scripts/ui_audit.js`` 的夹具上证明过, 没有在**真实 HTTP 响应**上证明过。

    这个脚本自己 spawn 一个用临时数据目录的 launcher, 在里面建课程 / 两堂课 /
    一份材料, 再渲染真实材料页。跑完删临时目录、kill 服务 —— 不碰 ``classroom-data/``。
    """

    def test_the_live_script_passes(self):
        script = Path(__file__).resolve().parent.parent / "scripts" / "e2e_session_picker.js"
        result = run_node_script(script, timeout=300)
        assert result.returncode == 0, result.stdout + result.stderr
        # 断言**脚本自己报的成功行**, 而不只是退出码 0 —— 否则脚本把每个 check
        # 都写成 no-op 也能绿。
        assert "E2E (live, with sessions) OK" in result.stdout, result.stdout
        assert "2 sessions" in result.stdout, result.stdout
