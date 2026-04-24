package com.codex.miniagents.tools.mcp;

import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.infrastructure.storage.file.McpConfigStore;
import com.codex.miniagents.tools.model.McpConfigData;
import com.codex.miniagents.tools.model.McpServerInfo;
import com.codex.miniagents.tools.registry.ToolRegistry;

import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;

import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

@Slf4j
@Service
@RequiredArgsConstructor
public class McpService {
    private final ToolRegistry toolRegistry;

    private final McpConfigStore store;

    @PostConstruct
    public void init() {
        try {
            restoreAll();
        } catch (Exception e) {
            log.warn("McpService: failed to restore MCP servers on startup", e);
        }
    }

    public McpServerInfo registerStdio(String name, String command, List<String> args,
        java.util.Map<String, String> env) {
        if (store.get(name).isPresent()) {
            throw new AppException(ErrorCode.MCP_ALREADY_EXISTS, "MCP server '" + name + "' already registered");
        }

        List<String> toolNames = toolRegistry.registerMcpStdio(name, command, args, env);

        store.save(McpConfigData.builder()
            .name(name)
            .type("stdio")
            .command(command)
            .args(args == null ? List.of() : List.copyOf(args))
            .env(env == null ? java.util.Map.of() : java.util.Map.copyOf(env))
            .build());

        return McpServerInfo.builder()
            .name(name)
            .type("stdio")
            .tools(toolNames)
            .command(command)
            .args(args == null ? List.of() : List.copyOf(args))
            .build();
    }

    public McpServerInfo registerHttp(String name, String url, Integer timeout) {
        if (store.get(name).isPresent()) {
            throw new AppException(ErrorCode.MCP_ALREADY_EXISTS, "MCP server '" + name + "' already registered");
        }

        int actualTimeout = timeout == null ? 30 : timeout;
        List<String> toolNames;
        try {
            toolNames = toolRegistry.registerMcpHttp(name, url, actualTimeout);
        } catch (RuntimeException e) {
            if (hasCause(e, java.util.concurrent.CancellationException.class)) {
                throw new AppException(ErrorCode.MCP_CONNECT_CANCELLED,
                    "MCP connection was cancelled; check server availability");
            }
            if (hasCause(e, java.util.concurrent.TimeoutException.class)) {
                throw new AppException(ErrorCode.MCP_CONNECT_TIMEOUT,
                    "MCP connection timed out after " + actualTimeout + " s");
            }
            throw e;
        }

        store.save(McpConfigData.builder().name(name).type("http").url(url).timeout(actualTimeout).build());

        return McpServerInfo.builder()
            .name(name)
            .type("http")
            .tools(toolNames)
            .url(url)
            .timeoutSec(actualTimeout)
            .build();
    }

    public List<McpServerInfo> listAll() {
        List<McpServerInfo> result = new ArrayList<>();
        for (McpConfigData config : store.listAll()) {
            McpServerInfo info = configToInfo(config);
            if (info != null) {
                result.add(info);
            }
        }
        return result;
    }

    public McpServerInfo get(String name) {
        Optional<McpConfigData> config = store.get(name);
        if (config.isEmpty()) {
            throw new AppException(ErrorCode.MCP_NOT_FOUND, "MCP server '" + name + "' not found");
        }

        McpServerInfo info = configToInfo(config.get());
        if (info == null) {
            throw new AppException(ErrorCode.MCP_INVALID_CONFIG, "MCP server '" + name + "' has invalid config");
        }
        return info;
    }

    public void delete(String name) {
        if (store.get(name).isEmpty()) {
            throw new AppException(ErrorCode.MCP_NOT_FOUND, "MCP server '" + name + "' not found");
        }

        toolRegistry.shutdownOne(name);
        store.delete(name);
    }

    public McpServerInfo refresh(String name) {
        McpConfigData config = store.get(name)
            .orElseThrow(() -> new AppException(ErrorCode.MCP_NOT_FOUND, "MCP server '" + name + "' not found"));

        toolRegistry.refreshMcp(name);

        McpServerInfo info = configToInfo(config);
        if (info == null) {
            throw new AppException(ErrorCode.MCP_INVALID_CONFIG, "MCP server '" + name + "' has invalid config");
        }

        info.setTools(toolRegistry.getServerToolNames(name));
        return info;
    }

    public void restoreAll() {
        List<McpConfigData> configs = store.listAll();
        if (configs.isEmpty()) {
            return;
        }

        log.info("McpService.restoreAll: restoring {} MCP server(s)", configs.size());
        for (McpConfigData config : configs) {
            String name = config.getName() == null ? "<unknown>" : config.getName();
            try {
                restoreOne(config);
                log.info("McpService.restoreAll: restored '{}'", name);
            } catch (Exception e) {
                log.warn("McpService.restoreAll: failed to restore '{}', skipping", name, e);
            }
        }
    }

    private void restoreOne(McpConfigData config) {
        String name = config.getName();

        if (toolRegistry.hasProvider(name)) {
            return;
        }

        if ("stdio".equals(config.getType())) {
            toolRegistry.registerMcpStdio(name, config.getCommand(), config.getArgs(), config.getEnv());
        } else if ("http".equals(config.getType())) {
            toolRegistry.registerMcpHttp(name, config.getUrl(), config.getTimeout() == null ? 30 : config.getTimeout());
        } else {
            throw new AppException(ErrorCode.MCP_INVALID_CONFIG, "Unknown MCP server type: '" + config.getType() + "'");
        }
    }

    private McpServerInfo configToInfo(McpConfigData config) {
        String type = config.getType();
        String name = config.getName() == null ? "" : config.getName();

        if ("stdio".equals(type)) {
            return McpServerInfo.builder()
                .name(name)
                .type("stdio")
                .tools(List.of())
                .command(config.getCommand())
                .args(config.getArgs() == null ? List.of() : List.copyOf(config.getArgs()))
                .build();
        }

        if ("http".equals(type)) {
            return McpServerInfo.builder()
                .name(name)
                .type("http")
                .tools(List.of())
                .url(config.getUrl())
                .timeoutSec(config.getTimeout() == null ? 30 : config.getTimeout())
                .build();
        }

        return null;
    }

    private boolean hasCause(Throwable throwable, Class<? extends Throwable> targetType) {
        Throwable current = throwable;
        while (current != null) {
            if (targetType.isInstance(current)) {
                return true;
            }
            current = current.getCause();
        }
        return false;
    }
}
