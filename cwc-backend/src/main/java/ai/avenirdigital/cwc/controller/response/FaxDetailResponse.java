package ai.avenirdigital.cwc.controller.response;

import ai.avenirdigital.cwc.model.ClassificationResultEntity;
import ai.avenirdigital.cwc.model.FaxDocumentEntity;
import ai.avenirdigital.cwc.model.RoutingDecisionEntity;

import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * Full detail for a single fax, including classification and routing when available.
 * Null for classification/routing fields until each pipeline step completes.
 */
public record FaxDetailResponse(
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

        // ── Classification (null until Phase 4) ──────────────────────────────
        String          category,
        String          subtype,
        Double          confidenceScore,
        String          confidenceBand,
        Boolean         actionRequired,
        String          actionSummary,
        String          responseDeadline,
        String          senderOrganization,
        String          senderCallbackFax,  // return fax read from the document body
        String          senderFaxNumberSource, // FILENAME | DOCUMENT | NONE
        String          evidence,           // JSON array — ALL quotes; feeds evidenceScore
        String          routingEvidence,    // JSON array — display subset: why this folder
        String          namingEvidence,     // JSON array — display subset: why this name

        // Phase 5 confidence-gate flags (independent of band per §5 Ruling 2)
        Boolean         forceManualReview,
        String          overrideReason,

        // ── Rename (taxonomy v2.0) ───────────────────────────────────────────
        String          specification,      // "US Abdomen", "Allergy", "Medical Clearance"
        String          suggestedFileName,  // CWC house style
        String          alternateFileName,  // specification_doctype style
        String          namingSource,       // MODEL | FALLBACK
        Integer         coverSheetPages,    // leading fax cover sheets detected

        // ── Routing (null until Phase 6 suggestion computed) ─────────────────
        String          suggestedFolder,
        String          routingStatus,      // SUGGESTED|CONFIRMED|OVERRIDDEN|MANUAL_REVIEW|FAILED
        String          finalFolder,
        String          finalFileName,
        Boolean         fileNameOverridden,
        String          ruleMatched
) {
    public static FaxDetailResponse from(
            FaxDocumentEntity e,
            ClassificationResultEntity cls,
            RoutingDecisionEntity rd) {

        String deadline = null;
        Double conf     = null;
        String cat      = null, sub = null, band = null, org = null, evidence = null;
        String routingEvidence = null, namingEvidence = null;
        String callbackFax = null;
        String actionSummary = null;
        Boolean actionRequired  = null, forceReview = null;
        String overrideReason   = null;
        String specification = null, suggestedFileName = null, alternateFileName = null;
        String namingSource  = null;
        Integer coverSheetPages = null;

        if (cls != null) {
            deadline      = cls.getResponseDeadline() != null ? cls.getResponseDeadline().toString() : null;
            conf          = cls.getCalibratedConfidence() != null
                                ? cls.getCalibratedConfidence().doubleValue() : null;
            cat           = cls.getDetectedCategory();
            sub           = cls.getDetectedSubtype();
            band          = cls.getConfidenceBand();
            org           = cls.getSenderOrganization();
            callbackFax   = cls.getSenderCallbackFax();
            evidence      = cls.getEvidence();
            routingEvidence = cls.getRoutingEvidence();
            namingEvidence  = cls.getNamingEvidence();
            actionRequired = cls.getActionRequired();
            actionSummary = cls.getActionSummary();
            forceReview   = cls.getForceManualReview();
            overrideReason = cls.getOverrideReason();
            specification     = cls.getSpecification();
            suggestedFileName = cls.getSuggestedFileName();
            alternateFileName = cls.getAlternateFileName();
            namingSource      = cls.getNamingSource();
            coverSheetPages   = cls.getCoverSheetPages();
        }

        // Resolve the sender fax the same way the Inbox does, so the value and its
        // provenance tag always agree. The model's own senderFaxNumberSource
        // describes where IT read a number, which can be DOCUMENT while the
        // filename parse yielded nothing — pairing that tag with a null value
        // reads as "we got this from the document" next to an em dash.
        String resolvedFax = e.getSenderFaxNumber() != null && !e.getSenderFaxNumber().isBlank()
                ? e.getSenderFaxNumber()
                : callbackFax;
        String resolvedFaxSource;
        if (e.getSenderFaxNumber() != null && !e.getSenderFaxNumber().isBlank()) {
            resolvedFaxSource = "FILENAME";
        } else if (callbackFax != null && !callbackFax.isBlank()) {
            resolvedFaxSource = "DOCUMENT";
        } else {
            resolvedFaxSource = "NONE";
        }

        String suggestedFolder  = null, routingStatus = null, finalFolder = null, ruleMatched = null;
        String finalFileName    = null;
        Boolean fileNameOverridden = null;
        if (rd != null) {
            suggestedFolder = rd.getSuggestedFolder();
            routingStatus   = rd.getRoutingStatus();
            finalFolder     = rd.getFinalFolder();
            ruleMatched     = rd.getRuleMatched();
            finalFileName   = rd.getFinalFileName();
            fileNameOverridden = rd.getFileNameOverridden();
            // The routing row wins where it has an opinion — it may hold a
            // collision-suffixed or human-edited name.
            if (rd.getSuggestedFileName() != null) {
                suggestedFileName = rd.getSuggestedFileName();
            }
        }

        return new FaxDetailResponse(
                e.getTrackingId(), e.getOriginalFileName(), resolvedFax,
                e.getReceivedAt(), e.getProcessingStatus(), e.getUploadSource(),
                e.getFileSizeBytes(), e.getPageCount(), e.getMimeType(), e.getErrorReason(),
                e.getCreatedAt(), e.getUpdatedAt(),
                cat, sub, conf, band, actionRequired, actionSummary, deadline, org,
                callbackFax, resolvedFaxSource, evidence, routingEvidence, namingEvidence,
                forceReview, overrideReason,
                specification, suggestedFileName, alternateFileName, namingSource, coverSheetPages,
                suggestedFolder, routingStatus, finalFolder, finalFileName, fileNameOverridden, ruleMatched
        );
    }
}
