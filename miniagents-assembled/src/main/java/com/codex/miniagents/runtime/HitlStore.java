package com.codex.miniagents.runtime;

import lombok.Getter;
import lombok.RequiredArgsConstructor;

import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Session 级 HITL 等待仓库。
 *
 * 与 Python 一致：不创建 user_input task，而是通过阻塞等待用户回答。
 */
public class HitlStore {
    @Getter
    @RequiredArgsConstructor
    public static class HitlEntry {
        private final CountDownLatch latch;
        private final String agentId;
        private final String prompt;
        private final String inputType;
        private final Instant createdAt;
        private volatile String answer = "";
    }

    private static final HitlStore INSTANCE = new HitlStore();

    private final Map<String, HitlEntry> store = new ConcurrentHashMap<>();

    public static HitlStore getInstance() {
        return INSTANCE;
    }

    public String waitForAnswer(String sessionId, String agentId, String prompt, String inputType) {
        return waitForAnswer(sessionId, agentId, prompt, inputType, 3600L);
    }

    public String waitForAnswer(String sessionId, String agentId, String prompt, String inputType, long timeoutSec) {
        HitlEntry entry = new HitlEntry(new CountDownLatch(1), agentId, prompt, inputType, Instant.now());
        store.put(sessionId, entry);
        try {
            entry.getLatch().await(timeoutSec, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } finally {
            store.remove(sessionId);
        }
        return entry.answer;
    }

    public HitlEntry submit(String sessionId, String answer) {
        HitlEntry entry = store.get(sessionId);
        if (entry != null) {
            entry.answer = answer == null ? "" : answer;
            entry.getLatch().countDown();
        }
        return entry;
    }

    public HitlEntry getPending(String sessionId) {
        return store.get(sessionId);
    }

    public void clear(String sessionId) {
        HitlEntry entry = store.remove(sessionId);
        if (entry != null) {
            entry.getLatch().countDown();
        }
    }
}
