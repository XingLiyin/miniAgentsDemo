package com.codex.miniagents.domain.service;

import com.codex.miniagents.domain.model.blackboard.BlackboardEntry;
import com.codex.miniagents.domain.model.blackboard.TopicCursor;
import com.codex.miniagents.infrastructure.storage.repository.BlackboardRepository;
import com.codex.miniagents.utils.IdUtils;

import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@Service
public class BlackboardService {
    private final BlackboardRepository repository;

    private final Map<String, TopicCursor> cursors = new ConcurrentHashMap<>();

    public BlackboardService(BlackboardRepository repository) {
        this.repository = repository;
    }

    public BlackboardEntry publish(String sessionId, String topic, String publisherId, String content) {
        BlackboardEntry entry = new BlackboardEntry();
        entry.setId(IdUtils.newBlackboardEntryId());
        entry.setSessionId(sessionId);
        entry.setTopic(topic);
        entry.setPublisherId(publisherId);
        entry.setContent(content);
        entry.setCreatedAt(Instant.now());
        repository.append(sessionId, topic, entry);
        return entry;
    }

    public List<BlackboardEntry> pull(String sessionId, String topic, String agentId) {
        String key = cursorKey(agentId, sessionId, topic);
        TopicCursor cursor = cursors.computeIfAbsent(key, ignored -> new TopicCursor(agentId, topic, 0));
        int lastRead = cursor.getLastRead();
        List<BlackboardEntry> entries = repository.readSince(sessionId, topic, lastRead);
        cursor.setLastRead(lastRead + entries.size());
        return entries;
    }

    public List<BlackboardEntry> subscribe(String sessionId, String topic) {
        return repository.readAll(sessionId, topic);
    }

    public List<String> listTopics(String sessionId) {
        return repository.listTopics(sessionId);
    }

    public void deleteSession(String sessionId) {
        repository.deleteSession(sessionId);
    }

    private String cursorKey(String agentId, String sessionId, String topic) {
        return agentId + "::" + sessionId + "::" + topic;
    }
}
