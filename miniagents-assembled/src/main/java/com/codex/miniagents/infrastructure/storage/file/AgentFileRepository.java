package com.codex.miniagents.infrastructure.storage.file;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.domain.model.agent.Agent;
import com.codex.miniagents.infrastructure.storage.repository.AgentRepository;

import org.springframework.stereotype.Repository;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

@Repository
public class AgentFileRepository implements AgentRepository {
    private final Path baseDir;

    private final FileJsonSupport jsonSupport;

    public AgentFileRepository(MiniAgentsProperties properties, FileJsonSupport jsonSupport) {
        this.baseDir = properties.getDataDir().resolve("agents");
        this.jsonSupport = jsonSupport;
    }

    private Path path(String agentId) {
        return baseDir.resolve(agentId + ".json");
    }

    @Override
    public void save(Agent agent) {
        jsonSupport.writeJsonAtomic(path(agent.getId()), agent);
    }

    @Override
    public Optional<Agent> findById(String agentId) {
        return jsonSupport.readJson(path(agentId), Agent.class);
    }

    @Override
    public List<String> listIds() {
        return jsonSupport.listJsonIds(baseDir);
    }

    @Override
    public List<String> listBySession(String sessionId) {
        List<String> result = new ArrayList<>();
        for (String id : listIds()) {
            findById(id).ifPresent(agent -> {
                if (sessionId.equals(agent.getSessionId())) {
                    result.add(agent.getId());
                }
            });
        }
        return result;
    }

    @Override
    public void delete(String agentId) {
        try {
            Files.deleteIfExists(path(agentId));
        } catch (IOException e) {
            throw new RuntimeException("Failed to delete agent: " + agentId, e);
        }
    }
}
