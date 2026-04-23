package com.codex.miniagents.domain.model.agent;

public enum AgentStatus {
    IDLE,
    RUNNING,
    WAITING,
    FINISHED,
    FAILED;

    public boolean isTerminal() {
        return this == FINISHED || this == FAILED;
    }
}
