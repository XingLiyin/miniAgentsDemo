package com.codex.miniagents.exception;

import lombok.Getter;

public class AppException extends RuntimeException {
    @Getter
    private final ErrorCode code;

    private final String message;

    public AppException(ErrorCode code, String message) {
        super(message);
        this.code = code;
        this.message = message;
    }

    @Override
    public String getMessage() {
        return message;
    }
}
