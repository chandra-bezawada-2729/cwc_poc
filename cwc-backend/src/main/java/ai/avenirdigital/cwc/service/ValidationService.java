package ai.avenirdigital.cwc.service;

import lombok.extern.slf4j.Slf4j;
import org.apache.pdfbox.Loader;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.rendering.PDFRenderer;
import org.apache.tika.detect.DefaultDetector;
import org.apache.tika.detect.Detector;
import org.apache.tika.metadata.Metadata;
import org.apache.tika.mime.MediaType;
import org.springframework.stereotype.Service;

import java.awt.image.BufferedImage;
import java.io.BufferedInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Set;

/**
 * Spec §4.2 validation gate.
 * Runs before any expensive OCR or AI work.
 * Never silently rejects — every failure carries a specific errorReason.
 */
@Slf4j
@Service
public class ValidationService {

    private static final Set<String> ALLOWED_EXTENSIONS =
            Set.of("pdf", "png", "jpg", "jpeg", "tif", "tiff");

    private static final Set<String> ALLOWED_MIME_TYPES =
            Set.of("application/pdf", "image/png", "image/jpeg", "image/tiff");

    private static final long MAX_FILE_SIZE_BYTES = 25L * 1024 * 1024;
    private static final int  MAX_PAGE_COUNT      = 50;
    /** Pixel variance (0–32512) below which a rendered page is treated as blank. */
    private static final double BLANK_VARIANCE_THRESHOLD = 25.0;

    private final Detector tikaDetector = new DefaultDetector();

    public record ValidationResult(
            boolean valid,
            String  errorReason,
            Integer pageCount,
            String  mimeType
    ) {
        static ValidationResult ok(int pageCount, String mimeType) {
            return new ValidationResult(true, null, pageCount, mimeType);
        }
        static ValidationResult fail(String reason) {
            return new ValidationResult(false, reason, null, null);
        }
    }

    /**
     * Validates a file that has already been written to {@code filePath}.
     * Does NOT validate {@code MultipartFile} directly so the gate can also be
     * applied to files already on disk (e.g. folder-watch in Phase 7).
     */
    public ValidationResult validate(Path filePath, String originalFileName) {

        // 1. Extension allowlist
        String ext = extension(originalFileName);
        if (!ALLOWED_EXTENSIONS.contains(ext)) {
            return ValidationResult.fail("UNSUPPORTED_EXTENSION:" + ext);
        }

        // 2. File size
        long size;
        try {
            size = Files.size(filePath);
        } catch (IOException e) {
            return ValidationResult.fail("CANNOT_READ_FILE:" + e.getMessage());
        }
        if (size > MAX_FILE_SIZE_BYTES) {
            return ValidationResult.fail("FILE_TOO_LARGE:" + size + "_bytes");
        }

        // 3. MIME sniff from magic bytes — never trust the extension
        String mimeType = sniffMime(filePath);
        if (!ALLOWED_MIME_TYPES.contains(mimeType)) {
            return ValidationResult.fail("UNSUPPORTED_MIME:" + mimeType);
        }

        // 4+5. PDF-specific: opens, page count, blank-page check
        if ("application/pdf".equals(mimeType)) {
            return validatePdf(filePath, mimeType);
        }

        // Images: no page-count limit; no blank check in phase 2
        return ValidationResult.ok(1, mimeType);
    }

    // ── Private helpers ────────────────────────────────────────────────────────

    private ValidationResult validatePdf(Path filePath, String mimeType) {
        try (PDDocument doc = Loader.loadPDF(filePath.toFile())) {

            int pages = doc.getNumberOfPages();
            if (pages == 0) {
                return ValidationResult.fail("PDF_NO_PAGES");
            }
            if (pages > MAX_PAGE_COUNT) {
                return ValidationResult.fail("PDF_TOO_MANY_PAGES:" + pages);
            }

            // Render first page at 72 DPI and check it is not blank
            PDFRenderer renderer = new PDFRenderer(doc);
            BufferedImage img;
            try {
                img = renderer.renderImageWithDPI(0, 72f);
            } catch (Exception e) {
                log.warn("[VALIDATION] Page render failed for {}: {}", filePath.getFileName(), e.getMessage());
                return ValidationResult.fail("PAGE_RENDER_FAILED:" + e.getMessage());
            }

            if (isBlankPage(img)) {
                return ValidationResult.fail("FIRST_PAGE_BLANK");
            }

            return ValidationResult.ok(pages, mimeType);

        } catch (IOException e) {
            log.warn("[VALIDATION] PDF load failed for {}: {}", filePath.getFileName(), e.getMessage());
            return ValidationResult.fail("PDF_CANNOT_OPEN:" + e.getMessage());
        }
    }

    private String sniffMime(Path filePath) {
        try (InputStream is = new BufferedInputStream(Files.newInputStream(filePath))) {
            Metadata metadata = new Metadata();
            MediaType type = tikaDetector.detect(is, metadata);
            return type.toString();
        } catch (IOException e) {
            log.warn("[VALIDATION] MIME detection failed: {}", e.getMessage());
            return "application/octet-stream";
        }
    }

    /**
     * Returns true when the rendered image is considered blank.
     * Converts to grayscale and computes pixel variance; below
     * {@link #BLANK_VARIANCE_THRESHOLD} is blank.
     */
    private boolean isBlankPage(BufferedImage img) {
        int w = img.getWidth(), h = img.getHeight();
        long sum = 0, sumSq = 0;
        long pixels = (long) w * h;

        for (int y = 0; y < h; y++) {
            for (int x = 0; x < w; x++) {
                int rgb  = img.getRGB(x, y);
                int gray = (((rgb >> 16) & 0xFF) + ((rgb >> 8) & 0xFF) + (rgb & 0xFF)) / 3;
                sum   += gray;
                sumSq += (long) gray * gray;
            }
        }

        double mean     = (double) sum   / pixels;
        double variance = (double) sumSq / pixels - mean * mean;
        log.debug("[VALIDATION] Page pixel variance={}", String.format("%.2f", variance));
        return variance < BLANK_VARIANCE_THRESHOLD;
    }

    private static String extension(String filename) {
        if (filename == null) return "";
        int dot = filename.lastIndexOf('.');
        return (dot >= 0) ? filename.substring(dot + 1).toLowerCase() : "";
    }
}
