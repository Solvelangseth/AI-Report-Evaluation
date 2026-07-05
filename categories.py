"""
Capture categories — what the inspector tags each photo as.

The mobile camera button lets the inspector pick a category before shooting
("Bad", "Tak", …). That tag is a strong prior for the finding's building part
(`Finding.part`) — far better than timestamp-pairing alone, and it lets a photo
seed a finding even when nothing was said at that moment.

Grounded in the forskrift til avhendingslova kap. 2 building parts (see
`db_setup.seed_regulations`). `slug` is the stable API/value; `label` is the
Norwegian UI text; `part` is what flows into `Finding.part`.
"""

from typing import Dict, List, Optional

CATEGORIES: List[Dict[str, str]] = [
    {"slug": "bad", "label": "Bad / våtrom", "part": "bad"},
    {"slug": "kjokken", "label": "Kjøkken", "part": "kjøkken"},
    {"slug": "kjeller", "label": "Kjeller / rom under terreng", "part": "kjeller"},
    {"slug": "tak", "label": "Tak / loft", "part": "tak"},
    {"slug": "yttervegg", "label": "Yttervegg / fasade", "part": "yttervegg"},
    {"slug": "vindu", "label": "Vinduer / ytterdører", "part": "vinduer"},
    {"slug": "grunnmur", "label": "Grunnmur / drenering", "part": "grunnmur"},
    {"slug": "vvs", "label": "Rør / VVS", "part": "vvs"},
    {"slug": "ventilasjon", "label": "Ventilasjon", "part": "ventilasjon"},
    {"slug": "el", "label": "Elektrisk anlegg", "part": "elektrisk"},
    {"slug": "varmtvann", "label": "Varmtvannsbereder", "part": "varmtvannsbereder"},
    {"slug": "balkong", "label": "Balkong / terrasse", "part": "balkong"},
    {"slug": "generelt", "label": "Generelt / annet", "part": "generelt"},
]

_BY_SLUG = {c["slug"]: c for c in CATEGORIES}
SLUGS = set(_BY_SLUG)


def is_valid(slug: Optional[str]) -> bool:
    return slug in _BY_SLUG


def part_for(slug: Optional[str]) -> str:
    """Map a category slug to a `Finding.part` (defaults to 'generelt')."""
    return _BY_SLUG.get(slug or "", _BY_SLUG["generelt"])["part"]


def label_for(slug: Optional[str]) -> str:
    return _BY_SLUG.get(slug or "", _BY_SLUG["generelt"])["label"]
