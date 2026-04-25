package com.codex.miniagents.runtime.prompt;

import com.codex.miniagents.llm.model.LlmMessage;
import com.codex.miniagents.llm.model.ToolCallBlock;
import com.codex.miniagents.runtime.model.ContextResource;
import com.codex.miniagents.runtime.model.ReasoningContext;
import com.codex.miniagents.domain.model.task.Task;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

public class ActorPromptBuilder extends BasePromptBuilder {
    public String buildSystemPrompt(ReasoningContext context) {
        List<String> parts = new ArrayList<>();
        if (hasText(context.getSoul())) {
            parts.add(context.getSoul());
        }
        String resources = buildResourcesSection(context);
        if (hasText(resources)) {
            parts.add(resources);
        }
        if (hasText(context.getSkillInstructions())) {
            parts.add(context.getSkillInstructions());
        }
        return String.join("\n\n---\n\n", parts);
    }

    public List<LlmMessage> buildMessages(Task task, ReasoningContext context) {
        List<LlmMessage> messages = new ArrayList<>();
        boolean isResume = isResumeTask(task, context);
        if (context.getRecentMessages() != null) {
            List<Map<String, Object>> recentMessages = context.getRecentMessages();
            List<Map<String, Object>> toAppend = recentMessages;
            if (!isResume && !recentMessages.isEmpty()) {
                Map<String, Object> last = recentMessages.get(recentMessages.size() - 1);
                if ("user".equals(String.valueOf(last.get("role")))) {
                    toAppend = recentMessages.subList(0, recentMessages.size() - 1);
                }
            }
            for (Map<String, Object> item : toAppend) {
                String toolCallId = item.get("tool_call_id") == null ? null : String.valueOf(item.get("tool_call_id"));
                List<ToolCallBlock> toolCalls = List.of();
                Object toolCallsObj = item.get("tool_calls");
                if (toolCallsObj instanceof List<?> rawToolCalls && !rawToolCalls.isEmpty()) {
                    List<ToolCallBlock> parsed = new ArrayList<>();
                    for (Object raw : rawToolCalls) {
                        if (!(raw instanceof Map<?, ?> map)) {
                            continue;
                        }
                        ToolCallBlock block = new ToolCallBlock();
                        block.setType("tool_call");
                        block.setId(map.get("id") == null ? "" : String.valueOf(map.get("id")));
                        block.setName(map.get("name") == null ? "" : String.valueOf(map.get("name")));
                        Object input = map.get("input");
                        if (input instanceof Map<?, ?> inputMap) {
                            LinkedHashMap<String, Object> normalized = new LinkedHashMap<>();
                            for (Map.Entry<?, ?> entry : inputMap.entrySet()) {
                                normalized.put(String.valueOf(entry.getKey()), entry.getValue());
                            }
                            block.setInput(normalized);
                        }
                        parsed.add(block);
                    }
                    toolCalls = parsed;
                }
                messages.add(LlmMessage.builder()
                    .role(item.get("role") == null ? "user" : String.valueOf(item.get("role")))
                    .content(item.get("content") == null ? "" : String.valueOf(item.get("content")))
                    .toolCallId(toolCallId)
                    .toolCalls(toolCalls)
                    .build());
            }
        }

        if (isResume) {
            List<String> resumeParts = new ArrayList<>();
            if (context.getBlackboardSnippets() != null && !context.getBlackboardSnippets().isEmpty()) {
                resumeParts.add("Sub-task results:\n" + context.getBlackboardSnippets().stream()
                    .map(s -> "- " + s)
                    .collect(Collectors.joining("\n")));
            }
            resumeParts.add("Sub-tasks completed. Please review the results and continue.");
            messages.add(LlmMessage.builder().role("user").content(String.join("\n\n", resumeParts)).build());
            return sanitizeMessages(messages);
        }

        List<String> parts = new ArrayList<>();
        if (context.getBlackboardSnippets() != null && !context.getBlackboardSnippets().isEmpty()) {
            parts.add("Task Background:\n" + context.getBlackboardSnippets().stream()
                .map(s -> "- " + s)
                .collect(Collectors.joining("\n")));
        }
        if (task != null && hasText(task.getTitle()) && hasText(task.getDescription())) {
            parts.add("Current goal: " + task.getTitle() + "\nDescription: " + task.getDescription());
        }
        if (hasText(context.getSummaryText())) {
            parts.add("Previous progress:\n" + context.getSummaryText());
        }
        parts.add("Current message: " + (task != null && task.getUserPrompt() != null ? task.getUserPrompt() : ""));

        messages.add(LlmMessage.builder().role("user").content(String.join("\n\n", parts)).build());
        return sanitizeMessages(messages);
    }

    private boolean isResumeTask(Task task, ReasoningContext context) {
        if (task == null || task.getId() == null || context.getRecentMessages() == null) {
            return false;
        }
        return context.getRecentMessages().stream()
            .anyMatch(item -> task.getId().equals(String.valueOf(item.get("task_id"))));
    }

    private String buildResourcesSection(ReasoningContext context) {
        List<ContextResource> skills = context.getActorResources() == null
            ? List.of()
            : context.getActorResources().stream().filter(r -> "skill".equals(r.getKind())).toList();
        List<ContextResource> tools = context.getActorResources() == null
            ? List.of()
            : context.getActorResources().stream()
                .filter(r -> "tool".equals(r.getKind()) && r.getLlmTool() != null)
                .toList();
        List<String> parts = new ArrayList<>();
        if (!skills.isEmpty()) {
            List<String> lines = new ArrayList<>();
            lines.add("## Available Skills (assign to tasks where appropriate)");
            for (ContextResource resource : skills) {
                lines.add("- " + defaultString(resource.getName()) + ": " + defaultString(resource.getDescription()));
            }
            parts.add(String.join("\n", lines));
        }
        if (!tools.isEmpty()) {
            List<String> lines = new ArrayList<>();
            lines.add("## Available Tools (use them via tool calls)");
            for (ContextResource resource : tools) {
                lines.add("- " + resource.getLlmTool().toPromptText());
            }
            parts.add(String.join("\n", lines));
        }
        return String.join("\n\n", parts);
    }
}
