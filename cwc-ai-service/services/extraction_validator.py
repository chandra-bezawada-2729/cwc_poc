"""
Structural validation for extracted metadata fields (Phase 9 §17.2).

Validates each field according to its declared type:

  NANP      — North American phone/fax: area code and exchange ≠ 0/1 (10 digits)
  LUHN      — NPI: 10 digits with Luhn check over "80840" prefix
  DEA       — DEA number: 2 letters + 7 digits with published checksum
  FORMAT_7  — NCPDP 7-digit pharmacy ID (format-only; exact check-digit algorithm
               for NCPDP IDs is not publicly documented and varies; production
               deployments should obtain the current spec from NCPDP)
  ICD10     — ICD-10-CM format: letter + 2 digits + optional decimal + alphanumeric
  DRUG      — Fuzzy-match against a curated canonical drug name list
  DATE_RANGE— Sanity bounds for dates (deadlines must not be far in the past)
  NONE      — No structural validation possible (free text, org names, etc.)

Each field returns a validation record:
  {
    "passed":  bool,
    "method":  "NANP" | "LUHN" | "DEA" | "FORMAT_7" | "ICD10" |
               "DRUG" | "DATE_RANGE" | "NONE",
    "reason":  str   # human-readable detail
  }

Confidence adjustment rules:
  - Validation passed  → multiply by PASS_FACTOR  (slight uplift, max 1.0)
  - Validation failed  → multiply by FAIL_FACTOR  (heavy penalty)
  - NONE method        → no adjustment
  - Model confidence 0.00 (verified absent) → no adjustment
"""
from __future__ import annotations

import difflib
import logging
import re
from datetime import date, datetime
from typing import Any

log = logging.getLogger(__name__)

# ── Confidence adjustment factors ────────────────────────────────────────────
PASS_FACTOR = 1.05   # modest uplift for structurally-confirmed values
FAIL_FACTOR = 0.20   # strong penalty: a checksum-failing value cannot be trusted

# ── NANP ─────────────────────────────────────────────────────────────────────

_DIGITS_ONLY = re.compile(r"\D")

def _nanp_digits(raw: str) -> str | None:
    """Strip formatting, remove leading country code, return 10 digits or None."""
    d = _DIGITS_ONLY.sub("", raw)
    if d.startswith("1") and len(d) == 11:
        d = d[1:]
    return d if len(d) == 10 else None


def validate_nanp(raw: str) -> dict:
    d = _nanp_digits(raw)
    if d is None:
        return {"passed": False, "method": "NANP",
                "reason": f"expected 10 digits after stripping, got {_DIGITS_ONLY.sub('', raw)!r}"}
    area, exch = int(d[0]), int(d[3])
    if area in (0, 1):
        return {"passed": False, "method": "NANP",
                "reason": f"area code starts with {area} — invalid NANP"}
    if exch in (0, 1):
        return {"passed": False, "method": "NANP",
                "reason": f"exchange starts with {exch} — invalid NANP"}
    return {"passed": True, "method": "NANP", "reason": "ok"}


# ── NPI Luhn ─────────────────────────────────────────────────────────────────

def validate_npi(raw: str) -> dict:
    d = _DIGITS_ONLY.sub("", raw)
    if len(d) != 10:
        return {"passed": False, "method": "LUHN",
                "reason": f"NPI must be 10 digits, got {len(d)}"}
    # Luhn check: prepend "80840" then run standard Luhn on the 15-digit string
    full = "80840" + d
    total = 0
    for i, ch in enumerate(reversed(full)):
        n = int(ch)
        if i % 2 == 1:        # every second digit from the right (1-indexed)
            n *= 2
            if n > 9:
                n -= 9
        total += n
    if total % 10 != 0:
        return {"passed": False, "method": "LUHN",
                "reason": f"NPI Luhn check failed (total={total})"}
    return {"passed": True, "method": "LUHN", "reason": "ok"}


# ── DEA checksum ─────────────────────────────────────────────────────────────

_DEA_RE = re.compile(r"^[A-PR-Z][A-Z9]\d{7}$", re.IGNORECASE)

def validate_dea(raw: str) -> dict:
    s = raw.strip().upper()
    if not _DEA_RE.match(s):
        return {"passed": False, "method": "DEA",
                "reason": f"DEA format invalid: expected 2 letters + 7 digits, got {s!r}"}
    d = [int(c) for c in s[2:]]
    # sum of 1st, 3rd, 5th digits + 2 × sum of 2nd, 4th, 6th digits
    check_sum = (d[0] + d[2] + d[4]) + 2 * (d[1] + d[3] + d[5])
    expected_check = check_sum % 10
    if expected_check != d[6]:
        return {"passed": False, "method": "DEA",
                "reason": f"DEA checksum mismatch: expected {expected_check}, got {d[6]}"}
    return {"passed": True, "method": "DEA", "reason": "ok"}


# ── NCPDP format-only ─────────────────────────────────────────────────────────

def validate_ncpdp(raw: str) -> dict:
    """
    Format-only validation: exactly 7 digits.
    The NCPDP check-digit algorithm for pharmacy IDs is not publicly documented.
    A production deployment should obtain the spec from NCPDP directly.
    """
    d = _DIGITS_ONLY.sub("", raw)
    if len(d) != 7:
        return {"passed": False, "method": "FORMAT_7",
                "reason": f"NCPDP must be 7 digits, got {len(d)} ({raw!r})"}
    return {"passed": True, "method": "FORMAT_7",
            "reason": "7-digit format valid (check-digit algorithm not verified)"}


# ── ICD-10-CM format ──────────────────────────────────────────────────────────

_ICD10_RE = re.compile(r"^[A-Z]\d{2}(\.\w{1,4})?$", re.IGNORECASE)

def validate_icd10(raw: str) -> dict:
    code = raw.strip().upper()
    if not _ICD10_RE.match(code):
        return {"passed": False, "method": "ICD10",
                "reason": f"ICD-10 format invalid: {raw!r} (must be letter+2digits[.chars])"}
    return {"passed": True, "method": "ICD10", "reason": "format ok"}


# ── Drug name fuzzy-match ─────────────────────────────────────────────────────

# Curated canonical list; extend as the corpus grows.
# A production implementation should query the RxNorm REST API (rxnav.nlm.nih.gov)
# or a local RXNCONSO.RRF database for comprehensive coverage.
_CANONICAL_DRUGS: list[str] = [
    "ATORVASTATIN", "ROSUVASTATIN", "PRAVASTATIN", "SIMVASTATIN", "LOVASTATIN",
    "AMLODIPINE", "LISINOPRIL", "LOSARTAN", "COZAAR", "METOPROLOL",
    "METFORMIN", "GLIPIZIDE", "SITAGLIPTIN", "EMPAGLIFLOZIN", "TIRZEPATIDE",
    "MOUNJARO", "OZEMPIC", "SEMAGLUTIDE",
    "LEVOTHYROXINE", "LEVOTHYROXINE SODIUM",
    "GABAPENTIN", "PREGABALIN",
    "QUETIAPINE", "OLANZAPINE", "ARIPIPRAZOLE",
    "SERTRALINE", "FLUOXETINE", "ESCITALOPRAM", "CITALOPRAM",
    "SAVELLA", "MILNACIPRAN",
    "LUBIPROSTONE", "AMITIZA",
    "OYSTER SHELL CALCIUM",
    "CALCIUM CARBONATE",
    "CALCIUM W/D",
    "CALCIUM WITH VITAMIN D",
    "OYSTER SHELL CALCIUM W/D",
    "WARFARIN", "APIXABAN", "RIVAROXABAN", "ELIQUIS", "XARELTO",
    "OMEPRAZOLE", "PANTOPRAZOLE", "ESOMEPRAZOLE",
    "ALBUTEROL", "FLUTICASONE", "BUDESONIDE",
    "AMOXICILLIN", "AZITHROMYCIN", "DOXYCYCLINE",
    "HYDROCODONE", "OXYCODONE", "TRAMADOL",
    "CEPHALEXIN", "CIPROFLOXACIN", "LEVOFLOXACIN",
    "IBUPROFEN", "NAPROXEN", "CELECOXIB",
]
_CANONICAL_UPPER = [d.upper() for d in _CANONICAL_DRUGS]


def validate_drug_name(raw: str) -> dict:
    name = raw.strip().upper()
    if name in _CANONICAL_UPPER:
        return {"passed": True, "method": "DRUG", "reason": "exact match",
                "canonical": name}
    close = difflib.get_close_matches(name, _CANONICAL_UPPER, n=1, cutoff=0.75)
    if close:
        return {"passed": True, "method": "DRUG",
                "reason": f"fuzzy match → {close[0]}",
                "canonical": close[0]}
    return {"passed": False, "method": "DRUG",
            "reason": f"no match in curated list for {raw!r} (may be a new drug)"}


# ── Date sanity ───────────────────────────────────────────────────────────────

_TOO_FAR_FUTURE_YEARS = 3
_STALE_DAYS           = 730   # response deadlines >2 years ago are almost certainly misreads

def validate_date_range(raw: str, field_key: str = "") -> dict:
    try:
        d = date.fromisoformat(raw)
    except (ValueError, TypeError):
        return {"passed": False, "method": "DATE_RANGE",
                "reason": f"not a valid ISO date: {raw!r}"}
    today = date.today()
    key_lower = field_key.lower()
    # Deadlines / due dates must not be years in the past
    if any(k in key_lower for k in ("deadline", "duedate", "responsedeadline")):
        if (today - d).days > _STALE_DAYS:
            return {"passed": False, "method": "DATE_RANGE",
                    "reason": f"response deadline {raw} is >2 years in the past — probable misread"}
    # Nothing should be more than 3 years in the future
    max_future = date(today.year + _TOO_FAR_FUTURE_YEARS, today.month, today.day)
    if d > max_future:
        return {"passed": False, "method": "DATE_RANGE",
                "reason": f"date {raw} is >{_TOO_FAR_FUTURE_YEARS} years in the future"}
    # Dates before year 2000 are suspicious for healthcare faxes
    if d.year < 2000:
        return {"passed": False, "method": "DATE_RANGE",
                "reason": f"date {raw} is before 2000 — probable OCR misread"}
    return {"passed": True, "method": "DATE_RANGE", "reason": "ok"}


# ── Field-type routing ────────────────────────────────────────────────────────

# Regex patterns that map a field key to a validator.
# Keys are matched case-insensitively against the dot-path (e.g. "core.senderFax").
_FIELD_VALIDATORS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"fax$|phone$|phonenumber$", re.I),        "nanp"),
    (re.compile(r"npI$",                    re.I),         "npi"),
    (re.compile(r"dea$",                    re.I),         "dea"),
    (re.compile(r"ncpdpid$|ncpdp$",         re.I),         "ncpdp"),
    (re.compile(r"exclusioncodes$|icd10codes$", re.I),     "icd10_list"),
    (re.compile(r"(requested|drug).*name$", re.I),         "drug"),
    (re.compile(r"date$|duedate$|deadline$",re.I),         "date"),
]


def _pick_method(field_key: str) -> str:
    for pattern, method in _FIELD_VALIDATORS:
        if pattern.search(field_key):
            return method
    return "none"


# ── Public API ────────────────────────────────────────────────────────────────

def validate_field(field_key: str, value: Any) -> dict:
    """
    Validate a single extracted field.

    Returns a validation record:
      {"passed": bool, "method": str, "reason": str, ...optional extra keys}
    """
    if value is None:
        return {"passed": True, "method": "NONE", "reason": "null — no value to validate"}

    method = _pick_method(field_key)

    if method == "nanp":
        return validate_nanp(str(value))
    if method == "npi":
        return validate_npi(str(value))
    if method == "dea":
        return validate_dea(str(value))
    if method == "ncpdp":
        return validate_ncpdp(str(value))
    if method == "icd10_list":
        # value is expected to be a list of ICD-10 codes
        if isinstance(value, list):
            results = [validate_icd10(str(c)) for c in value]
            all_pass = all(r["passed"] for r in results)
            failures = [f"{c}: {r['reason']}" for c, r in zip(value, results) if not r["passed"]]
            return {"passed": all_pass, "method": "ICD10",
                    "reason": "all valid" if all_pass else f"invalid codes: {failures}"}
        return validate_icd10(str(value))
    if method == "drug":
        return validate_drug_name(str(value))
    if method == "date":
        return validate_date_range(str(value), field_key)
    return {"passed": True, "method": "NONE", "reason": "no structural validation defined"}


def adjust_confidence(model_conf: float, vr: dict) -> float:
    """
    Combine model self-reported confidence with structural validation outcome.
    A value failing its checksum cannot remain high-confidence.
    """
    if model_conf == 0.0:
        return 0.0          # verified absent; don't adjust
    if vr["method"] == "NONE":
        return model_conf   # no structural basis to adjust
    if vr["passed"]:
        return min(1.0, round(model_conf * PASS_FACTOR, 4))
    return round(model_conf * FAIL_FACTOR, 4)


def validate_extraction(
    field_confidences: dict[str, float],
    core: dict,
    category_data: dict,
) -> tuple[dict[str, dict], dict[str, float]]:
    """
    Validate all extracted fields and return:
      - validation_block: {field_key -> validation_record}
      - adjusted_confidences: {field_key -> adjusted_confidence}

    Fields present in core/categoryData but absent from fieldConfidences are
    also validated (using confidence 0.0 = verified absent, no adjustment).
    """
    # Build a flat view of all extracted values
    flat: dict[str, Any] = {}
    for k, v in (core or {}).items():
        flat[f"core.{k}"] = v
    for k, v in (category_data or {}).items():
        flat[f"categoryData.{k}"] = v

    validation_block: dict[str, dict] = {}
    adjusted: dict[str, float] = {}

    # Validate every field that has a model-reported confidence
    for field_key, model_conf in (field_confidences or {}).items():
        value = flat.get(field_key)
        # Skip keys inside nested objects in categoryData (e.g. requestedMedication.*)
        if value is None and "." in field_key.split("categoryData.")[-1]:
            # Try to dig into nested dicts
            parts = field_key.split(".")
            if len(parts) == 3:
                parent = (category_data or {}).get(parts[1])
                if isinstance(parent, dict):
                    value = parent.get(parts[2])

        vr = validate_field(field_key, value)
        validation_block[field_key] = vr
        adjusted[field_key] = adjust_confidence(_as_confidence(model_conf), vr)

    return validation_block, adjusted


def _as_confidence(raw: Any) -> float:
    """
    A model-reported confidence as a float in [0, 1].

    Claude always sends a number. Smaller local models (Qwen) send null for a
    field they did not find, and sometimes a string ("0.9") or a percentage
    (90). null means "not found", which is the same as 0.0 here: verified
    absent, never a crash that throws away every other field.
    """
    if raw is None or isinstance(raw, bool):
        return 0.0
    try:
        v = float(str(raw).strip().rstrip("%"))
    except (TypeError, ValueError):
        return 0.0
    if v > 1.0:            # 90 or "90%" -> 0.90
        v = v / 100.0
    return max(0.0, min(1.0, v))
