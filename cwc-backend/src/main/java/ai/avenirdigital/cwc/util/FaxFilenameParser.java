package ai.avenirdigital.cwc.util;

import lombok.Value;
import lombok.extern.slf4j.Slf4j;

import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Parses the CWC fax filename convention:
 *   (AAA)BBB-CCCC_YYYY-MM-DD_HHMMAM.pdf
 *
 * §16.6 — sender_fax_number is stored E.164 (+1XXXXXXXXXX for NANP numbers).
 * §16.7 — the timestamp in the filename is the fax server's local wall-clock.
 *          Callers must supply the server's ZoneId; the result is always UTC.
 */
@Slf4j
public class FaxFilenameParser {

    private static final Pattern FILENAME_PATTERN = Pattern.compile(
            "\\((\\d{3})\\)(\\d{3})-(\\d{4})_(\\d{4}-\\d{2}-\\d{2})_(\\d{4})(AM|PM)",
            Pattern.CASE_INSENSITIVE
    );

    @Value
    public static class ParsedFilename {
        /** E.164-normalised fax number, e.g. "+12124978948". */
        String senderFaxNumber;
        /** Instant in UTC derived from the filename timestamp + the supplied zone. */
        ZonedDateTime receivedAt;
    }

    /**
     * Parses a filename.
     *
     * @param filename      the raw PDF filename, e.g. "(212)497-8948_2026-08-18_0943PM.pdf"
     * @param faxServerZone IANA zone of the fax server (from cwc.ingestion.fax-server-zone)
     * @return parsed result, or {@code null} if the name does not match the pattern
     */
    public static ParsedFilename parse(String filename, ZoneId faxServerZone) {
        if (filename == null || filename.isBlank()) return null;

        Matcher m = FILENAME_PATTERN.matcher(filename);
        if (!m.find()) {
            log.debug("[FILENAME] No match for pattern in: {}", filename);
            return null;
        }

        // §16.6 — E.164: strip formatting, prepend +1 (NANP 10-digit numbers)
        String digits    = m.group(1) + m.group(2) + m.group(3);  // e.g. "2124978948"
        String faxNumber = "+1" + digits;                          // e.g. "+12124978948"

        // §16.7 — parse as local wall-clock in faxServerZone, convert to UTC
        String datePart = m.group(4);              // "2026-08-18"
        String timePart = m.group(5);              // "0943"
        String ampm     = m.group(6).toUpperCase(); // "PM"
        String combined = datePart + " " + timePart + ampm;

        DateTimeFormatter fmt = DateTimeFormatter.ofPattern("yyyy-MM-dd hhmma", Locale.US);
        ZonedDateTime receivedAt = null;
        try {
            LocalDateTime ldt = LocalDateTime.parse(combined, fmt);
            // Interpret as fax-server local time, then shift to UTC
            receivedAt = ldt.atZone(faxServerZone).withZoneSameInstant(ZoneOffset.UTC);
        } catch (Exception e) {
            log.warn("[FILENAME] Could not parse datetime from '{}': {}", filename, e.getMessage());
        }

        log.debug("[FILENAME] Parsed faxNumber={} receivedAt={} (zone={})",
                faxNumber, receivedAt, faxServerZone);
        return new ParsedFilename(faxNumber, receivedAt);
    }
}
