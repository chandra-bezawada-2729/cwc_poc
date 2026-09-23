"""
Unit tests for utils/json_utils.py — parse_llm_json, validate_schema, parse_deadline.
No live LLM calls.
"""
import pytest
from datetime import date

from utils.json_utils import parse_llm_json, validate_schema, parse_deadline


# ── parse_llm_json ────────────────────────────────────────────────────────────

class TestParseLlmJson:
    def test_clean_json(self):
        raw = '{"documentCategory": "PAYER_CARE_GAP", "modelConfidence": 0.9}'
        result = parse_llm_json(raw)
        assert result["documentCategory"] == "PAYER_CARE_GAP"

    def test_fenced_json_with_language_tag(self):
        raw = '```json\n{"documentCategory": "FOLLOW_UP"}\n```'
        result = parse_llm_json(raw)
        assert result["documentCategory"] == "FOLLOW_UP"

    def test_fenced_json_without_language_tag(self):
        raw = '```\n{"documentCategory": "PRIOR_AUTHORIZATION"}\n```'
        result = parse_llm_json(raw)
        assert result["documentCategory"] == "PRIOR_AUTHORIZATION"

    def test_prose_wrapped_json(self):
        raw = (
            "Here is my classification result:\n\n"
            '{"documentCategory": "PHARMACY_REQUEST", "modelConfidence": 0.85}\n\n'
            "I hope this helps."
        )
        result = parse_llm_json(raw)
        assert result["documentCategory"] == "PHARMACY_REQUEST"

    def test_empty_input_returns_none(self):
        assert parse_llm_json("") is None
        assert parse_llm_json(None) is None

    def test_malformed_json_returns_none(self):
        result = parse_llm_json('{"documentCategory": "PAYER_CARE_GAP"')
        assert result is None

    def test_pure_prose_no_json_returns_none(self):
        result = parse_llm_json("I cannot classify this document because it is unclear.")
        assert result is None

    def test_nested_json(self):
        raw = '{"documentCategory": "MEDICATION_REVIEW", "evidence": ["polypharmacy notice", "candidates for discontinuation"]}'
        result = parse_llm_json(raw)
        assert isinstance(result["evidence"], list)
        assert len(result["evidence"]) == 2

    def test_fenced_with_leading_whitespace(self):
        raw = "\n\n```json\n{\"documentCategory\": \"REFERRAL\"}\n```\n"
        result = parse_llm_json(raw)
        assert result["documentCategory"] == "REFERRAL"


# ── validate_schema ───────────────────────────────────────────────────────────

def _valid_payload(**overrides) -> dict:
    base = {
        # Taxonomy v2.0: the v1.0 codes (PAYER_CARE_GAP, PHARMACY_REQUEST, …)
        # are subtypes of MISCELLANEOUS now, not categories in their own right.
        "documentCategory":          "MISCELLANEOUS",
        "documentSubtype":           "PAYER_CARE_GAP",
        "specification":             "Healthfirst",
        "modelConfidence":           0.94,
        "runnerUpCategory":          "FORM",
        "runnerUpConfidence":        0.05,
        "reason":                    "Health plan letterhead; HEDIS quality measure named.",
        "evidence":                  ["Based on claims data", "is a HEDIS Quality Measure"],
        "senderOrganization":        "Healthfirst",
        "senderFaxNumber":           "+12124978948",
        "senderCallbackFax":         None,
        "senderFaxNumberSource":     "FILENAME",
        "actionRequired":            True,
        "actionSummary":             "Tick response box and fax back.",
        "responseDeadline":          "2026-09-01",
        "patientIdentifiersPresent": ["MEMBER_ID", "DOB"],
        "pageCount":                 1,
        "containsFillableForm":      False,
        "classificationMode":        "VISION",
        "ocrCharCount":              1832,
    }
    base.update(overrides)
    return base


class TestValidateSchema:
    def test_valid_payload_no_errors(self):
        errors = validate_schema(_valid_payload())
        assert errors == []

    def test_none_input(self):
        errors = validate_schema(None)
        assert len(errors) > 0
        assert any("parseable" in e.lower() or "none" in e.lower() for e in errors)

    def test_missing_required_field(self):
        payload = _valid_payload()
        del payload["documentCategory"]
        errors = validate_schema(payload)
        assert any("documentCategory" in e for e in errors)

    def test_unknown_category(self):
        errors = validate_schema(_valid_payload(documentCategory="INVOICE"))
        assert any("INVOICE" in e for e in errors)

    def test_confidence_out_of_range(self):
        errors = validate_schema(_valid_payload(modelConfidence=1.5))
        assert any("modelConfidence" in e for e in errors)

    def test_confidence_negative(self):
        errors = validate_schema(_valid_payload(runnerUpConfidence=-0.1))
        assert any("runnerUpConfidence" in e for e in errors)

    def test_evidence_not_a_list(self):
        errors = validate_schema(_valid_payload(evidence="some quote"))
        assert any("evidence" in e for e in errors)

    def test_invalid_sender_fax_number_source(self):
        errors = validate_schema(_valid_payload(senderFaxNumberSource="UNKNOWN_SOURCE"))
        assert any("senderFaxNumberSource" in e for e in errors)

    def test_unknown_category_code_is_valid(self):
        # UNKNOWN is a valid category for unreadable documents
        errors = validate_schema(_valid_payload(documentCategory="UNKNOWN"))
        assert errors == []

    def test_missing_multiple_fields(self):
        payload = _valid_payload()
        del payload["documentCategory"]
        del payload["actionRequired"]
        errors = validate_schema(payload)
        assert len(errors) >= 2


# ── parse_deadline ────────────────────────────────────────────────────────────

class TestParseDeadline:
    def test_iso_format(self):
        assert parse_deadline("2026-09-01") == date(2026, 9, 1)

    def test_day_mon_year_format(self):
        assert parse_deadline("01SEP2026") == date(2026, 9, 1)

    def test_day_mon_year_single_digit(self):
        assert parse_deadline("1SEP2026") == date(2026, 9, 1)

    def test_month_day_year_format(self):
        assert parse_deadline("September 1, 2026") == date(2026, 9, 1)

    def test_month_day_year_abbreviated(self):
        assert parse_deadline("Sep 1, 2026") == date(2026, 9, 1)

    def test_slash_format(self):
        assert parse_deadline("09/01/2026") == date(2026, 9, 1)

    def test_null_string(self):
        assert parse_deadline("null") is None
        assert parse_deadline("None") is None
        assert parse_deadline("") is None
        assert parse_deadline(None) is None

    def test_unrecognised_format_returns_none(self):
        result = parse_deadline("next Tuesday")
        assert result is None


# ── routingEvidence / namingEvidence (prompt v2.1, display-only) ──────────────

class TestDisplayEvidenceIsOptional:
    """
    These two arrays are curated subsets of `evidence`, shown in the UI and read
    by nothing else. A repair retry is a second vision call, so the contract is
    that only a wrong container type is worth one — never an absent, empty or
    ragged list. The backend depends on this: it stores whatever arrives.
    """

    @pytest.mark.parametrize("key", ["routingEvidence", "namingEvidence"])
    def test_absent_is_valid(self, key):
        payload = _valid_payload()
        payload.pop(key, None)
        assert validate_schema(payload) == []

    @pytest.mark.parametrize("key", ["routingEvidence", "namingEvidence"])
    def test_null_is_valid(self, key):
        payload = _valid_payload()
        payload[key] = None
        assert validate_schema(payload) == []

    @pytest.mark.parametrize("key", ["routingEvidence", "namingEvidence"])
    def test_empty_list_is_valid(self, key):
        payload = _valid_payload()
        payload[key] = []
        assert validate_schema(payload) == []

    @pytest.mark.parametrize("key", ["routingEvidence", "namingEvidence"])
    def test_ragged_list_is_valid(self, key):
        """classify_fax coerces these; a retry would buy nothing."""
        payload = _valid_payload()
        payload[key] = ["IOP: TP OD: 17 OS: 15", None, 42]
        assert validate_schema(payload) == []

    @pytest.mark.parametrize("key", ["routingEvidence", "namingEvidence"])
    def test_wrong_container_type_is_an_error(self, key):
        payload = _valid_payload()
        payload[key] = "Dry AMD, Early Dry Stage OU"
        errors = validate_schema(payload)
        assert any(key in e for e in errors)

    def test_populating_them_never_touches_evidence(self):
        """
        evidence feeds evidence_score and calibrated confidence. Validation must
        judge it on its own terms whatever the display subsets contain.
        """
        payload = _valid_payload()
        payload["evidence"] = ["Imp/Plan: 1. Diabetes, Type II", "Dear Dr."]
        payload["routingEvidence"] = ["Imp/Plan: 1. Diabetes, Type II"]
        payload["namingEvidence"] = []
        assert validate_schema(payload) == []
