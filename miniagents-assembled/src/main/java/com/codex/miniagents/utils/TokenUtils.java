package com.codex.miniagents.utils;

public final class TokenUtils {
    private TokenUtils() {
    }

    public static int estimateTokens(String text) {
        if (text == null || text.isEmpty()) {
            return 1;
        }
        return Math.max(1, text.length() / 4);
    }
}
