"""Tilstandsgrad summary (pure) + its rendering in the PDF/report page."""

from types import SimpleNamespace

import tg_summary


def _f(part, severity):
    return SimpleNamespace(part=part, severity=severity)


def test_worst_tg_picks_highest_severity():
    assert tg_summary.worst_tg(["TG1", "TG3", "TG2"]) == "TG3"
    assert tg_summary.worst_tg(["TG0", "TG1"]) == "TG1"


def test_worst_tg_handles_tgiu_and_empty():
    assert tg_summary.worst_tg(["TGiU"]) == "TGiU"      # only not-investigated
    assert tg_summary.worst_tg(["TGiU", "TG2"]) == "TG2"  # a real grade wins
    assert tg_summary.worst_tg([]) == ""


def test_summarize_groups_worst_per_part_sorted():
    findings = [_f("bad", "TG2"), _f("bad", "TG3"), _f("kjeller", "TG1"),
                _f("tak", "TG2")]
    s = tg_summary.summarize(findings)
    parts = {p["part"]: p for p in s["parts"]}
    assert parts["bad"]["tg"] == "TG3" and parts["bad"]["count"] == 2
    assert parts["kjeller"]["tg"] == "TG1"
    assert s["highest"] == "TG3" and s["total"] == 4
    assert s["parts"][0]["part"] == "bad"               # worst first
    assert s["by_grade"]["TG2"] == 2 and s["by_grade"]["TG3"] == 1


def test_summarize_empty():
    s = tg_summary.summarize([])
    assert s["parts"] == [] and s["highest"] == "" and s["total"] == 0
