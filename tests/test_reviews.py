import pytest

import reviews
from db_setup import Report, QAResult


def _make_result(session, final, expected=None, source="upload", rule=None, llm=None):
    report = Report(filename=f"r_{final}_{id(final)}_{session.query(Report).count()}.txt",
                    topic="t", status=final, source=source, report_text="x")
    session.add(report)
    session.flush()
    qa = QAResult(report_id=report.id, rule_quality=rule or final, llm_quality=llm or final,
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


def test_triage_auto_clears_high_confidence_clean(fresh_session):
    qa = _make_result(fresh_session, "clean", rule="clean", llm="clean")
    assert reviews.triage(qa) == "auto_cleared"


def test_triage_flags_conflicting_signals(fresh_session):
    # Rule says major, LLM says clean → low confidence → needs review.
    qa = _make_result(fresh_session, "major_error", rule="major_error", llm="clean")
    assert reviews.triage(qa) == "needs_review"


def test_triage_flags_non_clean_even_if_confident(fresh_session):
    qa = _make_result(fresh_session, "major_error", rule="major_error", llm="major_error")
    assert reviews.triage(qa) == "needs_review"  # only 'clean' is auto-cleared by default


def test_triage_reviewed_wins(fresh_session):
    qa = _make_result(fresh_session, "clean", rule="clean", llm="clean")
    reviews.record_review(fresh_session, qa, "accepted")
    assert reviews.triage(qa) == "reviewed"


def test_triage_stats_counts(fresh_session):
    _make_result(fresh_session, "clean", rule="clean", llm="clean")           # auto_cleared
    _make_result(fresh_session, "major_error", rule="major_error", llm="clean")  # needs_review
    reviewed = _make_result(fresh_session, "clean", rule="clean", llm="clean")
    reviews.record_review(fresh_session, reviewed, "accepted")
    stats = reviews.triage_stats(fresh_session)
    assert stats == {"auto_cleared": 1, "needs_review": 1, "reviewed": 1}


def test_review_stats_agreement(fresh_session):
    qa1 = _make_result(fresh_session, "clean")
    qa2 = _make_result(fresh_session, "minor_error")
    reviews.record_review(fresh_session, qa1, "accepted")
    reviews.record_review(fresh_session, qa2, "overridden", corrected_quality="major_error")
    stats = reviews.review_stats(fresh_session)
    assert stats["reviewed"] == 2 and stats["overrides"] == 1
    assert stats["agreement_rate"] == 50.0
