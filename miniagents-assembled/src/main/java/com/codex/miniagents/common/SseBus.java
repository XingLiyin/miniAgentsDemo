package com.codex.miniagents.common;

import com.codex.miniagents.infrastructure.storage.file.EventStore;

import lombok.extern.slf4j.Slf4j;

import java.util.List;
import java.util.Map;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Session 级 SSE 事件总线。
 */
@Slf4j
public class SseBus {
    private static final SseBus INSTANCE = new SseBus();

    private final Map<String, List<BlockingQueue<Map<String, Object>>>> queues = new ConcurrentHashMap<>();

    public static SseBus getInstance() {
        return INSTANCE;
    }

    public BlockingQueue<Map<String, Object>> createSubscription(String sessionId) {
        BlockingQueue<Map<String, Object>> queue = new LinkedBlockingQueue<>(500);
        queues.computeIfAbsent(sessionId, k -> new CopyOnWriteArrayList<>()).add(queue);
        log.debug("SseBus.createSubscription: session={} subscribers={}", sessionId, queues.get(sessionId).size());
        return queue;
    }

    public void removeSubscription(String sessionId, BlockingQueue<Map<String, Object>> queue) {
        List<BlockingQueue<Map<String, Object>>> list = queues.get(sessionId);
        if (list != null) {
            list.remove(queue);
            log.debug("SseBus.removeSubscription: session={} remainingSubscribers={}", sessionId, list.size());
        }
    }

    public void push(String sessionId, Map<String, Object> event) {
        try {
            EventStore.getInstance().append(sessionId, event);
        } catch (Exception e) {
            log.debug("SSE event persist failed for session {}", sessionId, e);
        }

        List<BlockingQueue<Map<String, Object>>> list = queues.get(sessionId);
        if (list == null || list.isEmpty()) {
            log.debug("SseBus.push: session={} no subscribers, event persisted only", sessionId);
            return;
        }

        for (BlockingQueue<Map<String, Object>> queue : list) {
            try {
                queue.offer(event);
            } catch (Exception e) {
                log.debug("SSE push failed for session {}", sessionId, e);
            }
        }
        log.debug("SseBus.push: session={} delivered event type={}", sessionId, event.get("type"));
    }
}
