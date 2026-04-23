package com.codex.miniagents.dto.response;

import com.codex.miniagents.domain.memory.model.MemoryItem;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public class MessageResponse {
    private String id;

    private String sessionId;

    private String agentId;

    private String role;

    private String content;

    private String taskId;

    private String createdAt;

    private String toolCallId;

    @Builder.Default
    private List<Map<String, Object>> toolCalls = new ArrayList<>();

    public static MessageResponse from(MemoryItem item) {
        return MessageResponse.builder()
            .id(item.getId())
            .sessionId(item.getSessionId())
            .agentId(item.getAgentId())
            .role(item.getRole())
            .content(item.getContent())
            .taskId(item.getTaskId())
            .createdAt(item.getCreatedAt())
            .toolCallId(item.getToolCallId())
            .toolCalls(item.getToolCalls())
            .build();
    }
}
