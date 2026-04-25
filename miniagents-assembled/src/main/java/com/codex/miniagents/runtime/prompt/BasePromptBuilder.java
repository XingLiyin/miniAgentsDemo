package com.codex.miniagents.runtime.prompt;

import com.codex.miniagents.llm.model.LlmMessage;
import com.codex.miniagents.llm.model.ToolCallBlock;
import com.codex.miniagents.tools.model.ToolResult;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

abstract class BasePromptBuilder {
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    public List<LlmMessage> appendAssistantToolCalls(
        List<LlmMessage> messages,
        String fullText,
        List<ToolCallBlock> toolCalls
    ) {
        List<LlmMessage> next = new ArrayList<>(messages);
        next.add(LlmMessage.builder()
            .role("assistant")
            .content(defaultString(fullText))
            .toolCalls(toolCalls == null ? List.of() : toolCalls)
            .build());
        return next;
    }

    public List<LlmMessage> appendToolResult(
        List<LlmMessage> messages,
        String toolName,
        ToolResult toolResult,
        String toolCallId
    ) {
        List<LlmMessage> next = new ArrayList<>(messages);
        boolean isError = toolResult != null && toolResult.isError();
        String content = toolResult == null || toolResult.getContent() == null ? "" : toolResult.getContent();
        if (isError) {
            content = "[ERROR] " + content;
        }
        next.add(LlmMessage.builder()
            .role("tool")
            .content(content)
            .toolCallId(defaultString(toolCallId))
            .build());
        return next;
    }

    public List<LlmMessage> sanitizeMessages(List<LlmMessage> messages) {
        List<LlmMessage> merged = new ArrayList<>();
        for (LlmMessage message : messages) {
            if (message == null) {
                continue;
            }
            boolean hasText = message.getContent() != null && !message.getContent().isBlank();
            boolean hasToolCalls = message.getToolCalls() != null && !message.getToolCalls().isEmpty();
            boolean isToolMessage = "tool".equals(defaultString(message.getRole()));
            if (!hasText && !hasToolCalls && !isToolMessage) {
                continue;
            }
            if (!merged.isEmpty() && sameRole(merged.get(merged.size() - 1), message)
                && !"tool".equals(defaultString(message.getRole()))
                && !"assistant".equals(defaultString(message.getRole()))) {
                LlmMessage previous = merged.remove(merged.size() - 1);
                merged.add(LlmMessage.builder()
                    .role(previous.getRole())
                    .content(previous.getContent() + "\n\n" + message.getContent())
                    .toolCallId(previous.getToolCallId())
                    .toolCalls(previous.getToolCalls())
                    .build());
            } else {
                merged.add(LlmMessage.builder()
                    .role(message.getRole())
                    .content(message.getContent())
                    .toolCallId(message.getToolCallId())
                    .toolCalls(message.getToolCalls())
                    .build());
            }
        }
        return merged;
    }

    public List<ToolCallBlock> buildToolCallsFromStream(Map<Integer, Map<String, Object>> acc) {
        List<ToolCallBlock> result = new ArrayList<>();
        for (Integer idx : acc.keySet().stream().sorted().toList()) {
            Map<String, Object> buf = acc.get(idx);
            if (buf == null) {
                continue;
            }
            String argsRaw = defaultString(buf.get("arguments"));
            Map<String, Object> args;
            try {
                args = argsRaw.isBlank() ? Map.of() : OBJECT_MAPPER.readValue(argsRaw, Map.class);
            } catch (Exception e) {
                args = new LinkedHashMap<>();
                args.put("_raw", argsRaw);
            }
            ToolCallBlock block = new ToolCallBlock();
            block.setType("tool_call");
            block.setId(defaultString(buf.get("id")));
            block.setName(defaultString(buf.get("name")));
            block.setInput(args);
            result.add(block);
        }
        return result;
    }

    protected String defaultString(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    protected boolean hasText(String value) {
        return value != null && !value.isBlank();
    }

    private boolean sameRole(LlmMessage left, LlmMessage right) {
        return defaultString(left.getRole()).equals(defaultString(right.getRole()));
    }
}
