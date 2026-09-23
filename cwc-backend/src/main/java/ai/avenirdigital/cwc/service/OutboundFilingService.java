package ai.avenirdigital.cwc.service;

import ai.avenirdigital.cwc.configuration.InboundProperties;
import ai.avenirdigital.cwc.model.FaxDocumentEntity;
import ai.avenirdigital.cwc.model.IngestLedgerEntity;
import ai.avenirdigital.cwc.model.RoutingDecisionEntity;
import ai.avenirdigital.cwc.repository.IngestLedgerRepository;
import ai.avenirdigital.cwc.repository.RoutingDecisionRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.file.*;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;

/**
 * Completes the automated path: write the classified fax into CWC's outbound
 * tree under its suggested name, then move the inbound original into the
 * archive so the inbound folder drains.
 *
 * <p>The two halves are deliberately ordered and non-atomic in a specific way:
 * <b>file first, archive second</b>. If the process dies between them the
 * outbound copy exists and the original is still in the inbound folder — the
 * ledger will retry, the content hash will match, and nothing is filed twice.
 * The reverse order could lose a document entirely.
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class OutboundFilingService {

    private final RoutingEngineService         routingEngine;
    private final FileStorageService           fileStorageService;
    private final RoutingDecisionRepository    routingRepo;
    private final IngestLedgerRepository       ledgerRepo;
    private final InboundProperties            inboundProps;
    /**
     * Optional because the scanner bean only exists when inbound automation is
     * enabled. Filing still works without it for manually uploaded documents —
     * there is simply no inbound original to archive.
     */
    private final ObjectProvider<InboundScannerService> scannerProvider;

    private static final DateTimeFormatter ARCHIVE_DATE = DateTimeFormatter.ofPattern("yyyy-MM-dd");

    public record FilingResult(
            boolean filed,
            String folder,
            String fileName,
            String path,
            String archivedPath,
            String note
    ) {}

    /**
     * Files one classified document and archives its inbound original.
     *
     * @param doc          the fax document
     * @param rd           its routing decision (already carries suggested folder + name)
     * @param toReviewFolder true when the confidence gate said this must go to
     *                       Manual-Review rather than its predicted folder
     */
    public FilingResult fileAndArchive(FaxDocumentEntity doc,
                                       RoutingDecisionEntity rd,
                                       boolean toReviewFolder) {

        String folder = toReviewFolder
                ? routingEngine.getManualReviewFolder()
                : rd.getSuggestedFolder();

        String fileName = rd.getSuggestedFileName() != null
                ? rd.getSuggestedFileName()
                : doc.getOriginalFileName();

        Path source = fileStorageService.resolve(doc.getStoredPath());
        Path dest;
        try {
            dest = routingEngine.fileDocument(
                    source, doc.getTrackingId().toString(), fileName, folder);
        } catch (IOException e) {
            log.error("[FILING] Failed to file trackingId={} into {}: {}",
                    doc.getTrackingId(), folder, e.getMessage());
            rd.setRoutingStatus("FAILED");
            routingRepo.save(rd);
            return new FilingResult(false, folder, fileName, null, null,
                    "file failed: " + e.getMessage());
        }

        rd.setFinalFolder(folder);
        rd.setFinalPath(dest.toString());
        rd.setFinalFileName(dest.getFileName().toString());
        rd.setRoutingStatus(toReviewFolder ? "MANUAL_REVIEW" : "CONFIRMED");
        rd.setDecidedBy("SYSTEM");
        routingRepo.save(rd);

        log.info("[FILING] trackingId={} -> {}/{}",
                doc.getTrackingId(), folder, dest.getFileName());

        // ── Archive the inbound original ─────────────────────────────────────
        String archived = archiveOriginal(doc, folder, dest);

        return new FilingResult(true, folder, dest.getFileName().toString(),
                dest.toString(), archived,
                toReviewFolder ? "filed to review folder" : "filed");
    }

    /**
     * Moves the inbound original into {@code _processed/yyyy-MM-dd/} and closes
     * out its ledger row.
     *
     * <p>Returns null when there is nothing to archive — a manual upload has no
     * inbound original, and that is not a failure.
     */
    private String archiveOriginal(FaxDocumentEntity doc, String folder, Path dest) {
        InboundScannerService scanner = scannerProvider.getIfAvailable();
        if (scanner == null) {
            return null;   // inbound automation not enabled
        }

        var ledgerOpt = ledgerRepo.findByFaxDocumentId(doc.getId());
        if (ledgerOpt.isEmpty()) {
            return null;   // not a scanned document
        }
        IngestLedgerEntity row = ledgerOpt.get();

        row.setRoutedFolder(folder);
        row.setRoutedFileName(dest.getFileName().toString());
        row.setRoutedPath(dest.toString());

        String archivedPath = null;
        try {
            Path original = Paths.get(row.getSourcePath());
            if (Files.exists(original)) {
                Path archiveDir = scanner.archiveRoot();
                if (inboundProps.isArchiveByDate()) {
                    archiveDir = archiveDir.resolve(LocalDate.now().format(ARCHIVE_DATE));
                }
                Files.createDirectories(archiveDir);
                Path target = InboundScannerService.uniqueDestination(
                        archiveDir, original.getFileName().toString());
                moveWithFallback(original, target);
                archivedPath = target.toString();
                row.setArchivedPath(archivedPath);
                log.info("[FILING] Archived original {} -> {}",
                        original.getFileName(), target);
            } else {
                log.debug("[FILING] Original already gone: {}", row.getSourcePath());
            }
            row.setState(IngestLedgerEntity.FILED);
            row.setCompletedAt(OffsetDateTime.now());
            row.setLastError(null);
        } catch (IOException e) {
            // The outbound copy is already written, so the document is safe.
            // Leave the row short of FILED so the next cycle retries the archive
            // rather than re-filing — re-filing is prevented by the content hash.
            log.warn("[FILING] Could not archive original for trackingId={}: {}",
                    doc.getTrackingId(), e.getMessage());
            row.setState(IngestLedgerEntity.FAILED);
            row.setLastError("archive failed: " + e.getMessage());
            row.setAttempts((row.getAttempts() == null ? 0 : row.getAttempts()) + 1);
        }
        ledgerRepo.save(row);
        return archivedPath;
    }

    /**
     * ATOMIC_MOVE fails across volumes, which happens when the inbound folder and
     * the archive sit on different drives — or, on Windows, when OneDrive holds a
     * transient lock. Fall back to copy-then-delete.
     */
    private static void moveWithFallback(Path source, Path target) throws IOException {
        try {
            Files.move(source, target, StandardCopyOption.ATOMIC_MOVE);
        } catch (FileSystemException e) {
            Files.copy(source, target, StandardCopyOption.REPLACE_EXISTING);
            try {
                Files.delete(source);
            } catch (IOException delete) {
                // Copy succeeded, delete did not. The file is archived but still
                // present inbound; the content hash stops it being reprocessed.
                Files.deleteIfExists(target.resolveSibling(target.getFileName() + ".partial"));
                throw new IOException(
                        "archived to " + target + " but could not remove the original: "
                        + delete.getMessage(), delete);
            }
        }
    }
}
