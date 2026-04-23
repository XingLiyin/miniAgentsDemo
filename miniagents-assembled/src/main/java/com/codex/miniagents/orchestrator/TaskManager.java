package com.codex.miniagents.orchestrator;

import com.codex.miniagents.domain.model.session.Session;
import com.codex.miniagents.domain.model.task.Task;
import com.codex.miniagents.domain.model.task.TaskStatus;
import com.codex.miniagents.domain.service.SessionService;
import com.codex.miniagents.domain.service.TaskService;
import org.springframework.stereotype.Component;

import java.util.Map;

@Component
public class TaskManager {
    private final TaskService taskService;

    private final SessionService sessionService;

    public TaskManager(TaskService taskService, SessionService sessionService) {
        this.taskService = taskService;
        this.sessionService = sessionService;
    }

    public Task activate(String taskId) {
        return taskService.transition(taskId, TaskStatus.ACTIVE);
    }

    public Task complete(String taskId, String result, Map<String, Object> outputs) {
        Task task = taskService.finish(taskId, result, outputs);
        resetFailureCounter(task.getSessionId());
        return task;
    }

    public Task failTask(String taskId, String error) {
        Task task = taskService.fail(taskId, error);
        incrementFailureCounter(task.getSessionId());
        return task;
    }

    public Task nextTask(String sessionId) {
        java.util.List<Task> pending = taskService.listPending(sessionId);
        return pending.isEmpty() ? null : pending.get(0);
    }

    public Task createReplan(String sessionId, String agentId) {
        return taskService.createPlanTask(
            sessionId,
            agentId,
            "Re-plan: evaluate next steps",
            "All current tasks completed. Re-evaluate the goal and plan next steps if needed.");
    }

    public void recordSuccess(String sessionId) {
        resetFailureCounter(sessionId);
    }

    public void recordFailure(String sessionId) {
        incrementFailureCounter(sessionId);
    }

    private void resetFailureCounter(String sessionId) {
        Session session = sessionService.get(sessionId);
        if (session.getFailureCounter() > 0) {
            session.resetFailureCounter();
            sessionService.save(session);
        }
    }

    private void incrementFailureCounter(String sessionId) {
        Session session = sessionService.get(sessionId);
        session.incrementFailureCounter();
        sessionService.save(session);
    }
}
