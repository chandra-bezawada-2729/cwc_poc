package ai.avenirdigital.cwc.service;

import ai.avenirdigital.cwc.service.FileReadinessChecker.Probe;
import ai.avenirdigital.cwc.service.FileReadinessChecker.Readiness;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Unit tests for FileReadinessChecker.
 *
 * <p>The distinction these lock down is AWAITING_HYDRATION (retry — the bytes are
 * still coming) versus UNSUPPORTED (give up — this will never be a document).
 * Confusing the two either retries a dead file forever or abandons a real fax that
 * OneDrive simply had not downloaded yet, so every test asserts which of the two it
 * is NOT as well as which it is.
 *
 * <p>These replace a manual OneDrive test: the placeholder conditions are all
 * reproducible on an ordinary filesystem, and forcing a genuine Files On-Demand
 * placeholder turned out not to be reliably possible anyway (see OPEN_QUESTIONS
 * item 27).
 */
class FileReadinessCheckerTest {

    private static final long MIN_STABLE_MS = 10_000;

    private final FileReadinessChecker checker = new FileReadinessChecker();

    /** Backdates mtime so a first sighting clears the stability window. */
    private static Path settled(Path file) throws IOException {
        Files.setLastModifiedTime(file, FileTime.fromMillis(System.currentTimeMillis() - 60_000));
        return file;
    }

    private Probe probeFirstSighting(Path file) {
        return checker.probe(file, null, null, MIN_STABLE_MS);
    }

    // ── AWAITING_HYDRATION: the bytes are not here yet, keep retrying ──────────

    @Test
    void zeroByteFileIsAwaitingHydrationNotUnsupported(@TempDir Path dir) throws IOException {
        // An unpopulated placeholder looks exactly like this.
        Path file = settled(Files.createFile(dir.resolve("placeholder.pdf")));

        Probe probe = probeFirstSighting(file);

        assertThat(probe.readiness()).isEqualTo(Readiness.AWAITING_HYDRATION);
        assertThat(probe.readiness()).isNotEqualTo(Readiness.UNSUPPORTED);
        assertThat(probe.sizeBytes()).isZero();
        assertThat(probe.detail()).contains("zero bytes");
    }

    @Test
    void truncatedHeadIsAwaitingHydration(@TempDir Path dir) throws IOException {
        // Fewer bytes readable than a magic number needs. A real Files On-Demand
        // placeholder reports the full logical size while yielding nothing; what
        // matters here is the branch, which fires whenever the head is short.
        Path file = settled(Files.write(dir.resolve("partial.pdf"), new byte[] { '%', 'P' }));

        Probe probe = probeFirstSighting(file);

        assertThat(probe.readiness()).isEqualTo(Readiness.AWAITING_HYDRATION);
        assertThat(probe.readiness()).isNotEqualTo(Readiness.UNSUPPORTED);
        assertThat(probe.detail()).contains("2 bytes readable");
    }

    // ── UNSUPPORTED: this will never become a document, stop retrying ──────────

    @Test
    void badMagicIsUnsupportedNotAwaitingHydration(@TempDir Path dir) throws IOException {
        // Corrupt or mislabelled. Retrying this forever would wedge the scanner on
        // one file, so it must be refused permanently rather than deferred.
        Path file = settled(Files.write(dir.resolve("corrupt.pdf"), "NOTAPDF!".getBytes()));

        Probe probe = probeFirstSighting(file);

        assertThat(probe.readiness()).isEqualTo(Readiness.UNSUPPORTED);
        assertThat(probe.readiness()).isNotEqualTo(Readiness.AWAITING_HYDRATION);
        assertThat(probe.detail()).contains("unrecognised magic bytes");
    }

    // ── READY ─────────────────────────────────────────────────────────────────

    @Test
    void goodPdfIsReady(@TempDir Path dir) throws IOException {
        Path file = settled(Files.write(dir.resolve("real.pdf"), "%PDF-1.7\nbody".getBytes()));

        Probe probe = probeFirstSighting(file);

        assertThat(probe.readiness()).isEqualTo(Readiness.READY);
        assertThat(probe.detail()).isEqualTo("ok");
        assertThat(probe.sizeBytes()).isEqualTo(Files.size(file));
    }

    // ── UNSTABLE: still arriving, wait for it to settle ────────────────────────

    @Test
    void growingFileIsUnstable(@TempDir Path dir) throws IOException {
        Path file = Files.write(dir.resolve("growing.pdf"), "%PDF-1.7\n".getBytes());
        long seenSize  = Files.size(file);
        long seenMtime = Files.getLastModifiedTime(file).toMillis();

        Files.write(file, "more pages arriving".getBytes(), java.nio.file.StandardOpenOption.APPEND);
        // Backdated so the result cannot come from the stability window instead.
        settled(file);

        Probe probe = checker.probe(file, seenSize, seenMtime, MIN_STABLE_MS);

        assertThat(probe.readiness()).isEqualTo(Readiness.UNSTABLE);
        assertThat(probe.detail()).contains("changed since last poll");
        assertThat(probe.sizeBytes()).isGreaterThan(seenSize);
    }

    @Test
    void freshFirstSightingIsUnstableEvenWhenOtherwiseValid(@TempDir Path dir) throws IOException {
        // Not backdated: a valid PDF written this instant could still be mid-copy.
        Path file = Files.write(dir.resolve("justarrived.pdf"), "%PDF-1.7\nbody".getBytes());

        Probe probe = probeFirstSighting(file);

        assertThat(probe.readiness()).isEqualTo(Readiness.UNSTABLE);
        assertThat(probe.detail()).contains("first sighting");
    }

    @Test
    void unchangedSinceLastPollSkipsTheStabilityWindow(@TempDir Path dir) throws IOException {
        // Already observed once with the same size and mtime, so the age heuristic
        // does not apply and a freshly written file is accepted.
        Path file = Files.write(dir.resolve("settled.pdf"), "%PDF-1.7\nbody".getBytes());

        Probe probe = checker.probe(file, Files.size(file),
                Files.getLastModifiedTime(file).toMillis(), MIN_STABLE_MS);

        assertThat(probe.readiness()).isEqualTo(Readiness.READY);
    }

    @Test
    void vanishedFileIsUnstableNotUnsupported(@TempDir Path dir) {
        // Losing a race with the sync engine is normal. Calling it UNSUPPORTED
        // would permanently skip a fax that merely moved a moment too early.
        Probe probe = probeFirstSighting(dir.resolve("gone.pdf"));

        assertThat(probe.readiness()).isEqualTo(Readiness.UNSTABLE);
        assertThat(probe.readiness()).isNotEqualTo(Readiness.UNSUPPORTED);
        assertThat(probe.detail()).contains("cannot read attributes");
    }

    // ── Name filter: sync artefacts must never reach the probe ─────────────────

    @Test
    void syncArtefactsAndUnsupportedTypesAreIgnorableByName(@TempDir Path dir) {
        assertThat(checker.isIgnorableName(dir.resolve("~$draft.pdf"))).isTrue();
        assertThat(checker.isIgnorableName(dir.resolve("fax.pdf.tmp"))).isTrue();
        assertThat(checker.isIgnorableName(dir.resolve("fax.crdownload"))).isTrue();
        assertThat(checker.isIgnorableName(dir.resolve("notes.docx"))).isTrue();
        assertThat(checker.isIgnorableName(dir.resolve("fax.pdf"))).isFalse();
        assertThat(checker.isIgnorableName(dir.resolve("FAX.PDF"))).isFalse();
        assertThat(checker.isIgnorableName(dir.resolve("scan.tiff"))).isFalse();
    }
}
