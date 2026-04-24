package com.codex.miniagents.runtime;

import com.codex.miniagents.common.SseBus;
import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.domain.model.agent.Agent;
import com.codex.miniagents.domain.model.task.Task;
import com.codex.miniagents.domain.model.task.TaskStatus;
import com.codex.miniagents.domain.service.TaskService;
import com.codex.miniagents.llm.ChatClient;
import com.codex.miniagents.llm.model.LlmMessage;
import com.codex.miniagents.llm.model.LlmTool;
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
import com.codex.miniagents.tools.model.CallContext;
import com.codex.miniagents.tools.model.ToolResult;

import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.time.Instant;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Component
public class Actor {
    private final LlmClientProvider llmClientProvider;

    private final ToolGateway toolGateway;

    private final TaskService taskService;

    private final MiniAgentsProperties properties;

    public Actor(LlmClientProvider llmClientProvider, ToolGateway toolGateway,
        TaskService taskService, MiniAgentsProperties properties) {
        this.llmClientProvider = llmClientProvider;
        this.toolGateway = toolGateway;
        this.taskService = taskService;
        this.properties = properties;
    }

    public ActorResult act(Task task, ReasoningContext context, Agent agent) {
        if (task.getStatus() == TaskStatus.PENDING) {
            taskService.transition(task.getId(), TaskStatus.ACTIVE);
        }

        ActorPromptBuilder promptBuilder = PromptBuilderFactory.forActor();
        ChatClient llmClient = resolveLlmClient(agent);
        String systemPrompt = promptBuilder.buildSystemPrompt(context);
        List<LlmMessage> messages = promptBuilder.buildMessages(task, context);
        List<LlmTool> tools = resolveTools(context);

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
            pushPromptEvent(sessionId, round, systemPrompt, messages, context);

            StreamRoundResult streamResult = streamLlm(llmClient, messages, systemPrompt, tools, round, sessionId);
            String fullText = streamResult.fullText;
            Map<Integer, Map<String, Object>> toolCallAcc = streamResult.toolCallAcc;

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

            ToolExecutionResult executionResult = executeTools(promptBuilder, parsedToolCalls, agent, task, messages,
                sessionId);
            messages = executionResult.messages;
            List<ToolCallRecord> roundToolCalls = executionResult.roundToolCalls;
            boolean done = executionResult.done;
            toolCalls.addAll(roundToolCalls);
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

    private ChatClient resolveLlmClient(Agent agent) {
        return llmClientProvider.get(agent.getLlmName(), agent.getLlmModel());
    }

    private List<LlmTool> resolveTools(ReasoningContext context) {
        return context.getActorResources() == null
            ? List.of()
            : context.getActorResources().stream()
                .filter(r -> "tool".equals(r.getKind()) && r.getLlmTool() != null)
                .map(ContextResource::getLlmTool)
                .collect(Collectors.toList());
    }

    private void pushPromptEvent(String sessionId, int round, String systemPrompt, List<LlmMessage> messages,
        ReasoningContext context) {
        try {
            SseBus.getInstance().push(sessionId, Map.of(
                "type", "llm_prompt",
                "source", "actor",
                "round_label", "actor_round_" + round,
                "system_prompt", systemPrompt,
                "messages", messages.stream().map(m -> {
                    Map<String, Object> msg = new LinkedHashMap<>();
                    msg.put("role", defaultString(m.getRole()));
                    msg.put("content", defaultString(m.getContent()));
                    if (m.getToolCallId() != null && !m.getToolCallId().isBlank()) {
                        msg.put("tool_call_id", m.getToolCallId());
                    }
                    if (m.getToolCalls() != null && !m.getToolCalls().isEmpty()) {
                        msg.put("tool_calls", m.getToolCalls());
                    }
                    return msg;
                }).toList(),
                "tool_names", context.getActorResources() == null ? List.of() : context.getActorResources().stream()
                    .filter(r -> "tool".equals(r.getKind()) && r.getLlmTool() != null)
                    .map(r -> r.getLlmTool().getName())
                    .toList()
            ));
        } catch (Exception ignored) {
        }
    }

    private StreamRoundResult streamLlm(ChatClient llmClient, List<LlmMessage> messages, String systemPrompt,
        List<LlmTool> tools, int round, String sessionId) {
        String fullText = "";
        Map<Integer, Map<String, Object>> toolCallAcc = new LinkedHashMap<>();
        for (Iterator<StreamChunk> stream = llmClient.streamMessage(messages, systemPrompt, tools); stream.hasNext();) {
            StreamChunk chunk = stream.next();
            if (chunk.getTextDelta() != null && !chunk.getTextDelta().isEmpty()) {
                fullText += chunk.getTextDelta();
                pushSseEvent(sessionId, Map.of(
                    "type", "text_delta",
                    "delta", chunk.getTextDelta(),
                    "round", round
                ));
            }
            if (chunk.getToolCallDelta() != null && !chunk.getToolCallDelta().isEmpty()) {
                Object idxObj = chunk.getToolCallDelta().get("index");
                int idx = idxObj instanceof Number n ? n.intValue() : 0;
                toolCallAcc.put(idx, new LinkedHashMap<>(chunk.getToolCallDelta()));
            }
        }
        pushSseEvent(sessionId, Map.of(
            "type", "text_done",
            "text", fullText,
            "round", round
        ));
        return new StreamRoundResult(fullText, toolCallAcc);
    }

    private ToolExecutionResult executeTools(ActorPromptBuilder promptBuilder, List<ToolCallBlock> parsedToolCalls,
        Agent agent, Task task, List<LlmMessage> messages, String sessionId) {
        CallContext callContext = CallContext.builder()
            .sessionId(sessionId == null ? "" : sessionId)
            .agentId(agent == null || agent.getId() == null ? "" : agent.getId())
            .agent(agent)
            .task(task)
            .workingDir(resolveWorkingDir(task, agent))
            .build();
        List<ToolCallRecord> roundToolCalls = new ArrayList<>();
        boolean done = false;
        List<LlmMessage> nextMessages = messages;
        for (ToolCallBlock toolCall : parsedToolCalls) {
            Map<String, Object> args = toolCall.getInput() == null ? Map.of() : toolCall.getInput();
            ToolResult result;
            try {
                result = toolGateway.call(toolCall.getName(), args, agent, task.getId(), callContext);
            } catch (Exception e) {
                result = ToolResult.builder().content(String.valueOf(e.getMessage())).isError(true)
                    .errorCode("TOOL_EXEC_ERROR").build();
            }
            if (isTaskCompleteSignal(result) || task.isActorDone()) {
                done = true;
            }
            ToolCallRecord record = ToolCallRecord.builder()
                .toolCallId(toolCall.getId())
                .toolName(toolCall.getName())
                .arguments(args)
                .result(result.getContent() == null ? "" : result.getContent())
                .isError(result.isError())
                .build();
            roundToolCalls.add(record);
            nextMessages = promptBuilder.appendToolResult(nextMessages, toolCall.getName(), result, toolCall.getId());
            pushSseEvent(sessionId, Map.of(
                "type", "tool_call",
                "tool_name", toolCall.getName(),
                "arguments", args,
                "result", result.getContent() == null ? "" : result.getContent(),
                "is_error", result.isError(),
                "created_at", Instant.now().toString()
            ));
        }
        return new ToolExecutionResult(roundToolCalls, nextMessages, done);
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

    private void pushSseEvent(String sessionId, Map<String, Object> event) {
        try {
            SseBus.getInstance().push(sessionId, event);
        } catch (Exception ignored) {
        }
    }

    private String resolveWorkingDir(Task task, Agent agent) {
        if (task != null && task.getSettings() != null) {
            Object value = task.getSettings().get("working_dir");
            if (value != null) {
                String workingDir = String.valueOf(value);
                if (!workingDir.isBlank()) {
                    return workingDir;
                }
            }
        }
        if (agent != null && agent.getSettings() != null) {
            Object value = agent.getSettings().get("working_dir");
            if (value != null) {
                String workingDir = String.valueOf(value);
                if (!workingDir.isBlank()) {
                    return workingDir;
                }
            }
        }
        return properties.getBashExecCwd() == null ? "" : properties.getBashExecCwd();
    }

    private record StreamRoundResult(
        String fullText,
        Map<Integer, Map<String, Object>> toolCallAcc
    ) {
    }

    private record ToolExecutionResult(
        List<ToolCallRecord> roundToolCalls,
        List<LlmMessage> messages,
        boolean done
    ) {
    }
}
