package ai.avenirdigital.cwc.configuration;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Binds cwc.storage.* from application.properties.
 *
 * basePath is resolved to an absolute path at startup (§16.5) so the storage
 * location is unambiguous regardless of the process working directory.
 * All paths are expressed relative to basePath — never absolute in source.
 */
@Data
@ConfigurationProperties(prefix = "cwc.storage")
public class StorageProperties {

    /**
     * Root of all file storage. Relative paths resolve against the JVM CWD
     * and are then absolutised. Default points to repo-root storage/ when
     * the server is started from cwc-backend/ via mvn spring-boot:run.
     */
    private String basePath = "../storage";

    /** Subdirectory under basePath for uploaded faxes awaiting processing. */
    private String incomingDir = "incoming";

    /** Subdirectory under basePath for files that have been routed. */
    private String routedDir = "routed";
}
