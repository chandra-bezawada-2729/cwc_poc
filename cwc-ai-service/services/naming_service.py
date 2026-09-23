"""
Filename suggestion service.

CWC's manual process renames every inbound fax before filing it. An inbound fax
arrives as `(614)321-2042_2026-08-18_1004PM.pdf` and is filed as, for example,
`Cardiology Consult.pdf` under `Consultation Reports/`.

This module turns a classification result into that suggested name. It produces
TWO forms, because CWC's own outbound folder uses house style while the working
convention discussed with them is specification_doctype:

    primary   — CWC / Avenir house style, Title Case With Spaces
                "Allergy Consult.pdf", "MRI Ankle.pdf", "Medical Clearance.pdf"
    alternate — specification_doctype, lowercase snake
                "allergy_consult.pdf", "cardiology_followup.pdf"

Everything here is DETERMINISTIC. The model supplies only `specification` (a
short noun phrase); the templates, slugs and casing rules come from
config/categories.yml, so a new category never requires a code change.

The service SUGGESTS a name. It never touches the filesystem.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)

# Characters illegal in Windows / SharePoint filenames, plus control chars.
_ILLEGAL = r'[\\/:*?"<>|\x00-\x1f]'

# Tokens whose casing must survive title-casing. Keyed by lowercase form.
_CASING_EXCEPTIONS = {
    "us": "US", "ct": "CT", "mri": "MRI", "mr": "MR", "pet": "PET",
    "egd": "EGD", "ercp": "ERCP", "ekg": "EKG", "ecg": "ECG",
    "cbc": "CBC", "bmp": "BMP", "cmp": "CMP", "psa": "PSA", "tsh": "TSH",
    "hiv": "HIV", "roi": "ROI", "phi": "PHI", "hipaa": "HIPAA",
    "fmla": "FMLA", "cdpas": "CDPAS", "cmn": "CMN", "dme": "DME",
    "ruq": "RUQ", "luq": "LUQ", "ls": "LS", "iv": "IV",
    "er": "ER", "ed": "ED", "pa": "PA", "aps": "APS", "ei": "EI",
    "x-ray": "X-ray", "xray": "X-ray", "mammo": "Mammo",
    "nyp": "NYP", "ssa": "SSA", "doh": "DOH", "nys": "NYS",
    "ob": "OB", "gi": "GI", "gu": "GU", "ent": "ENT",
}

# Tokens that must not be split on their hyphen when snake-casing.
# "X-ray" -> "xray", not "x_ray". Everything else keeps the hyphen as a separator
# ("Person-Centered" -> "person_centered"), which reads correctly.
_SNAKE_OVERRIDES = {
    "x-ray": "xray",
    "xray": "xray",
    "e-mail": "email",
    "follow-up": "followup",
    "pre-op": "preop",
    "post-op": "postop",
}

# Maximum length of the stem, so the result stays comfortable in SharePoint.
_MAX_STEM = 80

_FALLBACK_SPECIFICATION = "Unclassified Fax"


# ── Public API ────────────────────────────────────────────────────────────────

def build_suggested_names(
    taxonomy: dict,
    document_category: str,
    document_subtype: str | None,
    specification: str | None,
    original_file_name: str,
    facility: str | None = None,
) -> dict:
    """
    Build the suggested filenames for one classified fax.

    Args:
        taxonomy          : parsed categories.yml
        document_category : category code returned by the classifier
        document_subtype  : subtype code returned by the classifier (may be None)
        specification     : short noun phrase returned by the classifier
        original_file_name: the inbound filename, used only for its extension
        facility          : sending organisation, read off the letterhead by the
                            classifier. Prefixed onto the name when present.
                            Optional by design - see _apply_facility.

    Returns:
        {
          "suggestedFileName":  "Mount Sinai Allergy_Allergy Consult.pdf",
          "alternateFileName":  "mount_sinai_allergy_allergy_consult.pdf",
          "specification":      "Allergy",          # normalised
          "documentTypeSlug":   "consult",
          "suggestedFolder":    "Consultation Reports",
          "namingSource":       "MODEL" | "FALLBACK",
        }

    Never raises — a naming failure must not fail a classification.
    """
    try:
        return _build(
            taxonomy, document_category, document_subtype,
            specification, original_file_name, facility,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(f"[NAMING] Falling back, build failed: {exc}")
        ext = _extension(original_file_name)
        return {
            "suggestedFileName": f"{_FALLBACK_SPECIFICATION}{ext}",
            "alternateFileName": f"unclassified_fax{ext}",
            "specification": None,
            "documentTypeSlug": "unknown",
            "suggestedFolder": "Manual-Review",
            "namingSource": "FALLBACK",
            "facility": None,
        }


def get_category(taxonomy: dict, code: str) -> dict | None:
    """Look up one category block from the parsed taxonomy."""
    for cat in taxonomy.get("categories", []):
        if cat.get("code") == code:
            return cat
    return None


def folder_for_category(taxonomy: dict, code: str) -> str:
    """The outbound folder CWC files this category into."""
    cat = get_category(taxonomy, code)
    return (cat or {}).get("folder", "Manual-Review")


# ── Internals ─────────────────────────────────────────────────────────────────

def _build(
    taxonomy: dict,
    document_category: str,
    document_subtype: str | None,
    specification: str | None,
    original_file_name: str,
    facility: str | None = None,
) -> dict:
    cat = get_category(taxonomy, document_category)
    if cat is None:
        logger.warning(f"[NAMING] Unmapped category {document_category!r}")
        cat = get_category(taxonomy, "UNKNOWN") or {}

    naming = cat.get("naming") or {}
    primary_tpl = naming.get("primary_template", "{specification}")
    alternate_tpl = naming.get("alternate_template", "{specification}_{doctype}")

    spec_raw = (specification or "").strip()
    source = "MODEL"
    if not spec_raw:
        spec_raw = _default_specification(cat)
        source = "FALLBACK"

    spec = _normalise_specification(spec_raw)
    doctype = _doctype_slug(cat, document_subtype)

    primary_stem = _apply_template(primary_tpl, spec, doctype, snake=False)
    alternate_stem = _apply_template(alternate_tpl, spec, doctype, snake=True)

    # The facility is a PREFIX on the house name, not a replacement for it.
    # Leading with the sender groups a day's post by who sent it; keeping the
    # specification after it is what stops two ultrasounds from one imaging
    # centre collapsing into the same name plus a "(2)" suffix - which reads as
    # a filename clash between two different documents rather than what it is.
    facility_clean = _normalise_facility(facility)
    primary_stem = _apply_facility(primary_stem, facility_clean, snake=False)
    alternate_stem = _apply_facility(alternate_stem, facility_clean, snake=True)

    ext = _extension(original_file_name)

    return {
        "suggestedFileName": _sanitise(primary_stem) + ext,
        "alternateFileName": _sanitise(alternate_stem) + ext,
        "specification": spec,
        "documentTypeSlug": doctype,
        "suggestedFolder": cat.get("folder", "Manual-Review"),
        "namingSource": source,
        "facility": facility_clean or None,
    }


def _default_specification(cat: dict) -> str:
    """When the model gave no specification, use the first vocabulary entry."""
    vocab = (cat.get("naming") or {}).get("specification_vocabulary") or []
    if vocab:
        return str(vocab[0])
    return cat.get("label") or _FALLBACK_SPECIFICATION


def _doctype_slug(cat: dict, subtype: str | None) -> str:
    """Subtype slug if the subtype is known, else the category slug."""
    slugs = cat.get("subtype_slugs") or {}
    if subtype and subtype in slugs:
        return str(slugs[subtype])
    return str(cat.get("slug") or "misc")


def _apply_template(template: str, spec: str, doctype: str, snake: bool) -> str:
    """
    Render a naming template.

    Guards against duplicating the trailing noun: template "{specification} Consult"
    with a specification the model already wrote as "Allergy Consult" must not
    become "Allergy Consult Consult".
    """
    if snake:
        spec_part = _snake(spec)
        # If the specification ALREADY says what the doctype slug says, appending
        # the slug just stutters: "Insurance Letter" + insuranceletter would give
        # "insurance_letter_insuranceletter". Compare them with separators removed
        # so "home_care_form" is recognised as already containing "homecareform".
        flat_spec = spec_part.replace("_", "")
        if doctype and (
            flat_spec == doctype
            or flat_spec.endswith(doctype)
            or flat_spec.startswith(doctype)
        ):
            doctype = ""

        stem = template.format(specification=spec_part, doctype=doctype)
        # collapse "allergy_consult_consult" -> "allergy_consult", and tidy the
        # trailing underscore left behind when doctype was suppressed above
        parts = [p for p in stem.split("_") if p]
        deduped: list[str] = []
        for p in parts:
            if not deduped or deduped[-1] != p:
                deduped.append(p)
        return "_".join(deduped)

    stem = template.format(specification=spec, doctype=doctype)
    # collapse a repeated trailing word, case-insensitively
    words = stem.split()
    deduped = []
    for w in words:
        if not deduped or deduped[-1].lower() != w.lower():
            deduped.append(w)
    return " ".join(deduped)


def _normalise_specification(value: str) -> str:
    """
    Title-case a specification while preserving domain casing (US, MRI, X-ray,
    EGD, FMLA...). Strips punctuation the filesystem dislikes and collapses space.
    """
    s = unicodedata.normalize("NFKD", value)
    s = re.sub(r"[_]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .-")
    if not s:
        return _FALLBACK_SPECIFICATION

    out: list[str] = []
    for token in s.split(" "):
        low = token.lower().strip(".,")
        if low in _CASING_EXCEPTIONS:
            out.append(_CASING_EXCEPTIONS[low])
        elif token.isupper() and len(token) <= 5:
            # already an acronym the model chose to shout — keep it
            out.append(token)
        else:
            out.append(token[:1].upper() + token[1:] if token else token)
    return " ".join(out)


_MAX_FACILITY = 38

# Legal-entity suffixes carry no filing information and eat the length budget
# that the clinical part of the name needs.
_ENTITY_SUFFIXES = {
    "pc", "plc", "llc", "llp", "inc", "corp", "co", "ltd", "pa", "pllc", "md",
}


def _normalise_facility(value: str | None) -> str:
    """
    Turn a letterhead organisation into a filename-safe prefix.

    'Vision Care Ophthalmology, PC'   -> 'Vision Care Ophthalmology'
    'NYP-Lower Manhattan Hospital /'  -> 'NYP-Lower Manhattan Hospital'
    'BioReference Health, LLC'        -> 'BioReference Health'

    Returns '' when there is nothing usable, which makes the prefix optional
    rather than producing a name that starts with a separator.
    """
    if not value:
        return ""
    s = unicodedata.normalize("NFKD", str(value))
    s = re.sub(_ILLEGAL, " ", s)
    s = re.sub(r"[,&]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .-/")
    if not s:
        return ""

    tokens = [t for t in s.split(" ") if t]
    while tokens and tokens[-1].lower().strip(".") in _ENTITY_SUFFIXES:
        tokens.pop()
    if not tokens:
        return ""

    s = " ".join(tokens)
    if len(s) > _MAX_FACILITY:
        # Trim on a word boundary; a name cut mid-word looks like corruption.
        s = s[:_MAX_FACILITY].rsplit(" ", 1)[0].rstrip(" .-") or s[:_MAX_FACILITY]
    return s


def _apply_facility(stem: str, facility: str, snake: bool) -> str:
    """
    Prefix the facility, or return the stem untouched when there is none.

    The no-facility path matters: it is the one that still reproduces CWC's own
    36 filenames exactly, so a fax whose letterhead could not be read is named
    the way CWC has always named it rather than with a placeholder.
    """
    if not facility:
        return stem
    if snake:
        return f"{_snake(facility)}_{stem}"
    return f"{facility}_{stem}"


def _snake(value: str) -> str:
    """
    'Hematology Oncology' -> 'hematology_oncology'
    'Chest X-ray'         -> 'chest_xray'      (see _SNAKE_OVERRIDES)
    'Person-Centered ...' -> 'person_centered_...'
    """
    tokens = []
    for token in value.lower().split():
        tokens.append(_SNAKE_OVERRIDES.get(token.strip(".,"), token))
    s = " ".join(tokens)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_") or "unclassified"


def _sanitise(stem: str) -> str:
    """Strip filesystem-illegal characters and trim to a sane length."""
    s = re.sub(_ILLEGAL, "", stem)
    s = re.sub(r"\s+", " ", s).strip(" .")
    if len(s) > _MAX_STEM:
        s = s[:_MAX_STEM].rstrip(" _-")
    return s or _FALLBACK_SPECIFICATION


def _extension(original_file_name: str) -> str:
    ext = Path(original_file_name or "").suffix.lower()
    return ext if ext else ".pdf"
