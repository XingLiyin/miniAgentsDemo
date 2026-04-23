package com.codex.miniagents.controller;

import com.codex.miniagents.dto.response.TaskResponse;
import com.codex.miniagents.domain.service.TaskService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/tasks")
@Slf4j
public class TaskController {

    private final TaskService taskService;

    public TaskController(TaskService taskService) {
        this.taskService = taskService;
    }

    @GetMapping("/{taskId}")
    public TaskResponse getTask(@PathVariable String taskId) {
        log.info("Fetching task: taskId='{}'", taskId);
        return TaskResponse.from(taskService.get(taskId));
    }
}
