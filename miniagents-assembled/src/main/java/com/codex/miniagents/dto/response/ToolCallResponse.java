package com.codex.miniagents.dto.response;

import com.codex.miniagents.domain.model.tool.ToolCall;
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
public class ToolCallResponse {
    private String id;

    private String sessionId;

    private String taskId;

    private String agentId;

    private String toolName;

    private String status;

    private Map<String, Object> arguments;

    private String result;

    private String error;

    @JsonFormat(shape = JsonFormat.Shape.STRING)
    private Instant startedAt;

    @JsonFormat(shape = JsonFormat.Shape.STRING)
    private Instant finishedAt;

    public static ToolCallResponse from(ToolCall call) {
        return ToolCallResponse.builder()
            .id(call.getId())
            .sessionId(call.getSessionId())
            .taskId(call.getTaskId())
            .agentId(call.getAgentId())
            .toolName(call.getToolName())
            .status(call.getStatus())
            .arguments(call.getArguments() == null ? new LinkedHashMap<>() : new LinkedHashMap<>(call.getArguments()))
            .result(call.getResult())
            .error(call.getError())
            .startedAt(call.getStartedAt())
            .finishedAt(call.getFinishedAt())
            .build();
    }
}
