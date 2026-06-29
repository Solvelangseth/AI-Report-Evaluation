from qa_rules import QABaseline


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
