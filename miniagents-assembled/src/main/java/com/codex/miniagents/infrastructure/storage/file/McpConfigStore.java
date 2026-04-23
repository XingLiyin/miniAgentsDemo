package com.codex.miniagents.infrastructure.storage.file;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.tools.model.McpConfigData;

import org.springframework.stereotype.Repository;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

@Repository
public class McpConfigStore {
    private final Path baseDir;

    private final FileJsonSupport jsonSupport;

    public McpConfigStore(MiniAgentsProperties properties, FileJsonSupport jsonSupport) {
        this.baseDir = properties.getDataDir().resolve("mcp_configs");
        this.jsonSupport = jsonSupport;
    }

    private Path path(String name) {
        String safe = name.replace("/", "_").replace("\\", "_");
        return baseDir.resolve(safe + ".json");
    }

    public void save(McpConfigData mcpConfigData) {
        jsonSupport.writeJsonAtomic(path(mcpConfigData.getName()), mcpConfigData);
    }

    public Optional<McpConfigData> get(String name) {
        return jsonSupport.readJson(path(name), McpConfigData.class);
    }

    public boolean delete(String name) {
        Path p = path(name);
        try {
            return Files.deleteIfExists(p);
        } catch (IOException e) {
            throw new RuntimeException("Failed to delete MCP config: " + name, e);
        }
    }

    public List<McpConfigData> listAll() {
        List<McpConfigData> results = new ArrayList<>();
        for (String id : jsonSupport.listJsonIds(baseDir)) {
            get(id).ifPresent(results::add);
        }
        return results;
    }
}
