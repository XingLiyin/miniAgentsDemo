package com.codex.miniagents.tools.model;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class McpConfigData {
    private String name;

    private String type;

    private String command;

    @Builder.Default
    private List<String> args = List.of();

    @Builder.Default
    private Map<String, String> env = new HashMap<>();

    private String url;

    private Integer timeout;
}
