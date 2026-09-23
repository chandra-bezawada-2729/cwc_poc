package ai.avenirdigital.cwc.controller;



import ai.avenirdigital.cwc.service.RoutingEngineService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * Routing config management — spec §6, §10.
 * GET  /api/routing/config  — returns the effective in-memory routing config.
 * POST /api/routing/reload  — re-reads routing-config.yml from classpath.
 */
@Slf4j
@RestController
@RequestMapping("/api/routing")
@RequiredArgsConstructor
public class RoutingController {

    private final RoutingEngineService routingEngine;

    @GetMapping("/config")
    public ResponseEntity<Map<String, Object>> getConfig() {
        return ResponseEntity.ok(routingEngine.getConfigMap());
    }

    @PostMapping("/reload")
    public ResponseEntity<Map<String, Object>> reload() {
        routingEngine.reload();
        log.info("[ROUTING] Config reloaded via API");
        return ResponseEntity.ok(Map.of(
                "status",  "reloaded",
                "mode",    routingEngine.getMode(),
                "config",  routingEngine.getConfigMap()
        ));
    }

    /**
     * POST /api/routing/mode  body: {"mode":"AUTO"} or {"mode":"SUGGEST"}
     *
     * In-memory mode toggle for demos without editing routing-config.yml.
     * The next POST /api/routing/reload restores the YAML-defined mode.
     */
    @PostMapping("/mode")
    public ResponseEntity<Map<String, Object>> setMode(@RequestBody Map<String, String> body) {
        String mode = body.get("mode");
        try {
            routingEngine.setRuntimeMode(mode);
        } catch (IllegalArgumentException e) {
            return ResponseEntity.badRequest().body(Map.of("error", e.getMessage()));
        }
        log.info("[ROUTING] Mode set to {} via API", mode);
        return ResponseEntity.ok(Map.of(
                "status", "ok",
                "mode",   routingEngine.getMode()
        ));
    }
}
