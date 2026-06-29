"""
Report authoring pipeline.

A drafting assistant for the certified bygningssakkyndige (NOT an autonomous
issuer — only a certified expert may issue a tilstandsrapport). Turns structured
findings into a draft report through collaborating agents:

  findings → [CostAnalyst] → costs
           → [Composer] assembles the report → [QAEngine] evaluates
           → (revise loop) → draft for the inspector to review and sign

All agents are provider-agnostic with a `fake` offline implementation, so the
whole pipeline runs and is tested without an API key. The evaluation stage reuses
the existing QAEngine; compose→evaluate→revise is the iterate-to-pass loop.

Cost estimates are grounded in prices.py and labelled indicative — see that
module's disclaimer; they must be verified by the inspector.
"""

import json
from typing import List, Optional, Protocol

from pydantic import BaseModel, ValidationError

import config
import prices


class AuthoringError(RuntimeError):
    """Raised when a cost/compose agent fails."""


# --- data models ---
class Finding(BaseModel):
    part: str                      # building part, e.g. "kjeller", "bad", "tak"
    observation: str               # what was seen
    measurement: str = ""          # e.g. "22 % fukt, 1,5 m²"
    cause: str = ""
    consequence: str = ""
    recommendation: str = ""
    severity: str = "TG2"          # TG0..TG3

    def search_text(self) -> str:
        return f"{self.part} {self.observation} {self.recommendation}"


class CostEstimate(BaseModel):
    part: str
    measure: str
    low: int = 0
    high: int = 0
    unit: str = "kr"
    assumption: str = ""
    basis: str = ""                # which price reference / source


# --- agent protocols ---
class CostAnalyst(Protocol):
    def estimate(self, finding: Finding) -> CostEstimate: ...


class Composer(Protocol):
    def compose(self, findings: List[Finding], costs: List[CostEstimate],
                feedback: str = "") -> str: ...


# --- fake (offline) agents ---
class FakeCostAnalyst:
    """Grounds each estimate in the nearest price-reference entry (no network)."""

    def estimate(self, finding: Finding) -> CostEstimate:
        match = prices.lookup(finding.search_text(), k=1)
        if match:
            m = match[0]
            return CostEstimate(part=finding.part, measure=m["item"], low=m["low"],
                                high=m["high"], unit=m["unit"],
                                assumption="Indicative range from price reference.",
                                basis=prices.SOURCE)
        return CostEstimate(part=finding.part,
                            measure=finding.recommendation or finding.observation,
                            assumption="No matching price reference.", basis="")


class FakeComposer:
    """Deterministically assembles the six required sections from the inputs."""

    def compose(self, findings: List[Finding], costs: List[CostEstimate],
                feedback: str = "") -> str:
        worst = max((f.severity for f in findings), default="TG1")
        obs = "\n".join(
            f"- {f.part}: {f.observation}" + (f" ({f.measurement})" if f.measurement else "")
            for f in findings
        )
        cause = " ".join(f"{f.part}: {f.cause}" for f in findings if f.cause) or \
            "Årsak vurdert ut fra observasjoner ved befaring."
        cons = " ".join(f.consequence for f in findings if f.consequence) or \
            "Avvik kan forverres dersom tiltak ikke gjennomføres."
        rec = "\n".join(f"- {f.part}: {f.recommendation or 'utbedres'}" for f in findings)
        cost_lines = "\n".join(
            f"- {c.measure}: {c.low}–{c.high} {c.unit}" for c in costs if c.high
        ) or "- Kostnad ikke fastsatt."
        total_low, total_high = sum(c.low for c in costs), sum(c.high for c in costs)
        return "\n\n".join([
            f"Sammendrag\nBefaringen avdekket {len(findings)} forhold. Høyeste "
            f"tilstandsgrad er {worst}. Se anbefalte tiltak og kostnadsoverslag nedenfor.",
            f"Observasjoner\n{obs}",
            f"Årsak\n{cause}",
            f"Konsekvenser\n{cons}",
            f"Anbefalinger\n{rec}",
            f"Kostnadsestimat\n{cost_lines}\nSamlet anslag: {total_low}–{total_high} kr. "
            f"{prices.DISCLAIMER}",
        ])


# --- real (LLM) agents ---
_COST_SYSTEM = (
    "You are a Norwegian building cost estimator. Ground every estimate in the "
    "provided price reference and state your assumption. Respond only with JSON."
)
_COMPOSE_SYSTEM = (
    "You are a Norwegian bygningssakkyndig drafting a tilstandsrapport per NS 3600 / "
    "forskrift til avhendingslova. For each deviation give cause, consequence, "
    "recommended measure and cost. Write the report in Norwegian with the six section "
    "headings: Sammendrag, Observasjoner, Årsak, Konsekvenser, Anbefalinger, Kostnadsestimat."
)

_COST_SCHEMA = {
    "type": "object",
    "properties": {
        "measure": {"type": "string"},
        "low": {"type": "integer"},
        "high": {"type": "integer"},
        "unit": {"type": "string"},
        "assumption": {"type": "string"},
    },
    "required": ["measure", "low", "high"],
    "additionalProperties": False,
}


def _cost_prompt(finding: Finding) -> str:
    ctx = prices.as_context(prices.lookup(finding.search_text()))
    return (f"Finding — {finding.part}: {finding.observation} "
            f"({finding.measurement}). Recommended measure: {finding.recommendation or 'TBD'}.\n\n"
            f"PRICE REFERENCE:\n{ctx}\n\n"
            f'Return JSON: {{"measure": "...", "low": int, "high": int, "unit": "kr or kr/m² etc", '
            f'"assumption": "..."}}')


def _compose_prompt(findings: List[Finding], costs: List[CostEstimate], feedback: str) -> str:
    f_lines = "\n".join(
        f"- {f.part} [{f.severity}]: {f.observation} ({f.measurement}); cause={f.cause}; "
        f"consequence={f.consequence}; measure={f.recommendation}" for f in findings
    )
    c_lines = "\n".join(f"- {c.part}: {c.measure} {c.low}–{c.high} {c.unit}" for c in costs)
    fb = f"\n\nAddress this reviewer feedback in the revision:\n{feedback}" if feedback else ""
    return (f"FINDINGS:\n{f_lines}\n\nCOST ESTIMATES:\n{c_lines}\n\n"
            f"Write the full tilstandsrapport in Norwegian with the six headings.{fb}")


class _OpenAIBase:
    def __init__(self, api_key: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key)
        self.model = model


class OpenAICostAnalyst(_OpenAIBase):
    def estimate(self, finding: Finding) -> CostEstimate:
        try:
            r = self.client.chat.completions.create(
                model=self.model, temperature=0.2, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": _COST_SYSTEM},
                          {"role": "user", "content": _cost_prompt(finding)}])
            data = json.loads(r.choices[0].message.content)
        except Exception as exc:
            raise AuthoringError(f"OpenAI cost estimate failed: {exc}") from exc
        return _cost_from_dict(finding, data)


class OpenAIComposer(_OpenAIBase):
    def compose(self, findings, costs, feedback="") -> str:
        try:
            r = self.client.chat.completions.create(
                model=self.model, temperature=0.4,
                messages=[{"role": "system", "content": _COMPOSE_SYSTEM},
                          {"role": "user", "content": _compose_prompt(findings, costs, feedback)}])
            return r.choices[0].message.content
        except Exception as exc:
            raise AuthoringError(f"OpenAI compose failed: {exc}") from exc


class AnthropicCostAnalyst:
    def __init__(self, api_key: str, model: str):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def estimate(self, finding: Finding) -> CostEstimate:
        try:
            r = self.client.messages.create(
                model=self.model, max_tokens=512, system=_COST_SYSTEM,
                messages=[{"role": "user", "content": _cost_prompt(finding)}],
                output_config={"format": {"type": "json_schema", "schema": _COST_SCHEMA}})
            text = next((b.text for b in r.content if getattr(b, "type", None) == "text"), "")
            data = json.loads(text)
        except Exception as exc:
            raise AuthoringError(f"Anthropic cost estimate failed: {exc}") from exc
        return _cost_from_dict(finding, data)


class AnthropicComposer:
    def __init__(self, api_key: str, model: str):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def compose(self, findings, costs, feedback="") -> str:
        try:
            r = self.client.messages.create(
                model=self.model, max_tokens=2048, system=_COMPOSE_SYSTEM,
                messages=[{"role": "user", "content": _compose_prompt(findings, costs, feedback)}])
            return next((b.text for b in r.content if getattr(b, "type", None) == "text"), "")
        except Exception as exc:
            raise AuthoringError(f"Anthropic compose failed: {exc}") from exc


def _cost_from_dict(finding: Finding, data: dict) -> CostEstimate:
    try:
        return CostEstimate(part=finding.part, basis=prices.SOURCE,
                            measure=str(data.get("measure", finding.recommendation)),
                            low=int(data.get("low", 0)), high=int(data.get("high", 0)),
                            unit=str(data.get("unit", "kr")),
                            assumption=str(data.get("assumption", "")))
    except (ValidationError, ValueError, TypeError) as exc:
        raise AuthoringError(f"Cost estimate failed validation: {exc}") from exc


# --- factories ---
def get_cost_analyst(provider: Optional[str] = None) -> CostAnalyst:
    provider = (provider or config.LLM_PROVIDER).lower()
    if provider == "fake":
        return FakeCostAnalyst()
    if provider == "openai":
        if not config.OPENAI_API_KEY:
            raise AuthoringError("OPENAI_API_KEY not set (or use LLM_PROVIDER=fake).")
        return OpenAICostAnalyst(config.OPENAI_API_KEY, config.OPENAI_MODEL)
    if provider in ("anthropic", "agent"):
        if not config.ANTHROPIC_API_KEY:
            raise AuthoringError("ANTHROPIC_API_KEY not set (or use LLM_PROVIDER=fake).")
        return AnthropicCostAnalyst(config.ANTHROPIC_API_KEY, config.ANTHROPIC_MODEL)
    raise AuthoringError(f"Unknown provider '{provider}' for cost analysis")


def get_composer(provider: Optional[str] = None) -> Composer:
    provider = (provider or config.LLM_PROVIDER).lower()
    if provider == "fake":
        return FakeComposer()
    if provider == "openai":
        if not config.OPENAI_API_KEY:
            raise AuthoringError("OPENAI_API_KEY not set (or use LLM_PROVIDER=fake).")
        return OpenAIComposer(config.OPENAI_API_KEY, config.OPENAI_MODEL)
    if provider in ("anthropic", "agent"):
        if not config.ANTHROPIC_API_KEY:
            raise AuthoringError("ANTHROPIC_API_KEY not set (or use LLM_PROVIDER=fake).")
        return AnthropicComposer(config.ANTHROPIC_API_KEY, config.ANTHROPIC_MODEL)
    raise AuthoringError(f"Unknown provider '{provider}' for composing")


# --- orchestrator ---
class AuthoringResult(BaseModel):
    report_text: str
    costs: List[CostEstimate]
    final_quality: str
    issues: list
    revisions: int


class AuthoringPipeline:
    """findings → costs → compose → evaluate → (revise until clean or capped)."""

    def __init__(self, cost_analyst: CostAnalyst, composer: Composer, engine):
        self.cost_analyst = cost_analyst
        self.composer = composer
        self.engine = engine

    def run(self, findings: List[Finding], max_revisions: int = 1) -> AuthoringResult:
        costs = [self.cost_analyst.estimate(f) for f in findings]
        draft = self.composer.compose(findings, costs)
        result = self.engine.evaluate(draft)
        revisions = 0
        while result["final_quality"] != "clean" and revisions < max_revisions:
            feedback = "; ".join(i["comment"] for i in result["issues"])
            draft = self.composer.compose(findings, costs, feedback=feedback)
            result = self.engine.evaluate(draft)
            revisions += 1
        return AuthoringResult(report_text=draft, costs=costs,
                               final_quality=result["final_quality"],
                               issues=result["issues"], revisions=revisions)
