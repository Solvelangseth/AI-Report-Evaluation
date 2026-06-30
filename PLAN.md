# Plan — Capture → Authoring → QA (living document)

> This is a **loose, evolving** plan. Expect it to change as we build. The point is
> direction and the module seams, not a fixed spec.

## Vision

Turn a site visit into a draft report. The inspector narrates while taking photos;
the system transcribes, pairs each photo to what was being said, extracts structured
findings, then runs the **existing** authoring + QA pipeline to draft and validate a
report the certified inspector reviews and signs.

First vertical: **Norwegian house inspection (tilstandsrapport)**, **voice + photos**.
Environmental surveying comes later via a "domain profile" — not built until the first
vertical works end-to-end.

## The seam (why this is additive, not a rewrite)

```
NEW front half                                  EXISTING back half (reused as-is)
─────────────────────────────────────────────  ─────────────────────────────────────
voice + timestamped photos                      authoring.AuthoringPipeline:
  → transcript (segments w/ start,end)            List[Finding]
  → pairing (photo ↔ segment by timestamp)          → CostAnalyst (prices.py)
  → extraction (vision: bundle → Finding)           → Composer (6 sections)
  ───────────────────────────────────────►          → QAEngine (judge + rules + RAG)
            List[Finding]                            → revise loop → draft
```

`Finding` (already in `authoring.py`) is the contract between the two halves.

## Modules

**New**
- `capture.py` — transcription, provider-agnostic (`whisper`/`openai`/`fake`), like `judge.py`.
- `pairing.py` — align photos to transcript segments by timestamp → context bundles.
- `extraction.py` — vision agent: `(transcript snippet + image) → Finding`. Provider-agnostic + `fake`.
- DB: `CaptureSession`, `TranscriptSegment`, `MediaItem` tables in `db_setup.py`.
- UI: a capture/upload page (audio file + photos with timestamps).

**Reused as-is**
- `authoring.py`, `judge.py`, `qa_engine.py`, `scoring.py`, `rag_pipeline.py`,
  `db_setup.seed_regulations`, `prices.py`, `eval_harness.py`.

**Later (only when adding surveying)**
- Extract a `domains/` profile: {section schema, regulation set, price reference, agents}.
  Currently hardcoded in `qa_rules.py`, `prices.py`, `seed_regulations()`.

## Conventions to keep (from CLAUDE.md)

- Provider-agnostic with a `fake` offline impl for every new agent → suite stays offline.
- Pydantic-validated structured output.
- DB is the single source of truth; no JSON side-files.
- Norwegian domain content stays Norwegian.

## Rough milestones (reorderable)

1. ✅ **Data model + seam** — `capture.py` (Transcript/Segment/Photo + `FakeTranscriber`),
   `pairing.py` (timestamp join → `ContextBundle`), `extraction.py` (bundle → `Finding`,
   `FakeExtractor` + vision impls), `intake.py` (wires the front half to the existing
   `AuthoringPipeline`). `voice+photos → List[Finding] → compose → QA` runs offline
   end-to-end; 8 tests in `tests/test_intake.py`. *(DB persistence deferred to its own slice.)*
2. **Real transcription** — wire `OpenAITranscriber` (Whisper, Norwegian) live; verify segment timestamps.
3. **Real pairing** — already timestamp-windowed; revisit only if real Whisper timings need tuning.
4. **Real extraction** — exercise `AnthropicExtractor`/`OpenAIExtractor` vision live: bundle + photo → grounded `Finding`.
5. ✅ **DB + UI** — `CaptureSession`/`TranscriptSegment`/`MediaItem` tables; `capture_store.save_session`
   persists the capture inputs + the composed draft (a `Report` source='authored' + `QAResult`,
   so it reuses the report detail/highlight/review views). Routes `/capture` (GET form, POST →
   draft) and `/session/<id>`; "New Inspection" nav link; `capture.html` + `session_detail.html`.
   Pasted transcript works fully offline; audio upload path transcribes when a real provider is set.
   Photos render as thumbnails on the session page and as an "Evidence photos" block on the draft
   report, served via a traversal-safe `/media/<filename>` route. 9 tests in `tests/test_capture_store.py`.
6. ✅ **Real vision extraction** — `AnthropicExtractor`/`OpenAIExtractor` send the segment's
   photos (Claude/OpenAI-supported types only; HEIC skipped; capped at `_MAX_IMAGES`) with an
   `is_finding` gate so navigation/small-talk segments yield no junk finding. Injectable `client`
   for offline tests (8 in `tests/test_extraction.py`). **Validated live**: with a transcript that
   omitted the number, both Claude Opus 4.8 and gpt-4o-mini read "22 %" straight off the image and
   produced a grounded TG2 finding; full intake → draft → QA ran clean end-to-end.
   *(To use vision in the web UI, run the server with a real provider, e.g. `LLM_PROVIDER=openai`.)*
7. ✅ **Mobile capture API + categories** — REST API for the native iOS client (below); per-photo
   **category** (`categories.py`, grounded in forskrift kap. 2) is a strong `Finding.part` prior and
   lets a tagged photo seed a finding. `capture_store` now supports incremental build
   (create → add photos/audio/transcript → process) alongside the one-shot web path. Token auth via
   `CAPTURE_API_TOKEN`. 7 tests (`test_capture_api.py`, extra `test_extraction.py`); validated live.
8. 🚧 **Native iOS app (Swift)** — scaffolded in `ios/` (SwiftUI capture screen, `AudioRecorder`,
   camera→JPEG, `APIClient`, multipart, `CaptureModel` orchestrating record→snap→upload→finalize→poll).
   **Not yet compiled** — needs Xcode (see `ios/README.md`). Remaining: offline-first upload queue,
   custom camera UI, real auth.
9. ✅ **Review/edit step** — extracted findings persist as editable `SessionFinding`s; the session page
   has an inline editor (add/edit/delete) + **Recompose** to re-draft from the corrected set. API:
   `GET/POST/PUT/DELETE /api/sessions/{id}/findings`, `POST /api/sessions/{id}/recompose`.
10. ✅ **Async finalize** — opt-in `{"background": true}` runs processing in a thread (returns 202,
    poll `GET`); SQLite WAL + busy timeout keep reader/writer from contending.
11. ✅ **Web browse** — `/sessions` list of captures + their drafts; "Inspections" nav link.
12. **Pairing in the audio flow** — orphan photos now get standalone bundles (`standalone_orphans`); revisit
    if real Whisper timings need tuning.
13. **(Later)** domain profiles for environmental surveying; video capture mode.

## Mobile capture API (built — the iOS contract)

JSON/REST. All routes accept `Authorization: Bearer <CAPTURE_API_TOKEN>` (or `X-API-Key`);
open when the token is unset (dev). Status: `open → processing → <verdict> | failed`.

| Method & path | Purpose | Body / form |
|---|---|---|
| `GET /api/categories` | Category list for the picker | — → `{categories:[{slug,label,part}]}` |
| `POST /api/sessions` | Start a session | json `{title, transcript?}` → `{session_id,status}` |
| `POST /api/sessions/{id}/photos` | Add one photo | multipart `photo`, `timestamp`(s), `category`(slug), `caption?` |
| `POST /api/sessions/{id}/audio` | Upload the voice track | multipart `audio` |
| `POST /api/sessions/{id}/transcript` | Provide/replace transcript | json `{transcript}` |
| `POST /api/sessions/{id}/finalize` | Process → draft report | — → session json (`report_id`,`verdict`) |
| `GET /api/sessions/{id}` | Poll status / result | — → session json |

`timestamp` is seconds from recording start (how a photo pairs to spoken segments).
Provide either a transcript or audio (transcribed server-side with a real provider) before finalize.

### Open question surfaced here
- A photo whose timestamp lands far from any speech still pairs to the *nearest* segment.
  For category-tagged photos taken in silence, consider giving each its own bundle so the
  category alone seeds a finding (today the fake extractor does this; the timestamp-pairing
  path doesn't yet for the audio flow).

## Open questions (decide as we hit them)

- Photo timestamps: EXIF vs. user-supplied vs. capture-order? Start with explicit per-photo time.
- How to represent "measurement spoken aloud" parsing (e.g. "tjueto prosent fukt" → 22 %).
- Where does the human review/edit findings before composing? (likely a review step between extraction and authoring.)
