package com.codex.miniagents.infrastructure.storage.repository;

import com.codex.miniagents.domain.model.task.Task;
import com.codex.miniagents.domain.model.task.TaskStatus;

import java.util.List;
import java.util.Optional;

public interface TaskRepository {
    void save(Task task);

    Optional<Task> findById(String taskId);

    List<String> listIds();

    List<Task> findBySessionId(String sessionId);

    List<Task> findBySessionAndStatus(String sessionId, TaskStatus status);

    void delete(String taskId);
}
