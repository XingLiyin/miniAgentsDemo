package com.codex.miniagents.tools.mcp;

import java.util.List;
import java.util.Map;

/**
 * 对底层 MCP 客户端的最小适配接口。
 * <p>
 * 目标是模拟 Python 里的 self._af_tool 所需能力：
 * - functions
 * - load_tools()
 * - call_tool(...)
 * - close()
 * <p>
 * 上层 AbstractMcpProvider 只依赖这个接口，不直接依赖 Spring AI 具体类。
 */
public interface McpClientAdapter {
    /**
     * 初始化连接并完成必要握手。
     */
    void connect();

    /**
     * 返回当前已发现的远端工具定义。
     */
    List<McpFunctionDescriptor> listFunctions();

    /**
     * 重新从 MCP Server 拉取工具列表并返回最新结果。
     */
    List<McpFunctionDescriptor> reloadTools();

    /**
     * 调用远端工具。
     *
     * @param toolName 工具名
     * @param arguments 参数
     * @param metadata  透传的 MCP _meta 元数据
     * @return 远端返回内容；允许是 String、结构化对象、内容块列表等
     */
    Object callTool(String toolName, Map<String, Object> arguments, Map<String, Object> metadata);

    /**
     * 关闭连接并释放资源。
     */
    void close();
}
