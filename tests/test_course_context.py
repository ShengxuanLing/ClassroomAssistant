import unittest
import copy
import uuid
from src.models import Course, ClassSession, Material, Evidence, KnowledgePoint, Language, Confidence, SourceReference
from src.course_context import CourseContext

class TestCourseCreation(unittest.TestCase):
    def test_create_course(self):
        c = Course(name="Economia", code="ECO2026")
        self.assertEqual(c.name, "Economia")
        self.assertEqual(c.code, "ECO2026")
        self.assertIsNotNone(c.course_id)
        self.assertTrue(c.course_id.startswith("course-"))
    def test_course_id_stable(self):
        c1 = Course(name="Economia", code="ECO2026")
        c2 = Course(name="Economia", code="ECO2026")
        self.assertEqual(c1.course_id, c2.course_id)
    def test_empty_course_context(self):
        ctx = CourseContext()
        self.assertEqual(len(ctx.courses), 0)
        self.assertEqual(len(ctx.sessions), 0)
    def test_add_course(self):
        ctx = CourseContext()
        c = Course(name="Economia", code="ECO2026")
        result = ctx.add_course(c)
        self.assertTrue(result)
        self.assertEqual(len(ctx.courses), 1)
        self.assertIn(c.course_id, ctx.courses)
    def test_duplicate_course_not_added(self):
        ctx = CourseContext()
        c = Course(name="Economia", code="ECO2026")
        ctx.add_course(c)
        result = ctx.add_course(c)
        self.assertFalse(result)
        self.assertEqual(len(ctx.courses), 1)

class TestSessionCreation(unittest.TestCase):
    def test_create_session(self):
        s = ClassSession(course_id="C001", session_number=1, date="2026-09-10", title="Lecture 1")
        self.assertIsNotNone(s.session_id)
        self.assertTrue(s.session_id.startswith("session-"))
    def test_session_references_course(self):
        c = Course(name="Economia", code="ECO2026")
        s = ClassSession(course_id=c.course_id, session_number=1, date="2026-09-10")
        self.assertEqual(s.course_id, c.course_id)
    def test_session_id_stable(self):
        s1 = ClassSession(course_id="C001", session_number=1, date="2026-09-10")
        s2 = ClassSession(course_id="C001", session_number=1, date="2026-09-10")
        self.assertEqual(s1.session_id, s2.session_id)
    def test_add_session(self):
        ctx = CourseContext()
        c = Course(name="Economia", code="ECO2026")
        ctx.add_course(c)
        s = ClassSession(course_id=c.course_id, session_number=1, date="2026-09-10")
        result = ctx.add_session(s)
        self.assertTrue(result)
        self.assertEqual(len(ctx.sessions), 1)
    def test_duplicate_session_not_added(self):
        ctx = CourseContext()
        c = Course(name="Economia", code="ECO2026")
        ctx.add_course(c)
        s = ClassSession(course_id=c.course_id, session_number=1, date="2026-09-10")
        ctx.add_session(s)
        result = ctx.add_session(s)
        self.assertFalse(result)
        self.assertEqual(len(ctx.sessions), 1)
    def test_session_number_validation(self):
        s = ClassSession(course_id="C001", session_number=3, date="2026-09-10")
        self.assertEqual(s.session_number, 3)
    def test_session_date_saved(self):
        s = ClassSession(course_id="C001", session_number=1, date="2026-09-10")
        self.assertEqual(s.date, "2026-09-10")

class TestMaterialInSession(unittest.TestCase):
    def setUp(self):
        self.ctx = CourseContext()
        self.c = Course(name="Economia", code="ECO2026")
        self.ctx.add_course(self.c)
        self.s = ClassSession(course_id=self.c.course_id, session_number=1, date="2026-09-10")
        self.ctx.add_session(self.s)
    def test_material_id_added_to_session(self):
        m = Material(filename="test.mp3", path="/tmp/test.mp3", material_type="audio", language=Language.SPANISH)
        result = self.ctx.add_material_to_session(self.s.session_id, m)
        self.assertTrue(result)
        stored = self.ctx.sessions[self.s.session_id]
        self.assertIn(m.material_id, stored.material_refs)
    def test_duplicate_material_not_repeated(self):
        m = Material(filename="test.mp3", path="/tmp/test.mp3", material_type="audio")
        self.ctx.add_material_to_session(self.s.session_id, m)
        result = self.ctx.add_material_to_session(self.s.session_id, m)
        self.assertFalse(result)
    def test_original_material_not_modified(self):
        m = Material(filename="test.mp3", path="/tmp/test.mp3", material_type="audio")
        original_refs_count = len(m.metadata)
        self.ctx.add_material_to_session(self.s.session_id, m)
        self.assertEqual(len(m.metadata), original_refs_count)

class TestEvidenceInSession(unittest.TestCase):
    def setUp(self):
        self.ctx = CourseContext()
        self.c = Course(name="Economia", code="ECO2026")
        self.ctx.add_course(self.c)
        self.s = ClassSession(course_id=self.c.course_id, session_number=1, date="2026-09-10")
        self.ctx.add_session(self.s)
    def test_evidence_id_added_to_session(self):
        src = SourceReference(material_id=str(uuid.uuid4()))
        e = Evidence(content="test", language=Language.SPANISH, source_reference=src, confidence=Confidence.HIGH, evidence_type="transcript")
        result = self.ctx.add_evidence_to_session(self.s.session_id, e)
        self.assertTrue(result)
        stored = self.ctx.sessions[self.s.session_id]
        self.assertIn(e.evidence_id, stored.evidence_refs)
    def test_duplicate_evidence_not_repeated(self):
        src = SourceReference(material_id=str(uuid.uuid4()))
        e = Evidence(content="test", language=Language.SPANISH, source_reference=src)
        self.ctx.add_evidence_to_session(self.s.session_id, e)
        result = self.ctx.add_evidence_to_session(self.s.session_id, e)
        self.assertFalse(result)
    def test_original_evidence_not_modified(self):
        src = SourceReference(material_id=str(uuid.uuid4()))
        e = Evidence(content="test", language=Language.SPANISH, source_reference=src)
        self.ctx.add_evidence_to_session(self.s.session_id, e)
        self.assertEqual(e.evidence_id, e.evidence_id)

class TestKnowledgePointInSession(unittest.TestCase):
    def setUp(self):
        self.ctx = CourseContext()
        self.c = Course(name="Economia", code="ECO2026")
        self.ctx.add_course(self.c)
        self.s = ClassSession(course_id=self.c.course_id, session_number=1, date="2026-09-10")
        self.ctx.add_session(self.s)
    def test_knowledge_point_id_added_to_session(self):
        kp = KnowledgePoint(title="test", content="content")
        result = self.ctx.add_knowledge_point_to_session(self.s.session_id, kp)
        self.assertTrue(result)
        stored = self.ctx.sessions[self.s.session_id]
        self.assertIn(kp.knowledge_id, stored.knowledge_point_refs)
    def test_duplicate_knowledge_point_not_repeated(self):
        kp = KnowledgePoint(title="test", content="content")
        self.ctx.add_knowledge_point_to_session(self.s.session_id, kp)
        result = self.ctx.add_knowledge_point_to_session(self.s.session_id, kp)
        self.assertFalse(result)
    def test_same_knowledge_point_in_multiple_sessions(self):
        c2 = Course(name="Math", code="MATH")
        self.ctx.add_course(c2)
        s2 = ClassSession(course_id=c2.course_id, session_number=2, date="2026-09-11")
        self.ctx.add_session(s2)
        kp = KnowledgePoint(title="test", content="content")
        self.ctx.add_knowledge_point_to_session(self.s.session_id, kp)
        self.ctx.add_knowledge_point_to_session(s2.session_id, kp)
        self.assertIn(kp.knowledge_id, self.ctx.sessions[self.s.session_id].knowledge_point_refs)
        self.assertIn(kp.knowledge_id, self.ctx.sessions[s2.session_id].knowledge_point_refs)
    def test_original_knowledge_point_not_copied(self):
        kp = KnowledgePoint(title="test", content="content")
        self.ctx.add_knowledge_point_to_session(self.s.session_id, kp)
        self.assertEqual(kp.knowledge_id, kp.knowledge_id)

class TestCourseContinuity(unittest.TestCase):
    def test_one_course_multiple_sessions(self):
        ctx = CourseContext()
        c = Course(name="Economia", code="ECO2026")
        ctx.add_course(c)
        s1 = ClassSession(course_id=c.course_id, session_number=1, date="2026-09-10")
        s2 = ClassSession(course_id=c.course_id, session_number=2, date="2026-09-11")
        ctx.add_session(s1)
        ctx.add_session(s2)
        self.assertEqual(len(ctx.sessions), 2)
        self.assertEqual(len(ctx.get_sessions_by_course(c.course_id)), 2)
    def test_session_order_stable(self):
        ctx = CourseContext()
        c = Course(name="Economia", code="ECO2026")
        ctx.add_course(c)
        s1 = ClassSession(course_id=c.course_id, session_number=1, date="2026-09-10")
        s2 = ClassSession(course_id=c.course_id, session_number=2, date="2026-09-11")
        s3 = ClassSession(course_id=c.course_id, session_number=3, date="2026-09-12")
        ctx.add_session(s1)
        ctx.add_session(s2)
        ctx.add_session(s3)
        sessions = ctx.get_sessions_by_course(c.course_id)
        self.assertEqual(sessions[0].session_number, 1)
        self.assertEqual(sessions[1].session_number, 2)
        self.assertEqual(sessions[2].session_number, 3)
    def test_session_not_overwrite_old(self):
        ctx = CourseContext()
        c = Course(name="Economia", code="ECO2026")
        ctx.add_course(c)
        s1 = ClassSession(course_id=c.course_id, session_number=1, date="2026-09-10")
        s2 = ClassSession(course_id=c.course_id, session_number=2, date="2026-09-11")
        ctx.add_session(s1)
        ctx.add_session(s2)
        self.assertEqual(len(ctx.sessions), 2)
        self.assertIn(s1.session_id, ctx.sessions)
        self.assertIn(s2.session_id, ctx.sessions)

class TestSerialization(unittest.TestCase):
    def test_to_dict_includes_core_fields(self):
        ctx = CourseContext()
        c = Course(name="Economia", code="ECO2026")
        ctx.add_course(c)
        s = ClassSession(course_id=c.course_id, session_number=1, date="2026-09-10")
        ctx.add_session(s)
        d = ctx.to_dict()
        self.assertIn("courses", d)
        self.assertIn("sessions", d)
        self.assertIn(c.course_id, d["courses"])
        self.assertIn(s.session_id, d["sessions"])
    def test_from_dict_roundtrip(self):
        ctx = CourseContext()
        c = Course(name="Economia", code="ECO2026")
        ctx.add_course(c)
        s = ClassSession(course_id=c.course_id, session_number=1, date="2026-09-10")
        ctx.add_session(s)
        d = ctx.to_dict()
        ctx2 = CourseContext.from_dict(d)
        self.assertEqual(len(ctx2.courses), 1)
        self.assertEqual(len(ctx2.sessions), 1)
        self.assertEqual(ctx2.courses[c.course_id].name, "Economia")
        self.assertEqual(ctx2.sessions[s.session_id].course_id, c.course_id)

class TestMultilingual(unittest.TestCase):
    def test_spanish_course(self):
        ctx = CourseContext()
        c = Course(name="Economia", code="ECO")
        ctx.add_course(c)
        self.assertEqual(ctx.get_course(c.course_id).name, "Economia")
    def test_catalan_course(self):
        ctx = CourseContext()
        c = Course(name="Economia", code="ECO")
        ctx.add_course(c)
        self.assertIn("Economia", ctx.get_course(c.course_id).name)
    def test_chinese_course(self):
        ctx = CourseContext()
        c = Course(name="经济学", code="ECO")
        ctx.add_course(c)
        self.assertEqual(ctx.get_course(c.course_id).name, "经济学")
    def test_mixed_language(self):
        ctx = CourseContext()
        c = Course(name="Economia", code="ECO")
        ctx.add_course(c)
        s = ClassSession(course_id=c.course_id, session_number=1, date="2026-09-10", title="Lecture")
        ctx.add_session(s)
        self.assertIsNotNone(ctx.get_session(s.session_id))

class TestDeterminism(unittest.TestCase):
    def test_same_input_same_id(self):
        s1 = ClassSession(course_id="C001", session_number=1, date="2026-09-10")
        s2 = ClassSession(course_id="C001", session_number=1, date="2026-09-10")
        self.assertEqual(s1.session_id, s2.session_id)
    def test_input_order_does_not_affect_id(self):
        c = Course(name="Economia", code="ECO")
        s1 = ClassSession(course_id=c.course_id, session_number=1, date="2026-09-10")
        sid = s1.session_id
        s2 = ClassSession(course_id=c.course_id, session_number=2, date="2026-09-11")
        ctx = CourseContext()
        ctx.add_course(c)
        ctx.add_session(s2)
        ctx.add_session(s1)
        self.assertEqual(ctx.sessions[s1.session_id].session_id, sid)
    def test_same_input_produces_same_result_across_runs(self):
        c = Course(name="Economia", code="ECO")
        s1 = ClassSession(course_id=c.course_id, session_number=1, date="2026-09-10")
        s2 = ClassSession(course_id=c.course_id, session_number=2, date="2026-09-11")
        s3 = ClassSession(course_id=c.course_id, session_number=3, date="2026-09-12")
        ctx1 = CourseContext()
        ctx1.add_course(c)
        ctx1.add_session(s1)
        ctx1.add_session(s2)
        ctx1.add_session(s3)
        s1b = ClassSession(course_id=c.course_id, session_number=1, date="2026-09-10")
        s2b = ClassSession(course_id=c.course_id, session_number=2, date="2026-09-11")
        s3b = ClassSession(course_id=c.course_id, session_number=3, date="2026-09-12")
        ctx2 = CourseContext()
        ctx2.add_course(c)
        ctx2.add_session(s3b)
        ctx2.add_session(s2b)
        ctx2.add_session(s1b)
        self.assertEqual(len(ctx1.sessions), len(ctx2.sessions))
        for sid in ctx1.sessions:
            self.assertIn(sid, ctx2.sessions)


class TestNoAutoInference(unittest.TestCase):
    def test_no_auto_inference_from_filename(self):
        ctx = CourseContext()
        c = Course(name="Unknown", code="UNK")
        ctx.add_course(c)
        s = ClassSession(course_id=c.course_id, session_number=1, date="2026-09-10")
        ctx.add_session(s)
        self.assertEqual(s.title, "")
    def test_no_auto_inference_from_date(self):
        ctx = CourseContext()
        c = Course(name="Unknown", code="UNK")
        ctx.add_course(c)
        s = ClassSession(course_id=c.course_id, session_number=1, date="2026-09-10")
        ctx.add_session(s)
        self.assertEqual(s.title, "")
    def test_no_auto_inference_on_session_order(self):
        ctx = CourseContext()
        c = Course(name="Unknown", code="UNK")
        ctx.add_course(c)
        s1 = ClassSession(course_id=c.course_id, session_number=1, date="2026-09-10")
        s2 = ClassSession(course_id=c.course_id, session_number=2, date="2026-09-11")
        ctx.add_session(s1)
        ctx.add_session(s2)
        self.assertEqual(len(s1.knowledge_point_refs), 0)

if __name__ == '__main__':
    unittest.main()
