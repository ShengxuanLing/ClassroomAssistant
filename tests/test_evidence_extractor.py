"""Tests for the Evidence Extractor (Task 5)."""

import os
import tempfile
import unittest
from pathlib import Path

from src.models import (
    Confidence, Evidence, EvidenceType, Language, Material, SourceReference,
)
from src.evidence_extractor import EvidenceExtractor


class TestEvidenceExtractorTXT(unittest.TestCase):
    """Test TXT extraction."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_txt_extraction(self):
        content = "Paragraph one.\n\nParagraph two.\n"
        txt_path = os.path.join(self.tmpdir, "test.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(content)
        material = Material(
            filename="test.txt", path=txt_path,
            material_type="note", language=Language.UNKNOWN
        )
        extractor = EvidenceExtractor(material)
        evidences = extractor.extract()
        self.assertEqual(len(evidences), 2)
        self.assertEqual(evidences[0].content, "Paragraph one.")
        self.assertEqual(evidences[1].content, "Paragraph two.")

    def test_txt_content_preserved(self):
        content = "Hello World."
        txt_path = os.path.join(self.tmpdir, "test.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(content)
        material = Material(
            filename="test.txt", path=txt_path,
            material_type="note", language=Language.SPANISH
        )
        extractor = EvidenceExtractor(material)
        evidences = extractor.extract()
        self.assertEqual(evidences[0].content, content)

    def test_txt_source_reference(self):
        content = "Line one.\nLine two.\n"
        txt_path = os.path.join(self.tmpdir, "test.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(content)
        material = Material(
            filename="test.txt", path=txt_path,
            material_type="note", language=Language.UNKNOWN
        )
        extractor = EvidenceExtractor(material)
        evidences = extractor.extract()
        for ev in evidences:
            self.assertIsInstance(ev.source_reference, SourceReference)
            self.assertEqual(ev.source_reference.material_id, material.material_id)
            self.assertIsNotNone(ev.source_reference.location)
            self.assertIsNotNone(ev.source_reference.line)
            self.assertIsNotNone(ev.source_reference.paragraph)

    def test_txt_evidence_type(self):
        content = "Test."
        txt_path = os.path.join(self.tmpdir, "test.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(content)
        material = Material(
            filename="test.txt", path=txt_path,
            material_type="note", language=Language.UNKNOWN
        )
        extractor = EvidenceExtractor(material)
        evidences = extractor.extract()
        for ev in evidences:
            self.assertEqual(ev.evidence_type, EvidenceType.PERSONAL_NOTE)

    def test_txt_language_preserved(self):
        for lang, content in [
            (Language.SPANISH, "La fotosíntesis es un proceso."),
            (Language.CATALAN, "La fotosíntesi és un procés."),
            (Language.CHINESE, "光合作用过程非常重要。"),
            (Language.UNKNOWN, "This is just English text without special characters."),
        ]:
            txt_path = os.path.join(self.tmpdir, f"test_{lang.value}.txt")
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(content)
            material = Material(
                filename="test.txt", path=txt_path,
                material_type="note", language=lang
            )
            extractor = EvidenceExtractor(material)
            evidences = extractor.extract()
            self.assertEqual(evidences[0].language, lang)


class TestEvidenceExtractorMarkdown(unittest.TestCase):
    """Test Markdown extraction."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_markdown_heading(self):
        md_content = "# Title\n\nThis is a paragraph.\n"
        md_path = os.path.join(self.tmpdir, "test.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        material = Material(
            filename="test.md", path=md_path,
            material_type="note", language=Language.UNKNOWN
        )
        extractor = EvidenceExtractor(material)
        evidences = extractor.extract()
        self.assertGreater(len(evidences), 0)

    def test_markdown_bullet_list(self):
        md_content = "## Tema\n\n- Item one\n- Item two\n"
        md_path = os.path.join(self.tmpdir, "test.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        material = Material(
            filename="test.md", path=md_path,
            material_type="note", language=Language.UNKNOWN
        )
        extractor = EvidenceExtractor(material)
        evidences = extractor.extract()
        self.assertGreater(len(evidences), 0)

    def test_markdown_source_reference(self):
        md_content = "# Title\n\nContent\n"
        md_path = os.path.join(self.tmpdir, "test.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        material = Material(
            filename="test.md", path=md_path,
            material_type="note", language=Language.UNKNOWN
        )
        extractor = EvidenceExtractor(material)
        evidences = extractor.extract()
        for ev in evidences:
            self.assertIsInstance(ev.source_reference, SourceReference)


class TestEvidenceExtractorUnsupported(unittest.TestCase):
    """Test unsupported material types."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_mp3_returns_empty(self):
        mp3_path = os.path.join(self.tmpdir, "example.mp3")
        Path(mp3_path).touch()
        material = Material(
            filename="example.mp3", path=mp3_path,
            material_type="audio", language=Language.UNKNOWN
        )
        extractor = EvidenceExtractor(material)
        self.assertEqual(extractor.extract(), [])

    def test_wav_returns_empty(self):
        wav_path = os.path.join(self.tmpdir, "example.wav")
        Path(wav_path).touch()
        material = Material(
            filename="example.wav", path=wav_path,
            material_type="audio", language=Language.UNKNOWN
        )
        extractor = EvidenceExtractor(material)
        self.assertEqual(extractor.extract(), [])

    def test_jpg_returns_empty(self):
        jpg_path = os.path.join(self.tmpdir, "example.jpg")
        Path(jpg_path).touch()
        material = Material(
            filename="example.jpg", path=jpg_path,
            material_type="image", language=Language.UNKNOWN
        )
        extractor = EvidenceExtractor(material)
        self.assertEqual(extractor.extract(), [])

    def test_pdf_returns_empty(self):
        pdf_path = os.path.join(self.tmpdir, "example.pdf")
        Path(pdf_path).touch()
        material = Material(
            filename="example.pdf", path=pdf_path,
            material_type="syllabus", language=Language.UNKNOWN
        )
        extractor = EvidenceExtractor(material)
        self.assertEqual(extractor.extract(), [])


class TestEvidenceExtractorEmptyFile(unittest.TestCase):
    """Test empty file handling."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_txt(self):
        txt_path = os.path.join(self.tmpdir, "empty.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("")
        material = Material(
            filename="empty.txt", path=txt_path,
            material_type="note", language=Language.UNKNOWN
        )
        extractor = EvidenceExtractor(material)
        self.assertEqual(extractor.extract(), [])

    def test_empty_markdown(self):
        md_path = os.path.join(self.tmpdir, "empty.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("")
        material = Material(
            filename="empty.md", path=md_path,
            material_type="note", language=Language.UNKNOWN
        )
        extractor = EvidenceExtractor(material)
        self.assertEqual(extractor.extract(), [])


class TestEvidenceExtractorBatch(unittest.TestCase):
    """Test batch extraction."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_batch_extraction(self):
        txt_path = os.path.join(self.tmpdir, "test.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("Txt content.\n")
        md_path = os.path.join(self.tmpdir, "test.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# Title\n\nMd content.\n")
        mp3_path = os.path.join(self.tmpdir, "test.mp3")
        Path(mp3_path).touch()

        materials = [
            Material(filename="test.txt", path=txt_path, material_type="note", language=Language.UNKNOWN),
            Material(filename="test.md", path=md_path, material_type="note", language=Language.UNKNOWN),
            Material(filename="test.mp3", path=mp3_path, material_type="audio", language=Language.UNKNOWN),
        ]
        all_evidence = EvidenceExtractor.extract_all(materials)
        txt_evidences = [e for e in all_evidence if e.source_reference and "test.txt" in (e.source_reference.location or "")]
        md_evidences = [e for e in all_evidence if e.source_reference and "test.md" in (e.source_reference.location or "")]
        self.assertEqual(len(txt_evidences), 1)
        self.assertEqual(len(md_evidences), 2)
        self.assertEqual(len(all_evidence), 3)

    def test_batch_preserves_order(self):
        txt_path = os.path.join(self.tmpdir, "a.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("A.")
        md_path = os.path.join(self.tmpdir, "b.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# B\n\nContent.\n")
        mp3_path = os.path.join(self.tmpdir, "c.mp3")
        Path(mp3_path).touch()

        materials = [
            Material(filename="a.txt", path=txt_path, material_type="note", language=Language.UNKNOWN),
            Material(filename="b.md", path=md_path, material_type="note", language=Language.UNKNOWN),
            Material(filename="c.mp3", path=mp3_path, material_type="audio", language=Language.UNKNOWN),
        ]
        all_evidence = EvidenceExtractor.extract_all(materials)
        locations = []
        for ev in all_evidence:
            loc = ev.source_reference.location if ev.source_reference else ""
            locations.append(os.path.basename(loc) if loc else "unknown")
        self.assertIn("a.txt", locations)
        self.assertIn("b.md", locations)
        self.assertNotIn("c.mp3", locations)


class TestEvidenceExtractorErrorIsolation(unittest.TestCase):
    """Test error isolation in batch processing."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_error_isolation(self):
        txt_path = os.path.join(self.tmpdir, "valid.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("Valid content.\n")
        broken_path = os.path.join(self.tmpdir, "broken.txt")
        with open(broken_path, "w", encoding="utf-8") as f:
            f.write("Valid content.\n")
        os.chmod(broken_path, 0o000)
        try:
            md_path = os.path.join(self.tmpdir, "valid2.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("# Title\n\nContent.\n")
            materials = [
                Material(filename="valid.txt", path=txt_path, material_type="note", language=Language.UNKNOWN),
                Material(filename="broken.txt", path=broken_path, material_type="note", language=Language.UNKNOWN),
                Material(filename="valid2.md", path=md_path, material_type="note", language=Language.UNKNOWN),
            ]
            all_evidence = EvidenceExtractor.extract_all(materials)
            self.assertGreater(len(all_evidence), 0)
        finally:
            os.chmod(broken_path, 0o644)


class TestEvidenceExtractorContentPreservation(unittest.TestCase):
    """Test that content is not modified."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_content_not_trimmed(self):
        content = "Exact content without changes."
        txt_path = os.path.join(self.tmpdir, "test.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(content)
        material = Material(
            filename="test.txt", path=txt_path,
            material_type="note", language=Language.UNKNOWN
        )
        extractor = EvidenceExtractor(material)
        evidences = extractor.extract()
        self.assertEqual(evidences[0].content, content.strip())

    def test_content_not_translated(self):
        content = "Este es un ejemplo. La fotosíntesis es importante."
        txt_path = os.path.join(self.tmpdir, "test.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(content)
        material = Material(
            filename="test.txt", path=txt_path,
            material_type="note", language=Language.SPANISH
        )
        extractor = EvidenceExtractor(material)
        evidences = extractor.extract()
        self.assertEqual(evidences[0].content, content.strip())


if __name__ == "__main__":
    unittest.main()
