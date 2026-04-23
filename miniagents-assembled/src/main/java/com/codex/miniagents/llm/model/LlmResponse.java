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
public class LlmResponse {
    private String text;

    @Builder.Default
    private Map<String, Object> raw = new HashMap<>();

    private LlmUsage usage;
}
