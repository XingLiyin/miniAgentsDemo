package com.codex.miniagents.dto.response;

import com.codex.miniagents.domain.memory.model.MemorySummary;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public class SummaryResponse {
    private String sessionId;

    private String agentId;

    private String summaryText;

    private Integer coveredUpTo;

    private String createdAt;

    public static SummaryResponse from(MemorySummary summary) {
        return SummaryResponse.builder()
            .sessionId(summary.getSessionId())
            .agentId(summary.getAgentId())
            .summaryText(summary.getSummaryText())
            .coveredUpTo(summary.getCoveredUpTo())
            .createdAt(summary.getCreatedAt())
            .build();
    }
}
