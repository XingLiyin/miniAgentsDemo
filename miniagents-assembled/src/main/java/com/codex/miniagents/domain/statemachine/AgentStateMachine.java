package com.codex.miniagents.domain.statemachine;

import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.domain.model.agent.AgentStatus;
import org.springframework.stereotype.Component;

import java.util.EnumMap;
import java.util.EnumSet;
import java.util.Map;
import java.util.Set;

@Component
public class AgentStateMachine {

    private static final Map<AgentStatus, Set<AgentStatus>> TRANSITIONS = new EnumMap<>(AgentStatus.class);

    static {
        TRANSITIONS.put(AgentStatus.IDLE, EnumSet.of(AgentStatus.RUNNING));
        TRANSITIONS.put(AgentStatus.RUNNING, EnumSet.of(AgentStatus.WAITING, AgentStatus.FINISHED, AgentStatus.FAILED));
        TRANSITIONS.put(AgentStatus.WAITING, EnumSet.of(AgentStatus.RUNNING, AgentStatus.FAILED));
        TRANSITIONS.put(AgentStatus.FINISHED, EnumSet.noneOf(AgentStatus.class));
        TRANSITIONS.put(AgentStatus.FAILED, EnumSet.noneOf(AgentStatus.class));
    }

    public void validate(AgentStatus from, AgentStatus to) {
        Set<AgentStatus> allowed = TRANSITIONS.getOrDefault(from, EnumSet.noneOf(AgentStatus.class));
        if (!allowed.contains(to)) {
            throw new AppException(
                ErrorCode.INVALID_STATE_TRANSITION,
                "Agent: " + from + " -> " + to + " is not allowed"
            );
        }
    }
}
