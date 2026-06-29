"""Shared pytest configuration.

Set environment before any project module imports config, so tests run fully
offline against a throwaway database.
"""

import os
import tempfile

os.environ.setdefault("LLM_PROVIDER", "fake")
# Fresh throwaway data dir per test run, so schema/state never leaks between runs.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="qa_pytest_"))
# Hermetic: ignore any real provider keys from a developer's .env so the offline
# suite is deterministic everywhere. (dotenv won't override these empty values.)
os.environ["OPENAI_API_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""

import pytest  # noqa: E402

import config  # noqa: E402
from db_setup import get_session, seed_rag_examples  # noqa: E402


@pytest.fixture
def session():
    config.ensure_dirs()
    seed_rag_examples()
    db = get_session()
    yield db
    db.close()


@pytest.fixture
def fresh_session(tmp_path):
    """A seeded database isolated per test (so embeddings don't bleed across tests)."""
    url = f"sqlite:///{tmp_path}/rag.db"
    seed_rag_examples(url)
    db = get_session(url)
    yield db
    db.close()
