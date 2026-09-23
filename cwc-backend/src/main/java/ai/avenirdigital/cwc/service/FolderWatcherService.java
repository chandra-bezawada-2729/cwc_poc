package ai.avenirdigital.cwc.service;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;

import java.io.*;
import java.nio.file.*;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.*;

/**
 * Watches {@code storage/incoming} for new files and ingests them automatically.
 *
 * <p><b>DEPRECATED — superseded by {@link InboundScannerService}.</b>
 *
 * <p>This was always a demo prop (spec §14: direct fax-server integration is a
 * non-goal, folder-drop is the demo path) and it does not survive real traffic:
 *
 * <ul>
 *   <li>It <b>skips every file already present at startup</b>, which is the
 *       opposite of "find the faxes that are yet to be processed".</li>
 *   <li>Its record of what has been handled is an in-JVM {@code Set} that dies on
 *       every restart.</li>
 *   <li>{@code WatchService} does not deliver events reliably on OneDrive-synced
 *       or SMB folders — they arrive late, coalesced, or never.</li>
 *   <li>Its only completeness test is a 2-second debounce, which a stalled sync
 *       beats, producing a half-read PDF.</li>
 *   <li>{@code PathMultipartFile.getBytes()} loads each document fully into the
 *       heap.</li>
 * </ul>
 *
 * <p>Kept only so an existing demo configuration keeps working. Do not extend it.
 * Enabling both this and {@code cwc.ingestion.inbound.enabled} is a configuration
 * error — see the startup guard below.
 *
 * @deprecated use {@link InboundScannerService}, which polls, hashes content, and
 *             keeps a durable ledger.
 */
@Deprecated(forRemoval = true)
@Slf4j
@Component
@ConditionalOnProperty(name = "cwc.ingestion.folder-watch.enabled", havingValue = "true")
public class FolderWatcherService {

    private final FileStorageService  fileStorageService;
    private final FaxIngestionService faxIngestionService;

    @Value("${cwc.ingestion.folder-watch.debounce-ms:2000}")
    private long debounceMs;

    private WatchService watchService;
    private Thread       watchThread;

    private final ScheduledExecutorService debouncer =
            Executors.newSingleThreadScheduledExecutor(r -> {
                Thread t = new Thread(r, "cwc-folder-debounce");
                t.setDaemon(true);
                return t;
            });

    /** Files seen at startup — skipped on first scan. */
    private final Set<Path> alreadySeen = ConcurrentHashMap.newKeySet();
    /** Pending debounce futures keyed by path. */
    private final Map<Path, ScheduledFuture<?>> pending = new ConcurrentHashMap<>();

    public FolderWatcherService(FileStorageService fs, FaxIngestionService fi) {
        this.fileStorageService  = fs;
        this.faxIngestionService = fi;
    }

    @Value("${cwc.ingestion.inbound.enabled:false}")
    private boolean inboundScannerEnabled;

    @PostConstruct
    public void start() throws IOException {
        if (inboundScannerEnabled) {
            // Both running would double-ingest anything dropped into
            // storage/incoming: this watcher has no content-hash ledger, so it
            // cannot see what the scanner has already claimed.
            throw new IllegalStateException(
                    "cwc.ingestion.folder-watch.enabled and cwc.ingestion.inbound.enabled "
                    + "are both true. FolderWatcherService is deprecated - set "
                    + "cwc.ingestion.folder-watch.enabled=false and use InboundScannerService.");
        }
        log.warn("[WATCHER] FolderWatcherService is DEPRECATED and will be removed. "
                 + "Migrate to cwc.ingestion.inbound.* (InboundScannerService).");

        Path incomingDir = fileStorageService.incomingDir();
        Files.createDirectories(incomingDir);

        // Record files already present — do not re-ingest them
        try (var stream = Files.list(incomingDir)) {
            stream.forEach(alreadySeen::add);
        }
        log.info("[WATCHER] Skipping {} pre-existing files in {}", alreadySeen.size(), incomingDir);

        watchService = FileSystems.getDefault().newWatchService();
        incomingDir.register(watchService,
                StandardWatchEventKinds.ENTRY_CREATE,
                StandardWatchEventKinds.ENTRY_MODIFY);

        watchThread = new Thread(() -> watchLoop(incomingDir), "cwc-folder-watcher");
        watchThread.setDaemon(true);
        watchThread.start();

        log.info("[WATCHER] Watching {} (debounce={}ms)", incomingDir, debounceMs);
    }

    @PreDestroy
    public void stop() {
        if (watchThread != null) watchThread.interrupt();
        try { if (watchService != null) watchService.close(); } catch (IOException ignored) {}
        debouncer.shutdownNow();
        log.info("[WATCHER] Stopped");
    }

    // ── Watch loop ────────────────────────────────────────────────────────────

    private void watchLoop(Path dir) {
        while (!Thread.currentThread().isInterrupted()) {
            WatchKey key;
            try {
                key = watchService.take();
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            } catch (ClosedWatchServiceException e) {
                return;
            }

            for (WatchEvent<?> event : key.pollEvents()) {
                if (event.kind() == StandardWatchEventKinds.OVERFLOW) continue;
                @SuppressWarnings("unchecked")
                Path relative = ((WatchEvent<Path>) event).context();
                Path absolute = dir.resolve(relative);
                if (!alreadySeen.contains(absolute) && isSupportedType(absolute)) {
                    scheduleIngest(absolute);
                }
            }
            key.reset();
        }
    }

    private void scheduleIngest(Path file) {
        ScheduledFuture<?> prev = pending.put(file,
                debouncer.schedule(() -> runIngest(file), debounceMs, TimeUnit.MILLISECONDS));
        if (prev != null) prev.cancel(false);
    }

    private void runIngest(Path file) {
        pending.remove(file);
        if (alreadySeen.contains(file) || !Files.isRegularFile(file)) return;
        alreadySeen.add(file);
        log.info("[WATCHER] Ingesting new file: {}", file.getFileName());
        try {
            faxIngestionService.ingest(new PathMultipartFile(file), "FOLDER_WATCH");
        } catch (Exception e) {
            log.error("[WATCHER] Ingest failed for {}: {}", file.getFileName(), e.getMessage());
        }
    }

    private static boolean isSupportedType(Path p) {
        String n = p.getFileName().toString().toLowerCase();
        return n.endsWith(".pdf") || n.endsWith(".tiff") || n.endsWith(".tif")
            || n.endsWith(".png") || n.endsWith(".jpg")  || n.endsWith(".jpeg");
    }

    // ── Minimal MultipartFile adapter wrapping a Path ─────────────────────────

    private static class PathMultipartFile implements MultipartFile {
        private final Path path;
        PathMultipartFile(Path path) { this.path = path; }

        @Override public String getName()              { return "file"; }
        @Override public String getOriginalFilename()  { return path.getFileName().toString(); }
        @Override public String getContentType()       { return "application/octet-stream"; }
        @Override public boolean isEmpty()             { return false; }
        @Override public long getSize() {
            try { return Files.size(path); } catch (IOException e) { return -1; }
        }
        @Override public byte[] getBytes() throws IOException { return Files.readAllBytes(path); }
        @Override public InputStream getInputStream() throws IOException { return Files.newInputStream(path); }
        @Override public void transferTo(File dest) throws IOException {
            Files.copy(path, dest.toPath(), StandardCopyOption.REPLACE_EXISTING);
        }
    }
}
