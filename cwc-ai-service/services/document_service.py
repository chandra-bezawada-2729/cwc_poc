"""
Orchestrates fax processing: validate → OCR → classify → confidence → return result.
Fully implemented in Phases 3–5.
"""
import logging

logger = logging.getLogger(__name__)


def process_fax(tracking_id: str, file_path: str, options: dict | None = None) -> dict:
    """
    Main processing pipeline for one fax document.
    Returns the §4.4 classification contract as a dict.
    Implemented in Phase 4.
    """
    raise NotImplementedError(f"[PROCESS] Phase 4 not yet implemented for {tracking_id}")
