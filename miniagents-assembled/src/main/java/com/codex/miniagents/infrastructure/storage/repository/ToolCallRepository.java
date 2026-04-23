package com.codex.miniagents.infrastructure.storage.repository;

import com.codex.miniagents.domain.model.tool.ToolCall;

import java.util.List;

public interface ToolCallRepository {
    void append(String sessionId, ToolCall toolCall);

    List<ToolCall> findBySessionId(String sessionId);

    void delete(String sessionId);
}
