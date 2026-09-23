package ai.avenirdigital.cwc.constants;

public enum ProcessingStatus {
    RECEIVED,
    VALIDATING,
    OCR,
    CLASSIFYING,
    CLASSIFIED,
    ROUTED,
    ERRORED;

    public static ProcessingStatus fromValue(String value) {
        if (value == null || value.isBlank()) {
            return RECEIVED;
        }
        try {
            return valueOf(value.trim().toUpperCase());
        } catch (IllegalArgumentException e) {
            return RECEIVED;
        }
    }
}
