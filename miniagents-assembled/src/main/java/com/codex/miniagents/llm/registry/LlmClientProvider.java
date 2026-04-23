package com.codex.miniagents.llm.registry;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.llm.ChatClient;

import org.springframework.stereotype.Component;

@Component
public class LlmClientProvider {
    private final LlmRegistry llmRegistry;

    private final MiniAgentsProperties properties;

    public LlmClientProvider(LlmRegistry llmRegistry, MiniAgentsProperties properties) {
        this.llmRegistry = llmRegistry;
        this.properties = properties;
    }

    public ChatClient get(String llmName) {
        String resolved = (llmName == null || llmName.isBlank()) ? properties.getAgentDefaultLlmName() : llmName;
        return llmRegistry.getClient(resolved);
    }

    public ChatClient get(String llmName, String llmModel) {
        String resolved = (llmName == null || llmName.isBlank()) ? properties.getAgentDefaultLlmName() : llmName;
        return llmRegistry.getClient(resolved, llmModel);
    }
}
