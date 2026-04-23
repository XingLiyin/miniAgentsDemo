package com.codex.miniagents.llm.registry;

import com.codex.miniagents.infrastructure.storage.file.LLMConfigStore;
import com.codex.miniagents.llm.ChatClient;
import com.codex.miniagents.llm.model.LlmProviderConfig;

import lombok.extern.slf4j.Slf4j;

import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

@Slf4j
@Component
public class LlmRegistry {
    public static final Set<String> SUPPORTED_LLM_STYLES = Set.of("openai", "anthropic");

    private final ProviderRegistry providerRegistry;

    private final LLMConfigStore store;

    private final Map<String, LlmProviderConfig> configs = new LinkedHashMap<>();

    public LlmRegistry(ProviderRegistry providerRegistry, LLMConfigStore store) {
        this.providerRegistry = providerRegistry;
        this.store = store;
    }

    public synchronized void register(LlmProviderConfig config, boolean persist) {
        if (configs.containsKey(config.getName())) {
            throw new IllegalArgumentException("LLM 已存在: " + config.getName());
        }

        if ((config.getDefaultModel() == null || config.getDefaultModel().isBlank())
            && config.getModels() != null && !config.getModels().isEmpty()) {
            config.setDefaultModel(config.getModels().get(0));
        }

        if ("openai".equals(config.getStyle())) {
            providerRegistry.registerOpenAi(config.getName(), config.getApiKey(), config.getBaseUrl(),
                config.getTimeoutSec());
        } else if ("anthropic".equals(config.getStyle())) {
            providerRegistry.registerAnthropic(config.getName(), config.getApiKey(), config.getBaseUrl(),
                config.getTimeoutSec());
        } else {
            throw new IllegalArgumentException("不支持的 LLM 风格: " + config.getStyle());
        }

        configs.put(config.getName(), config);

        if (persist) {
            store.save(config);
        }
    }

    public synchronized ChatClient getClient(String name) {
        return getClient(name, null);
    }

    public synchronized ChatClient getClient(String name, String model) {
        LlmProviderConfig cfg = configs.get(name);
        if (cfg == null) {
            throw new IllegalArgumentException("未注册 LLM: " + name);
        }
        String resolvedModel = (model == null || model.isBlank()) ? cfg.getDefaultModel() : model;
        if (resolvedModel == null || resolvedModel.isBlank()) {
            throw new IllegalArgumentException("Provider '" + name + "' 无可用模型");
        }
        return new ChatClient(providerRegistry.get(name), resolvedModel, cfg.getMaxTokens());
    }

    public synchronized LlmProviderConfig getConfig(String name) {
        LlmProviderConfig cfg = configs.get(name);
        if (cfg == null) {
            throw new IllegalArgumentException("未注册 LLM: " + name);
        }
        return cfg;
    }

    public synchronized List<LlmProviderConfig> listConfigs() {
        return new ArrayList<>(configs.values());
    }

    public synchronized LlmProviderConfig addModel(String name, String model) {
        LlmProviderConfig cfg = getConfig(name);
        if (cfg.getModels() == null) {
            cfg.setModels(new ArrayList<>());
        }
        if (!cfg.getModels().contains(model)) {
            cfg.getModels().add(model);
        }
        if (cfg.getDefaultModel() == null || cfg.getDefaultModel().isBlank()) {
            cfg.setDefaultModel(model);
        }
        store.save(cfg);
        return cfg;
    }

    public synchronized LlmProviderConfig removeModel(String name, String model) {
        LlmProviderConfig cfg = getConfig(name);
        if (cfg.getModels() != null) {
            cfg.getModels().remove(model);
        }
        if (model.equals(cfg.getDefaultModel())) {
            cfg.setDefaultModel(cfg.getModels() == null || cfg.getModels().isEmpty() ? "" : cfg.getModels().get(0));
        }
        store.save(cfg);
        return cfg;
    }

    public synchronized LlmProviderConfig setDefaultModel(String name, String model) {
        LlmProviderConfig cfg = getConfig(name);
        if (cfg.getModels() == null || !cfg.getModels().contains(model)) {
            throw new IllegalArgumentException("模型 '" + model + "' 不在 provider '" + name + "' 的列表中");
        }
        cfg.setDefaultModel(model);
        store.save(cfg);
        return cfg;
    }

    public synchronized boolean isRegistered(String name) {
        return configs.containsKey(name);
    }

    public synchronized void delete(String name) {
        if (!configs.containsKey(name)) {
            throw new IllegalArgumentException("未注册 LLM: " + name);
        }
        configs.remove(name);
        store.delete(name);
    }

    public synchronized int loadFromStore() {
        int count = 0;
        for (LlmProviderConfig data : store.listAll()) {
            String name = data.getName();
            if (name == null || name.isBlank() || configs.containsKey(name)) {
                continue;
            }
            try {
                List<String> models = data.getModels() == null ? new ArrayList<>() : new ArrayList<>(new LinkedHashSet<>(data.getModels()));
                if (models.isEmpty() && data.getDefaultModel() != null && !data.getDefaultModel().isBlank()) {
                    models.add(data.getDefaultModel());
                }
                LlmProviderConfig config = new LlmProviderConfig(data.getName(), data.getStyle(), data.getApiKey(),
                    data.getBaseUrl(), models, data.getDefaultModel(), data.getTimeoutSec(), data.getMaxTokens());
                if ((config.getDefaultModel() == null || config.getDefaultModel().isBlank()) && !models.isEmpty()) {
                    config.setDefaultModel(models.get(0));
                }
                register(config, false);
                count++;
                log.info("LlmRegistry: auto-loaded provider '{}' ({}/{})", name, config.getStyle(), config.getDefaultModel());
            } catch (Exception e) {
                log.error("LlmRegistry: failed to load provider '{}'", name, e);
            }
        }
        return count;
    }
}
