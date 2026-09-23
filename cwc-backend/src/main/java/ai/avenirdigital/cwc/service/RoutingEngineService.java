package ai.avenirdigital.cwc.service;

import ai.avenirdigital.cwc.model.RoutingDecisionEntity;
import ai.avenirdigital.cwc.repository.RoutingDecisionRepository;
import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.yaml.snakeyaml.Yaml;

import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.FileTime;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Routing engine — spec §6.
 *
 * Resolution order: sender-overrides → subtype-overrides → rules → manual review.
 *
 * No category or folder string literals appear in Java control flow; all resolution
 * is driven by the loaded config. An unmapped category falls to Manual-Review with
 * a warning, never an exception.
 *
 * Hot-reload: POST /api/routing/reload calls reload(), which re-reads
 * routing-config.yml from the classpath. In dev (mvn spring-boot:run) the
 * source file is on the classpath, so edits are picked up immediately.
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class RoutingEngineService {

    private final FileStorageService         fileStorageService;
    private final RoutingDecisionRepository  routingRepo;

    /** Live config — replaced atomically on reload. */
    private volatile LiveRoutingConfig liveConfig;

    // ── Inner config types ────────────────────────────────────────────────────

    public record RoutingRule(String category, String folder) {}
    public record SubtypeOverride(String category, String subtype, String folder) {}
    public record SenderOverride(String senderFax, String folder) {}

    /** cwc.routing.naming — controls which suggested filename the engine proposes. */
    public record NamingConfig(
            String style,               // HOUSE | SNAKE
            String collisionSuffix,     // e.g. " ({n})"
            boolean preserveOriginalInAudit
    ) {
        static NamingConfig defaults() {
            return new NamingConfig("HOUSE", " ({n})", true);
        }
    }

    /**
     * In AUTO mode, should a document that fails the confidence gate still be
     * written to the Manual-Review folder?
     *
     * <p>True keeps the inbound folder draining — everything leaves, uncertain
     * items land in one place staff already watch. False holds uncertain items
     * in the app and leaves their originals in the inbound folder.
     */
    public boolean isFileUncertainToReviewFolder() {
        return liveConfig.fileUncertainToReviewFolder();
    }

    /**
     * Default when routing-config.yml says nothing. Matches the shipped value so
     * a missing key behaves like the documented policy rather than like 0.
     */
    static final double DEFAULT_MIN_CONFIDENCE = 0.90;

    /** Minimum calibrated confidence for a document to file itself. */
    public double getAutoRouteMinConfidence() {
        return liveConfig.autoRouteMinConfidence();
    }

    /**
     * True when this document is confident enough to file without a human.
     *
     * <p>Null confidence counts as NOT confident. A document the model could not
     * score is exactly the one a person should see, and treating null as passing
     * would auto-file every classification failure.
     */
    public boolean meetsConfidenceGate(Number calibratedConfidence) {
        double min = getAutoRouteMinConfidence();
        if (min <= 0) return true;
        return calibratedConfidence != null && calibratedConfidence.doubleValue() >= min;
    }

    public record LiveRoutingConfig(
            String mode,                          // SUGGEST | AUTO
            String manualReviewFolder,
            List<String> autoRouteDisabled,
            List<RoutingRule> rules,
            List<SubtypeOverride> subtypeOverrides,
            List<SenderOverride> senderOverrides,
            NamingConfig naming,
            boolean fileUncertainToReviewFolder,
            double autoRouteMinConfidence
    ) {
        /**
         * Pre-v2.0 six-argument constructor, kept so existing unit tests that
         * build a config by hand keep compiling. Naming falls back to defaults.
         */
        public LiveRoutingConfig(
                String mode,
                String manualReviewFolder,
                List<String> autoRouteDisabled,
                List<RoutingRule> rules,
                List<SubtypeOverride> subtypeOverrides,
                List<SenderOverride> senderOverrides
        ) {
            this(mode, manualReviewFolder, autoRouteDisabled, rules,
                 subtypeOverrides, senderOverrides, NamingConfig.defaults(), true,
                 DEFAULT_MIN_CONFIDENCE);
        }

        /** Seven-argument form from the taxonomy-v2.0 change. */
        public LiveRoutingConfig(
                String mode,
                String manualReviewFolder,
                List<String> autoRouteDisabled,
                List<RoutingRule> rules,
                List<SubtypeOverride> subtypeOverrides,
                List<SenderOverride> senderOverrides,
                NamingConfig naming
        ) {
            this(mode, manualReviewFolder, autoRouteDisabled, rules,
                 subtypeOverrides, senderOverrides, naming, true,
                 DEFAULT_MIN_CONFIDENCE);
        }
    }

    /** Suggestion result returned to callers. */
    public record SuggestResult(String folder, String ruleMatched) {}

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    @PostConstruct
    public void init() {
        reload();
    }

    /**
     * Re-reads routing-config.yml from the classpath.
     * Thread-safe: replaces the volatile reference atomically.
     */
    public synchronized void reload() {
        try (InputStream is = getClass().getClassLoader()
                .getResourceAsStream("routing-config.yml")) {
            if (is == null) {
                throw new IllegalStateException("routing-config.yml not found on classpath");
            }
            liveConfig = parseYaml(is);
            log.info("[ROUTING] Config loaded: mode={} rules={} senderOverrides={} subtypeOverrides={}",
                    liveConfig.mode(), liveConfig.rules().size(),
                    liveConfig.senderOverrides().size(), liveConfig.subtypeOverrides().size());
        } catch (Exception e) {
            if (liveConfig == null) throw new IllegalStateException("Cannot load routing config", e);
            log.error("[ROUTING] Reload failed - retaining previous config: {}", e.getMessage());
        }
    }

    // ── Public API ────────────────────────────────────────────────────────────

    /** Current routing mode (SUGGEST | AUTO). */
    public String getMode() {
        return liveConfig.mode();
    }

    /**
     * Returns true when the category is in auto_route_disabled list.
     * forceManualReview=true in the classification result ALSO queues, regardless of mode.
     */
    public boolean isAutoRouteDisabled(String category) {
        return liveConfig.autoRouteDisabled().contains(category);
    }

    /**
     * Resolves the suggested folder for a given category/subtype/sender combination
     * using the loaded config. Resolution order: sender → subtype → category → manual review.
     */
    public SuggestResult suggest(String category, String subtype, String senderFax) {
        LiveRoutingConfig cfg = liveConfig;

        // 1. Sender overrides — wins over everything when the sender fax matches
        if (senderFax != null && !senderFax.isBlank()) {
            for (SenderOverride so : cfg.senderOverrides()) {
                if (senderFax.equals(so.senderFax())) {
                    log.info("[ROUTING] Sender override matched: senderFax={} -> folder={}", senderFax, so.folder());
                    return new SuggestResult(so.folder(), "sender:" + senderFax);
                }
            }
        }

        // 2. Subtype overrides — more specific than category rules
        if (category != null && subtype != null) {
            for (SubtypeOverride so : cfg.subtypeOverrides()) {
                if (category.equals(so.category()) && subtype.equals(so.subtype())) {
                    log.info("[ROUTING] Subtype override matched: {}/{} -> {}", category, subtype, so.folder());
                    return new SuggestResult(so.folder(), "subtype:" + category + "/" + subtype);
                }
            }
        }

        // 3. Category rules
        if (category != null) {
            for (RoutingRule rule : cfg.rules()) {
                if (category.equals(rule.category())) {
                    log.info("[ROUTING] Category rule matched: {} -> {}", category, rule.folder());
                    return new SuggestResult(rule.folder(), "category:" + category);
                }
            }
        }

        // 4. Unmapped — fall to manual review (never throw)
        log.warn("[ROUTING] No rule matched for category={} subtype={} - falling to {}",
                category, subtype, cfg.manualReviewFolder());
        return new SuggestResult(cfg.manualReviewFolder(), "unmapped");
    }

    /**
     * Picks the filename to propose, honouring cwc.routing.naming.style.
     *
     * @param houseName     suggestedFileName from the AI service ("US Abdomen.pdf")
     * @param snakeName     alternateFileName from the AI service ("us_abdomen_radiologyreport.pdf")
     * @param originalName  the inbound filename, used if neither suggestion exists
     */
    public String chooseFileName(String houseName, String snakeName, String originalName) {
        NamingConfig naming = liveConfig.naming();
        String preferred = "SNAKE".equalsIgnoreCase(naming.style()) ? snakeName : houseName;
        String fallback  = "SNAKE".equalsIgnoreCase(naming.style()) ? houseName : snakeName;

        if (preferred != null && !preferred.isBlank()) return preferred;
        if (fallback  != null && !fallback.isBlank())  return fallback;

        log.warn("[ROUTING] No suggested filename available - keeping original name");
        return originalName;
    }

    /**
     * Resolves a collision-free destination inside {@code folder}.
     *
     * <p>CWC's folders are flat and human-browsed, so two "US Abdomen.pdf" faxes
     * for different patients WILL collide — and unlike the old
     * {@code trackingId__originalName} scheme, a house-style name carries nothing
     * unique. Overwriting would destroy a patient document, so the collision
     * suffix from config is appended: "US Abdomen (2).pdf", "US Abdomen (3).pdf".
     *
     * <p>Returns the first free path. Gives up after 999 attempts and falls back
     * to a tracking-id-prefixed name, which is guaranteed unique.
     */
    public Path resolveDestination(String folder, String fileName, String trackingId) {
        Path dir = fileStorageService.routedBase().resolve(folder);
        Path candidate = dir.resolve(fileName);
        if (!Files.exists(candidate)) {
            return candidate;
        }

        String suffixTemplate = liveConfig.naming().collisionSuffix();
        int dot = fileName.lastIndexOf('.');
        String stem = dot > 0 ? fileName.substring(0, dot) : fileName;
        String ext  = dot > 0 ? fileName.substring(dot)    : "";

        for (int n = 2; n <= 999; n++) {
            String suffix = suffixTemplate.replace("{n}", Integer.toString(n));
            candidate = dir.resolve(stem + suffix + ext);
            if (!Files.exists(candidate)) {
                log.info("[ROUTING] Name collision on {} - using {}", fileName, candidate.getFileName());
                return candidate;
            }
        }

        log.warn("[ROUTING] Exhausted collision suffixes for {} - falling back to tracking id", fileName);
        return dir.resolve(trackingId + "__" + fileName);
    }

    /**
     * Copies the source file into {@code {routed-base}/{folder}/{fileName}},
     * appending a collision suffix rather than overwriting an existing document.
     *
     * <p>Behaviour change in v2.0: the destination is now the SUGGESTED NAME, not
     * {@code trackingId__originalName}, because CWC's whole manual process is a
     * rename. The original filename is preserved in the routing audit row
     * (see {@code routing_decisions.original_file_name}) so nothing is lost.
     *
     * @return absolute path of the filed copy
     */
    public Path fileDocument(Path source, String trackingId, String fileName, String folder)
            throws java.io.IOException {
        Path dest = resolveDestination(folder, fileName, trackingId);
        Files.createDirectories(dest.getParent());
        // CREATE_NEW would be ideal, but resolveDestination has already picked a
        // free name; REPLACE_EXISTING only matters in the 999-collision fallback.
        Files.copy(source, dest, StandardCopyOption.REPLACE_EXISTING);

        // Stamp the filed copy with the time it was FILED. On Windows,
        // Files.copy goes through CopyFileEx, which carries the source's
        // last-write time across even without COPY_ATTRIBUTES - so a fax filed
        // today showed whatever date the PDF last had on someone's disk, and
        // sorting an outbound folder by "Date modified" to find today's post
        // listed it among weeks-old files. The original's own timestamp is not
        // lost: the archived copy in _processed keeps it.
        try {
            Files.setLastModifiedTime(dest, FileTime.from(Instant.now()));
        } catch (java.io.IOException e) {
            // Cosmetic. The document is filed; a stale date must not fail that.
            log.warn("[ROUTING] Could not set filed time on {}: {}", dest, e.getMessage());
        }

        log.info("[ROUTING] Filed trackingId={} -> {}", trackingId, dest);
        return dest;
    }

    /**
     * Returns the effective config as a plain map suitable for JSON serialisation.
     */
    public Map<String, Object> getConfigMap() {
        LiveRoutingConfig cfg = liveConfig;
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("mode", cfg.mode());
        map.put("manualReviewFolder", cfg.manualReviewFolder());
        map.put("autoRouteDisabled", cfg.autoRouteDisabled());

        List<Map<String, String>> rules = new ArrayList<>();
        for (RoutingRule r : cfg.rules()) {
            rules.add(Map.of("category", r.category(), "folder", r.folder()));
        }
        map.put("rules", rules);

        List<Map<String, String>> subtypeOverrides = new ArrayList<>();
        for (SubtypeOverride so : cfg.subtypeOverrides()) {
            subtypeOverrides.add(Map.of("category", so.category(),
                    "subtype", so.subtype(), "folder", so.folder()));
        }
        map.put("subtypeOverrides", subtypeOverrides);

        List<Map<String, String>> senderOverrides = new ArrayList<>();
        for (SenderOverride so : cfg.senderOverrides()) {
            senderOverrides.add(Map.of("senderFax", so.senderFax(), "folder", so.folder()));
        }
        map.put("senderOverrides", senderOverrides);

        NamingConfig n = cfg.naming();
        map.put("naming", Map.of(
                "style", n.style(),
                "collisionSuffix", n.collisionSuffix(),
                "preserveOriginalInAudit", n.preserveOriginalInAudit()
        ));
        map.put("fileUncertainToReviewFolder", cfg.fileUncertainToReviewFolder());
        map.put("autoRouteMinConfidence", cfg.autoRouteMinConfidence());
        return map;
    }

    /**
     * Runtime mode override — does NOT write routing-config.yml.
     * Intended for demo use (the Routing Config page toggle) and tests.
     * The next reload() call restores the YAML-defined mode.
     *
     * Valid values: "SUGGEST", "AUTO".
     */
    public synchronized void setRuntimeMode(String mode) {
        if (!"SUGGEST".equals(mode) && !"AUTO".equals(mode)) {
            throw new IllegalArgumentException("mode must be SUGGEST or AUTO, got: " + mode);
        }
        LiveRoutingConfig old = liveConfig;
        liveConfig = new LiveRoutingConfig(
                mode,
                old.manualReviewFolder(),
                old.autoRouteDisabled(),
                old.rules(),
                old.subtypeOverrides(),
                old.senderOverrides(),
                old.naming(),
                old.fileUncertainToReviewFolder(),
                old.autoRouteMinConfidence()
        );
        log.info("[ROUTING] Runtime mode overridden to {} (YAML value unchanged)", mode);
    }

    /** Returns the name of the configured manual-review folder. */
    public String getManualReviewFolder() {
        return liveConfig.manualReviewFolder();
    }

    // ── Package-private accessor for unit tests ───────────────────────────────

    /** Replaces the live config directly — used only in unit tests. */
    void setLiveConfig(LiveRoutingConfig cfg) {
        this.liveConfig = cfg;
    }

    // ── YAML parsing ──────────────────────────────────────────────────────────

    @SuppressWarnings("unchecked")
    private static LiveRoutingConfig parseYaml(InputStream is) {
        Yaml yaml = new Yaml();
        Map<String, Object> root    = yaml.load(is);
        Map<String, Object> cwc     = (Map<String, Object>) root.get("cwc");
        Map<String, Object> routing = (Map<String, Object>) cwc.get("routing");

        // Mode resolution: env > YAML > SUGGEST.
        // The tracked file stays SUGGEST so a fresh checkout never auto-files;
        // AUTO is a deployment decision, set via CWC_ROUTING_MODE.
        String mode = System.getenv("CWC_ROUTING_MODE");
        String modeSource = "env CWC_ROUTING_MODE";
        if (mode == null || mode.isBlank()) {
            mode = str(routing, "mode", "SUGGEST");
            modeSource = "routing-config.yml";
        }
        mode = mode.trim().toUpperCase();
        if (!"SUGGEST".equals(mode) && !"AUTO".equals(mode)) {
            log.warn("[ROUTING] Unknown mode {} - falling back to SUGGEST", mode);
            mode = "SUGGEST";
            modeSource += " (unrecognised, defaulted)";
        }
        log.info("[ROUTING] Mode resolved: {} (source: {})", mode, modeSource);

        String manualReviewFolder = str(routing, "manual-review-folder", "Manual-Review");

        List<String> autoDisabled = new ArrayList<>();
        Object ad = routing.get("auto-route-disabled");
        if (ad instanceof List<?> list) {
            list.forEach(v -> autoDisabled.add(v.toString()));
        }

        List<RoutingRule> rules = new ArrayList<>();
        List<Map<String, String>> rawRules =
                (List<Map<String, String>>) routing.getOrDefault("rules", List.of());
        for (Map<String, String> r : rawRules) {
            if (r.get("category") != null && r.get("folder") != null) {
                rules.add(new RoutingRule(r.get("category"), r.get("folder")));
            }
        }

        List<SubtypeOverride> subtypeOverrides = new ArrayList<>();
        List<Map<String, String>> rawSub =
                (List<Map<String, String>>) routing.getOrDefault("subtype-overrides", List.of());
        for (Map<String, String> s : rawSub) {
            if (s.get("category") != null && s.get("subtype") != null && s.get("folder") != null) {
                subtypeOverrides.add(new SubtypeOverride(s.get("category"), s.get("subtype"), s.get("folder")));
            }
        }

        List<SenderOverride> senderOverrides = new ArrayList<>();
        Object rawSender = routing.get("sender-overrides");
        if (rawSender instanceof List<?> rawList && !rawList.isEmpty()) {
            for (Object item : rawList) {
                if (item instanceof Map<?, ?> m) {
                    Object sf = m.get("senderFax");
                    Object fo = m.get("folder");
                    if (sf != null && fo != null) {
                        senderOverrides.add(new SenderOverride(sf.toString(), fo.toString()));
                    }
                }
            }
        }

        NamingConfig naming = NamingConfig.defaults();
        Object rawNaming = routing.get("naming");
        if (rawNaming instanceof Map<?, ?> nm) {
            Object style     = nm.get("style");
            Object collision = nm.get("collision-suffix");
            Object preserve  = nm.get("preserve-original-in-audit");
            naming = new NamingConfig(
                    style instanceof String s && !s.isBlank() ? s : naming.style(),
                    collision instanceof String c && c.contains("{n}") ? c : naming.collisionSuffix(),
                    preserve instanceof Boolean b ? b : naming.preserveOriginalInAudit()
            );
            if (collision != null && !(collision instanceof String c2 && c2.contains("{n}"))) {
                log.warn("[ROUTING] collision-suffix {} has no {{n}} placeholder - using default {}",
                        collision, naming.collisionSuffix());
            }
        }

        Object rawUncertain = routing.get("file-uncertain-to-review-folder");
        boolean fileUncertain = !(rawUncertain instanceof Boolean b) || b;

        Object rawMin = routing.get("auto-route-min-confidence");
        double minConfidence = rawMin instanceof Number num
                ? num.doubleValue() : DEFAULT_MIN_CONFIDENCE;
        if (minConfidence < 0 || minConfidence > 1) {
            log.warn("[ROUTING] auto-route-min-confidence {} is outside 0..1 - using {}",
                    rawMin, DEFAULT_MIN_CONFIDENCE);
            minConfidence = DEFAULT_MIN_CONFIDENCE;
        }

        return new LiveRoutingConfig(mode, manualReviewFolder, autoDisabled, rules,
                subtypeOverrides, senderOverrides, naming, fileUncertain, minConfidence);
    }

    private static String str(Map<String, Object> m, String key, String def) {
        Object v = m.get(key);
        return v instanceof String s ? s : def;
    }
}
