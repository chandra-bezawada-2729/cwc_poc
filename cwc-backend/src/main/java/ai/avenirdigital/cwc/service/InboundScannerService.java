package ai.avenirdigital.cwc.service;

import ai.avenirdigital.cwc.configuration.InboundProperties;
import ai.avenirdigital.cwc.model.IngestLedgerEntity;
import ai.avenirdigital.cwc.repository.IngestLedgerRepository;
import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.*;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.stream.Stream;

/**
 * Polls CWC's inbound folder and pushes anything new through the pipeline.
 *
 * <p>Supersedes {@code FolderWatcherService}, which was a demo prop with four
 * defects that made it unusable for real traffic:
 * <ul>
 *   <li>it skipped every file already present at startup — the exact opposite of
 *       "find the faxes that are yet to be processed";</li>
 *   <li>its record of what had been handled lived in a {@code Set} in the JVM and
 *       died on every restart;</li>
 *   <li>it relied on {@code WatchService}, which does not deliver events reliably
 *       on OneDrive-synced or SMB folders;</li>
 *   <li>its only test for "is this file finished" was a 2-second debounce, which a
 *       stalled sync beats easily.</li>
 * </ul>
 *
 * <p>This class replaces all four: a poll instead of a watch, a database ledger
 * instead of a Set, a hydration-and-stability probe instead of a debounce, and a
 * content hash instead of a filename.
 *
 * <p><b>Ordering note.</b> Discovery is deliberately separate from processing.
 * A cycle first records everything it can see, then works the due queue. That
 * way a crash midway leaves a truthful ledger rather than a half-scanned folder.
 */
@Slf4j
@Service
@RequiredArgsConstructor
@ConditionalOnProperty(name = "cwc.ingestion.inbound.enabled", havingValue = "true")
public class InboundScannerService {

    private final InboundProperties      props;
    private final IngestLedgerRepository ledgerRepo;
    private final FileReadinessChecker   readiness;
    private final FaxIngestionService    ingestionService;

    /**
     * Guards against a scheduled cycle overlapping a manually triggered one.
     * The DB unique constraint already makes double-claiming impossible; this
     * just avoids two cycles doing redundant hashing work at the same time.
     */
    private final AtomicBoolean cycleRunning = new AtomicBoolean(false);

    private Path inboundRoot;
    private Path archiveRoot;
    private Path failedRoot;

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    @PostConstruct
    public void init() {
        if (props.getPath() == null || props.getPath().isBlank()) {
            throw new IllegalStateException(
                    "cwc.ingestion.inbound.enabled=true but cwc.ingestion.inbound.path is not set");
        }
        inboundRoot = Paths.get(props.getPath()).toAbsolutePath().normalize();
        archiveRoot = inboundRoot.resolve(props.getArchiveDir());
        failedRoot  = inboundRoot.resolve(props.getFailedDir());

        if (!Files.isDirectory(inboundRoot)) {
            String msg = "Inbound path does not exist or is not a directory: " + inboundRoot;
            if (props.isRequirePathAtStartup()) {
                throw new IllegalStateException(msg);
            }
            log.warn("[SCANNER] {} - scanning will no-op until it appears", msg);
        }

        log.info("[SCANNER] Inbound root : {}", inboundRoot);
        log.info("[SCANNER] Archive      : {}", archiveRoot);
        log.info("[SCANNER] Quarantine   : {}", failedRoot);
        log.info("[SCANNER] Poll {}ms, stability window {}ms, max {}/cycle, {} attempts",
                props.getPollIntervalMs(), props.getMinStableMs(),
                props.getMaxPerCycle(), props.getMaxAttempts());
    }

    // ── Scheduled entry point ─────────────────────────────────────────────────

    @Scheduled(fixedDelayString = "${cwc.ingestion.inbound.poll-interval-ms:15000}")
    public void scheduledScan() {
        try {
            scanOnce();
        } catch (Exception e) {
            // A scheduled method that throws is silently cancelled by Spring in
            // some configurations. Never let one escape.
            log.error("[SCANNER] Scan cycle failed: {}", e.getMessage(), e);
        }
    }

    /**
     * Runs one full cycle: discover, then process what is due.
     *
     * @return a summary suitable for returning from the admin endpoint
     */
    public ScanSummary scanOnce() {
        if (!cycleRunning.compareAndSet(false, true)) {
            log.debug("[SCANNER] Cycle already in progress - skipping");
            return new ScanSummary(0, 0, 0, 0, 0, 0, 0, "skipped: cycle already running");
        }
        try {
            if (!Files.isDirectory(inboundRoot)) {
                recordScan("inbound path not available");
                return new ScanSummary(0, 0, 0, 0, 0, 0, 0, "inbound path not available");
            }
            DiscoverCounts d = discover();
            ProcessCounts counts = processDue();
            String note = d.candidates() > 0 && d.claimed() == 0 && counts.ingested == 0
                    ? "ok: nothing new (" + d.skippedKnown() + " already known, "
                      + d.notReady() + " not ready, of " + d.candidates() + " seen)"
                    : "ok";
            ScanSummary summary = new ScanSummary(d.claimed(), counts.ingested, counts.deferred,
                    counts.failed, d.skippedKnown(), d.notReady(), d.candidates(), note);
            recordScan(note);
            return summary;
        } finally {
            cycleRunning.set(false);
        }
    }

    // ── Phase 1: discovery ────────────────────────────────────────────────────

    /**
     * Lists candidate files and records any whose bytes we have not seen before.
     *
     * <p>Hashing is the expensive part, so it only happens for files that pass
     * the cheap name filter and the readiness probe.
     */
    private DiscoverCounts discover() {
        int claimed = 0, skippedKnown = 0, notReady = 0;
        List<Path> candidates = listCandidates();
        log.debug("[SCANNER] {} candidate files under {}", candidates.size(), inboundRoot);

        for (Path file : candidates) {
            if (claimed >= props.getMaxPerCycle()) {
                log.info("[SCANNER] Reached max-per-cycle ({}) - remaining files next cycle",
                        props.getMaxPerCycle());
                break;
            }

            FileReadinessChecker.Probe probe =
                    readiness.probe(file, null, null, props.getMinStableMs());

            if (probe.readiness() == FileReadinessChecker.Readiness.UNSUPPORTED) {
                log.debug("[SCANNER] Ignoring {} - {}", file.getFileName(), probe.detail());
                continue;
            }
            if (probe.readiness() != FileReadinessChecker.Readiness.READY) {
                // Not ready yet. We deliberately do NOT create a ledger row here:
                // the row is keyed by content hash, and we cannot hash a file we
                // cannot fully read. It will be picked up on a later cycle.
                notReady++;
                log.debug("[SCANNER] Not ready: {} - {}", file.getFileName(), probe.detail());
                continue;
            }

            String sha;
            try {
                sha = sha256(file);
            } catch (IOException e) {
                log.warn("[SCANNER] Hash failed for {}: {}", file.getFileName(), e.getMessage());
                continue;
            }

            // Atomic claim. Returns 0 when these bytes are already in the ledger,
            // which covers re-drops, re-syncs, renames, and a racing instance.
            int inserted = ledgerRepo.claim(
                    sha, file.toString(), file.getFileName().toString(),
                    probe.sizeBytes(), probe.mtimeMs(), IngestLedgerEntity.DISCOVERED,
                    IngestLedgerEntity.SCANNER);

            if (inserted > 0) {
                claimed++;
                log.info("[SCANNER] Discovered {} (sha={}...)",
                        file.getFileName(), sha.substring(0, 12));
            } else {
                skippedKnown++;
                // The bytes are known but this particular path may be a second
                // copy sitting in the folder. Leave it alone — filing it again
                // would duplicate a patient document in the outbound tree.
                announceSkip(file, sha);
            }
        }

        return new DiscoverCounts(claimed, skippedKnown, notReady, candidates.size());
    }

    /** How many announced hashes to remember before evicting the oldest. */
    private static final int ANNOUNCED_SKIP_CAP = 512;

    /**
     * Content hashes already announced as duplicates, so a file left sitting in the
     * folder is reported once instead of every poll interval.
     *
     * <p>Keyed by HASH, not by a count of skipped files. An earlier version compared
     * counts, which silently swallowed the case that matters most: drop a new
     * duplicate in while an old one is still sitting there and the count is unchanged,
     * so the scanner said nothing at the exact moment someone was watching to see
     * whether it had noticed.
     *
     * <p>Bounded, and it does not need to survive a restart — announcing a duplicate
     * once per process is the point.
     *
     * <p>Only ever touched from discover(), which runs behind the cycleRunning CAS,
     * so a single-threaded map is safe here.
     */
    private final java.util.LinkedHashMap<String, Boolean> announcedSkips =
            new java.util.LinkedHashMap<String, Boolean>(64, 0.75f, false) {
                @Override
                protected boolean removeEldestEntry(
                        java.util.Map.Entry<String, Boolean> eldest) {
                    return size() > ANNOUNCED_SKIP_CAP;
                }
            };

    /**
     * Says, once per content hash, that a file was recognised and left alone — and
     * where the earlier copy went.
     *
     * <p>"Nothing happened" is the single most confusing thing this scanner can do.
     * The file sitting motionless in the folder looks identical whether the scanner
     * recognised it, never saw it, or is dead. This line is the difference.
     */
    private void announceSkip(Path file, String sha) {
        // A file being processed RIGHT NOW is not a duplicate. Between discovery
        // and filing the original still sits in the inbound folder, so every poll
        // in that window sees it and the claim is refused. A first run of
        // seventeen faxes announced all seventeen as duplicates of themselves,
        // fifteen seconds after discovering them. Only a ledger row that reached a
        // terminal state means these bytes were genuinely handled before.
        var row = ledgerRepo.findByContentSha256(sha).orElse(null);

        if (row != null && !IngestLedgerEntity.TERMINAL.contains(row.getState())) {
            // Deliberately NOT recorded as announced, so it can still be reported
            // if the same bytes turn up again once this one has been filed.
            log.debug("[SCANNER] In progress, original still in place: {} (state={})",
                    file.getFileName(), row.getState());
            return;
        }
        if (announcedSkips.putIfAbsent(sha, Boolean.TRUE) != null) {
            log.debug("[SCANNER] Still present, already known: {}", file.getFileName());
            return;
        }
        String where = (row != null && row.getRoutedFolder() != null
                        && row.getRoutedFileName() != null)
                ? " - already filed as " + row.getRoutedFolder() + "/" + row.getRoutedFileName()
                : " - already received earlier";
        log.info("[SCANNER] Duplicate, left in place: {}{}", file.getFileName(), where);
    }

    private record DiscoverCounts(int claimed, int skippedKnown, int notReady, int candidates) {}

    private List<Path> listCandidates() {
        List<Path> out = new ArrayList<>();
        int maxDepth = props.isRecursive() ? 8 : 1;
        try (Stream<Path> stream = Files.walk(inboundRoot, maxDepth)) {
            stream.filter(Files::isRegularFile)
                  .filter(p -> !isUnderManagedFolder(p))
                  .filter(p -> !readiness.isIgnorableName(p))
                  .forEach(out::add);
        } catch (IOException e) {
            log.error("[SCANNER] Cannot list {}: {}", inboundRoot, e.getMessage());
        }
        return out;
    }

    /** Our own archive and quarantine folders are never re-scanned. */
    private boolean isUnderManagedFolder(Path p) {
        return p.startsWith(archiveRoot) || p.startsWith(failedRoot);
    }

    // ── Phase 2: process the due queue ────────────────────────────────────────

    private record ProcessCounts(int ingested, int deferred, int failed) {}

    private ProcessCounts processDue() {
        List<IngestLedgerEntity> due = ledgerRepo.findDue(OffsetDateTime.now());
        int ingested = 0, deferred = 0, failed = 0;

        for (IngestLedgerEntity row : due) {
            // Only DISCOVERED and FAILED rows need the ingest step. INGESTED and
            // PROCESSING rows are already inside the async pipeline, which owns
            // their completion.
            if (!IngestLedgerEntity.DISCOVERED.equals(row.getState())
                    && !IngestLedgerEntity.FAILED.equals(row.getState())
                    && !IngestLedgerEntity.AWAITING_HYDRATION.equals(row.getState())
                    && !IngestLedgerEntity.UNSTABLE.equals(row.getState())) {
                continue;
            }
            if (ingested >= props.getMaxPerCycle()) break;

            Path file = Paths.get(row.getSourcePath());
            if (!Files.exists(file)) {
                // Someone moved or deleted it between discovery and now. Not an
                // error — record it and stop retrying.
                markQuarantined(row, "source file no longer exists at " + row.getSourcePath());
                failed++;
                continue;
            }

            FileReadinessChecker.Probe probe = readiness.probe(
                    file, row.getSourceSizeBytes(), row.getSourceMtimeMs(), props.getMinStableMs());

            if (probe.readiness() != FileReadinessChecker.Readiness.READY) {
                row.setState(switch (probe.readiness()) {
                    case AWAITING_HYDRATION -> IngestLedgerEntity.AWAITING_HYDRATION;
                    case UNSTABLE           -> IngestLedgerEntity.UNSTABLE;
                    default                 -> IngestLedgerEntity.FAILED;
                });
                row.setSourceSizeBytes(probe.sizeBytes());
                row.setSourceMtimeMs(probe.mtimeMs());
                row.setLastError(probe.detail());
                ledgerRepo.save(row);
                deferred++;
                continue;
            }

            try {
                var result = ingestionService.ingestFromPath(
                        file, "FOLDER_SCAN", inboundRoot.toString());
                row.setFaxDocumentId(result.faxDocumentId());
                row.setState(IngestLedgerEntity.INGESTED);
                row.setLastError(null);
                row.setNextAttemptAt(null);
                ledgerRepo.save(row);
                ingested++;
                log.info("[SCANNER] Ingested {} -> trackingId={}",
                        row.getSourceFileName(), result.trackingId());
            } catch (Exception e) {
                recordFailure(row, e);
                failed++;
            }
        }
        return new ProcessCounts(ingested, deferred, failed);
    }

    // ── Failure handling ──────────────────────────────────────────────────────

    private void recordFailure(IngestLedgerEntity row, Exception e) {
        int attempts = (row.getAttempts() == null ? 0 : row.getAttempts()) + 1;
        row.setAttempts(attempts);
        row.setLastError(e.getClass().getSimpleName() + ": " + e.getMessage());

        if (attempts >= props.getMaxAttempts()) {
            log.error("[SCANNER] {} failed {} times - quarantining: {}",
                    row.getSourceFileName(), attempts, e.getMessage());
            quarantineOriginal(row);
            row.setState(IngestLedgerEntity.QUARANTINED);
            row.setCompletedAt(OffsetDateTime.now());
        } else {
            long backoff = props.getRetryBackoffMs() * (1L << (attempts - 1));
            row.setState(IngestLedgerEntity.FAILED);
            row.setNextAttemptAt(OffsetDateTime.now().plusNanos(backoff * 1_000_000L));
            log.warn("[SCANNER] {} failed (attempt {}/{}), retrying in {}ms: {}",
                    row.getSourceFileName(), attempts, props.getMaxAttempts(),
                    backoff, e.getMessage());
        }
        ledgerRepo.save(row);
    }

    private void markQuarantined(IngestLedgerEntity row, String reason) {
        log.error("[SCANNER] Quarantining {}: {}", row.getSourceFileName(), reason);
        row.setState(IngestLedgerEntity.QUARANTINED);
        row.setLastError(reason);
        row.setCompletedAt(OffsetDateTime.now());
        ledgerRepo.save(row);
    }

    /** Moves a poison file out of the inbound folder so the scan stops tripping on it. */
    private void quarantineOriginal(IngestLedgerEntity row) {
        try {
            Path source = Paths.get(row.getSourcePath());
            if (!Files.exists(source)) return;
            Files.createDirectories(failedRoot);
            Path dest = uniqueDestination(failedRoot, source.getFileName().toString());
            Files.move(source, dest, StandardCopyOption.ATOMIC_MOVE);
            row.setArchivedPath(dest.toString());
            log.info("[SCANNER] Quarantined original -> {}", dest);
        } catch (IOException e) {
            // Not fatal: the ledger already marks it QUARANTINED, so the scanner
            // will not retry even though the file is still sitting there.
            log.warn("[SCANNER] Could not move quarantined file: {}", e.getMessage());
        }
    }

    /** Appends " (2)", " (3)"… until the destination is free. */
    static Path uniqueDestination(Path dir, String fileName) {
        Path candidate = dir.resolve(fileName);
        if (!Files.exists(candidate)) return candidate;
        int dot = fileName.lastIndexOf('.');
        String stem = dot > 0 ? fileName.substring(0, dot) : fileName;
        String ext  = dot > 0 ? fileName.substring(dot) : "";
        for (int n = 2; n < 1000; n++) {
            candidate = dir.resolve(stem + " (" + n + ")" + ext);
            if (!Files.exists(candidate)) return candidate;
        }
        return dir.resolve(System.currentTimeMillis() + "__" + fileName);
    }

    // ── Hashing ───────────────────────────────────────────────────────────────

    /** Streaming SHA-256 — a fax can be 25 MB and must not be held in memory. */
    static String sha256(Path file) throws IOException {
        MessageDigest digest;
        try {
            digest = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
        byte[] buffer = new byte[64 * 1024];
        try (InputStream in = Files.newInputStream(file)) {
            int n;
            while ((n = in.read(buffer)) > 0) {
                digest.update(buffer, 0, n);
            }
        }
        return HexFormat.of().formatHex(digest.digest());
    }

    // ── Accessors used by the filing service and the admin controller ─────────

    public Path inboundRoot() { return inboundRoot; }
    public Path archiveRoot() { return archiveRoot; }
    public Path failedRoot()  { return failedRoot; }

    /**
     * When the last cycle finished, and what it concluded.
     *
     * <p>Volatile because the scheduler thread writes and an HTTP thread reads.
     * These exist for the status endpoint: knowing a folder is CONFIGURED is not
     * the same as knowing the scanner is ALIVE, and a fax sitting still in the
     * folder looks identical under both. The UI cannot tell them apart without
     * a timestamp that moves.
     */
    private volatile OffsetDateTime lastScanAt;
    private volatile String lastScanNote;

    private void recordScan(String note) {
        lastScanAt = OffsetDateTime.now();
        lastScanNote = note;
    }

    public OffsetDateTime lastScanAt()  { return lastScanAt; }
    public String         lastScanNote(){ return lastScanNote; }
    public long           pollIntervalMs() { return props.getPollIntervalMs(); }

    public Optional<IngestLedgerEntity> ledgerForFaxDocument(Long faxDocumentId) {
        return ledgerRepo.findByFaxDocumentId(faxDocumentId);
    }

    /**
     * Outcome of one cycle.
     *
     * <p>{@code skippedKnown} and {@code notReady} exist because without them a
     * cycle that saw files and correctly skipped every one is reported as all
     * zeros — identical to a scanner that is not running at all. That ambiguity
     * cost real debugging time: the honest answer to "nothing happened" is
     * "I looked at 17 files and had already seen all 17".
     */
    public record ScanSummary(
            int discovered,
            int ingested,
            int deferred,
            int failed,
            int skippedKnown,
            int notReady,
            int candidates,
            String note
    ) {}
}
