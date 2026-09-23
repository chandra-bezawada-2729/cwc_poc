"""
PDF and image rendering utilities (Phase 3).

Renders up to min(max_pages, N) pages of a PDF to PNG at 200 DPI using PyMuPDF.
Each image is:
  - capped at 1500 px on the long edge (Anthropic image limits)
  - under 4 MB after base64 encoding (Anthropic per-image payload limit)

Also handles direct image inputs (.png / .jpg / .jpeg / .tif / .tiff) by loading
them straight through Pillow.
"""
import base64
import logging
from io import BytesIO
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

logger = logging.getLogger(__name__)

# 200 DPI render; fitz uses 72 as its base resolution.
_DPI = 200
_SCALE = _DPI / 72
_MAX_LONG_EDGE = 1500
_MAX_BASE64_BYTES = 4 * 1024 * 1024  # 4 MB raw bytes before base64 inflation

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def render_pages(file_path: str, max_pages: int = 5) -> list[dict]:
    """
    Render pages 1 .. min(max_pages, N) to base64-encoded PNG.

    Returns a list of dicts:
        {
            page_num   : int,      # 1-based
            base64_png : str,      # ASCII base64, safe to embed in JSON
            width      : int,      # pixels
            height     : int,      # pixels
            size_bytes : int,      # raw PNG bytes (before base64)
        }

    Raises RuntimeError if the file cannot be opened or no pages can be rendered.
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext in IMAGE_EXTENSIONS:
        return _render_image_file(file_path)
    else:
        return _render_pdf_file(file_path, max_pages)


def _render_pdf_file(file_path: str, max_pages: int) -> list[dict]:
    results: list[dict] = []
    try:
        doc = fitz.open(file_path)
    except Exception as exc:
        raise RuntimeError(f"Cannot open PDF: {file_path!r} — {exc}") from exc

    with doc:
        total = len(doc)
        n = min(max_pages, total)
        logger.info(f"[PDF_UTILS] Rendering {n}/{total} pages of {Path(file_path).name}")
        for i in range(n):
            page = doc[i]
            mat = fitz.Matrix(_SCALE, _SCALE)
            try:
                pix = page.get_pixmap(matrix=mat, alpha=False)
            except Exception as exc:
                logger.warning(f"[PDF_UTILS] Page {i + 1} render failed: {exc}")
                continue
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img = _cap_long_edge(img)
            encoded = _encode_png(img, page_num=i + 1)
            results.append(encoded)
            logger.info(
                f"[PDF_UTILS] page={i + 1} "
                f"size={encoded['width']}x{encoded['height']} "
                f"bytes={encoded['size_bytes']}"
            )

    if not results:
        raise RuntimeError(f"No pages could be rendered from {file_path!r}")
    return results


def _render_image_file(file_path: str) -> list[dict]:
    try:
        img = Image.open(file_path).convert("RGB")
    except Exception as exc:
        raise RuntimeError(f"Cannot open image: {file_path!r} — {exc}") from exc
    img = _cap_long_edge(img)
    encoded = _encode_png(img, page_num=1)
    logger.info(
        f"[PDF_UTILS] image {Path(file_path).name} "
        f"size={encoded['width']}x{encoded['height']} "
        f"bytes={encoded['size_bytes']}"
    )
    return [encoded]


def _cap_long_edge(img: Image.Image) -> Image.Image:
    """Downscale so neither dimension exceeds MAX_LONG_EDGE. Never upscales."""
    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= _MAX_LONG_EDGE:
        return img
    scale = _MAX_LONG_EDGE / long_edge
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    return img.resize((new_w, new_h), Image.LANCZOS)


def _encode_png(img: Image.Image, page_num: int) -> dict:
    """
    Encode a Pillow image to PNG bytes, then base64.
    If the raw PNG bytes exceed 4 MB, reduce the image to 75% of its linear
    dimension and try once more (preserves aspect ratio).
    """
    data = _to_png_bytes(img)
    if len(data) > _MAX_BASE64_BYTES:
        w, h = img.size
        img = img.resize((max(1, int(w * 0.75)), max(1, int(h * 0.75))), Image.LANCZOS)
        data = _to_png_bytes(img)
        logger.info(f"[PDF_UTILS] page={page_num} resized to stay under 4 MB")

    w, h = img.size
    return {
        "page_num": page_num,
        "base64_png": base64.b64encode(data).decode("ascii"),
        "width": w,
        "height": h,
        "size_bytes": len(data),
    }


def _to_png_bytes(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
