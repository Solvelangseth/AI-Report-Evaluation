"""
Tilstandsgrad summary — the standard TG overview table.

A tilstandsrapport leads with a table of each building part and its condition
grade (TG0–TG3, or TGiU when not investigated). This derives that table from the
findings, taking the **worst** grade per part. Pure — works on anything with
`.part` and `.severity` (authoring `Finding`s or `SessionFinding` rows).
"""

from collections import OrderedDict
from typing import Iterable

_RANK = {"TG0": 0, "TG1": 1, "TG2": 2, "TG3": 3}
GRADES = ["TG0", "TG1", "TG2", "TG3", "TGiU"]


def worst_tg(grades: Iterable[str]) -> str:
    """Highest-severity grade in ``grades``; 'TGiU' if only not-investigated; '' if empty."""
    grades = list(grades)
    ranked = [g for g in grades if g in _RANK]
    if ranked:
        return max(ranked, key=lambda g: _RANK[g])
    return "TGiU" if grades else ""


def summarize(findings) -> dict:
    """Return {parts: [{part, tg, count}], by_grade, highest, total}, worst part first."""
    by_part = OrderedDict()
    for f in findings:
        by_part.setdefault(f.part or "generelt", []).append(f.severity or "TG2")

    parts = [{"part": part, "tg": worst_tg(grades), "count": len(grades)}
             for part, grades in by_part.items()]
    parts.sort(key=lambda row: _RANK.get(row["tg"], -1), reverse=True)

    all_grades = [f.severity or "TG2" for f in findings]
    by_grade = {g: sum(1 for x in all_grades if x == g) for g in GRADES}
    return {"parts": parts, "by_grade": by_grade,
            "highest": worst_tg(all_grades), "total": len(all_grades)}
