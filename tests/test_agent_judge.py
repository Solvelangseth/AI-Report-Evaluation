"""AgentJudge loop tests driven by a scripted fake Anthropic client (offline)."""

from types import SimpleNamespace

import pytest

from judge import AgentJudge, JudgeError
from qa_engine import QAEngine


def tool_use(name, tool_input, block_id="t1"):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def response(content):
    return SimpleNamespace(stop_reason="tool_use", content=content)


class ScriptedClient:
    """Returns pre-scripted responses; records the tool names it was asked about."""

    def __init__(self, scripted):
        self._scripted = scripted
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        resp = self._scripted[self.calls]
        self.calls += 1
        return resp


def test_agent_investigates_then_reports():
    # Turn 1: agent searches. Turn 2: agent submits its verdict.
    client = ScriptedClient([
        response([tool_use("search_similar_reports", {"query": "fukt"}, "a1")]),
        response([tool_use("report_issues",
                           {"issues": [{"type": "major", "comment": "vague",
                                        "text_snippet": "kanskje"}]}, "a2")]),
    ])
    judge = AgentJudge(client=client, rag=None, max_iterations=4)
    verdict = judge.evaluate("Det er kanskje lekkasje.", "review this")
    assert client.calls == 2  # it looped: searched, then reported
    assert len(verdict.issues) == 1
    assert verdict.issues[0].type == "major"


def test_agent_verdict_flows_through_engine_spans():
    # End-to-end: the engine turns the agent's snippet into a real span.
    client = ScriptedClient([
        response([tool_use("report_issues",
                           {"issues": [{"type": "major", "comment": "vague",
                                        "text_snippet": "kanskje lekkasje"}]}, "a1")]),
    ])
    eng = QAEngine(judge=AgentJudge(client=client, rag=None), rag=None)
    text = "### Sammendrag\nDet er kanskje lekkasje i kjelleren."
    res = eng.evaluate(text)
    agent_issue = [i for i in res["issues"] if i["comment"] == "vague"][0]
    start, end = map(int, agent_issue["span"].split(":"))
    assert text[start:end] == "kanskje lekkasje"


def test_agent_raises_without_verdict():
    # Always asks for a tool, never reports → exhausts iterations.
    client = ScriptedClient([response([tool_use("recheck_rules", {})]) for _ in range(5)])
    judge = AgentJudge(client=client, rag=None, max_iterations=3)
    with pytest.raises(JudgeError):
        judge.evaluate("### Sammendrag\nx", "review")


def test_agent_falls_back_to_text_json():
    # No tool calls, just a JSON text answer → parsed as the verdict.
    client = ScriptedClient([
        SimpleNamespace(stop_reason="end_turn",
                        content=[text_block('{"issues": [{"type": "minor", "comment": "x"}]}')]),
    ])
    verdict = AgentJudge(client=client, rag=None).evaluate("text", "review")
    assert verdict.issues[0].type == "minor"
