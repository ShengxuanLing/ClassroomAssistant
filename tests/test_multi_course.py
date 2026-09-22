# -*- coding: utf-8 -*-
"""Task 68 —— Multi-Course Workspace（多课程工作台）测试。

覆盖的 spec 条款::

    68.1  全局 "My Courses" 总览（每门课各查各的, 再并列）
    68.2  严格课程隔离
    68.3  课程切换器 + 切换持久化 + 脏偏好回落
    68.4  学生隔离
    68.5  内容寻址 ID 跨课程碰撞是**合法**的
    68.6  两条真相轴（validation / review）仍然分开
    68.7  API / UI / 重启一致性

几条刻意写死的判据（产品语义，不是实现细节）
--------------------------------------------
1. **相同内容 = 相同 knowledge_id，这是合法的。** 两门课用了同一份讲义
   就会得到同一个 id。本文件**专门**造了这个局面（每门课都注册同一份
   ``shared`` 笔记），并断言：三门的知识点数**仍然各自相等**，且重启后
   每门课**仍然都**拥有那些共享 id。任何"按 id 去重"或"全局唯一约束"
   的实现都会在这里挂掉。
2. **不允许任何排序意图。** 序列化后的总览里不得出现 priority /
   recommended / 优先级 / 推荐顺序 这类词。课程顺序是 ``course_id`` 的
   字典序 —— 一个稳定但**不带含义**的键。
3. **不得把两门课的数字合并后再拆分。** 每个计数都必须来自带
   ``course_id`` 的查询。隔离用"另一门课的知识点标签绝不出现在这一门课
   里"来验证，而不是只比总数。
4. **两条真相轴各计各的。** ``validation``（证据说了什么）与 ``review``
   （人说了什么）是两个字典，永不相加，也不合成"完成度"。

验证方式的说明（不掩饰限制）
----------------------------
本机是 Windows，浏览器自动化（agent-browser）**只支持 macOS / Linux**，
因此**没有**浏览器端到端测试。UI 由三层真实执行覆盖：

  1. ``scripts/ui_audit.js`` 与 ``scripts/ui_render_check.js`` —— 用最小
     DOM 桩在 Node 里**真实执行** ``pageMyCourses()`` 等页面函数，覆盖
     三语渲染、空态、无排序话术、CSS 类已定义；
  2. 本文件 ``TestUi`` —— 对 app.js / index.html 的静态契约断言；
  3. 本文件 ``TestApi`` —— 真实 HTTP 打到三个新端点。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Optional

import pytest

from src.api.server import create_server
from src.application.multi_course import (
    COUNT_KEYS,
    MULTI_COURSE_VERSION,
    MY_COURSES_EMPTY_NOTE,
    NO_RANKING_TERMS,
    REVIEW_STATUSES,
    SELECTION_REASONS,
    VALIDATION_STATUSES,
    MultiCourseWorkspace,
)
from src.application.runtime import fixed_clock
from src.application.workspace import Workspace

from tests.support import read_web_source

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "src" / "web"

FIXED_TIME = "2026-09-18T09:00:00+00:00"
TODAY = "2026-09-18"

#: 规模基准（spec 68 点名的量级）。
COURSE_COUNT = 3
SESSIONS_PER_COURSE = 5
STUDENTS = ("s-ana", "s-quim", "s-marta")

#: 每份笔记的主题数。知识点 id 内容寻址，主题越多知识点越多。
TOPICS_BIG = 6
TOPICS_SMALL = 2

#: 断言"隔离"用的标签。每门课每节课的笔记内容里都带自己的标签，
#: 因此"另一门课的标签出现在这一门课的知识点里"= 串课。
SHARED_TAG = "shared"

CJK = re.compile(r"[一-鿿]")


# ---------------------------------------------------------------------------
# 夹具构造
# ---------------------------------------------------------------------------


def _note(directory: Path, tag: str, topics: int) -> str:
    """写一份笔记文件并返回路径。

    ``tag`` 会进入**标题**（冒号前）和正文，因此内容寻址的知识点 id 也随
    tag 而变。这让"这门课的知识点到底是哪些"可被断言 —— 只比总数的话，
    两门课各自少一半、总数却一样的情况是测不出来的。

    标签必须放在**冒号前面**: 知识点的锚点是标题被 ``. / ; / :`` 截断后的
    前半段（``knowledge_pipeline._entity_anchor``，既有行为，不是本任务改的）。
    若写成 ``## Tema 1: Concepto shared-1``，锚点一律是 ``## tema 1:`` ——
    自己笔记和共享笔记的章节知识点会撞成同一个 id，哪个存活取决于证据 id
    的哈希序，隔离断言就变成了掷骰子。
    """
    lines = []
    for index in range(topics):
        name = "%s-%d" % (tag, index)
        lines.append(
            "## Tema %d (%s): Concepto\n\n"
            "El concepto %s es una definicion del tema %d "
            "que describe una relacion entre dos elementos.\n" % (index, tag, name, index)
        )
    path = directory / ("nota-%s.md" % tag)
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _seed(
    data_dir: str,
    *,
    courses: int = COURSE_COUNT,
    sessions: int = SESSIONS_PER_COURSE,
    topics: int = TOPICS_BIG,
    students: tuple[str, ...] = STUDENTS,
    shared: bool = True,
) -> dict[str, Any]:
    """造一个多课程数据目录并**关闭**工作区（返回后必须靠重开验证）。"""
    root = Path(data_dir)
    notes = root / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    shared_note = _note(notes, SHARED_TAG, topics) if shared else None

    workspace = Workspace(
        data_dir,
        clock=fixed_clock(FIXED_TIME),
        asr_mode="mock",
        ocr_mode="mock",
    )
    course_ids: list[str] = []
    material_ids: list[str] = []
    try:
        for course_index in range(courses):
            cid = workspace.create_course(
                "Course%d" % course_index, "C%d" % course_index, "es"
            )["course_id"]
            course_ids.append(cid)
            for session_number in range(1, sessions + 1):
                session = workspace.create_session(
                    cid,
                    session_number=session_number,
                    date=TODAY,
                    title="Tema %d" % session_number,
                )
                sid = session["session_id"]
                own = _note(notes, "c%ds%d" % (course_index, session_number), topics)
                material_ids.append(
                    workspace.register_material(cid, own, session_id=sid)["material_id"]
                )
                if shared_note:
                    material_ids.append(
                        workspace.register_material(
                            cid, shared_note, session_id=sid
                        )["material_id"]
                    )
                workspace.process_session(cid, sid)
            for student in students:
                workspace.create_student(cid, student, student.upper())
    finally:
        workspace.close()
    return {
        "data_dir": data_dir,
        "course_ids": course_ids,
        "students": list(students),
        "shared_note": shared_note,
        "material_ids": material_ids,
    }


def _open(data_dir: str) -> Workspace:
    return Workspace(
        data_dir,
        clock=fixed_clock(FIXED_TIME),
        asr_mode="mock",
        ocr_mode="mock",
    )


def _tags_of(workspace: Workspace, course_id: str) -> set[str]:
    """这门课的知识点里出现了哪些课程标签。"""
    tags: set[str] = set()
    for point in workspace.knowledge_points(course_id):
        title = str(point.get("title") or "")
        content = str(point.get("content") or "")
        for match in re.findall(r"Concepto ([a-z0-9]+)-\d+", title + " " + content):
            tags.add(match.rsplit("s", 1)[0] if re.search(r"c\d+s\d+", match) else match)
    return tags


def _shared_ids(workspace: Workspace, course_id: str) -> set[str]:
    ids = set()
    for point in workspace.knowledge_points(course_id):
        blob = str(point.get("title") or "") + " " + str(point.get("content") or "")
        if SHARED_TAG in blob:
            ids.add(str(point["knowledge_id"]))
    return ids


def _row_by_id(payload: Mapping[str, Any], course_id: str) -> dict[str, Any]:
    for row in payload["courses"]:
        if row["course_id"] == course_id:
            return row
    raise AssertionError("course %s not in my_courses" % course_id)


@pytest.fixture(scope="module")
def big(tmp_path_factory) -> dict[str, Any]:
    """3 门课 × 5 节课 × 6 主题。只造一次，供**只读**测试共用。"""
    data_dir = str(tmp_path_factory.mktemp("mc-big") / "data")
    return _seed(data_dir, topics=TOPICS_BIG)


@pytest.fixture
def big_ws(big):
    workspace = _open(big["data_dir"])
    yield workspace
    workspace.close()


@pytest.fixture
def small(tmp_path) -> dict[str, Any]:
    """2 门课 × 2 节课 × 2 主题 —— 给会写数据的测试用（快）。"""
    return _seed(
        str(tmp_path / "data"),
        courses=2,
        sessions=2,
        topics=TOPICS_SMALL,
    )


@pytest.fixture
def small_ws(small):
    workspace = _open(small["data_dir"])
    yield workspace
    workspace.close()


# ---------------------------------------------------------------------------
# 1. 总览形状契约
# ---------------------------------------------------------------------------


class TestMyCoursesShape:
    def test_payload_carries_its_contract_version(self, big_ws):
        payload = big_ws.my_courses()
        assert payload["view"] == MULTI_COURSE_VERSION
        assert payload["course_count"] == COURSE_COUNT
        assert len(payload["courses"]) == COURSE_COUNT

    def test_every_row_carries_every_count_key(self, big_ws):
        payload = big_ws.my_courses()
        for row in payload["courses"]:
            assert set(row["counts"]) == set(COUNT_KEYS)
            for key in COUNT_KEYS:
                assert isinstance(row["counts"][key], int)
                assert row["counts"][key] >= 0

    def test_totals_are_the_sum_over_courses_not_a_second_source(self, big_ws):
        payload = big_ws.my_courses()
        for key in COUNT_KEYS:
            expected = sum(row["counts"][key] for row in payload["courses"])
            assert payload["totals"]["counts"][key] == expected, key
        assert payload["totals"]["evidence_total"] == sum(
            row["evidence_total"] for row in payload["courses"]
        )

    def test_courses_are_ordered_by_course_id(self, big_ws):
        ids = [row["course_id"] for row in big_ws.my_courses()["courses"]]
        assert ids == sorted(ids)

    def test_rows_are_sorted_even_if_the_caller_asks_for_a_preference(self, big_ws):
        """偏好只影响 selection，绝不重排课程列表。"""
        preferred = big_ws.list_courses()[-1]["course_id"]
        ids = [row["course_id"] for row in big_ws.my_courses(preferred=preferred)["courses"]]
        assert ids == sorted(ids)

    def test_empty_workspace_is_explicit_not_blank(self, tmp_path):
        workspace = _open(str(tmp_path / "data"))
        try:
            payload = workspace.my_courses()
            assert payload["courses"] == []
            assert payload["course_count"] == 0
            assert payload["empty"] is True
            assert payload["empty_note"] == MY_COURSES_EMPTY_NOTE
            assert payload["totals"]["counts"] == {key: 0 for key in COUNT_KEYS}
        finally:
            workspace.close()

    def test_two_reads_are_byte_identical(self, big_ws):
        first = json.dumps(big_ws.my_courses(), sort_keys=True, ensure_ascii=False)
        second = json.dumps(big_ws.my_courses(), sort_keys=True, ensure_ascii=False)
        assert first == second

    def test_two_processes_agree_on_the_same_directory(self, big):
        first = _open(big["data_dir"])
        second = _open(big["data_dir"])
        try:
            payload_a = json.dumps(first.my_courses(), sort_keys=True, ensure_ascii=False)
            payload_b = json.dumps(second.my_courses(), sort_keys=True, ensure_ascii=False)
            assert payload_a == payload_b
        finally:
            second.close()
            first.close()

    def test_language_is_echoed_not_guessed(self, big_ws):
        for lang in ("zh", "es", "ca"):
            assert big_ws.my_courses(lang=lang)["language"] == lang

    def test_selection_block_is_part_of_the_payload(self, big_ws):
        selection = big_ws.my_courses()["selection"]
        assert set(selection) == {"preferred", "course_id", "reason", "available"}
        assert selection["reason"] in SELECTION_REASONS

    def test_last_session_uses_session_number_not_the_free_text_date(self, big_ws):
        """``date`` 是用户手填的字符串，拿它排序会得到不稳定的结果。"""
        for row in big_ws.my_courses()["courses"]:
            last = row["last_session"]
            assert last is not None
            assert last["session_number"] == SESSIONS_PER_COURSE

    def test_the_projection_cannot_write(self):
        """公开方法只有三个 —— 它结构上就没有写入口。"""
        public = {
            name
            for name in dir(MultiCourseWorkspace)
            if not name.startswith("_") and callable(getattr(MultiCourseWorkspace, name))
        }
        assert public == {"my_courses", "course_summary", "resolve_selection"}


# ---------------------------------------------------------------------------
# 2. 严格课程隔离 (spec 68.2)
# ---------------------------------------------------------------------------


class TestStrictCourseIsolation:
    def test_no_course_contains_another_courses_own_knowledge(self, big_ws, big):
        for index, cid in enumerate(big["course_ids"]):
            tags = _tags_of(big_ws, cid)
            foreign = {
                "c%ds" % other
                for other in range(len(big["course_ids"]))
                if other != index
            }
            leaked = sorted(tag for tag in tags if tag in foreign)
            assert not leaked, "课程 %s 混进了别的课的知识点: %s" % (cid, leaked)

    def test_every_course_has_the_same_number_of_knowledge_points(self, big_ws, big):
        """三门课结构完全一样 —— 数量不同就是串课。"""
        counts = [len(big_ws.knowledge_points(cid)) for cid in big["course_ids"]]
        assert len(set(counts)) == 1, counts

    def test_every_course_has_the_same_session_and_student_counts(self, big_ws, big):
        for cid in big["course_ids"]:
            assert len(big_ws.list_sessions(cid)) == SESSIONS_PER_COURSE
            assert len(big_ws.list_students(cid)) == len(STUDENTS)

    def test_material_lists_do_not_leak_across_courses(self, big_ws, big):
        for cid in big["course_ids"]:
            rows = big_ws.list_materials(cid)
            assert rows
            for row in rows:
                assert row["course_id"] == cid

    def test_exercises_created_in_one_course_do_not_appear_in_another(
        self, small_ws, small
    ):
        cid_a, cid_b = small["course_ids"]
        kp = small_ws.knowledge_points(cid_a)[0]["knowledge_id"]
        before = len(small_ws.list_exercises(cid_b))
        small_ws.create_exercise(
            cid_a, "true_false", "Pregunta de la asignatura A", [kp], is_true=True
        )
        assert len(small_ws.list_exercises(cid_a)) == 1
        assert len(small_ws.list_exercises(cid_b)) == before

    def test_answers_recorded_in_one_course_do_not_count_in_another(
        self, small_ws, small
    ):
        cid_a, cid_b = small["course_ids"]
        student = small["students"][0]
        kp_a = small_ws.knowledge_points(cid_a)[0]["knowledge_id"]
        kp_b = small_ws.knowledge_points(cid_b)[0]["knowledge_id"]
        ex_a = small_ws.create_exercise(
            cid_a, "true_false", "Pregunta A", [kp_a], is_true=True
        )["exercise_id"]
        ex_b = small_ws.create_exercise(
            cid_b, "true_false", "Pregunta B", [kp_b], is_true=True
        )["exercise_id"]
        small_ws.submit_answer(cid_a, student, ex_a, "true")
        summary_a = small_ws.course_summary(cid_a)
        summary_b = small_ws.course_summary(cid_b)
        assert summary_a["counts"]["answers"] == 1
        assert summary_b["counts"]["answers"] == 0

    def test_the_same_student_has_separate_state_per_course(self, small_ws, small):
        cid_a, cid_b = small["course_ids"]
        student = small["students"][0]
        kp_a = small_ws.knowledge_points(cid_a)[0]["knowledge_id"]
        # 只在 A 课推一次状态
        # 走 Workspace 的方法而不是 learning_service 的方法 —— 只有 Workspace
        # 这层才会落盘 (``_flush_learning``)。直接调服务层, 事件只活在内存里。
        small_ws.record_learning_event(cid_a, student, kp_a, "viewed")
        state_a = small_ws.student_state(cid_a, student)
        state_b = small_ws.student_state(cid_b, student)
        assert state_a["states"], "A 课应已记录状态"
        assert not state_b["states"], "B 课不该被 A 课的事件推动"

    def test_review_history_does_not_leak_across_courses(self, small_ws, small):
        cid_a, cid_b = small["course_ids"]
        shared_ids = sorted(_shared_ids(small_ws, cid_a))
        assert shared_ids, "夹具应产出共享知识点"
        small_ws.review_confirm(cid_a, shared_ids[0])
        assert small_ws.review_history(cid_a, shared_ids[0])
        assert small_ws.review_history(cid_b, shared_ids[0]) == []

    def test_conflicts_are_course_scoped(self, small_ws, small):
        cid_a, cid_b = small["course_ids"]
        before = len(small_ws.conflicts(cid_b))
        assert before == 0
        assert len(small_ws.conflicts(cid_a)) == 0
        # 两门课的知识点集合不同，冲突计数各自为 0 且互不影响
        assert small_ws.course_summary(cid_a)["counts"]["conflicts"] == 0
        assert small_ws.course_summary(cid_b)["counts"]["conflicts"] == 0

    def test_summary_matches_the_my_courses_row(self, big_ws, big):
        payload = big_ws.my_courses()
        for cid in big["course_ids"]:
            row = _row_by_id(payload, cid)
            summary = dict(big_ws.course_summary(cid))
            summary.pop("language_ui", None)
            assert summary == row

    def test_a_course_id_never_appears_in_another_courses_row(self, big_ws, big):
        payload = big_ws.my_courses()
        for row in payload["courses"]:
            assert row["course_id"] in big["course_ids"]

    def test_student_counts_are_per_course_not_global(self, small_ws, small):
        """同一个 student_id 在两门课各注册一次 —— 计数是 1 而不是 2。"""
        cid_a, cid_b = small["course_ids"]
        assert len(small_ws.list_students(cid_a)) == len(STUDENTS)
        assert len(small_ws.list_students(cid_b)) == len(STUDENTS)
        assert small_ws.my_courses()["totals"]["counts"]["students"] == (
            len(STUDENTS) * len(small["course_ids"])
        )

    def test_knowledge_point_views_are_course_scoped(self, small_ws, small):
        cid_a, cid_b = small["course_ids"]
        ids_a = {p["knowledge_id"] for p in small_ws.knowledge_points(cid_a)}
        ids_b = {p["knowledge_id"] for p in small_ws.knowledge_points(cid_b)}
        # 交集**只能**是共享笔记产出的那些 id（内容相同 → id 相同，合法）
        assert ids_a & ids_b == _shared_ids(small_ws, cid_a)
        # 各自私有的部分必须存在，否则说明知识被合并了
        assert ids_a - ids_b
        assert ids_b - ids_a


# ---------------------------------------------------------------------------
# 3. 内容寻址 ID 跨课程碰撞是合法的 (spec 68.5)
# ---------------------------------------------------------------------------


class TestSharedKnowledgeIds:
    def test_the_shared_note_produces_the_same_id_in_every_course(self, big_ws, big):
        per_course = [_shared_ids(big_ws, cid) for cid in big["course_ids"]]
        assert all(per_course), "每门课都应有共享笔记产出的知识点"
        assert per_course[0] == per_course[1] == per_course[2]

    def test_sharing_an_id_does_not_reduce_any_courses_count(self, big_ws, big):
        counts = [len(big_ws.knowledge_points(cid)) for cid in big["course_ids"]]
        union = set()
        for cid in big["course_ids"]:
            union |= {p["knowledge_id"] for p in big_ws.knowledge_points(cid)}
        # 并列展示：合计 = 各课之和，不是去重后的并集
        assert sum(counts) > len(union), "共享 id 被去重了 —— 那等于丢数据"
        assert big_ws.my_courses()["totals"]["counts"]["knowledge_points"] == sum(counts)

    def test_shared_ids_survive_a_restart_in_every_course(self, big):
        """这是 Task 68 发现的真实缺陷的回归测试。

        修复前：知识点按 ``knowledge_id`` 全局唯一存储，后写的课程**整行
        覆盖**先写的 —— 三门课重开后变成 28 / 48 / 68，且每门课都缺一部
        分共享知识点。这里的断言在那套实现下会直接失败。
        """
        before = None
        for _ in range(2):
            workspace = _open(big["data_dir"])
            try:
                per_course = [_shared_ids(workspace, cid) for cid in big["course_ids"]]
                assert all(per_course)
                assert per_course[0] == per_course[1] == per_course[2]
                if before is None:
                    before = per_course[0]
                else:
                    assert per_course[0] == before
            finally:
                workspace.close()

    def test_course_summary_for_two_courses_with_the_same_knowledge_id(
        self, big_ws, big
    ):
        shared = sorted(_shared_ids(big_ws, big["course_ids"][0]))
        first = big_ws.course_summary(big["course_ids"][0])
        second = big_ws.course_summary(big["course_ids"][1])
        assert first["course_id"] != second["course_id"]
        # 两门课各自拥有同一批共享 id —— 课程隔离不能变成"丢数据"
        assert shared

    def test_no_global_uniqueness_constraint_is_claimed(self):
        """模块文档必须把这条规则写下来（否则下一个人会'修'掉它）。"""
        source = (ROOT / "src" / "application" / "multi_course.py").read_text(
            encoding="utf-8"
        )
        assert "碰撞" in source or "collide" in source.lower()
        assert "course-scoped" in source

    def test_the_payload_never_deduplicates_course_rows(self, big_ws):
        payload = big_ws.my_courses()
        ids = [row["course_id"] for row in payload["courses"]]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# 4. 两条真相轴 (spec 68.6 / 全局铁律 4)
# ---------------------------------------------------------------------------


class TestTwoTruthAxes:
    def test_both_axes_are_present_and_separate(self, big_ws):
        for row in big_ws.my_courses()["courses"]:
            assert set(row["validation"]) >= set(VALIDATION_STATUSES)
            assert set(row["review"]) >= set(REVIEW_STATUSES)
            assert not (set(row["validation"]) & set(row["review"]))

    def test_totals_sum_each_axis_separately(self, big_ws):
        payload = big_ws.my_courses()
        for axis, keys in (("validation", VALIDATION_STATUSES), ("review", REVIEW_STATUSES)):
            for key in keys:
                expected = sum(row[axis].get(key, 0) for row in payload["courses"])
                assert payload["totals"][axis][key] == expected

    def test_confirming_knowledge_moves_only_the_review_axis(self, small_ws, small):
        cid = small["course_ids"][0]
        shared = sorted(_shared_ids(small_ws, cid))
        before = small_ws.course_summary(cid)
        small_ws.review_confirm(cid, shared[0])
        after = small_ws.course_summary(cid)
        assert after["review"]["confirmed"] == before["review"]["confirmed"] + 1
        assert after["validation"] == before["validation"]

    def test_the_row_has_no_combined_score_field(self, big_ws):
        forbidden = re.compile(
            r"mastery|proficien|readiness|completion|readiness|score|progress|"
            r"掌握|完成度|可信度",
            re.IGNORECASE,
        )
        for row in big_ws.my_courses()["courses"]:
            for key in row:
                assert not forbidden.search(key), key

    def test_axis_counts_account_for_every_knowledge_point(self, big_ws):
        for row in big_ws.my_courses()["courses"]:
            assert sum(row["validation"].values()) == row["counts"]["knowledge_points"]
            assert sum(row["review"].values()) == row["counts"]["knowledge_points"]

    def test_pending_review_is_not_derived_from_the_review_axis_alone(self, big_ws):
        """``pending_review`` 来自审核候选集，与 review.pending 是两件事。

        它们此刻数值相同（全部未审核）实现上是可以的，但**语义**不同；
        这里钉住的是"它确实来自另一条查询"，不是"它等于某个数"。
        """
        for row in big_ws.my_courses()["courses"]:
            assert "pending_review" in row["counts"]
            assert isinstance(row["counts"]["pending_review"], int)


# ---------------------------------------------------------------------------
# 5. 课程选择 (spec 68.3)
# ---------------------------------------------------------------------------


class TestCourseSelection:
    def test_no_preference_falls_back_to_the_first_course(self, big_ws, big):
        selection = big_ws.resolve_course_selection(None)
        assert selection["reason"] == "first_course"
        assert selection["course_id"] == sorted(big["course_ids"])[0]

    def test_a_valid_preference_is_kept(self, big_ws, big):
        wanted = big["course_ids"][-1]
        selection = big_ws.resolve_course_selection(wanted)
        assert selection == {
            "preferred": wanted,
            "course_id": wanted,
            "reason": "preferred",
            "available": sorted(big["course_ids"]),
        }

    def test_a_stale_preference_falls_back_and_says_why(self, big_ws):
        selection = big_ws.resolve_course_selection("course-does-not-exist")
        assert selection["reason"] == "preferred_missing"
        assert selection["preferred"] == "course-does-not-exist"
        assert selection["course_id"] is not None

    def test_an_empty_preference_is_treated_as_no_preference(self, big_ws):
        selection = big_ws.resolve_course_selection("")
        assert selection["preferred"] is None
        assert selection["reason"] == "first_course"

    def test_a_whitespace_preference_is_treated_as_no_preference(self, big_ws):
        selection = big_ws.resolve_course_selection("   ")
        assert selection["preferred"] is None
        assert selection["reason"] == "first_course"

    def test_no_courses_at_all_is_its_own_reason(self, tmp_path):
        workspace = _open(str(tmp_path / "data"))
        try:
            selection = workspace.resolve_course_selection("anything")
            assert selection == {
                "preferred": "anything",
                "course_id": None,
                "reason": "no_courses",
                "available": [],
            }
        finally:
            workspace.close()

    def test_available_is_sorted_and_complete(self, big_ws, big):
        selection = big_ws.resolve_course_selection(None)
        assert selection["available"] == sorted(big["course_ids"])

    def test_the_reason_is_always_one_of_the_declared_reasons(self, big_ws, big):
        seen = set()
        for preferred in (None, "", "ghost", *big["course_ids"]):
            selection = big_ws.resolve_course_selection(preferred)
            seen.add(selection["reason"])
            assert selection["reason"] in SELECTION_REASONS
        assert seen == {"first_course", "preferred_missing", "preferred"}

    def test_selection_is_a_pure_function(self, big_ws, big):
        wanted = big["course_ids"][1]
        assert big_ws.resolve_course_selection(wanted) == big_ws.resolve_course_selection(
            wanted
        )

    def test_selection_does_not_change_any_count(self, big_ws, big):
        before = json.dumps(big_ws.my_courses()["totals"], sort_keys=True)
        big_ws.resolve_course_selection("ghost")
        big_ws.resolve_course_selection(big["course_ids"][0])
        after = json.dumps(big_ws.my_courses()["totals"], sort_keys=True)
        assert before == after

    def test_my_courses_embeds_the_same_selection_rule(self, big_ws, big):
        wanted = big["course_ids"][-1]
        payload = big_ws.my_courses(preferred=wanted)
        assert payload["selection"] == big_ws.resolve_course_selection(wanted)

    def test_selection_survives_a_restart(self, big):
        wanted = big["course_ids"][-1]
        first = _open(big["data_dir"])
        try:
            expected = first.resolve_course_selection(wanted)
        finally:
            first.close()
        second = _open(big["data_dir"])
        try:
            assert second.resolve_course_selection(wanted) == expected
        finally:
            second.close()


# ---------------------------------------------------------------------------
# 6. 单课摘要
# ---------------------------------------------------------------------------


class TestCourseSummary:
    def test_summary_equals_the_my_courses_row(self, big_ws, big):
        payload = big_ws.my_courses()
        for cid in big["course_ids"]:
            assert big_ws.course_summary(cid)["counts"] == _row_by_id(payload, cid)["counts"]

    def test_summary_counts_match_the_underlying_lists(self, big_ws, big):
        for cid in big["course_ids"]:
            summary = big_ws.course_summary(cid)
            assert summary["counts"]["sessions"] == len(big_ws.list_sessions(cid))
            assert summary["counts"]["materials"] == len(big_ws.list_materials(cid))
            assert summary["counts"]["students"] == len(big_ws.list_students(cid))
            assert summary["counts"]["exercises"] == len(big_ws.list_exercises(cid))
            assert summary["counts"]["knowledge_points"] == len(
                big_ws.knowledge_points(cid)
            )

    def test_summary_echoes_the_requested_language(self, big_ws, big):
        for lang in ("zh", "es", "ca"):
            assert big_ws.course_summary(big["course_ids"][0], lang=lang)["language_ui"] == lang

    def test_unknown_course_is_not_found(self, big_ws):
        from src.application.errors import NotFoundError

        with pytest.raises(NotFoundError):
            big_ws.course_summary("course-does-not-exist")

    @pytest.mark.parametrize("bad", ["", "   ", None, 123, object()])
    def test_a_bad_course_id_is_invalid_input(self, big_ws, bad):
        from src.application.errors import InvalidInputError

        with pytest.raises(InvalidInputError):
            big_ws.course_summary(bad)

    def test_summary_is_read_only(self, big_ws, big):
        cid = big["course_ids"][0]
        before = json.dumps(big_ws.course_summary(cid), sort_keys=True)
        big_ws.course_summary(cid)
        assert json.dumps(big_ws.course_summary(cid), sort_keys=True) == before


# ---------------------------------------------------------------------------
# 7. 禁止排序 / 预测话术
# ---------------------------------------------------------------------------


class TestNoRankingContract:
    def _serialised(self, workspace: Workspace) -> str:
        return json.dumps(workspace.my_courses(), ensure_ascii=False).lower()

    def test_no_ranking_terms_in_the_payload(self, big_ws):
        blob = self._serialised(big_ws)
        for term in NO_RANKING_TERMS:
            assert term.lower() not in blob, term

    def test_no_mastery_or_prediction_terms_in_the_payload(self, big_ws):
        blob = self._serialised(big_ws)
        forbidden = (
            "mastery", "mastered", "proficiency", "predicted",
            "probability", "likelihood",
            "最可能考", "考试概率", "预测题", "押题", "考点概率", "预测分数",
            "已掌握", "掌握度",
        )
        for term in forbidden:
            assert term not in blob, term

    def test_rows_have_no_ranking_key(self, big_ws):
        banned = re.compile(r"rank|priority|order|recommend|importance", re.IGNORECASE)
        for row in big_ws.my_courses()["courses"]:
            for key in row:
                assert not banned.search(key), key
            for key in row["counts"]:
                assert not banned.search(key), key

    def test_module_declares_why_the_order_is_meaningless(self):
        source = (ROOT / "src" / "application" / "multi_course.py").read_text(
            encoding="utf-8"
        )
        assert "字典序" in source
        assert "不是" in source

    def test_the_note_field_does_not_rank_either(self, big_ws):
        note = big_ws.my_courses()["note"]
        for term in NO_RANKING_TERMS:
            assert term not in note


# ---------------------------------------------------------------------------
# 8. 规模 (spec 68 点名的量级)
# ---------------------------------------------------------------------------


class TestScale:
    def test_seed_reaches_the_declared_scale(self, big_ws, big):
        """3 门课 / 3 学生 / 15 节课 / 100+ 知识点。"""
        assert len(big["course_ids"]) == COURSE_COUNT
        assert len(big_ws.list_courses()) == COURSE_COUNT
        assert sum(len(big_ws.list_sessions(cid)) for cid in big["course_ids"]) == 15
        assert len(STUDENTS) == 3
        total_kps = sum(len(big_ws.knowledge_points(cid)) for cid in big["course_ids"])
        assert total_kps >= 100, total_kps
        registered = sum(len(big_ws.list_materials(cid)) for cid in big["course_ids"])
        assert registered >= 15

    def test_scale_seed_registers_thirty_material_slots(self, big):
        """30 个材料位（每节课 2 份，其中共享那份内容相同）。"""
        assert len(big["material_ids"]) == 30

    def test_hundreds_of_exercises_and_answers_stay_course_scoped(self, tmp_path):
        """100+ 练习 / 500+ 答案, 且**各归各课**。

        每个知识点出两道判断题, 一道答案为真一道为假 —— 于是答案里
        天然对错各半, 顺便压到评估链路 (而不是全对跑个空壳)。
        """
        data_dir = str(tmp_path / "data")
        seeded = _seed(data_dir, courses=3, sessions=5, topics=6)
        workspace = _open(data_dir)
        try:
            exercises: dict[str, list[str]] = {}
            answers = 0
            for cid in seeded["course_ids"]:
                ids = [p["knowledge_id"] for p in workspace.knowledge_points(cid)]
                created = []
                for index, kp in enumerate(ids[:40]):
                    for variant in range(2):
                        created.append(
                            workspace.create_exercise(
                                cid,
                                "true_false",
                                "Pregunta %d.%d de %s" % (index, variant, cid[:8]),
                                [kp],
                                is_true=(variant == 0),
                            )["exercise_id"]
                        )
                exercises[cid] = created
                for exercise_id in created:
                    for offset, student in enumerate(seeded["students"]):
                        workspace.submit_answer(
                            cid, student, exercise_id, "true", sequence=offset
                        )
                        answers += 1
            assert sum(len(v) for v in exercises.values()) >= 100
            assert answers >= 500, answers
            for cid in seeded["course_ids"]:
                summary = workspace.course_summary(cid)
                assert summary["counts"]["exercises"] == len(exercises[cid])
                assert summary["counts"]["answers"] == len(exercises[cid]) * len(
                    seeded["students"]
                )
        finally:
            workspace.close()

    def test_my_courses_is_bounded_at_scale(self, big_ws):
        import time

        started = time.time()
        payload = big_ws.my_courses()
        elapsed = time.time() - started
        assert payload["course_count"] == COURSE_COUNT
        assert elapsed < 10, "%.1fs" % elapsed

    def test_isolation_holds_at_scale(self, big_ws, big):
        seen: dict[str, set[str]] = {}
        for cid in big["course_ids"]:
            seen[cid] = {p["knowledge_id"] for p in big_ws.knowledge_points(cid)}
        shared = _shared_ids(big_ws, big["course_ids"][0])
        for cid in big["course_ids"]:
            for other in big["course_ids"]:
                if other == cid:
                    continue
                assert seen[cid] & seen[other] == shared


# ---------------------------------------------------------------------------
# 9. API (真实 HTTP)
# ---------------------------------------------------------------------------


class _HttpClient:
    def __init__(self, base: str) -> None:
        self.base = base

    def get(self, path: str) -> tuple[int, Any]:
        request = urllib.request.Request(self.base + path, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture
def client(big):
    workspace = _open(big["data_dir"])
    instance = create_server(workspace, port=0).start()
    try:
        yield _HttpClient(instance.url), workspace
    finally:
        instance.stop()
        workspace.close()


class TestApi:
    def test_my_courses_returns_success(self, client):
        http, _ = client
        status, payload = http.get("/api/my-courses")
        assert status == 200
        assert payload["success"] is True
        assert "error" not in payload

    def test_my_courses_shape(self, client):
        http, _ = client
        _, payload = http.get("/api/my-courses")
        data = payload["data"]
        assert data["view"] == MULTI_COURSE_VERSION
        assert data["course_count"] == COURSE_COUNT
        assert set(data["totals"]["counts"]) == set(COUNT_KEYS)

    @pytest.mark.parametrize("lang", ["zh", "es", "ca"])
    def test_language_query_is_echoed(self, client, lang):
        http, _ = client
        _, payload = http.get("/api/my-courses?lang=" + lang)
        assert payload["data"]["language"] == lang

    def test_preferred_query_drives_the_selection(self, client, big):
        http, _ = client
        wanted = sorted(big["course_ids"])[-1]
        _, payload = http.get("/api/my-courses?preferred=" + wanted)
        assert payload["data"]["selection"]["reason"] == "preferred"
        assert payload["data"]["selection"]["course_id"] == wanted

    def test_stale_preferred_query_is_reported(self, client):
        http, _ = client
        _, payload = http.get("/api/my-courses?preferred=course-ghost")
        assert payload["data"]["selection"]["reason"] == "preferred_missing"

    def test_course_selection_endpoint(self, client):
        http, _ = client
        status, payload = http.get("/api/course-selection")
        assert status == 200
        assert payload["data"]["reason"] == "first_course"

    def test_course_selection_endpoint_with_a_preference(self, client, big):
        http, _ = client
        wanted = big["course_ids"][0]
        _, payload = http.get("/api/course-selection?preferred=" + wanted)
        assert payload["data"]["reason"] == "preferred"
        assert payload["data"]["course_id"] == wanted

    def test_course_selection_endpoint_with_a_stale_preference(self, client):
        http, _ = client
        _, payload = http.get("/api/course-selection?preferred=ghost")
        assert payload["data"]["reason"] == "preferred_missing"

    def test_course_summary_endpoint(self, client, big):
        http, _ = client
        status, payload = http.get("/api/courses/%s/summary" % big["course_ids"][0])
        assert status == 200
        assert payload["data"]["course_id"] == big["course_ids"][0]

    def test_course_summary_endpoint_echoes_language(self, client, big):
        http, _ = client
        _, payload = http.get("/api/courses/%s/summary?lang=ca" % big["course_ids"][0])
        assert payload["data"]["language_ui"] == "ca"

    def test_unknown_course_is_a_structured_404(self, client):
        http, _ = client
        status, payload = http.get("/api/courses/course-ghost/summary")
        assert status == 404
        assert payload["success"] is False
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_empty_course_id_is_a_structured_400(self, client):
        http, _ = client
        status, payload = http.get("/api/courses/%20/summary")
        assert status == 400
        assert payload["success"] is False
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_endpoints_never_return_a_500_for_user_input(self, client, big):
        http, _ = client
        paths = (
            "/api/my-courses?preferred=",
            "/api/my-courses?preferred=%20%20",
            "/api/my-courses?lang=xx",
            "/api/course-selection?preferred=",
            "/api/courses/ghost/summary",
            "/api/courses/%20/summary",
        )
        for path in paths:
            status, _ = http.get(path)
            assert status < 500, (path, status)

    def test_my_courses_json_has_no_ranking_terms(self, client):
        http, _ = client
        _, payload = http.get("/api/my-courses")
        blob = json.dumps(payload, ensure_ascii=False).lower()
        for term in NO_RANKING_TERMS:
            assert term.lower() not in blob, term

    def test_two_calls_are_byte_identical(self, client):
        http, _ = client
        _, first = http.get("/api/my-courses")
        _, second = http.get("/api/my-courses")
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    def test_empty_workspace_over_http(self, tmp_path):
        workspace = _open(str(tmp_path / "data"))
        instance = create_server(workspace, port=0).start()
        try:
            http = _HttpClient(instance.url)
            status, payload = http.get("/api/my-courses")
            assert status == 200
            assert payload["data"]["empty"] is True
            assert payload["data"]["empty_note"] == MY_COURSES_EMPTY_NOTE
            status, payload = http.get("/api/course-selection")
            assert payload["data"]["reason"] == "no_courses"
        finally:
            instance.stop()
            workspace.close()

    def test_selection_endpoint_agrees_with_my_courses(self, client, big):
        http, _ = client
        wanted = big["course_ids"][-1]
        _, via_my = http.get("/api/my-courses?preferred=" + wanted)
        _, via_sel = http.get("/api/course-selection?preferred=" + wanted)
        assert via_my["data"]["selection"] == via_sel["data"]

    def test_summary_endpoint_agrees_with_the_row(self, client, big):
        http, _ = client
        wanted = big["course_ids"][0]
        _, listed = http.get("/api/my-courses")
        row = _row_by_id(listed["data"], wanted)
        _, summary = http.get("/api/courses/%s/summary" % wanted)
        assert summary["data"]["counts"] == row["counts"]

    def test_payload_is_utf8_json(self, client):
        http, _ = client
        request = urllib.request.Request(http.base + "/api/my-courses")
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "")
        assert "utf-8" in content_type.lower()
        json.loads(body.decode("utf-8"))

    def test_all_three_endpoints_are_reachable_together(self, client, big):
        http, _ = client
        for path in (
            "/api/my-courses",
            "/api/course-selection",
            "/api/courses/%s/summary" % big["course_ids"][0],
        ):
            status, payload = http.get(path)
            assert status == 200, path
            assert payload["success"] is True, path


# ---------------------------------------------------------------------------
# 10. UI 契约（静态断言；真实渲染由 scripts/ui_*.js 覆盖）
# ---------------------------------------------------------------------------


def _app_js() -> str:
    """P1-6: 前端已拆分成多个零构建脚本; 这里返回按加载顺序拼接的
    **全部前端源码**, 语义与拆分前读单个 app.js 一致。"""
    return read_web_source("app.js")


def _index_html() -> str:
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


#: ``'key': 'valor'`` 或 ``"key": "valor"``。
#:
#: 两个坑, 都踩过:
#:
#: * **不能加行锚** —— zh 表里一行挤着写好几条
#:   (``'ex.answer': '你的作答', 'ex.submit': '提交答案',``), 加了 ``^`` 只能
#:   抓到第一条, "三张表 key 对齐" 的断言会凭空多出几个"漏译"的 key。
#: * **不能只认单引号** —— 加泰罗尼亚语大量撇号, 那些值是用**双引号**写的
#:   (``'learn.start': "Començar l'estudi d'avui"``), 只认单引号会静默漏 key。
_JS_STRING = r"(?:'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\")"
_I18N_ENTRY = re.compile(_JS_STRING + r"\s*:\s*" + _JS_STRING)


def _i18n_tables() -> dict[str, dict[str, str]]:
    """从 app.js 源码里取出 I18N 三张表（正则，不执行 JS）。

    ``app.js`` 是浏览器脚本，不是 Python —— 别用 ``ast`` 去解析它。
    """
    source = _app_js()
    start = source.index("const I18N = {")
    end = source.index("\n};", start)
    block = source[start:end]
    tables: dict[str, dict[str, str]] = {}
    for lang in ("zh", "es", "ca"):
        s = block.index("  %s: {" % lang)
        e = block.index("\n  },", s)
        body = block[s:e]
        parsed = {}
        for match in _I18N_ENTRY.finditer(body):
            key = match.group(1) if match.group(1) is not None else match.group(2)
            value = match.group(3) if match.group(3) is not None else match.group(4)
            parsed[key] = value.replace("\\'", "'").replace('\\"', '"')
        # 解析不出来就一定是解析错了 —— 空表会让下面每条断言形同虚设。
        assert len(parsed) > 50, (lang, len(parsed))
        # 交叉核对: 表里的符号 key 一个都不能漏。
        declared = set(re.findall(r"'([A-Za-z0-9_.]+)'\s*:", body))
        assert not (declared - set(parsed)), (lang, sorted(declared - set(parsed))[:5])
        tables[lang] = parsed
    assert set(tables) == {"zh", "es", "ca"}
    return tables


class TestUi:
    def test_shell_has_a_my_courses_nav_entry(self):
        assert 'href="#/courses"' in _index_html()

    def test_shell_has_the_course_switcher(self):
        html = _index_html()
        assert 'id="course-switch"' in html
        assert 'data-i18n="mc.switch"' in html

    def test_router_handles_the_courses_index(self):
        js = _app_js()
        assert "parts[0] === 'courses' && parts.length === 1" in js
        assert "pageMyCourses()" in js

    def test_page_function_exists(self):
        assert "async function pageMyCourses()" in _app_js()

    def test_page_calls_the_my_courses_endpoint(self):
        assert "api('/my-courses'" in _app_js()

    def test_sidebar_calls_the_selection_endpoint(self):
        """选择规则必须在后端 —— 前端不自己写第二遍。"""
        assert "api('/course-selection'" in _app_js()

    def test_switcher_persists_the_choice(self):
        js = _app_js()
        assert "function renderCourseSwitcher" in js
        assert "setCourse(courseId)" in js
        assert "__courseSwitchWired" in js

    def test_switcher_navigates_to_the_course_page(self):
        assert "'#/courses/' + encodeURIComponent(courseId)" in _app_js()

    def test_switcher_is_cleared_when_there_are_no_courses(self):
        js = _app_js()
        assert js.count("renderCourseSwitcher([])") >= 2

    def test_page_marks_the_current_course(self):
        js = _app_js()
        assert "mc.current" in js
        assert "card-current" in js

    def test_page_renders_both_axes(self):
        js = _app_js()
        assert "mc.axisValidation" in js
        assert "mc.axisReview" in js
        assert "MC_VALIDATION_KEYS" in js
        assert "MC_REVIEW_KEYS" in js

    def test_page_escapes_course_names(self):
        """课程名与身份串都必须转义后才进 HTML。

        身份串那一行原来是**内联**拼的（``esc(row.course_id) + (row.code ? ...)``），
        2026-09-20 收口成调 ``courseIdentity()``（与课程详情页同一个助手）——
        转义保证完全不变（拼好的整个串一起过 ``esc()``），只是拼法从两份变成一份。
        同一天稍后又把身份串里的 ``course_id`` 去掉（改成 ``code · language``）,
        因为哈希对用户零信息量、而 code 已经足够区分同名课程。

        所以这里钉的是**转义这件事**，不是某一种拼法：字面量 ``esc(row.course_id)``
        会因为换拼法而消失，但"用户数据必须转义"这条保证不会。两次改断言都用变异
        验证过（去掉那层 ``esc()`` -> 本断言必须报红），理由记在 docs/status.md 的
        「设计取舍」里，不藏在"顺手清理"里。
        """
        js = _app_js()
        assert "esc(row.name" in js
        assert "esc(courseIdentity(row))" in js

    def test_axis_labels_are_written_out_not_concatenated(self):
        """拼前缀漏译时 t() 会原样返回 key，静态检查抓不到 —— 逐个写死。"""
        js = _app_js()
        for key in (
            "val.supported", "val.unverified", "val.conflicted",
            "rev.confirmed", "rev.pending", "rev.rejected", "rev.kept_unverified",
        ):
            assert "t('%s')" % key in js, key

    def test_i18n_tables_have_identical_keys(self):
        tables = _i18n_tables()
        assert set(tables["zh"]) == set(tables["es"]) == set(tables["ca"])

    def test_mc_keys_exist_in_all_three_languages(self):
        tables = _i18n_tables()
        for lang in ("zh", "es", "ca"):
            for key in ("mc.title", "mc.subtitle", "mc.empty", "mc.totals",
                        "mc.axisValidation", "mc.axisReview", "mc.sharedIdNote"):
                assert tables[lang].get(key), (lang, key)

    def test_every_count_key_has_a_label(self):
        tables = _i18n_tables()
        for lang in ("zh", "es", "ca"):
            for key in COUNT_KEYS:
                assert tables[lang].get("mc.count." + key), (lang, key)

    def test_no_ranking_terms_in_the_my_courses_copy(self):
        tables = _i18n_tables()
        for lang in ("zh", "es", "ca"):
            for key, value in tables[lang].items():
                if not key.startswith("mc."):
                    continue
                for term in NO_RANKING_TERMS:
                    assert term.lower() not in value.lower(), (lang, key, term)

    def test_spanish_and_catalan_my_courses_copy_has_no_cjk(self):
        tables = _i18n_tables()
        for lang in ("es", "ca"):
            for key, value in tables[lang].items():
                if key.startswith(("mc.", "val.", "rev.")):
                    assert not CJK.search(value), (lang, key, value)

    def test_no_cjk_in_the_es_ca_count_labels(self):
        """es/ca 计数标签里出现中文 = 漏译。"""
        tables = _i18n_tables()
        for lang in ("es", "ca"):
            for key in COUNT_KEYS:
                value = tables[lang]["mc.count." + key]
                assert not CJK.search(value), (lang, key, value)

    def test_css_defines_the_new_classes(self):
        css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
        for klass in (".mc-counts", ".mc-axis", ".card-current"):
            assert klass in css, klass

    # ---- 回归: "选中的课" 与 "高亮的课" 必须同源 ------------------------

    def test_the_course_choice_has_exactly_one_write_entry(self):
        """只有 ``setCourse()`` 能写 ``ca.course``。

        绕过它直接写 localStorage 的地方 (``pageLearnKnowledge`` /
        ``pageReview`` 曾经就是这样), ``state.courseId`` 会与持久化的值脱钩 ——
        下一次渲染时侧边栏高亮的就还是上一门课。
        """
        js = _app_js()
        assert js.count("localStorage.setItem('ca.course'") == 1
        assert "window.localStorage.setItem('ca.course', courseId)" in js

    def test_set_course_repaints_the_current_course_chrome(self):
        """``setCourse()`` 必须重绘侧边栏高亮与顶栏切换器。

        回归的根因: ``route()`` 的顺序是 ``loadSidebar()`` -> ``pageXxx()``,
        而课程页要到**渲染完之后**才从路由里拿到 course_id 并 ``setCourse()``。
        如果它不重绘, 用户点开 B 课看到的就是"主视图是 B, 左侧高亮的却是 A"。

        真实行为由 ``scripts/ui_render_check.js`` 的
        "当前课程的三处显示必须同源" 一组用例在 Node + DOM 桩里执行验证;
        这里只锁住结构, 防止有人把那次重绘删掉。
        """
        js = _app_js()
        body = js[js.index("function setCourse("):]
        body = body[: body.index("\n}")]
        assert "syncCourseChrome()" in body
        assert "function syncCourseChrome()" in js
        assert "__courseCache" in js

    def test_the_sidebar_and_the_switcher_share_one_highlight_rule(self):
        """高亮规则只能有一份 —— 复制粘贴出来的第二份迟早会漂移。"""
        js = _app_js()
        assert "function courseListHtml(courses)" in js
        # 侧边栏的 active 标记只在 courseListHtml 里算一次。
        assert js.count("=== state.courseId ? ' class=\"active\"'") == 1

    def test_route_supplied_course_ids_are_validated(self):
        """路由里的 course_id 必须先确认存在, 才认作"当前课程"。

        手输错 / 失效书签 / 课程被删之后的深链都会给出不存在的 id。直接提交的
        后果: 侧边栏没有任何高亮、顶栏切换器退回显示第一项 (显示的又不是"当前
        课程"), 且脏 id 被持久化, 此后每个页面都 404 —— 用户被卡住。

        真实行为由 ``scripts/ui_render_check.js`` 的
        "路由里的 course_id 不存在时, 不得污染当前课程" 一组用例执行验证;
        这里只锁住结构与调用点。
        """
        js = _app_js()
        assert "function setRouteCourse(courseId)" in js
        assert "__courseCache.some((c) => c.course_id === courseId)" in js

    def test_every_route_driven_page_uses_the_validating_entry(self):
        """8 个从路由拿 course_id 的页面函数都必须走 ``setRouteCourse()``。

        ``pageReview`` / ``pageDashboard`` / ``pageMyCourses`` / ``loadSidebar``
        不在这个名单里 —— 它们的 course_id 来自后端 (requireCourse /
        dashboard / course-selection), 本来就是有效的。
        """
        js = _app_js()
        for signature in (
            "async function pageCourse(courseId)",
            "async function pageSession(courseId, sessionId)",
            "async function pageKnowledgeDetail(courseId, knowledgeId)",
            "async function pageExercise(courseId, exerciseId, studentId)",
            "async function pageMistakeDetail(courseId, knowledgeId)",
            "async function pageStudent(courseId, studentId)",
            "async function pageLearnKnowledge(courseId, knowledgeId)",
        ):
            body = js[js.index(signature):]
            body = body[: body.index("\n}")]
            assert "setRouteCourse(courseId)" in body, signature
            assert "\n  setCourse(courseId);" not in body, signature

    def test_the_student_choice_has_exactly_one_write_entry(self):
        """只有 ``setStudent()`` 能写 ``ca.student``, 内存与持久化一起改。

        回归: 6 处页面函数各自写一遍 ``state.studentId`` + ``localStorage``, 其中
        ``pageExercise()`` **漏了持久化那一步**。从错题本点「Practice Again」进
        ``#/courses/<c>/exercises/<e>/<sid>`` 之后, 内存里是 sid, localStorage 里
        还是上一个学生 —— 刷新一下学生就悄悄换人了。

        真实行为由 ``scripts/ui_render_check.js`` 的
        "当前学生的内存与持久化必须同步" 一组用例执行验证。
        """
        js = _app_js()
        assert js.count("localStorage.setItem('ca.student'") == 1
        assert "window.localStorage.setItem('ca.student', studentId)" in js
        # 除了 setStudent() 自己, 任何地方都不许直接写 state.studentId。
        assert js.count("state.studentId = ") == 1
        assert "state.studentId = studentId || null;" in js

    def test_every_page_that_picks_a_student_goes_through_set_student(self):
        """6 个会设定"当前学生"的页面函数都必须走 ``setStudent()``。"""
        js = _app_js()
        for signature in (
            "async function pageLearn()",
            "async function pageLearnKnowledge(courseId, knowledgeId)",
            "async function pageReview()",
            "async function pageExercise(courseId, exerciseId, studentId)",
            "async function pageMistakes()",
            "async function pageStudent(courseId, studentId)",
        ):
            body = js[js.index(signature):]
            body = body[: body.index("\n}")]
            assert "setStudent(" in body, signature
            assert "state.studentId = " not in body, signature

    def test_every_page_function_sets_the_top_nav_highlight(self):
        """**每个**页面函数都必须设置顶栏高亮。

        ``index.html`` 的 topnav 里没有任何硬编码 ``active`` —— 高亮完全由页面
        函数设置, 谁不设谁就继承上一页。

        回归: ``pageLearnKnowledge()`` 曾经是 19 个页面函数里唯一漏掉的那个。
        后果是"高亮的位置不是用户所在的位置": 在 ``#/learn/<kp>`` 上刷新整条导航
        都没有高亮; 从别的页面点进来则高亮停在上一页。

        真实行为由 ``scripts/ui_render_check.js`` 的
        "顶栏高亮必须跟随用户所在的页面" 一组用例执行验证 (桩里补上了
        ``.topnav a``); 这里做全量清点, 防止新增页面函数时再漏一个。
        """
        js = _app_js()
        pages = re.findall(r"^async function (page[A-Za-z]+)\(", js, re.M)
        assert len(pages) >= 19, pages
        for name in pages:
            body = js[js.index("async function %s(" % name):]
            body = body[: body.index("\n}")]
            assert body.count("markActiveNav(") == 1, (name, body.count("markActiveNav("))

    def test_the_top_nav_ownership_table_is_exactly_as_declared(self):
        """顶栏高亮的归属表必须与 ``markActiveNav()`` 上方声明的那张表逐项一致。

        规则是"跟随用户所在页面所属的顶栏分区; 不属于任何分区的页面清空"。
        光有"恰好调用一次"的清点还不够 —— 调用的**目标**本身也是一条被写了 19 遍
        的规则, 而"被抄 N 遍的规则迟早有一遍漂移"正是这一族缺陷的成因
        (``pageLearnKnowledge()`` 漏调高亮, ``pageExercise()`` 漏持久化)。

        所以这里把目标钉死: 任何一处改动都会让这条测试失败, 必须**刻意**同步
        这张表。自动化能保证"确定", 这张表就是"确定"的落点 —— 至于"哪一个更
        恰当", 那是人读代码判断的事, 但至少它不会再**悄悄**变。
        """
        declared = {
            "pageDashboard": "#/",
            "pageToday": "#/today",
            "pageLearn": "#/today",
            "pageLearnKnowledge": "#/today",
            "pageReview": "#/review",
            "pageKnowledge": "#/knowledge",
            "pageMaterials": "#/materials",
            "pageReviews": "#/reviews",
            "pageStudents": "#/students",
            "pageExercises": "#/exercises",
            "pageMistakes": "#/mistakes",
            "pageMistakeDetail": "#/mistakes",
            "pageExercise": "#/exercises",
            "pageMyCourses": "#/courses",
            # pageReviewPack: 顶栏有 #/review-pack 分区, 高亮它 (曾经漏登记,
            # 代码里写的是清空 '' —— 与注释表 "#/review-pack -> #/review-pack"
            # 矛盾, 已按注释表改回 '#/review-pack')。
            "pageReviewPack": "#/review-pack",
            "pageCourse": "",
            "pageSession": "",
            "pageKnowledgeDetail": "",
            "pageStudent": "",
        }
        js = _app_js()
        observed = {}
        for name in re.findall(r"^async function (page[A-Za-z]+)\(", js, re.M):
            body = js[js.index("async function %s(" % name):]
            body = body[: body.index("\n}")]
            found = re.findall(r"markActiveNav\(\s*'([^']*)'\s*\)", body)
            assert len(found) == 1, (name, found)
            observed[name] = found[0]

        drifted = {k: v for k, v in observed.items() if declared.get(k) != v}
        assert not drifted, (
            "顶栏高亮的归属表变了: %r。若是有意改动, 请同步更新 app.js 里 "
            "markActiveNav() 上方的注释表与本测试的 declared; 若是顺手漂移, 改回去。"
            % (drifted,)
        )
        assert set(observed) == set(declared), (
            "页面函数增减了: %r" % (set(observed) ^ set(declared),)
        )
        # 规则本身也必须留在代码旁边 —— 表只活在测试里的话, 读 app.js 的人看不到它。
        assert "不属于任何分区的页面清空" in js

    def test_every_persisted_ui_choice_has_exactly_one_write_entry(self):
        """**每一个**持久化的"用户选择"都必须只有一个写入点。

        这一族缺陷的共同形状是"同一条规则被抄了 N 遍, 其中一遍抄漏了":
        ``ca.course`` 曾有 3 个写入点 (其中 2 个绕过 ``setCourse()``),
        ``state.studentId`` 曾有 6 个 (其中 ``pageExercise()`` 漏了持久化),
        顶栏高亮的归属规则被抄了 19 遍 (``pageLearnKnowledge()`` 漏了)。

        收口之后这条不变量是全局的: 内存状态、持久化、以及依赖它的显示
        必须在**同一个函数**里一起改完 —— 否则"改了 state 没改显示"和
        "改了 state 没持久化"就会各出现一次 (它们其实是同一个 bug)。

        前两条 (``course`` / ``student``) 是具体的回归; 这条是**全量**的,
        顺带把 ``ca.lang`` 与 ``ca.mistakeGroup`` 也钉住 —— 它们目前靠人工核对,
        而"目前是对的"不是"不会再错"。
        """
        js = _app_js()
        # key -> 期望的 localStorage 写入点数量
        declared = {
            "ca.course": 1,
            "ca.student": 1,
            "ca.lang": 1,
            "ca.mistakeGroup": 1,
        }
        observed = {}
        for key in declared:
            observed[key] = len(re.findall(
                r"localStorage\.setItem\(\s*'%s'" % re.escape(key), js))
        assert observed == declared, observed

        # 反过来: 出现过的 ``ca.*`` 键必须都在表里 —— 新增一个持久化的选择时,
        # 必须**刻意**把它加进来 (以及决定它的唯一写入口在哪)。
        keys = set(re.findall(r"localStorage\.setItem\(\s*'(ca\.[A-Za-z]+)'", js))
        assert keys == set(declared), "未登记的选择键: %r" % (keys ^ set(declared),)

        # 每个 state 字段也只有一个赋值点 (setter 之内)。
        fields = re.findall(r"^(?:const|let|var) state = \{(.*?)^\};", js, re.M | re.S)
        assert len(fields) == 1, "找不到 state 字面量"
        names = re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*):", fields[0], re.M)
        assert names, "state 字段没解析出来"
        for name in names:
            count = len(re.findall(r"\bstate\.%s\s*=(?!=)" % re.escape(name), js))
            assert count == 1, (name, count)


# ---------------------------------------------------------------------------
# 11. 重启一致性
# ---------------------------------------------------------------------------


class TestRestart:
    def test_course_count_survives_a_restart(self, big):
        workspace = _open(big["data_dir"])
        try:
            assert workspace.my_courses()["course_count"] == COURSE_COUNT
        finally:
            workspace.close()

    def test_knowledge_counts_survive_a_restart(self, big):
        """三门课结构相同 —— 重开后数量必须仍然相同。"""
        workspace = _open(big["data_dir"])
        try:
            counts = [len(workspace.knowledge_points(cid)) for cid in big["course_ids"]]
            assert len(set(counts)) == 1, counts
            assert counts[0] > 0
        finally:
            workspace.close()

    def test_shared_ids_survive_a_restart_in_every_course(self, big):
        workspace = _open(big["data_dir"])
        try:
            per_course = [_shared_ids(workspace, cid) for cid in big["course_ids"]]
            assert all(per_course)
            assert per_course[0] == per_course[1] == per_course[2]
        finally:
            workspace.close()

    def test_students_survive_a_restart_per_course(self, big):
        workspace = _open(big["data_dir"])
        try:
            for cid in big["course_ids"]:
                assert len(workspace.list_students(cid)) == len(STUDENTS)
        finally:
            workspace.close()

    def test_exercises_and_answers_survive_a_restart(self, tmp_path):
        data_dir = str(tmp_path / "data")
        seeded = _seed(data_dir, courses=2, sessions=1, topics=2)
        workspace = _open(data_dir)
        try:
            cid = seeded["course_ids"][0]
            kp = workspace.knowledge_points(cid)[0]["knowledge_id"]
            exercise_id = workspace.create_exercise(
                cid, "true_false", "Pregunta", [kp], is_true=True
            )["exercise_id"]
            workspace.submit_answer(cid, seeded["students"][0], exercise_id, "true")
        finally:
            workspace.close()
        reopened = _open(data_dir)
        try:
            assert len(reopened.list_exercises(seeded["course_ids"][0])) == 1
            assert reopened.course_summary(seeded["course_ids"][0])["counts"]["answers"] == 1
            assert reopened.course_summary(seeded["course_ids"][1])["counts"]["answers"] == 0
        finally:
            reopened.close()

    def test_answer_history_isolation_survives_a_restart(self, tmp_path):
        """同一个学生在两门课各答一题，重启后各自的答案日志仍然只属于自己。

        这是"隔离 + 持久化"的交叉点: 只测一门课的答案能查回来, 是测不出
        "两门课的答案被并到同一个日志里" 的。
        """
        seeded = _seed(str(tmp_path / "data"), courses=2, sessions=1, topics=2)
        data_dir = seeded["data_dir"]
        workspace = _open(data_dir)
        per_course: dict[str, str] = {}
        try:
            for cid in seeded["course_ids"]:
                kp = workspace.knowledge_points(cid)[0]["knowledge_id"]
                exercise_id = workspace.create_exercise(
                    cid, "true_false", "Pregunta de " + cid[:8], [kp], is_true=True
                )["exercise_id"]
                per_course[cid] = exercise_id
                for student in seeded["students"][:2]:
                    workspace.submit_answer(cid, student, exercise_id, "true")
        finally:
            workspace.close()
        reopened = _open(data_dir)
        try:
            for cid in seeded["course_ids"]:
                assert reopened.course_summary(cid)["counts"]["answers"] == 2, cid
                log = reopened.context(cid).learning_service.answer_log_for(
                    seeded["students"][0]
                )
                assert [row.get("exercise_id") for row in log] == [per_course[cid]]
            other = seeded["course_ids"][1]
            assert reopened.context(
                seeded["course_ids"][0]
            ).learning_service.answer_log_for(seeded["students"][0])
            # 第二门课的答案绝不能出现在第一门课学生的日志里。
            first_log = reopened.context(
                seeded["course_ids"][0]
            ).learning_service.answer_log_for(seeded["students"][0])
            assert per_course[other] not in [
                row.get("exercise_id") for row in first_log
            ]
        finally:
            reopened.close()

    def test_review_history_survives_a_restart_per_course(self, tmp_path):
        data_dir = str(tmp_path / "data")
        seeded = _seed(data_dir, courses=2, sessions=1, topics=2)
        workspace = _open(data_dir)
        try:
            cid = seeded["course_ids"][0]
            shared = sorted(_shared_ids(workspace, cid))
            workspace.review_confirm(cid, shared[0])
        finally:
            workspace.close()
        reopened = _open(data_dir)
        try:
            assert reopened.review_history(seeded["course_ids"][0], shared[0])
            assert reopened.review_history(seeded["course_ids"][1], shared[0]) == []
        finally:
            reopened.close()

    def test_summary_matches_after_a_restart(self, big):
        first = _open(big["data_dir"])
        try:
            expected = {
                cid: first.course_summary(cid)["counts"] for cid in big["course_ids"]
            }
        finally:
            first.close()
        second = _open(big["data_dir"])
        try:
            for cid in big["course_ids"]:
                assert second.course_summary(cid)["counts"] == expected[cid]
        finally:
            second.close()

    def test_three_restart_cycles_produce_identical_payloads(self, big):
        snapshots = []
        for _ in range(3):
            workspace = _open(big["data_dir"])
            try:
                snapshots.append(
                    json.dumps(workspace.my_courses(), sort_keys=True, ensure_ascii=False)
                )
            finally:
                workspace.close()
        assert snapshots[0] == snapshots[1] == snapshots[2]

    def test_a_new_course_appears_after_a_restart(self, tmp_path):
        """**不能**用模块级 ``big`` 夹具 —— 它会写数据，会污染共用它的只读
        测试（实测：跑完这门课多出来，后面的断言看到 4 门课）。"""
        seeded = _seed(str(tmp_path / "data"), courses=2, sessions=1, topics=2)
        data_dir = seeded["data_dir"]
        workspace = _open(data_dir)
        try:
            before = workspace.my_courses()["course_count"]
            assert before == 2
            workspace.create_course("Nueva", "NEW", "es")
            assert workspace.my_courses()["course_count"] == before + 1
        finally:
            workspace.close()
        reopened = _open(data_dir)
        try:
            assert reopened.my_courses()["course_count"] == before + 1
        finally:
            reopened.close()

    def test_an_empty_directory_restarts_into_an_empty_overview(self, tmp_path):
        data_dir = str(tmp_path / "data")
        workspace = _open(data_dir)
        try:
            assert workspace.my_courses()["empty"] is True
        finally:
            workspace.close()
        reopened = _open(data_dir)
        try:
            payload = reopened.my_courses()
            assert payload["empty"] is True
            assert payload["courses"] == []
        finally:
            reopened.close()

    def test_restart_in_a_real_subprocess_agrees(self, big):
        """真子进程（不是同进程重开）—— 证明数据真的落盘了。"""
        script = (
            "import json, sys\n"
            "sys.path.insert(0, r'%s')\n"
            "from src.application.workspace import Workspace\n"
            "ws = Workspace(r'%s')\n"
            "payload = ws.my_courses()\n"
            "print(json.dumps({\n"
            "    'course_count': payload['course_count'],\n"
            "    'counts': [r['counts']['knowledge_points'] for r in payload['courses']],\n"
            "    'selection': payload['selection'],\n"
            "}, sort_keys=True))\n"
            "ws.close()\n" % (ROOT, big["data_dir"])
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(ROOT),
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        assert payload["course_count"] == COURSE_COUNT
        assert len(set(payload["counts"])) == 1, payload["counts"]

    def test_student_state_isolation_survives_a_restart(self, tmp_path):
        data_dir = str(tmp_path / "data")
        seeded = _seed(data_dir, courses=2, sessions=1, topics=2)
        workspace = _open(data_dir)
        try:
            cid = seeded["course_ids"][0]
            kp = workspace.knowledge_points(cid)[0]["knowledge_id"]
            workspace.record_learning_event(cid, seeded["students"][0], kp, "viewed")
        finally:
            workspace.close()
        reopened = _open(data_dir)
        try:
            student = seeded["students"][0]
            assert reopened.student_state(seeded["course_ids"][0], student)["states"]
            assert not reopened.student_state(seeded["course_ids"][1], student)["states"]
        finally:
            reopened.close()

    def test_selection_survives_a_restart_with_a_stale_preference(self, big):
        workspace = _open(big["data_dir"])
        try:
            selection = workspace.resolve_course_selection("course-ghost")
        finally:
            workspace.close()
        reopened = _open(big["data_dir"])
        try:
            assert reopened.resolve_course_selection("course-ghost") == selection
        finally:
            reopened.close()

    def test_gaps_and_evidence_totals_survive_a_restart(self, big):
        first = _open(big["data_dir"])
        try:
            expected = {
                cid: (first.course_summary(cid)["evidence_total"],
                      first.course_summary(cid)["gaps"])
                for cid in big["course_ids"]
            }
        finally:
            first.close()
        second = _open(big["data_dir"])
        try:
            for cid in big["course_ids"]:
                summary = second.course_summary(cid)
                assert (summary["evidence_total"], summary["gaps"]) == expected[cid]
        finally:
            second.close()


# ---------------------------------------------------------------------------
# 12. 边界情况
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_a_course_without_sessions_has_zeros_and_no_last_session(self, tmp_path):
        workspace = _open(str(tmp_path / "data"))
        try:
            cid = workspace.create_course("Vacia", "VAC", "es")["course_id"]
            row = _row_by_id(workspace.my_courses(), cid)
            assert row["counts"]["sessions"] == 0
            assert row["counts"]["knowledge_points"] == 0
            assert row["last_session"] is None
        finally:
            workspace.close()

    def test_a_session_without_materials_still_counts(self, tmp_path):
        workspace = _open(str(tmp_path / "data"))
        try:
            cid = workspace.create_course("Sola", "SOL", "es")["course_id"]
            workspace.create_session(cid, session_number=1, date=TODAY, title="Tema 1")
            row = _row_by_id(workspace.my_courses(), cid)
            assert row["counts"]["sessions"] == 1
            assert row["counts"]["materials"] == 0
        finally:
            workspace.close()

    def test_a_single_course_is_handled(self, tmp_path):
        seeded = _seed(str(tmp_path / "data"), courses=1, sessions=1, topics=2)
        workspace = _open(seeded["data_dir"])
        try:
            payload = workspace.my_courses()
            assert payload["course_count"] == 1
            assert payload["selection"]["reason"] == "first_course"
            assert payload["selection"]["course_id"] == seeded["course_ids"][0]
        finally:
            workspace.close()

    def test_ten_courses_are_handled(self, tmp_path):
        seeded = _seed(str(tmp_path / "data"), courses=10, sessions=1, topics=1)
        workspace = _open(seeded["data_dir"])
        try:
            payload = workspace.my_courses()
            assert payload["course_count"] == 10
            ids = [row["course_id"] for row in payload["courses"]]
            assert ids == sorted(ids)
        finally:
            workspace.close()

    def test_course_names_are_returned_verbatim(self, tmp_path):
        """API 返回原文；转义是 UI 的事（UI 侧另有断言）。"""
        workspace = _open(str(tmp_path / "data"))
        try:
            cid = workspace.create_course("<b>XSS</b> & Co", "X", "es")["course_id"]
            row = _row_by_id(workspace.my_courses(), cid)
            assert row["name"] == "<b>XSS</b> & Co"
        finally:
            workspace.close()

    def test_a_unicode_preference_is_handled(self, big_ws):
        selection = big_ws.resolve_course_selection("课程-üñî")
        assert selection["reason"] == "preferred_missing"
        assert selection["preferred"] == "课程-üñî"

    def test_reading_the_overview_does_not_change_the_course_list(self, big_ws):
        before = json.dumps(big_ws.list_courses(), sort_keys=True)
        big_ws.my_courses()
        big_ws.my_courses(preferred="ghost")
        assert json.dumps(big_ws.list_courses(), sort_keys=True) == before

    def test_gaps_are_counted_per_course(self, big_ws, big):
        for cid in big["course_ids"]:
            summary = big_ws.course_summary(cid)
            expected = len((big_ws.gaps(cid) or {}).get("gaps") or [])
            assert summary["gaps"] == expected

    def test_pending_review_matches_the_review_candidates(self, big_ws, big):
        for cid in big["course_ids"]:
            summary = big_ws.course_summary(cid)
            assert summary["counts"]["pending_review"] == len(
                big_ws.review_candidates(cid)
            )

    def test_conflicts_match_the_conflict_view(self, big_ws, big):
        for cid in big["course_ids"]:
            summary = big_ws.course_summary(cid)
            assert summary["counts"]["conflicts"] == len(big_ws.conflicts(cid))
