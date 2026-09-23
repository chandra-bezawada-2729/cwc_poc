"""
Unit tests for services/confidence_service.py — table-driven, no live calls.
Tests pin exact expected values (within tolerance) to detect formula regressions.
"""
import pytest
from unittest.mock import patch

from services.confidence_service import compute_calibrated_confidence


def _run(
    model_confidence=0.9,
    runner_up_confidence=0.05,
    evidence=None,
    ocr_text="Based on claims data is a HEDIS Quality Measure",
    ocr_char_count=500,
    render_ok=True,
    document_category="PAYER_CARE_GAP",
):
    if evidence is None:
        evidence = ["Based on claims data", "is a HEDIS Quality Measure"]
    return compute_calibrated_confidence(
        model_confidence=model_confidence,
        runner_up_confidence=runner_up_confidence,
        evidence=evidence,
        ocr_text=ocr_text,
        ocr_char_count=ocr_char_count,
        render_ok=render_ok,
        document_category=document_category,
    )


class TestCalibratedConfidenceFormula:
    def test_high_confidence_case(self):
        result = _run(
            model_confidence=0.94,
            runner_up_confidence=0.05,
            ocr_char_count=1832,
        )
        # margin = 0.89 → margin_score = min(1, 0.89/0.4) = 1.0
        # evidence ~1.0 (both quotes fuzzy-match the OCR)
        # input_quality = 1.0 * (0.3 + 0.7 * min(1, 1832/400)) = 1.0
        # calibrated = 0.55*0.94 + 0.20*1.0 + 0.15*ev + 0.10*1.0
        calibrated = result["calibrated_confidence"]
        assert 0.80 <= calibrated <= 1.0, f"Expected high calibrated, got {calibrated}"
        assert result["confidence_band"] == "HIGH"
        assert not result["force_manual_review"]

    def test_low_ocr_chars_neutralises_evidence_score(self):
        result = _run(ocr_char_count=50)
        assert result["evidence_score"] == 0.5  # neutralised
        # no force_manual_review from evidence (score is 0.5, not < 0.34)

    def test_unknown_category_forces_manual_review(self):
        # RULING 1: UNKNOWN skips formula → calibrated=0.0 → LOW band
        result = _run(document_category="UNKNOWN")
        assert result["calibrated_confidence"] == 0.0
        assert result["force_manual_review"]
        assert result["override_reason"] == "UNKNOWN"
        assert result["confidence_band"] == "LOW"

    def test_prescription_forces_manual_review_but_band_reflects_quality(self):
        # RULING 2: Band derives from calibrated confidence, NOT from the override.
        # PRESCRIPTION has high model/margin scores → calibrated ~0.984 → HIGH band.
        # forceManualReview fires independently via AUTO_ROUTE_DISABLED.
        result = _run(document_category="PRESCRIPTION")
        assert result["force_manual_review"]
        assert "PRESCRIPTION" in result["override_reason"]
        # Band is HIGH (quality is high); the human will review before routing
        assert result["confidence_band"] == "HIGH"

    def test_narrow_margin_forces_ambiguous(self):
        result = _run(model_confidence=0.5, runner_up_confidence=0.45)
        # margin = 0.05 < 0.15
        assert result["force_manual_review"]
        assert result["override_reason"] == "AMBIGUOUS"

    def test_unverified_evidence_forces_review(self):
        result = _run(
            evidence=["this phrase does not appear in the fax text at all xyz123"],
            ocr_text="Based on claims data is a HEDIS Quality Measure",
            ocr_char_count=500,
            model_confidence=0.9,
            runner_up_confidence=0.05,
        )
        # The evidence quote does not fuzzy-match the OCR text
        assert result["evidence_score"] < 0.34
        assert result["force_manual_review"]
        assert result["override_reason"] == "UNVERIFIED_EVIDENCE"

    def test_degraded_render_factor(self):
        normal  = _run(render_ok=True, ocr_char_count=400)
        degraded = _run(render_ok=False, ocr_char_count=400)
        assert degraded["calibrated_confidence"] < normal["calibrated_confidence"]

    def test_band_high(self):
        result = _run(model_confidence=0.95, runner_up_confidence=0.02, ocr_char_count=800)
        assert result["confidence_band"] == "HIGH"

    def test_band_low_only_when_calibrated_is_low(self):
        # Band=LOW comes from calibrated<0.60, not from any override.
        # UNKNOWN short-circuits to calibrated=0.0, which naturally gives LOW.
        result = _run(document_category="UNKNOWN")
        assert result["confidence_band"] == "LOW"
        assert result["calibrated_confidence"] == 0.0

    def test_empty_evidence_neutral_score(self):
        result = _run(evidence=[], ocr_char_count=500)
        # Empty evidence → neutralised to 0.5
        assert result["evidence_score"] == 0.5

    def test_zero_ocr_chars_input_quality_floored_at_03(self):
        result = _run(ocr_char_count=0)
        # render_factor=1.0, 0.3 + 0.7*0 = 0.3; clamp [0.3,1.0] → 0.3
        assert abs(result["input_quality_score"] - 0.3) < 0.01

    def test_high_ocr_chars_input_quality_at_10(self):
        result = _run(ocr_char_count=400)
        # render_factor=1.0, 0.3 + 0.7*1.0 = 1.0
        assert abs(result["input_quality_score"] - 1.0) < 0.01


class TestConfidenceEdgeCases:
    def test_calibrated_clamped_at_1(self):
        result = _run(
            model_confidence=1.0, runner_up_confidence=0.0, ocr_char_count=1000
        )
        assert result["calibrated_confidence"] <= 1.0

    def test_calibrated_clamped_at_0(self):
        result = _run(
            model_confidence=0.0, runner_up_confidence=0.0, ocr_char_count=0,
            evidence=["absent phrase xyz"], document_category="PAYER_CARE_GAP"
        )
        assert result["calibrated_confidence"] >= 0.0

    def test_return_keys_present(self):
        result = _run()
        required_keys = {
            "calibrated_confidence", "confidence_band", "evidence_score",
            "input_quality_score", "margin_score", "force_manual_review", "override_reason",
        }
        assert required_keys.issubset(result.keys())
