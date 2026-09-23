-- V6 — Inbound folder automation: the ingest ledger
--
-- PROBLEM THIS SOLVES
-- "Which faxes are yet to be processed?" cannot be answered by looking at the
-- folder, and cannot be answered from process memory (the previous
-- FolderWatcherService kept an in-JVM Set that died on every restart, and seeded
-- itself by SKIPPING everything already present — the opposite of what is wanted).
--
-- The ledger makes it a fact in the database.
--
-- IDENTITY IS THE CONTENT HASH, NOT THE PATH
-- content_sha256 is UNIQUE. That single constraint gives us, for free:
--   · re-dropping the same fax  -> rejected, already processed
--   · OneDrive re-syncing a file -> rejected, same bytes
--   · a fax renamed in place     -> rejected, same bytes
--   · two different faxes that happen to share a filename -> both processed
--   · two app instances racing   -> the loser's INSERT fails, no double-filing
-- A path- or filename-based key gets every one of those cases wrong.

CREATE TABLE IF NOT EXISTS cwc.ingest_ledger (
    id                  BIGSERIAL PRIMARY KEY,

    -- ── Identity ─────────────────────────────────────────────────────────────
    -- Hex SHA-256 of the file's bytes. The idempotency key for the whole system.
    content_sha256      CHAR(64)     NOT NULL,

    -- ── Where it came from ───────────────────────────────────────────────────
    -- Absolute path at discovery time. Informational and for traceability only:
    -- never used to decide whether a file is new.
    source_path         TEXT         NOT NULL,
    source_file_name    TEXT         NOT NULL,
    source_size_bytes   BIGINT,
    -- Last-modified as seen on disk. Used ONLY for the stability comparison
    -- between two polls. OneDrive preserves the ORIGINAL mtime when it syncs, so
    -- a file that arrived today can carry last week's timestamp — which is
    -- exactly why mtime must never imply "new".
    source_mtime_ms     BIGINT,

    -- ── State machine ────────────────────────────────────────────────────────
    -- DISCOVERED          seen on disk, nothing done yet
    -- AWAITING_HYDRATION  OneDrive placeholder; bytes not local yet
    -- UNSTABLE            still being written or synced; size/mtime moving
    -- INGESTED            copied into storage/incoming, fax_documents row created
    -- PROCESSING          handed to the classification pipeline
    -- FILED               written to the outbound tree and archived. Terminal, success.
    -- FAILED              recoverable error; will be retried
    -- QUARANTINED         exhausted max attempts; original moved to _failed/. Terminal.
    -- SKIPPED_DUPLICATE   these exact bytes were already filed under another name
    state               TEXT         NOT NULL DEFAULT 'DISCOVERED',

    -- ── Pipeline linkage ─────────────────────────────────────────────────────
    fax_document_id     BIGINT       REFERENCES cwc.fax_documents(id) ON DELETE SET NULL,

    -- ── Outcome ──────────────────────────────────────────────────────────────
    routed_folder       TEXT,
    routed_file_name    TEXT,
    routed_path         TEXT,
    -- Where the ORIGINAL was moved after successful filing (inbound/_processed/...).
    -- The rename is lossy; this is how a filed document is traced back to the
    -- file that actually arrived.
    archived_path       TEXT,

    -- ── Retry bookkeeping ────────────────────────────────────────────────────
    attempts            INT          NOT NULL DEFAULT 0,
    last_error          TEXT,
    -- Set when a transient failure should not be retried until this time.
    -- Backoff lives in the database so it survives a restart.
    next_attempt_at     TIMESTAMPTZ,

    -- ── Timing ───────────────────────────────────────────────────────────────
    first_seen_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,

    CONSTRAINT uq_ingest_ledger_sha UNIQUE (content_sha256),
    CONSTRAINT ck_ingest_ledger_state CHECK (state IN (
        'DISCOVERED', 'AWAITING_HYDRATION', 'UNSTABLE', 'INGESTED',
        'PROCESSING', 'FILED', 'FAILED', 'QUARANTINED', 'SKIPPED_DUPLICATE'
    ))
);

COMMENT ON TABLE cwc.ingest_ledger IS
    'One row per distinct set of file bytes discovered in the inbound folder. content_sha256 is the idempotency key for the whole ingestion pipeline.';
COMMENT ON COLUMN cwc.ingest_ledger.content_sha256 IS
    'Hex SHA-256 of the file contents. UNIQUE — this is what makes re-drops, re-syncs and renames safe.';
COMMENT ON COLUMN cwc.ingest_ledger.source_mtime_ms IS
    'Used only for the two-poll stability comparison. Never used to infer novelty: OneDrive preserves the source mtime, so a newly-synced file can carry an old timestamp.';
COMMENT ON COLUMN cwc.ingest_ledger.archived_path IS
    'Where the original was moved after successful filing. The only link back from a renamed outbound file to the fax that arrived.';

-- The scanner's hot query: "what is not finished yet, and is due for another try?"
CREATE INDEX IF NOT EXISTS idx_ledger_state_due
    ON cwc.ingest_ledger (state, next_attempt_at);

-- Status endpoint and the backlog count.
CREATE INDEX IF NOT EXISTS idx_ledger_first_seen
    ON cwc.ingest_ledger (first_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_ledger_fax_doc
    ON cwc.ingest_ledger (fax_document_id);

-- ── fax_documents: record where the file came in from ────────────────────────
-- upload_source already distinguishes MANUAL_UPLOAD / FOLDER_WATCH / API, but it
-- does not say WHICH folder — and once CWC has more than one inbound source
-- (different fax lines, different practices) that matters for triage.
ALTER TABLE cwc.fax_documents
    ADD COLUMN IF NOT EXISTS source_folder TEXT;

COMMENT ON COLUMN cwc.fax_documents.source_folder IS
    'Inbound root this document was discovered under. Null for manual uploads.';
