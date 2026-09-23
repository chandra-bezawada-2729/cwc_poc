import os
import logging

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

health_bp = Blueprint("health", __name__)


def _tesseract_version() -> str:
    """Return Tesseract version string, or an error message if not found."""
    try:
        import os
        import pytesseract
        cmd = os.getenv("TESSERACT_CMD", "tesseract")
        pytesseract.pytesseract.tesseract_cmd = cmd
        return str(pytesseract.get_tesseract_version())
    except Exception as exc:
        logger.warning(f"[HEALTH] Tesseract version check failed: {exc}")
        return f"unavailable ({exc})"


@health_bp.route("/health", methods=["GET"])
def health():
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    if provider == "anthropic":
        model = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-5")
        extraction_model = (os.getenv("ANTHROPIC_EXTRACTION_MODEL") or "").strip() or model
    else:
        model = os.getenv("OPENAI_MODEL", "gpt-4o")
        extraction_model = (os.getenv("OPENAI_EXTRACTION_MODEL") or "").strip() or model

    return jsonify({
        "status": "UP",
        "service": "cwc-ai-service",
        "version": "0.1.0",
        "provider": provider,
        "model": model,
        "extractionModel": extraction_model,
        "endpoint": (os.getenv("OPENAI_BASE_URL") or "").strip() or ("api.openai.com" if provider == "openai" else "api.anthropic.com"),
        "tesseract_version": _tesseract_version(),
    }), 200
