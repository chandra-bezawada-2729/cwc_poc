package ai.avenirdigital.cwc.model;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

import java.time.OffsetDateTime;

/**
 * Persists every routing decision — suggestion, confirmation, override, or failure.
 * Maps to cwc.routing_decisions (V1__init.sql).
 * Human overrides are the most valuable POC signal; never discard them.
 */
@Getter
@Setter
@Entity
@Table(name = "routing_decisions", schema = "cwc")
public class RoutingDecisionEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "fax_document_id", nullable = false)
    private Long faxDocumentId;

    /** Folder the engine recommended. Never null. */
    @Column(name = "suggested_folder", nullable = false)
    private String suggestedFolder;

    /** Folder the document actually landed in (null until confirmed). */
    @Column(name = "final_folder")
    private String finalFolder;

    /** Absolute path of the filed copy (null until confirmed). */
    @Column(name = "final_path")
    private String finalPath;

    /** SUGGESTED | CONFIRMED | OVERRIDDEN | MANUAL_REVIEW | FAILED */
    @Column(name = "routing_status", nullable = false)
    private String routingStatus;

    /** SYSTEM for automated decisions; user identifier for manual confirmations. */
    @Column(name = "decided_by")
    private String decidedBy;

    /** Required when human files in a different folder than suggested. */
    @Column(name = "override_reason")
    private String overrideReason;

    /** Which config rule matched: sender:<fax>, subtype:<cat>/<sub>, category:<cat>, or unmapped. */
    @Column(name = "rule_matched")
    private String ruleMatched;

    // ── Taxonomy v2.0: the rename half of the decision (V5 migration) ────────

    /**
     * The inbound filename as CWC received it, e.g.
     * "(614)321-2042_2026-08-18_1004PM.pdf". Kept so a routed file can always be
     * traced back to the fax that arrived — the rename is otherwise lossy.
     */
    @Column(name = "original_file_name")
    private String originalFileName;

    /** Filename the engine proposed. Never null once a suggestion exists. */
    @Column(name = "suggested_file_name")
    private String suggestedFileName;

    /**
     * Filename the document was actually filed under (null until confirmed).
     * Differs from suggestedFileName when a human edited the name, or when a
     * collision suffix was appended.
     */
    @Column(name = "final_file_name")
    private String finalFileName;

    /**
     * True when a human changed the suggested NAME (as distinct from the
     * folder). Name overrides and folder overrides are separate training
     * signals and must be countable separately.
     */
    @Column(name = "file_name_overridden")
    private Boolean fileNameOverridden;

    @Column(name = "decided_at", nullable = false, updatable = false)
    private OffsetDateTime decidedAt;

    @PrePersist
    void prePersist() {
        if (decidedAt == null) decidedAt = OffsetDateTime.now();
    }
}
