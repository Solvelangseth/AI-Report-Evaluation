import pytest

import curation
import reviews
from curation import CurationError, FakeCurator, curate_review, curate_pending
from db_setup import Report, QAResult, QAIssue, RAGExample
from rag_pipeline import RAGPipeline


def _override(session, topic="fukt i kjeller", verdict="major_error", corrected="clean",
              note="false alarm", text="### Sammendrag\nKjelleren er tørr og godt vedlikeholdt."):
    report = Report(filename=f"r_{id(text)}_{session.query(Report).count()}.txt",
                    topic=topic, status=verdict, source="upload", report_text=text)
    session.add(report)
    session.flush()
    qa = QAResult(report_id=report.id, rule_quality=verdict, llm_quality=verdict,
                  final_quality=verdict)
    session.add(qa)
    session.flush()
    session.add(QAIssue(qa_result_id=qa.id, issue_type="major", span="0:0", comment="missing section"))
    session.commit()
    review = reviews.record_review(session, qa, "overridden",
                                   corrected_quality=corrected, note=note)
    return review


def test_curate_creates_example_with_ground_truth_label(fresh_session):
    review = _override(fresh_session, corrected="clean")
    example = curate_review(fresh_session, review, FakeCurator())
    assert example is not None
    assert example.source == "curation"
    assert example.quality_label == "clean"          # human truth, not the LLM's
    assert example.topic == "fukt i kjeller"          # fell back to report topic
    assert review.rag_example_id == example.id        # linked back


def test_curate_is_idempotent(fresh_session):
    review = _override(fresh_session)
    first = curate_review(fresh_session, review, FakeCurator())
    before = fresh_session.query(RAGExample).count()
    second = curate_review(fresh_session, review, FakeCurator())
    assert second.id == first.id
    assert fresh_session.query(RAGExample).count() == before  # no duplicate


def test_accepted_reviews_are_not_curated(fresh_session):
    report = Report(filename="acc.txt", topic="t", status="clean", source="upload",
                    report_text="x")
    fresh_session.add(report)
    fresh_session.flush()
    qa = QAResult(report_id=report.id, final_quality="clean")
    fresh_session.add(qa)
    fresh_session.commit()
    review = reviews.record_review(fresh_session, qa, "accepted")
    assert curate_review(fresh_session, review, FakeCurator()) is None


def test_curate_pending_counts(fresh_session):
    _override(fresh_session)
    _override(fresh_session, topic="råte i tak", corrected="minor_error")
    assert curate_pending(fresh_session, FakeCurator()) == 2
    # Already curated → nothing left to do.
    assert curate_pending(fresh_session, FakeCurator()) == 0


def test_flywheel_curated_example_is_retrievable(fresh_session):
    review = _override(fresh_session, topic="vannskade på bad",
                       text="### Sammendrag\nVannskade på bad med fukt ved sluk.")
    example = curate_review(fresh_session, review, FakeCurator())
    # The new example is now retrievable for a similar report.
    rag = RAGPipeline(fresh_session, mode="lexical")
    results = rag.retrieve_examples("vannskade på bad fukt ved sluk", top_k=5)
    assert any(r["id"] == example.id for r in results)


def test_title_collision_is_disambiguated(fresh_session):
    r1 = _override(fresh_session)
    r2 = _override(fresh_session, topic="annen sak")
    e1 = curate_review(fresh_session, r1, FakeCurator())
    e2 = curate_review(fresh_session, r2, FakeCurator())
    assert e1.title != e2.title  # FakeCurator returns a constant title; ids disambiguate


def test_get_curator_wiring():
    assert isinstance(curation.get_curator("fake"), FakeCurator)
    with pytest.raises(CurationError):
        curation.get_curator("anthropic")  # no key in tests


def test_parse_draft_rejects_bad_output():
    with pytest.raises(CurationError):
        curation._parse_draft("not json")
    with pytest.raises(CurationError):
        curation._parse_draft('{"title": "", "guidance": ""}')  # empty → invalid
