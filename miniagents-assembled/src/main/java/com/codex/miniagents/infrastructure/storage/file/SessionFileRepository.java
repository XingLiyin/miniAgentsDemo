package com.codex.miniagents.infrastructure.storage.file;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.domain.model.session.Session;
import com.codex.miniagents.infrastructure.storage.repository.SessionRepository;

import org.springframework.stereotype.Repository;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Optional;

@Repository
public class SessionFileRepository implements SessionRepository {
    private final Path baseDir;

    private final FileJsonSupport jsonSupport;

    public SessionFileRepository(MiniAgentsProperties properties, FileJsonSupport jsonSupport) {
        this.baseDir = properties.getDataDir().resolve("sessions");
        this.jsonSupport = jsonSupport;
    }

    private Path path(String sessionId) {
        return baseDir.resolve(sessionId + ".json");
    }

    @Override
    public void save(Session session) {
        jsonSupport.writeJsonAtomic(path(session.getId()), session);
    }

    @Override
    public Optional<Session> findById(String sessionId) {
        return jsonSupport.readJson(path(sessionId), Session.class);
    }

    @Override
    public List<String> listIds() {
        return jsonSupport.listJsonIds(baseDir);
    }

    @Override
    public void delete(String sessionId) {
        try {
            Files.deleteIfExists(path(sessionId));
        } catch (IOException e) {
            throw new RuntimeException("Failed to delete session: " + sessionId, e);
        }
    }
}
