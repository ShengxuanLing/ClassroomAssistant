"""Task 45 acceptance-dataset generator (deterministic, offline).

Generates a **realistic UAB second-year classroom dataset** under
``tests/fixtures/acceptance/``.  This is the dataset the Acceptance Harness
(Task 45) drives through the whole product flow.

Run::

    python scripts/gen_task45_fixtures.py

Design notes
------------
* Everything is deterministic: no timestamps, no randomness, no network.
* The dataset deliberately contains the four things an acceptance run must
  be able to observe:

  1. a **real order conflict** between two independent sources
     (teacher's spoken order vs. the student's written order for the two
     steps of Dijkstra) -- detected by the deterministic order-reversal
     rule, and semantically a genuine contradiction;
  2. an **exact duplicate sentence** shared by two different materials
     (teacher speech + student notes) -- must be recognised as a
     duplicate, never as a conflict;
  3. content that must **not** be mistaken for a conflict, including the
     Spanish word "notacion" and the Catalan word "luminosa"-style
     substrings (regression guard for the negation-word bug fixed in
     Task 45);
  4. a **broken file** (corrupt PDF) so failure isolation is exercised.

* Both required input languages are present: the teacher speaks Spanish
  and the board is Catalan; the student takes the main notes in Spanish
  and writes a separate Catalan summary.
* The Catalan summary is uploaded **without a session**, so its knowledge
  points are genuinely ``uncovered`` -- the acceptance run must be able to
  answer "what is not covered?" with a non-empty, truthful answer.
"""

from __future__ import annotations

import io
import json
import math
import os
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "tests" / "fixtures" / "acceptance"
OUT.mkdir(parents=True, exist_ok=True)

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

try:  # Pillow is used elsewhere in the project; degrade loudly if missing.
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - dependency guard
    Image = None
    ImageDraw = None


# ---------------------------------------------------------------------------
# Course / session identity (kept in sync with tests/acceptance_dataset.py)
# ---------------------------------------------------------------------------

COURSE_NAME = "Estructura de Dades i Algorismes"
COURSE_CODE = "EDA201"
COURSE_LANGUAGE = "ca"
SESSION_NUMBER = 1
SESSION_DATE = "2026-09-15"
SESSION_TITLE = "Tema 1 - Fonaments d'algorismica"


# ---------------------------------------------------------------------------
# 1) PDF: teacher handout (Spanish, latin-1 safe)
# ---------------------------------------------------------------------------

def _pdf_page(writer, text_lines):
    page = writer.add_blank_page(width=612, height=792)
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )
    resources = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    page[NameObject("/Resources")] = writer._add_object(resources)
    parts = []
    if text_lines:
        body = "BT /F1 11 Tf 72 720 Td 14 TL\n"
        for line in text_lines:
            escaped = (
                line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            )
            body += "(%s) Tj T*\n" % escaped
        body += "ET\n"
        stream = DecodedStreamObject()
        stream.set_data(body.encode("latin-1"))
        parts.append(writer._add_object(stream))
    page[NameObject("/Contents")] = writer._add_object(ArrayObject(parts))
    return page


def _save_pdf(name, pages):
    writer = PdfWriter()
    for text in pages:
        _pdf_page(writer, text.split("\n") if text else [])
    buf = io.BytesIO()
    writer.write(buf)
    raw = buf.getvalue()
    (OUT / name).write_bytes(raw)
    print("  %-34s %6d bytes" % (name, len(raw)))


# ---------------------------------------------------------------------------
# 2) DOCX: student notes (Spanish, contains the reversed order claim)
# ---------------------------------------------------------------------------

def _save_docx(name, paragraphs):
    from docx import Document as DocxDocument

    document = DocxDocument()
    for para in paragraphs:
        document.add_paragraph(para)
    buf = io.BytesIO()
    document.save(buf)
    raw = _freeze_zip_timestamps(buf.getvalue())
    (OUT / name).write_bytes(raw)
    print("  %-34s %6d bytes" % (name, len(raw)))


#: 固定的 ZIP 条目时间戳 —— 生成物必须逐字节可复现。
_FROZEN_ZIP_DATETIME = (1980, 1, 1, 0, 0, 0)


def _freeze_zip_timestamps(raw: bytes) -> bytes:
    """Rewrite an OOXML zip with fixed entry timestamps.

    ``zipfile`` stamps every entry with the current local time, so two runs of
    this generator produced byte-different DOCX files (same XML, different
    hashes).  Material identity is content-addressed, so a fixture that changes
    hash between runs would silently invalidate every ``material_id`` derived
    from it.  Freezing the timestamps makes the fixture genuinely reproducible.
    """
    import zipfile

    source = zipfile.ZipFile(io.BytesIO(raw))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            frozen = zipfile.ZipInfo(info.filename, date_time=_FROZEN_ZIP_DATETIME)
            frozen.compress_type = zipfile.ZIP_DEFLATED
            frozen.external_attr = info.external_attr
            target.writestr(frozen, payload)
    source.close()
    return out.getvalue()


# ---------------------------------------------------------------------------
# 3) WAV: classroom recording container
# ---------------------------------------------------------------------------

def _save_wav(name, *, seconds=3.0, rate=8000, freq=440.0, amplitude=0.35):
    """A small, valid, non-silent mono 16-bit PCM WAV."""
    frames = bytearray()
    total = int(seconds * rate)
    for index in range(total):
        # Two-tone so the file is clearly not silence, still deterministic.
        value = amplitude * math.sin(2.0 * math.pi * freq * index / rate)
        if index > total // 2:
            value = amplitude * math.sin(2.0 * math.pi * (freq * 1.5) * index / rate)
        frames += struct.pack("<h", int(max(-1.0, min(1.0, value)) * 32767))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    raw = buf.getvalue()
    (OUT / name).write_bytes(raw)
    print("  %-34s %6d bytes" % (name, len(raw)))


# ---------------------------------------------------------------------------
# 4) PNG: board photo
# ---------------------------------------------------------------------------

BOARD_LINES = [
    "Tema 1: Fonaments d'algorismica",
    "Algorisme = sequencia finita d'instruccions",
    "Complexitat temporal: O(n), O(n log n), O(n^2)",
    "Dijkstra: inicialitzacio -> seleccio del node minim",
    "Cerca binaria: cal un array ordenat",
]


def _save_board_png(name):
    if Image is None:  # pragma: no cover - dependency guard
        raise SystemExit("Pillow is required to generate the board image fixture")
    width, height = 900, 600
    image = Image.new("RGB", (width, height), (24, 32, 28))
    draw = ImageDraw.Draw(image)
    font = None
    for candidate in (
        r"C:\Windows\Fonts\consola.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.isfile(candidate):
            try:
                from PIL import ImageFont

                font = ImageFont.truetype(candidate, 26)
                break
            except OSError:  # pragma: no cover - font load failure
                font = None
    y = 40
    for index, line in enumerate(BOARD_LINES):
        draw.rectangle([30, y - 8, width - 30, y + 34], outline=(70, 90, 80))
        draw.text((48, y), line, fill=(226, 240, 230), font=font)
        y += 62
    draw.rectangle([30, y - 8, width - 30, y + 34], outline=(70, 90, 80))
    draw.text((48, y), "Prof. M. Serra - 15/09/2026", fill=(150, 200, 170), font=font)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    raw = buf.getvalue()
    (OUT / name).write_bytes(raw)
    print("  %-34s %6d bytes" % (name, len(raw)))


# ---------------------------------------------------------------------------
# 5) Classroom transcript (what the teacher actually said)
# ---------------------------------------------------------------------------

TRANSCRIPT = {
    "fixture": "task45-classroom-transcript",
    "note": (
        "Transcript of a real-style UAB lecture (Spanish). Consumed by "
        "MockASRProvider(segments=...); no audio is actually decoded."
    ),
    "language": "es",
    "segments": [
        {
            "start": 0.0,
            "end": 16.0,
            "speaker": "profesor",
            "language": "es",
            "confidence": 0.92,
            "text": "Hoy empezamos el Tema 1: fundamentos de algoritmia.",
        },
        {
            "start": 16.0,
            "end": 40.0,
            "speaker": "profesor",
            "language": "es",
            "confidence": 0.9,
            "text": (
                "Un algoritmo es una secuencia finita de instrucciones "
                "que transforma una entrada en una salida."
            ),
        },
        {
            "start": 40.0,
            "end": 66.0,
            "speaker": "profesor",
            "language": "es",
            "confidence": 0.88,
            "text": (
                "La complejidad temporal mide como crece el numero de "
                "operaciones con el tamano de la entrada."
            ),
        },
        {
            "start": 66.0,
            "end": 96.0,
            "speaker": "profesor",
            "language": "es",
            "confidence": 0.85,
            "text": (
                "En el algoritmo de Dijkstra, la inicializacion del vector "
                "de distancias antes que la seleccion del nodo minimo."
            ),
        },
        {
            "start": 96.0,
            "end": 124.0,
            "speaker": "profesor",
            "language": "es",
            "confidence": 0.9,
            "text": (
                "La notacion O grande describe una cota superior asintotica "
                "del coste."
            ),
        },
        {
            "start": 124.0,
            "end": 150.0,
            "speaker": "profesor",
            "language": "es",
            "confidence": 0.87,
            "text": (
                "Recordad que la busqueda binaria requiere que el array "
                "este ordenado."
            ),
        },
    ],
}


def _save_json(name, payload):
    raw = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")
    (OUT / name).write_bytes(raw)
    print("  %-34s %6d bytes" % (name, len(raw)))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Task 45 acceptance fixtures ->", OUT)

    # -- PDF (teacher handout, Spanish) ---------------------------------
    _save_pdf(
        "tema1-guia-docent.pdf",
        [
            "\n".join(
                [
                    "Estructura de Datos y Algoritmos - Tema 1",
                    "Guia del profesor: fundamentos de algoritmia",
                    "",
                    "Definicion: un algoritmo transforma una entrada en una salida.",
                    "La complejidad temporal mide el coste en funcion del tamano",
                    "de la entrada.",
                    "La notacion O grande describe una cota superior asintotica.",
                ]
            ),
            "\n".join(
                [
                    "Evaluacion del Tema 1",
                    "",
                    "Entrega de ejercicios: 30 de septiembre de 2026",
                    "Examen parcial: 20 de octubre de 2026",
                    "Nota minima para aprobar el parcial: 5.0",
                ]
            ),
        ],
    )

    # -- DOCX (student notes, Spanish; holds the reversed order claim) --
    _save_docx(
        "tema1-apunts-alumne.docx",
        [
            "Tema 1 - Fundamentos de algoritmia",
            "Apuntes de clase del alumno",
            (
                "Un algoritmo es una secuencia finita de instrucciones "
                "que transforma una entrada en una salida."
            ),
            (
                "En el algoritmo de Dijkstra, la seleccion del nodo minimo "
                "antes que la inicializacion del vector de distancias."
            ),
            "La complejidad temporal se expresa con la notacion O grande.",
        ],
    )

    # -- WAV (classroom recording container) ----------------------------
    _save_wav("tema1-classe.wav")

    # -- PNG (board photo) ----------------------------------------------
    _save_board_png("tema1-pissarra.png")

    # -- Transcript + board OCR -----------------------------------------
    _save_json("tema1-transcripcio.json", TRANSCRIPT)
    _save_json(
        "tema1-pissarra-ocr.json",
        {
            "fixture": "task45-board-ocr",
            "note": (
                "Board OCR lines (Catalan). Consumed by ScriptedOCREngine; "
                "the board photo is never actually decoded."
            ),
            "language": "ca",
            "segments": [
                {
                    "text": line,
                    "confidence": round(0.90 + 0.01 * (index % 5), 2),
                    "page": 1,
                    "bounding_box": {
                        "x": 30.0,
                        "y": 32.0 + index * 62.0,
                        "width": 840.0,
                        "height": 42.0,
                    },
                }
                for index, line in enumerate(BOARD_LINES)
            ],
        },
    )

    # -- Markdown notes, Catalan, uploaded WITHOUT a session ------------
    summary = "\n".join(
        [
            "# Resum Tema 1 - Fonaments d'algorismica",
            "",
            "Aquest resum l'he fet pel meu compte, despres de classe.",
            "",
            "L'algorisme es una sequencia finita d'instruccions.",
            "La complexitat temporal es mesura amb la notacio O gran.",
            "La cerca binaria necessita un array ordenat.",
            "",
        ]
    )
    (OUT / "tema1-resum-alumne.md").write_text(summary, encoding="utf-8")
    print("  %-34s %6d bytes" % ("tema1-resum-alumne.md", len(summary.encode("utf-8"))))

    # -- Corrupt PDF (failure isolation) --------------------------------
    broken = b"%PDF-1.4 this fixture is intentionally corrupt\n"
    (OUT / "tema1-fallit.pdf").write_bytes(broken)
    print("  %-34s %6d bytes" % ("tema1-fallit.pdf", len(broken)))

    # -- Manifest -------------------------------------------------------
    manifest = {
        "fixture": "task45-acceptance-dataset",
        "course": {
            "name": COURSE_NAME,
            "code": COURSE_CODE,
            "language": COURSE_LANGUAGE,
        },
        "session": {
            "session_number": SESSION_NUMBER,
            "date": SESSION_DATE,
            "title": SESSION_TITLE,
        },
        "transcript": {
            "filename": "tema1-transcripcio.json",
            "language": "es",
            "speaker_label": "profesor",
        },
        "board_ocr": {
            "filename": "tema1-pissarra-ocr.json",
            "language": "ca",
        },
        "materials": [
            {
                "filename": "tema1-guia-docent.pdf",
                "role": "handout",
                "language": "es",
                "attach_to_session": True,
                "expected": "COMPLETED",
            },
            {
                "filename": "tema1-apunts-alumne.docx",
                "role": "student_notes",
                "language": "es",
                "attach_to_session": True,
                "expected": "COMPLETED",
                "note": "contains the reversed Dijkstra step order",
            },
            {
                "filename": "tema1-classe.wav",
                "role": "audio",
                "language": "es",
                "attach_to_session": True,
                "expected": "COMPLETED",
                "note": "text comes from tema1-transcripcio.json",
            },
            {
                "filename": "tema1-pissarra.png",
                "role": "board_image",
                "language": "ca",
                "attach_to_session": True,
                "expected": "COMPLETED",
                "note": "text comes from tema1-pissarra-ocr.json",
            },
            {
                "filename": "tema1-resum-alumne.md",
                "role": "student_summary",
                "language": "ca",
                "attach_to_session": False,
                "expected": "COMPLETED",
                "note": "deliberately outside the session -> uncovered knowledge",
            },
            {
                "filename": "tema1-fallit.pdf",
                "role": "broken_document",
                "language": "es",
                "attach_to_session": True,
                "expected": "FAILED",
                "note": "failure isolation: must not break the other materials",
            },
        ],
    }
    _save_json("manifest.json", manifest)

    print("acceptance fixtures done:", len(os.listdir(OUT)), "files")


if __name__ == "__main__":
    main()
