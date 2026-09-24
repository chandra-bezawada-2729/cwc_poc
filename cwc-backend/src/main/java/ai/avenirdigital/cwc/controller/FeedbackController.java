package ai.avenirdigital.cwc.controller;

import ai.avenirdigital.cwc.model.*;
import ai.avenirdigital.cwc.repository.*;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.util.*;

/**
 * Reviewer feedback on processed faxes.
 *
 *   POST /api/faxes/{trackingId}/feedback   record or change a verdict
 *   GET  /api/faxes/{trackingId}/feedback   the current verdict, if any
 *   GET  /api/feedback                      all feedback, newest first
 *   GET  /api/feedback/export.csv           the same as a spreadsheet
 *
 * A thumbs down carries a written summary of what went wrong. That summary is
 * the input to changing the routing configuration, so it is kept alongside a
 * snapshot of what the AI decided at the time — the original file name, the
 * name and folder the AI chose, its category and confidence. Joining that at
 * read time would be wrong: if the document is reclassified later, the record
 * must still show what was being complained about.
 *
 * PHI note: summary is free text written while looking at a patient document.
 * It is never logged. Only tracking IDs and verdicts appear in log lines.
 */
@Slf4j
@RestController
@RequiredArgsConstructor
public class FeedbackController {

    private final FaxDocumentRepository          faxRepo;
    private final ClassificationResultRepository classificationRepo;
    private final RoutingDecisionRepository      routingRepo;
    private final FaxFeedbackRepository          feedbackRepo;

    // ── Request / response shapes ────────────────────────────────────────────

    public record FeedbackRequest(String verdict, String summary) {}

    public record FeedbackResponse(
            String  trackingId,
            String  verdict,
            String  summary,
            String  originalFileName,
            String  aiFileName,
            String  aiFolder,
            String  aiCategory,
            String  aiSubtype,
            Double  aiConfidence,
            String  reviewState,
            String  createdAt,
            String  updatedAt
    ) {
        static FeedbackResponse from(FaxFeedbackEntity f) {
            return new FeedbackResponse(
                    f.getTrackingId().toString(),
                    f.getVerdict(),
                    f.getSummary(),
                    f.getOriginalFileName(),
                    f.getAiFileName(),
                    f.getAiFolder(),
                    f.getAiCategory(),
                    f.getAiSubtype(),
                    f.getAiConfidence() == null ? null : f.getAiConfidence().doubleValue(),
                    f.getReviewState(),
                    f.getCreatedAt() == null ? null : f.getCreatedAt().toString(),
                    f.getUpdatedAt() == null ? null : f.getUpdatedAt().toString()
            );
        }
    }

    // ── POST /api/faxes/{trackingId}/feedback ────────────────────────────────

    @PostMapping("/api/faxes/{trackingId}/feedback")
    public ResponseEntity<?> submit(@PathVariable UUID trackingId,
                                    @RequestBody FeedbackRequest body) {

        String verdict = body.verdict() == null ? "" : body.verdict().trim().toUpperCase();
        if (!FaxFeedbackEntity.UP.equals(verdict) && !FaxFeedbackEntity.DOWN.equals(verdict)) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "verdict must be UP or DOWN"));
        }

        String summary = body.summary() == null ? null : body.summary().trim();
        if (summary != null && summary.isEmpty()) summary = null;

        // A thumbs down with no explanation is the case this feature exists to
        // capture, so it is required rather than merely encouraged.
        if (FaxFeedbackEntity.DOWN.equals(verdict) && summary == null) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "a summary is required when marking a fax wrong"));
        }

        Optional<FaxDocumentEntity> docOpt = faxRepo.findByTrackingId(trackingId);
        if (docOpt.isEmpty()) return ResponseEntity.notFound().build();
        FaxDocumentEntity doc = docOpt.get();

        FaxFeedbackEntity fb = feedbackRepo.findByFaxDocumentId(doc.getId())
                .orElseGet(FaxFeedbackEntity::new);

        fb.setFaxDocumentId(doc.getId());
        fb.setTrackingId(doc.getTrackingId());
        fb.setVerdict(verdict);
        // Changing UP -> DOWN must not leave the old text behind, and
        // DOWN -> UP must not keep a complaint about a decision now accepted.
        fb.setSummary(FaxFeedbackEntity.DOWN.equals(verdict) ? summary : null);

        fb.setOriginalFileName(doc.getOriginalFileName());
        fb.setReviewState(doc.getProcessingStatus());

        classificationRepo.findByFaxDocumentId(doc.getId()).ifPresent(cr -> {
            fb.setAiCategory(cr.getDetectedCategory());
            fb.setAiSubtype(cr.getDetectedSubtype());
            fb.setAiConfidence(cr.getCalibratedConfidence());
        });

        routingRepo.findTopByFaxDocumentIdOrderByDecidedAtDesc(doc.getId()).ifPresent(rd -> {
            fb.setAiFolder(rd.getFinalFolder() != null ? rd.getFinalFolder() : rd.getSuggestedFolder());
            fb.setAiFileName(rd.getFinalFileName() != null ? rd.getFinalFileName() : rd.getSuggestedFileName());
        });

        feedbackRepo.save(fb);
        log.info("[FEEDBACK] trackingId={} verdict={} hasSummary={}",
                trackingId, verdict, summary != null);

        return ResponseEntity.ok(FeedbackResponse.from(fb));
    }

    // ── GET /api/faxes/{trackingId}/feedback ─────────────────────────────────

    @GetMapping("/api/faxes/{trackingId}/feedback")
    public ResponseEntity<FeedbackResponse> get(@PathVariable UUID trackingId) {
        return feedbackRepo.findByTrackingId(trackingId)
                .map(f -> ResponseEntity.ok(FeedbackResponse.from(f)))
                .orElseGet(() -> ResponseEntity.noContent().build());
    }

    // ── GET /api/feedback ────────────────────────────────────────────────────

    @GetMapping("/api/feedback")
    public Map<String, Object> list(@RequestParam(required = false) String verdict) {
        List<FaxFeedbackEntity> rows = (verdict == null || verdict.isBlank())
                ? feedbackRepo.findAllByOrderByCreatedAtDesc()
                : feedbackRepo.findAllByVerdictOrderByCreatedAtDesc(verdict.trim().toUpperCase());

        return Map.of(
                "total",   rows.size(),
                "upCount",   feedbackRepo.countByVerdict(FaxFeedbackEntity.UP),
                "downCount", feedbackRepo.countByVerdict(FaxFeedbackEntity.DOWN),
                "items",   rows.stream().map(FeedbackResponse::from).toList()
        );
    }

    // ── GET /api/feedback/export.csv ─────────────────────────────────────────

    @GetMapping(value = "/api/feedback/export.csv", produces = "text/csv")
    public ResponseEntity<String> exportCsv(@RequestParam(required = false) String verdict) {

        List<FaxFeedbackEntity> rows = (verdict == null || verdict.isBlank())
                ? feedbackRepo.findAllByOrderByCreatedAtDesc()
                : feedbackRepo.findAllByVerdictOrderByCreatedAtDesc(verdict.trim().toUpperCase());

        StringBuilder sb = new StringBuilder();
        sb.append("recorded_at,verdict,tracking_id,original_file_name,")
          .append("ai_file_name,ai_folder,ai_category,ai_subtype,ai_confidence,")
          .append("review_state,summary\n");

        for (FaxFeedbackEntity f : rows) {
            sb.append(csv(f.getCreatedAt() == null ? "" : f.getCreatedAt()
                          .format(DateTimeFormatter.ISO_OFFSET_DATE_TIME))).append(',')
              .append(csv(f.getVerdict())).append(',')
              .append(csv(f.getTrackingId() == null ? "" : f.getTrackingId().toString())).append(',')
              .append(csv(f.getOriginalFileName())).append(',')
              .append(csv(f.getAiFileName())).append(',')
              .append(csv(f.getAiFolder())).append(',')
              .append(csv(f.getAiCategory())).append(',')
              .append(csv(f.getAiSubtype())).append(',')
              .append(csv(f.getAiConfidence() == null ? "" : f.getAiConfidence().toPlainString())).append(',')
              .append(csv(f.getReviewState())).append(',')
              .append(csv(f.getSummary())).append('\n');
        }

        String stamp = OffsetDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd-HHmm"));
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.parseMediaType("text/csv; charset=UTF-8"));
        headers.setContentDispositionFormData("attachment", "fax-feedback-" + stamp + ".csv");

        return new ResponseEntity<>(sb.toString(), headers, org.springframework.http.HttpStatus.OK);
    }

    /**
     * Quote a CSV field. Summaries are free text and routinely contain commas,
     * quotes and newlines; without this the file silently loses columns when
     * opened in a spreadsheet.
     */
    private static String csv(String v) {
        if (v == null) return "";
        String s = v.replace("\"", "\"\"");
        return "\"" + s + "\"";
    }
}
