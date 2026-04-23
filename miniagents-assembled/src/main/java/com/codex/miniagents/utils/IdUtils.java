package com.codex.miniagents.utils;

import java.security.SecureRandom;
import java.time.Instant;

public final class IdUtils {
    private static final String ENCODING = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

    private static final SecureRandom RANDOM = new SecureRandom();

    private IdUtils() {
    }

    private static String encodeTime(long millis, int length) {
        char[] chars = new char[length];
        for (int i = length - 1; i >= 0; i--) {
            chars[i] = ENCODING.charAt((int) (millis & 0x1F));
            millis >>= 5;
        }
        return new String(chars);
    }

    private static String encodeRandom(int length) {
        StringBuilder sb = new StringBuilder(length);
        for (int i = 0; i < length; i++) {
            sb.append(ENCODING.charAt(RANDOM.nextInt(ENCODING.length())));
        }
        return sb.toString();
    }

    public static String newUlid() {
        long millis = Instant.now().toEpochMilli();
        return encodeTime(millis, 10) + encodeRandom(16);
    }

    public static String newSessionId() {
        return "ses_" + newUlid();
    }

    public static String newTaskId() {
        return "tsk_" + newUlid();
    }

    public static String newAgentId() {
        return "agt_" + newUlid();
    }

    public static String newTemplateId() {
        return "tpl_" + newUlid();
    }

    public static String newMemoryId() {
        return "mem_" + newUlid();
    }

    public static String newBlackboardEntryId() {
        return "bbe_" + newUlid();
    }

    public static String newToolCallId() {
        return "tlc_" + newUlid();
    }
}
