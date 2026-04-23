package com.codex.miniagents.llm.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonSetter;

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
@JsonIgnoreProperties(ignoreUnknown = true)
public class LlmProviderConfig {
    public static final int DEFAULT_MAX_TOKENS = 8096;

    private String name;

    private String style;

    private String apiKey;

    private String baseUrl;

    @Builder.Default
    private List<String> models = new ArrayList<>();

    @Builder.Default
    private String defaultModel = "";

    private int timeoutSec;

    @Builder.Default
    private int maxTokens = DEFAULT_MAX_TOKENS;

    @JsonSetter("model")
    public void setLegacyModel(String model) {
        if (model == null || model.isBlank()) {
            return;
        }
        if (this.models == null) {
            this.models = new ArrayList<>();
        }
        if (!this.models.contains(model)) {
            this.models.add(model);
        }
        if (this.defaultModel == null || this.defaultModel.isBlank()) {
            this.defaultModel = model;
        }
    }
}
