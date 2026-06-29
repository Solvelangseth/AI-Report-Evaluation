"""
RAG-curation agent — closes the feedback loop.

When a reviewer *overrides* a QA verdict, that correction is the most valuable
training signal we have. This module distills each override into a new
``RAGExample`` (title, topic, excerpt, guidance) so the retrieval store grows
from real corrections and future similar reports are judged correctly.

The curator is provider-agnostic, mirroring judge.py:
- ``openai`` / ``anthropic`` — LLM with schema-validated structured output.
- ``fake`` — deterministic, offline. Lets the whole flywheel run without a key.

The corrected quality label is taken from the human review (ground truth), never
from the LLM — the model only writes the supporting title/topic/excerpt/guidance.
New examples are embedded lazily by the RAG layer on next use.
"""

import json
from typing import List, Optional, Protocol

from pydantic import BaseModel, ValidationError, field_validator

import config
from db_setup import RAGExample, Review

SYSTEM_PROMPT = (
    "You curate reusable QA reference examples for Norwegian building inspection "
    "reports. Given a reviewer's correction, write a concise example that will help a "
    "future evaluator judge a SIMILAR report correctly. Respond only with the requested JSON."
)


class CurationError(RuntimeError):
    """Raised when a curator cannot produce a valid draft."""


class CurationDraft(BaseModel):
    """The LLM-authored parts of a curated example (label comes from the review)."""

    title: str
    guidance: str
    topic: str = ""
    report_excerpt: str = ""

    @field_validator("title", "guidance")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("must not be empty")
        return value


CURATION_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "topic": {"type": "string"},
        "report_excerpt": {"type": "string"},
        "guidance": {"type": "string"},
    },
    "required": ["title", "guidance"],
    "additionalProperties": False,
}


def build_context(report, qa_result, review) -> str:
    issues = "; ".join(i.comment for i in qa_result.issues) or "none recorded"
    return f"""A reviewer corrected the QA verdict on a Norwegian building inspection report.

REPORT TOPIC: {report.topic}
MODEL VERDICT: {qa_result.final_quality}
CORRECT LABEL (reviewer): {review.corrected_quality}
REVIEWER NOTE: {review.note or "(none)"}
ISSUES THE MODEL FLAGGED: {issues}

REPORT TEXT:
{report.report_text[:1500]}

Produce a reusable reference example. Return STRICT JSON of this shape:
{{"title": "short descriptive title", "topic": "the Norwegian topic", "report_excerpt": "a short exact excerpt from the report", "guidance": "instruction explaining when to apply this and why, reflecting the reviewer's correction"}}.""".strip()


def _parse_draft(raw: str) -> CurationDraft:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise CurationError(f"Curator returned non-JSON output: {exc}") from exc
    try:
        return CurationDraft.model_validate(data)
    except ValidationError as exc:
        raise CurationError(f"Curator output failed validation: {exc}") from exc


class Curator(Protocol):
    def draft(self, context: str) -> CurationDraft: ...


class FakeCurator:
    """Offline curator: deterministic template draft (no network)."""

    def draft(self, context: str) -> CurationDraft:
        return CurationDraft(
            title="Curated correction",
            topic="",
            report_excerpt="",
            guidance="A reviewer corrected the model here; apply the corrected label "
                     "to similar reports and weigh the same issues.",
        )


class OpenAICurator:
    def __init__(self, api_key: str, model: str):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def draft(self, context: str) -> CurationDraft:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
                temperature=0.3,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            raise CurationError(f"OpenAI request failed: {exc}") from exc
        return _parse_draft(response.choices[0].message.content)


class AnthropicCurator:
    def __init__(self, api_key: str, model: str):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def draft(self, context: str) -> CurationDraft:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": context}],
                output_config={"format": {"type": "json_schema", "schema": CURATION_SCHEMA}},
            )
        except Exception as exc:
            raise CurationError(f"Anthropic request failed: {exc}") from exc
        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), "")
        return _parse_draft(text)


def get_curator(provider: Optional[str] = None) -> Curator:
    """Build the configured curator. Raises CurationError if it can't be built."""
    provider = (provider or config.LLM_PROVIDER).lower()
    if provider == "fake":
        return FakeCurator()
    if provider == "openai":
        if not config.OPENAI_API_KEY:
            raise CurationError("OPENAI_API_KEY is not set (or use LLM_PROVIDER=fake offline).")
        return OpenAICurator(config.OPENAI_API_KEY, config.OPENAI_MODEL)
    if provider in ("anthropic", "agent"):
        if not config.ANTHROPIC_API_KEY:
            raise CurationError("ANTHROPIC_API_KEY is not set (or use LLM_PROVIDER=fake offline).")
        return AnthropicCurator(config.ANTHROPIC_API_KEY, config.ANTHROPIC_MODEL)
    raise CurationError(f"Unknown provider '{provider}' for curation")


def _unique_title(session, title: str, review_id: int) -> str:
    """RAGExample.title is unique — disambiguate collisions with the review id."""
    if session.query(RAGExample).filter_by(title=title).first():
        return f"{title} (#{review_id})"
    return title


def curate_review(session, review: Review, curator: Curator) -> Optional[RAGExample]:
    """Distill one override review into a RAGExample. Idempotent; overrides only."""
    if review.decision != "overridden":
        return None
    if review.rag_example_id:
        return session.get(RAGExample, review.rag_example_id)

    qa_result = review.qa_result
    report = qa_result.report
    draft = curator.draft(build_context(report, qa_result, review))

    example = RAGExample(
        title=_unique_title(session, draft.title, review.id),
        topic=(draft.topic or report.topic).strip(),
        quality_label=review.corrected_quality,  # ground truth, not the LLM's guess
        report_excerpt=(draft.report_excerpt or report.report_text[:600])[:1000],
        guidance=draft.guidance,
        source="curation",
    )
    session.add(example)
    session.flush()
    review.rag_example_id = example.id
    session.commit()
    return example


def curate_pending(session, curator: Curator) -> int:
    """Curate all override reviews not yet turned into examples. Returns the count."""
    pending: List[Review] = (
        session.query(Review)
        .filter(Review.decision == "overridden", Review.rag_example_id == None)  # noqa: E711
        .all()
    )
    done = 0
    for review in pending:
        try:
            if curate_review(session, review, curator):
                done += 1
        except CurationError as exc:
            session.rollback()
            print(f"Curation failed for review {review.id}: {exc}")
    return done
