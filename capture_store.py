"""
Persistence for the capture → authoring flow.

Two entry points share one persistence core:

- **One-shot** (`save_session`) — web form: an already-computed `IntakeResult` is
  handed in and stored.
- **Incremental** (`create_session` → `add_photo`/`set_audio`/`set_transcript` →
  `process_session`) — REST capture API: the mobile client builds a session up
  over several requests, then asks the server to process it.

Processing persists three things: the **transcript** (`TranscriptSegment`s), the
extracted **findings** (`SessionFinding`s — human-editable), and the composed
**draft** (a `Report` source='authored' + `QAResult`/`QAIssue`s). Editing the
findings and calling `recompose` re-drafts from the corrected set.

Kept separate from `intake.py` (which stays pure, no DB).
"""

import secrets
from datetime import datetime, timezone
from typing import List, Optional

import capture
import config
import intake
from authoring import AuthoringResult, Finding
from db_setup import (
    CaptureSession, MediaItem, QAIssue, QAResult, Report, SessionFinding,
    TranscriptSegment,
)
from intake import IntakeResult


class CaptureStoreError(RuntimeError):
    """Raised when a session cannot be processed (no transcript/audio, etc.)."""


class SessionLockedError(CaptureStoreError):
    """Raised when editing a signed (locked) session."""


# --- conversions ---
def _finding_to_row(session_id: int, finding: Finding, order: int) -> SessionFinding:
    return SessionFinding(
        session_id=session_id, order_index=order, part=finding.part,
        observation=finding.observation, measurement=finding.measurement,
        cause=finding.cause, consequence=finding.consequence,
        recommendation=finding.recommendation, severity=finding.severity,
        photo_filename=(finding.photo_refs[0] if finding.photo_refs else None))


def _row_to_finding(row: SessionFinding) -> Finding:
    return Finding(
        part=row.part, observation=row.observation, measurement=row.measurement or "",
        cause=row.cause or "", consequence=row.consequence or "",
        recommendation=row.recommendation or "", severity=row.severity or "TG2",
        photo_refs=[row.photo_filename] if row.photo_filename else [])


# --- persistence pieces ---
def _persist_report(session, capture_row: CaptureSession, authoring: AuthoringResult,
                    provider: str) -> Report:
    """Write the composed draft (Report + QAResult + issues) and link the session."""
    now = datetime.now(timezone.utc)
    report = Report(
        filename=f"authored_{now.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}.md",
        topic=capture_row.title, status=authoring.final_quality, source="authored",
        report_text=authoring.report_text, generator_version="authoring_v1",
        model=provider or "intake", created_at=now)
    session.add(report)
    session.flush()

    qa = QAResult(report_id=report.id, rule_quality=authoring.rule_quality or None,
                  llm_quality=authoring.llm_quality or None,
                  final_quality=authoring.final_quality, expected_status=None,
                  evaluated_at=now)
    session.add(qa)
    session.flush()
    for issue in authoring.issues:
        session.add(QAIssue(qa_result_id=qa.id, issue_type=issue.get("type", "minor"),
                            span=issue.get("span", "0:0"), comment=issue.get("comment", "")))

    capture_row.report_id = report.id
    capture_row.status = authoring.final_quality
    capture_row.provider = provider
    capture_row.error = None
    return report


def _persist_transcript(session, capture_row: CaptureSession, transcript) -> None:
    session.query(TranscriptSegment).filter_by(session_id=capture_row.id).delete()
    for seg in transcript.segments:
        session.add(TranscriptSegment(session_id=capture_row.id, start_s=seg.start,
                                      end_s=seg.end, text=seg.text))


def _persist_findings(session, capture_row: CaptureSession, findings: List[Finding]) -> None:
    session.query(SessionFinding).filter_by(session_id=capture_row.id).delete()
    for i, finding in enumerate(findings):
        session.add(_finding_to_row(capture_row.id, finding, i))


# --- one-shot (web form) ---
def save_session(session, title: str, result: IntakeResult,
                 provider: str = "", audio_filename: Optional[str] = None,
                 media: Optional[List[dict]] = None) -> CaptureSession:
    """Persist a capture session and its produced draft in one call."""
    capture_row = CaptureSession(title=title, audio_filename=audio_filename,
                                 provider=provider, status="open",
                                 created_at=datetime.now(timezone.utc))
    session.add(capture_row)
    session.flush()
    for m in media or []:
        session.add(MediaItem(session_id=capture_row.id, filename=m.get("filename"),
                              timestamp=float(m.get("timestamp", 0.0)),
                              caption=m.get("caption", ""), category=m.get("category")))
    _persist_transcript(session, capture_row, result.transcript)
    _persist_findings(session, capture_row, result.findings)
    _persist_report(session, capture_row, result.authoring, provider)
    session.commit()
    return capture_row


# --- incremental (REST API) ---
def create_session(session, title: str, transcript_text: str = "") -> CaptureSession:
    capture_row = CaptureSession(title=title or "Befaring", status="open",
                                 transcript_text=transcript_text or None,
                                 created_at=datetime.now(timezone.utc))
    session.add(capture_row)
    session.commit()
    return capture_row


def add_photo(session, capture_row: CaptureSession, filename: str, timestamp: float,
              category: Optional[str] = None, caption: str = "") -> MediaItem:
    item = MediaItem(session_id=capture_row.id, filename=filename,
                     timestamp=float(timestamp or 0.0), caption=caption or "",
                     category=category)
    session.add(item)
    session.commit()
    return item


def set_audio(session, capture_row: CaptureSession, audio_filename: str) -> None:
    capture_row.audio_filename = audio_filename
    session.commit()


def set_transcript(session, capture_row: CaptureSession, text: str) -> None:
    capture_row.transcript_text = text
    session.commit()


def _build_inputs(capture_row: CaptureSession, provider: str):
    """Reconstruct (transcript, photos) from the stored session rows."""
    photos = [
        capture.Photo(id=m.filename or str(m.id), timestamp=m.timestamp,
                      image_path=str(config.UPLOAD_DIR / m.filename) if m.filename else "",
                      caption=m.caption or "", category=m.category or "")
        for m in capture_row.media
    ]
    if capture_row.transcript_text and capture_row.transcript_text.strip():
        transcript = capture.parse_transcript(capture_row.transcript_text)
    elif capture_row.audio_filename:
        audio_path = str(config.UPLOAD_DIR / capture_row.audio_filename)
        transcript = capture.get_transcriber(provider).transcribe(audio_path)
    else:
        raise CaptureStoreError("Session has no transcript and no audio to transcribe.")
    return transcript, photos


def process_session(session, capture_row: CaptureSession, provider: str,
                    rag=None, max_revisions: int = 1) -> CaptureSession:
    """Run the full intake pipeline on a stored session and persist the draft.

    On failure the session is marked status='failed' with the error recorded.
    """
    capture_row.status = "processing"
    capture_row.provider = provider
    session.commit()
    try:
        transcript, photos = _build_inputs(capture_row, provider)
        result = intake.run_from_transcript(transcript, photos, provider=provider,
                                            rag=rag, max_revisions=max_revisions)
        if not result.findings:
            raise CaptureStoreError("No findings could be extracted from this session.")
        _persist_transcript(session, capture_row, result.transcript)
        _persist_findings(session, capture_row, result.findings)
        _persist_report(session, capture_row, result.authoring, provider)
        session.commit()
        return capture_row
    except Exception as exc:  # noqa: BLE001 - record and re-raise for the route
        session.rollback()
        capture_row.status = "failed"
        capture_row.error = str(exc)
        session.commit()
        raise


def recompose(session, capture_row: CaptureSession, provider: str,
              rag=None, max_revisions: int = 1) -> CaptureSession:
    """Re-draft the report from the session's (edited) findings — no re-extraction."""
    if capture_row.is_signed:
        raise SessionLockedError("Session is signed and locked.")
    findings = [_row_to_finding(r) for r in capture_row.findings]
    if not findings:
        raise CaptureStoreError("No findings to compose from.")
    authoring = intake.author_from_findings(findings, provider=provider, rag=rag,
                                            max_revisions=max_revisions)
    _persist_report(session, capture_row, authoring, provider)
    session.commit()
    return capture_row


def sign(session, capture_row: CaptureSession, signed_by: str) -> CaptureSession:
    """Sign the draft into the final report (bygningssakkyndig approval).

    Freezes the current draft: records who/when and locks findings editing and
    recompose. Requires a produced report and a non-empty signer name.
    """
    if capture_row.is_signed:
        raise SessionLockedError("Session is already signed.")
    if not capture_row.report_id:
        raise CaptureStoreError("Nothing to sign — process the session first.")
    signed_by = (signed_by or "").strip()
    if not signed_by:
        raise CaptureStoreError("A signer name is required.")
    capture_row.signed_by = signed_by
    capture_row.signed_at = datetime.now(timezone.utc)
    session.commit()
    return capture_row
