package ai.avenirdigital.cwc.controller;

import ai.avenirdigital.cwc.model.IngestLedgerEntity;
import ai.avenirdigital.cwc.repository.IngestLedgerRepository;
import ai.avenirdigital.cwc.service.InboundScannerService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.util.*;

/**
 * Operating surface for the inbound-folder automation.
 *
 * <p>Without these, the only way to know whether the pipeline is alive is to read
 * the log or query the database — neither of which a CWC staff member will do.
 */
@Slf4j
@RestController
@RequestMapping("/api/ingest")
@RequiredArgsConstructor
public class IngestController {

    private final IngestLedgerRepository ledgerRepo;
    /** Absent when inbound automation is disabled — endpoints degrade gracefully. */
    private final ObjectProvider<InboundScannerService> scannerProvider;

    // ── GET /api/ingest/status ────────────────────────────────────────────────

    @GetMapping("/status")
    public ResponseEntity<Map<String, Object>> status() {
        InboundScannerService scanner = scannerProvider.getIfAvailable();

        Map<String, Long> byState = new LinkedHashMap<>();
        for (Object[] row : ledgerRepo.countByState()) {
            byState.put((String) row[0], ((Number) row[1]).longValue());
        }

        long backlog = byState.entrySet().stream()
                .filter(e -> !IngestLedgerEntity.TERMINAL.contains(e.getKey()))
                .mapToLong(Map.Entry::getValue)
                .sum();

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("enabled", scanner != null);
        body.put("inboundPath", scanner != null ? scanner.inboundRoot().toString() : null);
        body.put("archivePath", scanner != null ? scanner.archiveRoot().toString() : null);
        body.put("failedPath",  scanner != null ? scanner.failedRoot().toString()  : null);
        // A moving timestamp is the only honest proof the scanner is running.
        // "enabled" alone says the bean loaded, which was still true in every
        // session where nothing was being picked up.
        body.put("lastScanAt",  scanner != null && scanner.lastScanAt() != null
                                    ? scanner.lastScanAt().toString() : null);
        body.put("lastScanNote", scanner != null ? scanner.lastScanNote() : null);
        body.put("pollIntervalMs", scanner != null ? scanner.pollIntervalMs() : null);
        body.put("byState", byState);
        body.put("backlog", backlog);
        body.put("filed", byState.getOrDefault(IngestLedgerEntity.FILED, 0L));
        body.put("quarantined", byState.getOrDefault(IngestLedgerEntity.QUARANTINED, 0L));
        body.put("recent", ledgerRepo.findTop25ByOrderByLastSeenAtDesc().stream()
                .map(IngestController::toSummary).toList());
        return ResponseEntity.ok(body);
    }

    // ── POST /api/ingest/scan ─────────────────────────────────────────────────

    /** Runs a scan cycle now rather than waiting for the next poll. */
    @PostMapping("/scan")
    public ResponseEntity<Map<String, Object>> scanNow() {
        InboundScannerService scanner = requireScanner();
        InboundScannerService.ScanSummary summary = scanner.scanOnce();
        log.info("[INGEST-API] Manual scan: candidates={} discovered={} ingested={} "
                + "deferred={} failed={} skippedKnown={} notReady={}",
                summary.candidates(), summary.discovered(), summary.ingested(),
                summary.deferred(), summary.failed(), summary.skippedKnown(),
                summary.notReady());
        // skippedKnown/notReady/candidates are returned so that a zero result is
        // self-explaining: "I saw 17 files and had already ingested all 17" is a
        // different answer from "I saw nothing", and the caller cannot otherwise
        // tell them apart.
        return ResponseEntity.ok(Map.of(
                "candidates",   summary.candidates(),
                "discovered",   summary.discovered(),
                "ingested",     summary.ingested(),
                "deferred",     summary.deferred(),
                "failed",       summary.failed(),
                "skippedKnown", summary.skippedKnown(),
                "notReady",     summary.notReady(),
                "note",         summary.note()
        ));
    }

    // ── POST /api/ingest/{id}/retry ───────────────────────────────────────────

    /**
     * Puts a failed or quarantined row back in the queue.
     *
     * <p>Clears the backoff and the attempt count. Does NOT move a quarantined
     * original back out of {@code _failed/} — that is a deliberate human action,
     * because the usual reason to retry is that someone fixed the underlying
     * problem and wants to re-drop the file themselves.
     */
    @PostMapping("/{id}/retry")
    public ResponseEntity<Map<String, Object>> retry(@PathVariable Long id) {
        IngestLedgerEntity row = ledgerRepo.findById(id)
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.NOT_FOUND, "No ledger entry " + id));

        if (IngestLedgerEntity.FILED.equals(row.getState())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "Already filed to " + row.getRoutedFolder() + "/" + row.getRoutedFileName());
        }

        row.setState(IngestLedgerEntity.DISCOVERED);
        row.setAttempts(0);
        row.setNextAttemptAt(null);
        row.setLastError(null);
        row.setCompletedAt(null);
        ledgerRepo.save(row);

        log.info("[INGEST-API] Requeued ledger {} ({})", id, row.getSourceFileName());
        return ResponseEntity.ok(Map.of(
                "id", id,
                "state", row.getState(),
                "sourceFileName", row.getSourceFileName()
        ));
    }

    // ── GET /api/ingest/ledger ────────────────────────────────────────────────

    @GetMapping("/ledger")
    public ResponseEntity<Map<String, Object>> ledger(
            @RequestParam(required = false) String state) {
        List<IngestLedgerEntity> rows = (state == null || state.isBlank())
                ? ledgerRepo.findTop25ByOrderByLastSeenAtDesc()
                : ledgerRepo.findByStateOrderByFirstSeenAtAsc(state.toUpperCase(Locale.ROOT));
        return ResponseEntity.ok(Map.of(
                "count", rows.size(),
                "items", rows.stream().map(IngestController::toSummary).toList()
        ));
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private InboundScannerService requireScanner() {
        InboundScannerService scanner = scannerProvider.getIfAvailable();
        if (scanner == null) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE,
                    "Inbound folder automation is disabled "
                    + "(set cwc.ingestion.inbound.enabled=true)");
        }
        return scanner;
    }

    /**
     * Ledger rows are safe to expose: they carry filenames, states and hashes,
     * never document content. Fax filenames contain a sender number and a
     * timestamp, never a patient identifier.
     */
    private static Map<String, Object> toSummary(IngestLedgerEntity e) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", e.getId());
        m.put("sourceFileName", e.getSourceFileName());
        m.put("state", e.getState());
        m.put("attempts", e.getAttempts());
        m.put("routedFolder", e.getRoutedFolder());
        m.put("routedFileName", e.getRoutedFileName());
        m.put("archivedPath", e.getArchivedPath());
        m.put("lastError", e.getLastError());
        m.put("firstSeenAt", e.getFirstSeenAt());
        m.put("completedAt", e.getCompletedAt());
        // Short hash prefix is enough to correlate with the logs without making
        // the response noisy.
        m.put("sha", e.getContentSha256() != null
                ? e.getContentSha256().substring(0, 12) : null);
        return m;
    }
}
