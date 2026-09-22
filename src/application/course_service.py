# -*- coding: utf-8 -*-
"""课程 / 课堂会话 / 材料 服务 (Task 34)。

所有业务 ID 必须稳定: Course / ClassSession / Material 的 ID 由
domain 层通过 SHA-256 确定性生成, 应用层禁止引入 uuid4 / datetime.now。

重复调用 create_course / create_session / register_material 必须幂等
(同一输入返回同一个对象, 不产生副作用)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.models import (
    ClassSession,
    Course,
    Language,
    Material,
    MaterialType,
)

from src.application.dto import course_to_dict, material_to_dict, session_to_dict
from src.application.errors import (
    ApplicationError,
    ConflictError,
    StorageError,
    InvalidInputError,
    NotFoundError,
    map_application_error,
)

__all__ = ["CourseService"]


def _require_nonempty_str(value: Any, field_name: str, *, allow_none: bool = False) -> Optional[str]:
    """校验必填 / 可选字符串字段, 返回 strip 后的值或 None。"""
    if value is None:
        if not allow_none:
            raise InvalidInputError(f"{field_name} must be a non-empty string")
        return None
    if not isinstance(value, str):
        raise InvalidInputError(f"{field_name} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not allow_none and not stripped:
        raise InvalidInputError(f"{field_name} must be a non-empty string")
    return stripped or None


_LANG_ALIASES: Dict[str, str] = {
    "es": "Spanish",
    "spanish": "Spanish",
    "es-es": "Spanish",
    "ca": "Catalan",
    "catalan": "Catalan",
    "ca-valencia": "Catalan",
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "chinese": "Chinese",
    "en": "English",
    "english": "English",
}


def _resolve_language(value: Optional[str]) -> Language:
    """把常见语言代码 / 名称解析为 domain Language (未知 -> UNKNOWN)。

    domain 层 Language.from_string 只识别显示名 (Spanish/Catalan/...),
    这里补上 ISO 639-1 代码 (es/ca/zh/en) 的别名映射 (AGENTS.md 规范)。
    """
    if value is None:
        return Language.UNKNOWN
    if not isinstance(value, str):
        raise InvalidInputError(
            f"language must be a string or None, got {type(value).__name__}"
        )
    normalized = value.strip().lower()
    if not normalized:
        return Language.UNKNOWN
    canonical = _LANG_ALIASES.get(normalized, value.strip())
    lang = Language.from_string(canonical)
    if lang is Language.UNKNOWN:
        # from_string 对未知字符串回退 UNKNOWN, 视为合法但标记 unknown
        pass
    return lang
#: 课程名语言识别词表: 加泰罗尼亚语与西班牙语共享大量词汇,
#: 靠这些课程常见的专有词组区分 (大小写不敏感)。词表命中优先,
#: 否则标记 UNKNOWN —— 不猜测。
_CATALAN_MARKERS = (
    "per a la", "digitalització", "microcontroladors",
    "la geoinformació", "programació d'aplicacions",
)
_SPANISH_MARKERS = (
    "gestió ambiental",
    "gestió de projectes",
)


def _detect_course_language(name: str) -> Optional[Language]:
    """根据课程名识别语言; 识别不了返回 None (由调用方决定回退)。"""
    text = name.lower()
    for marker in _CATALAN_MARKERS:
        if marker in text:
            return Language.CATALAN
    for marker in _SPANISH_MARKERS:
        if marker in text:
            return Language.SPANISH
    return None


class CourseService:
    """课程 / 课堂 / 材料 的查询与创建入口。

    状态保存在内存 dict 中; 持久化由上层 (Task 42) 注入。
    每次操作都是幂等的: 相同的 (name, code) 只创建一个 Course,
    相同的 (course_id, session_number) 只创建一个 ClassSession,
    相同的 (material_id) 只登记一次。
    """

    def __init__(self) -> None:
        self._courses: Dict[str, Course] = {}
        self._sessions: Dict[str, ClassSession] = {}
        self._materials: Dict[str, Material] = {}

    # ------------------------------------------------------------------
    # Course
    # ------------------------------------------------------------------

    def create_course(
        self,
        name: str,
        code: str = "",
        language: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """创建 / 获取一个 Course (幂等)。

        重复调用相同 (name, code) 返回同一个 Course DTO。
        返回 DTO dict (不暴露 domain 对象)。
        """
        normalized_name = _require_nonempty_str(name, "name")
        normalized_code = _require_nonempty_str(code, "code", allow_none=True) or ""
        if language is None:
            lang = _detect_course_language(normalized_name) or Language.UNKNOWN
        else:
            lang = _resolve_language(language)
        meta = dict(metadata) if metadata else {}

        # 按 (name, code) 概念键去重: 相同键的不同 language / metadata
        # 视为冲突 (domain 稳定 ID 不含 language, 同键只能有一个语言版本)。
        existing = self._find_course_by_key(normalized_name, normalized_code)
        if existing is not None:
            if (
                existing.language.value != lang.value
                or existing.metadata != meta
            ):
                raise ConflictError(
                    f"course with key ({normalized_name!r}, {normalized_code!r}) "
                    f"already exists with different fields"
                )
            return course_to_dict(existing)

        course = Course(
            name=normalized_name,
            code=normalized_code,
            language=lang,
            metadata=meta,
        )
        # domain 层按 name+code 派生稳定 ID; 概念键已保证同键同语言,
        # 直接按 course.course_id 登记。
        self._courses[course.course_id] = course
        return course_to_dict(course)

    def get_course(self, course_id: str) -> dict[str, Any]:
        """获取单个 Course DTO; 不存在则 NotFoundError。"""
        cid = _require_nonempty_str(course_id, "course_id")
        course = self._courses.get(cid)
        if course is None:
            raise NotFoundError(f"course {cid!r} not found")
        return course_to_dict(course)

    def list_courses(self) -> List[dict[str, Any]]:
        """返回全部 Course DTO, 按 course_id 升序 (确定性)。"""
        return [course_to_dict(c) for _, c in sorted(self._courses.items())]

    def update_course(
        self,
        course_id: str,
        *,
        name: Optional[str] = None,
        code: Optional[str] = None,
        language: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        replace_metadata: bool = False,
    ) -> dict[str, Any]:
        """更新 Course 字段; 未提供的字段保持不变。

        返回更新后的 DTO。"""
        cid = _require_nonempty_str(course_id, "course_id")
        course = self._courses.get(cid)
        if course is None:
            raise NotFoundError(f"course {cid!r} not found")

        new_name = _require_nonempty_str(name, "name") if name is not None else None
        new_code = _require_nonempty_str(code, "code") if code is not None else None

        if new_name is not None:
            course.name = new_name
        if new_code is not None:
            course.code = new_code
        if language is not None:
            course.language = _resolve_language(language)
        if metadata is not None:
            if replace_metadata:
                course.metadata = dict(metadata)
            else:
                course.metadata = {**(course.metadata or {}), **dict(metadata)}

        return course_to_dict(course)

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def create_session(
        self,
        course_id: str,
        session_number: int = 0,
        date: str = "",
        title: str = "",
        material_refs: Optional[Sequence[str]] = None,
        evidence_refs: Optional[Sequence[str]] = None,
        knowledge_point_refs: Optional[Sequence[str]] = None,
        verification_refs: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        """创建 / 获取一个 ClassSession (幂等)。

        重复调用相同 (course_id, session_number) 返回同一个 Session DTO。
        """
        cid = _require_nonempty_str(course_id, "course_id")
        if cid not in self._courses:
            raise NotFoundError(f"course {cid!r} not found")

        session = ClassSession(
            course_id=cid,
            session_number=session_number,
            date=date,
            title=title,
            material_refs=list(material_refs or []),
            evidence_refs=list(evidence_refs or []),
            knowledge_point_refs=list(knowledge_point_refs or []),
            verification_refs=list(verification_refs or []),
        )

        existing = self._sessions.get(session.session_id)
        if existing is not None:
            return session_to_dict(existing)
        self._sessions[session.session_id] = session
        return session_to_dict(session)

    def get_session(self, session_id: str) -> dict[str, Any]:
        """获取单个 ClassSession DTO; 不存在则 NotFoundError。"""
        sid = _require_nonempty_str(session_id, "session_id")
        session = self._sessions.get(sid)
        if session is None:
            raise NotFoundError(f"session {sid!r} not found")
        return session_to_dict(session)

    def list_sessions(self, course_id: Optional[str] = None) -> List[dict[str, Any]]:
        """返回指定课程 (或全部) 的 Session DTO。

        排序键是 ``(course_id, session_number, session_id)`` —— 与持久化层
        声明的排序 (``tables.py`` 里 sessions 的 ``order_by``) **完全一致**。

        为什么必须一致: 如果服务层按 id 哈希排、存储层按课号排, 那么
        "从数据库读回来"与"从内存读出来"就会给出两种顺序, UI 在重启前后
        看到的课程顺序会莫名变化。末尾带 ``session_id`` 是为了消除并列,
        保证全序 (没有全序就不是确定性排序)。
        """
        items = list(self._sessions.values())
        if course_id is not None:
            cid = _require_nonempty_str(course_id, "course_id")
            if cid not in self._courses:
                raise NotFoundError(f"course {cid!r} not found")
            items = [s for s in items if s.course_id == cid]
        return [
            session_to_dict(s)
            for s in sorted(
                items,
                key=lambda s: (s.course_id, int(s.session_number), s.session_id),
            )
        ]

    # ------------------------------------------------------------------
    # Material
    # ------------------------------------------------------------------

    def register_material(
        self,
        material: Mapping[str, Any],
    ) -> dict[str, Any]:
        """登记一个 Material (幂等: 相同 material_id 不重复登记)。

        接受 dict 形式的 material (来自 UI 层), 内部转换为 domain Material。
        必须包含 material_id 和 filename。
        """
        if not isinstance(material, Mapping):
            raise InvalidInputError(f"material must be a mapping, got {type(material).__name__}")
        mid = _require_nonempty_str(material.get("material_id"), "material_id")
        if mid is None:
            raise InvalidInputError("material_id is required")
        filename = _require_nonempty_str(material.get("filename"), "filename")

        mt_raw = material.get("material_type")
        lang_raw = material.get("language")
        created_at = material.get("created_at")
        meta = dict(material.get("metadata") or {})

        existing = self._materials.get(mid)
        if existing is not None:
            # 幂等: 相同 ID 直接返回, 不覆盖
            return material_to_dict(existing)

        new_material = Material(
            material_id=mid,
            filename=filename,
            path=str(material.get("path", "")),
            material_type=MaterialType.from_string(mt_raw) if mt_raw else MaterialType.TEXT,
            language=_resolve_language(lang_raw) if lang_raw else Language.UNKNOWN,
            created_at=created_at,
            metadata=meta,
        )
        self._materials[mid] = new_material
        return material_to_dict(new_material)

    def get_material(self, material_id: str) -> dict[str, Any]:
        """获取单个 Material DTO; 不存在则 NotFoundError。"""
        mid = _require_nonempty_str(material_id, "material_id")
        mat = self._materials.get(mid)
        if mat is None:
            raise NotFoundError(f"material {mid!r} not found")
        return material_to_dict(mat)

    def list_materials(
        self,
        course_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[dict[str, Any]]:
        """返回 Material DTO 列表, 按 material_id 升序。

        可通过 course_id / session_id 过滤 (依赖 metadata 中的字段)。
        """
        items = list(self._materials.values())
        if course_id is not None:
            items = [
                m for m in items
                if (m.metadata or {}).get("course_id") == course_id
            ]
        if session_id is not None:
            items = [
                m for m in items
                if (m.metadata or {}).get("session_id") == session_id
            ]
        return [material_to_dict(m) for m in sorted(items, key=lambda m: m.material_id)]

    def _find_course_by_key(
        self, name: str, code: str
    ) -> Optional[Course]:
        """按 (name, code) 概念键查找课程 (domain ID 不含 language)。"""
        for course in self._courses.values():
            if course.name == name and course.code == code:
                return course
        return None

    # ------------------------------------------------------------------
    # 持久化恢复 (Task 48/49)
    # ------------------------------------------------------------------

    def load_state(
        self,
        *,
        courses: Sequence[Course] = (),
        sessions: Sequence[ClassSession] = (),
    ) -> dict[str, int]:
        """从 SQLite 恢复课程 / 课堂注册表 (**幂等**, 返回恢复条数)。

        这是"重启后课程还在"的唯一入口。恢复的对象就是领域对象本身
        (由领域层 ``from_dict`` 重建), 不是"看起来一样"的替身。

        课堂的 ``course_id`` 必须指向一个真实存在的课程 —— 悬空课堂是
        数据损坏, 必须**报错**而不是静默丢弃或静默生成课程。
        """
        restored_courses = 0
        for course in courses:
            if course is None or not course.course_id:
                raise StorageError("persisted course has an empty course_id")
            self._courses[course.course_id] = course
            restored_courses += 1

        restored_sessions = 0
        for session in sessions:
            if session is None or not session.session_id:
                raise StorageError("persisted session has an empty session_id")
            if session.course_id not in self._courses:
                raise StorageError(
                    f"session {session.session_id!r} references unknown course "
                    f"{session.course_id!r}"
                )
            self._sessions[session.session_id] = session
            restored_sessions += 1
        return {"courses": restored_courses, "sessions": restored_sessions}

    # ------------------------------------------------------------------
    # 内部状态 (供测试 / 持久化层使用)
    # ------------------------------------------------------------------

    def _all_courses(self) -> Dict[str, Course]:
        return self._courses

    def _all_sessions(self) -> Dict[str, ClassSession]:
        return self._sessions

    def _all_materials(self) -> Dict[str, Material]:
        return self._materials
