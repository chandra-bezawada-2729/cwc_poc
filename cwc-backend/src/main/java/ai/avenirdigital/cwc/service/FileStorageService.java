package ai.avenirdigital.cwc.service;

import ai.avenirdigital.cwc.configuration.StorageProperties;
import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.util.UUID;

/**
 * Stores uploaded fax files under an absolute, startup-resolved base path (§16.5).
 * The stored_path value persisted to the database is relative to that base so it
 * remains portable if the base is re-configured.
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class FileStorageService {

    private final StorageProperties storageProperties;

    /** Absolute, normalised base directory — resolved once at startup. */
    private Path absoluteBase;
    private Path incomingDir;
    private Path routedDir;

    @PostConstruct
    public void init() {
        absoluteBase = Paths.get(storageProperties.getBasePath())
                            .toAbsolutePath()
                            .normalize();
        incomingDir = absoluteBase.resolve(storageProperties.getIncomingDir());
        routedDir   = absoluteBase.resolve(storageProperties.getRoutedDir());

        try {
            Files.createDirectories(incomingDir);
            Files.createDirectories(routedDir);
        } catch (IOException e) {
            throw new IllegalStateException("Cannot create storage directories under: " + absoluteBase, e);
        }

        // §16.5: log the resolved absolute path so the location is never in doubt
        log.info("[STORAGE] Base path resolved: {}", absoluteBase);
        log.info("[STORAGE] Incoming directory ready: {}", incomingDir);
        log.info("[STORAGE] Routed directory ready: {}", routedDir);
    }

    /** Absolute path to the routed tree root. Used by RoutingEngineService. */
    public Path routedBase() { return routedDir; }

    /** Absolute path to the incoming directory. Used by FolderWatcherService. */
    public Path incomingDir() { return incomingDir; }

    /**
     * Saves the file to {@code <base>/incoming/{trackingId}__{originalName}}.
     *
     * @return path relative to basePath (e.g. {@code incoming/{uuid}__{name}});
     *         stored in the database and passed back to {@link #resolve(String)}.
     */
    public String store(MultipartFile file, UUID trackingId) throws IOException {
        String safeName    = sanitize(file.getOriginalFilename());
        String fileName    = trackingId + "__" + safeName;
        Path   destination = incomingDir.resolve(fileName);

        try (InputStream in = file.getInputStream()) {
            Files.copy(in, destination, StandardCopyOption.REPLACE_EXISTING);
        }

        String relativePath = storageProperties.getIncomingDir() + "/" + fileName;
        log.info("[STORAGE] Saved trackingId={} -> {}", trackingId, destination);
        return relativePath;
    }

    /**
     * Streams a file already on disk into {@code <base>/incoming/{trackingId}__{name}}.
     *
     * <p>Used by the inbound folder scanner. Deliberately a copy, not a move: the
     * original must stay in the inbound folder until the document is safely filed,
     * so that a crash mid-pipeline leaves it where the next scan will find it.
     *
     * @return path relative to basePath, for the database
     */
    public String storeFromPath(Path source, UUID trackingId) throws IOException {
        String safeName    = sanitize(source.getFileName().toString());
        String fileName    = trackingId + "__" + safeName;
        Path   destination = incomingDir.resolve(fileName);

        Files.copy(source, destination, StandardCopyOption.REPLACE_EXISTING);

        String relativePath = storageProperties.getIncomingDir() + "/" + fileName;
        log.info("[STORAGE] Copied trackingId={} from {} -> {}", trackingId, source, destination);
        return relativePath;
    }

    /**
     * Resolves a stored relative path back to an absolute {@link Path}.
     */
    public Path resolve(String storedPath) {
        return absoluteBase.resolve(storedPath);
    }

    private static String sanitize(String originalName) {
        if (originalName == null || originalName.isBlank()) return "upload";
        return originalName.replaceAll("[^a-zA-Z0-9.()\\-_]", "_");
    }
}
