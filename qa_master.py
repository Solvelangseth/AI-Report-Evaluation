"""
QA Master Module
Performs rule-based and LLM-based quality assurance on uploaded reports.
"""

import os
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Literal, Tuple
from dotenv import load_dotenv
from openai import OpenAI

from qa_rules import QABaseline
from db_setup import init_db, get_session, Report, QAResult, QAIssue

# Load environment variables
load_dotenv()

class QAEvaluator:
    """Master QA evaluation system for uploaded reports."""
    
    def __init__(self):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.qa_results_dir = Path("data/qa_results")
        self.combined_dir = self.qa_results_dir / "combined"
        self.llm_dir = self.qa_results_dir / "llm"
        self.rules_dir = self.qa_results_dir / "rules"
        
        # Create directories
        for dir_path in [self.combined_dir, self.llm_dir, self.rules_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize database session
        self.session = get_session()
    
    def _extract_sections(self, text: str) -> Dict[str, str]:
        """Extracts report sections based on Markdown-style headers."""
        sections = {}
        current_section = None
        current_content = []

        # Normalize required section names (lowercase)
        required = [r.lower() for r in QABaseline.REQUIRED_SECTIONS]

        for line in text.splitlines():
            line_stripped = line.strip()

            # Match Markdown-like section headers
            match = re.match(r"^#+\s*(.+)", line_stripped)
            if match:
                title = match.group(1).lower().strip(" :")
                # Try to align title with known sections
                for req in required:
                    if req in title:
                        if current_section:
                            sections[current_section] = "\n".join(current_content).strip()
                        current_section = req
                        current_content = []
                        break
                else:
                    # Not a recognized section, just continue collecting
                    if current_section:
                        current_content.append(line_stripped)
            elif current_section:
                current_content.append(line_stripped)

        if current_section:
            sections[current_section] = "\n".join(current_content).strip()

        return sections

    
    def rule_based_qa(self, report_text: str) -> Tuple[str, List[Dict]]:
        """Performs deterministic rule-based QA checks."""
        issues = []
        
        # Extract sections
        sections = self._extract_sections(report_text)
        
        # Check required sections
        missing_sections = []
        for required in QABaseline.REQUIRED_SECTIONS:
            if required not in sections:
                missing_sections.append(required)
                issues.append({
                    "type": "major",
                    "span": "0:0",
                    "comment": f"Missing required section: {required}"
                })
        
        # Check section order
        if not missing_sections:
            section_list = list(sections.keys())
            if not QABaseline.validate_section_order(section_list):
                issues.append({
                    "type": "major",
                    "span": "0:0",
                    "comment": "Sections are not in the correct order"
                })
        
        # Check forbidden words
        forbidden_found = QABaseline.check_forbidden_words(report_text)
        for forbidden in forbidden_found:
            issues.append({
                "type": "minor",
                "span": forbidden["span"],
                "comment": f"Forbidden word '{forbidden['word']}' found - use more precise language"
            })
        
        # Check quantification
        quant_issues = QABaseline.check_quantification(report_text)
        for quant in quant_issues:
            issues.append({
                "type": "minor",
                "span": quant.get("span", "0:0"),
                "comment": quant["suggestion"]
            })
        
        # Check section lengths
        for section_name, content in sections.items():
            length = len(content)
            rules = QABaseline.STRUCTURE_RULES
            
            if length < rules["min_section_length"]:
                issues.append({
                    "type": "minor",
                    "span": f"section:{section_name}",
                    "comment": f"Section '{section_name}' too short ({length} chars, min {rules['min_section_length']})"
                })
            elif length > rules["max_section_length"]:
                issues.append({
                    "type": "minor",
                    "span": f"section:{section_name}",
                    "comment": f"Section '{section_name}' too long ({length} chars, max {rules['max_section_length']})"
                })
        
        # Determine overall quality
        major_count = sum(1 for i in issues if i["type"] == "major")
        minor_count = sum(1 for i in issues if i["type"] == "minor")
        
        if major_count > 0:
            quality = "major_error"
        elif minor_count > 2:
            quality = "minor_error"
        else:
            quality = "clean"
        
        return quality, issues
    
    def llm_based_qa(self, report_text: str, baseline: Dict) -> Tuple[str, List[Dict]]:
        """Performs LLM-based QA evaluation."""
        
        prompt = f"""
        Evaluate this Norwegian building inspection report against quality standards.
        
        REPORT:
        {report_text}
        
        QUALITY STANDARDS:
        1. Required sections (in order): {', '.join(baseline['required_sections'])}
        2. Forbidden vague words: {', '.join(baseline['forbidden_words'])}
        3. Must use specific measurements with units (m², %, kr)
        4. Professional, technical tone required
        5. Logical consistency between severity and recommendations
        
        Identify issues in this format:
        - Type: "minor" or "major"
        - Text snippet: exact phrase with the issue
        - Comment: specific improvement suggestion
        
        Return as JSON list of issues. If no issues, return empty list.
        """
        
        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a quality assurance expert for building inspection reports."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            response_format={"type": "json_object"}
        )
        
        try:
            result = json.loads(response.choices[0].message.content)
            issues = result.get("issues", [])
            
            # Add span information for each issue
            for issue in issues:
                if "text_snippet" in issue:
                    snippet = issue["text_snippet"]
                    start = report_text.find(snippet)
                    if start != -1:
                        issue["span"] = f"{start}:{start + len(snippet)}"
                    else:
                        issue["span"] = "0:0"
                    del issue["text_snippet"]
            
            # Determine quality
            major_count = sum(1 for i in issues if i.get("type") == "major")
            minor_count = sum(1 for i in issues if i.get("type") == "minor")
            
            if major_count > 0:
                quality = "major_error"
            elif minor_count > 2:
                quality = "minor_error"
            else:
                quality = "clean"
            
            return quality, issues
            
        except Exception as e:
            print(f"Error parsing LLM response: {e}")
            return "unknown", []
    
    def evaluate_report(self, report: Report) -> Dict[str, Any]:
        """Evaluates a single uploaded report using both methods."""
        
        report_text = report.report_text
        baseline = QABaseline.get_baseline()
        
        # Run both QA methods
        rule_quality, rule_issues = self.rule_based_qa(report_text)
        llm_quality, llm_issues = self.llm_based_qa(report_text, baseline)
        
        # Merge issues (deduplicate similar ones)
        all_issues = rule_issues + llm_issues
        merged_issues = self._merge_issues(all_issues)
        
        # Determine final quality (most severe wins)
        if "major_error" in [rule_quality, llm_quality]:
            final_quality = "major_error"
        elif "minor_error" in [rule_quality, llm_quality]:
            final_quality = "minor_error"
        else:
            final_quality = "clean"
        
        # Create result
        result = {
            "file": report.filename,
            "report_id": report.id,
            "expected_status": report.status,  # For uploaded files, this is 'pending'
            "rule_quality": rule_quality,
            "llm_quality": llm_quality,
            "final_quality": final_quality,
            "issues": merged_issues,
            "evaluated_at": datetime.now().isoformat()
        }
        
        return result
    
    def _merge_issues(self, issues: List[Dict]) -> List[Dict]:
        """Merge and deduplicate issues from different sources."""
        seen = set()
        merged = []
        
        for issue in issues:
            # Create a simple key for deduplication
            key = (issue.get("span", ""), issue.get("comment", "")[:30])
            if key not in seen:
                seen.add(key)
                merged.append(issue)
        
        return merged
    
    def save_to_database(self, report: Report, qa_result: Dict) -> None:
        """Save QA results to database."""
        from sqlalchemy.exc import IntegrityError

        try:
            # Check if QA result already exists for this report
            existing_qa = (
                self.session.query(QAResult)
                .filter_by(report_id=report.id)
                .first()
            )

            if existing_qa:
                # Update existing QA result
                existing_qa.rule_quality = qa_result["rule_quality"]
                existing_qa.llm_quality = qa_result["llm_quality"]
                existing_qa.final_quality = qa_result["final_quality"]
                existing_qa.evaluated_at = datetime.utcnow()
                
                # Delete old issues
                self.session.query(QAIssue).filter_by(qa_result_id=existing_qa.id).delete()
                db_qa_result = existing_qa
            else:
                # Create new QAResult entry
                db_qa_result = QAResult(
                    report_id=report.id,
                    rule_quality=qa_result["rule_quality"],
                    llm_quality=qa_result["llm_quality"],
                    final_quality=qa_result["final_quality"],
                    expected_status=qa_result["expected_status"]
                )
                self.session.add(db_qa_result)
            
            self.session.flush()

            # Create QAIssue entries
            for issue in qa_result["issues"]:
                db_issue = QAIssue(
                    qa_result_id=db_qa_result.id,
                    issue_type=issue["type"],
                    span=issue.get("span", "0:0"),
                    comment=issue["comment"]
                )
                self.session.add(db_issue)
            
            # Update report status based on QA result
            report.status = qa_result["final_quality"]
            
            self.session.commit()

        except IntegrityError:
            self.session.rollback()
            print(f"Error saving QA result for report: {report.filename}")
        except Exception as e:
            self.session.rollback()
            print(f"Unexpected error: {e}")

    
    def run_evaluation_on_uploads(self) -> None:
        """Run QA evaluation on all unevaluated uploaded reports."""
        
        # Find all uploaded reports that haven't been evaluated
        unevaluated_reports = (
            self.session.query(Report)
            .outerjoin(QAResult)
            .filter(QAResult.id == None)
            .filter(Report.model == 'user_upload')
            .all()
        )
        
        if not unevaluated_reports:
            print("No unevaluated uploaded reports found")
            return
        
        print(f"Evaluating {len(unevaluated_reports)} uploaded reports...")
        
        for report in unevaluated_reports:
            try:
                print(f"Evaluating {report.filename}...")
                
                # Evaluate report
                result = self.evaluate_report(report)
                
                # Save to JSON (optional)
                result_file = self.combined_dir / f"qa_upload_{report.id}_{report.filename.split('.')[0]}.json"
                with open(result_file, 'w', encoding='utf-8') as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                
                # Save to database
                self.save_to_database(report, result)
                
                # Print summary
                print(f"  File: {report.filename}")
                print(f"  Final QA: {result['final_quality']}")
                print(f"  Issues found: {len(result['issues'])}")
                
            except Exception as e:
                print(f"Error evaluating {report.filename}: {e}")
                continue
        
        print(f"Evaluation complete. Results saved to database")


def main():
    """Main entry point for QA evaluation of uploaded files."""
    
    # Check for API key
    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY not found in .env file")
        return
    
    evaluator = QAEvaluator()
    evaluator.run_evaluation_on_uploads()


if __name__ == "__main__":
    main()