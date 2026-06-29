"""
Reviewer workflow — human accept/override decisions as ground truth.

A reviewer either accepts a QA verdict or overrides it with the correct label.
That decision is the authoritative ground truth: it drives accuracy (preferred
over the synthetic `expected_status`) and is the training signal the RAG
curation agent will later learn from.

This module also owns ground-truth resolution and accuracy so the web app and
the CLI compute them the same way (one place, not two).
"""

from datetime import datetime, timezone
from typing import Optional

import config
from db_setup import QAResult, Review

VALID_DECISIONS = {"accepted", "overridden"}


def ground_truth(qa_result: QAResult) -> Optional[str]:
    """The authoritative label for a result: human review wins over synthetic."""
    if qa_result.review:
        return qa_result.review.corrected_quality
    expected = qa_result.expected_status
    return expected if expected in config.QUALITY_LEVELS else None


def record_review(session, qa_result: QAResult, decision: str,
                  corrected_quality: Optional[str] = None,
                  note: Optional[str] = None) -> Review:
    """Create or update the review for a QA result. Returns the Review.

    For ``accepted`` the corrected label is the verdict itself; for
    ``overridden`` the caller must supply a valid ``corrected_quality``.
    """
    decision = (decision or "").lower()
    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of {sorted(VALID_DECISIONS)}")

    if decision == "accepted":
        corrected = qa_result.final_quality
    else:
        if corrected_quality not in config.QUALITY_LEVELS:
            raise ValueError(f"corrected_quality must be one of {list(config.QUALITY_LEVELS)}")
        corrected = corrected_quality

    note = (note or "").strip() or None
    review = qa_result.review
    if review:
        review.decision = decision
        review.corrected_quality = corrected
        review.note = note
        review.created_at = datetime.now(timezone.utc)
    else:
        review = Review(qa_result_id=qa_result.id, decision=decision,
                        corrected_quality=corrected, note=note)
        session.add(review)
    session.commit()
    return review


def accuracy(session) -> dict:
    """QA accuracy against ground truth (human review preferred)."""
    pairs = [(r.final_quality, ground_truth(r)) for r in session.query(QAResult).all()]
    pairs = [(final, truth) for final, truth in pairs if truth]
    if not pairs:
        return {"accuracy": 0, "matches": 0, "total": 0}
    matches = sum(1 for final, truth in pairs if final == truth)
    return {"accuracy": round(matches / len(pairs) * 100, 1),
            "matches": matches, "total": len(pairs)}


def review_stats(session) -> dict:
    """Counts of human review activity and the human/model agreement rate."""
    total = session.query(Review).count()
    overrides = session.query(Review).filter(Review.decision == "overridden").count()
    evaluated = session.query(QAResult).count()
    return {
        "reviewed": total,
        "overrides": overrides,
        "unreviewed": max(evaluated - total, 0),
        "agreement_rate": round((1 - overrides / total) * 100, 1) if total else 0,
    }
