"""
Main Pipeline Runner
Orchestrates the full workflow: generation → QA → database storage
"""

import sys
from pathlib import Path

# Ensure we can import our modules
sys.path.insert(0, str(Path(__file__).parent))

import config
import reviews
from generate_reports import ReportGenerator
from qa_master import QAEvaluator
from judge import JudgeError
from db_setup import init_db, get_session, Report, QAResult, seed_rag_examples

def check_environment(require_generation: bool) -> bool:
    """Check that the environment is configured for the requested work."""
    if require_generation and not config.OPENAI_API_KEY:
        print("❌ Error: report generation requires OPENAI_API_KEY in .env")
        print("   Add the key, or use --skip-generation with LLM_PROVIDER=fake.")
        return False

    print(f"✅ Environment configured (LLM provider: {config.LLM_PROVIDER})")
    return True

def run_pipeline(generate_count: int = 5, skip_generation: bool = False):
    """Run the complete pipeline."""

    print("=" * 60)
    print("INSPECTION REPORT QA PIPELINE")
    print("=" * 60)

    # Check environment
    if not check_environment(require_generation=not skip_generation):
        return

    # Ensure schema exists and seed deterministic RAG examples for testing
    session = init_db()
    session.close()
    inserted = seed_rag_examples()
    print(f"📚 RAG example seed complete (new rows inserted: {inserted})")

    # Step 1: Generate reports
    if not skip_generation:
        print("\n📝 STEP 1: Generating synthetic reports...")
        print("-" * 40)
        generator = ReportGenerator()
        generator.batch_generate(count=generate_count)
    else:
        print("\n⏭️  Skipping report generation...")

    # Step 2: Run QA evaluation
    print("\n🔍 STEP 2: Running QA evaluation...")
    print("-" * 40)
    try:
        evaluator = QAEvaluator()
    except JudgeError as exc:
        print(f"❌ {exc}")
        return
    try:
        evaluator.run_evaluation()
    finally:
        evaluator.close()
    
    # Step 3: Display database statistics
    print("\n📊 STEP 3: Database Statistics")
    print("-" * 40)
    display_statistics()
    
    print("\n✅ Pipeline complete!")

def display_statistics():
    """Display statistics from the database."""
    session = init_db()
    session.close()
    session = get_session()
    
    try:
        # Total reports
        total_reports = session.query(Report).count()
        print(f"Total reports in database: {total_reports}")
        
        if total_reports == 0:
            print("No reports found in database")
            return
        
        # Reports by status
        print("\nReports by status:")
        for status in ["clean", "minor_error", "major_error"]:
            count = session.query(Report).filter(Report.status == status).count()
            print(f"  {status}: {count}")
        
        # QA Results
        total_qa = session.query(QAResult).count()
        print(f"\nTotal QA evaluations: {total_qa}")
        
        # Quality distribution
        print("\nFinal quality distribution:")
        for quality in ["clean", "minor_error", "major_error"]:
            count = session.query(QAResult).filter(QAResult.final_quality == quality).count()
            if total_qa > 0:
                percentage = (count / total_qa) * 100
                print(f"  {quality}: {count} ({percentage:.1f}%)")
        
        # Accuracy against ground truth (human review preferred over synthetic labels)
        acc = reviews.accuracy(session)
        if acc["total"] > 0:
            print(f"\nQA Accuracy (vs ground truth): {acc['accuracy']:.1f}% "
                  f"({acc['matches']}/{acc['total']})")
        else:
            print("\nQA Accuracy: N/A (no reports with ground-truth labels)")

        rstats = reviews.review_stats(session)
        print(f"\nHuman reviews: {rstats['reviewed']} "
              f"({rstats['overrides']} overrides, {rstats['agreement_rate']:.1f}% agreement)")

        tstats = reviews.triage_stats(session)
        print(f"Triage: {tstats['auto_cleared']} auto-cleared, "
              f"{tstats['needs_review']} need review, {tstats['reviewed']} reviewed")
        
        # Top issues
        from sqlalchemy import func
        from db_setup import QAIssue
        
        issue_count = session.query(QAIssue).count()
        if issue_count > 0:
            print(f"\nTotal issues found: {issue_count}")
            
            # Issues by type
            issue_types = session.query(
                QAIssue.issue_type,
                func.count(QAIssue.id).label('count')
            ).group_by(QAIssue.issue_type).all()
            
            print("Issues by severity:")
            for issue_type, count in issue_types:
                print(f"  {issue_type}: {count}")
    
    finally:
        session.close()

def clean_data():
    """Remove the database and uploads (useful for testing)."""
    print("\n🧹 Cleaning all data...")

    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
        print(f"  Removed database ({config.DB_PATH})")

    if config.UPLOAD_DIR.exists():
        for file in config.UPLOAD_DIR.iterdir():
            if file.is_file():
                file.unlink()
        print(f"  Cleared uploads ({config.UPLOAD_DIR})")

    print("✅ Data cleaned")

def curate_data():
    """Distill reviewer overrides into RAG examples (the learning flywheel)."""
    import curation

    print("\n🎓 Curating reviewer corrections into the knowledge base...")
    try:
        curator = curation.get_curator()
    except curation.CurationError as exc:
        print(f"❌ {exc}")
        return
    session = get_session()
    try:
        count = curation.curate_pending(session, curator)
        print(f"✅ Added {count} correction(s) to the knowledge base")
    finally:
        session.close()

def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Run the inspection report QA pipeline")
    parser.add_argument(
        "--generate", 
        type=int, 
        default=5,
        help="Number of reports to generate (default: 5)"
    )
    parser.add_argument(
        "--skip-generation",
        action="store_true",
        help="Skip report generation and only run QA on existing reports"
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean all data before running"
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Only display statistics"
    )
    parser.add_argument(
        "--curate",
        action="store_true",
        help="Distill reviewer overrides into RAG examples, then exit"
    )

    args = parser.parse_args()

    if args.clean:
        clean_data()

    if args.curate:
        curate_data()
    elif args.stats_only:
        print("\n📊 Database Statistics")
        print("=" * 60)
        display_statistics()
    else:
        run_pipeline(
            generate_count=args.generate,
            skip_generation=args.skip_generation
        )

if __name__ == "__main__":
    main()
