package com.codex.miniagents.llm.model;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.HashMap;
import java.util.Map;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class StreamChunk {
    @Builder.Default
    private String textDelta = "";

    @Builder.Default
    private Map<String, Object> toolCallDelta = new HashMap<>();

    @Builder.Default
    private boolean done = false;

    private LlmUsage usage;
}
