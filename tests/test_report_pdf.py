"""PDF export of a report (offline)."""

import io
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

import config
import report_pdf
from app import app as flask_app
from capture import parse_transcript
from capture_store import save_session
from db_setup import Report
from intake import run_from_transcript


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


# Norwegian chars + smart punctuation that must not break latin-1 encoding.
REPORT_TEXT = (
    "Sammendrag\nBoligen har fukt i kjeller – TG2. Måling viser 22 % i vegg.\n\n"
    "Observasjoner\n- Fukt 22 % i kjellervegg over 1,5 m².\n\n"
    "Årsak\nManglende drenering.\n\n"
    "Konsekvenser\nRisiko for råte.\n\n"
    "Anbefalinger\n- Etabler drenering.\n\n"
    "Kostnadsestimat\n30 000–60 000 kr."
)


def _fake_report():
    return SimpleNamespace(id=1, topic="Befaring Storgata", status="minor_error",
                           source="generated", report_text=REPORT_TEXT,
                           created_at=datetime(2026, 6, 30, tzinfo=timezone.utc))


def test_build_report_pdf_returns_pdf_bytes():
    data = report_pdf.build_report_pdf(_fake_report())
    assert isinstance(data, bytes) and data[:5] == b"%PDF-"
    assert len(data) > 800  # non-trivial document


def test_latin1_transliterates_smart_punctuation():
    out = report_pdf._latin1("fukt – 22 % “m²” — råte")
    assert "–" not in out and "—" not in out and "“" not in out
    assert "råte" in out and "m²" in out  # Norwegian + latin-1 superscript survive


def test_pdf_route_serves_pdf(client, session):
    report = Report(filename="r.md", topic="Test", status="clean", source="generated",
                    report_text=REPORT_TEXT, generator_version="t", model="t")
    session.add(report)
    session.commit()
    resp = client.get(f"/report/{report.id}/pdf")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.get_data()[:5] == b"%PDF-"


def test_pdf_includes_evidence_photos_for_authored(client, session):
    # An authored draft with a real photo file → PDF embeds it without error.
    config.ensure_dirs()
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
        "53de0000000c49444154789c6360000002000154a24f230000000049454e44ae426082")
    (config.UPLOAD_DIR / "ev.png").write_bytes(png)
    result = run_from_transcript(parse_transcript("0-10: Fukt i kjeller 22 %."),
                                 [], provider="fake", rag=None)
    cap = save_session(session, "Med bilde", result, provider="fake",
                       media=[{"filename": "ev.png", "timestamp": 5.0, "caption": "vegg"}])
    resp = client.get(f"/report/{cap.report_id}/pdf")
    assert resp.status_code == 200 and resp.get_data()[:5] == b"%PDF-"
