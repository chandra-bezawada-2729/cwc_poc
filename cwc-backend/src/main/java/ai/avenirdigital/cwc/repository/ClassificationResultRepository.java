package ai.avenirdigital.cwc.repository;

import ai.avenirdigital.cwc.model.ClassificationResultEntity;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDate;
import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface ClassificationResultRepository extends JpaRepository<ClassificationResultEntity, Long> {

    Optional<ClassificationResultEntity> findByFaxDocumentId(Long faxDocumentId);

    /** Batch lookup for list endpoint — avoids N+1. */
    List<ClassificationResultEntity> findAllByFaxDocumentIdIn(Collection<Long> faxDocumentIds);

    /** Dashboard: count per detected_category. Returns [category, count] pairs. */
    @Query("SELECT c.detectedCategory, COUNT(c) FROM ClassificationResultEntity c GROUP BY c.detectedCategory ORDER BY COUNT(c) DESC")
    List<Object[]> countByCategory();

    /** Dashboard: count per confidence_band. Returns [band, count] pairs. */
    @Query("SELECT c.confidenceBand, COUNT(c) FROM ClassificationResultEntity c GROUP BY c.confidenceBand ORDER BY COUNT(c) DESC")
    List<Object[]> countByBand();

    /** Dashboard: mean classification latency in ms across all classified docs. */
    @Query("SELECT AVG(c.latencyMs) FROM ClassificationResultEntity c WHERE c.latencyMs IS NOT NULL")
    Double avgLatencyMs();

    /** Dashboard: count of action-required docs with deadline between now and cutoff. */
    @Query("SELECT COUNT(c) FROM ClassificationResultEntity c WHERE c.actionRequired = true AND c.responseDeadline IS NOT NULL AND c.responseDeadline >= :now AND c.responseDeadline <= :cutoff")
    long countActionDueSoon(@Param("now") LocalDate now, @Param("cutoff") LocalDate cutoff);
}
