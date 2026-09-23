package ai.avenirdigital.cwc.controller;

import ai.avenirdigital.cwc.model.ClassificationResultEntity;
import ai.avenirdigital.cwc.model.ExtractedMetadataEntity;
import ai.avenirdigital.cwc.model.FaxDocumentEntity;
import ai.avenirdigital.cwc.repository.ClassificationResultRepository;
import ai.avenirdigital.cwc.repository.ExtractedMetadataRepository;
import ai.avenirdigital.cwc.repository.FaxDocumentRepository;
import ai.avenirdigital.cwc.service.ExtractionService;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Phase 9 metadata endpoints (spec §17.7).
 *
 * PHI note: /download and /export responses contain patient PHI.
 * Every response from those endpoints is logged (trackingId / filter params,
 * timestamp, source IP) but the payload itself is NEVER logged.
 */
@Slf4j
@RestController
@RequestMapping("/api/faxes")
@RequiredArgsConstructor
public class MetadataController {

    private final FaxDocumentRepository          faxRepo;
    private final ClassificationResultRepository classificationRepo;
    private final ExtractedMetadataRepository    metadataRepo;
    private final ExtractionService              extractionService;
    private final ObjectMapper                   objectMapper;

    // ── GET /api/faxes/{trackingId}/metadata ──────────────────────────────────

    @GetMapping("/{trackingId}/metadata")
    public ResponseEntity<Map<String, Object>> getMetadata(
            @PathVariable String trackingId) {

        FaxDocumentEntity doc = findDoc(trackingId);
        ExtractedMetadataEntity entity =
                metadataRepo.findByFaxDocumentId(doc.getId())
                        .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND,
                                "No extraction result found for trackingId=" + trackingId
                                + ". Run POST /api/faxes/" + trackingId + "/extract first."));

        return ResponseEntity.ok(toResponseMap(entity, trackingId));
    }

    // ── GET /api/faxes/{trackingId}/metadata/download ─────────────────────────

    /**
     * Returns the metadata JSON as a downloadable attachment.
     * Logs a PHI download audit event (never logs the payload).
     */
    @GetMapping("/{trackingId}/metadata/download")
    public ResponseEntity<byte[]> downloadMetadata(
            @PathVariable String trackingId,
            HttpServletRequest request) {

        FaxDocumentEntity doc = findDoc(trackingId);
        ExtractedMetadataEntity entity =
                metadataRepo.findByFaxDocumentId(doc.getId())
                        .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND,
                                "No extraction result found for trackingId=" + trackingId));

        // PHI audit log — payload is never logged
        log.info("[METADATA_DOWNLOAD] trackingId={} ip={} at={}",
                trackingId,
                request.getRemoteAddr(),
                OffsetDateTime.now());

        try {
            byte[] json = objectMapper
                    .writerWithDefaultPrettyPrinter()
                    .writeValueAsBytes(toResponseMap(entity, trackingId));

            return ResponseEntity.ok()
                    .header(HttpHeaders.CONTENT_DISPOSITION,
                            "attachment; filename=\"" + trackingId + "-metadata.json\"")
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(json);
        } catch (Exception e) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR,
                    "Failed to serialise metadata: " + e.getMessage());
        }
    }

    // ── GET /api/faxes/metadata/export ────────────────────────────────────────

    /**
     * Bulk export of extraction results for downstream integration.
     * Logs a PHI bulk export audit event.
     */
    @GetMapping("/metadata/export")
    public ResponseEntity<List<Map<String, Object>>> exportMetadata(
            @RequestParam(required = false) String category,
            @RequestParam(required = false) String dateFrom,
            @RequestParam(required = false) String dateTo,
            HttpServletRequest request) {

        OffsetDateTime from = dateFrom != null && !dateFrom.isBlank()
                ? OffsetDateTime.parse(dateFrom) : null;
        OffsetDateTime to   = dateTo   != null && !dateTo.isBlank()
                ? OffsetDateTime.parse(dateTo)   : null;

        List<ExtractedMetadataEntity> rows =
                metadataRepo.exportFiltered(category, from, to);

        List<Map<String, Object>> result = new ArrayList<>(rows.size());
        for (ExtractedMetadataEntity em : rows) {
            FaxDocumentEntity doc = faxRepo.findById(em.getFaxDocumentId()).orElse(null);
            String tid = doc != null ? doc.getTrackingId().toString() : "unknown";
            result.add(toResponseMap(em, tid));
        }

        // PHI audit log
        log.info("[METADATA_EXPORT] category={} dateFrom={} dateTo={} count={} ip={} at={}",
                category, dateFrom, dateTo, result.size(),
                request.getRemoteAddr(), OffsetDateTime.now());

        return ResponseEntity.ok(result);
    }

    // ── POST /api/faxes/{trackingId}/extract ──────────────────────────────────

    @PostMapping("/{trackingId}/extract")
    public ResponseEntity<Map<String, Object>> triggerExtraction(
            @PathVariable String trackingId) {

        FaxDocumentEntity doc = findDoc(trackingId);
        String status = doc.getProcessingStatus();

        if (!"CLASSIFIED".equals(status) && !"ROUTED".equals(status)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "Document must be CLASSIFIED or ROUTED before extraction. "
                    + "Current status: " + status);
        }

        log.info("[EXTRACT] Queuing extraction trackingId={}", trackingId);
        extractionService.extractAsync(doc.getTrackingId());

        return ResponseEntity.accepted().body(Map.of(
                "trackingId", trackingId,
                "message",    "Extraction queued. Poll GET /api/faxes/"
                              + trackingId + "/metadata for results."
        ));
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private FaxDocumentEntity findDoc(String trackingId) {
        UUID uuid;
        try {
            uuid = UUID.fromString(trackingId);
        } catch (IllegalArgumentException e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "Invalid trackingId format");
        }
        return faxRepo.findByTrackingId(uuid)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND,
                        "No fax found for trackingId=" + trackingId));
    }

    private Map<String, Object> toResponseMap(
            ExtractedMetadataEntity entity, String trackingId) {

        Map<String, Object> resp = new LinkedHashMap<>();
        resp.put("schemaVersion", entity.getSchemaVersion());
        resp.put("trackingId",    trackingId);

        // Determine category from extraction_json if available
        resp.put("category",      extractCategory(entity));
        resp.put("core",          parseJson(entity.getCoreJson()));
        resp.put("categoryData",  parseJson(entity.getCategoryData()));
        resp.put("extraction",    parseJson(entity.getExtractionJson()));
        resp.put("createdAt",     entity.getCreatedAt());
        resp.put("updatedAt",     entity.getUpdatedAt());
        return resp;
    }

    private Object parseJson(String json) {
        if (json == null || json.isBlank()) return Map.of();
        try {
            return objectMapper.readValue(json, Object.class);
        } catch (Exception e) {
            return Map.of("_parseError", e.getMessage());
        }
    }

    private String extractCategory(ExtractedMetadataEntity entity) {
        try {
            if (entity.getExtractionJson() != null) {
                Map<?, ?> ext = objectMapper.readValue(entity.getExtractionJson(), Map.class);
                // category may be top-level in the stored extraction_json block
                // (stored from the AI response which includes it)
                if (ext.containsKey("category")) return String.valueOf(ext.get("category"));
            }
            // Fallback: look up the classification result
            ClassificationResultEntity cls =
                    classificationRepo.findByFaxDocumentId(entity.getFaxDocumentId())
                            .orElse(null);
            return cls != null ? cls.getDetectedCategory() : null;
        } catch (Exception e) {
            return null;
        }
    }
}
