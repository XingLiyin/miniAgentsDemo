package com.codex.miniagents.domain.service;

import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.domain.event.EventBus;
import com.codex.miniagents.domain.model.session.Session;
import com.codex.miniagents.domain.model.session.SessionStatus;
import com.codex.miniagents.domain.statemachine.SessionStateMachine;
import com.codex.miniagents.infrastructure.storage.repository.SessionRepository;
import com.codex.miniagents.common.SseBus;
import com.codex.miniagents.utils.IdUtils;

import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
public class SessionService {

    private final SessionRepository repository;
    private final SessionStateMachine stateMachine;
    private final EventBus eventBus;

    public SessionService(SessionRepository repository, SessionStateMachine stateMachine, EventBus eventBus) {
        this.repository = repository;
        this.stateMachine = stateMachine;
        this.eventBus = eventBus;
    }

    public Session create(String userPrompt, String templateId, Integer tokenBudget, Integer rootMaxTurns) {
        Instant now = Instant.now();
        Session session = new Session();
        session.setId(IdUtils.newSessionId());
        session.setUserPrompt(userPrompt);
        session.setGoal(userPrompt);
        session.setStatus(SessionStatus.QUEUED);
        session.setTemplateId(templateId);
        session.setRootAgentId(null);
        session.setTokenBudget(tokenBudget == null ? 200_000 : tokenBudget);
        session.setRootMaxTurns(rootMaxTurns == null ? 20 : rootMaxTurns);
        session.setCreatedAt(now);
        session.setUpdatedAt(now);
        repository.save(session);
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("session_id", session.getId());
        payload.put("user_prompt", userPrompt);
        eventBus.publish("SESSION_CREATED", payload);
        return session;
    }

    public Session get(String sessionId) {
        return repository.findById(sessionId)
            .orElseThrow(() -> new AppException(ErrorCode.SESSION_NOT_FOUND, "Session " + sessionId + " not found"));
    }

    public void save(Session session) {
        session.setUpdatedAt(Instant.now());
        repository.save(session);
    }

    public Session transition(String sessionId, SessionStatus toStatus) {
        Session session = get(sessionId);
        stateMachine.validate(session.getStatus(), toStatus);
        session.setStatus(toStatus);
        save(session);

        switch (toStatus) {
            case RUNNING -> eventBus.publish("SESSION_STARTED", java.util.Map.of("session_id", sessionId));
            case SUCCEEDED -> eventBus.publish("SESSION_SUCCEEDED", java.util.Map.of("session_id", sessionId));
            case FAILED -> eventBus.publish("SESSION_FAILED", java.util.Map.of("session_id", sessionId));
            case CANCELED -> eventBus.publish("SESSION_CANCELED", java.util.Map.of("session_id", sessionId));
            default -> {
            }
        }
        try {
            java.util.Map<String, Object> sseEvent = new java.util.LinkedHashMap<>();
            sseEvent.put("type", "session_update");
            sseEvent.put("session_id", sessionId);
            sseEvent.put("status", toStatus.name());
            sseEvent.put("token_used", session.getTokenUsed());
            SseBus.getInstance().push(sessionId, sseEvent);
            if (toStatus.isTerminal()) {
                SseBus.getInstance().push(sessionId, java.util.Map.of(
                    "type", "done",
                    "final_status", toStatus.name()
                ));
            }
        } catch (Exception ignored) {
        }
        return session;
    }

    public void setRootAgent(String sessionId, String agentId) {
        Session session = get(sessionId);
        session.bindRootAgent(agentId);
        save(session);
    }

    public Session addTokens(String sessionId, int tokens) {
        Session session = get(sessionId);
        session.addTokens(tokens);
        save(session);
        if (session.getTokenUsed() >= session.getTokenBudget()) {
            throw new AppException(
                ErrorCode.TOKEN_BUDGET_EXCEEDED,
                "Session " + sessionId + " token budget exhausted (" + session.getTokenUsed() + "/" + session.getTokenBudget() + ")"
            );
        }
        return session;
    }

    public List<String> listIds() {
        return repository.listIds();
    }

    public void delete(String sessionId) {
        repository.delete(sessionId);
    }
}
