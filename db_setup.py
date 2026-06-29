"""
Database layer.

SQLAlchemy models plus session helpers backed by a single cached engine per
database URL (creating a new engine on every call defeats connection pooling).
SQLite lives at the path configured in ``config.py``.
"""

from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy import (
    create_engine, inspect, text, Column, Integer, String, Text,
    DateTime, ForeignKey
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

import config

# --- Base ---
Base = declarative_base()


def _utcnow() -> datetime:
    """Timezone-aware UTC now (``datetime.utcnow`` is deprecated in 3.12)."""
    return datetime.now(timezone.utc)


# --- Models ---
class Report(Base):
    """Stores generated or uploaded inspection reports."""
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True)
    filename = Column(String(255), unique=True, nullable=False)
    topic = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False)   # clean, minor_error, major_error, pending
    source = Column(String(50), nullable=False, default="generated")  # generated | upload
    report_text = Column(Text, nullable=False)
    generator_version = Column(String(50))
    model = Column(String(100))
    created_at = Column(DateTime, default=_utcnow)

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
    evaluated_at = Column(DateTime, default=_utcnow)

    # Relationships
    report = relationship("Report", back_populates="qa_results")
    issues = relationship(
        "QAIssue", back_populates="qa_result", cascade="all, delete-orphan"
    )
    review = relationship(
        "Review", back_populates="qa_result", uselist=False,
        cascade="all, delete-orphan"
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
    created_at = Column(DateTime, default=_utcnow)

    # Relationship
    qa_result = relationship("QAResult", back_populates="issues")

    def __repr__(self):
        return f"<QAIssue(id={self.id}, type='{self.issue_type}')>"


class Review(Base):
    """A human reviewer's decision on a QA result (the ground-truth signal)."""
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True)
    qa_result_id = Column(Integer, ForeignKey("qa_results.id"), unique=True, nullable=False)
    decision = Column(String(20), nullable=False)          # accepted | overridden
    corrected_quality = Column(String(50), nullable=False)  # the human's true label
    note = Column(Text)                                     # optional rationale
    # Set once an override has been distilled into a RAGExample by the curator.
    rag_example_id = Column(Integer, ForeignKey("rag_examples.id"))
    created_at = Column(DateTime, default=_utcnow)

    qa_result = relationship("QAResult", back_populates="review")

    def __repr__(self):
        return f"<Review(id={self.id}, decision='{self.decision}', quality='{self.corrected_quality}')>"


class RAGExample(Base):
    """Stores retrieval examples used by the RAG QA prompt."""
    __tablename__ = "rag_examples"

    id = Column(Integer, primary_key=True)
    title = Column(String(255), unique=True, nullable=False)
    topic = Column(String(255), nullable=False)
    quality_label = Column(String(50), nullable=False)  # clean, minor_error, major_error
    report_excerpt = Column(Text, nullable=False)
    guidance = Column(Text, nullable=False)
    source = Column(String(50), default="seed")  # seed | curation
    embedding = Column(Text)  # JSON-encoded vector, backfilled lazily by the RAG layer
    created_at = Column(DateTime, default=_utcnow)

    def __repr__(self):
        return f"<RAGExample(id={self.id}, title='{self.title}', quality='{self.quality_label}')>"


# --- Engine / session helpers ---
# Columns added after the initial schema. SQLite's create_all won't ALTER an
# existing table, so we add them by hand for databases created before they
# existed (the data/ dir is throwaway, but this avoids a hard crash on upgrade).
_ADDED_COLUMNS = {
    "reports": {"source": "VARCHAR(50) DEFAULT 'generated'"},
    "rag_examples": {"embedding": "TEXT", "source": "VARCHAR(50) DEFAULT 'seed'"},
    "reviews": {"rag_example_id": "INTEGER"},
}


def _apply_migrations(engine) -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for table, columns in _ADDED_COLUMNS.items():
        if table not in tables:
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in columns.items():
            if name not in existing:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


@lru_cache(maxsize=None)
def get_engine(db_url: str = config.DB_URL):
    """Return a cached engine for ``db_url``, creating/upgrading tables on first use."""
    config.ensure_dirs()
    engine = create_engine(db_url, echo=False)
    Base.metadata.create_all(engine)
    _apply_migrations(engine)
    return engine


def get_session(db_url: str = config.DB_URL) -> Session:
    """Return a new session bound to the cached engine for ``db_url``."""
    return sessionmaker(bind=get_engine(db_url))()


def init_db(db_url: str = config.DB_URL) -> Session:
    """Ensure the schema exists and return a fresh session."""
    engine = get_engine(db_url)
    print(f"Database ready at {engine.url}")
    return get_session(db_url)


def seed_rag_examples(db_url: str = config.DB_URL) -> int:
    """Insert deterministic RAG examples for local testing if absent."""
    examples = [
        {
            "title": "Major - Missing sections and vague language",
            "topic": "fukt i kjeller",
            "quality_label": "major_error",
            "report_excerpt": (
                "### Sammendrag\n"
                "Kjelleren er litt fuktig og det er kanskje lekkasje.\n\n"
                "### Observasjoner\n"
                "- Veggen er våt.\n"
                "- Noe lukt."
            ),
            "guidance": (
                "Flag as major when required sections are missing and vague words are used. "
                "Recommend complete section structure and measurable findings with units."
            ),
        },
        {
            "title": "Clean - Structured with measurements",
            "topic": "vannskade på bad",
            "quality_label": "clean",
            "report_excerpt": (
                "### Sammendrag\n"
                "Fuktmåling på bad viser 18 % i vegg ved sluk. Begrenset skadeomfang.\n\n"
                "### Observasjoner\n"
                "- Fukt 18 % i nedre veggpanel\n"
                "- Misfarging over 0.8 m²\n\n"
                "### Årsak\n"
                "Lekkasjepunkt ved overgang mellom membran og sluk."
            ),
            "guidance": (
                "Treat reports as clean when required sections are complete, ordered, technical, "
                "and include concrete measurements and cost/time recommendations."
            ),
        },
    ]

    session = get_session(db_url)
    inserted = 0
    try:
        for example in examples:
            exists = session.query(RAGExample).filter_by(title=example["title"]).first()
            if exists:
                continue
            session.add(RAGExample(**example))
            inserted += 1
        session.commit()
    finally:
        session.close()
    return inserted


# Reference content grounded in the public Forskrift til avhendingslova (tryggere
# bolighandel), FOR-2021-06-08-1850, kap. 2 (Minstekrav til tilstandsrapporter,
# §§ 2-1–2-23). Section text is paraphrased/abbreviated from Lovdata and should be
# verified against the source for production use. NS 3600/NS 3424 are copyrighted and
# only referenced, not reproduced.
_REGULATION_SOURCE = "Forskrift til avhendingslova FOR-2021-06-08-1850 (lovdata.no)"
_REGULATION_REFERENCES = [
    {
        "title": "Tilstandsgrader TG0–TG3 (NS 3424 / NS 3600)",
        "topic": "tilstandsgrad",
        "quality_label": "reference",
        "report_excerpt": (
            "§ 2-23 Fastsetting av tilstandsgrad: TG0 gis når bygningsdelen ikke har "
            "noen avvik og er tilnærmet ny. TG1: mindre/moderate avvik (normal slitasje). "
            "TG2: vesentlige avvik. TG3: store eller alvorlige avvik. TGiU: ikke undersøkt "
            "eller ikke tilgjengelig."
        ),
        "guidance": (
            "Map severity to condition grade: TG0/TG1 ≈ clean, TG2 ≈ minor/major "
            "(substantial, tiltak på sikt), TG3 ≈ major (alvorlig, strakstiltak). A finding "
            f"graded serious but without urgency, cause or cost is inconsistent. Kilde: {_REGULATION_SOURCE} § 2-23."
        ),
    },
    {
        "title": "Minstekrav til tilstandsrapport (forskrift til avhendingslova kap. 2)",
        "topic": "minstekrav",
        "quality_label": "reference",
        "report_excerpt": (
            "§ 2-22 Resultatet av undersøkelsene: Det skal fastsettes en tilstandsgrad for "
            "det enkelte rommet eller den enkelte bygningsdelen. Årsak og konsekvens av "
            "avvik skal beskrives, og for bygningsdeler med laveste tilstandsgrad (TG3) "
            "skal det gis et anslag på utbedringskostnader."
        ),
        "guidance": (
            "Flag a major issue when a serious deviation (TG2/TG3) is reported without "
            "cause (årsak), consequence (konsekvens), recommended measure or a cost "
            f"estimate. Kilde: {_REGULATION_SOURCE} § 2-22."
        ),
    },
    {
        "title": "Kostnadsoverslag og konsistens",
        "topic": "kostnadsestimat",
        "quality_label": "reference",
        "report_excerpt": (
            "§ 2-22 krever anslag på utbedringskostnader for bygningsdeler med tilstandsgrad "
            "TG3. Anbefalte tiltak skal stå i forhold til alvorlighetsgrad."
        ),
        "guidance": (
            "Expect a concrete cost figure (kr) for TG3 findings; recommendations should "
            f"match severity: strakstiltak for TG3, planlagt vedlikehold for TG2. Kilde: {_REGULATION_SOURCE} § 2-22."
        ),
    },
    {
        "title": "Krav til undersøkte rom og bygningsdeler (forskrift kap. 2)",
        "topic": "undersøkelse",
        "quality_label": "reference",
        "report_excerpt": (
            "Kap. 2 krever undersøkelse av bl.a.: våtrom (§ 2-2), kjøkken (§ 2-3), vann- og "
            "avløpsrør (§ 2-4), varmtvannsbereder (§ 2-5), ventilasjon (§ 2-8), tak­"
            "konstruksjon og skorstein (§ 2-9), loft (§ 2-10), yttervegger (§ 2-11), vinduer "
            "og ytterdører (§ 2-12), balkonger (§ 2-13), krypkjeller (§ 2-14), rom under "
            "terreng (§ 2-15), byggegrunn og drenering (§ 2-16), terrengforhold (§ 2-17), "
            "elektrisk anlegg (§ 2-18) og oppmåling av areal BRA etter NS 3940 (§ 2-20)."
        ),
        "guidance": (
            "A thorough report covers the relevant building parts above, each with a "
            "condition grade. Flag major gaps when key wet-room, roof, basement or drainage "
            f"assessments are absent. Kilde: {_REGULATION_SOURCE} kap. 2."
        ),
    },
]


def seed_regulations(db_url: str = config.DB_URL) -> int:
    """Upsert public Norwegian regulation references into the RAG store.

    Reference entries (source='regulation') the judge and agent retrieve to ground
    evaluations in the real standards. Refreshes content in place (re-embedding on
    change) and is idempotent: re-running with unchanged content returns 0.
    """
    session = get_session(db_url)
    fields = ("topic", "quality_label", "report_excerpt", "guidance")
    changed = 0
    try:
        for ref in _REGULATION_REFERENCES:
            existing = session.query(RAGExample).filter_by(title=ref["title"]).first()
            if existing is None:
                session.add(RAGExample(source="regulation", **ref))
                changed += 1
            elif existing.source != "regulation" or any(getattr(existing, f) != ref[f] for f in fields):
                for f in fields:
                    setattr(existing, f, ref[f])
                existing.source = "regulation"
                existing.embedding = None  # content changed → re-embed lazily
                changed += 1
        session.commit()
    finally:
        session.close()
    return changed


if __name__ == "__main__":
    session = init_db()
    try:
        report_count = session.query(Report).count()
        print(f"Current reports: {report_count}")
    except Exception as e:
        print(f"No data yet: {e}")
    finally:
        session.close()
