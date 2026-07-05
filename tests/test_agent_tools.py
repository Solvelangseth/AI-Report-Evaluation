from agent_tools import ReviewToolbox
from rag_pipeline import RAGPipeline

REPORT = "### Sammendrag\nKjelleren er litt fuktig og kanskje lekkasje ved sluk."


def test_quote_span_found():
    tb = ReviewToolbox(REPORT)
    span = tb.quote_span("kanskje lekkasje")
    start, end = map(int, span.split(":"))
    assert REPORT[start:end] == "kanskje lekkasje"


def test_quote_span_not_found():
    assert "not found" in ReviewToolbox(REPORT).quote_span("nonexistent text").lower()


def test_get_rule_sections_lists_required():
    out = ReviewToolbox(REPORT).get_rule("sections")
    assert "sammendrag" in out.lower() and "kostnadsestimat" in out.lower()


def test_recheck_rules_flags_bad_report():
    out = ReviewToolbox(REPORT).recheck_rules()
    assert "Missing required section" in out


def test_recheck_rules_clean_report():
    good = ("### Sammendrag\n" + "x" * 60 + "\n### Observasjoner\n- " + "y" * 60 +
            "\n### Årsak\n" + "z" * 60 + "\n### Konsekvenser\n" + "k" * 60 +
            "\n### Anbefalinger\n- " + "a" * 60 + "\n### Kostnadsestimat\n50000 kr " + "b" * 60)
    out = ReviewToolbox(good).recheck_rules()
    assert out == "No rule-based issues found."


def test_search_similar_reports(fresh_session):
    tb = ReviewToolbox(REPORT, RAGPipeline(fresh_session, mode="lexical"))
    out = tb.search_similar_reports("fukt i kjeller")
    assert "Example 1" in out


def test_search_without_rag_returns_message():
    assert "No retrieval" in ReviewToolbox(REPORT, None).search_similar_reports("x")


def test_run_dispatch():
    tb = ReviewToolbox(REPORT)
    assert tb.run("get_rule", {"topic": "forbidden_words"}).startswith("Forbidden")
    assert tb.run("recheck_rules", {}).startswith("-")
    assert "Unknown tool" in tb.run("bogus", {})
