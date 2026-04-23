package com.codex.miniagents.tools.model;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.HashMap;
import java.util.Map;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ToolResult {
    @Builder.Default
    private String content = "";

    @Builder.Default
    private boolean isError = false;

    private String errorCode;

    @Builder.Default
    private Map<String, Object> metadata = new HashMap<>();
}
