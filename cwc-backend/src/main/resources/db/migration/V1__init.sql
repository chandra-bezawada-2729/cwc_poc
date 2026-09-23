-- CWC Healthcare Fax Classification & Routing
-- Flyway migration V1 — initial schema
-- Schema: cwc
-- Do NOT add ddl-auto=update; this file is the single source of schema truth.

CREATE SCHEMA IF NOT EXISTS cwc;

CREATE TABLE cwc.fax_documents (
    id                  BIGSERIAL PRIMARY KEY,
    tracking_id         UUID NOT NULL UNIQUE,
    original_file_name  TEXT NOT NULL,
    stored_path         TEXT NOT NULL,
    file_size_bytes     BIGINT,
    page_count          INT,
    mime_type           TEXT,
    sender_fax_number   TEXT,           -- from filename and/or fax banner
    received_at         TIMESTAMPTZ,    -- parsed from filename when present
    upload_source       TEXT NOT NULL,  -- MANUAL_UPLOAD | FOLDER_WATCH | API
    processing_status   TEXT NOT NULL,  -- RECEIVED|VALIDATING|OCR|CLASSIFYING|CLASSIFIED|ROUTED|ERRORED
    error_reason        TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cwc.classification_results (
    id                      BIGSERIAL PRIMARY KEY,
    fax_document_id         BIGINT NOT NULL REFERENCES cwc.fax_documents(id) ON DELETE CASCADE,
    detected_category       TEXT NOT NULL,
    detected_subtype        TEXT,
    model_confidence        NUMERIC(4,3),
    calibrated_confidence   NUMERIC(4,3),
    confidence_band         TEXT NOT NULL,   -- HIGH | MEDIUM | LOW
    runner_up_category      TEXT,
    runner_up_confidence    NUMERIC(4,3),
    reason                  TEXT,
    evidence                JSONB,           -- array of quoted snippets
    evidence_score          NUMERIC(4,3),
    sender_organization     TEXT,
    action_required         BOOLEAN,
    action_summary          TEXT,
    response_deadline       DATE,
    contains_fillable_form  BOOLEAN,
    phi_identifiers         JSONB,           -- kinds only, never values
    classification_mode     TEXT,            -- VISION | OCR_FALLBACK
    ocr_char_count          INT,
    ocr_text                TEXT,            -- searchable text, PHI-bearing
    model_name              TEXT,
    prompt_version          TEXT,
    latency_ms              INT,
    raw_response            JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cwc.routing_decisions (
    id                 BIGSERIAL PRIMARY KEY,
    fax_document_id    BIGINT NOT NULL REFERENCES cwc.fax_documents(id) ON DELETE CASCADE,
    suggested_folder   TEXT NOT NULL,
    final_folder       TEXT,
    final_path         TEXT,
    routing_status     TEXT NOT NULL,   -- SUGGESTED|CONFIRMED|OVERRIDDEN|MANUAL_REVIEW|FAILED
    decided_by         TEXT,            -- SYSTEM | user identifier
    override_reason    TEXT,
    rule_matched       TEXT,            -- which config rule fired
    decided_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes
CREATE INDEX idx_fax_status  ON cwc.fax_documents(processing_status);
CREATE INDEX idx_fax_created ON cwc.fax_documents(created_at DESC);
CREATE INDEX idx_cls_fax     ON cwc.classification_results(fax_document_id);
CREATE INDEX idx_route_fax   ON cwc.routing_decisions(fax_document_id);
