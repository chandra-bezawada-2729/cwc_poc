"""
Calibrated confidence scoring — spec §5.

Formula (skipped for UNKNOWN):
  calibrated = clamp01(
      0.55 * model_confidence
    + 0.20 * margin_score          # min(1.0, margin / 0.4)
    + 0.15 * evidence_score        # rapidfuzz fuzzy match against OCR text
    + 0.10 * input_quality_score   # render_factor * (0.3 + 0.7 * min(1, chars/400))
  )

Key design rulings (spec §5):

RULING 1 — UNKNOWN short-circuit.
  When documentCategory == UNKNOWN, modelConfidence means "confident this is
  unreadable" — the INVERSE of routing confidence. The formula is skipped and
  calibrated_confidence is set to 0.0 directly.

RULING 2 — Band / forceManualReview are independent outputs.
  confidence_band is derived SOLELY from calibrated_confidence against the
  configured thresholds (HIGH >= 0.85, MEDIUM >= 0.60, else LOW).
  It is NEVER overwritten by a hard override.
  force_manual_review and override_reason are separate flags that can fire at
  any band. Example: PRESCRIPTION reaches HIGH band but forceManualReview=True
  because it is in auto_route_disabled.
"""
import logging
import os

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

AUTO_THRESHOLD   = float(os.getenv("CWC_CONFIDENCE_AUTO_THRESHOLD",   "0.85"))
REVIEW_THRESHOLD = float(os.getenv("CWC_CONFIDENCE_REVIEW_THRESHOLD", "0.60"))

# Categories that must always go to manual review regardless of confidence
_AUTO_ROUTE_DISABLED: set[str] = {"PRESCRIPTION"}

_FUZZY_THRESHOLD = 80   # rapidfuzz partial_ratio minimum for a verified quote
_THIN_OCR_CHARS  = 200  # below this, evidence_score is neutralised to 0.5


def compute_calibrated_confidence(
    model_confidence: float,
    runner_up_confidence: float,
    evidence: list[str],
    ocr_text: str,
    ocr_char_count: int,
    render_ok: bool = True,
    document_category: str = "",
) -> dict:
    """
    Returns:
      {
        calibrated_confidence  : float,
        confidence_band        : "HIGH" | "MEDIUM" | "LOW",
        evidence_score         : float,
        input_quality_score    : float,
        margin_score           : float,
        force_manual_review    : bool,
        override_reason        : str | None,
      }
    """
    margin = model_confidence - runner_up_confidence

    # ── RULING 1: UNKNOWN short-circuit ───────────────────────────────────────
    # "confident this is unreadable" is the INVERSE of routing confidence.
    # Skip the formula entirely; hard-set calibrated_confidence to 0.0.
    if document_category == "UNKNOWN":
        logger.info("[CONF] UNKNOWN category — calibrated set to 0.0 (skip formula)")
        return {
            "calibrated_confidence": 0.0,
            "confidence_band":       "LOW",
            "evidence_score":        0.0,
            "input_quality_score":   0.0,
            "margin_score":          0.0,
            "force_manual_review":   True,
            "override_reason":       "UNKNOWN",
        }

    # ── Component scores ──────────────────────────────────────────────────────

    margin_score = min(1.0, margin / 0.4)

    if ocr_char_count < _THIN_OCR_CHARS:
        # Thin OCR — evidence matching is unreliable; neutralise to 0.5
        evidence_score = 0.5
        logger.info(
            f"[CONF] ocr_char_count={ocr_char_count} < {_THIN_OCR_CHARS}; "
            "evidence_score neutralised to 0.5"
        )
    else:
        evidence_score = _compute_evidence_score(evidence, ocr_text)

    render_factor = 1.0 if render_ok else 0.7
    input_quality_score = max(0.3, min(1.0,
        render_factor * (0.3 + 0.7 * min(1.0, ocr_char_count / 400.0))
    ))

    calibrated = (
        0.55 * model_confidence
      + 0.20 * margin_score
      + 0.15 * evidence_score
      + 0.10 * input_quality_score
    )
    calibrated = max(0.0, min(1.0, calibrated))

    logger.info(
        f"[CONF] model={model_confidence:.3f} margin={margin:.3f} "
        f"margin_score={margin_score:.3f} evidence_score={evidence_score:.3f} "
        f"input_quality={input_quality_score:.3f} calibrated={calibrated:.3f}"
    )

    # ── RULING 2: Band derives SOLELY from calibrated confidence ──────────────
    # Applied before hard overrides so the band reflects actual quality.
    # It is NEVER overwritten afterwards.
    if calibrated >= AUTO_THRESHOLD:
        band = "HIGH"
    elif calibrated >= REVIEW_THRESHOLD:
        band = "MEDIUM"
    else:
        band = "LOW"

    # ── Hard overrides — independent of band ──────────────────────────────────
    force_manual_review: bool = False
    override_reason: str | None = None

    if margin < 0.15:
        force_manual_review = True
        override_reason = "AMBIGUOUS"
    elif evidence_score < 0.34:
        force_manual_review = True
        override_reason = "UNVERIFIED_EVIDENCE"
    elif document_category in _AUTO_ROUTE_DISABLED:
        force_manual_review = True
        override_reason = f"AUTO_ROUTE_DISABLED:{document_category}"

    return {
        "calibrated_confidence": round(calibrated, 3),
        "confidence_band":       band,
        "evidence_score":        round(evidence_score, 3),
        "input_quality_score":   round(input_quality_score, 3),
        "margin_score":          round(margin_score, 3),
        "force_manual_review":   force_manual_review,
        "override_reason":       override_reason,
    }


def _compute_evidence_score(evidence: list[str], ocr_text: str) -> float:
    """
    Fraction of evidence quotes that fuzzy-match (partial_ratio >= 80) the OCR text,
    normalised to [0, 1]. Returns 0.5 on empty evidence (neutral — can't verify or deny).
    """
    if not evidence:
        return 0.5

    ocr_normalised = _normalise(ocr_text)
    verified = sum(
        1 for quote in evidence
        if quote and fuzz.partial_ratio(_normalise(quote), ocr_normalised) >= _FUZZY_THRESHOLD
    )
    score = verified / len(evidence)
    logger.info(
        f"[CONF] Evidence score: {verified}/{len(evidence)} quotes verified "
        f"(score={score:.3f})"
    )
    return score


def _normalise(text: str) -> str:
    """Collapse whitespace and lowercase for fuzzy matching."""
    import re
    return re.sub(r"\s+", " ", text.lower()).strip()
