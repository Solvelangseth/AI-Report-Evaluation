"""
Curated unit-price reference for remediation cost estimates.

These ranges ground the cost-analysis agent so it doesn't invent numbers. They
are **indicative drafting placeholders only** — rough Norwegian ranges meant to
be replaced with maintained, sourced data. Cost figures in a tilstandsrapport
are legally relevant; the certified bygningssakkyndige must verify every estimate.
"""

import re
from typing import Dict, List

DISCLAIMER = (
    "Indikative prisanslag for utkast — faktiske kostnader varierer med omfang, "
    "region og tilstand. Må verifiseres av bygningssakkyndig."
)
SOURCE = "Internal indicative price reference (draft placeholders)"

# Each entry: an item, its unit, and a low–high range in NOK. Keep keywords in
# `item` descriptive so lookup can match free-text findings.
PRICE_REFERENCE: List[Dict] = [
    {"item": "drenering rundt grunnmur", "unit": "kr/løpemeter", "low": 3000, "high": 6000},
    {"item": "utvendig fuktsikring grunnmur", "unit": "kr/m²", "low": 1500, "high": 3000},
    {"item": "membran og rehabilitering våtrom/bad", "unit": "kr/m²", "low": 2500, "high": 5000},
    {"item": "omlegging taktekking", "unit": "kr/m²", "low": 1500, "high": 3000},
    {"item": "utskifting bærende takbjelke / råteutbedring", "unit": "kr/stk", "low": 15000, "high": 40000},
    {"item": "utbedring setningsskade / fundamentering", "unit": "kr", "low": 50000, "high": 250000},
    {"item": "reparasjon sprekk i grunnmur", "unit": "kr/løpemeter", "low": 1000, "high": 3000},
    {"item": "utskifting vindu", "unit": "kr/stk", "low": 8000, "high": 15000},
    {"item": "balansert ventilasjonsanlegg", "unit": "kr", "low": 80000, "high": 150000},
    {"item": "utskifting varmtvannsbereder", "unit": "kr/stk", "low": 12000, "high": 25000},
]


def _tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-zA-Z0-9æøåÆØÅ]+", (text or "").lower()) if len(t) > 2]


def lookup(query: str, k: int = 4) -> List[Dict]:
    """Return the price entries most relevant to a free-text query (lexical overlap)."""
    q = set(_tokens(query))
    if not q:
        return []
    scored = []
    for entry in PRICE_REFERENCE:
        overlap = len(q & set(_tokens(entry["item"])))
        if overlap:
            scored.append((overlap, entry))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [entry for _, entry in scored[:k]]


def as_context(entries: List[Dict]) -> str:
    """Format price entries for an LLM prompt."""
    if not entries:
        return "No matching price reference entries."
    return "\n".join(f"- {e['item']}: {e['low']}–{e['high']} {e['unit']}" for e in entries)
