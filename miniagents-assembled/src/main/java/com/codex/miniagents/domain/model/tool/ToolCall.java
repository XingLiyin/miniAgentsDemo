package com.codex.miniagents.domain.model.tool;

import lombok.Getter;

import java.time.Instant;
import java.util.HashMap;
import java.util.Map;

@Getter
public class ToolCall {
    private String id;

    private String sessionId;

    private String taskId;

    private String agentId;

    private String toolName;

    private String status;

    private Map<String, Object> arguments = new HashMap<>();

    private String result;

    private String error;

    private Instant startedAt;

    private Instant finishedAt;

    public ToolCall() {
    }

    public ToolCall(String id, String sessionId, String taskId, String agentId, String toolName, String status,
        Map<String, Object> arguments, String result, String error, Instant startedAt, Instant finishedAt) {
        this.id = id;
        this.sessionId = sessionId;
        this.taskId = taskId;
        this.agentId = agentId;
        this.toolName = toolName;
        this.status = status;
        this.arguments = arguments == null ? new HashMap<>() : new HashMap<>(arguments);
        this.result = result;
        this.error = error;
        this.startedAt = startedAt;
        this.finishedAt = finishedAt;
    }

    public void setId(String id) {
        this.id = id;
    }

    public void setSessionId(String sessionId) {
        this.sessionId = sessionId;
    }

    public void setTaskId(String taskId) {
        this.taskId = taskId;
    }

    public void setAgentId(String agentId) {
        this.agentId = agentId;
    }

    public void setToolName(String toolName) {
        this.toolName = toolName;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public void setArguments(Map<String, Object> arguments) {
        this.arguments = arguments == null ? new HashMap<>() : new HashMap<>(arguments);
    }

    public void setResult(String result) {
        this.result = result;
    }

    public void setError(String error) {
        this.error = error;
    }

    public void setStartedAt(Instant startedAt) {
        this.startedAt = startedAt;
    }

    public void setFinishedAt(Instant finishedAt) {
        this.finishedAt = finishedAt;
    }
}
