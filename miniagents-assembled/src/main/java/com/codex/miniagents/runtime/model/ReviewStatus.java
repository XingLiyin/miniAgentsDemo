package com.codex.miniagents.runtime.model;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

public enum ReviewStatus {
    CONFIRMED("confirmed"),
    REOPEN("reopen"),
    SKIP("skip");

    private final String code;

    ReviewStatus(String code) {
        this.code = code;
    }

    @JsonValue
    public String getCode() {
        return code;
    }

    @JsonCreator
    public static ReviewStatus fromValue(String value) {
        if (value == null) {
            throw new IllegalArgumentException("ReviewStatus cannot be null");
        }
        for (ReviewStatus status : values()) {
            if (status.code.equalsIgnoreCase(value) || status.name().equalsIgnoreCase(value)) {
                return status;
            }
        }
        throw new IllegalArgumentException("Unknown ReviewStatus: " + value);
    }
}
