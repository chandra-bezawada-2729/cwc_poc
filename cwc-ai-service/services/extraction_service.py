"""
Metadata extraction pipeline — category-specific second LLM pass.

Loads the per-category prompt from prompts/extraction/{CATEGORY}.md at request time
(hot-editable, no restart needed). Calls Claude vision with page images + OCR text.
Parses and returns the structured extraction JSON.

Adding a new category requires only a new template file — no code changes.
"""
import json
import logging
import re
import time
from pathlib import Path

from services import vision_service
from utils import pdf_utils
from services.ocr_service import run_ocr
from services.extraction_validator import validate_extraction

logger = logging.getLogger(__name__)

_BASE_DIR      = Path(__file__).parent.parent
_PROMPTS_DIR   = _BASE_DIR / "prompts" / "extraction"
_FALLBACK_TPL  = _PROMPTS_DIR / "OTHER.md"

# Which categories have dedicated templates is decided by what is on disk, not by
# a list in this file. A hardcoded set went stale the moment taxonomy v2.0 renamed
# every category, and silently routed every document to OTHER.md.

# Jinja2-style substitutions using the same <<var>> syntax as the classification prompt
_PLACEHOLDER_RE = re.compile(r"<<(\w+)>>")


def extract_metadata(
    file_path: str,
    tracking_id: str,
    category: str,
) -> dict:
    """
    Run the extraction pipeline for one fax.

    Args:
        file_path  : absolute path to the stored PDF
        tracking_id: log correlation ID
        category   : document category code from classification (e.g. MEDICATION_REVIEW)

    Returns the extraction JSON dict (schemaVersion, trackingId, category,
    core, categoryData, extraction).
    Raises RuntimeError on unrecoverable errors.
    """
    start_ms = _now_ms()
    logger.info(
        f"[EXTRACT] start tracking_id={tracking_id} category={category}"
    )

    prompt_text, prompt_version = _load_prompt(category)

    model = vision_service.EXTRACTION_MODEL

    # The tracking id is deliberately NOT written into the prompt: a per-fax value
    # makes every prompt unique and defeats prompt caching. The model echoes this
    # neutral token and _finalise() overwrites trackingId / modelName afterwards.
    rendered_prompt = _PLACEHOLDER_RE.sub(
        lambda m: {
            "tracking_id": "TRACKING_ID",
            "category":    category,
            "model_name":  model,
        }.get(m.group(1), f"<<{m.group(1)}>>"),
        prompt_text,
    )

    # Render pages + OCR (reuse same steps as classification)
    try:
        rendered_pages = pdf_utils.render_pages(file_path, max_pages=3)
    except RuntimeError as exc:
        logger.error(
            f"[EXTRACT] Render failed tracking_id={tracking_id}: {exc}"
        )
        rendered_pages = []

    ocr_result = run_ocr(file_path, rendered_pages)
    ocr_text   = ocr_result["text"]

    # Call LLM
    if rendered_pages:
        raw = vision_service.classify_with_vision(
            rendered_pages, ocr_text, rendered_prompt, model=model
        )
    else:
        raw = vision_service.classify_from_ocr_text(ocr_text, rendered_prompt, model=model)

    latency_ms = _now_ms() - start_ms

    # Parse response
    result = _parse_extraction(raw, tracking_id, category, prompt_version)
    # Authoritative values, whatever the model echoed back.
    result["trackingId"] = tracking_id
    result.setdefault("extraction", {})["modelName"] = model
    result["extraction"]["latencyMs"] = latency_ms

    # Structural validation — adjusts fieldConfidences and adds a validation block
    validation_block, adjusted_confidences = validate_extraction(
        result["extraction"].get("fieldConfidences", {}),
        result.get("core", {}),
        result.get("categoryData", {}),
    )
    result["extraction"]["fieldConfidences"] = adjusted_confidences
    result["extraction"]["validation"] = validation_block

    logger.info(
        f"[EXTRACT] done tracking_id={tracking_id} "
        f"category={category} latency={latency_ms}ms "
        f"prompt_version={prompt_version}"
    )
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_prompt(category: str) -> tuple[str, str]:
    """
    Load the extraction template for `category`. Falls back to OTHER.md.
    Returns (template_body, extraction_prompt_version).
    """
    tpl_path = _PROMPTS_DIR / f"{category.upper()}.md"
    if not tpl_path.exists():
        logger.warning(
            f"[EXTRACT] No template for category={category!r}; "
            f"falling back to OTHER.md"
        )
        tpl_path = _FALLBACK_TPL

    raw = tpl_path.read_text(encoding="utf-8")
    first_line = raw.split("\n", 1)[0].strip()
    m = re.match(r"^EXTRACTION_PROMPT_VERSION:\s*(\S+)", first_line)
    version = m.group(1) if m else "unknown"
    body = raw.split("\n", 1)[1] if m else raw
    return body, version


def _parse_extraction(
    raw: str,
    tracking_id: str,
    category: str,
    prompt_version: str,
) -> dict:
    """
    Parse the raw LLM output into a validated extraction dict.
    Fills in defaults on parse failure so the caller always gets a usable dict.
    """
    if not raw:
        logger.error(
            f"[EXTRACT] Empty LLM response tracking_id={tracking_id}"
        )
        return _empty_result(tracking_id, category, prompt_version)

    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned.strip(), flags=re.MULTILINE).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError as e:
                logger.error(
                    f"[EXTRACT] JSON parse failed tracking_id={tracking_id}: {e}"
                )
                return _empty_result(tracking_id, category, prompt_version)
        else:
            logger.error(
                f"[EXTRACT] No JSON in response tracking_id={tracking_id}"
            )
            return _empty_result(tracking_id, category, prompt_version)

    # Ensure required top-level keys exist
    parsed.setdefault("schemaVersion", "1")
    parsed.setdefault("trackingId",    tracking_id)
    parsed.setdefault("category",      category)
    parsed.setdefault("core",          {})
    parsed.setdefault("categoryData",  {})
    parsed.setdefault("extraction",    {})
    parsed["extraction"].setdefault("promptVersion",    prompt_version)
    parsed["extraction"].setdefault("modelName",        vision_service.EXTRACTION_MODEL)
    parsed["extraction"].setdefault("latencyMs",        0)
    parsed["extraction"].setdefault("fieldConfidences", {})
    parsed["extraction"].setdefault("fieldEvidence",    {})

    return parsed


def _empty_result(
    tracking_id: str, category: str, prompt_version: str
) -> dict:
    return {
        "schemaVersion": "1",
        "trackingId":    tracking_id,
        "category":      category,
        "core":          {},
        "categoryData":  {},
        "extraction": {
            "promptVersion":    prompt_version,
            "modelName":        vision_service.EXTRACTION_MODEL,
            "latencyMs":        0,
            "fieldConfidences": {},
            "fieldEvidence":    {},
            "error":            "EXTRACTION_PARSE_FAILED",
        },
    }


def _now_ms() -> int:
    return int(time.time() * 1000)
