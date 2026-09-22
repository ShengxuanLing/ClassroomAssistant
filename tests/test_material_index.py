import os
import tempfile
import json
import unittest
import hashlib
from pathlib import Path
from src.models import Material, MaterialType, Language
from src.material_index import scan_materials, save_index, build_index, _compute_stable_id

class TestMaterialIndex(unittest.TestCase):
    def test_empty_input(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            materials = scan_materials(tmpdir)
            self.assertEqual(materials, [])

    def test_audio_recognition(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_dir = os.path.join(tmpdir, 'audio')
            os.makedirs(audio_dir)
            Path(os.path.join(audio_dir, 'lecture01.mp3')).touch()
            materials = scan_materials(tmpdir)
            self.assertEqual(len(materials), 1)
            self.assertEqual(materials[0].material_type, MaterialType.AUDIO)

    def test_image_recognition(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            img_dir = os.path.join(tmpdir, 'images')
            os.makedirs(img_dir)
            Path(os.path.join(img_dir, 'board.jpg')).touch()
            materials = scan_materials(tmpdir)
            self.assertEqual(len(materials), 1)
            self.assertEqual(materials[0].material_type, MaterialType.IMAGE)

    def test_note_recognition(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_dir = os.path.join(tmpdir, 'notes')
            os.makedirs(notes_dir)
            Path(os.path.join(notes_dir, 'my_notes.md')).touch()
            Path(os.path.join(notes_dir, 'classmate.txt')).touch()
            materials = scan_materials(tmpdir)
            self.assertEqual(len(materials), 2)
            types = {m.material_type for m in materials}
            self.assertEqual(types, {MaterialType.NOTE})

    def test_syllabus_recognition(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            syllabi_dir = os.path.join(tmpdir, 'syllabi')
            os.makedirs(syllabi_dir)
            Path(os.path.join(syllabi_dir, 'course.pdf')).touch()
            materials = scan_materials(tmpdir)
            self.assertEqual(len(materials), 1)
            self.assertEqual(materials[0].material_type, MaterialType.SYLLABUS)

    def test_unsupported_files_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, 'random.exe')).touch()
            Path(os.path.join(tmpdir, 'script.sh')).touch()
            materials = scan_materials(tmpdir)
            self.assertEqual(materials, [])

    def test_stable_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_dir = os.path.join(tmpdir, 'audio')
            os.makedirs(audio_dir)
            fpath = os.path.join(audio_dir, 'lecture01.mp3')
            Path(fpath).touch()
            materials1 = scan_materials(tmpdir)
            materials2 = scan_materials(tmpdir)
            self.assertEqual(len(materials1), 1)
            self.assertEqual(len(materials2), 1)
            self.assertEqual(materials1[0].material_id, materials2[0].material_id)

    def test_stable_id_after_copy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_dir = os.path.join(tmpdir, 'audio')
            os.makedirs(audio_dir)
            fpath = os.path.join(audio_dir, 'a.mp3')
            Path(fpath).touch()
            copy_path = os.path.join(audio_dir, 'a_copy.mp3')
            Path(copy_path).write_bytes(Path(fpath).read_bytes())
            materials = scan_materials(tmpdir)
            ids = {m.material_id for m in materials}
            self.assertEqual(len(ids), 2)

    def test_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_dir = os.path.join(tmpdir, 'audio')
            os.makedirs(audio_dir)
            fpath = os.path.join(audio_dir, 'lecture01.mp3')
            Path(fpath).touch()
            materials = scan_materials(tmpdir)
            self.assertEqual(len(materials), 1)
            m = materials[0]
            self.assertIn('size', m.metadata)
            self.assertIn('sha256', m.metadata)
            self.assertIn('relative_path', m.metadata)
            self.assertEqual(m.metadata['relative_path'], 'audio/lecture01.mp3')
            self.assertEqual(m.filename, 'lecture01.mp3')

    def test_unicode_filenames(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_dir = os.path.join(tmpdir, 'notes')
            os.makedirs(notes_dir)
            Path(os.path.join(notes_dir, '我的课堂笔记.md')).touch()
            Path(os.path.join(notes_dir, 'apuntes_es.md')).touch()
            Path(os.path.join(notes_dir, 'apunts_ca.md')).touch()
            materials = scan_materials(tmpdir)
            self.assertEqual(len(materials), 3)
            filenames = {m.filename for m in materials}
            self.assertIn('我的课堂笔记.md', filenames)
            self.assertIn('apuntes_es.md', filenames)
            self.assertIn('apunts_ca.md', filenames)

    def test_output_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_dir = os.path.join(tmpdir, 'audio')
            os.makedirs(audio_dir)
            Path(os.path.join(audio_dir, 'lecture01.mp3')).touch()
            output_path = os.path.join(tmpdir, 'output', 'index', 'materials.json')
            materials = build_index(tmpdir, output_path)
            self.assertTrue(os.path.exists(output_path))
            with open(output_path, 'r') as f:
                data = json.load(f)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]['material_type'], 'audio')
            self.assertIn('sha256', data[0]['metadata'])

    def test_no_content_parsed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_dir = os.path.join(tmpdir, 'audio')
            os.makedirs(audio_dir)
            fpath = os.path.join(audio_dir, 'lecture01.mp3')
            Path(fpath).write_text('This is not real audio content')
            materials = scan_materials(tmpdir)
            self.assertEqual(len(materials), 1)
            self.assertIn('size', materials[0].metadata)

    def test_language_unknown_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_dir = os.path.join(tmpdir, 'audio')
            os.makedirs(audio_dir)
            Path(os.path.join(audio_dir, 'lecture01.mp3')).touch()
            materials = scan_materials(tmpdir)
            self.assertEqual(materials[0].language, Language.UNKNOWN)

if __name__ == '__main__':
    unittest.main()
