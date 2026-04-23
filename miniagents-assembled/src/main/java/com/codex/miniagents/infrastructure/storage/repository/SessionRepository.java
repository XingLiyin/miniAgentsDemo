package com.codex.miniagents.infrastructure.storage.repository;

import com.codex.miniagents.domain.model.session.Session;

import java.util.List;
import java.util.Optional;

public interface SessionRepository {
    void save(Session session);

    Optional<Session> findById(String sessionId);

    List<String> listIds();

    void delete(String sessionId);
}
