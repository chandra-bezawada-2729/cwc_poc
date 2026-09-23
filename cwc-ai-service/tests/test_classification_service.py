"""
Unit tests for services/classification_service.py — mocked Anthropic client.
No test makes a live API call.
"""
import json
import pytest
from unittest.mock import patch, MagicMock


_VALID_LLM_RESPONSE = json.dumps({
    # Taxonomy v2.0: CWC files every payer/pharmacy letter into Miscellaneous.
    # The old v1.0 codes survive as subtypes, not as categories.
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
    "actionSummary":             "Tick response box and fax back by the deadline.",
    "responseDeadline":          "2026-09-01",
    "patientIdentifiersPresent": ["MEMBER_ID", "DOB"],
    "pageCount":                 1,
    "containsFillableForm":      False,
    "classificationMode":        "VISION",
    "ocrCharCount":              1832,
})


def _make_anthropic_response(text: str):
    """Build a minimal mock Anthropic response object."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


@pytest.fixture
def mock_render_pages():
    """Stub render_pages to return a single fake page."""
    fake_page = {
        "page_num": 1,
        "base64_png": "aGVsbG8=",  # base64("hello")
        "width": 800,
        "height": 1100,
        "size_bytes": 1000,
    }
    with patch("services.classification_service.pdf_utils.render_pages", return_value=[fake_page]):
        yield


@pytest.fixture
def mock_run_ocr():
    """Stub run_ocr to return a plausible result."""
    ocr_result = {
        "text": "Based on claims data is a HEDIS Quality Measure statin therapy",
        "char_count": 65,
        "mode": "TESSERACT",
        "per_page_char_counts": [65],
        "tesseract_available": True,
    }
    with patch("services.classification_service.run_ocr", return_value=ocr_result):
        yield


class TestClassifyFax:
    def test_successful_vision_classification(self, mock_render_pages, mock_run_ocr):
        mock_response = _make_anthropic_response(_VALID_LLM_RESPONSE)
        with patch("services.vision_service._get_anthropic_client") as mock_client:
            mock_client.return_value.messages.create.return_value = mock_response
            from services.classification_service import classify_fax
            result = classify_fax(
                file_path="/fake/path/file.pdf",
                tracking_id="test-tracking-123",
                sender_fax_number="+12124978948",
                received_at="2026-08-19T01:43:00Z",
            )
        assert result["documentCategory"] == "MISCELLANEOUS"
        assert result["documentSubtype"] == "PAYER_CARE_GAP"
        assert "calibratedConfidence" in result
        assert "confidenceBand" in result
        assert result["promptVersion"].startswith("cwc-classify-")

    def test_schema_failure_triggers_repair(self, mock_render_pages, mock_run_ocr):
        bad_response = '{"documentCategory": "MISCELLANEOUS"}'  # missing required fields
        good_response = _make_anthropic_response(_VALID_LLM_RESPONSE)
        bad_mock = _make_anthropic_response(bad_response)

        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return bad_mock
            return good_response  # repair call returns good response

        with patch("services.vision_service._get_anthropic_client") as mock_client:
            mock_client.return_value.messages.create.side_effect = side_effect
            from importlib import reload
            import services.classification_service as cls_mod
            result = cls_mod.classify_fax(
                file_path="/fake/path/file.pdf",
                tracking_id="test-repair-123",
            )
        assert result["documentCategory"] == "MISCELLANEOUS"

    def test_no_render_pages_with_thin_ocr_returns_unknown(self):
        """If rendering fails and OCR < 50 chars, result is UNKNOWN."""
        with patch("services.classification_service.pdf_utils.render_pages", side_effect=RuntimeError("render failed")):
            with patch("services.classification_service.run_ocr", return_value={
                "text": "short", "char_count": 5, "mode": "TESSERACT",
                "per_page_char_counts": [], "tesseract_available": True,
            }):
                from services.classification_service import classify_fax
                result = classify_fax(
                    file_path="/fake/path/file.pdf",
                    tracking_id="test-unknown-123",
                )
        assert result["documentCategory"] == "UNKNOWN"
        assert result["forceManualReview"] is True

    def test_ocr_fallback_when_vision_unavailable(self, mock_render_pages):
        """Vision fails → falls back to text-only Claude call."""
        ocr_result = {
            "text": "Prior Authorization Follow Up patient waiting for medication ePA",
            "char_count": 65,
            "mode": "TESSERACT",
            "per_page_char_counts": [65],
            "tesseract_available": True,
        }
        with patch("services.classification_service.run_ocr", return_value=ocr_result):
            with patch("services.vision_service.classify_with_vision",
                       side_effect=RuntimeError("vision unavailable")):
                fallback_response = _make_anthropic_response(
                    _VALID_LLM_RESPONSE.replace('"VISION"', '"OCR_FALLBACK"')
                )
                with patch("services.vision_service._get_anthropic_client") as mock_client:
                    mock_client.return_value.messages.create.return_value = fallback_response
                    from services.classification_service import classify_fax
                    result = classify_fax(
                        file_path="/fake/path/file.pdf",
                        tracking_id="test-fallback-123",
                    )
        assert result["documentCategory"] == "MISCELLANEOUS"


class TestDisplayEvidenceSubsets:
    """
    routingEvidence / namingEvidence are display-only (prompt v2.1).

    The load-bearing rule is the first test: `evidence` is what
    compute_calibrated_confidence fuzzy-matches against the OCR text to produce
    evidence_score. Narrowing it to the curated quotes would quietly move every
    document's confidence and invalidate the eval baseline, and nothing in the
    pipeline would raise. This is the tripwire for that.
    """

    def test_evidence_is_passed_through_untouched(self, mock_render_pages, mock_run_ocr):
        payload = json.loads(_VALID_LLM_RESPONSE)
        payload["evidence"] = [
            "Dear Dr.",
            "Based on claims data",
            "is a HEDIS Quality Measure",
        ]
        payload["routingEvidence"] = ["is a HEDIS Quality Measure"]
        payload["namingEvidence"]  = ["Healthfirst"]

        with patch("services.vision_service._get_anthropic_client") as mock_client:
            mock_client.return_value.messages.create.return_value = \
                _make_anthropic_response(json.dumps(payload))
            from services.classification_service import classify_fax
            result = classify_fax(
                file_path="/fake/path/file.pdf",
                tracking_id="test-evidence-123",
                sender_fax_number="+12124978948",
                received_at="2026-08-19T01:43:00Z",
            )

        assert result["evidence"] == payload["evidence"], (
            "evidence was narrowed; evidence_score and calibrated confidence "
            "just moved for every document"
        )
        assert result["routingEvidence"] == ["is a HEDIS Quality Measure"]
        assert result["namingEvidence"]  == ["Healthfirst"]

    def test_absent_subsets_become_empty_lists_not_missing_keys(
            self, mock_render_pages, mock_run_ocr):
        """The backend reads these unconditionally; the contract is always-present."""
        with patch("services.vision_service._get_anthropic_client") as mock_client:
            mock_client.return_value.messages.create.return_value = \
                _make_anthropic_response(_VALID_LLM_RESPONSE)   # has neither key
            from services.classification_service import classify_fax
            result = classify_fax(
                file_path="/fake/path/file.pdf",
                tracking_id="test-evidence-absent",
                sender_fax_number=None,
                received_at=None,
            )
        assert result["routingEvidence"] == []
        assert result["namingEvidence"]  == []

    def test_ragged_subset_is_cleaned_rather_than_failing(
            self, mock_render_pages, mock_run_ocr):
        payload = json.loads(_VALID_LLM_RESPONSE)
        payload["routingEvidence"] = ["  is a HEDIS Quality Measure  ", None, 42, ""]
        with patch("services.vision_service._get_anthropic_client") as mock_client:
            mock_client.return_value.messages.create.return_value = \
                _make_anthropic_response(json.dumps(payload))
            from services.classification_service import classify_fax
            result = classify_fax(
                file_path="/fake/path/file.pdf",
                tracking_id="test-evidence-ragged",
                sender_fax_number=None,
                received_at=None,
            )
        assert result["routingEvidence"] == ["is a HEDIS Quality Measure"]
