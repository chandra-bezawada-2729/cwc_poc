-- V7 — Split evidence for display; persist sender fax provenance.
-- routing_evidence / naming_evidence are curated display subsets only.
-- evidence column is unchanged (still used for evidence_score).
-- sender_callback_fax / sender_fax_number_source support Inbox provenance tags.

ALTER TABLE cwc.classification_results
    ADD COLUMN IF NOT EXISTS routing_evidence         JSONB,
    ADD COLUMN IF NOT EXISTS naming_evidence          JSONB,
    ADD COLUMN IF NOT EXISTS sender_callback_fax      TEXT,
    ADD COLUMN IF NOT EXISTS sender_fax_number_source TEXT;

COMMENT ON COLUMN cwc.classification_results.evidence IS
    'All supporting quotes from the model. Used for evidence_score; do not narrow or filter.';
COMMENT ON COLUMN cwc.classification_results.routing_evidence IS
    'Display-only quotes justifying the category. Null for rows before classify prompt v2.1.';
COMMENT ON COLUMN cwc.classification_results.naming_evidence IS
    'Display-only quotes justifying the specification. Null for rows before classify prompt v2.1.';
COMMENT ON COLUMN cwc.classification_results.sender_callback_fax IS
    'Return fax read from the document body (not the inbound filename).';
COMMENT ON COLUMN cwc.classification_results.sender_fax_number_source IS
    'FILENAME, DOCUMENT, or NONE — provenance for sender fax shown in the Inbox.';
