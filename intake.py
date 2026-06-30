"""
Intake — the front half wired to the existing back half.

    audio + photos
      → capture.transcribe        (transcript with timestamped segments)
      → pairing.pair              (photos joined to segments → context bundles)
      → extraction.extract        (bundles → List[Finding])
      → authoring.AuthoringPipeline   (the EXISTING cost → compose → QA loop)

`Finding` is the seam: everything above produces it, the authoring pipeline
consumes it unchanged. Provider-agnostic throughout, so the whole path runs
offline with `LLM_PROVIDER=fake`. The output is a draft for the certified
inspector to review and sign — not an autonomously issued report.
"""

from typing import List, Optional

from pydantic import BaseModel

import capture
import extraction
from authoring import AuthoringPipeline, AuthoringResult, Finding, get_composer, get_cost_analyst
from capture import Photo, Transcript
from judge import get_judge
from pairing import ContextBundle, pair
from qa_engine import QAEngine


class IntakeResult(BaseModel):
    transcript: Transcript
    bundles: List[ContextBundle]
    findings: List[Finding]
    authoring: AuthoringResult


def build_findings(audio_path: str, photos: List[Photo],
                   provider: Optional[str] = None):
    """audio + photos → (transcript, bundles, findings). The capture→extract front half."""
    transcript = capture.get_transcriber(provider).transcribe(audio_path)
    bundles = pair(transcript, photos)
    findings = extraction.extract_findings(bundles, extraction.get_extractor(provider))
    return transcript, bundles, findings


def author_from_findings(findings: List[Finding], provider: Optional[str] = None,
                         rag=None, max_revisions: int = 1) -> AuthoringResult:
    """Compose + QA a draft from findings (no extraction).

    The recompose path: after the inspector edits the extracted findings, draft
    the report again from the corrected set.
    """
    pipeline = AuthoringPipeline(
        cost_analyst=get_cost_analyst(provider),
        composer=get_composer(provider),
        engine=QAEngine(judge=get_judge(provider), rag=rag),
    )
    return pipeline.run(findings, max_revisions=max_revisions)


def run_from_transcript(transcript: Transcript, photos: List[Photo],
                        provider: Optional[str] = None, rag=None,
                        max_revisions: int = 1) -> IntakeResult:
    """pair → extract → author, starting from an already-built transcript.

    Used when the transcript is pasted/edited in the UI rather than transcribed
    from audio, so the inspector can correct it before findings are extracted.
    """
    # Orphan photos (shot in silence, e.g. a deliberate category-tagged shot) get
    # their own bundle so they still seed a finding instead of being mis-attached.
    bundles = pair(transcript, photos, standalone_orphans=True)
    findings = extraction.extract_findings(bundles, extraction.get_extractor(provider))
    authoring = author_from_findings(findings, provider, rag, max_revisions)
    return IntakeResult(transcript=transcript, bundles=bundles, findings=findings,
                        authoring=authoring)


def run_intake(audio_path: str, photos: List[Photo], provider: Optional[str] = None,
               rag=None, max_revisions: int = 1) -> IntakeResult:
    """Full path: capture → pair → extract → author a validated draft report."""
    transcript = capture.get_transcriber(provider).transcribe(audio_path)
    return run_from_transcript(transcript, photos, provider, rag, max_revisions)
