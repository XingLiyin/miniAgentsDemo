package com.codex.miniagents.infrastructure.storage.file;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.skills.model.RemoteSkillSourceConfig;

import org.springframework.stereotype.Repository;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

@Repository
public class RemoteSkillSourceStore {
    private final Path baseDir;

    private final FileJsonSupport jsonSupport;

    public RemoteSkillSourceStore(MiniAgentsProperties properties, FileJsonSupport jsonSupport) {
        this.baseDir = properties.getDataDir().resolve("remote_skill_sources");
        this.jsonSupport = jsonSupport;
    }

    private Path path(String sourceName) {
        String safe = sourceName.replace("/", "_").replace("\\", "_");
        return baseDir.resolve(safe + ".json");
    }

    public void save(RemoteSkillSourceConfig config) {
        jsonSupport.writeJsonAtomic(path(config.getSourceName()), config);
    }

    public Optional<RemoteSkillSourceConfig> get(String sourceName) {
        return jsonSupport.readJson(path(sourceName), RemoteSkillSourceConfig.class);
    }

    public boolean delete(String sourceName) {
        Path p = path(sourceName);
        try {
            return Files.deleteIfExists(p);
        } catch (IOException e) {
            throw new RuntimeException("Failed to delete remote skill source: " + sourceName, e);
        }
    }

    public List<RemoteSkillSourceConfig> listAll() {
        List<RemoteSkillSourceConfig> results = new ArrayList<>();
        for (String sourceName : jsonSupport.listJsonIds(baseDir)) {
            get(sourceName).ifPresent(results::add);
        }
        return results;
    }
}
