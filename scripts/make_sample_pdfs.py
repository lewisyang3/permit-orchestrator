"""Regenerate the sample plan PDFs in fixtures/sample_plans/.

Each PDF is a single US-letter page with one text line per non-blank source line,
laid out top to bottom (T* advances the line), so page-aware extractors such as
pdfplumber recover the same lines with real positions. No third-party dependencies.

    uv run python scripts/make_sample_pdfs.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence, Union

SAMPLES = Path(__file__).resolve().parent.parent / "fixtures" / "sample_plans"

INCOMPLETE_PLAN = """\
PERMIT APPLICATION & ARCHITECTURAL SPECIFICATIONS
Project Title: Incomplete Storage Shed Addition
Property Location:
Address: 100 Main St
City: San Jose
State: CA
Zip: 95125
Zoning Parameters:
Zone District: R-1
Building Height: 12.0 ft
"""

FONT_SIZE = 9
LEADING = 13
LEFT = 40
TOP = 760


def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content_stream(text: str) -> bytes:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    ops = [f"BT /F1 {FONT_SIZE} Tf {LEADING} TL {LEFT} {TOP} Td"]
    ops += [f"({_escape(ln)}) Tj T*" for ln in lines]
    ops.append("ET")
    return "\n".join(ops).encode("latin-1")


def build_pdf(text: Union[str, Sequence[str]]) -> bytes:
    """A PDF with one page per string (a single string makes one page)."""
    pages = [text] if isinstance(text, str) else list(text)
    n = len(pages)
    # Objects: 1 catalog, 2 pages, 3 font, then (page, contents) pairs.
    page_ids = [4 + 2 * i for i in range(n)]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % pid for pid in page_ids) + b"] /Count %d >>" % n,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    for pid, page_text in zip(page_ids, pages):
        stream = _content_stream(page_text)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
            b"/Resources << /Font << /F1 3 0 R >> >> >>" % (pid + 1)
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def main() -> None:
    sources = {
        "residential_rear_addition.pdf": (SAMPLES / "residential_rear_addition.txt").read_text(encoding="utf-8"),
        "incomplete_plan.pdf": INCOMPLETE_PLAN,
    }
    for name, text in sources.items():
        (SAMPLES / name).write_bytes(build_pdf(text))
        print(f"wrote {SAMPLES / name}")


if __name__ == "__main__":
    main()
