"""
Tools for the review agent.

Each tool wraps existing logic so the agent can *investigate* a report before
ruling on it, instead of guessing in one shot:

- search_similar_reports → RAG retrieval (semantic/lexical)
- get_rule              → QABaseline (the QA standards)
- recheck_rules         → deterministic rule checks on the report
- quote_span            → exact character offsets for a snippet (no hallucinated spans)

The ``ReviewToolbox`` is pure and import-cheap, so it is unit-tested without any
LLM. ``TOOL_SCHEMAS`` / ``REPORT_ISSUES_TOOL`` are the Anthropic tool definitions.
"""

import json
from typing import Optional

from judge import RESPONSE_SCHEMA
from qa_engine import rule_based_issues
from qa_rules import QABaseline
from rag_pipeline import RAGPipeline


class ReviewToolbox:
    """Tool implementations bound to one report (and an optional RAG handle)."""

    def __init__(self, report_text: str, rag: Optional[RAGPipeline] = None):
        self.report_text = report_text
        self.rag = rag

    def search_similar_reports(self, query: str, top_k: int = 2) -> str:
        if self.rag is None:
            return "No retrieval database available."
        examples = self.rag.retrieve_examples(query, top_k=top_k)
        return RAGPipeline.build_context(examples)

    def get_rule(self, topic: str) -> str:
        baseline = QABaseline.get_baseline()
        topic = (topic or "all").lower()
        if topic == "sections":
            return "Required sections, in order: " + ", ".join(baseline["required_sections"])
        if topic == "forbidden_words":
            return "Forbidden vague words: " + ", ".join(baseline["forbidden_words"])
        if topic == "quantification":
            return "Quantification rules: " + json.dumps(baseline["quantification_rules"],
                                                         ensure_ascii=False)
        if topic == "structure":
            return "Structure rules: " + json.dumps(baseline["structure_rules"],
                                                    ensure_ascii=False)
        return json.dumps(baseline, ensure_ascii=False)

    def recheck_rules(self) -> str:
        issues = rule_based_issues(self.report_text)
        if not issues:
            return "No rule-based issues found."
        return "\n".join(
            f"- [{i['type']}] {i['comment']} (span {i['span']})" for i in issues
        )

    def quote_span(self, snippet: str) -> str:
        idx = self.report_text.find(snippet)
        if idx == -1:
            return "Snippet not found verbatim in the report. Quote the exact text."
        return f"{idx}:{idx + len(snippet)}"

    def run(self, name: str, tool_input: dict) -> str:
        """Dispatch a tool call by name. Returns a string result."""
        tool_input = tool_input or {}
        if name == "search_similar_reports":
            return self.search_similar_reports(tool_input.get("query", ""),
                                               int(tool_input.get("top_k", 2)))
        if name == "get_rule":
            return self.get_rule(tool_input.get("topic", "all"))
        if name == "recheck_rules":
            return self.recheck_rules()
        if name == "quote_span":
            return self.quote_span(tool_input.get("snippet", ""))
        return f"Unknown tool: {name}"


# --- Anthropic tool definitions ---
TOOL_SCHEMAS = [
    {
        "name": "search_similar_reports",
        "description": "Retrieve reference QA examples similar to a query. Use it to "
                       "see how comparable reports were judged before ruling.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to find similar examples for"},
                "top_k": {"type": "integer", "description": "How many examples (default 2)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_rule",
        "description": "Look up the QA standards (required sections, forbidden words, "
                       "quantification, structure).",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": ["sections", "forbidden_words", "quantification", "structure", "all"],
                }
            },
            "required": ["topic"],
        },
    },
    {
        "name": "recheck_rules",
        "description": "Run the deterministic rule checks on this report and return the "
                       "issues found. Use it to confirm or supplement your own findings.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "quote_span",
        "description": "Return the exact character offsets (start:end) of a snippet in the "
                       "report. Use this for every issue so spans are accurate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "snippet": {"type": "string", "description": "Exact text from the report"}
            },
            "required": ["snippet"],
        },
    },
]

# Terminal tool: the agent calls this once to submit its verdict.
REPORT_ISSUES_TOOL = {
    "name": "report_issues",
    "description": "Submit the final list of QA issues. Call this exactly once when your "
                   "review is complete. Use an empty list if the report is clean.",
    "input_schema": RESPONSE_SCHEMA,
}
