package ai.avenirdigital.cwc.model;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.OffsetDateTime;

/**
 * One row per distinct set of file bytes discovered in the inbound folder.
 *
 * <p>Maps to {@code cwc.ingest_ledger} (V6 migration). This table is what makes
 * "which faxes are yet to be processed?" answerable after a restart, after a
 * re-sync, and with two app instances running.
 *
 * <p><b>Identity is {@link #contentSha256}, never the path.</b> See the V6
 * migration header for why filename- and mtime-based identity gets the real
 * OneDrive cases wrong.
 */
@Getter
@Setter
@Entity
@Table(name = "ingest_ledger", schema = "cwc")
public class IngestLedgerEntity {

    // ── States ───────────────────────────────────────────────────────────────
    // Kept as String constants rather than an enum so the values cannot drift
    // from the CHECK constraint in V6 without a compile-time touch point here.
    public static final String DISCOVERED         = "DISCOVERED";
    public static final String AWAITING_HYDRATION = "AWAITING_HYDRATION";
    public static final String UNSTABLE           = "UNSTABLE";
    public static final String INGESTED           = "INGESTED";
    public static final String PROCESSING         = "PROCESSING";
    public static final String FILED              = "FILED";
    public static final String FAILED             = "FAILED";
    public static final String QUARANTINED        = "QUARANTINED";
    public static final String SKIPPED_DUPLICATE  = "SKIPPED_DUPLICATE";

    /** States that are finished — the scanner never picks these up again. */
    public static final java.util.Set<String> TERMINAL =
            java.util.Set.of(FILED, QUARANTINED, SKIPPED_DUPLICATE);

    // ── Origin ───────────────────────────────────────────────────────────────
    // A SCANNER row has a real inbound file at sourcePath that the retry loop can
    // re-read. An UPLOAD row does not: it exists only so that the content hash of
    // a manually uploaded fax is recorded and cannot be uploaded and filed twice.
    // Mixing them up would have the scanner try to re-ingest an upload from a path
    // that was never an inbound file.
    public static final String SCANNER = "SCANNER";
    public static final String UPLOAD  = "UPLOAD";

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** Hex SHA-256 of the file bytes. UNIQUE — the idempotency key. */
    // V6 declares this CHAR(64); ddl-auto=validate rejects the default VARCHAR mapping.
    @JdbcTypeCode(SqlTypes.CHAR)
    @Column(name = "content_sha256", nullable = false, unique = true, updatable = false, length = 64)
    private String contentSha256;

    @Column(name = "origin", nullable = false)
    private String origin = SCANNER;

    @Column(name = "source_path", nullable = false)
    private String sourcePath;

    @Column(name = "source_file_name", nullable = false)
    private String sourceFileName;

    @Column(name = "source_size_bytes")
    private Long sourceSizeBytes;

    /**
     * Last-modified as seen on disk, for the two-poll stability comparison only.
     * Never used to infer that a file is new — OneDrive preserves the source
     * mtime, so a file that synced this morning can carry last month's stamp.
     */
    @Column(name = "source_mtime_ms")
    private Long sourceMtimeMs;

    @Column(name = "state", nullable = false)
    private String state = DISCOVERED;

    @Column(name = "fax_document_id")
    private Long faxDocumentId;

    @Column(name = "routed_folder")
    private String routedFolder;

    @Column(name = "routed_file_name")
    private String routedFileName;

    @Column(name = "routed_path")
    private String routedPath;

    /** Where the original was moved after successful filing. */
    @Column(name = "archived_path")
    private String archivedPath;

    @Column(name = "attempts", nullable = false)
    private Integer attempts = 0;

    @Column(name = "last_error")
    private String lastError;

    /** Backoff deadline. Stored in the DB so it survives a restart. */
    @Column(name = "next_attempt_at")
    private OffsetDateTime nextAttemptAt;

    @Column(name = "first_seen_at", nullable = false, updatable = false)
    private OffsetDateTime firstSeenAt;

    @Column(name = "last_seen_at", nullable = false)
    private OffsetDateTime lastSeenAt;

    @Column(name = "completed_at")
    private OffsetDateTime completedAt;

    @PrePersist
    void onPrePersist() {
        OffsetDateTime now = OffsetDateTime.now();
        if (firstSeenAt == null) firstSeenAt = now;
        lastSeenAt = now;
    }

    @PreUpdate
    void onPreUpdate() {
        lastSeenAt = OffsetDateTime.now();
    }

    public boolean isTerminal() {
        return TERMINAL.contains(state);
    }
}
