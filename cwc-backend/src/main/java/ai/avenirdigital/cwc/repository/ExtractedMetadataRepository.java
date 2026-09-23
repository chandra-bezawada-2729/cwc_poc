package ai.avenirdigital.cwc.repository;

import ai.avenirdigital.cwc.model.ExtractedMetadataEntity;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

public interface ExtractedMetadataRepository
        extends JpaRepository<ExtractedMetadataEntity, Long> {

    Optional<ExtractedMetadataEntity> findByFaxDocumentId(Long faxDocumentId);

    /** One query for a whole Inbox page, so the list does not fan out per row. */
    List<ExtractedMetadataEntity> findAllByFaxDocumentIdIn(List<Long> faxDocumentIds);

    /**
     * Bulk export: all extraction rows for fax documents classified into a
     * given category within a date range. Used by GET /api/faxes/metadata/export.
     *
     * category and date bounds are all optional — pass null to omit a filter.
     */
    @Query("""
        SELECT em FROM ExtractedMetadataEntity em
        JOIN FaxDocumentEntity fd ON fd.id = em.faxDocumentId
        JOIN ClassificationResultEntity cr ON cr.faxDocumentId = fd.id
        WHERE (:category IS NULL OR cr.detectedCategory = :category)
          AND (:dateFrom  IS NULL OR em.createdAt >= :dateFrom)
          AND (:dateTo    IS NULL OR em.createdAt <= :dateTo)
        ORDER BY em.createdAt DESC
        """)
    List<ExtractedMetadataEntity> exportFiltered(
            @Param("category") String category,
            @Param("dateFrom")  OffsetDateTime dateFrom,
            @Param("dateTo")    OffsetDateTime dateTo
    );
}
