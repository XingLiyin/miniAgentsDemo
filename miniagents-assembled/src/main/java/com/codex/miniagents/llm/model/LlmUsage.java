package com.codex.miniagents.llm.model;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class LlmUsage {
    private Integer promptTokens;

    private Integer completionTokens;

    private Integer totalTokens;
}
