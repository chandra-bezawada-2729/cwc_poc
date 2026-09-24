package ai.avenirdigital.cwc.repository;

import ai.avenirdigital.cwc.model.FaxFeedbackEntity;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface FaxFeedbackRepository extends JpaRepository<FaxFeedbackEntity, Long> {

    Optional<FaxFeedbackEntity> findByFaxDocumentId(Long faxDocumentId);

    Optional<FaxFeedbackEntity> findByTrackingId(UUID trackingId);

    /** One query for a whole page, so a list does not fan out per row. */
    List<FaxFeedbackEntity> findAllByFaxDocumentIdIn(List<Long> faxDocumentIds);

    List<FaxFeedbackEntity> findAllByOrderByCreatedAtDesc();

    List<FaxFeedbackEntity> findAllByVerdictOrderByCreatedAtDesc(String verdict);

    long countByVerdict(String verdict);
}
