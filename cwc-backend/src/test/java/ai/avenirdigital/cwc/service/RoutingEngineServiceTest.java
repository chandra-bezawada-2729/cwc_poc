package ai.avenirdigital.cwc.service;

import ai.avenirdigital.cwc.repository.RoutingDecisionRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Unit tests for RoutingEngineService.
 *
 * Covers every resolution branch from spec §6:
 *   sender-override → subtype-override → category-rule → unmapped (manual review)
 *
 * Also tests:
 *   - auto_route_disabled check
 *   - isAutoRouteDisabled flag
 *   - mode accessor
 */
@ExtendWith(MockitoExtension.class)
class RoutingEngineServiceTest {

    @Mock FileStorageService      fileStorageService;
    @Mock RoutingDecisionRepository routingRepo;

    RoutingEngineService svc;

    // Shared test config
    private static RoutingEngineService.LiveRoutingConfig testConfig() {
        return new RoutingEngineService.LiveRoutingConfig(
                "SUGGEST",
                "Manual-Review",
                List.of("PRESCRIPTION"),
                List.of(
                        new RoutingEngineService.RoutingRule("PHARMACY_REQUEST", "Pharmacy-Requests"),
                        new RoutingEngineService.RoutingRule("PRIOR_AUTHORIZATION", "Prior-Authorization"),
                        new RoutingEngineService.RoutingRule("PAYER_CARE_GAP", "Care-Gaps"),
                        new RoutingEngineService.RoutingRule("FOLLOW_UP", "Follow-up"),
                        new RoutingEngineService.RoutingRule("MEDICAL_REPORT", "Reports"),
                        new RoutingEngineService.RoutingRule("PRESCRIPTION", "Prescriptions"),
                        new RoutingEngineService.RoutingRule("OTHER", "Manual-Review"),
                        new RoutingEngineService.RoutingRule("UNKNOWN", "Manual-Review")
                ),
                List.of(
                        new RoutingEngineService.SubtypeOverride(
                                "MEDICAL_REPORT", "IMAGING_REPORT", "Reports/Imaging")
                ),
                List.of(
                        new RoutingEngineService.SenderOverride("+12124978948", "Care-Gaps")
                )
        );
    }

    @BeforeEach
    void setUp() {
        svc = new RoutingEngineService(fileStorageService, routingRepo);
        svc.setLiveConfig(testConfig());
    }

    // ── Plain category match ──────────────────────────────────────────────────

    @Test
    void plainCategoryMatch_pharmacyRequest() {
        var result = svc.suggest("PHARMACY_REQUEST", null, null);
        assertThat(result.folder()).isEqualTo("Pharmacy-Requests");
        assertThat(result.ruleMatched()).startsWith("category:");
    }

    @Test
    void plainCategoryMatch_priorAuthorization() {
        var result = svc.suggest("PRIOR_AUTHORIZATION", null, null);
        assertThat(result.folder()).isEqualTo("Prior-Authorization");
    }

    @Test
    void plainCategoryMatch_payerCareGap() {
        var result = svc.suggest("PAYER_CARE_GAP", null, null);
        assertThat(result.folder()).isEqualTo("Care-Gaps");
    }

    // ── Subtype override ──────────────────────────────────────────────────────

    @Test
    void subtypeOverride_imagingReport_routesToImagingSubfolder() {
        var result = svc.suggest("MEDICAL_REPORT", "IMAGING_REPORT", null);
        assertThat(result.folder()).isEqualTo("Reports/Imaging");
        assertThat(result.ruleMatched()).startsWith("subtype:");
    }

    @Test
    void subtypeOverride_nonImageReport_fallsToBaseCategory() {
        // No subtype override for RADIOLOGY_REPORT → plain category rule applies
        var result = svc.suggest("MEDICAL_REPORT", "RADIOLOGY_REPORT", null);
        assertThat(result.folder()).isEqualTo("Reports");
        assertThat(result.ruleMatched()).startsWith("category:");
    }

    // ── Sender override ───────────────────────────────────────────────────────

    @Test
    void senderOverride_e164_winsOverCategoryRule() {
        // Sender +12124978948 → Care-Gaps regardless of category
        var result = svc.suggest("PHARMACY_REQUEST", null, "+12124978948");
        assertThat(result.folder()).isEqualTo("Care-Gaps");
        assertThat(result.ruleMatched()).startsWith("sender:");
    }

    @Test
    void senderOverride_unknownSender_fallsToCategoryRule() {
        var result = svc.suggest("PHARMACY_REQUEST", null, "+19999999999");
        assertThat(result.folder()).isEqualTo("Pharmacy-Requests");
        assertThat(result.ruleMatched()).startsWith("category:");
    }

    @Test
    void senderOverride_nullSender_fallsToCategoryRule() {
        var result = svc.suggest("FOLLOW_UP", null, null);
        assertThat(result.folder()).isEqualTo("Follow-up");
    }

    // ── Unmapped category ─────────────────────────────────────────────────────

    @Test
    void unmappedCategory_fallsToManualReview() {
        var result = svc.suggest("NONEXISTENT_CATEGORY", null, null);
        assertThat(result.folder()).isEqualTo("Manual-Review");
        assertThat(result.ruleMatched()).isEqualTo("unmapped");
    }

    @Test
    void nullCategory_fallsToManualReview() {
        var result = svc.suggest(null, null, null);
        assertThat(result.folder()).isEqualTo("Manual-Review");
        assertThat(result.ruleMatched()).isEqualTo("unmapped");
    }

    // ── auto_route_disabled ───────────────────────────────────────────────────

    @Test
    void autoRouteDisabled_prescription_isTrue() {
        assertThat(svc.isAutoRouteDisabled("PRESCRIPTION")).isTrue();
    }

    @Test
    void autoRouteDisabled_pharmacyRequest_isFalse() {
        assertThat(svc.isAutoRouteDisabled("PHARMACY_REQUEST")).isFalse();
    }

    @Test
    void prescription_suggest_stillResolvesToFolder() {
        // auto_route_disabled affects whether it's filed in AUTO mode — not the folder
        var result = svc.suggest("PRESCRIPTION", null, null);
        assertThat(result.folder()).isEqualTo("Prescriptions");
    }

    // ── Mode accessor ─────────────────────────────────────────────────────────

    @Test
    void mode_defaultIsSuggest() {
        assertThat(svc.getMode()).isEqualTo("SUGGEST");
    }

    @Test
    void mode_canBeOverriddenToAuto() {
        var autoCfg = new RoutingEngineService.LiveRoutingConfig(
                "AUTO", "Manual-Review", List.of(), List.of(), List.of(), List.of());
        svc.setLiveConfig(autoCfg);
        assertThat(svc.getMode()).isEqualTo("AUTO");
    }

    // ── Resolution order: sender beats subtype ────────────────────────────────

    @Test
    void resolutionOrder_senderBeatsSubtype() {
        // Sender override should win even when a subtype override also exists
        var result = svc.suggest("MEDICAL_REPORT", "IMAGING_REPORT", "+12124978948");
        assertThat(result.folder()).isEqualTo("Care-Gaps");
        assertThat(result.ruleMatched()).startsWith("sender:");
    }

    // ── forceManualReview override (logic in AsyncProcessingService) ──────────

    @Test
    void suggest_isIndependentOfForceManualReview() {
        // suggest() always returns the config-driven folder.
        // forceManualReview queuing is handled in AsyncProcessingService, not here.
        var result = svc.suggest("PAYER_CARE_GAP", null, null);
        assertThat(result.folder()).isEqualTo("Care-Gaps");
    }
}
