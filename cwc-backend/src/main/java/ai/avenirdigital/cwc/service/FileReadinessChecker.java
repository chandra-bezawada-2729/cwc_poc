package ai.avenirdigital.cwc.service;

import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.Arrays;
import java.util.Locale;

/**
 * Decides whether a file in the inbound folder is actually ready to be read.
 *
 * <p>Three different failure modes have to be separated, because they need
 * different responses:
 *
 * <ol>
 *   <li><b>Not hydrated.</b> OneDrive Files On-Demand leaves a placeholder that
 *       reports the file's FULL logical size while holding no local bytes.
 *       {@code Files.size()} lies about these. Response: wait and retry — the
 *       bytes are coming.</li>
 *   <li><b>Still arriving.</b> The file is mid-copy or mid-sync, so its size or
 *       mtime is still moving. Response: wait for it to settle.</li>
 *   <li><b>Not a document we handle.</b> Wrong magic bytes, zero length, or a
 *       sync sidecar. Response: skip permanently, do not retry.</li>
 * </ol>
 *
 * <p>Collapsing these three into "skip it" — which a naive debounce does — is how
 * you end up either ingesting half a PDF or silently never ingesting a real one.
 */
@Slf4j
@Component
public class FileReadinessChecker {

    /** Outcome of a readiness check. */
    public enum Readiness {
        /** Bytes are local, file has settled, magic bytes are valid. */
        READY,
        /** OneDrive placeholder — retry once the sync engine has fetched it. */
        AWAITING_HYDRATION,
        /** Size or mtime still moving — retry after the stability window. */
        UNSTABLE,
        /** Not a document type we process, or empty. Do not retry. */
        UNSUPPORTED
    }

    public record Probe(
            Readiness readiness,
            long sizeBytes,
            long mtimeMs,
            String detail
    ) {}

    /**
     * Filename patterns produced by sync engines and editors that must never be
     * ingested. OneDrive in particular churns out .tmp and ~$ files constantly.
     */
    private static final String[] IGNORED_PREFIXES = { "~$", ".~", "._" };
    private static final String[] IGNORED_SUFFIXES = {
            ".tmp", ".temp", ".partial", ".crdownload", ".part",
            ".lock", ".ds_store", "thumbs.db", ".filepart"
    };

    private static final String[] SUPPORTED_EXTENSIONS = {
            ".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg"
    };

    /** How many bytes we read to confirm the file really is what it claims. */
    private static final int MAGIC_PROBE_BYTES = 8;

    // Magic numbers. A OneDrive placeholder cannot satisfy these without the
    // bytes being local, which makes this check double as a hydration test.
    private static final byte[] PDF  = { '%', 'P', 'D', 'F' };
    private static final byte[] PNG  = { (byte) 0x89, 'P', 'N', 'G' };
    private static final byte[] JPEG = { (byte) 0xFF, (byte) 0xD8, (byte) 0xFF };
    private static final byte[] TIFF_LE = { 'I', 'I', 0x2A, 0x00 };
    private static final byte[] TIFF_BE = { 'M', 'M', 0x00, 0x2A };

    /**
     * True when the name looks like a sync artefact or an unsupported type.
     * Cheap — call this before touching the file at all.
     */
    public boolean isIgnorableName(Path path) {
        String name = path.getFileName().toString().toLowerCase(Locale.ROOT);
        for (String p : IGNORED_PREFIXES) {
            if (name.startsWith(p)) return true;
        }
        for (String s : IGNORED_SUFFIXES) {
            if (name.endsWith(s)) return true;
        }
        return !isSupportedExtension(name);
    }

    public boolean isSupportedExtension(String lowerName) {
        for (String ext : SUPPORTED_EXTENSIONS) {
            if (lowerName.endsWith(ext)) return true;
        }
        return false;
    }

    /**
     * Probes one file.
     *
     * @param path           the file on disk
     * @param lastSeenSize   size recorded on the previous poll, or null if first sight
     * @param lastSeenMtime  mtime recorded on the previous poll, or null if first sight
     * @param minStableMs    how old the last modification must be before we trust it
     */
    public Probe probe(Path path, Long lastSeenSize, Long lastSeenMtime, long minStableMs) {
        BasicFileAttributes attrs;
        try {
            attrs = Files.readAttributes(path, BasicFileAttributes.class);
        } catch (IOException e) {
            // The file vanished between listing and probing — a normal race with
            // a sync engine, not an error worth shouting about.
            return new Probe(Readiness.UNSTABLE, -1, -1,
                    "cannot read attributes: " + e.getMessage());
        }

        if (!attrs.isRegularFile()) {
            return new Probe(Readiness.UNSUPPORTED, 0, 0, "not a regular file");
        }

        long size  = attrs.size();
        long mtime = attrs.lastModifiedTime().toMillis();

        if (size == 0) {
            // Could be a placeholder that has not been populated, or a genuinely
            // empty file someone dropped. Treat as hydration-pending: if it is
            // truly empty it will simply keep failing and eventually quarantine,
            // which is the right outcome for a zero-byte "fax".
            return new Probe(Readiness.AWAITING_HYDRATION, 0, mtime, "zero bytes on disk");
        }

        // ── Stability: has anything moved since the last poll? ────────────────
        if (lastSeenSize != null && lastSeenMtime != null) {
            if (lastSeenSize != size || lastSeenMtime != mtime) {
                return new Probe(Readiness.UNSTABLE, size, mtime,
                        "changed since last poll (size %d->%d, mtime %d->%d)"
                                .formatted(lastSeenSize, size, lastSeenMtime, mtime));
            }
        } else {
            // First sighting: we have no previous observation to compare against,
            // so require the file to have been untouched for the stability window
            // before we accept it. Without this, a file first seen mid-copy would
            // be ingested on its very first poll.
            long ageMs = System.currentTimeMillis() - mtime;
            if (ageMs < minStableMs) {
                return new Probe(Readiness.UNSTABLE, size, mtime,
                        "first sighting, modified %dms ago (< %dms)".formatted(ageMs, minStableMs));
            }
        }

        // ── Hydration + type: can we actually read the leading bytes? ─────────
        byte[] head = new byte[MAGIC_PROBE_BYTES];
        int read;
        try (InputStream in = Files.newInputStream(path)) {
            read = in.readNBytes(head, 0, MAGIC_PROBE_BYTES);
        } catch (IOException e) {
            // On Windows this is what an unhydrated OneDrive placeholder throws
            // when Files On-Demand cannot fetch it (offline, quota, throttled).
            // It is recoverable — the sync engine may succeed later.
            log.debug("[READINESS] Cannot read head of {}: {}", path.getFileName(), e.getMessage());
            return new Probe(Readiness.AWAITING_HYDRATION, size, mtime,
                    "head unreadable: " + e.getMessage());
        }

        if (read < 4) {
            return new Probe(Readiness.AWAITING_HYDRATION, size, mtime,
                    "only %d bytes readable despite reported size %d".formatted(read, size));
        }

        if (!hasKnownMagic(head)) {
            return new Probe(Readiness.UNSUPPORTED, size, mtime,
                    "unrecognised magic bytes: " + hex(head, read));
        }

        return new Probe(Readiness.READY, size, mtime, "ok");
    }

    private static boolean hasKnownMagic(byte[] head) {
        return startsWith(head, PDF)
            || startsWith(head, PNG)
            || startsWith(head, JPEG)
            || startsWith(head, TIFF_LE)
            || startsWith(head, TIFF_BE);
    }

    private static boolean startsWith(byte[] data, byte[] prefix) {
        if (data.length < prefix.length) return false;
        return Arrays.equals(data, 0, prefix.length, prefix, 0, prefix.length);
    }

    private static String hex(byte[] data, int len) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < Math.min(len, data.length); i++) {
            sb.append(String.format("%02X ", data[i]));
        }
        return sb.toString().trim();
    }
}
