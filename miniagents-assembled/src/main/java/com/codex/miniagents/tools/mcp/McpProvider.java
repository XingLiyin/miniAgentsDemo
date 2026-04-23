package com.codex.miniagents.tools.mcp;

import com.codex.miniagents.tools.model.ToolDefinition;
import com.codex.miniagents.tools.provider.ToolProvider;

import java.util.List;

public interface McpProvider extends ToolProvider {
    String getName();

    /**
     * 启动 provider，建立与 MCP Server 的连接并加载工具。
     */
    void start();

    /**
     * 停止 provider，关闭连接并释放资源。
     */
    void stop();

    /**
     * 重新从 MCP Server 拉取工具列表并返回最新定义。
     */
    List<ToolDefinition> reloadTools();

    /**
     * 是否已经完成 start。
     */
    boolean isInitialized();
}
