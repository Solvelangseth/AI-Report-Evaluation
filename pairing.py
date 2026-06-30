"""
Pairing — join the two timelines.

A capture session has two parallel timelines: transcript segments (each with a
``[start, end]`` window) and photos (each with a capture timestamp). Pairing
produces **context bundles** — a segment plus the photos taken while it was being
spoken — which `extraction.py` turns into structured `Finding`s.

A photo is assigned to the segment whose window contains its timestamp; a photo
that falls in no window (silence, or clock skew) is attached to the nearest
segment so no evidence is dropped.
"""

from typing import List

from pydantic import BaseModel

from capture import Photo, Transcript, TranscriptSegment


class ContextBundle(BaseModel):
    """One spoken segment paired with the photos taken during it."""
    segment: TranscriptSegment
    photos: List[Photo] = []

    @property
    def has_content(self) -> bool:
        return bool(self.segment.text.strip() or self.photos)


def _contained_index(segments: List[TranscriptSegment], t: float):
    """Index of the segment whose window contains ``t``, else None."""
    for i, seg in enumerate(segments):
        if seg.contains(t):
            return i
    return None


def _nearest_index(segments: List[TranscriptSegment], t: float) -> int:
    """Index of the segment whose window contains ``t``, else the closest one."""
    contained = _contained_index(segments, t)
    if contained is not None:
        return contained
    # Distance to the nearest window edge.
    return min(
        range(len(segments)),
        key=lambda i: min(abs(t - segments[i].start), abs(t - segments[i].end)),
    )


def _orphan_bundle(photo: Photo) -> ContextBundle:
    """A standalone bundle for a photo taken outside any spoken segment."""
    return ContextBundle(
        segment=TranscriptSegment(start=photo.timestamp, end=photo.timestamp, text=""),
        photos=[photo])


def pair(transcript: Transcript, photos: List[Photo],
         standalone_orphans: bool = False) -> List[ContextBundle]:
    """Align ``photos`` to ``transcript`` segments by timestamp.

    Returns one bundle per segment (in order); empty bundles are kept so the
    transcript timeline stays intact for downstream extraction.

    ``standalone_orphans``: a photo that falls in no segment window gets its OWN
    bundle (with an empty segment) rather than being attached to the nearest
    speech. This stops a photo shot in silence — typically a deliberate,
    category-tagged shot — from being mis-attributed to unrelated narration.
    """
    segments = transcript.segments
    bundles = [ContextBundle(segment=s) for s in segments]
    if not segments:
        return [_orphan_bundle(p) for p in sorted(photos, key=lambda p: p.timestamp)] \
            if standalone_orphans else bundles

    orphans: List[ContextBundle] = []
    for photo in sorted(photos, key=lambda p: p.timestamp):
        idx = _contained_index(segments, photo.timestamp)
        if idx is not None:
            bundles[idx].photos.append(photo)
        elif standalone_orphans:
            orphans.append(_orphan_bundle(photo))
        else:
            bundles[_nearest_index(segments, photo.timestamp)].photos.append(photo)
    return bundles + orphans
