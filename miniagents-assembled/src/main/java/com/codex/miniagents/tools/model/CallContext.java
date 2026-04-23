package com.codex.miniagents.tools.model;

import com.codex.miniagents.domain.model.agent.Agent;
import com.codex.miniagents.domain.model.task.Task;

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
public class CallContext {
    @Builder.Default
    private String sessionId = "";

    @Builder.Default
    private String agentId = "";

    private Agent agent;

    private Task task;

    @Builder.Default
    private String workingDir = "";

    public static CallContext empty() {
        return CallContext.builder().build();
    }
}
