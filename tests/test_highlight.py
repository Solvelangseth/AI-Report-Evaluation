from app import highlight_issues


def _issue(start, end, **kw):
    base = {"id": 1, "type": "minor", "start": start, "end": end, "comment": "c"}
    base.update(kw)
    return base


def test_snippet_is_wrapped_in_span():
    text = "Det er kanskje lekkasje."
    start = text.index("kanskje")
    html = str(highlight_issues(text, [_issue(start, start + 7, comment="vague")]))
    assert 'class="issue-highlight issue-minor"' in html
    assert "kanskje" in html
    assert 'data-comment="vague"' in html


def test_markdown_header_not_corrupted():
    text = "### Sammendrag\n\nfoo bar baz"
    start = text.index("foo")
    html = str(highlight_issues(text, [_issue(start, start + 3, type="major")]))
    assert "<h3>" in html  # header still renders
    assert "issue-major" in html


def test_invalid_span_is_skipped():
    text = "short"
    html = str(highlight_issues(text, [_issue(100, 200)]))
    assert "issue-highlight" not in html
    assert "short" in html


def test_comment_is_escaped():
    text = "alpha beta"
    html = str(highlight_issues(text, [_issue(0, 5, comment='<script>"x"')]))
    assert "<script>" not in html
