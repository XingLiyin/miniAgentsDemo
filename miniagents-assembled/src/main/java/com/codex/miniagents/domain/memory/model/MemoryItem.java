package com.codex.miniagents.domain.memory.model;

import com.codex.miniagents.dto.response.MessageResponse;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

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
@JsonIgnoreProperties(ignoreUnknown = true)
public class MemoryItem {
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

    public MessageResponse toResponse() {
        return MessageResponse.builder()
            .id(id)
            .sessionId(sessionId)
            .agentId(agentId)
            .role(role)
            .content(content)
            .taskId(taskId)
            .createdAt(createdAt)
            .toolCallId(toolCallId)
            .toolCalls(toolCalls)
            .build();
    }
}
