package com.codex.miniagents.runtime;

import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.domain.model.agent.Agent;
import com.codex.miniagents.tools.registry.ToolRegistry;

import org.springframework.stereotype.Component;

import java.util.List;

@Component
public class PolicyEngine {
    private final ToolRegistry toolRegistry;

    public PolicyEngine(ToolRegistry toolRegistry) {
        this.toolRegistry = toolRegistry;
    }

    public void authorize(Agent agent, String toolName) {
        if (!toolRegistry.isRegistered(toolName)) {
            throw new AppException(ErrorCode.TOOL_NOT_FOUND, "Tool '" + toolName + "' is not registered");
        }
        boolean allowed = agent != null
            && agent.getActToolList() != null
            && agent.getActToolList().contains(toolName);
        if (!allowed && agent != null && agent.getMcpActServers() != null) {
            for (String server : agent.getMcpActServers()) {
                List<String> providerTools = toolRegistry.getServerToolNames(server);
                if (providerTools.contains(toolName)) {
                    allowed = true;
                    break;
                }
            }
        }
        if (!allowed) {
            throw new AppException(ErrorCode.TOOL_NOT_AUTHORIZED,
                "Tool '" + toolName + "' is not allowed for agent '" + agent.getId() + "'");
        }
    }
}
