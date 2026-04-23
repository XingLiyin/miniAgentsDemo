package com.codex.miniagents.infrastructure.storage.repository;

import com.codex.miniagents.domain.memory.model.MemoryItem;
import com.codex.miniagents.domain.memory.model.MemorySummary;

import java.util.List;
import java.util.Optional;

public interface MemoryRepository {
    void appendMessage(String agentId, MemoryItem item);

    List<MemoryItem> readMessages(String agentId);

    List<MemoryItem> readWindow(String agentId, int n);

    void saveSummary(String agentId, MemorySummary summary);

    Optional<MemorySummary> getSummary(String agentId);

    int countMessages(String agentId);

    void deleteAgent(String agentId);
}
