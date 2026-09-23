package ai.avenirdigital.cwc.controller;

import ai.avenirdigital.cwc.controller.response.FaxDetailResponse;
import ai.avenirdigital.cwc.model.ClassificationResultEntity;
import ai.avenirdigital.cwc.model.FaxDocumentEntity;
import ai.avenirdigital.cwc.model.RoutingDecisionEntity;
import ai.avenirdigital.cwc.repository.ClassificationResultRepository;
import ai.avenirdigital.cwc.repository.FaxDocumentRepository;
import ai.avenirdigital.cwc.repository.RoutingDecisionRepository;
import ai.avenirdigital.cwc.service.RoutingEngineService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * GET /api/review-queue — returns faxes that require human review before routing.
 *
 * A fax is in review when ANY of:
 *   (a) confidence_band = LOW
 *   (b) force_manual_review = true (hard override)
 *   (c) routing_status = MANUAL_REVIEW (set by routing engine when forced or auto-disabled)
 *   (d) final_folder = configured manual-review folder (user explicitly overrode there)
 *
 * Faxes already CONFIRMED to a non-review folder are excluded.
 * Faxes already CONFIRMED to the manual-review folder remain (they still need action).
 */
@Slf4j
@RestController
@RequestMapping("/api/review-queue")
@RequiredArgsConstructor
public class ReviewQueueController {

    private final FaxDocumentRepository          faxRepo;
    private final ClassificationResultRepository classificationRepo;
    private final RoutingDecisionRepository      routingDecisionRepo;
    private final RoutingEngineService           routingEngine;

    @GetMapping
    public ResponseEntity<Map<String, Object>> queue() {
        // All docs that may need review:
        // CLASSIFIED (not yet acted on) or ROUTED to the manual-review folder (action still needed)
        List<FaxDocumentEntity> classified = faxRepo.findAll()
                .stream()
                .filter(d -> "CLASSIFIED".equals(d.getProcessingStatus())
                          || "ROUTED".equals(d.getProcessingStatus()))
                .toList();

        List<Long> ids = classified.stream().map(FaxDocumentEntity::getId).toList();

        Map<Long, ClassificationResultEntity> clsByDocId =
                classificationRepo.findAllByFaxDocumentIdIn(ids).stream()
                        .collect(Collectors.toMap(ClassificationResultEntity::getFaxDocumentId, c -> c,
                                (a, b) -> a));

        Map<Long, RoutingDecisionEntity> rdByDocId = new HashMap<>();
        routingDecisionRepo.findAllByFaxDocumentIdIn(ids).forEach(rd ->
                rdByDocId.merge(rd.getFaxDocumentId(), rd,
                        (a, b) -> a.getDecidedAt().isAfter(b.getDecidedAt()) ? a : b));

        String manualFolder = routingEngine.getManualReviewFolder();
        List<FaxDetailResponse> items = new ArrayList<>();
        for (FaxDocumentEntity doc : classified) {
            ClassificationResultEntity cls = clsByDocId.get(doc.getId());
            RoutingDecisionEntity rd       = rdByDocId.get(doc.getId());

            boolean lowBand      = cls != null && "LOW".equals(cls.getConfidenceBand());
            boolean forcedReview = cls != null && Boolean.TRUE.equals(cls.getForceManualReview());
            boolean manualStatus = rd  != null && "MANUAL_REVIEW".equals(rd.getRoutingStatus());
            // (d): explicitly overridden to manual-review folder by a human — still needs action
            boolean sentToManualFolder = rd != null && manualFolder.equals(rd.getFinalFolder());

            boolean inReview = lowBand || forcedReview || manualStatus || sentToManualFolder;

            // Exclude docs that have been definitively routed to a non-manual-review folder
            // (CONFIRMED or OVERRIDDEN to somewhere other than manual review)
            boolean confirmedElsewhere = rd != null
                    && ("CONFIRMED".equals(rd.getRoutingStatus()) || "OVERRIDDEN".equals(rd.getRoutingStatus()))
                    && !manualFolder.equals(rd.getFinalFolder());

            if (inReview && !confirmedElsewhere) {
                items.add(FaxDetailResponse.from(doc, cls, rd));
            }
        }

        return ResponseEntity.ok(Map.of(
                "content",       items,
                "totalElements", (long) items.size()
        ));
    }
}
