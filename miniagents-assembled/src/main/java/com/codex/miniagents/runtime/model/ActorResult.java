package com.codex.miniagents.runtime.model;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public class ActorResult {
    private String taskId;
    private boolean success;
    private String output;

    @Builder.Default
    private List<ToolCallRecord> toolCallsMade = new ArrayList<>();

    @Builder.Default
    private List<ConversationTurn> conversationTurns = new ArrayList<>();

    @Builder.Default
    private Map<String, Object> taskOutputs = new HashMap<>();

    private String actorMode;
    private Integer planTaskCount;
    private String skillUsed;
    private String error;
}
