"""
OCR service (Phase 3).

Strategy:
  1. Fast path — pdfminer text extraction for born-digital PDFs.
     If it yields >= 30 characters, skip Tesseract entirely.
  2. Tesseract fallback — for scanned/image-only PDFs.
     Preprocessing pipeline applied before OCR:
       a. Convert to greyscale  (eliminates fax colour noise)
       b. Deskew via Tesseract OSD  (handles tilted scans)
       c. Adaptive threshold  (binarises for sharper strokes)
     Char counts are measured WITH and WITHOUT preprocessing so the caller
     can see the quality delta without having to run two separate requests.

Tesseract binary path comes from the TESSERACT_CMD environment variable.
Never hardcode "C:\\Program Files\\Tesseract-OCR".
"""
import base64
import io
import logging
import os
import subprocess

import pytesseract
from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger(__name__)

# ── Tesseract configuration ──────────────────────────────────────────────────

_TESSERACT_CMD = os.getenv("TESSERACT_CMD", "tesseract")
pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD

# --oem 3 = LSTM + legacy (best accuracy); --psm 3 = auto page segmentation
_TESS_CONFIG = "--oem 3 --psm 3"

# Minimum native-text characters required to skip Tesseract
_NATIVE_MIN_CHARS = 30


# ── Public API ────────────────────────────────────────────────────────────────

def run_ocr(file_path: str, rendered_pages: list[dict]) -> dict:
    """
    Run OCR on a document.

    Args:
        file_path:      Absolute path to the source file.
        rendered_pages: Output of pdf_utils.render_pages()
                        (list of {base64_png, width, height, page_num}).

    Returns a dict:
        {
            text                : str,
            char_count          : int,
            mode                : "NATIVE" | "TESSERACT",
            per_page_char_counts: [int, ...],  # chars per page (final text)
            preprocessing_report: [...],        # only present in TESSERACT mode
            tesseract_available : bool,
        }
    """
    tess_ok = _check_tesseract()

    # ── Fast path: pdfminer ──────────────────────────────────────────────────
    native_text = _extract_native(file_path)
    stripped = native_text.strip()
    logger.info(f"[OCR] pdfminer extracted {len(stripped)} chars from {file_path!r}")

    if len(stripped) >= _NATIVE_MIN_CHARS:
        logger.info("[OCR] Using NATIVE (pdfminer) path")
        return {
            "text": native_text,
            "char_count": len(native_text),
            "mode": "NATIVE",
            "per_page_char_counts": [len(native_text)],
            "tesseract_available": tess_ok,
        }

    # ── Tesseract fallback ───────────────────────────────────────────────────
    logger.info(f"[OCR] pdfminer < {_NATIVE_MIN_CHARS} chars; falling back to Tesseract")

    if not tess_ok:
        logger.error("[OCR] Tesseract unavailable — cannot OCR this document")
        return {
            "text": "",
            "char_count": 0,
            "mode": "TESSERACT",
            "per_page_char_counts": [],
            "tesseract_available": False,
        }

    all_text: list[str] = []
    per_page_char_counts: list[int] = []
    preprocessing_report: list[dict] = []

    for page_info in rendered_pages:
        img = _b64_to_pil(page_info["base64_png"])
        page_num = page_info["page_num"]

        # Without preprocessing
        raw_text = _run_tesseract(img)
        raw_chars = len(raw_text.strip())

        # With preprocessing
        proc_img = _preprocess(img)
        proc_text = _run_tesseract(proc_img)
        proc_chars = len(proc_text.strip())

        logger.info(
            f"[OCR] page={page_num} "
            f"raw_chars={raw_chars} "
            f"preprocessed_chars={proc_chars} "
            f"delta={proc_chars - raw_chars:+d}"
        )

        # Use whichever yields more characters (never tune to look better)
        if proc_chars >= raw_chars:
            best_text = proc_text
            winner = "preprocessed"
        else:
            best_text = raw_text
            winner = "raw"

        logger.info(f"[OCR] page={page_num} using {winner} text ({max(raw_chars, proc_chars)} chars)")

        all_text.append(best_text)
        per_page_char_counts.append(len(best_text.strip()))
        preprocessing_report.append({
            "page": page_num,
            "raw_chars": raw_chars,
            "preprocessed_chars": proc_chars,
            "delta": proc_chars - raw_chars,
            "selected": winner,
        })

    combined = "\n\n".join(all_text)
    return {
        "text": combined,
        "char_count": len(combined.strip()),
        "mode": "TESSERACT",
        "per_page_char_counts": per_page_char_counts,
        "preprocessing_report": preprocessing_report,
        "tesseract_available": True,
    }


# ── Preprocessing pipeline ───────────────────────────────────────────────────

def _preprocess(img: Image.Image) -> Image.Image:
    """
    Fax-quality preprocessing:
      1. Greyscale — eliminates colour noise present in some fax renders.
      2. Deskew — corrects tilt via Tesseract OSD; falls back silently.
      3. Adaptive threshold — binarises; sharpens thin strokes vs grey background.
    """
    # 1. Greyscale
    gray = img.convert("L")

    # 2. Deskew via Tesseract OSD (requires enough text; silently skipped on failure)
    gray = _deskew(gray)

    # 3. Adaptive binarisation using local mean (implemented via median filter +
    #    threshold, which approximates Sauvola for fax documents).
    smoothed = gray.filter(ImageFilter.MedianFilter(size=3))
    auto = ImageOps.autocontrast(smoothed, cutoff=2)
    # Convert to binary at midpoint — crisp black/white for Tesseract
    binarized = auto.point(lambda px: 255 if px > 128 else 0, mode="L")

    return binarized


def _deskew(gray: Image.Image) -> Image.Image:
    """
    Detect page rotation using Tesseract OSD and correct it.
    Silently returns the original image if OSD fails or the angle is < 1°.
    """
    try:
        osd = pytesseract.image_to_osd(
            gray,
            output_type=pytesseract.Output.DICT,
            config="--psm 0 -c min_characters_to_try=15",
        )
        angle = osd.get("rotate", 0)
        if abs(angle) >= 1:
            logger.info(f"[OCR] deskew: rotating {angle}°")
            return gray.rotate(-angle, expand=True, fillcolor=255)
    except Exception as exc:
        logger.debug(f"[OCR] OSD failed (skipping deskew): {exc}")
    return gray


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_native(file_path: str) -> str:
    """Extract embedded text with pdfminer. Returns empty string on non-PDF."""
    try:
        from pdfminer.high_level import extract_text as pm_extract
        return pm_extract(file_path) or ""
    except Exception as exc:
        logger.debug(f"[OCR] pdfminer failed ({exc}); falling through to Tesseract")
        return ""


def _run_tesseract(img: Image.Image) -> str:
    """Run pytesseract and return the text string."""
    try:
        return pytesseract.image_to_string(img, config=_TESS_CONFIG)
    except Exception as exc:
        logger.warning(f"[OCR] Tesseract error: {exc}")
        return ""


def _check_tesseract() -> bool:
    """Return True if the Tesseract binary responds to --version."""
    try:
        result = subprocess.run(
            [_TESSERACT_CMD, "--version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _b64_to_pil(b64_str: str) -> Image.Image:
    data = base64.b64decode(b64_str)
    return Image.open(io.BytesIO(data)).convert("RGB")
