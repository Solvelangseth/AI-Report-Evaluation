"""
Capture layer — turn a site visit into a timestamped transcript.

The inspector narrates while taking photos. This module produces the transcript
(text split into time-stamped segments) that `pairing.py` later aligns with the
photos. Transcription is provider-agnostic with a `fake` offline implementation,
mirroring `judge.py` / `authoring.py`, so the whole intake pipeline runs and is
tested without an API key or audio backend.

Photos carry their own capture timestamp; the transcript carries segment time
windows. Those two timelines are what `pairing.py` joins.
"""

from typing import List, Optional, Protocol

from pydantic import BaseModel

import config


class CaptureError(RuntimeError):
    """Raised when transcription fails."""


# --- data models ---
class TranscriptSegment(BaseModel):
    start: float                   # seconds from start of recording
    end: float
    text: str

    def contains(self, t: float) -> bool:
        return self.start <= t <= self.end


class Transcript(BaseModel):
    segments: List[TranscriptSegment] = []

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments)


def _parse_line(line: str, cursor: float, segment_seconds: float):
    """Parse one transcript line into (start, end, text).

    Accepts an optional ``start-end: text`` timing hint; otherwise advances the
    clock by ``segment_seconds`` so plain lines still get a time window.
    """
    if ":" in line:
        head, _, tail = line.partition(":")
        head = head.strip()
        if "-" in head:
            lo, _, hi = head.partition("-")
            try:
                return float(lo), float(hi), tail.strip()
            except ValueError:
                pass
    return cursor, cursor + segment_seconds, line


def parse_transcript(text: str, segment_seconds: float = 15.0) -> Transcript:
    """Build a Transcript from free text (one utterance per line).

    The same format the UI's transcript box and the FakeTranscriber sidecar use:
    plain lines, or ``start-end: text`` to pin timings (so photos pair precisely).
    """
    segments: List[TranscriptSegment] = []
    cursor = 0.0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        start, end, body = _parse_line(line, cursor, segment_seconds)
        segments.append(TranscriptSegment(start=start, end=end, text=body))
        cursor = end
    return Transcript(segments=segments)


class Photo(BaseModel):
    """A photo taken during the visit, keyed to the recording timeline."""
    id: str
    timestamp: float               # seconds from start of recording
    image_path: str = ""           # path on disk (used by the vision extractor)
    caption: str = ""              # optional inspector note
    category: str = ""             # category slug (see categories.py) — a part prior


# --- transcriber protocol + implementations ---
class Transcriber(Protocol):
    def transcribe(self, audio_path: str) -> Transcript: ...


class FakeTranscriber:
    """Offline transcriber.

    Reads a sidecar ``<audio>.txt`` transcript if present (one utterance per
    line, optionally ``start-end: text``), otherwise returns a single segment so
    the pipeline still runs. Deterministic, no audio backend, no network.
    """

    def __init__(self, segment_seconds: float = 15.0):
        self.segment_seconds = segment_seconds

    def transcribe(self, audio_path: str) -> Transcript:
        raw = self._read_text(audio_path)
        transcript = parse_transcript(raw, self.segment_seconds)
        if not transcript.segments:
            transcript.segments.append(
                TranscriptSegment(start=0.0, end=self.segment_seconds, text=""))
        return transcript

    def _read_text(self, audio_path: str) -> str:
        sidecar = audio_path.rsplit(".", 1)[0] + ".txt" if "." in audio_path else audio_path
        try:
            with open(sidecar, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""


class OpenAITranscriber:
    """Whisper transcription with per-segment timestamps."""

    def __init__(self, api_key: str, model: str = "whisper-1"):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def transcribe(self, audio_path: str) -> Transcript:
        try:
            with open(audio_path, "rb") as fh:
                resp = self.client.audio.transcriptions.create(
                    model=self.model, file=fh, language="no",
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )
            segments = [
                TranscriptSegment(start=s.start, end=s.end, text=s.text.strip())
                for s in getattr(resp, "segments", []) or []
            ]
            if not segments and getattr(resp, "text", ""):
                segments = [TranscriptSegment(start=0.0, end=0.0, text=resp.text.strip())]
            return Transcript(segments=segments)
        except Exception as exc:  # noqa: BLE001 - surface as a typed error
            raise CaptureError(f"Whisper transcription failed: {exc}") from exc


def get_transcriber(provider: Optional[str] = None) -> Transcriber:
    provider = (provider or config.LLM_PROVIDER).lower()
    if provider == "fake":
        return FakeTranscriber()
    if provider in ("openai", "anthropic", "agent"):
        # Whisper is OpenAI-only; the Anthropic/agent paths still transcribe via
        # OpenAI when a key is present (no Claude audio endpoint).
        if not config.OPENAI_API_KEY:
            raise CaptureError("OPENAI_API_KEY not set for transcription (or use LLM_PROVIDER=fake).")
        return OpenAITranscriber(config.OPENAI_API_KEY)
    raise CaptureError(f"Unknown provider '{provider}' for transcription")
