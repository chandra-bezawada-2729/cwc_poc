"""
POST /extract endpoint — category-specific metadata extraction.
"""
import logging

from flask import Blueprint, jsonify, request

from services import usage_tracker

from services.extraction_service import extract_metadata

logger = logging.getLogger(__name__)

extraction_bp = Blueprint("extraction", __name__)


@extraction_bp.route("/extract", methods=["POST"])
def extract():
    """
    POST /extract
    {
      "file_path"  : "/abs/path/to/file.pdf",
      "tracking_id": "<UUID>",
      "category"   : "MEDICATION_REVIEW"
    }

    Returns the §17.3 extraction JSON:
    { schemaVersion, trackingId, category, core, categoryData, extraction }
    """
    data        = request.get_json(silent=True) or {}
    file_path   = data.get("file_path", "")
    tracking_id = data.get("tracking_id", "unknown")
    category    = data.get("category", "OTHER")

    if not file_path:
        return jsonify({"error": "file_path is required"}), 400

    logger.info(
        f"[EXTRACT] Request tracking_id={tracking_id} category={category}"
    )

    try:
        with usage_tracker.track(tracking_id, "extract", category=category,
                                 fileName=file_path.replace("\\", "/").rsplit("/", 1)[-1]) as usage:
            result = extract_metadata(
                file_path=file_path,
                tracking_id=tracking_id,
                category=category,
            )
        result["usage"] = usage_tracker.summary(usage)
    except Exception as exc:
        logger.error(
            f"[EXTRACT] Unexpected error tracking_id={tracking_id}: {exc}",
            exc_info=True,
        )
        return jsonify({
            "error":       f"Internal error: {exc}",
            "tracking_id": tracking_id,
        }), 500

    logger.info(
        f"[EXTRACT] Done tracking_id={tracking_id} "
        f"category={category} "
        f"latency={result.get('extraction', {}).get('latencyMs')}ms"
    )
    return jsonify(result), 200
