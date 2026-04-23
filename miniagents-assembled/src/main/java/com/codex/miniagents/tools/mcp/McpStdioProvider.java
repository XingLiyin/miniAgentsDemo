package com.codex.miniagents.tools.mcp;

import lombok.Getter;

import java.util.List;
import java.util.Map;

@Getter
public class McpStdioProvider extends AbstractMcpProvider {
    private final String command;

    private final List<String> args;

    private final Map<String, String> env;

    public McpStdioProvider(String name, String command, List<String> args, Map<String, String> env) {
        super(name, new SpringAiStdioMcpClientAdapter(name, command, args, env));
        this.command = command;
        this.args = args == null ? List.of() : List.copyOf(args);
        this.env = env == null ? Map.of() : Map.copyOf(env);
    }

    @Override
    public void start() {
        mcpClientAdapter.connect();
        markInitialized();
    }
}
