package com.codex.miniagents.infrastructure.storage.repository;

import com.codex.miniagents.domain.model.blackboard.BlackboardEntry;

import java.util.List;

public interface BlackboardRepository {
    void append(String sessionId, String topic, BlackboardEntry entry);

    List<BlackboardEntry> readAll(String sessionId, String topic);

    List<BlackboardEntry> readSince(String sessionId, String topic, int cursor);

    List<String> listTopics(String sessionId);

    void deleteSession(String sessionId);
}
