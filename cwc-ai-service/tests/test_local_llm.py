"""OpenAI-compatible path used for a self-hosted Qwen (llama.cpp / vLLM / SGLang)."""
from types import SimpleNamespace

from services import vision_service, usage_tracker


class _FakeClient:
    def __init__(self, text):
        self.sent = None
        self._text = text
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.sent = kw
        return SimpleNamespace(
            model=kw["model"], id="local-1",
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._text))],
            usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=300),
        )


def _local(monkeypatch, text):
    fake = _FakeClient(text)
    monkeypatch.setattr(vision_service, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(vision_service, "LOCAL_LLM", True)
    monkeypatch.setattr(vision_service, "OPENAI_MODEL", "qwen-local")
    monkeypatch.setattr(vision_service, "_get_openai_client", lambda: fake)
    return fake


def test_local_call_disables_thinking_and_strips_think_block(monkeypatch):
    fake = _local(monkeypatch, "<think>hmm</think>\n{\"ok\": true}")
    out = vision_service.classify_with_vision(
        [{"page_num": 1, "base64_png": "AAAA"}], "ocr", "system prompt")
    assert out == '{"ok": true}'
    assert fake.sent["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert fake.sent["model"] == "qwen-local"
    assert fake.sent["messages"][0] == {"role": "system", "content": "system prompt"}


def test_extraction_model_override_is_passed_through(monkeypatch):
    fake = _local(monkeypatch, "{}")
    vision_service.classify_from_ocr_text("ocr", "p", model="qwen-small")
    assert fake.sent["model"] == "qwen-small"


def test_repair_goes_to_the_active_provider(monkeypatch):
    fake = _local(monkeypatch, '{"fixed": 1}')
    assert vision_service.repair_output("bad", ["x missing"], "p") == '{"fixed": 1}'
    assert fake.sent["messages"][-1]["role"] == "user"


def test_local_usage_is_counted_at_zero_cost(monkeypatch):
    _local(monkeypatch, "{}")
    with usage_tracker.track("trk-local", "classify") as acc:
        vision_service.classify_from_ocr_text("ocr", "p")
    call = acc["calls"][0]
    assert (call["inputTokens"], call["outputTokens"], call["costUsd"]) == (1200, 300, 0.0)


def test_real_openai_gets_no_unknown_fields(monkeypatch):
    fake = _local(monkeypatch, "{}")
    monkeypatch.setattr(vision_service, "LOCAL_LLM", False)
    vision_service.classify_from_ocr_text("ocr", "p")
    assert fake.sent["extra_body"] == {}
