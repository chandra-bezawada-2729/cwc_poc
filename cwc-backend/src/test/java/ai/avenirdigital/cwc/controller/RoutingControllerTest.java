package ai.avenirdigital.cwc.controller;

import ai.avenirdigital.cwc.service.RoutingEngineService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;
import java.util.Map;

import static org.hamcrest.Matchers.*;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

/**
 * @WebMvcTest for RoutingController — tests HTTP layer only (spec §13).
 * RoutingEngineService is mocked.
 */
@WebMvcTest(RoutingController.class)
class RoutingControllerTest {

    @Autowired MockMvc mvc;

    @MockBean RoutingEngineService routingEngine;

    private Map<String, Object> sampleConfig() {
        return Map.of(
                "mode",               "SUGGEST",
                "manualReviewFolder", "Manual-Review",
                "autoRouteDisabled",  List.of("PRESCRIPTION"),
                "rules",              List.of(Map.of("category", "PAYER_CARE_GAP", "folder", "Care-Gaps")),
                "subtypeOverrides",   List.of(),
                "senderOverrides",    List.of()
        );
    }

    // ── GET /api/routing/config ───────────────────────────────────────────────

    @Test
    void getConfig_returns200WithConfigMap() throws Exception {
        given(routingEngine.getConfigMap()).willReturn(sampleConfig());

        mvc.perform(get("/api/routing/config"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.mode").value("SUGGEST"))
                .andExpect(jsonPath("$.manualReviewFolder").value("Manual-Review"))
                .andExpect(jsonPath("$.autoRouteDisabled[0]").value("PRESCRIPTION"))
                .andExpect(jsonPath("$.rules[0].category").value("PAYER_CARE_GAP"));
    }

    // ── POST /api/routing/reload ──────────────────────────────────────────────

    @Test
    void reload_callsReload_returns200() throws Exception {
        given(routingEngine.getMode()).willReturn("SUGGEST");
        given(routingEngine.getConfigMap()).willReturn(sampleConfig());

        mvc.perform(post("/api/routing/reload"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("reloaded"))
                .andExpect(jsonPath("$.mode").value("SUGGEST"));

        verify(routingEngine).reload();
    }

    // ── POST /api/routing/mode ────────────────────────────────────────────────

    @Test
    void setMode_auto_returns200() throws Exception {
        given(routingEngine.getMode()).willReturn("AUTO");

        mvc.perform(post("/api/routing/mode")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"mode":"AUTO"}
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.mode").value("AUTO"))
                .andExpect(jsonPath("$.status").value("ok"));

        verify(routingEngine).setRuntimeMode("AUTO");
    }

    @Test
    void setMode_invalidValue_returns400() throws Exception {
        given(routingEngine.getMode()).willReturn("SUGGEST");
        org.mockito.Mockito.doThrow(new IllegalArgumentException("mode must be SUGGEST or AUTO, got: INVALID"))
                .when(routingEngine).setRuntimeMode("INVALID");

        mvc.perform(post("/api/routing/mode")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"mode":"INVALID"}
                                """))
                .andExpect(status().isBadRequest());
    }

    @Test
    void setMode_suggest_returns200() throws Exception {
        given(routingEngine.getMode()).willReturn("SUGGEST");

        mvc.perform(post("/api/routing/mode")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"mode":"SUGGEST"}
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.mode").value("SUGGEST"));
    }
}
