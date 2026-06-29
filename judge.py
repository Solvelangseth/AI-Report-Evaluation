"""
LLM judge — provider-agnostic, schema-validated.

The judge turns a report + prompt into a validated list of issues. Output is
parsed through a Pydantic schema so malformed model output is rejected loudly
instead of silently degrading to "unknown" (the old behaviour).

Providers:
- ``openai``    — default; uses the OpenAI Chat Completions API (gpt-4o-mini).
- ``anthropic`` — Claude via the official Anthropic SDK with structured outputs.
- ``fake``      — no network; returns no LLM issues. Lets the whole pipeline run
                  offline (rule-based QA still applies) and powers the tests.

Select via ``LLM_PROVIDER`` in the environment (see config.py).
"""

import json
from typing import List, Optional, Protocol

from pydantic import BaseModel, Field, ValidationError, field_validator

import config

SYSTEM_PROMPT = (
    "You are a quality assurance expert for Norwegian building inspection reports. "
    "Respond only with the JSON object requested by the user."
)


class JudgeError(RuntimeError):
    """Raised when the judge cannot produce a valid verdict."""


class LLMIssue(BaseModel):
    """A single issue reported by the LLM."""

    type: str
    comment: str
    text_snippet: Optional[str] = None

    @field_validator("type")
    @classmethod
    def _normalize_type(cls, value: str) -> str:
        return "major" if str(value).lower().strip() == "major" else "minor"

    @field_validator("comment")
    @classmethod
    def _clean_comment(cls, value: str) -> str:
        value = (value or "").strip()
        return value or "No comment provided"


class LLMVerdict(BaseModel):
    """The full structured response from the judge."""

    issues: List[LLMIssue] = Field(default_factory=list)


# JSON schema handed to providers that support structured outputs.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["minor", "major"]},
                    "text_snippet": {"type": "string"},
                    "comment": {"type": "string"},
                },
                "required": ["type", "comment"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["issues"],
    "additionalProperties": False,
}


def _parse_verdict(raw: str) -> LLMVerdict:
    """Parse and validate a raw JSON string into an LLMVerdict."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise JudgeError(f"Judge returned non-JSON output: {exc}") from exc

    # Tolerate a bare list as well as the documented {"issues": [...]} object.
    if isinstance(data, list):
        data = {"issues": data}
    return _verdict_from_dict(data)


def _verdict_from_dict(data) -> LLMVerdict:
    """Validate an already-parsed dict (e.g. a tool-call input) into a verdict."""
    if isinstance(data, list):
        data = {"issues": data}
    try:
        return LLMVerdict.model_validate(data)
    except ValidationError as exc:
        raise JudgeError(f"Judge output failed schema validation: {exc}") from exc


class Judge(Protocol):
    """Anything that can turn a report + prompt into a validated verdict.

    ``report_text`` is the raw report (the simple judges ignore it — it's already
    inside ``prompt`` — but the agent judge needs it for its tools).
    """

    def evaluate(self, report_text: str, prompt: str) -> LLMVerdict: ...


class FakeJudge:
    """Offline judge: contributes no LLM issues. Rule-based QA still runs."""

    def evaluate(self, report_text: str, prompt: str) -> LLMVerdict:
        return LLMVerdict(issues=[])


class OpenAIJudge:
    """OpenAI Chat Completions judge with JSON-object output + schema validation."""

    def __init__(self, api_key: str, model: str):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def evaluate(self, report_text: str, prompt: str) -> LLMVerdict:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # network / API failure
            raise JudgeError(f"OpenAI request failed: {exc}") from exc
        return _parse_verdict(response.choices[0].message.content)


class AnthropicJudge:
    """Claude judge using the official Anthropic SDK with structured outputs."""

    def __init__(self, api_key: str, model: str):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def evaluate(self, report_text: str, prompt: str) -> LLMVerdict:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
            )
        except Exception as exc:
            raise JudgeError(f"Anthropic request failed: {exc}") from exc

        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), "")
        return _parse_verdict(text)


AGENT_SYSTEM_PROMPT = (
    "You are a meticulous QA reviewer for Norwegian building inspection reports. "
    "Investigate before ruling: look up the relevant standards, re-run the rule "
    "checks, retrieve similar reference reports, and confirm the exact span of every "
    "issue with quote_span. When your review is complete, call report_issues exactly "
    "once with the final list. Do not guess spans — verify them."
)


class AgentJudge:
    """Claude judge that uses tools to investigate before ruling.

    Runs a tool-use loop: the model can call the read-only review tools any number
    of times, then submits its verdict via the terminal ``report_issues`` tool.
    A ``client`` can be injected for testing; otherwise the Anthropic SDK is used.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 client=None, rag="auto", max_iterations: int = 6):
        if client is not None:
            self.client = client
        else:
            import anthropic

            self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model or config.ANTHROPIC_MODEL
        self.max_iterations = max_iterations
        self._rag = rag

    def _resolve_rag(self):
        if self._rag == "auto":
            from rag_pipeline import RAGPipeline
            from db_setup import get_session

            self._rag = RAGPipeline(get_session())
        return self._rag if self._rag not in (None, "none") else None

    def evaluate(self, report_text: str, prompt: str) -> LLMVerdict:
        from agent_tools import ReviewToolbox, TOOL_SCHEMAS, REPORT_ISSUES_TOOL

        toolbox = ReviewToolbox(report_text, self._resolve_rag())
        tools = TOOL_SCHEMAS + [REPORT_ISSUES_TOOL]
        messages = [{"role": "user", "content": prompt}]

        for _ in range(self.max_iterations):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system=AGENT_SYSTEM_PROMPT,
                    tools=tools,
                    messages=messages,
                )
            except Exception as exc:
                raise JudgeError(f"Agent request failed: {exc}") from exc

            tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]

            # Verdict submitted → done.
            for block in tool_uses:
                if block.name == "report_issues":
                    return _verdict_from_dict(block.input)

            # No tools called → try to read a JSON verdict from the text, else fail.
            if not tool_uses:
                text = next((b.text for b in response.content
                             if getattr(b, "type", None) == "text"), "")
                return _parse_verdict(text)

            # Execute the read-only tools and feed results back.
            messages.append({"role": "assistant", "content": response.content})
            results = [
                {"type": "tool_result", "tool_use_id": block.id,
                 "content": toolbox.run(block.name, block.input)}
                for block in tool_uses
            ]
            messages.append({"role": "user", "content": results})

        raise JudgeError("Agent did not submit a verdict within max_iterations")


def get_judge(provider: Optional[str] = None) -> Judge:
    """Build the configured judge. Raises JudgeError if it can't be constructed."""
    provider = (provider or config.LLM_PROVIDER).lower()

    if provider == "fake":
        return FakeJudge()
    if provider == "openai":
        if not config.OPENAI_API_KEY:
            raise JudgeError(
                "OPENAI_API_KEY is not set. Add it to .env, or set LLM_PROVIDER=fake "
                "to run offline with rule-based QA only."
            )
        return OpenAIJudge(config.OPENAI_API_KEY, config.OPENAI_MODEL)
    if provider == "anthropic":
        if not config.ANTHROPIC_API_KEY:
            raise JudgeError(
                "ANTHROPIC_API_KEY is not set. Add it to .env, or set LLM_PROVIDER=fake "
                "to run offline with rule-based QA only."
            )
        return AnthropicJudge(config.ANTHROPIC_API_KEY, config.ANTHROPIC_MODEL)
    if provider == "agent":
        if not config.ANTHROPIC_API_KEY:
            raise JudgeError(
                "The agent judge uses Claude tool-use; set ANTHROPIC_API_KEY in .env "
                "(or use LLM_PROVIDER=fake for offline rule-based QA)."
            )
        return AgentJudge(api_key=config.ANTHROPIC_API_KEY, model=config.ANTHROPIC_MODEL)

    raise JudgeError(
        f"Unknown LLM_PROVIDER '{provider}' (expected openai, anthropic, agent, or fake)"
    )
