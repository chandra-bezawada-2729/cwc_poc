package ai.avenirdigital.cwc.controller;

import ai.avenirdigital.cwc.model.FaxDocumentEntity;
import ai.avenirdigital.cwc.model.RoutingDecisionEntity;
import ai.avenirdigital.cwc.repository.FaxDocumentRepository;
import ai.avenirdigital.cwc.repository.RoutingDecisionRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.csv.CSVFormat;
import org.apache.commons.csv.CSVPrinter;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.io.IOException;
import java.io.StringWriter;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * GET /api/audit/export.csv — routing decisions + overrides as CSV.
 * Human overrides are the most valuable POC data; make them easy to export.
 */
@Slf4j
@RestController
@RequestMapping("/api/audit")
@RequiredArgsConstructor
public class AuditController {

    private final RoutingDecisionRepository routingDecisionRepo;
    private final FaxDocumentRepository     faxRepo;

    @GetMapping(value = "/export.csv", produces = "text/csv")
    public ResponseEntity<String> exportCsv() throws IOException {
        List<RoutingDecisionEntity> decisions = routingDecisionRepo.findAllForAudit();
        List<Long> docIds = decisions.stream().map(RoutingDecisionEntity::getFaxDocumentId).distinct().toList();
        Map<Long, FaxDocumentEntity> docsById =
                faxRepo.findAllById(docIds).stream()
                        .collect(Collectors.toMap(FaxDocumentEntity::getId, d -> d));

        StringWriter sw = new StringWriter();
        CSVFormat format = CSVFormat.DEFAULT.builder()
                .setHeader("trackingId", "originalFileName", "suggestedFolder", "finalFolder",
                        "routingStatus", "decidedBy", "overrideReason", "ruleMatched", "decidedAt")
                .build();

        try (CSVPrinter printer = new CSVPrinter(sw, format)) {
            for (RoutingDecisionEntity rd : decisions) {
                FaxDocumentEntity doc = docsById.get(rd.getFaxDocumentId());
                printer.printRecord(
                        doc != null ? doc.getTrackingId() : rd.getFaxDocumentId(),
                        doc != null ? doc.getOriginalFileName() : "",
                        rd.getSuggestedFolder(),
                        rd.getFinalFolder(),
                        rd.getRoutingStatus(),
                        rd.getDecidedBy(),
                        rd.getOverrideReason(),
                        rd.getRuleMatched(),
                        rd.getDecidedAt()
                );
            }
        }

        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"routing-audit.csv\"")
                .contentType(MediaType.parseMediaType("text/csv; charset=UTF-8"))
                .body(sw.toString());
    }
}
