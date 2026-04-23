package com.codex.miniagents.runtime.prompt;

import com.codex.miniagents.llm.model.LlmMessage;
import com.codex.miniagents.runtime.model.ContextResource;
import com.codex.miniagents.runtime.model.ReasoningContext;
import com.codex.miniagents.domain.model.task.Task;

import java.util.ArrayList;
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
        if (context.getRecentMessages() != null) {
            List<Map<String, Object>> recentMessages = context.getRecentMessages();
            List<Map<String, Object>> toAppend = recentMessages;
            if (!recentMessages.isEmpty()) {
                Map<String, Object> last = recentMessages.get(recentMessages.size() - 1);
                if ("user".equals(String.valueOf(last.get("role")))) {
                    toAppend = recentMessages.subList(0, recentMessages.size() - 1);
                }
            }
            for (Map<String, Object> item : toAppend) {
                messages.add(LlmMessage.builder()
                    .role(item.get("role") == null ? "user" : String.valueOf(item.get("role")))
                    .content(item.get("content") == null ? "" : String.valueOf(item.get("content")))
                    .build());
            }
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
