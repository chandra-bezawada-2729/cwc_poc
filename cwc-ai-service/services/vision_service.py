"""
Vision classification service — calls Claude (or OpenAI) with page images + OCR text.
Provider dispatched via LLM_PROVIDER env var. Temperature=0 for reproducibility.
Retry once on transient errors with exponential backoff.

NOTE on temperature (Anthropic SDK 1.0.0):
  The SDK removed 'temperature' from the typed messages.create() parameters.
  We pass it via extra_body={"temperature": 0} which the SDK forwards verbatim
  into the JSON request body sent to the Anthropic API endpoint.
  The raw API still honours the field; the SDK just no longer wraps it as a kwarg.
  Generation-5 models (claude-sonnet-5, claude-opus-5) reject temperature with a
  400, so request_options() sends it only to 4.x models.
"""
import logging
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

from services import usage_tracker

# Load .env with the same absolute-path + override strategy as server.py so that
# this module works correctly regardless of CWD when imported during testing.
_ENV_PATH = (Path(__file__).parent.parent / ".env").resolve()
load_dotenv(dotenv_path=_ENV_PATH, override=True)

logger = logging.getLogger(__name__)

LLM_PROVIDER    = os.getenv("LLM_PROVIDER", "anthropic").lower()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-5")
# Extraction is a second, separate call. It only reads fields off a document whose
# category is already decided, and it never affects routing, so it can run on a
# cheaper model than classification. Unset means "same model as classification".
ANTHROPIC_EXTRACTION_MODEL = (os.getenv("ANTHROPIC_EXTRACTION_MODEL") or "").strip() or ANTHROPIC_MODEL

# Prompt caching of the static part of the system prompt. The classification prompt
# (instructions + taxonomy, ~5k tokens) is identical for every fax, so from the second
# fax inside the 5-minute cache window it is billed at 10% of the input price. A
# prompt shorter than the model's cache minimum is simply not cached - no error and
# no surcharge. Set CWC_PROMPT_CACHE=0 to turn it off.
PROMPT_CACHE = os.getenv("CWC_PROMPT_CACHE", "1").strip().lower() not in ("0", "false", "no", "off")

# The per-fax part of the classification prompt starts at this section. Everything
# before it is the cacheable prefix; caching needs an exact byte-identical prefix.
# Generation-5 models (claude-sonnet-5, claude-opus-5, ...) differ from the 4.x
# models this service was written against:
#   * a non-default temperature is rejected with HTTP 400, so it is not sent;
#   * adaptive thinking is ON by default and bills as output tokens, so it is
#     turned off to keep the call equivalent to the 4.x behaviour (and cost).
#     CWC_THINKING=adaptive re-enables it.
# 4.x models keep temperature=0 exactly as before.
_MODEL_GEN_RE = re.compile(r"^claude-[a-z]+-(\d+)")
THINKING_MODE = os.getenv("CWC_THINKING", "disabled").strip().lower()


def _is_gen5_plus(model: str) -> bool:
    m = _MODEL_GEN_RE.match(model or "")
    return bool(m) and int(m.group(1)) >= 5


def request_options(model: str) -> dict:
    """extra_body for messages.create, per model generation."""
    if _is_gen5_plus(model):
        return {"thinking": {"type": "adaptive" if THINKING_MODE == "adaptive" else "disabled"}}
    return {"temperature": 0}


def response_text(response) -> str:
    """
    The first text block. With thinking on, content[0] can be a thinking block,
    so reading content[0].text by position would fail.
    """
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) in ("thinking", "redacted_thinking"):
            continue
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text
    raise RuntimeError("LLM response contained no text block")


_DYNAMIC_SECTION_RE = re.compile(r"\n[═=]{10,}\s*\nSENDER CONTEXT")
OPENAI_MODEL    = os.getenv("OPENAI_MODEL", "gpt-4o")
# Any OpenAI-compatible server: a self-hosted Qwen behind llama.cpp, vLLM or SGLang.
# Set it and LLM_PROVIDER=openai sends every call there instead of api.openai.com.
OPENAI_BASE_URL = (os.getenv("OPENAI_BASE_URL") or "").strip() or None
LOCAL_LLM       = OPENAI_BASE_URL is not None
OPENAI_EXTRACTION_MODEL = (os.getenv("OPENAI_EXTRACTION_MODEL") or "").strip() or OPENAI_MODEL
# A local model on a laptop GPU can take minutes on a multi-page fax.
_LLM_TIMEOUT_SECS = float(os.getenv("CWC_LLM_TIMEOUT_SECS", "600"))

# The model the extraction pass uses, whichever provider is active.
EXTRACTION_MODEL = ANTHROPIC_EXTRACTION_MODEL if LLM_PROVIDER == "anthropic" else OPENAI_EXTRACTION_MODEL

_MAX_TOKENS   = 4096
_MAX_RETRIES  = 1        # retry once on transient errors
_BACKOFF_SECS = 2.0

_anthropic_client = None
_openai_client    = None


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        raw_key = os.getenv("ANTHROPIC_API_KEY", "")
        api_key = raw_key.strip().strip('"').strip("'")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is not set. Add it to .env before starting."
            )
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
        logger.info(
            f"[LLM] Anthropic client initialised — classify model: {ANTHROPIC_MODEL}, "
            f"extract model: {ANTHROPIC_EXTRACTION_MODEL}, prompt cache: {PROMPT_CACHE}, "
            f"key_len={len(api_key)}, key_last4={api_key[-4:]!r}"
        )
    return _anthropic_client


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY is not set.")
        _openai_client = OpenAI(
            api_key=api_key.strip().strip('"').strip("'"),
            base_url=OPENAI_BASE_URL,          # None = api.openai.com
            timeout=_LLM_TIMEOUT_SECS,
        )
        logger.info(
            f"[LLM] OpenAI-compatible client initialised — model: {OPENAI_MODEL}, "
            f"extract model: {OPENAI_EXTRACTION_MODEL}, "
            f"endpoint: {OPENAI_BASE_URL or 'api.openai.com'}"
        )
    return _openai_client


# ── Public API ────────────────────────────────────────────────────────────────

def classify_with_vision(
    rendered_pages: list[dict],
    ocr_text: str,
    rendered_prompt: str,
    model: str | None = None,
) -> str:
    """
    Send up to 3 page images + OCR text to the configured LLM.

    Args:
        rendered_pages : output of pdf_utils.render_pages()
        ocr_text       : concatenated OCR output (may be empty for scanned docs)
        rendered_prompt: fully rendered system prompt (taxonomy injected)

    Returns the raw LLM response string.
    Raises RuntimeError after exhausting retries.
    """
    if LLM_PROVIDER == "anthropic":
        return _call_anthropic_vision(rendered_pages, ocr_text, rendered_prompt, model)
    elif LLM_PROVIDER == "openai":
        return _call_openai_vision(rendered_pages, ocr_text, rendered_prompt, model)
    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER={LLM_PROVIDER!r}. "
            "Set LLM_PROVIDER=anthropic or LLM_PROVIDER=openai in .env."
        )


def classify_from_ocr_text(ocr_text: str, rendered_prompt: str, model: str | None = None) -> str:
    """
    OCR-only fallback: classify using text alone (no images).
    Still calls the LLM — no keyword heuristics.
    Returns the raw LLM response string.
    """
    if LLM_PROVIDER == "anthropic":
        return _call_anthropic_text_only(ocr_text, rendered_prompt, model)
    elif LLM_PROVIDER == "openai":
        return _call_openai_text_only(ocr_text, rendered_prompt, model)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER={LLM_PROVIDER!r}")


# ── Anthropic ─────────────────────────────────────────────────────────────────

def system_blocks(rendered_prompt: str):
    """
    The system prompt as Anthropic content blocks, with a cache breakpoint on the
    static prefix. With caching off this is the plain string, exactly as before.
    Two consecutive text blocks are read as one prompt, so splitting changes
    nothing about what the model sees.
    """
    if not PROMPT_CACHE or not rendered_prompt:
        return rendered_prompt
    m = _DYNAMIC_SECTION_RE.search(rendered_prompt)
    static, dynamic = (
        (rendered_prompt[:m.start()], rendered_prompt[m.start():]) if m
        else (rendered_prompt, "")
    )
    blocks = [{"type": "text", "text": static, "cache_control": {"type": "ephemeral"}}]
    if dynamic:
        blocks.append({"type": "text", "text": dynamic})
    return blocks


def _build_anthropic_content(rendered_pages: list[dict], ocr_text: str) -> list[dict]:
    """Build the Anthropic messages content array: images first, then OCR text."""
    content: list[dict] = []

    for page in rendered_pages:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": page["base64_png"],
            },
        })
        content.append({
            "type": "text",
            "text": f"[Page {page['page_num']} — {page['width']}×{page['height']} px]",
        })

    # OCR as a clearly labelled secondary hint
    ocr_label = (
        "=== POSSIBLY NOISY OCR — the images above are authoritative ===\n"
        "Trust OCR wording; do NOT trust OCR digits (dates, codes, numbers).\n\n"
    )
    content.append({
        "type": "text",
        "text": ocr_label + (ocr_text or "(no OCR text extracted)"),
    })

    return content


def _call_anthropic_vision(
    rendered_pages: list[dict], ocr_text: str, rendered_prompt: str,
    model: str | None = None,
) -> str:
    client = _get_anthropic_client()
    model = model or ANTHROPIC_MODEL
    content = _build_anthropic_content(rendered_pages, ocr_text)

    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=_MAX_TOKENS,
                system=system_blocks(rendered_prompt),
                messages=[{"role": "user", "content": content}],
                extra_body=request_options(model),
            )
            usage_tracker.record(response, "vision")
            return response_text(response)
        except Exception as exc:
            if attempt < _MAX_RETRIES and _is_transient(exc):
                logger.warning(
                    f"[LLM] Transient error on attempt {attempt + 1}: {exc}. "
                    f"Retrying in {_BACKOFF_SECS}s..."
                )
                time.sleep(_BACKOFF_SECS)
            else:
                raise RuntimeError(f"Anthropic vision call failed: {exc}") from exc

    raise RuntimeError("Unreachable")


def _call_anthropic_text_only(ocr_text: str, rendered_prompt: str, model: str | None = None) -> str:
    client = _get_anthropic_client()
    model = model or ANTHROPIC_MODEL
    text_content = (
        "=== OCR FALLBACK — no page images available ===\n"
        "Classify from the text below. If text is insufficient, return UNKNOWN.\n\n"
        + (ocr_text or "(no OCR text)")
    )
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=_MAX_TOKENS,
                system=system_blocks(rendered_prompt),
                messages=[{"role": "user", "content": text_content}],
                extra_body=request_options(model),
            )
            usage_tracker.record(response, "text-only")
            return response_text(response)
        except Exception as exc:
            if attempt < _MAX_RETRIES and _is_transient(exc):
                logger.warning(f"[LLM] Transient error (text-only), retrying: {exc}")
                time.sleep(_BACKOFF_SECS)
            else:
                raise RuntimeError(f"Anthropic text-only call failed: {exc}") from exc

    raise RuntimeError("Unreachable")


def _repair_instruction(bad_output: str, errors: list[str]) -> str:
    return (
        "Your previous response did not match the required JSON schema.\n"
        f"Validation errors:\n" + "\n".join(f"  - {e}" for e in errors) + "\n\n"
        "Your previous response was:\n"
        f"{bad_output}\n\n"
        "Return ONLY valid JSON matching the schema exactly. No markdown fences, no prose."
    )


def repair_output(bad_output: str, errors: list[str], rendered_prompt: str) -> str:
    """Schema-repair retry on whichever provider is active."""
    if LLM_PROVIDER == "openai":
        return _repair_openai(bad_output, errors, rendered_prompt)
    return repair_with_anthropic(bad_output, errors, rendered_prompt)


def repair_with_anthropic(bad_output: str, errors: list[str], rendered_prompt: str) -> str:
    """
    Retry once with a repair instruction that includes the bad output and schema errors.
    Used by classification_service after a schema validation failure.
    """
    client = _get_anthropic_client()
    repair_instruction = _repair_instruction(bad_output, errors)
    try:
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system_blocks(rendered_prompt),
            messages=[
                {"role": "user",      "content": "Please classify the document."},
                {"role": "assistant", "content": bad_output},
                {"role": "user",      "content": repair_instruction},
            ],
            extra_body=request_options(ANTHROPIC_MODEL),
        )
        # The repair retry is a whole second call and the easiest cost to miss:
        # it only happens when the first answer failed schema validation, so it
        # never shows up in a happy-path estimate.
        usage_tracker.record(response, "repair")
        return response_text(response)
    except Exception as exc:
        raise RuntimeError(f"Repair call failed: {exc}") from exc


# ── OpenAI-compatible (OpenAI, or a self-hosted Qwen) ─────────────────────────

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _openai_extra(model: str) -> dict:
    """
    Qwen thinks before answering by default: minutes of extra output on a laptop
    GPU, and the reasoning can land in the answer text. Off for local servers.
    Not sent to api.openai.com, which rejects unknown fields.
    """
    if LOCAL_LLM and os.getenv("CWC_LOCAL_THINKING", "0").strip() not in ("1", "true", "on"):
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return {}


def _openai_text(response) -> str:
    text = response.choices[0].message.content or ""
    # Belt and braces: a server without a reasoning parser leaves <think> inline.
    return _THINK_RE.sub("", text).strip()


def _openai_create(messages: list[dict], model: str | None, call: str):
    client = _get_openai_client()
    model = model or OPENAI_MODEL
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.0,
        max_tokens=_MAX_TOKENS,
        extra_body=_openai_extra(model),
    )
    usage_tracker.record(response, call, local=LOCAL_LLM)
    return response


def _repair_openai(bad_output: str, errors: list[str], rendered_prompt: str) -> str:
    try:
        response = _openai_create([
            {"role": "system",    "content": rendered_prompt},
            {"role": "user",      "content": "Please classify the document."},
            {"role": "assistant", "content": bad_output},
            {"role": "user",      "content": _repair_instruction(bad_output, errors)},
        ], None, "repair")
        return _openai_text(response)
    except Exception as exc:
        raise RuntimeError(f"Repair call failed: {exc}") from exc


def _call_openai_vision(
    rendered_pages: list[dict], ocr_text: str, rendered_prompt: str,
    model: str | None = None,
) -> str:
    messages = [{"role": "system", "content": rendered_prompt}]

    user_parts: list[dict] = []
    for page in rendered_pages:
        user_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{page['base64_png']}"},
        })
        user_parts.append({"type": "text", "text": f"[Page {page['page_num']}]"})

    ocr_label = (
        "=== POSSIBLY NOISY OCR — the images above are authoritative ===\n"
        + (ocr_text or "(no OCR text)")
    )
    user_parts.append({"type": "text", "text": ocr_label})
    messages.append({"role": "user", "content": user_parts})

    for attempt in range(_MAX_RETRIES + 1):
        try:
            return _openai_text(_openai_create(messages, model, "vision"))
        except Exception as exc:
            if attempt < _MAX_RETRIES and _is_transient(exc):
                logger.warning(f"[LLM] OpenAI transient error, retrying: {exc}")
                time.sleep(_BACKOFF_SECS)
            else:
                raise RuntimeError(f"OpenAI vision call failed: {exc}") from exc

    raise RuntimeError("Unreachable")


def _call_openai_text_only(ocr_text: str, rendered_prompt: str, model: str | None = None) -> str:
    text_body = (
        "=== OCR FALLBACK — no page images available ===\n"
        + (ocr_text or "(no OCR text)")
    )
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return _openai_text(_openai_create([
                {"role": "system", "content": rendered_prompt},
                {"role": "user",   "content": text_body},
            ], model, "text-only"))
        except Exception as exc:
            if attempt < _MAX_RETRIES and _is_transient(exc):
                logger.warning(f"[LLM] OpenAI text-only transient error, retrying: {exc}")
                time.sleep(_BACKOFF_SECS)
            else:
                raise RuntimeError(f"OpenAI text-only call failed: {exc}") from exc

    raise RuntimeError("Unreachable")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_transient(exc: Exception) -> bool:
    """Return True if the error is likely transient (rate-limit, server error)."""
    msg = str(exc).lower()
    transient_markers = ("rate_limit", "overloaded", "529", "503", "timeout", "connection")
    return any(m in msg for m in transient_markers)
