package com.codex.miniagents.tools.provider;

import com.codex.miniagents.tools.model.ToolDefinition;
import com.codex.miniagents.tools.model.CallContext;
import com.codex.miniagents.tools.model.ToolResult;

import java.util.List;
import java.util.Map;

public interface ToolProvider {
    List<ToolDefinition> listDefinitions();

    default ToolResult call(String toolName, Map<String, Object> arguments) {
        return call(toolName, arguments, CallContext.empty());
    }

    ToolResult call(String toolName, Map<String, Object> arguments, CallContext context);
}
