package com.codex.miniagents.tools.provider;

import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.tools.model.CallContext;
import com.codex.miniagents.tools.model.ToolDefinition;
import com.codex.miniagents.tools.model.ToolResult;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class BuiltinToolProvider implements ToolProvider {
    private final Map<String, ToolDefinition> defs;

    public BuiltinToolProvider(List<ToolDefinition> definitions) {
        this.defs = new HashMap<>();
        if (definitions != null) {
            for (ToolDefinition definition : definitions) {
                this.defs.put(definition.getName(), definition);
            }
        }
    }

    @Override
    public List<ToolDefinition> listDefinitions() {
        return new ArrayList<>(defs.values());
    }

    @Override
    public ToolResult call(String toolName, Map<String, Object> arguments, CallContext context) {
        ToolDefinition definition = defs.get(toolName);
        if (definition == null) {
            throw new AppException(ErrorCode.TOOL_NOT_FOUND, "Tool '" + toolName + "' not in BuiltinToolProvider");
        }
        return definition.getHandler().apply(arguments, context == null ? CallContext.empty() : context);
    }
}
