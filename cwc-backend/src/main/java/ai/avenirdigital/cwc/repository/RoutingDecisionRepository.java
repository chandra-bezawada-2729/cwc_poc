package ai.avenirdigital.cwc.repository;

import ai.avenirdigital.cwc.model.RoutingDecisionEntity;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface RoutingDecisionRepository extends JpaRepository<RoutingDecisionEntity, Long> {

    Optional<RoutingDecisionEntity> findTopByFaxDocumentIdOrderByDecidedAtDesc(Long faxDocumentId);

    /** Batch lookup for the list endpoint — avoids N+1. */
    List<RoutingDecisionEntity> findAllByFaxDocumentIdIn(Collection<Long> faxDocumentIds);

    /** All routing decisions for audit export. */
    @Query("""
        SELECT rd FROM RoutingDecisionEntity rd
        ORDER BY rd.decidedAt DESC
    """)
    List<RoutingDecisionEntity> findAllForAudit();

    /** Dashboard: count of routing decisions per status (CONFIRMED, OVERRIDDEN, etc.). */
    @Query("SELECT rd.routingStatus, COUNT(rd) FROM RoutingDecisionEntity rd GROUP BY rd.routingStatus")
    List<Object[]> countByStatus();
}
