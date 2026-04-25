package com.codex.miniagents.dto.response;

import com.codex.miniagents.skills.model.RemoteSkillSourceConfig;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public class RemoteSkillSourceResponse {
    private String sourceName;

    private String mcpType;

    private String mcpToolListSkills;

    private String mcpToolLoadSkillMd;

    private String mcpToolGetSkillFiles;

    private String mcpToolLoadSkillReference;

    private String mcpToolExecSkillScript;

    private String mcpUrl;

    private Integer mcpTimeout;

    private String mcpCommand;

    private List<String> mcpArgs;

    private Map<String, String> mcpEnv;

    public static RemoteSkillSourceResponse from(RemoteSkillSourceConfig config) {
        return RemoteSkillSourceResponse.builder()
            .sourceName(config.getSourceName())
            .mcpType(config.getMcpType())
            .mcpToolListSkills(defaultString(config.getMcpToolListSkills(), "listSkills"))
            .mcpToolLoadSkillMd(defaultString(config.getMcpToolLoadSkillMd(), "loadSkillMd"))
            .mcpToolGetSkillFiles(defaultString(config.getMcpToolGetSkillFiles(), "getSkillFiles"))
            .mcpToolLoadSkillReference(defaultString(config.getMcpToolLoadSkillReference(), "loadSkillReference"))
            .mcpToolExecSkillScript(defaultString(config.getMcpToolExecSkillScript(), "execSkillScript"))
            .mcpUrl(config.getMcpUrl())
            .mcpTimeout(config.getMcpTimeout())
            .mcpCommand(config.getMcpCommand())
            .mcpArgs(config.getMcpArgs() == null ? new ArrayList<>() : new ArrayList<>(config.getMcpArgs()))
            .mcpEnv(config.getMcpEnv() == null ? new LinkedHashMap<>() : new LinkedHashMap<>(config.getMcpEnv()))
            .build();
    }

    private static String defaultString(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }
}
