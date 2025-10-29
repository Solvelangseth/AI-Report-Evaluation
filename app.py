"""
Flask Web Application for Inspection Report QA System
"""

import os
import json
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, jsonify, request, redirect, url_for
from flask_cors import CORS
from sqlalchemy import create_engine, desc
from sqlalchemy.orm import sessionmaker
import re
import markdown
from markupsafe import Markup

# Import our modules
from db_setup import Report, QAResult, QAIssue, Base
from generate_reports import ReportGenerator
from qa_master import QAEvaluator

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = 'your-secret-key-here-change-in-production'

# Database setup
engine = create_engine('sqlite:///data/reports.db')
Base.metadata.bind = engine
DBSession = sessionmaker(bind=engine)

def get_db():
    """Get database session."""
    return DBSession()

@app.route('/')
def index():
    """Main dashboard page."""
    db = get_db()
    try:
        # Get statistics
        total_reports = db.query(Report).count()
        total_evaluated = db.query(QAResult).count()
        
        # Get quality distribution
        clean_count = db.query(QAResult).filter(QAResult.final_quality == 'clean').count()
        minor_count = db.query(QAResult).filter(QAResult.final_quality == 'minor_error').count()
        major_count = db.query(QAResult).filter(QAResult.final_quality == 'major_error').count()
        
        # Calculate accuracy
        if total_evaluated > 0:
            matches = db.query(QAResult).filter(
                QAResult.final_quality == QAResult.expected_status
            ).count()
            accuracy = round((matches / total_evaluated) * 100, 1)
        else:
            accuracy = 0
        
        stats = {
            'total_reports': total_reports,
            'total_evaluated': total_evaluated,
            'clean_count': clean_count,
            'minor_count': minor_count,
            'major_count': major_count,
            'accuracy': accuracy
        }
        
        return render_template('index.html', stats=stats)
    finally:
        db.close()

@app.route('/reports')
def reports_list():
    """List all reports with their QA status."""
    db = get_db()
    try:
        # Get all reports with their QA results
        reports = db.query(Report).order_by(desc(Report.created_at)).all()
        
        reports_data = []
        for report in reports:
            qa_result = db.query(QAResult).filter(QAResult.report_id == report.id).first()
            
            reports_data.append({
                'id': report.id,
                'filename': report.filename,
                'topic': report.topic,
                'status': report.status,
                'created_at': report.created_at.strftime('%Y-%m-%d %H:%M'),
                'qa_status': qa_result.final_quality if qa_result else 'pending',
                'issue_count': len(qa_result.issues) if qa_result else 0
            })
        
        return render_template('reports.html', reports=reports_data)
    finally:
        db.close()

@app.route('/report/<int:report_id>')
def report_detail(report_id):
    """Show detailed report with QA issues highlighted."""
    db = get_db()
    try:
        report = db.query(Report).filter(Report.id == report_id).first()
        if not report:
            return "Report not found", 404
        
        qa_result = db.query(QAResult).filter(QAResult.report_id == report_id).first()
        
        # Process issues for highlighting
        issues = []
        if qa_result:
            for issue in qa_result.issues:
                # Parse span (format: "start:end")
                span_parts = issue.span.split(':')
                if len(span_parts) == 2 and span_parts[0].isdigit():
                    start = int(span_parts[0])
                    end = int(span_parts[1])
                    issues.append({
                        'id': issue.id,
                        'type': issue.issue_type,
                        'start': start,
                        'end': end,
                        'comment': issue.comment,
                        'span': issue.span
                    })
                else:
                    # Handle special spans like "section:sammendrag"
                    issues.append({
                        'id': issue.id,
                        'type': issue.issue_type,
                        'start': -1,
                        'end': -1,
                        'comment': issue.comment,
                        'span': issue.span
                    })
        
        # Sort issues by position
        issues.sort(key=lambda x: x['start'])
        
        return render_template('report_detail.html', 
                             report=report, 
                             qa_result=qa_result, 
                             issues=issues)
    finally:
        db.close()

@app.route('/generate', methods=['POST'])
def generate_reports():
    """Generate new reports via AJAX."""
    try:
        count = int(request.json.get('count', 5))
        generator = ReportGenerator()
        generator.batch_generate(count=count)
        
        return jsonify({
            'success': True,
            'message': f'Successfully generated {count} reports'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

@app.route('/evaluate', methods=['POST'])
def evaluate_reports():
    """Run QA evaluation on unevaluated reports."""
    try:
        evaluator = QAEvaluator()
        evaluator.run_evaluation()
        
        return jsonify({
            'success': True,
            'message': 'QA evaluation completed successfully'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

@app.route('/api/report/<int:report_id>/issues')
def get_report_issues(report_id):
    """Get issues for a specific report (AJAX endpoint)."""
    db = get_db()
    try:
        qa_result = db.query(QAResult).filter(QAResult.report_id == report_id).first()
        
        if not qa_result:
            return jsonify({'issues': []})
        
        issues = []
        for issue in qa_result.issues:
            issues.append({
                'id': issue.id,
                'type': issue.issue_type,
                'span': issue.span,
                'comment': issue.comment
            })
        
        return jsonify({'issues': issues})
    finally:
        db.close()

@app.route('/api/stats')
def get_stats():
    """Get current statistics (AJAX endpoint)."""
    db = get_db()
    try:
        stats = {
            'total_reports': db.query(Report).count(),
            'total_evaluated': db.query(QAResult).count(),
            'clean': db.query(QAResult).filter(QAResult.final_quality == 'clean').count(),
            'minor_errors': db.query(QAResult).filter(QAResult.final_quality == 'minor_error').count(),
            'major_errors': db.query(QAResult).filter(QAResult.final_quality == 'major_error').count()
        }
        
        # Calculate accuracy
        if stats['total_evaluated'] > 0:
            matches = db.query(QAResult).filter(
                QAResult.final_quality == QAResult.expected_status
            ).count()
            stats['accuracy'] = round((matches / stats['total_evaluated']) * 100, 1)
        else:
            stats['accuracy'] = 0
        
        # Get recent issues
        recent_issues = db.query(QAIssue).order_by(desc(QAIssue.created_at)).limit(10).all()
        stats['recent_issues'] = [
            {
                'type': issue.issue_type,
                'comment': issue.comment[:100] + '...' if len(issue.comment) > 100 else issue.comment
            }
            for issue in recent_issues
        ]
        
        return jsonify(stats)
    finally:
        db.close()

@app.template_filter('highlight_issues')
def highlight_issues(text, issues):
    """Render report text as HTML with highlighted issue spans."""
    if not text:
        return ""

    # Apply highlights before Markdown conversion
    if issues:
        replacements = []
        for issue in issues:
            if issue["start"] >= 0 and issue["end"] > issue["start"]:
                snippet = text[issue["start"]:issue["end"]]
                highlighted = (
                    f'<span class="issue-highlight issue-{issue["type"]}" '
                    f'data-issue-id="{issue["id"]}" '
                    f'data-comment="{issue["comment"]}">{snippet}</span>'
                )
                replacements.append((issue["start"], issue["end"], highlighted))

        # Sort and apply in reverse order
        replacements.sort(key=lambda x: x[0], reverse=True)
        for start, end, replacement in replacements:
            text = text[:start] + replacement + text[end:]

    # Now convert Markdown to HTML — and keep inserted <span> tags intact
    html = markdown.markdown(
        text,
        extensions=["extra", "sane_lists"],
        output_format="html5",
        extension_configs={
        "markdown.extensions.extra": {"markdown_in_html": True}
        }
    )

    return Markup(html)  # Mark safe so Jinja doesn't escape <span>


if __name__ == '__main__':
    # Ensure data directory exists
    Path('data').mkdir(exist_ok=True)
    
    # Initialize database if needed
    Base.metadata.create_all(engine)
    
    # Run the app
    app.run(debug=True, port=5000)