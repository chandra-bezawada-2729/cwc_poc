package ai.avenirdigital.cwc.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Comparator;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Stream;

/**
 * Serves the latest evaluation/run_eval.py JSON report to the frontend AccuracyPage.
 * Reads from ../evaluation/reports/ relative to cwc-backend/ (i.e. repo-root/evaluation/reports/).
 *
 * This endpoint is display-only. It never writes to the reports directory.
 */
@Slf4j
@RestController
@RequestMapping("/api/eval")
public class EvalReportController {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    /** Path to evaluation/reports/ relative to process working directory (cwc-backend/ in dev). */
    private static final Path REPORTS_DIR =
            Paths.get("../evaluation/reports").toAbsolutePath().normalize();

    @GetMapping("/report")
    public ResponseEntity<Object> latestReport() {
        if (!Files.isDirectory(REPORTS_DIR)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND,
                    "No reports directory found. Run: python evaluation/run_eval.py");
        }

        Optional<Path> latest;
        try (Stream<Path> stream = Files.list(REPORTS_DIR)) {
            latest = stream
                    .filter(p -> p.getFileName().toString().startsWith("eval_")
                              && p.getFileName().toString().endsWith(".json"))
                    .max(Comparator.comparing(p -> p.getFileName().toString()));
        } catch (IOException e) {
            log.error("[EVAL] Cannot list reports dir: {}", e.getMessage());
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR,
                    "Cannot read reports directory: " + e.getMessage());
        }

        if (latest.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND,
                    "No evaluation report yet. Run: python evaluation/run_eval.py");
        }

        try {
            String json = Files.readString(latest.get());
            Object report = MAPPER.readValue(json, Object.class);
            log.info("[EVAL] Serving report: {}", latest.get().getFileName());
            return ResponseEntity.ok(report);
        } catch (IOException e) {
            log.error("[EVAL] Cannot read report {}: {}", latest.get(), e.getMessage());
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR,
                    "Cannot read report: " + e.getMessage());
        }
    }

    @GetMapping("/reports")
    public ResponseEntity<Object> listReports() {
        if (!Files.isDirectory(REPORTS_DIR)) {
            return ResponseEntity.ok(Map.of("reports", java.util.List.of()));
        }
        try (Stream<Path> stream = Files.list(REPORTS_DIR)) {
            var names = stream
                    .filter(p -> p.getFileName().toString().endsWith(".json"))
                    .map(p -> p.getFileName().toString())
                    .sorted(Comparator.reverseOrder())
                    .toList();
            return ResponseEntity.ok(Map.of("reports", names));
        } catch (IOException e) {
            return ResponseEntity.ok(Map.of("reports", java.util.List.of()));
        }
    }
}
