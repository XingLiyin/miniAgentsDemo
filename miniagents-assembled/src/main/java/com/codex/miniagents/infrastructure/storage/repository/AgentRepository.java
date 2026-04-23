package com.codex.miniagents.infrastructure.storage.repository;

import com.codex.miniagents.domain.model.agent.Agent;

import java.util.List;
import java.util.Optional;

public interface AgentRepository {
    void save(Agent agent);

    Optional<Agent> findById(String agentId);

    List<String> listIds();

    List<String> listBySession(String sessionId);

    void delete(String agentId);
}
