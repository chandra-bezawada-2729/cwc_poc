"""
PHI redaction utilities.
Used to scrub patient identifiers from text before any logging.
Gate: only called when cwc.debug.log-ocr-text=true (default false).
Fully implemented in Phase 4.
"""
import re


# Patterns that look like common PHI identifiers.
# These are intentionally broad (false positives are acceptable for a redactor).
_PATTERNS = [
    (re.compile(r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b"), "[SSN]"),                          # SSN
    (re.compile(r"\b(DOB|Date of Birth|D\.O\.B\.?)\s*[:\-]?\s*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}", re.I), "[DOB]"),
    (re.compile(r"\b\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b"), "[DATE]"),                       # dates
    (re.compile(r"\b(MRN|Member ID|Account\s*#)\s*[:\-]?\s*[A-Z0-9\-]+", re.I), "[ID]"),  # MRN/Member ID
    (re.compile(r"\b\d{10,}\b"), "[ID]"),                                                   # long numeric IDs
]


def redact(text: str) -> str:
    """Replace recognisable PHI patterns with placeholder tokens."""
    if not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
