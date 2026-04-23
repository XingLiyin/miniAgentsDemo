package com.codex.miniagents.domain.model.session;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.Getter;

import java.time.Instant;
import java.util.HashMap;
import java.util.Map;

@Getter
@JsonIgnoreProperties(ignoreUnknown = true)
public class Session {
    private String id;

    private String userPrompt = "";

    private String goal;

    private SessionStatus status;

    private String templateId;

    private String rootAgentId;

    private int tokenBudget = 200_000;

    private int tokenUsed = 0;

    private int rootMaxTurns = 20;

    private int failureCounter = 0;

    private int failureThreshold = 3;

    private Instant createdAt;

    private Instant updatedAt;

    private Map<String, Object> metadata = new HashMap<>();

    public Session() {
    }

    public Session(String id, String goal, SessionStatus status, String templateId, String rootAgentId, int tokenBudget,
        int tokenUsed, int rootMaxTurns, int failureCounter, int failureThreshold, Instant createdAt, Instant updatedAt,
        Map<String, Object> metadata) {
        this(id, goal, goal, status, templateId, rootAgentId, tokenBudget, tokenUsed, rootMaxTurns, failureCounter,
            failureThreshold, createdAt, updatedAt, metadata);
    }

    public Session(String id, String userPrompt, String goal, SessionStatus status, String templateId,
        String rootAgentId, int tokenBudget, int tokenUsed, int rootMaxTurns, int failureCounter, int failureThreshold,
        Instant createdAt, Instant updatedAt, Map<String, Object> metadata) {
        this.id = id;
        this.userPrompt = userPrompt == null ? "" : userPrompt;
        this.goal = goal;
        this.status = status;
        this.templateId = templateId;
        this.rootAgentId = rootAgentId;
        this.tokenBudget = tokenBudget;
        this.tokenUsed = tokenUsed;
        this.rootMaxTurns = rootMaxTurns;
        this.failureCounter = failureCounter;
        this.failureThreshold = failureThreshold;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
        this.metadata = metadata == null ? new HashMap<>() : new HashMap<>(metadata);
    }

    public void bindRootAgent(String agentId) {
        this.rootAgentId = agentId;
    }

    public void addTokens(int delta) {
        this.tokenUsed += delta;
    }

    public void incrementFailureCounter() {
        this.failureCounter += 1;
    }

    public void resetFailureCounter() {
        this.failureCounter = 0;
    }

    @JsonIgnore
    public boolean isTerminal() {
        return this.status != null && this.status.isTerminal();
    }

    @JsonIgnore
    public boolean isWaitingInput() {
        return this.status == SessionStatus.WAITING_INPUT;
    }

    public void setId(String id) {
        this.id = id;
    }

    public void setUserPrompt(String userPrompt) {
        this.userPrompt = userPrompt == null ? "" : userPrompt;
    }

    public void setGoal(String goal) {
        this.goal = goal;
    }

    public void setStatus(SessionStatus status) {
        this.status = status;
    }

    public void setTemplateId(String templateId) {
        this.templateId = templateId;
    }

    public void setRootAgentId(String rootAgentId) {
        this.rootAgentId = rootAgentId;
    }

    public void setTokenBudget(int tokenBudget) {
        this.tokenBudget = tokenBudget;
    }

    public void setTokenUsed(int tokenUsed) {
        this.tokenUsed = tokenUsed;
    }

    public void setRootMaxTurns(int rootMaxTurns) {
        this.rootMaxTurns = rootMaxTurns;
    }

    public void setFailureCounter(int failureCounter) {
        this.failureCounter = failureCounter;
    }

    public void setFailureThreshold(int failureThreshold) {
        this.failureThreshold = failureThreshold;
    }

    public void setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
    }

    public void setUpdatedAt(Instant updatedAt) {
        this.updatedAt = updatedAt;
    }

    public void setMetadata(Map<String, Object> metadata) {
        this.metadata = metadata == null ? new HashMap<>() : new HashMap<>(metadata);
    }
}
