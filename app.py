"""
Flask Web Application for Inspection Report QA System
Modified to support file uploads instead of generation
"""

import os
import json
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from flask_cors import CORS
from werkzeug.utils import secure_filename
from sqlalchemy import create_engine, desc
from sqlalchemy.orm import sessionmaker
import re
import markdown
from markupsafe import Markup

# Import our modules
from db_setup import Report, QAResult, QAIssue, Base
from qa_master import QAEvaluator

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = 'your-secret-key-here-change-in-production'
app.config['UPLOAD_FOLDER'] = 'data/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx', 'json'}

# Create upload folder if it doesn't exist
Path(app.config['UPLOAD_FOLDER']).mkdir(parents=True, exist_ok=True)

# Database setup
engine = create_engine('sqlite:///data/reports.db')
Base.metadata.bind = engine
DBSession = sessionmaker(bind=engine)

def get_db():
    """Get database session."""
    return DBSession()

def allowed_file(filename):
    """Check if file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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
        
        # Get pending reports (uploaded but not evaluated)
        pending_count = db.query(Report).outerjoin(QAResult).filter(QAResult.id == None).count()
        
        stats = {
            'total_reports': total_reports,
            'total_evaluated': total_evaluated,
            'clean_count': clean_count,
            'minor_count': minor_count,
            'major_count': major_count,
            'accuracy': accuracy,
            'pending_count': pending_count
        }
        
        return render_template('index.html', stats=stats)
    finally:
        db.close()

@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    """Handle file upload."""
    if request.method == 'POST':
        # Check if file is present
        if 'file' not in request.files:
            flash('No file selected')
            return redirect(request.url)
        
        file = request.files['file']
        
        # Check if file is selected
        if file.filename == '':
            flash('No file selected')
            return redirect(request.url)
        
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # Add timestamp to filename to avoid collisions
            base_name = filename.rsplit('.', 1)[0]
            extension = filename.rsplit('.', 1)[1]
            unique_filename = f"{base_name}_{timestamp}.{extension}"
            
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(filepath)
            
            # Extract text content based on file type
            try:
                report_text = extract_text_from_file(filepath, extension)
                topic = request.form.get('topic', base_name)
                
                # Save to database
                db = get_db()
                try:
                    report = Report(
                        filename=unique_filename,
                        topic=topic,
                        status='pending',  # Will be determined by QA
                        report_text=report_text,
                        generator_version='upload_v1',
                        model='user_upload',
                        created_at=datetime.utcnow()
                    )
                    db.add(report)
                    db.commit()
                    
                    return jsonify({
                        'success': True,
                        'message': f'File "{filename}" uploaded successfully',
                        'report_id': report.id
                    })
                finally:
                    db.close()
                    
            except Exception as e:
                return jsonify({
                    'success': False,
                    'message': f'Error processing file: {str(e)}'
                }), 400
        else:
            return jsonify({
                'success': False,
                'message': 'Invalid file type. Allowed types: txt, pdf, doc, docx, json'
            }), 400
    
    # GET request - show upload form
    return render_template('upload.html')

def extract_text_from_file(filepath, extension):
    """Extract text content from uploaded file."""
    if extension == 'txt':
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    elif extension == 'json':
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Try to extract report_text field or stringify the whole JSON
            if isinstance(data, dict) and 'report_text' in data:
                return data['report_text']
            else:
                return json.dumps(data, ensure_ascii=False, indent=2)
    elif extension == 'pdf':
        # You'll need to install PyPDF2 or pdfplumber for this
        try:
            import PyPDF2
            with open(filepath, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                text = ""
                for page in reader.pages:
                    text += page.extract_text() + "\n"
                return text
        except ImportError:
            return "PDF support requires PyPDF2 library. Please install it."
    elif extension in ['doc', 'docx']:
        # You'll need python-docx for this
        try:
            from docx import Document
            doc = Document(filepath)
            text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
            return text
        except ImportError:
            return "Word document support requires python-docx library. Please install it."
    else:
        return "Unsupported file type"

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
                'issue_count': len(qa_result.issues) if qa_result else 0,
                'source': 'Upload' if report.model == 'user_upload' else 'Generated'
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

@app.route('/evaluate', methods=['POST'])
def evaluate_reports():
    """Run QA evaluation on unevaluated reports."""
    try:
        evaluator = QAEvaluator()
        # Modified to evaluate uploaded files
        evaluator.run_evaluation_on_uploads()
        
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
            'major_errors': db.query(QAResult).filter(QAResult.final_quality == 'major_error').count(),
            'pending': db.query(Report).outerjoin(QAResult).filter(QAResult.id == None).count()
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

    # Now convert Markdown to HTML
    html = markdown.markdown(
        text,
        extensions=["extra", "sane_lists"],
        output_format="html5",
        extension_configs={
        "markdown.extensions.extra": {"markdown_in_html": True}
        }
    )

    return Markup(html)


if __name__ == '__main__':
    # Ensure data directory exists
    Path('data').mkdir(exist_ok=True)
    Path('data/uploads').mkdir(exist_ok=True)
    
    # Initialize database if needed
    Base.metadata.create_all(engine)
    
    # Run the app
    app.run(debug=True, port=5000)