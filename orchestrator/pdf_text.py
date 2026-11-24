"""Page-aware document text, and locating quoted evidence in it.

The intake parser (Gemini or regex) says where each value came from by quoting the
document. Nothing here trusts that quote: locate_quote() searches the real page text
and returns the page, line and word boxes only when the text is actually there.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from contract.normalize import US_STATE_CODES, US_STATES

logger = logging.getLogger(__name__)

# Words whose tops are within this many points share a line.
_LINE_TOLERANCE = 3.0


@dataclass
class Word:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    char_start: int  # offset of the word in PageText.text
    char_end: int


@dataclass
class PageText:
    page: Optional[int]  # 1-based PDF page; None for plain-text documents
    text: str
    words: List[Word] = field(default_factory=list)
    width: Optional[float] = None
    height: Optional[float] = None


@dataclass
class DocumentText:
    pages: List[PageText]
    kind: str  # "pdf" or "text"

    @property
    def has_text_layer(self) -> bool:
        return any(p.text.strip() for p in self.pages)

    @property
    def full_text(self) -> str:
        return "\n".join(p.text for p in self.pages)


@dataclass
class Located:
    page: Optional[int]
    line: Optional[int]  # 1-based line within the page (or document, for text)
    char_start: int
    char_end: int
    bboxes: List[Tuple[float, float, float, float]]  # (x0, top, x1, bottom), PDF points


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _group_lines(words: Iterable[dict]) -> List[List[dict]]:
    lines: List[List[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= _LINE_TOLERANCE:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def _page_from_words(page_no: int, raw_words: Sequence[dict], width: float, height: float) -> PageText:
    parts: List[str] = []
    words: List[Word] = []
    pos = 0
    for li, line in enumerate(_group_lines(raw_words)):
        if li:
            parts.append("\n")
            pos += 1
        for wi, w in enumerate(line):
            if wi:
                parts.append(" ")
                pos += 1
            text = w["text"]
            words.append(Word(text, float(w["x0"]), float(w["top"]), float(w["x1"]), float(w["bottom"]), pos, pos + len(text)))
            parts.append(text)
            pos += len(text)
    return PageText(page=page_no, text="".join(parts), words=words, width=float(width), height=float(height))


def extract_pdf(pdf_bytes: bytes) -> DocumentText:
    """Read each PDF page's words and positions. Raises if the bytes aren't a readable PDF."""
    import pdfplumber

    pages: List[PageText] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            raw = page.extract_words(keep_blank_chars=False, use_text_flow=False)
            pages.append(_page_from_words(i, raw, page.width, page.height))
    return DocumentText(pages=pages, kind="pdf")


def text_document(text: str) -> DocumentText:
    return DocumentText(pages=[PageText(page=None, text=text)], kind="text")


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

_CHAR_MAP = {
    "‘": "'", "’": "'", "′": "'", "´": "'", "`": "'",
    "“": '"', "”": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    " ": " ",
}


def _normalize_with_map(text: str) -> Tuple[str, List[int]]:
    """Casefold, unify quotes/dashes, collapse whitespace; map each output char to its source index."""
    out: List[str] = []
    index: List[int] = []
    prev_space = True  # also drops leading whitespace
    for i, ch in enumerate(text):
        ch = _CHAR_MAP.get(ch, ch)
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            index.append(i)
            prev_space = True
            continue
        for c in ch.casefold():
            out.append(c)
            index.append(i)
        prev_space = False
    if out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


def normalize_for_match(text: str) -> str:
    return _normalize_with_map(text)[0]


def _boxes(page: PageText, start: int, end: int) -> List[Tuple[float, float, float, float]]:
    hits = [w for w in page.words if w.char_end > start and w.char_start < end]
    lines: List[List[Word]] = []
    for w in hits:
        if lines and abs(lines[-1][0].top - w.top) <= _LINE_TOLERANCE:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [
        (min(w.x0 for w in ln), min(w.top for w in ln), max(w.x1 for w in ln), max(w.bottom for w in ln))
        for ln in lines
    ]


def _continues_number(hay: str, i: int, step: int) -> bool:
    """True if hay[i] is a decimal point or thousands comma with a digit beyond it (i + step)."""
    j = i + step
    return 0 <= i < len(hay) and 0 <= j < len(hay) and hay[i] in ".," and hay[j].isdigit()


def _find_in_page(page: PageText, needle: str) -> Optional[Tuple[int, int]]:
    hay, index = _normalize_with_map(page.text)
    at = hay.find(needle)
    while at >= 0:
        end = at + len(needle)
        # A quote must not start or end in the middle of a word or number:
        # "Occupant Load: 14" must not match inside "Occupant Load: 140", and
        # "Setback: 20" / "Load: 1" must not match inside "20.5" / "1,400".
        starts_ok = not (needle[0].isalnum() and at > 0 and hay[at - 1].isalnum()) and not (
            needle[0].isdigit() and _continues_number(hay, at - 1, -1)
        )
        ends_ok = not (needle[-1].isalnum() and end < len(hay) and hay[end].isalnum()) and not (
            needle[-1].isdigit() and _continues_number(hay, end, 1)
        )
        if starts_ok and ends_ok:
            return index[at], index[end - 1] + 1
        at = hay.find(needle, at + 1)
    return None


def locate_quote(doc: DocumentText, quote: Optional[str], page_hint: Optional[int] = None) -> Optional[Located]:
    """Find a quote in the document's real text; None if it isn't there.

    Matching ignores case, whitespace runs/line breaks and typographic quote or dash
    variants, nothing else. The hinted page is searched first, then every page.
    """
    needle = normalize_for_match(quote or "")
    if len(needle) < 2:
        return None
    order = list(doc.pages)
    if page_hint is not None:
        order.sort(key=lambda p: p.page != page_hint)
    for page in order:
        span = _find_in_page(page, needle)
        if span is None:
            continue
        start, end = span
        return Located(
            page=page.page,
            line=page.text.count("\n", 0, start) + 1,
            char_start=start,
            char_end=end,
            bboxes=_boxes(page, start, end),
        )
    return None


def line_at(text: str, start: int, end: int) -> str:
    """The full source line(s) containing text[start:end]."""
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    return text[ls: le if le >= 0 else len(text)].strip()


# ---------------------------------------------------------------------------
# Does the quote support the value?
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?|\.\d+")
# 18'-6"  18' 6 1/2"  25'  (feet, optional inches with an optional fraction)
_FEET_INCHES = re.compile(
    r"(\d+(?:\.\d+)?)\s*['’′]"
    r"(?:\s*-?\s*(\d+(?:\.\d+)?)?(?:[\s-]*(\d+)/(\d+))?\s*(?:[\"”″]|in\b))?"
)
_MIXED_FRACTION = re.compile(r"(\d+)[\s-]+(\d+)/(\d+)")
_FRACTION = re.compile(r"(\d+)/(\d+)")
_SCALED = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k|m|thousand|million)\b", re.IGNORECASE)
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}

# Converted values (inches to feet, fractions) are compared loosely: Gemini may round them.
_EXACT_TOL = 1e-6
_CONVERTED_TOL = 0.005


def _frac(num: str, den: str) -> Optional[float]:
    d = float(den)
    return float(num) / d if d else None


def _numbers(text: str) -> List[Tuple[float, float]]:
    """Every number the text states, as (value, tolerance).

    Feet-and-inches and fractions are read as one number, and their parts are not
    counted on their own: "18'-6\"" states 18.5, not 18 or 6.
    """
    out: List[Tuple[float, float]] = []
    work = text

    def consume(pattern: re.Pattern, read) -> None:
        nonlocal work
        def sub(m: re.Match) -> str:
            val = read(m)
            if val is not None:
                out.append((val, _CONVERTED_TOL))
            return " " * len(m.group(0))
        work = pattern.sub(sub, work)

    def feet_inches(m: re.Match) -> Optional[float]:
        ft = float(m.group(1))
        inches = float(m.group(2)) if m.group(2) else 0.0
        if m.group(3):
            frac = _frac(m.group(3), m.group(4))
            if frac is None:
                return None
            inches += frac
        return ft + inches / 12

    def mixed(m: re.Match) -> Optional[float]:
        frac = _frac(m.group(2), m.group(3))
        return None if frac is None else float(m.group(1)) + frac

    consume(_FEET_INCHES, feet_inches)
    consume(_MIXED_FRACTION, mixed)
    consume(_FRACTION, lambda m: _frac(m.group(1), m.group(2)))

    for m in _SCALED.finditer(work):  # "$1.2M": keep 1.2 (below) and add 1,200,000
        out.append((float(m.group(1).replace(",", "")) * _SCALE[m.group(2).lower()], _EXACT_TOL))

    for tok in _NUMBER.findall(work):
        try:
            out.append((float(tok.replace(",", "")), _EXACT_TOL))
        except ValueError:
            continue
    return out


def _state_names(code: str) -> List[str]:
    return [name for name, c in US_STATES.items() if c == code]


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold().replace("_", " ")))


def value_in_quote(value: Any, quote: str) -> Optional[bool]:
    """Whether the quoted text states this value. None when that can't be judged from text.

    Numbers must appear as a number in the quote (commas ignored; feet-and-inches,
    fractions and k/M suffixes are converted); a quote with no number can't be judged.
    Strings and enum values must appear as words, and a state code may appear as its
    name. Booleans are interpretations ("not required" -> False), so they are left to
    the worker.
    """
    if value is None or isinstance(value, bool):
        return None
    if hasattr(value, "value"):  # Enum
        value = value.value
    if isinstance(value, (int, float)):
        numbers = _numbers(quote)
        if not numbers:
            return None  # "half acre": no number to compare against
        return any(abs(n - float(value)) < tol for n, tol in numbers)
    words = _words(str(value))
    if not words:
        return None
    candidates = [words]
    if str(value) in US_STATE_CODES:  # "CA" is stated by "California"
        candidates += [_words(name) for name in _state_names(str(value))]
    haystack = f" {_words(quote)} "
    return any(f" {w} " in haystack for w in candidates)
