"""
QA engine — pure evaluation logic.

Takes report text in, returns a verdict + issues out. No database, no
filesystem, no global state. This makes it trivially testable and lets the
runner (qa_master.py) and the web app share exactly the same evaluation.

Combines:
- deterministic rule checks (qa_rules.QABaseline)
- an LLM judge (judge.Judge) augmented with RAG examples
and merges them: the final verdict is the most severe of the two.
"""

import re
from typing import Dict, List, Optional, Tuple

import config
import scoring
from judge import Judge, JudgeError, LLMVerdict
from qa_rules import QABaseline

# Recognise section headers written as Markdown (`### X`), bold (`**X**`), or a
# bare line that is exactly a known section name.
_HEADER_MARKDOWN = re.compile(r"^(#{1,6})\s*(.*)$")
_HEADER_EMPHASIS = re.compile(r"^(\*\*|__|\*|_)(.+?)(\*\*|__|\*|_)$")


def _candidate_header(line: str) -> Tuple[str, bool]:
    """Return (cleaned_title, had_markup) for a line.

    ``had_markup`` is True when the line used header syntax (#, **, __), which
    lets us treat fuzzy matches as headers while requiring plain lines to match
    a section name exactly.
    """
    s = line.strip()
    if not s:
        return "", False

    had_markup = False
    m = _HEADER_MARKDOWN.match(s)
    if m:
        had_markup = True
        s = m.group(2).strip()

    e = _HEADER_EMPHASIS.match(s)
    if e:
        had_markup = True
        s = e.group(2).strip()

    return s.rstrip(":").strip(), had_markup


def _match_section(title: str, had_markup: bool, required: List[str]) -> Optional[str]:
    """Map a candidate header title to a required section name, or None."""
    title_lower = title.lower()
    if title_lower in required:
        return title_lower

    # Only fuzzy-match lines that actually looked like headers.
    if had_markup and len(title_lower) <= 40:
        hits = [(title_lower.find(req), req) for req in required if req in title_lower]
        if hits:
            return min(hits)[1]
    return None


def extract_sections(text: str) -> Dict[str, str]:
    """Split report text into {section_name: content} by recognised headers."""
    required = [r.lower() for r in QABaseline.RECOGNIZED_SECTIONS]
    sections: Dict[str, str] = {}
    current: Optional[str] = None
    buffer: List[str] = []

    for line in text.splitlines():
        title, had_markup = _candidate_header(line)
        section = _match_section(title, had_markup, required) if title else None

        if section:
            if current:
                sections[current] = "\n".join(buffer).strip()
            current = section
            buffer = []
        elif current:
            buffer.append(line.strip())

    if current:
        sections[current] = "\n".join(buffer).strip()
    return sections


def rule_based_issues(report_text: str) -> List[Dict]:
    """Run all deterministic checks and return a flat list of issues."""
    issues: List[Dict] = []
    sections = extract_sections(report_text)

    missing = [req for req in QABaseline.REQUIRED_SECTIONS if req not in sections]
    for req in missing:
        issues.append({"type": "major", "span": "0:0",
                       "comment": f"Missing required section: {req}"})

    if not missing and not QABaseline.validate_section_order(list(sections.keys())):
        issues.append({"type": "major", "span": "0:0",
                       "comment": "Sections are not in the correct order"})

    # Rules only flag *objective* issues. Vague language and tone are contextual,
    # so they are left to the LLM judge (the forbidden-word list is passed to it as
    # guidance instead). The one quantification signal we keep is structural: a
    # substantial report with no units at all.
    for quant in QABaseline.check_quantification(report_text):
        if quant.get("type") != "missing_units":
            continue
        issues.append({"type": "minor", "span": quant.get("span", "0:0"),
                       "comment": quant["suggestion"]})

    rules = QABaseline.STRUCTURE_RULES
    for name, content in sections.items():
        if name not in QABaseline.REQUIRED_SECTIONS:
            continue  # optional sections (e.g. tilstandsgrader) aren't length-policed
        length = len(content)
        if length < rules["min_section_length"]:
            issues.append({"type": "minor", "span": f"section:{name}",
                           "comment": f"Section '{name}' too short ({length} chars, "
                                      f"min {rules['min_section_length']})"})
        elif length > rules["max_section_length"]:
            issues.append({"type": "minor", "span": f"section:{name}",
                           "comment": f"Section '{name}' too long ({length} chars, "
                                      f"max {rules['max_section_length']})"})

    return issues


class QAEngine:
    """Evaluates report text using rules + an LLM judge."""

    def __init__(self, judge: Judge, rag=None, top_k: int = config.RAG_TOP_K):
        self.judge = judge
        self.rag = rag
        self.top_k = top_k

    def _build_prompt(self, report_text: str, rag_context: str) -> str:
        baseline = QABaseline.get_baseline()
        return f"""
Evaluate this Norwegian building inspection report (tilstandsrapport) against the
standards used for Norwegian condition reports (NS 3600 / forskrift til avhendingslova).

REPORT:
{report_text}

QUALITY STANDARDS:
1. Required sections, in order: {', '.join(baseline['required_sections'])}.
2. Each reported deviation should state its cause (årsak), consequence (konsekvens),
   recommended measure (anbefaling), and a cost estimate (kostnad) — especially for
   serious deviations (condition grade TG2/TG3).
3. Severity must match urgency: TG3/strakstiltak for serious, planned maintenance for
   moderate. Flag mismatches.
4. Prefer specific, measurable findings with units (m², %, kr, mm) over vague language.
   Judge vagueness IN CONTEXT — terms such as [{', '.join(baseline['forbidden_words'])}]
   are only a problem when they replace a concrete measurement or assessment, NOT in
   ordinary descriptive prose. Do not flag a word merely for appearing.
5. Professional, technical tone.

HOW TO GRADE (be calibrated — do NOT over-flag):
- "major": only for issues that would mislead a buyer or breach the minimum
  requirements — a missing required section, a serious deviation (TG2/TG3) reported
  without its cause/consequence/recommended measure/cost, or a clear contradiction
  between the described severity and the recommended action or cost.
- "minor": improvement suggestions — wording that could be more specific, slightly
  thin descriptions, tone.
- A complete, internally consistent, well-measured report should produce FEW OR NO
  issues. Do not invent problems, and do NOT flag the mere absence of an explicit
  "TG" label when the severity is already clear from the description.

RETRIEVED REFERENCE EXAMPLES AND REGULATION GUIDANCE (RAG):
{rag_context}

Return a STRICT JSON object of this shape:
{{"issues": [{{"type": "minor|major", "text_snippet": "exact phrase with the issue", "comment": "specific improvement suggestion"}}]}}
If there are no issues, return {{"issues": []}}.
""".strip()

    def _llm_issues(self, report_text: str) -> Tuple[List[Dict], str, List[Dict]]:
        examples: List[Dict] = []
        rag_context = "No retrieval database configured."
        if self.rag is not None:
            examples = self.rag.retrieve_examples(report_text, top_k=self.top_k)
            rag_context = self.rag.build_context(examples)

        prompt = self._build_prompt(report_text, rag_context)
        try:
            verdict: LLMVerdict = self.judge.evaluate(report_text, prompt)
        except JudgeError as exc:
            # Degrade gracefully — a judge failure never lowers the verdict.
            print(f"LLM judge unavailable: {exc}")
            return [], "error", examples

        issues: List[Dict] = []
        for issue in verdict.issues:
            span = "0:0"
            if issue.text_snippet:
                start = report_text.find(issue.text_snippet)
                if start != -1:
                    span = f"{start}:{start + len(issue.text_snippet)}"
            issues.append({"type": issue.type, "span": span, "comment": issue.comment})

        return issues, scoring.classify_issues(issues), examples

    @staticmethod
    def _merge(issues: List[Dict]) -> List[Dict]:
        seen = set()
        merged = []
        for issue in issues:
            key = (issue.get("span", ""), issue.get("comment", "")[:30])
            if key not in seen:
                seen.add(key)
                merged.append(issue)
        return merged

    def evaluate(self, report_text: str, expected_status: Optional[str] = None) -> Dict:
        """Evaluate a report. Returns rule/llm/final quality, merged issues, RAG examples."""
        rule_issues = rule_based_issues(report_text)
        rule_quality = scoring.classify_issues(rule_issues)

        llm_issues, llm_quality, rag_examples = self._llm_issues(report_text)

        merged = self._merge(rule_issues + llm_issues)
        final_quality = scoring.worst_quality(rule_quality, llm_quality)

        return {
            "rule_quality": rule_quality,
            "llm_quality": llm_quality,
            "final_quality": final_quality,
            "expected_status": expected_status if expected_status in config.QUALITY_LEVELS else None,
            "issues": merged,
            "rag_examples": rag_examples,
        }
