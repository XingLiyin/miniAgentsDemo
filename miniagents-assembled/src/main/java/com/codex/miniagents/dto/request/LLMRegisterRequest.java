package com.codex.miniagents.dto.request;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
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
public class LLMRegisterRequest {
    @NotBlank
    private String name;

    @NotBlank
    private String style;

    @NotBlank
    private String apiKey;

    @NotBlank
    private String baseUrl;

    @Builder.Default
    private List<String> models = new ArrayList<>();

    private String defaultModel;

    @Min(1)
    private Integer timeoutSec;

    @Min(1)
    private Integer maxTokens;
}
