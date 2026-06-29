# Report QA System

Flask application for quality assurance of Norwegian building inspection reports.
It combines three signals:
- deterministic **rule-based** checks
- an **LLM judge** (provider-agnostic, schema-validated)
- a lightweight **RAG** retrieval step over in-database QA examples

Each report is classified `clean`, `minor_error`, or `major_error`, with per-issue
spans highlighted in the report text.

## What it does
- Upload `.txt`, `.pdf`, `.docx`, or `.json` reports
- Evaluate report quality with rules + LLM, taking the most severe verdict
- Highlight detected issues inline in the rendered Markdown
- Store reports, QA results, and issues in SQLite (the single source of truth)
- Generate synthetic reports for testing

## Architecture

| Module | Responsibility |
|--------|----------------|
| `config.py` | All settings (paths, DB URL, provider/model, thresholds), env-overridable |
| `qa_rules.py` | `QABaseline` — the single source of truth for QA standards |
| `scoring.py` | Turns issues into a verdict (one place for the thresholds) |
| `qa_engine.py` | Pure evaluation: rules + judge + RAG → verdict. No DB, no I/O |
| `judge.py` | Provider-agnostic LLM judge (`openai`/`anthropic`/`agent`/`fake`), Pydantic-validated |
| `agent_tools.py` | Tools for the `agent` judge (RAG search, rule lookup, re-check, span lookup) |
| `qa_master.py` | Persistence + run loop (wires the engine to the database) |
| `rag_pipeline.py` | Semantic (embedding cosine) retrieval, lexical fallback |
| `embeddings.py` | Provider-agnostic embedder (`openai` / `fake` offline) |
| `db_setup.py` | SQLAlchemy models + cached engine/session helpers |
| `generate_reports.py` | Synthetic report generation (OpenAI) |
| `reviews.py` | Human accept/override workflow; ground-truth + accuracy |
| `curation.py` | RAG-curation agent — turns overrides into new retrieval examples |
| `app.py` | Flask web layer (upload, dashboard, evaluation, review, highlighting) |

## Quick start

```bash
pip install -r requirements.txt
```

Add `.env`:

```bash
OPENAI_API_KEY=your_key_here       # required for the LLM judge + generation
FLASK_SECRET_KEY=optional_dev_secret
# LLM_PROVIDER=openai               # openai (default) | anthropic | agent | fake
# ANTHROPIC_API_KEY=...             # required for anthropic / agent providers
# RAG_MODE=auto                     # auto (default) | semantic | lexical
# EMBEDDING_PROVIDER=openai         # openai when a key is present, else fake (offline)
```

### Retrieval (RAG)

Retrieval is **semantic** (embedding cosine similarity) when an embedder is
available, and falls back to **lexical** (token overlap) otherwise. Example
embeddings are computed once and cached in the database. With no API key the
`fake` embedder keeps the semantic path running offline (deterministic hashed
bag-of-words) — lower quality than real embeddings, but fully functional.

Run the web app:

```bash
python app.py
```

Run the pipeline (generate → QA → stats):

```bash
python main.py --generate 5
python main.py --skip-generation   # QA existing reports only
python main.py --stats-only
python main.py --clean             # wipe the DB + uploads
```

## Offline / no-API-key mode

Set `LLM_PROVIDER=fake` to run the whole pipeline without any API key. The LLM
judge contributes nothing and rule-based QA still applies — useful for local
testing and demos:

```bash
LLM_PROVIDER=fake python main.py --skip-generation
```

## Reviewer workflow

On a report's detail page a human can **accept** the QA verdict or **override** it
with the correct label (plus an optional note). That decision is stored as
**ground truth**: it drives the accuracy metric (preferred over synthetic labels)
and the human/model agreement rate, and overrides are the signal a future
RAG-curation agent will learn from. Reviews are shown on the reports list and
recorded via `POST /report/<id>/review`.

## Learning from corrections (the flywheel)

Each reviewer **override** can be distilled into a new retrieval example by the
curation agent: it writes a reusable `RAGExample` (title/topic/excerpt/guidance)
labelled with the human's corrected verdict, so future similar reports retrieve
the lesson and are judged better. New examples are embedded automatically.

```bash
python main.py --curate           # batch: distill all un-curated overrides
```

Or per report via the "Add correction to knowledge base" button on the detail
page (`POST /curate`). Runs offline with `LLM_PROVIDER=fake`.

## Agentic review (`LLM_PROVIDER=agent`)

Instead of a single-shot classification, the `agent` provider runs a Claude
tool-use loop that *investigates* before ruling. It can look up the standards,
re-run the rule checks, retrieve similar reference reports, and verify the exact
character span of every issue — then submits a structured verdict. It slots in
behind the same `Judge` protocol, so nothing else changes.

```bash
LLM_PROVIDER=agent
ANTHROPIC_API_KEY=your_key_here
```

## Using Claude instead of OpenAI

The judge is provider-agnostic. To use Claude:

```bash
pip install anthropic
# in .env:
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_key_here
# ANTHROPIC_MODEL=claude-opus-4-8   # default; claude-haiku-4-5 / claude-sonnet-4-6 are cheaper
```

## Tests

```bash
pytest
```

The suite runs fully offline (uses `LLM_PROVIDER=fake`) and covers scoring,
rules, section extraction, RAG ranking, issue highlighting, the QA engine, and
the Flask routes.
