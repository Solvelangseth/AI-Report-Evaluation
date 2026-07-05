# Project Overview — AI-Powered Inspection Report System (Norwegian *tilstandsrapporter*)

## One-line summary
A full-stack, AI-powered system that both **quality-checks** and **authors** Norwegian
building inspection reports — turning a site visit (voice narration + categorized
photos) into a regulation-grounded draft report, then validating it with a
multi-signal QA engine.

## What it does
The system has two complementary halves joined by a shared data contract:

1. **QA / evaluation engine** — ingests an inspection report (upload or generated) and
   classifies it `clean` / `minor_error` / `major_error` by combining three independent
   signals: deterministic rule checks, an LLM judge, and a RAG retrieval step over
   regulation references. Detected issues are highlighted inline at exact character
   offsets in the rendered report.

2. **Capture → authoring pipeline** — the inspector narrates a walkthrough while snapping
   photos tagged with a building-part category. The system transcribes the audio, pairs
   each photo to what was being said (by timestamp), uses **vision models to extract
   structured findings from the photos**, then runs a multi-agent authoring pipeline
   (cost analysis → report composition → QA → revise loop) to produce a draft report. A
   certified inspector reviews/edits the findings on the web app, recomposes, and signs
   it off (which locks it), then exports a PDF.

## Problem domain (why it's non-trivial)
Norwegian condition reports are legally regulated documents. The system is grounded in
the real framework: **forskrift til avhendingslova (FOR-2021-06-08-1850)**, the
**tilstandsgrad TG0–TG3** grading scale, and NS 3600 / NS 3424 (referenced, not
reproduced, as they're copyrighted). The whole domain is in Norwegian — section names,
regulation text, prompts. Cost figures and condition grades are legally relevant, so the
design deliberately frames the AI as a **drafting assistant for a certified expert, never
an autonomous issuer**, with disclaimers and a human sign-off step.

## Architecture & engineering approach
- **Clean layered design**: a pure evaluation core (no I/O), a persistence layer, and a
  thin Flask web layer. Pure functions (scoring, rules, section extraction, RAG ranking)
  are unit-tested directly; the engine with a stubbed judge; the web layer via the Flask
  test client.
- **Provider-agnostic AI layer**: every LLM touchpoint (judge, curator, cost analyst,
  composer, transcriber, extractor, embedder) sits behind a Python `Protocol` with
  implementations for **OpenAI**, **Anthropic (Claude)**, and a **`fake` offline
  implementation**. This makes the *entire* test suite run offline with no API keys, and
  swapping providers is a one-line config change.
- **Structured, validated LLM output**: all model responses are parsed through
  **Pydantic v2** schemas; malformed output raises typed errors instead of silently
  degrading, and a judge failure can never *lower* a verdict.
- **The "seam"**: a single `Finding` model is the contract between the capture front-half
  and the authoring back-half, so the new voice/photo pipeline plugged into the existing
  QA/authoring system without changing it.

## AI / ML techniques demonstrated
- **LLM application engineering** across two vendors (OpenAI + Anthropic Claude) with
  structured outputs.
- **Agentic AI / tool use**: an "agent judge" runs a Claude tool-use loop — it
  investigates before ruling (looks up standards, re-runs rule checks, retrieves similar
  reports, verifies exact issue spans) then submits a validated verdict via a terminal
  tool.
- **Multimodal / vision**: findings are extracted from inspection photos by Claude/OpenAI
  vision — e.g. reading a moisture meter's "22 %" off the image even when the narration
  omitted the number — with an `is_finding` gate to suppress noise.
- **RAG**: semantic retrieval via embedding cosine similarity with a lexical (Jaccard)
  fallback; example embeddings computed once and cached.
- **A learning "flywheel"**: human reviewer corrections (overrides) are distilled by a
  curation agent into new RAG examples, so retrieval and judging improve from real
  feedback over time.
- **Speech-to-text** (Whisper) with per-segment timestamps; **prompt engineering**
  grounded in real regulation; a **calibration harness** guarding the judge against
  over/under-flagging regressions.
- **Confidence-gated triage**: a free, deterministic uncertainty signal derived from
  rule-vs-LLM agreement routes reports to auto-clear or human review.

## Software engineering practices
- **131 automated tests, fully offline and hermetic** (deterministic `fake` provider,
  temp databases, keys neutralized) — covers pure logic, the engine, RAG ranking, the web
  routes, the capture API, vision-extraction wiring (via injected stub clients), and PDF
  generation.
- REST API design with token auth and **asynchronous background processing** (thread-based
  finalize with SQLite WAL + busy-timeout tuning for reader/writer concurrency).
- Lightweight schema migrations, cached DB engine, UTC-correct timestamps.
- Git branch + Pull Request workflow with focused, well-described commits; living design
  doc (`PLAN.md`), `README`, and an architecture guide (`CLAUDE.md`).

## Feature highlights
Upload/generate reports · inline issue highlighting · human accept/override review
workflow with a ground-truth accuracy metric · confidence-gated triage · RAG curation
flywheel · agentic tool-using judge · voice+photo capture → draft · timestamped
photo↔speech pairing · category-tagged vision extraction · grounded cost estimation ·
multi-section report composition with an auto-generated tilstandsgrad (TG) overview table
· editable findings + recompose · inspector sign-off/lock · **PDF export** with embedded
evidence photos · a token-secured **mobile capture REST API** · a **native iOS (SwiftUI)
capture app** (records voice, category picker, camera, uploads to the API).

## Tech stack
- **Backend/AI**: Python, Flask, SQLAlchemy, SQLite, Pydantic v2, OpenAI & Anthropic SDKs,
  Whisper, fpdf2, pytest.
- **Frontend**: Jinja2, Tailwind CSS, Alpine.js.
- **Mobile**: Swift, SwiftUI, AVFoundation (native iOS capture client).
- **Practices**: provider-agnostic architecture, offline-testable design, structured LLM
  outputs, RAG, agentic tool use, multimodal vision, git/PR workflow.

## Scope (solo project)
~28 Python modules (~4,900 lines of application code), 131 offline tests (~1,500 lines),
an 8-file SwiftUI iOS client, 4 interchangeable AI providers (openai / anthropic / agent /
fake), all grounded in real Norwegian building regulation.

## Skills demonstrated
Python · Flask · SQLAlchemy/SQL · REST API design · LLM application development (OpenAI +
Anthropic) · Retrieval-Augmented Generation (RAG) · agentic AI & tool use ·
multimodal/vision models · prompt engineering · structured output validation (Pydantic) ·
speech-to-text · test-driven development & hermetic test design · provider-agnostic /
decoupled architecture · async processing & concurrency · PDF generation · iOS/SwiftUI ·
domain modeling with regulatory grounding · Git/GitHub PR workflow.
