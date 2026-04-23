package com.codex.miniagents.infrastructure.storage.file;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.domain.model.AgentTemplate;
import com.codex.miniagents.infrastructure.storage.repository.AgentTemplateRepository;

import org.springframework.stereotype.Repository;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

@Repository
public class AgentTemplateFileRepository implements AgentTemplateRepository {
    private final Path baseDir;

    private final FileJsonSupport jsonSupport;

    public AgentTemplateFileRepository(MiniAgentsProperties properties, FileJsonSupport jsonSupport) {
        this.baseDir = properties.getDataDir().resolve("agent_templates");
        this.jsonSupport = jsonSupport;
    }

    private Path path(String id) {
        return baseDir.resolve(id + ".json");
    }

    @Override
    public void save(AgentTemplate template) {
        jsonSupport.writeJsonAtomic(path(template.getId()), template);
    }

    @Override
    public Optional<AgentTemplate> findById(String id) {
        return jsonSupport.readJson(path(id), AgentTemplate.class);
    }

    @Override
    public Optional<AgentTemplate> findByName(String name) {
        for (AgentTemplate template : findAll()) {
            if (name.equals(template.getName())) {
                return Optional.of(template);
            }
        }
        return Optional.empty();
    }

    @Override
    public boolean delete(String templateId) {
        Path path = path(templateId);
        try {
            if (Files.exists(path)) {
                Files.delete(path);
                return true;
            }
            return false;
        } catch (Exception e) {
            throw new RuntimeException("删除 AgentTemplate 失败: " + templateId, e);
        }
    }

    @Override
    public List<String> listIds() {
        return jsonSupport.listJsonIds(baseDir);
    }

    @Override
    public List<AgentTemplate> findAll() {
        List<AgentTemplate> results = new ArrayList<>();
        for (String id : listIds()) {
            findById(id).ifPresent(results::add);
        }
        return results;
    }
}
