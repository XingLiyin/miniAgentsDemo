package com.codex.miniagents.domain.memory.service;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.domain.memory.model.MemoryItem;
import com.codex.miniagents.domain.memory.model.MemorySummary;
import com.codex.miniagents.domain.memory.model.PromptContext;
import com.codex.miniagents.infrastructure.storage.repository.MemoryRepository;
import com.codex.miniagents.utils.IdUtils;
import com.codex.miniagents.utils.TimeUtils;
import com.codex.miniagents.utils.TokenUtils;

import lombok.RequiredArgsConstructor;

import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.List;

@Service
@RequiredArgsConstructor
public class MemoryService {
    private static final double PROMPT_TRUNCATION_RATIO = 0.6D;

    private static final int FALLBACK_RECENT_MESSAGE_COUNT = 5;

    private final MemoryRepository store;

    private final MiniAgentsProperties properties;

    public MemoryItem appendMessage(String sessionId, String agentId, String role, String content, String taskId) {
        return appendMessage(sessionId, agentId, role, content, taskId, null, List.of());
    }

    public MemoryItem appendMessage(
        String sessionId,
        String agentId,
        String role,
        String content,
        String taskId,
        String toolCallId,
        List<Map<String, Object>> toolCalls
    ) {
        MemoryItem item = MemoryItem.builder()
            .id(IdUtils.newMemoryId())
            .sessionId(sessionId)
            .agentId(agentId)
            .role(role)
            .content(content)
            .taskId(taskId)
            .createdAt(TimeUtils.nowIso())
            .toolCallId(toolCallId)
            .toolCalls(toolCalls == null ? List.of() : toolCalls)
            .build();

        store.appendMessage(agentId, item);
        return item;
    }

    public List<MemoryItem> getWindow(String agentId, Integer n) {
        int size = n != null ? n : properties.getDefaultShortWindowSize();
        return store.readWindow(agentId, size);
    }

    public List<MemoryItem> getAllMessages(String agentId) {
        return store.readMessages(agentId);
    }

    public MemorySummary getSummary(String agentId) {
        return store.getSummary(agentId).orElse(null);
    }

    public void saveSummary(String agentId, MemorySummary summary) {
        store.saveSummary(agentId, summary);
    }

    public boolean shouldSummarize(String agentId, Integer threshold) {
        int actualThreshold = threshold != null ? threshold : properties.getDefaultSummaryThreshold();
        int count = store.countMessages(agentId);
        MemorySummary summary = getSummary(agentId);
        int covered = summary != null ? summary.getCoveredUpTo() : 0;
        return (count - covered) >= actualThreshold;
    }

    public PromptContext buildPromptContext(String agentId, String systemPrompt, String goal,
        String taskDescription, List<String> blackboardSnippets, int tokenBudget) {
        List<MemoryItem> messages = getWindow(agentId, properties.getDefaultShortWindowSize());
        MemorySummary summary = getSummary(agentId);
        String summaryText = summary != null ? defaultString(summary.getSummaryText()) : "";

        PromptContext ctx = PromptContext.builder()
            .systemPrompt(systemPrompt)
            .goal(goal)
            .taskDescription(taskDescription)
            .blackboardSnippets(blackboardSnippets != null ? blackboardSnippets : List.of())
            .recentMessages(toMessageMaps(messages))
            .summaryText(summaryText)
            .build();

        StringBuilder totalText = new StringBuilder();
        totalText.append(defaultString(systemPrompt));
        totalText.append(defaultString(goal));
        totalText.append(defaultString(taskDescription));
        totalText.append(defaultString(summaryText));

        for (MemoryItem message : messages) {
            totalText.append(' ').append(defaultString(message.getContent()));
        }
        if (blackboardSnippets != null) {
            for (String snippet : blackboardSnippets) {
                totalText.append(' ').append(defaultString(snippet));
            }
        }

        ctx.setTokenEstimate(TokenUtils.estimateTokens(totalText.toString()));

        int limit = (int) (tokenBudget * PROMPT_TRUNCATION_RATIO);
        if (ctx.getTokenEstimate() > limit) {
            ctx.setBlackboardSnippets(List.of());

            if (messages.size() > FALLBACK_RECENT_MESSAGE_COUNT) {
                ctx.setRecentMessages(
                    toMessageMaps(messages.subList(messages.size() - FALLBACK_RECENT_MESSAGE_COUNT, messages.size())));
            } else {
                ctx.setRecentMessages(toMessageMaps(messages));
            }
        }

        return ctx;
    }

    public PromptContext buildPromptContext(String agentId, String systemPrompt, String goal,
        String taskDescription, List<String> blackboardSnippets) {
        return buildPromptContext(agentId, systemPrompt, goal, taskDescription, blackboardSnippets, 200_000);
    }

    public void deleteAgent(String agentId) {
        store.deleteAgent(agentId);
    }

    private String defaultString(String value) {
        return value == null ? "" : value;
    }

    private List<Map<String, Object>> toMessageMaps(List<MemoryItem> messages) {
        List<Map<String, Object>> result = new java.util.ArrayList<>();
        for (MemoryItem message : messages) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("id", message.getId());
            item.put("session_id", message.getSessionId());
            item.put("agent_id", message.getAgentId());
            item.put("role", message.getRole());
            item.put("content", message.getContent());
            item.put("task_id", message.getTaskId());
            item.put("created_at", message.getCreatedAt());
            if (message.getToolCallId() != null && !message.getToolCallId().isBlank()) {
                item.put("tool_call_id", message.getToolCallId());
            }
            if (message.getToolCalls() != null && !message.getToolCalls().isEmpty()) {
                item.put("tool_calls", message.getToolCalls());
            }
            result.add(item);
        }
        return result;
    }
}
