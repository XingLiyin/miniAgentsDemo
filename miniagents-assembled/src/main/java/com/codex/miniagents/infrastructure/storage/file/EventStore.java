package com.codex.miniagents.infrastructure.storage.file;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;

import lombok.extern.slf4j.Slf4j;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 按 session 持久化 SSE 历史事件，供重连回放使用。
 */
@Slf4j
public class EventStore {
    private static final Path DATA_DIR = Path.of("resources", "event_logs");
    
    private static final EventStore INSTANCE = new EventStore();

    private static final java.util.Set<String> PERSIST_TYPES = java.util.Set.of(
        "message",
        "tool_call",
        "task_created",
        "task_updated",
        "text_done",
        "llm_prompt",
        "observer_text_done"
    );

    private final Object lock = new Object();
    private final ObjectMapper objectMapper = new ObjectMapper()
        .registerModule(new JavaTimeModule())
        .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);

    private EventStore() {
        try {
            Files.createDirectories(DATA_DIR);
            log.info("EventStore: using event log directory {}", DATA_DIR.toAbsolutePath());
        } catch (Exception e) {
            log.warn("EventStore: failed to create data dir {}", DATA_DIR, e);
        }
    }

    public static EventStore getInstance() {
        return INSTANCE;
    }

    private Path path(String sessionId) {
        return DATA_DIR.resolve(sessionId + ".jsonl");
    }

    public void append(String sessionId, Map<String, Object> event) {
        if (event == null || !PERSIST_TYPES.contains(String.valueOf(event.get("type")))) {
            return;
        }

        Map<String, Object> normalized = event;
        if (!normalized.containsKey("created_at")) {
            normalized = new java.util.LinkedHashMap<>(normalized);
            normalized.put("created_at", Instant.now().toString());
        }

        synchronized (lock) {
            try {
                Files.writeString(
                    path(sessionId),
                    objectMapper.writeValueAsString(normalized) + "\n",
                    Files.exists(path(sessionId))
                        ? java.nio.file.StandardOpenOption.APPEND
                        : java.nio.file.StandardOpenOption.CREATE
                );
            } catch (Exception e) {
                log.warn("EventStore.append failed for session {}", sessionId, e);
            }
        }
    }

    public List<Map<String, Object>> load(String sessionId) {
        Path path = path(sessionId);
        if (!Files.exists(path)) {
            log.debug("EventStore.load: no event log found for session {}", sessionId);
            return List.of();
        }
        List<Map<String, Object>> events = new ArrayList<>();
        synchronized (lock) {
            try {
                for (String line : Files.readAllLines(path, StandardCharsets.UTF_8)) {
                    if (line == null || line.isBlank()) {
                        continue;
                    }
                    try {
                        events.add(objectMapper.readValue(line, new TypeReference<>() {}));
                    } catch (Exception ignored) {
                    }
                }
            } catch (Exception e) {
                log.warn("EventStore.load failed for session {}", sessionId, e);
            }
        }
        log.debug("EventStore.load: loaded {} event(s) for session {}", events.size(), sessionId);
        return events;
    }

    public void delete(String sessionId) {
        synchronized (lock) {
            try {
                Files.deleteIfExists(path(sessionId));
                log.debug("EventStore.delete: removed event log for session {}", sessionId);
            } catch (Exception e) {
                log.warn("EventStore.delete failed for session {}", sessionId, e);
            }
        }
    }
}
