package com.codex.miniagents.runtime;

import com.codex.miniagents.domain.model.agent.Agent;
import com.codex.miniagents.domain.model.tool.ToolCall;
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

    public ToolGateway(PolicyEngine policyEngine, ToolRegistry toolRegistry, ToolCallRepository toolCallRepository) {
        this.policyEngine = policyEngine;
        this.toolRegistry = toolRegistry;
        this.toolCallRepository = toolCallRepository;
    }

    public ToolResult call(String toolName, Map<String, Object> arguments, Agent agent, String taskId) {
        return call(toolName, arguments, agent, taskId, null);
    }

    public ToolResult call(String toolName, Map<String, Object> arguments, Agent agent, String taskId,
        CallContext ctx) {
        if (agent != null) {
            policyEngine.authorize(agent, toolName);
        }

        CallContext context = ctx == null ? buildFallbackContext(agent) : ctx;
        String sessionId = context.getSessionId() == null ? "" : context.getSessionId();
        String agentId = context.getAgentId() == null || context.getAgentId().isBlank()
            ? (agent == null ? null : agent.getId())
            : context.getAgentId();
        String callId = "call_" + UUID.randomUUID().toString().replace("-", "");
        Instant startedAt = Instant.now();
        ToolCall running = new ToolCall();
        running.setId(callId);
        running.setSessionId(sessionId);
        running.setTaskId(taskId);
        running.setAgentId(agentId);
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
        finished.setAgentId(agentId);
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

    private CallContext buildFallbackContext(Agent agent) {
        String sessionId = agent != null && agent.getSessionId() != null
            ? agent.getSessionId() : "";
        String agentId = agent != null
            ? agent.getId() : "";
        return CallContext.builder()
            .sessionId(sessionId == null ? "" : sessionId)
            .agentId(agentId == null ? "" : agentId)
            .agent(agent)
            .build();
    }
}
