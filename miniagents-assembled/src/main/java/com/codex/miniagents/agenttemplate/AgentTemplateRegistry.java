package com.codex.miniagents.agenttemplate;

import com.codex.miniagents.agenttemplate.definition.AgentDefContent;
import com.codex.miniagents.agenttemplate.definition.AgentDefMetadata;
import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.domain.service.AgentTemplateService;

import jakarta.annotation.PostConstruct;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Component
public class AgentTemplateRegistry {
    private static final Logger log = LoggerFactory.getLogger(AgentTemplateRegistry.class);

    private final Map<String, AgentDefMetadata> agents = new LinkedHashMap<>();

    private final AgentLoader loader;

    private final AgentTemplateService templateService;

    private final MiniAgentsProperties properties;

    public AgentTemplateRegistry(
        AgentLoader loader,
        AgentTemplateService templateService,
        MiniAgentsProperties properties
    ) {
        this.loader = loader;
        this.templateService = templateService;
        this.properties = properties;
    }

    @PostConstruct
    public void init() {
        loadFromDir(properties.getAgentsDir());
    }

    public void loadFromDir(Path agentsDir) {
        for (AgentDefMetadata metadata : loader.scan(agentsDir)) {
            agents.put(metadata.getName(), metadata);

            try {
                AgentDefContent content = loader.loadContent(metadata.getAgentDir());
                templateService.upsertByName(
                    metadata.getName(),
                    metadata.getVersion(),
                    metadata.getDescription(),
                    metadata.getActToolSpec() == null ? List.of() : metadata.getActToolSpec().effective(),
                    metadata.getObserveToolSpec() == null ? List.of() : metadata.getObserveToolSpec().effective(),
                    metadata.getMcpActServers(),
                    metadata.getMcpObserveServers(),
                    String.valueOf(metadata.getAgentDir())
                );
                log.debug("AgentTemplateRegistry: upserted template '{}'", metadata.getName());
            } catch (Exception e) {
                log.warn(
                    "AgentTemplateRegistry: failed to load content for '{}': {}",
                    metadata.getName(),
                    e.getMessage()
                );
            }
        }

        log.info("AgentTemplateRegistry: loaded {} agent(s) from '{}'", agents.size(), agentsDir);
    }

    public AgentDefMetadata getMetadata(String name) {
        return agents.get(name);
    }

    public List<AgentDefMetadata> listAll() {
        return new ArrayList<>(agents.values());
    }

    public AgentDefContent loadContent(String name) {
        AgentDefMetadata meta = agents.get(name);
        if (meta == null) {
            log.warn("AgentTemplateRegistry: agent '{}' not found", name);
            return null;
        }

        try {
            return loader.loadContent(meta.getAgentDir());
        } catch (Exception e) {
            log.warn("AgentTemplateRegistry: failed to load content for '{}': {}", name, e.getMessage());
            return null;
        }
    }
}
