package com.codex.miniagents.dto.response;

import com.codex.miniagents.domain.model.AgentTemplate;
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
public class AgentTemplateResponse {
    private String id;

    private String name;

    private String version;

    private String description;

    private List<String> actToolList;

    private List<String> observeToolList;

    private List<String> mcpActServers;

    private List<String> mcpObserveServers;

    private String sourceDir;

    private Boolean hasSpawnPermission;

    private String createdAt;

    private String updatedAt;

    public static AgentTemplateResponse from(AgentTemplate template) {
        return AgentTemplateResponse.builder()
            .id(template.getId())
            .name(template.getName())
            .version(template.getVersion())
            .description(template.getDescription())
            .actToolList(template.getActToolList() == null ? new ArrayList<>() : new ArrayList<>(template.getActToolList()))
            .observeToolList(template.getObserveToolList() == null ? new ArrayList<>() : new ArrayList<>(template.getObserveToolList()))
            .mcpActServers(template.getMcpActServers() == null ? new ArrayList<>() : new ArrayList<>(template.getMcpActServers()))
            .mcpObserveServers(template.getMcpObserveServers() == null ? new ArrayList<>() : new ArrayList<>(template.getMcpObserveServers()))
            .sourceDir(template.getSourceDir())
            .hasSpawnPermission(template.isHasSpawnPermission())
            .createdAt(template.getCreatedAt())
            .updatedAt(template.getUpdatedAt())
            .build();
    }
}
