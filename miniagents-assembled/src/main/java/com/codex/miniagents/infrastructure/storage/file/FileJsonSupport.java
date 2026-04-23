package com.codex.miniagents.infrastructure.storage.file;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;

import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.AccessDeniedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.stream.Stream;

@Component
public class FileJsonSupport {
    private static final int ATOMIC_WRITE_MAX_RETRIES = 5;

    private final ObjectMapper objectMapper;

    public FileJsonSupport() {
        this.objectMapper = new ObjectMapper();
        this.objectMapper.registerModule(new JavaTimeModule());
    }

    public void writeJsonAtomic(Path path, Object data) {
        try {
            Files.createDirectories(path.getParent());
            Path tmp = path.resolveSibling(path.getFileName() + ".tmp");
            byte[] bytes = objectMapper.writeValueAsBytes(data);
            Files.write(tmp, bytes, StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING,
                StandardOpenOption.WRITE);
            moveWithRetry(tmp, path);
        } catch (IOException e) {
            throw new RuntimeException("Failed to write json atomically: " + path, e);
        }
    }

    private void moveWithRetry(Path tmp, Path path) throws IOException {
        IOException last = null;
        for (int attempt = 0; attempt < ATOMIC_WRITE_MAX_RETRIES; attempt++) {
            try {
                Files.move(tmp, path, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
                return;
            } catch (AccessDeniedException e) {
                last = e;
            } catch (IOException e) {
                if (!isLikelyTransientMoveError(e)) {
                    throw e;
                }
                last = e;
            }

            if (attempt == ATOMIC_WRITE_MAX_RETRIES - 1) {
                break;
            }
            sleepQuietly(20L * (1L << attempt));
        }
        throw last == null ? new IOException("Failed to atomically move temp file to " + path) : last;
    }

    private boolean isLikelyTransientMoveError(IOException e) {
        String message = e.getMessage();
        if (message == null) {
            return false;
        }
        String lower = message.toLowerCase();
        return lower.contains("being used")
            || lower.contains("used by another process")
            || lower.contains("permission denied")
            || lower.contains("access is denied")
            || lower.contains("resource busy");
    }

    private void sleepQuietly(long millis) throws IOException {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("Interrupted while retrying atomic file write", e);
        }
    }

    public <T> Optional<T> readJson(Path path, Class<T> type) {
        if (!Files.exists(path)) {
            return Optional.empty();
        }
        try {
            return Optional.of(objectMapper.readValue(path.toFile(), type));
        } catch (IOException e) {
            throw new RuntimeException("Failed to read json: " + path, e);
        }
    }

    public void appendJsonl(Path path, Object record) {
        try {
            Files.createDirectories(path.getParent());
            String line = objectMapper.writeValueAsString(record) + System.lineSeparator();
            Files.writeString(path, line, StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND,
                StandardOpenOption.WRITE);
        } catch (IOException e) {
            throw new RuntimeException("Failed to append jsonl: " + path, e);
        }
    }

    public <T> List<T> readJsonl(Path path, Class<T> type) {
        if (!Files.exists(path)) {
            return List.of();
        }
        try {
            List<T> results = new ArrayList<>();
            for (String line : Files.readAllLines(path, StandardCharsets.UTF_8)) {
                String trimmed = line == null ? "" : line.trim();
                if (!trimmed.isEmpty()) {
                    results.add(objectMapper.readValue(trimmed, type));
                }
            }
            return results;
        } catch (IOException e) {
            throw new RuntimeException("Failed to read jsonl: " + path, e);
        }
    }

    public <T> Optional<T> readJson(Path path, TypeReference<T> typeReference) {
        if (!Files.exists(path)) {
            return Optional.empty();
        }
        try {
            return Optional.of(objectMapper.readValue(path.toFile(), typeReference));
        } catch (IOException e) {
            throw new RuntimeException("Failed to read json: " + path, e);
        }
    }

    public List<String> listJsonIds(Path directory) {
        if (!Files.exists(directory)) {
            return List.of();
        }
        try (Stream<Path> stream = Files.list(directory)) {
            return stream.filter(p -> p.getFileName().toString().endsWith(".json")).map(p -> {
                String name = p.getFileName().toString();
                return name.substring(0, name.length() - 5);
            }).toList();
        } catch (IOException e) {
            throw new RuntimeException("Failed to list json ids: " + directory, e);
        }
    }
}
