package ai.avenirdigital.cwc.constants;

import java.util.Map;

/**
 * Mirrors the category codes in cwc-ai-service/config/categories.yml.
 * Adding a category requires editing YAML + this enum — never the pipeline code.
 *
 * <p>TAXONOMY v2.0 — folder-aligned. Each constant corresponds 1:1 to a folder
 * CWC actually maintains in their outbound tree ("Sample for Avenir Digital").
 *
 * <p>The v1.0 codes (PHARMACY_REQUEST, PRIOR_AUTHORIZATION, MEDICATION_REVIEW,
 * PAYER_CARE_GAP, FOLLOW_UP, MEDICAL_REPORT, PRESCRIPTION, REFERRAL, OTHER) are
 * no longer categories — CWC files every one of those document types into
 * Miscellaneous — but rows classified under v1.0 still exist in the database and
 * in saved evaluation fixtures. {@link #fromValue(String)} therefore maps each
 * legacy code onto its v2.0 destination rather than silently degrading it to
 * UNKNOWN, which would make old rows look like classification failures.
 */
public enum DocumentCategory {

    CONSULTATION_REPORT("CONSULTATION_REPORT"),
    LAB_REPORT("LAB_REPORT"),
    RADIOLOGY_REPORT("RADIOLOGY_REPORT"),
    PROCEDURE_REPORT("PROCEDURE_REPORT"),
    HOSPITAL_RECORD("HOSPITAL_RECORD"),
    HOMECARE("HOMECARE"),
    FORM("FORM"),
    ROI_CONSENT("ROI_CONSENT"),
    MISCELLANEOUS("MISCELLANEOUS"),
    UNKNOWN("UNKNOWN");

    /**
     * v1.0 category code → v2.0 category. Read-only; used only by fromValue so
     * that historic rows and fixtures keep resolving to a sensible folder.
     *
     * MEDICAL_REPORT is deliberately mapped to LAB_REPORT rather than
     * RADIOLOGY_REPORT: under v1.0 it was a single bucket for labs, imaging and
     * pathology, and labs were the majority. Historic rows carrying it should be
     * treated as approximate — the subtype, where present, is the better guide.
     */
    private static final Map<String, DocumentCategory> LEGACY_ALIASES = Map.of(
            "PHARMACY_REQUEST",    MISCELLANEOUS,
            "PRIOR_AUTHORIZATION", MISCELLANEOUS,
            "MEDICATION_REVIEW",   MISCELLANEOUS,
            "PAYER_CARE_GAP",      MISCELLANEOUS,
            "FOLLOW_UP",           MISCELLANEOUS,
            "OTHER",               MISCELLANEOUS,
            "MEDICAL_REPORT",      LAB_REPORT,
            "PRESCRIPTION",        MISCELLANEOUS,
            "REFERRAL",            CONSULTATION_REPORT
    );

    public final String value;

    DocumentCategory(String value) {
        this.value = value;
    }

    public String getValue() {
        return value;
    }

    public static DocumentCategory fromValue(String value) {
        if (value == null || value.isBlank()) {
            return UNKNOWN;
        }
        String normalised = value.trim().toUpperCase().replace("-", "_").replace(" ", "_");
        for (DocumentCategory cat : values()) {
            if (cat.value.equalsIgnoreCase(normalised)) {
                return cat;
            }
        }
        DocumentCategory legacy = LEGACY_ALIASES.get(normalised);
        return legacy != null ? legacy : UNKNOWN;
    }

    /** True when the code is a v1.0 category that no longer exists as a category. */
    public static boolean isLegacyCode(String value) {
        if (value == null) return false;
        return LEGACY_ALIASES.containsKey(
                value.trim().toUpperCase().replace("-", "_").replace(" ", "_"));
    }
}
