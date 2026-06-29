from judge import get_judge, LLMVerdict, LLMIssue
from qa_engine import QAEngine


class StubJudge:
    """Returns a fixed verdict so we can test merging/spans deterministically."""

    def __init__(self, issues):
        self._issues = issues

    def evaluate(self, report_text, prompt):
        return LLMVerdict(issues=self._issues)


def test_fake_judge_pipeline_uses_rules_only():
    eng = QAEngine(judge=get_judge("fake"), rag=None)
    bad = "Bare noe tekst uten seksjoner."
    res = eng.evaluate(bad, expected_status="major_error")
    assert res["final_quality"] == "major_error"  # missing required sections
    assert res["llm_quality"] == "clean"  # fake judge contributes nothing


def test_llm_snippet_resolves_to_span():
    snippet = "kanskje lekkasje"
    eng = QAEngine(
        judge=StubJudge([LLMIssue(type="major", comment="vague", text_snippet=snippet)]),
        rag=None,
    )
    text = "### Sammendrag\nDet er kanskje lekkasje i kjelleren."
    res = eng.evaluate(text)
    spans = [i["span"] for i in res["issues"] if i["comment"] == "vague"]
    assert spans and spans[0] != "0:0"
    start, end = map(int, spans[0].split(":"))
    assert text[start:end] == snippet


def test_judge_failure_degrades_gracefully():
    class FailingJudge:
        def evaluate(self, report_text, prompt):
            from judge import JudgeError
            raise JudgeError("boom")

    eng = QAEngine(judge=FailingJudge(), rag=None)
    res = eng.evaluate("### Sammendrag\n" + "x" * 100)
    assert res["llm_quality"] == "error"
    # 'error' is ignored by worst_quality, so final reflects rules only.
    assert res["final_quality"] in ("clean", "minor_error", "major_error")
