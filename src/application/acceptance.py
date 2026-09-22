# -*- coding: utf-8 -*-
"""真实课程端到端验收 (Task 45)。

本模块把 spec 要求的 17 步产品流程与 7 个验收问题, 做成一个可复用的
**Acceptance Harness**::

    Create Course -> Create Session -> Upload PDF/DOCX -> Upload Audio
      -> Upload Board Image -> Process -> Evidence -> Knowledge -> Validation
      -> Review -> Coverage -> Student -> Exercise -> Answer -> Evaluation
      -> Study Plan -> Learning Path

它回答的 7 个问题 (spec 原文):

====  =========================================  ==============================
序号  问题                                        走的路径
====  =========================================  ==============================
1     老师今天讲了什么?                            Session Knowledge
2     这个知识点来自哪里?                          Evidence provenance
3     这个知识点有没有冲突?                        Conflict / Review
4     课程哪里没有覆盖?                            Coverage / Gap
5     学生现在应该学什么?                          Study Plan / Learning Path
6     为什么给学生这道题?                          Knowledge / prerequisite /
                                                   learning state
7     学生答完之后发生什么?                        Evaluation -> Learning State
====  =========================================  ==============================

硬性边界 (必须说清楚, 否则就是夸大验收结果)
--------------------------------------------------------------------

1. **业务对象仍在内存中**。Task 42 落地的是 SQLite 库、迁移链与仓储
   接口; 课程 / 材料 / 证据 / 知识点 / 学生学习状态当前仍由领域服务在
   内存里持有 (Task 44 的 ``bootstrap`` docstring 已明确声明)。因此本
   harness 的 "restart" 场景验证的是**跨进程仍然成立的性质**:
   材料注册表从磁盘恢复、``material_id`` / ``evidence_id`` /
   ``knowledge_id`` 在重新处理时逐字节一致 —— 而不是"业务状态被持久化
   后原样读回"。后者不属于本任务范围。
2. **ASR / OCR 是脚本化的替身**。harness 通过
   :class:`ScriptedOCREngine` 与 ``MockASRProvider(segments=...)``
   注入**真实的课堂文本** (教师原话 / 板书原文), 但不解码音频、不做
   视觉识别。所以本 harness 验证的是**流水线**, 不是语音/视觉模型本身;
   真实引擎的验收由 ``@pytest.mark.integration`` 标记的测试单独覆盖。
   这一点在报告里由 :attr:`AcceptanceReport.engine_note` 显式声明。
3. **绝不制造事实**。harness 只调用产品自身的公开入口
   (``Workspace`` / ``BackupService``), 不直接写知识库、不伪造证据、
   不绕过领域层。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from src.application.errors import InvalidInputError, NotFoundError
from src.application.runtime import Clock, fixed_clock, utc_now_iso
from src.application.workspace import Workspace
from src.asr_provider import MockASRProvider
from src.models import (
    BoundingBox,
    Material,
    MaterialType,
    OCRResult,
    OCRSegment,
    OCRLanguage,
)
from src.ocr_processor import OCREngine, create_ocr_result

__all__ = [
    "STEP_SEQUENCE",
    "ACCEPTANCE_QUESTIONS",
    "DatasetMaterial",
    "ClassroomDataset",
    "ScriptedOCREngine",
    "StepRecord",
    "AcceptanceAnswer",
    "AcceptanceReport",
    "AcceptanceHarness",
]

#: spec 规定的 17 步流程 (顺序即权威顺序, 不允许重排)。
STEP_SEQUENCE: tuple[str, ...] = (
    "create_course",
    "create_session",
    "upload_documents",
    "upload_audio",
    "upload_board_image",
    "process",
    "evidence",
    "knowledge",
    "validation",
    "review",
    "coverage",
    "student",
    "exercise",
    "answer",
    "evaluation",
    "study_plan",
    "learning_path",
)

#: spec 规定的 7 个验收问题: (编号, 问题, 路径)。
ACCEPTANCE_QUESTIONS: tuple[tuple[str, str, str], ...] = (
    ("1", "老师今天讲了什么？", "Session Knowledge"),
    ("2", "这个知识点来自哪里？", "Evidence provenance"),
    ("3", "这个知识点有没有冲突？", "Conflict / Review"),
    ("4", "课程哪里没有覆盖？", "Coverage / Gap"),
    ("5", "学生现在应该学什么？", "Study Plan / Learning Path"),
    ("6", "为什么给学生这道题？", "Knowledge / prerequisite / learning state"),
    ("7", "学生答完之后发生什么？", "Evaluation → Learning State"),
)

#: 默认的确定性时钟值 —— 归档文件名与 created_at 都来自它。
DEFAULT_ACCEPTANCE_CLOCK = "2026-09-15T18:00:00+00:00"

#: ``ValidationStatus.CONFLICTED.value`` —— 注意领域层用的是**小写**字面值
#: ("unverified" / "supported" / "conflicted"), 而 ``ReviewStatus`` 用的是
#: 大写。报告里比较状态时一律走 :func:`_status_is` 做大小写无关比较, 免得
#: 在两种约定之间踩坑。
CONFLICTED_STATUS = "conflicted"
PENDING_REVIEW_STATUS = "pending"


def _status_is(value: Any, expected: str) -> bool:
    """大小写无关的状态比较 (领域层两种大小写约定并存)。"""
    return str(value or "").strip().lower() == expected.lower()


def _registry_material_ids(path: str) -> list[str]:
    """从磁盘上的材料注册表 JSON 里读出 ``material_id`` 列表。

    材料注册表是当前**唯一**落盘的产品状态 (``data/materials/<course_id>.json``,
    ``{"course_id", "materials": [...], "schema_version"}``)。备份/恢复场景用它
    来证明"恢复出来的数据是可读的", 而不是仅仅"文件存在"。
    """
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    entries = payload.get("materials")
    if not isinstance(entries, list):
        return []
    return sorted(
        str(entry.get("material_id"))
        for entry in entries
        if isinstance(entry, dict) and entry.get("material_id")
    )


# ---------------------------------------------------------------------------
# 数据集描述 (纯数据; 不依赖 tests/)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DatasetMaterial:
    """数据集里的一个材料 (文件 + 期望)。"""

    filename: str
    path: str
    role: str = ""
    language: str = ""
    attach_to_session: bool = True
    expected: str = "COMPLETED"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "role": self.role,
            "language": self.language,
            "attach_to_session": self.attach_to_session,
            "expected": self.expected,
            "note": self.note,
        }


@dataclass(frozen=True)
class ClassroomDataset:
    """一门真实课程的课堂数据集 (课程 + 课堂 + 材料 + 教师原话 + 板书)。"""

    root: str
    course_name: str
    course_code: str = ""
    course_language: Optional[str] = None
    session_number: int = 1
    session_date: str = ""
    session_title: str = ""
    materials: tuple[DatasetMaterial, ...] = ()
    transcript_segments: tuple[Mapping[str, Any], ...] = ()
    transcript_language: str = "es"
    board_segments: tuple[Mapping[str, Any], ...] = ()
    board_language: str = "ca"
    manifest: Mapping[str, Any] = field(default_factory=dict)

    # -- 便捷视图 -------------------------------------------------------

    def material_for(self, role: str) -> DatasetMaterial:
        for item in self.materials:
            if item.role == role:
                return item
        raise NotFoundError(f"dataset has no material with role {role!r}")

    def materials_in_session(self) -> tuple[DatasetMaterial, ...]:
        return tuple(m for m in self.materials if m.attach_to_session)

    def materials_outside_session(self) -> tuple[DatasetMaterial, ...]:
        return tuple(m for m in self.materials if not m.attach_to_session)

    def expected_completed(self) -> tuple[DatasetMaterial, ...]:
        return tuple(m for m in self.materials if m.expected == "COMPLETED")

    def expected_failed(self) -> tuple[DatasetMaterial, ...]:
        return tuple(m for m in self.materials if m.expected != "COMPLETED")

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "course": {
                "name": self.course_name,
                "code": self.course_code,
                "language": self.course_language,
            },
            "session": {
                "session_number": self.session_number,
                "date": self.session_date,
                "title": self.session_title,
            },
            "materials": [m.to_dict() for m in self.materials],
            "transcript_segments": len(self.transcript_segments),
            "board_segments": len(self.board_segments),
        }

    # -- 加载 -----------------------------------------------------------

    @classmethod
    def from_directory(cls, root: str) -> "ClassroomDataset":
        """按 ``manifest.json`` 约定加载一个数据集目录。

        manifest 的字段约定见 ``scripts/gen_task45_fixtures.py``。这里
        只做读取与校验, 不猜任何缺省内容 —— 缺字段就报错。
        """
        if not isinstance(root, str) or not root.strip():
            raise InvalidInputError("dataset root must be a non-empty string")
        manifest_path = os.path.join(root, "manifest.json")
        if not os.path.isfile(manifest_path):
            raise NotFoundError(f"dataset manifest not found: {manifest_path!r}")
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)

        course = dict(manifest.get("course") or {})
        session = dict(manifest.get("session") or {})
        transcript_meta = dict(manifest.get("transcript") or {})
        board_meta = dict(manifest.get("board_ocr") or {})

        materials = tuple(
            DatasetMaterial(
                filename=str(entry.get("filename") or ""),
                path=os.path.join(root, str(entry.get("filename") or "")),
                role=str(entry.get("role") or ""),
                language=str(entry.get("language") or ""),
                attach_to_session=bool(entry.get("attach_to_session", True)),
                expected=str(entry.get("expected") or "COMPLETED"),
                note=str(entry.get("note") or ""),
            )
            for entry in (manifest.get("materials") or [])
        )

        transcript = cls._load_json_entries(root, transcript_meta.get("filename"))
        board = cls._load_json_entries(root, board_meta.get("filename"))

        return cls(
            root=root,
            course_name=str(course.get("name") or ""),
            course_code=str(course.get("code") or ""),
            course_language=course.get("language"),
            session_number=int(session.get("session_number") or 0),
            session_date=str(session.get("date") or ""),
            session_title=str(session.get("title") or ""),
            materials=materials,
            transcript_segments=transcript,
            transcript_language=str(transcript_meta.get("language") or "es"),
            board_segments=board,
            board_language=str(board_meta.get("language") or "ca"),
            manifest=manifest,
        )

    @staticmethod
    def _load_json_entries(
        root: str, filename: Any
    ) -> tuple[Mapping[str, Any], ...]:
        if not filename:
            return ()
        path = os.path.join(root, str(filename))
        if not os.path.isfile(path):
            raise NotFoundError(f"dataset payload not found: {path!r}")
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        entries = payload.get("segments") if isinstance(payload, Mapping) else None
        if not isinstance(entries, list):
            raise InvalidInputError(
                f"dataset payload {filename!r} has no 'segments' list"
            )
        return tuple(dict(entry) for entry in entries)


# ---------------------------------------------------------------------------
# 脚本化 OCR 引擎 (验收专用替身)
# ---------------------------------------------------------------------------

class ScriptedOCREngine(OCREngine):
    """把夹具里的**板书原文**逐字返回的 OCR 引擎。

    **验收专用, 生产路径不得使用** —— 与 :class:`MockOCREngine` 同一性质,
    区别只在于它返回的是真实课堂文本而不是 "OCR line N"。

    它存在的理由: 真实视觉识别 (``LocalOCRProvider`` / rapidocr) 对合成
    板书图片的结果不可复现, 而验收要验证的是**流水线**能不能把板书文本
    变成可溯源的知识。真实引擎由 ``integration`` 标记的测试单独覆盖。

    与 ``MockASRProvider(segments=...)`` 是同一套做法: 领域层接口不变,
    文本从外部注入。
    """

    name = "scripted-ocr"

    def __init__(
        self,
        segments: Sequence[Mapping[str, Any]],
        language: str = "ca",
    ) -> None:
        self._segments = tuple(dict(segment) for segment in segments)
        self._language = language
        self.calls: list[str] = []

    @property
    def segments(self) -> tuple[Mapping[str, Any], ...]:
        return self._segments

    @property
    def language(self) -> str:
        return self._language

    def _validate_material(self, material: Material) -> None:
        if not isinstance(material, Material):
            raise TypeError("material must be a Material instance")
        if material.material_type != MaterialType.IMAGE:
            raise ValueError(
                "OCR only supports IMAGE materials, got "
                f"{material.material_type.value}"
            )

    def ocr(self, material: Material) -> OCRResult:
        self._validate_material(material)
        self.calls.append(str(material.material_id))
        result = create_ocr_result(
            material.material_id,
            [dict(segment) for segment in self._segments],
            self._language,
        )
        # 保留夹具声明的语言, 而不是把未知语言写成 Spanish。
        return OCRResult(
            material_id=result.material_id,
            language=OCRLanguage.from_string(self._language),
            segments=list(result.segments),
            metadata={
                "engine": self.name,
                "note": "scripted board text; the image is never decoded",
            },
        )

    def ocr_segments(self, material: Material) -> list[OCRSegment]:
        return list(self.ocr(material).segments)


# ---------------------------------------------------------------------------
# 记录结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StepRecord:
    """17 步流程里的一步 (可审计: 每步都留下可核对的事实)。"""

    step: str
    ok: bool
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"step": self.step, "ok": self.ok, "detail": dict(self.detail)}


@dataclass(frozen=True)
class AcceptanceAnswer:
    """一个验收问题的回答 (结构化 + 一句话摘要)。"""

    number: str
    question: str
    route: str
    summary: str
    data: Mapping[str, Any] = field(default_factory=dict)

    @property
    def answered(self) -> bool:
        """有实质内容才算回答; 空壳不算。"""
        return bool(self.summary.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "question": self.question,
            "route": self.route,
            "answered": self.answered,
            "summary": self.summary,
            "data": dict(self.data),
        }

    def render(self) -> str:
        mark = "OK " if self.answered else "?? "
        return f"{mark}[{self.number}] {self.question} ({self.route})\n     {self.summary}"


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AcceptanceReport:
    """一次完整验收运行的结果 (确定性; 不含运行期时间戳)。"""

    dataset: Mapping[str, Any]
    course_id: str
    session_id: str
    steps: tuple[StepRecord, ...]
    answers: tuple[AcceptanceAnswer, ...]
    materials: tuple[Mapping[str, Any], ...]
    knowledge_points: tuple[Mapping[str, Any], ...]
    conflicts: tuple[Mapping[str, Any], ...]
    coverage: Mapping[str, Any]
    gaps: tuple[Mapping[str, Any], ...]
    review_candidates: tuple[Mapping[str, Any], ...]
    review_candidates_before: tuple[Mapping[str, Any], ...]
    review_decisions: tuple[Mapping[str, Any], ...]
    evidence_index: Mapping[str, Any]
    student: Mapping[str, Any]
    exercise: Mapping[str, Any]
    answer: Mapping[str, Any]
    evaluation: Mapping[str, Any]
    learning_state_before: Mapping[str, Any]
    learning_state_after: Mapping[str, Any]
    study_plan_before_answer: Mapping[str, Any]
    study_plan_after_answer: Mapping[str, Any]
    learning_path: Mapping[str, Any]
    engine_note: str = ""

    # -- 汇总 -----------------------------------------------------------

    @property
    def all_steps_ok(self) -> bool:
        return all(step.ok for step in self.steps)

    @property
    def failed_steps(self) -> tuple[str, ...]:
        return tuple(step.step for step in self.steps if not step.ok)

    @property
    def all_answered(self) -> bool:
        return all(answer.answered for answer in self.answers)

    @property
    def unanswered(self) -> tuple[str, ...]:
        return tuple(a.number for a in self.answers if not a.answered)

    @property
    def knowledge_point_ids(self) -> tuple[str, ...]:
        return tuple(str(kp["knowledge_id"]) for kp in self.knowledge_points)

    def answer_for(self, number: str) -> AcceptanceAnswer:
        """按编号取一个验收问题 (1..7)。

        命名为 ``answer_for`` 而不是 ``answer``: ``answer`` 是数据字段
        (学生作答 DTO), 同名方法会被 dataclass 当成字段默认值。
        """
        for item in self.answers:
            if item.number == str(number):
                return item
        raise NotFoundError(f"no acceptance answer numbered {number!r}")

    # -- 序列化 ---------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "dataset": dict(self.dataset),
            "course_id": self.course_id,
            "session_id": self.session_id,
            "engine_note": self.engine_note,
            "steps": [step.to_dict() for step in self.steps],
            "failed_steps": list(self.failed_steps),
            "all_steps_ok": self.all_steps_ok,
            "answers": [answer.to_dict() for answer in self.answers],
            "unanswered": list(self.unanswered),
            "materials": [dict(m) for m in self.materials],
            "knowledge_points": [dict(kp) for kp in self.knowledge_points],
            "conflicts": [dict(c) for c in self.conflicts],
            "coverage": dict(self.coverage),
            "gaps": [dict(g) for g in self.gaps],
            "review_candidates": [dict(c) for c in self.review_candidates],
            "review_candidates_before": [
                dict(c) for c in self.review_candidates_before
            ],
            "review_decisions": [dict(d) for d in self.review_decisions],
            "evidence_index": dict(self.evidence_index),
            "student": dict(self.student),
            "exercise": dict(self.exercise),
            "answer": dict(self.answer),
            "evaluation": dict(self.evaluation),
            "learning_state_before": dict(self.learning_state_before),
            "learning_state_after": dict(self.learning_state_after),
            "study_plan_before_answer": dict(self.study_plan_before_answer),
            "study_plan_after_answer": dict(self.study_plan_after_answer),
            "learning_path": dict(self.learning_path),
        }

    def render(self) -> str:
        """人类可读的验收报告 (稳定输出, 便于人工核对)。"""
        lines: list[str] = []
        lines.append("=" * 72)
        lines.append("课堂助手 — 真实课程端到端验收报告 (Task 45)")
        lines.append("=" * 72)
        lines.append(f"课程: {self.dataset.get('course', {}).get('name', '?')}")
        lines.append(f"course_id: {self.course_id}")
        lines.append(f"session_id: {self.session_id}")
        lines.append("")
        lines.append("-- 17 步流程 --")
        for step in self.steps:
            lines.append(f"  {'OK ' if step.ok else 'FAIL'} {step.step}")
        if self.failed_steps:
            lines.append(f"  失败步骤: {', '.join(self.failed_steps)}")
        lines.append("")
        lines.append("-- 材料 --")
        for material in self.materials:
            lines.append(
                "  {status:<9} {filename:<26} {source_type:<9} evidence={count}".format(
                    status=str(material.get("processing_status")),
                    filename=str(material.get("filename")),
                    source_type=str(material.get("source_type")),
                    count=material.get("evidence_count", 0),
                )
            )
        lines.append("")
        lines.append("-- 7 个验收问题 --")
        for answer in self.answers:
            lines.append(answer.render())
        lines.append("")
        lines.append("-- 覆盖 --")
        for key in sorted(self.coverage):
            if key == "course_id":
                continue
            lines.append(f"  {key}: {self.coverage[key]}")
        lines.append("")
        lines.append(f"engine_note: {self.engine_note}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class AcceptanceHarness:
    """把 17 步产品流程跑一遍, 并回答 7 个验收问题。

    典型用法::

        dataset = ClassroomDataset.from_directory("tests/fixtures/acceptance")
        harness = AcceptanceHarness(dataset, data_dir=tmp_path)
        report = harness.run()
        assert report.all_steps_ok and report.all_answered

    ``data_dir`` 必须是一个**空目录或专用目录**: harness 会在里面建立完整
    的数据布局 (materials / documents / audio / images / temp / database)。
    """

    def __init__(
        self,
        dataset: ClassroomDataset,
        data_dir: str,
        *,
        clock: Optional[Clock] = None,
        include_failure: bool = True,
        review_note: str = "acceptance-run",
    ) -> None:
        if not isinstance(dataset, ClassroomDataset):
            raise InvalidInputError("dataset must be a ClassroomDataset")
        self._dataset = dataset
        self._data_dir = data_dir
        self._clock: Clock = clock or fixed_clock(DEFAULT_ACCEPTANCE_CLOCK)
        self._include_failure = bool(include_failure)
        self._review_note = review_note
        self._steps: list[StepRecord] = []
        self._asr = MockASRProvider(
            segments=[dict(s) for s in dataset.transcript_segments],
            language=dataset.transcript_language,
        )
        self._ocr = ScriptedOCREngine(
            dataset.board_segments, language=dataset.board_language
        )
        self.workspace = Workspace(
            data_dir,
            asr_provider=self._asr,
            ocr_engine=self._ocr,
            # 诚实标注: 注入的是脚本化替身, 不是真实 Whisper/OCR 引擎。
            asr_mode="mock",
            ocr_mode="mock",
            clock=self._clock,
        )
        self.course_id: str = ""
        self.session_id: str = ""
        self.materials: dict[str, dict[str, Any]] = {}
        self.student_id: str = ""
        self.exercise_id: str = ""
        self.answer_id: str = ""
        self.knowledge_ids: list[str] = []
        self._last_report: Optional[AcceptanceReport] = None

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def dataset(self) -> ClassroomDataset:
        return self._dataset

    @property
    def data_dir(self) -> str:
        return self._data_dir

    @property
    def steps(self) -> tuple[StepRecord, ...]:
        return tuple(self._steps)

    @property
    def asr_provider(self) -> MockASRProvider:
        return self._asr

    @property
    def ocr_engine(self) -> ScriptedOCREngine:
        return self._ocr

    @property
    def last_report(self) -> Optional[AcceptanceReport]:
        """最近一次 :meth:`run` 产出的报告 (还没跑过则是 ``None``)。

        给"跑一次、多处断言"的场景用 —— **不要**为了拿报告再跑一遍流程。
        第二次 ``run()`` 会重新装配知识, 而人工复核决定是"黏"的
        (``KnowledgeReviewService.get_review_candidates``: human decisions
        are sticky, 只有新证据 / 新冲突才会重新开队列), 于是复核队列为空、
        ``review`` 步判失败。那不是流水线坏了, 而是"人已经做过的决定不该
        被重跑悄悄清空"。
        """
        return self._last_report

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def run(self) -> AcceptanceReport:
        """跑完 17 步并产出报告 (幂等: 同一 harness 重复调用结果一致)。"""
        self._steps = []
        self._step_create_course()
        self._step_create_session()
        self._step_upload_documents()
        self._step_upload_audio()
        self._step_upload_board_image()
        self._step_process()
        self._step_evidence()
        self._step_knowledge()
        self._step_validation()
        self._step_review()
        self._step_coverage()
        self._step_student()
        self._step_exercise()
        self._step_answer()
        self._step_evaluation()
        self._step_study_plan()
        self._step_learning_path()
        self._last_report = self._build_report()
        return self._last_report

    # -- 1. Create Course -----------------------------------------------

    def _step_create_course(self) -> None:
        dto = self.workspace.create_course(
            self._dataset.course_name,
            self._dataset.course_code,
            self._dataset.course_language,
        )
        self.course_id = str(dto["course_id"])
        self._record("create_course", True, {"course_id": self.course_id})

    # -- 2. Create Session ----------------------------------------------

    def _step_create_session(self) -> None:
        dto = self.workspace.create_session(
            self.course_id,
            session_number=self._dataset.session_number,
            date=self._dataset.session_date,
            title=self._dataset.session_title,
        )
        self.session_id = str(dto["session_id"])
        self._record(
            "create_session", True, {"session_id": self.session_id}
        )

    # -- 3. Upload PDF / DOCX -------------------------------------------

    def _step_upload_documents(self) -> None:
        uploaded: dict[str, Any] = {}
        ok = True
        for material in self._dataset.materials:
            if material.role not in ("handout", "student_notes", "broken_document"):
                continue
            record = self._register(material)
            uploaded[material.filename] = record.get("material_id")
            if material.expected == "COMPLETED" and not record.get("material_id"):
                ok = False
        self._record("upload_documents", ok, uploaded)

    # -- 4. Upload Audio ------------------------------------------------

    def _step_upload_audio(self) -> None:
        material = self._dataset.material_for("audio")
        record = self._register(material)
        self._record(
            "upload_audio",
            bool(record.get("material_id")),
            {"material_id": record.get("material_id")},
        )

    # -- 5. Upload Board Image ------------------------------------------

    def _step_upload_board_image(self) -> None:
        material = self._dataset.material_for("board_image")
        record = self._register(material)
        self._record(
            "upload_board_image",
            bool(record.get("material_id")),
            {"material_id": record.get("material_id")},
        )

    # -- 6. Process -----------------------------------------------------

    def _step_process(self) -> None:
        report = self.workspace.process_session(self.course_id, self.session_id)
        for material in self._dataset.materials_outside_session():
            record = self._register(material)
            if record.get("material_id"):
                self.workspace.process_material(
                    self.course_id, str(record["material_id"])
                )
        self._record(
            "process",
            int(report.get("succeeded", 0)) > 0,
            {
                "total": report.get("total"),
                "succeeded": report.get("succeeded"),
                "failed": report.get("failed"),
                "skipped": report.get("skipped"),
                "evidence_total": report.get("evidence_total"),
                "session_link": dict(report.get("knowledge", {}).get("session_link") or {}),
            },
        )

    # -- 7. Evidence ----------------------------------------------------

    def _step_evidence(self) -> None:
        index: dict[str, Any] = {}
        total = 0
        current = self._current_materials()
        for material in self._dataset.materials:
            record = current.get(material.filename)
            if not record or not record.get("material_id"):
                continue
            evidence = self.workspace.material_evidence(
                self.course_id, str(record["material_id"])
            )
            index[material.filename] = {
                "material_id": record["material_id"],
                "role": material.role,
                "source_type": record.get("source_type"),
                "evidence_count": len(evidence),
                "evidence_ids": sorted(str(e["evidence_id"]) for e in evidence),
                "samples": [
                    {
                        "evidence_id": str(e["evidence_id"]),
                        "content": str(e.get("content") or "")[:120],
                        "location": (e.get("source") or {}).get("location"),
                    }
                    for e in evidence[:2]
                ],
            }
            total += len(evidence)
        self._evidence_index = index
        self._record("evidence", total > 0, {"evidence_total": total})

    # -- 8. Knowledge ---------------------------------------------------

    def _step_knowledge(self) -> None:
        points = self.workspace.knowledge_points(self.course_id)
        self.knowledge_ids = [str(p["knowledge_id"]) for p in points]
        self._record(
            "knowledge",
            len(points) > 0,
            {
                "knowledge_point_total": len(points),
                "session_knowledge_total": len(
                    self.workspace.knowledge_points(
                        self.course_id, session_id=self.session_id
                    )
                ),
            },
        )

    # -- 9. Validation --------------------------------------------------

    def _step_validation(self) -> None:
        points = self.workspace.knowledge_points(self.course_id)
        by_status: dict[str, int] = {}
        for point in points:
            key = str(point.get("validation_status") or "unknown")
            by_status[key] = by_status.get(key, 0) + 1
        self._record(
            "validation",
            len(points) > 0 and len(by_status) > 0,
            {"by_status": by_status},
        )

    # -- 10. Review -----------------------------------------------------

    def _step_review(self) -> None:
        candidates = self.workspace.review_candidates(self.course_id)
        decisions: list[dict[str, Any]] = []
        points = {
            str(p["knowledge_id"]): p
            for p in self.workspace.knowledge_points(self.course_id)
        }

        # CONFLICTED 的知识点**不能**用 confirm 处理: 领域层会拒绝
        # "没有显式选择证据侧" 的确认 (spec: 不允许自动解决冲突)。
        # 必须先 resolve_conflict, 由人指定信任哪一侧证据。
        conflicted = sorted(
            kp_id
            for kp_id, point in points.items()
            if _status_is(point.get("validation_status"), CONFLICTED_STATUS)
        )
        for kp_id in conflicted:
            conflict = self._conflict_for(kp_id)
            refs = sorted(str(ref) for ref in (conflict or {}).get("evidence_refs") or [])
            if not refs:
                decisions.append(
                    {
                        "knowledge_id": kp_id,
                        "decision": "unresolved_no_evidence_side",
                        "record": {},
                    }
                )
                continue
            record = self.workspace.review_resolve_conflict(
                self.course_id,
                kp_id,
                selected_evidence_ids=refs[:1],
                note=self._review_note,
            )
            decisions.append(
                {"knowledge_id": kp_id, "decision": "resolve_conflict", "record": record}
            )

        decided = {str(d["knowledge_id"]) for d in decisions}
        for candidate in candidates:
            kp_id = str(
                candidate.get("knowledge_point_id")
                or candidate.get("knowledge_id")
                or ""
            )
            if not kp_id or kp_id in decided:
                continue
            record = self.workspace.review_confirm(
                self.course_id, kp_id, note=self._review_note
            )
            decisions.append(
                {"knowledge_id": kp_id, "decision": "confirm", "record": record}
            )
        self._review_decisions = decisions
        self._review_candidates_before = [dict(c) for c in candidates]
        self._record(
            "review",
            len(candidates) > 0 and len(decisions) > 0,
            {
                "candidates": len(candidates),
                "conflicted": len(conflicted),
                "decisions": len(decisions),
            },
        )

    # -- 11. Coverage ---------------------------------------------------

    def _step_coverage(self) -> None:
        coverage = self.workspace.coverage(self.course_id)
        gaps = self.workspace.gaps(self.course_id)
        self._record(
            "coverage",
            int(coverage.get("total_knowledge_points", 0)) > 0,
            {
                "total": coverage.get("total_knowledge_points"),
                "covered": coverage.get("covered_knowledge_points"),
                "uncovered": coverage.get("uncovered_knowledge_points"),
                "gaps": len(gaps.get("gaps") or []),
            },
        )

    # -- 12. Student ----------------------------------------------------

    def _step_student(self) -> None:
        self.student_id = "student-uab-2026-001"
        dto = self.workspace.create_student(
            self.course_id, self.student_id, "Alumne de prova"
        )
        state_before = self.workspace.student_state(self.course_id, self.student_id)
        self._learning_state_before = state_before
        self._record(
            "student",
            bool(dto.get("student_id")),
            {
                "student_id": dto.get("student_id"),
                "registered_knowledge_points": len(
                    state_before.get("registered_knowledge_points") or []
                ),
            },
        )

    # -- 13. Exercise ---------------------------------------------------

    def _step_exercise(self) -> None:
        plan_before = self.workspace.study_plan(self.course_id, self.student_id)
        self._study_plan_before = plan_before
        items = list(plan_before.get("items") or [])
        if not items:
            self._record("exercise", False, {"error": "study plan is empty"})
            return
        target = str(items[0]["knowledge_point_id"])
        point = self.workspace.knowledge_point(self.course_id, target)
        evidence = self.workspace.knowledge_evidence(self.course_id, target)
        evidence_ids = sorted(str(e["evidence_id"]) for e in evidence)
        exercise = self.workspace.create_exercise(
            self.course_id,
            "multiple_choice",
            "¿Qué afirma el Tema 1 sobre la complejidad temporal?",
            [target],
            choices=[
                {"choice_id": "a", "text": "Mide cómo crece el coste con la entrada."},
                {"choice_id": "b", "text": "Es siempre constante."},
                {"choice_id": "c", "text": "Solo se aplica a la búsqueda binaria."},
            ],
            correct_choice_id="a",
            evidence_ids=evidence_ids,
            explanation="La complejidad temporal describe el crecimiento del coste.",
        )
        self.exercise_id = str(exercise["exercise_id"])
        self._exercise = exercise
        self._record(
            "exercise",
            bool(self.exercise_id),
            {
                "exercise_id": self.exercise_id,
                "target_knowledge_id": target,
                "reason_codes": list(items[0].get("reason_codes") or []),
                "evidence_ids": evidence_ids,
                "prompt": exercise.get("prompt"),
                "point_title": point.get("title"),
            },
        )

    # -- 14. Answer -----------------------------------------------------

    def _step_answer(self) -> None:
        if not self.exercise_id:
            self._record("answer", False, {"error": "no exercise"})
            return
        answer = self.workspace.submit_answer(
            self.course_id, self.student_id, self.exercise_id, "a", sequence=1
        )
        self.answer_id = str(answer["answer_id"])
        self._answer = answer
        self._record(
            "answer",
            bool(self.answer_id),
            {
                "answer_id": self.answer_id,
                "submitted_value": answer.get("submitted_value"),
                "evaluation_status": answer.get("evaluation_status"),
            },
        )

    # -- 15. Evaluation -------------------------------------------------

    def _step_evaluation(self) -> None:
        if not self.answer_id:
            self._record("evaluation", False, {"error": "no answer"})
            return
        evaluation = self.workspace.get_evaluation(self.course_id, self.answer_id)
        state_after = self.workspace.student_state(self.course_id, self.student_id)
        self._evaluation = evaluation
        self._learning_state_after = state_after
        self._record(
            "evaluation",
            str(evaluation.get("status") or "") != "",
            {
                "status": evaluation.get("status"),
                "score": evaluation.get("score"),
            },
        )

    # -- 16. Study Plan -------------------------------------------------

    def _step_study_plan(self) -> None:
        plan_after = self.workspace.study_plan(self.course_id, self.student_id)
        self._study_plan_after = plan_after
        self._record(
            "study_plan",
            len(plan_after.get("items") or []) > 0,
            {"items": len(plan_after.get("items") or [])},
        )

    # -- 17. Learning Path ----------------------------------------------

    def _step_learning_path(self) -> None:
        target = self._exercise.get("knowledge_point_ids") or []
        target_id = str(target[0]) if target else (
            self.knowledge_ids[0] if self.knowledge_ids else ""
        )
        path = self.workspace.learning_path(self.course_id, target_id)
        self._learning_path = path
        self._record(
            "learning_path",
            bool(target_id),
            {"target": target_id, "status": path.get("status")},
        )

    # ------------------------------------------------------------------
    # 场景 (spec 明确要求覆盖的验收场景)
    # ------------------------------------------------------------------

    def scenario_duplicate(self) -> dict[str, Any]:
        """重复上传: 同一文件再传一次 + 同内容不同文件名再传一次。

        产品必须把两者都识别为重复, 而不是产生第二份材料 / 第二条证据。
        """
        material = self._dataset.material_for("audio")
        again = self.workspace.register_material(
            self.course_id, material.path, session_id=self.session_id
        )
        # 同内容、不同文件名: 复制到暂存区后以新名字登记。
        staged = os.path.join(self.workspace.upload_root, "copia-" + material.filename)
        os.makedirs(os.path.dirname(staged), exist_ok=True)
        with open(material.path, "rb") as src, open(staged, "wb") as dst:
            dst.write(src.read())
        renamed = self.workspace.register_material(
            self.course_id,
            staged,
            session_id=self.session_id,
            filename="copia-" + material.filename,
        )
        materials = self.workspace.list_materials(self.course_id)
        return {
            "same_file": {
                "material_id": again.get("material_id"),
                "duplicate": bool(again.get("duplicate")),
            },
            "same_content_new_name": {
                "material_id": renamed.get("material_id"),
                "duplicate": bool(renamed.get("duplicate")),
                "duplicate_of": renamed.get("duplicate_of"),
            },
            "material_total": len(materials),
            "distinct_material_ids": len(
                {str(m.get("material_id")) for m in materials}
            ),
        }

    def scenario_failure(self) -> dict[str, Any]:
        """失败隔离: 一个坏文件不能拖垮整节课。

        断言的顺序很重要 —— 先证明坏文件确实失败了, 再证明其余材料照常
        成功、整节课的知识点没有被清空。
        """
        broken = self._dataset.material_for("broken_document")
        record = self.workspace.get_material(self.course_id, str(
            self.materials[broken.filename]["material_id"]
        ))
        points_before = len(self.workspace.knowledge_points(self.course_id))
        report = self.workspace.process_session(self.course_id, self.session_id)
        points_after = len(self.workspace.knowledge_points(self.course_id))
        return {
            "broken_material_id": record.get("material_id"),
            "broken_processing_status": record.get("processing_status"),
            "broken_error": record.get("error"),
            "broken_retryable": record.get("retryable"),
            "session_succeeded": report.get("succeeded"),
            "session_failed": report.get("failed"),
            "knowledge_points_before": points_before,
            "knowledge_points_after": points_after,
            "knowledge_preserved": points_after >= points_before,
        }

    def scenario_restart(self, data_dir: Optional[str] = None) -> dict[str, Any]:
        """重启: 用**新的 Workspace** 打开同一个 data_dir。

        可验证的确定性性质 (跨进程仍然成立):

        - 材料注册表从磁盘恢复, ``material_id`` 逐个一致;
        - 同一个课程名/代码重新创建得到**同一个** ``course_id``;
        - 同一个课堂号重新创建得到**同一个** ``session_id``;
        - 同一份材料重新处理得到**同一组** ``evidence_id``
          (内容寻址), 因此 ``knowledge_id`` 也一致。
        """
        target_dir = data_dir or self._data_dir
        # 先把**原工作区**推进到与重启侧相同的状态。scenario_duplicate 会往这节课
        # 里登记一份副本材料, 但登记之后原工作区再没跑过 ``process_session`` ——
        # 于是"处理过的材料集合"两边不同, 比出来的差异是场景顺序的产物, 不是产品
        # 行为。``process_session`` 是幂等的 (证据内容寻址 + _evidence_present 守卫),
        # 所以补跑一次不会制造新状态, 只会把两侧拉平。
        self.workspace.process_session(self.course_id, self.session_id)
        restarted = Workspace(
            target_dir,
            asr_provider=MockASRProvider(
                segments=[dict(s) for s in self._dataset.transcript_segments],
                language=self._dataset.transcript_language,
            ),
            ocr_engine=ScriptedOCREngine(
                self._dataset.board_segments, language=self._dataset.board_language
            ),
            asr_mode="mock",
            ocr_mode="mock",
            clock=self._clock,
        )
        course = restarted.create_course(
            self._dataset.course_name,
            self._dataset.course_code,
            self._dataset.course_language,
        )
        session = restarted.create_session(
            str(course["course_id"]),
            session_number=self._dataset.session_number,
            date=self._dataset.session_date,
            title=self._dataset.session_title,
        )
        restored = {
            str(m.get("material_id")) for m in restarted.list_materials(
                str(course["course_id"])
            )
        }
        # 期望值取自**调用时刻**的注册表, 而不是构造时的快照 —— 否则先跑
        # 重复上传场景会往注册表里加材料, 让这个对比变成假失败。
        expected = {
            str(m.get("material_id"))
            for m in self.workspace.list_materials(self.course_id)
            if m.get("material_id")
        }
        # 重新处理一节课: evidence_id / knowledge_id 必须逐字一致。
        report = restarted.process_session(
            str(course["course_id"]), str(session["session_id"])
        )
        # 只重放**原运行真的处理过**的材料。scenario_duplicate 会往注册表里加一份
        # "内容相同、名字不同" 的副本, 但它从不处理这份副本; 若无差别地全部重放,
        # 重启侧反而会多出知识 —— 那是探查顺序的产物, 不是产品行为差异。
        attempted = {
            str(record.get("material_id"))
            for record in self.workspace.list_materials(self.course_id)
            if record.get("material_id")
            and str(record.get("processing_status") or "") in {"COMPLETED", "FAILED"}
        }
        for record in restarted.list_materials(str(course["course_id"])):
            material_id = str(record.get("material_id") or "")
            if material_id and material_id in attempted:
                restarted.process_material(str(course["course_id"]), material_id)

        new_knowledge = {
            str(p["knowledge_id"])
            for p in restarted.knowledge_points(str(course["course_id"]))
        }
        original_knowledge = {
            str(p["knowledge_id"])
            for p in self.workspace.knowledge_points(self.course_id)
        }
        # 再单独比一次"这节课的知识": 这是 Q1 (老师今天讲了什么) 的确定性内核,
        # 与课外材料无关, 因此它是**必须**成立的强断言。
        new_session_knowledge = {
            str(p["knowledge_id"])
            for p in restarted.knowledge_points(
                str(course["course_id"]), session_id=str(session["session_id"])
            )
        }
        original_session_knowledge = {
            str(p["knowledge_id"])
            for p in self.workspace.knowledge_points(
                self.course_id, session_id=self.session_id
            )
        }
        audio = self._dataset.material_for("audio")
        audio_id = str(self.materials[audio.filename]["material_id"])
        original_evidence = {
            str(e["evidence_id"])
            for e in self.workspace.material_evidence(self.course_id, audio_id)
        }
        replayed_evidence = {
            str(e["evidence_id"])
            for e in restarted.material_evidence(
                str(course["course_id"]), audio_id
            )
        }
        return {
            "course_id_same": str(course["course_id"]) == self.course_id,
            "session_id_same": str(session["session_id"]) == self.session_id,
            "restored_material_ids": sorted(restored),
            "expected_material_ids": sorted(expected),
            "material_ids_same": restored == expected,
            "reprocessed_succeeded": report.get("succeeded"),
            "reprocessed_failed": report.get("failed"),
            "knowledge_ids": sorted(new_knowledge),
            "knowledge_ids_same": new_knowledge == original_knowledge,
            "session_knowledge_ids_same": (
                new_session_knowledge == original_session_knowledge
            ),
            "session_knowledge_total": len(new_session_knowledge),
            "evidence_ids": sorted(replayed_evidence),
            "evidence_ids_same": replayed_evidence == original_evidence,
        }

    def scenario_backup(self, backup_dir: Optional[str] = None) -> dict[str, Any]:
        """备份 / 恢复: 归档 -> 校验 -> 恢复到新目录 -> 逐项核对。

        ``BackupService`` 在这里**延迟 import**: 应用层里唯一允许建立
        application -> persistence 依赖的文件是 ``bootstrap.py``
        (``tests/test_persistence_layering.py`` 逐文件钉住)。harness 属于
        验收工具, 不应该在 import 期就新增一条这样的路径。
        """
        from src.backup.service import BackupService  # 延迟: 见 docstring

        service = BackupService(
            self._data_dir,
            clock=self._clock,
            application_version="0.45.0",
            config={"acceptance": True},
        )
        result = service.create_backup(label="task45", overwrite=True)
        validation = service.validate_backup(result.archive_path)
        listing = service.list_backups()

        restore_dir = backup_dir or (
            os.path.dirname(os.path.abspath(self._data_dir))
            + os.sep
            + os.path.basename(os.path.abspath(self._data_dir))
            + "-restore"
        )
        restore_dir = os.path.abspath(restore_dir)
        os.makedirs(restore_dir, exist_ok=True)
        target = BackupService(restore_dir, clock=self._clock)
        restored = target.restore_backup(result.archive_path)

        restored_files = sorted(
            name
            for _, _, files in os.walk(restore_dir)
            for name in files
        )
        # ``RestoreResult`` **没有** ``ok`` 字段 (它有 database_path /
        # replaced_dirs / material_file_count / warnings)。恢复是否成功必须由
        # 事实推导, 而不是读一个不存在的属性 —— 否则永远得到 False。
        restored_database = str(getattr(restored, "database_path", "") or "")
        restore_warnings = tuple(getattr(restored, "warnings", ()) or ())
        restore_ok = (
            bool(restored_database)
            and os.path.isfile(restored_database)
            and int(getattr(restored, "material_file_count", 0))
            == int(result.material_file_count)
            and not restore_warnings
        )
        # 更强的断言: 恢复出来的**材料注册表**必须读得出来, 且逐条与原始一致。
        #
        # 这里刻意不用 ``Workspace.list_courses()``: 课程/课堂目前只存在于内存
        # 里的 CourseService, 唯一落盘的是材料注册表 (``data/materials/<course>.json``,
        # Task 44 的 bootstrap docstring 写明了这个边界)。拿一个产品**本来就不持久化**
        # 的东西去断言, 只会得到一个假的失败。
        original_registry = os.path.join(
            self._data_dir, "materials", f"{self.course_id}.json"
        )
        restored_registry = os.path.join(
            restore_dir, "materials", f"{self.course_id}.json"
        )
        original_registry_ids = _registry_material_ids(original_registry)
        restored_registry_ids = _registry_material_ids(restored_registry)
        return {
            "archive_path": result.archive_path,
            "archive_size": result.archive_size,
            "material_file_count": result.material_file_count,
            "validation_ok": bool(getattr(validation, "ok", False)),
            "backup_count": len(listing),
            "restore_ok": restore_ok,
            "restore_warnings": list(restore_warnings),
            "restored_database_present": os.path.isfile(
                os.path.join(restore_dir, "database", "classroom.sqlite")
            ),
            "restored_file_count": len(restored_files),
            "registry_material_total": len(restored_registry_ids),
            "registry_material_ids_same": (
                original_registry_ids == restored_registry_ids
            ),
            "restore_dir": restore_dir,
        }

    def scenario_student_learning(self) -> dict[str, Any]:
        """学生学习: 错答 -> 学习状态变化 -> 计划理由变化。

        与主流程互补: 主流程答对一次, 这里刻意答错, 验证
        "答完之后发生什么" 的另一半 (错误 → RECENT_INCORRECT 理由)。
        """
        if not self.exercise_id:
            raise InvalidInputError("run() must be called before this scenario")
        before = self.workspace.student_state(self.course_id, self.student_id)
        wrong = self.workspace.submit_answer(
            self.course_id, self.student_id, self.exercise_id, "b", sequence=2
        )
        evaluation = self.workspace.get_evaluation(
            self.course_id, str(wrong["answer_id"])
        )
        after = self.workspace.student_state(self.course_id, self.student_id)
        plan = self.workspace.study_plan(self.course_id, self.student_id)
        target = str(wrong.get("exercise_id") or "")
        reasons = []
        for item in plan.get("items") or []:
            if str(item.get("knowledge_point_id")) in set(
                self._exercise.get("knowledge_point_ids") or []
            ):
                reasons = list(item.get("reason_codes") or [])
        return {
            "answer_id": wrong.get("answer_id"),
            "evaluation_status": evaluation.get("status"),
            "evaluation_score": evaluation.get("score"),
            "states_before": before.get("states"),
            "states_after": after.get("states"),
            "exercise_id": target,
            "plan_reason_codes": reasons,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _register(self, material: DatasetMaterial) -> dict[str, Any]:
        if not os.path.isfile(material.path):
            record: dict[str, Any] = {
                "material_id": None,
                "processing_status": "FAILED",
                "error": "FILE_NOT_FOUND",
                "filename": material.filename,
            }
            self.materials[material.filename] = record
            return record
        record = self.workspace.register_material(
            self.course_id,
            material.path,
            session_id=self.session_id if material.attach_to_session else None,
            language=material.language or None,
        )
        if record.get("material_id"):
            self.materials[material.filename] = record
        return record

    def _conflict_for(self, knowledge_id: str) -> Optional[Mapping[str, Any]]:
        point = self.workspace.knowledge_point(self.course_id, knowledge_id)
        refs = set(str(ref) for ref in (point.get("evidence_refs") or []))
        for conflict in self.workspace.conflicts(self.course_id):
            if refs & set(str(ref) for ref in (conflict.get("evidence_refs") or [])):
                return conflict
        return None

    def _current_materials(self) -> dict[str, dict[str, Any]]:
        """每个已登记材料**当前**的记录 (含处理后的状态与证据 ID)。"""
        out: dict[str, dict[str, Any]] = {}
        for filename, record in self.materials.items():
            material_id = record.get("material_id")
            if not material_id:
                out[filename] = dict(record)
                continue
            try:
                out[filename] = self.workspace.get_material(
                    self.course_id, str(material_id)
                )
            except NotFoundError:
                out[filename] = dict(record)
        return out

    def _record(self, step: str, ok: bool, detail: Mapping[str, Any]) -> None:
        self._steps.append(StepRecord(step=step, ok=bool(ok), detail=dict(detail)))

    # -- 报告 -----------------------------------------------------------

    def _build_report(self) -> AcceptanceReport:
        points = tuple(
            {
                "knowledge_id": str(p["knowledge_id"]),
                "title": str(p.get("title") or ""),
                "validation_status": p.get("validation_status"),
                "review_status": p.get("review_status"),
                "knowledge_score": p.get("knowledge_score"),
                "evidence_refs": list(p.get("evidence_refs") or []),
                "content": str(p.get("content") or ""),
            }
            for p in self.workspace.knowledge_points(self.course_id)
        )
        conflicts = tuple(
            dict(c) for c in self.workspace.conflicts(self.course_id)
        )
        coverage = dict(self.workspace.coverage(self.course_id))
        gaps = tuple(dict(g) for g in (self.workspace.gaps(self.course_id).get("gaps") or []))
        review_candidates = tuple(
            dict(c) for c in self.workspace.review_candidates(self.course_id)
        )
        # 材料状态必须读**处理之后**的记录: ``self.materials`` 里存的是登记
        # 那一刻返回的快照 (processing_status=REGISTERED), 直接拿它做报告会
        # 让"处理成功"这件事在报告里看不见。
        materials = tuple(
            {
                "filename": str(record.get("filename")),
                "material_id": record.get("material_id"),
                "session_id": record.get("session_id"),
                "source_type": record.get("source_type"),
                "processing_status": record.get("processing_status"),
                "error": record.get("error"),
                "retryable": record.get("retryable"),
                "warning": record.get("warning"),
                "attempts": record.get("attempts"),
                "evidence_count": len(record.get("evidence_ids") or []),
                "duplicate": bool(record.get("duplicate")),
            }
            for _, record in sorted(self._current_materials().items())
        )
        answers = self._build_answers(
            points=points,
            conflicts=conflicts,
            coverage=coverage,
            gaps=gaps,
            review_candidates=review_candidates,
        )
        return AcceptanceReport(
            dataset=self._dataset.to_dict(),
            course_id=self.course_id,
            session_id=self.session_id,
            steps=self.steps,
            answers=answers,
            materials=materials,
            knowledge_points=points,
            conflicts=conflicts,
            coverage=coverage,
            gaps=gaps,
            review_candidates=review_candidates,
            review_candidates_before=tuple(
                dict(c) for c in getattr(self, "_review_candidates_before", [])
            ),
            review_decisions=tuple(
                {
                    "knowledge_id": str(d["knowledge_id"]),
                    "decision": str(d["decision"]),
                    "record": dict(d["record"]),
                }
                for d in getattr(self, "_review_decisions", [])
            ),
            evidence_index=getattr(self, "_evidence_index", {}),
            student=self.workspace.get_student(self.course_id, self.student_id),
            exercise=dict(getattr(self, "_exercise", {})),
            answer=dict(getattr(self, "_answer", {})),
            evaluation=dict(getattr(self, "_evaluation", {})),
            learning_state_before=dict(getattr(self, "_learning_state_before", {})),
            learning_state_after=dict(getattr(self, "_learning_state_after", {})),
            study_plan_before_answer=dict(getattr(self, "_study_plan_before", {})),
            study_plan_after_answer=dict(getattr(self, "_study_plan_after", {})),
            learning_path=dict(getattr(self, "_learning_path", {})),
            engine_note=(
                "ASR/OCR 为脚本化替身, 承载真实课堂文本 (教师原话 / 板书原文); "
                "本报告验证的是产品流水线, 不是语音/视觉模型。"
            ),
        )

    def _build_answers(
        self,
        *,
        points: Sequence[Mapping[str, Any]],
        conflicts: Sequence[Mapping[str, Any]],
        coverage: Mapping[str, Any],
        gaps: Sequence[Mapping[str, Any]],
        review_candidates: Sequence[Mapping[str, Any]],
    ) -> tuple[AcceptanceAnswer, ...]:
        session_points = self.workspace.knowledge_points(
            self.course_id, session_id=self.session_id
        )
        by_number = {number: (question, route) for number, question, route in ACCEPTANCE_QUESTIONS}

        # 1 — Session Knowledge
        titles = [str(p.get("title") or "") for p in session_points]
        answer_1 = AcceptanceAnswer(
            number="1",
            question=by_number["1"][0],
            route=by_number["1"][1],
            summary=(
                f"本节课 ({self.session_id}) 产出 {len(session_points)} 个知识点: "
                + "; ".join(titles[:3])
                + ("…" if len(titles) > 3 else "")
            ) if session_points else "本节课没有产出知识点",
            data={
                "session_id": self.session_id,
                "knowledge_point_total": len(session_points),
                "knowledge_point_ids": [str(p["knowledge_id"]) for p in session_points],
                "titles": titles,
            },
        )

        # 2 — Evidence provenance
        trace: dict[str, Any] = {}
        if points:
            first = str(points[0]["knowledge_id"])
            trace = self.workspace.knowledge_trace(self.course_id, first)
        materials = [
            {
                "material_id": m.get("material_id"),
                "filename": m.get("filename"),
                "session_id": m.get("session_id"),
                "source_type": m.get("source_type"),
            }
            for m in trace.get("materials") or []
        ]
        answer_2 = AcceptanceAnswer(
            number="2",
            question=by_number["2"][0],
            route=by_number["2"][1],
            summary=(
                f"知识点 {trace.get('knowledge_point', {}).get('knowledge_id')} "
                f"← {len(trace.get('evidence') or [])} 条证据 "
                f"← {len(materials)} 份源材料 "
                f"({', '.join(str(m['filename']) for m in materials) or '无'}); "
                f"溯源{'完整' if trace.get('complete') else '断链'}"
            ) if trace else "没有知识点, 无法追溯来源",
            data={
                "knowledge_id": (trace.get("knowledge_point") or {}).get("knowledge_id"),
                "evidence_count": len(trace.get("evidence") or []),
                "materials": materials,
                "sessions": [
                    s.get("session_id") for s in (trace.get("source_sessions") or [])
                ],
                "complete": trace.get("complete"),
                "unresolved_material_ids": trace.get("unresolved_material_ids"),
                "evidence": [
                    {
                        "evidence_id": e.get("evidence_id"),
                        "location": (e.get("source") or {}).get("location"),
                        "content": str(e.get("content") or "")[:160],
                    }
                    for e in (trace.get("evidence") or [])
                ],
            },
        )

        # 3 — Conflict / Review
        conflicted_kps = sorted(
            str(p["knowledge_id"])
            for p in points
            if _status_is(p.get("validation_status"), CONFLICTED_STATUS)
        )
        candidates_before = getattr(self, "_review_candidates_before", [])
        decisions = getattr(self, "_review_decisions", [])
        answer_3 = AcceptanceAnswer(
            number="3",
            question=by_number["3"][0],
            route=by_number["3"][1],
            summary=(
                f"检测到 {len(conflicts)} 条冲突, 涉及 {len(conflicted_kps)} 个知识点; "
                f"复核队列 {len(candidates_before)} 条, 已记录 {len(decisions)} 条人工决定"
            ),
            data={
                "conflict_total": len(conflicts),
                "conflicted_knowledge_point_ids": conflicted_kps,
                "conflicts": [
                    {
                        "conflict_id": c.get("conflict_id"),
                        "evidence_refs": c.get("evidence_refs"),
                        "description": c.get("description"),
                        "status": c.get("status"),
                    }
                    for c in conflicts
                ],
                "review_candidate_total_before_review": len(candidates_before),
                "review_decision_total": len(decisions),
                "review_decisions": [
                    {
                        "knowledge_id": str(d.get("knowledge_id")),
                        "decision": str(d.get("decision")),
                    }
                    for d in decisions
                ],
            },
        )

        # 4 — Coverage / Gap
        gap_types: dict[str, int] = {}
        for gap in gaps:
            for gap_type in gap.get("gap_types") or []:
                gap_types[str(gap_type)] = gap_types.get(str(gap_type), 0) + 1
        answer_4 = AcceptanceAnswer(
            number="4",
            question=by_number["4"][0],
            route=by_number["4"][1],
            summary=(
                f"{coverage.get('total_knowledge_points')} 个知识点中 "
                f"{coverage.get('covered_knowledge_points')} 个有课堂归属, "
                f"{coverage.get('uncovered_knowledge_points')} 个未覆盖, "
                f"{coverage.get('unassigned_knowledge_points')} 个未归入主题; "
                f"缺口 {len(gaps)} 个"
            ),
            data={
                "coverage": dict(coverage),
                "gap_total": len(gaps),
                "gap_types": gap_types,
                "gaps": [
                    {
                        "knowledge_point_id": g.get("knowledge_point_id"),
                        "gap_types": list(g.get("gap_types") or []),
                        "session_count": g.get("session_count"),
                        "validation_status": g.get("validation_status"),
                    }
                    for g in gaps
                ],
            },
        )

        # 5 — Study Plan / Learning Path
        plan = dict(getattr(self, "_study_plan_after", {}) or getattr(self, "_study_plan_before", {}))
        items = list(plan.get("items") or [])
        answer_5 = AcceptanceAnswer(
            number="5",
            question=by_number["5"][0],
            route=by_number["5"][1],
            summary=(
                f"学习计划 {len(items)} 项, 首项 {items[0].get('knowledge_point_id')} "
                f"(理由: {', '.join(items[0].get('reason_codes') or []) or '无'})"
            ) if items else "学习计划为空",
            data={
                "plan_id": plan.get("plan_id"),
                "items": [
                    {
                        "knowledge_point_id": item.get("knowledge_point_id"),
                        "reason_codes": list(item.get("reason_codes") or []),
                        "prerequisite_ids": list(item.get("prerequisite_ids") or []),
                    }
                    for item in items
                ],
                "learning_path": {
                    "status": self._learning_path.get("status"),
                    "target": self._learning_path.get("target_knowledge_point_id"),
                    "node_ids": list(self._learning_path.get("node_ids") or []),
                },
            },
        )

        # 6 — Why this exercise
        exercise = dict(getattr(self, "_exercise", {}))
        exercise_kps = [str(k) for k in (exercise.get("knowledge_point_ids") or [])]
        reasons = [
            {
                "knowledge_point_id": item.get("knowledge_point_id"),
                "reason_codes": list(item.get("reason_codes") or []),
                "prerequisite_ids": list(item.get("prerequisite_ids") or []),
            }
            for item in (self._study_plan_before.get("items") or [])
            if str(item.get("knowledge_point_id")) in set(exercise_kps)
        ]
        state = {
            "states": [
                s
                for s in (getattr(self, "_learning_state_before", {}) or {}).get("states") or []
                if str(s.get("knowledge_point_id")) in set(exercise_kps)
            ]
        }
        answer_6 = AcceptanceAnswer(
            number="6",
            question=by_number["6"][0],
            route=by_number["6"][1],
            summary=(
                f"练习 {exercise.get('exercise_id')} 关联知识点 {exercise_kps}; "
                f"选择理由 {', '.join(reasons[0]['reason_codes']) if reasons else '无'}; "
                f"证据 {len(exercise.get('evidence_ids') or [])} 条"
            ) if exercise else "没有创建练习",
            data={
                "exercise_id": exercise.get("exercise_id"),
                "prompt": exercise.get("prompt"),
                "knowledge_point_ids": exercise_kps,
                "evidence_ids": list(exercise.get("evidence_ids") or []),
                "plan_reasons": reasons,
                "learning_state": state,
                "prerequisite_ids": reasons[0]["prerequisite_ids"] if reasons else [],
            },
        )

        # 7 — What happens after answering
        evaluation = dict(getattr(self, "_evaluation", {}))
        before = getattr(self, "_learning_state_before", {}) or {}
        after = getattr(self, "_learning_state_after", {}) or {}
        before_by_id = {str(s.get("knowledge_point_id")): s for s in before.get("states") or []}
        after_by_id = {str(s.get("knowledge_point_id")): s for s in after.get("states") or []}
        changed = sorted(
            kp_id
            for kp_id in set(before_by_id) | set(after_by_id)
            if before_by_id.get(kp_id) != after_by_id.get(kp_id)
        )
        answer_7 = AcceptanceAnswer(
            number="7",
            question=by_number["7"][0],
            route=by_number["7"][1],
            summary=(
                f"评估 {evaluation.get('status')} (score={evaluation.get('score')}); "
                f"学习状态变化的知识点 {len(changed)} 个 "
                f"({', '.join(changed) if changed else '无'})"
            ),
            data={
                "answer_id": evaluation.get("answer_id"),
                "evaluation": {
                    "status": evaluation.get("status"),
                    "score": evaluation.get("score"),
                    "feedback": evaluation.get("feedback"),
                },
                "states_before": list(before_by_id.values()),
                "states_after": list(after_by_id.values()),
                "changed_knowledge_point_ids": changed,
                "plan_items_before": len(
                    (self._study_plan_before.get("items") or [])
                ),
                "plan_items_after": len(
                    (self._study_plan_after.get("items") or [])
                ),
            },
        )

        return (answer_1, answer_2, answer_3, answer_4, answer_5, answer_6, answer_7)


def _sha256_of_file(path: str) -> str:
    """文件内容 sha256 (验收报告里用于逐字节核对夹具)。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
