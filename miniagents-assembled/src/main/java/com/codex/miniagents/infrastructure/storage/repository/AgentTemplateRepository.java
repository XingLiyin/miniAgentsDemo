package com.codex.miniagents.infrastructure.storage.repository;

import com.codex.miniagents.domain.model.AgentTemplate;

import java.util.List;
import java.util.Optional;

public interface AgentTemplateRepository {
    void save(AgentTemplate template);

    Optional<AgentTemplate> findById(String id);

    Optional<AgentTemplate> findByName(String name);

    boolean delete(String templateId);

    List<String> listIds();

    List<AgentTemplate> findAll();
}
