package ai.avenirdigital.cwc.configuration;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Runtime configuration for the fax ingestion pipeline.
 * Zone must come from config — never a hardcoded constant (§16.7).
 */
@Data
@ConfigurationProperties(prefix = "cwc.ingestion")
public class IngestionProperties {

    /**
     * IANA zone ID of the fax server that stamps filenames.
     * Handles DST correctly (EDT in summer, EST in winter).
     * Default: America/New_York (inferred from fax banner evidence — see OPEN_QUESTIONS.md).
     */
    private String faxServerZone = "America/New_York";
}
