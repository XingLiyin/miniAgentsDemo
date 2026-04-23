package com.codex.miniagents.domain.model.task;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.Getter;

import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Map;
import java.util.List;

@Getter
@JsonIgnoreProperties(ignoreUnknown = true)
public class Task {
    private String id;

    private String sessionId;

    private String creatorAgentId;

    private String assignedAgentId;

    private String userPrompt = "";

    private String title;

    private TaskStatus status;

    private String description = "";

    private Map<String, Object> settings = new HashMap<>();

    private String result;

    private Map<String, Object> outputs = new HashMap<>();

    private String error;

    private String parentTaskId;

    private int retryCount;

    private List<String> dagDeps = new ArrayList<>();

    private Instant createdAt;

    private Instant updatedAt;

    @JsonIgnore
    private boolean actorDone;

    @JsonIgnore
    private String actorOutcome = "";

    @JsonIgnore
    private String actorResult = "";

    @JsonIgnore
    private String actorSummary = "";

    @JsonIgnore
    private boolean proceedToReview;

    public Task() {
    }

    public void setResult(String result) {
        this.result = result;
    }

    public void setOutputs(Map<String, Object> outputs) {
        this.outputs = outputs == null ? new HashMap<>() : new HashMap<>(outputs);
    }

    public void setError(String error) {
        this.error = error;
    }

    public void setId(String id) {
        this.id = id;
    }

    public void setSessionId(String sessionId) {
        this.sessionId = sessionId;
    }

    public void setCreatorAgentId(String creatorAgentId) {
        this.creatorAgentId = creatorAgentId;
    }

    public void setAssignedAgentId(String assignedAgentId) {
        this.assignedAgentId = assignedAgentId;
    }

    public void setUserPrompt(String userPrompt) {
        this.userPrompt = userPrompt == null ? "" : userPrompt;
    }

    public void setTitle(String title) {
        this.title = title;
    }

    public void setStatus(TaskStatus status) {
        this.status = status;
    }

    public void setDescription(String description) {
        this.description = description;
    }

    public void setSettings(Map<String, Object> settings) {
        this.settings = settings == null ? new HashMap<>() : new HashMap<>(settings);
    }

    public void setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
    }

    public void setUpdatedAt(Instant updatedAt) {
        this.updatedAt = updatedAt;
    }

    public void setActorDone(boolean actorDone) {
        this.actorDone = actorDone;
    }

    public void setActorOutcome(String actorOutcome) {
        this.actorOutcome = actorOutcome == null ? "" : actorOutcome;
    }

    public void setActorResult(String actorResult) {
        this.actorResult = actorResult == null ? "" : actorResult;
    }

    public void setActorSummary(String actorSummary) {
        this.actorSummary = actorSummary == null ? "" : actorSummary;
    }

    public void setProceedToReview(boolean proceedToReview) {
        this.proceedToReview = proceedToReview;
    }

    public void setParentTaskId(String parentTaskId) {
        this.parentTaskId = parentTaskId;
    }

    public void setRetryCount(int retryCount) {
        this.retryCount = retryCount;
    }

    public void setDagDeps(List<String> dagDeps) {
        this.dagDeps = dagDeps == null ? new ArrayList<>() : new ArrayList<>(dagDeps);
    }
}
