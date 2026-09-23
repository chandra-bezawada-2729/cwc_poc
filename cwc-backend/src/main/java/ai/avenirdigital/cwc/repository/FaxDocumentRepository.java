package ai.avenirdigital.cwc.repository;

import ai.avenirdigital.cwc.model.FaxDocumentEntity;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Repository
public interface FaxDocumentRepository
        extends JpaRepository<FaxDocumentEntity, Long>,
                JpaSpecificationExecutor<FaxDocumentEntity> {

    Optional<FaxDocumentEntity> findByTrackingId(UUID trackingId);

    /** Dashboard: count of documents per processing_status. Returns [status, count] pairs. */
    @Query("SELECT d.processingStatus, COUNT(d) FROM FaxDocumentEntity d GROUP BY d.processingStatus ORDER BY COUNT(d) DESC")
    List<Object[]> countByProcessingStatus();
}
