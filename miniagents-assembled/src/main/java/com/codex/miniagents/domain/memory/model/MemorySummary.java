package com.codex.miniagents.domain.memory.model;

import com.codex.miniagents.dto.response.SummaryResponse;

import lombok.Getter;
import lombok.Setter;

@Getter
@Setter
public class MemorySummary {
    private String sessionId;

    private String agentId;

    private String summaryText;

    private int coveredUpTo;

    private String createdAt;

    public SummaryResponse toResponse() {
        return SummaryResponse.builder()
            .sessionId(sessionId)
            .agentId(agentId)
            .summaryText(summaryText)
            .coveredUpTo(coveredUpTo)
            .createdAt(createdAt)
            .build();
    }
}
