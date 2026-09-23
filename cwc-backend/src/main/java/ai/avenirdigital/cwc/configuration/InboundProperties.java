package ai.avenirdigital.cwc.configuration;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Configuration for the inbound-folder automation.
 *
 * <p>Bound from {@code cwc.ingestion.inbound.*}. Every value that could differ
 * between CWC's environment and a dev machine lives here — nothing about the
 * folder layout is hardcoded in the scanner.
 */
@Data
@ConfigurationProperties(prefix = "cwc.ingestion.inbound")
public class InboundProperties {

    /** Master switch. Off by default so an existing deployment is unchanged. */
    private boolean enabled = false;

    /**
     * Absolute path to CWC's inbound folder — the OneDrive/SharePoint-synced
     * directory faxes land in. Required when enabled.
     */
    private String path;

    /** Scan sub-directories too. Archive and failure folders are always skipped. */
    private boolean recursive = true;

    /**
     * How often to poll, in milliseconds.
     *
     * <p>Polling rather than {@code WatchService} is deliberate: Java's watch API
     * is unreliable on SMB shares and OneDrive-synced folders, where events are
     * delayed, coalesced, or simply never delivered.
     */
    private long pollIntervalMs = 15_000;

    /**
     * A file must be untouched for this long before we accept it on first
     * sighting. Guards against ingesting a partially-synced document.
     */
    private long minStableMs = 10_000;

    /** Subfolder of the inbound root that successfully filed originals move to. */
    private String archiveDir = "_processed";

    /** Subfolder of the inbound root that quarantined originals move to. */
    private String failedDir = "_failed";

    /** Group archived originals into dated subfolders (_processed/2026-09-12/). */
    private boolean archiveByDate = true;

    /** Attempts before a file is quarantined rather than retried forever. */
    private int maxAttempts = 3;

    /** Base for exponential retry backoff. Attempt n waits base * 2^(n-1). */
    private long retryBackoffMs = 60_000;

    /**
     * Maximum files ingested per scan cycle. Bounds the blast radius of pointing
     * the scanner at a folder with a large backlog, and keeps one cycle from
     * saturating the async pool and the AI service.
     */
    private int maxPerCycle = 25;

    /** Fail fast at startup if the configured inbound path does not exist. */
    private boolean requirePathAtStartup = true;
}
