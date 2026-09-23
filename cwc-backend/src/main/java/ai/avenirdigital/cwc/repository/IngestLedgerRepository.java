package ai.avenirdigital.cwc.repository;

import ai.avenirdigital.cwc.model.IngestLedgerEntity;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public interface IngestLedgerRepository extends JpaRepository<IngestLedgerEntity, Long> {

    Optional<IngestLedgerEntity> findByContentSha256(String contentSha256);

    boolean existsByContentSha256(String contentSha256);

    Optional<IngestLedgerEntity> findByFaxDocumentId(Long faxDocumentId);

    /**
     * Claims a newly-discovered file, atomically and idempotently.
     *
     * <p>This is the concurrency guard for the whole pipeline. Two scanner
     * instances — or the scheduled scan racing a manual {@code POST /api/ingest/scan}
     * — can both reach this for the same bytes; the unique constraint on
     * content_sha256 means exactly one INSERT lands and the other is a silent
     * no-op. The return value says which one you were: 1 = you claimed it,
     * 0 = someone else already has it.
     *
     * <p>Written as native SQL because {@code ON CONFLICT DO NOTHING} has no JPQL
     * equivalent, and doing this as select-then-insert would reintroduce the race.
     */
    @Modifying
    @Transactional
    @Query(value = """
            INSERT INTO cwc.ingest_ledger
                (content_sha256, source_path, source_file_name,
                 source_size_bytes, source_mtime_ms, state, origin)
            VALUES (:sha, :path, :name, :size, :mtime, :state, :origin)
            ON CONFLICT (content_sha256) DO NOTHING
            """, nativeQuery = true)
    int claim(@Param("sha") String sha,
              @Param("path") String path,
              @Param("name") String name,
              @Param("size") Long size,
              @Param("mtime") Long mtime,
              @Param("state") String state,
              @Param("origin") String origin);

    /**
     * Everything unfinished and due for another attempt.
     *
     * <p>Restricted to SCANNER rows. An UPLOAD row names a path that was never an
     * inbound file, so retrying it would fail forever; it is in the ledger purely
     * as a content-hash fingerprint.
     * Backoff is honoured here so a poison file does not spin the scanner.
     */
    @Query("""
            SELECT l FROM IngestLedgerEntity l
            WHERE l.origin = 'SCANNER'
              AND l.state IN ('DISCOVERED','AWAITING_HYDRATION','UNSTABLE','INGESTED','PROCESSING','FAILED')
              AND (l.nextAttemptAt IS NULL OR l.nextAttemptAt <= :now)
            ORDER BY l.firstSeenAt ASC
            """)
    List<IngestLedgerEntity> findDue(@Param("now") OffsetDateTime now);

    /** Status endpoint: [state, count] pairs. */
    @Query("SELECT l.state, COUNT(l) FROM IngestLedgerEntity l GROUP BY l.state ORDER BY l.state")
    List<Object[]> countByState();

    /** Recent activity for the status panel. */
    List<IngestLedgerEntity> findTop25ByOrderByLastSeenAtDesc();

    List<IngestLedgerEntity> findByStateOrderByFirstSeenAtAsc(String state);
}
