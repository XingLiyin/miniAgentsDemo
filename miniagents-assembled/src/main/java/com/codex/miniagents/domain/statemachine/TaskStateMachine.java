package com.codex.miniagents.domain.statemachine;

import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.domain.model.task.TaskStatus;
import org.springframework.stereotype.Component;

import java.util.EnumMap;
import java.util.EnumSet;
import java.util.Map;
import java.util.Set;

@Component
public class TaskStateMachine {

    private static final Map<TaskStatus, Set<TaskStatus>> TRANSITIONS = new EnumMap<>(TaskStatus.class);

    static {
        TRANSITIONS.put(TaskStatus.PENDING,
            EnumSet.of(TaskStatus.ACTIVE, TaskStatus.CANCELED, TaskStatus.FINISHED, TaskStatus.TO_BE_OBSERVED));
        TRANSITIONS.put(TaskStatus.ACTIVE,
            EnumSet.of(TaskStatus.FINISHED, TaskStatus.FAILED, TaskStatus.CANCELED, TaskStatus.SUSPENDED,
                TaskStatus.TO_BE_OBSERVED));
        TRANSITIONS.put(TaskStatus.SUSPENDED,
            EnumSet.of(TaskStatus.PENDING, TaskStatus.ACTIVE, TaskStatus.FAILED, TaskStatus.CANCELED));
        TRANSITIONS.put(TaskStatus.TO_BE_OBSERVED, EnumSet.of(TaskStatus.FINISHED, TaskStatus.FAILED));
        TRANSITIONS.put(TaskStatus.FINISHED, EnumSet.of(TaskStatus.PENDING));
        TRANSITIONS.put(TaskStatus.FAILED, EnumSet.of(TaskStatus.PENDING));
        TRANSITIONS.put(TaskStatus.CANCELED, EnumSet.noneOf(TaskStatus.class));
    }

    public void validate(TaskStatus from, TaskStatus to) {
        Set<TaskStatus> allowed = TRANSITIONS.getOrDefault(from, EnumSet.noneOf(TaskStatus.class));
        if (!allowed.contains(to)) {
            throw new AppException(
                ErrorCode.INVALID_STATE_TRANSITION,
                "Task: " + from + " -> " + to + " is not allowed"
            );
        }
    }
}
