"""The capture → pair → extract → author seam, fully offline."""

import capture
from capture import FakeTranscriber, Photo, Transcript, TranscriptSegment
from extraction import FakeExtractor, extract_findings
from intake import build_findings, run_intake
from pairing import pair


def _write_sidecar(tmp_path):
    # FakeTranscriber reads "<audio>.txt" with "start-end: text" lines.
    audio = tmp_path / "visit.m4a"
    audio.write_bytes(b"")
    (tmp_path / "visit.txt").write_text(
        "0-15: Vi er i kjelleren. Fuktmåling viser 22 % i vegg ved gulvnivå.\n"
        "15-30: På badet er det alvorlig råteskade ved sluk, om lag 0,6 m².\n",
        encoding="utf-8",
    )
    return str(audio)


def test_fake_transcriber_reads_timed_sidecar(tmp_path):
    audio = _write_sidecar(tmp_path)
    transcript = FakeTranscriber().transcribe(audio)
    assert len(transcript.segments) == 2
    assert transcript.segments[0].start == 0 and transcript.segments[0].end == 15
    assert "kjeller" in transcript.text.lower()


def test_transcriber_falls_back_to_single_empty_segment(tmp_path):
    audio = tmp_path / "nosidecar.m4a"
    audio.write_bytes(b"")
    transcript = FakeTranscriber().transcribe(str(audio))
    assert len(transcript.segments) == 1


def test_pairing_assigns_photo_to_containing_segment():
    transcript = Transcript(segments=[
        TranscriptSegment(start=0, end=15, text="kjeller"),
        TranscriptSegment(start=15, end=30, text="bad"),
    ])
    photos = [Photo(id="p1", timestamp=8.0), Photo(id="p2", timestamp=20.0)]
    bundles = pair(transcript, photos)
    assert [p.id for p in bundles[0].photos] == ["p1"]
    assert [p.id for p in bundles[1].photos] == ["p2"]


def test_pairing_attaches_out_of_window_photo_to_nearest():
    transcript = Transcript(segments=[TranscriptSegment(start=0, end=10, text="x")])
    bundles = pair(transcript, [Photo(id="late", timestamp=999.0)])
    assert [p.id for p in bundles[0].photos] == ["late"]  # nothing dropped


def test_pairing_standalone_orphan_gets_own_bundle():
    transcript = Transcript(segments=[TranscriptSegment(start=0, end=10, text="snakk")])
    bundles = pair(transcript, [Photo(id="silent", timestamp=999.0, category="tak")],
                   standalone_orphans=True)
    # The in-silence photo is NOT attached to the spoken segment...
    assert bundles[0].photos == []
    # ...it gets its own bundle so its category can still seed a finding.
    assert len(bundles) == 2 and [p.id for p in bundles[1].photos] == ["silent"]


def test_fake_extractor_grounds_part_and_severity():
    seg = TranscriptSegment(start=0, end=15,
                            text="Alvorlig råteskade i bærende takbjelke, nedbøyning 30 mm.")
    from pairing import ContextBundle
    finding = FakeExtractor().extract(ContextBundle(segment=seg))
    assert finding is not None
    assert finding.part == "tak"
    assert finding.severity == "TG3"
    assert "30 mm" in finding.measurement


def test_extract_findings_skips_empty_segments():
    transcript = Transcript(segments=[
        TranscriptSegment(start=0, end=15, text="Fukt i kjeller, 22 %."),
        TranscriptSegment(start=15, end=30, text=""),  # silence → no finding
    ])
    findings = extract_findings(pair(transcript, []), FakeExtractor())
    assert len(findings) == 1
    assert findings[0].part == "kjeller"


def test_build_findings_end_to_end(tmp_path):
    audio = _write_sidecar(tmp_path)
    photos = [Photo(id="p1", timestamp=8.0, caption="fuktmåler mot vegg")]
    _, bundles, findings = build_findings(audio, photos, provider="fake")
    assert len(bundles) == 2
    parts = {f.part for f in findings}
    assert {"kjeller", "bad"} <= parts


def test_run_intake_produces_validated_draft(tmp_path):
    audio = _write_sidecar(tmp_path)
    result = run_intake(audio, [Photo(id="p1", timestamp=8.0)], provider="fake")
    assert result.findings
    # The existing authoring pipeline composed and QA'd the draft.
    assert result.authoring.report_text
    assert result.authoring.final_quality in ("clean", "minor_error", "major_error")
