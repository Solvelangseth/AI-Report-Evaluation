from qa_engine import extract_sections


def test_markdown_headers():
    text = "### Sammendrag\nfoo\n### Observasjoner\nbar"
    secs = extract_sections(text)
    assert set(secs) == {"sammendrag", "observasjoner"}
    assert secs["sammendrag"] == "foo"


def test_bold_and_plain_headers():
    text = "**Sammendrag**\nfoo\nObservasjoner\nbar\nÅrsak\nbaz"
    secs = extract_sections(text)
    assert set(secs) == {"sammendrag", "observasjoner", "årsak"}


def test_fuzzy_header_maps_to_known_section():
    secs = extract_sections("### Observasjoner og funn\nbar")
    assert "observasjoner" in secs


def test_prose_line_is_not_a_header():
    # A body line containing a section word must not be treated as a header.
    text = "### Sammendrag\nDette er en analyse av årsak til skaden."
    secs = extract_sections(text)
    assert set(secs) == {"sammendrag"}
