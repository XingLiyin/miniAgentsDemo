package com.codex.miniagents.runtime.model;

import com.codex.miniagents.domain.model.task.Task;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public class ReasoningContext {
    private String goal;

    @Builder.Default
    private List<Map<String, Object>> recentMessages = new ArrayList<>();

    @Builder.Default
    private String summaryText = "";

    @Builder.Default
    private List<String> blackboardSnippets = new ArrayList<>();

    @Builder.Default
    private String soul = "";

    @Builder.Default
    private String role = "";

    @Builder.Default
    private String skillInstructions = "";

    @Builder.Default
    private List<ContextResource> actorResources = new ArrayList<>();

    @Builder.Default
    private List<ContextResource> observerResources = new ArrayList<>();

    private Task currentTask;

    @Builder.Default
    private int tokenEstimate = 0;
}
