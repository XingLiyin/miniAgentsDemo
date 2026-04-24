package com.codex.miniagents.domain.service;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.domain.event.EventBus;
import com.codex.miniagents.domain.model.task.Task;
import com.codex.miniagents.domain.model.task.TaskStatus;
import com.codex.miniagents.domain.statemachine.TaskStateMachine;
import com.codex.miniagents.infrastructure.storage.repository.TaskRepository;
import com.codex.miniagents.common.SseBus;
import com.codex.miniagents.utils.IdUtils;

import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Map;
import java.time.Instant;
import java.util.LinkedHashMap;

@Service
public class TaskService {
    private final TaskRepository repository;

    private final TaskStateMachine stateMachine;

    private final EventBus eventBus;

    private final MiniAgentsProperties properties;

    public TaskService(TaskRepository repository, TaskStateMachine stateMachine, EventBus eventBus,
        MiniAgentsProperties properties) {
        this.repository = repository;
        this.stateMachine = stateMachine;
        this.eventBus = eventBus;
        this.properties = properties;
    }

    public Task create(String sessionId, String creatorAgentId, String title, String description,
        Map<String, Object> inputs) {
        return create(sessionId, creatorAgentId, creatorAgentId, "", title, description, inputs, null);
    }

    public Task create(String sessionId, String creatorAgentId, String assignedAgentId, String title,
        String description, Map<String, Object> inputs) {
        return create(sessionId, creatorAgentId, assignedAgentId, "", title, description, inputs, null);
    }

    public Task create(String sessionId, String creatorAgentId, String assignedAgentId,
        String userPrompt, String title, String description, Map<String, Object> settings) {
        return create(sessionId, creatorAgentId, assignedAgentId, userPrompt, title, description, settings, null);
    }

    public Task create(String sessionId, String creatorAgentId, String assignedAgentId,
        String userPrompt, String title, String description, Map<String, Object> settings, String parentTaskId) {
        Instant now = Instant.now();
        Task task = new Task();
        task.setId(IdUtils.newTaskId());
        task.setSessionId(sessionId);
        task.setCreatorAgentId(creatorAgentId);
        task.setAssignedAgentId(assignedAgentId);
        task.setUserPrompt(userPrompt);
        task.setTitle(title);
        task.setStatus(TaskStatus.PENDING);
        task.setDescription(description == null ? "" : description);
        task.setSettings(settings);
        task.setParentTaskId(parentTaskId);
        task.setRetryCount(0);
        task.setDagDeps(List.of());
        task.setCreatedAt(now);
        task.setUpdatedAt(now);
        repository.save(task);
        eventBus.publish("TASK_CREATED", java.util.Map.of("task_id", task.getId(), "session_id", sessionId));
        try {
            SseBus.getInstance().push(sessionId, java.util.Map.of(
                "type", "task_created",
                "task", task
            ));
        } catch (Exception ignored) {
        }
        return task;
    }

    public Task get(String taskId) {
        return repository.findById(taskId)
            .orElseThrow(() -> new AppException(ErrorCode.TASK_NOT_FOUND, "Task " + taskId + " not found"));
    }

    public void save(Task task) {
        task.setUpdatedAt(Instant.now());
        repository.save(task);
    }

    public Task transition(String taskId, TaskStatus toStatus) {
        Task task = get(taskId);
        stateMachine.validate(task.getStatus(), toStatus);
        task.setStatus(toStatus);
        save(task);

        switch (toStatus) {
            case ACTIVE -> eventBus.publish("TASK_STARTED",
                java.util.Map.of("task_id", taskId, "session_id", task.getSessionId()));
            case FINISHED -> eventBus.publish("TASK_FINISHED",
                java.util.Map.of("task_id", taskId, "session_id", task.getSessionId()));
            case FAILED ->
                eventBus.publish("TASK_FAILED", java.util.Map.of("task_id", taskId, "session_id", task.getSessionId()));
            default -> {
            }
        }
        try {
            SseBus.getInstance().push(task.getSessionId(), java.util.Map.of(
                "type", "task_updated",
                "task", task
            ));
        } catch (Exception ignored) {
        }
        return task;
    }

    public Task finish(String taskId, String result, Map<String, Object> outputs) {
        Task task = get(taskId);
        if (result != null) {
            task.setResult(result);
        }
        if (outputs != null) {
            task.setOutputs(outputs);
        }
        save(task);
        return transition(taskId, TaskStatus.FINISHED);
    }

    public Task finish(String taskId, String result) {
        return finish(taskId, result, null);
    }

    public Task fail(String taskId, String error) {
        Task task = get(taskId);
        task.setError(error);
        save(task);
        return transition(taskId, TaskStatus.FAILED);
    }

    public Task retry(String taskId) {
        Task task = get(taskId);
        task.setError(null);
        save(task);
        return transition(taskId, TaskStatus.PENDING);
    }

    public Task resume(String taskId) {
        return transition(taskId, TaskStatus.PENDING);
    }

    public Task reopen(String taskId) {
        Task task = get(taskId);
        task.setResult(null);
        task.setOutputs(Map.of());
        save(task);
        return transition(taskId, TaskStatus.PENDING);
    }

    public Task toBeObserved(String taskId) {
        return transition(taskId, TaskStatus.TO_BE_OBSERVED);
    }

    public List<Task> listBySession(String sessionId) {
        return repository.findBySessionId(sessionId);
    }

    public List<Task> listPending(String sessionId) {
        return repository.findBySessionAndStatus(sessionId, TaskStatus.PENDING);
    }

    public Task assignAgent(String taskId, String assignedAgentId) {
        Task task = get(taskId);
        task.setAssignedAgentId(assignedAgentId);
        save(task);
        return task;
    }

    public Task createPlanTask(String sessionId, String creatorAgentId, String title, String description) {
        Map<String, Object> settings = new LinkedHashMap<>();
        settings.put("use_subagent", true);
        settings.put("inherit_memory", true);
        settings.put("subagent_template", properties.getDefaultPlannerTemplateName());
        return create(sessionId, creatorAgentId, creatorAgentId, "", title, description, settings);
    }

    public int cancelPending(String sessionId) {
        int canceled = 0;
        for (Task task : listPending(sessionId)) {
            transition(task.getId(), TaskStatus.CANCELED);
            canceled += 1;
        }
        return canceled;
    }

    public void delete(String taskId) {
        get(taskId);
        repository.delete(taskId);
    }
}
