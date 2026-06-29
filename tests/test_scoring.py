import scoring


def test_major_dominates():
    assert scoring.classify_issues([{"type": "major"}, {"type": "minor"}]) == "major_error"


def test_minor_threshold():
    assert scoring.classify_issues([{"type": "minor"}] * 2) == "clean"
    assert scoring.classify_issues([{"type": "minor"}] * 3) == "minor_error"


def test_empty_is_clean():
    assert scoring.classify_issues([]) == "clean"


def test_worst_quality_ranking():
    assert scoring.worst_quality("clean", "minor_error") == "minor_error"
    assert scoring.worst_quality("minor_error", "major_error") == "major_error"


def test_worst_quality_ignores_unknown():
    # A failed judge ("error") must never lower the verdict.
    assert scoring.worst_quality("minor_error", "error") == "minor_error"
    assert scoring.worst_quality("error", "unknown") == "clean"
