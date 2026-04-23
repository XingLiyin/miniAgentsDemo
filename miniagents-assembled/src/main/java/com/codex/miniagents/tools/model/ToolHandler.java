package com.codex.miniagents.tools.model;

import java.util.Map;

@FunctionalInterface
public interface ToolHandler {
    ToolResult apply(Map<String, Object> arguments, CallContext context);

    default ToolResult apply(Map<String, Object> arguments) {
        return apply(arguments, CallContext.empty());
    }
}
