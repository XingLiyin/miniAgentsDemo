package com.codex.miniagents.infrastructure.storage.file;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.domain.memory.model.MemoryItem;
import com.codex.miniagents.domain.memory.model.MemorySummary;
import com.codex.miniagents.infrastructure.storage.repository.MemoryRepository;

import org.springframework.stereotype.Repository;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Optional;
import java.util.stream.Stream;

@Repository
public class MemoryFileRepository implements MemoryRepository {
    private final Path baseDir;

    private final FileJsonSupport jsonSupport;

    public MemoryFileRepository(MiniAgentsProperties properties, FileJsonSupport jsonSupport) {
        this.baseDir = properties.getDataDir().resolve("memory");
        this.jsonSupport = jsonSupport;
    }

    private Path agentDir(String agentId) {
        return baseDir.resolve(agentId);
    }

    private Path messagesPath(String agentId) {
        return agentDir(agentId).resolve("messages.jsonl");
    }

    private Path summaryPath(String agentId) {
        return agentDir(agentId).resolve("summaries.json");
    }

    @Override
    public void appendMessage(String agentId, MemoryItem item) {
        jsonSupport.appendJsonl(messagesPath(agentId), item);
    }

    @Override
    public List<MemoryItem> readMessages(String agentId) {
        return jsonSupport.readJsonl(messagesPath(agentId), MemoryItem.class);
    }

    @Override
    public List<MemoryItem> readWindow(String agentId, int n) {
        List<MemoryItem> all = readMessages(agentId);
        if (all.isEmpty()) {
            return List.of();
        }
        if (n == 0) {
            return all;
        }
        int from;
        if (n > 0) {
            from = Math.max(0, all.size() - n);
        } else {
            from = -n;
            if (from >= all.size()) {
                return List.of();
            }
        }
        return all.subList(from, all.size());
    }

    @Override
    public void saveSummary(String agentId, MemorySummary summary) {
        jsonSupport.writeJsonAtomic(summaryPath(agentId), summary);
    }

    @Override
    public Optional<MemorySummary> getSummary(String agentId) {
        return jsonSupport.readJson(summaryPath(agentId), MemorySummary.class);
    }

    @Override
    public int countMessages(String agentId) {
        return readMessages(agentId).size();
    }

    @Override
    public void deleteAgent(String agentId) {
        Path dir = agentDir(agentId);
        if (!Files.exists(dir)) {
            return;
        }
        try (Stream<Path> stream = Files.walk(dir)) {
            stream.sorted(java.util.Comparator.reverseOrder()).forEach(path -> {
                try {
                    Files.deleteIfExists(path);
                } catch (IOException e) {
                    throw new RuntimeException("Failed to delete memory path: " + path, e);
                }
            });
        } catch (IOException e) {
            throw new RuntimeException("Failed to delete memory for agent: " + agentId, e);
        }
    }
}
