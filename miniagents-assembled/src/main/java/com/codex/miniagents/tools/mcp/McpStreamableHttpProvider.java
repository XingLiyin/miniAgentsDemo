package com.codex.miniagents.tools.mcp;

import lombok.Getter;

@Getter
public class McpStreamableHttpProvider extends AbstractMcpProvider {

    private final String url;

    private final int timeout;

    public McpStreamableHttpProvider(String name, String url, int timeout) {
        super(name, new SpringAiStreamableHttpMcpClientAdapter(name, url, timeout));
        this.url = url;
        this.timeout = timeout;
    }

    @Override
    public void start() {
        mcpClientAdapter.connect();
        markInitialized();
    }
}
