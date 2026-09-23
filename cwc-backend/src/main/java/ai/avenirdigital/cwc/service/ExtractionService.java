package ai.avenirdigital.cwc.service;

import ai.avenirdigital.cwc.model.ExtractedMetadataEntity;
import ai.avenirdigital.cwc.model.ClassificationResultEntity;
import ai.avenirdigital.cwc.model.FaxDocumentEntity;
import ai.avenirdigital.cwc.repository.ClassificationResultRepository;
import ai.avenirdigital.cwc.repository.ExtractedMetadataRepository;
import ai.avenirdigital.cwc.repository.FaxDocumentRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;

import java.nio.file.Path;
import java.util.UUID;

/**
 * Phase 9 extraction service.
 *
 * Calls POST /extract on the AI service for a classified fax and persists
 * the result into cwc.extracted_metadata (one row per fax, upserted on re-run).
 *
 * PHI note: coreJson / categoryDataJson contain patient PHI — never log them.
 * Use trackingId for all log correlation.
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class ExtractionService {

    private final FaxDocumentRepository          faxRepo;
    private final ClassificationResultRepository classificationRepo;
    private final ExtractedMetadataRepository    metadataRepo;
    private final FileStorageService             fileStorageService;
    private final AiServiceClient                aiServiceClient;

    /**
     * When true, every successfully filed document is extracted without a human
     * asking. Switch off to return to on-demand extraction via Re-Extract only.
     *
     * <p>Note the PHI posture this implies: with auto-extraction on, patient name,
     * DOB and MRN are extracted and stored for every fax that arrives, not only
     * for the ones staff chose to extract. See OPEN_QUESTIONS item 8.
     */
    @Value("${cwc.extraction.auto-extract:true}")
    private boolean autoExtract;

    /**
     * Trigger extraction asynchronously. Resolves the file path and category,
     * calls the AI service, and upserts into extracted_metadata.
     *
     * Prerequisites: document must be in CLASSIFIED or ROUTED status.
     */
    @Async
    public void extractAsync(UUID trackingId) {
        runExtraction(trackingId);
    }

    /**
     * Pipeline entry point, called once the pipeline has reached a resting state
     * for a document: filed and ROUTED in AUTO mode, or suggestion-persisted and
     * awaiting a reviewer in SUGGEST mode.
     *
     * <p>Runs on the async pool rather than the pipeline thread: extraction is a
     * second vision call and roughly doubles per-document latency, and routing
     * does not depend on anything it produces. A failure here is logged and
     * dropped — the fax is already correctly filed, and Re-Extract remains the
     * manual retry path. It must never move the document out of ROUTED.
     */
    @Async
    public void autoExtractAfterFiling(UUID trackingId) {
        if (!autoExtract) {
            log.debug("[EXTRACT] Auto-extraction disabled (cwc.extraction.auto-extract=false); "
                    + "skipping trackingId={}", trackingId);
            return;
        }
        try {
            runExtraction(trackingId);
        } catch (Exception e) {
            log.warn("[EXTRACT] Auto-extraction failed trackingId={} error={} - "
                    + "document remains ROUTED; use POST /api/faxes/{}/extract to retry",
                    trackingId, e.getMessage(), trackingId);
        }
    }

    private void runExtraction(UUID trackingId) {
        log.info("[EXTRACT] Starting async extraction trackingId={}", trackingId);

        FaxDocumentEntity doc = faxRepo.findByTrackingId(trackingId).orElse(null);
        if (doc == null) {
            log.error("[EXTRACT] Document not found trackingId={}", trackingId);
            return;
        }

        ClassificationResultEntity cls =
                classificationRepo.findByFaxDocumentId(doc.getId()).orElse(null);
        if (cls == null || cls.getDetectedCategory() == null) {
            log.warn("[EXTRACT] No classification result yet for trackingId={}; "
                    + "extraction skipped", trackingId);
            return;
        }

        Path filePath = fileStorageService.resolve(doc.getStoredPath());
        if (!filePath.toFile().exists()) {
            log.error("[EXTRACT] File not found on disk trackingId={}", trackingId);
            return;
        }

        AiServiceClient.ExtractResult result;
        try {
            result = aiServiceClient.callExtract(
                    filePath.toString(),
                    trackingId,
                    cls.getDetectedCategory()
            );
        } catch (AiServiceClient.AiServiceException e) {
            log.error("[EXTRACT] AI service call failed trackingId={} error={}",
                    trackingId, e.getMessage());
            return;
        }

        // Upsert — one row per fax document
        ExtractedMetadataEntity entity =
                metadataRepo.findByFaxDocumentId(doc.getId())
                        .orElseGet(ExtractedMetadataEntity::new);
        entity.setFaxDocumentId(doc.getId());
        entity.setSchemaVersion(result.schemaVersion() != null ? result.schemaVersion() : "1");
        entity.setCoreJson(result.coreJson());
        entity.setCategoryData(result.categoryDataJson());
        entity.setExtractionJson(result.extractionJson());
        entity.setPromptVersion(result.promptVersion());
        metadataRepo.save(entity);

        log.info("[EXTRACT] Persisted extraction trackingId={} category={} latency={}ms",
                trackingId, cls.getDetectedCategory(), result.latencyMs());
    }
}
