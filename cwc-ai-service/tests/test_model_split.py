"""Per-stage model selection and prompt-cache block construction."""
import json
from pathlib import Path

from services import vision_service, extraction_service

_PROMPT = (Path(__file__).parent.parent / "prompts" / "classification_prompt.md").read_text(encoding="utf-8")


def test_system_blocks_plain_string_when_cache_off(monkeypatch):
    monkeypatch.setattr(vision_service, "PROMPT_CACHE", False)
    assert vision_service.system_blocks("hello") == "hello"


def test_classification_prompt_splits_before_sender_context(monkeypatch):
    monkeypatch.setattr(vision_service, "PROMPT_CACHE", True)
    blocks = vision_service.system_blocks(_PROMPT)
    assert len(blocks) == 2
    static, dynamic = blocks[0]["text"], blocks[1]["text"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1]
    # Per-fax values must be outside the cached prefix or the cache never hits.
    assert "<<sender_fax_number>>" in dynamic and "<<received_at>>" in dynamic
    assert "<<sender_fax_number>>" not in static
    # The split must not lose or reorder a single character.
    assert static + dynamic == _PROMPT


def test_prompt_without_marker_is_cached_whole(monkeypatch):
    monkeypatch.setattr(vision_service, "PROMPT_CACHE", True)
    blocks = vision_service.system_blocks("static only")
    assert blocks == [{"type": "text", "text": "static only", "cache_control": {"type": "ephemeral"}}]


def test_extraction_uses_extraction_model_and_static_prompt(monkeypatch):
    seen = {}

    def fake_vision(pages, ocr, prompt, model=None):
        seen["model"], seen["prompt"] = model, prompt
        return json.dumps({"trackingId": "TRACKING_ID", "category": "LAB_REPORT",
                           "core": {}, "categoryData": {},
                           "extraction": {"modelName": "whatever", "fieldConfidences": {}}})

    monkeypatch.setattr(vision_service, "EXTRACTION_MODEL", "claude-sonnet-5")
    monkeypatch.setattr(vision_service, "classify_with_vision", fake_vision)
    monkeypatch.setattr(extraction_service.pdf_utils, "render_pages",
                        lambda path, max_pages=3: [{"page_num": 1}])
    monkeypatch.setattr(extraction_service, "run_ocr",
                        lambda path, pages: {"text": "", "char_count": 0})

    out = extraction_service.extract_metadata("x.pdf", "trk-123", "LAB_REPORT")

    assert seen["model"] == "claude-sonnet-5"
    assert "trk-123" not in seen["prompt"]          # keeps the prompt cacheable
    assert out["trackingId"] == "trk-123"            # restored after the call
    assert out["extraction"]["modelName"] == "claude-sonnet-5"


class _Block:
    def __init__(self, type_, text=""):
        self.type, self.text = type_, text


class _Resp:
    def __init__(self, blocks):
        self.content = blocks


def test_gen5_models_get_no_temperature_and_thinking_off(monkeypatch):
    monkeypatch.setattr(vision_service, "THINKING_MODE", "disabled")
    for m in ("claude-sonnet-5", "claude-opus-5", "claude-sonnet-5-20260901"):
        opts = vision_service.request_options(m)
        assert "temperature" not in opts, m          # would be a 400
        assert opts["thinking"] == {"type": "disabled"}


def test_gen4_models_keep_temperature_zero():
    for m in ("claude-opus-4-5", "claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8"):
        assert vision_service.request_options(m) == {"temperature": 0}, m


def test_response_text_skips_thinking_blocks():
    r = _Resp([_Block("thinking"), _Block("text", '{"a": 1}')])
    assert vision_service.response_text(r) == '{"a": 1}'
