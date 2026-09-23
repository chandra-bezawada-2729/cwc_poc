package ai.avenirdigital.cwc.constants;

public enum RoutingStatus {
    SUGGESTED,
    CONFIRMED,
    OVERRIDDEN,
    MANUAL_REVIEW,
    FAILED;

    public static RoutingStatus fromValue(String value) {
        if (value == null || value.isBlank()) {
            return SUGGESTED;
        }
        try {
            return valueOf(value.trim().toUpperCase().replace("-", "_").replace(" ", "_"));
        } catch (IllegalArgumentException e) {
            return SUGGESTED;
        }
    }
}
