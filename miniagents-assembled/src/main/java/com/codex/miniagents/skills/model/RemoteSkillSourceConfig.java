package com.codex.miniagents.skills.model;

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
public class RemoteSkillSourceConfig {
    private String sourceName;

    private String mcpType;

    private String mcpUrl;

    @Builder.Default
    private int mcpTimeout = 30;

    private String mcpCommand;

    @Builder.Default
    private List<String> mcpArgs = new ArrayList<>();

    @Builder.Default
    private Map<String, String> mcpEnv = new LinkedHashMap<>();

    @Builder.Default
    private String mcpToolListSkills = "listSkills";

    @Builder.Default
    private String mcpToolLoadSkillMd = "loadSkillMd";

    @Builder.Default
    private String mcpToolGetSkillFiles = "getSkillFiles";

    @Builder.Default
    private String mcpToolLoadSkillReference = "loadSkillReference";

    @Builder.Default
    private String mcpToolExecSkillScript = "execSkillScript";
}
