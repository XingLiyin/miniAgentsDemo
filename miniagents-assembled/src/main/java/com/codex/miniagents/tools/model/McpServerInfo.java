package com.codex.miniagents.tools.model;

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
public class McpServerInfo {
    private String name;

    private String type;

    @Builder.Default
    private List<String> tools = new ArrayList<>();

    private String command;

    @Builder.Default
    private List<String> args = new ArrayList<>();

    private String url;

    private Integer timeoutSec;
}
