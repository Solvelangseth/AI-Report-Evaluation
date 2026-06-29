# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A Flask app that runs QA on **Norwegian** building inspection reports. Each report
is scored `clean` / `minor_error` / `major_error` from three signals: deterministic
rules, an LLM judge, and a lexical RAG step. The most severe of the rule and LLM
verdicts wins.

Reports are Norwegian — section names (`sammendrag`, `observasjoner`, `årsak`,
`konsekvenser`, `anbefalinger`, `kostnadsestimat`), forbidden words, and prompts are
all in Norwegian. Keep that when editing `qa_rules.py` or any prompt.

## Commands

```bash
pip install -r requirements.txt
python app.py                          # web UI on http://localhost:5000
python main.py --generate 5            # pipeline: generate → QA → stats
python main.py --skip-generation       # QA existing reports only
python main.py --stats-only
python main.py --clean                 # wipe DB + uploads
pytest                                 # full test suite (runs offline)
pytest tests/test_engine.py::test_llm_snippet_resolves_to_span   # single test
```

**Offline mode:** set `LLM_PROVIDER=fake` to run everything with no API key — the
judge contributes nothing, rules still apply. The test suite relies on this.

## Architecture

The data flow is: report text → `qa_engine.QAEngine` (rules + judge + RAG) →
verdict → `qa_master` persists it. Modules are flat (no package).

- **`config.py`** — every setting (paths, `DB_URL`, `LLM_PROVIDER`/models, thresholds,
  Flask). Env-overridable. Import this rather than hard-coding values.
- **`qa_rules.py`** — `QABaseline`: the QA standards (required sections + order,
  forbidden words, quantification/structure rules). **Rules only emit *objective*
  issues** (missing/misordered sections, no-units-at-all, length) — vagueness/tone/
  consistency are deliberately left to the LLM, which judges them in context. The
  `forbidden_words` list is fed to the judge prompt as *guidance*, not auto-flagged
  (a hard blocklist false-positives on common words like "noe"). Grounded in the real
  Norwegian framework (NS 3600 / forskrift til avhendingslova / tilstandsgrad TG0–TG3).
- **Regulation references** live in the RAG via `db_setup.seed_regulations()`
  (`RAGExample.source='regulation'`, `quality_label='reference'`) — public forskrift +
  TG definitions the judge/agent retrieve to ground evaluations. NS standards are
  copyrighted, so they're referenced, not reproduced.
- **`curation.py`** — the RAG-curation agent (the feedback flywheel). Distills a reviewer
  **override** into a new `RAGExample` so retrieval improves from real corrections.
  Provider-agnostic curator (`openai`/`anthropic`/`fake`) with Pydantic-validated structured
  output; the new example's `quality_label` is the human's `corrected_quality` (ground truth),
  never the LLM's. `curate_review` (one, idempotent) / `curate_pending` (batch); the new
  example is linked back via `Review.rag_example_id` and tagged `RAGExample.source="curation"`,
  then embedded lazily by the RAG layer. Triggered by `POST /curate` or `python main.py --curate`.
- **`authoring.py`** / **`prices.py`** — the report **authoring** pipeline (a drafting
  assistant for the certified inspector, not an autonomous issuer). `AuthoringPipeline`:
  structured `Finding`s → `CostAnalyst` (grounded in `prices.py`'s curated, *indicative*
  unit-price reference) → `Composer` (assembles the six sections per the standard) →
  `QAEngine` evaluation → revise loop. Agents are provider-agnostic with `fake` offline
  impls; the eval stage reuses the judge. Cost figures are legally relevant — labelled
  indicative and must be verified (`prices.DISCLAIMER`). Not yet wired to web/CLI.
- **`eval_harness.py`** — labelled-report calibration harness (run against a real
  provider) guarding the judge against over/under-flagging regressions.
- **`reviews.py`** — the human reviewer workflow **and triage**. `triage(qa_result)` →
  `reviewed` | `auto_cleared` (high-confidence verdict in `config.AUTO_CLEAR_QUALITIES`,
  default just `clean`) | `needs_review`; `triage_stats()` for the workload picture.
  `record_review` (accept/override,
  upserted per `QAResult`), and the **single home for ground truth + accuracy**:
  `ground_truth(qa_result)` prefers a human `Review.corrected_quality` over the synthetic
  `expected_status`; `accuracy()` and `review_stats()` (agreement rate) are used by both
  `app.py` and `main.py`. Overrides are the training signal for the future curation agent.
- **`scoring.py`** — `classify_issues()`, `worst_quality()`, and `confidence()`
  (high/medium/low from rule-vs-LLM agreement — a free, deterministic uncertainty
  signal). The *only* place the thresholds live (any major → `major_error`; > `MINOR_ISSUE_THRESHOLD` minors →
  `minor_error`). `worst_quality` ignores unknown labels so a judge failure can never
  lower a verdict.
- **`qa_engine.py`** — pure: text in, verdict out. No DB, no I/O. Holds the robust
  section extractor (`extract_sections`, handles `#`, `**bold**`, and plain headers)
  and `rule_based_issues`. Testable with a stub/fake judge.
- **`judge.py`** / **`agent_tools.py`** — provider-agnostic LLM judge behind a `Judge`
  protocol (`evaluate(report_text, prompt)`): `OpenAIJudge`, `AnthropicJudge` (Claude,
  structured outputs), `FakeJudge` (offline), and `AgentJudge` (Claude tool-use loop).
  The agent investigates before ruling via the `ReviewToolbox` tools in `agent_tools.py`
  (`search_similar_reports`→RAG, `get_rule`→QABaseline, `recheck_rules`→`rule_based_issues`,
  `quote_span`→exact offsets) and submits its verdict through the terminal `report_issues`
  tool. Output is validated through a Pydantic `LLMVerdict`; invalid output raises
  `JudgeError` (caught by the engine → `llm_quality="error"`, which `worst_quality`
  ignores). `get_judge(provider)` is the factory (`openai`/`anthropic`/`agent`/`fake`);
  `AgentJudge` accepts an injected `client` for offline testing. Note `judge.py` must not
  import `agent_tools` at module top (cycle: agent_tools→qa_engine→judge) — `AgentJudge`
  imports it lazily.
- **`qa_master.py`** — `QAEvaluator`: wires the engine to the DB and loops over
  reports. `run_evaluation(source=None, reevaluate=False)` — `reevaluate=True` re-runs
  QA on already-evaluated reports (the re-eval path is reachable, unlike before).
- **`rag_pipeline.py`** / **`embeddings.py`** — `RAGPipeline` does **semantic** retrieval
  (embedding cosine) when an embedder is available, falling back to **lexical Jaccard
  overlap** otherwise (`RAG_MODE=auto|semantic|lexical`). Example embeddings are
  backfilled lazily and cached in `RAGExample.embedding`. Embedders are provider-agnostic
  (`embeddings.get_embedder`: `openai` / `fake` offline), and `get_embedder` returns None
  rather than raising so retrieval degrades to lexical silently.
- **`db_setup.py`** — SQLAlchemy models over SQLite. One **cached** engine per URL
  (`get_engine` is `lru_cache`'d). UTC timestamps via `datetime.now(timezone.utc)`.
- **`app.py`** — Flask web layer. The `highlight_issues` filter is the tricky part
  (see below).

## Conventions that matter

- **DB is the single source of truth.** There are no JSON side-files — generation and
  QA write only to SQLite. Don't reintroduce `data/reports/` or `data/qa_results/`.
- **`Report.source`** (`'generated'` | `'upload'`) distinguishes origin — not the
  `model` column. `run_evaluation_on_uploads()` filters on `source='upload'`.
- **Issue `span`** is a string: usually `"start:end"` char offsets into `report_text`,
  but rule checks emit `"section:<name>"` or `"0:0"` when there's no precise location.
  Code consuming spans must handle both.
- **Highlighting** (`app.py:highlight_issues`): inserts sentinel tokens (`@@IO{id}@@` /
  `@@IC{id}@@`) at offsets, renders Markdown, *then* swaps tokens for `<span>` tags,
  then sanitizes with `bleach`. This avoids the old bug of splicing raw HTML into
  Markdown source. Tokens are plain alphanumerics on purpose (underscores would trigger
  Markdown emphasis). Out-of-range spans are skipped.
- **Ground truth** = `reviews.ground_truth(qa_result)`: a human `Review.corrected_quality`
  if one exists, else `QAResult.expected_status` (the synthetic label for
  `source='generated'`, `None` for uploads). Accuracy compares `final_quality` against
  this. Don't recompute accuracy inline — call `reviews.accuracy(session)`.
- Provider lock-in is avoided via `judge.py`. To add a provider, add a `Judge` impl and
  a branch in `get_judge`. The OpenAI path stays OpenAI; Anthropic code uses the
  official SDK.

## Tests

`tests/conftest.py` sets `LLM_PROVIDER=fake` and a temp `DATA_DIR` before importing
`config`, so the suite is fully offline and isolated from real data. Pure functions
(scoring, rules, sections, RAG) are tested directly; the engine is tested with a stub
judge; `app.py` via the Flask test client.
