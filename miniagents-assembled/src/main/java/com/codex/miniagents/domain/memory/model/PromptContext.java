package com.codex.miniagents.domain.memory.model;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class PromptContext {
    private String systemPrompt;

    private String goal;

    private String taskDescription;

    @Builder.Default
    private List<String> blackboardSnippets = new ArrayList<>();

    @Builder.Default
    private List<Map<String, Object>> recentMessages = new ArrayList<>();

    @Builder.Default
    private String summaryText = "";

    private int tokenEstimate;
}
