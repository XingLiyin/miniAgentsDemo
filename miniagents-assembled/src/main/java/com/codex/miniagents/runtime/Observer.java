package com.codex.miniagents.runtime;

import com.codex.miniagents.common.SseBus;
import com.codex.miniagents.domain.model.agent.Agent;
import com.codex.miniagents.domain.model.session.Session;
import com.codex.miniagents.domain.model.task.Task;
import com.codex.miniagents.domain.service.TaskService;
import com.codex.miniagents.llm.ChatClient;
import com.codex.miniagents.llm.model.LlmMessage;
import com.codex.miniagents.llm.model.LlmTool;
import com.codex.miniagents.llm.model.StreamChunk;
import com.codex.miniagents.llm.model.ToolCallBlock;
import com.codex.miniagents.llm.registry.LlmClientProvider;
import com.codex.miniagents.runtime.model.ActorResult;
import com.codex.miniagents.runtime.model.ContextResource;
import com.codex.miniagents.runtime.model.ObserverVerdict;
import com.codex.miniagents.runtime.model.ReasoningContext;
import com.codex.miniagents.runtime.prompt.ObserverPromptBuilder;
import com.codex.miniagents.runtime.prompt.PromptBuilderFactory;
import com.codex.miniagents.tools.model.CallContext;
import com.codex.miniagents.tools.model.ToolResult;

import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Slf4j
@Component
public class Observer {
    private final LlmClientProvider llmClientProvider;
    private final ToolGateway toolGateway;
    private final TaskService taskService;

    public Observer(LlmClientProvider llmClientProvider, ToolGateway toolGateway, TaskService taskService) {
        this.llmClientProvider = llmClientProvider;
        this.toolGateway = toolGateway;
        this.taskService = taskService;
    }

    public ObserverVerdict observe(Session session, Agent agent, ActorResult result, ReasoningContext context,
        Task task, List<Task> taskList) {
        ChatClient llmClient = llmClientProvider.get(agent.getLlmName(), agent.getLlmModel());
        if (!hasText(context.getRole())) {
            log.debug("Observer: no role_md configured, using rule-based fallback");
            return ruleObserve(session, task, result);
        }
        try {
            return llmObserve(session, agent, result, context, task, taskList == null ? List.of() : taskList, llmClient);
        } catch (Exception e) {
            log.warn("Observer LLM call failed, falling back to rules: {}", e.getMessage());
            return ruleObserve(session, task, result);
        }
    }

    private ObserverVerdict ruleObserve(Session session, Task task, ActorResult result) {
        double tokenPct = session.getTokenBudget() <= 0 ? 0D : (double) session.getTokenUsed() / session.getTokenBudget();
        if (tokenPct > 0.9D) {
            taskService.finish(task.getId(), "Token budget nearly exhausted; treating as complete.");
            return ObserverVerdict.builder()
                .summary("Token budget nearly exhausted; stopping.")
                .build();
        }

        boolean success = result != null && result.isSuccess();
        if (success) {
            taskService.finish(task.getId(), result == null || result.getOutput() == null ? "" : result.getOutput());
        } else {
            taskService.fail(task.getId(), result == null
                ? ""
                : (result.getError() == null ? (result.getOutput() == null ? "" : result.getOutput()) : result.getError()));
        }
        return ObserverVerdict.builder()
            .summary(success ? "Completed this turn's task." : "Task failed this turn.")
            .build();
    }

    private ObserverVerdict llmObserve(Session session, Agent agent, ActorResult result, ReasoningContext context,
        Task task, List<Task> taskList, ChatClient llmClient) {
        ObserverPromptBuilder promptBuilder = PromptBuilderFactory.forObserver();
        String systemPrompt = promptBuilder.buildSystemPrompt(context);
        List<LlmMessage> messages = promptBuilder.buildMessages(session, result, context, task, taskList);
        List<LlmTool> tools = context.getObserverResources() == null
            ? List.of()
            : context.getObserverResources().stream()
                .filter(r -> "tool".equals(r.getKind()) && r.getLlmTool() != null)
                .map(ContextResource::getLlmTool)
                .toList();
        int maxRounds = agent.getLoopGuard() != null ? agent.getLoopGuard().getObserverMaxToolRounds() : 5;
        String lastLlmText = "";
        boolean stopObserver = false;

        for (int round = 0; round < maxRounds && !stopObserver; round++) {
            String roundLabel = "observer_round_" + round;
            pushLlmEvent(task.getSessionId(), roundLabel, systemPrompt, messages, tools);
            StreamResult roundResult = streamObserver(llmClient, messages, systemPrompt, tools, task.getSessionId(), roundLabel);
            if (hasText(roundResult.fullText())) {
                lastLlmText = roundResult.fullText();
            }
            List<ToolCallBlock> toolCalls = promptBuilder.buildToolCallsFromStream(roundResult.toolCallAcc());
            if (toolCalls.isEmpty()) {
                break;
            }
            messages = promptBuilder.appendAssistantToolCalls(messages, roundResult.fullText(), toolCalls);
            CallContext callContext = CallContext.builder()
                .sessionId(task.getSessionId() == null ? "" : task.getSessionId())
                .agentId(task.getAssignedAgentId() == null ? "" : task.getAssignedAgentId())
                .task(task)
                .build();
            for (ToolCallBlock call : toolCalls) {
                ToolResult toolResult = toolGateway.call(call.getName(),
                    call.getInput() == null ? Map.of() : call.getInput(), null, task.getId(), callContext);
                messages = promptBuilder.appendToolResult(messages, call.getName(), toolResult, call.getId());

                Task latestTask = taskService.get(task.getId());
                if (latestTask.getStatus() != com.codex.miniagents.domain.model.task.TaskStatus.TO_BE_OBSERVED) {
                    stopObserver = true;
                    break;
                }
            }
        }

        Task latestTask = taskService.get(task.getId());
        if (latestTask.getStatus() == com.codex.miniagents.domain.model.task.TaskStatus.TO_BE_OBSERVED) {
            throw new RuntimeException("Observer: no assessment submitted by LLM");
        }
        return ObserverVerdict.builder()
            .summary(hasText(task.getActorResult()) ? task.getActorResult()
                : (hasText(lastLlmText) ? lastLlmText : defaultString(latestTask.getResult())))
            .build();
    }

    private void pushLlmEvent(String sessionId, String roundLabel, String systemPrompt, List<LlmMessage> messages,
        List<LlmTool> tools) {
        if (sessionId == null || sessionId.isBlank()) {
            return;
        }
        try {
            Map<String, Object> event = new LinkedHashMap<>();
            event.put("type", "llm_prompt");
            event.put("source", "observer");
            event.put("round_label", roundLabel);
            event.put("system_prompt", systemPrompt);
            List<Map<String, Object>> msg = new ArrayList<>();
            for (LlmMessage m : messages) {
                Map<String, Object> item = new LinkedHashMap<>();
                item.put("role", m.getRole());
                item.put("content", m.getContent());
                msg.add(item);
            }
            event.put("messages", msg);
            List<String> toolNames = new ArrayList<>();
            for (LlmTool t : tools) {
                toolNames.add(t.getName());
            }
            event.put("tool_names", toolNames);
            SseBus.getInstance().push(sessionId, event);
        } catch (Exception ignored) {
        }
    }

    private StreamResult streamObserver(ChatClient llmClient, List<LlmMessage> messages, String systemPrompt,
        List<LlmTool> tools, String sessionId, String roundLabel) {
        String fullText = "";
        Map<Integer, Map<String, Object>> toolCallAcc = new LinkedHashMap<>();

        for (Iterator<StreamChunk> stream = llmClient.streamMessage(messages, systemPrompt, tools); stream.hasNext(); ) {
            StreamChunk chunk = stream.next();
            if (chunk.getTextDelta() != null && !chunk.getTextDelta().isEmpty()) {
                fullText += chunk.getTextDelta();
                try {
                    SseBus.getInstance().push(sessionId, Map.of(
                        "type", "observer_text_delta",
                        "delta", chunk.getTextDelta(),
                        "round_label", roundLabel
                    ));
                } catch (Exception ignored) {
                }
            }
            if (chunk.getToolCallDelta() != null && !chunk.getToolCallDelta().isEmpty()) {
                Object idxObj = chunk.getToolCallDelta().get("index");
                int idx = idxObj instanceof Number n ? n.intValue() : 0;
                toolCallAcc.put(idx, new LinkedHashMap<>(chunk.getToolCallDelta()));
            }
        }

        try {
            SseBus.getInstance().push(sessionId, Map.of(
                "type", "observer_text_done",
                "text", fullText,
                "round_label", roundLabel
            ));
        } catch (Exception ignored) {
        }

        return new StreamResult(fullText, toolCallAcc);
    }

    private boolean hasText(String s) {
        return s != null && !s.trim().isEmpty();
    }

    private String defaultString(String s) {
        return s == null ? "" : s;
    }

    private record StreamResult(String fullText, Map<Integer, Map<String, Object>> toolCallAcc) {}
}
