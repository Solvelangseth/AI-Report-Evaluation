"""
Main Pipeline Runner
Orchestrates the full workflow: generation → QA → database storage
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure we can import our modules
sys.path.insert(0, str(Path(__file__).parent))

from generate_reports import ReportGenerator
from qa_master import QAEvaluator
from db_setup import init_db, get_session, Report, QAResult

def check_environment():
    """Check that environment is properly configured."""
    load_dotenv()
    
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ Error: OPENAI_API_KEY not found in .env file")
        print("Please create a .env file with:")
        print("OPENAI_API_KEY=your_api_key_here")
        return False
    
    print("✅ Environment configured")
    return True

def run_pipeline(generate_count: int = 5, skip_generation: bool = False):
    """Run the complete pipeline."""
    
    print("=" * 60)
    print("INSPECTION REPORT QA PIPELINE")
    print("=" * 60)
    
    # Check environment
    if not check_environment():
        return
    
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
    evaluator = QAEvaluator()
    evaluator.run_evaluation()
    
    # Step 3: Display database statistics
    print("\n📊 STEP 3: Database Statistics")
    print("-" * 40)
    display_statistics()
    
    print("\n✅ Pipeline complete!")

def display_statistics():
    """Display statistics from the database."""
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
        
        # Accuracy (where final_quality matches expected_status)
        if total_qa > 0:
            matches = session.query(QAResult).filter(
                QAResult.final_quality == QAResult.expected_status
            ).count()
            accuracy = (matches / total_qa) * 100
            print(f"\nQA Accuracy (matches expected): {accuracy:.1f}%")
        
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
    """Clean all generated data (useful for testing)."""
    print("\n🧹 Cleaning all data...")
    
    # Remove JSON files
    for dir_path in ["data/reports", "data/qa_results/combined", 
                     "data/qa_results/llm", "data/qa_results/rules"]:
        dir_obj = Path(dir_path)
        if dir_obj.exists():
            for file in dir_obj.glob("*.json"):
                file.unlink()
            print(f"  Cleaned {dir_path}")
    
    # Remove evaluation tracker
    eval_file = Path("data/qa_master_evaluated.json")
    if eval_file.exists():
        eval_file.unlink()
        print("  Cleaned evaluation tracker")
    
    # Remove database
    db_file = Path("data/reports.db")
    if db_file.exists():
        db_file.unlink()
        print("  Removed database")
    
    print("✅ Data cleaned")

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
    
    args = parser.parse_args()
    
    if args.stats_only:
        print("\n📊 Database Statistics")
        print("=" * 60)
        display_statistics()
    elif args.clean:
        clean_data()
    else:
        if args.clean:
            clean_data()
        run_pipeline(
            generate_count=args.generate,
            skip_generation=args.skip_generation
        )

if __name__ == "__main__":
    main()