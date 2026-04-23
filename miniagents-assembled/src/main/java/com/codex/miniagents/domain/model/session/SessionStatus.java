package com.codex.miniagents.domain.model.session;

public enum SessionStatus {
    QUEUED,
    RUNNING,
    WAITING_INPUT,
    SUCCEEDED,
    FAILED,
    CANCELED;

    public boolean isTerminal() {
        return this == SUCCEEDED || this == FAILED || this == CANCELED;
    }
}
