package ai.avenirdigital.cwc;

import ai.avenirdigital.cwc.repository.FaxDocumentRepository;
import ai.avenirdigital.cwc.repository.RoutingDecisionRepository;
import ai.avenirdigital.cwc.service.AiServiceClient;
import ai.avenirdigital.cwc.service.AiServiceClient.ClassifyResult;
import ai.avenirdigital.cwc.service.AiServiceClient.OcrResult;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.BDDMockito.given;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

/**
 * Integration test: fixture PDF → upload → stubbed classify → gate → route → assert
 *
 * The AI service is stubbed via @MockBean so no live API calls are made (spec §13).
 * The test uses the dev PostgreSQL database; test records are cleaned up in @AfterEach.
 *
 * Note: requires the CWC PostgreSQL database to be running locally.
 * Set SPRING_DATASOURCE_URL / USERNAME / PASSWORD (or rely on application.properties).
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureMockMvc
@ActiveProfiles("test")
class FaxPipelineIntegrationTest {

    private static final String FIXTURE_PDF =
            "src/test/resources/fixtures/sample_pa.pdf";

    @Autowired MockMvc                 mvc;
    @Autowired FaxDocumentRepository   faxRepo;
    @Autowired RoutingDecisionRepository rdRepo;
    @Autowired ObjectMapper            mapper;

    @MockBean AiServiceClient aiClient;

    /** Tracking ID created during the test — cleaned up in afterEach. */
    private UUID createdTrackingId;

    @BeforeEach
    void stubAiClient() {
        // Stub OCR: returns realistic text so classification proceeds
        given(aiClient.callOcr(any(), any()))
                .willReturn(new OcrResult(
                        "PRIOR AUTHORIZATION REQUEST Patient needs medication approval.",
                        60, "NATIVE", true));

        // Stub classify: returns PRIOR_AUTHORIZATION / HIGH — routes to
        // "Prior-Authorization" per routing-config.yml
        given(aiClient.callClassify(any(), any(), any(), any()))
                .willReturn(new ClassifyResult(
                        "PRIOR_AUTHORIZATION",   // documentCategory
                        "MEDICATION",            // documentSubtype
                        "Medication PA",         // specification
                        "Medication PA.pdf",     // suggestedFileName
                        "medication_pa_priorauthorization.pdf", // alternateFileName
                        "Prior-Authorization",   // suggestedFolderFromAi
                        "priorauthorization",    // documentTypeSlug
                        "MODEL",                 // namingSource
                        0,                       // coverSheetPages
                        new BigDecimal("0.92"),  // modelConfidence
                        new BigDecimal("0.89"),  // calibratedConfidence
                        "HIGH",                  // confidenceBand
                        "PAYER_CARE_GAP",        // runnerUpCategory
                        new BigDecimal("0.05"),  // runnerUpConfidence
                        "Clear PA header and body",  // reason
                        "[\"PA request header\"]",  // evidenceJson
                        new BigDecimal("1.0"),   // evidenceScore
                        "Test Insurer",          // senderOrganization
                        false,                   // actionRequired
                        null,                    // actionSummary
                        null,                    // responseDeadline
                        false,                   // containsFillableForm
                        "[]",                    // phiIdentifiersJson
                        "VISION",                // classificationMode
                        60,                      // ocrCharCount
                        "claude-opus-4-5",       // modelName
                        "cwc-classify-v1.1",     // promptVersion
                        1200,                    // latencyMs
                        "{\"documentCategory\":\"PRIOR_AUTHORIZATION\"}",  // rawResponseJson
                        false,                   // forceManualReview
                        null,                    // overrideReason
                        null                     // errorReason
                ));
    }

    @AfterEach
    void cleanup() {
        if (createdTrackingId == null) return;
        faxRepo.findByTrackingId(createdTrackingId).ifPresent(doc -> {
            rdRepo.findAllByFaxDocumentIdIn(java.util.List.of(doc.getId()))
                    .forEach(rdRepo::delete);
            faxRepo.delete(doc);
        });
    }

    /**
     * Spec §13: fixture PDF walks upload → stubbed classify → gate → route.
     * Asserts file landed in expected folder AND routing_decisions row exists.
     */
    @Test
    void fullPipeline_upload_classify_confirm_assertsFileAndDbRow() throws Exception {

        // ── 1. Upload ──────────────────────────────────────────────────────────
        byte[] pdfBytes = Files.readAllBytes(Paths.get(FIXTURE_PDF));
        MockMultipartFile mpf = new MockMultipartFile(
                "files",
                "(212)497-8948_2026-08-18_0943PM.pdf",  // parseable filename → E.164
                "application/pdf",
                pdfBytes
        );

        MvcResult uploadResult = mvc.perform(multipart("/api/faxes/upload").file(mpf))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.uploaded[0].trackingId").isNotEmpty())
                .andReturn();

        String body    = uploadResult.getResponse().getContentAsString();
        createdTrackingId = UUID.fromString(
                (String) ((Map<?, ?>) ((java.util.List<?>) mapper.readValue(body, Map.class)
                        .get("uploaded")).get(0)).get("trackingId"));

        // ── 2. Poll until CLASSIFIED (async pipeline runs in bounded pool) ─────
        String trackingId = createdTrackingId.toString();
        String status = pollUntilTerminal(trackingId, 30, 300);
        assertThat(status)
                .as("Expected CLASSIFIED but got " + status)
                .isIn("CLASSIFIED", "ROUTED");

        // ── 3. Verify routing suggestion was created ───────────────────────────
        var doc = faxRepo.findByTrackingId(createdTrackingId).orElseThrow();
        var decisions = rdRepo.findAllByFaxDocumentIdIn(java.util.List.of(doc.getId()));
        assertThat(decisions).isNotEmpty();
        var suggestion = decisions.stream()
                .filter(rd -> "SUGGESTED".equals(rd.getRoutingStatus())
                           || "CONFIRMED".equals(rd.getRoutingStatus()))
                .findFirst();
        assertThat(suggestion).isPresent();
        // Matches routing-config.yml rule: PRIOR_AUTHORIZATION → Prior-Authorization
        assertThat(suggestion.get().getSuggestedFolder()).isEqualTo("Prior-Authorization");

        // ── 4. Confirm routing ─────────────────────────────────────────────────
        mvc.perform(post("/api/faxes/" + trackingId + "/confirm")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"folder":"Prior-Authorization","decidedBy":"INTEGRATION_TEST"}
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("CONFIRMED"))
                .andExpect(jsonPath("$.folder").value("Prior-Authorization"));

        // ── 5. Assert file landed on disk ──────────────────────────────────────
        var confirmedRd = rdRepo.findTopByFaxDocumentIdOrderByDecidedAtDesc(doc.getId())
                .orElseThrow();
        assertThat(confirmedRd.getRoutingStatus()).isEqualTo("CONFIRMED");
        assertThat(confirmedRd.getFinalFolder()).isEqualTo("Prior-Authorization");

        String finalPath = confirmedRd.getFinalPath();
        assertThat(finalPath).isNotNull();
        assertThat(Path.of(finalPath)).exists();

        // ── 6. Assert routing_decisions row in DB ──────────────────────────────
        assertThat(confirmedRd.getDecidedBy()).isEqualTo("INTEGRATION_TEST");
        assertThat(confirmedRd.getRuleMatched()).isNotNull();
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    /**
     * Polls GET /api/faxes/{trackingId} until processingStatus is a terminal state
     * (CLASSIFIED, ROUTED, ERRORED) or the attempt budget is exhausted.
     *
     * @param trackingId  document to poll
     * @param maxAttempts maximum number of polls before giving up
     * @param intervalMs  sleep duration between polls in milliseconds
     * @return the final observed status
     */
    private String pollUntilTerminal(String trackingId, int maxAttempts, int intervalMs)
            throws Exception {
        for (int i = 0; i < maxAttempts; i++) {
            MvcResult r = mvc.perform(get("/api/faxes/" + trackingId))
                    .andExpect(status().isOk())
                    .andReturn();
            String s = mapper.readValue(r.getResponse().getContentAsString(), Map.class)
                    .get("processingStatus").toString();
            if ("CLASSIFIED".equals(s) || "ROUTED".equals(s) || "ERRORED".equals(s)) {
                return s;
            }
            Thread.sleep(intervalMs);
        }
        throw new AssertionError(
                "Document " + trackingId + " did not reach terminal status within "
                + (maxAttempts * intervalMs / 1000.0) + "s");
    }
}
