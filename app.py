"""
Flask web application for the inspection report QA system.

Handles uploads, evaluation, and the dashboard. Evaluation logic lives in
qa_engine/qa_master; this module is the web layer only.
"""

import os
import re
import json
import secrets
from datetime import datetime, timezone

from flask import Flask, render_template, jsonify, request, redirect, flash
from flask_cors import CORS
from werkzeug.utils import secure_filename
from sqlalchemy import desc
import markdown
import bleach
from markupsafe import Markup, escape

import config
import reviews
import curation
from db_setup import get_session, Report, QAResult, QAIssue, seed_rag_examples
from judge import JudgeError
from qa_master import QAEvaluator

app = Flask(__name__)
CORS(app)
app.config["SECRET_KEY"] = config.FLASK_SECRET_KEY or secrets.token_hex(32)
app.config["UPLOAD_FOLDER"] = str(config.UPLOAD_DIR)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

# Ensure directories and schema exist, and seed RAG examples once at startup.
config.ensure_dirs()
seed_rag_examples()


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
                "source": "Upload" if report.source == "upload" else "Generated",
                "review_decision": review.decision if review else None,
                "corrected_quality": review.corrected_quality if review else None,
                "reviewable": qa_result is not None,
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
        return render_template(
            "report_detail.html", report=report, qa_result=qa_result, issues=issues,
            review=qa_result.review if qa_result else None,
            ground_truth=reviews.ground_truth(qa_result) if qa_result else None,
            quality_levels=config.QUALITY_LEVELS,
        )
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
