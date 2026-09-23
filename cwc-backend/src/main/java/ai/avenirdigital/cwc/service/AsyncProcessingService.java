package ai.avenirdigital.cwc.service;

import ai.avenirdigital.cwc.model.ClassificationResultEntity;
import ai.avenirdigital.cwc.model.FaxDocumentEntity;
import ai.avenirdigital.cwc.model.RoutingDecisionEntity;
import ai.avenirdigital.cwc.repository.ClassificationResultRepository;
import ai.avenirdigital.cwc.repository.FaxDocumentRepository;
import ai.avenirdigital.cwc.repository.RoutingDecisionRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.UUID;

/**
 * Runs post-upload pipeline steps on the bounded thread pool.
 *
 * Phase 2: validate.
 * Phase 3: validate → OCR → persist classification_results row (null category).
 * Phase 4: validate → OCR → classify → persist full result → CLASSIFIED.
 * Phase 6: compute routing suggestion; in AUTO mode file HIGH-band non-forced docs immediately.
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class AsyncProcessingService {

    private final FaxDocumentRepository          faxRepo;
    private final ClassificationResultRepository classificationResultRepo;
    private final RoutingDecisionRepository      routingDecisionRepo;
    private final FileStorageService             fileStorageService;
    private final ValidationService              validationService;
    private final AiServiceClient                aiServiceClient;
    private final RoutingEngineService           routingEngine;
    private final OutboundFilingService          outboundFilingService;
    private final ExtractionService              extractionService;

    @Async
    public void processDocument(UUID trackingId) {
        log.info("[ASYNC] Starting processing for trackingId={}", trackingId);

        FaxDocumentEntity doc = faxRepo.findByTrackingId(trackingId).orElse(null);
        if (doc == null) {
            log.error("[ASYNC] Entity not found for trackingId={}", trackingId);
            return;
        }

        // ── Validate ──────────────────────────────────────────────────────────
        setStatus(doc, "VALIDATING");
        Path filePath = fileStorageService.resolve(doc.getStoredPath());
        ValidationService.ValidationResult validation =
                validationService.validate(filePath, doc.getOriginalFileName());

        if (!validation.valid()) {
            log.warn("[ASYNC] Validation failed trackingId={} reason={}", trackingId, validation.errorReason());
            doc.setProcessingStatus("ERRORED");
            doc.setErrorReason(validation.errorReason());
            faxRepo.save(doc);
            return;
        }
        doc.setMimeType(validation.mimeType());
        doc.setPageCount(validation.pageCount());
        setStatus(doc, "RECEIVED");
        log.info("[ASYNC] Validation OK trackingId={} pages={}", trackingId, validation.pageCount());

        // ── OCR ───────────────────────────────────────────────────────────────
        setStatus(doc, "OCR");
        AiServiceClient.OcrResult ocr;
        try {
            ocr = aiServiceClient.callOcr(filePath.toString(), trackingId);
        } catch (AiServiceClient.AiServiceException e) {
            log.error("[ASYNC] OCR failed trackingId={} error={}", trackingId, e.getMessage());
            doc.setProcessingStatus("ERRORED");
            doc.setErrorReason("OCR_FAILED: " + e.getMessage());
            faxRepo.save(doc);
            return;
        }

        // Persist OCR data to classification_results (category still null)
        ClassificationResultEntity clsRow =
                classificationResultRepo.findByFaxDocumentId(doc.getId())
                        .orElseGet(ClassificationResultEntity::new);
        clsRow.setFaxDocumentId(doc.getId());
        clsRow.setOcrCharCount(ocr.charCount());
        clsRow.setOcrText(ocr.text());           // PHI-bearing — never logged
        clsRow.setClassificationMode(ocr.mode());
        classificationResultRepo.save(clsRow);
        log.info("[ASYNC] OCR done trackingId={} mode={} chars={}", trackingId, ocr.mode(), ocr.charCount());

        // ── Classify ──────────────────────────────────────────────────────────
        setStatus(doc, "CLASSIFYING");
        AiServiceClient.ClassifyResult cls;
        try {
            String receivedAtUtc = doc.getReceivedAt() != null
                    ? doc.getReceivedAt().atZoneSameInstant(ZoneOffset.UTC)
                           .format(DateTimeFormatter.ISO_OFFSET_DATE_TIME)
                    : null;
            cls = aiServiceClient.callClassify(
                    filePath.toString(),
                    trackingId,
                    doc.getSenderFaxNumber(),
                    receivedAtUtc,
                    doc.getOriginalFileName()
            );
        } catch (AiServiceClient.AiServiceException e) {
            log.error("[ASYNC] Classify failed trackingId={} error={}", trackingId, e.getMessage());
            doc.setProcessingStatus("ERRORED");
            doc.setErrorReason("CLASSIFY_FAILED: " + e.getMessage());
            faxRepo.save(doc);
            return;
        }

        // Persist full classification result
        clsRow = classificationResultRepo.findByFaxDocumentId(doc.getId())
                .orElseGet(ClassificationResultEntity::new);
        clsRow.setFaxDocumentId(doc.getId());

        clsRow.setDetectedCategory(cls.documentCategory());
        clsRow.setDetectedSubtype(cls.documentSubtype());
        clsRow.setModelConfidence(cls.modelConfidence());
        clsRow.setCalibratedConfidence(cls.calibratedConfidence());
        clsRow.setConfidenceBand(cls.confidenceBand() != null ? cls.confidenceBand() : "LOW");
        clsRow.setRunnerUpCategory(cls.runnerUpCategory());
        clsRow.setRunnerUpConfidence(cls.runnerUpConfidence());
        clsRow.setReason(cls.reason());
        clsRow.setEvidence(cls.evidenceJson());
        clsRow.setRoutingEvidence(cls.routingEvidenceJson());
        clsRow.setNamingEvidence(cls.namingEvidenceJson());
        clsRow.setEvidenceScore(cls.evidenceScore());
        clsRow.setSenderOrganization(cls.senderOrganization());
        clsRow.setSenderCallbackFax(cls.senderCallbackFax());
        clsRow.setSenderFaxNumberSource(cls.senderFaxNumberSource());
        clsRow.setActionRequired(cls.actionRequired());
        clsRow.setActionSummary(cls.actionSummary());
        clsRow.setResponseDeadline(parseDeadline(cls.responseDeadline()));
        clsRow.setContainsFillableForm(cls.containsFillableForm());
        clsRow.setPhiIdentifiers(cls.phiIdentifiersJson());
        clsRow.setClassificationMode(cls.classificationMode());
        clsRow.setOcrCharCount(cls.ocrCharCount());
        // ocrText is already set from the OCR step; do not overwrite with null
        if (clsRow.getOcrText() == null) {
            clsRow.setOcrText(ocr.text());
        }
        clsRow.setModelName(cls.modelName());
        clsRow.setPromptVersion(cls.promptVersion());
        clsRow.setLatencyMs(cls.latencyMs());
        clsRow.setRawResponse(cls.rawResponseJson());
        clsRow.setForceManualReview(cls.forceManualReview());
        clsRow.setOverrideReason(cls.overrideReason());
        // Taxonomy v2.0 — the suggested rename
        clsRow.setSpecification(cls.specification());
        clsRow.setSuggestedFileName(cls.suggestedFileName());
        clsRow.setAlternateFileName(cls.alternateFileName());
        clsRow.setDocumentTypeSlug(cls.documentTypeSlug());
        clsRow.setNamingSource(cls.namingSource());
        clsRow.setCoverSheetPages(cls.coverSheetPages());
        classificationResultRepo.save(clsRow);

        // Update page count from AI service (it counted from the image)
        if (cls.ocrCharCount() > 0 && doc.getPageCount() == null) {
            // leave page count as validated
        }

        log.info("[ASYNC] Classify done trackingId={} category={} band={} rename={} latency={}ms",
                trackingId, cls.documentCategory(), cls.confidenceBand(),
                cls.suggestedFileName(), cls.latencyMs());

        setStatus(doc, "CLASSIFIED");

        // ── Routing suggestion ────────────────────────────────────────────────
        RoutingEngineService.SuggestResult suggestion = routingEngine.suggest(
                cls.documentCategory(),
                cls.documentSubtype(),
                doc.getSenderFaxNumber()
        );

        boolean forced    = Boolean.TRUE.equals(cls.forceManualReview());
        boolean highBand  = "HIGH".equals(cls.confidenceBand());
        boolean autoDisabled = routingEngine.isAutoRouteDisabled(cls.documentCategory());
        boolean autoMode  = "AUTO".equals(routingEngine.getMode());

        // CWC's policy dial, separate from the band. The band describes the
        // model's calibration; this is the business answer to "how much doubt is
        // acceptable before a person looks at a patient document". Both must
        // pass, so raising the threshold can only ever send MORE to review.
        boolean meetsGate = routingEngine.meetsConfidenceGate(cls.calibratedConfidence());

        String routingStatus;
        if (forced || autoDisabled || !meetsGate) {
            routingStatus = "MANUAL_REVIEW";
        } else {
            routingStatus = "SUGGESTED"; // becomes CONFIRMED below when AUTO files it
        }

        if (!meetsGate && !forced && !autoDisabled) {
            log.info("[ROUTING] Below confidence gate trackingId={} confidence={} min={} "
                     + "-> Manual-Review",
                    trackingId, cls.calibratedConfidence(),
                    routingEngine.getAutoRouteMinConfidence());
        }

        // The rename is half the routing decision — CWC's manual step picks a
        // folder AND a name in one action, so both are recorded together.
        String suggestedName = routingEngine.chooseFileName(
                cls.suggestedFileName(),
                cls.alternateFileName(),
                doc.getOriginalFileName()
        );

        RoutingDecisionEntity rd = new RoutingDecisionEntity();
        rd.setFaxDocumentId(doc.getId());
        rd.setSuggestedFolder(suggestion.folder());
        rd.setRuleMatched(suggestion.ruleMatched());
        rd.setRoutingStatus(routingStatus);
        rd.setDecidedBy("SYSTEM");
        rd.setOriginalFileName(doc.getOriginalFileName());
        rd.setSuggestedFileName(suggestedName);
        rd.setFileNameOverridden(Boolean.FALSE);
        routingDecisionRepo.save(rd);

        // ── Automated filing ─────────────────────────────────────────────────
        // Three outcomes in AUTO mode:
        //   confident        -> file into the predicted folder
        //   NOT confident    -> file into Manual-Review, if fileUncertain is on
        //   fileUncertain off-> hold in the app, original stays inbound
        //
        // Filing an uncertain document to Manual-Review is NOT the same as
        // guessing a folder for it. The safety intent of the confidence gate is
        // preserved; what changes is that the inbound folder still drains, so
        // staff work one queue instead of watching a folder and a UI.
        boolean confident        = autoMode && highBand && meetsGate && !forced && !autoDisabled;
        boolean uncertainButFile = autoMode && !confident
                                   && routingEngine.isFileUncertainToReviewFolder();

        if (confident || uncertainButFile) {
            try {
                OutboundFilingService.FilingResult result =
                        outboundFilingService.fileAndArchive(doc, rd, uncertainButFile);

                if (result.filed()) {
                    setStatus(doc, "ROUTED");
                    log.info("[ROUTING] AUTO-filed trackingId={} -> {}/{} ({})",
                            trackingId, result.folder(), result.fileName(), result.note());
                    // Metadata extraction runs only now, after the document is
                    // safely filed. It is a second vision call, nothing about
                    // routing depends on it, and it must not be able to fail a
                    // correctly-routed fax.
                    extractionService.autoExtractAfterFiling(trackingId);
                } else {
                    log.error("[ROUTING] AUTO-file failed trackingId={}: {}",
                            trackingId, result.note());
                }
            } catch (Exception e) {
                log.error("[ROUTING] AUTO-file threw for trackingId={}: {}",
                        trackingId, e.getMessage(), e);
                rd.setRoutingStatus("FAILED");
                routingDecisionRepo.save(rd);
            }
        } else {
            log.info("[ROUTING] Suggestion persisted trackingId={} folder={} name={} status={}",
                    trackingId, suggestion.folder(), suggestedName, routingStatus);
            // No filing happens on this path, so filing cannot be the trigger.
            // The equivalent safe point is here: the routing suggestion is
            // persisted and the reviewer is about to open the document. Without
            // this, SUGGEST mode — the shipped default — never extracts anything,
            // and the File Metadata card is empty on the one screen that needs it.
            extractionService.autoExtractAfterFiling(trackingId);
        }
    }

    private void setStatus(FaxDocumentEntity doc, String status) {
        doc.setProcessingStatus(status);
        faxRepo.save(doc);
    }

    private LocalDate parseDeadline(String value) {
        if (value == null || value.isBlank() || value.equalsIgnoreCase("null")) return null;
        try {
            return LocalDate.parse(value, DateTimeFormatter.ISO_LOCAL_DATE);
        } catch (Exception e) {
            log.warn("[ASYNC] Could not parse responseDeadline: {}", value);
            return null;
        }
    }
}
