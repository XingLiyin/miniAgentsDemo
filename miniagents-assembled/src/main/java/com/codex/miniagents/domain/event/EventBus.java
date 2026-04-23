package com.codex.miniagents.domain.event;

import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.BiConsumer;

@Component
public class EventBus {
    private final Map<String, List<BiConsumer<String, Map<String, Object>>>> handlers = new ConcurrentHashMap<>();

    public void subscribe(String eventType, BiConsumer<String, Map<String, Object>> handler) {
        handlers.computeIfAbsent(eventType, k -> new CopyOnWriteArrayList<>()).add(handler);
    }

    public void subscribeAll(BiConsumer<String, Map<String, Object>> handler) {
        handlers.computeIfAbsent("*", k -> new CopyOnWriteArrayList<>()).add(handler);
    }

    public void publish(String eventType, Map<String, Object> payload) {
        List<BiConsumer<String, Map<String, Object>>> direct = handlers.getOrDefault(eventType, List.of());
        List<BiConsumer<String, Map<String, Object>>> wildcard = handlers.getOrDefault("*", List.of());
        for (BiConsumer<String, Map<String, Object>> handler : direct) {
            safeInvoke(handler, eventType, payload);
        }
        for (BiConsumer<String, Map<String, Object>> handler : wildcard) {
            safeInvoke(handler, eventType, payload);
        }
    }

    private void safeInvoke(BiConsumer<String, Map<String, Object>> handler, String eventType,
        Map<String, Object> payload) {
        try {
            handler.accept(eventType, payload);
        } catch (Exception ignored) {
            // Phase 1: swallow handler exceptions to avoid breaking publisher flow.
        }
    }
}
