"""
Flask web application for the inspection report QA system.

Handles uploads, evaluation, and the dashboard. Evaluation logic lives in
qa_engine/qa_master; this module is the web layer only.
"""

import os
import re
import json
import secrets
import threading
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Flask, render_template, jsonify, request, redirect, flash, send_from_directory,
    abort, Response,
)
from flask_cors import CORS
from werkzeug.utils import secure_filename
from sqlalchemy import desc
import markdown
import bleach
from markupsafe import Markup, escape

import config
import reviews
import scoring
import curation
import capture
import categories
import intake
import capture_store
import report_pdf
import tg_summary
from db_setup import (
    get_session, Report, QAResult, QAIssue, CaptureSession, MediaItem, SessionFinding,
    seed_rag_examples, seed_regulations,
)
from judge import JudgeError
from qa_master import QAEvaluator
from rag_pipeline import RAGPipeline

app = Flask(__name__)
CORS(app)
app.config["SECRET_KEY"] = config.FLASK_SECRET_KEY or secrets.token_hex(32)
app.config["UPLOAD_FOLDER"] = str(config.UPLOAD_DIR)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

# Ensure directories and schema exist, and seed RAG examples + regulation
# references once at startup.
config.ensure_dirs()
seed_rag_examples()
seed_regulations()


def get_db():
    return get_session()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in config.ALLOWED_EXTENSIONS


def _quality_counts(db):
    return {
        q: db.query(QAResult).filter(QAResult.final_quality == q).count()
        for q in config.QUALITY_LEVELS
    }


@app.route("/")
def index():
    db = get_db()
    try:
        counts = _quality_counts(db)
        stats = {
            "total_reports": db.query(Report).count(),
            "total_evaluated": db.query(QAResult).count(),
            "clean_count": counts["clean"],
            "minor_count": counts["minor_error"],
            "major_count": counts["major_error"],
            "accuracy": reviews.accuracy(db)["accuracy"],
            "pending_count": db.query(Report).outerjoin(QAResult)
                               .filter(QAResult.id == None).count(),  # noqa: E711
            **reviews.review_stats(db),
            **reviews.triage_stats(db),
        }
        return render_template("index.html", stats=stats)
    finally:
        db.close()


@app.route("/upload", methods=["GET", "POST"])
def upload_file():
    if request.method == "POST":
        if "file" not in request.files or request.files["file"].filename == "":
            flash("No file selected")
            return redirect(request.url)

        file = request.files["file"]
        if not allowed_file(file.filename):
            return jsonify({"success": False,
                            "message": "Invalid file type. Allowed: txt, pdf, docx, json"}), 400

        filename = secure_filename(file.filename)
        base_name, extension = filename.rsplit(".", 1)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Random suffix so same-named uploads within one second don't collide.
        unique_filename = f"{base_name}_{timestamp}_{secrets.token_hex(3)}.{extension}"
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], unique_filename)
        file.save(filepath)

        try:
            report_text = extract_text_from_file(filepath, extension)
        except Exception as exc:
            return jsonify({"success": False, "message": f"Error processing file: {exc}"}), 400

        db = get_db()
        try:
            report = Report(
                filename=unique_filename,
                topic=request.form.get("topic", base_name),
                status="pending",
                source="upload",
                report_text=report_text,
                generator_version="upload_v1",
                model="user_upload",
                created_at=datetime.now(timezone.utc),
            )
            db.add(report)
            db.commit()
            return jsonify({"success": True,
                            "message": f'File "{filename}" uploaded successfully',
                            "report_id": report.id})
        finally:
            db.close()

    return render_template("upload.html")


def extract_text_from_file(filepath, extension):
    """Extract text content from an uploaded file."""
    if extension == "txt":
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    if extension == "json":
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "report_text" in data:
            return data["report_text"]
        return json.dumps(data, ensure_ascii=False, indent=2)
    if extension == "pdf":
        try:
            import PyPDF2
        except ImportError:
            raise RuntimeError("PDF support requires PyPDF2.")
        with open(filepath, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            return "\n".join((page.extract_text() or "") for page in reader.pages)
    if extension == "docx":
        try:
            from docx import Document
        except ImportError:
            raise RuntimeError("DOCX support requires python-docx.")
        doc = Document(filepath)
        return "\n".join(p.text for p in doc.paragraphs)
    raise ValueError("Unsupported file type")


ALLOWED_IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "heic"}
ALLOWED_AUDIO_EXT = {"m4a", "mp3", "wav", "ogg", "webm", "mp4"}


def _save_upload(file_storage, allowed_ext) -> str:
    """Save an uploaded file under a collision-proof name; return the filename."""
    name = secure_filename(file_storage.filename)
    base, _, ext = name.rpartition(".")
    if ext.lower() not in allowed_ext:
        raise ValueError(f"Unsupported file type: .{ext}")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique = f"{base}_{stamp}_{secrets.token_hex(3)}.{ext}"
    file_storage.save(os.path.join(app.config["UPLOAD_FOLDER"], unique))
    return unique


@app.route("/capture", methods=["GET", "POST"])
def capture_session():
    """Author a draft report from a narrated transcript + timestamped photos."""
    if request.method == "GET":
        return render_template("capture.html", categories=categories.CATEGORIES)

    title = (request.form.get("title") or "").strip() or "Befaring"
    transcript_text = (request.form.get("transcript") or "").strip()
    provider = config.LLM_PROVIDER

    db = get_db()
    try:
        # Photos: file + per-row timestamp + caption, aligned by index.
        files = request.files.getlist("photos")
        tss = request.form.getlist("photo_timestamp")
        caps = request.form.getlist("photo_caption")
        cats = request.form.getlist("photo_category")
        photos, media = [], []
        for i, f in enumerate(files):
            if not f or not f.filename:
                continue
            try:
                fname = _save_upload(f, ALLOWED_IMAGE_EXT)
            except ValueError as exc:
                return render_template("capture.html", categories=categories.CATEGORIES,
                                       error=str(exc)), 400
            ts = float(tss[i]) if i < len(tss) and tss[i].strip() else 0.0
            cap = caps[i] if i < len(caps) else ""
            cat = cats[i] if i < len(cats) and categories.is_valid(cats[i]) else None
            path = os.path.join(app.config["UPLOAD_FOLDER"], fname)
            photos.append(capture.Photo(id=fname, timestamp=ts, image_path=path,
                                        caption=cap, category=cat or ""))
            media.append({"filename": fname, "timestamp": ts, "caption": cap, "category": cat})

        # Transcript: pasted text wins; otherwise transcribe an uploaded audio file.
        audio_filename = None
        audio_file = request.files.get("audio")
        if transcript_text:
            transcript = capture.parse_transcript(transcript_text)
        elif audio_file and audio_file.filename:
            try:
                audio_filename = _save_upload(audio_file, ALLOWED_AUDIO_EXT)
            except ValueError as exc:
                return render_template("capture.html", categories=categories.CATEGORIES,
                                       error=str(exc)), 400
            audio_path = os.path.join(app.config["UPLOAD_FOLDER"], audio_filename)
            try:
                transcript = capture.get_transcriber(provider).transcribe(audio_path)
            except capture.CaptureError as exc:
                return render_template("capture.html", categories=categories.CATEGORIES,
                                       error=str(exc)), 400
        else:
            return render_template(
                "capture.html", categories=categories.CATEGORIES,
                error="Provide a transcript, or upload audio (needs a real LLM provider)."), 400

        try:
            result = intake.run_from_transcript(
                transcript, photos, provider=provider, rag=RAGPipeline(db))
        except Exception as exc:  # noqa: BLE001 - surface intake/LLM failures to the page
            return render_template("capture.html", categories=categories.CATEGORIES,
                                   error=f"Intake failed: {exc}"), 500

        if not result.findings:
            return render_template(
                "capture.html", categories=categories.CATEGORIES,
                error="No findings could be extracted — add more detail to the transcript."), 400

        capture_row = capture_store.save_session(
            db, title, result, provider=provider,
            audio_filename=audio_filename, media=media)
        return redirect(f"/session/{capture_row.id}")
    finally:
        db.close()


@app.route("/sessions")
def sessions_list():
    db = get_db()
    try:
        rows = db.query(CaptureSession).order_by(desc(CaptureSession.created_at)).all()
        return render_template("sessions.html", sessions=rows)
    finally:
        db.close()


@app.route("/session/<int:session_id>")
def session_detail(session_id):
    db = get_db()
    try:
        capture_row = db.query(CaptureSession).filter(CaptureSession.id == session_id).first()
        if not capture_row:
            return "Session not found", 404
        return render_template("session_detail.html", session=capture_row,
                               report=capture_row.report)
    finally:
        db.close()


@app.route("/media/<path:filename>")
def media(filename):
    """Serve an uploaded photo. ``send_from_directory`` blocks path traversal."""
    name = secure_filename(filename)
    if not name:
        abort(404)
    return send_from_directory(app.config["UPLOAD_FOLDER"], name)


# ----------------------------------------------------------------------------
# Mobile capture API (REST, JSON) — consumed by the native iOS capture client.
# Flow: create session → add photos/audio/transcript → finalize → poll status.
# ----------------------------------------------------------------------------
def require_api_token(fn):
    """Gate a route behind ``CAPTURE_API_TOKEN`` when one is configured."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = config.CAPTURE_API_TOKEN
        if token:
            auth = request.headers.get("Authorization", "")
            sent = auth[7:] if auth.startswith("Bearer ") else request.headers.get("X-API-Key", "")
            if sent != token:
                return jsonify({"success": False, "message": "Unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper


def _session_json(capture_row):
    return {
        "session_id": capture_row.id,
        "title": capture_row.title,
        "status": capture_row.status,            # open|processing|<verdict>|failed
        "photo_count": len(capture_row.media),
        "has_audio": bool(capture_row.audio_filename),
        "has_transcript": bool(capture_row.transcript_text),
        "report_id": capture_row.report_id,
        "verdict": capture_row.status if capture_row.report_id else None,
        "error": capture_row.error,
        "is_signed": capture_row.is_signed,
        "signed_by": capture_row.signed_by,
        "signed_at": capture_row.signed_at.isoformat() if capture_row.signed_at else None,
    }


@app.route("/api/categories")
@require_api_token
def api_categories():
    """The photo categories the capture client offers (for its picker)."""
    return jsonify({"categories": categories.CATEGORIES})


@app.route("/api/sessions", methods=["POST"])
@require_api_token
def api_create_session():
    payload = request.get_json(silent=True) or {}
    db = get_db()
    try:
        capture_row = capture_store.create_session(
            db, title=payload.get("title", ""),
            transcript_text=payload.get("transcript", ""))
        return jsonify({"success": True, **_session_json(capture_row)}), 201
    finally:
        db.close()


def _load_session(db, session_id):
    return db.query(CaptureSession).filter(CaptureSession.id == session_id).first()


@app.route("/api/sessions/<int:session_id>/photos", methods=["POST"])
@require_api_token
def api_add_photo(session_id):
    """Add one captured photo: image file + timestamp + category + caption."""
    db = get_db()
    try:
        capture_row = _load_session(db, session_id)
        if not capture_row:
            return jsonify({"success": False, "message": "Session not found"}), 404

        file = request.files.get("photo") or request.files.get("image")
        if not file or not file.filename:
            return jsonify({"success": False, "message": "No photo file"}), 400
        category = request.form.get("category") or None
        if category and not categories.is_valid(category):
            return jsonify({"success": False, "message": f"Unknown category '{category}'"}), 400
        try:
            fname = _save_upload(file, ALLOWED_IMAGE_EXT)
        except ValueError as exc:
            return jsonify({"success": False, "message": str(exc)}), 400

        ts = request.form.get("timestamp", "0")
        item = capture_store.add_photo(
            db, capture_row, filename=fname, timestamp=float(ts or 0.0),
            category=category, caption=request.form.get("caption", ""))
        return jsonify({"success": True, "photo_id": item.id, "filename": fname}), 201
    finally:
        db.close()


@app.route("/api/sessions/<int:session_id>/audio", methods=["POST"])
@require_api_token
def api_add_audio(session_id):
    db = get_db()
    try:
        capture_row = _load_session(db, session_id)
        if not capture_row:
            return jsonify({"success": False, "message": "Session not found"}), 404
        file = request.files.get("audio")
        if not file or not file.filename:
            return jsonify({"success": False, "message": "No audio file"}), 400
        try:
            fname = _save_upload(file, ALLOWED_AUDIO_EXT)
        except ValueError as exc:
            return jsonify({"success": False, "message": str(exc)}), 400
        capture_store.set_audio(db, capture_row, fname)
        return jsonify({"success": True, "filename": fname})
    finally:
        db.close()


@app.route("/api/sessions/<int:session_id>/transcript", methods=["POST"])
@require_api_token
def api_set_transcript(session_id):
    payload = request.get_json(silent=True) or {}
    db = get_db()
    try:
        capture_row = _load_session(db, session_id)
        if not capture_row:
            return jsonify({"success": False, "message": "Session not found"}), 404
        capture_store.set_transcript(db, capture_row, payload.get("transcript", ""))
        return jsonify({"success": True})
    finally:
        db.close()


def _process_session_worker(session_id):
    """Run processing in a background thread with its own DB session."""
    db = get_session()
    try:
        capture_row = _load_session(db, session_id)
        if capture_row:
            capture_store.process_session(
                db, capture_row, provider=config.LLM_PROVIDER, rag=RAGPipeline(db))
    except Exception:  # noqa: BLE001 - status='failed' already recorded on the row
        pass
    finally:
        db.close()


@app.route("/api/sessions/<int:session_id>/finalize", methods=["POST"])
@require_api_token
def api_finalize_session(session_id):
    """Process the session: transcribe (if needed) → extract → compose → QA.

    Synchronous by default (client shows a spinner). With ``{"background": true}``
    the work runs in a thread and the route returns 202 immediately; the client
    polls ``GET /api/sessions/{id}`` until status leaves ``processing``.
    """
    payload = request.get_json(silent=True) or {}
    db = get_db()
    try:
        capture_row = _load_session(db, session_id)
        if not capture_row:
            return jsonify({"success": False, "message": "Session not found"}), 404
        if capture_row.is_signed:
            return jsonify({"success": False, "message": "Session is signed and locked"}), 409

        if payload.get("background"):
            capture_row.status = "processing"
            capture_row.error = None
            db.commit()
            threading.Thread(target=_process_session_worker, args=(session_id,),
                             daemon=True).start()
            return jsonify({"success": True, **_session_json(capture_row)}), 202

        try:
            capture_store.process_session(
                db, capture_row, provider=config.LLM_PROVIDER, rag=RAGPipeline(db))
        except Exception as exc:  # noqa: BLE001 - reported via status='failed'
            return jsonify({"success": False, **_session_json(capture_row),
                            "message": str(exc)}), 422
        return jsonify({"success": True, **_session_json(capture_row)})
    finally:
        db.close()


@app.route("/api/sessions/<int:session_id>")
@require_api_token
def api_get_session(session_id):
    db = get_db()
    try:
        capture_row = _load_session(db, session_id)
        if not capture_row:
            return jsonify({"success": False, "message": "Session not found"}), 404
        return jsonify({"success": True, **_session_json(capture_row)})
    finally:
        db.close()


# --- findings review/edit (the "go deeper on the computer" step) ---
_FINDING_FIELDS = ("part", "observation", "measurement", "cause", "consequence",
                   "recommendation", "severity")
_TG_VALUES = {"TG0", "TG1", "TG2", "TG3", "TGiU"}


def _finding_json(f):
    return {"id": f.id, "order_index": f.order_index, "part": f.part,
            "observation": f.observation, "measurement": f.measurement or "",
            "cause": f.cause or "", "consequence": f.consequence or "",
            "recommendation": f.recommendation or "", "severity": f.severity or "TG2",
            "photo_filename": f.photo_filename}


@app.route("/api/sessions/<int:session_id>/findings")
@require_api_token
def api_list_findings(session_id):
    db = get_db()
    try:
        capture_row = _load_session(db, session_id)
        if not capture_row:
            return jsonify({"success": False, "message": "Session not found"}), 404
        return jsonify({"success": True,
                        "findings": [_finding_json(f) for f in capture_row.findings]})
    finally:
        db.close()


@app.route("/api/sessions/<int:session_id>/findings", methods=["POST"])
@require_api_token
def api_add_finding(session_id):
    payload = request.get_json(silent=True) or {}
    db = get_db()
    try:
        capture_row = _load_session(db, session_id)
        if not capture_row:
            return jsonify({"success": False, "message": "Session not found"}), 404
        if capture_row.is_signed:
            return jsonify({"success": False, "message": "Session is signed and locked"}), 409
        nxt = max((f.order_index for f in capture_row.findings), default=-1) + 1
        finding = SessionFinding(
            session_id=session_id, order_index=nxt,
            part=payload.get("part", "generelt"), observation=payload.get("observation", ""),
            measurement=payload.get("measurement", ""), cause=payload.get("cause", ""),
            consequence=payload.get("consequence", ""),
            recommendation=payload.get("recommendation", ""),
            severity=payload.get("severity", "TG2"))
        db.add(finding)
        db.commit()
        return jsonify({"success": True, "finding": _finding_json(finding)}), 201
    finally:
        db.close()


@app.route("/api/sessions/<int:session_id>/findings/<int:finding_id>", methods=["PUT"])
@require_api_token
def api_update_finding(session_id, finding_id):
    payload = request.get_json(silent=True) or {}
    db = get_db()
    try:
        finding = db.query(SessionFinding).filter_by(id=finding_id, session_id=session_id).first()
        if not finding:
            return jsonify({"success": False, "message": "Finding not found"}), 404
        if finding.session.is_signed:
            return jsonify({"success": False, "message": "Session is signed and locked"}), 409
        if "severity" in payload and payload["severity"] not in _TG_VALUES:
            return jsonify({"success": False, "message": "Invalid severity"}), 400
        for field in _FINDING_FIELDS:
            if field in payload:
                setattr(finding, field, payload[field])
        db.commit()
        return jsonify({"success": True, "finding": _finding_json(finding)})
    finally:
        db.close()


@app.route("/api/sessions/<int:session_id>/findings/<int:finding_id>", methods=["DELETE"])
@require_api_token
def api_delete_finding(session_id, finding_id):
    db = get_db()
    try:
        finding = db.query(SessionFinding).filter_by(id=finding_id, session_id=session_id).first()
        if not finding:
            return jsonify({"success": False, "message": "Finding not found"}), 404
        if finding.session.is_signed:
            return jsonify({"success": False, "message": "Session is signed and locked"}), 409
        db.delete(finding)
        db.commit()
        return jsonify({"success": True})
    finally:
        db.close()


@app.route("/api/sessions/<int:session_id>/recompose", methods=["POST"])
@require_api_token
def api_recompose(session_id):
    """Re-draft the report from the session's edited findings."""
    db = get_db()
    try:
        capture_row = _load_session(db, session_id)
        if not capture_row:
            return jsonify({"success": False, "message": "Session not found"}), 404
        if capture_row.is_signed:
            return jsonify({"success": False, "message": "Session is signed and locked"}), 409
        try:
            capture_store.recompose(db, capture_row, provider=config.LLM_PROVIDER,
                                    rag=RAGPipeline(db))
        except Exception as exc:  # noqa: BLE001
            return jsonify({"success": False, "message": str(exc)}), 422
        return jsonify({"success": True, **_session_json(capture_row)})
    finally:
        db.close()


@app.route("/api/sessions/<int:session_id>/sign", methods=["POST"])
@require_api_token
def api_sign_session(session_id):
    """Sign the draft into the final report (records signer + locks editing)."""
    payload = request.get_json(silent=True) or {}
    db = get_db()
    try:
        capture_row = _load_session(db, session_id)
        if not capture_row:
            return jsonify({"success": False, "message": "Session not found"}), 404
        try:
            capture_store.sign(db, capture_row, signed_by=payload.get("signed_by", ""))
        except capture_store.SessionLockedError as exc:
            return jsonify({"success": False, "message": str(exc)}), 409
        except capture_store.CaptureStoreError as exc:
            return jsonify({"success": False, "message": str(exc)}), 400
        return jsonify({"success": True, **_session_json(capture_row)})
    finally:
        db.close()


@app.route("/reports")
def reports_list():
    db = get_db()
    try:
        reports = db.query(Report).order_by(desc(Report.created_at)).all()
        reports_data = []
        for report in reports:
            qa_result = db.query(QAResult).filter(QAResult.report_id == report.id).first()
            review = qa_result.review if qa_result else None
            reports_data.append({
                "id": report.id,
                "filename": report.filename,
                "topic": report.topic,
                "status": report.status,
                "created_at": report.created_at.strftime("%Y-%m-%d %H:%M"),
                "qa_status": qa_result.final_quality if qa_result else "pending",
                "issue_count": len(qa_result.issues) if qa_result else 0,
                "source": {"upload": "Upload", "authored": "Authored"}.get(
                    report.source, "Generated"),
                "review_decision": review.decision if review else None,
                "corrected_quality": review.corrected_quality if review else None,
                "reviewable": qa_result is not None,
                "confidence": scoring.confidence(qa_result.rule_quality,
                                                 qa_result.llm_quality) if qa_result else None,
                "triage": reviews.triage(qa_result) if qa_result else "pending",
            })
        return render_template("reports.html", reports=reports_data)
    finally:
        db.close()


@app.route("/report/<int:report_id>")
def report_detail(report_id):
    db = get_db()
    try:
        report = db.query(Report).filter(Report.id == report_id).first()
        if not report:
            return "Report not found", 404

        qa_result = db.query(QAResult).filter(QAResult.report_id == report_id).first()
        issues = []
        if qa_result:
            for issue in qa_result.issues:
                parts = issue.span.split(":")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    start, end = int(parts[0]), int(parts[1])
                else:
                    start, end = -1, -1
                issues.append({
                    "id": issue.id,
                    "type": issue.issue_type,
                    "start": start,
                    "end": end,
                    "comment": issue.comment,
                    "span": issue.span,
                })
        issues.sort(key=lambda x: x["start"])
        # Authored drafts carry their capture photos as supporting evidence.
        capture_row = (db.query(CaptureSession)
                       .filter(CaptureSession.report_id == report_id).first()
                       if report.source == "authored" else None)
        tg = (tg_summary.summarize(capture_row.findings)
              if capture_row and capture_row.findings else None)
        return render_template(
            "report_detail.html", report=report, qa_result=qa_result, issues=issues,
            review=qa_result.review if qa_result else None,
            ground_truth=reviews.ground_truth(qa_result) if qa_result else None,
            confidence=scoring.confidence(qa_result.rule_quality,
                                          qa_result.llm_quality) if qa_result else None,
            triage=reviews.triage(qa_result) if qa_result else None,
            quality_levels=config.QUALITY_LEVELS,
            capture_session=capture_row, tg_summary=tg,
        )
    finally:
        db.close()


@app.route("/report/<int:report_id>/pdf")
def download_report_pdf(report_id):
    """Render a report as a downloadable PDF (with evidence photos for drafts)."""
    db = get_db()
    try:
        report = db.query(Report).filter(Report.id == report_id).first()
        if not report:
            return "Report not found", 404
        capture_row = (db.query(CaptureSession)
                       .filter(CaptureSession.report_id == report_id).first()
                       if report.source == "authored" else None)
        pdf_bytes = report_pdf.build_report_pdf(report, capture_row)
        return Response(pdf_bytes, mimetype="application/pdf", headers={
            "Content-Disposition": f'inline; filename="report_{report_id}.pdf"'})
    finally:
        db.close()


@app.route("/report/<int:report_id>/review", methods=["POST"])
def review_report(report_id):
    """Record a reviewer's accept/override decision on a report's QA result."""
    payload = request.get_json(silent=True) or {}
    db = get_db()
    try:
        qa_result = db.query(QAResult).filter(QAResult.report_id == report_id).first()
        if not qa_result:
            return jsonify({"success": False,
                            "message": "Report has no QA result to review yet"}), 400
        try:
            review = reviews.record_review(
                db, qa_result,
                decision=payload.get("decision", ""),
                corrected_quality=payload.get("corrected_quality"),
                note=payload.get("note"),
            )
        except ValueError as exc:
            return jsonify({"success": False, "message": str(exc)}), 400

        return jsonify({"success": True, "message": "Review saved",
                        "decision": review.decision,
                        "corrected_quality": review.corrected_quality})
    finally:
        db.close()


@app.route("/curate", methods=["POST"])
def curate():
    """Distill reviewer overrides into RAG examples.

    With ``{"report_id": N}`` curates that one report's override; otherwise
    batch-curates every override not yet in the knowledge base.
    """
    payload = request.get_json(silent=True) or {}
    try:
        curator = curation.get_curator()
    except curation.CurationError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    db = get_db()
    try:
        report_id = payload.get("report_id")
        if report_id is not None:
            qa_result = db.query(QAResult).filter(QAResult.report_id == report_id).first()
            review = qa_result.review if qa_result else None
            if not review or review.decision != "overridden":
                return jsonify({"success": False,
                                "message": "No override on this report to curate"}), 400
            try:
                example = curation.curate_review(db, review, curator)
            except curation.CurationError as exc:
                return jsonify({"success": False, "message": str(exc)}), 500
            return jsonify({"success": True,
                            "message": "Correction added to the knowledge base",
                            "example_id": example.id})

        count = curation.curate_pending(db, curator)
        message = (f"Added {count} correction(s) to the knowledge base" if count
                   else "No new corrections to curate")
        return jsonify({"success": True, "message": message})
    finally:
        db.close()


@app.route("/evaluate", methods=["POST"])
def evaluate_reports():
    """Run QA evaluation on uploaded reports."""
    payload = request.get_json(silent=True) or {}
    reevaluate = bool(payload.get("reevaluate", False))
    try:
        evaluator = QAEvaluator()
    except JudgeError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    try:
        count = evaluator.run_evaluation_on_uploads(reevaluate=reevaluate)
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500
    finally:
        evaluator.close()

    message = (f"QA evaluation completed on {count} report(s)" if count
               else "No reports needed evaluation")
    return jsonify({"success": True, "message": message})


@app.route("/api/report/<int:report_id>/issues")
def get_report_issues(report_id):
    db = get_db()
    try:
        qa_result = db.query(QAResult).filter(QAResult.report_id == report_id).first()
        if not qa_result:
            return jsonify({"issues": []})
        return jsonify({"issues": [
            {"id": i.id, "type": i.issue_type, "span": i.span, "comment": i.comment}
            for i in qa_result.issues
        ]})
    finally:
        db.close()


@app.route("/api/stats")
def get_stats():
    db = get_db()
    try:
        counts = _quality_counts(db)
        stats = {
            "total_reports": db.query(Report).count(),
            "total_evaluated": db.query(QAResult).count(),
            "clean": counts["clean"],
            "minor_errors": counts["minor_error"],
            "major_errors": counts["major_error"],
            "pending": db.query(Report).outerjoin(QAResult)
                         .filter(QAResult.id == None).count(),  # noqa: E711
            "accuracy": reviews.accuracy(db)["accuracy"],
            **reviews.review_stats(db),
            **reviews.triage_stats(db),
        }
        recent = db.query(QAIssue).order_by(desc(QAIssue.created_at)).limit(10).all()
        stats["recent_issues"] = [
            {"type": i.issue_type,
             "comment": (i.comment[:100] + "...") if len(i.comment) > 100 else i.comment}
            for i in recent
        ]
        return jsonify(stats)
    finally:
        db.close()


# Tokens used to mark highlight boundaries *before* Markdown rendering, then
# swapped for real <span> tags after. Plain alphanumerics so Markdown leaves
# them untouched (no underscores → no accidental emphasis).
_OPEN_RE = re.compile(r"@@IO(\d+)@@")
_CLOSE_RE = re.compile(r"@@IC\d+@@")


@app.template_filter("highlight_issues")
def highlight_issues(text, issues):
    """Render report Markdown as HTML with issue spans highlighted.

    Highlights are inserted as sentinel tokens, Markdown is rendered, then the
    tokens become <span> tags. This avoids splicing raw HTML into Markdown
    source (which corrupted block structure in the old implementation).
    """
    if not text:
        return ""

    by_id = {}
    valid = []
    for issue in issues or []:
        start, end = issue.get("start", -1), issue.get("end", -1)
        if start is not None and 0 <= start < end <= len(text):
            valid.append(issue)
            by_id[str(issue.get("id"))] = issue

    # Insert tokens from the end backwards so earlier offsets stay valid.
    for issue in sorted(valid, key=lambda i: i["start"], reverse=True):
        s, e, tid = issue["start"], issue["end"], issue.get("id")
        text = f"{text[:s]}@@IO{tid}@@{text[s:e]}@@IC{tid}@@{text[e:]}"

    html = markdown.markdown(text, extensions=["extra", "sane_lists"], output_format="html5")

    def open_repl(match):
        issue = by_id.get(match.group(1), {})
        itype = "major" if issue.get("type") == "major" else "minor"
        return (f'<span class="issue-highlight issue-{itype}" '
                f'data-issue-id="{escape(match.group(1))}" '
                f'data-comment="{escape(str(issue.get("comment", "")))}">')

    html = _OPEN_RE.sub(open_repl, html)
    html = _CLOSE_RE.sub("</span>", html)

    clean_html = bleach.clean(
        html,
        tags=["p", "br", "ul", "ol", "li", "strong", "em", "code", "pre",
              "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "span"],
        attributes={"span": ["class", "data-issue-id", "data-comment"]},
        strip=True,
    )
    return Markup(clean_html)


if __name__ == "__main__":
    app.run(debug=config.FLASK_DEBUG, port=config.FLASK_PORT)
