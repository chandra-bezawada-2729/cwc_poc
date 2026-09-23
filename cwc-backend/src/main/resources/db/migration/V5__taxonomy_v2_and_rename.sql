-- V5 — Taxonomy v2.0 (folder-aligned) + suggested rename
--
-- Two changes ship together because they are the same feature: CWC's manual
-- process decides a FOLDER and a NAME in one judgement, so the POC must record
-- both.
--
-- 1. classification_results gains the naming fields produced by the AI service.
-- 2. routing_decisions gains the filename half of the routing decision, plus the
--    original inbound filename so a renamed file stays traceable.
--
-- Existing rows are NOT rewritten. Rows classified under taxonomy v1.0 keep
-- their old detected_category values; DocumentCategory.fromValue maps those
-- legacy codes onto v2.0 categories at read time. A destructive UPDATE was
-- considered and rejected: the v1.0 labels are the historical record of what the
-- POC actually predicted, and overwriting them would corrupt the only
-- before/after comparison available for the CWC demo.

-- ── classification_results ───────────────────────────────────────────────────

ALTER TABLE cwc.classification_results
    ADD COLUMN IF NOT EXISTS specification        TEXT,
    ADD COLUMN IF NOT EXISTS suggested_file_name  TEXT,
    ADD COLUMN IF NOT EXISTS alternate_file_name  TEXT,
    ADD COLUMN IF NOT EXISTS document_type_slug   TEXT,
    ADD COLUMN IF NOT EXISTS naming_source        TEXT,
    ADD COLUMN IF NOT EXISTS cover_sheet_pages    INT;

COMMENT ON COLUMN cwc.classification_results.specification IS
    'Short noun phrase describing the document (specialty, modality + body part, procedure or form name). Drives the suggested filename. Must never contain a patient identifier.';
COMMENT ON COLUMN cwc.classification_results.suggested_file_name IS
    'CWC house style, e.g. "US Abdomen.pdf".';
COMMENT ON COLUMN cwc.classification_results.alternate_file_name IS
    'specification_doctype style, e.g. "us_abdomen_radiologyreport.pdf".';
COMMENT ON COLUMN cwc.classification_results.naming_source IS
    'MODEL when the classifier supplied a specification; FALLBACK when it was defaulted from the taxonomy.';

-- ── routing_decisions ────────────────────────────────────────────────────────

ALTER TABLE cwc.routing_decisions
    ADD COLUMN IF NOT EXISTS original_file_name    TEXT,
    ADD COLUMN IF NOT EXISTS suggested_file_name   TEXT,
    ADD COLUMN IF NOT EXISTS final_file_name       TEXT,
    ADD COLUMN IF NOT EXISTS file_name_overridden  BOOLEAN;

COMMENT ON COLUMN cwc.routing_decisions.original_file_name IS
    'The inbound filename as received, e.g. "(614)321-2042_2026-08-18_1004PM.pdf". The rename is lossy; this keeps a routed file traceable to the fax that arrived.';
COMMENT ON COLUMN cwc.routing_decisions.file_name_overridden IS
    'True when a human changed the suggested NAME. Tracked separately from a folder override — they are different training signals.';

-- Human overrides are the most valuable data this POC produces (spec §6).
-- Index them so the audit export and the accuracy page stay cheap as the
-- corpus grows.
CREATE INDEX IF NOT EXISTS idx_route_name_override
    ON cwc.routing_decisions (file_name_overridden)
    WHERE file_name_overridden IS TRUE;
