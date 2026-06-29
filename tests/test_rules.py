from qa_rules import QABaseline
from qa_engine import rule_based_issues


def test_rules_no_longer_flag_vague_words():
    # A complete report with vague words: rules must NOT flag the words
    # (that's the LLM's job now); only objective/structural issues remain.
    report = ("### Sammendrag\n" + "Boligen er litt fuktig og kanskje utsatt. " + "x" * 40 +
              "\n### Observasjoner\n- Fukt 18 % ved sluk " + "y" * 40 +
              "\n### Årsak\n" + "z" * 60 + "\n### Konsekvenser\n" + "k" * 60 +
              "\n### Anbefalinger\n- " + "a" * 60 +
              "\n### Kostnadsestimat\n25000 kr " + "b" * 60)
    issues = rule_based_issues(report)
    assert not any("Forbidden word" in i["comment"] for i in issues)
    assert not any("vague" in i["comment"].lower() for i in issues)


def test_rules_still_flag_structure():
    # Missing sections are still flagged deterministically.
    issues = rule_based_issues("### Sammendrag\nKort tekst uten resten.")
    assert any("Missing required section" in i["comment"] for i in issues)


def test_forbidden_words_finds_all_occurrences():
    text = "Kjelleren er litt fuktig og litt kald og litt mørk."
    hits = [h for h in QABaseline.check_forbidden_words(text) if h["word"] == "litt"]
    assert len(hits) == 3


def test_litt_not_double_counted_as_vague_quantifier():
    # "litt" lives only in FORBIDDEN_WORDS now, not in avoid_vague.
    quant = QABaseline.check_quantification("Det er litt fukt her." * 5)
    assert not any(q.get("word") == "litt" for q in quant)


def test_section_order_validation():
    ordered = ["sammendrag", "observasjoner", "årsak", "konsekvenser",
               "anbefalinger", "kostnadsestimat"]
    assert QABaseline.validate_section_order(ordered)
    assert not QABaseline.validate_section_order(list(reversed(ordered)))
    assert not QABaseline.validate_section_order(ordered[:-1])  # missing one
