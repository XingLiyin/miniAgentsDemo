package com.codex.miniagents.llm.registry;

import com.codex.miniagents.llm.adapter.AnthropicAdapter;
import com.codex.miniagents.llm.adapter.LlmAdapter;
import com.codex.miniagents.llm.adapter.OpenAiAdapter;
import com.codex.miniagents.llm.transport.Transport;

import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

@Component
public class ProviderRegistry {
    private final Transport transport;

    private final Map<String, LlmAdapter> providers = new LinkedHashMap<>();

    private final Set<String> openaiNames = new LinkedHashSet<>();

    private final Set<String> anthropicNames = new LinkedHashSet<>();

    public ProviderRegistry(Transport transport) {
        this.transport = transport;
    }

    public void registerOpenAi(String name, String apiKey, String baseUrl, int timeoutSec) {
        String finalBaseUrl = (baseUrl == null || baseUrl.isBlank()) ? "https://api.openai.com" : baseUrl;
        if (providers.containsKey(name)) {
            throw new IllegalArgumentException("Provider already exists: " + name);
        }
        providers.put(name, new OpenAiAdapter(apiKey, finalBaseUrl, transport, timeoutSec));
        openaiNames.add(name);
    }

    public void registerAnthropic(String name, String apiKey, String baseUrl, int timeoutSec) {
        String finalBaseUrl = (baseUrl == null || baseUrl.isBlank()) ? "https://api.anthropic.com" : baseUrl;
        if (providers.containsKey(name)) {
            throw new IllegalArgumentException("Provider already exists: " + name);
        }
        providers.put(name, new AnthropicAdapter(apiKey, finalBaseUrl, transport, timeoutSec));
        anthropicNames.add(name);
    }

    public LlmAdapter get(String name) {
        LlmAdapter adapter = providers.get(name);
        if (adapter == null) {
            throw new IllegalArgumentException("Provider not found: " + name);
        }
        return adapter;
    }

    public List<ProviderInfo> listProviders() {
        List<ProviderInfo> result = new ArrayList<>();
        for (String name : providers.keySet()) {
            String style;
            if (openaiNames.contains(name)) {
                style = "openai";
            } else if (anthropicNames.contains(name)) {
                style = "anthropic";
            } else {
                style = "custom";
            }
            result.add(new ProviderInfo(name, style));
        }
        return result;
    }

    public record ProviderInfo(String name, String style) {}
}
