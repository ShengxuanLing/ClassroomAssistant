from __future__ import annotations
import copy
from dataclasses import dataclass, field
from typing import Any, Optional
from src.models import Course, ClassSession, Material, Evidence, KnowledgePoint

@dataclass
class CourseContext:
    courses: dict[str, Course] = field(default_factory=dict)
    sessions: dict[str, ClassSession] = field(default_factory=dict)
    _session_counter: dict[str, int] = field(default_factory=dict)

    def add_course(self, course: Course) -> bool:
        if course.course_id in self.courses:
            return False
        self.courses[course.course_id] = copy.deepcopy(course)
        return True

    def add_session(self, session: ClassSession) -> bool:
        if session.session_id in self.sessions:
            return False
        if session.course_id not in self.courses:
            return False
        self.sessions[session.session_id] = copy.deepcopy(session)
        return True

    def add_material_to_session(self, session_id: str, material: Material) -> bool:
        session = self.sessions.get(session_id)
        if session is None:
            return False
        if material.material_id in session.material_refs:
            return False
        session.material_refs.append(material.material_id)
        return True

    def add_evidence_to_session(self, session_id: str, evidence: Evidence) -> bool:
        session = self.sessions.get(session_id)
        if session is None:
            return False
        if evidence.evidence_id in session.evidence_refs:
            return False
        session.evidence_refs.append(evidence.evidence_id)
        return True

    def add_knowledge_point_to_session(self, session_id: str, kp: KnowledgePoint) -> bool:
        session = self.sessions.get(session_id)
        if session is None:
            return False
        if kp.knowledge_id in session.knowledge_point_refs:
            return False
        session.knowledge_point_refs.append(kp.knowledge_id)
        return True

    def get_session_materials(self, session_id: str) -> list[str]:
        session = self.sessions.get(session_id)
        if session is None:
            return []
        return list(session.material_refs)

    def get_session_evidence(self, session_id: str) -> list[str]:
        session = self.sessions.get(session_id)
        if session is None:
            return []
        return list(session.evidence_refs)

    def get_session_knowledge_points(self, session_id: str) -> list[str]:
        session = self.sessions.get(session_id)
        if session is None:
            return []
        return list(session.knowledge_point_refs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "courses": {k: v.to_dict() for k, v in self.courses.items()},
            "sessions": {k: v.to_dict() for k, v in self.sessions.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CourseContext:
        ctx = cls()
        for course_id, course_data in data.get("courses", {}).items():
            course = Course.from_dict(course_data)
            ctx.courses[course_id] = course
        for session_id, session_data in data.get("sessions", {}).items():
            session = ClassSession.from_dict(session_data)
            ctx.sessions[session_id] = session
        return ctx

    def get_course(self, course_id: str) -> Optional[Course]:
        return self.courses.get(course_id)

    def get_session(self, session_id: str) -> Optional[ClassSession]:
        return self.sessions.get(session_id)

    def get_sessions_by_course(self, course_id: str) -> list[ClassSession]:
        return [s for s in self.sessions.values() if s.course_id == course_id]
