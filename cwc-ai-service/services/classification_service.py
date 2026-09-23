"""
Classification pipeline — orchestrates:
  validate → render pages → OCR → classify (vision or OCR fallback) →
  parse JSON → validate schema (retry once with repair) → compute calibrated confidence

Loads the prompt from disk at request time (hot-editable without restart).
Injects taxonomy from config/categories.yml using Jinja2 with custom delimiters.
"""
import json
import logging
import os
import re
import time
from pathlib import Path

import yaml
from jinja2 import Environment

from services import vision_service, confidence_service, naming_service
from utils import json_utils, pdf_utils
from services.ocr_service import run_ocr

logger = logging.getLogger(__name__)

_BASE_DIR     = Path(__file__).parent.parent
_PROMPT_FILE  = _BASE_DIR / "prompts" / "classification_prompt.md"
_CATEGORIES_FILE = _BASE_DIR / "config" / "categories.yml"

# Pages rendered for the vision call.
# Raised from 3 to 5 in taxonomy v2.0: the CWC corpus routinely puts a fax cover
# sheet on page 1 (sometimes two), so a 3-page window could see only boilerplate
# plus the first page of the real document. Five pages reaches the substantive
# content on every sample in the dataset while keeping the payload modest.
_MAX_RENDER_PAGES = int(os.getenv("CWC_MAX_RENDER_PAGES", "5"))

# Jinja2 env with custom delimiters to avoid conflicts with JSON braces in prompt
_JINJA_ENV = Environment(
    variable_start_string="<<",
    variable_end_string=">>",
    block_start_string="<%",
    block_end_string="%>",
    keep_trailing_newline=True,
)

# OCR char threshold below which vision fallback is UNKNOWN, not OCR-only classify
_MIN_OCR_FOR_FALLBACK = 50


# ── Public API ────────────────────────────────────────────────────────────────

def classify_fax(
    file_path: str,
    tracking_id: str,
    sender_fax_number: str | None = None,
    received_at: str | None = None,
    original_file_name: str | None = None,
) -> dict:
    """
    Full classification pipeline for one fax.

    Args:
        file_path        : absolute path to the stored file
        tracking_id      : used for log correlation
        sender_fax_number: E.164 from filename (already parsed by backend)
        received_at      : ISO-8601 UTC string from filename parsing
        original_file_name: inbound filename, used for the suggested-name extension

    Returns the validated §4.4 JSON contract as a dict, augmented with:
        calibratedConfidence, confidenceBand, evidenceScore, modelName,
        promptVersion, latencyMs, rawResponse, forceManualReview, overrideReason,
        suggestedFileName, alternateFileName, suggestedFolder, documentTypeSlug,
        namingSource
    """
    start_ms = _now_ms()
    logger.info(f"[CLS] classify_fax start tracking_id={tracking_id}")

    # ── 1. Load prompt + taxonomy ─────────────────────────────────────────────
    prompt_text, prompt_version = _load_prompt()
    taxonomy     = _load_taxonomy()
    taxonomy_block = _render_taxonomy_block(taxonomy)

    rendered_prompt = _JINJA_ENV.from_string(prompt_text).render(
        taxonomy_block=taxonomy_block,
        sender_fax_number=sender_fax_number or "unknown",
        received_at=received_at or "unknown",
    )

    # ── 2. Render pages ───────────────────────────────────────────────────────
    render_ok = True
    try:
        rendered_pages = pdf_utils.render_pages(file_path, max_pages=_MAX_RENDER_PAGES)
    except RuntimeError as exc:
        logger.error(f"[CLS] Render failed tracking_id={tracking_id}: {exc}")
        rendered_pages = []
        render_ok = False

    # ── 3. OCR ────────────────────────────────────────────────────────────────
    ocr_result = run_ocr(file_path, rendered_pages)
    ocr_text   = ocr_result["text"]
    ocr_chars  = ocr_result["char_count"]
    logger.info(f"[CLS] OCR done tracking_id={tracking_id} chars={ocr_chars}")

    # ── 4. LLM classification ─────────────────────────────────────────────────
    raw_response: str | None = None
    classification_mode = "VISION"
    vision_failed = False

    if rendered_pages:
        try:
            raw_response = vision_service.classify_with_vision(
                rendered_pages, ocr_text, rendered_prompt
            )
            logger.info(f"[CLS] Vision call succeeded tracking_id={tracking_id}")
        except RuntimeError as exc:
            logger.warning(f"[CLS] Vision call failed tracking_id={tracking_id}: {exc}")
            vision_failed = True
    else:
        vision_failed = True

    if vision_failed:
        if ocr_chars < _MIN_OCR_FOR_FALLBACK:
            latency_ms = _now_ms() - start_ms
            logger.error(
                f"[CLS] Vision unavailable and OCR too thin ({ocr_chars} chars); "
                f"returning UNKNOWN for tracking_id={tracking_id}"
            )
            return _unknown_result(
                tracking_id, ocr_chars, sender_fax_number, rendered_pages,
                prompt_version, latency_ms, taxonomy, original_file_name or file_path,
            )
        logger.info(
            f"[CLS] Falling back to OCR-only classification tracking_id={tracking_id}"
        )
        classification_mode = "OCR_FALLBACK"
        try:
            raw_response = vision_service.classify_from_ocr_text(ocr_text, rendered_prompt)
        except RuntimeError as exc:
            logger.error(f"[CLS] OCR fallback also failed: {exc}")
            return _errored_result(
                tracking_id, "LLM_CALL_FAILED", str(exc),
                ocr_chars, prompt_version, _now_ms() - start_ms,
            )

    # ── 5. Parse JSON ─────────────────────────────────────────────────────────
    parsed = json_utils.parse_llm_json(raw_response)
    errors = json_utils.validate_schema(parsed)

    if errors:
        logger.warning(
            f"[CLS] Schema validation failed tracking_id={tracking_id} "
            f"errors={errors}; attempting repair"
        )
        try:
            repaired_raw = vision_service.repair_output(
                raw_response or "", errors, rendered_prompt
            )
            parsed = json_utils.parse_llm_json(repaired_raw)
            errors = json_utils.validate_schema(parsed)
            if errors:
                logger.error(
                    f"[CLS] Repair failed tracking_id={tracking_id} errors={errors}"
                )
                return _errored_result(
                    tracking_id, "SCHEMA_VALIDATION_FAILED", str(errors),
                    ocr_chars, prompt_version, _now_ms() - start_ms,
                )
            raw_response = repaired_raw
        except RuntimeError as exc:
            return _errored_result(
                tracking_id, "REPAIR_CALL_FAILED", str(exc),
                ocr_chars, prompt_version, _now_ms() - start_ms,
            )

    # ── 6. Calibrated confidence ──────────────────────────────────────────────
    model_conf   = float(parsed.get("modelConfidence", 0.0))
    runner_conf  = float(parsed.get("runnerUpConfidence", 0.0))
    evidence     = parsed.get("evidence") or []
    doc_category = parsed.get("documentCategory", "UNKNOWN")

    conf = confidence_service.compute_calibrated_confidence(
        model_confidence    = model_conf,
        runner_up_confidence= runner_conf,
        evidence            = evidence,
        ocr_text            = ocr_text,
        ocr_char_count      = ocr_chars,
        render_ok           = render_ok,
        document_category   = doc_category,
    )

    latency_ms = _now_ms() - start_ms
    logger.info(
        f"[CLS] Done tracking_id={tracking_id} "
        f"category={doc_category} "
        f"calibrated={conf['calibrated_confidence']:.3f} "
        f"band={conf['confidence_band']} "
        f"latency={latency_ms}ms"
    )

    # ── 7. Assemble result ────────────────────────────────────────────────────
    result = dict(parsed)
    result["classificationMode"]   = classification_mode
    result["ocrCharCount"]         = ocr_chars
    result["calibratedConfidence"] = conf["calibrated_confidence"]
    result["confidenceBand"]       = conf["confidence_band"]
    result["evidenceScore"]        = conf["evidence_score"]
    result["modelName"]            = vision_service.ANTHROPIC_MODEL
    result["promptVersion"]        = prompt_version
    result["latencyMs"]            = latency_ms
    result["rawResponse"]          = raw_response
    result["forceManualReview"]    = conf["force_manual_review"]
    result["overrideReason"]       = conf["override_reason"]

    # Display-only evidence subsets (prompt v2.1). Always present as lists so the
    # backend contract is stable, and never allowed to affect `evidence` — that
    # array feeds evidence_score above and must stay exactly as the model returned it.
    result["routingEvidence"] = _display_evidence(parsed.get("routingEvidence"))
    result["namingEvidence"]  = _display_evidence(parsed.get("namingEvidence"))

    # ── 8. Suggested filename + folder ────────────────────────────────────────
    # CWC renames every fax before filing it. Deterministic, config-driven, and
    # never allowed to fail a classification.
    names = naming_service.build_suggested_names(
        taxonomy           = taxonomy,
        document_category  = doc_category,
        document_subtype   = parsed.get("documentSubtype"),
        specification      = parsed.get("specification"),
        original_file_name = original_file_name or Path(file_path).name,
        # Read off the letterhead during classification, not extraction:
        # extraction runs AFTER filing, so its senderOrganization arrives long
        # after the name has been chosen. The classifier already returns one.
        facility           = parsed.get("senderOrganization"),
    )
    result.update(names)

    logger.info(
        f"[CLS] Naming tracking_id={tracking_id} "
        f"folder={names['suggestedFolder']!r} "
        f"name={names['suggestedFileName']!r} "
        f"alt={names['alternateFileName']!r} "
        f"source={names['namingSource']}"
    )

    return result


def get_categories() -> list[dict]:
    """Return the live taxonomy loaded from categories.yml."""
    data = _load_taxonomy()
    return data.get("categories", [])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _display_evidence(value) -> list[str]:
    """
    Coerce a display evidence array into a clean list of non-empty strings.

    Optional by contract: a missing, null or malformed value yields [] and the UI
    falls back to the flat `evidence` list. Never raises.
    """
    if not isinstance(value, list):
        return []
    return [q.strip() for q in value if isinstance(q, str) and q.strip()]


def _load_prompt() -> tuple[str, str]:
    """Load prompt from disk. Returns (template_text, prompt_version)."""
    raw = _PROMPT_FILE.read_text(encoding="utf-8")
    first_line = raw.split("\n", 1)[0].strip()
    m = re.match(r"^PROMPT_VERSION:\s*(\S+)", first_line)
    version = m.group(1) if m else "unknown"
    # Strip the version header line from the template body
    body = raw.split("\n", 1)[1] if m else raw
    return body, version


def _load_taxonomy() -> dict:
    """Load categories from categories.yml."""
    return yaml.safe_load(_CATEGORIES_FILE.read_text(encoding="utf-8")) or {}


def _render_taxonomy_block(taxonomy: dict) -> str:
    """
    Render the category list into a human-readable block for the prompt.

    v2.0 additionally renders the destination folder and the naming guidance, so
    the model can pick a specification that matches CWC's house style without any
    of that vocabulary being hardcoded in the prompt file.
    """
    lines: list[str] = []
    for cat in taxonomy.get("categories", []):
        lines.append(f"  {cat['code']}: {cat.get('label', cat['code'])}")
        folder = cat.get("folder")
        if folder:
            lines.append(f"    Files into: {folder}/")
        desc = (cat.get("description") or "").strip().replace("\n", " ")
        if desc:
            lines.append(f"    Description: {desc}")
        subtypes = cat.get("subtypes") or []
        if subtypes:
            lines.append(f"    Subtypes: {', '.join(subtypes)}")
        signals = cat.get("evidence_signals") or []
        if signals:
            lines.append("    Evidence signals:")
            for sig in signals:
                lines.append(f"      · {sig}")

        naming = cat.get("naming") or {}
        hint = (naming.get("specification_hint") or "").strip().replace("\n", " ")
        if hint:
            lines.append(f"    Specification: {hint}")
        vocab = naming.get("specification_vocabulary") or []
        if vocab:
            lines.append(f"    Specification vocabulary: {', '.join(vocab)}")
        tpl = naming.get("primary_template")
        if tpl:
            lines.append(f"    Filename template: {tpl}")
        lines.append("")
    return "\n".join(lines)


def _unknown_result(
    tracking_id: str, ocr_chars: int, sender_fax_number: str | None,
    rendered_pages: list[dict], prompt_version: str, latency_ms: int,
    taxonomy: dict | None = None, original_file_name: str = "",
) -> dict:
    names = naming_service.build_suggested_names(
        taxonomy or {}, "UNKNOWN", None, None, original_file_name,
    )
    return {
        **names,
        "documentCategory":          "UNKNOWN",
        "documentSubtype":           None,
        "coverSheetPages":           0,
        "modelConfidence":           0.0,
        "runnerUpCategory":          None,
        "runnerUpConfidence":        0.0,
        "reason":                    "Vision unavailable and OCR text insufficient for classification.",
        "evidence":                  [],
        "routingEvidence":           [],
        "namingEvidence":            [],
        "senderOrganization":        None,
        "senderFaxNumber":           sender_fax_number,
        "senderCallbackFax":         None,
        "senderFaxNumberSource":     "FILENAME" if sender_fax_number else "NONE",
        "actionRequired":            False,
        "actionSummary":             None,
        "responseDeadline":          None,
        "patientIdentifiersPresent": [],
        "pageCount":                 len(rendered_pages),
        "containsFillableForm":      False,
        "classificationMode":        "OCR_FALLBACK",
        "ocrCharCount":              ocr_chars,
        "calibratedConfidence":      0.0,
        "confidenceBand":            "LOW",
        "evidenceScore":             0.0,
        "modelName":                 vision_service.ANTHROPIC_MODEL,
        "promptVersion":             prompt_version,
        "latencyMs":                 latency_ms,
        "rawResponse":               None,
        "forceManualReview":         True,
        "overrideReason":            "UNKNOWN",
        "errorReason":               "VISION_UNAVAILABLE_AND_OCR_TOO_THIN",
    }


def _errored_result(
    tracking_id: str, error_code: str, error_detail: str,
    ocr_chars: int, prompt_version: str, latency_ms: int,
) -> dict:
    return {
        **naming_service.build_suggested_names({}, "UNKNOWN", None, None, ""),
        "documentCategory":          "UNKNOWN",
        "documentSubtype":           None,
        "coverSheetPages":           0,
        "modelConfidence":           0.0,
        "runnerUpCategory":          None,
        "runnerUpConfidence":        0.0,
        "reason":                    f"Classification pipeline error: {error_code}",
        "evidence":                  [],
        "routingEvidence":           [],
        "namingEvidence":            [],
        "senderOrganization":        None,
        "senderFaxNumber":           None,
        "senderCallbackFax":         None,
        "senderFaxNumberSource":     "NONE",
        "actionRequired":            False,
        "actionSummary":             None,
        "responseDeadline":          None,
        "patientIdentifiersPresent": [],
        "pageCount":                 0,
        "containsFillableForm":      False,
        "classificationMode":        "VISION",
        "ocrCharCount":              ocr_chars,
        "calibratedConfidence":      0.0,
        "confidenceBand":            "LOW",
        "evidenceScore":             0.0,
        "modelName":                 vision_service.ANTHROPIC_MODEL,
        "promptVersion":             prompt_version,
        "latencyMs":                 latency_ms,
        "rawResponse":               None,
        "forceManualReview":         True,
        "overrideReason":            "SCHEMA_VALIDATION_FAILED",
        "errorReason":               f"{error_code}: {error_detail}",
    }


def _now_ms() -> int:
    return int(time.time() * 1000)
