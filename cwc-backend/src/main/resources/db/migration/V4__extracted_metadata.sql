-- Phase 9: per-fax structured metadata extracted by the category-specific LLM pass.
-- One row per fax document. Re-running extraction upserts (ON CONFLICT DO UPDATE).

CREATE TABLE cwc.extracted_metadata (
    id               BIGSERIAL PRIMARY KEY,
    fax_document_id  BIGINT NOT NULL REFERENCES cwc.fax_documents(id) ON DELETE CASCADE,
    schema_version   TEXT NOT NULL DEFAULT '1',
    core_json        JSONB NOT NULL DEFAULT '{}',
    category_data    JSONB NOT NULL DEFAULT '{}',
    extraction_json  JSONB NOT NULL DEFAULT '{}',
    prompt_version   TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Enforce one extraction row per document
CREATE UNIQUE INDEX idx_extracted_metadata_fax
    ON cwc.extracted_metadata(fax_document_id);

CREATE INDEX idx_extracted_metadata_created
    ON cwc.extracted_metadata(created_at DESC);
