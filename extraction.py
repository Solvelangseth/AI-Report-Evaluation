"""
Extraction — context bundles → structured `Finding`s.

This is the bridge into the existing authoring pipeline: each `ContextBundle`
(spoken segment + its photos) becomes a `Finding` (the contract `authoring.py`
already consumes). The real extractors are **vision** calls — the inspector says
"tjueto prosent fukt her i hjørnet" and the photo shows the wall, so the model
grounds a `Finding` with part, measurement, severity (TG) and recommendation.

Provider-agnostic with a deterministic `fake` (keyword heuristics, no network),
so `voice + photos → List[Finding]` runs offline end-to-end. The QA layer
downstream is the safety net for thin extractions, and a certified inspector
reviews every finding before composing — this is a drafting assistant.
"""

import base64
import json
import re
from typing import List, Optional, Protocol

import categories
import config
from authoring import Finding
from pairing import ContextBundle

# Norwegian building-part keywords → canonical `Finding.part`.
_PART_KEYWORDS = {
    "kjeller": "kjeller", "krypkjeller": "kjeller", "grunnmur": "grunnmur",
    "drenering": "grunnmur", "bad": "bad", "våtrom": "bad", "dusj": "bad",
    "sluk": "bad", "tak": "tak", "taktekking": "tak", "takbjelke": "tak",
    "loft": "loft", "vindu": "vinduer", "yttervegg": "yttervegg",
    "kjøkken": "kjøkken", "ventilasjon": "ventilasjon", "elektrisk": "elektrisk",
    "varmtvannsbereder": "varmtvannsbereder", "fundament": "grunnmur",
}
# Severity cues → tilstandsgrad.
_TG3_CUES = ("alvorlig", "kritisk", "omfattende", "råte", "svikt i bærende", "fare for")
_TG1_CUES = ("mindre", "normal slitasje", "ubetydelig", "kosmetisk")
_MEASUREMENT_RE = re.compile(r"\d+[.,]?\d*\s*(?:%|mm|cm|m²|m2|m\b|kr|grader)", re.IGNORECASE)


class ExtractionError(RuntimeError):
    """Raised when a vision extractor fails."""


class Extractor(Protocol):
    def extract(self, bundle: ContextBundle) -> Optional[Finding]: ...


# --- fake (offline) extractor ---
class FakeExtractor:
    """Derive a `Finding` from segment text via keyword heuristics (no network)."""

    def extract(self, bundle: ContextBundle) -> Optional[Finding]:
        text = bundle.segment.text.strip()
        captions = " ".join(p.caption for p in bundle.photos if p.caption).strip()
        blob = f"{text} {captions}".strip()
        # A categorized photo can seed a finding even with no spoken segment.
        photo_cat = next((p.category for p in bundle.photos if p.category), "")
        if not blob and not photo_cat:
            return None
        low = blob.lower()
        # A photo's category is the strongest part signal; fall back to keywords.
        if photo_cat:
            part = categories.part_for(photo_cat)
        else:
            part = next((canon for kw, canon in _PART_KEYWORDS.items() if kw in low), "generelt")
        severity = "TG3" if any(c in low for c in _TG3_CUES) else (
            "TG1" if any(c in low for c in _TG1_CUES) else "TG2")
        measurement = ", ".join(dict.fromkeys(_MEASUREMENT_RE.findall(blob)))  # de-dup, keep order
        return Finding(
            part=part,
            observation=text or captions or f"Forhold registrert: {categories.label_for(photo_cat)}",
            measurement=measurement,
            severity=severity,
        )


# --- real (vision) extractors ---
_EXTRACT_SYSTEM = (
    "You are a Norwegian bygningssakkyndig assistant. From an inspector's spoken "
    "observation and any photos, extract ONE structured finding for a tilstandsrapport. "
    "Use the photos to ground the building part, the measurement and the severity. Assign "
    "a tilstandsgrad TG0–TG3. If the segment is just navigation or small talk with no "
    "actual deviation, set is_finding=false. Write all field values in Norwegian. Respond "
    "only with the requested JSON."
)
_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_finding": {"type": "boolean"},
        "part": {"type": "string"},
        "observation": {"type": "string"},
        "measurement": {"type": "string"},
        "cause": {"type": "string"},
        "consequence": {"type": "string"},
        "recommendation": {"type": "string"},
        "severity": {"type": "string", "enum": ["TG0", "TG1", "TG2", "TG3", "TGiU"]},
    },
    "required": ["is_finding", "part", "observation", "severity"],
    "additionalProperties": False,
}

# Image formats Claude/OpenAI vision accept. HEIC and others are skipped (text
# still drives the extraction) rather than sent as an unsupported media type.
_IMAGE_MEDIA = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "webp": "image/webp", "gif": "image/gif"}
_MAX_IMAGES = 4  # cap evidence per segment to bound tokens/latency


def _bundle_text(bundle: ContextBundle) -> str:
    captions = "; ".join(p.caption for p in bundle.photos if p.caption)
    cats = "; ".join(dict.fromkeys(
        categories.label_for(p.category) for p in bundle.photos if p.category))
    return (f"Spoken observation: {bundle.segment.text}\n"
            f"Photo categories (inspector-tagged building part): {cats or '(none)'}\n"
            f"Photo notes: {captions or '(none)'}\n\n"
            f"Use the photo category as the building part unless the image clearly shows "
            f"otherwise. Return JSON with keys is_finding (bool), part, observation, "
            f"measurement, cause, consequence, recommendation, severity (TG0-TG3).")


def _load_images(bundle: ContextBundle):
    """Return [(base64_data, media_type)] for the bundle's supported photos."""
    images = []
    for photo in bundle.photos:
        if not photo.image_path:
            continue
        ext = photo.image_path.rsplit(".", 1)[-1].lower() if "." in photo.image_path else ""
        media = _IMAGE_MEDIA.get(ext)
        if not media:
            continue
        try:
            with open(photo.image_path, "rb") as fh:
                images.append((base64.b64encode(fh.read()).decode(), media))
        except OSError:
            continue
        if len(images) >= _MAX_IMAGES:
            break
    return images


def _finding_from_dict(data: dict, fallback_obs: str) -> Optional[Finding]:
    """Build a Finding, or None when the model says there's no real finding."""
    if data.get("is_finding") is False:
        return None
    try:
        return Finding(
            part=str(data.get("part", "generelt")),
            observation=str(data.get("observation") or fallback_obs),
            measurement=str(data.get("measurement", "")),
            cause=str(data.get("cause", "")),
            consequence=str(data.get("consequence", "")),
            recommendation=str(data.get("recommendation", "")),
            severity=str(data.get("severity", "TG2")),
        )
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError(f"Finding failed validation: {exc}") from exc


class AnthropicExtractor:
    """Claude vision: (transcript snippet + photos) → Finding.

    Accepts an injected ``client`` for offline testing; otherwise uses the SDK.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 client=None):
        if client is not None:
            self.client = client
        else:
            import anthropic
            self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model or config.ANTHROPIC_MODEL

    def extract(self, bundle: ContextBundle) -> Optional[Finding]:
        if not bundle.has_content:
            return None
        content: list = [{"type": "image",
                          "source": {"type": "base64", "media_type": media, "data": b64}}
                         for b64, media in _load_images(bundle)]
        content.append({"type": "text", "text": _bundle_text(bundle)})
        try:
            resp = self.client.messages.create(
                model=self.model, max_tokens=700, system=_EXTRACT_SYSTEM,
                messages=[{"role": "user", "content": content}],
                output_config={"format": {"type": "json_schema", "schema": _EXTRACT_SCHEMA}},
            )
            text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
            data = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError(f"Anthropic extraction failed: {exc}") from exc
        return _finding_from_dict(data, bundle.segment.text)


class OpenAIExtractor:
    """OpenAI vision: (transcript snippet + photos) → Finding."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 client=None):
        if client is not None:
            self.client = client
        else:
            from openai import OpenAI
            self.client = OpenAI(api_key=api_key)
        self.model = model or config.OPENAI_MODEL

    def extract(self, bundle: ContextBundle) -> Optional[Finding]:
        if not bundle.has_content:
            return None
        content: list = [{"type": "text", "text": _bundle_text(bundle)}]
        for b64, media in _load_images(bundle):
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:{media};base64,{b64}"}})
        try:
            resp = self.client.chat.completions.create(
                model=self.model, temperature=0.2,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": _EXTRACT_SYSTEM},
                          {"role": "user", "content": content}])
            data = json.loads(resp.choices[0].message.content)
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError(f"OpenAI extraction failed: {exc}") from exc
        return _finding_from_dict(data, bundle.segment.text)


def get_extractor(provider: Optional[str] = None) -> Extractor:
    provider = (provider or config.LLM_PROVIDER).lower()
    if provider == "fake":
        return FakeExtractor()
    if provider == "openai":
        if not config.OPENAI_API_KEY:
            raise ExtractionError("OPENAI_API_KEY not set (or use LLM_PROVIDER=fake).")
        return OpenAIExtractor(config.OPENAI_API_KEY, config.OPENAI_MODEL)
    if provider in ("anthropic", "agent"):
        if not config.ANTHROPIC_API_KEY:
            raise ExtractionError("ANTHROPIC_API_KEY not set (or use LLM_PROVIDER=fake).")
        return AnthropicExtractor(config.ANTHROPIC_API_KEY, config.ANTHROPIC_MODEL)
    raise ExtractionError(f"Unknown provider '{provider}' for extraction")


def extract_findings(bundles: List[ContextBundle],
                     extractor: Optional[Extractor] = None) -> List[Finding]:
    """Run extraction over every bundle, dropping the ones with no finding."""
    extractor = extractor or get_extractor()
    findings = []
    for bundle in bundles:
        finding = extractor.extract(bundle)
        if finding is not None:
            # Link the evidence photos (their stored filenames) to the finding.
            finding.photo_refs = [p.id for p in bundle.photos if p.id]
            findings.append(finding)
    return findings
