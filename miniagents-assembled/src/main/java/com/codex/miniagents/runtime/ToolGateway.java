package com.codex.miniagents.runtime;

import com.codex.miniagents.domain.model.agent.Agent;
import com.codex.miniagents.domain.model.task.Task;
import com.codex.miniagents.domain.model.tool.ToolCall;
import com.codex.miniagents.domain.service.TaskService;
import com.codex.miniagents.infrastructure.storage.repository.ToolCallRepository;
import com.codex.miniagents.tools.model.CallContext;
import com.codex.miniagents.tools.registry.ToolRegistry;
import com.codex.miniagents.tools.model.ToolResult;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

@Component
public class ToolGateway {

    private final PolicyEngine policyEngine;
    private final ToolRegistry toolRegistry;
    private final ToolCallRepository toolCallRepository;
    private final TaskService taskService;

    public ToolGateway(PolicyEngine policyEngine, ToolRegistry toolRegistry, ToolCallRepository toolCallRepository,
        TaskService taskService) {
        this.policyEngine = policyEngine;
        this.toolRegistry = toolRegistry;
        this.toolCallRepository = toolCallRepository;
        this.taskService = taskService;
    }

    public ToolResult call(String sessionId, String taskId, Agent agent, String toolName, Map<String, Object> arguments) {
        return call(sessionId, taskId, agent, null, toolName, arguments);
    }

    public ToolResult call(String sessionId, String taskId, Agent agent, Task task, String toolName,
        Map<String, Object> arguments) {
        if (agent != null) {
            policyEngine.authorize(agent, toolName);
        }

        String callId = "call_" + UUID.randomUUID().toString().replace("-", "");
        Instant startedAt = Instant.now();
        ToolCall running = new ToolCall();
        running.setId(callId);
        running.setSessionId(sessionId);
        running.setTaskId(taskId);
        running.setAgentId(agent == null ? null : agent.getId());
        running.setToolName(toolName);
        running.setStatus("RUNNING");
        running.setArguments(redact(arguments));
        running.setStartedAt(startedAt);
        toolCallRepository.append(sessionId, running);

        ToolResult result;
        String status;
        String error = null;
        try {
            var definition = toolRegistry.get(toolName);
            CallContext context = buildCallContext(sessionId, taskId, agent, task);
            result = definition.getHandler().apply(arguments, context);
            status = result.isError() ? "FAILED" : "SUCCEEDED";
            if (result.isError()) {
                error = result.getErrorCode();
            }
        } catch (Exception e) {
            result = ToolResult.builder().content("").isError(true).errorCode("TOOL_EXEC_ERROR").build();
            status = "FAILED";
            error = e.getMessage();
        }

        ToolCall finished = new ToolCall();
        finished.setId(callId);
        finished.setSessionId(sessionId);
        finished.setTaskId(taskId);
        finished.setAgentId(agent == null ? null : agent.getId());
        finished.setToolName(toolName);
        finished.setStatus(status);
        finished.setArguments(redact(arguments));
        finished.setStartedAt(startedAt);
        finished.setResult(result.getContent() == null ? null
            : result.getContent().substring(0, Math.min(200, result.getContent().length())));
        finished.setError(error);
        finished.setFinishedAt(Instant.now());
        toolCallRepository.append(sessionId, finished);

        return result;
    }

    private Map<String, Object> redact(Map<String, Object> arguments) {
        if (arguments == null || arguments.isEmpty()) {
            return new HashMap<>();
        }
        Map<String, Object> redacted = new HashMap<>(arguments);
        Object headersObj = redacted.get("headers");
        if (headersObj instanceof Map<?, ?> headers) {
            Map<String, Object> cleaned = new HashMap<>();
            for (Map.Entry<?, ?> entry : headers.entrySet()) {
                String key = String.valueOf(entry.getKey());
                Object value = entry.getValue();
                if ("authorization".equalsIgnoreCase(key) || "x-api-key".equalsIgnoreCase(key)
                    || "cookie".equalsIgnoreCase(key)) {
                    cleaned.put(key, "***");
                } else {
                    cleaned.put(key, value);
                }
            }
            redacted.put("headers", cleaned);
        }
        return redacted;
    }

    private CallContext buildCallContext(String sessionId, String taskId, Agent agent, Task task) {
        Task resolvedTask = task;
        if (resolvedTask == null) {
            try {
                resolvedTask = taskService.get(taskId);
            } catch (Exception ignored) {
            }
        }
        String agentId = agent != null
            ? agent.getId()
            : (resolvedTask == null || resolvedTask.getAssignedAgentId() == null ? "" : resolvedTask.getAssignedAgentId());
        return CallContext.builder()
            .sessionId(sessionId == null ? "" : sessionId)
            .agentId(agentId == null ? "" : agentId)
            .agent(agent)
            .task(resolvedTask)
            .workingDir(resolveWorkingDir(resolvedTask))
            .build();
    }

    private String resolveWorkingDir(Task task) {
        if (task == null || task.getSettings() == null) {
            return "";
        }
        try {
            Object value = task.getSettings().get("working_dir");
            return value == null ? "" : String.valueOf(value);
        } catch (Exception ignored) {
            return "";
        }
    }
}
