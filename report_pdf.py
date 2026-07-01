"""
Render a report to a clean A4 PDF — the actual deliverable.

Takes a `Report` row (any source) and lays out the six standard sections, then,
for authored drafts, the evidence photos from the linked capture session, and
finally the indicative-cost disclaimer. Uses fpdf2 core fonts (no bundled TTF),
so text is sanitised to latin-1 — Norwegian æøå and m² are latin-1, but smart
punctuation (– — “ ”) is transliterated first so it never breaks encoding.
"""

from io import BytesIO

from fpdf import FPDF
from fpdf.enums import XPos, YPos

import config
import prices
from qa_engine import extract_sections

_SECTION_TITLES = {
    "sammendrag": "Sammendrag",
    "observasjoner": "Observasjoner",
    "årsak": "Årsak",
    "konsekvenser": "Konsekvenser",
    "anbefalinger": "Anbefalinger",
    "kostnadsestimat": "Kostnadsestimat",
}
_SECTION_ORDER = list(_SECTION_TITLES)

_TRANSLIT = {
    "–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'",
    "→": "->", "•": "-", "…": "...", " ": " ",
}


def _latin1(text: str) -> str:
    """Make text safe for fpdf core fonts (latin-1), transliterating smart punctuation."""
    for bad, good in _TRANSLIT.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "replace").decode("latin-1")


def _write(pdf, height, text):
    """multi_cell that returns the cursor to the left margin (fpdf2 leaves it at
    the right edge by default, which breaks the next width-0 cell)."""
    pdf.multi_cell(0, height, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def build_report_pdf(report, capture_session=None) -> bytes:
    """Return the report rendered as PDF bytes."""
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(18, 16, 18)
    pdf.add_page()

    # Header
    pdf.set_font("Helvetica", "B", 16)
    _write(pdf, 9, _latin1(report.topic or "Tilstandsrapport"))
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(110, 110, 110)
    _write(pdf, 6, _latin1(f"Utkast - {report.created_at:%Y-%m-%d} - "
                           f"QA: {report.status.replace('_', ' ')}"))
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    # Sections (in the standard order; skip any that are absent)
    sections = extract_sections(report.report_text or "")
    for key in _SECTION_ORDER:
        body = sections.get(key)
        if not body:
            continue
        pdf.set_font("Helvetica", "B", 12)
        _write(pdf, 7, _latin1(_SECTION_TITLES[key]))
        pdf.set_font("Helvetica", "", 11)
        _write(pdf, 6, _latin1(body))
        pdf.ln(2)

    # Evidence photos (authored drafts only)
    media = getattr(capture_session, "media", None) if capture_session else None
    photos = [m for m in (media or []) if m.filename]
    if photos:
        pdf.set_font("Helvetica", "B", 12)
        _write(pdf, 7, _latin1("Vedlegg: bilder"))
        pdf.ln(1)
        for m in photos:
            try:
                pdf.image(str(config.UPLOAD_DIR / m.filename), w=85)
            except Exception:  # noqa: BLE001 - skip unreadable/unsupported images
                continue
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(110, 110, 110)
            _write(pdf, 5, _latin1(f"{m.timestamp:.0f}s  {m.caption or ''}".strip()))
            pdf.set_text_color(0, 0, 0)
            pdf.ln(2)

    # Disclaimer
    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(120, 120, 120)
    _write(pdf, 4, _latin1(prices.DISCLAIMER))

    out = pdf.output()
    return bytes(out) if isinstance(out, (bytes, bytearray)) else BytesIO(out).getvalue()
