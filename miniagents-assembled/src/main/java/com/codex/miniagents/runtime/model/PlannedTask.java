package com.codex.miniagents.runtime.model;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonProperty;

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
public class PlannedTask {
    private String title;

    private String description;

    @JsonProperty("user_prompt")
    private String userPrompt;

    @JsonProperty("skill_name")
    private String skillName;

    @JsonIgnore
    private String prompt;

    @JsonProperty("use_subagent")
    private Boolean useSubagent;

    @JsonProperty("inherit_memory")
    private Boolean inheritMemory;

    @JsonProperty("subagent_template")
    private String subagentTemplate;
}
