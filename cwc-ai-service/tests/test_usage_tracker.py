"""
Unit tests for services/usage_tracker.py.

The point of the module is that a per-fax cost can be read off a ledger rather
than estimated from a console total. These tests pin down the arithmetic, the
scoping, and the one property that matters most in production: it can never be
the reason a classification fails.
"""
import json
from types import SimpleNamespace

import pytest

from services import usage_tracker as ut


def fake_response(i, o, model="claude-opus-4-5-20251101", cw=0, cr=0, rid="req_x"):
    return SimpleNamespace(
        id=rid, model=model,
        usage=SimpleNamespace(input_tokens=i, output_tokens=o,
                              cache_creation_input_tokens=cw, cache_read_input_tokens=cr),
    )


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "ai_usage.jsonl"
    monkeypatch.setattr(ut, "_LEDGER", path)
    return path


class TestPricing:
    def test_opus_4_5_matches_the_published_rate(self):
        # 1M in at $5 + 1M out at $25
        assert ut.cost_usd("claude-opus-4-5", 1_000_000, 1_000_000) == pytest.approx(30.0)

    def test_dated_model_id_resolves_to_its_alias(self):
        assert ut.cost_usd("claude-opus-4-5-20251101", 7_561, 869) == \
               ut.cost_usd("claude-opus-4-5", 7_561, 869)

    def test_a_real_console_row(self):
        """req_011CfGGaMgYins2Z2vN6EfRd: 7,561 in / 869 out."""
        assert ut.cost_usd("claude-opus-4-5-20251101", 7_561, 869) == pytest.approx(0.059530, abs=1e-6)

    def test_longest_prefix_wins(self):
        """opus-4-1 must not be priced as opus-4 just because it starts with it."""
        assert ut.price_for("claude-opus-4-1-20250805")["input"] == 15.0
        assert ut.price_for("claude-opus-4-5-20251101")["input"] == 5.0

    @pytest.mark.parametrize("model,rate", [
        ("claude-opus-4-8-20260801", 5.0),
        ("claude-opus-4-6", 5.0),
        ("claude-opus-5", 5.0),
        ("claude-sonnet-5-20260901", 2.0),
        ("claude-sonnet-4-6", 3.0),
    ])
    def test_newer_models_do_not_fall_through_to_an_older_price(self, model, rate):
        """claude-opus-4-8 must not match the claude-opus-4 row and be tripled."""
        assert ut.price_for(model)["input"] == rate

    def test_unknown_model_is_none_not_zero(self):
        """Zero would quietly under-count the bill. None makes a report say 'unknown'."""
        assert ut.cost_usd("gpt-4o", 1000, 1000) is None

    def test_cache_tokens_are_priced_separately(self):
        base = ut.cost_usd("claude-opus-4-5", 0, 0, cache_write=1_000_000)
        read = ut.cost_usd("claude-opus-4-5", 0, 0, cache_read=1_000_000)
        assert base == pytest.approx(6.25)
        assert read == pytest.approx(0.50)

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("CWC_PRICE_OPUS_4_5_INPUT", "7")
        assert ut.price_for("claude-opus-4-5")["input"] == 7.0


class TestScope:
    def test_accumulates_every_call_in_the_scope(self, ledger):
        with ut.track("t-1", "classify", fileName="Sample 8.pdf") as acc:
            ut.record(fake_response(7_000, 600), "vision")
            ut.record(fake_response(7_500, 400), "repair")
        assert acc["inputTokens"] == 14_500
        assert acc["outputTokens"] == 1_000
        assert len(acc["calls"]) == 2
        assert [c["call"] for c in acc["calls"]] == ["vision", "repair"]

    def test_writes_one_ledger_line_per_scope(self, ledger):
        with ut.track("t-1", "classify"):
            ut.record(fake_response(100, 10), "vision")
        with ut.track("t-1", "extract"):
            ut.record(fake_response(200, 20), "vision")
        lines = [json.loads(l) for l in ledger.read_text().splitlines()]
        assert [l["stage"] for l in lines] == ["classify", "extract"]
        assert all(l["trackingId"] == "t-1" for l in lines)

    def test_record_outside_a_scope_is_a_noop(self, ledger):
        ut.record(fake_response(100, 10), "vision")
        assert not ledger.exists()

    def test_scopes_do_not_leak_into_each_other(self, ledger):
        with ut.track("a", "classify") as a:
            ut.record(fake_response(100, 10), "vision")
        with ut.track("b", "classify") as b:
            ut.record(fake_response(300, 30), "vision")
        assert a["inputTokens"] == 100
        assert b["inputTokens"] == 300

    def test_ledger_carries_no_document_content(self, ledger):
        """Only counts, model and IDs. Nothing from the prompt or the answer."""
        with ut.track("t-1", "classify", fileName="Sample 8.pdf"):
            ut.record(fake_response(100, 10), "vision")
        line = json.loads(ledger.read_text())
        allowed = {"trackingId", "stage", "calls", "inputTokens", "outputTokens",
                   "cacheWriteTokens", "cacheReadTokens", "costUsd", "costKnown",
                   "model", "fileName", "category", "durationMs", "at"}
        assert set(line) <= allowed

    def test_scope_with_no_calls_still_records_the_fax(self, ledger):
        """An OCR-only UNKNOWN path costs $0 — worth proving, not omitting."""
        with ut.track("t-1", "classify"):
            pass
        line = json.loads(ledger.read_text())
        assert line["calls"] == [] and line["costUsd"] == 0


class TestNeverBreaksClassification:
    def test_response_without_usage_is_ignored(self, ledger):
        with ut.track("t-1", "classify") as acc:
            ut.record(SimpleNamespace(model="claude-opus-4-5"), "vision")
        assert acc["inputTokens"] == 0

    def test_garbage_response_does_not_raise(self, ledger):
        with ut.track("t-1", "classify"):
            ut.record(object(), "vision")
            ut.record(None, "vision")

    def test_unwritable_ledger_does_not_raise(self, monkeypatch, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        monkeypatch.setattr(ut, "_LEDGER", blocker / "nested" / "ai_usage.jsonl")
        with ut.track("t-1", "classify"):
            ut.record(fake_response(100, 10), "vision")

    def test_exception_inside_scope_still_writes_the_line(self, ledger):
        """A failed classification still spent the tokens. They must be counted."""
        with pytest.raises(RuntimeError):
            with ut.track("t-1", "classify"):
                ut.record(fake_response(100, 10), "vision")
                raise RuntimeError("downstream failure")
        assert json.loads(ledger.read_text())["inputTokens"] == 100


class TestSummary:
    def test_summary_shape(self):
        with ut.track("t-1", "classify") as acc:
            ut.record(fake_response(7_561, 869), "vision")
        s = ut.summary(acc)
        assert s["calls"] == 1
        assert s["inputTokens"] == 7_561 and s["outputTokens"] == 869
        assert s["costUsd"] == pytest.approx(0.05953, abs=1e-5)

    def test_unknown_model_summary_cost_is_none(self):
        with ut.track("t-1", "classify") as acc:
            ut.record(fake_response(100, 10, model="gpt-4o"), "vision")
        assert ut.summary(acc)["costUsd"] is None
