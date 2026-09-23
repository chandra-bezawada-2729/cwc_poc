package ai.avenirdigital.cwc.controller.response;

import ai.avenirdigital.cwc.model.ClassificationResultEntity;
import ai.avenirdigital.cwc.model.FaxDocumentEntity;
import ai.avenirdigital.cwc.model.RoutingDecisionEntity;

import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * Lightweight projection for the paged Inbox list and upload response.
 * Carries summary classification/routing data so the Inbox table can show
 * Detected Type, Confidence, and Suggested Route without fetching full detail.
 *
 * <p>PHI: this projection is rendered in a list view, which is visible over
 * shoulders, in screenshots and in demos. It deliberately carries NO patient
 * identifiers — no name, DOB or MRN — even though extraction now produces them.
 * Those stay on the detail page, which is a deliberate click.
 */
public record FaxSummaryResponse(
        UUID            trackingId,
        String          originalFileName,
        String          senderFaxNumber,
        OffsetDateTime  receivedAt,
        String          processingStatus,
        String          uploadSource,
        Long            fileSizeBytes,
        Integer         pageCount,
        String          mimeType,
        String          errorReason,
        OffsetDateTime  createdAt,
        OffsetDateTime  updatedAt,

        // Classification summary (null until CLASSIFIED)
        String          category,
        String          subtype,
        String          confidenceBand,
        /**
         * 0..1, so the Inbox can show a meter instead of a band word. Every row in
         * a healthy run reads HIGH, at which point the band alone no longer
         * separates 0.82 from 0.97 - and that gap is what decides whether a person
         * opens the document.
         */
        Double          calibratedConfidence,
        Boolean         forceManualReview,

        // ── Sender identity, with provenance ─────────────────────────────────
        // Each value is resolved from the best available source and reports
        // which one it came from, so "we got this from the file name" is visible
        // on screen instead of being an assumption staff have to make.
        String          senderOrganization,
        String          senderOrganizationSource,
        String          senderFaxNumberSource,
        String          destinationFax,
        String          destinationFaxSource,

        // Routing summary (null until routing suggestion computed)
        String          suggestedFolder,
        String          routingStatus,

        // Rename summary — the Inbox needs to show the proposed name next to the
        // proposed folder, because CWC's manual step is one action, not two.
        String          suggestedFileName,
        String          alternateFileName
) {

    /** Where a resolved value came from. Ordered best-first. */
    public static final String SRC_EXTRACTION = "EXTRACTION";
    public static final String SRC_DOCUMENT   = "DOCUMENT";
    public static final String SRC_FILENAME   = "FILENAME";
    public static final String SRC_NONE       = "NONE";

    /**
     * The subset of extracted metadata the Inbox is allowed to see.
     * Deliberately narrow: constructing this from the extraction JSON is the only
     * path by which extracted data reaches the list view, and it has no patient
     * fields to leak.
     */
    public record ExtractedSender(String organization, String fax, String destinationFax) {
        public static final ExtractedSender EMPTY = new ExtractedSender(null, null, null);
    }

    /** Plain doc-only factory — used by the upload response before classification runs. */
    public static FaxSummaryResponse from(FaxDocumentEntity e) {
        return from(e, null, null, ExtractedSender.EMPTY);
    }

    public static FaxSummaryResponse from(
            FaxDocumentEntity e,
            ClassificationResultEntity cls,
            RoutingDecisionEntity rd) {
        return from(e, cls, rd, ExtractedSender.EMPTY);
    }

    public static FaxSummaryResponse from(
            FaxDocumentEntity e,
            ClassificationResultEntity cls,
            RoutingDecisionEntity rd,
            ExtractedSender extracted) {

        String cat = null, sub = null, band = null, sugFolder = null, routeStatus = null;
        String sugName = null, altName = null;
        Boolean forceReview = null;
        Double calibrated = null;
        String clsOrg = null, clsCallbackFax = null;

        if (cls != null) {
            cat         = cls.getDetectedCategory();
            sub         = cls.getDetectedSubtype();
            band        = cls.getConfidenceBand();
            forceReview = cls.getForceManualReview();
            calibrated  = cls.getCalibratedConfidence() != null
                              ? cls.getCalibratedConfidence().doubleValue() : null;
            sugName     = cls.getSuggestedFileName();
            altName     = cls.getAlternateFileName();
            clsOrg         = cls.getSenderOrganization();
            clsCallbackFax = cls.getSenderCallbackFax();
        }
        if (rd != null) {
            sugFolder   = rd.getSuggestedFolder();
            routeStatus = rd.getRoutingStatus();
            // The routing decision is authoritative once it exists: it may carry
            // a collision-suffixed or human-edited name the classification row
            // knows nothing about.
            if (rd.getSuggestedFileName() != null) {
                sugName = rd.getSuggestedFileName();
            }
        }

        ExtractedSender ex = extracted != null ? extracted : ExtractedSender.EMPTY;

        // Organisation: extracted (structured, carries a confidence) beats the
        // classifier's letterhead read. Neither comes from the filename — CWC's
        // inbound names carry a number, never an org.
        String org       = firstPresent(ex.organization(), clsOrg);
        String orgSource = present(ex.organization()) ? SRC_EXTRACTION
                         : present(clsOrg)            ? SRC_DOCUMENT
                         :                              SRC_NONE;

        // Sender fax: the filename parse is the LAST resort, and saying so is the
        // whole point of the tag. A number read off the document is worth more
        // than one inferred from what the fax server happened to name the file.
        String fax       = firstPresent(ex.fax(), clsCallbackFax, e.getSenderFaxNumber());
        String faxSource = present(ex.fax())              ? SRC_EXTRACTION
                         : present(clsCallbackFax)        ? SRC_DOCUMENT
                         : present(e.getSenderFaxNumber()) ? SRC_FILENAME
                         :                                   SRC_NONE;

        // Destination fax — which of CWC's lines the fax arrived on. Only
        // extraction produces it; nothing else in the pipeline knows it.
        String destFax       = ex.destinationFax();
        String destFaxSource = present(destFax) ? SRC_EXTRACTION : SRC_NONE;

        return new FaxSummaryResponse(
                e.getTrackingId(), e.getOriginalFileName(), fax,
                e.getReceivedAt(), e.getProcessingStatus(), e.getUploadSource(),
                e.getFileSizeBytes(), e.getPageCount(), e.getMimeType(), e.getErrorReason(),
                e.getCreatedAt(), e.getUpdatedAt(),
                cat, sub, band, calibrated, forceReview,
                org, orgSource, faxSource, destFax, destFaxSource,
                sugFolder, routeStatus,
                sugName, altName
        );
    }

    private static boolean present(String s) {
        return s != null && !s.isBlank();
    }

    private static String firstPresent(String... candidates) {
        for (String c : candidates) {
            if (present(c)) return c;
        }
        return null;
    }
}
