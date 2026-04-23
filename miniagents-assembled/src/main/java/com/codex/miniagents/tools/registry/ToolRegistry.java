package com.codex.miniagents.tools.registry;

import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.llm.model.LlmTool;
import com.codex.miniagents.tools.ToolSyncService;
import com.codex.miniagents.tools.control.ControlToolProvider;
import com.codex.miniagents.tools.mcp.AbstractMcpProvider;
import com.codex.miniagents.tools.mcp.McpStdioProvider;
import com.codex.miniagents.tools.mcp.McpStreamableHttpProvider;
import com.codex.miniagents.tools.model.ToolDefinition;
import com.codex.miniagents.tools.provider.BuiltinToolProvider;
import com.codex.miniagents.tools.provider.ToolProvider;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;

import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;

@Slf4j
@Component
@RequiredArgsConstructor
public class ToolRegistry {
    private final ToolSyncService toolSyncService;

    private final BuiltinToolProvider builtinToolProvider;

    private final ControlToolProvider controlToolProvider;

    private final Map<String, ToolDefinition> tools = new LinkedHashMap<>();

    private final List<AbstractMcpProvider> mcpProviders = new ArrayList<>();

    private final Map<String, List<String>> providerToolNames = new LinkedHashMap<>();

    private final LinkedHashSet<String> controlToolNames = new LinkedHashSet<>();

    @PostConstruct
    public void init() {
        registerProvider(builtinToolProvider, "builtin");
        registerControlProvider(controlToolProvider, "control");
    }

    public synchronized void register(ToolDefinition toolDefinition) {
        tools.put(toolDefinition.getName(), toolDefinition);
        log.debug("ToolRegistry: registered tool '{}'", toolDefinition.getName());
    }

    public synchronized void registerAsControl(ToolDefinition toolDefinition) {
        register(toolDefinition);
        controlToolNames.add(toolDefinition.getName());
    }

    public synchronized void registerProvider(ToolProvider provider, String name) {
        List<ToolDefinition> toolDefinitions = List.copyOf(provider.listDefinitions());
        for (ToolDefinition toolDefinition : toolDefinitions) {
            register(toolDefinition);
        }
        providerToolNames.put(name, toolDefinitions.stream().map(ToolDefinition::getName).toList());
        syncAdded(toolDefinitions, name);
    }

    public synchronized List<String> registerControlProvider(ToolProvider provider, String name) {
        List<ToolDefinition> toolDefinitions = List.copyOf(provider.listDefinitions());
        List<String> toolNames = new ArrayList<>();
        for (ToolDefinition toolDefinition : toolDefinitions) {
            registerAsControl(toolDefinition);
            toolNames.add(toolDefinition.getName());
        }
        providerToolNames.put(name, List.copyOf(toolNames));
        syncAdded(toolDefinitions, name);
        return List.copyOf(toolNames);
    }

    public synchronized List<String> registerMcpStdio(String name, String command, List<String> args,
        Map<String, String> env) {
        McpStdioProvider provider = new McpStdioProvider(name, command, args, env);
        provider.start();
        mcpProviders.add(provider);
        return registerProviderWithTracking(name, provider);
    }

    public synchronized List<String> registerMcpHttp(String name, String url, int timeout) {
        McpStreamableHttpProvider provider = new McpStreamableHttpProvider(name, url, timeout);
        provider.start();
        mcpProviders.add(provider);
        return registerProviderWithTracking(name, provider);
    }

    public synchronized void shutdownOne(String name) {
        AbstractMcpProvider provider = mcpProviders.stream()
            .filter(p -> Objects.equals(p.getName(), name))
            .findFirst()
            .orElse(null);

        if (provider == null) {
            log.warn("ToolRegistry.shutdownOne: provider '{}' not found", name);
            return;
        }

        try {
            provider.stop();
        } catch (Exception e) {
            log.error("Error stopping MCP provider '{}'", name, e);
        }

        mcpProviders.remove(provider);

        List<String> removedNames = providerToolNames.remove(name);
        if (removedNames == null) {
            removedNames = List.of();
        }

        for (String toolName : removedNames) {
            tools.remove(toolName);
        }
        syncRemoved(removedNames);

        log.debug("ToolRegistry: removed provider '{}'", name);
    }

    public synchronized RefreshResult refreshMcp(String name) {
        AbstractMcpProvider provider = mcpProviders.stream()
            .filter(p -> Objects.equals(p.getName(), name))
            .findFirst()
            .orElseThrow(
                () -> new AppException(ErrorCode.MCP_NOT_FOUND, "MCP provider '" + name + "' not found in registry"));

        List<ToolDefinition> newDefinitions = provider.reloadTools();
        var oldNames = new LinkedHashSet<>(providerToolNames.getOrDefault(name, List.of()));
        var newNames = new LinkedHashSet<>(newDefinitions.stream().map(ToolDefinition::getName).toList());

        List<String> addedNames = newNames.stream().filter(n -> !oldNames.contains(n)).sorted().toList();

        List<String> removedNames = oldNames.stream().filter(n -> !newNames.contains(n)).sorted().toList();

        for (String toolName : removedNames) {
            tools.remove(toolName);
        }

        List<ToolDefinition> addedDefinitions = new ArrayList<>();
        for (ToolDefinition definition : newDefinitions) {
            tools.put(definition.getName(), definition);
            if (addedNames.contains(definition.getName())) {
                addedDefinitions.add(definition);
            }
        }

        providerToolNames.put(name, newNames.stream().sorted().toList());

        if (!addedDefinitions.isEmpty()) {
            syncAdded(addedDefinitions, name);
        }
        if (!removedNames.isEmpty()) {
            syncRemoved(removedNames);
        }

        log.info("ToolRegistry.refreshMcp '{}': +{} -{}", name, addedNames.size(), removedNames.size());
        return new RefreshResult(addedNames, removedNames);
    }

    private synchronized List<String> registerProviderWithTracking(String name, ToolProvider provider) {
        List<ToolDefinition> toolDefinitions = List.copyOf(provider.listDefinitions());
        List<String> toolNames = new ArrayList<>();
        for (ToolDefinition toolDefinition : toolDefinitions) {
            register(toolDefinition);
            toolNames.add(toolDefinition.getName());
        }
        providerToolNames.put(name, List.copyOf(toolNames));
        syncAdded(toolDefinitions, name);
        return List.copyOf(toolNames);
    }

    @PreDestroy
    public synchronized void shutdown() {
        for (AbstractMcpProvider provider : mcpProviders) {
            try {
                provider.stop();
            } catch (Exception e) {
                log.error("Error stopping MCP provider {}", provider.getName(), e);
            }
        }
        mcpProviders.clear();
    }

    public synchronized ToolDefinition get(String name) {
        ToolDefinition definition = tools.get(name);
        if (definition == null) {
            throw new AppException(ErrorCode.TOOL_NOT_FOUND, "Tool '" + name + "' is not registered");
        }
        return definition;
    }

    public synchronized boolean isRegistered(String name) {
        return tools.containsKey(name);
    }

    public synchronized List<String> getServerToolNames(String name) {
        return List.copyOf(providerToolNames.getOrDefault(name, List.of()));
    }

    public synchronized boolean hasProvider(String name) {
        return providerToolNames.containsKey(name);
    }

    public synchronized LinkedHashSet<String> getControlToolNames() {
        return new LinkedHashSet<>(controlToolNames);
    }

    public synchronized List<String> listNames() {
        return List.copyOf(tools.keySet());
    }

    public synchronized List<ToolDefinition> listAllDefinitions() {
        return List.copyOf(tools.values());
    }

    public synchronized List<LlmTool> toLlmTools(List<String> names) {
        List<LlmTool> result = new ArrayList<>();
        for (String name : names) {
            ToolDefinition definition = tools.get(name);
            if (definition != null) {
                result.add(definition.toLlmTool());
            } else {
                log.warn("ToolRegistry.toLlmTools: unknown tool '{}', skipped", name);
            }
        }
        return result;
    }

    public synchronized List<String> getProviderToolNames(String name) {
        return getServerToolNames(name);
    }

    private void syncAdded(List<ToolDefinition> toolDefinitions, String provider) {
        try {
            toolSyncService.onToolsAdded(toolDefinitions, provider);
        } catch (Exception e) {
            log.warn("ToolRegistry: sync-added failed for provider '{}'", provider, e);
        }
    }

    private void syncRemoved(List<String> names) {
        try {
            toolSyncService.onToolsRemoved(names);
        } catch (Exception e) {
            log.warn("ToolRegistry: sync-removed failed for {}", names, e);
        }
    }

    public record RefreshResult(List<String> addedNames, List<String> removedNames) {}
}
