"""Vision extraction wiring, exercised offline with stub SDK clients."""

import json
from types import SimpleNamespace

import pytest

from capture import Photo, TranscriptSegment
from extraction import AnthropicExtractor, OpenAIExtractor, _load_images
from pairing import ContextBundle

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c49444154789c6360000002000154a24f230000000049454e44ae426082"
)


def _bundle(text="Fukt i kjeller, 22 % i vegg.", photos=None):
    return ContextBundle(segment=TranscriptSegment(start=0, end=15, text=text),
                         photos=photos or [])


# --- Anthropic stub client (records calls, returns canned text block) ---
class _AnthropicStub:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.payload)])


class _OpenAIStub:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        msg = SimpleNamespace(content=self.payload)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


FINDING_JSON = json.dumps({
    "is_finding": True, "part": "bad", "observation": "Fukt ved sluk",
    "measurement": "19 %", "cause": "membransvikt", "consequence": "fuktskade",
    "recommendation": "utbedre membran", "severity": "TG2",
})


def test_anthropic_extractor_parses_finding():
    ext = AnthropicExtractor(client=_AnthropicStub(FINDING_JSON), model="m")
    finding = ext.extract(_bundle())
    assert finding is not None
    assert finding.part == "bad" and finding.measurement == "19 %"
    assert finding.severity == "TG2"


def test_openai_extractor_parses_finding():
    ext = OpenAIExtractor(client=_OpenAIStub(FINDING_JSON), model="m")
    finding = ext.extract(_bundle())
    assert finding is not None and finding.part == "bad"


def test_is_finding_false_returns_none():
    payload = json.dumps({"is_finding": False, "part": "", "observation": "", "severity": "TG0"})
    assert AnthropicExtractor(client=_AnthropicStub(payload), model="m").extract(_bundle()) is None


def test_photo_sent_as_image_block(tmp_path):
    img = tmp_path / "wall.png"
    img.write_bytes(PNG_1PX)
    stub = _AnthropicStub(FINDING_JSON)
    bundle = _bundle(photos=[Photo(id="p1", timestamp=8.0, image_path=str(img))])
    AnthropicExtractor(client=stub, model="m").extract(bundle)
    content = stub.calls[0]["messages"][0]["content"]
    image_blocks = [b for b in content if b.get("type") == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["media_type"] == "image/png"


def test_openai_photo_sent_as_image_url(tmp_path):
    img = tmp_path / "wall.png"
    img.write_bytes(PNG_1PX)
    stub = _OpenAIStub(FINDING_JSON)
    bundle = _bundle(photos=[Photo(id="p1", timestamp=8.0, image_path=str(img))])
    OpenAIExtractor(client=stub, model="m").extract(bundle)
    content = stub.calls[0]["messages"][1]["content"]
    urls = [b for b in content if b.get("type") == "image_url"]
    assert urls and urls[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_unsupported_image_type_is_skipped(tmp_path):
    heic = tmp_path / "photo.heic"
    heic.write_bytes(b"\x00\x00")
    bundle = _bundle(photos=[Photo(id="p1", timestamp=1.0, image_path=str(heic))])
    assert _load_images(bundle) == []  # HEIC skipped, not sent as bad media type


def test_images_capped(tmp_path):
    photos = []
    for i in range(6):
        p = tmp_path / f"img{i}.jpg"
        p.write_bytes(PNG_1PX)
        photos.append(Photo(id=f"p{i}", timestamp=float(i), image_path=str(p)))
    assert len(_load_images(_bundle(photos=photos))) == 4  # _MAX_IMAGES


def test_empty_segment_returns_none():
    ext = AnthropicExtractor(client=_AnthropicStub(FINDING_JSON), model="m")
    assert ext.extract(_bundle(text="")) is None  # no content, no API call


def test_fake_extractor_uses_photo_category_for_part():
    from extraction import FakeExtractor
    # Spoken text mentions "tak" but the tagged category is "bad" — category wins.
    bundle = _bundle(text="ser litt på taket her",
                     photos=[Photo(id="p1", timestamp=1.0, category="bad")])
    finding = FakeExtractor().extract(bundle)
    assert finding.part == "bad"  # categories.part_for("bad")


def test_fake_extractor_category_only_photo_seeds_finding():
    from extraction import FakeExtractor
    # No speech, but a categorized photo still produces a finding.
    bundle = _bundle(text="", photos=[Photo(id="p1", timestamp=1.0, category="tak")])
    finding = FakeExtractor().extract(bundle)
    assert finding is not None and finding.part == "tak"
