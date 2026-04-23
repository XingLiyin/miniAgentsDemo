package com.codex.miniagents.infrastructure.storage.file;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.domain.model.blackboard.BlackboardEntry;
import com.codex.miniagents.infrastructure.storage.repository.BlackboardRepository;

import org.springframework.stereotype.Repository;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.stream.Stream;

@Repository
public class BlackboardFileRepository implements BlackboardRepository {
    private final Path baseDir;

    private final FileJsonSupport jsonSupport;

    public BlackboardFileRepository(MiniAgentsProperties properties, FileJsonSupport jsonSupport) {
        this.baseDir = properties.getDataDir().resolve("blackboard");
        this.jsonSupport = jsonSupport;
    }

    private Path sessionDir(String sessionId) {
        return baseDir.resolve(sessionId);
    }

    private Path path(String sessionId, String topic) {
        return sessionDir(sessionId).resolve(topic + ".jsonl");
    }

    @Override
    public void append(String sessionId, String topic, BlackboardEntry entry) {
        jsonSupport.appendJsonl(path(sessionId, topic), entry);
    }

    @Override
    public List<BlackboardEntry> readAll(String sessionId, String topic) {
        return jsonSupport.readJsonl(path(sessionId, topic), BlackboardEntry.class);
    }

    @Override
    public List<BlackboardEntry> readSince(String sessionId, String topic, int cursor) {
        List<BlackboardEntry> all = readAll(sessionId, topic);
        if (cursor <= 0) {
            return all;
        }
        if (cursor >= all.size()) {
            return List.of();
        }
        return all.subList(cursor, all.size());
    }

    @Override
    public List<String> listTopics(String sessionId) {
        Path dir = sessionDir(sessionId);
        if (!Files.exists(dir)) {
            return List.of();
        }
        try (Stream<Path> stream = Files.list(dir)) {
            return stream.filter(p -> p.getFileName().toString().endsWith(".jsonl")).map(p -> {
                String name = p.getFileName().toString();
                return name.substring(0, name.length() - 6);
            }).toList();
        } catch (IOException e) {
            throw new RuntimeException("Failed to list blackboard topics for session: " + sessionId, e);
        }
    }

    @Override
    public void deleteSession(String sessionId) {
        Path dir = sessionDir(sessionId);
        if (!Files.exists(dir)) {
            return;
        }
        try (Stream<Path> stream = Files.walk(dir)) {
            stream.sorted(java.util.Comparator.reverseOrder()).forEach(path -> {
                try {
                    Files.deleteIfExists(path);
                } catch (IOException e) {
                    throw new RuntimeException("Failed to delete blackboard path: " + path, e);
                }
            });
        } catch (IOException e) {
            throw new RuntimeException("Failed to delete blackboard for session: " + sessionId, e);
        }
    }
}
