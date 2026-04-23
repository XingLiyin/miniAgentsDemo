package com.codex.miniagents.infrastructure.storage.file;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.domain.model.task.Task;
import com.codex.miniagents.domain.model.task.TaskStatus;
import com.codex.miniagents.infrastructure.storage.repository.TaskRepository;

import org.springframework.stereotype.Repository;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;

@Repository
public class TaskFileRepository implements TaskRepository {
    private final Path baseDir;

    private final FileJsonSupport jsonSupport;

    public TaskFileRepository(MiniAgentsProperties properties, FileJsonSupport jsonSupport) {
        this.baseDir = properties.getDataDir().resolve("tasks");
        this.jsonSupport = jsonSupport;
    }

    private Path path(String taskId) {
        return baseDir.resolve(taskId + ".json");
    }

    @Override
    public void save(Task task) {
        jsonSupport.writeJsonAtomic(path(task.getId()), task);
    }

    @Override
    public Optional<Task> findById(String taskId) {
        return jsonSupport.readJson(path(taskId), Task.class);
    }

    @Override
    public List<String> listIds() {
        return jsonSupport.listJsonIds(baseDir);
    }

    @Override
    public List<Task> findBySessionId(String sessionId) {
        List<Task> tasks = new ArrayList<>();
        for (String id : listIds()) {
            findById(id).ifPresent(task -> {
                if (sessionId.equals(task.getSessionId())) {
                    tasks.add(task);
                }
            });
        }
        return tasks;
    }

    @Override
    public List<Task> findBySessionAndStatus(String sessionId, TaskStatus status) {
        List<Task> tasks = new ArrayList<>();
        for (Task task : findBySessionId(sessionId)) {
            if (task.getStatus() == status) {
                tasks.add(task);
            }
        }
        tasks.sort(Comparator.comparing(Task::getCreatedAt, Comparator.nullsLast(Comparator.naturalOrder())));
        return tasks;
    }

    @Override
    public void delete(String taskId) {
        try {
            Files.deleteIfExists(path(taskId));
        } catch (IOException e) {
            throw new RuntimeException("Failed to delete task: " + taskId, e);
        }
    }
}
