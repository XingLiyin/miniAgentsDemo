package com.codex.miniagents.runtime.model;

import com.codex.miniagents.llm.model.LlmMessage;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

import java.util.ArrayList;
import java.util.List;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public class ConversationTurn {
    private int round;

    @Builder.Default
    private List<LlmMessage> messagesSent = new ArrayList<>();

    private String llmText;

    @Builder.Default
    private List<ToolCallRecord> toolCalls = new ArrayList<>();
}
