"""
Quality scoring — the single source of truth for turning issues into a verdict.

Both the rule-based and LLM-based checks, and the final merge, go through these
functions so the thresholds live in exactly one place.
"""

from typing import Dict, Iterable, List

import config

# Worst-to-best severity ranking used to merge multiple verdicts.
_SEVERITY = {"clean": 0, "minor_error": 1, "major_error": 2}


def classify_issues(
    issues: Iterable[Dict],
    minor_threshold: int = config.MINOR_ISSUE_THRESHOLD,
) -> str:
    """Derive a quality label from a list of issues.

    Any ``major`` issue makes the report ``major_error``; more than
    ``minor_threshold`` ``minor`` issues makes it ``minor_error``; otherwise
    ``clean``.
    """
    major = sum(1 for i in issues if i.get("type") == "major")
    minor = sum(1 for i in issues if i.get("type") == "minor")

    if major > 0:
        return "major_error"
    if minor > minor_threshold:
        return "minor_error"
    return "clean"


def confidence(rule_quality: str, llm_quality: str) -> str:
    """Confidence in a verdict, from agreement between the two signals.

    The rule and LLM checks are independent, so their agreement is a cheap,
    deterministic uncertainty signal:
    - both agree            → "high"
    - both disagree         → "low"  (the signals conflict — worth a human look)
    - LLM unavailable/error → "medium" (rules only)
    """
    rule_known = rule_quality in _SEVERITY
    llm_known = llm_quality in _SEVERITY
    if rule_known and llm_known:
        return "high" if rule_quality == llm_quality else "low"
    return "medium"


def worst_quality(*qualities: str) -> str:
    """Return the most severe known quality label among the arguments.

    Unknown labels (e.g. ``"error"`` from a failed LLM call) are ignored so a
    judge failure can never *lower* a verdict. Defaults to ``clean``.
    """
    known: List[str] = [q for q in qualities if q in _SEVERITY]
    if not known:
        return "clean"
    return max(known, key=lambda q: _SEVERITY[q])
