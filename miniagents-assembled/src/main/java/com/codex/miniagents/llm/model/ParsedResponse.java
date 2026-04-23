package com.codex.miniagents.llm.model;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ParsedResponse {
    private String text;

    @Builder.Default
    private List<LlmContentBlock> blocks = new ArrayList<>();

    @Builder.Default
    private List<ToolCallBlock> toolCalls = new ArrayList<>();

    @Builder.Default
    private Map<String, Object> raw = new HashMap<>();

    private LlmUsage usage;
}
