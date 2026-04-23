package com.codex.miniagents.domain.statemachine;

import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.domain.model.session.SessionStatus;
import org.springframework.stereotype.Component;

import java.util.EnumMap;
import java.util.EnumSet;
import java.util.Map;
import java.util.Set;

@Component
public class SessionStateMachine {

    private static final Map<SessionStatus, Set<SessionStatus>> TRANSITIONS = new EnumMap<>(SessionStatus.class);

    static {
        TRANSITIONS.put(SessionStatus.QUEUED, EnumSet.of(SessionStatus.RUNNING, SessionStatus.CANCELED));
        TRANSITIONS.put(SessionStatus.RUNNING, EnumSet.of(SessionStatus.SUCCEEDED, SessionStatus.FAILED, SessionStatus.CANCELED, SessionStatus.WAITING_INPUT));
        TRANSITIONS.put(SessionStatus.WAITING_INPUT, EnumSet.of(SessionStatus.RUNNING, SessionStatus.QUEUED, SessionStatus.CANCELED));
        TRANSITIONS.put(SessionStatus.SUCCEEDED, EnumSet.of(SessionStatus.QUEUED));
        TRANSITIONS.put(SessionStatus.FAILED, EnumSet.of(SessionStatus.QUEUED));
        TRANSITIONS.put(SessionStatus.CANCELED, EnumSet.noneOf(SessionStatus.class));
    }

    public void validate(SessionStatus from, SessionStatus to) {
        Set<SessionStatus> allowed = TRANSITIONS.getOrDefault(from, EnumSet.noneOf(SessionStatus.class));
        if (!allowed.contains(to)) {
            throw new AppException(
                ErrorCode.INVALID_STATE_TRANSITION,
                "Session: " + from + " -> " + to + " is not allowed"
            );
        }
    }
}
