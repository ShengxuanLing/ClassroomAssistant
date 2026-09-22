"""Tests for the Note Parser module (Task 4)."""

import os
import tempfile
import unittest
from pathlib import Path

from src.models import (
    Confidence, Evidence, EvidenceType, Language, Material, SourceReference
)
from src.note_parser import NoteParser


class TestNoteParserTXT(unittest.TestCase):
    """Tests for TXT parsing."""

    def setUp(self):
        self.content = "Line 1\nLine 2\nLine 3\n"
        self.tmpdir = tempfile.mkdtemp()
        self.txt_path = os.path.join(self.tmpdir, "test.txt")
        with open(self.txt_path, "w", encoding="utf-8") as f:
            f.write(self.content)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_txt_parsing(self):
        material = Material(
            filename="test.txt", path=self.txt_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        self.assertGreater(len(evidences), 0)

    def test_txt_content_preserved(self):
        material = Material(
            filename="test.txt", path=self.txt_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        all_content = "\n".join(e.content for e in evidences)
        self.assertIn("Line 1", all_content)
        self.assertIn("Line 2", all_content)
        self.assertIn("Line 3", all_content)

    def test_txt_empty_file(self):
        empty_path = os.path.join(self.tmpdir, "empty.txt")
        with open(empty_path, "w", encoding="utf-8") as f:
            f.write("")
        material = Material(
            filename="empty.txt", path=empty_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        self.assertEqual(evidences, [])

    def test_txt_whitespace_only(self):
        ws_path = os.path.join(self.tmpdir, "ws.txt")
        with open(ws_path, "w", encoding="utf-8") as f:
            f.write("   \n\n  \n")
        material = Material(
            filename="ws.txt", path=ws_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        self.assertEqual(evidences, [])

    def test_txt_source_reference(self):
        material = Material(
            filename="test.txt", path=self.txt_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        for ev in evidences:
            self.assertIsNotNone(ev.source_reference)
            self.assertEqual(ev.source_reference.material_id, material.material_id)
            self.assertIsNotNone(ev.source_reference.location)

    def test_txt_evidence_type(self):
        material = Material(
            filename="test.txt", path=self.txt_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        for ev in evidences:
            self.assertEqual(ev.evidence_type, EvidenceType.PERSONAL_NOTE)


class TestNoteParserMarkdown(unittest.TestCase):
    """Tests for Markdown parsing."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_markdown_headings(self):
        md_content = "## Tema 1\n\nLa fotosíntesis es...\n"
        md_path = os.path.join(self.tmpdir, "test.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        material = Material(
            filename="test.md", path=md_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        self.assertGreater(len(evidences), 0)
        headings = [e for e in evidences if e.source_reference.paragraph and "heading" in e.source_reference.paragraph]
        self.assertGreater(len(headings), 0)

    def test_markdown_paragraphs(self):
        md_content = "## Tema 1\n\nLa fotosíntesis es un proceso.\n"
        md_path = os.path.join(self.tmpdir, "test.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        material = Material(
            filename="test.md", path=md_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        paragraphs = [e for e in evidences if e.source_reference.paragraph == "paragraph"]
        self.assertGreater(len(paragraphs), 0)

    def test_markdown_bullet_lists(self):
        md_content = "## Tema\n\n- item 1\n- item 2\n"
        md_path = os.path.join(self.tmpdir, "test.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        material = Material(
            filename="test.md", path=md_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        bullets = [e for e in evidences if e.source_reference.paragraph == "bullet_list"]
        self.assertGreater(len(bullets), 0)

    def test_markdown_numbered_lists(self):
        md_content = "## Tema\n\n1. item 1\n2. item 2\n"
        md_path = os.path.join(self.tmpdir, "test.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        material = Material(
            filename="test.md", path=md_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        numbered = [e for e in evidences if e.source_reference.paragraph == "numbered_list"]
        self.assertGreater(len(numbered), 0)

    def test_markdown_content_not_lost(self):
        md_content = "## Tema 1\n\nLa fotosíntesis es...\n\n- fase luminosa\n- fase oscura\n"
        md_path = os.path.join(self.tmpdir, "test.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        material = Material(
            filename="test.md", path=md_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        all_content = "\n".join(e.content for e in evidences)
        self.assertIn("fotosíntesis", all_content)
        self.assertIn("fase luminosa", all_content)
        self.assertIn("fase oscura", all_content)


class TestNoteParserUnicode(unittest.TestCase):
    """Tests for Unicode character handling."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_chinese_characters(self):
        content = "光合作用是植物将光能转化为化学能的过程。\n"
        md_path = os.path.join(self.tmpdir, "chinese.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)
        material = Material(
            filename="chinese.md", path=md_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        self.assertGreater(len(evidences), 0)
        self.assertEqual(evidences[0].language, Language.CHINESE)

    def test_spanish_characters(self):
        content = "La fotosíntesis es un proceso biológico.\n"
        md_path = os.path.join(self.tmpdir, "spanish.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)
        material = Material(
            filename="spanish.md", path=md_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        self.assertGreater(len(evidences), 0)
        self.assertEqual(evidences[0].language, Language.SPANISH)

    def test_catalan_characters(self):
        content = "La fotosíntesi és un procés biològic.\n"
        md_path = os.path.join(self.tmpdir, "catalan.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)
        material = Material(
            filename="catalan.md", path=md_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        self.assertGreater(len(evidences), 0)

    def test_mixed_characters_preserved(self):
        content = "## Tema\n\nLa fotosíntesis es importante.\n老师补充说很重要。\n"
        md_path = os.path.join(self.tmpdir, "mixed.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)
        material = Material(
            filename="mixed.md", path=md_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        all_content = "\n".join(e.content for e in evidences)
        self.assertIn("fotosíntesis", all_content)
        self.assertIn("老师补充说很重要", all_content)


class TestNoteParserSourceReference(unittest.TestCase):
    """Tests for SourceReference correctness."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_source_reference_fields(self):
        md_content = "## Tema\n\nLine content\n"
        md_path = os.path.join(self.tmpdir, "test.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        material = Material(
            filename="test.md", path=md_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        for ev in evidences:
            self.assertIsInstance(ev.source_reference, SourceReference)
            self.assertEqual(ev.source_reference.material_id, material.material_id)
            self.assertIsNotNone(ev.source_reference.location)
            self.assertIsNotNone(ev.source_reference.line)

    def test_line_numbers(self):
        lines = "Line 1\nLine 2\nLine 3\n"
        txt_path = os.path.join(self.tmpdir, "test.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(lines)
        material = Material(
            filename="test.txt", path=txt_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        for ev in evidences:
            self.assertIsNotNone(ev.source_reference.line)
            self.assertGreater(ev.source_reference.line, 0)


class TestNoteParserRepeatedParsing(unittest.TestCase):
    """Tests for stable behavior on repeated parsing."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_repeated_parse_same_content(self):
        md_content = "## Tema\n\nContent\n"
        md_path = os.path.join(self.tmpdir, "test.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        material = Material(
            filename="test.md", path=md_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser1 = NoteParser(material)
        parser2 = NoteParser(material)
        evidences1 = parser1.parse()
        evidences2 = parser2.parse()
        self.assertEqual(len(evidences1), len(evidences2))
        for e1, e2 in zip(evidences1, evidences2):
            self.assertEqual(e1.content, e2.content)
            self.assertEqual(e1.language, e2.language)
            self.assertEqual(e1.evidence_type, e2.evidence_type)


class TestNoteParserParseFile(unittest.TestCase):
    """Tests for the parse_file convenience method."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_parse_file_method(self):
        md_content = "## Tema\n\nContent\n"
        md_path = os.path.join(self.tmpdir, "test.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        evidences = NoteParser.parse_file(md_path)
        self.assertGreater(len(evidences), 0)

    def test_parse_file_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            NoteParser.parse_file("/nonexistent/path/file.md")


class TestNoteParserLanguageDetection(unittest.TestCase):
    """Tests for language detection in evidence."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_unknown_language(self):
        content = "This is just English text without special characters.\n"
        md_path = os.path.join(self.tmpdir, "test.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)
        material = Material(
            filename="test.md", path=md_path,
            material_type="note", language=Language.UNKNOWN
        )
        parser = NoteParser(material)
        evidences = parser.parse()
        self.assertEqual(evidences[0].language, Language.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
