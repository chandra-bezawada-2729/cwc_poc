-- V8 - Distinguish folder-scanned ledger rows from manual uploads.
--
-- The ingest ledger was built for the folder scanner, so every row was assumed
-- to have a real inbound file behind it at source_path. Manual uploads need a
-- ledger row too - it is the only content-hash fingerprint in the system, and
-- without one the same fax could be uploaded twice and filed twice - but such a
-- row must never be picked up by the scanner's retry loop, because the path it
-- names is not an inbound file waiting to be processed.

ALTER TABLE cwc.ingest_ledger
    ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'SCANNER';

ALTER TABLE cwc.ingest_ledger
    DROP CONSTRAINT IF EXISTS ck_ingest_ledger_origin;

ALTER TABLE cwc.ingest_ledger
    ADD CONSTRAINT ck_ingest_ledger_origin CHECK (origin IN ('SCANNER', 'UPLOAD'));

COMMENT ON COLUMN cwc.ingest_ledger.origin IS
    'SCANNER for a file found in the inbound folder, UPLOAD for one posted to /api/faxes/upload. Only SCANNER rows are eligible for the scanner retry loop.';
