package com.codex.miniagents.runtime.model;

import com.codex.miniagents.runtime.ControlSignal;
import com.codex.miniagents.tools.model.ToolResult;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

import java.util.HashMap;
import java.util.Map;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public class ControlResult {
    @Builder.Default
    private ToolResult toolResult = ToolResult.builder().content("").build();

    @Builder.Default
    private ControlSignal signal = ControlSignal.NONE;

    @Builder.Default
    private Map<String, Object> signalData = new HashMap<>();
}
