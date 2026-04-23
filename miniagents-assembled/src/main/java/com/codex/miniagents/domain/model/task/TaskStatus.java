package com.codex.miniagents.domain.model.task;

public enum TaskStatus {
    PENDING,
    ACTIVE,
    SUSPENDED,
    TO_BE_OBSERVED,
    FINISHED,
    FAILED,
    CANCELED;

    public boolean isTerminal() {
        return this == FINISHED || this == FAILED || this == CANCELED;
    }
}
