package com.codex.miniagents.agenttemplate.definition;

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
public class AgentDefContent {
    private AgentDefMetadata metadata;

    private String soulMd;

    private String roleMd;

    private String toolsMd;

    private String styleMd;
}
