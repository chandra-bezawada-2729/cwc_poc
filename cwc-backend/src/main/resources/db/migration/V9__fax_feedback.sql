-- V9 - Reviewer feedback on a processed fax.
--
-- Staff mark each processed fax right or wrong from its detail page. A wrong
-- one carries a written summary of what went wrong, which is the input to
-- changing the routing configuration.
--
-- The AI's own output is copied in at the time feedback is given, rather than
-- joined at read time. Feedback is evidence about a decision that was made on a
-- particular day; if the fax is later reclassified or the routing rules change,
-- the record must still show what was wrong when someone complained about it.

CREATE TABLE IF NOT EXISTS cwc.fax_feedback (
    id                  BIGSERIAL    PRIMARY KEY,
    fax_document_id     BIGINT      NOT NULL REFERENCES cwc.fax_documents (id) ON DELETE CASCADE,
    tracking_id         UUID        NOT NULL,

    verdict             TEXT        NOT NULL,
    summary             TEXT,

    -- Snapshot of what the AI decided, as it stood when the feedback was given.
    original_file_name  TEXT,
    ai_file_name        TEXT,
    ai_folder           TEXT,
    ai_category         TEXT,
    ai_subtype          TEXT,
    ai_confidence       NUMERIC(5,4),
    review_state        TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_fax_feedback_verdict CHECK (verdict IN ('UP', 'DOWN'))
);

-- One current verdict per document. Changing your mind updates the row rather
-- than adding a second, so a count of DOWN rows is a count of problem faxes.
CREATE UNIQUE INDEX IF NOT EXISTS ux_fax_feedback_document
    ON cwc.fax_feedback (fax_document_id);

CREATE INDEX IF NOT EXISTS ix_fax_feedback_verdict_created
    ON cwc.fax_feedback (verdict, created_at DESC);

COMMENT ON TABLE cwc.fax_feedback IS
    'Reviewer verdict on a processed fax. DOWN rows carry a summary describing what went wrong; they drive routing configuration changes.';
COMMENT ON COLUMN cwc.fax_feedback.summary IS
    'Free text from the reviewer. May describe document content, so treat as PHI: never log it.';
COMMENT ON COLUMN cwc.fax_feedback.ai_file_name IS
    'The name the AI gave the document, copied at feedback time so the record survives a later reclassification.';
