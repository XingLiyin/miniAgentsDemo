package com.codex.miniagents.runtime.prompt;

import com.codex.miniagents.domain.model.session.Session;
import com.codex.miniagents.domain.model.task.Task;
import com.codex.miniagents.domain.model.task.TaskStatus;
import com.codex.miniagents.llm.model.LlmMessage;
import com.codex.miniagents.runtime.model.ActorResult;
import com.codex.miniagents.runtime.model.ContextResource;
import com.codex.miniagents.runtime.model.ReasoningContext;

import java.util.ArrayList;
import java.util.List;

public class ObserverPromptBuilder extends BasePromptBuilder {
    private static final String ROLE_FALLBACK = "You are an objective observer evaluating task execution results.";

    public String buildSystemPrompt(ReasoningContext context, String assessmentGuide) {
        String role = hasText(context.getRole()) ? context.getRole() : ROLE_FALLBACK;
        List<ContextResource> tools = context.getObserverResources() == null ? List.of()
            : context.getObserverResources().stream()
                .filter(r -> "tool".equals(r.getKind()) && r.getLlmTool() != null)
                .toList();
        List<String> parts = new ArrayList<>();
        parts.add(role);
        parts.add(assessmentGuide);
        if (hasText(context.getSkillInstructions())) {
            parts.add("## Skill Instructions for This Task\n\n"
                + "The task was executed under the following skill. Use these instructions to calibrate your evaluation criteria and emphasis. "
                + "You may also call skill-related tools listed below to gather more information before submitting your assessment.\n\n"
                + context.getSkillInstructions());
        }
        if (!tools.isEmpty()) {
            List<String> lines = new ArrayList<>();
            lines.add("## Available Tools");
            for (ContextResource resource : tools) {
                lines.add("- " + resource.getLlmTool().toPromptText());
            }
            parts.add(String.join("\n", lines));
        }
        return String.join("\n\n---\n\n", parts);
    }

    public List<LlmMessage> buildMessages(Session session, ActorResult result, ReasoningContext context,
        Task task, List<Task> taskList) {
        String transcript = buildTranscript(result);
        List<Task> siblings = taskList == null ? List.of() : taskList.stream()
            .filter(t -> t != null && task != null && !task.getId().equals(t.getId()))
            .toList();
        boolean hasPending = siblings.stream().anyMatch(t -> t.getStatus() == TaskStatus.PENDING);
        List<Task> reviewable = hasPending
            ? siblings.stream().filter(t -> t.getStatus() == TaskStatus.FINISHED || t.getStatus() == TaskStatus.PENDING).toList()
            : List.of();

        List<String> contentParts = new ArrayList<>();
        contentParts.add("Previous progress summary: " + (hasText(context.getSummaryText()) ? context.getSummaryText() : "None"));
        contentParts.add("Current task: " + defaultString(task == null ? "" : task.getTitle())
            + "\nTask description: " + defaultString(task != null && hasText(task.getDescription()) ? task.getDescription() : task == null ? "" : task.getTitle()));
        contentParts.add("User requirements: " + defaultString(session == null ? "" : session.getUserPrompt()));
        contentParts.add("Execution transcript (" + (result == null || result.getConversationTurns() == null ? 0 : result.getConversationTurns().size())
            + " round(s)):\n" + transcript);
        if (!reviewable.isEmpty()) {
            contentParts.add("Session task list:\n" + buildTaskListSection(reviewable));
        }
        return List.of(LlmMessage.builder().role("user").content(String.join("\n\n", contentParts)).build());
    }

    private String buildTranscript(ActorResult result) {
        if (result == null) {
            return "[No conversation recorded]";
        }
        if (result.getConversationTurns() == null || result.getConversationTurns().isEmpty()) {
            return hasText(result.getOutput()) ? "[No tool calls] Agent response: " + result.getOutput() : "[No conversation recorded]";
        }
        StringBuilder sb = new StringBuilder();
        result.getConversationTurns().forEach(turn -> {
            sb.append("--- Round ").append(turn.getRound() + 1).append(" ---\n");
            if (turn.getToolCalls() != null) {
                turn.getToolCalls().forEach(call -> {
                    String status = call.isError() ? "ERROR" : "OK";
                    sb.append("  Tool call: ").append(call.getToolName()).append("(").append(call.getArguments()).append(")\n");
                    String callResult = call.getResult() == null ? "" : call.getResult();
                    if (callResult.length() > 500) {
                        callResult = callResult.substring(0, 500);
                    }
                    sb.append("  Result [").append(status).append("]: ").append(callResult).append("\n");
                });
            }
            if (hasText(turn.getLlmText())) {
                sb.append("  Agent reply: ").append(turn.getLlmText()).append("\n");
            }
        });
        return sb.toString();
    }

    private String buildTaskListSection(List<Task> tasks) {
        StringBuilder sb = new StringBuilder();
        for (Task task : tasks) {
            String resultHint = task.getStatus() == TaskStatus.FINISHED && hasText(task.getResult())
                ? " | result: " + truncate(task.getResult(), 120)
                : "";
            sb.append("  [").append(task.getStatus() == null ? "" : task.getStatus().name()).append("] ")
                .append(defaultString(task.getTitle())).append(resultHint).append("\n");
        }
        return sb.toString();
    }

    private String truncate(String text, int limit) {
        if (text == null || text.length() <= limit) {
            return text == null ? "" : text;
        }
        return text.substring(0, limit);
    }
}
