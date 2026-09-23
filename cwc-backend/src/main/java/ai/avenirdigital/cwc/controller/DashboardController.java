package ai.avenirdigital.cwc.controller;

import ai.avenirdigital.cwc.repository.ClassificationResultRepository;
import ai.avenirdigital.cwc.repository.FaxDocumentRepository;
import ai.avenirdigital.cwc.repository.RoutingDecisionRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;
import java.util.*;

/**
 * GET /api/dashboard/stats — aggregate counts for the Inbox stat row.
 *
 * Returns:
 *   byStatus        — count of fax_documents per processing_status
 *   byCategory      — count of classification_results per detected_category
 *   byBand          — count per confidence_band
 *   autoRouteRate   — fraction of routing_decisions that were CONFIRMED without an override
 *   overrideRate    — fraction of confirmed decisions that were overrides
 *   avgLatencyMs    — mean classification latency across all completed classifications
 *   actionDueSoon   — docs with action_required=true and response_deadline within 7 days
 */
@RestController
@RequestMapping("/api/dashboard")
@RequiredArgsConstructor
public class DashboardController {

    private final FaxDocumentRepository          faxRepo;
    private final ClassificationResultRepository clsRepo;
    private final RoutingDecisionRepository      rdRepo;

    @GetMapping("/stats")
    public ResponseEntity<Map<String, Object>> stats() {
        Map<String, Object> out = new LinkedHashMap<>();

        // ── Counts by processing_status ───────────────────────────────────────
        Map<String, Long> byStatus = new LinkedHashMap<>();
        faxRepo.countByProcessingStatus().forEach(row ->
                byStatus.put((String) row[0], (Long) row[1]));
        out.put("byStatus", byStatus);
        out.put("total",    byStatus.values().stream().mapToLong(v -> v).sum());

        // ── Counts by category ────────────────────────────────────────────────
        Map<String, Long> byCategory = new LinkedHashMap<>();
        clsRepo.countByCategory().forEach(row ->
                byCategory.put(row[0] == null ? "UNKNOWN" : (String) row[0], (Long) row[1]));
        out.put("byCategory", byCategory);

        // ── Counts by confidence band ─────────────────────────────────────────
        Map<String, Long> byBand = new LinkedHashMap<>();
        clsRepo.countByBand().forEach(row ->
                byBand.put(row[0] == null ? "UNCLASSIFIED" : (String) row[0], (Long) row[1]));
        out.put("byBand", byBand);

        // ── Auto-route / override rates ───────────────────────────────────────
        List<Object[]> rdCounts = rdRepo.countByStatus();
        long confirmed  = sum(rdCounts, "CONFIRMED");
        long overridden = sum(rdCounts, "OVERRIDDEN");
        long total      = confirmed + overridden;
        double autoRate     = total > 0 ? (double) confirmed  / total : 0.0;
        double overrideRate = total > 0 ? (double) overridden / total : 0.0;
        out.put("autoRouteRate",   round2(autoRate));
        out.put("overrideRate",    round2(overrideRate));
        out.put("totalRouted",     total);

        // ── Average classification latency ────────────────────────────────────
        Double avgLatency = clsRepo.avgLatencyMs();
        out.put("avgLatencyMs", avgLatency != null ? Math.round(avgLatency) : null);

        // ── Action-required items due within 7 days ───────────────────────────
        LocalDate now    = LocalDate.now();
        LocalDate cutoff = now.plusDays(7);
        long dueSoon = clsRepo.countActionDueSoon(now, cutoff);
        out.put("actionDueSoon", dueSoon);

        return ResponseEntity.ok(out);
    }

    // ── Helpers ────────────────────────────────────────────────────────────────

    private static long sum(List<Object[]> rows, String status) {
        return rows.stream()
                .filter(r -> status.equals(r[0]))
                .mapToLong(r -> (Long) r[1])
                .sum();
    }

    private static double round2(double v) {
        return Math.round(v * 100.0) / 100.0;
    }
}
