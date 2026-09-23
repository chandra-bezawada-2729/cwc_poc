package ai.avenirdigital.cwc.service;

import ai.avenirdigital.cwc.configuration.IngestionProperties;
import ai.avenirdigital.cwc.model.FaxDocumentEntity;
import ai.avenirdigital.cwc.repository.FaxDocumentRepository;
import ai.avenirdigital.cwc.util.FaxFilenameParser;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.ZoneId;
import java.util.UUID;

/**
 * Orchestrates a single-file upload:
 *   1. Parse filename metadata using the configured fax-server timezone (§16.7).
 *   2. Persist entity with RECEIVED status (micro-transaction via JPA repo).
 *   3. Copy bytes to the absolute-resolved storage/incoming/ (§16.5).
 *   4. Fire async validation pipeline (entity committed before async starts).
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class FaxIngestionService {

    private final FaxDocumentRepository  faxRepo;
    private final FileStorageService     fileStorageService;
    private final AsyncProcessingService asyncProcessingService;
    private final IngestionProperties   ingestionProperties;

    public record IngestResult(UUID trackingId, String originalFileName, String status) {}

    /**
     * Extended result for folder-scanned ingests. The scanner needs the database
     * id so it can link its ledger row to the pipeline row.
     */
    public record ScannedIngestResult(
            UUID trackingId,
            Long faxDocumentId,
            String originalFileName,
            String status
    ) {}

    /**
     * Ingests a file already on disk, discovered by {@link InboundScannerService}.
     *
     * <p>Distinct from {@link #ingest(MultipartFile, String)} in one way that
     * matters: the bytes are <b>streamed</b> from the source into storage rather
     * than materialised as a byte array. The old FolderWatcherService wrapped
     * each file in a MultipartFile adapter whose {@code getBytes()} pulled the
     * whole document into the heap — fine for a 20 KB demo fax, not fine for a
     * 25 MB scanned packet arriving alongside twenty others.
     *
     * <p>The inbound original is NOT moved here. It stays put until the document
     * has been successfully filed, so a crash mid-pipeline leaves the fax where
     * the scanner can find it again.
     *
     * @param source       absolute path to the discovered file
     * @param uploadSource provenance tag stored on the document row
     * @param sourceFolder the inbound root, recorded for multi-folder triage
     */
    public ScannedIngestResult ingestFromPath(Path source, String uploadSource, String sourceFolder)
            throws IOException {

        UUID   trackingId   = UUID.randomUUID();
        String originalName = source.getFileName().toString();
        long   sizeBytes    = Files.size(source);

        ZoneId faxZone = ZoneId.of(ingestionProperties.getFaxServerZone());
        FaxFilenameParser.ParsedFilename parsed = FaxFilenameParser.parse(originalName, faxZone);

        FaxDocumentEntity entity = new FaxDocumentEntity();
        entity.setTrackingId(trackingId);
        entity.setOriginalFileName(originalName);
        entity.setStoredPath("PENDING");
        entity.setFileSizeBytes(sizeBytes);
        entity.setUploadSource(uploadSource);
        entity.setSourceFolder(sourceFolder);
        entity.setProcessingStatus("RECEIVED");

        if (parsed != null) {
            entity.setSenderFaxNumber(parsed.getSenderFaxNumber());
            if (parsed.getReceivedAt() != null) {
                entity.setReceivedAt(parsed.getReceivedAt().toOffsetDateTime());
            }
        }

        faxRepo.save(entity);

        String storedPath = fileStorageService.storeFromPath(source, trackingId);
        entity.setStoredPath(storedPath);
        faxRepo.save(entity);

        log.info("[INGEST] trackingId={} file={} size={}B source={} zone={}",
                trackingId, originalName, sizeBytes, uploadSource, faxZone);

        asyncProcessingService.processDocument(trackingId);
        return new ScannedIngestResult(trackingId, entity.getId(), originalName, "RECEIVED");
    }

    public IngestResult ingest(MultipartFile file, String uploadSource) throws IOException {
        UUID   trackingId    = UUID.randomUUID();
        String originalName  = file.getOriginalFilename() != null
                ? file.getOriginalFilename() : "upload.pdf";

        // §16.7: zone from config, never hardcoded
        ZoneId faxZone = ZoneId.of(ingestionProperties.getFaxServerZone());
        FaxFilenameParser.ParsedFilename parsed = FaxFilenameParser.parse(originalName, faxZone);

        FaxDocumentEntity entity = new FaxDocumentEntity();
        entity.setTrackingId(trackingId);
        entity.setOriginalFileName(originalName);
        entity.setStoredPath("PENDING");
        entity.setFileSizeBytes(file.getSize());
        entity.setUploadSource(uploadSource);
        entity.setProcessingStatus("RECEIVED");

        if (parsed != null) {
            // §16.6: stored in E.164 form
            entity.setSenderFaxNumber(parsed.getSenderFaxNumber());
            if (parsed.getReceivedAt() != null) {
                entity.setReceivedAt(parsed.getReceivedAt().toOffsetDateTime());
            }
        }

        faxRepo.save(entity);

        // §16.5: stored_path is relative to the absolute base
        String storedPath = fileStorageService.store(file, trackingId);
        entity.setStoredPath(storedPath);
        faxRepo.save(entity);

        log.info("[INGEST] trackingId={} file={} size={}B zone={}",
                trackingId, originalName, file.getSize(), faxZone);

        asyncProcessingService.processDocument(trackingId);
        return new IngestResult(trackingId, originalName, "RECEIVED");
    }
}
