"""
/classify, /classify-batch, /config/categories, /ocr endpoints.
"""
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Blueprint, jsonify, request

from services import usage_tracker

from services.classification_service import classify_fax, get_categories
from services.ocr_service import run_ocr
from utils.pdf_utils import render_pages

logger = logging.getLogger(__name__)

classify_bp = Blueprint("classify", __name__)

_DEBUG_LOG_OCR_TEXT = os.getenv("CWC_DEBUG_LOG_OCR_TEXT", "false").lower() == "true"
_BATCH_MAX_WORKERS  = int(os.getenv("CLASSIFY_BATCH_WORKERS", "4"))


# ── POST /ocr ─────────────────────────────────────────────────────────────────

@classify_bp.route("/ocr", methods=["POST"])
def ocr():
    """
    POST /ocr  { "file_path": "/abs/path/...", "tracking_id": "..." }
    Independent OCR endpoint for Phase 3 testing.
    """
    data = request.get_json(silent=True) or {}
    file_path   = data.get("file_path", "")
    tracking_id = data.get("tracking_id", "unknown")

    if not file_path:
        return jsonify({"error": "file_path is required"}), 400

    logger.info(f"[OCR] Request: tracking_id={tracking_id}")

    try:
        rendered = render_pages(file_path, max_pages=3)
    except RuntimeError as exc:
        logger.error(f"[OCR] Render failed for tracking_id={tracking_id}: {exc}")
        return jsonify({"error": str(exc)}), 422

    result = run_ocr(file_path, rendered)

    logger.info(
        f"[OCR] tracking_id={tracking_id} "
        f"mode={result['mode']} "
        f"char_count={result['char_count']}"
    )
    return jsonify(result), 200


# ── POST /classify ────────────────────────────────────────────────────────────

@classify_bp.route("/classify", methods=["POST"])
def classify():
    """
    POST /classify
    {
      "tracking_id"       : "...",
      "file_path"         : "/abs/path/...",
      "sender_fax_number" : "+1XXXXXXXXXX",   // optional, already E.164
      "received_at"       : "2026-08-19T...", // optional ISO-8601 UTC
      "original_file_name": "(614)...pdf"     // optional; used for the rename extension
    }

    Returns the §4.4 JSON contract, augmented with calibrated confidence fields
    and the suggested rename (suggestedFileName / alternateFileName /
    suggestedFolder).
    """
    data = request.get_json(silent=True) or {}
    tracking_id        = data.get("tracking_id", "unknown")
    file_path          = data.get("file_path", "")
    sender_fax_number  = data.get("sender_fax_number")
    received_at        = data.get("received_at")
    original_file_name = data.get("original_file_name")

    if not file_path:
        return jsonify({"error": "file_path is required"}), 400

    logger.info(f"[CLASSIFY] Request tracking_id={tracking_id}")

    try:
        with usage_tracker.track(tracking_id, "classify",
                                 fileName=original_file_name or file_path.replace("\\", "/").rsplit("/", 1)[-1]) as usage:
            result = classify_fax(
                file_path=file_path,
                tracking_id=tracking_id,
                sender_fax_number=sender_fax_number,
                received_at=received_at,
                original_file_name=original_file_name,
            )
            usage["category"] = result.get("documentCategory")
        result["usage"] = usage_tracker.summary(usage)
    except Exception as exc:
        logger.error(f"[CLASSIFY] Unexpected error tracking_id={tracking_id}: {exc}", exc_info=True)
        return jsonify({"error": f"Internal error: {exc}", "tracking_id": tracking_id}), 500

    logger.info(
        f"[CLASSIFY] Done tracking_id={tracking_id} "
        f"category={result.get('documentCategory')} "
        f"band={result.get('confidenceBand')} "
        f"chars={result.get('ocrCharCount')}"
    )
    if _DEBUG_LOG_OCR_TEXT:
        # PHI safety gate — default false. OCR text may contain patient names/MRNs.
        from utils.phi_utils import redact
        safe = redact(result.get("ocrText", "") or "")
        logger.debug(f"[DEBUG-OCR] tracking_id={tracking_id} text_redacted={safe[:200]!r}")
    return jsonify(result), 200


# ── POST /classify-batch ──────────────────────────────────────────────────────

@classify_bp.route("/classify-batch", methods=["POST"])
def classify_batch():
    """
    POST /classify-batch  { "items": [{tracking_id, file_path, ...}, ...] }
    Processes items in a bounded thread pool (CLASSIFY_BATCH_WORKERS, default 4).
    Returns a list of results in the same order as the input.
    """
    data  = request.get_json(silent=True) or {}
    items = data.get("items", [])

    if not items:
        return jsonify({"error": "items list is required and must be non-empty"}), 400

    logger.info(f"[BATCH] classify-batch: {len(items)} items, workers={_BATCH_MAX_WORKERS}")

    results: list[dict | None] = [None] * len(items)

    def _process(idx: int, item: dict) -> tuple[int, dict]:
        try:
            r = classify_fax(
                file_path         = item.get("file_path", ""),
                tracking_id       = item.get("tracking_id", f"batch-{idx}"),
                sender_fax_number  = item.get("sender_fax_number"),
                received_at        = item.get("received_at"),
                original_file_name = item.get("original_file_name"),
            )
        except Exception as exc:
            r = {"error": str(exc), "tracking_id": item.get("tracking_id")}
        return idx, r

    with ThreadPoolExecutor(max_workers=_BATCH_MAX_WORKERS) as pool:
        futures = {pool.submit(_process, i, item): i for i, item in enumerate(items)}
        for future in as_completed(futures):
            idx, result = future.result()
            results[idx] = result

    return jsonify({"results": results}), 200


# ── GET /config/categories ────────────────────────────────────────────────────

@classify_bp.route("/config/categories", methods=["GET"])
def categories():
    """Return the live taxonomy loaded from categories.yml."""
    try:
        cats = get_categories()
        return jsonify({"categories": cats}), 200
    except Exception as exc:
        logger.error(f"[CONFIG] Failed to load categories: {exc}")
        return jsonify({"error": str(exc)}), 500
