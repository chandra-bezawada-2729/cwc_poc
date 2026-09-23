package ai.avenirdigital.cwc.controller;

import ai.avenirdigital.cwc.controller.response.FaxDetailResponse;
import ai.avenirdigital.cwc.controller.response.FaxSummaryResponse;
import ai.avenirdigital.cwc.controller.response.UploadResponse;
import ai.avenirdigital.cwc.model.ClassificationResultEntity;
import ai.avenirdigital.cwc.model.IngestLedgerEntity;
import ai.avenirdigital.cwc.model.FaxDocumentEntity;
import ai.avenirdigital.cwc.model.RoutingDecisionEntity;
import ai.avenirdigital.cwc.repository.ClassificationResultRepository;
import ai.avenirdigital.cwc.repository.ExtractedMetadataRepository;
import ai.avenirdigital.cwc.repository.FaxDocumentRepository;
import ai.avenirdigital.cwc.repository.IngestLedgerRepository;
import ai.avenirdigital.cwc.repository.RoutingDecisionRepository;
import com.fasterxml.jackson.databind.ObjectMapper;
import ai.avenirdigital.cwc.service.AsyncProcessingService;
import ai.avenirdigital.cwc.service.ExtractionService;
import ai.avenirdigital.cwc.service.FaxIngestionService;
import ai.avenirdigital.cwc.service.FileStorageService;
import ai.avenirdigital.cwc.service.RoutingEngineService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.core.io.PathResource;
import org.springframework.core.io.Resource;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.OffsetDateTime;
import java.util.*;
import java.util.stream.Collectors;

@Slf4j
@RestController
@RequestMapping("/api/faxes")
@RequiredArgsConstructor
public class FaxController {

    private final FaxIngestionService              ingestionService;
    private final AsyncProcessingService           asyncProcessingService;
    private final FaxDocumentRepository            faxRepo;
    private final ClassificationResultRepository   classificationRepo;
    private final RoutingDecisionRepository        routingDecisionRepo;
    private final FileStorageService               fileStorageService;
    private final RoutingEngineService             routingEngine;
    private final ExtractionService                extractionService;
    private final ExtractedMetadataRepository      extractedMetadataRepo;
    private final ObjectMapper                     objectMapper;
    private final IngestLedgerRepository           ledgerRepo;

    // ── POST /api/faxes/upload ─────────────────────────────────────────────────

    @PostMapping("/upload")
    public ResponseEntity<UploadResponse> upload(
            @RequestParam("files") MultipartFile[] files) {

        List<UploadResponse.Accepted> accepted = new ArrayList<>();
        List<UploadResponse.Rejected> rejected = new ArrayList<>();

        for (MultipartFile file : files) {
            String name = file.getOriginalFilename() != null ? file.getOriginalFilename() : "upload";
            try {
                // Duplicate guard. The folder scanner has refused re-filing by
                // content hash since V6; this path had no check at all, so the same
                // fax uploaded twice produced two copies in the outbound tree,
                // distinguished only by a " (2)" suffix — which reads as a filename
                // collision between two DIFFERENT documents, not as a duplicate.
                // Re-uploading a fax you are not sure went through is a normal thing
                // for staff to do, so this has to be caught rather than tidied up after.
                String sha = sha256(file);
                Optional<IngestLedgerEntity> existing = ledgerRepo.findByContentSha256(sha);
                if (existing.isPresent()) {
                    rejected.add(new UploadResponse.Rejected(name, duplicateReason(existing.get())));
                    log.info("[UPLOAD] Rejected duplicate file={} sha={}... existing ledger id={}",
                            name, sha.substring(0, 12), existing.get().getId());
                    continue;
                }

                FaxIngestionService.IngestResult result =
                        ingestionService.ingest(file, "MANUAL_UPLOAD");

                // Record the fingerprint so the NEXT upload of these bytes is caught,
                // and so the scanner will not re-file them if the same fax later
                // arrives in the inbound folder. Claimed after a successful ingest:
                // a failed upload must not leave a hash that blocks the retry.
                // ON CONFLICT DO NOTHING covers two uploads racing on the same bytes.
                faxRepo.findByTrackingId(result.trackingId()).ifPresent(doc -> {
                    int claimed = ledgerRepo.claim(
                            sha, doc.getStoredPath(), doc.getOriginalFileName(),
                            doc.getFileSizeBytes(), null,
                            IngestLedgerEntity.INGESTED, IngestLedgerEntity.UPLOAD);
                    if (claimed > 0) {
                        ledgerRepo.findByContentSha256(sha).ifPresent(row -> {
                            row.setFaxDocumentId(doc.getId());
                            ledgerRepo.save(row);
                        });
                    }
                });
                accepted.add(new UploadResponse.Accepted(
                        result.trackingId().toString(),
                        result.originalFileName(),
                        result.status()
                ));
            } catch (IOException e) {
                log.error("[UPLOAD] Failed to store file={} error={}", name, e.getMessage());
                rejected.add(new UploadResponse.Rejected(name, "IO_ERROR:" + e.getMessage()));
            }
        }

        return ResponseEntity.status(accepted.isEmpty() ? HttpStatus.BAD_REQUEST : HttpStatus.CREATED)
                .body(new UploadResponse(accepted, rejected));
    }

    // ── GET /api/faxes ─────────────────────────────────────────────────────────

    @GetMapping
    public ResponseEntity<Map<String, Object>> list(
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String category,
            @RequestParam(required = false) String q,
            @RequestParam(required = false) String dateFrom,
            @RequestParam(required = false) String dateTo,
            @RequestParam(defaultValue = "0")  int page,
            @RequestParam(defaultValue = "20") int size) {

        Specification<FaxDocumentEntity> spec = buildSpec(status, q, dateFrom, dateTo);
        Pageable pageable = PageRequest.of(page, size, Sort.by(Sort.Direction.DESC, "createdAt"));
        Page<FaxDocumentEntity> result = faxRepo.findAll(spec, pageable);

        List<FaxDocumentEntity> docs = result.getContent();
        List<Long> ids = docs.stream().map(FaxDocumentEntity::getId).toList();

        // Batch-fetch classification and routing data to avoid N+1
        Map<Long, ClassificationResultEntity> clsByDocId =
                classificationRepo.findAllByFaxDocumentIdIn(ids).stream()
                        .collect(Collectors.toMap(ClassificationResultEntity::getFaxDocumentId, c -> c,
                                (a, b) -> a));

        // Latest routing decision per document
        Map<Long, RoutingDecisionEntity> rdByDocId = new HashMap<>();
        routingDecisionRepo.findAllByFaxDocumentIdIn(ids).forEach(rd ->
                rdByDocId.merge(rd.getFaxDocumentId(), rd,
                        (a, b) -> a.getDecidedAt().isAfter(b.getDecidedAt()) ? a : b));

        // Extracted sender fields, where extraction has already run. Absent rows
        // are normal: extraction fires after filing, so a fax can appear in the
        // Inbox before its metadata exists.
        Map<Long, FaxSummaryResponse.ExtractedSender> senderByDocId = new HashMap<>();
        extractedMetadataRepo.findAllByFaxDocumentIdIn(ids).forEach(em ->
                senderByDocId.put(em.getFaxDocumentId(), readExtractedSender(em.getCoreJson())));

        List<FaxSummaryResponse> items = docs.stream()
                .map(d -> FaxSummaryResponse.from(
                        d,
                        clsByDocId.get(d.getId()),
                        rdByDocId.get(d.getId()),
                        senderByDocId.getOrDefault(d.getId(),
                                FaxSummaryResponse.ExtractedSender.EMPTY)))
                .toList();

        return ResponseEntity.ok(Map.of(
                "content",       items,
                "totalElements", result.getTotalElements(),
                "totalPages",    result.getTotalPages(),
                "number",        result.getNumber(),
                "size",          result.getSize()
        ));
    }

    // ── GET /api/faxes/{trackingId} ───────────────────────────────────────────

    @GetMapping("/{trackingId}")
    public ResponseEntity<FaxDetailResponse> detail(@PathVariable String trackingId) {
        FaxDocumentEntity entity = findByTrackingId(trackingId);
        ClassificationResultEntity cls =
                classificationRepo.findByFaxDocumentId(entity.getId()).orElse(null);
        RoutingDecisionEntity rd =
                routingDecisionRepo.findTopByFaxDocumentIdOrderByDecidedAtDesc(entity.getId()).orElse(null);
        return ResponseEntity.ok(FaxDetailResponse.from(entity, cls, rd));
    }

    // ── GET /api/faxes/{trackingId}/file ──────────────────────────────────────

    @GetMapping("/{trackingId}/file")
    public ResponseEntity<Resource> streamFile(@PathVariable String trackingId) {
        FaxDocumentEntity entity = findByTrackingId(trackingId);

        Path filePath = fileStorageService.resolve(entity.getStoredPath());
        if (!filePath.toFile().exists()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "File not found on disk");
        }

        Resource resource = new PathResource(filePath);
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_DISPOSITION,
                        "inline; filename=\"" + entity.getOriginalFileName() + "\"")
                .contentType(MediaType.APPLICATION_PDF)
                .body(resource);
    }

    // ── POST /api/faxes/{trackingId}/confirm ──────────────────────────────────

    @PostMapping("/{trackingId}/confirm")
    public ResponseEntity<Map<String, Object>> confirm(
            @PathVariable String trackingId,
            @RequestBody ConfirmRequest body) {

        FaxDocumentEntity doc = findByTrackingId(trackingId);

        RoutingDecisionEntity rd =
                routingDecisionRepo.findTopByFaxDocumentIdOrderByDecidedAtDesc(doc.getId())
                        .orElseThrow(() -> new ResponseStatusException(HttpStatus.CONFLICT,
                                "No routing suggestion found - run classification first"));

        if ("CONFIRMED".equals(rd.getRoutingStatus()) || "OVERRIDDEN".equals(rd.getRoutingStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "Document already routed to: " + rd.getFinalFolder());
        }

        String requestedFolder = body.folder();
        if (requestedFolder == null || requestedFolder.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "folder is required");
        }

        // The human may change the folder, the name, or both. They are separate
        // signals: a folder override says the taxonomy is wrong, a name override
        // says the specification is wrong. Counting them together would hide
        // which half of the model needs work.
        String suggestedName = rd.getSuggestedFileName() != null
                ? rd.getSuggestedFileName()
                : doc.getOriginalFileName();
        String requestedName = (body.fileName() != null && !body.fileName().isBlank())
                ? sanitiseFileName(body.fileName(), doc.getOriginalFileName())
                : suggestedName;

        boolean folderOverride = !requestedFolder.equals(rd.getSuggestedFolder());
        boolean nameOverride   = !requestedName.equals(suggestedName);
        boolean isOverride     = folderOverride || nameOverride;

        if (isOverride && (body.overrideReason() == null || body.overrideReason().isBlank())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "overrideReason is required when the chosen folder or filename "
                    + "differs from the suggestion");
        }

        // File the document
        Path source = fileStorageService.resolve(doc.getStoredPath());
        Path dest;
        try {
            dest = routingEngine.fileDocument(
                    source,
                    doc.getTrackingId().toString(),
                    requestedName,
                    requestedFolder);
        } catch (IOException e) {
            log.error("[CONFIRM] Filing failed trackingId={}: {}", trackingId, e.getMessage());
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR,
                    "Failed to file document: " + e.getMessage());
        }

        // Update routing decision
        rd.setFinalFolder(requestedFolder);
        rd.setFinalPath(dest.toString());
        // The name on disk is the authority — a collision suffix may have been
        // appended after the human chose.
        rd.setFinalFileName(dest.getFileName().toString());
        rd.setFileNameOverridden(nameOverride);
        rd.setRoutingStatus(isOverride ? "OVERRIDDEN" : "CONFIRMED");
        rd.setDecidedBy(body.decidedBy() != null ? body.decidedBy() : "MANUAL");
        if (isOverride) {
            rd.setOverrideReason(body.overrideReason());
        }
        routingDecisionRepo.save(rd);

        // Mark fax as ROUTED
        doc.setProcessingStatus("ROUTED");
        faxRepo.save(doc);

        log.info("[CONFIRM] trackingId={} folder={} name={} status={} folderOverride={} nameOverride={}",
                trackingId, requestedFolder, rd.getFinalFileName(),
                rd.getRoutingStatus(), folderOverride, nameOverride);

        // Same rule as the AUTO path: extraction starts once the document is
        // filed and ROUTED, never before, and never blocks the response.
        extractionService.autoExtractAfterFiling(doc.getTrackingId());

        Map<String, Object> response = new HashMap<>();
        response.put("trackingId",     trackingId);
        response.put("status",         rd.getRoutingStatus());
        response.put("folder",         requestedFolder);
        response.put("fileName",       rd.getFinalFileName());
        response.put("path",           dest.toString());
        response.put("isOverride",     isOverride);
        response.put("folderOverride", folderOverride);
        response.put("nameOverride",   nameOverride);
        return ResponseEntity.ok(response);
    }

    // ── POST /api/faxes/{trackingId}/reclassify ───────────────────────────────

    /**
     * Re-runs the full pipeline (validate → OCR → classify → gate → suggest) on an
     * existing document that is stuck in a non-terminal state (RECEIVED, OCR, CLASSIFYING)
     * or was previously classified.
     *
     * Rules:
     *  - CONFIRMED and OVERRIDDEN documents are already filed; return 409 (use /confirm to
     *    override the routing instead).
     *  - Non-final routing decisions (SUGGESTED, MANUAL_REVIEW, FAILED) are deleted so that
     *    AsyncProcessingService can create a fresh one.
     *  - The classification_results row is kept; AsyncProcessingService will overwrite it.
     *  - Returns 202 Accepted immediately; the pipeline runs asynchronously.
     */
    @PostMapping("/{trackingId}/reclassify")
    public ResponseEntity<Map<String, Object>> reclassify(@PathVariable String trackingId) {
        FaxDocumentEntity doc = findByTrackingId(trackingId);

        // Guard: do not re-run a document that has already been filed
        routingDecisionRepo.findTopByFaxDocumentIdOrderByDecidedAtDesc(doc.getId())
                .ifPresent(rd -> {
                    if ("CONFIRMED".equals(rd.getRoutingStatus())
                            || "OVERRIDDEN".equals(rd.getRoutingStatus())) {
                        throw new ResponseStatusException(HttpStatus.CONFLICT,
                                "Document is already filed to '" + rd.getFinalFolder()
                                + "'. Use /confirm to override routing instead.");
                    }
                });

        // Verify the file still exists on disk
        Path filePath = fileStorageService.resolve(doc.getStoredPath());
        if (!filePath.toFile().exists()) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,
                    "Source file not found on disk - cannot reclassify without the original file");
        }

        // Delete non-final routing decisions so the pipeline creates a fresh one
        List<RoutingDecisionEntity> allRd = routingDecisionRepo.findAllByFaxDocumentIdIn(
                List.of(doc.getId()));
        allRd.stream()
                .filter(rd -> !"CONFIRMED".equals(rd.getRoutingStatus())
                           && !"OVERRIDDEN".equals(rd.getRoutingStatus()))
                .forEach(routingDecisionRepo::delete);

        // Reset to RECEIVED so the async pipeline runs cleanly
        doc.setProcessingStatus("RECEIVED");
        doc.setErrorReason(null);
        faxRepo.save(doc);

        log.info("[RECLASSIFY] Queuing trackingId={} for re-classification", trackingId);
        asyncProcessingService.processDocument(doc.getTrackingId());

        return ResponseEntity.accepted().body(Map.of(
                "trackingId", trackingId,
                "message",    "Re-classification queued. Poll GET /api/faxes/" + trackingId + " for status."
        ));
    }

    // ── Request body ──────────────────────────────────────────────────────────

    /**
     * @param folder         destination folder the human chose
     * @param fileName       name the human chose; null keeps the suggested name.
     *                       Taxonomy v2.0 — CWC's manual step renames as well as
     *                       files, so the confirm action must accept both.
     * @param overrideReason required when folder OR fileName differs from the suggestion
     * @param decidedBy      user identifier, defaults to MANUAL
     */
    public record ConfirmRequest(String folder, String fileName,
                                 String overrideReason, String decidedBy) {
        /** Pre-v2.0 three-argument form; keeps the suggested filename. */
        public ConfirmRequest(String folder, String overrideReason, String decidedBy) {
            this(folder, null, overrideReason, decidedBy);
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    /**
     * Streams the upload through SHA-256 rather than calling getBytes().
     *
     * <p>A 25 MB scanned packet arriving alongside twenty others should not be
     * materialised on the heap just to fingerprint it — the same reason
     * ingestFromPath streams instead of buffering.
     */
    private static String sha256(MultipartFile file) throws IOException {
        try (InputStream in = file.getInputStream()) {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] buffer = new byte[8192];
            int read;
            while ((read = in.read(buffer)) != -1) {
                digest.update(buffer, 0, read);
            }
            StringBuilder hex = new StringBuilder(64);
            for (byte b : digest.digest()) {
                hex.append(Character.forDigit((b >> 4) & 0xF, 16));
                hex.append(Character.forDigit(b & 0xF, 16));
            }
            return hex.toString();
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }

    /**
     * Says where the earlier copy went, not merely that one exists.
     *
     * <p>"Duplicate" on its own makes someone hunt for the original to check it
     * was filed correctly. The folder and filed name answer that in the message.
     */
    private String duplicateReason(IngestLedgerEntity row) {
        String where = (row.getRoutedFolder() != null && row.getRoutedFileName() != null)
                ? "already filed as " + row.getRoutedFolder() + "/" + row.getRoutedFileName()
                : "already received and currently " + row.getState();
        String tracking = Optional.ofNullable(row.getFaxDocumentId())
                .flatMap(faxRepo::findById)
                .map(d -> " (tracking " + d.getTrackingId() + ")")
                .orElse("");
        return "DUPLICATE: " + where + tracking;
    }


    /**
     * Pull only the sender fields out of an extraction's core JSON.
     *
     * <p>Reads three keys and ignores everything else, so patient name, DOB and
     * MRN cannot reach the Inbox projection by accident as the extraction schema
     * grows. A malformed blob yields EMPTY — a list view must not 500 because one
     * row's metadata is bad.
     */
    private FaxSummaryResponse.ExtractedSender readExtractedSender(String coreJson) {
        if (coreJson == null || coreJson.isBlank()) {
            return FaxSummaryResponse.ExtractedSender.EMPTY;
        }
        try {
            Map<?, ?> core = objectMapper.readValue(coreJson, Map.class);
            return new FaxSummaryResponse.ExtractedSender(
                    asString(core.get("senderOrganization")),
                    asString(core.get("senderFax")),
                    asString(core.get("destinationFax")));
        } catch (Exception e) {
            log.warn("[INBOX] Unreadable extraction core JSON; sender fields omitted: {}",
                    e.getMessage());
            return FaxSummaryResponse.ExtractedSender.EMPTY;
        }
    }

    private static String asString(Object v) {
        if (v == null) return null;
        String s = v.toString().trim();
        return s.isEmpty() || "null".equalsIgnoreCase(s) ? null : s;
    }

    /** Characters Windows and SharePoint reject in a filename, plus control chars. */
    private static final java.util.regex.Pattern ILLEGAL_FILENAME_CHARS =
            java.util.regex.Pattern.compile("[\\\\/:*?\"<>|\\x00-\\x1f]");

    /**
     * Sanitises a human-supplied filename. The confirm endpoint writes to disk,
     * so a name arriving over HTTP must never be able to contain a path
     * separator or traverse out of the destination folder.
     *
     * <p>Preserves the original file's extension if the human omitted one.
     */
    private static String sanitiseFileName(String requested, String originalFileName) {
        String cleaned = ILLEGAL_FILENAME_CHARS.matcher(requested).replaceAll("").trim();
        // Strip any leading dots so ".." and hidden-file names cannot survive.
        cleaned = cleaned.replaceAll("^\\.+", "").trim();
        if (cleaned.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "fileName contains no usable characters");
        }
        if (cleaned.length() > 120) {
            cleaned = cleaned.substring(0, 120).trim();
        }
        if (!cleaned.contains(".") && originalFileName != null) {
            int dot = originalFileName.lastIndexOf('.');
            if (dot > 0) {
                cleaned = cleaned + originalFileName.substring(dot).toLowerCase();
            }
        }
        return cleaned;
    }

    private FaxDocumentEntity findByTrackingId(String trackingId) {
        UUID uuid;
        try {
            uuid = UUID.fromString(trackingId);
        } catch (IllegalArgumentException e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid trackingId format");
        }
        return faxRepo.findByTrackingId(uuid)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND,
                        "No fax found for trackingId=" + trackingId));
    }

    private Specification<FaxDocumentEntity> buildSpec(
            String status, String q, String dateFrom, String dateTo) {

        return (root, query, cb) -> {
            var predicates = new ArrayList<>();

            if (status != null && !status.isBlank()) {
                predicates.add(cb.equal(root.get("processingStatus"), status.toUpperCase()));
            }
            if (q != null && !q.isBlank()) {
                predicates.add(cb.like(cb.lower(root.get("originalFileName")),
                        "%" + q.toLowerCase() + "%"));
            }
            if (dateFrom != null && !dateFrom.isBlank()) {
                predicates.add(cb.greaterThanOrEqualTo(
                        root.get("createdAt"), OffsetDateTime.parse(dateFrom)));
            }
            if (dateTo != null && !dateTo.isBlank()) {
                predicates.add(cb.lessThanOrEqualTo(
                        root.get("createdAt"), OffsetDateTime.parse(dateTo)));
            }

            return predicates.isEmpty()
                    ? cb.conjunction()
                    : cb.and(predicates.toArray(new jakarta.persistence.criteria.Predicate[0]));
        };
    }
}
