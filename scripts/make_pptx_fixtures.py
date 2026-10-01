# -*- coding: utf-8 -*-
"""Regenerate the PPTX document fixtures used by ``tests/test_document_input.py``.

Run:  python scripts/make_pptx_fixtures.py

The fixtures are checked in (tests must not depend on python-pptx at
collection time), but the *source* of truth for them is this script, so
they can be rebuilt and diffed when the fixture set changes.
"""

from __future__ import annotations

import os
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "documents"


def _png(width: int = 24, height: int = 16) -> bytes:
    """Minimal, dependency-free RGB PNG (solid colour) used as slide art."""
    raw = b"".join(b"\x00" + bytes([220, 40, 60] * width) for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def build_lecture() -> bytes:
    import io

    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()

    slide = prs.slides.add_slide(prs.slide_layouts[1])  # title + content
    slide.shapes.title.text = "Funcions de densitat"
    slide.placeholders[1].text = (
        "La densitat de probabilitat integra 1 sobre tot el suport."
    )
    slide.notes_slide.notes_text_frame.text = (
        "Recordator: normalitzar abans de dibuixar."
    )

    slide2 = prs.slides.add_slide(prs.slide_layouts[5])  # title only
    slide2.shapes.title.text = "Taula de valors"
    rows, cols = 2, 2
    table = slide2.shapes.add_table(
        rows, cols, Inches(1), Inches(2), Inches(4), Inches(1)
    ).table
    table.cell(0, 0).text = "x"
    table.cell(0, 1).text = "f(x)"
    table.cell(1, 0).text = "0"
    table.cell(1, 1).text = "0.25"

    slide3 = prs.slides.add_slide(prs.slide_layouts[5])
    slide3.shapes.title.text = "Gràfic"
    slide3.shapes.add_picture(
        io.BytesIO(_png()), Inches(1), Inches(2), width=Inches(3)
    )
    slide3.notes_slide.notes_text_frame.text = "Projectar en gran."

    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


def build_picture_only() -> bytes:
    """A deck whose only content is a picture: no text, no notes."""
    import io

    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    slide.shapes.add_picture(io.BytesIO(_png()), Inches(1), Inches(1))
    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


def build_empty() -> bytes:
    """Zero slides, zero shapes, zero text."""
    import io

    from pptx import Presentation

    buffer = io.BytesIO()
    Presentation().save(buffer)
    return buffer.getvalue()


def build_encrypted() -> bytes:
    """A password-protected OOXML package.

    Real encryption turns the file into an OLE/CFB container holding an
    ``EncryptedPackage`` stream.  We emit the CFB magic + a stub stream:
    python-pptx rejects it exactly like a genuinely encrypted deck (it is
    not a zip), which is the behaviour under test.
    """
    cfb = bytearray(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    cfb += b"\x00" * 504
    cfb += struct.pack("<H", 0x003E)          # minor version
    cfb += struct.pack("<H", 0x0003)          # major version (512-byte sectors)
    cfb += struct.pack("<H", 0xFFFE)          # little endian
    cfb += struct.pack("<H", 9)               # sector shift -> 512
    cfb += struct.pack("<H", 6)               # mini sector shift -> 64
    cfb += b"\x00" * 6
    cfb += struct.pack("<I", 0)               # number of directory sectors
    cfb += struct.pack("<I", 1)               # number of FAT sectors
    cfb += struct.pack("<I", 0)               # first directory sector
    cfb += struct.pack("<I", 0x1000)          # transaction signature
    cfb += struct.pack("<I", 0x1000)          # mini stream cutoff
    cfb += struct.pack("<I", 0)               # first mini FAT sector
    cfb += struct.pack("<I", 0)               # number of mini FAT sectors
    cfb += struct.pack("<I", 0xFFFFFFFE)      # first DIFAT sector
    cfb += struct.pack("<I", 0)               # number of DIFAT sectors
    cfb += b"\x00" * 76                       # DIFAT array (all FREESECT)
    cfb += b"\x00" * (512 - len(cfb) % 512) if len(cfb) % 512 else b""
    cfb += b"EncryptedPackage".ljust(512, b"\x00")
    return bytes(cfb)


def main() -> int:
    try:
        import pptx  # noqa: F401
    except ImportError:
        print("python-pptx missing: pip install -r requirements.txt", file=sys.stderr)
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "lecture.pptx": build_lecture(),
        "picture_only.pptx": build_picture_only(),
        "empty.pptx": build_empty(),
        "encrypted.pptx": build_encrypted(),
        "corrupted.pptx": b"PK\x03\x04 this is not really a pptx payload",
    }
    for name, data in artifacts.items():
        (OUT / name).write_bytes(data)
        print(f"wrote {name} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
