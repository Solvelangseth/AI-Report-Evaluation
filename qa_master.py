"""
QA runner — persistence and orchestration.

Wires the pure QAEngine to the database and loops over reports. The engine does
the thinking; this module just loads reports, stores results, and updates
statuses. The database is the single source of truth (no JSON side-files).
"""

from datetime import datetime, timezone
from typing import Optional

import config
from db_setup import get_session, Report, QAResult, QAIssue
from judge import get_judge
from qa_engine import QAEngine
from rag_pipeline import RAGPipeline


class QAEvaluator:
    """Evaluates stored reports and persists the results."""

    def __init__(self, provider: Optional[str] = None):
        self.session = get_session()
        self.engine = QAEngine(judge=get_judge(provider), rag=RAGPipeline(self.session))

    def _persist(self, report: Report, result: dict) -> None:
        existing = self.session.query(QAResult).filter_by(report_id=report.id).first()
        if existing:
            existing.rule_quality = result["rule_quality"]
            existing.llm_quality = result["llm_quality"]
            existing.final_quality = result["final_quality"]
            existing.expected_status = result["expected_status"]
            existing.evaluated_at = datetime.now(timezone.utc)
            self.session.query(QAIssue).filter_by(qa_result_id=existing.id).delete()
            qa_result = existing
        else:
            qa_result = QAResult(
                report_id=report.id,
                rule_quality=result["rule_quality"],
                llm_quality=result["llm_quality"],
                final_quality=result["final_quality"],
                expected_status=result["expected_status"],
            )
            self.session.add(qa_result)

        self.session.flush()
        for issue in result["issues"]:
            self.session.add(QAIssue(
                qa_result_id=qa_result.id,
                issue_type=issue["type"],
                span=issue.get("span", "0:0"),
                comment=issue["comment"],
            ))

        report.status = result["final_quality"]
        self.session.commit()

    def run_evaluation(self, source: Optional[str] = None, reevaluate: bool = False) -> int:
        """Evaluate reports, returning the number processed.

        By default only un-evaluated reports are processed; set ``reevaluate``
        to re-run QA on already-evaluated reports too. ``source`` filters by
        Report.source ('generated' | 'upload').
        """
        query = self.session.query(Report)
        if not reevaluate:
            query = query.outerjoin(QAResult).filter(QAResult.id == None)  # noqa: E711
        if source:
            query = query.filter(Report.source == source)
        reports = query.all()

        if not reports:
            print("No reports to evaluate.")
            return 0

        print(f"Evaluating {len(reports)} report(s)...")
        for report in reports:
            try:
                expected = report.status if report.source == "generated" else None
                result = self.engine.evaluate(report.report_text, expected_status=expected)
                self._persist(report, result)
                print(f"  {report.filename}: {result['final_quality']} "
                      f"({len(result['issues'])} issues)")
            except Exception as exc:  # one bad report shouldn't stop the batch
                self.session.rollback()
                print(f"  Error evaluating {report.filename}: {exc}")
        print("Evaluation complete.")
        return len(reports)

    def run_evaluation_on_uploads(self, reevaluate: bool = False) -> int:
        """Evaluate uploaded reports only."""
        return self.run_evaluation(source="upload", reevaluate=reevaluate)

    def close(self) -> None:
        self.session.close()


def main():
    if config.LLM_PROVIDER != "fake" and not (config.OPENAI_API_KEY or config.ANTHROPIC_API_KEY):
        print("No API key configured. Set one in .env, or use LLM_PROVIDER=fake for offline mode.")
        return
    evaluator = QAEvaluator()
    try:
        evaluator.run_evaluation_on_uploads()
    finally:
        evaluator.close()


if __name__ == "__main__":
    main()
