package com.codex.miniagents.infrastructure.storage.file;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.domain.model.tool.ToolCall;
import com.codex.miniagents.infrastructure.storage.repository.ToolCallRepository;

import org.springframework.stereotype.Repository;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

@Repository
public class ToolCallFileRepository implements ToolCallRepository {
    private final Path baseDir;

    private final FileJsonSupport jsonSupport;

    public ToolCallFileRepository(MiniAgentsProperties properties, FileJsonSupport jsonSupport) {
        this.baseDir = properties.getDataDir().resolve("tool_calls");
        this.jsonSupport = jsonSupport;
    }

    private Path path(String sessionId) {
        return baseDir.resolve(sessionId + ".jsonl");
    }

    @Override
    public void append(String sessionId, ToolCall toolCall) {
        jsonSupport.appendJsonl(path(sessionId), toolCall);
    }

    @Override
    public List<ToolCall> findBySessionId(String sessionId) {
        return jsonSupport.readJsonl(path(sessionId), ToolCall.class);
    }

    @Override
    public void delete(String sessionId) {
        try {
            Files.deleteIfExists(path(sessionId));
        } catch (IOException e) {
            throw new RuntimeException("Failed to delete tool calls for session: " + sessionId, e);
        }
    }
}
