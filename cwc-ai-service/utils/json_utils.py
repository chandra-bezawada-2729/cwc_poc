"""
JSON parsing and schema validation utilities — ported from eMENDER helpers.py parse_output.
Handles fenced JSON, prose-wrapped JSON, and partial/malformed LLM responses.
"""
import json
import re
import logging
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_CATEGORIES_FILE = Path(__file__).parent.parent / "config" / "categories.yml"

# Fallback used only if categories.yml cannot be read. Validation must never be
# the thing that takes the service down, but it must also never silently accept
# a category the routing engine has no folder for.
_FALLBACK_CATEGORIES = {"MISCELLANEOUS", "UNKNOWN"}


@lru_cache(maxsize=1)
def _valid_categories() -> frozenset[str]:
    """
    Category codes are read from config/categories.yml, never hardcoded.

    Before taxonomy v2.0 this was a literal set, which meant adding a category
    required editing validation code as well as the YAML — exactly the coupling
    the spec forbids. It is now derived, and cached for the process lifetime.
    """
    try:
        data = yaml.safe_load(_CATEGORIES_FILE.read_text(encoding="utf-8")) or {}
        codes = {
            c["code"] for c in data.get("categories", []) if c.get("code")
        }
        if codes:
            return frozenset(codes)
        logger.error("[JSON] categories.yml contains no categories; using fallback")
    except Exception as exc:
        logger.error(f"[JSON] Cannot read categories.yml ({exc}); using fallback")
    return frozenset(_FALLBACK_CATEGORIES)


@lru_cache(maxsize=1)
def _valid_subtypes() -> frozenset[str]:
    """Every subtype code across every category, for soft validation."""
    try:
        data = yaml.safe_load(_CATEGORIES_FILE.read_text(encoding="utf-8")) or {}
        out: set[str] = set()
        for c in data.get("categories", []):
            out.update(c.get("subtypes") or [])
        return frozenset(out)
    except Exception:
        return frozenset()


_REQUIRED_FIELDS = {
    "documentCategory", "modelConfidence", "runnerUpCategory",
    "runnerUpConfidence", "reason", "evidence",
    "senderFaxNumberSource", "actionRequired",
    "patientIdentifiersPresent", "pageCount", "containsFillableForm",
    "classificationMode", "ocrCharCount",
    # taxonomy v2.0 — drives the suggested filename
    "specification",
}

# Longest specification we will accept before treating it as a hallucinated
# sentence rather than a filename fragment.
_MAX_SPECIFICATION_CHARS = 60

_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


# ── Deadline parsing helpers (must be defined before _DEADLINE_PATTERNS) ──────

def _parse_iso(m: re.Match) -> date:
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _parse_day_mon_year(m: re.Match) -> date:
    mon = _MONTH_ABBR.get(m.group(2).lower()[:3])
    if not mon:
        raise ValueError(f"Unknown month abbreviation: {m.group(2)!r}")
    return date(int(m.group(3)), mon, int(m.group(1)))


def _parse_mon_day_year(m: re.Match) -> date:
    mon = _MONTH_ABBR.get(m.group(1).lower()[:3])
    if not mon:
        raise ValueError(f"Unknown month name: {m.group(1)!r}")
    return date(int(m.group(3)), mon, int(m.group(2)))


def _parse_slash(m: re.Match) -> date:
    return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))


_DEADLINE_PATTERNS: list[tuple[re.Pattern, callable]] = [
    # 2026-09-01
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"), _parse_iso),
    # 01SEP2026 or 1SEP2026
    (re.compile(r"^(\d{1,2})([A-Za-z]{3})(\d{4})$"), _parse_day_mon_year),
    # September 1, 2026 or Sep 1, 2026
    (re.compile(r"^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$"), _parse_mon_day_year),
    # 09/01/2026
    (re.compile(r"^(\d{2})/(\d{2})/(\d{4})$"), _parse_slash),
]


# ── Public API ────────────────────────────────────────────────────────────────

def parse_llm_json(output: str) -> dict | None:
    """
    Parse a JSON object out of raw LLM output.
    Handles:
      - Markdown code fences (```json ... ```)
      - Stray prose before or after the JSON object
      - Partial / malformed responses (returns None on failure)

    Returns the parsed dict, or None if parsing fails.
    """
    if not output:
        logger.error("[JSON] Empty LLM output — nothing to parse")
        return None

    # Strip markdown fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", output.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned.strip(), flags=re.MULTILINE).strip()

    # Attempt full parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Extract first {...} block
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError as e:
            logger.error(f"[JSON] Failed to parse extracted JSON block: {e}")
            return None

    logger.error("[JSON] No JSON object found in LLM output")
    return None


def validate_schema(parsed: dict | None) -> list[str]:
    """
    Validate the parsed classification result against the §4.4 schema.

    Returns a list of human-readable error strings.
    An empty list means the schema is valid.
    """
    if parsed is None:
        return ["Response is not parseable JSON"]

    errors: list[str] = []

    # Required fields present?
    for field in _REQUIRED_FIELDS:
        if field not in parsed:
            errors.append(f"Missing required field: {field!r}")

    valid = _valid_categories()

    # Category valid?
    cat = parsed.get("documentCategory")
    if cat and cat not in valid:
        errors.append(f"Unknown documentCategory: {cat!r}")

    # Runner-up valid?
    runner = parsed.get("runnerUpCategory")
    if runner and runner not in valid:
        errors.append(f"Unknown runnerUpCategory: {runner!r}")

    # Subtype is soft-validated: an unrecognised subtype degrades the filename
    # but must not send an otherwise-good classification to manual review.
    subtype = parsed.get("documentSubtype")
    known_subtypes = _valid_subtypes()
    if subtype and known_subtypes and subtype not in known_subtypes:
        logger.warning(f"[JSON] Unrecognised documentSubtype: {subtype!r}")

    # specification drives the suggested filename — it must be a short phrase,
    # not a sentence, and must not carry an extension or a path.
    spec = parsed.get("specification")
    if spec is not None:
        if not isinstance(spec, str):
            errors.append(f"'specification' must be a string; got {type(spec).__name__}")
        elif len(spec) > _MAX_SPECIFICATION_CHARS:
            errors.append(
                f"'specification' is {len(spec)} chars; expected a short noun "
                f"phrase under {_MAX_SPECIFICATION_CHARS}"
            )
        elif re.search(r"[\\/]|\.(pdf|tif|tiff|png|jpe?g)$", spec, re.IGNORECASE):
            errors.append(
                f"'specification' must not contain a path or file extension; got {spec!r}"
            )

    # Confidence in [0, 1]?
    for key in ("modelConfidence", "runnerUpConfidence"):
        val = parsed.get(key)
        if val is not None and not (isinstance(val, (int, float)) and 0.0 <= val <= 1.0):
            errors.append(f"{key!r} must be a float in [0.0, 1.0]; got {val!r}")

    # evidence must be a list
    ev = parsed.get("evidence")
    if ev is not None and not isinstance(ev, list):
        errors.append(f"'evidence' must be a list; got {type(ev).__name__}")

    # routingEvidence / namingEvidence are display-only curated subsets of
    # evidence (prompt v2.1). They are optional by design: a model that omits
    # them, or that finds nothing worth quoting, must not trigger a repair retry
    # — the UI falls back to the flat evidence list. Only a wrong *type* is an
    # error, because that would break the JSONB column.
    for key in ("routingEvidence", "namingEvidence"):
        val = parsed.get(key)
        if val is None:
            continue
        if not isinstance(val, list):
            errors.append(f"{key!r} must be a list; got {type(val).__name__}")
        # A ragged element inside the list is NOT an error. classify_fax coerces
        # these through _display_evidence, which drops non-strings, so the only
        # thing a repair retry would buy is a second vision call for a field that
        # is already handled and that nothing but the UI reads.

    # patientIdentifiersPresent must be a list
    phi = parsed.get("patientIdentifiersPresent")
    if phi is not None and not isinstance(phi, list):
        errors.append("'patientIdentifiersPresent' must be a list")

    # senderFaxNumberSource values
    src = parsed.get("senderFaxNumberSource")
    if src and src not in ("FILENAME", "DOCUMENT", "NONE"):
        errors.append(f"senderFaxNumberSource must be FILENAME|DOCUMENT|NONE; got {src!r}")

    return errors


def parse_deadline(value) -> date | None:
    """
    Parse a response deadline in any of the common healthcare fax formats.
    Returns a datetime.date or None on failure / null input.

    Formats supported:
      2026-09-01   01SEP2026   September 1, 2026   09/01/2026
    """
    if value is None:
        return None
    s = str(value).strip()
    if s.lower() in ("null", "none", ""):
        return None
    for pattern, converter in _DEADLINE_PATTERNS:
        m = pattern.match(s)
        if m:
            try:
                return converter(m)
            except (ValueError, KeyError):
                continue
    logger.warning(f"[JSON] Could not parse deadline: {s!r}")
    return None
