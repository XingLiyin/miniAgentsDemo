package com.codex.miniagents.dto.response;

import com.codex.miniagents.tools.model.McpServerInfo;
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
public class McpServerResponse {
    private String name;

    private String type;

    @Builder.Default
    private List<String> tools = new ArrayList<>();

    private String command;

    private List<String> args;

    private String url;

    private Integer timeout;

    public static McpServerResponse from(McpServerInfo info) {
        return McpServerResponse.builder()
            .name(info.getName())
            .type(info.getType())
            .tools(info.getTools() == null ? new ArrayList<>() : new ArrayList<>(info.getTools()))
            .command(info.getCommand())
            .args("http".equals(info.getType()) ? null
                : (info.getArgs() == null ? new ArrayList<>() : new ArrayList<>(info.getArgs())))
            .url(info.getUrl())
            .timeout(info.getTimeoutSec())
            .build();
    }
}
