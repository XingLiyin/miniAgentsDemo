package com.codex.miniagents.config;

import com.codex.miniagents.agenttemplate.AgentLoader;
import com.codex.miniagents.agenttemplate.definition.AgentDefContent;
import com.codex.miniagents.agenttemplate.definition.AgentDefMetadata;
import com.codex.miniagents.domain.service.AgentTemplateService;
import com.codex.miniagents.llm.registry.LlmRegistry;
import com.codex.miniagents.tools.mcp.McpService;

import jakarta.annotation.PostConstruct;
import lombok.extern.slf4j.Slf4j;

import org.springframework.stereotype.Component;

import java.nio.file.Path;
import java.util.List;

@Slf4j
@Component
public class StartupInitializer {
    private final LlmRegistry llmRegistry;

    private final McpService mcpService;

    private final AgentTemplateService agentTemplateService;

    private final MiniAgentsProperties properties;

    public StartupInitializer(LlmRegistry llmRegistry, McpService mcpService, AgentTemplateService agentTemplateService,
        MiniAgentsProperties properties) {
        this.llmRegistry = llmRegistry;
        this.mcpService = mcpService;
        this.agentTemplateService = agentTemplateService;
        this.properties = properties;
    }

    @PostConstruct
    public void init() {
        log.info("StartupInitializer: begin startup bootstrap");
        restoreLlmProviders();
        restoreMcpServers();
        loadAgentTemplates();
        log.info("StartupInitializer: startup bootstrap finished");
    }

    private void restoreLlmProviders() {
        try {
            int loaded = llmRegistry.loadFromStore();
            if (loaded > 0) {
                log.info("StartupInitializer: restored {} LLM provider(s)", loaded);
            }
        } catch (Exception e) {
            log.warn("StartupInitializer: failed to restore LLM providers", e);
        }
    }

    private void restoreMcpServers() {
        try {
            mcpService.restoreAll();
        } catch (Exception e) {
            log.warn("StartupInitializer: failed to restore MCP servers", e);
        }
    }

    private void loadAgentTemplates() {
        Path agentsDir = properties.getAgentsDir();
        log.info("StartupInitializer: scanning agent templates from '{}'", agentsDir);
        AgentLoader loader = new AgentLoader();
        List<AgentDefMetadata> metadataList = loader.scan(agentsDir);
        for (AgentDefMetadata metadata : metadataList) {
            try {
                AgentDefContent content = loader.loadContent(metadata.getAgentDir());
                agentTemplateService.upsertByName(metadata.getName(), metadata.getVersion(), metadata.getDescription(),
                    metadata.getActToolSpec() == null ? List.of() : metadata.getActToolSpec().effective(),
                    metadata.getObserveToolSpec() == null ? List.of() : metadata.getObserveToolSpec().effective(),
                    metadata.getMcpActServers(),
                    metadata.getMcpObserveServers(), metadata.getAgentDir().toString());
            } catch (Exception e) {
                log.warn("StartupInitializer: failed to load template '{}'", metadata.getName(), e);
            }
        }
        if (!metadataList.isEmpty()) {
            log.info("StartupInitializer: loaded {} agent template(s)", metadataList.size());
        } else {
            log.info("StartupInitializer: no agent templates found under '{}'", agentsDir);
        }
    }
}
