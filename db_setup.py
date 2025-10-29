"""
Database Setup Module
SQLAlchemy models and database initialization for the inspection report system.
"""

from datetime import datetime
from pathlib import Path
from sqlalchemy import (
    create_engine, Column, Integer, String, Text,
    DateTime, ForeignKey
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

# --- Base ---
Base = declarative_base()


# --- Models ---
class Report(Base):
    """Stores generated inspection reports."""
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True)
    filename = Column(String(255), unique=True, nullable=False)
    topic = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False)   # clean, minor_error, major_error
    report_text = Column(Text, nullable=False)
    generator_version = Column(String(50))
    model = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    qa_results = relationship(
        "QAResult", back_populates="report", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Report(id={self.id}, topic='{self.topic}', status='{self.status}')>"


class QAResult(Base):
    """Stores QA evaluation results for reports."""
    __tablename__ = "qa_results"

    id = Column(Integer, primary_key=True)
    report_id = Column(Integer, ForeignKey("reports.id"), nullable=False)
    rule_quality = Column(String(50))
    llm_quality = Column(String(50))
    final_quality = Column(String(50))
    expected_status = Column(String(50))
    evaluated_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    report = relationship("Report", back_populates="qa_results")
    issues = relationship(
        "QAIssue", back_populates="qa_result", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<QAResult(id={self.id}, final_quality='{self.final_quality}')>"


class QAIssue(Base):
    """Stores individual QA issues found in reports."""
    __tablename__ = "qa_issues"

    id = Column(Integer, primary_key=True)
    qa_result_id = Column(Integer, ForeignKey("qa_results.id"), nullable=False)
    issue_type = Column(String(50), nullable=False)  # minor or major
    span = Column(String(100))                       # e.g., "45:67"
    comment = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship
    qa_result = relationship("QAResult", back_populates="issues")

    def __repr__(self):
        return f"<QAIssue(id={self.id}, type='{self.issue_type}')>"


# --- Database init / session helpers ---
def init_db(db_path: str = "data/reports.db") -> Session:
    """Initialize the database and return a session."""
    Path("data").mkdir(exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    print(f"Database initialized at {db_path}")
    return SessionLocal()


def get_session(db_path: str = "data/reports.db") -> Session:
    """Return a new database session."""
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


if __name__ == "__main__":
    session = init_db()
    try:
        report_count = session.query(Report).count()
        print(f"Current reports: {report_count}")
    except Exception as e:
        print(f"No data yet: {e}")
    finally:
        session.close()
