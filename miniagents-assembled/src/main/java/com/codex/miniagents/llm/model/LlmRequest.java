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
public class LlmRequest {
    private String model;

    @Builder.Default
    private List<LlmMessage> messages = new ArrayList<>();

    private String systemPrompt;

    @Builder.Default
    private List<LlmTool> tools = new ArrayList<>();

    private Float temperature;

    private Integer maxTokens;

    private Float topP;

    @Builder.Default
    private List<String> stop = new ArrayList<>();

    @Builder.Default
    private Map<String, Object> metadata = new HashMap<>();

    public LlmRequest clone() {
        return LlmRequest.builder()
            .model(model)
            .messages(messages)
            .systemPrompt(systemPrompt)
            .tools(tools)
            .temperature(temperature)
            .maxTokens(maxTokens)
            .topP(topP)
            .stop(stop)
            .metadata(metadata == null ? Map.of() : metadata)
            .build();
    }
}
