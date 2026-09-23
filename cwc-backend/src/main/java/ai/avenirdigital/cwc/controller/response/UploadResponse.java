package ai.avenirdigital.cwc.controller.response;

import java.util.List;

/**
 * Returned by POST /api/faxes/upload.
 */
public record UploadResponse(
        List<Accepted> uploaded,
        List<Rejected> failed
) {
    public record Accepted(String trackingId, String originalFileName, String status) {}
    public record Rejected(String originalFileName, String reason) {}
}
