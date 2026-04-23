package com.codex.miniagents.agenttemplate.definition;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.nio.file.Path;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class AgentDefMetadata {
    private String name;

    private String version;

    private String description;

    private ToolSpec actToolSpec;

    private ToolSpec observeToolSpec;

    @Builder.Default
    private java.util.List<String> mcpActServers = new java.util.ArrayList<>();

    @Builder.Default
    private java.util.List<String> mcpObserveServers = new java.util.ArrayList<>();

    private Path agentDir;
}
