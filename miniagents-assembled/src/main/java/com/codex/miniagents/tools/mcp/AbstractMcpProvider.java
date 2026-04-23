package com.codex.miniagents.tools.mcp;

import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.llm.model.InputSchema;
import com.codex.miniagents.tools.model.CallContext;
import com.codex.miniagents.tools.model.ToolDefinition;
import com.codex.miniagents.tools.model.ToolHandler;
import com.codex.miniagents.tools.model.ToolResult;

import lombok.Getter;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Getter
public abstract class AbstractMcpProvider implements McpProvider {
    protected final String name;

    protected final McpClientAdapter mcpClientAdapter;

    protected volatile boolean initialized = false;

    protected AbstractMcpProvider(String name, McpClientAdapter mcpClientAdapter) {
        this.name = name;
        this.mcpClientAdapter = mcpClientAdapter;
    }

    @Override
    public String getName() {
        return name;
    }

    @Override
    public List<ToolDefinition> listDefinitions() {
        ensureInitialized();
        List<McpFunctionDescriptor> functions = mcpClientAdapter.listFunctions();
        List<ToolDefinition> result = new ArrayList<>(functions.size());
        for (McpFunctionDescriptor function : functions) {
            result.add(mapFunctionTool(function));
        }
        return result;
    }

    @Override
    public List<ToolDefinition> reloadTools() {
        ensureInitialized();
        List<McpFunctionDescriptor> functions = mcpClientAdapter.reloadTools();
        List<ToolDefinition> result = new ArrayList<>(functions.size());
        for (McpFunctionDescriptor function : functions) {
            result.add(mapFunctionTool(function));
        }
        return result;
    }

    @Override
    public ToolResult call(String toolName, Map<String, Object> arguments, CallContext context) {
        ensureInitialized();
        Object result = mcpClientAdapter.callTool(toolName, arguments, buildMcpMetadata(context, Map.of()));
        String text = contentToText(result);
        return ToolResult.builder().content(text).metadata(Map.of()).build();
    }

    @Override
    public void stop() {
        initialized = false;
        try {
            mcpClientAdapter.close();
        } catch (Exception ignored) {
            // 与 Python 版本保持一致：stop() 尽量吞掉关闭异常
        }
    }

    protected ToolDefinition mapFunctionTool(McpFunctionDescriptor function) {
        ToolHandler handler = (arguments, context) -> {
            ensureInitialized();
            Object result = mcpClientAdapter.callTool(function.getName(), arguments,
                buildMcpMetadata(context, function.getMetadata()));
            return ToolResult.builder().content(contentToText(result)).metadata(Map.of()).build();
        };

        return ToolDefinition.builder()
            .name(function.getName())
            .description(function.getDescription() == null ? "" : function.getDescription())
            .inputSchema(buildInputSchema(function.getInputSchema()))
            .metadata(function.getMetadata() == null ? Map.of() : function.getMetadata())
            .handler(handler)
            .build();
    }

    protected void markInitialized() {
        this.initialized = true;
    }

    protected void ensureInitialized() {
        if (!initialized) {
            throw new AppException(ErrorCode.MCP_NOT_STARTED,
                getClass().getSimpleName() + ".start() has not been called");
        }
    }

    protected InputSchema buildInputSchema(Map<String, Object> rawSchema) {
        InputSchema schema = new InputSchema();
        if (rawSchema == null || rawSchema.isEmpty()) {
            return schema;
        }

        Object type = rawSchema.get("type");
        if (type instanceof String s) {
            schema.setType(s);
        }

        Object properties = rawSchema.get("properties");
        if (properties instanceof Map<?, ?> map) {
            @SuppressWarnings("unchecked") Map<String, Object> casted = (Map<String, Object>) map;
            schema.setProperties(casted);
        }

        Object required = rawSchema.get("required");
        if (required instanceof List<?> list) {
            List<String> requiredList = new ArrayList<>();
            for (Object item : list) {
                requiredList.add(String.valueOf(item));
            }
            schema.setRequired(requiredList);
        }

        return schema;
    }

    protected String contentToText(Object content) {
        if (content == null) {
            return "";
        }
        if (content instanceof String str) {
            return str;
        }
        if (content instanceof List<?> list) {
            List<String> parts = new ArrayList<>();
            for (Object item : list) {
                if (item == null) {
                    continue;
                }
                parts.add(String.valueOf(item));
            }
            return String.join("\n", parts);
        }
        return String.valueOf(content);
    }

    protected Map<String, Object> buildMcpMetadata(CallContext context, Map<String, Object> baseMetadata) {
        Map<String, Object> metadata = new LinkedHashMap<>();
        if (baseMetadata != null && !baseMetadata.isEmpty()) {
            metadata.putAll(baseMetadata);
        }
        if (context != null) {
            metadata.put("netcowork/sessionId", context.getSessionId());
            metadata.put("netcowork/agentId", context.getAgentId());
            metadata.put("netcowork/taskId", context.getTask() == null ? "" : context.getTask().getId());
            metadata.put("working_dir", context.getWorkingDir() == null ? "" : context.getWorkingDir());
        }
        return metadata;
    }
}
