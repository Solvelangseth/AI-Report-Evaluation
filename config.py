"""
Central configuration.

Single place for paths, database location, LLM provider/model, scoring
thresholds, and Flask settings. Everything is overridable via environment
variables (loaded from a local .env if present) so nothing important is
hard-coded across the codebase.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# --- Filesystem layout ---
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DB_PATH = DATA_DIR / "reports.db"
DB_URL = f"sqlite:///{DB_PATH}"
UPLOAD_DIR = DATA_DIR / "uploads"

# --- LLM provider ---
# One of: "openai", "anthropic", "fake".
# "fake" runs the full pipeline offline (rule-based QA only, no API key needed).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").strip().lower()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
# Claude default is the most capable Opus model. For a high-volume classification
# judge, claude-haiku-4-5 or claude-sonnet-4-6 are cheaper; override via env.
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")

# Model used for synthetic report generation (always OpenAI for now).
GENERATION_MODEL = os.getenv("GENERATION_MODEL", OPENAI_MODEL)
GENERATOR_VERSION = "v2.0"

# --- Scoring thresholds (single source of truth) ---
# More than this many minor issues escalates a report to "minor_error".
MINOR_ISSUE_THRESHOLD = int(os.getenv("MINOR_ISSUE_THRESHOLD", "2"))

# --- RAG ---
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "2"))
# Retrieval strategy: "auto" uses embeddings when an embedder is available and
# falls back to lexical overlap; "semantic"/"lexical" force one or the other.
RAG_MODE = os.getenv("RAG_MODE", "auto").strip().lower()

# Embedding provider for semantic retrieval. Defaults to OpenAI when a key is
# present, otherwise a deterministic offline embedder (no network, hashed
# bag-of-words) so the semantic path still runs in tests/demos.
EMBEDDING_PROVIDER = (
    os.getenv("EMBEDDING_PROVIDER")
    or ("openai" if OPENAI_API_KEY else "fake")
).strip().lower()
OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "256"))  # used by the fake embedder

# --- Flask / uploads ---
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = _flag("FLASK_DEBUG", True)
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB
ALLOWED_EXTENSIONS = {"txt", "pdf", "docx", "json"}

# Report quality labels, ordered from best to worst.
QUALITY_LEVELS = ("clean", "minor_error", "major_error")

# Triage: verdicts in this set are auto-cleared when confidence is high (no human
# review needed). Everything else is routed to a reviewer.
AUTO_CLEAR_QUALITIES = {
    s.strip() for s in os.getenv("AUTO_CLEAR_QUALITIES", "clean").split(",") if s.strip()
}


def ensure_dirs() -> None:
    """Create the data/upload directories if they do not yet exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
