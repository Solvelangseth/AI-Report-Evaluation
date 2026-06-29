import pytest

import reviews
from db_setup import Report, QAResult


def _make_result(session, final, expected=None, source="upload"):
    report = Report(filename=f"r_{final}_{id(final)}_{session.query(Report).count()}.txt",
                    topic="t", status=final, source=source, report_text="x")
    session.add(report)
    session.flush()
    qa = QAResult(report_id=report.id, rule_quality=final, llm_quality=final,
                  final_quality=final, expected_status=expected)
    session.add(qa)
    session.commit()
    return qa


def test_accept_sets_corrected_to_verdict(fresh_session):
    qa = _make_result(fresh_session, "minor_error")
    review = reviews.record_review(fresh_session, qa, "accepted")
    assert review.decision == "accepted"
    assert review.corrected_quality == "minor_error"


def test_override_requires_valid_label(fresh_session):
    qa = _make_result(fresh_session, "clean")
    with pytest.raises(ValueError):
        reviews.record_review(fresh_session, qa, "overridden", corrected_quality="bogus")


def test_override_sets_ground_truth(fresh_session):
    qa = _make_result(fresh_session, "clean", expected="clean")
    reviews.record_review(fresh_session, qa, "overridden",
                          corrected_quality="major_error", note="missed a section")
    # Human override beats the synthetic expected label.
    assert reviews.ground_truth(qa) == "major_error"
    assert qa.review.note == "missed a section"


def test_review_is_upserted(fresh_session):
    qa = _make_result(fresh_session, "clean")
    reviews.record_review(fresh_session, qa, "accepted")
    reviews.record_review(fresh_session, qa, "overridden", corrected_quality="minor_error")
    assert fresh_session.query(QAResult).first().review.decision == "overridden"


def test_invalid_decision_rejected(fresh_session):
    qa = _make_result(fresh_session, "clean")
    with pytest.raises(ValueError):
        reviews.record_review(fresh_session, qa, "maybe")


def test_accuracy_prefers_human_truth(fresh_session):
    # Verdict clean, but human says major → counts as a miss.
    qa = _make_result(fresh_session, "clean", expected="clean")
    reviews.record_review(fresh_session, qa, "overridden", corrected_quality="major_error")
    acc = reviews.accuracy(fresh_session)
    assert acc["total"] == 1 and acc["matches"] == 0 and acc["accuracy"] == 0


def test_review_stats_agreement(fresh_session):
    qa1 = _make_result(fresh_session, "clean")
    qa2 = _make_result(fresh_session, "minor_error")
    reviews.record_review(fresh_session, qa1, "accepted")
    reviews.record_review(fresh_session, qa2, "overridden", corrected_quality="major_error")
    stats = reviews.review_stats(fresh_session)
    assert stats["reviewed"] == 2 and stats["overrides"] == 1
    assert stats["agreement_rate"] == 50.0
