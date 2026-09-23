package ai.avenirdigital.cwc.model;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * Persisted record of one inbound fax file.
 * Maps exactly to cwc.fax_documents DDL (spec §7). Flyway owns the schema.
 */
@Getter
@Setter
@Entity
@Table(name = "fax_documents", schema = "cwc")
public class FaxDocumentEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "tracking_id", nullable = false, unique = true, updatable = false)
    private UUID trackingId;

    @Column(name = "original_file_name", nullable = false)
    private String originalFileName;

    /** Relative path — never absolute. Resolved from the JVM working directory. */
    @Column(name = "stored_path", nullable = false)
    private String storedPath;

    @Column(name = "file_size_bytes")
    private Long fileSizeBytes;

    @Column(name = "page_count")
    private Integer pageCount;

    @Column(name = "mime_type")
    private String mimeType;

    /** Transmitting fax number parsed from the filename; never a patient identifier. */
    @Column(name = "sender_fax_number")
    private String senderFaxNumber;

    /** Timestamp parsed from the filename; stored as UTC. */
    @Column(name = "received_at")
    private OffsetDateTime receivedAt;

    /** MANUAL_UPLOAD | FOLDER_WATCH | FOLDER_SCAN | API */
    @Column(name = "upload_source", nullable = false)
    private String uploadSource;

    /**
     * Inbound root this document was discovered under (V6). Null for manual
     * uploads. Matters once CWC has more than one inbound source — different fax
     * lines or practices — because triage differs per source.
     */
    @Column(name = "source_folder")
    private String sourceFolder;

    /** RECEIVED | VALIDATING | OCR | CLASSIFYING | CLASSIFIED | ROUTED | ERRORED */
    @Column(name = "processing_status", nullable = false)
    private String processingStatus;

    @Column(name = "error_reason")
    private String errorReason;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt;

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt;

    @PrePersist
    void onPrePersist() {
        OffsetDateTime now = OffsetDateTime.now();
        if (createdAt == null) createdAt = now;
        updatedAt = now;
    }

    @PreUpdate
    void onPreUpdate() {
        updatedAt = OffsetDateTime.now();
    }
}
