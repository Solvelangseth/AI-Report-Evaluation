import io

import pytest

from app import app as flask_app


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_dashboard_loads(client):
    assert client.get("/").status_code == 200


def test_reports_page_loads(client):
    assert client.get("/reports").status_code == 200


def test_stats_api(client):
    data = client.get("/api/stats").get_json()
    assert "accuracy" in data and "total_reports" in data


def test_upload_rejects_bad_extension(client):
    resp = client.post("/upload", data={"file": (io.BytesIO(b"x"), "bad.exe")},
                       content_type="multipart/form-data")
    assert resp.status_code == 400


def test_upload_and_evaluate_offline(client):
    report = b"### Sammendrag\nKjelleren er litt fuktig og kanskje lekkasje. litt litt."
    resp = client.post("/upload",
                       data={"file": (io.BytesIO(report), "demo.txt"), "topic": "fukt"},
                       content_type="multipart/form-data")
    assert resp.get_json()["success"] is True

    # LLM_PROVIDER=fake → evaluation runs offline using rule-based QA only.
    ev = client.post("/evaluate", json={}).get_json()
    assert ev["success"] is True


def _seed_evaluated_report(client):
    report = b"### Sammendrag\nKjelleren er litt fuktig og kanskje lekkasje."
    rid = client.post("/upload",
                      data={"file": (io.BytesIO(report), "rev.txt")},
                      content_type="multipart/form-data").get_json()["report_id"]
    client.post("/evaluate", json={})
    return rid


def test_review_override_is_recorded(client):
    rid = _seed_evaluated_report(client)
    resp = client.post(f"/report/{rid}/review",
                      json={"decision": "overridden", "corrected_quality": "clean",
                            "note": "looks fine"}).get_json()
    assert resp["success"] is True
    assert resp["corrected_quality"] == "clean"
    # The decision is reflected on the detail page.
    assert b"Overridden" in client.get(f"/report/{rid}").data or \
           b"Accept verdict" in client.get(f"/report/{rid}").data


def test_review_invalid_label_rejected(client):
    rid = _seed_evaluated_report(client)
    resp = client.post(f"/report/{rid}/review",
                      json={"decision": "overridden", "corrected_quality": "bogus"})
    assert resp.status_code == 400


def test_review_without_qa_result_rejected(client):
    rid = client.post("/upload",
                     data={"file": (io.BytesIO(b"### Sammendrag\nx"), "noqa.txt")},
                     content_type="multipart/form-data").get_json()["report_id"]
    resp = client.post(f"/report/{rid}/review", json={"decision": "accepted"})
    assert resp.status_code == 400


def test_curate_endpoint_adds_example(client):
    rid = _seed_evaluated_report(client)
    client.post(f"/report/{rid}/review",
               json={"decision": "overridden", "corrected_quality": "clean", "note": "fine"})
    resp = client.post("/curate", json={"report_id": rid}).get_json()
    assert resp["success"] is True and "example_id" in resp
    # Detail page now shows it's in the knowledge base.
    assert b"knowledge base" in client.get(f"/report/{rid}").data


def test_curate_requires_an_override(client):
    rid = _seed_evaluated_report(client)
    client.post(f"/report/{rid}/review", json={"decision": "accepted"})
    resp = client.post("/curate", json={"report_id": rid})
    assert resp.status_code == 400
