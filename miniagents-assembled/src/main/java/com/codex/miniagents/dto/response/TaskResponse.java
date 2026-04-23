package com.codex.miniagents.dto.response;

import com.codex.miniagents.domain.model.task.Task;
import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public class TaskResponse {
    private String id;

    private String sessionId;

    private String creatorAgentId;

    private String assignedAgentId;

    private String title;

    private String status;

    private String description;

    private Map<String, Object> settings;

    private String result;

    private Map<String, Object> outputs;

    private String error;

    @JsonFormat(shape = JsonFormat.Shape.STRING)
    private Instant createdAt;

    @JsonFormat(shape = JsonFormat.Shape.STRING)
    private Instant updatedAt;

    public static TaskResponse from(Task task) {
        return TaskResponse.builder()
            .id(task.getId())
            .sessionId(task.getSessionId())
            .creatorAgentId(task.getCreatorAgentId())
            .assignedAgentId(task.getAssignedAgentId())
            .title(task.getTitle())
            .status(task.getStatus() == null ? null : task.getStatus().name())
            .description(task.getDescription())
            .settings(task.getSettings() == null ? new LinkedHashMap<>() : new LinkedHashMap<>(task.getSettings()))
            .result(task.getResult())
            .outputs(task.getOutputs() == null ? new LinkedHashMap<>() : new LinkedHashMap<>(task.getOutputs()))
            .error(task.getError())
            .createdAt(task.getCreatedAt())
            .updatedAt(task.getUpdatedAt())
            .build();
    }
}
