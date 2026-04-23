package com.codex.miniagents.domain.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
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
@JsonIgnoreProperties(ignoreUnknown = true)
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public class AgentTemplate {
    private String id;

    private String name;

    @Builder.Default
    private String version = "1.0.0";

    @Builder.Default
    private String description = "";

    @Builder.Default
    private List<String> actToolList = new ArrayList<>();

    @Builder.Default
    private List<String> observeToolList = new ArrayList<>();

    @Builder.Default
    private List<String> mcpActServers = new ArrayList<>();

    @Builder.Default
    private List<String> mcpObserveServers = new ArrayList<>();

    @Builder.Default
    private String sourceDir = "";

    @Builder.Default
    private boolean hasSpawnPermission = false;

    private String createdAt;

    private String updatedAt;
}
