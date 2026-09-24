package ai.avenirdigital.cwc.controller;

import ai.avenirdigital.cwc.model.FaxDocumentEntity;
import ai.avenirdigital.cwc.model.RoutingDecisionEntity;
import ai.avenirdigital.cwc.repository.ClassificationResultRepository;
import ai.avenirdigital.cwc.repository.FaxDocumentRepository;
import ai.avenirdigital.cwc.repository.RoutingDecisionRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.io.IOException;
import java.nio.file.*;
import java.nio.file.attribute.BasicFileAttributes;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.*;

/**
 * Read-only view of the fax folders on disk, for the web interface.
 *
 *   GET /api/storage/browse
 *
 * The server has no desktop, so staff cannot open a file manager to see what
 * is actually in Inbound, the working folder and Outbound. Without that, "the
 * fax was filed" is a claim the application makes about itself. This reports
 * what is genuinely on disk, read fresh each time.
 *
 * Deliberately read-only: nothing here creates, moves or deletes anything.
 *
 * Files are matched back to documents by their filed path, so a row in
 * Outbound can link to the fax it came from. A file with no match is still
 * listed — that is the interesting case, since it means something is on disk
 * that the application does not know about.
 */
@Slf4j
@RestController
@RequiredArgsConstructor
public class StorageBrowseController {

    /** Guard against walking a huge tree and returning an unusable payload. */
    private static final int MAX_FILES_PER_FOLDER = 500;

    private final FaxDocumentRepository          faxRepo;
    private final RoutingDecisionRepository      routingRepo;
    private final ClassificationResultRepository classificationRepo;

    @Value("${cwc.storage.base-path:/data}")
    private String basePath;

    @Value("${cwc.storage.incoming-dir:incoming}")
    private String incomingDir;

    @Value("${cwc.storage.routed-dir:Outbound}")
    private String routedDir;

    @Value("${cwc.ingestion.inbound.path:}")
    private String inboundPath;

    // ── Response shapes ──────────────────────────────────────────────────────

    public record FileEntry(
            String  name,
            long    sizeBytes,
            String  modifiedAt,
            /** Set when this file on disk is a document the application filed. */
            String  trackingId,
            String  originalFileName,
            String  category
    ) {}

    public record FolderEntry(
            String           name,
            String           path,
            int              fileCount,
            long             totalBytes,
            List<FileEntry>  files,
            /** Sub-folders, e.g. the categories under Outbound. */
            List<FolderEntry> folders,
            boolean          truncated
    ) {}

    public record BrowseResponse(
            String            basePath,
            String            generatedAt,
            List<FolderEntry> roots,
            List<String>      warnings
    ) {}

    // ── GET /api/storage/browse ──────────────────────────────────────────────

    @GetMapping("/api/storage/browse")
    public BrowseResponse browse() {
        Path base = Paths.get(basePath).toAbsolutePath().normalize();
        List<String> warnings = new ArrayList<>();

        // Filed path -> the document it belongs to. Built once; the alternative
        // is a database round trip per file, which on a busy Outbound folder is
        // hundreds of queries to draw one page.
        Map<String, FaxDocumentEntity> byPath = new HashMap<>();
        Map<Long, FaxDocumentEntity>   docsById = new HashMap<>();
        for (FaxDocumentEntity d : faxRepo.findAll()) docsById.put(d.getId(), d);
        for (RoutingDecisionEntity rd : routingRepo.findAll()) {
            FaxDocumentEntity d = docsById.get(rd.getFaxDocumentId());
            if (d == null) continue;
            if (rd.getFinalPath() != null && !rd.getFinalPath().isBlank()) {
                byPath.put(normalise(rd.getFinalPath()), d);
            }
            if (rd.getFinalFileName() != null) {
                // Fallback for installations where final_path was not recorded.
                byPath.putIfAbsent("name:" + rd.getFinalFileName(), d);
            }
        }

        // Category per document, so a filed file can say what it was judged to
        // be without a query per row.
        Map<Long, String> categoryByDoc = new HashMap<>();
        classificationRepo.findAll().forEach(cr ->
                categoryByDoc.put(cr.getFaxDocumentId(), cr.getDetectedCategory()));

        List<FolderEntry> roots = new ArrayList<>();

        Path inbound = (inboundPath == null || inboundPath.isBlank())
                ? base.resolve("Inbound")
                : Paths.get(inboundPath).toAbsolutePath().normalize();
        roots.add(walk(inbound, "Inbound", byPath, categoryByDoc, 1, warnings));
        roots.add(walk(base.resolve(incomingDir), "Working (incoming)", byPath, categoryByDoc, 0, warnings));
        roots.add(walk(base.resolve(routedDir),  "Outbound (filed)",    byPath, categoryByDoc, 1, warnings));

        return new BrowseResponse(
                base.toString(),
                OffsetDateTime.now().toString(),
                roots,
                warnings
        );
    }

    // ── Walking ──────────────────────────────────────────────────────────────

    /**
     * @param depth how many levels of sub-folder to descend. Outbound needs one
     *              (the category folders); the working folder is flat.
     */
    private FolderEntry walk(Path dir, String label,
                             Map<String, FaxDocumentEntity> byPath,
                             Map<Long, String> categoryByDoc,
                             int depth, List<String> warnings) {

        List<FileEntry>   files   = new ArrayList<>();
        List<FolderEntry> folders = new ArrayList<>();
        long total = 0;
        boolean truncated = false;

        if (!Files.isDirectory(dir)) {
            warnings.add(label + ": not found at " + dir);
            return new FolderEntry(label, dir.toString(), 0, 0, files, folders, false);
        }

        try (DirectoryStream<Path> stream = Files.newDirectoryStream(dir)) {
            for (Path p : stream) {
                if (Files.isDirectory(p)) {
                    if (depth > 0) {
                        folders.add(walk(p, p.getFileName().toString(), byPath, categoryByDoc, depth - 1, warnings));
                    }
                    continue;
                }
                // Hidden files are operator scratch (.wtest, .DS_Store), never faxes.
                if (p.getFileName().toString().startsWith(".")) continue;
                if (files.size() >= MAX_FILES_PER_FOLDER) { truncated = true; continue; }

                BasicFileAttributes a = Files.readAttributes(p, BasicFileAttributes.class);
                FaxDocumentEntity doc = byPath.get(normalise(p.toString()));
                if (doc == null) doc = byPath.get("name:" + p.getFileName());

                files.add(new FileEntry(
                        p.getFileName().toString(),
                        a.size(),
                        Instant.ofEpochMilli(a.lastModifiedTime().toMillis())
                               .atZone(ZoneId.systemDefault()).toOffsetDateTime().toString(),
                        doc == null ? null : doc.getTrackingId().toString(),
                        doc == null ? null : doc.getOriginalFileName(),
                        doc == null ? null : categoryByDoc.get(doc.getId())
                ));
                total += a.size();
            }
        } catch (IOException e) {
            // A folder we cannot read is worth showing as a warning rather than
            // failing the whole page: the other folders are still useful.
            warnings.add(label + ": " + e.getMessage());
        }

        files.sort(Comparator.comparing(FileEntry::modifiedAt).reversed());
        folders.sort(Comparator.comparing(FolderEntry::name));

        int count = files.size();
        for (FolderEntry f : folders) { count += f.fileCount(); total += f.totalBytes(); }

        return new FolderEntry(label, dir.toString(), count, total, files, folders, truncated);
    }

    private static String normalise(String path) {
        return path.replace('\\', '/');
    }
}
