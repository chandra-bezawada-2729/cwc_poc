-- Phase 3: classification_results rows are created at OCR time.
-- detected_category and confidence_band are filled only at classification time (Phase 4).
-- Making them nullable avoids a dummy placeholder value in the schema.
ALTER TABLE cwc.classification_results ALTER COLUMN detected_category DROP NOT NULL;
ALTER TABLE cwc.classification_results ALTER COLUMN confidence_band   DROP NOT NULL;
