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

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public class RemoteSkillSourceHttpRegisterRequest {
    @NotBlank
    private String sourceName;

    @NotBlank
    private String mcpUrl;

    @Builder.Default
    @Min(1)
    private Integer mcpTimeout = 30;

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
