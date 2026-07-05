"""Persistence + web route for the capture → authoring flow (offline)."""

import io

import pytest

import capture_store
from app import app as flask_app
from capture import Photo, parse_transcript
from db_setup import CaptureSession, MediaItem, QAResult, Report, TranscriptSegment
from intake import run_from_transcript


TRANSCRIPT = (
    "0-15: Vi er i kjelleren. Fuktmåling viser 22 % i vegg ved gulvnivå, manglende drenering.\n"
    "15-30: På badet er det alvorlig råteskade ved sluk, om lag 0,6 m²."
)


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_parse_transcript_honours_time_windows():
    t = parse_transcript(TRANSCRIPT)
    assert len(t.segments) == 2
    assert (t.segments[0].start, t.segments[0].end) == (0, 15)
    assert "kjeller" in t.segments[0].text.lower()


def test_parse_transcript_plain_lines_advance_clock():
    t = parse_transcript("første\nandre", segment_seconds=10)
    assert [(s.start, s.end) for s in t.segments] == [(0, 10), (10, 20)]


def test_save_session_persists_inputs_and_draft(session):
    result = run_from_transcript(parse_transcript(TRANSCRIPT),
                                 [Photo(id="p1", timestamp=8.0, caption="fuktmåler")],
                                 provider="fake", rag=None)
    cap = capture_store.save_session(
        session, "Befaring test", result, provider="fake",
        media=[{"filename": "p1.jpg", "timestamp": 8.0, "caption": "fuktmåler"}])

    # Capture inputs persisted.
    assert session.query(CaptureSession).filter_by(id=cap.id).first() is not None
    assert session.query(TranscriptSegment).filter_by(session_id=cap.id).count() == 2
    assert session.query(MediaItem).filter_by(session_id=cap.id).count() == 1

    # Draft report + QA result persisted and linked.
    report = session.query(Report).filter_by(id=cap.report_id).first()
    assert report is not None and report.source == "authored"
    qa = session.query(QAResult).filter_by(report_id=report.id).first()
    assert qa is not None and qa.final_quality == cap.status


def test_capture_get_renders(client):
    assert client.get("/capture").status_code == 200


def test_sessions_list_renders(client):
    # Create one via the web form so a row exists, then list it.
    client.post("/capture", data={"title": "Listed", "transcript": TRANSCRIPT},
                content_type="multipart/form-data")
    resp = client.get("/sessions")
    assert resp.status_code == 200 and b"Listed" in resp.data


def test_capture_get_offers_category_picker(client):
    html = client.get("/capture").get_data(as_text=True)
    assert 'name="photo_category"' in html and "Bad / våtrom" in html


def test_capture_form_persists_photo_category(client, session):
    import io
    from db_setup import MediaItem
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
        "53de0000000c49444154789c6360000002000154a24f230000000049454e44ae426082")
    resp = client.post("/capture", data={
        "title": "Cat web", "transcript": TRANSCRIPT,
        "photos": (io.BytesIO(png), "w.png"),
        "photo_timestamp": "8", "photo_category": "kjeller", "photo_caption": "vegg",
    }, content_type="multipart/form-data")
    assert resp.status_code == 302
    item = session.query(MediaItem).filter_by(caption="vegg").first()
    assert item is not None and item.category == "kjeller"


def test_capture_requires_some_input(client):
    resp = client.post("/capture", data={"title": "Tom"},
                       content_type="multipart/form-data")
    assert resp.status_code == 400


def test_capture_creates_session_and_redirects(client):
    resp = client.post("/capture",
                       data={"title": "Befaring web", "transcript": TRANSCRIPT},
                       content_type="multipart/form-data")
    assert resp.status_code == 302
    assert "/session/" in resp.headers["Location"]
    # The session page renders.
    assert client.get(resp.headers["Location"]).status_code == 200


def test_media_route_serves_uploaded_file(client):
    import config
    config.ensure_dirs()
    (config.UPLOAD_DIR / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert client.get("/media/shot.png").status_code == 200


def test_media_route_blocks_traversal(client):
    # secure_filename strips path components, so traversal can't escape uploads.
    assert client.get("/media/..%2f..%2fapp.py").status_code in (404, 308)


def test_session_page_renders_photo_imgs(client, session):
    result = run_from_transcript(parse_transcript(TRANSCRIPT), [], provider="fake", rag=None)
    cap = capture_store.save_session(
        session, "Med bilde", result, provider="fake",
        media=[{"filename": "shot.png", "timestamp": 8.0, "caption": "vegg"}])
    html = client.get(f"/session/{cap.id}").get_data(as_text=True)
    assert "/media/shot.png" in html and "<img" in html
