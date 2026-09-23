package ai.avenirdigital.cwc.model;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.OffsetDateTime;

/**
 * Persisted output of the Phase 9 category-specific metadata extraction pass.
 * Maps to cwc.extracted_metadata (spec §17.6). Flyway owns the schema.
 *
 * core_json, category_data, extraction_json are stored as JSONB.
 * The Java layer treats them as opaque Strings (already-serialised JSON)
 * so no per-field mapping is needed and new fields in the LLM output are
 * preserved without a schema migration.
 *
 * PHI note: core_json and category_data may contain patient names, DOBs, MRNs.
 * Never log their contents; use tracking_id for log correlation.
 */
@Getter
@Setter
@Entity
@Table(name = "extracted_metadata", schema = "cwc")
public class ExtractedMetadataEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "fax_document_id", nullable = false, unique = true)
    private Long faxDocumentId;

    @Column(name = "schema_version", nullable = false)
    private String schemaVersion = "1";

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "core_json", nullable = false, columnDefinition = "jsonb")
    private String coreJson;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "category_data", nullable = false, columnDefinition = "jsonb")
    private String categoryData;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "extraction_json", nullable = false, columnDefinition = "jsonb")
    private String extractionJson;

    @Column(name = "prompt_version")
    private String promptVersion;

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
