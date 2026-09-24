package ai.avenirdigital.cwc.model;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * A reviewer's verdict on one processed fax. Maps to cwc.fax_feedback.
 * Flyway owns the schema.
 *
 * The AI's own output is copied in when the feedback is given rather than
 * joined at read time. Feedback is evidence about a decision made on a
 * particular day: if the document is later reclassified, or the routing rules
 * change, the record must still show what was wrong when someone objected.
 *
 * PHI note: summary is free text written by a person looking at a patient
 * document, so it may quote patient details. Never log its contents; use
 * trackingId for log correlation.
 */
@Getter
@Setter
@Entity
@Table(name = "fax_feedback", schema = "cwc")
public class FaxFeedbackEntity {

    public static final String UP   = "UP";
    public static final String DOWN = "DOWN";

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "fax_document_id", nullable = false, unique = true)
    private Long faxDocumentId;

    @Column(name = "tracking_id", nullable = false)
    private UUID trackingId;

    /** UP or DOWN. */
    @Column(name = "verdict", nullable = false)
    private String verdict;

    /** Only meaningful for DOWN: what went wrong, in the reviewer's words. */
    @Column(name = "summary")
    private String summary;

    @Column(name = "original_file_name")
    private String originalFileName;

    @Column(name = "ai_file_name")
    private String aiFileName;

    @Column(name = "ai_folder")
    private String aiFolder;

    @Column(name = "ai_category")
    private String aiCategory;

    @Column(name = "ai_subtype")
    private String aiSubtype;

    @Column(name = "ai_confidence")
    private BigDecimal aiConfidence;

    /** Where the document sat when judged: ROUTED, MANUAL_REVIEW, ERROR. */
    @Column(name = "review_state")
    private String reviewState;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt;

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt;

    @PrePersist
    void onCreate() {
        OffsetDateTime now = OffsetDateTime.now();
        if (createdAt == null) createdAt = now;
        updatedAt = now;
    }

    @PreUpdate
    void onUpdate() {
        updatedAt = OffsetDateTime.now();
    }
}
