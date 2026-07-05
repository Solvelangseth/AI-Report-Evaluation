import prices
from authoring import (
    Finding, FakeCostAnalyst, FakeComposer, AuthoringPipeline, get_cost_analyst,
    get_composer, AuthoringError,
)
from judge import get_judge
from qa_engine import QAEngine, extract_sections


FINDINGS = [
    Finding(part="kjeller", observation="fukt og saltutslag", measurement="22 % fukt, 1,5 m²",
            cause="manglende drenering", consequence="risiko for råte",
            recommendation="etabler drenering rundt grunnmur", severity="TG2"),
    Finding(part="bad", observation="fukt ved sluk", measurement="19 % i veggsone",
            cause="svikt i membran", consequence="fuktskade",
            recommendation="utbedre membran ved sluk", severity="TG2"),
]


def test_price_lookup_matches_keywords():
    hits = prices.lookup("etabler drenering rundt grunnmur")
    assert hits and "drenering" in hits[0]["item"]


def test_fake_cost_analyst_grounds_in_reference():
    est = FakeCostAnalyst().estimate(FINDINGS[0])
    assert est.high > 0 and est.basis == prices.SOURCE
    assert "drenering" in est.measure


def test_fake_composer_produces_all_sections():
    costs = [FakeCostAnalyst().estimate(f) for f in FINDINGS]
    report = FakeComposer().compose(FINDINGS, costs)
    secs = extract_sections(report)
    assert {"sammendrag", "observasjoner", "årsak", "konsekvenser",
            "anbefalinger", "kostnadsestimat"} <= set(secs)
    assert "kr" in secs["kostnadsestimat"]
    assert prices.DISCLAIMER in report  # estimates are labelled indicative


def test_sammendrag_states_highest_tg():
    # TGiU must NOT out-rank TG3 (the old naive string-max bug).
    findings = [Finding(part="tak", observation="ikke tilgjengelig", severity="TGiU"),
                Finding(part="bad", observation="råte", severity="TG3")]
    costs = [FakeCostAnalyst().estimate(f) for f in findings]
    secs = extract_sections(FakeComposer().compose(findings, costs))
    assert "Høyeste tilstandsgrad er TG3" in secs["sammendrag"]


def test_fake_composer_includes_tg_section():
    costs = [FakeCostAnalyst().estimate(f) for f in FINDINGS]
    report = FakeComposer().compose(FINDINGS, costs)
    secs = extract_sections(report)
    assert "tilstandsgrader" in secs
    assert "Høyeste tilstandsgrad" in secs["tilstandsgrader"]
    # It leads the report (the standard TG overview comes first).
    assert report.lstrip().startswith("Tilstandsgrader")


def test_pipeline_end_to_end_offline():
    # findings -> costs -> compose -> evaluate, all with fakes (no network)
    pipeline = AuthoringPipeline(
        cost_analyst=FakeCostAnalyst(),
        composer=FakeComposer(),
        engine=QAEngine(judge=get_judge("fake"), rag=None),
    )
    result = pipeline.run(FINDINGS)
    assert result.report_text
    assert len(result.costs) == 2
    assert result.final_quality in ("clean", "minor_error", "major_error")
    # the composed report passes the structural rules (all six sections present)
    assert result.final_quality == "clean"


def test_factories_wiring():
    assert isinstance(get_cost_analyst("fake"), FakeCostAnalyst)
    assert isinstance(get_composer("fake"), FakeComposer)
    import pytest
    with pytest.raises(AuthoringError):
        get_composer("anthropic")  # no key in tests
