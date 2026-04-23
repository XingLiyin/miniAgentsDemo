package com.codex.miniagents.domain.model.blackboard;

import lombok.Getter;

import java.time.Instant;

@Getter
public class BlackboardEntry {
    private String id;

    private String sessionId;

    private String topic;

    private String publisherId;

    private String content;

    private Instant createdAt;

    public BlackboardEntry() {
    }

    public BlackboardEntry(String id, String sessionId, String topic, String publisherId, String content,
        Instant createdAt) {
        this.id = id;
        this.sessionId = sessionId;
        this.topic = topic;
        this.publisherId = publisherId;
        this.content = content;
        this.createdAt = createdAt;
    }

    public void setId(String id) {
        this.id = id;
    }

    public void setSessionId(String sessionId) {
        this.sessionId = sessionId;
    }

    public void setTopic(String topic) {
        this.topic = topic;
    }

    public void setPublisherId(String publisherId) {
        this.publisherId = publisherId;
    }

    public void setContent(String content) {
        this.content = content;
    }

    public void setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
    }
}
