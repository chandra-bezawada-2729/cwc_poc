package ai.avenirdigital.cwc.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.*;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import java.math.BigDecimal;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * HTTP client for the cwc-ai-service (Flask, port 5002).
 * All PHI stays in the JSON body — never appears in log messages.
 */
@Slf4j
@Service
public class AiServiceClient {

    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;
    private final String aiServiceUrl;

    public AiServiceClient(
            @Value("${ai.service.url:http://127.0.0.1:5002}") String aiServiceUrl) {
        this.restTemplate = new RestTemplate();
        this.objectMapper = new ObjectMapper();
        this.aiServiceUrl = aiServiceUrl;
    }

    // ── POST /ocr ────────────────────────────────────────────────────────────

    /**
     * POST /ocr to the AI service.
     *
     * @param absoluteFilePath filesystem path the AI service can read directly
     * @param trackingId       used for log correlation, never for routing
     * @return OcrResult containing text, char_count, mode, and per-page counts
     * @throws AiServiceException if the call fails or the service returns a non-2xx status
     */
    public OcrResult callOcr(String absoluteFilePath, UUID trackingId) {
        String url = aiServiceUrl + "/ocr";

        Map<String, String> body = Map.of(
                "file_path", absoluteFilePath,
                "tracking_id", trackingId.toString()
        );

        log.info("[AI_CLIENT] POST /ocr trackingId={}", trackingId);
        return post(url, body, OcrResult.class, OcrResult::from);
    }

    // ── POST /classify ────────────────────────────────────────────────────────

    /**
     * POST /classify to the AI service — full vision classification pipeline.
     *
     * @param absoluteFilePath the stored PDF/image the AI service can read
     * @param trackingId       log correlation ID
     * @param senderFaxNumber  E.164 fax number parsed from filename (may be null)
     * @param receivedAtUtc    ISO-8601 UTC string parsed from filename (may be null)
     * @param originalFileName inbound filename; the AI service uses only its
     *                         extension when building the suggested rename (may be null)
     * @return ClassifyResult with the full §4.4 contract + calibrated confidence
     *         + the suggested rename
     * @throws AiServiceException on failure
     */
    public ClassifyResult callClassify(
            String absoluteFilePath,
            UUID trackingId,
            String senderFaxNumber,
            String receivedAtUtc,
            String originalFileName
    ) {
        String url = aiServiceUrl + "/classify";

        Map<String, String> body = new HashMap<>();
        body.put("file_path", absoluteFilePath);
        body.put("tracking_id", trackingId.toString());
        if (senderFaxNumber != null)  body.put("sender_fax_number", senderFaxNumber);
        if (receivedAtUtc != null)    body.put("received_at", receivedAtUtc);
        if (originalFileName != null) body.put("original_file_name", originalFileName);

        log.info("[AI_CLIENT] POST /classify trackingId={}", trackingId);

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        HttpEntity<Map<String, String>> entity = new HttpEntity<>(body, headers);

        try {
            @SuppressWarnings("unchecked")
            ResponseEntity<Map<String, Object>> response =
                    restTemplate.exchange(url, HttpMethod.POST, entity,
                            (Class<Map<String, Object>>) (Class<?>) Map.class);

            if (!response.getStatusCode().is2xxSuccessful() || response.getBody() == null) {
                throw new AiServiceException("/classify returned: " + response.getStatusCode());
            }
            return ClassifyResult.from(response.getBody(), objectMapper);

        } catch (AiServiceException e) {
            throw e;
        } catch (Exception e) {
            throw new AiServiceException("Classify call failed: " + e.getMessage(), e);
        }
    }

    // ── POST /extract ─────────────────────────────────────────────────────────

    /**
     * POST /extract — category-specific metadata extraction (Phase 9 §17.7).
     *
     * @param absoluteFilePath the stored PDF the AI service can read
     * @param trackingId       log correlation ID
     * @param category         document category code (e.g. "MEDICATION_REVIEW")
     * @return ExtractResult carrying the raw extraction JSON as a String
     * @throws AiServiceException on failure
     */
    public ExtractResult callExtract(
            String absoluteFilePath,
            UUID trackingId,
            String category
    ) {
        String url = aiServiceUrl + "/extract";

        Map<String, String> body = new HashMap<>();
        body.put("file_path",    absoluteFilePath);
        body.put("tracking_id",  trackingId.toString());
        body.put("category",     category != null ? category : "OTHER");

        log.info("[AI_CLIENT] POST /extract trackingId={} category={}", trackingId, category);

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        HttpEntity<Map<String, String>> entity = new HttpEntity<>(body, headers);

        try {
            @SuppressWarnings("unchecked")
            ResponseEntity<Map<String, Object>> response =
                    restTemplate.exchange(url, HttpMethod.POST, entity,
                            (Class<Map<String, Object>>) (Class<?>) Map.class);

            if (!response.getStatusCode().is2xxSuccessful() || response.getBody() == null) {
                throw new AiServiceException("/extract returned: " + response.getStatusCode());
            }
            return ExtractResult.from(response.getBody(), objectMapper);

        } catch (AiServiceException e) {
            throw e;
        } catch (Exception e) {
            throw new AiServiceException("Extract call failed: " + e.getMessage(), e);
        }
    }

    // ── Generic POST helper ──────────────────────────────────────────────────

    @FunctionalInterface
    private interface BodyMapper<T> {
        T map(Map<String, Object> body);
    }

    private <T> T post(String url, Object requestBody, Class<T> ignored, BodyMapper<T> mapper) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        HttpEntity<?> entity = new HttpEntity<>(requestBody, headers);
        try {
            @SuppressWarnings("unchecked")
            ResponseEntity<Map<String, Object>> response =
                    restTemplate.exchange(url, HttpMethod.POST, entity,
                            (Class<Map<String, Object>>) (Class<?>) Map.class);
            if (!response.getStatusCode().is2xxSuccessful() || response.getBody() == null) {
                throw new AiServiceException("Non-2xx from " + url + ": " + response.getStatusCode());
            }
            return mapper.map(response.getBody());
        } catch (AiServiceException e) {
            throw e;
        } catch (Exception e) {
            throw new AiServiceException("Call to " + url + " failed: " + e.getMessage(), e);
        }
    }

    // ── DTOs ─────────────────────────────────────────────────────────────────

    public record OcrResult(
            String text,
            int charCount,
            String mode,       // NATIVE | TESSERACT
            boolean tesseractAvailable
    ) {
        static OcrResult from(Map<String, Object> body) {
            String text = body.get("text") instanceof String s ? s : "";
            int charCount = body.get("char_count") instanceof Number n ? n.intValue() : 0;
            String mode = body.get("mode") instanceof String s ? s : "TESSERACT";
            boolean tessOk = Boolean.TRUE.equals(body.get("tesseract_available"));
            return new OcrResult(text, charCount, mode, tessOk);
        }
    }

    /**
     * Overload keeping the pre-v2.0 four-argument signature working.
     * Without an original filename the AI service defaults the extension to .pdf.
     */
    public ClassifyResult callClassify(
            String absoluteFilePath,
            UUID trackingId,
            String senderFaxNumber,
            String receivedAtUtc
    ) {
        return callClassify(absoluteFilePath, trackingId, senderFaxNumber, receivedAtUtc, null);
    }

    public record ClassifyResult(
            String documentCategory,
            String documentSubtype,
            // ── taxonomy v2.0: the suggested rename ──────────────────────────
            String specification,        // short noun phrase, e.g. "US Abdomen"
            String suggestedFileName,    // CWC house style,  e.g. "US Abdomen.pdf"
            String alternateFileName,    // snake style,      e.g. "us_abdomen_radiologyreport.pdf"
            String suggestedFolderFromAi,// folder the taxonomy maps this category to
            String documentTypeSlug,     // slug used to build alternateFileName
            String namingSource,         // MODEL | FALLBACK
            Integer coverSheetPages,     // leading fax cover sheets detected
            // ────────────────────────────────────────────────────────────────
            BigDecimal modelConfidence,
            BigDecimal calibratedConfidence,
            String confidenceBand,
            String runnerUpCategory,
            BigDecimal runnerUpConfidence,
            String reason,
            String evidenceJson,         // serialized JSON array — ALL quotes, scored
            String routingEvidenceJson,  // display subset: why this category (may be null)
            String namingEvidenceJson,   // display subset: why this name (may be null)
            BigDecimal evidenceScore,
            String senderOrganization,
            String senderCallbackFax,    // return fax from the document body
            String senderFaxNumberSource,// FILENAME | DOCUMENT | NONE
            Boolean actionRequired,
            String actionSummary,
            String responseDeadline,     // ISO-8601 date string or null
            Boolean containsFillableForm,
            String phiIdentifiersJson,   // serialized JSON array
            String classificationMode,   // VISION | OCR_FALLBACK
            int ocrCharCount,
            String modelName,
            String promptVersion,
            int latencyMs,
            String rawResponseJson,      // full response serialized as JSON
            Boolean forceManualReview,
            String overrideReason,
            String errorReason
    ) {
        /**
         * Pre-v2.1 shape, without the split display-evidence arrays or the
         * document-read sender fax. Those four are presentation and provenance
         * only, so a caller that predates them gets nulls — the same thing a row
         * classified before prompt v2.1 has in the database.
         */
        public ClassifyResult(
                String documentCategory,
                String documentSubtype,
                String specification,
                String suggestedFileName,
                String alternateFileName,
                String suggestedFolderFromAi,
                String documentTypeSlug,
                String namingSource,
                Integer coverSheetPages,
                BigDecimal modelConfidence,
                BigDecimal calibratedConfidence,
                String confidenceBand,
                String runnerUpCategory,
                BigDecimal runnerUpConfidence,
                String reason,
                String evidenceJson,
                BigDecimal evidenceScore,
                String senderOrganization,
                Boolean actionRequired,
                String actionSummary,
                String responseDeadline,
                Boolean containsFillableForm,
                String phiIdentifiersJson,
                String classificationMode,
                int ocrCharCount,
                String modelName,
                String promptVersion,
                int latencyMs,
                String rawResponseJson,
                Boolean forceManualReview,
                String overrideReason,
                String errorReason
        ) {
            this(documentCategory, documentSubtype, specification, suggestedFileName,
                    alternateFileName, suggestedFolderFromAi, documentTypeSlug, namingSource,
                    coverSheetPages, modelConfidence, calibratedConfidence, confidenceBand,
                    runnerUpCategory, runnerUpConfidence, reason,
                    evidenceJson, null, null, evidenceScore,
                    senderOrganization, null, null,
                    actionRequired, actionSummary, responseDeadline, containsFillableForm,
                    phiIdentifiersJson, classificationMode, ocrCharCount, modelName,
                    promptVersion, latencyMs, rawResponseJson, forceManualReview,
                    overrideReason, errorReason);
        }

        @SuppressWarnings("unchecked")
        static ClassifyResult from(Map<String, Object> body, ObjectMapper mapper) {
            try {
                String evidenceJson = null;
                Object ev = body.get("evidence");
                if (ev instanceof List<?>) {
                    evidenceJson = mapper.writeValueAsString(ev);
                }

                // Display subsets are optional by contract (prompt v2.1). An empty
                // list is stored as null so the UI's "both empty → fall back to the
                // flat evidence list" test is one null check, not a JSON parse.
                String routingEvidenceJson = displayEvidence(body.get("routingEvidence"), mapper);
                String namingEvidenceJson  = displayEvidence(body.get("namingEvidence"), mapper);

                String phiJson = null;
                Object phi = body.get("patientIdentifiersPresent");
                if (phi instanceof List<?>) {
                    phiJson = mapper.writeValueAsString(phi);
                }

                String rawJson = mapper.writeValueAsString(body);

                return new ClassifyResult(
                        str(body, "documentCategory"),
                        str(body, "documentSubtype"),
                        str(body, "specification"),
                        str(body, "suggestedFileName"),
                        str(body, "alternateFileName"),
                        str(body, "suggestedFolder"),
                        str(body, "documentTypeSlug"),
                        str(body, "namingSource"),
                        integer(body, "coverSheetPages"),
                        decimal(body, "modelConfidence"),
                        decimal(body, "calibratedConfidence"),
                        str(body, "confidenceBand"),
                        str(body, "runnerUpCategory"),
                        decimal(body, "runnerUpConfidence"),
                        str(body, "reason"),
                        evidenceJson,
                        routingEvidenceJson,
                        namingEvidenceJson,
                        decimal(body, "evidenceScore"),
                        str(body, "senderOrganization"),
                        str(body, "senderCallbackFax"),
                        str(body, "senderFaxNumberSource"),
                        bool(body, "actionRequired"),
                        str(body, "actionSummary"),
                        str(body, "responseDeadline"),
                        bool(body, "containsFillableForm"),
                        phiJson,
                        str(body, "classificationMode"),
                        intVal(body, "ocrCharCount"),
                        str(body, "modelName"),
                        str(body, "promptVersion"),
                        intVal(body, "latencyMs"),
                        rawJson,
                        bool(body, "forceManualReview"),
                        str(body, "overrideReason"),
                        str(body, "errorReason")
                );
            } catch (Exception e) {
                throw new AiServiceException("Failed to deserialize /classify response: " + e.getMessage(), e);
            }
        }

        private static String str(Map<String, Object> m, String key) {
            Object v = m.get(key);
            return v instanceof String s ? s : (v != null ? v.toString() : null);
        }

        /**
         * Serialize an optional display-evidence array, dropping blanks.
         * Returns null for anything absent, malformed or empty — these arrays are
         * presentation-only, so a bad one degrades the screen rather than the run.
         */
        private static String displayEvidence(Object raw, ObjectMapper mapper)
                throws JsonProcessingException {
            if (!(raw instanceof List<?> list)) return null;
            List<String> quotes = list.stream()
                    .filter(String.class::isInstance)
                    .map(q -> ((String) q).trim())
                    .filter(q -> !q.isEmpty())
                    .toList();
            return quotes.isEmpty() ? null : mapper.writeValueAsString(quotes);
        }

        private static BigDecimal decimal(Map<String, Object> m, String key) {
            Object v = m.get(key);
            if (v instanceof Number n) return BigDecimal.valueOf(n.doubleValue());
            return null;
        }

        private static Boolean bool(Map<String, Object> m, String key) {
            Object v = m.get(key);
            if (v instanceof Boolean b) return b;
            return null;
        }

        private static int intVal(Map<String, Object> m, String key) {
            Object v = m.get(key);
            if (v instanceof Number n) return n.intValue();
            return 0;
        }

        /** Nullable variant of intVal — absent means "not reported", not zero. */
        private static Integer integer(Map<String, Object> m, String key) {
            Object v = m.get(key);
            if (v instanceof Number n) return n.intValue();
            return null;
        }
    }

    public record ExtractResult(
            String schemaVersion,
            String trackingId,
            String category,
            String coreJson,
            String categoryDataJson,
            String extractionJson,
            String promptVersion,
            int    latencyMs
    ) {
        static ExtractResult from(Map<String, Object> body, ObjectMapper mapper) {
            try {
                String coreJson = mapper.writeValueAsString(
                        body.getOrDefault("core", Map.of()));
                String catJson  = mapper.writeValueAsString(
                        body.getOrDefault("categoryData", Map.of()));
                String extJson  = mapper.writeValueAsString(
                        body.getOrDefault("extraction", Map.of()));

                Map<?, ?> extMap = body.get("extraction") instanceof Map<?, ?> m ? m : Map.of();
                int latency = extMap.get("latencyMs") instanceof Number n ? n.intValue() : 0;
                Object pvObj = extMap.get("promptVersion");
                String pv = pvObj instanceof String s ? s : null;

                return new ExtractResult(
                        strVal(body, "schemaVersion"),
                        strVal(body, "trackingId"),
                        strVal(body, "category"),
                        coreJson,
                        catJson,
                        extJson,
                        pv,
                        latency
                );
            } catch (Exception e) {
                throw new AiServiceException(
                        "Failed to deserialize /extract response: " + e.getMessage(), e);
            }
        }

        private static String strVal(Map<String, Object> m, String key) {
            Object v = m.get(key);
            return v instanceof String s ? s : (v != null ? v.toString() : null);
        }
    }

    public static class AiServiceException extends RuntimeException {
        public AiServiceException(String msg) { super(msg); }
        public AiServiceException(String msg, Throwable cause) { super(msg, cause); }
    }
}
