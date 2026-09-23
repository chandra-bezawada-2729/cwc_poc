package ai.avenirdigital.cwc.model;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.OffsetDateTime;

/**
 * Persisted output of one classification pipeline run.
 * Maps to cwc.classification_results (spec §7). Flyway owns the schema.
 *
 * Phase 3 creates the row at OCR time with detected_category = null.
 * Phase 4 fills in the LLM classification fields.
 */
@Getter
@Setter
@Entity
@Table(name = "classification_results", schema = "cwc")
public class ClassificationResultEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** FK to cwc.fax_documents.id */
    @Column(name = "fax_document_id", nullable = false)
    private Long faxDocumentId;

    /** Null until Phase 4 classification completes. Made nullable by V2 migration. */
    @Column(name = "detected_category")
    private String detectedCategory;

    @Column(name = "detected_subtype")
    private String detectedSubtype;

    // ── Taxonomy v2.0: the suggested rename (V5 migration) ───────────────────

    /**
     * Short noun phrase describing the document — the specialty, modality +
     * body part, procedure or form name. Drives both filename forms.
     * Never contains a patient identifier; see the naming rules in the prompt.
     */
    @Column(name = "specification")
    private String specification;

    /** CWC house style, e.g. "US Abdomen.pdf". */
    @Column(name = "suggested_file_name")
    private String suggestedFileName;

    /** specification_doctype style, e.g. "us_abdomen_radiologyreport.pdf". */
    @Column(name = "alternate_file_name")
    private String alternateFileName;

    /** Slug used to build alternateFileName — from the subtype, else the category. */
    @Column(name = "document_type_slug")
    private String documentTypeSlug;

    /** MODEL when the model supplied a specification; FALLBACK when defaulted. */
    @Column(name = "naming_source")
    private String namingSource;

    /** Leading fax cover-sheet pages the model detected, for the review screen. */
    @Column(name = "cover_sheet_pages")
    private Integer coverSheetPages;

    @Column(name = "model_confidence")
    private java.math.BigDecimal modelConfidence;

    @Column(name = "calibrated_confidence")
    private java.math.BigDecimal calibratedConfidence;

    /** Null until Phase 4. Made nullable by V2 migration. HIGH | MEDIUM | LOW */
    @Column(name = "confidence_band")
    private String confidenceBand;

    @Column(name = "runner_up_category")
    private String runnerUpCategory;

    @Column(name = "runner_up_confidence")
    private java.math.BigDecimal runnerUpConfidence;

    @Column(name = "reason")
    private String reason;

    /**
     * ALL supporting quotes, exactly as the model returned them. Scored against
     * the OCR text to produce evidenceScore — never narrow or filter this array.
     */
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "evidence", columnDefinition = "jsonb")
    private String evidence;

    /** Display-only subset of {@link #evidence}: why this CATEGORY. Null pre-v2.1. */
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "routing_evidence", columnDefinition = "jsonb")
    private String routingEvidence;

    /** Display-only subset of {@link #evidence}: why this SPECIFICATION. Null pre-v2.1. */
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "naming_evidence", columnDefinition = "jsonb")
    private String namingEvidence;

    @Column(name = "evidence_score")
    private java.math.BigDecimal evidenceScore;

    @Column(name = "sender_organization")
    private String senderOrganization;

    /**
     * Return fax read from the document body — distinct from
     * fax_documents.sender_fax_number, which is parsed from the inbound filename.
     */
    @Column(name = "sender_callback_fax")
    private String senderCallbackFax;

    /** FILENAME | DOCUMENT | NONE — provenance of the sender fax shown to staff. */
    @Column(name = "sender_fax_number_source")
    private String senderFaxNumberSource;

    @Column(name = "action_required")
    private Boolean actionRequired;

    @Column(name = "action_summary")
    private String actionSummary;

    @Column(name = "response_deadline")
    private java.time.LocalDate responseDeadline;

    @Column(name = "contains_fillable_form")
    private Boolean containsFillableForm;

    /** JSON array of PHI identifier kinds (never values). */
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "phi_identifiers", columnDefinition = "jsonb")
    private String phiIdentifiers;

    /** VISION | OCR_FALLBACK at classification time; NATIVE | TESSERACT at OCR time. */
    @Column(name = "classification_mode")
    private String classificationMode;

    @Column(name = "ocr_char_count")
    private Integer ocrCharCount;

    /**
     * Full OCR text — may contain PHI. Never log this field.
     * Use tracking_id for log correlation.
     */
    @Column(name = "ocr_text")
    private String ocrText;

    @Column(name = "model_name")
    private String modelName;

    @Column(name = "prompt_version")
    private String promptVersion;

    @Column(name = "latency_ms")
    private Integer latencyMs;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "raw_response", columnDefinition = "jsonb")
    private String rawResponse;

    /** True when any hard override requires human sign-off before routing (spec §5 Ruling 2). */
    @Column(name = "force_manual_review")
    private Boolean forceManualReview;

    /** Human-readable override code: UNKNOWN | AMBIGUOUS | UNVERIFIED_EVIDENCE | AUTO_ROUTE_DISABLED:X */
    @Column(name = "override_reason")
    private String overrideReason;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt;

    @PrePersist
    void onPrePersist() {
        if (createdAt == null) {
            createdAt = OffsetDateTime.now();
        }
    }
}
