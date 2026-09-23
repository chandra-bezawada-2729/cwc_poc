-- Phase 5: store the confidence-gate override flags produced by confidence_service.py.
-- force_manual_review and override_reason are independent of confidence_band (spec §5).

ALTER TABLE cwc.classification_results
    ADD COLUMN IF NOT EXISTS force_manual_review boolean,
    ADD COLUMN IF NOT EXISTS override_reason     varchar(80);
