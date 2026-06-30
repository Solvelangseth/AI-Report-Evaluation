"""REST capture API + categories (offline, fake provider)."""

import io

import pytest

import categories
import config
from app import app as flask_app

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c49444154789c6360000002000154a24f230000000049454e44ae426082"
)
TRANSCRIPT = ("0-15: Vi er i kjelleren, fukt i vegg 22 %.\n"
              "15-30: På badet, råte ved sluk.")


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_categories_endpoint(client):
    data = client.get("/api/categories").get_json()
    slugs = {c["slug"] for c in data["categories"]}
    assert {"bad", "tak", "kjeller", "grunnmur"} <= slugs


def test_full_capture_flow(client):
    # 1. create
    r = client.post("/api/sessions", json={"title": "Storgata 1", "transcript": TRANSCRIPT})
    assert r.status_code == 201
    sid = r.get_json()["session_id"]

    # 2. add a categorized photo
    r = client.post(f"/api/sessions/{sid}/photos",
                    data={"photo": (io.BytesIO(PNG), "wall.png"),
                          "timestamp": "8", "category": "kjeller", "caption": "vegg"},
                    content_type="multipart/form-data")
    assert r.status_code == 201

    # 3. finalize → draft produced
    r = client.post(f"/api/sessions/{sid}/finalize")
    body = r.get_json()
    assert r.status_code == 200 and body["success"]
    assert body["report_id"] and body["verdict"] in config.QUALITY_LEVELS

    # 4. status reflects the result
    got = client.get(f"/api/sessions/{sid}").get_json()
    assert got["report_id"] == body["report_id"]
    assert got["photo_count"] == 1


def test_photo_rejects_unknown_category(client):
    sid = client.post("/api/sessions", json={"title": "x"}).get_json()["session_id"]
    r = client.post(f"/api/sessions/{sid}/photos",
                    data={"photo": (io.BytesIO(PNG), "p.png"), "category": "garasje"},
                    content_type="multipart/form-data")
    assert r.status_code == 400


def test_finalize_without_inputs_fails_cleanly(client):
    sid = client.post("/api/sessions", json={"title": "tom"}).get_json()["session_id"]
    r = client.post(f"/api/sessions/{sid}/finalize")
    assert r.status_code == 422
    assert r.get_json()["status"] == "failed"
    # And the failure is readable on the status endpoint.
    assert client.get(f"/api/sessions/{sid}").get_json()["error"]


def test_token_required_when_configured(client, monkeypatch):
    monkeypatch.setattr(config, "CAPTURE_API_TOKEN", "s3cret")
    assert client.get("/api/categories").status_code == 401
    ok = client.get("/api/categories", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


def _finalized_session(client):
    sid = client.post("/api/sessions",
                      json={"title": "F", "transcript": TRANSCRIPT}).get_json()["session_id"]
    client.post(f"/api/sessions/{sid}/finalize")
    return sid


def test_findings_are_persisted_and_listed(client):
    sid = _finalized_session(client)
    findings = client.get(f"/api/sessions/{sid}/findings").get_json()["findings"]
    assert findings and {"kjeller", "bad"} & {f["part"] for f in findings}


def test_edit_findings_then_recompose(client):
    sid = _finalized_session(client)
    before = client.get(f"/api/sessions/{sid}").get_json()["report_id"]
    fid = client.get(f"/api/sessions/{sid}/findings").get_json()["findings"][0]["id"]

    # Edit a finding.
    r = client.put(f"/api/sessions/{sid}/findings/{fid}",
                   json={"severity": "TG3", "recommendation": "strakstiltak"})
    assert r.status_code == 200 and r.get_json()["finding"]["severity"] == "TG3"

    # Add and delete a finding.
    new_id = client.post(f"/api/sessions/{sid}/findings",
                         json={"part": "tak", "observation": "ny"}).get_json()["finding"]["id"]
    assert client.delete(f"/api/sessions/{sid}/findings/{new_id}").status_code == 200

    # Recompose → a fresh draft report.
    r = client.post(f"/api/sessions/{sid}/recompose")
    assert r.status_code == 200
    assert r.get_json()["report_id"] != before


def test_update_finding_rejects_bad_severity(client):
    sid = _finalized_session(client)
    fid = client.get(f"/api/sessions/{sid}/findings").get_json()["findings"][0]["id"]
    assert client.put(f"/api/sessions/{sid}/findings/{fid}",
                      json={"severity": "TG9"}).status_code == 400


def test_background_finalize_processes_async(client):
    import time
    sid = client.post("/api/sessions",
                      json={"title": "BG", "transcript": TRANSCRIPT}).get_json()["session_id"]
    r = client.post(f"/api/sessions/{sid}/finalize", json={"background": True})
    assert r.status_code == 202 and r.get_json()["status"] == "processing"

    # Poll until the worker finishes (fake provider is fast).
    for _ in range(50):
        status = client.get(f"/api/sessions/{sid}").get_json()
        if status["status"] != "processing":
            break
        time.sleep(0.1)
    assert status["status"] in config.QUALITY_LEVELS and status["report_id"]
