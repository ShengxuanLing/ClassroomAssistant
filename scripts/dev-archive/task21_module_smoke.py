"""Task 21 - deterministic document input/parsing layer probe + module smoke.

Run: python scripts/task21_module_smoke.py
"""
import io
import os
import sys
import zipfile

# Force UTF-8 to avoid gbk console issues on Windows
sys.stdout.reconfigure(encoding="utf-8")

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject
from pypdf.errors import (
    PdfReadError,
    PyPdfError,
    EmptyFileError,
    WrongPasswordError,
    FileNotDecryptedError,
)
from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from zipfile import BadZipFile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def make_pdf(pages_content, password=None):
    """Build a minimal text PDF. pages_content: list of raw content-stream bytes."""
    w = PdfWriter()
    for _ in pages_content:
        w.add_blank_page(width=595, height=842)
    for i, content in enumerate(pages_content):
        page = w.pages[i]
        s = StreamObject()
        s.set_data(content)
        s[NameObject("/Type")] = NameObject("/Stream")
        fonts = DictionaryObject()
        f1 = DictionaryObject()
        f1[NameObject("/Type")] = NameObject("/Font")
        f1[NameObject("/Subtype")] = NameObject("/Type1")
        f1[NameObject("/BaseFont")] = NameObject("/Helvetica")
        fonts[NameObject("/F1")] = f1
        res = DictionaryObject()
        res[NameObject("/Font")] = fonts
        page[NameObject("/Resources")] = res
        page[NameObject("/Contents")] = s
    if password:
        w.encrypt(password)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def text_page_latin1(text):
    return b"BT /F1 12 Tf 72 712 Td (" + text.encode("latin-1") + b") Tj ET"


def text_page_utf8(text):
    return b"BT /F1 12 Tf 72 712 Td (" + text.encode("utf-8") + b") Tj ET"


def empty_pdf(n_pages):
    return make_pdf([b""] * n_pages)


def make_docx(path=None):
    d = Document()
    d.add_heading("Titulo", 0)
    d.add_heading("H1", 1)
    d.add_paragraph("Para uno")
    d.add_heading("H2", 2)
    d.add_paragraph("Para dos")
    d.add_paragraph("")
    d.add_paragraph("Para tres")
    if path:
        d.save(path)
        return path
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ---------- build fixtures ----------
os.makedirs(r"D:\Project\Clases\tests\fixtures\documents", exist_ok=True)

# simple.pdf: 3 pages Hello / Intro / End
simple = make_pdf([text_page_latin1(x) for x in [b'Hello', ('Introducci' + chr(243) + 'n').encode('latin-1'), b'End']])
open(r"D:\Project\Clases\tests\fixtures\documents\simple.pdf", "wb").write(simple)

# multilingual.pdf: spanish / catalan / chinese / german
ml = make_pdf([
    text_page_latin1("Introducci\u00f3n a la teor\u00eda de sistemas"),
    text_page_latin1("Introduccio\u00e0 a la teoria dels sistemes".encode("latin-1", "replace").decode("latin-1")),
    text_page_utf8("Stability of the system (system-stability tests)"),
    text_page_latin1("Stabilit\u00e4t eines Systems".encode("latin-1")),
])
open(r"D:\Project\Clases\tests\fixtures\documents\multilingual.pdf", "wb").write(ml)

# empty.pdf: 1 blank page
open(r"D:\Project\Clases\tests\fixtures\documents\empty.pdf", "wb").write(empty_pdf(1))

# scanned_like.pdf: 3 blank pages
open(r"D:\Project\Clases\tests\fixtures\documents\scanned_like.pdf", "wb").write(empty_pdf(3))

# corrupted.pdf
open(r"D:\Project\Clases\tests\fixtures\documents\corrupted.pdf", "wb").write(
    b"This is definitely not a PDF file \x00\x01\x02\x03")

# password.pdf
pw = make_pdf([text_page_latin1(b'Secret page')], password='sesame')
open(r"D:\Project\Clases\tests\fixtures\documents\password.pdf", "wb").write(pw)

# .0bytes.pdf
open(r"D:\Project\Clases\tests\fixtures\documents\zero_bytes.pdf", "wb").write(b"")

# .0bytes.docx
open(r"D:\Project\Clases\tests\fixtures\documents\zero_bytes.docx", "wb").write(b"")

# .bad.docx
open(r"D:\Project\Clases\tests\fixtures\documents\corrupted.docx", "wb").write(
    b"not a zip \x50\x4b\x03\x04garbage")

# simple.docx
simple_docx = make_docx()
open(r"D:\Project\Clases\tests\fixtures\documents\simple.docx", "wb").write(simple_docx)

# multilingual.docx
d = Document()
d.add_heading("Document multilingue", 0)
d.add_heading("Capitulo 1", 1)
d.add_paragraph("Introducci\u00f3n a la teor\u00eda de sistemas (Spanish)")
d.add_paragraph("Introduccio\u00e0 a la teoria dels sistemes (Catalan)")
d.add_paragraph("System-stability tests (system-stability tests)")
d.add_heading("Capitulo 2", 1)
d.add_paragraph("Stabilit\u00e4t eines Systems (German)")
d.add_paragraph("Para final")
buf = io.BytesIO()
d.save(buf)
open(r"D:\Project\Clases\tests\fixtures\documents\multilingual.docx", "wb").write(buf.getvalue())

# headings.docx
d = Document()
d.add_heading("Top", 0)
d.add_heading("H1A", 1)
d.add_paragraph("normal after h1")
d.add_heading("H2A", 2)
d.add_paragraph("normal after h2")
d.add_heading("H3A", 3)
d.add_paragraph("normal after h3")
d.add_paragraph("just a chapter 1")
d.add_paragraph("just a chapter 2")
buf = io.BytesIO()
d.save(buf)
open(r"D:\Project\Clases\tests\fixtures\documents\headings.docx", "wb").write(buf.getvalue())

# tables.docx
d = Document()
d.add_heading("Table section", 1)
d.add_paragraph("before table")
t1 = d.add_table(rows=2, cols=3)
for r in range(2):
    for c in range(3):
        t1.cell(r, c).text = f"row{r}col{c}"
d.add_paragraph("after table 1")
t2 = d.add_table(rows=1, cols=2)
t2.cell(0, 0).text = "x"
t2.cell(0, 1).text = "y"
d.add_paragraph("after table 2")
buf = io.BytesIO()
d.save(buf)
open(r"D:\Project\Clases\tests\fixtures\documents\tables.docx", "wb").write(buf.getvalue())

# empty.docx: 1 blank normal paragraph + empty paragraphs
d = Document()
d.add_paragraph("")
d.add_paragraph("   ")
d.add_paragraph("")
buf = io.BytesIO()
d.save(buf)
open(r"D:\Project\Clases\tests\fixtures\documents\empty.docx", "wb").write(buf.getvalue())

# images.docx
d = Document()
d.add_paragraph("before image")
import struct
import zlib


def make_png(width=2, height=2):
    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr = chunk(b"IHDR", ihdr_data)
    raw = b""
    for _ in range(height):
        raw += b"\x00" + b"\x00\xff\xff" * width
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


img_para = d.add_paragraph()
img_para.add_run("has image ").add_picture(io.BytesIO(make_png()))
d.add_paragraph("after image")
buf = io.BytesIO()
d.save(buf)
open(r"D:\Project\Clases\tests\fixtures\documents\images.docx", "wb").write(buf.getvalue())

# 1000 paragraphs docx
d = Document()
for i in range(1000):
    d.add_paragraph(f"Para {i}")
buf = io.BytesIO()
d.save(buf)
open(r"D:\Project\Clases\tests\fixtures\documents\large_1000.docx", "wb").write(buf.getvalue())

# 120 pages PDF
big = make_pdf([text_page_latin1(f'Page {i+1}') for i in range(120)])
open(r"D:\Project\Clases\tests\fixtures\documents\large_120_pages.pdf", "wb").write(big)

print("fixtures written OK")

# ---------- now exercise the real module ----------
from src.document_input import (
    DocumentInput,
    DocumentMaterialValidator,
    DocumentValidationResult,
    ParsedDocument,
    DocumentBlock,
    create_document_parser,
    DocumentParser,
)

def round_trip(pd_obj):
    d = pd_obj.to_dict()
    s = d if isinstance(d, str) else __import__("json").dumps(d, ensure_ascii=False)
    pd2 = ParsedDocument.from_dict(__import__("json").loads(s) if not isinstance(d, str) else s)
    assert pd_obj == pd2, "round-trip mismatch"
    return pd2


# PDF validation
v = DocumentMaterialValidator()
r = v.validate(r"D:\Project\Clases\tests\fixtures\documents\simple.pdf")
print("pdf valid:", r.valid, r.extension, r.document_type, r.material.material_type)
assert r.valid
di = v.to_document_input(r)
print("input:", di.path, di.document_type, "material_id:", di.material_id)

parsed = create_document_parser(di.document_type).parse(di)
print("parsed:", parsed.status, "pages:", parsed.page_count, "blocks:", len(parsed.blocks))
assert parsed.status == "PARSED"
assert parsed.page_count == 3
assert len(parsed.blocks) == 3
for b in parsed.blocks:
    print("  blk:", b.block_id, b.block_type, b.page_number, b.text[:20])
assert parsed.blocks[0].page_number == 1
assert parsed.blocks[0].text.strip() == "Hello"
assert parsed.blocks[1].text.strip().startswith("Introducci\u00f3n")
assert parsed.blocks[2].text.strip() == "End"
round_trip(parsed)
print("pdf simple OK; doc_id:", parsed.document_id)

# determinism
p2 = create_document_parser(di.document_type).parse(di)
assert parsed.to_dict() == p2.to_dict()
assert parsed.document_id == p2.document_id
assert all(b.block_id == b2.block_id for b, b2 in zip(parsed.blocks, p2.blocks))
print("determinism OK")

# scanned-like
rs = v.validate(r"D:\Project\Clases\tests\fixtures\documents\scanned_like.pdf")
di_s = v.to_document_input(rs)
ps = create_document_parser("PDF").parse(di_s)
print("scanned:", ps.status, "pages:", ps.page_count, "blocks:", len(ps.blocks),
      "empty_page_count:", ps.metadata.get("empty_page_count"),
      "diagnostics:", [x for x in ps.metadata.get("diagnostics", [])])
assert ps.status == "PARSED_EMPTY"
assert ps.page_count == 3
assert len(ps.blocks) == 0
assert ps.metadata.get("empty_page_count") == 3
assert any("SCANNED_OR_NO_TEXT_LAYER" in d for d in ps.metadata.get("diagnostics", []))
round_trip(ps)
print("scanned OK")

# empty pdf (zero text)
re_ = v.validate(r"D:\Project\Clases\tests\fixtures\documents\empty.pdf")
di_e = v.to_document_input(re_)
pe = create_document_parser("PDF").parse(di_e)
print("empty pdf:", pe.status, "pages:", pe.page_count)
assert pe.status == "PARSED_EMPTY"
assert pe.page_count == 1
assert len(pe.blocks) == 0
round_trip(pe)

# corrupted
rc = v.validate(r"D:\Project\Clases\tests\fixtures\documents\corrupted.pdf")
di_c = v.to_document_input(rc)
pc = create_document_parser("PDF").parse(di_c)
print("corrupt:", pc.status, [e.error_code for e in pc.errors])
assert pc.status == "FAILED"
assert pc.errors and pc.errors[0].error_code == "INVALID_DOCUMENT"
assert pc.blocks == []
round_trip(pc)

# password
rp = v.validate(r"D:\Project\Clases\tests\fixtures\documents\password.pdf")
di_pw = v.to_document_input(rp)
pp = create_document_parser("PDF").parse(di_pw)
print("password:", pp.status, [e.error_code for e in pp.errors])
assert pp.status == "FAILED"
assert pp.errors and pp.errors[0].error_code == "PASSWORD_PROTECTED"
round_trip(pp)

# zero byte
rz = v.validate(r"D:\Project\Clases\tests\fixtures\documents\zero_bytes.pdf")
print("zero bytes:", rz.valid, [e for e in rz.errors])
assert not rz.valid
assert "EMPTY_FILE" in [e.value for e in rz.errors]

# unsupported
ru = v.validate(r"D:\Project\Clases\AGENTS.md")
print("unsupported:", ru.valid, [e.value for e in ru.errors])
assert not ru.valid
assert "UNSUPPORTED_EXTENSION" in [e.value for e in ru.errors]

# missing
rm = v.validate(r"D:\Project\Clases\tests\fixtures\documents\nope.pdf")
print("missing:", rm.valid, [e.value for e in rm.errors])
assert "FILE_NOT_FOUND" in [e.value for e in rm.errors]

# directory
rd = v.validate(r"D:\Project\Clases\tests\fixtures\documents")
print("dir:", rd.valid, [e.value for e in rd.errors])
assert "NOT_A_FILE" in [e.value for e in rd.errors]

# factory unsupported
try:
    create_document_parser("TXT")
    raise AssertionError("should raise")
except ValueError as e:
    print("factory unsupported OK:", e)

# DOCX
rdx = v.validate(r"D:\Project\Clases\tests\fixtures\documents\simple.docx")
print("docx valid:", rdx.valid, rdx.document_type)
assert rdx.valid
di_d = v.to_document_input(rdx)
pd_ = create_document_parser("DOCX").parse(di_d)
print("docx:", pd_.status, "blocks:", len(pd_.blocks), "para_count:", pd_.metadata.get("paragraph_count"))
assert pd_.status == "PARSED"
texts = [b.text for b in pd_.blocks]
print("docx texts:", texts)
types = [b.block_type for b in pd_.blocks]
print("docx types:", types)
assert "HEADING" in types and "PARAGRAPH" in types
assert len(pd_.blocks) == 7
assert all(b.paragraph_index is not None for b in pd_.blocks)
assert all(b.page_number is None for b in pd_.blocks)
assert pd_.metadata.get("table_count") == 0
assert pd_.metadata.get("image_count") == 0
round_trip(pd_)

# empty docx
re2 = v.validate(r"D:\Project\Clases\tests\fixtures\documents\empty.docx")
di_e2 = v.to_document_input(re2)
pe2 = create_document_parser("DOCX").parse(di_e2)
print("empty docx:", pe2.status, "blocks:", len(pe2 := pe2.blocks), "para_count:", pe2.metadata.get("paragraph_count"))
assert pe2.status == "PARSED_EMPTY"
assert pe2.metadata.get("paragraph_count") == 3
assert len(pe2.blocks) == 0
round_trip(pe2)

# corrupted docx
rc2 = v.validate(r"D:\Project\Clases\tests\fixtures\documents\corrupted.docx")
di_c2 = v.to_document_input(rc2)
pc2 = create_document_parser("DOCX").parse(di_c2)
print("corrupt docx:", pc2.status, [e.error_code for e in pc2.errors])
assert pc2.status == "FAILED"
assert "INVALID_DOCUMENT" in [e.error_code for e in pc2.errors]
round_trip(pc2)

# tables
rt = v.validate(r"D:\Project\Clases\tests\fixtures\documents\tables.docx")
di_t = v.to_document_input(rt)
pt = create_document_parser("DOCX").parse(di_t)
print("tables:", pt.status, "table_count:", pt.metadata.get("table_count"), "blocks:", len(pt.blocks))
assert pt.metadata.get("table_count") == 2
assert any(b.block_type == "TABLE" for b in pt.blocks)
assert any("tables_not_extracted" in str(d) for d in pt.metadata.get("diagnostics", []))
round_trip(pt)

# images
ri = v.validate(r"D:\Project\Clases\tests\fixtures\documents\images.docx")
di_i = v.to_document_input(ri)
pi = create_document_parser("DOCX").parse(di_i)
print("images:", pi.metadata.get("image_count"))
assert pi.metadata.get("image_count") == 1
assert all("ocr" not in d.lower() for d in pi.metadata.get("diagnostics", []))
round_trip(pi)

# headings
rh = v.validate(r"D:\Project\Clases\tests\fixtures\documents\headings.docx")
di_h = v.to_document_input(rh)
ph = create_document_parser("DOCX").parse(di_h)
text_by_type = {b.block_type: b.text for b in ph.blocks}
print("headings types:", [(b.block_type, b.text, b.metadata.get("style")) for b in ph.blocks])
assert "just a chapter 1" in [b.text for b in ph.blocks if b.block_type == "PARAGRAPH"]
assert "just a chapter 2" in [b.text for b in ph.blocks if b.block_type == "PARAGRAPH"]
assert text_by_type.get("HEADING") is not None
round_trip(ph)

# large 1000
rl = v.validate(r"D:\Project\Clases\tests\fixtures\documents\large_1000.docx")
di_l = v.to_document_input(rl)
pl = create_document_parser("DOCX").parse(di_l)
print("large1000:", pl.status, "blocks:", len(pl.blocks), "para_count:", pl.metadata.get("paragraph_count"))
assert len(pl.blocks) == 1000
assert pl.blocks[0].text == "Para 0"
assert pl.blocks[999].text == "Para 999"
assert [b.paragraph_index for b in pl.blocks] == list(range(1000))
round_trip(pl)

# large 120 pages
rp3 = v.validate(r"D:\Project\Clases\tests\fixtures\documents\large_120_pages.pdf")
di_p3 = v.to_document_input(rp3)
pp3 = create_document_parser("PDF").parse(di_p3)
print("large120:", pp3.status, "pages:", pp3.page_count, "blocks:", len(pp3.blocks))
assert pp3.page_count == 120
assert len(pp3.blocks) == 120
assert pp3.blocks[0].page_number == 1
assert pp3.blocks[119].page_number == 120
round_trip(pp3)

# multilingual pdf + docx
rm3 = v.validate(r"D:\Project\Clases\tests\fixtures\documents\multilingual.pdf")
di_m3 = v.to_document_input(rm3)
pm3 = create_document_parser("PDF").parse(di_m3)
print("ml pdf blocks:", [b.text for b in pm3.blocks])
assert "Introducci\u00f3n" in pm3.blocks[0].text
assert "Introduccio\u00e0" in pm3.blocks[1].text
assert "Stabilit" in pm3.blocks[3].text
round_trip(pm3)

rm4 = v.validate(r"D:\Project\Clases\tests\fixtures\documents\multilingual.docx")
di_m4 = v.to_document_input(rm4)
pm4 = create_document_parser("DOCX").parse(di_m4)
ml_texts = [b.text for b in pm4.blocks]
print("ml docx blocks:", ml_texts)
assert "Introducci\u00f3n a la teor\u00eda de sistemas (Spanish)" in ml_texts
assert "Introduccio\u00e0 a la teoria dels sistemes (Catalan)" in ml_texts
assert "System-stability tests (system-stability tests)" in ml_texts
assert "Stabilit\u00e4t eines Systems (German)" in ml_texts
round_trip(pm4)

# block id + source reference
b0 = parsed.blocks[0]
print("block source ref keys:", [k for k, v in b0.to_source_reference().items() if v is not None])
print("block id:", b0.block_id)
assert b0.block_id.startswith("docblock-")
assert b0.block_id != parsed.document_id
# determinism check on same block id
p_again = create_document_parser(di.document_type).parse(di)
assert p_again.blocks[0].block_id == b0.block_id

# immutability of input after parse
di_snapshot = di.to_dict()
create_document_parser(di.document_type).parse(di)
assert di.to_dict() == di_snapshot, "input was mutated"

print()
print("=== ALL SMOKE CHECKS PASSED ===")
