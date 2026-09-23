"""
Tests must not depend on whichever provider the developer's .env points at.
vision_service loads .env at import, so a machine switched to the local Qwen
(LLM_PROVIDER=openai) would route the Anthropic-mocked tests to the wrong
client. Pin the Anthropic defaults here; tests that exercise the local path
override these with their own monkeypatch.
"""
import pytest

from services import vision_service


@pytest.fixture(autouse=True)
def _pin_provider(monkeypatch):
    monkeypatch.setattr(vision_service, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(vision_service, "LOCAL_LLM", False)
    monkeypatch.setattr(vision_service, "EXTRACTION_MODEL", vision_service.ANTHROPIC_EXTRACTION_MODEL)
