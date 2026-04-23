package com.codex.miniagents.runtime;

import com.codex.miniagents.common.SseBus;
import com.codex.miniagents.domain.model.agent.Agent;
import com.codex.miniagents.domain.model.task.Task;
import com.codex.miniagents.domain.model.task.TaskStatus;
import com.codex.miniagents.domain.service.TaskService;
import com.codex.miniagents.llm.ChatClient;
import com.codex.miniagents.llm.model.LlmMessage;
import com.codex.miniagents.llm.model.StreamChunk;
import com.codex.miniagents.llm.model.ToolCallBlock;
import com.codex.miniagents.llm.registry.LlmClientProvider;
import com.codex.miniagents.runtime.model.ActorResult;
import com.codex.miniagents.runtime.model.ContextResource;
import com.codex.miniagents.runtime.model.ConversationTurn;
import com.codex.miniagents.runtime.model.ReasoningContext;
import com.codex.miniagents.runtime.model.ToolCallRecord;
import com.codex.miniagents.runtime.prompt.ActorPromptBuilder;
import com.codex.miniagents.runtime.prompt.PromptBuilderFactory;
import com.codex.miniagents.tools.model.ToolResult;

import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.time.Instant;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Component
public class Actor {
    private final LlmClientProvider llmClientProvider;

    private final ToolGateway toolGateway;

    private final TaskService taskService;

    public Actor(LlmClientProvider llmClientProvider, ToolGateway toolGateway,
        TaskService taskService) {
        this.llmClientProvider = llmClientProvider;
        this.toolGateway = toolGateway;
        this.taskService = taskService;
    }

    public ActorResult act(Task task, ReasoningContext context, Agent agent) {
        if (task.getStatus() == TaskStatus.PENDING) {
            taskService.transition(task.getId(), TaskStatus.ACTIVE);
        }

        ActorPromptBuilder promptBuilder = PromptBuilderFactory.forActor();
        ChatClient llmClient = llmClientProvider.get(agent.getLlmName(), agent.getLlmModel());
        String systemPrompt = promptBuilder.buildSystemPrompt(context);
        List<LlmMessage> messages = promptBuilder.buildMessages(task, context);
        List<com.codex.miniagents.llm.model.LlmTool> tools = context.getActorResources() == null
            ? List.of()
            : context.getActorResources().stream()
                .filter(r -> "tool".equals(r.getKind()) && r.getLlmTool() != null)
                .map(ContextResource::getLlmTool)
                .collect(Collectors.toList());

        List<ToolCallRecord> toolCalls = new ArrayList<>();
        List<ConversationTurn> conversationTurns = new ArrayList<>();
        String lastText = "";
        String sessionId = task.getSessionId();

        int maxRounds = 1;
        if (agent.getLoopGuard() != null) {
            maxRounds = Math.max(1, agent.getLoopGuard().getActorMaxToolRounds());
        }

        for (int round = 0; round < maxRounds; round++) {
            List<LlmMessage> messagesSent = cloneMessages(messages);

            try {
                SseBus.getInstance().push(sessionId, Map.of(
                    "type", "llm_prompt",
                    "source", "actor",
                    "round_label", "actor_round_" + round,
                    "system_prompt", systemPrompt,
                    "messages", messages.stream().map(m -> Map.of(
                        "role", defaultString(m.getRole()),
                        "content", defaultString(m.getContent())
                    )).toList(),
                    "tool_names", context.getActorResources() == null ? List.of() : context.getActorResources().stream()
                        .filter(r -> "tool".equals(r.getKind()) && r.getLlmTool() != null)
                        .map(r -> r.getLlmTool().getName())
                        .toList()
                ));
            } catch (Exception ignored) {
            }

            String fullText = "";
            Map<Integer, Map<String, Object>> toolCallAcc = new java.util.LinkedHashMap<>();
            for (Iterator<StreamChunk> stream = llmClient.streamMessage(messages, systemPrompt, tools); stream.hasNext(); ) {
                StreamChunk chunk = stream.next();
                if (chunk.getTextDelta() != null && !chunk.getTextDelta().isEmpty()) {
                    fullText += chunk.getTextDelta();
                    try {
                        SseBus.getInstance().push(sessionId, Map.of(
                            "type", "text_delta",
                            "delta", chunk.getTextDelta(),
                            "round", round
                        ));
                    } catch (Exception ignored) {
                    }
                }
                if (chunk.getToolCallDelta() != null && !chunk.getToolCallDelta().isEmpty()) {
                    Object idxObj = chunk.getToolCallDelta().get("index");
                    int idx = idxObj instanceof Number n ? n.intValue() : 0;
                    toolCallAcc.put(idx, new java.util.LinkedHashMap<>(chunk.getToolCallDelta()));
                }
            }

            try {
                SseBus.getInstance().push(sessionId, Map.of(
                    "type", "text_done",
                    "text", fullText,
                    "round", round
                ));
            } catch (Exception ignored) {
            }

            List<ToolCallBlock> parsedToolCalls = promptBuilder.buildToolCallsFromStream(toolCallAcc);
            if (parsedToolCalls.isEmpty()) {
                lastText = fullText;
                conversationTurns.add(ConversationTurn.builder()
                    .round(round)
                    .messagesSent(messagesSent)
                    .llmText(defaultString(lastText))
                    .toolCalls(List.of())
                    .build());
                break;
            }

            messages = promptBuilder.appendAssistantToolCalls(messages, defaultString(fullText), parsedToolCalls);

            List<ToolCallRecord> roundToolCalls = new ArrayList<>();
            boolean done = false;
            for (ToolCallBlock toolCall : parsedToolCalls) {
                Map<String, Object> args = toolCall.getInput() == null ? Map.of() : toolCall.getInput();
                ToolResult result;
                try {
                    result = toolGateway.call(task.getSessionId(), task.getId(), agent, task, toolCall.getName(), args);
                } catch (Exception e) {
                    result = ToolResult.builder().content(String.valueOf(e.getMessage())).isError(true)
                        .errorCode("TOOL_EXEC_ERROR").build();
                }
                if (isTaskCompleteSignal(result) || task.isActorDone()) {
                    done = true;
                }
                toolCalls.add(ToolCallRecord.builder()
                    .toolCallId(toolCall.getId())
                    .toolName(toolCall.getName())
                    .arguments(args)
                    .result(result.getContent() == null ? "" : result.getContent())
                    .isError(result.isError())
                    .build());
                roundToolCalls.add(ToolCallRecord.builder()
                    .toolCallId(toolCall.getId())
                    .toolName(toolCall.getName())
                    .arguments(args)
                    .result(result.getContent() == null ? "" : result.getContent())
                    .isError(result.isError())
                    .build());
                messages = promptBuilder.appendToolResult(messages, toolCall.getName(), result, toolCall.getId());

                try {
                    SseBus.getInstance().push(sessionId, Map.of(
                        "type", "tool_call",
                        "tool_name", toolCall.getName(),
                        "arguments", args,
                        "result", result.getContent() == null ? "" : result.getContent(),
                        "is_error", result.isError(),
                        "created_at", Instant.now().toString()
                    ));
                } catch (Exception ignored) {
                }
            }
            lastText = fullText;
            conversationTurns.add(ConversationTurn.builder()
                .round(round)
                .messagesSent(messagesSent)
                .llmText(defaultString(lastText))
                .toolCalls(roundToolCalls)
                .build());
            if (done) {
                break;
            }
        }

        Map<String, Object> settings = task.getSettings() == null ? Map.of() : task.getSettings();
        String skillUsed = settings.get("skill_name") == null ? null : String.valueOf(settings.get("skill_name"));
        boolean hasSkill = skillUsed != null && !skillUsed.isBlank();
        String actorMode = hasSkill
            ? "skill"
            : (toolCalls.isEmpty() ? "text" : "tool_use");
        return ActorResult.builder()
            .taskId(task.getId())
            .success(true)
            .output(lastText == null ? "" : lastText)
            .toolCallsMade(toolCalls)
            .conversationTurns(conversationTurns)
            .actorMode(actorMode)
            .skillUsed(hasSkill ? skillUsed : null)
            .build();
    }

    private boolean isTaskCompleteSignal(ToolResult result) {
        if (result == null || result.getMetadata() == null) {
            return false;
        }
        Object signal = result.getMetadata().get("control_signal");
        return ControlSignal.TASK_COMPLETE.name().equals(String.valueOf(signal));
    }

    private List<LlmMessage> cloneMessages(List<LlmMessage> source) {
        List<LlmMessage> copy = new ArrayList<>();
        for (LlmMessage m : source) {
            copy.add(LlmMessage.builder()
                .role(m.getRole())
                .content(m.getContent())
                .toolCallId(m.getToolCallId())
                .toolCalls(m.getToolCalls())
                .build());
        }
        return copy;
    }

    private String defaultString(String value) {
        return value == null ? "" : value;
    }
}
