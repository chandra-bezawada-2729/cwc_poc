package ai.avenirdigital.cwc.util;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;

import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Unit tests for FaxFilenameParser.
 *
 * §16.6 — senderFaxNumber must be E.164 (+1XXXXXXXXXX).
 * §16.7 — filename timestamp is fax-server local time (America/New_York);
 *          stored receivedAt must be UTC.
 */
class FaxFilenameParserTest {

    private static final ZoneId NY = ZoneId.of("America/New_York");

    // ── Matching cases (real sample filenames from spec §1) ───────────────────

    /**
     * §16.7 canonical assertion: (212)497-8948_2026-08-18_0943PM.pdf
     * Banner confirms 8/18/2026 9:42:24 PM EDT (UTC-4).
     * Expected UTC: 2026-08-19T01:43:00Z
     */
    @Test
    void parse_sample1_healthfirst() {
        FaxFilenameParser.ParsedFilename p =
                FaxFilenameParser.parse("(212)497-8948_2026-08-18_0943PM.pdf", NY);
        assertNotNull(p);
        // §16.6: E.164
        assertEquals("+12124978948", p.getSenderFaxNumber());
        // §16.7: 9:43 PM EDT = 01:43 UTC next day
        ZonedDateTime expected = ZonedDateTime.of(2026, 8, 19, 1, 43, 0, 0, ZoneOffset.UTC);
        assertEquals(expected, p.getReceivedAt());
    }

    @Test
    void parse_sample2_mainStreetRadiology() {
        FaxFilenameParser.ParsedFilename p =
                FaxFilenameParser.parse("(305)503-8807_2026-08-19_0702AM.pdf", NY);
        assertNotNull(p);
        assertEquals("+13055038807", p.getSenderFaxNumber());
        // 7:02 AM EDT = 11:02 UTC
        ZonedDateTime expected = ZonedDateTime.of(2026, 8, 19, 11, 2, 0, 0, ZoneOffset.UTC);
        assertEquals(expected, p.getReceivedAt());
    }

    @Test
    void parse_sample3_rxAdvocate() {
        FaxFilenameParser.ParsedFilename p =
                FaxFilenameParser.parse("(360)200-5103_2026-08-19_0137AM.pdf", NY);
        assertNotNull(p);
        assertEquals("+13602005103", p.getSenderFaxNumber());
        // 1:37 AM EDT = 05:37 UTC
        ZonedDateTime expected = ZonedDateTime.of(2026, 8, 19, 5, 37, 0, 0, ZoneOffset.UTC);
        assertEquals(expected, p.getReceivedAt());
    }

    @Test
    void parse_sample4_amazonPharmacy() {
        FaxFilenameParser.ParsedFilename p =
                FaxFilenameParser.parse("(603)935-9108_2026-08-18_0945PM.pdf", NY);
        assertNotNull(p);
        assertEquals("+16039359108", p.getSenderFaxNumber());
        // 9:45 PM EDT = 01:45 UTC next day
        ZonedDateTime expected = ZonedDateTime.of(2026, 8, 19, 1, 45, 0, 0, ZoneOffset.UTC);
        assertEquals(expected, p.getReceivedAt());
    }

    @Test
    void parse_sample5_coverMyMeds() {
        FaxFilenameParser.ParsedFilename p =
                FaxFilenameParser.parse("(614)321-2042_2026-08-18_1004PM.pdf", NY);
        assertNotNull(p);
        assertEquals("+16143212042", p.getSenderFaxNumber());
        // 10:04 PM EDT = 02:04 UTC next day
        ZonedDateTime expected = ZonedDateTime.of(2026, 8, 19, 2, 4, 0, 0, ZoneOffset.UTC);
        assertEquals(expected, p.getReceivedAt());
    }

    // ── Non-matching cases — must return null gracefully ──────────────────────

    @ParameterizedTest(name = "non-match: {0}")
    @MethodSource("nonMatchingFilenames")
    void parse_nonMatching_returnsNull(String filename) {
        assertNull(FaxFilenameParser.parse(filename, NY),
                "Expected null for non-matching filename: " + filename);
    }

    static Stream<String> nonMatchingFilenames() {
        return Stream.of(
                null,
                "",
                "plain_document.pdf",
                "2026-08-18_0943PM.pdf",
                "12345678_2026-08-18_0943PM.pdf"
        );
    }
}
