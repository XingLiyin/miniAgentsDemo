package com.codex.miniagents.dto.response;

import com.codex.miniagents.domain.model.session.Session;
import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.Instant;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public class SessionResponse {
    private String id;

    private String userPrompt;

    private String status;

    private String templateId;

    private String rootAgentId;

    private Integer tokenBudget;

    private Integer tokenUsed;

    private Integer rootMaxTurns;

    private Integer failureCounter;

    @JsonFormat(shape = JsonFormat.Shape.STRING)
    private Instant createdAt;

    @JsonFormat(shape = JsonFormat.Shape.STRING)
    private Instant updatedAt;

    public static SessionResponse from(Session session) {
        return SessionResponse.builder()
            .id(session.getId())
            .userPrompt(session.getUserPrompt())
            .status(session.getStatus() == null ? null : session.getStatus().name())
            .templateId(session.getTemplateId())
            .rootAgentId(session.getRootAgentId())
            .tokenBudget(session.getTokenBudget())
            .tokenUsed(session.getTokenUsed())
            .rootMaxTurns(session.getRootMaxTurns())
            .failureCounter(session.getFailureCounter())
            .createdAt(session.getCreatedAt())
            .updatedAt(session.getUpdatedAt())
            .build();
    }
}
