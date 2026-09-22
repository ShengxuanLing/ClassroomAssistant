import os
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from src.models import Material, MaterialType, Language

_EXT_TO_TYPE = {
    '.mp3': MaterialType.AUDIO,
    '.m4a': MaterialType.AUDIO,
    '.wav': MaterialType.AUDIO,
    '.ogg': MaterialType.AUDIO,
    '.jpg': MaterialType.IMAGE,
    '.jpeg': MaterialType.IMAGE,
    '.png': MaterialType.IMAGE,
    '.webp': MaterialType.IMAGE,
    '.txt': MaterialType.NOTE,
    '.md': MaterialType.NOTE,
    '.pdf': MaterialType.SYLLABUS,
    '.docx': MaterialType.SYLLABUS,
}
_RECOGNIZED_SUBDIRS = {'audio', 'images', 'notes', 'syllabi'}


def _compute_file_hash(filepath):
    hasher = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def _compute_stable_id(rel_path, size, mtime_ns):
    data = rel_path + chr(0) + str(size) + chr(0) + str(mtime_ns)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def _classify_by_extension(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    return _EXT_TO_TYPE.get(ext)


def _classify_by_directory(filepath, input_dir):
    rel = os.path.relpath(filepath, input_dir)
    parts = rel.split(os.sep)
    if len(parts) > 1 and parts[0] in _RECOGNIZED_SUBDIRS:
        dt = {'audio': MaterialType.AUDIO, 'images': MaterialType.IMAGE, 'notes': MaterialType.NOTE, 'syllabi': MaterialType.SYLLABUS}
        return dt.get(parts[0])
    return None


def _infer_language(filepath):
    basename = os.path.basename(filepath).lower()
    name_without_ext = os.path.splitext(basename)[0]
    if '_ca' in name_without_ext or '-ca' in name_without_ext or name_without_ext.endswith('ca'):
        return Language.CATALAN
    if '_es' in name_without_ext or '-es' in name_without_ext or name_without_ext.endswith('es'):
        return Language.SPANISH
    return Language.UNKNOWN


def scan_materials(input_dir):
    input_path = Path(input_dir)
    if not input_path.exists():
        return []
    materials = []
    seen_paths = set()
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != 'cache']
        for filename in files:
            if filename.startswith('.'):
                continue
            filepath = os.path.join(root, filename)
            rel_path = os.path.relpath(filepath, input_dir).replace(os.sep, '/')
            if rel_path in seen_paths:
                continue
            seen_paths.add(rel_path)
            material_type = _classify_by_extension(filepath)
            if material_type is None:
                continue
            try:
                stat = os.stat(filepath)
                size = stat.st_size
                mtime_ns = int(stat.st_mtime_ns)
            except OSError:
                continue
            material_id = _compute_stable_id(rel_path, size, mtime_ns)
            content_hash = _compute_file_hash(filepath)
            metadata = {'size': size, 'sha256': content_hash, 'mtime': mtime_ns, 'relative_path': rel_path}
            m = Material(
                material_id=material_id,
                filename=filename,
                path=rel_path,
                material_type=material_type,
                language=_infer_language(filepath),
                created_at=datetime.fromtimestamp(mtime_ns / 1e9, tz=timezone.utc).isoformat(),
                metadata=metadata,
            )
            materials.append(m)
    return materials


def save_index(materials, output_path):
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    data = [m.to_dict() for m in materials]
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_index(input_dir, output_path):
    materials = scan_materials(input_dir)
    save_index(materials, output_path)
    return materials
