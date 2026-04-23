package com.codex.miniagents.tools.mcp;

import io.modelcontextprotocol.client.McpClient;
import io.modelcontextprotocol.client.McpSyncClient;
import io.modelcontextprotocol.client.transport.HttpClientStreamableHttpTransport;
import io.modelcontextprotocol.json.McpJsonMapper;
import io.modelcontextprotocol.spec.McpClientTransport;
import io.modelcontextprotocol.spec.McpSchema;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

public class SpringAiStreamableHttpMcpClientAdapter implements McpClientAdapter {
    private static final String DEFAULT_ENDPOINT = "/mcp";
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    private final String name;

    private final String url;

    private final int timeoutSeconds;

    private volatile McpSyncClient client;

    private volatile List<McpFunctionDescriptor> cachedFunctions = List.of();

    public SpringAiStreamableHttpMcpClientAdapter(String name, String url, int timeoutSeconds) {
        this.name = name;
        this.url = url;
        this.timeoutSeconds = timeoutSeconds <= 0 ? 30 : timeoutSeconds;
    }

    @Override
    public synchronized void connect() {
        if (this.client != null) {
            return;
        }

        McpJsonMapper jsonMapper = McpJsonMapper.getDefault();

        McpClientTransport transport = HttpClientStreamableHttpTransport.builder(url)
            .endpoint(DEFAULT_ENDPOINT)
            .jsonMapper(jsonMapper)
            .build();

        this.client = McpClient.sync(transport)
            .requestTimeout(Duration.ofSeconds(timeoutSeconds))
            .capabilities(McpSchema.ClientCapabilities.builder().roots(false).build())
            .build();

        this.client.initialize();
        this.cachedFunctions = fetchFunctions();
    }

    @Override
    public List<McpFunctionDescriptor> listFunctions() {
        ensureConnected();
        return List.copyOf(cachedFunctions);
    }

    @Override
    public synchronized List<McpFunctionDescriptor> reloadTools() {
        ensureConnected();
        this.cachedFunctions = fetchFunctions();
        return List.copyOf(cachedFunctions);
    }

    @Override
    public Object callTool(String toolName, Map<String, Object> arguments, Map<String, Object> metadata) {
        ensureConnected();
        return client.callTool(new McpSchema.CallToolRequest(toolName, arguments,
            metadata == null ? Map.of() : metadata));
    }

    @Override
    public synchronized void close() {
        if (client == null) {
            return;
        }
        try {
            client.closeGracefully();
        } finally {
            client = null;
            cachedFunctions = List.of();
        }
    }

    private List<McpFunctionDescriptor> fetchFunctions() {
        McpSchema.ListToolsResult toolsResult = client.listTools();
        List<McpFunctionDescriptor> result = new ArrayList<>();
        for (McpSchema.Tool tool : toolsResult.tools()) {
            result.add(McpFunctionDescriptor.builder()
                .name(tool.name())
                .description(tool.description() == null ? "" : tool.description())
                .inputSchema(normalizeSchema(tool.inputSchema()))
                .metadata(normalizeMetadata(tool.meta()))
                .build());
        }
        return result;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> normalizeSchema(Object schema) {
        if (schema == null) {
            return Map.of();
        }
        if (schema instanceof Map<?, ?> map) {
            return (Map<String, Object>) map;
        }
        return OBJECT_MAPPER.convertValue(schema, new TypeReference<Map<String, Object>>() {});
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> normalizeMetadata(Object metadata) {
        if (metadata == null) {
            return Map.of();
        }
        if (metadata instanceof Map<?, ?> map) {
            return (Map<String, Object>) map;
        }
        return OBJECT_MAPPER.convertValue(metadata, new TypeReference<Map<String, Object>>() {});
    }

    private void ensureConnected() {
        if (client == null) {
            throw new IllegalStateException("MCP client '" + name + "' is not connected");
        }
    }
}
