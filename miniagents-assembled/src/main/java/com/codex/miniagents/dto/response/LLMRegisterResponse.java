package com.codex.miniagents.dto.response;

import com.codex.miniagents.llm.model.LlmProviderConfig;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.ArrayList;
import java.util.List;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public class LLMRegisterResponse {
    private String name;

    private String style;

    private String baseUrl;

    private List<String> models;

    private String defaultModel;

    private Integer timeoutSec;

    private Integer maxTokens;

    public static LLMRegisterResponse from(LlmProviderConfig config) {
        return LLMRegisterResponse.builder()
            .name(config.getName())
            .style(config.getStyle())
            .baseUrl(config.getBaseUrl())
            .models(config.getModels() == null ? new ArrayList<>() : new ArrayList<>(config.getModels()))
            .defaultModel(config.getDefaultModel())
            .timeoutSec(config.getTimeoutSec())
            .maxTokens(config.getMaxTokens())
            .build();
    }
}
